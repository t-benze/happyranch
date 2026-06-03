"""In-memory tracker for the active session + subprocess PID per (task_id, agent).

The session_id half gates agent callbacks (409 `unknown_session` if the
caller's session isn't current). The pid half exists so `/tasks/{id}/cancel`
can send SIGTERM to every live subprocess attached to a cancelled subtree
without grepping the process table.
"""
from __future__ import annotations

from threading import Lock


class SessionTracker:
    def __init__(self) -> None:
        self._active: dict[tuple[str, str], str] = {}
        self._pids: dict[tuple[str, str], int] = {}
        self._lock = Lock()

    def set_active(self, task_id: str, agent: str, session_id: str) -> None:
        with self._lock:
            self._active[(task_id, agent)] = session_id

    def get_active(self, task_id: str, agent: str) -> str | None:
        with self._lock:
            return self._active.get((task_id, agent))

    def set_pid(self, task_id: str, agent: str, pid: int) -> None:
        """Register the OS pid for an already-set-active session.

        Called from the executor's on_started callback after Popen returns.
        If `set_active` hasn't been called yet (unit tests, odd ordering),
        the pid is still stored — it will simply have no session_id to
        validate against, which is fine because cancel only needs the pid.
        """
        with self._lock:
            self._pids[(task_id, agent)] = pid

    def get_pid(self, task_id: str, agent: str) -> int | None:
        with self._lock:
            return self._pids.get((task_id, agent))

    def iter_task_pids(self, task_id: str) -> list[tuple[str, int]]:
        """Return (agent, pid) for every live pid under ``task_id``.

        Used by /cancel to SIGTERM the entire task's attached subprocesses.
        Returns a snapshot — safe to iterate without holding the lock.
        """
        with self._lock:
            return [
                (agent, pid)
                for (tid, agent), pid in self._pids.items()
                if tid == task_id
            ]

    def clear(self, task_id: str, agent: str) -> None:
        with self._lock:
            self._active.pop((task_id, agent), None)
            self._pids.pop((task_id, agent), None)
