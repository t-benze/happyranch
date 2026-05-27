"""Script request endpoints (spec §5)."""
from __future__ import annotations

import asyncio
import json as _json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.daemon.auth import require_token
from src.daemon.event_bus import job_topic
from src.daemon.routes._org_dep import OrgDep
from src.daemon.jobs_runner import run_job as _spawn_job
from src.daemon.jobs_runner import _interpreter_binary
from src.infrastructure.audit_logger import AuditLogger
from src.models import (
    JobInterpreter,
    JobRecord,
    JobStatus,
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


@router.post("/jobs/submit", status_code=201)
async def submit_job(slug: str, body: SubmitBody, org: OrgDep) -> dict:
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
        job_id = org.db.next_job_id()
        record = JobRecord(
            id=job_id,
            task_id=body.task_id,
            agent_name=agent,
            title=title,
            rationale=rationale,
            script_text=body.script,
            interpreter=JobInterpreter(body.interpreter),
            cwd_hint=cwd_hint,
            status=JobStatus.PENDING,
            created_at=_now_iso(),
        )
        org.db.insert_job(record)

    audit = AuditLogger(org.db)
    audit.log_job_submitted(
        task_id=body.task_id,
        job_id=job_id,
        agent=agent,
        title=title,
        interpreter=body.interpreter,
        cwd_hint=cwd_hint,
        byte_size=len(body.script.encode("utf-8")),
        line_count=body.script.count("\n") + 1,
    )

    # Fire-and-forget Feishu push (no-op when notifier is unset).
    if getattr(org, "orchestrator", None) is not None:
        org.orchestrator.notify_job_submitted(
            job_id=job_id, agent=agent, task_id=body.task_id,
            title=title, rationale=rationale, script_text=body.script,
            interpreter=body.interpreter, cwd_hint=cwd_hint,
        )

    return {"id": job_id, "status": "pending", "created_at": record.created_at}


_MAX_REJECT_REASON_LEN = 1000


class RejectBody(BaseModel):
    reason: str


async def reject_job_from_notification(
    org, *, job_id: str, reason: str,
) -> JobRecord:
    """In-process reject path used by the Feishu listener.

    Same validation + transition + audit as POST /jobs/{job_id}/reject,
    minus the request-body parsing. Raises HTTPException on failure with
    the same status/detail shape the route returns.
    """
    record = org.db.get_job(job_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "unknown_job", "job_id": job_id},
        )

    reason_stripped = reason.strip()
    if not reason_stripped:
        raise HTTPException(status_code=422, detail={"code": "empty_reason"})
    if len(reason_stripped) > _MAX_REJECT_REASON_LEN:
        raise HTTPException(
            status_code=422,
            detail={"code": "reason_too_long", "max": _MAX_REJECT_REASON_LEN},
        )

    if record.status != JobStatus.PENDING:
        raise HTTPException(
            status_code=409,
            detail={"code": "not_pending", "status": record.status.value},
        )

    reviewed_at = _now_iso()
    try:
        org.db.transition_job_to_rejected(
            job_id, reviewer="founder", reason=reason_stripped,
            reviewed_at=reviewed_at,
        )
    except ValueError:
        # Race: someone else acted between our read and our write.
        raise HTTPException(status_code=409, detail={"code": "not_pending"})

    AuditLogger(org.db).log_job_rejected(
        task_id=record.task_id, job_id=job_id,
        reviewer="founder", reason=reason_stripped,
    )
    return org.db.get_job(job_id)


def _consume_open_feishu_notification(org, job_id: str) -> None:
    """Mark any open kind=job_request Feishu notification as consumed by
    'cli-fallback'.

    Matches the pattern from `grassland resolve-escalation` / `grassland revisit`
    (see `src/daemon/routes/tasks.py`): a CLI/Web action wins the race against
    a later Feishu reply, which would otherwise hit `not_pending` in the
    listener and leave the row stale until reply_ttl_hours expiry.
    """
    row = org.db.get_latest_notification_for_sr(job_id, kind="job_request")
    if row is None or row["consumed_at"] is not None:
        return
    org.db.consume_escalation_notification(
        row["feishu_message_id"], consumed_by="cli-fallback",
    )


@router.post("/jobs/{job_id}/reject")
async def reject_job(slug: str, job_id: str, body: RejectBody, org: OrgDep) -> dict:
    updated = await reject_job_from_notification(
        org, job_id=job_id, reason=body.reason,
    )
    _consume_open_feishu_notification(org, job_id)
    return updated.model_dump()


_VALID_STATUSES = {"pending", "rejected", "running", "completed", "failed"}


@router.get("/jobs/")
async def list_jobs(
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
    rows = org.db.list_jobs_db(
        status=status_filter, agent=agent, task_id=task_id, limit=limit,
    )
    return {"jobs": [r.model_dump() for r in rows]}


@router.get("/jobs/{job_id}")
async def get_job_route(slug: str, job_id: str, org: OrgDep) -> dict:
    record = org.db.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"code": "unknown_job", "job_id": job_id})
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


async def run_job_from_notification(
    org, *, job_id: str,
) -> dict:
    """In-process run path used by the Feishu listener.

    Uses the SR's stored defaults — no cwd_override, no timeout_override.
    Returns the same 202-style dict the HTTP route returns. Raises
    HTTPException on failure with the same status/detail shape.
    """
    return await _run_job_core(
        org, job_id=job_id,
        cwd_override=None, timeout_override=None,
    )


async def _run_job_core(
    org, *, job_id: str,
    cwd_override: str | None, timeout_override: int | None,
) -> dict:
    """Shared core for HTTP and in-process run paths."""
    record = org.db.get_job(job_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "unknown_job", "job_id": job_id},
        )

    if record.status != JobStatus.PENDING:
        raise HTTPException(
            status_code=409,
            detail={"code": "not_pending", "status": record.status.value},
        )

    timeout = (
        timeout_override if timeout_override is not None
        else record.max_runtime_seconds
    )
    # `timeout=None` means unbounded — skip the positive-range validation.
    if timeout is not None and (timeout <= 0 or timeout > 86400):
        raise HTTPException(status_code=422, detail={"code": "invalid_timeout"})

    workspace_root = org.root / "workspaces" / record.agent_name
    try:
        cwd_resolved = _resolve_cwd(
            cwd_override=cwd_override,
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
            detail={
                "code": "interpreter_unavailable",
                "interpreter": record.interpreter.value,
            },
        )

    # Allocate output paths under <runtime>/orgs/<slug>/jobs/.
    jobs_dir = org.root / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = jobs_dir / f"{job_id}.out"
    stderr_path = jobs_dir / f"{job_id}.err"
    stdout_path.write_bytes(b"")
    stderr_path.write_bytes(b"")

    now = _now_iso()
    try:
        org.db.transition_job_to_running(
            job_id,
            reviewer="founder",
            reviewed_at=now,
            started_at=now,
            cwd_resolved=str(cwd_resolved),
            max_runtime_seconds=timeout,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
        )
    except ValueError:
        raise HTTPException(status_code=409, detail={"code": "not_pending"})

    audit = AuditLogger(org.db)
    audit.log_job_run_started(
        task_id=record.task_id, job_id=job_id, reviewer="founder",
        cwd_resolved=str(cwd_resolved),
        timeout_seconds=timeout,
        interpreter=record.interpreter.value,
    )

    # Spawn the runner outside the request lifecycle.
    async def _run_and_persist() -> None:
        loop = asyncio.get_running_loop()

        def _sync_publish(evt: dict) -> None:
            asyncio.run_coroutine_threadsafe(
                org.event_bus.publish(job_topic(job_id), evt), loop
            )

        try:
            result = await _spawn_job(
                job_id=job_id,
                script_text=record.script_text,
                interpreter=record.interpreter.value,
                cwd=str(cwd_resolved),
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                max_runtime_seconds=timeout,
                publish=_sync_publish,
            )
        except FileNotFoundError:
            finished = _now_iso()
            try:
                org.db.transition_job_to_terminal(
                    job_id, status=JobStatus.FAILED,
                    exit_code=None, finished_at=finished, duration_ms=0,
                    stdout_head=None, stderr_head=None,
                )
            except ValueError:
                pass
            audit.log_job_run_failed(
                task_id=record.task_id, job_id=job_id, reason="spawn_failed",
            )
            parent = org.db.get_latest_notification_for_sr(job_id, kind="job_request")
            if parent is not None and getattr(org, "orchestrator", None) is not None:
                org.orchestrator.notify_job_run_result(
                    job_id=job_id, task_id=record.task_id,
                    parent_message_id=parent["feishu_message_id"],
                    status="failed", exit_code=None, duration_ms=0,
                    stdout_head=None, stderr_head=None, reason="spawn_failed",
                )
            return
        except Exception as exc:
            finished = _now_iso()
            try:
                org.db.transition_job_to_terminal(
                    job_id, status=JobStatus.FAILED,
                    exit_code=None, finished_at=finished, duration_ms=0,
                    stdout_head=None, stderr_head=str(exc),
                )
            except ValueError:
                pass
            audit.log_job_run_failed(
                task_id=record.task_id, job_id=job_id, reason="internal_error",
            )
            parent = org.db.get_latest_notification_for_sr(job_id, kind="job_request")
            if parent is not None and getattr(org, "orchestrator", None) is not None:
                org.orchestrator.notify_job_run_result(
                    job_id=job_id, task_id=record.task_id,
                    parent_message_id=parent["feishu_message_id"],
                    status="failed", exit_code=None, duration_ms=0,
                    stdout_head=None, stderr_head=str(exc), reason="internal_error",
                )
            return

        finished = _now_iso()
        try:
            org.db.transition_job_to_terminal(
                job_id,
                status=JobStatus(result.status),
                exit_code=result.exit_code,
                finished_at=finished,
                duration_ms=result.duration_ms,
                stdout_head=result.stdout_head,
                stderr_head=result.stderr_head,
            )
        except ValueError:
            return

        if result.status == "completed":
            audit.log_job_run_completed(
                task_id=record.task_id, job_id=job_id,
                exit_code=result.exit_code or 0,
                duration_ms=result.duration_ms,
                stdout_bytes=result.stdout_bytes,
                stderr_bytes=result.stderr_bytes,
                truncated_stdout=result.truncated_stdout,
                truncated_stderr=result.truncated_stderr,
            )
        else:
            audit.log_job_run_failed(
                task_id=record.task_id, job_id=job_id,
                exit_code=result.exit_code,
                duration_ms=result.duration_ms,
                reason=result.reason or "unknown",
            )

        parent = org.db.get_latest_notification_for_sr(job_id, kind="job_request")
        if parent is not None and getattr(org, "orchestrator", None) is not None:
            org.orchestrator.notify_job_run_result(
                job_id=job_id, task_id=record.task_id,
                parent_message_id=parent["feishu_message_id"],
                status=result.status,
                exit_code=result.exit_code,
                duration_ms=result.duration_ms,
                stdout_head=result.stdout_head,
                stderr_head=result.stderr_head,
                reason=result.reason,
            )

    from src.daemon.jobs_runner import register_runner_task
    register_runner_task(job_id, asyncio.create_task(_run_and_persist()))

    return {
        "id": job_id,
        "status": "running",
        "started_at": now,
        "cwd_resolved": str(cwd_resolved),
        "timeout_seconds": timeout,
        "events_url": f"/api/v1/orgs/{org.slug}/jobs/{job_id}/events",
    }


@router.post("/jobs/{job_id}/run", status_code=202)
async def run_job_route(
    slug: str, job_id: str, body: RunBody, org: OrgDep,
) -> dict:
    result = await _run_job_core(
        org,
        job_id=job_id,
        cwd_override=body.cwd_override,
        timeout_override=body.timeout_seconds,
    )
    _consume_open_feishu_notification(org, job_id)
    return result


@router.get("/jobs/{job_id}/output")
async def get_job_output(
    slug: str, job_id: str, org: OrgDep,
    stream: str = "both",
    max_bytes: int = 1_048_576,
) -> dict:
    record = org.db.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"code": "unknown_job"})
    if max_bytes <= 0 or max_bytes > 10 * 1_048_576:
        raise HTTPException(status_code=422, detail={"code": "invalid_max_bytes"})
    if record.status not in (
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.REJECTED,
    ):
        raise HTTPException(status_code=409, detail={"code": "not_terminal", "status": record.status.value})
    if stream not in ("stdout", "stderr", "both"):
        raise HTTPException(status_code=422, detail={"code": "invalid_stream"})

    def _read(path: str | None) -> tuple[str, bool, int]:
        if path is None:
            return ("", False, 0)
        p = Path(path)
        if not p.exists():
            return ("", False, 0)
        total = p.stat().st_size
        data = p.read_bytes()[:max_bytes]
        return (data.decode("utf-8", errors="replace"), total > max_bytes, total)

    out, out_trunc, out_total = _read(record.stdout_path) if stream in ("stdout", "both") else ("", False, 0)
    err, err_trunc, err_total = _read(record.stderr_path) if stream in ("stderr", "both") else ("", False, 0)
    return {
        "stdout": out,
        "stderr": err,
        "truncated_stdout": out_trunc,
        "truncated_stderr": err_trunc,
        "total_stdout_bytes": out_total,
        "total_stderr_bytes": err_total,
    }


_TERMINAL_SR_STATUSES = (
    JobStatus.COMPLETED,
    JobStatus.FAILED,
    JobStatus.REJECTED,
)


def _terminal_frame_from_record(record) -> str:
    payload = {
        "status": record.status.value,
        "exit_code": record.exit_code,
        "duration_ms": record.duration_ms,
    }
    return f"event: terminal\ndata: {_json.dumps(payload)}\n\n"


@router.get("/jobs/{job_id}/events")
async def job_events_stream(slug: str, job_id: str, org: OrgDep) -> StreamingResponse:
    record = org.db.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"code": "unknown_job"})

    async def gen():
        # If already terminal at request time, emit one terminal event and close.
        if record.status in _TERMINAL_SR_STATUSES:
            yield _terminal_frame_from_record(record)
            return

        # Race window: between this check and the moment the subscription is
        # actually registered inside event_bus.subscribe (which only happens
        # on the first __anext__), the runner can publish a terminal event and
        # we'd miss it — hanging until disconnect. Mitigate by waking up
        # periodically to re-poll the DB; the row is the authoritative source.
        sub_iter = org.event_bus.subscribe(job_topic(job_id)).__aiter__()
        next_task: asyncio.Task | None = asyncio.create_task(sub_iter.__anext__())
        try:
            while True:
                done, _pending = await asyncio.wait({next_task}, timeout=1.0)
                if not done:
                    # Timeout: re-check DB in case we raced past a terminal publish.
                    rec_now = org.db.get_job(job_id)
                    if rec_now is None:
                        return
                    if rec_now.status in _TERMINAL_SR_STATUSES:
                        yield _terminal_frame_from_record(rec_now)
                        return
                    continue
                try:
                    evt = next_task.result()
                except StopAsyncIteration:
                    return
                next_task = None
                kind = evt.get("kind", "line")
                if kind == "line":
                    stream_name = evt.get("stream", "stdout")
                    yield f"event: {stream_name}\ndata: {_json.dumps({'line': evt.get('line', ''), 'ts': evt.get('ts')})}\n\n"
                elif kind == "terminal":
                    yield f"event: terminal\ndata: {_json.dumps({'status': evt.get('status'), 'exit_code': evt.get('exit_code'), 'duration_ms': evt.get('duration_ms'), 'reason': evt.get('reason')})}\n\n"
                    return
                next_task = asyncio.create_task(sub_iter.__anext__())
        finally:
            if next_task is not None and not next_task.done():
                next_task.cancel()

    return StreamingResponse(gen(), media_type="text/event-stream")
