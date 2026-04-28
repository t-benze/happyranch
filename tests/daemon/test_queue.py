from __future__ import annotations

import asyncio

import pytest

from src.daemon.queue import TaskQueue


def test_enqueue_takes_slug_and_id() -> None:
    q = TaskQueue()
    q.enqueue("alpha", "TASK-001")
    q.enqueue("beta", "TASK-001")
    # Internal state: tuples in the underlying queue
    items = []
    while not q._queue.empty():
        items.append(q._queue.get_nowait())
    assert items == [("alpha", "TASK-001"), ("beta", "TASK-001")]


@pytest.mark.asyncio
async def test_drain_sync_dispatches_per_slug() -> None:
    q = TaskQueue()
    q.enqueue("alpha", "TASK-001")
    q.enqueue("beta", "TASK-002")

    seen: list[tuple[str, str]] = []

    class FakeOrch:
        def run_step(self, slug: str, task_id: str) -> None:
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
