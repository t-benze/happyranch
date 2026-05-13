from __future__ import annotations

from fastapi.testclient import TestClient

# We use the existing daemon conftest fixtures: tmp_home, app, org_state, auth_headers.
# Helper to seed an approved agent in the alpha org.


def _seed_agent(org_state, name: str, *, team: str = "engineering") -> None:
    """Create the agent's pending file and workspace dir.

    The compose endpoint validates: `prompt_loader.load_agent(...)` is not None
    AND `<root>/workspaces/<name>` exists.
    """
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


def test_compose_creates_thread_and_invocations(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    _seed_agent(org_state, "qa_engineer")

    resp = client.post(
        "/api/v1/orgs/alpha/threads",
        json={
            "subject": "Refund policy",
            "recipients": ["dev_agent", "qa_engineer"],
            "body_markdown": "should we cap refunds at 30 days?",
            "addressed_to": ["@all"],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["thread_id"].startswith("THR-")
    assert set(data["pending_replies"]) == {"dev_agent", "qa_engineer"}

    invocations = org_state.db.list_thread_invocations(data["thread_id"])
    assert len(invocations) == 2
    assert all(inv.purpose.value == "reply" for inv in invocations)


def test_compose_rejects_unknown_recipient(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    resp = client.post(
        "/api/v1/orgs/alpha/threads",
        json={
            "subject": "x",
            "recipients": ["ghost"],
            "body_markdown": "hi",
            "addressed_to": ["@all"],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "unknown_agent"


def test_compose_rejects_empty_subject(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    resp = client.post(
        "/api/v1/orgs/alpha/threads",
        json={
            "subject": "   ",
            "recipients": ["dev_agent"],
            "body_markdown": "hi",
            "addressed_to": ["@all"],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Task 20 — GET /threads, GET /threads/{id}, GET /threads/{id}/messages
# ---------------------------------------------------------------------------


def test_list_threads_returns_recent(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "a", "recipients": ["dev_agent"], "body_markdown": "x", "addressed_to": ["@all"]},
        headers=auth_headers,
    )
    client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "b", "recipients": ["dev_agent"], "body_markdown": "x", "addressed_to": ["@all"]},
        headers=auth_headers,
    )
    resp = client.get("/api/v1/orgs/alpha/threads", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["threads"]) == 2
    assert data["threads"][0]["subject"] in {"a", "b"}


def test_get_thread_returns_messages_and_participants(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "a", "recipients": ["dev_agent"], "body_markdown": "hi", "addressed_to": ["@all"]},
        headers=auth_headers,
    ).json()
    tid = r["thread_id"]
    resp = client.get(f"/api/v1/orgs/alpha/threads/{tid}", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["thread_id"] == tid
    assert data["participants"] == ["dev_agent"]
    assert data["messages"][0]["body_markdown"] == "hi"


def test_get_thread_missing_returns_404(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    resp = client.get("/api/v1/orgs/alpha/threads/THR-999", headers=auth_headers)
    assert resp.status_code == 404
