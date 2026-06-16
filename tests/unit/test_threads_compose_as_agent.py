"""Unit coverage for agent-initiated thread composition."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.daemon.routes.threads import _thread_row_to_dict
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.database import Database
from runtime.models import ThreadRecord


def _columns(db: Database, table: str) -> set[str]:
    cursor = db._conn.execute(f"PRAGMA table_info({table})")
    return {row["name"] for row in cursor.fetchall()}


def test_threads_table_has_composer_columns(tmp_path: Path) -> None:
    db = Database(tmp_path / "happyranch.db")
    cols = _columns(db, "threads")
    assert "composed_by" in cols
    assert "composed_from_task_id" in cols
    assert "composed_from_dream_id" in cols


def test_composer_columns_index_present(tmp_path: Path) -> None:
    db = Database(tmp_path / "happyranch.db")
    cursor = db._conn.execute("PRAGMA index_list(threads)")
    index_names = {row["name"] for row in cursor.fetchall()}
    assert "idx_threads_composed_from_task" in index_names
    assert "idx_threads_composed_from_dream" in index_names


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


def test_thread_record_defaults_to_founder(tmp_path: Path) -> None:
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-002", subject="founder thread"))
    got = db.get_thread("THR-002")
    assert got.composed_by == "founder"
    assert got.composed_from_task_id is None


def test_thread_record_roundtrip_no_task_binding(tmp_path: Path) -> None:
    """Thread roundtrip with composed_by set but no composed_from_task_id."""
    db = Database(tmp_path / "happyranch.db")
    rec = ThreadRecord(
        id="THR-003",
        subject="talk-side handoff",
        composed_by="payment_agt",
    )
    db.insert_thread(rec)
    got = db.get_thread("THR-003")
    assert got is not None
    assert got.composed_by == "payment_agt"
    assert got.composed_from_task_id is None


def test_thread_row_dict_exposes_composer_fields(tmp_path: Path) -> None:
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(
        ThreadRecord(
            id="THR-010", subject="s",
            composed_by="engineering_head",
        )
    )
    rec = db.get_thread("THR-010")
    d = _thread_row_to_dict(rec)
    assert d["composed_by"] == "engineering_head"
    assert d["composed_from_task_id"] is None


def test_thread_roundtrip_with_dream_id(tmp_path: Path) -> None:
    """Thread composed by a dream persists composed_from_dream_id."""
    db = Database(tmp_path / "happyranch.db")
    rec = ThreadRecord(
        id="THR-030",
        subject="dream reflection thread",
        composed_by="dev_agent",
        composed_from_dream_id="DREAM-001",
    )
    db.insert_thread(rec)
    got = db.get_thread("THR-030")
    assert got is not None
    assert got.composed_from_dream_id == "DREAM-001"


def test_thread_without_dream_id_reads_null(tmp_path: Path) -> None:
    """Back-compat: old threads without the column read back NULL."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-031", subject="coordination thread"))
    got = db.get_thread("THR-031")
    assert got is not None
    assert got.composed_from_dream_id is None


def test_row_to_thread_pre_migration_row_no_composed_from_dream_id_key(tmp_path: Path) -> None:
    """Back-compat: _row_to_thread on a v0/v1 row projection lacking the
    composed_from_dream_id column reads back None without KeyError.

    Exercises the 'composed_from_dream_id in keys' guard at
    runtime/infrastructure/database.py:_row_to_thread.
    """
    db = Database(tmp_path / "happyranch.db")
    # Simulate a pre-migration row (no composed_by / composed_from_task_id /
    # composed_from_dream_id columns in the SELECT).
    row = {
        "id": "THR-PRE",
        "subject": "pre-migration thread",
        "status": "open",
        "started_at": "2025-01-01T00:00:00+00:00",
        "archived_at": None,
        "forwarded_from_id": None,
        "forwarded_from_kind": None,
        "turn_cap": 500,
        "turns_used": 0,
        "summary": None,
        "transcript_path": None,
        # composed_by, composed_from_task_id, composed_from_dream_id
        # intentionally absent — as in a v0/v1 schema.
    }
    rec = db._row_to_thread(row)
    assert rec.composed_by == "founder"
    assert rec.composed_from_task_id is None
    assert rec.composed_from_dream_id is None


def test_thread_row_dict_exposes_dream_id(tmp_path: Path) -> None:
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(
        ThreadRecord(
            id="THR-032", subject="dream thread",
            composed_by="dev_agent",
            composed_from_dream_id="DREAM-002",
        )
    )
    rec = db.get_thread("THR-032")
    d = _thread_row_to_dict(rec)
    assert d["composed_from_dream_id"] == "DREAM-002"


def test_thread_row_dict_null_dream_id(tmp_path: Path) -> None:
    """Coordination threads expose composed_from_dream_id as None."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-033", subject="task coordination"))
    rec = db.get_thread("THR-033")
    d = _thread_row_to_dict(rec)
    assert d["composed_from_dream_id"] is None


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
    )
    rows = db._conn.execute(
        "SELECT payload FROM audit_log WHERE task_id = ? AND action = 'thread_started'",
        ("THR-020",),
    ).fetchall()
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload"])
    assert payload["composed_by"] == "engineering_head"
    assert payload["composed_from_task_id"] == "TASK-9"


