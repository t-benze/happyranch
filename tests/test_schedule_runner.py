"""THR-105 Phase 3: TDD tests for schedule runner — prompt composition,
terminal transitions, token scope, executor integration.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.infrastructure.database import Database
from runtime.models import ScheduleKind, ScheduleRecord, ScheduleStatus
from runtime.orchestrator.org_config import OrgConfig
from runtime.daemon.schedule_runner import _failure_transition, _timeout_transition, build_schedule_prompt


# ── helpers ──────────────────────────────────────────────────────────────

def _org_config(**overrides) -> OrgConfig:
    cfg = OrgConfig()
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


# ── prompt composition ──────────────────────────────────────────────────

def test_build_prompt_one_shot():
    prompt = build_schedule_prompt(
        org_slug="test-org",
        schedule_id="SCHEDULE-001",
        agent_name="dev_agent",
        role="worker",
        team="engineering",
        normalized_brief="Send weekly status report to the founder.",
        kind="one_shot",
        fire_at_iso="2026-07-22T12:00:00+00:00",
        recurrence=None,
        timezone="UTC",
        org_config=_org_config(),
    )
    assert "Schedule Fire" in prompt
    assert "SCHEDULE-001" in prompt
    assert "one_shot" in prompt
    assert "2026-07-22T12:00:00+00:00" in prompt
    assert "happyranch schedules spawn" in prompt
    assert "--schedule-id SCHEDULE-001" in prompt
    assert "--org test-org" in prompt
    assert "Send weekly status report to the founder." in prompt
    assert "Normalized Brief" in prompt


@pytest.mark.parametrize("marker", [
    "## [RESERVED] Active Team Escalation Policy",
    "<!-- BEGIN HAPPYRANCH ACTIVE TEAM POLICY -->",
    "<!-- END HAPPYRANCH ACTIVE TEAM POLICY -->",
])
def test_schedule_shipping_builder_rejects_every_reserved_untrusted_marker(marker):
    from runtime.orchestrator.active_authority_policy import ActiveAuthorityPolicyError
    with pytest.raises(ActiveAuthorityPolicyError, match="server-reserved"):
        build_schedule_prompt(
            org_slug="test", schedule_id="SCHEDULE-X", agent_name="dev_agent",
            role="worker", team="engineering", normalized_brief=marker,
            kind="one_shot", fire_at_iso="2026-01-01T00:00:00Z",
            recurrence=None, timezone="UTC", org_config=_org_config(),
        )


def test_build_prompt_weekly():
    prompt = build_schedule_prompt(
        org_slug="test-org",
        schedule_id="SCHEDULE-002",
        agent_name="dev_agent",
        role="worker",
        team="engineering",
        normalized_brief="Market update for Saturday.",
        kind="weekly",
        fire_at_iso="2026-07-25T09:00:00+00:00",
        recurrence={"day": "Sat", "time": "09:00", "tz": "UTC"},
        timezone="UTC",
        org_config=_org_config(),
    )
    assert "Schedule Fire" in prompt
    assert "weekly" in prompt
    assert "Recurrence: Sat 09:00 UTC" in prompt
    assert "Market update for Saturday." in prompt


def test_failure_continuity_rearms_recurring_and_weekly_but_one_shot_is_terminal(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    now = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)
    recurring = ScheduleRecord(
        id="SCHEDULE-001", agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.RECURRING, status=ScheduleStatus.FIRING,
        fire_at=now, timezone="UTC", normalized_brief="x", source_instruction="x",
        recurrence={"freq": "DAILY", "interval": 1, "anchor_date": "2026-07-01", "time": "09:00", "tz": "UTC", "until": None, "count": None},
    )
    one_shot = ScheduleRecord(
        id="SCHEDULE-002", agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT, status=ScheduleStatus.FIRING,
        fire_at=now, timezone="UTC", normalized_brief="x", source_instruction="x",
    )
    weekly = ScheduleRecord(
        id="SCHEDULE-003", agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.WEEKLY, status=ScheduleStatus.FIRING,
        fire_at=now, timezone="UTC", normalized_brief="x", source_instruction="x",
        recurrence={"day": "Wed", "time": "09:00", "tz": "UTC"},
    )
    db.schedules.insert(recurring)
    db.schedules.insert(one_shot)
    db.schedules.insert(weekly)
    _failure_transition(db.schedules, recurring, now, "executor_failed")
    _timeout_transition(db.schedules, one_shot, now, "timed_out")
    _timeout_transition(db.schedules, weekly, now, "timed_out")
    assert db.schedules.get(recurring.id).status == ScheduleStatus.ARMED
    assert db.schedules.get(recurring.id).fire_count == 0
    assert db.schedules.get(weekly.id).status == ScheduleStatus.ARMED
    assert db.schedules.get(weekly.id).fire_count == 0
    assert db.schedules.get(one_shot.id).status == ScheduleStatus.TIMEOUT

    _failure_transition(db.schedules, one_shot, now, "executor_failed")
    assert db.schedules.get(one_shot.id).status == ScheduleStatus.FAILED


def test_build_prompt_includes_managed_skills_when_present():
    prompt = build_schedule_prompt(
        org_slug="test-org",
        schedule_id="SCHEDULE-001",
        agent_name="dev_agent",
        role="worker",
        team="engineering",
        normalized_brief="test",
        kind="one_shot",
        fire_at_iso="2026-07-22T12:00:00+00:00",
        recurrence=None,
        timezone="UTC",
        org_config=_org_config(),
        managed_skills_index="## Your Skills\n\ntest-skill: a test skill",
    )
    assert "## Your Skills" in prompt
    assert "test-skill" in prompt


def test_build_prompt_includes_protocol_docs_when_present():
    prompt = build_schedule_prompt(
        org_slug="test-org",
        schedule_id="SCHEDULE-001",
        agent_name="dev_agent",
        role="worker",
        team="engineering",
        normalized_brief="test",
        kind="one_shot",
        fire_at_iso="2026-07-22T12:00:00+00:00",
        recurrence=None,
        timezone="UTC",
        org_config=_org_config(),
        protocol_doc_manifest="Protocol Docs:\n- 00-completion-contract.md",
    )
    assert "Protocol Docs:" in prompt
    assert "00-completion-contract.md" in prompt


def test_build_prompt_current_time_rendered():
    """current_time is injected with the org's effective timezone."""
    from datetime import date

    class FixedDate(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 22, 12, 0, tzinfo=timezone.utc)

    prompt = build_schedule_prompt(
        org_slug="test-org",
        schedule_id="SCHEDULE-001",
        agent_name="dev_agent",
        role="worker",
        team="engineering",
        normalized_brief="test",
        kind="one_shot",
        fire_at_iso="2026-07-22T12:00:00+00:00",
        recurrence=None,
        timezone="UTC",
        org_config=_org_config(),
        now=lambda: FixedDate(2026, 7, 22, 12, 0, tzinfo=timezone.utc),
    )
    assert "current_time:" in prompt
    assert "2026-07-22" in prompt
