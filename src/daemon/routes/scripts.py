"""Script request endpoints (spec §5)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.daemon.auth import require_token
from src.daemon.routes._org_dep import OrgDep
from src.infrastructure.audit_logger import AuditLogger
from src.models import (
    ScriptInterpreter,
    ScriptRequestRecord,
    ScriptRequestStatus,
)

router = APIRouter(dependencies=[require_token()])

_MAX_SCRIPT_BYTES = 65536
_MAX_TITLE_LEN = 200
_VALID_INTERPRETERS = {"bash", "sh", "zsh", "python3"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_cwd_hint(cwd_hint: str | None) -> str | None:
    """Reject absolute paths or anything containing ``..`` segments."""
    if cwd_hint is None:
        return None
    if cwd_hint.startswith("/"):
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_cwd_hint", "reason": "absolute_path"},
        )
    parts = [p for p in cwd_hint.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_cwd_hint", "reason": "dotdot"},
        )
    return cwd_hint


class SubmitBody(BaseModel):
    task_id: str
    session_id: str
    title: str
    rationale: str
    script: str
    interpreter: str
    cwd_hint: str | None = None


@router.post("/scripts/submit", status_code=201)
async def submit_script(slug: str, body: SubmitBody, org: OrgDep) -> dict:
    # §5.1 validation order.

    # 1. Task exists.
    task = org.db.get_task(body.task_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "unknown_task", "task_id": body.task_id},
        )

    # 2. Task status active (BEFORE session — completed tasks have no live session).
    if task.status.value not in ("pending", "in_progress"):
        raise HTTPException(
            status_code=400,
            detail={"code": "task_not_active", "status": task.status.value},
        )

    # 3. Session ownership.
    agent = task.assigned_agent
    active_sid = org.sessions.get_active(body.task_id, agent)
    if active_sid is None or active_sid != body.session_id:
        raise HTTPException(
            status_code=409,
            detail={"code": "session_mismatch", "active": active_sid, "got": body.session_id},
        )

    # 4. Title.
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail={"code": "empty_title"})
    if len(title) > _MAX_TITLE_LEN:
        raise HTTPException(
            status_code=422,
            detail={"code": "title_too_long", "max": _MAX_TITLE_LEN},
        )

    # 5. Rationale.
    rationale = body.rationale.strip()
    if not rationale:
        raise HTTPException(status_code=422, detail={"code": "empty_rationale"})

    # 6. Script size (check before stripping to catch payloads that are only whitespace).
    if len(body.script.encode("utf-8")) > _MAX_SCRIPT_BYTES:
        raise HTTPException(
            status_code=422,
            detail={"code": "script_too_large", "max_bytes": _MAX_SCRIPT_BYTES},
        )
    script_stripped = body.script.strip()
    if not script_stripped:
        raise HTTPException(status_code=422, detail={"code": "empty_script"})

    # 7. Interpreter.
    if body.interpreter not in _VALID_INTERPRETERS:
        raise HTTPException(
            status_code=422,
            detail={"code": "unknown_interpreter", "got": body.interpreter},
        )

    # 8. cwd_hint shape (resolves under workspace root — existence not checked here).
    cwd_hint = _validate_cwd_hint(body.cwd_hint)

    # Effect: allocate id, insert row, audit.
    async with org.db_lock:
        sr_id = org.db.next_script_request_id()
        record = ScriptRequestRecord(
            id=sr_id,
            task_id=body.task_id,
            agent_name=agent,
            title=title,
            rationale=rationale,
            script_text=body.script,
            interpreter=ScriptInterpreter(body.interpreter),
            cwd_hint=cwd_hint,
            status=ScriptRequestStatus.PENDING,
            created_at=_now_iso(),
        )
        org.db.insert_script_request(record)

    audit = AuditLogger(org.db)
    audit.log_script_submitted(
        task_id=body.task_id,
        sr_id=sr_id,
        agent=agent,
        title=title,
        interpreter=body.interpreter,
        cwd_hint=cwd_hint,
        byte_size=len(body.script.encode("utf-8")),
        line_count=body.script.count("\n") + 1,
    )

    return {"id": sr_id, "status": "pending", "created_at": record.created_at}


_MAX_REJECT_REASON_LEN = 1000


class RejectBody(BaseModel):
    reason: str


@router.post("/scripts/{sr_id}/reject")
async def reject_script(slug: str, sr_id: str, body: RejectBody, org: OrgDep) -> dict:
    record = org.db.get_script_request(sr_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"code": "unknown_script_request", "sr_id": sr_id})

    reason = body.reason.strip()
    if not reason:
        raise HTTPException(status_code=422, detail={"code": "empty_reason"})
    if len(reason) > _MAX_REJECT_REASON_LEN:
        raise HTTPException(status_code=422, detail={"code": "reason_too_long", "max": _MAX_REJECT_REASON_LEN})

    if record.status != ScriptRequestStatus.PENDING:
        raise HTTPException(
            status_code=409,
            detail={"code": "not_pending", "status": record.status.value},
        )

    reviewed_at = _now_iso()
    try:
        org.db.transition_script_to_rejected(
            sr_id, reviewer="founder", reason=reason, reviewed_at=reviewed_at,
        )
    except ValueError:
        # Race: someone else acted between our read and our write.
        raise HTTPException(status_code=409, detail={"code": "not_pending"})

    audit = AuditLogger(org.db)
    audit.log_script_rejected(
        task_id=record.task_id,
        sr_id=sr_id,
        reviewer="founder",
        reason=reason,
    )

    updated = org.db.get_script_request(sr_id)
    return updated.model_dump()
