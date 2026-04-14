from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, Header
from fastapi.testclient import TestClient

from src.client.client import (
    DaemonNotRunning,
    DaemonStateInconsistent,
    OpcClient,
)
from src.daemon import paths as paths_mod


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("OPC_DAEMON_HOME", str(tmp_path / ".opc"))
    paths_mod.ensure_daemon_home()
    return tmp_path / ".opc"


def test_missing_port_raises_daemon_not_running(tmp_home: Path) -> None:
    with pytest.raises(DaemonNotRunning):
        OpcClient.from_env()


def test_missing_token_raises_state_inconsistent(tmp_home: Path) -> None:
    paths_mod.port_file().write_text("12345")
    with pytest.raises(DaemonStateInconsistent):
        OpcClient.from_env()


def test_constructor_uses_token_for_auth(tmp_home: Path) -> None:
    paths_mod.port_file().write_text("12345")
    paths_mod.ensure_token()
    client = OpcClient.from_env()
    assert client.headers["Authorization"].startswith("Bearer ")


class _StarletteTransport(httpx.BaseTransport):
    """Wraps Starlette's TestClient as an httpx sync transport."""

    def __init__(self, app: FastAPI) -> None:
        self._tc = TestClient(app, raise_server_exceptions=True)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self._tc.request(
            method=request.method,
            url=str(request.url),
            content=request.content,
            headers=dict(request.headers),
        )
        return httpx.Response(
            status_code=response.status_code,
            headers=dict(response.headers),
            content=response.content,
        )


def test_get_uses_injected_transport(tmp_home: Path) -> None:
    paths_mod.port_file().write_text("12345")
    token = paths_mod.ensure_token()

    app = FastAPI()

    @app.get("/api/v1/ping")
    def ping(authorization: str | None = Header(default=None)) -> dict:
        return {"auth_seen": authorization}

    client = OpcClient.from_env()
    # swap transport for the test
    client._client = httpx.Client(
        base_url=client.base_url,
        headers=client.headers,
        transport=_StarletteTransport(app),
    )
    body = client.get("/api/v1/ping").json()
    assert body["auth_seen"] == f"Bearer {token}"
