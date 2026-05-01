"""Asyncio queue + worker pool for invoking Orchestrator.run_step.

`run_step` is synchronous (it launches a Claude Code subprocess via
subprocess.run), so workers bridge to a thread via run_in_executor.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.orchestrator.orchestrator import Orchestrator

logger = logging.getLogger("opc.daemon.queue")

# Heartbeat cadence while a subprocess is alive. Independent of the
# session timeout (1800s) — small enough that `opc details` shows recent
# liveness for long-running tasks, large enough that we don't flood the
# audit DB with unrelated writes.
HEARTBEAT_INTERVAL_SECONDS = 30


class TaskQueue:
    """Wrapper around asyncio.Queue + a worker pool."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker_tasks: list[asyncio.Task] = []
        self._stopping = False

    def enqueue(self, task_id: str) -> None:
        """Non-blocking enqueue. Called from sync context (e.g. run_step)."""
        self._queue.put_nowait(task_id)

    def put_nowait(self, task_id: str) -> None:
        """Alias for `enqueue` — matches asyncio.Queue's method name so the
        orchestrator can treat `TaskQueue` and `asyncio.Queue` interchangeably."""
        self.enqueue(task_id)

    @staticmethod
    async def _heartbeat(orch: "Orchestrator", task_id: str) -> None:
        """Stamp tasks.last_heartbeat every HEARTBEAT_INTERVAL_SECONDS.

        Lives alongside the run_in_executor call in `_worker_loop`. Cancelled
        when run_step returns (success or failure). Database.update_task is
        thread-safe via its internal RLock, so writes from this event-loop
        coroutine race-safely with the run_step thread holding state.db_lock
        for higher-level transactions.
        """
        # Tap once up front so a task that finishes faster than the interval
        # still leaves a non-null marker that the worker actually picked it up.
        try:
            now = datetime.now(timezone.utc).isoformat()
            orch.db.update_task(task_id, last_heartbeat=now)
        except Exception:
            logger.exception("initial heartbeat for %s failed", task_id)
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            try:
                now = datetime.now(timezone.utc).isoformat()
                orch.db.update_task(task_id, last_heartbeat=now)
            except Exception:
                logger.exception("heartbeat for %s failed", task_id)

    async def _worker_loop(self, orch: "Orchestrator") -> None:
        loop = asyncio.get_running_loop()
        while not self._stopping:
            task_id = await self._queue.get()
            hb = asyncio.create_task(self._heartbeat(orch, task_id))
            try:
                await loop.run_in_executor(None, orch.run_step, task_id)
            except Exception:
                logger.exception("run_step %s raised — continuing", task_id)
            finally:
                hb.cancel()
                try:
                    await hb
                except asyncio.CancelledError:
                    pass
                self._queue.task_done()

    def start_workers(self, orch: "Orchestrator", n: int = 3) -> None:
        """Spawn `n` worker coroutines. Idempotent per-call is NOT expected —
        call once per daemon lifecycle."""
        for _ in range(n):
            self._worker_tasks.append(
                asyncio.create_task(self._worker_loop(orch))
            )

    def is_running(self) -> bool:
        """True if at least one worker coroutine is live.

        The app-level bootstrap uses this to decide whether a deferred
        start (e.g. after a runtime swap) still needs to spawn workers."""
        return any(not t.done() for t in self._worker_tasks)

    async def stop(self, *, timeout: float = 5.0) -> None:
        """Graceful shutdown: stop accepting work, cancel workers."""
        self._stopping = True
        for t in self._worker_tasks:
            t.cancel()
        await asyncio.gather(*self._worker_tasks, return_exceptions=True)

    async def drain_sync(self, orch: "Orchestrator") -> None:
        """Test helper: process every currently-queued item SYNCHRONOUSLY on
        this event loop, without spinning up long-lived worker tasks. Returns
        when the queue is empty.

        This exists so tests can drive the queue deterministically without
        racing against `run_in_executor`-backed workers."""
        loop = asyncio.get_running_loop()
        while not self._queue.empty():
            task_id = self._queue.get_nowait()
            try:
                await loop.run_in_executor(None, orch.run_step, task_id)
            except Exception:
                logger.exception("run_step %s raised during drain", task_id)
            finally:
                self._queue.task_done()
