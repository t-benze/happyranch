from __future__ import annotations

import pytest

from src.infrastructure.database import Database
from src.models import TaskRecord


def test_dispatched_from_columns_are_independent(tmp_path):
    db = Database(tmp_path / "opc.db")
    # Both NULL — OK.
    db.insert_task(TaskRecord(id="TASK-001", brief="x"))
    # talk only — OK.
    db.insert_task(TaskRecord(id="TASK-002", brief="x", dispatched_from_talk_id="TALK-1"))
    # thread only — OK.
    db.insert_task(TaskRecord(id="TASK-003", brief="x", dispatched_from_thread_id="THR-1"))
