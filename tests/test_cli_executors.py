"""Tests for ``happyranch executors register`` CLI verb — THR-052 PR-3.

Follows the argparse-only + mock OpcClient pattern from test_cli_artifacts.py.
"""
from __future__ import annotations

import argparse
import sys
from unittest.mock import MagicMock, patch

import pytest
from cli.main import build_parser


def _parse(*args: str) -> argparse.Namespace:
    return build_parser().parse_args(list(args))


# ── argparse parsing ─────────────────────────────────────────────────────────


def test_executors_register_parses_all_required_args() -> None:
    # argv_template elements are plain strings (no leading dashes).
    ns = _parse(
        "executors", "register",
        "--org", "demo",
        "--token", "hrreg_abc123",
        "--exec-command", "my-cli",
        "--argv-template", "{prompt}", "option1", "{workspace}",
    )
    assert ns.command == "executors"
    assert ns.executors_command == "register"
    assert ns.org == "demo"
    assert ns.token == "hrreg_abc123"
    assert ns.exec_command == "my-cli"
    assert ns.argv_template == ["{prompt}", "option1", "{workspace}"]
    assert ns.adapter == "pi"  # default


def test_executors_register_parses_adapter_override() -> None:
    ns = _parse(
        "executors", "register",
        "--org", "demo",
        "--token", "hrreg_xyz",
        "--exec-command", "my-cli",
        "--argv-template", "{prompt}",
        "--adapter", "codex",
    )
    assert ns.adapter == "codex"


def test_executors_register_requires_token() -> None:
    with pytest.raises(SystemExit):
        _parse(
            "executors", "register",
            "--org", "demo",
            "--exec-command", "my-cli",
            "--argv-template", "{prompt}",
        )


def test_executors_register_requires_org() -> None:
    with pytest.raises(SystemExit):
        _parse(
            "executors", "register",
            "--token", "hrreg_abc",
            "--exec-command", "my-cli",
            "--argv-template", "{prompt}",
        )


def test_executors_register_requires_command() -> None:
    with pytest.raises(SystemExit):
        _parse(
            "executors", "register",
            "--org", "demo",
            "--token", "hrreg_abc",
            "--argv-template", "{prompt}",
        )


def test_executors_register_no_argv_template_defaults_to_empty() -> None:
    ns = _parse(
        "executors", "register",
        "--org", "demo",
        "--token", "hrreg_abc",
        "--exec-command", "my-cli",
    )
    assert ns.argv_template == []


def test_executors_register_adapter_only_valid_choices() -> None:
    # Valid choices: claude, codex, opencode, pi
    ns = _parse(
        "executors", "register",
        "--org", "demo",
        "--token", "hrreg_abc",
        "--exec-command", "my-cli",
        "--argv-template", "{prompt}",
        "--adapter", "claude",
    )
    assert ns.adapter == "claude"

    with pytest.raises(SystemExit):
        _parse(
            "executors", "register",
            "--org", "demo",
            "--token", "hrreg_abc",
            "--exec-command", "my-cli",
            "--argv-template", "{prompt}",
            "--adapter", "invalid",
        )


# ── cmd_executors_register handler ────────────────────────────────────────


def test_cmd_executors_register_happy_path(capsys) -> None:
    """Conformance check-ins succeed, register succeeds."""
    from cli.commands.executors import cmd_executors_register

    fake = MagicMock()
    # Mock POST responses: 3 check-ins (200) + register (200)
    fake.post.side_effect = [
        # workspace_access
        MagicMock(
            status_code=200,
            json=lambda: {
                "step_id": "workspace_access",
                "arrived": True,
                "pending": ["loopback_reachable", "cli_callback"],
                "all_complete": False,
            },
        ),
        # loopback_reachable
        MagicMock(
            status_code=200,
            json=lambda: {
                "step_id": "loopback_reachable",
                "arrived": True,
                "pending": ["cli_callback"],
                "all_complete": False,
            },
        ),
        # cli_callback
        MagicMock(
            status_code=200,
            json=lambda: {
                "step_id": "cli_callback",
                "arrived": True,
                "pending": [],
                "all_complete": True,
            },
        ),
        # register
        MagicMock(
            status_code=200,
            json=lambda: {
                "name": "my-exec",
                "kind": "custom",
                "adapter_id": "pi",
                "command": "my-cli",
                "argv_template": ["{prompt}"],
            },
        ),
    ]

    with patch("cli.commands.executors.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["demo"]):
        args = argparse.Namespace(
            org="demo",
            token="hrreg_abc123",
            exec_command="my-cli",
            argv_template=["{prompt}"],
            adapter="pi",
        )
        cmd_executors_register(args)

    out = capsys.readouterr().out
    assert "workspace_access" in out
    assert "loopback_reachable" in out
    assert "cli_callback" in out
    assert "registered: my-exec" in out
    assert "adapter   : pi" in out


def test_cmd_executors_register_rejects_non_hrreg_token(capsys) -> None:
    """Token must start with hrreg_."""
    from cli.commands.executors import cmd_executors_register

    fake = MagicMock()
    with patch("cli.commands.executors.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["demo"]):
        args = argparse.Namespace(
            org="demo",
            token="bad_token",
            exec_command="my-cli",
            argv_template=["{prompt}"],
            adapter="pi",
        )
        with pytest.raises(SystemExit):
            cmd_executors_register(args)

    captured = capsys.readouterr()
    assert "must start with 'hrreg_'" in captured.err


def test_cmd_executors_register_rejects_missing_token(capsys) -> None:
    from cli.commands.executors import cmd_executors_register

    fake = MagicMock()
    with patch("cli.commands.executors.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["demo"]):
        args = argparse.Namespace(
            org="demo",
            token="",
            exec_command="my-cli",
            argv_template=["{prompt}"],
            adapter="pi",
        )
        with pytest.raises(SystemExit):
            cmd_executors_register(args)


def test_cmd_executors_register_rejects_empty_argv_template(capsys) -> None:
    from cli.commands.executors import cmd_executors_register

    fake = MagicMock()
    with patch("cli.commands.executors.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["demo"]):
        args = argparse.Namespace(
            org="demo",
            token="hrreg_abc123",
            exec_command="my-cli",
            argv_template=[],
            adapter="pi",
        )
        with pytest.raises(SystemExit):
            cmd_executors_register(args)


def test_cmd_executors_register_checkin_http_error(capsys) -> None:
    """Check-in returns 401 -> exit 1."""
    from cli.commands.executors import cmd_executors_register

    fake = MagicMock()
    fake.post.return_value = MagicMock(
        status_code=401,
        json=lambda: {"detail": "invalid or expired registration token"},
    )

    with patch("cli.commands.executors.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["demo"]):
        args = argparse.Namespace(
            org="demo",
            token="hrreg_expired",
            exec_command="my-cli",
            argv_template=["{prompt}"],
            adapter="pi",
        )
        with pytest.raises(SystemExit):
            cmd_executors_register(args)


def test_cmd_executors_register_checkin_connection_error(capsys) -> None:
    """Connection error during check-in -> exit 1."""
    from cli.commands.executors import cmd_executors_register

    fake = MagicMock()
    fake.post.side_effect = ConnectionError("refused")

    with patch("cli.commands.executors.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["demo"]):
        args = argparse.Namespace(
            org="demo",
            token="hrreg_abc123",
            exec_command="my-cli",
            argv_template=["{prompt}"],
            adapter="pi",
        )
        with pytest.raises(SystemExit):
            cmd_executors_register(args)

    err = capsys.readouterr().err
    assert "connection error" in err.lower() or "refused" in err.lower()


def test_cmd_executors_register_register_http_error(capsys) -> None:
    """Check-ins pass, but register returns 409 -> exit 1."""
    from cli.commands.executors import cmd_executors_register

    fake = MagicMock()
    fake.post.side_effect = [
        # workspace_access
        MagicMock(status_code=200,
                   json=lambda: {"step_id": "workspace_access", "arrived": True,
                                 "pending": ["loopback_reachable", "cli_callback"],
                                 "all_complete": False}),
        # loopback_reachable
        MagicMock(status_code=200,
                   json=lambda: {"step_id": "loopback_reachable", "arrived": True,
                                 "pending": ["cli_callback"],
                                 "all_complete": False}),
        # cli_callback
        MagicMock(status_code=200,
                   json=lambda: {"step_id": "cli_callback", "arrived": True,
                                 "pending": [], "all_complete": True}),
        # register -> 409
        MagicMock(
            status_code=409,
            json=lambda: {"detail": "Custom executor profile 'my-exec' is already registered"},
        ),
    ]

    with patch("cli.commands.executors.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["demo"]):
        args = argparse.Namespace(
            org="demo",
            token="hrreg_abc123",
            exec_command="my-cli",
            argv_template=["{prompt}"],
            adapter="pi",
        )
        with pytest.raises(SystemExit):
            cmd_executors_register(args)

    err = capsys.readouterr().err
    assert "409" in err or "rejected" in err


def test_cmd_executors_register_filters_empty_argv_strings(capsys) -> None:
    """Extra spaces in argv_template are filtered out."""
    from cli.commands.executors import cmd_executors_register

    fake = MagicMock()
    fake.post.side_effect = [
        MagicMock(status_code=200,
                   json=lambda: {"step_id": "workspace_access", "arrived": True,
                                 "pending": ["loopback_reachable", "cli_callback"],
                                 "all_complete": False}),
        MagicMock(status_code=200,
                   json=lambda: {"step_id": "loopback_reachable", "arrived": True,
                                 "pending": ["cli_callback"],
                                 "all_complete": False}),
        MagicMock(status_code=200,
                   json=lambda: {"step_id": "cli_callback", "arrived": True,
                                 "pending": [], "all_complete": True}),
        MagicMock(
            status_code=200,
            json=lambda: {
                "name": "my-exec",
                "kind": "custom",
                "adapter_id": "pi",
                "command": "my-cli",
                "argv_template": ["{prompt}", "--verbose"],
            },
        ),
    ]

    with patch("cli.commands.executors.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["demo"]):
        # Extra spaces and empty elements
        args = argparse.Namespace(
            org="demo",
            token="hrreg_abc123",
            exec_command="my-cli",
            argv_template=["{prompt}", "", "--verbose"],
            adapter="pi",
        )
        cmd_executors_register(args)

    out = capsys.readouterr().out
    assert "registered: my-exec" in out


def test_cmd_executors_register_register_connection_error(capsys) -> None:
    """Check-ins pass, register connection fails -> exit 1."""
    from cli.commands.executors import cmd_executors_register

    fake = MagicMock()
    fake.post.side_effect = [
        MagicMock(status_code=200,
                   json=lambda: {"step_id": "workspace_access", "arrived": True,
                                 "pending": ["loopback_reachable", "cli_callback"],
                                 "all_complete": False}),
        MagicMock(status_code=200,
                   json=lambda: {"step_id": "loopback_reachable", "arrived": True,
                                 "pending": ["cli_callback"],
                                 "all_complete": False}),
        MagicMock(status_code=200,
                   json=lambda: {"step_id": "cli_callback", "arrived": True,
                                 "pending": [], "all_complete": True}),
        ConnectionError("register refused"),
    ]

    with patch("cli.commands.executors.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["demo"]):
        args = argparse.Namespace(
            org="demo",
            token="hrreg_abc123",
            exec_command="my-cli",
            argv_template=["{prompt}"],
            adapter="pi",
        )
        with pytest.raises(SystemExit):
            cmd_executors_register(args)

    err = capsys.readouterr().err
    assert "registration failed" in err.lower() or "refused" in err.lower()
