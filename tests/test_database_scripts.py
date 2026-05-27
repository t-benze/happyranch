"""Schema + CRUD tests for jobs (spec §3.1)."""
from __future__ import annotations

import pytest

from src.infrastructure.database import Database


def test_script_requests_table_exists(db: Database):
    cur = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'"
    )
    assert cur.fetchone() is not None


def test_script_requests_columns(db: Database):
    cur = db._conn.execute("PRAGMA table_info(jobs)")
    names = {row["name"] for row in cur.fetchall()}
    expected = {
        "id", "task_id", "agent_name", "title", "rationale", "script_text",
        "interpreter", "cwd_hint", "status", "exit_code",
        "stdout_head", "stderr_head", "stdout_path", "stderr_path",
        "duration_ms", "started_at", "finished_at",
        "reviewed_at", "reviewed_by", "reject_reason",
        "cwd_resolved", "created_at",
    }
    assert expected.issubset(names), f"missing: {expected - names}"


def test_script_requests_indexes(db: Database):
    cur = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='jobs'"
    )
    names = {row["name"] for row in cur.fetchall()}
    assert "jobs_task_id_idx" in names
    assert "jobs_status_idx" in names


def test_next_job_id_first(db: Database):
    assert db.next_job_id() == "JOB-001"


def test_next_job_id_monotonic(db: Database):
    # Manually insert a row with JOB-005 to verify the allocator picks JOB-006.
    db._conn.execute(
        "INSERT INTO jobs (id, task_id, agent_name, title, rationale, "
        "script_text, interpreter, status, created_at) "
        "VALUES ('JOB-005', 'TASK-001', 'a', 't', 'r', 's', 'bash', 'pending', '2026-05-23T00:00:00Z')"
    )
    db._conn.commit()
    assert db.next_job_id() == "JOB-006"


from src.models import JobRecord, JobStatus, JobInterpreter


def _make_record(id_: str = "JOB-001") -> JobRecord:
    return JobRecord(
        id=id_,
        task_id="TASK-001",
        agent_name="engineering_head",
        title="Close PR #247",
        rationale="needs founder gh scope",
        script_text="gh pr close 247",
        interpreter=JobInterpreter.BASH,
        cwd_hint="repos/web-app",
        created_at="2026-05-23T10:00:00Z",
    )


def test_insert_and_get_job(db: Database):
    rec = _make_record()
    db.insert_job(rec)
    fetched = db.get_job("JOB-001")
    assert fetched is not None
    assert fetched.id == "JOB-001"
    assert fetched.task_id == "TASK-001"
    assert fetched.agent_name == "engineering_head"
    assert fetched.interpreter == JobInterpreter.BASH
    assert fetched.status == JobStatus.PENDING
    assert fetched.timeout_seconds == 300
    assert fetched.cwd_hint == "repos/web-app"


def test_get_job_missing(db: Database):
    assert db.get_job("JOB-999") is None


def test_list_script_requests_all(db: Database):
    for i in range(1, 4):
        rec = _make_record(f"JOB-{i:03d}")
        db.insert_job(rec)
    results = db.list_jobs_db()
    assert len(results) == 3
    # Most recent first (created_at DESC, ties broken by id DESC).
    assert results[0].id == "JOB-003"


def test_list_script_requests_filter_by_status(db: Database):
    r1 = _make_record("JOB-001")
    db.insert_job(r1)
    r2 = _make_record("JOB-002")
    db.insert_job(r2)
    db._conn.execute("UPDATE jobs SET status='rejected' WHERE id='JOB-002'")
    db._conn.commit()
    pending = db.list_jobs_db(status="pending")
    assert [r.id for r in pending] == ["JOB-001"]


def test_list_script_requests_filter_by_agent(db: Database):
    db.insert_job(_make_record("JOB-001"))
    other = _make_record("JOB-002")
    other.agent_name = "payment_agt"
    db.insert_job(other)
    only_payment = db.list_jobs_db(agent="payment_agt")
    assert [r.id for r in only_payment] == ["JOB-002"]


def test_list_script_requests_limit(db: Database):
    for i in range(1, 11):
        db.insert_job(_make_record(f"JOB-{i:03d}"))
    results = db.list_jobs_db(limit=3)
    assert len(results) == 3
    assert [r.id for r in results] == ["JOB-010", "JOB-009", "JOB-008"]


def test_transition_to_rejected(db: Database):
    db.insert_job(_make_record("JOB-001"))
    db.transition_job_to_rejected("JOB-001", reviewer="founder",
                                     reason="too risky", reviewed_at="2026-05-23T10:05:00Z")
    fetched = db.get_job("JOB-001")
    assert fetched.status == JobStatus.REJECTED
    assert fetched.reviewed_by == "founder"
    assert fetched.reject_reason == "too risky"
    assert fetched.reviewed_at == "2026-05-23T10:05:00Z"


def test_transition_to_rejected_only_from_pending(db: Database):
    db.insert_job(_make_record("JOB-001"))
    db._conn.execute("UPDATE jobs SET status='running' WHERE id='JOB-001'")
    db._conn.commit()
    with pytest.raises(ValueError, match="not_pending"):
        db.transition_job_to_rejected("JOB-001", reviewer="founder",
                                         reason="x", reviewed_at="2026-05-23T10:05:00Z")


def test_transition_to_running(db: Database):
    db.insert_job(_make_record("JOB-001"))
    db.transition_job_to_running(
        "JOB-001",
        reviewer="founder",
        reviewed_at="2026-05-23T10:10:00Z",
        started_at="2026-05-23T10:10:00Z",
        cwd_resolved="/abs/path",
        timeout_seconds=600,
        stdout_path="/abs/jobs/JOB-001.out",
        stderr_path="/abs/jobs/JOB-001.err",
    )
    fetched = db.get_job("JOB-001")
    assert fetched.status == JobStatus.RUNNING
    assert fetched.cwd_resolved == "/abs/path"
    assert fetched.timeout_seconds == 600
    assert fetched.started_at == "2026-05-23T10:10:00Z"


def test_transition_to_terminal_completed(db: Database):
    db.insert_job(_make_record("JOB-001"))
    db.transition_job_to_running(
        "JOB-001", reviewer="founder", reviewed_at="2026-05-23T10:10:00Z",
        started_at="2026-05-23T10:10:00Z", cwd_resolved="/x",
        timeout_seconds=300, stdout_path="/x/JOB-001.out", stderr_path="/x/JOB-001.err",
    )
    db.transition_job_to_terminal(
        "JOB-001",
        status=JobStatus.COMPLETED,
        exit_code=0,
        finished_at="2026-05-23T10:11:00Z",
        duration_ms=60000,
        stdout_head="hello\n",
        stderr_head="",
    )
    fetched = db.get_job("JOB-001")
    assert fetched.status == JobStatus.COMPLETED
    assert fetched.exit_code == 0
    assert fetched.duration_ms == 60000
    assert fetched.stdout_head == "hello\n"


def test_recover_orphaned_running_jobs(db: Database):
    """On daemon startup, any job left in 'running' state is orphaned and
    must be force-transitioned to 'failed' with reason=killed_daemon_restart."""
    db.insert_job(_make_record("JOB-001"))
    db._conn.execute(
        "UPDATE jobs SET status='running', "
        "started_at='2026-05-23T10:00:00Z', cwd_resolved='/x', "
        "stdout_path='/x/JOB-001.out', stderr_path='/x/JOB-001.err' "
        "WHERE id='JOB-001'"
    )
    db._conn.commit()
    recovered = db.recover_orphaned_running_jobs(now_iso="2026-05-23T11:00:00Z")
    assert recovered == ["JOB-001"]
    fetched = db.get_job("JOB-001")
    assert fetched.status == JobStatus.FAILED
    assert fetched.finished_at == "2026-05-23T11:00:00Z"


def test_recover_no_orphans(db: Database):
    db.insert_job(_make_record("JOB-001"))  # stays pending
    assert db.recover_orphaned_running_jobs(now_iso="2026-05-23T11:00:00Z") == []
