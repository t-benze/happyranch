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
