from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.config import Settings
from src.daemon.app import create_app
from src.daemon.state import DaemonState
from src.runtime import RuntimeDir


@pytest.fixture
def auth(monkeypatch, tmp_path):
    home = tmp_path / "opc-home"
    home.mkdir()
    monkeypatch.setenv("OPC_DAEMON_HOME", str(home))
    from src.daemon import paths
    return {"Authorization": f"Bearer {paths.ensure_token()}"}


def test_get_runtime_idle(tmp_path: Path, auth) -> None:
    state = DaemonState.idle(Settings())
    client = TestClient(create_app(state))
    r = client.get("/api/v1/runtime", headers=auth)
    assert r.status_code == 200
    assert r.json() == {"runtime": None}


def test_post_runtime_registers(tmp_path: Path, auth) -> None:
    state = DaemonState.idle(Settings())
    client = TestClient(create_app(state))
    target = tmp_path / "rt"
    r = client.post(
        "/api/v1/runtime", headers=auth,
        json={"path": str(target)},
    )
    assert r.status_code == 200
    assert r.json()["runtime"] == str(target.resolve())
    assert state.runtime is not None
