"""Executor-backed private dream invocations."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from runtime.config import Settings, settings as global_settings
from runtime.daemon.thread_runner import _build_executor_for_provider
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.models import DreamRecord, DreamStatus


def build_dream_prompt(
    *,
    org_slug: str,
    dream: DreamRecord,
    workspace: Path,
    recent_audit: list[dict],
    task_history: str,
) -> str:
    return f"""# Private Nightly Dream

You are {dream.agent_name}. This is private reflection for HappyRanch org `{org_slug}`.
This is not a task, talk, or thread. Do not call report-completion.

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


def _executor_name(workspace: Path) -> str:
    try:
        from runtime.daemon.agent_config import load_agent_config
        agent_yaml = load_agent_config(workspace) or {}
    except Exception:
        agent_yaml = {}
    return (agent_yaml.get("executor") or "claude").lower()


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

    recent_audit = org_state.db.get_audit_logs(dream_id)
    prompt = build_dream_prompt(
        org_slug=org_state.slug,
        dream=dream,
        workspace=workspace,
        recent_audit=recent_audit,
        task_history=_load_task_history(workspace),
    )

    executor_name = _executor_name(workspace)
    if executor_name not in {"claude", "codex", "opencode", "pi"}:
        executor_name = "claude"
    executor = executor_factory(executor_name, settings, None) if executor_factory else _build_executor_for_provider(executor_name, settings, None)

    loop = asyncio.get_event_loop()
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
            usage=result.token_usage,
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
    org_state.db.update_dream(
        dream_id,
        status=DreamStatus.FAILED,
        ended_at=datetime.now(timezone.utc),
        error=str(getattr(result, "error", "") or "executor_failed"),
    )
    AuditLogger(org_state.db).log_dream_failed(
        dream_id,
        dream.agent_name,
        reason=str(getattr(result, "error", "") or "executor_failed"),
    )
