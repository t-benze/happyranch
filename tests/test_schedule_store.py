"""CRUD and query tests for ``ScheduleStore`` (THR-105 Phase 1).

Mirrors ``test_work_hours_store.py``: next-id allocation, insert/get/list
with filters, due listing, active counts, update with mutable-field guard,
JSON spawned_task_ids round-trip, and recover_firing.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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


def test_list_due_weekly_asia_shanghai_correctly_detected_in_utc(tmp_path):
    """Regression: THR-105 Phase 1 fix-forward — a weekly occurrence computed
    for Asia/Shanghai must be persisted as a UTC instant and found by list_due
    at the equivalent UTC time.

    Scenario: next_weekly_occurrence("Mon", "09:00", "Asia/Shanghai", after=…)
    returns 2026-07-20T01:00:00+00:00 (UTC).  That is the same instant as
    2026-07-20T09:00:00+08:00.  When querying at 2026-07-20T01:30:00+00:00
    (30 min later in UTC), list_due MUST return the schedule.

    Before the fix, the local-zone datetime was stored as raw TEXT
    (e.g. "2026-07-20T09:00:00+08:00") and TEXT comparison against
    "2026-07-20T01:30:00+00:00" failed — '2' vs '0' at position 18.
    """
    from runtime.orchestrator.schedule_rules import next_weekly_occurrence

    db = Database(tmp_path / "db.sqlite")

    # Compute next Mon 09:00 Asia/Shanghai after 2026-07-19T00:00Z.
    after = datetime(2026, 7, 19, 0, 0, 0, tzinfo=timezone.utc)
    fire_at = next_weekly_occurrence("Mon", "09:00", "Asia/Shanghai", after=after)
    assert fire_at is not None
    # This should be UTC: 2026-07-20T01:00:00+00:00
    assert fire_at.tzinfo == timezone.utc

    db.schedules.insert(_record(
        id="SCHEDULE-001",
        kind=ScheduleKind.WEEKLY,
        fire_at=fire_at,
        status=ScheduleStatus.ARMED,
        recurrence={"day": "Mon", "time": "09:00", "tz": "Asia/Shanghai"},
    ))

    # Query at 2026-07-20T01:30:00+00:00 — 30 min after the fire_at UTC instant.
    now = datetime(2026, 7, 20, 1, 30, 0, tzinfo=timezone.utc)
    due = db.schedules.list_due(now)
    assert [r.id for r in due] == ["SCHEDULE-001"], (
        f"weekly Asia/Shanghai schedule not found by list_due at UTC {now.isoformat()}"
    )

    # Also verify: querying just BEFORE fire_at (e.g. 1 min before) should NOT return it.
    before_now = datetime(2026, 7, 20, 0, 59, 0, tzinfo=timezone.utc)
    due_before = db.schedules.list_due(before_now)
    assert due_before == [], "list_due should not return schedule before fire_at"

    # And: direct insert of a non-UTC fire_at should be normalized at the
    # store boundary, so list_due still finds it.
    local_fire = datetime(2026, 7, 20, 9, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert local_fire.utcoffset() == timedelta(hours=8)
    db.schedules.insert(_record(
        id="SCHEDULE-002",
        kind=ScheduleKind.WEEKLY,
        fire_at=local_fire,
        status=ScheduleStatus.ARMED,
        agent_name="qa_engineer",
        recurrence={"day": "Mon", "time": "09:00", "tz": "Asia/Shanghai"},
    ))
    due2 = db.schedules.list_due(now)
    assert "SCHEDULE-002" in [r.id for r in due2], (
        "directly-inserted local-zone fire_at not normalized to UTC"
    )


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
