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
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import urlsplit

from runtime.remote_access import headscale_service as headscale
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

    The generation fingerprints the exact validated transaction-record bytes. It
    is absent unless all six fixed-file digests match a known failed/partial
    installation, or an exact replacement intent. It never authorizes activation.
    """

    installation: InstallInventory
    journal_generation: str | None = None


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
    ) -> dict[str, bytes]:
        """Bounded, shallow inventory of one fixed directory; no unknown reads."""
        files: dict[str, bytes] = {}
        try:
            with self._directory(relative) as parent:
                with os.scandir(parent) as entries:
                    for index, entry in enumerate(entries):
                        path = str(self.root / relative / entry.name)
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
    ) -> tuple[set[str], dict[str, dict[str, str]]]:
        if connector and not isinstance(connector.get("managed"), dict):
            raise InstallerError(str(self.root / "etc/happyranch/connector.json"))
        expected = managed_units(self.layout, connector_config=connector)
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
        location = str(self.root / "etc/happyranch")
        paths: list[str] = []
        try:
            self._service_owner = self._account(create=False)
            service = self._service_owner
            config_owner = None if service is None else (self.root_owner[0], service[1])
            phase = None
            if prepared_transaction is not None:
                self._validate_install_record("install-transaction.json", prepared_transaction, replacement=True)
                phase = self._json(prepared_transaction)["phase"]
            if phase in (*_STAGING_PHASES, *_CLOSURE_PHASES):
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
            config_metadata = {name: (config_owner, (0o600, 0o640, 0o644), False)
                               for name in _QUARANTINE_CONFIGS}
            if quarantine_phase in (*_QUARANTINE_PHASES, *_CONFIG_REMOVAL_PHASES):
                config_metadata = self._quarantine_metadata(quarantine_phase)
            files = self._inventory_files("etc/happyranch", {
                **config_metadata,
                "release-daemon.json": (self.root_owner, 0o644, False),
                "provision.json": (self.root_owner, 0o600, False),
                "daemon.token": (service, 0o600, False),
                "enrollment.key": (self.root_owner, 0o600, False),
                **({"install-transaction.json": (self.root_owner, 0o600, False)}
                   if prepared_transaction is not None else {}),
                **({".install-transaction.json.pending": (self.root_owner, 0o600, False)}
                   if pending is not None and pending[0] == "install-transaction.json" else {}),
            })
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
                if any((name not in files and name not in removable_configs)
                       or (name in files and hashlib.sha256(files[name]).hexdigest() != digest)
                       for name, digest in digests.items()):
                    raise InstallerError(location)
            connector = self._json(files["connector.json"]) if "connector.json" in files else {}
            provenance = files.get("provision.json")
            modern = False
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
                    if name in files and hashlib.sha256(files[name]).hexdigest() != digest:
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
                ("run/happyranch-tsnet-sidecar", {"health.json": (service, 0o644, False)}),
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
            units, properties = self._inventory_units(connector, removing_units=removing_units)
            paths.extend(str(self.root / _UNIT_DIR / name) for name in units)
            complete = required <= files.keys()
            failed = any(props["ActiveState"] == "failed" for props in properties.values())
            if phase in _REPLACEMENT_PHASES:
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

    def replacement_inventory(self) -> ReplacementInventory:
        """Inspect a journal only for replacement candidate selection.

        V1 stopped/quarantine phases belong only to this consumer. Only exact
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
            return ReplacementInventory(self.inventory())
        except (OSError, InstallerError):
            return ReplacementInventory(InstallInventory("manual-install-conflict", "unsafe-or-unrecognized", (location,)))
        generation = (hashlib.sha256(transaction).hexdigest()
                      if installation.state == "known-failed-or-partial" else None)
        return ReplacementInventory(installation, generation)

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
        phases = ("prepared", *_REPLACEMENT_PHASES)
        previous = phases.index(expected["phase"])
        staged_name = dict(_STAGING_FILES).get(expected["phase"])
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
            self._staging_custody()
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

    def replace_failed(self) -> None:
        """Resume exact config quarantine, then fixed state/enrollment removal.

        Prepared candidates publish stopped intent first; the next call quarantines
        configs. A later call consumes quarantine to remove fixed retired state.
        A further call removes quarantined configs, then another removes only
        owned unit entries. A later call stages fresh inert files; the next call
        closes that exact generation into ordinary modern staged-inert inventory.
        Unjournaled candidates stay unavailable until their recovery owner ships.
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
            release = self._check_release_record()
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
            elif record["phase"] in ("replacement-units-removed", *_STAGING_PHASES):
                self._stage_replacement(transaction)
            elif record["phase"] in (*_REMOVAL_PHASES, *_CONFIG_REMOVAL_PHASES, *_UNIT_REMOVAL_PHASES,
                                     "replacement-quarantined"):
                self._remove_replacement_state(transaction)
            else:
                self._quarantine_configs(transaction)
        except (OSError, ServiceManagerError):
            raise InstallerError("replacement stop intent incomplete; inspect the fixed transaction record") from None
        raise InstallerError("replacement paused after stop intent; quarantine/removal and fresh staging unavailable")

    def _quarantine_metadata(self, phase: str) -> dict:
        """Accept exactly the before/after placement of ONE journaled rename.

        Only its source may have partially tightened metadata. Every earlier
        config is root-only in quarantine; every later config is still 0640.
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
        current = (_STAGING_PHASES.index(record["phase"]) if record["phase"] in _STAGING_PHASES
                   else len(_STAGING_PHASES))
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
        required = {"release-daemon.json"}
        allowed = set(required)
        for index, (_, name) in enumerate(_STAGING_FILES, start=len(_STAGING_REMOVALS)):
            if index <= current:
                allowed.add(name)
            if index < current:
                required.add(name)
        # Until each discard intent completes, only its original digest may exist.
        for index, (_, relative) in enumerate(_STAGING_REMOVALS):
            name = Path(relative).name
            if current <= index:
                allowed.add(name)
                if current < index:
                    required.add(name)
        if not required <= files.keys() <= allowed:
            raise InstallerError("replacement staging placement changed")
        if any(hashlib.sha256(data).hexdigest() != record["digests"][name] for name, data in files.items()):
            raise InstallerError("replacement staging digest changed")
        self._validate_configs({name: data for name, data in files.items() if name in _QUARANTINE_CONFIGS},
                               staged=True, partial=True, require_current=False)
        if current >= _STAGING_PHASES.index("replacement-stage-provision") and "provision.json" in files:
            self._validate_install_record("provision.json", files["provision.json"])
            if self._json(files["provision.json"])["digests"] != {
                    name: hashlib.sha256(data).hexdigest() for name, data in files.items() if name != "provision.json"}:
                raise InstallerError("replacement provisioning digest changed")
        from runtime.remote_access.release_verifier import validate_record
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

    def _staging_custody(self) -> bytes:
        release = self._check_release_record()
        token = self._input(Path(release["data_home"]) / "daemon.token", token=True, source_uid=release["uid"])
        self._confirm_quiescent()
        return token

    def _staging_data(self, name: str, transaction: bytes) -> bytes:
        """Generate only new settings; never open quarantined or retired configs."""
        if name == "daemon.token":
            return self._staging_custody()
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
        self._check_deb_payload()
        while record["phase"] != "replacement-staged":
            phase = record["phase"]
            if phase in dict(_STAGING_REMOVALS):
                self._remove_replacement_entry(transaction)
            elif phase in dict(_STAGING_FILES):
                name = dict(_STAGING_FILES)[phase]
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
                             if name == "provision.json" else (self.root_owner[0], self._service_owner[1]))
                    self._write_provision_file(name, data, owner, 0o640 if name in _QUARANTINE_CONFIGS else 0o600,
                                               expected_transaction=transaction)
            next_index = 0 if phase == "replacement-units-removed" else _STAGING_PHASES.index(phase) + 1
            record["phase"] = _STAGING_PHASES[next_index]
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
                    self._staging_custody()
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
            self._staging_custody()
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
                    release = self._check_release_record()
                    self._input(Path(release["data_home"]) / "daemon.token", token=True, source_uid=release["uid"])
                    if self._inventory(prepared_transaction=transaction).state != "known-failed-or-partial":
                        raise InstallerError("replacement inventory changed before unit effect")
                    self._confirm_quiescent()
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
                    # Opaque state is never read, interpreted, copied or restored.
                    # Repeat the full bounded inventory before this effect.
                    self._check_replacement_transaction(journal_parent, transaction)
                    if phase in _STAGING_PHASES:
                        self._staging_custody()
                    if self._inventory(prepared_transaction=transaction).state != "known-failed-or-partial":
                        raise InstallerError("replacement inventory changed before unlink")
                    revalidate()
                    revalidate_removal()
                    after = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    fields = ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink",
                              "st_size", "st_mtime_ns", "st_ctime_ns")
                    if any(getattr(before, field) != getattr(after, field) for field in fields):
                        raise InstallerError("removal entry changed")
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
            metadata = self._quarantine_metadata(record["phase"])
            quarantine = ".replacement-" + name
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

    def _check_replacement_transaction(self, parent: int, transaction: bytes) -> None:
        if self._read_file(parent, "install-transaction.json", owner=self.root_owner, mode=0o600) != transaction:
            raise InstallerError("replacement generation changed before mutation")
        record = self._json(transaction)
        if record["phase"] in (*_STAGING_PHASES, *_CLOSURE_PHASES):
            return  # The staging consumer checks the complete phase-bound inventory.
        phase = ("replacement-configs-removed" if record["phase"] in _UNIT_REMOVAL_PHASES else
                 "replacement-quarantined" if record["phase"] in _REMOVAL_PHASES else record["phase"])
        configs = (self._quarantine_metadata(phase) if phase in (*_QUARANTINE_PHASES, *_CONFIG_REMOVAL_PHASES) else
                   {name: ((self.root_owner[0], self._service_owner[1]), 0o640, False)
                    for name in _QUARANTINE_CONFIGS})
        metadata = {**configs, "release-daemon.json": (self.root_owner, 0o644, False),
                    "provision.json": (self.root_owner, 0o600, False), "daemon.token": (self._service_owner, 0o600, False)}
        for name, digest in record["digests"].items():
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

    def _headscale_inventory(self) -> None:
        """Preparation admits empty custody or this exact inert policy only.

        Config, database, key, socket, pending or unknown inventory refuses; this
        prefix cannot establish ownership of an existing Headscale installation.
        """
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
                        if entries and (path != headscale.CUSTODY[0] or entries != ["policy.hujson"]):
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

    def cleanup_headscale(self) -> dict[str, str]:
        """Disable/remove only the inert installation this installer can prove.

        No config/DB/key/socket writer ships in this slice: those entries remain
        foreign. Receipt retirement is last, including after a partial userdel.
        """
        if self.layout != "deb":
            raise headscale.HeadscaleServiceError("deb-layout-required")
        self._headscale_preparing = True
        partial = False
        try:
            receipt = self._headscale_receipt()
            user, group = self._headscale_cleanup_identity(receipt)
            self._headscale_inventory()
            self._headscale_cleanup_links()
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
            if receipt:
                partial = not user
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
                    "account": "removed" if receipt else "preserved: not created by HappyRanch"}
        except (OSError, InstallerError, UnicodeError, ValueError, ServiceManagerError,
                headscale.HeadscaleServiceError):
            pass
        finally:
            self._headscale_preparing = False
        raise headscale.HeadscaleServiceError("partial-cleanup-uncertain" if partial else "cleanup-uncertain")

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
            self._headscale_owner = self._headscale_shape()
            receipt = self._headscale_receipt()
            if receipt and self._headscale_owner != (receipt["uid"], receipt["gid"]):
                raise headscale.HeadscaleServiceError("account-receipt-uncertain")
            self._headscale_inventory()
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
            return {"state": "inert", "account": outcome,
                    "preservation": "preserved: not created by HappyRanch" if outcome == "unowned" else "receipt-recorded"}
        except headscale.HeadscaleServiceError as error:
            category = error.category
        except (OSError, InstallerError, UnicodeError, ValueError, ServiceManagerError):
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
            layout[headscale.INSTALLER_STATE] = (0o700, self.root_owner)
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
        if relative != "etc/happyranch":
            metadata = {
                headscale.INSTALLER_STATE: {headscale.RECEIPT: (self.root_owner, 0o600)},
                headscale.CUSTODY[0]: {"policy.hujson": (self._headscale_owner, 0o600)},
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
            phases = ("prepared", *_REPLACEMENT_PHASES)
            previous = phases.index(expected_record["phase"])
            if previous + 1 == len(phases):
                raise InstallerError("replacement retirement intent has no successor")
            updated_record = self._json(data)
            staged_name = dict(_STAGING_FILES).get(expected_record["phase"])
            rebinding = staged_name is not None and updated_record["phase"] == expected_record["phase"]
            if rebinding:
                expected_record["digests"][staged_name] = updated_record["digests"][staged_name]
            else:
                expected_record["phase"] = phases[previous + 1]
            if updated_record != expected_record:
                raise InstallerError("replacement update must preserve the exact six digests")
        elif replacement:
            record = self._json(expected_transaction)
            if (dict(_STAGING_FILES).get(record["phase"]) != name
                    or hashlib.sha256(data).hexdigest() != record["digests"][name]):
                raise InstallerError("replacement publication requires its exact staging intent")
            if name == "provision.json":
                self._validate_install_record(name, data)
        elif name in ("provision.json", "install-transaction.json"):
            self._validate_install_record(name, data)
        with self._publication_parent(relative) as (parent, revalidate):
            if replacement:
                if self._inventory(prepared_transaction=expected_transaction).state == "manual-install-conflict":
                    raise InstallerError("replacement inventory changed before intent publication")
                if self._json(expected_transaction)["phase"] in ("replacement-configs-removed", *_UNIT_REMOVAL_PHASES,
                                                                  *_STAGING_PHASES, *_CLOSURE_PHASES):
                    token = self._staging_custody()
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
                    if self._json(expected_transaction)["phase"] in ("replacement-units-removed", *_STAGING_PHASES, *_CLOSURE_PHASES):
                        token = self._staging_custody()
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
        """Destination-bound v1 records, with the existing provenance kept verbatim.

        Publication alone supplies no service, transaction-update or recovery
        authority. The generator computes these fixed digests; no path
        manifest or secret field is accepted. Only the replacement consumer can
        read/write stopped/quarantine phases; ordinary publication requires prepared.
        """
        record = self._json(data)
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

    def _refuse_staged_identity(self) -> None:
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
                # No modern install is selectable until the complete protected
                # home binding ships. Never interpret a supplied URL as enough.
                raise InstallerError("home control binding not provisioned; requires slice 3b-2")

    def _write_units(self) -> None:
        self._refuse_staged_identity()
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
        self._refuse_staged_identity()
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
            self._refuse_staged_identity()
            if self._has_provisioning():
                with self._directory("etc/happyranch") as parent:
                    record = self._json(self._read_file(parent, "provision.json", owner=self.root_owner, mode=0o600))
                self.layout = record.get("layout")
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
        self._refuse_staged_identity()
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
