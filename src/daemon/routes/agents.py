"""Agent inspection, init, and learnings callback endpoints."""
from __future__ import annotations

import asyncio
import json as _json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, field_validator, model_validator
from sse_starlette.sse import EventSourceResponse

from src.daemon.agent_config import (
    add_repo,
    load_agent_config,
    remove_repo,
    set_executor,
    update_repo_url,
    write_default_agent_config,
)
from src.daemon.auth import require_token
from src.daemon.state import DaemonState
from src.infrastructure.audit_logger import AuditLogger
from src.models import PerformanceTier, TalkStatus
from src.orchestrator import prompt_loader
from src.orchestrator.agent_def import AgentDef, AgentParseError
from src.orchestrator.context_builder import ContextBuilder
from src.orchestrator.performance_tracker import PerformanceTracker

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


class ManageAgentAction(StrEnum):
    enroll = "enroll"
    update = "update"
    terminate = "terminate"


class ManageAgentBody(BaseModel):
    action: ManageAgentAction
    name: str
    task_id: str | None = None
    session_id: str | None = None
    talk_id: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    repos: dict[str, str] | None = None
    executor: Literal["claude", "codex"] | None = None
    allow_rules: list[str] | None = None
    target_team: str | None = None

    @field_validator("allow_rules")
    @classmethod
    def _reject_unsafe_allow_rules(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        forbidden = {"\n", "\r", ";", "|", "&", "`", "$("}
        for entry in v:
            if not entry or not entry.strip():
                raise ValueError("allow_rules entries must be non-empty")
            if entry != entry.strip():
                raise ValueError("allow_rules entries must not have leading/trailing whitespace")
            for bad in forbidden:
                if bad in entry:
                    raise ValueError(f"allow_rules entries must not contain {bad!r}")
        return v

    @model_validator(mode="after")
    def _exactly_one_auth_path(self) -> ManageAgentBody:
        task_path = self.task_id is not None and self.session_id is not None
        partial_task = (self.task_id is not None) != (self.session_id is not None)
        talk_path = self.talk_id is not None
        if partial_task:
            raise ValueError("task_id and session_id must be supplied together")
        if task_path and talk_path:
            raise ValueError("supply either (task_id + session_id) or talk_id, not both")
        if not task_path and not talk_path:
            raise ValueError("supply either (task_id + session_id) or talk_id")
        return self


_VALID_AGENT_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def _require_active(state: DaemonState) -> None:
    if state.is_idle:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "no_active_runtime"},
        )


def _require_eh_auth(body: ManageAgentBody, state: DaemonState) -> None:
    """Validate the caller is authorized to run manage-agent as EH.

    Supports two auth paths:
      - Task path: (task_id, session_id) must map to an active
        engineering_head session in SessionTracker.
      - Talk path: talk_id must reference an open talk whose
        agent_name == 'engineering_head'.

    The pydantic validator on ManageAgentBody guarantees exactly one path
    is set, so this function only checks the path that is present.
    """
    if body.talk_id is not None:
        talk = state.db.get_talk(body.talk_id)
        if talk is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"talk {body.talk_id!r} not found",
            )
        if talk.agent_name != "engineering_head":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="manage-agent requires an engineering_head talk",
            )
        if talk.status != TalkStatus.OPEN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"talk {body.talk_id!r} is {talk.status.value}, not open",
            )
        return

    # Task path
    expected = state.sessions.get_active(body.task_id, "engineering_head")
    if expected is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="manage-agent requires an active engineering_head session",
        )
    if expected != body.session_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="session_id does not match the active engineering_head session",
        )


def _require_team_manager_auth(body: ManageAgentBody, state: DaemonState) -> tuple[str, str]:
    """Validate the caller is a team manager and return (manager_name, manager_team).

    Supports the same two auth paths as _require_eh_auth, but accepts *any*
    team manager (not just engineering_head):
      - Talk path: talk_id must reference an open talk whose agent_name is a
        registered team manager.
      - Task path: iterate all team managers, find the one with a matching
        active (task_id, session_id) session.

    Returns (manager_name, manager_team) so callers can enforce team scoping.
    """
    if state.teams is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="manage-agent requires teams registry (no active runtime)",
        )

    if body.talk_id is not None:
        talk = state.db.get_talk(body.talk_id)
        if talk is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"talk {body.talk_id!r} not found",
            )
        if not state.teams.is_team_manager(talk.agent_name):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="manage-agent requires a team-manager talk",
            )
        if talk.status != TalkStatus.OPEN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"talk {body.talk_id!r} is {talk.status.value}, not open",
            )
        manager_team = state.teams.team_for_manager(talk.agent_name)
        assert manager_team is not None  # guaranteed by is_team_manager check above
        return talk.agent_name, manager_team

    # Task path: find the team manager whose active session matches
    for candidate in state.teams.all_agents():
        if not state.teams.is_team_manager(candidate):
            continue
        active = state.sessions.get_active(body.task_id, candidate)
        if active is not None and active == body.session_id:
            manager_team = state.teams.team_for_manager(candidate)
            assert manager_team is not None
            return candidate, manager_team

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="manage-agent requires an active team-manager session",
    )


def _append_to_learnings_file(learnings_path: Path, agent_name: str, text: str) -> None:
    """Append a single learning line to learnings.md, creating the file+header if missing.

    Callers are responsible for serialization (e.g. holding state.db_lock) when
    concurrent writes are possible. The function itself performs no locking.
    """
    if not learnings_path.exists():
        learnings_path.parent.mkdir(parents=True, exist_ok=True)
        learnings_path.write_text(f"# Learnings: {agent_name}\n\n")
    existing = learnings_path.read_text()
    learnings_path.write_text(existing + f"- {text}\n")


@router.get("/agents")
def list_agents(request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)
    tracker = PerformanceTracker(state.db, state.settings)
    ws_dir = state.runtime.workspaces_dir
    if ws_dir.exists():
        agent_names = sorted(d.name for d in ws_dir.iterdir() if d.is_dir())
    else:
        agent_names = []
    tiers = tracker.get_all_tiers(agent_names)
    return {
        "agents": [
            {
                "name": name,
                "tier": tiers.get(name, PerformanceTier.GREEN).value,
                "scorecard": state.db.get_scorecard(name),
            }
            for name in agent_names
        ],
    }


@router.post("/agents/init")
async def init_agents(body: InitBody, request: Request):
    state: DaemonState = request.app.state.daemon
    _require_active(state)

    if body.agent is None:
        ws_dir = state.runtime.workspaces_dir
        known: set[str] = set()
        if state.teams is not None:
            known.update(state.teams.all_agents())
        if ws_dir.exists():
            known.update(d.name for d in ws_dir.iterdir() if d.is_dir())
        known.update([a.name for a in prompt_loader.list_agents(state.runtime)])
        targets = sorted(known)
    else:
        targets = [body.agent]

    async def gen():
        ctx = ContextBuilder(state.settings, state.runtime)
        for agent_name in targets:
            workspace = state.runtime.workspaces_dir / agent_name
            workspace.mkdir(parents=True, exist_ok=True)
            yield {"data": _json.dumps({"agent": agent_name, "phase": "starting"})}
            try:
                had_agent_config = (workspace / "agent.yaml").exists()
                write_default_agent_config(workspace)
                agent_def = prompt_loader.load_agent(state.runtime, agent_name)
                if not had_agent_config and agent_def is not None:
                    set_executor(workspace, agent_def.executor)
                cfg = load_agent_config(workspace)
                provider = cfg.get("executor") or "claude"
                repos = cfg.get("repos") or {}
                for repo_name, url in repos.items():
                    yield {"data": _json.dumps({
                        "agent": agent_name, "phase": "repo_cloning",
                        "repo": repo_name,
                    })}
                    ok = await asyncio.to_thread(
                        ctx.clone_repo, workspace, repo_name, url,
                    )
                    yield {"data": _json.dumps({
                        "agent": agent_name,
                        "phase": "repo_ready" if ok else "repo_failed",
                        "repo": repo_name,
                    })}
                sys_prompt = agent_def.system_prompt if agent_def else ""
                await asyncio.to_thread(
                    ctx.ensure_workspace_ready, workspace, agent_name, sys_prompt,
                    provider=provider,
                )
                await asyncio.to_thread(
                    ctx.create_agent_dirs, workspace, agent_name,
                )
            except Exception as exc:
                yield {"data": _json.dumps({
                    "agent": agent_name, "phase": "error", "detail": str(exc),
                })}
                return
            yield {"data": _json.dumps({"agent": agent_name, "phase": "done"})}
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

    ctx = ContextBuilder(state.settings, state.runtime)
    agent_def = prompt_loader.load_agent(state.runtime, agent_name)
    agent_prompt = agent_def.system_prompt if agent_def else ""

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


@router.post("/agents/manage")
async def manage_agent(body: ManageAgentBody, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)

    # Any team manager may manage agents within their team.
    manager_name, manager_team = _require_team_manager_auth(body, state)

    scope_id = body.talk_id if body.talk_id is not None else body.task_id
    assert scope_id is not None  # guaranteed by ManageAgentBody._exactly_one_auth_path
    source = "talk" if body.talk_id is not None else "task"
    audit = AuditLogger(state.db)

    if not _VALID_AGENT_NAME.match(body.name):
        raise HTTPException(status_code=422, detail=f"invalid agent name: {body.name!r}")

    if body.action == ManageAgentAction.enroll:
        if not body.description or not body.system_prompt:
            raise HTTPException(status_code=422, detail="description and system_prompt required for enroll")
        # Check for duplicate: look in both pending and active.
        if (prompt_loader.load_pending_agent(state.runtime, body.name) is not None
                or prompt_loader.load_agent(state.runtime, body.name) is not None):
            raise HTTPException(status_code=409, detail=f"agent {body.name!r} already enrolled")
        # Validate target_team BEFORE inserting — avoid zombie enrollment files.
        async with state.teams_lock:
            target_team = body.target_team or manager_team
            if target_team != manager_team:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "code": "cross_team_forbidden",
                        "caller_team": manager_team,
                        "requested_team": target_team,
                    },
                )
            agent = AgentDef(
                name=body.name,
                team=target_team,
                role="worker",
                executor=body.executor or "claude",
                allow_rules=tuple(body.allow_rules or []),
                repos=body.repos or {},
                enrolled_by=manager_name,
                enrolled_at_task=body.task_id,
                enrolled_at=datetime.now(timezone.utc),
                system_prompt=body.system_prompt,
                description=body.description,
            )
            prompt_loader.write_pending_agent(state.runtime, agent)
            state.teams.add_worker(manager_team, body.name)
            state.teams.save(state.runtime)
        audit.log_agent_managed(
            scope_id=scope_id,
            action="enroll",
            name=body.name,
            source=source,
            actor=manager_name,
        )
        return {"ok": True, "status": "pending"}

    elif body.action == ManageAgentAction.update:
        existing = prompt_loader.load_agent(state.runtime, body.name)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"agent {body.name!r} not found")
        # Reject cross-team update attempts — hold the lock to prevent a torn
        # read racing against a concurrent terminate.
        async with state.teams_lock:
            agent_team = state.teams.team_for_agent(body.name) if state.teams is not None else None
            if agent_team != manager_team:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "code": "cross_team_forbidden",
                        "caller_team": manager_team,
                        "agent_team": agent_team,
                    },
                )
        # Build the updated AgentDef, preserving fields not being updated.
        updated = AgentDef(
            name=existing.name,
            team=existing.team,
            role=existing.role,
            executor=body.executor or existing.executor,
            allow_rules=tuple(body.allow_rules) if body.allow_rules is not None else existing.allow_rules,
            repos=body.repos if body.repos is not None else existing.repos,
            enrolled_by=existing.enrolled_by,
            enrolled_at_task=existing.enrolled_at_task,
            enrolled_at=existing.enrolled_at,
            system_prompt=body.system_prompt if body.system_prompt is not None else existing.system_prompt,
            description=body.description if body.description is not None else existing.description,
        )
        # Atomic overwrite of the active file via tempfile + os.replace.
        active_path = state.runtime.agents_dir / f"{body.name}.md"
        from src.orchestrator.agent_def import render_agent_text
        fd, tmp = tempfile.mkstemp(
            prefix=f".{body.name}.", suffix=".md",
            dir=str(state.runtime.agents_dir),
        )
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(render_agent_text(updated))
            os.replace(tmp, active_path)
        except Exception:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise
        if body.system_prompt:
            workspace = state.runtime.workspaces_dir / body.name
            if workspace.exists():
                ctx = ContextBuilder(state.settings, state.runtime)
                await asyncio.to_thread(
                    ctx.ensure_workspace_ready, workspace, body.name, body.system_prompt,
                )
        if body.executor is not None:
            workspace = state.runtime.workspaces_dir / body.name
            if workspace.exists():
                await asyncio.to_thread(set_executor, workspace, body.executor)
        audit.log_agent_managed(
            scope_id=scope_id,
            action="update",
            name=body.name,
            source=source,
            actor=manager_name,
        )
        return {"ok": True}

    elif body.action == ManageAgentAction.terminate:
        existing = prompt_loader.load_agent(state.runtime, body.name)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"agent {body.name!r} not found")
        async with state.teams_lock:
            agent_team = state.teams.team_for_agent(body.name) if state.teams is not None else None
            if agent_team != manager_team:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "code": "cross_team_forbidden",
                        "caller_team": manager_team,
                        "agent_team": agent_team,
                    },
                )
            # Unlink file first — if it raises, teams.yaml stays untouched.
            active_path = state.runtime.agents_dir / f"{body.name}.md"
            active_path.unlink(missing_ok=True)
            state.teams.remove_worker(manager_team, body.name)
            state.teams.save(state.runtime)
        workspace = state.runtime.workspaces_dir / body.name
        if workspace.exists():
            shutil.rmtree(workspace)
        audit.log_agent_managed(
            scope_id=scope_id,
            action="terminate",
            name=body.name,
            source=source,
            actor=manager_name,
        )
        return {"ok": True}

    raise HTTPException(status_code=422, detail=f"unknown action: {body.action}")


@router.get("/agents/enrollments")
def list_enrollments(
    request: Request,
    enrollment_status: str | None = Query(default=None, alias="status"),
    team: str | None = Query(default=None),
) -> dict:
    """List enrollments with optional ?status= and/or ?team= filters.

    File-based: pending agents live in _pending/, active in agents_dir/.
    The ?team= filter is voluntary scoping — it does not authenticate the
    caller as a member of that team. Founders always get an unfiltered view
    when neither parameter is supplied.
    """
    state: DaemonState = request.app.state.daemon
    _require_active(state)

    # Collect all enrollments from files.
    all_enrollments: list[dict] = []
    for agent in prompt_loader.list_pending(state.runtime):
        all_enrollments.append({
            "name": agent.name,
            "description": agent.description or "",
            "status": "pending",
            "created_at": agent.enrolled_at.isoformat() if agent.enrolled_at else None,
        })
    for agent in prompt_loader.list_agents(state.runtime):
        all_enrollments.append({
            "name": agent.name,
            "description": agent.description or "",
            "status": "approved",
            "created_at": agent.enrolled_at.isoformat() if agent.enrolled_at else None,
        })

    # Apply status filter.
    if enrollment_status is not None:
        all_enrollments = [e for e in all_enrollments if e["status"] == enrollment_status]

    # Apply team filter.
    if team is not None and state.teams is not None:
        team_agents = {
            agent
            for agent in state.teams.all_agents()
            if state.teams.team_for_agent(agent) == team
        }
        all_enrollments = [e for e in all_enrollments if e["name"] in team_agents]

    return {"enrollments": all_enrollments}


@router.post("/agents/{agent_name}/approve")
async def approve_agent(agent_name: str, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)

    pending = prompt_loader.load_pending_agent(state.runtime, agent_name)
    if pending is None:
        # Check if already approved (active).
        existing = prompt_loader.load_agent(state.runtime, agent_name)
        if existing is not None:
            raise HTTPException(status_code=409, detail=f"agent is approved, not pending")
        raise HTTPException(status_code=404, detail=f"agent {agent_name!r} not found")

    try:
        agent_def = prompt_loader.approve_agent(state.runtime, agent_name)
    except FileExistsError:
        raise HTTPException(status_code=409, detail=f"agent is approved, not pending")

    workspace = state.runtime.workspaces_dir / agent_name
    workspace.mkdir(parents=True, exist_ok=True)
    write_default_agent_config(workspace)
    set_executor(workspace, agent_def.executor)

    repos = agent_def.repos or {}
    if repos:
        for repo_name, url in repos.items():
            add_repo(workspace, repo_name, url)

    ctx = ContextBuilder(state.settings, state.runtime)
    for repo_name, url in repos.items():
        await asyncio.to_thread(ctx.clone_repo, workspace, repo_name, url)

    await asyncio.to_thread(
        ctx.ensure_workspace_ready,
        workspace,
        agent_name,
        agent_def.system_prompt,
        provider=load_agent_config(workspace).get("executor") or "claude",
    )
    await asyncio.to_thread(ctx.create_agent_dirs, workspace, agent_name)

    return {"ok": True}


@router.post("/agents/{agent_name}/reject")
async def reject_agent(agent_name: str, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)

    pending = prompt_loader.load_pending_agent(state.runtime, agent_name)
    if pending is None:
        existing = prompt_loader.load_agent(state.runtime, agent_name)
        if existing is not None:
            raise HTTPException(status_code=409, detail=f"agent is approved, not pending")
        raise HTTPException(status_code=404, detail=f"agent {agent_name!r} not found")

    # Drop the file first; if it's already gone the reject_agent helper raises
    # FileNotFoundError. Holding teams_lock keeps the file-unlink + teams-yaml
    # mutation paired so a concurrent enrollment can't observe a half-state.
    async with state.teams_lock:
        try:
            prompt_loader.reject_agent(state.runtime, agent_name)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"agent {agent_name!r} not found")
        # manage_agent.enroll added this worker to teams.yaml when it wrote the
        # pending file. Reject must undo both — otherwise the agent stays in
        # team membership forever and re-enrollment hits "duplicate" on the
        # team-side too. remove_worker is a no-op if the agent isn't a worker
        # under pending.team, so this is safe even if teams drifted.
        if state.teams is not None and pending.team in state.teams.teams():
            state.teams.remove_worker(pending.team, agent_name)

    return {"ok": True}


@router.post("/agents/backfill-enrollments")
def backfill_enrollments(request: Request) -> dict:
    """Deprecated no-op. Previously imported pre-existing workspaces into the
    SQLite enrollment registry; that registry is replaced by file-based agents.
    Returns a success response with empty lists for backwards compatibility.
    """
    state: DaemonState = request.app.state.daemon
    _require_active(state)
    return {
        "backfilled": [],
        "skipped_already_enrolled": [],
        "skipped_unknown_prompt": [],
        "deprecated": True,
        "note": "Backfill is now done via `opc migrate-to-org-runtime`. Pre-existing workspaces without org/agents/<name>.md should be reconstructed by the founder manually.",
    }


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
        _append_to_learnings_file(learnings_path, agent_name, body.text)
    return {"ok": True}
