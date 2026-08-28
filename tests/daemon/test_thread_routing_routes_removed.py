"""TASK-6027 — removed routing-switch routes return the established
missing-route behavior; the thread wire no longer exposes either switch.

Founder-directed removal of ALL THREE switches (shipped per-thread
``mention_routing_enabled``, proposed per-thread ``reply_exchange_enabled``,
proposed org key ``org_settings.threads.reply_exchange_enabled``). Mention
routing and the strict mention-led exchange are unconditional. The three
mutating routes (``POST /threads/{id}/mention-routing``,
``POST /threads/{id}/exchange-routing``, ``POST /threads/exchange-routing``)
are gone — FastAPI's default missing-route behavior (404 Not Found) applies.
``GET /threads/{id}`` no longer carries either boolean, and no route can
produce the retired audit events (``thread_mention_routing_changed`` /
``thread_exchange_routing_changed`` / ``org_config_write`` for the removed
key).
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
def thread_fixture(tmp_home, app, org_state, auth_headers):
    for name in ("alpha", "bravo", "charlie"):
        _seed_agent(org_state, name)
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={
            "subject": "removed routes",
            "recipients": ["alpha", "bravo", "charlie"],
            "body_markdown": "kickoff",
        },
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    return r.json()["thread_id"], client, org_state, auth_headers


def test_mention_routing_route_removed_404(thread_fixture):
    thread_id, client, _org, auth_headers = thread_fixture
    r = client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/mention-routing",
        json={"mention_routing_enabled": False},
        headers=auth_headers,
    )
    assert r.status_code == 404
    assert r.json() == {"detail": "Not Found"}


def test_per_thread_exchange_routing_route_removed_404(thread_fixture):
    thread_id, client, _org, auth_headers = thread_fixture
    r = client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/exchange-routing",
        json={"reply_exchange_enabled": False},
        headers=auth_headers,
    )
    assert r.status_code == 404
    assert r.json() == {"detail": "Not Found"}


def test_org_exchange_routing_route_removed_404(thread_fixture):
    _thread_id, client, _org, auth_headers = thread_fixture
    # ``/threads/exchange-routing`` collides with the EXISTING route pattern
    # ``GET /threads/{thread_id}`` (thread_id='exchange-routing'), so FastAPI
    # answers 405 Method Not Allowed — the established missing-route
    # behavior for a removed method on a surviving path pattern. The route
    # itself does not exist; no store write or audit happens.
    r = client.post(
        "/api/v1/orgs/alpha/threads/exchange-routing",
        json={"reply_exchange_enabled": False},
        headers=auth_headers,
    )
    assert r.status_code == 405
    assert r.json() == {"detail": "Method Not Allowed"}


def test_thread_detail_no_longer_exposes_either_switch(thread_fixture):
    thread_id, client, _org, auth_headers = thread_fixture
    r = client.get(
        f"/api/v1/orgs/alpha/threads/{thread_id}", headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert "mention_routing_enabled" not in body
    assert "reply_exchange_enabled" not in body


def test_retired_audit_events_cannot_be_produced(thread_fixture):
    """No surviving route can emit the retired routing-switch audit events."""
    thread_id, client, org_state, auth_headers = thread_fixture
    # Any attempt against the removed routes is 404/405 before any store
    # write: 404 for paths with no matching pattern; 405 for the org path,
    # which collides with the surviving ``GET /threads/{thread_id}`` pattern.
    for path, payload in (
        (f"/threads/{thread_id}/mention-routing", {"mention_routing_enabled": False}),
        (f"/threads/{thread_id}/exchange-routing", {"reply_exchange_enabled": False}),
        ("/threads/exchange-routing", {"reply_exchange_enabled": False}),
    ):
        r = client.post(
            f"/api/v1/orgs/alpha{path}", json=payload, headers=auth_headers,
        )
        assert r.status_code in (404, 405)
    rows = org_state.db._conn.execute(
        "SELECT action FROM audit_log WHERE action IN "
        "('thread_mention_routing_changed', 'thread_exchange_routing_changed')",
    ).fetchall()
    assert rows == []
