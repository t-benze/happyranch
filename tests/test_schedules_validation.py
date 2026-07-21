"""Tests for schedule validation / normalization helpers (pure, unit-testable).

Covers the approved v1 envelope: self-target, kind, exactly-one-weekday
recurrence, timezone validation, one-shot horizon, recurring expiry/defaults,
per-agent/org armed caps, source_instruction + normalized_brief required,
and disabled-capability input rejection.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from runtime.infrastructure.schedule_validation import (
    validate_schedule_create,
    ValidationError,
    _DAYS_OF_WEEK,
)
from runtime.models import ScheduleKind, ScheduleStatus

# --------------- helpers ---------------

def _utc(y: int, m: int, d: int, h: int = 9, minute: int = 0) -> datetime:
    return datetime(y, m, d, h, minute, tzinfo=timezone.utc)


TEST_CLOCK = _utc(2026, 7, 21, 12)  # Tuesday

# --------------- kind / envelope shape --------------

@pytest.mark.parametrize("kind", ["one_shot", "weekly"])
def test_accepts_valid_one_shot_and_weekly(kind):
    r = validate_schedule_create(
        agent_name="dev_agent",
        kind=kind,
        fire_at=_utc(2026, 7, 25, 9),  # Saturday
        recurrence={"day": "Sat", "time": "09:00", "tz": "Asia/Shanghai"} if kind == "weekly" else None,
        timezone="Asia/Shanghai",
        source_instruction="send market update",
        normalized_brief="Send the weekly market update.",
        armed_count_agent=0,
        armed_count_org=0,
        scheduling_enabled=True,
        now=TEST_CLOCK,
        indefinite=(kind == "weekly"),
        expires_at=None,
    )
    assert r["fire_at"] is not None
    assert r["status"] == ScheduleStatus.ARMED.value


def test_rejects_unknown_kind():
    with pytest.raises(ValidationError, match="schedule kind"):
        validate_schedule_create(
            agent_name="dev_agent",
            kind="cron",
            fire_at=_utc(2026, 7, 25, 9),
            recurrence=None,
            timezone="Asia/Shanghai",
            source_instruction="x",
            normalized_brief="x",
            armed_count_agent=0,
            armed_count_org=0,
            scheduling_enabled=True,
            now=TEST_CLOCK,
        )


def test_rejects_one_shot_with_recurrence():
    with pytest.raises(ValidationError, match="one_shot"):
        validate_schedule_create(
            agent_name="dev_agent",
            kind="one_shot",
            fire_at=_utc(2026, 7, 25, 9),
            recurrence={"day": "Sat", "time": "09:00", "tz": "Asia/Shanghai"},
            timezone="Asia/Shanghai",
            source_instruction="x",
            normalized_brief="x",
            armed_count_agent=0,
            armed_count_org=0,
            scheduling_enabled=True,
            now=TEST_CLOCK,
        )


def test_rejects_weekly_without_recurrence():
    with pytest.raises(ValidationError, match="weekly"):
        validate_schedule_create(
            agent_name="dev_agent",
            kind="weekly",
            fire_at=_utc(2026, 7, 25, 9),
            recurrence=None,
            timezone="Asia/Shanghai",
            source_instruction="x",
            normalized_brief="x",
            armed_count_agent=0,
            armed_count_org=0,
            scheduling_enabled=True,
            now=TEST_CLOCK,
        )


# --------------- recurrence shape ---------------

def test_weekly_accepts_exactly_one_weekday():
    r = validate_schedule_create(
        agent_name="dev_agent",
        kind="weekly",
        fire_at=_utc(2026, 7, 25, 9),  # Saturday
        recurrence={"day": "Sat", "time": "09:00", "tz": "Asia/Shanghai"},
        timezone="Asia/Shanghai",
        source_instruction="send market update every Saturday",
        normalized_brief="Send the weekly market update.",
        armed_count_agent=0,
        armed_count_org=0,
        scheduling_enabled=True,
        now=TEST_CLOCK,
        indefinite=True,
        expires_at=None,
    )
    assert r["kind"] == "weekly"
    assert r["recurrence"] == {"day": "Sat", "time": "09:00", "tz": "Asia/Shanghai"}


def test_rejects_extra_recurrence_key_cron():
    """Reject any extra/alternate keys beyond {day, time, tz}."""
    with pytest.raises(ValidationError, match="extra keys"):
        validate_schedule_create(
            agent_name="dev_agent",
            kind="weekly",
            fire_at=_utc(2026, 7, 25, 9),
            recurrence={"day": "Sat", "time": "09:00", "tz": "Asia/Shanghai", "cron": "* * * * *"},
            timezone="Asia/Shanghai",
            source_instruction="x",
            normalized_brief="x",
            armed_count_agent=0,
            armed_count_org=0,
            scheduling_enabled=True,
            now=TEST_CLOCK,
        )


def test_rejects_extra_recurrence_keys_multiple():
    """Reject multiple extra keys like interval, every, count."""
    with pytest.raises(ValidationError, match="extra keys"):
        validate_schedule_create(
            agent_name="dev_agent",
            kind="weekly",
            fire_at=_utc(2026, 7, 25, 9),
            recurrence={"day": "Sat", "time": "09:00", "tz": "Asia/Shanghai", "interval": 2, "every": "week"},
            timezone="Asia/Shanghai",
            source_instruction="x",
            normalized_brief="x",
            armed_count_agent=0,
            armed_count_org=0,
            scheduling_enabled=True,
            now=TEST_CLOCK,
        )


def test_normalized_recurrence_has_no_extra_keys():
    """The returned recurrence dict must be a clean normalized {day, time, tz}."""
    r = validate_schedule_create(
        agent_name="dev_agent",
        kind="weekly",
        fire_at=_utc(2026, 7, 25, 9),
        recurrence={"day": "Sat", "time": "09:00", "tz": "Asia/Shanghai"},
        timezone="Asia/Shanghai",
        source_instruction="x",
        normalized_brief="x",
        armed_count_agent=0,
        armed_count_org=0,
        scheduling_enabled=True,
        now=TEST_CLOCK,
        indefinite=True,
        expires_at=None,
    )
    assert r["recurrence"] == {"day": "Sat", "time": "09:00", "tz": "Asia/Shanghai"}
    assert set(r["recurrence"].keys()) == {"day", "time", "tz"}


def test_rejects_multi_weekday_recurrence():
    with pytest.raises(ValidationError, match="exactly one weekday"):
        validate_schedule_create(
            agent_name="dev_agent",
            kind="weekly",
            fire_at=_utc(2026, 7, 25, 9),
            recurrence={"day": "Mon,Wed", "time": "09:00", "tz": "Asia/Shanghai"},
            timezone="Asia/Shanghai",
            source_instruction="x",
            normalized_brief="x",
            armed_count_agent=0,
            armed_count_org=0,
            scheduling_enabled=True,
            now=TEST_CLOCK,
        )


def test_rejects_invalid_weekday_name():
    with pytest.raises(ValidationError, match="weekday"):
        validate_schedule_create(
            agent_name="dev_agent",
            kind="weekly",
            fire_at=_utc(2026, 7, 25, 9),
            recurrence={"day": "Funday", "time": "09:00", "tz": "Asia/Shanghai"},
            timezone="Asia/Shanghai",
            source_instruction="x",
            normalized_brief="x",
            armed_count_agent=0,
            armed_count_org=0,
            scheduling_enabled=True,
            now=TEST_CLOCK,
        )


def test_rejects_weekly_missing_day():
    with pytest.raises(ValidationError, match="recurrence"):
        validate_schedule_create(
            agent_name="dev_agent",
            kind="weekly",
            fire_at=_utc(2026, 7, 25, 9),
            recurrence={"time": "09:00", "tz": "Asia/Shanghai"},
            timezone="Asia/Shanghai",
            source_instruction="x",
            normalized_brief="x",
            armed_count_agent=0,
            armed_count_org=0,
            scheduling_enabled=True,
            now=TEST_CLOCK,
        )


def test_rejects_weekly_missing_time():
    with pytest.raises(ValidationError, match="recurrence"):
        validate_schedule_create(
            agent_name="dev_agent",
            kind="weekly",
            fire_at=_utc(2026, 7, 25, 9),
            recurrence={"day": "Sat", "tz": "Asia/Shanghai"},
            timezone="Asia/Shanghai",
            source_instruction="x",
            normalized_brief="x",
            armed_count_agent=0,
            armed_count_org=0,
            scheduling_enabled=True,
            now=TEST_CLOCK,
        )


def test_rejects_weekly_missing_tz():
    with pytest.raises(ValidationError, match="recurrence"):
        validate_schedule_create(
            agent_name="dev_agent",
            kind="weekly",
            fire_at=_utc(2026, 7, 25, 9),
            recurrence={"day": "Sat", "time": "09:00"},
            timezone="Asia/Shanghai",
            source_instruction="x",
            normalized_brief="x",
            armed_count_agent=0,
            armed_count_org=0,
            scheduling_enabled=True,
            now=TEST_CLOCK,
        )


# --------------- timezone validation ---------------

def test_rejects_invalid_timezone():
    with pytest.raises(ValidationError, match="timezone"):
        validate_schedule_create(
            agent_name="dev_agent",
            kind="one_shot",
            fire_at=_utc(2026, 7, 25, 9),
            recurrence=None,
            timezone="Mars/Base1",
            source_instruction="x",
            normalized_brief="x",
            armed_count_agent=0,
            armed_count_org=0,
            scheduling_enabled=True,
            now=TEST_CLOCK,
        )


def test_accepts_iana_timezone():
    r = validate_schedule_create(
        agent_name="dev_agent",
        kind="one_shot",
        fire_at=_utc(2026, 7, 25, 9),
        recurrence=None,
        timezone="America/New_York",
        source_instruction="x",
        normalized_brief="x",
        armed_count_agent=0,
        armed_count_org=0,
        scheduling_enabled=True,
        now=TEST_CLOCK,
    )
    assert r["timezone"] == "America/New_York"


# --------------- self-target ---------------

def test_rejects_cross_agent_target():
    with pytest.raises(ValidationError, match="cross-agent"):
        validate_schedule_create(
            agent_name="dev_agent",
            kind="one_shot",
            fire_at=_utc(2026, 7, 25, 9),
            recurrence=None,
            timezone="Asia/Shanghai",
            source_instruction="x",
            normalized_brief="x",
            armed_count_agent=0,
            armed_count_org=0,
            scheduling_enabled=True,
            now=TEST_CLOCK,
            target_agent="investment_advisor",
        )


def test_accepts_self_target():
    r = validate_schedule_create(
        agent_name="dev_agent",
        kind="one_shot",
        fire_at=_utc(2026, 7, 25, 9),
        recurrence=None,
        timezone="Asia/Shanghai",
        source_instruction="x",
        normalized_brief="x",
        armed_count_agent=0,
        armed_count_org=0,
        scheduling_enabled=True,
        now=TEST_CLOCK,
        target_agent="dev_agent",
    )
    assert r["target_agent"] == "dev_agent"


# --------------- required fields ---------------

def test_rejects_missing_normalized_brief():
    with pytest.raises(ValidationError, match="normalized_brief"):
        validate_schedule_create(
            agent_name="dev_agent",
            kind="one_shot",
            fire_at=_utc(2026, 7, 25, 9),
            recurrence=None,
            timezone="Asia/Shanghai",
            source_instruction="some instruction",
            normalized_brief="",
            armed_count_agent=0,
            armed_count_org=0,
            scheduling_enabled=True,
            now=TEST_CLOCK,
        )


def test_rejects_missing_source_instruction():
    with pytest.raises(ValidationError, match="source_instruction"):
        validate_schedule_create(
            agent_name="dev_agent",
            kind="one_shot",
            fire_at=_utc(2026, 7, 25, 9),
            recurrence=None,
            timezone="Asia/Shanghai",
            source_instruction="",
            normalized_brief="send it",
            armed_count_agent=0,
            armed_count_org=0,
            scheduling_enabled=True,
            now=TEST_CLOCK,
        )


# --------------- caps ---------------

def test_rejects_agent_cap_exceeded():
    with pytest.raises(ValidationError, match="agent cap"):
        validate_schedule_create(
            agent_name="dev_agent",
            kind="one_shot",
            fire_at=_utc(2026, 7, 25, 9),
            recurrence=None,
            timezone="Asia/Shanghai",
            source_instruction="x",
            normalized_brief="x",
            armed_count_agent=20,
            armed_count_org=0,
            scheduling_enabled=True,
            now=TEST_CLOCK,
        )


def test_rejects_org_cap_exceeded():
    with pytest.raises(ValidationError, match="org cap"):
        validate_schedule_create(
            agent_name="dev_agent",
            kind="one_shot",
            fire_at=_utc(2026, 7, 25, 9),
            recurrence=None,
            timezone="Asia/Shanghai",
            source_instruction="x",
            normalized_brief="x",
            armed_count_agent=3,
            armed_count_org=100,
            scheduling_enabled=True,
            now=TEST_CLOCK,
        )


def test_accepts_at_cap_boundary():
    r = validate_schedule_create(
        agent_name="dev_agent",
        kind="one_shot",
        fire_at=_utc(2026, 7, 25, 9),
        recurrence=None,
        timezone="Asia/Shanghai",
        source_instruction="x",
        normalized_brief="x",
        armed_count_agent=19,
        armed_count_org=99,
        scheduling_enabled=True,
        now=TEST_CLOCK,
    )
    assert r is not None


# --------------- one-shot horizon ---------------

def test_rejects_one_shot_beyond_horizon():
    with pytest.raises(ValidationError, match="horizon"):
        validate_schedule_create(
            agent_name="dev_agent",
            kind="one_shot",
            fire_at=_utc(2026, 12, 1, 9),  # well beyond 90 days from Jul 21
            recurrence=None,
            timezone="Asia/Shanghai",
            source_instruction="x",
            normalized_brief="x",
            armed_count_agent=0,
            armed_count_org=0,
            scheduling_enabled=True,
            now=TEST_CLOCK,
        )


def test_accepts_one_shot_within_horizon():
    r = validate_schedule_create(
        agent_name="dev_agent",
        kind="one_shot",
        fire_at=_utc(2026, 10, 10, 9),  # ~81 days from Jul 21, within 90 days
        recurrence=None,
        timezone="Asia/Shanghai",
        source_instruction="x",
        normalized_brief="x",
        armed_count_agent=0,
        armed_count_org=0,
        scheduling_enabled=True,
        now=TEST_CLOCK,
    )
    assert r is not None


# --------------- recurring expiry / indefinite ---------------

def test_defaults_recurring_expiry_when_omitted():
    """When indefinite is False and expires_at is omitted, default to now+90 days."""
    r = validate_schedule_create(
        agent_name="dev_agent",
        kind="weekly",
        fire_at=_utc(2026, 7, 25, 9),
        recurrence={"day": "Sat", "time": "09:00", "tz": "Asia/Shanghai"},
        timezone="Asia/Shanghai",
        source_instruction="x",
        normalized_brief="x",
        armed_count_agent=0,
        armed_count_org=0,
        scheduling_enabled=True,
        now=TEST_CLOCK,
        indefinite=False,
        expires_at=None,
    )
    assert r["expires_at"] is not None
    expected_default = TEST_CLOCK + timedelta(days=90)
    assert r["expires_at"] == expected_default


def test_rejects_recurring_expiry_beyond_90_days():
    """Provided expires_at must not exceed the 90-day review window."""
    with pytest.raises(ValidationError, match="expir"):
        validate_schedule_create(
            agent_name="dev_agent",
            kind="weekly",
            fire_at=_utc(2026, 7, 25, 9),
            recurrence={"day": "Sat", "time": "09:00", "tz": "Asia/Shanghai"},
            timezone="Asia/Shanghai",
            source_instruction="x",
            normalized_brief="x",
            armed_count_agent=0,
            armed_count_org=0,
            scheduling_enabled=True,
            now=TEST_CLOCK,
            indefinite=False,
            expires_at=_utc(2027, 1, 1, 12),  # > 90 days from Jul 21
        )


def test_accepts_recurring_expiry_within_90_days():
    """Explicit expires_at within the 90-day window is accepted."""
    r = validate_schedule_create(
        agent_name="dev_agent",
        kind="weekly",
        fire_at=_utc(2026, 7, 25, 9),
        recurrence={"day": "Sat", "time": "09:00", "tz": "Asia/Shanghai"},
        timezone="Asia/Shanghai",
        source_instruction="x",
        normalized_brief="x",
        armed_count_agent=0,
        armed_count_org=0,
        scheduling_enabled=True,
        now=TEST_CLOCK,
        indefinite=False,
        expires_at=_utc(2026, 8, 21, 12),  # ~31 days, within 90
    )
    assert r["expires_at"] is not None
    assert r["indefinite"] is False


def test_accepts_recurring_with_indefinite():
    r = validate_schedule_create(
        agent_name="dev_agent",
        kind="weekly",
        fire_at=_utc(2026, 7, 25, 9),
        recurrence={"day": "Sat", "time": "09:00", "tz": "Asia/Shanghai"},
        timezone="Asia/Shanghai",
        source_instruction="x",
        normalized_brief="x",
        armed_count_agent=0,
        armed_count_org=0,
        scheduling_enabled=True,
        now=TEST_CLOCK,
        indefinite=True,
        expires_at=None,
    )
    assert r["indefinite"] is True


def test_accepts_recurring_with_expiry():
    r = validate_schedule_create(
        agent_name="dev_agent",
        kind="weekly",
        fire_at=_utc(2026, 7, 25, 9),
        recurrence={"day": "Sat", "time": "09:00", "tz": "Asia/Shanghai"},
        timezone="Asia/Shanghai",
        source_instruction="x",
        normalized_brief="x",
        armed_count_agent=0,
        armed_count_org=0,
        scheduling_enabled=True,
        now=TEST_CLOCK,
        indefinite=False,
        expires_at=_utc(2026, 10, 19, 12),
    )
    assert r["indefinite"] is False
    assert r["expires_at"] is not None


# --------------- disabled capability ---------------

def test_rejects_when_scheduling_disabled():
    with pytest.raises(ValidationError, match="disabled"):
        validate_schedule_create(
            agent_name="dev_agent",
            kind="one_shot",
            fire_at=_utc(2026, 7, 25, 9),
            recurrence=None,
            timezone="Asia/Shanghai",
            source_instruction="x",
            normalized_brief="x",
            armed_count_agent=0,
            armed_count_org=0,
            scheduling_enabled=False,
            now=TEST_CLOCK,
        )


# --------------- Saturday morning market-update acceptance test ---------------

def test_accepts_saturday_weekly_market_update():
    """The anchor use case: 'every Saturday, send me the weekly market update'."""
    r = validate_schedule_create(
        agent_name="investment_advisor",
        kind="weekly",
        fire_at=_utc(2026, 7, 25, 9),  # next Saturday
        recurrence={"day": "Sat", "time": "09:00", "tz": "Asia/Shanghai"},
        timezone="Asia/Shanghai",
        source_instruction="Every Saturday, send me the weekly market update.",
        normalized_brief="Send the weekly market update covering equities, bonds, and crypto.",
        armed_count_agent=0,
        armed_count_org=0,
        scheduling_enabled=True,
        now=TEST_CLOCK,
        indefinite=True,
        expires_at=None,
    )
    assert r["kind"] == "weekly"
    assert r["recurrence"]["day"] == "Sat"
    assert r["normalized_brief"] is not None


# --------------- absolute one-shot acceptance test ---------------

def test_accepts_absolute_one_shot():
    """The anchor use case: follow up in 48 hours."""
    r = validate_schedule_create(
        agent_name="support_agent",
        kind="one_shot",
        fire_at=_utc(2026, 7, 23, 9),  # 48h from now
        recurrence=None,
        timezone="Asia/Shanghai",
        source_instruction="Follow up with customer in 48 hours after issue was filed.",
        normalized_brief="Follow up with customer re: issue #1234 and confirm resolution.",
        armed_count_agent=0,
        armed_count_org=0,
        scheduling_enabled=True,
        now=TEST_CLOCK,
    )
    assert r["kind"] == "one_shot"
    assert r["fire_at"] is not None
    assert r["recurrence"] is None
