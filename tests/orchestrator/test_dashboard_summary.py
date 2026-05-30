"""Unit tests for dashboard_summary aggregations.

Each test seeds an in-memory SQLite via the standard Database fixture and
exercises one aggregation function with a deterministic `now` clock.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.infrastructure.database import Database
from src.orchestrator.dashboard_summary import (
    compute_org_age_days,
    compute_spend_today,
)


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
