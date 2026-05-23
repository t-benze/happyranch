"""Schema + CRUD tests for script_requests (spec §3.1)."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.infrastructure.database import Database


@pytest.fixture
def db() -> Database:
    d = tempfile.mkdtemp()
    db = Database(Path(d) / "test.db")
    yield db
    db.close()


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
