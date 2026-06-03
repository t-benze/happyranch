"""Tests for the SPA bootstrap endpoint — localhost-only, returns the bearer token."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from runtime.config import Settings
from runtime.daemon import paths
from runtime.daemon.app import create_app
from runtime.daemon.state import DaemonState


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
    paths.ensure_token()  # mints daemon.token under tmp_path
    state = DaemonState.idle(Settings())
    app = create_app(state)
    return TestClient(app), tmp_path


def test_bootstrap_rejects_non_localhost(app_client):
    client, _ = app_client
    # TestClient's default peer is 'testclient', which is NOT in _LOCAL_HOSTS,
    # so the bare GET is already a deny path.
    r = client.get("/api/v1/auth/bootstrap")
    assert r.status_code == 403
    body = r.json()
    assert body["detail"]["code"] == "not_localhost"


def test_bootstrap_returns_token_from_localhost(app_client, monkeypatch):
    client, home = app_client
    from runtime.daemon.routes import auth as auth_route
    monkeypatch.setattr(
        auth_route, "_LOCAL_HOSTS", auth_route._LOCAL_HOSTS | {"testclient"}
    )
    expected = (home / "daemon.token").read_text().strip()
    r = client.get("/api/v1/auth/bootstrap")
    assert r.status_code == 200
    assert r.json() == {"token": expected}


def test_bootstrap_returns_500_if_token_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
    # Do NOT call ensure_token; daemon.token is absent.
    state = DaemonState.idle(Settings())
    app = create_app(state)
    client = TestClient(app)
    from runtime.daemon.routes import auth as auth_route
    monkeypatch.setattr(
        auth_route, "_LOCAL_HOSTS", auth_route._LOCAL_HOSTS | {"testclient"}
    )
    r = client.get("/api/v1/auth/bootstrap")
    assert r.status_code == 500
    assert "daemon token file missing" in r.text
