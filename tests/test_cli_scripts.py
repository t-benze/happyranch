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
