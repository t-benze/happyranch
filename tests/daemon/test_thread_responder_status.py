"""responder_status field on GET /threads/{id}.

Spec: docs/superpowers/specs/2026-05-30-thread-broadcast-only-design.md §9
"""
from __future__ import annotations

import pytest


def _seed_agent(org_state, name: str, *, team: str = "engineering", role: str = "worker") -> None:
    """Create the agent's frontmatter file and workspace dir."""
    agents_dir = org_state.root / "org" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / f"{name}.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"team: {team}\n"
        f"role: {role}\n"
        "executor: claude\n"
        "description: test agent\n"
        "---\n"
        "# system prompt\n"
    )
    (org_state.root / "workspaces" / name).mkdir(parents=True, exist_ok=True)


@pytest.fixture
def org_slug() -> str:
    return "alpha"


@pytest.fixture
def db(org_state):
    return org_state.db


@pytest.fixture
def three_agent_thread(tmp_home, client, org_state, org_slug):
    """Compose a thread with three approved-agent participants; return thread_id."""
    _seed_agent(org_state, "alpha")
    _seed_agent(org_state, "bravo")
    _seed_agent(org_state, "charlie")
    r = client.post(
        f"/api/v1/orgs/{org_slug}/threads",
        json={
            "subject": "responder status test",
            "recipients": ["alpha", "bravo", "charlie"],
            "body_markdown": "kickoff",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["thread_id"]


def test_responder_status_present_on_get(client, org_slug, three_agent_thread):
    """Every kind=message in the thread has a responder_status array
    with one entry per non-speaker participant."""
    thread_id = three_agent_thread
    r = client.get(f"/api/v1/orgs/{org_slug}/threads/{thread_id}")
    assert r.status_code == 200
    data = r.json()
    kickoff = data["messages"][0]
    assert kickoff["kind"] == "message"
    statuses = kickoff["responder_status"]
    agents = sorted(s["agent_name"] for s in statuses)
    assert agents == ["alpha", "bravo", "charlie"]
    assert all(s["status"] == "pending" for s in statuses)
    assert all(s["responded_at"] is None for s in statuses)


def test_responder_status_reflects_replied_state(
    client, org_slug, three_agent_thread, db
):
    thread_id = three_agent_thread
    alpha_inv = db._conn.execute(
        "SELECT invocation_token FROM thread_invocations "
        "WHERE thread_id=? AND agent_name='alpha' AND status='pending'",
        (thread_id,),
    ).fetchone()
    client.post(
        f"/api/v1/orgs/{org_slug}/threads/{thread_id}/reply",
        json={"thread_id": thread_id,
              "invocation_token": alpha_inv["invocation_token"],
              "speaker": "alpha",
              "body_markdown": "alpha responding",
              "in_response_to_seq": 1},
    )

    r = client.get(f"/api/v1/orgs/{org_slug}/threads/{thread_id}")
    kickoff = r.json()["messages"][0]
    alpha_entry = next(s for s in kickoff["responder_status"] if s["agent_name"] == "alpha")
    assert alpha_entry["status"] == "replied"   # wire-renamed from DB 'consumed'
    assert alpha_entry["responded_at"] is not None


def test_responder_status_reflects_declined_state(
    client, org_slug, three_agent_thread, db
):
    thread_id = three_agent_thread
    alpha_inv = db._conn.execute(
        "SELECT invocation_token FROM thread_invocations "
        "WHERE thread_id=? AND agent_name='alpha' AND status='pending'",
        (thread_id,),
    ).fetchone()
    client.post(
        f"/api/v1/orgs/{org_slug}/threads/{thread_id}/decline",
        json={"thread_id": thread_id,
              "invocation_token": alpha_inv["invocation_token"],
              "speaker": "alpha",
              "reason": "no material to add",
              "in_response_to_seq": 1},
    )

    r = client.get(f"/api/v1/orgs/{org_slug}/threads/{thread_id}")
    kickoff = r.json()["messages"][0]
    alpha_entry = next(s for s in kickoff["responder_status"] if s["agent_name"] == "alpha")
    assert alpha_entry["status"] == "declined"
    assert alpha_entry["responded_at"] is not None


def test_responder_status_maps_timeout_to_failed(
    client, org_slug, three_agent_thread, db
):
    """§9: DB status 'timeout' is exposed as wire status 'failed'.
    Users don't need to distinguish crash from timeout at the strip level."""
    thread_id = three_agent_thread
    # Directly set alpha's pending invocation to timeout in the DB
    # (simulates what thread_runner does on session timeout).
    db._conn.execute(
        "UPDATE thread_invocations SET status='timeout', "
        "consumed_at=datetime('now') "
        "WHERE thread_id=? AND agent_name='alpha' AND status='pending'",
        (thread_id,),
    )
    db._conn.commit()

    r = client.get(f"/api/v1/orgs/{org_slug}/threads/{thread_id}")
    kickoff = r.json()["messages"][0]
    alpha_entry = next(s for s in kickoff["responder_status"] if s["agent_name"] == "alpha")
    assert alpha_entry["status"] == "failed"
    assert alpha_entry["responded_at"] is not None
