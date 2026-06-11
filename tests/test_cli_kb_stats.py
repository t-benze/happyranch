"""Tests for the ``happyranch kb stats`` CLI subcommand.

Mirrors the argparse-only + ``cmd_*`` mock pattern used in
``tests/test_cli_tokens.py`` — we exercise parser wiring directly and stub
out :class:`OpcClient` for the command-handler test.
"""
from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

from cli.commands.kb import cmd_kb_stats
from cli.main import build_parser


def _parse(*args: str) -> argparse.Namespace:
    return build_parser().parse_args(list(args))


def test_kb_stats_subcommand_parses():
    ns = _parse("kb", "stats", "--org", "myorg")
    assert ns.kb_command == "stats"
    assert ns.org == "myorg"
    assert ns.func is cmd_kb_stats


def test_cmd_kb_stats_renders_tally(capsys):
    fake = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "entries": [
            {"slug": "high", "view_count": 3, "last_viewed_at": "2026-06-10T12:00:00+00:00"},
            {"slug": "low", "view_count": 1, "last_viewed_at": "2026-06-09T08:00:00+00:00"},
        ]
    }
    fake.get.return_value = resp
    with patch("cli.commands.kb.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["myorg"]):
        cmd_kb_stats(argparse.Namespace(org="myorg"))
    fake.get.assert_called_once_with("/api/v1/orgs/myorg/kb/stats")
    out = capsys.readouterr().out
    assert "high" in out
    assert "3" in out
    assert "low" in out


def test_cmd_kb_stats_handles_empty(capsys):
    fake = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"entries": []}
    fake.get.return_value = resp
    with patch("cli.commands.kb.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["myorg"]):
        cmd_kb_stats(argparse.Namespace(org="myorg"))
    assert "no views recorded" in capsys.readouterr().out
