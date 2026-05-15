"""Tests for `opc web`."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.cli import build_parser, cmd_web


def test_web_subcommand_parses():
    parser = build_parser()
    args = parser.parse_args(["web", "--no-open"])
    assert args.command == "web"
    assert args.no_open is True


def test_web_default_opens_browser():
    parser = build_parser()
    args = parser.parse_args(["web"])
    assert args.command == "web"
    assert args.no_open is False


def test_cmd_web_prints_url(capsys):
    fake_client = MagicMock()
    fake_client.base_url = "http://127.0.0.1:12345"
    fake_client.get.return_value.status_code = 200

    with patch("src.cli.OpcClient.from_env", return_value=fake_client), \
         patch("webbrowser.open") as wb:
        args = MagicMock()
        args.no_open = False
        cmd_web(args)
        wb.assert_called_once_with("http://127.0.0.1:12345/")
    assert "http://127.0.0.1:12345/" in capsys.readouterr().out


def test_cmd_web_no_open(capsys):
    fake_client = MagicMock()
    fake_client.base_url = "http://127.0.0.1:12345"
    fake_client.get.return_value.status_code = 200

    with patch("src.cli.OpcClient.from_env", return_value=fake_client), \
         patch("webbrowser.open") as wb:
        args = MagicMock()
        args.no_open = True
        cmd_web(args)
        wb.assert_not_called()
    assert "http://127.0.0.1:12345/" in capsys.readouterr().out


def test_cmd_web_daemon_unreachable_exits(capsys):
    fake_client = MagicMock()
    fake_client.base_url = "http://127.0.0.1:12345"
    fake_client.get.side_effect = ConnectionError("refused")

    with patch("src.cli.OpcClient.from_env", return_value=fake_client):
        args = MagicMock()
        args.no_open = True
        with pytest.raises(SystemExit) as exc:
            cmd_web(args)
        assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "daemon unreachable" in err


def test_cmd_web_health_failure_exits(capsys):
    fake_client = MagicMock()
    fake_client.base_url = "http://127.0.0.1:12345"
    fake_client.get.return_value.status_code = 500
    fake_client.get.return_value.text = "boom"
    fake_client.get.return_value.json.side_effect = ValueError

    with patch("src.cli.OpcClient.from_env", return_value=fake_client):
        args = MagicMock()
        args.no_open = True
        with pytest.raises(SystemExit) as exc:
            cmd_web(args)
        # _ok() exits with 1 on any non-2xx (shared helper).
        assert exc.value.code == 1
