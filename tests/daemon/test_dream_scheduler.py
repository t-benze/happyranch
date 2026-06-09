from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from runtime.daemon.dream_scheduler import select_dream_agents, should_schedule_for_agent
from runtime.models import DreamRecord, DreamStatus
from runtime.orchestrator.org_config import DreamingConfig, OrgConfigError


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
