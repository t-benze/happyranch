"""FastAPI app factory."""
from __future__ import annotations

from fastapi import FastAPI

from src.daemon.routes import health, runtimes, tasks
from src.daemon.state import DaemonState


def create_app(state: DaemonState) -> FastAPI:
    app = FastAPI(title="OPC Daemon", version="0.1.0")
    app.state.daemon = state
    app.include_router(health.router, prefix="/api/v1")
    app.include_router(runtimes.router, prefix="/api/v1")
    app.include_router(tasks.router, prefix="/api/v1")
    return app
