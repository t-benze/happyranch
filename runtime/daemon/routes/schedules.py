"""Agent Todos — founder management surface for schedules (THR-105 Phase 3).

Read-only list/show and state-mutation pause/cancel/edit routes over the
existing ``ScheduleService``.  No firing, no scheduler loop, no agent
arming yet — this is the founder/operator visibility and management layer.

User-facing label: Todos.  Internal primitive: Schedule / SCHEDULE-NNN.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from runtime.daemon.auth import require_token
from runtime.daemon.routes._org_dep import OrgDep
from runtime.daemon.state import DaemonState
from runtime.models import ScheduleKind, ScheduleStatus
from runtime.orchestrator.schedule_service import ScheduleService, ScheduleServiceError

router = APIRouter(dependencies=[require_token()])


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

from pydantic import BaseModel, Field  # noqa: E402


class ScheduleEditBody(BaseModel):
    fire_at: str | None = Field(None, description="ISO-8601 datetime for the next fire")
    recurrence: dict | None = Field(None, description="Weekly recurrence dict")
    timezone: str | None = Field(None, description="IANA timezone string")


@router.patch("/schedules/{schedule_id}")
def edit_schedule(
    slug: str, schedule_id: str, body: ScheduleEditBody, org: OrgDep, request: Request,
) -> dict:
    """Edit mutable fields of a Todo (fire_at, recurrence, timezone)."""
    from datetime import datetime, timezone as tz_mod

    svc = ScheduleService(org.db)
    acting_agent = f"operator@{slug}"

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
