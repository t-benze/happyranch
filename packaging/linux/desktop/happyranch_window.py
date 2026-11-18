#!/usr/bin/python3
"""System-Python GTK shell. No HappyRanch imports or bundled Python dependencies."""
from __future__ import annotations

import argparse
import ast
import fcntl
import hashlib
import http.client
import html
import json
import logging
import os
from pathlib import Path
import queue
import re
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import IO, Callable
import urllib.error
import urllib.request


class LaunchError(Exception):
    """A diagnostic safe to show inside the desktop window."""

    def __init__(self, message: str, action: str | None = None) -> None:
        super().__init__(message)
        self.action = action


def desktop_home(user_home: Path | None = None, *,
                 environment: dict[str, str] | None = None) -> Path:
    """Shared by the packaged unit launcher, window and port query."""
    account = user_home if user_home is not None else Path.home()
    env = os.environ if environment is None else environment
    data = Path(env.get("XDG_DATA_HOME", ""))
    return (data if data.is_absolute() else account / ".local/share") / "happyranch"


def acquire_lock(home: Path) -> IO[bytes] | None:
    """Keep the inode in place: unlinking a held flock creates a launch race."""
    if home.resolve() == (Path.home() / ".happyranch").resolve():
        raise LaunchError("The desktop window cannot use the development daemon home.")
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (home / "desktop.lock").open("ab")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return None
    return lock


def prepare_home(home: Path) -> None:
    # Even stale/partial state is not ours. Never attach, signal its PID, or
    # remove its markers. The user's CLI does not participate in desktop.lock.
    if any(os.path.lexists(home / name) for name in ("daemon.pid", "daemon.port")):
        raise LaunchError("Existing daemon state in this home; choose a separate desktop home.")


def child_environment(prefix: Path, home: Path) -> dict[str, str]:
    env = {k: os.environ[k] for k in ("HOME", "PATH", "LANG", "LC_ALL", "LC_CTYPE")
           if k in os.environ}
    env.update(HAPPYRANCH_DAEMON_HOME=str(home), HAPPYRANCH_DAEMON_PORT="0",
               HAPPYRANCH_DAEMON_BIND_HOST="127.0.0.1",
               HAPPYRANCH_WEB_DIST=str(prefix / "web/dist"))
    return env


def read_owned_port(home: Path, pid: int) -> int | None:
    """A port is usable only alongside our child's published PID."""
    try:
        owner = (home / "daemon.pid").read_text().strip()
        raw = (home / "daemon.port").read_text().strip()
        if owner != str(pid) or not raw.isascii() or not raw.isdecimal():
            return None
        port = int(raw)
        return port if 1 <= port <= 65535 else None
    except (OSError, ValueError):
        return None


def health_ok(port: int, timeout: float) -> bool:
    # Never send a loopback health request through an inherited HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/api/v1/health", timeout=timeout) as response:
            body = json.loads(response.read(65536))
            return response.status == 200 and isinstance(body, dict) and body.get("status") == "ok"
    except urllib.error.HTTPError as exc:
        exc.close()
        return False
    except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException):
        return False


class ServiceMode:
    """Packaged user-service lifecycle, independent of GTK and child ownership."""

    unit = "happyranch-daemon"
    fragment = "/usr/lib/systemd/user/happyranch-daemon.service"
    base_path = "/opt/happyranch-desktop/bin:/usr/local/bin:/usr/bin:/bin"
    legacy_sha = "482dee228bce0f41354ea7a803341b1b6cdb4076a41c713467b10d348f4a2817"

    def __init__(self, home: Path, *, user_home: Path | None = None,
                 proc: Path = Path("/proc"), runner: Callable = subprocess.run,
                 health: Callable = health_ok) -> None:
        self.home = home
        self.user_home = user_home if user_home is not None else Path.home()
        self.proc = proc
        self.runner = runner
        self.health = health
        self.attached = False
        self.shutdown_pending = False

    def command(self, *args: str) -> subprocess.CompletedProcess:
        try:
            stopping = args[:3] in (("systemctl", "--user", "stop"),
                                    ("systemctl", "--user", "restart"))
            return self.runner(list(args), capture_output=True, text=True,
                               timeout=60 if stopping else 10)
        except (OSError, subprocess.TimeoutExpired) as exc:
            # Do not include command output or environment values in diagnostics.
            raise LaunchError(f"Could not run {args[0]}.") from exc

    def control(self, verb: str) -> None:
        if self.command("systemctl", "--user", verb, self.unit).returncode:
            raise LaunchError(f"Could not {verb} the HappyRanch user service.")

    def manager_available(self) -> bool:
        try:
            result = self.command("systemctl", "--user", "is-system-running")
        except LaunchError:
            return False
        # A degraded manager returns 1, but is still a working user manager.
        return result.returncode in (0, 1) and result.stdout.strip() in {
            "running", "degraded", "starting", "initializing", "maintenance"}

    @property
    def legacy_unit(self) -> Path:
        return self.user_home / ".config/systemd/user/happyranch-release.service"

    def legacy_snapshot(self) -> tuple[bytes, os.stat_result] | None:
        path = self.legacy_unit
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(metadata.st_mode):
            raise LaunchError("The hand-written happyranch-release.service is not a regular file; stop and review it.")
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
            opened = os.fstat(stream.fileno())
            if opened != metadata or not stat.S_ISREG(opened.st_mode):
                raise LaunchError("The hand-written unit changed while being inspected; review it yourself.")
            content = stream.read(65537)
        if len(content) > 65536:
            raise LaunchError("A different hand-written happyranch-release.service exists. Review it yourself before starting; HappyRanch will not change it.")
        return content, metadata

    @staticmethod
    def unit_identity(metadata: os.stat_result) -> tuple:
        # Reads can update atime; identity and modification times must remain.
        return (metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_nlink,
                metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns)

    def check_legacy(self) -> None:
        snapshot = self.legacy_snapshot()
        if snapshot is None:
            return
        if hashlib.sha256(snapshot[0]).hexdigest() == self.legacy_sha:
            raise LaunchError("An older hand-written happyranch-release.service must be removed before using the packaged service. Remove that exact file and reload the user manager?", "remove")
        raise LaunchError("A different hand-written happyranch-release.service exists. Review it yourself before starting; HappyRanch will not change it.")

    def remove_legacy(self) -> None:
        """Called only by the explicit Remove button; recheck the exact file."""
        snapshot = self.legacy_snapshot()
        if snapshot is None:
            raise LaunchError("The old unit is no longer present; retry opening HappyRanch.")
        if hashlib.sha256(snapshot[0]).hexdigest() != self.legacy_sha:
            raise LaunchError("A different hand-written happyranch-release.service exists. Review it yourself before starting; HappyRanch will not change it.")
        if self.unit_identity(self.legacy_unit.lstat()) != self.unit_identity(snapshot[1]):
            raise LaunchError("The hand-written unit changed before removal; review it yourself.")
        self.legacy_unit.unlink()
        if self.command("systemctl", "--user", "daemon-reload").returncode:
            raise LaunchError("The old unit was removed, but the user manager could not reload.")

    def manager_home(self) -> Path:
        result = self.command("systemctl", "--user", "show-environment")
        if result.returncode:
            raise LaunchError("Could not inspect the user manager environment.")
        polluted = sorted({line.partition("=")[0] for line in result.stdout.splitlines()
                           if line.startswith("HAPPYRANCH_")})
        if polluted:
            raise LaunchError("Remove these variables from the user manager environment: " + ", ".join(polluted))
        environment = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        value = environment.get("XDG_DATA_HOME", "")
        if value.startswith("$'"):
            # show-environment uses dollar-single-quote C escaping for spaces,
            # quotes and control bytes. Decode a literal; never run a shell.
            try:
                environment["XDG_DATA_HOME"] = ast.literal_eval(value[1:])
            except (SyntaxError, ValueError) as exc:
                raise LaunchError("Could not decode the user manager XDG_DATA_HOME.") from exc
        return desktop_home(self.user_home, environment=environment)

    def validate_environment(self) -> None:
        if self.home.resolve() != self.manager_home().resolve():
            raise LaunchError("The packaged service requires its XDG release home; remove --daemon-home.")
        path = self.user_home / ".config/happyranch/daemon.env"
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISREG(metadata.st_mode):
            raise LaunchError("daemon.env must be a regular file containing only PATH additions.")
        rejected = set()
        try:
            content = path.read_text()
        except UnicodeError as exc:
            raise LaunchError("daemon.env is not valid UTF-8; only PATH additions are allowed.") from exc
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith(("#", ";")):
                continue
            key, separator, value = line.partition("=")
            key = key.strip()
            if not separator or re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", key) is None:
                rejected.add("<invalid line>")
                continue
            if key != "PATH":
                rejected.add(key)
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            parts = value.split(":")
            base = self.base_path.split(":")
            if (any(not part.startswith("/") or any(c in part for c in "$%\\\"' \t")
                    for part in parts) or
                    not any(parts[i:i + len(base)] == base for i in range(len(parts)))):
                rejected.add("PATH")
        if rejected:
            raise LaunchError("Unsupported daemon.env keys (only literal PATH additions are allowed): " + ", ".join(sorted(rejected)))

    def state(self) -> dict[str, str]:
        result = self.command("systemctl", "--user", "show", self.unit,
                              "-p", "MainPID", "-p", "FragmentPath", "-p", "DropInPaths",
                              "-p", "ActiveState", "-p", "Result", "-p", "UnitFileState",
                              "-p", "NeedDaemonReload", "-p", "ExecMainStartTimestampMonotonic")
        if result.returncode:
            raise LaunchError("Could not inspect the HappyRanch user service.")
        state = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if state.get("FragmentPath") != self.fragment or state.get("DropInPaths") != "":
            raise LaunchError("HappyRanch requires the packaged user unit with no drop-ins. Review your user service overrides.")
        return state

    def lifetime_state(self) -> str:
        """Always observe both controls; desktop.json never determines ON."""
        enabled = self.state().get("UnitFileState") == "enabled"
        linger = self.command("loginctl", "show-user", str(os.getuid()), "-p", "Linger", "--value")
        if linger.returncode or linger.stdout.strip() not in {"yes", "no"}:
            raise LaunchError("Could not inspect login linger; close again to retry.")
        lingering = linger.stdout.strip() == "yes"
        return "on" if enabled and lingering else "partial" if enabled != lingering else "off"

    @property
    def desktop_config(self) -> Path:
        return self.user_home / ".config/happyranch/desktop.json"

    def app_set_linger(self) -> bool:
        try:
            data = json.loads(self.desktop_config.read_text(encoding="utf-8"))
            return isinstance(data, dict) and data.get("linger_set_by_app") is True
        except (OSError, ValueError):
            return False

    def save_linger(self, owned: bool) -> None:
        path = self.desktop_config
        path.parent.mkdir(parents=True, exist_ok=True)
        pending = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=".desktop-", delete=False) as stream:
                pending = Path(stream.name)
                json.dump({"linger_set_by_app": owned}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(pending, path)
        finally:
            if pending is not None:
                pending.unlink(missing_ok=True)

    def set_linger(self, enabled: bool) -> None:
        # Gio is already a system GTK dependency; no bundled Python/D-Bus package.
        from gi.repository import Gio, GLib
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            bus.call_sync("org.freedesktop.login1", "/org/freedesktop/login1",
                          "org.freedesktop.login1.Manager", "SetUserLinger",
                          GLib.Variant("(ubb)", (os.getuid(), enabled, True)), None,
                          Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION, 10000, None)
        except GLib.Error as exc:
            if "AccessDenied" in str(exc):
                raise LaunchError("A local polkit rule denied keeping HappyRanch running while logged out.") from exc
            raise LaunchError("Could not change login linger. Review the logind service.") from exc

    def set_keep_running(self, enabled: bool) -> None:
        if not self.manager_available():
            raise LaunchError("Keep running is unavailable because there is no user manager.")
        state = self.state()  # Validate packaged unit before enable/disable.
        owned = self.app_set_linger()
        if enabled:
            linger = self.command("loginctl", "show-user", str(os.getuid()), "-p", "Linger", "--value")
            if linger.returncode or linger.stdout.strip() not in {"yes", "no"}:
                raise LaunchError("Could not inspect login linger.")
            changed = linger.stdout.strip() == "no"
            self.set_linger(True)
            unit_enabled = False
            try:
                self.control("enable")
                unit_enabled = True
                self.save_linger(owned or changed)
            except (LaunchError, OSError):
                if changed:
                    try:
                        self.set_linger(False)
                    except LaunchError:
                        self.save_linger(True)  # Retain undo custody if rollback fails.
                        raise
                if unit_enabled and state.get("UnitFileState") != "enabled":
                    self.control("disable")
                raise
        else:
            self.control("disable")
            if owned:
                self.set_linger(False)
            self.save_linger(False)

    def update_installed(self, prefix: Path) -> bool:
        """Best-effort install-time substitute for a running-build identity."""
        if not self.attached:
            return False
        try:
            state = self.state()
            with (prefix / "build-info.json").open("rb") as stream:
                stream.read(1)  # Require readable metadata, without consuming build fields.
                installed = os.fstat(stream.fileno()).st_ctime
            result = self.command("busctl", "--user", "get-property", "org.freedesktop.systemd1",
                                  "/org/freedesktop/systemd1/unit/happyranch_2ddaemon_2eservice",
                                  "org.freedesktop.systemd1.Service", "ExecMainStartTimestamp")
            raw = re.fullmatch(r"t ([0-9]+)", result.stdout.strip())
            if result.returncode or raw is None or not 0 < int(raw[1]) < 2**64 - 1:
                raise ValueError("invalid service start timestamp")
            # Recorded CLOCK_REALTIME microseconds, unaffected by later suspend.
            started = int(raw[1]) / 1_000_000
            return state.get("NeedDaemonReload") == "yes" or installed > started
        except (OSError, ValueError, OverflowError, LaunchError):
            logging.getLogger(__name__).warning("Could not compare HappyRanch install and service start times.")
            return False

    def reload_if_needed(self) -> dict[str, str]:
        state = self.state()
        if state.get("NeedDaemonReload") == "yes":
            try:
                result = self.command("systemctl", "--user", "daemon-reload")
            except LaunchError as exc:
                raise LaunchError("Could not reload the user manager.", "retry") from exc
            if result.returncode:
                raise LaunchError("Could not reload the user manager.", "retry")
            state = self.state()
        return state

    def restart(self, cancel: threading.Event, *, timeout: float = 60) -> str:
        try:
            # Revalidate the complete current ownership proof before restart as well.
            if self.start(cancel, timeout=timeout) is None:
                raise LaunchError("The user manager is unavailable; restart HappyRanch manually.")
            self.reload_if_needed()
            self.state()
            self.attached = False
            self.control("restart")
            return self.wait_for_attachment(cancel, timeout=timeout)
        except (OSError, LaunchError):
            # Prior attachment cannot discharge a failed restart's stop obligation.
            self.attached = False
            raise

    def close_window(self) -> None:
        if self.attached and self.lifetime_state() == "on":
            self.shutdown_pending = False
            self.attached = False
            return
        self.close()

    def pid(self, state: dict[str, str]) -> int:
        raw = state.get("MainPID", "")
        if not raw.isascii() or not raw.isdecimal():
            raise LaunchError("The user service did not report a valid MainPID.")
        return int(raw)

    def read_marker(self, name: str) -> int | None:
        try:
            path = self.home / name
            before = path.lstat()
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or before.st_uid != os.getuid() or before.st_size > 64):
                raise LaunchError(f"Invalid {name}; require a small owned regular marker without links.")
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
                if self.unit_identity(os.fstat(stream.fileno())) != self.unit_identity(before):
                    raise LaunchError(f"Changed {name}; retry after daemon publication.")
                data = stream.read(65)
                if (self.unit_identity(os.fstat(stream.fileno())) != self.unit_identity(before)
                        or self.unit_identity(path.lstat()) != self.unit_identity(before)):
                    raise LaunchError(f"Changed {name}; retry after daemon publication.")
            raw = data.decode("utf-8").strip()
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError) as exc:
            raise LaunchError(f"Could not read {name}; review the release-home marker before starting HappyRanch.") from exc
        try:
            if not raw.isascii() or not raw.isdecimal():
                raise ValueError
            value = int(raw)
            if value <= 0 or (name == "daemon.port" and value > 65535):
                raise ValueError
        except ValueError as exc:
            raise LaunchError(f"Invalid {name}; review the release-home marker before starting HappyRanch.") from exc
        return value

    def owned_port(self, pid: int) -> int | None:
        # Missing/partial/invalid publication remains pending only after an
        # admitted start. read_marker is strict in the pre-start/query path.
        try:
            return self.read_marker("daemon.port") if self.read_marker("daemon.pid") == pid else None
        except LaunchError:
            return None

    def refuse_external(self, pid: int) -> None:
        owner = self.read_marker("daemon.pid")
        self.read_marker("daemon.port")
        if owner is not None and owner != pid and (self.proc / str(owner)).exists():
            raise LaunchError("A CLI-started daemon is running in the release home. Stop it yourself before opening HappyRanch.")

    def listeners(self, port: int | None) -> set[str]:
        if port is None or port == 8765:
            return set()
        inodes = set()
        for name in ("tcp", "tcp6"):
            try:
                lines = (self.proc / "net" / name).read_text().splitlines()[1:]
            except FileNotFoundError:
                if name == "tcp6":
                    continue
                raise
            for line in lines:
                fields = line.split()
                if len(fields) < 10:
                    raise LaunchError("Could not inspect the release port listener.")
                address, raw_port = fields[1].split(":")
                if raw_port.upper() == f"{port:04X}" and fields[3] == "0A" and address.upper() in {
                        "0100007F", "00000000", "0" * 32, "0000000000000000FFFF00000100007F"}:
                    inodes.add(fields[9])
        return inodes

    def owns_listener(self, pid: int, listeners: set[str]) -> bool:
        if pid <= 0 or not listeners:
            return False
        sockets = set()
        for fd in (self.proc / str(pid) / "fd").iterdir():
            try:
                target = os.readlink(fd)
            except FileNotFoundError:
                continue
            if target.startswith("socket:[") and target.endswith("]"):
                sockets.add(target[8:-1])
        return listeners <= sockets

    def crash_error(self, state: dict[str, str]) -> None:
        if state.get("ActiveState") == "failed" and state.get("Result") == "start-limit-hit":
            result = self.command("journalctl", "--user", "-u", self.unit, "-n", "20", "--no-pager")
            detail = "\n".join(result.stdout.splitlines()[-20:]) if result.returncode == 0 else "Journal unavailable."
            raise LaunchError("HappyRanch has reached its restart limit.\n" + detail, "retry")

    def start(self, cancel: threading.Event, *, retry: bool = False,
              timeout: float = 60) -> str | None:
        if not self.manager_available():
            return None
        self.check_legacy()
        self.validate_environment()
        state = self.reload_if_needed()
        pid = self.pid(state)
        self.refuse_external(pid)
        port = self.read_marker("daemon.port")
        if port == 8765:
            raise LaunchError("Release daemon ownership or health check failed: development port 8765 is excluded.")
        listeners = self.listeners(port)
        if listeners and not self.owns_listener(pid, listeners):
            raise LaunchError(f"Port {port} is used by another program")
        if retry:
            self.control("reset-failed")
        else:
            self.crash_error(state)
        # start can fail because the unit has just hit its start limit.
        started = self.command("systemctl", "--user", "start", self.unit)
        if started.returncode:
            self.crash_error(self.state())
            raise LaunchError("Could not start the HappyRanch user service.")
        self.shutdown_pending = True
        return self.wait_for_attachment(cancel, timeout=timeout)

    def verified_port(self, state: dict[str, str]) -> int | None:
        """One proof for GTK attachment and the read-only CLI query."""
        pid = self.pid(state)
        port = self.owned_port(pid)
        if port is None or port == 8765:
            return None
        listeners = self.listeners(port)
        owns = self.owns_listener(pid, listeners)
        if listeners and not owns:
            raise LaunchError(f"Port {port} is used by another program")
        if state.get("ActiveState") == "active" and owns and self.health(port, 0.5):
            # Re-observe after HTTP before releasing either the URL or port.
            if (self.state() == state and self.owned_port(pid) == port
                    and self.owns_listener(pid, self.listeners(port))):
                return port
        return None

    def current_port(self) -> int:
        """Inspect only: never start, stop, enable, repair or migrate anything."""
        if not self.manager_available():
            raise LaunchError("The user manager is unavailable; no release port can be verified.")
        self.check_legacy()
        self.validate_environment()
        state = self.state()
        self.refuse_external(self.pid(state))
        port = self.verified_port(state)
        if port is None:
            raise LaunchError("Release daemon ownership or health check failed; no port was printed.")
        return port

    def wait_for_attachment(self, cancel: threading.Event, *, timeout: float = 60) -> str:
        deadline = time.monotonic() + timeout
        while True:
            state = self.state()
            self.crash_error(state)
            # After an admitted start, write_text can expose missing, empty or
            # partial markers. The tolerant read below waits for the complete
            # PID/port pair within this deadline; pre-start validation is strict.
            port = self.verified_port(state)
            if port is not None:
                self.attached = True
                return f"http://127.0.0.1:{port}/"
            if cancel.is_set():
                # The supervisor owns shutdown, including cancellation before
                # attachment. A failed stop must retain its worker and lock.
                raise LaunchError("Startup cancelled.")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LaunchError("Release daemon ownership or health check failed; no web view was attached.", "retry")
            cancel.wait(min(0.1, remaining))

    def close(self, *, keep_running: bool = False) -> None:
        if self.shutdown_pending and not keep_running:
            try:
                self.state()  # Refuse changed unit provenance before a stop.
                self.control("stop")
            except LaunchError as exc:
                raise LaunchError("HappyRanch shutdown failed. Close the window again to retry.\n" + str(exc)) from exc
            self.shutdown_pending = False
            self.attached = False


def wait_for_health(child: subprocess.Popen, home: Path, cancel: threading.Event,
                    timeout: float = 60) -> str:
    deadline = time.monotonic() + timeout
    while not cancel.is_set():
        if child.poll() is not None:
            raise LaunchError(f"Daemon exited during startup (status {child.returncode}).")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LaunchError("Daemon health check timed out.")
        port = read_owned_port(home, child.pid)
        if port is not None and health_ok(port, min(0.5, remaining)):
            if child.poll() is None and read_owned_port(home, child.pid) == port:
                return f"http://127.0.0.1:{port}/"
        cancel.wait(min(0.1, remaining))
    raise LaunchError("Startup cancelled.")


def stop_child(child: subprocess.Popen, home: Path, term_timeout: float = 60,
               kill_timeout: float = 2, *, markers_absent_at_spawn: bool = False) -> None:
    """Signal only the Popen child; bounded TERM/KILL waits also reap it."""
    pid_file, port_file = home / "daemon.pid", home / "daemon.port"
    pinned: dict[Path, int] = {}
    try:
        # Pin both inodes before TERM: even an empty PID may be mid-write.
        # O_PATH never follows a symlink or reads a device/FIFO.
        for path in (pid_file, port_file):
            try:
                pinned[path] = os.open(path, os.O_PATH | os.O_NOFOLLOW)
            except FileNotFoundError:
                pass
        if child.poll() is None:
            try:
                child.terminate()
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=term_timeout)
            except subprocess.TimeoutExpired:
                try:
                    child.kill()
                except ProcessLookupError:
                    pass
                try:
                    child.wait(timeout=kill_timeout)
                except subprocess.TimeoutExpired as exc:
                    raise LaunchError("Daemon stop incomplete; child has not exited.") from exc
        else:
            child.wait()
        if not markers_absent_at_spawn:
            return
        remaining = []
        for path in (pid_file, port_file):
            try:
                # Publication may start during TERM. The admitted empty home
                # and confirmed reap own that late publication as well.
                if path not in pinned:
                    pinned[path] = os.open(path, os.O_PATH | os.O_NOFOLLOW)
                before, after = os.fstat(pinned[path]), path.lstat()
            except FileNotFoundError:
                continue
            if (not stat.S_ISREG(after.st_mode) or after.st_nlink != 1 or
                    (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)):
                return
            remaining.append(path)
        if pid_file in remaining:
            owner = pid_file.read_bytes()
            # Path.write_text can leave no digits or any decimal prefix when
            # interrupted before the daemon installs its cleanup handlers.
            if owner and not (owner.isdigit() and str(child.pid).encode("ascii").startswith(owner)):
                return
        # Validate the whole pair before removing either marker. A replacement
        # PID or port protects the remaining state even if its bytes match.
        for path in remaining:
            before, after = os.fstat(pinned[path]), path.lstat()
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                return
        for path in remaining:
            path.unlink(missing_ok=True)
    finally:
        for fd in pinned.values():
            os.close(fd)


class RemoteAccessPanel:
    """Unprivileged presentation of the helper's fixed, no-input interface.

    A dedicated bus connection keeps its selection cursor private to this window.
    No local state, hostname, label or fingerprint is sent as mutation authority.
    """

    bus_name = "ai.happyranch.RemoteAccess1"
    object_path = "/ai/happyranch/RemoteAccess1"
    mutations = frozenset({"ApprovePending", "RejectPending", "RevokeSelected"})

    def __init__(self, tr: Callable[[str, str], str]) -> None:
        from gi.repository import Gio, GLib, Gtk
        self.Gio, self.GLib, self.Gtk, self.tr = Gio, GLib, Gtk, tr
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.box.set_border_width(12)
        self.connection = None
        self.cancellable = Gio.Cancellable()
        self.snapshot = None
        self.busy, self.closed, self.expanded = False, False, False
        self.state = tr("Checking remote access…", "正在检查远程访问…")
        self.notice = ""
        self.target = tr("HappyRanch on ", "此电脑上的 HappyRanch：") + self.plain(socket.gethostname())
        self.refresh()

    @staticmethod
    def plain(value: str) -> str:
        # Helper metadata is already HTML-escaped. Decode once into a *plain*
        # Gtk.Label; markup is never enabled. Bound local hostname the same way.
        return "".join(c for c in html.unescape(value)[:96] if c.isprintable())

    @staticmethod
    def read_snapshot(value: object) -> dict:
        if (not isinstance(value, dict) or set(value) != {"phase", "ready", "devices", "pending", "selected"}
                or value["phase"] not in ("off", "pending", "ready", "paused", "failed")
                or type(value["ready"]) is not bool or not isinstance(value["devices"], list)
                or len(value["devices"]) > 32):
            raise ValueError("invalid helper status")
        for device in [*value["devices"], value["pending"], value["selected"]]:
            if device is None:
                continue
            if (not isinstance(device, dict) or set(device) != {"label", "fingerprint", "comparison_phrase"}
                    or any(not isinstance(v, str) or len(v) > 576 for v in device.values())
                    or re.fullmatch("[0-9a-f]{24}", device["fingerprint"]) is None):
                raise ValueError("invalid helper device")
        if (any(device is None for device in value["devices"])
                or value["selected"] is not None and value["selected"] not in value["devices"]):
            raise ValueError("invalid helper selection")
        return value

    def refresh(self) -> None:
        if self.closed or self.busy:
            return
        if self.connection is not None and self.connection.is_closed():
            self.connection = None
        if self.connection is not None:
            self.request("Status")
            return
        self.busy = True
        self.render()
        try:
            self.Gio.DBusConnection.new_for_address("unix:path=/run/dbus/system_bus_socket",
                self.Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT |
                self.Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
                None, self.cancellable, self.connected, None)
        except Exception:
            self.unavailable()

    def connected(self, _source: object, result: object, _data: object) -> None:
        try:
            connection = self.Gio.DBusConnection.new_for_address_finish(result)
            if self.closed:
                connection.close(None, None, None)
                return
            connection.set_exit_on_close(False)
            self.connection, self.busy = connection, False
            self.request("Status")
        except Exception as error:
            if not self.closed:
                self.unavailable(error)

    def unavailable(self, error: Exception | None = None) -> None:
        self.busy, self.snapshot = False, None
        remote = ""
        if error is not None and isinstance(error, self.GLib.Error):
            remote = self.Gio.DBusError.get_remote_error(error)
        self.state = (self.tr("Not set up", "尚未设置") if remote in {
            "org.freedesktop.DBus.Error.ServiceUnknown", "org.freedesktop.DBus.Error.NameHasNoOwner"}
            else self.tr("Remote access unavailable — Retry", "远程访问不可用 — 重试"))
        self.render()

    def request(self, method: str) -> None:
        if (self.closed or self.busy or self.connection is None
                or method not in {"Status", "NextDevice", *self.mutations}):
            return
        # UI eligibility is convenience only. The helper independently captures
        # the current tuple, asks polkit, revalidates and commits (or refuses).
        if method in self.mutations:
            key = "selected" if method == "RevokeSelected" else "pending"
            if self.snapshot is None or self.snapshot[key] is None:
                return
        self.busy = True
        self.notice = (self.tr("Waiting for system permission…", "正在等待系统授权…")
                       if method in self.mutations else "")
        self.render()
        try:
            self.connection.call(self.bus_name, self.object_path, self.bus_name, method,
                None, self.GLib.VariantType.new("(s)"),
                self.Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION if method in self.mutations
                else self.Gio.DBusCallFlags.NONE,
                122000 if method in self.mutations else 3000, self.cancellable, self.replied, method)
        except Exception:
            self.failed(method)

    def replied(self, connection: object, result: object, method: str) -> None:
        if self.closed:
            return
        try:
            raw, = connection.call_finish(result).unpack()
            if not isinstance(raw, str) or len(raw.encode("utf-8")) > 32768:
                raise ValueError("invalid helper reply")
            value = json.loads(raw)
            if method in self.mutations:
                if value != "ok":
                    raise ValueError("unsuccessful helper reply")
                # Never predict the resulting list from a click. Only the next
                # successful Status reply supplies the new displayed devices.
                self.snapshot, self.busy = None, False
                self.state = self.tr("Refreshing remote access…", "正在刷新远程访问…")
                self.request("Status")
            else:
                self.snapshot = self.read_snapshot(value)
                self.busy = False
                if value["phase"] == "off":
                    self.state = self.tr("Not set up", "尚未设置")
                elif value["phase"] == "failed":
                    self.state = self.tr("Remote access unavailable — Retry", "远程访问不可用 — 重试")
                elif not value["ready"]:
                    self.state = self.tr("Remote access ready; HappyRanch offline/restarting",
                                         "远程访问已就绪；HappyRanch 已离线或正在重启")
                else:
                    self.state = self.tr("Ready to invite", "可邀请设备")
                self.render()
        except Exception as error:
            self.failed(method, error)

    def failed(self, method: str, error: Exception | None = None) -> None:
        self.busy = False
        if method in self.mutations:
            # No retry queue, timer or optimistic local approval, including on
            # malformed replies, prompt denial, timeout or transport loss.
            self.notice = self.tr("Action cancelled — no change", "操作已取消 — 未作更改")
            self.render()
        else:
            self.unavailable(error)

    def render(self) -> None:
        Gtk, tr = self.Gtk, self.tr
        for child in self.box.get_children():
            self.box.remove(child)

        def text(parent: object, value: str) -> None:
            label = Gtk.Label(label=value)
            label.set_line_wrap(True)
            label.set_xalign(0)
            label.set_selectable(True)
            parent.pack_start(label, False, False, 0)

        def button(parent: object, en: str, zh: str, callback: Callable,
                   available: bool = True) -> None:
            widget = Gtk.Button(label=tr(en, zh))
            widget.set_sensitive(available and not self.busy)
            widget.connect("clicked", lambda _button: callback())
            parent.pack_start(widget, False, False, 0)

        def device(parent: object, value: dict) -> None:
            text(parent, tr("Device: ", "设备：") + self.plain(value["label"]))
            text(parent, tr("Stable-ID fingerprint: ", "稳定 ID 指纹：") + value["fingerprint"])
            text(parent, tr("Comparison phrase: ", "比对短语：") + self.plain(value["comparison_phrase"]))

        text(self.box, tr("Remote access", "远程访问"))
        text(self.box, self.state)
        if self.notice:
            text(self.box, self.notice)
        if self.snapshot is not None:
            pending = self.snapshot["pending"] is not None
            text(self.box, tr("Pending device", "待批准设备") if pending else
                 tr("No pending device", "没有待批准设备"))
            count = len(self.snapshot["devices"])
            text(self.box, tr(f"Devices with access ({count})", f"有访问权限的设备（{count}）"))
        button(self.box, "Back to status" if self.expanded else "Settings and devices",
               "返回状态" if self.expanded else "设置与设备", self.toggle)
        if self.expanded:
            details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            text(details, self.target)
            text(details, tr("Home-network access", "家庭网络访问"))
            button(details, "Refresh", "刷新", self.refresh)
            if self.snapshot is not None:
                pending = self.snapshot["pending"]
                if pending is not None:
                    text(details, tr("Pending device", "待批准设备"))
                    device(details, pending)
                    button(details, "Approve device", "批准设备", lambda: self.request("ApprovePending"))
                    button(details, "Reject device", "拒绝设备", lambda: self.request("RejectPending"))
                text(details, tr("Approved devices remain approved until revoked.",
                                 "已批准的设备将保留访问权限，直到被撤销。"))
                for item in self.snapshot["devices"]:
                    device(details, item)
                button(details, "Next device", "下一台设备", lambda: self.request("NextDevice"),
                       bool(self.snapshot["devices"]))
                if self.snapshot["selected"] is not None:
                    text(details, tr("Selected device", "已选设备"))
                    device(details, self.snapshot["selected"])
                    button(details, "Revoke device", "撤销设备", lambda: self.request("RevokeSelected"))
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.set_min_content_height(180)
            scroll.set_max_content_height(320)
            scroll.set_propagate_natural_height(True)
            scroll.add(details)
            self.box.pack_start(scroll, False, False, 0)
        self.box.show_all()

    def toggle(self) -> None:
        self.expanded = not self.expanded
        if self.expanded:
            self.refresh()
        else:
            self.render()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.cancellable.cancel()
        if self.connection is not None:
            self.connection.close(None, None, None)


def run_gui(prefix: Path, home: Path) -> int:
    # Lazy imports keep lifecycle functions runnable without GI or a display.
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("WebKit2", "4.1")
    from gi.repository import Gio, GLib, Gtk, WebKit2

    chinese = os.environ.get("LANG", "").lower().startswith("zh")

    def tr(en: str, zh: str) -> str:
        return zh if chinese else en

    GLib.set_prgname("happyranch-app")
    app_id = "org.happyranch.Desktop.h" + hashlib.sha256(os.fsencode(home)).hexdigest()[:24]
    app = Gtk.Application(application_id=app_id, flags=Gio.ApplicationFlags.FLAGS_NONE)
    window = None
    lock = None
    cancel = threading.Event()
    worker = None
    closing = False
    log_path = home / "logs/desktop-daemon.log"
    service = ServiceMode(home)
    requests = queue.Queue()
    controls = None
    remote = None
    connection_windows = []

    def open_connection(_button: object) -> None:
        # Keep the installed module local to this native control. The daemon's
        # WebView below receives no content manager, user scripts or forwarding.
        import importlib.util
        path = prefix / "share/happyranch/desktop/connection_window.py"
        spec = importlib.util.spec_from_file_location("connection_window", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        connection_windows.append(module.ConnectionWindow(app, window, prefix, tr))

    def attach_remote(box: object) -> None:
        nonlocal remote
        if remote is None:
            remote = RemoteAccessPanel(tr)
        parent = remote.box.get_parent()
        if parent is not None:
            parent.remove(remote.box)
        box.pack_start(remote.box, False, False, 0)

    def message(title: str, detail: str = "") -> None:
        for widget in window.get_children():
            window.remove(widget)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_border_width(32)
        heading = Gtk.Label(label=title)
        heading.set_line_wrap(True)
        body = Gtk.Label(label=detail)
        body.set_line_wrap(True)
        body.set_selectable(True)
        box.pack_start(heading, False, False, 0)
        box.pack_start(body, False, False, 0)
        if not closing:
            attach_remote(box)
        window.add(box)
        window.show_all()

    def show_error(detail: str | LaunchError) -> bool:
        diagnostic = str(detail)
        if diagnostic == "Could not reload the user manager.":
            diagnostic = tr("Could not reload the user manager. Retry to start HappyRanch again.",
                            "无法重新加载用户服务管理器。请重试以启动 HappyRanch。")
        elif diagnostic == "Release daemon ownership or health check failed; no web view was attached.":
            diagnostic = tr("Timed out waiting to connect to HappyRanch. Retry to start again.",
                            "连接 HappyRanch 超时。请重试以再次启动。")
        message(tr("HappyRanch could not run", "HappyRanch 无法运行"),
                diagnostic + "\n\n" + tr("Fallback daemon log: ", "回退守护进程日志：") + str(log_path))
        action = getattr(detail, "action", None)
        if action is not None and not closing:
            label = {"remove": tr("Remove old unit", "移除旧服务单元"),
                     "start": tr("Start HappyRanch", "启动 HappyRanch"),
                     "retry": tr("Retry", "重试")}[action]
            button = Gtk.Button(label=label)
            button.connect("clicked", lambda _button: begin_action(action))
            window.get_children()[0].pack_start(button, False, False, 0)
            window.show_all()
        return False

    def render_controls(state: str, detail: str, update: bool, available: bool = True) -> bool:
        if closing or controls is None:
            return False
        for widget in controls.get_children():
            controls.remove(widget)
        setting_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        label = Gtk.Label(label=tr("Keep HappyRanch running when I'm logged out",
                                   "退出登录后保持 HappyRanch 运行"))
        setting_row.pack_start(label, False, False, 0)
        switch = Gtk.Switch()
        switch.set_valign(Gtk.Align.CENTER)
        switch.set_active(state == "on")
        switch.set_sensitive(available)
        def toggled(_switch: object, active: bool) -> bool:
            switch.set_sensitive(False)
            requests.put("on" if active else "off")
            return True  # Only fresh observations update the displayed switch.
        switch.connect("state-set", toggled)
        setting_row.pack_start(switch, False, False, 0)
        controls.pack_start(setting_row, False, False, 0)
        if state == "partial":
            partial_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            partial_row.pack_start(Gtk.Label(label=tr("Partly on - click to repair/turn off",
                                                      "部分开启 - 点击修复或关闭")), False, False, 0)
            for text, zh, action in (("Repair", "修复", "on"), ("Turn off", "关闭", "off")):
                button = Gtk.Button(label=tr(text, zh))
                button.connect("clicked", lambda _button, action=action: requests.put(action))
                partial_row.pack_start(button, False, False, 0)
            controls.pack_start(partial_row, False, False, 0)
        if detail:
            diagnostic = Gtk.Label(label=detail)
            diagnostic.set_line_wrap(True)
            diagnostic.set_xalign(0)
            controls.pack_start(diagnostic, False, False, 0)
        if update:
            update_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            update_row.pack_start(Gtk.Label(label=tr("Update installed - restart HappyRanch",
                                                     "更新已安装 - 请重启 HappyRanch")), False, False, 0)
            button = Gtk.Button(label=tr("Restart HappyRanch", "重启 HappyRanch"))
            button.connect("clicked", lambda _button: requests.put("restart"))
            update_row.pack_start(button, False, False, 0)
            controls.pack_start(update_row, False, False, 0)
        controls.show_all()
        return False

    def refresh_controls(detail: str = "") -> None:
        try:
            state = service.lifetime_state()
            GLib.idle_add(render_controls, state, detail, service.update_installed(prefix))
        except (OSError, LaunchError) as exc:
            GLib.idle_add(render_controls, "off", str(exc), False, False)

    def show_web(url: str, fallback: bool = False) -> bool:
        nonlocal controls
        if closing:
            return False
        for widget in window.get_children():
            window.remove(widget)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        controls.set_margin_start(12)
        controls.set_margin_end(12)
        controls.set_margin_top(12)
        controls.set_margin_bottom(12)
        box.pack_start(controls, False, False, 0)
        attach_remote(box)
        box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)
        view = WebKit2.WebView()
        def load_failed(*_args: object) -> bool:
            show_error(tr("The web interface could not load.", "无法加载网页界面。"))
            return True
        view.connect("load-failed", load_failed)
        box.pack_start(view, True, True, 0)
        window.add(box)
        view.load_uri(url)
        window.show_all()
        if fallback:
            render_controls("off", tr("Unavailable: no systemd user manager.",
                                       "不可用：没有 systemd 用户服务管理器。"), False, False)
        else:
            requests.put("refresh")
        return False

    def finish(error: str | LaunchError | None) -> bool:
        nonlocal worker
        worker = None
        if error is not None:
            # Keep the window/lock until the user can read the failure.
            show_error(error)
        elif closing:
            app.quit()
        return False

    def supervise(action: str | None = None) -> None:
        child = None
        error = None
        try:
            if action == "remove":
                service.remove_legacy()
                error = LaunchError("The old unit was removed and the user manager reloaded. Start HappyRanch when ready.", "start")
                return
            url = service.start(cancel, retry=action == "retry")
            if url is not None:
                GLib.idle_add(show_web, url)
                detail = ""
                while not cancel.is_set():
                    try:
                        operation = requests.get(timeout=2)
                    except queue.Empty:
                        operation = "refresh"
                    if cancel.is_set():
                        break
                    if operation != "refresh":
                        detail = ""
                    try:
                        if operation in {"on", "off"}:
                            service.set_keep_running(operation == "on")
                        elif operation == "restart":
                            GLib.idle_add(message, tr("Restarting HappyRanch…", "正在重启 HappyRanch…"))
                            url = service.restart(cancel)
                            GLib.idle_add(show_web, url)
                    except (OSError, LaunchError) as exc:
                        if operation == "restart":
                            raise
                        detail = str(exc)
                    refresh_controls(detail)
                return
            GLib.idle_add(message, tr("Starting HappyRanch without a user manager…",
                                     "用户服务管理器不可用，正在启动 HappyRanch…"))
            prepare_home(home)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("ab", buffering=0) as log:
                child = subprocess.Popen(
                    [str(prefix / "bin/happyranch-daemon"),
                     "--reader-guarded-start=online-team-move@3"],
                    cwd=home, env=child_environment(prefix, home),
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log)
                url = wait_for_health(child, home, cancel)
                GLib.idle_add(show_web, url, True)
                while not cancel.wait(0.2):
                    if child.poll() is not None:
                        raise LaunchError(f"Daemon exited (status {child.returncode}).")
        except (OSError, LaunchError) as exc:
            if not cancel.is_set():
                error = exc if isinstance(exc, LaunchError) else str(exc)
        finally:
            while service.shutdown_pending:
                try:
                    if closing:
                        service.close_window()
                    else:
                        service.close()
                    if closing:
                        error = None
                except LaunchError as exc:
                    error = exc
                    cancel.clear()
                    GLib.idle_add(show_error, exc)
                    cancel.wait()
            while child is not None:
                try:
                    stop_child(child, home, markers_absent_at_spawn=True)
                    if closing:
                        error = None
                    break
                except (OSError, LaunchError) as exc:
                    error = str(exc)
                    if child.poll() is not None:
                        break
                    # A failed signal/wait is not an exit. Keep ownership and
                    # the lock; another close/quit requests a bounded retry.
                    cancel.clear()
                    GLib.idle_add(show_error, error)
                    cancel.wait()
            GLib.idle_add(finish, error)

    def begin_action(action: str | None = None) -> None:
        nonlocal worker
        if worker is not None or closing:
            return
        cancel.clear()
        message(tr("Starting HappyRanch…", "正在启动 HappyRanch…"))
        worker = threading.Thread(target=supervise, args=(action,), name="desktop-daemon")
        worker.start()

    def request_close(*_args: object) -> bool:
        nonlocal closing
        closing = True
        if remote is not None:
            remote.close()
        cancel.set()
        if worker is None:
            app.quit()
        else:
            message(tr("Stopping HappyRanch…", "正在停止 HappyRanch…"))
        return True

    def activate(_app: object) -> None:
        nonlocal window, lock, worker
        if window is not None:
            window.present()
            return
        window = Gtk.ApplicationWindow(application=app, title="HappyRanch")
        header = Gtk.HeaderBar(title="HappyRanch", show_close_button=True)
        connection_button = Gtk.Button(label=tr("Connection", "连接"))
        connection_button.connect("clicked", open_connection)
        header.pack_end(connection_button)
        window.set_titlebar(header)
        window.set_default_size(1100, 760)
        icon = prefix / "share/icons/hicolor/128x128/apps/happyranch.png"
        if icon.is_file():
            window.set_icon_from_file(str(icon))
        window.connect("delete-event", request_close)
        message(tr("Starting HappyRanch…", "正在启动 HappyRanch…"))
        try:
            lock = acquire_lock(home)
        except (OSError, LaunchError) as exc:
            show_error(str(exc))
            return
        if lock is None:
            # Without a session bus, flock still prevents a second daemon.
            app.quit()
            return
        begin_action()

    quit_action = Gio.SimpleAction.new("quit", None)
    quit_action.connect("activate", request_close)
    app.add_action(quit_action)
    app.set_accels_for_action("app.quit", ["<Primary>q"])
    app.connect("activate", activate)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, request_close)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, request_close)
    try:
        return app.run([])
    finally:
        for connection_window in connection_windows:
            connection_window.close()
        if remote is not None:
            remote.close()
        cancel.set()
        if worker is not None:
            worker.join(8)
        if lock is not None:
            lock.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="HappyRanch Linux desktop window")
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--daemon-home", type=Path,
                        help="Explicit isolated desktop runtime home")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--print-port", action="store_true", help="Print the verified running release daemon port")
    modes.add_argument("--run-service", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--user-home", type=Path, default=Path.home(), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.run_service:
        # systemd supplies its account home via %h; exec preserves MainPID.
        env = dict(os.environ, HAPPYRANCH_DAEMON_HOME=str(desktop_home(args.user_home)),
                   HAPPYRANCH_DAEMON_PORT="0", HAPPYRANCH_DAEMON_BIND_HOST="127.0.0.1")
        os.execve(args.prefix / "bin/happyranch-daemon", ["happyranch-daemon"], env)
    try:
        home = args.daemon_home or desktop_home()
        service = ServiceMode(home)
        if args.daemon_home is None and service.manager_available():
            # Select the manager's XDG home before acquiring desktop.lock.
            try:
                home = service.manager_home()
            except LaunchError:
                if args.print_port:
                    raise
                # The GUI's existing startup path presents the diagnostic.
        home = home.expanduser().resolve()
        if args.print_port:
            service.home = home
            print(service.current_port())
            return 0
        return run_gui(args.prefix.resolve(), home)
    except (OSError, LaunchError) as exc:
        print(f"happyranch-app: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
