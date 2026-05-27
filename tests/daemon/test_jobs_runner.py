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


@pytest.mark.asyncio
async def test_run_job_kills_on_output_cap(tmp_path: Path) -> None:
    """When stdout exceeds max_output_bytes, the runner SIGKILLs and reports output_cap."""
    out = tmp_path / "out.log"
    err = tmp_path / "err.log"
    result = await run_job(
        job_id="JOB-T3",
        # Print 200 KB of 'A's, then sleep. Output should exceed 10 KB cap quickly.
        script_text="python3 -c 'import sys; sys.stdout.write(\"A\" * 200_000); sys.stdout.flush(); import time; time.sleep(30)'\n",
        interpreter="bash",
        cwd=str(tmp_path),
        stdout_path=str(out),
        stderr_path=str(err),
        max_runtime_seconds=None,
        max_output_bytes=10_000,
        publish=lambda e: None,
    )

    assert result.status == "failed"
    assert result.reason == "output_cap"
    # Cap is 10_000 bytes. OS pipe buffer + in-flight chunks may push the actual
    # on-disk size somewhat over the cap, but should remain well under the 200K
    # the script would have written without the cap.
    assert out.stat().st_size < 100_000
