import asyncio
import inspect
from types import SimpleNamespace

import pytest

from runtime.daemon.thread_breaker_scheduler import thread_breaker_scheduler_loop


def test_app_lifespan_owns_breaker_scheduler_start_and_cancel():
    from runtime.daemon import app

    source = inspect.getsource(app._lifespan)
    assert "thread_breaker_scheduler_loop(state)" in source
    assert "thread_breaker_scheduler_task.cancel()" in source


@pytest.mark.asyncio
async def test_shipping_scheduler_enqueues_each_recovered_probe_once(monkeypatch):
    entries = [SimpleNamespace(invocation_token="probe-token")]

    class _Db:
        calls = 0

        def list_reply_delivery_states(self):
            return []

        def mint_due_thread_reply_breaker_probes(self, **kwargs):
            self.calls += 1
            return entries if self.calls == 1 else []

    class _Queue:
        def __init__(self):
            self.items = []

        async def put(self, item):
            self.items.append(item)

    org = SimpleNamespace(slug="happyranch", db=_Db(), thread_queue=_Queue())
    org.root = None
    state = SimpleNamespace(
        orgs={"happyranch": org},
        settings=SimpleNamespace(thread_reply_breaker_cooldown_seconds=900),
    )

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


@pytest.mark.asyncio
async def test_scheduler_failure_rearms_on_next_tick(monkeypatch):
    class _Db:
        calls = 0

        def list_reply_delivery_states(self):
            return []

        def mint_due_thread_reply_breaker_probes(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("transient store failure")
            return [SimpleNamespace(invocation_token="rearmed-probe")]

    class _Queue:
        items = []

        async def put(self, item):
            self.items.append(item)

    db = _Db()
    org = SimpleNamespace(slug="happyranch", db=db, thread_queue=_Queue(), root=None)
    state = SimpleNamespace(
        orgs={"happyranch": org},
        settings=SimpleNamespace(thread_reply_breaker_cooldown_seconds=900),
    )
    sleeps = 0

    async def _sleep(_interval):
        nonlocal sleeps
        sleeps += 1
        if sleeps == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    with pytest.raises(asyncio.CancelledError):
        await thread_breaker_scheduler_loop(state, interval_seconds=0)
    assert db.calls == 2
    assert [item.invocation_token for item in org.thread_queue.items] == [
        "rearmed-probe"
    ]
