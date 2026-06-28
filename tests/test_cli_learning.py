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


def test_memory_help_shows_verbs():
    r = _run(["memory", "--help"])
    assert r.returncode == 0
    out = r.stdout
    for verb in ("list", "get", "search", "add", "update", "promote", "reindex"):
        assert verb in out


def test_memory_list_help_shows_filters():
    r = _run(["memory", "list", "--help"])
    assert r.returncode == 0
    assert "--topic" in r.stdout
    assert "--tag" in r.stdout
    assert "--promoted" in r.stdout


def test_learning_alias_still_registered():
    """The deprecated `learning` verb still exists for one rollout cycle."""
    r = _run(["learning", "--help"])
    assert r.returncode == 0
    for verb in ("list", "get", "search", "add", "update", "promote", "reindex"):
        assert verb in r.stdout


def test_learning_alias_dispatch_warns(monkeypatch, capsys):
    from cli import main as cli

    class FakeResponse:
        status_code = 200
        def json(self): return {"entries": []}

    class FakeClient:
        def get(self, path, params=None): return FakeResponse()
        def close(self): pass

    monkeypatch.setattr(cli.OpcClient, "from_env", classmethod(lambda c: FakeClient()))
    monkeypatch.setattr("cli._shared._fetch_available_orgs", lambda c: ["o"])
    from cli.commands.learning import _deprecation_wrapper, cmd_learning_list
    args = type("A", (), dict(
        org="o", agent="dev_agent",
        topic=None, tag=None, promoted=False, not_promoted=False, json=False,
    ))()
    _deprecation_wrapper(cmd_learning_list)(args)
    assert "deprecated" in capsys.readouterr().err


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
    monkeypatch.setattr("cli._shared._fetch_available_orgs", lambda c: ["my-org"])
    args = type("A", (), dict(
        org="my-org", agent="dev_agent",
        topic="workflow", tag=None, promoted=False, not_promoted=False, json=False,
    ))()
    cli.cmd_learning_list(args)
    assert captured["path"] == "/api/v1/orgs/my-org/agents/dev_agent/memory/entries/"
    assert captured["params"]["topic"] == "workflow"


def test_cmd_learning_add_reads_yaml_and_posts(monkeypatch, tmp_path):
    captured = {}

    class FakeResponse:
        status_code = 200
        def json(self): return {"id": "MEM-001", "path": "memory/MEM-001-x.md"}

    class FakeClient:
        def post(self, path, json=None):
            captured["path"] = path
            captured["json"] = json
            return FakeResponse()
        def close(self): pass

    from cli import main as cli
    monkeypatch.setattr(cli.OpcClient, "from_env", classmethod(lambda c: FakeClient()))
    monkeypatch.setattr("cli._shared._fetch_available_orgs", lambda c: ["o"])
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
    assert captured["path"] == "/api/v1/orgs/o/agents/dev_agent/memory/entries/"
    assert captured["json"]["slug"] == "x"
    assert captured["json"]["tags"] == ["a", "b"]
    assert "body line 2" in captured["json"]["body"]


def test_cmd_learning_promote_posts_correct_path(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        def json(self): return {"id": "MEM-001", "promoted_to": "kb-x", "body": "..."}

    class FakeClient:
        def post(self, path, json=None):
            captured["path"] = path
            captured["json"] = json
            return FakeResponse()
        def close(self): pass

    from cli import main as cli
    monkeypatch.setattr(cli.OpcClient, "from_env", classmethod(lambda c: FakeClient()))
    monkeypatch.setattr("cli._shared._fetch_available_orgs", lambda c: ["o"])
    args = type("A", (), dict(
        org="o", agent="dev_agent", id="MEM-001", kb_slug="kb-x",
    ))()
    cli.cmd_learning_promote(args)
    assert captured["path"] == "/api/v1/orgs/o/agents/dev_agent/memory/entries/MEM-001/promote"
    assert captured["json"] == {"kb_slug": "kb-x"}


# ---------------------------------------------------------------------------
# REVISE TASK-974 F2: real documented command forms must PARSE through the
# actual build_parser() (exit 0) and DISPATCH to the correct handler/route —
# not merely render help text. Prior bug: a required parent --org plus a
# colliding subcommand --org made `memory get --org o --agent a MEM-001`
# fail argparse (exit 2), and `memory --org o get ...` silently clobber org.
# ---------------------------------------------------------------------------

def _parse(argv):
    from cli import main as cli
    return cli.build_parser().parse_args(argv)


def _install_fake_client(monkeypatch, captured):
    class FakeResponse:
        status_code = 200
        def json(self):
            return {
                "entries": [], "hits": [],
                "id": "MEM-001", "slug": "s", "title": "T", "topic": "t",
                "body": "b", "path": "memory/MEM-001-s.md",
            }

    class FakeClient:
        def get(self, path, params=None):
            captured["path"] = path
            return FakeResponse()

        def post(self, path, json=None):
            captured["path"] = path
            captured["json"] = json
            return FakeResponse()

        def request(self, method, path, json=None):
            captured["path"] = path
            return FakeResponse()

        def close(self):
            pass

    from cli import main as cli
    monkeypatch.setattr(cli.OpcClient, "from_env", classmethod(lambda c: FakeClient()))
    monkeypatch.setattr("cli._shared._fetch_available_orgs", lambda c: ["o"])


def test_memory_get_form_parses_and_dispatches(monkeypatch):
    captured = {}
    _install_fake_client(monkeypatch, captured)
    args = _parse(["memory", "get", "--org", "o", "--agent", "a", "MEM-001"])
    assert args.org == "o"
    args.func(args)
    assert captured["path"] == "/api/v1/orgs/o/agents/a/memory/entries/MEM-001"


def test_memory_org_before_verb_form_does_not_clobber_org(monkeypatch):
    captured = {}
    _install_fake_client(monkeypatch, captured)
    # Parent --org before the verb must survive (subparser must not reset it).
    args = _parse(["memory", "--org", "o", "get", "--agent", "a", "MEM-001"])
    assert args.org == "o"
    args.func(args)
    assert captured["path"] == "/api/v1/orgs/o/agents/a/memory/entries/MEM-001"


def test_memory_add_form_parses_and_dispatches(monkeypatch, tmp_path):
    captured = {}
    _install_fake_client(monkeypatch, captured)
    payload = tmp_path / "p.yaml"
    payload.write_text("slug: x\ntitle: T\ntopic: w\nbody: hi\n")
    args = _parse(["memory", "add", "--org", "o", "--agent", "a", "--from-file", str(payload)])
    assert args.org == "o"
    args.func(args)
    assert captured["path"] == "/api/v1/orgs/o/agents/a/memory/entries/"
    assert captured["json"]["slug"] == "x"


def test_memory_search_form_parses_and_dispatches(monkeypatch):
    captured = {}
    _install_fake_client(monkeypatch, captured)
    args = _parse(["memory", "search", "--org", "o", "--agent", "a", "rename gotchas"])
    assert args.org == "o"
    args.func(args)
    assert captured["path"] == "/api/v1/orgs/o/agents/a/memory/entries/search"
    assert captured["json"]["query"] == "rename gotchas"


def test_learning_alias_get_form_parses_and_dispatches(monkeypatch, capsys):
    """The one-cycle `learning` deprecation alias must parse + dispatch the
    same real forms as `memory`, accepting a legacy LRN- id."""
    captured = {}
    _install_fake_client(monkeypatch, captured)
    args = _parse(["learning", "get", "--org", "o", "--agent", "a", "LRN-001"])
    assert args.org == "o"
    args.func(args)
    assert captured["path"] == "/api/v1/orgs/o/agents/a/memory/entries/LRN-001"
    assert "deprecated" in capsys.readouterr().err


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


# ═══════════════════════════════════════════════════════════════════
# THR-032 P3a — lifecycle command
# ═══════════════════════════════════════════════════════════════════


def _fake_client_for_lifecycle(monkeypatch, captured):
    """Install a fake OPC client that captures PATCH calls."""

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "id": "MEM-001",
                "lifecycle": "evicted",
                "previous_lifecycle": "valid",
                "slug": "x", "title": "x", "topic": "w",
            }

    class FakeClient:
        def patch(self, path, json=None):
            captured["path"] = path
            captured["json"] = json
            return FakeResponse()

        def close(self):
            pass

    from cli import main as cli

    monkeypatch.setattr(cli.OpcClient, "from_env", classmethod(lambda c: FakeClient()))
    monkeypatch.setattr("cli._shared._fetch_available_orgs", lambda c: ["o"])


def test_memory_help_includes_lifecycle():
    r = _run(["memory", "--help"])
    assert r.returncode == 0
    assert "lifecycle" in r.stdout


def test_memory_lifecycle_parses_and_dispatches(monkeypatch):
    captured = {}
    _fake_client_for_lifecycle(monkeypatch, captured)
    args = _parse([
        "memory", "lifecycle",
        "--org", "o", "--agent", "a",
        "MEM-001",
        "--set", "evicted",
        "--reason", "obsolete info",
    ])
    assert args.org == "o"
    args.func(args)
    assert captured["path"] == "/api/v1/orgs/o/agents/a/memory/entries/MEM-001/lifecycle"
    assert captured["json"] == {"lifecycle": "evicted", "reason": "obsolete info"}


def test_memory_lifecycle_missing_reason_fails_before_http(monkeypatch):
    """Missing --reason should fail argparse, not reach HTTP."""
    with pytest.raises(SystemExit):
        _parse([
            "memory", "lifecycle",
            "--org", "o", "--agent", "a",
            "MEM-001",
            "--set", "evicted",
        ])


def test_memory_lifecycle_missing_set_fails_before_http(monkeypatch):
    """Missing --set should fail argparse, not reach HTTP."""
    with pytest.raises(SystemExit):
        _parse([
            "memory", "lifecycle",
            "--org", "o", "--agent", "a",
            "MEM-001",
            "--reason", "test",
        ])


def test_learning_lifecycle_alias_warns_and_dispatches(monkeypatch, capsys):
    """The deprecated `learning lifecycle` alias warns and dispatches."""
    captured = {}
    _fake_client_for_lifecycle(monkeypatch, captured)
    args = _parse([
        "learning", "lifecycle",
        "--org", "o", "--agent", "a",
        "MEM-001",
        "--set", "evicted",
        "--reason", "test alias",
    ])
    assert args.org == "o"
    args.func(args)
    assert captured["path"] == "/api/v1/orgs/o/agents/a/memory/entries/MEM-001/lifecycle"
    assert "deprecated" in capsys.readouterr().err


# ── Compact tests ──

def _fake_client_for_compact(monkeypatch, captured: dict):
    class FakeResp:
        status_code = 200
        @staticmethod
        def json():
            return {"dry_run": captured["dry_run"], "candidates": [],
                    "evicted": [], "skipped": [], "errors": []}

    class FakeClient:
        @staticmethod
        def from_env():
            return FakeClient()
        def post(self, path, json=None):
            captured["path"] = path
            captured["dry_run"] = json.get("dry_run")
            return FakeResp()

    monkeypatch.setattr("cli.commands.learning.OpcClient", FakeClient)
    monkeypatch.setattr("cli._shared._fetch_available_orgs", lambda client: ["o"])


def test_memory_help_includes_compact():
    import pytest as _pytest
    with _pytest.raises(SystemExit):
        _parse(["memory", "compact", "--help"])


def test_memory_compact_dry_run_parses_and_dispatches(monkeypatch):
    captured = {}
    _fake_client_for_compact(monkeypatch, captured)
    args = _parse([
        "memory", "compact",
        "--org", "o", "--agent", "a",
        "--dry-run",
    ])
    assert args.org == "o"
    args.func(args)
    assert captured["path"] == "/api/v1/orgs/o/agents/a/memory/entries/compact"
    assert captured["dry_run"] is True


def test_memory_compact_apply_parses_and_dispatches(monkeypatch):
    captured = {}
    _fake_client_for_compact(monkeypatch, captured)
    args = _parse([
        "memory", "compact",
        "--org", "o", "--agent", "a",
        "--apply",
    ])
    args.func(args)
    assert captured["path"] == "/api/v1/orgs/o/agents/a/memory/entries/compact"
    assert captured["dry_run"] is False


def test_memory_compact_mutually_exclusive(monkeypatch):
    """--dry-run and --apply are mutually exclusive."""
    import pytest as _pytest
    with _pytest.raises(SystemExit):
        _parse([
            "memory", "compact",
            "--org", "o", "--agent", "a",
            "--dry-run", "--apply",
        ])


def test_memory_compact_requires_one_mode(monkeypatch):
    """Either --dry-run or --apply must be provided."""
    import pytest as _pytest
    with _pytest.raises(SystemExit):
        _parse([
            "memory", "compact",
            "--org", "o", "--agent", "a",
        ])


# ── Search with new flags ──

def _fake_client_for_search(monkeypatch, captured: dict):
    class FakeResp:
        status_code = 200
        @staticmethod
        def json():
            return {"hits": [], "warnings": []}

    class FakeClient:
        @staticmethod
        def from_env():
            return FakeClient()
        def post(self, path, json=None):
            captured["path"] = path
            captured["body"] = json
            return FakeResp()

    monkeypatch.setattr("cli.commands.learning.OpcClient", FakeClient)
    monkeypatch.setattr("cli._shared._fetch_available_orgs", lambda client: ["o"])


def test_memory_search_new_flags(monkeypatch):
    captured = {}
    _fake_client_for_search(monkeypatch, captured)
    args = _parse([
        "memory", "search",
        "--org", "o", "--agent", "a",
        "--include-evicted", "--include-superseded", "--include-kb",
        "test query",
    ])
    args.func(args)
    assert captured["body"]["include_evicted"] is True
    assert captured["body"]["include_superseded"] is True
    assert captured["body"]["include_kb"] is True
    assert captured["body"]["query"] == "test query"
