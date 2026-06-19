from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from runtime.config import Settings
from runtime.daemon import paths as paths_mod
from runtime.daemon.app import create_app
from runtime.daemon.state import DaemonState
from runtime.system_assistant import (
    AssistantConfig,
    AssistantState,
    load_assistant_config,
    save_assistant_config,
    system_assistant_paths,
)


@pytest.fixture
def auth(auth_headers: dict[str, str]) -> dict[str, str]:
    return auth_headers


def _idle_client(auth: dict[str, str]) -> TestClient:
    client = TestClient(create_app(DaemonState.idle(Settings())))
    client.headers.update(auth)
    return client


def _receive_until(websocket: Any, needle: str, *, max_reads: int = 10) -> str:
    """Drain WS text frames until one contains ``needle``.

    The assistant session runs over a PTY, so the pty echoes the typed input
    back as its own line, racing with the fake CLI's ``print('echo: ' + line)``
    output. A single ``receive_text()`` may grab whichever lands first, so read
    up to ``max_reads`` frames before giving up.
    """
    seen: list[str] = []
    for _ in range(max_reads):
        frame = websocket.receive_text()
        seen.append(frame)
        if needle in frame:
            return frame
    raise AssertionError(
        f"{needle!r} not seen within {max_reads} WS frames; received {seen!r}"
    )


class _CloseTrackingSessions:
    def __init__(self, *, lock: asyncio.Lock) -> None:
        self.lock = lock
        self.close_calls = 0

    async def close_all(self) -> None:
        assert self.lock.locked()
        self.close_calls += 1


def test_assistant_status_no_active_runtime(tmp_home: Path, auth: dict[str, str]) -> None:
    client = _idle_client(auth)

    response = client.get("/api/v1/assistant/status")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "no_active_runtime"


def test_assistant_status_uninitialized(client: TestClient) -> None:
    response = client.get("/api/v1/assistant/status")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "state": AssistantState.UNINITIALIZED,
        "selected_executor": None,
        "workspace_path": None,
        "detail": None,
    }


def test_assistant_status_requires_http_auth(client: TestClient) -> None:
    no_auth = TestClient(client.app)

    response = no_auth.get("/api/v1/assistant/status")

    assert response.status_code == 401


def test_parse_resize_control_frame() -> None:
    from runtime.daemon.routes.assistant import _parse_resize_control

    assert _parse_resize_control("__HAPPYRANCH_ASSISTANT_RESIZE__ 43 132") == (
        43,
        132,
    )
    assert _parse_resize_control("__HAPPYRANCH_ASSISTANT_RESIZE__ 0 132") is None
    assert _parse_resize_control("hello") is None


def test_websocket_token_is_valid_parses_bearer_and_compares(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from starlette.datastructures import Headers

    from runtime.daemon.routes import assistant as assistant_route

    expected = "s3cret-bearer-token"

    def _fake_ws(authorization: str | None) -> Any:
        raw = {} if authorization is None else {"authorization": authorization}
        return SimpleNamespace(headers=Headers(raw))

    monkeypatch.setattr(assistant_route.daemon_paths, "read_token", lambda: expected)

    # Valid 'Bearer <token>' header is accepted.
    assert assistant_route._websocket_token_is_valid(_fake_ws(f"Bearer {expected}")) is True
    # A non-matching token is rejected.
    assert assistant_route._websocket_token_is_valid(_fake_ws("Bearer wrong-token")) is False
    # Fail-closed: missing Authorization header.
    assert assistant_route._websocket_token_is_valid(_fake_ws(None)) is False
    # Fail-closed: header carries the token but lacks the 'Bearer ' prefix.
    assert assistant_route._websocket_token_is_valid(_fake_ws(expected)) is False

    # Fail-closed: no expected token on disk, even with a well-formed header.
    monkeypatch.setattr(assistant_route.daemon_paths, "read_token", lambda: None)
    assert assistant_route._websocket_token_is_valid(_fake_ws(f"Bearer {expected}")) is False


def test_websocket_token_is_valid_accepts_subprotocol_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THR-006 Option A: browsers cannot set the Authorization header on
    ``new WebSocket()``, so the bearer token may also arrive via the
    ``Sec-WebSocket-Protocol`` subprotocol ``happyranch.bearer.<token>``."""
    from types import SimpleNamespace

    from starlette.datastructures import Headers

    from runtime.daemon.routes import assistant as assistant_route

    expected = "s3cret-bearer-token"

    def _fake_ws(headers: dict[str, str]) -> Any:
        # No ``scope`` attribute -> the reader falls back to the
        # Sec-WebSocket-Protocol header, exactly like a real upgrade.
        return SimpleNamespace(headers=Headers(headers))

    monkeypatch.setattr(assistant_route.daemon_paths, "read_token", lambda: expected)

    # Browser path: token offered via the subprotocol is accepted.
    assert assistant_route._websocket_token_is_valid(
        _fake_ws({"sec-websocket-protocol": f"happyranch.bearer.{expected}"})
    ) is True
    # The bearer subprotocol is found even alongside other offered protocols.
    assert assistant_route._websocket_token_is_valid(
        _fake_ws({"sec-websocket-protocol": f"chat, happyranch.bearer.{expected}"})
    ) is True
    # Fail-closed: a non-matching token via the subprotocol is rejected.
    assert assistant_route._websocket_token_is_valid(
        _fake_ws({"sec-websocket-protocol": "happyranch.bearer.wrong-token"})
    ) is False
    # Fail-closed: no bearer subprotocol offered at all.
    assert assistant_route._websocket_token_is_valid(
        _fake_ws({"sec-websocket-protocol": "chat"})
    ) is False
    # Fail-closed: no expected token on disk, even with a well-formed subprotocol.
    monkeypatch.setattr(assistant_route.daemon_paths, "read_token", lambda: None)
    assert assistant_route._websocket_token_is_valid(
        _fake_ws({"sec-websocket-protocol": f"happyranch.bearer.{expected}"})
    ) is False


def test_assistant_register_configures_with_valid_payload(client: TestClient) -> None:
    response = client.post(
        "/api/v1/assistant/register",
        json={"executor": "claude", "command": "sh", "argv": ["sh"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "configured"
    assert body["selected_executor"] == "claude"


def test_assistant_register_rejects_missing_executable(client: TestClient) -> None:
    response = client.post(
        "/api/v1/assistant/register",
        json={
            "executor": "ghost",
            "command": "definitely-not-a-real-binary-xyz",
            "argv": ["definitely-not-a-real-binary-xyz"],
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "assistant_executable_not_found"


def test_assistant_register_rejects_empty_executor(client: TestClient) -> None:
    response = client.post(
        "/api/v1/assistant/register",
        json={"executor": "  ", "command": "sh", "argv": ["sh"]},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "assistant_registration_invalid"


def test_assistant_register_rejects_extra_fields(client: TestClient) -> None:
    response = client.post(
        "/api/v1/assistant/register",
        json={"executor": "claude", "command": "sh", "argv": ["sh"], "x": 1},
    )
    assert response.status_code == 422


def test_assistant_register_closes_active_session(client: TestClient) -> None:
    sessions = _CloseTrackingSessions(
        lock=client.app.state.daemon.assistant_lifecycle_lock,
    )
    client.app.state.daemon.assistant_sessions = sessions

    response = client.post(
        "/api/v1/assistant/register",
        json={"executor": "claude", "command": "sh", "argv": ["sh"]},
    )

    assert response.status_code == 200, response.text
    assert sessions.close_calls == 1


def test_assistant_init_prepares_registration_workspace(client: TestClient) -> None:
    response = client.post("/api/v1/assistant/init", json={})
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "uninitialized"


def test_assistant_init_reconfigure_clears_existing_config(client: TestClient) -> None:
    configured = client.post(
        "/api/v1/assistant/register",
        json={"executor": "claude", "command": "sh", "argv": ["sh"]},
    )
    assert configured.json()["state"] == "configured"

    response = client.post("/api/v1/assistant/init", json={"reconfigure": True})
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "uninitialized"


def test_assistant_init_reconfigure_closes_active_session(client: TestClient) -> None:
    configured = client.post(
        "/api/v1/assistant/register",
        json={"executor": "claude", "command": "sh", "argv": ["sh"]},
    )
    assert configured.json()["state"] == "configured"

    sessions = _CloseTrackingSessions(
        lock=client.app.state.daemon.assistant_lifecycle_lock,
    )
    client.app.state.daemon.assistant_sessions = sessions

    response = client.post("/api/v1/assistant/init", json={"reconfigure": True})

    assert response.status_code == 200, response.text
    assert sessions.close_calls == 1


def test_assistant_websocket_streams_to_selected_cli(
    client: TestClient,
    tmp_path: Path,
) -> None:
    fake_cli = tmp_path / "fake-assistant"
    fake_cli.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "print('assistant ready', flush=True)\n"
        "for line in sys.stdin:\n"
        "    print('echo: ' + line.strip(), flush=True)\n"
    )
    fake_cli.chmod(0o755)

    response = client.post(
        "/api/v1/assistant/register",
        json={
            "executor": "codex",
            "command": str(fake_cli),
            "argv": [str(fake_cli)],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "configured"

    try:
        token = paths_mod.read_token()
        with client.websocket_connect(
            "/api/v1/assistant/session",
            headers={"Authorization": f"Bearer {token}"},
        ) as websocket:
            # The HTTP Authorization-header path is unchanged: no subprotocol
            # was offered, so none is echoed back.
            assert websocket.accepted_subprotocol is None
            assert websocket.receive_text().strip() == "assistant ready"

            websocket.send_text("hello from websocket\n")

            _receive_until(websocket, "echo: hello from websocket")
    finally:
        asyncio.run(client.app.state.daemon.assistant_sessions.close_all())


def test_assistant_websocket_accepts_subprotocol_token_and_echoes(
    client: TestClient,
    tmp_path: Path,
) -> None:
    """THR-006 Option A: a browser authenticates by offering the bearer token
    via the Sec-WebSocket-Protocol subprotocol (no Authorization header), and
    the server echoes the accepted subprotocol back on accept()."""
    fake_cli = tmp_path / "fake-assistant"
    fake_cli.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "print('assistant ready', flush=True)\n"
        "for line in sys.stdin:\n"
        "    print('echo: ' + line.strip(), flush=True)\n"
    )
    fake_cli.chmod(0o755)

    response = client.post(
        "/api/v1/assistant/register",
        json={
            "executor": "codex",
            "command": str(fake_cli),
            "argv": [str(fake_cli)],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "configured"

    token = paths_mod.read_token()
    subprotocol = f"happyranch.bearer.{token}"
    # A fresh client WITHOUT the Authorization header — auth must succeed on
    # the subprotocol alone.
    browser = TestClient(client.app)
    try:
        with browser.websocket_connect(
            "/api/v1/assistant/session",
            subprotocols=[subprotocol],
        ) as websocket:
            assert websocket.accepted_subprotocol == subprotocol
            assert websocket.receive_text().strip() == "assistant ready"

            websocket.send_text("hi over subprotocol\n")

            _receive_until(websocket, "echo: hi over subprotocol")
    finally:
        asyncio.run(client.app.state.daemon.assistant_sessions.close_all())


def test_assistant_websocket_rejects_bad_subprotocol_token(
    client: TestClient,
    runtime,
) -> None:
    """A bad token offered via the subprotocol is rejected fail-closed
    (close 1008 before accept) and never starts a session."""
    paths = system_assistant_paths(runtime.root)
    paths.root.mkdir(parents=True)
    paths.workspace.mkdir(parents=True)
    (paths.workspace / "agent.yaml").write_text("name: system_assistant\n")
    (paths.workspace / "AGENTS.md").write_text("# Assistant\n")
    (paths.learnings_dir).mkdir(parents=True)
    (paths.learnings_dir / "_index.md").write_text("# Learnings\n")
    save_assistant_config(
        runtime.root,
        AssistantConfig(
            selected_executor="codex",
            selected_command="/should/not/start",
            selected_argv=["/should/not/start"],
            workspace_path=str(paths.workspace),
        ),
    )

    class NoStartSessions:
        async def get_or_start(self, **_kwargs: Any) -> None:
            raise AssertionError("unauthorized websocket started assistant session")

    client.app.state.daemon.assistant_sessions = NoStartSessions()

    browser = TestClient(client.app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with browser.websocket_connect(
            "/api/v1/assistant/session",
            subprotocols=["happyranch.bearer.bad-token"],
        ):
            pass

    assert exc_info.value.code == 1008


def test_assistant_websocket_rejects_bad_token_without_starting_session(
    client: TestClient,
    runtime,
) -> None:
    paths = system_assistant_paths(runtime.root)
    paths.root.mkdir(parents=True)
    paths.workspace.mkdir(parents=True)
    (paths.workspace / "agent.yaml").write_text("name: system_assistant\n")
    (paths.workspace / "AGENTS.md").write_text("# Assistant\n")
    (paths.learnings_dir).mkdir(parents=True)
    (paths.learnings_dir / "_index.md").write_text("# Learnings\n")
    save_assistant_config(
        runtime.root,
        AssistantConfig(
            selected_executor="codex",
            selected_command="/should/not/start",
            selected_argv=["/should/not/start"],
            workspace_path=str(paths.workspace),
        ),
    )

    class NoStartSessions:
        async def get_or_start(self, **_kwargs: Any) -> None:
            raise AssertionError("unauthorized websocket started assistant session")

    client.app.state.daemon.assistant_sessions = NoStartSessions()

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/api/v1/assistant/session",
            headers={"Authorization": "Bearer bad-token"},
        ):
            pass

    assert exc_info.value.code == 1008


def test_assistant_websocket_uninitialized_sends_hint_without_starting_session(
    client: TestClient,
) -> None:
    class NoStartSessions:
        async def get_or_start(self, **_kwargs: Any) -> None:
            raise AssertionError("uninitialized assistant started session")

    client.app.state.daemon.assistant_sessions = NoStartSessions()
    token = paths_mod.read_token()

    with client.websocket_connect(
        "/api/v1/assistant/session",
        headers={"Authorization": f"Bearer {token}"},
    ) as websocket:
        assert websocket.receive_text() == (
            "assistant_init_required: configure the system assistant before attaching"
        )
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_text()

    assert exc_info.value.code == 1000


def test_assistant_websocket_starts_session_under_lifecycle_lock(
    client: TestClient,
    runtime,
) -> None:
    paths = system_assistant_paths(runtime.root)
    paths.workspace.mkdir(parents=True)
    (paths.workspace / "agent.yaml").write_text("name: system_assistant\n")
    (paths.workspace / "AGENTS.md").write_text("# Assistant\n")
    paths.learnings_dir.mkdir(parents=True)
    (paths.learnings_dir / "_index.md").write_text("# Learnings\n")
    paths.knowledge_dir.mkdir(parents=True)
    (paths.knowledge_dir / "README.md").write_text("# Knowledge\n")
    paths.logs_dir.mkdir(parents=True)
    save_assistant_config(
        runtime.root,
        AssistantConfig(
            selected_executor="codex",
            selected_command=sys.executable,
            selected_argv=[sys.executable],
            workspace_path=str(paths.workspace),
        ),
    )

    class FakeSession:
        def subscribe(self) -> asyncio.Queue[str | None]:
            queue: asyncio.Queue[str | None] = asyncio.Queue()
            queue.put_nowait("fake ready")
            return queue

        def unsubscribe(self, _queue: asyncio.Queue[str | None]) -> None:
            pass

        async def write_text(self, _text: str) -> None:
            pass

        async def resize(self, *, rows: int, cols: int) -> None:
            pass

    class LockCheckingSessions:
        def __init__(self) -> None:
            self.started_under_lock = False

        async def get_or_start(self, **_kwargs: Any) -> FakeSession:
            self.started_under_lock = (
                client.app.state.daemon.assistant_lifecycle_lock.locked()
            )
            return FakeSession()

    sessions = LockCheckingSessions()
    client.app.state.daemon.assistant_sessions = sessions
    token = paths_mod.read_token()

    with client.websocket_connect(
        "/api/v1/assistant/session",
        headers={"Authorization": f"Bearer {token}"},
    ) as websocket:
        assert websocket.receive_text() == "fake ready"

    assert sessions.started_under_lock is True


def test_assistant_repair_refreshes_workspace(client: TestClient, runtime) -> None:
    paths = system_assistant_paths(runtime.root)
    paths.root.mkdir(parents=True)
    save_assistant_config(
        runtime.root,
        AssistantConfig(
            selected_executor="claude",
            selected_command=sys.executable,
            selected_argv=[sys.executable],
            workspace_path=str(paths.workspace),
        ),
    )

    response = client.post("/api/v1/assistant/repair")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == AssistantState.CONFIGURED
    assert body["selected_executor"] == "claude"
    assert body["workspace_path"] == str(paths.workspace)
    assert (paths.workspace / "agent.yaml").is_file()
    assert (paths.workspace / "CLAUDE.md").is_file()
    assert (paths.learnings_dir / "_index.md").is_file()


def test_assistant_repair_closes_active_session(client: TestClient, runtime) -> None:
    paths = system_assistant_paths(runtime.root)
    paths.root.mkdir(parents=True)
    save_assistant_config(
        runtime.root,
        AssistantConfig(
            selected_executor="claude",
            selected_command=sys.executable,
            selected_argv=[sys.executable],
            workspace_path=str(paths.workspace),
        ),
    )
    sessions = _CloseTrackingSessions(
        lock=client.app.state.daemon.assistant_lifecycle_lock,
    )
    client.app.state.daemon.assistant_sessions = sessions

    response = client.post("/api/v1/assistant/repair")

    assert response.status_code == 200, response.text
    assert sessions.close_calls == 1


def test_assistant_repair_loads_config_under_lifecycle_lock(
    client: TestClient,
    runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.daemon.routes import assistant as assistant_route

    paths = system_assistant_paths(runtime.root)
    config = AssistantConfig(
        selected_executor="claude",
        selected_command=sys.executable,
        selected_argv=[sys.executable],
        workspace_path=str(paths.workspace),
    )
    lock_states: list[bool] = []

    def fake_load_assistant_config(_root: Path) -> AssistantConfig:
        lock_states.append(client.app.state.daemon.assistant_lifecycle_lock.locked())
        return config

    monkeypatch.setattr(
        assistant_route,
        "load_assistant_config",
        fake_load_assistant_config,
    )

    response = client.post("/api/v1/assistant/repair")

    assert response.status_code == 200, response.text
    assert lock_states == [True]


def test_assistant_repair_invalid_config_returns_conflict(
    client: TestClient,
    runtime,
) -> None:
    paths = system_assistant_paths(runtime.root)
    paths.root.mkdir(parents=True)
    paths.config_path.write_text("{invalid json")

    response = client.post("/api/v1/assistant/repair")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "assistant_config_invalid"


def test_assistant_repair_invalid_config_schema_returns_conflict(
    client: TestClient,
    runtime,
) -> None:
    paths = system_assistant_paths(runtime.root)
    paths.root.mkdir(parents=True)
    paths.config_path.write_text('{"selected_executor": "codex"}\n')
    no_raise_client = TestClient(client.app, raise_server_exceptions=False)
    no_raise_client.headers.update(client.headers)

    response = no_raise_client.post("/api/v1/assistant/repair")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "assistant_config_invalid"


def test_assistant_repair_requires_config(client: TestClient) -> None:
    response = client.post("/api/v1/assistant/repair")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "assistant_not_configured"


def test_assistant_websocket_structured_mode_handshake_and_output(
    client: TestClient,
    runtime,
) -> None:
    """Structured JSON-chat mode: client sends a handshake, receives
    structured {"type":"output","text":"..."} frames for PTY output."""
    fake_cli = runtime.root / "fake-assistant"
    fake_cli.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, time\n"
        "print('assistant ready', flush=True)\n"
        "for line in sys.stdin:\n"
        "    print('echo: ' + line.strip(), flush=True)\n"
    )
    fake_cli.chmod(0o755)

    response = client.post(
        "/api/v1/assistant/register",
        json={
            "executor": "codex",
            "command": str(fake_cli),
            "argv": [str(fake_cli)],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "configured"

    token = paths_mod.read_token()
    try:
        with client.websocket_connect(
            "/api/v1/assistant/session",
            headers={"Authorization": f"Bearer {token}"},
        ) as websocket:
            # Send JSON handshake to negotiate structured mode.
            import json

            websocket.send_text(
                json.dumps(
                    {"type": "handshake", "protocol": "json-chat", "version": 1}
                )
            )

            # First frame: ack.
            ack = json.loads(websocket.receive_text())
            assert ack["type"] == "status"
            assert ack["code"] == "ready"

            # Second frame: structured PTY output.
            output_frame = json.loads(websocket.receive_text())
            assert output_frame["type"] == "output"
            assert "assistant ready" in output_frame["text"]

            # Send structured chat input.
            websocket.send_text(
                json.dumps({"type": "chat", "text": "hello structured"})
            )

            # Drain frames until we see the echo.
            seen = []
            for _ in range(10):
                frame_text = websocket.receive_text()
                frame = json.loads(frame_text)
                seen.append(frame)
                if (
                    frame.get("type") == "output"
                    and "echo: hello structured" in frame.get("text", "")
                ):
                    break
            else:
                raise AssertionError(
                    f"'{'echo: hello structured'!r}' not in WS frames: {seen!r}"
                )
    finally:
        asyncio.run(client.app.state.daemon.assistant_sessions.close_all())


def test_assistant_websocket_structured_mode_rejects_bad_token(
    client: TestClient,
    runtime,
) -> None:
    """Structured mode auth rejection: bad subprotocol token → close 1008."""
    paths = system_assistant_paths(runtime.root)
    paths.root.mkdir(parents=True)
    paths.workspace.mkdir(parents=True)
    (paths.workspace / "agent.yaml").write_text("name: system_assistant\n")
    (paths.workspace / "AGENTS.md").write_text("# Assistant\n")
    (paths.learnings_dir).mkdir(parents=True)
    (paths.learnings_dir / "_index.md").write_text("# Learnings\n")
    save_assistant_config(
        runtime.root,
        AssistantConfig(
            selected_executor="codex",
            selected_command="/should/not/start",
            selected_argv=["/should/not/start"],
            workspace_path=str(paths.workspace),
        ),
    )
    browser = TestClient(client.app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with browser.websocket_connect(
            "/api/v1/assistant/session",
            subprotocols=["happyranch.bearer.bad-token"],
        ):
            pass
    assert exc_info.value.code == 1008


def test_assistant_websocket_legacy_still_works_after_structured_added(
    client: TestClient,
    tmp_path: Path,
) -> None:
    """Backward compat: legacy xterm clients (no handshake) still work."""
    fake_cli = tmp_path / "fake-assistant"
    fake_cli.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "print('assistant ready', flush=True)\n"
        "for line in sys.stdin:\n"
        "    print('echo: ' + line.strip(), flush=True)\n"
    )
    fake_cli.chmod(0o755)

    response = client.post(
        "/api/v1/assistant/register",
        json={
            "executor": "codex",
            "command": str(fake_cli),
            "argv": [str(fake_cli)],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "configured"

    token = paths_mod.read_token()
    try:
        with client.websocket_connect(
            "/api/v1/assistant/session",
            headers={"Authorization": f"Bearer {token}"},
        ) as websocket:
            # Backward compat: the server buffers output until the first
            # client message to detect structured handshakes.  Real xterm
            # clients send a resize control on connect; send one here so
            # the server flushes buffered output as raw text.
            websocket.send_text(
                "__HAPPYRANCH_ASSISTANT_RESIZE__ 24 80"
            )
            greeting = websocket.receive_text()
            assert greeting.strip() == "assistant ready"

            # Send input to confirm bidirectional PTY-tunnel works.
            websocket.send_text("hello legacy\n")
            _receive_until(websocket, "echo: hello legacy")
    finally:
        asyncio.run(client.app.state.daemon.assistant_sessions.close_all())


def test_assistant_websocket_structured_mode_no_raw_frames_before_handshake(
    client: TestClient,
    runtime,
) -> None:
    """Structured clients must NEVER receive a raw-text frame before the
    handshake ack and structured output frames.

    The fake CLI prints 'assistant ready' immediately when the session
    starts.  If the server pumps PTY output before reading the client's
    handshake, the greeting would leak as a raw-text frame.  This test
    verifies that EVERY frame arriving before the structured output is
    valid JSON — no raw-text leak.
    """
    fake_cli = runtime.root / "fake-assistant"
    fake_cli.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, time\n"
        "# Emit immediately so the output is ready before the handshake arrives.\n"
        "print('assistant ready', flush=True)\n"
        "print('another greeting', flush=True)\n"
        "for line in sys.stdin:\n"
        "    print('echo: ' + line.strip(), flush=True)\n"
    )
    fake_cli.chmod(0o755)

    response = client.post(
        "/api/v1/assistant/register",
        json={
            "executor": "codex",
            "command": str(fake_cli),
            "argv": [str(fake_cli)],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "configured"

    token = paths_mod.read_token()
    try:
        with client.websocket_connect(
            "/api/v1/assistant/session",
            headers={"Authorization": f"Bearer {token}"},
        ) as websocket:
            import json

            # Send the JSON handshake.
            websocket.send_text(
                json.dumps(
                    {"type": "handshake", "protocol": "json-chat", "version": 1}
                )
            )

            # Collect all frames until we've seen the structured greeting.
            seen = []
            found_greeting = False
            for _ in range(20):
                raw = websocket.receive_text()
                try:
                    frame = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    raise AssertionError(
                        f"Raw-text frame leaked before handshake was complete: {raw!r}"
                    ) from None
                seen.append(frame)
                if (
                    frame.get("type") == "output"
                    and "assistant ready" in frame.get("text", "")
                ):
                    found_greeting = True
                    break

            assert found_greeting, (
                f"Never received structured output with 'assistant ready'; "
                f"frames so far: {seen!r}"
            )

            # Every frame we received must be valid JSON — the assertion
            # above already enforces this in the loop.
    finally:
        asyncio.run(client.app.state.daemon.assistant_sessions.close_all())


@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("post", "/api/v1/assistant/init", {}),
        (
            "post",
            "/api/v1/assistant/register",
            {"executor": "claude", "command": "sh", "argv": ["sh"]},
        ),
        ("post", "/api/v1/assistant/repair", None),
    ],
)
def test_assistant_mutations_require_active_runtime(
    tmp_home: Path,
    auth: dict[str, str],
    method: str,
    path: str,
    json: dict[str, Any] | None,
) -> None:
    client = _idle_client(auth)

    response = client.request(method, path, json=json)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "no_active_runtime"
