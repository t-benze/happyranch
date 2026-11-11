"""Run directly with system Python; no display, GI, or HappyRanch imports."""
from __future__ import annotations

import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
from typing import Iterator
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

    def test_home_uses_absolute_xdg_or_private_default(self) -> None:
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(self.home),
                                     "HAPPYRANCH_DAEMON_HOME": "/external"}):
            self.assertEqual(window.desktop_home(), self.home / "happyranch")
        for value in ("", "relative"):
            with self.subTest(value=value), patch.dict(os.environ, {"XDG_DATA_HOME": value}):
                self.assertEqual(window.desktop_home(), Path.home() / ".local/share/happyranch")

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

    def test_stop_terms_and_reaps_only_owned_child_and_clears_markers(self) -> None:
        external = self.spawn()
        for publication in ("complete", "port-only", "empty-port", "finishing-port"):
            with self.subTest(publication=publication):
                home = self.home / publication
                home.mkdir()
                window.prepare_home(home)
                # The ready pipe synchronizes with the real child's port write,
                # before the daemon would publish its PID.
                port = "" if publication in ("empty-port", "finishing-port") else "54321"
                body = f"p = pathlib.Path({str(home / 'daemon.port')!r})\np.write_text({port!r})"
                if publication == "finishing-port":
                    body += ("\ndef finish_port(*_args):\n"
                             "    p.write_text('54321')\n    raise SystemExit(0)\n"
                             "signal.signal(signal.SIGTERM, finish_port)")
                child = self.spawn(body)
                if publication == "complete":
                    (home / "daemon.pid").write_text(str(child.pid))
                self.assertEqual((home / "daemon.port").read_text(), port)
                self.assertEqual((home / "daemon.pid").exists(), publication == "complete")
                window.stop_child(child, home, 0.5, 0.5, markers_absent_at_spawn=True)
                self.assertEqual(child.returncode, 0 if publication == "finishing-port"
                                 else -signal.SIGTERM)
                with self.assertRaises(ChildProcessError):
                    os.waitpid(child.pid, os.WNOHANG)
                self.assertIsNone(external.poll())
                self.assertFalse((home / "daemon.pid").exists())
                self.assertFalse((home / "daemon.port").exists())
                window.prepare_home(home)

    def test_stop_kills_term_resistant_child_then_reaps(self) -> None:
        child = self.spawn(ignore_term=True)
        self.publish(child.pid)
        window.stop_child(child, self.home, 0.03, 1)
        self.assertEqual(child.returncode, -signal.SIGKILL)
        with self.assertRaises(ChildProcessError):
            os.waitpid(child.pid, os.WNOHANG)
        self.assertFalse((self.home / "daemon.pid").exists())
        self.assertFalse((self.home / "daemon.port").exists())

    def test_stop_preserves_replaced_external_markers(self) -> None:
        window.prepare_home(self.home)
        child = self.spawn()
        external = self.spawn()
        self.publish(external.pid, "45678")
        window.stop_child(child, self.home, 0.5, 0.5, markers_absent_at_spawn=True)
        self.assertEqual(child.returncode, -signal.SIGTERM)
        self.assertIsNone(external.poll())
        self.assertTrue((self.home / "daemon.pid").exists())
        self.assertTrue((self.home / "daemon.port").exists())
        self.assertEqual((self.home / "daemon.pid").read_text(), str(external.pid))
        self.assertEqual((self.home / "daemon.port").read_text(), "45678")

        (self.home / "daemon.pid").unlink()
        child = self.spawn()
        window.stop_child(child, self.home, 0.5, 0.5)
        self.assertTrue((self.home / "daemon.port").exists())
        self.assertEqual((self.home / "daemon.port").read_text(), "45678")
        self.assertIsNone(external.poll())
        (self.home / "daemon.port").unlink()

        window.prepare_home(self.home)
        port_file = str(self.home / "daemon.port")
        child = self.spawn(
            f"p = pathlib.Path({port_file!r})\np.write_text('54321')\n"
            "def replace_port(*_args):\n"
            "    p.unlink()\n    p.write_text('45678')\n    raise SystemExit(0)\n"
            "signal.signal(signal.SIGTERM, replace_port)")
        window.stop_child(child, self.home, 0.5, 0.5, markers_absent_at_spawn=True)
        self.assertEqual(child.returncode, 0)
        self.assertTrue((self.home / "daemon.port").exists())
        self.assertEqual((self.home / "daemon.port").read_text(), "45678")
        self.assertIsNone(external.poll())

    def test_stop_reaps_already_exited_child_and_removes_owned_stale_state(self) -> None:
        for publication in ("complete", "port-only", "empty-port"):
            with self.subTest(publication=publication):
                home = self.home / publication
                home.mkdir()
                window.prepare_home(home)
                port = "" if publication == "empty-port" else "54321"
                child = self.spawn(
                    f"pathlib.Path({str(home / 'daemon.port')!r}).write_text({port!r})")
                if publication == "complete":
                    (home / "daemon.pid").write_text(str(child.pid))
                child.terminate()
                child.wait(timeout=2)
                window.stop_child(child, home, markers_absent_at_spawn=True)
                self.assertEqual(child.returncode, -signal.SIGTERM)
                with self.assertRaises(ChildProcessError):
                    os.waitpid(child.pid, os.WNOHANG)
                self.assertFalse((home / "daemon.pid").exists())
                self.assertFalse((home / "daemon.port").exists())
                window.prepare_home(home)


if __name__ == "__main__":
    unittest.main()
