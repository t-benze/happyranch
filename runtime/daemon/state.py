"""Process-wide state holder for the daemon."""
from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import Future
from contextlib import contextmanager
from contextvars import copy_context
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from runtime.daemon.team_move_service import TeamMoveService
    from runtime.daemon.cleanup_diagnostics import _CleanupDiagnostics
    from runtime.daemon.thread_delivery_release_service import _ThreadDeliveryReleaseService
    from runtime.daemon.startup_recovery import StartupRecovery
    from runtime.daemon.startup_reads import StartupReads

from runtime.infrastructure.startup_observation import BootObservation, observed
from runtime.config import Settings
from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore
from runtime.daemon.host_session_store import HostSessionStore
from runtime.daemon.metrics import MetricsRegistry
from runtime.daemon.metrics_store import MetricsStore
from runtime.daemon.org_state import OrgState, _OrgBusyError
from runtime.daemon.queue import TaskQueue
from runtime.daemon.registration_token import RegistrationTokenStore
from runtime.orchestrator.org_validation import OrgConsistencyError
from runtime.runtime import RuntimeDir
from runtime.workflows.profile_coordinator import ProfileCoordinator

logger = logging.getLogger(__name__)


class _AttachmentCapture:
    """Local custody for one original attachment boundary, never settlement."""

    def __init__(self, owner: dict) -> None:
        self.owner = owner
        self.occurrences: list[dict] = []
        self.load_started = False
        self.load_returned = False
        self.load_result: OrgState | None = None
        self.load_outcome: dict | None = None
        self.load_frame = None
        self.preconstruction = False

    @property
    def first(self) -> dict | None:
        # Native callback and loop waiter share this original record. The
        # first list append selects FIRST, without a read/check/write race or
        # another lock/wait. Later appends never overwrite that selection.
        return self.occurrences[0] if self.occurrences else None

    def capture(self, phase: str, error: BaseException, *,
                observation: bool = False, history: bool = True) -> dict:
        record = dict(phase=phase, error=error,
            arguments=BaseException.args.__get__(error),
            traceback=BaseException.__traceback__.__get__(error),
            context=BaseException.__context__.__get__(error),
            cause=BaseException.__cause__.__get__(error),
            suppress_context=BaseException.__suppress_context__.__get__(error),
            observation=observation, delivery=None, previous_occurrence=None,
            history=None, child=None)
        self.occurrences.append(record)
        for previous in self.occurrences:
            if previous is record:
                break
            if previous["error"] is error:
                record["previous_occurrence"] = previous
        if history:
            try:
                record["history"] = BaseException.__dict__["__dict__"].__get__(error).copy()
            except BaseException as later:
                self.capture("attachment prior retention", later, history=False)
        return record

    def attach(self, error: BaseException, key: str, value: Any) -> None:
        try:
            BaseException.__setattr__(error, key, value)
        except BaseException as later:
            self.capture("attachment " + key, later)

    def note(self, error: BaseException, message: str) -> None:
        try:
            BaseException.add_note(error, message)
        except BaseException as later:
            self.capture("attachment note", later)

    def load_failure(self, record: dict) -> None:
        # Only an actual current child frame, never reused exception attrs,
        # supplies this invocation's unpublished close outcome.
        if self.load_returned:
            return
        try:
            trace = record["traceback"]
            while trace is not None:
                frame = trace.tb_frame
                if frame.f_code is OrgState.load.__func__.__code__:
                    values = frame.f_locals
                    if values.get("attachment_owner") is self.owner:
                        self.load_frame = trace
                        outcome = values.get("load_outcome")
                        if (type(outcome) is dict
                                and outcome.get("attachment_owner") is self.owner
                                and outcome.get("db") is values.get("db")):
                            self.load_outcome = outcome
                            record["child"] = outcome
                        elif "load_outcome" not in values and "db" not in values:
                            # In the original load, both locals are established
                            # before entering the Database construction arm.
                            self.preconstruction = True
                        return
                trace = trace.tb_next
        except BaseException as later:
            self.capture("attachment load evidence", later)

    def load_disposition(self) -> str:
        if self.load_returned:
            return "returned"
        if not self.load_started:
            return "no_handle"
        outcome = self.load_outcome
        if outcome is not None:
            occurrences = outcome.get("occurrences")
            if (outcome.get("db") is not None
                    and outcome.get("close_attempted") is True
                    and outcome.get("close_returned") is True
                    and "close_error" in outcome and outcome["close_error"] is None
                    and type(occurrences) is list and len(occurrences) == 1
                    and outcome.get("first") is occurrences[0]):
                return "closed"
        if self.preconstruction:
            return "no_handle"
        return "unknown"

    def load_first(self) -> dict | None:
        # The fresh SAME owner is supplied to its one original load. This
        # prefix can precede native return while that load is still closing.
        if self.load_started and not self.load_returned:
            outcome = self.load_outcome
            if outcome is None:
                outcome = self.owner.get("load_outcome")
            if (type(outcome) is dict
                    and outcome.get("attachment_owner") is self.owner):
                first = outcome.get("first")
                if type(first) is dict and all(key in first for key in (
                        "error", "arguments", "traceback", "context", "cause", "suppress_context")):
                    return first
        return None

    def cancellation(self, cause: BaseException, earlier: dict | None) -> BaseException:
        if self.first is None and earlier is not None:
            # An actual child snapshot already exists BEFORE this cancellation
            # observation. Link its native values, not the error's live attrs.
            record = dict(earlier)
            record.update(phase="attachment child before cancellation",
                          observation=True, child=earlier, delivery=None)
            self.occurrences.append(record)
        self.capture("attachment waiter cancellation", cause, observation=True)
        return self.first["error"]

    def raise_first(self) -> Any:
        record = self.first
        assert record is not None
        self.raise_record(record)

    def raise_record(self, record: dict) -> Any:
        error = record["error"]
        try:
            BaseException.args.__set__(error, record["arguments"])
            BaseException.__context__.__set__(error, record["context"])
            BaseException.__cause__.__set__(error, record["cause"])
            BaseException.__suppress_context__.__set__(error, record["suppress_context"])
        except BaseException as later:
            self.capture("attachment native restoration", later)
        record["delivery"] = (type(error), record["traceback"])
        raise BaseException.with_traceback(error, record["traceback"])


@dataclass
class DaemonState:
    runtime: RuntimeDir | None
    settings: Settings
    orgs: dict[str, OrgState] = field(default_factory=dict)
    # Orgs whose folder is on disk but failed to attach (typically an
    # OrgConsistencyError from validate_team_membership, or a
    # ValueError / AgentParseError from agent file validation).
    # Surfaced via GET /orgs so the founder isn't left guessing why an
    # org went missing after a restart.
    broken_orgs: dict[str, str] = field(default_factory=dict)
    queue: TaskQueue = field(default_factory=TaskQueue)
    registration_token_store: RegistrationTokenStore = field(
        default_factory=RegistrationTokenStore
    )
    direct_connect_authority_store: DirectConnectAuthorityStore | None = None
    orgs_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    metrics_registry: MetricsRegistry = field(default_factory=MetricsRegistry)
    metrics_store: MetricsStore | None = None
    # Throttle for periodic snapshot writes — monotonic timestamp of last write.
    _last_metrics_snapshot_at: float = 0.0
    # Daemon-wide HostSessionSupervisor (THR-207 real-caller wiring): task,
    # schedule, thread, dream, and wake producers run through it; the app
    # lifespan drain calls its ``shutdown()`` before producer workers are
    # cancelled. Constructed in ``from_runtime``; ``None`` for idle/test
    # states (the producers then keep the legacy uncontained path).
    host_supervisor: "HostSessionSupervisor | None" = None
    # Bounded in-memory receipt + health store (THR-207 observability slice):
    # the supervisor's receipt publisher lands here, and the existing
    # /metrics + /health surfaces compose the bounded ``host_sessions`` block
    # from it plus the supervisor's live admission/backpressure view.
    # Always constructed (idle and runtime states) so the payload shape is
    # deterministic.
    host_session_store: HostSessionStore = field(default_factory=HostSessionStore)
    # U2B same-host coordinator for machine-global executor profiles.  It is
    # constructed only for runtime-backed states after every initial OrgState
    # has loaded, then reconciled before the DaemonState is returned.
    profile_coordinator: ProfileCoordinator | None = None
    team_move_service: TeamMoveService = field(init=False, repr=False)
    _runner_shutdown: bool = field(init=False, default=False, repr=False)
    _shutdown_complete: bool = field(init=False, default=False, repr=False)

    _cleanup_diagnostics: _CleanupDiagnostics | None = field(init=False, default=None, repr=False)

    _thread_release_service: _ThreadDeliveryReleaseService | None = field(init=False, default=None, repr=False)
    _startup_recovery: StartupRecovery | None = field(init=False, default=None, repr=False)
    _startup_reads: StartupReads | None = field(init=False, default=None, repr=False)
    _startup_observation: BootObservation | None = field(init=False, default=None, repr=False)

    _maintenance_workers: set[asyncio.Future | Future] = field(init=False, default_factory=set, repr=False)
    _maintenance_workers_lock: threading.RLock = field(init=False, default_factory=threading.RLock, repr=False)

    def _retire_maintenance_worker(self, worker) -> None:
        # A marked original failure retains its actual Process and blocks
        # destruction even after its Python wrapper has ended.
        tail = getattr(worker, '_job_termination_owner', None)
        if tail is not None:
            try:
                if not tail.retirement_ready():
                    return
                with self._maintenance_workers_lock:
                    if worker not in self._maintenance_workers and not tail.retired:
                        raise RuntimeError('job_termination_retirement_owner_missing')
                    self._maintenance_workers.discard(worker)
                    tail.retired = True
                return
            except BaseException as error:
                tail.unknown = True
                tail.errors._retain('retirement', error)
                # Retain the original worker even if discard/lock exit partly
                # succeeded. Absence or the retired flag cannot settle UNKNOWN.
                try:
                    with self._maintenance_workers_lock:
                        self._maintenance_workers.add(worker)
                except BaseException as secondary:
                    tail.errors._retain('retirement_retention', secondary)
                tail.errors._attach('_job_tail_errors')
                tail.errors.raise_first()
        with self._maintenance_workers_lock:
            cleanup = getattr(worker, '_worktree_cleanup_owner', None)
            if cleanup is not None:
                if not cleanup.retirement_ready:
                    return
                if worker not in self._maintenance_workers and not cleanup.retired:
                    raise RuntimeError('worktree_cleanup_retirement_owner_missing')
            if worker.done() and not worker.cancelled():
                failure = worker.exception()
                if failure is None or not getattr(failure, "maintenance_native_unsettled", False):
                    self._maintenance_workers.discard(worker)

    async def _await_maintenance_worker(self, worker):
        """Keep an original submitted maintenance worker visible through cancellation."""
        from runtime.daemon.startup_recovery import _await_startup_owned
        with self._maintenance_workers_lock:
            self._maintenance_workers.add(worker)
        try:
            return await _await_startup_owned(worker, self._startup_recovery._retain_secondary)
        finally:
            self._retire_maintenance_worker(worker)

    async def _drain_maintenance_workers(self) -> None:
        """Join original operations with the loop/DB alive, after producers stop."""
        from runtime.daemon.startup_recovery import _await_startup_owned
        from runtime.orchestrator.run_step import _JobTailErrors
        errors = _JobTailErrors()

        def capture(kind: str, error: BaseException, origin: tuple | None = None) -> None:
            first = errors.primary is None
            errors._retain(kind, error)
            if first and origin is not None:
                # An owner/helper observation precedes this later delivery.
                errors.traceback = origin[1]

        def retain_secondary(error: BaseException, tail: Any, *, retirement: bool = False) -> None:
            try:
                if retirement and tail is not None:
                    tail.errors.remember('retirement_delivery', error)
                elif self._startup_recovery is not None:
                    self._startup_recovery._retain_secondary(error)
                elif tail is not None:
                    tail.errors.remember('maintenance_drain', error)
            except BaseException as secondary:
                capture('retirement_retention' if retirement else 'drain_retention', secondary)

        with self._maintenance_workers_lock:
            workers = tuple(self._maintenance_workers)
        for worker in workers:
            tail = None
            deliveries = []
            wait_first = None
            try:
                tail = getattr(worker, '_job_termination_owner', None)
                if tail is not None:
                    # Active/missing native/loop acknowledgements refuse rather
                    # than waiting for an absent worker or a foreign-loop Task.
                    failure = tail.maintenance_error()
                    if failure is not None:
                        # Consume its original snapshot without a synthetic
                        # raise/catch that would replace the native traceback.
                        origin = ((failure, tail.errors.traceback)
                                  if failure is tail.errors.primary else None)
                        first = errors.primary is None
                        capture('maintenance_outcome', failure, origin)
                        if not first:
                            retain_secondary(failure, tail)
                    continue
                cleanup = getattr(worker, '_worktree_cleanup_owner', None)
                if cleanup is not None:
                    # Missing submission/native/loop/retirement acknowledgement
                    # is retained custody, not an endlessly awaitable operation.
                    failure = cleanup.maintenance_error()
                    if failure is not None:
                        first = errors.primary is None
                        capture('maintenance_outcome', failure)
                        if not first:
                            retain_secondary(failure, tail)
                        continue
                if worker is asyncio.current_task():
                    raise _OrgBusyError("maintenance_self_join_refused")
                completion = asyncio.wrap_future(worker) if isinstance(worker, Future) else worker
                original_retain = self._startup_recovery._retain_secondary

                def retain(error: BaseException) -> None:
                    nonlocal wait_first
                    # Keep each helper delivery before its fallible diagnostic
                    # sink or final rethrow. A cancellation delivery can follow
                    # an already-ended native error on this SAME completion.
                    traceback = BaseException.__traceback__.__get__(error)
                    deliveries.append(('wait_observation', error, traceback))
                    observation_failure = None
                    if wait_first is None:
                        wait_first = (error, traceback)
                        if isinstance(error, asyncio.CancelledError):
                            try:
                                earlier = (None if not completion.done() or completion.cancelled()
                                           else completion.exception())
                                if earlier is not None:
                                    wait_first = (earlier, BaseException.__traceback__.__get__(earlier))
                            except BaseException as secondary:
                                observation_failure = (secondary, BaseException.__traceback__.__get__(secondary))
                        if wait_first[0] is not error:
                            capture('wait_prior_native', wait_first[0], wait_first)
                    capture('wait_observation', error, (error, traceback))
                    if observation_failure is not None:
                        secondary, traceback = observation_failure
                        deliveries.append(('wait_observation_failed', secondary, traceback))
                        capture('wait_observation_failed', secondary, observation_failure)
                    try:
                        original_retain(error)
                    except BaseException as secondary:
                        deliveries.append(('wait_retention', secondary,
                                           BaseException.__traceback__.__get__(secondary)))
                        capture('wait_retention', secondary)

                await _await_startup_owned(completion, retain)
            except BaseException as exc:
                first = errors.primary is None
                origin = next(((error, traceback) for _, error, traceback in deliveries
                               if error is exc), None)
                if tail is not None and exc is tail.errors.primary:
                    origin = (exc, tail.errors.traceback)
                capture('maintenance_drain', exc, origin)
                if not first:
                    retain_secondary(exc, tail)
            finally:
                # Callback observations were captured before their diagnostics;
                # final helper delivery is a separate observation of that error,
                # not an invented second native incident or a reason to retry.
                try:
                    self._retire_maintenance_worker(worker)
                except BaseException as exc:
                    first = errors.primary is None
                    origin = ((exc, tail.errors.traceback)
                              if tail is not None and exc is tail.errors.primary else None)
                    capture('retirement_delivery', exc, origin)
                    if not first:
                        retain_secondary(exc, tail, retirement=True)
        errors._attach('_job_tail_errors')
        errors.raise_first()

    def _bind_startup_owner(self, app) -> None:
        """One boot owner; construction binds references without DB work."""
        from runtime.daemon.startup_recovery import StartupRecovery
        from runtime.daemon.startup_reads import StartupReads
        if self._startup_recovery is not None or self._startup_reads is not None:
            raise RuntimeError("startup_owner_already_bound")
        recovery = StartupRecovery(self, app)
        self._startup_recovery = recovery
        self._startup_reads = StartupReads(self, recovery)
        from runtime.daemon.direct_connect_projection_sweep import bind_startup_references
        bind_startup_references(self)
        from runtime.daemon.jobs_runner import bind_jobs_startup_references
        bind_jobs_startup_references(self)
        from runtime.daemon.startup_recovery import _bind_task_admission
        _bind_task_admission(self.queue, self)
        if self.host_supervisor is not None:
            _bind_task_admission(self.host_supervisor, self)
        for org in self.orgs.values():
            self._attach_startup_owner(org)

    def _require_startup_ready(self) -> None:
        from runtime.daemon.startup_recovery import require_ready
        require_ready(self)

    def _notify_attached_returned(self, org: OrgState, *, queue=None) -> None:
        """Explicit original dynamic return obligation; no new boot sweep.

        Preserve the original public binder's complete selection and scoped
        error/local-hold policy, now at the actual attachment caller.
        """
        self._require_startup_ready()
        if self.orgs.get(org.slug) is not org or org._startup_recovery is not self._startup_recovery:
            raise RuntimeError("dynamic_startup_attachment_changed")
        coding = getattr(org.orchestrator, "_workflow_coding_dispatch", None)
        if coding is not None:
            import sqlite3
            from runtime.workflows.recovery import rediscover_returned
            try:
                rediscover_returned(coding, queue=queue)
            except (ValueError, TypeError, sqlite3.Error):
                logger.exception("coding caller notification rediscovery unavailable")

    def _attach_startup_owner(self, org) -> None:
        from runtime.daemon.startup_recovery import _bind_task_admission, StartupUnavailable
        originals = org._startup_admission_refs
        if originals is None:
            org._startup_admission_refs = (org.orchestrator, org.db, org.sessions, org.thread_queue,
                                         org.dream_queue, org.wake_queue, org.schedule_queue)
        elif (not isinstance(originals, tuple) or len(originals) != 7
              or org.orchestrator is not originals[0] or org.db is not originals[1]
              or org.sessions is not originals[2] or org.thread_queue is not originals[3]
              or org.dream_queue is not originals[4] or org.wake_queue is not originals[5]
              or org.schedule_queue is not originals[6]):
            raise StartupUnavailable("startup_attachment_changed")
        for owner in (org, org.orchestrator, org.sessions, org.thread_queue,
                      org.dream_queue, org.wake_queue, org.schedule_queue):
            _bind_task_admission(owner, self, org)
        from runtime.daemon.startup_recovery import _document_references
        for owner in _document_references(org)[:5]:
            _bind_task_admission(owner, self, org)
        _bind_task_admission(_document_references(org)[6], self, org)
        from runtime.daemon.startup_recovery import _coding_references
        for owner in _coding_references(org)[:4]:
            _bind_task_admission(owner, self, org)
        org._startup_reads = self._startup_reads
        from runtime.daemon.jobs_runner import bind_jobs_startup_references
        bind_jobs_startup_references(self, org)

    async def _drain_startup(self, org=None) -> None:
        # Recovery may require request-loop queue acknowledgements to end. Join
        # outside org/DB/publisher locks, before the first connection can close.
        primary = None
        traceback = None
        try:
            if self._startup_recovery is not None:
                if org is None:
                    await self._startup_recovery.close()
                else:
                    self._startup_recovery.assert_native_idle()
        except BaseException as exc:
            primary, traceback = exc, exc.__traceback__
        try:
            # Independent read ownership remains owed after an ended recovery
            # error, and while unresolved recovery keeps its own closure fence.
            if self._startup_reads is not None:
                await self._startup_reads.drain(org)
        except BaseException as exc:
            if self._startup_recovery is not None:
                self._startup_recovery.fail("startup_read_drain_incomplete", exc)
            if primary is None:
                primary, traceback = exc, exc.__traceback__
            else:
                primary.add_note("startup read drain also failed: " + type(exc).__name__)
        if primary is not None:
            raise primary.with_traceback(traceback)

    def _assert_startup_idle(self, org=None) -> None:
        # A native tail's loop callback may precede actual Thread exit. Renew
        # that SAME completed owner once, without joining under this mutex.
        with self._maintenance_workers_lock:
            tails = tuple(worker for worker in self._maintenance_workers
                          if getattr(worker, '_job_termination_owner', None) is not None)
        for worker in tails:
            self._retire_maintenance_worker(worker)
        with self._maintenance_workers_lock:
            if self._maintenance_workers:
                raise _OrgBusyError("maintenance_native_in_flight")
        if self._startup_recovery is not None:
            self._startup_recovery.assert_native_idle()
        if self._startup_reads is not None:
            self._startup_reads.assert_idle(org)

    # Only attachment membership and in-memory reader/worker bookkeeping use
    # this mutex. Never acquire DB/profile locks, await or close under it.
    _org_map_lock: threading.RLock = field(init=False, default_factory=threading.RLock, repr=False)
    _attachment_swap_pending: bool = field(init=False, default=False, repr=False)
    _org_readers: dict[int, int] = field(init=False, default_factory=dict, repr=False)
    _retiring_orgs: set[int] = field(init=False, default_factory=set, repr=False)
    _attachment_tasks: set[asyncio.Task] = field(init=False, default_factory=set, repr=False)
    _attachment_workers: dict[object, dict] = field(init=False, default_factory=dict, repr=False)
    _attachment_owners: dict[object, dict] = field(init=False, default_factory=dict, repr=False)

    def orgs_snapshot(self) -> dict[str, OrgState]:
        """Membership only; existing runner admission still owns execution."""
        with self._org_map_lock:
            return dict(self.orgs)

    @contextmanager
    def capture_orgs(self, slugs: tuple[str, ...] | None = None, *, original: OrgState | None = None) -> Iterator[dict[str, OrgState]]:
        """Retain the actual DB attachments for a native read interval."""
        with self._org_map_lock:
            orgs = dict(self.orgs) if slugs is None else {slug: self.orgs[slug] for slug in slugs if slug in self.orgs}
            if original is not None and orgs.get(original.slug) is not original:
                raise _OrgBusyError("attachment_changed_during_read")
            if self._attachment_swap_pending or any(id(org) in self._retiring_orgs for org in orgs.values()):
                raise _OrgBusyError("attachment_retiring")
            for org in orgs.values():
                self._org_readers[id(org)] = self._org_readers.get(id(org), 0) + 1
        try:
            yield orgs
        finally:
            with self._org_map_lock:
                for org in orgs.values():
                    remaining = self._org_readers[id(org)] - 1
                    if remaining:
                        self._org_readers[id(org)] = remaining
                    else:
                        del self._org_readers[id(org)]

    def _assert_attachment_readers_idle(self, orgs: tuple[OrgState, ...]) -> None:
        with self._org_map_lock:
            if any(self._org_readers.get(id(org), 0) for org in orgs):
                raise _OrgBusyError("attachment_readers_in_flight")

    def _begin_attachment_close(self, orgs: tuple[OrgState, ...]) -> None:
        # Admission and the idle observation are atomic, but close is outside
        # this mutex. A failed close remains fenced, with its actual owner.
        with self._org_map_lock:
            self._assert_attachment_readers_idle(orgs)
            self._retiring_orgs.update(id(org) for org in orgs)
        for org in orgs:
            org._runner_admission_closed = True

    def _assert_attachment_loads_idle(self) -> None:
        with self._org_map_lock:
            if self._attachment_tasks or self._attachment_workers or self._attachment_owners:
                raise _OrgBusyError("attachment_native_in_flight_or_unsafe")

    def _begin_attachment_swap(self) -> None:
        # Called at the final loop preflight, before registry/activation effects.
        # Prevent a new native reader from opening the close race afterward.
        with self._org_map_lock:
            self._assert_attachment_readers_idle(tuple(self.orgs.values()))
            self._attachment_swap_pending = True

    def _finish_attachment_swap(self) -> None:
        with self._org_map_lock:
            self._attachment_swap_pending = False

    def _assert_attachment_unpublished_idle(self, slug: str) -> None:
        """Fresh-org rollback cannot delete a published or uncertain owner."""
        with self._org_map_lock:
            if slug in self.orgs or any(o["slug"] == slug for o in self._attachment_owners.values()):
                raise _OrgBusyError("attachment_published_or_unsafe")

    async def _drain_attachments(self) -> None:
        # Each original child keeps its own native tickets. A failed child
        # does not discharge another child's join or the final owner barrier.
        from runtime.daemon.startup_recovery import _await_startup_owned
        primary = None
        traceback = None
        def retain(error: BaseException) -> None:
            if self._startup_recovery is not None:
                self._startup_recovery._retain_secondary(error)
        for task in tuple(self._attachment_tasks):
            try:
                outcome = task._attachment_outcome
                await _await_startup_owned(task, retain,
                    on_cancel=lambda cause: outcome.get("primary", cause))
            except BaseException as exc:
                if primary is None:
                    primary, traceback = exc, exc.__traceback__
                else:
                    retain(exc)
        try:
            self._assert_attachment_loads_idle()
        except BaseException as exc:
            if primary is None:
                primary, traceback = exc, exc.__traceback__
            else:
                retain(exc)
        if primary is not None:
            raise primary.with_traceback(traceback)

    def _revoke_thread_releases(self, org=None) -> None:
        if self._thread_release_service is not None:
            self._thread_release_service.revoke(org)
        else:
            for current in self.orgs_snapshot().values() if org is None else (org,):
                current._revoke_thread_releases()

    async def _drain_thread_releases(self, org=None) -> None:
        if self._thread_release_service is not None:
            await self._thread_release_service.drain(org)
        for current in self.orgs_snapshot().values() if org is None else (org,):
            current._assert_thread_releases_idle()

    def _capture_attachments(self) -> tuple:
        with self._org_map_lock:
            return self._capture_attachments_locked()

    def _capture_attachments_locked(self) -> tuple:
        # Private two-phase detach identity, captured under orgs_lock. Paths
        # accompany object identity so in-place root/DB changes also refuse.
        return (self.orgs, self.runtime, self.runtime.root if self.runtime else None,
                self.host_supervisor, self._thread_release_service, tuple((slug, org, org.root, org.db, org.db.db_path, org.sessions, org._thread_release_attachment)
                                            for slug, org in self.orgs_snapshot().items()))

    def _assert_attachments_unchanged(self, captured: tuple) -> None:
        with self._org_map_lock:
            self._assert_attachments_unchanged_locked(captured)

    def _assert_attachments_unchanged_locked(self, captured: tuple) -> None:
        org_map, runtime, root, supervisor, release_service, orgs = captured
        if (self.orgs is not org_map or self.runtime is not runtime
                or (self.runtime.root if self.runtime else None) != root
                or self.host_supervisor is not supervisor or self._thread_release_service is not release_service
                or len(self.orgs) != len(orgs)
                or any(self.orgs.get(slug) is not org or org.slug != slug or org.root != org_root
                       or org.db is not db or db.db_path != db_path or org.sessions is not tracker
                       or org._thread_release_attachment is not release_attachment
                       for slug, org, org_root, db, db_path, tracker, release_attachment in orgs)):
            raise _OrgBusyError("attachment_changed_during_diagnostic_drain")

    def _revoke_cleanup_diagnostics(self) -> None:
        if self._cleanup_diagnostics is not None:
            self._cleanup_diagnostics.revoke()
        for org in self.orgs_snapshot().values():
            org._revoke_cleanup_diagnostics()

    async def _drain_cleanup_diagnostics(self, org: OrgState | None = None) -> None:
        if self._cleanup_diagnostics is not None:
            await self._cleanup_diagnostics.drain(org)
        for current in self.orgs_snapshot().values() if org is None else (org,):
            current._assert_cleanup_diagnostics_idle()

    def _stop_runner_admission(self) -> None:
        # No await: close fresh admission even if another revoke itself fails.
        with self._org_map_lock:
            self._runner_shutdown = True
            orgs = tuple(self.orgs.values())
        for org in orgs:
            org._runner_admission_closed = True
        primary = None
        traceback = None
        revokes = [self.queue.close_admission]
        if self._startup_recovery is not None:
            revokes.append(self._startup_recovery.stop)
        if self._startup_reads is not None:
            revokes.append(self._startup_reads.revoke)
        revokes.append(self._revoke_thread_releases)
        for revoke in revokes:
            try:
                revoke()
            except BaseException as exc:
                if primary is None:
                    primary, traceback = exc, exc.__traceback__
                elif self._startup_recovery is not None:
                    self._startup_recovery._retain_secondary(exc)
        if primary is not None:
            raise primary.with_traceback(traceback)

    @classmethod
    def idle(cls, settings: Settings) -> "DaemonState":
        state = cls(runtime=None, settings=settings)
        state.metrics_store = MetricsStore(None)  # in-memory for idle state
        state.direct_connect_authority_store = DirectConnectAuthorityStore(None)
        state.direct_connect_authority_store._projection_startup_owner = state
        return state

    def __post_init__(self) -> None:
        """Construct metrics_store for runtime-backed state.

        Called by from_runtime after the dataclass constructor.
        Idle states still own the inert move service; metrics_store is set by idle().
        """
        from runtime.daemon.startup_recovery import _bind_task_admission
        _bind_task_admission(self.queue, self)
        if self.host_supervisor is not None:
            _bind_task_admission(self.host_supervisor, self)
        for org in self.orgs.values():
            self._attach_startup_owner(org)
        from runtime.daemon.team_move_service import TeamMoveService
        self.team_move_service = TeamMoveService(self)
        if self.runtime is not None and self.metrics_store is None:
            self.metrics_store = MetricsStore(
                str(self.runtime.root / "metrics.db")
            )
        if self.runtime is not None and self.direct_connect_authority_store is None:
            self.direct_connect_authority_store = DirectConnectAuthorityStore(
                self.runtime.root / "direct_connect_authority.db",
                runtime_root=self.runtime.root,
            )
        if self.direct_connect_authority_store is not None:
            # Production ownership precedes boot reference binding. Missing
            # later attachment must never select the standalone default.
            self.direct_connect_authority_store._projection_startup_owner = self

    @classmethod
    def from_runtime(cls, runtime: RuntimeDir, settings: Settings, *, move_diagnostics=None, observation: BootObservation | None = None, defer_coding_registration: bool = True) -> "DaemonState":
        from runtime.orchestrator.team_move_startup import require_runtime_reader
        diagnostics = dict(move_diagnostics or {})
        diagnostics.update(require_runtime_reader(runtime))
        state = cls(runtime=runtime, settings=settings)
        state._startup_observation = observation
        state.broken_orgs.update(diagnostics)
        from runtime.orchestrator.team_move_startup import diagnostic_move_orgs
        state.broken_orgs.update(diagnostic_move_orgs(runtime))
        # __post_init__ constructs metrics_store at runtime.root/metrics.db

        # THR-207 task-producer wiring: construct the daemon-wide supervisor
        # with the capability-factory-selected REAL backend (Linux
        # systemd/cgroup-v2 when the operational probe is healthy, macOS
        # process-group otherwise, honest no-enforcement passthrough as the
        # fallback) and the configured 429 retry schedule owned by the
        # supervisor (finish/release/sleep/reacquire with original enqueue
        # age + fresh backend handle). The same 5/15/45 values the executor
        # throttle used — no policy-default change. Task, schedule, thread,
        # dream, and wake sessions run through it (thread/dream/wake via the
        # worker loops below, which pass ``state.host_supervisor`` to each
        # producer; when it is absent — tests / idle state — the producers
        # keep the legacy uncontained path).
        from runtime.orchestrator.host_supervisor import (
            build_default_host_supervisor,
            canary_policy,
        )
        from runtime.platform.backend_factory import select_session_backend
        state.host_supervisor = build_default_host_supervisor(
            backend=select_session_backend(),
            policy=canary_policy(
                global_session_cap=settings.host_global_session_cap,
                producer_envelope=settings.queue_workers + 7,
            ),
            publisher=state.host_session_store.publish,
            max_retry_attempts=len(settings.executor_rate_limit_backoff_seconds),
            backoff_seconds=tuple(settings.executor_rate_limit_backoff_seconds),
        )

        from runtime.daemon.startup_recovery import _bind_task_admission
        _bind_task_admission(state.host_supervisor, state)

        from runtime.orchestrator.runtime_executor_store import (
            load_runtime_profiles,
        )
        from runtime.runtime import daemon_home

        state.profile_coordinator = ProfileCoordinator(
            daemon_home=daemon_home(),
            orgs=state.orgs,
            capture_orgs=state.capture_orgs,
        )

        # Load runtime-level executor profiles into the process-wide registry
        # so every org can resolve them (machine-global, visible to all orgs).
        #
        # Per-profile iteration: a bad persisted profile must not prevent
        # valid later profiles from loading. Each profile is validated and
        # registered individually; invalid entries are logged and skipped.
        from runtime.orchestrator.executor_registry import get_registry
        runtime_profiles = load_runtime_profiles()
        if runtime_profiles:
            registry = get_registry()
            for name in runtime_profiles:
                try:
                    with state.profile_coordinator.profile_read(name):
                        # Re-read this exact profile while paired with the
                        # process cache publication. A concurrent supported
                        # writer can never expose split YAML/registry state.
                        current = load_runtime_profiles().get(name)
                        if current is not None:
                            profile = registry.validate_custom_profile_config(
                                name, current
                            )
                            registry.register_custom_profile(profile)
                except Exception as exc:
                    logger.warning(
                        "runtime executor profile %r skipped during "
                        "startup load: %s",
                        name,
                        exc,
                    )

        for slug, root in runtime.iter_org_roots():
            if slug in state.broken_orgs:
                if observation is not None:
                    observation.count("orgs_skipped_diagnostic")
                continue
            token = object()
            owner = {"slug": slug, "runtime": runtime, "org_map": state.orgs,
                     "root": root, "org": None, "published": False}
            capture = _AttachmentCapture(owner)
            owner["capture"] = capture
            state._attachment_owners[token] = owner
            try:
                with observed(observation, "org_load"):
                    capture.load_started = True
                    org = OrgState.load(slug=slug, root=root, settings=settings, observation=observation,
                                        attachment_owner=owner,
                                        defer_coding_registration=defer_coding_registration)
                    capture.load_result = org
                    capture.load_returned = True
            except BaseException as exc:
                record = capture.capture("startup load delivery", exc, observation=True)
                capture.load_failure(record)
                owner["primary"] = exc
                capture.attach(exc, "_org_attachment_state", state)
                if issubclass(type(exc), (OrgConsistencyError, ValueError)):
                    # Preserve the original per-org skip categories only after
                    # their required cleanup and diagnostics really completed.
                    try:
                        if observation is not None:
                            observation.count("orgs_refused")
                        state.broken_orgs[slug] = str(exc)
                        logger.error("org %r failed consistency check: %s", slug, exc)
                    except BaseException as later:
                        capture.capture("startup load diagnostics", later)
                    if (capture.load_disposition() in ("closed", "no_handle")
                            and len(capture.occurrences) == 1):
                        del state._attachment_owners[token]
                        continue
                capture.raise_first()
            owner["org"] = org
            # U2A: every whole-runtime construction path (normal startup and
            # live register/switch) initializes eligible selectors before the
            # resulting DaemonState is returned or published. Raw
            # ``OrgState.load`` retains its generation-1 recovery seam; this
            # containing lifecycle owner makes it launch-ready.
            from runtime.orchestrator.active_authority_policy import (
                is_eligible_policy_manager,
            )
            try:
                with observed(observation, "authority_selectors"):
                    for team in org.teams.teams():
                        manager = org.teams.executable_manager_for_team(team)
                        if manager is not None and is_eligible_policy_manager(
                            root=org.root,
                            agent_name=manager,
                            team=team,
                            teams=org.teams,
                        ):
                            org.workflow_authority.ensure_authority_selector(
                                team=team,
                                publisher="daemon-state-load:selector-initialization",
                            )
            except BaseException as primary:
                capture.capture("startup selector delivery", primary, observation=True)
                owner["primary"] = primary
                owner["close_attempted"] = True
                try:
                    org.close()
                except BaseException as cleanup:
                    capture.capture("startup selector close delivery", cleanup, observation=True)
                    owner["close_error"] = cleanup
                else:
                    owner["close_returned"] = True
                # These sinks cannot skip the original selector close above.
                capture.attach(primary, "_org_attachment_state", state)
                if "close_error" in owner:
                    capture.attach(primary, "_org_attachment_close_error", owner["close_error"])
                    capture.note(primary, "startup org close also failed")
                if owner.get("close_returned") is True and len(capture.occurrences) == 1:
                    del state._attachment_owners[token]
                capture.raise_first()
            # Attach the global queue + per-org sessions + daemon-wide host
            # supervisor so the orchestrator can re-enqueue tasks (e.g. parent
            # wake-up after a child resolves) and run task sessions through
            # admission/containment. The lifespan wiring also does this, but
            # `from_runtime` is used by tests that bypass lifespan, so we do
            # it here too — idempotent.
            state._attach_startup_owner(org)
            org.orchestrator.attach_queue(state.queue)
            org.orchestrator.attach_sessions(org.sessions)
            org.orchestrator.attach_host_supervisor(state.host_supervisor)
            with state._org_map_lock:
                state.orgs[slug] = org
                owner["published"] = True
                del state._attachment_owners[token]
            if observation is not None:
                observation.count("orgs_attached")
        assert state.profile_coordinator is not None
        for org in state.orgs.values():
            # Keep the established route-call signatures intact: org-scoped
            # writers reach their daemon-owned machine coordinator through
            # the already-injected OrgState.
            org._profile_coordinator = state.profile_coordinator
        # Diagnostic move orgs remain outside runnable orgs. Global profile
        # writers must not omit their held dependency closure merely because
        # ordinary attachment correctly refused the partial roster.
        state.profile_coordinator._move_diagnostic_orgs = {
            slug: runtime.orgs_dir / slug for slug, reason in state.broken_orgs.items()
            if "team_move_" in reason
        }
        with observed(observation, "profile_reconcile"):
            state.profile_coordinator.reconcile_startup()
        return state

    @property
    def is_idle(self) -> bool:
        return self.runtime is None

    def get_org(self, slug: str) -> OrgState:
        with self._org_map_lock:
            try:
                return self.orgs[slug]
            except KeyError as exc:
                raise KeyError(slug) from exc

    async def add_org(self, slug: str) -> OrgState:
        """Own the entire native/loop attachment before the first await."""
        self._require_startup_ready()
        if self._runner_shutdown:
            raise _OrgBusyError("daemon_shutting_down")
        outcome: dict = {}
        capture = _AttachmentCapture(outcome)
        outcome["capture"] = capture
        coroutine = self._add_org(slug, outcome=outcome)
        try:
            task = asyncio.create_task(coroutine, name="org-attachment:" + slug)
        except BaseException:
            coroutine.close()
            raise
        task._attachment_outcome = outcome
        self._attachment_tasks.add(task)
        def retired(done: asyncio.Task) -> None:
            self._attachment_tasks.discard(done)
            if not done.cancelled():
                error = done.exception()  # consume late errors after disconnect
                if error is not None:
                    retain(error)
        task.add_done_callback(retired)
        from runtime.daemon.startup_recovery import _await_startup_owned
        def retain(error: BaseException) -> None:
            capture.capture("attachment task forwarding", error, observation=True)
            try:
                if self._startup_recovery is not None:
                    self._startup_recovery._retain_secondary(error)
            except BaseException as later:
                capture.capture("attachment task retention", later)
        def on_cancel(cause: BaseException) -> BaseException:
            earlier = None
            owner = outcome.get("owner")
            if owner is not None:
                earlier = owner["capture"].first
                work = owner.get("native_work")
                if earlier is None and work is not None:
                    earlier = work["capture"].first
                if earlier is None:
                    earlier = owner["load_capture"].load_first()
            return capture.cancellation(cause, earlier)
        try:
            return await _await_startup_owned(task, retain, on_cancel=on_cancel)
        except BaseException as exc:
            capture.capture("attachment task delivery", exc, observation=True)
            capture.attach(capture.first["error"], "_attachment_outcome", outcome)
            capture.raise_first()

    async def _attachment_native(self, owner: dict, callback: Callable[[], Any]) -> Any:
        """Join the original worker; cancellation is no native retirement proof."""
        from runtime.daemon.startup_recovery import _await_startup_owned
        ticket = object()
        work = {"owner": owner, "callback": callback, "returned": threading.Event()}
        capture = _AttachmentCapture(owner)
        work["capture"] = capture
        owner["native_work"] = work
        context = copy_context()
        def run() -> Any:
            native_first = None
            result = None
            try:
                result = context.run(callback)
                work["result"] = result
                work["callback_returned"] = True
            except BaseException as exc:
                native_first = capture.capture("native callback delivery", exc, observation=True)
                work["error"] = exc
                work["traceback"] = native_first["traceback"]
            finally:
                try:
                    work["returned"].set()
                except BaseException as later:
                    record = capture.capture("native return event", later)
                    if native_first is None:
                        native_first = record
            if native_first is not None:
                capture.raise_record(native_first)
            return result
        with self._org_map_lock:
            self._attachment_workers[ticket] = work
        # Submission may enqueue and then raise. Never discard its original
        # ticket or wait forever for a Future which was never returned.
        worker = None
        primary = None
        def retain(error: BaseException) -> None:
            capture.capture("native waiter forwarding", error, observation=True)
            try:
                if self._startup_recovery is not None:
                    self._startup_recovery._retain_secondary(error)
            except BaseException as later:
                capture.capture("native waiter retention", later)
        def on_cancel(cause: BaseException) -> BaseException:
            # The first actual native error or waiter cancellation wins at
            # this boundary; a later worker failure stays separately captured.
            load_capture = owner.get("load_capture")
            earlier = None if load_capture is None else load_capture.load_first()
            return capture.cancellation(cause, earlier)
        try:
            worker = asyncio.get_running_loop().run_in_executor(None, run)
            work["worker"] = worker
            return await _await_startup_owned(worker, retain,
                on_cancel=on_cancel)
        except BaseException as exc:
            capture.capture("native submit/wait delivery", exc, observation=True)
            primary = capture.first["error"]
            # Retain the actual operation and its native E1/traceback, including
            # a genuine later error after an earlier waiter cancellation.
            capture.attach(primary, "_attachment_native_work", work)
            capture.raise_first()
        finally:
            try:
                if (worker is not None and worker.done() and not worker.cancelled()
                        and work["returned"].is_set()):
                    with self._org_map_lock:
                        del self._attachment_workers[ticket]
                elif primary is None:
                    raise _OrgBusyError("attachment_native_return_unknown")
            except BaseException as cleanup:
                capture.capture("native ticket retirement", cleanup, observation=True)
                owner["native_retirement_error"] = cleanup
                capture.raise_first()

    async def _add_org(self, slug: str, *, outcome: dict) -> OrgState:
        async with self.orgs_lock:
            self._require_startup_ready()
            if self._runner_shutdown:
                raise _OrgBusyError("daemon_shutting_down")
            current = self.orgs_snapshot().get(slug)
            if current is not None:
                return current
            assert self.runtime is not None
            owner = {"slug": slug, "runtime": self.runtime, "org_map": self.orgs,
                     "root": self.runtime.orgs_dir / slug,
                     "profiles": self.profile_coordinator, "org": None, "published": False,
                     "task": asyncio.current_task(), "native_token": None}
            capture = _AttachmentCapture(owner)
            load_capture = _AttachmentCapture(owner)
            owner["capture"] = capture
            owner["load_capture"] = load_capture
            outcome["owner"] = owner
            token = object()
            with self._org_map_lock:
                self._attachment_owners[token] = owner
            primary = None
            try:
                await self._attachment_native(owner, lambda: self._load_attachment(owner))
                org = owner["org"]
                native_token = object()
                if not org._acquire_native_task(native_token):
                    raise _OrgBusyError("daemon_shutting_down")
                owner["native_token"] = native_token
                # Bind H startup/read identity before M native map publication.
                self._attach_startup_owner(org)
                # Actual asyncio references/publication stay on their loop.
                org.orchestrator.attach_queue(self.queue)
                org.orchestrator.attach_sessions(org.sessions)
                org.orchestrator.attach_host_supervisor(self.host_supervisor)
                org._profile_coordinator = owner["profiles"]
                org._cleanup_diagnostic_unavailable = "dynamic_attachment_unsupported"
                loop = asyncio.get_running_loop()
                await self._attachment_native(owner, lambda: self._publish_attachment(owner))
                # H's binder authenticates current map membership. No loop
                # suspension separates the returned publication from binding.
                org.orchestrator.attach_thread_queue(org.thread_queue, loop, rediscover=False)
                # Reuse the original returned-notification owner, with its actual
                # raw queue handoff on this loop, before releasing native lifetime.
                from runtime.daemon.queue import _AttachmentNotificationQueue
                queue = _AttachmentNotificationQueue(self.queue, loop, org.thread_queue)
                owner["notifications"] = queue
                await self._attachment_native(owner, lambda: self._notify_attached_returned(org, queue=queue))
                await queue.join_puts()
                self.broken_orgs.pop(slug, None)
                if owner["profiles"] is not None:
                    getattr(owner["profiles"], "_move_diagnostic_orgs", {}).pop(slug, None)
                outcome["result"] = org
                outcome["body_returned"] = True
                return org
            except BaseException as exc:
                capture.capture("attachment body delivery", exc, observation=True)
                primary = exc
                owner["primary"] = exc
                outcome["primary"] = exc
                try:
                    if issubclass(type(exc), ValueError) and "team_move_" in str(exc):
                        self.broken_orgs[slug] = str(exc)
                        if owner["profiles"] is not None:
                            diagnostics = getattr(owner["profiles"], "_move_diagnostic_orgs", {})
                            diagnostics[slug] = owner["root"]
                            owner["profiles"]._move_diagnostic_orgs = diagnostics
                except BaseException as later:
                    capture.capture("attachment diagnostics", later)
                capture.raise_first()
            finally:
                try:
                    # A successful Future is insufficient if native retirement is
                    # unknown. The ticket and actual loaded handle stay reachable.
                    with self._org_map_lock:
                        uncertain = any(w["owner"] is owner for w in self._attachment_workers.values())
                    if "native_retirement_error" in owner:
                        uncertain = True
                    notifications = owner.get("notifications")
                    if notifications is not None and notifications.has_unretired_puts():
                        uncertain = True
                    load_disposition = load_capture.load_disposition()
                    if load_disposition == "unknown":
                        uncertain = True
                    if not uncertain:
                        org = owner["org"]
                        if owner["native_token"] is not None:
                            org._release_native_task(owner["native_token"])
                            owner["native_token"] = None
                        safe = True
                        if (org is not None and not owner["published"]
                                and load_disposition == "returned"):
                            owner["close_attempted"] = True
                            try:
                                await self._attachment_native(owner, org.close)
                            except BaseException as cleanup:
                                capture.capture("attachment close delivery", cleanup, observation=True)
                                safe = False
                                owner["close_error"] = cleanup
                                if primary is None:
                                    primary = cleanup
                                    outcome["primary"] = cleanup
                                capture.attach(primary, "_attachment_close_error", cleanup)
                                capture.note(primary, "unpublished attachment close failed")
                            else:
                                owner["close_returned"] = True
                        if safe and len(capture.occurrences) <= 1:
                            with self._org_map_lock:
                                del self._attachment_owners[token]

                except BaseException as cleanup:
                    capture.capture("attachment retirement delivery", cleanup, observation=True)
                    owner["retirement_error"] = cleanup
                    if primary is None:
                        outcome["primary"] = cleanup
                        primary = cleanup
                    capture.attach(primary, "_attachment_retirement_error", cleanup)
                    capture.note(primary, "attachment retirement also failed")
                if primary is not None:
                    # A repeated throw of the SAME object during cleanup may
                    # have changed its live args/traceback; deliver FIRST anew.
                    capture.raise_first()

    def _load_attachment(self, owner: dict) -> None:
        self._require_startup_ready()
        slug, root = owner["slug"], owner["root"]
        if "team_move_" in self.broken_orgs.get(slug, ""):
            from runtime.infrastructure.team_move_store import inspect_team_move_org
            inspect_team_move_org(root, org_slug=slug, require_database=True)
        capture = owner["load_capture"]
        try:
            capture.load_started = True
            org = OrgState.load(slug=slug, root=root, settings=self.settings, attachment_owner=owner)
            capture.load_result = org
            capture.load_returned = True
        except BaseException as exc:
            record = capture.capture("dynamic load delivery", exc, observation=True)
            capture.load_failure(record)
            capture.raise_first()
        owner["org"] = org  # strong handle before selector validation can fail
        from runtime.orchestrator.active_authority_policy import is_eligible_policy_manager
        for team in org.teams.teams():
            manager = org.teams.executable_manager_for_team(team)
            if manager is not None and is_eligible_policy_manager(
                root=org.root, agent_name=manager, team=team, teams=org.teams,
            ):
                org.workflow_authority.ensure_authority_selector(
                    team=team, publisher="daemon-dynamic-add:selector-initialization",
                )

    def _publish_attachment(self, owner: dict) -> None:
        self._require_startup_ready()
        org, profiles = owner["org"], owner["profiles"]
        def insert() -> None:
            with self._org_map_lock:
                if (self._runner_shutdown or self.runtime is not owner["runtime"]
                        or self.orgs is not owner["org_map"]
                        or self.profile_coordinator is not profiles
                        or (profiles is not None and profiles.orgs is not self.orgs)
                        or org.slug in self.orgs):
                    raise _OrgBusyError("attachment_changed_during_preparation")
                self.orgs[org.slug] = org
                owner["published"] = True
        if profiles is None:
            insert()
        else:
            # Acquire/use/release every native profile/publication lease on
            # THIS worker, including publication before the leases are released.
            with profiles.dynamic_org_attachment(org):
                insert()

    def _assert_can_remove_org(self, slug: str) -> OrgState | None:
        self._require_startup_ready()
        if self._runner_shutdown:
            raise _OrgBusyError("daemon_shutting_down")
        self.team_move_service.assert_no_owner(slug)
        current = self.orgs_snapshot().get(slug)
        if current is not None:
            self._assert_startup_idle(current)
            current._assert_runners_idle()
            if current.db._team_move_coordinator.store.read() is not None:
                from runtime.infrastructure.team_move_store import TeamMoveError
                raise TeamMoveError("team_move_runtime_detach_held")
            in_flight = current.db.get_nonterminal_task_ids()
            if in_flight:
                raise _OrgBusyError("active_tasks_in_flight", task_ids=in_flight)
        return current

    async def remove_org(self, slug: str) -> None:
        self._require_startup_ready()
        primary = None
        traceback = None
        async with self.orgs_lock:
            self._require_startup_ready()
            # Local checks only: C may own the DB through a stalled COMMIT.
            # Reach finite revoke before any DB-backed task/move preflight.
            if self._runner_shutdown:
                raise _OrgBusyError("daemon_shutting_down")
            self.team_move_service.assert_no_owner(slug)
            current = self.orgs_snapshot().get(slug)
            if current is not None:
                current._assert_runners_idle(thread_releases=False)
                if self._startup_recovery is not None:
                    self._startup_recovery.assert_native_idle()
            captured = self._capture_attachments()
            if current is not None:
                current._runner_admission_closed = True
                revokes = [lambda: self._revoke_thread_releases(current),
                           current._revoke_cleanup_diagnostics]
                if self._startup_reads is not None:
                    revokes.insert(0, lambda: self._startup_reads.revoke(current))
                for revoke in revokes:
                    try:
                        revoke()
                    except BaseException as exc:
                        if primary is None:
                            primary, traceback = exc, exc.__traceback__
                        elif self._startup_recovery is not None:
                            self._startup_recovery._retain_secondary(exc)
        # Never await a native scan under orgs/root/move/terminal locks.
        if current is not None:
            for drain in (self._drain_startup, self._drain_thread_releases,
                          self._drain_cleanup_diagnostics):
                try:
                    await drain(current)
                except BaseException as exc:
                    if primary is None:
                        primary, traceback = exc, exc.__traceback__
                    elif self._startup_recovery is not None:
                        self._startup_recovery._retain_secondary(exc)
            if primary is not None:
                raise primary.with_traceback(traceback)
        async with self.orgs_lock:
            self._assert_attachments_unchanged(captured)
            current = self._assert_can_remove_org(slug)
            if current is not None:
                self._begin_attachment_close((current,))
                current.close()
                with self._org_map_lock:
                    self.orgs.pop(slug)
                    self._retiring_orgs.discard(id(current))
                # Positive close and removal authenticate only THIS original.
                gate = self._startup_recovery
                current._startup_detached = (self, gate, None if gate is None else gate.epoch,
                                             current._startup_admission_refs, slug, current.root)

    def _worker_org_current(self, org, validate_service) -> bool:
        """Skip only a positively detached original selection in this epoch."""
        from runtime.daemon.startup_recovery import require_ready, StartupUnavailable
        validate_service()  # The SAME shared service survives, never a failed epoch.
        gate = self._startup_recovery
        detached = org._startup_detached
        if (self.orgs.get(org.slug) is None and isinstance(detached, tuple)
                and len(detached) == 6 and detached[0] is self and detached[1] is gate
                and detached[2] == (None if gate is None else gate.epoch)
                and detached[3] is org._startup_admission_refs
                and detached[4] == org.slug and detached[5] == org.root
                and isinstance(detached[3], tuple) and len(detached[3]) == 7
                and all(current is original for current, original in zip(
                    (org.orchestrator, org.db, org.sessions, org.thread_queue,
                     org.dream_queue, org.wake_queue, org.schedule_queue), detached[3], strict=True))
                and org._startup_state is self and org._startup_recovery is gate
                and org._runner_admission_closed):
            return False
        if detached is not None or org._runner_admission_closed:
            raise StartupUnavailable("startup_attachment_changed")
        require_ready(org)  # Current replacement and unknown detachment still refuse.
        return True

    async def close_all(self) -> None:
        primary = None
        traceback = None
        def remember(exc: BaseException) -> None:
            nonlocal primary, traceback
            if primary is None:
                primary, traceback = exc, exc.__traceback__
            elif self._startup_recovery is not None:
                self._startup_recovery._retain_secondary(exc)
        # A failed revocation still forbids destruction, but cannot discharge
        # independently owed original attachment/read/service drains.
        try:
            self._stop_runner_admission()
        except BaseException as exc:
            remember(exc)
        captured = self._capture_attachments()
        for drain in (self._drain_attachments, self._drain_startup,
                      self._drain_thread_releases):
            try:
                await drain()
            except BaseException as exc:
                remember(exc)
        try:
            self._revoke_cleanup_diagnostics()
        except BaseException as exc:
            remember(exc)
        for drain in (self._drain_cleanup_diagnostics, self.team_move_service.close):
            try:
                await drain()
            except BaseException as exc:
                remember(exc)
        # Ended errors preserve E1 while every independent join above is owed.
        # Unknown owners still fail the final positive barriers below.
        if primary is not None:
            raise primary.with_traceback(traceback)
        if self._startup_recovery is not None:
            self._startup_recovery.assert_consumers_idle()
        self._assert_attachments_unchanged(captured)
        self.queue.assert_native_idle()
        async with self.orgs_lock:
            self._assert_attachments_unchanged(captured)
            self._assert_attachment_loads_idle()
            self.queue.assert_native_idle()
            orgs = tuple(self.orgs_snapshot().values())
            for org in orgs:
                self._assert_startup_idle(org)
                org._assert_runners_idle()
                org._assert_cleanup_diagnostics_idle()
            self._begin_attachment_close(orgs)
            for org in orgs:
                try:
                    org.close()
                except BaseException as exc:
                    remember(exc)
            if primary is not None:
                # Keep the original map/handles fenced on partial close.
                raise primary.with_traceback(traceback)
            with self._org_map_lock:
                self.orgs.clear()
                self._retiring_orgs.difference_update(id(org) for org in orgs)
        self._assert_startup_idle()
        if self.direct_connect_authority_store is not None:
            self.direct_connect_authority_store.close()
        self._shutdown_complete = True
