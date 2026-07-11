"""THR-079: daemon-restart pid-liveness probe tests.

Tests that Branch-1 of _sweep_on_startup uses the persisted executor_pid
with os.kill(pid, 0) rather than assuming the subprocess is dead."""
from __future__ import annotations

from pathlib import Path
from unittest import mock

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


def _seed_org_with_orch(
    tmp_path: Path, slug: str = "test",
) -> tuple[Database, Orchestrator, TaskQueue]:
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


# ── (a) Alive pid → task left untouched ────────────────────────────────────

def test_alive_pid_leaves_task_untouched(tmp_path: Path) -> None:
    """When executor_pid is alive (os.kill returns cleanly), the task is
    left in_progress — no FAILED, no spawn."""
    db, orch, queue = _seed_org_with_orch(tmp_path)
    db.insert_task(TaskRecord(
        id="T-1", brief="x", team="engineering",
        assigned_agent="dev_agent",
        status=TaskStatus.IN_PROGRESS,
    ))
    db.update_task("T-1", executor_pid=42)

    with mock.patch("os.kill", return_value=None):  # alive: no exception
        _sweep_on_startup(db, queue, "test", orch)

    t = db.get_task("T-1")
    assert t is not None
    assert t.status == TaskStatus.IN_PROGRESS, (
        f"alive pid should leave task in_progress; got {t.status}"
    )
    # No auto-revisit spawned.
    revisits = [
        rt for rt in (db.get_task(tid)
                       for tid in db.get_nonterminal_task_ids())
        if rt is not None and rt.revisit_of_task_id == "T-1"
    ]
    assert len(revisits) == 0, (
        f"expected 0 auto-revisit twins for alive pid; got {len(revisits)}"
    )


# ── (b) Dead pid (ProcessLookupError) → FAILED ─────────────────────────────

def test_dead_pid_marks_failed(tmp_path: Path) -> None:
    """When executor_pid is dead (ProcessLookupError), the task is FAILED
    with an explicit reason."""
    db, orch, queue = _seed_org_with_orch(tmp_path)
    db.insert_task(TaskRecord(
        id="T-2", brief="x", team="engineering",
        assigned_agent="dev_agent",
        status=TaskStatus.IN_PROGRESS,
    ))
    db.update_task("T-2", executor_pid=99)

    def _kill_raises_process_lookup(pid: int, sig: int) -> None:
        raise ProcessLookupError()

    with mock.patch("os.kill", side_effect=_kill_raises_process_lookup):
        _sweep_on_startup(db, queue, "test", orch)

    t = db.get_task("T-2")
    assert t is not None
    assert t.status == TaskStatus.FAILED, (
        f"dead pid should be FAILED; got {t.status}"
    )
    assert t.note is not None
    assert "executor pid not alive" in t.note, (
        f"expected 'executor pid not alive' in note; got: {t.note}"
    )


# ── (c) NULL pid → FAILED (fail-closed) ────────────────────────────────────

def test_null_pid_marks_failed_fail_closed(tmp_path: Path) -> None:
    """When executor_pid is NULL (pre-migration / not set yet), the task is
    FAILED per fail-closed default."""
    db, orch, queue = _seed_org_with_orch(tmp_path)
    db.insert_task(TaskRecord(
        id="T-3", brief="x", team="engineering",
        assigned_agent="dev_agent",
        status=TaskStatus.IN_PROGRESS,
        executor_pid=None,
    ))

    kill_calls: list[tuple[int, int]] = []
    def _record_kill(pid: int, sig: int) -> None:
        kill_calls.append((pid, sig))

    with mock.patch("os.kill", side_effect=_record_kill):
        _sweep_on_startup(db, queue, "test", orch)

    t = db.get_task("T-3")
    assert t is not None
    assert t.status == TaskStatus.FAILED, (
        f"null pid should be FAILED (fail-closed); got {t.status}"
    )
    assert t.note is not None
    assert "liveness undeterminable" in t.note, (
        f"expected 'liveness undeterminable' in note; got: {t.note}"
    )
    # os.kill should NOT have been called — null pid short-circuits.
    assert kill_calls == [], (
        f"os.kill should not be called for null pid; got {kill_calls}"
    )


# ── (c-2) Indeterminate pid (PermissionError) → FAILED (fail-closed) ──────

def test_permission_error_marks_failed_fail_closed(tmp_path: Path) -> None:
    """When os.kill raises PermissionError (non-ProcessLookupError), the
    task is FAILED per fail-closed default — do NOT leave-alone."""
    db, orch, queue = _seed_org_with_orch(tmp_path)
    db.insert_task(TaskRecord(
        id="T-4", brief="x", team="engineering",
        assigned_agent="dev_agent",
        status=TaskStatus.IN_PROGRESS,
    ))
    db.update_task("T-4", executor_pid=1)  # pid 1 often raises PermissionError

    def _kill_raises_permission(pid: int, sig: int) -> None:
        raise PermissionError()

    with mock.patch("os.kill", side_effect=_kill_raises_permission):
        _sweep_on_startup(db, queue, "test", orch)

    t = db.get_task("T-4")
    assert t is not None
    assert t.status == TaskStatus.FAILED, (
        f"permission-denied pid should be FAILED (fail-closed); got {t.status}"
    )
    assert t.note is not None
    assert "liveness undeterminable" in t.note, (
        f"expected 'liveness undeterminable' in note; got: {t.note}"
    )


# ── (d) No auto-revisit twin spawned ───────────────────────────────────────

def test_no_auto_revisit_spawned_on_restart(tmp_path: Path) -> None:
    """A genuinely dead task (executor_pid present, ProcessLookupError) is
    FAILED but does NOT spawn an auto-revisit twin. The THR-079 ruling
    supersedes the earlier heartbeat/auto-revisit approach."""
    db, orch, queue = _seed_org_with_orch(tmp_path)
    db.insert_task(TaskRecord(
        id="T-ROOT", brief="root work", team="engineering",
        assigned_agent="dev_agent",
        status=TaskStatus.IN_PROGRESS,
        task_type="task",
    ))
    db.update_task("T-ROOT", executor_pid=123)

    def _kill_raises_process_lookup(pid: int, sig: int) -> None:
        raise ProcessLookupError()

    with mock.patch("os.kill", side_effect=_kill_raises_process_lookup):
        _sweep_on_startup(db, queue, "test", orch)

    root = db.get_task("T-ROOT")
    assert root is not None
    assert root.status == TaskStatus.FAILED

    # No auto-revisit twin should have been created.
    revisits = [
        t for t in (db.get_task(tid)
                    for tid in db.get_nonterminal_task_ids())
        if t is not None and t.revisit_of_task_id == "T-ROOT"
    ]
    assert len(revisits) == 0, (
        f"expected 0 auto-revisit twins; got {len(revisits)}"
    )


# ── (e) Migration is idempotent — run twice, assert no-op ──────────────────

def test_executor_pid_migration_idempotent(tmp_path: Path) -> None:
    """Constructing Database twice on the same path should not fail — the
    ADD COLUMN is guarded by try/except OperationalError.

    Also verifies that existing columns are intact after migration.
    """
    db_path = tmp_path / "happyranch.db"
    db1 = Database(db_path)
    # Seed a task before migration to exercise the column on a live row.
    db1.insert_task(TaskRecord(id="T-MIG", brief="migration test"))
    db1.update_task("T-MIG", executor_pid=42)

    # Re-open — migration runs again, should be idempotent.
    db2 = Database(db_path)

    # Existing data intact.
    t = db2.get_task("T-MIG")
    assert t is not None
    assert t.executor_pid == 42, (
        f"executor_pid should survive re-open; got {t.executor_pid}"
    )

    # Column is writable after re-open.
    db2.update_task("T-MIG", executor_pid=99)
    assert db2.get_task("T-MIG").executor_pid == 99

    # Re-open a third time.
    db3 = Database(db_path)
    assert db3.get_task("T-MIG").executor_pid == 99
