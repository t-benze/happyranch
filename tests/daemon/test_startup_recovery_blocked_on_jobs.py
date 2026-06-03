from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.database import Database
from runtime.models import BlockKind, JobInterpreter, JobRecord, JobStatus, TaskRecord, TaskStatus


def _make_orch(db: Database, slug: str = "org-a"):
    orch = MagicMock()
    orch._db = db
    orch._audit = AuditLogger(db)
    orch._queue = MagicMock()
    orch._slug = slug
    return orch


def _insert_task_blocked_on_job(db: Database, task_id: str, job_ids: list[str]) -> None:
    db.insert_task(TaskRecord(
        id=task_id, team="engineering", brief="t",
        status=TaskStatus.IN_PROGRESS, parent_task_id=None,
    ))
    db.update_task(
        task_id,
        status=TaskStatus.BLOCKED,
        block_kind=BlockKind.BLOCKED_ON_JOB,
        blocked_on_job_ids=json.dumps(job_ids),
    )


def _insert_job(db: Database, job_id: str, task_id: str, status: JobStatus) -> None:
    db.insert_job(JobRecord(
        id=job_id,
        task_id=task_id,
        agent_name="dev_agent",
        title="test job",
        rationale="need to run something",
        script_text="echo hi",
        interpreter=JobInterpreter.BASH,
        status=status,
        created_at=datetime.now(timezone.utc).isoformat(),
    ))


def test_startup_recovery_resumes_tasks_with_all_terminal_jobs():
    """After recovery scan, a BLOCKED+BLOCKED_ON_JOB task whose listed jobs
    are all terminal gets enqueued."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "t.db")
        _insert_task_blocked_on_job(db, "TASK-1", ["JOB-1"])
        _insert_job(db, "JOB-1", "TASK-1", JobStatus.FAILED)

        orch = _make_orch(db)

        from runtime.orchestrator.run_step import _maybe_resume_blocked_task
        for task_id in db.list_tasks_blocked_on_jobs():
            _maybe_resume_blocked_task(
                orch, task_id,
                trigger="startup_recovery", triggering_job_id=None,
            )

        orch._queue.enqueue.assert_called_once_with(
            "org-a", "TASK-1",
            metadata={"trigger": "startup_recovery", "triggering_job_id": None},
        )


def test_startup_recovery_resumes_tasks_with_completed_jobs():
    """A BLOCKED+BLOCKED_ON_JOB task whose job completed (not failed) also
    gets enqueued — both terminal statuses satisfy the predicate."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "t.db")
        _insert_task_blocked_on_job(db, "TASK-1", ["JOB-1"])
        _insert_job(db, "JOB-1", "TASK-1", JobStatus.COMPLETED)

        orch = _make_orch(db)

        from runtime.orchestrator.run_step import _maybe_resume_blocked_task
        for task_id in db.list_tasks_blocked_on_jobs():
            _maybe_resume_blocked_task(
                orch, task_id,
                trigger="startup_recovery", triggering_job_id=None,
            )

        orch._queue.enqueue.assert_called_once()


def test_startup_recovery_no_resume_for_running_jobs():
    """Tasks blocked on jobs that are STILL running stay blocked."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "t.db")
        _insert_task_blocked_on_job(db, "TASK-1", ["JOB-1"])
        _insert_job(db, "JOB-1", "TASK-1", JobStatus.RUNNING)

        orch = _make_orch(db)

        from runtime.orchestrator.run_step import _maybe_resume_blocked_task
        for task_id in db.list_tasks_blocked_on_jobs():
            _maybe_resume_blocked_task(
                orch, task_id,
                trigger="startup_recovery", triggering_job_id=None,
            )

        orch._queue.enqueue.assert_not_called()


def test_startup_recovery_no_resume_for_pending_jobs():
    """Tasks blocked on PENDING jobs also stay blocked."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "t.db")
        _insert_task_blocked_on_job(db, "TASK-1", ["JOB-1"])
        _insert_job(db, "JOB-1", "TASK-1", JobStatus.PENDING)

        orch = _make_orch(db)

        from runtime.orchestrator.run_step import _maybe_resume_blocked_task
        for task_id in db.list_tasks_blocked_on_jobs():
            _maybe_resume_blocked_task(
                orch, task_id,
                trigger="startup_recovery", triggering_job_id=None,
            )

        orch._queue.enqueue.assert_not_called()


def test_startup_recovery_multi_job_all_terminal():
    """Multiple jobs all terminal — task is enqueued."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "t.db")
        _insert_task_blocked_on_job(db, "TASK-1", ["JOB-1", "JOB-2"])
        _insert_job(db, "JOB-1", "TASK-1", JobStatus.COMPLETED)
        _insert_job(db, "JOB-2", "TASK-1", JobStatus.FAILED)

        orch = _make_orch(db)

        from runtime.orchestrator.run_step import _maybe_resume_blocked_task
        for task_id in db.list_tasks_blocked_on_jobs():
            _maybe_resume_blocked_task(
                orch, task_id,
                trigger="startup_recovery", triggering_job_id=None,
            )

        orch._queue.enqueue.assert_called_once()


def test_startup_recovery_multi_job_one_still_running():
    """Multiple jobs, one still running — task stays blocked."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "t.db")
        _insert_task_blocked_on_job(db, "TASK-1", ["JOB-1", "JOB-2"])
        _insert_job(db, "JOB-1", "TASK-1", JobStatus.COMPLETED)
        _insert_job(db, "JOB-2", "TASK-1", JobStatus.RUNNING)

        orch = _make_orch(db)

        from runtime.orchestrator.run_step import _maybe_resume_blocked_task
        for task_id in db.list_tasks_blocked_on_jobs():
            _maybe_resume_blocked_task(
                orch, task_id,
                trigger="startup_recovery", triggering_job_id=None,
            )

        orch._queue.enqueue.assert_not_called()
