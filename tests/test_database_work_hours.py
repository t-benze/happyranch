"""Schema-level checks for the additive ``work_hours`` table.

Mirrors the intent of ``test_database_dreams.py`` but at the DDL level: the
table exists, has exactly the columns/defaults/NOT-NULLs the working-hours
spec mandates, carries both named indexes, and enforces the
``UNIQUE(agent_name, local_date, slot)`` scheduling-identity guard. CRUD
behaviour is covered separately in ``test_work_hours_store.py``.

The checks open a second read connection to the same sqlite file via the
public ``Database.path`` so they stay independent of the store wiring.
"""
from __future__ import annotations

import sqlite3

import pytest

from runtime.infrastructure.database import Database


# (name, notnull, default) for every column the spec's Data Model defines.
_EXPECTED_COLUMNS = {
    "id": (0, None),
    "agent_name": (1, None),
    "local_date": (1, None),
    "slot": (1, None),
    "mode": (1, None),
    "scheduled_for": (1, None),
    "started_at": (0, None),
    "ended_at": (0, None),
    "status": (1, "'pending'"),
    "routine_count": (1, "0"),
    "dropped_count": (1, "0"),
    "spawned_task_ids": (0, None),
    "spawned_task_count": (1, "0"),
    "summary": (0, None),
    "transcript_path": (0, None),
    "session_id": (0, None),
    "error": (0, None),
    "created_at": (1, None),
}


def _conn(tmp_path) -> sqlite3.Connection:
    db = Database(tmp_path / "db.sqlite")
    conn = sqlite3.connect(str(db.path))
    conn.row_factory = sqlite3.Row
    return conn


def test_work_hours_table_exists(tmp_path):
    conn = _conn(tmp_path)
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='work_hours'"
    ).fetchone()
    assert row is not None and row["name"] == "work_hours"


def test_work_hours_columns_match_spec(tmp_path):
    conn = _conn(tmp_path)
    info = conn.execute("PRAGMA table_info(work_hours)").fetchall()
    actual = {r["name"]: (r["notnull"], r["dflt_value"]) for r in info}
    assert set(actual) == set(_EXPECTED_COLUMNS)
    for name, expected in _EXPECTED_COLUMNS.items():
        assert actual[name] == expected, name

    # id is the single-column primary key.
    pk = [r["name"] for r in info if r["pk"] == 1]
    assert pk == ["id"]


def test_work_hours_indexes_present(tmp_path):
    conn = _conn(tmp_path)
    names = {
        r["name"]
        for r in conn.execute("PRAGMA index_list(work_hours)").fetchall()
    }
    assert "idx_work_hours_agent_date" in names
    assert "idx_work_hours_status" in names


def _insert(conn, *, work_id, agent="dev_agent", local_date="2026-06-11", slot="09:00"):
    conn.execute(
        "INSERT INTO work_hours (id, agent_name, local_date, slot, mode, "
        "scheduled_for, created_at) VALUES (?, ?, ?, ?, 'windowed', ?, ?)",
        (work_id, agent, local_date, slot, "2026-06-11T09:00:00+00:00",
         "2026-06-11T08:59:00+00:00"),
    )
    conn.commit()


def test_unique_agent_date_slot_enforced(tmp_path):
    conn = _conn(tmp_path)
    _insert(conn, work_id="WORKHOUR-001", slot="09:00")

    # Same (agent_name, local_date, slot) triple is rejected.
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, work_id="WORKHOUR-002", slot="09:00")

    # A different slot on the same day is allowed (many wakes per day).
    _insert(conn, work_id="WORKHOUR-003", slot="11:00")
    count = conn.execute("SELECT COUNT(*) AS n FROM work_hours").fetchone()["n"]
    assert count == 2
