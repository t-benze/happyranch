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
