from __future__ import annotations

import pytest

from src.infrastructure.database import Database
from src.models import TaskRecord


def test_dispatched_from_thread_id_round_trips(tmp_path):
    """After Task 4 wires TaskRecord + insert_task, a thread-dispatched task
    should round-trip its dispatched_from_thread_id through SQLite. Today
    this fails: Pydantic drops the unknown field and/or insert_task ignores
    the column.
    """
    db = Database(tmp_path / "opc.db")
    db.insert_task(TaskRecord(
        id="TASK-001", brief="x", dispatched_from_thread_id="THR-007",
    ))
    fetched = db.get_task("TASK-001")
    assert fetched is not None
    assert fetched.dispatched_from_thread_id == "THR-007"


def test_dispatched_from_talk_id_round_trips(tmp_path):
    """Regression guard for the sibling column. Should pass today and after Task 4."""
    db = Database(tmp_path / "opc.db")
    db.insert_task(TaskRecord(
        id="TASK-002", brief="x", dispatched_from_talk_id="TALK-1",
    ))
    fetched = db.get_task("TASK-002")
    assert fetched is not None
    assert fetched.dispatched_from_talk_id == "TALK-1"
