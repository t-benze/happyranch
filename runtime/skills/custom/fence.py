"""Process-local serialization for custom-skill publication and tombstones."""

from __future__ import annotations

from contextlib import contextmanager
from threading import Condition, RLock, get_ident

_registry_lock = RLock()
class _ReadWriteFence:
    def __init__(self) -> None:
        self.condition = Condition(RLock())
        self.readers: dict[int, int] = {}
        self.writer: int | None = None
        self.writer_depth = 0
        self.waiting_writers = 0

    def acquire_read(self) -> None:
        owner = get_ident()
        with self.condition:
            while self.writer not in (None, owner) or (
                self.waiting_writers and owner not in self.readers
            ):
                self.condition.wait()
            self.readers[owner] = self.readers.get(owner, 0) + 1

    def release_read(self) -> None:
        owner = get_ident()
        with self.condition:
            depth = self.readers[owner] - 1
            if depth:
                self.readers[owner] = depth
            else:
                del self.readers[owner]
            self.condition.notify_all()

    def acquire_write(self) -> None:
        owner = get_ident()
        with self.condition:
            if self.writer == owner:
                self.writer_depth += 1
                return
            self.waiting_writers += 1
            try:
                while self.writer is not None or sum(self.readers.values()) != self.readers.get(owner, 0):
                    self.condition.wait()
                self.writer = owner
                self.writer_depth = 1
            finally:
                self.waiting_writers -= 1

    def release_write(self) -> None:
        with self.condition:
            self.writer_depth -= 1
            if self.writer_depth == 0:
                self.writer = None
                self.condition.notify_all()


_org_locks: dict[str, _ReadWriteFence] = {}


def _lock_for(org_slug: str) -> _ReadWriteFence:
    with _registry_lock:
        return _org_locks.setdefault(org_slug, _ReadWriteFence())


@contextmanager
def custom_skill_publication_fence(org_slug: str, *, write: bool = False):
    """Exclude an org tombstone commit from resolution through publication.

    The lock is deliberately process-local: all supported producers and the
    synchronous purge route execute in the daemon process.  It is acquired
    before workspace/DB locks and released after both provider roots publish.
    Process death releases it; SQLite remains the durable authority.
    """
    fence = _lock_for(org_slug)
    acquire = fence.acquire_write if write else fence.acquire_read
    release = fence.release_write if write else fence.release_read
    acquire()
    try:
        yield
    finally:
        release()
