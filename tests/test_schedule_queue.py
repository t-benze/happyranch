"""THR-105 Phase 3: TDD tests for schedule queue — ScheduleJob, ScheduleQueue,
and schedule_worker_loop behavior.
"""
from __future__ import annotations

import asyncio

import pytest

from runtime.daemon.schedule_queue import (
    ScheduleJob,
    ScheduleQueue,
    schedule_worker_loop,
)


def test_schedule_job_equality():
    j1 = ScheduleJob(org_slug="test-org", schedule_id="SCHEDULE-001")
    j2 = ScheduleJob(org_slug="test-org", schedule_id="SCHEDULE-001")
    j3 = ScheduleJob(org_slug="test-org", schedule_id="SCHEDULE-002")
    assert j1 == j2
    assert j1 != j3
    assert hash(j1) == hash(j2)


def test_schedule_job_frozen():
    j = ScheduleJob(org_slug="test-org", schedule_id="SCHEDULE-001")
    with pytest.raises(Exception):
        j.schedule_id = "SCHEDULE-002"  # type: ignore[misc]


def test_queue_put_and_get():
    q = ScheduleQueue()
    assert q.size == 0
    q.put_nowait(ScheduleJob(org_slug="test-org", schedule_id="SCHEDULE-001"))
    assert q.size == 1


@pytest.mark.asyncio
async def test_queue_async_put_get():
    q = ScheduleQueue()
    await q.put(ScheduleJob(org_slug="test-org", schedule_id="SCHEDULE-001"))
    assert q.size == 1
    job = await q.get()
    assert job.org_slug == "test-org"
    assert job.schedule_id == "SCHEDULE-001"
    assert q.size == 0


def test_queue_put_nowait_multiple():
    q = ScheduleQueue()
    for i in range(5):
        q.put_nowait(ScheduleJob(org_slug="test-org", schedule_id=f"SCHEDULE-{i:03d}"))
    assert q.size == 5
    # FIFO ordering check via async
    jobs = []
    import asyncio
    async def drain():
        for _ in range(5):
            jobs.append(await q.get())
    asyncio.run(drain())
    assert [j.schedule_id for j in jobs] == [f"SCHEDULE-{i:03d}" for i in range(5)]
