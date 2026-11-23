"""Read-only retirement observations for the former Host-local controller.

Detection never adopts a legacy artifact, resumes an intent or performs cleanup.
Only the explicit retirement owner may produce the final receipt; a filename,
intent or disappearing path cannot establish completion. Native observations
are sampled, under the same cooperative-root-writer limitation as the installer.
"""
from __future__ import annotations

import json
import os
import stat
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

from runtime.remote_access.service_manager import ServiceManagerError

if TYPE_CHECKING:
    from runtime.remote_access.installer import ManagedInstaller

MESSAGE = "Home mode has been retired. Sign in to use your account's private network. Your local work is kept."
STATE_DIRECTORY = "var/lib/happyranch-remote-access"
RECEIPT = "home-retirement.json"
_FINAL_BYTES = b'{"account":"absent","format":"home-retirement@1","state":"retired"}\n'
_RETAINED = "retained-disabled: static consumer"
_RETAINED_BYTES = b'{"account":"retained-disabled: static consumer","format":"home-retirement@1","state":"retired"}\n'
_FINAL_PENDING = ".home-retirement.json.pending"
_RECOVERY_RECORDS = frozenset(("home-retirement-inventory.json", "home-retirement-transport.json",
    "home-retirement-peer.json", "home-retirement-denied.json"))
UNIT = "happyranch-headscale.service"
ACCOUNT = "happyranch-headscale"
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_UNIT_ROOTS = (
    "etc/systemd/system.control", "run/systemd/system.control",
    "run/systemd/transient", "run/systemd/generator.early", "etc/systemd/system",
    "etc/systemd/system.attached", "run/systemd/system", "run/systemd/system.attached",
    "run/systemd/generator", "usr/local/lib/systemd/system", "usr/lib/systemd/system",
    "run/systemd/generator.late",
)
_DBUS_ROOTS = ("usr/share/dbus-1/system-services", "usr/local/share/dbus-1/system-services",
               "etc/dbus-1/system-services")
_UNIT_SUFFIXES = (".service", ".socket", ".target", ".device", ".mount", ".automount",
                  ".swap", ".timer", ".path", ".slice", ".scope")
_LEGACY_PATHS = (
    "etc/happyranch/headscale", "var/lib/happyranch-headscale", "run/happyranch-headscale",
    "etc/happyranch/control-ca.pem", "etc/happyranch/empty-ca-directory",
    "etc/happyranch/invite-issuer.json", "etc/happyranch/.invite-issuer.json.pending",
    "etc/happyranch/.control-ca.pem.pending", "etc/happyranch/.empty-ca-directory.pending",
)
_ABSENT_UNIT = dict(FragmentPath="", DropInPaths="", LoadState="not-found", ActiveState="inactive",
                    SubState="dead", UnitFileState="", MainPID="0", ControlPID="0", ControlGroup="",
                    Job="", NeedDaemonReload="no")


class RetirementUncertain(ValueError):
    """A closed diagnostic: native/file contents never become exception text."""

    def __init__(self) -> None:
        super().__init__("migration pending")


@dataclass(frozen=True)
class RetirementStatus:
    state: str
    detail: str
    message: str = MESSAGE

    def public(self) -> dict[str, str]:
        return asdict(self)


def _signature(info: os.stat_result) -> tuple[int, ...]:
    return tuple(getattr(info, name) for name in (
        "st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink", "st_size",
        "st_mtime_ns", "st_ctime_ns"))


class HomeRetirement:
    """Use the installer's existing temp-root/command isolation, never a CLI root.

    Metadata-only legacy probes do not open a private key, database, socket,
    FIFO, symlink or untrusted receipt. A separate pinned walk also checks the
    nearest existing ancestor when a later component is absent.
    """

    def __init__(self, io: ManagedInstaller) -> None:
        self.io = io

    @contextmanager
    def _entry(self, relative: str) -> Iterator[tuple[int, str, os.stat_result | None]]:
        path = self.io.root / relative
        descriptor = os.open("/", _DIRECTORY_FLAGS)
        opened = [descriptor]
        parents: list[tuple[int, str, int, os.stat_result]] = []
        missing: tuple[int, str] | None = None

        def revalidate() -> None:
            for ancestor, name, child, before in parents:
                expected = (before.st_dev, before.st_ino, before.st_uid, before.st_gid, before.st_mode)
                for info in (os.stat(name, dir_fd=ancestor, follow_symlinks=False), os.fstat(child)):
                    if (not stat.S_ISDIR(info.st_mode)
                            or (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode) != expected):
                        raise RetirementUncertain()
            if missing is not None:
                try:
                    os.stat(missing[1], dir_fd=missing[0], follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise RetirementUncertain()

        try:
            if self.io.root == Path("/"):
                root_info = os.fstat(descriptor)
                if ((root_info.st_uid, root_info.st_gid) != self.io.root_owner or root_info.st_mode & 0o022):
                    raise RetirementUncertain()
            current = Path("/")
            for part in path.parts[1:-1]:
                try:
                    named = os.stat(part, dir_fd=descriptor, follow_symlinks=False)
                except FileNotFoundError:
                    missing = descriptor, part
                    yield descriptor, part, None
                    revalidate()
                    return
                if not stat.S_ISDIR(named.st_mode):
                    raise RetirementUncertain()
                child = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
                opened.append(child)
                info = os.fstat(child)
                current /= part
                inside = current.is_relative_to(self.io.root)
                # Outside the managed root, sibling writes can change directory
                # size/timestamps between stat and open without changing lineage.
                fields = 9 if inside else 5
                if _signature(named)[:fields] != _signature(info)[:fields]:
                    raise RetirementUncertain()
                if inside:
                    if ((info.st_uid, info.st_gid) != self.io.root_owner or info.st_mode & 0o022
                            or current == self.io.root / STATE_DIRECTORY and stat.S_IMODE(info.st_mode) != 0o700):
                        raise RetirementUncertain()
                parents.append((descriptor, part, child, info))
                descriptor = child
            try:
                before = os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                before = None
                missing = descriptor, path.name
            yield descriptor, path.name, before
            revalidate()
            if before is not None and _signature(before) != _signature(os.stat(
                    path.name, dir_fd=descriptor, follow_symlinks=False)):
                raise RetirementUncertain()
        finally:
            for child in reversed(opened):
                os.close(child)

    def _present(self, relative: str) -> bool:
        with self._entry(relative) as (_, _, info):
            return info is not None

    def _read(self, parent: int, name: str, info: os.stat_result, *, private: bool = False) -> bytes:
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != self.io.root_owner[0] or info.st_mode & 0o022
                or info.st_size > (1024 if private else 65536)
                or private and ((info.st_uid, info.st_gid) != self.io.root_owner
                                or stat.S_IMODE(info.st_mode) != 0o600)):
            raise RetirementUncertain()
        descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent)
        try:
            if _signature(os.fstat(descriptor)) != _signature(info):
                raise RetirementUncertain()
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                data = stream.read((1024 if private else 65536) + 1)
            if (len(data) != info.st_size or _signature(os.fstat(descriptor)) != _signature(info)
                    or _signature(os.stat(name, dir_fd=parent, follow_symlinks=False)) != _signature(info)):
                raise RetirementUncertain()
            return data
        finally:
            os.close(descriptor)

    def _receipt(self) -> bytes | None:
        with self._entry(STATE_DIRECTORY + "/" + RECEIPT) as (parent, name, info):
            if info is None:
                return None
            data = self._read(parent, name, info, private=True)
        # Only a minimized final record, not a legacy/partial phase or a free
        # form path manifest. The writer must prove completion before publishing.
        def unique(items: list[tuple[str, object]]) -> dict:
            value: dict = {}
            for key, item in items:
                if key in value:
                    raise RetirementUncertain()
                value[key] = item
            return value

        record = json.loads(data, object_pairs_hook=unique)
        if record not in ({"format": "home-retirement@1", "state": "retired", "account": account}
                          for account in ("absent", _RETAINED)):
            raise RetirementUncertain()
        return data

    def _state_evidence(self, *, completed: bool, publishing: bool = False) -> bool:
        with self._entry(STATE_DIRECTORY + "/" + RECEIPT) as (parent, name, _):
            # A missing intermediate path is represented by its nearest existing
            # ancestor; do not inventory that unrelated directory as state.
            if name != RECEIPT:
                return False
            found = False
            recovery, fenced = False, False
            with os.scandir(parent) as entries:
                for index, item in enumerate(entries):
                    if index >= 128:
                        raise RetirementUncertain()
                    if item.name == RECEIPT:
                        continue
                    if completed and item.name in ("home-audit.jsonl", "home-audit.jsonl.1"):
                        continue
                    if completed and item.name in _RECOVERY_RECORDS:
                        recovery = True
                        # Inert private recovery provenance remains available
                        # across unpack. It supplies no completion authority.
                        info = os.stat(item.name, dir_fd=parent, follow_symlinks=False)
                        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                                or (info.st_uid, info.st_gid) != self.io.root_owner
                                or stat.S_IMODE(info.st_mode) != 0o600):
                            raise RetirementUncertain()
                        continue
                    if completed and item.name == ".home-activation.json.pending":
                        fenced = True
                        from runtime.remote_access.home_retirement_fence import _BYTES
                        info = os.stat(item.name, dir_fd=parent, follow_symlinks=False)
                        if self._read(parent, item.name, info, private=True) != _BYTES:
                            raise RetirementUncertain()
                        continue
                    if publishing and item.name == _FINAL_PENDING:
                        info = os.stat(item.name, dir_fd=parent, follow_symlinks=False)
                        data = self._read(parent, item.name, info, private=True)
                        if not any(value.startswith(data) for value in (_FINAL_BYTES, _RETAINED_BYTES)):
                            raise RetirementUncertain()
                        continue
                    if item.name.startswith(("home-", ".home-", "headscale-", ".headscale-")):
                        found = True
            return found or recovery and not fenced

    def _config_evidence(self) -> bool:
        with self._entry("etc/happyranch/sidecar.json") as (parent, name, info):
            if info is None:
                return False
            # This is a retained shared config, not the private legacy files.
            value = json.loads(self._read(parent, name, info))
            if type(value) is not dict:
                raise RetirementUncertain()
            return "home_binding" in value

    def _unit_evidence(self, account_consumers: frozenset[str] = frozenset()) -> bool:
        count = 0
        found = False

        def scan(parent: int, relative: str, depth: int, dbus: bool) -> None:
            nonlocal count, found
            if depth > 32:
                raise RetirementUncertain()
            before = _signature(os.fstat(parent))
            with os.scandir(parent) as entries:
                for item in entries:
                    count += 1
                    if count > 65536:
                        raise RetirementUncertain()
                    info = os.stat(item.name, dir_fd=parent, follow_symlinks=False)
                    if item.name.startswith("happyranch-headscale."):
                        found = True
                    if stat.S_ISLNK(info.st_mode):
                        if Path(os.readlink(item.name, dir_fd=parent)).name == UNIT:
                            found = True
                    elif stat.S_ISDIR(info.st_mode):
                        child = os.open(item.name, _DIRECTORY_FLAGS, dir_fd=parent)
                        try:
                            actual = os.fstat(child)
                            if (_signature(actual) != _signature(info)
                                    or (actual.st_uid, actual.st_gid) != self.io.root_owner
                                    or actual.st_mode & 0o022):
                                raise RetirementUncertain()
                            scan(child, relative + "/" + item.name, depth + 1, dbus)
                        finally:
                            os.close(child)
                    elif stat.S_ISREG(info.st_mode) and (dbus or item.name.endswith(_UNIT_SUFFIXES)
                            or relative.endswith(".d") and item.name.endswith(".conf")):
                        data = self._read(parent, item.name, info)
                        if b"happyranch-headscale" in data:
                            from runtime.remote_access.installer import _owned_helper_unit
                            path = relative + "/" + item.name
                            retained = path in account_consumers and UNIT.encode() not in data
                            if not retained and not _owned_helper_unit(path, info, data, self.io.root_owner):
                                found = True
                    elif not stat.S_ISREG(info.st_mode):
                        raise RetirementUncertain()
                    if _signature(os.stat(item.name, dir_fd=parent, follow_symlinks=False)) != _signature(info):
                        raise RetirementUncertain()
            if _signature(os.fstat(parent)) != before:
                raise RetirementUncertain()

        roots = list(_UNIT_ROOTS)
        with self._entry("lib") as (parent, name, info):
            if info is not None:
                if stat.S_ISLNK(info.st_mode):
                    if os.readlink(name, dir_fd=parent) not in ("usr/lib", "/usr/lib"):
                        raise RetirementUncertain()
                elif stat.S_ISDIR(info.st_mode):
                    roots.append("lib/systemd/system")
                else:
                    raise RetirementUncertain()
        for relative in (*roots, *_DBUS_ROOTS):
            # Use a child path so the load-root itself is pinned and validated.
            with self._entry(relative + "/" + UNIT) as (parent, name, _):
                if name == UNIT:
                    scan(parent, relative, 0, relative in _DBUS_ROOTS)
        return found

    def _retained_consumers(self) -> frozenset[str]:
        from runtime.remote_access.home_retirement_account import require_retained
        from runtime.remote_access.home_retirement_journal import observe_original
        return require_retained(self.io, observe_original(self.io).ownership.service)

    def detect(self) -> RetirementStatus:
        """No mutation, even with an interrupted intent or a valid final record."""
        try:
            receipt = self._receipt()
            retained = receipt is not None and json.loads(receipt)["account"] == _RETAINED
            consumers = self._retained_consumers() if retained else frozenset()
            legacy = any(self._present(relative) for relative in _LEGACY_PATHS)
            # Do not short-circuit the shared config/state/unit inspections:
            # private unsafe evidence must never be treated as owned by name.
            legacy |= self._state_evidence(completed=receipt is not None)
            legacy |= self._config_evidence()
            legacy |= self._unit_evidence(consumers)
            if legacy:
                return RetirementStatus("migration pending", "legacy evidence retained")
            properties = self.io.manager.show_properties(UNIT, tuple(_ABSENT_UNIT))
            if properties != _ABSENT_UNIT:
                return RetirementStatus("migration pending", "service retirement unconfirmed")
            if retained:
                if self._retained_consumers() != consumers:
                    raise RetirementUncertain()
            else:
                for lookup in (self.io._lookup_user, self.io._lookup_group):
                    try:
                        lookup(ACCOUNT)
                    except KeyError:
                        pass
                    else:
                        return RetirementStatus("migration pending", "account retirement unconfirmed")
            if self._receipt() != receipt:
                raise RetirementUncertain()
            if retained:
                return RetirementStatus("retired", "retirement confirmed; account retained disabled: static consumer")
            return (RetirementStatus("fresh", "no legacy evidence") if receipt is None else
                    RetirementStatus("retired", "retirement confirmed"))
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError, ServiceManagerError):
            return RetirementStatus("migration pending", "unsafe or changing evidence")
