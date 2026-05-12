from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _build_orch_with_task(tmp_path: Path, predecessor_root_status: str):
    """Helper builds the minimal orchestrator state needed to drive
    _maybe_spawn_auto_revisit. Returns (orch, failed_task_id, agent)."""
    from src.infrastructure.database import Database
    from src.infrastructure.audit_logger import AuditLogger
    from src.models import TaskRecord, TaskStatus

    db = Database(tmp_path / "opc.db")
    db.insert_task(TaskRecord(
        id="TASK-1", brief="x", team="engineering",
        assigned_agent="manager", status=TaskStatus(predecessor_root_status),
    ))
    audit = AuditLogger(db)
    orch = MagicMock()
    orch._db = db
    orch._audit = audit
    orch._queue = MagicMock()
    orch._slug = "acme"
    return orch, "TASK-1", "manager"


def test_returns_true_when_spawned(tmp_path: Path):
    from src.orchestrator.run_step import _maybe_spawn_auto_revisit
    orch, failed_id, agent = _build_orch_with_task(tmp_path, "failed")
    spawned = _maybe_spawn_auto_revisit(
        orch, failed_id, agent, error_context={"mode": "exception", "detail": "boom"},
    )
    assert spawned is True


def test_returns_false_when_no_chain(tmp_path: Path):
    from src.orchestrator.run_step import _maybe_spawn_auto_revisit
    orch = MagicMock()
    orch._db.walk_ancestors.return_value = []  # no chain → False
    spawned = _maybe_spawn_auto_revisit(
        orch, "TASK-X", "agent", error_context={},
    )
    assert spawned is False


def test_returns_false_when_cap_hit(tmp_path: Path, monkeypatch):
    from src.orchestrator import run_step
    from src.orchestrator.run_step import _maybe_spawn_auto_revisit, _AUTO_REVISIT_CAP

    orch, failed_id, agent = _build_orch_with_task(tmp_path, "failed")

    # Stub walk_revisit_chain + audit_logs to simulate cap-hit
    fake_revisit_chain = [MagicMock(id=f"TASK-AR{i}") for i in range(_AUTO_REVISIT_CAP)]
    orch._db.walk_revisit_chain = MagicMock(return_value=fake_revisit_chain)
    orch._db.get_audit_logs = MagicMock(
        return_value=[{"action": "auto_revisit_of"}]
    )
    orch._db.walk_ancestors = MagicMock(
        return_value=[MagicMock(id="TASK-1", brief="x", team="engineering",
                                assigned_agent="manager",
                                session_timeout_seconds=None)]
    )

    spawned = _maybe_spawn_auto_revisit(
        orch, failed_id, agent, error_context={},
    )
    assert spawned is False
