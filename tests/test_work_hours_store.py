from __future__ import annotations

from datetime import datetime, timezone

import pytest

from runtime.infrastructure.database import Database
from runtime.models import WorkHourMode, WorkHourRecord, WorkHourStatus


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, 11, hour, minute, tzinfo=timezone.utc)


def _record(**overrides) -> WorkHourRecord:
    base = dict(
        id="WORKHOUR-001",
        agent_name="dev_agent",
        local_date="2026-06-11",
        slot="09:00",
        mode=WorkHourMode.WINDOWED,
        scheduled_for=_dt(9),
    )
    base.update(overrides)
    return WorkHourRecord(**base)


def test_next_id_and_insert_round_trip(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    assert db.work_hours.next_id() == "WORKHOUR-001"

    db.work_hours.insert(_record())

    got = db.work_hours.get("WORKHOUR-001")
    assert got is not None
    assert got.agent_name == "dev_agent"
    assert got.slot == "09:00"
    assert got.mode == WorkHourMode.WINDOWED
    assert got.status == WorkHourStatus.PENDING
    assert got.spawned_task_ids == []
    assert got.spawned_task_count == 0
    assert db.work_hours.next_id() == "WORKHOUR-002"


def test_unique_per_agent_date_slot_blocks_dupes_but_allows_other_slots(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.work_hours.insert(_record(id="WORKHOUR-001", slot="09:00"))

    # Same (agent, local_date, slot) triple -> blocked.
    with pytest.raises(Exception):
        db.work_hours.insert(_record(id="WORKHOUR-002", slot="09:00"))

    # Different slot on the same day -> allowed (many wakes per day).
    db.work_hours.insert(_record(id="WORKHOUR-003", slot="11:00", scheduled_for=_dt(11)))
    assert db.work_hours.get_for_agent_date_slot("dev_agent", "2026-06-11", "11:00") is not None


def test_get_for_agent_date_slot_misses_return_none(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.work_hours.insert(_record())
    assert db.work_hours.get_for_agent_date_slot("dev_agent", "2026-06-11", "13:00") is None
    assert db.work_hours.get_for_agent_date_slot("other", "2026-06-11", "09:00") is None


def test_list_filters_and_newest_first(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.work_hours.insert(_record(id="WORKHOUR-001", slot="09:00", scheduled_for=_dt(9)))
    db.work_hours.insert(_record(id="WORKHOUR-002", agent_name="qa_engineer",
                                 slot="11:00", scheduled_for=_dt(11)))

    assert [r.id for r in db.work_hours.list()] == ["WORKHOUR-002", "WORKHOUR-001"]
    assert [r.id for r in db.work_hours.list(agent="dev_agent")] == ["WORKHOUR-001"]


def test_update_status_transitions_and_spawned_ids_round_trip(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.work_hours.insert(_record())

    db.work_hours.update("WORKHOUR-001", status=WorkHourStatus.RUNNING, started_at=_dt(9))
    assert db.work_hours.get("WORKHOUR-001").status == WorkHourStatus.RUNNING

    db.work_hours.update(
        "WORKHOUR-001",
        status=WorkHourStatus.COMPLETED,
        ended_at=_dt(9, 5),
        spawned_task_ids=["TASK-201", "TASK-202"],
        spawned_task_count=2,
        summary="Launched 2 routine tasks.",
    )
    got = db.work_hours.get("WORKHOUR-001")
    assert got.status == WorkHourStatus.COMPLETED
    assert got.spawned_task_ids == ["TASK-201", "TASK-202"]
    assert got.spawned_task_count == 2


def test_update_rejects_unknown_field(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.work_hours.insert(_record())
    with pytest.raises(ValueError, match="unsupported work_hour fields"):
        db.work_hours.update("WORKHOUR-001", slot="10:00")


def test_recover_running_marks_failed_daemon_restart(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.work_hours.insert(_record(id="WORKHOUR-001", slot="09:00",
                                 status=WorkHourStatus.RUNNING))
    db.work_hours.insert(_record(id="WORKHOUR-002", slot="11:00", scheduled_for=_dt(11),
                                 status=WorkHourStatus.COMPLETED))

    assert db.work_hours.recover_running() == 1
    recovered = db.work_hours.get("WORKHOUR-001")
    assert recovered.status == WorkHourStatus.FAILED
    assert recovered.error == "daemon_restart"
    assert recovered.ended_at is not None
    # Already-terminal rows are untouched.
    assert db.work_hours.get("WORKHOUR-002").status == WorkHourStatus.COMPLETED
