"""Deterministic linearizability + producer-wiring tests for the THR-187 Slice B
transfer fence (admission lease).

Reviewer finding #1 demanded that the predicate-only ``TransferFence.held``
model be replaced with a real per-org admission lease/serialization protocol,
wired into every production producer, and proven with deterministic concurrency
tests that never deadlock (bounded waits, ``finally``-release/join of every
thread/task).

The tests drive the REAL production seams — ``admission()`` (the reader lease
the routes wrap), ``org.db.insert_task`` + ``runner.enqueue_task`` (the durable
producer) and ``acquire()/release()`` (the exporter's writer lease) — on a
single event loop. Route-level wiring is separately proven by the
``*_refused_while_fence_held`` tests, which show the real HTTP seams return 409
while the fence is held.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from runtime.daemon.runner import enqueue_task
from runtime.models import TaskRecord, TaskStatus
from runtime.portability.fence import TransferFence, TransferFenceHeld


_BOUND = 10.0  # bounded wait ceiling (seconds) for every rendezvous


def _run(scenario) -> None:
    """Run an async scenario on a fresh loop with a hard outer bound so a
    wiring regression fails fast instead of hanging the suite."""
    asyncio.run(asyncio.wait_for(scenario(), timeout=_BOUND * 4))


# ── Primitive lease-semantics tests (deterministic) ─────────────────────────


def test_fence_acquire_waits_for_inflight_admission() -> None:
    """An admission that started before ``acquire`` completes its durable write
    BEFORE ``acquire`` returns — so the exporter's recheck observes it."""

    async def scenario() -> None:
        fence = TransferFence()
        landed: list[str] = []
        entered = asyncio.Event()
        release = asyncio.Event()
        acquire_done = asyncio.Event()

        async def producer() -> None:
            async with fence.admission():
                entered.set()
                await release.wait()  # paused AFTER admission, BEFORE durable write
                landed.append("task")

        async def exporter() -> None:
            await fence.acquire()
            acquire_done.set()
            await fence.release()

        pt = asyncio.create_task(producer())
        try:
            await asyncio.wait_for(entered.wait(), timeout=_BOUND)
            et = asyncio.create_task(exporter())
            try:
                # The exporter must NOT return while the producer holds its lease.
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                assert not acquire_done.is_set(), (
                    "exporter returned before the in-flight admission drained"
                )
                release.set()
                await asyncio.wait_for(pt, timeout=_BOUND)
                await asyncio.wait_for(et, timeout=_BOUND)
                assert landed == ["task"]
            finally:
                release.set()
                for t in (pt, et):
                    if not t.done():
                        t.cancel()
                asyncio.gather(pt, et, return_exceptions=True)
        finally:
            release.set()
            if not pt.done():
                pt.cancel()

    _run(scenario)


def test_fence_refuses_admission_after_acquire() -> None:
    """Once the fence is held, every new admission raises; release resumes it."""

    async def scenario() -> None:
        fence = TransferFence()
        assert await fence.acquire() is True
        with pytest.raises(TransferFenceHeld):
            async with fence.admission():
                pass  # pragma: no cover
        assert await fence.acquire() is False  # already held — no second exporter
        await fence.release()
        async with fence.admission():
            pass

    _run(scenario)


def test_fence_normal_admission_without_fence() -> None:
    """Concurrent admissions without an exporter proceed normally."""

    async def scenario() -> None:
        fence = TransferFence()
        done = []

        async def admit(tag: str) -> None:
            async with fence.admission():
                done.append(tag)

        await asyncio.gather(*(admit(f"a{i}") for i in range(5)))
        assert sorted(done) == ["a0", "a1", "a2", "a3", "a4"]

    _run(scenario)


# ── Real-seam linearizability tests (Test A / Test B) ───────────────────────


def test_producer_admitted_then_paused_lands_before_export_recheck(
    daemon_state,
) -> None:
    """Test A: a real producer admitted (reader lease held) signals after lease
    acquisition but before its durable write; the exporter waits; after release
    the exporter's final recheck observes the producer's write (nothing can land
    between the recheck and the capture)."""

    org = daemon_state.orgs["alpha"]

    async def scenario() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        exporter_acquired = asyncio.Event()
        landed: list[str] = []

        async def producer() -> None:
            # Mirrors the real route: the durable insert + enqueue are one
            # admission critical section.
            async with org.transfer_fence.admission():
                entered.set()
                await release.wait()  # pause AFTER admission, BEFORE durable write
                org.db.insert_task(TaskRecord(
                    id="T-NEW", brief="t", team="engineering",
                    assigned_agent="dev_agent", status=TaskStatus.PENDING,
                ))
                enqueue_task(daemon_state, "alpha", "T-NEW")
                landed.append("T-NEW")

        async def exporter() -> None:
            assert await org.transfer_fence.acquire() is True
            exporter_acquired.set()
            # The final recheck runs here: the producer's write must already be
            # visible because acquire() only returned after the lease drained.
            assert org.db.get_task("T-NEW") is not None
            assert "T-NEW" in org.db.get_nonterminal_task_ids()
            await org.transfer_fence.release()

        pt = asyncio.create_task(producer())
        et: asyncio.Task | None = None
        try:
            await asyncio.wait_for(entered.wait(), timeout=_BOUND)
            et = asyncio.create_task(exporter())
            # Give the exporter a chance to run: it must NOT have acquired while
            # the producer still holds its reader lease.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert not exporter_acquired.is_set(), (
                "exporter acquired the fence before the admitted producer drained"
            )
            release.set()
            await asyncio.wait_for(pt, timeout=_BOUND)
            await asyncio.wait_for(et, timeout=_BOUND)
            assert landed == ["T-NEW"]
        finally:
            release.set()
            await org.transfer_fence.release()
            for t in (pt, et):
                if t is not None and not t.done():
                    t.cancel()
            await asyncio.gather(
                *[t for t in (pt, et) if t is not None],
                return_exceptions=True,
            )

    _run(scenario)


def test_export_exclusive_late_producer_cannot_reach_write_hook(
    daemon_state,
) -> None:
    """Test B: the export establishes the exclusive capture first; a late real
    producer attempts admission but cannot reach its post-admission/pre-write
    hook (``insert_task``) or mutate; release the export and join both."""

    org = daemon_state.orgs["alpha"]

    async def scenario() -> None:
        real_insert = org.db.insert_task
        reached: list[str] = []

        def spy_insert(*args, **kwargs):
            reached.append("insert_task")
            return real_insert(*args, **kwargs)

        assert await org.transfer_fence.acquire() is True  # exporter holds fence
        org.db.insert_task = spy_insert
        try:
            # A late producer is refused at admission, before any durable write.
            with pytest.raises(TransferFenceHeld):
                async with org.transfer_fence.admission():
                    org.db.insert_task(TaskRecord(  # pragma: no cover
                        id="T-LATE", brief="t", team="engineering",
                        assigned_agent="dev_agent", status=TaskStatus.PENDING,
                    ))
            assert reached == []  # the write hook was never reached
            assert org.db.get_task("T-LATE") is None  # no mutation
        finally:
            org.db.insert_task = real_insert
            await org.transfer_fence.release()

        # After release, a producer can admit and mutate normally.
        async with org.transfer_fence.admission():
            org.db.insert_task(TaskRecord(
                id="T-AFTER", brief="t", team="engineering",
                assigned_agent="dev_agent", status=TaskStatus.PENDING,
            ))
        assert org.db.get_task("T-AFTER") is not None

    _run(scenario)


# ── Producer-wiring tests (real HTTP routes on one loop) ────────────────────


def _seed_agent(org_state, name: str, *, team: str = "engineering") -> None:
    agents_dir = org_state.root / "org" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / f"{name}.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"team: {team}\n"
        "role: worker\n"
        "executor: claude\n"
        "description: test agent\n"
        "---\n"
        "# system prompt\n"
    )
    (org_state.root / "workspaces" / name).mkdir(parents=True, exist_ok=True)


def test_thread_compose_refused_while_fence_held(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Thread composition is wired to the admission lease: while held it
    returns 409 and mints no invocation."""
    _seed_agent(org_state, "dev_agent")
    _seed_agent(org_state, "qa_engineer")
    transport = ASGITransport(app=app)

    async def scenario() -> None:
        assert await org_state.transfer_fence.acquire() is True
        try:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/orgs/alpha/threads",
                    json={
                        "subject": "Refund policy",
                        "recipients": ["dev_agent", "qa_engineer"],
                        "body_markdown": "cap refunds?",
                    },
                    headers=auth_headers,
                )
            assert resp.status_code == 409, resp.text
            assert resp.json()["detail"]["code"] == "transfer_in_progress"
        finally:
            await org_state.transfer_fence.release()

        assert org_state.db.list_threads() == []

    _run(scenario)


def test_escalation_supersede_refused_while_fence_held(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Escalation supersede is wired: while held it returns 409 and mints no
    successor task."""
    org_state.db.insert_task(TaskRecord(
        id="T-ESC", brief="escalated work", team="engineering",
        assigned_agent="dev_agent", status=TaskStatus.ESCALATED,
    ))
    transport = ASGITransport(app=app)

    async def scenario() -> None:
        assert await org_state.transfer_fence.acquire() is True
        try:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/orgs/alpha/tasks/T-ESC/resolve-escalation",
                    json={"decision": "supersede", "brief": "successor brief", "rationale": "r"},
                    headers=auth_headers,
                )
            assert resp.status_code == 409, resp.text
            assert resp.json()["detail"]["code"] == "transfer_in_progress"
        finally:
            await org_state.transfer_fence.release()

        assert org_state.db.get_task("T-ESC").status == TaskStatus.ESCALATED
        assert org_state.db.get_nonterminal_task_ids() == ["T-ESC"]

    _run(scenario)


def test_escalation_continue_refused_while_fence_held(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Escalation continue is wired: while held it returns 409 and the task
    stays escalated (not re-PENDING, not re-enqueued)."""
    org_state.db.insert_task(TaskRecord(
        id="T-ESC", brief="escalated work", team="engineering",
        assigned_agent="dev_agent", status=TaskStatus.ESCALATED,
    ))
    transport = ASGITransport(app=app)

    async def scenario() -> None:
        assert await org_state.transfer_fence.acquire() is True
        try:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/orgs/alpha/tasks/T-ESC/resolve-escalation",
                    json={"decision": "continue", "rationale": "r"},
                    headers=auth_headers,
                )
            assert resp.status_code == 409, resp.text
            assert resp.json()["detail"]["code"] == "transfer_in_progress"
        finally:
            await org_state.transfer_fence.release()

        assert org_state.db.get_task("T-ESC").status == TaskStatus.ESCALATED

    _run(scenario)
