"""Immutable original cleanup evidence, never an effect or completion receipt.

Only explicit retirement publishes this fixed root-private journal under the
original directory fence. Loading preserves the original observations even when
members have disappeared; it neither adopts current replacements nor proves
that an absent member was removed by us. The coordinator still owns every
revocation, transport acknowledgement, native effect and selective removal.
"""
from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from runtime.remote_access.home_retirement import (
    HomeRetirement, RetirementUncertain, STATE_DIRECTORY, _FILE_FLAGS, _signature,
)
from runtime.remote_access.home_retirement_fence import RetirementFence
from runtime.remote_access.home_retirement_inventory import (
    LegacyAuxiliaryMembers, LegacyInventory, LegacyManagedMembers, LegacyOwnership, LegacyPrivateMembers,
    _AuxiliaryMembers, _PrivateMembers,
)
from runtime.remote_access.home_retirement_service import ServiceIdentity, ServiceObservation, _PROPERTIES
from runtime.remote_access.legacy_home_receipt import HomeActivationReceipt, LIVE_MEMBERS, OWNED_LINK_TARGET

if TYPE_CHECKING:
    from runtime.remote_access.installer import ManagedInstaller

_NAME = "home-retirement-inventory.json"
_LIMIT = 1024 * 1024
_CONFIGS = frozenset("etc/happyranch/" + name for name in (
    "connector.json", "sidecar.json", "policy.json", "release-daemon.json",
    "daemon.token", "provision.json", "invite-issuer.json"))
_RUNTIME = {
    "var/lib/happyranch-connector": ("trust-state.json", "trust-state.json.anchor", "trust-state.json.lock"),
    "var/lib/happyranch-tsnet-sidecar": ("tailscaled.state", "credential.consumed", "tailscaled.log.conf",
        "tailscaled.log1.txt", "tailscaled.log2.txt"),
    "run/happyranch-connector": ("control.sock", "ingress.sock"),
    "run/happyranch-tsnet-sidecar": ("observe.sock", "health.json"),
}
_MANAGED = _CONFIGS | {parent + "/" + name for parent, names in _RUNTIME.items() for name in names}
_CA = frozenset(STATE_DIRECTORY + "/home-ca/" + name for name in ("ca.pem", "ca.key", "leaf.pem", "leaf.key"))
_PUBLIC = frozenset(("etc/happyranch/headscale/config.yaml", "etc/happyranch/headscale/derp.yaml"))
_TLS = frozenset(("etc/happyranch/headscale/server.crt", "etc/happyranch/headscale/server.key",
    "etc/happyranch/control-ca.pem", "etc/happyranch/empty-ca-directory"))
_PRIVATE = {name.lstrip("/") for name in LIVE_MEMBERS if not name.endswith("/observe.sock")} | _CA | _PUBLIC
_RECEIPTS = frozenset(("headscale-account.json", "headscale-publication.json", "home-activation.json"))
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}")
_AUXILIARY = frozenset(STATE_DIRECTORY + "/" + path for path in (
    "home-issuer", "home-issuer/invite-issuer.json", "home-issuer/invite-issuer.lock",
    "home-issuer-publication.json", "home-activation-txn.json", "home-minter",
    "home-minter/happyranch-invite", "home-minter/happyranch-node"))
_OPERATION = re.compile(re.escape(STATE_DIRECTORY) + r"/home-minter/happyranch-(invite|node)/operation-[0-9a-f]{64}")


def _encode(value: dict) -> bytes:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"
    if not 0 < len(data) <= _LIMIT:
        raise RetirementUncertain()
    return data


def _signature_value(value: object, *, absent: bool = False) -> tuple[int, ...] | None:
    if value is None and absent:
        return None
    if (type(value) is not list or len(value) != 9
            or any(type(item) is not int or item < 0 for item in value)
            or value[1] == 0 or not any(check(value[4]) for check in (stat.S_ISREG, stat.S_ISDIR, stat.S_ISSOCK, stat.S_ISLNK))):
        raise RetirementUncertain()
    return tuple(value)


def _signatures(value: object, *, absent: bool = False, limit: int = 96) -> dict[str, tuple[int, ...] | None]:
    if type(value) is not dict or len(value) > limit:
        raise RetirementUncertain()
    return {key: _signature_value(item, absent=absent) for key, item in value.items()}


def _digests(value: object) -> dict[str, str]:
    if (type(value) is not dict or any(type(item) is not str or re.fullmatch(r"[0-9a-f]{64}", item) is None
                                     for item in value.values())):
        raise RetirementUncertain()
    return value


@dataclass(frozen=True, repr=False)
class OriginalInventory:
    ownership: LegacyOwnership
    private: LegacyPrivateMembers
    managed: LegacyManagedMembers
    auxiliary: LegacyAuxiliaryMembers

    def __repr__(self) -> str:
        return "OriginalInventory(<redacted>)"

    def _record(self) -> dict:
        ownership, private, managed, auxiliary = self.ownership, self.private, self.managed, self.auxiliary
        return dict(service=asdict(ownership.service), native=asdict(ownership.native),
            activation=None if ownership.activation is None else json.loads(ownership.activation.to_bytes()),
            receipt_digests=dict(ownership.receipt_digests), receipt_identities=dict(ownership.receipt_identities),
            private=dict(members=dict(private.members), directories=dict(private.directories)),
            auxiliary=dict(members=dict(auxiliary.members), directories=dict(auxiliary.directories)),
            managed=dict(identity=list(managed.identity), control_plane_id=managed.control_plane_id,
                release_uid=managed.release_uid, account=list(managed.account), live_sidecar=managed.live_sidecar,
                members=dict(managed.members), directories=dict(managed.directories), digests=dict(managed.digests)))

    @classmethod
    def _parse(cls, value: dict) -> OriginalInventory:
        if type(value) is not dict or value.keys() != {
                "service", "native", "activation", "receipt_digests", "receipt_identities", "private", "managed", "auxiliary"}:
            raise RetirementUncertain()
        service = ServiceIdentity(**value["service"])
        activation = None if value["activation"] is None else HomeActivationReceipt.parse(_encode(value["activation"]).rstrip())
        native = value["native"]
        if (type(native) is not dict or native.keys() != {"fragment", "executable", "link", "properties", "process"}
                or type(native["properties"]) is not list or any(type(pair) is not list or len(pair) != 2
                    or any(type(item) is not str for item in pair) for pair in native["properties"])
                or [pair[0] for pair in native["properties"]] != sorted(_PROPERTIES)
                or native["process"] is not None and (type(native["process"]) is not list
                    or len(native["process"]) != 2 or any(type(item) is not int or item <= 0 for item in native["process"]))):
            raise RetirementUncertain()
        observed = ServiceObservation(_signature_value(native["fragment"]), _signature_value(native["executable"]),
            _signature_value(native["link"], absent=True), tuple(map(tuple, native["properties"])),
            None if native["process"] is None else tuple(native["process"]))
        digests, identities = _digests(value["receipt_digests"]), _signatures(value["receipt_identities"])
        if digests.keys() != identities.keys() or digests.keys() != (_RECEIPTS if activation else _RECEIPTS - {"home-activation.json"}):
            raise RetirementUncertain()
        private, managed, auxiliary = value["private"], value["managed"], value["auxiliary"]
        if (type(private) is not dict or private.keys() != {"members", "directories"}
                or type(auxiliary) is not dict or auxiliary.keys() != {"members", "directories"}
                or type(managed) is not dict or managed.keys() != {"identity", "control_plane_id", "release_uid",
                    "account", "live_sidecar", "members", "directories", "digests"}
                or type(managed["identity"]) is not list or len(managed["identity"]) != 3
                or any(type(item) is not str or _IDENTIFIER.fullmatch(item) is None
                    for item in (*managed["identity"], managed["control_plane_id"]))
                or type(managed["account"]) is not list or len(managed["account"]) != 2
                or any(type(item) is not int or not 0 < item < 2**32 - 1 for item in (*managed["account"], managed["release_uid"]))
                or type(managed["live_sidecar"]) is not bool):
            raise RetirementUncertain()
        private_members, managed_members = _signatures(private["members"], absent=True), _signatures(managed["members"], absent=True)
        allowed_private = _PRIVATE | _TLS | (set() if activation is None else
            {path.lstrip("/") for path in activation.member_digests if path != OWNED_LINK_TARGET})
        if (not _PRIVATE <= private_members.keys() or not private_members.keys() <= allowed_private
                or not _MANAGED <= managed_members.keys() or any(path not in _MANAGED and re.fullmatch(
                    r"run/happyranch-tsnet-sidecar/\.health-[0-9]+", path) is None for path in managed_members)
                or len(managed_members.keys() - _MANAGED) > 7):
            raise RetirementUncertain()
        managed_digests = _digests(managed["digests"])
        if managed_digests.keys() != {path for path in _CONFIGS if managed_members[path] is not None}:
            raise RetirementUncertain()
        auxiliary_members = _signatures(auxiliary["members"], absent=True, limit=len(_AUXILIARY) + 4096)
        if (not _AUXILIARY <= auxiliary_members.keys() or any(path not in _AUXILIARY and _OPERATION.fullmatch(path) is None
                for path in auxiliary_members) or any(signature is None for path, signature in auxiliary_members.items()
                    if path not in _AUXILIARY)):
            raise RetirementUncertain()
        private_dirs, managed_dirs, auxiliary_dirs = (_signatures(item["directories"])
                                                     for item in (private, managed, auxiliary))
        allowed_dirs = {"."} | {str(parent) for path in allowed_private | _MANAGED | _AUXILIARY
            for parent in PurePosixPath(path).parents if str(parent) != "."}
        allowed_dirs.update(str(parent) for parent in PurePosixPath(service.executable).parents)
        allowed_dirs.update(STATE_DIRECTORY + "/home-minter/" + user for user in ("happyranch-invite", "happyranch-node"))
        if any(path not in allowed_dirs or not stat.S_ISDIR(signature[4])
               for values in (private_dirs, managed_dirs, auxiliary_dirs) for path, signature in values.items()):
            raise RetirementUncertain()
        if activation is not None and (managed["release_uid"], managed["identity"][1], managed["identity"][2],
                managed["control_plane_id"]) != (activation.release_uid, activation.home_id, activation.connector_id, activation.control_plane_id):
            raise RetirementUncertain()
        return cls(LegacyOwnership(service, observed, activation, digests, identities),
            LegacyPrivateMembers(private_members, private_dirs), LegacyManagedMembers(tuple(managed["identity"]),
                managed["control_plane_id"], managed["release_uid"], tuple(managed["account"]), managed["live_sidecar"],
                managed_members, managed_dirs, managed_digests), LegacyAuxiliaryMembers(auxiliary_members, auxiliary_dirs))


class RetirementJournal:
    def __init__(self, io: ManagedInstaller, fence: RetirementFence) -> None:
        self.io, self.fence = io, fence
        self.reader = HomeRetirement(io)
        self._identity: tuple[int, ...] | None = None
        self._data: bytes | None = None

    def _root(self) -> list[int]:
        with self.io._publication_parent(STATE_DIRECTORY) as (parent, check):
            self.fence.require(self.io)
            check()
            # Same root-bound serialization inode; its mutable directory size
            # and timestamps deliberately do not become replacement evidence.
            return list(_signature(os.fstat(parent))[:5])

    def _read(self) -> tuple[bytes, tuple[int, ...]]:
        self.fence.require(self.io)
        with self.reader._entry(STATE_DIRECTORY + "/" + _NAME) as (parent, name, info):
            if (info is None or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or (info.st_uid, info.st_gid) != self.io.root_owner
                    or stat.S_IMODE(info.st_mode) != 0o600 or not 0 < info.st_size <= _LIMIT):
                raise RetirementUncertain()
            descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent)
            try:
                if _signature(os.fstat(descriptor)) != _signature(info):
                    raise RetirementUncertain()
                chunks, size = [], 0
                while True:
                    chunk = os.read(descriptor, _LIMIT + 1 - size)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > _LIMIT:
                        raise RetirementUncertain()
                if (size != info.st_size or _signature(os.fstat(descriptor)) != _signature(info)
                        or _signature(os.stat(name, dir_fd=parent, follow_symlinks=False)) != _signature(info)):
                    raise RetirementUncertain()
                return b"".join(chunks), _signature(info)
            finally:
                os.close(descriptor)

    def load(self) -> OriginalInventory:
        """Load only the saved original; no discovery, repair or acknowledgement."""
        try:
            data, identity = self._read()
            if self._identity is not None and (identity, data) != (self._identity, self._data):
                raise RetirementUncertain()
            record = self.io._json(data)
            if (record.keys() != {"format", "directory", "original"}
                    or record["format"] != "home-retirement-inventory@1" or record["directory"] != self._root()):
                raise RetirementUncertain()
            original = OriginalInventory._parse(record["original"])
            if _encode(dict(format=record["format"], directory=self._root(), original=original._record())) != data:
                raise RetirementUncertain()
            # A complete earlier write may have been interrupted before either
            # sync. Explicit reuse resynchronizes the same pinned inode.
            with self.io._publication_parent(STATE_DIRECTORY) as (parent, check):
                descriptor = os.open(_NAME, _FILE_FLAGS, dir_fd=parent)
                try:
                    if _signature(os.fstat(descriptor)) != identity:
                        raise RetirementUncertain()
                    os.fsync(descriptor)
                    os.fsync(parent)
                    check()
                    if _signature(os.fstat(descriptor)) != identity:
                        raise RetirementUncertain()
                finally:
                    os.close(descriptor)
            if self._read() != (data, identity):
                raise RetirementUncertain()
            self._identity, self._data = identity, data
            return original
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError, RecursionError):
            raise RetirementUncertain() from None

    def require_original_members(self, original: OriginalInventory) -> None:
        """Before the first mutation, compare original leaves and ancestors.

        The journal's intentional publication changes its own parent metadata.
        Only that directory's size/timestamps/link count may differ; its inode,
        custody and mode, and every captured leaf/other ancestor, stay pinned.
        This check is intentionally unsuitable after revocation changes the
        trust pair. That separate owner must authenticate its own new state.
        """
        self._require_members(original, revoked_pair=False)

    def require_revoke_members(self, original: OriginalInventory) -> None:
        """Pin non-trust originals while the real pair owner validates denies.

        Only the fixed state/anchor pair can have changed through committed
        revocation. The original trust lock, parent inode/custody/mode and every
        other member remain exact. This is not trust validation or cutoff proof.
        """
        self._require_members(original, revoked_pair=True)

    def require_controller_members(self, original: OriginalInventory, *, sidecar_stopped: bool = False) -> None:
        """Original artifacts after an independently observed controller effect.

        The caller separately proves current native ownership and real cutoff.
        A disabled original link may be absent. Only a stopped controller's
        fixed runtime leaves may be flushed or absent; the same applies to an
        independently stopped/process-empty sidecar. Present inode/custody and
        immutable files remain original. This supplies no native authority.
        """
        self._require_members(original, revoked_pair=True, controller_recovery=True,
            sidecar_stopped=sidecar_stopped)

    def _require_members(self, original: OriginalInventory, *, revoked_pair: bool,
                         controller_recovery: bool = False, sidecar_stopped: bool = False) -> None:
        try:
            self.fence.require(self.io)
            trust_parent = 'var/lib/happyranch-connector'
            changed = {trust_parent + '/trust-state.json', trust_parent + '/trust-state.json.anchor'} if revoked_pair else set()
            walker = _PrivateMembers(LegacyInventory(self.io), original.ownership)
            walker.parents.update({name: (original.managed.account, 0o700) for name in _RUNTIME})
            walker.parents.update({name: (self.io.root_owner, 0o700) for name in (
                _AuxiliaryMembers._MINTER, _AuxiliaryMembers._ISSUER,
                *(_AuxiliaryMembers._MINTER + "/" + user for user in _AuxiliaryMembers._USERS))})
            members = dict(original.private.members)
            for observation in (original.managed, original.auxiliary):
                for path, signature in observation.members.items():
                    if path in members and members[path] != signature:
                        raise RetirementUncertain()
                    members[path] = signature
            members.update({STATE_DIRECTORY + "/" + name: signature
                            for name, signature in original.ownership.receipt_identities.items()})
            service, native = original.ownership.service, original.ownership.native
            runtime = {'var/lib/happyranch-headscale', 'run/happyranch-headscale'} if controller_recovery else set()
            if sidecar_stopped:
                runtime.update(('var/lib/happyranch-tsnet-sidecar', 'run/happyranch-tsnet-sidecar'))
            members["etc/systemd/system/" + service.unit] = native.fragment
            members[service.executable] = native.executable
            if service.link is not None:
                members[service.link] = native.link
            directories = dict(original.private.directories)
            for observation in (original.managed, original.auxiliary):
                for path, signature in observation.directories.items():
                    if path in directories and directories[path] != signature:
                        raise RetirementUncertain()
                    directories[path] = signature
            for relative in sorted({str(PurePosixPath(path).parent) for path in members}):
                with walker._directory(relative) as parent:
                    if parent is not None and (relative in runtime or controller_recovery and relative in _RUNTIME):
                        allowed = {PurePosixPath(path).name for path in members
                                   if str(PurePosixPath(path).parent) == relative}
                        with os.scandir(parent) as entries:
                            for index, entry in enumerate(entries):
                                if index >= 128 or entry.name not in allowed:
                                    raise RetirementUncertain()
                    for path, expected in members.items():
                        if str(PurePosixPath(path).parent) != relative or path in changed:
                            continue
                        try:
                            current = None if parent is None else _signature(os.stat(
                                PurePosixPath(path).name, dir_fd=parent, follow_symlinks=False))
                        except FileNotFoundError:
                            current = None
                        if controller_recovery and path == service.link and current is None:
                            continue
                        if relative in runtime:
                            fields = 9 if expected is not None and stat.S_ISDIR(expected[4]) else 6
                            if current is not None and (expected is None or current[:fields] != expected[:fields]):
                                raise RetirementUncertain()
                            continue
                        if current != expected:
                            raise RetirementUncertain()
            for path, expected in directories.items():
                current = walker.directory_identities.get(path)
                mutable = path == STATE_DIRECTORY or revoked_pair and path == trust_parent or path in runtime
                if current is None or (current[:5] != expected[:5] if mutable else current != expected):
                    raise RetirementUncertain()
            self.fence.require(self.io)
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
            raise RetirementUncertain() from None

    def capture(self, ownership: LegacyOwnership) -> OriginalInventory:
        """Capture all original members before effects, never overwrite a journal.

        The coordinator pins the original connector first. This journal grants
        neither physical cutoff nor permission to replace that separate pin.
        Recovery must call load, rather than capture a partially cleaned layout.
        """
        try:
            self.fence.require(self.io)
            inventory = LegacyInventory(self.io)
            private = inventory.private_members(ownership, fence=self.fence)
            managed = inventory.managed_members(ownership, fence=self.fence)
            auxiliary = inventory.auxiliary_members(ownership, fence=self.fence)
            inventory.require_private(ownership, private, fence=self.fence)
            inventory.require_managed(ownership, managed, fence=self.fence)
            inventory.require_auxiliary(ownership, auxiliary, fence=self.fence)
            original = OriginalInventory(ownership, private, managed, auxiliary)
            data = _encode(dict(format="home-retirement-inventory@1", directory=self._root(), original=original._record()))
            # Validate the closed serialization before publishing any bytes.
            OriginalInventory._parse(self.io._json(data)["original"])
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
                    self.fence.require(self.io)
                    if _signature(os.fstat(descriptor)) != identity or self._read() != (data, identity):
                        raise RetirementUncertain()
                finally:
                    # Interrupted or foreign bytes are preserved, never reset.
                    os.close(descriptor)
            self._identity, self._data = identity, data
            self.require_original_members(original)
            inventory.require(ownership, fence=self.fence)
            self.require_original_members(original)
            return self.load()
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError, RecursionError):
            raise RetirementUncertain() from None
