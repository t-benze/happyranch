from __future__ import annotations

import functools
import logging
import time as _time
from datetime import datetime


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _synchronized(method):
    """Serialize every public ``Database`` call through ``self._lock``.

    Why: the daemon shares ONE sqlite3 connection across the event-loop thread
    (async routes) and the threadpool thread running ``Orchestrator.run_step``
    (see ``src/daemon/queue.py``). ``DaemonState.db_lock`` is an ``asyncio.Lock``
    and can't serialize against threads; ``check_same_thread=False`` on the
    connection allows cross-thread access but not concurrent cursor/exec ops —
    overlap raises ``sqlite3.InterfaceError`` or hands back rows with None-valued
    columns. A ``threading.RLock`` inside ``Database`` closes that gap without
    per-thread connections or a migration.

    Lock instrumentation (THR-129): times wait duration (acquire) and hold
    duration (method body). Warns when either exceeds the instance's
    ``_lock_warn_threshold_seconds`` (default 1.0 s). RLock reentrancy is
    respected — nested acquires show near-zero wait time.
    """
    _db_logger = logging.getLogger("happyranch.database.lock")

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        threshold = getattr(self, '_lock_warn_threshold_seconds', 1.0)
        t_wait_start = _time.monotonic()
        self._lock.acquire(blocking=True)
        wait_sec = _time.monotonic() - t_wait_start
        if wait_sec > threshold:
            _db_logger.warning(
                "Database._lock wait %.3fs > threshold %.3fs "
                "for %s.%s (lock convoy may stall other routes)",
                wait_sec, threshold,
                type(self).__name__, method.__name__,
            )
        try:
            t_hold_start = _time.monotonic()
            result = method(self, *args, **kwargs)
            hold_sec = _time.monotonic() - t_hold_start
            if hold_sec > threshold:
                _db_logger.warning(
                    "Database._lock hold %.3fs > threshold %.3fs "
                    "for %s.%s",
                    hold_sec, threshold,
                    type(self).__name__, method.__name__,
                )
            return result
        finally:
            self._lock.release()
    return wrapper
