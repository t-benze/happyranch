"""Run directly with system Python; no display, GI, or HappyRanch imports."""
from __future__ import annotations

import contextlib
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import tempfile
import threading
from types import ModuleType, SimpleNamespace
from typing import Callable, Iterator
import unittest
from unittest.mock import patch

import happyranch_window as window


class LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.home = Path(self.scratch.name)

    def spawn(self, body: str = "", ignore_term: bool = False) -> subprocess.Popen:
        script = "import os, pathlib, signal, time\n"
        if ignore_term:
            script += "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        script += body + "\nprint('ready', flush=True)\ntime.sleep(30)\n"
        child = subprocess.Popen([sys.executable, "-u", "-c", script], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.reap, child)
        self.assertEqual(child.stdout.readline().strip(), "ready")
        return child

    @staticmethod
    def reap(child: subprocess.Popen) -> None:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=2)

    def publish(self, pid: int, port: str = "54321") -> None:
        (self.home / "daemon.pid").write_text(str(pid))
        (self.home / "daemon.port").write_text(port)

    def test_home_uses_absolute_xdg_and_ignores_daemon_override(self) -> None:
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(self.home),
                                     "HAPPYRANCH_DAEMON_HOME": "/external"}):
            self.assertEqual(window.desktop_home(), self.home / "happyranch")
        for value in ("", "relative"):
            with self.subTest(value=value), patch.dict(os.environ, {"XDG_DATA_HOME": value}):
                self.assertEqual(window.desktop_home(), Path.home() / ".local/share/happyranch")
        with patch.object(Path, "home", return_value=self.home):
            with self.assertRaisesRegex(window.LaunchError, "development daemon home"):
                window.acquire_lock(self.home / ".happyranch")
            self.assertFalse((self.home / ".happyranch").exists())

    def test_service_entry_resolves_home_and_execs_with_same_pid(self) -> None:
        prefix = self.home / "payload"
        (prefix / "bin").mkdir(parents=True)
        daemon = prefix / "bin/happyranch-daemon"
        daemon.write_text("#!/usr/bin/python3\nimport json, os\n"
                          "print(json.dumps([os.getpid(), {k: os.environ[k] for k in "
                          "('HAPPYRANCH_DAEMON_HOME', 'HAPPYRANCH_DAEMON_PORT', "
                          "'HAPPYRANCH_DAEMON_BIND_HOST')}]))\n")
        daemon.chmod(0o755)
        for value in (None, "", "relative", str(self.home / "data space")):
            with self.subTest(xdg=value):
                env = dict(os.environ, HOME=str(self.home / "untrusted-home"),
                           HAPPYRANCH_DAEMON_HOME="/ignored", HAPPYRANCH_DAEMON_PORT="8765")
                env.pop("XDG_DATA_HOME", None)
                if value is not None:
                    env["XDG_DATA_HOME"] = value
                child = subprocess.Popen([sys.executable, "-I", window.__file__, "--prefix", str(prefix),
                                          "--run-service", "--user-home", str(self.home)],
                                         env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                stdout, stderr = child.communicate(timeout=3)
                self.assertEqual(child.returncode, 0, stderr)
                pid, actual = json.loads(stdout)
                self.assertEqual(pid, child.pid, "launcher must exec to preserve unit MainPID")
                expected = Path(value) if value and value.startswith("/") else self.home / ".local/share"
                self.assertEqual(actual, {"HAPPYRANCH_DAEMON_HOME": str(expected / "happyranch"),
                                          "HAPPYRANCH_DAEMON_PORT": "0",
                                          "HAPPYRANCH_DAEMON_BIND_HOST": "127.0.0.1"})

    def test_lock_excludes_second_process_and_reopens_same_inode(self) -> None:
        lock = window.acquire_lock(self.home)
        self.addCleanup(lock.close)
        inode = (self.home / "desktop.lock").stat().st_ino
        script = ("import pathlib, sys; import happyranch_window as w; "
                  "lock=w.acquire_lock(pathlib.Path(sys.argv[1])); "
                  "print('held' if lock is None else 'acquired')")
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).parent)}
        result = subprocess.run([sys.executable, "-c", script, str(self.home)],
                                env=env, capture_output=True, text=True, timeout=3, check=True)
        self.assertEqual(result.stdout.strip(), "held")
        lock.close()
        second = window.acquire_lock(self.home)
        self.assertIsNotNone(second)
        second.close()
        self.assertEqual((self.home / "desktop.lock").stat().st_ino, inode)

    def test_existing_state_refuses_without_touching_external_child(self) -> None:
        child = self.spawn()
        for name in ("daemon.pid", "daemon.port"):
            with self.subTest(name=name):
                path = self.home / name
                path.write_text(str(child.pid))
                try:
                    with self.assertRaises(window.LaunchError):
                        window.prepare_home(self.home)
                    self.assertEqual(path.read_text(), str(child.pid))
                    self.assertIsNone(child.poll())
                finally:
                    path.unlink()
        (self.home / "daemon.pid").symlink_to(self.home / "missing")
        with self.assertRaises(window.LaunchError):
            window.prepare_home(self.home)
        self.assertTrue((self.home / "daemon.pid").is_symlink())

    def test_child_environment_sets_private_home_and_ephemeral_loopback(self) -> None:
        with patch.dict(os.environ, {"HAPPYRANCH_DAEMON_HOME": "/external",
                                     "HAPPYRANCH_DAEMON_PORT": "8765",
                                     "HAPPYRANCH_DEBUG": "true"}):
            env = window.child_environment(Path("/payload"), self.home)
        self.assertEqual(env["HAPPYRANCH_DAEMON_HOME"], str(self.home))
        self.assertEqual(env["HAPPYRANCH_DAEMON_PORT"], "0")
        self.assertEqual(env["HAPPYRANCH_DAEMON_BIND_HOST"], "127.0.0.1")
        self.assertEqual(env["HAPPYRANCH_WEB_DIST"], "/payload/web/dist")
        self.assertNotIn("HAPPYRANCH_DEBUG", env)

    def test_port_requires_owned_pid_and_valid_number(self) -> None:
        self.assertIsNone(window.read_owned_port(self.home, 123))
        for raw, expected in ((" 54321\n", 54321), ("1", 1), ("65535", 65535),
                              ("0", None), ("65536", None), ("bad", None),
                              ("", None), ("-1", None), ("１２", None)):
            with self.subTest(raw=raw):
                self.publish(123, raw)
                self.assertEqual(window.read_owned_port(self.home, 123), expected)
        self.publish(124)
        self.assertIsNone(window.read_owned_port(self.home, 123))

    @contextlib.contextmanager
    def server(self, body: bytes = b'{"status":"ok"}',
               status: int = 200) -> Iterator[tuple[int, list[str]]]:
        paths = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                paths.append(self.path)
                self.send_response(status)
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *_args: object) -> None:
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        try:
            yield server.server_port, paths
        finally:
            server.shutdown()
            thread.join(2)
            server.server_close()

    def test_health_requires_http_ok_and_status_ok_without_proxy(self) -> None:
        for body, status, expected in ((b'{"status":"ok"}', 200, True),
                                       (b'{"status":"starting"}', 200, False),
                                       (b'[]', 200, False), (b'bad', 200, False),
                                       (b'{"status":"ok"}', 503, False)):
            with self.subTest(body=body, status=status), self.server(body, status) as (port, paths):
                with patch.dict(os.environ, {"http_proxy": "http://127.0.0.1:1", "no_proxy": ""}):
                    self.assertEqual(window.health_ok(port, 0.5), expected)
                self.assertEqual(paths, ["/api/v1/health"])

    def test_wait_discovers_late_port_and_health(self) -> None:
        child = self.spawn()
        with self.server() as (port, _paths):
            timer = threading.Timer(0.03, self.publish, args=(child.pid, str(port)))
            timer.start()
            try:
                self.assertEqual(window.wait_for_health(child, self.home, threading.Event(), 1),
                                 f"http://127.0.0.1:{port}/")
            finally:
                timer.join(1)

    def test_wait_rejects_timeout_exit_and_cancel(self) -> None:
        child = self.spawn()
        with self.assertRaisesRegex(window.LaunchError, "timed out"):
            window.wait_for_health(child, self.home, threading.Event(), 0.03)
        cancel = threading.Event()
        cancel.set()
        with self.assertRaisesRegex(window.LaunchError, "cancelled"):
            window.wait_for_health(child, self.home, cancel, 1)
        child.terminate()
        child.wait(timeout=2)
        with self.assertRaisesRegex(window.LaunchError, "exited"):
            window.wait_for_health(child, self.home, threading.Event(), 1)

    def test_wait_never_loads_foreign_healthy_daemon(self) -> None:
        child = self.spawn()
        external = self.spawn()
        with self.server() as (port, paths):
            self.publish(external.pid, str(port))
            with self.assertRaisesRegex(window.LaunchError, "timed out"):
                window.wait_for_health(child, self.home, threading.Event(), 0.03)
            self.assertEqual(paths, [])
        self.assertIsNone(external.poll())

    @staticmethod
    def marker_snapshot(home: Path) -> dict[str, tuple[int, int, bytes]]:
        result = {}
        for name in ("daemon.port", "daemon.pid"):
            path = home / name
            if path.exists():
                info = path.stat()
                result[name] = (info.st_dev, info.st_ino, path.read_bytes())
        return result

    @staticmethod
    def publication_body(home: Path, port: str, pid: str, external_pid: int) -> str:
        # Actual child writes and a ready pipe establish the filesystem boundary.
        return (f"h = pathlib.Path({str(home)!r})\n"
                f"port = {port!r}\npid = {pid!r}\n"
                "if port != 'absent':\n"
                "    (h / 'daemon.port').write_text("
                "{'empty': '', 'partial': '54', 'complete': '54321'}[port])\n"
                "if pid != 'absent':\n"
                "    (h / 'daemon.pid').write_text("
                "{'empty': '', 'prefix-first': str(os.getpid())[:1], "
                "'prefix-last': str(os.getpid())[:-1], 'matching': str(os.getpid()), "
                f"'different': {str(external_pid)!r}}}[pid])")

    def check_publications(self, already_exited: bool) -> None:
        external = self.spawn()
        for port in ("absent", "empty", "partial", "complete"):
            for pid in ("absent", "empty", "prefix-first", "prefix-last", "matching", "different"):
                with self.subTest(port=port, pid=pid, already_exited=already_exited):
                    home = self.home / f"{port}-{pid}"
                    home.mkdir()
                    window.prepare_home(home)
                    child = self.spawn(self.publication_body(home, port, pid, external.pid))
                    before = self.marker_snapshot(home)
                    if already_exited:
                        # waitid observes real exit without reaping: stop_child owns the reap.
                        child.terminate()
                        os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)
                    window.stop_child(child, home, 0.5, 0.5, markers_absent_at_spawn=True)
                    self.assertEqual(child.returncode, -signal.SIGTERM)
                    with self.assertRaises(ChildProcessError):
                        os.waitpid(child.pid, os.WNOHANG)
                    self.assertIsNone(external.poll())
                    if pid == "different":
                        self.assertEqual(self.marker_snapshot(home), before)
                        with self.assertRaises(window.LaunchError):
                            window.prepare_home(home)
                    else:
                        self.assertEqual(self.marker_snapshot(home), {})
                        window.prepare_home(home)

    def test_stop_terms_and_reaps_only_owned_child_and_clears_markers(self) -> None:
        self.check_publications(already_exited=False)
        # Same-inode completion and first publication inside TERM remain owned.
        for publication in ("finish-port", "finish-pid", "publish-both"):
            with self.subTest(publication=publication):
                home = self.home / publication
                home.mkdir()
                window.prepare_home(home)
                initial = ("absent", "absent") if publication == "publish-both" else (
                    "empty", "empty" if publication == "finish-pid" else "absent")
                body = self.publication_body(home, *initial, 0)
                body += ("\ndef finish(*_args):\n"
                         "    (h / 'daemon.port').write_text('54321')\n"
                         "    (h / 'daemon.pid').write_text(str(os.getpid()))\n"
                         "    raise SystemExit(0)\n"
                         "signal.signal(signal.SIGTERM, finish)")
                child = self.spawn(body)
                window.stop_child(child, home, 0.5, 0.5, markers_absent_at_spawn=True)
                self.assertEqual(child.returncode, 0)
                with self.assertRaises(ChildProcessError):
                    os.waitpid(child.pid, os.WNOHANG)
                self.assertEqual(self.marker_snapshot(home), {})
                window.prepare_home(home)

    def test_stop_kills_term_resistant_child_then_reaps(self) -> None:
        window.prepare_home(self.home)
        child = self.spawn(ignore_term=True)
        self.publish(child.pid)
        window.stop_child(child, self.home, 0.03, 1, markers_absent_at_spawn=True)
        self.assertEqual(child.returncode, -signal.SIGKILL)
        with self.assertRaises(ChildProcessError):
            os.waitpid(child.pid, os.WNOHANG)
        self.assertFalse((self.home / "daemon.pid").exists())
        self.assertFalse((self.home / "daemon.port").exists())
        window.prepare_home(self.home)

    def test_stop_preserves_replaced_external_markers(self) -> None:
        external = self.spawn()
        # No launch fact: even empty or coincidentally matching contents preserve.
        for pid in ("absent", "empty", "prefix-first", "matching", "different"):
            with self.subTest(no_launch_fact=pid):
                home = self.home / f"unproven-{pid}"
                home.mkdir()
                (home / "daemon.port").write_text("54")
                # Port really predates launch; prepare_home refuses it.
                with self.assertRaises(window.LaunchError):
                    window.prepare_home(home)
                child = self.spawn(self.publication_body(home, "partial", pid, external.pid))
                before = self.marker_snapshot(home)
                window.stop_child(child, home, 0.5, 0.5)
                self.assertEqual(self.marker_snapshot(home), before)
                self.assertEqual(child.returncode, -signal.SIGTERM)
                with self.assertRaises(window.LaunchError):
                    window.prepare_home(home)
                self.assertIsNone(external.poll())

        for raw in ("0", "not-a-pid", "１２", " ", "123\n"):
            with self.subTest(non_prefix=raw):
                home = self.home / f"invalid-{raw.encode().hex()}"
                home.mkdir()
                window.prepare_home(home)
                child = self.spawn()
                (home / "daemon.pid").write_text(raw)
                (home / "daemon.port").write_text("54321")
                before = self.marker_snapshot(home)
                window.stop_child(child, home, 0.5, 0.5, markers_absent_at_spawn=True)
                self.assertEqual(self.marker_snapshot(home), before)
                with self.assertRaises(window.LaunchError):
                    window.prepare_home(home)
                self.assertIsNone(external.poll())

        for marker in ("daemon.port", "daemon.pid"):
            publications = (("complete", "absent"), ("complete", "empty"),
                            ("complete", "prefix-last"), ("complete", "matching"))
            if marker == "daemon.pid":
                # Replacing a PID requires a PID inode before TERM. With no
                # prior inode it is first publication (covered above).
                publications = (("absent", "empty"), *publications[1:])
            for port, pid in publications:
                with self.subTest(replaced=marker, port=port, pid=pid):
                    home = self.home / f"replace-{marker}-{port}-{pid}"
                    home.mkdir()
                    window.prepare_home(home)
                    body = self.publication_body(home, port, pid, external.pid)
                    body += ("\ndef replace(*_args):\n"
                             f"    (h / 'replacement').replace(h / {marker!r})\n"
                             "    raise SystemExit(0)\n"
                             "signal.signal(signal.SIGTERM, replace)")
                    child = self.spawn(body)
                    # Matching-looking replacement bytes still do not confer ownership.
                    replacement = home / "replacement"
                    replacement.write_text(str(child.pid) if marker == "daemon.pid" else "54321")
                    info = replacement.stat()
                    expected = self.marker_snapshot(home)
                    expected[marker] = (info.st_dev, info.st_ino, replacement.read_bytes())
                    window.stop_child(child, home, 0.5, 0.5, markers_absent_at_spawn=True)
                    self.assertEqual(child.returncode, 0)
                    self.assertEqual(self.marker_snapshot(home), expected)
                    with self.assertRaises(window.LaunchError):
                        window.prepare_home(home)
                    self.assertIsNone(external.poll())

    def test_stop_reaps_already_exited_child_and_removes_owned_stale_state(self) -> None:
        self.check_publications(already_exited=True)


class ServiceTests(unittest.TestCase):
    """All external service/proc/HTTP observations are synthetic."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.user_home = Path(scratch.name)
        self.home = self.user_home / ".local/share/happyranch"
        self.home.mkdir(parents=True)
        self.proc = self.user_home / "proc"
        (self.proc / "net").mkdir(parents=True)
        (self.proc / "42/fd").mkdir(parents=True)
        (self.proc / "42/fd/3").symlink_to("socket:[555]")
        self.publish()
        self.net()
        self.state = {"MainPID": "42", "FragmentPath": window.ServiceMode.fragment,
                      "DropInPaths": "", "ActiveState": "active", "Result": "success",
                      "UnitFileState": "disabled", "NeedDaemonReload": "no"}
        self.calls = []
        self.probes = []
        self.manager = (0, "running\n")
        self.environment = "PATH=/usr/bin\n"
        self.failed_command = None
        self.linger = "no"
        self.healthy = True
        self.service = window.ServiceMode(self.home, user_home=self.user_home,
                                          proc=self.proc, runner=self.run_command,
                                          health=self.health)

    def publish(self, pid: str = "42", port: str = "43127") -> None:
        (self.home / "daemon.pid").write_text(pid)
        (self.home / "daemon.port").write_text(port)

    def net(self, *, inode: str = "555", address: str = "0100007F",
            protocol: str = "tcp", state: str = "0A", port: str = "A877") -> None:
        (self.proc / "net" / protocol).write_text(
            "header\n" + f"0: {address}:{port} 00000000:0000 {state} 0:0 00:0 0 1000 0 {inode} 1\n")

    def run_command(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        self.calls.append(argv)
        self.assertEqual(kwargs, {"capture_output": True, "text": True, "timeout": 10})
        rc, output = 0, ""
        if argv[2] == self.failed_command:
            rc = 1
        elif argv[:3] == ["systemctl", "--user", "is-system-running"]:
            rc, output = self.manager
        elif argv[0] == "loginctl":
            output = self.linger + "\n"
        elif argv[0] == "busctl":
            self.assertEqual(argv, ["busctl", "--user", "get-property", "org.freedesktop.systemd1",
                                   "/org/freedesktop/systemd1/unit/happyranch_2ddaemon_2eservice",
                                   "org.freedesktop.systemd1.Service", "ExecMainStartTimestamp"])
            output = "t " + self.state.get("ExecMainStartTimestamp", "1") + "\n"
        elif argv[2] in {"enable", "disable"}:
            self.state["UnitFileState"] = "enabled" if argv[2] == "enable" else "disabled"
        elif argv[0] == "journalctl":
            output = "\n".join(f"synthetic journal {i}" for i in range(25))
        elif argv[2] == "show-environment":
            output = self.environment
        elif argv[2] == "show":
            output = "\n".join(f"{key}={value}" for key, value in self.state.items())
        elif argv[2] == "reset-failed":
            self.state.update(ActiveState="active", Result="success")
        return subprocess.CompletedProcess(argv, rc, output, "synthetic command error")

    def health(self, port: int, timeout: float) -> bool:
        self.probes.append((port, timeout))
        return self.healthy

    def start(self, *, retry: bool = False) -> str | None:
        return self.service.start(threading.Event(), timeout=0, retry=retry)

    def verbs(self) -> list[str]:
        return [call[2] for call in self.calls if call[0] == "systemctl"]

    def env_file(self, text: str) -> Path:
        path = self.user_home / ".config/happyranch/daemon.env"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_lifetime_state_is_derived_for_every_combination(self) -> None:
        for enabled, linger, expected in ((False, False, "off"), (True, True, "on"),
                                          (True, False, "partial"), (False, True, "partial")):
            with self.subTest(enabled=enabled, linger=linger):
                self.state["UnitFileState"] = "enabled" if enabled else "disabled"
                self.linger = "yes" if linger else "no"
                self.service.save_linger(not linger)  # Provenance must not set UI state.
                self.assertEqual(self.service.lifetime_state(), expected)
        self.failed_command = str(os.getuid())
        with self.assertRaisesRegex(window.LaunchError, "inspect login linger"):
            self.service.lifetime_state()

    def test_linger_dbus_uses_self_uid_interactive_and_plain_denial(self) -> None:
        calls = []
        class Bus:
            def call_sync(self, *args: object) -> None:
                calls.append(args)
        repository = ModuleType("gi.repository")
        class Error(Exception):
            pass
        repository.Gio = SimpleNamespace(BusType=SimpleNamespace(SYSTEM="system"),
            bus_get_sync=lambda kind, cancel: Bus(),
            DBusCallFlags=SimpleNamespace(ALLOW_INTERACTIVE_AUTHORIZATION="interactive"))
        repository.GLib = SimpleNamespace(Variant=lambda signature, value: (signature, value), Error=Error)
        with patch.dict(sys.modules, {"gi.repository": repository}):
            self.service.set_linger(True)
            self.service.set_linger(False)
            self.assertEqual([call[4] for call in calls],
                             [("(ubb)", (os.getuid(), True, True)), ("(ubb)", (os.getuid(), False, True))])
            self.assertTrue(all(call[:4] == ("org.freedesktop.login1", "/org/freedesktop/login1",
                "org.freedesktop.login1.Manager", "SetUserLinger") for call in calls))
            self.assertTrue(all(call[6:] == ("interactive", 10000, None) for call in calls))
            repository.Gio.bus_get_sync = lambda *_args: (_ for _ in ()).throw(Error("org.freedesktop.DBus.Error.AccessDenied"))
            with self.assertRaisesRegex(window.LaunchError, "local polkit rule denied"):
                self.service.set_keep_running(True)
            self.assertEqual(self.service.lifetime_state(), "off")
            self.assertNotIn("enable", self.verbs())
            self.assertFalse(self.service.desktop_config.exists())

    def test_enable_persists_atomic_provenance_and_disable_preserves_external_linger(self) -> None:
        for previously_on in (False, True):
            with self.subTest(previously_on=previously_on):
                self.linger = "yes" if previously_on else "no"
                self.service.desktop_config.unlink(missing_ok=True)
                events = []
                runner = self.service.runner
                def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
                    events.append(argv[2])
                    return runner(argv, **kwargs)
                def linger(enabled: bool) -> None:
                    events.append(("linger", enabled))
                    self.linger = "yes" if enabled else "no"
                with patch.object(self.service, "set_linger", side_effect=linger), \
                        patch.object(self.service, "runner", side_effect=run), \
                        patch.object(window.os, "replace", wraps=os.replace) as replace:
                    self.service.set_keep_running(True)
                    self.assertLess(events.index(("linger", True)), events.index("enable"))
                    self.assertEqual(self.service.app_set_linger(), not previously_on)
                    self.assertEqual(self.service.lifetime_state(), "on")
                    self.assertEqual(replace.call_count, 1)
                    self.assertEqual(Path(replace.call_args.args[0]).parent, self.service.desktop_config.parent)
                    self.assertEqual(replace.call_args.args[1], self.service.desktop_config)
                    # Repeated ON/repair must retain the app's original undo obligation.
                    self.service.set_keep_running(True)
                    self.assertEqual(self.service.app_set_linger(), not previously_on)
                    self.service.set_keep_running(False)
                self.assertFalse(self.service.app_set_linger())
                self.assertEqual(self.linger, "yes" if previously_on else "no")
                self.assertEqual(events.count(("linger", False)), 0 if previously_on else 1)
                self.assertNotIn("stop", events)
                self.assertEqual(list(self.service.desktop_config.parent.glob(".desktop-*")), [])

    def test_enable_failure_rolls_back_only_linger_changed_by_app(self) -> None:
        self.failed_command = "enable"
        for prior in ("no", "yes"):
            with self.subTest(prior=prior):
                self.linger = prior
                def linger(enabled: bool) -> None:
                    self.linger = "yes" if enabled else "no"
                with patch.object(self.service, "set_linger", side_effect=linger) as change:
                    with self.assertRaisesRegex(window.LaunchError, "enable"):
                        self.service.set_keep_running(True)
                    self.assertEqual(change.call_args_list, [unittest.mock.call(True)] +
                                     ([unittest.mock.call(False)] if prior == "no" else []))
                self.assertEqual(self.linger, prior)
                self.assertFalse(self.service.desktop_config.exists())

    def test_missing_malformed_config_never_disables_external_linger(self) -> None:
        self.service.desktop_config.parent.mkdir(parents=True)
        for raw in (None, b"{", b"\xff", b"[]", b'{"linger_set_by_app":"true"}', b'{"linger_set_by_app":1}'):
            with self.subTest(raw=raw):
                self.service.desktop_config.unlink(missing_ok=True)
                if raw is not None:
                    self.service.desktop_config.write_bytes(raw)
                self.linger = "yes"
                with patch.object(self.service, "set_linger") as change:
                    self.service.set_keep_running(False)
                change.assert_not_called()
                self.assertEqual(self.linger, "yes")
                self.assertNotIn("stop", self.verbs())

    def test_close_window_rechecks_lifetime_and_retains_failed_stop_custody(self) -> None:
        for enabled, linger in (("enabled", "yes"), ("disabled", "no"),
                                ("enabled", "no"), ("disabled", "yes")):
            with self.subTest(enabled=enabled, linger=linger):
                self.calls.clear()
                self.start()
                self.state["UnitFileState"], self.linger = enabled, linger
                self.service.close_window()
                self.assertEqual(self.verbs().count("stop"), 0 if (enabled, linger) == ("enabled", "yes") else 1)
                self.assertFalse(self.service.shutdown_pending)
        self.start()
        self.state["UnitFileState"], self.linger = "disabled", "no"
        self.failed_command = "stop"
        with self.assertRaisesRegex(window.LaunchError, "shutdown failed"):
            self.service.close_window()
        self.assertTrue(self.service.shutdown_pending)
        self.failed_command = None
        self.service.close_window()
        self.assertFalse(self.service.shutdown_pending)

    def test_update_signal_is_install_ctime_or_reload_and_fails_quiet(self) -> None:
        prefix = self.user_home / "payload"
        prefix.mkdir()
        info = prefix / "build-info.json"
        info.write_text('{}')
        installed = info.stat().st_ctime
        with patch.object(window.time, "time", return_value=installed + 100), \
                patch.object(window.time, "monotonic", return_value=200):
            for elapsed, reload, expected in ((101, "no", True), (99, "no", False), (99, "yes", True)):
                with self.subTest(elapsed=elapsed, reload=reload):
                    self.state.update(ExecMainStartTimestamp=str(int((installed+100-elapsed)*1_000_000)),
                                      ExecMainStartTimestampMonotonic=str((200-elapsed)*1_000_000),
                                      NeedDaemonReload=reload)
                    self.assertFalse(self.service.update_installed(prefix))  # Not owned yet.
                    self.start()
                    self.assertEqual(self.service.update_installed(prefix), expected)
                    self.service.close()
            self.start()
            self.state.update(ExecMainStartTimestamp=str(int((installed-60)*1_000_000)),
                              ExecMainStartTimestampMonotonic="100000000", NeedDaemonReload="no")
            # CLOCK_MONOTONIC pauses during suspend; recorded realtime does not move.
            for suspended in (0, 3600):
                with self.subTest(suspended=suspended), \
                        patch.object(window.time, "time", return_value=installed+60+suspended), \
                        patch.object(window.time, "monotonic", return_value=220):
                    self.assertTrue(self.service.update_installed(prefix))
            for raw in ("", "n/a", "0", "-1", "１２", "18446744073709551615", "9" * 4000):
                self.state["ExecMainStartTimestamp"] = raw
                with self.assertLogs(window.__name__, level="WARNING"):
                    self.assertFalse(self.service.update_installed(prefix))
            self.state["ExecMainStartTimestamp"] = "1000000"
            self.failed_command = "get-property"
            with self.assertLogs(window.__name__, level="WARNING"):
                self.assertFalse(self.service.update_installed(prefix))
            self.failed_command = None
            runner = self.service.runner
            for error in (subprocess.TimeoutExpired(["busctl"], 10), FileNotFoundError()):
                def unavailable(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
                    if argv[0] == "busctl":
                        raise error
                    return runner(argv, **kwargs)
                with patch.object(self.service, "runner", side_effect=unavailable):
                    with self.assertLogs(window.__name__, level="WARNING"):
                        self.assertFalse(self.service.update_installed(prefix))
            info.unlink()
            with self.assertLogs(window.__name__, level="WARNING"):
                self.assertFalse(self.service.update_installed(prefix))
            with patch.object(Path, "open", side_effect=PermissionError):
                with self.assertLogs(window.__name__, level="WARNING"):
                    self.assertFalse(self.service.update_installed(prefix))

    def test_restart_reloads_then_reproves_async_markers_and_custody(self) -> None:
        self.start()
        self.state["NeedDaemonReload"] = "yes"
        runner = self.service.runner
        polls = 0
        def restart(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            nonlocal polls
            result = runner(argv, **kwargs)
            if argv[2] == "restart" and result.returncode == 0:
                self.state["MainPID"] = "43"
                (self.proc / "43/fd").mkdir(parents=True)
                (self.proc / "43/fd/3").symlink_to("socket:[555]")
                (self.home / "daemon.pid").write_bytes(b"\xff")
                (self.home / "daemon.port").write_text("")
                self.assertFalse(self.service.attached)
            if argv[2] == "show" and self.state["MainPID"] == "43":
                polls += 1
                if polls == 2:
                    self.assertEqual(len(self.probes), 2)  # Only original + pre-restart proofs.
                    self.publish(pid="43")
            return result
        self.service.runner = restart
        self.assertEqual(self.service.restart(threading.Event(), timeout=1), "http://127.0.0.1:43127/")
        self.assertTrue(self.service.attached)
        self.assertTrue(self.service.shutdown_pending)
        self.assertGreaterEqual(polls, 3)  # Includes post-health state observation.
        self.assertLess(self.verbs().index("daemon-reload"), self.verbs().index("restart"))
        self.assertEqual(len(self.probes), 3)
        self.state["NeedDaemonReload"] = "no"
        self.failed_command = "restart"
        with self.assertRaisesRegex(window.LaunchError, "restart"):
            self.service.restart(threading.Event(), timeout=0)
        self.assertFalse(self.service.attached)
        self.assertTrue(self.service.shutdown_pending)
        self.failed_command = "stop"
        with self.assertRaisesRegex(window.LaunchError, "shutdown failed"):
            self.service.close_window()
        self.assertTrue(self.service.shutdown_pending)
        self.failed_command = None
        self.service.close_window()
        self.assertFalse(self.service.shutdown_pending)

        # A previously attached, fully-ON service still owes a successful stop
        # after any restart error, including errors before restart is invoked.
        self.service.runner = runner
        for verb in ("start", "daemon-reload", "restart"):
            for timed_out in (False, True):
                with self.subTest(verb=verb, timed_out=timed_out):
                    self.start()
                    self.state.update(UnitFileState="enabled", NeedDaemonReload="yes")
                    self.linger = "yes"
                    self.assertTrue(self.service.attached)
                    self.assertEqual(self.service.lifetime_state(), "on")
                    def fail(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
                        result = runner(argv, **kwargs)
                        if argv[2] == verb:
                            if timed_out:
                                raise subprocess.TimeoutExpired(argv, 10)
                            result.returncode = 1
                        return result
                    with patch.object(self.service, "runner", side_effect=fail):
                        with self.assertRaises(window.LaunchError):
                            self.service.restart(threading.Event(), timeout=0)
                    self.assertFalse(self.service.attached)
                    self.assertTrue(self.service.shutdown_pending)
                    stops = self.verbs().count("stop")
                    self.failed_command = "stop"
                    with self.assertRaisesRegex(window.LaunchError, "shutdown failed"):
                        self.service.close_window()
                    self.assertTrue(self.service.shutdown_pending)
                    self.assertEqual(self.verbs().count("stop"), stops+1)
                    self.failed_command = None
                    self.service.close_window()
                    self.assertFalse(self.service.shutdown_pending)
                    self.assertEqual(self.verbs().count("stop"), stops+2)

    def test_gui_switch_partial_actions_fallback_and_restart(self) -> None:
        for scenario in ("off", "on", "partial-repair", "partial-off", "denied", "fallback", "restart",
                         "reload-error", "reload-timeout", "restart-error", "restart-timeout",
                         "start-reload-error-en", "start-reload-error-zh",
                         "start-reload-timeout-en", "start-reload-timeout-zh",
                         "start-attach-timeout-en", "start-attach-timeout-zh"):
            with self.subTest(scenario=scenario):
                restart_error = scenario in {"reload-error", "reload-timeout", "restart-error", "restart-timeout"}
                startup_error = scenario.startswith("start-")
                chinese = scenario.endswith("-zh")
                callbacks = queue.Queue()
                widgets = []
                urls = []
                self.calls.clear()
                self.probes.clear()
                self.service.attached = self.service.shutdown_pending = False
                self.service.desktop_config.unlink(missing_ok=True)
                self.state.update(UnitFileState="enabled" if restart_error or scenario in {"on", "partial-repair", "partial-off"} else "disabled",
                                  NeedDaemonReload="yes" if startup_error or scenario == "restart" else "no",
                                  ExecMainStartTimestamp="1")
                self.linger = "yes" if restart_error or scenario == "on" else "no"
                self.manager = (1, "offline") if scenario == "fallback" else (0, "running")
                prefix = self.user_home / "payload"
                prefix.mkdir(exist_ok=True)
                (prefix / "build-info.json").write_text('{}')

                class Widget:
                    def __init__(self, **kwargs: object) -> None:
                        self.children, self.signals = [], {}
                        self.orientation = kwargs.get("orientation")
                        self.packing = {}
                        self.label = kwargs.get("label")
                        self.active, self.sensitive = False, True
                        widgets.append(self)
                    def connect(self, name: str, callback: object) -> None:
                        self.signals[name] = callback
                    def get_children(self) -> list:
                        return self.children[:]
                    def add(self, child: object) -> None:
                        self.children.append(child)
                    def pack_start(self, child: object, *args: object) -> None:
                        self.packing[child] = args
                        self.add(child)
                    def remove(self, child: object) -> None:
                        self.children.remove(child)
                    def set_active(self, active: bool) -> None:
                        self.active = active
                    def set_sensitive(self, sensitive: bool) -> None:
                        self.sensitive = sensitive
                    def load_uri(self, url: str) -> None:
                        urls.append(url)
                    def __getattr__(self, _name: str) -> object:
                        return lambda *_args: None

                class Switch(Widget):
                    pass
                shell, app = Widget(), Widget()
                app.quit_called = False
                def quit_app() -> None:
                    app.quit_called = True
                def pump(predicate: Callable[[], bool]) -> None:
                    for _ in range(80):
                        if predicate():
                            return
                        try:
                            callback, args = callbacks.get(timeout=0.1)
                        except queue.Empty:
                            continue
                        callback(*args)
                    self.fail("GUI did not reach expected state")
                def current() -> list:
                    root = shell.get_children()
                    def descendants(widget: Widget) -> list:
                        return [child for item in widget.children for child in [item, *descendants(item)]]
                    return descendants(root[0]) if root else []
                def switch() -> Switch | None:
                    return next((w for w in current() if isinstance(w, Switch)), None)
                def click(label: str) -> None:
                    button = next(w for w in current() if w.label == label)
                    button.signals["clicked"](button)
                def linger(enabled: bool) -> None:
                    if scenario == "denied":
                        raise window.LaunchError("A local polkit rule denied keeping HappyRanch running while logged out.")
                    self.linger = "yes" if enabled else "no"
                runner = self.service.runner
                fail_stop = restart_error
                fail_start = startup_error
                def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
                    result = runner(argv, **kwargs)
                    failure = "daemon-reload" if scenario.startswith(("reload-", "start-reload-")) else "restart"
                    if (restart_error or fail_start) and argv[2] == failure:
                        if "timeout" in scenario:
                            raise subprocess.TimeoutExpired(argv, 10)
                        result.returncode = 1
                    if argv[2] == "stop" and fail_stop:
                        result.returncode = 1
                    if argv[2] == "daemon-reload":
                        self.state["NeedDaemonReload"] = "no"
                    if argv[2] == "restart":
                        self.state["ExecMainStartTimestamp"] = str(int((window.time.time()+1)*1_000_000))
                    return result
                start = self.service.start
                def start_service(cancel: threading.Event, **kwargs: object) -> str | None:
                    if startup_error:
                        kwargs["timeout"] = 0
                    return start(cancel, **kwargs)
                def run_app(_args: list) -> int:
                    nonlocal fail_stop, fail_start
                    app.signals["activate"](app)
                    try:
                        if startup_error:
                            retry_label = "重试" if chinese else "Retry"
                            heading = "HappyRanch 无法运行" if chinese else "HappyRanch could not run"
                            pump(lambda: switch() is not None or any(w.label == heading for w in current()))
                            self.assertTrue(any(w.label == retry_label for w in current()), "startup error needs Retry")
                            detail = (("无法重新加载用户服务管理器。" if chinese else "Could not reload the user manager.")
                                      if "reload" in scenario else
                                      ("连接 HappyRanch 超时。" if chinese else "Timed out waiting to connect to HappyRanch."))
                            self.assertEqual(sum(w.label == heading for w in current()), 1)
                            self.assertTrue(any(w.label and detail in w.label for w in current()))
                            self.assertEqual(urls, [])
                            self.assertFalse(self.service.attached)
                            self.assertFalse(self.service.shutdown_pending)
                            self.assertEqual(self.verbs().count("start"), 0 if "reload" in scenario else 1)
                            self.assertEqual(self.verbs().count("stop"), 0 if "reload" in scenario else 1)
                            self.assertFalse(app.quit_called)
                            self.assertIsNone(window.acquire_lock(self.home))
                            fail_start = False
                            self.healthy = True
                            click(retry_label)
                        pump(lambda: switch() is not None)
                        if startup_error:
                            self.assertEqual(urls, ["http://127.0.0.1:43127/"])
                            self.assertIn("reset-failed", self.verbs())
                            shell.signals["delete-event"]()
                            pump(lambda: app.quit_called)
                            return 0
                        setting_row = shell.children[0].children[0].children[0]
                        self.assertEqual(setting_row.orientation, 1, "setting must be a horizontal row")
                        self.assertIn(switch(), setting_row.children)
                        self.assertEqual(setting_row.packing[switch()], (False, False, 0))
                        for child in current():
                            if child.label in {"Repair", "Turn off", "Restart HappyRanch"}:
                                self.assertTrue(any(row.orientation == 1 and child in row.children
                                                    and row.packing[child] == (False, False, 0)
                                                    for row in shell.children[0].children[0].children),
                                                "action must share a horizontal row without stretching")
                        self.assertTrue(any(w.label == "Keep HappyRanch running when I'm logged out" for w in current()))
                        self.assertEqual(switch().active, restart_error or scenario == "on")
                        if scenario == "fallback":
                            self.assertFalse(switch().sensitive)
                            self.assertTrue(any(w.label and "no systemd user manager" in w.label for w in current()))
                        elif scenario.startswith("partial"):
                            self.assertTrue(any(w.label == "Partly on - click to repair/turn off" for w in current()))
                            self.assertTrue(any(w.label == "Repair" for w in current()))
                            self.assertTrue(any(w.label == "Turn off" for w in current()))
                            click("Repair" if scenario == "partial-repair" else "Turn off")
                            pump(lambda: switch() is not None and switch().active == (scenario == "partial-repair")
                                 and not any(w.label == "Partly on - click to repair/turn off" for w in current()))
                        elif scenario == "denied":
                            switch().signals["state-set"](switch(), True)
                            pump(lambda: any(w.label and "local polkit rule denied" in w.label for w in current()))
                            self.assertFalse(switch().active)
                            self.assertNotIn("enable", self.verbs())
                        elif restart_error:
                            pump(lambda: any(w.label == "Update installed - restart HappyRanch" for w in current()))
                            self.state["NeedDaemonReload"] = "yes"
                            self.assertTrue(self.service.attached)
                            click("Restart HappyRanch")
                            pump(lambda: any(w.label and "shutdown failed" in w.label for w in widgets))
                            self.assertTrue(self.service.shutdown_pending)
                            self.assertFalse(app.quit_called)
                            self.assertIsNone(window.acquire_lock(self.home))
                            self.assertTrue(any(t.name == "desktop-daemon" and t.is_alive()
                                                for t in threading.enumerate()))
                            self.assertEqual(self.verbs().count("stop"), 1)
                            self.assertEqual(len(urls), 1)
                            fail_stop = False
                        elif scenario == "restart":
                            pump(lambda: any(w.label == "Update installed - restart HappyRanch" for w in current()))
                            click("Restart HappyRanch")
                            pump(lambda: len(urls) == 2 and switch() is not None)
                            self.assertEqual(urls, ["http://127.0.0.1:43127/"] * 2)
                            self.assertIn("daemon-reload", self.verbs())
                            self.assertIn("restart", self.verbs())
                        elif scenario == "on":
                            # Change the external state after rendering, before close.
                            self.state["UnitFileState"], self.linger = "disabled", "no"
                        shell.signals["delete-event"]()
                        pump(lambda: app.quit_called)
                        self.assertEqual(self.verbs().count("stop"), 2 if restart_error else
                                         0 if scenario in {"partial-repair", "fallback"} else 1)
                        self.assertFalse(self.service.shutdown_pending)
                        return 0
                    finally:
                        fail_stop = False
                        shell.signals["delete-event"]()
                        pump(lambda: app.quit_called)
                app.run, app.quit = run_app, quit_app
                gi, repository = ModuleType("gi"), ModuleType("gi.repository")
                gi.require_version = lambda *_args: None
                repository.Gio = SimpleNamespace(ApplicationFlags=SimpleNamespace(FLAGS_NONE=0),
                    SimpleAction=SimpleNamespace(new=lambda *_args: Widget()),
                    Cancellable=lambda: SimpleNamespace(cancel=lambda: None))
                repository.GLib = SimpleNamespace(set_prgname=lambda *_args: None, PRIORITY_DEFAULT=0,
                    unix_signal_add=lambda *_args: None,
                    idle_add=lambda callback, *args: callbacks.put((callback, args)))
                repository.Gtk = SimpleNamespace(Application=lambda **_kwargs: app,
                    ApplicationWindow=lambda **_kwargs: shell, Box=Widget, Label=Widget, Button=Widget,
                    Switch=Switch, Separator=Widget, Orientation=SimpleNamespace(VERTICAL=0, HORIZONTAL=1),
                    Align=SimpleNamespace(CENTER=0))
                repository.WebKit2 = SimpleNamespace(WebView=Widget)
                child = SimpleNamespace(poll=lambda: None)
                with patch.dict(sys.modules, {"gi": gi, "gi.repository": repository}), \
                        patch.dict(os.environ, {"LANG": "zh_CN.UTF-8" if chinese else "en_US.UTF-8"}), \
                        patch.object(window, "ServiceMode", return_value=self.service), \
                        patch.object(self.service, "start", side_effect=start_service), \
                        patch.object(self.service, "set_linger", side_effect=linger), \
                        patch.object(self.service, "runner", side_effect=run), \
                        patch.object(window, "prepare_home"), \
                        patch.object(subprocess, "Popen", return_value=child) as popen, \
                        patch.object(window, "wait_for_health", return_value="http://127.0.0.1:12345/"), \
                        patch.object(window, "stop_child") as stop_child:
                    self.healthy = not scenario.startswith("start-attach-timeout")
                    self.assertEqual(window.run_gui(prefix, self.home), 0)
                    self.assertEqual(popen.call_count, 1 if scenario == "fallback" else 0)
                    self.assertEqual(stop_child.call_count, 1 if scenario == "fallback" else 0)
                lock = window.acquire_lock(self.home)
                self.assertIsNotNone(lock)
                lock.close()

    def test_config_write_failure_restores_lifetime_and_original_bytes(self) -> None:
        self.service.desktop_config.parent.mkdir(parents=True)
        original = b'{"linger_set_by_app":false}'
        self.service.desktop_config.write_bytes(original)
        def linger(enabled: bool) -> None:
            self.linger = "yes" if enabled else "no"
        with patch.object(self.service, "set_linger", side_effect=linger), \
                patch.object(window.os, "replace", side_effect=OSError("synthetic disk failure")):
            with self.assertRaisesRegex(OSError, "synthetic disk failure"):
                self.service.set_keep_running(True)
        self.assertEqual(self.service.desktop_config.read_bytes(), original)
        self.assertEqual(self.service.lifetime_state(), "off")
        self.assertEqual(self.verbs().count("disable"), 1)
        self.assertEqual(list(self.service.desktop_config.parent.glob(".desktop-*")), [])

    def test_restart_refuses_lost_ownership_and_keeps_shutdown_obligation(self) -> None:
        runner = self.service.runner
        for failure in ("listener", "health", "unit"):
            with self.subTest(failure=failure):
                self.net()
                self.healthy = True
                self.state["DropInPaths"] = ""
                self.service.attached = False
                self.calls.clear()
                self.probes.clear()
                def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
                    result = runner(argv, **kwargs)
                    if argv[2] == "restart":
                        if failure == "listener":
                            self.net(inode="999")
                        elif failure == "health":
                            self.healthy = False
                        else:
                            self.state["DropInPaths"] = "/synthetic/override.conf"
                    return result
                with patch.object(self.service, "runner", side_effect=run):
                    with self.assertRaises(window.LaunchError):
                        self.service.restart(threading.Event(), timeout=0)
                self.assertIn("restart", self.verbs())
                self.assertFalse(self.service.attached)
                self.assertTrue(self.service.shutdown_pending)
                if failure == "unit":
                    with self.assertRaisesRegex(window.LaunchError, "shutdown failed"):
                        self.service.close_window()
                    self.assertTrue(self.service.shutdown_pending)
                self.state["DropInPaths"] = ""
                self.service.close_window()
                self.assertFalse(self.service.shutdown_pending)

    def test_no_manager_refuses_lifetime_changes_without_mutations(self) -> None:
        self.manager = (1, "offline")
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                with patch.object(self.service, "set_linger") as change:
                    with self.assertRaisesRegex(window.LaunchError, "no user manager"):
                        self.service.set_keep_running(enabled)
                change.assert_not_called()
                self.assertNotIn("enable", self.verbs())
                self.assertNotIn("disable", self.verbs())
                self.assertFalse(self.service.desktop_config.exists())

    def test_random_port_and_print_port_share_read_only_ownership_proof(self) -> None:
        for port in (43127, 53219):
            with self.subTest(port=port):
                self.publish(port=str(port))
                self.net(port=f"{port:04X}")
                self.assertEqual(self.start(), f"http://127.0.0.1:{port}/")
                self.calls.clear()
                self.probes.clear()
                out, err = io.StringIO(), io.StringIO()
                with patch.object(sys, "argv", ["happyranch-app", "--prefix", "/unused", "--print-port"]), \
                        patch.object(window, "ServiceMode", return_value=self.service), \
                        contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    self.assertEqual(window.main(), 0)
                self.assertEqual(out.getvalue(), f"{port}\n")
                self.assertEqual(err.getvalue(), "")
                self.assertEqual(self.probes, [(port, 0.5)])
                self.assertFalse({"start", "stop", "enable", "restart"} & set(self.verbs()))

    def test_print_port_refuses_unproven_daemon_without_lifecycle_effects(self) -> None:
        for case in ("dev", "missing", "symlink", "hardlink", "foreign", "health", "changed", "unit", "inactive", "manager"):
            with self.subTest(case=case):
                (self.home / "daemon.port").unlink(missing_ok=True)
                self.publish()
                self.net()
                self.healthy = True
                self.state.update(ActiveState="active", DropInPaths="")
                self.manager = (0, "running\n")
                self.service.health = self.health
                if case == "dev":
                    self.publish(port="8765")
                    self.net(port="223D")
                elif case == "missing":
                    (self.home / "daemon.port").unlink()
                elif case in {"symlink", "hardlink"}:
                    target = self.user_home / (case + ".port")
                    target.write_text("43127")
                    marker = self.home / "daemon.port"
                    marker.unlink()
                    if case == "symlink":
                        marker.symlink_to(target)
                    else:
                        os.link(target, marker)
                elif case == "foreign":
                    self.net(inode="999")
                elif case == "health":
                    self.healthy = False
                elif case == "changed":
                    def changed(_port: int, _timeout: float) -> bool:
                        self.publish(port="53219")
                        return True
                    self.service.health = changed
                elif case == "unit":
                    self.state["DropInPaths"] = "/override.conf"
                elif case == "inactive":
                    self.state["ActiveState"] = "inactive"
                elif case == "manager":
                    self.manager = (1, "offline\n")
                self.calls.clear()
                self.probes.clear()
                out, err = io.StringIO(), io.StringIO()
                with patch.object(sys, "argv", ["happyranch-app", "--prefix", "/unused", "--daemon-home", str(self.home), "--print-port"]), \
                        patch.object(window, "ServiceMode", return_value=self.service), \
                        contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    self.assertEqual(window.main(), 1)
                self.assertEqual(out.getvalue(), "")
                self.assertIn("happyranch-app:", err.getvalue())
                self.assertFalse({"start", "stop", "enable", "restart"} & set(self.verbs()))
                if case in {"dev", "missing", "symlink", "hardlink", "foreign", "unit", "inactive", "manager"}:
                    self.assertEqual(self.probes, [])

    def test_service_home_uses_manager_xdg_and_main_selects_it_before_gui(self) -> None:
        for value in ("", "relative", str(self.user_home / "custom data"),
                      str(self.user_home / "données 'quoted'\nline")):
            with self.subTest(xdg=value):
                escaped = value.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
                self.environment = "XDG_DATA_HOME=$'" + escaped + "'\n"
                expected = ((Path(value) if value.startswith("/") else self.user_home / ".local/share")
                            / "happyranch")
                expected.mkdir(parents=True, exist_ok=True)
                self.service.home = expected
                self.home = expected
                self.publish()
                self.assertEqual(self.start(), "http://127.0.0.1:43127/")
                with patch.dict(os.environ, {"XDG_DATA_HOME": "/different"}), \
                        patch.object(sys, "argv", ["happyranch-app", "--prefix", "/unused"]), \
                        patch.object(window, "ServiceMode", return_value=self.service), \
                        patch.object(window, "run_gui", return_value=0) as gui:
                    self.assertEqual(window.main(), 0)
                    self.assertEqual(gui.call_args.args[1], expected.resolve())

    def test_service_positive_and_stop_on_close(self) -> None:
        self.state["MainPID"] = "0"
        (self.home / "daemon.pid").unlink()
        (self.home / "daemon.port").unlink()
        self.net(state="01")
        runner = self.service.runner
        def publish_on_start(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            result = runner(argv, **kwargs)
            if argv[2] == "start":
                self.state["MainPID"] = "42"
                self.publish()
                self.net()
            return result
        self.service.runner = publish_on_start
        self.assertEqual(self.start(), "http://127.0.0.1:43127/")
        self.assertEqual(self.probes, [(43127, 0.5)])
        self.assertIn(["systemctl", "--user", "start", "happyranch-daemon"], self.calls)
        self.service.close(keep_running=True)
        self.assertNotIn("stop", self.verbs())
        self.service.close()
        self.assertEqual(self.calls[-1], ["systemctl", "--user", "stop", "happyranch-daemon"])
        self.service.close()
        self.assertEqual(self.verbs().count("stop"), 1)

    @contextlib.contextmanager
    def publishing_marker(self, name: str, raw: bytes | None, *,
                          complete: bool = True) -> Iterator[list[int]]:
        """Hold publication across the first poll, then release a real writer."""
        self.state["MainPID"] = "0"
        for marker in ("daemon.pid", "daemon.port"):
            (self.home / marker).unlink(missing_ok=True)
        self.net(state="01")
        self.calls.clear()
        self.probes.clear()
        runner = self.service.runner
        release = threading.Event()
        writer = None
        publication = None
        polls = [0]
        started = False

        def publish_later() -> None:
            release.wait()
            # Match the real daemon's port-then-PID publication order.
            (self.home / "daemon.port").write_text("43127")
            (self.home / "daemon.pid").write_text("42")

        def run_command(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            nonlocal started, writer, publication
            if argv[2] == "start":
                self.state["MainPID"] = "42"
                self.net()
                if name == "daemon.pid":
                    (self.home / "daemon.port").write_text("43127")
                else:
                    (self.home / "daemon.pid").write_text("42")
                if raw is not None:
                    writer = (self.home / name).open("wb")
                    writer.write(raw)
                    writer.flush()
                if complete:
                    publication = threading.Thread(target=publish_later)
                    publication.start()
                started = True
            elif argv[2] == "show" and started:
                polls[0] += 1
                if polls[0] == 2 and complete:
                    self.assertEqual(self.probes, [], "probed before complete publication")
                    release.set()
                    publication.join(1)
                    self.assertFalse(publication.is_alive(), "publication writer did not finish")
            return runner(argv, **kwargs)

        self.service.runner = run_command
        try:
            yield polls
        finally:
            release.set()
            if publication is not None:
                publication.join(1)
                self.assertFalse(publication.is_alive())
            if writer is not None:
                writer.close()
            self.service.runner = runner

    def test_start_waits_for_marker_publication_before_attaching(self) -> None:
        for name in ("daemon.port", "daemon.pid"):
            for raw in (None, b"", b"bad", b"\xff", b"4"):
                with self.subTest(marker=name, raw=raw), self.publishing_marker(name, raw) as polls:
                    try:
                        url = self.service.start(threading.Event(), timeout=1)
                    except window.LaunchError as exc:
                        self.fail(f"Pending {name} refused before publication: {exc}")
                    self.assertEqual(url, "http://127.0.0.1:43127/")
                    self.assertGreaterEqual(polls[0], 2)
                    self.assertEqual(self.probes, [(43127, 0.5)])
                    self.assertTrue(self.service.attached)
                    self.assertNotIn("stop", self.verbs())
                    self.service.close()

    def test_invalid_post_start_marker_waits_for_deadline_then_stops_once(self) -> None:
        for name in ("daemon.port", "daemon.pid"):
            for raw in (b"", b"bad", b"\xff"):
                with self.subTest(marker=name, raw=raw), \
                        self.publishing_marker(name, raw, complete=False) as polls:
                    with self.assertRaises(window.LaunchError) as caught:
                        self.service.start(threading.Event(), timeout=0.02)
                    self.assertEqual(str(caught.exception),
                                     "Release daemon ownership or health check failed; no web view was attached.")
                    self.assertEqual(caught.exception.action, "retry")
                    self.assertGreaterEqual(polls[0], 2, "invalid marker refused before deadline")
                    self.assertEqual(self.probes, [])
                    self.assertFalse(self.service.attached)
                    self.assertTrue(self.service.shutdown_pending)
                    self.assertNotIn("stop", self.verbs())
                    self.service.close()
                    self.service.close()
                    self.assertEqual([call for call in self.calls if call[2] == "stop"],
                                     [["systemctl", "--user", "stop", "happyranch-daemon"]])
                    self.assertFalse(self.service.shutdown_pending)
                    self.assertEqual((self.home / name).read_bytes(), raw)

    def test_start_reloads_changed_unit_before_start_or_retry(self) -> None:
        for reload in ("yes", "no"):
            for retry in (False, True):
                with self.subTest(reload=reload, retry=retry):
                    self.calls.clear()
                    self.state["NeedDaemonReload"] = reload
                    self.assertEqual(self.start(retry=retry), "http://127.0.0.1:43127/")
                    self.assertTrue(self.service.attached)
                    expected = ["systemctl", "--user", "daemon-reload"]
                    if reload == "yes":
                        self.assertIn(expected, self.calls)
                        self.assertEqual(self.calls.count(expected), 1)
                        self.assertLess(self.verbs().index("daemon-reload"), self.verbs().index("start"))
                        if retry:
                            self.assertLess(self.verbs().index("daemon-reload"), self.verbs().index("reset-failed"))
                    else:
                        self.assertNotIn(expected, self.calls)

    def test_manager_fallback_and_degraded_manager(self) -> None:
        for rc, output in ((1, "offline"), (1, ""), (127, "")):
            with self.subTest(rc=rc, output=output):
                self.manager = rc, output
                self.assertIsNone(self.start())
                self.service.close()
                self.assertNotIn("start", self.verbs())
                self.assertNotIn("stop", self.verbs())
                self.assertEqual(self.probes, [])
        for error in (FileNotFoundError(), subprocess.TimeoutExpired("systemctl", 10)):
            with self.subTest(error=type(error).__name__), patch.object(self.service, "runner", side_effect=error):
                self.assertIsNone(self.start())
        self.manager = 1, "degraded\n"
        self.assertEqual(self.start(), "http://127.0.0.1:43127/")

    def test_manager_pollution_names_only_and_inspection_failure(self) -> None:
        self.environment = "HAPPYRANCH_DAEMON_HOME=synthetic-private-value\nHAPPYRANCH_DEBUG=synthetic-secret\n"
        with self.assertRaises(window.LaunchError) as caught:
            self.start()
        self.assertIn("HAPPYRANCH_DAEMON_HOME", str(caught.exception))
        self.assertIn("HAPPYRANCH_DEBUG", str(caught.exception))
        self.assertNotIn("synthetic-", str(caught.exception))
        self.assertNotIn("start", self.verbs())
        self.failed_command = "show-environment"
        with self.assertRaisesRegex(window.LaunchError, "inspect.*environment"):
            self.start()

    def test_env_file_accepts_literal_path_additions(self) -> None:
        for value in (window.ServiceMode.base_path,
                      "/home/example/node/bin:" + window.ServiceMode.base_path,
                      window.ServiceMode.base_path + ":/home/example/node/bin"):
            with self.subTest(value=value):
                path = self.env_file("# optional tools\n; comment\nPATH=\"" + value + "\"\n")
                before = path.read_bytes()
                self.assertEqual(self.start(), "http://127.0.0.1:43127/")
                self.assertEqual(path.read_bytes(), before)

    def test_env_file_refuses_every_other_key_and_path_replacement(self) -> None:
        for text, key in (("LD_PRELOAD=synthetic-value", "LD_PRELOAD"),
                          ("LD_LIBRARY_PATH=synthetic-value", "LD_LIBRARY_PATH"),
                          ("PYTHONPATH=synthetic-value", "PYTHONPATH"),
                          ("HAPPYRANCH_DAEMON_PORT=synthetic-value", "HAPPYRANCH_DAEMON_PORT"),
                          ("OTHER=synthetic-value", "OTHER"), ("export PATH=x", "<invalid line>"),
                          ("PATH=/usr/bin", "PATH"), ("PATH=/extra:$PATH", "PATH"),
                          ("PATH=:" + window.ServiceMode.base_path, "PATH"),
                          ("PATH=" + window.ServiceMode.base_path + ":relative", "PATH"),
                          ("PATH=" + window.ServiceMode.base_path + ":%h/bin", "PATH")):
            with self.subTest(key=key, text=text):
                path = self.env_file(text)
                before = path.read_bytes()
                with self.assertRaises(window.LaunchError) as caught:
                    self.start()
                self.assertIn(key, str(caught.exception))
                self.assertNotIn("synthetic-value", str(caught.exception))
                self.assertEqual(path.read_bytes(), before)
                self.assertNotIn("start", self.verbs())
        path.unlink()
        path.write_bytes(b"PATH=\xff")
        with self.assertRaisesRegex(window.LaunchError, "not valid UTF-8"):
            self.start()
        path.unlink()
        path.symlink_to(self.user_home / "absent")
        with self.assertRaisesRegex(window.LaunchError, "regular file"):
            self.start()

    def test_unit_and_cli_pid_refusals_never_start_stop_or_probe(self) -> None:
        for key, value in (("FragmentPath", "/home/example/override.service"),
                           ("DropInPaths", "/home/example/override.conf"),
                           ("MainPID", "invalid")):
            with self.subTest(key=key):
                original = self.state[key]
                self.state[key] = value
                with self.assertRaises(window.LaunchError):
                    self.start()
                self.state[key] = original
        (self.proc / "77").mkdir()
        self.publish(pid="77")
        with self.assertRaisesRegex(window.LaunchError, "CLI-started.*Stop it yourself"):
            self.start()
        self.assertEqual((self.home / "daemon.pid").read_text(), "77")
        self.service.close()
        self.assertNotIn("start", self.verbs())
        self.assertNotIn("stop", self.verbs())
        self.assertEqual(self.probes, [])

    def test_foreign_listener_refuses_ipv4_and_dual_stack(self) -> None:
        for protocol, address in (("tcp", "0100007F"), ("tcp", "00000000"),
                                  ("tcp6", "0" * 32),
                                  ("tcp6", "0000000000000000FFFF00000100007F")):
            with self.subTest(protocol=protocol, address=address):
                self.net(inode="999", address=address, protocol=protocol)
                with self.assertRaisesRegex(window.LaunchError, "Port 43127 is used by another program"):
                    self.start()
                self.net(protocol=protocol, state="01")
        self.assertNotIn("start", self.verbs())
        self.assertEqual(self.probes, [])

    def test_markers_listener_health_and_post_health_recheck_all_required(self) -> None:
        for pid, port in (("999", "43127"), ("42", "8765"), ("42", "bad")):
            with self.subTest(pid=pid, port=port):
                self.publish(pid, port)
                expected = "Invalid daemon.port" if port == "bad" else "ownership or health"
                with self.assertRaisesRegex(window.LaunchError, expected):
                    self.start()
        self.publish()
        self.net(state="01")
        with self.assertRaisesRegex(window.LaunchError, "ownership or health"):
            self.start()
        self.net()
        self.healthy = False
        with self.assertRaisesRegex(window.LaunchError, "ownership or health"):
            self.start()
        def replaced(_port: int, _timeout: float) -> bool:
            self.publish(port="8765")
            return True
        self.service.health = replaced
        with self.assertRaisesRegex(window.LaunchError, "ownership or health"):
            self.start()
        self.service.close()
        self.assertEqual(self.calls[-1], ["systemctl", "--user", "stop", "happyranch-daemon"])
        self.assertEqual(self.verbs().count("stop"), 1)

    def test_missing_proc_evidence_refuses_without_stop(self) -> None:
        (self.proc / "net/tcp").unlink()
        with self.assertRaises(OSError):
            self.start()
        self.net()
        (self.proc / "42/fd/3").unlink()
        (self.proc / "42/fd").rmdir()
        with self.assertRaises(OSError):
            self.start()
        self.assertNotIn("start", self.verbs())
        self.service.close()
        self.assertNotIn("stop", self.verbs())

    def test_close_during_startup_stops_only_validated_service(self) -> None:
        cancel = threading.Event()
        cancel.set()
        self.healthy = False
        with self.assertRaisesRegex(window.LaunchError, "cancelled"):
            self.service.start(cancel, timeout=0)
        self.service.close()
        self.assertEqual(self.calls[-1], ["systemctl", "--user", "stop", "happyranch-daemon"])
        self.service.close()
        self.assertEqual(self.verbs().count("stop"), 1)
        self.calls.clear()
        self.net(inode="999")
        with self.assertRaisesRegex(window.LaunchError, "Port 43127"):
            self.service.start(cancel, timeout=0)
        self.assertNotIn("stop", self.verbs())

    def check_startup_cancel_stop_retry(self, *, timeout: bool) -> None:
        """Drive run_gui's real worker/close/finish path with only GI doubles."""
        callbacks = queue.Queue()
        labels = []
        started = threading.Event()
        fail_stop = True
        self.healthy = False
        runner = self.service.runner

        def run_command(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            result = runner(argv, **kwargs)
            if argv[2] == "start":
                started.set()
            if argv[2] == "stop" and fail_stop:
                if timeout:
                    raise subprocess.TimeoutExpired(argv, 10)
                result.returncode = 1
            return result

        self.service.runner = run_command

        class Widget:
            def __init__(self, **kwargs: object) -> None:
                self.children = []
                self.signals = {}
                if "label" in kwargs:
                    labels.append(kwargs["label"])

            def connect(self, name: str, callback: object) -> None:
                self.signals[name] = callback

            def get_children(self) -> list:
                return self.children[:]

            def add(self, child: object) -> None:
                self.children.append(child)

            def pack_start(self, child: object, *_args: object) -> None:
                self.add(child)

            def remove(self, child: object) -> None:
                self.children.remove(child)

            def __getattr__(self, _name: str) -> object:
                return lambda *_args: None

        shell = Widget()
        app = Widget()
        app.quit_called = False

        def quit_app() -> None:
            app.quit_called = True

        def pump_until(predicate: Callable[[], bool]) -> None:
            # Bounded queue waits coordinate the real worker without sleeps.
            for _ in range(30):
                if predicate():
                    return
                try:
                    callback, args = callbacks.get(timeout=0.1)
                except queue.Empty:
                    continue
                callback(*args)
            self.fail("GUI worker did not reach the expected state")

        def run_app(_args: list) -> int:
            nonlocal fail_stop
            app.signals["activate"](app)
            try:
                self.assertTrue(started.wait(2))
                shell.signals["delete-event"]()
                pump_until(lambda: app.quit_called or any("shutdown failed" in label for label in labels))
                self.assertFalse(app.quit_called, "failed startup stop released GUI custody")
                self.assertFalse(self.service.attached)
                self.assertTrue(any("Close the window again to retry" in label for label in labels))
                self.assertTrue(any(thread.name == "desktop-daemon" and thread.is_alive()
                                    for thread in threading.enumerate()))
                second_lock = window.acquire_lock(self.home)
                if second_lock is not None:
                    second_lock.close()
                self.assertIsNone(second_lock, "desktop.lock was released before shutdown")
                stops = [call for call in self.calls if call[2] == "stop"]
                self.assertEqual(stops, [["systemctl", "--user", "stop", "happyranch-daemon"]])
                fail_stop = False
                shell.signals["delete-event"]()
                pump_until(lambda: app.quit_called)
                self.assertEqual([call for call in self.calls if call[2] == "stop"], stops * 2)
                self.assertEqual(self.verbs().count("start"), 1)
                return 0
            finally:
                fail_stop = False
                shell.signals["delete-event"]()
                pump_until(lambda: app.quit_called)

        app.run = run_app
        app.quit = quit_app
        gi = ModuleType("gi")
        gi.require_version = lambda *_args: None
        repository = ModuleType("gi.repository")
        repository.Gio = SimpleNamespace(ApplicationFlags=SimpleNamespace(FLAGS_NONE=0),
                                         SimpleAction=SimpleNamespace(new=lambda *_args: Widget()),
                                         Cancellable=lambda: SimpleNamespace(cancel=lambda: None))
        repository.GLib = SimpleNamespace(set_prgname=lambda *_args: None, PRIORITY_DEFAULT=0,
                                          unix_signal_add=lambda *_args: None,
                                          idle_add=lambda callback, *args: callbacks.put((callback, args)))
        repository.Gtk = SimpleNamespace(Application=lambda **_kwargs: app,
                                         ApplicationWindow=lambda **_kwargs: shell,
                                         Box=Widget, Label=Widget, Button=Widget,
                                         Orientation=SimpleNamespace(VERTICAL=0))
        repository.WebKit2 = SimpleNamespace()
        with patch.dict(sys.modules, {"gi": gi, "gi.repository": repository}), \
                patch.object(window, "ServiceMode", return_value=self.service), \
                patch.object(subprocess, "Popen", side_effect=AssertionError("no child launch")):
            self.assertEqual(window.run_gui(Path("/synthetic-payload"), self.home), 0)
        lock = window.acquire_lock(self.home)
        self.assertIsNotNone(lock)
        lock.close()

    def test_startup_cancel_nonzero_stop_keeps_lock_and_retries_on_close(self) -> None:
        self.check_startup_cancel_stop_retry(timeout=False)

    def test_startup_cancel_timeout_stop_keeps_lock_and_retries_on_close(self) -> None:
        self.check_startup_cancel_stop_retry(timeout=True)

    def test_invalid_markers_refuse_before_start_health_or_stop(self) -> None:
        for name in ("daemon.pid", "daemon.port"):
            for raw in (b"\xff", b"", b"bad", b"-1", b"0", "１２".encode()):
                with self.subTest(marker=name, raw=raw):
                    self.publish()
                    path = self.home / name
                    path.write_bytes(raw)
                    self.calls.clear()
                    with self.assertRaisesRegex(window.LaunchError, name):
                        self.start()
                    self.service.close()
                    self.assertEqual(path.read_bytes(), raw)
                    self.assertEqual(self.probes, [])
                    self.assertEqual(self.verbs(), ["is-system-running", "show-environment", "show"])

    def test_start_time_foreign_listener_and_crash_limit(self) -> None:
        runner = self.service.runner
        def changed_on_start(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            result = runner(argv, **kwargs)
            if argv[2] == "start":
                self.net(inode="999")
            return result
        self.service.runner = changed_on_start
        with self.assertRaisesRegex(window.LaunchError, "Port 43127"):
            self.start()
        self.assertIn("start", self.verbs())
        self.assertEqual(self.probes, [])
        self.assertNotIn("stop", self.verbs())
        self.net()
        def failed_on_start(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            result = runner(argv, **kwargs)
            if argv[2] == "start":
                self.state.update(ActiveState="failed", Result="start-limit-hit")
                result.returncode = 1
            return result
        self.service.runner = failed_on_start
        with self.assertRaises(window.LaunchError) as caught:
            self.start()
        self.assertEqual(caught.exception.action, "retry")

    def test_start_limit_journal_and_explicit_retry(self) -> None:
        self.state.update(ActiveState="failed", Result="start-limit-hit")
        with self.assertRaises(window.LaunchError) as caught:
            self.start()
        self.assertEqual(caught.exception.action, "retry")
        self.assertEqual(str(caught.exception).splitlines()[1:],
                         [f"synthetic journal {i}" for i in range(5, 25)])
        self.assertIn(["journalctl", "--user", "-u", "happyranch-daemon", "-n", "20", "--no-pager"], self.calls)
        self.assertNotIn("start", self.verbs())
        self.assertEqual(self.start(retry=True), "http://127.0.0.1:43127/")
        self.assertLess(self.verbs().index("reset-failed"), self.verbs().index("start"))

    def test_command_failures_and_custom_home_refuse(self) -> None:
        for command in ("show", "start", "reset-failed"):
            with self.subTest(command=command):
                self.failed_command = command
                with self.assertRaises(window.LaunchError):
                    self.start(retry=command == "reset-failed")
        self.failed_command = None
        self.start()
        self.failed_command = "stop"
        with self.assertRaises(window.LaunchError):
            self.service.close()
        self.assertTrue(self.service.attached)
        self.service.home = self.user_home / "custom"
        with self.assertRaisesRegex(window.LaunchError, "requires.*release"):
            self.start()

    def test_unknown_legacy_unit_never_modified(self) -> None:
        path = self.service.legacy_unit
        path.parent.mkdir(parents=True)
        path.write_bytes(b"synthetic user-owned unit\n")
        for operation in (self.start, self.service.remove_legacy):
            with self.assertRaisesRegex(window.LaunchError, "different hand-written") as caught:
                operation()
            self.assertIsNone(caught.exception.action)
            self.assertEqual(path.read_bytes(), b"synthetic user-owned unit\n")
        self.assertNotIn("daemon-reload", self.verbs())
        self.assertNotIn("start", self.verbs())
        path.unlink()
        path.symlink_to(self.user_home / "absent")
        with self.assertRaisesRegex(window.LaunchError, "not a regular file"):
            self.service.remove_legacy()
        self.assertTrue(path.is_symlink())

    def test_known_legacy_offers_removal_only_on_explicit_action(self) -> None:
        path = self.service.legacy_unit
        path.parent.mkdir(parents=True)
        content = b"synthetic known old unit\n"
        path.write_bytes(content)
        self.assertEqual(self.service.legacy_sha,
                         "482dee228bce0f41354ea7a803341b1b6cdb4076a41c713467b10d348f4a2817")
        # No actual user unit is read. Substitute only the digest of this synthetic fixture.
        real_sha = window.hashlib.sha256
        def digest(data: bytes) -> object:
            if data == content:
                result = unittest.mock.Mock()
                result.hexdigest.return_value = self.service.legacy_sha
                return result
            return real_sha(data)
        with patch.object(window.hashlib, "sha256", side_effect=digest):
            with self.assertRaises(window.LaunchError) as caught:
                self.start()
            self.assertEqual(caught.exception.action, "remove")
            self.assertEqual(path.read_bytes(), content)
            self.assertNotIn("daemon-reload", self.verbs())
            self.service.remove_legacy()
            self.assertFalse(path.exists())
            self.assertEqual(self.calls[-1], ["systemctl", "--user", "daemon-reload"])
            self.assertNotIn("start", self.verbs())
            path.write_bytes(content)
            path.write_bytes(b"changed after offer")
            with self.assertRaisesRegex(window.LaunchError, "different hand-written"):
                self.service.remove_legacy()
            self.assertEqual(path.read_bytes(), b"changed after offer")


class RemoteAccessTests(unittest.TestCase):
    """Drive the shipped window with GI/transport doubles, never a host bus."""

    @staticmethod
    def snapshot(phase: str = "pending", ready: bool = True) -> dict:
        device = {"label": "Travel laptop", "fingerprint": "0123456789abcdef01234567",
                  "comparison_phrase": "clay meadow river"}
        return {"phase": phase, "ready": ready, "devices": [dict(device, label="Paired laptop")],
                "pending": device if phase == "pending" else None, "selected": None}

    def exercise(self, check: Callable, snapshot: dict | None = None, *,
                 chinese: bool = False, connect_error: bool = False,
                 hostname: str = "home-host") -> None:
        self.data = snapshot if snapshot is not None else self.snapshot()
        self.calls, self.pending, self.callbacks = [], [], queue.Queue()
        test = self

        class Widget:
            def __init__(self, **kwargs: object) -> None:
                self.label = kwargs.get("label", "")
                self.children, self.signals = [], {}
                self.parent, self.sensitive = None, True

            def connect(self, name: str, callback: Callable) -> None:
                self.signals[name] = callback

            def get_children(self) -> list:
                return self.children[:]

            def get_parent(self) -> object:
                return self.parent

            def add(self, child: object) -> None:
                child.parent = self
                self.children.append(child)

            def pack_start(self, child: object, *_args: object) -> None:
                self.add(child)

            def remove(self, child: object) -> None:
                self.children.remove(child)
                child.parent = None

            def set_sensitive(self, value: bool) -> None:
                self.sensitive = value

            def set_markup(self, _value: str) -> None:
                test.fail("device metadata must use plain GTK text")

            def __getattr__(self, _name: str) -> Callable:
                return lambda *_args: None

        class Reply:
            def __init__(self, value: object) -> None:
                self.value = value

            def unpack(self) -> tuple:
                return (json.dumps(self.value),)

        class Error(Exception):
            pass

        class Connection:
            closed = False

            def is_closed(self) -> bool:
                return self.closed

            def set_exit_on_close(self, value: bool) -> None:
                test.assertFalse(value, "system-bus loss must not terminate the window")

            def call(self, *args: object) -> None:
                test.calls.append(args)
                method, callback = args[3], args[-2]
                if method in {"Status", "NextDevice"}:
                    test.callbacks.put((callback, (self, Reply(test.data), args[-1])))
                else:
                    test.pending.append((callback, args[-1]))

            def call_finish(self, result: object) -> Reply:
                if isinstance(result, Exception):
                    raise result
                return result

            def close(self, *_args: object) -> None:
                self.closed = True

        class Cancellable:
            cancelled = False

            def cancel(self) -> None:
                self.cancelled = True

        connection = Connection()
        self.connection = connection
        self.Error = Error
        self.cancel = Cancellable()
        shell, app = Widget(), Widget()
        self.shell = shell

        def descendants(widget: Widget) -> list:
            return [widget] + [item for child in widget.children for item in descendants(child)]

        self.labels = lambda: [widget.label for widget in descendants(shell) if widget.label]

        def pump() -> None:
            for _ in range(50):
                if self.callbacks.empty():
                    return
                callback, args = self.callbacks.get_nowait()
                callback(*args)
            self.fail("unexpected automatic retry or queued action")

        def find_button(label: str) -> Widget:
            matches = [w for w in descendants(shell) if w.label == label and "clicked" in w.signals]
            self.assertEqual(len(matches), 1, f"missing or duplicate action: {label}; {self.labels()}")
            return matches[0]

        def click(label: str) -> None:
            widget = find_button(label)
            self.assertTrue(widget.sensitive, f"disabled action: {label}")
            widget.signals["clicked"](widget)
            pump()

        self.click, self.find_button = click, find_button

        def respond(value: object) -> None:
            self.assertEqual(len(self.pending), 1)
            callback, method = self.pending.pop()
            callback(connection, value if isinstance(value, Exception) else Reply(value), method)
            pump()

        self.respond = respond

        def connect_bus(*args: object) -> None:
            self.assertEqual(args[0], "unix:path=/run/dbus/system_bus_socket")
            callback = args[-2]
            self.callbacks.put((callback, (None, None, None)))

        def finish_connect(_result: object) -> Connection:
            if connect_error:
                raise Error("org.freedesktop.DBus.Error.ServiceUnknown")
            return connection

        def run_app(_args: list) -> int:
            app.signals["activate"](app)
            try:
                pump()
                check()
                return 0
            finally:
                shell.signals["delete-event"]()

        app.run = run_app
        gi, repository = ModuleType("gi"), ModuleType("gi.repository")
        gi.require_version = lambda *_args: None
        repository.Gio = SimpleNamespace(ApplicationFlags=SimpleNamespace(FLAGS_NONE=0),
            SimpleAction=SimpleNamespace(new=lambda *_args: Widget()), Cancellable=lambda: self.cancel,
            DBusConnection=SimpleNamespace(new_for_address=connect_bus,
                new_for_address_finish=finish_connect),
            DBusConnectionFlags=SimpleNamespace(AUTHENTICATION_CLIENT=1, MESSAGE_BUS_CONNECTION=2),
            DBusCallFlags=SimpleNamespace(NONE=0, ALLOW_INTERACTIVE_AUTHORIZATION=1),
            DBusError=SimpleNamespace(get_remote_error=lambda error: str(error)))
        repository.GLib = SimpleNamespace(set_prgname=lambda *_args: None, PRIORITY_DEFAULT=0,
            unix_signal_add=lambda *_args: None, Error=Error,
            VariantType=SimpleNamespace(new=lambda signature: signature),
            idle_add=lambda callback, *args: self.callbacks.put((callback, args)))
        repository.Gtk = SimpleNamespace(Application=lambda **_kwargs: app,
            ApplicationWindow=lambda **_kwargs: shell, Box=Widget, Label=Widget, Button=Widget,
            ScrolledWindow=Widget, Separator=Widget, Orientation=SimpleNamespace(VERTICAL=0, HORIZONTAL=1),
            PolicyType=SimpleNamespace(NEVER=0, AUTOMATIC=1))
        repository.WebKit2 = SimpleNamespace()
        # Startup remains idle: these checks own remote UI, not daemon lifecycle.
        thread = SimpleNamespace(start=lambda: None, join=lambda *_args: None)
        with tempfile.TemporaryDirectory() as scratch, \
                patch.dict(sys.modules, {"gi": gi, "gi.repository": repository}), \
                patch.dict(os.environ, {"LANG": "zh_CN.UTF-8" if chinese else "en_US.UTF-8"}), \
                patch.object(window.threading, "Thread", return_value=thread), \
                patch.object(window, "ServiceMode"), \
                patch("socket.gethostname", return_value=hostname):
            self.assertEqual(window.run_gui(Path("/synthetic-payload"), Path(scratch)), 0)

    def open_details(self) -> None:
        self.click("Settings and devices")

    def verbs(self) -> list[str]:
        return [call[3] for call in self.calls]

    def test_remote_states_and_target_in_both_languages(self) -> None:
        cases = (("off", False, "Not set up", "尚未设置"),
                 ("ready", True, "Ready to invite", "可邀请设备"),
                 ("pending", True, "Ready to invite", "可邀请设备"),
                 ("paused", False, "Remote access ready; HappyRanch offline/restarting",
                  "远程访问已就绪；HappyRanch 已离线或正在重启"),
                 ("failed", False, "Remote access unavailable — Retry", "远程访问不可用 — 重试"))
        for phase, ready, english, chinese in cases:
            for zh in (False, True):
                with self.subTest(phase=phase, chinese=zh):
                    def check() -> None:
                        self.assertIn(chinese if zh else english, self.labels())
                        self.click("设置与设备" if zh else "Settings and devices")
                        self.assertIn("此电脑上的 HappyRanch：home-host" if zh else
                                      "HappyRanch on home-host", self.labels())
                        self.assertFalse(any("admin" in str(label).lower() for label in self.labels()))
                        self.assertEqual(self.verbs(), ["Status", "Status"])
                    self.exercise(check, self.snapshot(phase, ready), chinese=zh)
        self.exercise(lambda: self.assertIn("Not set up", self.labels()), connect_error=True)

    def test_remote_metadata_is_plain_bounded_and_not_derived_from_fingerprint(self) -> None:
        data = self.snapshot()
        data["pending"]["label"] = "&lt;b&gt;OS device&lt;/b&gt;" + "x" * 100
        data["pending"]["comparison_phrase"] = "&lt;meadow&gt;"
        def check() -> None:
            self.open_details()
            self.assertIn("Device: " + ("<b>OS device</b>" + "x" * 100)[:96], self.labels())
            self.assertIn("Comparison phrase: <meadow>", self.labels())
            self.assertIn("Stable-ID fingerprint: 0123456789abcdef01234567", self.labels())
            self.assertNotIn("Device: 0123456789abcdef01234567", self.labels())
            self.assertIn("Devices with access (1)", self.labels())
            self.assertIn("HappyRanch on " + ("<host>" + "h" * 100)[:96], self.labels())
        self.exercise(check, data, hostname="<host>" + "h" * 100)

    def test_remote_approval_requires_successful_helper_reply(self) -> None:
        def check() -> None:
            self.open_details()
            stale_button = self.find_button("Approve device")
            self.click("Approve device")
            # A click delivered from the removed/disabled widget cannot queue work.
            stale_button.signals["clicked"](stale_button)
            self.assertIn("Waiting for system permission…", self.labels())
            self.assertIn("Pending device", self.labels())
            self.assertEqual(self.verbs(), ["Status", "Status", "ApprovePending"])
            self.respond("not-ok")
            self.assertIn("Action cancelled — no change", self.labels())
            self.assertIn("Pending device", self.labels())
            self.assertEqual(self.verbs(), ["Status", "Status", "ApprovePending"])
        self.exercise(check)

    def test_remote_cancel_preserves_state_and_queues_nothing(self) -> None:
        for label, method in (("Approve device", "ApprovePending"),
                              ("Reject device", "RejectPending"), ("Revoke device", "RevokeSelected")):
            with self.subTest(method=method):
                data = self.snapshot()
                data["selected"] = data["devices"][0]
                def check() -> None:
                    self.open_details()
                    before = self.labels()
                    self.click(label)
                    self.respond(self.Error("org.freedesktop.PolicyKit1.Error.Cancelled"))
                    self.assertIn("Action cancelled — no change", self.labels())
                    for text in before:
                        self.assertIn(text, self.labels())
                    self.assertEqual(self.verbs(), ["Status", "Status", method])
                    self.assertEqual(self.pending, [])
                    self.assertTrue(self.callbacks.empty())
                    # Only a new deliberate click requests a fresh helper/polkit action.
                    self.click(label)
                    self.assertEqual(self.verbs(), ["Status", "Status", method, method])
                    self.respond(self.Error("denied"))
                self.exercise(check, data)

    def test_remote_success_and_selection_come_only_from_fresh_helper_status(self) -> None:
        for label, method, count in (("Approve device", "ApprovePending", 2),
                                     ("Reject device", "RejectPending", 1),
                                     ("Revoke device", "RevokeSelected", 0)):
            with self.subTest(method=method):
                def check() -> None:
                    self.open_details()
                    self.assertNotIn("Revoke device", self.labels())
                    self.data = self.snapshot()
                    self.data["selected"] = self.data["devices"][0]
                    self.click("Next device")
                    self.click(label)
                    self.assertIn("Device: Paired laptop", self.labels())
                    self.data = self.snapshot("ready")
                    if count == 2:
                        self.data["devices"].append(dict(self.snapshot()["pending"]))
                    elif count == 0:
                        self.data["devices"] = []
                    self.respond("ok")
                    self.assertIn(f"Devices with access ({count})", self.labels())
                    self.assertIn("No pending device", self.labels())
                    self.assertNotIn("Revoke device", self.labels())
                    self.assertEqual(self.verbs(), ["Status", "Status", "NextDevice", method, "Status"])
                    for call in self.calls:
                        self.assertEqual(call[:3], ("ai.happyranch.RemoteAccess1",
                            "/ai/happyranch/RemoteAccess1", "ai.happyranch.RemoteAccess1"))
                        self.assertIsNone(call[4], "helper methods have no input arguments")
                        self.assertEqual(call[6], 1 if call[3] == method else 0)
                self.exercise(check)

    def test_remote_errors_do_not_log_secrets_or_fingerprints(self) -> None:
        canary = "synthetic-credential-invite-hrinvite2-secret"
        fingerprint = "0123456789abcdef01234567"
        output = io.StringIO()
        handler = window.logging.StreamHandler(output)
        root = window.logging.getLogger()
        self.addCleanup(root.setLevel, root.level)
        root.setLevel(window.logging.DEBUG)
        root.addHandler(handler)
        self.addCleanup(root.removeHandler, handler)
        def check() -> None:
            self.open_details()
            self.click("Approve device")
            self.respond(self.Error(canary + fingerprint))
            self.assertIn("Action cancelled — no change", self.labels())
            # A malformed read must not become display or logs either.
            self.data = {"secret": canary, "fingerprint": fingerprint}
            self.click("Refresh")
            self.assertIn("Remote access unavailable — Retry", self.labels())
            self.assertFalse(any(canary in label for label in self.labels()))
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            self.exercise(check)
        self.assertNotIn(canary, output.getvalue())
        self.assertNotIn(fingerprint, output.getvalue())

    def test_remote_close_cancels_inflight_and_ignores_late_reply(self) -> None:
        def check() -> None:
            self.open_details()
            self.click("Approve device")
            self.shell.signals["delete-event"]()
            self.assertTrue(self.cancel.cancelled)
            self.assertTrue(self.connection.closed)
            self.respond("ok")
            self.assertEqual(self.verbs(), ["Status", "Status", "ApprovePending"])
        self.exercise(check)


if __name__ == "__main__":
    unittest.main()
