from __future__ import annotations

import asyncio

import pytest

from src.daemon.event_bus import EventBus


@pytest.mark.asyncio
async def test_publish_then_subscribe_delivers_history() -> None:
    bus = EventBus(history_loader=lambda task_id: [
        {"type": "step", "n": 1}, {"type": "step", "n": 2},
    ])
    received: list = []

    async def consumer():
        async for event in bus.subscribe("TASK-001"):
            received.append(event)
            if len(received) == 3:
                break

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0.05)  # let subscriber consume history
    await bus.publish("TASK-001", {"type": "step", "n": 3})
    await asyncio.wait_for(consumer_task, timeout=2.0)
    assert [e["n"] for e in received] == [1, 2, 3]


@pytest.mark.asyncio
async def test_terminal_event_closes_subscriber() -> None:
    bus = EventBus(history_loader=lambda _t: [])
    received: list = []

    async def consumer():
        async for event in bus.subscribe("TASK-001"):
            received.append(event)

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0.05)
    await bus.publish("TASK-001", {"type": "task_complete", "status": "approved"})
    await asyncio.wait_for(consumer_task, timeout=2.0)
    assert received[-1]["type"] == "task_complete"


@pytest.mark.asyncio
async def test_two_subscribers_both_receive() -> None:
    bus = EventBus(history_loader=lambda _t: [])
    a, b = [], []

    async def consume(into):
        async for event in bus.subscribe("TASK-001"):
            into.append(event)

    ta = asyncio.create_task(consume(a))
    tb = asyncio.create_task(consume(b))
    await asyncio.sleep(0.05)
    await bus.publish("TASK-001", {"type": "task_complete"})
    await asyncio.wait_for(asyncio.gather(ta, tb), timeout=2.0)
    assert a == b == [{"type": "task_complete"}]


@pytest.mark.asyncio
async def test_late_subscriber_to_finished_task_gets_synthesized_terminal() -> None:
    """Reattach scenario: task already finished, no live publisher exists.
    The history loader must surface a terminal event so the subscriber closes."""
    history = [
        {"type": "audit", "action": "session_end"},
        {"type": "task_complete", "outcome": "approved", "synthesized": True},
    ]
    bus = EventBus(history_loader=lambda _t: history)
    received: list = []

    async def consumer():
        async for event in bus.subscribe("TASK-DONE"):
            received.append(event)

    await asyncio.wait_for(consumer(), timeout=2.0)
    assert received[-1]["type"] == "task_complete"
    assert received[-1].get("synthesized") is True


def test_terminal_types_include_new_events():
    from src.daemon.event_bus import _TERMINAL_TYPES
    assert "task_failed" in _TERMINAL_TYPES
    assert "task_blocked" in _TERMINAL_TYPES
    assert "task_complete" in _TERMINAL_TYPES


def test_job_topic_format():
    from src.daemon.event_bus import job_topic
    assert job_topic("JOB-019") == "job:JOB-019"
