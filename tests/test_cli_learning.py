from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.database import Database


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
        def get(self, path, params=None, headers=None):
            captured["path"] = path
            if headers:
                captured["headers"] = headers
            return FakeResponse()

        def post(self, path, json=None, params=None):
            captured["path"] = path
            captured["json"] = json
            if params:
                captured["params"] = params
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


# ── Memory report ──

def test_memory_report_paginates_and_prints_guarded_status(monkeypatch, capsys):
    """The canonical report command exhausts audit pages and stays guarded."""
    from argparse import Namespace
    from cli.commands.learning import cmd_memory_report

    class FakeResp:
        status_code = 200

        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    calls = []
    rows = {
        "memory_digest_impression": [
            {"timestamp": "2026-01-01T00:00:00+00:00", "agent": "dev_agent",
             "task_id": "TASK-1", "payload": '{"session_id":"sess-1","digest_ids":["MEM-1"]}'},
        ],
        "memory_read": [
            {"agent": "dev_agent", "task_id": "TASK-1",
             "payload": '{"session_id":"sess-1","id":"MEM-1"}'},
        ],
        "memory_search": [],
    }

    class FakeClient:
        @staticmethod
        def from_env():
            return FakeClient()

        def get(self, path, params=None):
            if path.endswith("/agents"):
                return FakeResp({"agents": []})
            assert path.endswith("/audit")
            action = params["action"]
            cursor = params.get("cursor")
            calls.append((action, cursor))
            if cursor is None:
                return FakeResp({"entries": rows[action], "next_cursor": f"{action}-next"})
            assert cursor == f"{action}-next"
            return FakeResp({"entries": [], "next_cursor": None})

    monkeypatch.setattr("cli.commands.learning.OpcClient", FakeClient)
    monkeypatch.setattr("cli._shared._fetch_available_orgs", lambda client: ["o"])
    cmd_memory_report(Namespace(org="o", json=False))

    assert calls == [
        ("memory_digest_impression", None), ("memory_digest_impression", "memory_digest_impression-next"),
        ("memory_read", None), ("memory_read", "memory_read-next"),
        ("memory_search", None), ("memory_search", "memory_search-next"),
    ]
    rendered = capsys.readouterr().out
    assert "DECISION: insufficient_instrumentation" in rendered
    assert "unversioned and invalid" in rendered
    assert "Thresholds:    NOT MET" in rendered
    assert "Canary-gated collection has NOT started" in rendered


def test_memory_report_exhausts_populated_pages_and_rejects_malformed_rows(monkeypatch, capsys):
    """Real command output stays fail-closed after later-page malformed input."""
    from argparse import Namespace
    from cli.commands.learning import cmd_memory_report

    class FakeResp:
        status_code = 200

        def __init__(self, body): self._body = body
        def json(self): return self._body

    pages = {
        "memory_digest_impression": [
            [{"timestamp": "2026-01-01T00:00:00+00:00", "agent": "dev_agent", "task_id": "TASK-1", "payload": '{"session_id":"sess-1","digest_ids":["MEM-1"]}'}],
            [{"timestamp": "2026-01-02T00:00:00+00:00", "agent": "dev_agent", "task_id": "TASK-2", "payload": '[]'}],
        ],
        "memory_read": [[{"agent": "dev_agent", "task_id": "TASK-1", "payload": '{"id":"MEM-1","session_id":"sess-1","task_id":"TASK-1"}'}], []],
        "memory_search": [[{"agent": "dev_agent", "task_id": "TASK-1", "payload": '{"id":"MEM-2","session_id":"sess-1","task_id":"TASK-1","source":"search"}'}], []],
    }

    class FakeClient:
        def get(self, path, params=None):
            if path.endswith("/agents"):
                return FakeResp({"agents": [{"name": "dev_agent", "role": "developer"}]})
            action = params["action"]
            index = 1 if params.get("cursor") else 0
            return FakeResp({"entries": pages[action][index], "next_cursor": "next" if index == 0 else None})

    monkeypatch.setattr("cli.commands.learning._learning_client", lambda: FakeClient())
    monkeypatch.setattr("cli._shared._fetch_available_orgs", lambda client: ["o"])
    cmd_memory_report(Namespace(org="o", json=False))
    rendered = capsys.readouterr().out
    assert "insufficient_instrumentation" in rendered
    assert "Thresholds:    NOT MET" in rendered


def test_memory_report_database_parity_exhausts_populated_pages(monkeypatch, capsys, tmp_path):
    """CLI exhausts real populated audit pages and agrees with AuditLogger."""
    from argparse import Namespace
    from cli.commands.learning import cmd_memory_report

    db = Database(tmp_path / "telemetry.db")
    logger = AuditLogger(db)
    for index in range(501):
        session_id = f"sess-{index:03d}"
        task_id = f"TASK-{index:03d}"
        memory_id = f"MEM-{index:03d}"
        logger.log_memory_digest_impression(
            agent="dev_agent", task_id=task_id, session_id=session_id,
            digest_ids=[memory_id], budget=1500,
        )
        logger.log_memory_read(
            agent="dev_agent", id=memory_id, slug=memory_id,
            session_id=session_id, task_id=task_id,
            source="search" if index == 500 else "digest",
        )
        logger.log_memory_search(
            agent="dev_agent", session_id=session_id, task_id=task_id,
            memory_ids=[memory_id], hit_count=1, kb_hit_count=0,
        )
    db.execute("UPDATE audit_log SET timestamp='2026-01-01T00:00:00+00:00'")

    class FakeResp:
        status_code = 200

        def __init__(self, body): self._body = body
        def json(self): return self._body

    class PaginatingDatabaseClient:
        def get(self, path, params=None):
            if path.endswith("/agents"):
                return FakeResp({"agents": [{"name": "dev_agent", "role": "developer"}]})
            rows = [dict(row) for row in db.fetch_all_readonly(
                "SELECT timestamp, agent, task_id, payload FROM audit_log"
                " WHERE action = ? ORDER BY id ASC", (params["action"],),
            )]
            start = int(params.get("cursor", "0"))
            end = start + 250
            return FakeResp({
                "entries": rows[start:end],
                "next_cursor": str(end) if end < len(rows) else None,
            })

    monkeypatch.setattr("cli.commands.learning._learning_client", PaginatingDatabaseClient)
    monkeypatch.setattr("cli._shared._fetch_available_orgs", lambda client: ["o"])
    backend = logger.compute_memory_telemetry_report(
        agent_role_map={"dev_agent": "developer"},
    )

    cmd_memory_report(Namespace(org="o", json=True))
    cli_json = json.loads(capsys.readouterr().out)
    assert cli_json["decision"] == backend["decision"] == "insufficient_instrumentation"
    assert cli_json["decision_detail"] == backend["decision_detail"]
    assert cli_json["observation_period"] == backend["observation_period"]
    assert cli_json["observation_period"]["thresholds_met"] is False
    assert cli_json["aggregate"] == backend["aggregate"]

    cmd_memory_report(Namespace(org="o", json=False))
    rendered = capsys.readouterr().out
    assert "DECISION: insufficient_instrumentation" in rendered
    assert "Thresholds:    NOT MET" in rendered
    assert "Thresholds:    MET" not in rendered
    assert "Canary-gated collection has NOT started" in rendered
    assert "Tuning advice" not in rendered


@pytest.mark.parametrize(
    ("action", "payload", "timestamp"),
    [
        ("memory_read", '{"id":null,"source":"search","session_id":"sess-500","task_id":"TASK-500"}', None),
        ("memory_read", '{"id":"MEM-500","source":"search","session_id":"sess-500","task_id":"TASK-500"}', "2026-01-01T00:00:00"),
        ("memory_search", "[]", None),
        ("memory_search", '{"session_id":"sess-500","task_id":"TASK-500","memory_ids":"MEM-500","hit_count":1,"kb_hit_count":0}', None),
    ],
)
def test_memory_report_real_database_rejects_malformed_later_pages_identically(
    monkeypatch, capsys, tmp_path, action, payload, timestamp,
):
    """All consumed streams are exhausted and malformed later pages get no credit."""
    from argparse import Namespace
    from cli.commands.learning import cmd_memory_report

    db = Database(tmp_path / "telemetry.db")
    logger = AuditLogger(db)
    for index in range(501):
        session_id = f"sess-{index:03d}"
        task_id = f"TASK-{index:03d}"
        memory_id = f"MEM-{index:03d}"
        logger.log_memory_digest_impression(
            agent="dev_agent", task_id=task_id, session_id=session_id,
            digest_ids=[memory_id], budget=1500,
        )
        logger.log_memory_read(
            agent="dev_agent", id=memory_id, slug=memory_id,
            session_id=session_id, task_id=task_id, source="search",
        )
        logger.log_memory_search(
            agent="dev_agent", session_id=session_id, task_id=task_id,
            memory_ids=[memory_id], hit_count=1, kb_hit_count=0,
        )
    db.execute("UPDATE audit_log SET timestamp='2026-01-01T00:00:00+00:00'")
    db.execute("UPDATE audit_log SET payload=? WHERE action=? AND payload LIKE '%sess-500%'", (payload, action))
    if timestamp is not None:
        db.execute("UPDATE audit_log SET timestamp=? WHERE action=? AND payload LIKE '%sess-500%'", (timestamp, action))

    calls = []

    class Response:
        status_code = 200

        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    class Client:
        def get(self, path, params=None):
            if path.endswith("/agents"):
                return Response({"agents": [{"name": "dev_agent", "role": "developer"}]})
            event = params["action"]
            rows = [dict(row) for row in db.fetch_all_readonly(
                "SELECT timestamp, agent, task_id, payload FROM audit_log WHERE action=? ORDER BY id",
                (event,),
            )]
            start = int(params.get("cursor", "0"))
            end = start + 250
            calls.append((event, start))
            return Response({"entries": rows[start:end], "next_cursor": str(end) if end < len(rows) else None})

    monkeypatch.setattr("cli.commands.learning._learning_client", Client)
    monkeypatch.setattr("cli._shared._fetch_available_orgs", lambda client: ["o"])
    backend = logger.compute_memory_telemetry_report(agent_role_map={"dev_agent": "developer"})
    cmd_memory_report(Namespace(org="o", json=True))
    cli_json = json.loads(capsys.readouterr().out)
    cmd_memory_report(Namespace(org="o", json=False))
    text = capsys.readouterr().out

    assert backend == cli_json
    assert backend["decision"] == "insufficient_instrumentation"
    assert backend["observation_period"]["thresholds_met"] is False
    assert backend["aggregate"] == {}
    assert "Thresholds:    NOT MET" in text
    assert "Canary-gated collection has NOT started" in text
    assert "Tuning advice" not in text
    assert calls == [
        (event, cursor)
        for event in ("memory_digest_impression", "memory_read", "memory_search")
        for cursor in (0, 250, 500)
    ] * 2


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
        def post(self, path, json=None, params=None):
            captured["path"] = path
            captured["body"] = json
            captured["params"] = params
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


# ── Tri-state search flags ──

def test_memory_search_omits_fields_when_not_provided(monkeypatch):
    """When no --limit or include flags are given, the JSON payload
    contains only the query field, letting the daemon apply org config."""
    captured = {}
    _fake_client_for_search(monkeypatch, captured)
    args = _parse([
        "memory", "search",
        "--org", "o", "--agent", "a",
        "bare query",
    ])
    args.func(args)
    body = captured["body"]
    assert body == {"query": "bare query"}
    assert "limit" not in body
    assert "include_promoted" not in body
    assert "include_evicted" not in body
    assert "include_superseded" not in body
    assert "include_kb" not in body


def test_memory_search_explicit_true_include_serialized(monkeypatch):
    """--include-kb (and siblings) serialize True when provided."""
    captured = {}
    _fake_client_for_search(monkeypatch, captured)
    args = _parse([
        "memory", "search",
        "--org", "o", "--agent", "a",
        "--include-kb", "--include-evicted",
        "query",
    ])
    args.func(args)
    body = captured["body"]
    assert body["include_kb"] is True
    assert body["include_evicted"] is True
    assert "query" in body


def test_memory_search_explicit_false_include_serialized(monkeypatch):
    """--no-include-kb (and siblings) serialize False when provided."""
    captured = {}
    _fake_client_for_search(monkeypatch, captured)
    args = _parse([
        "memory", "search",
        "--org", "o", "--agent", "a",
        "--no-include-kb", "--no-include-evicted",
        "query",
    ])
    args.func(args)
    body = captured["body"]
    assert body["include_kb"] is False
    assert body["include_evicted"] is False
    assert "query" in body


def test_memory_search_explicit_limit_serialized(monkeypatch):
    """--limit serializes when provided."""
    captured = {}
    _fake_client_for_search(monkeypatch, captured)
    args = _parse([
        "memory", "search",
        "--org", "o", "--agent", "a",
        "--limit", "5",
        "query",
    ])
    args.func(args)
    body = captured["body"]
    assert body["limit"] == 5
    assert "include_kb" not in body  # flags still omitted


def test_memory_search_include_promoted_still_sends_true(monkeypatch):
    """--include-promoted keeps its compatible behavior: only sends True."""
    captured = {}
    _fake_client_for_search(monkeypatch, captured)
    args = _parse([
        "memory", "search",
        "--org", "o", "--agent", "a",
        "--include-promoted",
        "query",
    ])
    args.func(args)
    body = captured["body"]
    assert body["include_promoted"] is True
