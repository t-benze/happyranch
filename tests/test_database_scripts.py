"""Schema + CRUD tests for script_requests (spec §3.1)."""
from __future__ import annotations

import pytest

from src.infrastructure.database import Database


def test_script_requests_table_exists(db: Database):
    cur = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='script_requests'"
    )
    assert cur.fetchone() is not None


def test_script_requests_columns(db: Database):
    cur = db._conn.execute("PRAGMA table_info(script_requests)")
    names = {row["name"] for row in cur.fetchall()}
    expected = {
        "id", "task_id", "agent_name", "title", "rationale", "script_text",
        "interpreter", "cwd_hint", "status", "exit_code",
        "stdout_head", "stderr_head", "stdout_path", "stderr_path",
        "duration_ms", "started_at", "finished_at",
        "reviewed_at", "reviewed_by", "reject_reason",
        "cwd_resolved", "timeout_seconds", "created_at",
    }
    assert expected.issubset(names), f"missing: {expected - names}"


def test_script_requests_indexes(db: Database):
    cur = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='script_requests'"
    )
    names = {row["name"] for row in cur.fetchall()}
    assert "idx_script_requests_task" in names
    assert "idx_script_requests_agent" in names
    assert "idx_script_requests_status" in names
    assert "idx_script_requests_created_at" in names


def test_next_script_request_id_first(db: Database):
    assert db.next_script_request_id() == "SR-001"


def test_next_script_request_id_monotonic(db: Database):
    # Manually insert a row with SR-005 to verify the allocator picks SR-006.
    db._conn.execute(
        "INSERT INTO script_requests (id, task_id, agent_name, title, rationale, "
        "script_text, interpreter, status, created_at) "
        "VALUES ('SR-005', 'TASK-001', 'a', 't', 'r', 's', 'bash', 'pending', '2026-05-23T00:00:00Z')"
    )
    db._conn.commit()
    assert db.next_script_request_id() == "SR-006"


from src.models import ScriptRequestRecord, ScriptRequestStatus, ScriptInterpreter


def _make_record(id_: str = "SR-001") -> ScriptRequestRecord:
    return ScriptRequestRecord(
        id=id_,
        task_id="TASK-001",
        agent_name="engineering_head",
        title="Close PR #247",
        rationale="needs founder gh scope",
        script_text="gh pr close 247",
        interpreter=ScriptInterpreter.BASH,
        cwd_hint="repos/web-app",
        created_at="2026-05-23T10:00:00Z",
    )


def test_insert_and_get_script_request(db: Database):
    rec = _make_record()
    db.insert_script_request(rec)
    fetched = db.get_script_request("SR-001")
    assert fetched is not None
    assert fetched.id == "SR-001"
    assert fetched.task_id == "TASK-001"
    assert fetched.agent_name == "engineering_head"
    assert fetched.interpreter == ScriptInterpreter.BASH
    assert fetched.status == ScriptRequestStatus.PENDING
    assert fetched.timeout_seconds == 300
    assert fetched.cwd_hint == "repos/web-app"


def test_get_script_request_missing(db: Database):
    assert db.get_script_request("SR-999") is None
