"""Explicit removal of the original completely denied managed trust triple.

Persist identities, never trust contents, before the first unlink. Remaining
members must still be those denied files. Independent original-process death
and current native quiescence are required even after every member is absent.
This intermediate recovery evidence cannot establish final retirement.
"""
from __future__ import annotations

import fcntl
import json
import os
import stat
from contextlib import contextmanager
from dataclasses import replace
from typing import TYPE_CHECKING, Callable, Iterator

from runtime.remote_access.home_retirement import HomeRetirement, RetirementUncertain, STATE_DIRECTORY, _FILE_FLAGS, _signature
from runtime.remote_access.home_retirement_cleanup import ManagedCleanup
from runtime.remote_access.home_retirement_cutoff import _PairFiles
from runtime.remote_access.home_retirement_inventory import LegacyManagedMembers
from runtime.remote_access.home_retirement_recovery import OriginalDeathRecovery
from runtime.remote_access.home_retirement_service import OwnedService
from runtime.remote_access.state_store import AtomicFileTrustStateStore, StateStoreError

if TYPE_CHECKING:
    from runtime.remote_access.home_retirement_coordinator import ActiveRetirement

_RECORD = 'home-retirement-denied.json'
_ROOT = 'var/lib/happyranch-connector'
_LOCK = _ROOT + '/trust-state.json.lock'
_MEMBERS = frozenset(_ROOT + '/trust-state.json' + suffix for suffix in ('', '.anchor', '.lock'))


class _DeniedMembers(ManagedCleanup):
    _retained = frozenset()
    _mutable = frozenset()
    _closed = _removable_directories = frozenset((_ROOT,))

    def __init__(self, owner: ActiveRetirement, members: dict, before_effect: Callable[[], None]) -> None:
        super().__init__(owner.journal, owner.original, before_effect=before_effect)
        self.denied = replace(owner.original.managed, members=members)

    def _inventory(self) -> LegacyManagedMembers:
        return self.denied


class TrustCleanup:
    def __init__(self, owner: ActiveRetirement) -> None:
        self.owner = owner
        self.io, self.fence = owner.journal.io, owner.journal.fence
        self.reader = HomeRetirement(self.io)
        self._saved: tuple[bytes, tuple[int, ...]] | None = None

    def _native(self) -> None:
        owner = self.owner
        owner.cutoff.remaining()
        owner.cutoff.require_fence()
        owner.cutoff._require_original_peer()
        owner._require_records()
        controller = OwnedService(self.io, owner.original.ownership.service)
        controller.observe_cleanup(owner.original.ownership.native)
        recovery = OriginalDeathRecovery(owner.cutoff, owner.native_journal, native_cleanup=True)
        current = recovery.transport.observe_cleanup(owner.native)
        recovery._original_gone(current.boot_id)
        owner.cutoff._require_original_peer()
        if recovery.transport.observe_cleanup(owner.native) != current:
            raise RetirementUncertain()
        controller.observe_cleanup(owner.original.ownership.native)
        recovery._original_gone(current.boot_id)
        owner.cutoff.remaining()

    def _binding(self) -> dict:
        owner = self.owner
        return dict(format='home-retirement-denied@1', directory=owner.journal._root(),
            peer=[owner.cutoff.peer.pid, owner.cutoff.peer.started, owner.cutoff.peer.boot_id],
            identity=list(owner.original.managed.identity))

    @staticmethod
    def _encode(value: dict) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode() + b'\n'

    def _read(self) -> tuple[bytes, tuple[int, ...]]:
        with self.reader._entry(STATE_DIRECTORY + '/' + _RECORD) as (parent, name, info):
            if info is None:
                raise RetirementUncertain()
            return self.io._read_file(parent, name, owner=self.io.root_owner, mode=0o600), _signature(info)

    def _load(self) -> dict:
        data, identity = self._read()
        if self._saved is not None and self._saved != (data, identity):
            raise RetirementUncertain()
        value = self.io._json(data)
        binding = self._binding()
        if (value.keys() != {*binding, 'members'} or any(value[key] != item for key, item in binding.items())
                or type(value['members']) is not dict or value['members'].keys() != _MEMBERS
                or self._encode(value) != data):
            raise RetirementUncertain()
        members = {}
        for path, signature in value['members'].items():
            if (type(signature) is not list or len(signature) != 9
                    or any(type(item) is not int or item < 0 for item in signature)
                    or signature[1] == 0 or tuple(signature[2:4]) != self.owner.original.managed.account
                    or not stat.S_ISREG(signature[4]) or stat.S_IMODE(signature[4]) != 0o600
                    or signature[5] != 1):
                raise RetirementUncertain()
            members[path] = tuple(signature)
        if (members[_LOCK] != self.owner.original.managed.members[_LOCK]
                or len({item[:2] for item in members.values()}) != 3):
            raise RetirementUncertain()
        with self.io._publication_parent(STATE_DIRECTORY) as (parent, check):
            descriptor = os.open(_RECORD, _FILE_FLAGS, dir_fd=parent)
            try:
                if _signature(os.fstat(descriptor)) != identity:
                    raise RetirementUncertain()
                os.fsync(descriptor)
                os.fsync(parent)
                check()
                if self._read() != (data, identity):
                    raise RetirementUncertain()
            finally:
                os.close(descriptor)
        self._saved = data, identity
        return members

    @contextmanager
    def _exclusion(self, members: dict | None) -> Iterator[None]:
        # Once the original lock is absent, only independent native/death
        # proof licenses recovery. Never create a replacement lock domain.
        cleanup = _DeniedMembers(self.owner, members or self.owner.original.managed.members, self._native)
        walker = cleanup._walker()
        descriptor = None
        try:
            with walker._directory(_ROOT, contents_may_change=True) as parent:
                cleanup._parents(walker)
                if parent is not None:
                    try:
                        _, signature = self.owner.cutoff._file(parent, 'trust-state.json.lock')
                    except FileNotFoundError:
                        signature = None
                    if signature is not None:
                        if signature != self.owner.original.managed.members[_LOCK]:
                            raise RetirementUncertain()
                        descriptor = os.open('trust-state.json.lock', _FILE_FLAGS, dir_fd=parent)
                        if _signature(os.fstat(descriptor)) != signature:
                            raise RetirementUncertain()
                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    elif members is None:
                        raise RetirementUncertain()
                elif members is None:
                    raise RetirementUncertain()
            yield
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _capture(self) -> dict:
        owner = self.owner
        self._native()
        with owner.cutoff._directory(_ROOT) as parent:
            files = {path: owner.cutoff._file(parent, path.rsplit('/', 1)[1]) for path in _MEMBERS}
            adapter = _PairFiles(self.io.root / _ROOT, {path.rsplit('/', 1)[1]: value[0]
                for path, value in files.items() if path != _LOCK})
            state = AtomicFileTrustStateStore._load_locked(adapter)
            if state.connector_identity != owner.cutoff.identity:
                raise RetirementUncertain()
            owner.cutoff._denied(state)
            members = {path: value[1] for path, value in files.items()}
            if (members[_LOCK] != owner.original.managed.members[_LOCK]
                    or any(owner.cutoff._file(parent, path.rsplit('/', 1)[1]) != value
                           for path, value in files.items())):
                raise RetirementUncertain()
        value = dict(self._binding(), members=members)
        data = self._encode(value)
        with self.io._publication_parent(STATE_DIRECTORY) as (parent, check):
            descriptor = os.open(_RECORD, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600, dir_fd=parent)
            try:
                os.fchown(descriptor, *self.io.root_owner)
                os.fchmod(descriptor, 0o600)
                if os.write(descriptor, data) != len(data):
                    raise RetirementUncertain()
                os.fsync(descriptor)
                identity = _signature(os.fstat(descriptor))
                os.fsync(parent)
                check()
                if self._read() != (data, identity):
                    raise RetirementUncertain()
            finally:
                os.close(descriptor)
        self._saved = data, identity
        return self._load()

    def remove(self) -> None:
        try:
            self._native()
            exists = self.reader._present(STATE_DIRECTORY + '/' + _RECORD)
            members = self._load() if exists else None
            with self._exclusion(members):
                if members is None:
                    members = self._capture()
                def guard() -> None:
                    self._native()
                    if self._load() != members:
                        raise RetirementUncertain()
                cleanup = _DeniedMembers(self.owner, members, guard)
                guard()
                # A completed rmdir is reconciled through its still-original
                # immediate parent, with parent sync and absence readback.
                if any(value is not None for value in cleanup._observe().values()):
                    cleanup.remove()
                cleanup.remove_directories()
                guard()
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError, RecursionError, StateStoreError):
            raise RetirementUncertain() from None
