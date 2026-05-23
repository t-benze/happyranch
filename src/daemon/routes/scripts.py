"""Script request endpoints (spec §5)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.daemon.auth import require_token
from src.daemon.event_bus import script_topic
from src.daemon.routes._org_dep import OrgDep
from src.daemon.scripts_runner import run_script as _spawn_script
from src.daemon.scripts_runner import _interpreter_binary
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


_VALID_STATUSES = {"pending", "rejected", "running", "completed", "failed"}


@router.get("/scripts/")
async def list_scripts(
    slug: str,
    org: OrgDep,
    status: str | None = "pending",
    agent: str | None = None,
    task_id: str | None = None,
    limit: int = 50,
) -> dict:
    if limit <= 0 or limit > 200:
        raise HTTPException(status_code=422, detail={"code": "invalid_limit"})
    if status == "all" or status is None:
        status_filter: list[str] | None = None
    else:
        status_filter = [s.strip() for s in status.split(",") if s.strip()]
        for s in status_filter:
            if s not in _VALID_STATUSES:
                raise HTTPException(status_code=422, detail={"code": "invalid_status", "got": s})
    rows = org.db.list_script_requests(
        status=status_filter, agent=agent, task_id=task_id, limit=limit,
    )
    return {"scripts": [r.model_dump() for r in rows]}


@router.get("/scripts/{sr_id}")
async def get_script(slug: str, sr_id: str, org: OrgDep) -> dict:
    record = org.db.get_script_request(sr_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"code": "unknown_script_request", "sr_id": sr_id})
    return record.model_dump()


class RunBody(BaseModel):
    cwd_override: str | None = None
    timeout_seconds: int | None = None


def _resolve_cwd(
    *, cwd_override: str | None, cwd_hint: str | None, workspace_root: Path,
) -> Path:
    if cwd_override is not None:
        if cwd_override.startswith("/"):
            return Path(cwd_override)
        return (workspace_root / cwd_override).resolve()
    if cwd_hint is not None:
        return (workspace_root / cwd_hint).resolve()
    return workspace_root


@router.post("/scripts/{sr_id}/run", status_code=202)
async def run_script_route(
    slug: str, sr_id: str, body: RunBody, org: OrgDep,
) -> dict:
    record = org.db.get_script_request(sr_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"code": "unknown_script_request", "sr_id": sr_id})

    if record.status != ScriptRequestStatus.PENDING:
        raise HTTPException(
            status_code=409, detail={"code": "not_pending", "status": record.status.value}
        )

    timeout = body.timeout_seconds if body.timeout_seconds is not None else record.timeout_seconds
    if timeout <= 0 or timeout > 86400:
        raise HTTPException(status_code=422, detail={"code": "invalid_timeout"})

    workspace_root = org.root / "workspaces" / record.agent_name
    try:
        cwd_resolved = _resolve_cwd(
            cwd_override=body.cwd_override,
            cwd_hint=record.cwd_hint,
            workspace_root=workspace_root,
        )
    except (ValueError, OSError):
        raise HTTPException(status_code=422, detail={"code": "invalid_cwd_override"})

    if not cwd_resolved.exists() or not cwd_resolved.is_dir():
        raise HTTPException(
            status_code=409,
            detail={"code": "cwd_missing", "resolved": str(cwd_resolved)},
        )

    # Interpreter binary must exist.
    if _interpreter_binary(record.interpreter.value) is None:
        raise HTTPException(
            status_code=422,
            detail={"code": "interpreter_unavailable", "interpreter": record.interpreter.value},
        )

    # Allocate output paths under <runtime>/orgs/<slug>/scripts/.
    scripts_dir = org.root / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = scripts_dir / f"{sr_id}.out"
    stderr_path = scripts_dir / f"{sr_id}.err"
    stdout_path.write_bytes(b"")
    stderr_path.write_bytes(b"")

    now = _now_iso()
    try:
        org.db.transition_script_to_running(
            sr_id,
            reviewer="founder",
            reviewed_at=now,
            started_at=now,
            cwd_resolved=str(cwd_resolved),
            timeout_seconds=timeout,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
        )
    except ValueError:
        raise HTTPException(status_code=409, detail={"code": "not_pending"})

    audit = AuditLogger(org.db)
    audit.log_script_run_started(
        task_id=record.task_id, sr_id=sr_id, reviewer="founder",
        cwd_resolved=str(cwd_resolved),
        timeout_seconds=timeout,
        interpreter=record.interpreter.value,
    )

    # Spawn the runner outside the request lifecycle.
    async def _run_and_persist() -> None:
        loop = asyncio.get_running_loop()

        def _sync_publish(evt: dict) -> None:
            asyncio.run_coroutine_threadsafe(
                org.event_bus.publish(script_topic(sr_id), evt), loop
            )

        try:
            result = await _spawn_script(
                sr_id=sr_id,
                script_text=record.script_text,
                interpreter=record.interpreter.value,
                cwd=str(cwd_resolved),
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                timeout_seconds=timeout,
                publish=_sync_publish,
            )
        except FileNotFoundError:
            finished = _now_iso()
            try:
                org.db.transition_script_to_terminal(
                    sr_id, status=ScriptRequestStatus.FAILED,
                    exit_code=None, finished_at=finished, duration_ms=0,
                    stdout_head=None, stderr_head=None,
                )
            except ValueError:
                pass
            audit.log_script_run_failed(
                task_id=record.task_id, sr_id=sr_id, reason="spawn_failed",
            )
            return
        except Exception as exc:
            finished = _now_iso()
            try:
                org.db.transition_script_to_terminal(
                    sr_id, status=ScriptRequestStatus.FAILED,
                    exit_code=None, finished_at=finished, duration_ms=0,
                    stdout_head=None, stderr_head=str(exc),
                )
            except ValueError:
                pass
            audit.log_script_run_failed(
                task_id=record.task_id, sr_id=sr_id, reason="internal_error",
            )
            return

        finished = _now_iso()
        try:
            org.db.transition_script_to_terminal(
                sr_id,
                status=ScriptRequestStatus(result.status),
                exit_code=result.exit_code,
                finished_at=finished,
                duration_ms=result.duration_ms,
                stdout_head=result.stdout_head,
                stderr_head=result.stderr_head,
            )
        except ValueError:
            return

        if result.status == "completed":
            audit.log_script_run_completed(
                task_id=record.task_id, sr_id=sr_id,
                exit_code=result.exit_code or 0,
                duration_ms=result.duration_ms,
                stdout_bytes=result.stdout_bytes,
                stderr_bytes=result.stderr_bytes,
                truncated_stdout=result.truncated_stdout,
                truncated_stderr=result.truncated_stderr,
            )
        else:
            audit.log_script_run_failed(
                task_id=record.task_id, sr_id=sr_id,
                exit_code=result.exit_code,
                duration_ms=result.duration_ms,
                reason=result.reason or "unknown",
            )

    asyncio.create_task(_run_and_persist())

    return {
        "id": sr_id,
        "status": "running",
        "started_at": now,
        "cwd_resolved": str(cwd_resolved),
        "timeout_seconds": timeout,
        "events_url": f"/api/v1/orgs/{slug}/scripts/{sr_id}/events",
    }
