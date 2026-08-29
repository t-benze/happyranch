"""Headless executor invocation for thread participation.

Single-turn lifecycle: build prompt → spawn subprocess → wait for token to be
consumed (via reply/decline callback) → exit. No NextStep loop.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
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
    ThreadReplyClaim,
)
from runtime.orchestrator.executors import (
    ExecutorResult,
    GenericCliExecutor,
)
from runtime.orchestrator.executor_registry import build_executor, get_registry
from runtime.orchestrator.host_supervisor import (
    AdmissionRequest,
    HostSessionSupervisor,
    LaunchResult,
    TerminalReason,
)
from runtime.orchestrator.org_config import (
    OrgConfig,
    render_current_time_line,
    resolve_managed_skills_index,
    resolve_org_timezone_display,
    resolve_protocol_doc_manifest,
)
from runtime.orchestrator.workspace_adapters import (
    format_repo_refresh_note,
    materialize_workspace_skills,
    refresh_workspace_repos,
    validate_workspace_skills_integrity,
    WorkspaceIntegrityError,
    SystemContractMaterializationError,
)

logger = logging.getLogger(__name__)

# Cap for the underlying-error detail appended to a no_callback reason so a
# multi-KB stdout/stderr tail can't bloat the audit row.
_REASON_DETAIL_CAP = 2000


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


@dataclass(frozen=True)
class _InvokeResult:
    """One executor phase outcome for ``run_invocation``.

    ``result`` is the executor ``ExecutorResult`` when a launch body ran;
    ``None`` when nothing ran (pre-launch terminal winner).
    ``terminal_reason`` is the supervisor's durable first-wins reason
    (``None`` on the legacy uncontained path); ``error`` the outcome error
    text when set.
    """

    result: "ExecutorResult | None"
    terminal_reason: "TerminalReason | None" = None
    error: str | None = None


# Terminal reasons that mean the daemon lifecycle interrupted the invocation
# (drain or cancellation) — the row is left for daemon-restart recovery rather
# than settled, matching the pre-wiring shutdown semantics.
_INTERRUPTED_TERMINALS = (TerminalReason.SHUTDOWN, TerminalReason.CANCELLED)


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
                f"awaiting a bounded-continuation assessment. First evaluate the "
                f"existing THR-166 policy against the server-recorded causal "
                f"terminal result; if it is eligible, submit the structured "
                f"continuation request. Otherwise post the precise founder decision "
                f"needed (pull details via `happyranch details {task_id}`). Do not "
                f"dispatch repair work from this turn. Acceptance only resumes this "
                f"SAME root's ordinary lifecycle, which must delegate repair, review, "
                f"and reverify before returning to the original protected gate; this "
                f"follow-up never authorizes that gate."
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


def _manual_break_glass_cli_example(task_id: str, invoked_agent: str) -> str:
    return (
        f'  `happyranch resolve-escalation --task-id {task_id} '
        f'--decision continue --as-agent {invoked_agent} '
        f'--rationale "<summarize the founder\'s reply>"`'
    )


def _resolves_json_example(task_id: str) -> str:
    return f'  {task_id} → {{"resolves": "{task_id}"}}'


def _maybe_unresolved_escalations_note(
    *,
    messages: list[ThreadMessage],
    org_state,
    purpose: str,
    invoked_agent: str,
) -> str:
    """Guardrail: when a manager receives a REPLY/BOOTSTRAP invocation in a
    thread that carries unresolved ``task_escalated`` system messages whose live
    task rows are still supersedable, surface the concrete task ids and the
    resolution options available for each: the named manual break-glass
    ``continue`` route (only for a founder-directed manual action on an
    ``"escalated"`` predecessor) and ``resolves`` (dispatch a new task naming
    the predecessor — valid for both ``"escalated"`` and ``"delegated"``
    block kinds).

    Derived from thread messages + task status, never from brief prose.
    """
    if purpose not in ("reply", "bootstrap"):
        return ""
    # Only fire for thread participants who can actually close a predecessor —
    # a worker self-dispatch that names resolves is rejected 403 anyway.
    teams = getattr(org_state, "teams", None)
    if teams is None or not teams.is_team_manager(invoked_agent):
        return ""
    from runtime.daemon.routes.tasks import _eligible_supersede_block_kind

    escalated: list[tuple[str, str]] = []  # (task_id, block_kind)
    seen_ids: set[str] = set()
    for m in messages:
        if m.kind is not ThreadMessageKind.SYSTEM:
            continue
        payload = m.system_payload or {}
        if payload.get("kind_tag") != "task_escalated":
            continue
        task_id = payload.get("task_id", "")
        if not task_id or task_id in seen_ids:
            continue
        task = org_state.db.get_task(task_id)
        if task is None:
            continue
        block_kind = _eligible_supersede_block_kind(org_state, task)
        if block_kind is None:
            continue
        escalated.append((task_id, block_kind))
        seen_ids.add(task_id)
    if not escalated:
        return ""

    if len(escalated) == 1:
        tid, block_kind = escalated[0]
        if block_kind == "escalated":
            return (
                "\n## Unresolved Escalation in This Thread\n\n"
                f"Task **{tid}** escalated in this thread and is still "
                f"awaiting a founder-authorized resolution. Pick the option "
                f"that matches the founder's reply:\n\n"
                f"- Do not self-authorize a continue from the reply or its "
                f"prose. The direct route is a named manual break-glass "
                f"exception under the shared-bearer model; use it only for "
                f"a founder-directed manual action:\n"
                f"{_manual_break_glass_cli_example(tid, invoked_agent)}\n\n"
                f"- If the founder's reply requires new delegated work, "
                f"your next self-dispatched task MUST include the explicit "
                f"linkage:\n"
                f'  ```json\n'
                f'  {{"resolves": "{tid}"}}\n'
                f'  ```\n'
                f"  Omitting this field leaves the predecessor open — the "
                f"runtime cannot infer the relationship from brief prose "
                f"alone.\n\n"
            )
        # block_kind == "delegated": continue is not valid (requires literal
        # ESCALATED status) — resolves is the only option, unchanged text.
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

    # Multiple unresolved escalations — show per-task options, each keyed to
    # its own task id and its own block-kind eligibility.
    ids_str = ", ".join(tid for tid, _ in escalated)
    per_task_lines: list[str] = []
    for tid, block_kind in escalated:
        if block_kind == "escalated":
            per_task_lines.append(
                f"**{tid}** — do not self-authorize from a reply. The direct "
                f"continue route is manual break-glass only:\n"
                f"{_manual_break_glass_cli_example(tid, invoked_agent)}\n"
                f"  Otherwise, if new delegated work is needed:\n"
                f"{_resolves_json_example(tid)}"
            )
        else:
            per_task_lines.append(
                f"**{tid}** — {_resolves_json_example(tid).strip()}"
            )
    per_task_block = "\n\n".join(per_task_lines)
    return (
        "\n## Unresolved Escalations in This Thread\n\n"
        f"The following tasks escalated in this thread and are still "
        f"awaiting a founder-authorized resolution: **{ids_str}**.\n\n"
        f"For each, pick the option that matches the founder's reply. If "
        f"your next self-dispatched task is the continuation of one of "
        f"these via `resolves`, you MUST include the explicit linkage for "
        f"the specific predecessor your continuation supersedes:\n"
        f"{per_task_block}\n\n"
        f"Omitting the `resolves` field leaves the predecessor open — the "
        f"runtime cannot infer the relationship from brief prose alone.\n\n"
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
        "  recent reply.\n"
        "- You have not already substantively answered that question, request,\n"
        "  or hand-off in a later message of your own.\n\n"
        "Before replying, read the full conversation supplied in this\n"
        "invocation — every message in the history, not just the newest\n"
        "messages in this wake's delivery range — and check whether this wake\n"
        "re-delivers a request you already answered. (In a resumed session, the\n"
        "earlier transcript is already in your context; read it too.) If your\n"
        "own later message already substantively answered the request, decline\n"
        "silently — nothing further is owed. Coverage is a question of\n"
        "substance, never of sequence or position alone: a later message from\n"
        "you that merely acknowledges, restates, or agrees does not count as\n"
        "an answer.\n\n"
        "Exception: if the newest message in this wake's delivery range\n"
        "contains a distinct request you have not yet answered — for example a\n"
        "genuine follow-up question — reply as you normally would. Do not use\n"
        "the \"already answered\" rule to suppress legitimate follow-up\n"
        "questions.\n\n"
        "The founder is a participant; she reads the full thread in the web UI.\n"
        "You do not need to \"keep her informed\" by replying.\n\n"
        "If you are unsure: decline. The thread can always be re-engaged by\n"
        "another message.\n\n"
    )


# Executor-specific provider-declared session-not-found contracts. Each is
# proven against the INSTALLED CLI (TASK-5977 audit + 2026-08-28 TASK-6008
# re-probe; bounded local probes, no API traffic) and carries the exact
# ATTEMPTED provider session id verbatim:
#   - claude 2.1.241: rc=1, stderr `No conversation found with session ID:
#     <uuid>` — the single evidence-backed complete form; the THR-200-era
#     generic legacy markers ("session not found", "no session found", …)
#     carry no immutable producer/CLI evidence and are NOT accepted.
#   - codex 0.148.0: rc=1, stderr `Error: thread/resume: thread/resume
#     failed: no rollout found for thread id <uuid> (code -32600)`
#   - pi 0.84.2: rc=1, stderr `No session found matching '<id>'`
# All three emit the signature on STDERR with exit code 1 and empty stdout,
# and each echoes the attempted id verbatim (claude after ``session ID:``,
# codex after ``thread id``, pi inside single quotes).
# Classification is dispatched on the executor that actually ran, reads ONLY
# the proven stream (stderr_tail — never the ``error`` envelope, which falls
# back to stdout text when stderr is empty), requires the proven return code
# (1), and requires the ANCHORED provider-declared signature bound to the
# regex-escaped attempted session id. For EVERY executor the match is a
# COMPLETE-LINE constraint: the observed signature must start a line and the
# bound attempted id (plus the executor's literal tokens — codex ``(code
# -32600)``, pi's quotes) must be the last content on that line
# (horizontal-whitespace-only padding allowed; unrelated text on other lines
# tolerated) — the observed provider contract is the one complete stderr
# line: claude ``No conversation found with session ID: <attempted-id>``,
# codex ``Error: thread/resume: thread/resume failed: no rollout found for
# thread id <attempted-id> (code -32600)`` (the CLI envelope is part of the
# observed line), pi ``No session found matching '<attempted-id>'``. Only
# HORIZONTAL whitespace [ \t] may pad where the observed contract permits
# spacing — ``\s`` never consumes LF/CRLF, so split-line forms can never
# match. Wrong/missing id, prefix/suffix near-matches (including
# punctuation-led suffixes a word boundary would accept), arbitrary
# same-line prefix/suffix text, cross-provider text, stdout-only text, wrong
# rc, generic legacy substrings, a marker embedded in auth/quota/transport
# output, and ambiguous output never match — a miss is safe (degrades to a
# normal failure — no fresh retry, session id/watermark untouched — never a
# wrong answer).
_CLAUDE_EVICTION_SIGNATURE = "no conversation found with session id:"
_CODEX_EVICTION_SIGNATURE = (
    "error: thread/resume: thread/resume failed: "
    "no rollout found for thread id"
)
_CODEX_EVICTION_JSON_RPC_CODE = "(code -32600)"
_PI_EVICTION_SIGNATURE = "no session found matching"
# Auth/quota/transport signal tokens: an otherwise matching eviction marker
# EMBEDDED in such output is not the provider declaring the session missing
# (a 401/429/timeout/network blob is not session eviction). The attempted id
# is stripped from stderr before this scan so an id that happens to contain
# such a digit/letter run (e.g. a hex id with "401") can never self-trigger.
_AUTH_QUOTA_TRANSPORT_TOKENS = (
    "unauthorized",
    "authentication",
    "forbidden",
    "401",
    "402",
    "403",
    "429",
    "quota",
    "rate limit",
    "rate_limit",
    "overloaded",
    "payment",
    "credit",
    "billing",
    "timeout",
    "timed out",
    "connection reset",
    "connection refused",
    "network error",
    "dns",
    "proxy",
    "tls",
    "ssl",
    "certificate",
    "panic:",
    "internal error",
    "segmentation fault",
)


def _escaped_attempted_id(attempted_session_id: str | None) -> str | None:
    """Regex-escaped lowercase attempted session id, or None when absent."""
    if not attempted_session_id:
        return None
    return re.escape(attempted_session_id.lower())


def _contains_auth_quota_transport_token(stderr: str, attempted_session_id: str) -> bool:
    """True when stderr (minus the attempted id, so a hex id containing a
    token-like run can never self-trigger) carries any auth/quota/transport
    signal token — such output is never the eviction contract."""
    if attempted_session_id:
        stderr = stderr.replace(attempted_session_id.lower(), "")
    return any(token in stderr for token in _AUTH_QUOTA_TRANSPORT_TOKENS)


def _is_claude_session_evicted(result, attempted_session_id: str) -> bool:
    """Claude 2.1.241 proven contract: rc=1, stderr exactly
    ``No conversation found with session ID: <attempted-id>`` — the COMPLETE
    observed provider stderr line. The signature and the regex-escaped
    attempted id must occur on the SAME physical stderr line (horizontal
    whitespace only where spacing is permitted; LF/CRLF are never consumed),
    the signature starts the line, and the id is the last non-whitespace
    content on it (complete-line constraint) — punctuation-led or
    alphanumeric suffixes (``01a0-live-suffix``, ``01a0-live.``, …), prefix
    text on the signature line, and split-line forms where the signature
    terminates line N and the id starts line N+1 (LF, CRLF, or
    whitespace-indented) never match. The removed THR-200-era generic legacy
    substrings never match, and Pi's distinct signature never matches (no
    shared-substring acceptance)."""
    if getattr(result, "returncode", None) != 1:
        return False
    stderr = (getattr(result, "stderr_tail", None) or "").lower()
    escaped = _escaped_attempted_id(attempted_session_id)
    if escaped is None:
        return False
    # The observed contract is one COMPLETE physical stderr line: the
    # signature starts the line, the attempted id terminates it (trailing
    # whitespace allowed). Only HORIZONTAL whitespace (space/tab) may pad
    # the signature/id — the old ``\s*`` gap also consumed LF/CRLF and
    # accepted split-line forms (``... session ID:\n<id>``, CRLF, and
    # whitespace-indented variants) as eviction (TASK-6024). ``^``/``$``
    # with MULTILINE anchor each line so a punctuation-led or alphanumeric
    # suffix (the old ``\b`` word boundary accepted punctuation-led
    # suffixes), same-line prefix text, and signature/id split across
    # lines can never match.
    bound = re.search(
        rf"^[ \t]*{_CLAUDE_EVICTION_SIGNATURE}[ \t]*{escaped}[ \t]*$",
        stderr,
        re.MULTILINE,
    ) is not None
    if not bound:
        return False
    # A marker embedded in auth/quota/transport output is not eviction.
    return not _contains_auth_quota_transport_token(stderr, attempted_session_id)


def _is_codex_session_evicted(result, attempted_session_id: str) -> bool:
    """codex-cli 0.148.0 proven contract: rc=1, stderr exactly
    ``Error: thread/resume: thread/resume failed: no rollout found for
    thread id <attempted-id> (code -32600)`` — the OBSERVED COMPLETE
    physical stderr line (CLI envelope + signature + JSON-RPC code). The
    signature starts the line, the regex-escaped attempted id is bound, the
    JSON-RPC code ``(code -32600)`` immediately follows it, and the line
    ends there (horizontal whitespace [ \t] only where the observed contract
    permits spacing — ``\\s`` never consumes LF/CRLF). Arbitrary same-line
    prefix/suffix text, split LF/CRLF/indented forms, bare signatures
    without the observed envelope, wrong/missing/prefix/suffix id, stdout,
    wrong rc, and a marker embedded in auth/quota/transport output never
    match."""
    if getattr(result, "returncode", None) != 1:
        return False
    stderr = (getattr(result, "stderr_tail", None) or "").lower()
    escaped = _escaped_attempted_id(attempted_session_id)
    if escaped is None:
        return False
    bound = re.search(
        rf"^[ \t]*{_CODEX_EVICTION_SIGNATURE}[ \t]+{escaped}[ \t]+"
        rf"{re.escape(_CODEX_EVICTION_JSON_RPC_CODE)}[ \t]*$",
        stderr,
        re.MULTILINE,
    ) is not None
    if not bound:
        return False
    return not _contains_auth_quota_transport_token(stderr, attempted_session_id)


def _is_pi_session_evicted(result, attempted_session_id: str) -> bool:
    """pi 0.84.2 proven contract: rc=1, stderr exactly
    ``No session found matching '<attempted-id>'`` — the OBSERVED COMPLETE
    physical stderr line (quoted id). The signature starts the line and the
    quoted regex-escaped attempted id terminates it (horizontal whitespace
    [ \t] only where the observed contract permits spacing — ``\\s`` never
    consumes LF/CRLF). Arbitrary same-line prefix/suffix text, split
    LF/CRLF/indented forms, wrong/missing/prefix/suffix id, stdout, wrong
    rc, and a marker embedded in auth/quota/transport output never match."""
    if getattr(result, "returncode", None) != 1:
        return False
    stderr = (getattr(result, "stderr_tail", None) or "").lower()
    escaped = _escaped_attempted_id(attempted_session_id)
    if escaped is None:
        return False
    bound = re.search(
        rf"^[ \t]*{_PI_EVICTION_SIGNATURE}[ \t]+'{escaped}'[ \t]*$",
        stderr,
        re.MULTILINE,
    ) is not None
    if not bound:
        return False
    return not _contains_auth_quota_transport_token(stderr, attempted_session_id)


# ANSI SGR escape sequences are stripped from stderr before the opencode
# signature match: the installed 1.18.25 CLI emits color codes around its
# error line even when stderr is a pipe (observed raw:
# ``\x1b[91m\x1b[1mError: \x1b[0mSession not found\n``).
_ANSI_SGR_RE = re.compile(r"\x1b\[[0-9;]*m")

# opencode 1.18.25 eviction contract: rc=1, stdout EMPTY, stderr exactly one
# physical line ``Error: Session not found`` (ANSI-stripped). Unlike
# claude/codex/pi the attempted session id is NOT echoed in the message
# (verified live: four bogus ids plus a really-deleted session all produce the
# identical line), so classification is complete-line rather than ID-anchored.
_OPENCODE_EVICTION_SIGNATURE = "error: session not found"


def _is_opencode_session_evicted(result, attempted_session_id: str) -> bool:
    """opencode 1.18.25 proven contract (TASK-6080 live audit, no API
    traffic beyond minimal free-model probes): rc=1, stdout EMPTY, stderr
    exactly one physical line ``Error: Session not found`` after ANSI-SGR
    stripping. The attempted id is NOT echoed by opencode, so the signature is
    the complete line — an exact per-line match with only horizontal
    whitespace permitted; LF/CRLF split forms, same-line prefix/suffix text,
    unrelated stderr lines (``Failed to change directory ...``, usage text),
    stdout JSON error events (invalid-model/unknown-server errors), wrong rc,
    cross-provider signatures, and empty attempted id never match. stdout must
    be EMPTY because a run that performed any work would have emitted NDJSON
    step events; a genuine eviction fails at session lookup before any model
    step. ``-s ""`` silently starts fresh (rc=0) — never classified, and the
    runner only ever wires a stored non-empty id. A global auth/quota/transport
    token veto is deliberately NOT applied: the complete-line anchor makes
    embedding impossible, and an unrelated warning line containing a token
    would falsely veto a genuine eviction."""
    if not attempted_session_id:
        return False
    if getattr(result, "returncode", None) != 1:
        return False
    if (getattr(result, "stdout_tail", None) or "").strip():
        return False
    stderr = _ANSI_SGR_RE.sub(
        "", getattr(result, "stderr_tail", None) or ""
    )
    # ``\r`` removed so a CRLF physical line still matches ``$`` exactly.
    stderr = stderr.replace("\r", "").lower()
    return re.search(
        rf"^[ \t]*{_OPENCODE_EVICTION_SIGNATURE}[ \t]*$",
        stderr,
        re.MULTILINE,
    ) is not None


def _classify_session_evicted(
    executor_name: str, result, attempted_session_id: str | None,
) -> bool:
    """Provider-declared session-not-found classification for the executor
    that ran, bound to the exact attempted session id. Only the proven
    executor/rc/stderr/signature contract may trigger the transactional
    invalidation + one fresh full-transcript retry. Wrong executor, wrong
    rc, stdout-only text, wrong/missing id, prefix/suffix near-matches,
    generic legacy substrings, a marker embedded in auth/quota/transport
    output, malformed output, and ambiguous failures all return False — the
    invocation is a plain failure and no resume state is invalidated."""
    if executor_name == "codex":
        return _is_codex_session_evicted(result, attempted_session_id)
    if executor_name == "pi":
        return _is_pi_session_evicted(result, attempted_session_id)
    if executor_name == "claude":
        return _is_claude_session_evicted(result, attempted_session_id)
    if executor_name == "opencode":
        return _is_opencode_session_evicted(result, attempted_session_id)
    return False


# Executors whose provider-session resume contract is PROVEN against the
# installed CLI (TASK-5977 audit): claude (2.1.241, production since THR-200),
# codex (0.148.0: `exec resume <thread_id> --json -`, same thread_id
# re-emitted), pi (0.84.2: `-p --session <id> --mode json`, same session.id
# re-emitted), opencode (1.18.25, TASK-6080 audit: `run -s <id> --dir <ws>
# --format json` with the prompt on stdin, same sessionID re-emitted; resume
# REQUIRES the identical project directory). Every other profile (generic-CLI,
# custom-adapter) stays fresh (full prompt every turn).
_RESUME_CAPABLE_EXECUTORS = frozenset({"claude", "codex", "pi", "opencode"})


def _delta_range_is_complete(
    messages: list[ThreadMessage],
    *,
    last_seq: int,
    max_seq: int,
) -> bool:
    """Strict no-message-omission proof for a resumed delta prompt.

    A delta ships every message with ``seq > last_seq`` (the durable delivery
    watermark), so it is authorized ONLY when the loaded canonical transcript
    proves the ENTIRE required range ``(last_seq, max_seq]`` is present and
    contiguous: the load must reach the authoritative transcript max (no
    truncation — the caller must load uncapped) and every internal sequence
    must exist (no holes). A null/zero/negative (<= 0) watermark — an
    ineligible delivered frontier — always fails closed, as do equal/ahead
    watermarks (empty required range) and any missing or truncated sequence:
    every one of those ships the complete canonical full-transcript fresh
    prompt.
    """
    if last_seq <= 0:
        # Null/zero/negative watermark: a stored provider id is INELIGIBLE
        # for delta resume (TASK-6007 HIGH 3) — always the complete canonical
        # full transcript, never a delta against the stored id.
        return False
    if max_seq <= last_seq:
        # Equal or ahead watermark — nothing new to ship as a delta.
        return False
    if not messages or messages[-1].seq != max_seq:
        # Truncated load: the required range cannot be proven complete.
        return False
    post = [m.seq for m in messages if m.seq > last_seq]
    return post == list(range(last_seq + 1, max_seq + 1))


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


def _settle_or_fail_reply(
    org_state,
    *,
    invocation_token: str,
    claim,
    status: ThreadInvocationStatus,
    decline_reason: str,
) -> bool:
    """Central settlement seam for a conversational REPLY terminal path.

    A claimed REPLY routes through ``settle_conversational_reply`` (which
    advances acknowledgement conditionally and never hot-loops); a non-REPLY
    invocation (or a REPLY whose claim was not taken) keeps the legacy
    ``fail_invocation`` transition. Maps DECLINED→decline, TIMEOUT→timeout,
    everything else→failed.
    """
    if claim is not None:
        if status is ThreadInvocationStatus.TIMEOUT:
            outcome = "timeout"
        elif status is ThreadInvocationStatus.DECLINED:
            outcome = "decline"
        else:
            outcome = "failed"
        return org_state.db.settle_conversational_reply(
            token=invocation_token,
            outcome=outcome,
            decline_reason=decline_reason,
        ) is not None
    return org_state.db.fail_invocation(
        invocation_token, status=status, decline_reason=decline_reason,
    )


async def run_invocation(
    *,
    org_state,
    invocation_token: str,
    settings: Settings,
    host_supervisor: HostSessionSupervisor | None = None,
) -> None:
    """Execute one thread invocation end-to-end.

    Reads the pending row, builds the prompt, spawns the executor subprocess,
    and records auto-decline rows on no-callback / timeout / failure.

    THR-207 supervised wiring: when ``host_supervisor`` is the daemon-wide
    ``HostSessionSupervisor`` the invocation runs through it (admission lease,
    atomic ownership at grant, real backend launch into containment, opaque
    cancellation, containment cleanup before exactly-once lease release); when
    it is ``None`` (tests / idle state) the legacy uncontained path is used
    unchanged.
    """
    inv = org_state.db.get_pending_invocation(invocation_token)
    if inv is None:
        logger.info("run_invocation: token %s already non-pending", invocation_token[:8])
        return

    # GitHub #688 Slice B: a conversational REPLY must pass the durable
    # queued→running CAS before any prompt/subprocess work. A stale/duplicate
    # queue notification no-ops here. BOOTSTRAP/TASK_FOLLOWUP keep the legacy
    # direct path (no delivery-state row).
    claim: "ThreadReplyClaim | None" = None
    if inv.purpose is ThreadInvocationPurpose.REPLY:
        claim = org_state.db.claim_conversational_reply(invocation_token)
        if claim is None:
            logger.info(
                "run_invocation: token %s stale/duplicate REPLY (claim CAS miss)",
                invocation_token[:8],
            )
            return

    thread = org_state.db.get_thread(inv.thread_id)
    if thread is None:
        _settle_or_fail_reply(
            org_state, invocation_token=invocation_token, claim=claim,
            status=ThreadInvocationStatus.FAILED,
            decline_reason="thread_missing",
        )
        return

    participants = org_state.db.list_thread_participants(inv.thread_id)
    messages = org_state.db.list_thread_messages(inv.thread_id, limit=None)

    workspace = org_state.root / "workspaces" / inv.agent_name

    # Build OrgPaths for executor resolution + allow rules.
    try:
        from runtime.orchestrator._paths import OrgPaths
        paths = OrgPaths(root=org_state.root)
    except Exception:
        paths = None

    # THR-095: read executor from org/agents/<name>.md (single source of truth).
    # FAIL-CLOSED: terminated or missing agents must never fall back to
    # ``claude`` and must never reach executor construction.
    try:
        from runtime.orchestrator.prompt_loader import is_terminated, load_agent
        agent_def = load_agent(paths, inv.agent_name) if paths else None
    except Exception:
        agent_def = None

    if agent_def is None:
        reason = "agent_unavailable"
        if paths and is_terminated(paths, inv.agent_name):
            reason = "agent_terminated"
        _settle_or_fail_reply(
            org_state, invocation_token=invocation_token, claim=claim,
            status=ThreadInvocationStatus.DECLINED,
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
        return

    executor_name = agent_def.executor.lower()
    if not _is_registered_executor(executor_name):
        executor_name = "claude"

    # Issue #568: forward AgentDef.model to executor.run for thread invocations.
    model_name: str | None = agent_def.model

    executor = _build_executor_for_provider(executor_name, settings, paths)

    # ── D7B: CustomAdapterExecutor invocation context ──────────────────
    if hasattr(executor, 'set_invocation_context'):
        executor.set_invocation_context(
            agent=inv.agent_name,
            org=org_state.slug,
            invocation_kind="thread",
            task_id=None,
        )

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

    # Resolve agent team before the unified materialization call.
    try:
        agent_team = "engineering"
        for p in participants:
            if p.agent_name == inv.agent_name:
                agent_team = p.team
                break
    except Exception:
        agent_team = "engineering"

    # Issue #536: serialize the complete pre-spawn skill materialization
    # transaction under a process-local workspace lock so concurrent
    # task/thread/wake/dream/schedule callers targeting the same workspace
    # cannot race on the predictable .tmp.<name> cleanup/write/replace
    # window in _copy_skills_tree.
    #
    # This single call replaces the previous three separate calls:
    #   refresh_session_skills (wholesale when enabled)
    #   ensure_system_contracts_materialized (inject + verify)
    #   inject_managed_skills (managed-catalog + lifecycle)
    # FAIL-CLOSED: a materialization error must persist a terminal failure
    # and return BEFORE executor spawn — no half-populated skills dir may
    # pass as complete (REVISE TASK-2829).
    session_id = f"sess-{uuid.uuid4().hex}"
    try:
        skills_root = settings.project_root / "runtime" / "skills"
        expected_specs = materialize_workspace_skills(
            workspace, settings,
            slug=org_state.slug,
            context="thread",
            provider=executor_name,
            agent_name=inv.agent_name,
            team=agent_team,
            skills_root=skills_root,
            org_root=org_state.root,
            db=org_state.db,
            session_id=session_id,
        )

        # ── Pre-launch integrity validation ─────────────────────
        validate_workspace_skills_integrity(
            workspace, expected_specs,
            settings=settings,
            db=org_state.db,
            agent_name=inv.agent_name,
            task_id=inv.thread_id,
        )
    except (SystemContractMaterializationError, Exception) as e:
        decline_reason = str(e)
        if not isinstance(e, SystemContractMaterializationError):
            decline_reason = f"materialization_failed: {e}"
        _settle_or_fail_reply(
            org_state, invocation_token=invocation_token, claim=claim,
            status=ThreadInvocationStatus.FAILED,
            decline_reason=decline_reason,
        )
        return

    # THR-103: fast-forward-refresh every cloned repo so the agent has
    # fresh code regardless of executor (claude/codex/opencode/pi).
    # Must run BEFORE the executor subprocess starts. Failure is non-
    # blocking: offline / dirty / non-ff / timeout are swallowed.
    repo_refresh_results = refresh_workspace_repos(workspace)

    repo_refresh_note = format_repo_refresh_note(repo_refresh_results)
    # Protocol doc manifest — bundled-path one-liner per doc (THR-070).
    try:
        protocol_doc_manifest = resolve_protocol_doc_manifest(settings=settings)
    except Exception:
        protocol_doc_manifest = ""
    protocol_doc_manifest = "\n".join(filter(None, (
        protocol_doc_manifest,
        repo_refresh_note,
    )))

    # THR-095 F2: resolve threads settings from DB (override) → dataclass defaults.
    threads_cfg = resolve_org_setting_threads(org_state.db, code_default=OrgConfig())
    timeout: int = settings.session_timeout_seconds
    if threads_cfg["invocation_timeout_seconds"] is not None:
        timeout = threads_cfg["invocation_timeout_seconds"]

    # --- Active-invocation lock (provider-agnostic, THR-042) ---
    # Every executor must acquire the per-(org, thread, agent) lock so no two
    # subprocess sessions for the same agent in the same thread run concurrently.
    # Only resume-capable executors (claude/codex/pi) support --resume/--session
    # and manage thread_session state; the lock protects all providers against
    # concurrent runs, not just Claude.
    resume_capable = executor_name in _RESUME_CAPABLE_EXECUTORS
    invocation_guard = _invocation_lock(org_state, inv.thread_id, inv.agent_name)
    async with invocation_guard:
        stored_sid, last_seq = (
            org_state.db.get_thread_session(inv.thread_id, inv.agent_name)
            if resume_capable else (None, 0)
        )
        # Authoritative upper bound of the required post-watermark range,
        # queried independently so the completeness proof never trusts the
        # loaded list's own extent.
        max_seq = org_state.db.get_thread_max_message_seq(inv.thread_id)
        resume_sid: str | None = None
        # GitHub #688 Slice B: a claimed conversational REPLY must explicitly
        # state its inclusive delivery range so the agent knows exactly which
        # messages this wake covers (a coalesced wake batches several). The
        # transcript renders them in order; the note makes the range explicit
        # and forbids skipping. Session resume below is allowed only as an
        # optimization that cannot omit any sequence in this range.
        range_note = ""
        if claim is not None:
            range_note = (
                f"\n## Delivery range (GH-688 Phase 1)\n"
                f"You are asked to respond to messages "
                f"{claim.running_from_seq} through {claim.running_through_seq} "
                f"(inclusive), in order. Consider every message in that range; "
                f"do not skip any of them.\n"
            )
        # Strict no-message-omission (TASK-5989) + resume eligibility
        # (TASK-6007 HIGH 3): a resumed delta is authorized ONLY when the
        # durable watermark is a strictly positive delivered frontier AND the
        # ENTIRE required post-watermark range is proven present and
        # contiguous in the canonical transcript (loaded uncapped above; see
        # _delta_range_is_complete). A stored id whose watermark is null/
        # zero/negative (<= 0) is INELIGIBLE — the runner must make a fresh
        # invocation with the complete canonical transcript. For a claimed
        # REPLY the session watermark must stay strictly below the claim's
        # running_from_seq AND the claim's inclusive end must exist in the
        # transcript; otherwise ``last_resumed_seq`` would silently control
        # (and drop) required delivery. Truncated loads, internal holes, and
        # equal/ahead/null watermarks all fail closed to the full fresh
        # prompt. Non-REPLY invocations keep the legacy resume behavior but
        # still require the completeness proof.
        delta_complete = _delta_range_is_complete(
            messages, last_seq=last_seq, max_seq=max_seq,
        )
        can_resume = (
            resume_capable
            and stored_sid
            and last_seq > 0
            and (claim is None or (
                last_seq < claim.running_from_seq
                and claim.running_through_seq <= max_seq
            ))
            and delta_complete
        )
        if can_resume:
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
        prompt += range_note

        org_state.db.stamp_invocation_started(invocation_token, session_id=session_id)
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

        def _invoke(run_prompt: str, resume: str | None) -> _InvokeResult:
            def _pre_launch_validator():
                validate_workspace_skills_integrity(
                    workspace, expected_specs,
                    settings=settings,
                    db=org_state.db,
                    agent_name=inv.agent_name,
                    task_id=inv.thread_id,
                )
            if host_supervisor is None:
                # ── Legacy uncontained path (unchanged) ──
                run_kwargs = dict(
                    workspace=Path(workspace), prompt=run_prompt,
                    session_id=session_id, timeout_seconds=timeout,
                    on_throttle_event=_on_throttle_event,
                    pre_launch_validator=_pre_launch_validator,
                    org_slug=org_state.slug,
                    model=model_name,
                )
                if resume:
                    run_kwargs["resume_session_id"] = resume
                return _InvokeResult(result=executor.run(**run_kwargs))
            # ── THR-207 supervised wiring: the invocation phase runs through
            # the daemon-wide HostSessionSupervisor (admission lease, atomic
            # ownership at grant, real backend launch into containment, opaque
            # cancellation, containment cleanup before exactly-once lease
            # release). The executor and its per-provider throttle stay inside
            # the launch body unchanged.
            spec_builder = getattr(executor, "build_launch_spec", None)
            if spec_builder is None:
                # Fail closed: no contained-launch seam — mirror the task
                # producer's fail-closed behavior.
                return _InvokeResult(
                    result=None,
                    terminal_reason=TerminalReason.FAILURE,
                    error=(
                        f"executor {type(executor).__name__!r} does not support "
                        "contained launch (build_launch_spec missing)"
                    ),
                )
            try:
                launch_spec = spec_builder(
                    workspace=Path(workspace), prompt=run_prompt,
                    session_id=session_id, model=model_name,
                    resume_session_id=resume, org_slug=org_state.slug,
                    timeout_seconds=timeout,
                )
            except Exception as exc:
                return _InvokeResult(
                    result=None, terminal_reason=TerminalReason.FAILURE,
                    error=f"launch_spec_failed: {exc}",
                )

            def _launch_body(running) -> LaunchResult:
                # Real backend: the subprocess is already launched into
                # containment — the executor communicates + parses only. Honest
                # passthrough: no containment capability — the executor
                # self-launches exactly as the legacy path, with the throttle's
                # internal 429 retry disabled so the supervisor owns the
                # finish/release/sleep/reacquire lifecycle.
                contained = running.process is not None
                run_kwargs = dict(
                    workspace=Path(workspace), prompt=run_prompt,
                    session_id=session_id, timeout_seconds=timeout,
                    on_throttle_event=_on_throttle_event,
                    pre_launch_validator=_pre_launch_validator if not contained else None,
                    org_slug=org_state.slug,
                    model=model_name,
                    running=running if contained else None,
                    throttle_backoff_seconds=() if not contained else None,
                )
                if resume:
                    run_kwargs["resume_session_id"] = resume
                res = executor.run(**run_kwargs)
                return LaunchResult(
                    success=res.success,
                    duration_seconds=float(getattr(res, "duration_seconds", 0) or 0),
                    returncode=getattr(res, "returncode", None),
                    error=getattr(res, "error", None),
                    rate_limited=bool(getattr(res, "rate_limited", False)),
                    timed_out=(
                        "timeout" in str(getattr(res, "error", "") or "").lower()
                    ),
                    payload=res,
                )

            outcome = host_supervisor.run(
                AdmissionRequest(
                    org=org_state.slug,
                    invocation_kind="thread",
                    logical_id=inv.thread_id,
                    executor_profile=executor_name,
                    enqueued_at=time.monotonic(),
                ),
                launch_spec=launch_spec,
                launch_body=_launch_body,
                pre_launch_validator=_pre_launch_validator,
            )
            launch = outcome.payload
            if launch is None:
                return _InvokeResult(
                    result=None, terminal_reason=outcome.terminal_reason,
                    error=outcome.error,
                )
            return _InvokeResult(
                result=launch.payload, terminal_reason=outcome.terminal_reason,
                error=outcome.error,
            )

        async def _settle_interrupted(phase: _InvokeResult) -> None:
            """Daemon drain/cancellation interrupted the invocation before it
            could settle its row: persist any token usage the attempt produced,
            and leave a still-pending row for daemon-restart recovery (REPLY
            delivery-state replacement; BOOTSTRAP/TASK_FOLLOWUP daemon_restart
            reap) — the pre-wiring shutdown semantics when a worker was
            cancelled mid-run. A row already settled by a real terminal
            callback (the callback landed before the drain) needs only its
            settled event."""
            if phase.result is not None:
                _persist_thread_token_usage(
                    org_state, inv=inv, result=phase.result,
                    executor_name=executor_name,
                    invocation_token=invocation_token,
                )
            after = org_state.db.get_invocation_any_status(invocation_token)
            if after is None:
                return
            if after.status in {ThreadInvocationStatus.CONSUMED, ThreadInvocationStatus.DECLINED}:
                if after.status is ThreadInvocationStatus.DECLINED:
                    await _publish_invocation_event(
                        org_state, thread_id=inv.thread_id,
                        agent_name=inv.agent_name, seq=inv.triggering_seq,
                        kind="invocation_settled", status="declined",
                    )
                return
            logger.info(
                "run_invocation: token %s interrupted by %s — leaving pending "
                "for daemon-restart recovery",
                invocation_token[:8],
                phase.terminal_reason.value if phase.terminal_reason else "?",
            )

        async def _settle_no_launch(phase: _InvokeResult) -> None:
            """The supervisor refused or aborted launch before any subprocess
            ran (pre-launch validator failure / prepare or spawn failure / no
            contained-launch seam): settle the row FAILED with the durable
            first-wins reason — mirroring the legacy runner_crash /
            materialization-failure handling."""
            reason = (
                f"session {phase.terminal_reason.value} before launch"
                if phase.terminal_reason else "session before launch"
            )
            if phase.error:
                reason = f"{reason}: {phase.error}"
            _settle_or_fail_reply(
                org_state, invocation_token=invocation_token, claim=claim,
                status=ThreadInvocationStatus.FAILED,
                decline_reason=reason,
            )
            audit.log_thread_invocation_failed(
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

        # Spawn subprocess in a thread pool (executors are synchronous).
        fallback_executed = False  # tracks session-not-found eviction fallback
        try:
            loop = asyncio.get_event_loop()
            phase = await loop.run_in_executor(None, lambda: _invoke(prompt, resume_sid))
            if phase.terminal_reason in _INTERRUPTED_TERMINALS:
                await _settle_interrupted(phase)
                return
            result = phase.result
            if result is None:
                await _settle_no_launch(phase)
                return

            if (resume_capable and resume_sid and not result.success
                    and _classify_session_evicted(executor_name, result, resume_sid)):
                # THR-200: the eviction audit AND the durable session-id
                # invalidation commit in ONE transaction (see
                # AuditLogger.log_agent_session_evicted_fallback /
                # Database.invalidate_thread_session_evicted) BEFORE the
                # full-prompt fallback launch. If the fallback also fails,
                # the id remains NULL and the delivery watermark is
                # untouched — the next wake re-attempts the same range from
                # a full prompt instead of a doomed resume against a stale
                # provider session.
                audit.log_agent_session_evicted_fallback(
                    inv.thread_id, agent_name=inv.agent_name, executor=executor_name,
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
                full_prompt += range_note
                shown_seqs = [m.seq for m in messages]
                resume_sid = None
                fallback_executed = True
                phase = await loop.run_in_executor(
                    None, lambda: _invoke(full_prompt, None),
                )
                if phase.terminal_reason in _INTERRUPTED_TERMINALS:
                    await _settle_interrupted(phase)
                    return
                if phase.result is None:
                    await _settle_no_launch(phase)
                    return
                result = phase.result
        except Exception as exc:
            _settle_or_fail_reply(
                org_state, invocation_token=invocation_token, claim=claim,
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
            if resume_capable and result.success and getattr(result, "agent_session_id", None):
                new_watermark = max(shown_seqs) if shown_seqs else last_seq
                new_watermark = max(new_watermark, last_seq)
                org_state.db.update_thread_session(
                    inv.thread_id, inv.agent_name,
                    agent_session_id=result.agent_session_id,
                    last_resumed_seq=new_watermark,
                )
                if resume_sid:
                    audit.log_agent_session_reused(
                        inv.thread_id, agent_name=inv.agent_name, executor=executor_name,
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
        if resume_capable and result.success and getattr(result, "agent_session_id", None):
            new_watermark = max(shown_seqs) if shown_seqs else last_seq
            new_watermark = max(new_watermark, last_seq)
            org_state.db.update_thread_session(
                inv.thread_id, inv.agent_name,
                agent_session_id=result.agent_session_id,
                last_resumed_seq=new_watermark,
            )
            if resume_sid:
                audit.log_agent_session_reused(
                    inv.thread_id, agent_name=inv.agent_name, executor=executor_name,
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

            if resume_capable and getattr(result, "agent_session_id", None):
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
                    + range_note
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
            retry_phase: _InvokeResult | None = None
            try:
                retry_phase = await loop.run_in_executor(
                    None, lambda: _invoke(retry_prompt, retry_resume_sid),
                )
            except Exception as exc:
                logger.warning(
                    "run_invocation: token %s nudge re-invoke crashed: %s",
                    invocation_token[:8], exc,
                )
                retry_result = None
                retry_exc = exc
            else:
                if retry_phase.terminal_reason in _INTERRUPTED_TERMINALS:
                    await _settle_interrupted(retry_phase)
                    return
                retry_result = retry_phase.result
                if retry_result is None:
                    await _settle_no_launch(retry_phase)
                    return

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
                if (resume_capable and retry_result is not None and retry_result.success
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
                            executor=executor_name,
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
                if (resume_capable and getattr(retry_result, "agent_session_id", None)):
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

            _settle_or_fail_reply(
                org_state, invocation_token=invocation_token, claim=claim,
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

        _settle_or_fail_reply(
            org_state, invocation_token=invocation_token, claim=claim,
            status=status, decline_reason=reason,
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
