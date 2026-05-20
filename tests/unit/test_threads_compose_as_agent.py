"""Unit coverage for agent-initiated thread composition."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.infrastructure.database import Database
from src.models import ThreadRecord


def _columns(db: Database, table: str) -> set[str]:
    cursor = db._conn.execute(f"PRAGMA table_info({table})")
    return {row["name"] for row in cursor.fetchall()}


def test_threads_table_has_composer_columns(tmp_path: Path) -> None:
    db = Database(tmp_path / "grassland.db")
    cols = _columns(db, "threads")
    assert "composed_by" in cols
    assert "composed_from_task_id" in cols
    assert "composed_from_talk_id" in cols


def test_composer_columns_index_present(tmp_path: Path) -> None:
    db = Database(tmp_path / "grassland.db")
    cursor = db._conn.execute("PRAGMA index_list(threads)")
    index_names = {row["name"] for row in cursor.fetchall()}
    assert "idx_threads_composed_from_task" in index_names
    assert "idx_threads_composed_from_talk" in index_names


def test_thread_record_roundtrip_with_composer_fields(tmp_path: Path) -> None:
    db = Database(tmp_path / "grassland.db")
    rec = ThreadRecord(
        id="THR-001",
        subject="cross-team handoff",
        composed_by="engineering_head",
        composed_from_task_id="TASK-091",
    )
    db.insert_thread(rec)
    got = db.get_thread("THR-001")
    assert got is not None
    assert got.composed_by == "engineering_head"
    assert got.composed_from_task_id == "TASK-091"
    assert got.composed_from_talk_id is None


def test_thread_record_defaults_to_founder(tmp_path: Path) -> None:
    db = Database(tmp_path / "grassland.db")
    db.insert_thread(ThreadRecord(id="THR-002", subject="founder thread"))
    got = db.get_thread("THR-002")
    assert got.composed_by == "founder"
    assert got.composed_from_task_id is None
    assert got.composed_from_talk_id is None
