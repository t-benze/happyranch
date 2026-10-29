"""Asyncio queue + worker pool for invoking Orchestrator.run_step.

Items are ``(slug, task_id, metadata)`` tuples. The worker loop unpacks each
item, captures ``state.get_org(slug)`` before submission, and calls that org's
``Orchestrator.run_step(task_id, metadata=metadata)`` on a thread.
``metadata`` is an optional dict that callers can use to pass trigger context
(e.g. ``{"trigger": "job_terminal", "triggering_job_id": "JOB-5"}``); it is
forwarded unchanged to ``run_step`` and defaults to ``None`` when omitted.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import partial
import logging
import threading
import time
from typing import TYPE_CHECKING, Callable, Protocol

if TYPE_CHECKING:
    from runtime.daemon.metrics import MetricsRegistry
    from runtime.daemon.org_state import OrgState
    from runtime.daemon.startup_recovery import StartupRecovery

logger = logging.getLogger("happyranch.daemon.queue")

# Heartbeat cadence while a subprocess is alive. Independent of the
# session timeout (1800s) — small enough that `happyranch details` shows recent
# liveness for long-running tasks, large enough that we don't flood the
# audit DB with unrelated writes.
HEARTBEAT_INTERVAL_SECONDS = 30


class _Dispatcher(Protocol):
    def task_attachment(self, slug: str) -> OrgState | None: ...
    def run_step(self, slug: str, task_id: str, metadata: dict | None = None, **kwargs: object) -> None: ...
    def heartbeat(self, slug: str, task_id: str, queue_owner: object = None, **kwargs: object) -> None: ...
    def worker_started(self, slug: str, task_id: str, **kwargs: object) -> object: ...
    def worker_finished(self, slug: str, task_id: str, owner: object) -> None: ...
    def task_admission_allowed(self, slug: str, task_id: str, metadata: dict | None = None, **kwargs: object) -> bool: ...


_QueueItem = tuple[str, str, dict | None]


@dataclass(eq=False)
class _NativeWork:
    """One queue delivery, owned beyond cancellation of its consumer."""

    dispatcher: _Dispatcher
    item: _QueueItem
    attachment: OrgState | None = None
    task: asyncio.Task | None = None
    preparation: asyncio.Future | None = None
    run: asyncio.Future | None = None
    retirement: asyncio.Future | None = None
    owner: object = None
    abandoned: bool = False
    failures: list[BaseException] = field(default_factory=list)

    def call(self, method: str, *args: object) -> Callable[[], object]:
        callback = getattr(self.dispatcher, method)
        if self.attachment is not None:
            return partial(callback, *args, attachment=self.attachment)
        return partial(callback, *args)


class _TrackedAsyncQueue(asyncio.Queue[_QueueItem]):
    """Keep TaskQueue pending counts atomic with the actual dequeue."""

    def __init__(
        self,
        admission_lock: threading.RLock,
        on_remove: Callable[[_QueueItem], None],
        before_remove: Callable[[], None],
    ) -> None:
        super().__init__()
        self._admission_lock = admission_lock
        self._on_remove = on_remove
        self._before_remove = before_remove

    def _get(self) -> _QueueItem:
        # asyncio.Queue.get/get_nowait both remove through this synchronous
        # hook. Holding the same lock as enqueue_if_absent makes "pending"
        # end at the exact dequeue operation, without a taken-item gap.
        self._before_remove()
        with self._admission_lock:
            item = super()._get()
            self._on_remove(item)
            return item


class TaskQueue:
    """Wrapper around asyncio.Queue + a worker pool."""

    def __init__(self) -> None:
        self._startup_state = None
        self._startup_org = None
        self._startup_recovery = None
        self._admission_lock = threading.RLock()
        self._publisher_validations = threading.local()
        self._pending_counts: Counter[tuple[str, str]] = Counter()
        self._admission_reservations: set[tuple[str, str]] = set()
        self._queue: asyncio.Queue[_QueueItem] = _TrackedAsyncQueue(
            self._admission_lock, self._record_dequeue, self._require_ready,
        )
        self._worker_tasks: list[asyncio.Task] = []
        self._stopping = False
        self._native_work: set[_NativeWork] = set()
        self._stop_task: asyncio.Task | None = None
        self._metrics_registry: MetricsRegistry | None = None  # set by daemon wiring

    def _require_ready(self) -> None:
        from runtime.daemon.startup_recovery import _require_ordinary_ready
        _require_ordinary_ready(self)

    def _publication_selection(self, slug: str) -> Callable[[], None]:
        from runtime.daemon.startup_recovery import StartupUnavailable
        # Select before any ordinary/publication check can wait on its owners.
        # Those checks must not turn a replacement into this operation's owner.
        try:
            state, gate, queue_org = self._startup_state, self._startup_recovery, self._startup_org
            orgs = None if state is None else state.orgs
            org = None if orgs is None else orgs.get(slug)
            refs = None if org is None else org._startup_admission_refs
            epoch, loop, phase = (None, None, None) if gate is None else (gate.epoch, gate._loop, gate._phase)
        except AttributeError as exc:
            raise StartupUnavailable("startup_attachment_changed") from exc

        def validate_selection() -> None:
            try:
                if (self._startup_state is not state or self._startup_recovery is not gate
                        or self._startup_org is not queue_org
                        or state is not None and (gate.state is not state or state.queue is not self
                                                  or state.orgs is not orgs or orgs.get(slug) is not org
                                                  or org is None or org._startup_state is not state
                                                  or org._startup_recovery is not gate or org._startup_org is not org)
                        or org is not None and org._startup_admission_refs is not refs
                        or gate is not None and (gate.epoch != epoch or gate._loop is not loop
                                                  or gate._phase != phase)):
                    raise StartupUnavailable("startup_attachment_changed")
                if state is not None:
                    gate.require_attachment(org)
            except AttributeError as exc:
                raise StartupUnavailable("startup_attachment_changed") from exc

        validate_selection()
        return validate_selection

    @contextmanager
    def _publication_scope(self, slug: str, task_id: str, *, _selection=None):
        """Retain this producer's domain before owner/DB discovery waits.

        A multi-target producer supplies its originally selected org domain
        when it binds each discovered target key. It never captures a replacement
        map at discovery return. Only this queue/thread inherits; finally
        restores all ancestors.
        """
        validate = self._publication_selection(slug) if _selection is None else _selection
        validate()
        previous = getattr(self._publisher_validations, "stack", ())
        self._publisher_validations.stack = (*previous, (slug, task_id, validate))
        try:
            yield
        finally:
            self._publisher_validations.stack = previous

    @contextmanager
    def _admission(self):
        from runtime.daemon.startup_recovery import _queue_admission_renewal
        # Renewal's exception/failure/status tail runs after the mutex exits.
        with _queue_admission_renewal():
            with self._admission_lock:
                yield

    def _log_stopping(self, slug: str, task_id: str) -> None:
        from runtime.daemon.startup_recovery import _defer_queue_diagnostic
        def log() -> None:
            logger.warning("task queue is stopping; refusing %s/%s", slug, task_id)
        # Our own mutex has exited, but a reentrant ancestor may still own
        # admission. Its outermost renewal also owns this diagnostic tail.
        if not _defer_queue_diagnostic(log):
            log()

    def _require_publication(self, slug: str, task_id: str, metadata: dict | None,
                             publication: object,
                             coding_validation: Callable[[], None] | None = None,
                             ) -> tuple[Callable[[], None], Callable[[], None] | None]:
        from runtime.daemon.startup_recovery import ordinary_admission_validation, require_ready, StartupUnavailable
        validate_selection = self._publication_selection(slug)
        state = self._startup_state
        org = None if state is None else state.orgs.get(slug)
        validate_selection()
        if coding_validation is not None:
            coding_validation()

        if publication is None:
            validate_queue = ordinary_admission_validation(self)
            if state is not None:
                require_ready(org)

            def validate() -> None:
                validate_selection()
                validate_queue()
                if state is not None:
                    require_ready(org)
                if coding_validation is not None:
                    coding_validation()
                validate_selection()
        else:
            from runtime.daemon.startup_publication import _TaskPublication
            if type(publication) is not _TaskPublication or self._startup_recovery is not publication.gate:
                raise StartupUnavailable("startup_task_publication_unbound")
            publication.check(self, slug, task_id, metadata)
            if coding_validation is not None and coding_validation is not publication.validate_admission:
                raise StartupUnavailable("startup_coding_queue_selection_changed")
            coding_validation = publication.validate_admission
            if org is None:
                raise StartupUnavailable("startup_attachment_changed")

            def validate() -> None:
                validate_selection()
                # Includes the exact kind/task/metadata, sweep/publication and
                # native-owner predicates, plus coding's original V0 if any.
                publication.check(self, slug, task_id, metadata)
                validate_selection()
        # General renewal is reference/phase-only, never DB/authority discovery.
        # Keep coding's supplied V0 separate: a composite is not an authentic V0.
        validate_selection()
        return validate, coding_validation

    def _record_dequeue(self, item: _QueueItem) -> None:
        key = item[0], item[1]
        count = self._pending_counts[key]
        if count <= 1:
            self._pending_counts.pop(key, None)
        else:
            self._pending_counts[key] = count - 1

    def enqueue(self, slug: str, task_id: str, *, metadata: dict | None = None,
                _publication: object = None,
                _coding_validation: Callable[[], None] | None = None,
                _queue_validation: Callable[[], None] | None = None) -> None:
        publisher_validations = getattr(self._publisher_validations, "stack", ())
        validate, _ = self._require_publication(slug, task_id, metadata, _publication, _coding_validation)
        with self._admission():
            # A nested enqueue also owes its caller's ORIGINAL selection.
            # This additional check cannot replace this entry's validation.
            for selected_slug, selected_task, selected_validate in publisher_validations:
                if (selected_slug, selected_task) == (slug, task_id):
                    selected_validate()
            if _queue_validation is not None:
                _queue_validation()
            validate()
            stopping = self._stopping
            if not stopping:
                self._queue.put_nowait((slug, task_id, metadata))
                self._pending_counts[(slug, task_id)] += 1
        if stopping:
            self._log_stopping(slug, task_id)

    def put_nowait(self, slug: str, task_id: str, *, metadata: dict | None = None,
                   _publication: object = None) -> None:
        self.enqueue(slug, task_id, metadata=metadata, _publication=_publication)

    def enqueue_if_absent(
        self,
        slug: str,
        task_id: str,
        *,
        metadata: dict | None = None,
        publisher: Callable[[], None] | None = None,
        _publication: object = None,
        _coding_validation: Callable[[], None] | None = None,
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
        validate, coding_validation = self._require_publication(
            slug, task_id, metadata, _publication, _coding_validation,
        )
        key = slug, task_id
        with self._admission():
            validate()
            stopping = self._stopping
            if not stopping:
                if self._pending_counts[key] or key in self._admission_reservations:
                    return False
                self._admission_reservations.add(key)
        if stopping:
            self._log_stopping(slug, task_id)
            return False
        try:
            if publisher is None:
                self.enqueue(slug, task_id, metadata=metadata, _publication=_publication,
                             _coding_validation=coding_validation, _queue_validation=validate)
            else:
                validate()
                # Existing custom/generation publishers call raw enqueue
                # synchronously on this thread, after their DB waits. Retain
                # every original selection for this queue/key until they return;
                # unrelated keys/threads never inherit it. Restore the previous
                # stack even on failure or reentry, without changing coding V0.
                previous = getattr(self._publisher_validations, "stack", ())
                self._publisher_validations.stack = (*previous, (slug, task_id, validate))
                try:
                    publisher()
                finally:
                    self._publisher_validations.stack = previous
            with self._admission():
                validate()
                return not self._stopping and self._pending_counts[key] > 0
        finally:
            with self._admission_lock:
                self._admission_reservations.discard(key)

    def pending_publication(self, slug: str, task_id: str) -> str:
        """Observe this original task owner without treating a reservation as put.

        The producer still owns generation classification/metadata. This exact
        key is the existing queue dedup owner; no queue scan or DB read occurs
        under the admission mutex. Stopping can never acknowledge delivery.
        """
        key = slug, task_id
        with self._admission_lock:
            if self._stopping:
                return "stopping"
            if key in self._admission_reservations:
                return "reserved"
            return "pending" if self._pending_counts[key] > 0 else "absent"

    @staticmethod
    async def _heartbeat(work: _NativeWork) -> None:
        """Keep attribution on the captured attachment until native return."""
        slug, task_id, _metadata = work.item
        while True:
            try:
                work.call("heartbeat", slug, task_id, work.owner)()
            except Exception:
                logger.exception("heartbeat for %s/%s failed", slug, task_id)
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)

    @staticmethod
    async def _join_native(work: _NativeWork, future: asyncio.Future) -> object:
        # Only this lifetime owns native futures. Consumer/stop cancellation
        # never reaches them. Even loop-shutdown cancellation must not abandon
        # an already submitted future or its SAME-owner retirement.
        while True:
            try:
                return await asyncio.shield(future)
            except asyncio.CancelledError:
                work.abandoned = True
                if future.done():
                    # Observe cancellation of the wrapper OR CancelledError
                    # raised by the native callable itself. Neither is a
                    # successful retirement; do not spin on a done future.
                    return future.result()

    def _submit(self, dispatcher: _Dispatcher, item: _QueueItem) -> _NativeWork | None:
        self._require_ready()
        # Submission and org close/preflight are synchronous on the event loop.
        # Register the obligation before creating any executor future/await.
        if self._stopping:
            return None
        attachment_reader = getattr(dispatcher, "task_attachment", None)
        attachment = attachment_reader(item[0]) if attachment_reader else None
        if attachment_reader is not None and attachment is None:
            return None
        if attachment is not None:
            from runtime.daemon.startup_recovery import _require_ordinary_ready
            _require_ordinary_ready(attachment)
        work = _NativeWork(dispatcher, item, attachment)
        if attachment is not None and not attachment._acquire_native_task(work):
            return None
        with self._admission_lock:
            self._native_work.add(work)
        work.task = asyncio.create_task(self._run_owned(work))
        return work

    async def _run_owned(self, work: _NativeWork) -> None:
        loop = asyncio.get_running_loop()
        slug, task_id, metadata = work.item
        hb = None
        t0 = time.monotonic()
        try:
            self._require_ready()
            if self._stopping or work.abandoned:
                return
            admission = getattr(work.dispatcher, "task_admission_allowed", None)
            if admission is not None and not work.call(
                "task_admission_allowed", slug, task_id, metadata,
            )():
                return
            if getattr(work.dispatcher, "worker_started", None) is not None:
                work.preparation = loop.run_in_executor(
                    None, work.call("worker_started", slug, task_id),
                )
                work.owner = await self._join_native(work, work.preparation)
            # Late preparation after cancellation must retire its returned
            # owner, without launching another native run during shutdown.
            if not self._stopping and not work.abandoned:
                self._require_ready()
                hb = asyncio.create_task(self._heartbeat(work))
                work.run = loop.run_in_executor(
                    None, work.call("run_step", slug, task_id, metadata),
                )
                await self._join_native(work, work.run)
        except BaseException as exc:
            from runtime.daemon.startup_recovery import StartupUnavailable
            # A known refusal BEFORE native run submission has no provider
            # effect. Still retire a genuinely prepared queue owner in finally.
            # An exception from an actual submitted run retains its old barrier.
            if not isinstance(exc, StartupUnavailable) or work.run is not None:
                work.failures.append(exc)
                logger.exception("native task %s/%s failed; retaining close barrier", slug, task_id)
        finally:
            if hb is not None:
                hb.cancel()
                # Join teardown independently of repeated lifetime cancellation.
                await self._join_native(work, asyncio.gather(hb, return_exceptions=True))
            # A failed run still genuinely returned and can retire its owner;
            # cancellation of its future is NOT that proof. Retain failures
            # even when worker_finished succeeds; never infer settlement.
            returned = work.run is None or (work.run.done() and not work.run.cancelled())
            if work.owner is not None and returned:
                try:
                    work.retirement = loop.run_in_executor(
                        None, work.dispatcher.worker_finished, slug, task_id, work.owner,
                    )
                    await self._join_native(work, work.retirement)
                except BaseException as exc:
                    work.failures.append(exc)
                    logger.exception("native retirement %s/%s failed; retaining close barrier", slug, task_id)
            if not work.failures:
                if work.attachment is not None:
                    work.attachment._release_native_task(work)
                with self._admission_lock:
                    self._native_work.remove(work)
            self._queue.task_done()
            if self._metrics_registry is not None:
                self._metrics_registry.record_loop_tick(
                    "run_step_worker", 0, time.monotonic() - t0,
                )

    async def _consume(self, dispatcher: _Dispatcher, item: _QueueItem) -> None:
        self._require_ready()
        work = self._submit(dispatcher, item)
        if work is None:
            self._queue.task_done()
            return
        try:
            await asyncio.shield(work.task)
        except asyncio.CancelledError:
            work.abandoned = True
            raise

    async def _worker_loop(self, dispatcher: _Dispatcher) -> None:
        self._require_ready()
        while not self._stopping:
            self._require_ready()
            item = await self._queue.get()
            await self._consume(dispatcher, item)

    def start_workers(self, dispatcher: _Dispatcher, n: int = 6, *, recovery: StartupRecovery | None = None) -> None:
        # Prepare every actual handle immediately; no dequeue before the ONE
        # boot latch. Post-ready callers still cross the same direct gate.
        from runtime.daemon.startup_recovery import require_ready
        if recovery is None:
            self._require_ready()
            require_ready(dispatcher._state)
        elif (recovery is not self._startup_recovery or recovery.state is not dispatcher._state
              or recovery.state.queue is not self):
            raise RuntimeError("startup_worker_owner_mismatch")
        if recovery is not None:
            recovery.require_attachment(self)
        for _ in range(n):
            if recovery is None:
                task = asyncio.create_task(self._worker_loop(dispatcher))
            else:
                task = recovery.prepare_consumer("task-worker", lambda: self._worker_loop(dispatcher))
            self._worker_tasks.append(task)

    def is_running(self) -> bool:
        with self._admission_lock:
            return bool(self._native_work) or any(not t.done() for t in self._worker_tasks)

    def pending_slugs(self) -> set[str]:
        """Snapshot slugs with queued items or unretired native work.

        Read-only; used by the portability preflight quiescence check as
        defense-in-depth on top of the DB nonterminal-task predicate. The
        queue-owned pending counts are read under the admission lock. Native
        obligations remain visible even after a consumer/task became terminal.
        """
        with self._admission_lock:
            return {
                slug for (slug, _task_id), count in self._pending_counts.items()
                if count > 0
            } | {work.item[0] for work in self._native_work}

    def close_admission(self) -> None:
        with self._admission_lock:
            self._stopping = True

    def assert_native_idle(self) -> None:
        with self._admission_lock:
            if self._native_work:
                raise RuntimeError("native_task_drain_incomplete")

    async def _drain(self) -> None:
        for task in self._worker_tasks:
            task.cancel()
        # This join is itself queue-owned, never canceled by stop's waiter.
        # Shield both groups even if shutdown directly cancels the drain task.
        workers_done = asyncio.gather(*self._worker_tasks, return_exceptions=True)
        while not workers_done.done():
            try:
                await asyncio.shield(workers_done)
            except asyncio.CancelledError:
                pass
        workers_done.result()
        native_done = asyncio.gather(
            *(work.task for work in self._native_work), return_exceptions=True,
        )
        while not native_done.done():
            try:
                await asyncio.shield(native_done)
            except asyncio.CancelledError:
                pass
        native_done.result()
        self.assert_native_idle()

    async def stop(self, *, timeout: float = 5.0) -> None:
        self.close_admission()
        if self._stop_task is None:
            self._stop_task = asyncio.create_task(self._drain())
            # Observe a late error after timeout without dropping its owned
            # task or the native obligations. No DB work lives in callbacks.
            self._stop_task.add_done_callback(self._observe_drain)
        deadline = asyncio.get_running_loop().time() + max(0.0, timeout)
        cancellation = None
        while not self._stop_task.done():
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError("native_task_drain_incomplete")
            try:
                done, _pending = await asyncio.wait(
                    [self._stop_task],
                    timeout=remaining,
                )
                if not done:
                    raise TimeoutError("native_task_drain_incomplete")
            except asyncio.CancelledError as exc:
                cancellation = cancellation or exc
                # Keep the same deadline and owned join on repeated cancel.
        self._stop_task.result()
        if cancellation is not None:
            raise cancellation

    @staticmethod
    def _observe_drain(task: asyncio.Task) -> None:
        if task.cancelled():
            logger.error("native task drain canceled; close barrier retained")
        elif task.exception() is not None:
            logger.error("native task drain incomplete: %r", task.exception())

    async def drain_sync(self, dispatcher: _Dispatcher) -> None:
        # Retained alternative entrypoint: use exactly the same owned native
        # submission/retirement as the worker pool, including cancellation.
        self._require_ready()
        while not self._stopping and not self._queue.empty():
            await self._consume(dispatcher, self._queue.get_nowait())
