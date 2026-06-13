"""Async queue for working-hours wake invocations.

Mirrors ``dream_queue``: the ``WakeJob`` payload, the unbounded ``WakeQueue``
with the synchronous ``put_nowait`` escape hatch (see ``DreamQueue`` and learning
LRN-005 — never use ``asyncio.run`` to enqueue from a no-loop context), the
``size`` accessor, and the ``wake_worker_loop`` that drains it into ``run_wake``.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from runtime.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WakeJob:
    org_slug: str
    work_hour_id: str


class WakeQueue:
    def __init__(self) -> None:
        self._q: asyncio.Queue[WakeJob] = asyncio.Queue()

    async def put(self, job: WakeJob) -> None:
        await self._q.put(job)

    def put_nowait(self, job: WakeJob) -> None:
        """Enqueue without a running event loop. Safe because the queue is
        unbounded (never raises QueueFull); used by the synchronous scheduling
        path so it never has to spin up/tear down a process-global loop."""
        self._q.put_nowait(job)

    async def get(self) -> WakeJob:
        return await self._q.get()

    @property
    def size(self) -> int:
        return self._q.qsize()


async def wake_worker_loop(state, settings: Settings) -> None:
    from runtime.daemon.wake_runner import run_wake

    while True:
        for org in list(state.orgs.values()):
            if org.wake_queue.size == 0:
                continue
            try:
                job = await asyncio.wait_for(org.wake_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            try:
                await run_wake(org_state=org, work_hour_id=job.work_hour_id, settings=settings)
            except Exception:
                logger.exception("wake_worker_loop: wake %s crashed", job.work_hour_id)
        await asyncio.sleep(0.05)
