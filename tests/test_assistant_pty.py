from __future__ import annotations

import asyncio
import os
from pathlib import Path
import time

import pytest

from runtime.config import Settings
from runtime.daemon.assistant_pty import (
    AssistantPtySession,
    AssistantSessionManager,
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


async def _read_until_terminal(
    queue: asyncio.Queue[str | None],
    *,
    timeout_seconds: float = 2,
) -> list[str]:
    output: list[str] = []
    while True:
        item = await asyncio.wait_for(queue.get(), timeout=timeout_seconds)
        if item is None:
            return output
        output.append(item)


def _wait_for_dead(pid: int, *, timeout_seconds: float = 2) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.01)
    raise AssertionError(f"process {pid} is still alive")


def _wait_for_pid_file(path: Path, *, timeout_seconds: float = 2) -> int:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if path.exists():
            return int(path.read_text())
        time.sleep(0.01)
    raise AssertionError(f"pid file {path} was not written")


def _wait_for_session_stopped(
    session: AssistantPtySession,
    *,
    timeout_seconds: float = 2,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not session.is_running():
            return
        time.sleep(0.01)
    raise AssertionError("assistant session leader is still running")


@pytest.mark.asyncio
async def test_assistant_session_marks_closed_after_natural_exit(tmp_path: Path) -> None:
    cli = _write_fake_cli(
        tmp_path,
        """
print("one-shot ready", flush=True)
""",
    )
    session = AssistantPtySession(command=str(cli), workspace=tmp_path)
    await session.start()
    queue = session.subscribe()

    output = await _read_until_terminal(queue)

    assert "one-shot ready" in "".join(output)
    assert session.is_running() is False
    assert session.master_fd is None


@pytest.mark.asyncio
async def test_assistant_session_manager_replaces_exited_session(tmp_path: Path) -> None:
    pid_log = tmp_path / "pid.log"
    cli = _write_fake_cli(
        tmp_path,
        f"""
from pathlib import Path
import os

with Path({str(pid_log)!r}).open("a") as fh:
    fh.write(str(os.getpid()) + "\\n")
print("short lived", flush=True)
""",
    )
    manager = AssistantSessionManager()
    session1 = await manager.get_or_start(command=str(cli), workspace=tmp_path)
    output1 = await _read_until_terminal(session1.subscribe())

    session2 = await manager.get_or_start(command=str(cli), workspace=tmp_path)
    output2 = await _read_until_terminal(session2.subscribe())

    await manager.close_all()
    pids = [int(line) for line in pid_log.read_text().splitlines()]
    assert session2 is not session1
    assert output1 == ["short lived\r\n"]
    assert output2 == ["short lived\r\n"]
    assert len(pids) == 2
    assert pids[0] != pids[1]


@pytest.mark.asyncio
async def test_assistant_session_manager_replacement_cleans_old_process_group(
    tmp_path: Path,
) -> None:
    helper_pid_path = tmp_path / "helper.pid"
    cli = _write_fake_cli(
        tmp_path,
        f"""
from pathlib import Path
import os
import subprocess
import sys
import time

subprocess.Popen(
    [
        sys.executable,
        "-c",
        (
            "from pathlib import Path; "
            "import os, signal, time; "
            "signal.signal(signal.SIGHUP, signal.SIG_IGN); "
            f"Path({str(helper_pid_path)!r}).write_text(str(os.getpid())); "
            "time.sleep(30)"
        ),
    ]
)
for _ in range(200):
    if Path({str(helper_pid_path)!r}).exists():
        break
    time.sleep(0.01)
print("leader exiting", flush=True)
""",
    )
    manager = AssistantSessionManager()
    session1 = await manager.get_or_start(command=str(cli), workspace=tmp_path)
    first_output = await asyncio.wait_for(session1.subscribe().get(), timeout=2)
    helper_pid = _wait_for_pid_file(helper_pid_path)
    _wait_for_session_stopped(session1)

    session2 = await manager.get_or_start(command=str(cli), workspace=tmp_path)
    await manager.close_all()

    assert first_output == "leader exiting\r\n"
    assert session2 is not session1
    _wait_for_dead(helper_pid)


@pytest.mark.asyncio
async def test_assistant_session_close_terminates_running_process(tmp_path: Path) -> None:
    pid_path = tmp_path / "assistant.pid"
    cli = _write_fake_cli(
        tmp_path,
        f"""
from pathlib import Path
import os
import time

Path({str(pid_path)!r}).write_text(str(os.getpid()))
print("running", flush=True)
time.sleep(30)
""",
    )
    session = AssistantPtySession(command=str(cli), workspace=tmp_path)
    await session.start()
    pid = _wait_for_pid_file(pid_path)

    await session.close()

    _wait_for_dead(pid)
    assert session.is_running() is False
    assert session.master_fd is None


@pytest.mark.asyncio
async def test_assistant_session_provides_controlling_terminal(tmp_path: Path) -> None:
    cli = _write_fake_cli(
        tmp_path,
        """
import os

try:
    foreground_pgrp = os.tcgetpgrp(0)
except OSError as exc:
    print(f"ctty failed: {exc.errno}", flush=True)
else:
    print(f"ctty ok: {foreground_pgrp == os.getpgrp()}", flush=True)
""",
    )
    session = AssistantPtySession(command=str(cli), workspace=tmp_path)
    await session.start()
    output = await _read_until_terminal(session.subscribe())

    assert output == ["ctty ok: True\r\n"]
    assert session.is_running() is False
    assert session.master_fd is None


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


def test_probe_cleans_up_successful_live_executor(tmp_path: Path) -> None:
    pid_path = tmp_path / "live.pid"
    cli = _write_fake_cli(
        tmp_path,
        f"""
from pathlib import Path
import os
import signal
import sys
import time

signal.signal(signal.SIGHUP, signal.SIG_IGN)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
request = sys.stdin.readline().strip()
nonce = request.split(maxsplit=1)[1]
Path(os.environ["PID_PATH"]).write_text(str(os.getpid()))
print(f"{PROBE_READY} {{nonce}}", flush=True)
time.sleep(30)
""",
    )
    spec = InteractiveExecutorSpec(
        name="live",
        argv=[str(cli)],
        prompt_surface="AGENTS.md",
        env={"PID_PATH": str(pid_path)},
    )

    result = ProbeRunner().probe_executor(spec)

    assert result.passed is True
    child_pid = int(pid_path.read_text())
    deadline = time.monotonic() + 1
    child_alive = True
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            child_alive = False
            break
        time.sleep(0.01)
    if child_alive:
        raise AssertionError(f"successful process {child_pid} was not cleaned up")


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


def test_probe_cleans_up_helper_after_failed_leader_exit(tmp_path: Path) -> None:
    pid_path = tmp_path / "helper.pid"
    cli = _write_fake_cli(
        tmp_path,
        """
from pathlib import Path
import os
import subprocess
import sys

helper = subprocess.Popen(
    [
        sys.executable,
        "-c",
        (
            "from pathlib import Path; "
            "import os, signal, time; "
            "signal.signal(signal.SIGHUP, signal.SIG_IGN); "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "Path(os.environ['PID_PATH']).write_text(str(os.getpid())); "
            "time.sleep(30)"
        ),
    ],
    env=os.environ.copy(),
)
while not Path(os.environ["PID_PATH"]).exists():
    pass
sys.stdin.readline()
print("NOT_READY", flush=True)
raise SystemExit(1)
""",
    )
    spec = InteractiveExecutorSpec(
        name="helper",
        argv=[str(cli)],
        prompt_surface="AGENTS.md",
        env={"PID_PATH": str(pid_path)},
    )

    result = ProbeRunner().probe_executor(spec, timeout_seconds=5)

    assert result.passed is False
    assert result.returncode == 1
    helper_pid = int(pid_path.read_text())
    deadline = time.monotonic() + 1
    helper_alive = True
    while time.monotonic() < deadline:
        try:
            os.kill(helper_pid, 0)
        except ProcessLookupError:
            helper_alive = False
            break
        time.sleep(0.01)
    if helper_alive:
        raise AssertionError(f"helper process {helper_pid} was not cleaned up")


def test_probe_fails_when_ready_marker_followed_by_nonzero_exit(tmp_path: Path) -> None:
    cli = _write_fake_cli(
        tmp_path,
        f"""
import sys

request = sys.stdin.readline().strip()
nonce = request.split(maxsplit=1)[1]
print(f"{PROBE_READY} {{nonce}}", flush=True)
raise SystemExit(2)
""",
    )
    spec = InteractiveExecutorSpec(
        name="nonzero",
        argv=[str(cli)],
        prompt_surface="AGENTS.md",
    )

    result = ProbeRunner().probe_executor(spec)

    assert result.passed is False
    assert result.returncode == 2
    assert result.error == "nonzero_exit"


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


def test_probe_rejects_prompt_dump_containing_ready_instructions(tmp_path: Path) -> None:
    cli = _write_fake_cli(
        tmp_path,
        """
from pathlib import Path
import sys

print(Path("AGENTS.md").read_text(), flush=True)
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
    assert "NOT_READY" in result.output_excerpt


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
request = sys.stdin.readline().strip()
Path(os.environ["SURFACE_MARKER_PATH"]).write_text(
    f"{{surface.exists()}}\\n{{request}}\\n---\\n{{content}}"
)
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
    surface_exists, request, content = marker_path.read_text().split("\n", 2)
    assert surface_exists == "True"
    nonce = request.split(maxsplit=1)[1]
    assert PROBE_REQUEST in content
    assert PROBE_READY in content
    assert request not in content
    assert f"{PROBE_READY} {nonce}" not in content


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


def test_probe_reports_exec_failure_after_path_resolution(tmp_path: Path) -> None:
    bad_executable = tmp_path / "bad-cli"
    bad_executable.write_text("not a script\n")
    bad_executable.chmod(bad_executable.stat().st_mode | 0o111)
    spec = InteractiveExecutorSpec(
        name="bad",
        argv=[str(bad_executable)],
        prompt_surface="AGENTS.md",
    )

    result = ProbeRunner().probe_executor(spec, timeout_seconds=1)

    assert result.passed is False
    assert result.error == "launch_error"
    assert result.returncode == 127
    assert "failed to exec" in result.detail


def test_probe_times_out_and_cleans_up_blocked_child(tmp_path: Path) -> None:
    pid_path = tmp_path / "child.pid"
    cli = _write_fake_cli(
        tmp_path,
        """
from pathlib import Path
import os
import time

request = input()
if not request.startswith("HAPPYRANCH_ASSISTANT_PTY_PROBE_REQUEST "):
    raise SystemExit(2)
Path(os.environ["PID_PATH"]).write_text(str(os.getpid()))
print("STARTED", flush=True)
time.sleep(30)
""",
    )
    spec = InteractiveExecutorSpec(
        name="blocked",
        argv=[str(cli)],
        prompt_surface="AGENTS.md",
        env={"PID_PATH": str(pid_path)},
    )

    result = ProbeRunner().probe_executor(spec, timeout_seconds=5)

    assert result.passed is False
    assert result.timed_out is True
    assert "STARTED" in result.output_excerpt
    child_pid = int(pid_path.read_text())
    deadline = time.monotonic() + 1
    child_alive = True
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            child_alive = False
            break
        time.sleep(0.01)
    if child_alive:
        raise AssertionError(f"child process {child_pid} was not cleaned up")
