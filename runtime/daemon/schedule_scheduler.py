"""Schedule firing decisions (pure, unit-testable core).

Mirrors ``work_hours_scheduler`` by separating the *decision* logic from the
async loop. This module holds only the decision/due functions; the
``schedule_scheduler_loop`` async loop, the ``schedule_due_schedules`` org pass,
and FastAPI lifespan wiring are added here and call the functions from
``schedule_store``.
"""
from __future__ import annotations

from runtime.orchestrator.producer_move import (
    native_writer, native_claim, owner_for, require_producer_admission, producer_admission_writer,
    producer_admission_validation,
)
from runtime.daemon.startup_recovery import require_ready, StartupUnavailable
from runtime.orchestrator.team_move import DeferredTeamMove


import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from runtime.daemon.metrics_store import maybe_persist_metrics_snapshot
from runtime.daemon.schedule_queue import ScheduleJob
from runtime.infrastructure.database import Database
from runtime.models import ScheduleKind, ScheduleStatus
from runtime.orchestrator.schedule_rules import (
    next_schedule_occurrence,
    recurrence_until_exhausted,
)

logger = logging.getLogger(__name__)


_RECURRENCE_STALE_TOLERANCE = timedelta(seconds=120)


async def _enqueue(org, schedule_id: str) -> None:
    try:
        require_producer_admission(org)
        await org.schedule_queue.put(ScheduleJob(org_slug=org.slug, schedule_id=schedule_id))
    except BaseException as exc:
        try:
            logger.exception("committed schedule source %s remains firing; queue delivery unacknowledged", schedule_id)
        except BaseException as secondary:
            exc.add_note(f"queue refusal logging failed: {secondary!r}")
        raise


def _enqueue_finished(task: asyncio.Task, source_id: str) -> None:
    # Cancellation before coroutine entry must also report the committed intent.
    if task.cancelled():
        logger.error("committed source %s: queue delivery cancelled/unacknowledged", source_id)
    else:
        task.exception()  # _enqueue already reported the original source failure.


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
    validate_admission = producer_admission_validation(org)
    admission = owner_for(org.db)
    if admission is not None:
        try:
            admission.redeliver_after_release(org)
        except DeferredTeamMove:
            pass
    validate_admission()
    if startup:
        with org.db._lock:
            validate_admission()
            recovered = org.db.schedules.recover_firing(validate_admission=validate_admission)
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
        try:
            with producer_admission_writer(org, native_claim(org.db, record.agent_name)):
                validate_admission()
                # Weekly no-replay/backfill: if fire_at is stale (missed during
                # daemon downtime), advance to the next occurrence without firing.
                if (
                    record.kind in (ScheduleKind.WEEKLY, ScheduleKind.RECURRING)
                    and record.fire_at < now - _RECURRENCE_STALE_TOLERANCE
                ):
                    recurrence = record.recurrence
                    next_fire = next_schedule_occurrence(record.kind.value, recurrence, after=now)
                    exhausted = next_fire is None and recurrence_until_exhausted(recurrence)
                    # native_claim retains the lock only with a native owner.
                    # Renew after the real store wait in the absent-owner path too.
                    with org.db._lock:
                        validate_admission()
                        if exhausted:
                            store.update(
                                record.id, status=ScheduleStatus.FIRED, active=0,
                                end_reason="date_ended",
                            )
                            org.db.insert_audit_log(
                                task_id=record.id, agent=record.agent_name,
                                action="schedule_fired", payload={"end_reason": "date_ended"},
                            )
                        elif (
                            next_fire is not None
                            and record.expires_at is not None
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
                        elif next_fire is None:
                            store.update(
                                record.id, status=ScheduleStatus.FAILED, active=0,
                                error="recurrence_no_candidate",
                            )
                            org.db.insert_audit_log(
                                task_id=record.id, agent=record.agent_name,
                                action="schedule_failed", payload={"reason": "recurrence_no_candidate"},
                            )
                        else:
                            store.update(
                                record.id, fire_at=next_fire, status=ScheduleStatus.ARMED, active=1,
                            )
                            org.db.insert_audit_log(
                                task_id=record.id, agent=record.agent_name,
                                action="occurrence_missed", payload={"kind": record.kind.value},
                            )
                    continue

                # Claim the row: armed → firing. A concurrent winner changes the
                # status first, so this caller skips the row without side effects.
                try:
                    with org.db._lock:
                        validate_admission()
                        if not store.claim_firing(record.id):
                            continue
                except DeferredTeamMove:
                    raise
                except StartupUnavailable:
                    raise
                except Exception:
                    logger.exception(
                        "schedule_due_schedules: failed to claim %s", record.id,
                    )
                    continue
                org.db.insert_audit_log(
                    task_id=record.id,
                    agent=record.agent_name,
                    action="schedule_claimed",
                    payload={"kind": record.kind.value, "fire_at": record.fire_at.isoformat()},
                )

                # Enqueue for the runner/worker loop.
                try:
                    loop = asyncio.get_running_loop()
                    require_producer_admission(org)
                    loop.create_task(_enqueue(org, record.id)).add_done_callback(
                        lambda task, source_id=record.id: _enqueue_finished(task, source_id),
                    )
                except StartupUnavailable:
                    raise
                except RuntimeError:
                    # Explicit standalone caller without a loop.
                    org.schedule_queue.put_nowait(
                        ScheduleJob(org_slug=org.slug, schedule_id=record.id),
                    )
                count += 1
        except DeferredTeamMove:
            continue
    return count


async def schedule_scheduler_loop(state, *, interval_seconds: int = 60) -> None:
    """Async scheduling loop: every ~60s, scan for due schedules.

    The first iteration is the startup catch-up pass: recover stale FIRING rows
    then process any due schedules that may have been missed during downtime.

    Mirrors ``work_hours_scheduler_loop``.
    """
    startup = True
    while True:
        require_ready(state)
        t0 = time.monotonic()
        now = datetime.now(timezone.utc)
        for org in list(state.orgs.values()):
            require_producer_admission(org)
            try:
                from runtime.orchestrator.producer_move import redeliver_native_jobs
                await redeliver_native_jobs(org)
                require_producer_admission(org)
                schedule_due_schedules(org=org, now=now, startup=startup)
            except StartupUnavailable:
                raise
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
