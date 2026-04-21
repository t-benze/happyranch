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


def test_resume_open_talk(tmp_home, app, runtime, auth_headers):
    client = TestClient(app)
    tid = client.post("/api/v1/talks", json={"agent_name": "dev_agent"}, headers=auth_headers).json()["talk_id"]
    r = client.post(f"/api/v1/talks/{tid}/resume", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["talk_id"] == tid


def test_resume_closed_talk_rejected(tmp_home, app, runtime, auth_headers):
    client = TestClient(app)
    tid = client.post("/api/v1/talks", json={"agent_name": "dev_agent"}, headers=auth_headers).json()["talk_id"]
    client.post(f"/api/v1/talks/{tid}/abandon", json={"reason": "test"}, headers=auth_headers)
    r = client.post(f"/api/v1/talks/{tid}/resume", headers=auth_headers)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "talk_not_open"


def test_abandon_open_talk(tmp_home, app, runtime, auth_headers):
    client = TestClient(app)
    tid = client.post("/api/v1/talks", json={"agent_name": "dev_agent"}, headers=auth_headers).json()["talk_id"]
    r = client.post(f"/api/v1/talks/{tid}/abandon", json={"reason": "orphan"}, headers=auth_headers)
    assert r.status_code == 200
    detail = client.get(f"/api/v1/talks/{tid}", headers=auth_headers).json()
    assert detail["status"] == "abandoned"
    assert detail["ended_at"] is not None


def test_abandon_already_closed(tmp_home, app, runtime, auth_headers):
    client = TestClient(app)
    tid = client.post("/api/v1/talks", json={"agent_name": "dev_agent"}, headers=auth_headers).json()["talk_id"]
    client.post(f"/api/v1/talks/{tid}/abandon", json={"reason": "first"}, headers=auth_headers)
    r = client.post(f"/api/v1/talks/{tid}/abandon", json={"reason": "second"}, headers=auth_headers)
    assert r.status_code == 400


def test_abandon_missing_talk(tmp_home, app, runtime, auth_headers):
    client = TestClient(app)
    r = client.post("/api/v1/talks/TALK-999/abandon", json={"reason": "x"}, headers=auth_headers)
    assert r.status_code == 404
