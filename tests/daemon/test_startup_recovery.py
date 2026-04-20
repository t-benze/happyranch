from __future__ import annotations

from pathlib import Path

from src.config import Settings
from src.daemon.__main__ import _sweep_on_startup
from src.daemon.queue import TaskQueue
from src.infrastructure.database import Database
from src.models import BlockKind, TaskRecord, TaskStatus, TaskType
from src.runtime import RuntimeDir


def test_sweep_in_progress_to_failed(tmp_path: Path) -> None:
    runtime = RuntimeDir.init(tmp_path / "rt")
    db = Database(runtime.db_path)
    db.insert_task(TaskRecord(id="T-1", type=TaskType.GENERAL, brief="x"))
    db.update_task("T-1", status=TaskStatus.IN_PROGRESS)

    _sweep_on_startup(db, TaskQueue())

    t = db.get_task("T-1")
    assert t.status == TaskStatus.FAILED
    assert t.note and "daemon restart" in t.note


def test_sweep_blocked_delegated_with_all_children_terminal_reenqueues(tmp_path):
    runtime = RuntimeDir.init(tmp_path / "rt")
    db = Database(runtime.db_path)
    # Parent blocked(DELEGATED), child completed — lost the wake-up signal
    # to the daemon crash.
    db.insert_task(TaskRecord(id="T-PAR", type=TaskType.GENERAL, brief="p"))
    db.update_task("T-PAR", status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(id="T-CHD", type=TaskType.GENERAL,
                              brief="c", parent_task_id="T-PAR"))
    db.update_task("T-CHD", status=TaskStatus.COMPLETED, note="done")

    queue = TaskQueue()
    _sweep_on_startup(db, queue)

    assert queue._queue.get_nowait() == "T-PAR"


def test_sweep_blocked_delegated_with_live_children_does_not_reenqueue(tmp_path):
    runtime = RuntimeDir.init(tmp_path / "rt")
    db = Database(runtime.db_path)
    db.insert_task(TaskRecord(id="T-PAR", type=TaskType.GENERAL, brief="p"))
    db.update_task("T-PAR", status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(id="T-CHD", type=TaskType.GENERAL,
                              brief="c", parent_task_id="T-PAR"))
    # Child was in progress at crash — the sweep will fail it, which in
    # turn should re-enqueue the parent. So after full sweep, parent IS
    # enqueued, but via the child's failure, not its own blocked row.
    db.update_task("T-CHD", status=TaskStatus.IN_PROGRESS)

    queue = TaskQueue()
    _sweep_on_startup(db, queue)

    # T-CHD was in_progress → swept to failed → parent enqueued
    assert db.get_task("T-CHD").status == TaskStatus.FAILED
    assert queue._queue.get_nowait() == "T-PAR"


def test_sweep_leaves_blocked_escalated_alone(tmp_path):
    runtime = RuntimeDir.init(tmp_path / "rt")
    db = Database(runtime.db_path)
    db.insert_task(TaskRecord(id="T-1", type=TaskType.GENERAL, brief="x"))
    db.update_task("T-1", status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.ESCALATED, note="halt")

    queue = TaskQueue()
    _sweep_on_startup(db, queue)

    t = db.get_task("T-1")
    assert t.status == TaskStatus.BLOCKED
    assert t.block_kind == BlockKind.ESCALATED
    assert queue._queue.empty()


def test_sweep_pending_stays_pending_but_gets_enqueued(tmp_path):
    """Pending rows from before the crash need a nudge — their original
    POST /tasks enqueue was lost when the daemon died."""
    runtime = RuntimeDir.init(tmp_path / "rt")
    db = Database(runtime.db_path)
    db.insert_task(TaskRecord(id="T-1", type=TaskType.GENERAL, brief="x"))

    queue = TaskQueue()
    _sweep_on_startup(db, queue)

    assert db.get_task("T-1").status == TaskStatus.PENDING
    assert queue._queue.get_nowait() == "T-1"
