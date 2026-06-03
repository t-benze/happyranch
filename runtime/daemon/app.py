"""FastAPI app factory."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from runtime.config import settings
from runtime.daemon.dispatcher import Dispatcher
from runtime.daemon.routes import (
    agents,
    artifacts,
    audit,
    auth,
    dashboard,
    health,
    jobs,
    kb,
    orgs,
    runtime,
    talks,
    tasks,
    teams,
    threads,
    tokens,
)
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


def _start_feishu_listeners(state: DaemonState, loop) -> None:
    """For each org with full Feishu config, construct and start a listener."""
    from runtime.daemon.feishu_listener import start_feishu_listeners_for_state

    start_feishu_listeners_for_state(state, loop)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    import asyncio
    import logging
    from datetime import datetime, timezone

    from runtime.daemon.thread_queue import thread_worker_loop

    state: DaemonState = app.state.daemon
    ensure_workers_started(state)

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
        recovered = org.db.recover_orphaned_running_jobs(now_iso=_now_iso)
        if recovered:
            _logger.warning(
                "recovered %d orphaned jobs in org %s: %s",
                len(recovered), org.slug, recovered,
            )

    _main_loop = asyncio.get_running_loop()
    _attach_thread_queue_wiring(state, _main_loop)
    _start_feishu_listeners(state, _main_loop)
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
    thread_worker_tasks = [
        asyncio.create_task(thread_worker_loop(state, state.settings))
        for _ in range(4)
    ]
    try:
        yield
    finally:
        for t in thread_worker_tasks:
            t.cancel()
        from runtime.daemon.jobs_runner import terminate_all_inflight
        await terminate_all_inflight(grace_seconds=5)
        await state.queue.stop()
        await state.close_all()


def create_app(state: DaemonState) -> FastAPI:
    app = FastAPI(title="HappyRanch Daemon", version="0.2.0", lifespan=_lifespan)
    app.state.daemon = state
    app.include_router(health.router, prefix="/api/v1")
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(runtime.router, prefix="/api/v1")
    app.include_router(orgs.router, prefix="/api/v1")
    app.include_router(tasks.router, prefix="/api/v1/orgs/{slug}")
    app.include_router(agents.router, prefix="/api/v1/orgs/{slug}")
    app.include_router(teams.router, prefix="/api/v1/orgs/{slug}")
    app.include_router(audit.router, prefix="/api/v1/orgs/{slug}")
    app.include_router(tokens.router, prefix="/api/v1/orgs/{slug}")
    app.include_router(kb.router, prefix="/api/v1/orgs/{slug}")
    app.include_router(talks.router, prefix="/api/v1/orgs/{slug}")
    app.include_router(threads.router, prefix="/api/v1/orgs/{slug}", tags=["threads"])
    app.include_router(jobs.router, prefix="/api/v1/orgs/{slug}", tags=["jobs"])
    app.include_router(jobs.dual_router, prefix="/api/v1/orgs/{slug}", tags=["jobs"])
    app.include_router(artifacts.router, prefix="/api/v1/orgs/{slug}", tags=["artifacts"])
    app.include_router(dashboard.router, prefix="/api/v1/orgs/{slug}", tags=["dashboard"])
    from runtime.daemon.routes import web_static
    web_static.register(app)
    return app
