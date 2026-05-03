"""Task submission and inspection endpoints."""
from __future__ import annotations

import json as _json
import logging
import os
import signal
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from src.daemon.auth import require_token
from src.daemon.org_state import OrgState
from src.daemon.routes._org_dep import OrgDep
from src.daemon.runner import enqueue_task
from src.daemon.state import DaemonState
from src.models import TaskRecord, TaskStatus

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[require_token()])

# Artifacts are fully inlined into the recall response when an agent asks for
# them, so cap the total to keep one recall under a comfortable prompt budget.
MAX_ARTIFACT_BYTES = 200 * 1024


class SubmitTask(BaseModel):
    team: str | None = None
    brief: str


@router.post("/tasks")
async def submit_task(body: SubmitTask, org: OrgDep, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    team = body.team or "engineering"
    registry = org.teams
    if registry is None or team not in registry.teams():
        valid = registry.teams() if registry is not None else []
        raise HTTPException(
            status_code=400,
            detail={"code": "unknown_team", "valid": valid},
        )
    manager = registry.manager_for_team(team)
    async with org.db_lock:
        task_id = org.db.next_task_id()
        org.db.insert_task(
            TaskRecord(
                id=task_id,
                brief=body.brief,
                team=team,
                assigned_agent=manager.name,
            )
        )

    enqueue_task(state, org.slug, task_id)
    return {"task_id": task_id, "team": team, "assigned_agent": manager.name}


@router.get("/tasks")
def list_tasks(org: OrgDep, limit: int = 20) -> dict:
    tasks = org.db.list_tasks(limit=limit)
    return {"tasks": [t.model_dump() for t in tasks]}


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

    return {
        "task": task.model_dump(),
        "results": org.db.get_task_results(task_id),
        "audit_log": audit_log,
        "revisit_chain": chain,
        "direct_revisits": direct_revisits,
        "predecessor_prior_status": prior_status,
    }


def _read_artifact(
    workspaces_dir: Path, assigned_agent: str | None, artifact_dir: str | None,
) -> dict | None:
    """Return {files, truncated} for the artifact folder, or None if unresolvable.

    Files are read as text; anything that fails decoding (binaries) is skipped.
    If the total inlined payload would exceed MAX_ARTIFACT_BYTES we flip to a
    path-only listing with truncated=True so the agent still sees the inventory.
    """
    if not assigned_agent or not artifact_dir:
        return None
    # artifact_dir is agent-supplied via the completion callback. Absolute paths
    # and `..` segments would let a buggy/malicious agent disclose arbitrary
    # readable files on the host, so confine the result to the assigned agent's
    # workspace by resolving both paths and checking containment.
    agent_root = (workspaces_dir / assigned_agent).resolve()
    base = (agent_root / artifact_dir).resolve()
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
        if total > MAX_ARTIFACT_BYTES:
            return {
                "files": [{"path": str(f.relative_to(base))} for f in all_files],
                "truncated": True,
            }
        files.append({"path": str(f.relative_to(base)), "content": text})
    return {"files": files, "truncated": False}


def _recall_node(
    org: OrgState, task_id: str, tree: bool, include_artifact: bool,
) -> dict | None:
    payload = org.db.get_recall_payload(task_id)
    if payload is None:
        return None
    if include_artifact:
        payload["artifact"] = _read_artifact(
            org.root / "workspaces",
            payload.get("assigned_agent"),
            payload.get("artifact_dir"),
        )
    if tree:
        child_ids = payload["children"]
        payload["children"] = [
            _recall_node(org, cid, tree=True, include_artifact=include_artifact)
            for cid in child_ids
        ]
    return payload


@router.get("/tasks/{task_id}/recall")
def recall_task(
    task_id: str,
    org: OrgDep,
    tree: bool = False,
    include_artifact: bool = False,
) -> dict:
    node = _recall_node(org, task_id, tree=tree, include_artifact=include_artifact)
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
    risks_flagged: list[str] = []
    dependencies: list[str] = []
    suggested_reviewer_focus: list[str] = []
    artifact_dir: str | None = None


@router.get("/tasks/{task_id}/events")
async def task_events(task_id: str, org: OrgDep):
    # Reject unknown task IDs up front — otherwise EventBus.subscribe() replays
    # no history for a fabricated id and then blocks forever, which makes
    # `opc tail <bad-id>` hang instead of surfacing a 404.
    if org.db.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")

    async def gen():
        async for event in org.event_bus.subscribe(task_id):
            yield {"data": _json.dumps(event)}

    return EventSourceResponse(gen())


@router.post("/tasks/{task_id}/completion")
async def submit_completion(task_id: str, body: CompletionBody, org: OrgDep) -> dict:
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
            artifact_dir=body.artifact_dir,
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
    logged as `action=progress` and broadcast on SSE so `opc tail` shows live
    movement on long-running tasks.
    """
    from src.infrastructure.audit_logger import AuditLogger

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


@router.post("/tasks/{task_id}/resolve-escalation")
async def resolve_escalation(
    task_id: str, body: ResolveEscalationBody, org: OrgDep, request: Request,
) -> dict:
    from src.infrastructure.audit_logger import AuditLogger
    from src.models import BlockKind, TaskStatus

    state: DaemonState = request.app.state.daemon
    if not body.rationale.strip():
        raise HTTPException(status_code=400, detail={"code": "rationale_required"})
    if body.decision not in ("approve", "reject"):
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
    resolved_note = f"Founder {body.decision}d: {body.rationale}"
    async with org.db_lock:
        if body.decision == "approve":
            new_status = TaskStatus.PENDING
        else:
            new_status = TaskStatus.FAILED
        org.db.update_task(
            task_id, status=new_status, block_kind=None, note=resolved_note,
        )
        AuditLogger(org.db).log_escalation_resolved(
            task_id=task_id, decision=body.decision, rationale=body.rationale
        )
    if body.decision == "approve":
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
        from src.orchestrator.run_step import _enqueue_parent_if_waiting
        _enqueue_parent_if_waiting(org.orchestrator, task_id)
    return {"ok": True, "task_id": task_id, "new_status": new_status.value}


class CancelBody(BaseModel):
    rationale: str = ""
    # Default cascades down the delegated subtree. The caller can ask for a
    # point-cancel with cascade=False but it's dangerous: a parent waiting on
    # a live child is cancelled while the child keeps running, leaving the
    # child with no observer for its eventual completion. Surfaced as a flag
    # rather than removed entirely because there are narrow cases (rogue-agent
    # isolation) where targeting a single node is right.
    cascade: bool = True


_TERMINAL_TASK_STATUSES = frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED})


class RevisitBody(BaseModel):
    founder_note: str | None = None


# Predecessor-root states that revisit accepts. Everything else is 409.
# `failed-cancelled` is not a DB value — it's the normalized label for
# (status=failed, cancelled_at!=NULL) that the response body returns and
# the team-manager prompt header surfaces.
_REVISIT_ELIGIBLE_STATUSES = frozenset({
    TaskStatus.FAILED, TaskStatus.COMPLETED,
})


def _classify_predecessor_status(task: TaskRecord) -> str | None:
    """Return the normalized prior_status label, or None if ineligible.

    Maps DB shape → the 4-valued spec vocabulary:
      failed + cancelled_at != NULL  → 'failed-cancelled'
      failed + cancelled_at == NULL  → 'failed'
      blocked(escalated)             → 'blocked-escalated'
      completed                      → 'completed'
    """
    from src.models import BlockKind
    if task.status == TaskStatus.FAILED:
        return "failed-cancelled" if task.cancelled_at is not None else "failed"
    if task.status == TaskStatus.COMPLETED:
        return "completed"
    if task.status == TaskStatus.BLOCKED and task.block_kind == BlockKind.ESCALATED:
        return "blocked-escalated"
    return None


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
    from src.infrastructure.audit_logger import AuditLogger
    from src.infrastructure.database import LineageTooDeep

    state: DaemonState = request.app.state.daemon

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
        from src.models import BlockKind as _BK
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
        ))
        audit = AuditLogger(org.db)
        audit.log_revisit_of(
            task_id=new_id,
            predecessor_root=predecessor.id,
            flagged=task_id,
            cascade=cascade,
            prior_status=prior_status,
            founder_note=body.founder_note,
        )
        audit.log_revisit_spawned(
            predecessor_task_id=predecessor.id, new_root=new_id,
        )

    enqueue_task(state, org.slug, new_id)

    return {
        "new_root_task_id": new_id,
        "predecessor_root_task_id": predecessor.id,
        "flagged_task_id": task_id,
        "cascade": cascade,
        "predecessor_status": prior_status,
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
    from src.infrastructure.audit_logger import AuditLogger

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
    # stays untouched.
    to_cancel: list[str] = []
    stack = [task_id]
    while stack:
        tid = stack.pop()
        t = org.db.get_task(tid)
        if t is None or t.status in _TERMINAL_TASK_STATUSES:
            continue
        to_cancel.append(tid)
        if body.cascade:
            stack.extend(org.db.get_children(tid))

    now = datetime.now(timezone.utc).isoformat()
    rationale = body.rationale.strip()
    note = f"cancelled by founder: {rationale}" if rationale else "cancelled by founder"

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
                task_id=tid, rationale=rationale, cascade=body.cascade,
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

    return {
        "ok": True,
        "task_id": task_id,
        "cancelled": to_cancel,
        "killed": killed,
    }
