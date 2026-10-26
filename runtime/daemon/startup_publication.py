"""Native startup queue outcomes, owned by the original recovery epoch.

The request loop performs only the real queue put. Observation and retirement
belong to the native producer after its DB/marker locks have unwound.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import Future
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import threading
from typing import Any, Iterator


_CURRENT: ContextVar[tuple[StartupPublications, Any] | None] = ContextVar(
    "startup_publication", default=None,
)


def current_publication(orch: Any) -> StartupPublications | None:
    current = _CURRENT.get()
    if current is None:
        return None
    owner, original = current
    if original is not orch:
        exc = RuntimeError("startup_publication_attachment_changed")
        owner.fail(exc)
        raise exc
    try:
        owner.require_producer(orch)
    except BaseException as exc:
        owner.fail(exc)
        raise
    return owner


@dataclass(eq=False)
class _Handoff:
    epoch: str
    orch: Any
    token: str
    future: Future | None = None
    submission_attempted: bool = False
    coroutine: Any = None
    task: asyncio.Task | None = None
    native_done: threading.Event = field(default_factory=threading.Event)
    native_result: str | None = None
    native_error: BaseException | None = None
    failure_observed: bool = False


class StartupPublications:
    """At most32 retained original queue handoffs, with scalar retired counts.

    This is not an admission capability for normal callers. Only the retained
    StartupRecovery native operation can install its context. An unfinished or
    cancelled concurrent wrapper never releases a live coroutine's ownership.
    """

    def __init__(self, gate: Any) -> None:
        self.gate = gate
        self.epoch = gate.epoch
        self._lock = threading.Lock()
        self._handles: set[_Handoff] = set()
        self._active = False
        self._actions = 0
        self._thread: int | None = None
        self._closed = False
        self._first_cause: BaseException | None = None
        self._counts = dict.fromkeys((
            "scopes", "selected", "not_applicable", "held", "preserved_marker",
            "parent_attempted", "thread_attempted", "task_attempted", "producer_attempted", "submitted", "acknowledged",
            "inserted", "already_pending", "failed", "failed_handoffs", "cancelled",
            "notifications_failed", "partial", "markers_written",
            "histories_written",
        ), 0)

    def require_producer(self, orch: Any) -> None:
        try:
            current = _CURRENT.get()
            if current is None or current[0] is not self or current[1] is not orch:
                raise RuntimeError("startup_publication_context_unavailable")
            self.gate.require_publication_owner(orch)
            with self._lock:
                if (not self._active or self._closed or self.epoch != self.gate.epoch
                        or self._thread != threading.get_ident()):
                    raise RuntimeError("startup_publication_owner_unavailable")
        except BaseException as exc:
            self.fail(exc)
            raise

    def close_admission(self) -> None:
        with self._lock:
            self._closed = True

    def fail(self, cause: BaseException) -> None:
        with self._lock:
            if self._first_cause is None:
                self._first_cause = cause
                self._counts["failed"] += 1  # one mandatory failure transition
            self._closed = True
        # Never acquire the gate mutex while holding the publication mutex.
        self.gate.fail("startup_publication_failed", cause)

    def count(self, event: str) -> None:
        with self._lock:
            self._counts[event] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"observed": self._counts["scopes"] > 0, **self._counts,
                    "incomplete": len(self._handles), "actions_incomplete": self._actions,
                    "active": self._active}

    def assert_idle(self) -> None:
        with self._lock:
            if self._active or self._handles or self._actions:
                raise RuntimeError("startup_publication_incomplete")

    @contextmanager
    def scope(self, orch: Any) -> Iterator[StartupPublications]:
        try:
            self.gate.require_publication_owner(orch)
            if _CURRENT.get() is not None:
                raise RuntimeError("startup_publication_scope_nested")
            with self._lock:
                if self._active or self._closed or self._handles or self._actions:
                    raise RuntimeError("startup_publication_scope_unavailable")
                self._active = True
                self._thread = threading.get_ident()
                self._counts["scopes"] += 1
        except BaseException as exc:
            self.fail(exc)
            raise
        token = None
        primary: BaseException | None = None
        try:
            token = _CURRENT.set((self, orch))
            with self._observed(orch):
                yield self
        except BaseException as exc:
            primary = exc
            self.fail(exc)
            raise
        finally:
            try:
                if token is not None:
                    _CURRENT.reset(token)
            except BaseException as exc:
                self.fail(exc)
                if primary is None:
                    raise
            finally:
                with self._lock:
                    self._active = False

    @contextmanager
    def _observed(self, orch: Any) -> Iterator[None]:
        try:
            yield
        except BaseException as exc:
            self.fail(exc)
            try:
                self.drain(orch)
            except BaseException as secondary:
                self.fail(secondary)
            raise
        else:
            self.drain(orch)

    @contextmanager
    def notification(self, orch: Any) -> Iterator[StartupPublications]:
        """One authenticated returned-source selection; no extra discovery."""
        self.require_producer(orch)
        self.count("selected")
        before = self.snapshot()
        try:
            with self._observed(orch):
                yield self
        except BaseException:
            self.count("notifications_failed")
            after = self.snapshot()
            if (after["markers_written"] > before["markers_written"]
                    or after["acknowledged"] > before["acknowledged"]
                    or after["histories_written"] > before["histories_written"]):
                self.count("partial")
            raise
        after = self.snapshot()
        if (after["parent_attempted"] == before["parent_attempted"]
                and after["thread_attempted"] == before["thread_attempted"]
                and after["preserved_marker"] == before["preserved_marker"]
                and after["not_applicable"] == before["not_applicable"]):
            self.count("held")

    @contextmanager
    def attempt(self, orch: Any, kind: str) -> Iterator[None]:
        """Short admission; no owner mutex spans a DB-aware producer action."""
        self.require_producer(orch)
        if kind not in ("parent_attempted", "thread_attempted", "task_attempted", "producer_attempted"):
            raise RuntimeError("startup_publication_attempt_invalid")
        with self._lock:
            if self._closed or self.gate._stop.is_set():
                raise RuntimeError("startup_publication_attempt_stopped")
            self._actions += 1
            self._counts[kind] += 1
        try:
            yield
        except BaseException as exc:
            self.fail(exc)
            raise
        finally:
            with self._lock:
                self._actions -= 1

    def make_room(self, orch: Any) -> None:
        """Incremental drain BEFORE selection/registration, outside all locks."""
        self.require_producer(orch)
        with self._lock:
            full = len(self._handles) >= 32
        if full:
            self.drain(orch)

    def submit_thread(self, orch: Any, queue: Any, loop: Any, job: Any) -> None:
        from runtime.daemon.thread_queue import ThreadJob, ThreadQueue
        if (not isinstance(queue, ThreadQueue) or not isinstance(job, ThreadJob)
                or queue is not orch._thread_queue):
            raise RuntimeError("startup_publication_thread_owner_unavailable")
        self._submit(orch, queue, loop, job, job.invocation_token, "thread")

    def submit_producer(self, org: Any, kind: str, source_id: str) -> None:
        """Only the three original native producer queues, on the request loop."""
        self.gate.require_redelivery_owner(org)
        from runtime.daemon.dream_queue import DreamJob, DreamQueue
        from runtime.daemon.wake_queue import WakeJob, WakeQueue
        from runtime.daemon.schedule_queue import ScheduleJob, ScheduleQueue
        choices = {
            "dream": (org.dream_queue, DreamQueue, DreamJob, "dream_id"),
            "wake": (org.wake_queue, WakeQueue, WakeJob, "work_hour_id"),
            "schedule": (org.schedule_queue, ScheduleQueue, ScheduleJob, "schedule_id"),
        }
        if kind not in choices:
            raise RuntimeError("startup_publication_producer_kind_invalid")
        queue, expected, cls, field = choices[kind]
        if not isinstance(queue, expected):
            raise RuntimeError("startup_publication_producer_queue_unavailable")
        self._submit(org.orchestrator, queue, self.gate._loop,
                     cls(org_slug=org.slug, **{field: source_id}), source_id, kind)

    def _submit(self, orch: Any, queue: Any, loop: Any, job: Any,
                key: str, kind: str) -> None:
        """Register before scheduling; the retained native Task owns its exit."""
        self.require_producer(orch)
        if (loop is not self.gate._loop or not loop.is_running()
                or loop.is_closed() or job.org_slug != orch._slug):
            exc = RuntimeError("startup_publication_loop_or_queue_unavailable")
            self.fail(exc)
            raise exc
        handoff = _Handoff(self.epoch, orch, key)

        async def put() -> str:
            # Real loop only: no DB/recovery call or worker join here.
            handoff.task = asyncio.current_task()
            if handoff.task is None:
                raise RuntimeError("startup_publication_native_task_missing")
            handoff.task.add_done_callback(lambda _task: handoff.native_done.set())
            try:
                org = self.gate.state.orgs.get(orch._slug)
                attached = (orch._thread_queue if kind == "thread" else
                            getattr(org, kind + "_queue", None))
                if (asyncio.get_running_loop() is not loop or self.gate._stop.is_set()
                        or self.gate.epoch != handoff.epoch or org is None
                        or org.orchestrator is not orch or attached is not queue):
                    raise RuntimeError("startup_publication_put_owner_changed_or_stopped")
                handoff.native_result = await queue.put_once(job)
                return handoff.native_result
            except BaseException as exc:
                handoff.native_error = exc
                raise

        try:
            with self._lock:
                if self._closed or self.gate._stop.is_set():
                    raise RuntimeError("startup_publication_stopped")
                if len(self._handles) >= 32:
                    raise RuntimeError("startup_publication_capacity")
                self._handles.add(handoff)
                handoff.coroutine = put()
                handoff.submission_attempted = True
                handoff.future = asyncio.run_coroutine_threadsafe(handoff.coroutine, loop)
                self._counts["submitted"] += 1
        except BaseException as exc:
            # A raised scheduler call never proves that no callback was installed.
            self.fail(exc)
            raise

    @staticmethod
    def _outside_db_lock(orch: Any) -> None:
        if orch._db._lock._is_owned():
            raise RuntimeError("startup_publication_drain_under_db_lock")
        if orch._team_moves.gate._is_owned():
            raise RuntimeError("startup_publication_drain_under_move_lock")

    def _wait_future(self, future: Future) -> Any:
        # This is observation of ONE original submission, never resubmission.
        # A stopped loop cannot finish its callbacks; retain the owner visibly
        # instead of indefinitely hiding that loss in an unbounded result().
        while True:
            try:
                return future.result(timeout=0.1)
            except TimeoutError:
                if future.done():
                    return future.result()  # a real put's TimeoutError
                if self.gate._loop.is_closed() or not self.gate._loop.is_running():
                    raise RuntimeError("startup_publication_loop_lost")

    def _native_ended(self, handoff: _Handoff) -> bool:
        if handoff.native_done.is_set():
            return True
        # Cancellation before the coroutine began supplies no actual Task
        # completion callback. Keep that uncertainty; do not infer no effect.
        if handoff.task is None:
            return False
        while not handoff.native_done.wait(0.1):
            if self.gate._loop.is_closed() or not self.gate._loop.is_running():
                raise RuntimeError("startup_publication_native_exit_unobserved")
        return True

    def drain(self, orch: Any) -> None:
        """Consume real outcomes on the native owner, never on the put loop.

        Cancelled wrappers whose coroutine has not positively ended stay in
        the bounded set. Shutdown must retain DB/loop ownership in that case.
        """
        try:
            if self._thread != threading.get_ident() or self.epoch != self.gate.epoch:
                raise RuntimeError("startup_publication_wrong_native_owner")
            self._outside_db_lock(orch)
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                raise RuntimeError("startup_publication_drain_on_loop")
            with self._lock:
                handles = tuple(self._handles)  # bounded32, no retired backlog
            for handoff in handles:
                self._outside_db_lock(handoff.orch)
                ended = not handoff.submission_attempted
                try:
                    if handoff.epoch != self.epoch:
                        raise RuntimeError("startup_publication_epoch_changed")
                    if handoff.future is None:
                        raise RuntimeError("startup_publication_submission_failed")
                    result = self._wait_future(handoff.future)
                    ended = self._native_ended(handoff)
                    if (not ended or handoff.native_error is not None
                            or result != handoff.native_result
                            or result not in ("inserted", "already_pending")):
                        raise RuntimeError("startup_publication_put_outcome_unknown")
                    self.count("acknowledged")
                    self.count(result)
                except BaseException as exc:
                    if not handoff.failure_observed:
                        handoff.failure_observed = True
                        self.count("failed_handoffs")
                        if handoff.future is not None and handoff.future.cancelled():
                            self.count("cancelled")
                    self.fail(exc)
                    try:
                        ended = ended or self._native_ended(handoff)
                    except BaseException as secondary:
                        self.fail(secondary)
                if ended:
                    with self._lock:
                        self._handles.remove(handoff)
            with self._lock:
                cause = self._first_cause
                incomplete = bool(self._handles)
            if cause is not None:
                raise cause
            if incomplete:
                raise RuntimeError("startup_publication_incomplete")
        except BaseException as exc:
            self.fail(exc)
            raise
