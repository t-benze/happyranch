from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


def _run(args: list[str], cwd: Path = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "cli.main"] + args,
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

    class FakeResponse:
        status_code = 200
        def json(self): return {"entries": []}

    class FakeClient:
        def get(self, path, params=None):
            captured["path"] = path
            captured["params"] = params
            return FakeResponse()
        def close(self): pass

    from cli import main as cli
    monkeypatch.setattr(cli.OpcClient, "from_env", classmethod(lambda c: FakeClient()))
    monkeypatch.setattr(cli, "_fetch_available_orgs", lambda c: ["my-org"])
    args = type("A", (), dict(
        org="my-org", agent="dev_agent",
        topic="workflow", tag=None, promoted=False, not_promoted=False, json=False,
    ))()
    cli.cmd_learning_list(args)
    assert captured["path"] == "/api/v1/orgs/my-org/agents/dev_agent/learnings/entries/"
    assert captured["params"]["topic"] == "workflow"


def test_cmd_learning_add_reads_yaml_and_posts(monkeypatch, tmp_path):
    captured = {}

    class FakeResponse:
        status_code = 200
        def json(self): return {"id": "LRN-001", "path": "learnings/LRN-001-x.md"}

    class FakeClient:
        def post(self, path, json=None):
            captured["path"] = path
            captured["json"] = json
            return FakeResponse()
        def close(self): pass

    from cli import main as cli
    monkeypatch.setattr(cli.OpcClient, "from_env", classmethod(lambda c: FakeClient()))
    monkeypatch.setattr(cli, "_fetch_available_orgs", lambda c: ["o"])
    payload_path = tmp_path / "p.yaml"
    payload_path.write_text(
        "slug: x\n"
        "title: T\n"
        "topic: w\n"
        "tags: [a, b]\n"
        "body: |\n"
        "  body line 1\n"
        "  body line 2\n"
    )
    args = type("A", (), dict(
        org="o", agent="dev_agent", from_file=str(payload_path),
    ))()
    cli.cmd_learning_add(args)
    assert captured["path"] == "/api/v1/orgs/o/agents/dev_agent/learnings/entries/"
    assert captured["json"]["slug"] == "x"
    assert captured["json"]["tags"] == ["a", "b"]
    assert "body line 2" in captured["json"]["body"]


def test_cmd_learning_promote_posts_correct_path(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        def json(self): return {"id": "LRN-001", "promoted_to": "kb-x", "body": "..."}

    class FakeClient:
        def post(self, path, json=None):
            captured["path"] = path
            captured["json"] = json
            return FakeResponse()
        def close(self): pass

    from cli import main as cli
    monkeypatch.setattr(cli.OpcClient, "from_env", classmethod(lambda c: FakeClient()))
    monkeypatch.setattr(cli, "_fetch_available_orgs", lambda c: ["o"])
    args = type("A", (), dict(
        org="o", agent="dev_agent", id="LRN-001", kb_slug="kb-x",
    ))()
    cli.cmd_learning_promote(args)
    assert captured["path"] == "/api/v1/orgs/o/agents/dev_agent/learnings/entries/LRN-001/promote"
    assert captured["json"] == {"kb_slug": "kb-x"}


def test_read_yaml_payload_rejects_non_dict(tmp_path, capsys):
    from cli import main as cli
    bad = tmp_path / "list.yaml"
    bad.write_text("- one\n- two\n")
    with pytest.raises(SystemExit) as exc:
        cli._read_yaml_payload(str(bad))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "must be a YAML mapping" in err


def test_read_yaml_payload_empty_file_returns_empty_dict(tmp_path):
    from cli import main as cli
    empty = tmp_path / "empty.yaml"
    empty.write_text("")
    assert cli._read_yaml_payload(str(empty)) == {}
