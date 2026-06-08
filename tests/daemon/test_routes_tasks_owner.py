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


def test_dispatch_owner_without_team_derives_owner_team(tmp_home, app, auth_headers):
    # content_writer is on the 'content' team; omitting --team must derive it,
    # so the task.team children inherit matches the owner's real team.
    r = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks",
        headers=auth_headers,
        json={"brief": "write copy", "owner": "content_writer"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["assigned_agent"] == "content_writer"
    assert body["team"] == "content"


def test_dispatch_owner_team_mismatch_is_400(tmp_home, app, auth_headers):
    # content_writer is on 'content', not 'engineering' → reject the mismatch
    # rather than create an engineering task owned by a content agent.
    r = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks",
        headers=auth_headers,
        json={"brief": "x", "team": "engineering", "owner": "content_writer"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "owner_team_mismatch"


def test_dispatch_manager_owner_without_team_derives_team(tmp_home, app, auth_headers):
    # A manager owner resolves its team via team_for_manager (managers aren't
    # in teams.yaml `workers`).
    r = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks",
        headers=auth_headers,
        json={"brief": "lead it", "owner": "content_manager"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["assigned_agent"] == "content_manager"
    assert body["team"] == "content"
