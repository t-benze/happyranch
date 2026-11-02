"""Process-wide state holder for the daemon."""
from __future__ import annotations

import asyncio
import logging
import threading
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
        # Strong children survive caller cancellation. Join the original tasks,
        # including repeated shutdown cancellation, before any DB/host close.
        tasks = tuple(self._attachment_tasks)
        joined = asyncio.gather(*tasks, return_exceptions=True)
        cancellation = None
        while not joined.done():
            try:
                await asyncio.shield(joined)
            except asyncio.CancelledError as exc:
                cancellation = cancellation or exc
        joined.result()
        self._assert_attachment_loads_idle()
        if cancellation is not None:
            raise cancellation

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
        # No await: fence both existing OrgState references and later add_org.
        with self._org_map_lock:
            self._runner_shutdown = True
            orgs = tuple(self.orgs.values())
        self.queue.close_admission()
        for org in orgs:
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
        with self._org_map_lock:
            try:
                return self.orgs[slug]
            except KeyError as exc:
                raise KeyError(slug) from exc

    async def add_org(self, slug: str) -> OrgState:
        """Own the entire native/loop attachment before the first await."""
        if self._runner_shutdown:
            raise _OrgBusyError("daemon_shutting_down")
        task = asyncio.create_task(self._add_org(slug), name="org-attachment:" + slug)
        self._attachment_tasks.add(task)
        def retired(done: asyncio.Task) -> None:
            self._attachment_tasks.discard(done)
            if not done.cancelled():
                done.exception()  # consume late errors even after disconnect
        task.add_done_callback(retired)
        cancellation = None
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as exc:
                cancellation = cancellation or exc
        result = task.result()
        if cancellation is not None:
            raise cancellation
        return result

    async def _attachment_native(self, owner: dict, callback: Callable[[], Any]) -> Any:
        """One joined native interval; retain uncertain submission/retirement."""
        ticket = object()
        work = {"owner": owner, "callback": callback, "returned": threading.Event()}
        context = copy_context()
        def run() -> None:
            try:
                work["result"] = context.run(callback)
            except BaseException as exc:
                work["error"] = exc
            finally:
                work["returned"].set()
        with self._org_map_lock:
            self._attachment_workers[ticket] = work
        # No cancellation or submission error can discard this original ticket.
        worker = asyncio.get_running_loop().run_in_executor(None, run)
        work["worker"] = worker
        cancellation = None
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError as exc:
                cancellation = cancellation or exc
        worker.result()
        if not work["returned"].is_set():
            raise _OrgBusyError("attachment_native_return_unknown")
        with self._org_map_lock:
            del self._attachment_workers[ticket]
        if "error" in work:
            raise work["error"]
        if cancellation is not None:
            raise cancellation
        return work.get("result")

    async def _add_org(self, slug: str) -> OrgState:
        async with self.orgs_lock:
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
                # Actual asyncio references/publication stay on their loop.
                org.orchestrator.attach_queue(self.queue)
                org.orchestrator.attach_sessions(org.sessions)
                org.orchestrator.attach_host_supervisor(self.host_supervisor)
                org._profile_coordinator = owner["profiles"]
                org._cleanup_diagnostic_unavailable = "dynamic_attachment_unsupported"
                loop = asyncio.get_running_loop()
                org.orchestrator.attach_thread_queue(org.thread_queue, loop, rediscover=False)
                await self._attachment_native(owner, lambda: self._publish_attachment(owner))
                # Reuse the original returned-notification owner, with its actual
                # raw queue handoff on this loop, before releasing native lifetime.
                from runtime.daemon.queue import _AttachmentNotificationQueue
                queue = _AttachmentNotificationQueue(self.queue, loop, org.thread_queue)
                owner["notifications"] = queue
                await self._attachment_native(owner, lambda: org.orchestrator._rediscover_returned(queue=queue))
                await queue.join_puts()
                self.broken_orgs.pop(slug, None)
                if owner["profiles"] is not None:
                    getattr(owner["profiles"], "_move_diagnostic_orgs", {}).pop(slug, None)
                return org
            except BaseException as exc:
                primary = exc
                owner["primary"] = exc
                if isinstance(exc, ValueError) and "team_move_" in str(exc):
                    self.broken_orgs[slug] = str(exc)
                    if owner["profiles"] is not None:
                        diagnostics = getattr(owner["profiles"], "_move_diagnostic_orgs", {})
                        diagnostics[slug] = owner["root"]
                        owner["profiles"]._move_diagnostic_orgs = diagnostics
                raise
            finally:
                # A successful Future is insufficient if native retirement is
                # unknown. The ticket and actual loaded handle stay reachable.
                with self._org_map_lock:
                    uncertain = any(w["owner"] is owner for w in self._attachment_workers.values())
                notifications = owner.get("notifications")
                if notifications is not None and notifications.has_unretired_puts():
                    uncertain = True
                if owner["org"] is None and owner.get("db_construction_started") and not owner.get("load_db_closed"):
                    uncertain = True
                if not uncertain:
                    org = owner["org"]
                    if owner["native_token"] is not None:
                        org._release_native_task(owner["native_token"])
                        owner["native_token"] = None
                    safe = True
                    if org is not None and not owner["published"]:
                        try:
                            await self._attachment_native(owner, org.close)
                        except BaseException as cleanup:
                            safe = False
                            owner["close_error"] = cleanup
                            if primary is None:
                                raise
                            primary.add_note("unpublished attachment close failed: " + type(cleanup).__name__)
                    if safe:
                        with self._org_map_lock:
                            del self._attachment_owners[token]

    def _load_attachment(self, owner: dict) -> None:
        slug, root = owner["slug"], owner["root"]
        if "team_move_" in self.broken_orgs.get(slug, ""):
            from runtime.infrastructure.team_move_store import inspect_team_move_org
            inspect_team_move_org(root, org_slug=slug, require_database=True)
        org = OrgState.load(slug=slug, root=root, settings=self.settings, attachment_owner=owner)
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
        if self._runner_shutdown:
            raise _OrgBusyError("daemon_shutting_down")
        self.team_move_service.assert_no_owner(slug)
        current = self.orgs_snapshot().get(slug)
        if current is not None:
            current._assert_runners_idle()
            if current.db._team_move_coordinator.store.read() is not None:
                from runtime.infrastructure.team_move_store import TeamMoveError
                raise TeamMoveError("team_move_runtime_detach_held")
            in_flight = current.db.get_nonterminal_task_ids()
            if in_flight:
                raise _OrgBusyError("active_tasks_in_flight", task_ids=in_flight)
        return current

    async def remove_org(self, slug: str) -> None:
        async with self.orgs_lock:
            # Local checks only: C may own the DB through a stalled COMMIT.
            # Reach finite revoke before any DB-backed task/move preflight.
            if self._runner_shutdown:
                raise _OrgBusyError("daemon_shutting_down")
            self.team_move_service.assert_no_owner(slug)
            current = self.orgs_snapshot().get(slug)
            if current is not None:
                current._assert_runners_idle(thread_releases=False)
            captured = self._capture_attachments()
            if current is not None:
                self._revoke_thread_releases(current)
                current._revoke_cleanup_diagnostics()
        # Never await a native scan under orgs/root/move/terminal locks.
        if current is not None:
            await self._drain_thread_releases(current)
            await self._drain_cleanup_diagnostics(current)
        async with self.orgs_lock:
            self._assert_attachments_unchanged(captured)
            current = self._assert_can_remove_org(slug)
            if current is not None:
                self._begin_attachment_close((current,))
                current.close()
                with self._org_map_lock:
                    self.orgs.pop(slug)
                    self._retiring_orgs.discard(id(current))

    async def close_all(self) -> None:
        self._stop_runner_admission()
        await self._drain_attachments()
        captured = self._capture_attachments()
        await self._drain_thread_releases()
        self._assert_attachments_unchanged(captured)
        self._revoke_cleanup_diagnostics()
        await self._drain_cleanup_diagnostics()
        # Refuse native users before any service/org closure. The final
        # all-org preflight below renews this after the service/lock awaits.
        self.queue.assert_native_idle()
        for org in self.orgs_snapshot().values():
            org._assert_runners_idle()
        await self.team_move_service.close()
        async with self.orgs_lock:
            self._assert_attachments_unchanged(captured)
            # Fail before any close if an unexpected caller still owns an org.
            # Lifespan joins the actual runners; cancellation is not that proof.
            self.queue.assert_native_idle()
            for org in self.orgs_snapshot().values():
                org._assert_runners_idle()
                org._assert_cleanup_diagnostics_idle()
            orgs = tuple(self.orgs_snapshot().values())
            self._begin_attachment_close(orgs)
            for org in orgs:
                org.close()
            with self._org_map_lock:
                self.orgs.clear()
                self._retiring_orgs.difference_update(id(org) for org in orgs)
        if self.direct_connect_authority_store is not None:
            self.direct_connect_authority_store.close()
        self._shutdown_complete = True
