"""Standalone daemon lock checks; no retired suite, provider or live runtime."""
from __future__ import annotations

import os
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.daemon.lifetime_lock import acquire_daemon_lock

ROOT = Path(__file__).resolve().parents[1]


class DaemonLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.env = {**os.environ, "HAPPYRANCH_DAEMON_HOME": str(self.home),
                    "HAPPYRANCH_DAEMON_PORT": "0"}

    def holder(self) -> subprocess.Popen:
        child = subprocess.Popen([sys.executable, "-B", "-c", """
import os, time
from pathlib import Path
from runtime.daemon.lifetime_lock import acquire_daemon_lock
held = acquire_daemon_lock(Path(os.environ['HAPPYRANCH_DAEMON_HOME']))
print('locked', flush=True)
time.sleep(30)
"""], cwd=ROOT, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.reap, child)
        self.assertEqual(child.stdout.readline().strip(), "locked")
        return child

    @staticmethod
    def reap(child: subprocess.Popen) -> None:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=3)

    def acquire(self):
        held = acquire_daemon_lock(self.home)
        self.addCleanup(os.close, held.fd)
        return held

    def test_second_main_refuses_before_any_startup_effect(self) -> None:
        self.holder()
        (self.home / "daemon.pid").write_text("12345")
        (self.home / "daemon.port").write_text("1234")
        result = subprocess.run([sys.executable, "-B", "-c", """
from pathlib import Path
from unittest.mock import patch
import runtime.daemon.__main__ as entry
def build(settings):
    (entry.paths.daemon_home() / 'build-reached').touch()
    raise AssertionError('startup reached before refusal')
with patch.object(entry, '_build_state', build):
    raise SystemExit(entry.main([]))
"""], cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("another daemon is already running on this runtime", result.stderr)
        self.assertIn(str(self.home / "daemon.lock"), result.stderr)
        self.assertIn("daemon.pid=12345", result.stderr)
        self.assertEqual(sorted(p.name for p in self.home.iterdir()),
                         ["daemon.lock", "daemon.pid", "daemon.port"])
        self.assertEqual((self.home / "daemon.pid").read_text(), "12345")
        self.assertEqual((self.home / "daemon.port").read_text(), "1234")

    def test_lock_released_after_holder_sigkill(self) -> None:
        child = self.holder()
        with self.assertRaises(OSError):
            acquire_daemon_lock(self.home)
        child.kill()
        child.wait(timeout=3)
        self.assertEqual(child.returncode, -signal.SIGKILL)
        held = self.acquire()
        self.assertFalse(os.get_inheritable(held.fd))

    def test_transition_proof_matrix(self) -> None:
        alive = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(self.reap, alive)
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait(timeout=3)
        cases = {"missing": None, "garbage": "nonsense", "zero": "0", "negative": "-1",
                 "own": str(os.getpid()), "alive": str(alive.pid), "dead": str(dead.pid),
                 "oversized": "1" * 65}
        for name, content in cases.items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as temp:
                home = Path(temp)
                if content is not None:
                    (home / "daemon.pid").write_text(content)
                held = acquire_daemon_lock(home)
                try:
                    self.assertEqual(held.proof.proven, name == "dead")
                    self.assertTrue(held.proof.transition_boot)
                    self.assertEqual(bool((home / "daemon.lock").read_bytes()), name == "dead")
                finally:
                    os.close(held.fd)

    def test_unreadable_and_ambiguous_pid_fail_closed(self) -> None:
        from runtime.daemon import lifetime_lock as locks
        real_open = os.open
        def unreadable(path, *args, **kwargs):
            if Path(path) == self.home / "daemon.pid":
                raise PermissionError("synthetic read refusal")
            return real_open(path, *args, **kwargs)
        (self.home / "daemon.pid").write_text("12345")
        with patch.object(locks.os, "open", side_effect=unreadable):
            held = acquire_daemon_lock(self.home)
        self.assertFalse(held.proof.proven)
        os.close(held.fd)
        with patch.object(locks.os, "kill", side_effect=PermissionError("synthetic EPERM")):
            held = acquire_daemon_lock(self.home)
        self.assertFalse(held.proof.proven)
        os.close(held.fd)

    def test_marker_never_launders_unproven_transition(self) -> None:
        for _ in range(2):
            held = acquire_daemon_lock(self.home)
            self.assertFalse(held.proof.proven)
            self.assertTrue(held.proof.transition_boot)
            os.close(held.fd)
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait(timeout=3)
        (self.home / "daemon.pid").write_text(str(child.pid))
        held = acquire_daemon_lock(self.home)
        self.assertTrue(held.proof.proven)
        os.close(held.fd)
        (self.home / "daemon.pid").unlink()
        held = self.acquire()
        self.assertTrue(held.proof.proven)
        self.assertFalse(held.proof.transition_boot)

    def test_lock_refuses_symlink_and_has_owner_only_permissions(self) -> None:
        target = self.home / "target"
        target.write_text("unchanged")
        lock = self.home / "daemon.lock"
        lock.symlink_to(target)
        with self.assertRaises(OSError):
            acquire_daemon_lock(self.home)
        self.assertEqual(target.read_text(), "unchanged")
        lock.unlink()
        held = self.acquire()
        self.assertEqual(stat.S_IMODE(os.fstat(held.fd).st_mode), 0o600)

    def test_real_main_holds_lock_before_build_and_through_error_tail(self) -> None:
        result = subprocess.run([sys.executable, "-B", "-c", """
import fcntl, os
from unittest.mock import patch
import runtime.daemon.__main__ as entry
def competing_acquire():
    fd = os.open(entry.paths.daemon_home() / 'daemon.lock', os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        raise AssertionError('main did not retain the lifetime lock')
    finally:
        os.close(fd)
def build(settings):
    competing_acquire()
    assert not entry.paths.pid_file().exists()
    assert not entry.paths.port_file().exists()
    raise SystemExit(73)
with patch.object(entry, '_build_state', build):
    try:
        entry.main([])
    except SystemExit as exc:
        assert exc.code == 73
        competing_acquire()
    else:
        raise AssertionError('build seam not reached')
print('ordering and error-tail lock observed')
"""], cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ordering and error-tail lock observed", result.stdout)

    def test_cleanup_helper_skips_daemon_lock(self) -> None:
        self.holder()
        result = subprocess.run([sys.executable, "-B", "-m", "runtime.daemon",
                                 "--cleanup-helper", "disk-sweep", "--help"],
                                cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--dry-run", result.stdout)
        self.assertFalse((self.home / "auth_token").exists())

    def test_maintenance_refuses_held_lock_without_pid(self) -> None:
        self.holder()
        result = subprocess.run([sys.executable, "-B", "-c", """
from unittest.mock import patch
import runtime.daemon.__main__ as entry
def registry():
    (entry.paths.daemon_home() / 'registry-reached').touch()
    raise AssertionError('offline DB preflight reached under another holder')
with patch.object(entry.runtimes, 'load', registry):
    raise SystemExit(entry.main(['--maintenance']))
"""], cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("FAILED (operational-error)", result.stderr)
        self.assertNotIn(str(self.home), result.stderr)
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), ["daemon.lock"])

    def test_shell_start_preserves_pid_and_reports_lock_refusal(self) -> None:
        self.holder()
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait(timeout=3)
        (self.home / "daemon.pid").write_text(str(dead.pid))
        (self.home / "daemon.port").write_text("1234")
        result = subprocess.run(["bash", "scripts/daemon.sh", "start"], cwd=ROOT,
                                env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("another daemon is already running on this runtime", result.stderr)
        self.assertIn(str(self.home / "daemon.lock"), result.stderr)
        self.assertTrue((self.home / "daemon.pid").exists())
        self.assertTrue((self.home / "daemon.port").exists())
        self.assertEqual((self.home / "daemon.pid").read_text(), str(dead.pid))
        self.assertEqual((self.home / "daemon.port").read_text(), "1234")
        self.assertFalse((self.home / "auth_token").exists())

    def test_shell_does_not_report_competing_daemon_ready(self) -> None:
        self.holder()
        bindir = self.home / "bin"
        bindir.mkdir()
        uv = bindir / "uv"
        uv.write_text(f"#!{sys.executable}\n" + """
import os, sys, time
from pathlib import Path
args = sys.argv[1:]
if args == ['--version']:
    print('uv test fixture')
elif args == ['run', 'python', '--version']:
    print('Python 3.13.15')
elif args[:3] == ['run', 'python', '-c']:
    print('127.0.0.1')
else:
    home = Path(os.environ['HAPPYRANCH_DAEMON_HOME'])
    (home / 'daemon.pid').write_text(os.environ['COMPETING_PID'])
    (home / 'daemon.port').write_text('1234')
    print('another daemon is already running on this runtime', file=sys.stderr, flush=True)
    time.sleep(1)
    sys.exit(1)
""")
        uv.chmod(0o700)
        curl = bindir / "curl"
        curl.write_text("#!/bin/sh\nexit 0\n")
        curl.chmod(0o700)
        result = subprocess.run(["bash", "scripts/daemon.sh", "start"], cwd=ROOT,
                                env={**self.env, "PATH": f"{bindir}:{os.environ['PATH']}",
                                     "COMPETING_PID": str(os.getpid())},
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("another daemon is already running", result.stderr)
        self.assertNotIn("daemon started", result.stdout)


if __name__ == "__main__":
    unittest.main()
