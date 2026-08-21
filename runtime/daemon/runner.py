"""Task enqueue entry point for the daemon."""
from __future__ import annotations

from runtime.daemon.state import DaemonState
from runtime.portability.fence import TransferFence, TransferFenceHeld


def enqueue_task(state: DaemonState, slug: str, task_id: str) -> None:
    if state.is_idle:
        raise RuntimeError("daemon is idle — no active runtime")
    # THR-187 Slice B transfer fence: refuse new task admission for an org
    # whose transfer fence is held (an export capture is in progress). This is
    # the single production choke point for founder/thread/schedule/work-hour
    # task dispatch, so the fence is enforced here rather than at each route.
    # The isinstance guard keeps MagicMock-style test doubles (whose auto-attr
    # access would otherwise synthesize a truthy fence) from false-positives.
    org = state.orgs.get(slug)
    fence = getattr(org, "transfer_fence", None)
    if isinstance(fence, TransferFence) and fence.held:
        raise TransferFenceHeld(
            f"transfer fence held for org {slug!r}; new task admission refused"
        )
    state.queue.enqueue(slug, task_id)
