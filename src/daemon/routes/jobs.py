"""Script request endpoints (spec §5)."""
from __future__ import annotations

import asyncio
import json as _json
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, model_validator

from src.daemon.auth import optional_bearer, require_token
from src.daemon.event_bus import job_topic
from src.daemon.routes._org_dep import OrgDep
from src.daemon.jobs_runner import run_job as _spawn_job
from src.daemon.jobs_runner import _interpreter_binary, fire_resume_check_for_job
from src.infrastructure.audit_logger import AuditLogger
from src.models import (
    JobInterpreter,
    JobRecord,
    JobStatus,
    TalkStatus,
)

# Two routers mounted at the same prefix. ``router`` carries the bearer-only
# routes (founder-facing surface); ``dual_router`` carries routes that accept
# either a bearer (founder) OR a valid session-binding (agent reading/acting
# on its own job).
router = APIRouter(dependencies=[require_token()])
dual_router = APIRouter()


_TERMINAL_SR_STATUSES = (
    JobStatus.COMPLETED,
    JobStatus.FAILED,
    JobStatus.REJECTED,
)


def _enforce_session_or_bearer(
    record: JobRecord,
    *,
    has_bearer: bool,
    task_id: str | None,
    session_id: str | None,
    talk_id: str | None = None,
    org,
) -> None:
    """Authorize a dual-auth call against a JobRecord.

    Founder path: ``has_bearer=True`` → allow.

    Agent path: ``has_bearer=False`` → exactly one of two bindings:
      - ``task_id`` + ``session_id``: verify the task is owned by the same
        agent as ``record`` AND ``session_id`` matches the active session
        for that (task, agent) pair.
      - ``talk_id``: verify the talk exists, is OPEN, and its ``agent_name``
        equals ``record.agent_name`` — the agent is talking to the founder
        and acting on its own job.

    On any mismatch raises ``HTTPException(409, {"code": "session_mismatch"})``.
    A bearer-less caller that supplies neither binding (or supplies both) gets
    the same 409 — one shared error class for "you didn't prove you can read
    this row".
    """
    if has_bearer:
        return

    has_task_binding = bool(task_id) and bool(session_id)
    has_talk_binding = bool(talk_id)

    # Reject mixed / partial bindings up front. (Pydantic-level mutual
    # exclusion only applies to /submit because the query-string params here
    # aren't a model.)
    if has_task_binding == has_talk_binding:
        raise HTTPException(
            status_code=409, detail={"code": "session_mismatch"},
        )

    if has_talk_binding:
        talk = org.db.get_talk(talk_id)
        if (
            talk is None
            or talk.status != TalkStatus.OPEN
            or talk.agent_name != record.agent_name
        ):
            raise HTTPException(
                status_code=409, detail={"code": "session_mismatch"},
            )
        return

    task = org.db.get_task(task_id)
    if task is None or task.assigned_agent != record.agent_name:
        raise HTTPException(
            status_code=409, detail={"code": "session_mismatch"},
        )
    expected = org.sessions.get_active(task_id, record.agent_name)
    if expected is None or expected != session_id:
        raise HTTPException(
            status_code=409, detail={"code": "session_mismatch"},
        )

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
    # Two mutually-exclusive auth paths (mirrors manage-agent / threads.compose):
    # - Task path: supply task_id + session_id from an active session.
    # - Talk path: supply talk_id alone from the open talk the agent is in.
    # The model_validator below rejects partial / dual / missing bindings.
    task_id: str | None = None
    session_id: str | None = None
    talk_id: str | None = None
    title: str
    # Rationale is required ONLY when review_required=True. Default empty so
    # agent-side auto-run callers can omit it. When review_required=True the
    # handler enforces a non-blank value (400 rationale_required).
    rationale: str = ""
    script: str
    interpreter: str
    cwd_hint: str | None = None
    # Founder-review gate (default False = auto-run inline).
    review_required: bool = False
    # Long-running flag (default False = 300s default runtime cap).
    persistent: bool = False
    # Optional explicit runtime cap; None falls back to the persistent-aware
    # default (300s when persistent=False, unbounded when persistent=True).
    max_runtime_seconds: int | None = None

    @model_validator(mode="after")
    def _exactly_one_auth_path(self) -> "SubmitBody":
        task_path = self.task_id is not None and self.session_id is not None
        partial_task = (self.task_id is not None) != (self.session_id is not None)
        talk_path = self.talk_id is not None
        if partial_task:
            raise ValueError("task_id and session_id must be supplied together")
        if task_path and talk_path:
            raise ValueError("supply either (task_id + session_id) or talk_id, not both")
        if not task_path and not talk_path:
            raise ValueError("supply either (task_id + session_id) or talk_id")
        return self


# Default runtime cap (seconds) for non-persistent auto-run jobs when the
# caller didn't pass an explicit max_runtime_seconds. Persistent jobs default
# to unbounded (None) until the founder /stop or task-terminal kill.
_DEFAULT_BOUNDED_RUNTIME_SECONDS = 300


@router.post("/jobs/submit", status_code=201)
async def submit_job(slug: str, body: SubmitBody, org: OrgDep) -> dict:
    # §5.1 validation order. Two auth paths (mutually exclusive — enforced by
    # SubmitBody._exactly_one_auth_path):
    #
    # - Task path  → derive agent from task.assigned_agent + verify active session
    # - Talk path  → derive agent from talk.agent_name + require talk OPEN
    #
    # ``scope_id`` is the audit-scope id this job belongs to. On the task path
    # it's the TASK-NNN; on the talk path it's the TALK-NNN. It's stored on
    # ``JobRecord.task_id`` (overloaded — same pattern as audit_log.task_id)
    # so every downstream audit / Feishu call already passes the right scope
    # without extra plumbing.

    if body.talk_id is not None:
        # Talk path. Steps 1–3 collapse: the talk is the auth subject; no
        # SessionTracker lookup needed.
        talk = org.db.get_talk(body.talk_id)
        if talk is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "unknown_talk", "talk_id": body.talk_id},
            )
        if talk.status != TalkStatus.OPEN:
            raise HTTPException(
                status_code=400,
                detail={"code": "talk_not_open", "status": talk.status.value},
            )
        agent = talk.agent_name
        scope_id = body.talk_id
    else:
        # Task path. The SubmitBody validator guarantees both task_id and
        # session_id are present when talk_id is None.
        assert body.task_id is not None and body.session_id is not None

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
        scope_id = body.task_id

    # 4. Title.
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail={"code": "empty_title"})
    if len(title) > _MAX_TITLE_LEN:
        raise HTTPException(
            status_code=422,
            detail={"code": "title_too_long", "max": _MAX_TITLE_LEN},
        )

    # 5. Rationale: required ONLY when founder review is requested.
    #    On the auto-run path we accept blank rationale (the agent didn't need
    #    to talk the founder through anything — it's just running its own work).
    rationale = body.rationale.strip()
    if body.review_required and not rationale:
        raise HTTPException(
            status_code=400, detail={"code": "rationale_required"},
        )

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

    # 9. Resolve effective max_runtime_seconds.
    #    Explicit override always wins; otherwise persistent → unbounded (None),
    #    non-persistent → _DEFAULT_BOUNDED_RUNTIME_SECONDS.
    if body.max_runtime_seconds is not None:
        effective_max_runtime: int | None = body.max_runtime_seconds
        if effective_max_runtime <= 0 or effective_max_runtime > 86400:
            raise HTTPException(
                status_code=422, detail={"code": "invalid_timeout"},
            )
    elif body.persistent:
        effective_max_runtime = None
    else:
        effective_max_runtime = _DEFAULT_BOUNDED_RUNTIME_SECONDS

    # Effect: allocate id, insert row, audit.
    async with org.db_lock:
        job_id = org.db.next_job_id()
        record = JobRecord(
            id=job_id,
            task_id=scope_id,
            submitted_from_talk_id=body.talk_id,
            agent_name=agent,
            title=title,
            rationale=rationale,
            script_text=body.script,
            interpreter=JobInterpreter(body.interpreter),
            cwd_hint=cwd_hint,
            status=JobStatus.PENDING,
            review_required=body.review_required,
            persistent=body.persistent,
            max_runtime_seconds=effective_max_runtime,
            created_at=_now_iso(),
        )
        org.db.insert_job(record)

    audit = AuditLogger(org.db)
    audit.log_job_submitted(
        task_id=scope_id,
        job_id=job_id,
        agent=agent,
        title=title,
        interpreter=body.interpreter,
        cwd_hint=cwd_hint,
        byte_size=len(body.script.encode("utf-8")),
        line_count=body.script.count("\n") + 1,
    )

    # Founder-review path: leave pending, push to Feishu, return.
    if body.review_required:
        if getattr(org, "orchestrator", None) is not None:
            org.orchestrator.notify_job_submitted(
                job_id=job_id, agent=agent, task_id=scope_id,
                title=title, rationale=rationale, script_text=body.script,
                interpreter=body.interpreter, cwd_hint=cwd_hint,
            )
        return {"id": job_id, "status": "pending", "created_at": record.created_at}

    # Auto-run path: dispatch the runner immediately. _run_job_core already
    # handles validation (cwd, interpreter, transition_to_running) + spawns
    # the background task. We pass trigger="agent" so it logs the right
    # audit action (job_auto_started, not job_run_started).
    try:
        run_result = await _run_job_core(
            org, job_id=job_id,
            cwd_override=None, timeout_override=None,
            trigger="agent", trigger_actor=agent,
        )
    except HTTPException:
        # cwd_missing / interpreter_unavailable / invalid_cwd_override —
        # surface to the caller; the row stays pending so the founder
        # could /run it manually later if appropriate.
        raise

    return {
        "id": job_id,
        "status": run_result["status"],
        "created_at": record.created_at,
        "started_at": run_result.get("started_at"),
        "cwd_resolved": run_result.get("cwd_resolved"),
        "timeout_seconds": run_result.get("timeout_seconds"),
        "events_url": run_result.get("events_url"),
    }


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
    # Bridge: any task blocked on this job can now be unblocked.
    fire_resume_check_for_job(org, job_id)
    return org.db.get_job(job_id)


def _consume_open_feishu_notification(org, job_id: str) -> None:
    """Mark any open kind=job_request Feishu notification as consumed by
    'cli-fallback'.

    Matches the pattern from `happyranch resolve-escalation` / `happyranch revisit`
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


def _parse_bool_filter(name: str, value: str | None) -> bool | None:
    """Parse a "true"/"false" query filter; raise 422 on anything else."""
    if value is None:
        return None
    if value not in ("true", "false"):
        raise HTTPException(
            status_code=422,
            detail={"code": f"invalid_{name}", "got": value},
        )
    return value == "true"


@router.get("/jobs/")
async def list_jobs(
    slug: str,
    org: OrgDep,
    status: str | None = "pending",
    agent: str | None = None,
    task_id: str | None = None,
    review_required: str | None = None,
    persistent: str | None = None,
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
    review_required_b = _parse_bool_filter("review_required", review_required)
    persistent_b = _parse_bool_filter("persistent", persistent)
    rows = org.db.list_jobs_db(
        status=status_filter,
        agent=agent,
        task_id=task_id,
        review_required=review_required_b,
        persistent=persistent_b,
        limit=limit,
    )
    return {"jobs": [r.model_dump() for r in rows]}


@dual_router.get("/jobs/{job_id}")
async def get_job_route(
    slug: str, job_id: str, org: OrgDep,
    task_id: str | None = None,
    session_id: str | None = None,
    talk_id: str | None = None,
    has_bearer: bool = optional_bearer(),
) -> dict:
    record = org.db.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"code": "unknown_job", "job_id": job_id})
    _enforce_session_or_bearer(
        record, has_bearer=has_bearer,
        task_id=task_id, session_id=session_id, talk_id=talk_id, org=org,
    )
    return record.model_dump()


@dual_router.get("/jobs/{job_id}/tail")
async def tail_job(
    slug: str, job_id: str, org: OrgDep,
    stream: str = "stdout",
    lines: int = 50,
    task_id: str | None = None,
    session_id: str | None = None,
    talk_id: str | None = None,
    has_bearer: bool = optional_bearer(),
) -> dict:
    """Return the last N lines of stdout or stderr from the on-disk file.

    Dual-auth (bearer OR session-binding). Works for running jobs and for
    terminal ones — readers can tail a completed job's final output the
    same way they peek at a still-running one.
    """
    if stream not in ("stdout", "stderr"):
        raise HTTPException(status_code=422, detail={"code": "invalid_stream"})
    if lines <= 0 or lines > 10_000:
        raise HTTPException(status_code=422, detail={"code": "invalid_lines"})
    record = org.db.get_job(job_id)
    if record is None:
        raise HTTPException(
            status_code=404, detail={"code": "unknown_job", "job_id": job_id},
        )
    _enforce_session_or_bearer(
        record, has_bearer=has_bearer,
        task_id=task_id, session_id=session_id, talk_id=talk_id, org=org,
    )
    path = record.stdout_path if stream == "stdout" else record.stderr_path
    if not path or not Path(path).exists():
        return {"stream": stream, "lines": []}
    # deque(iter, maxlen=N) is O(file_size) but constant memory — fine for
    # the 10k-line cap. For multi-GB log files the right move would be a
    # backwards block-read; out of scope for v1.
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        tail_lines = deque(f, maxlen=lines)
    return {
        "stream": stream,
        "lines": [line.rstrip("\n") for line in tail_lines],
    }


@dual_router.post("/jobs/{job_id}/stop")
async def stop_job(
    slug: str, job_id: str, org: OrgDep,
    task_id: str | None = None,
    session_id: str | None = None,
    talk_id: str | None = None,
    has_bearer: bool = optional_bearer(),
) -> dict:
    """SIGTERM a running job. Founder via bearer OR agent via session-binding.

    The actual terminal state transition flows through the runner's normal
    exit path. We deposit ``founder_stop``/``agent_stop`` into
    ``_KILL_REASON_OVERRIDE`` so the row reports who pressed the button.
    """
    record = org.db.get_job(job_id)
    if record is None:
        raise HTTPException(
            status_code=404, detail={"code": "unknown_job", "job_id": job_id},
        )
    if record.status != JobStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail={"code": "not_running", "status": record.status.value},
        )
    _enforce_session_or_bearer(
        record, has_bearer=has_bearer,
        task_id=task_id, session_id=session_id, talk_id=talk_id, org=org,
    )
    stopped_by = "founder" if has_bearer else "agent"

    # Local import to avoid pulling jobs_runner internals into the module
    # surface every time this file is imported.
    from src.daemon.jobs_runner import _INFLIGHT, _KILL_REASON_OVERRIDE
    import os
    import signal

    proc = _INFLIGHT.get(job_id)
    if proc is None:
        # Status said running but no inflight entry — terminal transition is
        # in flight on the runner. Audit nothing; return a benign ok so the
        # caller doesn't loop.
        return {"ok": True, "id": job_id, "already_terminal": True}
    _KILL_REASON_OVERRIDE[job_id] = f"{stopped_by}_stop"
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    AuditLogger(org.db).log_job_stopped(
        job_id=job_id, task_id=record.task_id, stopped_by=stopped_by,
    )
    return {"ok": True, "id": job_id}


@dual_router.post("/jobs/{job_id}/wait")
async def wait_job(
    slug: str, job_id: str, org: OrgDep,
    timeout_seconds: int = 30,
    task_id: str | None = None,
    session_id: str | None = None,
    talk_id: str | None = None,
    has_bearer: bool = optional_bearer(),
) -> dict:
    """Long-poll until the job reaches a terminal state or the timeout fires.

    Returns ``record.model_dump() | {"timed_out": bool}``. ``timed_out=True``
    means the row was still ``running`` when the timeout fired; callers
    typically loop with another /wait.
    """
    if timeout_seconds <= 0 or timeout_seconds > 300:
        raise HTTPException(status_code=422, detail={"code": "invalid_timeout"})
    record = org.db.get_job(job_id)
    if record is None:
        raise HTTPException(
            status_code=404, detail={"code": "unknown_job", "job_id": job_id},
        )
    _enforce_session_or_bearer(
        record, has_bearer=has_bearer,
        task_id=task_id, session_id=session_id, talk_id=talk_id, org=org,
    )
    if record.status in _TERMINAL_SR_STATUSES:
        return record.model_dump() | {"timed_out": False}

    # Subscribe to the runner's terminal publish; race against the timeout.
    # Mirrors the SSE handler's race-window mitigation: the runner can
    # publish a terminal event between our DB check and our subscribe
    # registration, so we ALSO re-poll the DB on each loop iteration.
    sub_iter = org.event_bus.subscribe(job_topic(job_id)).__aiter__()
    next_task: asyncio.Task | None = asyncio.create_task(sub_iter.__anext__())
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    try:
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            # Wake every ~1s to re-poll the DB even if no event arrives.
            wait_for = min(remaining, 1.0)
            done, _pending = await asyncio.wait({next_task}, timeout=wait_for)
            if not done:
                rec_now = org.db.get_job(job_id)
                if rec_now is None:
                    break
                if rec_now.status in _TERMINAL_SR_STATUSES:
                    return rec_now.model_dump() | {"timed_out": False}
                continue
            try:
                evt = next_task.result()
            except StopAsyncIteration:
                break
            next_task = None
            if evt.get("kind") == "terminal":
                rec_now = org.db.get_job(job_id) or record
                return rec_now.model_dump() | {"timed_out": False}
            next_task = asyncio.create_task(sub_iter.__anext__())
    finally:
        if next_task is not None and not next_task.done():
            next_task.cancel()
            try:
                await next_task
            except (asyncio.CancelledError, StopAsyncIteration, Exception):
                pass

    rec_final = org.db.get_job(job_id)
    if rec_final is None:
        # Shouldn't happen — the row existed at the top of the handler.
        raise HTTPException(
            status_code=404, detail={"code": "unknown_job", "job_id": job_id},
        )
    timed_out = rec_final.status not in _TERMINAL_SR_STATUSES
    return rec_final.model_dump() | {"timed_out": timed_out}


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
    trigger: str = "founder",
    trigger_actor: str | None = None,
) -> dict:
    """Shared core for HTTP and in-process run paths.

    ``trigger`` picks the audit action and the ``reviewed_by`` value:

    - ``"founder"`` (default) — the founder /run path. Logs job_run_started
      and stamps ``reviewed_by="founder"`` on the row.
    - ``"agent"`` — the auto-run-at-submit path. Logs job_auto_started with
      the agent's name, stamps ``reviewed_by=<agent>`` so audit traces stay
      coherent. ``trigger_actor`` is required when trigger="agent".
    """
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

    if trigger == "agent":
        if trigger_actor is None:
            raise ValueError("trigger_actor required when trigger='agent'")
        reviewer = trigger_actor
    else:
        reviewer = "founder"

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
            reviewer=reviewer,
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
    if trigger == "agent":
        audit.log_job_auto_started(
            task_id=record.task_id, job_id=job_id, agent=reviewer,
            cwd_resolved=str(cwd_resolved),
            timeout_seconds=timeout,
            interpreter=record.interpreter.value,
            persistent=record.persistent,
        )
    else:
        audit.log_job_run_started(
            task_id=record.task_id, job_id=job_id, reviewer=reviewer,
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
                max_output_bytes=record.max_output_bytes,
                publish=_sync_publish,
            )
        except FileNotFoundError:
            finished = _now_iso()
            try:
                org.db.transition_job_to_terminal(
                    job_id, status=JobStatus.FAILED,
                    exit_code=None, finished_at=finished, duration_ms=0,
                    stdout_head=None, stderr_head=None,
                    reason="spawn_failed",
                )
            except ValueError:
                pass
            audit.log_job_run_failed(
                task_id=record.task_id, job_id=job_id, reason="spawn_failed",
            )
            # Bridge: terminal status committed — resume any tasks blocked on this job.
            fire_resume_check_for_job(org, job_id)
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
                    reason="internal_error",
                )
            except ValueError:
                pass
            audit.log_job_run_failed(
                task_id=record.task_id, job_id=job_id, reason="internal_error",
            )
            # Bridge: terminal status committed — resume any tasks blocked on this job.
            fire_resume_check_for_job(org, job_id)
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
                reason=result.reason,
            )
        except ValueError:
            return

        # Bridge: terminal status committed — resume any tasks blocked on this job.
        fire_resume_check_for_job(org, job_id)

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
