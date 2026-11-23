"""Held original issuer/minter exclusion for explicit retirement.

No lock is initialized, no journal is repaired and no artifact is removed.
Absent lock domains remain explicit absence, never a writer-death claim.
Cleanup/recovery must separately account for their intentional mutations.
"""
from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import PurePosixPath
from typing import Iterator

from runtime.remote_access.home_retirement import RetirementUncertain, STATE_DIRECTORY, _FILE_FLAGS, _signature
from runtime.remote_access.home_retirement_inventory import LegacyInventory, _AuxiliaryMembers
from runtime.remote_access.home_retirement_journal import OriginalInventory, RetirementJournal


class AuxiliaryExclusion:
    """Live held descriptions plus unchanged original auxiliary evidence.

    Only ``held_auxiliary_operations`` supplies a live instance. The saved
    original inventory establishes the closed paths/custody; every use checks
    the current names and held descriptions against those original facts.
    """

    def __init__(self, journal: RetirementJournal, original: OriginalInventory) -> None:
        self.journal, self.original = journal, original
        self._descriptors: dict[str, int] = {}
        self._live = False

    def __repr__(self) -> str:
        return 'AuxiliaryExclusion(<redacted>)'

    def _original_members(self) -> None:
        journal, original = self.journal, self.original
        journal.fence.require(journal.io)
        if journal.load() != original:
            raise RetirementUncertain()
        walker = _AuxiliaryMembers(LegacyInventory(journal.io), original.ownership, journal.fence)
        members = original.auxiliary.members
        for relative in sorted({str(PurePosixPath(path).parent) for path in members}):
            with walker._directory(relative) as parent:
                for path, expected in members.items():
                    if str(PurePosixPath(path).parent) != relative:
                        continue
                    try:
                        current = None if parent is None else _signature(os.stat(
                            PurePosixPath(path).name, dir_fd=parent, follow_symlinks=False))
                    except FileNotFoundError:
                        current = None
                    if current != expected:
                        raise RetirementUncertain()
        for path, expected in original.auxiliary.directories.items():
            current = walker.directory_identities.get(path)
            # Original record publication deliberately changed only this
            # directory's contents metadata, never its inode/custody/mode.
            if current is None or (current[:5] != expected[:5] if path == STATE_DIRECTORY else current != expected):
                raise RetirementUncertain()
        journal.fence.require(journal.io)

    def require(self) -> None:
        try:
            if not self._live:
                raise RetirementUncertain()
            self._original_members()
            for path, descriptor in self._descriptors.items():
                if _signature(os.fstat(descriptor)) != self.original.auxiliary.members[path]:
                    raise RetirementUncertain()
                # Reassert on the same open description; never replace it with
                # a freshly opened name or release it between effect guards.
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._original_members()
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
            raise RetirementUncertain() from None

    def _acquire(self) -> None:
        self._original_members()
        journal, original = self.journal, self.original
        walker = _AuxiliaryMembers(LegacyInventory(journal.io), original.ownership, journal.fence)
        issuer = _AuxiliaryMembers._ISSUER
        lock = issuer + '/invite-issuer.lock'
        if original.auxiliary.members[issuer] is not None and original.auxiliary.members[lock] is None:
            # Do not create a new serialization domain inside an empty
            # historical directory; an old issuer may still hold that parent.
            raise RetirementUncertain()
        paths = (lock, *(_AuxiliaryMembers._MINTER + '/' + user for user in _AuxiliaryMembers._USERS))
        for path in paths:
            expected = original.auxiliary.members[path]
            if expected is None:
                continue
            if path == lock:
                with walker._directory(issuer) as parent:
                    if parent is None:
                        raise RetirementUncertain()
                    descriptor = os.open('invite-issuer.lock', _FILE_FLAGS, dir_fd=parent)
                    self._descriptors[path] = descriptor
            else:
                with walker._directory(path) as parent:
                    if parent is None:
                        raise RetirementUncertain()
                    descriptor = os.dup(parent)
                    self._descriptors[path] = descriptor
            if _signature(os.fstat(descriptor)) != expected:
                raise RetirementUncertain()
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self._live = True
        self.require()

    def _close(self) -> None:
        self._live = False
        for descriptor in reversed(tuple(self._descriptors.values())):
            os.close(descriptor)
        self._descriptors.clear()


@contextmanager
def held_auxiliary_operations(journal: RetirementJournal, original: OriginalInventory) -> Iterator[AuxiliaryExclusion]:
    """Keep original locks held for the entire explicit operation's context.

    Acquiring one lock and then failing another releases all acquired locks.
    No auxiliary parser that samples a second file lock runs under our own
    exclusion. Current unchanged metadata is compared with the already parsed
    durable original evidence instead. This is not selective cleanup authority.
    """
    exclusion = AuxiliaryExclusion(journal, original)
    try:
        try:
            exclusion._acquire()
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
            raise RetirementUncertain() from None
        yield exclusion
        exclusion.require()
    finally:
        exclusion._close()
