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
    ZombieCandidate,
    compute_eligibility,
    is_true_zombie,
)
from runtime.portability.roots import classify_root_entries

router = APIRouter(dependencies=[require_token()])

_ACTIVE_JOB_STATUSES = {"pending", "running"}
_ACTIVE_DREAM_STATUSES = {"pending", "running"}
_ACTIVE_WORK_HOUR_STATUSES = {"pending", "running"}
_ARMED_SCHEDULE_STATUSES = {"armed"}
_FIRING_SCHEDULE_STATUSES = {"firing"}


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
    # Dedicated uncapped status-filtered id query — never the capped,
    # newest-first presentation list (list_jobs_db), which could hide an old
    # active row behind newer terminal rows.
    return org.db.list_job_ids_by_status(_ACTIVE_JOB_STATUSES)


def _active_dream_ids(org) -> list[str]:
    return org.db.list_dream_ids_by_status(_ACTIVE_DREAM_STATUSES)


def _active_work_hour_ids(org) -> list[str]:
    return org.db.work_hours.list_ids_by_status(_ACTIVE_WORK_HOUR_STATUSES)


def _armed_schedule_ids(org) -> list[str]:
    return org.db.schedules.list_ids_by_status(_ARMED_SCHEDULE_STATUSES)


def _firing_schedule_ids(org) -> list[str]:
    return org.db.schedules.list_ids_by_status(_FIRING_SCHEDULE_STATUSES)


def _active_schedule_ids(org) -> list[str]:
    return sorted(set(_armed_schedule_ids(org)) | set(_firing_schedule_ids(org)))


def _build_remedies(
    slug: str,
    *,
    tasks: list[str],
    jobs: list[str],
    dreams: list[str],
    work_hours: list[str],
    armed_schedules: list[str],
    firing_schedules: list[str],
    active_session_count: int,
    queued_for_org: int,
    pending_invocation_count: int,
    zombies: list[ZombieCandidate],
) -> list[dict]:
    """Report the exact actionable remedy for each blocker using only the
    existing founder controls (no relocation-specific disarm command, no
    export fence). For a state with no existing control (a firing schedule,
    live sessions/queue/invocations/dreams/work-hours), report the correct
    non-mutating wait/resolve condition instead."""
    remedies: list[dict] = []

    for sid in sorted(armed_schedules):
        remedies.append({
            "kind": "schedule",
            "target": sid,
            "status": "armed",
            "remedy": (
                f"happyranch todos pause --org {slug} {sid} "
                f"(or: happyranch todos cancel --org {slug} {sid})"
            ),
        })
    for sid in sorted(firing_schedules):
        remedies.append({
            "kind": "schedule",
            "target": sid,
            "status": "firing",
            "remedy": (
                f"{sid} is firing and cannot be paused or cancelled under the "
                f"existing schedule state machine; wait for it to reach a "
                f"terminal state, then re-run the preflight"
            ),
        })

    for tid in tasks:
        remedies.append({
            "kind": "task",
            "target": tid,
            "status": None,
            "remedy": f"happyranch cancel {tid} --org {slug}",
        })

    for jid in jobs:
        remedies.append({
            "kind": "job",
            "target": jid,
            "status": None,
            "remedy": f"happyranch jobs stop {jid} --org {slug}",
        })

    live_surfaces: list[str] = []
    if active_session_count:
        live_surfaces.append(f"{active_session_count} active session(s)")
    if queued_for_org:
        live_surfaces.append("a queued task")
    if pending_invocation_count:
        live_surfaces.append(f"{pending_invocation_count} pending thread invocation(s)")
    if dreams:
        live_surfaces.append(f"{len(dreams)} active dream(s)")
    if work_hours:
        live_surfaces.append(f"{len(work_hours)} active work-hour(s)")
    if live_surfaces:
        remedies.append({
            "kind": "live_work",
            "target": None,
            "status": None,
            "remedy": (
                "no founder cancel control exists for: "
                + "; ".join(live_surfaces)
                + ". Wait for these to complete, then re-run the preflight"
            ),
        })

    for z in zombies:
        remedies.append({
            "kind": "zombie",
            "target": z.task_id,
            "status": None,
            "remedy": (
                f"happyranch orgs reconcile-portability {slug} "
                f"--from-file <absolute-json-path>"
            ),
        })

    return remedies


@router.get("/portability-preflight")
def portability_preflight(slug: str, org: OrgDep, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    inventory = classify_root_entries(org.root)
    now = datetime.now(timezone.utc)
    armed_schedules = _armed_schedule_ids(org)
    firing_schedules = _firing_schedule_ids(org)
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
        "remedies": _build_remedies(
            slug,
            tasks=eligibility.tasks,
            jobs=eligibility.active_jobs,
            dreams=eligibility.active_dreams,
            work_hours=eligibility.active_work_hours,
            armed_schedules=armed_schedules,
            firing_schedules=firing_schedules,
            active_session_count=eligibility.active_session_count,
            queued_for_org=eligibility.queued_for_org,
            pending_invocation_count=eligibility.pending_invocation_count,
            zombies=eligibility.possible_zombies,
        ),
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
