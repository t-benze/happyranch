"""Agent Todos — schedule management and fire-spawn callback (THR-105).

Management surface (founder/operator): list, show, pause, cancel, edit.
Fire path (agent callback): single-use record-scoped spawn that creates
exactly one root task from the stored normalized_brief.

User-facing label: Todos.  Internal primitive: Schedule / SCHEDULE-NNN.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema

from runtime.daemon.auth import require_token
from runtime.daemon.routes._org_dep import OrgDep
from runtime.daemon.runner import enqueue_task
from runtime.daemon.state import DaemonState
from runtime.models import ScheduleKind, ScheduleStatus, TaskRecord
from runtime.orchestrator.schedule_capability import is_scheduling_enabled
from runtime.orchestrator.schedule_rules import next_weekly_occurrence
from runtime.orchestrator.schedule_service import ScheduleService, ScheduleServiceError

router = APIRouter(dependencies=[require_token()])


# ── helpers ─────────────────────────────────────────────────────────────

def _schedule_to_dict(record) -> dict:
    return {
        "schedule_id": record.id,
        "agent_name": record.agent_name,
        "team": record.team,
        "kind": record.kind.value,
        "fire_at": record.fire_at.isoformat(),
        "recurrence": record.recurrence,
        "timezone": record.timezone,
        "normalized_brief": record.normalized_brief,
        "source_instruction": record.source_instruction,
        "status": record.status.value,
        "active": record.active,
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        "indefinite": record.indefinite,
        "spawned_task_ids": record.spawned_task_ids,
        "last_fired_at": record.last_fired_at.isoformat() if record.last_fired_at else None,
        "fire_count": record.fire_count,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


# ── create ─────────────────────────────────────────────────────────────

class ScheduleCreateBody(BaseModel):
    """Payload for the agent schedule create callback.

    The creating agent is bound server-side through session validation
    (task_id + session_id + agent), not through payload fields.  The
    payload must carry the explicit instruction and a normalized brief;
    natural-language-only arming is refused.
    """
    model_config = {"extra": "forbid"}

    task_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    agent: str = Field(min_length=1)
    source_instruction: str = Field(min_length=1)
    normalized_brief: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    fire_at: str = Field(min_length=1)
    recurrence: dict | SkipJsonSchema[None] = Field(None)
    timezone: str = Field(default="UTC")


@router.post("/schedules")
def create_schedule(
    slug: str,
    body: ScheduleCreateBody,
    org: OrgDep,
    request: Request,
) -> dict:
    """Create a new schedule (Todo) — agent autonomous arming callback.

    Self-target only: the agent is resolved from the session context
    (task_id + session_id + agent).  The payload cannot choose another
    agent.  Scheduling is default-deny — the per-agent capability flag
    must be enabled.
    """
    # ── self-target: session validation ──
    expected_session = org.sessions.get_active(body.task_id, body.agent)
    if expected_session is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "unknown_session",
                "task_id": body.task_id,
                "agent": body.agent,
            },
        )
    if expected_session != body.session_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "session_mismatch",
                "active": expected_session,
                "got": body.session_id,
            },
        )

    # ── capability gate ──
    if not is_scheduling_enabled(org.root, body.agent):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "scheduling_disabled",
                "agent": body.agent,
                "message": (
                    "scheduling is not enabled for this agent. "
                    "Add the agent to scheduling.enabled_agents "
                    "in org/config.yaml."
                ),
            },
        )

    # ── resolve team ──
    registry = org.teams
    if registry is None:
        raise HTTPException(
            status_code=400,
            detail={"code": "unknown_team", "valid": []},
        )
    team = registry.team_for_agent(body.agent) or registry.team_for_manager(body.agent)
    if team is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "agent_team_unresolved", "agent": body.agent},
        )

    # ── parse kind ──
    try:
        kind = ScheduleKind(body.kind)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_kind",
                "got": body.kind,
                "valid": [k.value for k in ScheduleKind],
            },
        )

    # ── parse fire_at ──
    try:
        fire_at = datetime.fromisoformat(body.fire_at)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_fire_at", "got": body.fire_at},
        )
    if fire_at.tzinfo is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_fire_at",
                "got": body.fire_at,
                "message": (
                    "fire_at must include a timezone offset "
                    "(e.g., +00:00, +08:00, Z)"
                ),
            },
        )

    # ── call service ──
    svc = ScheduleService(org.db)
    try:
        record = svc.create(
            agent_name=body.agent,
            team=team,
            kind=kind,
            fire_at=fire_at,
            recurrence=body.recurrence,
            timezone=body.timezone,
            normalized_brief=body.normalized_brief,
            source_instruction=body.source_instruction,
            scheduling_enabled=True,  # already gated above
        )
    except ScheduleServiceError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "create_failed", "message": str(exc)},
        )

    return _schedule_to_dict(record)


# ── list ────────────────────────────────────────────────────────────────

@router.get("/schedules")
def list_schedules(
    slug: str,
    org: OrgDep,
    agent: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
) -> dict:
    """List Todos with optional filtering by agent and/or status."""
    svc = ScheduleService(org.db)
    status_val: ScheduleStatus | None = None
    if status is not None:
        try:
            status_val = ScheduleStatus(status)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_status",
                    "got": status,
                    "valid": [s.value for s in ScheduleStatus],
                },
            )
    records = svc.list(agent=agent, status=status_val, limit=limit)
    return {"schedules": [_schedule_to_dict(r) for r in records]}


# ── show ────────────────────────────────────────────────────────────────

@router.get("/schedules/{schedule_id}")
def show_schedule(slug: str, schedule_id: str, org: OrgDep) -> dict:
    """Show a single Todo by its SCHEDULE-NNN id."""
    svc = ScheduleService(org.db)
    record = svc.get(schedule_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "schedule_id": schedule_id},
        )
    return _schedule_to_dict(record)


# ── pause ───────────────────────────────────────────────────────────────

@router.post("/schedules/{schedule_id}/pause")
def pause_schedule(
    slug: str, schedule_id: str, org: OrgDep, request: Request,
) -> dict:
    """Pause a Todo — suspend it without deleting.  Idempotent."""
    svc = ScheduleService(org.db)
    # Use org slug as the acting agent for audit provenance.
    acting_agent = f"operator@{slug}"
    try:
        record = svc.pause(schedule_id, agent_name=acting_agent)
    except ScheduleServiceError as exc:
        raise HTTPException(status_code=409, detail={"code": "state_conflict", "message": str(exc)})
    return _schedule_to_dict(record)


# ── cancel ──────────────────────────────────────────────────────────────

@router.post("/schedules/{schedule_id}/cancel")
def cancel_schedule(
    slug: str, schedule_id: str, org: OrgDep, request: Request,
) -> dict:
    """Cancel a Todo — permanent termination."""
    svc = ScheduleService(org.db)
    acting_agent = f"operator@{slug}"
    try:
        record = svc.cancel(schedule_id, agent_name=acting_agent)
    except ScheduleServiceError as exc:
        raise HTTPException(status_code=409, detail={"code": "state_conflict", "message": str(exc)})
    return _schedule_to_dict(record)


# ── edit ────────────────────────────────────────────────────────────────

class ScheduleEditBody(BaseModel):
    model_config = {"extra": "forbid"}

    fire_at: str | SkipJsonSchema[None] = Field(
        None, description="ISO-8601 datetime for the next fire"
    )
    recurrence: dict | SkipJsonSchema[None] = Field(
        None, description="Weekly recurrence dict"
    )
    timezone: str | SkipJsonSchema[None] = Field(
        None, description="IANA timezone string"
    )


@router.patch("/schedules/{schedule_id}")
def edit_schedule(
    slug: str, schedule_id: str, body: ScheduleEditBody, org: OrgDep, request: Request,
) -> dict:
    """Edit mutable fields of a Todo (fire_at, recurrence, timezone)."""
    svc = ScheduleService(org.db)
    acting_agent = f"operator@{slug}"

    # Reject explicit null for every mutable edit field.
    excplicit_nulls = [
        f for f in body.model_fields_set
        if getattr(body, f) is None
    ]
    if excplicit_nulls:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "explicit_null",
                "fields": excplicit_nulls,
                "message": (
                    "fields must be omitted when not providing a value; "
                    "null is not a valid edit value"
                ),
            },
        )

    # Build keyword args from non-None body fields.
    kwargs: dict = {}
    if body.fire_at is not None:
        try:
            kwargs["fire_at"] = datetime.fromisoformat(body.fire_at)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_fire_at", "got": body.fire_at},
            )
    if body.recurrence is not None:
        kwargs["recurrence"] = body.recurrence
    if body.timezone is not None:
        kwargs["timezone"] = body.timezone

    try:
        record = svc.edit(schedule_id, acting_agent, **kwargs)
    except ScheduleServiceError as exc:
        raise HTTPException(status_code=409, detail={"code": "state_conflict", "message": str(exc)})
    return _schedule_to_dict(record)


# ── spawn ───────────────────────────────────────────────────────────────

class ScheduleSpawnBody(BaseModel):
    """Payload for the schedule spawn callback. Only ``summary`` is accepted;
    the target agent, team, and brief come from the stored Schedule row — the
    payload cannot choose them."""
    summary: str = Field(min_length=1)


def _write_schedule_transcript(
    root: Path,
    schedule_id: str,
    agent_name: str,
    summary: str,
    spawned_task_ids: list[str],
    status: str = "fired",
) -> Path:
    """Write a schedule transcript under ``<root>/schedules/SCHEDULE-NNN.md``.

    Atomic replace mirrors ``_write_wake_transcript``.
    The ``status`` parameter reflects the true terminal final status
    (``fired`` for completed, ``expired`` for weekly expiry scenarios).
    """
    target_dir = root / "schedules"
    target_dir.mkdir(parents=True, exist_ok=True)
    frontmatter = {
        "schedule_id": schedule_id,
        "agent_name": agent_name,
        "status": status,
        "spawned_task_count": len(spawned_task_ids),
        "spawned_task_ids": spawned_task_ids,
    }
    body = f"---\n{yaml.safe_dump(frontmatter, sort_keys=False)}---\n\n{summary}\n"
    target = target_dir / f"{schedule_id}.md"
    fd, tmp_name = tempfile.mkstemp(dir=target_dir, prefix=f".{schedule_id}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(body.encode("utf-8"))
        os.replace(tmp_name, target)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise
    return target


@router.post("/schedules/{schedule_id}/spawn")
async def spawn_schedule(
    slug: str,
    schedule_id: str,
    body: ScheduleSpawnBody,
    org: OrgDep,
    request: Request,
) -> dict:
    state: DaemonState = request.app.state.daemon
    registry = org.teams
    if registry is None:
        raise HTTPException(
            status_code=400,
            detail={"code": "unknown_team", "valid": []},
        )

    created: list[str] = []
    async with org.db_lock:
        schedule = org.db.schedules.get(schedule_id)
        if schedule is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "not_found", "schedule_id": schedule_id},
            )
        # Single-use / record-scoped guard: only a `firing` schedule may spawn.
        if schedule.status != ScheduleStatus.FIRING:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "schedule_not_firing",
                    "status": schedule.status.value,
                },
            )

        agent = schedule.agent_name
        # Self-team-only (structural): the spawned task is always created on
        # the schedule's owning agent's team and targeted to that agent.
        team = registry.team_for_agent(agent) or registry.team_for_manager(agent)
        if team is None:
            raise HTTPException(
                status_code=409,
                detail={"code": "agent_team_unresolved", "agent": agent},
            )

        # Create exactly one root task from the stored normalized_brief.
        # The payload cannot choose the agent, team, or brief.
        try:
            task_id = org.db.next_task_id()
            org.db.insert_task(TaskRecord(
                id=task_id,
                brief=schedule.normalized_brief,
                team=team,
                assigned_agent=agent,
            ))
            created.append(task_id)
        except Exception as exc:  # pragma: no cover
            raise HTTPException(
                status_code=500,
                detail={"code": "task_creation_failed", "error": str(exc)},
            )

        now = datetime.now(timezone.utc)
        spawned_task_ids = schedule.spawned_task_ids + created
        fire_count = schedule.fire_count + 1

        # Terminal status returned to the caller after enqueue+audit.
        # "completed" for one-shot / weekly re-arm; "expired" for weekly
        # expiry paths that still enqueue the current fire's task.
        return_status = "completed"

        if schedule.kind == ScheduleKind.ONE_SHOT:
            # One-shot: transition to fired (terminal).
            transcript_path = _write_schedule_transcript(
                org.root, schedule_id, agent, body.summary, spawned_task_ids,
            )
            org.db.schedules.update(
                schedule_id,
                status=ScheduleStatus.FIRED,
                active=0,
                spawned_task_ids=spawned_task_ids,
                last_fired_at=now,
                fire_count=fire_count,
                session_id=None,
                transcript_path=str(transcript_path),
                updated_at=now,
            )
        else:
            # Weekly: compute next occurrence, re-arm or expire.
            recurrence = schedule.recurrence
            if recurrence is None:
                raise HTTPException(
                    status_code=500,
                    detail={"code": "weekly_no_recurrence", "schedule_id": schedule_id},
                )

            next_fire = next_weekly_occurrence(
                recurrence["day"],
                recurrence["time"],
                recurrence["tz"],
                after=now,
            )

            if next_fire is None:
                # Could not compute next occurrence — should not happen for a
                # valid weekly recurrence, but guard cleanly.
                transcript_path = _write_schedule_transcript(
                    org.root, schedule_id, agent, body.summary, spawned_task_ids,
                    status="expired",
                )
                org.db.schedules.update(
                    schedule_id,
                    status=ScheduleStatus.EXPIRED,
                    active=0,
                    spawned_task_ids=spawned_task_ids,
                    last_fired_at=now,
                    fire_count=fire_count,
                    transcript_path=str(transcript_path),
                    updated_at=now,
                )
                org.db.insert_audit_log(
                    task_id=schedule_id,
                    agent=agent,
                    action="schedule_expired",
                    payload={"reason": "no_next_occurrence"},
                )
                return_status = "expired"
            elif (
                schedule.expires_at is not None
                and schedule.indefinite != 1
                and next_fire > schedule.expires_at
            ):
                # Expired: no re-arm. Next occurrence exceeds expires_at.
                transcript_path = _write_schedule_transcript(
                    org.root, schedule_id, agent, body.summary, spawned_task_ids,
                    status="expired",
                )
                org.db.schedules.update(
                    schedule_id,
                    status=ScheduleStatus.EXPIRED,
                    active=0,
                    spawned_task_ids=spawned_task_ids,
                    last_fired_at=now,
                    fire_count=fire_count,
                    transcript_path=str(transcript_path),
                    updated_at=now,
                )
                org.db.insert_audit_log(
                    task_id=schedule_id,
                    agent=agent,
                    action="schedule_expired",
                    payload={"reason": "past_expires_at"},
                )
                return_status = "expired"
            else:
                # Re-arm with next fire_at.
                transcript_path = _write_schedule_transcript(
                    org.root, schedule_id, agent, body.summary, spawned_task_ids,
                )
                org.db.schedules.update(
                    schedule_id,
                    status=ScheduleStatus.ARMED,
                    active=1,
                    fire_at=next_fire,
                    spawned_task_ids=spawned_task_ids,
                    last_fired_at=now,
                    fire_count=fire_count,
                    session_id=None,
                    transcript_path=str(transcript_path),
                    updated_at=now,
                )

    # Enqueue + audit outside the db lock.
    for task_id in created:
        enqueue_task(state, org.slug, task_id)

    # Audit: schedule_spawned + schedule_completed.
    org.db.insert_audit_log(
        task_id=schedule_id,
        agent=agent,
        action="schedule_spawned",
        payload={"spawned_task_ids": created},
    )
    org.db.insert_audit_log(
        task_id=schedule_id,
        agent=agent,
        action="schedule_completed",
        payload={"spawned_task_ids": created, "summary": body.summary},
    )

    return {
        "schedule_id": schedule_id,
        "status": return_status,
        "spawned_task_ids": created,
    }
