"""Thread-safe maintenance admission/drain/exclusivity gate (TASK-5443).

The daemon-global metrics maintenance operation (prune + WAL checkpoint +
integrity check + ``VACUUM``) must never run concurrently with normal traffic,
the periodic snapshot writer, or a second maintenance invocation.  This gate is
the *admission/drain/exclusivity* primitive that makes that guarantee explicit.

It coordinates three threads:

* the **event-loop thread** — the HTTP admission middleware and the scheduler
  loops (which call the periodic snapshot writer);
* the **threadpool thread** — the synchronous ``POST /api/v1/metrics/maintenance``
  FastAPI route handler (FastAPI runs sync handlers off the event loop);
* **any second maintenance caller** — deterministically rejected.

A ``threading.Condition`` (not an ``asyncio`` primitive) is the right tool
because the maintenance route handler is synchronous and runs in a threadpool
thread, while the middleware/scheduler side only ever holds the condition's
lock for a bounded, non-async critical section.

State machine::

    OPEN ──try_enter_pending()──▶ PENDING ──mark_active()──▶ ACTIVE
      ▲                              │                        │
      └───────────release()──────────┴────────────────────────┘

* ``OPEN``    — normal traffic flows; the snapshot writer may write.
* ``PENDING`` — atomically entered by the single winning maintenance caller;
                new normal traffic is rejected and already-admitted requests
                are drained; the periodic snapshot writer skips.
* ``ACTIVE``  — quiescence has been re-checked; the blocking maintenance
                sequence runs.

``release()`` must be called on every success *and* failure path so the gate
always returns to ``OPEN``.
"""
from __future__ import annotations

import threading
import time as _time


class MaintenanceGate:
    """Admission/drain/exclusivity gate for the metrics maintenance operation."""

    OPEN = "open"
    PENDING = "pending"
    ACTIVE = "active"

    def __init__(self) -> None:
        self._cond = threading.Condition(threading.Lock())
        self._phase = self.OPEN
        self._admitted = 0

    # ------------------------------------------------------------------
    # Admission (event-loop HTTP middleware)
    # ------------------------------------------------------------------

    def admit(self) -> bool:
        """Atomically admit one normal request iff the gate is OPEN.

        Returns ``False`` when maintenance is pending/active — the caller must
        reject the request.  This is atomic with respect to
        ``try_enter_pending`` (both hold the same condition lock), so a
        request cannot slip through the instant the gate closes.
        """
        with self._cond:
            if self._phase != self.OPEN:
                return False
            self._admitted += 1
            return True

    def finish(self) -> None:
        """Mark one previously admitted request complete (called in ``finally``)."""
        with self._cond:
            self._admitted -= 1
            if self._admitted == 0:
                self._cond.notify_all()

    # ------------------------------------------------------------------
    # Maintenance (threadpool route handler)
    # ------------------------------------------------------------------

    def try_enter_pending(self) -> bool:
        """Atomically transition OPEN → PENDING.

        Returns ``False`` when the gate is already PENDING/ACTIVE — this is
        the deterministic overlap rejection: only one maintenance call wins.
        """
        with self._cond:
            if self._phase != self.OPEN:
                return False
            self._phase = self.PENDING
            return True

    def drain(self, timeout: float) -> bool:
        """Wait until every already-admitted request has finished.

        Returns ``False`` on timeout (the caller must release the gate and
        surface a structured failure).  Bounded so a long-lived admitted
        request cannot wedge the maintenance call forever.
        """
        deadline = _time.monotonic() + timeout
        with self._cond:
            while self._admitted > 0:
                remaining = deadline - _time.monotonic()
                if remaining <= 0:
                    return False
                self._cond.wait(timeout=remaining)
            return True

    def mark_active(self) -> None:
        """Transition PENDING → ACTIVE (quiescence re-checked; operation starts)."""
        with self._cond:
            self._phase = self.ACTIVE

    def release(self) -> None:
        """Return the gate to OPEN.  Call on every success/failure path."""
        with self._cond:
            self._phase = self.OPEN
            self._cond.notify_all()

    # ------------------------------------------------------------------
    # Observers (event-loop scheduler loops)
    # ------------------------------------------------------------------

    def is_maintenance_in_progress(self) -> bool:
        """True while PENDING or ACTIVE — the snapshot writer must skip."""
        with self._cond:
            return self._phase != self.OPEN
