"""CLI smoke tests for grassland scripts subcommands."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _run(*args) -> subprocess.CompletedProcess:
    """Invoke `uv run grassland ...` (worktree-local)."""
    return subprocess.run(
        ["uv", "run", "grassland", *args],
        capture_output=True, text=True,
        cwd="/Users/tangbz/projects/my-opc/.claude/worktrees/scripts-executor",
    )


def test_scripts_submit_help():
    result = _run("scripts", "submit", "--help")
    assert result.returncode == 0
    assert "--from-file" in result.stdout
    assert "--org" in result.stdout


def test_scripts_submit_missing_from_file():
    """argparse should fail-fast when --from-file is missing."""
    result = _run("scripts", "submit", "--org", "alpha")
    assert result.returncode != 0
    assert "from-file" in (result.stderr + result.stdout)


def test_scripts_list_help():
    r = _run("scripts", "list", "--help")
    assert r.returncode == 0
    assert "--status" in r.stdout


def test_scripts_show_help():
    r = _run("scripts", "show", "--help")
    assert r.returncode == 0


def test_scripts_reject_help():
    r = _run("scripts", "reject", "--help")
    assert r.returncode == 0
    assert "--reason" in r.stdout


def test_scripts_output_help():
    r = _run("scripts", "output", "--help")
    assert r.returncode == 0
    assert "--stream" in r.stdout
