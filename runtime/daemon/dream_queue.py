"""Async queue for dream invocations."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from runtime.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DreamJob:
    org_slug: str
    dream_id: str


class DreamQueue:
    def __init__(self) -> None:
        self._q: asyncio.Queue[DreamJob] = asyncio.Queue()

    async def put(self, job: DreamJob) -> None:
        await self._q.put(job)

    def put_nowait(self, job: DreamJob) -> None:
        """Enqueue without a running event loop. Safe because the queue is
        unbounded (never raises QueueFull); used by the synchronous scheduling
        path so it never has to spin up/tear down a process-global loop."""
        self._q.put_nowait(job)

    async def get(self) -> DreamJob:
        return await self._q.get()

    @property
    def size(self) -> int:
        return self._q.qsize()


async def dream_worker_loop(state, settings: Settings) -> None:
    from runtime.daemon.dream_runner import run_dream

    while True:
        for org in list(state.orgs.values()):
            if org.dream_queue.size == 0:
                continue
            try:
                job = await asyncio.wait_for(org.dream_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            try:
                await run_dream(org_state=org, dream_id=job.dream_id, settings=settings)
            except Exception:
                logger.exception("dream_worker_loop: dream %s crashed", job.dream_id)
        await asyncio.sleep(0.05)
