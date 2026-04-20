"""FastAPI app factory."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.daemon.routes import agents, audit, health, kb, runtimes, tasks
from src.daemon.state import DaemonState
from src.orchestrator.orchestrator import Orchestrator


@asynccontextmanager
async def _lifespan(app: FastAPI):
    state: DaemonState = app.state.daemon
    if not state.is_idle:
        orch = Orchestrator(db=state.db, settings=state.settings, runtime=state.runtime)
        orch.attach_queue(state.queue)
        orch.attach_sessions(state.sessions)
        state.queue.start_workers(orch, n=3)
    try:
        yield
    finally:
        await state.queue.stop()


def create_app(state: DaemonState) -> FastAPI:
    app = FastAPI(title="OPC Daemon", version="0.1.0", lifespan=_lifespan)
    app.state.daemon = state
    app.include_router(health.router, prefix="/api/v1")
    app.include_router(runtimes.router, prefix="/api/v1")
    app.include_router(tasks.router, prefix="/api/v1")
    app.include_router(agents.router, prefix="/api/v1")
    app.include_router(audit.router, prefix="/api/v1")
    app.include_router(kb.router, prefix="/api/v1")
    return app
