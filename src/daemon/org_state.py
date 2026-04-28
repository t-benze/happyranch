"""Per-org runtime state: DB, queue events, sessions, teams, locks.

One ``OrgState`` per active org under ``<runtime>/orgs/<slug>/``. Constructed
once at daemon startup (via ``DaemonState.from_runtime``) or lazily on
``opc orgs init <slug>``. Each instance is fully self-contained — no
cross-references to other orgs.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from src.config import Settings
from src.daemon.event_bus import EventBus
from src.daemon.sessions import SessionTracker
from src.infrastructure.database import Database
from src.models import BlockKind, TaskStatus
from src.orchestrator.teams import TeamsRegistry


@dataclass
class OrgState:
    slug: str
    root: Path                        # <runtime>/orgs/<slug>
    db: Database
    teams: TeamsRegistry
    settings: Settings
    sessions: SessionTracker = field(default_factory=SessionTracker)
    db_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    kb_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    teams_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    event_bus: EventBus = field(init=False)

    _TERMINAL_STATUS_TO_EVENT = {
        TaskStatus.COMPLETED: "task_complete",
        TaskStatus.FAILED: "task_failed",
    }

    def __post_init__(self) -> None:
        def loader(task_id: str) -> list[dict]:
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
    def load(cls, *, slug: str, root: Path, settings: Settings) -> "OrgState":
        db = Database(root / "opc.db")
        teams = TeamsRegistry.load(root)
        return cls(
            slug=slug,
            root=root,
            db=db,
            teams=teams,
            settings=settings,
        )

    def close(self) -> None:
        self.db.close()
