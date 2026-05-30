"""Unit tests for dashboard_summary aggregations.

Each test seeds an in-memory SQLite via the standard Database fixture and
exercises one aggregation function with a deterministic `now` clock.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.infrastructure.database import Database
from src.orchestrator.dashboard_summary import (
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
    # 1 escalated (status='blocked' + block_kind='escalated')
    db._conn.execute(
        "INSERT INTO tasks (id, brief, assigned_agent, team, status, block_kind, created_at, updated_at) "
        "VALUES ('TASK-4', 'b', 'a', 't', 'blocked', 'escalated', ?, ?)",
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


from src.orchestrator.dashboard_summary import compute_heartbeat_24h


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
    # 9 completion_reports with status=completed, 1 with status=failed → 10% fail = warn
    ts = now.replace(hour=11, minute=0, second=0, microsecond=0)
    for _ in range(9):
        db._conn.execute(
            "INSERT INTO audit_log (timestamp, task_id, agent, action, payload) "
            'VALUES (?, \'T\', \'a\', \'completion_report\', \'{"status":"completed"}\')',
            (ts.isoformat(),),
        )
    db._conn.execute(
        "INSERT INTO audit_log (timestamp, task_id, agent, action, payload) "
        'VALUES (?, \'T\', \'a\', \'completion_report\', \'{"status":"failed"}\')',
        (ts.isoformat(),),
    )
    db._conn.commit()
    buckets = compute_heartbeat_24h(db, now=now)
    bucket_11 = next(b for b in buckets if b.hour == 11)
    assert bucket_11.steps == 10
    assert bucket_11.tier == "warn"


from src.orchestrator.dashboard_summary import compute_recent_activity


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


from src.orchestrator.dashboard_summary import compute_updates_this_week


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
