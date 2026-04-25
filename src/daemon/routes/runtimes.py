"""Runtime registry endpoints."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from src.daemon import runtimes as reg
from src.daemon.auth import require_token
from src.daemon.state import DaemonState
from src.infrastructure.database import Database
from src.orchestrator.teams import TeamsRegistry
from src.runtime import RuntimeDir

router = APIRouter(dependencies=[require_token()])


class RuntimePath(BaseModel):
    path: str


def _swap_active_runtime(state: DaemonState, new_path: Path) -> None:
    """Replace the daemon's active runtime atomically.

    Build the new runtime + connection before touching the old one so a
    failing load leaves the daemon on the old runtime instead of a closed
    half-state.
    """
    new_runtime = RuntimeDir.load(new_path)
    new_db = Database(new_runtime.db_path)
    new_teams = TeamsRegistry.load(new_runtime)
    old_db = state.db
    state.runtime = new_runtime
    state.db = new_db
    state.teams = new_teams
    if old_db is not None:
        old_db.close()


@router.get("/runtimes")
def list_runtimes(request: Request) -> dict:
    state = reg.load()
    return {
        "active": str(state.active) if state.active else None,
        "registered": [str(p) for p in state.registered],
    }


@router.post("/runtimes/register")
async def register_runtime(body: RuntimePath, request: Request) -> dict:
    from src.daemon.app import ensure_workers_started

    daemon: DaemonState = request.app.state.daemon
    path = Path(body.path).expanduser()
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
    RuntimeDir.init(path)
    try:
        reg.register(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _swap_active_runtime(daemon, path.resolve())
    # If the daemon booted idle, workers weren't started in the lifespan.
    # Start them now that a runtime is present — otherwise enqueued tasks
    # sit forever and SSE streams stall. Must happen on the running event
    # loop, which is why this route is async.
    ensure_workers_started(daemon)
    return list_runtimes(request)


@router.post("/runtimes/activate")
async def activate_runtime(body: RuntimePath, request: Request) -> dict:
    from src.daemon.app import ensure_workers_started

    daemon: DaemonState = request.app.state.daemon
    path = Path(body.path).expanduser().resolve()
    state = reg.load()
    if path not in state.registered:
        raise HTTPException(status_code=404, detail=f"{path} is not registered")

    async with daemon.db_lock:
        if daemon.db is not None:
            in_flight = daemon.db.get_nonterminal_task_ids()
            if in_flight:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "active_tasks_in_flight", "task_ids": in_flight},
                )
        reg.activate(path)
        _swap_active_runtime(daemon, path)
    ensure_workers_started(daemon)
    return list_runtimes(request)
