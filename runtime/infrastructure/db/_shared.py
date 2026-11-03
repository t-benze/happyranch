from __future__ import annotations

import functools
import logging
import sys
from datetime import datetime


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _late_database_now() -> datetime:
    """Resolve the facade clock late for patched-global rule 3."""
    return sys.modules["runtime.infrastructure.database"]._now()


def _synchronized(method):
    """Serialize every public ``Database`` call through ``self._lock``.

    Why: the daemon shares ONE sqlite3 connection across the event-loop thread
    (async routes) and the threadpool thread running ``Orchestrator.run_step``
    (see ``runtime/daemon/queue.py``). ``DaemonState.db_lock`` is an ``asyncio.Lock``
    and can't serialize against threads; ``check_same_thread=False`` on the
    connection allows cross-thread access but not concurrent cursor/exec ops —
    overlap raises ``sqlite3.InterfaceError`` or hands back rows with None-valued
    columns. A ``threading.RLock`` inside ``Database`` closes that gap without
    per-thread connections or a migration.

    Lock instrumentation (THR-129): times wait duration (acquire) and hold
    duration (method body). Warns when either exceeds the instance's
    ``_lock_warn_threshold_seconds`` (default 1.0 s). RLock reentrancy is
    respected — nested acquires show near-zero wait time.

    The first actual failure keeps its object and original traceback. Private
    per-call evidence retains later release/attachment failures and any exact
    completed body result; it never turns a failed tail into a successful call.
    """
    _db_logger = logging.getLogger("happyranch.database.lock")

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        # Keep this local even if publishing it on the exception fails: the
        # rethrown traceback retains this frame and its exact evidence. Nested
        # wrappers link records rather than replacing an inner call's result.
        failure = {
            "method": method,
            "lock": None,
            "acquire_returned": False,
            "release_attempted": False,
            "release_returned": False,
            "body_returned": False,
            "result": None,
            "first_error": None,
            "release_error": None,
            "retention_error": None,
            "previous": None,
        }
        phase = "wait instrumentation"
        try:
            _time = sys.modules["runtime.infrastructure.database"]._time
            threshold = getattr(self, '_lock_warn_threshold_seconds', 1.0)
            t_wait_start = _time.monotonic()
            lock = self._lock
            failure["lock"] = lock
            phase = "acquire"
            if lock.acquire(blocking=True) is not True:
                raise RuntimeError("Database lock acquisition was not acknowledged")
            failure["acquire_returned"] = True
            phase = "wait instrumentation"
            wait_sec = _time.monotonic() - t_wait_start
            if wait_sec > threshold:
                _db_logger.warning(
                    "Database._lock wait %.3fs > threshold %.3fs "
                    "for %s.%s (lock convoy may stall other routes)",
                    wait_sec, threshold,
                    type(self).__name__, method.__name__,
                )
            t_hold_start = _time.monotonic()
            phase = "body"
            failure["result"] = method(self, *args, **kwargs)
            failure["body_returned"] = True
            phase = "hold instrumentation"
            hold_sec = _time.monotonic() - t_hold_start
            if hold_sec > threshold:
                _db_logger.warning(
                    "Database._lock hold %.3fs > threshold %.3fs "
                    "for %s.%s",
                    hold_sec, threshold,
                    type(self).__name__, method.__name__,
                )
        except BaseException as primary:
            failure["first_error"] = (
                phase, primary, BaseException.__traceback__.__get__(primary),
            )
        finally:
            if failure["acquire_returned"]:
                # A raised release can follow an effect. Never retry or infer
                # successful release/native retirement from an attempted call.
                failure["release_attempted"] = True
                try:
                    lock.release()
                    failure["release_returned"] = True
                except BaseException as later:
                    failure["release_error"] = (
                        "release", later, BaseException.__traceback__.__get__(later),
                    )
                    if failure["first_error"] is None:
                        failure["first_error"] = failure["release_error"]
        if failure["first_error"] is not None:
            _, primary, original_traceback = failure["first_error"]
            try:
                values = BaseException.__getattribute__(primary, "__dict__")
                failure["previous"] = values.get("_database_synchronized_failure")
                BaseException.__setattr__(primary, "_database_synchronized_failure", failure)
            except BaseException as later:
                failure["retention_error"] = (
                    later, BaseException.__traceback__.__get__(later),
                )
            raise BaseException.with_traceback(primary, original_traceback)
        return failure["result"]
    return wrapper


def _task_move_admission(method):
    """Outer move gate for task allocation/claims, before the DB decorator.

    A caller-owned DB transaction must use its uncommitted check directly,
    never acquire another gate beneath DB. Its allocation is durable before
    the move writer can commit a hold; the move census must see that task.
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        from contextlib import nullcontext
        move = getattr(self, "_team_move_coordinator", None)
        gate = move.gate if move is not None and not self._lock._is_owned() else nullcontext()
        with gate:
            return method(self, *args, **kwargs)
    return wrapper
