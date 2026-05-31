"""Unit coverage for agent-initiated thread composition."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.daemon.routes.threads import _thread_row_to_dict
from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database
from src.models import ThreadRecord


def _columns(db: Database, table: str) -> set[str]:
    cursor = db._conn.execute(f"PRAGMA table_info({table})")
    return {row["name"] for row in cursor.fetchall()}


def test_threads_table_has_composer_columns(tmp_path: Path) -> None:
    db = Database(tmp_path / "happyranch.db")
    cols = _columns(db, "threads")
    assert "composed_by" in cols
    assert "composed_from_task_id" in cols
    assert "composed_from_talk_id" in cols


def test_composer_columns_index_present(tmp_path: Path) -> None:
    db = Database(tmp_path / "happyranch.db")
    cursor = db._conn.execute("PRAGMA index_list(threads)")
    index_names = {row["name"] for row in cursor.fetchall()}
    assert "idx_threads_composed_from_task" in index_names
    assert "idx_threads_composed_from_talk" in index_names


def test_thread_record_roundtrip_with_composer_fields(tmp_path: Path) -> None:
    db = Database(tmp_path / "happyranch.db")
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
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-002", subject="founder thread"))
    got = db.get_thread("THR-002")
    assert got.composed_by == "founder"
    assert got.composed_from_task_id is None
    assert got.composed_from_talk_id is None


def test_insert_thread_rejects_dual_binding(tmp_path: Path) -> None:
    db = Database(tmp_path / "happyranch.db")
    with pytest.raises(ValueError, match="mutually exclusive"):
        db.insert_thread(
            ThreadRecord(
                id="THR-099",
                subject="bad",
                composed_by="engineering_head",
                composed_from_task_id="TASK-1",
                composed_from_talk_id="TALK-1",
            )
        )


def test_thread_record_roundtrip_with_talk_binding(tmp_path: Path) -> None:
    """Talk-side roundtrip symmetric to test_thread_record_roundtrip_with_composer_fields."""
    db = Database(tmp_path / "happyranch.db")
    rec = ThreadRecord(
        id="THR-003",
        subject="talk-side handoff",
        composed_by="payment_agt",
        composed_from_talk_id="TALK-042",
    )
    db.insert_thread(rec)
    got = db.get_thread("THR-003")
    assert got is not None
    assert got.composed_by == "payment_agt"
    assert got.composed_from_talk_id == "TALK-042"
    assert got.composed_from_task_id is None


def test_thread_row_dict_exposes_composer_fields(tmp_path: Path) -> None:
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(
        ThreadRecord(
            id="THR-010", subject="s",
            composed_by="engineering_head",
            composed_from_talk_id="TALK-007",
        )
    )
    rec = db.get_thread("THR-010")
    d = _thread_row_to_dict(rec)
    assert d["composed_by"] == "engineering_head"
    assert d["composed_from_task_id"] is None
    assert d["composed_from_talk_id"] == "TALK-007"


def test_log_thread_started_payload_includes_composer(tmp_path: Path) -> None:
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-020", subject="x", composed_by="engineering_head", composed_from_task_id="TASK-9"))
    AuditLogger(db).log_thread_started(
        "THR-020",
        subject="x",
        initial_recipients=["payment_agt"],
        forwarded_from_id=None,
        composed_by="engineering_head",
        composed_from_task_id="TASK-9",
        composed_from_talk_id=None,
    )
    rows = db._conn.execute(
        "SELECT payload FROM audit_log WHERE task_id = ? AND action = 'thread_started'",
        ("THR-020",),
    ).fetchall()
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload"])
    assert payload["composed_by"] == "engineering_head"
    assert payload["composed_from_task_id"] == "TASK-9"
    assert payload["composed_from_talk_id"] is None


