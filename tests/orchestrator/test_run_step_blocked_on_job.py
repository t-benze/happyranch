from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database
from src.models import BlockKind, TaskRecord, TaskStatus
from src.orchestrator.run_step import run_step_impl


@pytest.fixture
def db_and_orch():
    """Minimal orchestrator + DB stub that runs run_step_impl through step 1."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "t.db")
        audit = AuditLogger(db)
        orch = MagicMock()
        orch._db = db
        orch._audit = audit
        orch._settings = MagicMock(max_orchestration_steps=50)
        orch._queue = MagicMock()
        orch._slug = "org-a"
        orch.teams = MagicMock(is_team_manager=MagicMock(return_value=False))
        yield db, orch


def _insert_blocked_on_jobs(db: Database, task_id: str, job_ids: list[str]):
    db.insert_task(TaskRecord(
        id=task_id, team="engineering", brief="t",
        status=TaskStatus.IN_PROGRESS, parent_task_id=None,
        assigned_agent="engineering_head",
    ))
    db.update_task(task_id, status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.BLOCKED_ON_JOB,
                   blocked_on_job_ids=json.dumps(job_ids))


def _insert_job(db: Database, jid: str, status: str, task_id: str = "TASK-1"):
    db._conn.execute(
        "INSERT INTO jobs (id, task_id, agent_name, title, script_text, "
        "interpreter, status, created_at) VALUES (?, ?, 'a', 't', 's', 'bash', ?, "
        "'2026-05-28T00:00:00')", (jid, task_id, status))
    db._conn.commit()


def test_step1_admits_blocked_on_job_when_all_terminal(db_and_orch):
    db, orch = db_and_orch
    _insert_blocked_on_jobs(db, "TASK-1", ["JOB-1"])
    _insert_job(db, "JOB-1", "completed")

    # Configure orch._run_agent (already a MagicMock attr) to raise so we can
    # short-circuit after step 3 CAS. run_step_impl catches the exception via
    # the "agent invocation failed" handler and marks the task FAILED via
    # _fail() — so we assert the status is FAILED (not BLOCKED), proving step 1
    # admitted the task and the CAS succeeded.
    orch._run_agent.side_effect = RuntimeError("don't actually run the agent here")
    run_step_impl(orch, "TASK-1")

    after = db.get_task("TASK-1")
    assert after.status == TaskStatus.FAILED


def test_step1_skips_when_blocking_job_still_running(db_and_orch):
    db, orch = db_and_orch
    _insert_blocked_on_jobs(db, "TASK-1", ["JOB-1"])
    _insert_job(db, "JOB-1", "running")

    run_step_impl(orch, "TASK-1")  # Returns silently without invoking agent

    after = db.get_task("TASK-1")
    assert after.status == TaskStatus.BLOCKED
    assert after.block_kind == BlockKind.BLOCKED_ON_JOB


def test_step1_skips_when_blocked_on_job_ids_empty(db_and_orch):
    db, orch = db_and_orch
    _insert_blocked_on_jobs(db, "TASK-1", [])

    run_step_impl(orch, "TASK-1")

    after = db.get_task("TASK-1")
    assert after.status == TaskStatus.BLOCKED  # unchanged


def test_step1_skips_when_blocked_on_job_ids_unparseable(db_and_orch):
    db, orch = db_and_orch
    db.insert_task(TaskRecord(
        id="TASK-1", team="engineering", brief="t",
        status=TaskStatus.IN_PROGRESS, parent_task_id=None,
    ))
    db.update_task("TASK-1", status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.BLOCKED_ON_JOB,
                   blocked_on_job_ids="not-valid-json")

    run_step_impl(orch, "TASK-1")

    after = db.get_task("TASK-1")
    assert after.status == TaskStatus.BLOCKED


def test_cas_win_writes_task_resumed_from_jobs_audit_row(db_and_orch):
    """After step-1 admits and step-3 CAS wins, an audit row exists carrying
    the trigger/triggering_job_id from the metadata parameter."""
    db, orch = db_and_orch
    _insert_blocked_on_jobs(db, "TASK-1", ["JOB-1", "JOB-2"])
    _insert_job(db, "JOB-1", "completed")
    _insert_job(db, "JOB-2", "failed")
    db.update_task("TASK-1", assigned_agent="engineering_worker")

    # Mock the agent invocation site so step-4 short-circuits (task goes FAILED
    # via the exception handler — audit hook fires before _run_agent is called).
    orch._run_agent.side_effect = RuntimeError("stop here")

    run_step_impl(orch, "TASK-1", metadata={
        "trigger": "job_terminal", "triggering_job_id": "JOB-2",
    })

    rows = db.get_audit_logs("TASK-1")
    resumed = [r for r in rows if r["action"] == "task_resumed_from_jobs"]
    assert len(resumed) == 1
    payload = resumed[0]["payload"]
    assert payload["trigger"] == "job_terminal"
    assert payload["triggering_job_id"] == "JOB-2"
    assert payload["blocking_job_ids"] == ["JOB-1", "JOB-2"]
    assert payload["job_outcomes"] == {"JOB-1": "completed", "JOB-2": "failed"}


def test_cas_win_writes_audit_with_unknown_trigger_when_metadata_missing(db_and_orch):
    """If no metadata was attached (manual revisit re-entry, defensive case),
    audit row still fires with trigger='unknown'."""
    db, orch = db_and_orch
    _insert_blocked_on_jobs(db, "TASK-1", ["JOB-1"])
    _insert_job(db, "JOB-1", "completed")
    db.update_task("TASK-1", assigned_agent="engineering_worker")

    orch._run_agent.side_effect = RuntimeError("stop here")

    run_step_impl(orch, "TASK-1", metadata=None)

    rows = db.get_audit_logs("TASK-1")
    resumed = [r for r in rows if r["action"] == "task_resumed_from_jobs"]
    assert len(resumed) == 1
    payload = resumed[0]["payload"]
    assert payload["trigger"] == "unknown"
    assert payload["triggering_job_id"] is None


def test_no_audit_when_entry_state_is_pending(db_and_orch):
    """When run_step_impl runs against a PENDING task (not resumed), no
    task_resumed_from_jobs audit row should fire — only the new-resume path
    triggers this audit."""
    db, orch = db_and_orch
    db.insert_task(TaskRecord(
        id="TASK-1", team="engineering", brief="t",
        status=TaskStatus.PENDING, parent_task_id=None,
        assigned_agent="engineering_worker",
    ))

    orch._run_agent.side_effect = RuntimeError("stop here")

    run_step_impl(orch, "TASK-1", metadata=None)

    rows = db.get_audit_logs("TASK-1")
    resumed = [r for r in rows if r["action"] == "task_resumed_from_jobs"]
    assert len(resumed) == 0
