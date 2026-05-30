from __future__ import annotations

from fastapi.testclient import TestClient


def test_list_teams_returns_seeded_teams(tmp_home, app, auth_headers) -> None:
    r = TestClient(app).get("/api/v1/orgs/alpha/teams", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    teams = body["teams"]
    # Sorted alphabetically: content before engineering.
    assert [t["name"] for t in teams] == ["content", "engineering"]
    eng = next(t for t in teams if t["name"] == "engineering")
    assert eng["manager"] == "engineering_head"
    assert "product_manager" in eng["workers"]


def test_list_teams_unknown_org_404(tmp_home, app, auth_headers) -> None:
    r = TestClient(app).get("/api/v1/orgs/nonsense/teams", headers=auth_headers)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "unknown_org"


def test_list_teams_requires_auth(tmp_home, app) -> None:
    r = TestClient(app).get("/api/v1/orgs/alpha/teams")
    assert r.status_code in (401, 403)


def test_list_teams_idle_returns_409(tmp_home, app_idle, auth_headers) -> None:
    r = TestClient(app_idle).get("/api/v1/orgs/alpha/teams", headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "no_active_runtime"


def test_list_teams_empty_for_fresh_org(tmp_home, app, auth_headers) -> None:
    # Provision a brand-new org with no teams, then list its teams.
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs", headers=auth_headers, json={"slug": "fresh"},
    )
    assert r.status_code == 200, r.text
    r = client.get("/api/v1/orgs/fresh/teams", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"teams": []}
