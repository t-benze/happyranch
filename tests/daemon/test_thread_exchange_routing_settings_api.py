"""TASK-5966 — strict mention-led exchange: per-thread settings API.

Read/write parity for ``threads.reply_exchange_enabled`` (the INDEPENDENT
rollback control — ``mention_routing_enabled`` is never touched): GET
/threads/{id} exposes the boolean; POST /threads/{id}/exchange-routing
toggles it with a ``thread_exchange_routing_changed`` audit row under the
existing ``task_id=thread_id`` scope. Mirrors the THR-198 mention-routing
route conventions: strict bool body (int/string "1" rejected), 404 unknown
thread, idempotent no-op, atomic toggle+audit.
"""
from __future__ import annotations

import json

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
def exchange_thread(tmp_home, app, org_state, auth_headers):
    for name in ("alpha", "bravo", "charlie"):
        _seed_agent(org_state, name)
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={
            "subject": "exchange api",
            "recipients": ["alpha", "bravo", "charlie"],
            "body_markdown": "kickoff",
        },
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    return r.json()["thread_id"], client, org_state, auth_headers


def _audit_rows(org_state, thread_id: str) -> list[dict]:
    rows = org_state.db._conn.execute(
        "SELECT action, task_id, agent, payload FROM audit_log "
        "WHERE action = 'thread_exchange_routing_changed' ORDER BY id",
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(r["payload"])
        out.append(d)
    return out


def test_thread_detail_exposes_reply_exchange_enabled_default(exchange_thread):
    thread_id, client, _org, auth_headers = exchange_thread
    r = client.get(
        f"/api/v1/orgs/alpha/threads/{thread_id}", headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reply_exchange_enabled"] is True
    assert body["mention_routing_enabled"] is True


def test_exchange_routing_toggle_is_atomic_with_audit(exchange_thread):
    thread_id, client, org_state, auth_headers = exchange_thread
    r = client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/exchange-routing",
        json={"reply_exchange_enabled": False},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["reply_exchange_enabled"] is False
    rows = _audit_rows(org_state, thread_id)
    assert len(rows) == 1
    assert rows[0]["task_id"] == thread_id
    assert rows[0]["payload"]["reply_exchange_enabled"] is False
    # mention_routing_enabled is untouched (independent rollback control).
    t = org_state.db.get_thread(thread_id)
    assert t.mention_routing_enabled is True
    assert t.reply_exchange_enabled is False


def test_exchange_routing_toggle_idempotent_no_op(exchange_thread):
    thread_id, client, org_state, auth_headers = exchange_thread
    # First POST flips to False (one durable transition + one audit).
    r = client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/exchange-routing",
        json={"reply_exchange_enabled": False},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["reply_exchange_enabled"] is False
    # Same-state repeat: a true no-op (idempotent flag, NO extra audit).
    r = client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/exchange-routing",
        json={"reply_exchange_enabled": False},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json().get("idempotent") is True
    assert len(_audit_rows(org_state, thread_id)) == 1


def test_exchange_routing_unknown_thread_404(exchange_thread):
    _thread_id, client, _org, auth_headers = exchange_thread
    r = client.post(
        "/api/v1/orgs/alpha/threads/THR-MISSING/exchange-routing",
        json={"reply_exchange_enabled": False},
        headers=auth_headers,
    )
    assert r.status_code == 404, r.text


def test_exchange_routing_strict_bool_rejects_int_and_string(exchange_thread):
    """MEM-304 discipline: bool-before-int — ``1``/``0``/``\"false\"`` must
    NOT silently coerce through the strict-bool wire contract."""
    thread_id, client, _org, auth_headers = exchange_thread
    for bad in (1, 0, "false", "true", "1", None):
        r = client.post(
            f"/api/v1/orgs/alpha/threads/{thread_id}/exchange-routing",
            json={"reply_exchange_enabled": bad},
            headers=auth_headers,
        )
        assert r.status_code == 422, f"{bad!r} should be rejected: {r.status_code}"
