"""In-memory pub/sub for daemon events with DB replay on subscribe."""
from __future__ import annotations

import asyncio
from typing import AsyncIterator, Callable

_TERMINAL_TYPES = {"task_complete", "task_failed", "task_blocked"}


def thread_topic(thread_id: str) -> str:
    return f"thread:{thread_id}"


def thread_inbox_topic(org_slug: str) -> str:
    return f"thread_inbox:{org_slug}"


def script_topic(sr_id: str) -> str:
    return f"script:{sr_id}"


class EventBus:
    def __init__(self, history_loader: Callable[[str], list[dict]]) -> None:
        self._history_loader = history_loader
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()

    async def publish(self, task_id: str, event: dict) -> None:
        async with self._lock:
            queues = list(self._subscribers.get(task_id, []))
        for q in queues:
            await q.put(event)

    async def subscribe(self, task_id: str) -> AsyncIterator[dict]:
        queue: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            self._subscribers.setdefault(task_id, []).append(queue)
        try:
            for past in self._history_loader(task_id):
                yield past
                if past.get("type") in _TERMINAL_TYPES:
                    return
            while True:
                event = await queue.get()
                yield event
                if event.get("type") in _TERMINAL_TYPES:
                    return
        finally:
            async with self._lock:
                if queue in self._subscribers.get(task_id, []):
                    self._subscribers[task_id].remove(queue)
                if not self._subscribers.get(task_id):
                    self._subscribers.pop(task_id, None)
