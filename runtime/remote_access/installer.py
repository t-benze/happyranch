"""Fresh-host managed systemd installation with transient file-only enrollment input.

Provision binaries separately; the provision command stages configuration.
No upgrade journal, rollback or automatic recovery is provided. Enrollment sources are removed on
success and failure; issue a fresh single-use key before retrying. Files already
present must match the rendered units; interrupted installs need operator care.
"""
from __future__ import annotations

import argparse
import grp
import hashlib
import json
import math
import os
import pwd
import re
import stat
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import urlsplit

from runtime.remote_access.service_manager import ServiceManagerError, SystemdServiceManager
from runtime.remote_access.systemd_unit import (
    ConnectorUnitSpec,
    SidecarUnitSpec,
    render_connector_unit,
    render_managed_target,
    render_sidecar_unit,
)

UNITS = (
    "happyranch-connector.service",
    "happyranch-tsnet-sidecar.service",
    "happyranch-managed.target",
)
_UNIT_DIR = "etc/systemd/system"
_ENROLLMENT = "etc/happyranch/enrollment.key"
_CREDSTORE = "etc/credstore"
_CREDENTIAL_NAME = "happyranch-tsnet-enrollment"
_STATE_DIRS = ("var/lib/happyranch-connector", "var/lib/happyranch-tsnet-sidecar")
_DEB_PREFIX = "opt/happyranch-desktop"
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


class InstallerError(RuntimeError):
    """An unsafe path, incompatible account, command failure, or unit drift."""


def managed_units(layout: str = "source") -> dict[str, str]:
    """Use the shared renderers verbatim, including sidecar credential injection."""
    if layout not in ("source", "deb"):
        raise InstallerError("unknown installation layout")
    python = (("/opt/happyranch-desktop/python/bin/python3", "-I") if layout == "deb"
              else ("/opt/happyranch/venv/bin/python",))
    sidecar = f"/opt/{'happyranch-desktop' if layout == 'deb' else 'happyranch'}/bin/happyranch-tsnet-sidecar"
    return {
        UNITS[0]: render_connector_unit(ConnectorUnitSpec(
            user="happyranch", group="happyranch",
            exec_start=(*python, "-m", "runtime.remote_access.cli",
                        "run", "--managed", "--config", "/etc/happyranch/connector.json"),
        )),
        UNITS[1]: render_sidecar_unit(SidecarUnitSpec(binary_path=sidecar)),
        UNITS[2]: render_managed_target(),
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
        if expected and path == _STATE_DIRS[0]:
            # Creation follows systemd's mode; the trust-state store tightens
            # this directory to owner-only on save. Preserve either safe mode.
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
        if not path.is_absolute() or ".." in path.parts or path == Path("/"):
            raise InstallerError("provisioning inputs require absolute literal file paths")
        parent = os.open("/", _DIR_FLAGS)
        try:
            for part in path.parts[1:-1]:
                child = os.open(part, _DIR_FLAGS, dir_fd=parent)
                os.close(parent)
                parent = child
            yield parent
        finally:
            os.close(parent)

    @staticmethod
    def _read_file(
        parent: int, name: str, *, owner: tuple[int, int] | None = None,
        mode: int | None = None,
    ) -> bytes:
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(named.st_mode):
            raise InstallerError("unsafe provisioning file type")
        descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent)
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 65536
                    or info.st_mode & 0o022 or (owner is not None and (info.st_uid, info.st_gid) != owner)
                    or (mode is not None and stat.S_IMODE(info.st_mode) != mode)
                    or (named.st_dev, named.st_ino) != (info.st_dev, info.st_ino)):
                raise InstallerError("unsafe provisioning file ownership, mode, links or size")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                data = stream.read(65537)
            after = os.fstat(descriptor)
            if (len(data) > 65536 or not data.strip()
                    or (info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                    != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                raise InstallerError("empty, oversized or changing provisioning file")
            return data
        finally:
            os.close(descriptor)

    def _input(self, path: Path, *, token: bool = False) -> bytes:
        with self._input_parent(path) as parent:
            data = self._read_file(parent, path.name, mode=0o600 if token else None)
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

    def _validate_configs(self, files: dict[str, bytes], *, staged: bool = False) -> None:
        # Reuse shipping connector/policy validators; never start a supervisor.
        from runtime.remote_access.managed_provider import ManagedProviderConfig
        from runtime.remote_access.policy import PolicyEnvelope, RoutePolicyConsumer
        from runtime.remote_access.supervisor import ConnectorConfig

        try:
            raw = self._json(files["connector.json"])
            managed = raw.pop("managed")
            if not isinstance(managed, dict) or raw.get("lab") is not None or raw.get("diy") is not None:
                raise ValueError
            cfg = ConnectorConfig(**raw, managed=ManagedProviderConfig(**managed))
            cfg.validate()
            if (any(not isinstance(value, str) or not value.strip() for value in (cfg.tenant_id, cfg.home_id, cfg.connector_id))
                    or cfg.system is not True or cfg.service_user != "happyranch" or cfg.service_group != "happyranch"
                    or type(cfg.daemon_port) is not int or not 1 <= cfg.daemon_port <= 65535
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
            policy = RoutePolicyConsumer.from_envelope(PolicyEnvelope(**self._json(files["policy.json"])))
            policy.require_current(datetime.now(timezone.utc))
            sidecar = self._json(files["sidecar.json"])
            required = {"StateDir", "ControlURL", "RoleIdentity", "ExpectedPeer", "DERPPolicy", "ListenAddr", "ConnectorAddr"}
            if (not required <= sidecar.keys() <= required | {"ExcludedDaemonPorts"}
                    or any(not isinstance(sidecar[key], str) for key in required)):
                raise ValueError
            sidecar_excluded = sidecar.get("ExcludedDaemonPorts", [])
            if (not isinstance(sidecar_excluded, list)
                    or any(type(port) is not int or not 1 <= port <= 65535 for port in sidecar_excluded)):
                raise ValueError
            excluded = sorted({8765, cfg.daemon_port, *cfg.excluded_daemon_ports, *sidecar_excluded})
            if (cfg.managed.bind_port in excluded
                    or sidecar["ConnectorAddr"] != f"127.0.0.1:{cfg.managed.bind_port}"):
                raise ValueError
            if (staged and self.layout == "deb"
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
                    or ingress is None or not 1 <= int(ingress[1]) <= 65535 or int(ingress[1]) == 8765):
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

    def _write_provision_file(self, name: str, data: bytes, owner: tuple[int, int], mode: int) -> None:
        with self._directory("etc/happyranch") as parent:
            descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                 0o600, dir_fd=parent)
            try:
                os.fchown(descriptor, *owner)
                with os.fdopen(descriptor, "wb", closefd=False) as stream:
                    stream.write(data)
                    stream.flush()
                    os.fchmod(descriptor, mode)
                    os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.fsync(parent)

    def provision(self, *, connector_config: Path, sidecar_config: Path, policy_file: Path,
                  daemon_token_source: Path) -> None:
        """Fresh-host, no-service-start staging. Existing files always refuse."""
        try:
            files = {name: self._input(path) for name, path in (
                ("connector.json", connector_config), ("sidecar.json", sidecar_config), ("policy.json", policy_file))}
            self._validate_configs(files)
            if self.layout == "deb":
                connector = self._json(files["connector.json"])
                sidecar = self._json(files["sidecar.json"])
                excluded = sorted({8765, connector["daemon_port"],
                                   *connector.get("excluded_daemon_ports", []),
                                   *sidecar.get("ExcludedDaemonPorts", [])})
                connector["excluded_daemon_ports"] = excluded
                sidecar["ExcludedDaemonPorts"] = excluded
                for name, value in (("connector.json", connector), ("sidecar.json", sidecar)):
                    files[name] = json.dumps(value, sort_keys=True).encode()
                self._validate_configs(files, staged=True)
            token = self._input(daemon_token_source, token=True)
            self._check_deb_payload()
            self._service_owner = self._account(create=False)
            self._preflight()
            try:
                with self._directory("etc/happyranch") as parent:
                    for name in (*files, "daemon.token", "provision.json"):
                        try:
                            os.stat(name, dir_fd=parent, follow_symlinks=False)
                        except FileNotFoundError:
                            continue
                        raise InstallerError("provisioning destination exists; inspect partial files before retrying")
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
                record = self._json(self._read_file(parent, "provision.json", owner=self.root_owner, mode=0o600))
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
            source = self._input(Path(record["token_source"]), token=True)
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

    def _write_units(self) -> None:
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
        if enrollment_key_file is None:
            raise InstallerError("install requires --enrollment-key-file; obtain a fresh single-use Headscale key")
        data = bytearray()
        staged = False
        try:
            data = self._read_enrollment(enrollment_key_file)
            self._service_owner = self._account(create=False)
            self._preflight()
            self._check_deb_payload()
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
            if self._has_provisioning():
                with self._directory("etc/happyranch") as parent:
                    record = self._json(self._read_file(parent, "provision.json", owner=self.root_owner, mode=0o600))
                self.layout = record.get("layout")
                self.units = managed_units(self.layout)
            self._check_deb_payload()
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

    def uninstall(self, *, purge: bool = False) -> None:
        """Wait for services before unit removal. Only purge removes service state."""
        try:
            self._service_owner = self._account(create=False)
            self._preflight()
            self._remove_enrollment()
            self.manager.stop(UNITS[2], timeout=50.0)
            # PartOf propagates stop, but the target's job finishes first.
            # Keep RuntimeDirectory loaded until each service has shut down.
            for name in reversed(UNITS[:2]):
                self.manager.stop(name, timeout=50.0)
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
    parser.add_argument("command", choices=("provision", "install", "uninstall", "check-drift"))
    parser.add_argument("--layout", choices=("source", "deb"), default="source")
    for option in ("connector-config", "sidecar-config", "policy-file", "daemon-token-source"):
        parser.add_argument(f"--{option}", type=Path, help="absolute input path (provision only)")
    parser.add_argument("--purge", action="store_true", help="also remove service state on uninstall")
    parser.add_argument("--enrollment-key-file", type=Path, help="root-owned regular 0600 file containing a fresh single-use key (install only)")
    args = parser.parse_args(argv)
    if args.purge and args.command != "uninstall":
        parser.error("--purge is only valid with uninstall")
    if args.enrollment_key_file is not None and args.command != "install":
        parser.error("--enrollment-key-file is only valid with install")
    inputs = {name: getattr(args, name) for name in (
        "connector_config", "sidecar_config", "policy_file", "daemon_token_source")}
    if args.command == "provision" and not all(inputs.values()):
        parser.error("provision requires --connector-config, --sidecar-config, --policy-file and --daemon-token-source")
    if args.command != "provision" and any(inputs.values()):
        parser.error("provisioning inputs are only valid with provision")
    try:
        installer = ManagedInstaller(layout=args.layout)
        if args.command == "provision":
            installer.provision(**inputs)
        elif args.command == "install":
            installer.install(enrollment_key_file=args.enrollment_key_file)
        elif args.command == "uninstall":
            installer.uninstall(purge=args.purge)
        else:
            installer.check_drift()
    except InstallerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
