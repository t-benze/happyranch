"""Selective original leaves, fragments and dedicated binary under fresh proofs.

Shared executables, trust state, operation locks and saved evidence are retained.
An absent original is current absence, never an acknowledgement of who removed
it. This explicit owner does not publish a final retired receipt.
"""
from __future__ import annotations

import os
import stat
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Callable

from runtime.remote_access.home_retirement import RetirementUncertain, STATE_DIRECTORY, _DIRECTORY_FLAGS, _signature
from runtime.remote_access.home_retirement_inventory import (
    LegacyInventory, LegacyManagedMembers, LegacyPrivateMembers, _ManagedMembers, _PrivateMembers,
)
from runtime.remote_access.home_retirement_journal import OriginalInventory, RetirementJournal
from runtime.remote_access.legacy_home_receipt import LIVE_MEMBERS

if TYPE_CHECKING:
    from runtime.remote_access.home_retirement_transport import ManagedObservation

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

    _mutable = _MUTABLE
    _closed = _CLOSED
    _removable_directories = _CLOSED
    _retained = frozenset()

    def _inventory(self) -> LegacyPrivateMembers | LegacyManagedMembers:
        return self.original.private

    def _members(self) -> dict[str, tuple[int, ...] | None]:
        return {path: signature for path, signature in self._inventory().members.items()
                if path not in self._retained}

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
            original = self._inventory().directories.get(path)
            if original is None or current[:5] != original[:5]:
                raise RetirementUncertain()

    def _observe(self) -> dict[str, tuple[int, ...] | None]:
        members = self._members()
        result: dict[str, tuple[int, ...] | None] = {}
        for relative in sorted({str(PurePosixPath(path).parent) for path in self._inventory().members}):
            walker = self._walker()
            with walker._directory(relative, contents_may_change=True) as parent:
                self._parents(walker)
                if parent is not None and relative in self._closed:
                    allowed = {PurePosixPath(path).name for path in self._inventory().members
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
                        fields = 6 if path in self._mutable else 9
                        if original is None or current[:fields] != original[:fields]:
                            raise RetirementUncertain()
                        if stat.S_ISDIR(current[4]):
                            # Empty original leaf directories are removed with
                            # rmdir only. No recursive traversal or removal.
                            # The leaf can belong to the original service
                            # account. The parent walker owns ancestors; the
                            # saved leaf signature owns this empty directory.
                            child = os.open(PurePosixPath(path).name, _DIRECTORY_FLAGS, dir_fd=parent)
                            try:
                                if _signature(os.fstat(child)) != current:
                                    raise RetirementUncertain()
                                with os.scandir(child) as entries:
                                    if next(entries, None) is not None:
                                        raise RetirementUncertain()
                                if (_signature(os.fstat(child)) != current
                                        or _signature(os.stat(PurePosixPath(path).name, dir_fd=parent,
                                            follow_symlinks=False)) != current):
                                    raise RetirementUncertain()
                            finally:
                                os.close(child)
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
            for path in sorted(self._members()):
                self._guard()
                current = self._observe()[path]
                relative, name = str(PurePosixPath(path).parent), PurePosixPath(path).name
                walker = self._walker()
                with walker._directory(relative, contents_may_change=True) as parent:
                    self._parents(walker)
                    if parent is None:
                        if self._members()[path] is not None:
                            raise RetirementUncertain()
                        continue
                    try:
                        named = _signature(os.stat(name, dir_fd=parent, follow_symlinks=False))
                    except FileNotFoundError:
                        named = None
                    if named != current:
                        raise RetirementUncertain()
                    if current is not None:
                        if stat.S_ISDIR(current[4]):
                            os.rmdir(name, dir_fd=parent)
                        else:
                            os.unlink(name, dir_fd=parent)
                    # A previous unlink may have lost its parent sync. Fresh
                    # absence alone is not its durability acknowledgement.
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

    def _empty_directories(self) -> dict[str, tuple[int, ...] | None]:
        if any(value is not None for value in self._observe().values()):
            raise RetirementUncertain()
        result = {}
        for path in sorted(self._removable_directories):
            walker = self._walker()
            relative, name = str(PurePosixPath(path).parent), PurePosixPath(path).name
            with walker._directory(relative, contents_may_change=True) as parent:
                self._parents(walker)
                if parent is None:
                    raise RetirementUncertain()
                try:
                    current = _signature(os.stat(name, dir_fd=parent, follow_symlinks=False))
                except FileNotFoundError:
                    current = None
                if current is not None:
                    original = self._inventory().directories.get(path)
                    if original is None or current[:5] != original[:5] or not stat.S_ISDIR(current[4]):
                        raise RetirementUncertain()
                    child = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent)
                    try:
                        if _signature(os.fstat(child)) != current:
                            raise RetirementUncertain()
                        with os.scandir(child) as entries:
                            if next(entries, None) is not None:
                                raise RetirementUncertain()
                        if (_signature(os.fstat(child)) != current
                                or _signature(os.stat(name, dir_fd=parent, follow_symlinks=False)) != current):
                            raise RetirementUncertain()
                    finally:
                        os.close(child)
                result[path] = current
        return result

    def remove_directories(self) -> None:
        """Finish only fixed original empty roots after leaf removal.

        Each immediate parent must still be original, even when the root is
        already absent. Recovery synchronizes that parent without replaying
        rmdir. Shared parents and serialization domains are never removed.
        """
        try:
            self._guard()
            self._empty_directories()
            for path in sorted(self._removable_directories):
                self._guard()
                current = self._empty_directories()[path]
                walker = self._walker()
                relative, name = str(PurePosixPath(path).parent), PurePosixPath(path).name
                with walker._directory(relative, contents_may_change=True) as parent:
                    self._parents(walker)
                    if parent is None:
                        raise RetirementUncertain()
                    try:
                        named = _signature(os.stat(name, dir_fd=parent, follow_symlinks=False))
                    except FileNotFoundError:
                        named = None
                    if named != current:
                        raise RetirementUncertain()
                    if current is not None:
                        os.rmdir(name, dir_fd=parent)
                    os.fsync(parent)
                    try:
                        os.stat(name, dir_fd=parent, follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    else:
                        raise RetirementUncertain()
            self._guard()
            if any(value is not None for value in self._empty_directories().values()):
                raise RetirementUncertain()
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
            raise RetirementUncertain() from None


class OriginalReceiptCleanup(PrivateCleanup):
    """Only the closed historical receipts saved before retirement effects.

    The caller must freshly prove complete cleanup and account disposition
    before each unlink/sync. Recovery journals and the admission fence survive.
    Missing original receipts never authorize another account or native effect.
    """

    _mutable = _closed = frozenset()

    def _inventory(self) -> LegacyPrivateMembers:
        return LegacyPrivateMembers({STATE_DIRECTORY + '/' + name: signature
            for name, signature in self.original.ownership.receipt_identities.items()},
            self.original.private.directories)


class ManagedCleanup(PrivateCleanup):
    """Original managed leaves with the denied trust pair/lock retained.

    The required caller guard independently reads the real trust pair and
    original native quiescence. This owner never opens or removes that proof.
    """

    _retained = frozenset('var/lib/happyranch-connector/' + name for name in
        ('trust-state.json', 'trust-state.json.anchor', 'trust-state.json.lock'))
    _closed = frozenset(('var/lib/happyranch-connector', 'var/lib/happyranch-tsnet-sidecar',
        'run/happyranch-connector', 'run/happyranch-tsnet-sidecar'))
    # The real denied trust pair/lock stays available for independent cutoff
    # readback. It is a separate final-cleanup obligation, never an empty root.
    _removable_directories = _closed - {'var/lib/happyranch-connector'}

    def _inventory(self) -> LegacyManagedMembers:
        return self.original.managed

    @property
    def _mutable(self) -> frozenset[str]:
        return frozenset(path for path in self._members() if path.startswith(('var/', 'run/')))

    def _walker(self) -> _ManagedMembers:
        walker = _ManagedMembers(LegacyInventory(self.journal.io), self.original.ownership)
        if walker.account != self.original.managed.account:
            raise RetirementUncertain()
        return walker


class InertConnectorCleanup(ManagedCleanup):
    """Only the original empty connector root of a never-activated layout.

    Active trust must survive for real denial readback. Historical absence of
    all three trust members is mandatory here, independently of current absence.
    The caller still supplies fresh inert/native proof before every effect.
    """

    _removable_directories = frozenset(('var/lib/happyranch-connector',))

    def _inventory(self) -> LegacyManagedMembers:
        managed = super()._inventory()
        if (self.original.ownership.activation is not None or managed.live_sidecar
                or any(managed.members[path] is not None for path in self._retained)):
            raise RetirementUncertain()
        return managed


class ExecutableCleanup(PrivateCleanup):
    """Only the saved dedicated controller binary, under original ancestry.

    Shared interpreter, sidecar, package registry and payload parents survive.
    The required caller guard supplies fresh native/cutoff/held-domain proof.
    """

    _mutable = frozenset()
    _closed = frozenset()

    def _inventory(self) -> LegacyPrivateMembers:
        ownership = self.original.ownership
        return LegacyPrivateMembers({ownership.service.executable: ownership.native.executable},
            self.original.private.directories)


class UnitCleanup(PrivateCleanup):
    """Six fixed original fragments, retaining binaries and native evidence.

    The coordinator supplies fresh cleanup-aware native/death/denial proof.
    Original parent identities come from the saved managed native observation;
    no current candidate is captured as provenance for removal.
    """

    _mutable = frozenset()
    _closed = frozenset()

    def __init__(self, journal: RetirementJournal, original: OriginalInventory,
                 native: ManagedObservation, *, before_effect: Callable[[], None]) -> None:
        super().__init__(journal, original, before_effect=before_effect)
        members = {path: value for path, value in native.artifacts.items()
                   if path.startswith('etc/systemd/system/') and path.endswith(('.service', '.socket', '.target'))
                   and '/' not in path.removeprefix('etc/systemd/system/')}
        members['etc/systemd/system/' + original.ownership.service.unit] = original.ownership.native.fragment
        self._units = LegacyPrivateMembers(members, {path.removeprefix('directory:'): value
            for path, value in native.artifacts.items() if path.startswith('directory:') and value is not None})

    def _inventory(self) -> LegacyPrivateMembers:
        return self._units


def require_cleanup_absent(journal: RetirementJournal, original: OriginalInventory,
                           native: ManagedObservation, *, before_effect: Callable[[], None]) -> None:
    """Read saved-original absence before account disposition; remove nothing.

    Independent current native/cutoff proof remains mandatory. This does not
    adopt missing domains as exclusion or permit a remaining obsolete artifact.
    """
    from runtime.remote_access.home_retirement_operations import AuxiliaryExclusion
    before_effect()
    for owner in (PrivateCleanup, ManagedCleanup):
        cleanup = owner(journal, original, before_effect=before_effect)
        if any(value is not None for value in cleanup._empty_directories().values()):
            raise RetirementUncertain()
    for cleanup in (ExecutableCleanup(journal, original, before_effect=before_effect),
            UnitCleanup(journal, original, native, before_effect=before_effect)):
        if any(value is not None for value in cleanup._observe().values()):
            raise RetirementUncertain()
    auxiliary = AuxiliaryExclusion(journal, original, cleanup=True, retirement_proof=before_effect)
    if any(value is not None for value in auxiliary._original_members().values()):
        raise RetirementUncertain()
    before_effect()
