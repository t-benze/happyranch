from __future__ import annotations

from fastapi.testclient import TestClient

from tests.daemon.conftest import client, open_talk_for  # type: ignore  # noqa: F401


def test_content_manager_can_enroll_into_content(client: TestClient) -> None:
    talk_id = open_talk_for(client, "content_manager")
    resp = client.post("/api/v1/agents/manage", json={
        "action": "enroll",
        "name": "seo_agent",
        "talk_id": talk_id,
        "description": "d", "system_prompt": "s", "repos": {},
    })
    assert resp.status_code == 200, resp.text


def test_content_manager_cannot_enroll_into_engineering(client: TestClient) -> None:
    talk_id = open_talk_for(client, "content_manager")
    resp = client.post("/api/v1/agents/manage", json={
        "action": "enroll",
        "name": "hostile_agent",
        "talk_id": talk_id,
        "target_team": "engineering",
        "description": "d", "system_prompt": "s", "repos": {},
    })
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "cross_team_forbidden"


def test_engineering_head_still_works(client: TestClient) -> None:
    talk_id = open_talk_for(client, "engineering_head")
    resp = client.post("/api/v1/agents/manage", json={
        "action": "enroll",
        "name": "codex_dev",
        "talk_id": talk_id,
        "description": "d", "system_prompt": "s", "repos": {}, "executor": "codex",
    })
    assert resp.status_code == 200, resp.text
