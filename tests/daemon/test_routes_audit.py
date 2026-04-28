from __future__ import annotations

from fastapi.testclient import TestClient


def _seed(state):
    state.db.insert_audit_log("TASK-001", "dev_agent", "session_start", {"w": "/tmp/a"})
    state.db.insert_audit_log("TASK-001", "dev_agent", "session_end", {"duration_seconds": 30})
    state.db.insert_audit_log("TASK-002", "engineering_head", "escalation", {"reason": "x"})


def test_audit_requires_token(tmp_home, app) -> None:
    r = TestClient(app).get("/api/v1/orgs/alpha/audit")
    assert r.status_code == 401


def test_audit_idle_returns_409(tmp_home, app_idle, auth_headers) -> None:
    r = TestClient(app_idle).get("/api/v1/orgs/alpha/audit", headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "no_active_runtime"


def test_audit_returns_all_entries(tmp_home, app, org_state, auth_headers) -> None:
    _seed(org_state)
    r = TestClient(app).get("/api/v1/orgs/alpha/audit", headers=auth_headers)
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert len(entries) == 3
    assert [e["id"] for e in entries] == [1, 2, 3]
    assert entries[0]["payload"] == {"w": "/tmp/a"}


def test_audit_filters_by_task_id(tmp_home, app, org_state, auth_headers) -> None:
    _seed(org_state)
    r = TestClient(app).get(
        "/api/v1/orgs/alpha/audit", params={"task_id": "TASK-001"}, headers=auth_headers,
    )
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert {e["task_id"] for e in entries} == {"TASK-001"}
    assert len(entries) == 2


def test_audit_filters_by_action_and_agent(tmp_home, app, org_state, auth_headers) -> None:
    _seed(org_state)
    r = TestClient(app).get(
        "/api/v1/orgs/alpha/audit",
        params={"agent": "engineering_head", "action": "escalation"},
        headers=auth_headers,
    )
    entries = r.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["payload"] == {"reason": "x"}


def test_audit_limit_caps_to_most_recent(tmp_home, app, org_state, auth_headers) -> None:
    _seed(org_state)
    r = TestClient(app).get(
        "/api/v1/orgs/alpha/audit", params={"limit": 2}, headers=auth_headers,
    )
    entries = r.json()["entries"]
    assert [e["id"] for e in entries] == [2, 3]
