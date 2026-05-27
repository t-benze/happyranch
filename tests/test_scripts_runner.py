"""Unit tests for src/daemon/jobs_runner.py (spec §6)."""
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


def test_run_job_captures_stdout_and_exit_zero(tmp_paths):
    from src.daemon.jobs_runner import run_job
    tmp_paths["cwd"].mkdir()
    result = asyncio.run(run_job(
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


def test_run_job_captures_stderr_and_nonzero_exit(tmp_paths):
    from src.daemon.jobs_runner import run_job
    tmp_paths["cwd"].mkdir()
    result = asyncio.run(run_job(
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


def test_run_job_publishes_line_events(tmp_paths):
    from src.daemon.jobs_runner import run_job
    tmp_paths["cwd"].mkdir()
    events: list[dict] = []
    asyncio.run(run_job(
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


def test_run_job_timeout_marks_failed(tmp_paths):
    from src.daemon.jobs_runner import run_job
    tmp_paths["cwd"].mkdir()
    result = asyncio.run(run_job(
        script_text="sleep 30",
        interpreter="bash",
        cwd=str(tmp_paths["cwd"]),
        stdout_path=str(tmp_paths["stdout"]),
        stderr_path=str(tmp_paths["stderr"]),
        timeout_seconds=1,
        publish=lambda evt: None,
    ))
    assert result.status == "failed"
    assert result.reason == "timeout"


def test_run_job_missing_interpreter_raises(tmp_paths):
    from src.daemon.jobs_runner import run_job
    tmp_paths["cwd"].mkdir()
    with pytest.raises(FileNotFoundError):
        asyncio.run(run_job(
            script_text="echo x",
            interpreter="no-such-shell-9999",
            cwd=str(tmp_paths["cwd"]),
            stdout_path=str(tmp_paths["stdout"]),
            stderr_path=str(tmp_paths["stderr"]),
            timeout_seconds=10,
            publish=lambda evt: None,
        ))


def test_in_flight_registry_clears_after_run(tmp_paths):
    from src.daemon.jobs_runner import run_job, in_flight_job_ids
    tmp_paths["cwd"].mkdir()
    asyncio.run(run_job(
        job_id="SR-T1",
        script_text="echo x",
        interpreter="bash",
        cwd=str(tmp_paths["cwd"]),
        stdout_path=str(tmp_paths["stdout"]),
        stderr_path=str(tmp_paths["stderr"]),
        timeout_seconds=10,
        publish=lambda evt: None,
    ))
    assert "SR-T1" not in in_flight_job_ids()
