"""Per-org transfer fence — admission lease (THR-187 Slice B).

The transfer fence is an asyncio reader/writer lease, not a checkable flag.

* **Admissions** (every producer of durable task / thread-invocation / job /
  scheduler work for an org) hold a *reader lease* around the durable mutation
  of captured state via ``async with org.transfer_fence.admission():``.
* **The exporter** holds the *writer lease* via ``acquire()`` / ``release()``
  around its final quiescence recheck + SQLite backup + allow-listed capture.

Linearizability guarantees (documented lease semantics):

1. ``acquire()`` (writer) atomically sets ``held`` and then *waits* for every
   in-flight admission to drain (``readers == 0``) before returning. An
   admission that started before the fence was acquired therefore completes
   its durable write *before* ``acquire()`` returns, so the export's
   under-lock final recheck observes it (and refuses if it made the org
   non-quiescent). Nothing can land between the final recheck and the capture.
2. Once ``held`` is set, every new ``admission()`` raises ``TransferFenceHeld``
   (mapped to HTTP 409 by ``app.py``) and lands nothing. No admission can pass
   the fence and then mutate captured state until the backup and allow-listed
   capture are complete.
3. The initial Slice-A preflight is read-only and is **not** atomic; the
   consistency boundary is ``acquire() → recheck → backup → capture`` alone.

Concurrency model: all admission seams (daemon routes + scheduler loops) run on
the daemon's single asyncio event loop, so the primitive is asyncio-native
(``asyncio.Condition``). ``run_step`` worker threads never create tasks /
invocations / jobs — they only advance an already-admitted task, so they do not
touch the fence (see the PR Native Impact Evidence for the per-producer call
site argument). The plain ``held`` / ``active_admissions`` properties are
best-effort predicates for diagnostics and fast-path skips; they are not an
authorization boundary.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator


class TransferFenceHeld(Exception):
    """Raised by an admission seam when the org transfer fence is held."""


class TransferFence:
    def __init__(self) -> None:
        self._cond = asyncio.Condition()
        self._held = False
        self._readers = 0

    @property
    def held(self) -> bool:
        """Best-effort predicate (diagnostics / fast-path skips only)."""
        return self._held

    @property
    def active_admissions(self) -> int:
        """Best-effort count of in-flight admission leases."""
        return self._readers

    async def acquire(self) -> bool:
        """Claim the fence exclusively (the exporter's writer lease).

        Returns ``False`` if the fence is already held by another exporter.
        Otherwise sets ``held`` and waits until every in-flight admission has
        drained (``readers == 0``) before returning — so any admission that
        started before this call has committed its durable write and will be
        observed by the exporter's subsequent recheck.
        """
        async with self._cond:
            if self._held:
                return False
            self._held = True
            while self._readers > 0:
                await self._cond.wait()
            return True

    async def release(self) -> None:
        """Release the exporter's writer lease."""
        async with self._cond:
            self._held = False
            self._cond.notify_all()

    @asynccontextmanager
    async def admission(self) -> AsyncIterator[None]:
        """Enter an admission reader lease.

        Raises :class:`TransferFenceHeld` if the fence is held. The caller must
        keep the lease for the whole durable mutation of captured state (e.g.
        ``insert_task`` + enqueue, ``mint_thread_invocation`` + thread-queue
        put, ``insert_job``). The check-and-increment is atomic under the same
        condition as ``acquire``, so there is no window in which an admission
        can pass its guard and then write after the exporter acquires the fence.
        """
        async with self._cond:
            if self._held:
                raise TransferFenceHeld(
                    "transfer fence held for this org; new admission refused"
                )
            self._readers += 1
        try:
            yield
        finally:
            async with self._cond:
                self._readers -= 1
                if self._readers == 0:
                    self._cond.notify_all()
