"""FastAPI app factory."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.daemon.routes import agents, audit, health, kb, runtimes, talks, tasks
from src.daemon.state import DaemonState
from src.orchestrator.orchestrator import Orchestrator


def ensure_workers_started(state: DaemonState) -> None:
    """Construct the orchestrator and start the worker pool, if not already.

    Idempotent: safe to call repeatedly. Needed because the daemon may boot
    idle (no active runtime) and have a runtime swapped in later via
    POST /runtimes/register — without this helper the lifespan's one-shot
    bootstrap would leave workers unstarted and every submitted task would
    sit in the queue forever (manifests in tests as httpx.ReadError on the
    SSE stream after heartbeat churn).
    """
    if state.is_idle:
        return
    if state.queue.is_running():
        return
    orch = Orchestrator(db=state.db, settings=state.settings, runtime=state.runtime, teams=state.teams)
    orch.attach_queue(state.queue)
    orch.attach_sessions(state.sessions)
    state.queue.start_workers(orch, n=3)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    state: DaemonState = app.state.daemon
    ensure_workers_started(state)
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
    app.include_router(talks.router, prefix="/api/v1")
    return app
