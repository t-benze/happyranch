"""Process-wide state holder for the daemon."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from runtime.daemon.team_move_service import TeamMoveService

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

    def _stop_runner_admission(self) -> None:
        # No await: fence both existing OrgState references and later add_org.
        self._runner_shutdown = True
        for org in self.orgs.values():
            org._runner_admission_closed = True

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
        async with self.orgs_lock:
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
            return org

    async def remove_org(self, slug: str) -> None:
        async with self.orgs_lock:
            self.team_move_service.assert_no_owner(slug)
            current = self.orgs.get(slug)
            if current is not None and current.db._team_move_coordinator.store.read() is not None:
                from runtime.infrastructure.team_move_store import TeamMoveError
                raise TeamMoveError("team_move_runtime_detach_held")
            if current is not None:
                # Renew the route's task check after acquiring orgs_lock. Keep
                # busy checks, admission fencing, close and detach synchronous
                # so a late runner either registers first or sees closed admission.
                in_flight = current.db.get_nonterminal_task_ids()
                if in_flight:
                    raise _OrgBusyError("active_tasks_in_flight", task_ids=in_flight)
                current.close()
                self.orgs.pop(slug)

    async def close_all(self) -> None:
        self._stop_runner_admission()
        await self.team_move_service.close()
        async with self.orgs_lock:
            # Fail before any close if an unexpected caller still owns an org.
            # Lifespan joins the actual runners; cancellation is not that proof.
            for org in self.orgs.values():
                org._assert_runners_idle()
            for org in self.orgs.values():
                org.close()
            self.orgs.clear()
        if self.direct_connect_authority_store is not None:
            self.direct_connect_authority_store.close()
