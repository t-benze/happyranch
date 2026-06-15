"""Test the destructive talk-removal migration ($4.5 of the spec).

Verifies:
- Talks table + 2 indexes are dropped.
- Four talk-reference columns (tasks/jobs/threads/session_token_usage) are gone.
- Five talk-related indexes are gone.
- audit_log talk_* rows are PRESERVED (decision #6).
- Unrelated task/job/thread rows survive intact.
- Second Database() construction is an idempotent no-op.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from runtime.infrastructure.database import Database


def _seed_pre_removal_db(db_path: Path) -> None:
    """Hand-build a DB with the pre-removal schema: talks table, 4 talk columns,
    5 talk indexes, and sample rows including audit_log talk entries."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'pending',
            assigned_agent TEXT,
            team TEXT NOT NULL DEFAULT 'engineering',
            brief TEXT NOT NULL,
            task_type TEXT NOT NULL DEFAULT 'task',
            revision_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            parent_task_id TEXT,
            final_output_summary TEXT,
            final_output_dir TEXT,
            dispatched_from_talk_id TEXT,
            dispatched_from_thread_id TEXT
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            title TEXT NOT NULL,
            rationale TEXT,
            script_text TEXT NOT NULL,
            interpreter TEXT NOT NULL,
            cwd_hint TEXT,
            review_required INTEGER NOT NULL DEFAULT 0,
            persistent INTEGER NOT NULL DEFAULT 0,
            max_runtime_seconds INTEGER,
            max_output_bytes INTEGER NOT NULL DEFAULT 52428800,
            status TEXT NOT NULL DEFAULT 'pending',
            exit_code INTEGER,
            reason TEXT,
            duration_ms INTEGER,
            stdout_head TEXT,
            stderr_head TEXT,
            stdout_path TEXT,
            stderr_path TEXT,
            stdout_bytes INTEGER,
            stderr_bytes INTEGER,
            cwd_resolved TEXT,
            started_at TEXT,
            finished_at TEXT,
            reviewed_at TEXT,
            reviewed_by TEXT,
            reject_reason TEXT,
            created_at TEXT NOT NULL,
            submitted_from_talk_id TEXT
        );

        CREATE TABLE IF NOT EXISTS threads (
            id TEXT PRIMARY KEY,
            subject TEXT NOT NULL,
            started_at TEXT NOT NULL,
            archived_at TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            forwarded_from_id TEXT,
            forwarded_from_kind TEXT,
            turn_cap INTEGER NOT NULL DEFAULT 500,
            turns_used INTEGER NOT NULL DEFAULT 0,
            summary TEXT,
            transcript_path TEXT,
            composed_by TEXT NOT NULL DEFAULT 'founder',
            composed_from_task_id TEXT,
            composed_from_talk_id TEXT
        );

        CREATE TABLE IF NOT EXISTS session_token_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT,
            agent TEXT NOT NULL,
            session_id TEXT NOT NULL,
            executor TEXT NOT NULL,
            model TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            cache_read_tokens INTEGER,
            cache_creation_tokens INTEGER,
            reasoning_tokens INTEGER,
            usage_raw_json TEXT,
            scope_type TEXT,
            scope_id TEXT,
            thread_id TEXT,
            talk_id TEXT,
            invocation_purpose TEXT,
            created_at TEXT NOT NULL,
            UNIQUE (task_id, agent, session_id)
        );

        CREATE TABLE IF NOT EXISTS talks (
            id TEXT PRIMARY KEY,
            agent_name TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            summary TEXT,
            topic_list_json TEXT,
            new_learnings_count INTEGER NOT NULL DEFAULT 0,
            new_kb_slugs_json TEXT,
            transcript_path TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_talks_agent_status ON talks(agent_name, status);
        CREATE INDEX IF NOT EXISTS idx_talks_started ON talks(started_at);

        CREATE INDEX IF NOT EXISTS idx_tasks_dispatched_from_talk_id
            ON tasks(dispatched_from_talk_id) WHERE dispatched_from_talk_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_threads_composed_from_talk
            ON threads(composed_from_talk_id) WHERE composed_from_talk_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_session_token_usage_talk
            ON session_token_usage(talk_id) WHERE talk_id IS NOT NULL;

        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            agent TEXT NOT NULL,
            action TEXT NOT NULL,
            payload TEXT,
            timestamp TEXT NOT NULL
        );

        -- Sample data
        INSERT INTO tasks (id, brief, assigned_agent, dispatched_from_talk_id, created_at, updated_at)
            VALUES ('TASK-001', 'test task', 'dev_agent', 'TALK-001', '2025-01-01T00:00:00', '2025-01-01T00:00:00');
        INSERT INTO jobs (id, task_id, agent_name, title, script_text, interpreter, created_at, submitted_from_talk_id)
            VALUES ('JOB-001', 'TASK-001', 'dev_agent', 'test job', 'echo hi', 'bash', '2025-01-01', 'TALK-001');
        INSERT INTO threads (id, subject, composed_from_talk_id, started_at)
            VALUES ('THR-001', 'test thread', 'TALK-001', '2025-01-01');
        INSERT INTO session_token_usage (task_id, agent, session_id, executor, talk_id, created_at)
            VALUES ('TASK-001', 'dev_agent', 'sess-1', 'claude', 'TALK-001', '2025-01-01');

        -- Audit log: talk rows that must survive
        INSERT INTO audit_log (task_id, agent, action, payload, timestamp)
            VALUES ('TALK-001', 'dev_agent', 'talk_started', '{}', '2025-01-01');
        INSERT INTO audit_log (task_id, agent, action, payload, timestamp)
            VALUES ('TALK-002', 'dev_agent', 'talk_ended', '{}', '2025-01-02');
        INSERT INTO audit_log (task_id, agent, action, payload, timestamp)
            VALUES ('TASK-001', 'dev_agent', 'task_completed', '{}', '2025-01-03');
    """)
    conn.commit()
    conn.close()


class TestTalkRemovalMigration:
    def test_migration_drops_talks_table_and_columns(self, tmp_path: Path) -> None:
        """Seed pre-removal DB, run Database(), assert table + 4 columns + 5 indexes gone."""
        db_path = tmp_path / "test.db"
        _seed_pre_removal_db(db_path)

        # Pre-condition: verify the pre-removal state
        pre_conn = sqlite3.connect(str(db_path))
        tables = {r[0] for r in pre_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "talks" in tables
        # Verify talk columns exist
        for table, col in [
            ("tasks", "dispatched_from_talk_id"),
            ("jobs", "submitted_from_talk_id"),
            ("threads", "composed_from_talk_id"),
            ("session_token_usage", "talk_id"),
        ]:
            cols = {r[1] for r in pre_conn.execute(f"PRAGMA table_info({table})")}
            assert col in cols, f"{col} missing from {table} pre-migration"
        pre_conn.close()

        # Run the migration
        db = Database(db_path)

        # Post-condition: talks table gone
        post_conn = sqlite3.connect(str(db_path))
        post_tables = {r[0] for r in post_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "talks" not in post_tables

        # Post-condition: 4 talk columns gone
        for table, col in [
            ("tasks", "dispatched_from_talk_id"),
            ("jobs", "submitted_from_talk_id"),
            ("threads", "composed_from_talk_id"),
            ("session_token_usage", "talk_id"),
        ]:
            cols = {r[1] for r in post_conn.execute(f"PRAGMA table_info({table})")}
            assert col not in cols, f"{col} still present in {table} after migration"

        # Post-condition: 5 talk indexes gone
        indexes = {r[0] for r in post_conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        for idx in (
            "idx_talks_agent_status",
            "idx_talks_started",
            "idx_tasks_dispatched_from_talk_id",
            "idx_threads_composed_from_talk",
            "idx_session_token_usage_talk",
        ):
            assert idx not in indexes, f"{idx} still present after migration"

        # Post-condition: audit_log talk rows PRESERVED
        talk_audit = post_conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE task_id LIKE 'TALK-%'"
        ).fetchone()[0]
        assert talk_audit == 2, f"Expected 2 talk audit rows, got {talk_audit}"

        # Post-condition: unrelated rows intact
        task_count = post_conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        assert task_count == 1
        job_count = post_conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        assert job_count == 1

        post_conn.close()

    def test_migration_is_idempotent(self, tmp_path: Path) -> None:
        """Second Database() construction on an already-migrated DB is a no-op."""
        db_path = tmp_path / "test.db"
        _seed_pre_removal_db(db_path)

        # First migration
        db1 = Database(db_path)
        # Second construction: should not raise
        db2 = Database(db_path)

        # Verify tables still intact (no corruption from double-run)
        conn = sqlite3.connect(str(db_path))
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "tasks" in tables
        assert "jobs" in tables
        assert "threads" in tables
        assert "session_token_usage" in tables
        assert "audit_log" in tables
        assert "talks" not in tables
        conn.close()

    def test_migration_preserves_audit_log_non_talk_rows(self, tmp_path: Path) -> None:
        """Non-talk audit rows survive the migration untouched."""
        db_path = tmp_path / "test.db"
        _seed_pre_removal_db(db_path)

        Database(db_path)

        conn = sqlite3.connect(str(db_path))
        task_audit = conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE task_id = 'TASK-001'"
        ).fetchone()[0]
        assert task_audit == 1, "Non-talk audit row was lost"
        conn.close()
