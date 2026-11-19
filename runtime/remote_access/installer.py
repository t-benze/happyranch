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
import ctypes
import errno
import fcntl
import grp
import hashlib
import json
import math
import os
import pwd
import re
import ssl
import stat
import sys
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import urlsplit

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
HOME_ISSUER = headscale.INSTALLER_STATE + "/home-issuer"
_ISSUER_RECEIPT = "home-issuer-publication.json"
_QUARANTINE_CONFIGS = ("connector.json", "sidecar.json", "policy.json")
_QUARANTINE_PHASES = ("replacement-quarantine-connector", "replacement-quarantine-sidecar",
                      "replacement-quarantine-policy", "replacement-quarantined")
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
_REMOVAL_PHASES = (*[phase for phase, _ in _REPLACEMENT_REMOVALS], "replacement-state-removed")
_CONFIG_REMOVALS = (
    ("replacement-remove-config-connector", "etc/happyranch/.replacement-connector.json"),
    ("replacement-remove-config-sidecar", "etc/happyranch/.replacement-sidecar.json"),
    ("replacement-remove-config-policy", "etc/happyranch/.replacement-policy.json"),
)
_CONFIG_REMOVAL_PHASES = (*[phase for phase, _ in _CONFIG_REMOVALS], "replacement-configs-removed")
_UNIT_REMOVALS = (
    ("replacement-remove-target-link", f"{_UNIT_DIR}/multi-user.target.wants/{UNITS[2]}", "link"),
    ("replacement-remove-verifier-link", f"{_UNIT_DIR}/{UNITS[2]}.wants/{UNITS[3]}", "link"),
    *[("replacement-remove-unit-" + label, f"{_UNIT_DIR}/{name}", "unit") for label, name in zip(
        ("connector", "sidecar", "target", "verifier-socket", "verifier-service"), UNITS)],
    *[("replacement-remove-dropins-" + label, f"{_UNIT_DIR}/{name}.d", "directory") for label, name in zip(
        ("connector", "sidecar", "target", "verifier-socket", "verifier-service"), UNITS)],
    ("replacement-remove-target-wants", f"{_UNIT_DIR}/{UNITS[2]}.wants", "directory"),
)
_UNIT_REMOVAL_PHASES = (*[phase for phase, _, _ in _UNIT_REMOVALS], "replacement-units-removed")
_STAGING_REMOVALS = (
    ("replacement-discard-token", "etc/happyranch/daemon.token"),
    ("replacement-discard-provision", "etc/happyranch/provision.json"),
)
_STAGING_FILES = (
    ("replacement-stage-connector", "connector.json"),
    ("replacement-stage-sidecar", "sidecar.json"),
    ("replacement-stage-policy", "policy.json"),
    ("replacement-stage-token", "daemon.token"),
    ("replacement-stage-provision", "provision.json"),
)
_STAGING_PHASES = (*[phase for phase, _ in (*_STAGING_REMOVALS, *_STAGING_FILES)], "replacement-staged")
_CLOSURE_PHASES = ("replacement-reload", "replacement-retire-journal")
_REPLACEMENT_PHASES = ("replacement-stopped", *_QUARANTINE_PHASES, *_REMOVAL_PHASES,
                       *_CONFIG_REMOVAL_PHASES, *_UNIT_REMOVAL_PHASES, *_STAGING_PHASES, *_CLOSURE_PHASES)
_REPLACEMENT_FILES = (*_QUARANTINE_CONFIGS, "release-daemon.json", "daemon.token", "provision.json")
_V2_STAGING_FILES = (("replacement-stage-release", "release-daemon.json"), *_STAGING_FILES)
_V2_STAGING_PHASES = (*[phase for phase, _ in (*_STAGING_REMOVALS, *_V2_STAGING_FILES)], "replacement-staged")
_V2_REPLACEMENT_PHASES = (*_REPLACEMENT_PHASES[:_REPLACEMENT_PHASES.index(_STAGING_PHASES[0])],
                          *_V2_STAGING_PHASES, *_CLOSURE_PHASES)
_ALL_STAGING_PHASES = (*_STAGING_PHASES, "replacement-stage-release")


def _replacement_phases(record: dict) -> tuple[str, ...]:
    return ("prepared", *(_V2_REPLACEMENT_PHASES if record["version"] == 2 else _REPLACEMENT_PHASES))


def _replacement_staging(record: dict) -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
    return (_V2_STAGING_FILES, _V2_STAGING_PHASES) if record["version"] == 2 else (_STAGING_FILES, _STAGING_PHASES)


class InstallerError(RuntimeError):
    """An unsafe path, incompatible account, command failure, or unit drift."""


@dataclass(frozen=True)
class InstallInventory:
    """Read-only classification, never authority to remove or replay files."""

    state: str
    detail: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class ReplacementInventory:
    """Read-only replacement candidate, never permission to replay a journal.

    journal_generation fingerprints an exact validated recovery record.
    inventory_generation binds an unjournaled fixed inventory for first admission.
    Neither observation authorizes activation or mutation by itself.
    """

    installation: InstallInventory
    journal_generation: str | None = None
    inventory_generation: str | None = None


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


class ManagedInstaller:
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
        *, directories: set[str] | None = None,
    ) -> dict[str, bytes]:
        """Bounded, shallow inventory of one fixed directory; no unknown reads."""
        files: dict[str, bytes] = {}
        try:
            with self._directory(relative) as parent:
                with os.scandir(parent) as entries:
                    for index, entry in enumerate(entries):
                        path = str(self.root / relative / entry.name)
                        if index < 64 and entry.name in (directories or set()):
                            with self._directory(relative + "/" + entry.name):
                                pass
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
        self, connector: dict, *, removing_units: set[str] | None = None,
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
            # systemd can retain the old fragment after its journaled unlink,
            # including across a new installer instance. Only this consumer's
            # exact current/completed unit intent permits a cached OWN fragment.
            cached = (not loaded and name in (removing_units or set())
                      and props.get("LoadState") == "loaded" and props.get("FragmentPath") == path)
            if (props.get("FragmentPath") != (path if loaded or cached else "")
                    or props.get("DropInPaths") != ""
                    or props.get("NeedDaemonReload") not in ({"no", "yes"} if cached else {"no"})
                    or props.get("LoadState") != ("loaded" if loaded or cached else "not-found")
                    or props.get("ActiveState") not in ({"active", "inactive", "failed"} if loaded else
                                                       {"inactive", "failed"} if cached else {"inactive"})):
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

    def _inventory(self, *, prepared_transaction: bytes | None = None,
                   pending: tuple[str, bytes] | None = None) -> InstallInventory:
        preparing = self._headscale_preparing
        location = str(self.root / "etc/happyranch")
        paths: list[str] = []
        try:
            self._service_owner = self._account(create=False)
            service = self._service_owner
            config_owner = None if service is None else (self.root_owner[0], service[1])
            phase = None
            replacement_record = None
            if prepared_transaction is not None:
                self._validate_install_record("install-transaction.json", prepared_transaction, replacement=True)
                replacement_record = self._json(prepared_transaction)
                phase = replacement_record["phase"]
            if phase in (*_ALL_STAGING_PHASES, *_CLOSURE_PHASES):
                return self._staging_inventory(prepared_transaction, pending=pending)
            unit_removal = phase in _UNIT_REMOVAL_PHASES
            quarantine_phase = ("replacement-configs-removed" if unit_removal else
                                "replacement-quarantined" if phase in _REMOVAL_PHASES else phase)
            removed = ({relative for _, relative in _REPLACEMENT_REMOVALS[:_REMOVAL_PHASES.index(phase)]}
                       if phase in _REMOVAL_PHASES else set())
            removable_configs: set[str] = set()
            if phase in _CONFIG_REMOVAL_PHASES:
                current = _CONFIG_REMOVAL_PHASES.index(phase)
                removed = {relative for _, relative in (*_REPLACEMENT_REMOVALS, *_CONFIG_REMOVALS[:current])}
                removable_configs = set(_QUARANTINE_CONFIGS[:current + 1])
            removing_units: set[str] = set()
            if unit_removal:
                current = _UNIT_REMOVAL_PHASES.index(phase)
                removed = {relative for _, relative in (*_REPLACEMENT_REMOVALS, *_CONFIG_REMOVALS)}
                removed.update(relative for _, relative, _ in _UNIT_REMOVALS[:current])
                removable_configs = set(_QUARANTINE_CONFIGS)
                removing_units = {Path(relative).name for _, relative, kind in _UNIT_REMOVALS[:current + 1]
                                  if kind == "unit"}
            if unit_removal or phase == "replacement-configs-removed":
                # Unlike multi-user.target.wants, this target's wants directory
                # belongs solely to these units. Unknown contents veto deletion.
                if self._unit_wants_names() - {UNITS[3]}:
                    raise InstallerError(str(self.root / _UNIT_DIR / (UNITS[2] + ".wants")))
            # Completed removal intents require absence, including on resume.
            for relative in removed:
                try:
                    with self._directory(str(Path(relative).parent)) as parent:
                        os.stat(Path(relative).name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                raise InstallerError(str(self.root / relative))
            home = None
            if prepared_transaction is None:
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
            if quarantine_phase in (*_QUARANTINE_PHASES, *_CONFIG_REMOVAL_PHASES):
                config_metadata = self._quarantine_metadata(quarantine_phase, record=replacement_record)
            files = self._inventory_files("etc/happyranch", {
                **config_metadata,
                **({"control-ca.pem": (config_owner, 0o640, False),
                    "invite-issuer.json": (self.root_owner, 0o600, False)} if home else {}),
                "release-daemon.json": (self.root_owner, 0o644, False),
                "provision.json": (self.root_owner, 0o600, False),
                "daemon.token": (service, 0o600, False),
                "enrollment.key": (self.root_owner, 0o600, False),
                **({"install-transaction.json": (self.root_owner, 0o600, False)}
                   if prepared_transaction is not None else {}),
                **({".install-transaction.json.pending": (self.root_owner, 0o600, False)}
                   if pending is not None and pending[0] == "install-transaction.json" else {}),
            }, directories={"headscale", "empty-ca-directory"} if home else None)
            if home is not None:
                files.pop("control-ca.pem", None)
                files.pop("invite-issuer.json", None)
            if pending is not None and files.pop("." + pending[0] + ".pending", None) != pending[1]:
                raise InstallerError(str(self.root / "etc/happyranch" / ("." + pending[0] + ".pending")))
            paths.extend(str(self.root / "etc/happyranch" / name) for name in files)
            if quarantine_phase in (*_QUARANTINE_PHASES, *_CONFIG_REMOVAL_PHASES):
                for name in _QUARANTINE_CONFIGS:
                    quarantined = ".replacement-" + name
                    if quarantined in files:
                        files[name] = files.pop(quarantined)
            if prepared_transaction is not None:
                location = str(self.root / "etc/happyranch/install-transaction.json")
                if files.pop("install-transaction.json", None) != prepared_transaction:
                    raise InstallerError(location)
                digests = self._json(prepared_transaction)["digests"]
                if any((name in files if digest == "absent" else
                        (name not in files and name not in removable_configs)
                        or (name in files and hashlib.sha256(files[name]).hexdigest() != digest))
                       for name, digest in digests.items()):
                    raise InstallerError(location)
                if replacement_record["version"] == 2:
                    self._validate_configs(files, staged=True, partial=True, require_current=False)
            connector = self._json(files["connector.json"]) if "connector.json" in files else {}
            provenance = files.get("provision.json")
            modern = False
            if home is not None and any(files.get(name) != data for name, data in home.files.items()):
                raise InstallerError(location)
            required = {"connector.json", "sidecar.json", "policy.json", "release-daemon.json", "daemon.token"}
            if provenance is not None:
                # Recorded provisioning retains its exact mode contract. Only
                # unrecorded legacy configs may use the other safe modes above.
                if quarantine_phase not in (*_QUARANTINE_PHASES, *_CONFIG_REMOVAL_PHASES):
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
                runtime = self._inventory_files(relative, entries)
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
            # Once the connector copy is journaledly removed, only the validated
            # provenance selects the existing exact unit-byte profile. No old
            # config/argv/control binding is reconstructed or executed.
            if (phase in _CONFIG_REMOVAL_PHASES or unit_removal) and "connector.json" not in files and modern:
                connector = {"managed": {"profile": "linux-managed-identity-v1"}}
            units, properties = self._inventory_units(connector, removing_units=removing_units,
                observed_units=HomeProfileAdmission(self).observed_units(home) if home else None)
            paths.extend(str(self.root / _UNIT_DIR / name) for name in units)
            if home is not None:
                if len(units) != len(UNITS):
                    raise InstallerError(location)
                return InstallInventory("owned-activated" if home.receipt.phase == "ready" else "owned",
                                        home.receipt.phase, tuple(sorted(paths)))
            complete = required <= files.keys()
            failed = any(props["ActiveState"] == "failed" for props in properties.values())
            if phase in _REPLACEMENT_PHASES or replacement_record is not None and replacement_record["version"] == 2:
                # Only the replacement consumer can recognize this intent. All
                # remaining original files must still match; stopping changes manager
                # state, so it cannot be used to infer a successful manual install.
                return InstallInventory("known-failed-or-partial", phase, tuple(sorted(paths)))
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

    def replacement_inventory(self) -> ReplacementInventory:
        """Inspect a journal only for replacement candidate selection.

        V1 and additive v2 recovery phases belong only to this consumer. Only exact
        interrupted writer intents qualify; observation never discards them.
        Missing/different fixed files, wrong placement or unsafe metadata refuse.
        Normal inventory and every lifecycle operation still reject journals.
        This observation neither reads a release-home token nor stops services.
        """
        location = str(self.root / "etc/happyranch/install-transaction.json")
        try:
            with self._publication_parent() as (parent, revalidate):
                transaction = self._read_file(parent, "install-transaction.json", owner=self.root_owner, mode=0o600)
                pending = self._replacement_pending(parent, transaction)
                installation = self._inventory(prepared_transaction=transaction,
                    pending=("install-transaction.json", pending) if pending is not None else None)
                revalidate()
        except FileNotFoundError:
            installation = self.inventory()
            if installation.state != "known-failed-or-partial":
                return ReplacementInventory(installation)
            try:
                snapshot = self._replacement_snapshot()
            except (OSError, InstallerError):
                return ReplacementInventory(InstallInventory("manual-install-conflict", "unsafe-or-unrecognized", (location,)))
            generation = hashlib.sha256(json.dumps((asdict(installation), snapshot), sort_keys=True).encode()).hexdigest()
            return ReplacementInventory(installation, inventory_generation=generation)
        except (OSError, InstallerError):
            return ReplacementInventory(InstallInventory("manual-install-conflict", "unsafe-or-unrecognized", (location,)))
        generation = (hashlib.sha256(transaction).hexdigest()
                      if installation.state == "known-failed-or-partial" else None)
        return ReplacementInventory(installation, generation)

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

    def _replacement_release(self, transaction: bytes) -> dict:
        record = self._json(transaction)
        if record["version"] == 1:
            return self._check_release_record()
        expected = record["release"]
        if self._release_record(expected["user"], Path(expected["data_home"])) != expected:
            raise InstallerError("replacement release owner changed")
        try:
            actual = self._check_release_record()
        except FileNotFoundError:
            staging_release = (record["phase"] == "replacement-stage-release"
                               and record["inventory"]["etc/happyranch/release-daemon.json"] == "absent")
            if record["digests"]["release-daemon.json"] != "absent" and not staging_release:
                raise InstallerError("replacement release record disappeared") from None
        else:
            if actual != expected:
                raise InstallerError("replacement release binding changed")
        return expected

    def _replacement_pending(self, parent: int, transaction: bytes) -> bytes | None:
        """Recognize only the normal writer's exact interrupted journal intent."""
        name = ".install-transaction.json.pending"
        try:
            data = self._read_file(parent, name, owner=self.root_owner, mode=0o600)
        except FileNotFoundError:
            return None
        self._validate_install_record("install-transaction.json", transaction, replacement=True)
        self._validate_install_record("install-transaction.json", data, replacement=True)
        expected, intent = self._json(transaction), self._json(data)
        phases = _replacement_phases(expected)
        previous = phases.index(expected["phase"])
        staged_name = dict(_replacement_staging(expected)[0]).get(expected["phase"])
        if staged_name is not None and intent["phase"] == expected["phase"]:
            if expected["digests"][staged_name] == intent["digests"][staged_name]:
                raise InstallerError("replacement pending is not a digest rebind")
            self._refuse_provision_entry(parent, staged_name)
            expected["digests"][staged_name] = intent["digests"][staged_name]
        elif previous + 1 < len(phases):
            expected["phase"] = phases[previous + 1]
        else:
            raise InstallerError("replacement pending has no successor")
        if data != json.dumps(expected, sort_keys=True).encode():
            raise InstallerError("replacement pending is not the exact writer intent")
        return data

    def _discard_replacement_pending(self, transaction: bytes) -> None:
        """Discard validated intent only; the normal writer regenerates/rebinds."""
        with self._publication_parent() as (parent, revalidate):
            pending = self._replacement_pending(parent, transaction)
            if pending is None:
                return
            name = ".install-transaction.json.pending"
            before = os.stat(name, dir_fd=parent, follow_symlinks=False)
            self._check_replacement_transaction(parent, transaction)
            self._replacement_custody(transaction)
            if self._inventory(prepared_transaction=transaction,
                               pending=("install-transaction.json", pending)).state != "known-failed-or-partial":
                raise InstallerError("replacement pending inventory changed before discard")
            self._check_replacement_transaction(parent, transaction)
            if self._replacement_pending(parent, transaction) != pending:
                raise InstallerError("replacement pending changed before discard")
            revalidate()
            after = os.stat(name, dir_fd=parent, follow_symlinks=False)
            fields = ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
            if any(getattr(before, field) != getattr(after, field) for field in fields):
                raise InstallerError("replacement pending metadata changed before discard")
            os.unlink(name, dir_fd=parent)
            os.fsync(parent)

    def begin_replacement(self, *, expected_release_uid: int, expected_generation: str) -> None:
        """Bind inventory and derived release owner before the additive first intent.

        Only the future fresh-auth helper lifecycle owner may supply expectations.
        Existing v1 journals already have a first intent and resume unchanged.
        This entry is deliberately absent from the operator CLI and D-Bus helper.
        """
        candidate = self.replacement_inventory()
        generation = candidate.journal_generation or candidate.inventory_generation
        if (type(expected_release_uid) is not int or expected_release_uid <= 0
                or not isinstance(expected_generation, str) or generation != expected_generation
                or candidate.installation.state != "known-failed-or-partial"):
            raise InstallerError("replacement expectations or candidate changed")
        try:
            if candidate.journal_generation is None:
                configs = {}
                try:
                    with self._directory("etc/happyranch") as parent:
                        for name in _QUARANTINE_CONFIGS:
                            try:
                                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                                configs[name] = self._read_file(parent, name,
                                    owner=(self.root_owner[0], self._service_owner[1]), mode=stat.S_IMODE(info.st_mode))
                            except FileNotFoundError:
                                pass
                except FileNotFoundError:
                    pass
                self._validate_configs(configs, staged=True, partial=True, require_current=False)
            if candidate.journal_generation is not None:
                with self._directory("etc/happyranch") as parent:
                    transaction = self._read_file(parent, "install-transaction.json", owner=self.root_owner, mode=0o600)
                release = self._replacement_release(transaction)
            else:
                try:
                    release = self._check_release_record()
                except FileNotFoundError:
                    user = pwd.getpwuid(expected_release_uid)
                    release = self._release_record(user.pw_name, None)
            if release["uid"] != expected_release_uid:
                raise InstallerError("replacement release UID mismatch")
            self._input(Path(release["data_home"]) / "daemon.token", token=True, source_uid=expected_release_uid)
            if self.replacement_inventory() != candidate:
                raise InstallerError("replacement inventory changed before first intent")
            if candidate.journal_generation is None:
                snapshot = self._replacement_snapshot()
                record = dict(format="replacement-intent@2", version=2, layout=self.layout, phase="prepared",
                              release=release, inventory=snapshot,
                              digests={name: snapshot["etc/happyranch/" + name].get("digest", "absent")
                                       if isinstance(snapshot["etc/happyranch/" + name], dict) else "absent"
                                       for name in _REPLACEMENT_FILES})
                self._write_first_replacement(json.dumps(record, sort_keys=True).encode(), candidate)
            self.replace_failed()
        except (OSError, KeyError, ValueError):
            raise InstallerError("replacement first intent or release binding incomplete") from None

    def _write_first_replacement(self, data: bytes, candidate: ReplacementInventory) -> None:
        """Publish only after fresh full inventory/owner binding, with no overwrite."""
        self._validate_install_record("install-transaction.json", data, replacement=True)
        record = self._json(data)
        created_parent = False
        try:
            with self._directory("etc/happyranch"):
                pass
        except FileNotFoundError:
            # The fixed journal's own parent is publication scaffolding, never
            # authority to change any pre-existing replacement member.
            if self.replacement_inventory() != candidate or self._replacement_snapshot() != record["inventory"]:
                raise InstallerError("replacement first inventory changed before parent creation")
            with self._publication_parent("etc") as (parent, revalidate):
                release = record["release"]
                if self._release_record(release["user"], Path(release["data_home"])) != release:
                    raise InstallerError("replacement release owner changed before parent creation")
                self._input(Path(release["data_home"]) / "daemon.token", token=True, source_uid=release["uid"])
                self._confirm_quiescent()
                revalidate()
                os.mkdir("happyranch", mode=0o700, dir_fd=parent)
                directory = os.open("happyranch", _DIR_FLAGS, dir_fd=parent)
                try:
                    os.fchown(directory, *self.root_owner)
                    os.fchmod(directory, 0o755)
                    os.fsync(directory)
                finally:
                    os.close(directory)
                revalidate()
                os.fsync(parent)
            created_parent = True
        with self._publication_parent() as (parent, revalidate):
            snapshot = self._replacement_snapshot()
            expected = dict(record["inventory"])
            if created_parent:
                expected["etc/happyranch"] = snapshot["etc/happyranch"]
            if self.inventory() != candidate.installation or snapshot != expected:
                raise InstallerError("replacement first inventory changed")
            release = record["release"]
            if self._release_record(release["user"], Path(release["data_home"])) != release:
                raise InstallerError("replacement release owner changed before intent")
            self._input(Path(release["data_home"]) / "daemon.token", token=True, source_uid=release["uid"])
            self._refuse_provision_entry(parent, "install-transaction.json")
            name = ".install-transaction.json.pending"
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=parent)
            try:
                os.fchown(fd, *self.root_owner)
                with os.fdopen(fd, "wb", closefd=False) as stream:
                    if stream.write(data) != len(data):
                        raise InstallerError("incomplete first replacement intent")
                    stream.flush()
                    os.fsync(fd)
                # The exclusive pending entry is not first-admission authority.
                # Inventory validates its exact bytes separately before rename.
                info = os.fstat(fd)
                named = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if ((info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)
                        or self._read_file(parent, name, owner=self.root_owner, mode=0o600) != data):
                    raise InstallerError("first replacement pending changed")
                if (self._inventory(pending=("install-transaction.json", data)) != candidate.installation
                        or self._replacement_snapshot() != expected
                        or self._release_record(release["user"], Path(release["data_home"])) != release):
                    raise InstallerError("first replacement binding changed before rename")
                self._input(Path(release["data_home"]) / "daemon.token", token=True, source_uid=release["uid"])
                named = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if ((named.st_dev, named.st_ino) != (info.st_dev, info.st_ino)
                        or self._read_file(parent, name, owner=self.root_owner, mode=0o600) != data):
                    raise InstallerError("first replacement pending changed before rename")
                revalidate()
                self._rename_provision_entry(parent, name, "install-transaction.json")
                os.fsync(parent)
            finally:
                os.close(fd)

    def replace_failed(self) -> None:
        """Resume exact config quarantine, then fixed state/enrollment removal.

        Prepared candidates publish stopped intent first; the next call quarantines
        configs. A later call consumes quarantine to remove fixed retired state.
        A further call removes quarantined configs, then another removes only
        owned unit entries. A later call stages fresh inert files; the next call
        closes that exact generation into ordinary modern staged-inert inventory.
        Unjournaled candidates require begin_replacement's explicit expectations.
        """
        candidate = self.replacement_inventory()
        installation = candidate.installation
        if installation.state == "modern" and installation.detail == "staged-inert":
            # Only a recognized journal resumes closure below. Retired staging
            # is indistinguishable from a clean-host non-candidate.
            raise InstallerError("replacement refused: modern:")
        if installation.state != "known-failed-or-partial":
            raise InstallerError(f"replacement refused: {installation.state}: " + ", ".join(installation.paths))
        if candidate.journal_generation is None:
            raise InstallerError("replacement mutation unavailable; unjournaled replacement requires the next 3b-1c sub-part")
        try:
            with self._directory("etc/happyranch") as parent:
                transaction = self._read_file(parent, "install-transaction.json", owner=self.root_owner, mode=0o600)
            if hashlib.sha256(transaction).hexdigest() != candidate.journal_generation:
                raise InstallerError("replacement journal changed before stop")
            # H2: derive only from the validated recorded release home. Reuse
            # the same-FD source-UID reader before ANY mutating manager call.
            release = self._replacement_release(transaction)
            self._input(Path(release["data_home"]) / "daemon.token", token=True, source_uid=release["uid"])
            if self.replacement_inventory() != candidate:
                raise InstallerError("replacement inventory changed before stop")
            self._discard_replacement_pending(transaction)
            self.stop_and_confirm(_replacement_transaction=transaction)
            record = self._json(transaction)
            if record["phase"] == "prepared":
                record["phase"] = "replacement-stopped"
                self._write_provision_file("install-transaction.json", json.dumps(record, sort_keys=True).encode(),
                                           self.root_owner, 0o600, expected_transaction=transaction)
            elif record["phase"] in ("replacement-staged", *_CLOSURE_PHASES):
                self._close_replacement(transaction)
                return
            elif record["phase"] in ("replacement-units-removed", *_ALL_STAGING_PHASES):
                self._stage_replacement(transaction)
            elif record["phase"] in (*_REMOVAL_PHASES, *_CONFIG_REMOVAL_PHASES, *_UNIT_REMOVAL_PHASES,
                                     "replacement-quarantined"):
                self._remove_replacement_state(transaction)
            else:
                self._quarantine_configs(transaction)
        except (OSError, ServiceManagerError):
            raise InstallerError("replacement stop intent incomplete; inspect the fixed transaction record") from None
        raise InstallerError("replacement paused after stop intent; quarantine/removal and fresh staging unavailable")

    def _quarantine_metadata(self, phase: str, *, record: dict | None = None) -> dict:
        """Accept exactly the before/after placement of ONE journaled rename.

        Only its source may have partially tightened metadata. Every earlier
        config is root-only in quarantine; every later v1 config is still 0640.
        V2 retains the captured legacy modes and explicitly absent originals.
        Neither duplicate locations nor missing configs can resume until a
        published config-removal intent permits that exact copy's absence.
        """
        removing = phase in _CONFIG_REMOVAL_PHASES
        current = (_CONFIG_REMOVAL_PHASES if removing else _QUARANTINE_PHASES).index(phase)
        metadata = {}
        config_owner = (self.root_owner[0], self._service_owner[1])
        with self._directory("etc/happyranch") as parent:
            for index, name in enumerate(_QUARANTINE_CONFIGS):
                quarantine = ".replacement-" + name
                found = {}
                for entry in (name, quarantine):
                    try:
                        found[entry] = os.stat(entry, dir_fd=parent, follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                if record is not None and record["version"] == 2 and record["digests"][name] == "absent":
                    if found:
                        raise InstallerError(str(self.root / "etc/happyranch" / name))
                    continue
                if removing:
                    if name in found or (index < current and found) or (index > current and not found):
                        raise InstallerError(str(self.root / "etc/happyranch" / name))
                    if not found:
                        continue  # Only completed/current journaled unlinks may be absent.
                    metadata[quarantine] = (self.root_owner, 0o600, False)
                    continue
                if (len(found) != 1 or (index < current and quarantine not in found)
                        or (index > current and name not in found)):
                    raise InstallerError(str(self.root / "etc/happyranch" / name))
                entry, info = next(iter(found.items()))
                actual = ((info.st_uid, info.st_gid), stat.S_IMODE(info.st_mode), False)
                allowed = {(self.root_owner, 0o600, False)} if entry == quarantine else {
                    (config_owner, 0o640, False)}
                if entry == name and record is not None and record["version"] == 2:
                    original = record["inventory"]["etc/happyranch/" + name]["metadata"]
                    allowed = {((original[2], original[3]), stat.S_IMODE(original[4]), False)}
                if index == current and entry == name:
                    allowed |= {(config_owner, 0o600, False), (self.root_owner, 0o600, False)}
                if actual not in allowed:
                    raise InstallerError(str(self.root / "etc/happyranch" / entry))
                metadata[entry] = actual
        return metadata

    def _quarantine_configs(self, transaction: bytes) -> None:
        record = self._json(transaction)
        while record["phase"] != "replacement-quarantined":
            phase = record["phase"]
            if phase in _QUARANTINE_PHASES:
                self._quarantine_config(_QUARANTINE_CONFIGS[_QUARANTINE_PHASES.index(phase)], transaction)
                next_phase = _QUARANTINE_PHASES[_QUARANTINE_PHASES.index(phase) + 1]
            else:
                next_phase = _QUARANTINE_PHASES[0]
            record["phase"] = next_phase
            updated = json.dumps(record, sort_keys=True).encode()
            self._write_provision_file("install-transaction.json", updated, self.root_owner, 0o600,
                                       expected_transaction=transaction)
            transaction = updated
        raise InstallerError("replacement paused after config quarantine; removal and fresh staging unavailable")

    def _remove_replacement_state(self, transaction: bytes) -> None:
        """Consume one bounded removal group; retain the journal until closure."""
        record = self._json(transaction)
        units = record["phase"] in ("replacement-configs-removed", *_UNIT_REMOVAL_PHASES)
        configs = record["phase"] in ("replacement-state-removed", *_CONFIG_REMOVAL_PHASES)
        phases = _UNIT_REMOVAL_PHASES if units else _CONFIG_REMOVAL_PHASES if configs else _REMOVAL_PHASES
        while record["phase"] != phases[-1]:
            phase = record["phase"]
            if phase in phases:
                if units:
                    self._remove_replacement_unit(transaction)
                else:
                    self._remove_replacement_entry(transaction)
                next_phase = phases[phases.index(phase) + 1]
            else:
                next_phase = phases[0]
            record["phase"] = next_phase
            updated = json.dumps(record, sort_keys=True).encode()
            self._write_provision_file("install-transaction.json", updated, self.root_owner, 0o600,
                                       expected_transaction=transaction)
            transaction = updated
        if units:
            raise InstallerError("replacement paused after unit removal; fresh staging and transaction closure unavailable")
        if configs:
            raise InstallerError("replacement paused after config removal; unit removal and fresh staging unavailable")
        raise InstallerError("replacement paused after state removal; config/unit removal and fresh staging unavailable")

    def _staging_inventory(self, transaction: bytes, *, pending: tuple[str, bytes] | None = None) -> InstallInventory:
        """Only completed/current effects may exist; old configs/state stay absent.

        The v1 digest slots bind the new files individually as staging advances.
        An absent current destination may receive a newly generated digest intent;
        a published destination is immutable and must match its recorded digest.
        """
        record = self._json(transaction)
        staging_files, staging_phases = _replacement_staging(record)
        current = (staging_phases.index(record["phase"]) if record["phase"] in staging_phases
                   else len(staging_phases))
        self._check_deb_payload()
        config_owner = (self.root_owner[0], self._service_owner[1])
        metadata = {**{name: (config_owner, 0o640, False) for name in _QUARANTINE_CONFIGS},
                    "release-daemon.json": (self.root_owner, 0o644, False),
                    "daemon.token": (self._service_owner, 0o600, False),
                    "provision.json": (self.root_owner, 0o600, False),
                    "install-transaction.json": (self.root_owner, 0o600, False)}
        if pending is not None:
            name, data = pending
            if name not in metadata:
                raise InstallerError("invalid replacement pending destination")
            metadata["." + name + ".pending"] = metadata[name]
        files = self._inventory_files("etc/happyranch", metadata)
        if pending is not None and files.pop("." + pending[0] + ".pending", None) != pending[1]:
            raise InstallerError("replacement pending bytes changed")
        if files.pop("install-transaction.json", None) != transaction:
            raise InstallerError("replacement generation changed during staging inventory")
        required = ({"release-daemon.json"} if record["version"] == 1
                    or record["inventory"]["etc/happyranch/release-daemon.json"] != "absent" else set())
        allowed = set(required)
        for index, (_, name) in enumerate(staging_files, start=len(_STAGING_REMOVALS)):
            if index <= current:
                allowed.add(name)
            if index < current:
                required.add(name)
        # Until each discard intent completes, only its original digest may exist.
        for index, (_, relative) in enumerate(_STAGING_REMOVALS):
            name = Path(relative).name
            if current <= index:
                allowed.add(name)
                if current < index and record["digests"][name] != "absent":
                    required.add(name)
        if not required <= files.keys() <= allowed:
            raise InstallerError("replacement staging placement changed")
        if any(hashlib.sha256(data).hexdigest() != record["digests"][name] for name, data in files.items()):
            raise InstallerError("replacement staging digest changed")
        self._validate_configs({name: data for name, data in files.items() if name in _QUARANTINE_CONFIGS},
                               staged=True, partial=True, require_current=False)
        if current >= staging_phases.index("replacement-stage-provision") and "provision.json" in files:
            self._validate_install_record("provision.json", files["provision.json"])
            if self._json(files["provision.json"])["digests"] != {
                    name: hashlib.sha256(data).hexdigest() for name, data in files.items() if name != "provision.json"}:
                raise InstallerError("replacement provisioning digest changed")
        from runtime.remote_access.release_verifier import validate_record
        if "release-daemon.json" in files:
            validate_record(self._json(files["release-daemon.json"]))
        paths = [str(self.root / "etc/happyranch" / name) for name in (*files, "install-transaction.json")]
        for relative in _STATE_DIRS:
            self._inventory_files(relative, {})  # No old trust, keys or logs are read.
        for _, relative, _ in _UNIT_REMOVALS:
            try:
                with self._directory(str(Path(relative).parent)) as parent:
                    os.stat(Path(relative).name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise InstallerError(str(self.root / relative))
        if self._check_enrollment():
            raise InstallerError("replacement enrollment reappeared")
        units, _ = self._inventory_units({"managed": {"profile": "linux-managed-identity-v1"}},
                                         removing_units=set() if record["phase"] == "replacement-retire-journal" else set(UNITS))
        if units:
            raise InstallerError("replacement units reappeared")
        for relative, entries in (
                ("run/happyranch-connector", {name: (self._service_owner, 0o600, True)
                                              for name in ("ingress.sock", "control.sock")}),
                ("run/happyranch-tsnet-sidecar", {"health.json": (self._service_owner, 0o644, False)}),
                ("run/happyranch-release-verifier", {"control.sock": (
                    (self.root_owner[0], self._service_owner[1]), 0o660, True)})):
            paths.extend(str(self.root / relative / name) for name in self._inventory_files(relative, entries))
        return InstallInventory("known-failed-or-partial", record["phase"], tuple(sorted(paths)))

    def _replacement_custody(self, transaction: bytes) -> bytes:
        if self._json(transaction)["version"] == 1:
            return self._staging_custody()
        release = self._replacement_release(transaction)
        token = self._input(Path(release["data_home"]) / "daemon.token", token=True, source_uid=release["uid"])
        self._confirm_quiescent()
        return token

    def _staging_custody(self) -> bytes:
        release = self._check_release_record()
        token = self._input(Path(release["data_home"]) / "daemon.token", token=True, source_uid=release["uid"])
        self._confirm_quiescent()
        return token

    def _staging_data(self, name: str, transaction: bytes) -> bytes:
        """Generate only new settings; never open quarantined or retired configs."""
        if name == "release-daemon.json":
            return json.dumps(self._replacement_release(transaction), sort_keys=True).encode()
        if name == "daemon.token":
            return self._replacement_custody(transaction)
        if name == "provision.json":
            record = self._json(transaction)
            return json.dumps(dict(version=1, layout=self.layout, phase="provisioned",
                                   digests={key: value for key, value in record["digests"].items()
                                            if key != "provision.json"}), sort_keys=True).encode()
        files = self._generated_configs()
        if name == "sidecar.json":
            with self._directory("etc/happyranch") as parent:
                connector = self._json(self._read_file(parent, "connector.json",
                    owner=(self.root_owner[0], self._service_owner[1]), mode=0o640))
            sidecar = self._json(files[name])
            sidecar["RoleIdentity"] = "home-sidecar-" + connector["home_id"]
            return json.dumps(sidecar, sort_keys=True).encode()
        return files[name]

    def _stage_replacement(self, transaction: bytes) -> None:
        """Stage the bounded inert prefix; keep every lifecycle guard closed."""
        record = self._json(transaction)
        staging_files, staging_phases = _replacement_staging(record)
        self._check_deb_payload()
        while record["phase"] != "replacement-staged":
            phase = record["phase"]
            if phase in dict(_STAGING_REMOVALS):
                self._remove_replacement_entry(transaction)
            elif phase in dict(staging_files):
                name = dict(staging_files)[phase]
                with self._directory("etc/happyranch") as parent:
                    try:
                        os.stat(name, dir_fd=parent, follow_symlinks=False)
                    except FileNotFoundError:
                        data = self._staging_data(name, transaction)
                    else:
                        data = None  # Full inventory already bound this completed effect.
                if data is not None:
                    # A crash before publication can lose generated UUID bytes.
                    # Rebind ONLY the still-absent current file, behind a new
                    # fsynced exact-generation intent. Existing files never change.
                    record["digests"][name] = hashlib.sha256(data).hexdigest()
                    updated = json.dumps(record, sort_keys=True).encode()
                    self._write_provision_file("install-transaction.json", updated, self.root_owner, 0o600,
                                               expected_transaction=transaction)
                    transaction = updated
                    owner = (self._service_owner if name == "daemon.token" else self.root_owner
                             if name in {"provision.json", "release-daemon.json"} else (self.root_owner[0], self._service_owner[1]))
                    mode = 0o640 if name in _QUARANTINE_CONFIGS else 0o644 if name == "release-daemon.json" else 0o600
                    self._write_provision_file(name, data, owner, mode,
                                               expected_transaction=transaction)
            next_index = 0 if phase == "replacement-units-removed" else staging_phases.index(phase) + 1
            record["phase"] = staging_phases[next_index]
            updated = json.dumps(record, sort_keys=True).encode()
            self._write_provision_file("install-transaction.json", updated, self.root_owner, 0o600,
                                       expected_transaction=transaction)
            transaction = updated
        raise InstallerError("replacement paused after fresh staging; retry ReplaceFailed to close transaction")

    def _close_replacement(self, transaction: bytes) -> None:
        """Keep the existing v1 payloads; retire the journal only after reload.

        Old quarantine destruction and final provenance publication already have
        individual fsynced intents. No file is regenerated or policy re-issued.
        """
        record = self._json(transaction)
        while record["phase"] != "replacement-retire-journal":
            if record["phase"] == "replacement-reload":
                with self._publication_parent() as (parent, revalidate):
                    self._check_replacement_transaction(parent, transaction)
                    self._replacement_custody(transaction)
                    inventory = self._inventory(prepared_transaction=transaction)
                    if inventory.state != "known-failed-or-partial":
                        raise InstallerError("replacement closure inventory changed: " + ", ".join(inventory.paths))
                    revalidate()
                    self.manager.daemon_reload()
            record["phase"] = _CLOSURE_PHASES[0] if record["phase"] == "replacement-staged" else _CLOSURE_PHASES[1]
            updated = json.dumps(record, sort_keys=True).encode()
            self._write_provision_file("install-transaction.json", updated, self.root_owner, 0o600,
                                       expected_transaction=transaction)
            transaction = updated
        with self._publication_parent() as (parent, revalidate):
            before = os.stat("install-transaction.json", dir_fd=parent, follow_symlinks=False)
            self._check_replacement_transaction(parent, transaction)
            self._replacement_custody(transaction)
            inventory = self._inventory(prepared_transaction=transaction)
            if inventory.state != "known-failed-or-partial":
                raise InstallerError("replacement closure inventory changed: " + ", ".join(inventory.paths))
            revalidate()
            after = os.stat("install-transaction.json", dir_fd=parent, follow_symlinks=False)
            fields = ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
            if any(getattr(before, field) != getattr(after, field) for field in fields):
                raise InstallerError(str(self.root / "etc/happyranch/install-transaction.json"))
            os.unlink("install-transaction.json", dir_fd=parent)
            os.fsync(parent)

    def _unit_wants_names(self) -> set[str]:
        try:
            with self._directory(f"{_UNIT_DIR}/{UNITS[2]}.wants") as parent:
                with os.scandir(parent) as entries:
                    names = set()
                    for index, entry in enumerate(entries):
                        if index >= 2:
                            raise InstallerError(str(self.root / _UNIT_DIR / (UNITS[2] + ".wants")))
                        names.add(entry.name)
                return names
        except FileNotFoundError:
            return set()

    def _remove_replacement_unit(self, transaction: bytes) -> None:
        """Remove one fixed unit, reserved link or EMPTY owned unit directory."""
        phase = self._json(transaction)["phase"]
        entry = next(((relative, kind) for intent, relative, kind in _UNIT_REMOVALS if intent == phase), None)
        if entry is None:
            raise InstallerError("unit removal requires the exact fixed-path intent")
        relative, kind = entry
        with self._publication_parent() as (journal_parent, revalidate):
            self._check_replacement_transaction(journal_parent, transaction)
            if self._inventory(prepared_transaction=transaction).state != "known-failed-or-partial":
                raise InstallerError("replacement inventory changed before unit removal")
            folder, name = str(Path(relative).parent), Path(relative).name
            try:
                with self._publication_parent(folder) as (parent, revalidate_removal):
                    try:
                        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    except FileNotFoundError:
                        revalidate()
                        revalidate_removal()
                        os.fsync(parent)
                        return
                    if (before.st_uid, before.st_gid) != self.root_owner:
                        raise InstallerError("unsafe unit removal owner")
                    self._check_replacement_member(relative, transaction)
                    if kind == "directory":
                        with self._directory(relative) as directory:
                            if os.listdir(directory) or (os.fstat(directory).st_dev, os.fstat(directory).st_ino) != (
                                    before.st_dev, before.st_ino):
                                raise InstallerError("unit removal directory changed or is not empty")
                    elif kind == "unit":
                        # Inventory already selected the exact known profile bytes.
                        self._read_unit(parent, name)
                    elif (not stat.S_ISLNK(before.st_mode) or before.st_nlink != 1
                          or os.readlink(name, dir_fd=parent) not in {f"../{name}", str(self.root / _UNIT_DIR / name)}):
                        raise InstallerError("unsafe unit removal link")
                    self._check_replacement_transaction(journal_parent, transaction)
                    release = self._replacement_release(transaction)
                    self._input(Path(release["data_home"]) / "daemon.token", token=True, source_uid=release["uid"])
                    if self._inventory(prepared_transaction=transaction).state != "known-failed-or-partial":
                        raise InstallerError("replacement inventory changed before unit effect")
                    self._confirm_quiescent()
                    self._check_replacement_member(relative, transaction)
                    revalidate()
                    revalidate_removal()
                    after = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    fields = ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink",
                              "st_size", "st_mtime_ns", "st_ctime_ns")
                    if any(getattr(before, field) != getattr(after, field) for field in fields):
                        raise InstallerError("unit removal entry changed")
                    (os.rmdir if kind == "directory" else os.unlink)(name, dir_fd=parent)
                    os.fsync(parent)
            except FileNotFoundError:
                # Only exact current intent absence is accepted by inventory.
                pass

    def _remove_replacement_entry(self, transaction: bytes) -> None:
        phase = self._json(transaction)["phase"]
        relative = dict((*_REPLACEMENT_REMOVALS, *_CONFIG_REMOVALS, *_STAGING_REMOVALS)).get(phase)
        if relative is None:
            raise InstallerError("removal requires the exact fixed-path intent")
        with self._publication_parent() as (journal_parent, revalidate):
            if self._inventory(prepared_transaction=transaction).state != "known-failed-or-partial":
                raise InstallerError("replacement inventory changed before removal")
            self._check_replacement_transaction(journal_parent, transaction)
            folder, name = str(Path(relative).parent), Path(relative).name
            try:
                with self._publication_parent(folder) as (parent, revalidate_removal):
                    try:
                        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    except FileNotFoundError:
                        # The exact current intent may have already unlinked it.
                        os.fsync(parent)
                        return
                    link = relative == f"{_CREDSTORE}/{_CREDENTIAL_NAME}"
                    owner = (self.root_owner if relative == _ENROLLMENT or link or phase in _CONFIG_REMOVAL_PHASES
                             or relative == "etc/happyranch/provision.json"
                             else self._service_owner)
                    if (before.st_nlink != 1 or (before.st_uid, before.st_gid) != owner
                            or (not link and (not stat.S_ISREG(before.st_mode) or stat.S_IMODE(before.st_mode) != 0o600))
                            or (link and (not stat.S_ISLNK(before.st_mode)
                                          or os.readlink(name, dir_fd=parent) != str(self.root / _ENROLLMENT)))):
                        raise InstallerError("unsafe replacement removal entry")
                    if phase not in (*_CONFIG_REMOVAL_PHASES, *_ALL_STAGING_PHASES):
                        self._check_replacement_member(relative, transaction)
                    # Opaque state is never read, interpreted, copied or restored.
                    # Repeat the full bounded inventory before this effect.
                    self._check_replacement_transaction(journal_parent, transaction)
                    if phase in _ALL_STAGING_PHASES:
                        self._replacement_custody(transaction)
                    if self._inventory(prepared_transaction=transaction).state != "known-failed-or-partial":
                        raise InstallerError("replacement inventory changed before unlink")
                    revalidate()
                    revalidate_removal()
                    after = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    fields = ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink",
                              "st_size", "st_mtime_ns", "st_ctime_ns")
                    if any(getattr(before, field) != getattr(after, field) for field in fields):
                        raise InstallerError("removal entry changed")
                    if self._json(transaction)["version"] == 2:
                        self._confirm_quiescent()
                        if phase not in (*_CONFIG_REMOVAL_PHASES, *_ALL_STAGING_PHASES):
                            self._check_replacement_member(relative, transaction)
                    os.unlink(name, dir_fd=parent)
                    os.fsync(parent)
            except FileNotFoundError:
                # Absent fixed state directories were permitted by admission.
                pass

    def _quarantine_config(self, name: str, transaction: bytes) -> None:
        """Tighten and move only a digest-bound fixed source; never overwrite."""
        record = self._json(transaction)
        if record["phase"] != _QUARANTINE_PHASES[_QUARANTINE_CONFIGS.index(name)]:
            raise InstallerError("quarantine requires the exact fixed-path intent")
        with self._publication_parent() as (parent, revalidate):
            if self._inventory(prepared_transaction=transaction).state != "known-failed-or-partial":
                raise InstallerError("replacement inventory changed before quarantine")
            self._check_replacement_transaction(parent, transaction)
            metadata = self._quarantine_metadata(record["phase"], record=record)
            quarantine = ".replacement-" + name
            if record["version"] == 2 and record["digests"][name] == "absent":
                return  # Missing at first admission; never manufacture an old digest.
            if quarantine in metadata:
                return  # The exact intent's rename already completed.
            owner, mode, _ = metadata[name]
            before = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if hashlib.sha256(self._read_file(parent, name, owner=owner, mode=mode)).hexdigest() != record["digests"][name]:
                raise InstallerError("quarantine source changed")
            descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent)
            try:
                info = os.fstat(descriptor)
                def signature(value: os.stat_result) -> tuple[int, ...]:
                    return (value.st_dev, value.st_ino, value.st_uid, value.st_gid,
                            value.st_mode, value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
                if signature(info) != signature(before):
                    raise InstallerError("quarantine source changed while opening")
                revalidate()
                self._check_replacement_transaction(parent, transaction)
                if signature(os.stat(name, dir_fd=parent, follow_symlinks=False)) != signature(info):
                    raise InstallerError("quarantine source changed before metadata update")
                revalidate()
                # Journal is durable before either metadata change or rename.
                os.fchmod(descriptor, 0o600)
                os.fchown(descriptor, *self.root_owner)
                os.fsync(descriptor)
                if hashlib.sha256(self._read_file(parent, name, owner=self.root_owner, mode=0o600)).hexdigest() != record["digests"][name]:
                    raise InstallerError("quarantine source changed before rename")
                named = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if (named.st_dev, named.st_ino) != (info.st_dev, info.st_ino):
                    raise InstallerError("quarantine source replaced before rename")
                self._check_replacement_transaction(parent, transaction)
                revalidate()
                self._rename_provision_entry(parent, name, quarantine)
                os.fsync(parent)
            finally:
                os.close(descriptor)

    def _check_replacement_member(self, relative: str, transaction: bytes) -> None:
        """V2 removes only an original fixed member, with its captured metadata."""
        record = self._json(transaction)
        if record["version"] == 1:
            return
        current = self._replacement_snapshot()[relative]
        if current != record["inventory"][relative]:
            raise InstallerError("replacement original member changed")

    def _check_replacement_transaction(self, parent: int, transaction: bytes) -> None:
        if self._read_file(parent, "install-transaction.json", owner=self.root_owner, mode=0o600) != transaction:
            raise InstallerError("replacement generation changed before mutation")
        record = self._json(transaction)
        if record["version"] == 2:
            self._replacement_custody(transaction)
        if record["phase"] in (*_ALL_STAGING_PHASES, *_CLOSURE_PHASES):
            return  # The staging consumer checks the complete phase-bound inventory.
        phase = ("replacement-configs-removed" if record["phase"] in _UNIT_REMOVAL_PHASES else
                 "replacement-quarantined" if record["phase"] in _REMOVAL_PHASES else record["phase"])
        configs = (self._quarantine_metadata(phase, record=record) if phase in (*_QUARANTINE_PHASES, *_CONFIG_REMOVAL_PHASES) else
                   {name: ((self.root_owner[0], self._service_owner[1]), 0o640, False)
                    for name in _QUARANTINE_CONFIGS})
        if record["version"] == 2 and phase not in (*_QUARANTINE_PHASES, *_CONFIG_REMOVAL_PHASES):
            for name in _QUARANTINE_CONFIGS:
                original = record["inventory"]["etc/happyranch/" + name]
                if isinstance(original, dict):
                    info = original["metadata"]
                    configs[name] = ((info[2], info[3]), stat.S_IMODE(info[4]), False)
        metadata = {**configs, "release-daemon.json": (self.root_owner, 0o644, False),
                    "provision.json": (self.root_owner, 0o600, False), "daemon.token": (self._service_owner, 0o600, False)}
        for name, digest in record["digests"].items():
            if digest == "absent":
                self._refuse_provision_entry(parent, name)
                continue
            if phase in _CONFIG_REMOVAL_PHASES and name in _QUARANTINE_CONFIGS and ".replacement-" + name not in configs:
                continue  # Exact current/completed intent absence was checked above.
            entry = ".replacement-" + name if ".replacement-" + name in configs else name
            owner, mode, _ = metadata[entry]
            if hashlib.sha256(self._read_file(parent, entry, owner=owner, mode=mode)).hexdigest() != digest:
                raise InstallerError("replacement fixed file changed before mutation")

    def stop_and_confirm(self, *, _replacement_transaction: bytes | None = None) -> InstallInventory:
        """Stop only recognized remote units and prove manager quiescence.

        No filesystem mutation or admission reopening is performed. Callers must
        not cache this sampled observation as later file-mutation authority.
        """
        inventory = self._inventory(prepared_transaction=_replacement_transaction)
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
        confirmed = self._inventory(prepared_transaction=_replacement_transaction)
        # An admitted failed manual installation can look intact after stopping.
        # Only the exact six-digest transaction consumer can cross that boundary.
        if (confirmed.state == "manual-install-conflict"
                or (confirmed.state == "healthy-manual" and _replacement_transaction is None)):
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

    def _headscale_materializer(self, verb: str) -> dict:
        # Trusted fixed package executable, never PATH or caller-supplied argv.
        binary = Path(headscale.MATERIALIZER)
        with self._directory(str(binary.parent)) as parent:
            info = os.stat(binary.name, dir_fd=parent, follow_symlinks=False)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or (info.st_uid, info.st_gid) != self.root_owner or stat.S_IMODE(info.st_mode) != 0o755):
                raise headscale.HeadscaleServiceError("materializer-uncertain")
        code, output = self._run(["/" + headscale.MATERIALIZER, verb], timeout=30.0)
        if code or type(output) is not str or len(output) > 4096 or len(output.splitlines()) != 1:
            raise headscale.HeadscaleServiceError("materializer-uncertain")
        result = self._json(output.encode())
        if (set(result) != {"ca_fingerprint", "ca_not_after", "leaf_not_after", "renewal_due"}
                or type(result["ca_fingerprint"]) is not str
                or re.fullmatch("[0-9a-f]{64}", result["ca_fingerprint"]) is None
                or type(result["renewal_due"]) is not bool):
            raise headscale.HeadscaleServiceError("materializer-uncertain")
        for name in ("ca_not_after", "leaf_not_after"):
            value = result[name]
            if value == "" and name == "leaf_not_after" and result["renewal_due"]:
                continue
            if type(value) is not str or re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value) is None:
                raise headscale.HeadscaleServiceError("materializer-uncertain")
            datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
        return result

    def _headscale_handoff(self, publication: dict | None) -> tuple[dict[str, bytes], bool]:
        """Materializer owns certificate/key validation; bind its public status."""
        try:
            sources = self._headscale_ca_sources()
            if publication and "directories" in publication:
                bindings = {"ca.pem": "/" + headscale.CONTROL_CA,
                            "leaf.pem": f"/{headscale.CUSTODY[0]}/server.crt",
                            "leaf.key": f"/{headscale.CUSTODY[0]}/server.key"}
                if sources is None or any(name not in sources or hashlib.sha256(sources[name]).hexdigest()
                                          != publication["files"][path] for name, path in bindings.items()):
                    raise ValueError
            status = self._headscale_materializer("status" if sources is not None else "ensure")
            if sources is not None:
                ca = sources["ca.pem"].decode("ascii")
                der = ssl.PEM_cert_to_DER_cert(ca)
                if (ssl.DER_cert_to_PEM_cert(der) != ca
                        or hashlib.sha256(der).hexdigest() != status["ca_fingerprint"]):
                    raise ValueError
            renewing = sources is not None and status["renewal_due"]
            before = status
            if sources is not None and (renewing or not publication or "directories" not in publication):
                status = self._headscale_materializer("renew-leaf" if renewing else "ensure")
                if (status["ca_fingerprint"], status["ca_not_after"]) != (
                        before["ca_fingerprint"], before["ca_not_after"]):
                    raise ValueError
            sources = self._headscale_ca_sources()
            if sources is None or status["renewal_due"] or not status["leaf_not_after"]:
                raise ValueError
            ca = sources["ca.pem"].decode("ascii")
            der = ssl.PEM_cert_to_DER_cert(ca)
            if (ssl.DER_cert_to_PEM_cert(der) != ca
                    or hashlib.sha256(der).hexdigest() != status["ca_fingerprint"]):
                raise ValueError
            if self._headscale_materializer("status") != status or self._headscale_ca_sources() != sources:
                raise ValueError
            files = {f"/{headscale.CUSTODY[0]}/server.crt": sources["leaf.pem"],
                     f"/{headscale.CUSTODY[0]}/server.key": sources["leaf.key"],
                     "/" + headscale.CONTROL_CA: sources["ca.pem"]}
            if publication and "directories" in publication:
                unchanged = ("/" + headscale.CONTROL_CA,) if renewing else tuple(files)
                if any(hashlib.sha256(files[path]).hexdigest() != publication["files"][path] for path in unchanged):
                    raise ValueError
            return files, renewing
        except (OSError, InstallerError, KeyError, UnicodeError, ValueError):
            pass
        raise headscale.HeadscaleServiceError("materializer-uncertain")

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
                            if b"happyranch-headscale" in data:
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
                    or self._headscale_owner[0] in map(int, fields)):
                raise headscale.HeadscaleServiceError("cleanup-uncertain")

    def _issuer_receipt(self) -> dict | None:
        try:
            with self._directory(headscale.INSTALLER_STATE) as parent:
                self._headscale_no_pending(parent, "home-issuer")
                self._headscale_no_pending(parent, _ISSUER_RECEIPT)
                data = self._read_file(parent, _ISSUER_RECEIPT, owner=self.root_owner, mode=0o600)
        except FileNotFoundError:
            return None
        record = self._json(data)
        if (record.keys() != {"version", "directory", "uid", "gid", "device", "inode"}
                or type(record["version"]) is not int or record["version"] != 1
                or record["directory"] != "/" + HOME_ISSUER
                or any(type(record[k]) is not int or record[k] < 0 for k in ("uid", "gid", "device", "inode"))
                or (record["uid"], record["gid"]) != self.root_owner):
            raise InstallerError("issuer receipt")
        return record

    @staticmethod
    def _issuer_signature(info: os.stat_result) -> tuple[int, ...]:
        return (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode,
                info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def _issuer_members(self, directory: int) -> dict[str, tuple[tuple[int, ...], bytes]]:
        names = set()
        with os.scandir(directory) as entries:
            for entry in entries:
                names.add(entry.name)
                if len(names) > 2 or entry.name not in {"invite-issuer.json", "invite-issuer.lock"}:
                    raise InstallerError("issuer residue")
        if names and names != {"invite-issuer.json", "invite-issuer.lock"}:
            raise InstallerError("issuer residue")
        members = {}
        for name in sorted(names):
            before = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if name == "invite-issuer.lock":
                fd = os.open(name, _FILE_FLAGS, dir_fd=directory)
                try:
                    info = os.fstat(fd)
                    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != 0
                            or (info.st_uid, info.st_gid) != self.root_owner
                            or stat.S_IMODE(info.st_mode) != 0o600
                            or self._issuer_signature(info) != self._issuer_signature(before)):
                        raise InstallerError("issuer lock")
                finally:
                    os.close(fd)
                data = b""
            else:
                data = self._read_file(directory, name, owner=self.root_owner, mode=0o600)
                record = self._json(data)
                if (len(data) > 4096
                        or record.keys() != {"operation_id", "binding_digest", "not_before", "expires_at", "outcome"}
                        or any(type(record[k]) is not str or re.fullmatch(pattern, record[k]) is None
                               for k, pattern in (("operation_id", "[0-9a-f]{32}"), ("binding_digest", "[0-9a-f]{64}")))
                        or type(record["outcome"]) is not str
                        or record["outcome"] not in {"issued", "refused", "expired", "encoding", "pairing", "recovered"}
                        or any(type(record[k]) not in (int, float) or not 0 <= record[k] < 253402300300
                               or not math.isfinite(record[k]) for k in ("not_before", "expires_at"))
                        or json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False).encode() != data):
                    raise InstallerError("issuer journal")
            if self._issuer_signature(os.stat(name, dir_fd=directory, follow_symlinks=False)) != self._issuer_signature(before):
                raise InstallerError("issuer changed")
            members[name] = self._issuer_signature(before), data
        return members

    def _issuer_inventory(self, *, cleanup: bool = False) -> tuple[dict | None, bool]:
        receipt = self._issuer_receipt()
        present = False
        try:
            with self._directory(headscale.INSTALLER_STATE) as parent:
                os.stat("home-issuer", dir_fd=parent, follow_symlinks=False)
                present = True
        except FileNotFoundError:
            pass
        if not present:
            # Retained cleanup receipts never license recreating an old directory.
            if receipt is not None and not cleanup:
                raise InstallerError("issuer directory missing") from None
            return receipt, False
        with self._directory(HOME_ISSUER) as directory:
            info = os.fstat(directory)
            if info.st_nlink != 2:
                raise InstallerError("issuer directory")
            members = self._issuer_members(directory)
            if "invite-issuer.lock" in members:
                lock = os.open("invite-issuer.lock", _FILE_FLAGS, dir_fd=directory)
                try:
                    if self._issuer_signature(os.fstat(lock)) != members["invite-issuer.lock"][0]:
                        raise InstallerError("issuer lock changed")
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(lock)
        if receipt is None:
            if cleanup or members:
                raise InstallerError("issuer directory unreceipted")
        elif (receipt["device"], receipt["inode"]) != (info.st_dev, info.st_ino):
            raise InstallerError("issuer directory changed")
        return receipt, True

    def _prepare_issuer(self) -> None:
        receipt, present = self._issuer_inventory()
        if not present:
            with self._publication_parent(headscale.INSTALLER_STATE) as (parent, revalidate):
                self._refuse_provision_entry(parent, "home-issuer")
                os.mkdir(".home-issuer.pending", mode=0o700, dir_fd=parent)
                fd = os.open(".home-issuer.pending", _DIR_FLAGS, dir_fd=parent)
                try:
                    os.fchown(fd, *self.root_owner)
                    os.fchmod(fd, 0o700)
                    os.fsync(fd)
                    info = os.fstat(fd)
                    self._check_directory(fd, HOME_ISSUER, (0o700, self.root_owner))
                    if (os.stat(".home-issuer.pending", dir_fd=parent, follow_symlinks=False) != info
                            or info.st_nlink != 2 or os.listdir(fd)):
                        raise InstallerError("issuer pending changed")
                    revalidate()
                    self._rename_provision_entry(parent, ".home-issuer.pending", "home-issuer")
                    os.fsync(parent)
                finally:
                    os.close(fd)
        with self._publication_parent(HOME_ISSUER) as (directory, revalidate):
            info = os.fstat(directory)
            record = dict(version=1, directory="/" + HOME_ISSUER, uid=self.root_owner[0],
                          gid=self.root_owner[1], device=info.st_dev, inode=info.st_ino)
            self._issuer_members(directory)
            revalidate()
            if receipt is None:
                self._write_provision_file(_ISSUER_RECEIPT, json.dumps(record, sort_keys=True).encode(),
                                           self.root_owner, 0o600, relative=headscale.INSTALLER_STATE)
            elif receipt != record:
                raise InstallerError("issuer receipt changed")
            revalidate()
            self._issuer_inventory()

    def _cleanup_issuer(self) -> None:
        partial = False
        lock = None
        try:
            receipt, present = self._issuer_inventory(cleanup=True)
            if not present:
                return
            with self._publication_parent(HOME_ISSUER) as (directory, revalidate):
                info = os.fstat(directory)
                if receipt is None or (receipt["device"], receipt["inode"]) != (info.st_dev, info.st_ino):
                    raise InstallerError("issuer directory changed")
                members = self._issuer_members(directory)
                if "invite-issuer.lock" in members:
                    lock = os.open("invite-issuer.lock", _FILE_FLAGS, dir_fd=directory)
                    if self._issuer_signature(os.fstat(lock)) != members["invite-issuer.lock"][0]:
                        raise InstallerError("issuer lock changed")
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if self._issuer_receipt() != receipt or self._issuer_members(directory) != members:
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

    def _cleanup_home_headscale(
        self, release_uid: int, home_id: str, control_plane_id: str, connector_id: str,
        generation: int,
    ) -> dict[str, str]:
        """Unmounted B6 executor. The lifecycle caller supplies trusted expectations.

        The caller stops all owned services first. The activation-directory lock
        serializes existing cooperative activation writers; sampled native state
        and literal parents are freshly checked before every destructive effect.
        No authentication, stop, admission revocation or public lifecycle is added.
        """
        # Callable-local imports avoid the existing activation -> installer edge.
        from runtime.remote_access import home_activation as activation
        from runtime.remote_access.home_activation_txn import HomeActivationTransactionStore
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
            store = activation.HomeActivationStore(root=self.root, root_owner=self.root_owner)
            transactions = HomeActivationTransactionStore(root=self.root, root_owner=self.root_owner)
            with transactions.locked():
                current = store.read(expected_generation=generation)
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
                for path in activation.LIVE_MEMBERS:
                    kind = ("directory" if path.endswith("/acme-unused") else
                            "socket" if path.endswith(".sock") else "file")
                    owner = (self._service_owner if path.endswith("/observe.sock") else
                             (self._headscale_owner[0], None) if kind == "directory" else self._headscale_owner)
                    members[path] = kind, owner, 0o700 if kind == "directory" else 0o600, None
                custody = self._headscale_members()
                for path, digest in current.member_digests.items():
                    owner, mode = custody.get(path, (self.root_owner, 0o600))
                    if path == activation.OWNED_LINK_TARGET:
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
                # B6 grants only the signing-key exception to D's preservation
                # of root-only CA staging. Other entries remain incomplete.
                # CA private bytes and sockets are never opened/read/connected.
                members[f"/{headscale.HOME_CA}/ca.key"] = "file", self.root_owner, 0o600, None

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
                                    if (activation._signature(os.fstat(child)) != activation._signature(info)
                                            or nonempty):
                                        raise InstallerError("live directory not empty")
                                finally:
                                    os.close(child)
                            elif digest is not None:
                                data = self._read_file(parent, relative.name, owner=owner, mode=mode)
                                if hashlib.sha256(data).hexdigest() != digest:
                                    raise InstallerError("live member digest")
                            check()
                            if activation._signature(os.stat(relative.name, dir_fd=parent,
                                                              follow_symlinks=False)) != activation._signature(info):
                                raise InstallerError("live member changed")
                            if remove:
                                require()
                                inventory()
                                check()
                                if activation._signature(os.stat(relative.name, dir_fd=parent,
                                                                  follow_symlinks=False)) != activation._signature(info):
                                    raise InstallerError("live member changed before removal")
                                partial = True  # An attempted unlink/fsync can have an uncertain outcome.
                                (os.rmdir if kind == "directory" else os.unlink)(relative.name, dir_fd=parent)
                                os.fsync(parent)
                            return True
                    except FileNotFoundError:
                        return False

                def inventory() -> None:
                    # Shallow fixed custody only; never recurse into an unknown entry.
                    for directory in headscale.CUSTODY:
                        try:
                            with self._publication_parent(directory) as (parent, check):
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

                # B7/B11 currently expose no cleanup proof + audit retirement
                # owner. Retain all fences, issuer journals and txn evidence.
                deferred = False
                with self._publication_parent(headscale.INSTALLER_STATE) as (parent, check):
                    with os.scandir(parent) as entries:
                        for index, item in enumerate(entries):
                            if index >= 64:
                                raise InstallerError("cleanup evidence inventory")
                            if item.name not in {Path(activation.ACTIVATION_PATH).name,
                                                 headscale.RECEIPT, headscale.PUBLICATION, "home-ca",
                                                 "home-issuer", _ISSUER_RECEIPT}:
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
                    raise InstallerError("unlisted custody or B7/B11 retirement deferred")
                require()
                self._cleanup_issuer()  # Empty, receipted issuer only; never a journal here.
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
                for directory in (*headscale.CUSTODY, headscale.HOME_CA, "run/happyranch-tsnet-sidecar"):
                    require()
                    inventory()
                    relative = Path(directory)
                    try:
                        with self._publication_parent(directory) as (opened, check):
                            with os.scandir(opened) as entries:
                                if next(entries, None) is not None:
                                    raise InstallerError("custody not empty")
                            with self._publication_parent(str(relative.parent)) as (parent, parent_check):
                                named = os.stat(relative.name, dir_fd=parent, follow_symlinks=False)
                                if activation._signature(named) != activation._signature(os.fstat(opened)):
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
                retire += [Path(activation.ACTIVATION_PATH).name]
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
                        elif name == Path(activation.ACTIVATION_PATH).name:
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
                headscale.HeadscaleServiceError):
            pass
        finally:
            self._headscale_preparing = False
        raise headscale.HeadscaleServiceError("partial-cleanup-uncertain" if partial else "cleanup-uncertain")

    def cleanup_headscale(
        self, *, expected_release_uid: int | None = None, expected_home_id: str | None = None,
        expected_control_plane_id: str | None = None, expected_connector_id: str | None = None,
        expected_generation: int | None = None,
    ) -> dict[str, str]:
        """Remove only proven custody; explicit trusted expectations select B6.

        Config/DERP need their final publication receipt. DB/key/socket entries
        remain foreign without B6. No CLI/uninstall caller supplies expectations;
        their ruling D behavior, including CA preservation, remains unchanged.
        """
        expected = (expected_release_uid, expected_home_id, expected_control_plane_id,
                    expected_connector_id, expected_generation)
        if any(value is not None for value in expected):
            return self._cleanup_home_headscale(*expected)
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

    def _headscale_publish_empty_ca(self) -> None:
        path = Path(headscale.EMPTY_CA)
        with self._publication_parent(str(path.parent)) as (parent, revalidate):
            self._refuse_provision_entry(parent, path.name)
            pending = "." + path.name + ".pending"
            os.mkdir(pending, mode=0o700, dir_fd=parent)
            descriptor = os.open(pending, _DIR_FLAGS, dir_fd=parent)
            try:
                os.fchown(descriptor, *self.root_owner)
                os.fchmod(descriptor, 0o755)
                os.fsync(descriptor)
                named, info = os.stat(pending, dir_fd=parent, follow_symlinks=False), os.fstat(descriptor)
                if ((named.st_dev, named.st_ino) != (info.st_dev, info.st_ino)
                        or (info.st_uid, info.st_gid, info.st_mode, info.st_nlink) !=
                        (*self.root_owner, stat.S_IFDIR | 0o755, 2) or os.listdir(descriptor)):
                    raise headscale.HeadscaleServiceError("publication-uncertain")
                revalidate()
                self._rename_provision_entry(parent, pending, path.name)
                os.fsync(parent)
            finally:
                os.close(descriptor)

    def prepare_headscale(self) -> dict[str, str]:
        """Explicit inert root-step, separate from all connector activation.

        Never runs Headscale or a service command. Existing accounts are never
        modified or adopted; cleanup separately proves deletion authority.
        """
        if self.layout != "deb":
            raise headscale.HeadscaleServiceError("deb-layout-required")
        self._headscale_preparing = True
        category = None
        try:
            try:
                self._issuer_inventory()
            except (OSError, InstallerError, ValueError, UnicodeError, headscale.HeadscaleServiceError):
                raise headscale.HeadscaleServiceError("issuer-state-uncertain") from None
            self._headscale_owner = self._headscale_shape()
            receipt = self._headscale_receipt()
            if receipt and self._headscale_owner != (receipt["uid"], receipt["gid"]):
                raise headscale.HeadscaleServiceError("account-receipt-uncertain")
            self._headscale_inventory()
            dial_ip = home_headscale.select_lan_dial_ip(*headscale.collect_lan_inventory())
            publication = self._headscale_publication()
            if publication and publication["dial_ip"] != dial_ip:
                raise headscale.HeadscaleServiceError("address-changed")
            headscale.preflight_ports(dial_ip)
            outcome = "owned" if receipt else "unowned"
            if self._headscale_owner is None:
                try:
                    self._lookup_group(headscale.ACCOUNT)
                except KeyError:
                    pass
                else:
                    raise headscale.HeadscaleServiceError("account-shape")
                with self._directory(headscale.INSTALLER_STATE, create=True):
                    pass
                # No pre-useradd intent. A crash before receipt publication leaves
                # an unowned account, never later adoption/deletion authority.
                code, _ = self._run([
                    "useradd", "--system", "--user-group", "--no-create-home",
                    "--home-dir", "/nonexistent", "--shell", "/usr/sbin/nologin", headscale.ACCOUNT,
                ], timeout=30.0)
                if code:
                    raise headscale.HeadscaleServiceError("account-create-uncertain")
                self._headscale_owner = self._headscale_shape()
                if self._headscale_owner is None:
                    raise headscale.HeadscaleServiceError("account-create-uncertain")
                uid, gid = self._headscale_owner
                record = dict(version=1, account=headscale.ACCOUNT, uid=uid, gid=gid, group_created_with_user=True)
                self._write_provision_file(headscale.RECEIPT, json.dumps(record, sort_keys=True).encode(),
                                           self.root_owner, 0o600, relative=headscale.INSTALLER_STATE)
                outcome = "created"
            # No running process may use this UID while we publish service
            # custody. This observes only; preparation never stops a service.
            self._headscale_stopped()
            with self._directory(headscale.INSTALLER_STATE, create=True):
                pass
            for path in headscale.CUSTODY:
                with self._directory(path, create=True):
                    pass
            for relative, name, data, owner, mode in (
                (headscale.CUSTODY[0], "policy.hujson", headscale.render_policy(), self._headscale_owner, 0o600),
                (_UNIT_DIR, headscale.UNIT, render_headscale_unit().encode(), self.root_owner, 0o644),
            ):
                with self._directory(relative, create=True) as parent:
                    try:
                        existing = self._read_file(parent, name, owner=owner, mode=mode)
                    except FileNotFoundError:
                        existing = None
                    if existing is not None and existing != data:
                        raise headscale.HeadscaleServiceError("foreign-custody")
                if existing is None:
                    self._write_provision_file(name, data, owner, mode, relative=relative)
            handoff, renewing = self._headscale_handoff(publication)
            extended = publication is not None and "directories" in publication
            if publication is None or not extended or renewing:
                removed: tuple[str, ...] = ()
                if publication:
                    if renewing and extended:
                        for name in ("server.key", "server.crt"):
                            self._headscale_stopped()
                            self._headscale_inventory(removed)
                            if self._headscale_publication(removed) != publication:
                                raise headscale.HeadscaleServiceError("publication-uncertain")
                            self._headscale_remove_member(publication, name)
                            removed += (name,)
                    self._headscale_stopped()
                    self._headscale_inventory(removed)
                    if self._headscale_publication(removed) != publication:
                        raise headscale.HeadscaleServiceError("publication-uncertain")
                    with self._publication_parent(headscale.INSTALLER_STATE) as (parent, revalidate):
                        if self._json(self._read_file(parent, headscale.PUBLICATION,
                                                      owner=self.root_owner, mode=0o600)) != publication:
                            raise headscale.HeadscaleServiceError("publication-uncertain")
                        revalidate()
                        os.unlink(headscale.PUBLICATION, dir_fd=parent)
                        os.fsync(parent)
                files = {f"/{headscale.CUSTODY[0]}/{name}": data
                         for name, data in self._headscale_config(dial_ip).items()} | handoff
                members = self._headscale_members()
                # Key is durable before cert; no published certificate precedes its key.
                order = (*headscale.CONFIG_FILES, "server.key", "server.crt", "control-ca.pem")
                for name in order:
                    path = next(path for path in files if Path(path).name == name)
                    if publication and path in publication["files"] and name not in removed:
                        continue
                    self._headscale_stopped()
                    self._write_provision_file(name, files[path], *members[path],
                                               relative=str(Path(path.lstrip("/")).parent))
                if not extended:
                    self._headscale_publish_empty_ca()
                uid, gid = self._headscale_owner
                record = dict(version=1, uid=uid, gid=gid, dial_ip=dial_ip,
                              files={path: hashlib.sha256(data).hexdigest() for path, data in files.items()},
                              directories=["/" + headscale.EMPTY_CA])
                self._headscale_stopped()
                self._write_provision_file(headscale.PUBLICATION, json.dumps(record, sort_keys=True).encode(),
                                           self.root_owner, 0o600, relative=headscale.INSTALLER_STATE)
            self._headscale_inventory()
            try:
                self._prepare_issuer()
            except (OSError, InstallerError, ValueError, UnicodeError, headscale.HeadscaleServiceError):
                raise headscale.HeadscaleServiceError("issuer-state-uncertain") from None
            return {"state": "inert", "account": outcome,
                    "preservation": "preserved: not created by HappyRanch" if outcome == "unowned" else "receipt-recorded"}
        except headscale.HeadscaleServiceError as error:
            category = error.category
        except home_headscale.HomeHeadscaleError as error:
            category = str(error)
        except (OSError, InstallerError, KeyError, UnicodeError, ValueError, ServiceManagerError):
            category = "custody-uncertain"
        finally:
            self._headscale_preparing = False
        # Leave native handlers before raising; no raw output/error context.
        raise headscale.HeadscaleServiceError(category)

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
            layout[headscale.EMPTY_CA] = (0o755, self.root_owner)
            for path in headscale.CUSTODY:
                layout[path] = (0o700, self._headscale_owner)
        return layout

    @contextmanager
    def _directory(self, relative: str = "", *, create: bool = False) -> Iterator[int]:
        """Walk literal parents with directory descriptors, never symlinks."""
        descriptor = os.open("/", _DIR_FLAGS)
        try:
            for part in self.root.parts[1:]:
                child = os.open(part, _DIR_FLAGS, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            self._check_directory(descriptor, str(self.root), None)
            layout = self._layout()
            parts = Path(relative).parts
            for index, part in enumerate(parts):
                prefix = str(Path(*parts[:index + 1]))
                expected = layout.get(prefix)
                try:
                    child = os.open(part, _DIR_FLAGS, dir_fd=descriptor)
                except FileNotFoundError:
                    if not create:
                        raise
                    mode, owner = expected or (0o755, self.root_owner)
                    if owner is None:
                        raise InstallerError("cannot create service directories without a validated happyranch account")
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                    child = os.open(part, _DIR_FLAGS, dir_fd=descriptor)
                    try:
                        os.fchown(child, *owner)
                        os.fchmod(child, mode)
                    except BaseException:
                        os.close(child)
                        raise
                os.close(descriptor)
                descriptor = child
                self._check_directory(descriptor, prefix, expected)
            yield descriptor
        finally:
            os.close(descriptor)

    def _check_directory(
        self, descriptor: int, path: str, expected: tuple[int, tuple[int, int] | None] | None,
    ) -> None:
        info = os.fstat(descriptor)
        if (expected is None and path == "var/log" and info.st_uid == self.root_owner[0]
                and not info.st_mode & 0o002):
            # Ubuntu's root:syslog 0775 ancestor trusts syslog members for log
            # entries. Never extend that trust to managed directories/parents.
            try:
                log_gid = self._lookup_group("syslog").gr_gid
            except KeyError:
                pass
            else:
                if info.st_gid == log_gid:
                    return
        owner = expected[1] if expected else self.root_owner
        allowed_modes = (expected[0],) if expected else ()
        if expected and path == _CREDSTORE:
            allowed_modes += (0o755,)
        if expected and path in {_STATE_DIRS[0], "run/happyranch-connector"}:
            # Creation follows systemd's mode; the trust-state store tightens
            # state on save; the identity unit also tightens its runtime dir.
            # Preserve either known safe mode without changing ownership.
            allowed_modes += (0o700,)
        if ((owner is not None and (info.st_uid, info.st_gid) != owner) or info.st_mode & 0o022
                or (expected and stat.S_IMODE(info.st_mode) not in allowed_modes)):
            raise InstallerError(f"unsafe directory ownership or mode: {path}")
        if owner is None:
            raise InstallerError(f"cannot validate existing service directory ownership without a happyranch account: {path}")

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

    @staticmethod
    def _read_file(
        parent: int, name: str, *, owner: tuple[int, int] | None = None,
        mode: int | None = None, source_uid: int | None = None,
    ) -> bytes:
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(named.st_mode):
            raise InstallerError("unsafe provisioning file type")
        descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent)
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 65536
                    or info.st_mode & 0o022 or (owner is not None and (info.st_uid, info.st_gid) != owner)
                    or (source_uid is not None and info.st_uid != source_uid)
                    or (mode is not None and stat.S_IMODE(info.st_mode) != mode)
                    or (named.st_dev, named.st_ino) != (info.st_dev, info.st_ino)):
                raise InstallerError("unsafe provisioning file ownership, mode, links or size")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                data = stream.read(65536)
            after = os.fstat(descriptor)
            named_after = os.stat(name, dir_fd=parent, follow_symlinks=False)
            def signature(value: os.stat_result) -> tuple[int, ...]:
                return (value.st_dev, value.st_ino, value.st_uid, value.st_gid, value.st_mode,
                        value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
            if (len(data) > 65536 or not data.strip()
                    or len(data) != info.st_size or signature(named) != signature(info)
                    or signature(info) != signature(after) or signature(info) != signature(named_after)):
                raise InstallerError("empty, oversized or changing provisioning file")
            return data
        finally:
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

    @staticmethod
    def _json(data: bytes) -> dict:
        def pairs(items: list[tuple[str, object]]) -> dict:
            result: dict = {}
            for key, value in items:
                if key in result:
                    raise ValueError
                result[key] = value
            return result
        try:
            value = json.loads(data, object_pairs_hook=pairs)
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (ValueError, UnicodeError):
            raise InstallerError("invalid provisioning JSON object") from None

    def _validate_configs(self, files: dict[str, bytes], *, staged: bool = False, partial: bool = False,
                          require_current: bool = True) -> None:
        # Reuse shipping connector/policy validators; never start a supervisor.
        # Only inert inventory/replacement recovery omits currentness. Schema,
        # artifact and cross-config checks still apply; admission defaults closed.
        from runtime.remote_access.managed_provider import ManagedProviderConfig
        from runtime.remote_access.policy import PolicyEnvelope, RoutePolicyConsumer
        from runtime.remote_access.supervisor import ConnectorConfig

        try:
            cfg = None
            if not partial or "connector.json" in files:
                raw = self._json(files["connector.json"])
                managed = raw.pop("managed")
                if not isinstance(managed, dict) or raw.get("lab") is not None or raw.get("diy") is not None:
                    raise ValueError
                cfg = ConnectorConfig(**raw, managed=ManagedProviderConfig(**managed))
                cfg.validate()
                if (any(not isinstance(value, str) or not value.strip() for value in (cfg.tenant_id, cfg.home_id, cfg.connector_id))
                        or cfg.system is not True or cfg.service_user != "happyranch" or cfg.service_group != "happyranch"
                        or cfg.daemon_port is not None
                        or not 1 <= cfg.managed.bind_port <= 65535
                        or cfg.daemon_token_path != "/etc/happyranch/daemon.token"
                        or cfg.policy_path != "/etc/happyranch/policy.json"
                        or cfg.state_path != "/var/lib/happyranch-connector/trust-state.json"
                        or cfg.credentials_directory is not None or cfg.exec_start is not None
                        or cfg.managed_dir_root is not None or cfg.unit_name != UNITS[0]
                        or any(value != "happyranch-connector" for value in (cfg.state_dir, cfg.run_dir, cfg.logs_dir))
                        or any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0
                               for value in (cfg.watchdog_sec, cfg.poll_seconds, cfg.restart_sec))):
                    raise ValueError
                if (cfg.managed.profile == "linux-managed-identity-v1"
                        and (cfg.excluded_daemon_ports != [8765] or not cfg.managed.control_plane_id)):
                    raise ValueError
            if not partial or "policy.json" in files:
                policy = RoutePolicyConsumer.from_envelope(PolicyEnvelope(**self._json(files["policy.json"])))
                if require_current:
                    policy.require_current(datetime.now(timezone.utc))
            if partial and "sidecar.json" not in files:
                return
            sidecar = self._json(files["sidecar.json"])
            profile = cfg.managed.profile if cfg is not None else sidecar.get("Profile")
            if profile == "linux-managed-identity-v1":
                if not staged and "home_binding" in sidecar:
                    from runtime.remote_access.home_admission import HomeProfileAdmission
                    admitted = HomeProfileAdmission(self).require()
                    if any(admitted.files.get(name) != data for name, data in files.items()):
                        raise ValueError
                    return
                # An inert staged bundle, not a runnable home transport. In
                # particular, never publish a URL without its protected CA.
                if (sidecar.keys() != {"Profile", "StateDir", "RoleIdentity", "DERPPolicy",
                                      "ListenAddr", "ExcludedDaemonPorts"}
                        or sidecar["Profile"] != profile
                        or sidecar["StateDir"] != "/var/lib/happyranch-tsnet-sidecar"
                        or not isinstance(sidecar["RoleIdentity"], str)
                        or not sidecar["RoleIdentity"].startswith("home-sidecar-")
                        or len(sidecar["RoleIdentity"]) <= len("home-sidecar-")
                        or (cfg is not None and sidecar["RoleIdentity"] != f"home-sidecar-{cfg.home_id}")
                        or sidecar["DERPPolicy"] != "private-only" or sidecar["ListenAddr"] != ":443"
                        or sidecar["ExcludedDaemonPorts"] != [8765]):
                    raise ValueError
                return
            required = {"StateDir", "ControlURL", "RoleIdentity", "ExpectedPeer", "DERPPolicy", "ListenAddr", "ConnectorAddr"}
            if (not required <= sidecar.keys() <= required | {"ExcludedDaemonPorts"}
                    or any(not isinstance(sidecar[key], str) for key in required)):
                raise ValueError
            sidecar_excluded = sidecar.get("ExcludedDaemonPorts", [])
            if (not isinstance(sidecar_excluded, list)
                    or any(type(port) is not int or not 1 <= port <= 65535 for port in sidecar_excluded)):
                raise ValueError
            excluded = sorted({8765, *(cfg.excluded_daemon_ports if cfg is not None else []), *sidecar_excluded})
            if cfg is not None and (cfg.managed.bind_port in excluded
                    or sidecar["ConnectorAddr"] != f"127.0.0.1:{cfg.managed.bind_port}"):
                raise ValueError
            if (cfg is not None and staged and self.layout == "deb"
                    and (cfg.excluded_daemon_ports != excluded or sidecar_excluded != excluded)):
                raise ValueError
            control = urlsplit(sidecar["ControlURL"])
            listen = re.fullmatch(r"(?:\[[^\]]+\]|[^:]*):([0-9]+)", sidecar["ListenAddr"])
            ingress = re.fullmatch(r"127\.0\.0\.1:([0-9]+)", sidecar["ConnectorAddr"])
            if (control.scheme != "https" or not control.hostname or control.username is not None
                    or control.password is not None or control.hostname.lower() == "controlplane.tailscale.com"
                    or not sidecar["RoleIdentity"].startswith("home-sidecar-")
                    or len(sidecar["RoleIdentity"]) <= len("home-sidecar-")
                    or not sidecar["ExpectedPeer"].strip() or sidecar["DERPPolicy"] != "private-only"
                    or sidecar["StateDir"] != "/var/lib/happyranch-tsnet-sidecar"
                    or listen is None or not 1 <= int(listen[1]) <= 65535
                    or ingress is None or not 1 <= int(ingress[1]) <= 65535 or int(ingress[1]) in excluded):
                raise ValueError
        except Exception:
            # Validators can include input values. Keep all diagnostics secret-free.
            raise InstallerError("invalid connector, sidecar or current policy provisioning") from None

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

    @contextmanager
    def _publication_parent(self, relative: str = "etc/happyranch") -> Iterator[tuple[int, Callable[[], None]]]:
        """Pin every literal ancestor until the last pre-rename validation."""
        descriptor = os.open("/", _DIR_FLAGS)
        opened = [descriptor]
        parents = []
        layout = self._layout()
        path = Path("/")
        try:
            for part in (self.root / relative).parts[1:]:
                named = os.stat(part, dir_fd=descriptor, follow_symlinks=False)
                child = os.open(part, _DIR_FLAGS, dir_fd=descriptor)
                opened.append(child)
                info = os.fstat(child)
                if (named.st_dev, named.st_ino) != (info.st_dev, info.st_ino):
                    raise InstallerError("publication parent changed while opening")
                path /= part
                managed_relative = str(path.relative_to(self.root)) if path.is_relative_to(self.root) else None
                if managed_relative is not None:
                    self._check_directory(child, str(self.root) if managed_relative == "." else managed_relative,
                                          layout.get(managed_relative))
                parents.append((descriptor, part, child, info))
                descriptor = child

            def revalidate() -> None:
                try:
                    for ancestor, name, child, before in parents:
                        named = os.stat(name, dir_fd=ancestor, follow_symlinks=False)
                        current = os.fstat(child)
                        expected = (before.st_dev, before.st_ino, before.st_uid, before.st_gid, before.st_mode)
                        for info in (named, current):
                            if ((info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode) != expected
                                    or not stat.S_ISDIR(info.st_mode)):
                                raise InstallerError("publication parent changed before rename")
                except OSError:
                    raise InstallerError("publication parent unavailable before rename") from None

            yield descriptor, revalidate
        finally:
            for descriptor in reversed(opened):
                os.close(descriptor)

    @staticmethod
    def _refuse_provision_entry(parent: int, name: str) -> None:
        for entry in (name, f".{name}.pending"):
            try:
                os.stat(entry, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise InstallerError("provisioning destination or pending entry exists; inspect partial files before retrying")

    def _write_provision_file(self, name: str, data: bytes, owner: tuple[int, int], mode: int,
                              *, expected_transaction: bytes | None = None, relative: str = "etc/happyranch") -> None:
        # Fresh files never overwrite. Only the closed linear replacement
        # transitions can update a final entry, through the same atomic writer.
        metadata = {
            **{key: ((self.root_owner[0], self._service_owner[1]), 0o640)
               for key in ("connector.json", "sidecar.json", "policy.json")
               if self._service_owner is not None},
            "release-daemon.json": (self.root_owner, 0o644),
            "daemon.token": (self._service_owner, 0o600),
            "provision.json": (self.root_owner, 0o600),
            "install-transaction.json": (self.root_owner, 0o600),
        }
        if self._headscale_preparing and relative == "etc/happyranch" and expected_transaction is None:
            metadata["control-ca.pem"] = self._headscale_members()["/" + headscale.CONTROL_CA]
        if relative != "etc/happyranch":
            metadata = {
                headscale.INSTALLER_STATE: {name: (self.root_owner, 0o600)
                                            for name in (headscale.RECEIPT, headscale.PUBLICATION, _ISSUER_RECEIPT)},
                headscale.CUSTODY[0]: {name: (self._headscale_owner, 0o600)
                                       for name in ("policy.hujson", *headscale.CONFIG_FILES, *headscale.LEAF_FILES)},
                _UNIT_DIR: {headscale.UNIT: (self.root_owner, 0o644)},
            }.get(relative, {}) if self._headscale_preparing and expected_transaction is None else {}
        if (name not in metadata or metadata[name] != (owner, mode)
                or owner is None or not 0 < len(data) <= 65536):
            raise InstallerError("invalid provisioning publication")
        replacement = expected_transaction is not None
        updating = replacement and name == "install-transaction.json"
        rebinding = False
        if replacement:
            self._validate_install_record("install-transaction.json", expected_transaction, replacement=True)
        if updating:
            self._validate_install_record(name, data, replacement=True)
            expected_record = self._json(expected_transaction)
            phases = _replacement_phases(expected_record)
            previous = phases.index(expected_record["phase"])
            if previous + 1 == len(phases):
                raise InstallerError("replacement retirement intent has no successor")
            updated_record = self._json(data)
            staged_name = dict(_replacement_staging(expected_record)[0]).get(expected_record["phase"])
            rebinding = staged_name is not None and updated_record["phase"] == expected_record["phase"]
            if rebinding:
                expected_record["digests"][staged_name] = updated_record["digests"][staged_name]
            else:
                expected_record["phase"] = phases[previous + 1]
            if updated_record != expected_record:
                raise InstallerError("replacement update must preserve the exact six digests")
        elif replacement:
            record = self._json(expected_transaction)
            if (dict(_replacement_staging(record)[0]).get(record["phase"]) != name
                    or hashlib.sha256(data).hexdigest() != record["digests"][name]):
                raise InstallerError("replacement publication requires its exact staging intent")
            if name == "provision.json":
                self._validate_install_record(name, data)
        elif name in ("provision.json", "install-transaction.json"):
            self._validate_install_record(name, data)
        with self._publication_parent(relative) as (parent, revalidate):
            if replacement:
                if self._json(expected_transaction)["version"] == 2:
                    self._replacement_custody(expected_transaction)
                if self._inventory(prepared_transaction=expected_transaction).state == "manual-install-conflict":
                    raise InstallerError("replacement inventory changed before intent publication")
                if self._json(expected_transaction)["phase"] in ("replacement-configs-removed", *_UNIT_REMOVAL_PHASES,
                                                                  *_ALL_STAGING_PHASES, *_CLOSURE_PHASES):
                    token = self._replacement_custody(expected_transaction)
                    if name == "daemon.token" and token != data:
                        raise InstallerError("release token changed before staging")
                if self._read_file(parent, "install-transaction.json", owner=self.root_owner, mode=0o600) != expected_transaction:
                    raise InstallerError("replacement journal changed before intent publication")
                if rebinding:
                    self._refuse_provision_entry(parent, staged_name)
                if not updating:
                    self._refuse_provision_entry(parent, name)
                try:
                    os.stat(f".{name}.pending", dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise InstallerError("replacement pending entry exists")
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
                if replacement:
                    if self._read_file(parent, "install-transaction.json", owner=self.root_owner, mode=0o600) != expected_transaction:
                        raise InstallerError("replacement journal changed before rename")
                    self._check_replacement_transaction(parent, expected_transaction)
                    if self._json(expected_transaction)["phase"] in ("replacement-units-removed", *_ALL_STAGING_PHASES, *_CLOSURE_PHASES):
                        token = self._replacement_custody(expected_transaction)
                        if name == "daemon.token" and token != data:
                            raise InstallerError("release token changed before staging rename")
                        if self._inventory(prepared_transaction=expected_transaction,
                                           pending=(name, data)).state != "known-failed-or-partial":
                            raise InstallerError("replacement staging inventory changed before rename")
                        if rebinding:
                            self._refuse_provision_entry(parent, staged_name)
                        named = os.stat(pending, dir_fd=parent, follow_symlinks=False)
                        if ((named.st_dev, named.st_ino) != (created.st_dev, created.st_ino)
                                or self._read_file(parent, pending, owner=owner, mode=mode) != data):
                            raise InstallerError("replacement pending generation changed before rename")
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

    @staticmethod
    def _rename_provision_entry(parent: int, source: str, target: str, *, updating: bool = False) -> None:
        # Plain rename can clobber a late target. Keep the shared O_EXCL
        # publication/quarantine contract atomic; no fallback.
        try:
            rename = ctypes.CDLL(None, use_errno=True).renameat2
        except AttributeError:
            raise InstallerError("atomic no-replace rename is unavailable") from None
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(parent, os.fsencode(source), parent, os.fsencode(target), 0 if updating else 1):
            error = ctypes.get_errno()
            if error == errno.EEXIST:
                raise InstallerError("provisioning destination appeared before rename")
            raise OSError(error, "atomic provisioning rename failed")

    def _validate_install_record(self, name: str, data: bytes, *, replacement: bool = False) -> None:
        """Destination-bound records, with v1 provenance/recovery kept verbatim.

        Publication alone supplies no service, transaction-update or recovery
        authority. The generator computes these fixed digests; no path
        manifest or secret field is accepted. Only the replacement consumer can
        read/write stopped/quarantine phases; ordinary publication requires prepared.
        The additive v2 first intent is accepted only by replacement consumers.
        """
        record = self._json(data)
        if name == "install-transaction.json" and record.get("version") == 2:
            from runtime.remote_access.release_verifier import validate_record
            if (not replacement or record.keys() != {"format", "version", "layout", "phase", "digests", "release", "inventory"}
                    or type(record["version"]) is not int or record["format"] != "replacement-intent@2"
                    or record["layout"] != self.layout or record["phase"] not in _replacement_phases(record)
                    or not isinstance(record["digests"], dict) or set(record["digests"]) != set(_REPLACEMENT_FILES)
                    or any(not isinstance(value, str) or value != "absent" and re.fullmatch(r"[0-9a-f]{64}", value) is None
                           for value in record["digests"].values())
                    or not isinstance(record["inventory"], dict)
                    or set(record["inventory"]) != set(self._replacement_members())
                    or data != json.dumps(record, sort_keys=True).encode() or len(data) > 65536):
                raise InstallerError("invalid first replacement intent")
            validate_record(record["release"])
            for value in record["inventory"].values():
                if value == "absent":
                    continue
                if (not isinstance(value, dict) or not {"metadata"} <= value.keys() <= {"metadata", "digest", "target"}
                        or not isinstance(value["metadata"], list) or len(value["metadata"]) != 9
                        or any(type(part) is not int for part in value["metadata"])
                        or "digest" in value and (not isinstance(value["digest"], str)
                                                   or re.fullmatch(r"[0-9a-f]{64}", value["digest"]) is None)
                        or "target" in value and not isinstance(value["target"], str)):
                    raise InstallerError("invalid first replacement inventory")
            return
        if name == "provision.json" and "version" not in record:
            # Preserve the legacy provision() output and its reader contract.
            if record.keys() != {"layout", "token_source", "token_sha256", "configs"} or record["layout"] != self.layout:
                raise InstallerError("invalid legacy provisioning record")
            return
        keys = {"connector.json", "sidecar.json", "policy.json", "release-daemon.json", "daemon.token"}
        phases = {"provisioned"}
        if name == "install-transaction.json":
            keys.add("provision.json")
            phases = {"prepared", *_REPLACEMENT_PHASES} if replacement else {"prepared"}
        digests = record.get("digests")
        if (record.keys() != {"version", "layout", "digests", "phase"}
                or type(record["version"]) is not int or record["version"] != 1
                or record["layout"] not in ("source", "deb") or record["layout"] != self.layout
                or not isinstance(record["phase"], str) or record["phase"] not in phases
                or not isinstance(digests, dict) or digests.keys() != keys
                or any(not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
                       for value in digests.values())):
            raise InstallerError("invalid versioned installation record")
        if (name == "install-transaction.json" and record["phase"] in _REPLACEMENT_PHASES
                and data != json.dumps(record, sort_keys=True).encode()):
            raise InstallerError("replacement intent bytes are not the exact generated record")

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

    @staticmethod
    def _generated_configs() -> dict[str, bytes]:
        """Fixed identity/socket settings and the shipped deny-default artifact.

        This is staging only. Helper/package selection and a complete protected
        home control binding are separate slices; there is no URL input here.
        """
        from runtime.remote_access.managed_provider import ManagedProviderConfig
        from runtime.remote_access.supervisor import ConnectorConfig

        connector = asdict(ConnectorConfig(
            tenant_id=uuid.uuid4().hex, home_id=uuid.uuid4().hex, connector_id=uuid.uuid4().hex,
            system=True, service_user="happyranch", service_group="happyranch",
            daemon_token_path="/etc/happyranch/daemon.token", policy_path="/etc/happyranch/policy.json",
            state_path="/var/lib/happyranch-connector/trust-state.json", excluded_daemon_ports=[8765],
            managed=ManagedProviderConfig(profile="linux-managed-identity-v1", control_plane_id=uuid.uuid4().hex),
        ))
        connector.pop("daemon_port")
        sidecar = dict(Profile="linux-managed-identity-v1", StateDir="/var/lib/happyranch-tsnet-sidecar",
                       RoleIdentity=f"home-sidecar-{connector['home_id']}", DERPPolicy="private-only",
                       ListenAddr=":443", ExcludedDaemonPorts=[8765])
        artifact = json.loads(Path(__file__).with_name("route_policy.json").read_bytes())
        policy = dict(schema_version=1, artifact_version=int(artifact["version"]), artifact=artifact,
                      issued_at=datetime.now(timezone.utc).isoformat(), max_age_seconds=3600,
                      revision=1, state="active")
        return {name: json.dumps(value, sort_keys=True).encode() for name, value in (
            ("connector.json", connector), ("sidecar.json", sidecar), ("policy.json", policy))}

    def provision(self, *, connector_config: Path | None = None, sidecar_config: Path | None = None,
                  policy_file: Path | None = None,
                  release_user: str, data_home: Path | None = None,
                  daemon_token_source: Path | None = None) -> None:
        """Stage fresh files; omitted config inputs generate an inert identity bundle.

        The operator CLI still requires its original three inputs. No helper
        action selects this generator until the package/H2 slice ships.
        """
        try:
            generated = connector_config is None and sidecar_config is None and policy_file is None
            if not generated and any(path is None for path in (connector_config, sidecar_config, policy_file)):
                raise InstallerError("provide all three config inputs or omit all three for staged generation")
            if generated and daemon_token_source is not None:
                raise InstallerError("generated bundle requires the fixed release-home token source")
            release = self._release_record(release_user, data_home)
            daemon_token_source = daemon_token_source or Path(release['data_home']) / 'daemon.token'
            files = self._generated_configs() if generated else {name: self._input(path) for name, path in (
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
                with self._directory(path, create=True):
                    pass
            for name, data in files.items():
                self._write_provision_file(name, data, (self.root_owner[0], self._service_owner[1]), 0o640)
            self._write_provision_file("release-daemon.json", json.dumps(release, sort_keys=True).encode(), self.root_owner, 0o644)
            self._write_provision_file("daemon.token", token, self._service_owner, 0o600)
            record = dict(layout=self.layout, token_source=str(daemon_token_source),
                          token_sha256=hashlib.sha256(token).hexdigest(),
                          configs={name: hashlib.sha256(data).hexdigest() for name, data in files.items()})
            if generated:
                record = dict(version=1, layout=self.layout, phase="provisioned", digests={
                    **{name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
                    "release-daemon.json": hashlib.sha256(json.dumps(release, sort_keys=True).encode()).hexdigest(),
                    "daemon.token": hashlib.sha256(token).hexdigest(),
                })
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
                from runtime.remote_access.home_admission import HomeProfileAdmission
                try:
                    return HomeProfileAdmission(self).require()
                except InstallerError:
                    raise InstallerError("home control binding not provisioned; requires slice 3b-2") from None

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
    parser.add_argument("command", choices=("provision", "install", "uninstall", "check-drift", "prepare-headscale", "disable-headscale"))
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
    try:
        installer = ManagedInstaller(layout=args.layout)
        if args.command == "prepare-headscale":
            if args.layout != "deb":
                raise headscale.HeadscaleServiceError("deb-layout-required")
            print(json.dumps(installer.prepare_headscale(), sort_keys=True))
        elif args.command == "disable-headscale":
            print(json.dumps(installer.cleanup_headscale(), sort_keys=True))
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
