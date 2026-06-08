from __future__ import annotations

import os
from pathlib import Path

from runtime.config import Settings
from runtime.daemon.assistant_pty import (
    PROBE_READY,
    PROBE_REQUEST,
    InteractiveExecutorSpec,
    ProbeRunner,
    build_executor_specs,
)


def _write_fake_cli(tmp_path: Path, body: str, name: str = "fake-cli") -> Path:
    path = tmp_path / name
    path.write_text("#!/usr/bin/env python3\n" + body)
    path.chmod(path.stat().st_mode | 0o111)
    return path


def test_probe_passes_when_marker_returned(tmp_path: Path) -> None:
    cli = _write_fake_cli(
        tmp_path,
        f"""
import sys

seen = sys.stdin.readline()
if {PROBE_REQUEST!r} not in seen:
    raise SystemExit(1)
nonce = seen.strip().split(maxsplit=1)[1]
print(f"{PROBE_READY} {{nonce}}", flush=True)
""",
    )
    spec = InteractiveExecutorSpec(
        name="fake",
        argv=[str(cli)],
        prompt_surface="AGENTS.md",
    )

    result = ProbeRunner().probe_executor(spec)

    assert result.passed is True
    assert result.executor == "fake"
    assert PROBE_READY in result.output_excerpt
    assert result.error is None
    assert result.elapsed_seconds >= 0


def test_probe_fails_on_wrong_marker(tmp_path: Path) -> None:
    cli = _write_fake_cli(
        tmp_path,
        f"""
import sys

sys.stdin.readline()
print("NOT_READY", flush=True)
""",
    )
    spec = InteractiveExecutorSpec(
        name="fake",
        argv=[str(cli)],
        prompt_surface="AGENTS.md",
    )

    result = ProbeRunner().probe_executor(spec)

    assert result.passed is False
    assert result.executor == "fake"
    assert "expected ready marker not found" in result.detail
    assert "NOT_READY" in result.output_excerpt


def test_probe_ignores_standalone_startup_ready_marker(tmp_path: Path) -> None:
    cli = _write_fake_cli(
        tmp_path,
        f"""
import sys

print({PROBE_READY!r}, flush=True)
sys.stdin.readline()
print("NOT_READY", flush=True)
""",
    )
    spec = InteractiveExecutorSpec(
        name="fake",
        argv=[str(cli)],
        prompt_surface="AGENTS.md",
    )

    result = ProbeRunner().probe_executor(spec)

    assert result.passed is False
    assert result.detail == "expected ready marker not found"
    assert PROBE_READY in result.output_excerpt
    assert "NOT_READY" in result.output_excerpt


def test_probe_rejects_startup_ready_marker_with_wrong_nonce(tmp_path: Path) -> None:
    cli = _write_fake_cli(
        tmp_path,
        f"""
import sys

print("{PROBE_READY} stale-nonce", flush=True)
sys.stdin.readline()
print("NOT_READY", flush=True)
""",
    )
    spec = InteractiveExecutorSpec(
        name="fake",
        argv=[str(cli)],
        prompt_surface="AGENTS.md",
    )

    result = ProbeRunner().probe_executor(spec)

    assert result.passed is False
    assert result.detail == "expected ready marker not found"
    assert f"{PROBE_READY} stale-nonce" in result.output_excerpt


def test_probe_writes_minimal_workspace_surface(tmp_path: Path) -> None:
    marker_path = tmp_path / "surface.txt"
    cli = _write_fake_cli(
        tmp_path,
        f"""
from pathlib import Path
import os
import sys

surface = Path("CLAUDE.md")
content = surface.read_text()
Path(os.environ["SURFACE_MARKER_PATH"]).write_text(
    f"{{surface.exists()}}\\n{{content}}"
)
request = sys.stdin.readline().strip()
nonce = request.split(maxsplit=1)[1]
print(f"{PROBE_READY} {{nonce}}", flush=True)
""",
    )
    spec = InteractiveExecutorSpec(
        name="claude",
        argv=[str(cli)],
        prompt_surface="CLAUDE.md",
        env={"SURFACE_MARKER_PATH": str(marker_path)},
    )

    result = ProbeRunner().probe_executor(spec)

    assert result.passed is True
    surface = marker_path.read_text()
    assert surface.startswith("True\n")
    assert PROBE_REQUEST in surface
    assert PROBE_READY in surface


def test_build_executor_specs_uses_settings_paths() -> None:
    settings = Settings(
        claude_cli_path="/bin/claude-test",
        codex_cli_path="/bin/codex-test",
        opencode_cli_path="/bin/opencode-test",
        pi_cli_path="/bin/pi-test",
    )

    specs = build_executor_specs(settings)

    by_name = {spec.name: spec for spec in specs}
    assert by_name["claude"].argv == ["/bin/claude-test"]
    assert by_name["claude"].prompt_surface == "CLAUDE.md"
    assert by_name["codex"].argv == ["/bin/codex-test"]
    assert by_name["codex"].prompt_surface == "AGENTS.md"
    assert by_name["opencode"].argv == ["/bin/opencode-test"]
    assert by_name["opencode"].prompt_surface == "AGENTS.md"
    assert by_name["pi"].argv == ["/bin/pi-test"]
    assert by_name["pi"].prompt_surface == "AGENTS.md"


def test_probe_returns_failure_for_missing_executable(tmp_path: Path) -> None:
    spec = InteractiveExecutorSpec(
        name="missing",
        argv=[str(tmp_path / "does-not-exist")],
        prompt_surface="AGENTS.md",
    )

    result = ProbeRunner().probe_executor(spec, timeout_seconds=1)

    assert result.passed is False
    assert result.error == "launch_error"
    assert "does-not-exist" in result.detail


def test_probe_times_out_and_cleans_up_blocked_child(tmp_path: Path) -> None:
    pid_path = tmp_path / "child.pid"
    cli = _write_fake_cli(
        tmp_path,
        """
from pathlib import Path
import os
import time

Path(os.environ["PID_PATH"]).write_text(str(os.getpid()))
time.sleep(30)
""",
    )
    spec = InteractiveExecutorSpec(
        name="blocked",
        argv=[str(cli)],
        prompt_surface="AGENTS.md",
        env={"PID_PATH": str(pid_path)},
    )

    result = ProbeRunner().probe_executor(spec, timeout_seconds=1)

    assert result.passed is False
    assert result.timed_out is True
    child_pid = int(pid_path.read_text())
    try:
        os.kill(child_pid, 0)
    except ProcessLookupError:
        pass
    else:
        raise AssertionError(f"child process {child_pid} was not cleaned up")
