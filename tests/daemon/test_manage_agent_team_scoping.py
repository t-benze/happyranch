from __future__ import annotations

from fastapi.testclient import TestClient

from runtime.daemon.org_state import OrgState
from runtime.models import TalkRecord


def _seed_open_talk(org_state: OrgState, agent_name: str) -> str:
    """Seed an open talk for *agent_name* directly via the org's DB.

    The talks HTTP route (POST /talks/start) hasn't been migrated to per-org
    URLs yet (Task 14), so we insert directly via the per-org DB to keep these
    agent-route tests independent of talks.py.
    """
    talk_id = org_state.db.next_talk_id()
    org_state.db.insert_talk(TalkRecord(id=talk_id, agent_name=agent_name))
    return talk_id


def test_content_manager_can_enroll_into_content(client_with_runtime) -> None:
    client, org_state = client_with_runtime
    talk_id = _seed_open_talk(org_state, "content_manager")
    resp = client.post("/api/v1/orgs/alpha/agents/manage", json={
        "action": "enroll",
        "name": "seo_agent",
        "talk_id": talk_id,
        "description": "d", "system_prompt": "s", "repos": {},
    })
    assert resp.status_code == 200, resp.text


def test_content_manager_cannot_enroll_into_engineering(client_with_runtime) -> None:
    client, org_state = client_with_runtime
    talk_id = _seed_open_talk(org_state, "content_manager")
    resp = client.post("/api/v1/orgs/alpha/agents/manage", json={
        "action": "enroll",
        "name": "hostile_agent",
        "talk_id": talk_id,
        "target_team": "engineering",
        "description": "d", "system_prompt": "s", "repos": {},
    })
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "cross_team_forbidden"


def test_engineering_head_still_works(client_with_runtime) -> None:
    client, org_state = client_with_runtime
    talk_id = _seed_open_talk(org_state, "engineering_head")
    resp = client.post("/api/v1/orgs/alpha/agents/manage", json={
        "action": "enroll",
        "name": "codex_dev",
        "talk_id": talk_id,
        "description": "d", "system_prompt": "s", "repos": {}, "executor": "codex",
    })
    assert resp.status_code == 200, resp.text
