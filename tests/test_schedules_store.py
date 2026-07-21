"""Tests for the ``schedules`` persistence layer (store + schema).

Covers: id allocation, insert/get/list/update round-trip, idempotent
table/index init, and status transitions (armed ↔ paused/cancelled/expired).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from runtime.infrastructure.database import Database
from runtime.models import ScheduleKind, ScheduleRecord, ScheduleStatus


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, 21, hour, minute, tzinfo=timezone.utc)


def _record(**overrides) -> ScheduleRecord:
    base = dict(
        id="SCHEDULE-001",
        agent_name="dev_agent",
        team="engineering",
        kind=ScheduleKind.ONE_SHOT.value,
        fire_at=_dt(9).isoformat(),
        recurrence=None,
        timezone="Asia/Shanghai",
        normalized_brief="Send the update.",
        source_instruction="Send update at 9am.",
        status=ScheduleStatus.ARMED.value,
        active=1,
        expires_at=None,
        indefinite=0,
        spawned_task_ids=[],
        last_fired_at=None,
        fire_count=0,
    )
    base.update(overrides)
    return ScheduleRecord(**base)


# --------------- schema ---------------

def test_table_exists_and_columns_are_idempotent(tmp_path):
    """Creating the Database twice should not fail (idempotent CREATE TABLE IF NOT EXISTS)."""
    db1 = Database(tmp_path / "db.sqlite")
    row = db1._conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schedules'").fetchone()
    assert row is not None
    db2 = Database(tmp_path / "db.sqlite")
    row2 = db2._conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schedules'").fetchone()
    assert row2 is not None


def test_schedules_table_columns(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    columns = {
        row["name"]: row["type"]
        for row in db._conn.execute("PRAGMA table_info(schedules)").fetchall()
    }
    assert columns["id"] == "TEXT"
    assert columns["agent_name"] == "TEXT"
    assert columns["kind"] == "TEXT"
    assert columns["fire_at"] == "TEXT"
    assert columns["status"] == "TEXT"
    assert columns["active"] == "INTEGER"
    assert columns["source_instruction"] == "TEXT"
    assert columns["normalized_brief"] == "TEXT"
    assert columns["spawned_task_ids"] == "TEXT"


def test_schedules_indexes_exist(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    indexes = {
        row["name"]
        for row in db._conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='schedules'").fetchall()
    }
    assert "idx_schedules_agent_status" in indexes
    assert "idx_schedules_fire_at" in indexes
    assert "idx_schedules_status" in indexes


# --------------- id allocation ---------------

def test_next_id_starts_at_001(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    assert db.schedules.next_id() == "SCHEDULE-001"


def test_next_id_increments(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.schedules.insert(_record(id="SCHEDULE-001"))
    db.schedules.insert(_record(id="SCHEDULE-002", agent_name="qa_engineer"))
    assert db.schedules.next_id() == "SCHEDULE-003"


def test_next_id_skips_non_numeric(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    # Insert a row with a non-standard id
    db.schedules.insert(_record(id="SCHEDULE-ABC"))
    # Should still return 001 because ABC has no numeric part
    assert db.schedules.next_id() == "SCHEDULE-001"


def test_next_id_handles_zero_rows(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    assert db.schedules.next_id() == "SCHEDULE-001"


# --------------- insert / get round-trip ---------------

def test_insert_and_get_round_trip(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.schedules.insert(_record())

    got = db.schedules.get("SCHEDULE-001")
    assert got is not None
    assert got.agent_name == "dev_agent"
    assert got.team == "engineering"
    assert got.kind == ScheduleKind.ONE_SHOT
    assert got.fire_at == _dt(9)
    assert got.timezone == "Asia/Shanghai"
    assert got.status == ScheduleStatus.ARMED
    assert got.active == 1
    assert got.source_instruction == "Send update at 9am."
    assert got.normalized_brief == "Send the update."
    assert got.spawned_task_ids == []
    assert got.fire_count == 0
    assert got.last_fired_at is None
    assert got.created_at is not None


def test_get_missing_returns_none(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    assert db.schedules.get("SCHEDULE-999") is None


# --------------- inserts ---------------

def test_insert_weekly_with_recurrence(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.schedules.insert(_record(
        id="SCHEDULE-001",
        kind=ScheduleKind.WEEKLY.value,
        recurrence={"day": "Sat", "time": "09:00", "tz": "Asia/Shanghai"},
        indefinite=1,
        expires_at=None,
    ))
    got = db.schedules.get("SCHEDULE-001")
    assert got.kind == ScheduleKind.WEEKLY
    assert got.recurrence == {"day": "Sat", "time": "09:00", "tz": "Asia/Shanghai"}
    assert got.indefinite == 1


# --------------- list ---------------

def test_list_orders_by_fire_at_desc(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.schedules.insert(_record(id="SCHEDULE-001", fire_at=_dt(9).isoformat()))
    db.schedules.insert(_record(id="SCHEDULE-002", agent_name="qa_engineer",
                                fire_at=_dt(14).isoformat()))
    results = db.schedules.list()
    assert [r.id for r in results] == ["SCHEDULE-002", "SCHEDULE-001"]


def test_list_filter_by_agent(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.schedules.insert(_record(id="SCHEDULE-001"))
    db.schedules.insert(_record(id="SCHEDULE-002", agent_name="qa_engineer"))
    results = db.schedules.list(agent="dev_agent")
    assert [r.id for r in results] == ["SCHEDULE-001"]


def test_list_limit_respects_bounds(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    for i in range(5):
        db.schedules.insert(_record(id=f"SCHEDULE-{i+1:03d}",
                                     fire_at=_dt(i).isoformat()))
    assert len(db.schedules.list(limit=1)) == 1
    assert len(db.schedules.list(limit=500)) == 5


# --------------- update ---------------

def test_update_status_and_fire_count(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.schedules.insert(_record())

    db.schedules.update("SCHEDULE-001", status=ScheduleStatus.FIRED.value, fire_count=1,
                        last_fired_at=_dt(9, 30).isoformat())
    got = db.schedules.get("SCHEDULE-001")
    assert got.status == ScheduleStatus.FIRED
    assert got.fire_count == 1
    assert got.last_fired_at is not None


def test_update_spawned_task_ids(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.schedules.insert(_record())

    db.schedules.update("SCHEDULE-001", spawned_task_ids=["TASK-999"])
    got = db.schedules.get("SCHEDULE-001")
    assert got.spawned_task_ids == ["TASK-999"]


def test_update_pause_and_resume(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.schedules.insert(_record())

    db.schedules.update("SCHEDULE-001", status=ScheduleStatus.PAUSED.value, active=0)
    got = db.schedules.get("SCHEDULE-001")
    assert got.status == ScheduleStatus.PAUSED
    assert got.active == 0

    db.schedules.update("SCHEDULE-001", status=ScheduleStatus.ARMED.value, active=1)
    got = db.schedules.get("SCHEDULE-001")
    assert got.status == ScheduleStatus.ARMED
    assert got.active == 1


def test_update_rejects_unknown_field(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.schedules.insert(_record())
    with pytest.raises(ValueError, match="unsupported schedule fields"):
        db.schedules.update("SCHEDULE-001", not_a_field="value")


# --------------- armed count helpers ---------------

def test_count_armed_by_agent(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.schedules.insert(_record(id="SCHEDULE-001"))
    db.schedules.insert(_record(id="SCHEDULE-002"))
    db.schedules.insert(_record(id="SCHEDULE-003", agent_name="qa_engineer"))
    assert db.schedules.count_armed(agent="dev_agent") == 2
    assert db.schedules.count_armed(agent="qa_engineer") == 1
    assert db.schedules.count_armed(agent="nonexistent") == 0


def test_count_armed_org_wide(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.schedules.insert(_record(id="SCHEDULE-001"))
    db.schedules.insert(_record(id="SCHEDULE-002", agent_name="qa_engineer"))
    assert db.schedules.count_armed() == 2


def test_count_armed_excludes_non_armed(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.schedules.insert(_record(id="SCHEDULE-001", status=ScheduleStatus.ARMED.value))
    db.schedules.insert(_record(id="SCHEDULE-002", status=ScheduleStatus.PAUSED.value))
    db.schedules.insert(_record(id="SCHEDULE-003", status=ScheduleStatus.CANCELLED.value))
    assert db.schedules.count_armed() == 1
