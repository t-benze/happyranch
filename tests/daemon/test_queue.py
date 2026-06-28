from __future__ import annotations

import asyncio

import pytest

from runtime.daemon.queue import TaskQueue


def test_enqueue_takes_slug_and_id() -> None:
    q = TaskQueue()
    q.enqueue("alpha", "TASK-001")
    q.enqueue("beta", "TASK-001")
    # Internal state: tuples in the underlying queue
    items = []
    while not q._queue.empty():
        items.append(q._queue.get_nowait())
    assert items == [("alpha", "TASK-001", None), ("beta", "TASK-001", None)]


@pytest.mark.asyncio
async def test_drain_sync_dispatches_per_slug() -> None:
    q = TaskQueue()
    q.enqueue("alpha", "TASK-001")
    q.enqueue("beta", "TASK-002")

    seen: list[tuple[str, str]] = []

    class FakeOrch:
        def run_step(self, slug: str, task_id: str, metadata: dict | None = None) -> None:
            seen.append((slug, task_id))

    await q.drain_sync(FakeOrch())
    assert sorted(seen) == [("alpha", "TASK-001"), ("beta", "TASK-002")]


@pytest.mark.asyncio
async def test_queue_worker_continues_past_individual_run_step_exception():
    from unittest.mock import MagicMock

    q = TaskQueue()
    q.enqueue("org", "T-1")
    q.enqueue("org", "T-2")

    dispatcher = MagicMock()
    dispatcher.run_step = MagicMock(side_effect=[RuntimeError("boom"), None])

    await q.drain_sync(dispatcher)

    assert dispatcher.run_step.call_count == 2


@pytest.mark.asyncio
async def test_queue_start_workers_spawns_n_tasks_and_stop_cancels_them():
    from unittest.mock import MagicMock

    dispatcher = MagicMock()
    dispatcher.run_step = MagicMock()

    q = TaskQueue()
    q.start_workers(dispatcher, n=2)
    assert len(q._worker_tasks) == 2

    await q.stop()
    assert all(t.done() for t in q._worker_tasks)


def test_daemon_state_carries_a_task_queue(daemon_state):
    from runtime.daemon.queue import TaskQueue
    assert isinstance(daemon_state.queue, TaskQueue)


def test_org_state_terminal_event_map_covers_new_statuses():
    """Path B: ESCALATED is intentionally absent (non-terminal — synthesized
    as a task_blocked/outcome=escalated event by _synthesize_terminal_event,
    not via this map). Legacy BLOCKED is likewise absent. CANCELLED replays as
    a failure-class terminal with the precise label in the synthesized event's
    `outcome`. The map moved to OrgState in the multi-org refactor."""
    from runtime.daemon.org_state import OrgState
    from runtime.models import TaskStatus
    assert OrgState._TERMINAL_STATUS_TO_EVENT == {
        TaskStatus.COMPLETED: "task_complete",
        TaskStatus.FAILED: "task_failed",
        # resolved_superseded replays as a completion-class terminal; the
        # precise label rides in the synthesized event's `outcome`.
        TaskStatus.RESOLVED_SUPERSEDED: "task_complete",
        # Path B: cancellation replays as failure-class; outcome="cancelled".
        TaskStatus.CANCELLED: "task_failed",
    }


@pytest.mark.asyncio
async def test_heartbeat_initial_tap_writes_last_heartbeat(daemon_state, org_state):
    """The heartbeat coroutine taps tasks.last_heartbeat synchronously at
    start (before its first sleep), so even short-lived tasks leave a
    non-null marker proving the worker actually picked them up."""
    from runtime.daemon.dispatcher import Dispatcher
    from runtime.daemon.queue import TaskQueue
    from runtime.models import TaskRecord

    org_state.db.insert_task(TaskRecord(id="T-HB", brief="x"))
    dispatcher = Dispatcher(daemon_state)

    hb = asyncio.create_task(TaskQueue._heartbeat(dispatcher, org_state.slug, "T-HB"))
    # The initial tap is synchronous (no await before update_task), so any
    # event-loop yield is enough to let the coroutine run it before sleep(30).
    await asyncio.sleep(0.05)
    hb.cancel()
    try:
        await hb
    except asyncio.CancelledError:
        pass

    task = org_state.db.get_task("T-HB")
    assert task.last_heartbeat is not None


@pytest.mark.asyncio
async def test_worker_loop_stamps_heartbeat_and_cancels_after_run_step(
    daemon_state, org_state,
):
    """End-to-end: enqueue a (slug, task_id), the worker spawns a heartbeat
    alongside run_step, and after run_step returns the heartbeat coroutine has
    been cancelled (no leak) but last_heartbeat was set during the run."""
    import time
    from unittest.mock import MagicMock

    from runtime.daemon.dispatcher import Dispatcher
    from runtime.daemon.queue import TaskQueue
    from runtime.models import TaskRecord

    org_state.db.insert_task(TaskRecord(id="T-HB", brief="x"))

    # Wrap the real Dispatcher: heartbeat() resolves slug→OrgState and writes
    # last_heartbeat as in production. We patch run_step to block briefly so
    # the event loop has time to schedule the heartbeat coroutine.
    real_dispatcher = Dispatcher(daemon_state)
    dispatcher = MagicMock(wraps=real_dispatcher)
    dispatcher.run_step = MagicMock(
        side_effect=lambda slug, task_id, metadata=None: time.sleep(0.1)
    )

    q = TaskQueue()
    q.start_workers(dispatcher, n=1)
    q.enqueue(org_state.slug, "T-HB")
    await asyncio.wait_for(q._queue.join(), timeout=2.0)
    # Give the worker's finally-block a tick to cancel + await the heartbeat.
    await asyncio.sleep(0.05)
    await q.stop()

    task = org_state.db.get_task("T-HB")
    assert task.last_heartbeat is not None


def test_synthesize_terminal_event_rules(org_state):
    """P1 regression: in_progress(delegated) is non-terminal for event purposes —
    the parent resumes when children finish. Only escalated should
    surface as task_blocked to a late subscriber."""
    from runtime.models import BlockKind, TaskRecord, TaskStatus

    def make(task_id: str, status: TaskStatus,
             block_kind: BlockKind | None = None):
        org_state.db.insert_task(TaskRecord(id=task_id, brief="x"))
        org_state.db.update_task(task_id, status=status, block_kind=block_kind)
        return org_state.db.get_task(task_id)

    done = make("T-DONE", TaskStatus.COMPLETED)
    failed = make("T-FAIL", TaskStatus.FAILED)
    delegated = make("T-DEL", TaskStatus.IN_PROGRESS, BlockKind.DELEGATED)
    escalated = make("T-ESC", TaskStatus.ESCALATED)

    assert org_state._synthesize_terminal_event(done)["type"] == "task_complete"
    assert org_state._synthesize_terminal_event(failed)["type"] == "task_failed"
    assert org_state._synthesize_terminal_event(delegated) is None
    esc_event = org_state._synthesize_terminal_event(escalated)
    assert esc_event["type"] == "task_blocked"
    assert esc_event["outcome"] == "escalated"
