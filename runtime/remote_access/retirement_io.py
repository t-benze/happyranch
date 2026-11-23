"""Fixed retirement IO without local controller, activation or issuer effects."""
from __future__ import annotations

import grp
import pwd
from pathlib import Path
from typing import Callable

from runtime.remote_access.installer_io import InstallerError, InstallerIO
from runtime.remote_access.installer_validation import InstallerValidation
from runtime.remote_access.legacy_issuer_readback import LegacyIssuerReadback
from runtime.remote_access.service_manager import SystemdServiceManager


class RetirementIO(InstallerIO, InstallerValidation, LegacyIssuerReadback):
    """Existing native/temporary-root adapter; the public entry fixes root/layout."""

    def __init__(self, *, root: Path = Path("/"), root_owner: tuple[int, int] = (0, 0),
                 layout: str = "deb", manager: SystemdServiceManager | None = None,
                 run: Callable[..., tuple[int, str]] | None = None,
                 lookup_user: Callable[[str], pwd.struct_passwd] = pwd.getpwnam,
                 lookup_group: Callable[[str], grp.struct_group] = grp.getgrnam) -> None:
        if not root.is_absolute() or ".." in root.parts:
            raise InstallerError(f"installation root must be an absolute literal path: {root}")
        if root == Path("/") and root_owner != (0, 0):
            raise InstallerError("live installation requires root ownership")
        if layout != "deb":
            raise InstallerError("retirement requires the Debian layout")
        self.root, self.root_owner, self.layout = root, root_owner, layout
        self.manager = manager or SystemdServiceManager(system=True, run=run)
        self._run = run or self.manager._run
        self._lookup_user, self._lookup_group = lookup_user, lookup_group
        self._service_owner = None

    def _layout(self) -> dict[str, tuple[int, tuple[int, int] | None]]:
        layout = {"etc/happyranch": (0o755, self.root_owner),
                  "etc/credstore": (0o700, self.root_owner)}
        for base in ("var/lib", "var/log", "run"):
            for name in ("happyranch-connector", "happyranch-tsnet-sidecar"):
                mode = 0o700 if base == "var/lib" and name == "happyranch-tsnet-sidecar" else 0o755
                layout[f"{base}/{name}"] = (mode, self._service_owner)
        return layout

    def _validate_install_record(self, name: str, data: bytes) -> None:
        if name != "provision.json":
            raise InstallerError("unsupported retirement provenance")
        self._validate_provision_record(data)
