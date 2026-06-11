"""Async queue for working-hours wake invocations.

Skeleton mirroring ``dream_queue``: the ``WakeJob`` payload, the unbounded
``WakeQueue`` with the synchronous ``put_nowait`` escape hatch (see
``DreamQueue`` and learning LRN-005 — never use ``asyncio.run`` to enqueue from
a no-loop context), and the ``size`` accessor. The ``wake_worker_loop`` that
drains it into ``WakeRunner`` is wired in leg B.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass


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
