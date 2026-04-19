"""Asyncio queue + worker pool for invoking Orchestrator.run_step.

`run_step` is synchronous (it launches a Claude Code subprocess via
subprocess.run), so workers bridge to a thread via run_in_executor.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.orchestrator.orchestrator import Orchestrator

logger = logging.getLogger("opc.daemon.queue")


class TaskQueue:
    """Wrapper around asyncio.Queue + a worker pool."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker_tasks: list[asyncio.Task] = []
        self._stopping = False

    def enqueue(self, task_id: str) -> None:
        """Non-blocking enqueue. Called from sync context (e.g. run_step)."""
        self._queue.put_nowait(task_id)

    def put_nowait(self, task_id: str) -> None:
        """Alias for `enqueue` — matches asyncio.Queue's method name so the
        orchestrator can treat `TaskQueue` and `asyncio.Queue` interchangeably."""
        self.enqueue(task_id)

    async def _worker_loop(self, orch: "Orchestrator") -> None:
        loop = asyncio.get_running_loop()
        while not self._stopping:
            task_id = await self._queue.get()
            try:
                await loop.run_in_executor(None, orch.run_step, task_id)
            except Exception:
                logger.exception("run_step %s raised — continuing", task_id)
            finally:
                self._queue.task_done()

    def start_workers(self, orch: "Orchestrator", n: int = 3) -> None:
        """Spawn `n` worker coroutines. Idempotent per-call is NOT expected —
        call once per daemon lifecycle."""
        for _ in range(n):
            self._worker_tasks.append(
                asyncio.create_task(self._worker_loop(orch))
            )

    async def stop(self, *, timeout: float = 5.0) -> None:
        """Graceful shutdown: stop accepting work, cancel workers."""
        self._stopping = True
        for t in self._worker_tasks:
            t.cancel()
        await asyncio.gather(*self._worker_tasks, return_exceptions=True)

    async def drain_sync(self, orch: "Orchestrator") -> None:
        """Test helper: process every currently-queued item SYNCHRONOUSLY on
        this event loop, without spinning up long-lived worker tasks. Returns
        when the queue is empty.

        This exists so tests can drive the queue deterministically without
        racing against `run_in_executor`-backed workers."""
        loop = asyncio.get_running_loop()
        while not self._queue.empty():
            task_id = self._queue.get_nowait()
            try:
                await loop.run_in_executor(None, orch.run_step, task_id)
            except Exception:
                logger.exception("run_step %s raised during drain", task_id)
            finally:
                self._queue.task_done()
