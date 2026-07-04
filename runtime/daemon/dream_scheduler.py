"""Nightly dream scheduling decisions."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo

from runtime.daemon.dream_queue import DreamJob
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.models import DreamRecord, DreamStatus
from runtime.orchestrator import prompt_loader
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.org_config import (
    DreamingConfig,
    OrgConfigError,
    load_org_config,
    resolve_dreaming_timezone,
    resolve_timezone_or_local,
)

logger = logging.getLogger(__name__)


def select_dream_agents(
    available_agents: list[str],
    config: DreamingConfig,
) -> list[str]:
    if not config.enabled:
        return []

    available = list(dict.fromkeys(available_agents))
    available_set = set(available)

    # Spec "Org Configuration" rule 5: unknown include/exclude names fail config
    # validation so typos do not silently skip agents. Validated here (not at
    # org-config load) because this is the only point with the resolved candidate
    # agent list — approved agent files with existing workspaces.
    unknown = sorted(
        {name for name in (*config.include_agents, *config.exclude_agents)
         if name not in available_set}
    )
    if unknown:
        raise OrgConfigError(
            f"dreaming.agents references unknown agents: {unknown} "
            f"(candidates: {sorted(available_set)})"
        )

    if config.agent_mode == "whitelist":
        selected = [name for name in config.include_agents if name in available_set]
    else:
        selected = available

    excluded = set(config.exclude_agents)
    return [name for name in selected if name not in excluded]


@dataclass(frozen=True)
class DreamScheduleDecision:
    should_schedule: bool
    local_date: str
    scheduled_for: datetime
    reason: str | None = None


def _scheduled_datetime(
    now: datetime, config: DreamingConfig, tz: tzinfo | None = None,
) -> tuple[str, datetime]:
    # ``tz`` is the effective zone resolved by the caller (which has the full
    # OrgConfig for the org.timezone inheritance step). When absent, resolve
    # from this DreamingConfig's own timezone (a None there falls back to
    # machine-local -> UTC) so a None never reaches ``ZoneInfo`` directly.
    if tz is None:
        tz = resolve_timezone_or_local(config.timezone)
    local_now = now.astimezone(tz)
    hour, minute = [int(part) for part in config.schedule_time.split(":", 1)]
    scheduled = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return scheduled.date().isoformat(), scheduled


def should_schedule_for_agent(
    *,
    agent_name: str,
    now: datetime,
    config: DreamingConfig,
    existing_for_date: DreamRecord | None,
    tz: tzinfo | None = None,
) -> DreamScheduleDecision:
    local_date, scheduled = _scheduled_datetime(now, config, tz)
    if existing_for_date is not None:
        return DreamScheduleDecision(False, local_date, scheduled, "already_exists")
    if now.astimezone(scheduled.tzinfo) < scheduled:
        return DreamScheduleDecision(False, local_date, scheduled, "not_due")
    return DreamScheduleDecision(True, local_date, scheduled, None)


def _available_agents(org_state) -> list[str]:
    paths = OrgPaths(root=org_state.root)
    agents = []
    for agent in prompt_loader.list_agents(paths):
        if (org_state.root / "workspaces" / agent.name).exists():
            agents.append(agent.name)
    return agents


def _window_start(org_state, agent_name: str, window_end):
    prior = org_state.db.get_last_successful_dream(agent_name)
    if prior and prior.ended_at:
        return prior.ended_at
    return window_end - timedelta(hours=24)


async def _enqueue(org, dream_id: str) -> None:
    await org.dream_queue.put(DreamJob(org_slug=org.slug, dream_id=dream_id))


def schedule_due_dreams(*, org, now, startup: bool = False) -> int:
    """Schedule due dreams for an org.

    ``startup`` distinguishes the one-time startup catch-up pass from the
    steady-state loop. At startup, an already-passed dream is only enqueued
    when ``catch_up_on_startup`` is true; otherwise a ``skipped`` row is
    recorded so the steady-state loop will not pick it up later the same day.
    The steady-state loop (``startup=False``) always enqueues due dreams.
    """
    org_cfg = load_org_config(OrgPaths(root=org.root))
    cfg = org_cfg.dreaming
    # Resolve the effective zone ONCE here, the only point with the full
    # OrgConfig: dreaming.timezone -> org.timezone -> machine-local -> UTC.
    tz = resolve_dreaming_timezone(org_cfg)
    selected = select_dream_agents(_available_agents(org), cfg)
    count = 0
    for agent in selected:
        local_date, _scheduled = _scheduled_datetime(now, cfg, tz)
        existing = org.db.get_dream_for_agent_date(agent, local_date)
        decision = should_schedule_for_agent(
            agent_name=agent,
            now=now,
            config=cfg,
            existing_for_date=existing,
            tz=tz,
        )
        if not decision.should_schedule:
            continue
        dream_id = org.db.next_dream_id()
        base = dict(
            id=dream_id,
            agent_name=agent,
            local_date=decision.local_date,
            scheduled_for=decision.scheduled_for,
            window_start=_window_start(org, agent, now),
            window_end=now,
        )
        if startup and not cfg.catch_up_on_startup:
            # Missed run at startup, catch-up disabled: record a skipped row so
            # the steady-state loop's existing-row guard suppresses re-scheduling
            # today. Not enqueued, not counted.
            org.db.insert_dream(DreamRecord(status=DreamStatus.SKIPPED, **base))
            continue
        org.db.insert_dream(DreamRecord(**base))
        AuditLogger(org.db).log_dream_scheduled(dream_id, agent, local_date=decision.local_date)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_enqueue(org, dream_id))
        except RuntimeError:
            # No running loop (synchronous callers/tests): enqueue directly via
            # the unbounded queue. Avoids asyncio.run(), which resets the
            # process-global event loop to None and breaks later sync code that
            # calls asyncio.get_event_loop().
            org.dream_queue.put_nowait(DreamJob(org_slug=org.slug, dream_id=dream_id))
        count += 1
    return count


def recover_running_dreams(org) -> int:
    changed = 0
    for dream in org.db.list_dreams(limit=500):
        if dream.status == DreamStatus.RUNNING:
            org.db.update_dream(
                dream.id,
                status=DreamStatus.FAILED,
                error="daemon_restart",
                ended_at=datetime.now(timezone.utc),
            )
            changed += 1
    return changed


async def dream_scheduler_loop(state, *, interval_seconds: int = 60) -> None:
    # The first iteration runs after orgs are loaded and DB recovery has run;
    # it IS the startup catch-up check (gated by catch_up_on_startup). Every
    # subsequent iteration is steady-state on-time scheduling.
    startup = True
    while True:
        t0 = time.monotonic()
        now = datetime.now(timezone.utc)
        for org in list(state.orgs.values()):
            try:
                schedule_due_dreams(org=org, now=now, startup=startup)
            except OrgConfigError:
                # A misconfigured org (e.g. unknown include/exclude agent) must
                # not halt scheduling for every other org. Surface loudly.
                logger.exception("dream scheduling skipped for org %s: invalid dreaming config", org.slug)
        startup = False
        duration = time.monotonic() - t0
        state.metrics_registry.record_loop_tick("dream_scheduler", interval_seconds, duration)
        await asyncio.sleep(interval_seconds)
