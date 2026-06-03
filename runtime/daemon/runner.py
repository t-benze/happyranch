"""Task enqueue entry point for the daemon."""
from __future__ import annotations

from runtime.daemon.state import DaemonState


def enqueue_task(state: DaemonState, slug: str, task_id: str) -> None:
    if state.is_idle:
        raise RuntimeError("daemon is idle — no active runtime")
    state.queue.enqueue(slug, task_id)
