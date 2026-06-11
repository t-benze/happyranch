from __future__ import annotations

import asyncio
import os
from pathlib import Path
import time

import pytest

from runtime.daemon.assistant_pty import (
    AssistantPtySession,
    AssistantSessionManager,
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

