from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.database import Database
from runtime.models import BlockKind, TaskRecord, TaskStatus
from runtime.orchestrator.run_step import run_step_impl, _blocked_jobs_resume_header_if_applicable


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
        # Prevent load_org_config from hanging via yaml.safe_load(MagicMock) in
        # Python 3.13. Setting exists()=False makes load_org_config return an
        # empty OrgConfig immediately (feishu_notifications=None → gate exits).
        orch._paths.org_config_path.exists.return_value = False
        yield db, orch


def _insert_blocked_on_jobs(db: Database, task_id: str, job_ids: list[str]):
    db.insert_task(TaskRecord(
        id=task_id, team="engineering", brief="t",
        status=TaskStatus.IN_PROGRESS, parent_task_id=None,
        assigned_agent="engineering_head",
    ))
    db.update_task(task_id, status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.BLOCKED_ON_JOB,
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
    assert after.status == TaskStatus.IN_PROGRESS
    assert after.block_kind == BlockKind.BLOCKED_ON_JOB


def test_step1_skips_when_blocked_on_job_ids_empty(db_and_orch):
    db, orch = db_and_orch
    _insert_blocked_on_jobs(db, "TASK-1", [])

    run_step_impl(orch, "TASK-1")

    after = db.get_task("TASK-1")
    assert after.status == TaskStatus.IN_PROGRESS  # unchanged


def test_step1_skips_when_blocked_on_job_ids_unparseable(db_and_orch):
    db, orch = db_and_orch
    db.insert_task(TaskRecord(
        id="TASK-1", team="engineering", brief="t",
        status=TaskStatus.IN_PROGRESS, parent_task_id=None,
    ))
    db.update_task("TASK-1", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.BLOCKED_ON_JOB,
                   blocked_on_job_ids="not-valid-json")

    run_step_impl(orch, "TASK-1")

    after = db.get_task("TASK-1")
    assert after.status == TaskStatus.IN_PROGRESS


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


from runtime.models import CompletionReport


def test_block_on_jobs_branch_transitions_in_place(db_and_orch):
    """report.status=blocked + non-empty waiting_on_job_ids → row goes to
    in_progress(blocked_on_job) (NOT _fail)."""
    db, orch = db_and_orch
    db.insert_task(TaskRecord(
        id="TASK-1", team="engineering", brief="t",
        status=TaskStatus.PENDING, parent_task_id=None,
        assigned_agent="engineering_worker",
    ))
    _insert_job(db, "JOB-1", "running")

    fake_result = MagicMock()
    fake_result.token_usage = None  # skip insert_session_token_usage
    fake_report = CompletionReport(
        task_id="TASK-1", agent="engineering_worker", status="blocked",
        confidence=0, output_summary="Waiting on migration",
        waiting_on_job_ids=["JOB-1"],
    )
    orch._run_agent.return_value = (fake_result, fake_report)
    run_step_impl(orch, "TASK-1")

    after = db.get_task("TASK-1")
    # Path B: a task waiting on jobs it submitted is in_progress(blocked_on_job),
    # NOT blocked — the waiting reason is the block_kind discriminant.
    assert after.status == TaskStatus.IN_PROGRESS
    assert after.block_kind == BlockKind.BLOCKED_ON_JOB
    assert after.blocked_on_job_ids == '["JOB-1"]'

    rows = db.get_audit_logs("TASK-1")
    blocked_audits = [r for r in rows if r["action"] == "task_blocked_on_jobs"]
    assert len(blocked_audits) == 1


def test_block_on_jobs_immediate_resume_when_jobs_already_terminal(db_and_orch):
    """Submit-time race: block submitted but all jobs already done → helper
    enqueues immediately."""
    db, orch = db_and_orch
    db.insert_task(TaskRecord(
        id="TASK-1", team="engineering", brief="t",
        status=TaskStatus.PENDING, parent_task_id=None,
        assigned_agent="engineering_worker",
    ))
    _insert_job(db, "JOB-1", "completed")

    fake_result = MagicMock()
    fake_result.token_usage = None  # skip insert_session_token_usage
    fake_report = CompletionReport(
        task_id="TASK-1", agent="engineering_worker", status="blocked",
        confidence=0, output_summary="Waiting on migration",
        waiting_on_job_ids=["JOB-1"],
    )
    orch._run_agent.return_value = (fake_result, fake_report)
    run_step_impl(orch, "TASK-1")

    # Helper should have enqueued via orch._queue.enqueue
    orch._queue.enqueue.assert_called_once_with(
        "org-a", "TASK-1",
        metadata={"trigger": "block_submit", "triggering_job_id": None},
    )


def test_block_on_jobs_with_missing_job_falls_back_to_fail(db_and_orch):
    """If a JOB id in waiting_on_job_ids doesn't exist (deleted between route
    and worker pickup), degrade to existing _fail path."""
    db, orch = db_and_orch
    db.insert_task(TaskRecord(
        id="TASK-1", team="engineering", brief="t",
        status=TaskStatus.PENDING, parent_task_id=None,
        assigned_agent="engineering_worker",
    ))
    # NOTE: no JOB-999 inserted

    fake_result = MagicMock()
    fake_result.token_usage = None  # skip insert_session_token_usage
    fake_report = CompletionReport(
        task_id="TASK-1", agent="engineering_worker", status="blocked",
        confidence=0, output_summary="Waiting",
        waiting_on_job_ids=["JOB-999"],
    )
    orch._run_agent.return_value = (fake_result, fake_report)
    run_step_impl(orch, "TASK-1")

    after = db.get_task("TASK-1")
    assert after.status == TaskStatus.FAILED  # _fail path
    assert "JOB-999 not found" in (after.note or "")


def test_existing_blocked_escalated_path_preserved(db_and_orch):
    """Blocked report with EMPTY waiting_on_job_ids → existing _fail path runs."""
    db, orch = db_and_orch
    db.insert_task(TaskRecord(
        id="TASK-1", team="engineering", brief="t",
        status=TaskStatus.PENDING, parent_task_id=None,
        assigned_agent="engineering_worker",
    ))

    fake_result = MagicMock()
    fake_result.token_usage = None  # skip insert_session_token_usage
    fake_report = CompletionReport(
        task_id="TASK-1", agent="engineering_worker", status="blocked",
        confidence=0, output_summary="self-escalated to founder",
        waiting_on_job_ids=[],  # empty — existing path
    )
    orch._run_agent.return_value = (fake_result, fake_report)
    run_step_impl(orch, "TASK-1")

    after = db.get_task("TASK-1")
    assert after.status == TaskStatus.FAILED  # _fail path
    assert "self-blocked" in (after.note or "")


def test_resume_header_rendered_after_audit_row(db_and_orch):
    """If a task_resumed_from_jobs audit row exists newer than the most
    recent orchestration_step row, header is rendered."""
    db, orch = db_and_orch
    db.insert_task(TaskRecord(
        id="TASK-1", team="engineering", brief="t",
        status=TaskStatus.IN_PROGRESS, parent_task_id=None,
    ))
    _insert_job(db, "JOB-1", "completed")
    orch._audit.log_task_resumed_from_jobs(
        task_id="TASK-1",
        blocking_job_ids=["JOB-1"],
        trigger="job_terminal",
        triggering_job_id="JOB-1",
        job_outcomes={"JOB-1": "completed"},
    )

    header = _blocked_jobs_resume_header_if_applicable(orch, "TASK-1")
    assert header is not None
    assert "BLOCKED-JOBS-RESULTS" in header
    assert "JOB-1" in header
    assert "completed" in header
    assert "happyranch jobs show JOB-1" in header


def test_resume_header_skipped_after_step_runs(db_and_orch):
    """Once an orchestration_step row exists newer than the audit row, the
    header stops rendering."""
    db, orch = db_and_orch
    db.insert_task(TaskRecord(
        id="TASK-1", team="engineering", brief="t",
        status=TaskStatus.IN_PROGRESS, parent_task_id=None,
    ))
    _insert_job(db, "JOB-1", "completed")
    orch._audit.log_task_resumed_from_jobs(
        task_id="TASK-1",
        blocking_job_ids=["JOB-1"],
        trigger="job_terminal",
        triggering_job_id="JOB-1",
        job_outcomes={"JOB-1": "completed"},
    )
    orch._audit.log_orchestration_step("TASK-1", 1, {"action": "done"})

    header = _blocked_jobs_resume_header_if_applicable(orch, "TASK-1")
    assert header is None


def test_resume_header_none_when_no_audit_row(db_and_orch):
    db, orch = db_and_orch
    db.insert_task(TaskRecord(
        id="TASK-1", team="engineering", brief="t",
        status=TaskStatus.IN_PROGRESS, parent_task_id=None,
    ))
    header = _blocked_jobs_resume_header_if_applicable(orch, "TASK-1")
    assert header is None
