#!/usr/bin/python3
"""System-Python GTK shell. No HappyRanch imports or bundled Python dependencies."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
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


def desktop_home() -> Path:
    """The packaged unit and window share only the release home."""
    return Path.home() / ".happyranch-release"


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
            return self.runner(list(args), capture_output=True, text=True, timeout=10)
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

    def validate_environment(self) -> None:
        result = self.command("systemctl", "--user", "show-environment")
        if result.returncode:
            raise LaunchError("Could not inspect the user manager environment.")
        polluted = sorted({line.partition("=")[0] for line in result.stdout.splitlines()
                           if line.startswith("HAPPYRANCH_")})
        if polluted:
            raise LaunchError("Remove these variables from the user manager environment: " + ", ".join(polluted))
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
                              "-p", "ActiveState", "-p", "Result")
        if result.returncode:
            raise LaunchError("Could not inspect the HappyRanch user service.")
        state = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if state.get("FragmentPath") != self.fragment or state.get("DropInPaths") != "":
            raise LaunchError("HappyRanch requires the packaged user unit with no drop-ins. Review your user service overrides.")
        return state

    def pid(self, state: dict[str, str]) -> int:
        raw = state.get("MainPID", "")
        if not raw.isascii() or not raw.isdecimal():
            raise LaunchError("The user service did not report a valid MainPID.")
        return int(raw)

    def read_marker(self, name: str) -> int | None:
        try:
            raw = (self.home / name).read_text(encoding="utf-8").strip()
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

    def refuse_external(self, pid: int) -> None:
        owner = self.read_marker("daemon.pid")
        self.read_marker("daemon.port")
        if owner is not None and owner != pid and (self.proc / str(owner)).exists():
            raise LaunchError("A CLI-started daemon is running in the release home. Stop it yourself before opening HappyRanch.")

    def listeners(self) -> set[str]:
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
                address, port = fields[1].split(":")
                if port.upper() == "223E" and fields[3] == "0A" and address.upper() in {
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
        if self.home != self.user_home / ".happyranch-release":
            raise LaunchError("The packaged service requires ~/.happyranch-release; remove --daemon-home.")
        self.check_legacy()
        self.validate_environment()
        state = self.state()
        pid = self.pid(state)
        self.refuse_external(pid)
        listeners = self.listeners()
        if listeners and not self.owns_listener(pid, listeners):
            raise LaunchError("Port 8766 is used by another program")
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
        deadline = time.monotonic() + timeout
        while True:
            state = self.state()
            self.crash_error(state)
            pid = self.pid(state)
            # After an admitted start, write_text can expose missing, empty or
            # partial markers. The tolerant read below waits for the complete
            # PID/port pair within this deadline; pre-start validation is strict.
            listeners = self.listeners()
            owns = self.owns_listener(pid, listeners)
            if listeners and not owns:
                raise LaunchError("Port 8766 is used by another program")
            if owns and read_owned_port(self.home, pid) == 8766:
                if self.health(8766, 0.5):
                    # Re-observe after HTTP before releasing the URL to GTK.
                    if (self.state() == state and read_owned_port(self.home, pid) == 8766
                            and self.owns_listener(pid, self.listeners())):
                        self.attached = True
                        return "http://127.0.0.1:8766/"
            if cancel.is_set():
                # The supervisor owns shutdown, including cancellation before
                # attachment. A failed stop must retain its worker and lock.
                raise LaunchError("Startup cancelled.")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LaunchError("Release daemon ownership or health check failed; no web view was attached.")
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


def stop_child(child: subprocess.Popen, home: Path, term_timeout: float = 5,
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
        window.add(box)
        window.show_all()

    def show_error(detail: str | LaunchError) -> bool:
        message(tr("HappyRanch could not run", "HappyRanch 无法运行"),
                str(detail) + "\n\n" + tr("Fallback daemon log: ", "回退守护进程日志：") + str(log_path))
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

    def show_web(url: str) -> bool:
        if closing:
            return False
        for widget in window.get_children():
            window.remove(widget)
        view = WebKit2.WebView()
        def load_failed(*_args: object) -> bool:
            show_error(tr("The web interface could not load.", "无法加载网页界面。"))
            return True
        view.connect("load-failed", load_failed)
        window.add(view)
        view.load_uri(url)
        window.show_all()
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
                cancel.wait()
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
                GLib.idle_add(show_web, url)
                while not cancel.wait(0.2):
                    if child.poll() is not None:
                        raise LaunchError(f"Daemon exited (status {child.returncode}).")
        except (OSError, LaunchError) as exc:
            if not cancel.is_set():
                error = exc if isinstance(exc, LaunchError) else str(exc)
        finally:
            while service.shutdown_pending:
                try:
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
        cancel.set()
        if worker is not None:
            worker.join(8)
        if lock is not None:
            lock.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="HappyRanch Linux desktop window")
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--daemon-home", type=Path, default=desktop_home(),
                        help="Explicit isolated desktop runtime home")
    args = parser.parse_args()
    return run_gui(args.prefix.resolve(), args.daemon_home.expanduser().resolve())


if __name__ == "__main__":
    raise SystemExit(main())
