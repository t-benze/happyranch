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
