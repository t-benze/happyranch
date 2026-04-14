"""Task submission and inspection endpoints."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from pydantic import BaseModel

from src.daemon.auth import require_token
from src.daemon.runner import TaskRunner
from src.daemon.state import DaemonState
from src.models import TaskRecord, TaskType

router = APIRouter(dependencies=[require_token()])


class SubmitTask(BaseModel):
    type: str = "general"
    brief: str


def _require_active(state: DaemonState) -> None:
    if state.is_idle:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "no_active_runtime"},
        )


@router.post("/tasks")
async def submit_task(body: SubmitTask, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)
    task_type = TaskType(body.type)
    task_id = state.db.next_task_id()
    state.db.insert_task(TaskRecord(id=task_id, type=task_type, brief=body.brief))

    runner = TaskRunner(state=state)
    asyncio.create_task(runner.run(task_id))
    return {"task_id": task_id}


@router.get("/tasks")
def list_tasks(request: Request, limit: int = 20) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)
    tasks = state.db.list_tasks(limit=limit)
    return {"tasks": [t.model_dump() for t in tasks]}


@router.get("/tasks/{task_id}")
def get_task(task_id: str, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)
    task = state.db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    return {
        "task": task.model_dump(),
        "results": state.db.get_task_results(task_id),
        "audit_log": state.db.get_audit_logs(task_id),
    }
