"""Founder-authorized reconciliation seam for abandoned never-started pending
jobs (THR-195 / TASK-5499).

This is the mutation half of the repair, deliberately separated from the
read-only observer (``runtime.daemon.stale_pending_jobs``). It is a narrowly
factored internal application lifecycle seam: every transition goes through
the store's guarded ``pending AND started_at IS NULL`` UPDATE and is recorded
durably by the audit logger — no ad-hoc SQL, no file deletion, no one-off
bypass. It is NOT wired into any periodic loop or daemon startup path; it is
invoked only with explicit (founder-authorized) authority, once, for records
that pass the full non-live proof below.

Liveness / ownership proof (required before ANY reconciliation):
1. The row is ``status='pending'`` — not running/completed/failed/rejected.
2. ``started_at IS NULL`` — ``transition_job_to_running`` is the only writer
   of ``started_at`` and the only path to a spawned subprocess; a never-started
   row cannot hold an in-flight process (``jobs_runner._INFLIGHT`` membership
   requires the running transition first), so no live process can own it.
3. The row is older than the justified threshold — far beyond the synchronous
   auto-run dispatch window.
4. The owning task is TERMINAL — the founder rule from THR-195: a terminal
   task must not have live pending jobs. A non-terminal task (e.g.
   TASK-1435/JOB-190 blocked_on_job) is categorically refused: its blocker is
   a founder-review decision that remains recoverable under the existing
   lifecycle, not a reconciliation candidate.
5. The owning task has no ``executor_pid`` (no live executor process) and no
   ``current_session_id`` (no active session binding).

Queue membership trace: there is no job queue that dispatches ``pending``
rows (verified: the only consumers of pending rows are read surfaces); the DB
row itself is the queue membership record, and it says never-started.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from runtime.daemon.stale_pending_jobs import (
    STALE_PENDING_JOB_MAX_AGE,
    stale_pending_cutoff_iso,
)
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.models import JobStatus
from runtime.orchestrator.run_step import TERMINAL_STATES

if TYPE_CHECKING:
    from runtime.infrastructure.database import Database


class ReconciliationError(Exception):
    """A candidate failed the non-live proof; nothing was changed."""


def _now_iso(now: datetime) -> str:
    return now.isoformat().replace("+00:00", "Z")


def proof_never_dispatched(
    db: "Database",
    job_id: str,
    *,
    now: datetime,
    max_age: timedelta = STALE_PENDING_JOB_MAX_AGE,
) -> dict:
    """Prove a job was never dispatched and is not live-owned.

    Returns an evidence dict on success; raises ``ReconciliationError`` with
    the failing predicate otherwise. Pure read — no mutation.
    """
    job = db.get_job(job_id)
    if job is None:
        raise ReconciliationError(f"unknown job {job_id}")
    if job.status != JobStatus.PENDING:
        raise ReconciliationError(
            f"job {job_id} is {job.status.value!r}, not pending"
        )
    if job.started_at is not None:
        raise ReconciliationError(
            f"job {job_id} has started_at set (was dispatched); not reconcilable"
        )
    if job.created_at >= stale_pending_cutoff_iso(now, max_age=max_age):
        raise ReconciliationError(
            f"job {job_id} created {job.created_at} is below the "
            f"{max_age.days}-day observation threshold"
        )
    task = db.get_task(job.task_id)
    if task is None:
        raise ReconciliationError(f"owning task {job.task_id} not found")
    if task.status not in TERMINAL_STATES:
        raise ReconciliationError(
            f"owning task {job.task_id} is {task.status.value!r} (not terminal); "
            "its blocker remains a live lifecycle decision — refusing (TASK-1435 shape)"
        )
    if task.executor_pid is not None:
        raise ReconciliationError(
            f"owning task {job.task_id} has live executor pid {task.executor_pid}"
        )
    if task.current_session_id is not None:
        raise ReconciliationError(
            f"owning task {job.task_id} has active session {task.current_session_id}"
        )
    return {
        "job_id": job.id,
        "task_id": job.task_id,
        "job_status": job.status.value,
        "started_at": job.started_at,
        "created_at": job.created_at,
        "task_status": task.status.value,
        "executor_pid": task.executor_pid,
        "current_session_id": task.current_session_id,
        "age_threshold_days": max_age.days,
    }


def reconcile_never_started_job(
    db: "Database",
    job_id: str,
    *,
    now: datetime | None = None,
    reason: str,
    max_age: timedelta = STALE_PENDING_JOB_MAX_AGE,
) -> dict:
    """Reconcile ONE proved never-dispatched pending job to terminal ``failed``.

    Full proof first (no mutation on failure), then the store's guarded
    transition, then a durable ``job_reconciled_orphaned`` audit row carrying
    before/after lifecycle state. The guarded transition and the audit row are
    ONE transaction (``transition_never_started_job_to_failed`` leaves the
    UPDATE uncommitted; the audit insert is ``insert_audit_log_uncommitted``;
    ``commit()`` makes both durable together) — an audit/commit failure rolls
    everything back, so a terminalized job can never survive without its
    durable non-live-proof recovery record. Returns the after record.
    """
    now = now or datetime.now(timezone.utc)
    evidence = proof_never_dispatched(db, job_id, now=now, max_age=max_age)

    before = {
        "status": evidence["job_status"],
        "started_at": evidence["started_at"],
        "created_at": evidence["created_at"],
    }
    try:
        db.transition_never_started_job_to_failed(
            job_id, now_iso=_now_iso(now), reason=reason,
        )
        after_row = db.get_job(job_id)
        assert after_row is not None
        after = {
            "status": after_row.status.value,
            "started_at": after_row.started_at,
            "finished_at": after_row.finished_at,
            "reason": after_row.reason,
        }
        AuditLogger(db).log_job_reconciled_orphaned(
            task_id=evidence["task_id"],
            job_id=job_id,
            reason=reason,
            evidence=evidence,
            before=before,
            after=after,
        )
        db.commit()
    except BaseException:
        # Atomicity: the transition UPDATE and the audit INSERT share this
        # transaction — discard BOTH on any failure so no terminalized job
        # and no partial audit residue can survive.
        db.rollback()
        raise
    return after
