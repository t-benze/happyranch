"""Original managed-native evidence, never an effect acknowledgement.

The explicit coordinator captures this before revocation or native effects.
Recovery loads the saved original without discovering current replacements.
Neither a saved record nor absence supplies cutoff, death or cleanup proof.
"""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

from runtime.remote_access.home_retirement import (
    RetirementUncertain, STATE_DIRECTORY, _FILE_FLAGS, _signature,
)
from runtime.remote_access.home_retirement_transport import (
    ManagedObservation, ManagedTransport, _COMMON, _EXECUTABLES, _LINKS, _REGISTRY, _SERVICE, _UNITS,
)

_NAME = 'home-retirement-transport.json'
_LIMIT = 65536


class ManagedTransportJournal:
    def __init__(self, transport: ManagedTransport) -> None:
        self.transport, self.io, self.fence = transport, transport.io, transport.fence
        self._identity: tuple[int, ...] | None = None
        self._data: bytes | None = None

    def _directory(self) -> list[int]:
        with self.io._publication_parent(STATE_DIRECTORY) as (parent, check):
            self.fence.require(self.io)
            check()
            return list(_signature(os.fstat(parent))[:5])

    def _record(self, original: ManagedObservation) -> dict:
        managed = self.transport.managed
        return dict(format='home-retirement-transport@1', directory=self._directory(),
            managed=dict(identity=list(managed.identity), control_plane_id=managed.control_plane_id,
                release_uid=managed.release_uid, account=list(managed.account), live_sidecar=managed.live_sidecar),
            observation=dict(boot_id=original.boot_id, artifacts=dict(original.artifacts),
                properties=dict(original.properties), processes=dict(original.processes)))

    @staticmethod
    def _encode(record: dict) -> bytes:
        data = json.dumps(record, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode() + b'\n'
        if not 0 < len(data) <= _LIMIT:
            raise RetirementUncertain()
        return data

    def _parse(self, data: bytes) -> ManagedObservation:
        record = self.io._json(data)
        managed = self.transport.managed
        binding = dict(identity=list(managed.identity), control_plane_id=managed.control_plane_id,
            release_uid=managed.release_uid, account=list(managed.account), live_sidecar=managed.live_sidecar)
        if (record.keys() != {'format', 'directory', 'managed', 'observation'}
                or record['format'] != 'home-retirement-transport@1' or record['directory'] != self._directory()
                or type(record['managed']) is not dict or self._encode(record['managed']) != self._encode(binding)):
            raise RetirementUncertain()
        value = record['observation']
        if (type(value) is not dict or value.keys() != {'boot_id', 'artifacts', 'properties', 'processes'}
                or type(value['boot_id']) is not str or re.fullmatch(
                    r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', value['boot_id']) is None):
            raise RetirementUncertain()
        files = {_REGISTRY, *_EXECUTABLES.values(), *('etc/systemd/system/' + name for name in _UNITS)}
        directories = {'.'} | {str(parent) for path in (*files, *_LINKS) for parent in Path(path).parents}
        paths = files | set(_LINKS) | {'directory:' + path for path in directories}
        if type(value['artifacts']) is not dict or value['artifacts'].keys() != paths:
            raise RetirementUncertain()
        artifacts = {}
        for path, signature in value['artifacts'].items():
            if signature is None and path in _LINKS:
                artifacts[path] = None
                continue
            if (type(signature) is not list or len(signature) != 9 or any(type(item) is not int or item < 0
                    for item in signature) or signature[1] == 0 or tuple(signature[2:4]) != self.io.root_owner):
                raise RetirementUncertain()
            mode = signature[4]
            if path.startswith('directory:'):
                if not stat.S_ISDIR(mode) or mode & 0o022:
                    raise RetirementUncertain()
            elif path in _LINKS:
                if not stat.S_ISLNK(mode) or signature[5] != 1:
                    raise RetirementUncertain()
            elif (not stat.S_ISREG(mode) or signature[5] != 1
                    or stat.S_IMODE(mode) != (0o755 if path in _EXECUTABLES.values() else 0o644)):
                raise RetirementUncertain()
            artifacts[path] = tuple(signature)
        properties, processes = value['properties'], value['processes']
        if (type(properties) is not dict or properties.keys() != set(_UNITS)
                or type(processes) is not dict or processes.keys() != set(_EXECUTABLES)):
            raise RetirementUncertain()
        for name, pairs in properties.items():
            keys = sorted(_COMMON + (_SERVICE if name in _EXECUTABLES else ()))
            if (type(pairs) is not list or any(type(pair) is not list or len(pair) != 2
                    or any(type(item) is not str or len(item) > 4096 for item in pair) for pair in pairs)
                    or [pair[0] for pair in pairs] != keys):
                raise RetirementUncertain()
        for name, process in processes.items():
            if process is not None and (type(process) is not list or len(process) != 2
                    or any(type(item) is not int or not 0 < item < 2**63 for item in process)):
                raise RetirementUncertain()
            if dict(properties[name])['MainPID'] != str(0 if process is None else process[0]):
                raise RetirementUncertain()
        original = ManagedObservation(value['boot_id'], artifacts,
            {name: tuple(map(tuple, pairs)) for name, pairs in properties.items()},
            {name: None if process is None else tuple(process) for name, process in processes.items()})
        if self._encode(self._record(original)) != data:
            raise RetirementUncertain()
        return original

    def _read(self) -> tuple[bytes, tuple[int, ...]]:
        self.fence.require(self.io)
        with self.transport.reader._entry(STATE_DIRECTORY + '/' + _NAME) as (parent, name, info):
            if (info is None or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or (info.st_uid, info.st_gid) != self.io.root_owner
                    or stat.S_IMODE(info.st_mode) != 0o600 or not 0 < info.st_size <= _LIMIT):
                raise RetirementUncertain()
            return self.transport.reader._read(parent, name, info), _signature(info)

    def capture(self) -> ManagedObservation:
        """Publish once before effects; existing unknown evidence is preserved."""
        try:
            original = self.transport.observe()
            data = self._encode(self._record(original))
            if self._parse(data) != original:
                raise RetirementUncertain()
            with self.io._publication_parent(STATE_DIRECTORY) as (parent, check):
                self.fence.require(self.io)
                descriptor = os.open(_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600, dir_fd=parent)
                try:
                    os.fchown(descriptor, *self.io.root_owner)
                    os.fchmod(descriptor, 0o600)
                    if os.write(descriptor, data) != len(data):
                        raise RetirementUncertain()
                    os.fsync(descriptor)
                    identity = _signature(os.fstat(descriptor))
                    if self._read() != (data, identity):
                        raise RetirementUncertain()
                    os.fsync(parent)
                    check()
                    if _signature(os.fstat(descriptor)) != identity or self._read() != (data, identity):
                        raise RetirementUncertain()
                finally:
                    os.close(descriptor)
            if self.transport.observe() != original:
                raise RetirementUncertain()
            self._identity, self._data = identity, data
            return original
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError, RecursionError):
            raise RetirementUncertain() from None

    def load(self) -> ManagedObservation:
        """Explicitly resync saved evidence; never discover current native owners."""
        try:
            data, identity = self._read()
            if self._identity is not None and (identity, data) != (self._identity, self._data):
                raise RetirementUncertain()
            original = self._parse(data)
            with self.io._publication_parent(STATE_DIRECTORY) as (parent, check):
                self.fence.require(self.io)
                descriptor = os.open(_NAME, _FILE_FLAGS, dir_fd=parent)
                try:
                    if _signature(os.fstat(descriptor)) != identity:
                        raise RetirementUncertain()
                    os.fsync(descriptor)
                    os.fsync(parent)
                    check()
                    if _signature(os.fstat(descriptor)) != identity or self._read() != (data, identity):
                        raise RetirementUncertain()
                finally:
                    os.close(descriptor)
            self._identity, self._data = identity, data
            return original
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError, RecursionError):
            raise RetirementUncertain() from None
