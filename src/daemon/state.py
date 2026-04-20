"""Process-wide state holder for the daemon."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from src.config import Settings
from src.daemon.event_bus import EventBus
from src.daemon.sessions import SessionTracker
from src.daemon.queue import TaskQueue
from src.infrastructure.database import Database
from src.models import BlockKind, TaskStatus
from src.runtime import RuntimeDir


@dataclass
class DaemonState:
    """Holds the active runtime, its DB, and the asyncio resources."""

    # BLOCKED is intentionally absent here — block_kind decides:
    #   DELEGATED  → non-terminal (parent resumes when children terminate)
    #   ESCALATED  → synthesized as task_blocked (awaiting founder resolution)
    # See _synthesize_terminal_event for the full rule.
    _TERMINAL_STATUS_TO_EVENT = {
        TaskStatus.COMPLETED: "task_complete",
        TaskStatus.FAILED: "task_failed",
    }

    runtime: RuntimeDir | None
    db: Database | None
    settings: Settings
    db_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    kb_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    sessions: SessionTracker = field(default_factory=SessionTracker)
    queue: TaskQueue = field(default_factory=TaskQueue)
    event_bus: EventBus = field(init=False)

    def __post_init__(self) -> None:
        def loader(task_id: str) -> list[dict]:
            if self.db is None:
                return []
            history: list[dict] = [
                {"type": "audit", **log}
                for log in self.db.get_audit_logs(task_id)
            ]
            task = self.db.get_task(task_id)
            terminal = self._synthesize_terminal_event(task) if task else None
            if terminal is not None:
                history.append(terminal)
            return history
        self.event_bus = EventBus(history_loader=loader)

    def _synthesize_terminal_event(self, task) -> dict | None:
        """Return a synthesized terminal event for a late subscriber, or None
        if the task is still in-flight from the subscriber's POV.

        COMPLETED / FAILED are unconditional terminals. BLOCKED(ESCALATED) is
        a human-in-the-loop pause, which is terminal-enough for `opc tail`.
        BLOCKED(DELEGATED) is explicitly NOT terminal — the parent resumes
        automatically when its children finish, and closing the stream here
        would make observers report waiting parents as done.
        """
        if task.status in self._TERMINAL_STATUS_TO_EVENT:
            return {
                "type": self._TERMINAL_STATUS_TO_EVENT[task.status],
                "outcome": task.status.value,
                "synthesized": True,
            }
        if task.status == TaskStatus.BLOCKED and task.block_kind == BlockKind.ESCALATED:
            return {
                "type": "task_blocked",
                "outcome": "escalated",
                "synthesized": True,
            }
        return None

    @classmethod
    def idle(cls, settings: Settings) -> "DaemonState":
        return cls(runtime=None, db=None, settings=settings)

    @classmethod
    def from_runtime(cls, runtime: RuntimeDir, settings: Settings) -> "DaemonState":
        return cls(runtime=runtime, db=Database(runtime.db_path), settings=settings)

    @property
    def is_idle(self) -> bool:
        return self.runtime is None
