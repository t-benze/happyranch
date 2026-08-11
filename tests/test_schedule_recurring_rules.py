"""Pure validation and calendar-walk tests for recurring Todos."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from runtime.orchestrator.schedule_rules import (
    next_recurring_occurrence,
    validate_recurring_rule,
)


def _rule(**overrides: object) -> dict:
    rule = {
        "freq": "DAILY",
        "interval": 1,
        "anchor_date": "2026-01-01",
        "time": "09:00",
        "tz": "UTC",
        "until": None,
        "count": None,
    }
    rule.update(overrides)
    return rule


def _error_code(rule: dict) -> str:
    error = validate_recurring_rule(rule)
    assert error is not None
    return error.code


@pytest.mark.parametrize("freq", ["DAILY", "YEARLY"])
@pytest.mark.parametrize("field,value", [("byday", ["MO"]), ("bymonthday", 1), ("ordinal", "first")])
def test_daily_and_yearly_reject_frequency_specific_fields(freq, field, value):
    assert _error_code(_rule(freq=freq, **{field: value})) == "invalid_freq_fields"


def test_weekly_rejects_monthly_fields_even_with_valid_byday():
    assert _error_code(_rule(freq="WEEKLY", byday=["MO"], bymonthday=1)) == "invalid_freq_fields"
    assert _error_code(_rule(freq="WEEKLY", byday=["MO"], ordinal="first")) == "invalid_freq_fields"


def test_monthly_rejects_missing_or_conflicting_selectors():
    assert _error_code(_rule(freq="MONTHLY")) == "monthly_selector_missing"
    assert _error_code(
        _rule(freq="MONTHLY", bymonthday=1, byday=["MO"], ordinal="first")
    ) == "monthly_selector_conflict"


@pytest.mark.parametrize("value", [[1], -1, 0, 32])
def test_monthly_bymonthday_must_be_one_positive_calendar_day(value):
    assert _error_code(_rule(freq="MONTHLY", bymonthday=value)) == "invalid_freq_fields"


@pytest.mark.parametrize("value", [["first"], -1, "ninth"])
def test_monthly_ordinal_must_be_a_named_single_value(value):
    assert _error_code(_rule(freq="MONTHLY", byday=["MO"], ordinal=value)) == "invalid_freq_fields"


def test_monthly_ordinal_requires_exactly_one_weekday():
    assert _error_code(_rule(freq="MONTHLY", ordinal="first")) == "monthly_selector_missing"
    assert _error_code(
        _rule(freq="MONTHLY", byday=["MO", "WE"], ordinal="first")
    ) == "invalid_byday"


@pytest.mark.parametrize("interval", [0, -1, True, "1"])
def test_interval_must_be_a_positive_integer(interval):
    assert _error_code(_rule(interval=interval)) == "invalid_interval"


def test_anchor_date_is_required_and_create_context_reserves_it_for_the_service():
    assert _error_code(_rule(anchor_date="not-a-date")) == "anchor_date_not_settable"
    create_rule = _rule()
    create_rule.pop("anchor_date")
    assert validate_recurring_rule(create_rule, context="create") is None
    assert validate_recurring_rule(_rule()) is None
    assert validate_recurring_rule(_rule(), context="create").code == "anchor_date_not_settable"


def test_end_conditions_validate_as_local_calendar_values():
    assert _error_code(_rule(until="2020-01-01")) == "invalid_until"
    assert _error_code(_rule(count=0)) == "invalid_count"
    assert _error_code(_rule(until="2027-01-01", count=1)) == "end_condition_conflict"


@pytest.mark.parametrize("field,value,code", [("time", "25:00", "invalid_time"), ("tz", "Mars/Olympus", "invalid_timezone")])
def test_time_and_timezone_reuse_weekly_validation(field, value, code):
    assert _error_code(_rule(**{field: value})) == code


def test_count_never_limits_the_calendar_iterator():
    rule = _rule(anchor_date="2026-01-01", count=2)
    first = next_recurring_occurrence(rule, datetime(2025, 12, 31, tzinfo=timezone.utc))
    second = next_recurring_occurrence(rule, first)
    third = next_recurring_occurrence(rule, second)
    assert [item.date().isoformat() for item in (first, second, third)] == [
        "2026-01-01", "2026-01-02", "2026-01-03",
    ]


def test_count_is_never_passed_to_rrule(monkeypatch):
    from runtime.orchestrator import schedule_rules

    seen_kwargs = {}
    real_rrule = schedule_rules.rrule

    def recording_rrule(*args, **kwargs):
        seen_kwargs.update(kwargs)
        return real_rrule(*args, **kwargs)

    monkeypatch.setattr(schedule_rules, "rrule", recording_rrule)
    next_recurring_occurrence(_rule(count=1), datetime(2025, 12, 31, tzinfo=timezone.utc))
    assert "count" not in seen_kwargs


def test_weekly_rrule_uses_iso_monday_week_start(monkeypatch):
    from runtime.orchestrator import schedule_rules

    seen_kwargs = {}
    real_rrule = schedule_rules.rrule

    def recording_rrule(*args, **kwargs):
        seen_kwargs.update(kwargs)
        return real_rrule(*args, **kwargs)

    monkeypatch.setattr(schedule_rules, "rrule", recording_rrule)
    next_recurring_occurrence(
        _rule(freq="WEEKLY", byday=["MO"], interval=2),
        datetime(2025, 12, 31, tzinfo=timezone.utc),
    )
    assert seen_kwargs["wkst"] == schedule_rules.MO


def test_rrule_skips_nonexistent_monthly_and_yearly_dates():
    monthly = _rule(freq="MONTHLY", anchor_date="2026-01-31", bymonthday=31)
    yearly = _rule(freq="YEARLY", anchor_date="2024-02-29")
    assert next_recurring_occurrence(monthly, datetime(2026, 1, 31, 10, tzinfo=timezone.utc)).date().isoformat() == "2026-03-31"
    assert next_recurring_occurrence(yearly, datetime(2024, 2, 29, 10, tzinfo=timezone.utc)).date().isoformat() == "2028-02-29"


def test_monthly_ordinal_maps_to_rrule_bysetpos():
    rule = _rule(freq="MONTHLY", anchor_date="2026-01-01", byday=["MO"], ordinal="second")
    occurrence = next_recurring_occurrence(rule, datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert occurrence == datetime(2026, 1, 12, 9, tzinfo=timezone.utc)


def test_until_is_inclusive_by_local_date_across_fall_back():
    rule = _rule(
        anchor_date="2026-11-01",
        time="01:30",
        tz="America/New_York",
        until="2026-11-01",
    )
    occurrence = next_recurring_occurrence(rule, datetime(2026, 10, 31, tzinfo=timezone.utc))
    assert occurrence == datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)
    assert next_recurring_occurrence(rule, occurrence) is None
