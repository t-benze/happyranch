from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from runtime.daemon.dream_scheduler import select_dream_agents, should_schedule_for_agent
from runtime.models import DreamRecord, DreamStatus
from runtime.orchestrator.org_config import (
    DreamingConfig,
    OrgConfig,
    OrgConfigError,
    resolve_dreaming_timezone,
)


def test_select_dream_agents_all_with_exclude() -> None:
    cfg = DreamingConfig(
        enabled=True,
        agent_mode="all",
        include_agents=[],
        exclude_agents=["qa_engineer"],
    )
    assert select_dream_agents(
        available_agents=["dev_agent", "qa_engineer", "ops_manager"],
        config=cfg,
    ) == ["dev_agent", "ops_manager"]


def test_select_dream_agents_whitelist_then_exclude() -> None:
    cfg = DreamingConfig(
        enabled=True,
        agent_mode="whitelist",
        include_agents=["qa_engineer", "dev_agent"],
        exclude_agents=["qa_engineer"],
    )
    assert select_dream_agents(
        available_agents=["dev_agent", "qa_engineer", "ops_manager"],
        config=cfg,
    ) == ["dev_agent"]


def test_select_dream_agents_disabled() -> None:
    cfg = DreamingConfig(enabled=False)
    assert select_dream_agents(["dev_agent"], cfg) == []


def test_select_dream_agents_unknown_include_raises() -> None:
    cfg = DreamingConfig(
        enabled=True,
        agent_mode="whitelist",
        include_agents=["dev_agent", "no_such_agent"],
    )
    with pytest.raises(OrgConfigError, match="no_such_agent"):
        select_dream_agents(["dev_agent", "qa_engineer"], cfg)


def test_select_dream_agents_unknown_exclude_raises() -> None:
    cfg = DreamingConfig(
        enabled=True,
        agent_mode="all",
        exclude_agents=["typo_agent"],
    )
    with pytest.raises(OrgConfigError, match="typo_agent"):
        select_dream_agents(["dev_agent", "qa_engineer"], cfg)


def test_should_schedule_after_local_time_when_no_row() -> None:
    now = datetime(2026, 6, 9, 3, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    decision = should_schedule_for_agent(
        agent_name="dev_agent",
        now=now,
        config=DreamingConfig(enabled=True, schedule_time="02:00", timezone="Asia/Shanghai"),
        existing_for_date=None,
    )
    assert decision.should_schedule is True
    assert decision.local_date == "2026-06-09"
    assert decision.scheduled_for.isoformat().startswith("2026-06-09T02:00:00")


def test_should_not_schedule_before_local_time() -> None:
    now = datetime(2026, 6, 9, 1, 59, tzinfo=ZoneInfo("Asia/Shanghai"))
    decision = should_schedule_for_agent(
        agent_name="dev_agent",
        now=now,
        config=DreamingConfig(enabled=True, schedule_time="02:00", timezone="Asia/Shanghai"),
        existing_for_date=None,
    )
    assert decision.should_schedule is False
    assert decision.reason == "not_due"


def test_should_not_schedule_when_row_exists() -> None:
    now = datetime(2026, 6, 9, 3, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    existing = DreamRecord(
        id="DREAM-001",
        agent_name="dev_agent",
        local_date="2026-06-09",
        scheduled_for=now,
        window_end=now,
        status=DreamStatus.FAILED,
    )
    decision = should_schedule_for_agent(
        agent_name="dev_agent",
        now=now,
        config=DreamingConfig(enabled=True, schedule_time="02:00", timezone="Asia/Shanghai"),
        existing_for_date=existing,
    )
    assert decision.should_schedule is False
    assert decision.reason == "already_exists"


# ---- TASK-976: threaded effective timezone (None must never reach ZoneInfo) ---

def test_threaded_tz_computes_in_passed_zone_even_when_config_tz_none() -> None:
    # dreaming.timezone omitted (None) but the caller threads the resolved
    # effective zone in — must NOT crash and must compute in that zone.
    now = datetime(2026, 6, 9, 18, 0, tzinfo=timezone.utc)  # 02:00 next day in +08
    decision = should_schedule_for_agent(
        agent_name="dev_agent",
        now=now,
        config=DreamingConfig(enabled=True, schedule_time="02:00", timezone=None),
        existing_for_date=None,
        tz=ZoneInfo("Asia/Shanghai"),
    )
    assert decision.should_schedule is True
    assert decision.local_date == "2026-06-10"


def test_none_config_tz_without_thread_does_not_crash() -> None:
    # No tz threaded and config.timezone None -> machine-local/UTC fallback,
    # never ZoneInfo(None).
    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    decision = should_schedule_for_agent(
        agent_name="dev_agent",
        now=now,
        config=DreamingConfig(enabled=True, schedule_time="02:00", timezone=None),
        existing_for_date=None,
    )
    assert isinstance(decision.local_date, str)
    assert len(decision.local_date) == 10  # YYYY-MM-DD


def test_inheriting_org_schedules_in_org_timezone() -> None:
    # dreaming omits its own tz; effective zone comes from org.timezone.
    org = OrgConfig(
        timezone="Asia/Shanghai",
        dreaming=DreamingConfig(enabled=True, schedule_time="02:00"),
    )
    tz = resolve_dreaming_timezone(org)
    now = datetime(2026, 6, 9, 18, 0, tzinfo=timezone.utc)  # 02:00 +08 next day
    decision = should_schedule_for_agent(
        agent_name="dev_agent", now=now,
        config=org.dreaming, existing_for_date=None, tz=tz,
    )
    assert decision.should_schedule is True
    assert decision.local_date == "2026-06-10"


def test_explicit_dreaming_tz_schedules_in_that_zone() -> None:
    org = OrgConfig(
        timezone="Asia/Shanghai",
        dreaming=DreamingConfig(enabled=True, schedule_time="02:00", timezone="America/New_York"),
    )
    tz = resolve_dreaming_timezone(org)
    # 2026-06-09 05:00 UTC == 01:00 EDT (-04) -> before 02:00, not due yet.
    now = datetime(2026, 6, 9, 5, 0, tzinfo=timezone.utc)
    decision = should_schedule_for_agent(
        agent_name="dev_agent", now=now,
        config=org.dreaming, existing_for_date=None, tz=tz,
    )
    assert decision.should_schedule is False
    assert decision.reason == "not_due"
