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
import signal
import subprocess
import threading
import time
from typing import IO
import urllib.error
import urllib.request


class LaunchError(Exception):
    """A diagnostic safe to show inside the desktop window."""


def desktop_home() -> Path:
    """Use an app-owned data home, separate from the CLI's ~/.happyranch."""
    data = os.environ.get("XDG_DATA_HOME", "")
    root = Path(data) if data and Path(data).is_absolute() else Path.home() / ".local/share"
    return root / "happyranch"


def acquire_lock(home: Path) -> IO[bytes] | None:
    """Keep the inode in place: unlinking a held flock creates a launch race."""
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
               kill_timeout: float = 2) -> None:
    """Signal only the Popen child; bounded TERM/KILL waits also reap it."""
    owned_port = read_owned_port(home, child.pid)
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
    # After a confirmed exit, clear only markers still naming this child.
    # A normal daemon may already have removed both files itself.
    pid_file, port_file = home / "daemon.pid", home / "daemon.port"
    try:
        owner = pid_file.read_text().strip()
    except FileNotFoundError:
        owner = None
    if owner == str(child.pid):
        port_file.unlink(missing_ok=True)
        pid_file.unlink(missing_ok=True)
    elif owner is None and owned_port is not None:
        try:
            if port_file.read_text().strip() == str(owned_port):
                port_file.unlink()
        except FileNotFoundError:
            pass


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

    def show_error(detail: str) -> bool:
        message(tr("HappyRanch could not run", "HappyRanch 无法运行"),
                detail + "\n\n" + tr("Daemon log: ", "守护进程日志：") + str(log_path))
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

    def finish(error: str | None) -> bool:
        nonlocal worker
        worker = None
        if error is not None:
            # Keep the window/lock until the user can read the failure.
            show_error(error)
        elif closing:
            app.quit()
        return False

    def supervise() -> None:
        child = None
        error = None
        try:
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
                error = str(exc)
        finally:
            while child is not None:
                try:
                    stop_child(child, home)
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
        except OSError as exc:
            show_error(str(exc))
            return
        if lock is None:
            # Without a session bus, flock still prevents a second daemon.
            app.quit()
            return
        worker = threading.Thread(target=supervise, name="desktop-daemon")
        worker.start()

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
