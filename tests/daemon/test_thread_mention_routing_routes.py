"""Phase-2 mention routing (THR-198) Slice A — route-level coverage.

Proves all seven compose/reply/send writer paths reach the two central
store seams (record_conversational_arrival / reply_conversational) and that
each persists mentions_json derived server-side from body_markdown — while
production wake fan-out stays EXACTLY at today's broadcast behavior
(participants minus speaker), because Slice A must not route yet.

Author types covered: founder compose (JSON + multipart), founder send,
agent reply, agent send, post-as-agent, compose-as-agent (route + DB core),
and the dream path via _create_agent_thread_locked.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from runtime.daemon.routes.threads import _create_agent_thread_locked
from runtime.models import (
    ThreadInvocationStatus,
    ThreadRecord,
    TaskRecord,
)


def _seed_agent(org_state, name: str, *, team: str = "engineering") -> None:
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


def _seed_active_task(org_state, daemon_state, agent: str) -> tuple[str, str]:
    task_id, sid = "TASK-200", "sid-1"
    org_state.db.insert_task(TaskRecord(
        id=task_id, brief="x", team="engineering", assigned_agent=agent,
    ))
    daemon_state.orgs["alpha"].sessions.set_active(task_id, agent, sid)
    return task_id, sid


def _mentions_json(org_state, thread_id: str, seq: int):
    row = org_state.db._conn.execute(
        "SELECT mentions_json FROM thread_messages "
        "WHERE thread_id = ? AND seq = ?",
        (thread_id, seq),
    ).fetchone()
    return row["mentions_json"]


def _pending_names(org_state, thread_id: str) -> dict[str, int]:
    pending: dict[str, int] = {}
    for inv in org_state.db.list_thread_invocations(thread_id):
        if inv.status == ThreadInvocationStatus.PENDING:
            pending[inv.agent_name] = pending.get(inv.agent_name, 0) + 1
    return pending


@pytest.fixture
def three_agent_thread(tmp_home, app, org_state, auth_headers):
    """Compose a thread with three approved-agent participants."""
    for name in ("alpha", "bravo", "charlie"):
        _seed_agent(org_state, name)
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={
            "subject": "mention routing slice A",
            "recipients": ["alpha", "bravo", "charlie"],
            "body_markdown": "kickoff",
        },
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    return r.json()["thread_id"], client, org_state, auth_headers


# ---------------------------------------------------------------------------
# founder compose (writer #3 — JSON) + fan-out regression
# ---------------------------------------------------------------------------


def test_founder_compose_persists_mentions_and_keeps_broadcast(
    three_agent_thread,
):
    """A compose body that @-mentions one participant persists the mention
    AND still wakes every participant (Slice A must not route)."""
    thread_id, client, org_state, auth_headers = three_agent_thread

    r = client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/send",
        json={"body_markdown": "please look at this @bravo"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    seq = r.json()["seq"]
    assert _mentions_json(org_state, thread_id, seq) == '["bravo"]'
    # Fan-out unchanged: every participant woken, exactly one pending each.
    assert _pending_names(org_state, thread_id) == {
        "alpha": 1, "bravo": 1, "charlie": 1,
    }


def test_founder_compose_unmentioned_body_persists_empty_mentions(
    three_agent_thread,
):
    thread_id, client, org_state, auth_headers = three_agent_thread
    r = client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/send",
        json={"body_markdown": "no mentions"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    seq = r.json()["seq"]
    assert _mentions_json(org_state, thread_id, seq) == "[]"
    assert _pending_names(org_state, thread_id) == {
        "alpha": 1, "bravo": 1, "charlie": 1,
    }


# ---------------------------------------------------------------------------
# founder multipart compose (writer #2)
# ---------------------------------------------------------------------------


def test_multipart_compose_persists_mentions(
    tmp_home, app, org_state, auth_headers,
):
    _seed_agent(org_state, "alpha")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        files=[("files", ("a.txt", b"aa", "text/plain"))],
        data={"body": json.dumps({
            "subject": "multipart compose",
            "recipients": ["alpha"],
            "body_markdown": "see the file @alpha",
        })},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    thread_id = r.json()["thread_id"]
    # The arrival row carries the normalized body; mentions derived from it.
    msgs = org_state.db.list_thread_messages(thread_id)
    assert len(msgs) == 1
    assert msgs[0].mentions == ["alpha"]


# ---------------------------------------------------------------------------
# agent reply (writer #7)
# ---------------------------------------------------------------------------


def test_agent_reply_persists_mentions_and_broadcast(
    three_agent_thread,
):
    thread_id, client, org_state, auth_headers = three_agent_thread
    # Settle bravo+charlie (NOT alpha) so the assertion is about NEW wakes
    # minted by alpha's reply.
    for agent in ("bravo", "charlie"):
        org_state.db.discard_reply_delivery(
            thread_id, agent_name=agent, decline_reason="test_settled",
        )
    alpha_token = next(
        inv.invocation_token
        for inv in org_state.db.list_thread_invocations(thread_id)
        if inv.agent_name == "alpha"
        and inv.status == ThreadInvocationStatus.PENDING
    )
    r = client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/reply",
        json={
            "thread_id": thread_id,
            "invocation_token": alpha_token,
            "speaker": "alpha",
            "body_markdown": "thanks @bravo for the notes",
            "in_response_to_seq": 1,
        },
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    seq = r.json()["seq"]
    assert _mentions_json(org_state, thread_id, seq) == '["bravo"]'
    # Broadcast unchanged: bravo + charlie woken, alpha not.
    pending = _pending_names(org_state, thread_id)
    assert pending.get("bravo") == 1
    assert pending.get("charlie") == 1
    assert "alpha" not in pending


# ---------------------------------------------------------------------------
# agent /send with binding (writer #5 agent path -> #6)
# ---------------------------------------------------------------------------


def test_agent_send_persists_mentions(
    tmp_home, app, org_state, auth_headers, daemon_state,
):
    _seed_agent(org_state, "alpha")
    _seed_agent(org_state, "bravo")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={
            "subject": "agent send", "recipients": ["alpha", "bravo"],
            "body_markdown": "kickoff",
        },
        headers=auth_headers,
    )
    thread_id = r.json()["thread_id"]
    task_id, sid = _seed_active_task(org_state, daemon_state, "alpha")

    r2 = client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/send",
        json={
            "body_markdown": "updating @bravo",
            "composer": "alpha", "task_id": task_id, "session_id": sid,
        },
        headers=auth_headers,
    )
    assert r2.status_code == 200, r2.text
    seq = r2.json()["seq"]
    assert _mentions_json(org_state, thread_id, seq) == '["bravo"]'
    # Fan-out unchanged: alpha still holds its compose-step wake; bravo's
    # pair coalesces the send into its existing queued wake (1 pending).
    assert _pending_names(org_state, thread_id) == {"alpha": 1, "bravo": 1}


# ---------------------------------------------------------------------------
# post-as-agent (writer #6 via /post-as-agent)
# ---------------------------------------------------------------------------


def test_post_as_agent_persists_mentions(
    tmp_home, app, org_state, auth_headers, daemon_state,
):
    _seed_agent(org_state, "alpha")
    _seed_agent(org_state, "bravo")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={
            "subject": "post-as-agent", "recipients": ["alpha", "bravo"],
            "body_markdown": "kickoff",
        },
        headers=auth_headers,
    )
    thread_id = r.json()["thread_id"]
    task_id, sid = _seed_active_task(org_state, daemon_state, "alpha")

    r2 = client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/post-as-agent",
        json={
            "composer": "alpha", "task_id": task_id, "session_id": sid,
            "body_markdown": "status @bravo",
        },
        headers=auth_headers,
    )
    assert r2.status_code == 200, r2.text
    seq = r2.json()["seq"]
    assert _mentions_json(org_state, thread_id, seq) == '["bravo"]'
    # Fan-out unchanged: alpha's compose-step wake persists; bravo's pair
    # coalesces the post into its existing queued wake (1 pending).
    assert _pending_names(org_state, thread_id) == {"alpha": 1, "bravo": 1}


# ---------------------------------------------------------------------------
# compose-as-agent (writer #4 route + #1 DB core)
# ---------------------------------------------------------------------------


def test_compose_as_agent_persists_mentions(
    tmp_home, app, org_state, auth_headers, daemon_state,
):
    _seed_agent(org_state, "engineering_head")
    _seed_agent(org_state, "payment_agt")
    task_id, sid = _seed_active_task(org_state, daemon_state, "engineering_head")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head", "subject": "subj",
            "recipients": ["payment_agt"],
            "body_markdown": "please handle @payment_agt",
            "task_id": task_id, "session_id": sid,
        },
    )
    assert r.status_code == 200, r.text
    thread_id = r.json()["thread_id"]
    msgs = org_state.db.list_thread_messages(thread_id)
    assert len(msgs) == 1
    assert msgs[0].mentions == ["payment_agt"]


def test_create_agent_thread_locked_core_persists_mentions(tmp_path):
    """The agent-compose DB core (writer #1; also the dream founder-thread
    path via dreams.py) persists mentions without any HTTP layer."""
    from runtime.infrastructure.database import Database

    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))

    class _Org:
        def __init__(self, database):
            self.db = database
            self.db_lock = database._lock

    org = _Org(db)
    with org.db_lock:
        thread_id, seq, _tokens, addressed = _create_agent_thread_locked(
            org,
            composer="alpha",
            subject="core compose",
            body_text="handle this @bravo please",
            recipients=["alpha", "bravo", "charlie"],
            turn_cap=500,
        )
    assert addressed == ["bravo", "charlie"]
    row = db._conn.execute(
        "SELECT mentions_json FROM thread_messages "
        "WHERE thread_id = ? AND seq = ?",
        (thread_id, seq),
    ).fetchone()
    assert row["mentions_json"] == '["bravo"]'
    # Fan-out unchanged: one REPLY per addressed agent (minus composer).
    invs = db.list_thread_invocations(thread_id)
    assert sorted(i.agent_name for i in invs) == ["bravo", "charlie"]
    assert all(i.purpose.value == "reply" for i in invs)
