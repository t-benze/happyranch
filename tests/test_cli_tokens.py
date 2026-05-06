"""Tests for the ``opc tokens`` CLI subcommand.

Mirrors the argparse-only + ``cmd_*`` mock pattern used in
``tests/test_cli.py`` for ``cmd_audit`` — we exercise parser wiring
directly and stub out :class:`OpcClient` for the command-handler tests.
"""
from __future__ import annotations

import argparse
import json as _json
from unittest.mock import MagicMock, patch

import pytest

from src.cli import build_parser


def _parse(*args: str) -> argparse.Namespace:
    return build_parser().parse_args(list(args))


# ── argparse parsing ────────────────────────────────────────────


def test_tokens_subcommand_parses_defaults():
    ns = _parse("tokens", "--org", "myorg")
    assert ns.command == "tokens"
    assert ns.org == "myorg"
    assert ns.task_id is None
    assert ns.agent is None
    assert ns.since is None
    assert ns.limit is None
    assert ns.json is False
    assert ns.by_agent is False
    assert ns.by_task is False


def test_tokens_subcommand_parses_filters():
    ns = _parse(
        "tokens",
        "--org", "myorg",
        "--task-id", "TASK-1",
        "--agent", "dev",
        "--since", "2026-05-01",
        "--limit", "5",
        "--json",
    )
    assert ns.task_id == "TASK-1"
    assert ns.agent == "dev"
    assert ns.since == "2026-05-01"
    assert ns.limit == 5
    assert ns.json is True


def test_tokens_subcommand_parses_by_agent():
    ns = _parse("tokens", "--org", "myorg", "--by-agent")
    assert ns.by_agent is True
    assert ns.by_task is False


def test_tokens_subcommand_parses_by_task():
    ns = _parse("tokens", "--org", "myorg", "--by-task")
    assert ns.by_task is True
    assert ns.by_agent is False


def test_tokens_subcommand_rejects_by_agent_and_by_task_together():
    with pytest.raises(SystemExit):
        _parse("tokens", "--org", "myorg", "--by-agent", "--by-task")


# ── cmd_tokens behaviour ───────────────────────────────────────


def _mock_args(**overrides) -> argparse.Namespace:
    base = dict(
        org="myorg", task_id=None, agent=None, since=None, limit=None,
        by_agent=False, by_task=False, json=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_cmd_tokens_calls_list_when_no_group_by(capsys):
    from src.cli import cmd_tokens

    fake = MagicMock()
    fake.list_tokens.return_value = []
    with patch("src.cli.OpcClient.from_env", return_value=fake), \
         patch("src.cli._fetch_available_orgs", return_value=["myorg"]):
        cmd_tokens(_mock_args())
    fake.list_tokens.assert_called_once()
    fake.aggregate_tokens.assert_not_called()
    # Default limit applied (20) when --limit omitted.
    _, kwargs = fake.list_tokens.call_args
    assert kwargs["limit"] == 20
    assert kwargs["slug"] == "myorg"


def test_cmd_tokens_forwards_explicit_limit_and_filters():
    from src.cli import cmd_tokens

    fake = MagicMock()
    fake.list_tokens.return_value = []
    args = _mock_args(task_id="TASK-7", agent="dev", since="2026-05-01", limit=3)
    with patch("src.cli.OpcClient.from_env", return_value=fake), \
         patch("src.cli._fetch_available_orgs", return_value=["myorg"]):
        cmd_tokens(args)
    fake.list_tokens.assert_called_once_with(
        slug="myorg", task_id="TASK-7", agent="dev",
        since="2026-05-01", limit=3,
    )


def test_cmd_tokens_calls_aggregate_when_by_agent():
    from src.cli import cmd_tokens

    fake = MagicMock()
    fake.aggregate_tokens.return_value = []
    with patch("src.cli.OpcClient.from_env", return_value=fake), \
         patch("src.cli._fetch_available_orgs", return_value=["myorg"]):
        cmd_tokens(_mock_args(by_agent=True))
    fake.aggregate_tokens.assert_called_once_with(
        slug="myorg", group_by="agent",
        task_id=None, agent=None, since=None,
    )
    fake.list_tokens.assert_not_called()


def test_cmd_tokens_calls_aggregate_when_by_task():
    from src.cli import cmd_tokens

    fake = MagicMock()
    fake.aggregate_tokens.return_value = []
    with patch("src.cli.OpcClient.from_env", return_value=fake), \
         patch("src.cli._fetch_available_orgs", return_value=["myorg"]):
        cmd_tokens(_mock_args(by_task=True))
    fake.aggregate_tokens.assert_called_once_with(
        slug="myorg", group_by="task",
        task_id=None, agent=None, since=None,
    )
    fake.list_tokens.assert_not_called()


def test_cmd_tokens_default_view_renders_total_excluding_cache_reads(capsys):
    from src.cli import cmd_tokens

    fake = MagicMock()
    fake.list_tokens.return_value = [
        {
            "id": 1,
            "task_id": "TASK-152",
            "agent": "engineering_head",
            "executor": "claude",
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_tokens": 9999,    # excluded from total
            "reasoning_tokens": 7,
            "created_at": "2026-05-05T14:22:11+00:00",
        },
    ]
    with patch("src.cli.OpcClient.from_env", return_value=fake), \
         patch("src.cli._fetch_available_orgs", return_value=["myorg"]):
        cmd_tokens(_mock_args())
    out = capsys.readouterr().out
    assert "TASK-152" in out
    assert "engineering_head" in out
    assert "claude" in out
    # total = 100 + 50 + 7 = 157, formatted with thousands separator
    assert "157" in out
    # cache reads shown but excluded from total (no '10,156' anywhere)
    assert "9,999" in out


def test_cmd_tokens_empty_default_view_message(capsys):
    from src.cli import cmd_tokens

    fake = MagicMock()
    fake.list_tokens.return_value = []
    with patch("src.cli.OpcClient.from_env", return_value=fake), \
         patch("src.cli._fetch_available_orgs", return_value=["myorg"]):
        cmd_tokens(_mock_args())
    assert "No token usage rows" in capsys.readouterr().out


def test_cmd_tokens_json_flag_dumps_raw_rows(capsys):
    from src.cli import cmd_tokens

    rows = [{"id": 1, "task_id": "T", "agent": "a", "executor": "claude",
             "input_tokens": 5, "output_tokens": 3, "cache_read_tokens": 0,
             "reasoning_tokens": None, "created_at": "2026-05-05T00:00:00+00:00"}]
    fake = MagicMock()
    fake.list_tokens.return_value = rows
    with patch("src.cli.OpcClient.from_env", return_value=fake), \
         patch("src.cli._fetch_available_orgs", return_value=["myorg"]):
        cmd_tokens(_mock_args(json=True))
    parsed = _json.loads(capsys.readouterr().out)
    assert parsed == rows


def test_cmd_tokens_json_flag_dumps_rollup(capsys):
    from src.cli import cmd_tokens

    rollup = [{"agent": "dev", "sessions": 2, "input_tokens": 10,
               "output_tokens": 5, "cache_read_tokens": 1,
               "cache_creation_tokens": 0, "reasoning_tokens": 0}]
    fake = MagicMock()
    fake.aggregate_tokens.return_value = rollup
    with patch("src.cli.OpcClient.from_env", return_value=fake), \
         patch("src.cli._fetch_available_orgs", return_value=["myorg"]):
        cmd_tokens(_mock_args(by_agent=True, json=True))
    parsed = _json.loads(capsys.readouterr().out)
    assert parsed == rollup
