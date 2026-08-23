"""Tests for the never-started pending-job reconciliation seam (THR-195).

The seam is a narrowly factored internal application lifecycle path: it goes
through the store's guarded pending→failed transition and the audit logger —
no ad-hoc SQL, no file deletion, no one-off bypass. It is NOT wired into any
periodic loop; it is invoked only with explicit (founder-authorized) authority
for records that pass the full non-live proof.

TASK-1435/JOB-190 protection: a job whose task is not terminal (e.g. an
in_progress task blocked on the job) must be refused by the seam.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from runtime.daemon.never_started_job_reconciliation import (
    ReconciliationError,
    reconcile_never_started_job,
)
from runtime.daemon.stale_pending_jobs import STALE_PENDING_JOB_MAX_AGE
from runtime.infrastructure.database import Database
from runtime.models import JobInterpreter, JobRecord, JobStatus, TaskRecord, TaskStatus


def _z(iso: datetime) -> str:
    return iso.isoformat().replace("+00:00", "Z")


def _now() -> datetime:
    return datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)


def _seed_task(db: Database, task_id: str, status: TaskStatus, **kw) -> None:
    db.insert_task(TaskRecord(
        id=task_id,
        assigned_agent="qa_engineer",
        team="engineering",
        brief="fixture",
        status=status,
        **kw,
    ))


def _seed_job(
    db: Database,
    *,
    job_id: str,
    task_id: str,
    created_at: str,
    status: JobStatus = JobStatus.PENDING,
    started_at: str | None = None,
    review_required: bool = False,
) -> None:
    db.insert_job(JobRecord(
        id=job_id,
        task_id=task_id,
        agent_name="qa_engineer",
        title=f"fixture {job_id}",
        rationale="",
        script_text="echo hi",
        interpreter=JobInterpreter.BASH,
        status=status,
        started_at=started_at,
        review_required=review_required,
        created_at=created_at,
    ))


def _audit_actions(db: Database, task_id: str) -> list[dict]:
    return db.get_audit_logs(task_id)


# ---------------------------------------------------------------------------
# Happy path: proved never-dispatched pending job on terminal task
# ---------------------------------------------------------------------------

def test_reconcile_happy_path_writes_audited_terminal_record(tmp_path: Path):
    db = Database(tmp_path / "happyranch.db")
    _seed_task(db, "TASK-861", TaskStatus.COMPLETED)
    _seed_job(
        db, job_id="JOB-155", task_id="TASK-861",
        created_at=_z(_now() - timedelta(days=61)),
    )
    after = reconcile_never_started_job(
        db, job_id="JOB-155",
        now=_now(), reason="abandoned_never_dispatched (THR-195 TASK-5501)",
    )
    assert after["status"] == "failed"
    assert after["reason"] == "abandoned_never_dispatched (THR-195 TASK-5501)"
    assert after["started_at"] is None  # honest: never started

    row = db.get_job("JOB-155")
    assert row is not None
    assert row.status == JobStatus.FAILED
    assert row.reason == "abandoned_never_dispatched (THR-195 TASK-5501)"
    assert row.started_at is None
    assert row.finished_at == _z(_now())

    # Durable audit evidence with before/after.
    audits = [a for a in _audit_actions(db, "TASK-861") if a["action"] == "job_reconciled_orphaned"]
    assert len(audits) == 1
    payload = audits[0]["payload"]
    assert payload["job_id"] == "JOB-155"
    assert payload["evidence"]["task_status"] == "completed"
    assert payload["evidence"]["executor_pid"] is None
    assert payload["evidence"]["current_session_id"] is None
    assert payload["before"]["status"] == "pending"
    assert payload["before"]["started_at"] is None
    assert payload["after"]["status"] == "failed"


# ---------------------------------------------------------------------------
# Guards — each predicate failure refuses the reconciliation with no change
# ---------------------------------------------------------------------------

def test_reconcile_refuses_non_pending(tmp_path: Path):
    db = Database(tmp_path / "happyranch.db")
    _seed_task(db, "TASK-001", TaskStatus.FAILED)
    _seed_job(
        db, job_id="JOB-001", task_id="TASK-001",
        created_at=_z(_now() - timedelta(days=30)),
        status=JobStatus.RUNNING,
        started_at=_z(_now() - timedelta(minutes=5)),
    )
    with pytest.raises(ReconciliationError, match="not pending"):
        reconcile_never_started_job(db, job_id="JOB-001", now=_now(), reason="x")
    assert db.get_job("JOB-001").status == JobStatus.RUNNING  # unchanged


def test_reconcile_refuses_started_pending(tmp_path: Path):
    db = Database(tmp_path / "happyranch.db")
    _seed_task(db, "TASK-001", TaskStatus.FAILED)
    _seed_job(
        db, job_id="JOB-001", task_id="TASK-001",
        created_at=_z(_now() - timedelta(days=30)),
        started_at=_z(_now() - timedelta(days=29)),
    )
    with pytest.raises(ReconciliationError, match="started"):
        reconcile_never_started_job(db, job_id="JOB-001", now=_now(), reason="x")
    row = db.get_job("JOB-001")
    assert row.status == JobStatus.PENDING and row.started_at is not None


def test_reconcile_refuses_recent_job(tmp_path: Path):
    db = Database(tmp_path / "happyranch.db")
    _seed_task(db, "TASK-001", TaskStatus.FAILED)
    _seed_job(
        db, job_id="JOB-001", task_id="TASK-001",
        created_at=_z(_now() - timedelta(hours=1)),
    )
    with pytest.raises(ReconciliationError, match="threshold"):
        reconcile_never_started_job(db, job_id="JOB-001", now=_now(), reason="x")
    assert db.get_job("JOB-001").status == JobStatus.PENDING


def test_reconcile_refuses_job_on_non_terminal_task_protects_t1435_shape(tmp_path: Path):
    """TASK-1435/JOB-190 protection: a pending job on an in_progress task
    (blocked_on_job) must NEVER be reconciled by the seam."""
    db = Database(tmp_path / "happyranch.db")
    _seed_task(
        db, "TASK-1435", TaskStatus.IN_PROGRESS,
        block_kind="blocked_on_job", blocked_on_job_ids='["JOB-190"]',
    )
    _seed_job(
        db, job_id="JOB-190", task_id="TASK-1435",
        created_at=_z(_now() - timedelta(days=53)),
        review_required=True,
    )
    with pytest.raises(ReconciliationError, match="terminal"):
        reconcile_never_started_job(db, job_id="JOB-190", now=_now(), reason="x")
    row = db.get_job("JOB-190")
    assert row.status == JobStatus.PENDING  # untouched
    task = db.get_task("TASK-1435")
    assert task.status == TaskStatus.IN_PROGRESS  # untouched


def test_reconcile_refuses_unknown_job(tmp_path: Path):
    db = Database(tmp_path / "happyranch.db")
    with pytest.raises(ReconciliationError, match="unknown"):
        reconcile_never_started_job(db, job_id="JOB-999", now=_now(), reason="x")


def test_reconcile_guard_against_concurrent_dispatch(tmp_path: Path):
    """The guarded UPDATE (pending AND started_at IS NULL) is the last line of
    defense: if a dispatch transitioned the row to running between the proof
    read and the transition, the transition refuses instead of clobbering."""
    db = Database(tmp_path / "happyranch.db")
    _seed_task(db, "TASK-001", TaskStatus.FAILED)
    _seed_job(
        db, job_id="JOB-001", task_id="TASK-001",
        created_at=_z(_now() - timedelta(days=30)),
    )
    # Simulate the race: a dispatch wins before our guarded transition.
    db.transition_job_to_running(
        "JOB-001", reviewer="founder", reviewed_at=_z(_now()),
        started_at=_z(_now()), cwd_resolved="/tmp", max_runtime_seconds=300,
        stdout_path="/tmp/j.out", stderr_path="/tmp/j.err",
    )
    with pytest.raises(ReconciliationError, match="not.*never|started|pending"):
        reconcile_never_started_job(db, job_id="JOB-001", now=_now(), reason="x")
    assert db.get_job("JOB-001").status == JobStatus.RUNNING  # not clobbered


# ---------------------------------------------------------------------------
# Regression fixture: the historical orphan mechanism is detectable
# ---------------------------------------------------------------------------

def test_historical_orphan_records_are_reconcilable_end_to_end(tmp_path: Path):
    """Each historical durable stage (auto-run cwd-miss orphan, abandoned
    review-gated orphan on a terminal task) reconciles through the seam and
    leaves the observer seeing zero stale rows afterwards."""
    db = Database(tmp_path / "happyranch.db")
    _seed_task(db, "TASK-516", TaskStatus.FAILED)
    for i in (2, 3, 4):
        _seed_job(
            db, job_id=f"JOB-00{i}", task_id="TASK-516",
            created_at=_z(_now() - timedelta(days=89)),
            review_required=True,
        )
    _seed_task(db, "TASK-861", TaskStatus.COMPLETED)
    _seed_job(
        db, job_id="JOB-155", task_id="TASK-861",
        created_at=_z(_now() - timedelta(days=61)),
    )

    from runtime.daemon.stale_pending_jobs import scan_org_stale_pending
    assert len(scan_org_stale_pending(db, now=_now())) == 4

    for job_id in ("JOB-002", "JOB-003", "JOB-004", "JOB-155"):
        reconcile_never_started_job(
            db, job_id=job_id,
            now=_now(), reason="abandoned_never_dispatched (THR-195 TASK-5501)",
        )
    assert scan_org_stale_pending(db, now=_now()) == []
    for job_id in ("JOB-002", "JOB-003", "JOB-004", "JOB-155"):
        assert db.get_job(job_id).status == JobStatus.FAILED
