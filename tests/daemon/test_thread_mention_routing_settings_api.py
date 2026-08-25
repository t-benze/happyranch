"""Phase-2 mention routing (THR-198) Slice B — per-thread settings API.

Founder-only read/write parity for ``threads.mention_routing_enabled``:
GET /threads/{id} (and the list) expose the boolean; POST
/threads/{id}/mention-routing toggles it with a ``thread_mention_routing_changed``
audit row under the existing ``task_id=thread_id`` scope. Mirrors the THR-209
rename/pin mutation conventions (strict bool body, 404 unknown thread,
idempotent no-op, atomic toggle+audit).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


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


@pytest.fixture
def three_agent_thread(tmp_home, app, org_state, auth_headers):
    for name in ("alpha", "bravo", "charlie"):
        _seed_agent(org_state, name)
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={
            "subject": "settings api",
            "recipients": ["alpha", "bravo", "charlie"],
            "body_markdown": "kickoff",
        },
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    return r.json()["thread_id"], client, org_state, auth_headers


def _pending_names(org_state, thread_id: str) -> dict[str, int]:
    from runtime.models import ThreadInvocationStatus
    pending: dict[str, int] = {}
    for inv in org_state.db.list_thread_invocations(thread_id):
        if inv.status == ThreadInvocationStatus.PENDING:
            pending[inv.agent_name] = pending.get(inv.agent_name, 0) + 1
    return pending


def _audit_rows(org_state, thread_id: str) -> list[dict]:
    return org_state.db._conn.execute(
        "SELECT action, task_id, agent, payload FROM audit_log "
        "WHERE action = 'thread_mention_routing_changed' ORDER BY id",
    ).fetchall()


# ---------------------------------------------------------------------------
# read parity
# ---------------------------------------------------------------------------


def test_thread_detail_exposes_default_enabled(three_agent_thread):
    thread_id, client, _org, auth_headers = three_agent_thread
    r = client.get(
        f"/api/v1/orgs/alpha/threads/{thread_id}", headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["mention_routing_enabled"] is True


def test_thread_list_exposes_setting(three_agent_thread):
    thread_id, client, _org, auth_headers = three_agent_thread
    r = client.get("/api/v1/orgs/alpha/threads", headers=auth_headers)
    assert r.status_code == 200, r.text
    row = next(
        t for t in r.json()["threads"] if t["thread_id"] == thread_id
    )
    assert row["mention_routing_enabled"] is True


# ---------------------------------------------------------------------------
# write parity — toggle + audit
# ---------------------------------------------------------------------------


def test_toggle_disabled_and_back_with_audit(three_agent_thread):
    thread_id, client, org_state, auth_headers = three_agent_thread

    r = client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/mention-routing",
        json={"mention_routing_enabled": False},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"thread_id": thread_id, "mention_routing_enabled": False}

    d = client.get(
        f"/api/v1/orgs/alpha/threads/{thread_id}", headers=auth_headers,
    ).json()
    assert d["mention_routing_enabled"] is False

    r = client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/mention-routing",
        json={"mention_routing_enabled": True},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"thread_id": thread_id, "mention_routing_enabled": True}

    rows = _audit_rows(org_state, thread_id)
    assert len(rows) == 2
    assert all(row["task_id"] == thread_id for row in rows)
    assert all(row["agent"] == "founder" for row in rows)
    assert [row["payload"] for row in rows] == [
        '{"mention_routing_enabled": false}',
        '{"mention_routing_enabled": true}',
    ]


def test_same_state_is_idempotent_noop(three_agent_thread):
    thread_id, client, org_state, auth_headers = three_agent_thread
    r = client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/mention-routing",
        json={"mention_routing_enabled": True},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["idempotent"] is True
    assert _audit_rows(org_state, thread_id) == []


# ---------------------------------------------------------------------------
# invalid inputs / boundaries
# ---------------------------------------------------------------------------


def test_non_boolean_values_rejected_strict(three_agent_thread):
    thread_id, client, _org, auth_headers = three_agent_thread
    for bad in ({"mention_routing_enabled": "yes"},
                {"mention_routing_enabled": 1},
                {"mention_routing_enabled": "false"},
                {}):
        r = client.post(
            f"/api/v1/orgs/alpha/threads/{thread_id}/mention-routing",
            json=bad,
            headers=auth_headers,
        )
        assert r.status_code == 422, (bad, r.status_code)


def test_unknown_thread_404(three_agent_thread):
    _thread_id, client, _org, auth_headers = three_agent_thread
    r = client.post(
        "/api/v1/orgs/alpha/threads/THR-NOPE/mention-routing",
        json={"mention_routing_enabled": False},
        headers=auth_headers,
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["code"] == "not_found"


# ---------------------------------------------------------------------------
# behavior effect through the API
# ---------------------------------------------------------------------------


def test_disabled_then_enabled_routing_through_api(three_agent_thread):
    thread_id, client, org_state, auth_headers = three_agent_thread
    for agent in ("alpha", "bravo", "charlie"):
        org_state.db.discard_reply_delivery(
            thread_id, agent_name=agent, decline_reason="test_settled",
        )

    # Disabled -> mention body broadcasts to everyone.
    client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/mention-routing",
        json={"mention_routing_enabled": False},
        headers=auth_headers,
    )
    r = client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/send",
        json={"body_markdown": "all eyes @bravo"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert set(r.json()["pending_replies"]) == {"alpha", "bravo", "charlie"}
    for agent in ("alpha", "bravo", "charlie"):
        org_state.db.discard_reply_delivery(
            thread_id, agent_name=agent, decline_reason="test_settled",
        )

    # Enabled again -> mention body narrows to the mentioned set.
    client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/mention-routing",
        json={"mention_routing_enabled": True},
        headers=auth_headers,
    )
    r = client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/send",
        json={"body_markdown": "just @charlie"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["pending_replies"] == ["charlie"]
    assert _pending_names(org_state, thread_id) == {"charlie": 1}


def test_bootstrap_reply_isolation_through_api(three_agent_thread):
    """A BOOTSTRAP reply keeps the full broadcast even when the body mentions
    a subset — TASK_FOLLOWUP/BOOTSTRAP are never mention-routed."""
    from runtime.models import ThreadInvocationPurpose
    thread_id, client, org_state, auth_headers = three_agent_thread
    seq1 = 1
    bootstrap = org_state.db.mint_thread_invocation(
        thread_id=thread_id, agent_name="alpha",
        triggering_seq=seq1, purpose=ThreadInvocationPurpose.BOOTSTRAP,
    )
    for agent in ("alpha", "bravo", "charlie"):
        org_state.db.discard_reply_delivery(
            thread_id, agent_name=agent, decline_reason="test_settled",
        )
    r = client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/reply",
        json={
            "thread_id": thread_id,
            "invocation_token": bootstrap.invocation_token,
            "speaker": "alpha",
            "body_markdown": "bootstrap note @bravo only",
            "in_response_to_seq": seq1,
        },
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    pending = _pending_names(org_state, thread_id)
    assert pending.get("bravo") == 1
    assert pending.get("charlie") == 1
    assert "alpha" not in pending
