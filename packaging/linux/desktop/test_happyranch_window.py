"""Run directly with system Python; no display, GI, or HappyRanch imports."""
from __future__ import annotations

import contextlib
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

    def test_home_uses_release_home_ignoring_inherited_settings(self) -> None:
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(self.home),
                                     "HAPPYRANCH_DAEMON_HOME": "/external"}):
            self.assertEqual(window.desktop_home(), Path.home() / ".happyranch-release")
        for value in ("", "relative"):
            with self.subTest(value=value), patch.dict(os.environ, {"XDG_DATA_HOME": value}):
                self.assertEqual(window.desktop_home(), Path.home() / ".happyranch-release")
        with patch.object(Path, "home", return_value=self.home):
            with self.assertRaisesRegex(window.LaunchError, "development daemon home"):
                window.acquire_lock(self.home / ".happyranch")
            self.assertFalse((self.home / ".happyranch").exists())

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
        self.home = self.user_home / ".happyranch-release"
        self.home.mkdir()
        self.proc = self.user_home / "proc"
        (self.proc / "net").mkdir(parents=True)
        (self.proc / "42/fd").mkdir(parents=True)
        (self.proc / "42/fd/3").symlink_to("socket:[555]")
        self.publish()
        self.net()
        self.state = {"MainPID": "42", "FragmentPath": window.ServiceMode.fragment,
                      "DropInPaths": "", "ActiveState": "active", "Result": "success"}
        self.calls = []
        self.probes = []
        self.manager = (0, "running\n")
        self.environment = "PATH=/usr/bin\n"
        self.failed_command = None
        self.healthy = True
        self.service = window.ServiceMode(self.home, user_home=self.user_home,
                                          proc=self.proc, runner=self.run_command,
                                          health=self.health)

    def publish(self, pid: str = "42", port: str = "8766") -> None:
        (self.home / "daemon.pid").write_text(pid)
        (self.home / "daemon.port").write_text(port)

    def net(self, *, inode: str = "555", address: str = "0100007F",
            protocol: str = "tcp", state: str = "0A", port: str = "223E") -> None:
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
        self.assertEqual(self.start(), "http://127.0.0.1:8766/")
        self.assertEqual(self.probes, [(8766, 0.5)])
        self.assertIn(["systemctl", "--user", "start", "happyranch-daemon"], self.calls)
        self.service.close(keep_running=True)
        self.assertNotIn("stop", self.verbs())
        self.service.close()
        self.assertEqual(self.calls[-1], ["systemctl", "--user", "stop", "happyranch-daemon"])
        self.service.close()
        self.assertEqual(self.verbs().count("stop"), 1)

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
        self.assertEqual(self.start(), "http://127.0.0.1:8766/")

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
                self.assertEqual(self.start(), "http://127.0.0.1:8766/")
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
                with self.assertRaisesRegex(window.LaunchError, "Port 8766 is used by another program"):
                    self.start()
                self.net(protocol=protocol, state="01")
        self.assertNotIn("start", self.verbs())
        self.assertEqual(self.probes, [])

    def test_markers_listener_health_and_post_health_recheck_all_required(self) -> None:
        for pid, port in (("999", "8766"), ("42", "8765"), ("42", "bad")):
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
        with self.assertRaisesRegex(window.LaunchError, "Port 8766"):
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
                                         SimpleAction=SimpleNamespace(new=lambda *_args: Widget()))
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
        with self.assertRaisesRegex(window.LaunchError, "Port 8766"):
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
        self.assertEqual(self.start(retry=True), "http://127.0.0.1:8766/")
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


if __name__ == "__main__":
    unittest.main()
