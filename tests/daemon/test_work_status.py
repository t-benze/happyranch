"""Deterministic tests for the derived work-status summary (TASK-5522).

All time is controlled via the explicit ``now`` clock input — no sleeps.
The audit_log fixtures mirror the chronological shape ``get_audit_logs``
returns (id ASC, ISO timestamps, JSON-decoded payloads).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from runtime.daemon.work_status import (
    STALE_PROGRESS_AFTER_SECONDS,
    derive_work_status,
)
from runtime.models import BlockKind, TaskRecord, TaskStatus

NOW = datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)


def _ts(dt: datetime) -> str:
    return dt.isoformat()


def _audit(entries: list[dict]) -> list[dict]:
    """Normalize a fixture list into id-ordered audit rows."""
    return [{"id": i, "task_id": "TASK-1", **e} for i, e in enumerate(entries)]


def _session_start(age_seconds: int, agent: str = "dev_agent") -> dict:
    return {
        "agent": agent,
        "action": "session_start",
        "payload": {"workspace": "/w"},
        "timestamp": _ts(NOW - timedelta(seconds=age_seconds)),
    }


def _progress(age_seconds: int, message: str = "Phase 3 of 6: tests passing", agent: str = "dev_agent") -> dict:
    return {
        "agent": agent,
        "action": "progress",
        "payload": {"message": message},
        "timestamp": _ts(NOW - timedelta(seconds=age_seconds)),
    }


def _task(**overrides) -> TaskRecord:
    base = dict(
        id="TASK-1",
        brief="b",
        status=TaskStatus.IN_PROGRESS,
        block_kind=None,
        assigned_agent="dev_agent",
        last_heartbeat=NOW - timedelta(seconds=30),  # fresh by default
    )
    base.update(overrides)
    return TaskRecord(**base)


# ── Non-applicable states ───────────────────────────────────────────────────


def test_terminal_task_is_not_applicable_terminal():
    for st in (TaskStatus.COMPLETED, TaskStatus.FAILED,
               TaskStatus.CANCELLED, TaskStatus.SUPERSEDED):
        s = derive_work_status(
            _task(status=st, last_heartbeat=None),
            _audit([_session_start(120), _progress(30)]),
            now=NOW,
        )
        assert s["applicable"] is False
        assert s["state"] == "not_applicable"
        assert s["reason"] == "terminal"
        # Never imply a live agent: no heartbeat/progress facts surface.
        assert s["heartbeat"]["timestamp"] is None
        assert s["latest_progress"] is None


def test_pending_task_is_not_applicable_pending():
    s = derive_work_status(
        _task(status=TaskStatus.PENDING, last_heartbeat=None),
        _audit([]),
        now=NOW,
    )
    assert s["state"] == "not_applicable"
    assert s["reason"] == "pending"


def test_escalated_task_is_not_applicable_escalated():
    s = derive_work_status(
        _task(status=TaskStatus.ESCALATED, last_heartbeat=None),
        _audit([]),
        now=NOW,
    )
    assert s["state"] == "not_applicable"
    assert s["reason"] == "escalated"


def test_in_progress_parked_on_block_is_not_applicable_blocked():
    for kind in (BlockKind.DELEGATED, BlockKind.BLOCKED_ON_JOB):
        s = derive_work_status(
            _task(block_kind=kind, last_heartbeat=None),
            _audit([_session_start(120)]),
            now=NOW,
        )
        assert s["state"] == "not_applicable"
        assert s["reason"] == "blocked"


# ── Live-task shape: (a) newly-started / (c) stale-no-receipt ───────────────


def test_newly_started_no_progress_under_5m():
    s = derive_work_status(
        _task(),
        _audit([_session_start(120)]),
        now=NOW,
    )
    assert s["applicable"] is True
    assert s["state"] == "newly_started"
    assert s["label"] == "Newly started — awaiting first update"
    assert s["heartbeat"]["freshness"] == "fresh"
    assert s["session_start_ts"] == _ts(NOW - timedelta(seconds=120))
    assert s["latest_progress"] is None


def test_no_receipt_boundary_at_exactly_5m_is_stale():
    # just under 5m → newly-started
    under = derive_work_status(
        _task(),
        _audit([_session_start(STALE_PROGRESS_AFTER_SECONDS - 1)]),
        now=NOW,
    )
    assert under["state"] == "newly_started"
    # exactly 5m → stale-but-alive (>= boundary)
    at = derive_work_status(
        _task(),
        _audit([_session_start(STALE_PROGRESS_AFTER_SECONDS)]),
        now=NOW,
    )
    assert at["state"] == "stale_no_receipt"
    assert at["label"] == "Stale-but-alive — no substantive update recorded"


def test_stale_no_receipt_at_or_after_5m():
    s = derive_work_status(
        _task(),
        _audit([_session_start(28 * 60)]),  # TASK-5521-shaped: ~28 min, no progress
        now=NOW,
    )
    assert s["state"] == "stale_no_receipt"
    assert s["heartbeat"]["freshness"] == "fresh"
    assert s["latest_progress"] is None


# ── Live-task shape: (b) recent / (d) stale-old receipt ─────────────────────


def test_recent_progress_under_5m():
    s = derive_work_status(
        _task(),
        _audit([_session_start(600), _progress(60, message="Milestone A")]),
        now=NOW,
    )
    assert s["state"] == "recent_progress"
    assert s["label"] == "Recent update recorded"
    assert s["latest_progress"] == {
        "timestamp": _ts(NOW - timedelta(seconds=60)),
        "message": "Milestone A",
        "agent": "dev_agent",
    }


def test_receipt_boundary_at_exactly_5m_is_stale():
    under = derive_work_status(
        _task(),
        _audit([_session_start(600), _progress(STALE_PROGRESS_AFTER_SECONDS - 1)]),
        now=NOW,
    )
    assert under["state"] == "recent_progress"
    at = derive_work_status(
        _task(),
        _audit([_session_start(600), _progress(STALE_PROGRESS_AFTER_SECONDS)]),
        now=NOW,
    )
    assert at["state"] == "stale_old_receipt"
    assert at["label"] == "Stale-but-alive — last update older than 5 minutes"


def test_stale_old_receipt_at_or_after_5m():
    s = derive_work_status(
        _task(),
        _audit([_session_start(3600), _progress(900, message="Old milestone")]),
        now=NOW,
    )
    assert s["state"] == "stale_old_receipt"
    assert s["latest_progress"]["message"] == "Old milestone"


# ── Current-session boundary scoping ────────────────────────────────────────


def test_prior_session_progress_does_not_satisfy_current_session():
    """A progress receipt BEFORE the latest session_start must not count."""
    s = derive_work_status(
        _task(),
        _audit([_session_start(600), _progress(700, message="prior session")]),
        now=NOW,
    )
    assert s["state"] == "stale_no_receipt"
    assert s["latest_progress"] is None


def test_latest_session_start_wins_as_boundary():
    """Two session_start rows: receipts between them belong to the OLD session."""
    s = derive_work_status(
        _task(),
        _audit([
            _session_start(3600, agent="dev_agent"),      # old session
            _progress(2400, message="old session work"),
            _session_start(120, agent="dev_agent"),       # current session
        ]),
        now=NOW,
    )
    assert s["state"] == "newly_started"  # current session has no receipt yet
    assert s["latest_progress"] is None
    assert s["session_start_ts"] == _ts(NOW - timedelta(seconds=120))


def test_receipt_after_current_session_start_counts():
    s = derive_work_status(
        _task(),
        _audit([
            _session_start(3600),
            _progress(2400, message="old session work"),
            _progress(120, message="current session work"),
        ]),
        now=NOW,
    )
    assert s["state"] == "recent_progress"
    assert s["latest_progress"]["message"] == "current session work"


def test_other_agent_session_start_and_progress_are_out_of_scope():
    s = derive_work_status(
        _task(),
        _audit([
            _session_start(600, agent="qa_engineer"),
            _progress(120, agent="qa_engineer", message="not ours"),
        ]),
        now=NOW,
    )
    # No dev_agent session_start → cannot bound the current session.
    assert s["state"] == "unavailable"
    assert s["reason"] == "no_session_start"
    assert s["latest_progress"] is None


# ── Heartbeat freshness honesty ─────────────────────────────────────────────


def test_heartbeat_stale_is_its_own_state():
    s = derive_work_status(
        _task(last_heartbeat=NOW - timedelta(seconds=120)),
        _audit([_session_start(600), _progress(60)]),
        now=NOW,
    )
    assert s["state"] == "heartbeat_stale"
    assert s["heartbeat"]["freshness"] == "stale"
    # Facts are still surfaced as observations, but the headline is liveness.
    assert s["latest_progress"] is not None


def test_heartbeat_stale_boundary_at_60s():
    under = derive_work_status(
        _task(last_heartbeat=NOW - timedelta(seconds=59)),
        _audit([_session_start(120)]),
        now=NOW,
    )
    assert under["state"] == "newly_started"
    at = derive_work_status(
        _task(last_heartbeat=NOW - timedelta(seconds=60)),
        _audit([_session_start(120)]),
        now=NOW,
    )
    assert at["state"] == "heartbeat_stale"
    assert at["heartbeat"]["freshness"] == "stale"


def test_heartbeat_absent_is_unavailable_not_fabricated():
    s = derive_work_status(
        _task(last_heartbeat=None),
        _audit([_session_start(120)]),
        now=NOW,
    )
    assert s["state"] == "heartbeat_unavailable"
    assert s["heartbeat"]["freshness"] == "unavailable"
    assert s["heartbeat"]["timestamp"] is None


# ── Absent / malformed historic data ────────────────────────────────────────


def test_unavailable_when_no_session_start_row():
    s = derive_work_status(
        _task(),
        _audit([_progress(120)]),  # progress exists but no boundary
        now=NOW,
    )
    assert s["state"] == "unavailable"
    assert s["reason"] == "no_session_start"
    assert s["latest_progress"] is None  # never claim a scoped receipt


def test_unavailable_when_unassigned():
    s = derive_work_status(
        _task(assigned_agent=None),
        _audit([_session_start(120)]),
        now=NOW,
    )
    assert s["state"] == "unavailable"
    assert s["reason"] == "unassigned"


def test_malformed_latest_session_start_timestamp_is_unavailable():
    s = derive_work_status(
        _task(),
        _audit([{** _session_start(120), "timestamp": "not-a-timestamp"}]),
        now=NOW,
    )
    assert s["state"] == "unavailable"


def test_malformed_progress_payload_surfaces_null_message_not_crash():
    s = derive_work_status(
        _task(),
        _audit([
            _session_start(600),
            {"agent": "dev_agent", "action": "progress",
             "payload": None, "timestamp": _ts(NOW - timedelta(seconds=60))},
        ]),
        now=NOW,
    )
    assert s["state"] == "recent_progress"
    assert s["latest_progress"]["message"] is None  # content unavailable, honest
    assert s["latest_progress"]["timestamp"] is not None


def test_progress_with_empty_message_still_counts_as_receipt():
    s = derive_work_status(
        _task(),
        _audit([_session_start(600), _progress(60, message="   ")]),
        now=NOW,
    )
    assert s["state"] == "recent_progress"
    assert s["latest_progress"]["message"] is None


def test_empty_audit_log_live_shape_is_unavailable():
    s = derive_work_status(_task(), _audit([]), now=NOW)
    assert s["state"] == "unavailable"
    assert s["reason"] == "no_session_start"
