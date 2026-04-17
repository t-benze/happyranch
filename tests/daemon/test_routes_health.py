from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_returns_active_runtime(tmp_home, app, daemon_state) -> None:
    client = TestClient(app)
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["active_runtime"] == str(daemon_state.runtime.root)


def test_health_returns_null_when_idle(tmp_home, app_idle) -> None:
    client = TestClient(app_idle)
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "active_runtime": None}


def test_health_does_not_require_auth(tmp_home, app) -> None:
    client = TestClient(app)
    r = client.get("/api/v1/health")
    assert r.status_code == 200
