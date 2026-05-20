"""Unit coverage for agent-initiated thread composition."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.infrastructure.database import Database


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
