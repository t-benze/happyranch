"""Unit tests for Orchestrator.run_step — the single primitive that advances
a task one subprocess call at a time under the new async execution model."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.config import Settings
from src.infrastructure.database import Database
from src.models import BlockKind, TaskRecord, TaskStatus, TaskType
from src.runtime import RuntimeDir


@pytest.fixture
def runtime(tmp_path: Path) -> RuntimeDir:
    return RuntimeDir.init(tmp_path / "rt")


@pytest.fixture
def db(runtime: RuntimeDir) -> Database:
    return Database(runtime.db_path)


def test_run_step_silent_noop_when_task_missing(runtime, db):
    from src.orchestrator.orchestrator import Orchestrator
    settings = Settings(max_orchestration_steps=3)
    orch = Orchestrator(db=db, settings=settings, runtime=runtime)
    # Just must not raise
    orch.run_step("TASK-NOPE")


def test_run_step_noop_on_blocked_escalated(runtime, db):
    """A task in blocked(ESCALATED) isn't eligible for run_step — it waits
    for /resolve-escalation to transition it first. Second-hand enqueue
    must be silently ignored."""
    from src.orchestrator.orchestrator import Orchestrator
    db.insert_task(TaskRecord(id="T-1", type=TaskType.GENERAL, brief="x"))
    db.update_task("T-1", status=TaskStatus.BLOCKED, block_kind=BlockKind.ESCALATED,
                   note="halted")
    orch = Orchestrator(db=db, settings=Settings(), runtime=runtime)
    orch.run_step("T-1")
    t = db.get_task("T-1")
    assert t.status == TaskStatus.BLOCKED
    assert t.block_kind == BlockKind.ESCALATED
