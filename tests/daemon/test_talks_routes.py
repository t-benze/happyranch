from __future__ import annotations

from fastapi.testclient import TestClient


def test_start_talk_creates_row(tmp_home, app, runtime, auth_headers):
    client = TestClient(app)
    r = client.post("/api/v1/talks", json={"agent_name": "dev_agent"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["talk_id"] == "TALK-001"
    assert "started_at" in body

    detail = client.get(f"/api/v1/talks/{body['talk_id']}", headers=auth_headers).json()
    assert detail["status"] == "open"


def test_start_talk_idle_runtime(tmp_home, app_idle, auth_headers):
    client = TestClient(app_idle)
    r = client.post("/api/v1/talks", json={"agent_name": "dev_agent"}, headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "no_active_runtime"


def test_start_talk_conflict_when_open_exists(tmp_home, app, runtime, auth_headers):
    client = TestClient(app)
    first = client.post("/api/v1/talks", json={"agent_name": "dev_agent"}, headers=auth_headers).json()
    second = client.post("/api/v1/talks", json={"agent_name": "dev_agent"}, headers=auth_headers)
    assert second.status_code == 409
    detail = second.json()["detail"]
    assert detail["code"] == "talk_already_open"
    assert detail["prior_open_talk_id"] == first["talk_id"]
