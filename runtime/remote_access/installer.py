"""Fresh-host managed systemd installation with transient file-only enrollment input.

Provision binaries separately; the provision command stages configuration.
Journal-bearing failed installs can stop and quarantine their exact old configs;
fixed state/enrollment, quarantined config and owned unit removal are journaled.
Fresh replacement staging and closure are journaled; upgrade rollback remains unavailable. Enrollment sources are removed on
success and failure; issue a fresh single-use key before retrying. Files already
present must match the rendered units; interrupted installs need operator care.
"""
from __future__ import annotations

import argparse
import base64
import fcntl
import grp
import hashlib
import json
import math
import os
import pwd
import re
import stat
import sys
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from runtime.remote_access.helper_unit import PACKAGED_HELPER_UNIT, PACKAGED_HELPER_UNIT_BYTES, _owned_helper_unit
from runtime.remote_access.installer_io import InstallerError, InstallerIO
from runtime.remote_access.installer_validation import InstallerValidation
from runtime.remote_access.legacy_issuer_readback import LegacyIssuerReadback
from runtime.remote_access import headscale_service as headscale
from runtime.remote_access import home_headscale
from runtime.remote_access.service_manager import ServiceManagerError, SystemdServiceManager
from runtime.remote_access.systemd_unit import (
    VERIFIER_FILESYSTEM_HARDENING,
    ConnectorUnitSpec,
    SidecarUnitSpec,
    render_connector_unit,
    render_headscale_unit,
    render_managed_target,
    render_sidecar_unit,
    render_verifier_socket,
    render_verifier_unit,
)

UNITS = (
    "happyranch-connector.service",
    "happyranch-tsnet-sidecar.service",
    "happyranch-managed.target",
    "happyranch-release-verifier.socket",
    "happyranch-release-verifier.service",
)
_UNIT_DIR = "etc/systemd/system"
# systemd.unit system load paths, including attached/generated overrides.
# /lib is checked separately: merged-/usr's literal alias is redundant.
_HEADSCALE_UNIT_DIRS = (
    "etc/systemd/system.control", "run/systemd/system.control",
    "run/systemd/transient", "run/systemd/generator.early", _UNIT_DIR,
    "etc/systemd/system.attached", "run/systemd/system", "run/systemd/system.attached",
    "run/systemd/generator", "usr/local/lib/systemd/system", "usr/lib/systemd/system",
    "run/systemd/generator.late",
)
_HEADSCALE_DBUS_DIRS = (
    "usr/share/dbus-1/system-services", "usr/local/share/dbus-1/system-services",
    "etc/dbus-1/system-services",
)
_ENROLLMENT = "etc/happyranch/enrollment.key"
_CREDSTORE = "etc/credstore"
_CREDENTIAL_NAME = "happyranch-tsnet-enrollment"
_STATE_DIRS = ("var/lib/happyranch-connector", "var/lib/happyranch-tsnet-sidecar")
_DEB_PREFIX = "opt/happyranch-desktop"
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_RUNTIME_DIAGNOSTIC_LIMIT = 8
_RUNTIME_DIAGNOSTIC_BYTES = 65536


def _runtime_diagnostic(name: str, info: os.stat_result, owner: tuple[int, int] | None) -> bool:
    """H1's content-free metadata predicate, shared by inventory and B6."""
    modes = ((0o644,) if name == "health.json" else
             (0o600, 0o644) if re.fullmatch(r"\.health-[0-9]+", name) else ())
    return (stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            and (info.st_uid, info.st_gid) == owner
            and stat.S_IMODE(info.st_mode) in modes
            and info.st_size <= _RUNTIME_DIAGNOSTIC_BYTES)


HOME_ISSUER = headscale.INSTALLER_STATE + "/home-issuer"
_ISSUER_RECEIPT = "home-issuer-publication.json"
_HOME_MINTER = headscale.INSTALLER_STATE + "/home-minter"
_HOME_MINTER_USERS = ("happyranch-node", "happyranch-invite")
_HOME_MINTER_LIMIT = 600_000
_HOME_STATE_MEMBERS = (
    "/var/lib/happyranch-connector/trust-state.json",
    "/var/lib/happyranch-connector/trust-state.json.anchor",
    "/var/lib/happyranch-connector/trust-state.json.lock",
    "/var/lib/happyranch-tsnet-sidecar/tailscaled.state",
    "/var/lib/happyranch-tsnet-sidecar/credential.consumed",
    "/var/lib/happyranch-tsnet-sidecar/tailscaled.log.conf",
    "/var/lib/happyranch-tsnet-sidecar/tailscaled.log1.txt",
    "/var/lib/happyranch-tsnet-sidecar/tailscaled.log2.txt",
)
_QUARANTINE_CONFIGS = ("connector.json", "sidecar.json", "policy.json")
_REPLACEMENT_REMOVALS = (
    ("replacement-remove-trust-state", "var/lib/happyranch-connector/trust-state.json"),
    ("replacement-remove-trust-anchor", "var/lib/happyranch-connector/trust-state.json.anchor"),
    ("replacement-remove-trust-lock", "var/lib/happyranch-connector/trust-state.json.lock"),
    ("replacement-remove-tsnet-state", "var/lib/happyranch-tsnet-sidecar/tailscaled.state"),
    ("replacement-remove-consumed", "var/lib/happyranch-tsnet-sidecar/credential.consumed"),
    ("replacement-remove-tsnet-log-config", "var/lib/happyranch-tsnet-sidecar/tailscaled.log.conf"),
    ("replacement-remove-tsnet-log1", "var/lib/happyranch-tsnet-sidecar/tailscaled.log1.txt"),
    ("replacement-remove-tsnet-log2", "var/lib/happyranch-tsnet-sidecar/tailscaled.log2.txt"),
    ("replacement-remove-enrollment", _ENROLLMENT),
    ("replacement-remove-enrollment-link", f"{_CREDSTORE}/{_CREDENTIAL_NAME}"),
)
_UNIT_REMOVALS = (
    ("replacement-remove-target-link", f"{_UNIT_DIR}/multi-user.target.wants/{UNITS[2]}", "link"),
    ("replacement-remove-verifier-link", f"{_UNIT_DIR}/{UNITS[2]}.wants/{UNITS[3]}", "link"),
    *[("replacement-remove-unit-" + label, f"{_UNIT_DIR}/{name}", "unit") for label, name in zip(
        ("connector", "sidecar", "target", "verifier-socket", "verifier-service"), UNITS)],
    *[("replacement-remove-dropins-" + label, f"{_UNIT_DIR}/{name}.d", "directory") for label, name in zip(
        ("connector", "sidecar", "target", "verifier-socket", "verifier-service"), UNITS)],
    ("replacement-remove-target-wants", f"{_UNIT_DIR}/{UNITS[2]}.wants", "directory"),
)
_REPLACEMENT_FILES = (*_QUARANTINE_CONFIGS, "release-daemon.json", "daemon.token", "provision.json")


@dataclass(frozen=True)
class InstallInventory:
    """Read-only classification, never authority to remove or replay files."""

    state: str
    detail: str
    paths: tuple[str, ...]


def managed_units(layout: str = "source", *, connector_config: dict | None = None) -> dict[str, str]:
    """Use the shared renderers verbatim, including sidecar credential injection."""
    if layout not in ("source", "deb"):
        raise InstallerError("unknown installation layout")
    profile = (connector_config or {}).get("managed", {}).get("profile", "")
    python = (("/opt/happyranch-desktop/python/bin/python3", "-I") if layout == "deb"
              else ("/opt/happyranch/venv/bin/python",))
    sidecar = f"/opt/{'happyranch-desktop' if layout == 'deb' else 'happyranch'}/bin/happyranch-tsnet-sidecar"
    return {
        UNITS[0]: render_connector_unit(ConnectorUnitSpec(
            user="happyranch", group="happyranch", release_verifier=True,
            ingress_profile=profile,
            exec_start=(*python, "-m", "runtime.remote_access.cli",
                        "run", "--managed", "--config", "/etc/happyranch/connector.json"),
        )),
        UNITS[1]: render_sidecar_unit(SidecarUnitSpec(binary_path=sidecar)),
        UNITS[2]: render_managed_target(),
        UNITS[3]: render_verifier_socket(),
        UNITS[4]: render_verifier_unit(python),
    }


class ManagedInstaller(InstallerIO, InstallerValidation, LegacyIssuerReadback):
    """Root operator installer with injectable commands and account lookup.

    ``root`` redirects filesystem operations for disposable verification only;
    it is not a chroot and never changes the commands' host. Supply fake command
    runners when using a temporary root. ``root_owner`` supports that fixture.
    """

    def __init__(
        self, *, root: Path = Path("/"), root_owner: tuple[int, int] = (0, 0),
        layout: str = "source",
        manager: SystemdServiceManager | None = None,
        run: Callable[..., tuple[int, str]] | None = None,
        lookup_user: Callable[[str], pwd.struct_passwd] = pwd.getpwnam,
        lookup_group: Callable[[str], grp.struct_group] = grp.getgrnam,
    ) -> None:
        if not root.is_absolute() or ".." in root.parts:
            raise InstallerError(f"installation root must be an absolute literal path: {root}")
        if root == Path("/") and root_owner != (0, 0):
            raise InstallerError("live installation requires root ownership")
        self.root, self.root_owner = root, root_owner
        self.manager = manager or SystemdServiceManager(system=True, run=run)
        self._run = run or self.manager._run
        self._lookup_user, self._lookup_group = lookup_user, lookup_group
        self.units = managed_units(layout)
        self.layout = layout
        self._service_owner: tuple[int, int] | None = None
        self._headscale_owner: tuple[int, int] | None = None
        self._headscale_preparing = False

    def _inventory_files(
        self, relative: str, allowed: dict[str, tuple[tuple[int, int] | None, int | tuple[int, ...], bool | None]],
        *, directories: set[str] | None = None, runtime_diagnostics: bool = False,
    ) -> dict[str, bytes]:
        """Bounded, shallow inventory of one fixed directory; no unknown reads."""
        files: dict[str, bytes] = {}
        diagnostics = 0
        try:
            with self._directory(relative) as parent:
                with os.scandir(parent) as entries:
                    for index, entry in enumerate(entries):
                        path = str(self.root / relative / entry.name)
                        if index < 64 and entry.name in (directories or set()):
                            with self._directory(relative + "/" + entry.name):
                                pass
                            continue
                        if index < 64 and runtime_diagnostics and entry.name != "observe.sock":
                            try:
                                info = os.stat(entry.name, dir_fd=parent, follow_symlinks=False)
                            except OSError:
                                raise InstallerError(path) from None
                            if (diagnostics >= _RUNTIME_DIAGNOSTIC_LIMIT
                                    or not _runtime_diagnostic(entry.name, info, self._service_owner)):
                                raise InstallerError(path)
                            diagnostics += 1
                            files[entry.name] = b""
                            continue
                        if index >= 64 or entry.name not in allowed:
                            raise InstallerError(path if index < 64 else str(self.root / relative))
                        owner, mode, socket = allowed[entry.name]
                        if owner is None:
                            raise InstallerError(path)
                        try:
                            if socket is not False:
                                info = os.stat(entry.name, dir_fd=parent, follow_symlinks=False)
                                known_type = stat.S_ISSOCK if socket else stat.S_ISREG
                                if (not known_type(info.st_mode) or info.st_nlink != 1
                                        or (info.st_uid, info.st_gid) != owner
                                        or stat.S_IMODE(info.st_mode) != mode):
                                    raise InstallerError(path)
                                files[entry.name] = b""
                            else:
                                if isinstance(mode, tuple):
                                    actual_mode = stat.S_IMODE(os.stat(entry.name, dir_fd=parent, follow_symlinks=False).st_mode)
                                    if actual_mode not in mode:
                                        raise InstallerError(path)
                                else:
                                    actual_mode = mode
                                files[entry.name] = self._read_file(parent, entry.name, owner=owner, mode=actual_mode)
                        except (OSError, InstallerError):
                            raise InstallerError(path) from None
        except FileNotFoundError:
            return files
        except OSError:
            raise InstallerError(str(self.root / relative)) from None
        return files

    def _inventory_units(
        self, connector: dict, *,
        observed_units: dict[str, str] | None = None,
    ) -> tuple[set[str], dict[str, dict[str, str]]]:
        if connector and not isinstance(connector.get("managed"), dict):
            raise InstallerError(str(self.root / "etc/happyranch/connector.json"))
        expected = observed_units or managed_units(self.layout, connector_config=connector)
        present: set[str] = set()
        properties: dict[str, dict[str, str]] = {}
        for name, text in expected.items():
            path = str(self.root / _UNIT_DIR / name)
            try:
                with self._directory(_UNIT_DIR) as parent:
                    if self._read_unit(parent, name) != text.encode():
                        raise InstallerError(path)
                    present.add(name)
            except FileNotFoundError:
                pass
            except (OSError, InstallerError):
                raise InstallerError(path) from None
            # Inspect only these unit names, never neighboring custom units.
            for base in (_UNIT_DIR, "run/systemd/system", "usr/local/lib/systemd/system", "usr/lib/systemd/system"):
                dropins = f"{base}/{name}.d"
                try:
                    with self._directory(dropins) as parent:
                        with os.scandir(parent) as entries:
                            entry = next(entries, None)
                            if entry is not None:
                                raise InstallerError(str(self.root / dropins / entry.name))
                    if base != _UNIT_DIR:
                        with self._directory(base) as parent:
                            os.stat(name, dir_fd=parent, follow_symlinks=False)
                            raise InstallerError(str(self.root / base / name))
                except FileNotFoundError:
                    # A missing drop-in directory says nothing about a fragment.
                    if base != _UNIT_DIR:
                        try:
                            with self._directory(base) as parent:
                                os.stat(name, dir_fd=parent, follow_symlinks=False)
                                raise InstallerError(str(self.root / base / name))
                        except FileNotFoundError:
                            pass
                except (OSError, InstallerError) as exc:
                    if isinstance(exc, InstallerError) and str(exc).startswith(str(self.root) + "/"):
                        raise
                    raise InstallerError(str(self.root / dropins)) from None
            try:
                props = self.manager.show_properties(name, (
                    "FragmentPath", "DropInPaths", "NeedDaemonReload", "LoadState", "ActiveState",
                ))
            except (ServiceManagerError, OSError):
                raise InstallerError(path) from None
            loaded = name in present
            if (props.get("FragmentPath") != (path if loaded else "")
                    or props.get("DropInPaths") != ""
                    or props.get("NeedDaemonReload") != "no"
                    or props.get("LoadState") != ("loaded" if loaded else "not-found")
                    or props.get("ActiveState") not in ({"active", "inactive", "failed"} if loaded else {"inactive"})):
                # Manager strings can contain arbitrary data: report fixed paths only.
                raise InstallerError(path)
            properties[name] = props
        for directory, name in (("multi-user.target.wants", UNITS[2]),
                                (f"{UNITS[2]}.wants", UNITS[3])):
            path = str(self.root / _UNIT_DIR / directory / name)
            try:
                with self._directory(f"{_UNIT_DIR}/{directory}") as parent:
                    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    if (not stat.S_ISLNK(info.st_mode) or info.st_nlink != 1
                            or (info.st_uid, info.st_gid) != self.root_owner or name not in present
                            or os.readlink(name, dir_fd=parent) not in {f"../{name}", str(self.root / _UNIT_DIR / name)}):
                        raise InstallerError(path)
            except FileNotFoundError:
                pass
            except (OSError, InstallerError) as exc:
                if isinstance(exc, InstallerError) and str(exc).startswith(str(self.root) + "/"):
                    raise
                raise InstallerError(path) from None
        return present, properties

    def inventory(self) -> InstallInventory:
        """Classify only fixed managed destinations without executing old inputs.

        Unknown, unsafe or transaction-bearing inventories refuse. Stopped intact
        manual installs still require migration. This supplies no replacement,
        cleanup, source-token read or activation authority.
        """
        return self._inventory()

    def home_retirement_status(self) -> dict[str, str]:
        """Read-only home retirement detection, never removal/replay authority."""
        from runtime.remote_access.home_retirement import HomeRetirement

        return HomeRetirement(self).detect().public()

    def _inventory(self) -> InstallInventory:
        preparing = self._headscale_preparing
        location = str(self.root / "etc/happyranch")
        paths: list[str] = []
        try:
            self._service_owner = self._account(create=False)
            service = self._service_owner
            config_owner = None if service is None else (self.root_owner[0], service[1])
            home = None
            activation_present = False
            try:
                with self._directory(headscale.INSTALLER_STATE) as parent:
                    for name in ("home-activation.json", ".home-activation.json.pending"):
                        try:
                            os.stat(name, dir_fd=parent, follow_symlinks=False)
                            activation_present = True
                        except FileNotFoundError:
                            pass
            except FileNotFoundError:
                pass
            try:
                with self._directory("etc/happyranch") as parent:
                    sidecar = self._json(self._read_file(parent, "sidecar.json"))
            except FileNotFoundError:
                if activation_present:
                    raise InstallerError(location) from None
                sidecar = {}
            if "home_binding" in sidecar and not activation_present:
                return self._home_staged_inventory()
            if "home_binding" in sidecar or activation_present:
                from runtime.remote_access.home_admission import HomeProfileAdmission
                home = HomeProfileAdmission(self).require(ordinary=False)
                # Fixed Headscale custody map; readers only, never prepare.
                self._headscale_preparing = True
                self._inventory_files("etc/happyranch/headscale", {
                    name: (self._headscale_owner, 0o600, False)
                    for name in (*headscale.CONFIG_FILES, *headscale.LEAF_FILES, "policy.hujson")})
            else:
                # No B1 binding grants an exception to inert Headscale policy.
                # In particular, a lone home fragment remains crash drift.
                try:
                    self._headscale_inventory()
                except headscale.HeadscaleServiceError:
                    raise InstallerError(str(self.root / _UNIT_DIR / headscale.UNIT)) from None
            config_metadata = {name: (config_owner, (0o600, 0o640, 0o644), False)
                               for name in _QUARANTINE_CONFIGS}
            files = self._inventory_files("etc/happyranch", {
                **config_metadata,
                **({"control-ca.pem": (config_owner, 0o640, False),
                    "invite-issuer.json": (self.root_owner, 0o600, False)} if home else {}),
                "release-daemon.json": (self.root_owner, 0o644, False),
                "provision.json": (self.root_owner, 0o600, False),
                "daemon.token": (service, 0o600, False),
                "enrollment.key": (self.root_owner, 0o600, False),
            }, directories={"headscale", "empty-ca-directory"} if home else None)
            if home is not None:
                files.pop("control-ca.pem", None)
                files.pop("invite-issuer.json", None)
            paths.extend(str(self.root / "etc/happyranch" / name) for name in files)
            connector = self._json(files["connector.json"]) if "connector.json" in files else {}
            provenance = files.get("provision.json")
            modern = False
            if home is not None and any(files.get(name) != data for name, data in home.files.items()):
                raise InstallerError(location)
            required = {"connector.json", "sidecar.json", "policy.json", "release-daemon.json", "daemon.token"}
            if provenance is not None:
                # Recorded provisioning retains its exact mode contract. Only
                # unrecorded legacy configs may use the other safe modes above.
                with self._directory("etc/happyranch") as parent:
                    for name in _QUARANTINE_CONFIGS:
                        if name in files:
                            location = str(self.root / "etc/happyranch" / name)
                            if self._read_file(parent, name, owner=config_owner, mode=0o640) != files[name]:
                                raise InstallerError(location)
                location = str(self.root / "etc/happyranch/provision.json")
                self._validate_install_record("provision.json", provenance)
                record = self._json(provenance)
                modern = "version" in record
                digests = record["digests"] if modern else {
                    **record["configs"], "daemon.token": record["token_sha256"],
                }
                if (set(digests) != (required if modern else required - {"release-daemon.json"})
                        or any(not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
                               for value in digests.values())):
                    raise InstallerError(location)
                for name, digest in digests.items():
                    if (name in files and not (home is not None and name in {"connector.json", "sidecar.json"})
                            and hashlib.sha256(files[name]).hexdigest() != digest):
                        raise InstallerError(str(self.root / "etc/happyranch" / name))
            elif files:
                # Interrupted publication may precede the next config or record.
                # Validate every present artifact, including cross-config checks
                # when both are present; absence alone is a known partial state.
                # Inventory can classify/stop inert remnants, never admit them.
                self._validate_configs(files, staged=True, partial=True, require_current=False)
            if "release-daemon.json" in files:
                from runtime.remote_access.release_verifier import validate_record
                location = str(self.root / "etc/happyranch/release-daemon.json")
                validate_record(self._json(files["release-daemon.json"]))
            for relative, names in (
                (_STATE_DIRS[0], ("trust-state.json", "trust-state.json.anchor", "trust-state.json.lock")),
                (_STATE_DIRS[1], ("tailscaled.state", "credential.consumed", "tailscaled.log.conf",
                                  "tailscaled.log1.txt", "tailscaled.log2.txt")),
            ):
                location = str(self.root / relative)
                # State contents are never interpreted, replayed or copied.
                state = self._inventory_files(relative, {name: (service, 0o600, None) for name in names})
                paths.extend(str(self.root / relative / name) for name in state)
            for relative, entries in (
                ("run/happyranch-connector", {name: (service, 0o600, True) for name in ("ingress.sock", "control.sock")}),
                ("run/happyranch-tsnet-sidecar", {"health.json": (service, 0o644, False),
                    **({"observe.sock": (service, 0o600, True)} if home else {})}),
                ("run/happyranch-release-verifier", {"control.sock": (
                    None if service is None else (self.root_owner[0], service[1]), 0o660, True)}),
            ):
                location = str(self.root / relative)
                runtime = self._inventory_files(relative, entries,
                    runtime_diagnostics=relative == "run/happyranch-tsnet-sidecar")
                paths.extend(str(self.root / relative / name) for name in runtime)
            location = str(self.root / _CREDSTORE / _CREDENTIAL_NAME)
            self._check_enrollment()
            with_enrollment = False
            try:
                with self._directory(_CREDSTORE) as parent:
                    os.stat(_CREDENTIAL_NAME, dir_fd=parent, follow_symlinks=False)
                    with_enrollment = True
                    paths.append(location)
            except FileNotFoundError:
                pass
            location = str(self.root / _UNIT_DIR)
            units, properties = self._inventory_units(connector,
                observed_units=HomeProfileAdmission(self).observed_units(home) if home else None)
            paths.extend(str(self.root / _UNIT_DIR / name) for name in units)
            if home is not None:
                if len(units) != len(UNITS):
                    raise InstallerError(location)
                return InstallInventory("owned-activated" if home.receipt.phase == "ready" else "owned",
                                        home.receipt.phase, tuple(sorted(paths)))
            complete = required <= files.keys()
            failed = any(props["ActiveState"] == "failed" for props in properties.values())
            if modern and complete and len(units) in {0, len(UNITS)} and not failed:
                managed = connector.get("managed")
                profile = managed.get("profile") if isinstance(managed, dict) else None
                if profile != "linux-managed-identity-v1":
                    raise InstallerError(str(self.root / "etc/happyranch/connector.json"))
                return InstallInventory("modern", "recorded" if units else "staged-inert", tuple(sorted(paths)))
            if not files and not units and not paths and not with_enrollment:
                return InstallInventory("clean", "absent", ())
            if complete and len(units) == len(UNITS) and not failed:
                return InstallInventory("healthy-manual", "migration-required", tuple(sorted(paths)))
            return InstallInventory("known-failed-or-partial", "failed" if failed else "partial", tuple(sorted(paths)))
        except (OSError, ValueError, TypeError, KeyError, InstallerError) as exc:
            # Only our fixed diagnostic paths escape; never config/manager values.
            if isinstance(exc, InstallerError) and str(exc).startswith(str(self.root) + "/"):
                location = str(exc)
            return InstallInventory("manual-install-conflict", "unsafe-or-unrecognized", (location,))
        finally:
            self._headscale_preparing = preparing


    @staticmethod
    def _replacement_members() -> tuple[str, ...]:
        return tuple(sorted({
            *["etc/happyranch/" + name for name in _REPLACEMENT_FILES],
            *[relative for _, relative in _REPLACEMENT_REMOVALS],
            *[relative for _, relative, _ in _UNIT_REMOVALS],
            *[base + "/" + name + ".d" for base in ("run/systemd/system", "usr/local/lib/systemd/system",
                                                     "usr/lib/systemd/system") for name in UNITS],
            "run/happyranch-connector/ingress.sock", "run/happyranch-connector/control.sock",
            "run/happyranch-tsnet-sidecar/health.json", "run/happyranch-release-verifier/control.sock",
            *_STATE_DIRS, "run/happyranch-connector", "run/happyranch-tsnet-sidecar",
            "run/happyranch-release-verifier", "etc/happyranch", _CREDSTORE,
        }))

    def _replacement_snapshot(self) -> dict:
        """Capture only the fixed validated inventory; opaque state is metadata-only."""
        snapshot = {}
        opaque = {relative for _, relative in _REPLACEMENT_REMOVALS if relative.startswith("var/lib/")}
        for relative in self._replacement_members():
            try:
                with self._directory(str(Path(relative).parent)) as parent:
                    name = Path(relative).name
                    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    signature = [getattr(info, field) for field in (
                        "st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink",
                        "st_size", "st_mtime_ns", "st_ctime_ns")]
                    if stat.S_ISDIR(info.st_mode):
                        signature[6:] = [0, 0, 0]  # Own journal publication changes parent times.
                    value = {"metadata": signature}
                    if stat.S_ISREG(info.st_mode) and relative not in opaque:
                        value["digest"] = hashlib.sha256(self._read_file(parent, name,
                            owner=(info.st_uid, info.st_gid), mode=stat.S_IMODE(info.st_mode))).hexdigest()
                    elif stat.S_ISLNK(info.st_mode):
                        value["target"] = os.readlink(name, dir_fd=parent)
                    snapshot[relative] = value
            except FileNotFoundError:
                snapshot[relative] = "absent"
        return snapshot


    def stop_and_confirm(self) -> InstallInventory:
        """Stop only recognized remote units and prove manager quiescence.

        No filesystem mutation or admission reopening is performed. Callers must
        not cache this sampled observation as later file-mutation authority.
        """
        inventory = self._inventory()
        if inventory.state in {"manual-install-conflict", "healthy-manual"}:
            raise InstallerError(f"{inventory.state}: " + ", ".join(inventory.paths))
        order = (UNITS[2], UNITS[0], UNITS[1], UNITS[3], UNITS[4])
        for name in order:
            try:
                props = self.manager.show_properties(name, ("LoadState",))
                if props.get("LoadState") != "not-found":
                    self.manager.stop(name, timeout=50.0)
            except (ServiceManagerError, OSError):
                raise InstallerError(f"quiescence unconfirmed: {self.root / _UNIT_DIR / name}") from None
        self._confirm_quiescent()
        confirmed = self._inventory()
        if confirmed.state in {"manual-install-conflict", "healthy-manual"}:
            raise InstallerError(f"{confirmed.state}: " + ", ".join(confirmed.paths))
        return confirmed

    def _confirm_quiescent(self) -> None:
        """Sample the same stop proof again immediately before each unit effect."""
        order = (UNITS[2], UNITS[0], UNITS[1], UNITS[3], UNITS[4])
        for name in order:
            process_keys = ("MainPID", "ControlPID", "ControlGroup") if name in (UNITS[0], UNITS[1], UNITS[4]) else ()
            try:
                props = self.manager.show_properties(name, ("ActiveState", "SubState", "Job", *process_keys))
            except (ServiceManagerError, OSError):
                props = {}
            if (props.get("ActiveState") not in {"inactive", "failed"}
                    or props.get("SubState") not in {"dead", "failed"}
                    or (process_keys and (props.get("MainPID") != "0" or props.get("ControlPID") != "0"
                                          or props.get("ControlGroup") != ""))
                    or props.get("Job") != ""):
                raise InstallerError(f"quiescence unconfirmed: {self.root / _UNIT_DIR / name}")

    def _account(self, *, create: bool) -> tuple[int, int] | None:
        try:
            user = self._lookup_user("happyranch")
        except KeyError:
            if not create:
                return None
            code, _ = self._run([
                "useradd", "--system", "--user-group", "--no-create-home",
                "--home-dir", "/nonexistent", "--shell", "/usr/sbin/nologin", "happyranch",
            ], timeout=30.0)
            if code:
                raise InstallerError(f"cannot create happyranch service account (useradd exit {code})")
            try:
                user = self._lookup_user("happyranch")
            except KeyError as exc:
                raise InstallerError("useradd returned success but happyranch account is absent") from exc
        try:
            group = self._lookup_group("happyranch")
        except KeyError as exc:
            raise InstallerError("happyranch service group is absent") from exc
        if (user.pw_uid <= 0 or user.pw_gid <= 0 or group.gr_gid != user.pw_gid
                or user.pw_shell not in {"/usr/sbin/nologin", "/sbin/nologin", "/bin/false"}
                or user.pw_dir != "/nonexistent"):
            raise InstallerError("unsafe happyranch account: require non-root dedicated group, /nonexistent home and nologin shell")
        return user.pw_uid, user.pw_gid

    def _headscale_shape(self) -> tuple[int, int] | None:
        """Fresh identity/lock/group proof; matching metadata is not ownership."""
        try:
            user = self._lookup_user(headscale.ACCOUNT)
        except KeyError:
            return None
        try:
            group = self._lookup_group(headscale.ACCOUNT)
        except KeyError:
            raise headscale.HeadscaleServiceError("account-shape") from None
        with self._directory("etc") as parent:
            definitions = self._read_file(parent, "login.defs", owner=self.root_owner, mode=0o644).decode("ascii")
        limits: dict[str, int] = {}
        for line in definitions.splitlines():
            fields = line.split("#", 1)[0].split()
            if fields and fields[0] in {"UID_MIN", "GID_MIN", "SYS_UID_MIN", "SYS_UID_MAX", "SYS_GID_MIN", "SYS_GID_MAX"}:
                if len(fields) != 2 or not fields[1].isascii() or not fields[1].isdigit() or fields[0] in limits:
                    raise headscale.HeadscaleServiceError("account-shape")
                limits[fields[0]] = int(fields[1])
        uid_min, gid_min = limits.get("UID_MIN", 1000), limits.get("GID_MIN", 1000)
        if (user.pw_name != headscale.ACCOUNT or group.gr_name != headscale.ACCOUNT
                or not 0 < limits.get("SYS_UID_MIN", 101) <= user.pw_uid <= limits.get("SYS_UID_MAX", uid_min - 1) < uid_min
                or not 0 < limits.get("SYS_GID_MIN", 101) <= user.pw_gid <= limits.get("SYS_GID_MAX", gid_min - 1) < gid_min
                or group.gr_gid != user.pw_gid or set(group.gr_mem) - {headscale.ACCOUNT}
                or user.pw_shell not in {"/usr/sbin/nologin", "/sbin/nologin", "/bin/false"}):
            raise headscale.HeadscaleServiceError("account-shape")
        # NSS inventories also detect shared primary groups and UID/GID aliases.
        for database, field, identity in (("passwd", 2, user.pw_uid), ("passwd", 3, user.pw_gid),
                                          ("group", 2, user.pw_gid)):
            code, output = self._run(["getent", database], timeout=10.0)
            if code or len(output) > 65536:
                raise headscale.HeadscaleServiceError("account-shape")
            matches = []
            for line in output.splitlines():
                fields = line.split(":")
                if len(fields) != (7 if database == "passwd" else 4) or not fields[field].isdigit():
                    raise headscale.HeadscaleServiceError("account-shape")
                if int(fields[field]) == identity:
                    matches.append(fields[0])
            if matches != [headscale.ACCOUNT]:
                raise headscale.HeadscaleServiceError("account-shape")
        code, status = self._run(["passwd", "--status", headscale.ACCOUNT], timeout=10.0)
        fields = status.split()
        if code or len(fields) != 7 or fields[:2] != [headscale.ACCOUNT, "L"]:
            raise headscale.HeadscaleServiceError("account-shape")
        if not user.pw_dir.startswith("/") or ".." in Path(user.pw_dir).parts or user.pw_dir == "/":
            raise headscale.HeadscaleServiceError("account-shape")
        try:
            # Every existing home component must be root-owned, no service write.
            with self._directory(user.pw_dir[1:]):
                pass
        except FileNotFoundError:
            if user.pw_dir != "/nonexistent":
                raise headscale.HeadscaleServiceError("account-shape") from None
        return user.pw_uid, user.pw_gid

    def _headscale_receipt(self) -> dict | None:
        try:
            with self._directory(headscale.INSTALLER_STATE) as parent:
                # Pending residue is never evidence of creation.
                try:
                    os.stat(f".{headscale.RECEIPT}.pending", dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise headscale.HeadscaleServiceError("account-receipt-uncertain")
                data = self._read_file(parent, headscale.RECEIPT, owner=self.root_owner, mode=0o600)
        except FileNotFoundError:
            return None
        value = self._json(data)
        if (set(value) != {"version", "account", "uid", "gid", "group_created_with_user"}
                or type(value["version"]) is not int or value["version"] != 1
                or value["account"] != headscale.ACCOUNT
                or type(value["uid"]) is not int or value["uid"] <= 0
                or type(value["gid"]) is not int or value["gid"] <= 0
                or value["group_created_with_user"] is not True):
            raise headscale.HeadscaleServiceError("account-receipt-uncertain")
        return value

    def _headscale_publication(self, removed: tuple[str, ...] = (), *, missing_ok: bool = False) -> dict | None:
        """Only the final root receipt proves ownership; never adopt by bytes.

        Missing members refuse by default. B6 alone supplies missing_ok after
        binding its current activation receipt; absence is permitted there.
        ``removed`` names only members freshly deleted in an inert invocation.
        """
        try:
            with self._directory(headscale.INSTALLER_STATE) as parent:
                try:
                    os.stat(f".{headscale.PUBLICATION}.pending", dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise headscale.HeadscaleServiceError("publication-uncertain")
                data = self._read_file(parent, headscale.PUBLICATION, owner=self.root_owner, mode=0o600)
        except FileNotFoundError:
            if removed:
                raise headscale.HeadscaleServiceError("publication-uncertain") from None
            return None
        record = self._json(data)
        extended = "directories" in record
        if (set(record) != {"version", "uid", "gid", "dial_ip", "files"} | ({"directories"} if extended else set())
                or type(record["version"]) is not int or record["version"] != 1
                or type(record["uid"]) is not int or type(record["gid"]) is not int
                or (record["uid"], record["gid"]) != self._headscale_owner
                or type(record["files"]) is not dict):
            raise headscale.HeadscaleServiceError("publication-uncertain")
        expected = self._headscale_config(record["dial_ip"])
        members = self._headscale_members(extended)
        if (set(record["files"]) != set(members)
                or any(type(value) is not str or re.fullmatch("[0-9a-f]{64}", value) is None
                       for value in record["files"].values())
                or (extended and record["directories"] != ["/" + headscale.EMPTY_CA])
                or any(name not in {Path(path).name for path in members} | {Path(headscale.EMPTY_CA).name}
                       for name in removed)):
            raise headscale.HeadscaleServiceError("publication-uncertain")
        for path, (owner, mode) in members.items():
            relative, name = str(Path(path.lstrip("/")).parent), Path(path).name
            try:
                with self._directory(relative) as parent:
                    self._headscale_no_pending(parent, name)
                    if name in removed:
                        try:
                            os.stat(name, dir_fd=parent, follow_symlinks=False)
                        except FileNotFoundError:
                            continue
                        raise headscale.HeadscaleServiceError("publication-uncertain")
                    value = self._read_file(parent, name, owner=owner, mode=mode)
                    if (hashlib.sha256(value).hexdigest() != record["files"][path]
                            or (name in expected and value != expected[name])):
                        raise headscale.HeadscaleServiceError("publication-uncertain")
            except FileNotFoundError:
                if not missing_ok:
                    raise
        if extended:
            try:
                self._headscale_empty_ca(removed=Path(headscale.EMPTY_CA).name in removed)
            except FileNotFoundError:
                if not missing_ok:
                    raise
        return record


    @staticmethod
    def _headscale_no_pending(parent: int, name: str) -> None:
        try:
            os.stat(f".{name}.pending", dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise headscale.HeadscaleServiceError("publication-uncertain")


    def _headscale_members(self, extended: bool = True) -> dict[str, tuple[tuple[int, int], int]]:
        members = {f"/{headscale.CUSTODY[0]}/{name}": (self._headscale_owner, 0o600)
                   for name in (*headscale.CONFIG_FILES, *(headscale.LEAF_FILES if extended else ()))}
        if extended:
            try:
                group = self._lookup_group("happyranch")
            except KeyError:
                raise headscale.HeadscaleServiceError("custody-uncertain") from None
            if (group.gr_name != "happyranch" or type(group.gr_gid) is not int
                    or not 0 <= group.gr_gid < 2**32 - 1):
                raise headscale.HeadscaleServiceError("custody-uncertain")
            members["/" + headscale.CONTROL_CA] = ((self.root_owner[0], group.gr_gid), 0o640)
        return members

    def _headscale_empty_ca(self, *, removed: bool = False) -> None:
        path = Path(headscale.EMPTY_CA)
        with self._directory(str(path.parent)) as parent:
            self._headscale_no_pending(parent, path.name)
            if removed:
                self._refuse_provision_entry(parent, path.name)
                return
        with self._directory(headscale.EMPTY_CA) as directory:
            with os.scandir(directory) as entries:
                if os.fstat(directory).st_nlink != 2 or next(entries, None) is not None:
                    raise headscale.HeadscaleServiceError("publication-uncertain")

    def _headscale_ca_sources(self) -> dict[str, bytes] | None:
        """Read ONLY public CA and leaf staging; never open the CA private key."""
        try:
            with self._directory(headscale.HOME_CA) as parent:
                names: set[str] = set()
                with os.scandir(parent) as entries:
                    for entry in entries:
                        names.add(entry.name)
                        if len(names) > 4:
                            raise headscale.HeadscaleServiceError("materializer-uncertain")
                if not names:
                    return None
                if names not in ({"ca.pem", "ca.key"}, {"ca.pem", "ca.key", "leaf.pem", "leaf.key"}):
                    raise headscale.HeadscaleServiceError("materializer-uncertain")
                result = {}
                for name in ("ca.pem", "ca.key", "leaf.pem", "leaf.key"):
                    if name not in names:
                        continue
                    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                            or (info.st_uid, info.st_gid) != self.root_owner
                            or stat.S_IMODE(info.st_mode) != 0o600 or not 0 < info.st_size <= 4096):
                        raise headscale.HeadscaleServiceError("materializer-uncertain")
                    if name != "ca.key":
                        result[name] = self._read_file(parent, name, owner=self.root_owner, mode=0o600)
                return result
        except FileNotFoundError:
            # Only the directory itself may be absent, never a partial pair.
            with self._directory(headscale.INSTALLER_STATE) as parent:
                try:
                    os.stat("home-ca", dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    return None
            raise headscale.HeadscaleServiceError("materializer-uncertain") from None
    @staticmethod
    def _headscale_config(dial_ip: str) -> dict[str, bytes]:
        return {
            "config.yaml": home_headscale.render_headscale_config(
                dial_ip=dial_ip, policy_path=headscale.POLICY_PATH).encode(),
            "derp.yaml": home_headscale.render_private_derp_map(dial_ip=dial_ip).encode(),
        }

    def _headscale_inventory(self, removed: tuple[str, ...] = ()) -> None:
        """Admit empty/policy custody or exactly the root-receipted members.

        DB, unreceipted key, socket, pending and unknown objects remain foreign. Nothing
        permits adopting a running or unrelated installation.
        """
        publication = self._headscale_publication(removed)
        if not publication or "directories" not in publication:
            try:
                with self._directory("etc/happyranch") as parent:
                    for name in ("control-ca.pem", "empty-ca-directory"):
                        self._refuse_provision_entry(parent, name)
            except FileNotFoundError:
                pass
        paths = list(_HEADSCALE_UNIT_DIRS)
        with self._directory() as root:
            try:
                info = os.stat("lib", dir_fd=root, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                if stat.S_ISLNK(info.st_mode):
                    if os.readlink("lib", dir_fd=root) not in {"usr/lib", "/usr/lib"}:
                        raise headscale.HeadscaleServiceError("foreign-unit")
                else:
                    paths.append("lib/systemd/system")
        # Publication must not complete existing dependencies or activation
        # triggers, including implicit same-stem units and D-Bus policy.
        self._headscale_cleanup_links((*paths, *_HEADSCALE_DBUS_DIRS), category="foreign-unit")
        for path in (*paths, *headscale.CUSTODY):
            try:
                with self._directory(path) as parent:
                    if path not in headscale.CUSTODY:
                        if path == _UNIT_DIR:
                            try:
                                data = self._read_unit(parent, headscale.UNIT)
                            except FileNotFoundError:
                                data = None
                            if data is not None and data != render_headscale_unit().encode():
                                raise headscale.HeadscaleServiceError("foreign-unit")
                        # Drop-ins merge across load paths, including dash-prefix
                        # and service-wide overrides; an empty dir is foreign too.
                        entries = (headscale.UNIT + ".d", "happyranch-.service.d", "service.d",
                                   "." + headscale.UNIT + ".pending")
                        if path != _UNIT_DIR:
                            entries += (headscale.UNIT,)
                        for extra in entries:
                            try:
                                os.stat(extra, dir_fd=parent, follow_symlinks=False)
                            except FileNotFoundError:
                                continue
                            raise headscale.HeadscaleServiceError("foreign-unit")
                    else:
                        entries = os.listdir(parent)
                        allowed = {"policy.hujson"}
                        if publication:
                            allowed.update(set(headscale.CONFIG_FILES) - set(removed))
                            if "directories" in publication:
                                allowed.update(set(headscale.LEAF_FILES) - set(removed))
                        if entries and (path != headscale.CUSTODY[0] or set(entries) - allowed):
                            raise headscale.HeadscaleServiceError("foreign-custody")
                        if entries and self._read_file(parent, "policy.hujson", owner=self._headscale_owner,
                                                       mode=0o600) != headscale.render_policy():
                            raise headscale.HeadscaleServiceError("foreign-custody")
            except FileNotFoundError:
                pass

    def _headscale_cleanup_links(self, paths: tuple[str, ...] = (_UNIT_DIR, "run/systemd/system"),
                                 *, category: str = "cleanup-uncertain") -> None:
        """Preparation and disable require no foreign activation policy.

        Inspect supplied trees without following links; share entry/depth limits
        across all roots. Generators and later root writers remain cooperative.
        """
        count = 0

        def inspect(parent: int, depth: int, relative: str, dbus: bool) -> None:
            nonlocal count
            if depth > 32:
                raise headscale.HeadscaleServiceError(category)
            with os.scandir(parent) as entries:
                for entry in entries:
                    count += 1
                    if count > 65536:
                        raise headscale.HeadscaleServiceError(category)
                    info = os.stat(entry.name, dir_fd=parent, follow_symlinks=False)
                    if (f"{relative}/{entry.name}" == PACKAGED_HELPER_UNIT
                            and not stat.S_ISREG(info.st_mode)):
                        raise headscale.HeadscaleServiceError(category)
                    managed = relative == _UNIT_DIR and entry.name == headscale.UNIT
                    if not dbus and entry.name.startswith("happyranch-headscale."):
                        if not managed or self._read_unit(parent, entry.name) != render_headscale_unit().encode():
                            raise headscale.HeadscaleServiceError(category)
                    if stat.S_ISLNK(info.st_mode):
                        target = os.readlink(entry.name, dir_fd=parent)
                        if entry.name == headscale.UNIT or Path(target).name == headscale.UNIT:
                            raise headscale.HeadscaleServiceError(category)
                    elif stat.S_ISDIR(info.st_mode):
                        child = os.open(entry.name, _DIR_FLAGS, dir_fd=parent)
                        try:
                            self._check_directory(child, "systemd config inventory", None)
                            inspect(child, depth + 1, f"{relative}/{entry.name}", dbus)
                        finally:
                            os.close(child)
                    elif stat.S_ISREG(info.st_mode) and not managed and (
                            dbus or entry.name.endswith((".service", ".socket", ".target", ".device",
                                ".mount", ".automount", ".swap", ".timer", ".path", ".slice", ".scope"))
                            or (relative.endswith(".d") and entry.name.endswith(".conf"))):
                        # Foreign files are observed, never adopted: do not
                        # impose the managed provisioning reader's file modes.
                        descriptor = os.open(entry.name, _FILE_FLAGS, dir_fd=parent)
                        try:
                            opened = os.fstat(descriptor)
                            def signature(value: os.stat_result) -> tuple[int, ...]:
                                return (value.st_dev, value.st_ino, value.st_uid, value.st_gid, value.st_mode,
                                        value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
                            if (not stat.S_ISREG(opened.st_mode) or opened.st_size > 65536
                                    or signature(info) != signature(opened)):
                                raise InstallerError("uncertain activation policy")
                            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                                data = stream.read(65537)
                            if (len(data) > 65536 or len(data) != opened.st_size
                                    or signature(opened) != signature(os.fstat(descriptor))
                                    or signature(opened) != signature(os.stat(
                                        entry.name, dir_fd=parent, follow_symlinks=False))):
                                raise InstallerError("uncertain activation policy")
                            if ((b"happyranch-headscale" in data
                                 or f"{relative}/{entry.name}" == PACKAGED_HELPER_UNIT) and not _owned_helper_unit(
                                    f"{relative}/{entry.name}", opened, data, self.root_owner)):
                                raise headscale.HeadscaleServiceError(category)
                        finally:
                            os.close(descriptor)

        for path in paths:
            opened = False
            try:
                with self._directory(path) as parent:
                    opened = True
                    inspect(parent, 0, path, path in _HEADSCALE_DBUS_DIRS)
            except FileNotFoundError:
                # Only an absent config root is empty; a disappearing entry
                # during inspection must not clear deletion admission.
                if opened:
                    raise headscale.HeadscaleServiceError(category) from None

    def _headscale_cleanup_identity(self, receipt: dict | None) -> tuple[bool, bool]:
        """Re-observe remaining identities; a receipt never licenses UID reuse."""
        if self._headscale_receipt() != receipt:
            raise headscale.HeadscaleServiceError("cleanup-uncertain")
        owner = self._headscale_shape()
        if receipt is None:
            self._headscale_owner = owner
            if owner is None:
                try:
                    self._lookup_group(headscale.ACCOUNT)
                except KeyError:
                    pass
                else:
                    raise headscale.HeadscaleServiceError("cleanup-uncertain")
            return owner is not None, False
        self._headscale_owner = receipt["uid"], receipt["gid"]
        if owner is not None and owner != self._headscale_owner:
            raise headscale.HeadscaleServiceError("cleanup-uncertain")
        try:
            group = self._lookup_group(headscale.ACCOUNT)
        except KeyError:
            group = None
        if group is not None and (group.gr_name != headscale.ACCOUNT or group.gr_gid != receipt["gid"]
                                  or set(group.gr_mem) - ({headscale.ACCOUNT} if owner else set())):
            raise headscale.HeadscaleServiceError("cleanup-uncertain")
        for database in ("passwd", "group"):
            code, output = self._run(["getent", database], timeout=10.0)
            if code or len(output) > 65536:
                raise headscale.HeadscaleServiceError("cleanup-uncertain")
            uid_names, gid_names = [], []
            for line in output.splitlines():
                fields = line.split(":")
                if (len(fields) != (7 if database == "passwd" else 4)
                        or not fields[2].isascii() or not fields[2].isdigit()
                        or (database == "passwd" and (not fields[3].isascii() or not fields[3].isdigit()))):
                    raise headscale.HeadscaleServiceError("cleanup-uncertain")
                if database == "passwd" and int(fields[2]) == receipt["uid"]:
                    uid_names.append(fields[0])
                if int(fields[3 if database == "passwd" else 2]) == receipt["gid"]:
                    gid_names.append(fields[0])
            expected = [headscale.ACCOUNT] if (owner if database == "passwd" else group) else []
            if gid_names != expected or (database == "passwd" and uid_names != expected):
                raise headscale.HeadscaleServiceError("cleanup-uncertain")
        # NSS enumeration may omit an entry that numeric lookup still resolves.
        for database, identity, expected in (("passwd", receipt["uid"], bool(owner)),
                                             ("group", receipt["gid"], group is not None)):
            code, output = self._run(["getent", database, str(identity)], timeout=10.0)
            if not expected:
                if code != 2 or output:
                    raise headscale.HeadscaleServiceError("cleanup-uncertain")
            else:
                fields = output.rstrip("\n").split(":")
                if (code or len(output) > 65536 or len(fields) != (7 if database == "passwd" else 4)
                        or fields[0] != headscale.ACCOUNT or fields[2] != str(identity)):
                    raise headscale.HeadscaleServiceError("cleanup-uncertain")
        return owner is not None, group is not None

    def _headscale_stopped(self) -> None:
        props = self.manager.show_properties(headscale.UNIT, (
            "LoadState", "FragmentPath", "DropInPaths", "ActiveState", "SubState",
            "MainPID", "ControlPID", "ControlGroup", "Job"))
        if (props.get("LoadState") not in {"loaded", "not-found"}
                or props.get("FragmentPath") != (str(self.root / _UNIT_DIR / headscale.UNIT)
                                                  if props.get("LoadState") == "loaded" else "")
                or props.get("DropInPaths") != ""
                or props.get("ActiveState") not in {"inactive", "failed"}
                or props.get("SubState") not in {"dead", "failed"}
                or any(props.get(key) != "0" for key in ("MainPID", "ControlPID"))
                or props.get("ControlGroup") != "" or props.get("Job") != ""):
            raise headscale.HeadscaleServiceError("cleanup-uncertain")
        code, output = self._run(["ps", "-eo", "ruid=,euid=,suid=,fuid="], timeout=10.0)
        if code or len(output) > 65536:
            raise headscale.HeadscaleServiceError("cleanup-uncertain")
        for line in output.splitlines():
            fields = line.split()
            if (len(fields) != 4 or any(not field.isascii() or not field.isdigit() for field in fields)
                    or (self._headscale_owner is not None and self._headscale_owner[0] in map(int, fields))):
                raise headscale.HeadscaleServiceError("cleanup-uncertain")


    def _cleanup_issuer(self, *, db_retired: bool = False) -> None:
        partial = False
        lock = None
        try:
            receipt, present = self._issuer_inventory(cleanup=True, db_retired=db_retired)
            if not present:
                return
            with self._publication_parent(HOME_ISSUER) as (directory, revalidate):
                info = os.fstat(directory)
                if receipt is None or (receipt["device"], receipt["inode"]) != (info.st_dev, info.st_ino):
                    raise InstallerError("issuer directory changed")
                members = self._issuer_members(directory, db_retired=db_retired)
                if "invite-issuer.lock" in members:
                    lock = os.open("invite-issuer.lock", _FILE_FLAGS, dir_fd=directory)
                    if self._issuer_signature(os.fstat(lock)) != members["invite-issuer.lock"][0]:
                        raise InstallerError("issuer lock changed")
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if self._issuer_receipt() != receipt or self._issuer_members(directory, db_retired=db_retired) != members:
                    raise InstallerError("issuer changed")
                with self._publication_parent(headscale.INSTALLER_STATE) as (parent, parent_revalidate):
                    revalidate()
                    parent_revalidate()
                    # A partial unlink must not make the next call mistake a
                    # surviving lock/empty directory for a complete operation.
                    marker = "invite-issuer.cleanup.pending"
                    fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                 0o600, dir_fd=directory)
                    partial = True
                    try:
                        os.fchown(fd, *self.root_owner)
                        os.fchmod(fd, 0o600)
                        if os.write(fd, b"cleanup\n") != 8:
                            raise InstallerError("issuer cleanup marker")
                        os.fsync(fd)
                        marker_info = os.fstat(fd)
                        if ((marker_info.st_uid, marker_info.st_gid, marker_info.st_mode, marker_info.st_nlink)
                                != (*self.root_owner, stat.S_IFREG | 0o600, 1)):
                            raise InstallerError("issuer cleanup marker changed")
                    finally:
                        os.close(fd)
                    os.fsync(directory)
                    for name, (before, data) in members.items():
                        revalidate()
                        parent_revalidate()
                        if (self._issuer_signature(os.stat(name, dir_fd=directory, follow_symlinks=False)) != before
                                or (name.endswith(".json") and self._read_file(directory, name,
                                    owner=self.root_owner, mode=0o600) != data)):
                            raise InstallerError("issuer changed")
                        os.unlink(name, dir_fd=directory)
                        os.fsync(directory)
                    revalidate()
                    parent_revalidate()
                    if (os.listdir(directory) != [marker]
                            or self._issuer_signature(os.stat(marker, dir_fd=directory, follow_symlinks=False))
                            != self._issuer_signature(marker_info)):
                        raise InstallerError("issuer cleanup changed")
                    os.unlink(marker, dir_fd=directory)
                    os.fsync(directory)
                    os.rmdir("home-issuer", dir_fd=parent)
                    os.fsync(parent)
            return
        except (OSError, InstallerError, ValueError, UnicodeError, headscale.HeadscaleServiceError):
            pass
        finally:
            if lock is not None:
                os.close(lock)
        raise headscale.HeadscaleServiceError("issuer-partial-cleanup-uncertain" if partial else "issuer-cleanup-uncertain")

    def _retire_home_minters(self, *, proof: Callable[[], None], require: Callable[[], None],
                            retire: Callable, removing: Callable[[], None]) -> None:
        """M3-M5: validate the whole fixed set under its existing locks first.

        Scan streaming, twice, without buffering 600,000 names or reading any
        record contents. The activation lock and both user locks span removal.
        """
        with ExitStack() as stack:
            try:
                base, check_base = stack.enter_context(self._publication_parent(_HOME_MINTER))
            except FileNotFoundError:
                return
            users = {}
            with os.scandir(base) as entries:
                for index, item in enumerate(entries):
                    if index >= 2 or item.name not in _HOME_MINTER_USERS:
                        raise InstallerError("home minter child")
                    users[item.name] = None
            if os.fstat(base).st_nlink != 2 + len(users):
                raise InstallerError("home minter links")

            def record_info(directory: int, name: str) -> os.stat_result:
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or (info.st_uid, info.st_gid) != self.root_owner
                        or stat.S_IMODE(info.st_mode) != 0o600):
                    raise InstallerError("home minter record")
                return info

            for user in _HOME_MINTER_USERS:
                if user not in users:
                    continue
                directory, check = stack.enter_context(self._publication_parent(_HOME_MINTER + "/" + user))
                if os.fstat(directory).st_nlink != 2:
                    raise InstallerError("home minter directory")
                fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
                counts = dict(reservations=0, markers=0, fences=0, pending_fences=0)
                with os.scandir(directory) as entries:
                    for index, item in enumerate(entries):
                        if index >= _HOME_MINTER_LIMIT:
                            raise InstallerError("home minter bound")
                        if re.fullmatch(r"operation-[0-9a-f]{64}", item.name):
                            counts["reservations"] += 1
                        elif item.name in {"user-create", "fence", "fence.pending"}:
                            counts[{"user-create": "markers", "fence": "fences",
                                    "fence.pending": "pending_fences"}[item.name]] += 1
                        elif item.name != "rpc.lock":
                            raise InstallerError("home minter name")
                        record_info(directory, item.name)
                # Direct fixed Go RPCs also hold rpc.lock. Never remove it from
                # under an active RPC, even if another writer omitted its outer lock.
                try:
                    lock = os.open("rpc.lock", _FILE_FLAGS, dir_fd=directory)
                except FileNotFoundError:
                    pass
                else:
                    stack.callback(os.close, lock)
                    if self._issuer_signature(os.fstat(lock)) != self._issuer_signature(record_info(directory, "rpc.lock")):
                        raise InstallerError("home minter lock changed")
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                check()
                users[user] = directory, check, counts
            check_base()
            proof()

            for user in _HOME_MINTER_USERS:
                if user not in users:
                    continue
                directory, check, counts = users[user]

                def remove() -> None:
                    require()
                    proof()
                    check_base()
                    check()

                    def unlink(name: str) -> None:
                        info = record_info(directory, name)
                        check_base()
                        check()
                        if self._issuer_signature(record_info(directory, name)) != self._issuer_signature(info):
                            raise InstallerError("home minter changed")
                        removing()
                        os.unlink(name, dir_fd=directory)

                    # All users were validated before the first effect. Recheck
                    # each leaf just before unlink; fixed records and RPC lock last.
                    with os.scandir(directory) as entries:
                        for index, item in enumerate(entries):
                            if index >= _HOME_MINTER_LIMIT:
                                raise InstallerError("home minter bound")
                            if re.fullmatch(r"operation-[0-9a-f]{64}", item.name):
                                unlink(item.name)
                            elif item.name not in {"user-create", "fence", "fence.pending", "rpc.lock"}:
                                raise InstallerError("home minter name")
                    for name in ("user-create", "fence", "fence.pending", "rpc.lock"):
                        try:
                            unlink(name)
                        except FileNotFoundError:
                            pass
                    os.fsync(directory)
                    check_base()
                    check()
                    with os.scandir(directory) as entries:
                        nonempty = next(entries, None) is not None
                    if nonempty or os.fstat(directory).st_nlink != 2:
                        raise InstallerError("home minter not empty")
                    # M4: release only after fsync, then remove this empty user
                    # directory before acknowledging its retirement in B16.
                    fcntl.flock(directory, fcntl.LOCK_UN)
                    check_base()
                    removing()
                    os.rmdir(user, dir_fd=base)
                    os.fsync(base)

                retire("subject-home-minter-" + user.removeprefix("happyranch-"), remove, counts=counts)
            require()
            proof()
            check_base()
            with self._publication_parent(headscale.INSTALLER_STATE) as (parent, check_parent):
                check_base()
                check_parent()
                with os.scandir(base) as entries:
                    nonempty = next(entries, None) is not None
                if nonempty or os.fstat(base).st_nlink != 2:
                    raise InstallerError("home minter not empty")
                removing()
                os.rmdir("home-minter", dir_fd=parent)
                os.fsync(parent)

    def _cleanup_home_headscale(
        self, release_uid: int, home_id: str, control_plane_id: str, connector_id: str,
        generation: int, operation_id: str | None = None, *, revoke_succeeded: bool = False,
    ) -> dict[str, str]:
        """Receipt-bound B6 executor. The lifecycle caller supplies trusted expectations.

        The caller stops all owned services first. The activation-directory lock
        serializes existing cooperative activation writers; sampled native state
        and literal parents are freshly checked before every destructive effect.
        No authentication, stop, admission revocation or public lifecycle is added.
        """
        from runtime.remote_access.legacy_home_receipt import ACTIVATION_PATH, LIVE_MEMBERS, OWNED_LINK_TARGET
        from runtime.remote_access.legacy_home_readback import LegacyHomeReadback, LegacyHomeTransactions, _signature
        from runtime.remote_access import home_audit
        from runtime.remote_access.home_unit_gate import HomeUnitGate

        partial = False
        self._headscale_preparing = True
        try:
            if self.layout != "deb":
                raise InstallerError("deb-layout-required")
            gate = HomeUnitGate(expected_release_uid=release_uid, expected_home_id=home_id,
                                expected_control_plane_id=control_plane_id,
                                expected_connector_id=connector_id, expected_generation=generation,
                                root=self.root, root_owner=self.root_owner)
            store = LegacyHomeReadback(root=self.root, root_owner=self.root_owner)
            transactions = LegacyHomeTransactions(root=self.root, root_owner=self.root_owner)
            with transactions.locked() as activation_directory:
                audit = home_audit.HomeAudit(self.root / headscale.INSTALLER_STATE / "home-audit.jsonl",
                    uid=self.root_owner[0], gid=self.root_owner[1], directory_fd=activation_directory)
                current = store.read(expected_generation=generation)
                if operation_id is not None and current.operation_id != operation_id:
                    raise InstallerError("home operation changed")
                if (current.release_uid, current.home_id, current.control_plane_id, current.connector_id) != (
                        release_uid, home_id, control_plane_id, connector_id):
                    raise InstallerError("home binding")
                receipt = self._headscale_receipt()
                self._headscale_cleanup_identity(receipt)
                self._service_owner = self._account(create=False)
                if self._headscale_owner is None or self._service_owner is None:
                    raise InstallerError("cleanup identity")
                publication = self._headscale_publication(missing_ok=True)
                if receipt is None and publication is None:
                    raise InstallerError("Headscale UID/GID not receipted")
                if publication is not None and publication["dial_ip"] != current.dial_ip:
                    raise InstallerError("publication binding")

                link_disabled = False

                def require() -> None:
                    if store.read(expected_generation=generation) != current:
                        raise InstallerError("home receipt changed")
                    self._headscale_cleanup_identity(receipt)
                    if self._account(create=False) != self._service_owner:
                        raise InstallerError("service identity changed")
                    self._confirm_quiescent()
                    self._headscale_stopped()
                    gate._inspect(require_absent=link_disabled)

                # kind, owner (directory GID may be unspecified by B6), mode, digest.
                members: dict[str, tuple[str, tuple[int, int | None], int, str | None]] = {}
                for path in LIVE_MEMBERS:
                    kind = ("directory" if path.endswith("/acme-unused") else
                            "socket" if path.endswith(".sock") else "file")
                    owner = (self._service_owner if path.endswith("/observe.sock") else
                             (self._headscale_owner[0], None) if kind == "directory" else self._headscale_owner)
                    members[path] = kind, owner, 0o700 if kind == "directory" else 0o600, None
                custody = self._headscale_members()
                for path, digest in current.member_digests.items():
                    owner, mode = custody.get(path, (self.root_owner, 0o600))
                    if path == OWNED_LINK_TARGET:
                        mode = 0o644
                    elif path.endswith("/policy.hujson"):
                        owner = self._headscale_owner
                    members[path] = "file", owner, mode, digest
                if publication is not None:
                    for path, digest in publication["files"].items():
                        if path in members and members[path][3] != digest:
                            raise InstallerError("publication digest")
                        owner, mode = custody[path]
                        members[path] = "file", owner, mode, digest
                    if "directories" in publication:
                        members["/" + headscale.EMPTY_CA] = "directory", self.root_owner, 0o755, None
                # I3c C2: fixed root-only staging, metadata only. The CA private
                # bytes, opaque trust/tsnet state and sockets are never read.
                for name in ("ca.key", "ca.pem", "leaf.pem", "leaf.key"):
                    members[f"/{headscale.HOME_CA}/{name}"] = "file", self.root_owner, 0o600, None
                state_paths = _HOME_STATE_MEMBERS
                if revoke_succeeded:
                    for path in state_paths:
                        members[path] = "file", self._service_owner, 0o600, None

                def entry(path: str, *, remove: bool = False) -> bool:
                    nonlocal partial
                    kind, owner, mode, digest = members[path]
                    relative = Path(path.lstrip("/"))
                    try:
                        with self._publication_parent(str(relative.parent)) as (parent, check):
                            self._headscale_no_pending(parent, relative.name)
                            try:
                                info = os.stat(relative.name, dir_fd=parent, follow_symlinks=False)
                            except FileNotFoundError:
                                check()
                                return False
                            valid_type = {"file": stat.S_ISREG, "socket": stat.S_ISSOCK,
                                          "directory": stat.S_ISDIR}[kind]
                            if (not valid_type(info.st_mode) or info.st_uid != owner[0]
                                    or (owner[1] is not None and info.st_gid != owner[1])
                                    or stat.S_IMODE(info.st_mode) != mode
                                    or info.st_nlink != (2 if kind == "directory" else 1)):
                                raise InstallerError("live member custody")
                            if kind == "directory":
                                child = os.open(relative.name, _DIR_FLAGS, dir_fd=parent)
                                try:
                                    with os.scandir(child) as entries:
                                        nonempty = next(entries, None) is not None
                                    if (_signature(os.fstat(child)) != _signature(info)
                                            or nonempty):
                                        raise InstallerError("live directory not empty")
                                finally:
                                    os.close(child)
                            elif digest is not None:
                                data = self._read_file(parent, relative.name, owner=owner, mode=mode)
                                if hashlib.sha256(data).hexdigest() != digest:
                                    raise InstallerError("live member digest")
                            check()
                            if _signature(os.stat(relative.name, dir_fd=parent,
                                                              follow_symlinks=False)) != _signature(info):
                                raise InstallerError("live member changed")
                            if remove:
                                require()
                                inventory()
                                if path == "/run/happyranch-tsnet-sidecar/observe.sock":
                                    runtime_diagnostics(parent, allow_observer=True)
                                check()
                                if _signature(os.stat(relative.name, dir_fd=parent,
                                                                  follow_symlinks=False)) != _signature(info):
                                    raise InstallerError("live member changed before removal")
                                partial = True  # An attempted unlink/fsync can have an uncertain outcome.
                                (os.rmdir if kind == "directory" else os.unlink)(relative.name, dir_fd=parent)
                                os.fsync(parent)
                            return True
                    except FileNotFoundError:
                        return False

                def runtime_diagnostics(parent: int, *, allow_observer: bool = False) -> list[tuple[str, tuple[int, ...]]]:
                    # H1: admit the whole bounded set before any unlink.
                    # The existing live-member inventory owns observe.sock.
                    diagnostics = []
                    with os.scandir(parent) as entries:
                        for item in entries:
                            if allow_observer and item.name == "observe.sock":
                                continue
                            info = os.stat(item.name, dir_fd=parent, follow_symlinks=False)
                            if (len(diagnostics) >= _RUNTIME_DIAGNOSTIC_LIMIT
                                    or not _runtime_diagnostic(item.name, info, self._service_owner)):
                                raise InstallerError("custody not empty")
                            diagnostics.append((item.name, _signature(info)))
                    return diagnostics

                def inventory() -> None:
                    # Shallow fixed custody only; never recurse into an unknown entry.
                    for directory in (*headscale.CUSTODY, headscale.HOME_CA,
                                      *(_STATE_DIRS if revoke_succeeded else ())):
                        try:
                            with self._publication_parent(directory) as (parent, check):
                                if directory in _STATE_DIRS and (stat.S_IMODE(os.fstat(parent).st_mode) != 0o700
                                                                or os.fstat(parent).st_nlink != 2):
                                    raise InstallerError("state directory custody")
                                allowed = {Path(path).name for path in members
                                           if str(Path(path).parent) == "/" + directory}
                                with os.scandir(parent) as entries:
                                    for index, item in enumerate(entries):
                                        if index >= 64 or item.name not in allowed:
                                            raise InstallerError("unknown live member")
                                check()
                        except FileNotFoundError:
                            pass
                    for path in members:
                        entry(path)

                require()
                inventory()
                # Existing B5 owns the exact-link exception and post-disable absence proof.
                def disable() -> None:
                    nonlocal partial
                    require()
                    partial = True
                    self.manager.disable(headscale.UNIT)

                gate.disable_owned_link(disable)
                link_disabled = True
                require()
                for path in members:
                    inventory()
                    entry(path, remove=True)
                if any(entry(path) for path in members):
                    raise InstallerError("live member returned")

                # C4/M1 proof is derived here, never accepted from the caller.
                # Existing absence supports a receipt-admitted partial retry.
                def db_proof() -> None:
                    if any(entry(path) for path in LIVE_MEMBERS[:5]):
                        raise InstallerError("Headscale DB retirement unproven")

                def removing() -> None:
                    nonlocal partial
                    partial = True

                def retire(subject: str, effect: Callable[[], None], *, counts: dict | None = None) -> None:
                    require()
                    db_proof()
                    label = "-".join(str(byte) for byte in os.urandom(12))
                    intent = audit.intent(home_audit.Event.TURN_OFF, intent_id="intent-" + label,
                        operation_id="op-" + label, component=home_audit.Component.LIVE_STATE,
                        subject_id=subject, counts=counts)
                    try:
                        require()
                        db_proof()
                        effect()
                        audit.outcome(intent, home_audit.Outcome.SUCCEEDED)
                    except Exception:
                        audit.uncertain(intent)
                        raise

                db_proof()
                # A failed revoke never licenses deleting un-revoked state.
                if not revoke_succeeded:
                    for path in state_paths:
                        relative = Path(path.lstrip("/"))
                        try:
                            with self._publication_parent(str(relative.parent)) as (parent, check):
                                try:
                                    os.stat(relative.name, dir_fd=parent, follow_symlinks=False)
                                except FileNotFoundError:
                                    check()
                                    continue
                                raise InstallerError("un-revoked state retained")
                        except FileNotFoundError:
                            pass

                self._retire_home_minters(proof=db_proof, require=require, retire=retire, removing=removing)

                # B7's bound nonsecret transaction may retire only after the DB.
                transaction = transactions.read_optional()
                if transaction is not None:
                    if not transaction.matches(current):
                        raise InstallerError("transaction binding")
                    def remove_transaction() -> None:
                        if transactions.read_optional() != transaction:
                            raise InstallerError("transaction changed")
                        with self._publication_parent(headscale.INSTALLER_STATE) as (parent, check):
                            check()
                            removing()
                            os.unlink("home-activation-txn.json", dir_fd=parent)
                            os.fsync(parent)
                    retire("subject-activation-evidence", remove_transaction)

                # B11: successful revoke burns all active ceremonies. Only that
                # fact plus DB retirement admits an otherwise uncertain journal.
                issuer_receipt, issuer_present = self._issuer_inventory(cleanup=True,
                    db_retired=revoke_succeeded)
                if issuer_present:
                    with self._publication_parent(HOME_ISSUER) as (parent, check):
                        issuer_members = self._issuer_members(parent, db_retired=revoke_succeeded)
                        check()
                    if issuer_members and not revoke_succeeded:
                        raise InstallerError("issuer ceremony not proven retired")
                    retire("subject-issuer-evidence", lambda: self._cleanup_issuer(db_retired=revoke_succeeded))
                if issuer_receipt is not None:
                    def remove_issuer_receipt() -> None:
                        if self._issuer_receipt() != issuer_receipt:
                            raise InstallerError("issuer receipt changed")
                        with self._publication_parent(headscale.INSTALLER_STATE) as (parent, check):
                            check()
                            removing()
                            os.unlink(_ISSUER_RECEIPT, dir_fd=parent)
                            os.fsync(parent)
                    retire("subject-issuer-custody", remove_issuer_receipt)

                deferred = False
                with self._publication_parent(headscale.INSTALLER_STATE) as (parent, check):
                    with os.scandir(parent) as entries:
                        for index, item in enumerate(entries):
                            if index >= 64:
                                raise InstallerError("cleanup evidence inventory")
                            if item.name not in {Path(ACTIVATION_PATH).name,
                                                 headscale.RECEIPT, headscale.PUBLICATION, "home-ca",
                                                 "home-issuer", _ISSUER_RECEIPT,
                                                 "home-audit.jsonl", "home-audit.jsonl.1"}:
                                deferred = True
                    check()
                try:
                    with self._publication_parent(HOME_ISSUER) as (parent, check):
                        with os.scandir(parent) as entries:
                            deferred = deferred or next(entries, None) is not None
                        check()
                except FileNotFoundError:
                    pass
                try:
                    with self._publication_parent(headscale.HOME_CA) as (parent, check):
                        with os.scandir(parent) as entries:
                            deferred = deferred or next(entries, None) is not None
                        check()
                except FileNotFoundError:
                    pass
                if deferred:
                    raise InstallerError("unlisted custody")
                require()
                # A-E are unchanged: fresh identity, userdel then unshared groupdel.
                user, group = self._headscale_cleanup_identity(receipt)
                if receipt:
                    partial = partial or not user
                    if user:
                        require()
                        code, _ = self._run(["userdel", headscale.ACCOUNT], timeout=30.0)
                        if code:
                            raise InstallerError("userdel uncertain")
                        partial = True
                    require()
                    user, group = self._headscale_cleanup_identity(receipt)
                    if user:
                        raise InstallerError("user remains")
                    if group and receipt["group_created_with_user"]:
                        code, _ = self._run(["groupdel", headscale.ACCOUNT], timeout=30.0)
                        if code:
                            raise InstallerError("groupdel uncertain")
                    if self._headscale_cleanup_identity(receipt) != (False, False):
                        raise InstallerError("identity remains")
                for directory in (*headscale.CUSTODY, headscale.HOME_CA, "run/happyranch-tsnet-sidecar",
                                  *(_STATE_DIRS if revoke_succeeded else ())):
                    require()
                    inventory()
                    relative = Path(directory)
                    try:
                        with self._publication_parent(directory) as (opened, check):
                            if directory == "run/happyranch-tsnet-sidecar":
                                diagnostics = runtime_diagnostics(opened)
                                check()
                                for name, signature in diagnostics:
                                    require()
                                    check()
                                    if _signature(os.stat(name, dir_fd=opened,
                                                                      follow_symlinks=False)) != signature:
                                        raise InstallerError("custody not empty")
                                    partial = True
                                    os.unlink(name, dir_fd=opened)
                                if diagnostics:
                                    os.fsync(opened)
                            with os.scandir(opened) as entries:
                                if next(entries, None) is not None:
                                    raise InstallerError("custody not empty")
                            with self._publication_parent(str(relative.parent)) as (parent, parent_check):
                                named = os.stat(relative.name, dir_fd=parent, follow_symlinks=False)
                                if _signature(named) != _signature(os.fstat(opened)):
                                    raise InstallerError("custody changed")
                                check()
                                parent_check()
                                partial = True
                                os.rmdir(relative.name, dir_fd=parent)
                                os.fsync(parent)
                    except FileNotFoundError:
                        pass
                require()
                self.manager.daemon_reload()
                require()
                inventory()
                if any(entry(path) for path in members):
                    raise InstallerError("live member returned")
                # Publication and activation evidence survive every earlier failure;
                # the account receipt is last, exactly as in the inert owner.
                retire = ([headscale.PUBLICATION] if publication else [])
                retire += [Path(ACTIVATION_PATH).name]
                retire += [headscale.RECEIPT] if receipt else []
                for name in retire:
                    self._headscale_stopped()
                    self._confirm_quiescent()
                    inventory()
                    if any(entry(path) for path in members):
                        raise InstallerError("live member returned before receipt retirement")
                    with self._publication_parent(headscale.INSTALLER_STATE) as (parent, check):
                        if name == headscale.PUBLICATION:
                            if self._headscale_publication(missing_ok=True) != publication:
                                raise InstallerError("publication changed")
                        elif name == Path(ACTIVATION_PATH).name:
                            require()
                        elif self._headscale_receipt() != receipt:
                            raise InstallerError("account receipt changed")
                        self._headscale_no_pending(parent, name)
                        check()
                        partial = True
                        os.unlink(name, dir_fd=parent)
                        os.fsync(parent)
                return {"state": "removed" if receipt else "preserved",
                        "account": "removed" if receipt else "preserved: not created by HappyRanch",
                        "home_ca": "removed"}
        except (OSError, InstallerError, ValueError, UnicodeError, ServiceManagerError,
                headscale.HeadscaleServiceError, home_audit.AuditRefused):
            pass
        finally:
            self._headscale_preparing = False
        raise headscale.HeadscaleServiceError("partial-cleanup-uncertain" if partial else "cleanup-uncertain")

    def cleanup_headscale(
        self, *, expected_release_uid: int | None = None, expected_home_id: str | None = None,
        expected_control_plane_id: str | None = None, expected_connector_id: str | None = None,
        expected_generation: int | None = None, expected_operation_id: str | None = None,
        revoke_succeeded: bool = False,
    ) -> dict[str, str]:
        """Remove only proven custody; explicit trusted expectations select B6.

        Config/DERP need their final publication receipt. DB/key/socket entries
        remain foreign without B6. No CLI/uninstall caller supplies expectations;
        their ruling D behavior, including CA preservation, remains unchanged.
        """
        expected = (expected_release_uid, expected_home_id, expected_control_plane_id,
                    expected_connector_id, expected_generation)
        if type(revoke_succeeded) is not bool:
            raise headscale.HeadscaleServiceError("cleanup-uncertain")
        if any(value is not None for value in (*expected, expected_operation_id)):
            return self._cleanup_home_headscale(*expected, operation_id=expected_operation_id,
                                                revoke_succeeded=revoke_succeeded)
        if revoke_succeeded:
            raise headscale.HeadscaleServiceError("cleanup-uncertain")
        if self.layout != "deb":
            raise headscale.HeadscaleServiceError("deb-layout-required")
        self._headscale_preparing = True
        partial = False
        try:
            issuer_uncertain = False
            try:
                self._issuer_inventory(cleanup=True)
            except (OSError, InstallerError, ValueError, UnicodeError, headscale.HeadscaleServiceError):
                issuer_uncertain = True
            if issuer_uncertain:
                raise headscale.HeadscaleServiceError("issuer-cleanup-uncertain")
            receipt = self._headscale_receipt()
            user, group = self._headscale_cleanup_identity(receipt)
            self._headscale_inventory()
            self._headscale_cleanup_links()
            self._cleanup_issuer()
            present = unit_present = False
            for path in (*headscale.CUSTODY, f"{_UNIT_DIR}/{headscale.UNIT}"):
                try:
                    with self._directory(str(Path(path).parent)) as parent:
                        os.stat(Path(path).name, dir_fd=parent, follow_symlinks=False)
                    present = True
                    if path == f"{_UNIT_DIR}/{headscale.UNIT}":
                        unit_present = True
                except FileNotFoundError:
                    pass
            if receipt is None and not present:
                return {"state": "preserved" if user else "absent",
                        "account": "preserved: not created by HappyRanch" if user else "absent"}
            if self._headscale_owner is None:
                raise headscale.HeadscaleServiceError("cleanup-uncertain")
            # Validate loaded custody BEFORE sending a command to any service.
            props = self.manager.show_properties(headscale.UNIT, ("LoadState", "FragmentPath", "DropInPaths"))
            if (props.get("LoadState") not in {"loaded", "not-found"} or props.get("DropInPaths") != ""
                    or props.get("FragmentPath") != (str(self.root / _UNIT_DIR / headscale.UNIT)
                                                      if props.get("LoadState") == "loaded" else "")):
                raise headscale.HeadscaleServiceError("cleanup-uncertain")
            if props["LoadState"] == "loaded" or unit_present:
                # The exact on-disk fragment must still exist before stop/disable.
                with self._directory(_UNIT_DIR) as parent:
                    if self._read_file(parent, headscale.UNIT, owner=self.root_owner,
                                       mode=0o644) != render_headscale_unit().encode():
                        raise headscale.HeadscaleServiceError("cleanup-uncertain")
                self.manager.stop(headscale.UNIT, timeout=50.0)
                self._headscale_cleanup_links()
                self.manager.disable(headscale.UNIT)
            self._headscale_stopped()
            user, group = self._headscale_cleanup_identity(receipt)
            self._headscale_inventory()
            publication = self._headscale_publication()
            if publication:
                removed: tuple[str, ...] = ()
                partial = True
                names = [Path(path).name for path in publication["files"]]
                if "directories" in publication:
                    names.append(Path(headscale.EMPTY_CA).name)
                for name in names:
                    self._headscale_cleanup_identity(receipt)
                    self._headscale_stopped()
                    self._headscale_inventory(removed)
                    if self._headscale_publication(removed) != publication:
                        raise headscale.HeadscaleServiceError("cleanup-uncertain")
                    self._headscale_remove_member(publication, name)
                    removed += (name,)
                self._headscale_cleanup_identity(receipt)
                self._headscale_stopped()
                self._headscale_inventory(removed)
                if self._headscale_publication(removed) != publication:
                    raise headscale.HeadscaleServiceError("cleanup-uncertain")
                with self._publication_parent(headscale.INSTALLER_STATE) as (parent, revalidate):
                    if self._json(self._read_file(parent, headscale.PUBLICATION,
                                                  owner=self.root_owner, mode=0o600)) != publication:
                        raise headscale.HeadscaleServiceError("cleanup-uncertain")
                    revalidate()
                    os.unlink(headscale.PUBLICATION, dir_fd=parent)
                    os.fsync(parent)
                self._headscale_inventory()
            if receipt:
                partial = partial or not user
                if user:
                    code, _ = self._run(["userdel", headscale.ACCOUNT], timeout=30.0)
                    if code:
                        raise headscale.HeadscaleServiceError("cleanup-uncertain")
                    partial = True
                self._headscale_stopped()
                user, group = self._headscale_cleanup_identity(receipt)
                if user:
                    raise headscale.HeadscaleServiceError("cleanup-uncertain")
                if group and receipt["group_created_with_user"]:
                    code, _ = self._run(["groupdel", headscale.ACCOUNT], timeout=30.0)
                    if code:
                        raise headscale.HeadscaleServiceError("cleanup-uncertain")
                if self._headscale_cleanup_identity(receipt) != (False, False):
                    raise headscale.HeadscaleServiceError("cleanup-uncertain")
            # Fresh identity, service and inventory checks precede each effect.
            for path in headscale.CUSTODY:
                identities = self._headscale_cleanup_identity(receipt)
                if receipt and identities != (False, False):
                    raise headscale.HeadscaleServiceError("cleanup-uncertain")
                self._headscale_stopped()
                self._headscale_inventory()
                try:
                    with self._directory(path) as directory:
                        if path == headscale.CUSTODY[0] and os.listdir(directory):
                            if self._read_file(directory, "policy.hujson", owner=self._headscale_owner,
                                               mode=0o600) != headscale.render_policy():
                                raise headscale.HeadscaleServiceError("cleanup-uncertain")
                            os.unlink("policy.hujson", dir_fd=directory)
                            os.fsync(directory)
                        with self._directory(str(Path(path).parent)) as parent:
                            named = os.stat(Path(path).name, dir_fd=parent, follow_symlinks=False)
                            opened = os.fstat(directory)
                            if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
                                raise headscale.HeadscaleServiceError("cleanup-uncertain")
                            os.rmdir(Path(path).name, dir_fd=parent)
                            os.fsync(parent)
                except FileNotFoundError:
                    pass
            identities = self._headscale_cleanup_identity(receipt)
            if receipt and identities != (False, False):
                raise headscale.HeadscaleServiceError("cleanup-uncertain")
            self._headscale_stopped()
            self._headscale_inventory()
            try:
                with self._directory(_UNIT_DIR) as parent:
                    if self._read_file(parent, headscale.UNIT, owner=self.root_owner,
                                       mode=0o644) != render_headscale_unit().encode():
                        raise headscale.HeadscaleServiceError("cleanup-uncertain")
                    os.unlink(headscale.UNIT, dir_fd=parent)
                    os.fsync(parent)
            except FileNotFoundError:
                pass
            self.manager.daemon_reload()
            self._headscale_stopped()
            self._headscale_inventory()
            for path in (*headscale.CUSTODY, f"{_UNIT_DIR}/{headscale.UNIT}"):
                try:
                    with self._directory(str(Path(path).parent)) as parent:
                        os.stat(Path(path).name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                raise headscale.HeadscaleServiceError("cleanup-uncertain")
            if receipt:
                if (self._headscale_cleanup_identity(receipt) != (False, False)
                        or self._headscale_receipt() != receipt):
                    raise headscale.HeadscaleServiceError("cleanup-uncertain")
                with self._directory(headscale.INSTALLER_STATE) as parent:
                    os.unlink(headscale.RECEIPT, dir_fd=parent)
                    os.fsync(parent)
            return {"state": "removed" if receipt else "preserved",
                    "account": "removed" if receipt else "preserved: not created by HappyRanch",
                    **({"home_ca": "preserved"} if publication else {})}
        except headscale.HeadscaleServiceError as error:
            if error.category.startswith("issuer-"):
                raise
        except (OSError, InstallerError, UnicodeError, ValueError, ServiceManagerError):
            pass
        finally:
            self._headscale_preparing = False
        raise headscale.HeadscaleServiceError("partial-cleanup-uncertain" if partial else "cleanup-uncertain")

    def _headscale_remove_member(self, publication: dict, name: str) -> None:
        directory = name == Path(headscale.EMPTY_CA).name
        path = ("/" + headscale.EMPTY_CA if directory else
                next(path for path in publication["files"] if Path(path).name == name))
        relative = str(Path(path.lstrip("/")).parent)
        with self._publication_parent(relative) as (parent, revalidate):
            if directory:
                self._headscale_empty_ca()
                with self._directory(headscale.EMPTY_CA) as opened:
                    named, info = os.stat(name, dir_fd=parent, follow_symlinks=False), os.fstat(opened)
                    if (named.st_dev, named.st_ino) != (info.st_dev, info.st_ino):
                        raise headscale.HeadscaleServiceError("cleanup-uncertain")
                    revalidate()
                    os.rmdir(name, dir_fd=parent)
            else:
                owner, mode = self._headscale_members("directories" in publication)[path]
                value = self._read_file(parent, name, owner=owner, mode=mode)
                if hashlib.sha256(value).hexdigest() != publication["files"][path]:
                    raise headscale.HeadscaleServiceError("cleanup-uncertain")
                revalidate()
                os.unlink(name, dir_fd=parent)
            os.fsync(parent)


    def _layout(self) -> dict[str, tuple[int, tuple[int, int] | None]]:
        paths = ("opt/happyranch", "opt/happyranch/bin") if self.layout == "source" else ()
        layout: dict[str, tuple[int, tuple[int, int] | None]] = {
            path: (0o755, self.root_owner) for path in (*paths, "etc/happyranch")}
        layout[_CREDSTORE] = (0o700, self.root_owner)
        for base in ("var/lib", "var/log", "run"):
            for name in ("happyranch-connector", "happyranch-tsnet-sidecar"):
                # Include destinations before account creation, so preflight
                # cannot overlook existing service-owned paths.
                mode = 0o700 if base == "var/lib" and name == "happyranch-tsnet-sidecar" else 0o755
                layout[f"{base}/{name}"] = (mode, self._service_owner)
        if self._headscale_preparing:
            for path in ("var", "var/lib"):
                layout[path] = (0o755, self.root_owner)
            layout[headscale.INSTALLER_STATE] = (0o700, self.root_owner)
            layout[headscale.HOME_CA] = (0o700, self.root_owner)
            layout[HOME_ISSUER] = (0o700, self.root_owner)
            for path in (_HOME_MINTER, *(_HOME_MINTER + "/" + user for user in _HOME_MINTER_USERS)):
                layout[path] = (0o700, self.root_owner)
            layout[headscale.EMPTY_CA] = (0o755, self.root_owner)
            for path in headscale.CUSTODY:
                layout[path] = (0o700, self._headscale_owner)
        return layout


    def _read_unit(self, parent: int, name: str) -> bytes:
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(named.st_mode):
            raise InstallerError(f"unsafe unit file type: {name}")
        descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent)
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or (info.st_uid, info.st_gid) != self.root_owner
                    or stat.S_IMODE(info.st_mode) != 0o644 or info.st_size > 65536):
                raise InstallerError(f"unsafe unit file ownership, type, links or mode: {name}")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                data = stream.read(65537)
            if len(data) > 65536:
                raise InstallerError(f"unit file is too large: {name}")
            return data
        finally:
            os.close(descriptor)

    @staticmethod
    @contextmanager
    def _input_parent(path: Path) -> Iterator[int]:
        if path.anchor != "/" or ".." in path.parts or path == Path("/"):
            raise InstallerError("provisioning inputs require absolute literal file paths")
        parent = os.open("/", _DIR_FLAGS)
        opened = [parent]
        parents = []
        try:
            for part in path.parts[1:-1]:
                named = os.stat(part, dir_fd=parent, follow_symlinks=False)
                child = os.open(part, _DIR_FLAGS, dir_fd=parent)
                opened.append(child)
                info = os.fstat(child)
                if (named.st_dev, named.st_ino) != (info.st_dev, info.st_ino):
                    raise InstallerError("input parent changed while opening")
                parents.append((parent, part, child, (info.st_dev, info.st_ino)))
                parent = child
            yield parent
            for ancestor, name, child, identity in parents:
                named, info = os.stat(name, dir_fd=ancestor, follow_symlinks=False), os.fstat(child)
                if (not stat.S_ISDIR(named.st_mode)
                        or (named.st_dev, named.st_ino) != identity
                        or (info.st_dev, info.st_ino) != identity):
                    raise InstallerError("input parent changed during read")
        finally:
            for descriptor in reversed(opened):
                os.close(descriptor)


    def _input(self, path: Path, *, token: bool = False, source_uid: int | None = None) -> bytes:
        if token and (type(source_uid) is not int or not 0 < source_uid < 2**32 - 1):
            raise InstallerError("daemon token source requires the release UID")
        with self._input_parent(path) as parent:
            data = self._read_file(parent, path.name, mode=0o600 if token else None,
                                   source_uid=source_uid)
        if token:
            try:
                if not data.decode("utf-8").strip():
                    raise ValueError
            except (UnicodeError, ValueError):
                raise InstallerError("daemon token source must contain a nonempty UTF8 token") from None
        return data


    def _check_deb_payload(self) -> None:
        if self.layout != "deb":
            return
        # dpkg owns this tree: validate only, never create or chmod it.
        service_owner = self._account(create=False)
        groups = set(os.getgrouplist("happyranch", service_owner[1])) if service_owner else set()

        def require_execute(info: os.stat_result, relative: str) -> None:
            # Fresh-host preflight can precede useradd. Provision and install
            # repeat the payload check after account creation, before publication.
            if service_owner is None:
                return
            uid, gid = service_owner
            if info.st_uid == uid:
                execute = stat.S_IXUSR
            elif info.st_gid == gid or info.st_gid in groups:
                execute = stat.S_IXGRP
            else:
                execute = stat.S_IXOTH
            if not info.st_mode & execute:
                permission = "search" if stat.S_ISDIR(info.st_mode) else "execute"
                raise InstallerError(
                    f"happyranch service account lacks {permission} permission: {self.root / relative}; "
                    "repair or reinstall the package")

        for relative in (f"{_DEB_PREFIX}/bin/happyranch-tsnet-sidecar", f"{_DEB_PREFIX}/python/bin/python3"):
            path = Path(relative)
            for ancestor in reversed(path.parents):
                if ancestor == Path("."):
                    continue
                with self._directory(str(ancestor)) as directory:
                    require_execute(os.fstat(directory), str(ancestor))
            with self._directory(str(path.parent)) as parent:
                name = path.name
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if name == "python3" and stat.S_ISLNK(info.st_mode):
                    if ((info.st_uid, info.st_gid) != self.root_owner
                            or os.readlink(name, dir_fd=parent) != "python3.12"):
                        raise InstallerError("unsafe packaged Python link")
                    name = "python3.12"  # The exact link shipped by build-payload.sh.
                if not stat.S_ISREG(os.stat(name, dir_fd=parent, follow_symlinks=False).st_mode):
                    raise InstallerError("unsafe packaged executable type")
                descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent)
                try:
                    info = os.fstat(descriptor)
                    if (not stat.S_ISREG(info.st_mode) or (info.st_uid, info.st_gid) != self.root_owner
                            or info.st_mode & 0o022 or not info.st_mode & 0o111):
                        raise InstallerError("unsafe packaged executable")
                    require_execute(info, str(path.with_name(name)))
                finally:
                    os.close(descriptor)


    @staticmethod
    def _refuse_provision_entry(parent: int, name: str) -> None:
        for entry in (name, f".{name}.pending"):
            try:
                os.stat(entry, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise InstallerError("provisioning destination or pending entry exists; inspect partial files before retrying")

    def _policy_renewal_record(self, data: bytes) -> dict:
        """Closed, bounded roll-forward intent; never a replacement record."""
        from runtime.remote_access.policy import PolicyEnvelope, RoutePolicyConsumer
        from runtime.remote_access.policy_renew_units import renewal_candidate

        try:
            record = self._json(data)
            if (not 0 < len(data) <= 65536 or record.keys() != {"format", "files"}
                    or record["format"] != "policy-renewal@1"
                    or type(record["files"]) is not dict
                    or record["files"].keys() != {"policy.json", "provision.json"}
                    or data != json.dumps(record, sort_keys=True).encode()):
                raise ValueError
            files = {}
            for name, entry in record["files"].items():
                if (type(entry) is not dict or entry.keys() != {"old_sha256", "new_sha256", "new_base64"}
                        or any(type(entry[key]) is not str or re.fullmatch("[0-9a-f]{64}", entry[key]) is None
                               for key in ("old_sha256", "new_sha256"))
                        or type(entry["new_base64"]) is not str):
                    raise ValueError
                new = base64.b64decode(entry["new_base64"], validate=True)
                if (not 0 < len(new) <= 65536 or base64.b64encode(new).decode() != entry["new_base64"]
                        or hashlib.sha256(new).hexdigest() != entry["new_sha256"]):
                    raise ValueError
                files[name] = {**entry, "new_bytes": new}
            provision = files["provision.json"]["new_bytes"]
            self._validate_install_record("provision.json", provision)
            if self._json(provision)["digests"]["policy.json"] != files["policy.json"]["new_sha256"]:
                raise ValueError
            envelope = PolicyEnvelope.model_validate(self._json(files["policy.json"]["new_bytes"]))
            consumer = RoutePolicyConsumer.from_envelope(envelope)
            if renewal_candidate(envelope, consumer.artifact, now=envelope.issued_at) is None:
                raise ValueError
            return files
        except Exception:
            raise InstallerError("invalid policy renewal journal") from None

    def _check_policy_renewal(self, parent: int, transaction: bytes, *, publishing: bool = False) -> dict[str, bytes]:
        files = self._policy_renewal_record(transaction)
        if publishing:
            try:
                os.stat("install-transaction.json", dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise InstallerError("policy renewal journal already exists")
        elif self._read_file(parent, "install-transaction.json", owner=self.root_owner, mode=0o600) != transaction:
            raise InstallerError("policy renewal journal changed")
        current = {}
        for name, entry in files.items():
            owner, mode = ((self.root_owner, 0o600) if name == "provision.json" else
                           ((self.root_owner[0], self._service_owner[1]), 0o640))
            current[name] = self._read_file(parent, name, owner=owner, mode=mode)
            allowed = (entry["old_sha256"],) if publishing else (entry["old_sha256"], entry["new_sha256"])
            if hashlib.sha256(current[name]).hexdigest() not in allowed:
                raise InstallerError("policy renewal destination drift")
        return current

    def _policy_renewal_pending(self, parent: int, files: dict | None = None) -> dict[str, tuple[int, ...]]:
        """Only exact journal-bound destination bytes can belong to renewal."""
        expected = {f".{name}.pending": name for name in files} if files is not None else {}
        pending = {}
        fields = ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
        for entry in os.listdir(parent):
            if not entry.endswith(".pending"):
                continue
            if entry not in expected:
                raise InstallerError("unknown policy renewal pending entry")
            name = expected[entry]
            owner, mode = ((self.root_owner, 0o600) if name == "provision.json" else
                           ((self.root_owner[0], self._service_owner[1]), 0o640))
            before = os.stat(entry, dir_fd=parent, follow_symlinks=False)
            if self._read_file(parent, entry, owner=owner, mode=mode) != files[name]["new_bytes"]:
                raise InstallerError("policy renewal pending bytes differ")
            signature = tuple(getattr(before, field) for field in fields)
            after = os.stat(entry, dir_fd=parent, follow_symlinks=False)
            if signature != tuple(getattr(after, field) for field in fields):
                raise InstallerError("policy renewal pending changed")
            pending[name] = signature
        return pending

    def _recover_policy_renewal(self, transaction: bytes) -> None:
        """Same lock/owner: validate BOTH destinations before any roll-forward."""
        files = self._policy_renewal_record(transaction)
        with self._publication_parent() as (parent, check):
            self._headscale_no_pending(parent, "install-transaction.json")
            self._check_policy_renewal(parent, transaction)
            self._policy_renewal_pending(parent, files)
            check()
        for name in ("policy.json", "provision.json"):
            with self._publication_parent() as (parent, check):
                current = self._check_policy_renewal(parent, transaction)
                pending = self._policy_renewal_pending(parent, files)
                check()
                if name in pending:
                    # Discard only this validated inode, then republish through
                    # the protected writer. A crash after discard still has
                    # the durable journal and permitted old/new destinations.
                    entry = f".{name}.pending"
                    info = os.stat(entry, dir_fd=parent, follow_symlinks=False)
                    fields = ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
                    if pending[name] != tuple(getattr(info, field) for field in fields):
                        raise InstallerError("policy renewal pending changed before discard")
                    os.unlink(entry, dir_fd=parent)
                    os.fsync(parent)
            if current[name] != files[name]["new_bytes"]:
                owner, mode = ((self.root_owner, 0o600) if name == "provision.json" else
                               ((self.root_owner[0], self._service_owner[1]), 0o640))
                self._write_provision_file(name, files[name]["new_bytes"], owner, mode,
                                           policy_renewal=transaction)
        with self._publication_parent() as (parent, check):
            before = os.stat("install-transaction.json", dir_fd=parent, follow_symlinks=False)
            current = self._check_policy_renewal(parent, transaction)
            self._policy_renewal_pending(parent)
            if any(current[name] != files[name]["new_bytes"] for name in files):
                raise InstallerError("policy renewal incomplete")
            check()
            after = os.stat("install-transaction.json", dir_fd=parent, follow_symlinks=False)
            fields = ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
            if any(getattr(before, field) != getattr(after, field) for field in fields):
                raise InstallerError("policy renewal journal changed")
            os.unlink("install-transaction.json", dir_fd=parent)
            os.fsync(parent)

    def renew_policy(self, *, fresh_auth: bool = False,
                     _before_renew: Callable[[], None] | None = None) -> None:
        """Fixed artifact only. Fresh-auth callers supply authorization separately."""
        from runtime.remote_access.installer_lock import installer_state_lock

        if os.geteuid() != 0 or type(fresh_auth) is not bool:
            raise InstallerError("policy renewal unavailable")
        try:
            with installer_state_lock(self):
                if _before_renew is not None:
                    _before_renew()  # Private Retry revalidates its live capture under this SAME flock.
                self._renew_policy_locked(fresh_auth=fresh_auth)
        except headscale.HeadscaleServiceError:
            raise InstallerError("policy renewal unavailable") from None

    def _renew_policy_locked(self, *, fresh_auth: bool) -> None:
        from runtime.remote_access.policy import PolicyEnvelope
        from runtime.remote_access.policy_renew_units import fresh_auth_renewal_candidate, renewal_candidate

        self._service_owner = self._account(create=False)
        if self._service_owner is None:
            raise InstallerError("policy renewal service account unavailable")
        with self._publication_parent() as (parent, check):
            self._headscale_no_pending(parent, "install-transaction.json")
            try:
                transaction = self._read_file(parent, "install-transaction.json", owner=self.root_owner, mode=0o600)
            except FileNotFoundError:
                transaction = None
            check()
        if transaction is not None:
            self._recover_policy_renewal(transaction)
            return
        with self._publication_parent() as (parent, check):
            self._policy_renewal_pending(parent)
            for name in ("policy.json", "provision.json"):
                self._headscale_no_pending(parent, name)
            old_policy = self._read_file(parent, "policy.json", owner=(self.root_owner[0], self._service_owner[1]), mode=0o640)
            old_provision = self._read_file(parent, "provision.json", owner=self.root_owner, mode=0o600)
            check()
        record = self._json(old_provision)
        self._validate_install_record("provision.json", old_provision)
        if record.get("digests", {}).get("policy.json") != hashlib.sha256(old_policy).hexdigest():
            raise InstallerError("policy renewal provision drift")
        source = Path(__file__).with_name("route_policy.json")
        with self._input_parent(source) as parent:
            shipped = self._json(self._read_file(parent, source.name, owner=self.root_owner))
        installed = self._json(old_policy)
        candidate = (fresh_auth_renewal_candidate if fresh_auth else renewal_candidate)(
            PolicyEnvelope.model_validate(installed), shipped, now=datetime.now(timezone.utc))
        if candidate is None:
            raise InstallerError("policy renewal refused")
        installed.update(issued_at=candidate.issued_at.isoformat(), revision=candidate.revision,
                         max_age_seconds=candidate.max_age_seconds)
        policy = json.dumps(installed, sort_keys=True).encode()
        record["digests"]["policy.json"] = hashlib.sha256(policy).hexdigest()
        provision = json.dumps(record, sort_keys=True).encode()
        transaction = json.dumps(dict(format="policy-renewal@1", files={
            name: dict(old_sha256=hashlib.sha256(old).hexdigest(), new_sha256=hashlib.sha256(new).hexdigest(),
                       new_base64=base64.b64encode(new).decode())
            for name, old, new in (("policy.json", old_policy, policy), ("provision.json", old_provision, provision))
        }), sort_keys=True).encode()
        self._write_provision_file("install-transaction.json", transaction, self.root_owner, 0o600,
                                   policy_renewal=transaction)
        self._recover_policy_renewal(transaction)

    def _write_provision_file(self, name: str, data: bytes, owner: tuple[int, int], mode: int,
                              *, relative: str = "etc/happyranch",
                              policy_renewal: bytes | None = None) -> None:
        # Fresh files never overwrite. Only real non-home policy renewal can
        # update its exact protected old/new publication pair.
        metadata = {
            **{key: ((self.root_owner[0], self._service_owner[1]), 0o640)
               for key in ("connector.json", "sidecar.json", "policy.json")
               if self._service_owner is not None},
            "release-daemon.json": (self.root_owner, 0o644),
            "daemon.token": (self._service_owner, 0o600),
            "provision.json": (self.root_owner, 0o600),
            "install-transaction.json": (self.root_owner, 0o600),
        }
        if (relative != "etc/happyranch" or name not in metadata or metadata[name] != (owner, mode)
                or owner is None or not 0 < len(data) <= 65536):
            raise InstallerError("invalid provisioning publication")
        renewal_files = self._policy_renewal_record(policy_renewal) if policy_renewal is not None else None
        if renewal_files is not None:
            if (relative != "etc/happyranch"
                    or (data != policy_renewal if name == "install-transaction.json" else
                        name not in renewal_files or data != renewal_files[name]["new_bytes"])):
                raise InstallerError("invalid policy renewal publication")
        updating = renewal_files is not None and name in renewal_files
        if renewal_files is None and name in ("provision.json", "install-transaction.json"):
            self._validate_install_record(name, data)
        with self._publication_parent(relative) as (parent, revalidate):
            if renewal_files is not None:
                self._check_policy_renewal(parent, policy_renewal, publishing=name == "install-transaction.json")
                if not updating:
                    self._refuse_provision_entry(parent, name)
                self._headscale_no_pending(parent, name)
            else:
                self._refuse_provision_entry(parent, name)
            pending = f".{name}.pending"
            descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                 0o600, dir_fd=parent)
            created = os.fstat(descriptor)
            try:
                os.fchown(descriptor, *owner)
                with os.fdopen(descriptor, "wb", closefd=False) as stream:
                    if stream.write(data) != len(data):
                        raise InstallerError("incomplete provisioning write")
                    stream.flush()
                    os.fchmod(descriptor, mode)
                    os.fsync(descriptor)
                info = os.fstat(descriptor)
                named = os.stat(pending, dir_fd=parent, follow_symlinks=False)
                expected = (created.st_dev, created.st_ino, *owner, stat.S_IFREG | mode, 1, len(data))
                for value in (info, named):
                    if ((value.st_dev, value.st_ino, value.st_uid, value.st_gid,
                         value.st_mode, value.st_nlink, value.st_size) != expected):
                        raise InstallerError("unsafe or changed pending provisioning file")
                revalidate()
                if renewal_files is not None:
                    self._check_policy_renewal(parent, policy_renewal, publishing=name == "install-transaction.json")
                    revalidate()
                if not updating:
                    # Fresh publication must never overwrite a late arrival.
                    try:
                        os.stat(name, dir_fd=parent, follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    else:
                        raise InstallerError("provisioning destination appeared before rename")
                self._rename_provision_entry(parent, pending, name, updating=updating)
                os.fsync(parent)
            finally:
                os.close(descriptor)
                # Only our exclusive inode may be removed. A substituted pending
                # entry belongs to the unknown inventory, never automatic cleanup.
                try:
                    named = os.stat(pending, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    if (named.st_dev, named.st_ino) == (created.st_dev, created.st_ino):
                        os.unlink(pending, dir_fd=parent)
                        os.fsync(parent)


    def _validate_install_record(self, name: str, data: bytes) -> None:
        """Destination-bound ordinary provenance; legacy journals never grant recovery."""
        record = self._json(data)
        if name == "provision.json":
            self._validate_provision_record(data)
            return
        keys = {"connector.json", "sidecar.json", "policy.json", "release-daemon.json", "daemon.token"}
        phases = {"provisioned"}
        if name == "install-transaction.json":
            keys.add("provision.json")
            phases = {"prepared"}
        digests = record.get("digests")
        if (record.keys() != {"version", "layout", "digests", "phase"}
                or type(record["version"]) is not int or record["version"] != 1
                or record["layout"] not in ("source", "deb") or record["layout"] != self.layout
                or not isinstance(record["phase"], str) or record["phase"] not in phases
                or not isinstance(digests, dict) or digests.keys() != keys
                or any(not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
                       for value in digests.values())):
            raise InstallerError("invalid versioned installation record")

    def _refuse_install_transaction(self) -> None:
        """Any transaction inventory is inert until its recovery owner ships."""
        try:
            with self._directory("etc/happyranch") as parent:
                self._refuse_provision_entry(parent, "install-transaction.json")
        except FileNotFoundError:
            pass

    def _release_record(self, user_name: str, data_home: Path | None) -> dict:
        from runtime.remote_access.release_verifier import FRAGMENT, UNIT, validate_record

        try:
            user = self._lookup_user(user_name)
            home = data_home if data_home is not None else Path(user.pw_dir) / '.local/share/happyranch'
            if home.anchor != '/':
                raise InstallerError(f'release data home {home} must use a canonical absolute path')
            record = dict(user=user_name, uid=user.pw_uid, data_home=str(home), unit=UNIT, fragment_path=FRAGMENT)
            with self._input_parent(home / 'daemon.port') as directory:
                info = os.fstat(directory)
                # No-follow traversal above rejects symlinked parents. Compare
                # that literal path without resolving through a new alias.
                for directive, hidden in VERIFIER_FILESYSTEM_HARDENING:
                    if any(home.is_relative_to(path) for path in hidden):
                        raise InstallerError(f'release data home {home} is hidden or inaccessible '
                                             f'in the verifier namespace by {directive}')
                if info.st_uid != user.pw_uid or info.st_mode & 0o022:
                    raise InstallerError('release data home must be owned by the target uid without group/other writes')
            validate_record(record)
            return record
        except (OSError, ValueError, KeyError):
            raise InstallerError('invalid release user or data home (require existing absolute no-symlink target-owned directory)') from None

    def _check_release_record(self) -> dict:
        from runtime.remote_access.release_verifier import validate_record

        with self._directory('etc/happyranch') as parent:
            record = self._json(self._read_file(parent, 'release-daemon.json', owner=self.root_owner, mode=0o644))
        try:
            validate_record(record)
            if record != self._release_record(record['user'], Path(record['data_home'])):
                raise InstallerError('release user identity changed')
        except (OSError, ValueError, KeyError, TypeError):
            raise InstallerError('invalid release install record') from None
        return record


    def provision(self, *, connector_config: Path | None = None, sidecar_config: Path | None = None,
                  policy_file: Path | None = None,
                  release_user: str, data_home: Path | None = None,
                  daemon_token_source: Path | None = None) -> None:
        """Stage explicit non-home configs; generated home provisioning is retired.

        The operator CLI retains its original three required inputs. Service
        directories remain supplied by systemd, with existing custody checks.
        """
        if connector_config is None and sidecar_config is None and policy_file is None:
            from runtime.remote_access.home_retirement import MESSAGE
            raise InstallerError(MESSAGE)
        try:
            self._refuse_install_transaction()
            if any(path is None for path in (connector_config, sidecar_config, policy_file)):
                raise InstallerError("provide all three config inputs")
            release = self._release_record(release_user, data_home)
            daemon_token_source = daemon_token_source or Path(release['data_home']) / 'daemon.token'
            files = {name: self._input(path) for name, path in (
                ("connector.json", connector_config), ("sidecar.json", sidecar_config), ("policy.json", policy_file))}
            self._validate_configs(files)
            connector = self._json(files["connector.json"])
            sidecar = self._json(files["sidecar.json"])
            excluded = sorted({8765,
                               *connector.get("excluded_daemon_ports", []),
                               *sidecar.get("ExcludedDaemonPorts", [])})
            connector.pop("daemon_port", None)
            connector["excluded_daemon_ports"] = excluded
            sidecar["ExcludedDaemonPorts"] = excluded
            for name, value in (("connector.json", connector), ("sidecar.json", sidecar)):
                files[name] = json.dumps(value, sort_keys=True).encode()
            self._validate_configs(files, staged=True)
            if any(not 0 < len(data) <= 65536 for data in files.values()):
                raise InstallerError("rendered provisioning config exceeds publication bound")
            token = self._input(daemon_token_source, token=True, source_uid=release['uid'])
            self._check_deb_payload()
            self._service_owner = self._account(create=False)
            self._preflight()
            try:
                with self._directory("etc/happyranch") as parent:
                    for name in (*files, "daemon.token", "provision.json", "release-daemon.json"):
                        self._refuse_provision_entry(parent, name)
            except FileNotFoundError:
                pass
            self._service_owner = self._account(create=True)
            self._preflight()
            self._check_deb_payload()
            for path in self._layout():
                if path in {f"{base}/{name}" for base in ("var/lib", "var/log", "run")
                            for name in ("happyranch-connector", "happyranch-tsnet-sidecar")}:
                    continue
                with self._directory(path, create=True):
                    pass
            for name, data in files.items():
                self._write_provision_file(name, data, (self.root_owner[0], self._service_owner[1]), 0o640)
            self._write_provision_file("release-daemon.json", json.dumps(release, sort_keys=True).encode(), self.root_owner, 0o644)
            self._write_provision_file("daemon.token", token, self._service_owner, 0o600)
            record = dict(layout=self.layout, token_source=str(daemon_token_source),
                          token_sha256=hashlib.sha256(token).hexdigest(),
                          configs={name: hashlib.sha256(data).hexdigest() for name, data in files.items()})
            self._write_provision_file("provision.json", json.dumps(record, sort_keys=True).encode(), self.root_owner, 0o600)
            self._check_provisioning()
        except OSError:
            raise InstallerError("provisioning failed; inspect partial files before retrying") from None

    def _check_provisioning(self) -> None:
        try:
            with self._directory("etc/happyranch") as parent:
                sidecar = self._json(self._read_file(parent, "sidecar.json"))
            if "home_binding" in sidecar:
                from runtime.remote_access.home_admission import HomeProfileAdmission
                HomeProfileAdmission(self).require()
                return
            release = self._check_release_record()
            with self._directory("etc/happyranch") as parent:
                record = self._json(self._read_file(parent, "provision.json", owner=self.root_owner, mode=0o600))
                if "version" in record:
                    self._validate_install_record("provision.json", json.dumps(record).encode())
                    if self._service_owner is None:
                        raise InstallerError("provisioned service account is missing")
                    files = {name: self._read_file(parent, name, owner=(self.root_owner[0], self._service_owner[1]), mode=0o640)
                             for name in ("connector.json", "sidecar.json", "policy.json")}
                    self._validate_configs(files, staged=True)
                    if self._json(files["connector.json"])["managed"].get("profile") != "linux-managed-identity-v1":
                        raise InstallerError("versioned provisioning requires the identity profile")
                    files["release-daemon.json"] = self._read_file(parent, "release-daemon.json", owner=self.root_owner, mode=0o644)
                    files["daemon.token"] = self._read_file(parent, "daemon.token", owner=self._service_owner, mode=0o600)
                    if record["digests"] != {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}:
                        raise InstallerError("provisioned configuration drift")
                    source = self._input(Path(release['data_home']) / "daemon.token", token=True, source_uid=release['uid'])
                    if hashlib.sha256(source).hexdigest() != record["digests"]["daemon.token"]:
                        raise InstallerError("daemon token drift; source rotation requires explicit reprovisioning")
                    return
                if record.keys() != {"layout", "token_source", "token_sha256", "configs"} or record["layout"] != self.layout:
                    raise InstallerError("provisioning layout mismatch or invalid record")
                if self._service_owner is None:
                    raise InstallerError("provisioned service account is missing")
                files = {name: self._read_file(parent, name, owner=(self.root_owner[0], self._service_owner[1]), mode=0o640)
                         for name in ("connector.json", "sidecar.json", "policy.json")}
                if record["configs"] != {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}:
                    raise InstallerError("provisioned configuration drift")
                self._validate_configs(files, staged=True)
                staged = self._read_file(parent, "daemon.token", owner=self._service_owner, mode=0o600)
            source = self._input(Path(record["token_source"]), token=True, source_uid=release['uid'])
            if (hashlib.sha256(source).hexdigest() != record["token_sha256"]
                    or hashlib.sha256(staged).hexdigest() != record["token_sha256"]):
                raise InstallerError("daemon token drift; source rotation requires explicit reprovisioning")
        except (OSError, TypeError, ValueError):
            raise InstallerError("missing, unsafe or invalid provisioning files or token source") from None

    def _has_provisioning(self) -> bool:
        try:
            with self._directory("etc/happyranch") as parent:
                os.stat("provision.json", dir_fd=parent, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False

    def _preflight(self) -> None:
        # Check every existing destination before account creation or systemctl.
        # This is not transactional recovery or a concurrent-root-writer fence.
        self._refuse_install_transaction()
        for path in (*self._layout(), _UNIT_DIR):
            try:
                with self._directory(path):
                    pass
            except FileNotFoundError:
                pass
        self._check_enrollment()
        try:
            with self._directory(_UNIT_DIR) as parent:
                for name, text in self.units.items():
                    try:
                        data = self._read_unit(parent, name)
                    except FileNotFoundError:
                        continue
                    if data != text.encode():
                        raise InstallerError(f"unit already exists with different content: {name}; inspect it before reinstalling")
        except FileNotFoundError:
            pass

    def _refuse_staged_identity(self) -> object | None:
        for name in ("connector.json", "sidecar.json"):
            try:
                with self._directory("etc/happyranch") as parent:
                    # This read can only deny service admission. Keep legacy
                    # source installs' config modes; provisioned files still
                    # undergo their exact ownership/mode checks separately.
                    raw = self._json(self._read_file(parent, name))
            except FileNotFoundError:
                continue
            managed = raw.get("managed")
            profile = managed.get("profile") if isinstance(managed, dict) else None
            if profile == "linux-managed-identity-v1" or raw.get("Profile") == "linux-managed-identity-v1":
                from runtime.remote_access.home_retirement import MESSAGE
                raise InstallerError(MESSAGE)

    def _write_units(self) -> None:
        if self._refuse_staged_identity() is not None:
            raise InstallerError("home unit publication requires the activation owner")
        with self._directory(_UNIT_DIR, create=True) as parent:
            for name, text in self.units.items():
                try:
                    descriptor = os.open(
                        name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                        0o600, dir_fd=parent,
                    )
                except FileExistsError:
                    if self._read_unit(parent, name) != text.encode():
                        raise InstallerError(f"unit changed during installation: {name}")
                    continue
                try:
                    os.fchown(descriptor, *self.root_owner)
                    with os.fdopen(descriptor, "wb", closefd=False) as stream:
                        stream.write(text.encode())
                        stream.flush()
                        os.fchmod(descriptor, 0o644)
                        os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            os.fsync(parent)

    @contextmanager
    def _home_preparation_custody(self) -> Iterator[None]:
        """Select the existing exact Headscale custody layout for inert reads."""
        preparing = self._headscale_preparing
        self._headscale_preparing = True
        try:
            yield
        finally:
            self._headscale_preparing = preparing

    def _home_unit_provisioning(self, parent: int, record: dict, *, reuse: bool) -> dict[str, bytes]:
        """Reverify the complete original generation, even after Disable.

        Live sidecar publication appends fixed fields without changing prepared
        bytes. Reuse may verify those original bytes against provenance; it never
        republishes or admits the retained live configuration.
        """
        files = {}
        for name, (owner, mode) in {
            **{name: ((self.root_owner[0], self._service_owner[1]), 0o640)
               for name in ("connector.json", "sidecar.json", "policy.json")},
            "release-daemon.json": (self.root_owner, 0o644),
            "daemon.token": (self._service_owner, 0o600),
        }.items():
            self._headscale_no_pending(parent, name)
            files[name] = self._read_file(parent, name, owner=owner, mode=mode)
        sidecar = self._json(files["sidecar.json"])
        if reuse and "home_binding" in sidecar:
            from runtime.remote_access.legacy_home_receipt import CONTROL_URL
            from runtime.remote_access.invite_v2 import _canonical_dial_ip
            connector = self._json(files["connector.json"])
            binding = sidecar["home_binding"]
            tls = sidecar.get("control_tls")
            if (not isinstance(binding, dict) or set(binding) != {
                    "home_id", "control_plane_id", "connector_id", "activation_generation"}
                    or binding["home_id"] != connector["home_id"]
                    or binding["connector_id"] != connector["connector_id"]
                    or binding["control_plane_id"] != connector["managed"]["control_plane_id"]
                    or type(binding["activation_generation"]) is not int or binding["activation_generation"] < 0
                    or sidecar.get("ControlURL") != CONTROL_URL or type(tls) is not dict
                    or set(tls) != {"dial_ip", "ca_sha256"}
                    or type(tls["dial_ip"]) is not str or not _canonical_dial_ip(tls["dial_ip"])
                    or type(tls["ca_sha256"]) is not str or re.fullmatch("[0-9a-f]{64}", tls["ca_sha256"]) is None):
                raise InstallerError("retained home configuration binding")
            addition = b"," + json.dumps({key: sidecar[key] for key in (
                "ControlURL", "control_tls", "home_binding")}, separators=(",", ":"), ensure_ascii=True).encode()[1:-1]
            end = files["sidecar.json"].rindex(b"}")
            if not files["sidecar.json"][:end].endswith(addition):
                raise InstallerError("retained home configuration shape")
            files["sidecar.json"] = files["sidecar.json"][:end - len(addition)] + files["sidecar.json"][end:]
        if record["digests"] != {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}:
            raise InstallerError("home publication generation drift")
        self._validate_configs({name: files[name] for name in ("connector.json", "sidecar.json", "policy.json")},
                               staged=True, require_current=not reuse)
        release = self._check_release_record()
        source = self._input(Path(release["data_home"]) / "daemon.token", token=True, source_uid=release["uid"])
        if source != files["daemon.token"]:
            raise InstallerError("home publication token drift")
        return files

    def _home_staging_residue(self, *, clean: bool = False) -> None:
        """Existing fixed inventories must prove no live or pending authority."""
        with self._home_preparation_custody():
            from runtime.remote_access.legacy_home_readback import LegacyHomeReadback, LegacyHomeTransactions
            from runtime.remote_access.home_audit import AuditRefused, HomeAudit

            store = LegacyHomeReadback(root=self.root, root_owner=self.root_owner)
            if (store.current() is not None or LegacyHomeTransactions(
                    root=self.root, root_owner=self.root_owner).read_optional() is not None):
                raise InstallerError("retained activation evidence")
            try:
                with self._publication_parent() as (parent, check):
                    self._refuse_provision_entry(parent, "invite-issuer.json")
                    check()
            except FileNotFoundError:
                with self._publication_parent("etc") as (parent, check):
                    self._refuse_provision_entry(parent, "happyranch")
                    check()
            self._refuse_install_transaction()
            self._service_owner = self._account(create=False)
            self._headscale_owner = self._headscale_shape()
            headscale_receipt = self._headscale_receipt()
            if headscale_receipt and self._headscale_owner != (headscale_receipt["uid"], headscale_receipt["gid"]):
                raise InstallerError("retained Headscale identity")
            self._headscale_inventory()
            if clean:
                try:
                    with self._publication_parent(_UNIT_DIR) as (parent, check):
                        self._refuse_provision_entry(parent, headscale.UNIT)
                        check()
                except FileNotFoundError:
                    pass
            self._headscale_ca_sources()
            self._headscale_stopped()
            self._confirm_quiescent()
            self._issuer_inventory()
            try:
                with self._publication_parent(HOME_ISSUER) as (parent, check):
                    if self._issuer_members(parent):
                        raise InstallerError("retained issuer evidence")
                    check()
            except FileNotFoundError:
                pass
            with self._publication_parent(headscale.INSTALLER_STATE) as (parent, check):
                # Same fixed installer evidence names B6 already inventories. All
                # other names (including minter/B7/pending evidence) veto admission.
                with os.scandir(parent) as entries:
                    for index, item in enumerate(entries):
                        if index >= 64 or item.name not in {
                                headscale.RECEIPT, headscale.PUBLICATION, "home-ca", "home-issuer",
                                _ISSUER_RECEIPT, "home-audit.jsonl", "home-audit.jsonl.1"}:
                            raise InstallerError("retained lifecycle residue")
                        if clean and item.name not in {"home-audit.jsonl", "home-audit.jsonl.1"}:
                            raise InstallerError("clean installation has Headscale residue")
                        if item.name in {"home-audit.jsonl", "home-audit.jsonl.1"}:
                            try:
                                HomeAudit(uid=self.root_owner[0], gid=self.root_owner[1])._file_info(
                                    os.stat(item.name, dir_fd=parent, follow_symlinks=False))
                            except AuditRefused:
                                raise InstallerError("retained audit custody") from None
                check()
            for relative in (*_STATE_DIRS, "run/happyranch-connector", "run/happyranch-tsnet-sidecar",
                             "run/happyranch-release-verifier"):
                if self._inventory_files(relative, {}):
                    raise InstallerError("retained live state")
            self._check_enrollment()
            for relative, name in (("etc/happyranch", "enrollment.key"), (_CREDSTORE, _CREDENTIAL_NAME)):
                try:
                    with self._publication_parent(relative) as (parent, check):
                        self._refuse_provision_entry(parent, name)
                        check()
                except FileNotFoundError:
                    pass

    def _home_staged_inventory(self, *, post_disable: bool = True) -> InstallInventory:
        """Prove complete staging or PD1-PD5 without adopting/rewriting bytes.

        Inert Headscale preparation is allowed between preparation and initial
        invite publication. Only the retained PD shape needs all five fragments.
        """
        self._home_staging_residue()
        if self._service_owner is None:
            raise InstallerError("staged service identity")
        with self._home_preparation_custody(), self._publication_parent() as (parent, check):
            self._inventory_files("etc/happyranch", {
                **{name: ((self.root_owner[0], self._service_owner[1]), 0o640, False)
                   for name in ("connector.json", "sidecar.json", "policy.json")},
                "release-daemon.json": (self.root_owner, 0o644, False),
                "daemon.token": (self._service_owner, 0o600, False),
                "provision.json": (self.root_owner, 0o600, False),
                "control-ca.pem": ((self.root_owner[0], self._service_owner[1]), 0o640, False),
            }, directories={"headscale", "empty-ca-directory"})
            record = self._read_file(parent, "provision.json", owner=self.root_owner, mode=0o600)
            self._validate_install_record("provision.json", record)
            provenance = self._json(record)
            if provenance.get("version") != 1:
                raise InstallerError("retained provenance")
            files = self._home_unit_provisioning(parent, provenance, reuse=post_disable)
            live = self._json(self._read_file(parent, "sidecar.json",
                owner=(self.root_owner[0], self._service_owner[1]), mode=0o640))
            if ("home_binding" in live) != post_disable:
                raise InstallerError("staged binding mismatch")
            connector = self._json(files["connector.json"])
            units = managed_units(self.layout, connector_config=connector)
            retained = dict(units)
            prefix = "happyranch-desktop" if self.layout == "deb" else "happyranch"
            retained[UNITS[1]] = render_sidecar_unit(SidecarUnitSpec(
                binary_path=f"/opt/{prefix}/bin/happyranch-tsnet-sidecar", home_transport=True))
            count = len(self._home_unit_inventory(units, retained))
            if count not in ({len(units)} if post_disable else {0, len(units)}):
                raise InstallerError("retained unit generation incomplete")
            if any(self.manager.show_properties(name, ("ActiveState",)).get("ActiveState") != "inactive"
                   for name in units):
                raise InstallerError("staged units are not inactive")
            check()
        return InstallInventory("modern", "post-disable" if post_disable else "staged-inert", tuple(sorted(
            str(self.root / path) for path, value in self._replacement_snapshot().items() if value != "absent")))

    def _home_unit_inventory(self, units: dict[str, str], retained: dict[str, str]) -> dict[str, bytes]:
        """Bounded no-follow load-root scan; no aliases or merged overrides."""
        present: dict[str, bytes] = {}
        forbidden = {"." + name + ".pending" for name in units}
        for name in units:
            stem, suffix = name.rsplit(".", 1)
            forbidden.update({name + ".d", suffix + ".d"})
            while "-" in stem:
                stem = stem.rsplit("-", 1)[0]
                forbidden.add(stem + "-." + suffix + ".d")
        count = 0

        def scan(parent: int, relative: str, depth: int) -> None:
            nonlocal count
            if depth > 32:
                raise InstallerError("home unit inventory bound")
            with os.scandir(parent) as entries:
                for entry in entries:
                    count += 1
                    if count > 65536 or entry.name in forbidden:
                        raise InstallerError("home unit override or pending residue")
                    info = os.stat(entry.name, dir_fd=parent, follow_symlinks=False)
                    if entry.name in units:
                        if relative != _UNIT_DIR:
                            raise InstallerError("home unit override or alias")
                        data = self._read_unit(parent, entry.name)
                        if data not in (units[entry.name].encode(), retained[entry.name].encode()):
                            raise InstallerError("differing home unit")
                        present[entry.name] = data
                    elif stat.S_ISLNK(info.st_mode):
                        if Path(os.readlink(entry.name, dir_fd=parent)).name in units:
                            raise InstallerError("home unit alias")
                    elif stat.S_ISDIR(info.st_mode):
                        with self._publication_parent(relative + "/" + entry.name) as (child, check):
                            scan(child, relative + "/" + entry.name, depth + 1)
                            check()

        roots = list(_HEADSCALE_UNIT_DIRS)
        with self._directory() as parent:
            try:
                info = os.stat("lib", dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                if stat.S_ISLNK(info.st_mode):
                    if os.readlink("lib", dir_fd=parent) not in {"usr/lib", "/usr/lib"}:
                        raise InstallerError("home unit load-root alias")
                else:
                    roots.append("lib/systemd/system")
        for relative in roots:
            opened = False
            try:
                with self._publication_parent(relative) as (parent, check):
                    opened = True
                    scan(parent, relative, 0)
                    check()
            except FileNotFoundError:
                if opened:
                    raise InstallerError("home unit inventory changed") from None
        for name in units:
            props = self.manager.show_properties(name, ("FragmentPath", "DropInPaths", "LoadState", "Names"))
            location = (props.get("FragmentPath"), props.get("LoadState"))
            allowed = {("", "not-found")}
            if name in present:
                allowed.add((str(self.root / _UNIT_DIR / name), "loaded"))
            if (location not in allowed or props.get("DropInPaths") != "" or props.get("Names") != name):
                raise InstallerError("home unit load inventory failed")
        return present

    def _key_info(self, info: os.stat_result) -> None:
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or (info.st_uid, info.st_gid) != self.root_owner
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 65536):
            raise InstallerError("enrollment key must be a root-owned single-link regular 0600 file (at most 64 KiB)")

    def _read_enrollment(self, path: Path) -> bytearray:
        if not path.is_absolute() or ".." in path.parts:
            raise InstallerError("--enrollment-key-file requires an absolute literal file path")
        parent = os.open("/", _DIR_FLAGS)
        try:
            for part in path.parts[1:-1]:
                child = os.open(part, _DIR_FLAGS, dir_fd=parent)
                os.close(parent)
                parent = child
            named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            self._key_info(named)
            descriptor = os.open(path.name, _FILE_FLAGS, dir_fd=parent)
            try:
                info = os.fstat(descriptor)
                self._key_info(info)
                if (named.st_dev, named.st_ino) != (info.st_dev, info.st_ino):
                    raise InstallerError("enrollment key changed while opening")
                with os.fdopen(descriptor, "rb", closefd=False) as stream:
                    data = bytearray(stream.read(65537))
                if not data.strip() or len(data) > 65536:
                    raise InstallerError("enrollment key must be nonempty and at most 64 KiB")
                return data
            finally:
                os.close(descriptor)
        finally:
            os.close(parent)

    def _check_enrollment(self) -> bool:
        """Validate only our fixed source and intentional credstore symlink."""
        present = False
        for relative in (_ENROLLMENT, f"{_CREDSTORE}/{_CREDENTIAL_NAME}"):
            try:
                with self._directory(str(Path(relative).parent)) as parent:
                    info = os.stat(Path(relative).name, dir_fd=parent, follow_symlinks=False)
                    if relative == _ENROLLMENT:
                        self._key_info(info)
                    elif (not stat.S_ISLNK(info.st_mode) or info.st_nlink != 1
                          or (info.st_uid, info.st_gid) != self.root_owner
                          or os.readlink(_CREDENTIAL_NAME, dir_fd=parent) != str(self.root / _ENROLLMENT)):
                        raise InstallerError("unsafe managed enrollment credstore entry")
                    present = True
            except FileNotFoundError:
                pass
        return present

    def _write_enrollment(self, data: bytearray) -> None:
        # Publish the lookup link durably before the source can exist. Cleanup
        # unlinks/fsyncs the source first, so a surviving source remains visible
        # to restart admission even if staging or cleanup is interrupted.
        with self._directory(_CREDSTORE) as parent:
            os.symlink(str(self.root / _ENROLLMENT), _CREDENTIAL_NAME, dir_fd=parent)
            os.chown(_CREDENTIAL_NAME, *self.root_owner, dir_fd=parent, follow_symlinks=False)
            os.fsync(parent)
        with self._directory("etc/happyranch") as parent:
            descriptor = os.open(
                "enrollment.key", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600, dir_fd=parent,
            )
            try:
                os.fchown(descriptor, *self.root_owner)
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "wb", closefd=False) as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.fsync(parent)

    def _remove_enrollment(self) -> None:
        # Validate everything before any unlink; never follow a substituted path.
        self._check_enrollment()
        for relative in (_ENROLLMENT, f"{_CREDSTORE}/{_CREDENTIAL_NAME}"):
            try:
                with self._directory(str(Path(relative).parent)) as parent:
                    try:
                        os.unlink(Path(relative).name, dir_fd=parent)
                    except FileNotFoundError:
                        pass
                    os.fsync(parent)
            except FileNotFoundError:
                pass

    def install(self, *, enrollment_key_file: Path | None = None) -> dict[str, str]:
        """Start then verify READY/drift; always discard staged enrollment input."""
        home = self._refuse_staged_identity()
        if home is not None:
            if enrollment_key_file is not None or self._check_enrollment():
                raise InstallerError("ready home restart requires no enrollment input")
            if self.inventory().state != "owned-activated":
                raise InstallerError("home inventory drift")
            self._refuse_staged_identity()
            self.manager.enable(UNITS[3])
            self._refuse_staged_identity()
            self.manager.enable(UNITS[2])
            self._refuse_staged_identity()
            self.manager.start(UNITS[2], timeout=250.0)
            return self.check_drift()
        if enrollment_key_file is None:
            raise InstallerError("install requires --enrollment-key-file; obtain a fresh single-use Headscale key")
        data = bytearray()
        staged = False
        try:
            data = self._read_enrollment(enrollment_key_file)
            self._service_owner = self._account(create=False)
            self._preflight()
            self._check_deb_payload()
            self._check_release_record()
            if self.layout == "deb" or self._has_provisioning():
                self._check_provisioning()
            if self._check_enrollment():
                raise InstallerError("enrollment residue exists; inspect and uninstall before retrying with a fresh key")
            self._service_owner = self._account(create=True)
            self._preflight()
            self._check_deb_payload()
            for path in self._layout():
                with self._directory(path, create=True):
                    pass
            self._write_units()
            staged = True
            self._write_enrollment(data)
            self.manager.daemon_reload()
            self.manager.enable(UNITS[3])
            self.manager.enable(UNITS[2])
            try:
                # Connector then sidecar each have TimeoutStartSec=120.
                self.manager.start(UNITS[2], timeout=250.0)
                return self.check_drift()
            except (InstallerError, ServiceManagerError, OSError) as exc:
                try:
                    self.manager.stop(UNITS[2], timeout=50.0)
                except ServiceManagerError as stop_exc:
                    raise InstallerError(f"{exc}; stopping the target also failed: {stop_exc}") from exc
                raise
        except (OSError, ServiceManagerError) as exc:
            raise InstallerError(f"installation failed: {exc}; inspect partial files and obtain a fresh key before retrying") from exc
        finally:
            data[:] = b"\x00" * len(data)
            if staged:
                try:
                    self._remove_enrollment()
                except (OSError, InstallerError) as exc:
                    raise InstallerError("cannot remove staged enrollment input; revoke the key and inspect /etc/happyranch/enrollment.key and /etc/credstore/happyranch-tsnet-enrollment") from exc

    def check_drift(self) -> dict[str, str]:
        """Observe installed-layout units, provisioning and current token source.

        A successful blocking systemctl start plus active Type=notify services
        establishes systemd's READY boundary. No loaded-property contract or
        pre-start drift check is used. The target itself is not Type=notify.
        """
        digests: dict[str, str] = {}
        try:
            self._refuse_install_transaction()
            home = self._refuse_staged_identity()
            if self._has_provisioning():
                with self._directory("etc/happyranch") as parent:
                    record = self._json(self._read_file(parent, "provision.json", owner=self.root_owner, mode=0o600))
                self.layout = record.get("layout")
                if home is not None:
                    from runtime.remote_access.home_admission import HomeProfileAdmission
                    self.units = HomeProfileAdmission(self).observed_units(home)
                else:
                    self.units = managed_units(self.layout)
            self._check_deb_payload()
            self._check_release_record()
            if self.layout == "deb" or self._has_provisioning():
                self._service_owner = self._account(create=False)
                self._check_provisioning()
            with self._directory(_UNIT_DIR) as parent:
                for name, text in self.units.items():
                    actual = hashlib.sha256(self._read_unit(parent, name)).hexdigest()
                    if actual != hashlib.sha256(text.encode()).hexdigest():
                        raise InstallerError(f"unit file digest changed: {name}")
                    props = self.manager.show_properties(name, (
                        "FragmentPath", "DropInPaths", "NeedDaemonReload", "ActiveState",
                    ))
                    expected_path = str(self.root / _UNIT_DIR / name)
                    for key, expected in (
                        ("FragmentPath", expected_path), ("DropInPaths", ""),
                        ("NeedDaemonReload", "no"), ("ActiveState", "active"),
                    ):
                        if props.get(key) != expected:
                            raise InstallerError(f"unit drift: {name} {key}={props.get(key)!r}; expected {expected!r}")
                    digests[name] = actual
        except (OSError, ServiceManagerError) as exc:
            raise InstallerError(f"cannot verify post-start unit drift: {exc}") from exc
        return digests

    def uninstall(self, *, purge: bool = False) -> dict[str, str]:
        """Wait for services before unit removal. Only purge removes service state."""
        self._refuse_install_transaction()
        if self._refuse_staged_identity() is not None:
            raise InstallerError("home control binding not provisioned; requires slice 3b-2")
        headscale_result = (self.cleanup_headscale() if self.layout == "deb"
                            else {"state": "absent", "account": "absent"})
        try:
            self._service_owner = self._account(create=False)
            self._preflight()
            self._remove_enrollment()
            self.manager.stop(UNITS[2], timeout=50.0)
            # PartOf propagates stop, but the target's job finishes first.
            # Keep RuntimeDirectory loaded until each service has shut down.
            for name in reversed(UNITS[:2]):
                self.manager.stop(name, timeout=50.0)
            self.manager.stop(UNITS[3], timeout=5.0)
            self.manager.stop(UNITS[4], timeout=5.0)
            self.manager.disable(UNITS[3])
            self.manager.disable(UNITS[2])
            try:
                with self._directory(_UNIT_DIR) as parent:
                    for name in self.units:
                        try:
                            self._read_unit(parent, name)
                        except FileNotFoundError:
                            continue
                        os.unlink(name, dir_fd=parent)
                    os.fsync(parent)
            except FileNotFoundError:
                pass
            try:
                with self._directory('etc/happyranch') as parent:
                    try:
                        self._read_file(parent, 'release-daemon.json', owner=self.root_owner, mode=0o644)
                    except FileNotFoundError:
                        pass
                    else:
                        os.unlink('release-daemon.json', dir_fd=parent)
                        os.fsync(parent)
            except FileNotFoundError:
                pass
            self.manager.daemon_reload()
            if purge:
                if self._service_owner is None:
                    raise InstallerError("cannot purge state without a validated happyranch account")
                for path in _STATE_DIRS:
                    try:
                        with self._directory(path) as descriptor:
                            self._purge_contents(descriptor)
                        with self._directory(str(Path(path).parent)) as parent:
                            os.rmdir(Path(path).name, dir_fd=parent)
                    except FileNotFoundError:
                        pass
        except (OSError, ServiceManagerError) as exc:
            raise InstallerError(f"uninstall failed: {exc}; partial changes may remain") from exc
        return headscale_result

    def _purge_contents(self, descriptor: int) -> None:
        for name in os.listdir(descriptor):
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if info.st_uid != self._service_owner[0]:
                raise InstallerError(f"refusing to purge state with foreign ownership: {name}")
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, _DIR_FLAGS, dir_fd=descriptor)
                try:
                    self._purge_contents(child)
                finally:
                    os.close(child)
                os.rmdir(name, dir_fd=descriptor)
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                os.unlink(name, dir_fd=descriptor)
            else:
                raise InstallerError(f"refusing to purge linked or special state entry: {name}")


def main(argv: list[str] | None = None) -> int:
    if sys.platform == "darwin":
        from runtime.remote_access.launchd_installer import main as darwin_main

        return darwin_main(argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("provision", "install", "uninstall", "check-drift", "prepare-headscale", "disable-headscale", "home-retirement-status"))
    parser.add_argument("--layout", choices=("source", "deb"), default="source")
    for option in ("connector-config", "sidecar-config", "policy-file", "daemon-token-source"):
        parser.add_argument(f"--{option}", type=Path, help="absolute input path (provision only)")
    parser.add_argument("--release-user", help="passwd user owning the release daemon (provision only)")
    parser.add_argument("--data-home", type=Path, help="resolved XDG data home; defaults to passwd-home/.local/share/happyranch")
    parser.add_argument("--purge", action="store_true", help="also remove service state on uninstall")
    parser.add_argument("--enrollment-key-file", type=Path, help="root-owned regular 0600 file containing a fresh single-use key (install only)")
    args = parser.parse_args(argv)
    if args.purge and args.command != "uninstall":
        parser.error("--purge is only valid with uninstall")
    if args.enrollment_key_file is not None and args.command != "install":
        parser.error("--enrollment-key-file is only valid with install")
    inputs = {name: getattr(args, name) for name in (
        "connector_config", "sidecar_config", "policy_file", "daemon_token_source", "release_user", "data_home")}
    if args.command == "provision" and not all(inputs[key] for key in ("connector_config", "sidecar_config", "policy_file", "release_user")):
        parser.error("provision requires --connector-config, --sidecar-config, --policy-file and --release-user")
    if args.command != "provision" and any(inputs.values()):
        parser.error("provisioning inputs are only valid with provision")
    if args.command in ("prepare-headscale", "disable-headscale"):
        from runtime.remote_access.home_retirement import MESSAGE
        print(MESSAGE, file=sys.stderr)
        return 1
    try:
        installer = ManagedInstaller(layout=args.layout)
        if args.command == "home-retirement-status":
            result = installer.home_retirement_status()
            print(json.dumps(result, sort_keys=True))
            return 1 if result["state"] == "migration pending" else 0
        elif args.command == "provision":
            installer.provision(**inputs)
        elif args.command == "install":
            installer.install(enrollment_key_file=args.enrollment_key_file)
        elif args.command == "uninstall":
            result = installer.uninstall(purge=args.purge)
            if result["state"] != "absent":
                print(json.dumps(result, sort_keys=True))
        else:
            installer.check_drift()
    except (InstallerError, headscale.HeadscaleServiceError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
