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
