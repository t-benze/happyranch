from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from cli.main import build_parser


def test_assistant_bare_defaults_to_status() -> None:
    parser = build_parser()
    args = parser.parse_args(["assistant"])

    assert args.command == "assistant"
    assert args.assistant_cmd == "status"


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
    }

    with patch("cli.main.OpcClient.from_env", return_value=fake):
        cmd_assistant_status(MagicMock())

    fake.get.assert_called_once_with("/api/v1/assistant/status")
    out = capsys.readouterr().out
    assert "state: configured" in out
    assert "executor: codex" in out


def test_assistant_register_parser() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["assistant", "register", "--from-file", "/tmp/payload.json"]
    )

    assert args.command == "assistant"
    assert args.assistant_cmd == "register"
    assert args.from_file == "/tmp/payload.json"


def test_cmd_assistant_init_posts_init_and_prints_next_steps(
    monkeypatch,
    capsys,
) -> None:
    from cli.main import cmd_assistant_init

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {
        "state": "uninitialized",
        "workspace_path": "/tmp/rt/system/assistant/workspace",
    }

    with patch("cli.main.OpcClient.from_env", return_value=fake):
        args = MagicMock(repair=False, reconfigure=False)
        cmd_assistant_init(args)

    init_call = fake.post.call_args_list[0]
    assert init_call.args[0] == "/api/v1/assistant/init"
    assert init_call.kwargs["json"] == {"reconfigure": False}
    out = capsys.readouterr().out
    assert "Next steps to register your assistant CLI:" in out
    assert "happyranch assistant register --from-file" in out
    assert "/tmp/rt/system/assistant/workspace" in out


def test_cmd_assistant_init_reconfigure_passes_flag(monkeypatch, capsys) -> None:
    from cli.main import cmd_assistant_init

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {"state": "uninitialized"}

    with patch("cli.main.OpcClient.from_env", return_value=fake):
        args = MagicMock(repair=False, reconfigure=True)
        cmd_assistant_init(args)

    init_call = fake.post.call_args_list[0]
    assert init_call.args[0] == "/api/v1/assistant/init"
    assert init_call.kwargs["json"] == {"reconfigure": True}


def test_cmd_assistant_init_repair_calls_repair_route(monkeypatch, capsys) -> None:
    from cli.main import cmd_assistant_init

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {
        "state": "configured",
        "selected_executor": "claude",
    }

    with patch("cli.main.OpcClient.from_env", return_value=fake):
        args = MagicMock(repair=True, reconfigure=False)
        cmd_assistant_init(args)

    fake.post.assert_called_once_with("/api/v1/assistant/repair")
    assert "state: configured" in capsys.readouterr().out


def test_cmd_assistant_register_posts_from_file(monkeypatch, capsys, tmp_path) -> None:
    from cli.main import cmd_assistant_register

    payload = tmp_path / "register.json"
    payload.write_text('{"executor": "claude", "command": "claude", "argv": ["claude"]}')
    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {
        "state": "configured",
        "selected_executor": "claude",
    }

    with patch("cli.main.OpcClient.from_env", return_value=fake):
        args = MagicMock(from_file=str(payload), executor=None, command=None, argv=None)
        cmd_assistant_register(args)

    register_call = fake.post.call_args_list[0]
    assert register_call.args[0] == "/api/v1/assistant/register"
    assert register_call.kwargs["json"] == {
        "executor": "claude",
        "command": "claude",
        "argv": ["claude"],
    }
    assert "state: configured" in capsys.readouterr().out


def test_cmd_assistant_register_reports_error(monkeypatch, capsys, tmp_path) -> None:
    from cli.main import cmd_assistant_register

    payload = tmp_path / "register.json"
    payload.write_text('{"executor": "ghost", "command": "ghost", "argv": ["ghost"]}')
    fake = MagicMock()
    fake.post.return_value.status_code = 400
    fake.post.return_value.text = '{"detail": {"code": "assistant_executable_not_found"}}'

    with patch("cli.main.OpcClient.from_env", return_value=fake):
        args = MagicMock(from_file=str(payload), executor=None, command=None, argv=None)
        with pytest.raises(SystemExit) as exc:
            cmd_assistant_register(args)

    assert exc.value.code == 1
    assert "Error (400)" in capsys.readouterr().out
