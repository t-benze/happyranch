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
    from runtime.daemon.jobs_runner import run_job
    tmp_paths["cwd"].mkdir()
    result = asyncio.run(run_job(
        script_text="echo hello",
        interpreter="bash",
        cwd=str(tmp_paths["cwd"]),
        stdout_path=str(tmp_paths["stdout"]),
        stderr_path=str(tmp_paths["stderr"]),
        max_runtime_seconds=10,
        publish=lambda evt: None,
    ))
    assert result.exit_code == 0
    assert result.status == "completed"
    assert result.duration_ms >= 0
    assert "hello" in tmp_paths["stdout"].read_text()


def test_run_job_captures_stderr_and_nonzero_exit(tmp_paths):
    from runtime.daemon.jobs_runner import run_job
    tmp_paths["cwd"].mkdir()
    result = asyncio.run(run_job(
        script_text="echo oops >&2; exit 7",
        interpreter="bash",
        cwd=str(tmp_paths["cwd"]),
        stdout_path=str(tmp_paths["stdout"]),
        stderr_path=str(tmp_paths["stderr"]),
        max_runtime_seconds=10,
        publish=lambda evt: None,
    ))
    assert result.exit_code == 7
    assert result.status == "completed"  # natural exit, even non-zero
    assert "oops" in tmp_paths["stderr"].read_text()


def test_run_job_publishes_line_events(tmp_paths):
    from runtime.daemon.jobs_runner import run_job
    tmp_paths["cwd"].mkdir()
    events: list[dict] = []
    asyncio.run(run_job(
        script_text="echo one; echo two; echo three >&2",
        interpreter="bash",
        cwd=str(tmp_paths["cwd"]),
        stdout_path=str(tmp_paths["stdout"]),
        stderr_path=str(tmp_paths["stderr"]),
        max_runtime_seconds=10,
        publish=lambda evt: events.append(evt),
    ))
    kinds = [(e["stream"], e["line"]) for e in events if e.get("kind") == "line"]
    assert ("stdout", "one") in kinds
    assert ("stdout", "two") in kinds
    assert ("stderr", "three") in kinds
    # Terminal event always last.
    assert events[-1]["kind"] == "terminal"


def test_run_job_timeout_marks_failed(tmp_paths):
    from runtime.daemon.jobs_runner import run_job
    tmp_paths["cwd"].mkdir()
    result = asyncio.run(run_job(
        script_text="sleep 30",
        interpreter="bash",
        cwd=str(tmp_paths["cwd"]),
        stdout_path=str(tmp_paths["stdout"]),
        stderr_path=str(tmp_paths["stderr"]),
        max_runtime_seconds=1,
        publish=lambda evt: None,
    ))
    assert result.status == "failed"
    assert result.reason == "timeout"


def test_run_job_missing_interpreter_raises(tmp_paths):
    from runtime.daemon.jobs_runner import run_job
    tmp_paths["cwd"].mkdir()
    with pytest.raises(FileNotFoundError):
        asyncio.run(run_job(
            script_text="echo x",
            interpreter="no-such-shell-9999",
            cwd=str(tmp_paths["cwd"]),
            stdout_path=str(tmp_paths["stdout"]),
            stderr_path=str(tmp_paths["stderr"]),
            max_runtime_seconds=10,
            publish=lambda evt: None,
        ))


def test_in_flight_registry_clears_after_run(tmp_paths):
    from runtime.daemon.jobs_runner import run_job, in_flight_job_ids
    tmp_paths["cwd"].mkdir()
    asyncio.run(run_job(
        job_id="SR-T1",
        script_text="echo x",
        interpreter="bash",
        cwd=str(tmp_paths["cwd"]),
        stdout_path=str(tmp_paths["stdout"]),
        stderr_path=str(tmp_paths["stderr"]),
        max_runtime_seconds=10,
        publish=lambda evt: None,
    ))
    assert "SR-T1" not in in_flight_job_ids()


def test_run_job_strips_venv_from_child_environment(tmp_paths):
    """The job subprocess must NOT inherit VIRTUAL_ENV or
    UV_PROJECT_ENVIRONMENT from the daemon."""
    from runtime.daemon.jobs_runner import run_job

    tmp_paths["cwd"].mkdir()
    # Script writes env vars to stdout for inspection.
    script = (
        "echo VIRTUAL_ENV=${VIRTUAL_ENV:-ABSENT};"
        "echo UV_PROJECT_ENVIRONMENT=${UV_PROJECT_ENVIRONMENT:-ABSENT};"
        "echo PATH=${PATH:-ABSENT};"
        "echo HAPPYRANCH_ORG_SLUG=${HAPPYRANCH_ORG_SLUG:-ABSENT}"
    )

    # Inject adversarial VIRTUAL_ENV into os.environ BEFORE the test.
    # The _sanitize_child_env call inside run_job should strip it.
    # Also inject a nonempty org slug so the assertion cannot pass by
    # substring coincidence (the prior assertion HAPPYRANCH_ORG_SLUG= is
    # vacuously true against the ABSENT output).
    import os
    os.environ["VIRTUAL_ENV"] = "/fake/canonical/.venv"
    os.environ["UV_PROJECT_ENVIRONMENT"] = "/fake/project"
    os.environ["HAPPYRANCH_ORG_SLUG"] = "testorg-jobs"
    try:
        result = asyncio.run(run_job(
            script_text=script,
            interpreter="bash",
            cwd=str(tmp_paths["cwd"]),
            stdout_path=str(tmp_paths["stdout"]),
            stderr_path=str(tmp_paths["stderr"]),
            max_runtime_seconds=10,
            publish=lambda evt: None,
        ))
    finally:
        del os.environ["VIRTUAL_ENV"]
        del os.environ["UV_PROJECT_ENVIRONMENT"]
        del os.environ["HAPPYRANCH_ORG_SLUG"]

    assert result.status == "completed"
    assert result.exit_code == 0
    out = tmp_paths["stdout"].read_text()
    assert "VIRTUAL_ENV=ABSENT" in out, f"VIRTUAL_ENV should be ABSENT, got: {out}"
    assert "UV_PROJECT_ENVIRONMENT=ABSENT" in out, f"UV_PROJECT_ENVIRONMENT should be ABSENT, got: {out}"
    assert "PATH=ABSENT" not in out, "PATH must be present"
    # Exact value assertion — cannot pass by substring coincidence.
    assert "HAPPYRANCH_ORG_SLUG=testorg-jobs" in out, (
        f"HAPPYRANCH_ORG_SLUG must be passed through with exact value; got: {out}"
    )
