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


def test_sweep_uses_unknown_for_missing_assigned_agent(tmp_path: Path):
    """If task.assigned_agent is None, the auto-revisit payload records
    agent='(unknown)' rather than crashing on the None."""
    db, orch, queue = _real_orch(tmp_path)
    # Root manager so cascade has a parent to walk to.
    db.insert_task(TaskRecord(
        id="TASK-ROOT", brief="root", team="engineering",
        assigned_agent="engineering_head",
        status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
    ))
    db.insert_task(TaskRecord(
        id="TASK-2", brief="x", team="engineering",
        assigned_agent=None, parent_task_id="TASK-ROOT",
        status=TaskStatus.IN_PROGRESS,
    ))

    _sweep_on_startup(db, queue, "acme", orch)

    # Auto-revisit row's failed_agent reflects the (unknown) substitute.
    revisits = [
        t for t in (db.get_task(tid)
                    for tid in db.get_nonterminal_task_ids())
        if t is not None and t.revisit_of_task_id == "TASK-ROOT"
    ]
    assert len(revisits) == 1
    ar_rows = db.get_audit_logs(revisits[0].id)
    auto_row = next(r for r in ar_rows if r["action"] == "auto_revisit_of")
    assert auto_row["payload"]["failed_agent"] == "(unknown)"
