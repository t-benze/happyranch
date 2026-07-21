"""Schedule firing decisions (pure, unit-testable core).

Mirrors ``work_hours_scheduler`` by separating the *decision* logic from the
async loop. This module holds only the decision/due functions; the
``schedule_scheduler_loop`` async loop, the ``schedule_due_schedules`` org pass,
and FastAPI lifespan wiring are added here and call the functions from
``schedule_store``.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from runtime.daemon.metrics_store import maybe_persist_metrics_snapshot
from runtime.daemon.schedule_queue import ScheduleJob
from runtime.infrastructure.database import Database
from runtime.models import ScheduleKind, ScheduleStatus
from runtime.orchestrator.schedule_rules import next_weekly_occurrence

logger = logging.getLogger(__name__)


_WEEKLY_STALE_TOLERANCE = timedelta(seconds=120)


def schedule_due_schedules(
    *,
    org,
    now: datetime,
    startup: bool = False,
) -> int:
    """Schedule due schedule fires for an org.

    For each due one-shot schedule (armed, fire_at <= now): claim it
    (transition armed → firing) so repeated scheduler ticks cannot spawn more
    than one task for the same firing, then enqueue a ScheduleJob.

    Weekly schedules are handled differently to prevent replay/backfill of
    occurrences missed during daemon downtime. For a weekly schedule whose
    fire_at is stale (more than ``_WEEKLY_STALE_TOLERANCE`` past ``now``),
    the fire_at is advanced to the next weekly occurrence or the schedule
    is expired (when the next occurrence exceeds expires_at). Only weekly
    schedules within the tolerance window are claimed (armed → firing) and
    enqueued.

    The claim-and-enqueue within the same tick is the duplicate-fire guard:
    the scheduler transitions to FIRING before enqueuing, so the next tick
    (or a restart catch-up) sees FIRING, not ARMED, and skips it.

    At startup (``startup=True``), stale FIRING rows from a prior daemon crash
    are recovered first via ``ScheduleStore.recover_firing()``, so the scheduler
    never re-fires an already-claimed row.
    """
    if startup:
        recovered = org.db.schedules.recover_firing()
        for schedule_id, agent_name in recovered:
            org.db.insert_audit_log(
                task_id=schedule_id,
                agent=agent_name,
                action="schedule_failed",
                payload={"reason": "daemon_restart"},
            )

    store = org.db.schedules
    due_records = store.list_due(now)
    count = 0
    for record in due_records:
        # Weekly no-replay/backfill: if fire_at is stale (missed during
        # daemon downtime), advance to the next occurrence without firing.
        if record.kind == ScheduleKind.WEEKLY and record.fire_at < now - _WEEKLY_STALE_TOLERANCE:
            recurrence = record.recurrence
            if recurrence is not None:
                next_fire = next_weekly_occurrence(
                    recurrence["day"], recurrence["time"], recurrence["tz"],
                    after=now,
                )
                if next_fire is None or (
                    record.expires_at is not None
                    and record.indefinite != 1
                    and next_fire > record.expires_at
                ):
                    org.db.insert_audit_log(
                        task_id=record.id,
                        agent=record.agent_name,
                        action="schedule_expired",
                        payload={"kind": record.kind.value},
                    )
                    store.update(
                        record.id,
                        status=ScheduleStatus.EXPIRED,
                        active=0,
                    )
                else:
                    store.update(
                        record.id,
                        fire_at=next_fire,
                        status=ScheduleStatus.ARMED,
                        active=1,
                    )
            else:
                org.db.insert_audit_log(
                    task_id=record.id,
                    agent=record.agent_name,
                    action="schedule_expired",
                    payload={"kind": record.kind.value},
                )
                store.update(
                    record.id,
                    status=ScheduleStatus.EXPIRED,
                    active=0,
                )
            continue

        # Claim the row: armed → firing. If the update fails (row already
        # claimed by a concurrent tick), skip it. The list_due query only
        # returns armed rows, so a race would mean it's no longer armed.
        try:
            store.update(record.id, status=ScheduleStatus.FIRING)
        except Exception:
            logger.exception(
                "schedule_due_schedules: failed to claim %s", record.id,
            )
            continue

        # Enqueue for the runner/worker loop.
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                org.schedule_queue.put(
                    ScheduleJob(org_slug=org.slug, schedule_id=record.id),
                )
            )
        except RuntimeError:
            # No running loop (sync callers/tests): enqueue directly.
            org.schedule_queue.put_nowait(
                ScheduleJob(org_slug=org.slug, schedule_id=record.id),
            )
        count += 1
    return count


async def schedule_scheduler_loop(state, *, interval_seconds: int = 60) -> None:
    """Async scheduling loop: every ~60s, scan for due schedules.

    The first iteration is the startup catch-up pass: recover stale FIRING rows
    then process any due schedules that may have been missed during downtime.

    Mirrors ``work_hours_scheduler_loop``.
    """
    startup = True
    while True:
        t0 = time.monotonic()
        now = datetime.now(timezone.utc)
        for org in list(state.orgs.values()):
            try:
                schedule_due_schedules(org=org, now=now, startup=startup)
            except Exception:
                logger.exception(
                    "schedule scheduling skipped for org %s",
                    org.slug,
                )
        startup = False
        duration = time.monotonic() - t0
        state.metrics_registry.record_loop_tick(
            "schedule_scheduler", interval_seconds, duration,
        )

        maybe_persist_metrics_snapshot(state, now)

        await asyncio.sleep(interval_seconds)
