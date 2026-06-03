from __future__ import annotations

from fastapi.testclient import TestClient


def test_dispatch_with_owner_assigns_owner_not_manager(tmp_home, app, auth_headers):
    r = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks",
        headers=auth_headers,
        json={"brief": "do it", "team": "engineering", "owner": "dev_agent"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["assigned_agent"] == "dev_agent"


def test_dispatch_without_owner_defaults_to_manager(tmp_home, app, auth_headers):
    r = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks",
        headers=auth_headers,
        json={"brief": "do it", "team": "engineering"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["assigned_agent"] == "engineering_head"


def test_dispatch_with_unknown_owner_is_400(tmp_home, app, auth_headers):
    r = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks",
        headers=auth_headers,
        json={"brief": "x", "team": "engineering", "owner": "ghost"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "unknown_owner"
