"""Unit tests for src/daemon/scripts_runner.py (spec §6)."""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_paths():
    d = Path(tempfile.mkdtemp())
    yield {
        "cwd": d / "cwd",
        "stdout": d / "out.log",
        "stderr": d / "err.log",
    }


def test_run_script_captures_stdout_and_exit_zero(tmp_paths):
    from src.daemon.scripts_runner import run_script
    tmp_paths["cwd"].mkdir()
    result = asyncio.run(run_script(
        script_text="echo hello",
        interpreter="bash",
        cwd=str(tmp_paths["cwd"]),
        stdout_path=str(tmp_paths["stdout"]),
        stderr_path=str(tmp_paths["stderr"]),
        timeout_seconds=10,
        publish=lambda evt: None,
    ))
    assert result.exit_code == 0
    assert result.status == "completed"
    assert result.duration_ms >= 0
    assert "hello" in tmp_paths["stdout"].read_text()


def test_run_script_captures_stderr_and_nonzero_exit(tmp_paths):
    from src.daemon.scripts_runner import run_script
    tmp_paths["cwd"].mkdir()
    result = asyncio.run(run_script(
        script_text="echo oops >&2; exit 7",
        interpreter="bash",
        cwd=str(tmp_paths["cwd"]),
        stdout_path=str(tmp_paths["stdout"]),
        stderr_path=str(tmp_paths["stderr"]),
        timeout_seconds=10,
        publish=lambda evt: None,
    ))
    assert result.exit_code == 7
    assert result.status == "completed"  # natural exit, even non-zero
    assert "oops" in tmp_paths["stderr"].read_text()


def test_run_script_publishes_line_events(tmp_paths):
    from src.daemon.scripts_runner import run_script
    tmp_paths["cwd"].mkdir()
    events: list[dict] = []
    asyncio.run(run_script(
        script_text="echo one; echo two; echo three >&2",
        interpreter="bash",
        cwd=str(tmp_paths["cwd"]),
        stdout_path=str(tmp_paths["stdout"]),
        stderr_path=str(tmp_paths["stderr"]),
        timeout_seconds=10,
        publish=lambda evt: events.append(evt),
    ))
    kinds = [(e["stream"], e["line"]) for e in events if e.get("kind") == "line"]
    assert ("stdout", "one") in kinds
    assert ("stdout", "two") in kinds
    assert ("stderr", "three") in kinds
    # Terminal event always last.
    assert events[-1]["kind"] == "terminal"
