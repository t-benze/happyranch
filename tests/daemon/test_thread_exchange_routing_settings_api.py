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


def _open_exchange_via_api(thread_id: str, client, auth_headers) -> None:
    """Open a strict exchange through the API: founder message mentioning one
    participant (P = that agent, D = the other two)."""
    r = client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/send",
        json={"body_markdown": "@alpha please"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text


def _open_exchange_row(org_state, thread_id: str):
    return org_state.db._conn.execute(
        "SELECT * FROM thread_reply_exchange WHERE thread_id = ? "
        "AND state = 'open'",
        (thread_id,),
    ).fetchone()


# ---------------------------------------------------------------------------
# TASK-5982 — disable/re-enable rollback compatibility over an OPEN epoch
# ---------------------------------------------------------------------------


def test_per_thread_disable_via_api_retires_open_epoch(exchange_thread):
    thread_id, client, org_state, auth_headers = exchange_thread
    _open_exchange_via_api(thread_id, client, auth_headers)
    assert _open_exchange_row(org_state, thread_id) is not None

    r = client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/exchange-routing",
        json={"reply_exchange_enabled": False},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["reply_exchange_enabled"] is False
    # The open epoch was terminalized with the disable reason.
    row = org_state.db._conn.execute(
        "SELECT * FROM thread_reply_exchange WHERE thread_id = ?",
        (thread_id,),
    ).fetchone()
    assert row["state"] == "released"
    assert row["close_reason"] == "exchange_disabled"
    # Uncovered deferred pairs got exactly-one slot-checked catch-up
    # (durable queued ownership the route enqueues after commit).
    deferred = org_state.db._conn.execute(
        "SELECT agent_name FROM thread_exchange_deferrals "
        "WHERE thread_id = ? AND exchange_id = ?",
        (thread_id, row["exchange_id"]),
    ).fetchall()
    assert deferred
    for d in deferred:
        pair = org_state.db._conn.execute(
            "SELECT acknowledged_through_seq, required_through_seq, "
            "queued_invocation_token FROM thread_reply_delivery_state "
            "WHERE thread_id = ? AND agent_name = ?",
            (thread_id, d["agent_name"]),
        ).fetchone()
        if pair["acknowledged_through_seq"] < pair["required_through_seq"]:
            assert pair["queued_invocation_token"] is not None
    # mention_routing_enabled untouched (independent control).
    t = org_state.db.get_thread(thread_id)
    assert t.mention_routing_enabled is True
    assert t.reply_exchange_enabled is False


def test_org_exchange_routing_kill_switch_disables_and_retires(exchange_thread):
    thread_id, client, org_state, auth_headers = exchange_thread
    _open_exchange_via_api(thread_id, client, auth_headers)
    # Second thread with its own open epoch.
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={
            "subject": "exchange api 2",
            "recipients": ["alpha", "bravo", "charlie"],
            "body_markdown": "kickoff 2",
        },
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    thread2 = r.json()["thread_id"]
    _open_exchange_via_api(thread2, client, auth_headers)
    assert _open_exchange_row(org_state, thread2) is not None

    r = client.post(
        "/api/v1/orgs/alpha/threads/exchange-routing",
        json={"reply_exchange_enabled": False},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["reply_exchange_enabled"] is False
    for tid in (thread_id, thread2):
        row = org_state.db._conn.execute(
            "SELECT state, close_reason FROM thread_reply_exchange "
            "WHERE thread_id = ?",
            (tid,),
        ).fetchone()
        assert row["state"] == "released"
        assert row["close_reason"] == "org_exchange_disabled"
        assert org_state.db._thread_reply_exchange_enabled(tid) is False
    # Kill-switch persisted under org_settings.threads.
    raw = org_state.db.get_org_setting("threads")
    assert json.loads(raw)["reply_exchange_enabled"] is False

    # Repeated disable: idempotent no-op.
    r = client.post(
        "/api/v1/orgs/alpha/threads/exchange-routing",
        json={"reply_exchange_enabled": False},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json().get("idempotent") is True


def test_org_exchange_routing_reenable_after_disable(exchange_thread):
    thread_id, client, org_state, auth_headers = exchange_thread
    _open_exchange_via_api(thread_id, client, auth_headers)
    r = client.post(
        "/api/v1/orgs/alpha/threads/exchange-routing",
        json={"reply_exchange_enabled": False},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    # Re-enable: no resurrection — only new epochs may open afterwards.
    r = client.post(
        "/api/v1/orgs/alpha/threads/exchange-routing",
        json={"reply_exchange_enabled": True},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["reply_exchange_enabled"] is True
    raw = org_state.db.get_org_setting("threads")
    assert json.loads(raw)["reply_exchange_enabled"] is True
    assert org_state.db._thread_reply_exchange_enabled(thread_id) is True
    # Historical epoch stays terminal.
    row = org_state.db._conn.execute(
        "SELECT state, close_reason FROM thread_reply_exchange "
        "WHERE thread_id = ?",
        (thread_id,),
    ).fetchone()
    assert row["state"] == "released"
    assert row["close_reason"] == "org_exchange_disabled"
    assert _open_exchange_row(org_state, thread_id) is None


def test_org_exchange_routing_strict_bool_rejects_int_and_string(exchange_thread):
    _thread_id, client, _org, auth_headers = exchange_thread
    for bad in (1, 0, "false", "true", "1", None):
        r = client.post(
            "/api/v1/orgs/alpha/threads/exchange-routing",
            json={"reply_exchange_enabled": bad},
            headers=auth_headers,
        )
        assert r.status_code == 422, f"{bad!r} should be rejected: {r.status_code}"
