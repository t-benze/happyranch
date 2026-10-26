"""Process-wide state holder for the daemon."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from runtime.daemon.team_move_service import TeamMoveService
    from runtime.daemon.cleanup_diagnostics import _CleanupDiagnostics
    from runtime.daemon.thread_delivery_release_service import _ThreadDeliveryReleaseService
    from runtime.daemon.startup_recovery import StartupRecovery
    from runtime.daemon.startup_reads import StartupReads

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

    _maintenance_workers: set[asyncio.Future] = field(init=False, default_factory=set, repr=False)

    async def _await_maintenance_worker(self, worker):
        """Keep an original submitted maintenance worker visible through cancellation."""
        from runtime.orchestrator.producer_move import await_producer_worker
        self._maintenance_workers.add(worker)
        try:
            return await await_producer_worker(worker)
        finally:
            if worker.done() and not worker.cancelled():
                failure = worker.exception()
                if failure is None or not getattr(failure, "maintenance_native_unsettled", False):
                    self._maintenance_workers.remove(worker)

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

    def _notify_attached_returned(self, org: OrgState) -> None:
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
                rediscover_returned(coding)
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
        from runtime.daemon.startup_recovery import _coding_references
        for owner in _coding_references(org)[:4]:
            _bind_task_admission(owner, self, org)
        org._startup_reads = self._startup_reads
        from runtime.daemon.jobs_runner import bind_jobs_startup_references
        bind_jobs_startup_references(self, org)

    async def _drain_startup(self, org=None) -> None:
        # Recovery may require request-loop queue acknowledgements to end. Join
        # outside org/DB/publisher locks, before the first connection can close.
        if self._startup_recovery is not None:
            if org is None:
                await self._startup_recovery.close()
            else:
                self._startup_recovery.assert_native_idle()
        if self._startup_reads is not None:
            await self._startup_reads.drain(org)

    def _assert_startup_idle(self, org=None) -> None:
        if self._maintenance_workers:
            raise _OrgBusyError("maintenance_native_in_flight")
        if self._startup_recovery is not None:
            self._startup_recovery.assert_native_idle()
        if self._startup_reads is not None:
            self._startup_reads.assert_idle(org)

    def _revoke_thread_releases(self, org=None) -> None:
        if self._thread_release_service is not None:
            self._thread_release_service.revoke(org)
        else:
            for current in self.orgs.values() if org is None else (org,):
                current._revoke_thread_releases()

    async def _drain_thread_releases(self, org=None) -> None:
        if self._thread_release_service is not None:
            await self._thread_release_service.drain(org)
        for current in self.orgs.values() if org is None else (org,):
            current._assert_thread_releases_idle()

    def _capture_attachments(self) -> tuple:
        # Private two-phase detach identity, captured under orgs_lock. Paths
        # accompany object identity so in-place root/DB changes also refuse.
        return (self.orgs, self.runtime, self.runtime.root if self.runtime else None,
                self.host_supervisor, self._thread_release_service, tuple((slug, org, org.root, org.db, org.db.db_path, org.sessions, org._thread_release_attachment)
                                            for slug, org in self.orgs.items()))

    def _assert_attachments_unchanged(self, captured: tuple) -> None:
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
        for org in self.orgs.values():
            org._revoke_cleanup_diagnostics()

    async def _drain_cleanup_diagnostics(self, org: OrgState | None = None) -> None:
        if self._cleanup_diagnostics is not None:
            await self._cleanup_diagnostics.drain(org)
        for current in self.orgs.values() if org is None else (org,):
            current._assert_cleanup_diagnostics_idle()

    def _stop_runner_admission(self) -> None:
        # No await: fence both existing OrgState references and later add_org.
        if self._startup_recovery is not None:
            self._startup_recovery.stop()
        if self._startup_reads is not None:
            self._startup_reads.revoke()
        self._runner_shutdown = True
        self.queue.close_admission()
        for org in self.orgs.values():
            org._runner_admission_closed = True
        # Finite C revoke; incomplete acquisition raises BEFORE host shutdown.
        self._revoke_thread_releases()

    @classmethod
    def idle(cls, settings: Settings) -> "DaemonState":
        state = cls(runtime=None, settings=settings)
        state.metrics_store = MetricsStore(None)  # in-memory for idle state
        state.direct_connect_authority_store = DirectConnectAuthorityStore(None)
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

    @classmethod
    def from_runtime(cls, runtime: RuntimeDir, settings: Settings, *, move_diagnostics=None) -> "DaemonState":
        from runtime.orchestrator.team_move_startup import require_runtime_reader
        diagnostics = dict(move_diagnostics or {})
        diagnostics.update(require_runtime_reader(runtime))
        state = cls(runtime=runtime, settings=settings)
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
                continue
            try:
                org = OrgState.load(slug=slug, root=root, settings=settings)
            except (OrgConsistencyError, ValueError) as exc:
                # One broken org must not crash the daemon. Record the
                # error for GET /orgs and skip; the folder stays intact
                # on disk so the founder can fix teams.yaml and restart.
                state.broken_orgs[slug] = str(exc)
                logger.error("org %r failed consistency check: %s", slug, exc)
                continue
            # U2A: every whole-runtime construction path (normal startup and
            # live register/switch) initializes eligible selectors before the
            # resulting DaemonState is returned or published. Raw
            # ``OrgState.load`` retains its generation-1 recovery seam; this
            # containing lifecycle owner makes it launch-ready.
            from runtime.orchestrator.active_authority_policy import (
                is_eligible_policy_manager,
            )
            try:
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
            except Exception:
                org.close()
                raise
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
            state.orgs[slug] = org
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
        state.profile_coordinator.reconcile_startup()
        return state

    @property
    def is_idle(self) -> bool:
        return self.runtime is None

    def get_org(self, slug: str) -> OrgState:
        try:
            return self.orgs[slug]
        except KeyError as exc:
            raise KeyError(slug) from exc

    async def add_org(self, slug: str) -> OrgState:
        """Lazy-load an org's OrgState. Idempotent — returns the existing
        instance if the slug is already loaded.

        The orchestrator's queue and per-org session tracker are attached
        here so a freshly-added org is immediately runnable (the lifespan
        ``_attach_org_runtime_wiring`` only runs over orgs present at boot).
        """
        self._require_startup_ready()
        async with self.orgs_lock:
            self._require_startup_ready()
            if self._runner_shutdown:
                raise _OrgBusyError("daemon_shutting_down")
            if slug in self.orgs:
                return self.orgs[slug]
            assert self.runtime is not None
            root = self.runtime.orgs_dir / slug
            # OrgConsistencyError propagates — add_org is an explicit
            # action (init / unload+reload) and must fail loudly so the
            # founder sees the reason at the HTTP layer.
            try:
                if "team_move_" in self.broken_orgs.get(slug, ""):
                    from runtime.infrastructure.team_move_store import inspect_team_move_org
                    inspect_team_move_org(root, org_slug=slug, require_database=True)
                org = OrgState.load(slug=slug, root=root, settings=self.settings)
            except ValueError as exc:
                if "team_move_" in str(exc):
                    self.broken_orgs[slug] = str(exc)
                    if self.profile_coordinator is not None:
                        diagnostics = getattr(self.profile_coordinator, "_move_diagnostic_orgs", {})
                        diagnostics[slug] = root
                        self.profile_coordinator._move_diagnostic_orgs = diagnostics
                raise
            # U2A: a dynamically attached org must cross the same coordinated
            # selector-initialization boundary as daemon startup before it is
            # wired or made runnable. Every launch reader is deliberately
            # read-only and fails closed if this boundary did not complete.
            from runtime.orchestrator.active_authority_policy import (
                is_eligible_policy_manager,
            )
            try:
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
                            publisher="daemon-dynamic-add:selector-initialization",
                        )
            except Exception:
                org.close()
                raise
            try:
                # Bind the existing boot gate/read domain BEFORE publication
                # under profile leases; no partially runnable attachment.
                self._attach_startup_owner(org)
                org.orchestrator.attach_queue(self.queue)
                org.orchestrator.attach_sessions(org.sessions)
                org.orchestrator.attach_host_supervisor(self.host_supervisor)
                if self.profile_coordinator is not None:
                    org._profile_coordinator = self.profile_coordinator
                    # Hold every referenced profile lease while synchronizing
                    # and publishing this org, then insert it into the shared
                    # map before releasing those leases. A concurrent writer
                    # therefore completes first or captures the new org; there
                    # is no visible stale-ready attachment window.
                    with self.profile_coordinator.dynamic_org_attachment(org):
                        self.orgs[slug] = org
                else:
                    self.orgs[slug] = org
            except Exception:
                self.orgs.pop(slug, None)
                org.close()
                raise
            # New/dynamic attachments never inherit a boot diagnostic pass.
            self._attach_startup_owner(org)
            org._cleanup_diagnostic_unavailable = "dynamic_attachment_unsupported"
            self.broken_orgs.pop(slug, None)
            if self.profile_coordinator is not None:
                # The outside-lease load positively inspected this existing DB;
                # dynamic attachment revalidated its actual native move owner
                # under profile leases before synchronization/map insertion.
                # Failed attachment never reaches this diagnostic clearing.
                getattr(self.profile_coordinator, "_move_diagnostic_orgs", {}).pop(slug, None)
            # Wire the thread queue + main loop so run_step workers can
            # cross the async boundary via run_coroutine_threadsafe when
            # posting task-followup invocations.
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                org.orchestrator.attach_thread_queue(org.thread_queue, loop)
                self._notify_attached_returned(org)
            return org

    def _assert_can_remove_org(self, slug: str) -> OrgState | None:
        self._require_startup_ready()
        if self._runner_shutdown:
            raise _OrgBusyError("daemon_shutting_down")
        self.team_move_service.assert_no_owner(slug)
        current = self.orgs.get(slug)
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
        async with self.orgs_lock:
            self._require_startup_ready()
            # Local checks only: C may own the DB through a stalled COMMIT.
            # Reach finite revoke before any DB-backed task/move preflight.
            if self._runner_shutdown:
                raise _OrgBusyError("daemon_shutting_down")
            self.team_move_service.assert_no_owner(slug)
            current = self.orgs.get(slug)
            if current is not None:
                current._assert_runners_idle(thread_releases=False)
                if self._startup_recovery is not None:
                    self._startup_recovery.assert_native_idle()
            captured = self._capture_attachments()
            if current is not None:
                if self._startup_reads is not None:
                    self._startup_reads.revoke(current)
                self._revoke_thread_releases(current)
                current._revoke_cleanup_diagnostics()
        # Never await a native scan under orgs/root/move/terminal locks.
        if current is not None:
            await self._drain_startup(current)
            await self._drain_thread_releases(current)
            await self._drain_cleanup_diagnostics(current)
        async with self.orgs_lock:
            self._assert_attachments_unchanged(captured)
            current = self._assert_can_remove_org(slug)
            if current is not None:
                current.close()
                self.orgs.pop(slug)

    async def close_all(self) -> None:
        self._stop_runner_admission()
        captured = self._capture_attachments()
        await self._drain_startup()
        if self._startup_recovery is not None:
            self._startup_recovery.assert_consumers_idle()
        self._assert_attachments_unchanged(captured)
        await self._drain_thread_releases()
        self._assert_attachments_unchanged(captured)
        self._revoke_cleanup_diagnostics()
        await self._drain_cleanup_diagnostics()
        # Refuse native users before any service/org closure. The final
        # all-org preflight below renews this after the service/lock awaits.
        self.queue.assert_native_idle()
        for org in self.orgs.values():
            org._assert_runners_idle()
        await self.team_move_service.close()
        async with self.orgs_lock:
            self._assert_attachments_unchanged(captured)
            # Fail before any close if an unexpected caller still owns an org.
            # Lifespan joins the actual runners; cancellation is not that proof.
            self.queue.assert_native_idle()
            for org in self.orgs.values():
                self._assert_startup_idle(org)
                org._assert_runners_idle()
                org._assert_cleanup_diagnostics_idle()
            for org in self.orgs.values():
                org.close()
            self.orgs.clear()
        self._assert_startup_idle()
        if self.direct_connect_authority_store is not None:
            self.direct_connect_authority_store.close()
        self._shutdown_complete = True
