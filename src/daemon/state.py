"""Process-wide state holder for the daemon."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from src.config import Settings
from src.infrastructure.database import Database
from src.runtime import RuntimeDir


@dataclass
class DaemonState:
    """Holds the active runtime, its DB, and the asyncio resources."""

    runtime: RuntimeDir | None
    db: Database | None
    settings: Settings
    db_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @classmethod
    def idle(cls, settings: Settings) -> "DaemonState":
        return cls(runtime=None, db=None, settings=settings)

    @classmethod
    def from_runtime(cls, runtime: RuntimeDir, settings: Settings) -> "DaemonState":
        return cls(runtime=runtime, db=Database(runtime.db_path), settings=settings)

    @property
    def is_idle(self) -> bool:
        return self.runtime is None
