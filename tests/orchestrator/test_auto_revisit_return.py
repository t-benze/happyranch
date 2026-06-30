from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _build_orch_with_task(tmp_path: Path, predecessor_root_status: str):
    """Helper builds the minimal orchestrator state needed to drive
    _maybe_spawn_auto_revisit. Returns (orch, failed_task_id, agent)."""
    from runtime.infrastructure.database import Database
    from runtime.infrastructure.audit_logger import AuditLogger
    from runtime.models import TaskRecord, TaskStatus

    db = Database(tmp_path / "happyranch.db")
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
    from runtime.orchestrator.run_step import _maybe_spawn_auto_revisit
    orch, failed_id, agent = _build_orch_with_task(tmp_path, "failed")
    spawned = _maybe_spawn_auto_revisit(
        orch, failed_id, agent,
        failure_kind="agent_exception",
        error_context={"mode": "exception", "detail": "boom"},
    )
    assert spawned is True


def test_returns_false_when_no_chain(tmp_path: Path):
    from runtime.orchestrator.run_step import _maybe_spawn_auto_revisit
    orch = MagicMock()
    orch._db.walk_ancestors.return_value = []  # no chain → False
    spawned = _maybe_spawn_auto_revisit(
        orch, "TASK-X", "agent",
        failure_kind="session_failed",
        error_context={},
    )
    assert spawned is False


def test_returns_false_when_task_cancelled(tmp_path: Path):
    """Founder cancellation must not auto-revisit. /cancel stamps
    cancelled_at + flips status to FAILED, then SIGTERMs the subprocess;
    run_step's post-Popen classifier re-enters the opaque-failure path
    (rc=-15, success=False) and calls _maybe_spawn_auto_revisit. The
    docstring explicitly excludes founder cancellations — implementation
    must honour it, else every cancel respawns a new root immediately."""
    from datetime import datetime, timezone

    from runtime.infrastructure.database import Database
    from runtime.infrastructure.audit_logger import AuditLogger
    from runtime.models import TaskRecord, TaskStatus
    from runtime.orchestrator.run_step import _maybe_spawn_auto_revisit

    db = Database(tmp_path / "happyranch.db")
    db.insert_task(TaskRecord(
        id="TASK-1", brief="x", team="engineering",
        assigned_agent="manager", status=TaskStatus.FAILED,
    ))
    # Simulate /cancel's phase-1 write: status=FAILED + cancelled_at.
    now = datetime.now(timezone.utc).isoformat()
    db.update_task(
        "TASK-1",
        status=TaskStatus.FAILED,
        block_kind=None,
        note="cancelled by founder: enough",
        cancelled_at=now,
        completed_at=now,
    )

    orch = MagicMock()
    orch._db = db
    orch._audit = AuditLogger(db)
    orch._queue = MagicMock()
    orch._slug = "acme"

    spawned = _maybe_spawn_auto_revisit(
        orch, "TASK-1", "manager",
        failure_kind="executor_error",
        error_context={"mode": "session_failure", "rc": -15},
    )
    assert spawned is False
    # No new root row inserted.
    assert db.get_task("TASK-2") is None
    # No auto_revisit_of audit entry written.
    rows = db.get_audit_logs("TASK-1")
    assert not any(r["action"] == "auto_revisit_of" for r in rows)
    # Queue never received an enqueue.
    orch._queue.put_nowait.assert_not_called()


def test_returns_false_when_cap_hit(tmp_path: Path, monkeypatch):
    from runtime.orchestrator.run_step import (
        _AUTO_REVISIT_CAP_PER_KIND,
        _maybe_spawn_auto_revisit,
    )

    orch, failed_id, agent = _build_orch_with_task(tmp_path, "failed")

    # Stub walk_revisit_chain + audit_logs to simulate per-kind cap-hit:
    # each fake predecessor carries an auto_revisit_of audit row whose payload
    # carries the same failure_kind we're now trying to spawn for. Under the
    # per-kind cap (spec §5) two prior same-kind entries exhaust the budget.
    fake_revisit_chain = [
        MagicMock(id=f"TASK-AR{i}") for i in range(_AUTO_REVISIT_CAP_PER_KIND)
    ]
    orch._db.walk_revisit_chain = MagicMock(return_value=fake_revisit_chain)
    orch._db.get_audit_logs = MagicMock(
        return_value=[{
            "action": "auto_revisit_of",
            "payload": {"failure_kind": "session_timeout"},
        }]
    )
    orch._db.walk_ancestors = MagicMock(
        return_value=[MagicMock(
            id="TASK-1", brief="x", team="engineering",
            assigned_agent="manager",
            session_timeout_seconds=None,
            cancelled_at=None,
        )]
    )

    spawned = _maybe_spawn_auto_revisit(
        orch, failed_id, agent,
        failure_kind="session_timeout",
        error_context={},
    )
    assert spawned is False


# --- Thread linkage inheritance tests (THR-046 message 64) ---


def test_auto_revisit_inherits_thread_linkage_from_root(tmp_path: Path):
    """Thread-dispatched root's auto-revisit successor inherits
    dispatched_from_thread_id so the task list does not treat it as
    'no thread'."""
    from runtime.infrastructure.database import Database
    from runtime.infrastructure.audit_logger import AuditLogger
    from runtime.models import TaskRecord, TaskStatus
    from runtime.orchestrator.run_step import _maybe_spawn_auto_revisit

    db = Database(tmp_path / "happyranch.db")
    db.insert_task(TaskRecord(
        id="TASK-1", brief="x", team="engineering",
        assigned_agent="manager", status=TaskStatus.FAILED,
        dispatched_from_thread_id="THR-0046",
    ))
    audit = AuditLogger(db)
    orch = MagicMock()
    orch._db = db
    orch._audit = audit
    orch._queue = MagicMock()
    orch._slug = "acme"

    spawned = _maybe_spawn_auto_revisit(
        orch, "TASK-1", "manager",
        failure_kind="session_timeout",
        error_context={},
    )
    assert spawned is True

    # The auto-revisit successor inherits the thread linkage from the root.
    # next_task_id() returns TASK-NNN format based on MAX numeric suffix.
    successor = db.get_task("TASK-002")
    assert successor is not None
    assert successor.revisit_of_task_id == "TASK-1"
    assert successor.dispatched_from_thread_id == "THR-0046"


def test_auto_revisit_walks_revisit_chain_for_thread_linkage(tmp_path: Path):
    """When an auto-revisit fires on a root that is itself a revisit of a
    thread-dispatched original, the successor inherits the thread linkage
    from the original found by walking the revisit chain."""
    from runtime.infrastructure.database import Database
    from runtime.infrastructure.audit_logger import AuditLogger
    from runtime.models import TaskRecord, TaskStatus
    from runtime.orchestrator.run_step import _maybe_spawn_auto_revisit

    db = Database(tmp_path / "happyranch.db")
    # Original: thread-dispatched
    db.insert_task(TaskRecord(
        id="TASK-1", brief="x", team="engineering",
        assigned_agent="manager", status=TaskStatus.FAILED,
        dispatched_from_thread_id="THR-0046",
    ))
    # Revisit of original (founder revisit or prior auto-revisit).
    # The revisit root itself does NOT carry dispatched_from_thread_id
    # (matching current revisit behavior).
    db.insert_task(TaskRecord(
        id="TASK-002", brief="x", team="engineering",
        assigned_agent="manager", status=TaskStatus.FAILED,
        revisit_of_task_id="TASK-1",
    ))
    audit = AuditLogger(db)
    orch = MagicMock()
    orch._db = db
    orch._audit = audit
    orch._queue = MagicMock()
    orch._slug = "acme"

    spawned = _maybe_spawn_auto_revisit(
        orch, "TASK-002", "manager",
        failure_kind="session_timeout",
        error_context={},
    )
    assert spawned is True

    successor = db.get_task("TASK-003")
    assert successor is not None
    assert successor.revisit_of_task_id == "TASK-002"
    # Must inherit from the original thread-dispatched root (TASK-1),
    # found by walking the revisit chain: TASK-002 → TASK-1.
    assert successor.dispatched_from_thread_id == "THR-0046"


def test_auto_revisit_non_thread_task_has_no_thread_linkage(tmp_path: Path):
    """Non-thread-dispatched root's auto-revisit successor correctly has
    dispatched_from_thread_id=None."""
    from runtime.infrastructure.database import Database
    from runtime.infrastructure.audit_logger import AuditLogger
    from runtime.models import TaskRecord, TaskStatus
    from runtime.orchestrator.run_step import _maybe_spawn_auto_revisit

    db = Database(tmp_path / "happyranch.db")
    db.insert_task(TaskRecord(
        id="TASK-1", brief="x", team="engineering",
        assigned_agent="manager", status=TaskStatus.FAILED,
    ))
    audit = AuditLogger(db)
    orch = MagicMock()
    orch._db = db
    orch._audit = audit
    orch._queue = MagicMock()
    orch._slug = "acme"

    spawned = _maybe_spawn_auto_revisit(
        orch, "TASK-1", "manager",
        failure_kind="session_timeout",
        error_context={},
    )
    assert spawned is True

    successor = db.get_task("TASK-002")
    assert successor is not None
    assert successor.revisit_of_task_id == "TASK-1"
    assert successor.dispatched_from_thread_id is None


def test_auto_revisit_thread_linkage_preserves_existing_behavior(tmp_path: Path):
    """The existing revisit_of_task_id, auto_revisit_of audit, and
    revisit_spawned audit behavior remain intact when adding thread
    linkage inheritance."""
    from runtime.infrastructure.database import Database
    from runtime.infrastructure.audit_logger import AuditLogger
    from runtime.models import TaskRecord, TaskStatus
    from runtime.orchestrator.run_step import _maybe_spawn_auto_revisit

    db = Database(tmp_path / "happyranch.db")
    db.insert_task(TaskRecord(
        id="TASK-1", brief="x", team="engineering",
        assigned_agent="manager", status=TaskStatus.FAILED,
        dispatched_from_thread_id="THR-0046",
    ))
    audit = AuditLogger(db)
    orch = MagicMock()
    orch._db = db
    orch._audit = audit
    orch._queue = MagicMock()
    orch._slug = "acme"

    spawned = _maybe_spawn_auto_revisit(
        orch, "TASK-1", "manager",
        failure_kind="session_timeout",
        error_context={},
    )
    assert spawned is True

    successor = db.get_task("TASK-002")
    assert successor is not None
    assert successor.parent_task_id is None
    assert successor.revisit_of_task_id == "TASK-1"
    assert successor.dispatched_from_thread_id == "THR-0046"
    assert successor.status == TaskStatus.PENDING
    assert successor.brief == "x"

    # auto_revisit_of is written to the new root.
    successor_rows = db.get_audit_logs("TASK-002")
    successor_actions = [r["action"] for r in successor_rows]
    assert "auto_revisit_of" in successor_actions
    # revisit_spawned is written to the predecessor.
    predecessor_rows = db.get_audit_logs("TASK-1")
    predecessor_actions = [r["action"] for r in predecessor_rows]
    assert "revisit_spawned" in predecessor_actions

    # Queue received an enqueue for the new root.
    orch._queue.put_nowait.assert_called_once_with("acme", "TASK-002")
