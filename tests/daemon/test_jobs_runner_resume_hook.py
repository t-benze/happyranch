"""Unit tests for fire_resume_check_for_job (Task 15: Caller A)."""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from runtime.daemon.jobs_runner import (
    attach_jobs_resume_main_loop,
    fire_resume_check_for_job,
)
from runtime.infrastructure.database import Database
from runtime.models import (
    BlockKind,
    JobInterpreter,
    JobRecord,
    JobStatus,
    TaskRecord,
    TaskStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(tmp_path: Path) -> Database:
    return Database(tmp_path / "t.db")


def _insert_task_blocked_on_job(db: Database, task_id: str, job_id: str) -> None:
    db.insert_task(TaskRecord(
        id=task_id,
        team="engineering",
        brief="test",
        status=TaskStatus.IN_PROGRESS,
        parent_task_id=None,
    ))
    db.update_task(
        task_id,
        status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.BLOCKED_ON_JOB,
        blocked_on_job_ids=json.dumps([job_id]),
    )


def _insert_job_terminal(db: Database, job_id: str, task_id: str, status: str = "completed") -> None:
    db.insert_job(JobRecord(
        id=job_id,
        task_id=task_id,
        agent_name="dev",
        title="t",
        rationale="",
        script_text="echo hi",
        interpreter=JobInterpreter.BASH,
        cwd_hint=None,
        status=JobStatus.PENDING,
        created_at="2026-05-28T00:00:00Z",
    ))
    db._conn.execute(
        "UPDATE jobs SET status = ? WHERE id = ?",
        (status, job_id),
    )
    db._conn.commit()


def _make_org(db: Database) -> SimpleNamespace:
    """Minimal org-like object with an orchestrator stub."""
    queue_mock = MagicMock()
    orch = MagicMock()
    orch._db = db
    orch._slug = "acme"
    orch._queue = queue_mock
    orch._audit = MagicMock()
    return SimpleNamespace(orchestrator=orch)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_fire_resume_silent_when_no_orchestrator(tmp_path):
    """fire_resume_check_for_job is a no-op when org.orchestrator is None."""
    org = SimpleNamespace(orchestrator=None)
    # Should not raise
    fire_resume_check_for_job(org, "JOB-1")


def test_fire_resume_silent_when_not_wired():
    """If attach_jobs_resume_main_loop hasn't run, module state is None —
    fire_resume_check_for_job still works because it uses the org arg directly,
    not the module state. Verify reset doesn't break anything.
    """
    import runtime.daemon.jobs_runner as jr
    jr._RESUME_MAIN_LOOP = None
    jr._ORCH_RESOLVER = None
    org = SimpleNamespace(orchestrator=None)
    fire_resume_check_for_job(org, "JOB-NOOP")  # must not raise


def test_fire_resume_enqueues_blocked_task(tmp_path):
    """fire_resume_check_for_job finds the task blocked on the job and calls
    _maybe_resume_blocked_task, which enqueues it when all jobs are terminal.
    """
    db = _make_db(tmp_path)
    _insert_task_blocked_on_job(db, "TASK-1", "JOB-12")
    _insert_job_terminal(db, "JOB-12", "TASK-1", status="completed")

    org = _make_org(db)

    fire_resume_check_for_job(org, "JOB-12")

    org.orchestrator._queue.enqueue.assert_called_once_with(
        "acme", "TASK-1",
        metadata={"trigger": "job_terminal", "triggering_job_id": "JOB-12"},
    )


def test_fire_resume_skips_non_blocked_tasks(tmp_path):
    """Tasks not in BLOCKED_ON_JOB state are silently skipped."""
    db = _make_db(tmp_path)
    # Insert a task that is COMPLETED — should not be resumed.
    db.insert_task(TaskRecord(
        id="TASK-9",
        team="eng",
        brief="done",
        status=TaskStatus.COMPLETED,
        parent_task_id=None,
    ))
    _insert_job_terminal(db, "JOB-5", "TASK-9", status="completed")

    org = _make_org(db)
    fire_resume_check_for_job(org, "JOB-5")

    org.orchestrator._queue.enqueue.assert_not_called()


def test_fire_resume_skips_task_when_sibling_job_still_running(tmp_path):
    """If a task is blocked on [JOB-1, JOB-2] and only JOB-1 is terminal,
    the task should NOT be enqueued yet.
    """
    db = _make_db(tmp_path)
    db.insert_task(TaskRecord(
        id="TASK-2", team="eng", brief="t",
        status=TaskStatus.IN_PROGRESS, parent_task_id=None,
    ))
    db.update_task(
        "TASK-2",
        status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.BLOCKED_ON_JOB,
        blocked_on_job_ids=json.dumps(["JOB-1", "JOB-2"]),
    )
    # JOB-1 terminal, JOB-2 still pending.
    _insert_job_terminal(db, "JOB-1", "TASK-2", status="completed")
    db.insert_job(JobRecord(
        id="JOB-2", task_id="TASK-2", agent_name="dev",
        title="t2", rationale="", script_text="s",
        interpreter=JobInterpreter.BASH, cwd_hint=None,
        status=JobStatus.PENDING, created_at="2026-05-28T00:00:00Z",
    ))

    org = _make_org(db)
    fire_resume_check_for_job(org, "JOB-1")

    org.orchestrator._queue.enqueue.assert_not_called()


def test_fire_resume_like_anchor_does_not_match_prefix(tmp_path):
    """LIKE pattern '%"JOB-1"%' must NOT match JOB-12.
    The double-quote anchors ensure exact substring matching.
    """
    db = _make_db(tmp_path)
    # TASK-3 is blocked on JOB-12 (not JOB-1).
    _insert_task_blocked_on_job(db, "TASK-3", "JOB-12")
    _insert_job_terminal(db, "JOB-12", "TASK-3", status="completed")

    org = _make_org(db)
    # Fire for JOB-1 — should NOT match JOB-12 due to quote anchoring.
    fire_resume_check_for_job(org, "JOB-1")

    org.orchestrator._queue.enqueue.assert_not_called()


def test_attach_jobs_resume_main_loop_stores_state():
    """attach_jobs_resume_main_loop sets the module-level variables."""
    import runtime.daemon.jobs_runner as jr

    loop = MagicMock(spec=asyncio.AbstractEventLoop)
    resolver = MagicMock()

    attach_jobs_resume_main_loop(loop, resolver)

    assert jr._RESUME_MAIN_LOOP is loop
    assert jr._ORCH_RESOLVER is resolver

    # Clean up so later tests start fresh.
    jr._RESUME_MAIN_LOOP = None
    jr._ORCH_RESOLVER = None


def test_fire_resume_rejected_job_triggers_resume(tmp_path):
    """A REJECTED job status should also trigger resume for blocked tasks."""
    db = _make_db(tmp_path)
    _insert_task_blocked_on_job(db, "TASK-5", "JOB-99")
    _insert_job_terminal(db, "JOB-99", "TASK-5", status="rejected")

    org = _make_org(db)
    fire_resume_check_for_job(org, "JOB-99")

    org.orchestrator._queue.enqueue.assert_called_once_with(
        "acme", "TASK-5",
        metadata={"trigger": "job_terminal", "triggering_job_id": "JOB-99"},
    )
