"""Unit tests for ``next_wake_slots`` — the forward next-N-wake computation
that powers the Work-Hours Config UI's "Next wakes" panel (THR-035 / TASK-967).

The function is the forward dual of ``current_due_slot``: it reuses the same
slot grids (``windowed_slot_minutes`` / ``continuous_slot_minutes``) and returns
the next ``count`` wake datetimes strictly after ``now`` in the schedule's
effective timezone.
"""
from __future__ import annotations

from datetime import datetime, timezone

from runtime.daemon.work_hours_scheduler import next_wake_slots
from runtime.orchestrator.org_config import WorkHoursSchedule


def _utc(y, mo, d, h, mi) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def test_continuous_next_wakes_step_by_interval() -> None:
    sched = WorkHoursSchedule(
        mode="continuous", interval="2h", timezone="UTC", catch_up_on_startup=True,
    )
    # now = 2026-06-27 09:30 UTC -> next 2h-grid slots are 10:00, 12:00, 14:00
    out = next_wake_slots(sched, _utc(2026, 6, 27, 9, 30), 3)
    assert [d.isoformat() for d in out] == [
        "2026-06-27T10:00:00+00:00",
        "2026-06-27T12:00:00+00:00",
        "2026-06-27T14:00:00+00:00",
    ]


def test_continuous_rolls_into_next_day() -> None:
    sched = WorkHoursSchedule(
        mode="continuous", interval="12h", timezone="UTC", catch_up_on_startup=True,
    )
    # now = 13:00 -> remaining slot today is none after... grid is 00:00, 12:00.
    # next strictly-after-13:00 slots: tomorrow 00:00, 12:00, day-after 00:00
    out = next_wake_slots(sched, _utc(2026, 6, 27, 13, 0), 3)
    assert [d.isoformat() for d in out] == [
        "2026-06-28T00:00:00+00:00",
        "2026-06-28T12:00:00+00:00",
        "2026-06-29T00:00:00+00:00",
    ]


def test_windowed_only_returns_configured_days() -> None:
    # 2026-06-27 is a Saturday. days=mon-fri so the next wake is Monday 06-29.
    sched = WorkHoursSchedule(
        mode="windowed", interval="2h", timezone="UTC", catch_up_on_startup=True,
        window_start="09:00", window_end="17:00",
        days=("mon", "tue", "wed", "thu", "fri"),
    )
    out = next_wake_slots(sched, _utc(2026, 6, 27, 10, 0), 2)
    assert [d.isoformat() for d in out] == [
        "2026-06-29T09:00:00+00:00",
        "2026-06-29T11:00:00+00:00",
    ]


def test_windowed_same_day_after_now() -> None:
    # 2026-06-29 is a Monday. now 10:30 -> next slots 11:00, 13:00 (grid anchored
    # at 09:00 stepped 2h up to/including 17:00).
    sched = WorkHoursSchedule(
        mode="windowed", interval="2h", timezone="UTC", catch_up_on_startup=True,
        window_start="09:00", window_end="17:00",
        days=("mon", "tue", "wed", "thu", "fri"),
    )
    out = next_wake_slots(sched, _utc(2026, 6, 29, 10, 30), 2)
    assert [d.isoformat() for d in out] == [
        "2026-06-29T11:00:00+00:00",
        "2026-06-29T13:00:00+00:00",
    ]


def test_timezone_is_honored() -> None:
    # continuous 24h interval -> one slot/day at local 00:00 America/Los_Angeles.
    sched = WorkHoursSchedule(
        mode="continuous", interval="24h", timezone="America/Los_Angeles",
        catch_up_on_startup=True,
    )
    out = next_wake_slots(sched, _utc(2026, 6, 27, 12, 0), 1)
    # local 00:00 PDT == 07:00 UTC; the next one strictly after 12:00 UTC (= 05:00
    # local) is 2026-06-28 00:00 PDT.
    assert out[0].isoformat() == "2026-06-28T00:00:00-07:00"


def test_count_zero_or_negative_returns_empty() -> None:
    sched = WorkHoursSchedule(
        mode="continuous", interval="1h", timezone="UTC", catch_up_on_startup=True,
    )
    assert next_wake_slots(sched, _utc(2026, 6, 27, 9, 0), 0) == []
