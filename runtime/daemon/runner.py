"""Task enqueue entry point for the daemon.

THR-229 checkpoint C3d4a: ``enqueue_task`` is the common DB-aware task enqueue
boundary.  It resolves the TARGET root's durable v2 generation at production
time through ``runtime.orchestrator.authority.enqueue_task_generation_aware`` so
a root whose dispatch pointer is ``pending(G)`` is published through the
authenticated notification publisher instead of an untagged fallback.  The
idle-runner refusal and the ordinary tuple shape are unchanged.
"""
from __future__ import annotations

from runtime.daemon.state import DaemonState


def enqueue_task(
    state: DaemonState, slug: str, task_id: str, *, metadata: dict | None = None,
) -> None:
    if state.is_idle:
        raise RuntimeError("daemon is idle — no active runtime")
    try:
        orchestrator = state.get_org(slug).orchestrator
    except Exception:
        orchestrator = None
    if orchestrator is None:
        # No attached orchestrator/DB to classify against (unchanged legacy
        # behavior for an org that is not loaded).
        if metadata is None:
            state.queue.enqueue(slug, task_id)
        else:
            state.queue.enqueue(slug, task_id, metadata=metadata)
        return
    from runtime.orchestrator.authority import enqueue_task_generation_aware
    enqueue_task_generation_aware(
        orchestrator, state.queue, slug, task_id, metadata=metadata,
    )
