"""Readiness and failure behavior for ``scripts/daemon.sh start``."""
from __future__ import annotations

import os
import signal
import subprocess
import textwrap
import time
from collections.abc import Iterator
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "daemon.sh"


@pytest.fixture
def fake_daemon_env(tmp_path: Path) -> Iterator[tuple[Path, dict[str, str]]]:
    home = tmp_path / "daemon-home"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    launch_marker = tmp_path / "daemon-launched"
    fake_daemon = tmp_path / "fake_daemon.py"
    fake_daemon.write_text(
        textwrap.dedent(
            """\
            import os
            import time
            from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
            from pathlib import Path

            home = Path(os.environ["HAPPYRANCH_DAEMON_HOME"])
            mode = os.environ["FAKE_DAEMON_MODE"]
            Path(os.environ["FAKE_LAUNCH_MARKER"]).write_text("launched")
            print(os.environ.get("FAKE_LOG_LINE", "fake daemon log line"), flush=True)

            if mode == "exit":
                raise SystemExit(17)

            home.mkdir(parents=True, exist_ok=True)
            (home / "daemon.pid").write_text(str(os.getpid()))

            if mode == "slow_health":
                time.sleep(float(os.environ["FAKE_START_DELAY"]))

                class Handler(BaseHTTPRequestHandler):
                    def do_GET(self):
                        if self.path == "/api/v1/health":
                            self.send_response(200)
                            self.send_header("Content-Type", "application/json")
                            self.end_headers()
                            self.wfile.write(b'{"status":"ok"}')
                        else:
                            self.send_response(404)
                            self.end_headers()

                    def log_message(self, format, *args):
                        pass

                server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                (home / "daemon.port").write_text(str(server.server_port))
                server.serve_forever()

            if mode == "never_healthy_with_port":
                (home / "daemon.port").write_text("1")

            time.sleep(600)
            """
        )
    )

    uv = bindir / "uv"
    uv.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            if [[ "${1:-}" == "--version" ]]; then
                echo "uv 0.12.5 (shim)"
                exit 0
            fi
            if [[ "${1:-}" == "run" && "${2:-}" == "python" && "${3:-}" == "--version" ]]; then
                echo "Python 3.14.4"
                exit 0
            fi
            if [[ "${1:-}" == "run" && "${2:-}" == "python" && "${3:-}" == "-c" ]]; then
                echo "${FAKE_BIND_HOST:-127.0.0.1}"
                exit 0
            fi
            if [[ "${1:-}" == "run" && "${2:-}" == "python" && "${3:-}" == "-m" && "${4:-}" == "runtime.daemon" ]]; then
                exec /usr/bin/python3 "$FAKE_DAEMON_SCRIPT"
            fi
            echo "unexpected uv invocation: $*" >&2
            exit 2
            """
        )
    )
    uv.chmod(0o755)

    env = {
        **os.environ,
        "HAPPYRANCH_DAEMON_HOME": str(home),
        "PATH": f"{bindir}:/usr/bin:/bin",
        "FAKE_DAEMON_SCRIPT": str(fake_daemon),
        "FAKE_LAUNCH_MARKER": str(launch_marker),
        "FAKE_LOG_LINE": "fake startup diagnostic",
    }
    try:
        yield home, env
    finally:
        pid_file = home / "daemon.pid"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, signal.SIGTERM)
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.05)
                else:
                    os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass


def _run_start(env: dict[str, str]) -> tuple[subprocess.CompletedProcess[str], float]:
    started = time.monotonic()
    result = subprocess.run(
        [str(_SCRIPT), "start"],
        env=env,
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result, time.monotonic() - started


def test_slow_start_beyond_five_seconds_waits_for_health(
    fake_daemon_env: tuple[Path, dict[str, str]],
) -> None:
    home, env = fake_daemon_env
    env.update(
        FAKE_DAEMON_MODE="slow_health",
        FAKE_START_DELAY="5.25",
        HAPPYRANCH_DAEMON_START_TIMEOUT="8",
    )

    result, elapsed = _run_start(env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert elapsed >= 5
    port = (home / "daemon.port").read_text().strip()
    pid = (home / "daemon.pid").read_text().strip()
    assert result.stdout.strip() == f"daemon started (pid {pid}, port {port})"


def test_process_exit_fails_fast_and_prints_log_tail(
    fake_daemon_env: tuple[Path, dict[str, str]],
) -> None:
    _, env = fake_daemon_env
    env.update(
        FAKE_DAEMON_MODE="exit",
        HAPPYRANCH_DAEMON_START_TIMEOUT="8",
    )

    result, elapsed = _run_start(env)

    assert result.returncode == 1
    assert elapsed < 2
    assert "daemon exited during startup" in result.stderr
    assert "fake startup diagnostic" in result.stderr
    assert "daemon started" not in result.stdout


def test_unhealthy_daemon_times_out_and_prints_log_tail(
    fake_daemon_env: tuple[Path, dict[str, str]],
) -> None:
    _, env = fake_daemon_env
    env.update(
        FAKE_DAEMON_MODE="never_healthy_with_port",
        HAPPYRANCH_DAEMON_START_TIMEOUT="1",
    )

    result, elapsed = _run_start(env)

    assert result.returncode == 1
    assert elapsed < 3
    assert "daemon failed to start within 1s" in result.stderr
    assert "fake startup diagnostic" in result.stderr
    assert "daemon started" not in result.stdout


def test_stale_port_file_does_not_count_as_success(
    fake_daemon_env: tuple[Path, dict[str, str]],
) -> None:
    home, env = fake_daemon_env
    home.mkdir(parents=True)
    (home / "daemon.port").write_text("39123")
    env.update(
        FAKE_DAEMON_MODE="never_healthy",
        HAPPYRANCH_DAEMON_START_TIMEOUT="1",
    )

    result, _ = _run_start(env)

    assert result.returncode == 1
    assert "daemon failed to start within 1s" in result.stderr
    assert "daemon started" not in result.stdout
    assert not (home / "daemon.port").exists()


@pytest.mark.parametrize("value", ["0", "abc"])
def test_invalid_timeout_refuses_before_launch(
    fake_daemon_env: tuple[Path, dict[str, str]],
    value: str,
) -> None:
    home, env = fake_daemon_env
    env.update(
        FAKE_DAEMON_MODE="never_healthy",
        HAPPYRANCH_DAEMON_START_TIMEOUT=value,
    )

    result, elapsed = _run_start(env)

    assert result.returncode == 1
    assert elapsed < 1
    assert "HAPPYRANCH_DAEMON_START_TIMEOUT must be a positive integer" in result.stderr
    assert not Path(env["FAKE_LAUNCH_MARKER"]).exists()
    assert not home.exists()
