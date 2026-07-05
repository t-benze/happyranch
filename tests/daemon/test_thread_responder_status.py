"""responder_status field on GET /threads/{id}.

Spec: docs/superpowers/specs/2026-05-30-thread-broadcast-only-design.md §9
"""
from __future__ import annotations

import pytest

from runtime.models import ThreadMessageKind


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
    # pending invocations that haven't spawned a subprocess read as "queued".
    assert all(s["status"] == "queued" for s in statuses)
    assert all(s["responded_at"] is None for s in statuses)
    assert all(s["started_at"] is None for s in statuses)


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


def test_started_invocation_reads_as_working_on_messages_endpoint(
    client, org_slug, three_agent_thread, db,
):
    """A pending invocation with started_at set reads as 'working', and the
    /messages endpoint (the strip's primary source) carries responder_status."""
    thread_id = three_agent_thread
    row = db._conn.execute(
        "SELECT invocation_token FROM thread_invocations "
        "WHERE thread_id = ? AND agent_name = 'alpha' LIMIT 1",
        (thread_id,),
    ).fetchone()
    db.stamp_invocation_started(row["invocation_token"], session_id=None)

    r = client.get(f"/api/v1/orgs/{org_slug}/threads/{thread_id}/messages")
    assert r.status_code == 200, r.text
    kickoff = r.json()["messages"][0]
    statuses = {s["agent_name"]: s for s in kickoff["responder_status"]}
    assert statuses["alpha"]["status"] == "working"
    assert statuses["alpha"]["started_at"] is not None
    assert statuses["bravo"]["status"] == "queued"


def test_messages_endpoint_has_responder_parity_with_detail(
    client, org_slug, three_agent_thread,
):
    """Regression: /messages must include responder_status, not []."""
    thread_id = three_agent_thread
    r = client.get(f"/api/v1/orgs/{org_slug}/threads/{thread_id}/messages")
    kickoff = r.json()["messages"][0]
    assert kickoff["kind"] == "message"
    assert len(kickoff["responder_status"]) == 3


# ---------------------------------------------------------------------------
# TASK-966 (THR-038): a task-followup / escalation-followup re-invocation hangs
# off a SYSTEM row (task_completed / task_failed / task_escalated), not a
# MESSAGE row. The TypingBubble must surface for the woken agent. This requires
# (A) the grouped query to return purpose='task_followup' invocations, and
# (B) the GET endpoints to NOT null responders on non-MESSAGE rows.
# ---------------------------------------------------------------------------


def _post_followup_system_row(db, thread_id: str, *, agent: str, kind_tag: str) -> str:
    """Append a SYSTEM row (kind_tag) and mint a pending TASK_FOLLOWUP invocation
    hanging off its seq — the exact shape run_step._append_followup_system_and_reinvoke
    produces when an agent is woken by a completion/escalation followup. Returns
    the minted invocation_token."""
    sys_seq = db.append_thread_message(
        thread_id=thread_id,
        speaker=agent,
        kind=ThreadMessageKind.SYSTEM,
        system_payload={"kind_tag": kind_tag, "status": "completed"},
    )
    inv, _ = db.mint_followup_invocation_with_cap_extend(
        thread_id, agent_name=agent, triggering_seq=sys_seq,
    )
    return inv.invocation_token


def test_followup_system_row_surfaces_working_responder_on_detail(
    client, org_slug, three_agent_thread, db,
):
    """A task_completed SYSTEM row carrying a pending+started TASK_FOLLOWUP
    invocation surfaces a `working` responder on GET /threads/{id}."""
    thread_id = three_agent_thread
    token = _post_followup_system_row(db, thread_id, agent="alpha", kind_tag="task_completed")
    db.stamp_invocation_started(token, session_id=None)

    r = client.get(f"/api/v1/orgs/{org_slug}/threads/{thread_id}")
    assert r.status_code == 200, r.text
    sys_msg = next(m for m in r.json()["messages"] if m["kind"] == "system")
    statuses = {s["agent_name"]: s for s in sys_msg["responder_status"]}
    assert statuses["alpha"]["status"] == "working"
    assert statuses["alpha"]["started_at"] is not None


def test_followup_system_row_surfaces_queued_responder_on_messages(
    client, org_slug, three_agent_thread, db,
):
    """A task_completed SYSTEM row carrying a pending (no started_at)
    TASK_FOLLOWUP invocation surfaces a `queued` responder on
    GET /threads/{id}/messages (the strip's primary source)."""
    thread_id = three_agent_thread
    _post_followup_system_row(db, thread_id, agent="alpha", kind_tag="task_completed")

    r = client.get(f"/api/v1/orgs/{org_slug}/threads/{thread_id}/messages")
    assert r.status_code == 200, r.text
    sys_msg = next(m for m in r.json()["messages"] if m["kind"] == "system")
    statuses = {s["agent_name"]: s for s in sys_msg["responder_status"]}
    assert statuses["alpha"]["status"] == "queued"
    assert statuses["alpha"]["started_at"] is None


def test_escalation_followup_system_row_surfaces_working_responder(
    client, org_slug, three_agent_thread, db,
):
    """Escalation followup reuses purpose=TASK_FOLLOWUP off a task_escalated
    SYSTEM row (run_step._maybe_post_thread_escalation) — the same widening must
    surface its in-flight responder."""
    thread_id = three_agent_thread
    token = _post_followup_system_row(db, thread_id, agent="alpha", kind_tag="task_escalated")
    db.stamp_invocation_started(token, session_id=None)

    r = client.get(f"/api/v1/orgs/{org_slug}/threads/{thread_id}")
    sys_msg = next(m for m in r.json()["messages"] if m["kind"] == "system")
    statuses = {s["agent_name"]: s for s in sys_msg["responder_status"]}
    assert statuses["alpha"]["status"] == "working"


# ---------------------------------------------------------------------------
# THR-071 slice (1) — decline_reason + category exposure on responder_status
# ---------------------------------------------------------------------------


def test_decline_reason_and_category_on_failed_invocation(
    client, org_slug, three_agent_thread, db,
):
    """A failed invocation (no_callback) surfaces decline_reason and
    category='no_callback' on the responder_status entry."""
    thread_id = three_agent_thread
    # Directly fail alpha's pending invocation with no_callback reason.
    db._conn.execute(
        "UPDATE thread_invocations SET status='failed', "
        "consumed_at=datetime('now'), "
        "decline_reason='no_callback: rc=0' "
        "WHERE thread_id=? AND agent_name='alpha' AND status='pending'",
        (thread_id,),
    )
    db._conn.commit()

    r = client.get(f"/api/v1/orgs/{org_slug}/threads/{thread_id}")
    assert r.status_code == 200, r.text
    kickoff = r.json()["messages"][0]
    alpha_entry = next(
        s for s in kickoff["responder_status"] if s["agent_name"] == "alpha"
    )
    assert alpha_entry["status"] == "failed"
    assert alpha_entry["decline_reason"] == "no_callback: rc=0"
    assert alpha_entry["category"] == "no_callback"


def test_decline_reason_and_category_on_declined_invocation(
    client, org_slug, three_agent_thread, db,
):
    """An explicitly declined invocation surfaces decline_reason and
    category='declined' on the responder_status entry."""
    thread_id = three_agent_thread
    alpha_inv = db._conn.execute(
        "SELECT invocation_token FROM thread_invocations "
        "WHERE thread_id=? AND agent_name='alpha' AND status='pending'",
        (thread_id,),
    ).fetchone()
    client.post(
        f"/api/v1/orgs/{org_slug}/threads/{thread_id}/decline",
        json={
            "thread_id": thread_id,
            "invocation_token": alpha_inv["invocation_token"],
            "speaker": "alpha",
            "reason": "no material to add",
            "in_response_to_seq": 1,
        },
    )

    r = client.get(f"/api/v1/orgs/{org_slug}/threads/{thread_id}")
    kickoff = r.json()["messages"][0]
    alpha_entry = next(
        s for s in kickoff["responder_status"] if s["agent_name"] == "alpha"
    )
    assert alpha_entry["status"] == "declined"
    assert alpha_entry["decline_reason"] == "no material to add"
    assert alpha_entry["category"] == "declined"


def test_decline_reason_and_category_on_no_callback_after_reprompt(
    client, org_slug, three_agent_thread, db,
):
    """A failed invocation after a nudge (no_callback_after_reprompt)
    surfaces category='no_callback_after_reprompt'."""
    thread_id = three_agent_thread
    db._conn.execute(
        "UPDATE thread_invocations SET status='failed', "
        "consumed_at=datetime('now'), "
        "decline_reason='no_callback_after_reprompt: rc=0' "
        "WHERE thread_id=? AND agent_name='alpha' AND status='pending'",
        (thread_id,),
    )
    db._conn.commit()

    r = client.get(f"/api/v1/orgs/{org_slug}/threads/{thread_id}")
    kickoff = r.json()["messages"][0]
    alpha_entry = next(
        s for s in kickoff["responder_status"] if s["agent_name"] == "alpha"
    )
    assert alpha_entry["status"] == "failed"
    assert alpha_entry["category"] == "no_callback_after_reprompt"


def test_decline_reason_and_category_on_infra_failure(
    client, org_slug, three_agent_thread, db,
):
    """An infrastructure failure (runner_crash / timeout / 529)
    surfaces category='infra_fail'."""
    thread_id = three_agent_thread
    db._conn.execute(
        "UPDATE thread_invocations SET status='failed', "
        "consumed_at=datetime('now'), "
        "decline_reason='runner_crash: something broke' "
        "WHERE thread_id=? AND agent_name='alpha' AND status='pending'",
        (thread_id,),
    )
    db._conn.commit()

    r = client.get(f"/api/v1/orgs/{org_slug}/threads/{thread_id}")
    kickoff = r.json()["messages"][0]
    alpha_entry = next(
        s for s in kickoff["responder_status"] if s["agent_name"] == "alpha"
    )
    assert alpha_entry["status"] == "failed"
    assert alpha_entry["category"] == "infra_fail"


def test_queued_invocation_has_null_category(
    client, org_slug, three_agent_thread,
):
    """A queued/pending invocation has decline_reason=None and
    category=None — nothing terminal yet."""
    thread_id = three_agent_thread
    r = client.get(f"/api/v1/orgs/{org_slug}/threads/{thread_id}")
    kickoff = r.json()["messages"][0]
    alpha_entry = next(
        s for s in kickoff["responder_status"] if s["agent_name"] == "alpha"
    )
    assert alpha_entry["status"] == "queued"
    assert alpha_entry["decline_reason"] is None
    assert alpha_entry["category"] is None


def test_replied_invocation_has_null_decline_category(
    client, org_slug, three_agent_thread, db,
):
    """A successfully replied invocation has no decline_reason or
    failure category — regression guard."""
    thread_id = three_agent_thread
    alpha_inv = db._conn.execute(
        "SELECT invocation_token FROM thread_invocations "
        "WHERE thread_id=? AND agent_name='alpha' AND status='pending'",
        (thread_id,),
    ).fetchone()
    client.post(
        f"/api/v1/orgs/{org_slug}/threads/{thread_id}/reply",
        json={
            "thread_id": thread_id,
            "invocation_token": alpha_inv["invocation_token"],
            "speaker": "alpha",
            "body_markdown": "alpha responding",
            "in_response_to_seq": 1,
        },
    )

    r = client.get(f"/api/v1/orgs/{org_slug}/threads/{thread_id}")
    kickoff = r.json()["messages"][0]
    alpha_entry = next(
        s for s in kickoff["responder_status"] if s["agent_name"] == "alpha"
    )
    assert alpha_entry["status"] == "replied"
    assert alpha_entry["decline_reason"] is None
    assert alpha_entry["category"] is None


def test_settled_followup_serializes_terminal_not_inflight(
    client, org_slug, three_agent_thread, db,
):
    """A SETTLED followup (DB status=consumed) maps to a terminal wire status
    (`replied`), NOT working/queued — so the web in-flight set is empty and the
    bubble clears. Confirms clears-correctly end-to-end at the serialization
    boundary on both endpoints."""
    thread_id = three_agent_thread
    token = _post_followup_system_row(db, thread_id, agent="alpha", kind_tag="task_completed")
    db.stamp_invocation_started(token, session_id=None)
    db._conn.execute(
        "UPDATE thread_invocations SET status='consumed', "
        "consumed_at=datetime('now') WHERE invocation_token=?",
        (token,),
    )
    db._conn.commit()

    for path in (f"/threads/{thread_id}", f"/threads/{thread_id}/messages"):
        r = client.get(f"/api/v1/orgs/{org_slug}{path}")
        sys_msg = next(m for m in r.json()["messages"] if m["kind"] == "system")
        alpha = next(s for s in sys_msg["responder_status"] if s["agent_name"] == "alpha")
        assert alpha["status"] == "replied"
        # Mirrors web selectInFlightResponders: only working/queued are in-flight.
        in_flight = [s for s in sys_msg["responder_status"]
                     if s["status"] in ("working", "queued")]
        assert in_flight == []
