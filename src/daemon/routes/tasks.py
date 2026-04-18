"""Task submission and inspection endpoints."""
from __future__ import annotations

import asyncio
import json as _json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from src.daemon.auth import require_token
from src.daemon.runner import TaskRunner
from src.daemon.state import DaemonState
from src.models import TaskRecord, TaskType

router = APIRouter(dependencies=[require_token()])

# Artifacts are fully inlined into the recall response when an agent asks for
# them, so cap the total to keep one recall under a comfortable prompt budget.
MAX_ARTIFACT_BYTES = 200 * 1024


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


def _read_artifact(
    workspaces_dir: Path, assigned_agent: str | None, artifact_dir: str | None,
) -> dict | None:
    """Return {files, truncated} for the artifact folder, or None if unresolvable.

    Files are read as text; anything that fails decoding (binaries) is skipped.
    If the total inlined payload would exceed MAX_ARTIFACT_BYTES we flip to a
    path-only listing with truncated=True so the agent still sees the inventory.
    """
    if not assigned_agent or not artifact_dir:
        return None
    base = workspaces_dir / assigned_agent / artifact_dir
    if not base.exists():
        return {"files": [], "truncated": False}
    all_files = sorted(f for f in base.rglob("*") if f.is_file())
    files: list[dict] = []
    total = 0
    for f in all_files:
        try:
            text = f.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        total += len(text.encode("utf-8"))
        if total > MAX_ARTIFACT_BYTES:
            return {
                "files": [{"path": str(f.relative_to(base))} for f in all_files],
                "truncated": True,
            }
        files.append({"path": str(f.relative_to(base)), "content": text})
    return {"files": files, "truncated": False}


def _recall_node(
    state: DaemonState, task_id: str, tree: bool, include_artifact: bool,
) -> dict | None:
    payload = state.db.get_recall_payload(task_id)
    if payload is None:
        return None
    if include_artifact:
        payload["artifact"] = _read_artifact(
            state.runtime.workspaces_dir,
            payload.get("assigned_agent"),
            payload.get("artifact_dir"),
        )
    if tree:
        child_ids = payload["children"]
        payload["children"] = [
            _recall_node(state, cid, tree=True, include_artifact=include_artifact)
            for cid in child_ids
        ]
    return payload


@router.get("/tasks/{task_id}/recall")
def recall_task(
    task_id: str,
    request: Request,
    tree: bool = False,
    include_artifact: bool = False,
) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)
    node = _recall_node(state, task_id, tree=tree, include_artifact=include_artifact)
    if node is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    return node


class CompletionBody(BaseModel):
    session_id: str
    agent: str
    status: str
    confidence: int
    output_summary: str
    risks_flagged: list[str] = []
    dependencies: list[str] = []
    suggested_reviewer_focus: list[str] = []
    artifact_dir: str | None = None


@router.get("/tasks/{task_id}/events")
async def task_events(task_id: str, request: Request):
    state: DaemonState = request.app.state.daemon
    _require_active(state)
    # Reject unknown task IDs up front — otherwise EventBus.subscribe() replays
    # no history for a fabricated id and then blocks forever, which makes
    # `opc tail <bad-id>` hang instead of surfacing a 404.
    if state.db.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")

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
            status=body.status,
            output_summary=body.output_summary,
            confidence_score=body.confidence,
            risks_flagged=body.risks_flagged,
            artifact_dir=body.artifact_dir,
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
