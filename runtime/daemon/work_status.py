"""Derived work-status summary for the task-detail surface (TASK-5522).

A read-only, honest summary of *observable* work activity for a task: the
current-session start, the last heartbeat with an explicit freshness label,
and the latest agent-written ``progress`` receipt scoped to the current
session. It never fabricates activity: progress receipts come only from the
``progress`` audit action the agent itself emits, and staleness is a pure
clock computation over stored timestamps.

Design constraints (founder-authorized, TASK-5522):
- No schema change, no synthetic audit rows, no background monitor. Only the
  existing ``tasks.last_heartbeat`` column and the ``session_start`` /
  ``progress`` audit rows are consumed.
- Privacy: never expose chain of thought, command stdout, workspace paths,
  session ids, or arbitrary audit payloads — only the agent-written milestone
  message and system timestamps.
- The heartbeat freshness threshold REUSES the zombie-reaper semantics
  (2 missed 30s heartbeats = 60s) instead of inventing a monitor/reaper
  threshold. The progress staleness threshold is a display/derivation policy
  (5 minutes) that says nothing about reaping — it only labels what an
  observer sees.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from runtime.daemon.queue import HEARTBEAT_INTERVAL_SECONDS
from runtime.models import TaskRecord, TaskStatus

# ---------------------------------------------------------------------------
# Display/derivation policy constants
# ---------------------------------------------------------------------------

# Substantive-receipt staleness policy: a live session whose latest
# current-session progress receipt (or whose very start, when no receipt has
# been emitted yet) is at least this old is shown as stale-but-alive. Pure
# display/derivation — it does NOT reap, cancel, or otherwise act on tasks.
STALE_PROGRESS_AFTER_SECONDS = 300  # 5 minutes

# Heartbeat freshness semantics — REUSED from the zombie reaper's definition
# of "definitely stale" (2 missed heartbeat intervals). Imported from the
# same cadence constant so the display can never drift from the runtime
# heartbeat interval. See runtime/daemon/zombie_reaper.py
# (STALE_HEARTBEAT_SECONDS = 2 * HEARTBEAT_INTERVAL_SECONDS).
HEARTBEAT_STALE_AFTER_SECONDS = 2 * HEARTBEAT_INTERVAL_SECONDS  # 60s

# ---------------------------------------------------------------------------
# Summary states (machine-readable ``state`` + human ``label``)
# ---------------------------------------------------------------------------
#
# States (a)-(d) apply ONLY to the live-task shape (in_progress, no
# block_kind) with a FRESH observed heartbeat; outside that shape the summary
# is explicitly non-applicable, and a stale/absent heartbeat is its own honest
# state rather than being papered over with a progress label.

STATE_NEWLY_STARTED = "newly_started"          # (a)
STATE_RECENT_PROGRESS = "recent_progress"      # (b)
STATE_STALE_NO_RECEIPT = "stale_no_receipt"    # (c)
STATE_STALE_OLD_RECEIPT = "stale_old_receipt"  # (d)
STATE_HEARTBEAT_STALE = "heartbeat_stale"
STATE_HEARTBEAT_UNAVAILABLE = "heartbeat_unavailable"
STATE_UNAVAILABLE = "unavailable"
STATE_NOT_APPLICABLE = "not_applicable"

# Labels say what is OBSERVED. They never claim execution progress from a
# heartbeat, and they never claim a receipt exists when only liveness does.
_LABELS = {
    STATE_NEWLY_STARTED: "Newly started — awaiting first update",
    STATE_RECENT_PROGRESS: "Recent update recorded",
    STATE_STALE_NO_RECEIPT: "Stale-but-alive — no substantive update recorded",
    STATE_STALE_OLD_RECEIPT: "Stale-but-alive — last update older than 5 minutes",
    STATE_HEARTBEAT_STALE: "Heartbeat stale — liveness not observed recently",
    STATE_HEARTBEAT_UNAVAILABLE: "No heartbeat observed",
    STATE_UNAVAILABLE: "Work status unavailable",
    STATE_NOT_APPLICABLE: "Not applicable",
}

# reason values for not_applicable / unavailable
_REASON_TERMINAL = "terminal"
_REASON_PENDING = "pending"
_REASON_ESCALATED = "escalated"
_REASON_BLOCKED = "blocked"
_REASON_NO_SESSION_START = "no_session_start"
_REASON_UNASSIGNED = "unassigned"


def _parse_ts(value: Any) -> datetime | None:
    """Parse a stored ISO timestamp; None when absent or malformed.

    DB rows stamp UTC; ``Z`` suffixes are normalized. A malformed value is
    treated as unusable (the caller decides whether that makes the summary
    unavailable or just skips the row).
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _audit_entries_by_action(audit_log: list[dict], action: str) -> list[dict]:
    return [e for e in audit_log if e.get("action") == action]


def _latest_for_agent(entries: list[dict], agent: str | None) -> dict | None:
    """Return the latest entry (audit id order) whose agent matches.

    ``agent`` None never matches — sessions are scoped to a concrete agent.
    """
    if agent is None:
        return None
    for entry in reversed(entries):  # get_audit_logs orders by id ASC
        if entry.get("agent") == agent:
            return entry
    return None


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _progress_payload_message(payload: Any) -> str | None:
    """Extract the agent-written milestone message from a progress payload.

    Malformed/absent payloads yield None (content unavailable) — the receipt
    timestamp still counts as an observed progress event, but no content is
    fabricated.
    """
    if not isinstance(payload, dict):
        return None
    msg = payload.get("message")
    if not isinstance(msg, str) or not msg.strip():
        return None
    return msg.strip()


def _heartbeat_observation(
    task: TaskRecord, now: datetime,
) -> tuple[str, dict]:
    """Classify the heartbeat observation for the live-task shape.

    Returns (freshness, heartbeat-dict). Freshness is one of
    ``fresh``/``stale``/``unavailable`` using the existing 60-second heartbeat
    freshness semantics (2 missed 30s intervals).
    """
    hb_raw = task.last_heartbeat
    if hb_raw is None:
        return "unavailable", {"timestamp": None, "freshness": "unavailable"}
    # TaskRecord parses last_heartbeat into a datetime; tolerate a raw ISO
    # string (defensive — historic/malformed rows) via the shared parser.
    hb = hb_raw if isinstance(hb_raw, datetime) else _parse_ts(hb_raw)
    if hb is None:
        return "unavailable", {"timestamp": None, "freshness": "unavailable"}
    age = (now - hb).total_seconds()
    freshness = "fresh" if age < HEARTBEAT_STALE_AFTER_SECONDS else "stale"
    return freshness, {"timestamp": _iso(hb), "freshness": freshness}


def derive_work_status(
    task: TaskRecord,
    audit_log: list[dict],
    *,
    now: datetime | None = None,
) -> dict:
    """Derive the read-only work-status summary for the task-detail envelope.

    ``audit_log`` is the same chronological list ``GET /tasks/{task_id}``
    already returns (``get_audit_logs`` ordering by id ASC). ``now`` is an
    explicit clock input for deterministic tests; production callers omit it.

    The summary is a plain JSON-ready dict:
    ``applicable``, ``state``, ``label``, ``reason``, ``session_start_ts``,
    ``heartbeat`` {timestamp, freshness}, ``latest_progress``
    {timestamp, message, agent} | None.
    """
    now = now or datetime.now(timezone.utc)

    # ── Non-applicable gates ────────────────────────────────────────────────
    # Terminal, pending, escalated, and in_progress parked-on-block tasks have
    # no live agent session to summarize. Explicit non-applicable — never
    # imply a live agent.
    if task.status != TaskStatus.IN_PROGRESS:
        reason = (
            _REASON_TERMINAL
            if task.status
            in (TaskStatus.COMPLETED, TaskStatus.FAILED,
                TaskStatus.CANCELLED, TaskStatus.SUPERSEDED)
            else _REASON_PENDING if task.status == TaskStatus.PENDING
            else _REASON_ESCALATED
        )
        return {
            "applicable": False,
            "state": STATE_NOT_APPLICABLE,
            "label": _LABELS[STATE_NOT_APPLICABLE],
            "reason": reason,
            "session_start_ts": None,
            "heartbeat": {"timestamp": None, "freshness": "unavailable"},
            "latest_progress": None,
        }
    if task.block_kind is not None:
        return {
            "applicable": False,
            "state": STATE_NOT_APPLICABLE,
            "label": _LABELS[STATE_NOT_APPLICABLE],
            "reason": _REASON_BLOCKED,
            "session_start_ts": None,
            "heartbeat": {"timestamp": None, "freshness": "unavailable"},
            "latest_progress": None,
        }

    # ── Live-task shape: heartbeat observation first ────────────────────────
    hb_freshness, hb = _heartbeat_observation(task, now)

    # Current-session lower boundary: the LATEST assigned-agent session_start.
    session_start_entry = _latest_for_agent(
        _audit_entries_by_action(audit_log, "session_start"),
        task.assigned_agent,
    )
    if session_start_entry is None:
        # A live shape with no scoped session start is anomalous/historic
        # data — we cannot honestly bound the current session, so the
        # receipt-derived summary is unavailable (not fabricated).
        return {
            "applicable": True,
            "state": STATE_UNAVAILABLE,
            "label": _LABELS[STATE_UNAVAILABLE],
            "reason": (
                _REASON_UNASSIGNED
                if task.assigned_agent is None
                else _REASON_NO_SESSION_START
            ),
            "session_start_ts": None,
            "heartbeat": hb,
            "latest_progress": None,
        }
    session_start_ts = _parse_ts(session_start_entry.get("timestamp"))
    if session_start_ts is None:
        return {
            "applicable": True,
            "state": STATE_UNAVAILABLE,
            "label": _LABELS[STATE_UNAVAILABLE],
            "reason": _REASON_NO_SESSION_START,
            "session_start_ts": None,
            "heartbeat": hb,
            "latest_progress": None,
        }

    # Latest current-session progress receipt: latest progress row by the
    # assigned agent AT OR AFTER the session-start boundary. A prior session's
    # receipt (before the boundary) must never satisfy the new session.
    progress_entries = _audit_entries_by_action(audit_log, "progress")
    latest_progress: dict | None = None
    for entry in reversed(progress_entries):
        if entry.get("agent") != task.assigned_agent:
            continue
        ts = _parse_ts(entry.get("timestamp"))
        if ts is None:
            continue  # cannot compare — skip, don't crash
        if ts < session_start_ts:
            continue  # prior session — out of scope
        latest_progress = {
            "timestamp": _iso(ts),
            "message": _progress_payload_message(entry.get("payload")),
            "agent": entry.get("agent") or task.assigned_agent,
        }
        break

    # ── State classification ────────────────────────────────────────────────
    # A stale/absent heartbeat is its own honest headline: liveness is not
    # observed recently, so no progress-freshness label is claimed.
    if hb_freshness == "stale":
        state = STATE_HEARTBEAT_STALE
    elif hb_freshness == "unavailable":
        state = STATE_HEARTBEAT_UNAVAILABLE
    elif latest_progress is None:
        session_age = (now - session_start_ts).total_seconds()
        state = (
            STATE_STALE_NO_RECEIPT
            if session_age >= STALE_PROGRESS_AFTER_SECONDS
            else STATE_NEWLY_STARTED
        )
    else:
        receipt_age = (now - _parse_ts(latest_progress["timestamp"])).total_seconds()
        state = (
            STATE_STALE_OLD_RECEIPT
            if receipt_age >= STALE_PROGRESS_AFTER_SECONDS
            else STATE_RECENT_PROGRESS
        )

    return {
        "applicable": True,
        "state": state,
        "label": _LABELS[state],
        "reason": None,
        "session_start_ts": _iso(session_start_ts),
        "heartbeat": hb,
        "latest_progress": latest_progress,
    }
