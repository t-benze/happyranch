"""Route tests for POST /threads/compose-as-agent (agent-initiated threads)."""
from __future__ import annotations

from fastapi.testclient import TestClient


def _seed_agent(org_state, name: str, *, team: str = "engineering") -> None:
    """Create the agent's frontmatter file and workspace dir."""
    agents_dir = org_state.root / "org" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / f"{name}.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"team: {team}\n"
        "role: worker\n"
        "executor: claude\n"
        "description: test agent\n"
        "---\n"
        "# system prompt\n"
    )
    (org_state.root / "workspaces" / name).mkdir(parents=True, exist_ok=True)


def test_compose_as_agent_route_rejects_empty_subject(tmp_home, app, org_state, auth_headers):
    _seed_agent(org_state, "engineering_head")
    _seed_agent(org_state, "payment_agt")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head",
            "subject": "",
            "recipients": ["payment_agt"],
            "body_markdown": "hi",
            "task_id": "TASK-1", "session_id": "abc",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "empty_subject"


def test_compose_as_agent_route_rejects_empty_body(tmp_home, app, org_state, auth_headers):
    _seed_agent(org_state, "engineering_head")
    _seed_agent(org_state, "payment_agt")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head",
            "subject": "s",
            "recipients": ["payment_agt"],
            "body_markdown": "   ",
            "task_id": "TASK-1", "session_id": "abc",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "empty_body"


def test_compose_as_agent_route_rejects_empty_recipients(tmp_home, app, org_state, auth_headers):
    _seed_agent(org_state, "engineering_head")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head",
            "subject": "s",
            "recipients": [],
            "body_markdown": "hi",
            "task_id": "TASK-1", "session_id": "abc",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "empty_recipients"


def test_compose_as_agent_rejects_missing_binding(tmp_home, app, org_state, auth_headers):
    _seed_agent(org_state, "engineering_head")
    _seed_agent(org_state, "payment_agt")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head",
            "subject": "s",
            "recipients": ["payment_agt"],
            "body_markdown": "b",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "binding_required"


def test_compose_as_agent_rejects_dual_binding(tmp_home, app, org_state, auth_headers):
    _seed_agent(org_state, "engineering_head")
    _seed_agent(org_state, "payment_agt")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head",
            "subject": "s",
            "recipients": ["payment_agt"],
            "body_markdown": "b",
            "task_id": "TASK-1", "session_id": "abc",
            "talk_id": "TALK-1",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "binding_ambiguous"


def test_compose_as_agent_rejects_unknown_composer(tmp_home, app, org_state, auth_headers):
    _seed_agent(org_state, "payment_agt")  # composer "nobody" is NOT seeded
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "nobody",
            "subject": "s",
            "recipients": ["payment_agt"],
            "body_markdown": "b",
            "task_id": "TASK-1", "session_id": "abc",
        },
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "unknown_composer"


def test_compose_as_agent_task_binding_missing_session_id(tmp_home, app, org_state, auth_headers):
    """task_id without session_id is invalid."""
    _seed_agent(org_state, "engineering_head")
    _seed_agent(org_state, "payment_agt")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head",
            "subject": "s",
            "recipients": ["payment_agt"],
            "body_markdown": "b",
            "task_id": "TASK-1",
            # session_id intentionally omitted
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "binding_required"
