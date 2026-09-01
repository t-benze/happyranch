import asyncio
from types import SimpleNamespace

import pytest

from runtime.daemon.thread_breaker_scheduler import thread_breaker_scheduler_loop


@pytest.mark.asyncio
async def test_shipping_scheduler_enqueues_each_recovered_probe_once(monkeypatch):
    entries = [SimpleNamespace(invocation_token="probe-token")]

    class _Db:
        calls = 0

        def mint_due_thread_reply_breaker_probes(self):
            self.calls += 1
            return entries if self.calls == 1 else []

    class _Queue:
        def __init__(self):
            self.items = []

        async def put(self, item):
            self.items.append(item)

    org = SimpleNamespace(slug="happyranch", db=_Db(), thread_queue=_Queue())
    state = SimpleNamespace(orgs={"happyranch": org})

    sleeps = 0

    async def _sleep(_interval):
        nonlocal sleeps
        sleeps += 1
        if sleeps == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    with pytest.raises(asyncio.CancelledError):
        await thread_breaker_scheduler_loop(state, interval_seconds=0)

    assert org.db.calls == 2
    assert [item.invocation_token for item in org.thread_queue.items] == ["probe-token"]
