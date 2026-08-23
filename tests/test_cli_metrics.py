"""Tests for the `happyranch metrics maintenance` CLI command."""
from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest

from cli.main import build_parser


def _response(body: dict[str, object], status_code: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = body
    response.text = "error body"
    return response


def _report() -> dict[str, object]:
    return {
        "before": {
            "row_count": 2, "oldest_captured_at": "2026-06-01T00:00:00+00:00",
            "newest_captured_at": "2026-07-04T00:00:00+00:00",
            "page_count": 10, "freelist_count": 2, "db_bytes": 4096, "wal_bytes": 0,
        },
        "after": {
            "row_count": 1, "oldest_captured_at": "2026-07-04T00:00:00+00:00",
            "newest_captured_at": "2026-07-04T00:00:00+00:00",
            "page_count": 6, "freelist_count": 0, "db_bytes": 2048, "wal_bytes": 0,
        },
        "cutoff": "2026-07-04T00:00:00+00:00",
        "pruned_rows": 1,
        "checkpoint": {"busy": 0, "log_frames": 0, "checkpointed_frames": 0},
        "integrity_check_before_vacuum": "ok",
        "integrity_check_after_vacuum": "ok",
        "duration_seconds": 0.01,
    }


def test_metrics_maintenance_parses_confirm_flag() -> None:
    args = build_parser().parse_args(["metrics", "maintenance", "--confirm-quiescent"])
    assert args.command == "metrics"
    assert args.metrics_command == "maintenance"
    assert args.confirm_quiescent is True


def test_metrics_maintenance_defaults_confirm_false() -> None:
    args = build_parser().parse_args(["metrics", "maintenance"])
    assert args.confirm_quiescent is False


def test_cmd_metrics_maintenance_posts_confirmation_and_prints_report(capsys) -> None:
    from cli.commands.metrics import cmd_metrics_maintenance

    client = MagicMock()
    client.post.return_value = _response(_report())

    with patch("cli.commands.metrics.OpcClient.from_env", return_value=client):
        cmd_metrics_maintenance(argparse.Namespace(confirm_quiescent=True))

    client.post.assert_called_once_with(
        "/api/v1/metrics/maintenance", json={"confirm_quiescent": True}
    )
    output = capsys.readouterr().out
    assert "metrics maintenance complete:" in output
    assert "pruned rows:       1" in output
    assert "integrity (pre):   ok" in output
    assert "integrity (post):  ok" in output
    assert "rows:              2 -> 1" in output


def test_cmd_metrics_maintenance_sends_false_when_not_confirmed(capsys) -> None:
    from cli.commands.metrics import cmd_metrics_maintenance

    client = MagicMock()
    client.post.return_value = _response(_report())

    with patch("cli.commands.metrics.OpcClient.from_env", return_value=client):
        cmd_metrics_maintenance(argparse.Namespace(confirm_quiescent=False))

    client.post.assert_called_once_with(
        "/api/v1/metrics/maintenance", json={"confirm_quiescent": False}
    )


def test_cmd_metrics_maintenance_refusal_prints_friendly_error(capsys) -> None:
    from cli.commands.metrics import cmd_metrics_maintenance

    client = MagicMock()
    client.post.return_value = _response(
        {
            "detail": {
                "code": "not_quiescent",
                "detail": "not quiescent",
                "nonterminal_tasks": 1,
                "running_jobs": 0,
                "active_executor_sessions": 2,
            }
        },
        status_code=409,
    )

    with patch("cli.commands.metrics.OpcClient.from_env", return_value=client), \
         pytest.raises(SystemExit, match="1"):
        cmd_metrics_maintenance(argparse.Namespace(confirm_quiescent=True))

    output = capsys.readouterr().out
    assert "not quiescent" in output
    assert "1 nonterminal tasks" in output
    assert "2 active executor sessions" in output
