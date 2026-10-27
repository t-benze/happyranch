"""FastAPI app factory."""
from __future__ import annotations

import time as _time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncio
    from collections.abc import AsyncIterator
    from runtime.daemon.org_state import OrgState

if TYPE_CHECKING:
    from runtime.daemon.startup_recovery import RecoveryOutcome, StartupRecovery

from fastapi import FastAPI, Request

from runtime.config import settings
from runtime.daemon.dispatcher import Dispatcher
from runtime.daemon.routes import (
    adapters,
    agents,
    artifacts,
    audit,
    authority_policy,
    auth,
    custom_skills,
    dashboard,
    direct_connect,
    direct_connect_commit,
    dreams,
    executor_binaries,
    executors,
    health,
    identities,
    jobs,
    kb,
    metrics,
    orgs,
    portability,
    runtime,
    schedules,
    skills,
    tasks,
    teams,
    team_moves,
    threads,
    tokens,
    usage,
    workflow_templates,
    workflow_release_templates,
    workflow_cutover,
    workflow_activations,
    workflow_submissions,
    workflow_reviews,
    workflow_revisions,
    work_hours,
    schedules,
)
from runtime.daemon.routes import settings as settings_routes
from runtime.daemon.metrics import error_label, route_template_label
from runtime.daemon.state import DaemonState
from runtime.orchestrator._paths import OrgPaths


def _attach_org_runtime_wiring(state: DaemonState) -> None:
    """Wire each loaded org's Orchestrator to the global queue + per-org
    sessions + the daemon-wide host supervisor.

    The Orchestrator is built inside ``OrgState.load`` so it knows its slug,
    but its ``_queue``/``_sessions``/``_host_supervisor`` references are
    populated separately so unit tests that build an OrgState without a
    daemon can still inspect the orchestrator before the queue exists.
    """
    from runtime.daemon.startup_recovery import _bind_task_admission
    _bind_task_admission(state.queue, state)
    if state.host_supervisor is not None:
        _bind_task_admission(state.host_supervisor, state)
    for org in state.orgs.values():
        state._attach_startup_owner(org)
        org.orchestrator.attach_queue(state.queue)
        org.orchestrator.attach_sessions(org.sessions)
        org.orchestrator.attach_host_supervisor(state.host_supervisor)


def ensure_workers_started(state: DaemonState) -> None:
    """Start the worker pool if a runtime is active and workers aren't running.

    Idempotent. Each org's Orchestrator is built once when the org is loaded
    (see OrgState.load); the Dispatcher routes (slug, task_id) tuples to the
    right one.
    """
    from runtime.daemon.startup_recovery import require_ready
    require_ready(state)
    if state.is_idle:
        return
    _attach_org_runtime_wiring(state)
    if state.queue.is_running():
        return
    dispatcher = Dispatcher(state)
    state.queue.start_workers(dispatcher, n=settings.queue_workers)


def _attach_thread_queue_wiring(state: DaemonState, loop: asyncio.AbstractEventLoop) -> None:
    """Wire each org's ThreadQueue + the daemon main loop into its Orchestrator.

    Called after the event loop is running (inside the lifespan) so that
    ``run_step`` workers can cross the thread boundary via
    ``asyncio.run_coroutine_threadsafe`` when enqueuing task-followup
    invocations. Mirrors ``_attach_org_runtime_wiring``'s decoupled pattern.
    """
    for org in state.orgs.values():
        org.orchestrator.attach_thread_queue(org.thread_queue, loop)


def _wire_then_start_workers(state: DaemonState, loop: asyncio.AbstractEventLoop) -> None:
    """Wire ThreadQueue + main loop into each Orchestrator, then start workers.

    Ordering is critical: the thread queue and main loop MUST be wired before
    task workers start.  Workers that execute before wiring will find
    ``orch._thread_queue`` and ``orch._main_loop`` as ``None``, producing
    ``enqueue_unavailable`` and stranding any TASK_FOLLOWUP invocation as
    permanently pending.

    Dynamic runtime callers use this ready-only helper. Boot P4 owns its
    reference binding and returned notifications on the native operation;
    boot P8 prepares workers separately behind the unopened latch.
    """
    from runtime.daemon.startup_recovery import require_ready
    require_ready(state)
    _attach_org_runtime_wiring(state)
    _attach_thread_queue_wiring(state, loop)
    for org in state.orgs.values():
        state._notify_attached_returned(org)
    ensure_workers_started(state)


def recover_startup_compatibility(state: DaemonState, *, recovery: StartupRecovery | None = None) -> RecoveryOutcome:
    """Original P5 body, called once by the native lifecycle phase.

    Native completion means original work/observations returned, not a proof
    of repair or an empty registry. Existing advisory isolation remains local.
    """
    import logging
    from datetime import datetime, timezone
    from runtime.daemon.startup_recovery import RecoveryOutcome

    held = False

    def check(org=None) -> None:
        if recovery is not None:
            recovery.require_compatibility_owner(org)

    def count(event: str, value: int = 1) -> None:
        if recovery is not None:
            recovery.count_phase("compatibility", event, value)

    check()
    # Recover any jobs left in 'running' state from a previous daemon process.
    _now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _logger = logging.getLogger("happyranch.daemon")
    from runtime.daemon.jobs_runner import migrate_artifacts_layout, migrate_filesystem_layout
    for org in state.orgs.values():
        check(org)
        # Rename <org_root>/scripts/ → jobs/ (and SR-* → JOB-*) BEFORE the
        # recovery scan reads any stdout_path/stderr_path. The DB-side
        # rename already happened in Database init; this realigns disk.
        migrate_filesystem_layout(org.root)
        # 2026-06-02 rename: rehome on-disk assets/ → artifacts/ and
        # per-workspace artifacts/ → output/ before any mkdir.
        check(org)
        migrate_artifacts_layout(org.root)
        check(org)
        OrgPaths(org.root).artifacts_dir.mkdir(exist_ok=True)
        count("layouts_completed")
        # THR-095: one-shot reconcile agent.yaml executor/repos/model → .md
        check(org)
        org.naming_readiness = "unavailable"
        migration_results = None
        migration_completed = False
        primary = None
        try:
            from runtime.daemon.agent_config import migrate_agent_yaml_to_frontmatter
            migration_results = migrate_agent_yaml_to_frontmatter(
                OrgPaths(root=org.root),
                workflow_authority=org.workflow_authority,
            )
            migration_completed = True
            if migration_results:
                changed = {k: v for k, v in migration_results.items() if v != "unchanged"}
                if changed:
                    _logger.info(
                        "THR-095 agent.yaml->frontmatter migration for org %s: %s",
                        org.slug, changed,
                    )
        except Exception as exc:
            primary = exc
            held = True
            count("migration_advisories")
            _logger.warning("THR-095 migration error for org %s: %s", org.slug, exc)
        except BaseException as exc:
            primary = exc
            raise
        finally:
            from runtime.identities.registry import refresh_names
            # The compatibility batch/profile reconciliation has released its
            # leases; startup has not exposed any route writers yet.
            try:
                check(org)
                with org.workflow_authority.writer_interval(publisher="identity_names_compatibility") as interval:
                    names_ready = refresh_names(org, writer=interval.native_disposition)
            except BaseException as secondary:
                if primary is not None:
                    if recovery is not None:
                        recovery.fail("startup_compatibility_failed", primary)
                        recovery.fail("startup_compatibility_finally_failed", secondary)
                    primary.add_note("identity_names_compatibility finally also failed: " + type(secondary).__name__)
                    raise primary from secondary
                raise
        check(org)
        if recovery is not None:
            if migration_completed and (type(migration_results) is not dict
                    or any(type(k) is not str or type(v) is not str for k, v in migration_results.items())):
                raise RuntimeError("startup_yaml_migration_outcome_unknown")
            if type(names_ready) is not bool:
                raise RuntimeError("startup_naming_outcome_unknown")
            if not names_ready:
                held = True
                count("naming_unavailable")
            if migration_results and any(v.startswith("error:") for v in migration_results.values()):
                held = True
                count("migration_advisories")
        # THR-106: one-shot skill-id rename hr:review -> hr:reflection in the
        # persisted org/config.yaml skills eligibility section (allow + deny,
        # org/team/agent scope). Sentinel-gated (.hr_review_renamed).
        check(org)
        rename_outcome = None
        rename_completed = False
        try:
            from runtime.orchestrator.org_config import migrate_hr_review_skill_id
            rename_outcome = migrate_hr_review_skill_id(OrgPaths(root=org.root))
            rename_completed = True
            if rename_outcome != "skipped (already migrated)":
                _logger.info(
                    "THR-106 hr:review->hr:reflection migration for org %s: %s",
                    org.slug, rename_outcome,
                )
        except Exception as exc:
            held = True
            count("skill_advisories")
            _logger.warning(
                "THR-106 skill-id rename migration error for org %s: %s",
                org.slug, exc,
            )
        check(org)
        if recovery is not None and rename_completed and type(rename_outcome) is not str:
            raise RuntimeError("startup_skill_rename_outcome_unknown")
        unknown_jobs = org.db.recover_orphaned_running_jobs(now_iso=_now_iso)
        if recovery is not None and (type(unknown_jobs) is not list
                or any(type(job) is not str or not job for job in unknown_jobs)):
            raise RuntimeError("startup_orphan_observation_outcome_unknown")
        count("unknown_jobs", len(unknown_jobs))
        if unknown_jobs:
            held = True
            _logger.warning(
                "job_liveness_unknown: retained %d running jobs in org %s: %s; "
                "restart is not attributable orphan/cleanup proof",
                len(unknown_jobs), org.slug, unknown_jobs,
            )

        check(org)
        count("orgs_observed")

    # THR-195: observation (read-only) of stale never-started pending jobs.
    # Registry-wide: scans EVERY org root discovered via
    # ``RuntimeDir.iter_org_roots`` — including a current DB-bearing org root
    # whose OrgState.load failed (``broken_orgs``), which ``state.orgs`` omits.
    # Read-only connections only: never creates/migrates/writes a store (the
    # founder contract protects the durable source ``.db``/``-wal`` bytes;
    # the WAL-index ``-shm`` may be created/modified/removed by read-side WAL
    # access — permitted). Startup-safe seam (founder ruling TASK-5544): a
    # per-org observation failure is logged with org/root/error context and
    # isolated, so it cannot abort daemon startup or suppress other org roots.
    # Surfaces a recurrence of the historical submission-to-dispatch
    # orphan; deliberately NOT a reaper — no automatic mutation.
    if state.runtime is not None:
        from runtime.daemon.stale_pending_jobs import scan_all_org_stale_pending
        check()
        class CheckedRegistry:
            # Only the original iterator; no second discovery or stored IDs.
            # Admission errors escape the scanner's per-org isolation, which
            # catches only the actual original observation body after yield.
            def iter_org_roots(self):
                for item in state.runtime.iter_org_roots():
                    check()
                    yield item
                check()

        observations = scan_all_org_stale_pending(
            state.runtime if recovery is None else CheckedRegistry())
        count("pending_scans")
        # This original API isolates/logs per-org failures as empty lists. It
        # supplies no complete-success/empty-census proof. Retain its advisory
        # posture instead of reconstructing discarded outcomes with a new scan.
        held = True
        for slug, stale_pending in observations.items():
            check()
            count("pending_orgs_reported")
            count("pending_rows_reported", len(stale_pending))
            if stale_pending:
                _logger.warning(
                    "stale never-started pending jobs in org %s: %s",
                    slug,
                    [
                        f"{r['id']} (task {r['task_id']}, created {r['created_at']})"
                        for r in stale_pending
                    ],
                )

    check()
    return RecoveryOutcome("local_hold" if held else "settled", "startup_compatibility_observed")


def recover_startup_dreams(state: DaemonState, *, recovery: StartupRecovery | None = None) -> RecoveryOutcome:
    """Original all-org dream recovery, without starting a consumer."""
    from runtime.daemon.dream_scheduler import recover_running_dreams
    from runtime.daemon.startup_recovery import RecoveryOutcome
    for org in state.orgs.values():
        if recovery is not None:
            recovery.require_producers_owner(org)
        changed = recover_running_dreams(org)
        if recovery is not None:
            if type(changed) is not int or changed < 0:
                raise RuntimeError("startup_dream_recovery_outcome_unknown")
            recovery.count_phase("producers", "dreams_recovered", changed)
            recovery.count_phase("producers", "dream_orgs")
            recovery.require_producers_owner(org)
    return RecoveryOutcome("settled", "startup_dream_recovery_observed")


def recover_startup_wakes(state: DaemonState, *, recovery: StartupRecovery | None = None) -> RecoveryOutcome:
    """Original all-org wake recovery; native-preserved rows stay untouched."""
    from runtime.daemon.startup_recovery import RecoveryOutcome
    for org in state.orgs.values():
        if recovery is not None:
            recovery.require_producers_owner(org)
        changed = org.db.work_hours.recover_running()
        if recovery is not None:
            if type(changed) is not int or changed < 0:
                raise RuntimeError("startup_wake_recovery_outcome_unknown")
            recovery.count_phase("producers", "wakes_recovered", changed)
            recovery.count_phase("producers", "wake_orgs")
            recovery.require_producers_owner(org)
    return RecoveryOutcome("settled", "startup_wake_recovery_observed")


def recover_startup_schedules(state: DaemonState, *, recovery: StartupRecovery | None = None) -> RecoveryOutcome:
    """Original firing recovery and exact schedule_failed audit publication."""
    from runtime.daemon.startup_recovery import RecoveryOutcome
    for org in state.orgs.values():
        if recovery is not None:
            recovery.require_producers_owner(org)
        recovered = org.db.schedules.recover_firing()
        if recovery is not None and type(recovered) is not list:
            raise RuntimeError("startup_schedule_recovery_outcome_unknown")
        for item in recovered:
            if recovery is not None:
                if (type(item) is not tuple or len(item) != 2
                        or any(type(value) is not str or not value for value in item)):
                    raise RuntimeError("startup_schedule_recovery_member_unknown")
                recovery.count_phase("producers", "schedules_recovered")
                recovery.require_producers_owner(org)
            schedule_id, agent_name = item
            audit_id = org.db.insert_audit_log(
                task_id=schedule_id,
                agent=agent_name,
                action="schedule_failed",
                payload={"reason": "daemon_restart"},
            )
            if recovery is not None:
                if type(audit_id) is not int or audit_id <= 0:
                    raise RuntimeError("startup_schedule_audit_outcome_unknown")
                recovery.count_phase("producers", "schedule_audits")
        if recovery is not None:
            recovery.count_phase("producers", "schedule_orgs")
            recovery.require_producers_owner(org)
    return RecoveryOutcome("settled", "startup_schedule_recovery_observed")


def _prepare_startup_consumers(state: DaemonState, recovery: StartupRecovery) -> None:
    """Prepare the complete original suffix behind ONE unopened latch."""
    from runtime.daemon.thread_queue import thread_worker_loop
    from runtime.daemon.thread_breaker_scheduler import thread_breaker_scheduler_loop
    from runtime.daemon.dream_queue import dream_worker_loop
    from runtime.daemon.dream_scheduler import dream_scheduler_loop
    from runtime.daemon.wake_queue import wake_worker_loop
    from runtime.daemon.work_hours_scheduler import work_hours_scheduler_loop
    from runtime.daemon.schedule_queue import schedule_worker_loop
    from runtime.daemon.schedule_scheduler import schedule_scheduler_loop
    from runtime.daemon.zombie_reaper import zombie_reaper_loop
    from runtime.daemon.disk_sweeper import disk_sweeper_loop
    from runtime.daemon.exchange_reaper import exchange_reaper_loop
    from runtime.daemon.direct_connect_projection_sweep import direct_connect_projection_sweep_loop
    from runtime.daemon.workspace_cleanup_scheduler import workspace_cleanup_scheduler_loop
    if not state.is_idle:
        state.queue.start_workers(Dispatcher(state), n=settings.queue_workers, recovery=recovery)
    for name, worker, count in (("thread-worker", thread_worker_loop, 4),
                                ("dream-worker", dream_worker_loop, 1),
                                ("wake-worker", wake_worker_loop, 1),
                                ("schedule-worker", schedule_worker_loop, 1)):
        for _ in range(count):
            recovery.prepare_consumer(name, lambda worker=worker: worker(state, state.settings))
    for scheduler in (thread_breaker_scheduler_loop, dream_scheduler_loop,
                      work_hours_scheduler_loop, schedule_scheduler_loop,
                      zombie_reaper_loop, disk_sweeper_loop, exchange_reaper_loop,
                      direct_connect_projection_sweep_loop, workspace_cleanup_scheduler_loop):
        recovery.prepare_consumer(scheduler.__name__, lambda scheduler=scheduler: scheduler(state))

    async def dashboard(org: OrgState) -> None:
        import asyncio
        from runtime.infrastructure.kb_store import KBStore
        manager = org.dashboard_projection
        persisted = await recovery.load_dashboard(manager)
        recovery.require_ready()
        if state.orgs.get(org.slug) is not org:
            raise RuntimeError("startup_dashboard_attachment_changed")
        if persisted is not None:
            manager._projection = persisted
            import logging
            logging.getLogger("happyranch.daemon").info(
                "dashboard projection loaded from disk for org %s (generated_at=%s)",
                org.slug, persisted.generated_at.isoformat())
        task = manager.start_scheduler(db=org.db, kb_store=KBStore(org.root / "kb"),
            teams=org.teams, loop=asyncio.get_running_loop(), initial_warm=True)
        # The actual original manager also retains this task for cancel/reap.
        await task

    for org in state.orgs.values():
        recovery.prepare_consumer("dashboard-" + org.slug, lambda org=org: dashboard(org))
    recovery.prepared()


async def _coordinate_startup(state: DaemonState, recovery: StartupRecovery) -> None:
    import asyncio
    import logging
    try:
        outcome = await recovery.join()
        if not outcome.positive:
            raise RuntimeError("startup_native_outcome_unavailable")
        _prepare_startup_consumers(state, recovery)
        # No await separates final checks, ready commit and latch release.
        recovery.activate()
    except BaseException as exc:
        recovery.fail("startup_coordinator_failed", exc)
        if isinstance(exc, asyncio.CancelledError):
            recovery.stop()
            try:
                await recovery.join()
            except BaseException as secondary:
                if secondary is not exc:
                    recovery.fail("startup_coordinator_join_failed", secondary)
        try:
            logging.getLogger(__name__).error("Startup recovery failed; admission remains closed", exc_info=exc)
        except BaseException as secondary:
            recovery.fail("startup_failure_observation_failed", secondary)


async def _shutdown_lifespan(
    state: DaemonState, *, revocation_error: BaseException | None,
    first_error: list[BaseException],
) -> None:
    import asyncio
    import logging
    recovery = state._startup_recovery
    primary = revocation_error
    traceback = None if primary is None else primary.__traceback__

    def remember(code: str, exc: BaseException) -> None:
        nonlocal primary, traceback
        if primary is None:
            primary, traceback = exc, exc.__traceback__
        if not first_error:
            first_error.append(exc)  # SAME Task's first error, visible before later drains.
        recovery.fail(code, exc)

    async def drain(operation, code: str) -> bool:
        try:
            await operation
            return True
        except BaseException as exc:
            remember(code, exc)
            return False

    # Known-ended exceptional outcomes cannot abandon independent owed drains.
    # These run while the REAL loop, host and DB resources still exist.
    await drain(state._drain_startup(), "startup_drain_incomplete")
    releases_closed = await drain(state._drain_thread_releases(), "startup_release_drain_incomplete")
    moves_closed = await drain(state.team_move_service.close(), "startup_move_drain_incomplete")
    prerequisites = revocation_error is None and releases_closed and moves_closed
    try:
        state._assert_startup_idle()
        if not recovery._closed:
            raise RuntimeError("startup_executor_close_incomplete")
    except BaseException as exc:
        prerequisites = False
        remember("startup_shutdown_owner_unsettled", exc)
    if prerequisites and state.host_supervisor is not None:
        try:
            state.host_supervisor.shutdown()
        except BaseException as exc:
            prerequisites = False
            remember("startup_host_shutdown_incomplete", exc)
    if prerequisites:
        for org in state.orgs.values():
            try:
                org.dashboard_projection.cancel_scheduler()
            except BaseException as exc:
                remember("startup_dashboard_cancel_incomplete", exc)
        handles = set(recovery._consumers)
        for org in state.orgs.values():
            handles.update(org._runner_tasks)
        for task in handles:
            task.cancel()
        # Every partial/dormant handle is retained and positively joined.
        outcomes = await asyncio.gather(*handles, return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, BaseException) and not isinstance(outcome, asyncio.CancelledError):
                remember("startup_consumer_failed", outcome)
                try:
                    logging.getLogger(__name__).error("Startup consumer ended with error: %r", outcome)
                except BaseException as exc:
                    remember("startup_consumer_observation_failed", exc)
        for org in state.orgs.values():
            reaped = await drain(org.dashboard_projection.reap_scheduler(), "startup_dashboard_reap_incomplete")
            prerequisites = prerequisites and reaped
        try:
            recovery.assert_consumers_idle()
            recovery.assert_native_idle()
        except BaseException as exc:
            prerequisites = False
            remember("startup_consumer_join_incomplete", exc)
        queue_closed = await drain(state.queue.stop(), "startup_queue_drain_incomplete")
        try:
            state.queue.assert_native_idle()
        except BaseException as exc:
            queue_closed = False
            remember("startup_queue_owner_unsettled", exc)
        from runtime.daemon.jobs_runner import terminate_all_inflight
        jobs_closed = await drain(terminate_all_inflight(grace_seconds=5), "startup_jobs_drain_incomplete")
        prerequisites = prerequisites and queue_closed and jobs_closed
    diagnostics_closed = await drain(state._drain_cleanup_diagnostics(), "startup_diagnostics_drain_incomplete")
    if prerequisites and diagnostics_closed:
        # close_all renews actual attachment, service, consumer, queue, reader,
        # diagnostic and org/runner fences. No ended error bypasses those checks.
        await drain(state.close_all(), "startup_database_close_incomplete")
    if primary is not None:
        raise primary.with_traceback(traceback)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    import asyncio
    state: DaemonState = app.state.daemon
    recovery = state._startup_recovery
    primary = None
    try:
        from runtime.daemon.cleanup_diagnostics import _CleanupDiagnostics
        if state._cleanup_diagnostics is None:
            try:
                state._cleanup_diagnostics = _CleanupDiagnostics(state, app)
            except Exception:
                pass  # Original optional diagnostic isolation.
        from runtime.daemon.thread_delivery_release_service import _ThreadDeliveryReleaseService
        if state._thread_release_service is None:
            state._thread_release_service = _ThreadDeliveryReleaseService(state, app)
        _attach_org_runtime_wiring(state)
        recovery.bind(asyncio.get_running_loop())
        from runtime.daemon.__main__ import recover_startup_native
        recovery.submit(recover_startup_native)
        coroutine = _coordinate_startup(state, recovery)
        try:
            recovery._coordinator = asyncio.create_task(coroutine, name="startup-recovery-coordinator")
        except BaseException:
            coroutine.close()
            raise
        # Serving is independent of recovery; only the actual Server adapter
        # supplies listener authority. No cold work or consumers are awaited.
        yield
    except BaseException as exc:
        primary = exc
        recovery.fail("startup_lifespan_failed", exc)
        raise
    finally:
        # Synchronous closure before FIRST await, also on partial startup.
        # A failed revoke remains a destructive-closure barrier; independent
        # retained startup/read/service drains are still owed.
        revocation_error = None
        for revoke in (state._stop_runner_admission, state._revoke_cleanup_diagnostics):
            try:
                revoke()
            except BaseException as secondary:
                recovery.fail("startup_revoke_incomplete", secondary)
                if revocation_error is None:
                    revocation_error = secondary
                if primary is not None:
                    primary.add_note("startup revoke also failed: " + type(secondary).__name__)
        # One bounded first-error slot shared only by this actual shutdown Task
        # and its waiter; an earlier failure can be retained before Task exit.
        shutdown_error = [] if revocation_error is None else [revocation_error]
        coroutine = _shutdown_lifespan(state, revocation_error=revocation_error,
                                      first_error=shutdown_error)
        try:
            shutdown = asyncio.create_task(coroutine, name="startup-shutdown")
        except BaseException as secondary:
            coroutine.close()
            recovery.fail("startup_shutdown_submission_failed", secondary)
            if primary is not None:
                raise primary from secondary
            raise
        from runtime.daemon.startup_recovery import _await_startup_owned

        def cancelled(cause: BaseException) -> BaseException:
            earlier = primary if primary is not None else (shutdown_error[0] if shutdown_error else cause)
            recovery.fail("startup_shutdown_cancelled" if isinstance(earlier, asyncio.CancelledError)
                          else "startup_shutdown_incomplete", earlier)
            return earlier

        try:
            await _await_startup_owned(shutdown,
                lambda exc: recovery.fail("startup_shutdown_incomplete", exc), on_cancel=cancelled)
        except BaseException as secondary:
            if primary is None:
                raise
            recovery.fail("startup_shutdown_incomplete", secondary)
            if secondary is not primary:
                primary.add_note("startup shutdown also failed: " + type(secondary).__name__)


async def metrics_timing_middleware(request: Request, call_next):
    """HTTP timing middleware (THR-066 remediation, Slice 1).

    Measures elapsed time around ``call_next``, then labels the latency by
    the matched FastAPI route template (resolved AFTER routing, prefixed with
    the request method) — never the literal ``request.url.path``.  A request
    with no matched template records ``METHOD __unmatched__``; a request
    whose handler raises records ``METHOD __error__`` (elapsed time is still
    recorded and the original exception is re-raised).
    """
    t0 = _time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        elapsed = _time.monotonic() - t0
        request.app.state.daemon.metrics_registry.record_http_latency(
            error_label(request.method), elapsed
        )
        raise
    elapsed = _time.monotonic() - t0
    route_path = getattr(request.scope.get("route"), "path", None)
    request.app.state.daemon.metrics_registry.record_http_latency(
        route_template_label(request.method, route_path), elapsed
    )
    return response


def create_app(state: DaemonState) -> FastAPI:
    app = FastAPI(title="HappyRanch Daemon", version="0.2.0", lifespan=_lifespan)
    app.state.daemon = state
    state._bind_startup_owner(app)
    from runtime.orchestrator.team_move import DeferredTeamMove
    from runtime.infrastructure.team_move_store import TeamMoveError
    from fastapi.responses import JSONResponse

    @app.exception_handler(TeamMoveError)
    @app.exception_handler(DeferredTeamMove)
    async def team_move_task_conflict(request: Request, exc: DeferredTeamMove | TeamMoveError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": {
            "code": exc.code, "operation_id": getattr(exc, "operation_id", None),
            "required_action": "Resolve the owning team-move hold or named admission owner before retrying",
        }})
    from runtime.daemon.startup_recovery import StartupUnavailable, StartupAdmissionMiddleware, StartupHTTPReferences

    @app.exception_handler(StartupUnavailable)
    async def startup_unavailable(request: Request, exc: StartupUnavailable) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": exc.detail},
                            headers={"Retry-After": "2"} if exc.code == "daemon_recovering" else None)

    # Wire metrics registry into the run_step worker queue for loop-tick recording.
    state.queue._metrics_registry = state.metrics_registry

    app.middleware("http")(metrics_timing_middleware)

    app.include_router(health.router, prefix="/api/v1")
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(metrics.router, prefix="/api/v1")
    app.include_router(runtime.router, prefix="/api/v1")
    app.include_router(orgs.router, prefix="/api/v1")
    app.include_router(tasks.router, prefix="/api/v1/orgs/{slug}")
    app.include_router(agents.router, prefix="/api/v1/orgs/{slug}")
    app.include_router(identities.router, prefix="/api/v1/orgs/{slug}", tags=["identities"])
    app.include_router(authority_policy.router, prefix="/api/v1/orgs/{slug}", tags=["authority-policy"])
    app.include_router(teams.router, prefix="/api/v1/orgs/{slug}")
    app.include_router(team_moves.router, prefix="/api/v1/orgs/{slug}", tags=["team-moves"])
    app.include_router(audit.router, prefix="/api/v1/orgs/{slug}")
    app.include_router(tokens.router, prefix="/api/v1/orgs/{slug}")
    app.include_router(usage.router, prefix="/api/v1/orgs/{slug}", tags=["usage"])
    app.include_router(workflow_cutover.router, prefix="/api/v1/orgs/{slug}", tags=["workflow-cutover"])
    app.include_router(workflow_activations.router, prefix="/api/v1/orgs/{slug}", tags=["workflow-activations"])
    app.include_router(workflow_templates.router, prefix="/api/v1/orgs/{slug}", tags=["workflow-templates"])
    app.include_router(workflow_release_templates.router, prefix="/api/v1/orgs/{slug}", tags=["workflow-release-templates"])
    from runtime.daemon.routes import workflow_coding_roles
    app.include_router(workflow_coding_roles.router, prefix="/api/v1/orgs/{slug}", tags=["workflows"])
    app.include_router(workflow_submissions.router, prefix="/api/v1/orgs/{slug}", tags=["workflow-submissions"])
    app.include_router(workflow_revisions.router, prefix="/api/v1/orgs/{slug}", tags=["workflow-revisions"])
    app.include_router(workflow_reviews.router, prefix="/api/v1/orgs/{slug}", tags=["workflow-reviews"])
    app.include_router(kb.router, prefix="/api/v1/orgs/{slug}")
    app.include_router(skills.router, prefix="/api/v1/orgs/{slug}", tags=["skills"])
    app.include_router(skills.agent_skills_router, prefix="/api/v1/orgs/{slug}", tags=["skills"])
    app.include_router(custom_skills.router, prefix="/api/v1/orgs/{slug}", tags=["custom-skills"])
    app.include_router(custom_skills.agent_custom_skills_router, prefix="/api/v1/orgs/{slug}", tags=["custom-skills"])

    app.include_router(threads.router, prefix="/api/v1/orgs/{slug}", tags=["threads"])
    app.include_router(dreams.router, prefix="/api/v1/orgs/{slug}", tags=["dreams"])
    app.include_router(work_hours.router, prefix="/api/v1/orgs/{slug}", tags=["work-hours"])
    app.include_router(schedules.router, prefix="/api/v1/orgs/{slug}", tags=["schedules"])
    app.include_router(portability.router, prefix="/api/v1/orgs/{slug}", tags=["portability"])
    app.include_router(jobs.router, prefix="/api/v1/orgs/{slug}", tags=["jobs"])
    app.include_router(jobs.dual_router, prefix="/api/v1/orgs/{slug}", tags=["jobs"])
    app.include_router(artifacts.router, prefix="/api/v1/orgs/{slug}", tags=["artifacts"])
    app.include_router(dashboard.router, prefix="/api/v1/orgs/{slug}", tags=["dashboard"])
    app.include_router(settings_routes.router, prefix="/api/v1/orgs/{slug}", tags=["settings"])
    app.include_router(executors.router, prefix="/api/v1/orgs/{slug}", tags=["executors"])
    app.include_router(executors.runtime_router, prefix="/api/v1", tags=["executors-runtime"])
    app.include_router(executor_binaries.router, prefix="/api/v1", tags=["executor-binaries"])
    # Slice A direct ingress is registration-token-only and must precede
    # master-bearer adapter catch-alls.
    app.include_router(direct_connect.router, prefix="/api/v1", tags=["direct-connect"])
    app.include_router(direct_connect_commit.router, prefix="/api/v1", tags=["direct-connect"])
    # seq184 contract-reference endpoint — isolated from master-bearer auth;
    # accepts ONLY registration-token auth (loopback + hrreg_ token, adapter-purpose).
    # Read-only; does not consume the token.
    # IMPORTANT: registered BEFORE adapters.router to ensure the specific
    # /runtime/adapters/contract-reference path takes priority over the
    # master-bearer GET /runtime/adapters/{adapter_id} catch-all on adapters.router.
    app.include_router(adapters.contract_reference_router, prefix="/api/v1", tags=["adapters"])
    app.include_router(adapters.router, prefix="/api/v1", tags=["adapters"])
    # seq141 submission endpoint — isolated from master-bearer auth;
    # accepts ONLY registration-token auth (loopback + hrreg_ token).
    app.include_router(adapters.submit_router, prefix="/api/v1", tags=["adapters"])
    from runtime.daemon.routes import web_static
    static_references = web_static.register(app, settings=state.settings)
    # Exact route/static identities must exist before middleware captures them.
    references = StartupHTTPReferences(app, state, static_references)
    app.state.startup_http = references
    state._startup_recovery._http_references = references
    app.add_middleware(StartupAdmissionMiddleware, references=references)
    return app
