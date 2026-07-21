"""Unit tests for schedule validation/recurrence helpers (THR-105 Phase 1).

Tests the pure rules in ``runtime.orchestrator.schedule_rules``: weekly
recurrence validation, one-shot horizon, expiry defaults, caps, and
next_weekly_occurrence.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from runtime.orchestrator.schedule_rules import (
    _WEEKDAYS,
    default_expires_at,
    next_weekly_occurrence,
    validate_caps,
    validate_one_shot_horizon,
    validate_weekly_recurrence,
)


def _now() -> datetime:
    return datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


# ------------------------------------------------- weekly recurrence validation


def test_valid_weekly_recurrence():
    rec = {"day": "Mon", "time": "09:00", "tz": "Asia/Shanghai"}
    assert validate_weekly_recurrence(rec) is None


def test_weekly_rejects_none():
    assert validate_weekly_recurrence(None) == "recurrence must not be null for weekly schedules"


def test_weekly_rejects_non_dict():
    assert validate_weekly_recurrence("foo")  # type: ignore[arg-type]
    assert validate_weekly_recurrence(42)  # type: ignore[arg-type]


def test_weekly_rejects_extra_keys():
    rec = {"day": "Mon", "time": "09:00", "tz": "UTC", "extra": "no"}
    err = validate_weekly_recurrence(rec)
    assert err is not None and "must have exactly keys" in err


def test_weekly_rejects_missing_keys():
    rec = {"day": "Mon", "time": "09:00"}
    err = validate_weekly_recurrence(rec)
    assert err is not None and "must have exactly keys" in err


def test_weekly_rejects_invalid_weekday():
    for bad in ["Funday", "mondayy", "", "3"]:
        rec = {"day": bad, "time": "09:00", "tz": "UTC"}
        err = validate_weekly_recurrence(rec)
        assert err is not None, f"expected error for day={bad!r}"
        assert "valid weekday" in err


def test_weekly_accepts_all_seven_days():
    for day in _WEEKDAYS:
        assert validate_weekly_recurrence(
            {"day": day, "time": "09:00", "tz": "UTC"}
        ) is None


def test_weekly_rejects_invalid_time_format():
    for bad_time in ["9:00", "0900", "ab:cd", "25:00", "12:60", "", "noon"]:
        rec = {"day": "Mon", "time": bad_time, "tz": "UTC"}
        err = validate_weekly_recurrence(rec)
        assert err is not None, f"expected error for time={bad_time!r}"
        assert "HH:MM" in err


def test_weekly_rejects_invalid_timezone():
    rec = {"day": "Mon", "time": "09:00", "tz": "Mars/Nowhere"}
    err = validate_weekly_recurrence(rec)
    assert err is not None and "not a valid timezone" in err


def test_weekly_rejects_empty_timezone():
    rec = {"day": "Mon", "time": "09:00", "tz": ""}
    err = validate_weekly_recurrence(rec)
    assert err is not None and "non-empty timezone" in err


# ------------------------------------------------------ one-shot horizon


def test_one_shot_valid_future_within_horizon():
    fire_at = _now() + timedelta(days=30)
    assert validate_one_shot_horizon(fire_at, _now()) is None


def test_one_shot_rejects_past():
    fire_at = _now() - timedelta(days=1)
    err = validate_one_shot_horizon(fire_at, _now())
    assert err is not None and "must be in the future" in err


def test_one_shot_rejects_beyond_90_days():
    fire_at = _now() + timedelta(days=91)
    err = validate_one_shot_horizon(fire_at, _now())
    assert err is not None and "within 90 days" in err


def test_one_shot_accepts_exactly_90_days():
    fire_at = _now() + timedelta(days=90)
    assert validate_one_shot_horizon(fire_at, _now()) is None


# --------------------------------------------------------- expiry defaults


def test_one_shot_has_no_expiry():
    assert default_expires_at(_now(), "one_shot") is None
    assert default_expires_at(_now(), "one_shot", indefinite=True) is None


def test_weekly_defaults_to_90_day_expiry():
    created = _now()
    exp = default_expires_at(created, "weekly")
    assert exp is not None
    assert exp == created + timedelta(days=90)


def test_weekly_indefinite_skips_expiry():
    assert default_expires_at(_now(), "weekly", indefinite=True) is None


# ----------------------------------------------------------------- caps


def test_caps_within_limits():
    assert validate_caps(5, 20) is None


def test_caps_agent_exceeded():
    err = validate_caps(20, 5)
    assert err is not None and "agent has 20" in err


def test_caps_org_exceeded():
    err = validate_caps(5, 100)
    assert err is not None and "org has 100" in err


# ------------------------------------------------- next_weekly_occurrence


def test_next_weekly_occurrence_tomorrow_same_tz():
    """Next Monday from a Sunday should be tomorrow."""
    # Sunday 2026-07-19 in UTC -> next Mon is 2026-07-20
    after = datetime(2026, 7, 19, 15, 0, 0, tzinfo=timezone.utc)
    result = next_weekly_occurrence("Mon", "09:00", "UTC", after=after)
    assert result is not None
    assert result == datetime(2026, 7, 20, 9, 0, 0, tzinfo=timezone.utc)


def test_next_weekly_occurrence_same_day_after_time():
    """Next Monday at 09:00 from Monday 08:00 should be today."""
    after = datetime(2026, 7, 20, 8, 0, 0, tzinfo=timezone.utc)
    result = next_weekly_occurrence("Mon", "09:00", "UTC", after=after)
    assert result is not None
    assert result == datetime(2026, 7, 20, 9, 0, 0, tzinfo=timezone.utc)


def test_next_weekly_occurrence_same_day_past_time():
    """Next Monday at 09:00 from Monday 10:00 should be next week."""
    after = datetime(2026, 7, 20, 10, 0, 0, tzinfo=timezone.utc)
    result = next_weekly_occurrence("Mon", "09:00", "UTC", after=after)
    assert result is not None
    assert result == datetime(2026, 7, 27, 9, 0, 0, tzinfo=timezone.utc)


def test_next_weekly_occurrence_with_timezone():
    """Shanghai is UTC+8; 09:00 Shanghai = 01:00 UTC.
    From Sunday 2026-07-19 00:00 UTC, next Mon 09:00 Shanghai should be
    2026-07-20 01:00 UTC."""
    after = datetime(2026, 7, 19, 0, 0, 0, tzinfo=timezone.utc)
    result = next_weekly_occurrence("Mon", "09:00", "Asia/Shanghai", after=after)
    assert result is not None
    # result is a datetime with tzinfo, compare in UTC
    assert result.astimezone(timezone.utc) == datetime(2026, 7, 20, 1, 0, 0, tzinfo=timezone.utc)


def test_next_weekly_occurrence_invalid_tz_returns_none():
    result = next_weekly_occurrence("Mon", "09:00", "Invalid/Zone")
    assert result is None


# ------------------------------------------ working-hours regression (shared helper)

def test_weekday_names_match_work_hours_scheduler():
    """The WEEKDAYS tuple in schedule_rules matches the _WEEKDAYS in
    work_hours_scheduler, ensuring the shared weekday walking approach
    stays consistent."""
    from runtime.daemon.work_hours_scheduler import _WEEKDAYS as WH_WEEKDAYS
    assert _WEEKDAYS == WH_WEEKDAYS
