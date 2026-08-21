"""Tests for the org-portability CLI commands (THR-187 Slice A)."""
from __future__ import annotations

import argparse
import json
from unittest.mock import MagicMock, patch

import pytest

from cli.main import build_parser


def _response(body: dict, status_code: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = body
    response.text = "error body"
    return response


def test_parser_preflight() -> None:
    args = build_parser().parse_args(["orgs", "portability-preflight", "family"])
    assert args.command == "orgs"
    assert args.orgs_cmd == "portability-preflight"
    assert args.slug == "family"


def test_parser_reconcile() -> None:
    args = build_parser().parse_args(
        ["orgs", "reconcile-portability", "family", "--from-file", "/tmp/req.json"]
    )
    assert args.orgs_cmd == "reconcile-portability"
    assert args.slug == "family"
    assert args.request_path == "/tmp/req.json"


def test_preflight_prints_ineligible_report(capsys) -> None:
    from cli.commands.runtime import cmd_orgs_portability_preflight

    client = MagicMock()
    client.get.return_value = _response({
        "slug": "family",
        "root": "/rt/orgs/family",
        "eligible": False,
        "classification": {"entries": [], "rejections": [
            {"path": "scripts", "classification": "reject", "reason": "unknown_root"},
        ]},
        "eligibility": {
            "eligible": False,
            "blockers": {
                "tasks": ["T-1"], "active_sessions": 0, "queued_items": 0,
                "pending_thread_invocations": 0, "active_jobs": [],
                "active_dreams": [], "active_work_hours": [], "active_schedules": [],
            },
            "possible_zombies": [{"task_id": "T-Z", "assigned_agent": "dev_agent"}],
        },
    })

    with patch("cli.commands.runtime.OpcClient.from_env", return_value=client):
        cmd_orgs_portability_preflight(argparse.Namespace(slug="family"))

    client.get.assert_called_once_with("/api/v1/orgs/family/portability-preflight")
    output = capsys.readouterr().out
    assert "portability: INELIGIBLE" in output
    assert "reject  scripts  (unknown_root)" in output
    assert "nonterminal tasks: T-1" in output
    assert "possible zombies (reported only — not resolved):" in output
    assert "T-Z" in output


def test_reconcile_reads_file_and_posts(capsys, tmp_path) -> None:
    from cli.commands.runtime import cmd_orgs_reconcile_portability

    request_path = tmp_path / "req.json"
    request_path.write_text(json.dumps({
        "candidate_task_id": "T-Z", "disposition": "cancel",
        "evidence": {"reason": "dead pid"},
    }))

    client = MagicMock()
    client.post.return_value = _response({
        "task_id": "T-Z", "disposition": "cancel",
        "request_hash": "a" * 64,
        "before": {"status": "in_progress"}, "after": {"status": "cancelled"},
    })

    with patch("cli.commands.runtime.OpcClient.from_env", return_value=client):
        cmd_orgs_reconcile_portability(
            argparse.Namespace(slug="family", request_path=str(request_path))
        )

    client.post.assert_called_once_with(
        "/api/v1/orgs/family/reconcile-portability",
        json={"candidate_task_id": "T-Z", "disposition": "cancel",
              "evidence": {"reason": "dead pid"}},
    )
    output = capsys.readouterr().out
    assert "reconciled: T-Z (cancel)" in output
    assert "request_hash: " + "a" * 64 in output


def test_reconcile_requires_absolute_path(capsys, tmp_path) -> None:
    from cli.commands.runtime import cmd_orgs_reconcile_portability

    with pytest.raises(SystemExit, match="1"):
        cmd_orgs_reconcile_portability(
            argparse.Namespace(slug="family", request_path="relative/req.json")
        )
    assert "absolute" in capsys.readouterr().err


# ── Slice B: export / inspect / import-relocation CLI ───────────────────────


def test_parser_export_inspect_import() -> None:
    args = build_parser().parse_args(
        ["orgs", "portability-export", "family", "--from-file", "/tmp/e.json"]
    )
    assert args.orgs_cmd == "portability-export"
    assert args.request_path == "/tmp/e.json"

    args = build_parser().parse_args(
        ["orgs", "portability-inspect", "family", "--from-file", "/tmp/i.json"]
    )
    assert args.orgs_cmd == "portability-inspect"

    args = build_parser().parse_args(
        ["orgs", "portability-import", "family", "--from-file", "/tmp/m.json"]
    )
    assert args.orgs_cmd == "portability-import"


def test_export_command_posts_and_prints(capsys, tmp_path) -> None:
    from cli.commands.runtime import cmd_orgs_portability_export

    req = tmp_path / "export.json"
    req.write_text(json.dumps({
        "archive_path": str(tmp_path / "org.archive"), "trust_acknowledged": True,
    }))
    client = MagicMock()
    client.post.return_value = _response({
        "slug": "family", "archive_digest": "a" * 64,
        "archive_path": str(tmp_path / "org.archive"), "member_count": 3,
        "legacy_skills_quarantined": [{"slug": "qa-scroll-test"}],
    })

    with patch("cli.commands.runtime.OpcClient.from_env", return_value=client):
        cmd_orgs_portability_export(
            argparse.Namespace(slug="family", request_path=str(req))
        )

    client.post.assert_called_once_with(
        "/api/v1/orgs/family/portability-export",
        json={"archive_path": str(tmp_path / "org.archive"),
              "trust_acknowledged": True},
    )
    out = capsys.readouterr().out
    assert "exported: family" in out
    assert "quarantined legacy skill: qa-scroll-test" in out


def test_import_command_requires_target_runtime(capsys, tmp_path) -> None:
    from cli.commands.runtime import cmd_orgs_portability_import

    req = tmp_path / "import.json"
    req.write_text(json.dumps({
        "archive_path": str(tmp_path / "org.archive"),
        "trust_acknowledged": True,
    }))
    with pytest.raises(SystemExit, match="1"):
        cmd_orgs_portability_import(
            argparse.Namespace(slug="family", request_path=str(req))
        )
    assert "target_runtime" in capsys.readouterr().err


def test_archive_request_requires_absolute_path(capsys, tmp_path) -> None:
    from cli.commands.runtime import cmd_orgs_portability_export

    req = tmp_path / "export.json"
    req.write_text(json.dumps({
        "archive_path": "relative/org.archive", "trust_acknowledged": True,
    }))
    with pytest.raises(SystemExit, match="1"):
        cmd_orgs_portability_export(
            argparse.Namespace(slug="family", request_path=str(req))
        )
    assert "absolute" in capsys.readouterr().err
