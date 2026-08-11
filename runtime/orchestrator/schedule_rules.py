"""Pure validation and recurrence helpers for agent schedules (THR-105 Phase 1).

No I/O, no database access — unit-testable rules that encode the v1 envelope:
one-shot absolute time with 90-day horizon, simple weekly recurrence
(exactly one weekday + HH:MM + timezone), expiry defaults, and next-occurrence
computation.

Reuses the weekday/timezone walking approach from
``runtime.daemon.work_hours_scheduler`` without modifying working-hours behavior.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from dateutil.rrule import DAILY, MONTHLY, WEEKLY, YEARLY, FR, MO, SA, SU, TH, TU, WE, rrule

_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# ----------------------------------------------------------------- validation

_WEEKDAY_NAMES = frozenset(_WEEKDAYS)

# Recurrence dict shape for weekly: {"day": "Sat", "time": "09:00", "tz": "Asia/Shanghai"}
_WeeklyRecurrence = dict[Literal["day", "time", "tz"], str]


@dataclass(frozen=True)
class RecurrenceValidationError:
    """A stable, API-ready recurrence-rule validation failure."""

    code: str


_RECURRING_FIELDS = frozenset({
    "freq", "interval", "anchor_date", "byday", "bymonthday", "ordinal",
    "time", "tz", "until", "count",
})
_RRULE_WEEKDAYS = {"MO": MO, "TU": TU, "WE": WE, "TH": TH, "FR": FR, "SA": SA, "SU": SU}
_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "last": -1}
_RRULE_FREQUENCIES = {"DAILY": DAILY, "WEEKLY": WEEKLY, "MONTHLY": MONTHLY, "YEARLY": YEARLY}


def _valid_hhmm(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        return False
    try:
        hour = int(value[:2])
        minute = int(value[3:])
    except ValueError:
        return False
    return 0 <= hour <= 23 and 0 <= minute <= 59


def _valid_timezone(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        ZoneInfo(value)
    except Exception:
        return False
    return True


def _parse_iso_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def _valid_byday(value: object, *, exact_count: int | None = None) -> bool:
    if not isinstance(value, list) or not value or any(not isinstance(day, str) for day in value):
        return False
    if exact_count is not None and len(value) != exact_count:
        return False
    return len(value) == len(set(value)) and all(day in _RRULE_WEEKDAYS for day in value)


def validate_recurring_rule(
    rule: dict,
    *,
    context: Literal["stored", "create"] = "stored",
    now: datetime | None = None,
) -> RecurrenceValidationError | None:
    """Validate the stored RRULE-subset grammar without performing I/O.

    ``context="create"`` is deliberately a pre-materialization seam for Phase 3:
    callers validate an untrusted payload there (where ``anchor_date`` is forbidden),
    let the service compute the anchor, then validate the complete stored rule.
    """
    if not isinstance(rule, dict) or set(rule) - _RECURRING_FIELDS:
        return RecurrenceValidationError("invalid_freq_fields")
    if context not in {"stored", "create"}:
        raise ValueError(f"unknown recurrence validation context: {context!r}")
    if context == "create" and "anchor_date" in rule:
        return RecurrenceValidationError("anchor_date_not_settable")

    freq = rule.get("freq")
    if freq not in _RRULE_FREQUENCIES:
        return RecurrenceValidationError("invalid_freq_fields")
    interval = rule.get("interval")
    if isinstance(interval, bool) or not isinstance(interval, int) or interval < 1:
        return RecurrenceValidationError("invalid_interval")
    if context == "stored" and _parse_iso_date(rule.get("anchor_date")) is None:
        return RecurrenceValidationError("anchor_date_not_settable")
    if not _valid_hhmm(rule.get("time")):
        return RecurrenceValidationError("invalid_time")
    if not _valid_timezone(rule.get("tz")):
        return RecurrenceValidationError("invalid_timezone")

    has_byday = rule.get("byday") is not None
    has_bymonthday = rule.get("bymonthday") is not None
    has_ordinal = rule.get("ordinal") is not None
    if freq in {"DAILY", "YEARLY"} and (has_byday or has_bymonthday or has_ordinal):
        return RecurrenceValidationError("invalid_freq_fields")
    if freq == "WEEKLY":
        if has_bymonthday or has_ordinal:
            return RecurrenceValidationError("invalid_freq_fields")
        if not _valid_byday(rule.get("byday")):
            return RecurrenceValidationError("invalid_byday")
    if freq == "MONTHLY":
        positional = has_byday or has_ordinal
        if has_bymonthday and positional:
            return RecurrenceValidationError("monthly_selector_conflict")
        if not has_bymonthday and not positional:
            return RecurrenceValidationError("monthly_selector_missing")
        if has_bymonthday:
            value = rule.get("bymonthday")
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 31:
                return RecurrenceValidationError("invalid_freq_fields")
        else:
            if not has_byday or not has_ordinal:
                return RecurrenceValidationError("monthly_selector_missing")
            if not _valid_byday(rule.get("byday"), exact_count=1):
                return RecurrenceValidationError("invalid_byday")
            if not isinstance(rule.get("ordinal"), str) or rule["ordinal"] not in _ORDINALS:
                return RecurrenceValidationError("invalid_freq_fields")

    has_until = rule.get("until") is not None
    has_count = rule.get("count") is not None
    if has_until and has_count:
        return RecurrenceValidationError("end_condition_conflict")
    if has_until:
        until = _parse_iso_date(rule.get("until"))
        local_now = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(rule["tz"])).date()
        if until is None or until < local_now:
            return RecurrenceValidationError("invalid_until")
    if has_count:
        count = rule.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            return RecurrenceValidationError("invalid_count")
    return None


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
    if not _valid_hhmm(time_val):
        return f"recurrence.time must be valid HH:MM, got {time_val!r}"

    tz_val = recurrence.get("tz", "")
    if not isinstance(tz_val, str) or not tz_val:
        return "recurrence.tz must be a non-empty timezone string"
    if not _valid_timezone(tz_val):
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
    kind: Literal["one_shot", "weekly", "recurring"],
    indefinite: bool = False,
) -> datetime | None:
    """Return the default expires_at for a new schedule.

    - one_shot: no expiry (terminal after fire).
    - weekly/recurring: created_at + 90 days, unless indefinite is explicitly True
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
                # Design contract: persisted fire_at values are UTC instants.
                return candidate.astimezone(timezone.utc)
        day_start += timedelta(days=1)
    return None


# ------------------------------------------------ recurring RRULE occurrence

def _localize_or_skip(naive_local_dt: datetime, tz: str) -> datetime | None:
    """Attach ``tz`` to a civil-time candidate, skipping spring-forward gaps.

    Leaving ``fold`` at Python's default zero deliberately selects the first
    instance of a fall-back ambiguous wall time.
    """
    local_tz = ZoneInfo(tz)
    localized = naive_local_dt.replace(tzinfo=local_tz)
    round_tripped = localized.astimezone(timezone.utc).astimezone(local_tz)
    if round_tripped.replace(tzinfo=None) != naive_local_dt:
        return None
    return localized


def next_recurring_occurrence(rule: dict, after: datetime) -> datetime | None:
    """Return the next valid recurring occurrence strictly after ``after`` in UTC.

    The calendar iterator receives only cadence fields and local-date ``until``.
    In particular, the service-level successful-dispatch ``count`` is never
    passed to :func:`dateutil.rrule.rrule` and cannot bound candidate generation.
    """
    anchor_date = _parse_iso_date(rule["anchor_date"])
    if anchor_date is None:
        return None
    tz_name = rule["tz"]
    try:
        local_tz = ZoneInfo(tz_name)
    except Exception:
        return None
    time_value = rule["time"]
    dtstart = datetime.combine(anchor_date, time(int(time_value[:2]), int(time_value[3:])))
    kwargs: dict[str, object] = {
        "dtstart": dtstart,
        "interval": rule["interval"],
    }
    if rule["freq"] == "WEEKLY":
        kwargs["wkst"] = MO
    byday = rule.get("byday")
    if byday is not None:
        kwargs["byweekday"] = tuple(_RRULE_WEEKDAYS[day] for day in byday)
    bymonthday = rule.get("bymonthday")
    if bymonthday is not None:
        kwargs["bymonthday"] = bymonthday
    ordinal = rule.get("ordinal")
    if ordinal is not None:
        kwargs["bysetpos"] = _ORDINALS[ordinal]
    until = rule.get("until")
    if until is not None:
        until_date = _parse_iso_date(until)
        if until_date is None:
            return None
        kwargs["until"] = datetime.combine(until_date, time.max)

    calendar = rrule(_RRULE_FREQUENCIES[rule["freq"]], **kwargs)
    if after.tzinfo is None:
        after = after.replace(tzinfo=timezone.utc)
    search_after = after.astimezone(local_tz).replace(tzinfo=None)
    while candidate := calendar.after(search_after, inc=False):
        localized = _localize_or_skip(candidate, tz_name)
        if localized is not None:
            return localized.astimezone(timezone.utc)
        search_after = candidate
    return None
