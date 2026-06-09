"""Nightly dream scheduling decisions."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from runtime.daemon.dream_queue import DreamJob
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.models import DreamRecord, DreamStatus
from runtime.orchestrator import prompt_loader
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.org_config import DreamingConfig, load_org_config


def select_dream_agents(
    available_agents: list[str],
    config: DreamingConfig,
) -> list[str]:
    if not config.enabled:
        return []

    available = list(dict.fromkeys(available_agents))
    available_set = set(available)

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


def _scheduled_datetime(now: datetime, config: DreamingConfig) -> tuple[str, datetime]:
    tz = ZoneInfo(config.timezone)
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
) -> DreamScheduleDecision:
    local_date, scheduled = _scheduled_datetime(now, config)
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


def schedule_due_dreams(*, org, now) -> int:
    cfg = load_org_config(OrgPaths(root=org.root)).dreaming
    selected = select_dream_agents(_available_agents(org), cfg)
    count = 0
    for agent in selected:
        local_date, _scheduled = _scheduled_datetime(now, cfg)
        existing = org.db.get_dream_for_agent_date(agent, local_date)
        decision = should_schedule_for_agent(
            agent_name=agent,
            now=now,
            config=cfg,
            existing_for_date=existing,
        )
        if not decision.should_schedule:
            continue
        dream_id = org.db.next_dream_id()
        dream = DreamRecord(
            id=dream_id,
            agent_name=agent,
            local_date=decision.local_date,
            scheduled_for=decision.scheduled_for,
            window_start=_window_start(org, agent, now),
            window_end=now,
        )
        org.db.insert_dream(dream)
        AuditLogger(org.db).log_dream_scheduled(dream_id, agent, local_date=decision.local_date)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_enqueue(org, dream_id))
        except RuntimeError:
            asyncio.run(_enqueue(org, dream_id))
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
    while True:
        now = datetime.now(timezone.utc)
        for org in list(state.orgs.values()):
            schedule_due_dreams(org=org, now=now)
        await asyncio.sleep(interval_seconds)
