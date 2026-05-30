"""Unit tests for dashboard_summary aggregations.

Each test seeds an in-memory SQLite via the standard Database fixture and
exercises one aggregation function with a deterministic `now` clock.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.infrastructure.database import Database
from src.orchestrator.dashboard_summary import compute_org_age_days


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
