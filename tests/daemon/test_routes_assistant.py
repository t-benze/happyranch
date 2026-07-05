from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

import pytest
from fastapi.testclient import TestClient

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
