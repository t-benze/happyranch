from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database
from src.models import BlockKind, TaskRecord, TaskStatus
from src.orchestrator.run_step import _maybe_resume_blocked_task


def _make_orch(db: Database):
    """Minimal orchestrator stub for helper unit-tests."""
    orch = MagicMock()
    orch._db = db
    orch._audit = AuditLogger(db)  # real one so we can assert on the audit log
    orch._queue = MagicMock()
    orch._slug = "org-a"
    return orch


def _insert_task_blocked_on_jobs(db: Database, task_id: str, job_ids: list[str]):
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


def _insert_job(db: Database, job_id: str, task_id: str, status: str):
    db._conn.execute(
        "INSERT INTO jobs (id, task_id, agent_name, title, script_text, "
        "interpreter, status, created_at) VALUES (?, ?, 'a', 't', 's', 'bash', ?, "
        "'2026-05-28T00:00:00')",
        (job_id, task_id, status),
    )
    db._conn.commit()


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        yield Database(Path(tmp) / "t.db")


def test_resumes_when_single_job_completed(db):
    _insert_task_blocked_on_jobs(db, "TASK-1", ["JOB-1"])
    _insert_job(db, "JOB-1", "TASK-1", "completed")
    orch = _make_orch(db)
    result = _maybe_resume_blocked_task(
        orch, "TASK-1", trigger="job_terminal", triggering_job_id="JOB-1",
    )
    assert result is True
    orch._queue.enqueue.assert_called_once_with(
        "org-a", "TASK-1",
        metadata={"trigger": "job_terminal", "triggering_job_id": "JOB-1"},
    )


def test_resumes_when_all_terminal_mixed_states(db):
    _insert_task_blocked_on_jobs(db, "TASK-1", ["JOB-1", "JOB-2", "JOB-3"])
    _insert_job(db, "JOB-1", "TASK-1", "completed")
    _insert_job(db, "JOB-2", "TASK-1", "failed")
    _insert_job(db, "JOB-3", "TASK-1", "rejected")
    orch = _make_orch(db)
    assert _maybe_resume_blocked_task(
        orch, "TASK-1", trigger="job_terminal", triggering_job_id="JOB-3",
    ) is True
    orch._queue.enqueue.assert_called_once()


def test_does_not_resume_when_one_still_running(db):
    _insert_task_blocked_on_jobs(db, "TASK-1", ["JOB-1", "JOB-2"])
    _insert_job(db, "JOB-1", "TASK-1", "completed")
    _insert_job(db, "JOB-2", "TASK-1", "running")
    orch = _make_orch(db)
    assert _maybe_resume_blocked_task(
        orch, "TASK-1", trigger="job_terminal", triggering_job_id="JOB-1",
    ) is False
    orch._queue.enqueue.assert_not_called()


def test_no_audit_when_task_not_blocked(db):
    db.insert_task(TaskRecord(
        id="TASK-1", team="engineering", brief="t",
        status=TaskStatus.IN_PROGRESS, parent_task_id=None,
    ))
    orch = _make_orch(db)
    assert _maybe_resume_blocked_task(
        orch, "TASK-1", trigger="job_terminal", triggering_job_id="JOB-1",
    ) is False
    orch._queue.enqueue.assert_not_called()
    # No audit row should have been written
    rows = db.get_audit_logs("TASK-1")
    assert len([r for r in rows if r["action"] == "task_resume_skipped"]) == 0


def test_no_audit_when_block_kind_is_escalated(db):
    db.insert_task(TaskRecord(
        id="TASK-1", team="engineering", brief="t",
        status=TaskStatus.IN_PROGRESS, parent_task_id=None,
    ))
    db.update_task("TASK-1", status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.ESCALATED)
    orch = _make_orch(db)
    assert _maybe_resume_blocked_task(
        orch, "TASK-1", trigger="job_terminal", triggering_job_id="JOB-1",
    ) is False
    orch._queue.enqueue.assert_not_called()
    rows = db.get_audit_logs("TASK-1")
    assert len([r for r in rows if r["action"] == "task_resume_skipped"]) == 0


def test_audits_skip_when_empty_job_list(db):
    db.insert_task(TaskRecord(
        id="TASK-1", team="engineering", brief="t",
        status=TaskStatus.IN_PROGRESS, parent_task_id=None,
    ))
    db.update_task("TASK-1", status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.BLOCKED_ON_JOB,
                   blocked_on_job_ids="[]")
    orch = _make_orch(db)
    assert _maybe_resume_blocked_task(
        orch, "TASK-1", trigger="job_terminal", triggering_job_id="JOB-1",
    ) is False
    orch._queue.enqueue.assert_not_called()
    rows = db.get_audit_logs("TASK-1")
    skip_rows = [r for r in rows if r["action"] == "task_resume_skipped"]
    assert len(skip_rows) == 1


def test_helper_does_not_mutate_task_status(db):
    """Helper is read-only — never writes to tasks.status / block_kind."""
    _insert_task_blocked_on_jobs(db, "TASK-1", ["JOB-1"])
    _insert_job(db, "JOB-1", "TASK-1", "completed")
    orch = _make_orch(db)
    _maybe_resume_blocked_task(
        orch, "TASK-1", trigger="job_terminal", triggering_job_id="JOB-1",
    )
    after = db.get_task("TASK-1")
    assert after.status == TaskStatus.BLOCKED  # NOT in_progress
    assert after.block_kind == BlockKind.BLOCKED_ON_JOB
