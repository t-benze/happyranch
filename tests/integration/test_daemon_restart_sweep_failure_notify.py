from __future__ import annotations

from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.daemon.__main__ import _sweep_on_startup
from runtime.daemon.queue import TaskQueue
from runtime.infrastructure.database import Database
from runtime.models import BlockKind, TaskRecord, TaskStatus
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.orchestrator import Orchestrator
from runtime.orchestrator.teams import TeamsRegistry
from runtime.runtime import RuntimeDir


pytestmark = pytest.mark.integration


def _real_orch(tmp_path: Path, slug: str = "acme") -> tuple[Database, Orchestrator, TaskQueue]:
    """Construct a sweep-ready org with real Orchestrator + queue."""
    runtime = RuntimeDir.init(tmp_path / "rt")
    paths = OrgPaths(root=runtime.orgs_dir / slug)
    paths.teams_config_path.parent.mkdir(parents=True, exist_ok=True)
    paths.teams_config_path.write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: engineering_head\n"
        "    workers: [dev_agent]\n"
    )
    db = Database(paths.db_path)
    queue = TaskQueue()
    orch = Orchestrator(
        db=db, settings=Settings(), paths=paths, slug=slug,
        teams=TeamsRegistry.load(paths.root),
    )
    orch._queue = queue
    return db, orch, queue


def test_sweep_writes_daemon_restart_failure_not_escalation(tmp_path: Path):
    """Daemon-restart sweep must use the daemon_restart_failure audit action,
    never the escalation action — APPROVE/REJECT don't make sense for
    sweep-killed tasks."""
    db, orch, queue = _real_orch(tmp_path)
    db.insert_task(TaskRecord(
        id="TASK-1", brief="x", team="engineering",
        assigned_agent="dev_agent", status=TaskStatus.IN_PROGRESS,
    ))

    _sweep_on_startup(db, queue, "acme", orch)

    task = db.get_task("TASK-1")
    assert task.status == TaskStatus.FAILED

    actions = [r["action"] for r in db.get_audit_logs("TASK-1")]
    assert "daemon_restart_failure" in actions, (
        f"expected 'daemon_restart_failure' audit row; got: {actions}"
    )
    assert "escalation" not in actions, (
        f"'escalation' must not appear for a daemon-restart failure; "
        f"got: {actions}"
    )


def test_sweep_null_assigned_agent_fails_with_liveness_note(tmp_path: Path):
    """THR-079: if task.assigned_agent is None, the sweep still fails the
    task with a liveness-undeterminable note. No crash, no auto-revisit."""
    db, orch, queue = _real_orch(tmp_path)
    # A worker root with no parent and no block_kind — genuine root-level death.
    db.insert_task(TaskRecord(
        id="TASK-2", brief="x", team="engineering",
        assigned_agent=None,
        status=TaskStatus.IN_PROGRESS,
    ))

    _sweep_on_startup(db, queue, "acme", orch)

    t = db.get_task("TASK-2")
    assert t.status == TaskStatus.FAILED
    assert t.note is not None and "liveness undeterminable" in t.note

    # THR-079: NO auto-revisit twin.
    revisits = [
        t for t in (db.get_task(tid)
                    for tid in db.get_nonterminal_task_ids())
        if t is not None and t.revisit_of_task_id == "TASK-2"
    ]
    assert len(revisits) == 0


def test_sweep_consumes_orphaned_task_result_done(tmp_path: Path):
    """THR-090 Track A: when an in_progress task has a dead executor_pid
    AND an unconsumed task_result row, the boot sweep must consume the
    result (honor the completion) instead of marking it FAILED.

    Scenario: root task completed cleanly (decision='done'), daemon died
    before consuming. The sweep should transition it to COMPLETED."""
    db, orch, queue = _real_orch(tmp_path)

    # Insert an in_progress root task with NO parent.
    db.insert_task(TaskRecord(
        id="TASK-Z1", brief="do work", team="engineering",
        assigned_agent="dev_agent", status=TaskStatus.IN_PROGRESS,
        task_type="task",
    ))

    # Insert an orphaned task_result row — the completion callback landed
    # but the daemon died before the orchestrator consumed it.
    db.insert_task_result(
        task_id="TASK-Z1",
        agent="dev_agent",
        session_id="sess-orphan-1",
        status="completed",
        output_summary="All done successfully",
        confidence_score=95,
        decision_json='{"action": "done", "summary": "All done successfully"}',
    )

    _sweep_on_startup(db, queue, "acme", orch)

    t = db.get_task("TASK-Z1")
    assert t.status == TaskStatus.COMPLETED, (
        f"expected COMPLETED (orphaned result consumed), got {t.status}"
    )
    assert t.note == "All done successfully"

    # Must NOT have a daemon_restart_failure audit row.
    actions = [r["action"] for r in db.get_audit_logs("TASK-Z1")]
    assert "daemon_restart_failure" not in actions, (
        f"orphaned result should be consumed, not failed; got actions: {actions}"
    )


def test_sweep_consumes_orphaned_task_result_wakes_parent(tmp_path: Path):
    """THR-090 Track A: consuming an orphaned result must also wake a
    waiting parent via _enqueue_parent_if_waiting."""
    db, orch, queue = _real_orch(tmp_path)

    # Spy on queue.put_nowait to verify the parent was enqueued.
    enqueued: list[str] = []
    _orig_put = queue.put_nowait
    def _spy_put(slug: str, task_id: str, *, metadata=None):
        enqueued.append(task_id)
        _orig_put(slug, task_id, metadata=metadata)
    queue.put_nowait = _spy_put  # type: ignore[method-assign]

    # Insert a parent task parked in in_progress(delegated) waiting on its child.
    db.insert_task(TaskRecord(
        id="TASK-PARENT", brief="manage work", team="engineering",
        assigned_agent="engineering_head",
        status=TaskStatus.IN_PROGRESS,
        block_kind=BlockKind.DELEGATED,
        task_type="task",
    ))

    # Insert the child subtask — in_progress with dead pid.
    db.insert_task(TaskRecord(
        id="TASK-CHILD", brief="do sub work", team="engineering",
        assigned_agent="dev_agent",
        parent_task_id="TASK-PARENT",
        status=TaskStatus.IN_PROGRESS,
        task_type="subtask",
    ))

    # Insert an orphaned task_result for the child.
    db.insert_task_result(
        task_id="TASK-CHILD",
        agent="dev_agent",
        session_id="sess-orphan-2",
        status="completed",
        output_summary="Sub work done",
        confidence_score=90,
    )

    _sweep_on_startup(db, queue, "acme", orch)

    child = db.get_task("TASK-CHILD")
    assert child.status == TaskStatus.COMPLETED, (
        f"expected child COMPLETED, got {child.status}"
    )

    # Parent should be enqueued (woken up).
    assert "TASK-PARENT" in enqueued, (
        f"parent should be enqueued after child completion; enqueued: {enqueued}"
    )


def test_sweep_orphaned_result_absent_falls_through_to_failed(tmp_path: Path):
    """When there is NO orphaned task_result, the existing FAILED path
    must still work unchanged."""
    db, orch, queue = _real_orch(tmp_path)

    db.insert_task(TaskRecord(
        id="TASK-Z2", brief="x", team="engineering",
        assigned_agent="dev_agent", status=TaskStatus.IN_PROGRESS,
    ))

    _sweep_on_startup(db, queue, "acme", orch)

    t = db.get_task("TASK-Z2")
    assert t.status == TaskStatus.FAILED

    actions = [r["action"] for r in db.get_audit_logs("TASK-Z2")]
    assert "daemon_restart_failure" in actions
