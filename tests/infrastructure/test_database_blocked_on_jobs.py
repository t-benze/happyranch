from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from src.infrastructure.database import Database
from src.models import BlockKind


def test_blocked_on_job_enum_value():
    """BlockKind has a BLOCKED_ON_JOB value with the string 'blocked_on_job'."""
    assert BlockKind.BLOCKED_ON_JOB.value == "blocked_on_job"
    assert BlockKind("blocked_on_job") is BlockKind.BLOCKED_ON_JOB


def test_blocked_on_job_ids_column_added():
    """Database init adds blocked_on_job_ids TEXT column to tasks table."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        db = Database(db_path)
        # Inspect schema directly via raw sqlite3 so we don't depend on ORM-side
        # field discovery — the column has to exist at the SQL layer.
        conn = sqlite3.connect(db_path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        conn.close()
        assert "blocked_on_job_ids" in cols


def test_migration_is_idempotent():
    """Running migration twice (re-opening Database) doesn't error."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        Database(db_path)  # first init creates column
        Database(db_path)  # second open should swallow "duplicate column"


def test_blocked_on_job_ids_round_trips_through_update_and_read():
    """update_task can set blocked_on_job_ids; get_task reads it back."""
    import json

    from src.models import TaskStatus

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        db = Database(db_path)
        from src.models import TaskRecord
        task = TaskRecord(
            id="TASK-001", team="engineering", brief="t",
            status=TaskStatus.IN_PROGRESS, parent_task_id=None,
        )
        db.insert_task(task)
        db.update_task(
            "TASK-001",
            status=TaskStatus.BLOCKED,
            block_kind=BlockKind.BLOCKED_ON_JOB,
            blocked_on_job_ids=json.dumps(["JOB-12", "JOB-13"]),
        )
        loaded = db.get_task("TASK-001")
        assert loaded.status == TaskStatus.BLOCKED
        assert loaded.block_kind == BlockKind.BLOCKED_ON_JOB
        assert loaded.blocked_on_job_ids == '["JOB-12", "JOB-13"]'


def test_get_job_status_terminal_and_running():
    """get_job_status returns the jobs.status string, or None if unknown."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        db = Database(db_path)
        # Insert two jobs directly. The jobs table is part of the per-org schema.
        # Use raw SQL since this is a DB-layer test.
        conn = db._conn
        conn.execute(
            "INSERT INTO jobs (id, task_id, agent_name, title, script_text, "
            "interpreter, status, created_at) VALUES "
            "('JOB-001', 'TASK-001', 'agent', 't', 's', 'bash', 'completed', "
            "'2026-05-28T00:00:00')"
        )
        conn.execute(
            "INSERT INTO jobs (id, task_id, agent_name, title, script_text, "
            "interpreter, status, created_at) VALUES "
            "('JOB-002', 'TASK-001', 'agent', 't', 's', 'bash', 'running', "
            "'2026-05-28T00:00:00')"
        )
        conn.commit()

        assert db.get_job_status("JOB-001") == "completed"
        assert db.get_job_status("JOB-002") == "running"
        assert db.get_job_status("JOB-999") is None
