"""Task submission and inspection endpoints."""
from __future__ import annotations

import json as _json
import logging
import os
import signal
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, field_validator
from sse_starlette.sse import EventSourceResponse

from runtime.daemon.auth import require_token
from runtime.daemon.org_state import OrgState
from runtime.daemon.routes._org_dep import OrgDep
from runtime.daemon.runner import enqueue_task
from runtime.daemon.state import DaemonState
from runtime.infrastructure.feishu.reply_parser import DispatchIntent  # re-export
from runtime.models import BlockKind, TaskRecord, TaskStatus

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[require_token()])

# Terminal statuses for task-active gating. Referenced by both the cancel
# route (l.~700) and the agent-callback routes (submit_completion, submit_progress)
# so it lives at module scope rather than next to its first use.
_TERMINAL_TASK_STATUSES = frozenset({
    TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.RESOLVED_SUPERSEDED,
})


def _require_task_active(task_id: str, task: TaskRecord | None) -> None:
    """Guard A: reject agent callbacks whose task is gone, terminal, or cancelled.

    Mirrors src/daemon/routes/scripts.py:64-90 validation order — existence
    before active, both before session ownership. A cancelled task reporting
    `session_mismatch` would mislead the agent into thinking its session was
    bumped when its whole task was terminated.

    Closes the cancel-race documented in
    docs/superpowers/specs/2026-05-26-cancel-race-design.md §5.1.
    """
    if task is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "unknown_task", "task_id": task_id},
        )
    if task.cancelled_at is not None or task.status in _TERMINAL_TASK_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "task_not_active",
                "task_id": task_id,
                "status": task.status.value,
                "cancelled": task.cancelled_at is not None,
            },
        )


def _task_to_dict(t: TaskRecord) -> dict:
    # Wire convention: every other task-shaped response (submit POST, recall)
    # exposes the primary key as `task_id`; talk/thread routes do the same via
    # their own helpers. Rename here so list + detail responses match.
    d = t.model_dump()
    d["task_id"] = d.pop("id")
    return d


# Outputs are fully inlined into the recall response when an agent asks for
# them, so cap the total to keep one recall under a comfortable prompt budget.
MAX_OUTPUT_BYTES = 200 * 1024


class SubmitTask(BaseModel):
    team: str | None = None
    brief: str
    owner: str | None = None  # assign a specific agent (default: team manager)


@router.post("/tasks")
async def submit_task(body: SubmitTask, org: OrgDep, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    registry = org.teams
    if registry is None:
        raise HTTPException(
            status_code=400,
            detail={"code": "unknown_team", "valid": []},
        )

    def _require_known_team(t: str) -> None:
        if t not in registry.teams():
            raise HTTPException(
                status_code=400,
                detail={"code": "unknown_team", "valid": registry.teams()},
            )

    if body.owner is not None:
        if body.owner not in registry.all_agents():
            raise HTTPException(
                status_code=400,
                detail={"code": "unknown_owner", "owner": body.owner,
                        "valid": registry.all_agents()},
            )
        # The owner's own team is authoritative for routing/audit. Derive it
        # when no team is requested; otherwise the requested team must match,
        # so the task.team that children inherit can't diverge from the owner.
        owner_team = (registry.team_for_agent(body.owner)
                      or registry.team_for_manager(body.owner))
        if body.team is None:
            team = owner_team
        else:
            team = body.team
            _require_known_team(team)
            if owner_team != team:
                raise HTTPException(
                    status_code=400,
                    detail={"code": "owner_team_mismatch", "owner": body.owner,
                            "owner_team": owner_team, "requested_team": team},
                )
        assigned = body.owner
    else:
        team = body.team or "engineering"
        _require_known_team(team)
        assigned = registry.manager_for_team(team).name
    async with org.db_lock:
        task_id = org.db.next_task_id()
        org.db.insert_task(
            TaskRecord(
                id=task_id,
                brief=body.brief,
                team=team,
                assigned_agent=assigned,
            )
        )

    enqueue_task(state, org.slug, task_id)
    return {"task_id": task_id, "team": team, "assigned_agent": assigned}


@router.get("/tasks")
def list_tasks(
    org: OrgDep,
    limit: int = 20,
    assigned_agent: str | None = None,
    before: str | None = None,
    status: str | None = None,
    block_kind: str | None = None,
) -> dict:
    # Cursor pagination: `before` is the task_id of the last item on the
    # previous page. `next_cursor` is the last id of this page when the page
    # is full (heuristic — caller stops when next_cursor is null OR the next
    # page comes back empty). When `before` references a missing task, the
    # database returns [] and we surface that as the end of the list.
    # `status` / `block_kind` are read-only equality filters for backlog
    # queries (e.g. `tasks --status blocked --block-kind escalated`).
    tasks = org.db.list_tasks(
        limit=limit, assigned_agent=assigned_agent, before_task_id=before,
        status=status, block_kind=block_kind,
    )
    next_cursor = tasks[-1].id if len(tasks) == limit else None
    return {
        "tasks": [_task_to_dict(t) for t in tasks],
        "next_cursor": next_cursor,
    }


@router.get("/tasks/{task_id}")
def get_task(task_id: str, org: OrgDep) -> dict:
    task = org.db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")

    # Revisit context: chain (this task back to original), direct revisits
    # (tasks that revisit THIS task), and the predecessor's normalized
    # prior_status (pulled from the revisit_of audit entry).
    # truncate=True: revisit history grows naturally over a task's lifetime,
    # so the read path must not 500 once the chain exceeds the defensive bound.
    chain = [t.id for t in org.db.walk_revisit_chain(task_id, truncate=True)]
    direct_revisits = org.db.get_direct_revisits(task_id)
    audit_log = org.db.get_audit_logs(task_id)
    prior_status = None
    if task.revisit_of_task_id is not None:
        for entry in audit_log:
            if entry["action"] == "revisit_of":
                payload = entry.get("payload") or {}
                prior_status = payload.get("prior_status")
                break

    # When a task is blocked waiting for jobs, include the id+status of each
    # blocking job so `happyranch details` can show the founder what to act on.
    blocked_on_jobs: list[dict] | None = None
    if task.status == TaskStatus.BLOCKED and task.block_kind == BlockKind.BLOCKED_ON_JOB:
        job_ids = _json.loads(task.blocked_on_job_ids or "[]")
        blocked_on_jobs = [
            {"job_id": jid, "status": org.db.get_job_status(jid) or "unknown"}
            for jid in job_ids
        ]

    active_chain = None
    if task.active_chain is not None:
        try:
            active_chain = _json.loads(task.active_chain)
        except _json.JSONDecodeError:
            active_chain = None  # defensive — never 500 on malformed on-disk state

    return {
        "task": _task_to_dict(task),
        "results": org.db.get_task_results(task_id),
        "audit_log": audit_log,
        "revisit_chain": chain,
        "direct_revisits": direct_revisits,
        "predecessor_prior_status": prior_status,
        "blocked_on_jobs": blocked_on_jobs,
        "active_chain": active_chain,
    }


def _read_output(
    workspaces_dir: Path, assigned_agent: str | None, output_dir: str | None,
) -> dict | None:
    """Return {files, truncated} for the output folder, or None if unresolvable.

    Files are read as text; anything that fails decoding (binaries) is skipped.
    If the total inlined payload would exceed MAX_OUTPUT_BYTES we flip to a
    path-only listing with truncated=True so the agent still sees the inventory.
    """
    if not assigned_agent or not output_dir:
        return None
    # output_dir is agent-supplied via the completion callback. Absolute paths
    # and `..` segments would let a buggy/malicious agent disclose arbitrary
    # readable files on the host, so confine the result to the assigned agent's
    # workspace by resolving both paths and checking containment.
    agent_root = (workspaces_dir / assigned_agent).resolve()
    base = (agent_root / output_dir).resolve()
    if not base.is_relative_to(agent_root):
        return None
    if not base.exists():
        return {"files": [], "truncated": False}
    all_files = sorted(f for f in base.rglob("*") if f.is_file())
    files: list[dict] = []
    total = 0
    for f in all_files:
        try:
            text = f.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        total += len(text.encode("utf-8"))
        if total > MAX_OUTPUT_BYTES:
            return {
                "files": [{"path": str(f.relative_to(base))} for f in all_files],
                "truncated": True,
            }
        files.append({"path": str(f.relative_to(base)), "content": text})
    return {"files": files, "truncated": False}


def _recall_node(
    org: OrgState, task_id: str, tree: bool, include_output: bool,
) -> dict | None:
    payload = org.db.get_recall_payload(task_id)
    if payload is None:
        return None
    if include_output:
        payload["output"] = _read_output(
            org.root / "workspaces",
            payload.get("assigned_agent"),
            payload.get("output_dir"),
        )
    if tree:
        child_ids = payload["children"]
        payload["children"] = [
            _recall_node(org, cid, tree=True, include_output=include_output)
            for cid in child_ids
        ]
    return payload


@router.get("/tasks/{task_id}/recall")
def recall_task(
    task_id: str,
    org: OrgDep,
    tree: bool = False,
    include_output: bool = False,
) -> dict:
    node = _recall_node(org, task_id, tree=tree, include_output=include_output)
    if node is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    return node


class CompletionBody(BaseModel):
    session_id: str
    agent: str
    status: str
    confidence: int
    output_summary: str
    # Manager-only. Structured next-step decision; workers omit or pass null.
    # Must be a dict matching the NextStep schema if present — validated
    # on the orchestrator side when the parser runs.
    decision: dict | None = None
    # Worker-reported outcome label for inline delegation chains. Free string;
    # per-team vocabulary (APPROVE, PASS, REQUEST_CHANGES, etc.) defined in
    # each team's workflow KB entry. Omit when the task is not part of a chain
    # or when the worker has no verdict to report.
    verdict: str | None = None
    risks_flagged: list[str] = []
    dependencies: list[str] = []
    suggested_reviewer_focus: list[str] = []
    output_dir: str | None = None
    waiting_on_job_ids: list[str] = []


@router.get("/tasks/{task_id}/events")
async def task_events(task_id: str, org: OrgDep):
    # Reject unknown task IDs up front — otherwise EventBus.subscribe() replays
    # no history for a fabricated id and then blocks forever, which makes
    # `happyranch tail <bad-id>` hang instead of surfacing a 404.
    if org.db.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")

    async def gen():
        async for event in org.event_bus.subscribe(task_id):
            yield {"data": _json.dumps(event)}

    return EventSourceResponse(gen())


@router.post("/tasks/{task_id}/completion")
async def submit_completion(task_id: str, body: CompletionBody, org: OrgDep) -> dict:
    # Task-active gate runs BEFORE session ownership (see _require_task_active).
    _require_task_active(task_id, org.db.get_task(task_id))
    expected = org.sessions.get_active(task_id, body.agent)
    # Reject callbacks the daemon never spawned. Both branches are 409 — the
    # tracker is the source of truth for "is this a real session". Unknown
    # comes first so an empty tracker can't silently accept a fabricated id.
    if expected is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "unknown_session", "task_id": task_id, "agent": body.agent},
        )
    if expected != body.session_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "session_mismatch", "active": expected, "got": body.session_id},
        )
    # Spec §6.2: validate waiting_on_job_ids if EXPLICITLY present. We check
    # model_fields_set rather than truthiness so we can distinguish "client
    # omitted the field" (legacy escalate path, no validation) from "client
    # explicitly sent []" (malformed payload — reject with 400). The truthy
    # guard collapses both cases, silently bypassing the contract.
    if "waiting_on_job_ids" in body.model_fields_set:
        if not body.waiting_on_job_ids:
            raise HTTPException(
                status_code=400,
                detail={"code": "empty_waiting_on_job_ids"},
            )
        if body.status != "blocked":
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "waiting_on_job_ids_requires_blocked",
                    "got_status": body.status,
                },
            )
        deduped = sorted(set(body.waiting_on_job_ids))
        for jid in deduped:
            owner = org.db.get_job_owner_task_id(jid)
            if owner is None:
                raise HTTPException(
                    status_code=404,
                    detail={"code": "job_not_found", "job_id": jid},
                )
            if owner != task_id:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "job_not_owned_by_task",
                        "job_id": jid,
                        "owner_task_id": owner,
                    },
                )
        # Persist the deduped list so run_step_impl sees the cleaned-up payload.
        body.waiting_on_job_ids = deduped
    decision_json = (
        _json.dumps(body.decision) if body.decision is not None else None
    )
    async with org.db_lock:
        org.db.insert_task_result(
            task_id=task_id,
            agent=body.agent,
            session_id=body.session_id,
            status=body.status,
            output_summary=body.output_summary,
            decision_json=decision_json,
            confidence_score=body.confidence,
            risks_flagged=body.risks_flagged,
            output_dir=body.output_dir,
            waiting_on_job_ids=body.waiting_on_job_ids or None,
            verdict=body.verdict,
        )
    # Clear the tracker so a duplicate POST for the same session is rejected as
    # unknown_session rather than silently persisting a second row.
    org.sessions.clear(task_id, body.agent)
    # TODO(events): subscribers that connect after this point won't replay
    # `completion_reported`. The terminal task_* event is still synthesized
    # from the DB status, but per-agent completion beats are lost. Acceptable
    # today because the orchestrator consumes completions via DB (not SSE) and
    # SSE is for human observers.
    await org.event_bus.publish(task_id, {
        "type": "completion_reported",
        "agent": body.agent,
        "session_id": body.session_id,
        "status": body.status,
    })
    return {"ok": True}


class ProgressBody(BaseModel):
    session_id: str
    agent: str
    message: str


@router.post("/tasks/{task_id}/progress")
async def submit_progress(task_id: str, body: ProgressBody, org: OrgDep) -> dict:
    """Agent-controlled mid-task progress note.

    Same auth shape as /completion (active session must match), but does NOT
    clear the tracker — the agent keeps working after a progress beat. Audit-
    logged as `action=progress` and broadcast on SSE so `happyranch tail` shows live
    movement on long-running tasks.
    """
    from runtime.infrastructure.audit_logger import AuditLogger

    # Task-active gate runs BEFORE session ownership (see _require_task_active).
    _require_task_active(task_id, org.db.get_task(task_id))
    expected = org.sessions.get_active(task_id, body.agent)
    if expected is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "unknown_session", "task_id": task_id, "agent": body.agent},
        )
    if expected != body.session_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "session_mismatch", "active": expected, "got": body.session_id},
        )
    message = body.message.strip()
    if not message:
        raise HTTPException(
            status_code=400,
            detail={"code": "message_required"},
        )
    async with org.db_lock:
        AuditLogger(org.db).log_progress(
            task_id=task_id, agent=body.agent, message=message,
        )
    await org.event_bus.publish(task_id, {
        "type": "progress",
        "agent": body.agent,
        "session_id": body.session_id,
        "message": message,
    })
    return {"ok": True}


class ResolveEscalationBody(BaseModel):
    decision: str  # "approve" | "reject"
    rationale: str


async def resolve_escalation_in_process(
    org,
    state,
    *,
    task_id: str,
    decision: str,
    rationale: str,
) -> str:
    """Same DB transition / audit / queue re-enqueue as the HTTP handler at
    POST /tasks/{task_id}/resolve-escalation. Reused by the Feishu listener.

    Returns the new task status value (e.g. "pending" or "failed").
    Raises HTTPException for the same validation failures the route raises so
    the HTTP wrapper can re-raise as-is.
    """
    from runtime.infrastructure.audit_logger import AuditLogger
    from runtime.models import BlockKind, TaskStatus
    from runtime.orchestrator.run_step import (
        _enqueue_parent_if_waiting,
        _kill_jobs_for_terminating_task,
    )

    if not rationale.strip():
        raise HTTPException(status_code=400, detail={"code": "rationale_required"})
    if decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail={"code": "invalid_decision"})
    task = org.db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    if task.status != TaskStatus.BLOCKED or task.block_kind != BlockKind.ESCALATED:
        raise HTTPException(
            status_code=409,
            detail={"code": "task_not_escalated", "current_status": task.status.value},
        )
    # approve resumes the work itself; reject terminates it. Pre-resolve we
    # used to mark approve→COMPLETED and lean on parent wake-up to carry the
    # work forward. That left root escalations (no parent) silently dropping
    # the work the approval was meant to authorize. New shape: approve sends
    # the task back to PENDING with the rationale on `note`, and the team
    # manager picks it up on the next step with a one-shot prompt header
    # (see `_resolved_escalation_header_if_applicable` in run_step.py).
    resolved_note = f"Founder {decision}d: {rationale}"
    async with org.db_lock:
        new_status = TaskStatus.PENDING if decision == "approve" else TaskStatus.FAILED
        org.db.update_task(
            task_id, status=new_status, block_kind=None, note=resolved_note,
        )
        AuditLogger(org.db).log_escalation_resolved(
            task_id=task_id, decision=decision, rationale=rationale,
        )
        # Best-effort: mark any open Feishu notification rows for this task
        # consumed, so they don't dangle if the founder later replies in-thread.
        for nrow in org.db.list_open_notifications_for_task(task_id):
            org.db.consume_escalation_notification(
                nrow["feishu_message_id"], consumed_by="cli-fallback",
            )
    if decision == "approve":
        # Re-enqueue self. The manager's next step sees the rationale via the
        # escalation-resolved prompt header (see
        # ``_resolved_escalation_header_if_applicable`` in run_step.py).
        # Parent (if any) stays blocked (DELEGATED) and will be woken when
        # this task next reaches a true terminal — no immediate wake here.
        if state.queue is not None:
            state.queue.put_nowait(org.slug, task_id)
    else:
        # Cascade-fail upward: the parent (if any) must learn this branch
        # failed. The org's Orchestrator owns its slug + queue + db, so we
        # just pass it through; ``_enqueue_parent_if_waiting`` calls _fail
        # on the parent on FAILED siblings.
        _enqueue_parent_if_waiting(org.orchestrator, task_id)
        # Reject is terminal — kill any persistent jobs this task owns.
        _kill_jobs_for_terminating_task(org.orchestrator, task_id)
    return new_status.value


@router.post("/tasks/{task_id}/resolve-escalation")
async def resolve_escalation(
    task_id: str, body: ResolveEscalationBody, org: OrgDep, request: Request,
) -> dict:
    state: DaemonState = request.app.state.daemon
    new_status = await resolve_escalation_in_process(
        org, state,
        task_id=task_id, decision=body.decision, rationale=body.rationale,
    )
    return {"ok": True, "task_id": task_id, "new_status": new_status}


class CancelBody(BaseModel):
    rationale: str = ""
    # Default cascades down the delegated subtree. The caller can ask for a
    # point-cancel with cascade=False but it's dangerous: a parent waiting on
    # a live child is cancelled while the child keeps running, leaving the
    # child with no observer for its eventual completion. Surfaced as a flag
    # rather than removed entirely because there are narrow cases (rogue-agent
    # isolation) where targeting a single node is right.
    cascade: bool = True
    # Caller-declared actor for attribution. Advisory only — founder and agents
    # share one bearer token, so this is not validated. Omitted/blank → "founder",
    # preserving the original founder-only behavior byte-for-byte.
    actor: str | None = None


class RevisitBody(BaseModel):
    founder_note: str | None = None
    # Founder-supplied per-task subprocess timeout (seconds). Persisted on the
    # new root and inherited by every delegated child + auto-revisit. NULL
    # falls through to the predecessor's value (so a manual revisit of an
    # already-bumped task keeps the bump) and then to org/Settings.
    session_timeout_seconds: int | None = None

    @field_validator("session_timeout_seconds")
    @classmethod
    def _positive_int(cls, v: int | None) -> int | None:
        if v is None:
            return None
        if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
            raise ValueError("session_timeout_seconds must be a positive integer")
        return v


# Predecessor-root states that revisit accepts. Everything else is 409.
# `failed-cancelled` is not a DB value — it's the normalized label for
# (status=failed, cancelled_at!=NULL) that the response body returns and
# the team-manager prompt header surfaces.
_REVISIT_ELIGIBLE_STATUSES = frozenset({
    TaskStatus.FAILED, TaskStatus.COMPLETED,
})


class DispatchError(Exception):
    """Raised by dispatch_via_feishu for validation and dispatch failures.

    reason is one of: empty_brief, unknown_team, dispatch_failed.
    valid_teams is populated for unknown_team so callers can surface the list.
    """

    def __init__(self, reason: str, valid_teams: list[str] | None = None) -> None:
        self.reason = reason
        self.valid_teams = valid_teams or []
        super().__init__(reason)


async def dispatch_via_feishu(
    org,
    state,
    *,
    intent: DispatchIntent,
    sender_id: str,
    event_id: str,
) -> tuple[str, str]:
    """Create a task from a Feishu DISPATCH intent. Mirrors POST /tasks.

    Returns (task_id, resolved_team).
    Raises DispatchError(reason=...) with reason in:
        empty_brief, unknown_team, dispatch_failed.
    """
    from runtime.infrastructure.audit_logger import AuditLogger

    if not intent.brief or not intent.brief.strip():
        raise DispatchError("empty_brief")

    team = intent.team or "engineering"
    registry = org.teams
    valid = list(registry.teams()) if registry is not None else []
    if registry is None or team not in valid:
        raise DispatchError("unknown_team", valid_teams=valid)

    try:
        manager = registry.manager_for_team(team)
        async with org.db_lock:
            task_id = org.db.next_task_id()
            org.db.insert_task(TaskRecord(
                id=task_id,
                brief=intent.brief.strip(),
                team=team,
                assigned_agent=manager.name,
            ))
            AuditLogger(org.db).log_dispatch_via_feishu_accepted(
                task_id=task_id, team=team, sender_id=sender_id,
                feishu_event_id=event_id,
            )
    except DispatchError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DispatchError("dispatch_failed") from exc

    enqueue_task(state, org.slug, task_id)
    return task_id, team


@dataclass(frozen=True)
class RevisitResult:
    """Structured return value from revisit_from_notification.

    Carries all fields needed to build the HTTP response body, so the
    route does not need a second walk_ancestors call after the helper
    returns (eliminates the LineageTooDeep race and the double DB round-trip).
    """
    new_root_id: str
    predecessor_root_id: str
    flagged_task_id: str
    cascade: list[str]
    prior_status: str


def _classify_predecessor_status(task: TaskRecord) -> str | None:
    """Return the normalized prior_status label, or None if ineligible.

    Maps DB shape → the 4-valued spec vocabulary:
      failed + cancelled_at != NULL  → 'failed-cancelled'
      failed + cancelled_at == NULL  → 'failed'
      blocked(escalated)             → 'blocked-escalated'
      completed                      → 'completed'
    """
    from runtime.models import BlockKind
    if task.status == TaskStatus.FAILED:
        return "failed-cancelled" if task.cancelled_at is not None else "failed"
    if task.status == TaskStatus.COMPLETED:
        return "completed"
    if task.status == TaskStatus.BLOCKED and task.block_kind == BlockKind.ESCALATED:
        return "blocked-escalated"
    return None


async def revisit_from_notification(
    org,
    state,
    *,
    task_id: str,
    founder_note: str | None,
    actor: str,
    session_timeout_seconds: int | None = None,
) -> RevisitResult:
    """Spawn a new root task linked to the predecessor.

    Mirrors the POST /tasks/{id}/revisit HTTP handler. Reused by the
    Feishu listener so HTTP and Feishu surfaces cannot drift.

    Args:
        actor: "cli" (HTTP route) or "feishu-reply" (listener). Recorded
            on the revisit_of audit row.
        session_timeout_seconds: Override; if None, inherit from predecessor.

    Returns a RevisitResult with all fields needed by the caller.
    Raises HTTPException for 404 / 409.
    """
    from runtime.infrastructure.audit_logger import AuditLogger
    from runtime.infrastructure.database import LineageTooDeep

    flagged = org.db.get_task(task_id)
    if flagged is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")

    # Walk to the predecessor root. Defensive bound guards against corrupt cycles.
    try:
        chain = org.db.walk_ancestors(task_id, max_hops=20)
    except LineageTooDeep as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "lineage_too_deep", "reason": str(exc)},
        )
    if not chain:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    predecessor = chain[-1]  # root is last; chain is [flagged, ..., root]

    prior_status = _classify_predecessor_status(predecessor)
    if prior_status is None:
        from runtime.models import BlockKind as _BK
        raise HTTPException(
            status_code=409,
            detail={
                "code": "cannot_revisit",
                "reason": f"predecessor {predecessor.id} is {predecessor.status.value}",
                "predecessor_root_task_id": predecessor.id,
                "predecessor_status": predecessor.status.value,
                "block_kind": (
                    predecessor.block_kind.value
                    if isinstance(predecessor.block_kind, _BK) else None
                ),
            },
        )

    # cascade: [predecessor_root, ..., flagged]. chain is [flagged, ..., root],
    # so reverse it. When flagged == root, this is a single-element list.
    cascade = [t.id for t in reversed(chain)]

    new_timeout = (
        session_timeout_seconds
        if session_timeout_seconds is not None
        else predecessor.session_timeout_seconds
    )
    async with org.db_lock:
        new_id = org.db.next_task_id()
        org.db.insert_task(TaskRecord(
            id=new_id,
            brief=predecessor.brief,
            team=predecessor.team,
            assigned_agent=predecessor.assigned_agent,
            status=TaskStatus.PENDING,
            parent_task_id=None,
            revisit_of_task_id=predecessor.id,
            session_timeout_seconds=new_timeout,
        ))
        audit = AuditLogger(org.db)
        audit.log_revisit_of(
            task_id=new_id,
            predecessor_root=predecessor.id,
            flagged=task_id,
            cascade=cascade,
            prior_status=prior_status,
            founder_note=founder_note,
            actor=actor,
        )
        audit.log_revisit_spawned(
            predecessor_task_id=predecessor.id, new_root=new_id,
        )
        # When the founder uses the CLI to revisit, any open Feishu failure
        # notification row for this task is implicitly resolved — consume
        # it with cli-fallback so a later in-thread REVISIT reply silently
        # no-ops. Mirrors resolve_escalation_in_process's behavior.
        # Feishu-reply path: listener consumes itself at step 8r (avoid race).
        if actor == "cli":
            for nrow in org.db.list_open_notifications_for_task(task_id):
                if nrow.get("kind") == "failure":
                    org.db.consume_escalation_notification(
                        nrow["feishu_message_id"], consumed_by="cli-fallback",
                    )

    enqueue_task(state, org.slug, new_id)

    return RevisitResult(
        new_root_id=new_id,
        predecessor_root_id=predecessor.id,
        flagged_task_id=task_id,
        cascade=cascade,
        prior_status=prior_status,
    )


@router.post("/tasks/{task_id}/revisit")
async def revisit_task(
    task_id: str, body: RevisitBody, org: OrgDep, request: Request,
) -> dict:
    """Founder-initiated: spawn a fresh root that inherits the predecessor's
    brief and references it via audit-log entries.

    The predecessor root (the ancestor we walk up to) MUST be in a terminal-ish
    state — see `_classify_predecessor_status`. The flagged task (the id the
    founder gave us) can be in any state; only the root's status is validated.
    """
    state: DaemonState = request.app.state.daemon
    result = await revisit_from_notification(
        org, state,
        task_id=task_id,
        founder_note=body.founder_note,
        actor="cli",
        session_timeout_seconds=body.session_timeout_seconds,
    )
    return {
        "new_root_task_id": result.new_root_id,
        "predecessor_root_task_id": result.predecessor_root_id,
        "flagged_task_id": result.flagged_task_id,
        "cascade": result.cascade,
        "predecessor_status": result.prior_status,
    }


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(
    task_id: str, body: CancelBody, org: OrgDep,
) -> dict:
    """Founder-initiated cancel of a task and (by default) its descendants.

    Order of operations matters. We stamp the DB row *before* sending SIGTERM
    so that run_step's post-Popen classifier — which re-enters with a
    subprocess rc=-15 looking like a normal failure — observes
    ``status=FAILED`` and ``cancelled_at != NULL`` and short-circuits instead
    of overwriting the founder's note with "agent session failed (rc=-15)".

    The corresponding idempotence guards in ``_complete`` / ``_fail`` are the
    other half of the race lock.
    """
    from runtime.infrastructure.audit_logger import AuditLogger
    from runtime.orchestrator.run_step import _kill_jobs_for_terminating_task

    root = org.db.get_task(task_id)
    if root is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    if root.status in _TERMINAL_TASK_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "task_already_terminal", "current_status": root.status.value},
        )

    # BFS subtree walk (cascade=True) or single-task (cascade=False). Only
    # non-terminal rows are collected — anything already COMPLETED/FAILED
    # stays untouched.  Also capture prior statuses so Phase 1 can route
    # PENDING-only followup calls without a second DB round-trip.
    to_cancel: list[str] = []
    prior_statuses: dict[str, TaskStatus] = {}
    stack = [task_id]
    while stack:
        tid = stack.pop()
        t = org.db.get_task(tid)
        if t is None or t.status in _TERMINAL_TASK_STATUSES:
            continue
        to_cancel.append(tid)
        prior_statuses[tid] = t.status
        if body.cascade:
            stack.extend(org.db.get_children(tid))

    now = datetime.now(timezone.utc).isoformat()
    rationale = body.rationale.strip()
    actor = (body.actor or "").strip() or "founder"
    note = f"cancelled by {actor}: {rationale}" if rationale else f"cancelled by {actor}"

    # Phase 1: DB writes + audit under the lock, to serialise with run_step
    # transitions. Collect PIDs while we hold the lock — we'll SIGTERM outside.
    pids_to_kill: list[tuple[str, str, int]] = []
    audit = AuditLogger(org.db)
    async with org.db_lock:
        for tid in to_cancel:
            org.db.update_task(
                tid,
                status=TaskStatus.FAILED,
                block_kind=None,
                note=note,
                cancelled_at=now,
                completed_at=now,
            )
            for agent, pid in org.sessions.iter_task_pids(tid):
                pids_to_kill.append((tid, agent, pid))
            audit.log_task_cancelled(
                task_id=tid, rationale=rationale, cascade=body.cascade, actor=actor,
            )

    # Phase 1b: fire thread followup for PENDING and BLOCKED tasks.
    #
    # Two-site coverage (disjoint conditions, no double-fire risk):
    #   • PENDING + BLOCKED → cancel route owns the followup here, because these
    #     tasks have no live subprocess — SIGTERM is never sent so run_step never
    #     runs and the cancel-race guard in run_step never triggers.
    #   • IN_PROGRESS → cancel route sends SIGTERM (Phase 2 below); the
    #     subprocess eventually exits with rc=-15; run_step's cancel-race guard
    #     (``refetch.cancelled_at is not None → return``) fires _before_ Site B,
    #     so run_step's cancel-race guard fires the helper there instead.
    #
    # The disjoint condition ensures we never fire twice for the same task.
    _CANCEL_ROUTE_FIRES_FOR = {TaskStatus.PENDING, TaskStatus.BLOCKED}
    from runtime.orchestrator.run_step import _maybe_post_thread_followup
    for tid in to_cancel:
        if prior_statuses.get(tid) in _CANCEL_ROUTE_FIRES_FOR:
            _maybe_post_thread_followup(
                org.orchestrator, tid,
                status=TaskStatus.FAILED, auto_revisit_spawned=False,
            )

    # Phase 2: deliver SIGTERM to any live subprocesses attached to cancelled
    # tasks. os.kill runs outside the db_lock so a slow signal delivery can't
    # stall concurrent DB writers. ProcessLookupError means the subprocess
    # already exited — fine, the DB row is already in its terminal shape.
    killed: list[dict] = []
    for tid, agent, pid in pids_to_kill:
        try:
            os.kill(pid, signal.SIGTERM)
            killed.append({"task_id": tid, "agent": agent, "pid": pid})
        except ProcessLookupError:
            pass
        except OSError as exc:
            logger.warning(
                "cancel %s: os.kill(%s, SIGTERM) failed: %s", tid, pid, exc,
            )
        # Clear the tracker entry so a parent auto-resume (if one gets
        # enqueued via run_step's dependent-child check) can't find a stale
        # pid and mis-route a subsequent cancel.
        org.sessions.clear(tid, agent)

    # Phase 3: publish terminal events for any live SSE tails. EventBus
    # recognises `task_failed` as terminal (see _TERMINAL_TYPES in
    # event_bus.py) and closes the stream on the observer side.
    for tid in to_cancel:
        await org.event_bus.publish(tid, {
            "type": "task_failed",
            "outcome": "cancelled",
            "task_id": tid,
        })

    # Phase 4: kill persistent jobs owned by any cancelled task. Fire-and-
    # forget so the route response isn't blocked on the 5s SIGTERM grace.
    for tid in to_cancel:
        _kill_jobs_for_terminating_task(org.orchestrator, tid)

    return {
        "ok": True,
        "task_id": task_id,
        "cancelled": to_cancel,
        "killed": killed,
    }
