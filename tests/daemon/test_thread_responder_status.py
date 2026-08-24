"""responder_status field on GET /threads/{id}.

Spec: docs/superpowers/specs/2026-05-30-thread-broadcast-only-design.md §9
"""
from __future__ import annotations

import pytest

from runtime.models import ThreadInvocationPurpose, ThreadMessageKind


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


# ---------------------------------------------------------------------------
# THR-071 HIGH-1 REVISE: no_callback with infra signatures → infra_fail
# ---------------------------------------------------------------------------


def test_no_callback_with_nonzero_rc_is_infra_fail(
    client, org_slug, three_agent_thread, db,
):
    """no_callback: rc=1 → infra_fail, not no_callback."""
    thread_id = three_agent_thread
    db._conn.execute(
        "UPDATE thread_invocations SET status='failed', "
        "consumed_at=datetime('now'), "
        "decline_reason='no_callback: rc=1 — API Error: 529 Overloaded' "
        "WHERE thread_id=? AND agent_name='alpha' AND status='pending'",
        (thread_id,),
    )
    db._conn.commit()

    r = client.get(f"/api/v1/orgs/{org_slug}/threads/{thread_id}")
    kickoff = r.json()["messages"][0]
    alpha_entry = next(
        s for s in kickoff["responder_status"] if s["agent_name"] == "alpha"
    )
    assert alpha_entry["category"] == "infra_fail", (
        f"expected infra_fail, got {alpha_entry['category']}"
    )


def test_no_callback_with_529_overloaded_is_infra_fail(
    client, org_slug, three_agent_thread, db,
):
    """no_callback with 529/Overloaded → infra_fail."""
    thread_id = three_agent_thread
    db._conn.execute(
        "UPDATE thread_invocations SET status='failed', "
        "consumed_at=datetime('now'), "
        "decline_reason='no_callback: rc=1 — API Error: 529 Overloaded' "
        "WHERE thread_id=? AND agent_name='alpha' AND status='pending'",
        (thread_id,),
    )
    db._conn.commit()

    r = client.get(f"/api/v1/orgs/{org_slug}/threads/{thread_id}")
    kickoff = r.json()["messages"][0]
    alpha_entry = next(
        s for s in kickoff["responder_status"] if s["agent_name"] == "alpha"
    )
    assert alpha_entry["category"] == "infra_fail"


def test_no_callback_with_rc143_is_infra_fail(
    client, org_slug, three_agent_thread, db,
):
    """no_callback: rc=143 → infra_fail."""
    thread_id = three_agent_thread
    db._conn.execute(
        "UPDATE thread_invocations SET status='failed', "
        "consumed_at=datetime('now'), "
        "decline_reason='no_callback: rc=143' "
        "WHERE thread_id=? AND agent_name='alpha' AND status='pending'",
        (thread_id,),
    )
    db._conn.commit()

    r = client.get(f"/api/v1/orgs/{org_slug}/threads/{thread_id}")
    kickoff = r.json()["messages"][0]
    alpha_entry = next(
        s for s in kickoff["responder_status"] if s["agent_name"] == "alpha"
    )
    assert alpha_entry["category"] == "infra_fail"


def test_no_callback_with_quota_is_infra_fail(
    client, org_slug, three_agent_thread, db,
):
    """no_callback with quota/usage-limit → infra_fail."""
    thread_id = three_agent_thread
    db._conn.execute(
        "UPDATE thread_invocations SET status='failed', "
        "consumed_at=datetime('now'), "
        "decline_reason='no_callback: rc=1 — usage limit exceeded' "
        "WHERE thread_id=? AND agent_name='alpha' AND status='pending'",
        (thread_id,),
    )
    db._conn.commit()

    r = client.get(f"/api/v1/orgs/{org_slug}/threads/{thread_id}")
    kickoff = r.json()["messages"][0]
    alpha_entry = next(
        s for s in kickoff["responder_status"] if s["agent_name"] == "alpha"
    )
    assert alpha_entry["category"] == "infra_fail"


def test_no_callback_with_unknown_session_is_infra_fail(
    client, org_slug, three_agent_thread, db,
):
    """no_callback with unknown_session → infra_fail."""
    thread_id = three_agent_thread
    db._conn.execute(
        "UPDATE thread_invocations SET status='failed', "
        "consumed_at=datetime('now'), "
        "decline_reason='no_callback: rc=0 — unknown_session: session not found' "
        "WHERE thread_id=? AND agent_name='alpha' AND status='pending'",
        (thread_id,),
    )
    db._conn.commit()

    r = client.get(f"/api/v1/orgs/{org_slug}/threads/{thread_id}")
    kickoff = r.json()["messages"][0]
    alpha_entry = next(
        s for s in kickoff["responder_status"] if s["agent_name"] == "alpha"
    )
    assert alpha_entry["category"] == "infra_fail"


def test_no_callback_clean_forget_stays_no_callback(
    client, org_slug, three_agent_thread, db,
):
    """no_callback: rc=0 with no infra markers stays no_callback."""
    thread_id = three_agent_thread
    db._conn.execute(
        "UPDATE thread_invocations SET status='failed', "
        "consumed_at=datetime('now'), "
        "decline_reason='no_callback: rc=0' "
        "WHERE thread_id=? AND agent_name='alpha' AND status='pending'",
        (thread_id,),
    )
    db._conn.commit()

    r = client.get(f"/api/v1/orgs/{org_slug}/threads/{thread_id}")
    kickoff = r.json()["messages"][0]
    alpha_entry = next(
        s for s in kickoff["responder_status"] if s["agent_name"] == "alpha"
    )
    assert alpha_entry["category"] == "no_callback"


def test_no_callback_after_reprompt_with_infra_is_infra_fail(
    client, org_slug, three_agent_thread, db,
):
    """no_callback_after_reprompt with rc=143 → infra_fail."""
    thread_id = three_agent_thread
    db._conn.execute(
        "UPDATE thread_invocations SET status='failed', "
        "consumed_at=datetime('now'), "
        "decline_reason='no_callback_after_reprompt: rc=143' "
        "WHERE thread_id=? AND agent_name='alpha' AND status='pending'",
        (thread_id,),
    )
    db._conn.commit()

    r = client.get(f"/api/v1/orgs/{org_slug}/threads/{thread_id}")
    kickoff = r.json()["messages"][0]
    alpha_entry = next(
        s for s in kickoff["responder_status"] if s["agent_name"] == "alpha"
    )
    assert alpha_entry["category"] == "infra_fail"


# ---------------------------------------------------------------------------
# TASK-5553: authoritative purpose on the wire — classification/dedup must
# use thread_invocations.purpose, never the triggering row kind.
# ---------------------------------------------------------------------------


def test_responder_status_carries_purpose_on_both_endpoints(
    client, org_slug, three_agent_thread, db,
):
    """Every responder_status entry carries the authoritative invocation
    purpose: 'reply' for message-row REPLY wakes, 'task_followup' for the
    system-row followup wake — on BOTH GET /threads/{id} and /messages.
    The same agent can appear with DIFFERENT purposes on different rows; the
    wire must never blur them."""
    thread_id = three_agent_thread
    token = _post_followup_system_row(db, thread_id, agent="alpha", kind_tag="task_completed")
    db.stamp_invocation_started(token, session_id=None)

    for path in (f"/threads/{thread_id}", f"/threads/{thread_id}/messages"):
        r = client.get(f"/api/v1/orgs/{org_slug}{path}")
        assert r.status_code == 200, r.text
        msgs = r.json()["messages"]
        kickoff = next(m for m in msgs if m["kind"] == "message")
        sys_msg = next(m for m in msgs if m["kind"] == "system")
        # Kickoff REPLY wakes are all purpose='reply'.
        assert {s["purpose"] for s in kickoff["responder_status"]} == {"reply"}
        # Alpha's followup wake on the system row is purpose='task_followup'.
        alpha_followup = next(
            s for s in sys_msg["responder_status"] if s["agent_name"] == "alpha"
        )
        assert alpha_followup["purpose"] == "task_followup"
        assert alpha_followup["status"] == "working"


def test_system_row_anchored_reply_range_carries_purpose_reply(
    client, org_slug, three_agent_thread, db,
):
    """GH-688 duplicate-responder regression (founder THR-198 seq 77): a
    coalesced conversational REPLY whose delivery range STARTS on a SYSTEM row
    still carries purpose='reply' on the wire. The follow-on REPLY mint keys
    the first unacknowledged sequence, which can be a system divider (system
    seq 39 + founder message seq 40 + REPLY running range 39-40). The web
    selector must classify it as a REPLY — owned by the pair projection —
    never infer a special purpose from the triggering row kind."""
    thread_id = three_agent_thread
    # Claim alpha's kickoff REPLY (running range 1..1).
    alpha_inv = next(
        i for i in db.list_thread_invocations(thread_id)
        if i.agent_name == "alpha" and i.purpose.value == "reply"
    )
    claim = db.claim_conversational_reply(alpha_inv.invocation_token)
    assert claim is not None and claim.running_through_seq == 1
    # A SYSTEM row lands at seq 2 (resumed divider — no arrivals).
    sys_seq = db.append_thread_message(
        thread_id=thread_id, speaker="founder",
        kind=ThreadMessageKind.SYSTEM,
        system_payload={"kind_tag": "resumed", "status": "ok"},
    )
    # Founder message at seq 3 coalesces into alpha's running wake.
    seq, _ = db.record_conversational_arrival(
        thread_id=thread_id, speaker="founder", kind=ThreadMessageKind.MESSAGE,
        body_markdown="any thoughts?",
        recipients=["alpha", "bravo", "charlie"],
    )
    assert seq == sys_seq + 1
    # Settle the claimed range (1..1): required(3) > ack(1) → exactly one
    # follow-on REPLY whose triggering_seq is the first unacknowledged seq —
    # 2, the SYSTEM row. This is the founder's exact edge.
    settlement = db.settle_conversational_reply(
        token=alpha_inv.invocation_token, outcome="reply",
    )
    assert settlement is not None and settlement.follow_on_token is not None
    follow_on = db.get_invocation_any_status(settlement.follow_on_token)
    assert follow_on.triggering_seq == sys_seq
    assert follow_on.purpose is ThreadInvocationPurpose.REPLY
    # The runner claims the follow-on (queued→running CAS) as it would.
    follow_claim = db.claim_conversational_reply(settlement.follow_on_token)
    assert follow_claim is not None
    assert follow_claim.running_from_seq == sys_seq
    assert follow_claim.running_through_seq == seq

    for path in (f"/threads/{thread_id}", f"/threads/{thread_id}/messages"):
        r = client.get(f"/api/v1/orgs/{org_slug}{path}")
        assert r.status_code == 200, r.text
        data = r.json()
        sys_msg = next(m for m in data["messages"] if m["seq"] == sys_seq)
        alpha_entry = next(
            s for s in sys_msg["responder_status"] if s["agent_name"] == "alpha"
        )
        # The system-row responder is a REPLY, not a special wake: purpose is
        # authoritative, and the status shows an in-flight working reply.
        assert alpha_entry["purpose"] == "reply"
        assert alpha_entry["status"] == "working"
        # The pair projection owns the range starting at the system row — the
        # web layer uses (purpose='reply' + pair ownership) to render exactly
        # one replying row.
        rd = {p["agent_name"]: p for p in data["reply_delivery"]}
        assert rd["alpha"]["state"] == "running"
        assert rd["alpha"]["from_seq"] == sys_seq
        assert rd["alpha"]["through_seq"] == seq


def test_system_row_anchored_reply_terminal_replied_marker(
    client, org_slug, three_agent_thread, db,
):
    """A system-row-anchored REPLY that settles reads 'replied' on its system
    row (per-message terminal marker restored for system-row ranges) with the
    authoritative purpose still intact."""
    thread_id = three_agent_thread
    alpha_inv = next(
        i for i in db.list_thread_invocations(thread_id)
        if i.agent_name == "alpha" and i.purpose.value == "reply"
    )
    sys_seq = db.append_thread_message(
        thread_id=thread_id, speaker="founder",
        kind=ThreadMessageKind.SYSTEM,
        system_payload={"kind_tag": "resumed", "status": "ok"},
    )
    seq, _ = db.record_conversational_arrival(
        thread_id=thread_id, speaker="founder", kind=ThreadMessageKind.MESSAGE,
        body_markdown="any thoughts?",
        recipients=["alpha", "bravo", "charlie"],
    )
    assert seq == sys_seq + 1
    # Directly anchor a fresh REPLY on the system row (as the store's follow-on
    # mint does) and settle it as replied.
    anchored = db.mint_thread_invocation(
        thread_id=thread_id, agent_name="alpha",
        triggering_seq=sys_seq, purpose=ThreadInvocationPurpose.REPLY,
    )
    db._conn.execute(
        "UPDATE thread_invocations SET status='consumed', consumed_at=datetime('now') "
        "WHERE invocation_token=?",
        (anchored.invocation_token,),
    )
    db._conn.commit()

    r = client.get(f"/api/v1/orgs/{org_slug}/threads/{thread_id}")
    sys_msg = next(m for m in r.json()["messages"] if m["seq"] == sys_seq)
    alpha_entry = next(
        s for s in sys_msg["responder_status"] if s["agent_name"] == "alpha"
    )
    assert alpha_entry["purpose"] == "reply"
    assert alpha_entry["status"] == "replied"
    assert alpha_entry["responded_at"] is not None
