from __future__ import annotations

from fastapi.testclient import TestClient

from runtime.daemon.org_state import OrgState

_CM_TASK = "TASK-200"
_CM_SESSION = "sess-cm-test"


def _activate_cm_session(org_state: OrgState) -> None:
    org_state.sessions.set_active(_CM_TASK, "content_manager", _CM_SESSION)


def test_content_manager_can_enroll_into_content(client_with_runtime) -> None:
    client, org_state = client_with_runtime
    _activate_cm_session(org_state)
    resp = client.post("/api/v1/orgs/alpha/agents/manage", json={
        "action": "enroll",
        "name": "seo_agent",
        "task_id": _CM_TASK,
        "session_id": _CM_SESSION,
        "description": "d", "system_prompt": "s", "repos": {},
    })
    assert resp.status_code == 200, resp.text


def test_content_manager_cannot_enroll_into_engineering(client_with_runtime) -> None:
    client, org_state = client_with_runtime
    _activate_cm_session(org_state)
    resp = client.post("/api/v1/orgs/alpha/agents/manage", json={
        "action": "enroll",
        "name": "hostile_agent",
        "task_id": _CM_TASK,
        "session_id": _CM_SESSION,
        "target_team": "engineering",
        "description": "d", "system_prompt": "s", "repos": {},
    })
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "cross_team_forbidden"


def test_engineering_head_still_works(client_with_runtime) -> None:
    client, org_state = client_with_runtime
    org_state.sessions.set_active("TASK-201", "engineering_head", "sess-eh-201")
    resp = client.post("/api/v1/orgs/alpha/agents/manage", json={
        "action": "enroll",
        "name": "codex_dev",
        "task_id": "TASK-201",
        "session_id": "sess-eh-201",
        "description": "d", "system_prompt": "s", "repos": {}, "executor": "codex",
    })
    assert resp.status_code == 200, resp.text
