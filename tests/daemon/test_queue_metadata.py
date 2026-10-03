from __future__ import annotations

import asyncio

import pytest

from runtime.daemon.queue import TaskQueue


class _RecordingDispatcher:
    def __init__(self):
        self.calls: list[tuple] = []

    def run_step(self, slug: str, task_id: str, metadata: dict | None = None) -> None:
        self.calls.append(("run_step", slug, task_id, metadata))

    def heartbeat(self, slug: str, task_id: str) -> None:
        pass


@pytest.mark.asyncio
async def test_enqueue_carries_metadata_to_run_step():
    q = TaskQueue()
    disp = _RecordingDispatcher()
    q.start_workers(disp, n=1)
    try:
        q.enqueue("org-a", "TASK-1", metadata={"trigger": "job_terminal",
                                                "triggering_job_id": "JOB-5"})
        for _ in range(50):
            if disp.calls:
                break
            await asyncio.sleep(0.01)
        assert disp.calls == [("run_step", "org-a", "TASK-1",
                              {"trigger": "job_terminal",
                               "triggering_job_id": "JOB-5"})]
    finally:
        await q.stop()


@pytest.mark.asyncio
async def test_enqueue_without_metadata_passes_none():
    q = TaskQueue()
    disp = _RecordingDispatcher()
    q.start_workers(disp, n=1)
    try:
        q.enqueue("org-a", "TASK-2")
        for _ in range(50):
            if disp.calls:
                break
            await asyncio.sleep(0.01)
        assert disp.calls == [("run_step", "org-a", "TASK-2", None)]
    finally:
        await q.stop()


@pytest.mark.asyncio
async def test_v2_generation_metadata_is_preserved_verbatim():
    """THR-229 C3d3b: the tagged generation token and diagnostic P survive the
    queue -> Dispatcher.run_step handoff unchanged, so a stale/absent token is
    never rewritten at consumption and only G is admission authority."""
    q = TaskQueue()
    disp = _RecordingDispatcher()
    q.start_workers(disp, n=1)
    metadata = {
        "authority_v2_generation": "APV2N-" + "a" * 64,
        "publication_attempt": 3,
    }
    try:
        q.enqueue("org-a", "TASK-3", metadata=dict(metadata))
        for _ in range(50):
            if disp.calls:
                break
            await asyncio.sleep(0.01)
        assert disp.calls == [("run_step", "org-a", "TASK-3", metadata)]
        assert disp.calls[0][3] is not metadata  # enqueue copies the dict
    finally:
        await q.stop()


@pytest.mark.asyncio
async def test_drain_sync_carries_v2_generation_metadata():
    q = TaskQueue()
    disp = _RecordingDispatcher()
    metadata = {"authority_v2_generation": "APV2N-" + "b" * 64}
    q.enqueue("org-a", "TASK-4", metadata=dict(metadata))
    await q.drain_sync(disp)
    assert disp.calls == [("run_step", "org-a", "TASK-4", metadata)]
