"""Tests for ``happyranch executor-binaries`` CLI verbs — THR-085.

Follows the argparse-only + mock OpcClient pattern from test_cli_artifacts.py
and test_cli_executors.py.
"""
from __future__ import annotations

import argparse
import shlex
import sys
from unittest.mock import MagicMock, patch

import pytest
from cli.main import build_parser


def _parse(*args: str) -> argparse.Namespace:
    return build_parser().parse_args(list(args))


# ── argparse parsing ─────────────────────────────────────────────────────────


def test_executor_binaries_register_parses_required_args() -> None:
    ns = _parse(
        "executor-binaries", "register",
        "claude",
        "--path", "/opt/homebrew/bin/claude",
    )
    assert ns.command == "executor-binaries"
    assert ns.executor_binaries_command == "register"
    assert ns.kind == "claude"
    assert ns.path == "/opt/homebrew/bin/claude"


def test_executor_binaries_register_requires_kind() -> None:
    with pytest.raises(SystemExit):
        _parse("executor-binaries", "register", "--path", "/some/path")


def test_executor_binaries_register_requires_path() -> None:
    with pytest.raises(SystemExit):
        _parse("executor-binaries", "register", "claude")


def test_executor_binaries_list_parses() -> None:
    ns = _parse("executor-binaries", "list")
    assert ns.command == "executor-binaries"
    assert ns.executor_binaries_command == "list"


# ── cmd_executor_binaries_register ────────────────────────────────────────


def test_cmd_executor_binaries_register_happy_path(capsys) -> None:
    """Successful register prints the registered kind + path."""
    from cli.commands.executor_binaries import cmd_executor_binaries_register

    fake = MagicMock()
    fake.post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"kind": "claude", "path": "/opt/bin/claude", "valid": True},
    )

    with patch(
        "cli.commands.executor_binaries.OpcClient.from_env", return_value=fake
    ):
        args = argparse.Namespace(
            kind="claude",
            path="/opt/bin/claude",
        )
        cmd_executor_binaries_register(args)

    fake.post.assert_called_once_with(
        "/api/v1/executor-binaries/register",
        json={"kind": "claude", "path": "/opt/bin/claude"},
    )
    out = capsys.readouterr().out
    assert "claude" in out
    assert "/opt/bin/claude" in out
    assert "valid" in out


def test_cmd_executor_binaries_register_rejects_relative_path(capsys) -> None:
    """Relative --path exits 1 with clear stderr."""
    from cli.commands.executor_binaries import cmd_executor_binaries_register

    with pytest.raises(SystemExit):
        args = argparse.Namespace(kind="claude", path="relative/path")
        cmd_executor_binaries_register(args)

    err = capsys.readouterr().err
    assert "absolute" in err
    assert "relative/path" in err


def test_cmd_executor_binaries_register_daemon_unreachable(capsys) -> None:
    """When the daemon is unreachable, exits 1 with a clear message."""
    from cli.commands.executor_binaries import cmd_executor_binaries_register

    fake = MagicMock()
    fake.post.side_effect = RuntimeError("connection refused")

    with patch(
        "cli.commands.executor_binaries.OpcClient.from_env", return_value=fake
    ):
        with pytest.raises(SystemExit):
            args = argparse.Namespace(kind="claude", path="/opt/bin/claude")
            cmd_executor_binaries_register(args)

    err = capsys.readouterr().err
    assert "failed to reach daemon" in err.lower()


def test_cmd_executor_binaries_register_422_validation_error(capsys) -> None:
    """422 from the daemon exits 1 with the detail message."""
    from cli.commands.executor_binaries import cmd_executor_binaries_register

    fake = MagicMock()
    fake.post.return_value = MagicMock(
        status_code=422,
        json=lambda: {"detail": "path does not exist: /nonexistent"},
    )

    with patch(
        "cli.commands.executor_binaries.OpcClient.from_env", return_value=fake
    ):
        with pytest.raises(SystemExit):
            args = argparse.Namespace(kind="claude", path="/nonexistent")
            cmd_executor_binaries_register(args)

    err = capsys.readouterr().err
    assert "path does not exist: /nonexistent" in err


def test_cmd_executor_binaries_register_unexpected_http_error(capsys) -> None:
    """Non-200, non-422 HTTP response exits 1."""
    from cli.commands.executor_binaries import cmd_executor_binaries_register

    fake = MagicMock()
    fake.post.return_value = MagicMock(
        status_code=500,
        json=lambda: {"detail": "internal error"},
    )

    with patch(
        "cli.commands.executor_binaries.OpcClient.from_env", return_value=fake
    ):
        with pytest.raises(SystemExit):
            args = argparse.Namespace(kind="claude", path="/opt/bin/claude")
            cmd_executor_binaries_register(args)

    err = capsys.readouterr().err
    assert "HTTP 500" in err


# ── cmd_executor_binaries_list ────────────────────────────────────────────


def test_cmd_executor_binaries_list_entries(capsys) -> None:
    """List prints registered entries with validity."""
    from cli.commands.executor_binaries import cmd_executor_binaries_list

    fake = MagicMock()
    fake.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "entries": [
                {"kind": "claude", "path": "/opt/bin/claude", "valid": True},
                {"kind": "pi", "path": "/stale/pi", "valid": False},
            ]
        },
    )

    with patch(
        "cli.commands.executor_binaries.OpcClient.from_env", return_value=fake
    ):
        args = argparse.Namespace()
        cmd_executor_binaries_list(args)

    fake.get.assert_called_once_with("/api/v1/executor-binaries")
    out = capsys.readouterr().out
    assert "claude" in out
    assert "/opt/bin/claude" in out
    assert "valid" in out
    assert "pi" in out
    assert "/stale/pi" in out
    assert "stale" in out


def test_cmd_executor_binaries_list_empty(capsys) -> None:
    """Empty registry prints a clear message."""
    from cli.commands.executor_binaries import cmd_executor_binaries_list

    fake = MagicMock()
    fake.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"entries": []},
    )

    with patch(
        "cli.commands.executor_binaries.OpcClient.from_env", return_value=fake
    ):
        args = argparse.Namespace()
        cmd_executor_binaries_list(args)

    out = capsys.readouterr().out
    assert "no registered" in out.lower()


def test_cmd_executor_binaries_list_daemon_unreachable(capsys) -> None:
    """When the daemon is unreachable, exits 1 with a clear message."""
    from cli.commands.executor_binaries import cmd_executor_binaries_list

    fake = MagicMock()
    fake.get.side_effect = RuntimeError("connection refused")

    with patch(
        "cli.commands.executor_binaries.OpcClient.from_env", return_value=fake
    ):
        with pytest.raises(SystemExit):
            args = argparse.Namespace()
            cmd_executor_binaries_list(args)

    err = capsys.readouterr().err
    assert "failed to reach daemon" in err.lower()


def test_cmd_executor_binaries_list_unexpected_http_error(capsys) -> None:
    """Non-200 response from list exits 1."""
    from cli.commands.executor_binaries import cmd_executor_binaries_list

    fake = MagicMock()
    fake.get.return_value = MagicMock(
        status_code=500,
        json=lambda: {"detail": "oops"},
    )

    with patch(
        "cli.commands.executor_binaries.OpcClient.from_env", return_value=fake
    ):
        with pytest.raises(SystemExit):
            args = argparse.Namespace()
            cmd_executor_binaries_list(args)

    err = capsys.readouterr().err
    assert "HTTP 500" in err


# ── Integration: parser + handler wired together ─────────────────────────


def test_executor_binaries_register_integration_happy_path(capsys) -> None:
    """Full parse + handler: happyranch executor-binaries register claude --path /opt/bin/claude."""
    from cli.commands.executor_binaries import cmd_executor_binaries_register

    fake = MagicMock()
    fake.post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"kind": "claude", "path": "/opt/bin/claude", "valid": True},
    )

    with patch(
        "cli.commands.executor_binaries.OpcClient.from_env", return_value=fake
    ):
        ns = _parse(
            "executor-binaries", "register",
            "claude",
            "--path", "/opt/bin/claude",
        )
        ns.func(ns)

    out = capsys.readouterr().out
    assert "claude" in out
    assert "/opt/bin/claude" in out


def test_executor_binaries_list_integration(capsys) -> None:
    """Full parse + handler: happyranch executor-binaries list."""
    from cli.commands.executor_binaries import cmd_executor_binaries_list

    fake = MagicMock()
    fake.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "entries": [
                {"kind": "claude", "path": "/opt/bin/claude", "valid": True},
            ]
        },
    )

    with patch(
        "cli.commands.executor_binaries.OpcClient.from_env", return_value=fake
    ):
        ns = _parse("executor-binaries", "list")
        ns.func(ns)

    out = capsys.readouterr().out
    assert "claude" in out
    assert "valid" in out
