"""Agent inspection, init, and learnings callback endpoints."""
from __future__ import annotations

import asyncio
import json as _json

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from src.daemon.auth import require_token
from src.daemon.state import DaemonState
from src.models import AgentName
from src.orchestrator.context_builder import ContextBuilder
from src.orchestrator.performance_tracker import PerformanceTracker
from src.orchestrator.prompt_loader import load_all_prompts

router = APIRouter(dependencies=[require_token()])


class InitBody(BaseModel):
    agent: str | None = None


class LearningBody(BaseModel):
    session_id: str
    task_id: str
    text: str


def _require_active(state: DaemonState) -> None:
    if state.is_idle:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "no_active_runtime"},
        )


@router.get("/agents")
def list_agents(request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)
    tracker = PerformanceTracker(state.db, state.settings)
    tiers = tracker.get_all_tiers()
    return {
        "agents": [
            {
                "name": a.value,
                "tier": tiers.get(a, "green").value if hasattr(tiers.get(a, "green"), "value") else tiers.get(a, "green"),
                "scorecard": state.db.get_scorecard(a.value),
            }
            for a in AgentName
        ],
    }


@router.post("/agents/init")
async def init_agents(body: InitBody, request: Request):
    state: DaemonState = request.app.state.daemon
    _require_active(state)

    targets: list[AgentName]
    if body.agent is None:
        targets = list(AgentName)
    else:
        targets = [AgentName(body.agent)]

    async def gen():
        protocol_dir = state.settings.get_protocol_dir()
        prompts = load_all_prompts(protocol_dir)
        ctx = ContextBuilder(state.settings)
        for agent in targets:
            workspace = state.runtime.workspaces_dir / agent.value
            workspace.mkdir(parents=True, exist_ok=True)
            yield {"data": _json.dumps({"agent": agent.value, "phase": "starting"})}
            await asyncio.to_thread(
                ctx.initialize_workspace, workspace, agent.value,
                prompts.get(agent.value, ""),
            )
            yield {"data": _json.dumps({"agent": agent.value, "phase": "done"})}
        yield {"data": _json.dumps({"phase": "all_done"})}

    return EventSourceResponse(gen())


@router.post("/agents/{agent_name}/learnings")
async def append_learning(agent_name: str, body: LearningBody, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)

    expected = state.sessions.get_active(body.task_id, agent_name)
    if expected is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "unknown_session", "task_id": body.task_id, "agent": agent_name},
        )
    if expected != body.session_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "session_mismatch", "active": expected, "got": body.session_id},
        )

    workspace = state.runtime.workspaces_dir / agent_name
    learnings_path = workspace / "learnings.md"
    if not learnings_path.exists():
        learnings_path.parent.mkdir(parents=True, exist_ok=True)
        learnings_path.write_text(f"# Learnings: {agent_name}\n\n")

    async with state.db_lock:
        existing = learnings_path.read_text()
        learnings_path.write_text(existing + f"- {body.text}\n")
    return {"ok": True}
