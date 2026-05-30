"""Headless executor invocation for thread participation.

Single-turn lifecycle: build prompt → spawn subprocess → wait for token to be
consumed (via reply/decline/close-out callback) → exit. No NextStep loop.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from src.config import Settings
from src.infrastructure.audit_logger import AuditLogger
from src.models import (
    ThreadInvocationPurpose,
    ThreadInvocationStatus,
    ThreadMessage,
    ThreadMessageKind,
    ThreadParticipant,
    ThreadRecord,
)
from src.orchestrator.executors import (
    ClaudeExecutor,
    CodexExecutor,
    OpencodeExecutor,
)

logger = logging.getLogger(__name__)

_EXECUTOR_MAP = {
    "claude": "claude",
    "codex": "codex",
    "opencode": "opencode",
}


def _render_message(m: ThreadMessage) -> str:
    ts = m.created_at.isoformat()
    if m.kind is ThreadMessageKind.MESSAGE:
        head = f"[Message {m.seq} — {m.speaker} · {ts}]"
        addressed = f"To: {', '.join(m.addressed_to)}" if m.addressed_to else ""
        body = m.body_markdown or ""
        return "\n".join(filter(None, [head, addressed, "", body])) + "\n---"
    if m.kind is ThreadMessageKind.DECLINE:
        return (
            f"[Message {m.seq} — {m.speaker} · {ts}]\n"
            f"👁 declined: {m.decline_reason or ''}\n---"
        )
    payload = m.system_payload or {}
    tag = payload.get("kind_tag", "system")
    return f"[Message {m.seq} — {m.speaker} · {ts}]\nsystem: {tag} · {payload}\n---"


def _purpose_note(
    purpose: str,
    triggering_seq: int,
    addressed_to: list[str] | None,
    invoked_agent: str,
    triggering_message: "ThreadMessage | None" = None,
) -> str:
    if purpose == "bootstrap":
        return "The founder has added you to this thread"
    if purpose == "close_out":
        return "This thread is being archived; provide a close-out"
    if purpose == "task_followup":
        payload = (triggering_message.system_payload or {}) if triggering_message else {}
        task_id = payload.get("task_id", "?")
        status = payload.get("status", "?")
        return (
            f"Task {task_id} that you dispatched from this thread reached "
            f"`{status}`. Compose a follow-up reply with the result (pull "
            f"details via `grassland details {task_id}`), or decline if "
            f"there is nothing substantive to add. Dispatching a new task "
            f"from this turn is not allowed; mention any new action in the "
            f"reply and let the founder loop in."
        )
    # purpose == "reply"
    addr = addressed_to or []
    if addr == ["@all"]:
        return f"Message {triggering_seq} addressed @all"
    if invoked_agent in addr:
        return f"Message {triggering_seq} addressed you individually"
    return f"Message {triggering_seq} (no explicit addressee)"


def build_thread_prompt(
    *,
    thread: ThreadRecord,
    participants: list[ThreadParticipant],
    messages: list[ThreadMessage],
    invocation_token: str,
    invoked_agent: str,
    purpose: str,          # 'reply' | 'bootstrap' | 'close_out'
    triggering_seq: int,
) -> str:
    triggering = next((m for m in messages if m.seq == triggering_seq), None)
    addressed_to = triggering.addressed_to if triggering else None
    parts_str = ", ".join(p.agent_name for p in participants)
    history = "\n".join(_render_message(m) for m in messages)
    forwarded = (
        f"Forwarded from {thread.forwarded_from_id}."
        if thread.forwarded_from_id else ""
    )
    note = _purpose_note(
        purpose, triggering_seq, addressed_to, invoked_agent,
        triggering_message=triggering,
    )
    return (
        f"You are participating in thread {thread.id}: \"{thread.subject}\".\n\n"
        f"Participants: {parts_str}.\n"
        f"Started: {thread.started_at.isoformat()}. {forwarded}\n\n"
        f"Full message history follows. Most recent message is at the bottom.\n\n"
        f"---\n{history}\n\n"
        f"You have been invoked because:\n  {note}\n\n"
        f"Your invocation_token for this turn is: {invocation_token}\n"
        f"Include this token in every callback payload (reply, decline, dispatch,\n"
        f"close-out). It authorizes this single turn and is single-use for the\n"
        f"terminal callback (reply/decline/close-out).\n\n"
        f"Consult `protocol/skills/thread/SKILL.md` and respond.\n"
    )


def _build_executor_for_provider(provider: str, settings: Settings, paths):
    """Construct the right executor for a given provider string."""
    if provider == "codex":
        return CodexExecutor(
            codex_cli_path=settings.codex_cli_path,
            sandbox_mode=settings.codex_sandbox_mode,
        )
    if provider == "opencode":
        return OpencodeExecutor(
            opencode_cli_path=settings.opencode_cli_path,
        )
    return ClaudeExecutor(
        claude_cli_path=settings.claude_cli_path,
        permission_mode=settings.permission_mode,
        settings=settings,
        paths=paths,
    )


async def run_invocation(
    *,
    org_state,
    invocation_token: str,
    settings: Settings,
) -> None:
    """Execute one thread invocation end-to-end.

    Reads the pending row, builds the prompt, spawns the executor subprocess,
    and records auto-decline rows on no-callback / timeout / failure.
    """
    inv = org_state.db.get_pending_invocation(invocation_token)
    if inv is None:
        logger.info("run_invocation: token %s already non-pending", invocation_token[:8])
        return

    thread = org_state.db.get_thread(inv.thread_id)
    if thread is None:
        org_state.db.fail_invocation(
            invocation_token,
            status=ThreadInvocationStatus.FAILED,
            decline_reason="thread_missing",
        )
        return

    participants = org_state.db.list_thread_participants(inv.thread_id)
    messages = org_state.db.list_thread_messages(inv.thread_id, limit=10000)

    prompt = build_thread_prompt(
        thread=thread,
        participants=participants,
        messages=messages,
        invocation_token=invocation_token,
        invoked_agent=inv.agent_name,
        purpose=inv.purpose.value,
        triggering_seq=inv.triggering_seq,
    )

    workspace = org_state.root / "workspaces" / inv.agent_name

    # Read agent.yaml to pick the executor.
    try:
        from src.daemon.agent_config import load_agent_config
        agent_yaml = load_agent_config(Path(workspace)) or {}
    except Exception:
        agent_yaml = {}
    executor_name = (agent_yaml.get("executor") or "claude").lower()
    if executor_name not in _EXECUTOR_MAP:
        executor_name = "claude"

    # Build OrgPaths so ClaudeExecutor can resolve allow rules.
    try:
        from src.orchestrator._paths import OrgPaths
        paths = OrgPaths(root=org_state.root)
    except Exception:
        paths = None

    executor = _build_executor_for_provider(executor_name, settings, paths)

    # Resolve timeout (org override → code default).
    timeout: int = settings.session_timeout_seconds
    try:
        from src.orchestrator.org_config import load_org_config
        from src.orchestrator._paths import OrgPaths as _OrgPaths
        cfg = load_org_config(_OrgPaths(root=org_state.root))
        if cfg.threads_invocation_timeout_seconds is not None:
            timeout = cfg.threads_invocation_timeout_seconds
    except Exception:
        pass

    org_state.db.stamp_invocation_started(invocation_token, session_id=None)

    # Spawn subprocess in a thread pool (executors are synchronous).
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: executor.run(
                workspace=Path(workspace),
                prompt=prompt,
                session_id=None,
                timeout_seconds=timeout,
            ),
        )
    except Exception as exc:
        org_state.db.fail_invocation(
            invocation_token,
            status=ThreadInvocationStatus.FAILED,
            decline_reason=f"runner_crash: {exc}",
        )
        AuditLogger(org_state.db).log_thread_invocation_failed(
            inv.thread_id,
            agent=inv.agent_name,
            token=invocation_token,
            purpose=inv.purpose.value,
            reason=str(exc),
        )
        return

    # Inspect post-subprocess token state.
    after = org_state.db.get_invocation_any_status(invocation_token)
    if after is None:
        return
    if after.status is ThreadInvocationStatus.CONSUMED:
        return

    # Subprocess exited without consuming → auto-decline.
    err_text = str(getattr(result, "error", "") or "").lower()
    rc = getattr(result, "returncode", "?")
    if "timeout" in err_text:
        reason = "invocation_timeout"
        status = ThreadInvocationStatus.TIMEOUT
    else:
        reason = f"no_callback: rc={rc}"
        status = ThreadInvocationStatus.FAILED

    org_state.db.fail_invocation(
        invocation_token, status=status, decline_reason=reason,
    )
    # Spec §6: silent decline — no thread_messages row, no turns_used increment.
    # The invocation row status (timeout/failed) and decline_reason are the record.
    AuditLogger(org_state.db).log_thread_invocation_failed(
        inv.thread_id,
        agent=inv.agent_name,
        token=invocation_token,
        purpose=inv.purpose.value,
        reason=reason,
        kind="thread_invocation_failed",
    )
