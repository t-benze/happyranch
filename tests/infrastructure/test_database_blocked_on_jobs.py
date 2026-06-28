from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from runtime.infrastructure.database import Database
from runtime.models import BlockKind


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

    from runtime.models import TaskStatus

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        db = Database(db_path)
        from runtime.models import TaskRecord
        task = TaskRecord(
            id="TASK-001", team="engineering", brief="t",
            status=TaskStatus.IN_PROGRESS, parent_task_id=None,
        )
        db.insert_task(task)
        db.update_task(
            "TASK-001",
            status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.BLOCKED_ON_JOB,
            blocked_on_job_ids=json.dumps(["JOB-12", "JOB-13"]),
        )
        loaded = db.get_task("TASK-001")
        assert loaded.status == TaskStatus.IN_PROGRESS
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


def test_list_tasks_blocked_on_jobs_filters_correctly():
    """Returns only ids of BLOCKED+BLOCKED_ON_JOB tasks; excludes other blocked."""
    from runtime.models import TaskRecord, TaskStatus

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        db = Database(db_path)

        # Three tasks: one blocked-on-job, one escalated, one in_progress.
        for tid, status, bk, jids in (
            ("TASK-A", TaskStatus.IN_PROGRESS, BlockKind.BLOCKED_ON_JOB, '["JOB-1"]'),
            ("TASK-B", TaskStatus.ESCALATED, None,     None),
            ("TASK-C", TaskStatus.IN_PROGRESS, None,                None),
        ):
            db.insert_task(TaskRecord(
                id=tid, team="engineering", brief="t",
                status=status, parent_task_id=None,
            ))
            if bk is not None:
                db.update_task(tid, status=status, block_kind=bk,
                              blocked_on_job_ids=jids)

        result = db.list_tasks_blocked_on_jobs()
        assert set(result) == {"TASK-A"}


def test_list_tasks_blocked_on_job_id_filter_requires_blocked_status():
    """list_tasks(blocked_on_job_id=...) MUST constrain status=blocked AND
    block_kind=blocked_on_job — non-blocked (e.g., done) tasks with the same
    job in blocked_on_job_ids must NOT appear.

    Regression for TASK-548: the initial DERIVE filter did an unconstrained
    LIKE match, leaking stale done/running tasks into the "if approved" cascade.
    """
    import json
    from runtime.models import TaskRecord, TaskStatus

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        db = Database(db_path)

        # (a) A task that WAS blocked on JOB-X but is now DONE — must NOT appear.
        done_task = TaskRecord(
            id="TASK-DONE", team="engineering", brief="done",
            status=TaskStatus.FAILED, parent_task_id=None,
        )
        db.insert_task(done_task)
        db.update_task(
            "TASK-DONE",
            status=TaskStatus.FAILED,
            blocked_on_job_ids=json.dumps(["JOB-X"]),
        )

        # (b) A genuinely BLOCKED + BLOCKED_ON_JOB task on JOB-X — MUST appear.
        blocked_task = TaskRecord(
            id="TASK-BLOCKED", team="engineering", brief="blocked",
            status=TaskStatus.IN_PROGRESS, parent_task_id=None,
        )
        db.insert_task(blocked_task)
        db.update_task(
            "TASK-BLOCKED",
            status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.BLOCKED_ON_JOB,
            blocked_on_job_ids=json.dumps(["JOB-X"]),
        )

        # (c) A task blocked on JOB-12 — must NOT match JOB-1.
        wrong_job_task = TaskRecord(
            id="TASK-JOB12", team="engineering", brief="wrong",
            status=TaskStatus.IN_PROGRESS, parent_task_id=None,
        )
        db.insert_task(wrong_job_task)
        db.update_task(
            "TASK-JOB12",
            status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.BLOCKED_ON_JOB,
            blocked_on_job_ids=json.dumps(["JOB-12"]),
        )

        # Query for JOB-X: should return ONLY TASK-BLOCKED.
        result = db.list_tasks(blocked_on_job_id="JOB-X", limit=50)
        result_ids = [t.id for t in result]

        # (a) Done task must NOT appear.
        assert "TASK-DONE" not in result_ids, (
            f"TASK-DONE (status=FAILED) leaked into blocked_on_job_id filter; "
            f"got {result_ids}"
        )

        # (b) Blocked task MUST appear.
        assert "TASK-BLOCKED" in result_ids, (
            f"TASK-BLOCKED (BLOCKED+BLOCKED_ON_JOB) missing from blocked_on_job_id filter; "
            f"got {result_ids}"
        )

        # (c) JOB-12 must NOT match JOB-1.
        result_job1 = db.list_tasks(blocked_on_job_id="JOB-1", limit=50)
        job1_ids = [t.id for t in result_job1]
        assert "TASK-JOB12" not in job1_ids, (
            f"JOB-12 leaked into blocked_on_job_id='JOB-1' filter; "
            f"got {job1_ids}"
        )
