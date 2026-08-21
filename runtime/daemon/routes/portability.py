"""Organization-portability routes (THR-187 Slice A): read-only preflight and
founder-only audited reconciliation.

Both routes are CLI-private daemon surfaces (no TS client, no browser contract).
They are mounted under ``/api/v1/orgs/{slug}`` like every other per-org route.

* ``GET /portability-preflight`` — read-only. Exhaustively classifies every
  direct org-root child and computes quiescence. Reports blockers and possible
  zombies; creates no archive, staging, fence, cancellation, import, or other
  transfer side effect.
* ``POST /reconcile-portability`` — founder/master-bearer-only (reuses the
  existing ``_require_human`` dependency unchanged). Revalidates exactly one
  named candidate as a true zombie under the org DB lock and invokes the shared
  result/terminalization seam. Preflight never calls reconciliation, and
  reconciliation offers no export-cancellation path.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from runtime.daemon.auth import _require_human, require_token
from runtime.daemon.routes._org_dep import OrgDep
from runtime.daemon.state import DaemonState
from runtime.daemon.zombie_reaper import _consume_zombie_fingerprint, _pid_is_dead
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.models import TaskStatus
from runtime.orchestrator.run_step import _enqueue_parent_if_waiting
from runtime.portability.eligibility import (
    TaskLiveness,
    compute_eligibility,
    is_true_zombie,
)
from runtime.portability.roots import classify_root_entries

router = APIRouter(dependencies=[require_token()])

_ACTIVE_DREAM_STATUSES = {"pending", "running"}
_ACTIVE_WORK_HOUR_STATUSES = {"pending", "running"}
_ACTIVE_SCHEDULE_STATUSES = {"armed", "firing"}


def _task_state_summary(task) -> dict:
    return {
        "task_id": task.id,
        "status": task.status.value,
        "block_kind": task.block_kind.value if task.block_kind else None,
        "last_heartbeat": task.last_heartbeat.isoformat() if task.last_heartbeat else None,
        "executor_pid": task.executor_pid,
        "assigned_agent": task.assigned_agent,
    }


def _gather_task_liveness(org) -> list[TaskLiveness]:
    out: list[TaskLiveness] = []
    for task_id in org.db.get_nonterminal_task_ids():
        t = org.db.get_task(task_id)
        if t is None:
            continue
        out.append(TaskLiveness(
            task_id=t.id,
            status=t.status.value,
            block_kind=t.block_kind.value if t.block_kind else None,
            last_heartbeat=t.last_heartbeat,
            executor_pid=t.executor_pid,
            assigned_agent=t.assigned_agent,
        ))
    return out


def _active_job_ids(org) -> list[str]:
    return [j.id for j in org.db.list_jobs_db(status=["pending", "running"], limit=500)]


def _active_dream_ids(org) -> list[str]:
    return org.db.list_dream_ids_by_status(_ACTIVE_DREAM_STATUSES)


def _active_work_hour_ids(org) -> list[str]:
    return org.db.work_hours.list_ids_by_status(_ACTIVE_WORK_HOUR_STATUSES)


def _active_schedule_ids(org) -> list[str]:
    return org.db.schedules.list_ids_by_status(_ACTIVE_SCHEDULE_STATUSES)


@router.get("/portability-preflight")
def portability_preflight(slug: str, org: OrgDep, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    inventory = classify_root_entries(org.root)
    now = datetime.now(timezone.utc)
    eligibility = compute_eligibility(
        tasks=_gather_task_liveness(org),
        active_session_count=org.sessions.count_active(),
        queued_for_org=1 if slug in state.queue.pending_slugs() else 0,
        pending_invocation_count=len(org.db.list_pending_thread_invocations()),
        active_job_ids=_active_job_ids(org),
        active_dream_ids=_active_dream_ids(org),
        active_work_hour_ids=_active_work_hour_ids(org),
        active_schedule_ids=_active_schedule_ids(org),
        now=now,
        pid_is_dead=_pid_is_dead,
    )
    return {
        "slug": slug,
        "root": str(org.root),
        "eligible": eligibility.eligible and not inventory.has_rejections,
        "classification": {
            "entries": [e.model_dump() for e in inventory.entries],
            "rejections": [e.model_dump() for e in inventory.rejected],
        },
        "eligibility": {
            "eligible": eligibility.eligible,
            "blockers": eligibility.blockers(),
            "possible_zombies": [z.__dict__ for z in eligibility.possible_zombies],
        },
    }


class ReconcilePortabilityBody(BaseModel):
    candidate_task_id: str = Field(min_length=1)
    evidence: dict = Field(default_factory=dict)
    disposition: str = "cancel"


@router.post("/reconcile-portability")
async def reconcile_portability(
    slug: str,
    body: ReconcilePortabilityBody,
    org: OrgDep,
    request: Request,
    _: None = Depends(_require_human),
) -> dict:
    if body.disposition not in ("cancel", "consume_result"):
        raise HTTPException(
            status_code=422,
            detail={"code": "bad_disposition", "disposition": body.disposition},
        )

    request_hash = hashlib.sha256(
        json.dumps(body.model_dump(), sort_keys=True).encode("utf-8")
    ).hexdigest()

    task_id = body.candidate_task_id
    now = datetime.now(timezone.utc)

    async with org.db_lock:
        task = org.db.get_task(task_id)
        if task is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "unknown_task", "task_id": task_id},
            )
        before = _task_state_summary(task)

        is_zombie, reason = is_true_zombie(
            status=task.status.value,
            block_kind=task.block_kind.value if task.block_kind else None,
            last_heartbeat=task.last_heartbeat,
            executor_pid=task.executor_pid,
            now=now,
            pid_is_dead=_pid_is_dead,
        )
        if not is_zombie:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "not_a_zombie",
                    "task_id": task_id,
                    "reason": reason,
                },
            )

        if body.disposition == "consume_result":
            fingerprint = None
            if task.current_session_id is not None and task.assigned_agent is not None:
                fingerprint = org.db.get_latest_task_result(
                    task_id, task.assigned_agent, task.current_session_id,
                )
            if fingerprint is None:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "no_result_to_consume", "task_id": task_id},
                )
            # Shared result seam: the same orphaned-result consumption the
            # ongoing zombie reaper uses.
            _consume_zombie_fingerprint(org.db, task_id, fingerprint, task, org.orchestrator)
        else:  # cancel — the reaper's terminalization sequence
            now_iso = now.isoformat()
            org.db.update_task(
                task_id,
                status=TaskStatus.CANCELLED,
                cancelled_at=now_iso,
                completed_at=now_iso,
                block_kind=None,
                note="portability reconcile: founder cancelled confirmed zombie",
            )
            AuditLogger(org.db).log_zombie_cancelled(
                task_id, task.assigned_agent or "unknown",
            )
            _enqueue_parent_if_waiting(org.orchestrator, task_id)

        after = _task_state_summary(org.db.get_task(task_id))
        AuditLogger(org.db).log_portability_reconciled(
            task_id=task_id,
            actor="founder",
            request_hash=request_hash,
            evidence=body.evidence,
            disposition=body.disposition,
            before=before,
            after=after,
        )

    return {
        "task_id": task_id,
        "disposition": body.disposition,
        "request_hash": request_hash,
        "before": before,
        "after": after,
    }
