"""run_step should call orch.notify_escalated on max-steps overflow."""
from __future__ import annotations

from unittest.mock import MagicMock

from src.models import BlockKind, TaskStatus
from src.orchestrator import run_step as run_step_mod


def test_max_steps_path_calls_notify_escalated():
    seen: list[dict] = []

    class _FakeOrch:
        def __init__(self):
            self._db = MagicMock()
            self._audit = MagicMock()
            self._settings = MagicMock(max_orchestration_steps=1)
            self._notifier = object()  # truthy

        def notify_escalated(self, **kwargs):
            seen.append(kwargs)

    fake = _FakeOrch()
    task = MagicMock(
        id="TASK-1", status=TaskStatus.PENDING, block_kind=None,
        cancelled_at=None, orchestration_step_count=1,
    )
    fake._db.get_task.return_value = task
    run_step_mod.run_step_impl(fake, "TASK-1")

    fake._audit.log_escalation.assert_called_once()
    assert seen, "notify_escalated was not called"
    assert seen[0]["task_id"] == "TASK-1"
    assert seen[0]["agent"] == "orchestrator"
