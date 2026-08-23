"""Thread-safe maintenance admission/drain/exclusivity gate (TASK-5443).

The daemon-global metrics maintenance operation (prune + WAL checkpoint +
integrity check + ``VACUUM``) must never run concurrently with normal traffic,
the periodic snapshot writer, or a second maintenance invocation.  This gate is
the *admission/drain/exclusivity* primitive that makes that guarantee explicit.

It coordinates three threads:

* the **event-loop thread** — the HTTP admission middleware, the scheduler
  loops (which call the periodic snapshot writer), and the background
  task/work producers (``schedule_due_schedules`` / ``schedule_due_wakes``);
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

* ``OPEN``    — normal traffic flows; the snapshot writer may write; the
                background task/work producers may claim/insert/enqueue.
* ``PENDING`` — atomically entered by the single winning maintenance caller;
                new normal traffic and NEW background-work leases are rejected
                (both atomic with ``try_enter_pending`` via the same condition
                lock); already-admitted requests AND already-admitted
                background passes are drained; the periodic snapshot writer
                AND both background producers defer (due schedules stay ARMED /
                due wake slots stay unscheduled — nothing is dropped or
                consumed).
* ``ACTIVE``  — quiescence has been re-checked; the blocking maintenance
                sequence runs.  The producers continue to defer.

``release()`` must be called on every success *and* failure path so the gate
always returns to ``OPEN``.  Producers observe the gate via the single
authoritative ``background_lease()`` API (atomic admission spanning their
ENTIRE authoritative claim/insert/audit/enqueue path) — no ad-hoc state
checks.  The condition mutex is never held across SQLite or queue operations:
only the admission increment and the release decrement touch it.
"""
from __future__ import annotations

import threading
import time as _time
from contextlib import contextmanager
from typing import Iterator


class MaintenanceGate:
    """Admission/drain/exclusivity gate for the metrics maintenance operation."""

    OPEN = "open"
    PENDING = "pending"
    ACTIVE = "active"

    def __init__(self) -> None:
        self._cond = threading.Condition(threading.Lock())
        self._phase = self.OPEN
        self._admitted = 0
        self._background = 0
        self._background_admissions = 0

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
            if self._admitted == 0 and self._background == 0:
                self._cond.notify_all()

    # ------------------------------------------------------------------
    # Background-work admission (event-loop scheduler producers)
    # ------------------------------------------------------------------

    @contextmanager
    def background_lease(self) -> Iterator[bool]:
        """Atomically admit ONE background-work pass iff the gate is OPEN.

        Use as a context manager spanning the caller's ENTIRE authoritative
        path (schedule recovery/claim/insert/audit/queue enqueue, or a metrics
        snapshot compose/append/prune)::

            with gate.background_lease() as admitted:
                if not admitted:
                    return 0  # deferred with ZERO side effects
                ... run the whole producer pass ...

        * Yields ``True`` when admitted while ``OPEN`` — the caller runs its
          full authoritative pass; the lease is released in ``finally`` so an
          exception can never leak it (drain would otherwise wedge forever).
        * Yields ``False`` when maintenance is already pending/active — the
          caller must defer with zero side effects; the due item stays
          eligible in the DB and is handled on a later post-release tick.

        Atomic with ``try_enter_pending``: both hold the same condition lock,
        so a producer cannot slip past the instant the gate closes.  The
        condition mutex is NOT held across the caller's work (no SQLite/queue
        ops under the mutex) — only the admission increment and the release
        decrement touch it.
        """
        denied = False
        with self._cond:
            if self._phase != self.OPEN:
                denied = True
            else:
                self._background += 1
                self._background_admissions += 1
        if denied:
            yield False
            return
        try:
            yield True
        finally:
            self.finish_background()

    def finish_background(self) -> None:
        """Release one previously admitted background-work lease.

        Called by the ``background_lease`` context manager's ``finally`` path
        — never directly.
        """
        with self._cond:
            self._background -= 1
            if self._background == 0 and self._admitted == 0:
                self._cond.notify_all()

    @property
    def background_admissions(self) -> int:
        """Monotonic count of admitted background-work passes (never reset).

        Lets the scheduler loops advance their startup catch-up flag only when
        a producer pass was ACTUALLY admitted — a pass deferred at the atomic
        lease seam (maintenance won OPEN→PENDING just before the producer
        acquired its lease) must not consume the startup pass.
        """
        with self._cond:
            return self._background_admissions

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
        """Wait until every already-admitted request AND background-work lease
        has finished.

        Returns ``False`` on timeout (the caller must release the gate and
        surface a structured failure).  Bounded so a long-lived admitted
        request or producer pass cannot wedge the maintenance call forever.
        """
        deadline = _time.monotonic() + timeout
        with self._cond:
            while self._admitted > 0 or self._background > 0:
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
