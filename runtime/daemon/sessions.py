"""In-memory tracker for the active session + subprocess PID per (task_id, agent).

The session_id half gates agent callbacks (409 `unknown_session` if the
caller's session isn't current). The pid half exists so `/tasks/{id}/cancel`
can send SIGTERM to every live subprocess attached to a cancelled subtree
without grepping the process table.
"""
from __future__ import annotations

import threading
from threading import Lock


class SessionTracker:
    def __init__(self) -> None:
        self._active: dict[tuple[str, str], str] = {}
        self._pids: dict[tuple[str, str], int] = {}
        # Additive: map session_id → (org_slug, task_id, agent_name) for
        # four-part server-authoritative provenance.  Callers that supply an
        # org_slug on set_active() populate this index; callers that don't
        # (legacy, tests) leave it empty and get_by_session still returns
        # only (task_id, agent_name) for backward compatibility.
        self._context_by_session: dict[str, tuple[str, str, str]] = {}
        self._lock = Lock()
        # Per-(task_id, agent_name) lease map for linearizing the agent
        # proposal route's authorization+persistence span against
        # clear()/set_active() on the SAME binding.  Each key maps to
        # a threading.Lock that is acquired by the proposal route
        # (around authorization + persistence) and by clear()/set_active()
        # (around session mutation).  This ensures same-binding mutual
        # exclusion WITHOUT serializing unrelated task/agent pairs
        # through a single SessionTracker-wide mutex.
        #
        # Guarded by self._lock during creation only; the Lock objects
        # themselves provide the per-binding synchronization.
        self._binding_leases: dict[tuple[str, str], Lock] = {}
        # Test seam: threading.Event that, when set (non-None), pauses
        # the proposal route after acquiring the binding lease and
        # re-verifying the session but BEFORE persistence.  Tests use
        # this to prove clear()/set_active() blocking and nonblocking
        # interleaving.  None in production.
        self._proposal_barrier: threading.Event | None = None
        # Test seam: set by the route when it reaches _proposal_barrier.
        # Tests wait on this to know the barrier has been reached before
        # driving concurrent clear()/set_active().
        self._barrier_reached: threading.Event | None = None

    def _get_binding_lease(self, task_id: str, agent: str) -> Lock:
        """Return the per-binding Lock for (task_id, agent).

        Creates a new Lock if one doesn't exist.  The caller must
        acquire the returned Lock (typically via a context manager).
        The Lock persists in the map; cleanup is not required for
        correctness and the objects are small.
        """
        key = (task_id, agent)
        with self._lock:
            lock = self._binding_leases.get(key)
            if lock is None:
                lock = Lock()
                self._binding_leases[key] = lock
            return lock

    def set_active(self, task_id: str, agent: str, session_id: str, *, org_slug: str | None = None) -> None:
        binding_lease = self._get_binding_lease(task_id, agent)
        with binding_lease:
            with self._lock:
                old_session_id = self._active.get((task_id, agent))
                self._active[(task_id, agent)] = session_id
                if old_session_id is not None and old_session_id != session_id:
                    # Invalidate the superseded session's context so stale
                    # opaque capabilities cannot create proposals.
                    self._context_by_session.pop(old_session_id, None)
                if org_slug is not None:
                    self._context_by_session[session_id] = (org_slug, task_id, agent)

    def get_active(self, task_id: str, agent: str) -> str | None:
        with self._lock:
            return self._active.get((task_id, agent))

    def get_by_session(self, session_id: str) -> tuple[str, str] | None:
        """Reverse lookup: given an opaque session_id, return (task_id, agent_name).

        Returns None when the session_id is not active, expired, or unknown.
        When multiple (task_id, agent) pairs share the same session_id
        (should not happen with UUID-v4 session ids), returns the first match.

        For four-part provenance, use get_context_by_session() which also
        returns the org_slug when available.
        """
        with self._lock:
            for (task_id, agent), sid in self._active.items():
                if sid == session_id:
                    return task_id, agent
            return None

    def get_context_by_session(self, session_id: str) -> tuple[str, str, str] | None:
        """Return (org_slug, task_id, agent_name) for an opaque session_id.

        Only populated when the caller of set_active() supplied org_slug.
        Returns None for unknown, inactive, expired, or context-free sessions.
        """
        with self._lock:
            return self._context_by_session.get(session_id)

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

    def count_active(self) -> int:
        with self._lock:
            return len(self._active)

    def clear(self, task_id: str, agent: str) -> None:
        binding_lease = self._get_binding_lease(task_id, agent)
        with binding_lease:
            with self._lock:
                old_session_id = self._active.pop((task_id, agent), None)
                self._pids.pop((task_id, agent), None)
                if old_session_id is not None:
                    # Invalidate the cleared session's context so completed/
                    # cancelled/revoked opaque capabilities cannot create proposals.
                    self._context_by_session.pop(old_session_id, None)
