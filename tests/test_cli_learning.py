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


def test_cmd_learning_list_calls_correct_route(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def get(self, path, params=None):
            captured["path"] = path
            captured["params"] = params
            return {"entries": []}

    from src import cli
    monkeypatch.setattr(cli, "Client", FakeClient)
    args = type("A", (), dict(
        org="my-org", agent="dev_agent",
        topic="workflow", tag=None, promoted=False, not_promoted=False, json=False,
    ))()
    cli.cmd_learning_list(args)
    assert captured["path"] == "/api/v1/orgs/my-org/agents/dev_agent/learnings/entries/"
    assert captured["params"]["topic"] == "workflow"
