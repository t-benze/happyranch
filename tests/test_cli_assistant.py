from __future__ import annotations

import pytest

from unittest.mock import MagicMock, patch

from cli.main import build_parser


def test_assistant_bare_aliases_to_attach() -> None:
    parser = build_parser()
    args = parser.parse_args(["assistant"])

    assert args.command == "assistant"
    assert args.assistant_cmd == "attach"


def test_assistant_init_parser() -> None:
    parser = build_parser()
    args = parser.parse_args(["assistant", "init", "--reconfigure"])

    assert args.command == "assistant"
    assert args.assistant_cmd == "init"
    assert args.reconfigure is True


def test_cmd_assistant_status_prints_state(capsys) -> None:
    from cli.main import cmd_assistant_status

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {
        "state": "configured",
        "selected_executor": "codex",
        "workspace_path": "/tmp/rt/system/assistant/workspace",
        "latest_probe_results": [],
    }

    with patch("cli.main.OpcClient.from_env", return_value=fake):
        cmd_assistant_status(MagicMock())

    fake.get.assert_called_once_with("/api/v1/assistant/status")
    out = capsys.readouterr().out
    assert "state: configured" in out
    assert "executor: codex" in out


def test_cmd_assistant_attach_uninitialized_prints_init_hint(capsys) -> None:
    from cli.main import cmd_assistant_attach

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {"state": "uninitialized"}

    with patch("cli.main.OpcClient.from_env", return_value=fake):
        with pytest.raises(SystemExit) as exc:
            cmd_assistant_attach(MagicMock())

    assert exc.value.code == 2
    fake.get.assert_called_once_with("/api/v1/assistant/status")
    out = capsys.readouterr().out
    assert "happyranch assistant init" in out


def test_cmd_assistant_attach_configured_calls_bridge() -> None:
    from cli.main import cmd_assistant_attach

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {"state": "configured"}

    with (
        patch("cli.main.OpcClient.from_env", return_value=fake),
        patch("cli.commands.assistant._run_attach_bridge") as bridge,
    ):
        cmd_assistant_attach(MagicMock())

    fake.get.assert_called_once_with("/api/v1/assistant/status")
    bridge.assert_called_once_with(fake)


def test_cmd_assistant_attach_status_error_exits_one(capsys) -> None:
    from cli.main import cmd_assistant_attach

    fake = MagicMock()
    fake.get.return_value.status_code = 503
    fake.get.return_value.text = "daemon unavailable"

    with patch("cli.main.OpcClient.from_env", return_value=fake):
        with pytest.raises(SystemExit) as exc:
            cmd_assistant_attach(MagicMock())

    assert exc.value.code == 1
    assert "Error (503): daemon unavailable" in capsys.readouterr().out


def test_cmd_assistant_attach_stale_prints_repair_hint(capsys) -> None:
    from cli.main import cmd_assistant_attach

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {"state": "stale_or_broken"}

    with patch("cli.main.OpcClient.from_env", return_value=fake):
        with pytest.raises(SystemExit) as exc:
            cmd_assistant_attach(MagicMock())

    assert exc.value.code == 2
    out = capsys.readouterr().out
    assert "repair" in out
    assert "reconfigure" in out


def test_ws_url_converts_http_to_ws_with_token() -> None:
    from cli.commands.assistant import _ws_url

    client = MagicMock()
    client.base_url = "http://127.0.0.1:4567"
    client.headers = {"Authorization": "Bearer test-token"}

    assert _ws_url(client) == (
        "ws://127.0.0.1:4567/api/v1/assistant/session?token=test-token"
    )


def test_ws_url_converts_https_to_wss_with_token() -> None:
    from cli.commands.assistant import _ws_url

    client = MagicMock()
    client.base_url = "https://example.test:8443"
    client.headers = {"Authorization": "Bearer secure-token"}

    assert _ws_url(client) == (
        "wss://example.test:8443/api/v1/assistant/session?token=secure-token"
    )


def test_cmd_assistant_init_selects_only_passing_executor(
    monkeypatch,
    capsys,
) -> None:
    from cli.main import cmd_assistant_init

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {"state": "uninitialized"}
    fake.post.side_effect = [
        MagicMock(
            status_code=200,
            json=lambda: {
                "probe_results": [
                    {
                        "executor": "claude",
                        "passed": False,
                        "detail": "timeout",
                        "hint": "login",
                    },
                    {"executor": "codex", "passed": True, "command": "codex"},
                ]
            },
        ),
        MagicMock(
            status_code=200,
            json=lambda: {"state": "configured", "selected_executor": "codex"},
        ),
    ]
    monkeypatch.setattr("builtins.input", lambda _: "1")

    with patch("cli.main.OpcClient.from_env", return_value=fake):
        args = MagicMock(repair=False, reconfigure=False)
        cmd_assistant_init(args)

    configure_call = fake.post.call_args_list[1]
    assert configure_call.args[0] == "/api/v1/assistant/configure"
    assert configure_call.kwargs["json"]["selected_executor"] == "codex"
    assert configure_call.kwargs["json"]["probe_results"] == [
        {
            "executor": "claude",
            "passed": False,
            "detail": "timeout",
            "hint": "login",
        },
        {"executor": "codex", "passed": True, "command": "codex"},
    ]
    assert "1. codex" in capsys.readouterr().out


def test_cmd_assistant_init_reprompts_on_invalid_selection(
    monkeypatch,
    capsys,
) -> None:
    from cli.main import cmd_assistant_init

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {"state": "uninitialized"}
    fake.post.side_effect = [
        MagicMock(
            status_code=200,
            json=lambda: {
                "probe_results": [
                    {"executor": "codex", "passed": True, "command": "codex"},
                ]
            },
        ),
        MagicMock(
            status_code=200,
            json=lambda: {"state": "configured", "selected_executor": "codex"},
        ),
    ]
    choices = iter(["nope", "2", "1"])
    monkeypatch.setattr("builtins.input", lambda _: next(choices))

    with patch("cli.main.OpcClient.from_env", return_value=fake):
        args = MagicMock(repair=False, reconfigure=False)
        cmd_assistant_init(args)

    assert "Enter a number from 1 to 1." in capsys.readouterr().out
    configure_call = fake.post.call_args_list[1]
    assert configure_call.kwargs["json"]["selected_executor"] == "codex"


def test_cmd_assistant_init_no_passing_executor_prints_details(
    monkeypatch,
    capsys,
) -> None:
    from cli.main import cmd_assistant_init

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {"state": "uninitialized"}
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {
        "probe_results": [
            {
                "executor": "codex",
                "passed": False,
                "detail": "timed out waiting for ready marker",
                "hint": "run codex login",
            },
        ]
    }
    monkeypatch.setattr("builtins.input", lambda _: "1")

    with patch("cli.main.OpcClient.from_env", return_value=fake):
        args = MagicMock(repair=False, reconfigure=False)
        with pytest.raises(SystemExit) as exc:
            cmd_assistant_init(args)

    assert exc.value.code == 2
    assert fake.post.call_count == 1
    out = capsys.readouterr().out
    assert "No PTY-capable executor passed the HappyRanch probe." in out
    assert "- codex: timed out waiting for ready marker" in out
    assert "hint: run codex login" in out


def test_cmd_assistant_init_ignores_status_passed_without_boolean_passed(
    monkeypatch,
    capsys,
) -> None:
    from cli.main import cmd_assistant_init

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {"state": "uninitialized"}
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {
        "probe_results": [
            {"executor": "claude", "passed": False, "status": "passed"},
        ]
    }
    monkeypatch.setattr("builtins.input", lambda _: "1")

    with patch("cli.main.OpcClient.from_env", return_value=fake):
        args = MagicMock(repair=False, reconfigure=False)
        with pytest.raises(SystemExit) as exc:
            cmd_assistant_init(args)

    assert exc.value.code == 2
    assert fake.post.call_count == 1
    assert "No PTY-capable executor passed" in capsys.readouterr().out
