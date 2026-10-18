"""Coherent canonical reads and the short online-move replacement window.

Process-local cooperation only. No DB/gate is acquired here; callers must enter
before DB ownership, never across an await. Direct external edits still require
exact byte/identity revalidation by the operation owner.
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

_locks: dict[str, threading.RLock] = {}
_registry_lock = threading.Lock()
# Ordinary readers hold the roster for a short file read, so a concurrent
# reader usually frees it within milliseconds. A bounded wait absorbs that
# contention while keeping the no-deadlock property: a waiter that already
# owns DB still gives up and defers rather than blocking indefinitely.
_ACQUIRE_TIMEOUT_SECONDS = 0.25


@contextmanager
def canonical_roster(root: Path):
    key = os.path.abspath(root)
    with _registry_lock:
        lock = _locks.setdefault(key, threading.RLock())
    # Some legacy readers already own DB. Never wait unboundedly behind a file
    # owner that may next need DB for a snapshot: after the short bounded wait,
    # defer instead of introducing reverse order.
    if not lock.acquire(timeout=_ACQUIRE_TIMEOUT_SECONDS):
        from runtime.infrastructure.team_move_store import TeamMoveError
        raise TeamMoveError("team_move_canonical_replacement_busy")
    try:
        yield
    finally:
        lock.release()


def coherent_paths_reader(function):
    @wraps(function)
    def read(paths, *args, **kwargs):
        with canonical_roster(paths.root):
            return function(paths, *args, **kwargs)
    return read
