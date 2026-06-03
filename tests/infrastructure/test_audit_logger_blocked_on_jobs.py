from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.database import Database


@pytest.fixture
def audit():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "t.db")
        yield AuditLogger(db), db


def test_log_task_blocked_on_jobs(audit):
    logger, db = audit
    logger.log_task_blocked_on_jobs(
        task_id="TASK-1", agent="engineering_worker",
        blocking_job_ids=["JOB-12", "JOB-13"],
        output_summary_excerpt="Waiting for migration verification",
    )
    rows = db.get_audit_logs("TASK-1")
    assert len(rows) == 1
    assert rows[0]["action"] == "task_blocked_on_jobs"
    payload = rows[0]["payload"]
    assert payload["blocking_job_ids"] == ["JOB-12", "JOB-13"]
    assert payload["agent"] == "engineering_worker"
    assert payload["output_summary_excerpt"] == "Waiting for migration verification"


def test_log_task_resumed_from_jobs(audit):
    logger, db = audit
    logger.log_task_resumed_from_jobs(
        task_id="TASK-1",
        blocking_job_ids=["JOB-12", "JOB-13"],
        trigger="job_terminal",
        triggering_job_id="JOB-13",
        job_outcomes={"JOB-12": "completed", "JOB-13": "failed"},
    )
    rows = db.get_audit_logs("TASK-1")
    assert len(rows) == 1
    assert rows[0]["action"] == "task_resumed_from_jobs"
    payload = rows[0]["payload"]
    assert payload["trigger"] == "job_terminal"
    assert payload["triggering_job_id"] == "JOB-13"
    assert payload["job_outcomes"] == {"JOB-12": "completed", "JOB-13": "failed"}


def test_log_task_resume_skipped_empty_job_list(audit):
    logger, db = audit
    logger.log_task_resume_skipped(
        task_id="TASK-1", reason="empty_job_list",
        blocked_on_job_ids_raw="[]",
    )
    rows = db.get_audit_logs("TASK-1")
    assert len(rows) == 1
    assert rows[0]["action"] == "task_resume_skipped"
    payload = rows[0]["payload"]
    assert payload["reason"] == "empty_job_list"
