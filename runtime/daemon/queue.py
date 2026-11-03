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
from concurrent.futures import Future
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import partial
import logging
import threading
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Protocol

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

# Only the current executor invocation binds this thread. A loop bridge may
# observe that SAME work; absence supplies no substitute queue owner.
_native_loop_invocation = threading.local()


class _Dispatcher(Protocol):
    def task_attachment(self, slug: str) -> OrgState | None: ...
    def run_step(self, slug: str, task_id: str, metadata: dict | None = None, **kwargs: object) -> Any: ...
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
    preparation_returned: bool = False
    run_returned: bool = False
    run_result: object = None
    heartbeat: asyncio.Task | None = None
    heartbeat_join: asyncio.Future | None = None
    attempted: set[str] = field(default_factory=set)
    completed: dict[str, object] = field(default_factory=dict)
    first_error: dict[str, Any] | None = None
    error_occurrences: list[dict[str, Any]] = field(default_factory=list)
    native_joins: list[dict[str, Any]] = field(default_factory=list)
    native_join: dict[str, Any] | None = None
    join_delivery: dict[str, Any] | None = None
    coroutine: Any = None
    submission: dict[str, Any] = field(default_factory=dict)
    capture_lock: Any = field(default_factory=threading.Lock, repr=False)
    invocations: dict[str, dict[str, Any]] = field(default_factory=dict)
    error_capture: dict[str, Any] | None = None
    pending_error: dict[str, Any] | None = None
    retention_error: tuple | None = None
    failure_pending: bool = False
    raw_failure: dict[str, Any] | None = None

    def _remember_error(self, phase: str, error: BaseException, *, delivery: str = "raised",
                        invocation: dict[str, Any] | None = None,
                        deferred: bool = False,
                        bridge: dict[str, Any] | None = None) -> dict[str, Any]:
        # Native threads publish ONLY raw same-work evidence. The loop later
        # owns collection/formatting/delivery. This short lock orders original
        # observations, not historical exception times or independent owners.
        observed = dict(phase=phase, error=error,
                        traceback=BaseException.__traceback__.__get__(error),
                        delivery=delivery, kind=type(error), message=None, message_observed=False,
                        description_attempted=False)
        with self.capture_lock:
            if bridge is not None:
                observed['bridge_previous'] = bridge['error_capture']
                bridge['error_capture'] = observed
                if bridge['first_error'] is None:
                    bridge['first_error'] = observed
            forwarded = None if deferred else self.join_delivery
            if forwarded is not None and forwarded['error'] is error:
                observed.update(delivery="context_delivery", join_cause=forwarded,
                    traceback=forwarded['traceback'], message=forwarded['message'],
                    message_observed=forwarded['message_observed'], description_attempted=True)
            else:
                forwarded = None
            if invocation is not None:
                if delivery == "native_error":
                    invocation['error_capture'] = observed
                if invocation['first_observation'] is None:
                    invocation['first_observation'] = observed
                observed['invocation_first'] = invocation['first_observation']
            selected = forwarded if forwarded is not None else observed.get('invocation_first', observed)
            observed['previous'] = self.error_capture
            self.error_capture = self.pending_error = observed
            if delivery in ("native_error", "repeated_entry") and self.raw_failure is None:
                self.raw_failure = observed
            if not deferred:
                if self.first_error is None:
                    self.first_error = (selected if self.raw_failure is None else
                        self.raw_failure.get('invocation_first', self.raw_failure))
                # Forwarding is consumed only AFTER its raw linked record is
                # retained, never before a fallible list append.
                self.join_delivery = None
        if deferred:
            return observed

        def secondary_capture(suffix: str, secondary: BaseException) -> None:
            # No recursive retention or diagnostic retry. Raw prior/pending
            # records remain reachable even if the occurrence collection fails.
            later = dict(phase=phase + suffix, error=secondary,
                traceback=BaseException.__traceback__.__get__(secondary), delivery="raised",
                kind=type(secondary), message=None, message_observed=False, description_attempted=True)
            with self.capture_lock:
                later['previous'] = self.error_capture
                self.error_capture = self.pending_error = later
                self.retention_error = (later, self.retention_error)

        try:
            self.error_occurrences.append(observed)
        except BaseException as secondary:
            secondary_capture("_retention", secondary)
        else:
            with self.capture_lock:
                if self.pending_error is observed:
                    self.pending_error = None
        for record in (selected, observed) if selected is not observed else (observed,):
            if record.get('description_attempted', False):
                continue
            record['description_attempted'] = True
            try:
                record['message'] = str(record['error'])
                record['message_observed'] = True
            except BaseException as secondary:
                secondary_capture("_message", secondary)
        return observed

    def _observe_failure(self, *, outer_error: bool = False) -> dict[str, Any] | None:
        # Freeze only this work's original captured failure at THIS reader's
        # observation, before its collection/diagnostics or another observation.
        # A successful suppressed join cancellation is not a failure marker.
        with self.capture_lock:
            if self.failures or self.failure_pending:
                if self.first_error is not None:
                    return self.first_error
            if self.raw_failure is not None:
                return (self.first_error if self.first_error is not None else
                        self.raw_failure.get('invocation_first', self.raw_failure))
            if outer_error:
                # A bridge/waiter can observe E1 before executor delivery.
                # Freeze that candidate only for an actual outer error; it
                # neither confirms failure nor changes retirement predicates.
                for invocation in self.invocations.values():
                    if invocation['returned'] or invocation['caller_returned']:
                        continue  # A handled candidate cannot poison a later stage.
                    cause = invocation['first_observation']
                    if cause is not None:
                        return self.first_error if self.first_error is not None else cause
            return None

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


class _AttachmentNotificationQueue:
    """Per-attachment adapter: native publisher, actual raw put on its loop.

    The native caller waits for the SAME synchronous put result before its
    acknowledgement/failure bookkeeping. No DB/profile work runs in the loop
    callback and no queue/map mutex is held while waiting. A lost loop never
    supplies retirement: the attachment's native worker remains owned.
    """

    def __init__(self, queue: TaskQueue, loop: asyncio.AbstractEventLoop, thread_queue: Any) -> None:
        self._queue = queue
        self._loop = loop
        self.task_puts: list[Future] = []
        self.thread_queue = _AttachmentThreadQueue(thread_queue)
        self.thread_queue._loop = loop
        self._attachment_org = thread_queue._startup_org
        self._attachment_selection = (None if self._attachment_org is None else
                                      queue._publication_selection(self._attachment_org.slug))
        # The attachment is selected before native returned discovery starts.
        # The thread notification renews it on its own actual loop too.
        if self._attachment_org is not None:
            self.thread_queue._validate = self._publication_selection(self._attachment_org.slug)

    def enqueue(self, *args: object, **kwargs: object) -> None:
        slug = args[0] if args else kwargs["slug"]
        task_id = args[1] if len(args) > 1 else kwargs["task_id"]
        attachment = self._publication_selection(slug)
        selected = tuple(validate for selected_slug, selected_task, validate in
                         getattr(self._queue._publisher_validations, "stack", ())
                         if (selected_slug, selected_task) == (slug, task_id))
        supplied = kwargs.get("_queue_validation")
        def renew() -> None:
            attachment()
            for validate in selected:
                validate()
            if supplied is not None:
                supplied()
        kwargs["_queue_validation"] = renew
        result: Future = Future()
        self.task_puts.append(result)  # retain BEFORE the thread-safe submission
        def put() -> None:
            try:
                if asyncio.get_running_loop() is not self._loop:
                    from runtime.daemon.startup_recovery import StartupUnavailable
                    raise StartupUnavailable("startup_attachment_changed")
                self._queue.enqueue(*args, **kwargs)
            except BaseException as exc:
                result.set_exception(exc)
            else:
                result.set_result(None)
        # A submission exception leaves this actual outcome pending. A known
        # callback failure completes it with that error instead. Neither path
        # substitutes or wraps the original publisher exception.
        self._loop.call_soon_threadsafe(put)
        result.result()

    def put_nowait(self, *args: object, **kwargs: object) -> None:
        self.enqueue(*args, **kwargs)

    def _publication_selection(self, slug: str) -> Callable[[], None]:
        queue, loop, threads = self._queue, self._loop, self.thread_queue
        org, original = self._attachment_org, self._attachment_selection
        selected = queue._publication_selection(slug)
        def validate() -> None:
            from runtime.daemon.startup_recovery import StartupUnavailable
            if (self._queue is not queue or self._loop is not loop or self.thread_queue is not threads
                    or self._attachment_org is not org or self._attachment_selection is not original
                    or threads._loop is not loop
                    or org is not None and (slug != org.slug or threads._queue is not org.thread_queue
                                           or loop is not org._startup_recovery._loop)):
                raise StartupUnavailable("startup_attachment_changed")
            if original is not None:
                original()
            selected()
        validate()
        return validate

    @contextmanager
    def _publication_scope(self, slug: str, task_id: str, *, _selection=None):
        selected = self._publication_selection(slug) if _selection is None else _selection
        with self._queue._publication_scope(slug, task_id, _selection=selected):
            yield

    def enqueue_if_absent(self, slug: str, task_id: str, *, metadata: dict | None = None, publisher: Callable | None = None) -> bool:
        # Original reservation and generation publisher run natively. Their
        # supplied publisher reaches this adapter only for the real raw put.
        first_put = len(self.task_puts)
        return self._queue.enqueue_if_absent(slug, task_id, metadata=metadata,
            publisher=publisher or (lambda: self.enqueue(slug, task_id, metadata=metadata)),
            _put_outcomes=lambda: tuple(self.task_puts[first_put:]))

    def has_unretired_puts(self) -> bool:
        return any(not done.done() or done.cancelled() for done in self._puts())

    def _puts(self) -> tuple[Future, ...]:
        return (*self.task_puts, *self.thread_queue.puts)

    async def join_puts(self) -> None:
        cancellation = None
        for done in self._puts():
            bridge = asyncio.wrap_future(done)
            while not bridge.done():
                try:
                    await asyncio.shield(bridge)
                except asyncio.CancelledError as exc:
                    cancellation = cancellation or exc
            bridge.result()
        if cancellation is not None:
            raise cancellation


class _AttachmentThreadQueue:
    """Retain the original notification's actual loop put until it returns."""

    def __init__(self, queue: Any) -> None:
        self._queue = queue
        self.puts: list[Future] = []
        self._validate: Callable[[], None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def put(self, job: Any) -> Awaitable[None]:
        done: Future = Future()
        self.puts.append(done)  # before the original thread-safe submission
        async def publish() -> None:
            try:
                if self._validate is not None:
                    self._validate()
                if self._loop is not None and asyncio.get_running_loop() is not self._loop:
                    from runtime.daemon.startup_recovery import StartupUnavailable
                    raise StartupUnavailable("startup_attachment_changed")
                await self._queue.put(job)
            except BaseException as exc:
                done.set_exception(exc)
                raise
            else:
                done.set_result(None)
        return publish()


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
        # Native and attachment publishers retain an insertion receipt that
        # survives dequeue while their exact generation acknowledgement runs.
        self._native_publication_puts: dict[tuple[str, str], bool] = {}
        self._queue: asyncio.Queue[_QueueItem] = _TrackedAsyncQueue(
            self._admission_lock, self._record_dequeue, self._require_ready,
        )
        self._worker_tasks: list[asyncio.Task] = []
        self._stopping = False
        self._native_work: set[_NativeWork] = set()
        self._stop_task: asyncio.Task | None = None
        self._submission_failure: dict[str, Any] | None = None
        self._drain_owner: dict[str, Any] | None = None
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
                if (slug, task_id) in self._native_publication_puts:
                    self._native_publication_puts[(slug, task_id)] = True
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
        _put_outcomes: Callable[[], tuple[Future, ...]] | None = None,
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
        reservation is released after the original publication retires. An
        uncertain attachment loop submission retains this SAME reservation;
        only the actual put's positive retirement can qualify its release.
        A successful insertion remains owned by the pending count until the
        same locked dequeue operation, so a later qualifying wake is admitted.
        """
        validate, coding_validation = self._require_publication(
            slug, task_id, metadata, _publication, _coding_validation,
        )
        if not self._reserve_bounded_enqueue(slug, task_id, validate=validate,
                                             track_put=_put_outcomes is not None):
            return False
        key = slug, task_id
        primary = None
        try:
            if publisher is None:
                self.enqueue(slug, task_id, metadata=metadata, _publication=_publication,
                             _coding_validation=coding_validation, _queue_validation=validate)
            else:
                validate()
                with self._publication_scope(slug, task_id, _selection=validate):
                    publisher()
            puts = () if _put_outcomes is None else _put_outcomes()
            inserted = _put_outcomes is None or any(
                done.done() and not done.cancelled() and done.exception() is None for done in puts
            )
            with self._admission():
                validate()
                return not self._stopping and inserted and (
                    self._native_publication_puts[key] if _put_outcomes is not None
                    else self._pending_counts[key] > 0
                )
        except BaseException as exc:
            primary = exc
            raise
        finally:
            # Inspect only this call's actual outcomes, outside admission. A
            # publisher may catch a put/submission error and return normally.
            # Cancelled/missing acknowledgements retain the original reservation.
            retired = _put_outcomes is None
            try:
                if _put_outcomes is not None:
                    retired = all(done.done() and not done.cancelled() for done in _put_outcomes())
            except BaseException as secondary:
                if primary is None:
                    raise
                BaseException.__setattr__(primary, "_queue_put_outcome_secondary",
                                          (secondary, secondary.__traceback__))
            if retired:
                self._release_bounded_enqueue(key)

    def _reserve_bounded_enqueue(
        self, slug: str, task_id: str, *, validate: Callable[[], None], track_put: bool = False,
    ) -> bool:
        key = slug, task_id
        with self._admission():
            # Every synchronous ancestor renews after this mutex wait too.
            for selected_slug, selected_task, selected in getattr(self._publisher_validations, "stack", ()):
                if (selected_slug, selected_task) == key:
                    selected()
            validate()
            stopping = self._stopping
            if not stopping:
                if self._pending_counts[key] or key in self._admission_reservations:
                    return False
                self._admission_reservations.add(key)
                if track_put:
                    self._native_publication_puts[key] = False
        if stopping:
            self._log_stopping(slug, task_id)
        return not stopping

    def _release_bounded_enqueue(self, key: tuple[str, str]) -> None:
        # Owed retirement is independent of fresh admission. No readiness or
        # DB work runs here, including after a successful put and late refusal.
        with self._admission_lock:
            self._admission_reservations.discard(key)
            self._native_publication_puts.pop(key, None)

    async def _enqueue_if_absent_native(
        self, slug: str, task_id: str, *, publisher: Callable[[], Awaitable[str]],
        _queue_validation: Callable[[], None] | None = None,
        _put_outcomes: Callable[[], tuple[Future, ...]] | None = None,
    ) -> bool:
        """Same bounded reservation for the joined native publisher only."""
        validate, _ = self._require_publication(slug, task_id, None, None)
        def renew() -> None:
            if _queue_validation is not None:
                _queue_validation()
            validate()
        if not self._reserve_bounded_enqueue(slug, task_id, validate=renew, track_put=True):
            return False
        key = slug, task_id
        retired = _put_outcomes is not None
        primary = None
        try:
            renew()
            await publisher()
            # Consume actual per-call raw outcomes outside the mutex. Another
            # ordinary enqueue of this key cannot acknowledge this publisher.
            puts = () if _put_outcomes is None else _put_outcomes()
            inserted = _put_outcomes is not None and any(
                done.done() and not done.cancelled() and done.exception() is None
                and done.result() is True for done in puts
            )
            with self._admission():
                renew()
                return not self._stopping and inserted and self._native_publication_puts[key]
        except BaseException as exc:
            primary = exc
            # The original native service records missing/cancelled wrappers
            # and submission loss. Its uncertainty is not callback retirement.
            retired = _put_outcomes is not None and not BaseException.__getattribute__(exc, "__dict__").get(
                "_team_move_native_unretired", False,
            )
            raise
        finally:
            if _put_outcomes is not None:
                try:
                    retired = retired and all(done.done() and not done.cancelled() for done in _put_outcomes())
                except BaseException as secondary:
                    retired = False
                    if primary is None:
                        raise
                    BaseException.__setattr__(primary, "_queue_put_outcome_secondary",
                                              (secondary, secondary.__traceback__))
            if retired:
                self._release_bounded_enqueue(key)

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
        # The SAME work/Future owns the captures even if exception attachment
        # fails. A suppressed cancellation is evidence, not a worker failure.
        join = dict(work=work, future=future, previous=work.native_join,
                    first_error=None, occurrences=[],
                    returned=False, result=None, acknowledgement="UNKNOWN",
                    retention_error=None, pending_capture=None, attachment_error=None)
        work.native_join = join
        try:
            work.native_joins.append(join)
        except BaseException as error:
            join['retention_error'] = (error, BaseException.__traceback__.__get__(error))
        first = None
        cancellation_observed = False
        invocation = next((original for original in work.invocations.values()
                           if original['future'] is future), None)

        def capture(error: BaseException, delivery: str) -> dict[str, Any]:
            observed = work._remember_error("native_join", error, delivery=delivery,
                                            invocation=invocation, deferred=True)
            # Same original outcome re-observation, not incident deduplication.
            # Selection uses the snapshot taken at the ORIGINAL observation.
            native = None if invocation is None else invocation.get('error_capture')
            if native is not None and native['error'] is error:
                observed['invocation_cause'] = native
                observed['traceback'] = native['traceback']
                observed['outcome_observation'] = delivery
            return observed

        def keep(observed: dict[str, Any]) -> None:
            # Raw evidence precedes observation/formatting/attachment. Failed
            # retention stays here/on the same owner, without a recursive retry.
            join['pending_capture'] = observed
            try:
                join['occurrences'].append(observed)
                join['pending_capture'] = None
            except BaseException as error:
                join['retention_error'] = (error, BaseException.__traceback__.__get__(error),
                                           observed, join['retention_error'])

        def describe(observed: dict[str, Any]) -> None:
            cause = observed.get('invocation_cause')
            if cause is not None:
                describe(cause)
                observed['message'] = cause['message']
                observed['message_observed'] = cause['message_observed']
                observed['description_attempted'] = True
                return
            if observed['delivery'] in ("shield_reobservation", "result_reobservation"):
                observed['join_cause'] = first
                observed['message'] = first['message']
                observed['message_observed'] = first['message_observed']
                return
            if observed.get('description_attempted', False):
                return
            observed['description_attempted'] = True
            try:
                observed['message'] = str(observed['error'])
                observed['message_observed'] = True
            except BaseException as error:
                keep(capture(error, "message_error"))  # no recursive formatting

        # Shield and consume the original Future, including after repeated
        # waiter cancellation. Never submit/cancel/replace native work here.
        while True:
            try:
                value = await asyncio.shield(future)
            except asyncio.CancelledError as error:
                observed = capture(error, "cancellation_observation")
                if first is None:
                    first = observed.get('invocation_first', observed)
                    if first is not observed:
                        keep(first)
                join['first_error'] = first
                keep(observed)
                work.abandoned = True
                done = False
                stored = None
                observation = None
                try:
                    done = future.done()
                    if done:
                        if future.cancelled():
                            join['acknowledgement'] = "UNKNOWN"
                        elif not cancellation_observed:
                            # The gather retains its original stored-Future
                            # rule. Captured executor invocations instead use
                            # their original synchronized observation above.
                            stored = future.exception()
                            if stored is not None and invocation is None:
                                first = capture(stored, "stored_native_error")
                                join['first_error'] = first
                                keep(first)
                except BaseException as secondary:
                    observation = capture(secondary, "observation_error")
                    keep(observation)
                cancellation_observed = True
                if first is not observed:
                    describe(first)
                if stored is not None:
                    if stored is error:
                        observed['delivery'] = "shield_reobservation"
                describe(observed)
                if observation is not None:
                    describe(observation)
                if not done:
                    continue
                # A done cancelled wrapper is UNKNOWN, never native-exit
                # proof. Consume once and leave; do not spin on a done Future.
                try:
                    value = future.result()
                except BaseException as secondary:
                    later = capture(secondary, "result_reobservation" if stored is secondary
                                    else "done_result_delivery")
                    keep(later)
                    describe(later)
                    break
            except BaseException as error:
                observed = capture(error, "shield_delivery")
                if first is None:
                    first = observed.get('invocation_first', observed)
                    if first is not observed:
                        keep(first)
                join['first_error'] = first
                keep(observed)
                if first is not observed:
                    describe(first)
                describe(observed)
                break
            # Preserve successful preparation, run, gather and retirement
            # values exactly, even after cancellation or failed diagnostics.
            join['result'] = value
            join['returned'] = True
            join['acknowledgement'] = "returned_value"
            return value

        # A failed join delivers its authentic selected cause. The raw record
        # precedes fallible attachment; an inaccessible attachment leaves join
        # in the SAME rethrown frame. Caller containment/tails stay unchanged.
        with work.capture_lock:
            if work.first_error is None:
                work.first_error = first
            else:
                first = work.first_error
            join['first_error'] = first
            work.join_delivery = first
        try:
            primary = first['error']
            values = BaseException.__dict__['__dict__'].__get__(primary)
            previous = values.get('_queue_native_join_failure')
            BaseException.__setattr__(primary, '_queue_native_join_failure',
                                      dict(join=join, previous=previous))
        except BaseException as error:
            join['attachment_error'] = capture(error, "attachment_error")
            keep(join['attachment_error'])
            describe(join['attachment_error'])
        raise BaseException.with_traceback(first['error'], first['traceback'])

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
        submission = work.submission
        submission.update(work=work, first_error=None, occurrences=[], phase="org_acquire")
        try:
            if attachment is not None:
                submission['org_acquire_attempted'] = True
                acquired = attachment._acquire_native_task(work)
                submission['org_acquire_returned'] = acquired
                if not acquired:
                    return None
            submission['phase'] = "queue_registration"
            submission['registration_attempted'] = True
            with self._admission_lock:
                self._native_work.add(work)
            submission['registered'] = True
            # Keep the ORIGINAL coroutine before the uncertain handoff. A
            # factory may schedule it and then raise without returning a Task.
            submission['phase'] = "task_submission"
            work.coroutine = self._run_owned(work)
            submission['attempted'] = True
            work.task = asyncio.create_task(work.coroutine)
            submission['returned_task'] = work.task
            submission['returned'] = True
        except BaseException as error:
            submission['first_observation'] = dict(phase=submission['phase'], error=error,
                traceback=BaseException.__traceback__.__get__(error))
            cause = submission['work_observation'] = work._observe_failure(outer_error=True)
            if submission['first_error'] is None and cause is not None:
                submission['first_error'] = cause
            observed = self._capture_queue_error(submission, submission['phase'], error, describe=False)
            # The raw native snapshot precedes E2's fallible retention. Its
            # original invocation provenance survives missing Task delivery.
            if cause is not None:
                self._capture_queue_error(submission, "native_before_submission_error",
                                          cause['error'], cause=cause)
            self._capture_queue_error(submission, submission['phase'], error, captured=observed)
            first = submission['first_error']
            # Retain even a partial acquire/registry publication. No retry,
            # coroutine close/cancel, token repair or no-run inference follows.
            submission['previous'] = self._submission_failure
            submission['first_failure'] = (submission if self._submission_failure is None
                                           else self._submission_failure['first_failure'])
            self._submission_failure = submission
            try:
                primary = first['error']
                values = BaseException.__dict__['__dict__'].__get__(primary)
                BaseException.__setattr__(primary, '_queue_submission_failure',
                    dict(submission=submission, previous=values.get('_queue_submission_failure')))
            except BaseException as secondary:
                self._capture_queue_error(submission, "submission_attachment", secondary)
            raise BaseException.with_traceback(first['error'], first['traceback'])
        return work

    async def _run_owned(self, work: _NativeWork) -> None:
        slug, task_id, metadata = work.item
        loop = hb = t0 = None
        phase = "setup"
        invocation = None

        def handoff(stage: str, callback: Callable[[], object]) -> asyncio.Future:
            nonlocal invocation
            original = dict(work=work, item=work.item, attachment=work.attachment,
                callable=callback, owner=work.owner, stage=stage, submission_attempted=False,
                submission_returned=False, future=None, entered=False, returned=False,
                result=None, error_capture=None, exited=False, caller_returned=False,
                caller_result=None, first_observation=None, loop_bridge=None)
            invocation = work.invocations[stage] = original

            def invoke() -> object:
                with work.capture_lock:
                    entered = original['entered']
                    if not entered:
                        original['entered'] = True
                if entered:
                    # Do not call the original callback a second time, even
                    # if an executor unexpectedly invokes its wrapper twice.
                    error = RuntimeError("native_task_invocation_repeated")
                    original['repeated_entry'] = work._remember_error(stage, error,
                        delivery="repeated_entry", invocation=original, deferred=True)
                    raise error
                primary = None
                binding_attempted = False
                try:
                    previous = getattr(_native_loop_invocation, 'value', None)
                    original['binding_previous'] = previous
                    binding_attempted = True
                    _native_loop_invocation.value = (work, original)
                    value = callback()  # Original bound callable/arguments, outside lock.
                except BaseException as error:
                    primary = error
                    work._remember_error(stage, error, delivery="native_error",
                                         invocation=original, deferred=True)
                    raise
                else:
                    with work.capture_lock:
                        original['result'] = value
                        original['returned'] = True
                    return value
                finally:
                    try:
                        if binding_attempted:
                            _native_loop_invocation.value = previous
                    except BaseException as error:
                        work._remember_error(stage + "_binding_reset", error,
                            delivery="native_error", invocation=original, deferred=True)
                        if primary is None:
                            raise
                    finally:
                        with work.capture_lock:
                            original['exited'] = True

            original['wrapper'] = invoke
            original['submission_attempted'] = True
            future = loop.run_in_executor(None, invoke)
            original['future'] = future
            original['submission_returned'] = True
            return future

        def failed(phase: str, error: BaseException, observed: dict | None = None) -> None:
            if observed is None:
                observed = work._remember_error(phase, error)
            work.failure_pending = True  # A failed list append cannot erase this failure.
            try:
                work.failures.append(error)
            except BaseException as retention:
                work._remember_error(phase + "_failure_retention", retention)
            try:
                logger.exception("native task %s/%s %s failed; retaining close barrier",
                    slug, task_id, phase,
                    exc_info=(type(error), error, observed['traceback']))
            except BaseException as diagnostic:
                work._remember_error(phase + "_diagnostic", diagnostic)
                try:
                    work.failures.append(diagnostic)
                except BaseException as retention:
                    work._remember_error(phase + "_diagnostic_retention", retention)

        try:
            loop = asyncio.get_running_loop()
            t0 = time.monotonic()
            phase = "readiness"
            self._require_ready()
            if self._stopping or work.abandoned:
                return
            phase = "admission"
            admission = getattr(work.dispatcher, "task_admission_allowed", None)
            if admission is not None and not work.call(
                "task_admission_allowed", slug, task_id, metadata,
            )():
                return
            if getattr(work.dispatcher, "worker_started", None) is not None:
                phase = "preparation"
                prepare = work.call("worker_started", slug, task_id)
                work.attempted.add("preparation_submit")
                work.preparation = handoff("preparation", prepare)
                work.owner = await self._join_native(work, work.preparation)
                work.preparation_returned = True
                work.completed['preparation'] = work.owner
                invocation['caller_returned'] = True
                invocation['caller_result'] = work.owner
                invocation = None
            # Late preparation after cancellation must retire its returned
            # owner, without launching another native run during shutdown.
            if not self._stopping and not work.abandoned:
                phase = "readiness"
                self._require_ready()
                phase = "heartbeat_start"
                work.attempted.add("heartbeat_start")
                hb = work.heartbeat = asyncio.create_task(self._heartbeat(work))
                phase = "run_submission"
                run = work.call("run_step", slug, task_id, metadata)
                work.attempted.add("run_submit")
                work.run = handoff("run", run)
                phase = "run"
                work.run_result = await self._join_native(work, work.run)
                # None is an observed return too. Keep the exact object before
                # any heartbeat, owner, accounting or diagnostic tail can fail.
                work.run_returned = True
                invocation['caller_returned'] = True
                invocation['caller_result'] = work.run_result
                invocation = None
        except BaseException as exc:
            observed = work._remember_error(phase, exc, invocation=invocation)
            if invocation is not None:
                invocation['caller_error'] = observed
                if not invocation['submission_returned']:
                    invocation['submission_error'] = observed
            try:
                from runtime.daemon.startup_recovery import StartupUnavailable
                # Only the known no-run refusal is contained without a worker
                # failure. A submission attempt with no Future is uncertain.
                refused = (isinstance(exc, StartupUnavailable) and work.run is None
                           and "run_submit" not in work.attempted
                           and (invocation is None or (invocation['submission_returned']
                                and invocation['future'] is not None
                                and invocation['future'].done() and not invocation['future'].cancelled())))
            except BaseException as secondary:
                failed(phase, exc, observed)
                failed("refusal_classification", secondary)
            else:
                if not refused:
                    failed(phase, exc, observed)
        finally:
            invocation = None
            if hb is not None:
                try:
                    work.attempted.add("heartbeat_cancel")
                    work.completed['heartbeat_cancel'] = hb.cancel()
                except BaseException as exc:
                    failed("heartbeat_cancel", exc)
                try:
                    # One original gather/join, even after cancellation failed.
                    work.attempted.add("heartbeat_join")
                    work.heartbeat_join = asyncio.gather(hb, return_exceptions=True)
                    errors = await self._join_native(work, work.heartbeat_join)
                    work.completed['heartbeat_join'] = errors
                    for error in errors:
                        if isinstance(error, BaseException):
                            if (isinstance(error, asyncio.CancelledError)
                                    and work.completed.get('heartbeat_cancel') is True
                                    and hb.cancelled()):
                                continue  # acknowledgement of our own cancellation
                            observed = work._remember_error("heartbeat_result", error,
                                                            delivery="gather_result")
                            failed("heartbeat_result", error, observed)
                except BaseException as exc:
                    failed("heartbeat_join", exc)
            # Retire only a positively returned prepared owner. A genuinely
            # ended failed run still qualifies; a cancelled/missing wrapper or
            # uncertain submission does not prove return. No replacement join.
            try:
                returned = ((work.run is None and "run_submit" not in work.attempted)
                            or (work.run is not None and work.run.done() and not work.run.cancelled()))
                if work.preparation_returned and work.owner is not None and returned:
                    work.attempted.add("retirement")
                    work.retirement = handoff("retirement",
                        partial(work.dispatcher.worker_finished, slug, task_id, work.owner))
                    work.completed['retirement'] = await self._join_native(work, work.retirement)
                    invocation['caller_returned'] = True
                    invocation['caller_result'] = work.completed['retirement']
            except BaseException as exc:
                observed = work._remember_error("retirement", exc, invocation=invocation)
                if invocation is not None:
                    invocation['caller_error'] = observed
                    if not invocation['submission_returned']:
                        invocation['submission_error'] = observed
                failed("retirement", exc, observed)
            try:
                work.attempted.add("task_done")
                work.completed['task_done'] = self._queue.task_done()
            except BaseException as exc:
                failed("task_done", exc)
            if self._metrics_registry is not None and t0 is not None:
                try:
                    work.attempted.add("metrics")
                    work.completed['metrics'] = self._metrics_registry.record_loop_tick(
                        "run_step_worker", 0, time.monotonic() - t0,
                    )
                except BaseException as exc:
                    failed("metrics", exc)
            if not work.failures and not work.failure_pending and work.attachment is not None:
                try:
                    work.attempted.add("org_release")
                    work.completed['org_release'] = work.attachment._release_native_task(work)
                except BaseException as exc:
                    # Release can effect-then-raise. Keep the SAME queue work;
                    # no retry, re-registration or native-success inference.
                    failed("org_release", exc)
            # Keep original org/queue barriers through accounting and metrics;
            # all effects and waits above are outside this short registry lock.
            if not work.failures and not work.failure_pending:
                try:
                    work.attempted.add("queue_remove")
                    with self._admission_lock:
                        self._native_work.remove(work)
                    work.completed['queue_remove'] = None
                except BaseException as exc:
                    failed("queue_remove", exc)
            if work.failures or work.failure_pending:
                # Preserve nested/prior records without mutating their bytes.
                # Even failed attachment leaves the full record on SAME work.
                try:
                    work.attempted.add("error_attachment")
                    primary = work.first_error['error']
                    values = BaseException.__dict__['__dict__'].__get__(primary)
                    previous = values.get('_queue_native_failure')
                    BaseException.__setattr__(primary, '_queue_native_failure',
                                              dict(work=work, previous=previous))
                    work.completed['error_attachment'] = None
                except BaseException as exc:
                    work._remember_error("error_attachment", exc)
                    try:
                        work.failures.append(exc)
                    except BaseException as retention:
                        work._remember_error("error_attachment_retention", retention)

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

    def _has_native_work_for(self, attachment: OrgState) -> bool:
        """Observe retained work for this exact captured attachment only."""
        with self._admission_lock:
            return any(work.attachment is attachment for work in self._native_work)

    @staticmethod
    def _capture_queue_error(owner: dict[str, Any], phase: str, error: BaseException,
                             *, cause: dict[str, Any] | None = None, describe: bool = True,
                             captured: dict[str, Any] | None = None) -> dict[str, Any]:
        # Only the selected submission/drain/waiter owners use this leaf. Raw
        # first cause precedes fallible retention, formatting and attachment.
        if cause is not None and cause['error'] is not error:
            cause = None  # An unrelated actual delivery is a new occurrence.
        observed = captured
        if observed is None:
            observed = dict(phase=phase, error=error,
                            traceback=BaseException.__traceback__.__get__(error),
                            kind=type(error), message=None, message_observed=False,
                            delivery="raised" if cause is None else "context_observation")
            if cause is not None:
                observed.update(cause=cause, traceback=cause['traceback'],
                                message=cause['message'], message_observed=cause['message_observed'])
            if owner.get('first_error') is None:
                candidate = None if owner.get('completed') else owner.get('cancellation_candidate')
                owner['first_error'] = observed if candidate is None else candidate
            owner['pending_capture'] = observed
            try:
                owner.setdefault('occurrences', []).append(observed)
                owner['pending_capture'] = None
            except BaseException as secondary:
                owner['capture_failed'] = True
                owner['retention_error'] = (secondary, BaseException.__traceback__.__get__(secondary),
                                            observed, owner.get('retention_error'))
        if describe and cause is None:
            try:
                observed['message'] = str(error)
                observed['message_observed'] = True
            except BaseException as secondary:
                owner['capture_failed'] = True
                # Do not recursively format a failed diagnostic. This linked
                # raw occurrence survives a failed occurrences append too.
                later = dict(phase=phase + "_message", error=secondary,
                             traceback=BaseException.__traceback__.__get__(secondary),
                             kind=type(secondary), message=None, message_observed=False,
                             delivery="raised", previous=owner.get('message_error'))
                owner['message_error'] = later
                try:
                    owner.setdefault('occurrences', []).append(later)
                except BaseException as retention:
                    owner['retention_error'] = (retention, BaseException.__traceback__.__get__(retention),
                                                later, owner.get('retention_error'))
        return observed

    async def _drain(self) -> None:
        owner = self._drain_owner
        if owner is None:
            # A direct original caller owns this operation too; stop cannot
            # create a replacement merely because it has no returned handle.
            owner = self._drain_owner = dict(first_error=None, occurrences=[], submission_attempted=True)
        if owner.get('entered'):
            error = RuntimeError("native_task_drain_already_entered")
            observed = self._capture_queue_error(owner, "duplicate_drain", error)
            raise BaseException.with_traceback(observed['error'], observed['traceback'])
        owner['entered'] = True
        workers: tuple = ()
        native: tuple = ()
        try:
            workers = tuple(dict.fromkeys(self._worker_tasks))
            owner['workers'] = tuple(dict(task=task, first_error=None, occurrences=[]) for task in workers)
        except BaseException as error:
            self._capture_queue_error(owner, "worker_snapshot", error)
        try:
            with self._admission_lock:
                native = tuple(self._native_work)
            owner['native'] = tuple(dict(work=work, task=work.task, first_error=None, occurrences=[])
                                    for work in native)
        except BaseException as error:
            self._capture_queue_error(owner, "native_snapshot", error)
        # Preserve the actual partial-submission chain, including acquisition
        # or publication errors that did not return a queue registration ack.
        submission = owner['submission_failures'] = self._submission_failure
        submissions = []
        while submission is not None:
            submissions.append(submission)
            submission = submission['previous']
        for submission in reversed(submissions):
            cause = submission['first_error']
            self._capture_queue_error(owner, "submission_failure", cause['error'], cause=cause)

        def native_failure(record: dict[str, Any], *, outer_error: bool = False,
                           raw: bool = False) -> dict[str, Any] | None:
            work = record.get('work')
            if work is None:
                # A failing consumer/join belongs to this original drain too.
                # Snapshot its existing native records before error collection;
                # their iteration order is not a chronology across owners.
                selected = None
                for original in owner.get('native', ()) if outer_error or raw else ():
                    cause = native_failure(original, raw=True)
                    if selected is None and cause is not None:
                        selected = cause
                if selected is not None and outer_error:
                    if record['first_error'] is None:
                        record['first_error'] = selected
                    if owner.get('first_error') is None:
                        candidate = owner.get('cancellation_candidate')
                        owner['first_error'] = record['first_error'] if candidate is None else candidate
                return selected
            cause = work._observe_failure(outer_error=outer_error or raw)
            record['work_observation'] = dict(work=work, cause=cause,
                                             previous=record.get('work_observation'))
            confirmed = bool(work.failures) or work.failure_pending
            if cause is not None and (confirmed or outer_error):
                if record['first_error'] is None:
                    record['first_error'] = cause
                if owner.get('first_error') is None:
                    candidate = owner.get('cancellation_candidate')
                    owner['first_error'] = cause if candidate is None else candidate
            # Raw capture orders a real outer error/cancellation; it does not
            # turn a subsequently handled no-run refusal into a failed drain.
            return cause if confirmed or outer_error or raw else None

        def promote(record: dict[str, Any], phase: str) -> None:
            cause = record['first_error']
            if cause is not None:
                self._capture_queue_error(owner, phase, cause['error'], cause=cause)

        def cancellation(record: dict[str, Any], observed: dict[str, Any], previous: dict[str, Any] | None) -> None:
            if not owner.get('cancellation_observed'):
                owner['cancellation_observed'] = True
                selected = None
                descriptions = []
                # Consume only the raw snapshots frozen before collection of
                # THIS cancellation. Later Task outcomes cannot backdate E1.
                originals = owner.get('native', ())
                if not any(original is record for original in originals):
                    originals = (*originals, record)
                for original in originals:
                    try:
                        # Frozen before the ORIGINAL cancellation's collection,
                        # never reconstructed from later completed work.
                        cause = original.get('cancellation_work_cause')
                        if selected is None and cause is not None:
                            selected = cause
                    except BaseException as error:
                        record['observation_failed'] = True
                        later = self._capture_queue_error(record, "cancellation_observation", error, describe=False)
                        descriptions.append((record, later))
                if selected is not None:
                    if previous is None and record['first_error'] is observed:
                        record['first_error'] = selected
                    if owner.get('first_error') is not None:
                        self._capture_queue_error(owner, "native_before_cancellation", selected['error'], cause=selected)
                if owner.get('cancellation_candidate') is None:
                    owner['cancellation_candidate'] = record['first_error']
                for target, capture in descriptions:
                    self._capture_queue_error(target, capture['phase'], capture['error'], captured=capture)
            elif owner.get('cancellation_candidate') is None:
                owner['cancellation_candidate'] = observed

        # Observe ended E1 before asking for cancellation. Cancellation return
        # is only a request ack, and worker cancellation never proves native exit.
        for record in owner.get('workers', ()):
            task = record['task']
            try:
                if task.done():
                    record['done_before'] = True
                    error = task.exception()
                    if error is not None:
                        native_failure(record, outer_error=True)
                        record['stored_error'] = self._capture_queue_error(record, "worker_stored_error", error)
                        record['stored_error']['delivery'] = "stored_outcome_observation"
                        promote(record, "worker_stored_error")
                record['cancelling_before'] = task.cancelling()
            except asyncio.CancelledError as error:
                for original in owner.get('native', ()):
                    cause = native_failure(original, raw=True)
                    if not owner.get('cancellation_observed'):
                        original['cancellation_work_cause'] = cause
                observed = self._capture_queue_error(record, "worker_observation", error, describe=False)
                try:
                    cancelled = task.cancelled()
                    record['cancelled_before'] = cancelled
                except BaseException as secondary:
                    self._capture_queue_error(record, "worker_cancel_observation", secondary)
                    promote(record, "worker_cancel_observation")
                else:
                    if record.get('done_before') is True and cancelled:
                        # An outer shutdown may already have cancelled this
                        # consumer. Retain its outcome, never as native-exit
                        # proof. Positive original native joins are still owed.
                        cancellation(record, observed, None)
                    else:
                        promote(record, "worker_observation")
                self._capture_queue_error(record, "worker_observation", error, captured=observed)
            except BaseException as error:
                native_failure(record, outer_error=True)
                self._capture_queue_error(record, "worker_observation", error)
                promote(record, "worker_observation")
        for record in owner.get('native', ()):
            cause = native_failure(record)
            if cause is not None:
                self._capture_queue_error(owner, "native_stored_error", cause['error'], cause=cause)
        for record in owner.get('workers', ()):
            try:
                record['cancel_attempted'] = True
                record['cancel_returned'] = record['task'].cancel()
            except BaseException as error:
                native_failure(record, outer_error=True)
                self._capture_queue_error(record, "worker_cancel", error)
                promote(record, "worker_cancel")

        joined: dict[asyncio.Task, dict[str, Any]] = {}

        async def join(record: dict[str, Any], *, worker: bool) -> None:
            task = record['task']
            if task is None:
                native_failure(record, outer_error=True)
                error = RuntimeError("native_task_submission_unacknowledged")
                self._capture_queue_error(record, "missing_task", error)
                promote(record, "missing_task")
                return
            if task in joined:
                record['original_join'] = joined[task]
                return  # Same actual handle, never another aggregate/operation.
            joined[task] = record
            record['join_attempted'] = True
            while True:
                try:
                    if task.done():
                        break
                except BaseException as error:
                    native_failure(record, outer_error=True)
                    self._capture_queue_error(record, "join_observation", error)
                    promote(record, "join_observation")
                    return
                try:
                    record['wait_outcome'] = await asyncio.wait((task,))
                except asyncio.CancelledError as error:
                    previous = record['first_error']
                    for original in owner.get('native', ()):
                        cause = native_failure(original, raw=True)
                        if not owner.get('cancellation_observed'):
                            original['cancellation_work_cause'] = cause
                    observed_cancel = self._capture_queue_error(record, "drain_cancellation", error,
                                                                 describe=False)
                    cancellation(record, observed_cancel, previous)
                    self._capture_queue_error(record, "drain_cancellation", error, captured=observed_cancel)
                    continue  # One original wait; never cancel the owned Task.
                except BaseException as error:
                    native_failure(record, outer_error=True)
                    self._capture_queue_error(record, "join_observation", error)
                    promote(record, "join_observation")
                    return
            try:
                record['result_attempted'] = True
                record['result'] = task.result()
                record['returned'] = True
            except BaseException as error:
                # Only this drain's positively accepted worker cancellation is
                # expected. A missing/cancelled native Task remains UNKNOWN.
                previous = record['first_error']
                native_cause = native_failure(record, raw=True)
                if isinstance(error, asyncio.CancelledError):
                    for original in owner.get('native', ()):
                        cause = native_failure(original, raw=True)
                        if not owner.get('cancellation_observed'):
                            original['cancellation_work_cause'] = cause
                cause = record.get('stored_error')
                if cause is None:
                    cause = native_cause
                observed = self._capture_queue_error(record, "task_result", error, cause=cause, describe=False)
                record['outcome_error'] = observed
                expected = False
                cancelled = False
                try:
                    cancelled = task.cancelled()
                    expected = (worker and isinstance(error, asyncio.CancelledError)
                                and record.get('cancel_returned') is True
                                and record.get('cancelling_before') == 0 and cancelled)
                except BaseException as secondary:
                    self._capture_queue_error(record, "cancel_ack_observation", secondary)
                if not expected and previous is None and native_cause is not None:
                    record['first_error'] = native_cause
                if expected:
                    observed['delivery'] = "own_worker_cancellation"
                    record['acknowledgement'] = "own_worker_cancellation"
                elif worker and isinstance(error, asyncio.CancelledError) and cancelled:
                    observed['delivery'] = "external_worker_cancellation"
                    record['acknowledgement'] = "cancelled_worker_wrapper"
                    cancellation(record, observed, record['first_error'] if record['first_error'] is not observed else None)
                    self._capture_queue_error(record, "task_result", error, cause=cause, captured=observed)
                else:
                    self._capture_queue_error(record, "task_result", error, cause=cause, captured=observed)
                    record['acknowledgement'] = "UNKNOWN" if isinstance(error, asyncio.CancelledError) else "ended_error"
                    promote(record, "task_result")
            else:
                record['acknowledgement'] = "returned_value"
                # Successful suppression of drain cancellation is recorded,
                # without adding a new permanent native-retirement blocker.
            cause = native_failure(record)
            if cause is not None:
                self._capture_queue_error(record, "native_work_failure", cause['error'], cause=cause)
                promote(record, "native_work_failure")
            if record.get('observation_failed') or record.get('capture_failed'):
                promote(record, "join_delivery_failure")

        for record in owner.get('workers', ()):
            try:
                await join(record, worker=True)
            except BaseException as error:
                native_failure(record, outer_error=True)
                self._capture_queue_error(owner, "worker_join_delivery", error)
        # A failed cancellation/join/diagnostic above cannot skip these actual
        # original native-work joins. No gather(None), replacement or retirement.
        for record in owner.get('native', ()):
            try:
                await join(record, worker=False)
            except BaseException as error:
                native_failure(record, outer_error=True)
                self._capture_queue_error(owner, "native_join_delivery", error)
        try:
            self.assert_native_idle()
            owner['idle_acknowledged'] = True
        except BaseException as error:
            for record in owner.get('native', ()):
                native_failure(record, outer_error=True)
            self._capture_queue_error(owner, "native_idle", error)
        first = owner.get('first_error')
        if first is not None:
            owner['body_error'] = first
            try:
                primary = first['error']
                values = BaseException.__dict__['__dict__'].__get__(primary)
                BaseException.__setattr__(primary, '_queue_drain_failure',
                    dict(owner=owner, previous=values.get('_queue_drain_failure')))
            except BaseException as error:
                self._capture_queue_error(owner, "drain_attachment", error)
            raise BaseException.with_traceback(first['error'], first['traceback'])
        owner['completed'] = True
        # A cancelled consumer is not a native acknowledgement. It can be
        # suppressed only after ALL original joins and the positive idle guard;
        # any later actual failure above instead retains the earlier candidate.
        owner['suppressed_worker_cancellation'] = owner.get('cancellation_candidate')

    async def stop(self, *, timeout: float = 5.0) -> None:
        owner = self._drain_owner
        original = owner is None
        if original:
            owner = self._drain_owner = dict(first_error=None, occurrences=[], submission_attempted=False)
        # Waiter delivery has its OWN first cause and finite deadline. It never
        # acknowledges or restarts the underlying operation by being cancelled.
        waiter: dict[str, Any] = dict(first_error=None, occurrences=[], previous=owner.get('waiter'))
        owner['waiter'] = waiter
        task = owner.get('task')

        def failed(phase: str, error: BaseException, *, cause: dict[str, Any] | None = None,
                   operation: bool = False) -> None:
            target = owner if operation else waiter
            previous = target['first_error']
            stored = previous if operation else owner.get('first_error')
            if previous is None:
                target['first_observation'] = dict(phase=phase, error=error,
                    traceback=BaseException.__traceback__.__get__(error), owner_cause=stored,
                    cause=None)
            if stored is None and self._submission_failure is not None:
                stored = self._submission_failure['first_failure']['first_error']
            # Freeze each actual failure/cancellation, including repeated ones,
            # before collection, diagnostics or later Task/outcome observation.
            # This registry order is not chronology between independent owners.
            with self._admission_lock:
                works = tuple(self._native_work)
            for work in works:
                native_cause = work._observe_failure(outer_error=True)
                target['work_observation'] = dict(work=work, cause=native_cause,
                    previous=target.get('work_observation'))
                if stored is None and native_cause is not None:
                    stored = native_cause
            if previous is None:
                target['first_observation']['cause'] = stored
                if stored is not None:
                    target['first_error'] = stored
            frozen = stored
            observed = self._capture_queue_error(target, phase, error, cause=cause, describe=False)
            if previous is not None:
                self._capture_queue_error(target, phase, error, cause=cause, captured=observed)
                return
            newly_stored = None
            try:
                if stored is None and task is not None and task.done() and not task.cancelled():
                    ended = task.exception()
                    if ended is not None:
                        stored = self._capture_queue_error(owner, "stored_drain_error", ended,
                                                           cause=owner.get('body_error'), describe=False)
                        newly_stored = stored
                        if (target['first_observation']['owner_cause'] is None
                                and owner.get('body_error') is not None):
                            stored = None  # Later original drain delivery, retained below.
                        if owner.get('body_error') is None:
                            stored['delivery'] = "stored_outcome_observation"
                if stored is None:
                    for worker in tuple(self._worker_tasks):
                        if worker.done() and not worker.cancelled():
                            ended = worker.exception()
                            if ended is not None:
                                stored = self._capture_queue_error(owner, "stored_worker_error", ended, describe=False)
                                newly_stored = stored
                                stored['delivery'] = "stored_outcome_observation"
                                break
            except BaseException as secondary:
                self._capture_queue_error(target, "stored_error_observation", secondary)
            if newly_stored is not None:
                self._capture_queue_error(owner, newly_stored['phase'], newly_stored['error'],
                                          cause=newly_stored.get('cause'), captured=newly_stored)
            if frozen is not None:
                target['first_error'] = frozen
                self._capture_queue_error(target, "earlier_drain_error", frozen['error'], cause=frozen)
            self._capture_queue_error(target, phase, error, cause=cause, captured=observed)

        try:
            self.close_admission()
        except BaseException as error:
            failed("close_admission", error, operation=True)
        else:
            if original:
                try:
                    owner['coroutine'] = self._drain()
                    owner['submission_attempted'] = True  # Sticky BEFORE create_task.
                    task = asyncio.create_task(owner['coroutine'])
                    owner['task'] = self._stop_task = task
                    owner['submission_returned'] = True
                    owner['callback_attempted'] = True
                    task.add_done_callback(self._observe_drain)
                    owner['callback_returned'] = True
                except BaseException as error:
                    failed("drain_submission_or_callback", error, operation=True)
        if task is None:
            if owner.get('first_error') is None:
                failed("missing_drain_task", RuntimeError("native_task_drain_submission_unacknowledged"),
                       operation=True)
        else:
            try:
                loop = asyncio.get_running_loop()
                deadline = waiter['deadline'] = loop.time() + max(0.0, timeout)
                while not task.done():
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise TimeoutError("native_task_drain_incomplete")
                    try:
                        outcome = await asyncio.wait((task,), timeout=remaining)
                        waiter['wait_outcome'] = outcome
                        if not outcome[0]:
                            raise TimeoutError("native_task_drain_incomplete")
                    except asyncio.CancelledError as error:
                        failed("stop_cancellation", error)
                        # The SAME finite deadline survives repeated cancels.
                waiter['result_attempted'] = True
                try:
                    waiter['result'] = task.result()
                    waiter['result_returned'] = True
                except BaseException as error:
                    failed("stop_result", error, cause=owner.get('body_error'))
                if not owner.get('completed') or not owner.get('submission_returned'):
                    failed("drain_acknowledgement", RuntimeError("native_task_drain_incomplete"))
            except BaseException as error:
                failed("stop_wait", error)
        cause = owner.get('first_error')
        if cause is not None:
            self._capture_queue_error(waiter, "drain_delivery", cause['error'], cause=cause)
        first = waiter['first_error']
        if first is not None:
            try:
                primary = first['error']
                values = BaseException.__dict__['__dict__'].__get__(primary)
                BaseException.__setattr__(primary, '_queue_stop_failure',
                    dict(waiter=waiter, owner=owner, previous=values.get('_queue_stop_failure')))
            except BaseException as error:
                self._capture_queue_error(waiter, "stop_attachment", error)
            raise BaseException.with_traceback(first['error'], first['traceback'])
        waiter['delivered'] = True

    def _observe_drain(self, task: asyncio.Task) -> None:
        owner = self._drain_owner
        if owner is None:
            return
        try:
            if task is not owner.get('task'):
                raise RuntimeError("native_task_drain_observer_owner_mismatch")
            owner['observer_attempted'] = True
            error = task.exception()  # Cancelled Task raises its actual cancellation.
        except BaseException as error:
            observed = self._capture_queue_error(owner, "drain_observation", error, cause=owner.get('body_error'))
        else:
            if error is None:
                owner['observer_returned'] = True
                return
            observed = self._capture_queue_error(owner, "drain_outcome", error, cause=owner.get('body_error'))
            if owner.get('body_error') is None:
                observed['delivery'] = "stored_outcome_observation"
        try:
            logger.error("native task drain incomplete: %r", observed['error'])
            owner['observer_returned'] = True
        except BaseException as error:
            # Diagnostic failure stays with the ORIGINAL drain, without DB or
            # native work, recursive diagnostics or replacement of its outcome.
            self._capture_queue_error(owner, "drain_diagnostic", error)

    async def drain_sync(self, dispatcher: _Dispatcher) -> None:
        # Retained alternative entrypoint: use exactly the same owned native
        # submission/retirement as the worker pool, including cancellation.
        self._require_ready()
        while not self._stopping and not self._queue.empty():
            await self._consume(dispatcher, self._queue.get_nowait())
