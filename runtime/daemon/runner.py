"""Task enqueue entry point for the daemon."""
from __future__ import annotations

from runtime.daemon.state import DaemonState


def enqueue_task(state: DaemonState, slug: str, task_id: str) -> None:
    """Enqueue a freshly created task onto the daemon worker queue.

    The THR-187 Slice B transfer fence is NOT checked here: the durable
    ``insert_task`` + this enqueue must be one atomic admission critical
    section, so each producer wraps both in ``async with
    org.transfer_fence.admission():``. A fence check here would fire only
    *after* the caller's insert already committed, orphaning a durable pending
    task. See ``runtime/portability/fence.py`` for the lease semantics.
    """
    if state.is_idle:
        raise RuntimeError("daemon is idle — no active runtime")
    state.queue.enqueue(slug, task_id)
