"""Tests for custom-CLI connection-status CLI support."""
from __future__ import annotations

import argparse
import time
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


def test_custom_cli_forget_parses_profile_name() -> None:
    args = build_parser().parse_args(["custom-cli", "forget", "my-cli"])

    assert args.command == "custom-cli"
    assert args.custom_cli_command == "forget"
    assert args.profile_name == "my-cli"


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
    clock = [0.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])

    def advance_clock(seconds: float) -> None:
        clock[0] += seconds

    with patch("cli.commands.runtime.OpcClient.from_env", return_value=client), \
         patch("cli.commands.runtime.time.sleep", side_effect=advance_clock) as sleep, \
         pytest.raises(SystemExit, match="1"):
        runtime.cmd_custom_cli_status(_args(wait=True))

    sleep.assert_called_once_with(2)
    assert "still pending" in capsys.readouterr().err


def test_custom_cli_status_wait_bounds_slow_successful_status_requests(monkeypatch) -> None:
    """The per-request timeout must share one --wait wall-clock budget."""
    from cli.commands import runtime

    client = MagicMock()

    def slow_pending_status(*args, timeout: float = 0.25, **kwargs) -> MagicMock:
        time.sleep(timeout)
        return _response({
            "wrapper_destination": "/runtime/adapters/my-cli-adapter",
            "operation_id": "op-123",
            "profile_state": None,
            "reason": None,
        })

    client.get.side_effect = slow_pending_status
    monkeypatch.setattr(runtime, "_WAIT_SECONDS", 0.1)
    monkeypatch.setattr(runtime, "_POLL_INTERVAL_SECONDS", 0.01)

    started = time.monotonic()
    with patch("cli.commands.runtime.OpcClient.from_env", return_value=client), \
         pytest.raises(SystemExit, match="1"):
        runtime.cmd_custom_cli_status(_args(wait=True))
    elapsed = time.monotonic() - started

    assert elapsed <= runtime._WAIT_SECONDS + 0.05
    assert client.get.call_args.kwargs["timeout"] <= runtime._WAIT_SECONDS


@pytest.mark.parametrize("profile_state", [None, "planned", "committed"])
def test_custom_cli_forget_refuses_nonfailed_status_without_post(capsys, profile_state) -> None:
    from cli.commands.runtime import cmd_custom_cli_forget

    client = MagicMock()
    client.get.return_value = _response({
        "wrapper_destination": "/runtime/adapters/my-cli-adapter",
        "operation_id": "op-123",
        "profile_state": profile_state,
        "reason": None,
    })

    with patch("cli.commands.runtime.OpcClient.from_env", return_value=client), \
         pytest.raises(SystemExit, match="1"):
        cmd_custom_cli_forget(argparse.Namespace(profile_name="my-cli"))

    assert f"profile_state is '{profile_state or 'none'}', not 'failed'" in capsys.readouterr().err
    client.post.assert_not_called()


def test_custom_cli_forget_posts_failed_operation_and_confirms_cleanup(capsys) -> None:
    from cli.commands.runtime import cmd_custom_cli_forget

    client = MagicMock()
    client.get.return_value = _response({
        "wrapper_destination": "/runtime/adapters/my-cli-adapter",
        "operation_id": "op-123",
        "profile_state": "failed",
        "reason": "probe failed",
    })
    client.post.return_value = _response({"operation_id": "op-123", "status": "forgotten"})

    with patch("cli.commands.runtime.OpcClient.from_env", return_value=client):
        cmd_custom_cli_forget(argparse.Namespace(profile_name="my-cli"))

    client.post.assert_called_once_with("/api/v1/runtime/custom-cli/op-123/forget")
    output = capsys.readouterr().out
    assert "forgot failed custom-CLI connection for my-cli" in output
    assert "wrapper file removed or already absent" in output
