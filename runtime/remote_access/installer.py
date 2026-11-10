"""Fresh-host managed systemd installation, without payload or enrollment setup.

Provision binaries/configuration separately before starting. No upgrade journal,
rollback, credential copying, or automatic recovery is provided. Files already
present must match the rendered units; interrupted installs need operator care.
"""
from __future__ import annotations

import argparse
import grp
import hashlib
import os
import pwd
import stat
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

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
_STATE_DIRS = ("var/lib/happyranch-connector", "var/lib/happyranch-tsnet-sidecar")
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


class InstallerError(RuntimeError):
    """An unsafe path, incompatible account, command failure, or unit drift."""


def managed_units() -> dict[str, str]:
    """Use the shared renderer verbatim; no enrollment or credential directives."""
    return {
        UNITS[0]: render_connector_unit(ConnectorUnitSpec(
            user="happyranch", group="happyranch",
            exec_start=("/opt/happyranch/venv/bin/python", "-m", "runtime.remote_access.cli",
                        "run", "--managed", "--config", "/etc/happyranch/connector.json"),
        )),
        UNITS[1]: render_sidecar_unit(SidecarUnitSpec()),
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
        self.units = managed_units()
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
        layout: dict[str, tuple[int, tuple[int, int] | None]] = {path: (0o755, self.root_owner) for path in (
            "opt/happyranch", "opt/happyranch/bin", "etc/happyranch",
        )}
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
        owner = expected[1] if expected else self.root_owner
        allowed_modes = (expected[0],) if expected else ()
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

    def _preflight(self) -> None:
        # Check every existing destination before account creation or systemctl.
        # This is not transactional recovery or a concurrent-root-writer fence.
        for path in (*self._layout(), _UNIT_DIR):
            try:
                with self._directory(path):
                    pass
            except FileNotFoundError:
                pass
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

    def install(self) -> dict[str, str]:
        """Create the layout/units, start, then reject drift; no rollback."""
        try:
            self._service_owner = self._account(create=False)
            self._preflight()
            self._service_owner = self._account(create=True)
            self._preflight()
            for path in self._layout():
                with self._directory(path, create=True):
                    pass
            self._write_units()
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
            raise InstallerError(f"installation failed: {exc}; inspect partial files before retrying") from exc

    def check_drift(self) -> dict[str, str]:
        """Observe only file digest, fragment, drop-ins, reload and active state.

        A successful blocking systemctl start plus active Type=notify services
        establishes systemd's READY boundary. No loaded-property contract or
        pre-start drift check is used. The target itself is not Type=notify.
        """
        digests: dict[str, str] = {}
        try:
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
        """Stop/disable/remove units. Only explicit purge removes service state."""
        try:
            self._service_owner = self._account(create=False)
            self._preflight()
            self.manager.stop(UNITS[2], timeout=50.0)
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "uninstall", "check-drift"))
    parser.add_argument("--purge", action="store_true", help="also remove service state on uninstall")
    args = parser.parse_args(argv)
    if args.purge and args.command != "uninstall":
        parser.error("--purge is only valid with uninstall")
    try:
        installer = ManagedInstaller()
        if args.command == "install":
            installer.install()
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
