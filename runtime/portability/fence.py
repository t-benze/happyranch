"""Per-org transfer fence (THR-187 Slice B).

While an org-portability export holds a source org's transfer fence, the
daemon's admission seams refuse to admit new dispatch / invocation / scheduler
work for that org. This closes the race between the export's quiescence
recheck and its SQLite backup + allow-listed capture: a founder/thread/schedule
admission that races the capture must fail with a conflict instead of landing
between the recheck and the snapshot.

The fence is a plain thread-safe flag, not a lock the admission seams acquire:
``acquire()`` is the exporter claiming the fence exclusively; ``held`` is the
non-mutating predicate the admission seams consult. It is thread-safe because
the daemon admits work from asyncio routes, background scheduler loops, and
``run_step`` worker threads.
"""
from __future__ import annotations

import threading


class TransferFenceHeld(Exception):
    """Raised by an admission seam when the org transfer fence is held."""


class TransferFence:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._held = False

    def acquire(self) -> bool:
        """Claim the fence exclusively. Returns ``False`` if already held."""
        with self._lock:
            if self._held:
                return False
            self._held = True
            return True

    def release(self) -> None:
        with self._lock:
            self._held = False

    @property
    def held(self) -> bool:
        with self._lock:
            return self._held
