"""Headless executor invocation for thread participation.

Single-turn lifecycle: build prompt → spawn subprocess → wait for token to be
consumed (via reply/decline callback) → exit. No NextStep loop.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
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
    GenericCliExecutor,
)
from runtime.orchestrator.executor_registry import build_executor, get_registry
from runtime.orchestrator.org_config import (
    OrgConfig,
    render_current_time_line,
    resolve_managed_skills_index,
    resolve_org_timezone_display,
    resolve_protocol_doc_manifest,
)
from runtime.orchestrator.workspace_adapters import (
    ensure_system_contracts_materialized,
    inject_managed_skills,
    inject_system_contracts,
    refresh_session_skills,
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


# Executor validation is registry-driven (THR-052). The registry singleton
# is the single source of truth for which executors are valid.
_EXECUTOR_MAP: dict[str, str] = {}  # populated lazily from registry


def _is_registered_executor(name: str) -> bool:
    """True when ``name`` resolves to a registered executor profile."""
    return get_registry().is_registered(name)


def _render_attachments_for_prompt(m: ThreadMessage) -> str:
    if not m.attachments:
        return ""
    lines = ["Attachments:"]
    for attachment in m.attachments:
        size = (
            f", {attachment.size_bytes} bytes"
            if attachment.size_bytes is not None
            else ""
        )
        lines.append(
            f"- {attachment.display_name} "
            f"(`artifact:{attachment.artifact_name}`{size})"
        )
    return "\n".join(lines)


def _render_message(m: ThreadMessage) -> str:
    ts = m.created_at.isoformat()
    if m.kind is ThreadMessageKind.MESSAGE:
        head = f"[Message {m.seq} — {m.speaker} · {ts}]"
        body = m.body_markdown or ""
        attachments = _render_attachments_for_prompt(m)
        return "\n".join(filter(None, [head, "", body, attachments])) + "\n---"
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
        if status == "escalated":
            reason = (payload.get("reason") or "").strip()
            reason_clause = f': "{reason[:240]}"' if reason else ""
            return (
                f"Task {task_id} that you dispatched from this thread has "
                f"ESCALATED to the founder{reason_clause}. The task is blocked "
                f"awaiting a founder decision. Post a concise reply in this "
                f"thread that states what you need from the founder and why, so "
                f"she sees it in context (pull details via `happyranch details "
                f"{task_id}`). Do not attempt to resolve the escalation "
                f"yourself; do not dispatch a new task from this turn. Decline "
                f"if the escalation already says everything and a thread "
                f"restatement adds nothing."
            )
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


def _maybe_unresolved_escalations_note(
    *,
    messages: list[ThreadMessage],
    org_state,
    purpose: str,
    invoked_agent: str,
) -> str:
    """Guardrail: when a manager receives a REPLY/BOOTSTRAP invocation in a
    thread that carries unresolved ``task_escalated`` system messages whose live
    task rows are still supersedable, surface the concrete task ids so the agent
    knows to include ``resolves`` in any continuation dispatch.

    Derived from thread messages + task status, never from brief prose.
    """
    if purpose not in ("reply", "bootstrap"):
        return ""
    # Only fire for thread participants who can actually close a predecessor —
    # a worker self-dispatch that names resolves is rejected 403 anyway.
    teams = getattr(org_state, "teams", None)
    if teams is None or not teams.is_team_manager(invoked_agent):
        return ""
    escalated_ids: list[str] = []
    for m in messages:
        if m.kind is not ThreadMessageKind.SYSTEM:
            continue
        payload = m.system_payload or {}
        if payload.get("kind_tag") != "task_escalated":
            continue
        task_id = payload.get("task_id", "")
        if not task_id or task_id in escalated_ids:
            continue
        task = org_state.db.get_task(task_id)
        if task is None:
            continue
        # Check supersedability — same logic as _eligible_supersede_block_kind
        # in routes/tasks.py; imported late to avoid circular imports.
        from runtime.daemon.routes.tasks import _eligible_supersede_block_kind
        if _eligible_supersede_block_kind(org_state, task) is None:
            continue
        escalated_ids.append(task_id)
    if not escalated_ids:
        return ""
    if len(escalated_ids) == 1:
        tid = escalated_ids[0]
        return (
            "\n## Unresolved Escalation in This Thread\n\n"
            f"Task **{tid}** escalated in this thread and is still "
            f"awaiting a founder-authorized continuation.\n\n"
            f"If your next self-dispatched task is the continuation, you MUST "
            f"include the explicit linkage in your dispatch payload:\n"
            f'  ```json\n'
            f'  {{"resolves": "{tid}"}}\n'
            f'  ```\n'
            f"Omitting this field leaves the predecessor open — the runtime cannot "
            f"infer the relationship from brief prose alone.\n\n"
        )
    # Multiple unresolved escalations — show per-task valid examples.
    per_task_lines = "\n".join(
        f'  {tid} →' + ' {"resolves": "' + tid + '"}'
        for tid in escalated_ids
    )
    ids_str = ", ".join(escalated_ids)
    return (
        "\n## Unresolved Escalations in This Thread\n\n"
        f"The following tasks escalated in this thread and are still "
        f"awaiting a founder-authorized continuation: **{ids_str}**.\n\n"
        f"If your next self-dispatched task is the continuation of one of these, "
        f"you MUST include the explicit linkage for the specific predecessor "
        f"your continuation supersedes:\n"
        f"{per_task_lines}\n\n"
        f"Omitting this field leaves the predecessor open — the runtime cannot "
        f"infer the relationship from brief prose alone.\n\n"
    )


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


# Per-(thread, agent) active-invocation lock (provider-agnostic, THR-042). The
# daemon runs a pool of thread workers that drain each org's queue concurrently,
# so two pending invocations for the SAME (org, thread, agent) can otherwise run
# in parallel — two subprocess sessions for the same agent in the same thread
# would race callback consumption and (for Claude) the stored session + watermark.
# This lock serializes the acquire→run→settle path per (org, thread, agent).
# Locks are created lazily; `get`-then-assign is atomic across coroutines (no
# await between), and the daemon is single-event-loop. The key is scoped by org
# root so distinct orgs never share a lock. The registry grows unbounded with
# distinct (thread, agent) pairs over the daemon's lifetime — entries are tiny;
# revisit only if it matters.
_invocation_locks: dict[tuple[str, str, str], asyncio.Lock] = {}


def _invocation_lock(org_state, thread_id: str, agent_name: str) -> asyncio.Lock:
    key = (str(org_state.root), thread_id, agent_name)
    lock = _invocation_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _invocation_locks[key] = lock
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
    org_config: OrgConfig,
    now: Callable[[], datetime] | None = None,
    managed_skills_index: str = "",
    protocol_doc_manifest: str = "",
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
    # current_time is injected (fresh per turn) via the shared renderer using
    # the org's effective timezone, so thread sessions carry the same local
    # wall clock as every other agent session.
    tz, label = resolve_org_timezone_display(org_config)
    current_time = render_current_time_line(tz, label, now)
    skills_block = f"\n{managed_skills_index}\n" if managed_skills_index else ""
    docs_block = f"\n{protocol_doc_manifest}\n" if protocol_doc_manifest else ""
    return (
        f"{doctrine}"
        f"You are participating in thread {thread.id}: \"{thread.subject}\".\n\n"
        f"Participants: {parts_str}.\n"
        f"current_time: {current_time}{skills_block}{docs_block}\n"
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
    org_config: OrgConfig,
    now: Callable[[], datetime] | None = None,
    managed_skills_index: str = "",
    protocol_doc_manifest: str = "",
) -> str:
    """Turn 2+ prompt for a resumed agent session (issue #53).

    The full transcript, participant roster, and workspace bootstrap doc are
    already in the resumed session's memory — we ship only the messages newer
    than the stored watermark plus the per-turn doctrine, purpose note, and
    single-use invocation token. ``new_messages`` is the delta the caller
    computed (seq > last_resumed_seq).

    ``current_time`` is re-injected on this resumed turn (fresh per turn) so the
    agent sees the current local wall clock even mid-thread. ``now`` is
    injectable for tests.
    """
    note = _purpose_note(
        purpose, triggering_seq, invoked_agent,
        triggering_message=triggering_message,
    )
    doctrine = _decline_by_default_doctrine() if purpose == "reply" else ""
    delta = "\n".join(_render_message(m) for m in new_messages)
    tz, label = resolve_org_timezone_display(org_config)
    current_time = render_current_time_line(tz, label, now)
    skills_block = f"\n{managed_skills_index}\n" if managed_skills_index else ""
    docs_block = f"\n{protocol_doc_manifest}\n" if protocol_doc_manifest else ""
    return (
        f"{doctrine}"
        f"Continuing thread {thread.id}: \"{thread.subject}\". "
        f"New activity since your last turn follows.\n\n"
        f"current_time: {current_time}{skills_block}{docs_block}\n\n"
        f"---\n{delta}\n\n"
        f"You have been invoked because:\n  {note}\n\n"
        f"Your invocation_token for this turn is: {invocation_token}\n"
        f"Include this token in every callback payload (reply, decline,\n"
        f"dispatch). It authorizes this single turn and is single-use for the\n"
        f"terminal callback (reply/decline).\n\n"
        f"Consult `protocol/skills/thread/SKILL.md` and respond.\n"
    )


def _build_executor_for_provider(provider: str, settings: Settings, paths):
    """Construct the right executor for a given provider string.

    Delegates to the shared registry factory (THR-052).
    """
    return build_executor(provider, settings, paths)


def _persist_thread_token_usage(
    org_state,
    *,
    inv,
    result,
    executor_name: str,
    invocation_token: str,
) -> None:
    token_usage = getattr(result, "token_usage", None)
    if token_usage is None:
        return
    session_id = getattr(result, "session_id", None) or invocation_token
    try:
        org_state.db.insert_session_token_usage(
            task_id=None,
            agent=inv.agent_name,
            session_id=session_id,
            executor=executor_name,
            token_usage=token_usage,
            scope_type="thread",
            scope_id=inv.thread_id,
            thread_id=inv.thread_id,
            invocation_purpose=inv.purpose.value,
        )
    except Exception as exc:
        logger.warning(
            "thread token usage persistence failed for %s/%s: %s",
            inv.thread_id,
            inv.agent_name,
            exc,
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
    if not _is_registered_executor(executor_name):
        executor_name = "claude"

    # Build OrgPaths so ClaudeExecutor can resolve allow rules.
    try:
        from runtime.orchestrator._paths import OrgPaths
        paths = OrgPaths(root=org_state.root)
    except Exception:
        paths = None

    executor = _build_executor_for_provider(executor_name, settings, paths)

    # Load org config once: it feeds both the timeout override and the
    # current_time injection on every thread prompt below. A malformed/missing
    # config falls back to defaults (which resolve to machine-local/UTC).
    from runtime.orchestrator._paths import OrgPaths as _OrgPaths
    from runtime.orchestrator.org_config import load_org_config, resolve_org_setting_threads
    try:
        org_config = load_org_config(_OrgPaths(root=org_state.root))
    except Exception:
        org_config = OrgConfig()

    # Resolve managed skills index once for all 3 prompt builders in this invocation.
    try:
        managed_skills_index = resolve_managed_skills_index(
            paths=paths, agent_name=inv.agent_name,
        )
    except Exception:
        managed_skills_index = ""

    # Refresh on-disk skill bodies on EVERY session (THR-070).
    try:
        refresh_session_skills(workspace, settings, slug=org_state.slug)
    except Exception:
        pass

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
            workspace, settings, slug=org_state.slug, context="thread",
            provider=executor_name,
        )
    except SystemContractMaterializationError as e:
        org_state.db.fail_invocation(
            invocation_token,
            status=ThreadInvocationStatus.FAILED,
            decline_reason=str(e),
        )
        return

    # Managed-catalog skill injection (THR-055 Phase 4).
    try:
        agent_team = "engineering"
        for p in participants:
            if p.agent_name == inv.agent_name:
                agent_team = p.team
                break
    except Exception:
        agent_team = "engineering"
    try:
        skills_root = settings.project_root / "runtime" / "skills"
        inject_managed_skills(
            workspace, settings,
            slug=org_state.slug,
            agent_name=inv.agent_name,
            team=agent_team,
            skills_root=skills_root,
        )
    except Exception:
        pass

    # Protocol doc manifest — bundled-path one-liner per doc (THR-070).
    try:
        protocol_doc_manifest = resolve_protocol_doc_manifest(settings=settings)
    except Exception:
        protocol_doc_manifest = ""

    # THR-095: resolve threads settings from DB (override) → config.yaml (default).
    threads_cfg = resolve_org_setting_threads(org_state.db, code_default=org_config)
    timeout: int = settings.session_timeout_seconds
    if threads_cfg["invocation_timeout_seconds"] is not None:
        timeout = threads_cfg["invocation_timeout_seconds"]

    # --- Active-invocation lock (provider-agnostic, THR-042) ---
    # Every executor must acquire the per-(org, thread, agent) lock so no two
    # subprocess sessions for the same agent in the same thread run concurrently.
    # Only Claude supports --resume and manages thread_session state; the lock
    # now protects all providers against concurrent runs, not just Claude.
    is_claude = executor_name == "claude"
    invocation_guard = _invocation_lock(org_state, inv.thread_id, inv.agent_name)
    async with invocation_guard:
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
                triggering_message=triggering, org_config=org_config,
                managed_skills_index=managed_skills_index,
                protocol_doc_manifest=protocol_doc_manifest,
            )
            resume_sid = stored_sid
            shown_seqs = [m.seq for m in new_messages]
        else:
            prompt = build_thread_prompt(
                thread=thread, participants=participants, messages=messages,
                invocation_token=invocation_token, invoked_agent=inv.agent_name,
                purpose=inv.purpose.value, triggering_seq=inv.triggering_seq,
                org_config=org_config,
                managed_skills_index=managed_skills_index,
                protocol_doc_manifest=protocol_doc_manifest,
            )
            shown_seqs = [m.seq for m in messages]

        # Guardrail: surface unresolved escalated tasks from this thread so a
        # manager continuation dispatch includes the explicit resolves linkage.
        escalation_note = _maybe_unresolved_escalations_note(
            messages=messages,
            org_state=org_state,
            purpose=inv.purpose.value,
            invoked_agent=inv.agent_name,
        )
        if escalation_note:
            prompt += "\n" + escalation_note

        org_state.db.stamp_invocation_started(invocation_token, session_id=None)
        await _publish_invocation_event(
            org_state, thread_id=inv.thread_id, agent_name=inv.agent_name,
            seq=inv.triggering_seq, kind="invocation_started", status="working",
        )
        audit = AuditLogger(org_state.db)

        # Layer-1 throttle audit surfacing (issue #85): the per-provider throttle
        # in executors._run_command calls this on a slot wait or a 429 backoff.
        # Additive action+payload via the existing insert_audit_log — no new
        # columns, no row-shape change. task_id carries the THR- scope id, exactly
        # as the other thread-scoped audit rows do.
        def _on_throttle_event(action: str, payload: dict) -> None:
            org_state.db.insert_audit_log(inv.thread_id, inv.agent_name, action, payload)

        def _invoke(run_prompt: str, resume: str | None):
            run_kwargs = dict(
                workspace=Path(workspace), prompt=run_prompt,
                session_id=None, timeout_seconds=timeout,
                on_throttle_event=_on_throttle_event,
            )
            if resume:
                run_kwargs["resume_session_id"] = resume
            return executor.run(**run_kwargs)

        # Spawn subprocess in a thread pool (executors are synchronous).
        fallback_executed = False  # tracks session-not-found eviction fallback
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
                    org_config=org_config,
                    managed_skills_index=managed_skills_index,
                    protocol_doc_manifest=protocol_doc_manifest,
                )
                # Re-apply the guardrail for the fallback prompt too.
                escalation_note2 = _maybe_unresolved_escalations_note(
                    messages=messages,
                    org_state=org_state,
                    purpose=inv.purpose.value,
                    invoked_agent=inv.agent_name,
                )
                if escalation_note2:
                    full_prompt += "\n" + escalation_note2
                shown_seqs = [m.seq for m in messages]
                resume_sid = None
                fallback_executed = True
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

        _persist_thread_token_usage(
            org_state,
            inv=inv,
            result=result,
            executor_name=executor_name,
            invocation_token=invocation_token,
        )

        # Inspect post-subprocess token state BEFORE updating thread session.
        # An invocation that became terminal via an external path during subprocess
        # execution (e.g. founder abort) must NOT have its agent_session_id stored
        # as the resumable Claude session for a later reply.
        after = org_state.db.get_invocation_any_status(invocation_token)
        if after is None:
            return
        if after.status in {ThreadInvocationStatus.CONSUMED, ThreadInvocationStatus.DECLINED}:
            # A reply (CONSUMED) already publishes a seq-bearing message event via
            # the reply route, which clears the indicator. A silent decline only
            # publishes decline_status with seq=null (ignored by the tail consumer),
            # so emit a settled event here to clear the "working" indicator live.
            #
            # On a CONSUMED/DECLINED turn, the subprocess produced a real callback;
            # the agent_session_id is valid and should be persisted for future resume.
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
            if after.status is ThreadInvocationStatus.DECLINED:
                await _publish_invocation_event(
                    org_state, thread_id=inv.thread_id, agent_name=inv.agent_name,
                    seq=inv.triggering_seq, kind="invocation_settled", status="declined",
                )
            return

        # Externally-failed / timed-out invocation: the row was already set to a
        # terminal state by another path (e.g. founder abort, archive reap).
        # Preserve the existing reason — do not overwrite with no_callback.
        # Crucial: do NOT call update_thread_session here — the aborted invocation's
        # agent_session_id must never become the resumable Claude session.
        if after.status in {ThreadInvocationStatus.FAILED, ThreadInvocationStatus.TIMEOUT}:
            logger.info(
                "run_invocation: token %s already terminal (%s), skipping auto-decline",
                invocation_token[:8], after.status.value,
            )
            return

        # Invocation is still pending — subprocess exited without consuming.
        # Persist the (possibly forked / freshly-minted) session id + delta
        # watermark. Advanced only on a successful subprocess — a failed turn
        # leaves the watermark so the next resume re-includes the skipped messages.
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

        # --- THR-071 slice (3): bounded terminal-callback enforcement ---
        # The model finished its run but forgot the terminal callback (clean
        # exit, rc==0, invocation still pending). Re-invoke EXACTLY ONCE with
        # a corrective NUDGE prompt. Fire ONLY on result.success/rc==0;
        # rc!=0 / infra paths (timeout / runner_crash / 529) are untouched.
        # Do NOT fire after the session-not-found eviction fallback — that
        # is already a second chance, so a third would be excessive.
        if result.success and not fallback_executed:
            nudge_prompt = (
                "## URGENT — you ended without posting a reply or declining\n\n"
                "You completed your analysis but exited the conversation "
                "without posting a terminal callback. You MUST now call exactly "
                "one of these commands with the SAME invocation_token:\n\n"
                "- `happyranch threads reply --from-file <payload>` if you "
                "have a substantive reply to post.\n"
                "- `happyranch threads decline --from-file <payload>` if you "
                "have nothing to add.\n\n"
                f"Your invocation_token (still valid): {invocation_token}\n"
                "This is your LAST chance — this single-use token will be "
                "auto-declined if you exit again without calling one of these."
            )

            if is_claude and getattr(result, "agent_session_id", None):
                # Resume the same agent session and append the nudge.
                retry_prompt = nudge_prompt
                retry_resume_sid: str | None = result.agent_session_id
            else:
                # Non-resumable executor: rebuild full prompt + corrective note.
                retry_prompt = (
                    build_thread_prompt(
                        thread=thread, participants=participants, messages=messages,
                        invocation_token=invocation_token, invoked_agent=inv.agent_name,
                        purpose=inv.purpose.value, triggering_seq=inv.triggering_seq,
                        org_config=org_config,
                        managed_skills_index=managed_skills_index,
                    )
                    + "\n"
                    + (escalation_note + "\n" if escalation_note else "")
                    + nudge_prompt
                )
                retry_resume_sid = None
                # Update shown_seqs for non-resumable (full prompt rebuild).
                shown_seqs = [m.seq for m in messages]

            logger.info(
                "run_invocation: token %s clean exit without callback — "
                "re-invoking once with nudge (resume=%s)",
                invocation_token[:8], retry_resume_sid,
            )

            retry_exc: Exception | None = None
            try:
                retry_result = await loop.run_in_executor(
                    None, lambda: _invoke(retry_prompt, retry_resume_sid),
                )
            except Exception as exc:
                logger.warning(
                    "run_invocation: token %s nudge re-invoke crashed: %s",
                    invocation_token[:8], exc,
                )
                retry_result = None
                retry_exc = exc

            if retry_result is not None:
                _persist_thread_token_usage(
                    org_state,
                    inv=inv,
                    result=retry_result,
                    executor_name=executor_name,
                    invocation_token=invocation_token,
                )

            # Re-inspect after the re-invoke.
            after = org_state.db.get_invocation_any_status(invocation_token)
            if after is None:
                return

            if after.status in {ThreadInvocationStatus.CONSUMED, ThreadInvocationStatus.DECLINED}:
                # The nudge worked — terminal callback happened during the
                # re-invoke. Persist the retry session for future resume.
                if (is_claude and retry_result is not None and retry_result.success
                        and getattr(retry_result, "agent_session_id", None)):
                    new_watermark = max(shown_seqs) if shown_seqs else last_seq
                    new_watermark = max(new_watermark, last_seq)
                    org_state.db.update_thread_session(
                        inv.thread_id, inv.agent_name,
                        agent_session_id=retry_result.agent_session_id,
                        last_resumed_seq=new_watermark,
                    )
                    if retry_resume_sid:
                        audit.log_agent_session_reused(
                            inv.thread_id, agent_name=inv.agent_name,
                            executor="claude",
                            agent_session_id=retry_result.agent_session_id,
                            triggering_seq=inv.triggering_seq,
                        )
                if after.status is ThreadInvocationStatus.DECLINED:
                    await _publish_invocation_event(
                        org_state, thread_id=inv.thread_id,
                        agent_name=inv.agent_name,
                        seq=inv.triggering_seq, kind="invocation_settled",
                        status="declined",
                    )
                return

            # Still pending after the nudge → mirror first-pass classification
            # (HIGH-2 REVISE): only tag no_callback_after_reprompt for a CLEAN retry
            # exit (rc==0); exception → runner_crash, timeout → invocation_timeout,
            # rc!=0 → no_callback: rc=N.
            if retry_result is not None and retry_result.success:
                # Session may still be persistable (clean exit from the nudge).
                if (is_claude and getattr(retry_result, "agent_session_id", None)):
                    new_watermark = max(shown_seqs) if shown_seqs else last_seq
                    new_watermark = max(new_watermark, last_seq)
                    org_state.db.update_thread_session(
                        inv.thread_id, inv.agent_name,
                        agent_session_id=retry_result.agent_session_id,
                        last_resumed_seq=new_watermark,
                    )

            if retry_result is None:
                # Exception during nudge re-invoke.
                reason = f"runner_crash: {retry_exc}"
                status = ThreadInvocationStatus.FAILED
            else:
                err_text = str(getattr(retry_result, "error", "") or "").lower()
                retry_rc = getattr(retry_result, "returncode", "?")
                if "timeout" in err_text:
                    reason = "invocation_timeout"
                    status = ThreadInvocationStatus.TIMEOUT
                elif retry_rc != 0:
                    reason = f"no_callback: rc={retry_rc}"
                    detail = _executor_error_detail(retry_result, retry_rc)
                    if detail:
                        reason = f"{reason} — {detail}"
                    status = ThreadInvocationStatus.FAILED
                else:
                    reason = f"no_callback_after_reprompt: rc={retry_rc}"
                    detail = _executor_error_detail(retry_result, retry_rc)
                    if detail:
                        reason = f"{reason} — {detail}"
                    status = ThreadInvocationStatus.FAILED

            org_state.db.fail_invocation(
                invocation_token,
                status=status,
                decline_reason=reason,
            )
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
            return

        # Subprocess exited without consuming (rc!=0 / timeout) → auto-decline.
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
