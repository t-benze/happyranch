"""Boot-local recovery ownership; listener liveness never grants admission.

Native completion and the request-loop coordinator have distinct ownership.
Cancelling a waiter cannot release a native owner or close its databases.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Literal

_LOG = logging.getLogger(__name__)
_PHASES = ("sweep", "workflows", "returned", "compatibility", "redelivery", "producers")
_COUNTERS = {
    "returned": ("orgs_bound", "jobs_bound", "coding_absent", "orgs_observed"),
    "compatibility": ("orgs_observed", "layouts_completed", "migration_advisories",
                      "naming_unavailable", "skill_advisories", "unknown_jobs",
                      "pending_scans", "pending_orgs_reported", "pending_rows_reported"),
    "producers": ("dream_orgs", "dreams_recovered", "wake_orgs", "wakes_recovered",
                  "schedule_orgs", "schedules_recovered", "schedule_audits"),
}
_ACTION = "Read threads while recovery runs; retry new work after recovery is ready."
_FAILED_ACTION = "New work remains paused. Send the startup error code to engineering_manager."


class StartupUnavailable(RuntimeError):
    """Closed admission, without claiming that a request was accepted."""

    def __init__(self, code: str, startup: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = {"code": code, "startup": startup, "required_action":
                       _ACTION if code == "daemon_recovering" else _FAILED_ACTION}


@dataclass(frozen=True)
class RecoveryOutcome:
    disposition: Literal["settled", "local_hold", "unavailable", "unknown"]
    code: str | None = None

    @property
    def positive(self) -> bool:
        return self.disposition in ("settled", "local_hold")


@dataclass
class _Phase:
    outcome: str = "not_started"
    started_ns: int | None = None
    ended_ns: int | None = None
    counts: dict[str, int] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return {"outcome": self.outcome, "counts": dict(self.counts), "duration_ns":
                None if self.started_ns is None or self.ended_ns is None
                else self.ended_ns - self.started_ns}


@dataclass
class _ReferenceBinding:
    """One original real-loop operation; wrapper completion is not native exit."""

    epoch: str
    future: Future | None = None
    coroutine: Any = None
    submission_attempted: bool = False
    task: asyncio.Task | None = None
    completion: Future = field(default_factory=Future)
    error: BaseException | None = None
    observed: bool = False
    orgs_bound: int = 0
    jobs_bound: bool = False


class StartupRecovery:
    """One retained native operation and one request-loop activation latch.

    Phases are serial owner intervals: durations include native joins and queue
    acknowledgements. Serving can overlap recovery; their times must not be
    summed. This owner does not infer launcher/API/browser usability timestamps.
    """

    def __init__(self, state: Any, app: Any) -> None:
        self.state = state
        self.app = app
        self.epoch = uuid.uuid4().hex
        self._admission_queue = state.queue
        self._admission_host = state.host_supervisor
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._status = "recovering"
        self._serving = False
        self._phase = "attachment"
        self._phases = {name: _Phase(counts=dict.fromkeys(_COUNTERS.get(name, ()), 0)) for name in _PHASES}
        self._first_cause: BaseException | None = None
        self._error_code: str | None = None
        self._secondary: list[BaseException] = []
        self._secondary_overflow = 0
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="startup-recovery")
        self._native: Future[RecoveryOutcome] | None = None
        self._native_thread: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._latch: asyncio.Event | None = None
        self._attachments: tuple | None = None
        self._redelivery_queues: tuple | None = None
        self._binding: _ReferenceBinding | None = None
        # Retain the EXACT resolver supplied to the jobs reference binder.
        self._jobs_resolver = lambda slug: self.state.orgs[slug].orchestrator if slug in self.state.orgs else None
        self._closed = False
        self._ready_commits = 0
        self._created_ns = time.monotonic_ns()
        self._serving_ns: int | None = None
        self._sweep_owner: Any | None = None
        self._coordinator: asyncio.Task | None = None
        self._consumers: list[asyncio.Task] = []
        self._preparation_complete = False
        self._dashboard_loads: set[Future] = set()
        self._services_signalled = False
        from runtime.daemon.startup_publication import StartupPublications
        self._publications = StartupPublications(self)

    def publications(self, orch: Any):
        """Native P4/P6 scope, after true-loop binding and cold P2 closure.

        Wrap the existing rediscover_returned call, not an extra scan. Its
        native producer drains after marker/DB locks unwind, before the phase
        outcome. This does not bind an orchestrator or recover at attachment.
        """
        return self._publications.scope(orch)

    def publication_outcome(self) -> RecoveryOutcome:
        """After scope exit/drain, supply the explicit P4/P6 phase outcome."""
        self.require_native_owner()
        self._publications.assert_idle()
        counts = self._publications.snapshot()
        return RecoveryOutcome("local_hold" if counts["held"] or counts["preserved_marker"] else "settled")

    def require_publication_owner(self, orch: Any) -> None:
        """The native returned/P6 owner, after cold sweep/workflow closure."""
        self.require_native_owner()
        with self._lock:
            allowed = (self._phase in ("returned", "redelivery", "producers")
                       and self._phases["sweep"].outcome in ("settled", "local_hold")
                       and self._phases["workflows"].outcome in ("settled", "local_hold"))
        org = self.state.orgs.get(orch._slug)
        if self._phase == "returned":
            self.require_returned_owner(org)
        if self._phase == "redelivery" and org is not None:
            self.require_redelivery_owner(org)
        if (not allowed or org is None or org.orchestrator is not orch
                or org.db is not orch._db or orch._main_loop is not self._loop
                or org._startup_recovery is not self
                or getattr(orch, "_startup_recovery", None) is not self
                or orch._thread_queue is not org.thread_queue):
            raise RuntimeError("startup_publication_phase_or_attachment_unavailable")

    def count_phase(self, phase: str, event: str, count: int = 1) -> None:
        """Fixed scalar observations at the existing producers, never a census."""
        self.require_native_owner()
        with self._lock:
            if (self._phase != phase or event not in self._phases[phase].counts
                    or type(count) is not int or count < 0):
                raise RuntimeError("startup_phase_counter_invalid")
            self._phases[phase].counts[event] += count

    def _require_phase_attachments(self, phase: str, *, binding: _ReferenceBinding | None = None) -> None:
        """Reference checks shared by native admission and actual-loop effects.

        This performs no DB/file work and deliberately has no native-thread
        guard: the binding coroutine must authenticate on the real loop too.
        """
        self.state._assert_attachments_unchanged(self._attachments)
        with self._lock:
            allowed = (self._phase == phase and self._phases[phase].outcome == "incomplete"
                       and self._status == "recovering" and self._first_cause is None
                       and not self._closed and not self._stop.is_set()
                       and self._sweep_owner is None and self._native_thread is not None)
            if binding is not None:
                allowed &= self._binding is binding and binding.epoch == self.epoch
        if (not allowed or self.state._startup_recovery is not self
                or self.app.state.daemon is not self.state or self._redelivery_queues is None
                or self.epoch != self._publications.epoch
                or self._loop is None or self._loop.is_closed() or not self._loop.is_running()):
            raise RuntimeError("startup_phase_owner_or_loop_unavailable")
        self._assert_reference_attachments(binding if binding is not None else self._binding)

    def _assert_reference_attachments(self, current: _ReferenceBinding | None) -> None:
        """Original references, including after the native operation has exited."""
        self.state._assert_attachments_unchanged(self._attachments)
        queue, originals = self._redelivery_queues
        if self.state.queue is not queue:
            raise RuntimeError("startup_phase_queue_changed")
        for index, (org, orch, thread, dream, wake, schedule) in enumerate(originals):
            bound = current is not None and index < current.orgs_bound
            if (self.state.orgs.get(org.slug) is not org or org.orchestrator is not orch
                    or orch._db is not org.db or orch._slug != org.slug or orch._queue is not queue
                    or org._startup_recovery is not self or getattr(orch, "_startup_recovery", None) is not self
                    or org.thread_queue is not thread or org.dream_queue is not dream
                    or org.wake_queue is not wake or org.schedule_queue is not schedule
                    or orch._main_loop is not (self._loop if bound else None)
                    or orch._thread_queue is not (thread if bound else None)):
                raise RuntimeError("startup_phase_attachment_changed")
        from runtime.daemon.jobs_runner import jobs_resume_binding_matches
        jobs_bound = current is not None and current.jobs_bound
        if not jobs_resume_binding_matches(self._loop if jobs_bound else None,
                                          self._jobs_resolver if jobs_bound else None):
            raise RuntimeError("startup_jobs_binding_changed")

    def _require_later_native(self, phase: str, org: Any = None, *, bound: bool = True) -> None:
        self.require_native_owner()
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("startup_phase_requires_native_thread")
        self._require_phase_attachments(phase)
        self._publications.assert_idle()
        if bound:
            self._assert_binding_complete(required=True)
        elif self._binding is not None:
            raise RuntimeError("startup_binding_already_reserved")
        if org is not None and self.state.orgs.get(org.slug) is not org:
            raise RuntimeError("startup_phase_org_changed")

    def require_returned_owner(self, org: Any = None, *, bound: bool = True) -> None:
        # Publication scopes themselves must be allowed to remain active here.
        self.require_native_owner()
        self._require_phase_attachments("returned")
        if bound:
            self._assert_binding_complete(required=True)
        elif self._binding is not None:
            raise RuntimeError("startup_binding_already_reserved")
        if org is not None and self.state.orgs.get(org.slug) is not org:
            raise RuntimeError("startup_returned_org_changed")

    def require_compatibility_owner(self, org: Any = None) -> None:
        self._require_later_native("compatibility", org)

    def require_producers_owner(self, org: Any = None) -> None:
        self._require_later_native("producers", org)

    def _assert_binding_complete(self, *, required: bool = False) -> None:
        with self._lock:
            binding = self._binding
        if binding is None:
            if required:
                raise RuntimeError("startup_binding_missing")
            return
        if (not binding.completion.done() or not binding.observed
                or binding.completion.result() is not True or binding.error is not None
                or binding.orgs_bound != len(self._redelivery_queues[1]) or not binding.jobs_bound):
            raise RuntimeError("startup_binding_incomplete")
        self._assert_reference_attachments(binding)

    def _assert_binding_idle(self) -> None:
        # A failed, positively ended operation is idle, but never successful.
        with self._lock:
            binding = self._binding
        if binding is not None and (not binding.completion.done() or binding.completion.cancelled()):
            raise RuntimeError("startup_binding_native_still_owned")

    def bind_returned_references(self) -> None:
        """P4 singleton: submit on the real loop, observe OFF loop/locks.

        Registration survives submission uncertainty and wrapper cancellation.
        Only the actual Task callback supplies native completion. No rollback
        of partial references or inference of quiescence from cancellation.
        """
        # Import closure on the native owner BEFORE any actual-loop callback.
        from runtime.daemon.jobs_runner import attach_jobs_resume_main_loop
        self._require_later_native("returned", bound=False)
        for org, orch, *_ in self._redelivery_queues[1]:
            self._publications._outside_db_lock(orch)
        binding = _ReferenceBinding(self.epoch)
        with self._lock:
            if self._binding is not None or self._stop.is_set():
                raise RuntimeError("startup_binding_duplicate_or_stopped")
            self._binding = binding

        def completed(task: asyncio.Task) -> None:
            try:
                result = task.result()
                if result is not True:
                    raise RuntimeError("startup_binding_outcome_unknown")
            except BaseException as exc:
                binding.error = exc
                self.fail("startup_binding_failed", exc)
                binding.completion.set_result(False)
            else:
                binding.completion.set_result(True)

        async def bind() -> bool:
            task = asyncio.current_task()
            if task is None or binding.task is not None:
                raise RuntimeError("startup_binding_native_task_invalid")
            binding.task = task
            task.add_done_callback(completed)
            # Separate loop admission: never call require_native_owner here.
            if asyncio.get_running_loop() is not self._loop:
                raise RuntimeError("startup_binding_wrong_loop")
            self._require_phase_attachments("returned", binding=binding)
            for org, orch, thread, *_ in self._redelivery_queues[1]:
                self._require_phase_attachments("returned", binding=binding)
                orch.bind_thread_queue_references(thread, self._loop)
                binding.orgs_bound += 1
                self._require_phase_attachments("returned", binding=binding)
            self._require_phase_attachments("returned", binding=binding)
            attach_jobs_resume_main_loop(self._loop, self._jobs_resolver)
            binding.jobs_bound = True
            self._require_phase_attachments("returned", binding=binding)
            return True

        try:
            binding.coroutine = bind()
            binding.submission_attempted = True
            binding.future = asyncio.run_coroutine_threadsafe(binding.coroutine, self._loop)
            # Same off-loop wait policy as owned queue publications. No owner,
            # DB, move, publication or SYSTEM-marker lock spans either wait.
            result = self._publications._wait_future(binding.future)
            native_result = self._publications._wait_future(binding.completion)
            binding.observed = True
            if result is not True or native_result is not True:
                raise binding.error or RuntimeError("startup_binding_unacknowledged")
            self.require_returned_owner()
            self.count_phase("returned", "orgs_bound", binding.orgs_bound)
            self.count_phase("returned", "jobs_bound")
        except BaseException as exc:
            self.fail("startup_binding_failed", exc)
            if binding.task is not None:
                try:
                    self._publications._wait_future(binding.completion)
                except BaseException as secondary:
                    self.fail("startup_binding_exit_unobserved", secondary)
            raise

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind references only, on the actual request loop, before submission."""
        if asyncio.get_running_loop() is not loop:
            raise RuntimeError("startup_wrong_loop")
        attachments = self.state._capture_attachments()
        with self._lock:
            if self._loop is not None or self._closed or self._stop.is_set():
                raise RuntimeError("startup_already_bound_or_stopped")
            self._loop = loop
            self._latch = asyncio.Event()
            self._attachments = attachments
            # Capture actual original queues before P4 binds loop references.
            self._redelivery_queues = (self.state.queue, tuple(
                (org, org.orchestrator, org.thread_queue, org.dream_queue,
                 org.wake_queue, org.schedule_queue) for org in self.state.orgs.values()))

    def signal_serving(self, app: Any) -> None:
        """Only the real Server's matching, successful startup calls this."""
        with self._lock:
            if app is not self.app or self._loop is None or self._closed or self._stop.is_set():
                raise RuntimeError("startup_listener_attachment_mismatch")
            if not self._serving:
                self._serving_ns = time.monotonic_ns()
                self._serving = True
        self._signal_services()

    def _signal_services(self) -> None:
        # The original listener observation and recovery may arrive in either
        # order. Their conjunction is consumed once, on the actual loop.
        with self._lock:
            if (self._services_signalled or not self._serving or self._status != "ready"
                    or self._stop.is_set() or self._closed):
                return
            self._services_signalled = True
        for service in (self.state._cleanup_diagnostics, self.state._thread_release_service):
            if service is not None:
                try:
                    service.signal_ready(self.app)
                except Exception:
                    # These independent services retain their original isolation;
                    # neither supplies the mandatory recovery outcome.
                    try:
                        _LOG.exception("startup listener service unavailable")
                    except Exception:
                        pass  # Optional diagnostic logging cannot change readiness.

    def prepare_consumer(self, name: str, factory: Callable[[], Any]) -> asyncio.Task:
        """Retain a dormant handle before any coroutine BODY/producer runs."""
        if asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("startup_consumer_wrong_loop")
        self.state._assert_attachments_unchanged(self._attachments)
        with self._lock:
            if (self._status != "recovering" or self._stop.is_set()
                    or self._first_cause is not None or self._preparation_complete):
                raise RuntimeError("startup_consumer_preparation_unavailable")
        async def run() -> Any:
            await self.wait_ready()
            return await factory()
        coroutine = run()
        try:
            task = self._loop.create_task(coroutine, name=name)
        except BaseException:
            coroutine.close()
            raise
        self._consumers.append(task)
        return task

    def prepared(self) -> None:
        if asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("startup_preparation_wrong_loop")
        self.state._assert_attachments_unchanged(self._attachments)
        with self._lock:
            if self._preparation_complete or any(task.done() for task in self._consumers):
                raise RuntimeError("startup_consumer_preparation_incomplete")
            self._preparation_complete = True

    async def load_dashboard(self, manager: Any) -> Any:
        """Only the existing cold disk read, on the original owned executor.

        Periodic warm/persistence retains its existing owner and behavior. A
        canceled disk-load waiter joins its actual Future before resources close.
        """
        self.require_ready()
        with self._lock:
            if self._stop.is_set() or self._closed or self._status != "ready":
                raise RuntimeError("startup_dashboard_admission_closed")
            native = self._executor.submit(manager.load_from_disk)
            self._dashboard_loads.add(native)
        wrapped = asyncio.wrap_future(native)
        cancellation = None
        try:
            while True:
                try:
                    result = await asyncio.shield(wrapped)
                    break
                except asyncio.CancelledError as exc:
                    cancellation = cancellation or exc
                    if wrapped.done():
                        result = wrapped.result()
                        break
            if cancellation is not None:
                raise cancellation
            self.require_ready()
            return result
        finally:
            if native.done() and not native.cancelled():
                with self._lock:
                    self._dashboard_loads.discard(native)

    def assert_consumers_idle(self) -> None:
        if any(not task.done() for task in self._consumers):
            raise RuntimeError("startup_consumer_join_incomplete")

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            binding = self._binding
            return {"state": self._status, "epoch": self.epoch,
                    "serving": self._serving,
                    "recovery_ready": self._status == "ready" and not self._stop.is_set(),
                    "stopping": self._stop.is_set(), "phase": self._phase,
                    "error_code": self._error_code,
                    "ready_commits": self._ready_commits,
                    "consumers_prepared": len(self._consumers),
                    "preparation_complete": self._preparation_complete,
                    "publications": self._publications.snapshot(),
                    "reference_binding": {
                        "registered": binding is not None,
                        "native_complete": binding is not None and binding.completion.done(),
                        "observed": binding is not None and binding.observed,
                        "orgs_bound": 0 if binding is None else binding.orgs_bound,
                        "jobs_bound": binding is not None and binding.jobs_bound,
                    },
                    "launcher_duration_ns": None,
                    "owner_to_serving_ns": None if self._serving_ns is None
                    else self._serving_ns - self._created_ns,
                    "phases": {name: phase.public() for name, phase in self._phases.items()}}

    def require_ready(self) -> None:
        with self._lock:
            allowed = self._status == "ready" and not self._stop.is_set() and not self._closed
        if not allowed:
            current = self.snapshot()
            code = "daemon_recovering" if current["state"] == "recovering" and not current["stopping"] else "daemon_recovery_failed"
            raise StartupUnavailable(code, current)

    def require_attachment(self, owner: Any) -> None:
        """Authenticate the actual current production owner without DB work."""
        state = self.state
        if (state._startup_recovery is not self or self.app.state.daemon is not state
                or (owner is not state and owner._startup_state is not state)
                or owner._startup_recovery is not self):
            raise StartupUnavailable("startup_attachment_changed")
        if (state.queue is not self._admission_queue or state.host_supervisor is not self._admission_host
                or state.queue._startup_state is not state or state.queue._startup_recovery is not self
                or (state.host_supervisor is not None
                    and (state.host_supervisor._startup_state is not state
                         or state.host_supervisor._startup_recovery is not self))):
            raise StartupUnavailable("startup_attachment_changed")
        if owner is state:
            return
        org = owner._startup_org
        if org is None:
            attached = owner is state.queue or owner is state.host_supervisor
        else:
            refs = org._startup_admission_refs
            attached = (isinstance(refs, tuple) and len(refs) == 4 and org.orchestrator is refs[0]
                        and org.db is refs[1] and org.sessions is refs[2]
                        and org.thread_queue is refs[3]
                        and org.thread_queue._startup_state is state
                        and org.thread_queue._startup_org is org
                        and org.thread_queue._startup_recovery is self
                        and state.orgs.get(org.slug) is org and org._startup_recovery is self
                        and org._startup_state is state
                        and any(owner is original for original in (org, org.orchestrator, org.sessions, org.thread_queue))
                        and org.orchestrator._db is org.db
                        and org.orchestrator._slug == org.slug
                        and org.orchestrator._queue is state.queue
                        and org.orchestrator._sessions is org.sessions
                        and org.orchestrator._host_supervisor is state.host_supervisor
                        and (self._status != "ready" or (
                            org.orchestrator._thread_queue is org.thread_queue
                            and org.orchestrator._main_loop is self._loop)))
        if not attached or state.queue._startup_recovery is not self:
            raise StartupUnavailable("startup_attachment_changed")

    def require_native_owner(self) -> None:
        with self._lock:
            allowed = (self._native_thread == threading.get_ident()
                       and self._status == "recovering" and not self._stop.is_set()
                       and self._first_cause is None)
        if not allowed:
            raise StartupUnavailable("startup_owner_unavailable", self.snapshot())

    def require_workflow_owner(self, org: Any, *, sweep_owner: Any = None) -> None:
        """P2/P3 only: original native operation, attachment and cold sweep.

        No caller Boolean grants this entry. P2 must carry the actually retained
        sweep/context owner; P3 follows its settled boundary. Neither can use
        the later returned-publication scope or a private asyncio loop.
        """
        self.require_native_owner()
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("startup_workflow_requires_native_thread")
        self.state._assert_attachments_unchanged(self._attachments)
        with self._lock:
            allowed = ((sweep_owner is not None and self._phase == "sweep"
                        and self._sweep_owner is sweep_owner)
                       or (sweep_owner is None and self._phase == "workflows"
                           and self._sweep_owner is None
                           and self._phases["sweep"].outcome in ("settled", "local_hold")))
        orch = org.orchestrator
        if (not allowed or self.state.orgs.get(org.slug) is not org
                or org._startup_recovery is not self
                or getattr(orch, "_startup_recovery", None) is not self
                or orch._db is not org.db or orch._slug != org.slug
                or orch._queue is not self.state.queue or orch._main_loop is not None):
            raise RuntimeError("startup_workflow_phase_or_attachment_unavailable")
        if sweep_owner is not None:
            from runtime.orchestrator.run_step import _ROOT_WORKTREE_OWNER_EVENTS
            if (sweep_owner.orch is not orch
                    or _ROOT_WORKTREE_OWNER_EVENTS.get() is not sweep_owner.delivery
                    or sweep_owner._body_thread != threading.get_ident()):
                raise RuntimeError("startup_workflow_sweep_owner_unavailable")
            sweep_owner.check()

    def require_redelivery_owner(self, org: Any) -> None:
        """P6 only; never reuse the cold P2/P3 workflow admission."""
        self.require_native_owner()
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("startup_redelivery_requires_native_thread")
        self.state._assert_attachments_unchanged(self._attachments)
        self._require_phase_attachments("redelivery")
        self._assert_binding_complete(required=True)
        with self._lock:
            allowed = (self._phase == "redelivery"
                       and self._phases["compatibility"].outcome in ("settled", "local_hold"))
        if not allowed or self._redelivery_queues is None:
            raise RuntimeError("startup_redelivery_phase_unavailable")
        queue, originals = self._redelivery_queues
        if self.state.queue is not queue or self.state.orgs.get(org.slug) is not org:
            raise RuntimeError("startup_redelivery_attachment_changed")
        matched = False
        for original, orch, thread, dream, wake, schedule in originals:
            if original is not org:
                continue
            matched = True
            if (org.orchestrator is not orch or orch._db is not org.db
                    or orch._slug != org.slug or orch._queue is not queue
                    or orch._main_loop is not self._loop
                    or org._startup_recovery is not self
                    or getattr(orch, "_startup_recovery", None) is not self
                    or orch._thread_queue is not thread or org.thread_queue is not thread
                    or org.dream_queue is not dream or org.wake_queue is not wake
                    or org.schedule_queue is not schedule):
                raise RuntimeError("startup_redelivery_queue_or_owner_changed")
        if not matched:
            raise RuntimeError("startup_redelivery_org_unavailable")

    def publish_workflow_task(self, org: Any, task_id: str, *, sweep_owner: Any = None) -> RecoveryOutcome:
        """Acknowledge the original committed task, never its reservation alone.

        These document/coding owners already select their authentic queued
        pointer. Keep their original raw publication shape and pending identity;
        no generation is minted and no committed graph is rolled back on loss.
        """
        self.require_workflow_owner(org, sweep_owner=sweep_owner)
        from runtime.daemon.queue import TaskQueue
        queue = self.state.queue
        if not isinstance(queue, TaskQueue):
            raise RuntimeError("startup_workflow_queue_unavailable")
        from runtime.daemon.startup_publication import _TaskPublication
        publication = _TaskPublication(self, org.orchestrator, task_id, "workflow",
                                       sweep_owner=sweep_owner)
        inserted = queue.enqueue_if_absent(org.slug, task_id, _publication=publication)
        if type(inserted) is not bool:
            raise RuntimeError("startup_workflow_queue_outcome_unknown")
        if queue.pending_publication(org.slug, task_id) != "pending":
            raise RuntimeError("startup_workflow_task_unacknowledged")
        self.require_workflow_owner(org, sweep_owner=sweep_owner)
        return RecoveryOutcome("settled", "startup_workflow_task_inserted" if inserted
                               else "startup_workflow_task_already_pending")

    def fail(self, code: str, cause: BaseException) -> None:
        with self._lock:
            if self._first_cause is None:
                self._first_cause = cause
                self._error_code = code
            elif cause is not self._first_cause:
                if len(self._secondary) < 8:
                    self._secondary.append(cause)
                else:
                    self._secondary_overflow += 1
            self._status = "recovery_failed"
            sweep = self._sweep_owner
        # Never nest the gate lock inside a sweep lock in the opposite order.
        # Close new tail effects without interrupting their native termination.
        if sweep is not None:
            sweep.fail(cause)
        self._publications.close_admission()

    @contextmanager
    def phase(self, name: str) -> Iterator[Callable[[RecoveryOutcome], None]]:
        self.require_native_owner()
        with self._lock:
            if name not in self._phases or self._phases[name].outcome != "not_started":
                raise RuntimeError("startup_phase_duplicate_or_unknown")
            index = _PHASES.index(name)
            if any(self._phases[previous].outcome not in ("settled", "local_hold") for previous in _PHASES[:index]):
                raise RuntimeError("startup_phase_out_of_order")
            phase = self._phases[name]
            phase.outcome = "incomplete"
            phase.started_ns = time.monotonic_ns()
            self._phase = name
        outcome: RecoveryOutcome | None = None
        primary: BaseException | None = None

        def settle(value: RecoveryOutcome) -> None:
            nonlocal outcome
            if outcome is not None or not isinstance(value, RecoveryOutcome):
                raise RuntimeError("startup_outcome_duplicate_or_invalid")
            outcome = value

        try:
            yield settle
            self.require_native_owner()
            self._publications.assert_idle()
            if _PHASES.index(name) >= _PHASES.index("returned"):
                self._assert_binding_complete(required=True)
            if outcome is None or not outcome.positive:
                raise RuntimeError(outcome.code if outcome is not None and outcome.code else "startup_outcome_unavailable")
        except BaseException as exc:
            primary = exc
            self.fail("startup_" + name + "_failed", exc)
            with self._lock:
                phase.outcome = "failed"
            raise
        else:
            with self._lock:
                phase.outcome = outcome.disposition
        finally:
            with self._lock:
                phase.ended_ns = time.monotonic_ns()
            try:
                _LOG.info("startup phase=%s outcome=%s epoch=%s", name, phase.outcome, self.epoch)
            except BaseException as exc:
                self.fail("startup_phase_observation_failed", exc)
                with self._lock:
                    phase.outcome = "failed"
                if primary is None:
                    raise

    def submit(self, operation: Callable[[StartupRecovery], RecoveryOutcome]) -> Future[RecoveryOutcome]:
        """Reserve before native start; this operation is never resubmitted."""
        with self._lock:
            if self._loop is None or self._native is not None or self._closed or self._stop.is_set():
                raise RuntimeError("startup_submission_unavailable")
            # submit can start immediately; _run first acquires this same lock.
            self._native = self._executor.submit(self._run, operation)
            return self._native

    def _run(self, operation: Callable[[StartupRecovery], RecoveryOutcome]) -> RecoveryOutcome:
        with self._lock:
            self._native_thread = threading.get_ident()
        try:
            self.require_native_owner()
            outcome = operation(self)
            if not isinstance(outcome, RecoveryOutcome) or not outcome.positive:
                raise RuntimeError("startup_native_outcome_unavailable")
            self.require_native_owner()
            self._publications.assert_idle()
            self._assert_binding_complete(required=True)
            with self._lock:
                if any(phase.outcome not in ("settled", "local_hold") for phase in self._phases.values()):
                    raise RuntimeError("startup_native_phases_incomplete")
            return outcome
        except BaseException as exc:
            self.fail("startup_native_failed", exc)
            raise
        finally:
            with self._lock:
                self._native_thread = None

    async def join(self) -> RecoveryOutcome:
        """A cancelled waiter still consumes the ORIGINAL native completion."""
        with self._lock:
            native = self._native
        if native is None:
            raise RuntimeError("startup_not_submitted")
        wrapped = asyncio.wrap_future(native)
        cancellation: asyncio.CancelledError | None = None
        while True:
            try:
                outcome = await asyncio.shield(wrapped)
                break
            except asyncio.CancelledError as exc:
                # Cancellation does not cancel the concurrent Future.
                self.stop()
                cancellation = cancellation or exc
                if wrapped.done():
                    # A genuinely cancelled native Future is not settlement.
                    outcome = wrapped.result()
                    break
        if cancellation is not None:
            raise cancellation
        return outcome

    def activate(self) -> None:
        """One non-await commit on the true loop, after dormant handles exist."""
        if asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("startup_wrong_activation_loop")
        self.state._assert_attachments_unchanged(self._attachments)
        self._publications.assert_idle()
        self._assert_binding_complete(required=True)
        with self._lock:
            if (self._native is None or not self._native.done() or self._native.cancelled()
                    or self._closed or self._stop.is_set() or self._first_cause is not None
                    or self._status != "recovering" or self._latch is None
                    or not self._preparation_complete or any(task.done() for task in self._consumers)):
                raise RuntimeError("startup_activation_unavailable")
            outcome = self._native.result()
            if not isinstance(outcome, RecoveryOutcome) or not outcome.positive:
                raise RuntimeError("startup_activation_outcome_unavailable")
            self._status = "ready"
            self._phase = "ready"
            self._ready_commits += 1
            self._latch.set()
        self._signal_services()

    async def wait_ready(self) -> None:
        if self._latch is None or asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("startup_latch_unbound")
        await self._latch.wait()
        self.require_ready()

    def stop(self) -> None:
        # Synchronous admission fence, before any shutdown await.
        with self._lock:
            self._stop.set()
            sweep = self._sweep_owner
        if sweep is not None:
            sweep.negative('startup_sweep_stopped')
        self._publications.close_admission()

    def assert_native_idle(self) -> None:
        self._publications.assert_idle()
        self._assert_binding_idle()
        with self._lock:
            if self._status == "recovering" and self._native is None and not self._stop.is_set():
                raise RuntimeError("startup_not_yet_quiescent")
            if self._native is not None and not self._native.done():
                raise RuntimeError("startup_native_still_owned")
            if self._sweep_owner is not None:
                raise RuntimeError("startup_sweep_native_still_owned")
            if any(not future.done() or future.cancelled() for future in self._dashboard_loads):
                raise RuntimeError("startup_dashboard_native_still_owned")

    def retain_sweep(self, owner: Any) -> None:
        self.require_native_owner()
        with self._lock:
            if (self._sweep_owner is not None or self._stop.is_set()
                    or self._first_cause is not None):
                raise RuntimeError("startup_duplicate_or_closed_sweep_owner")
            self._sweep_owner = owner

    def settle_sweep(self, owner: Any) -> None:
        # The body Future or tail seal alone cannot retire this owner. Context
        # reset and the sole actual final batch result also belong to it.
        with owner._lock:
            if (not owner._boundary_complete or not owner._sealed or owner._tails
                    or owner._humans
                    or owner.counts['effects_started'] != owner.counts['effects_settled']):
                raise RuntimeError("startup_sweep_boundary_incomplete")
        with self._lock:
            if self._sweep_owner is not owner:
                raise RuntimeError("startup_sweep_owner_changed")
            self._sweep_owner = None

    async def close(self) -> None:
        self.stop()
        cancellation = None
        if self._native is not None:
            try:
                await self.join()
            except asyncio.CancelledError as exc:
                cancellation = exc
            except BaseException as exc:
                self.fail("startup_native_failed", exc)
        coordinator = self._coordinator
        if coordinator is not None and coordinator is not asyncio.current_task():
            while not coordinator.done():
                try:
                    await asyncio.shield(coordinator)
                except asyncio.CancelledError as exc:
                    cancellation = cancellation or exc
                except BaseException as exc:
                    self.fail("startup_coordinator_failed", exc)
            if not coordinator.cancelled():
                coordinator.result()
        binding = self._binding
        with self._lock:
            waits = list(self._dashboard_loads)
        if binding is not None:
            waits.append(binding.completion)
        for native in waits:
            wrapped = asyncio.wrap_future(native)
            while True:
                try:
                    await asyncio.shield(wrapped)
                    break
                except asyncio.CancelledError as exc:
                    cancellation = cancellation or exc
                    if wrapped.done():
                        wrapped.result()
                        break
        self.assert_native_idle()
        self._executor.shutdown(wait=True, cancel_futures=False)
        with self._lock:
            self._closed = True
        if cancellation is not None:
            raise cancellation


def require_ready(owner: Any) -> None:
    """Direct production boundaries must have an explicitly bound owner."""
    gate = getattr(owner, "_startup_recovery", None)
    if not isinstance(gate, StartupRecovery):
        raise StartupUnavailable("startup_gate_unbound")
    try:
        gate.require_attachment(owner)
    except AttributeError as exc:
        raise StartupUnavailable("startup_attachment_changed") from exc
    gate.require_ready()


def _require_ordinary_ready(owner: Any) -> None:
    """Explicitly constructed standalone owners retain their offline contract.

    Production construction binds state BEFORE exposing the owner. Missing
    fields, a replaced gate or a detached original owner never select offline.
    No recovery publication object is accepted at this launch boundary.
    """
    try:
        state, gate, org = owner._startup_state, owner._startup_recovery, owner._startup_org
    except AttributeError as exc:
        raise StartupUnavailable("startup_gate_unbound") from exc
    if state is None:
        if gate is not None or org is not None:
            raise StartupUnavailable("startup_gate_unbound")
        return
    require_ready(owner)


def _bind_task_admission(owner: Any, state: Any, org: Any = None) -> None:
    """Bind an original native reference; never reset a bound production gate."""
    previous = owner._startup_state
    if owner._startup_org is not None and owner._startup_org is not org:
        raise StartupUnavailable("startup_attachment_changed")
    if previous is not None and previous is not state:
        # Existing ready-only runtime transfer moves freshly constructed,
        # unpublished references, never an already activated epoch.
        if previous._startup_recovery is not None or state._startup_recovery is None:
            raise StartupUnavailable("startup_attachment_changed")
        state._require_startup_ready()
    if owner._startup_recovery is not None and owner._startup_recovery is not state._startup_recovery:
        raise StartupUnavailable("startup_attachment_changed")
    owner._startup_state, owner._startup_org = state, org
    owner._startup_recovery = state._startup_recovery


class StartupAdmissionMiddleware:
    """Resolve the actual route BEFORE receive, dependencies or body parsing.

    Matching never runs an endpoint. The seven early identities still enter
    their original auth/OrgDep handlers. Neither query strings, slash redirects
    nor a path-only method match can confer permission.
    """

    def __init__(self, app: Any, *, route_app: Any, state: Any) -> None:
        from runtime.daemon.routes import auth, health, orgs, runtime, threads
        from starlette.exceptions import HTTPException
        self.app = app
        self.route_app = route_app
        self.state = state
        self._early = {
            ("/api/v1/health", health.health),
            ("/api/v1/auth/bootstrap", auth.bootstrap),
            ("/api/v1/runtime", runtime.get_runtime),
            ("/api/v1/orgs", orgs.list_orgs),
            ("/api/v1/orgs/{slug}/threads", threads.list_threads_endpoint),
            ("/api/v1/orgs/{slug}/threads/{thread_id}", threads.get_thread_endpoint),
            ("/api/v1/orgs/{slug}/threads/{thread_id}/messages", threads.list_thread_messages_endpoint),
        }
        ready = getattr(health, "health_ready", None)
        if ready is not None:
            self._early.add(("/api/v1/health/ready", ready))
        self._spa_fallback = route_app.exception_handlers.get(HTTPException)
        if (self._spa_fallback is not None
                and self._spa_fallback.__module__ != "runtime.daemon.routes.web_static"):
            self._spa_fallback = None

    def _allowed(self, scope: dict[str, Any]) -> bool:
        from starlette.routing import Match, Mount
        from starlette.staticfiles import StaticFiles
        from starlette.exceptions import HTTPException
        from runtime.daemon.routes.web_static import _is_spa_route
        method = scope["method"]
        partial = False
        for route in self.route_app.router.routes:
            match, _ = route.matches(scope)
            if match is Match.PARTIAL:
                partial = True
            if match is not Match.FULL:
                continue
            if isinstance(route, Mount):
                return (route.path == "/assets" and route.name == "assets"
                        and isinstance(route.app, StaticFiles) and method in ("GET", "HEAD"))
            endpoint = getattr(route, "endpoint", None)
            identity = (getattr(route, "path", None), endpoint)
            if method == "GET" and identity in self._early:
                return True
            # Only the registered real SPA root, never docs/OpenAPI/API GET.
            return (method == "GET" and identity[0] == "/"
                    and endpoint is not None
                    and endpoint.__module__ == "runtime.daemon.routes.web_static"
                    and endpoint.__name__ in ("_spa_root", "_placeholder_root"))
        path = scope.get("path", "")
        if (partial or method != "GET" or path == "/api" or path.startswith("/api/")
                or not _is_spa_route(path) or self._spa_fallback is None
                or self.route_app.exception_handlers.get(HTTPException) is not self._spa_fallback):
            return False
        # Router slash redirects happen before the SPA 404 handler. A redirect
        # to a held or method-only identity is not an early read exemption.
        if path != "/":
            redirect_scope = {**scope, "path": path.rstrip("/") if path.endswith("/") else path + "/"}
            for route in self.route_app.router.routes:
                match, _ = route.matches(redirect_scope)
                if match is not Match.NONE:
                    return False
        return True

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from starlette.responses import JSONResponse
        gate = getattr(self.state, "_startup_recovery", None)
        if not isinstance(gate, StartupRecovery) or self.route_app.state.daemon is not self.state:
            error = StartupUnavailable("startup_gate_unbound")
        else:
            snapshot = gate.snapshot()
            if snapshot["recovery_ready"] and not snapshot["stopping"]:
                await self.app(scope, receive, send)
                return
            if self._allowed(scope):
                await self.app(scope, receive, send)
                return
            code = "daemon_recovering" if snapshot["state"] == "recovering" and not snapshot["stopping"] else "daemon_recovery_failed"
            error = StartupUnavailable(code, snapshot)
        headers = {"Retry-After": "2"} if error.code == "daemon_recovering" else None
        await JSONResponse({"detail": error.detail}, status_code=503, headers=headers)(scope, receive, send)
