"""Per-user launchd lifecycle; no root, LaunchDaemon, shell, or credential copying.

KeepAlive=True recovers after every exit (including a killed process), with a
10-second launchd throttle. Stop uses bootout so KeepAlive cannot undo it.
Process status is not readiness: launchd has no sd_notify/watchdog contract.
"""
from __future__ import annotations

import os
import plistlib
import re
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable
from xml.parsers.expat import ExpatError

from runtime.remote_access.service_manager import (
    ServiceManagerError, ServiceStatus, UpgradeOutcome, _default_run,
)

LABEL = "ai.happyranch.connector"
_LEGACY_NAME = "happyranch-connector.service"


def launchd_label(unit_name: str) -> str:
    label = LABEL if unit_name == _LEGACY_NAME else unit_name
    if not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", label):
        raise ServiceManagerError("LaunchAgent label must be a reverse-DNS identifier")
    return label


def render_launch_agent(
    unit_name: str, program_arguments: tuple[str, ...], *, logs_dir: Path | None = None,
) -> str:
    """Render argv and log paths only; config/credential contents never enter plist."""
    label = launchd_label(unit_name)
    if not program_arguments or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in program_arguments):
        raise ServiceManagerError("invalid LaunchAgent ProgramArguments")
    if not Path(program_arguments[0]).is_absolute():
        raise ServiceManagerError("LaunchAgent executable must be an absolute path")
    logs = logs_dir or Path.home() / "Library/Logs/HappyRanch"
    value = {
        "Label": label,
        "ProgramArguments": list(program_arguments),
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "LimitLoadToSessionType": ["Aqua", "Background"],
        # Installation alone must not start a process; enable is an explicit
        # launchctl override. --no-enable preserves an existing override.
        "Disabled": True,
        "StandardOutPath": str(logs / f"{label}.stdout.log"),
        "StandardErrorPath": str(logs / f"{label}.stderr.log"),
    }
    try:
        return plistlib.dumps(value, sort_keys=True).decode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ServiceManagerError("invalid LaunchAgent plist values") from exc


@dataclass(frozen=True)
class LaunchdStatus(ServiceStatus):
    """ServiceStatus plus launchd domain/exit observations; Linux is unchanged."""

    domain: str = ""
    last_exit_code: int | None = None
    last_exit_signal: int | None = None


def parse_launchctl_print(label: str, output: str, *, enabled: bool) -> LaunchdStatus:
    """Read only top-level fields; nested endpoint/process state is not job state.

    launchctl print is diagnostic text rather than a stable machine API. Only
    running with a positive PID claims activity. Other nonempty states (including
    future vocabulary) remain inactive; malformed required fields still refuse.
    """
    props: dict[str, str] = {}
    depth = 0
    quoted = False
    escaped = False
    for line in output.splitlines():
        if depth == 1 and not quoted and " = " in line:
            key, _, value = line.strip().partition(" = ")
            if key in {"state", "pid", "last exit code", "last terminating signal"}:
                if key in props:
                    raise ServiceManagerError("duplicate launchctl print field")
                props[key] = value
        for char in line:
            if escaped:
                escaped = False
            elif quoted and char == "\\":
                escaped = True
            elif char == '"':
                quoted = not quoted
            elif not quoted:
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth < 0:
                        raise ServiceManagerError("unbalanced launchctl print braces")
    state = props.get("state")
    if depth != 0 or quoted:
        raise ServiceManagerError("unbalanced launchctl print braces or quotes")
    if state is None or not state.strip():
        raise ServiceManagerError("missing launchctl print state")
    try:
        pid_text = props.get("pid", "0")
        if re.fullmatch(r"-?[0-9]+", pid_text) is None:
            raise ValueError("invalid pid")
        pid = int(pid_text)
        code = props.get("last exit code")
        last_exit = None
        if code not in {None, "(never exited)"}:
            exit_match = re.fullmatch(r"(-?\d+)(?::\s*[A-Z_]+)?", code)
            if exit_match is None:
                raise ValueError("invalid exit code")
            last_exit = int(exit_match[1])
        sig = props.get("last terminating signal")
        last_signal = None
        if sig is not None:
            signal_match = re.fullmatch(r"(?:[^:\n]+:\s*)?(\d+)", sig)
            if signal_match is None:
                raise ValueError("invalid terminating signal")
            last_signal = int(signal_match[1])
    except ValueError as exc:
        raise ServiceManagerError("malformed launchctl print numeric field") from exc
    if pid < 0 or (state == "running" and pid == 0):
        raise ServiceManagerError("launchctl running state has no valid pid")
    return LaunchdStatus(
        unit_name=label, active_state="active" if state == "running" else "inactive",
        sub_state=state[:2048], load_state="loaded", pid=pid if state == "running" else 0,
        unit_file_state="enabled" if enabled else "disabled",
        last_exit_code=last_exit, last_exit_signal=last_signal,
    )


class LaunchdServiceManager:
    """Current user's gui domain, or user domain on a genuinely headless login.

    Only an explicit missing GUI domain or exit 125 unsupported-action
    diagnostic permits fallback to a successfully probed user domain. Other
    launchctl errors refuse. The selected domain is pinned for this instance;
    bootstrap/bootout failures never trigger migration to another domain.
    """

    def __init__(
        self, *, unit_dir: Path | None = None, backup_dir: Path | None = None,
        run: Callable[..., tuple[int, str]] | None = None,
        verify_seconds: float = 5.0, poll_seconds: float = 0.1,
    ) -> None:
        self.unit_dir = unit_dir or Path.home() / "Library/LaunchAgents"
        self.backup_dir = backup_dir or self.unit_dir / "backups"
        self._run = run or _default_run
        self._domain: str | None = None
        self._verify_seconds = verify_seconds
        self._poll_seconds = poll_seconds

    def _call(self, *args: str, timeout: float = 10.0) -> str:
        code, output = self._run(["launchctl", *args], timeout=timeout)
        if code:
            raise ServiceManagerError(f"launchctl {' '.join(args)} failed ({code}): {output.strip()[:200]}")
        return output

    @property
    def domain(self) -> str:
        if self._domain is None:
            uid = os.getuid()
            gui = f"gui/{uid}"
            code, output = self._run(["launchctl", "print", gui], timeout=10.0)
            if code == 0:
                self._domain = gui
            elif (re.search(r'Could not find domain for', output)
                  or (code == 125 and "Domain does not support specified action" in output)):
                user = f"user/{uid}"
                user_code, user_output = self._run(["launchctl", "print", user], timeout=10.0)
                if user_code:
                    raise ServiceManagerError(
                        f"launchctl print {gui} failed ({code}): {output.strip()[:200]}; "
                        f"launchctl print {user} failed ({user_code}): {user_output.strip()[:200]}"
                    )
                self._domain = user
            else:
                raise ServiceManagerError(f"launchctl print {gui} failed ({code}): {output.strip()[:200]}")
        return self._domain

    def _target(self, name: str) -> str:
        return f"{self.domain}/{launchd_label(name)}"

    def _path(self, name: str) -> Path:
        return self.unit_dir / f"{launchd_label(name)}.plist"

    def _print(self, name: str, *, deadline: float | None = None) -> str | None:
        def remaining() -> float:
            if deadline is None:
                return 10.0
            seconds = deadline - time.monotonic()
            if seconds <= 0:
                raise ServiceManagerError("LaunchAgent still loaded after bootout")
            return min(10.0, seconds)

        target = self._target(name)
        code, output = self._run(["launchctl", "print", target], timeout=remaining())
        if not code:
            return output
        label = launchd_label(name)
        if f'Could not find service "{label}" in domain' in output:
            # A vanished domain is not an absent service.
            self._call("print", self.domain, timeout=remaining())
            return None
        raise ServiceManagerError(f"launchctl print {target} failed ({code}): {output.strip()[:200]}")

    def _enabled(self, name: str) -> bool:
        domain = self.domain
        code, output = self._run(["launchctl", "print-disabled", domain], timeout=10.0)
        diagnostic = f"launchctl print-disabled {domain} (exit {code}): {output.strip()[:512]}"
        if code:
            raise ServiceManagerError(f"{diagnostic}; command failed")
        # Only this flat block owns service overrides. A following section's
        # closing brace must not stand in for a missing disabled-block brace.
        block: list[str] | None = None
        for line in output.splitlines():
            if block is None:
                if re.fullmatch(r"\s*disabled services\s*=\s*\(no disabled services\)\s*", line):
                    block = []
                    break  # The observed empty sentinel is already complete.
                if re.fullmatch(r"\s*disabled services\s*=\s*\{\s*", line):
                    block = []
            elif line.strip() == "}":
                break  # Ignore login-item associations and everything after.
            elif not line.lstrip().startswith('"') and re.search(r"=\s*\{\s*$", line):
                raise ServiceManagerError(f"unrecognized launchctl print-disabled output; {diagnostic}")
            else:
                block.append(line)
        else:
            raise ServiceManagerError(f"unrecognized launchctl print-disabled output; {diagnostic}")
        entries = re.findall(r'^\s*"' + re.escape(launchd_label(name)) + r'"([^\n]*)$', "\n".join(block), re.M)
        if entries:
            match = re.fullmatch(r'\s*=>\s*(true|false|disabled|enabled)\s*', entries[0])
            if len(entries) != 1 or match is None:
                raise ServiceManagerError(f"malformed launchctl disabled override; {diagnostic}")
            return match[1] in {"false", "enabled"}
        path = self._path(name)
        return path.exists() and not self._validated(path.read_text(), name).get("Disabled", False)

    @staticmethod
    def _validated(text: str, name: str) -> dict:
        try:
            value = plistlib.loads(text.encode("utf-8"))
            # Round-trip before ANY replacement: equivalent syntax validation
            # to plutil -lint, without spawning a second platform utility.
            if not isinstance(value, dict) or plistlib.loads(plistlib.dumps(value)) != value:
                raise ValueError("not a plist dictionary")
            if value.get("Label") != launchd_label(name):
                raise ValueError("label does not match service")
            args = value.get("ProgramArguments")
            if not isinstance(args, list) or not args or not all(isinstance(arg, str) and arg and "\x00" not in arg for arg in args):
                raise ValueError("invalid ProgramArguments")
            if not Path(args[0]).is_absolute():
                raise ValueError("executable must be absolute")
            return value
        except (ValueError, TypeError, ExpatError, plistlib.InvalidFileException) as exc:
            raise ServiceManagerError(f"invalid LaunchAgent plist: {exc}") from exc

    def _write(self, text: str, name: str) -> Path:
        value = self._validated(text, name)
        path = self._path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        for key in ("StandardOutPath", "StandardErrorPath"):
            if key in value:
                Path(value[key]).parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".plist.pending")
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                os.chmod(temporary, 0o600)
                stream.write(text)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return path

    def _backup(self, name: str) -> Path | None:
        path = self._path(name)
        if not path.exists():
            return None
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        backup = self.backup_dir / f"{path.name}.{time.time_ns()}"
        temporary = None
        try:
            # Hidden pending files cannot match rollback's candidate glob.
            # NamedTemporaryFile creates an exclusive owner-only inode.
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.backup_dir,
                                             prefix=f".{path.name}.", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(path.read_text(encoding="utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, backup)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return backup

    def install(self, unit_text: str, unit_name: str, *, enable: bool = True) -> Path:
        self._validated(unit_text, unit_name)
        self.domain  # Refuse unavailable domains before writing anything.
        self._backup(unit_name)
        path = self._write(unit_text, unit_name)
        if enable:
            self.enable(unit_name)
        return path

    def uninstall(self, unit_name: str) -> None:
        self.stop(unit_name)
        self.disable(unit_name)
        self._path(unit_name).unlink(missing_ok=True)

    def uninstall_agents(self, names: tuple[str, ...], *, before_remove: Callable[[], None]) -> None:
        """Stop every writer before removing any plist; preserve all on stop failure.

        The managed installer rechecks its private layout after the writers stop.
        launchd has no composite target or dependency propagation of its own.
        """
        for name in names:
            self.stop(name)
        before_remove()
        for name in names:
            self.disable(name)
        for name in names:
            self._path(name).unlink(missing_ok=True)

    def start(self, unit_name: str, *, timeout: float = 10.0) -> None:
        enabled = self._enabled(unit_name)
        if not enabled:
            self.enable(unit_name)
        try:
            if self._print(unit_name) is None:
                self._call("bootstrap", self.domain, str(self._path(unit_name)), timeout=timeout)
            else:
                self._call("kickstart", self._target(unit_name), timeout=timeout)
        finally:
            # Manual start must not enable login startup for --no-enable users.
            if not enabled:
                self.disable(unit_name)

    def stop(self, unit_name: str, *, timeout: float = 10.0) -> None:
        if self._print(unit_name) is None:
            if self._print(unit_name) is not None:
                raise ServiceManagerError("LaunchAgent still loaded after bootout")
            return
        self._call("bootout", self._target(unit_name), timeout=timeout)
        # bootout may return while launchd is still waiting for SIGTERM/exit.
        # One post-bootout deadline includes print and its domain recheck.
        deadline = time.monotonic() + 30.0
        while True:
            output = self._print(unit_name, deadline=deadline)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ServiceManagerError("LaunchAgent still loaded after bootout")
            if output is None:
                return
            parse_launchctl_print(launchd_label(unit_name), output, enabled=False)
            time.sleep(min(max(self._poll_seconds, 0.001), 0.5, remaining))

    def restart(self, unit_name: str) -> None:
        enabled = self._enabled(unit_name)
        if not enabled:
            self.enable(unit_name)
        try:
            self._call("kickstart", "-k", self._target(unit_name))
        finally:
            if not enabled:
                self.disable(unit_name)

    def enable(self, unit_name: str) -> None:
        self._call("enable", self._target(unit_name))

    def disable(self, unit_name: str) -> None:
        self._call("disable", self._target(unit_name))

    def status(self, unit_name: str) -> LaunchdStatus:
        output = self._print(unit_name)
        enabled = self._enabled(unit_name)
        if output is None:
            return LaunchdStatus(
                unit_name=launchd_label(unit_name), active_state="inactive", sub_state="dead",
                load_state="unloaded" if self._path(unit_name).exists() else "not-found",
                unit_file_state="enabled" if enabled else "disabled", domain=self.domain,
            )
        return replace(parse_launchctl_print(launchd_label(unit_name), output, enabled=enabled),
                       domain=self.domain)

    def _replace_and_start(self, text: str, name: str) -> None:
        # kickstart alone never rereads a changed plist: bootout/bootstrap is
        # required for upgrade and rollback, whereas restart uses kickstart -k.
        self._validated(text, name)
        self.stop(name)
        self._write(text, name)
        self.start(name)

    def upgrade(self, unit_text: str, unit_name: str, *, verify_start: bool = True) -> UpgradeOutcome:
        self._validated(unit_text, unit_name)
        backup = self._backup(unit_name)
        try:
            self._replace_and_start(unit_text, unit_name)
            if verify_start and not self._wait_running(unit_name):
                raise ServiceManagerError("LaunchAgent did not become running")
            return UpgradeOutcome(ok=True, backup_path=backup)
        except (ServiceManagerError, OSError):
            if backup is not None:
                try:
                    self._replace_and_start(backup.read_text(), unit_name)
                    return UpgradeOutcome(ok=False, rolled_back=self._wait_running(unit_name), backup_path=backup)
                except (ServiceManagerError, OSError):
                    pass
            return UpgradeOutcome(ok=False, backup_path=backup)

    def rollback(self, unit_name: str) -> UpgradeOutcome:
        backups = sorted(self.backup_dir.glob(f"{self._path(unit_name).name}.*"))
        if not backups:
            raise ServiceManagerError(f"no backup available to roll back {unit_name}")
        backup = backups[-1]
        try:
            text = backup.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ServiceManagerError("cannot read LaunchAgent backup") from exc
        self._replace_and_start(text, unit_name)
        return UpgradeOutcome(ok=self._wait_running(unit_name), backup_path=backup)

    def _wait_running(self, name: str) -> bool:
        deadline = time.monotonic() + self._verify_seconds
        while time.monotonic() < deadline:
            if self.status(name).running:
                return True
            time.sleep(self._poll_seconds)
        return False
