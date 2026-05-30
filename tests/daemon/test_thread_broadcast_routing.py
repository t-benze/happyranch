"""Broadcast-mint routing tests for threads.

Spec: docs/superpowers/specs/2026-05-30-thread-broadcast-only-design.md §4
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


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
def three_agent_thread(tmp_home, app, org_state, auth_headers):
    """Compose a thread with three approved-agent participants."""
    _seed_agent(org_state, "alpha")
    _seed_agent(org_state, "bravo")
    _seed_agent(org_state, "charlie")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={
            "subject": "broadcast routing test",
            "recipients": ["alpha", "bravo", "charlie"],
            "body_markdown": "kickoff",
        },
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    return r.json()["thread_id"], client, org_state, auth_headers


def test_founder_compose_mints_one_invocation_per_participant(
    three_agent_thread,
):
    """§4: every kind=message mints REPLY for every participant except speaker.
    The founder is not a participant, so all three agents get invocations."""
    thread_id, client, org_state, auth_headers = three_agent_thread
    rows = org_state.db.list_thread_invocations(thread_id)
    agent_names = sorted(r.agent_name for r in rows)
    assert agent_names == ["alpha", "bravo", "charlie"]
    assert all(r.purpose.value == "reply" for r in rows)
    assert all(r.status.value == "pending" for r in rows)


def test_agent_reply_excludes_self_from_broadcast(
    three_agent_thread,
):
    """§4: speaker self-exclusion. When 'alpha' replies, bravo + charlie get
    NEW invocations but alpha does NOT.

    We first consume bravo + charlie's initial (compose-step) invocations so
    the assertion is unambiguously about NEW invocations minted by the reply.
    """
    thread_id, client, org_state, auth_headers = three_agent_thread

    from src.models import ThreadInvocationStatus
    all_invs = org_state.db.list_thread_invocations(thread_id)

    # Consume bravo + charlie's existing invocations so we can cleanly detect
    # any NEW ones minted by alpha's reply.
    for inv in all_invs:
        if inv.agent_name in ("bravo", "charlie"):
            org_state.db.consume_invocation(inv.invocation_token)

    # Get alpha's pending invocation and consume it by replying.
    alpha_invs = [
        inv for inv in all_invs
        if inv.agent_name == "alpha" and inv.status == ThreadInvocationStatus.PENDING
    ]
    assert len(alpha_invs) == 1, "alpha should have exactly one pending invocation from compose"
    alpha_token = alpha_invs[0].invocation_token

    # Compose message seq is 1; alpha replies to that.
    r = client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/reply",
        json={
            "thread_id": thread_id,
            "invocation_token": alpha_token,
            "speaker": "alpha",
            "body_markdown": "alpha responding",
            "in_response_to_seq": 1,
        },
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text

    # After alpha's reply: bravo + charlie should each have a NEW pending
    # invocation; alpha should have NO new pending invocation.
    pending = {}
    for inv in org_state.db.list_thread_invocations(thread_id):
        if inv.status == ThreadInvocationStatus.PENDING:
            pending[inv.agent_name] = pending.get(inv.agent_name, 0) + 1

    assert pending.get("bravo") == 1, f"bravo should have 1 pending, got {pending}"
    assert pending.get("charlie") == 1, f"charlie should have 1 pending, got {pending}"
    assert "alpha" not in pending, f"alpha should have no pending invocations, got {pending}"


def test_founder_not_pinged_on_agent_reply(
    three_agent_thread,
):
    """§4: the founder is not in thread_participants and is never a
    mint target. No row with agent_name='founder' or '@founder' should
    exist after any compose or agent reply."""
    thread_id, client, org_state, auth_headers = three_agent_thread

    # Verify no founder-targeted invocations exist after compose.
    rows = org_state.db.list_thread_invocations(thread_id)
    names = {r.agent_name for r in rows}
    assert "founder" not in names
    assert "@founder" not in names


def test_turns_used_increments_per_message_not_per_invocation(
    three_agent_thread,
):
    """§7: turns_used increments once per kind=message row, regardless of
    participant count."""
    thread_id, client, org_state, auth_headers = three_agent_thread
    db = org_state.db

    # After compose (1 message, 3 participants), turns_used should be 1, not 3.
    row = db._conn.execute(
        "SELECT turns_used FROM threads WHERE id=?", (thread_id,)
    ).fetchone()
    assert row["turns_used"] == 1

    # After one agent reply (another message), turns_used should be 2.
    alpha_inv = db._conn.execute(
        "SELECT invocation_token FROM thread_invocations "
        "WHERE thread_id=? AND agent_name='alpha' AND status='pending'",
        (thread_id,),
    ).fetchone()
    r = client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/reply",
        json={"thread_id": thread_id,
              "invocation_token": alpha_inv["invocation_token"],
              "speaker": "alpha",
              "body_markdown": "alpha responding",
              "in_response_to_seq": 1},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    row = db._conn.execute(
        "SELECT turns_used FROM threads WHERE id=?", (thread_id,)
    ).fetchone()
    assert row["turns_used"] == 2


def test_founder_send_broadcasts_to_all_participants(tmp_home, app, org_state, auth_headers):
    """§4: founder /send mints REPLY for every participant (all 3 agents)."""
    _seed_agent(org_state, "alpha")
    _seed_agent(org_state, "bravo")
    _seed_agent(org_state, "charlie")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={
            "subject": "broadcast send test",
            "recipients": ["alpha", "bravo", "charlie"],
            "body_markdown": "kickoff",
        },
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    thread_id = r.json()["thread_id"]

    # Consume all pending invocations so no pending load.
    from src.models import ThreadInvocationStatus
    for inv in org_state.db.list_thread_invocations(thread_id):
        if inv.status == ThreadInvocationStatus.PENDING:
            org_state.db.consume_invocation(inv.invocation_token)

    # Founder sends a follow-up — should mint for all 3 participants.
    r2 = client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/send",
        json={"body_markdown": "any thoughts everyone?"},
        headers=auth_headers,
    )
    assert r2.status_code == 200, r2.text

    # After send: exactly 3 new pending invocations (one per participant).
    pending = {}
    for inv in org_state.db.list_thread_invocations(thread_id):
        if inv.status == ThreadInvocationStatus.PENDING:
            pending[inv.agent_name] = pending.get(inv.agent_name, 0) + 1

    assert pending == {"alpha": 1, "bravo": 1, "charlie": 1}, f"got {pending}"
