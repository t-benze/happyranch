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

    def _unit_path(self, unit_name: str) -> Path:
        return self.unit_dir / unit_name

    def _backup_path(self, unit_name: str, stamp: str) -> Path:
        return self.backup_dir / f"{unit_name}.{stamp}"

    # ── install / uninstall ───────────────────────────────────────────────

    def install(self, unit_text: str, unit_name: str, *, enable: bool = True) -> Path:
        """Write *unit_text* owner-only, back up any existing unit, reload."""
        path = self._unit_path(unit_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            self._backup_current(unit_name)
        try:
            path.write_text(unit_text, encoding="utf-8")
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError as exc:
            raise ServiceManagerError(f"cannot write unit {path}: {exc}") from exc
        self.daemon_reload()
        if enable:
            self.enable(unit_name)
        return path

    def uninstall(self, unit_name: str) -> None:
        try:
            self.stop(unit_name)
        except ServiceManagerError:
            pass  # a not-running unit is fine to uninstall
        try:
            self.disable(unit_name)
        except ServiceManagerError:
            pass
        path = self._unit_path(unit_name)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise ServiceManagerError(f"cannot remove unit {path}: {exc}") from exc
        self.daemon_reload()

    # ── lifecycle verbs ───────────────────────────────────────────────────

    def start(self, unit_name: str) -> None:
        self._systemctl("start", unit_name)

    def stop(self, unit_name: str) -> None:
        self._systemctl("stop", unit_name)

    def restart(self, unit_name: str) -> None:
        self._systemctl("restart", unit_name)

    def enable(self, unit_name: str) -> None:
        self._systemctl("enable", unit_name)

    def disable(self, unit_name: str) -> None:
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
        backup = self._backup_current(unit_name)
        path = self._unit_path(unit_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(unit_text, encoding="utf-8")
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
        backup = self._latest_backup(unit_name)
        if backup is None:
            raise ServiceManagerError(f"no backup available to roll back {unit_name}")
        self._restore_backup(unit_name, backup)
        self.daemon_reload()
        self.restart(unit_name)
        return UpgradeOutcome(ok=True, backup_path=backup)

    def _rollback_or_report(self, backup: Path | None, unit_name: str) -> UpgradeOutcome:
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
        path = self._unit_path(unit_name)
        if not path.exists():
            return None
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        backup = self._backup_path(unit_name, f"{int(time.time() * 1000)}")
        try:
            backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            os.chmod(backup, stat.S_IRUSR | stat.S_IWUSR)
        except OSError as exc:
            raise ServiceManagerError(f"cannot back up unit {path}: {exc}") from exc
        return backup

    def _latest_backup(self, unit_name: str) -> Path | None:
        candidates = sorted(self.backup_dir.glob(f"{unit_name}.*"))
        return candidates[-1] if candidates else None

    def _restore_backup(self, unit_name: str, backup: Path) -> None:
        path = self._unit_path(unit_name)
        try:
            path.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8")
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError as exc:
            raise ServiceManagerError(f"cannot restore unit {path}: {exc}") from exc
