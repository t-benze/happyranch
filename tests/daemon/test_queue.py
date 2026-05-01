from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest


@pytest.mark.asyncio
async def test_queue_worker_calls_run_step_for_each_enqueued_id():
    from src.daemon.queue import TaskQueue

    orch = MagicMock()
    orch.run_step = MagicMock()

    q = TaskQueue()
    q.enqueue("T-1")
    q.enqueue("T-2")
    q.enqueue("T-3")

    # Run one drain cycle and stop
    await q.drain_sync(orch)

    calls = [c.args[0] for c in orch.run_step.call_args_list]
    assert calls == ["T-1", "T-2", "T-3"]


@pytest.mark.asyncio
async def test_queue_worker_continues_past_individual_run_step_exception():
    from src.daemon.queue import TaskQueue

    orch = MagicMock()
    orch.run_step = MagicMock(side_effect=[RuntimeError("boom"), None])

    q = TaskQueue()
    q.enqueue("T-1")
    q.enqueue("T-2")
    await q.drain_sync(orch)

    assert orch.run_step.call_count == 2


@pytest.mark.asyncio
async def test_queue_start_workers_spawns_n_tasks_and_stop_cancels_them():
    from src.daemon.queue import TaskQueue

    orch = MagicMock()
    orch.run_step = MagicMock()

    q = TaskQueue()
    q.start_workers(orch, n=2)
    assert len(q._worker_tasks) == 2

    await q.stop()
    assert all(t.done() for t in q._worker_tasks)


def test_daemon_state_carries_a_task_queue(tmp_path):
    from src.config import Settings
    from src.daemon.state import DaemonState
    from src.daemon.queue import TaskQueue
    from src.runtime import RuntimeDir
    rt = RuntimeDir.init(tmp_path / "rt", slug="test")
    state = DaemonState.from_runtime(rt, Settings())
    assert isinstance(state.queue, TaskQueue)


def test_daemon_state_terminal_event_map_covers_new_statuses(tmp_path):
    """BLOCKED is intentionally absent — block_kind decides whether it reads
    as terminal for a late subscriber (ESCALATED yes, DELEGATED no)."""
    from src.daemon.state import DaemonState
    from src.models import TaskStatus
    assert DaemonState._TERMINAL_STATUS_TO_EVENT == {
        TaskStatus.COMPLETED: "task_complete",
        TaskStatus.FAILED: "task_failed",
    }


@pytest.mark.asyncio
async def test_heartbeat_initial_tap_writes_last_heartbeat(tmp_path):
    """The heartbeat coroutine taps tasks.last_heartbeat synchronously at
    start (before its first sleep), so even short-lived tasks leave a
    non-null marker proving the worker actually picked them up."""
    from src.config import Settings
    from src.daemon.queue import TaskQueue
    from src.daemon.state import DaemonState
    from src.models import TaskRecord
    from src.runtime import RuntimeDir

    rt = RuntimeDir.init(tmp_path / "rt", slug="test")
    state = DaemonState.from_runtime(rt, Settings())
    state.db.insert_task(TaskRecord(id="T-HB", brief="x"))

    orch = MagicMock()
    orch.db = state.db

    hb = asyncio.create_task(TaskQueue._heartbeat(orch, "T-HB"))
    # The initial tap is synchronous (no await before update_task), so any
    # event-loop yield is enough to let the coroutine run it before sleep(30).
    await asyncio.sleep(0.05)
    hb.cancel()
    try:
        await hb
    except asyncio.CancelledError:
        pass

    task = state.db.get_task("T-HB")
    assert task.last_heartbeat is not None


@pytest.mark.asyncio
async def test_worker_loop_stamps_heartbeat_and_cancels_after_run_step(tmp_path):
    """End-to-end: enqueue a task, the worker spawns a heartbeat alongside
    run_step, and after run_step returns the heartbeat coroutine has been
    cancelled (no leak) but last_heartbeat was set during the run."""
    import time

    from src.config import Settings
    from src.daemon.queue import TaskQueue
    from src.daemon.state import DaemonState
    from src.models import TaskRecord
    from src.runtime import RuntimeDir

    rt = RuntimeDir.init(tmp_path / "rt", slug="test")
    state = DaemonState.from_runtime(rt, Settings())
    state.db.insert_task(TaskRecord(id="T-HB", brief="x"))

    orch = MagicMock()
    orch.db = state.db
    # Block for 100ms in the executor thread so the event loop has time to
    # schedule the heartbeat coroutine and run its initial tap.
    orch.run_step = MagicMock(side_effect=lambda task_id: time.sleep(0.1))

    q = TaskQueue()
    q.start_workers(orch, n=1)
    q.enqueue("T-HB")
    await asyncio.wait_for(q._queue.join(), timeout=2.0)
    # Give the worker's finally-block a tick to cancel + await the heartbeat.
    await asyncio.sleep(0.05)
    await q.stop()

    task = state.db.get_task("T-HB")
    assert task.last_heartbeat is not None


def test_synthesize_terminal_event_rules(tmp_path):
    """P1 regression: BLOCKED(DELEGATED) is non-terminal for event purposes —
    the parent resumes when children finish. Only BLOCKED(ESCALATED) should
    surface as task_blocked to a late subscriber."""
    from src.config import Settings
    from src.daemon.state import DaemonState
    from src.models import BlockKind, TaskRecord, TaskStatus
    from src.runtime import RuntimeDir
    rt = RuntimeDir.init(tmp_path / "rt", slug="test")
    state = DaemonState.from_runtime(rt, Settings())

    def make(task_id: str, status: TaskStatus,
             block_kind: BlockKind | None = None):
        state.db.insert_task(TaskRecord(id=task_id, brief="x"))
        state.db.update_task(task_id, status=status, block_kind=block_kind)
        return state.db.get_task(task_id)

    done = make("T-DONE", TaskStatus.COMPLETED)
    failed = make("T-FAIL", TaskStatus.FAILED)
    delegated = make("T-DEL", TaskStatus.BLOCKED, BlockKind.DELEGATED)
    escalated = make("T-ESC", TaskStatus.BLOCKED, BlockKind.ESCALATED)

    assert state._synthesize_terminal_event(done)["type"] == "task_complete"
    assert state._synthesize_terminal_event(failed)["type"] == "task_failed"
    assert state._synthesize_terminal_event(delegated) is None
    esc_event = state._synthesize_terminal_event(escalated)
    assert esc_event["type"] == "task_blocked"
    assert esc_event["outcome"] == "escalated"
