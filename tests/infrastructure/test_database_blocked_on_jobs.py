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
