"""Task enqueue entry point for the daemon.

The old `TaskRunner` wrapped a synchronous `Orchestrator.run_task` call in a
thread. Under the async queue model, task submission just pushes the task ID
onto `state.queue` and worker coroutines (started at daemon boot) invoke
`Orchestrator.run_step` one step at a time.
"""
from __future__ import annotations

from src.daemon.state import DaemonState


def enqueue_task(state: DaemonState, task_id: str) -> None:
    """Push a task onto the daemon's work queue.

    Raises RuntimeError if the daemon is idle (no runtime). The /tasks route
    already gates on is_idle, so this is a defensive backstop for direct callers.
    """
    if state.is_idle:
        raise RuntimeError("daemon is idle — no active runtime")
    state.queue.enqueue(task_id)
