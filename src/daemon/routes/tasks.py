"""Task submission and inspection endpoints."""
from __future__ import annotations

import asyncio
import json as _json

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from src.daemon.auth import require_token
from src.daemon.runner import TaskRunner
from src.daemon.state import DaemonState
from src.models import TaskRecord, TaskType

router = APIRouter(dependencies=[require_token()])


class SubmitTask(BaseModel):
    type: TaskType = TaskType.GENERAL
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
    async with state.db_lock:
        task_id = state.db.next_task_id()
        state.db.insert_task(TaskRecord(id=task_id, type=body.type, brief=body.brief))

    runner = TaskRunner(state=state)
    asyncio.create_task(runner.run(task_id))  # TODO(task-26): track these tasks so shutdown can cancel/await them.
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


class CompletionBody(BaseModel):
    session_id: str
    agent: str
    status: str
    confidence: int
    output_summary: str
    risks_flagged: list[str] = []
    dependencies: list[str] = []
    suggested_reviewer_focus: list[str] = []


@router.get("/tasks/{task_id}/events")
async def task_events(task_id: str, request: Request):
    state: DaemonState = request.app.state.daemon
    _require_active(state)

    async def gen():
        async for event in state.event_bus.subscribe(task_id):
            yield {"data": _json.dumps(event)}

    return EventSourceResponse(gen())


@router.post("/tasks/{task_id}/completion")
async def submit_completion(task_id: str, body: CompletionBody, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)
    expected = state.sessions.get_active(task_id, body.agent)
    # Reject callbacks the daemon never spawned. Both branches are 409 — the
    # tracker is the source of truth for "is this a real session". Unknown
    # comes first so an empty tracker can't silently accept a fabricated id.
    if expected is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "unknown_session", "task_id": task_id, "agent": body.agent},
        )
    if expected != body.session_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "session_mismatch", "active": expected, "got": body.session_id},
        )
    async with state.db_lock:
        state.db.insert_task_result(
            task_id=task_id,
            agent=body.agent,
            session_id=body.session_id,
            output_summary=body.output_summary,
            confidence_score=body.confidence,
            risks_flagged=body.risks_flagged,
        )
    # Clear the tracker so a duplicate POST for the same session is rejected as
    # unknown_session rather than silently persisting a second row.
    state.sessions.clear(task_id, body.agent)
    # TODO(events): subscribers that connect after this point won't replay
    # `completion_reported`. The terminal task_* event is still synthesized
    # from the DB status, but per-agent completion beats are lost. Acceptable
    # today because the orchestrator consumes completions via DB (not SSE) and
    # SSE is for human observers.
    await state.event_bus.publish(task_id, {
        "type": "completion_reported",
        "agent": body.agent,
        "session_id": body.session_id,
        "status": body.status,
    })
    return {"ok": True}
