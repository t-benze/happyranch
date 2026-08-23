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
            "total_snapshot_bytes": 2048, "route_label_count": 3,
        },
        "after": {
            "row_count": 1, "oldest_captured_at": "2026-07-04T00:00:00+00:00",
            "newest_captured_at": "2026-07-04T00:00:00+00:00",
            "page_count": 6, "freelist_count": 0, "db_bytes": 2048, "wal_bytes": 0,
            "total_snapshot_bytes": 1024, "route_label_count": 3,
        },
        "cutoff": "2026-07-04T00:00:00+00:00",
        "pruned_rows": 1,
        "checkpoint": {"busy": 0, "log_frames": 0, "checkpointed_frames": 0},
        "integrity_check_before_vacuum": "ok",
        "integrity_check_after_vacuum": "ok",
        "vacuum": "ok",
        "duration_seconds": 0.01,
    }


def test_metrics_maintenance_parses_confirm_flag() -> None:
    args = build_parser().parse_args(["metrics", "maintenance", "--confirm-quiescent"])
    assert args.command == "metrics"
    assert args.metrics_command == "maintenance"
    assert args.confirm_quiescent is True


def test_metrics_maintenance_flag_defaults_false() -> None:
    args = build_parser().parse_args(["metrics", "maintenance"])
    assert args.confirm_quiescent is False


def test_metrics_maintenance_sends_confirm_quiescent() -> None:
    client = MagicMock()
    client.post.return_value = _response(_report(), 200)

    args = build_parser().parse_args(["metrics", "maintenance", "--confirm-quiescent"])

    with patch("cli.commands.metrics._client", return_value=client):
        from cli.commands.metrics import cmd_metrics_maintenance
        cmd_metrics_maintenance(args)

    client.post.assert_called_once_with(
        "/api/v1/metrics/maintenance",
        json={"confirm_quiescent": True},
    )


def test_metrics_maintenance_prints_report(capsys) -> None:
    client = MagicMock()
    client.post.return_value = _response(_report(), 200)

    args = build_parser().parse_args(["metrics", "maintenance", "--confirm-quiescent"])

    with patch("cli.commands.metrics._client", return_value=client):
        from cli.commands.metrics import cmd_metrics_maintenance
        cmd_metrics_maintenance(args)

    out = capsys.readouterr().out
    assert "metrics maintenance complete:" in out
    assert "pruned rows:       1" in out
    assert "vacuum:            ok" in out
    assert "route-label count: 3 -> 3" in out


def test_metrics_maintenance_confirmation_required_message(capsys) -> None:
    from cli.commands.metrics import cmd_metrics_maintenance
    client = MagicMock()
    client.post.return_value = _response(
        {"detail": {"code": "confirmation_required", "detail": "confirm"}}, 409
    )
    args = build_parser().parse_args(["metrics", "maintenance"])
    with patch("cli.commands.metrics._client", return_value=client), pytest.raises(SystemExit):
        cmd_metrics_maintenance(args)
    assert "explicit confirmation" in capsys.readouterr().out


def test_metrics_maintenance_not_quiescent_message(capsys) -> None:
    from cli.commands.metrics import cmd_metrics_maintenance
    client = MagicMock()
    client.post.return_value = _response(
        {
            "detail": {
                "code": "not_quiescent",
                "nonterminal_tasks": 2,
                "running_jobs": 0,
                "active_executor_sessions": 1,
            }
        },
        409,
    )
    args = build_parser().parse_args(["metrics", "maintenance", "--confirm-quiescent"])
    with patch("cli.commands.metrics._client", return_value=client), pytest.raises(SystemExit):
        cmd_metrics_maintenance(args)
    out = capsys.readouterr().out
    assert "not quiescent" in out
    assert "2 nonterminal tasks" in out
    assert "1 active executor sessions" in out


def test_metrics_maintenance_failure_message(capsys) -> None:
    from cli.commands.metrics import cmd_metrics_maintenance
    client = MagicMock()
    client.post.return_value = _response(
        {"detail": {"code": "maintenance_failed", "detail": "PRAGMA integrity_check"}},
        500,
    )
    args = build_parser().parse_args(["metrics", "maintenance", "--confirm-quiescent"])
    with patch("cli.commands.metrics._client", return_value=client), pytest.raises(SystemExit):
        cmd_metrics_maintenance(args)
    out = capsys.readouterr().out
    assert "failed" in out
    assert "fresh explicit invocation" in out
