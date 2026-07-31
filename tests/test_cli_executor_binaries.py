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


def test_executor_binaries_register_parses_without_path() -> None:
    """--path is now required; parse fails without it (THR-107 seq155)."""
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


# ── cmd_executor_binaries_register auto-resolve from PATH (THR-085) ──────


def test_cmd_executor_binaries_register_missing_path_gives_error(capsys) -> None:
    """Missing --path -> actionable error + exit 1, no POST (THR-107 seq155)."""
    from cli.commands.executor_binaries import cmd_executor_binaries_register

    fake = MagicMock()

    with patch(
        "cli.commands.executor_binaries.OpcClient.from_env", return_value=fake
    ):
        with pytest.raises(SystemExit):
            args = argparse.Namespace(kind="claude", path=None)
            cmd_executor_binaries_register(args)

    fake.post.assert_not_called()
    err = capsys.readouterr().err
    assert "--path is required" in err
    assert "claude" in err


def test_cmd_executor_binaries_register_not_on_path(capsys) -> None:
    """Missing --path -> actionable error + exit 1 (THR-107 seq155)."""
    from cli.commands.executor_binaries import cmd_executor_binaries_register

    fake = MagicMock()

    with patch(
        "cli.commands.executor_binaries.OpcClient.from_env", return_value=fake
    ):
        with pytest.raises(SystemExit):
            args = argparse.Namespace(kind="codex", path=None)
            cmd_executor_binaries_register(args)

    fake.post.assert_not_called()
    err = capsys.readouterr().err
    assert "--path is required" in err
    assert "codex" in err


def test_cmd_executor_binaries_register_explicit_path_still_works(capsys) -> None:
    """Explicit --path is accepted as override (THR-107 seq155: required)."""
    from cli.commands.executor_binaries import cmd_executor_binaries_register

    fake = MagicMock()
    fake.post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"kind": "pi", "path": "/custom/pi", "valid": True},
    )

    with patch(
        "cli.commands.executor_binaries.OpcClient.from_env", return_value=fake
    ):
        args = argparse.Namespace(kind="pi", path="/custom/pi")
        cmd_executor_binaries_register(args)

    fake.post.assert_called_once_with(
        "/api/v1/executor-binaries/register",
        json={"kind": "pi", "path": "/custom/pi"},
    )
    out = capsys.readouterr().out
    assert "registered" in out
    assert "/custom/pi" in out


# ── cmd_executor_binaries_remove ──────────────────────────────────────────


def test_executor_binaries_remove_parses_required_args() -> None:
    ns = _parse(
        "executor-binaries", "remove",
        "my-custom-cli",
        "--expected-path", "/opt/bin/my-cli",
    )
    assert ns.command == "executor-binaries"
    assert ns.executor_binaries_command == "remove"
    assert ns.kind == "my-custom-cli"
    assert ns.expected_path == "/opt/bin/my-cli"


def test_executor_binaries_remove_requires_kind() -> None:
    with pytest.raises(SystemExit):
        _parse("executor-binaries", "remove", "--expected-path", "/some/path")


def test_executor_binaries_remove_requires_expected_path() -> None:
    with pytest.raises(SystemExit):
        _parse("executor-binaries", "remove", "my-custom-cli")


def test_cmd_executor_binaries_remove_happy_path(capsys) -> None:
    """200 response prints the removed kind."""
    from cli.commands.executor_binaries import cmd_executor_binaries_remove

    fake = MagicMock()
    fake.request.return_value = MagicMock(
        status_code=200,
        json=lambda: {"kind": "my-custom-cli", "removed": True},
    )

    with patch(
        "cli.commands.executor_binaries.OpcClient.from_env", return_value=fake
    ):
        args = argparse.Namespace(kind="my-custom-cli", expected_path="/opt/bin/my-cli")
        cmd_executor_binaries_remove(args)

    fake.request.assert_called_once_with(
        "DELETE",
        "/api/v1/executor-binaries/my-custom-cli",
        json={"expected_name": "my-custom-cli", "expected_path": "/opt/bin/my-cli"},
    )
    out = capsys.readouterr().out
    assert "removed" in out
    assert "my-custom-cli" in out
    assert "/opt/bin/my-cli" in out


def test_cmd_executor_binaries_remove_not_found(capsys) -> None:
    """404 prints a not-found message to stdout (no error exit)."""
    from cli.commands.executor_binaries import cmd_executor_binaries_remove

    fake = MagicMock()
    fake.request.return_value = MagicMock(
        status_code=404,
        json=lambda: {"detail": "Executor kind 'unknown' is not in the binary registry."},
    )

    with patch(
        "cli.commands.executor_binaries.OpcClient.from_env", return_value=fake
    ):
        args = argparse.Namespace(kind="unknown", expected_path="/tmp/unknown")
        cmd_executor_binaries_remove(args)

    out = capsys.readouterr().out
    assert "not found" in out


def test_cmd_executor_binaries_remove_conflict_stale_path(capsys) -> None:
    """409 exits 1 and prints conflict advice."""
    from cli.commands.executor_binaries import cmd_executor_binaries_remove

    fake = MagicMock()
    fake.request.return_value = MagicMock(
        status_code=409,
        json=lambda: {
            "detail": (
                "Stored path for 'my-cli' ('/actual/path') does not match "
                "expected_path ('/old/path'). "
                "The record may have been updated concurrently — refresh and retry."
            ),
        },
    )

    with patch(
        "cli.commands.executor_binaries.OpcClient.from_env", return_value=fake
    ):
        with pytest.raises(SystemExit):
            args = argparse.Namespace(kind="my-cli", expected_path="/old/path")
            cmd_executor_binaries_remove(args)

    err = capsys.readouterr().err
    assert "conflict" in err
    assert "use 'list' to refresh" in err


def test_cmd_executor_binaries_remove_builtin_rejected(capsys) -> None:
    """422 on built-in kind exits 1 with error detail."""
    from cli.commands.executor_binaries import cmd_executor_binaries_remove

    fake = MagicMock()
    fake.request.return_value = MagicMock(
        status_code=422,
        json=lambda: {"detail": "Cannot remove built-in executor kind 'claude'."},
    )

    with patch(
        "cli.commands.executor_binaries.OpcClient.from_env", return_value=fake
    ):
        with pytest.raises(SystemExit):
            args = argparse.Namespace(kind="claude", expected_path="/opt/bin/claude")
            cmd_executor_binaries_remove(args)

    err = capsys.readouterr().err
    assert "Cannot remove built-in executor kind" in err


def test_cmd_executor_binaries_remove_name_mismatch(capsys) -> None:
    """422 on expected_name mismatch exits 1."""
    from cli.commands.executor_binaries import cmd_executor_binaries_remove

    fake = MagicMock()
    fake.request.return_value = MagicMock(
        status_code=422,
        json=lambda: {"detail": "expected_name 'pi' does not match URL kind 'my-cli'."},
    )

    with patch(
        "cli.commands.executor_binaries.OpcClient.from_env", return_value=fake
    ):
        with pytest.raises(SystemExit):
            # The CLI always sends kind==expected_name, so this code path is
            # server-reachable if someone manipulates the HTTP body.
            args = argparse.Namespace(kind="my-cli", expected_path="/opt/bin/pi")
            cmd_executor_binaries_remove(args)

    err = capsys.readouterr().err
    assert "does not match" in err


def test_cmd_executor_binaries_remove_non_absolute_expected_path(capsys) -> None:
    """Non-absolute --expected-path exits 1 before calling the daemon."""
    from cli.commands.executor_binaries import cmd_executor_binaries_remove

    fake = MagicMock()

    with patch(
        "cli.commands.executor_binaries.OpcClient.from_env", return_value=fake
    ):
        with pytest.raises(SystemExit):
            args = argparse.Namespace(kind="my-cli", expected_path="relative/path")
            cmd_executor_binaries_remove(args)

    fake.request.assert_not_called()
    err = capsys.readouterr().err
    assert "absolute" in err
    assert "relative/path" in err


def test_cmd_executor_binaries_remove_daemon_unreachable(capsys) -> None:
    """Transport error exits 1 with clear message."""
    from cli.commands.executor_binaries import cmd_executor_binaries_remove

    fake = MagicMock()
    fake.request.side_effect = RuntimeError("connection refused")

    with patch(
        "cli.commands.executor_binaries.OpcClient.from_env", return_value=fake
    ):
        with pytest.raises(SystemExit):
            args = argparse.Namespace(kind="my-cli", expected_path="/opt/bin/my-cli")
            cmd_executor_binaries_remove(args)

    err = capsys.readouterr().err
    assert "failed to reach daemon" in err.lower()


def test_cmd_executor_binaries_remove_unexpected_http_error(capsys) -> None:
    """Non-200/404/409/422 HTTP response exits 1."""
    from cli.commands.executor_binaries import cmd_executor_binaries_remove

    fake = MagicMock()
    fake.request.return_value = MagicMock(
        status_code=500,
        json=lambda: {"detail": "internal error"},
    )

    with patch(
        "cli.commands.executor_binaries.OpcClient.from_env", return_value=fake
    ):
        with pytest.raises(SystemExit):
            args = argparse.Namespace(kind="my-cli", expected_path="/opt/bin/my-cli")
            cmd_executor_binaries_remove(args)

    err = capsys.readouterr().err
    assert "HTTP 500" in err


def test_executor_binaries_remove_integration_happy_path(capsys) -> None:
    """Full parse + handler: happyranch executor-binaries remove my-cli --expected-path /opt/bin/my-cli."""
    from cli.commands.executor_binaries import cmd_executor_binaries_remove

    fake = MagicMock()
    fake.request.return_value = MagicMock(
        status_code=200,
        json=lambda: {"kind": "my-cli", "removed": True},
    )

    with patch(
        "cli.commands.executor_binaries.OpcClient.from_env", return_value=fake
    ):
        ns = _parse(
            "executor-binaries", "remove",
            "my-cli",
            "--expected-path", "/opt/bin/my-cli",
        )
        ns.func(ns)

    out = capsys.readouterr().out
    assert "removed" in out
    assert "my-cli" in out
    assert "/opt/bin/my-cli" in out


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
