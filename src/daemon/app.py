"""FastAPI app factory."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.daemon.dispatcher import Dispatcher
from src.daemon.routes import (
    agents,
    audit,
    health,
    kb,
    orgs,
    runtime,
    talks,
    tasks,
    tokens,
)
from src.daemon.state import DaemonState


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
    state.queue.start_workers(dispatcher, n=3)


def _start_feishu_listeners(state: DaemonState, loop) -> None:
    """For each org with full Feishu config, construct and start a listener."""
    import logging

    from src.daemon.feishu_listener import FeishuEventListener
    from src.daemon.routes.tasks import resolve_escalation_in_process
    from src.infrastructure.audit_logger import AuditLogger

    _log = logging.getLogger(__name__)

    for org in state.orgs.values():
        if (
            org.feishu_app_id is None or org.feishu_app_secret is None
            or org.feishu_chat_id is None or org.feishu_domain is None
        ):
            continue

        async def _resolve_for_listener(_org=org, _state=state, **kw):
            # Strip slug kwarg forwarded by the listener — already bound via _org.
            kw.pop("slug", None)
            # Listener should never raise; swallow validation errors and log.
            try:
                await resolve_escalation_in_process(_org, _state, **kw)
            except Exception:
                _log.exception(
                    "resolve_escalation_in_process rejected reply for task %s",
                    kw.get("task_id"),
                )

        listener = FeishuEventListener(
            slug=org.slug,
            db=org.db,
            audit=AuditLogger(org.db),
            chat_id=org.feishu_chat_id,
            resolve_escalation=_resolve_for_listener,
            loop=loop,
            app_id=org.feishu_app_id,
            app_secret=org.feishu_app_secret,
            domain=org.feishu_domain,
        )
        listener.start()
        org.feishu_listener = listener


@asynccontextmanager
async def _lifespan(app: FastAPI):
    import asyncio

    state: DaemonState = app.state.daemon
    ensure_workers_started(state)
    _start_feishu_listeners(state, asyncio.get_running_loop())
    try:
        yield
    finally:
        await state.queue.stop()
        await state.close_all()


def create_app(state: DaemonState) -> FastAPI:
    app = FastAPI(title="OPC Daemon", version="0.2.0", lifespan=_lifespan)
    app.state.daemon = state
    app.include_router(health.router, prefix="/api/v1")
    app.include_router(runtime.router, prefix="/api/v1")
    app.include_router(orgs.router, prefix="/api/v1")
    app.include_router(tasks.router, prefix="/api/v1/orgs/{slug}")
    app.include_router(agents.router, prefix="/api/v1/orgs/{slug}")
    app.include_router(audit.router, prefix="/api/v1/orgs/{slug}")
    app.include_router(tokens.router, prefix="/api/v1/orgs/{slug}")
    app.include_router(kb.router, prefix="/api/v1/orgs/{slug}")
    app.include_router(talks.router, prefix="/api/v1/orgs/{slug}")
    return app
