"""Async queue for schedule fire invocations.

Mirrors ``wake_queue``: the ``ScheduleJob`` payload, the unbounded ``ScheduleQueue``
with the synchronous ``put_nowait`` escape hatch, the ``size`` accessor, and the
``schedule_worker_loop`` that drains it into ``run_schedule``.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from runtime.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScheduleJob:
    org_slug: str
    schedule_id: str


class ScheduleQueue:
    def __init__(self) -> None:
        self._q: asyncio.Queue[ScheduleJob] = asyncio.Queue()

    async def put(self, job: ScheduleJob) -> None:
        await self._q.put(job)

    def put_nowait(self, job: ScheduleJob) -> None:
        """Enqueue without a running event loop. Safe because the queue is
        unbounded (never raises QueueFull); used by the synchronous scheduling
        path so it never has to spin up/tear down a process-global loop."""
        self._q.put_nowait(job)

    async def get(self) -> ScheduleJob:
        return await self._q.get()

    @property
    def size(self) -> int:
        return self._q.qsize()


async def schedule_worker_loop(state, settings: Settings) -> None:
    from runtime.daemon.schedule_runner import run_schedule

    while True:
        for org in list(state.orgs.values()):
            if org.schedule_queue.size == 0:
                continue
            try:
                job = await asyncio.wait_for(org.schedule_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            try:
                await run_schedule(
                    org_state=org,
                    schedule_id=job.schedule_id,
                    settings=settings,
                )
            except Exception:
                logger.exception(
                    "schedule_worker_loop: schedule %s crashed", job.schedule_id,
                )
        await asyncio.sleep(0.05)
