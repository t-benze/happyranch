"""Async queue + job payload for thread invocations.

Each ThreadJob points to a thread_invocations row by `invocation_token`.
A worker pool consumes jobs and hands them to ThreadInvocationRunner.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class ThreadJob:
    org_slug: str
    invocation_token: str


class ThreadQueue:
    def __init__(self) -> None:
        self._q: asyncio.Queue[ThreadJob] = asyncio.Queue()

    async def put(self, job: ThreadJob) -> None:
        await self._q.put(job)

    async def get(self) -> ThreadJob:
        return await self._q.get()

    @property
    def size(self) -> int:
        return self._q.qsize()
