"""Working-hours scheduling decisions (pure, unit-testable core).

Mirrors ``dream_scheduler`` by separating the *decision* logic from the async
loop. This module holds only the decision/slot-grid functions; the
``work_hours_scheduler_loop`` async loop, the ``schedule_due_wakes`` org pass,
and FastAPI lifespan wiring are added in leg B and call the functions here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from runtime.orchestrator.org_config import (
    OrgConfigError,
    WorkHoursSchedule,
    WorkingHoursConfig,
)

_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_DAY_MINUTES = 24 * 60


def select_work_hours_agents(
    available_agents: list[str],
    config: WorkingHoursConfig,
    known_teams: set[str],
) -> list[str]:
    """Resolve the eligible agents. Mirrors ``select_dream_agents`` and ALSO
    enforces the two resolved-candidate-point checks the spec defers here:
    unknown include/exclude agent names and unknown ``teams.<team>`` keys."""
    if not config.enabled:
        return []

    available = list(dict.fromkeys(available_agents))
    available_set = set(available)

    unknown = sorted(
        {name for name in (*config.include_agents, *config.exclude_agents)
         if name not in available_set}
    )
    if unknown:
        raise OrgConfigError(
            f"working_hours.agents references unknown agents: {unknown} "
            f"(candidates: {sorted(available_set)})"
        )

    unknown_teams = sorted(team for team in config.teams if team not in known_teams)
    if unknown_teams:
        raise OrgConfigError(
            f"working_hours.teams references unknown teams: {unknown_teams} "
            f"(known: {sorted(known_teams)})"
        )

    if config.agent_mode == "whitelist":
        selected = [name for name in config.include_agents if name in available_set]
    else:
        selected = available

    excluded = set(config.exclude_agents)
    return [name for name in selected if name not in excluded]


def _interval_minutes(interval: str) -> int:
    return int(interval[:-1]) * (60 if interval.endswith("h") else 1)


def _to_minutes(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:])


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def windowed_slot_minutes(schedule: WorkHoursSchedule) -> list[int]:
    """Grid for a windowed agent on a configured day: anchored at window.start,
    stepped by interval, up to and including the last slot <= window.end."""
    start = _to_minutes(schedule.window_start)  # type: ignore[arg-type]
    end = _to_minutes(schedule.window_end)      # type: ignore[arg-type]
    return list(range(start, end + 1, _interval_minutes(schedule.interval)))


def continuous_slot_minutes(schedule: WorkHoursSchedule) -> list[int]:
    """Grid for a continuous agent: anchored at 00:00, stepped by interval
    across the full day. The divide-24h validation guarantees the last slot is
    < 24:00 and the next slot is exactly 00:00 of the following local_date."""
    return list(range(0, _DAY_MINUTES, _interval_minutes(schedule.interval)))


def current_due_slot(
    schedule: WorkHoursSchedule, now: datetime,
) -> tuple[str, str, datetime] | None:
    """The most recent valid grid slot at-or-before ``now`` on the *current*
    local_date (in the effective timezone). Returns ``(local_date, slot,
    scheduled_for)`` or ``None`` when no slot is due today (before the first
    windowed slot, or an unconfigured day for a windowed agent).

    Only the current local_date is considered — earlier slots and historical
    days are never returned, so the scheduler can never backfill or replay.
    """
    tz = ZoneInfo(schedule.timezone)
    local_now = now.astimezone(tz)
    now_minutes = local_now.hour * 60 + local_now.minute

    if schedule.mode == "continuous":
        slots = continuous_slot_minutes(schedule)
    else:
        weekday = _WEEKDAYS[local_now.weekday()]
        if schedule.days is None or weekday not in schedule.days:
            return None
        slots = windowed_slot_minutes(schedule)

    due = [m for m in slots if m <= now_minutes]
    if not due:
        return None
    slot_minutes = max(due)
    scheduled_for = local_now.replace(
        hour=slot_minutes // 60, minute=slot_minutes % 60, second=0, microsecond=0,
    )
    return local_now.date().isoformat(), _hhmm(slot_minutes), scheduled_for


@dataclass(frozen=True)
class WakeScheduleDecision:
    should_schedule: bool
    local_date: str
    slot: str
    scheduled_for: datetime
    # True when the slot must be recorded as a ``skipped`` row (startup pass
    # with catch_up disabled) so the steady-state guard suppresses it later.
    record_skipped: bool = False
    reason: str | None = None


def decide_wake(
    *,
    now: datetime,
    schedule: WorkHoursSchedule,
    existing_for_slot: object | None,
    startup: bool = False,
) -> WakeScheduleDecision:
    """Pure scheduling decision for one agent.

    Combines the current-due-slot computation, the uniqueness guard
    (``existing_for_slot`` is the ``work_hours`` row for this
    ``(agent, local_date, slot)``), and the startup catch-up rule:

    - no due slot today -> do nothing;
    - a row already exists for the slot -> do nothing (uniqueness guard);
    - startup pass with ``catch_up_on_startup`` false -> record a ``skipped``
      row, do not enqueue;
    - otherwise -> schedule the single latest due slot.
    """
    due = current_due_slot(schedule, now)
    if due is None:
        return WakeScheduleDecision(False, "", "", now, reason="no_due_slot")
    local_date, slot, scheduled_for = due

    if existing_for_slot is not None:
        return WakeScheduleDecision(
            False, local_date, slot, scheduled_for, reason="already_exists",
        )
    if startup and not schedule.catch_up_on_startup:
        return WakeScheduleDecision(
            False, local_date, slot, scheduled_for,
            record_skipped=True, reason="catch_up_disabled",
        )
    return WakeScheduleDecision(True, local_date, slot, scheduled_for)
