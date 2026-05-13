from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _run(args: list[str], cwd: Path = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "src.cli"] + args,
        capture_output=True, text=True, cwd=cwd,
    )


def test_learning_help_shows_verbs():
    r = _run(["learning", "--help"])
    assert r.returncode == 0
    out = r.stdout
    for verb in ("list", "get", "search", "add", "update", "promote", "reindex"):
        assert verb in out


def test_learning_list_help_shows_filters():
    r = _run(["learning", "list", "--help"])
    assert r.returncode == 0
    assert "--topic" in r.stdout
    assert "--tag" in r.stdout
    assert "--promoted" in r.stdout
