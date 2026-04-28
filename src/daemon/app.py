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
)
from src.daemon.state import DaemonState


def ensure_workers_started(state: DaemonState) -> None:
    """Start the worker pool if a runtime is active and workers aren't running.

    Idempotent. Each org's Orchestrator is built once when the org is loaded
    (see OrgState.load); the Dispatcher routes (slug, task_id) tuples to the
    right one.
    """
    if state.is_idle:
        return
    if state.queue.is_running():
        return
    dispatcher = Dispatcher(state)
    state.queue.start_workers(dispatcher, n=3)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    state: DaemonState = app.state.daemon
    ensure_workers_started(state)
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
    app.include_router(tasks.router, prefix="/api/v1")
    app.include_router(agents.router, prefix="/api/v1")
    app.include_router(audit.router, prefix="/api/v1")
    app.include_router(kb.router, prefix="/api/v1")
    app.include_router(talks.router, prefix="/api/v1")
    return app
