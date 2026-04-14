"""In-memory tracker for the active session per (task_id, agent)."""
from __future__ import annotations

from threading import Lock


class SessionTracker:
    def __init__(self) -> None:
        self._active: dict[tuple[str, str], str] = {}
        self._lock = Lock()

    def set_active(self, task_id: str, agent: str, session_id: str) -> None:
        with self._lock:
            self._active[(task_id, agent)] = session_id

    def get_active(self, task_id: str, agent: str) -> str | None:
        with self._lock:
            return self._active.get((task_id, agent))

    def clear(self, task_id: str, agent: str) -> None:
        with self._lock:
            self._active.pop((task_id, agent), None)
