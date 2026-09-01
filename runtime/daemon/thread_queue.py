"""Async queue + job payload for thread invocations.

Each ThreadJob points to a thread_invocations row by `invocation_token`.
A worker pool consumes jobs and hands them to ThreadInvocationRunner.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from runtime.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class ThreadJob:
    org_slug: str
    invocation_token: str


class ThreadQueue:
    def __init__(self) -> None:
        self._q: asyncio.Queue[ThreadJob] = asyncio.Queue()
        self._published_once_tokens: set[str] = set()

    async def put(self, job: ThreadJob) -> None:
        await self._q.put(job)

    async def put_once(self, job: ThreadJob) -> None:
        # The queue is unbounded, so put_nowait cannot suspend between enqueue
        # and the ownership mark. Ownership spans queued + in-flight delivery;
        # the worker releases it after either acknowledgement or failure. The
        # durable invocation claim remains the exactly-once launch authority.
        if job.invocation_token in self._published_once_tokens:
            return
        self._q.put_nowait(job)
        self._published_once_tokens.add(job.invocation_token)

    async def get(self) -> ThreadJob:
        return await self._q.get()

    def acknowledge_once(self, job: ThreadJob) -> None:
        """Finish queue-owned delivery after the consumer returns normally."""
        self._published_once_tokens.discard(job.invocation_token)

    def release_once(self, job: ThreadJob) -> None:
        """Release failed in-flight delivery so a durable producer can retry."""
        self._published_once_tokens.discard(job.invocation_token)

    @property
    def size(self) -> int:
        return self._q.qsize()


async def thread_worker_loop(state, settings: Settings) -> None:
    """Single worker that drains ThreadJobs across all orgs.

    Round-robins across orgs because each org has its own asyncio.Queue.
    Multiple workers can run in parallel; each picks the next available job.
    """
    from runtime.daemon.thread_runner import run_invocation

    while True:
        all_orgs = list(state.orgs.values())
        if not all_orgs:
            await asyncio.sleep(0.5)
            continue
        for org in all_orgs:
            if org.thread_queue.size == 0:
                continue
            try:
                job = await asyncio.wait_for(org.thread_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            try:
                await run_invocation(
                    org_state=org,
                    invocation_token=job.invocation_token,
                    settings=settings,
                    host_supervisor=state.host_supervisor,
                )
            except asyncio.CancelledError:
                org.thread_queue.release_once(job)
                raise
            except Exception:
                org.thread_queue.release_once(job)
                logger.exception(
                    "thread_worker_loop: invocation %s crashed",
                    job.invocation_token[:8],
                )
            else:
                org.thread_queue.acknowledge_once(job)
        await asyncio.sleep(0.05)
