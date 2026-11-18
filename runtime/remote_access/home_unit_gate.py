"""Unmounted B5 fragment/link inventory; no service-manager or activation caller."""
from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Callable

from runtime.remote_access import headscale_service as headscale
from runtime.remote_access.home_activation import (
    OWNED_LINK,
    OWNED_LINK_TARGET,
    HomeActivationError,
    HomeActivationReceipt,
    HomeActivationStore,
)
from runtime.remote_access.installer import (
    _HEADSCALE_DBUS_DIRS,
    _HEADSCALE_UNIT_DIRS,
    _UNIT_DIR,
    InstallerError,
)
from runtime.remote_access.systemd_unit import render_headscale_unit


class HomeUnitGateError(ValueError):
    """Closed diagnostics without filesystem content or receipt data."""


class HomeUnitGate:
    """Inventory only; expected identity/generation must come from the owner.

    root/root_owner reuse the protected reader's temp-root venue. Live "/" still
    requires root:root. Successful verification supplies no lifecycle authority.
    """

    def __init__(self, *, release_uid: int, home_id: str, control_plane_id: str,
                 connector_id: str, expected_generation: int,
                 root: Path = Path("/"), root_owner: tuple[int, int] = (0, 0)) -> None:
        if (type(release_uid) is not int or not 0 < release_uid < 2**32 - 1
                or type(expected_generation) is not int or expected_generation < 0
                or any(type(value) is not str or not value for value in
                       (home_id, control_plane_id, connector_id))):
            raise HomeUnitGateError("foreign-unit")
        self._store = HomeActivationStore(root=root, root_owner=root_owner)
        self._io = self._store._io
        self._identity = (release_uid, home_id, control_plane_id, connector_id)
        self._generation = expected_generation

    def _receipt(self) -> HomeActivationReceipt | None:
        try:
            receipt = self._store.read(expected_generation=self._generation)
        except HomeActivationError:
            return None
        # Ruling 9: every closed phase, including failed_uncertain, is valid.
        if (receipt.release_uid, receipt.home_id, receipt.control_plane_id,
                receipt.connector_id) != self._identity:
            return None
        if receipt.ownership.to_dict()["links"] != {OWNED_LINK: OWNED_LINK_TARGET}:
            return None
        return receipt

    def _inventory(self, receipt: HomeActivationReceipt | None, *, allow_link: bool) -> None:
        paths = list(_HEADSCALE_UNIT_DIRS)
        with self._io._directory() as root:
            try:
                info = os.stat("lib", dir_fd=root, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                if stat.S_ISLNK(info.st_mode):
                    if os.readlink("lib", dir_fd=root) not in {"usr/lib", "/usr/lib"}:
                        raise HomeUnitGateError("foreign-unit")
                else:
                    paths.append("lib/systemd/system")

        unit = render_headscale_unit(home_transport=receipt is not None).encode()
        for path in paths:
            opened = False
            try:
                with self._io._directory(path) as parent:
                    opened = True
                    if path == _UNIT_DIR:
                        try:
                            data = self._io._read_unit(parent, headscale.UNIT)
                        except FileNotFoundError:
                            data = None
                        if data is not None and (data != unit or (receipt is not None
                                and hashlib.sha256(data).hexdigest()
                                != receipt.member_digests[OWNED_LINK_TARGET])):
                            raise HomeUnitGateError("foreign-unit")
                    extras = (headscale.UNIT + ".d", "happyranch-.service.d", "service.d",
                              "." + headscale.UNIT + ".pending")
                    if path != _UNIT_DIR:
                        extras += (headscale.UNIT,)
                    for name in extras:
                        self._io._refuse_provision_entry(parent, name)
            except FileNotFoundError:
                if opened:
                    raise HomeUnitGateError("foreign-unit") from None
        self._io._headscale_cleanup_links(
            (*paths, *_HEADSCALE_DBUS_DIRS), category="foreign-unit",
            _home_unit=unit, _home_link=allow_link and receipt is not None)

    def verify(self) -> HomeActivationReceipt | None:
        """Admit only inert bytes/no link or the receipt-bound B5 exceptions."""
        try:
            receipt = self._receipt()
            self._inventory(receipt, allow_link=True)
            if self._receipt() != receipt:
                raise HomeUnitGateError("foreign-unit")
            return receipt
        except (OSError, InstallerError, headscale.HeadscaleServiceError):
            raise HomeUnitGateError("foreign-unit") from None

    def disable_owned_link(self, disable: Callable[[str], None]) -> None:
        """Inject a synchronous, exact-link-only disable, then prove absence.

        The future lifecycle owner establishes quiescence and supplies the
        operation. This primitive runs no systemctl and removes no other state.
        Even failed_uncertain must retain link ownership for this cleanup.
        """
        try:
            receipt = self.verify()
            if receipt is not None:
                disable(OWNED_LINK)
            self._inventory(receipt, allow_link=False)
            if self._receipt() != receipt:
                raise HomeUnitGateError("cleanup-uncertain")
        except (OSError, InstallerError, headscale.HeadscaleServiceError, HomeUnitGateError):
            raise HomeUnitGateError("cleanup-uncertain") from None
