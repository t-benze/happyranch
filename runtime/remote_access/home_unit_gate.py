"""B5 receipt-aware unit inventory; no lifecycle or service effect.

E2a admission/inventory composes this policy. Inert preparation retains its own
scanner. This receipt-aware policy uses
the same load roots, no-follow readers and shared 65536-entry/32-depth bounds.
Like that scanner, it assumes cooperative systemd generators/root writers;
successful sampled inventory is not a racing-writer fence or activation grant.
"""
from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Callable

from runtime.remote_access import legacy_home_readback as activation
from runtime.remote_access import installer
from runtime.remote_access.systemd_unit import render_headscale_unit

_UNIT = "happyranch-headscale.service"
_UNIT_DIR = "etc/systemd/system"
_UNIT_SUFFIXES = (".service", ".socket", ".target", ".device", ".mount", ".automount",
                  ".swap", ".timer", ".path", ".slice", ".scope")


class HomeUnitGateError(ValueError):
    def __init__(self) -> None:
        super().__init__("home-unit-inventory")


class HomeUnitGate:
    """Require caller-supplied current identity/generation, never infer them.

    root/root_owner reuse the ManagedInstaller temp-root venue; live / requires
    root:root. A parsed receipt in ANY phase may authorize only the fixed B5
    fragment/link. A missing/unsafe/stale/invalid receipt grants no exception.
    """

    def __init__(self, *, expected_release_uid: int, expected_home_id: str,
                 expected_control_plane_id: str, expected_connector_id: str,
                 expected_generation: int, root: Path = Path("/"),
                 root_owner: tuple[int, int] = (0, 0)) -> None:
        if (type(expected_release_uid) is not int or not 0 < expected_release_uid < 2**32 - 1
                or type(expected_generation) is not int or expected_generation < 0
                or any(type(value) is not str for value in
                       (expected_home_id, expected_control_plane_id, expected_connector_id))):
            raise HomeUnitGateError()
        try:
            self._store = activation.LegacyHomeReadback(root=root, root_owner=root_owner)
        except activation.HomeActivationError:
            raise HomeUnitGateError() from None
        self._io = self._store._io
        self._generation = expected_generation
        self._binding = (expected_release_uid, expected_home_id, expected_control_plane_id, expected_connector_id)

    def _receipt(self) -> activation.HomeActivationReceipt | None:
        try:
            receipt = self._store.read(expected_generation=self._generation)
        except activation.HomeActivationError:
            return None
        if (receipt.release_uid, receipt.home_id, receipt.control_plane_id, receipt.connector_id) != self._binding:
            return None
        return receipt

    def inspect(self) -> None:
        """Admit only this sampled unit inventory; perform no effect."""
        self._inspect()

    def disable_owned_link(self, disable: Callable[[], None]) -> None:
        """Invoke the injected exact-link disable, then freshly prove absence.

        The future lifecycle owner must supply a synchronous operation removing
        ONLY OWNED_LINK, under its existing authorization/quiescence/writer
        serialization. This primitive neither stops services nor calls systemctl.
        The receipt is retained, including failed_uncertain, throughout cleanup.
        """
        receipt, present = self._inspect()
        if present:
            if receipt is None:
                raise HomeUnitGateError()
            try:
                disable()
            except Exception:
                raise HomeUnitGateError() from None
        current, _ = self._inspect(require_absent=True)
        if receipt is not None and current != receipt:
            raise HomeUnitGateError()

    def _inspect(self, *, require_absent: bool = False) -> tuple[activation.HomeActivationReceipt | None, bool]:
        receipt = self._receipt()
        count = 0
        present = False

        def fragment(parent: int, name: str) -> None:
            before = activation._signature(os.stat(name, dir_fd=parent, follow_symlinks=False))
            data = self._io._read_unit(parent, name)
            if activation._signature(os.stat(name, dir_fd=parent, follow_symlinks=False)) != before:
                raise HomeUnitGateError()
            if data == render_headscale_unit().encode():
                return
            if (receipt is None or data != render_headscale_unit(home_transport=True).encode()
                    or hashlib.sha256(data).hexdigest() != receipt.member_digests[activation.OWNED_LINK_TARGET]):
                raise HomeUnitGateError()

        def scan(parent: int, depth: int, relative: str, dbus: bool) -> None:
            nonlocal count, present
            if depth > 32:
                raise HomeUnitGateError()
            before = activation._signature(os.fstat(parent))
            with os.scandir(parent) as entries:
                for entry in entries:
                    count += 1
                    if count > 65536:
                        raise HomeUnitGateError()
                    info = os.stat(entry.name, dir_fd=parent, follow_symlinks=False)
                    if (f"{relative}/{entry.name}" == installer.PACKAGED_HELPER_UNIT
                            and not stat.S_ISREG(info.st_mode)):
                        raise HomeUnitGateError()
                    if ("/" + relative + "/" + entry.name == activation.OWNED_LINK
                            and not stat.S_ISLNK(info.st_mode)):
                        raise HomeUnitGateError()
                    managed = relative == _UNIT_DIR and entry.name == _UNIT
                    if not dbus and entry.name.startswith("happyranch-headscale."):
                        if not managed:
                            # The single receipted link is handled below, not a
                            # second fragment or a same-stem activation trigger.
                            if "/" + relative + "/" + entry.name != activation.OWNED_LINK:
                                raise HomeUnitGateError()
                        else:
                            fragment(parent, entry.name)
                    if stat.S_ISLNK(info.st_mode):
                        target = os.readlink(entry.name, dir_fd=parent)
                        if entry.name == _UNIT or Path(target).name == _UNIT:
                            if (require_absent or receipt is None
                                    or "/" + relative + "/" + entry.name != activation.OWNED_LINK
                                    or receipt.ownership.to_dict()["links"] != {
                                        activation.OWNED_LINK: activation.OWNED_LINK_TARGET}
                                    or target != activation.OWNED_LINK_TARGET or info.st_nlink != 1
                                    or (info.st_uid, info.st_gid) != self._io.root_owner):
                                raise HomeUnitGateError()
                            present = True
                        if activation._signature(os.stat(
                                entry.name, dir_fd=parent, follow_symlinks=False)) != activation._signature(info):
                            raise HomeUnitGateError()
                    elif stat.S_ISDIR(info.st_mode):
                        child = os.open(entry.name, installer._DIR_FLAGS, dir_fd=parent)
                        try:
                            self._io._check_directory(child, "systemd config inventory", None)
                            if activation._signature(os.fstat(child)) != activation._signature(info):
                                raise HomeUnitGateError()
                            scan(child, depth + 1, f"{relative}/{entry.name}", dbus)
                            if activation._signature(os.stat(
                                    entry.name, dir_fd=parent, follow_symlinks=False)) != activation._signature(info):
                                raise HomeUnitGateError()
                        finally:
                            os.close(child)
                    elif stat.S_ISREG(info.st_mode) and not managed and (
                            dbus or entry.name.endswith(_UNIT_SUFFIXES)
                            or (relative.endswith(".d") and entry.name.endswith(".conf"))):
                        self._foreign_file(parent, entry.name, info, relative)
            if activation._signature(os.fstat(parent)) != before:
                raise HomeUnitGateError()

        try:
            # This fixed dependency directory cannot hide the owned link behind
            # an alias to an unscanned tree. Absence is fine; an unsafe parent is
            # never evidence that the owned link is absent.
            try:
                with self._io._publication_parent(str(Path(activation.OWNED_LINK).parent).lstrip("/")) as (_, revalidate):
                    revalidate()
            except FileNotFoundError:
                pass
            paths = list(installer._HEADSCALE_UNIT_DIRS)
            with self._io._publication_parent("") as (root, revalidate):
                try:
                    info = os.stat("lib", dir_fd=root, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    if stat.S_ISLNK(info.st_mode):
                        if os.readlink("lib", dir_fd=root) not in {"usr/lib", "/usr/lib"}:
                            raise HomeUnitGateError()
                    else:
                        paths.append("lib/systemd/system")
                revalidate()
            for path in (*paths, *installer._HEADSCALE_DBUS_DIRS):
                opened = False
                try:
                    with self._io._publication_parent(path) as (parent, revalidate):
                        opened = True
                        if path in paths:
                            for extra in (_UNIT + ".d", "happyranch-.service.d", "service.d", "." + _UNIT + ".pending"):
                                try:
                                    os.stat(extra, dir_fd=parent, follow_symlinks=False)
                                except FileNotFoundError:
                                    continue
                                raise HomeUnitGateError()
                        scan(parent, 0, path, path in installer._HEADSCALE_DBUS_DIRS)
                        revalidate()
                except FileNotFoundError:
                    if opened:
                        raise HomeUnitGateError() from None
            if receipt is not None and self._receipt() != receipt:
                raise HomeUnitGateError()
        except (OSError, installer.InstallerError):
            raise HomeUnitGateError() from None
        return receipt, present

    def _foreign_file(self, parent: int, name: str, named: os.stat_result, relative: str) -> None:
        # Foreign policy is observed, never adopted; keep the inert scanner's
        # file-size/signature checks without imposing managed-file modes.
        descriptor = os.open(name, installer._FILE_FLAGS, dir_fd=parent)
        try:
            opened = os.fstat(descriptor)
            signature = activation._signature
            if (not stat.S_ISREG(opened.st_mode) or opened.st_size > 65536
                    or signature(named) != signature(opened)):
                raise HomeUnitGateError()
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                data = stream.read(65537)
            if (len(data) > 65536 or len(data) != opened.st_size
                    or signature(opened) != signature(os.fstat(descriptor))
                    or signature(opened) != signature(os.stat(name, dir_fd=parent, follow_symlinks=False))
                    or ((b"happyranch-headscale" in data
                         or f"{relative}/{name}" == installer.PACKAGED_HELPER_UNIT) and not installer._owned_helper_unit(
                        f"{relative}/{name}", opened, data, self._io.root_owner))):
                raise HomeUnitGateError()
        finally:
            os.close(descriptor)
