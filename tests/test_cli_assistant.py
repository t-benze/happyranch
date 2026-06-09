from __future__ import annotations

import asyncio
import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from cli.main import build_parser


class _FakeStdin:
    def __init__(self, values: list[str], *, exc: BaseException | None = None) -> None:
        self._values = values
        self._exc = exc

    def fileno(self) -> int:
        return 123

    def read(self, size: int) -> str:
        assert size == 1
        if self._exc is not None:
            raise self._exc
        return self._values.pop(0)


class _LoopProxy:
    def __init__(self, loop: asyncio.AbstractEventLoop, callback_count: int) -> None:
        self._loop = loop
        self._callback_count = callback_count
        self.removed = False
        self.signal_handler: Any | None = None
        self.signal_removed = False

    def create_future(self) -> asyncio.Future[None]:
        return self._loop.create_future()

    def add_reader(self, fd: int, callback: Any) -> None:
        assert fd == 123
        for _ in range(self._callback_count):
            self._loop.call_soon(callback)

    def remove_reader(self, fd: int) -> None:
        assert fd == 123
        self.removed = True

    def add_signal_handler(self, sig: Any, callback: Any) -> None:
        self.signal_handler = callback

    def remove_signal_handler(self, sig: Any) -> bool:
        self.signal_removed = True
        return True


class _ConnectContext:
    def __init__(
        self,
        websocket: Any | None = None,
        exc: BaseException | None = None,
    ) -> None:
        self._websocket = websocket
        self._exc = exc

    async def __aenter__(self) -> Any:
        if self._exc is not None:
            raise self._exc
        return self._websocket

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class _IdleWebSocket:
    def __init__(self) -> None:
        self.closed = False

    async def send(self, message: str) -> None:
        return None

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self) -> _IdleWebSocket:
        return self

    async def __anext__(self) -> str:
        await asyncio.Future()
        raise StopAsyncIteration


def _fake_terminal(monkeypatch, stdin: _FakeStdin) -> list[tuple[int, int, list[str]]]:
    restored: list[tuple[int, int, list[str]]] = []
    monkeypatch.setattr("sys.stdin", stdin)
    monkeypatch.setattr("termios.tcgetattr", lambda fd: ["old"])
    monkeypatch.setattr("tty.setraw", lambda fd: None)
    monkeypatch.setattr(
        "termios.tcsetattr",
        lambda fd, when, attrs: restored.append((fd, when, attrs)),
    )
    return restored


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


def test_cmd_assistant_attach_bridge_error_exits_one(capsys) -> None:
    from cli.main import cmd_assistant_attach

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {"state": "configured"}

    with (
        patch("cli.main.OpcClient.from_env", return_value=fake),
        patch("cli.commands.assistant._run_attach_bridge", side_effect=OSError("boom")),
        pytest.raises(SystemExit) as exc,
    ):
        cmd_assistant_attach(MagicMock())

    assert exc.value.code == 1
    assert "Error: assistant attach failed: boom" in capsys.readouterr().out


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


def test_ws_url_converts_http_to_ws_without_token() -> None:
    from cli.commands.assistant import _ws_headers, _ws_url

    client = MagicMock()
    client.base_url = "http://127.0.0.1:4567"
    client.headers = {"Authorization": "Bearer test-token"}

    assert _ws_url(client) == "ws://127.0.0.1:4567/api/v1/assistant/session"
    assert _ws_headers(client) == {"Authorization": "Bearer test-token"}


def test_ws_url_converts_https_to_wss_without_token() -> None:
    from cli.commands.assistant import _ws_url

    client = MagicMock()
    client.base_url = "https://example.test:8443"
    client.headers = {"Authorization": "Bearer secure-token"}

    assert _ws_url(client) == "wss://example.test:8443/api/v1/assistant/session"


def test_ws_headers_preserve_special_token_chars() -> None:
    from cli.commands.assistant import _ws_headers

    client = MagicMock()
    client.base_url = "http://127.0.0.1:4567"
    client.headers = {"Authorization": "Bearer a+b/c?d&e"}

    assert _ws_headers(client) == {"Authorization": "Bearer a+b/c?d&e"}


def test_attach_bridge_restores_terminal_when_connect_fails(monkeypatch) -> None:
    from cli.commands.assistant import _run_attach_bridge

    client = MagicMock()
    client.base_url = "http://127.0.0.1:4567"
    client.headers = {"Authorization": "Bearer token"}
    restored = _fake_terminal(monkeypatch, _FakeStdin([""]))
    monkeypatch.setattr(
        "cli.commands.assistant.websockets.connect",
        lambda url, **_kwargs: _ConnectContext(exc=OSError("cannot connect")),
    )

    with pytest.raises(OSError, match="cannot connect"):
        _run_attach_bridge(client)

    assert restored == [(123, 1, ["old"])]


@pytest.mark.asyncio
async def test_attach_bridge_restores_terminal_and_surfaces_stdin_read_failure(
    monkeypatch,
) -> None:
    from cli.commands.assistant import _attach_bridge

    client = MagicMock()
    client.base_url = "http://127.0.0.1:4567"
    client.headers = {"Authorization": "Bearer token"}
    restored = _fake_terminal(monkeypatch, _FakeStdin([], exc=OSError("stdin broke")))
    loop = asyncio.get_running_loop()
    loop_proxy = _LoopProxy(loop, callback_count=1)
    monkeypatch.setattr("asyncio.get_running_loop", lambda: loop_proxy)
    monkeypatch.setattr(
        "cli.commands.assistant.websockets.connect",
        lambda url, **_kwargs: _ConnectContext(_IdleWebSocket()),
    )

    with pytest.raises(OSError, match="stdin broke"):
        await asyncio.wait_for(_attach_bridge(client), timeout=1)

    assert loop_proxy.removed is True
    assert restored == [(123, 1, ["old"])]


@pytest.mark.asyncio
async def test_attach_bridge_surfaces_send_failure_and_restores_terminal(
    monkeypatch,
) -> None:
    from cli.commands.assistant import _attach_bridge

    class FailingSendWebSocket(_IdleWebSocket):
        async def send(self, message: str) -> None:
            raise OSError("send failed")

    client = MagicMock()
    client.base_url = "http://127.0.0.1:4567"
    client.headers = {"Authorization": "Bearer token"}
    restored = _fake_terminal(monkeypatch, _FakeStdin(["x", ""]))
    loop = asyncio.get_running_loop()
    loop_proxy = _LoopProxy(loop, callback_count=2)
    monkeypatch.setattr("asyncio.get_running_loop", lambda: loop_proxy)
    monkeypatch.setattr(
        "cli.commands.assistant.websockets.connect",
        lambda url, **_kwargs: _ConnectContext(FailingSendWebSocket()),
    )

    with pytest.raises(OSError, match="send failed"):
        await asyncio.wait_for(_attach_bridge(client), timeout=1)

    assert loop_proxy.removed is True
    assert restored == [(123, 1, ["old"])]


@pytest.mark.asyncio
async def test_attach_bridge_sends_stdin_chars_in_order(monkeypatch) -> None:
    from cli.commands.assistant import _attach_bridge

    class OrderedWebSocket(_IdleWebSocket):
        def __init__(self) -> None:
            super().__init__()
            self.sent: list[str] = []

        async def send(self, message: str) -> None:
            if message == "a":
                await asyncio.sleep(0.01)
            self.sent.append(message)

    websocket = OrderedWebSocket()
    client = MagicMock()
    client.base_url = "http://127.0.0.1:4567"
    client.headers = {"Authorization": "Bearer token"}
    _fake_terminal(monkeypatch, _FakeStdin(["a", "b", ""]))
    loop = asyncio.get_running_loop()
    loop_proxy = _LoopProxy(loop, callback_count=3)
    monkeypatch.setattr("asyncio.get_running_loop", lambda: loop_proxy)
    monkeypatch.setattr(
        "cli.commands.assistant.websockets.connect",
        lambda url, **_kwargs: _ConnectContext(websocket),
    )

    await asyncio.wait_for(_attach_bridge(client), timeout=1)

    assert websocket.sent == ["a", "b"]


@pytest.mark.asyncio
async def test_attach_bridge_sends_initial_resize(monkeypatch) -> None:
    from cli.commands.assistant import _attach_bridge

    class RecordingWebSocket(_IdleWebSocket):
        def __init__(self) -> None:
            super().__init__()
            self.sent: list[str] = []

        async def send(self, message: str) -> None:
            self.sent.append(message)

    websocket = RecordingWebSocket()
    client = MagicMock()
    client.base_url = "http://127.0.0.1:4567"
    client.headers = {"Authorization": "Bearer token"}
    _fake_terminal(monkeypatch, _FakeStdin([""]))
    monkeypatch.setattr(
        "os.get_terminal_size",
        lambda fd: os.terminal_size((132, 43)),
    )
    loop = asyncio.get_running_loop()
    loop_proxy = _LoopProxy(loop, callback_count=1)
    monkeypatch.setattr("asyncio.get_running_loop", lambda: loop_proxy)
    monkeypatch.setattr(
        "cli.commands.assistant.websockets.connect",
        lambda url, **_kwargs: _ConnectContext(websocket),
    )

    await asyncio.wait_for(_attach_bridge(client), timeout=1)

    assert websocket.sent[0] == "__HAPPYRANCH_ASSISTANT_RESIZE__ 43 132"
    assert loop_proxy.signal_handler is not None
    assert loop_proxy.signal_removed is True


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
