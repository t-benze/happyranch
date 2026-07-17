"""Working-hours endpoints: founder list/show + the agent spawn callback.

Founder-facing GET routes (``status``/``list``/``show``) mirror the dream read
routes and get TypeScript mirrors under ``web/src/lib/api/``. The agent
``spawn`` callback is the single-line ``happyranch work-hours spawn --from-file``
target: it is **single-use and slot-scoped** (accepts only a ``running``
WORKHOUR-NNN), creates one root task per routine **targeted to the waking agent
as executor on its own team** (Q2: pre-set ``assigned_agent`` honored by
run_step), records the spawned ids on the work-hours row, and marks it
``completed``. Like the dream/thread callbacks it is not browser-callable,
so it has no web mirror.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from runtime.daemon.auth import require_token
from runtime.daemon.routes._org_dep import OrgDep
from runtime.daemon.runner import enqueue_task
from runtime.daemon.state import DaemonState
from runtime.daemon.work_hours_scheduler import next_wake_slots
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.models import TaskRecord, WorkHourRecord, WorkHourStatus
from runtime.orchestrator.org_config import (
    OrgConfigError,
    WorkingHoursConfig,
    resolve_org_setting_working_hours,
)
from runtime.orchestrator.routine_parser import MAX_ROUTINES_PER_WAKE

router = APIRouter(dependencies=[require_token()])


def _wh_to_dict(wh: WorkHourRecord) -> dict:
    return {
        "work_hour_id": wh.id,
        "agent_name": wh.agent_name,
        "local_date": wh.local_date,
        "slot": wh.slot,
        "mode": wh.mode.value,
        "scheduled_for": wh.scheduled_for.isoformat(),
        "started_at": wh.started_at.isoformat() if wh.started_at else None,
        "ended_at": wh.ended_at.isoformat() if wh.ended_at else None,
        "status": wh.status.value,
        "routine_count": wh.routine_count,
        "spawned_task_ids": wh.spawned_task_ids,
        "spawned_task_count": wh.spawned_task_count,
        "summary": wh.summary,
        "transcript_path": wh.transcript_path,
        "session_id": wh.session_id,
        "error": wh.error,
        "created_at": wh.created_at.isoformat(),
    }


def _write_wake_transcript(root: Path, wh: WorkHourRecord, *, summary: str, spawned_task_ids: list[str]) -> Path:
    """Write a minimal wake transcript under ``<root>/work_hours/WORKHOUR-NNN.md``.

    The wake produces no long transcript (its only job is to dispatch), so the
    body is the wake's own ``summary``; the frontmatter carries the provenance
    the spec lists. Atomic replace mirrors ``DreamStore.write_transcript``.
    """
    target_dir = root / "work_hours"
    target_dir.mkdir(parents=True, exist_ok=True)
    frontmatter = {
        "work_hour_id": wh.id,
        "agent_name": wh.agent_name,
        "local_date": wh.local_date,
        "slot": wh.slot,
        "mode": wh.mode.value,
        "status": WorkHourStatus.COMPLETED.value,
        "routine_count": wh.routine_count,
        "spawned_task_count": len(spawned_task_ids),
        "spawned_task_ids": spawned_task_ids,
    }
    body = f"---\n{yaml.safe_dump(frontmatter, sort_keys=False)}---\n\n{summary}\n"
    target = target_dir / f"{wh.id}.md"
    fd, tmp_name = tempfile.mkstemp(dir=target_dir, prefix=f".{wh.id}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(body.encode("utf-8"))
        os.replace(tmp_name, target)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise
    return target


@router.get("/work-hours/status")
def work_hours_status(slug: str, org: OrgDep, agent: str | None = None) -> dict:
    rows = org.db.work_hours.list(agent=agent, limit=20)
    return {"recent": [_wh_to_dict(w) for w in rows]}


@router.get("/work-hours")
def list_work_hours(slug: str, org: OrgDep, agent: str | None = None, limit: int = 50) -> dict:
    rows = org.db.work_hours.list(agent=agent, limit=limit)
    return {"work_hours": [_wh_to_dict(w) for w in rows]}


@router.get("/work-hours/next-wakes")
def work_hours_next_wakes(
    slug: str, org: OrgDep, agent: str, count: int = 5,
) -> dict:
    """Preview the next N wake timestamps for an agent's RESOLVED effective
    schedule (THR-035 / TASK-967). Additive, read-only: reuses the scheduler's
    slot grid via ``next_wake_slots`` — no scheduling side effects.

    Registered BEFORE ``/work-hours/{work_hour_id}`` so the literal ``next-wakes``
    path is not captured by the id route. An incomplete/invalid schedule is
    surfaced as a 200 with ``error`` set + empty ``next_wakes`` (a preview, not
    a client fault).
    """
    count = max(1, min(count, 50))
    cfg = resolve_org_setting_working_hours(org.db, code_default=WorkingHoursConfig())
    team = None
    registry = getattr(org, "teams", None)
    if registry is not None:
        team = registry.team_for_agent(agent) or registry.team_for_manager(agent)
    try:
        schedule = cfg.resolve_for(agent, team)
    except OrgConfigError as exc:
        return {
            "agent": agent, "enabled": cfg.enabled, "timezone": None, "mode": None,
            "next_wakes": [], "error": str(exc),
        }
    now = datetime.now(timezone.utc)
    slots = next_wake_slots(schedule, now, count)
    return {
        "agent": agent,
        "enabled": cfg.enabled,
        "timezone": schedule.timezone,
        "mode": schedule.mode,
        "next_wakes": [dt.isoformat() for dt in slots],
        "error": None,
    }


@router.get("/work-hours/{work_hour_id}")
def show_work_hour(slug: str, work_hour_id: str, org: OrgDep) -> dict:
    wh = org.db.work_hours.get(work_hour_id)
    if wh is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "work_hour_id": work_hour_id})
    return _wh_to_dict(wh)


class SpawnRoutine(BaseModel):
    slug: str | None = None
    brief: str = Field(min_length=1)


class WorkHoursSpawnBody(BaseModel):
    summary: str = Field(min_length=1)
    routines: list[SpawnRoutine] = Field(min_length=1)


@router.post("/work-hours/{work_hour_id}/spawn")
async def spawn_work_hour(
    slug: str, work_hour_id: str, body: WorkHoursSpawnBody, org: OrgDep, request: Request,
) -> dict:
    state: DaemonState = request.app.state.daemon
    registry = org.teams
    if registry is None:
        raise HTTPException(status_code=400, detail={"code": "unknown_team", "valid": []})
    if len(body.routines) > MAX_ROUTINES_PER_WAKE:
        raise HTTPException(
            status_code=422,
            detail={"code": "too_many_routines", "max": MAX_ROUTINES_PER_WAKE, "got": len(body.routines)},
        )

    created: list[str] = []
    partial_error: str | None = None
    async with org.db_lock:
        wh = org.db.work_hours.get(work_hour_id)
        if wh is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "work_hour_id": work_hour_id})
        # Single-use / slot-scoped guard: only a `running` wake may spawn, so the
        # endpoint can't be reused as a generic root-task backdoor.
        if wh.status != WorkHourStatus.RUNNING:
            raise HTTPException(
                status_code=409,
                detail={"code": "work_hour_not_running", "status": wh.status.value},
            )

        agent = wh.agent_name
        # Self-team-only (structural): the spawned tasks are always created on the
        # waking agent's own team and targeted to the waking agent as executor.
        # There is no per-routine team selector — no cross-team path from a wake.
        team = registry.team_for_agent(agent) or registry.team_for_manager(agent)
        if team is None:
            raise HTTPException(
                status_code=409, detail={"code": "agent_team_unresolved", "agent": agent},
            )

        # Validate-then-create: Pydantic has already validated the whole payload
        # (summary non-empty, >=1 routine, each brief non-empty) before any task
        # is born. If creation still fails partway, already-created root tasks are
        # real work and are NOT rolled back (settled no-rollback ruling).
        try:
            for routine in body.routines:
                task_id = org.db.next_task_id()
                org.db.insert_task(TaskRecord(
                    id=task_id,
                    brief=routine.brief,
                    team=team,
                    assigned_agent=agent,
                ))
                created.append(task_id)
        except Exception as exc:  # pragma: no cover - exercised via monkeypatch in tests
            partial_error = f"partial_spawn: {exc}"

        now = datetime.now(timezone.utc)
        if partial_error is None:
            transcript_path = _write_wake_transcript(
                org.root, wh, summary=body.summary, spawned_task_ids=created,
            )
            org.db.work_hours.update(
                work_hour_id,
                status=WorkHourStatus.COMPLETED,
                ended_at=now,
                summary=body.summary,
                spawned_task_ids=created,
                spawned_task_count=len(created),
                transcript_path=str(transcript_path),
            )
        else:
            org.db.work_hours.update(
                work_hour_id,
                status=WorkHourStatus.FAILED,
                ended_at=now,
                summary=body.summary,
                spawned_task_ids=created,
                spawned_task_count=len(created),
                error=partial_error,
            )

    # Enqueue + audit outside the db lock. Already-created tasks are enqueued even
    # on partial failure — they are real work that should proceed.
    for task_id in created:
        enqueue_task(state, org.slug, task_id)
    audit = AuditLogger(org.db)
    if created:
        audit.log_work_hour_spawned(work_hour_id, agent, task_ids=created)
    if partial_error is None:
        audit.log_work_hour_completed(
            work_hour_id, agent, spawned_task_count=len(created), routine_count=wh.routine_count,
        )
        return {"work_hour_id": work_hour_id, "status": "completed", "spawned_task_ids": created}
    audit.log_work_hour_failed(work_hour_id, agent, reason="partial_spawn")
    return {
        "work_hour_id": work_hour_id,
        "status": "failed",
        "spawned_task_ids": created,
        "error": partial_error,
    }
