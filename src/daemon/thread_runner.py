"""Headless executor invocation for thread participation.

Single-turn lifecycle: build prompt → spawn subprocess → wait for token to be
consumed (via reply/decline/close-out callback) → exit. No NextStep loop.
"""
from __future__ import annotations

from src.models import (
    ThreadMessage,
    ThreadMessageKind,
    ThreadParticipant,
    ThreadRecord,
)


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
) -> str:
    if purpose == "bootstrap":
        return "The founder has added you to this thread"
    if purpose == "close_out":
        return "This thread is being archived; provide a close-out"
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
    note = _purpose_note(purpose, triggering_seq, addressed_to, invoked_agent)
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
