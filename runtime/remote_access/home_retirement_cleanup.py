"""Selective original private-leaf removal under fresh retirement proofs.

Native artifacts, trust state, operation locks and saved evidence are retained.
An absent original is current absence, never an acknowledgement of who removed
it. This explicit owner does not publish a final retired receipt.
"""
from __future__ import annotations

import os
import stat
from pathlib import PurePosixPath
from typing import Callable

from runtime.remote_access.home_retirement import RetirementUncertain, STATE_DIRECTORY, _signature
from runtime.remote_access.home_retirement_inventory import LegacyInventory, _PrivateMembers
from runtime.remote_access.home_retirement_journal import OriginalInventory, RetirementJournal
from runtime.remote_access.legacy_home_receipt import LIVE_MEMBERS

_MUTABLE = frozenset(path.lstrip('/') for path in LIVE_MEMBERS)
_CLOSED = frozenset(('etc/happyranch/headscale', 'var/lib/happyranch-headscale',
    'run/happyranch-headscale', STATE_DIRECTORY + '/home-ca'))


class PrivateCleanup:
    """Required effect guard plus saved original no-follow private inventory.

    The coordinator supplies actual stopped/cutoff and held-operation proofs.
    The callback is required on every use, including explicit recovery. Only
    original paths from the closed journal schema can be removed.
    """

    def __init__(self, journal: RetirementJournal, original: OriginalInventory, *,
                 before_effect: Callable[[], None]) -> None:
        self.journal, self.original, self.before_effect = journal, original, before_effect

    def _guard(self) -> None:
        self.journal.fence.require(self.journal.io)
        if self.journal.load() != self.original:
            raise RetirementUncertain()
        self.before_effect()
        self.journal.fence.require(self.journal.io)

    def _walker(self) -> _PrivateMembers:
        return _PrivateMembers(LegacyInventory(self.journal.io), self.original.ownership)

    def _parents(self, walker: _PrivateMembers) -> None:
        for path, current in walker.directory_identities.items():
            original = self.original.private.directories.get(path)
            if original is None or current[:5] != original[:5]:
                raise RetirementUncertain()

    def _observe(self) -> dict[str, tuple[int, ...] | None]:
        members = self.original.private.members
        result: dict[str, tuple[int, ...] | None] = {}
        for relative in sorted({str(PurePosixPath(path).parent) for path in members}):
            walker = self._walker()
            with walker._directory(relative, contents_may_change=True) as parent:
                self._parents(walker)
                if parent is not None and relative in _CLOSED:
                    allowed = {PurePosixPath(path).name for path in members
                               if str(PurePosixPath(path).parent) == relative}
                    with os.scandir(parent) as entries:
                        for index, entry in enumerate(entries):
                            if index >= 128 or entry.name not in allowed:
                                raise RetirementUncertain()
                for path, original in members.items():
                    if str(PurePosixPath(path).parent) != relative:
                        continue
                    try:
                        current = None if parent is None else _signature(os.stat(
                            PurePosixPath(path).name, dir_fd=parent, follow_symlinks=False))
                    except FileNotFoundError:
                        current = None
                    if current is not None:
                        fields = 6 if path in _MUTABLE else 9
                        if original is None or current[:fields] != original[:fields]:
                            raise RetirementUncertain()
                        if stat.S_ISDIR(current[4]):
                            # Empty original leaf directories are removed with
                            # rmdir only. No recursive traversal or removal.
                            with walker._directory(path, contents_may_change=True) as child:
                                if child is None:
                                    raise RetirementUncertain()
                                with os.scandir(child) as entries:
                                    if next(entries, None) is not None:
                                        raise RetirementUncertain()
                    result[path] = current
        return result

    def remove(self) -> None:
        """Remove remaining original private leaves, synchronizing each parent.

        Stopping may flush an original controller runtime inode. Its contents
        can change, but inode, custody, type and link count cannot. Immutable
        private/config files retain their complete original signatures. Recovery
        loads the same originals and never adopts a new current inventory.
        """
        try:
            self._guard()
            self._observe()  # Refuse any known drift before the first effect.
            for path in sorted(self.original.private.members):
                self._guard()
                current = self._observe()[path]
                if current is None:
                    continue
                relative, name = str(PurePosixPath(path).parent), PurePosixPath(path).name
                walker = self._walker()
                with walker._directory(relative, contents_may_change=True) as parent:
                    self._parents(walker)
                    if parent is None or _signature(os.stat(name, dir_fd=parent,
                            follow_symlinks=False)) != current:
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
            self._guard()
            if any(value is not None for value in self._observe().values()):
                raise RetirementUncertain()
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
            raise RetirementUncertain() from None
