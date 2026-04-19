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
