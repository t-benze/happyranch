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


@contextmanager
def canonical_roster(root: Path):
    key = os.path.abspath(root)
    with _registry_lock:
        lock = _locks.setdefault(key, threading.RLock())
    # Some legacy readers already own DB. Never wait behind a file owner that
    # may next need DB for a snapshot. Defer instead of introducing reverse order.
    if not lock.acquire(blocking=False):
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
