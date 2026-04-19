from __future__ import annotations

import sqlite3
from pathlib import Path


def _write_pre_migration_db(path: Path) -> None:
    """Build a SQLite DB with the pre-migration shape and a row per old status."""
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            assigned_agent TEXT,
            team TEXT NOT NULL DEFAULT 'product_engineering',
            brief TEXT NOT NULL,
            revision_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            parent_task_id TEXT,
            final_output_summary TEXT,
            final_artifact_dir TEXT
        );
    """)
    ts = "2026-04-01T00:00:00+00:00"
    rows = [
        ("T-APR", "general", "approved", "agent-a", "done-summary", None),
        ("T-REJ", "general", "rejected", "agent-b", "rej-summary", None),
        ("T-ESC", "general", "escalated", "agent-c", "esc-reason", None),
        ("T-PEN", "general", "pending", None, None, None),
        ("T-PRO", "general", "in_progress", "agent-d", None, None),
        ("T-COMPLETED", "general", "completed", "agent-e", "old-complete", None),
        ("T-REVIEW", "general", "in_review", "agent-f", "old-review", None),
    ]
    for r in rows:
        conn.execute(
            "INSERT INTO tasks (id, type, status, assigned_agent, brief, "
            "revision_count, created_at, updated_at, final_output_summary, final_artifact_dir) "
            "VALUES (?, ?, ?, ?, 'brief', 0, ?, ?, ?, ?)",
            (r[0], r[1], r[2], r[3], ts, ts, r[4], r[5]),
        )
    conn.commit()
    conn.close()


def test_migration_maps_old_statuses(tmp_path: Path) -> None:
    db_path = tmp_path / "opc.db"
    _write_pre_migration_db(db_path)

    # Trigger the migration by opening the DB through our class.
    from src.infrastructure.database import Database
    db = Database(db_path)

    rows = {r["id"]: dict(r) for r in db._conn.execute("SELECT * FROM tasks")}

    # Status remaps
    assert rows["T-APR"]["status"] == "completed"
    assert rows["T-APR"]["block_kind"] is None
    assert rows["T-REJ"]["status"] == "failed"
    assert rows["T-REJ"]["block_kind"] is None
    assert rows["T-ESC"]["status"] == "blocked"
    assert rows["T-ESC"]["block_kind"] == "escalated"

    # Unchanged non-terminal rows remain unchanged
    assert rows["T-PEN"]["status"] == "pending"
    assert rows["T-PRO"]["status"] == "in_progress"

    # Dead-enum rows get normalized to failed (they were never written in
    # practice but a migration must still leave the table in a legal shape)
    assert rows["T-COMPLETED"]["status"] == "completed"  # already legal
    assert rows["T-REVIEW"]["status"] == "failed"         # in_review → failed

    # final_output_summary folded into note, column still present but unused
    assert rows["T-APR"]["note"] == "done-summary"
    assert rows["T-ESC"]["note"] == "esc-reason"

    # orchestration_step_count defaults to 0
    assert rows["T-PEN"]["orchestration_step_count"] == 0


def test_migration_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "opc.db"
    _write_pre_migration_db(db_path)
    from src.infrastructure.database import Database

    Database(db_path).close()
    # Re-open: migration already applied; this must not raise.
    db = Database(db_path)
    rows = list(db._conn.execute("SELECT status FROM tasks WHERE id='T-APR'"))
    assert rows[0]["status"] == "completed"
