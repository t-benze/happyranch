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
        self._owned: set[tuple[str, str]] = set()

    async def put(self, job: WakeJob) -> None:
        self._put_once(job)

    async def put_once(self, job: WakeJob) -> str:
        """Positive insertion/SAME original owner acknowledgement on the real loop."""
        return self._put_once(job)

    def _put_once(self, job: WakeJob) -> str:
        key = (job.org_slug, job.work_hour_id)
        if key in self._owned:
            return "already_pending"
        # No suspension: a failed insertion cannot manufacture ownership.
        self._q.put_nowait(job)
        self._owned.add(key)
        return "inserted"

    def put_nowait(self, job: WakeJob) -> None:
        """Keep ordinary synchronous dedup; native startup uses real-loop put_once."""
        self._put_once(job)

    def release(self, job):
        self._owned.discard((job.org_slug, job.work_hour_id))

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
                await run_wake(
                    org_state=org, work_hour_id=job.work_hour_id, settings=settings,
                    host_supervisor=state.host_supervisor,
                )
            except Exception:
                logger.exception("wake_worker_loop: wake %s crashed", job.work_hour_id)
            finally:
                org.wake_queue.release(job)
        await asyncio.sleep(0.05)
