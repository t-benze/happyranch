"""Ownership-only stop/disable primitive for explicit home retirement.

The retirement coordinator must establish persistent admission denial and actual
transport cutoff before calling this owner. This module grants neither of those
proofs, performs no cleanup/account mutation, and is not a lifecycle CLI. A
same-named unit, a successful command, or an old observation is never ownership.
Like the installer, observations assume cooperative root/systemd writers; they
do not fence an adversarial root between a final observation and a native call.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from runtime.remote_access.home_retirement import (
    HomeRetirement, RetirementUncertain, _DBUS_ROOTS, _DIRECTORY_FLAGS,
    _FILE_FLAGS, _UNIT_ROOTS, _UNIT_SUFFIXES, _signature,
)
from runtime.remote_access.service_manager import ServiceManagerError

if TYPE_CHECKING:
    from runtime.remote_access.installer import ManagedInstaller

_PROPERTIES = (
    "Id", "Names", "FragmentPath", "DropInPaths", "NeedDaemonReload", "LoadState",
    "ActiveState", "SubState", "UnitFileState", "MainPID", "ControlPID", "ControlGroup",
    "Job", "User", "Group", "DynamicUser", "Transient",
)


def boot_identity(io: ManagedInstaller) -> str:
    """Fixed no-follow kernel identity shared by retirement native observers."""
    with io._publication_parent("proc/sys/kernel/random") as (parent, revalidate):
        info = os.stat("boot_id", dir_fd=parent, follow_symlinks=False)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or (info.st_uid, info.st_gid) != io.root_owner or stat.S_IMODE(info.st_mode) != 0o444):
            raise RetirementUncertain()
        value = OwnedService._proc_read(parent, "boot_id")
        revalidate()
        if (_signature(os.stat("boot_id", dir_fd=parent, follow_symlinks=False)) != _signature(info)
                or not value.endswith("\n") or re.fullmatch(
                    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", value[:-1]) is None):
            raise RetirementUncertain()
        return value[:-1]


@dataclass(frozen=True)
class ServiceIdentity:
    """Fixed legacy member selected by the coordinator's protected inventory.

    Digests come from validated legacy receipts, not from reading a candidate
    and declaring its own hash trusted. Unit/link names are fixed in source.
    """

    unit: str
    account: str
    uid: int
    gid: int
    fragment_sha256: str
    executable: str
    executable_sha256: str
    link: str | None = None

    def __post_init__(self) -> None:
        fixed = {
            "happyranch-headscale.service": (
                "happyranch-headscale", "opt/happyranch-desktop/libexec/headscale/headscale"),
        }
        if (self.unit not in fixed or (self.account, self.executable) != fixed[self.unit]
                or type(self.uid) is not int or type(self.gid) is not int
                or not 0 < self.uid < 2**32 - 1 or not 0 < self.gid < 2**32 - 1
                or any(type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None
                       for value in (self.fragment_sha256, self.executable_sha256))
                or self.link not in (None, "etc/systemd/system/happyranch-managed.target.wants/" + self.unit)):
            raise RetirementUncertain()


@dataclass(frozen=True)
class ServiceObservation:
    fragment: tuple[int, ...] | None
    executable: tuple[int, ...] | None
    link: tuple[int, ...] | None
    properties: tuple[tuple[str, str], ...]
    process: tuple[int, int] | None


@dataclass(frozen=True)
class OwnedStopReceipt:
    """An in-process readback, never a durable intent or replay permission."""

    unit: str
    observation: ServiceObservation


class OwnedService:
    def __init__(self, io: ManagedInstaller, identity: ServiceIdentity) -> None:
        self.io, self.identity = io, identity
        self.reader = HomeRetirement(io)

    def _regular(self, relative: str, digest: str, *, executable: bool = False) -> tuple[int, ...]:
        with self.reader._entry(relative) as (parent, name, info):
            if (info is None or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or (info.st_uid, info.st_gid) != self.io.root_owner
                    or stat.S_IMODE(info.st_mode) != (0o755 if executable else 0o644)):
                raise RetirementUncertain()
            # The package binary can exceed the bounded unit reader's limit.
            # Stream it with an explicit byte budget; never buffer private data.
            descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent)
            try:
                if _signature(os.fstat(descriptor)) != _signature(info):
                    raise RetirementUncertain()
                result, total = hashlib.sha256(), 0
                while True:
                    chunk = os.read(descriptor, 65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > (256 * 1024 * 1024 if executable else 65536):
                        raise RetirementUncertain()
                    result.update(chunk)
                if (total != info.st_size or result.hexdigest() != digest
                        or _signature(os.fstat(descriptor)) != _signature(info)):
                    raise RetirementUncertain()
            finally:
                os.close(descriptor)
            return _signature(info)

    def _links(self, *, disabled: bool, account_consumers: set[str] | None = None) -> tuple[int, ...] | None:
        identity, count, found = self.identity, 0, None
        fragment = "etc/systemd/system/" + identity.unit

        def scan(parent: int, relative: str, depth: int, dbus: bool) -> None:
            nonlocal count, found
            if depth > 32:
                raise RetirementUncertain()
            before = _signature(os.fstat(parent))
            with os.scandir(parent) as entries:
                for entry in entries:
                    count += 1
                    if count > 65536:
                        raise RetirementUncertain()
                    path = relative + "/" + entry.name
                    info = os.stat(entry.name, dir_fd=parent, follow_symlinks=False)
                    if (entry.name.startswith(identity.unit.removesuffix(".service") + ".")
                            and path not in (fragment, identity.link)):
                        # A same-stem socket/path/timer may activate the service
                        # implicitly without containing its service name.
                        raise RetirementUncertain()
                    if stat.S_ISLNK(info.st_mode):
                        target = os.readlink(entry.name, dir_fd=parent)
                        relevant = entry.name == identity.unit or Path(target).name == identity.unit
                        if relevant:
                            if (disabled or path != identity.link or target != "/" + fragment
                                    or (info.st_uid, info.st_gid) != self.io.root_owner or info.st_nlink != 1):
                                raise RetirementUncertain()
                            found = _signature(info)
                    elif stat.S_ISDIR(info.st_mode):
                        if (entry.name == identity.unit + ".d"
                                or (info.st_uid, info.st_gid) != self.io.root_owner or info.st_mode & 0o022):
                            raise RetirementUncertain()
                        child = os.open(entry.name, _DIRECTORY_FLAGS, dir_fd=parent)
                        try:
                            if _signature(os.fstat(child)) != _signature(info):
                                raise RetirementUncertain()
                            scan(child, path, depth + 1, dbus)
                        finally:
                            os.close(child)
                    elif stat.S_ISREG(info.st_mode):
                        if entry.name == identity.unit and path != fragment:
                            raise RetirementUncertain()
                        if path != fragment and (dbus or entry.name.endswith(_UNIT_SUFFIXES)
                                or relative.endswith(".d") and entry.name.endswith(".conf")):
                            data = self.reader._read(parent, entry.name, info)
                            if identity.unit.encode() in data or identity.account.encode() in data:
                                from runtime.remote_access.installer import _owned_helper_unit
                                if not _owned_helper_unit(path, info, data, self.io.root_owner):
                                    if account_consumers is None or identity.unit.encode() in data:
                                        raise RetirementUncertain()
                                    account_consumers.add(path)
                    else:
                        raise RetirementUncertain()
                    if _signature(os.stat(entry.name, dir_fd=parent, follow_symlinks=False)) != _signature(info):
                        raise RetirementUncertain()
            if _signature(os.fstat(parent)) != before:
                raise RetirementUncertain()

        roots = list(_UNIT_ROOTS)
        with self.reader._entry("lib") as (parent, name, info):
            if info is not None:
                if stat.S_ISLNK(info.st_mode):
                    if os.readlink(name, dir_fd=parent) not in ("usr/lib", "/usr/lib"):
                        raise RetirementUncertain()
                elif stat.S_ISDIR(info.st_mode):
                    roots.append("lib/systemd/system")
                else:
                    raise RetirementUncertain()
        for relative in (*roots, *_DBUS_ROOTS):
            with self.reader._entry(relative + "/" + identity.unit) as (parent, name, _):
                if name == identity.unit:
                    scan(parent, relative, 0, relative in _DBUS_ROOTS)
        return found

    @staticmethod
    def _proc_read(parent: int, name: str) -> str:
        descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise RetirementUncertain()
            chunks, total = [], 0
            while True:
                data = os.read(descriptor, 4096)
                if not data:
                    return b"".join(chunks).decode("ascii")
                total += len(data)
                if total > 65536:
                    raise RetirementUncertain()
                chunks.append(data)
        finally:
            os.close(descriptor)

    def _process(self, pid: int) -> tuple[int, int] | None:
        identity = self.identity
        code, output = self.io._run(["ps", "-eo", "pid=,ruid=,euid=,suid=,fuid="], timeout=10.0)
        if code or len(output.encode()) > 65536:
            raise RetirementUncertain()
        owners = []
        for line in output.splitlines():
            fields = line.split()
            if len(fields) != 5 or any(not field.isascii() or not field.isdigit() for field in fields):
                raise RetirementUncertain()
            values = tuple(map(int, fields))
            if identity.uid in values[1:]:
                if values[1:] != (identity.uid,) * 4:
                    raise RetirementUncertain()
                owners.append(values[0])
        if owners != ([pid] if pid else []):
            raise RetirementUncertain()
        if not pid:
            return None
        with self.io._publication_parent("proc") as (proc, revalidate):
            before = os.stat(str(pid), dir_fd=proc, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode) or before.st_uid != identity.uid:
                raise RetirementUncertain()
            descriptor = os.open(str(pid), _DIRECTORY_FLAGS, dir_fd=proc)
            try:
                if _signature(os.fstat(descriptor)) != _signature(before):
                    raise RetirementUncertain()
                status = self._proc_read(descriptor, "status")
                for key, expected in (("Uid", identity.uid), ("Gid", identity.gid)):
                    lines = [line.split()[1:] for line in status.splitlines() if line.startswith(key + ":")]
                    if lines != [[str(expected)] * 4]:
                        raise RetirementUncertain()
                fields = self._proc_read(descriptor, "stat").rsplit(") ", 1)
                if (len(fields) != 2 or fields[0].split(" (", 1)[0] != str(pid)
                        or len(fields[1].split()) < 20 or not fields[1].split()[19].isdigit()):
                    raise RetirementUncertain()
                started = int(fields[1].split()[19])
                if (started <= 0 or os.readlink("exe", dir_fd=descriptor) != str(self.io.root / identity.executable)
                        or self._proc_read(descriptor, "cgroup") != "0::/system.slice/" + identity.unit + "\n"):
                    raise RetirementUncertain()
                revalidate()
                if _signature(os.stat(str(pid), dir_fd=proc, follow_symlinks=False)) != _signature(before):
                    raise RetirementUncertain()
                return pid, started
            finally:
                os.close(descriptor)

    def observe(self, *, stopped: bool = False, disabled: bool = False) -> ServiceObservation:
        try:
            return self._observe(stopped=stopped, disabled=disabled)
        except (OSError, ValueError, RuntimeError, KeyError, ServiceManagerError):
            raise RetirementUncertain() from None

    def _observe(self, *, stopped: bool, disabled: bool,
                 cleanup: ServiceObservation | None = None, account_cleanup: bool = False) -> ServiceObservation:
        identity = self.identity
        # NSS receipts/lineage are the coordinator's separate prerequisite;
        # re-observe numeric/name bindings here so account replacement is closed.
        def account() -> None:
            if account_cleanup:
                from runtime.remote_access.home_retirement_account import account_status
                account_status(self.io, identity)
            else:
                user, group = self.io._lookup_user(identity.account), self.io._lookup_group(identity.account)
                if ((user.pw_name, user.pw_uid, user.pw_gid) != (identity.account, identity.uid, identity.gid)
                        or (group.gr_name, group.gr_gid) != (identity.account, identity.gid)):
                    raise RetirementUncertain()
        account()
        path = "etc/systemd/system/" + identity.unit
        missing = cleanup is not None and not self.reader._present(path)
        fragment = None if missing else self._regular(path, identity.fragment_sha256)
        def executable_observation() -> tuple[int, ...] | None:
            if cleanup is not None and not self.reader._present(identity.executable):
                return None
            return self._regular(identity.executable, identity.executable_sha256, executable=True)
        executable = executable_observation()
        consumers: set[str] | None = set() if account_cleanup else None
        link = self._links(disabled=disabled, account_consumers=consumers)
        props = self.io.manager.show_properties(identity.unit, _PROPERTIES)
        fixed = dict(Id=identity.unit, Names=identity.unit,
            FragmentPath=str(self.io.root / "etc/systemd/system" / identity.unit), DropInPaths="",
            NeedDaemonReload="no", LoadState="loaded", ControlPID="0", Job="",
            User=identity.account, Group=identity.account, DynamicUser="no", Transient="no")
        if missing:
            fixed.pop("NeedDaemonReload")
            if props.get("LoadState") == "not-found":
                fixed.update(FragmentPath="", LoadState="not-found", User="", Group="")
            if props.get("NeedDaemonReload") not in ({"no"} if props.get("LoadState") == "not-found" else {"no", "yes"}):
                raise RetirementUncertain()
        if (set(props) != set(_PROPERTIES) or any(props.get(key) != value for key, value in fixed.items())
                or not props["MainPID"].isascii() or not props["MainPID"].isdigit()):
            raise RetirementUncertain()
        pid = int(props["MainPID"])
        if props["ActiveState"] in ("inactive", "failed"):
            if props["SubState"] not in ("dead", "failed") or pid or props["ControlGroup"]:
                raise RetirementUncertain()
        elif (stopped or props["ActiveState"] != "active" or props["SubState"] != "running" or pid <= 0
                or props["ControlGroup"] != "/system.slice/" + identity.unit):
            raise RetirementUncertain()
        if props["UnitFileState"] not in (("", "disabled", "static") if missing else ("enabled", "disabled", "static")):
            raise RetirementUncertain()
        if ((props["UnitFileState"] == "enabled") != (link is not None)
                or disabled and props["UnitFileState"] == "enabled"):
            raise RetirementUncertain()
        process = self._process(pid)
        # Native calls above may block. Pinning the original bytes/links is
        # required again after them, before this observation can admit effects.
        if (self.io.manager.show_properties(identity.unit, _PROPERTIES) != props
                or self._process(pid) != process
                or self._links(disabled=disabled, account_consumers=consumers) != link
                or executable_observation() != executable
                or (None if missing and not self.reader._present(path)
                    else self._regular(path, identity.fragment_sha256)) != fragment):
            raise RetirementUncertain()
        if cleanup is not None and (cleanup.fragment is None or fragment is not None and fragment != cleanup.fragment
                or cleanup.executable is None or executable is not None and executable != cleanup.executable
                or process is not None or link is not None):
            raise RetirementUncertain()
        account()
        return ServiceObservation(fragment, executable, link, tuple(sorted(props.items())), process)

    def observe_cleanup(self, original: ServiceObservation, *, account_cleanup: bool = False) -> ServiceObservation:
        """Read stopped/disabled ownership after explicit native artifact removal.

        Only the fixed saved fragment/executable may be absent. Remaining
        artifacts and account stay original; aliases, jobs and processes refuse.
        Explicit account-stage observation additionally admits proven original
        name absence and static consumers, with full numeric-reuse checks.
        The remover separately pins saved original ancestors before effects.
        This performs no stop, disable, reload or removal.
        """
        try:
            if type(original) is not ServiceObservation:
                raise RetirementUncertain()
            return self._observe(stopped=True, disabled=True, cleanup=original, account_cleanup=account_cleanup)
        except (OSError, ValueError, RuntimeError, KeyError, ServiceManagerError):
            raise RetirementUncertain() from None

    @staticmethod
    def _same_members(first: ServiceObservation, current: ServiceObservation) -> None:
        if ((first.fragment, first.executable) != (current.fragment, current.executable)
                or current.link is not None and current.link != first.link):
            raise RetirementUncertain()

    def stop_and_disable(self, *, expected: ServiceObservation | None = None,
                         before_effect: Callable[[], None] | None = None) -> OwnedStopReceipt:
        """Explicit caller only; every uncertain native result stops this chain.

        Re-observation after an interruption can skip a positively stopped or
        disabled effect. It never treats a recorded intention as that proof.
        No successful exit code substitutes for native/process/link readback.
        A coordinator can bind the preserved original observation and supply its
        real cutoff guard; native identity is checked again after that blocking
        guard and before each effect. The guard grants no standalone authority.
        """
        try:
            return self._stop_and_disable(expected=expected, before_effect=before_effect)
        except (OSError, ValueError, RuntimeError, KeyError, ServiceManagerError):
            raise RetirementUncertain() from None

    def _stop_and_disable(self, *, expected: ServiceObservation | None,
                          before_effect: Callable[[], None] | None) -> OwnedStopReceipt:
        before = self.observe()
        if expected is not None and (type(expected) is not ServiceObservation or before != expected):
            raise RetirementUncertain()
        if before_effect is not None:
            before_effect()
        if self.observe() != before:
            raise RetirementUncertain()
        if before.process is not None:
            self.io.manager.stop(self.identity.unit, timeout=50.0)
        current = self.observe(stopped=True)
        self._same_members(before, current)
        if before_effect is not None:
            before_effect()
        if self.observe(stopped=True) != current:
            raise RetirementUncertain()
        if dict(current.properties)["UnitFileState"] == "enabled":
            self.io.manager.disable(self.identity.unit)
        final = self.observe(stopped=True, disabled=True)
        self._same_members(before, final)
        return OwnedStopReceipt(self.identity.unit, final)

    def confirm(self, receipt: OwnedStopReceipt) -> None:
        """Fresh proof required by the coordinator before each later cleanup."""
        if (receipt.unit != self.identity.unit
                or self.observe(stopped=True, disabled=True) != receipt.observation):
            raise RetirementUncertain()
