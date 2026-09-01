import asyncio
import inspect
from types import SimpleNamespace

import pytest

from runtime.daemon.thread_breaker_scheduler import thread_breaker_scheduler_loop
from runtime.daemon.thread_queue import ThreadJob, ThreadQueue


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

        async def put_once(self, item):
            self.items.append(item)

    org = SimpleNamespace(slug="happyranch", db=_Db(), thread_queue=_Queue())
    org.root = None
    state = SimpleNamespace(
        orgs={"happyranch": org},
        settings=SimpleNamespace(),
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

        async def put_once(self, item):
            self.items.append(item)

    db = _Db()
    org = SimpleNamespace(slug="happyranch", db=db, thread_queue=_Queue(), root=None)
    state = SimpleNamespace(
        orgs={"happyranch": org},
        settings=SimpleNamespace(),
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


@pytest.mark.asyncio
async def test_real_queue_retries_post_commit_publication_failure_once(monkeypatch):
    entry = SimpleNamespace(invocation_token="durable-probe")

    class _Db:
        calls = 0

        def list_reply_delivery_states(self):
            return []

        def mint_due_thread_reply_breaker_probes(self, **kwargs):
            self.calls += 1
            return [entry]

    queue = ThreadQueue()
    original = queue.put_once
    attempts = 0

    async def fail_after_commit_once(job):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("post-commit publication failed")
        await original(job)

    queue.put_once = fail_after_commit_once
    org = SimpleNamespace(slug="happyranch", db=_Db(), thread_queue=queue, root=None)
    state = SimpleNamespace(orgs={"happyranch": org}, settings=SimpleNamespace())
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
    assert queue.size == 1
    assert (await queue.get()).invocation_token == "durable-probe"


@pytest.mark.asyncio
async def test_real_queue_repeated_publication_is_idempotent():
    queue = ThreadQueue()
    job = ThreadJob(org_slug="happyranch", invocation_token="same-probe")
    await queue.put_once(job)
    await queue.put_once(job)
    assert queue.size == 1


@pytest.mark.asyncio
async def test_real_queue_publication_has_no_cancellation_window():
    queue = ThreadQueue()
    task = asyncio.current_task()
    assert task is not None
    task.cancel()
    await queue.put_once(ThreadJob("happyranch", "cancel-safe-probe"))
    assert queue.size == 1
    with pytest.raises(asyncio.CancelledError):
        await asyncio.sleep(0)
    task.uncancel()


@pytest.mark.asyncio
async def test_shipping_worker_republishes_probe_after_preclaim_failure(monkeypatch):
    """A dequeue is not an acknowledgement until run_invocation returns."""
    import runtime.daemon.thread_runner as runner_mod
    from runtime.daemon.thread_queue import thread_worker_loop

    entry = SimpleNamespace(invocation_token="durable-probe")
    claimed = False

    class _Db:
        calls = 0

        def list_reply_delivery_states(self):
            return []

        def mint_due_thread_reply_breaker_probes(self, **kwargs):
            self.calls += 1
            return [] if claimed else [entry]

    queue = ThreadQueue()
    org = SimpleNamespace(
        slug="happyranch", db=_Db(), thread_queue=queue, root=None,
    )
    state = SimpleNamespace(
        orgs={"happyranch": org}, settings=SimpleNamespace(),
        host_supervisor=None,
    )
    attempts = 0
    launched = 0
    recovered = asyncio.Event()

    async def transient_then_claim(**kwargs):
        nonlocal attempts, launched, claimed
        attempts += 1
        if attempts == 1:
            # Models get_pending_invocation failing before the durable claim.
            raise RuntimeError("transient pending-invocation read")
        launched += 1
        claimed = True
        recovered.set()

    monkeypatch.setattr(runner_mod, "run_invocation", transient_then_claim)
    scheduler = asyncio.create_task(
        thread_breaker_scheduler_loop(state, interval_seconds=0.001)
    )
    workers = [
        asyncio.create_task(thread_worker_loop(state, state.settings))
        for _ in range(2)
    ]
    try:
        await asyncio.wait_for(recovered.wait(), timeout=1)
        await asyncio.sleep(0.02)
    finally:
        scheduler.cancel()
        for worker in workers:
            worker.cancel()
        await asyncio.gather(scheduler, *workers, return_exceptions=True)

    assert attempts == 2
    assert launched == 1
    assert org.db.calls >= 2
    assert queue.size == 0


@pytest.mark.asyncio
async def test_shipping_worker_cancellation_releases_dequeued_probe(monkeypatch):
    import runtime.daemon.thread_runner as runner_mod
    from runtime.daemon.thread_queue import thread_worker_loop

    queue = ThreadQueue()
    job = ThreadJob("happyranch", "cancelled-probe")
    await queue.put_once(job)
    entered = asyncio.Event()

    async def cancelled_before_claim(**kwargs):
        entered.set()
        raise asyncio.CancelledError

    monkeypatch.setattr(runner_mod, "run_invocation", cancelled_before_claim)
    org = SimpleNamespace(slug="happyranch", thread_queue=queue)
    state = SimpleNamespace(
        orgs={"happyranch": org}, settings=SimpleNamespace(),
        host_supervisor=None,
    )
    worker = asyncio.create_task(thread_worker_loop(state, state.settings))
    await asyncio.wait_for(entered.wait(), timeout=1)
    with pytest.raises(asyncio.CancelledError):
        await worker

    await queue.put_once(job)
    assert queue.size == 1
