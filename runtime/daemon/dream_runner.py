"""Executor-backed private dream invocations."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from runtime.config import Settings, settings as global_settings
from runtime.daemon.thread_runner import _build_executor_for_provider
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.orchestrator.executor_registry import get_registry
from runtime.models import DreamRecord, DreamStatus
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.org_config import (
    OrgConfig,
    load_org_config,
    render_current_time_line,
    resolve_dreaming_timezone_display,
    resolve_managed_skills_index,
    resolve_protocol_doc_manifest,
)
from runtime.orchestrator.workspace_adapters import (
    ensure_system_contracts_materialized,
    inject_managed_skills,
    inject_system_contracts,
    refresh_session_skills,
)

# Cap on the agent's window audit rows folded into the dream prompt. The most
# recent N (chronological); keeps the prompt bounded on busy agents.
_AUDIT_WINDOW_CAP = 200


def _is_timeout(result) -> bool:
    """Distinguish an executor timeout from an ordinary non-zero exit. Timeouts
    leave returncode=None and carry the executor's 'timed out' error string
    (see runtime/orchestrator/executors.py)."""
    err = str(getattr(result, "error", "") or "").lower()
    return "timed out" in err or "timeout" in err


def build_dream_prompt(
    *,
    org_slug: str,
    dream: DreamRecord,
    workspace: Path,
    recent_audit: list[dict],
    task_history: str,
    org_config: OrgConfig,
    now: Callable[[], datetime] | None = None,
    managed_skills_index: str = "",
    protocol_doc_manifest: str = "",
) -> str:
    """Compose the private dream-session prompt.

    ``current_time`` is injected (fresh per dream) via the shared renderer using
    the DREAMING effective timezone (dreaming.timezone -> org.timezone ->
    machine-local -> UTC), so dream sessions carry the same local wall clock as
    every other agent session. ``now`` is injectable for tests.
    """
    tz, label = resolve_dreaming_timezone_display(org_config)
    current_time = render_current_time_line(tz, label, now)
    skills_block = f"\n{managed_skills_index}\n" if managed_skills_index else ""
    docs_block = f"\n{protocol_doc_manifest}\n" if protocol_doc_manifest else ""
    return f"""# Private Nightly Dream

You are {dream.agent_name}. This is private reflection for HappyRanch org `{org_slug}`.
This is not a task or thread. Do not call report-completion.

current_time: {current_time}{skills_block}{docs_block}
Dream id: {dream.id}
Window start: {dream.window_start.isoformat() if dream.window_start else "last 24 hours"}
Window end: {dream.window_end.isoformat()}

Review recent work, recurring friction, stale assumptions, contradictions, and durable lessons.
Write KB candidate bodies to temporary markdown files, then complete with:

happyranch dreams complete --org {org_slug} --dream-id {dream.id} --from-file /tmp/dream-result-{dream.id}.json

Task history:
{task_history}

Recent audit:
{recent_audit}
"""


def _load_task_history(workspace: Path) -> str:
    path = workspace / "task_history.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")[-20000:]


def _executor_name(paths: OrgPaths, agent_name: str) -> str:
    """THR-095: resolve executor from org/agents/<name>.md (single source)."""
    try:
        from runtime.orchestrator.prompt_loader import load_agent
        agent_def = load_agent(paths, agent_name)
        return (agent_def.executor if agent_def else "claude").lower()
    except Exception:
        return "claude"


async def run_dream(
    *,
    org_state,
    dream_id: str,
    settings: Settings = global_settings,
    executor_factory: Callable | None = None,
) -> None:
    dream = org_state.db.get_dream(dream_id)
    if dream is None or dream.status != DreamStatus.PENDING:
        return

    workspace = org_state.root / "workspaces" / dream.agent_name
    now = datetime.now(timezone.utc)
    org_state.db.update_dream(dream_id, status=DreamStatus.RUNNING, started_at=now)
    AuditLogger(org_state.db).log_dream_started(dream_id, dream.agent_name)

    # Spec "Input Window": include the agent's audit rows since window_start,
    # not only the dream-scoped rows. window_start is set by the scheduler; fall
    # back to no lower bound (capped recent rows) if absent.
    if dream.window_start is not None:
        recent_audit = org_state.db.get_audit_logs_for_agent_since(
            dream.agent_name, dream.window_start.isoformat(), limit=_AUDIT_WINDOW_CAP,
        )
    else:
        recent_audit, _ = org_state.db.query_audit_logs(
            agent=dream.agent_name, limit=_AUDIT_WINDOW_CAP,
        )
    paths = OrgPaths(root=org_state.root)
    try:
        org_config = load_org_config(paths)
    except Exception:
        org_config = OrgConfig()
    managed_skills_index = resolve_managed_skills_index(
        paths=paths, agent_name=dream.agent_name,
    )

    # Refresh on-disk skill bodies on EVERY session (THR-070).
    try:
        refresh_session_skills(workspace, settings, slug=org_state.slug)
    except Exception:
        pass

    # TASK-2511: resolve executor name early for the materialization guard.
    _prov = _executor_name(paths, dream.agent_name)
    if not get_registry().is_registered(_prov):
        _prov = "claude"

    # Explicit context-aware system-contract injection with on-disk verification
    # (THR-055 Phase 1 + TASK-2511 hardening). This is a HARD synchronous
    # pre-spawn precondition — if materialization fails we persist the named
    # error and STOP before executor spawn, never proceeding with missing
    # contract files (REVISE TASK-2525).
    from runtime.orchestrator.workspace_adapters import (
        SystemContractMaterializationError,
    )
    try:
        ensure_system_contracts_materialized(
            workspace, settings, slug=org_state.slug, context="dream",
            provider=_prov,
        )
    except SystemContractMaterializationError as e:
        org_state.db.update_dream(
            dream_id,
            status=DreamStatus.FAILED,
            ended_at=datetime.now(timezone.utc),
            error=str(e),
        )
        AuditLogger(org_state.db).log_dream_failed(
            dream_id, dream.agent_name, reason=str(e),
        )
        return

    # Managed-catalog skill injection (THR-055 Phase 4).
    try:
        from runtime.orchestrator.prompt_loader import load_agent
        agent_def = load_agent(paths, dream.agent_name)
        agent_team = agent_def.team if agent_def else "engineering"
    except Exception:
        agent_team = "engineering"
    try:
        skills_root = settings.project_root / "runtime" / "skills"
        inject_managed_skills(
            workspace, settings,
            slug=org_state.slug,
            agent_name=dream.agent_name,
            team=agent_team,
            skills_root=skills_root,
        )
    except Exception:
        pass

    protocol_doc_manifest = resolve_protocol_doc_manifest(settings=settings)

    prompt = build_dream_prompt(
        org_slug=org_state.slug,
        dream=dream,
        workspace=workspace,
        recent_audit=recent_audit,
        task_history=_load_task_history(workspace),
        org_config=org_config,
        managed_skills_index=managed_skills_index,
        protocol_doc_manifest=protocol_doc_manifest,
    )

    executor_name = _prov  # already resolved above (TASK-2511)
    executor = executor_factory(executor_name, settings, paths) if executor_factory else _build_executor_for_provider(executor_name, settings, paths)

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, lambda: executor.run(
        workspace=workspace,
        prompt=prompt,
        session_id=None,
        timeout_seconds=settings.session_timeout_seconds,
    ))

    if getattr(result, "token_usage", None) is not None:
        org_state.db.insert_session_token_usage(
            task_id=None,
            agent=dream.agent_name,
            session_id=getattr(result, "agent_session_id", None) or getattr(result, "session_id", None) or dream_id,
            executor=executor_name,
            token_usage=result.token_usage,
            scope_type="dream",
            scope_id=dream_id,
        )

    refreshed = org_state.db.get_dream(dream_id)
    if refreshed is None:
        return
    if refreshed.status == DreamStatus.COMPLETED:
        return
    if result.success:
        org_state.db.update_dream(
            dream_id,
            status=DreamStatus.FAILED,
            ended_at=datetime.now(timezone.utc),
            session_id=getattr(result, "agent_session_id", None) or getattr(result, "session_id", None),
            error="no_callback",
        )
        AuditLogger(org_state.db).log_dream_failed(dream_id, dream.agent_name, reason="no_callback")
        return
    error = str(getattr(result, "error", "") or "executor_failed")
    if _is_timeout(result):
        # Spec "Failure Handling": timeout is a distinct terminal status; the
        # successful-dream window is not advanced (get_last_successful_dream
        # only counts COMPLETED).
        org_state.db.update_dream(
            dream_id,
            status=DreamStatus.TIMEOUT,
            ended_at=datetime.now(timezone.utc),
            error=error,
        )
        AuditLogger(org_state.db).log_dream_timeout(dream_id, dream.agent_name, reason=error)
        return
    org_state.db.update_dream(
        dream_id,
        status=DreamStatus.FAILED,
        ended_at=datetime.now(timezone.utc),
        error=error,
    )
    AuditLogger(org_state.db).log_dream_failed(dream_id, dream.agent_name, reason=error)
