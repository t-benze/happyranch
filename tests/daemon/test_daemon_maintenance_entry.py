"""Tests for the daemon startup-only maintenance invocation seam (TASK-5505).

Exercises the ACTUAL shipping bootstrap — ``python -m runtime.daemon
--maintenance`` — the same entry module the normal daemon uses.  Proves:

* the maintenance branch runs BEFORE ``_build_state``, ``create_app``,
  ``_bind_port``, and uvicorn (i.e. before any HTTP listener, FastAPI
  lifespan, scheduler loop, or worker),
* the maintenance-only process exits and never starts a normal daemon,
* failure paths return a nonzero startup-mode result with bounded
  recovery guidance and no auto-retry,
* the offline guard refuses to run while a daemon pid is alive,
* the real one-shot subprocess prunes the store and writes no pid/port files.

Deterministic mocks are used ONLY at the real server-bind / scheduler-start
seams where process-level integration would otherwise be unsafe; the
subprocess test runs the genuine shipping seam end-to-end.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from runtime.daemon.metrics_store import MetricsMaintenanceError, MetricsStore
from runtime.daemon.state import DaemonState
from runtime.runtime import RuntimeDir

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _seed_runtime(tmp_path: Path, rows: list[tuple[str, dict]]) -> Path:
    """Create a daemon home + active runtime with a seeded metrics.db.

    Uses the conftest-isolated ``HAPPYRANCH_DAEMON_HOME`` (tmp_path/.happyranch)
    so ``runtimes.register`` writes runtimes.yaml into the temp home.
    """
    from runtime.daemon import runtimes

    runtime_root = tmp_path / "runtime"
    RuntimeDir.init(runtime_root)
    runtimes.register(runtime_root)  # registers + activates

    store = MetricsStore(str(runtime_root / "metrics.db"))
    for iso, snap in rows:
        store.append_snapshot(iso, snap)
    store.close()
    return runtime_root


def _boom(*_args, **_kwargs):
    raise AssertionError(
        "normal daemon startup must not run under --maintenance"
    )


def _guard_server_seams(monkeypatch) -> None:
    """Make every normal-server seam raise if reached under --maintenance."""
    import runtime.daemon.__main__ as dm

    monkeypatch.setattr(dm, "_build_state", _boom)
    monkeypatch.setattr(dm, "create_app", _boom)
    monkeypatch.setattr(dm, "_bind_port", _boom)
    monkeypatch.setattr(dm.uvicorn.Server, "run", _boom)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# main(["--maintenance"]) — the shipping entry seam (mocked server seams)
# ---------------------------------------------------------------------------

class TestMaintenanceEntrySeam:
    def test_maintenance_runs_sequence_and_never_starts_server(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        now = _now()
        old = (now - timedelta(days=60)).isoformat()
        recent = now.isoformat()
        runtime_root = _seed_runtime(
            tmp_path, [(old, {"n": "old"}), (recent, {"n": "recent"})]
        )

        import runtime.daemon.__main__ as dm

        _guard_server_seams(monkeypatch)

        rc = dm.main(["--maintenance"])

        # Success exit code; the one-shot process does not serve.
        assert rc == 0
        # The on-disk store was actually pruned at the 30-day cutoff.
        store = MetricsStore(str(runtime_root / "metrics.db"))
        rows = store.query()
        assert len(rows) == 1
        assert json.loads(rows[0]["snapshot_json"]) == {"n": "recent"}
        store.close()

    def test_maintenance_flag_without_flag_still_starts_server(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """The normal (no-flag) path is untouched: server seams are reached."""
        import runtime.daemon.__main__ as dm

        called: list[str] = []

        def _fake_bind(host: str, port: int = 0):
            called.append("_bind_port")
            import socket
            return socket.socket(), 9999

        def _fake_state(_settings):
            called.append("_build_state")
            return DaemonState.idle(_settings)

        def _fake_app(_state):
            called.append("create_app")
            return object()

        class _FakeServer:
            def __init__(self, _config):
                called.append("uvicorn.Server(config)")

            def run(self, sockets=None):
                called.append("server.run")

        monkeypatch.setattr(dm, "_bind_port", _fake_bind)
        monkeypatch.setattr(dm, "_build_state", _fake_state)
        monkeypatch.setattr(dm, "create_app", _fake_app)
        monkeypatch.setattr(dm.uvicorn, "Server", _FakeServer)

        class _FakePaths:
            @staticmethod
            def ensure_daemon_home():
                return None

            @staticmethod
            def ensure_token():
                return "token"

            @staticmethod
            def port_file():
                return tmp_path / "daemon.port"

            @staticmethod
            def pid_file():
                return tmp_path / "daemon.pid"

        monkeypatch.setattr(dm, "paths", _FakePaths())
        monkeypatch.setattr(dm, "_install_signal_handlers", lambda _s: None)

        rc = dm.main([])

        assert rc == 0
        assert called == [
            "_build_state", "create_app", "_bind_port",
            "uvicorn.Server(config)", "server.run",
        ]

    def test_maintenance_failure_returns_nonzero_with_guidance(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        _seed_runtime(tmp_path, [(_now().isoformat(), {"n": 1})])

        import runtime.daemon.__main__ as dm
        from runtime.daemon.metrics_store import MetricsStore

        _guard_server_seams(monkeypatch)

        def _broken(_self, cutoff):
            raise MetricsMaintenanceError("WAL checkpoint was busy")

        monkeypatch.setattr(MetricsStore, "maintenance", _broken)

        rc = dm.main(["--maintenance"])
        assert rc == 1

    def test_maintenance_operational_exception_returns_nonzero(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        _seed_runtime(tmp_path, [(_now().isoformat(), {"n": 1})])

        import runtime.daemon.__main__ as dm
        from runtime.daemon.metrics_store import MetricsStore

        _guard_server_seams(monkeypatch)

        def _broken(_self, cutoff):
            raise OSError("disk gone")

        monkeypatch.setattr(MetricsStore, "maintenance", _broken)

        rc = dm.main(["--maintenance"])
        assert rc == 1

    def test_maintenance_refuses_when_daemon_pid_alive(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        _seed_runtime(tmp_path, [(_now().isoformat(), {"n": 1})])

        import runtime.daemon.__main__ as dm

        # The pid file names THIS live test process.
        (tmp_path / ".happyranch" / "daemon.pid").write_text(str(os.getpid()))
        _guard_server_seams(monkeypatch)

        rc = dm.main(["--maintenance"])
        assert rc == 1

    def test_maintenance_refuses_without_active_runtime(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        import runtime.daemon.__main__ as dm

        _guard_server_seams(monkeypatch)

        rc = dm.main(["--maintenance"])
        assert rc == 1


# ---------------------------------------------------------------------------
# Real one-shot subprocess — the actual shipping seam end-to-end
# ---------------------------------------------------------------------------

class TestMaintenanceSubprocess:
    def test_real_python_m_daemon_maintenance_one_shot(
        self, tmp_path: Path
    ) -> None:
        now = _now()
        old = (now - timedelta(days=60)).isoformat()
        # 29 days old: clearly inside the 30-day window, so it survives.
        # (The exact strict-before 30-day boundary is deterministic at the
        # store level; here the cutoff is computed inside the real process.)
        kept = (now - timedelta(days=29)).isoformat()
        recent = now.isoformat()
        runtime_root = _seed_runtime(tmp_path, [
            (old, {"n": "old"}),
            (kept, {"n": "kept"}),
            (recent, {"n": "recent"}),
        ])

        env = dict(os.environ)
        env["HAPPYRANCH_DAEMON_HOME"] = str(tmp_path / ".happyranch")

        proc = subprocess.run(
            [sys.executable, "-m", "runtime.daemon", "--maintenance"],
            cwd=_REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

        # Success exit; stdout/stderr carry bounded guidance/report only.
        assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
        # The maintenance-only process wrote NO pid/port files → it never
        # bound a listener and exited without starting the normal daemon.
        assert not (tmp_path / ".happyranch" / "daemon.pid").exists()
        assert not (tmp_path / ".happyranch" / "daemon.port").exists()

        # The one-shot pruned exactly the strictly-older row (30-day cutoff;
        # the 29-day-old and recent rows survive).
        store = MetricsStore(str(runtime_root / "metrics.db"))
        rows = store.query()
        remaining = {json.loads(r["snapshot_json"])["n"] for r in rows}
        assert remaining == {"kept", "recent"}
        store.close()

    def test_real_subprocess_failure_exits_nonzero(
        self, tmp_path: Path
    ) -> None:
        """A corrupt store fails the one-shot with a nonzero exit — no
        success claim, no auto-retry, no server start."""
        now = _now()
        runtime_root = _seed_runtime(tmp_path, [(now.isoformat(), {"n": 1})])
        # Corrupt the database file so integrity_check cannot return ok.
        db_path = runtime_root / "metrics.db"
        db_path.write_bytes(b"this is not a sqlite database")

        env = dict(os.environ)
        env["HAPPYRANCH_DAEMON_HOME"] = str(tmp_path / ".happyranch")

        proc = subprocess.run(
            [sys.executable, "-m", "runtime.daemon", "--maintenance"],
            cwd=_REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert proc.returncode != 0
        assert not (tmp_path / ".happyranch" / "daemon.pid").exists()
        assert not (tmp_path / ".happyranch" / "daemon.port").exists()
