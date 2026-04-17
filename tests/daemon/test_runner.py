from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.daemon.runner import TaskRunner


@pytest.mark.asyncio
async def test_runner_invokes_orchestrator_run_task() -> None:
    state = MagicMock()
    state.runtime = MagicMock()
    state.db = MagicMock()
    state.settings = MagicMock()
    state.sessions = MagicMock()
    state.event_bus = MagicMock()
    state.event_bus.publish = AsyncMock()

    orch = MagicMock()
    orch.run_task = MagicMock(return_value="approved")

    runner = TaskRunner(state=state, orchestrator_factory=lambda _r, _d, _s: orch)
    await runner.run("TASK-001")

    orch.run_task.assert_called_once_with("TASK-001")


@pytest.mark.asyncio
async def test_runner_publishes_terminal_event() -> None:
    state = MagicMock()
    state.event_bus = MagicMock()

    captured: list[dict] = []
    async def fake_publish(task_id, event):
        captured.append(event)
    state.event_bus.publish = fake_publish

    orch = MagicMock()
    orch.run_task = MagicMock(return_value="escalated")

    runner = TaskRunner(state=state, orchestrator_factory=lambda _r, _d, _s: orch)
    await runner.run("TASK-001")
    assert any(e["type"] == "task_escalated" for e in captured)


@pytest.mark.asyncio
async def test_runner_wraps_run_agent_to_track_sessions() -> None:
    """The runner must patch `_run_agent` so `SessionTracker.set_active` fires
    when the orchestrator spawns an agent session."""
    state = MagicMock()
    state.event_bus = MagicMock()
    state.event_bus.publish = AsyncMock()
    state.sessions = MagicMock()

    captured_agent_args: dict = {}

    class FakeOrchestrator:
        def _run_agent(self, task_id, agent, prompt, on_session_started=None):
            captured_agent_args["on_session_started"] = on_session_started
            if on_session_started is not None:
                on_session_started(task_id, agent, "sess-xyz")
            return (MagicMock(), MagicMock())

        def run_task(self, task_id):
            # Simulate run_task spawning one agent session
            self._run_agent(task_id, "engineering_head", "prompt")
            return "approved"

    orch = FakeOrchestrator()

    runner = TaskRunner(state=state, orchestrator_factory=lambda _r, _d, _s: orch)
    await runner.run("TASK-001")

    state.sessions.set_active.assert_called_once_with(
        "TASK-001", "engineering_head", "sess-xyz",
    )


@pytest.mark.asyncio
async def test_runner_crash_flips_task_to_escalated() -> None:
    """If orchestrator.run_task raises, runner must flip DB status to ESCALATED
    so the row stops blocking runtime activate."""
    from src.models import TaskStatus

    state = MagicMock()
    state.event_bus = MagicMock()
    state.event_bus.publish = AsyncMock()
    state.sessions = MagicMock()
    state.db = MagicMock()

    orch = MagicMock()
    orch.run_task = MagicMock(side_effect=RuntimeError("boom"))

    runner = TaskRunner(state=state, orchestrator_factory=lambda _r, _d, _s: orch)
    await runner.run("TASK-001")

    state.db.update_task.assert_called_once_with("TASK-001", status=TaskStatus.ESCALATED)


@pytest.mark.asyncio
async def test_runner_snapshots_runtime_and_db_at_construction() -> None:
    """If DaemonState gets a different runtime/db after the runner is built,
    the runner must still use the originals."""
    state = MagicMock()
    state.event_bus = MagicMock()
    state.event_bus.publish = AsyncMock()
    state.sessions = MagicMock()

    original_runtime = MagicMock(name="rt-original")
    original_db = MagicMock(name="db-original")
    original_settings = MagicMock(name="settings-original")
    state.runtime = original_runtime
    state.db = original_db
    state.settings = original_settings

    captured: dict = {}
    def factory(rt, db, settings):
        captured["rt"] = rt
        captured["db"] = db
        captured["settings"] = settings
        m = MagicMock()
        m.run_task = MagicMock(return_value="approved")
        return m

    runner = TaskRunner(state=state, orchestrator_factory=factory)

    # Simulate a runtime swap after submit but before the runner actually runs.
    state.runtime = MagicMock(name="rt-swapped")
    state.db = MagicMock(name="db-swapped")

    await runner.run("TASK-001")

    assert captured["rt"] is original_runtime
    assert captured["db"] is original_db
    assert captured["settings"] is original_settings
