"""Schema-level checks for the additive ``schedules`` table (THR-105 Phase 1).

Mirrors ``test_database_work_hours.py``: table exists, columns match spec with
correct defaults/NOT-NULL, indexes present, and additive-schema safety (no
existing table/column is altered).
"""
from __future__ import annotations

import sqlite3

import pytest

from runtime.infrastructure.database import Database


# (name, notnull, default) for every column the THR-105 design mandates.
_EXPECTED_COLUMNS = {
    # SQLite reports notnull=0 for TEXT PRIMARY KEY (the PK constraint
    # already enforces uniqueness and non-null in its own way).
    "id": (0, None),
    "agent_name": (1, None),
    "team": (1, "'engineering'"),
    "kind": (1, None),
    "fire_at": (1, None),
    "recurrence": (0, None),
    "timezone": (1, "'UTC'"),
    "normalized_brief": (1, None),
    "source_instruction": (1, None),
    "status": (1, "'armed'"),
    "active": (1, "1"),
    "expires_at": (0, None),
    "indefinite": (1, "0"),
    "spawned_task_ids": (0, None),
    "last_fired_at": (0, None),
    "fire_count": (1, "0"),
    "session_id": (0, None),
    "error": (0, None),
    "transcript_path": (0, None),
    "created_at": (1, None),
    "updated_at": (1, None),
}


def _conn(tmp_path) -> sqlite3.Connection:
    db = Database(tmp_path / "db.sqlite")
    conn = sqlite3.connect(str(db.path))
    conn.row_factory = sqlite3.Row
    return conn


def test_schedules_table_exists(tmp_path):
    conn = _conn(tmp_path)
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schedules'"
    ).fetchone()
    assert row is not None and row["name"] == "schedules"


def test_schedules_columns_match_spec(tmp_path):
    conn = _conn(tmp_path)
    info = conn.execute("PRAGMA table_info(schedules)").fetchall()
    actual = {r["name"]: (r["notnull"], r["dflt_value"]) for r in info}
    assert set(actual) == set(_EXPECTED_COLUMNS), (
        f"extra={set(actual) - set(_EXPECTED_COLUMNS)} "
        f"missing={set(_EXPECTED_COLUMNS) - set(actual)}"
    )
    for name, expected in _EXPECTED_COLUMNS.items():
        assert actual[name] == expected, f"column {name}: {actual[name]} != {expected}"

    pk = [r["name"] for r in info if r["pk"] == 1]
    assert pk == ["id"]


def test_schedules_indexes_present(tmp_path):
    conn = _conn(tmp_path)
    names = {
        r["name"]
        for r in conn.execute("PRAGMA index_list(schedules)").fetchall()
    }
    assert "idx_schedules_agent_status" in names
    assert "idx_schedules_status_fire_at" in names


def test_additive_schema_does_not_alter_existing_tables(tmp_path):
    """Verify the work_hours table still has its expected columns after
    the schedules table is added — no column dropped or altered."""
    conn = _conn(tmp_path)
    wh_info = conn.execute("PRAGMA table_info(work_hours)").fetchall()
    wh_names = {r["name"] for r in wh_info}
    for col in ("id", "agent_name", "local_date", "slot", "mode", "scheduled_for",
                "spawned_task_ids", "status", "created_at"):
        assert col in wh_names, f"work_hours missing column {col}"
