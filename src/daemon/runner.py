"""Daemon-side task runner that wraps the blocking Orchestrator."""
from __future__ import annotations

import asyncio
import logging
from typing import Callable

from src.config import Settings
from src.daemon.state import DaemonState
from src.infrastructure.database import Database
from src.orchestrator.orchestrator import Orchestrator
from src.runtime import RuntimeDir

logger = logging.getLogger("opc.daemon.runner")

_OUTCOME_TO_EVENT = {
    "approved": "task_complete",
    "rejected": "task_rejected",
    "escalated": "task_escalated",
}


class TaskRunner:
    """Snapshot of (runtime, db, settings) at construction time, decoupled from
    live `DaemonState` mutation. The event bus + session tracker are still
    looked up live on `state` because they're singletons that don't change on
    runtime swap."""

    def __init__(
        self,
        state: DaemonState,
        orchestrator_factory: Callable[[RuntimeDir, Database, Settings], Orchestrator] | None = None,
    ) -> None:
        assert state.db is not None and state.runtime is not None, \
            "TaskRunner cannot be constructed in idle mode"
        # Snapshot the runtime/db/settings now. Even if state.runtime swaps
        # later (which the activate guard should prevent anyway), this runner
        # keeps operating against the runtime the task was created in.
        self._runtime: RuntimeDir = state.runtime
        self._db: Database = state.db
        self._settings: Settings = state.settings
        self._sessions = state.sessions
        self._event_bus = state.event_bus
        self._make_orchestrator = orchestrator_factory or self._default_factory

    @staticmethod
    def _default_factory(runtime: RuntimeDir, db: Database, settings: Settings) -> Orchestrator:
        return Orchestrator(db=db, settings=settings, runtime=runtime)

    async def run(self, task_id: str) -> None:
        orchestrator = self._make_orchestrator(self._runtime, self._db, self._settings)

        # Patch the orchestrator's per-spawn callback into SessionTracker.
        original_run_agent = orchestrator._run_agent
        sessions = self._sessions

        def _wrapped_run_agent(task_id_, agent, prompt):
            def _on_started(t, a, s):
                sessions.set_active(t, a, s)
            return original_run_agent(task_id_, agent, prompt, on_session_started=_on_started)

        orchestrator._run_agent = _wrapped_run_agent  # type: ignore[assignment]

        try:
            outcome = await asyncio.to_thread(orchestrator.run_task, task_id)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("task %s crashed in runner", task_id)
            await self._event_bus.publish(task_id, {
                "type": "task_escalated", "reason": f"runner crash: {exc}",
            })
            return

        await self._event_bus.publish(task_id, {
            "type": _OUTCOME_TO_EVENT.get(outcome, "task_complete"),
            "outcome": outcome,
        })
