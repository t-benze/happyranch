"""In-memory tracker for the active session + subprocess PID per (task_id, agent).

The session_id half gates agent callbacks (409 `unknown_session` if the
caller's session isn't current). The pid half exists so `/tasks/{id}/cancel`
can deliver cancellation to every live subprocess attached to a cancelled
subtree without grepping the process table.

THR-207 task-producer wiring: the opaque **cancel control** half is the
cancellation authority for wired (contained) task sessions. The cancel route
invokes the registered control (which drives the HostSessionSupervisor's
opaque containment handle) instead of signalling the PID directly; the PID is
retained as diagnostic/restart evidence only and is never the cancellation
path for a session that holds a control.

The PID diagnostics and opaque cancel controls are **versioned by
session_id**: a registration can only ever touch its own generation's slot, a
superseded invocation's late registration is rejected (no-op) and its terminal
cleanup is a no-op, and every lookup/iteration resolves the CURRENTLY active
generation's control/PID — cancellation can never invoke an old or
already-terminal token.
"""
from __future__ import annotations

from collections.abc import Callable
from threading import Lock


class SessionTracker:
    def __init__(self) -> None:
        self._active: dict[tuple[str, str], str] = {}
        # PID diagnostics, versioned by session_id: (task_id, agent) ->
        # {session_id: pid}. A registration can only ever touch its OWN
        # generation's slot — a superseded invocation can never overwrite the
        # newer generation's diagnostic pid.
        self._pids: dict[tuple[str, str], dict[str, int]] = {}
        # Opaque cancellation controls for wired (contained) task sessions
        # (THR-207): (task_id, agent) -> {session_id: callable} that cancels
        # the logical invocation through the HostSessionSupervisor's opaque
        # containment handle. Versioned by session_id like the PID half, so
        # registration is generation-owned. Invoked by the /tasks/{id}/cancel
        # route — which resolves ONLY the currently active generation's
        # control; the PID is never the cancellation authority for a session
        # that holds a control.
        self._cancel_controls: dict[tuple[str, str], dict[str, Callable[[], None]]] = {}
        # Additive: map session_id → (org_slug, task_id, agent_name) for
        # four-part server-authoritative provenance.  Callers that supply an
        # org_slug on set_active() populate this index; callers that don't
        # (legacy, tests) leave it empty and get_by_session still returns
        # only (task_id, agent_name) for backward compatibility.
        self._context_by_session: dict[str, tuple[str, str, str]] = {}
        self._lock = Lock()
        # Per-(task_id, agent_name) lease map for linearizing session-bound
        # B2 custom-skill creation against clear()/set_active() on the SAME
        # binding. Each key maps to a threading.Lock acquired around B2
        # creation and session mutation. This ensures same-binding mutual
        # exclusion WITHOUT serializing unrelated task/agent pairs through
        # a single SessionTracker-wide mutex.
        #
        # Guarded by self._lock during creation only; the Lock objects
        # themselves provide the per-binding synchronization.
        self._binding_leases: dict[tuple[str, str], Lock] = {}

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
                if old_session_id is not None and old_session_id != session_id:
                    # Supersession: the (task_id, agent) binding is owned by
                    # exactly ONE generation. The superseded generation's PID
                    # diagnostics and opaque cancel control die with the
                    # active flip (atomically, under the same lock), so a
                    # stale control can never cancel a later session of the
                    # same pair. Its context is invalidated too, so stale
                    # opaque capabilities cannot create B2 custom skills.
                    self._pids.pop((task_id, agent), None)
                    self._cancel_controls.pop((task_id, agent), None)
                    self._context_by_session.pop(old_session_id, None)
                self._active[(task_id, agent)] = session_id
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

    def set_pid(self, task_id: str, agent: str, session_id: str, pid: int) -> None:
        """Register the OS pid for an already-set-active session.

        Called from the executor's on_started callback after Popen returns,
        carrying the owning invocation's session_id. The pid is stored under
        the (task_id, agent, session_id) generation: a superseded
        invocation's late registration is rejected (no-op) and can never
        overwrite the newer generation's diagnostic pid. If `set_active`
        hasn't been called yet (unit tests, odd ordering), the pid is still
        stored — lookups resolve through the active generation only, which is
        fine because cancel only needs the current generation's pid.
        """
        binding_lease = self._get_binding_lease(task_id, agent)
        with binding_lease:
            with self._lock:
                active = self._active.get((task_id, agent))
                if active is None or active == session_id:
                    self._pids.setdefault((task_id, agent), {})[session_id] = pid

    def get_pid(self, task_id: str, agent: str) -> int | None:
        """Return the diagnostic pid of the CURRENTLY active session, or None.

        Resolves through the active generation only — a superseded or
        orphaned generation's pid is never reported.
        """
        with self._lock:
            active = self._active.get((task_id, agent))
            if active is None:
                return None
            by_session = self._pids.get((task_id, agent))
            if by_session is None:
                return None
            return by_session.get(active)

    def iter_task_pids(self, task_id: str) -> list[tuple[str, int]]:
        """Return (agent, pid) for every CURRENTLY active session's pid under ``task_id``.

        Diagnostic/restart evidence only for wired sessions — cancellation
        goes through the opaque control (see :meth:`set_cancel_control` /
        :meth:`iter_task_cancel_controls`), never a bare PID signal. A
        superseded/orphaned generation's pid is never reported. Returns a
        snapshot — safe to iterate without holding the lock.
        """
        with self._lock:
            result: list[tuple[str, int]] = []
            for (tid, agent), by_session in self._pids.items():
                if tid != task_id:
                    continue
                active = self._active.get((tid, agent))
                if active is None:
                    continue
                pid = by_session.get(active)
                if pid is not None:
                    result.append((agent, pid))
            return result

    def set_cancel_control(self, task_id: str, agent: str, session_id: str, cancel: Callable[[], None]) -> None:
        """Register the opaque cancellation control for a wired session.

        ``cancel`` is the logical invocation's ``CancellationToken.cancel``
        (drives the supervisor's containment handle), registered by the
        invocation that owns ``session_id`` BEFORE it enters admission so the
        cancel route can cancel a queued request without launch. The control
        is stored under the (task_id, agent, session_id) generation: a
        superseded invocation's late registration is rejected (no-op) and can
        never overwrite the newer generation's control. Latest-wins only
        WITHIN a generation, mirroring ``set_active``.
        """
        binding_lease = self._get_binding_lease(task_id, agent)
        with binding_lease:
            with self._lock:
                active = self._active.get((task_id, agent))
                if active is None or active == session_id:
                    self._cancel_controls.setdefault((task_id, agent), {})[session_id] = cancel

    def get_cancel_control(self, task_id: str, agent: str) -> Callable[[], None] | None:
        """Return the opaque control of the CURRENTLY active session, or None.

        Resolves through the active generation only — cancellation can never
        invoke an old/already-terminal token.
        """
        with self._lock:
            active = self._active.get((task_id, agent))
            if active is None:
                return None
            by_session = self._cancel_controls.get((task_id, agent))
            if by_session is None:
                return None
            return by_session.get(active)

    def iter_task_cancel_controls(self, task_id: str) -> list[tuple[str, Callable[[], None]]]:
        """Return (agent, cancel) for every CURRENTLY active session's control under ``task_id``.

        Used by /cancel to drive containment teardown for the whole subtree —
        only the active generation's control per (task, agent), so a stale or
        already-terminal token is never invoked. Returns a snapshot — safe to
        iterate without holding the lock.
        """
        with self._lock:
            result: list[tuple[str, Callable[[], None]]] = []
            for (tid, agent), by_session in self._cancel_controls.items():
                if tid != task_id:
                    continue
                active = self._active.get((tid, agent))
                if active is None:
                    continue
                cancel = by_session.get(active)
                if cancel is not None:
                    result.append((agent, cancel))
            return result

    def count_active(self) -> int:
        with self._lock:
            return len(self._active)

    def clear(self, task_id: str, agent: str) -> None:
        binding_lease = self._get_binding_lease(task_id, agent)
        with binding_lease:
            with self._lock:
                old_session_id = self._active.pop((task_id, agent), None)
                self._pids.pop((task_id, agent), None)
                # The opaque cancel control dies with the session — a stale
                # control must never cancel a later session of the same pair.
                self._cancel_controls.pop((task_id, agent), None)
                if old_session_id is not None:
                    # Invalidate the cleared session's context so completed/
                    # cancelled/revoked opaque capabilities cannot create B2 custom skills.
                    self._context_by_session.pop(old_session_id, None)

    def clear_if_active_session(self, task_id: str, agent: str, session_id: str) -> bool:
        """Clear the session binding only when it still belongs to ``session_id``.

        THR-207 task-producer terminal cleanup: the supervisor's terminal hook
        calls this on every final terminal path so the opaque cancel control,
        PID diagnostic, and active-session record die with the invocation.
        Ownership/generation-safe: a NEWER logical invocation for the same
        (task_id, agent) supersedes ``set_active`` with its own session_id, so
        this guard refuses to clear a newer session — an old attempt can never
        wipe a newer attempt's control/pid/session. Returns True when the
        binding was cleared, False when it was absent or owned by a different
        (newer) session.
        """
        binding_lease = self._get_binding_lease(task_id, agent)
        with binding_lease:
            with self._lock:
                if self._active.get((task_id, agent)) != session_id:
                    return False
                old_session_id = self._active.pop((task_id, agent), None)
                self._pids.pop((task_id, agent), None)
                # The opaque cancel control dies with the session — a stale
                # control must never cancel a later session of the same pair.
                self._cancel_controls.pop((task_id, agent), None)
                if old_session_id is not None:
                    # Invalidate the cleared session's context so completed/
                    # cancelled/revoked opaque capabilities cannot create B2 custom skills.
                    self._context_by_session.pop(old_session_id, None)
                return True
