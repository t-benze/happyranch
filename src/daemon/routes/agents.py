"""Agent inspection, init, and learnings callback endpoints."""
from __future__ import annotations

import asyncio
import json as _json
import re
import shutil
from enum import StrEnum
from pathlib import Path

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
from src.orchestrator.context_builder import ContextBuilder
from src.orchestrator.performance_tracker import PerformanceTracker
from src.orchestrator.prompt_loader import load_all_prompts, load_system_prompt

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
    executor: str | None = None
    allow_rules: list[str] | None = None

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
        targets = sorted(d.name for d in ws_dir.iterdir() if d.is_dir()) if ws_dir.exists() else []
    else:
        targets = [body.agent]

    async def gen():
        protocol_dir = state.settings.get_protocol_dir()
        prompts = load_all_prompts(protocol_dir)
        ctx = ContextBuilder(state.settings)
        for agent_name in targets:
            workspace = state.runtime.workspaces_dir / agent_name
            workspace.mkdir(parents=True, exist_ok=True)
            yield {"data": _json.dumps({"agent": agent_name, "phase": "starting"})}
            try:
                had_agent_config = (workspace / "agent.yaml").exists()
                write_default_agent_config(workspace)
                enrollment = state.db.get_enrollment(agent_name)
                if not had_agent_config and enrollment is not None:
                    set_executor(workspace, enrollment.get("executor"))
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
                sys_prompt = enrollment["system_prompt"] if enrollment else prompts.get(agent_name, "")
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


@router.post("/agents/manage")
async def manage_agent(body: ManageAgentBody, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)

    # Only the Engineering Head may manage agents (either via task session or open talk).
    _require_eh_auth(body, state)

    scope_id = body.talk_id if body.talk_id is not None else body.task_id
    assert scope_id is not None  # guaranteed by ManageAgentBody._exactly_one_auth_path
    source = "talk" if body.talk_id is not None else "task"
    audit = AuditLogger(state.db)

    if not _VALID_AGENT_NAME.match(body.name):
        raise HTTPException(status_code=422, detail=f"invalid agent name: {body.name!r}")

    if body.action == ManageAgentAction.enroll:
        if not body.description or not body.system_prompt:
            raise HTTPException(status_code=422, detail="description and system_prompt required for enroll")
        if state.db.get_enrollment(body.name) is not None:
            raise HTTPException(status_code=409, detail=f"agent {body.name!r} already enrolled")
        state.db.insert_enrollment(
            name=body.name,
            description=body.description,
            system_prompt=body.system_prompt,
            repos=body.repos,
            executor=body.executor,
            allow_rules=body.allow_rules or [],
        )
        audit.log_agent_managed(
            scope_id=scope_id,
            action="enroll",
            name=body.name,
            source=source,
        )
        return {"ok": True, "status": "pending"}

    elif body.action == ManageAgentAction.update:
        enrollment = state.db.get_enrollment(body.name)
        if enrollment is None:
            raise HTTPException(status_code=404, detail=f"agent {body.name!r} not found")
        if enrollment["status"] != "approved":
            raise HTTPException(status_code=409, detail=f"agent {body.name!r} is {enrollment['status']}, not approved")
        state.db.update_enrollment_fields(
            body.name,
            description=body.description,
            system_prompt=body.system_prompt,
            repos=body.repos,
            executor=body.executor,
        )
        if body.system_prompt:
            workspace = state.runtime.workspaces_dir / body.name
            if workspace.exists():
                ctx = ContextBuilder(state.settings)
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
        )
        return {"ok": True}

    elif body.action == ManageAgentAction.terminate:
        enrollment = state.db.get_enrollment(body.name)
        if enrollment is None:
            raise HTTPException(status_code=404, detail=f"agent {body.name!r} not found")
        if enrollment["status"] != "approved":
            raise HTTPException(status_code=409, detail=f"agent {body.name!r} is {enrollment['status']}, not approved")
        state.db.update_enrollment_status(body.name, "terminated")
        workspace = state.runtime.workspaces_dir / body.name
        if workspace.exists():
            shutil.rmtree(workspace)
        audit.log_agent_managed(
            scope_id=scope_id,
            action="terminate",
            name=body.name,
            source=source,
        )
        return {"ok": True}

    raise HTTPException(status_code=422, detail=f"unknown action: {body.action}")


@router.get("/agents/enrollments")
def list_enrollments(
    request: Request,
    enrollment_status: str | None = Query(default=None, alias="status"),
) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)
    enrollments = state.db.list_enrollments(status=enrollment_status)
    return {"enrollments": [
        {"name": e["name"], "description": e["description"], "status": e["status"],
         "created_at": e["created_at"]}
        for e in enrollments
    ]}


@router.post("/agents/{agent_name}/approve")
async def approve_agent(agent_name: str, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)
    enrollment = state.db.get_enrollment(agent_name)
    if enrollment is None:
        raise HTTPException(status_code=404, detail=f"agent {agent_name!r} not found")
    if enrollment["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"agent is {enrollment['status']}, not pending")

    state.db.update_enrollment_status(agent_name, "approved")

    workspace = state.runtime.workspaces_dir / agent_name
    workspace.mkdir(parents=True, exist_ok=True)
    write_default_agent_config(workspace)
    set_executor(workspace, enrollment["executor"])

    repos = _json.loads(enrollment["repos"]) if enrollment["repos"] else {}
    if repos:
        for repo_name, url in repos.items():
            add_repo(workspace, repo_name, url)

    ctx = ContextBuilder(state.settings)
    for repo_name, url in repos.items():
        await asyncio.to_thread(ctx.clone_repo, workspace, repo_name, url)

    await asyncio.to_thread(
        ctx.ensure_workspace_ready,
        workspace,
        agent_name,
        enrollment["system_prompt"],
        provider=load_agent_config(workspace).get("executor") or "claude",
    )
    await asyncio.to_thread(ctx.create_agent_dirs, workspace, agent_name)

    return {"ok": True}


@router.post("/agents/backfill-enrollments")
def backfill_enrollments(request: Request) -> dict:
    """Founder recovery op: import pre-existing workspaces into the enrollment
    registry so `manage-agent update`/`terminate` can target them.

    Scans `workspaces_dir`, and for each agent that has a workspace on disk
    but no enrollment row, inserts a row with status='approved' (bypassing
    the enroll→approve flow, which would re-clone repos we already have).

    Scope is intentionally narrow:
      - Never mutates workspace files — registry-only.
      - Skips agents already in the enrollment registry (any status).
      - Skips workspaces whose name isn't in the protocol prompt loader
        — those prompts can't be reconstructed without human input.
      - `description` is a placeholder pointing at provenance; the real role
        lives in `system_prompt` and EH can improve the description via
        `manage-agent update` later.
    """
    state: DaemonState = request.app.state.daemon
    _require_active(state)

    audit = AuditLogger(state.db)
    ws_dir = state.runtime.workspaces_dir
    if not ws_dir.exists():
        return {"backfilled": [], "skipped_already_enrolled": [], "skipped_unknown_prompt": []}

    protocol_dir = state.settings.get_protocol_dir()
    backfilled: list[dict] = []
    skipped_already: list[str] = []
    skipped_unknown: list[str] = []

    for entry in sorted(ws_dir.iterdir()):
        if not entry.is_dir():
            continue
        name = entry.name
        if state.db.get_enrollment(name) is not None:
            skipped_already.append(name)
            continue
        sys_prompt = load_system_prompt(protocol_dir, name)
        if not sys_prompt:
            skipped_unknown.append(name)
            continue
        cfg = load_agent_config(entry)
        repos = cfg.get("repos") or {}
        executor = cfg.get("executor") or "claude"
        description = (
            f"Pre-existing agent backfilled from protocol "
            f"(source: {protocol_dir.name})."
        )
        state.db.insert_enrollment(
            name=name,
            description=description,
            system_prompt=sys_prompt,
            repos=repos,
            executor=executor,
            status="approved",
        )
        audit.log_agent_backfilled(
            name=name,
            repos_count=len(repos),
            executor=executor,
        )
        backfilled.append({"name": name, "executor": executor, "repos_count": len(repos)})

    return {
        "backfilled": backfilled,
        "skipped_already_enrolled": skipped_already,
        "skipped_unknown_prompt": skipped_unknown,
    }


@router.post("/agents/{agent_name}/reject")
def reject_agent(agent_name: str, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)
    enrollment = state.db.get_enrollment(agent_name)
    if enrollment is None:
        raise HTTPException(status_code=404, detail=f"agent {agent_name!r} not found")
    if enrollment["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"agent is {enrollment['status']}, not pending")
    state.db.update_enrollment_status(agent_name, "rejected")
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
        _append_to_learnings_file(learnings_path, agent_name, body.text)
    return {"ok": True}
