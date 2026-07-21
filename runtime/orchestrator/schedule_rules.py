"""Pure validation and recurrence helpers for agent schedules (THR-105 Phase 1).

No I/O, no database access — unit-testable rules that encode the v1 envelope:
one-shot absolute time with 90-day horizon, simple weekly recurrence
(exactly one weekday + HH:MM + timezone), expiry defaults, and next-occurrence
computation.

Reuses the weekday/timezone walking approach from
``runtime.daemon.work_hours_scheduler`` without modifying working-hours behavior.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# ----------------------------------------------------------------- validation

_WEEKDAY_NAMES = frozenset(_WEEKDAYS)

# Recurrence dict shape for weekly: {"day": "Sat", "time": "09:00", "tz": "Asia/Shanghai"}
_WeeklyRecurrence = dict[Literal["day", "time", "tz"], str]


def validate_weekly_recurrence(recurrence: dict | None) -> _WeeklyRecurrence | None:
    """Return None on success, or an error string describing the violation.

    v1 weekly recurrence must be exactly one weekday, one HH:MM local time,
    and a valid timezone.  Multi-weekday, cron, arbitrary intervals, and
    missing fields are rejected.
    """
    if recurrence is None:
        return "recurrence must not be null for weekly schedules"
    if not isinstance(recurrence, dict):
        return "recurrence must be a JSON object"
    keys = set(recurrence.keys())
    required = {"day", "time", "tz"}
    if keys != required:
        return f"recurrence must have exactly keys {sorted(required)}, got {sorted(keys)}"

    day = recurrence.get("day", "")
    if not isinstance(day, str) or day.lower() not in _WEEKDAY_NAMES:
        return f"recurrence.day must be a valid weekday (mon-sun), got {day!r}"

    time_val = recurrence.get("time", "")
    if not isinstance(time_val, str) or len(time_val) != 5 or time_val[2] != ":":
        return f"recurrence.time must be HH:MM, got {time_val!r}"
    try:
        hour = int(time_val[:2])
        minute = int(time_val[3:])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except (ValueError, IndexError):
        return f"recurrence.time must be valid HH:MM, got {time_val!r}"

    tz_val = recurrence.get("tz", "")
    if not isinstance(tz_val, str) or not tz_val:
        return "recurrence.tz must be a non-empty timezone string"
    try:
        ZoneInfo(tz_val)
    except Exception:
        return f"recurrence.tz is not a valid timezone: {tz_val!r}"

    return None  # success


# ----------------------------------------------------------------- horizon

_ONE_SHOT_MAX_HORIZON_DAYS = 90


def validate_one_shot_horizon(fire_at: datetime, now: datetime) -> str | None:
    """Return None if ``fire_at`` is within the v1 one-shot horizon, or an error."""
    if fire_at <= now:
        return "fire_at must be in the future"
    max_fire = now + timedelta(days=_ONE_SHOT_MAX_HORIZON_DAYS)
    if fire_at > max_fire:
        return f"one-shot fire_at must be within {_ONE_SHOT_MAX_HORIZON_DAYS} days"
    return None


# ----------------------------------------------------- expiry default

_RECURRING_EXPIRY_DAYS = 90


def default_expires_at(
    created_at: datetime,
    kind: Literal["one_shot", "weekly"],
    indefinite: bool = False,
) -> datetime | None:
    """Return the default expires_at for a new schedule.

    - one_shot: no expiry (terminal after fire).
    - weekly: created_at + 90 days, unless indefinite is explicitly True
      (founder-set only), in which case None.
    """
    if kind == "one_shot":
        return None
    if indefinite:
        return None
    return created_at + timedelta(days=_RECURRING_EXPIRY_DAYS)


# -------------------------------------------------- caps (constant envelope)

MAX_ARMED_PER_AGENT = 20
MAX_ARMED_ORG = 100


def validate_caps(
    agent_armed_count: int,
    org_armed_count: int,
) -> str | None:
    """Return None if both caps are not exceeded, or an actionable error."""
    if agent_armed_count >= MAX_ARMED_PER_AGENT:
        return (
            f"agent has {agent_armed_count} armed schedules "
            f"(max {MAX_ARMED_PER_AGENT}). Pause or cancel an existing one."
        )
    if org_armed_count >= MAX_ARMED_ORG:
        return (
            f"org has {org_armed_count} armed schedules "
            f"(max {MAX_ARMED_ORG}). Pause or cancel an existing one."
        )
    return None


# ----------------------------------------------- next weekly occurrence

_SENTINEL_DATE = datetime(1970, 1, 1)


def next_weekly_occurrence(
    day: str,
    time_str: str,
    tz_name: str,
    after: datetime | None = None,
) -> datetime | None:
    """Return the next occurrence of ``day`` at ``time_str`` in ``tz_name``
    strictly after ``after`` (default: now UTC).

    Walks at most 366 days forward so a misconfigured tz/loop can never run
    forever.  Returns None if no occurrence is found within that window.
    """
    if after is None:
        after = datetime.now(timezone.utc)
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        return None

    target_weekday = _WEEKDAYS.index(day.lower())
    hour = int(time_str[:2])
    minute = int(time_str[3:5])

    local_after = after.astimezone(tz)
    # Anchor from a fixed sentinel: compute the target occurrence for the
    # first week containing ``after``.
    day_start = local_after.date()
    for _ in range(366):
        if day_start.weekday() == target_weekday:
            candidate = datetime(
                day_start.year, day_start.month, day_start.day,
                hour, minute, tzinfo=tz,
            )
            if candidate > local_after:
                return candidate
        day_start += timedelta(days=1)
    return None
