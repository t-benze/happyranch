"""Current-user managed macOS installation; source Python entry until C7/C8.

The operator provisions a managed connector config and policy. This installer
stages them, preserving existing trust state, and delegates every launchctl
operation to LaunchdServiceManager. It does not provision/start a sidecar or
claim readiness. Partial failures are retained for explicit uninstall/retry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from typing import Iterator

from runtime.remote_access.credentials import read_darwin_enrollment
from runtime.remote_access.launchd_agent import LABEL, LaunchdServiceManager, render_launch_agent
from runtime.remote_access.launchd_files import DARWIN_CONNECTOR_STATE_ROOT, literal_directory, read_private_file
from runtime.remote_access.managed_provider import ManagedProviderConfig
from runtime.remote_access.service_manager import ServiceManagerError
from runtime.remote_access.supervisor import ConnectorConfig, ConnectorConfigError

_SOURCE_CONFIG = "~/.happyranch/remote_access/config.json"
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
_MAX_ENTRIES = 4096


class DarwinInstallerError(RuntimeError):
    """Fixed non-secret installation/drift refusal."""


def _digest(data: bytes | bytearray) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(data: bytes | bytearray) -> dict:
    def unique(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    value = json.loads(data, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError("not an object")
    return value


class DarwinManagedInstaller:
    def __init__(self) -> None:
        self.home = Path.home()
        if not self.home.is_absolute() or ".." in self.home.parts:
            raise DarwinInstallerError("macOS home must be an absolute literal path")
        self.root = DARWIN_CONNECTOR_STATE_ROOT.expanduser()
        self.state = self.root / "happyranch-connector"
        self.agents = self.home / "Library/LaunchAgents"
        self.logs = self.home / "Library/Logs/HappyRanch"
        self.plist = self.agents / f"{LABEL}.plist"
        self.manager = LaunchdServiceManager(unit_dir=self.agents)
        self.directories = (self.root, self.state, self.agents, self.logs)
        self.log_files = tuple(self.logs / f"{LABEL}.{name}.log" for name in ("stdout", "stderr"))

    @contextmanager
    def _directory(self, path: Path, *, create: bool = False) -> Iterator[int]:
        """Pin literal home parents; never repair unsafe existing directories."""
        parts = path.relative_to(self.home).parts
        with literal_directory(self.home) as home:
            descriptor = os.dup(home)
        try:
            current = self.home
            for part in (None, *parts):
                if part is not None:
                    current /= part
                    if create:
                        try:
                            os.mkdir(part, 0o700, dir_fd=descriptor)
                        except FileExistsError:
                            pass
                    child = os.open(part, _DIR_FLAGS, dir_fd=descriptor)
                    os.close(descriptor)
                    descriptor = child
                info = os.fstat(descriptor)
                if (info.st_uid != os.geteuid() or info.st_mode & 0o022
                        or (current in (self.root, self.state) and stat.S_IMODE(info.st_mode) != 0o700)):
                    raise DarwinInstallerError("unsafe macOS managed directory ownership or mode")
            yield descriptor
        finally:
            os.close(descriptor)

    @staticmethod
    def _file_info(info: os.stat_result) -> None:
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600):
            raise DarwinInstallerError("unsafe macOS managed file ownership, mode, links or type")

    def _tree(self, descriptor: int, *, remove: bool = False, depth: int = 0,
              budget: list[int] | None = None, device: int | None = None) -> None:
        """Bounded no-follow state inspection/deletion; no foreign/mounted tree."""
        budget = [_MAX_ENTRIES] if budget is None else budget
        device = os.fstat(descriptor).st_dev if device is None else device
        if depth > 32:
            raise DarwinInstallerError("macOS managed state exceeds inspection limits")
        for name in os.listdir(descriptor):
            budget[0] -= 1
            if budget[0] < 0:
                raise DarwinInstallerError("macOS managed state exceeds inspection limits")
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if info.st_uid != os.geteuid() or info.st_dev != device:
                raise DarwinInstallerError("foreign or mounted macOS managed state")
            if stat.S_ISDIR(info.st_mode):
                if stat.S_IMODE(info.st_mode) != 0o700:
                    raise DarwinInstallerError("unsafe macOS managed state directory mode")
                child = os.open(name, _DIR_FLAGS, dir_fd=descriptor)
                try:
                    opened = os.fstat(child)
                    if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                        raise DarwinInstallerError("macOS managed state changed during inspection")
                    self._tree(child, remove=remove, depth=depth + 1, budget=budget, device=device)
                finally:
                    os.close(child)
                if remove:
                    os.rmdir(name, dir_fd=descriptor)
            else:
                self._file_info(info)
                if remove:
                    os.unlink(name, dir_fd=descriptor)

    def _pending(self, *, remove: bool = False) -> None:
        """Validate interrupted plist publication; only unlink after verified stop."""
        pending = self.plist.with_suffix(".plist.pending")
        try:
            with self._directory(self.agents) as parent:
                named = os.stat(pending.name, dir_fd=parent, follow_symlinks=False)
                self._file_info(named)
                descriptor = os.open(pending.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                try:
                    opened = os.fstat(descriptor)
                    self._file_info(opened)
                    if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
                        raise DarwinInstallerError("LaunchAgent pending file changed during inspection")
                    if remove:
                        current = os.stat(pending.name, dir_fd=parent, follow_symlinks=False)
                        self._file_info(current)
                        self._file_info(os.fstat(descriptor))
                        if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
                            raise DarwinInstallerError("LaunchAgent pending file changed before removal")
                        os.unlink(pending.name, dir_fd=parent)
                        os.fsync(parent)
                finally:
                    os.close(descriptor)
        except FileNotFoundError:
            pass

    def _preflight(self, *, allow_pending: bool = False) -> None:
        for path in self.directories:
            try:
                with self._directory(path) as descriptor:
                    if path == self.state:
                        self._tree(descriptor)
            except FileNotFoundError:
                pass
        for path in (self.plist, *self.log_files):
            try:
                with self._directory(path.parent) as parent:
                    self._file_info(os.stat(path.name, dir_fd=parent, follow_symlinks=False))
            except FileNotFoundError:
                pass
        if allow_pending:
            self._pending()
            return
        try:
            with self._directory(self.agents) as parent:
                os.stat(self.plist.with_suffix(".plist.pending").name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise DarwinInstallerError("LaunchAgent pending file exists; inspect partial installation")

    def _read(self, path: Path) -> bytearray:
        with self._directory(path.parent):
            return read_private_file(path, exact_mode=True)

    def _write(self, path: Path, data: bytes | bytearray) -> None:
        with self._directory(path.parent) as parent:
            try:
                descriptor = os.open(path.name, _FILE_FLAGS, 0o600, dir_fd=parent)
            except FileExistsError:
                if self._read(path) != data:
                    raise DarwinInstallerError("managed file differs; inspect partial installation before retrying") from None
                return
            with os.fdopen(descriptor, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(parent)

    def _plist(self) -> bytes:
        if getattr(sys, "frozen", False):
            raise DarwinInstallerError("bundled macOS connector entry point is not available yet")
        return render_launch_agent(LABEL, (
            sys.executable, "-m", "runtime.remote_access.cli", "run", "--managed",
            "--config", str(self.state / "config.json"),
        ), logs_dir=self.logs).encode()

    @staticmethod
    def _config(data: bytes | bytearray) -> ConnectorConfig:
        raw = _json(data)
        managed = raw.pop("managed", None)
        if not isinstance(managed, dict) or raw.get("lab") is not None or raw.get("diy") is not None:
            raise DarwinInstallerError("macOS managed installer requires only a managed provider")
        cfg = ConnectorConfig(**raw, managed=ManagedProviderConfig(**managed))
        cfg.validate()
        if (cfg.system or cfg.exec_start is not None or cfg.managed_dir_root is not None
                or cfg.unit_name != "happyranch-connector.service" or cfg.state_dir != "happyranch-connector"):
            raise DarwinInstallerError("macOS managed installer requires the fixed per-user layout and source entry")
        return cfg

    def install(self, *, source_config: Path, enrollment_key_file: Path | None) -> dict:
        if enrollment_key_file is None:
            raise DarwinInstallerError("install requires --enrollment-key-file")
        data = bytearray()
        try:
            data = read_darwin_enrollment(enrollment_key_file)
            self._preflight()
            cfg = self._config(read_private_file(source_config.expanduser(), exact_mode=True))
            policy = read_private_file(Path(cfg.policy_path).expanduser(), exact_mode=True)
            token_path = Path(cfg.daemon_token_path).expanduser()
            if not token_path.is_absolute() or ".." in token_path.parts:
                raise DarwinInstallerError("daemon_token_path requires an absolute literal path")
            cfg = replace(cfg, state_path=str(self.state / "trust-state.json"),
                          policy_path=str(self.state / "policy.json"), daemon_token_path=str(token_path))
            config = json.dumps(asdict(cfg), indent=2, sort_keys=True).encode()
            plist = self._plist()
            files = {"config.json": config, "policy.json": policy}
            receipt = json.dumps({"version": 1, "files": {
                **{name: _digest(value) for name, value in files.items()}, "plist": _digest(plist),
            }}, sort_keys=True).encode()
            # All existing destinations must match before any managed write or
            # launchctl call. Existing trust/anchors are never seeded/replaced.
            for path, expected in ((self.plist, plist), *((self.state / name, value) for name, value in files.items()),
                                   (self.state / "install.json", receipt)):
                try:
                    if self._read(path) != expected:
                        raise DarwinInstallerError("managed configuration drift; uninstall before reinstalling")
                except FileNotFoundError:
                    pass
            try:
                self._read(self.state / "enrollment.key")
            except FileNotFoundError:
                pass
            else:
                raise DarwinInstallerError("stored enrollment key exists; inspect and purge before reinstalling")
            self.manager.domain  # Refuse unavailable launchd before writes.
            for path in self.directories:
                with self._directory(path, create=True):
                    pass
            for name, value in (*files.items(), ("install.json", receipt), ("enrollment.key", data)):
                self._write(self.state / name, value)
            for path in self.log_files:
                with self._directory(path.parent) as parent:
                    try:
                        fd = os.open(path.name, _FILE_FLAGS, 0o600, dir_fd=parent)
                    except FileExistsError:
                        continue  # Metadata checked; preserve existing logs.
                    os.close(fd)
            # Reuse an identical plist without creating a second backup owner.
            if self.plist.exists():
                self.manager.enable(LABEL)
            else:
                self.manager.install(plist.decode(), LABEL)
            return self.check_drift()
        finally:
            data[:] = b"\x00" * len(data)

    def check_drift(self) -> dict:
        self._preflight()
        receipt = _json(self._read(self.state / "install.json"))
        files = receipt.get("files")
        if (receipt.keys() != {"version", "files"} or type(receipt["version"]) is not int
                or receipt["version"] != 1 or not isinstance(files, dict)
                or files.keys() != {"config.json", "policy.json", "plist"}):
            raise DarwinInstallerError("invalid macOS installation receipt")
        for name, path in (("config.json", self.state / "config.json"),
                           ("policy.json", self.state / "policy.json"), ("plist", self.plist)):
            if _digest(self._read(path)) != files[name]:
                raise DarwinInstallerError(f"macOS managed {name} digest drift")
        cfg = self._config(self._read(self.state / "config.json"))
        if (cfg.state_path != str(self.state / "trust-state.json")
                or cfg.policy_path != str(self.state / "policy.json")
                or _digest(self._plist()) != files["plist"]):
            raise DarwinInstallerError("macOS managed paths or source entry drift")
        for path in self.log_files:
            with self._directory(path.parent) as parent:
                self._file_info(os.stat(path.name, dir_fd=parent, follow_symlinks=False))
        return {"files": files, "layout": "ok", "readiness": "not-verified", "sidecar": "not-installed"}

    def _unlink(self, path: Path) -> None:
        try:
            with self._directory(path.parent) as parent:
                try:
                    self._file_info(os.stat(path.name, dir_fd=parent, follow_symlinks=False))
                    os.unlink(path.name, dir_fd=parent)
                    os.fsync(parent)
                except FileNotFoundError:
                    pass
        except FileNotFoundError:
            pass

    def uninstall(self, *, purge: bool = False) -> None:
        self._preflight(allow_pending=True)  # Validate partial publication before lifecycle effects.
        self.manager.uninstall(LABEL)  # stop/bootout verification, disable, plist removal
        # A stopped writer may have left new files; recheck before deleting.
        self._preflight(allow_pending=True)
        self._pending(remove=True)
        for name in ("config.json", "policy.json", "install.json"):
            self._unlink(self.state / name)
        for path in self.log_files:
            self._unlink(path)
        if purge:
            try:
                with self._directory(self.state) as descriptor:
                    self._tree(descriptor, remove=True)
                    os.fsync(descriptor)
                with self._directory(self.root) as parent:
                    os.rmdir(self.state.name, dir_fd=parent)
                    os.fsync(parent)
            except FileNotFoundError:
                pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "uninstall", "check-drift"))
    parser.add_argument("--purge", action="store_true", help="also remove managed state and stored enrollment key")
    parser.add_argument("--enrollment-key-file", type=Path, help="current-user regular 0600 key file (install only)")
    parser.add_argument("--config", type=Path, help=f"prepared source connector config (install only; default {_SOURCE_CONFIG})")
    args = parser.parse_args(argv)
    if args.purge and args.command != "uninstall":
        parser.error("--purge is only valid with uninstall")
    if args.enrollment_key_file is not None and args.command != "install":
        parser.error("--enrollment-key-file is only valid with install")
    if args.config is not None and args.command != "install":
        parser.error("--config is only valid with install")
    try:
        installer = DarwinManagedInstaller()
        if args.command == "install":
            result = installer.install(source_config=args.config or Path(_SOURCE_CONFIG), enrollment_key_file=args.enrollment_key_file)
        elif args.command == "uninstall":
            installer.uninstall(purge=args.purge)
            return 0
        else:
            result = installer.check_drift()
        print(json.dumps(result, sort_keys=True))
        return 0
    except DarwinInstallerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (ServiceManagerError, ConnectorConfigError, OSError,
            ValueError, TypeError, RecursionError):
        # Native exceptions and invalid JSON can contain credential values.
        print("error: macOS managed installation unavailable, unsafe or drifted; inspect local layout (partial files may remain)", file=sys.stderr)
        return 1
