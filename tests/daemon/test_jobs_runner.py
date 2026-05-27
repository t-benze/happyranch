"""Unit tests for src/daemon/jobs_runner.py — max_runtime_seconds semantics."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.daemon.jobs_runner import run_job


@pytest.mark.asyncio
async def test_run_job_with_none_timeout_runs_to_completion(tmp_path: Path) -> None:
    """max_runtime_seconds=None means no asyncio.wait_for wrapper — natural exit."""
    out = tmp_path / "out.log"
    err = tmp_path / "err.log"
    events: list[dict] = []

    result = await run_job(
        job_id="JOB-T1",
        script_text="echo hello\nsleep 0.1\necho done\n",
        interpreter="bash",
        cwd=str(tmp_path),
        stdout_path=str(out),
        stderr_path=str(err),
        max_runtime_seconds=None,
        publish=events.append,
    )

    assert result.status == "completed"
    assert result.exit_code == 0
    assert result.reason is None
    assert "hello" in out.read_text()
    assert "done" in out.read_text()


@pytest.mark.asyncio
async def test_run_job_with_explicit_timeout_kills_runaway(tmp_path: Path) -> None:
    """max_runtime_seconds=positive still kills the process."""
    out = tmp_path / "out.log"
    err = tmp_path / "err.log"
    result = await run_job(
        job_id="JOB-T2",
        script_text="sleep 60\n",
        interpreter="bash",
        cwd=str(tmp_path),
        stdout_path=str(out),
        stderr_path=str(err),
        max_runtime_seconds=1,
        publish=lambda e: None,
    )

    assert result.status == "failed"
    assert result.reason == "timeout"
