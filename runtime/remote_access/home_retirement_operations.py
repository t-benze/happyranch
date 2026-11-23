"""Held original issuer/minter exclusion for explicit retirement.

No lock is initialized and no journal is repaired. Ordinary cleanup retains
original serialization domains. Their separate explicit removal requires
independent fresh native/cutoff proof, including after interrupted removal.
Absent lock domains remain explicit absence, never a writer-death claim.
"""
from __future__ import annotations

import fcntl
import os
import stat
from contextlib import contextmanager
from pathlib import PurePosixPath
from typing import Callable, Iterator

from runtime.remote_access.home_retirement import RetirementUncertain, STATE_DIRECTORY, _FILE_FLAGS, _signature
from runtime.remote_access.home_retirement_inventory import LegacyInventory, _AuxiliaryMembers
from runtime.remote_access.home_retirement_journal import OriginalInventory, RetirementJournal


class AuxiliaryExclusion:
    """Live held descriptions plus original auxiliary evidence.

    The held-operation context or explicit domain retirement supplies a live
    instance. The saved
    original inventory establishes the closed paths/custody; every use checks
    the current names and held descriptions against those original facts.
    Ordinary cleanup mode permits prior non-lock leaf absence only. Final
    domain removal additionally requires its mandatory independent proof.
    """

    def __init__(self, journal: RetirementJournal, original: OriginalInventory, *, cleanup: bool = False,
                 retirement_proof: Callable[[], None] | None = None) -> None:
        self.journal, self.original = journal, original
        self._cleanup = cleanup
        self._retirement_proof = retirement_proof
        self._descriptors: dict[str, int] = {}
        self._live = False

    def __repr__(self) -> str:
        return 'AuxiliaryExclusion(<redacted>)'

    @staticmethod
    def _locks() -> tuple[str, ...]:
        return (_AuxiliaryMembers._ISSUER + '/invite-issuer.lock',
            *(_AuxiliaryMembers._MINTER + '/' + user for user in _AuxiliaryMembers._USERS))

    @classmethod
    def _domains(cls) -> tuple[str, ...]:
        return (*cls._locks(), _AuxiliaryMembers._ISSUER, _AuxiliaryMembers._MINTER)

    def _original_members(self) -> dict[str, tuple[int, ...] | None]:
        journal, original = self.journal, self.original
        journal.fence.require(journal.io)
        if journal.load() != original:
            raise RetirementUncertain()
        walker = _AuxiliaryMembers(LegacyInventory(journal.io), original.ownership, journal.fence)
        members = original.auxiliary.members
        observed = {}
        for relative in sorted({str(PurePosixPath(path).parent) for path in members}):
            with walker._directory(relative, contents_may_change=self._cleanup) as parent:
                if self._cleanup and parent is not None and relative in (
                        _AuxiliaryMembers._ISSUER, _AuxiliaryMembers._MINTER,
                        *(_AuxiliaryMembers._MINTER + '/' + user for user in _AuxiliaryMembers._USERS)):
                    allowed = {PurePosixPath(path).name for path in members
                               if str(PurePosixPath(path).parent) == relative and members[path] is not None}
                    with os.scandir(parent) as entries:
                        for index, entry in enumerate(entries):
                            if index >= _AuxiliaryMembers._MAX_OPERATIONS or entry.name not in allowed:
                                raise RetirementUncertain()
                for path, expected in members.items():
                    if str(PurePosixPath(path).parent) != relative:
                        continue
                    try:
                        current = None if parent is None else _signature(os.stat(
                            PurePosixPath(path).name, dir_fd=parent, follow_symlinks=False))
                    except FileNotFoundError:
                        current = None
                    observed[path] = current
                    if self._retirement_proof is not None and path not in self._domains():
                        # Domain removal is last: opaque operation/issuer
                        # leaves and their publication must already be absent.
                        same = current is None
                    elif self._retirement_proof is not None and current is None:
                        same = True
                    elif self._cleanup and expected is not None and stat.S_ISDIR(expected[4]):
                        same = current is not None and current[:5] == expected[:5]
                    elif self._cleanup and current is None and path not in self._locks():
                        same = True
                    else:
                        same = current == expected
                    if not same:
                        raise RetirementUncertain()
        for path, expected in original.auxiliary.directories.items():
            current = walker.directory_identities.get(path)
            if self._retirement_proof is not None and current is None and path in self._domains():
                continue
            # Original record publication deliberately changed only this
            # directory's contents metadata, never its inode/custody/mode.
            if current is None or (current[:5] != expected[:5]
                    if self._cleanup or path == STATE_DIRECTORY else current != expected):
                raise RetirementUncertain()
        journal.fence.require(journal.io)
        return observed

    def require(self) -> None:
        try:
            if not self._live:
                raise RetirementUncertain()
            if self._retirement_proof is not None:
                self._retirement_proof()
            observed = self._original_members()
            for path, descriptor in self._descriptors.items():
                current, expected = _signature(os.fstat(descriptor)), self.original.auxiliary.members[path]
                removed = self._retirement_proof is not None and observed[path] is None
                fields = 5 if removed or self._cleanup and stat.S_ISDIR(expected[4]) else 9
                if current[:fields] != expected[:fields]:
                    raise RetirementUncertain()
                if removed and current[5] != 0:
                    raise RetirementUncertain()
                # Reassert on the same open description; never replace it with
                # a freshly opened name or release it between effect guards.
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._original_members()
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
            raise RetirementUncertain() from None

    def remove_leaves(self, *, before_effect: Callable[[], None]) -> None:
        """Remove only original non-lock regular leaves under fresh proofs.

        Originally present lock names and directories remain required. Fresh
        cleanup contexts can reconcile prior leaf absence without recapture.
        A required caller proof supplies stopped/native/cutoff authority; the
        held descriptions supply only cooperative writer exclusion.
        """
        try:
            if not self._cleanup:
                raise RetirementUncertain()
            journal, original = self.journal, self.original
            def guard() -> None:
                before_effect()
                self.require()
            guard()
            for path, expected in sorted(original.auxiliary.members.items()):
                if expected is None or path in self._locks() or stat.S_ISDIR(expected[4]):
                    continue
                if not stat.S_ISREG(expected[4]):
                    raise RetirementUncertain()
                guard()
                walker = _AuxiliaryMembers(LegacyInventory(journal.io), original.ownership, journal.fence)
                relative, name = str(PurePosixPath(path).parent), PurePosixPath(path).name
                with walker._directory(relative, contents_may_change=True) as parent:
                    if parent is None or any(current[:5] != original.auxiliary.directories[key][:5]
                            for key, current in walker.directory_identities.items()):
                        raise RetirementUncertain()
                    try:
                        current = _signature(os.stat(name, dir_fd=parent, follow_symlinks=False))
                    except FileNotFoundError:
                        current = None
                    if current is not None:
                        if current != expected:
                            raise RetirementUncertain()
                        os.unlink(name, dir_fd=parent)
                    # Reconcile a prior unlink whose parent sync was lost.
                    os.fsync(parent)
                    try:
                        os.stat(name, dir_fd=parent, follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    else:
                        raise RetirementUncertain()
            guard()
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
            raise RetirementUncertain() from None

    def _acquire(self) -> None:
        if self._retirement_proof is not None:
            self._retirement_proof()
        observed = self._original_members()
        journal, original = self.journal, self.original
        walker = _AuxiliaryMembers(LegacyInventory(journal.io), original.ownership, journal.fence)
        issuer = _AuxiliaryMembers._ISSUER
        lock = issuer + '/invite-issuer.lock'
        if original.auxiliary.members[issuer] is not None and original.auxiliary.members[lock] is None:
            # Do not create a new serialization domain inside an empty
            # historical directory; an old issuer may still hold that parent.
            raise RetirementUncertain()
        for path in self._locks():
            expected = original.auxiliary.members[path]
            if expected is None:
                continue
            if self._retirement_proof is not None and observed[path] is None:
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
            fields = 5 if self._cleanup and stat.S_ISDIR(expected[4]) else 9
            if _signature(os.fstat(descriptor))[:fields] != expected[:fields]:
                raise RetirementUncertain()
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self._live = True
        self.require()

    def remove_domains(self) -> None:
        """Remove only empty original domains under independent fresh proof.

        Surviving original locks stay held until context exit, including after
        unlink/rmdir. Fresh recovery can reconcile original absence only in
        this proof-bearing context; ordinary exclusion remains strict.
        """
        try:
            if self._retirement_proof is None or not self._cleanup:
                raise RetirementUncertain()
            self.require()
            for path in sorted(self._domains(), key=lambda value: (-value.count('/'), value)):
                self.require()
                walker = _AuxiliaryMembers(LegacyInventory(self.journal.io), self.original.ownership,
                    self.journal.fence)
                relative, name = str(PurePosixPath(path).parent), PurePosixPath(path).name
                with walker._directory(relative, contents_may_change=True) as parent:
                    if any(value[:5] != self.original.auxiliary.directories[key][:5]
                            for key, value in walker.directory_identities.items()):
                        raise RetirementUncertain()
                    if parent is None:
                        # Its original removable parent is already gone. The
                        # later parent entry synchronizes its surviving parent.
                        if relative not in self._domains():
                            raise RetirementUncertain()
                        continue
                    try:
                        current = _signature(os.stat(name, dir_fd=parent, follow_symlinks=False))
                    except FileNotFoundError:
                        current = None
                    expected = self.original.auxiliary.members[path]
                    if current is not None:
                        fields = 5 if expected is not None and stat.S_ISDIR(expected[4]) else 9
                        if expected is None or current[:fields] != expected[:fields]:
                            raise RetirementUncertain()
                        if stat.S_ISDIR(current[4]):
                            os.rmdir(name, dir_fd=parent)
                        else:
                            os.unlink(name, dir_fd=parent)
                    os.fsync(parent)
                    try:
                        os.stat(name, dir_fd=parent, follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    else:
                        raise RetirementUncertain()
            self.require()
            observed = self._original_members()
            if any(observed[path] is not None for path in self._domains()):
                raise RetirementUncertain()
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
            raise RetirementUncertain() from None

    def _close(self) -> None:
        self._live = False
        for descriptor in reversed(tuple(self._descriptors.values())):
            os.close(descriptor)
        self._descriptors.clear()


@contextmanager
def held_auxiliary_operations(journal: RetirementJournal, original: OriginalInventory, *,
                              cleanup: bool = False) -> Iterator[AuxiliaryExclusion]:
    """Keep original locks held for the entire explicit operation's context.

    Acquiring one lock and then failing another releases all acquired locks.
    No auxiliary parser that samples a second file lock runs under our own
    exclusion. Current unchanged metadata is compared with the already parsed
    durable original evidence instead. Cleanup mode permits only absent
    original non-lock leaves and directory contents metadata changes. The
    caller must still supply fresh native/cutoff proof for each removal.
    """
    exclusion = AuxiliaryExclusion(journal, original, cleanup=cleanup)
    try:
        try:
            exclusion._acquire()
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
            raise RetirementUncertain() from None
        yield exclusion
        exclusion.require()
    finally:
        exclusion._close()


def retire_auxiliary_domains(journal: RetirementJournal, original: OriginalInventory, *,
                             before_effect: Callable[[], None]) -> None:
    """Explicit final domain cleanup; missing domains never prove writer death.

    The caller must independently prove current native/cutoff quiescence before
    acquiring surviving original domains and before every effect/readback.
    No absent lock is created, and no original evidence is recaptured.
    """
    exclusion = AuxiliaryExclusion(journal, original, cleanup=True, retirement_proof=before_effect)
    try:
        try:
            exclusion._acquire()
            exclusion.remove_domains()
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
            raise RetirementUncertain() from None
    finally:
        exclusion._close()
