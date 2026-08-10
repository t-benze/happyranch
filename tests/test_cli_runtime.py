"""Tests for custom-CLI connection-status CLI support."""
from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest

from cli.main import build_parser


def _args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {"intended_profile_name": "my-cli", "wait": False}
    values.update(overrides)
    return argparse.Namespace(**values)


def _response(body: dict[str, object], status_code: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = body
    response.text = "error body"
    return response


def test_custom_cli_status_parses_profile_name_and_wait_flag() -> None:
    args = build_parser().parse_args(["custom-cli", "status", "my-cli", "--wait"])

    assert args.command == "custom-cli"
    assert args.custom_cli_command == "status"
    assert args.intended_profile_name == "my-cli"
    assert args.wait is True


def test_custom_cli_status_prints_committed_profile(capsys) -> None:
    from cli.commands.runtime import cmd_custom_cli_status

    client = MagicMock()
    client.get.return_value = _response({
        "wrapper_destination": "/runtime/adapters/my-cli-adapter",
        "operation_id": "op-123",
        "profile_state": "committed",
        "reason": None,
    })

    with patch("cli.commands.runtime.OpcClient.from_env", return_value=client):
        cmd_custom_cli_status(_args())

    client.get.assert_called_once_with(
        "/api/v1/runtime/custom-cli/status", params={"intended_profile_name": "my-cli"}
    )
    output = capsys.readouterr().out
    assert "profile_state: committed" in output
    assert "profile_name: my-cli" in output
    assert "wrapper_destination: /runtime/adapters/my-cli-adapter" in output


def test_custom_cli_status_prints_failure_reason(capsys) -> None:
    from cli.commands.runtime import cmd_custom_cli_status

    client = MagicMock()
    client.get.return_value = _response({
        "wrapper_destination": "/runtime/adapters/my-cli-adapter",
        "operation_id": "op-123",
        "profile_state": "failed",
        "reason": "conformance probe failed",
    })

    with patch("cli.commands.runtime.OpcClient.from_env", return_value=client):
        cmd_custom_cli_status(_args())

    output = capsys.readouterr().out
    assert "profile_state: failed" in output
    assert "reason: conformance probe failed" in output
    assert "wrapper_destination: /runtime/adapters/my-cli-adapter" in output


def test_custom_cli_status_waits_for_terminal_result(capsys) -> None:
    from cli.commands.runtime import cmd_custom_cli_status

    client = MagicMock()
    client.get.side_effect = [
        _response({"wrapper_destination": "/runtime/adapters/my-cli-adapter", "operation_id": "op-123", "profile_state": None, "reason": None}),
        _response({"wrapper_destination": "/runtime/adapters/my-cli-adapter", "operation_id": "op-123", "profile_state": "committed", "reason": None}),
    ]

    with patch("cli.commands.runtime.OpcClient.from_env", return_value=client), \
         patch("cli.commands.runtime.time.sleep") as sleep:
        cmd_custom_cli_status(_args(wait=True))

    sleep.assert_called_once_with(2)
    assert client.get.call_count == 2
    assert "profile_state: committed" in capsys.readouterr().out


def test_custom_cli_status_wait_exits_nonzero_when_still_pending(capsys, monkeypatch) -> None:
    from cli.commands import runtime

    client = MagicMock()
    client.get.return_value = _response({
        "wrapper_destination": "/runtime/adapters/my-cli-adapter",
        "operation_id": "op-123",
        "profile_state": None,
        "reason": None,
    })
    monkeypatch.setattr(runtime, "_WAIT_SECONDS", 2)
    monkeypatch.setattr(runtime, "_POLL_INTERVAL_SECONDS", 2)

    with patch("cli.commands.runtime.OpcClient.from_env", return_value=client), \
         patch("cli.commands.runtime.time.sleep") as sleep, \
         pytest.raises(SystemExit, match="1"):
        runtime.cmd_custom_cli_status(_args(wait=True))

    sleep.assert_called_once_with(2)
    assert "still pending" in capsys.readouterr().err
