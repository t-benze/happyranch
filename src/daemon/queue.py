"""Asyncio queue + worker pool for invoking Orchestrator.run_step.

Items are ``(slug, task_id)`` tuples. The worker loop unpacks each item,
looks up ``state.get_org(slug)``, and calls that org's
``Orchestrator.run_step(task_id)`` on a thread.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from src.daemon.state import DaemonState

logger = logging.getLogger("opc.daemon.queue")


class _Dispatcher(Protocol):
    def run_step(self, slug: str, task_id: str) -> None: ...


class TaskQueue:
    """Wrapper around asyncio.Queue + a worker pool."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._worker_tasks: list[asyncio.Task] = []
        self._stopping = False

    def enqueue(self, slug: str, task_id: str) -> None:
        self._queue.put_nowait((slug, task_id))

    def put_nowait(self, slug: str, task_id: str) -> None:
        self.enqueue(slug, task_id)

    async def _worker_loop(self, dispatcher: _Dispatcher) -> None:
        loop = asyncio.get_running_loop()
        while not self._stopping:
            slug, task_id = await self._queue.get()
            try:
                await loop.run_in_executor(
                    None, dispatcher.run_step, slug, task_id,
                )
            except Exception:
                logger.exception(
                    "run_step %s/%s raised — continuing", slug, task_id,
                )
            finally:
                self._queue.task_done()

    def start_workers(self, dispatcher: _Dispatcher, n: int = 3) -> None:
        for _ in range(n):
            self._worker_tasks.append(
                asyncio.create_task(self._worker_loop(dispatcher))
            )

    def is_running(self) -> bool:
        return any(not t.done() for t in self._worker_tasks)

    async def stop(self, *, timeout: float = 5.0) -> None:
        self._stopping = True
        for t in self._worker_tasks:
            t.cancel()
        await asyncio.gather(*self._worker_tasks, return_exceptions=True)

    async def drain_sync(self, dispatcher: _Dispatcher) -> None:
        loop = asyncio.get_running_loop()
        while not self._queue.empty():
            slug, task_id = self._queue.get_nowait()
            try:
                await loop.run_in_executor(
                    None, dispatcher.run_step, slug, task_id,
                )
            except Exception:
                logger.exception(
                    "run_step %s/%s raised during drain", slug, task_id,
                )
            finally:
                self._queue.task_done()
