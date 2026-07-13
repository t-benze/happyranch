"""Agent inspection, init, and learnings callback endpoints."""
from __future__ import annotations

import asyncio
import json as _json
import logging
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, field_validator, model_validator
from sse_starlette.sse import EventSourceResponse

from runtime.daemon.agent_config import (
    load_agent_config,
)
from runtime.daemon.auth import require_token
from runtime.daemon.org_state import OrgState
from runtime.daemon.routes._org_dep import OrgDep
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.kb_store import KBStore, InvalidSlug
from runtime.infrastructure.learnings_store import (
    InvalidLearningEntry,
    InvalidLearningId,
    LearningIdExists,
    LearningNotFound,
    LearningSearchHit,
    LearningSlugExists,
    MemoryCompactionPolicy,
    MemoryItem,
    MemoryStore,
    PromotedLocked,
)
from runtime.infrastructure.memory_migration import migrate_workspace
from runtime.orchestrator import prompt_loader
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.org_config import load_org_config
from runtime.orchestrator.agent_def import AgentDef, AgentParseError, Executor
from runtime.orchestrator.context_builder import ContextBuilder
from runtime.orchestrator.workspace_adapters import (
    SystemContractMaterializationError,
    ensure_system_contracts_materialized,
)

router = APIRouter(dependencies=[require_token()])


class InitBody(BaseModel):
    agent: str | None = None


class LearningBody(BaseModel):
    session_id: str
    task_id: str
    text: str


_ALLOW_RULES_FORBIDDEN = ("\n", "\r", ";", "|", "&", "`", "$(")


def _validate_allow_rules(values: list[str] | None) -> list[str] | None:
    """Shared validator for ``allow_rules`` arrays.

    Rejects entries containing shell metacharacters that could break the
    Claude/opencode permission matcher. Returns the original list (or None)
    unchanged on success; raises ``ValueError`` on the first bad entry.
    """
    if values is None:
        return values
    for entry in values:
        if not entry or not entry.strip():
            raise ValueError("allow_rules entries must be non-empty")
        if entry != entry.strip():
            raise ValueError("allow_rules entries must not have leading/trailing whitespace")
        for bad in _ALLOW_RULES_FORBIDDEN:
            if bad in entry:
                raise ValueError(f"allow_rules entries must not contain {bad!r}")
    return values


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
    description: str | None = None
    system_prompt: str | None = None
    repos: dict[str, str] | None = None
    executor: str | None = None
    model: str | None = None
    allow_rules: list[str] | None = None
    target_team: str | None = None

    @field_validator("allow_rules")
    @classmethod
    def _reject_unsafe_allow_rules(cls, v: list[str] | None) -> list[str] | None:
        return _validate_allow_rules(v)

    @model_validator(mode="after")
    def _exactly_one_auth_path(self) -> ManageAgentBody:
        task_path = self.task_id is not None and self.session_id is not None
        partial_task = (self.task_id is not None) != (self.session_id is not None)
        if partial_task:
            raise ValueError("task_id and session_id must be supplied together")
        if not task_path:
            raise ValueError("supply task_id and session_id")
        return self


class FounderCreateAgentBody(BaseModel):
    name: str
    role: Literal["worker", "manager"]
    team: str | None = None
    new_team: str | None = None
    executor: str = "claude"
    model: str | None = None
    description: str
    system_prompt: str
    allow_rules: list[str] | None = None
    repos: dict[str, str] | None = None

    @field_validator("allow_rules")
    @classmethod
    def _reject_unsafe_allow_rules(cls, v: list[str] | None) -> list[str] | None:
        return _validate_allow_rules(v)


_VALID_AGENT_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def _require_team_manager_auth(body: ManageAgentBody, org: OrgState) -> tuple[str, str]:
    """Validate the caller is a team manager and return (manager_name, manager_team).

    Iterates all team managers, finds the one with a matching active
    (task_id, session_id) session.

    Returns (manager_name, manager_team) so callers can enforce team scoping.
    """
    if org.teams is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="manage-agent requires teams registry (no active runtime)",
        )

    # Find the team manager whose active session matches
    for candidate in org.teams.all_agents():
        if not org.teams.is_team_manager(candidate):
            continue
        active = org.sessions.get_active(body.task_id, candidate)
        if active is not None and active == body.session_id:
            manager_team = org.teams.team_for_manager(candidate)
            assert manager_team is not None
            return candidate, manager_team

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="manage-agent requires an active team-manager session",
    )


def _append_to_learnings_file(learnings_path: Path, agent_name: str, text: str) -> None:
    """Append a single learning line to learnings.md, creating the file+header if missing.

    Callers are responsible for serialization (e.g. holding org.db_lock) when
    concurrent writes are possible. The function itself performs no locking.
    """
    if not learnings_path.exists():
        learnings_path.parent.mkdir(parents=True, exist_ok=True)
        learnings_path.write_text(f"# Learnings: {agent_name}\n\n")
    existing = learnings_path.read_text()
    learnings_path.write_text(existing + f"- {text}\n")


def _resolve_agent_model(paths: OrgPaths, agent_name: str) -> str | None:
    """Resolve the per-agent model from org/agents/<name>.md frontmatter.

    THR-095: AgentDef.model is the SINGLE authoritative store.
    The agent.yaml ladder has been REMOVED per founder ruling (option B).
    """
    agent_def = prompt_loader.load_agent(paths, agent_name)
    return agent_def.model if agent_def else None


@router.get("/agents")
def list_agents(slug: str, org: OrgDep) -> dict:
    paths = OrgPaths(root=org.root)
    ws_dir = paths.workspaces_dir
    if ws_dir.exists():
        agent_names = sorted(d.name for d in ws_dir.iterdir() if d.is_dir())
    else:
        agent_names = []
    rows = []
    for name in agent_names:
        agent_def = prompt_loader.load_agent(paths, name)
        # THR-095: repos are read from AgentDef.repos (org/agents/<name>.md).
        # agent.yaml is no longer the source for repos.
        repos = dict(agent_def.repos) if agent_def else {}
        rows.append({
            "name": name,
            "team": agent_def.team if agent_def else None,
            "role": agent_def.role if agent_def else None,
            "executor": agent_def.executor if agent_def else None,
            "model": _resolve_agent_model(paths, name),
            "description": agent_def.description if agent_def else None,
            # Phase 2: additive read-only fields (D6 spec)
            "repos": repos,
            "system_prompt": agent_def.system_prompt if agent_def else "",
        })
    return {"agents": rows}


@router.post("/agents/init")
async def init_agents(slug: str, body: InitBody, org: OrgDep):
    paths = OrgPaths(root=org.root)

    if body.agent is None:
        ws_dir = paths.workspaces_dir
        known: set[str] = set()
        if org.teams is not None:
            known.update(org.teams.all_agents())
        if ws_dir.exists():
            known.update(d.name for d in ws_dir.iterdir() if d.is_dir())
        known.update([a.name for a in prompt_loader.list_agents(paths)])
        targets = sorted(known)
    else:
        targets = [body.agent]

    async def gen():
        ctx = ContextBuilder(org.settings, paths, slug=org.slug)
        for agent_name in targets:
            workspace = paths.workspaces_dir / agent_name
            workspace.mkdir(parents=True, exist_ok=True)
            yield {"data": _json.dumps({"agent": agent_name, "phase": "starting"})}
            try:
                agent_def = prompt_loader.load_agent(paths, agent_name)
                # THR-095: agent.yaml is no longer the source for
                # executor/repos.  Read both from AgentDef (.md frontmatter).
                provider = agent_def.executor if agent_def else "claude"
                repos = dict(agent_def.repos) if agent_def else {}
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
async def manage_repo(
    slug: str, agent_name: str, body: ManageRepoBody, org: OrgDep,
) -> dict:
    paths = OrgPaths(root=org.root)
    workspace = paths.workspaces_dir / agent_name
    if not workspace.exists():
        raise HTTPException(status_code=404, detail=f"workspace {agent_name!r} not found")

    if body.action in (RepoAction.add, RepoAction.update) and not body.url:
        raise HTTPException(status_code=422, detail=f"url required for {body.action!r}")

    ctx = ContextBuilder(org.settings, paths, slug=org.slug)
    agent_def = prompt_loader.load_agent(paths, agent_name)
    if agent_def is None:
        raise HTTPException(status_code=404, detail=f"agent {agent_name!r} not found")
    agent_prompt = agent_def.system_prompt

    # THR-095: persist repos to org/agents/<name>.md frontmatter ONLY
    # (single source of truth).  agent.yaml is no longer the repo store.
    from runtime.orchestrator.agent_def import render_agent_text
    new_repos = dict(agent_def.repos)

    if body.action == RepoAction.add:
        if body.repo_name in new_repos:
            raise HTTPException(status_code=409, detail=f"repo {body.repo_name!r} already exists")
        new_repos[body.repo_name] = body.url
    elif body.action == RepoAction.remove:
        if body.repo_name not in new_repos:
            raise HTTPException(status_code=404, detail=f"repo {body.repo_name!r} not found")
        del new_repos[body.repo_name]
    elif body.action == RepoAction.update:
        if body.repo_name not in new_repos:
            raise HTTPException(status_code=404, detail=f"repo {body.repo_name!r} not found")
        new_repos[body.repo_name] = body.url

    # Atomic write the updated .md
    updated = AgentDef(
        name=agent_def.name,
        team=agent_def.team,
        role=agent_def.role,
        executor=agent_def.executor,
        allow_rules=agent_def.allow_rules,
        repos=new_repos,
        enrolled_by=agent_def.enrolled_by,
        enrolled_at_task=agent_def.enrolled_at_task,
        enrolled_at=agent_def.enrolled_at,
        system_prompt=agent_def.system_prompt,
        description=agent_def.description,
        model=agent_def.model,
    )
    active_path = paths.agents_dir / f"{agent_name}.md"
    fd, tmp = tempfile.mkstemp(
        prefix=f".{agent_name}.", suffix=".md", dir=str(paths.agents_dir),
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

    # Clone/remove repo dir as before
    if body.action == RepoAction.add:
        await asyncio.to_thread(ctx.clone_repo, workspace, body.repo_name, body.url)
    elif body.action == RepoAction.remove:
        repo_dir = workspace / "repos" / body.repo_name
        if repo_dir.exists():
            shutil.rmtree(repo_dir)
    elif body.action == RepoAction.update:
        repo_dir = workspace / "repos" / body.repo_name
        if repo_dir.exists():
            shutil.rmtree(repo_dir)
        await asyncio.to_thread(ctx.clone_repo, workspace, body.repo_name, body.url)

    await asyncio.to_thread(
        ctx.ensure_workspace_ready, workspace, agent_name, agent_prompt,
        provider=agent_def.executor,
    )
    return {"ok": True}


@router.post("/agents/manage")
async def manage_agent(slug: str, body: ManageAgentBody, org: OrgDep) -> dict:
    paths = OrgPaths(root=org.root)

    # Any team manager may manage agents within their team.
    manager_name, manager_team = _require_team_manager_auth(body, org)

    scope_id = body.task_id
    assert scope_id is not None  # guaranteed by ManageAgentBody validation
    source = "task"
    audit = AuditLogger(org.db)

    if not _VALID_AGENT_NAME.match(body.name):
        raise HTTPException(status_code=422, detail=f"invalid agent name: {body.name!r}")

    if body.action == ManageAgentAction.enroll:
        if not body.description or not body.system_prompt:
            raise HTTPException(status_code=422, detail="description and system_prompt required for enroll")
        _validate_executor(body.executor or "claude")
        # Check for duplicate: look in both pending and active.
        if (prompt_loader.load_pending_agent(paths, body.name) is not None
                or prompt_loader.load_agent(paths, body.name) is not None):
            raise HTTPException(status_code=409, detail=f"agent {body.name!r} already enrolled")
        # Validate target_team BEFORE inserting — avoid zombie enrollment files.
        async with org.teams_lock:
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
                model=body.model if body.model else None,
            )
            prompt_loader.write_pending_agent(paths, agent)
            org.teams.add_worker(manager_team, body.name)
        audit.log_agent_managed(
            scope_id=scope_id,
            action="enroll",
            name=body.name,
            source=source,
            actor=manager_name,
        )
        return {"ok": True, "status": "pending"}

    elif body.action == ManageAgentAction.update:
        existing = prompt_loader.load_agent(paths, body.name)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"agent {body.name!r} not found")
        # Reject cross-team update attempts — hold the lock to prevent a torn
        # read racing against a concurrent terminate.
        async with org.teams_lock:
            agent_team = org.teams.team_for_agent(body.name) if org.teams is not None else None
            if agent_team != manager_team:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "code": "cross_team_forbidden",
                        "caller_team": manager_team,
                        "agent_team": agent_team,
                    },
                )
        if body.executor is not None:
            _validate_executor(body.executor)
        # Build the updated AgentDef, preserving fields not being updated.
        # model: use Pydantic field-set detection to distinguish omitted
        # (preserve existing) vs explicit null (clear).
        model_is_set = "model" in body.model_fields_set
        if model_is_set:
            resolved_model = body.model if body.model else None
        else:
            resolved_model = existing.model
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
            model=resolved_model,
        )
        # Atomic overwrite of the active file via tempfile + os.replace.
        active_path = paths.agents_dir / f"{body.name}.md"
        from runtime.orchestrator.agent_def import render_agent_text
        fd, tmp = tempfile.mkstemp(
            prefix=f".{body.name}.", suffix=".md",
            dir=str(paths.agents_dir),
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
        workspace = paths.workspaces_dir / body.name
        if workspace.exists() and (body.system_prompt or body.executor is not None):
            # Reconcile the workspace bootstrap for the (possibly new) executor
            # profile. Use the preserved or updated system prompt so the
            # bootstrap files reflect the current agent definition — not only
            # the caller-supplied body.system_prompt.
            ctx = ContextBuilder(org.settings, paths, slug=org.slug)
            await asyncio.to_thread(
                ctx.ensure_workspace_ready,
                workspace,
                body.name,
                updated.system_prompt,
                provider=updated.executor,
            )
        # THR-095: agent.yaml executor/model sync REMOVED.
        # The .md frontmatter is the single source of truth.
        audit.log_agent_managed(
            scope_id=scope_id,
            action="update",
            name=body.name,
            source=source,
            actor=manager_name,
        )
        return {"ok": True}

    elif body.action == ManageAgentAction.terminate:
        existing = prompt_loader.load_agent(paths, body.name)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"agent {body.name!r} not found")
        async with org.teams_lock:
            agent_team = org.teams.team_for_agent(body.name) if org.teams is not None else None
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
            active_path = paths.agents_dir / f"{body.name}.md"
            active_path.unlink(missing_ok=True)
            org.teams.remove_worker(manager_team, body.name)
        workspace = paths.workspaces_dir / body.name
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


@router.post("/agents")
async def founder_create_agent(
    slug: str, body: FounderCreateAgentBody, org: OrgDep,
) -> dict:
    """Founder-driven enroll. Lands the agent ACTIVE immediately (no
    pending hop). Worker: assigned to an existing team. Manager: creates
    a new team in teams.yaml as part of the same call.

    If workspace bootstrap (clone_repo / ensure_workspace_ready /
    create_agent_dirs) fails mid-flight, the agent file and teams.yaml
    entry are RETAINED — matches ``approve_agent``'s semantics. Use
    ``manage-agent terminate`` to clean up before retrying.
    """
    paths = OrgPaths(root=org.root)

    # ---- validation ----
    if not _VALID_AGENT_NAME.match(body.name):
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_agent_name", "name": body.name},
        )
    if not body.description.strip() or not body.system_prompt.strip():
        raise HTTPException(
            status_code=422,
            detail={"code": "missing_required_field"},
        )
    _validate_executor(body.executor)
    if body.role == "worker":
        if not body.team or body.new_team:
            raise HTTPException(
                status_code=422,
                detail={"code": "role_team_mismatch"},
            )
    else:  # manager
        if not body.new_team or body.team:
            raise HTTPException(
                status_code=422,
                detail={"code": "role_team_mismatch"},
            )

    # ---- team mutation + agent file write, under the same lock ----
    async with org.teams_lock:
        # Duplicate check inside the lock to close TOCTOU between check + write.
        if (prompt_loader.load_pending_agent(paths, body.name) is not None
                or prompt_loader.load_agent(paths, body.name) is not None):
            raise HTTPException(
                status_code=409,
                detail={"code": "agent_exists", "name": body.name},
            )

        if body.role == "worker":
            assert body.team is not None
            if body.team not in org.teams.teams():
                raise HTTPException(
                    status_code=404,
                    detail={"code": "unknown_team", "team": body.team},
                )
            team_name = body.team
            org.teams.add_worker(team_name, body.name)
        else:
            assert body.new_team is not None
            if body.new_team in org.teams.teams():
                raise HTTPException(
                    status_code=409,
                    detail={"code": "team_exists", "team": body.new_team},
                )
            team_name = body.new_team
            try:
                org.teams.add_team(team_name, manager=body.name)
            except ValueError:
                # Defense in depth — the in-lock check above should make this
                # unreachable, but if a future refactor drifts, surface as 409
                # rather than a bare 500.
                raise HTTPException(
                    status_code=409,
                    detail={"code": "team_exists", "team": body.new_team},
                )

        agent_def = AgentDef(
            name=body.name,
            team=team_name,
            role=body.role,
            executor=body.executor,
            allow_rules=tuple(body.allow_rules or []),
            repos=body.repos or {},
            enrolled_by="founder",
            enrolled_at_task=None,
            enrolled_at=datetime.now(timezone.utc),
            system_prompt=body.system_prompt,
            description=body.description,
            model=body.model if body.model else None,
        )

        # Atomic write directly into active agents/ (skip _pending/).
        from runtime.orchestrator.agent_def import render_agent_text
        paths.agents_dir.mkdir(parents=True, exist_ok=True)
        active_path = paths.agents_dir / f"{body.name}.md"
        fd, tmp = tempfile.mkstemp(
            prefix=f".{body.name}.", suffix=".md",
            dir=str(paths.agents_dir),
        )
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(render_agent_text(agent_def))
            os.replace(tmp, active_path)
        except Exception:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            # Roll back the registry mutation (add_worker or add_team)
            # so we don't leave a phantom team-membership entry without
            # the corresponding agent file. Without this rollback the
            # manager-branch case is unrecoverable on retry (returns
            # 409 team_exists even though no manager file ever landed).
            if body.role == "worker":
                org.teams.remove_worker(team_name, body.name)
            else:
                org.teams.remove_team(team_name)
            raise

    # ---- workspace bootstrap (THR-095: no agent.yaml writes) ----
    workspace = paths.workspaces_dir / body.name
    workspace.mkdir(parents=True, exist_ok=True)

    # THR-095: agent.yaml is no longer the source for executor/repos.
    # The .md frontmatter (AgentDef) is the single source of truth.
    repos = agent_def.repos or {}
    ctx = ContextBuilder(org.settings, paths, slug=org.slug)
    for repo_name, url in repos.items():
        await asyncio.to_thread(ctx.clone_repo, workspace, repo_name, url)
    await asyncio.to_thread(
        ctx.ensure_workspace_ready,
        workspace,
        body.name,
        agent_def.system_prompt,
        provider=agent_def.executor,
    )
    await asyncio.to_thread(ctx.create_agent_dirs, workspace, body.name)

    AuditLogger(org.db).log_agent_managed(
        scope_id="founder",
        action="enroll",
        name=body.name,
        source="founder",
        actor="founder",
    )
    return {"name": body.name, "team": team_name, "role": body.role}


# ---------------------------------------------------------------------------
# Founder surface: switch an existing agent's executor end-to-end.
# ---------------------------------------------------------------------------

# Executor validation is now registry-driven (THR-052). The registry
# singleton is the single source of truth for which executors are valid.
# We derive _VALID_EXECUTORS lazily to avoid a module-level import cycle.
def _get_valid_executors() -> tuple[str, ...]:
    from runtime.orchestrator.executor_registry import get_registry as _gr
    return tuple(_gr().list_profile_names())

_VALID_EXECUTORS: tuple[str, ...] = ()  # populated lazily

# Claude-only workspace files that go stale when an agent switches AWAY from
# the Claude executor: the new adapter writes AGENTS.md/.agents/ and never
# removes these, so they linger unused. (.claude holds settings.json + skills.)
_CLAUDE_ONLY_WORKSPACE_FILES: tuple[str, ...] = ("CLAUDE.md", ".claude")


class SetExecutorBody(BaseModel):
    executor: str
    clean: bool = False


def _validate_executor(executor: str) -> None:
    """Reject an unregistered executor with an actionable error.

    Raises HTTPException(422) listing the registered values. Kept as a standalone
    helper so the validation is unit-testable without an HTTP round trip.
    """
    from runtime.orchestrator.executor_registry import get_registry as _gr
    registry = _gr()
    if not registry.is_registered(executor):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_executor",
                "got": executor,
                "valid": registry.list_profile_names(),
            },
        )


@router.put("/agents/{agent_name}/executor")
async def set_agent_executor(
    slug: str, agent_name: str, body: SetExecutorBody, org: OrgDep,
) -> dict:
    """Founder action: switch an existing agent's executor end-to-end.

    Reconciles all three surfaces the orchestrator reads:
      1. org agent .md frontmatter (``executor:``) — atomic rebuild via
         render_agent_text + tempfile + os.replace (same pattern as the
         manage-agent update path).
      2. workspace agent.yaml — via set_executor (what _resolve_executor_name
         actually reads at dispatch time).
      3. executor bootstrap — via ContextBuilder.ensure_workspace_ready with
         ``provider=<NEW executor>`` so the correct adapter regenerates
         (Claude → CLAUDE.md/.claude/; others → AGENTS.md/.agents/).

    Stale-file handling (away-from-Claude only): switching off Claude leaves
    CLAUDE.md and .claude/ behind. By default these are WARNED about, not
    deleted; ``clean=True`` opts into deleting them. Never auto-deletes.
    """
    paths = OrgPaths(root=org.root)

    _validate_executor(body.executor)

    existing = prompt_loader.load_agent(paths, agent_name)
    if existing is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "agent_not_found", "agent": agent_name},
        )

    workspace = paths.workspaces_dir / agent_name
    has_workspace = workspace.exists()

    before_org = existing.executor
    before_ws = load_agent_config(workspace).get("executor") if has_workspace else None

    # 1. org .md frontmatter — atomic overwrite via tempfile + os.replace.
    updated = AgentDef(
        name=existing.name,
        team=existing.team,
        role=existing.role,
        executor=body.executor,  # type: ignore[arg-type]
        allow_rules=existing.allow_rules,
        repos=existing.repos,
        enrolled_by=existing.enrolled_by,
        enrolled_at_task=existing.enrolled_at_task,
        enrolled_at=existing.enrolled_at,
        system_prompt=existing.system_prompt,
        description=existing.description,
        model=existing.model,
    )
    from runtime.orchestrator.agent_def import render_agent_text
    active_path = paths.agents_dir / f"{agent_name}.md"
    fd, tmp = tempfile.mkstemp(
        prefix=f".{agent_name}.", suffix=".md", dir=str(paths.agents_dir),
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

    after_ws = before_ws
    stale_files: list[str] = []
    removed: list[str] = []
    cleaned = False
    materialization_errors: list[str] = []

    if has_workspace:
        # THR-095: agent.yaml is no longer the source — skip the
        # agent.yaml reconcile.  The .md frontmatter is the single
        # source of truth.
        after_ws = before_ws
        # regenerate the executor bootstrap with the NEW provider.
        ctx = ContextBuilder(org.settings, paths, slug=org.slug)
        await asyncio.to_thread(
            ctx.ensure_workspace_ready,
            workspace,
            agent_name,
            existing.system_prompt,
            provider=body.executor,
        )
        # 5. Materialize system contracts for ALL 4 session contexts so
        #    skills are present the INSTANT the switch completes (not one
        #    session later).  Complements #378's spawn precondition.
        #
        #    Decision (1): No single context is a strict superset — 'task'
        #    misses 'dream'; 'dream' misses 'start-task' and 'thread'.
        #    Loop over all 4 to guarantee every contract any future session
        #    could need is on disk.
        #
        #    Decision (2): Failure is NON-FATAL — steps 1-3 have already
        #    mutated org .md irreversibly; #378 guarantees
        #    correctness at next spawn regardless.  Surface errors in the
        #    response body + warning log.
        #
        #    Decision (3): provider=body.executor matches existing step-3
        #    convention.  Standard claude/codex/opencode/pi names route to
        #    the correct .claude/ or .agents/ skills root.  A custom profile
        #    on the claude adapter with a non-"claude" name would mis-route
        #    the verify path — flagged, out of scope for this change.
        materialization_errors = []
        _logger = logging.getLogger(__name__)
        for ctx in ("task", "thread", "wake", "dream"):
            try:
                await asyncio.to_thread(
                    ensure_system_contracts_materialized,
                    workspace,
                    org.settings,
                    slug=org.slug,
                    context=ctx,
                    provider=body.executor,
                )
            except SystemContractMaterializationError as e:
                materialization_errors.append(str(e))
                _logger.warning(
                    "Executor switch: system contract materialization "
                    "failed for context=%s provider=%s agent=%s: %s",
                    ctx, body.executor, agent_name, e,
                )

        # 4. stale Claude-only files when switching AWAY from a Claude
        #    adapter. Check the profile's adapter_id, not the name — a
        #    custom profile might use the pi adapter but not be named "pi".
        from runtime.orchestrator.executor_registry import get_registry as _gr
        profile = _gr().get_profile(body.executor)
        if profile is not None and profile.adapter_id != "claude":
            stale_files = [
                name for name in _CLAUDE_ONLY_WORKSPACE_FILES
                if (workspace / name).exists()
            ]
            if stale_files and body.clean:
                for name in stale_files:
                    target = workspace / name
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                    removed.append(name)
                cleaned = True

    AuditLogger(org.db).log_agent_managed(
        scope_id="founder",
        action="update",
        name=agent_name,
        source="founder",
        actor="founder",
    )

    return {
        "agent": agent_name,
        "before": {"org_executor": before_org, "workspace_executor": before_ws},
        "after": {"org_executor": body.executor, "workspace_executor": after_ws},
        "stale_files": stale_files,
        "cleaned": cleaned,
        "removed": removed,
        "materialization_errors": materialization_errors,
    }


# ---------------------------------------------------------------------------
# Founder surface: set an existing agent's model end-to-end.
# ---------------------------------------------------------------------------


class SetModelBody(BaseModel):
    model: str | None = None


@router.put("/agents/{agent_name}/model")
async def set_agent_model(
    slug: str, agent_name: str, body: SetModelBody, org: OrgDep,
) -> dict:
    """Founder action: set or clear an existing agent's model.

    THR-095: writes to org/agents/<name>.md frontmatter ONLY.
    agent.yaml is no longer the source — the .md is the single
    source of truth.
    """
    paths = OrgPaths(root=org.root)

    existing = prompt_loader.load_agent(paths, agent_name)
    if existing is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "agent_not_found", "agent": agent_name},
        )

    workspace = paths.workspaces_dir / agent_name
    has_workspace = workspace.exists()

    before_model = _resolve_agent_model(paths, agent_name)

    # 1. org .md frontmatter — atomic overwrite via tempfile + os.replace.
    updated = AgentDef(
        name=existing.name,
        team=existing.team,
        role=existing.role,
        executor=existing.executor,  # type: ignore[arg-type]
        allow_rules=existing.allow_rules,
        repos=existing.repos,
        enrolled_by=existing.enrolled_by,
        enrolled_at_task=existing.enrolled_at_task,
        enrolled_at=existing.enrolled_at,
        system_prompt=existing.system_prompt,
        description=existing.description,
        model=body.model if body.model else None,
    )
    from runtime.orchestrator.agent_def import render_agent_text
    active_path = paths.agents_dir / f"{agent_name}.md"
    fd, tmp = tempfile.mkstemp(
        prefix=f".{agent_name}.", suffix=".md", dir=str(paths.agents_dir),
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

    after_model = _resolve_agent_model(paths, agent_name)

    AuditLogger(org.db).log_agent_managed(
        scope_id="founder",
        action="update",
        name=agent_name,
        source="founder",
        actor="founder",
    )

    return {
        "agent": agent_name,
        "before": before_model,
        "after": after_model,
    }


@router.get("/agents/enrollments")
def list_enrollments(
    slug: str,
    org: OrgDep,
    enrollment_status: str | None = Query(default=None, alias="status"),
    team: str | None = Query(default=None),
) -> dict:
    """List enrollments with optional ?status= and/or ?team= filters.

    File-based: pending agents live in _pending/, active in agents_dir/.
    The ?team= filter is voluntary scoping — it does not authenticate the
    caller as a member of that team. Founders always get an unfiltered view
    when neither parameter is supplied.
    """
    paths = OrgPaths(root=org.root)

    # Collect all enrollments from files. `team` and `role` come from the
    # parsed AgentDef so the founder UI can render the same shape as the
    # active-agents table without a second roundtrip.
    all_enrollments: list[dict] = []
    for agent in prompt_loader.list_pending(paths):
        all_enrollments.append({
            "name": agent.name,
            "team": agent.team,
            "role": agent.role,
            "executor": agent.executor,
            "description": agent.description or "",
            "status": "pending",
            "enrolled_by": agent.enrolled_by,
            "created_at": agent.enrolled_at.isoformat() if agent.enrolled_at else None,
        })
    for agent in prompt_loader.list_agents(paths):
        all_enrollments.append({
            "name": agent.name,
            "team": agent.team,
            "role": agent.role,
            "executor": agent.executor,
            "description": agent.description or "",
            "status": "approved",
            "enrolled_by": agent.enrolled_by,
            "created_at": agent.enrolled_at.isoformat() if agent.enrolled_at else None,
        })

    # Apply status filter.
    if enrollment_status is not None:
        all_enrollments = [e for e in all_enrollments if e["status"] == enrollment_status]

    # Apply team filter.
    if team is not None and org.teams is not None:
        team_agents = {
            agent
            for agent in org.teams.all_agents()
            if org.teams.team_for_agent(agent) == team
        }
        all_enrollments = [e for e in all_enrollments if e["name"] in team_agents]

    return {"enrollments": all_enrollments}


@router.post("/agents/{agent_name}/approve")
async def approve_agent(slug: str, agent_name: str, org: OrgDep) -> dict:
    paths = OrgPaths(root=org.root)

    pending = prompt_loader.load_pending_agent(paths, agent_name)
    if pending is None:
        # Check if already approved (active).
        existing = prompt_loader.load_agent(paths, agent_name)
        if existing is not None:
            raise HTTPException(status_code=409, detail=f"agent is approved, not pending")
        raise HTTPException(status_code=404, detail=f"agent {agent_name!r} not found")

    # Refuse to promote an agent whose declared team isn't registered.
    # For workers, manage-agent enroll already added the team — this is
    # defense in depth against hand-edited pending files. For managers,
    # this is the primary guard: bootstrap managers must have their team
    # wired in teams.yaml first, never the other way around.
    if org.teams is None or pending.team not in org.teams.teams():
        raise HTTPException(
            status_code=409,
            detail={
                "code": "team_not_registered",
                "agent": agent_name,
                "team": pending.team,
                "fix": "add the team to teams.yaml first, then approve",
            },
        )

    try:
        agent_def = prompt_loader.approve_agent(paths, agent_name)
    except FileExistsError:
        raise HTTPException(status_code=409, detail=f"agent is approved, not pending")

    workspace = paths.workspaces_dir / agent_name
    workspace.mkdir(parents=True, exist_ok=True)

    # THR-095: agent.yaml is no longer the source for executor/repos.
    # The .md frontmatter (AgentDef) is the single source of truth.
    # Repos from AgentDef.repos; executor from AgentDef.executor.
    repos = agent_def.repos or {}

    ctx = ContextBuilder(org.settings, paths, slug=org.slug)
    for repo_name, url in repos.items():
        await asyncio.to_thread(ctx.clone_repo, workspace, repo_name, url)

    await asyncio.to_thread(
        ctx.ensure_workspace_ready,
        workspace,
        agent_name,
        agent_def.system_prompt,
        provider=agent_def.executor,
    )
    await asyncio.to_thread(ctx.create_agent_dirs, workspace, agent_name)

    return {"ok": True}


@router.post("/agents/{agent_name}/reject")
async def reject_agent(slug: str, agent_name: str, org: OrgDep) -> dict:
    paths = OrgPaths(root=org.root)

    pending = prompt_loader.load_pending_agent(paths, agent_name)
    if pending is None:
        existing = prompt_loader.load_agent(paths, agent_name)
        if existing is not None:
            raise HTTPException(status_code=409, detail=f"agent is approved, not pending")
        raise HTTPException(status_code=404, detail=f"agent {agent_name!r} not found")

    # Drop the file first; if it's already gone the reject_agent helper raises
    # FileNotFoundError. Holding teams_lock keeps the file-unlink + teams-yaml
    # mutation paired so a concurrent enrollment can't observe a half-state.
    async with org.teams_lock:
        try:
            prompt_loader.reject_agent(paths, agent_name)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"agent {agent_name!r} not found")
        # manage_agent.enroll added this worker to teams.yaml when it wrote the
        # pending file. Reject must undo both — otherwise the agent stays in
        # team membership forever and re-enrollment hits "duplicate" on the
        # team-side too. remove_worker is a no-op if the agent isn't a worker
        # under pending.team, so this is safe even if teams drifted.
        if org.teams is not None and pending.team in org.teams.teams():
            org.teams.remove_worker(pending.team, agent_name)

    return {"ok": True}


# THR-032 Phase R: canonical path is /memory; /learnings is kept as a hidden
# forwarder for one rollout cycle so in-flight callers don't break.
@router.post("/agents/{agent_name}/memory")
@router.post("/agents/{agent_name}/learnings", include_in_schema=False)
async def append_learning(
    slug: str, agent_name: str, body: LearningBody, org: OrgDep,
) -> dict:
    workspace = org.root / "workspaces" / agent_name
    if (workspace / "memory").exists() or (workspace / "learnings").exists():
        raise HTTPException(
            status_code=410,
            detail={
                "error": "endpoint_deprecated_for_migrated_workspace",
                "migrate_to": f"POST /api/v1/orgs/{slug}/agents/{agent_name}/memory/entries",
            },
        )
    expected = org.sessions.get_active(body.task_id, agent_name)
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

    learnings_path = workspace / "learnings.md"

    # Hold the lock across exists/init/append so two concurrent posts can't both
    # see the file as missing and race the header write.
    async with org.db_lock:
        _append_to_learnings_file(learnings_path, agent_name, body.text)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Memory read routes + 412 pre-migration guard
# ---------------------------------------------------------------------------


def _workspace_memory_store(org: OrgState, agent_name: str) -> MemoryStore:
    """Return the per-agent MemoryStore.

    Raises 404 if the agent workspace doesn't exist, 412 if it exists but
    hasn't been migrated to the structured per-entry layout. A legacy
    ``learnings/`` workspace is moved forward to ``memory/`` lazily here
    (idempotent + lossless, THR-032 Phase R) so first memory access migrates it.
    """
    workspace = org.root / "workspaces" / agent_name
    if not workspace.exists():
        raise HTTPException(
            status_code=404,
            detail={"error": "agent_not_found", "agent": agent_name},
        )
    migrate_workspace(workspace)
    memory_dir = workspace / "memory"
    if not memory_dir.exists():
        raise HTTPException(
            status_code=412,
            detail={"error": "workspace_not_migrated", "migrate_first": True},
        )
    return MemoryStore(memory_dir)


def _entry_to_dict(entry: MemoryItem) -> dict:
    result = {
        "id": entry.id,
        "slug": entry.slug,
        "title": entry.title,
        "topic": entry.topic,
        "tags": entry.tags,
        "body": entry.body,
        "source_task": entry.source_task,
        "related_to": entry.related_to,
        "supersedes": entry.supersedes,
        "promoted_to": entry.promoted_to,
        "authored_by": entry.authored_by,
        "authored_at": entry.authored_at,
        "updated_by": entry.updated_by,
        "updated_at": entry.updated_at,
        "lifecycle": entry.lifecycle,
        "provenance": entry.provenance,
        "salience": entry.salience,
        "last_verified": entry.last_verified,
    }
    # THR-091: surface entry age at recall
    result.update(entry.age_summary())
    return result


@router.get("/agents/{agent_name}/memory/entries/")


@router.get("/agents/{agent_name}/learnings/entries/", include_in_schema=False)
async def list_learnings(
    slug: str,
    agent_name: str,
    org: OrgDep,
    topic: str | None = None,
    tag: str | None = None,
    promoted: bool | None = None,
) -> dict:
    store = _workspace_memory_store(org, agent_name)
    summaries = store.list_entries(topic=topic, tag=tag, promoted=promoted)
    return {
        "entries": [
            {
                "id": s.id,
                "slug": s.slug,
                "title": s.title,
                "topic": s.topic,
                "tags": s.tags,
                "promoted_to": s.promoted_to,
                "updated_at": s.updated_at,
            }
            for s in summaries
        ],
    }


@router.get("/agents/{agent_name}/memory/entries/{id_or_slug}")


@router.get("/agents/{agent_name}/learnings/entries/{id_or_slug}", include_in_schema=False)
async def get_learning(slug: str, agent_name: str, id_or_slug: str, org: OrgDep) -> dict:
    store = _workspace_memory_store(org, agent_name)
    try:
        entry = store.read_entry(id_or_slug)
    except LearningNotFound:
        raise HTTPException(
            status_code=404,
            detail={"error": "id_not_found", "id_or_slug": id_or_slug},
        )
    AuditLogger(org.db).log_memory_read(
        agent=agent_name, id=entry.id, slug=entry.slug,
    )
    return _entry_to_dict(entry)


class LearningSearchBody(BaseModel):
    query: str
    limit: int | None = None
    include_promoted: bool | None = None
    include_evicted: bool | None = None
    include_superseded: bool | None = None
    include_kb: bool | None = None


@router.post("/agents/{agent_name}/memory/entries/search")


@router.post("/agents/{agent_name}/learnings/entries/search", include_in_schema=False)
async def search_learnings(
    slug: str, agent_name: str, body: LearningSearchBody, org: OrgDep,
) -> dict:
    org_cfg = load_org_config(OrgPaths(root=org.root))
    sc = org_cfg.memory_search
    store = _workspace_memory_store(org, agent_name)
    # Merge: explicit request fields override org config defaults
    limit = body.limit if body.limit is not None else sc.default_limit
    include_promoted = body.include_promoted if body.include_promoted is not None else False
    include_evicted = body.include_evicted if body.include_evicted is not None else sc.include_evicted_by_default
    include_superseded = body.include_superseded if body.include_superseded is not None else sc.include_superseded_by_default
    include_kb = body.include_kb if body.include_kb is not None else sc.include_kb_by_default
    hits = store.search(
        body.query,
        limit=limit,
        include_promoted=include_promoted,
        include_evicted=include_evicted,
        include_superseded=include_superseded,
    )
    warnings: list[str] = []
    # THR-032 P4b: opt-in read-only KB federation
    if include_kb:
        kb_store = KBStore(org.root / "kb")
        try:
            kb_hits = kb_store.search(body.query, limit=limit)
            for kh in kb_hits:
                hits.append(LearningSearchHit(
                    id=kh.slug, slug=kh.slug, title=kh.title,
                    snippet=kh.snippet, score=kh.score,
                    source="kb",
                    lifecycle="valid", provenance="experiential",
                    salience=50, updated_at=None,
                ))
        except Exception as exc:
            warnings.append(f"KB search failed: {exc}")
    # THR-032 P4b: merge + sort combined memory+KB hits, then truncate
    hits.sort(key=lambda h: (-h.score, h.updated_at or "", h.title, h.id))
    hits = hits[:limit]
    result: dict = {
        "hits": [
            {
                "id": h.id,
                "slug": h.slug,
                "title": h.title,
                "snippet": h.snippet,
                "score": h.score,
                "source": h.source,
                "lifecycle": h.lifecycle,
                "provenance": h.provenance,
                "salience": h.salience,
                "updated_at": h.updated_at,
            }
            for h in hits
        ],
    }
    if warnings:
        result["warnings"] = warnings
    return result


# ---------------------------------------------------------------------------
# Learnings write routes — POST add + PUT update
# ---------------------------------------------------------------------------


class LearningAddBody(BaseModel):
    slug: str
    title: str
    topic: str
    body: str
    tags: list[str] = []
    source_task: str | None = None
    related_to: list[str] = []
    supersedes: str | None = None


class LearningUpdateBody(BaseModel):
    slug: str
    title: str
    topic: str
    body: str
    tags: list[str] = []
    source_task: str | None = None
    related_to: list[str] = []
    supersedes: str | None = None


def _invalid_entry_to_http(err: InvalidLearningEntry) -> HTTPException:
    return HTTPException(status_code=400, detail={"error": err.code, "message": str(err)})


@router.post("/agents/{agent_name}/memory/entries/", status_code=201)


@router.post("/agents/{agent_name}/learnings/entries/", status_code=201, include_in_schema=False)
async def add_learning(
    slug: str, agent_name: str, body: LearningAddBody, org: OrgDep,
) -> dict:
    store = _workspace_memory_store(org, agent_name)
    async with org.db_lock:
        new_id = store.next_id()
        entry = MemoryItem(
            id=new_id,
            slug=body.slug,
            title=body.title,
            topic=body.topic,
            body=body.body,
            tags=list(body.tags),
            source_task=body.source_task,
            related_to=list(body.related_to),
            supersedes=body.supersedes,
        )
        try:
            written = store.write_entry(entry, agent=agent_name)
        except InvalidLearningEntry as e:
            raise _invalid_entry_to_http(e)
        except LearningIdExists as e:
            raise HTTPException(status_code=409, detail={"error": "id_exists", "id": e.id})
        except LearningSlugExists as e:
            raise HTTPException(status_code=409, detail={"error": "slug_exists", "slug": e.slug})
        # THR-032 P3b: audit supersede lifecycle transition if it occurred
        sup_target = getattr(written, '_superseded_target_id', None)
        store.regenerate_index()
        if sup_target is not None:
            AuditLogger(org.db).log_memory_lifecycle_changed(
                agent=agent_name,
                id=sup_target,
                from_lifecycle="valid",
                to_lifecycle="superseded",
                reason=f"superseded by {written.id}",
                source="supersedes",
            )
        AuditLogger(org.db).log_memory_added(
            agent=agent_name,
            id=written.id,
            slug=written.slug,
            topic=written.topic,
            tags=written.tags,
            source_task=written.source_task,
        )
    rel_path = f"memory/{written.id}-{written.slug}.md"
    return {"id": written.id, "path": rel_path, "authored_at": written.authored_at}


@router.put("/agents/{agent_name}/memory/entries/{id}")


@router.put("/agents/{agent_name}/learnings/entries/{id}", include_in_schema=False)
async def update_learning(
    slug: str, agent_name: str, id: str, body: LearningUpdateBody, org: OrgDep,
) -> dict:
    store = _workspace_memory_store(org, agent_name)
    # THR-091 WS-A: preserve last_verified on normal update (read-modify-write).
    # The PUT body deliberately does NOT carry last_verified — we carry forward
    # the existing value so a content update never silently clears verification state.
    try:
        prior_last_verified = store.read_entry(id).last_verified
    except LearningNotFound:
        prior_last_verified = None
    entry = MemoryItem(
        id=id,
        slug=body.slug,
        title=body.title,
        topic=body.topic,
        body=body.body,
        tags=list(body.tags),
        source_task=body.source_task,
        related_to=list(body.related_to),
        supersedes=body.supersedes,
        last_verified=prior_last_verified,
    )
    async with org.db_lock:
        try:
            prior_slug = store.read_entry(id).slug
        except LearningNotFound:
            prior_slug = None  # store.update_entry will raise its own LearningNotFound
        try:
            written = store.update_entry(id, entry, agent=agent_name)
        except LearningNotFound:
            raise HTTPException(status_code=404, detail={"error": "id_not_found", "id": id})
        except PromotedLocked as e:
            raise HTTPException(status_code=409, detail={"error": "promoted_locked", "id": e.id, "kb_slug": e.kb_slug})
        except InvalidLearningId:
            raise HTTPException(status_code=400, detail={"error": "invalid_id", "id": id})
        except InvalidLearningEntry as e:
            raise _invalid_entry_to_http(e)
        except LearningSlugExists as e:
            raise HTTPException(status_code=409, detail={"error": "slug_exists", "slug": e.slug})
        # THR-032 P3b: audit supersede lifecycle transition if it occurred
        sup_target = getattr(written, '_superseded_target_id', None)
        store.regenerate_index()
        if sup_target is not None:
            AuditLogger(org.db).log_memory_lifecycle_changed(
                agent=agent_name,
                id=sup_target,
                from_lifecycle="valid",
                to_lifecycle="superseded",
                reason=f"superseded by {written.id}",
                source="supersedes",
            )
        AuditLogger(org.db).log_memory_updated(
            agent=agent_name,
            id=written.id,
            slug_changed=prior_slug is not None and prior_slug != written.slug,
        )
    return _entry_to_dict(written)


class LearningPromoteBody(BaseModel):
    kb_slug: str


@router.post("/agents/{agent_name}/memory/entries/reindex")


@router.post("/agents/{agent_name}/learnings/entries/reindex", include_in_schema=False)
async def reindex_learnings(slug: str, agent_name: str, org: OrgDep) -> dict:
    store = _workspace_memory_store(org, agent_name)
    async with org.db_lock:
        store.regenerate_index()
    return {"ok": True}


@router.post("/agents/{agent_name}/memory/entries/{id}/promote")


@router.post("/agents/{agent_name}/learnings/entries/{id}/promote", include_in_schema=False)
async def promote_learning(
    slug: str, agent_name: str, id: str, body: LearningPromoteBody, org: OrgDep,
) -> dict:
    if not body.kb_slug:
        raise HTTPException(status_code=400, detail={"error": "kb_slug_missing"})
    kb_store = KBStore(org.root / "kb")
    try:
        kb_store.validate_slug(body.kb_slug)
    except InvalidSlug:
        raise HTTPException(
            status_code=400, detail={"error": "invalid_kb_slug", "kb_slug": body.kb_slug},
        )
    if not kb_store.path_for(body.kb_slug).exists():
        raise HTTPException(
            status_code=404,
            detail={"error": "kb_slug_not_found", "kb_slug": body.kb_slug},
        )
    store = _workspace_memory_store(org, agent_name)
    async with org.db_lock:
        try:
            written = store.promote(id, kb_slug=body.kb_slug, agent=agent_name)
        except InvalidLearningId:
            raise HTTPException(status_code=400, detail={"error": "invalid_id", "id": id})
        except LearningNotFound:
            raise HTTPException(status_code=404, detail={"error": "id_not_found", "id": id})
        except PromotedLocked as e:
            raise HTTPException(status_code=409, detail={"error": "promoted_locked", "id": e.id, "kb_slug": e.kb_slug})
        except InvalidLearningEntry as e:
            raise _invalid_entry_to_http(e)
        store.regenerate_index()
        AuditLogger(org.db).log_memory_promoted(
            agent=agent_name,
            id=written.id,
            kb_slug=body.kb_slug,
        )
    return _entry_to_dict(written)


class LifecyclePatchBody(BaseModel):
    lifecycle: str
    reason: str | None = None


@router.patch("/agents/{agent_name}/memory/entries/{id}/lifecycle")
@router.patch(
    "/agents/{agent_name}/learnings/entries/{id}/lifecycle",
    include_in_schema=False,
)
async def patch_lifecycle(
    slug: str, agent_name: str, id: str, body: LifecyclePatchBody, org: OrgDep,
) -> dict:
    if not body.reason or not body.reason.strip():
        raise HTTPException(
            status_code=400,
            detail={"error": "reason_required", "message": "reason must be non-empty"},
        )
    store = _workspace_memory_store(org, agent_name)
    async with org.db_lock:
        try:
            updated, prior = store.set_lifecycle(
                id, body.lifecycle, agent=agent_name, reason=body.reason.strip(),
            )
        except InvalidLearningId:
            raise HTTPException(status_code=400, detail={"error": "invalid_id", "id": id})
        except LearningNotFound:
            raise HTTPException(status_code=404, detail={"error": "id_not_found", "id": id})
        except PromotedLocked as e:
            raise HTTPException(
                status_code=409,
                detail={"error": "promoted_locked", "id": e.id, "kb_slug": e.kb_slug},
            )
        except InvalidLearningEntry as e:
            raise _invalid_entry_to_http(e)
        store.regenerate_index()
        AuditLogger(org.db).log_memory_lifecycle_changed(
            agent=agent_name,
            id=updated.id,
            from_lifecycle=prior,
            to_lifecycle=updated.lifecycle,
            reason=body.reason.strip(),
            source="manual",
        )
    result = _entry_to_dict(updated)
    result["previous_lifecycle"] = prior
    return result


class CompactBody(BaseModel):
    dry_run: bool = True
    # Optional per-request overrides for compaction policy knobs.
    # When absent, org config defaults apply.
    salience_floor: int | None = None
    stale_days: int | None = None
    superseded_grace_days: int | None = None
    max_evictions_per_run: int | None = None


@router.post("/agents/{agent_name}/memory/entries/compact")
async def compact_memory(
    slug: str, agent_name: str, body: CompactBody, org: OrgDep,
) -> dict:
    org_cfg = load_org_config(OrgPaths(root=org.root))
    cc = org_cfg.memory_compaction
    # Dry-runs are always allowed (read-only). Apply requires config enablement.
    if not body.dry_run and not cc.enabled:
        raise HTTPException(
            status_code=403,
            detail={"error": "compaction_disabled",
                    "message": "memory compaction apply is disabled in org config"},
        )
    # Build policy: org config defaults, with explicit request-field overrides
    policy = MemoryCompactionPolicy(
        salience_floor=body.salience_floor if body.salience_floor is not None else cc.salience_floor,
        stale_days=body.stale_days if body.stale_days is not None else cc.stale_days,
        superseded_grace_days=body.superseded_grace_days if body.superseded_grace_days is not None else cc.superseded_grace_days,
        max_evictions_per_run=body.max_evictions_per_run if body.max_evictions_per_run is not None else cc.max_evictions_per_run,
    )
    store = _workspace_memory_store(org, agent_name)
    async with org.db_lock:
        result = store.compact(dry_run=body.dry_run, policy=policy)
        # Audit each eviction
        if not body.dry_run:
            for evicted_id in result.evicted:
                AuditLogger(org.db).log_memory_lifecycle_changed(
                    agent=agent_name,
                    id=evicted_id,
                    from_lifecycle="valid" if evicted_id not in {
                        c.id for c in result.candidates if c.current_lifecycle == "superseded"
                    } else "superseded",
                    to_lifecycle="evicted",
                    reason=f"compaction (dry_run={body.dry_run})",
                    source="compaction",
                )
        return {
            "dry_run": result.dry_run,
            "candidates": [
                {"id": c.id, "title": c.title, "reason": c.reason, "current_lifecycle": c.current_lifecycle}
                for c in result.candidates
            ],
            "evicted": result.evicted,
            "skipped": result.skipped,
            "errors": result.errors,
        }
