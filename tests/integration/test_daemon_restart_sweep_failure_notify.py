from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


pytestmark = pytest.mark.integration


def test_sweep_calls_notify_failed_not_escalated(tmp_path: Path):
    """Daemon-restart sweep should classify mid-task failures as failures,
    not escalations — APPROVE/REJECT don't make sense for FAILED tasks."""
    from src.daemon.__main__ import _sweep_on_startup
    from src.infrastructure.database import Database
    from src.models import TaskRecord, TaskStatus

    db = Database(tmp_path / "opc.db")
    db.insert_task(TaskRecord(
        id="TASK-1", brief="x", team="engineering",
        assigned_agent="dev_agent", status=TaskStatus.IN_PROGRESS,
    ))

    queue = MagicMock()
    orchestrator = MagicMock()

    _sweep_on_startup(db, queue, "acme", orchestrator)

    # Verify the new notify path was used, not the old one
    assert orchestrator.notify_failed.called
    assert not orchestrator.notify_escalated.called

    kwargs = orchestrator.notify_failed.call_args.kwargs
    assert kwargs["failure_kind"] == "daemon_restart"
    assert kwargs["task_id"] == "TASK-1"
    assert kwargs["agent"] == "dev_agent"  # task's assigned_agent

    # Task is transitioned to FAILED
    task = db.get_task("TASK-1")
    assert task.status == TaskStatus.FAILED


def test_sweep_uses_unknown_for_missing_assigned_agent(tmp_path: Path):
    """If task.assigned_agent is None, notify_failed gets agent='(unknown)'."""
    from src.daemon.__main__ import _sweep_on_startup
    from src.infrastructure.database import Database
    from src.models import TaskRecord, TaskStatus

    db = Database(tmp_path / "opc.db")
    db.insert_task(TaskRecord(
        id="TASK-2", brief="x", team="engineering",
        assigned_agent=None,  # ← no assigned agent
        status=TaskStatus.IN_PROGRESS,
    ))

    queue = MagicMock()
    orchestrator = MagicMock()

    _sweep_on_startup(db, queue, "acme", orchestrator)

    assert orchestrator.notify_failed.called
    kwargs = orchestrator.notify_failed.call_args.kwargs
    assert kwargs["agent"] == "(unknown)"
