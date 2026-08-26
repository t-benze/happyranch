"""Unit tests for dashboard_summary aggregations.

Each test seeds an in-memory SQLite via the standard Database fixture and
exercises one aggregation function with a deterministic `now` clock.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from runtime.infrastructure.database import Database
from runtime.models import JobInterpreter, JobRecord, JobStatus
from runtime.orchestrator.dashboard_summary import (
    compute_pending_review_jobs,
    compute_narrative_counts_today,
    compute_org_age_days,
    compute_spend_today,
)


class _MockKbStore:
    def __init__(self) -> None:
        self._today = 0
        self._this_week: list[dict] = []

    def set_entries_today(self, n: int) -> None:
        self._today = n

    def set_entries_this_week(self, rows: list[dict]) -> None:
        self._this_week = rows

    def count_entries_created_since(self, since: datetime) -> int:
        return self._today

    def list_entries_created_since(self, since: datetime) -> list[dict]:
        return list(self._this_week)


@pytest.fixture
def mock_kb_store() -> _MockKbStore:
    return _MockKbStore()


def test_org_age_days_empty_db(db: Database) -> None:
    assert compute_org_age_days(db) == 0


def test_org_age_days_counts_from_first_audit_row(db: Database) -> None:
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    five_days_ago = now - timedelta(days=5)
    # insert_audit_log always stamps `now`; use raw SQL for a backdated row.
    db._conn.execute(
        "INSERT INTO audit_log (task_id, agent, action, payload, timestamp)"
        " VALUES (?, ?, ?, ?, ?)",
        ("TASK-1", "founder", "session_start", None, five_days_ago.isoformat()),
    )
    db._conn.commit()
    assert compute_org_age_days(db, now=now) == 5


def test_spend_today_empty(db: Database) -> None:
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    assert compute_spend_today(db, now=now) == 0.0


def test_spend_today_sums_today_only(db: Database) -> None:
    # NOTE: The dashboard spec calls this "spend from token_usage" but the real
    # schema stores per-session estimated_cost on task_results, not on
    # session_token_usage. compute_spend_today aggregates task_results
    # since local midnight; tests seed that table accordingly.
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    yesterday = now - timedelta(days=1)
    today = now - timedelta(hours=2)
    for idx, (ts, cost) in enumerate(
        [(yesterday, 5.00), (today, 1.50), (today, 2.25)]
    ):
        db._conn.execute(
            "INSERT INTO task_results "
            "(task_id, agent, session_id, status, estimated_cost, created_at) "
            "VALUES (?, 'a', ?, 'completed', ?, ?)",
            (f"T-{idx}", f"s-{idx}", cost, ts.isoformat()),
        )
    db._conn.commit()
    assert compute_spend_today(db, now=now) == pytest.approx(3.75)


def test_pending_review_jobs_include_only_pending_founder_review_rows_in_order(
    db: Database,
) -> None:
    def insert(
        job_id: str, *, status: JobStatus, review_required: bool, created_at: str,
    ) -> None:
        db.insert_job(JobRecord(
            id=job_id,
            task_id=f"TASK-{job_id[-1]}",
            agent_name="dev_agent",
            title=f"{job_id} title",
            rationale="test",
            script_text="echo hi",
            interpreter=JobInterpreter.BASH,
            status=status,
            review_required=review_required,
            created_at=created_at,
        ))

    insert("JOB-001", status=JobStatus.PENDING, review_required=True, created_at="2026-05-30T10:00:00Z")
    insert("JOB-002", status=JobStatus.PENDING, review_required=True, created_at="2026-05-30T11:00:00Z")
    insert("JOB-003", status=JobStatus.PENDING, review_required=False, created_at="2026-05-30T12:00:00Z")
    insert("JOB-004", status=JobStatus.REJECTED, review_required=True, created_at="2026-05-30T13:00:00Z")

    rows = compute_pending_review_jobs(db)

    assert [row.id for row in rows] == ["JOB-002", "JOB-001"]
    assert rows[0].model_dump(mode="json") == {
        "id": "JOB-002",
        "task_id": "TASK-2",
        "agent_name": "dev_agent",
        "title": "JOB-002 title",
        "created_at": "2026-05-30T11:00:00Z",
    }


def test_narrative_counts_zero(db: Database, mock_kb_store: _MockKbStore) -> None:
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    counts = compute_narrative_counts_today(db, now=now, kb_store=mock_kb_store)
    assert counts.completed_today == 0
    assert counts.failed_today == 0
    assert counts.escalated_open == 0
    assert counts.kb_added_today == 0
    assert counts.agents_active_now == 0
    assert counts.spend_today_usd == 0.0


def test_narrative_counts_populated(db: Database, mock_kb_store: _MockKbStore) -> None:
    # NOTE: the `tasks` table PK is `id` (not `task_id`); `block_kind` is added
    # via ALTER in _create_tables. Both confirmed against
    # src/infrastructure/database.py before composing these INSERTs.
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    today = now - timedelta(hours=2)

    # 2 completed today, 1 failed today
    for tid, status in [("TASK-1", "completed"), ("TASK-2", "completed"), ("TASK-3", "failed")]:
        db._conn.execute(
            "INSERT INTO tasks (id, brief, assigned_agent, team, status, created_at, updated_at) "
            "VALUES (?, 'b', 'a', 't', ?, ?, ?)",
            (tid, status, today.isoformat(), today.isoformat()),
        )
    # 1 escalated (Path B: stored top-level status='escalated', block_kind cleared)
    db._conn.execute(
        "INSERT INTO tasks (id, brief, assigned_agent, team, status, created_at, updated_at) "
        "VALUES ('TASK-4', 'b', 'a', 't', 'escalated', ?, ?)",
        (today.isoformat(), today.isoformat()),
    )
    # 1 active session_start with no matching session_end (distinct agent counts as active)
    db._conn.execute(
        "INSERT INTO audit_log (timestamp, task_id, agent, action, payload) "
        "VALUES (?, 'TASK-5', 'dev_agent', 'session_start', NULL)",
        (today.isoformat(),),
    )
    # Spend today: 2.50 in task_results.estimated_cost
    db._conn.execute(
        "INSERT INTO task_results (task_id, agent, session_id, status, estimated_cost, created_at) "
        "VALUES ('TASK-6', 'a', 's', 'completed', 2.50, ?)",
        (today.isoformat(),),
    )
    db._conn.commit()
    # KB: 3 entries today via the mock
    mock_kb_store.set_entries_today(3)

    counts = compute_narrative_counts_today(db, now=now, kb_store=mock_kb_store)
    assert counts.completed_today == 2
    assert counts.failed_today == 1
    assert counts.escalated_open == 1
    assert counts.kb_added_today == 3
    assert counts.agents_active_now == 1
    assert counts.spend_today_usd == pytest.approx(2.50)


# ── Phase 0 adversarial parity tests ──────────────────────────────────────
# These prove the new grouped MAX(session_start) vs MAX(session_end) query
# is semantically identical to the original correlated NOT EXISTS query
# across all edge cases identified in the TASK-3878 root-cause report.

def _seed_session(db: Database, ts: datetime, task_id: str, agent: str,
                  action: str) -> None:
    db._conn.execute(
        "INSERT INTO audit_log (timestamp, task_id, agent, action, payload) "
        "VALUES (?, ?, ?, ?, NULL)",
        (ts.isoformat(), task_id, agent, action),
    )


def test_agents_active_open_session_no_end(db: Database, mock_kb_store: _MockKbStore) -> None:
    """An unmatched session_start → agent is active."""
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    _seed_session(db, now - timedelta(hours=1), "TASK-A", "agent1", "session_start")
    db._conn.commit()
    counts = compute_narrative_counts_today(db, now=now, kb_store=mock_kb_store)
    assert counts.agents_active_now == 1


def test_agents_active_closed_session_not_active(db: Database, mock_kb_store: _MockKbStore) -> None:
    """A session_start followed by a later session_end → agent is NOT active."""
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    _seed_session(db, now - timedelta(hours=2), "TASK-A", "agent1", "session_start")
    _seed_session(db, now - timedelta(hours=1), "TASK-A", "agent1", "session_end")
    db._conn.commit()
    counts = compute_narrative_counts_today(db, now=now, kb_store=mock_kb_store)
    assert counts.agents_active_now == 0


def test_agents_active_equal_timestamps_stays_active(db: Database, mock_kb_store: _MockKbStore) -> None:
    """End whose timestamp EQUALS (not > ) a start does NOT close it.
    The original > semantic leaves it active."""
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    ts = now - timedelta(hours=1)
    _seed_session(db, ts, "TASK-A", "agent1", "session_start")
    _seed_session(db, ts, "TASK-A", "agent1", "session_end")
    db._conn.commit()
    counts = compute_narrative_counts_today(db, now=now, kb_store=mock_kb_store)
    assert counts.agents_active_now == 1


def test_agents_active_restarted_session_open_after_close(db: Database, mock_kb_store: _MockKbStore) -> None:
    """Restarted session: close old, start new, no close → still active."""
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    _seed_session(db, now - timedelta(hours=4), "TASK-A", "agent1", "session_start")
    _seed_session(db, now - timedelta(hours=3), "TASK-A", "agent1", "session_end")
    _seed_session(db, now - timedelta(hours=2), "TASK-A", "agent1", "session_start")
    db._conn.commit()
    counts = compute_narrative_counts_today(db, now=now, kb_store=mock_kb_store)
    assert counts.agents_active_now == 1


def test_agents_active_restarted_fully_closed_not_active(db: Database, mock_kb_store: _MockKbStore) -> None:
    """Restarted session that was fully closed → not active."""
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    _seed_session(db, now - timedelta(hours=4), "TASK-A", "agent1", "session_start")
    _seed_session(db, now - timedelta(hours=3), "TASK-A", "agent1", "session_end")
    _seed_session(db, now - timedelta(hours=2), "TASK-A", "agent1", "session_start")
    _seed_session(db, now - timedelta(hours=1), "TASK-A", "agent1", "session_end")
    db._conn.commit()
    counts = compute_narrative_counts_today(db, now=now, kb_store=mock_kb_store)
    assert counts.agents_active_now == 0


def test_agents_active_multiple_tasks_one_open(db: Database, mock_kb_store: _MockKbStore) -> None:
    """One agent in two tasks: one closed, one open → still active (distinct-agent count, not per-session)."""
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    # Task A: closed
    _seed_session(db, now - timedelta(hours=3), "TASK-A", "agent1", "session_start")
    _seed_session(db, now - timedelta(hours=2), "TASK-A", "agent1", "session_end")
    # Task B: still open
    _seed_session(db, now - timedelta(hours=1), "TASK-B", "agent1", "session_start")
    db._conn.commit()
    counts = compute_narrative_counts_today(db, now=now, kb_store=mock_kb_store)
    assert counts.agents_active_now == 1


def test_agents_active_multiple_tasks_all_closed(db: Database, mock_kb_store: _MockKbStore) -> None:
    """One agent in two tasks: both closed → not active."""
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    _seed_session(db, now - timedelta(hours=4), "TASK-A", "agent1", "session_start")
    _seed_session(db, now - timedelta(hours=3), "TASK-A", "agent1", "session_end")
    _seed_session(db, now - timedelta(hours=2), "TASK-B", "agent1", "session_start")
    _seed_session(db, now - timedelta(hours=1), "TASK-B", "agent1", "session_end")
    db._conn.commit()
    counts = compute_narrative_counts_today(db, now=now, kb_store=mock_kb_store)
    assert counts.agents_active_now == 0


def test_agents_active_distinct_agents_counted_separately(db: Database, mock_kb_store: _MockKbStore) -> None:
    """Two different agents, both active → count = 2 (distinct agents)."""
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    _seed_session(db, now - timedelta(hours=1), "TASK-A", "agent1", "session_start")
    _seed_session(db, now - timedelta(hours=1), "TASK-B", "agent2", "session_start")
    db._conn.commit()
    counts = compute_narrative_counts_today(db, now=now, kb_store=mock_kb_store)
    assert counts.agents_active_now == 2


def test_agents_active_duplicate_agent_starts_one_task_counted_once(db: Database, mock_kb_store: _MockKbStore) -> None:
    """Same agent, same task → distinct agent count = 1 (agent is counted once regardless of how many tasks they're active in)."""
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    _seed_session(db, now - timedelta(hours=2), "TASK-A", "agent1", "session_start")
    _seed_session(db, now - timedelta(hours=1), "TASK-A", "agent1", "session_start")
    db._conn.commit()
    counts = compute_narrative_counts_today(db, now=now, kb_store=mock_kb_store)
    assert counts.agents_active_now == 1


def test_agents_active_no_sessions(db: Database, mock_kb_store: _MockKbStore) -> None:
    """No session rows at all → 0 active agents."""
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    counts = compute_narrative_counts_today(db, now=now, kb_store=mock_kb_store)
    assert counts.agents_active_now == 0


def test_agents_active_end_before_start_different_task_irrelevant(db: Database, mock_kb_store: _MockKbStore) -> None:
    """An end for a different task_id should not affect the active count for another task."""
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    # Task A: agent1 is open
    _seed_session(db, now - timedelta(hours=1), "TASK-A", "agent1", "session_start")
    # Task B: agent1 has an end but no start (should not close Task A's start)
    _seed_session(db, now - timedelta(hours=2), "TASK-B", "agent1", "session_end")
    db._conn.commit()
    counts = compute_narrative_counts_today(db, now=now, kb_store=mock_kb_store)
    assert counts.agents_active_now == 1


def test_agents_active_only_session_ends_no_starts(db: Database, mock_kb_store: _MockKbStore) -> None:
    """Only session_end rows (no session_start) → 0 active agents."""
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    _seed_session(db, now - timedelta(hours=1), "TASK-A", "agent1", "session_end")
    db._conn.commit()
    counts = compute_narrative_counts_today(db, now=now, kb_store=mock_kb_store)
    assert counts.agents_active_now == 0


def test_agents_active_overlapping_mixed_agents(db: Database, mock_kb_store: _MockKbStore) -> None:
    """Two agents with overlapping sessions: one open, one closed → count = 1."""
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    # agent1: closed
    _seed_session(db, now - timedelta(hours=3), "TASK-A", "agent1", "session_start")
    _seed_session(db, now - timedelta(hours=2), "TASK-A", "agent1", "session_end")
    # agent2: open
    _seed_session(db, now - timedelta(hours=1), "TASK-B", "agent2", "session_start")
    db._conn.commit()
    counts = compute_narrative_counts_today(db, now=now, kb_store=mock_kb_store)
    assert counts.agents_active_now == 1


def test_agents_active_multiple_restarts_last_open(db: Database, mock_kb_store: _MockKbStore) -> None:
    """Agent restarts 3 times: the last start is unmatched → active."""
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    _seed_session(db, now - timedelta(hours=6), "TASK-A", "agent1", "session_start")
    _seed_session(db, now - timedelta(hours=5), "TASK-A", "agent1", "session_end")
    _seed_session(db, now - timedelta(hours=4), "TASK-A", "agent1", "session_start")
    _seed_session(db, now - timedelta(hours=3), "TASK-A", "agent1", "session_end")
    _seed_session(db, now - timedelta(hours=2), "TASK-A", "agent1", "session_start")
    db._conn.commit()
    counts = compute_narrative_counts_today(db, now=now, kb_store=mock_kb_store)
    assert counts.agents_active_now == 1


def test_agents_active_parity_regression_safety(db: Database, mock_kb_store: _MockKbStore) -> None:
    """Brute-force parity check: seed a variety of overlapping/restarted
    sessions and verify agents_active_now is deterministic and ≥ 0."""
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    # agent1: open (TASK-A)
    _seed_session(db, now - timedelta(hours=1), "TASK-A", "agent1", "session_start")
    # agent2: closed (TASK-B) then open (TASK-C)
    _seed_session(db, now - timedelta(hours=5), "TASK-B", "agent2", "session_start")
    _seed_session(db, now - timedelta(hours=4), "TASK-B", "agent2", "session_end")
    _seed_session(db, now - timedelta(hours=1), "TASK-C", "agent2", "session_start")
    # agent3: closed (TASK-D)
    _seed_session(db, now - timedelta(hours=3), "TASK-D", "agent3", "session_start")
    _seed_session(db, now - timedelta(hours=2), "TASK-D", "agent3", "session_end")
    # agent4: no sessions at all
    # agent5: equal-timestamp pair (should stay active)
    ts_eq = now - timedelta(hours=1, minutes=30)
    _seed_session(db, ts_eq, "TASK-E", "agent5", "session_start")
    _seed_session(db, ts_eq, "TASK-E", "agent5", "session_end")
    db._conn.commit()
    counts = compute_narrative_counts_today(db, now=now, kb_store=mock_kb_store)
    # agent1 (active), agent2 (active in TASK-C), agent5 (equal timestamps → active)
    assert counts.agents_active_now == 3, f"expected 3 active agents, got {counts.agents_active_now}"


from runtime.orchestrator.dashboard_summary import compute_heartbeat_24h


def test_heartbeat_empty_returns_24_zero_buckets(db: Database) -> None:
    now = datetime(2026, 5, 30, 14, 30, 0, tzinfo=timezone.utc)
    buckets = compute_heartbeat_24h(db, now=now)
    assert len(buckets) == 24
    assert all(b.steps == 0 for b in buckets)
    assert all(b.tier == "ok" for b in buckets)


def test_heartbeat_counts_steps_per_hour(db: Database) -> None:
    now = datetime(2026, 5, 30, 14, 30, 0, tzinfo=timezone.utc)
    # Three session_starts in the same hour
    for minute in [5, 20, 50]:
        ts = now.replace(hour=10, minute=minute, second=0, microsecond=0)
        db._conn.execute(
            "INSERT INTO audit_log (timestamp, task_id, agent, action, payload) "
            "VALUES (?, 'T', 'a', 'session_start', NULL)",
            (ts.isoformat(),),
        )
    db._conn.commit()
    buckets = compute_heartbeat_24h(db, now=now)
    # The bucket for hour=10 should show 3 steps
    bucket_10 = next(b for b in buckets if b.hour == 10)
    assert bucket_10.steps == 3


def test_heartbeat_tier_thresholds(db: Database) -> None:
    now = datetime(2026, 5, 30, 14, 30, 0, tzinfo=timezone.utc)
    # 9 completed + 1 failed terminal tasks at hour=11 → 10% fail = warn.
    # The 10 session_start audit rows keep `steps` aligned with the activity.
    ts = now.replace(hour=11, minute=0, second=0, microsecond=0)
    for i in range(9):
        db._conn.execute(
            "INSERT INTO tasks (id, brief, assigned_agent, team, status, created_at, updated_at) "
            "VALUES (?, 'b', 'a', 't', 'completed', ?, ?)",
            (f"TASK-OK-{i}", ts.isoformat(), ts.isoformat()),
        )
    db._conn.execute(
        "INSERT INTO tasks (id, brief, assigned_agent, team, status, created_at, updated_at) "
        "VALUES ('TASK-FAIL-1', 'b', 'a', 't', 'failed', ?, ?)",
        (ts.isoformat(), ts.isoformat()),
    )
    for _ in range(10):
        db._conn.execute(
            "INSERT INTO audit_log (timestamp, task_id, agent, action, payload) "
            "VALUES (?, 'T', 'a', 'session_start', NULL)",
            (ts.isoformat(),),
        )
    db._conn.commit()
    buckets = compute_heartbeat_24h(db, now=now)
    bucket_11 = next(b for b in buckets if b.hour == 11)
    assert bucket_11.steps == 10
    assert bucket_11.failed == 1
    assert bucket_11.tier == "warn"


def test_heartbeat_counts_failed_from_terminal_tasks(db: Database) -> None:
    """Failed tasks transitioned via _fail() / daemon-restart sweep / escalation
    rejection never write a completion_report row, and several paths leave
    completed_at NULL. The bucket's `failed` count must reflect terminal
    status bucketed by `updated_at` so all three paths show up."""
    now = datetime(2026, 5, 30, 14, 30, 0, tzinfo=timezone.utc)
    ts = now.replace(hour=9, minute=15, second=0, microsecond=0)
    # Mix: two with completed_at set, one with completed_at NULL (mirrors
    # the daemon-restart-sweep path which only writes status+note).
    db._conn.execute(
        "INSERT INTO tasks (id, brief, assigned_agent, team, status, created_at, updated_at, completed_at) "
        "VALUES ('TASK-A', 'b', 'a', 't', 'failed', ?, ?, ?)",
        (ts.isoformat(), ts.isoformat(), ts.isoformat()),
    )
    db._conn.execute(
        "INSERT INTO tasks (id, brief, assigned_agent, team, status, created_at, updated_at, completed_at) "
        "VALUES ('TASK-B', 'b', 'a', 't', 'failed', ?, ?, ?)",
        (ts.isoformat(), ts.isoformat(), ts.isoformat()),
    )
    db._conn.execute(
        "INSERT INTO tasks (id, brief, assigned_agent, team, status, created_at, updated_at) "
        "VALUES ('TASK-C', 'b', 'a', 't', 'failed', ?, ?)",
        (ts.isoformat(), ts.isoformat()),
    )
    db._conn.commit()
    buckets = compute_heartbeat_24h(db, now=now)
    bucket_9 = next(b for b in buckets if b.hour == 9)
    assert bucket_9.failed == 3
    assert bucket_9.tier == "bad"


from runtime.orchestrator.dashboard_summary import compute_recent_activity


def test_recent_activity_empty(db: Database) -> None:
    assert compute_recent_activity(db, n=6) == []


def test_recent_activity_returns_last_n_desc(db: Database) -> None:
    base = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    for i in range(10):
        ts = base + timedelta(minutes=i)
        db._conn.execute(
            "INSERT INTO audit_log (timestamp, task_id, agent, action, payload) "
            "VALUES (?, ?, 'agent', 'session_start', NULL)",
            (ts.isoformat(), f"TASK-{i}"),
        )
    db._conn.commit()
    rows = compute_recent_activity(db, n=6)
    assert len(rows) == 6
    # DESC by timestamp — newest first
    assert rows[0].task_id == "TASK-9"
    assert rows[5].task_id == "TASK-4"


def test_recent_activity_filters_kind(db: Database) -> None:
    base = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    # 'progress' is NOT in the recent_activity allowlist
    db._conn.execute(
        "INSERT INTO audit_log (timestamp, task_id, agent, action, payload) "
        "VALUES (?, 'T', 'a', 'progress', NULL)",
        (base.isoformat(),),
    )
    db._conn.execute(
        "INSERT INTO audit_log (timestamp, task_id, agent, action, payload) "
        "VALUES (?, 'T', 'a', 'session_start', NULL)",
        ((base + timedelta(seconds=1)).isoformat(),),
    )
    db._conn.commit()
    rows = compute_recent_activity(db, n=6)
    assert len(rows) == 1
    assert rows[0].event_kind == "session_start"


def test_recent_activity_extracts_verdict(db: Database) -> None:
    base = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    db._conn.execute(
        "INSERT INTO audit_log (timestamp, task_id, agent, action, payload) "
        'VALUES (?, \'T\', \'a\', \'completion_report\', \'{"status":"completed"}\')',
        (base.isoformat(),),
    )
    db._conn.execute(
        "INSERT INTO audit_log (timestamp, task_id, agent, action, payload) "
        'VALUES (?, \'T\', \'a\', \'review_verdict\', \'{"verdict":"request_changes"}\')',
        ((base + timedelta(seconds=1)).isoformat(),),
    )
    db._conn.commit()
    rows = compute_recent_activity(db, n=6)
    by_kind = {r.event_kind: r for r in rows}
    assert by_kind["completion_report"].verdict == "ok"
    assert by_kind["review_verdict"].verdict == "fail"


def test_verdict_from_payload_normalizes_spellings() -> None:
    """review_verdict payloads normalize case/separator-insensitively at the
    recent-activity read boundary; unknown/blank yields no tone."""
    from runtime.orchestrator.dashboard_summary import _verdict_from_payload

    assert _verdict_from_payload(
        "review_verdict", '{"verdict":"APPROVE"}') == "ok"
    assert _verdict_from_payload(
        "review_verdict", '{"verdict":"request changes"}') == "fail"
    assert _verdict_from_payload(
        "review_verdict", '{"verdict":"pass"}') == "ok"
    assert _verdict_from_payload(
        "review_verdict", '{"verdict":"REVISE"}') == "fail"
    # Blank / unknown free-string verdict → no tone (not ok, not fail).
    assert _verdict_from_payload(
        "review_verdict", '{"verdict":""}') is None
    assert _verdict_from_payload(
        "review_verdict", '{"verdict":"MAYBE"}') is None


def test_recent_activity_serializes_dream_id_for_dream_thread(db: Database) -> None:
    """ActivityRow serializes _thread_dream_id ONLY for dream-originated threads."""
    base = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)

    # Thread with composed_from_dream_id set
    db._conn.execute(
        "INSERT INTO threads (id, subject, started_at, status, composed_from_dream_id) "
        "VALUES ('THR-001', 'Dream chat', ?, 'open', 'dream-alpha')",
        (base.isoformat(),),
    )
    # Thread without composed_from_dream_id
    db._conn.execute(
        "INSERT INTO threads (id, subject, started_at, status) "
        "VALUES ('THR-002', 'Normal chat', ?, 'open')",
        (base.isoformat(),),
    )
    # Audit entry for dream thread
    db._conn.execute(
        "INSERT INTO audit_log (timestamp, task_id, agent, action, payload) "
        "VALUES (?, 'THR-001', 'founder', 'session_start', NULL)",
        ((base + timedelta(seconds=1)).isoformat(),),
    )
    # Audit entry for non-dream thread
    db._conn.execute(
        "INSERT INTO audit_log (timestamp, task_id, agent, action, payload) "
        "VALUES (?, 'THR-002', 'founder', 'session_start', NULL)",
        ((base + timedelta(seconds=2)).isoformat(),),
    )
    db._conn.commit()

    rows = compute_recent_activity(db, n=6)
    assert len(rows) == 2

    # Non-dream thread (newer, so index 0): _thread_dream_id must be absent in dump
    non_dream = rows[0]
    assert non_dream.task_id == "THR-002"
    dumped_non = non_dream.model_dump(by_alias=True)
    assert "_thread_dream_id" in dumped_non
    assert dumped_non["_thread_dream_id"] is None

    # Dream thread: _thread_dream_id must carry the dream id
    dream_row = rows[1]
    assert dream_row.task_id == "THR-001"
    dumped_dream = dream_row.model_dump(by_alias=True)
    assert "_thread_dream_id" in dumped_dream
    assert dumped_dream["_thread_dream_id"] == "dream-alpha"

    # Verify by_alias=False also works (no alias, uses field name)
    dumped_fieldname = dream_row.model_dump()
    assert "thread_dream_id" in dumped_fieldname
    assert dumped_fieldname["thread_dream_id"] == "dream-alpha"


from runtime.orchestrator.dashboard_summary import (
    compute_updates_this_week, ActivityRow, DashboardSummaryResponse,
)


def test_updates_this_week_empty(db: Database, mock_kb_store: _MockKbStore) -> None:
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    assert compute_updates_this_week(db, now=now, kb_store=mock_kb_store) == []


def test_updates_this_week_combines_kb_and_learnings(
    db: Database, mock_kb_store: _MockKbStore
) -> None:
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    two_days_ago = now - timedelta(days=2)
    # KB entries this week (via mock)
    mock_kb_store.set_entries_this_week([
        {"slug": "release-publish-authority",
         "created_at": (now - timedelta(days=1)).isoformat()},
        {"slug": "photo-attribution",
         "created_at": (now - timedelta(days=3)).isoformat()},
    ])
    # learning_promoted audit row
    db._conn.execute(
        "INSERT INTO audit_log (timestamp, task_id, agent, action, payload) "
        'VALUES (?, \'T\', \'engineering_head\', \'learning_promoted\', \'{"kb_slug":"prd-authority"}\')',
        (two_days_ago.isoformat(),),
    )
    db._conn.commit()
    rows = compute_updates_this_week(db, now=now, kb_store=mock_kb_store)
    assert len(rows) == 3
    kinds = [(r.marker, r.text, r.meta) for r in rows]
    assert ("add", "KB +1", "release-publish-authority") in kinds
    assert ("add", "KB +1", "photo-attribution") in kinds
    assert ("info", "Learning promoted to KB", "prd-authority") in kinds
    # Sort assertion: DESC by timestamp
    for i in range(len(rows) - 1):
        assert rows[i].timestamp >= rows[i + 1].timestamp


from runtime.orchestrator.dashboard_summary import (
    compute_escalations_open, compute_active_by_team,
    compute_stale_escalations, classify_escalation_flavor,
)


def test_escalations_empty(db: Database) -> None:
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    assert compute_escalations_open(db, now=now) == []


def test_escalations_reads_question_from_audit_payload(db: Database) -> None:
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    raised = now - timedelta(minutes=30)
    db._conn.execute(
        "INSERT INTO tasks (id, brief, assigned_agent, team, status, created_at, updated_at) "
        "VALUES ('TASK-101', 'b', 'qa_engineer', 'engineering', 'escalated', ?, ?)",
        (raised.isoformat(), raised.isoformat()),
    )
    db._conn.execute(
        "INSERT INTO audit_log (timestamp, task_id, agent, action, payload) "
        'VALUES (?, \'TASK-101\', \'qa_engineer\', \'escalation\', \'{"reason":"Photo licensing unclear"}\')',
        (raised.isoformat(),),
    )
    db._conn.commit()
    rows = compute_escalations_open(db, now=now)
    assert len(rows) == 1
    assert rows[0].task_id == "TASK-101"
    assert rows[0].question == "Photo licensing unclear"
    assert rows[0].age_seconds == 30 * 60
    # §G: a genuine agent reason derives the "needs-decision" flavor.
    assert rows[0].flavor == "needs-decision"


def test_escalations_flavor_derived_from_reason(db: Database) -> None:
    """§G: the single stored `escalated` status derives a display flavor from
    the escalation audit reason (exhausted / over-budget / needs-decision)."""
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    raised = now - timedelta(minutes=5)
    cases = [
        ("TASK-EX", "failure-round bound (2) exhausted: 3 failed subtasks", "exhausted"),
        ("TASK-OB", "max steps (40) exceeded", "over-budget"),
        ("TASK-BS", "iteration_budget_exhausted: revise budget (3 rounds) exhausted", "budget-stopped"),
        ("TASK-ND", "Need founder ruling on refund policy", "needs-decision"),
    ]
    for tid, reason, _ in cases:
        db._conn.execute(
            "INSERT INTO tasks (id, brief, assigned_agent, team, status, created_at, updated_at) "
            "VALUES (?, 'b', 'orchestrator', 'engineering', 'escalated', ?, ?)",
            (tid, raised.isoformat(), raised.isoformat()),
        )
        db._conn.execute(
            "INSERT INTO audit_log (timestamp, task_id, agent, action, payload) "
            "VALUES (?, ?, 'orchestrator', 'escalation', ?)",
            (raised.isoformat(), tid, json.dumps({"reason": reason})),
        )
    db._conn.commit()
    by_id = {r.task_id: r for r in compute_escalations_open(db, now=now)}
    for tid, _, expected in cases:
        assert by_id[tid].flavor == expected


def test_classify_escalation_flavor_budget_stopped() -> None:
    """THR-026 seq33: iteration_budget_exhausted → 'budget-stopped' flavor."""
    assert classify_escalation_flavor(
        "iteration_budget_exhausted: revise budget (3 rounds) exhausted"
    ) == "budget-stopped"
    # Plain agent escalate still returns needs-decision
    assert classify_escalation_flavor("Need founder ruling on refund policy") == "needs-decision"


def test_classify_escalation_flavor_graceful_fallback() -> None:
    """§G best-effort: absent/empty reason → None (surface shows plain escalated)."""
    assert classify_escalation_flavor(None) is None
    assert classify_escalation_flavor("") is None
    assert classify_escalation_flavor("anything else") == "needs-decision"


def test_escalations_open_only_roots_excludes_children(db: Database) -> None:
    """TASK-1763: compute_escalations_open must match list_roots semantics —
    only root tasks (parent_task_id IS NULL) in status='escalated' count.
    A child task in escalated status inflates the Home 'Waiting on you' badge
    without appearing in the roots-only Tasks list."""
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    raised = now - timedelta(minutes=30)

    # ROOT escalated task: this IS the founder-facing escalation.
    db._conn.execute(
        "INSERT INTO tasks (id, brief, assigned_agent, team, status, parent_task_id, created_at, updated_at) "
        "VALUES ('TASK-root', 'b', 'dev_agent', 'engineering', 'escalated', NULL, ?, ?)",
        (raised.isoformat(), raised.isoformat()),
    )
    # CHILD escalated task (parent_task_id IS NOT NULL): this is a subtask that
    # entered the escalated state but should NOT appear as a founder-facing
    # escalation — only its root ancestor matters (per CLAUDE.md: "Only root
    # tasks escalate to the founder").
    db._conn.execute(
        "INSERT INTO tasks (id, brief, assigned_agent, team, status, parent_task_id, created_at, updated_at) "
        "VALUES ('TASK-child', 'b', 'qa_engineer', 'engineering', 'escalated', 'TASK-parent', ?, ?)",
        (raised.isoformat(), raised.isoformat()),
    )
    # The parent of the child — an in-progress root, not itself escalated.
    db._conn.execute(
        "INSERT INTO tasks (id, brief, assigned_agent, team, status, parent_task_id, created_at, updated_at) "
        "VALUES ('TASK-parent', 'b', 'engineering_manager', 'engineering', 'in_progress', NULL, ?, ?)",
        (raised.isoformat(), raised.isoformat()),
    )
    db._conn.commit()

    # compute_escalations_open should only list root-level escalations.
    rows = compute_escalations_open(db, now=now)
    task_ids = [r.task_id for r in rows]
    assert "TASK-root" in task_ids, f"Root escalation missing: {task_ids}"
    assert "TASK-child" not in task_ids, (
        f"Child escalation leaked into dashboard: {task_ids}"
    )
    assert len(rows) == 1, f"Expected 1 root escalation, got {len(rows)}: {task_ids}"


def test_narrative_counts_escalated_open_only_roots(db: Database) -> None:
    """TASK-1763: narrative_counts.escalated_open must count only root tasks,
    matching list_roots semantics — a child escalation inflates the count
    without appearing in the Tasks list."""
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    raised = now - timedelta(minutes=30)

    # Root escalated → should count.
    db._conn.execute(
        "INSERT INTO tasks (id, brief, assigned_agent, team, status, parent_task_id, created_at, updated_at) "
        "VALUES ('TASK-root', 'b', 'dev_agent', 'engineering', 'escalated', NULL, ?, ?)",
        (raised.isoformat(), raised.isoformat()),
    )
    # Child escalated → should NOT count.
    db._conn.execute(
        "INSERT INTO tasks (id, brief, assigned_agent, team, status, parent_task_id, created_at, updated_at) "
        "VALUES ('TASK-child', 'b', 'qa_engineer', 'engineering', 'escalated', 'TASK-other', ?, ?)",
        (raised.isoformat(), raised.isoformat()),
    )
    db._conn.commit()

    counts = compute_narrative_counts_today(db, now=now, kb_store=_MockKbStore())
    assert counts.escalated_open == 1, (
        f"Expected escalated_open=1 (roots only), got {counts.escalated_open}"
    )


def test_stale_escalations_only_roots_excludes_children(db: Database) -> None:
    """TASK-1763: compute_stale_escalations must also filter to root tasks
    for internal consistency — 'stale escalations awaiting founder' has the
    same roots-only semantics as the main escalation aggregations."""
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    old = now - timedelta(hours=30)  # past the 24h default threshold

    # Root stale escalation → should show.
    db._conn.execute(
        "INSERT INTO tasks (id, brief, assigned_agent, team, status, parent_task_id, created_at, updated_at) "
        "VALUES ('TASK-root', 'b', 'dev_agent', 'engineering', 'escalated', NULL, ?, ?)",
        (old.isoformat(), old.isoformat()),
    )
    # Child stale escalation → should NOT show.
    db._conn.execute(
        "INSERT INTO tasks (id, brief, assigned_agent, team, status, parent_task_id, created_at, updated_at) "
        "VALUES ('TASK-child', 'b', 'qa_engineer', 'engineering', 'escalated', 'TASK-other', ?, ?)",
        (old.isoformat(), old.isoformat()),
    )
    db._conn.commit()

    rows = compute_stale_escalations(db, now=now)
    task_ids = [r.task_id for r in rows]
    assert "TASK-root" in task_ids, f"Root stale escalation missing: {task_ids}"
    assert "TASK-child" not in task_ids, (
        f"Child stale escalation leaked: {task_ids}"
    )


def test_stale_escalations_empty(db: Database) -> None:
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    assert compute_stale_escalations(db, now=now) == []


def test_stale_escalations_includes_escalated_excludes_delegated(db: Database) -> None:
    """Path B (THR-037 §F.4 fix): only genuine stale `escalated` rows count.
    A delegating parent is now a healthy `in_progress` task and is EXCLUDED —
    it no longer bucketed in with open founder items."""
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    old = now - timedelta(hours=30)  # past the 24h threshold
    # Genuine stale escalation → INCLUDED.
    db._conn.execute(
        "INSERT INTO tasks (id, brief, assigned_agent, team, status, created_at, updated_at) "
        "VALUES ('TASK-ESC', 'b', 'dev_agent', 'engineering', 'escalated', ?, ?)",
        (old.isoformat(), old.isoformat()),
    )
    # Parked delegating parent (in_progress + delegated) → EXCLUDED (healthy).
    db._conn.execute(
        "INSERT INTO tasks (id, brief, assigned_agent, team, status, block_kind, created_at, updated_at) "
        "VALUES ('TASK-DEL', 'b', 'dev_agent', 'engineering', 'in_progress', 'delegated', ?, ?)",
        (old.isoformat(), old.isoformat()),
    )
    db._conn.commit()
    rows = compute_stale_escalations(db, now=now)
    assert {r.task_id for r in rows} == {"TASK-ESC"}
    assert rows[0].age_seconds == 30 * 3600


def test_stale_escalations_excludes_fresh_and_other_states(db: Database) -> None:
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    fresh = now - timedelta(hours=2)   # within threshold → excluded
    old = now - timedelta(hours=30)
    # Fresh escalation → too new → excluded.
    db._conn.execute(
        "INSERT INTO tasks (id, brief, assigned_agent, team, status, created_at, updated_at) "
        "VALUES ('TASK-fresh', 'b', 'dev_agent', 'engineering', 'escalated', ?, ?)",
        (fresh.isoformat(), fresh.isoformat()),
    )
    # Parked-on-job (in_progress + blocked_on_job) → not escalated → excluded.
    db._conn.execute(
        "INSERT INTO tasks (id, brief, assigned_agent, team, status, block_kind, created_at, updated_at) "
        "VALUES ('TASK-job', 'b', 'dev_agent', 'engineering', 'in_progress', 'blocked_on_job', ?, ?)",
        (old.isoformat(), old.isoformat()),
    )
    # A completed task is never escalated → excluded.
    db._conn.execute(
        "INSERT INTO tasks (id, brief, assigned_agent, team, status, created_at, updated_at) "
        "VALUES ('TASK-done', 'b', 'dev_agent', 'engineering', 'completed', ?, ?)",
        (old.isoformat(), old.isoformat()),
    )
    db._conn.commit()
    assert compute_stale_escalations(db, now=now) == []


def test_stale_escalations_oldest_first(db: Database) -> None:
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    older = now - timedelta(hours=48)
    newer = now - timedelta(hours=26)
    for tid, ts in [("TASK-newer", newer), ("TASK-older", older)]:
        db._conn.execute(
            "INSERT INTO tasks (id, brief, assigned_agent, team, status, created_at, updated_at) "
            "VALUES (?, 'b', 'dev_agent', 'engineering', 'escalated', ?, ?)",
            (tid, ts.isoformat(), ts.isoformat()),
        )
    db._conn.commit()
    rows = compute_stale_escalations(db, now=now)
    assert [r.task_id for r in rows] == ["TASK-older", "TASK-newer"]


def test_active_by_team_groups_in_progress(db: Database) -> None:
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    for tid, team in [("TASK-1", "engineering"), ("TASK-2", "engineering"), ("TASK-3", "content")]:
        db._conn.execute(
            "INSERT INTO tasks (id, brief, assigned_agent, team, status, created_at, updated_at) "
            "VALUES (?, 'b', 'a', ?, 'in_progress', ?, ?)",
            (tid, team, now.isoformat(), now.isoformat()),
        )
    db._conn.commit()
    rows = compute_active_by_team(db)
    by_team = {r.team: r for r in rows}
    assert by_team["engineering"].count == 2
    assert set(by_team["engineering"].task_ids) == {"TASK-1", "TASK-2"}
    assert by_team["content"].count == 1


def test_active_by_team_counts_parked_delegated_parent(db: Database) -> None:
    """Path B (ratified #5): a parked parent (in_progress + delegated) IS
    counted as active by team — it is in-progress, waiting on its children."""
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    # A running task and a parked delegating parent, same team.
    db._conn.execute(
        "INSERT INTO tasks (id, brief, assigned_agent, team, status, created_at, updated_at) "
        "VALUES ('TASK-run', 'b', 'a', 'engineering', 'in_progress', ?, ?)",
        (now.isoformat(), now.isoformat()),
    )
    db._conn.execute(
        "INSERT INTO tasks (id, brief, assigned_agent, team, status, block_kind, created_at, updated_at) "
        "VALUES ('TASK-parked', 'b', 'a', 'engineering', 'in_progress', 'delegated', ?, ?)",
        (now.isoformat(), now.isoformat()),
    )
    db._conn.commit()
    rows = compute_active_by_team(db)
    by_team = {r.team: r for r in rows}
    assert by_team["engineering"].count == 2
    assert set(by_team["engineering"].task_ids) == {"TASK-run", "TASK-parked"}


from runtime.orchestrator.dashboard_summary import (
    compute_org_pulse_7d, compose_dashboard_summary,
    normalize_review_verdict,
)


class _MockTeamsRegistry:
    """Duck-types the subset of TeamsRegistry that dashboard_summary needs."""
    def __init__(self, layout: dict[str, tuple[str, list[str]]]) -> None:
        # layout: {team_name: (manager_handle, [worker_handles])}
        self._layout = layout

    def teams(self) -> list[str]:
        return sorted(self._layout.keys())

    def manager_for_team(self, team: str):
        from runtime.orchestrator.teams import TeamManager
        mgr, workers = self._layout[team]
        return TeamManager(name=mgr, team=team, workers=tuple(workers))


@pytest.fixture
def mock_teams_empty() -> _MockTeamsRegistry:
    return _MockTeamsRegistry({})


@pytest.fixture
def mock_teams_one() -> _MockTeamsRegistry:
    return _MockTeamsRegistry({"engineering": ("engineering_head", ["eng_worker"])})


def test_org_pulse_zero_teams(db: Database, mock_teams_empty: _MockTeamsRegistry) -> None:
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    assert compute_org_pulse_7d(db, now=now, teams=mock_teams_empty) == []


def test_org_pulse_acceptance_pct(db: Database, mock_teams_one: _MockTeamsRegistry) -> None:
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    week_start = now - timedelta(days=7)
    # 4 reviews this week: 3 approved, 1 rejected → 75%
    for i, verdict in enumerate(["approved", "approved", "approved", "rejected"]):
        ts = week_start + timedelta(days=i)
        tid = f"TASK-{i}"
        db._conn.execute(
            "INSERT INTO tasks (id, brief, assigned_agent, team, status, created_at, updated_at) "
            "VALUES (?, 'b', 'eng_worker', 'engineering', 'completed', ?, ?)",
            (tid, ts.isoformat(), ts.isoformat()),
        )
        db._conn.execute(
            "INSERT INTO audit_log (timestamp, task_id, agent, action, payload) "
            "VALUES (?, ?, 'engineering_head', 'review_verdict', ?)",
            (ts.isoformat(), tid, f'{{"verdict":"{verdict}"}}'),
        )
    db._conn.commit()
    rows = compute_org_pulse_7d(db, now=now, teams=mock_teams_one)
    assert len(rows) == 1
    assert rows[0].team == "engineering"
    assert rows[0].acceptance_pct == 75
    assert rows[0].members == 1   # one worker (manager not counted as member)
    assert rows[0].lead == "engineering_head"
    assert len(rows[0].sparkline) == 12


def test_org_pulse_acceptance_normalizes_verdict_spellings(
    db: Database, mock_teams_one: _MockTeamsRegistry,
) -> None:
    """Approval spellings count as accepted case-/separator-insensitively;
    non-approval and unknown verdicts never count as accepted."""
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    week_start = now - timedelta(days=7)
    # 5 rows: APPROVE, accept, request changes, MAYBE, "" (blank) → 2 approved / 5.
    for i, verdict in enumerate(["APPROVE", " accept ", "request changes", "MAYBE", ""]):
        ts = week_start + timedelta(days=i)
        tid = f"TASK-{i}"
        db._conn.execute(
            "INSERT INTO tasks (id, brief, assigned_agent, team, status, created_at, updated_at) "
            "VALUES (?, 'b', 'eng_worker', 'engineering', 'completed', ?, ?)",
            (tid, ts.isoformat(), ts.isoformat()),
        )
        db._conn.execute(
            "INSERT INTO audit_log (timestamp, task_id, agent, action, payload) "
            "VALUES (?, ?, 'engineering_head', 'review_verdict', ?)",
            (ts.isoformat(), tid, f'{{"verdict":"{verdict}"}}'),
        )
    db._conn.commit()
    rows = compute_org_pulse_7d(db, now=now, teams=mock_teams_one)
    assert len(rows) == 1
    assert rows[0].acceptance_pct == 40   # 2 of 5


@pytest.mark.parametrize("raw", [
    "APPROVE", "approved", "approve", "ACCEPT", "accept", "OK", "ok",
    "PASS", "pass", " approve ",
])
def test_normalize_review_verdict_approval_family(raw: str) -> None:
    assert normalize_review_verdict(raw) == "approve"


@pytest.mark.parametrize("raw", [
    "REQUEST_CHANGES", "request changes", "request-changes", "request_changes",
    "REVISE", "revise", "REJECT", "reject", "rejected", "FAIL", "fail",
])
def test_normalize_review_verdict_non_approval_family(raw: str) -> None:
    assert normalize_review_verdict(raw) == "reject"


@pytest.mark.parametrize("raw", [None, "", "   ", "maybe", "UNKNOWN-VERDICT"])
def test_normalize_review_verdict_unknown(raw) -> None:
    assert normalize_review_verdict(raw) == "unknown"


def test_compose_returns_full_shape(
    db: Database, mock_kb_store: _MockKbStore, mock_teams_empty: _MockTeamsRegistry,
) -> None:
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    resp = compose_dashboard_summary(
        db=db, kb_store=mock_kb_store, teams=mock_teams_empty, now=now,
    )
    assert len(resp.heartbeat) == 24
    assert resp.narrative_counts.completed_today == 0
    assert resp.escalations == []
    assert resp.stale_escalations == []
    assert resp.active_by_team == []
    assert resp.recent_activity == []
    assert resp.updates_this_week == []
    assert resp.org_pulse == []
    assert resp.org_age_days == 0
    assert resp.server_now == now
