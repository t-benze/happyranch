"""Agent inspection, init, and learnings callback endpoints."""
from __future__ import annotations

import asyncio
import json as _json
import shutil
from enum import StrEnum

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from src.daemon.agent_config import add_repo, load_agent_config, remove_repo, update_repo_url, write_default_agent_config
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


class RepoAction(StrEnum):
    add = "add"
    remove = "remove"
    update = "update"


class ManageRepoBody(BaseModel):
    action: RepoAction
    repo_name: str
    url: str | None = None


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
                "tier": tiers[a].value,
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
        try:
            targets = [AgentName(body.agent)]
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail={"code": "unknown_agent", "agent": body.agent},
            )

    async def gen():
        protocol_dir = state.settings.get_protocol_dir()
        prompts = load_all_prompts(protocol_dir)
        ctx = ContextBuilder(state.settings)
        for agent in targets:
            workspace = state.runtime.workspaces_dir / agent.value
            workspace.mkdir(parents=True, exist_ok=True)
            yield {"data": _json.dumps({"agent": agent.value, "phase": "starting"})}
            try:
                # 1. Ensure agent.yaml, then clone any configured repos. The
                #    make-worktree skill assumes `repos/<name>/` already exists,
                #    so this has to run before CLAUDE.md is regenerated (which
                #    lists the available repos).
                write_default_agent_config(workspace)
                repos = load_agent_config(workspace).get("repos") or {}
                for repo_name, url in repos.items():
                    yield {"data": _json.dumps({
                        "agent": agent.value, "phase": "repo_cloning",
                        "repo": repo_name,
                    })}
                    ok = await asyncio.to_thread(
                        ctx.clone_repo, workspace, repo_name, url,
                    )
                    yield {"data": _json.dumps({
                        "agent": agent.value,
                        "phase": "repo_ready" if ok else "repo_failed",
                        "repo": repo_name,
                    })}
                # 2. Write CLAUDE.md / settings.json / copy skills.
                await asyncio.to_thread(
                    ctx.ensure_workspace_ready, workspace, agent.value,
                    prompts.get(agent.value, ""),
                )
                # 3. Create agent-specific folders (specs/, proposals/).
                await asyncio.to_thread(
                    ctx.create_agent_dirs, workspace, agent.value,
                )
            except Exception as exc:  # noqa: BLE001 - surface to client and stop
                # SSE clients otherwise see a silent disconnect — emit an
                # explicit error frame so the caller can distinguish failure
                # from clean completion.
                yield {"data": _json.dumps({
                    "agent": agent.value, "phase": "error", "detail": str(exc),
                })}
                return
            yield {"data": _json.dumps({"agent": agent.value, "phase": "done"})}
        yield {"data": _json.dumps({"phase": "all_done"})}

    return EventSourceResponse(gen())


@router.post("/agents/{agent_name}/repos")
async def manage_repo(agent_name: str, body: ManageRepoBody, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)

    workspace = state.runtime.workspaces_dir / agent_name
    if not workspace.exists():
        raise HTTPException(status_code=404, detail=f"workspace {agent_name!r} not found")

    if body.action in (RepoAction.add, RepoAction.update) and not body.url:
        raise HTTPException(status_code=422, detail=f"url required for {body.action!r}")

    ctx = ContextBuilder(state.settings)
    prompts = load_all_prompts(state.settings.get_protocol_dir())
    agent_prompt = prompts.get(agent_name, "")

    if body.action == RepoAction.add:
        try:
            add_repo(workspace, body.repo_name, body.url)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        await asyncio.to_thread(ctx.clone_repo, workspace, body.repo_name, body.url)

    elif body.action == RepoAction.remove:
        try:
            remove_repo(workspace, body.repo_name)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"repo {body.repo_name!r} not found")
        repo_dir = workspace / "repos" / body.repo_name
        if repo_dir.exists():
            shutil.rmtree(repo_dir)

    elif body.action == RepoAction.update:
        try:
            update_repo_url(workspace, body.repo_name, body.url)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"repo {body.repo_name!r} not found")
        repo_dir = workspace / "repos" / body.repo_name
        if repo_dir.exists():
            shutil.rmtree(repo_dir)
        await asyncio.to_thread(ctx.clone_repo, workspace, body.repo_name, body.url)

    await asyncio.to_thread(
        ctx.ensure_workspace_ready, workspace, agent_name, agent_prompt,
    )
    return {"ok": True}


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

    # Hold the lock across exists/init/append so two concurrent posts can't both
    # see the file as missing and race the header write.
    async with state.db_lock:
        if not learnings_path.exists():
            learnings_path.parent.mkdir(parents=True, exist_ok=True)
            learnings_path.write_text(f"# Learnings: {agent_name}\n\n")
        existing = learnings_path.read_text()
        learnings_path.write_text(existing + f"- {body.text}\n")
    return {"ok": True}
