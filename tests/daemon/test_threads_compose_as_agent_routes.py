"""Route tests for POST /threads/compose-as-agent (agent-initiated threads)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from src.models import TalkRecord, TalkStatus, TaskRecord, TaskStatus


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


def test_compose_as_agent_task_path_rejects_unowned_task(tmp_home, app, org_state, auth_headers):
    _seed_agent(org_state, "engineering_head")
    _seed_agent(org_state, "payment_agt")
    # Seed a task assigned to payment_agt, but composer claims engineering_head.
    org_state.db.insert_task(TaskRecord(
        id="TASK-50", brief="x", team="engineering", assigned_agent="payment_agt",
    ))
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head", "subject": "s",
            "recipients": ["payment_agt"], "body_markdown": "b",
            "task_id": "TASK-50", "session_id": "abc",
        },
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "composer_not_task_owner"


def test_compose_as_agent_task_path_rejects_unknown_task(tmp_home, app, org_state, auth_headers):
    _seed_agent(org_state, "engineering_head")
    _seed_agent(org_state, "payment_agt")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head", "subject": "s",
            "recipients": ["payment_agt"], "body_markdown": "b",
            "task_id": "TASK-9999", "session_id": "abc",
        },
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "unknown_task"


def test_compose_as_agent_task_path_rejects_session_mismatch(tmp_home, app, org_state, auth_headers, daemon_state):
    _seed_agent(org_state, "engineering_head")
    _seed_agent(org_state, "payment_agt")
    org_state.db.insert_task(TaskRecord(
        id="TASK-51", brief="x", team="engineering", assigned_agent="engineering_head",
    ))
    daemon_state.orgs["alpha"].sessions.set_active("TASK-51", "engineering_head", "real-session")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head", "subject": "s",
            "recipients": ["payment_agt"], "body_markdown": "b",
            "task_id": "TASK-51", "session_id": "wrong",
        },
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "session_mismatch"


def test_compose_as_agent_talk_path_rejects_unknown_talk(tmp_home, app, org_state, auth_headers):
    _seed_agent(org_state, "engineering_head")
    _seed_agent(org_state, "payment_agt")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head", "subject": "s",
            "recipients": ["payment_agt"], "body_markdown": "b",
            "talk_id": "TALK-9999",
        },
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "unknown_talk"


def test_compose_as_agent_talk_path_rejects_closed_talk(tmp_home, app, org_state, auth_headers):
    _seed_agent(org_state, "engineering_head")
    _seed_agent(org_state, "payment_agt")
    org_state.db.insert_talk(TalkRecord(
        id="TALK-9", agent_name="engineering_head", status=TalkStatus.CLOSED,
    ))
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head", "subject": "s",
            "recipients": ["payment_agt"], "body_markdown": "b",
            "talk_id": "TALK-9",
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "talk_not_open"


def test_compose_as_agent_talk_path_rejects_unowned_talk(tmp_home, app, org_state, auth_headers):
    _seed_agent(org_state, "engineering_head")
    _seed_agent(org_state, "payment_agt")
    org_state.db.insert_talk(TalkRecord(
        id="TALK-10", agent_name="payment_agt", status=TalkStatus.OPEN,
    ))
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head", "subject": "s",
            "recipients": ["payment_agt"], "body_markdown": "b",
            "talk_id": "TALK-10",
        },
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "composer_not_talk_owner"


def test_compose_as_agent_task_path_rejects_completed_task(
    tmp_home, app, org_state, auth_headers, daemon_state,
):
    """Task already in a terminal state (completed/failed) is rejected as task_not_active."""
    _seed_agent(org_state, "engineering_head")
    _seed_agent(org_state, "payment_agt")
    org_state.db.insert_task(TaskRecord(
        id="TASK-60", brief="x", team="engineering",
        assigned_agent="engineering_head", status=TaskStatus.COMPLETED,
    ))
    daemon_state.orgs["alpha"].sessions.set_active("TASK-60", "engineering_head", "sid-60")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head", "subject": "s",
            "recipients": ["payment_agt"], "body_markdown": "b",
            "task_id": "TASK-60", "session_id": "sid-60",
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "task_not_active"
    assert r.json()["detail"]["status"] == "completed"
