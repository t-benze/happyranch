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
        self._owned: set[tuple[str, str]] = set()

    async def put(self, job: DreamJob) -> None:
        self._put_once(job)

    async def put_once(self, job: DreamJob) -> str:
        """Positive insertion/SAME original owner acknowledgement on the real loop."""
        return self._put_once(job)

    def _put_once(self, job: DreamJob) -> str:
        key = (job.org_slug, job.dream_id)
        if key in self._owned:
            return "already_pending"
        # No suspension: a failed insertion cannot manufacture ownership.
        self._q.put_nowait(job)
        self._owned.add(key)
        return "inserted"

    def put_nowait(self, job: DreamJob) -> None:
        """Keep ordinary synchronous dedup; native startup uses real-loop put_once."""
        self._put_once(job)

    def release(self, job):
        self._owned.discard((job.org_slug, job.dream_id))

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
                await run_dream(
                    org_state=org, dream_id=job.dream_id, settings=settings,
                    host_supervisor=state.host_supervisor,
                )
            except Exception:
                logger.exception("dream_worker_loop: dream %s crashed", job.dream_id)
            finally:
                org.dream_queue.release(job)
        await asyncio.sleep(0.05)
