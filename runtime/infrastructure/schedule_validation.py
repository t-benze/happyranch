"""Pure schedule-validation and normalization helpers.

These are unit-testable with an injected clock and do NOT touch the database.
The caller (route / scheduler) provides armed counts from the store layer
and a capability-enabled flag. All rules match the approved v1 envelope from
the THR-105 design spec and PRD.

Status vocabulary per the design spec:
  armed → firing → fired (one-shot terminal)
  armed → firing → armed (weekly cycle)
  armed → paused / cancelled / expired / failed
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import available_timezones

from runtime.models import ScheduleKind, ScheduleStatus

# --------------- defaults (match PRD §9) ---------------

MAX_ARMED_PER_AGENT = 20
MAX_ARMED_ORG_WIDE = 100
ONE_SHOT_HORIZON_DAYS = 90
DEFAULT_RECURRING_EXPIRY_DAYS = 90

_DAYS_OF_WEEK = frozenset(
    ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
)

_VALID_KINDS = frozenset(k.value for k in ScheduleKind)  # type: ignore[var-annotated]


class ValidationError(ValueError):
    """Raised when a schedule-create request violates the v1 envelope."""


def _validate_timezone(tz: str) -> None:
    if tz not in available_timezones():
        raise ValidationError(f"unsupported timezone: {tz!r}")


def _validate_kind(kind: str) -> ScheduleKind:
    if kind not in _VALID_KINDS:
        raise ValidationError(
            f"unsupported schedule kind {kind!r}; v1 supports: "
            f"{', '.join(sorted(_VALID_KINDS))}"
        )
    return ScheduleKind(kind)


_ALLOWED_RECURRENCE_KEYS = frozenset({"day", "time", "tz"})


def _validate_recurrence(kind: ScheduleKind, recurrence: dict | None) -> dict | None:
    """Validate and normalize weekly recurrence.

    Returns a clean normalized dict for weekly, or None for one_shot.
    Raises ValidationError on any v1 envelope violation.
    """
    if kind == ScheduleKind.ONE_SHOT:
        if recurrence is not None:
            raise ValidationError("one_shot schedules must not carry recurrence")
        return None

    # weekly
    if recurrence is None:
        raise ValidationError(
            "weekly schedules require a recurrence dict "
            "{day, time, tz}"
        )
    if not isinstance(recurrence, dict):
        raise ValidationError(
            "weekly recurrence must be a dict {day, time, tz}"
        )

    # Reject any extra or alternate keys
    extra_keys = set(recurrence.keys()) - _ALLOWED_RECURRENCE_KEYS
    if extra_keys:
        raise ValidationError(
            f"weekly recurrence must only contain keys {sorted(_ALLOWED_RECURRENCE_KEYS)}; "
            f"extra keys: {sorted(extra_keys)}"
        )

    day = recurrence.get("day")
    time_val = recurrence.get("time")
    tz = recurrence.get("tz")

    if not day or not time_val or not tz:
        raise ValidationError(
            "weekly recurrence must include day, time, and tz"
        )
    # Exactly one weekday
    day_str = str(day).strip()
    if day_str not in _DAYS_OF_WEEK:
        raise ValidationError(
            f"weekly recurrence day must be exactly one weekday "
            f"({', '.join(sorted(_DAYS_OF_WEEK))}), got {day_str!r}"
        )
    # Validate time format HH:MM
    _validate_time_format(str(time_val))
    _validate_timezone(str(tz))

    # Return clean normalized dict — no hidden/extra keys
    return {"day": day_str, "time": str(time_val), "tz": str(tz)}


def _validate_time_format(t: str) -> None:
    """Validate HH:MM format."""
    if not isinstance(t, str) or len(t) != 5 or t[2] != ":":
        raise ValidationError(
            f"recurrence time must be HH:MM format, got {t!r}"
        )
    hh_str, mm_str = t[0:2], t[3:5]
    try:
        hh, mm = int(hh_str), int(mm_str)
    except ValueError:
        raise ValidationError(
            f"recurrence time must be HH:MM format, got {t!r}"
        )
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValidationError(
            f"recurrence time must be valid HH:MM (00:00–23:59), got {t!r}"
        )


def validate_schedule_create(
    *,
    agent_name: str,
    kind: str,
    fire_at: datetime,
    recurrence: dict | None,
    timezone: str,
    source_instruction: str,
    normalized_brief: str,
    armed_count_agent: int,
    armed_count_org: int,
    scheduling_enabled: bool,
    now: datetime,
    target_agent: str | None = None,
    indefinite: bool = False,
    expires_at: datetime | None = None,
) -> dict:
    """Validate a schedule-create request against the v1 envelope.

    Returns a normalized dict of schedule fields ready for insertion.
    Raises ``ValidationError`` on any envelope violation.

    Parameters:
        agent_name: The creating agent (and default target).
        kind: ``"one_shot"`` or ``"weekly"``.
        fire_at: Next UTC firing instant.
        recurrence: Weekly recurrence dict {day, time, tz} or None.
        timezone: Display timezone for the founder-facing list.
        source_instruction: Verbatim NL instruction from the founder.
        normalized_brief: Structured brief that will be dispatched.
        armed_count_agent: Current armed count for this agent.
        armed_count_org: Current org-wide armed count.
        scheduling_enabled: Per-agent capability flag.
        now: Current time (injected for deterministic tests).
        target_agent: Override target (must equal agent_name in v1).
        indefinite: Founder marked this schedule as indefinite.
        expires_at: Absolute expiry time (default derived if not set).
    """
    # Capability gating
    if not scheduling_enabled:
        raise ValidationError(
            "scheduling is disabled for this agent; "
            "ask the founder to enable the scheduling capability"
        )

    # Kind validation + normalization
    schedule_kind = _validate_kind(kind)

    # Self-target check
    resolved_target = target_agent or agent_name
    if resolved_target != agent_name:
        raise ValidationError(
            "cross-agent scheduling is not supported in v1; "
            f"target_agent ({resolved_target}) must equal "
            f"agent_name ({agent_name})"
        )

    # Required fields
    if not source_instruction.strip():
        raise ValidationError("source_instruction is required")
    if not normalized_brief.strip():
        raise ValidationError("normalized_brief is required")

    # Timezone validation
    _validate_timezone(timezone)

    # Recurrence shape validation — returns normalized recurrence
    recurrence = _validate_recurrence(schedule_kind, recurrence)

    # Horizon check for one-shot
    if schedule_kind == ScheduleKind.ONE_SHOT:
        horizon = now + timedelta(days=ONE_SHOT_HORIZON_DAYS)
        if fire_at > horizon:
            raise ValidationError(
                f"one-shot fire_at exceeds {ONE_SHOT_HORIZON_DAYS}-day horizon; "
                f"max allowed: {horizon.isoformat()}"
            )

    # Recurring expiry / review
    if schedule_kind == ScheduleKind.WEEKLY:
        if not indefinite:
            expiry_boundary = now + timedelta(days=DEFAULT_RECURRING_EXPIRY_DAYS)
            if expires_at is None:
                # Default to now + 90 days when omitted
                expires_at = expiry_boundary
            elif expires_at > expiry_boundary:
                raise ValidationError(
                    f"recurring schedule expires_at exceeds the "
                    f"{DEFAULT_RECURRING_EXPIRY_DAYS}-day review window; "
                    f"max allowed: {expiry_boundary.isoformat()}"
                )

    # Caps (pre-insert count — caller passes current armed count)
    if armed_count_agent >= MAX_ARMED_PER_AGENT:
        raise ValidationError(
            f"agent cap exceeded: {armed_count_agent} armed schedules "
            f"(max {MAX_ARMED_PER_AGENT}); pause or cancel an existing schedule"
        )
    if armed_count_org >= MAX_ARMED_ORG_WIDE:
        raise ValidationError(
            f"org cap exceeded: {armed_count_org} armed schedules "
            f"(max {MAX_ARMED_ORG_WIDE}); pause or cancel an existing schedule"
        )

    return {
        "agent_name": agent_name,
        "target_agent": resolved_target,
        "kind": kind,
        "fire_at": fire_at,
        "recurrence": recurrence,
        "timezone": timezone,
        "source_instruction": source_instruction.strip(),
        "normalized_brief": normalized_brief.strip(),
        "status": ScheduleStatus.ARMED.value,
        "indefinite": indefinite,
        "expires_at": expires_at,
    }
