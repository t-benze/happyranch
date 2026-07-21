"""CRUD and query tests for ``ScheduleStore`` (THR-105 Phase 1).

Mirrors ``test_work_hours_store.py``: next-id allocation, insert/get/list
with filters, due listing, active counts, update with mutable-field guard,
JSON spawned_task_ids round-trip, and recover_firing.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from runtime.infrastructure.database import Database
from runtime.models import ScheduleKind, ScheduleRecord, ScheduleStatus


def _dt(hour: int = 0, minute: int = 0, day: int = 21, month: int = 7) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=timezone.utc)


def _record(**overrides) -> ScheduleRecord:
    base: dict = dict(
        id="SCHEDULE-001",
        agent_name="dev_agent",
        team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_dt(day=28, hour=9),
        timezone="Asia/Shanghai",
        normalized_brief="Send weekly report",
        source_instruction="Every Monday send the status report",
    )
    base.update(overrides)
    return ScheduleRecord(**base)


# -------------------------------------------------------------------- next_id


def test_next_id_starts_at_001(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    assert db.schedules.next_id() == "SCHEDULE-001"
    assert db.schedules.next_id() == "SCHEDULE-001"  # idempotent before insert


def test_next_id_increments(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.schedules.insert(_record(id="SCHEDULE-001"))
    db.schedules.insert(_record(id="SCHEDULE-002", agent_name="qa_engineer"))
    assert db.schedules.next_id() == "SCHEDULE-003"


# ---------------------------------------------------------- insert / get / list


def test_insert_and_get_round_trip(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.schedules.insert(_record())

    got = db.schedules.get("SCHEDULE-001")
    assert got is not None
    assert got.agent_name == "dev_agent"
    assert got.team == "engineering"
    assert got.kind == ScheduleKind.ONE_SHOT
    assert got.fire_at == _dt(day=28, hour=9)
    assert got.status == ScheduleStatus.ARMED
    assert got.active == 1
    assert got.spawned_task_ids == []
    assert got.fire_count == 0
    assert got.indefinite == 0
    assert got.created_at is not None
    assert got.updated_at is not None


def test_get_missing_returns_none(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    assert db.schedules.get("SCHEDULE-999") is None


def test_list_all_newest_first(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.schedules.insert(_record(id="SCHEDULE-001", agent_name="dev_agent"))
    db.schedules.insert(_record(id="SCHEDULE-002", agent_name="qa_engineer"))
    ids = [r.id for r in db.schedules.list()]
    assert ids == ["SCHEDULE-002", "SCHEDULE-001"]


def test_list_filter_by_agent(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.schedules.insert(_record(id="SCHEDULE-001", agent_name="dev_agent"))
    db.schedules.insert(_record(id="SCHEDULE-002", agent_name="qa_engineer"))
    assert [r.id for r in db.schedules.list(agent="dev_agent")] == ["SCHEDULE-001"]


def test_list_filter_by_status(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.schedules.insert(_record(id="SCHEDULE-001", status=ScheduleStatus.ARMED))
    db.schedules.insert(_record(id="SCHEDULE-002", status=ScheduleStatus.PAUSED,
                                agent_name="qa_engineer"))
    armed = [r.id for r in db.schedules.list(status=ScheduleStatus.ARMED)]
    assert armed == ["SCHEDULE-001"]


# ---------------------------------------------------------------- list_due


def test_list_due_returns_armed_past_fire_at(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    past = _dt(day=21, hour=1)
    future = _dt(day=28, hour=9)

    db.schedules.insert(_record(id="SCHEDULE-001", fire_at=past))
    db.schedules.insert(_record(id="SCHEDULE-002", fire_at=future))
    db.schedules.insert(_record(id="SCHEDULE-003", fire_at=past,
                                status=ScheduleStatus.PAUSED,
                                agent_name="qa_engineer"))

    due = db.schedules.list_due(_dt(day=21, hour=12))
    assert [r.id for r in due] == ["SCHEDULE-001"]


def test_list_due_excludes_future(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.schedules.insert(_record(id="SCHEDULE-001", fire_at=_dt(day=28, hour=9)))
    due = db.schedules.list_due(_dt(day=21, hour=12))
    assert due == []


# --------------------------------------------------------- active counts


def test_active_count_for_agent(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.schedules.insert(_record(id="SCHEDULE-001"))
    db.schedules.insert(_record(id="SCHEDULE-002",
                                status=ScheduleStatus.PAUSED))
    assert db.schedules.active_count_for_agent("dev_agent") == 1


def test_active_count_org(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.schedules.insert(_record(id="SCHEDULE-001"))
    db.schedules.insert(_record(id="SCHEDULE-002",
                                agent_name="qa_engineer"))
    db.schedules.insert(_record(id="SCHEDULE-003",
                                status=ScheduleStatus.CANCELLED,
                                agent_name="support_agent"))
    assert db.schedules.active_count_org() == 2


# --------------------------------------------------------------- update


def test_update_lifecycle_fields(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.schedules.insert(_record())

    db.schedules.update("SCHEDULE-001",
                        status=ScheduleStatus.FIRING,
                        fire_at=_dt(day=28, hour=10))
    got = db.schedules.get("SCHEDULE-001")
    assert got.status == ScheduleStatus.FIRING
    assert got.fire_at == _dt(day=28, hour=10)
    assert got.updated_at > got.created_at


def test_update_spawned_task_ids_round_trip(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.schedules.insert(_record())

    db.schedules.update("SCHEDULE-001",
                        spawned_task_ids=["TASK-400", "TASK-401"],
                        fire_count=2,
                        last_fired_at=_dt(day=28, hour=9, minute=1))
    got = db.schedules.get("SCHEDULE-001")
    assert got.spawned_task_ids == ["TASK-400", "TASK-401"]
    assert got.fire_count == 2
    assert got.last_fired_at is not None


def test_update_rejects_immutable_identity_fields(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.schedules.insert(_record())
    with pytest.raises(ValueError, match="unsupported schedule fields"):
        db.schedules.update("SCHEDULE-001", agent_name="other")
    with pytest.raises(ValueError, match="unsupported schedule fields"):
        db.schedules.update("SCHEDULE-001", kind=ScheduleKind.WEEKLY)
    with pytest.raises(ValueError, match="unsupported schedule fields"):
        db.schedules.update("SCHEDULE-001", source_instruction="changed")


def test_update_rejects_unknown_field(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.schedules.insert(_record())
    with pytest.raises(ValueError, match="unsupported schedule fields"):
        db.schedules.update("SCHEDULE-001", nonsense=42)


def test_update_with_recurrence_json(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    rec = {"day": "Mon", "time": "09:00", "tz": "Asia/Shanghai"}
    db.schedules.insert(_record(kind=ScheduleKind.WEEKLY, recurrence=rec,
                                expires_at=_dt(day=28, hour=9, minute=0,
                                               month=10)))

    db.schedules.update("SCHEDULE-001",
                        recurrence={"day": "Tue", "time": "10:00",
                                     "tz": "Asia/Shanghai"})
    got = db.schedules.get("SCHEDULE-001")
    assert got.recurrence == {"day": "Tue", "time": "10:00", "tz": "Asia/Shanghai"}


# ------------------------------------------------------------- recover_firing


def test_recover_firing_marks_failed(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.schedules.insert(_record(id="SCHEDULE-001",
                                status=ScheduleStatus.FIRING))
    db.schedules.insert(_record(id="SCHEDULE-002",
                                status=ScheduleStatus.ARMED,
                                agent_name="qa_engineer"))
    db.schedules.insert(_record(id="SCHEDULE-003",
                                status=ScheduleStatus.FIRED,
                                agent_name="support_agent"))

    assert db.schedules.recover_firing() == 1
    recovered = db.schedules.get("SCHEDULE-001")
    assert recovered.status == ScheduleStatus.FAILED
    assert recovered.error == "daemon_restart"
    assert db.schedules.get("SCHEDULE-002").status == ScheduleStatus.ARMED
    assert db.schedules.get("SCHEDULE-003").status == ScheduleStatus.FIRED
