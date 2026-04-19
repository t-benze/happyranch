"""Process-wide state holder for the daemon."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from src.config import Settings
from src.daemon.event_bus import EventBus
from src.daemon.sessions import SessionTracker
from src.daemon.queue import TaskQueue
from src.infrastructure.database import Database
from src.models import TaskStatus
from src.runtime import RuntimeDir


@dataclass
class DaemonState:
    """Holds the active runtime, its DB, and the asyncio resources."""

    _TERMINAL_STATUS_TO_EVENT = {
        TaskStatus.COMPLETED: "task_complete",
        TaskStatus.FAILED: "task_failed",
        TaskStatus.BLOCKED: "task_blocked",
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
            if task is not None and task.status in self._TERMINAL_STATUS_TO_EVENT:
                history.append({
                    "type": self._TERMINAL_STATUS_TO_EVENT[task.status],
                    "outcome": task.status.value,
                    "synthesized": True,
                })
            return history
        self.event_bus = EventBus(history_loader=loader)

    @classmethod
    def idle(cls, settings: Settings) -> "DaemonState":
        return cls(runtime=None, db=None, settings=settings)

    @classmethod
    def from_runtime(cls, runtime: RuntimeDir, settings: Settings) -> "DaemonState":
        return cls(runtime=runtime, db=Database(runtime.db_path), settings=settings)

    @property
    def is_idle(self) -> bool:
        return self.runtime is None
