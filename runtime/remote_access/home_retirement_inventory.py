"""Read-only historical lineage for explicit home retirement.

Names, matching candidate hashes and successful native commands cannot establish
ownership. This reader composes the protected historical account/publication
receipts, the closed activation schema, fixed released binary/unit digests and
the native ownership reader. It grants no admission, stop or deletion itself.
An in-memory observation must be freshly revalidated before an effect; durable
recovery inventory and original connector cutoff remain separate prerequisites.
"""
from __future__ import annotations

import hashlib
import fcntl
import json
import os
import platform
import re
import stat
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Iterator, Mapping

from runtime.remote_access.home_retirement import (
    ACCOUNT, STATE_DIRECTORY, UNIT, HomeRetirement, RetirementUncertain,
    _DIRECTORY_FLAGS, _FILE_FLAGS, _signature,
)
from runtime.remote_access.home_retirement_fence import RetirementFence
from runtime.remote_access.home_retirement_service import OwnedService, ServiceIdentity, ServiceObservation
from runtime.remote_access.legacy_home_receipt import (
    HomeActivationReceipt, HomeActivationTransaction, LIVE_MEMBERS, OWNED_LINK_TARGET,
)

if TYPE_CHECKING:
    from runtime.remote_access.installer import ManagedInstaller

# Source provenance: 8ba249d01807f8ef08b5f82d4780a01ea3e3d660
# systemd_unit.render_headscale_unit(False/True), and headscale-pin.json v0.25.1.
# These values survive removal of the provisioning renderer/download machinery.
_INERT_UNIT = "ca7aab44898b455c353ccbbf31abc015f42383d9cedc2ad1d11a911fe3b9b9f3"
_HOME_UNIT = "345bce5ff33ddd08c08d90e96e103688621ffa6c8b91598b5dda5efac63066b6"
_BINARIES = {
    "x86_64": "d2cda0a5d748587f77c920a76cd1bf1ab429e5299ba5bc6b3dda90712721b45b",
    "aarch64": "49dfe4254c9251f3e8acc73792fd00263f0727074ac4ea1c7d1b2e670818869b",
}
_ACCOUNT_RECEIPT = "headscale-account.json"
_PUBLICATION = "headscale-publication.json"
_ACTIVATION = "home-activation.json"
_PUBLIC_MEMBERS = frozenset((
    "/etc/happyranch/headscale/config.yaml", "/etc/happyranch/headscale/derp.yaml",
))
_TLS_MEMBERS = frozenset((
    "/etc/happyranch/headscale/server.crt", "/etc/happyranch/headscale/server.key",
    "/etc/happyranch/control-ca.pem",
))


@dataclass(frozen=True, repr=False)
class LegacyOwnership:
    service: ServiceIdentity
    native: ServiceObservation
    activation: HomeActivationReceipt | None
    receipt_digests: Mapping[str, str] = field(repr=False)
    receipt_identities: Mapping[str, tuple[int, ...]] = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt_digests", MappingProxyType(dict(self.receipt_digests)))
        object.__setattr__(self, "receipt_identities", MappingProxyType(dict(self.receipt_identities)))

    def __repr__(self) -> str:
        return "LegacyOwnership(<redacted>)"


@dataclass(frozen=True, repr=False)
class LegacyPrivateMembers:
    """Pinned private custody, not a durable receipt or deletion capability."""

    members: Mapping[str, tuple[int, ...] | None] = field(repr=False)
    directories: Mapping[str, tuple[int, ...]] = field(repr=False)

    def __post_init__(self) -> None:
        for name in ("members", "directories"):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))

    def __repr__(self) -> str:
        return "LegacyPrivateMembers(<redacted>)"


@dataclass(frozen=True, repr=False)
class LegacyManagedMembers:
    """Historical managed files, never an acknowledgement or cleanup receipt."""

    identity: tuple[str, str, str]
    control_plane_id: str
    release_uid: int
    account: tuple[int, int]
    live_sidecar: bool
    members: Mapping[str, tuple[int, ...] | None] = field(repr=False)
    directories: Mapping[str, tuple[int, ...]] = field(repr=False)
    digests: Mapping[str, str] = field(repr=False)

    def __post_init__(self) -> None:
        for name in ("members", "directories", "digests"):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))

    def __repr__(self) -> str:
        return "LegacyManagedMembers(<redacted>)"


@dataclass(frozen=True, repr=False)
class LegacyAuxiliaryMembers:
    """Original root-private journals; no mint, cleanup or effect authority."""

    members: Mapping[str, tuple[int, ...] | None] = field(repr=False)
    directories: Mapping[str, tuple[int, ...]] = field(repr=False)

    def __post_init__(self) -> None:
        for name in ("members", "directories"):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))

    def __repr__(self) -> str:
        return "LegacyAuxiliaryMembers(<redacted>)"


class LegacyInventory:
    def __init__(self, io: ManagedInstaller) -> None:
        self.io, self.reader = io, HomeRetirement(io)

    def _file(self, relative: str, *, private: bool = False, optional: bool = False) -> bytes | None:
        with self.reader._entry(relative) as (parent, name, info):
            if info is None:
                if optional:
                    return None
                raise RetirementUncertain()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or (info.st_uid, info.st_gid) != self.io.root_owner
                    or stat.S_IMODE(info.st_mode) != (0o600 if private else 0o644)
                    or info.st_size > 65536):
                raise RetirementUncertain()
            # The shared reader's smaller private-final-record budget is not
            # the historical schema budget. Custody is checked explicitly here.
            return self.reader._read(parent, name, info)

    def _receipt(self, name: str, fence: RetirementFence | None, *, optional: bool = False
                 ) -> tuple[bytes, tuple[int, ...]] | None:
        pending = STATE_DIRECTORY + "/." + name + ".pending"
        if name == _ACTIVATION and fence is not None:
            if type(fence) is not RetirementFence:
                raise RetirementUncertain()
            fence.require(self.io)
        elif self.reader._present(pending):
            raise RetirementUncertain()
        relative = STATE_DIRECTORY + "/" + name
        with self.reader._entry(relative) as (_, _, info):
            data = self._file(relative, private=True, optional=optional)
            if info is None:
                if data is not None:
                    raise RetirementUncertain()
                return None
            return data, _signature(info)

    @staticmethod
    def _digest(value: object) -> bool:
        return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None

    def _account(self, receipt: dict) -> tuple[int, int]:
        if (receipt.keys() != {"version", "account", "uid", "gid", "group_created_with_user"}
                or type(receipt["version"]) is not int or receipt["version"] != 1
                or receipt["account"] != ACCOUNT or receipt["group_created_with_user"] is not True
                or any(type(receipt[key]) is not int or not 0 < receipt[key] < 2**32 - 1
                       for key in ("uid", "gid"))):
            raise RetirementUncertain()
        uid, gid = receipt["uid"], receipt["gid"]
        user, group = self.io._lookup_user(ACCOUNT), self.io._lookup_group(ACCOUNT)
        if ((user.pw_name, user.pw_uid, user.pw_gid) != (ACCOUNT, uid, gid)
                or (group.gr_name, group.gr_gid) != (ACCOUNT, gid)
                or set(group.gr_mem) - {ACCOUNT}
                or user.pw_shell not in ("/usr/sbin/nologin", "/sbin/nologin", "/bin/false")
                or type(user.pw_dir) is not str or not user.pw_dir.startswith("/")
                or ".." in user.pw_dir.split("/") or user.pw_dir == "/"):
            raise RetirementUncertain()
        # Receipt lineage alone cannot adopt a reused or shared numeric account.
        definitions = self._file("etc/login.defs").decode("ascii")
        limits: dict[str, int] = {}
        for line in definitions.splitlines():
            fields = line.split("#", 1)[0].split()
            if fields and fields[0] in {"UID_MIN", "GID_MIN", "SYS_UID_MIN", "SYS_UID_MAX", "SYS_GID_MIN", "SYS_GID_MAX"}:
                if (len(fields) != 2 or not fields[1].isascii() or not fields[1].isdigit()
                        or fields[0] in limits):
                    raise RetirementUncertain()
                limits[fields[0]] = int(fields[1])
        for kind, value in (("UID", uid), ("GID", gid)):
            low = limits.get("SYS_" + kind + "_MIN", 101)
            regular = limits.get(kind + "_MIN", 1000)
            high = limits.get("SYS_" + kind + "_MAX", regular - 1)
            if not 0 < low <= value <= high < regular:
                raise RetirementUncertain()
        for database, field, value in (("passwd", 2, uid), ("passwd", 3, gid), ("group", 2, gid)):
            code, output = self.io._run(["getent", database], timeout=10.0)
            if code or len(output.encode()) > 65536:
                raise RetirementUncertain()
            matches = []
            for line in output.splitlines():
                fields = line.split(":")
                if (len(fields) != (7 if database == "passwd" else 4)
                        or not fields[field].isascii() or not fields[field].isdigit()):
                    raise RetirementUncertain()
                if int(fields[field]) == value:
                    matches.append(fields[0])
            if matches != [ACCOUNT]:
                raise RetirementUncertain()
        code, output = self.io._run(["passwd", "--status", ACCOUNT], timeout=10.0)
        fields = output.split()
        if code or len(output.encode()) > 65536 or len(fields) != 7 or fields[:2] != [ACCOUNT, "L"]:
            raise RetirementUncertain()
        # Retain the historical protected-home rule. Only /nonexistent may be
        # absent; other homes must exist with protected no-follow parents.
        with self.reader._entry(user.pw_dir.lstrip("/") + "/retirement-probe") as (_, name, _info):
            if name != "retirement-probe" and user.pw_dir != "/nonexistent":
                raise RetirementUncertain()
        return uid, gid

    def _publication(self, data: bytes, account: tuple[int, int]) -> dict:
        record = self.io._json(data)
        extended = "directories" in record
        if (record.keys() != {"version", "uid", "gid", "dial_ip", "files"}
                | ({"directories"} if extended else set())
                or type(record["version"]) is not int or record["version"] != 1
                or any(type(record[key]) is not int for key in ("uid", "gid"))
                or (record["uid"], record["gid"]) != account
                or type(record["files"]) is not dict
                or record["files"].keys() != _PUBLIC_MEMBERS | (_TLS_MEMBERS if extended else frozenset())
                or any(not self._digest(value) for value in record["files"].values())
                or extended and record["directories"] != ["/etc/happyranch/empty-ca-directory"]):
            raise RetirementUncertain()
        from runtime.remote_access.invite_v2 import _canonical_dial_ip
        if type(record["dial_ip"]) is not str or not _canonical_dial_ip(record["dial_ip"]):
            raise RetirementUncertain()
        return record

    def observe(self, *, fence: RetirementFence | None = None) -> LegacyOwnership:
        try:
            return self._observe(fence)
        except (OSError, ValueError, RuntimeError, KeyError, AttributeError, TypeError):
            raise RetirementUncertain() from None

    def _observe(self, fence: RetirementFence | None) -> LegacyOwnership:
        originals = {name: self._receipt(name, fence, optional=name == _ACTIVATION)
                     for name in (_ACCOUNT_RECEIPT, _PUBLICATION, _ACTIVATION)}
        account = self._account(self.io._json(originals[_ACCOUNT_RECEIPT][0]))
        publication = self._publication(originals[_PUBLICATION][0], account)
        activation = (None if originals[_ACTIVATION] is None else
                      HomeActivationReceipt.parse(originals[_ACTIVATION][0]))
        fragment = self._file("etc/systemd/system/" + UNIT)
        digest = hashlib.sha256(fragment).hexdigest()
        if digest not in (_INERT_UNIT, _HOME_UNIT):
            raise RetirementUncertain()
        if digest == _HOME_UNIT and activation is None:
            raise RetirementUncertain()
        if activation is not None:
            if (activation.member_digests[OWNED_LINK_TARGET] != digest
                    or activation.dial_ip != publication["dial_ip"]
                    or not _TLS_MEMBERS <= publication["files"].keys()
                    or any(activation.member_digests[path] != publication["files"][path]
                           for path in _PUBLIC_MEMBERS | {"/etc/happyranch/control-ca.pem"})):
                raise RetirementUncertain()
        service = ServiceIdentity(unit=UNIT, account=ACCOUNT, uid=account[0], gid=account[1],
            fragment_sha256=digest, executable="opt/happyranch-desktop/libexec/headscale/headscale",
            executable_sha256=_BINARIES[platform.machine()],
            link="etc/systemd/system/happyranch-managed.target.wants/" + UNIT)
        owner = OwnedService(self.io, service)
        native = owner.observe()
        # An inert prepared install can have no live controller/link. Active
        # ownership requires the actual closed activation record and home unit.
        if (native.process is not None or native.link is not None) and (
                activation is None or digest != _HOME_UNIT):
            raise RetirementUncertain()
        if (self._account(self.io._json(originals[_ACCOUNT_RECEIPT][0])) != account
                or any(self._receipt(name, fence, optional=name == _ACTIVATION) != data
                       for name, data in originals.items())
                or owner.observe() != native):
            raise RetirementUncertain()
        return LegacyOwnership(service, native, activation,
            {name: hashlib.sha256(data[0]).hexdigest() for name, data in originals.items() if data is not None},
            {name: data[1] for name, data in originals.items() if data is not None})

    def require(self, observed: LegacyOwnership, *, fence: RetirementFence | None = None) -> None:
        if type(observed) is not LegacyOwnership or self.observe(fence=fence) != observed:
            raise RetirementUncertain()

    def private_members(self, observed: LegacyOwnership, *, fence: RetirementFence) -> LegacyPrivateMembers:
        """Explicit retirement prerequisite; immutable digests come from lineage.

        Opaque mutable DB/key/socket bytes and the staging CA private key are
        never opened. Absence is recorded, not inferred to mean an owned removal.
        The coordinator must preserve this inventory before effects and obtain
        separate connector/sidecar, issuer/minter, cutoff and stop proofs.
        """
        try:
            fence.require(self.io)
            self.require(observed, fence=fence)
            reader = _PrivateMembers(self, observed)
            first = reader.observe()
            self.require(observed, fence=fence)
            if reader.observe() != first:
                raise RetirementUncertain()
            fence.require(self.io)
            return first
        except (OSError, ValueError, RuntimeError, KeyError, AttributeError, TypeError):
            raise RetirementUncertain() from None

    def require_private(self, ownership: LegacyOwnership, observed: LegacyPrivateMembers,
                        *, fence: RetirementFence) -> None:
        if type(observed) is not LegacyPrivateMembers or self.private_members(ownership, fence=fence) != observed:
            raise RetirementUncertain()


    def managed_members(self, observed: LegacyOwnership, *, fence: RetirementFence) -> LegacyManagedMembers:
        """Observe protected managed provenance without admitting old home mode.

        No trust parser/initializer, socket connection, token-source read or
        native effect occurs here. Cutoff must independently parse the real
        pair and obtain the original owner's physical acknowledgements.
        """
        try:
            fence.require(self.io)
            self.require(observed, fence=fence)
            reader = _ManagedMembers(self, observed)
            first = reader.observe()
            self.require(observed, fence=fence)
            if reader.observe() != first:
                raise RetirementUncertain()
            fence.require(self.io)
            return first
        except (OSError, ValueError, RuntimeError, KeyError, AttributeError, TypeError):
            raise RetirementUncertain() from None

    def require_managed(self, ownership: LegacyOwnership, observed: LegacyManagedMembers,
                        *, fence: RetirementFence) -> None:
        if type(observed) is not LegacyManagedMembers or self.managed_members(ownership, fence=fence) != observed:
            raise RetirementUncertain()

    def auxiliary_members(self, observed: LegacyOwnership, *, fence: RetirementFence) -> LegacyAuxiliaryMembers:
        """Inventory existing issuer, transaction and minter state without creation.

        Directory/issuer-lock contention refuses. These sampled locks are not
        retained by the returned evidence; the coordinator must separately hold
        actual quiescence/exclusion before deleting any captured member.
        """
        try:
            fence.require(self.io)
            self.require(observed, fence=fence)
            reader = _AuxiliaryMembers(self, observed, fence)
            first = reader.observe()
            self.require(observed, fence=fence)
            if reader.observe() != first:
                raise RetirementUncertain()
            fence.require(self.io)
            return first
        except (OSError, ValueError, RuntimeError, KeyError, AttributeError, TypeError):
            raise RetirementUncertain() from None

    def require_auxiliary(self, ownership: LegacyOwnership, observed: LegacyAuxiliaryMembers,
                          *, fence: RetirementFence) -> None:
        if type(observed) is not LegacyAuxiliaryMembers or self.auxiliary_members(ownership, fence=fence) != observed:
            raise RetirementUncertain()


class _PrivateMembers:
    """Fixed private layout; service-owned parents cannot use the root reader."""

    def __init__(self, inventory: LegacyInventory, ownership: LegacyOwnership) -> None:
        self.inventory, self.io, self.ownership = inventory, inventory.io, ownership
        service = ownership.service
        self.account = (service.uid, service.gid)
        self.parents = {name: (self.account, 0o700) for name in (
            "etc/happyranch/headscale", "var/lib/happyranch-headscale", "run/happyranch-headscale")}
        self.parents.update({STATE_DIRECTORY: (self.io.root_owner, 0o700),
            STATE_DIRECTORY + "/home-ca": (self.io.root_owner, 0o700)})
        self.directory_identities: dict[str, tuple[int, ...]] = {}

    def _remember_directory(self, path: str, info: os.stat_result) -> None:
        signature = _signature(info)
        if self.directory_identities.get(path, signature) != signature:
            raise RetirementUncertain()
        self.directory_identities[path] = signature

    @contextmanager
    def _directory(self, relative: str) -> Iterator[int | None]:
        descriptor = os.open("/", _DIRECTORY_FLAGS)
        opened, parents = [descriptor], []
        current = Path("/")
        missing: tuple[int, str] | None = None
        try:
            if self.io.root == Path("/"):
                info = os.fstat(descriptor)
                if (info.st_uid, info.st_gid) != self.io.root_owner or info.st_mode & 0o022:
                    raise RetirementUncertain()
                self._remember_directory(".", info)
            for part in (self.io.root / relative).parts[1:]:
                try:
                    before = os.stat(part, dir_fd=descriptor, follow_symlinks=False)
                except FileNotFoundError:
                    missing = descriptor, part
                    break
                child = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
                opened.append(child)
                info = os.fstat(child)
                current /= part
                if not stat.S_ISDIR(info.st_mode) or _signature(info) != _signature(before):
                    raise RetirementUncertain()
                if current.is_relative_to(self.io.root):
                    rule = self.parents.get(str(current.relative_to(self.io.root)))
                    if rule is None:
                        if (info.st_uid, info.st_gid) != self.io.root_owner or info.st_mode & 0o022:
                            raise RetirementUncertain()
                    elif (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (*rule[0], rule[1]):
                        raise RetirementUncertain()
                    self._remember_directory(str(current.relative_to(self.io.root)), info)
                parents.append((descriptor, part, child, _signature(info)))
                descriptor = child
            yield None if missing is not None else descriptor
            for ancestor, name, child, signature in parents:
                if (_signature(os.fstat(child)) != signature
                        or _signature(os.stat(name, dir_fd=ancestor, follow_symlinks=False)) != signature):
                    raise RetirementUncertain()
            if missing is not None:
                try:
                    os.stat(missing[1], dir_fd=missing[0], follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise RetirementUncertain()
        finally:
            for child in reversed(opened):
                os.close(child)

    def observe(self) -> LegacyPrivateMembers:
        self.directory_identities = {}
        publication = self.inventory.io._json(self.inventory._receipt(_PUBLICATION, None)[0])
        members = {path.lstrip("/"): ("file", self.account, 0o600, digest)
                   for path, digest in publication["files"].items()}
        control = "etc/happyranch/control-ca.pem"
        if control in members:
            group = self.io._lookup_group("happyranch")
            if group.gr_name != "happyranch" or type(group.gr_gid) is not int or not 0 < group.gr_gid < 2**32 - 1:
                raise RetirementUncertain()
            members[control] = ("file", (self.io.root_owner[0], group.gr_gid), 0o640, members[control][3])
            members["etc/happyranch/empty-ca-directory"] = ("directory", self.io.root_owner, 0o755, None)
        activation = self.ownership.activation
        if activation is not None:
            for path, digest in activation.member_digests.items():
                path = path.lstrip("/")
                if path == OWNED_LINK_TARGET.lstrip("/"):
                    continue  # Native service reader already owns this member.
                owner = self.account if path.endswith("/policy.hujson") else self.io.root_owner
                members.setdefault(path, ("file", owner, 0o600, digest))
        for path in LIVE_MEMBERS:
            if path.endswith("/observe.sock"):
                continue  # Shared sidecar is a separate ownership prerequisite.
            kind = "socket" if path.endswith(".sock") else "directory" if path.endswith("/acme-unused") else "file"
            members[path.lstrip("/")] = (kind,
                (self.account[0], None) if kind == "directory" else self.account,
                0o700 if kind == "directory" else 0o600, None)
        ca = STATE_DIRECTORY + "/home-ca"
        for name in ("ca.pem", "ca.key", "leaf.pem", "leaf.key"):
            source = control if name == "ca.pem" else "etc/happyranch/headscale/server.crt" if name == "leaf.pem" else ""
            members[ca + "/" + name] = ("file", self.io.root_owner, 0o600,
                members[source][3] if source in members else None)
        required = {path for path, value in members.items() if value[3] is not None and not path.startswith(ca + "/")}
        if control in members:
            required.add("etc/happyranch/empty-ca-directory")
        result, directories = {}, {}
        for relative in sorted({str(Path(path).parent) for path in members} | (set(self.parents) - {STATE_DIRECTORY})):
            allowed = {Path(path).name for path in members if str(Path(path).parent) == relative}
            with self._directory(relative) as parent:
                if parent is None:
                    for name in allowed:
                        if relative + "/" + name in required:
                            raise RetirementUncertain()
                        result[relative + "/" + name] = None
                    continue
                directories[relative] = _signature(os.fstat(parent))
                with os.scandir(parent) as entries:
                    names = set()
                    for index, item in enumerate(entries):
                        if index >= 128 or relative in self.parents and item.name not in allowed:
                            raise RetirementUncertain()
                        names.add(item.name)
                if any("." + name + ".pending" in names for name in allowed):
                    raise RetirementUncertain()
                if relative == ca:
                    if (names not in (set(), {"ca.pem", "ca.key"}, {"ca.pem", "ca.key", "leaf.pem", "leaf.key"})
                            or names and control not in members):
                        raise RetirementUncertain()
                for name in sorted(allowed):
                    path = relative + "/" + name
                    kind, owner, mode, digest = members[path]
                    if name not in names:
                        if path in required:
                            raise RetirementUncertain()
                        result[path] = None
                        continue
                    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    valid = {"file": stat.S_ISREG, "socket": stat.S_ISSOCK, "directory": stat.S_ISDIR}[kind]
                    if (not valid(info.st_mode) or info.st_uid != owner[0] or owner[1] is not None and info.st_gid != owner[1]
                            or stat.S_IMODE(info.st_mode) != mode or info.st_nlink != (2 if kind == "directory" else 1)
                            or relative == ca and not 0 < info.st_size <= 4096):
                        raise RetirementUncertain()
                    signature = _signature(info)
                    if kind == "directory":
                        child = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent)
                        try:
                            with os.scandir(child) as entries:
                                if next(entries, None) is not None or _signature(os.fstat(child)) != signature:
                                    raise RetirementUncertain()
                        finally:
                            os.close(child)
                    elif digest is not None:
                        descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent)
                        try:
                            if info.st_size > 65536 or _signature(os.fstat(descriptor)) != signature:
                                raise RetirementUncertain()
                            data = os.read(descriptor, 65537)
                            if (len(data) != info.st_size or hashlib.sha256(data).hexdigest() != digest
                                    or _signature(os.fstat(descriptor)) != signature):
                                raise RetirementUncertain()
                        finally:
                            os.close(descriptor)
                    if _signature(os.stat(name, dir_fd=parent, follow_symlinks=False)) != signature:
                        raise RetirementUncertain()
                    result[path] = signature
                if _signature(os.fstat(parent)) != directories[relative]:
                    raise RetirementUncertain()
        return LegacyPrivateMembers(result, self.directory_identities)


class _ManagedMembers(_PrivateMembers):
    """Reuse the pinned no-follow traversal for the fixed managed layout."""

    def __init__(self, inventory: LegacyInventory, ownership: LegacyOwnership) -> None:
        super().__init__(inventory, ownership)
        self.account = self._account()
        self.parents = {name: (self.account, 0o700) for name in (
            "var/lib/happyranch-connector", "var/lib/happyranch-tsnet-sidecar",
            "run/happyranch-connector", "run/happyranch-tsnet-sidecar")}

    def _account(self) -> tuple[int, int]:
        user, group = self.io._lookup_user("happyranch"), self.io._lookup_group("happyranch")
        if (user.pw_name != "happyranch" or group.gr_name != "happyranch"
                or user.pw_gid != group.gr_gid
                or any(type(value) is not int or not 0 < value < 2**32 - 1
                       for value in (user.pw_uid, user.pw_gid, group.gr_gid))):
            raise RetirementUncertain()
        # This shared account is observed only, never selected for deletion.
        return user.pw_uid, group.gr_gid

    def _config(self, parent: int, name: str) -> tuple[bytes, tuple[int, ...]]:
        owner, mode = ((self.io.root_owner, 0o600) if name in ("provision.json", "invite-issuer.json") else
            (self.io.root_owner, 0o644) if name == "release-daemon.json" else
            (self.account, 0o600) if name == "daemon.token" else
            ((self.io.root_owner[0], self.account[1]), 0o640))
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        signature = _signature(before)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or (before.st_uid, before.st_gid) != owner or stat.S_IMODE(before.st_mode) != mode
                or not 0 < before.st_size <= 65536):
            raise RetirementUncertain()
        descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent)
        try:
            if _signature(os.fstat(descriptor)) != signature:
                raise RetirementUncertain()
            chunks, size = [], 0
            while True:
                chunk = os.read(descriptor, 65537 - size)
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > 65536:
                    raise RetirementUncertain()
            if (size != before.st_size or _signature(os.fstat(descriptor)) != signature
                    or _signature(os.stat(name, dir_fd=parent, follow_symlinks=False)) != signature):
                raise RetirementUncertain()
            return b"".join(chunks), signature
        finally:
            os.close(descriptor)

    def _configs(self) -> dict[str, tuple[bytes, tuple[int, ...]]]:
        with self._directory("etc/happyranch") as parent:
            if parent is None:
                raise RetirementUncertain()
            names = ("connector.json", "sidecar.json", "policy.json", "release-daemon.json",
                     "daemon.token", "provision.json", "invite-issuer.json")
            for name in ("install-transaction.json", *("." + name + ".pending" for name in
                         (*names, "install-transaction.json"))):
                try:
                    os.stat(name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                raise RetirementUncertain()
            files = {}
            for name in names:
                try:
                    files[name] = self._config(parent, name)
                except FileNotFoundError:
                    if name != "invite-issuer.json" or self.ownership.activation is not None:
                        raise
            return files

    def _provenance(self, files: dict[str, tuple[bytes, tuple[int, ...]]]) -> tuple[tuple[str, str, str], str, int, bool]:
        from runtime.remote_access.invite_issuer import HomeInviteConfigRecord, IssuerError
        from runtime.remote_access.legacy_home_receipt import CONTROL_URL, IDENTITY_KEYS
        from runtime.remote_access.release_verifier import validate_record

        data = {name: item[0] for name, item in files.items()}
        record = self.io._json(data["provision.json"])
        self.io._validate_install_record("provision.json", data["provision.json"])
        if type(record.get("version")) is not int or record["version"] != 1 or record["layout"] != "deb":
            raise RetirementUncertain()
        release = self.io._json(data["release-daemon.json"])
        validate_record(release)
        user = self.io._lookup_user(release["user"])
        if (user.pw_name, user.pw_uid) != (release["user"], release["uid"]):
            raise RetirementUncertain()
        connector, sidecar = self.io._json(data["connector.json"]), self.io._json(data["sidecar.json"])
        receipt = self.ownership.activation
        live = "home_binding" in sidecar
        if live:
            binding = sidecar["home_binding"]
            if (receipt is None or type(binding) is not dict
                    or type(binding.get("activation_generation")) is not int):
                raise RetirementUncertain()
            additions = dict(ControlURL=CONTROL_URL,
                control_tls=dict(dial_ip=receipt.dial_ip, ca_sha256=receipt.ca_der_sha256),
                home_binding={key: getattr(receipt, key) for key in
                    ("home_id", "control_plane_id", "connector_id", "activation_generation")})
            inert = {key: value for key, value in sidecar.items() if key not in additions}
            if sidecar != {**inert, **additions}:
                raise RetirementUncertain()
            # Historical publication inserts these exact bytes before the last
            # brace. Do not canonicalize an unrelated candidate into provenance.
            addition = b"," + json.dumps(additions, separators=(",", ":"), ensure_ascii=True).encode()[1:-1]
            end = data["sidecar.json"].rindex(b"}")
            if data["sidecar.json"][end-len(addition):end] != addition:
                raise RetirementUncertain()
            data["sidecar.json"] = data["sidecar.json"][:end-len(addition)] + data["sidecar.json"][end:]
        self.io._validate_configs({name: data[name] for name in ("connector.json", "sidecar.json", "policy.json")},
                                  staged=True, require_current=False)
        if (connector["managed"]["profile"] != "linux-managed-identity-v1"
                or record["digests"] != {name: hashlib.sha256(data[name]).hexdigest() for name in
                    ("connector.json", "sidecar.json", "policy.json", "release-daemon.json", "daemon.token")}):
            raise RetirementUncertain()
        identity = tuple(connector[key] for key in ("tenant_id", "home_id", "connector_id"))
        control = connector["managed"]["control_plane_id"]
        if receipt is not None and (receipt.release_uid != release["uid"] or receipt.home_id != identity[1]
                or receipt.connector_id != identity[2] or receipt.control_plane_id != control):
            raise RetirementUncertain()
        if "invite-issuer.json" in data:
            try:
                invite = HomeInviteConfigRecord.parse(data["invite-issuer.json"])
            except IssuerError:
                raise RetirementUncertain() from None
            if ((invite.release_uid, invite.home_id, invite.connector_id, invite.control_plane_id)
                    != (release["uid"], identity[1], identity[2], control)
                    or receipt is not None and (any(getattr(invite, key) != getattr(receipt, key) for key in IDENTITY_KEYS)
                        or hashlib.sha256(data["invite-issuer.json"]).hexdigest()
                        != receipt.member_digests["/etc/happyranch/invite-issuer.json"])):
                raise RetirementUncertain()
        return identity, control, release["uid"], live

    def _runtime(self) -> dict[str, tuple[int, ...] | None]:
        layouts = {
            "var/lib/happyranch-connector": ("trust-state.json", "trust-state.json.anchor", "trust-state.json.lock"),
            "var/lib/happyranch-tsnet-sidecar": ("tailscaled.state", "credential.consumed", "tailscaled.log.conf",
                "tailscaled.log1.txt", "tailscaled.log2.txt"),
            "run/happyranch-connector": ("control.sock", "ingress.sock"),
            "run/happyranch-tsnet-sidecar": ("observe.sock", "health.json"),
        }
        result = {}
        for relative, allowed in layouts.items():
            with self._directory(relative) as parent:
                names = set()
                if parent is not None:
                    with os.scandir(parent) as entries:
                        for index, item in enumerate(entries):
                            diagnostic = relative == "run/happyranch-tsnet-sidecar" and re.fullmatch(r"\.health-[0-9]+", item.name)
                            if index >= 10 or item.name not in allowed and not diagnostic:
                                raise RetirementUncertain()
                            names.add(item.name)
                if relative == "var/lib/happyranch-connector" and names not in (set(), set(allowed)):
                    raise RetirementUncertain()
                if len(names - {"observe.sock"}) > 8 and relative == "run/happyranch-tsnet-sidecar":
                    raise RetirementUncertain()
                for name in sorted(names | set(allowed)):
                    path = relative + "/" + name
                    if name not in names:
                        result[path] = None
                        continue
                    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    socket = name.endswith(".sock")
                    diagnostic = relative == "run/happyranch-tsnet-sidecar" and not socket
                    if (not (stat.S_ISSOCK(info.st_mode) if socket else stat.S_ISREG(info.st_mode))
                            or info.st_nlink != 1 or (info.st_uid, info.st_gid) != self.account
                            or stat.S_IMODE(info.st_mode) not in ((0o644,) if name == "health.json" else
                                (0o600, 0o644) if diagnostic else (0o600,))
                            or diagnostic and info.st_size > 65536):
                        raise RetirementUncertain()
                    # Opaque state, locks, diagnostics and sockets are metadata-
                    # only. No content open, connect, initialization or chmod.
                    result[path] = _signature(info)
        return result

    def observe(self) -> LegacyManagedMembers:
        self.directory_identities = {}
        if self._account() != self.account:
            raise RetirementUncertain()
        files = self._configs()
        identity, control, release_uid, live = self._provenance(files)
        runtime = self._runtime()
        if self._configs() != files or self._account() != self.account:
            raise RetirementUncertain()
        members = {"etc/happyranch/" + name: item[1] for name, item in files.items()}
        members.setdefault("etc/happyranch/invite-issuer.json", None)
        members.update(runtime)
        return LegacyManagedMembers(identity, control, release_uid, self.account, live,
            members, self.directory_identities,
            {"etc/happyranch/" + name: hashlib.sha256(item[0]).hexdigest() for name, item in files.items()})


class _AuxiliaryMembers(_PrivateMembers):
    _MINTER = STATE_DIRECTORY + "/home-minter"
    _ISSUER = STATE_DIRECTORY + "/home-issuer"
    _USERS = ("happyranch-invite", "happyranch-node")
    _RECEIPT = "home-issuer-publication.json"
    _TRANSACTION = "home-activation-txn.json"
    _MAX_OPERATIONS = 4096

    def __init__(self, inventory: LegacyInventory, ownership: LegacyOwnership, fence: RetirementFence) -> None:
        super().__init__(inventory, ownership)
        self.fence = fence
        self.parents.update({name: (self.io.root_owner, 0o700) for name in (
            self._MINTER, self._ISSUER, *(self._MINTER + "/" + user for user in self._USERS))})

    def _operation(self, parent: int, name: str) -> tuple[int, ...]:
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or (before.st_uid, before.st_gid) != self.io.root_owner
                or stat.S_IMODE(before.st_mode) != 0o600 or not 0 < before.st_size <= 160):
            raise RetirementUncertain()
        descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent)
        try:
            if _signature(os.fstat(descriptor)) != _signature(before):
                raise RetirementUncertain()
            data = os.read(descriptor, 161)
            started, expires, end = data.decode("ascii").split("\n")
            start, expiry = datetime.fromisoformat(started), datetime.fromisoformat(expires)
            if (end or start.utcoffset() != timedelta(0) or expiry.utcoffset() != timedelta(0)
                    or not timedelta(0) < expiry - start <= timedelta(minutes=5)
                    or data != (start.isoformat() + "\n" + expiry.isoformat() + "\n").encode("ascii")
                    or _signature(os.fstat(descriptor)) != _signature(before)
                    or _signature(os.stat(name, dir_fd=parent, follow_symlinks=False)) != _signature(before)):
                raise RetirementUncertain()
            return _signature(before)
        finally:
            os.close(descriptor)

    def _minter(self) -> dict[str, tuple[int, ...] | None]:
        result: dict[str, tuple[int, ...] | None] = {}
        with self._directory(self._MINTER) as parent:
            if parent is not None:
                with os.scandir(parent) as entries:
                    for index, entry in enumerate(entries):
                        if index >= 2 or entry.name not in self._USERS:
                            raise RetirementUncertain()
            result[self._MINTER] = None if parent is None else _signature(os.fstat(parent))
        count = 0
        for user in self._USERS:
            relative = self._MINTER + "/" + user
            with self._directory(relative) as parent:
                result[relative] = None if parent is None else _signature(os.fstat(parent))
                if parent is None:
                    continue
                fcntl.flock(parent, fcntl.LOCK_SH | fcntl.LOCK_NB)
                with os.scandir(parent) as entries:
                    for entry in entries:
                        count += 1
                        if count > self._MAX_OPERATIONS or re.fullmatch(r"operation-[0-9a-f]{64}", entry.name) is None:
                            raise RetirementUncertain()
                        result[relative + "/" + entry.name] = self._operation(parent, entry.name)
        return result

    def observe(self) -> LegacyAuxiliaryMembers:
        self.directory_identities = {}
        self.fence.require(self.io)
        # Reuse the existing independently receipted issuer directory and its
        # closed data-only journal parser. Minting/uncertain rows are historical
        # evidence here, never an assertion that key cleanup has completed.
        receipt, present = self.io._issuer_inventory(db_retired=True)
        if present and receipt is None:
            raise RetirementUncertain()
        result: dict[str, tuple[int, ...] | None] = {}
        with self._directory(self._ISSUER) as parent:
            if (parent is not None) != present:
                raise RetirementUncertain()
            result[self._ISSUER] = None if parent is None else _signature(os.fstat(parent))
            members = {} if parent is None else self.io._issuer_members(parent, db_retired=True)
            for name in ("invite-issuer.json", "invite-issuer.lock"):
                result[self._ISSUER + "/" + name] = members[name][0] if name in members else None
        saved = self.inventory._receipt(self._RECEIPT, self.fence, optional=True)
        if (saved is None) != (receipt is None) or saved is not None and self.io._json(saved[0]) != receipt:
            raise RetirementUncertain()
        result[STATE_DIRECTORY + "/" + self._RECEIPT] = None if saved is None else saved[1]
        transaction = None
        with self._directory(STATE_DIRECTORY) as parent:
            if parent is None:
                raise RetirementUncertain()
            self.io._headscale_no_pending(parent, self._TRANSACTION)
            try:
                info = os.stat(self._TRANSACTION, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                if not 0 < info.st_size <= 16384:
                    raise RetirementUncertain()
                data = self.io._read_file(parent, self._TRANSACTION, owner=self.io.root_owner, mode=0o600)
                if _signature(os.stat(self._TRANSACTION, dir_fd=parent, follow_symlinks=False)) != _signature(info):
                    raise RetirementUncertain()
                transaction = data, _signature(info)
        if transaction is not None:
            parsed = HomeActivationTransaction.parse(transaction[0])
            if self.ownership.activation is None or not parsed.matches(self.ownership.activation):
                raise RetirementUncertain()
        result[STATE_DIRECTORY + "/" + self._TRANSACTION] = None if transaction is None else transaction[1]
        result.update(self._minter())
        self.fence.require(self.io)
        return LegacyAuxiliaryMembers(result, self.directory_identities)
