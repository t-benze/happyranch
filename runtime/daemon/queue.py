"""Asyncio queue + worker pool for invoking Orchestrator.run_step.

Items are ``(slug, task_id, metadata)`` tuples. The worker loop unpacks each
item, looks up ``state.get_org(slug)``, and calls that org's
``Orchestrator.run_step(task_id, metadata=metadata)`` on a thread.
``metadata`` is an optional dict that callers can use to pass trigger context
(e.g. ``{"trigger": "job_terminal", "triggering_job_id": "JOB-5"}``); it is
forwarded unchanged to ``run_step`` and defaults to ``None`` when omitted.
"""
from __future__ import annotations

import asyncio
from collections import Counter
import logging
import threading
import time
from typing import TYPE_CHECKING, Callable, Protocol

if TYPE_CHECKING:
    from runtime.daemon.metrics import MetricsRegistry
    from runtime.daemon.state import DaemonState

logger = logging.getLogger("happyranch.daemon.queue")

# Heartbeat cadence while a subprocess is alive. Independent of the
# session timeout (1800s) — small enough that `happyranch details` shows recent
# liveness for long-running tasks, large enough that we don't flood the
# audit DB with unrelated writes.
HEARTBEAT_INTERVAL_SECONDS = 30


class _Dispatcher(Protocol):
    def run_step(self, slug: str, task_id: str, metadata: dict | None = None) -> None: ...
    def heartbeat(self, slug: str, task_id: str) -> None: ...


_QueueItem = tuple[str, str, dict | None]


class _TrackedAsyncQueue(asyncio.Queue[_QueueItem]):
    """Keep TaskQueue pending counts atomic with the actual dequeue."""

    def __init__(
        self,
        admission_lock: threading.RLock,
        on_remove: Callable[[_QueueItem], None],
    ) -> None:
        super().__init__()
        self._admission_lock = admission_lock
        self._on_remove = on_remove

    def _get(self) -> _QueueItem:
        # asyncio.Queue.get/get_nowait both remove through this synchronous
        # hook. Holding the same lock as enqueue_if_absent makes "pending"
        # end at the exact dequeue operation, without a taken-item gap.
        with self._admission_lock:
            item = super()._get()
            self._on_remove(item)
            return item


class TaskQueue:
    """Wrapper around asyncio.Queue + a worker pool."""

    def __init__(self) -> None:
        self._admission_lock = threading.RLock()
        self._pending_counts: Counter[tuple[str, str]] = Counter()
        self._admission_reservations: set[tuple[str, str]] = set()
        self._queue: asyncio.Queue[_QueueItem] = _TrackedAsyncQueue(
            self._admission_lock, self._record_dequeue,
        )
        self._worker_tasks: list[asyncio.Task] = []
        self._stopping = False
        self._metrics_registry: MetricsRegistry | None = None  # set by daemon wiring

    def _record_dequeue(self, item: _QueueItem) -> None:
        key = item[0], item[1]
        count = self._pending_counts[key]
        if count <= 1:
            self._pending_counts.pop(key, None)
        else:
            self._pending_counts[key] = count - 1

    def enqueue(self, slug: str, task_id: str, *, metadata: dict | None = None) -> None:
        with self._admission_lock:
            self._queue.put_nowait((slug, task_id, metadata))
            self._pending_counts[(slug, task_id)] += 1

    def put_nowait(self, slug: str, task_id: str, *, metadata: dict | None = None) -> None:
        self.enqueue(slug, task_id, metadata=metadata)

    def enqueue_if_absent(
        self,
        slug: str,
        task_id: str,
        *,
        metadata: dict | None = None,
        publisher: Callable[[], None] | None = None,
    ) -> bool:
        """Publish only when this task has no reservation or pending item.

        This is a narrow producer-selected boundary, not global coalescing:
        ordinary ``enqueue`` and ``put_nowait`` remain non-deduplicating.
        ``publisher`` lets generation-aware producers retain their existing
        classification and metadata logic.  Admission first claims an
        in-memory reservation under the queue lock, then invokes that external
        callback without the lock: DB-aware publishers must never participate
        in a queue-lock/DB-lock cycle.  Concurrent bounded producers observe
        the reservation and lose until publication succeeds or refuses.  The
        reservation is always released; a successful insertion remains owned
        by the pending count until the same locked dequeue operation, so a
        later qualifying wake is admitted.
        """
        key = slug, task_id
        with self._admission_lock:
            if self._pending_counts[key] or key in self._admission_reservations:
                return False
            self._admission_reservations.add(key)
        try:
            if publisher is None:
                self.enqueue(slug, task_id, metadata=metadata)
            else:
                publisher()
            with self._admission_lock:
                return self._pending_counts[key] > 0
        finally:
            with self._admission_lock:
                self._admission_reservations.discard(key)

    @staticmethod
    async def _heartbeat(dispatcher: _Dispatcher, slug: str, task_id: str) -> None:
        """Stamp tasks.last_heartbeat every HEARTBEAT_INTERVAL_SECONDS.

        Lives alongside the run_in_executor call in `_worker_loop`. Cancelled
        when run_step returns (success or failure). The dispatcher resolves
        ``slug`` to the correct OrgState and updates that org's database.
        Database.update_task is thread-safe via its internal RLock, so writes
        from this event-loop coroutine race-safely with the run_step thread
        holding state.db_lock for higher-level transactions.
        """
        # Tap once up front so a task that finishes faster than the interval
        # still leaves a non-null marker that the worker actually picked it up.
        try:
            dispatcher.heartbeat(slug, task_id)
        except Exception:
            logger.exception("initial heartbeat for %s/%s failed", slug, task_id)
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            try:
                dispatcher.heartbeat(slug, task_id)
            except Exception:
                logger.exception("heartbeat for %s/%s failed", slug, task_id)

    async def _worker_loop(self, dispatcher: _Dispatcher) -> None:
        loop = asyncio.get_running_loop()
        while not self._stopping:
            slug, task_id, metadata = await self._queue.get()
            hb = asyncio.create_task(self._heartbeat(dispatcher, slug, task_id))
            t0 = time.monotonic()
            try:
                await loop.run_in_executor(
                    None, dispatcher.run_step, slug, task_id, metadata,
                )
            except Exception:
                logger.exception(
                    "run_step %s/%s raised — continuing", slug, task_id,
                )
            finally:
                duration = time.monotonic() - t0
                if self._metrics_registry is not None:
                    self._metrics_registry.record_loop_tick("run_step_worker", 0, duration)
                hb.cancel()
                try:
                    await hb
                except asyncio.CancelledError:
                    pass
                self._queue.task_done()

    def start_workers(self, dispatcher: _Dispatcher, n: int = 6) -> None:
        for _ in range(n):
            self._worker_tasks.append(
                asyncio.create_task(self._worker_loop(dispatcher))
            )

    def is_running(self) -> bool:
        return any(not t.done() for t in self._worker_tasks)

    def pending_slugs(self) -> set[str]:
        """Snapshot the set of slugs with at least one queued item.

        Read-only; used by the portability preflight quiescence check as
        defense-in-depth on top of the DB nonterminal-task predicate. The
        queue-owned pending counts are read under the admission lock; the DB
        check remains the authoritative backstop.
        """
        with self._admission_lock:
            return {
                slug for (slug, _task_id), count in self._pending_counts.items()
                if count > 0
            }

    async def stop(self, *, timeout: float = 5.0) -> None:
        self._stopping = True
        for t in self._worker_tasks:
            t.cancel()
        await asyncio.gather(*self._worker_tasks, return_exceptions=True)

    async def drain_sync(self, dispatcher: _Dispatcher) -> None:
        loop = asyncio.get_running_loop()
        while not self._queue.empty():
            slug, task_id, metadata = self._queue.get_nowait()
            try:
                await loop.run_in_executor(
                    None, dispatcher.run_step, slug, task_id, metadata,
                )
            except Exception:
                logger.exception(
                    "run_step %s/%s raised during drain", slug, task_id,
                )
            finally:
                self._queue.task_done()
