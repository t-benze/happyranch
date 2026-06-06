"""Headless executor invocation for thread participation.

Single-turn lifecycle: build prompt → spawn subprocess → wait for token to be
consumed (via reply/decline callback) → exit. No NextStep loop.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from runtime.config import Settings
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.models import (
    ThreadInvocationPurpose,
    ThreadInvocationStatus,
    ThreadMessage,
    ThreadMessageKind,
    ThreadParticipant,
    ThreadRecord,
)
from runtime.orchestrator.executors import (
    ClaudeExecutor,
    CodexExecutor,
    OpencodeExecutor,
    PiExecutor,
)

logger = logging.getLogger(__name__)

# Cap for the underlying-error detail appended to a no_callback reason so a
# multi-KB stdout/stderr tail can't bloat the audit row.
_REASON_DETAIL_CAP = 300


def _executor_error_detail(result, rc) -> str:
    """Single-line cause behind a non-zero subprocess exit, for the audit reason.

    The executor sets ``error`` to ``"Command exited with code N[: <stderr>]"``;
    that envelope is stripped so the reason carries just the underlying cause
    (e.g. an ``API Error: 529 Overloaded`` raised inside the claude CLI), which
    was previously only recoverable by digging into the claude session JSONL.
    """
    raw = (str(getattr(result, "error", "") or "")
           or str(getattr(result, "stderr_tail", "") or "")).strip()
    prefix = f"Command exited with code {rc}"
    if raw.startswith(prefix):
        raw = raw[len(prefix):].lstrip(": ").strip()
    raw = " ".join(raw.split())  # collapse newlines → single-line reason
    return raw[:_REASON_DETAIL_CAP]


async def _publish_invocation_event(
    org_state, *, thread_id: str, agent_name: str, seq: int, kind: str, status: str
) -> None:
    """Publish an invocation lifecycle event to the thread tail topic.

    Guarded no-op when org_state has no event_bus (test harness). Published
    directly to thread_topic (NOT the inbox topic) so invocation churn doesn't
    light up the threads-list badge. `seq` carries the triggering message seq so
    the existing client tail consumer refetches the messages (which embed
    responder_status)."""
    bus = getattr(org_state, "event_bus", None)
    if bus is None:
        return
    try:
        from runtime.daemon.event_bus import thread_topic
        await bus.publish(
            thread_topic(thread_id),
            {
                "thread_id": thread_id,
                "seq": seq,
                "kind": kind,
                "agent_name": agent_name,
                "status": status,
            },
        )
    except Exception as exc:  # event delivery must never break the turn
        logger.warning("invocation event publish failed: %s", exc)


_EXECUTOR_MAP = {
    "claude": "claude",
    "codex": "codex",
    "opencode": "opencode",
    "pi": "pi",
}


def _render_message(m: ThreadMessage) -> str:
    ts = m.created_at.isoformat()
    if m.kind is ThreadMessageKind.MESSAGE:
        head = f"[Message {m.seq} — {m.speaker} · {ts}]"
        body = m.body_markdown or ""
        return "\n".join(filter(None, [head, "", body])) + "\n---"
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
    invoked_agent: str,
    triggering_message: "ThreadMessage | None" = None,
) -> str:
    if purpose == "bootstrap":
        return "The founder has added you to this thread"
    if purpose == "task_followup":
        payload = (triggering_message.system_payload or {}) if triggering_message else {}
        task_id = payload.get("task_id", "?")
        status = payload.get("status", "?")
        return (
            f"Task {task_id} that you dispatched from this thread reached "
            f"`{status}`. Compose a follow-up reply with the result (pull "
            f"details via `happyranch details {task_id}`), or decline if "
            f"there is nothing substantive to add. Dispatching a new task "
            f"from this turn is not allowed; mention any new action in the "
            f"reply and let the founder loop in."
        )
    # purpose == "reply" — broadcast model; all participants receive the message
    return f"Message {triggering_seq} was posted to this thread"


def _decline_by_default_doctrine() -> str:
    return (
        "## Decline-by-Default in Threads\n\n"
        "This invocation was minted because a new message was posted to this\n"
        "thread. Every participant gets an invocation on every message — that\n"
        "does NOT mean every participant should reply.\n\n"
        "Default behavior: call `happyranch threads decline --from-file <payload>`\n"
        "with no reason. Your invocation is consumed silently; no transcript\n"
        "entry is written.\n\n"
        "Reply (with `happyranch threads reply --from-file <payload>`) only when\n"
        "ALL of the following hold:\n"
        "- The latest message contains a question, request, or hand-off that\n"
        "  you can uniquely answer based on your role.\n"
        "- You have substantive content to add — not acknowledgment, not\n"
        "  \"I agree\", not \"noted\".\n"
        "- No other participant has already covered the same ground in a\n"
        "  recent reply.\n\n"
        "The founder is a participant; she reads the full thread in the web UI.\n"
        "You do not need to \"keep her informed\" by replying.\n\n"
        "If you are unsure: decline. The thread can always be re-engaged by\n"
        "another message.\n\n"
    )


# Best-effort markers for "the resume target no longer exists in the agent CLI's
# local session store" (TTL eviction / workspace move). Verify against the running
# CLI during integration — a miss is safe (degrades to a normal failure, never a
# wrong answer).
_SESSION_NOT_FOUND_MARKERS = (
    "no conversation found",
    "session not found",
    "no session found",
    "could not find session",
    "no such session",
)


def _is_session_not_found(result) -> bool:
    blob = " ".join(
        filter(None, [
            getattr(result, "error", "") or "",
            getattr(result, "stderr_tail", "") or "",
            getattr(result, "stdout_tail", "") or "",
        ])
    ).lower()
    return any(marker in blob for marker in _SESSION_NOT_FOUND_MARKERS)


# Per-(thread, agent) serialization for resumed sessions (issue #53). The daemon
# runs a pool of thread workers (4) that drain each org's queue concurrently, so
# two pending invocations for the SAME Claude participant can otherwise run in
# parallel — both read the same stored session, both `--resume` it (undefined),
# and the last writer advances `last_resumed_seq` from stale state. This lock
# serializes the read→run→update path per (thread, agent). Locks are created
# lazily; `get`-then-assign is atomic across coroutines (no await between), and
# the daemon is single-event-loop. The key is scoped by org root so distinct orgs
# never share a lock. The registry grows unbounded with distinct (thread, agent)
# pairs over the daemon's lifetime — entries are tiny; revisit only if it matters.
_session_locks: dict[tuple[str, str, str], asyncio.Lock] = {}


def _session_lock(org_state, thread_id: str, agent_name: str) -> asyncio.Lock:
    key = (str(org_state.root), thread_id, agent_name)
    lock = _session_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _session_locks[key] = lock
    return lock


def build_thread_prompt(
    *,
    thread: ThreadRecord,
    participants: list[ThreadParticipant],
    messages: list[ThreadMessage],
    invocation_token: str,
    invoked_agent: str,
    purpose: str,          # 'reply' | 'bootstrap'
    triggering_seq: int,
) -> str:
    triggering = next((m for m in messages if m.seq == triggering_seq), None)
    parts_str = ", ".join(p.agent_name for p in participants)
    history = "\n".join(_render_message(m) for m in messages)
    forwarded = (
        f"Forwarded from {thread.forwarded_from_id}."
        if thread.forwarded_from_id else ""
    )
    note = _purpose_note(
        purpose, triggering_seq, invoked_agent,
        triggering_message=triggering,
    )
    doctrine = _decline_by_default_doctrine() if purpose == "reply" else ""
    return (
        f"{doctrine}"
        f"You are participating in thread {thread.id}: \"{thread.subject}\".\n\n"
        f"Participants: {parts_str}.\n"
        f"Started: {thread.started_at.isoformat()}. {forwarded}\n\n"
        f"Full message history follows. Most recent message is at the bottom.\n\n"
        f"---\n{history}\n\n"
        f"You have been invoked because:\n  {note}\n\n"
        f"Your invocation_token for this turn is: {invocation_token}\n"
        f"Include this token in every callback payload (reply, decline,\n"
        f"dispatch). It authorizes this single turn and is single-use for the\n"
        f"terminal callback (reply/decline).\n\n"
        f"Consult `protocol/skills/thread/SKILL.md` and respond.\n"
    )


def build_thread_delta_prompt(
    *,
    thread: ThreadRecord,
    new_messages: list[ThreadMessage],
    invocation_token: str,
    invoked_agent: str,
    purpose: str,
    triggering_seq: int,
    triggering_message: "ThreadMessage | None",
) -> str:
    """Turn 2+ prompt for a resumed agent session (issue #53).

    The full transcript, participant roster, and workspace bootstrap doc are
    already in the resumed session's memory — we ship only the messages newer
    than the stored watermark plus the per-turn doctrine, purpose note, and
    single-use invocation token. ``new_messages`` is the delta the caller
    computed (seq > last_resumed_seq).
    """
    note = _purpose_note(
        purpose, triggering_seq, invoked_agent,
        triggering_message=triggering_message,
    )
    doctrine = _decline_by_default_doctrine() if purpose == "reply" else ""
    delta = "\n".join(_render_message(m) for m in new_messages)
    return (
        f"{doctrine}"
        f"Continuing thread {thread.id}: \"{thread.subject}\". "
        f"New activity since your last turn follows.\n\n"
        f"---\n{delta}\n\n"
        f"You have been invoked because:\n  {note}\n\n"
        f"Your invocation_token for this turn is: {invocation_token}\n"
        f"Include this token in every callback payload (reply, decline,\n"
        f"dispatch). It authorizes this single turn and is single-use for the\n"
        f"terminal callback (reply/decline).\n\n"
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
    if provider == "pi":
        return PiExecutor(
            pi_cli_path=settings.pi_cli_path,
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

    workspace = org_state.root / "workspaces" / inv.agent_name

    # Read agent.yaml to pick the executor.
    try:
        from runtime.daemon.agent_config import load_agent_config
        agent_yaml = load_agent_config(Path(workspace)) or {}
    except Exception:
        agent_yaml = {}
    executor_name = (agent_yaml.get("executor") or "claude").lower()
    if executor_name not in _EXECUTOR_MAP:
        executor_name = "claude"

    # Build OrgPaths so ClaudeExecutor can resolve allow rules.
    try:
        from runtime.orchestrator._paths import OrgPaths
        paths = OrgPaths(root=org_state.root)
    except Exception:
        paths = None

    executor = _build_executor_for_provider(executor_name, settings, paths)

    # Resolve timeout (org override → code default).
    timeout: int = settings.session_timeout_seconds
    try:
        from runtime.orchestrator.org_config import load_org_config
        from runtime.orchestrator._paths import OrgPaths as _OrgPaths
        cfg = load_org_config(_OrgPaths(root=org_state.root))
        if cfg.threads_invocation_timeout_seconds is not None:
            timeout = cfg.threads_invocation_timeout_seconds
    except Exception:
        pass

    # --- Agent session resume (issue #53) ---
    # Only Claude supports --resume; other executors always run full-context.
    # Serialize the read→run→update path per (thread, agent): the worker pool runs
    # invocations concurrently, so two pending turns for the same Claude participant
    # would otherwise race the stored session + watermark (see _session_lock). The
    # guard is a no-op for non-Claude executors, which keep no session state.
    is_claude = executor_name == "claude"
    session_guard: contextlib.AbstractAsyncContextManager = (
        _session_lock(org_state, inv.thread_id, inv.agent_name)
        if is_claude
        else contextlib.nullcontext()
    )
    async with session_guard:
        stored_sid, last_seq = (
            org_state.db.get_thread_session(inv.thread_id, inv.agent_name)
            if is_claude else (None, 0)
        )
        resume_sid: str | None = None
        if is_claude and stored_sid:
            new_messages = [m for m in messages if m.seq > last_seq]
            triggering = next((m for m in messages if m.seq == inv.triggering_seq), None)
            prompt = build_thread_delta_prompt(
                thread=thread, new_messages=new_messages,
                invocation_token=invocation_token, invoked_agent=inv.agent_name,
                purpose=inv.purpose.value, triggering_seq=inv.triggering_seq,
                triggering_message=triggering,
            )
            resume_sid = stored_sid
            shown_seqs = [m.seq for m in new_messages]
        else:
            prompt = build_thread_prompt(
                thread=thread, participants=participants, messages=messages,
                invocation_token=invocation_token, invoked_agent=inv.agent_name,
                purpose=inv.purpose.value, triggering_seq=inv.triggering_seq,
            )
            shown_seqs = [m.seq for m in messages]

        org_state.db.stamp_invocation_started(invocation_token, session_id=None)
        await _publish_invocation_event(
            org_state, thread_id=inv.thread_id, agent_name=inv.agent_name,
            seq=inv.triggering_seq, kind="invocation_started", status="working",
        )
        audit = AuditLogger(org_state.db)

        def _invoke(run_prompt: str, resume: str | None):
            run_kwargs = dict(
                workspace=Path(workspace), prompt=run_prompt,
                session_id=None, timeout_seconds=timeout,
            )
            if resume:
                run_kwargs["resume_session_id"] = resume
            return executor.run(**run_kwargs)

        # Spawn subprocess in a thread pool (executors are synchronous).
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: _invoke(prompt, resume_sid))

            if (is_claude and resume_sid and not result.success
                    and _is_session_not_found(result)):
                audit.log_agent_session_evicted_fallback(
                    inv.thread_id, agent_name=inv.agent_name, executor="claude",
                    stale_session_id=resume_sid,
                    error=str(getattr(result, "error", "") or ""),
                )
                full_prompt = build_thread_prompt(
                    thread=thread, participants=participants, messages=messages,
                    invocation_token=invocation_token, invoked_agent=inv.agent_name,
                    purpose=inv.purpose.value, triggering_seq=inv.triggering_seq,
                )
                shown_seqs = [m.seq for m in messages]
                resume_sid = None
                result = await loop.run_in_executor(None, lambda: _invoke(full_prompt, None))
        except Exception as exc:
            org_state.db.fail_invocation(
                invocation_token,
                status=ThreadInvocationStatus.FAILED,
                decline_reason=f"runner_crash: {exc}",
            )
            audit.log_thread_invocation_failed(
                inv.thread_id,
                agent=inv.agent_name,
                token=invocation_token,
                purpose=inv.purpose.value,
                reason=str(exc),
            )
            # Clear the live "working" indicator: invocation_started already fired,
            # and a runner crash never reaches a route that publishes a terminal
            # event, so emit a seq-bearing settled event here to trigger refetch.
            await _publish_invocation_event(
                org_state, thread_id=inv.thread_id, agent_name=inv.agent_name,
                seq=inv.triggering_seq, kind="invocation_settled", status="failed",
            )
            return

        # Persist the (possibly forked / freshly-minted) session id + delta watermark.
        # Advanced only on a successful subprocess — a failed turn leaves the watermark
        # so the next resume re-includes the skipped messages.
        if is_claude and result.success and getattr(result, "agent_session_id", None):
            new_watermark = max(shown_seqs) if shown_seqs else last_seq
            new_watermark = max(new_watermark, last_seq)
            org_state.db.update_thread_session(
                inv.thread_id, inv.agent_name,
                agent_session_id=result.agent_session_id,
                last_resumed_seq=new_watermark,
            )
            if resume_sid:
                audit.log_agent_session_reused(
                    inv.thread_id, agent_name=inv.agent_name, executor="claude",
                    agent_session_id=result.agent_session_id,
                    triggering_seq=inv.triggering_seq,
                )

        # Inspect post-subprocess token state.
        after = org_state.db.get_invocation_any_status(invocation_token)
        if after is None:
            return
        if after.status in {ThreadInvocationStatus.CONSUMED, ThreadInvocationStatus.DECLINED}:
            # A reply (CONSUMED) already publishes a seq-bearing message event via
            # the reply route, which clears the indicator. A silent decline only
            # publishes decline_status with seq=null (ignored by the tail consumer),
            # so emit a settled event here to clear the "working" indicator live.
            if after.status is ThreadInvocationStatus.DECLINED:
                await _publish_invocation_event(
                    org_state, thread_id=inv.thread_id, agent_name=inv.agent_name,
                    seq=inv.triggering_seq, kind="invocation_settled", status="declined",
                )
            return

        # Subprocess exited without consuming → auto-decline.
        err_text = str(getattr(result, "error", "") or "").lower()
        rc = getattr(result, "returncode", "?")
        if "timeout" in err_text:
            reason = "invocation_timeout"
            status = ThreadInvocationStatus.TIMEOUT
        else:
            reason = f"no_callback: rc={rc}"
            detail = _executor_error_detail(result, rc)
            if detail:
                reason = f"{reason} — {detail}"
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
        await _publish_invocation_event(
            org_state, thread_id=inv.thread_id, agent_name=inv.agent_name,
            seq=inv.triggering_seq, kind="invocation_settled", status="failed",
        )
