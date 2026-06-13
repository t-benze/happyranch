from __future__ import annotations

from datetime import datetime, timezone

import pytest

from runtime.daemon.work_hours_scheduler import (
    continuous_slot_minutes,
    current_due_slot,
    decide_wake,
    select_work_hours_agents,
    windowed_slot_minutes,
)
from runtime.orchestrator.org_config import (
    OrgConfigError,
    WorkHoursSchedule,
    WorkingHoursConfig,
    WorkHoursScheduleLayer,
)


def _windowed(*, interval="2h", catch_up=True, tz="UTC") -> WorkHoursSchedule:
    return WorkHoursSchedule(
        mode="windowed", interval=interval, timezone=tz,
        catch_up_on_startup=catch_up, window_start="09:00", window_end="18:00",
        days=("mon", "tue", "wed", "thu", "fri"),
    )


def _continuous(*, interval="15m", catch_up=True, tz="UTC") -> WorkHoursSchedule:
    return WorkHoursSchedule(
        mode="continuous", interval=interval, timezone=tz, catch_up_on_startup=catch_up,
    )


def _utc(y, mo, d, h, mi) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


# --- agent selection ---

def _cfg(**kw) -> WorkingHoursConfig:
    base = dict(enabled=True, agent_mode="all", include_agents=[], exclude_agents=[])
    base.update(kw)
    return WorkingHoursConfig(**base)


def test_select_all_with_exclude() -> None:
    cfg = _cfg(exclude_agents=["qa_engineer"])
    assert select_work_hours_agents(
        ["dev_agent", "qa_engineer", "ops_manager"], cfg, {"engineering"}
    ) == ["dev_agent", "ops_manager"]


def test_select_whitelist_then_exclude() -> None:
    cfg = _cfg(agent_mode="whitelist", include_agents=["qa_engineer", "dev_agent"],
               exclude_agents=["qa_engineer"])
    assert select_work_hours_agents(
        ["dev_agent", "qa_engineer", "ops_manager"], cfg, set()
    ) == ["dev_agent"]


def test_select_disabled() -> None:
    assert select_work_hours_agents(["dev_agent"], _cfg(enabled=False), set()) == []


def test_select_unknown_include_raises() -> None:
    cfg = _cfg(agent_mode="whitelist", include_agents=["dev_agent", "no_such"])
    with pytest.raises(OrgConfigError, match="no_such"):
        select_work_hours_agents(["dev_agent"], cfg, set())


def test_select_unknown_exclude_raises() -> None:
    cfg = _cfg(exclude_agents=["typo_agent"])
    with pytest.raises(OrgConfigError, match="typo_agent"):
        select_work_hours_agents(["dev_agent"], cfg, set())


def test_select_unknown_team_key_raises() -> None:
    cfg = _cfg(teams={"marketng": WorkHoursScheduleLayer(interval="3h")})
    with pytest.raises(OrgConfigError, match="marketng"):
        select_work_hours_agents(["dev_agent"], cfg, {"engineering", "customer_service"})


# --- slot grid ---

def test_windowed_slot_grid() -> None:
    # 09:00 stepped by 2h up to <= 18:00 -> 09,11,13,15,17 (no 18:00).
    assert windowed_slot_minutes(_windowed()) == [540, 660, 780, 900, 1020]


def test_continuous_slot_grid_full_day() -> None:
    slots = continuous_slot_minutes(_continuous(interval="15m"))
    assert slots[0] == 0           # anchored at 00:00
    assert slots[-1] == 1425       # 23:45, last slot < 24:00
    assert len(slots) == 96


# --- current due slot ---

def test_windowed_due_slot_is_latest_at_or_before_now() -> None:
    # 2026-06-11 is a Thursday. 15:30 -> latest due slot is 15:00, NOT 09:00.
    due = current_due_slot(_windowed(), _utc(2026, 6, 11, 15, 30))
    assert due is not None
    local_date, slot, _ = due
    assert (local_date, slot) == ("2026-06-11", "15:00")


def test_windowed_before_first_slot_is_none() -> None:
    assert current_due_slot(_windowed(), _utc(2026, 6, 11, 8, 0)) is None


def test_windowed_unconfigured_day_is_none() -> None:
    # 2026-06-13 is a Saturday, not in mon-fri.
    assert current_due_slot(_windowed(), _utc(2026, 6, 13, 15, 0)) is None


def test_continuous_due_slot_and_midnight_rollover() -> None:
    sched = _continuous(interval="15m")
    # Just before midnight: latest slot is 23:45 on the old date.
    before = current_due_slot(sched, _utc(2026, 6, 11, 23, 59))
    assert before is not None
    assert (before[0], before[1]) == ("2026-06-11", "23:45")
    # At 00:00 the grid restarts: the 00:00 slot belongs to the NEW local_date.
    after = current_due_slot(sched, _utc(2026, 6, 12, 0, 0))
    assert after is not None
    assert (after[0], after[1]) == ("2026-06-12", "00:00")


def test_due_slot_respects_effective_timezone() -> None:
    # 01:30 UTC on 2026-06-11 == 09:30 Asia/Shanghai (UTC+8) -> 09:00 slot, local date 06-11.
    sched = _windowed(tz="Asia/Shanghai")
    due = current_due_slot(sched, _utc(2026, 6, 11, 1, 30))
    assert due is not None
    assert (due[0], due[1]) == ("2026-06-11", "09:00")


# --- decision: uniqueness guard + startup catch-up ---

def test_decide_schedules_latest_due_slot() -> None:
    decision = decide_wake(
        now=_utc(2026, 6, 11, 15, 30), schedule=_windowed(),
        existing_for_slot=None, startup=False,
    )
    assert decision.should_schedule is True
    assert (decision.local_date, decision.slot) == ("2026-06-11", "15:00")
    assert decision.record_skipped is False


def test_decide_blocks_when_row_exists() -> None:
    decision = decide_wake(
        now=_utc(2026, 6, 11, 15, 30), schedule=_windowed(),
        existing_for_slot=object(), startup=False,
    )
    assert decision.should_schedule is False
    assert decision.reason == "already_exists"


def test_decide_no_due_slot() -> None:
    decision = decide_wake(
        now=_utc(2026, 6, 11, 8, 0), schedule=_windowed(),
        existing_for_slot=None, startup=False,
    )
    assert decision.should_schedule is False
    assert decision.reason == "no_due_slot"


def test_startup_catch_up_disabled_records_skipped() -> None:
    decision = decide_wake(
        now=_utc(2026, 6, 11, 15, 30), schedule=_windowed(catch_up=False),
        existing_for_slot=None, startup=True,
    )
    assert decision.should_schedule is False
    assert decision.record_skipped is True
    # Still the single latest due slot, never an earlier one.
    assert decision.slot == "15:00"


def test_startup_catch_up_enabled_schedules_only_latest() -> None:
    decision = decide_wake(
        now=_utc(2026, 6, 11, 15, 30), schedule=_windowed(catch_up=True),
        existing_for_slot=None, startup=True,
    )
    assert decision.should_schedule is True
    assert decision.record_skipped is False
    assert decision.slot == "15:00"   # not 09:00/11:00/13:00 — no backfill
