"""CLI smoke tests for happyranch jobs subcommands."""
from __future__ import annotations

import subprocess
from pathlib import Path


def _run(*args) -> subprocess.CompletedProcess:
    """Invoke `uv run happyranch ...` from the worktree root."""
    return subprocess.run(
        ["uv", "run", "happyranch", *args],
        capture_output=True, text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )


def test_jobs_submit_help():
    result = _run("jobs", "submit", "--help")
    assert result.returncode == 0
    assert "--from-file" in result.stdout
    assert "--org" in result.stdout


def test_jobs_submit_missing_from_file():
    """argparse should fail-fast when --from-file is missing."""
    result = _run("jobs", "submit", "--org", "alpha")
    assert result.returncode != 0
    assert "from-file" in (result.stderr + result.stdout)


def test_jobs_list_help():
    r = _run("jobs", "list", "--help")
    assert r.returncode == 0
    assert "--status" in r.stdout


def test_jobs_show_help():
    r = _run("jobs", "show", "--help")
    assert r.returncode == 0


def test_jobs_reject_help():
    r = _run("jobs", "reject", "--help")
    assert r.returncode == 0
    assert "--reason" in r.stdout


def test_jobs_output_help():
    r = _run("jobs", "output", "--help")
    assert r.returncode == 0
    assert "--stream" in r.stdout


def test_jobs_run_help():
    r = _run("jobs", "run", "--help")
    assert r.returncode == 0
    assert "--cwd" in r.stdout
    assert "--timeout-seconds" in r.stdout


def test_jobs_run_requires_tty():
    """Non-TTY invocation fails-fast with canonical message."""
    r = _run("jobs", "run", "JOB-001", "--org", "alpha")
    assert r.returncode != 0
    assert "TTY" in (r.stderr + r.stdout)


def test_scripts_shim_prints_deprecation_warning():
    """`happyranch scripts <verb>` reaches the handler with a deprecation banner."""
    # `run` fails the TTY gate before any network call — same pattern as
    # test_jobs_run_requires_tty above, just on the deprecated alias path.
    r = _run("scripts", "run", "JOB-001", "--org", "alpha")
    assert r.returncode != 0
    assert "deprecated" in r.stderr.lower()
