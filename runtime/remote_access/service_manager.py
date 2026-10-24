"""Systemd service-manager lifecycle (THR-097 phase unit 3).

Manages the connector service unit through ``systemctl`` shell-outs
(injectable for deterministic tests — same pattern as the daemon's
``LinuxSystemdBackend``, no new dependency). Every operation fails closed on
a non-zero exit. ``upgrade`` writes a new unit over an owner-only backup,
``daemon-reload``s, restarts, and verifies the unit reaches ``active``;
a failed start auto-rolls back to the previous unit. ``rollback`` restores
the most recent backup.
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_SYSTEM_UNIT_DIR = Path("/etc/systemd/system")
_USER_UNIT_DIR = Path("~/.config/systemd/user").expanduser()
_DEFAULT_VERIFY_SECONDS = 5.0
_DEFAULT_POLL_SECONDS = 0.1


class ServiceManagerError(Exception):
    """A systemctl operation failed (non-zero exit, unreadable unit, etc.)."""


@dataclass(frozen=True)
class ServiceStatus:
    """Parsed ``systemctl show`` state for one unit."""

    unit_name: str
    active_state: str
    sub_state: str
    load_state: str
    pid: int = 0
    unit_file_state: str = ""

    @property
    def running(self) -> bool:
        return self.active_state == "active" and self.sub_state not in {"failed", "dead"}


@dataclass(frozen=True)
class UpgradeOutcome:
    """Result of an upgrade/rollback attempt."""

    ok: bool
    rolled_back: bool = False
    backup_path: Path | None = None


def _default_run(argv: list[str], timeout: float = 10.0) -> tuple[int, str]:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, f"timeout after {timeout}s: {' '.join(argv)}"
    except OSError as exc:
        return -1, f"cannot execute {' '.join(argv)}: {exc}"


class SystemdServiceManager:
    """systemd lifecycle operations for the connector service."""

    def __init__(
        self,
        *,
        systemctl: str = "systemctl",
        system: bool = False,
        unit_dir: Path | None = None,
        backup_dir: Path | None = None,
        run: Callable[[list[str]], tuple[int, str]] | None = None,
        verify_seconds: float = _DEFAULT_VERIFY_SECONDS,
        poll_seconds: float = _DEFAULT_POLL_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._systemctl_bin = systemctl
        self._system = system
        self.unit_dir = Path(unit_dir or (_SYSTEM_UNIT_DIR if system else _USER_UNIT_DIR))
        self.backup_dir = Path(backup_dir or (self.unit_dir / "backups"))
        self._run = run or _default_run
        self._verify_seconds = verify_seconds
        self._poll_seconds = poll_seconds
        self._sleep = sleep

    # ── primitives ────────────────────────────────────────────────────────

    def _systemctl(self, *args: str, timeout: float = 10.0) -> str:
        argv = [self._systemctl_bin]
        if not self._system:
            argv.append("--user")
        argv.extend(args)
        code, out = self._run(argv, timeout=timeout)
        if code != 0:
            raise ServiceManagerError(
                f"systemctl {' '.join(args)} failed ({code}): {out.strip()[:200]}"
            )
        return out

    def daemon_reload(self) -> None:
        self._systemctl("daemon-reload")

    def _nonmanaged_selection_properties(self, unit_name: str, deadline: float) -> tuple[int, dict[str, str]]:
        """Observe one selected unit; metadata supplies exclusion, not authority."""
        from runtime.remote_access.cli import _capture_bounded_query_output, _close_and_reap_query_process
        from runtime.remote_access.linux_package import _MANAGED_LOCK
        names = ("Id", "Names", "LoadState", "FragmentPath", "SourcePath", "UnitFileState", "NeedDaemonReload")
        argv = [self._systemctl_bin]
        if not self._system:
            argv.append("--user")
        argv.extend(["show", unit_name, *["--property=" + name for name in names]])
        if time.monotonic() >= deadline:
            raise ServiceManagerError("nonmanaged_selection_unavailable")
        try:
            child = subprocess.Popen(
                argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                pass_fds=(() if _MANAGED_LOCK.get() is None else (_MANAGED_LOCK.get(),)),
            )
            try:
                raw, overflow = _capture_bounded_query_output(child, deadline)
                remaining = deadline - time.monotonic()
                if overflow or remaining <= 0:
                    raise ServiceManagerError("nonmanaged_selection_unavailable")
                code = child.wait(timeout=remaining)
            finally:
                # Reuse the original deadline, never the helper's renewed
                # nonmanaged cleanup grace. A refused read cannot admit effects.
                _close_and_reap_query_process(child, deadline, managed=True)
            if time.monotonic() >= deadline:
                raise ServiceManagerError("nonmanaged_selection_unavailable")
            text = raw.decode("utf-8")
        except (OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
            raise ServiceManagerError("nonmanaged_selection_unavailable") from exc
        values: dict[str, str] = {}
        if not text.endswith("\n"):
            raise ServiceManagerError("nonmanaged_selection_unavailable")
        for line in text[:-1].split("\n"):
            key, separator, value = line.partition("=")
            if (not separator or key not in names or key in values
                    or any(ord(c) < 32 or ord(c) == 127 for c in line)):
                raise ServiceManagerError("nonmanaged_selection_unavailable")
            values[key] = value
        if set(values) != set(names) or time.monotonic() >= deadline:
            raise ServiceManagerError("nonmanaged_selection_unavailable")
        return code, values

    def _require_nonmanaged_owner(self, unit_name: str, *, allow_absent: bool = False) -> None:
        from runtime.remote_access.cli import _SERVICE_QUERY_TIMEOUT_SECONDS
        from runtime.remote_access.linux_package import (
            UNITS, _MANAGED_DEADLINE, _nonmanaged_owner_destination,
            _nonmanaged_owner_same_path, require_nonmanaged_unit_owner,
        )
        inherited = _MANAGED_DEADLINE.get()
        deadline = min(time.monotonic() + _SERVICE_QUERY_TIMEOUT_SECONDS,
                       inherited / 1_000_000_000 if inherited is not None else float("inf"))
        # A writer destination is not a manager selection. Only bare canonical
        # names and named instances can reach the selected-unit metadata read.
        grammar = (r"[A-Za-z0-9_:][A-Za-z0-9_.:-]*(?:@[A-Za-z0-9_:][A-Za-z0-9_.:-]*)?"
                   r"\.(?:service|socket|device|mount|automount|swap|target|path|timer|slice|scope)")
        if (not isinstance(unit_name, str) or len(unit_name) > 255
                or re.fullmatch(grammar, unit_name) is None):
            raise ServiceManagerError("noncanonical_unit_selection")
        # Admit the concrete writer independently of manager metadata and of
        # whether a fixed-named local unit has any managed markers yet.
        self._require_file_operand(self._unit_path(unit_name))
        if self._system:
            require_nonmanaged_unit_owner(unit_name, _SYSTEM_UNIT_DIR)
        code, props = self._nonmanaged_selection_properties(unit_name, deadline)
        names = props["Names"].split(" ")
        if (props["Id"] != unit_name or unit_name not in names or len(set(names)) != len(names)
                or any(len(name) > 255 or re.fullmatch(grammar, name) is None for name in names)):
            # Includes aliases from /run, vendor/generator paths and currently
            # loaded aliases, even when the hypothetical writer file is absent.
            raise ServiceManagerError("nonmanaged_selection_ambiguous")
        if self._system and set(names).intersection(UNITS):
            # Never interpret metadata about a fixed system owner as permission
            # to enter generic lifecycle, even with missing disk/state markers.
            raise ServiceManagerError("managed_lifecycle_owner_required")
        if props["LoadState"] == "not-found":
            # systemctl may return its exact no-such-unit status4. Neither that
            # status alone nor any other show failure is proof of absence.
            if (not allow_absent or code not in (0, 4) or names != [unit_name]
                    or any(props[k] for k in ("FragmentPath", "SourcePath", "UnitFileState"))
                    or props["NeedDaemonReload"] != "no"):
                raise ServiceManagerError("nonmanaged_selection_unavailable")
        elif (code != 0 or props["LoadState"] != "loaded" or not props["FragmentPath"]
              or props["UnitFileState"] not in {
                  "enabled", "enabled-runtime", "linked", "linked-runtime", "static",
                  "disabled", "indirect", "generated", "transient",
              } or props["NeedDaemonReload"] not in ("no", "yes")):
            raise ServiceManagerError("nonmanaged_selection_unavailable")
        for value in (props["FragmentPath"], props["SourcePath"]):
            if not value:
                continue
            path = Path(value)
            if not path.is_absolute() or str(path) != value or ".." in path.parts:
                raise ServiceManagerError("nonmanaged_selection_ambiguous")
            destination = _nonmanaged_owner_destination(path)
            if (self._system and (path.name in UNITS or destination.name in UNITS) or any(
                    _nonmanaged_owner_same_path(destination, _SYSTEM_UNIT_DIR / name) for name in UNITS)):
                raise ServiceManagerError("managed_lifecycle_owner_required")
            require_nonmanaged_unit_owner(
                path.name, path.parent,
                user_unit_dir=None if self._system else _USER_UNIT_DIR,
            )
        # Includes destination metadata work and validation after the child reap.
        # This source check is not an atomic fence against namespace replacement.
        if time.monotonic() >= deadline:
            raise ServiceManagerError("nonmanaged_selection_unavailable")

    def _require_file_operand(self, path: Path, *, directory: bool = False,
                              required: bool = False) -> None:
        from runtime.remote_access.linux_package import (
            PackageError, _require_nonmanaged_state_owner, require_nonmanaged_unit_owner,
        )
        try:
            _require_nonmanaged_state_owner(path, staging=True, directory=directory)
            if not directory:
                require_nonmanaged_unit_owner(
                    path.name, path.parent,
                    user_unit_dir=None if self._system else _USER_UNIT_DIR,
                )
            if required:
                metadata = path.lstat()
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                    raise ServiceManagerError("nonmanaged_operand_unknown")
        except (PackageError, OSError) as exc:
            raise ServiceManagerError("nonmanaged_operand_unknown") from exc

    def _unit_path(self, unit_name: str) -> Path:
        return self.unit_dir / unit_name

    def _backup_path(self, unit_name: str, stamp: str) -> Path:
        return self.backup_dir / f"{unit_name}.{stamp}"

    # ── install / uninstall ───────────────────────────────────────────────

    def install(self, unit_text: str, unit_name: str, *, enable: bool = True) -> Path:
        """Write *unit_text* owner-only, back up any existing unit, reload."""
        self._require_nonmanaged_owner(unit_name, allow_absent=True)
        path = self._unit_path(unit_name)
        if path.exists():
            self._backup_current(unit_name)
        self._require_nonmanaged_owner(unit_name, allow_absent=True)
        self._require_file_operand(path.parent, directory=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._require_file_operand(path)
            path.write_text(unit_text, encoding="utf-8")
            self._require_file_operand(path, required=True)
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError as exc:
            raise ServiceManagerError(f"cannot write unit {path}: {exc}") from exc
        self.daemon_reload()
        if enable:
            self.enable(unit_name)
        return path

    def uninstall(self, unit_name: str) -> None:
        self._require_nonmanaged_owner(unit_name, allow_absent=True)
        try:
            self.stop(unit_name)
        except ServiceManagerError:
            pass  # a not-running unit is fine to uninstall
        try:
            self.disable(unit_name)
        except ServiceManagerError:
            pass
        self._require_nonmanaged_owner(unit_name, allow_absent=True)
        path = self._unit_path(unit_name)
        self._require_file_operand(path)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise ServiceManagerError(f"cannot remove unit {path}: {exc}") from exc
        self.daemon_reload()

    # ── lifecycle verbs ───────────────────────────────────────────────────

    def start(self, unit_name: str) -> None:
        self._require_nonmanaged_owner(unit_name)
        self._systemctl("start", unit_name)

    def stop(self, unit_name: str) -> None:
        self._require_nonmanaged_owner(unit_name)
        self._systemctl("stop", unit_name)

    def restart(self, unit_name: str) -> None:
        self._require_nonmanaged_owner(unit_name)
        self._systemctl("restart", unit_name)

    def enable(self, unit_name: str) -> None:
        self._require_nonmanaged_owner(unit_name)
        self._systemctl("enable", unit_name)

    def disable(self, unit_name: str) -> None:
        self._require_nonmanaged_owner(unit_name)
        self._systemctl("disable", unit_name)

    def status(self, unit_name: str) -> ServiceStatus:
        out = self._systemctl(
            "show",
            "-p", "ActiveState",
            "-p", "SubState",
            "-p", "LoadState",
            "-p", "ExecMainPID",
            "-p", "UnitFileState",
            unit_name,
        )
        props: dict[str, str] = {}
        for line in out.splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                props[key.strip()] = value.strip()
        try:
            pid = int(props.get("ExecMainPID", "0") or "0")
        except ValueError:
            pid = 0
        return ServiceStatus(
            unit_name=unit_name,
            active_state=props.get("ActiveState", "unknown"),
            sub_state=props.get("SubState", "unknown"),
            load_state=props.get("LoadState", "unknown"),
            pid=pid,
            unit_file_state=props.get("UnitFileState", ""),
        )

    def is_active(self, unit_name: str) -> bool:
        argv = [self._systemctl_bin]
        if not self._system:
            argv.append("--user")
        argv += ["is-active", unit_name]
        code, _ = self._run(argv, timeout=10.0)
        return code == 0

    # ── upgrade / rollback ────────────────────────────────────────────────

    def upgrade(
        self, unit_text: str, unit_name: str, *, verify_start: bool = True
    ) -> UpgradeOutcome:
        """Replace the unit, reload, restart, and verify it comes up active.

        A failed start auto-rolls back to the backed-up previous unit when
        one exists. The new unit is never left half-installed on a failure
        with a recoverable previous unit.
        """
        self._require_nonmanaged_owner(unit_name, allow_absent=True)
        backup = self._backup_current(unit_name)
        path = self._unit_path(unit_name)
        self._require_nonmanaged_owner(unit_name, allow_absent=True)
        self._require_file_operand(path.parent, directory=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._require_file_operand(path)
            path.write_text(unit_text, encoding="utf-8")
            self._require_file_operand(path, required=True)
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError as exc:
            raise ServiceManagerError(f"cannot write unit {path}: {exc}") from exc
        self.daemon_reload()
        try:
            self.restart(unit_name)
        except ServiceManagerError:
            return self._rollback_or_report(backup, unit_name)
        if verify_start and not self._wait_active(unit_name):
            return self._rollback_or_report(backup, unit_name)
        return UpgradeOutcome(ok=True, backup_path=backup)

    def rollback(self, unit_name: str) -> UpgradeOutcome:
        """Restore the most recent backup and restart the unit."""
        self._require_nonmanaged_owner(unit_name, allow_absent=True)
        backup = self._latest_backup(unit_name)
        if backup is None:
            raise ServiceManagerError(f"no backup available to roll back {unit_name}")
        self._restore_backup(unit_name, backup)
        self.daemon_reload()
        self.restart(unit_name)
        return UpgradeOutcome(ok=True, backup_path=backup)

    def _rollback_or_report(self, backup: Path | None, unit_name: str) -> UpgradeOutcome:
        self._require_nonmanaged_owner(unit_name, allow_absent=True)
        if backup is None:
            return UpgradeOutcome(ok=False, rolled_back=False)
        try:
            self._restore_backup(unit_name, backup)
            self.daemon_reload()
            self.restart(unit_name)
            return UpgradeOutcome(ok=False, rolled_back=True, backup_path=backup)
        except ServiceManagerError:
            # The rollback itself failed: surface the original failure with
            # the backup path so an operator can recover manually.
            return UpgradeOutcome(ok=False, rolled_back=False, backup_path=backup)

    def _wait_active(self, unit_name: str) -> bool:
        deadline = time.monotonic() + self._verify_seconds
        while time.monotonic() < deadline:
            try:
                if self.is_active(unit_name):
                    return True
            except ServiceManagerError:
                return False
            self._sleep(self._poll_seconds)
        return False

    # ── backup helpers ────────────────────────────────────────────────────

    def _backup_current(self, unit_name: str) -> Path | None:
        self._require_nonmanaged_owner(unit_name, allow_absent=True)
        path = self._unit_path(unit_name)
        self._require_file_operand(path)
        self._require_file_operand(self.backup_dir, directory=True)
        if not path.exists():
            return None
        backup = self._backup_path(unit_name, f"{int(time.time() * 1000)}")
        self._require_file_operand(backup)
        self._require_file_operand(path, required=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._require_file_operand(path, required=True)
            self._require_file_operand(backup)
            backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            self._require_file_operand(backup, required=True)
            os.chmod(backup, stat.S_IRUSR | stat.S_IWUSR)
        except OSError as exc:
            raise ServiceManagerError(f"cannot back up unit {path}: {exc}") from exc
        return backup

    def _latest_backup(self, unit_name: str) -> Path | None:
        self._require_nonmanaged_owner(unit_name, allow_absent=True)
        self._require_file_operand(self.backup_dir, directory=True)
        candidates = sorted(self.backup_dir.glob(f"{unit_name}.*"))
        backup = candidates[-1] if candidates else None
        if backup is not None:
            self._require_file_operand(backup, required=True)
        return backup

    def _restore_backup(self, unit_name: str, backup: Path) -> None:
        self._require_nonmanaged_owner(unit_name, allow_absent=True)
        path = self._unit_path(unit_name)
        self._require_file_operand(backup, required=True)
        self._require_file_operand(path)
        try:
            path.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8")
            self._require_file_operand(path, required=True)
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError as exc:
            raise ServiceManagerError(f"cannot restore unit {path}: {exc}") from exc
