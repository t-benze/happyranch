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
