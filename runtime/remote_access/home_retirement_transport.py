"""Fixed managed transport ownership for explicit home retirement.

The coordinator must independently fence admission and obtain real persistent
revocation/original-owner transport proof before native effects. This owner
never grants those proofs, initializes state, deletes files or modifies users.
Like the installer, native sampling assumes cooperative root/systemd writers.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Mapping

from runtime.remote_access.home_retirement import (
    HomeRetirement, RetirementUncertain, _DBUS_ROOTS, _DIRECTORY_FLAGS,
    _FILE_FLAGS, _UNIT_ROOTS, _UNIT_SUFFIXES, _signature,
)
from runtime.remote_access.home_retirement_fence import RetirementFence
from runtime.remote_access.home_retirement_inventory import LegacyManagedMembers
from runtime.remote_access.home_retirement_service import OwnedService, boot_identity
from runtime.remote_access.service_manager import ServiceManagerError

if TYPE_CHECKING:
    from runtime.remote_access.installer import ManagedInstaller

# Historical source provenance: 8ba249d01807f8ef08b5f82d4780a01ea3e3d660,
# Debian linux-managed-identity-v1, with the separately published home sidecar.
_UNIT_DIGESTS = {'happyranch-connector.service': 'd620b41f37777518231d8b4e404529767d85f860e88c8af589087cf3515ce0e7',
 'happyranch-tsnet-sidecar.service': 'c66f28ab9a625ad0dd9b17fdd2fd00f5618f79bde8f4e22d6dddf175ffca48bc',
 'happyranch-managed.target': 'af7c83b16489a907a8b58f7a56f5153b3203aab8791df8290af847344375982f',
 'happyranch-release-verifier.socket': '8db88c53f4a00581009acc58e15cf70c00aabd07f707305242e2494c5d9b1351',
 'happyranch-release-verifier.service': '3d4ac2db496b321baea22e019b07e1b7194113ed0a77f02594f962688252106a'}
_HOME_SIDECAR = 'cb14c32bf0e68f07269eb6b2de9135b1c05e9dcc312b86124c8efa2e6bbf36dd'
_ALLOWED_OTHER = {'etc/systemd/system/happyranch-headscale.service': ['ca7aab44898b455c353ccbbf31abc015f42383d9cedc2ad1d11a911fe3b9b9f3',
                                                     '345bce5ff33ddd08c08d90e96e103688621ffa6c8b91598b5dda5efac63066b6'],
 'usr/lib/systemd/system/happyranch-remote-access-policy-renew.service': ['5ffcbeeb6ce4f515c36c0fea1dff6a129a1d30ae9c24922865fde38500b3784d'],
 'usr/lib/systemd/system/happyranch-remote-access-policy-renew.timer': ['3c0e734f08bcd1bb91f99ecaca06048060a7e06be37770195b2c2bb65cf10182']}

_UNITS = tuple(_UNIT_DIGESTS)
_LINKS = {
    'etc/systemd/system/multi-user.target.wants/happyranch-managed.target': 'happyranch-managed.target',
    'etc/systemd/system/happyranch-managed.target.wants/happyranch-release-verifier.socket': 'happyranch-release-verifier.socket',
}
_EXECUTABLES = {
    'happyranch-connector.service': 'opt/happyranch-desktop/python/bin/python3.12',
    'happyranch-tsnet-sidecar.service': 'opt/happyranch-desktop/bin/happyranch-tsnet-sidecar',
    'happyranch-release-verifier.service': 'opt/happyranch-desktop/python/bin/python3.12',
}
_COMMON = ('Id', 'Names', 'FragmentPath', 'DropInPaths', 'LoadState', 'ActiveState', 'SubState',
           'UnitFileState', 'Job', 'NeedDaemonReload')
_SERVICE = ('MainPID', 'ControlPID', 'ControlGroup', 'User', 'Group', 'DynamicUser', 'Transient')
_REGISTRY = 'var/lib/dpkg/info/happyranch.md5sums'


@dataclass(frozen=True, repr=False)
class ManagedObservation:
    boot_id: str
    artifacts: Mapping[str, tuple[int, ...] | None] = field(repr=False)
    properties: Mapping[str, tuple[tuple[str, str], ...]] = field(repr=False)
    processes: Mapping[str, tuple[int, int] | None] = field(repr=False)

    def __post_init__(self) -> None:
        for name in ('artifacts', 'properties', 'processes'):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))

    def __repr__(self) -> str:
        return 'ManagedObservation(<redacted>)'


@dataclass(frozen=True)
class ManagedStopReceipt:
    observation: ManagedObservation


class ManagedTransport:
    """Fixed native owner; managed config lineage/cutoff are separate owners.

    Executable provenance comes from the protected installed package registry,
    rather than hashing an unknown candidate and declaring that hash trusted.
    No package database, shared account or process outside the fixed units is
    changed. Missing/partial/foreign units refuse before effects.
    """

    def __init__(self, io: ManagedInstaller, fence: RetirementFence, managed: LegacyManagedMembers) -> None:
        if (type(managed) is not LegacyManagedMembers or len(managed.account) != 2
                or any(type(value) is not int or not 0 < value < 2**32 - 1 for value in managed.account)):
            raise RetirementUncertain()
        self.io, self.fence, self.managed = io, fence, managed
        self.reader = HomeRetirement(io)
        self.digests = dict(_UNIT_DIGESTS)
        if managed.live_sidecar:
            self.digests['happyranch-tsnet-sidecar.service'] = _HOME_SIDECAR

    def _account(self) -> None:
        self.fence.require(self.io)
        user, group = self.io._lookup_user('happyranch'), self.io._lookup_group('happyranch')
        if ((user.pw_name, user.pw_uid, user.pw_gid) != ('happyranch', *self.managed.account)
                or (group.gr_name, group.gr_gid) != ('happyranch', self.managed.account[1])):
            raise RetirementUncertain()

    def _read(self, path: str, *, executable: bool = False) -> tuple[bytes | str, tuple[int, ...]]:
        limit = 256 * 1024 * 1024 if executable else 16 * 1024 * 1024 if path == _REGISTRY else 65536
        with self.reader._entry(path) as (parent, name, info):
            if (info is None or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or (info.st_uid, info.st_gid) != self.io.root_owner
                    or stat.S_IMODE(info.st_mode) != (0o755 if executable else 0o644)
                    or not 0 < info.st_size <= limit):
                raise RetirementUncertain()
            descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent)
            try:
                if _signature(os.fstat(descriptor)) != _signature(info):
                    raise RetirementUncertain()
                chunks, count = [], 0
                digest = hashlib.md5(usedforsecurity=False)
                while True:
                    data = os.read(descriptor, 65536)
                    if not data:
                        break
                    count += len(data)
                    if count > limit:
                        raise RetirementUncertain()
                    if executable:
                        digest.update(data)
                    else:
                        chunks.append(data)
                if (count != info.st_size or _signature(os.fstat(descriptor)) != _signature(info)
                        or _signature(os.stat(name, dir_fd=parent, follow_symlinks=False)) != _signature(info)):
                    raise RetirementUncertain()
                return digest.hexdigest() if executable else b''.join(chunks), _signature(info)
            finally:
                os.close(descriptor)

    def _files(self) -> dict[str, tuple[int, ...] | None]:
        raw, registry = self._read(_REGISTRY)
        expected = {}
        for index, line in enumerate(raw.decode('utf-8').splitlines()):
            if index >= 65536 or len(line) > 4096:
                raise RetirementUncertain()
            parts = line.split('  ', 1)
            if len(parts) != 2 or re.fullmatch(r'[0-9a-f]{32}', parts[0]) is None:
                raise RetirementUncertain()
            if parts[1] in _EXECUTABLES.values():
                if parts[1] in expected:
                    raise RetirementUncertain()
                expected[parts[1]] = parts[0]
        if expected.keys() != set(_EXECUTABLES.values()):
            raise RetirementUncertain()
        result = {_REGISTRY: registry}
        for path, digest in expected.items():
            actual, signature = self._read(path, executable=True)
            if actual != digest:
                raise RetirementUncertain()
            result[path] = signature
        for name, digest in self.digests.items():
            path = 'etc/systemd/system/' + name
            raw, signature = self._read(path)
            if hashlib.sha256(raw).hexdigest() != digest:
                raise RetirementUncertain()
            result[path] = signature
        directories = {'.'} | {str(parent) for path in (*result, *_LINKS) for parent in Path(path).parents}
        for relative in sorted(directories):
            with self.reader._entry(str(Path(relative) / 'retirement-parent-probe')) as (parent, name, _):
                result['directory:' + relative] = _signature(os.fstat(parent)) if name == 'retirement-parent-probe' else None
        return result

    def _links(self) -> dict[str, tuple[int, ...] | None]:
        result = dict.fromkeys(_LINKS)
        link_parents = {str(Path(path).parent) for path in _LINKS}
        unit_paths = {'etc/systemd/system/' + name for name in _UNITS}
        count = 0
        forbidden = {'.' + name + '.pending' for name in _UNITS}
        for name in _UNITS:
            stem, suffix = name.rsplit('.', 1)
            forbidden.update((name + '.d', suffix + '.d'))
            while '-' in stem:
                stem = stem.rsplit('-', 1)[0]
                forbidden.add(stem + '-.' + suffix + '.d')

        def scan(parent: int, relative: str, depth: int, dbus: bool) -> None:
            nonlocal count
            if depth > 32:
                raise RetirementUncertain()
            before = _signature(os.fstat(parent))
            with os.scandir(parent) as entries:
                for entry in entries:
                    count += 1
                    path = relative + '/' + entry.name
                    if (count > 65536 or entry.name in forbidden or any(entry.name.startswith(
                            name.rsplit('.', 1)[0] + '.') for name in _UNITS)
                            and path not in unit_paths
                            and path not in _LINKS and path not in link_parents):
                        raise RetirementUncertain()
                    info = os.stat(entry.name, dir_fd=parent, follow_symlinks=False)
                    if path in link_parents and not stat.S_ISDIR(info.st_mode):
                        raise RetirementUncertain()
                    if stat.S_ISLNK(info.st_mode):
                        target = os.readlink(entry.name, dir_fd=parent)
                        if entry.name in _UNITS or Path(target).name in _UNITS:
                            if (path not in _LINKS or target != '/etc/systemd/system/' + _LINKS[path]
                                    or (info.st_uid, info.st_gid) != self.io.root_owner or info.st_nlink != 1):
                                raise RetirementUncertain()
                            result[path] = _signature(info)
                    elif stat.S_ISDIR(info.st_mode):
                        if (info.st_uid, info.st_gid) != self.io.root_owner or info.st_mode & 0o022:
                            raise RetirementUncertain()
                        child = os.open(entry.name, _DIRECTORY_FLAGS, dir_fd=parent)
                        try:
                            if _signature(os.fstat(child)) != _signature(info):
                                raise RetirementUncertain()
                            scan(child, path, depth + 1, dbus)
                        finally:
                            os.close(child)
                    elif stat.S_ISREG(info.st_mode):
                        if entry.name in _UNITS and path != 'etc/systemd/system/' + entry.name:
                            raise RetirementUncertain()
                        if dbus or entry.name.endswith(_UNIT_SUFFIXES) or relative.endswith('.d') and entry.name.endswith('.conf'):
                            data = self.reader._read(parent, entry.name, info)
                            if (any(name.encode() in data for name in _UNITS)
                                    or re.search(rb'(?m)^\s*(User|Group)\s*=\s*happyranch\s*$', data)):
                                known = self.digests.get(entry.name) if path == 'etc/systemd/system/' + entry.name else None
                                allowed = ([known] if known else []) + _ALLOWED_OTHER.get(path, [])
                                from runtime.remote_access.installer import _owned_helper_unit
                                if (hashlib.sha256(data).hexdigest() not in allowed
                                        and not _owned_helper_unit(path, info, data, self.io.root_owner)):
                                    raise RetirementUncertain()
                    else:
                        raise RetirementUncertain()
                    if _signature(os.stat(entry.name, dir_fd=parent, follow_symlinks=False)) != _signature(info):
                        raise RetirementUncertain()
            if _signature(os.fstat(parent)) != before:
                raise RetirementUncertain()

        roots = list(_UNIT_ROOTS)
        with self.reader._entry('lib') as (parent, name, info):
            if info is not None:
                if stat.S_ISLNK(info.st_mode):
                    if os.readlink(name, dir_fd=parent) not in ('usr/lib', '/usr/lib'):
                        raise RetirementUncertain()
                elif stat.S_ISDIR(info.st_mode):
                    roots.append('lib/systemd/system')
                else:
                    raise RetirementUncertain()
        for relative in (*roots, *_DBUS_ROOTS):
            with self.reader._entry(relative + '/retirement-load-probe') as (parent, name, _):
                if name == 'retirement-load-probe':
                    scan(parent, relative, 0, relative in _DBUS_ROOTS)
        return result

    def _native(self) -> dict[str, tuple[tuple[str, str], ...]]:
        result = {}
        for name in _UNITS:
            props = self.io.manager.show_properties(name, _COMMON + (_SERVICE if name in _EXECUTABLES else ()))
            fixed = dict(Id=name, Names=name, FragmentPath=str(self.io.root / 'etc/systemd/system' / name),
                DropInPaths='', LoadState='loaded', Job='', NeedDaemonReload='no')
            if set(props) != set(_COMMON + (_SERVICE if name in _EXECUTABLES else ())) or any(props.get(k) != v for k, v in fixed.items()):
                raise RetirementUncertain()
            if name in _EXECUTABLES:
                expected_user = 'root' if name == 'happyranch-release-verifier.service' else 'happyranch'
                if (any(props.get(k) != v for k, v in dict(User=expected_user, Group='happyranch',
                        DynamicUser='no', Transient='no', ControlPID='0').items())
                        or not props['MainPID'].isascii() or not props['MainPID'].isdigit()):
                    raise RetirementUncertain()
                pid = int(props['MainPID'])
                if props['ActiveState'] in ('inactive', 'failed'):
                    if props['SubState'] not in ('dead', 'failed') or pid or props['ControlGroup']:
                        raise RetirementUncertain()
                elif (props['ActiveState'], props['SubState']) != ('active', 'running') or pid <= 0 or props['ControlGroup'] != '/system.slice/' + name:
                    raise RetirementUncertain()
            elif (props['ActiveState'], props['SubState']) not in (
                    ('inactive', 'dead'), ('active', 'listening' if name.endswith('.socket') else 'active')):
                raise RetirementUncertain()
            if props['UnitFileState'] not in ('enabled', 'disabled', 'static'):
                raise RetirementUncertain()
            result[name] = tuple(sorted(props.items()))
        return result

    def _process(self, name: str, properties: tuple[tuple[str, str], ...], artifacts: dict) -> tuple[int, int] | None:
        props = dict(properties)
        pid = int(props['MainPID'])
        if pid == 0:
            return None
        uid = self.io.root_owner[0] if name == 'happyranch-release-verifier.service' else self.managed.account[0]
        path = _EXECUTABLES[name]
        python = '/opt/happyranch-desktop/python/bin/python3'
        argv = ([python, '-I', '-m', 'runtime.remote_access.release_verifier'] if name == 'happyranch-release-verifier.service' else
            [python, '-I', '-m', 'runtime.remote_access.cli', 'run', '--managed', '--config', '/etc/happyranch/connector.json']
            if name == 'happyranch-connector.service' else
            ['/opt/happyranch-desktop/bin/happyranch-tsnet-sidecar', '--config', '/etc/happyranch/sidecar.json',
             '--credential-file', '/run/credentials/happyranch-tsnet-sidecar.service/enrollment-key'])
        with self.io._publication_parent('proc') as (parent, check):
            before = os.stat(str(pid), dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode) or before.st_uid != uid:
                raise RetirementUncertain()
            descriptor = os.open(str(pid), _DIRECTORY_FLAGS, dir_fd=parent)
            try:
                if _signature(os.fstat(descriptor)) != _signature(before):
                    raise RetirementUncertain()
                status = OwnedService._proc_read(descriptor, 'status')
                for key, value in (('Uid', uid), ('Gid', self.managed.account[1])):
                    if [line.split()[1:] for line in status.splitlines() if line.startswith(key + ':')] != [[str(value)] * 4]:
                        raise RetirementUncertain()
                fields = OwnedService._proc_read(descriptor, 'stat').rsplit(') ', 1)
                if (len(fields) != 2 or fields[0].split(' (', 1)[0] != str(pid) or len(fields[1].split()) < 20
                        or not fields[1].split()[19].isdigit() or int(fields[1].split()[19]) <= 0
                        or os.readlink('exe', dir_fd=descriptor) != str(self.io.root / path)
                        or _signature(os.stat('exe', dir_fd=descriptor)) != artifacts[path]
                        or OwnedService._proc_read(descriptor, 'cmdline') != '\0'.join(argv) + '\0'
                        or OwnedService._proc_read(descriptor, 'cgroup') != '0::/system.slice/' + name + '\n'):
                    raise RetirementUncertain()
                check()
                if _signature(os.stat(str(pid), dir_fd=parent, follow_symlinks=False)) != _signature(before):
                    raise RetirementUncertain()
                return pid, int(fields[1].split()[19])
            finally:
                os.close(descriptor)

    def _processes(self, properties: dict, artifacts: dict) -> dict[str, tuple[int, int] | None]:
        result = {name: self._process(name, properties[name], artifacts) for name in _EXECUTABLES}
        expected = {value[0] for name, value in result.items() if value is not None
                    and (name != 'happyranch-release-verifier.service' or self.io.root_owner[0] == self.managed.account[0])}
        if len([value[0] for value in result.values() if value is not None]) != len({value[0] for value in result.values() if value is not None}):
            raise RetirementUncertain()
        code, output = self.io._run(['ps', '-eo', 'pid=,ruid=,euid=,suid=,fuid='], timeout=10.0)
        if code or len(output.encode()) > 65536:
            raise RetirementUncertain()
        found = set()
        for line in output.splitlines():
            parts = line.split()
            if len(parts) != 5 or any(not part.isascii() or not part.isdigit() for part in parts):
                raise RetirementUncertain()
            values = tuple(map(int, parts))
            if self.managed.account[0] in values[1:]:
                if values[1:] != (self.managed.account[0],) * 4 or values[0] in found:
                    raise RetirementUncertain()
                found.add(values[0])
        if found != expected:
            raise RetirementUncertain()
        return result

    def observe(self, *, stopped: bool = False, disabled: bool = False) -> ManagedObservation:
        try:
            self._account()
            boot = boot_identity(self.io)
            artifacts = self._files()
            links = self._links()
            properties = self._native()
            for name in _UNITS:
                enabled = any(unit == name and links[path] is not None for path, unit in _LINKS.items())
                if (dict(properties[name])['UnitFileState'] == 'enabled') != enabled or disabled and enabled:
                    raise RetirementUncertain()
                if stopped and dict(properties[name])['ActiveState'] not in ('inactive', 'failed'):
                    raise RetirementUncertain()
            processes = self._processes(properties, artifacts)
            self._account()
            if (self._native() != properties or self._processes(properties, artifacts) != processes
                    or self._links() != links or self._files() != artifacts or boot_identity(self.io) != boot):
                raise RetirementUncertain()
            self.fence.require(self.io)
            return ManagedObservation(boot, {**artifacts, **links}, properties, processes)
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError, ServiceManagerError):
            raise RetirementUncertain() from None

    @staticmethod
    def _same_members(original: ManagedObservation, current: ManagedObservation,
                      removed: set[str]) -> None:
        def stable(key: str, value: tuple[int, ...] | None) -> tuple[int, ...] | None:
            return value[:5] if key.startswith('directory:') and value is not None else value
        if {key: stable(key, None if key in removed else value) for key, value in original.artifacts.items()} != {
                key: stable(key, value) for key, value in current.artifacts.items()}:
            raise RetirementUncertain()

    def stop_and_disable(self, expected: ManagedObservation) -> ManagedStopReceipt:
        try:
            original = self.observe()
            if type(expected) is not ManagedObservation or original != expected:
                raise RetirementUncertain()
            removed: set[str] = set()
            for name in ('happyranch-managed.target', 'happyranch-tsnet-sidecar.service', 'happyranch-connector.service',
                         'happyranch-release-verifier.socket', 'happyranch-release-verifier.service'):
                before = self.observe()
                self._same_members(original, before, removed)
                if self.observe() != before:
                    raise RetirementUncertain()
                if dict(before.properties[name])['ActiveState'] not in ('inactive', 'failed'):
                    self.io.manager.stop(name, timeout=50.0)
                after = self.observe()
                self._same_members(original, after, removed)
                if dict(after.properties[name])['ActiveState'] not in ('inactive', 'failed'):
                    raise RetirementUncertain()
            for name in ('happyranch-managed.target', 'happyranch-release-verifier.socket'):
                before = self.observe(stopped=True)
                self._same_members(original, before, removed)
                if self.observe(stopped=True) != before:
                    raise RetirementUncertain()
                if dict(before.properties[name])['UnitFileState'] == 'enabled':
                    self.io.manager.disable(name)
                    removed.update(path for path, unit in _LINKS.items() if unit == name)
            final = self.observe(stopped=True, disabled=True)
            self._same_members(original, final, removed)
            return ManagedStopReceipt(final)
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError, ServiceManagerError):
            raise RetirementUncertain() from None

    def confirm(self, receipt: ManagedStopReceipt) -> None:
        if type(receipt) is not ManagedStopReceipt or self.observe(stopped=True, disabled=True) != receipt.observation:
            raise RetirementUncertain()
