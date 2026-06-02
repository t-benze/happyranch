from __future__ import annotations

import asyncio

import pytest

from runtime.daemon.thread_queue import ThreadQueue, ThreadJob


async def test_queue_enqueue_then_get():
    q = ThreadQueue()
    job = ThreadJob(org_slug="hk", invocation_token="abc")
    await q.put(job)
    got = await asyncio.wait_for(q.get(), timeout=1.0)
    assert got.invocation_token == "abc"
    assert got.org_slug == "hk"


async def test_queue_size_reflects_pending():
    q = ThreadQueue()
    assert q.size == 0
    await q.put(ThreadJob(org_slug="hk", invocation_token="a"))
    await q.put(ThreadJob(org_slug="hk", invocation_token="b"))
    assert q.size == 2
