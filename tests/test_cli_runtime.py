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


def test_custom_cli_retry_parses_profile_name() -> None:
    args = build_parser().parse_args(["custom-cli", "retry", "my-cli"])

    assert args.command == "custom-cli"
    assert args.custom_cli_command == "retry"
    assert args.profile_name == "my-cli"


def test_adapters_remove_parses_adapter_id() -> None:
    args = build_parser().parse_args(["adapters", "remove", "my-adapter"])

    assert args.command == "adapters"
    assert args.adapters_command == "remove"
    assert args.adapter_id == "my-adapter"


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


def test_custom_cli_forget_refuses_a_live_committed_profile_without_post(capsys) -> None:
    """The PR #646 guard keeps a connected profile out of cleanup."""
    from cli.commands.runtime import cmd_custom_cli_forget

    client = MagicMock()
    client.get.return_value = _response({
        "wrapper_destination": "/runtime/adapters/my-cli-adapter",
        "operation_id": "op-live-committed",
        "profile_state": "committed",
        "reason": None,
    })

    with patch("cli.commands.runtime.OpcClient.from_env", return_value=client), \
         pytest.raises(SystemExit, match="1"):
        cmd_custom_cli_forget(argparse.Namespace(profile_name="my-cli"))

    assert "profile_state is 'committed', not 'failed'" in capsys.readouterr().err
    client.post.assert_not_called()


@pytest.mark.parametrize(("wrapper_status", "expected_detail"), [
    ("already_absent", "wrapper file was already absent"),
    ("preserved_changed", "wrapper file was preserved because it changed"),
    ("preserved_unsafe", "wrapper file was preserved because it could not be safely verified"),
])
def test_custom_cli_forget_posts_failed_operation_and_reports_wrapper_outcome(
    capsys, wrapper_status, expected_detail,
) -> None:
    from cli.commands.runtime import cmd_custom_cli_forget

    client = MagicMock()
    client.get.return_value = _response({
        "wrapper_destination": "/runtime/adapters/my-cli-adapter",
        "operation_id": "op-123",
        "profile_state": "failed",
        "reason": "probe failed",
    })
    client.post.return_value = _response({
        "operation_id": "op-123", "status": "forgotten", "wrapper_status": wrapper_status,
    })

    with patch("cli.commands.runtime.OpcClient.from_env", return_value=client):
        cmd_custom_cli_forget(argparse.Namespace(profile_name="my-cli"))

    client.post.assert_called_once_with("/api/v1/runtime/custom-cli/op-123/forget")
    output = capsys.readouterr().out
    assert "cleared failed custom-CLI connection record for my-cli" in output
    assert expected_detail in output


@pytest.mark.parametrize("profile_state", [None, "planned", "committed"])
def test_custom_cli_retry_refuses_nonfailed_status_without_post(capsys, profile_state) -> None:
    from cli.commands.runtime import cmd_custom_cli_retry

    client = MagicMock()
    client.get.return_value = _response({
        "wrapper_destination": "/runtime/adapters/my-cli-adapter",
        "operation_id": "op-123",
        "profile_state": profile_state,
        "reason": None,
    })

    with patch("cli.commands.runtime.OpcClient.from_env", return_value=client), \
         pytest.raises(SystemExit, match="1"):
        cmd_custom_cli_retry(argparse.Namespace(profile_name="my-cli"))

    assert f"profile_state is '{profile_state or 'none'}', not 'failed'" in capsys.readouterr().err
    client.post.assert_not_called()


def test_custom_cli_retry_posts_only_server_resolved_failed_operation(capsys) -> None:
    from cli.commands.runtime import cmd_custom_cli_retry

    client = MagicMock()
    client.get.return_value = _response({
        "wrapper_destination": "/runtime/adapters/my-cli-adapter",
        "operation_id": "op-123",
        "profile_state": "failed",
        "reason": "original failure",
    })
    client.post.return_value = _response({
        "operation_id": "op-123", "profile_state": "committed", "profile_name": "my-cli",
    })

    with patch("cli.commands.runtime.OpcClient.from_env", return_value=client):
        cmd_custom_cli_retry(argparse.Namespace(profile_name="my-cli"))

    client.post.assert_called_once_with("/api/v1/runtime/custom-cli/op-123/retry")
    assert "retry validated and connected custom-CLI profile my-cli" in capsys.readouterr().out


def test_adapters_remove_fetches_fresh_snapshot_then_deletes(capsys) -> None:
    from cli.commands.runtime import cmd_adapters_remove

    client = MagicMock()
    current = {
        "id": "my-adapter",
        "name": "My adapter",
        "executable": "/tmp/my-adapter",
        "executable_hash": "fresh-hash",
        "version": "1.2.3",
        "capabilities": ["workspace"],
        "contract_version": 1,
        "workspace_adapter": "codex",
        "status": "approved",
        "registered_at": "2026-08-13T00:00:00Z",
        "registered_by": "direct-connect",
        "approved_at": "2026-08-13T00:00:00Z",
        "approved_by": "direct-connect",
        "intended_profile_name": "my-profile",
        "dependency_manifest_version": 1,
        "dependencies": [{"executable": "/tmp/child", "sha256": "child-hash"}],
        "eligibility": "ready_to_bind",
    }
    client.get.return_value = _response(current)
    client.request.return_value = _response({"id": "my-adapter", "removed": True, "name": "My adapter"})

    with patch("cli.commands.runtime.OpcClient.from_env", return_value=client):
        cmd_adapters_remove(argparse.Namespace(adapter_id="my-adapter"))

    client.get.assert_called_once_with("/api/v1/runtime/adapters/my-adapter")
    client.request.assert_called_once_with(
        "DELETE",
        "/api/v1/runtime/adapters/my-adapter",
        json={
            "name": "My adapter",
            "executable": "/tmp/my-adapter",
            "executable_hash": "fresh-hash",
            "version": "1.2.3",
            "capabilities": ["workspace"],
            "contract_version": 1,
            "workspace_adapter": "codex",
            "intended_profile_name": "my-profile",
            "dependency_manifest_version": 1,
            "dependencies": [{"executable": "/tmp/child", "sha256": "child-hash"}],
        },
    )
    output = capsys.readouterr().out
    assert "removing adapter my-adapter (My adapter)" in output
    assert "removed adapter my-adapter (My adapter)" in output
