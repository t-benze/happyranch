"""Singular runtime endpoints: get info, register, switch."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from runtime.daemon import runtimes as reg
from runtime.daemon.auth import require_token
from runtime.daemon.state import DaemonState
from runtime.runtime import RuntimeDir

router = APIRouter(dependencies=[require_token()])


class RuntimePath(BaseModel):
    path: str


def _swap(state: DaemonState, runtime: RuntimeDir) -> None:
    """Replace the daemon's active runtime atomically."""
    new_state = DaemonState.from_runtime(runtime, state.settings)
    # Move the worker queue and lock instances into the new state so an
    # in-flight worker pool keeps consuming. Note: this swap happens only
    # when the queue is empty (use endpoint enforces it).
    new_state.queue = state.queue
    new_state.orgs_lock = state.orgs_lock
    state.runtime = new_state.runtime
    state.orgs = new_state.orgs


@router.get("/runtime")
async def get_runtime(request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    return {"runtime": str(state.runtime.root) if state.runtime else None}


@router.post("/runtime")
async def register_runtime(body: RuntimePath, request: Request) -> dict:
    from runtime.daemon.app import ensure_workers_started

    daemon: DaemonState = request.app.state.daemon
    path = Path(body.path).expanduser()
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
    runtime = RuntimeDir.init(path)
    reg.register(path)

    # If we're swapping the active runtime out, refuse when any org still has
    # in-flight work — same guard as `/runtime/use`. Re-registering the same
    # path (idempotent `happyranch init <existing>`) is allowed: identical resolved
    # root means there's no swap.
    same_root = (
        daemon.runtime is not None and daemon.runtime.root == runtime.root
    )
    async with daemon.orgs_lock:
        if not same_root:
            for org in daemon.orgs.values():
                in_flight = org.db.get_nonterminal_task_ids()
                if in_flight:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "active_tasks_in_flight",
                            "org": org.slug,
                            "task_ids": in_flight,
                        },
                    )
            for org in list(daemon.orgs.values()):
                org.close()
            daemon.orgs.clear()
        _swap(daemon, runtime)
    ensure_workers_started(daemon)
    return {"runtime": str(path.resolve())}


@router.post("/runtime/use")
async def use_runtime(body: RuntimePath, request: Request) -> dict:
    from runtime.daemon.app import ensure_workers_started

    daemon: DaemonState = request.app.state.daemon
    path = Path(body.path).expanduser().resolve()
    runtime = RuntimeDir.load(path)

    async with daemon.orgs_lock:
        for org in daemon.orgs.values():
            in_flight = org.db.get_nonterminal_task_ids()
            if in_flight:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "active_tasks_in_flight",
                        "org": org.slug,
                        "task_ids": in_flight,
                    },
                )
        reg.activate(path)
        for org in list(daemon.orgs.values()):
            org.close()
        daemon.orgs.clear()
        _swap(daemon, runtime)
    ensure_workers_started(daemon)
    return {"runtime": str(path)}
