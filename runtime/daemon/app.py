"""FastAPI app factory."""
from __future__ import annotations

import time as _time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from runtime.config import settings
from runtime.daemon.dispatcher import Dispatcher
from runtime.daemon.routes import (
    adapters,
    agents,
    artifacts,
    assistant,
    assistant_a_mode,
    audit,
    auth,
    custom_skills,
    dashboard,
    direct_connect,
    direct_connect_commit,
    dreams,
    executor_binaries,
    executors,
    health,
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
    threads,
    tokens,
    work_hours,
    schedules,
)
from runtime.daemon.routes import settings as settings_routes
from runtime.daemon.metrics import error_label, route_template_label
from runtime.daemon.state import DaemonState
from runtime.orchestrator._paths import OrgPaths


def _attach_org_runtime_wiring(state: DaemonState) -> None:
    """Wire each loaded org's Orchestrator to the global queue + per-org sessions.

    The Orchestrator is built inside ``OrgState.load`` so it knows its slug,
    but its ``_queue`` and ``_sessions`` references are populated separately
    so unit tests that build an OrgState without a daemon can still inspect
    the orchestrator before the queue exists.
    """
    for org in state.orgs.values():
        org.orchestrator.attach_queue(state.queue)
        org.orchestrator.attach_sessions(org.sessions)


def ensure_workers_started(state: DaemonState) -> None:
    """Start the worker pool if a runtime is active and workers aren't running.

    Idempotent. Each org's Orchestrator is built once when the org is loaded
    (see OrgState.load); the Dispatcher routes (slug, task_id) tuples to the
    right one.
    """
    if state.is_idle:
        return
    _attach_org_runtime_wiring(state)
    if state.queue.is_running():
        return
    dispatcher = Dispatcher(state)
    state.queue.start_workers(dispatcher, n=settings.queue_workers)


def _attach_thread_queue_wiring(state: DaemonState, loop) -> None:
    """Wire each org's ThreadQueue + the daemon main loop into its Orchestrator.

    Called after the event loop is running (inside the lifespan) so that
    ``run_step`` workers can cross the thread boundary via
    ``asyncio.run_coroutine_threadsafe`` when enqueuing task-followup
    invocations. Mirrors ``_attach_org_runtime_wiring``'s decoupled pattern.
    """
    for org in state.orgs.values():
        org.orchestrator.attach_thread_queue(org.thread_queue, loop)


def _wire_then_start_workers(state: DaemonState, loop) -> None:
    """Wire ThreadQueue + main loop into each Orchestrator, then start workers.

    Ordering is critical: the thread queue and main loop MUST be wired before
    task workers start.  Workers that execute before wiring will find
    ``orch._thread_queue`` and ``orch._main_loop`` as ``None``, producing
    ``enqueue_unavailable`` and stranding any TASK_FOLLOWUP invocation as
    permanently pending.

    This helper is the single atomic call used by both ``_lifespan`` (the
    production startup path) and the THR-109 regression test, so the same
    ordering is exercised and verified in both contexts.
    """
    _attach_thread_queue_wiring(state, loop)
    ensure_workers_started(state)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    import asyncio
    import logging
    from datetime import datetime, timezone

    from runtime.daemon.thread_queue import thread_worker_loop

    state: DaemonState = app.state.daemon

    # THR-109: wire the ThreadQueue + main loop BEFORE task workers start.
    # Uses _wire_then_start_workers to enforce ordering: wiring must precede
    # ensure_workers_started so a rapid terminal thread-dispatched task can
    # enqueue its TASK_FOLLOWUP via asyncio.run_coroutine_threadsafe.
    _main_loop = asyncio.get_running_loop()
    _wire_then_start_workers(state, _main_loop)

    # Recover any jobs left in 'running' state from a previous daemon process.
    _now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _logger = logging.getLogger("happyranch.daemon")
    from runtime.daemon.jobs_runner import migrate_artifacts_layout, migrate_filesystem_layout
    for org in state.orgs.values():
        # Rename <org_root>/scripts/ → jobs/ (and SR-* → JOB-*) BEFORE the
        # recovery scan reads any stdout_path/stderr_path. The DB-side
        # rename already happened in Database init; this realigns disk.
        migrate_filesystem_layout(org.root)
        # 2026-06-02 rename: rehome on-disk assets/ → artifacts/ and
        # per-workspace artifacts/ → output/ before any mkdir.
        migrate_artifacts_layout(org.root)
        OrgPaths(org.root).artifacts_dir.mkdir(exist_ok=True)
        # THR-095: one-shot reconcile agent.yaml executor/repos/model → .md
        try:
            from runtime.daemon.agent_config import migrate_agent_yaml_to_frontmatter
            migration_results = migrate_agent_yaml_to_frontmatter(OrgPaths(root=org.root))
            if migration_results:
                changed = {k: v for k, v in migration_results.items() if v != "unchanged"}
                if changed:
                    _logger.info(
                        "THR-095 agent.yaml->frontmatter migration for org %s: %s",
                        org.slug, changed,
                    )
        except Exception as exc:
            _logger.warning("THR-095 migration error for org %s: %s", org.slug, exc)
        # THR-106: one-shot skill-id rename hr:review -> hr:reflection in the
        # persisted org/config.yaml skills eligibility section (allow + deny,
        # org/team/agent scope). Sentinel-gated (.hr_review_renamed).
        try:
            from runtime.orchestrator.org_config import migrate_hr_review_skill_id
            rename_outcome = migrate_hr_review_skill_id(OrgPaths(root=org.root))
            if rename_outcome != "skipped (already migrated)":
                _logger.info(
                    "THR-106 hr:review->hr:reflection migration for org %s: %s",
                    org.slug, rename_outcome,
                )
        except Exception as exc:
            _logger.warning(
                "THR-106 skill-id rename migration error for org %s: %s",
                org.slug, exc,
            )
        recovered = org.db.recover_orphaned_running_jobs(now_iso=_now_iso)
        if recovered:
            _logger.warning(
                "recovered %d orphaned jobs in org %s: %s",
                len(recovered), org.slug, recovered,
            )

    # _attach_thread_queue_wiring was called above (before workers).
    # The second call at the original location is now a no-op for
    # the wiring; the subsequent jobs_resume_main_loop + blocked-on-job
    # recovery still follow the same order relative to wiring.
    from runtime.daemon.jobs_runner import attach_jobs_resume_main_loop as _wire_jobs
    _wire_jobs(
        _main_loop,
        lambda slug: state.orgs[slug].orchestrator if slug in state.orgs else None,
    )

    # Spec §5.7: re-evaluate predicate for blocked-on-job tasks. Catches the
    # case where a job's terminal happened during a crash window — the job is
    # now terminal but caller A (jobs_runner hook) never fired because the
    # daemon was down. Runs AFTER _attach_thread_queue_wiring so resumed
    # tasks that complete fast (sub-second) still have a wired thread queue
    # when their _maybe_post_thread_followup fires — otherwise the followup
    # audits enqueue_unavailable and the thread loses its task-completed
    # message.
    from runtime.orchestrator.run_step import _maybe_resume_blocked_task
    for org in state.orgs.values():
        for _task_id in org.db.list_tasks_blocked_on_jobs():
            _maybe_resume_blocked_task(
                org.orchestrator, _task_id,
                trigger="startup_recovery", triggering_job_id=None,
            )

    # GitHub #688 Slice B: enqueue startup-recovered reply-delivery tokens
    # BEFORE the thread workers start draining each org's queue, so retained
    # queued wakes and daemon_restart replacements are picked up exactly once
    # (duplicate notifications remain harmless through the claim CAS).
    from runtime.daemon.thread_queue import ThreadJob
    for org in state.orgs.values():
        for token in getattr(org, "_startup_recovered_thread_tokens", []):
            await org.thread_queue.put(
                ThreadJob(org_slug=org.slug, invocation_token=token),
            )

    thread_worker_tasks = [
        asyncio.create_task(thread_worker_loop(state, state.settings))
        for _ in range(4)
    ]

    from runtime.daemon.dream_scheduler import (
        dream_scheduler_loop,
        recover_running_dreams,
    )
    from runtime.daemon.dream_queue import dream_worker_loop

    for org in state.orgs.values():
        recover_running_dreams(org)

    dream_worker_tasks = [
        asyncio.create_task(dream_worker_loop(state, state.settings))
        for _ in range(1)
    ]
    dream_scheduler_task = asyncio.create_task(dream_scheduler_loop(state))

    from runtime.daemon.work_hours_scheduler import work_hours_scheduler_loop
    from runtime.daemon.wake_queue import wake_worker_loop

    # Startup recovery: a wake left `running` when the daemon died can never
    # receive its spawn callback, so mark it failed (mirrors recover_running_dreams).
    for org in state.orgs.values():
        org.db.work_hours.recover_running()

    wake_worker_tasks = [
        asyncio.create_task(wake_worker_loop(state, state.settings))
        for _ in range(1)
    ]
    work_hours_scheduler_task = asyncio.create_task(work_hours_scheduler_loop(state))

    # ── Schedule fire wiring (THR-105 Phase 3) ──
    from runtime.daemon.schedule_scheduler import schedule_scheduler_loop
    from runtime.daemon.schedule_queue import schedule_worker_loop

    # Startup recovery: a schedule left `firing` when the daemon died can never
    # receive its spawn callback, so mark it failed (mirrors recover_running).
    for org in state.orgs.values():
        recovered = org.db.schedules.recover_firing()
        for schedule_id, agent_name in recovered:
            org.db.insert_audit_log(
                task_id=schedule_id,
                agent=agent_name,
                action="schedule_failed",
                payload={"reason": "daemon_restart"},
            )

    schedule_worker_tasks = [
        asyncio.create_task(schedule_worker_loop(state, state.settings))
        for _ in range(1)
    ]
    schedule_scheduler_task = asyncio.create_task(schedule_scheduler_loop(state))

    from runtime.daemon.zombie_reaper import zombie_reaper_loop
    zombie_reaper_task = asyncio.create_task(zombie_reaper_loop(state))
    from runtime.daemon.direct_connect_projection_sweep import direct_connect_projection_sweep_loop
    direct_connect_projection_sweep_task = asyncio.create_task(
        direct_connect_projection_sweep_loop(state)
    )

    # ── Dashboard projection cold-start + periodic refresh ────────────
    # THR-129: per-org durable last-known-good cache, refreshed every 10s.
    # Load persisted snapshot synchronously (never block the event loop),
    # then start an async bootstrap-and-schedule task. The lifespan must
    # NOT await a DB compose before yielding — the initial warm runs in
    # the background. Cache-only GET /dashboard/summary returns 503 until
    # the first warm completes.
    from runtime.infrastructure.kb_store import KBStore
    _dashboard_tasks: list[asyncio.Task] = []
    for org in state.orgs.values():
        mgr = org.dashboard_projection
        # Load persisted snapshot from disk synchronously (no-op if missing).
        # This is a fast filesystem read — no DB access, no event-loop block.
        persisted = mgr.load_from_disk()
        if persisted is not None:
            mgr._projection = persisted
            _logger.info(
                "dashboard projection loaded from disk for org %s (generated_at=%s)",
                org.slug, persisted.generated_at.isoformat(),
            )
        # Start the bootstrap-then-schedule task. The initial warm runs
        # asynchronously — the lifespan yields immediately. The route
        # returns 503 until warm completes (cache-only, never synchronous).
        kb_store = KBStore(org.root / "kb")
        task = mgr.start_scheduler(
            db=org.db, kb_store=kb_store, teams=org.teams,
            loop=_main_loop, initial_warm=True,
        )
        _dashboard_tasks.append(task)

    try:
        yield
    finally:
        # ── Dashboard projection cleanup ──────────────────────────────
        # Cancel FIRST (before any await) so a refresh stuck in its
        # to_thread/warm path doesn't make daemon shutdown wait for
        # cooperative stop observation. Then reap to collect outcomes
        # with return_exceptions — never leave unowned task exceptions.
        for org in state.orgs.values():
            org.dashboard_projection.cancel_scheduler()
        if _dashboard_tasks:
            await asyncio.gather(*_dashboard_tasks, return_exceptions=True)
        for org in state.orgs.values():
            await org.dashboard_projection.reap_scheduler()
        for t in thread_worker_tasks:
            t.cancel()
        dream_scheduler_task.cancel()
        for t in dream_worker_tasks:
            t.cancel()
        work_hours_scheduler_task.cancel()
        for t in wake_worker_tasks:
            t.cancel()
        schedule_scheduler_task.cancel()
        for t in schedule_worker_tasks:
            t.cancel()
        zombie_reaper_task.cancel()
        direct_connect_projection_sweep_task.cancel()
        from runtime.daemon.jobs_runner import terminate_all_inflight
        await terminate_all_inflight(grace_seconds=5)
        await state.queue.stop()
        await state.close_all()


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
    # Wire metrics registry into the run_step worker queue for loop-tick recording.
    state.queue._metrics_registry = state.metrics_registry

    app.middleware("http")(metrics_timing_middleware)

    app.include_router(health.router, prefix="/api/v1")
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(metrics.router, prefix="/api/v1")
    app.include_router(runtime.router, prefix="/api/v1")
    app.include_router(orgs.router, prefix="/api/v1")
    app.include_router(assistant.router, prefix="/api/v1")
    app.include_router(assistant_a_mode.router, prefix="/api/v1")
    app.include_router(tasks.router, prefix="/api/v1/orgs/{slug}")
    app.include_router(agents.router, prefix="/api/v1/orgs/{slug}")
    app.include_router(teams.router, prefix="/api/v1/orgs/{slug}")
    app.include_router(audit.router, prefix="/api/v1/orgs/{slug}")
    app.include_router(tokens.router, prefix="/api/v1/orgs/{slug}")
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
    web_static.register(app, settings=state.settings)
    return app
