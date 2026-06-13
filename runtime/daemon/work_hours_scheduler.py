"""Working-hours scheduling decisions (pure, unit-testable core).

Mirrors ``dream_scheduler`` by separating the *decision* logic from the async
loop. This module holds only the decision/slot-grid functions; the
``work_hours_scheduler_loop`` async loop, the ``schedule_due_wakes`` org pass,
and FastAPI lifespan wiring are added in leg B and call the functions here.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from runtime.daemon.wake_queue import WakeJob
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.models import WorkHourRecord, WorkHourStatus
from runtime.orchestrator import prompt_loader
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.org_config import (
    OrgConfigError,
    WorkHoursSchedule,
    WorkingHoursConfig,
    load_org_config,
)
from runtime.orchestrator.routine_parser import RoutineParseResult, parse_routines

logger = logging.getLogger(__name__)

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


# --- Async scheduling loop (mirrors dream_scheduler) -------------------------


def _available_agents(org) -> list[str]:
    """Candidate agents: approved agent files with an existing workspace."""
    paths = OrgPaths(root=org.root)
    agents = []
    for agent in prompt_loader.list_agents(paths):
        if (org.root / "workspaces" / agent.name).exists():
            agents.append(agent.name)
    return agents


def _agent_routines(org, agent_name: str) -> RoutineParseResult:
    """Parse the agent's ``## Routine Tasks`` section. Absent agent file or
    section yields ``present=False`` (``has_wake=False``)."""
    agent_def = prompt_loader.load_agent(OrgPaths(root=org.root), agent_name)
    if agent_def is None:
        return RoutineParseResult(present=False, preamble="", routines=[], dropped=0)
    return parse_routines(agent_def.system_prompt)


def _agent_team(org, agent_name: str) -> str | None:
    registry = getattr(org, "teams", None)
    if registry is None:
        return None
    return registry.team_for_agent(agent_name) or registry.team_for_manager(agent_name)


async def _enqueue(org, work_hour_id: str) -> None:
    await org.wake_queue.put(WakeJob(org_slug=org.slug, work_hour_id=work_hour_id))


def schedule_due_wakes(*, org, now: datetime, startup: bool = False) -> int:
    """Schedule due working-hours wakes for an org.

    For each selected agent: resolve its effective schedule, find the current
    due slot, gate on a present-and-non-empty ``## Routine Tasks`` section
    (absent/empty -> skip silently, no row), apply the uniqueness guard, and at
    startup honor ``catch_up_on_startup`` (false -> record a ``skipped`` row so
    the steady-state loop won't re-pick the slot today).
    """
    cfg = load_org_config(OrgPaths(root=org.root)).working_hours
    if not cfg.enabled:
        return 0
    known_teams = set(org.teams.teams()) if getattr(org, "teams", None) else set()
    selected = select_work_hours_agents(_available_agents(org), cfg, known_teams)
    count = 0
    for agent in selected:
        schedule = cfg.resolve_for(agent, _agent_team(org, agent))
        due = current_due_slot(schedule, now)
        if due is None:
            continue
        # Routine-presence is the outermost precondition: an agent with no
        # routines never accrues a work_hours row (not even a skipped one).
        parsed = _agent_routines(org, agent)
        if not parsed.has_wake:
            continue
        local_date, slot, scheduled_for = due
        existing = org.db.work_hours.get_for_agent_date_slot(agent, local_date, slot)
        decision = decide_wake(
            now=now, schedule=schedule, existing_for_slot=existing, startup=startup,
        )
        if not decision.should_schedule:
            if decision.record_skipped:
                org.db.work_hours.insert(WorkHourRecord(
                    id=org.db.work_hours.next_id(),
                    agent_name=agent,
                    local_date=local_date,
                    slot=slot,
                    mode=schedule.mode,
                    scheduled_for=scheduled_for,
                    status=WorkHourStatus.SKIPPED,
                    routine_count=len(parsed.routines),
                ))
            continue

        work_hour_id = org.db.work_hours.next_id()
        org.db.work_hours.insert(WorkHourRecord(
            id=work_hour_id,
            agent_name=agent,
            local_date=local_date,
            slot=slot,
            mode=schedule.mode,
            scheduled_for=scheduled_for,
            status=WorkHourStatus.PENDING,
            routine_count=len(parsed.routines),
        ))
        AuditLogger(org.db).log_work_hour_scheduled(
            work_hour_id, agent, local_date=local_date, slot=slot, mode=schedule.mode,
        )
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_enqueue(org, work_hour_id))
        except RuntimeError:
            # No running loop (sync callers/tests): enqueue directly. The queue
            # is unbounded so put_nowait never raises (LRN-005: never
            # asyncio.run() from a no-loop context — it nukes the global loop).
            org.wake_queue.put_nowait(WakeJob(org_slug=org.slug, work_hour_id=work_hour_id))
        count += 1
    return count


async def work_hours_scheduler_loop(state, *, interval_seconds: int = 60) -> None:
    # The first iteration runs after orgs are loaded and DB recovery has run; it
    # IS the startup catch-up pass (gated per agent by catch_up_on_startup).
    # Every later iteration is steady-state on-time scheduling.
    startup = True
    while True:
        now = datetime.now(timezone.utc)
        for org in list(state.orgs.values()):
            try:
                schedule_due_wakes(org=org, now=now, startup=startup)
            except OrgConfigError:
                logger.exception(
                    "work-hours scheduling skipped for org %s: invalid working_hours config",
                    org.slug,
                )
        startup = False
        await asyncio.sleep(interval_seconds)
