from __future__ import annotations

import asyncio
from unittest.mock import MagicMock


def test_notify_failed_routes_to_notifier_send_failure():
    from runtime.orchestrator.orchestrator import Orchestrator
    orch = Orchestrator.__new__(Orchestrator)
    notifier = MagicMock()
    notifier.send_failure = MagicMock()
    # send_failure must be awaitable; return a completed coroutine
    async def _noop(**kw):
        return None
    notifier.send_failure.side_effect = _noop
    orch._notifier = notifier

    orch.notify_failed(
        task_id="TASK-9", agent="dev_agent",
        failure_kind="self_blocked", failure_note="x", last_summary="y",
    )
    # Give the daemon thread a moment to call send_failure
    import time
    for _ in range(20):
        if notifier.send_failure.called:
            break
        time.sleep(0.05)
    assert notifier.send_failure.called
    kwargs = notifier.send_failure.call_args.kwargs
    assert kwargs["task_id"] == "TASK-9"
    assert kwargs["failure_kind"] == "self_blocked"


def test_notify_failed_when_notifier_none_is_silent():
    from runtime.orchestrator.orchestrator import Orchestrator
    orch = Orchestrator.__new__(Orchestrator)
    orch._notifier = None
    # Must not raise
    orch.notify_failed(
        task_id="TASK-9", agent="x",
        failure_kind="self_blocked", failure_note="x", last_summary="",
    )
