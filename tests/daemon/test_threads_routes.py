from __future__ import annotations

from fastapi.testclient import TestClient

# We use the existing daemon conftest fixtures: tmp_home, app, org_state, auth_headers.
# Helper to seed an approved agent in the alpha org.


def _seed_agent(org_state, name: str, *, team: str = "engineering") -> None:
    """Create the agent's pending file and workspace dir.

    The compose endpoint validates: `prompt_loader.load_agent(...)` is not None
    AND `<root>/workspaces/<name>` exists.
    """
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


def test_compose_creates_thread_and_invocations(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    _seed_agent(org_state, "qa_engineer")

    resp = client.post(
        "/api/v1/orgs/alpha/threads",
        json={
            "subject": "Refund policy",
            "recipients": ["dev_agent", "qa_engineer"],
            "body_markdown": "should we cap refunds at 30 days?",
            "addressed_to": ["@all"],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["thread_id"].startswith("THR-")
    assert set(data["pending_replies"]) == {"dev_agent", "qa_engineer"}

    invocations = org_state.db.list_thread_invocations(data["thread_id"])
    assert len(invocations) == 2
    assert all(inv.purpose.value == "reply" for inv in invocations)


def test_compose_rejects_unknown_recipient(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    resp = client.post(
        "/api/v1/orgs/alpha/threads",
        json={
            "subject": "x",
            "recipients": ["ghost"],
            "body_markdown": "hi",
            "addressed_to": ["@all"],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "unknown_agent"


def test_compose_rejects_empty_subject(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    resp = client.post(
        "/api/v1/orgs/alpha/threads",
        json={
            "subject": "   ",
            "recipients": ["dev_agent"],
            "body_markdown": "hi",
            "addressed_to": ["@all"],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Task 20 — GET /threads, GET /threads/{id}, GET /threads/{id}/messages
# ---------------------------------------------------------------------------


def test_list_threads_returns_recent(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "a", "recipients": ["dev_agent"], "body_markdown": "x", "addressed_to": ["@all"]},
        headers=auth_headers,
    )
    client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "b", "recipients": ["dev_agent"], "body_markdown": "x", "addressed_to": ["@all"]},
        headers=auth_headers,
    )
    resp = client.get("/api/v1/orgs/alpha/threads", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["threads"]) == 2
    assert data["threads"][0]["subject"] in {"a", "b"}


def test_get_thread_returns_messages_and_participants(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "a", "recipients": ["dev_agent"], "body_markdown": "hi", "addressed_to": ["@all"]},
        headers=auth_headers,
    ).json()
    tid = r["thread_id"]
    resp = client.get(f"/api/v1/orgs/alpha/threads/{tid}", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["thread_id"] == tid
    assert data["participants"] == ["dev_agent"]
    assert data["messages"][0]["body_markdown"] == "hi"


def test_get_thread_missing_returns_404(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    resp = client.get("/api/v1/orgs/alpha/threads/THR-999", headers=auth_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Task 21 — POST /threads/{id}/reply with token validation
# ---------------------------------------------------------------------------


def _start_thread(client, org_state, auth_headers, *, recipient="dev_agent", addressed=None):
    """Helper: seeds the agent and creates a thread, returning (thread_id, invocation_token)."""
    _seed_agent(org_state, recipient)
    addressed = addressed or ["@all"]
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "s", "recipients": [recipient], "body_markdown": "hi", "addressed_to": addressed},
        headers=auth_headers,
    ).json()
    inv = org_state.db.list_thread_invocations(r["thread_id"])[0]
    return r["thread_id"], inv.invocation_token


def test_reply_appends_message_and_consumes_token(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid, token = _start_thread(client, org_state, auth_headers)
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/reply",
        json={
            "thread_id": tid, "invocation_token": token,
            "speaker": "dev_agent", "body_markdown": "hello back",
            "in_response_to_seq": 1,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    msgs = org_state.db.list_thread_messages(tid)
    assert msgs[-1].body_markdown == "hello back"
    assert org_state.db.get_thread(tid).turns_used == 1
    assert org_state.db.get_pending_invocation(token) is None


def test_reply_rejects_missing_token(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid, _token = _start_thread(client, org_state, auth_headers)
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/reply",
        json={"thread_id": tid, "invocation_token": "bogus",
              "speaker": "dev_agent", "body_markdown": "x", "in_response_to_seq": 1},
        headers=auth_headers,
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "invocation_token_invalid"


def test_reply_rejects_consumed_token(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid, token = _start_thread(client, org_state, auth_headers)
    p = {"thread_id": tid, "invocation_token": token,
         "speaker": "dev_agent", "body_markdown": "hi", "in_response_to_seq": 1}
    assert client.post(f"/api/v1/orgs/alpha/threads/{tid}/reply", json=p, headers=auth_headers).status_code == 200
    second = client.post(f"/api/v1/orgs/alpha/threads/{tid}/reply", json=p, headers=auth_headers)
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "invocation_token_consumed"


def test_reply_rejects_mismatched_speaker(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    _seed_agent(org_state, "qa_engineer")
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "s", "recipients": ["dev_agent", "qa_engineer"],
              "body_markdown": "hi", "addressed_to": ["@all"]},
        headers=auth_headers,
    ).json()
    tid = r["thread_id"]
    dev_token = next(
        inv.invocation_token
        for inv in org_state.db.list_thread_invocations(tid)
        if inv.agent_name == "dev_agent"
    )
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/reply",
        json={"thread_id": tid, "invocation_token": dev_token,
              "speaker": "qa_engineer", "body_markdown": "x", "in_response_to_seq": 1},
        headers=auth_headers,
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Task 22 — POST /threads/{id}/decline
# ---------------------------------------------------------------------------


def test_decline_records_decline_and_consumes_token(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid, token = _start_thread(client, org_state, auth_headers)
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/decline",
        json={"thread_id": tid, "invocation_token": token,
              "speaker": "dev_agent", "reason": "nothing to add",
              "in_response_to_seq": 1},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    msgs = org_state.db.list_thread_messages(tid)
    assert msgs[-1].kind.value == "decline"
    assert msgs[-1].decline_reason == "nothing to add"
    assert org_state.db.get_pending_invocation(token) is None


# ---------------------------------------------------------------------------
# Task 23 — POST /threads/{id}/dispatch
# ---------------------------------------------------------------------------


def test_worker_self_dispatch_creates_task_with_thread_link(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid, token = _start_thread(client, org_state, auth_headers, recipient="dev_agent")
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/dispatch",
        json={"thread_id": tid, "invocation_token": token,
              "dispatcher": "dev_agent",
              "brief": "Implement option B"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["dispatched_from_thread_id"] == tid
    assert data["assigned_agent"] == "dev_agent"

    # System message landed.
    msgs = org_state.db.list_thread_messages(tid)
    sys_msg = [m for m in msgs if m.kind.value == "system"][-1]
    assert sys_msg.system_payload["kind_tag"] == "task_dispatched"

    # Token stays pending (dispatch does NOT consume).
    assert org_state.db.get_pending_invocation(token) is not None
    inv = org_state.db.get_invocation_any_status(token)
    assert inv.dispatched_task_id == data["task_id"]


def test_worker_cannot_dispatch_to_other_agent(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    _seed_agent(org_state, "qa_engineer")
    tid, token = _start_thread(client, org_state, auth_headers, recipient="dev_agent")
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/dispatch",
        json={"thread_id": tid, "invocation_token": token,
              "dispatcher": "dev_agent", "target_agent": "qa_engineer",
              "brief": "do x"},
        headers=auth_headers,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "worker_must_self_dispatch"


def test_dispatch_twice_on_same_token_rejected(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid, token = _start_thread(client, org_state, auth_headers, recipient="dev_agent")
    p = {"thread_id": tid, "invocation_token": token,
         "dispatcher": "dev_agent", "brief": "x"}
    assert client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/dispatch", json=p, headers=auth_headers
    ).status_code == 200
    again = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/dispatch", json=p, headers=auth_headers,
    )
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "dispatch_already_used"


# ---------------------------------------------------------------------------
# Task 24 — POST /threads/{id}/send (founder follow-up)
# ---------------------------------------------------------------------------


def test_founder_send_appends_and_enqueues(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    _seed_agent(org_state, "qa_engineer")
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "s", "recipients": ["dev_agent", "qa_engineer"],
              "body_markdown": "hi", "addressed_to": ["dev_agent"]},
        headers=auth_headers,
    ).json()
    tid = r["thread_id"]
    before_invocations = len(org_state.db.list_thread_invocations(tid))
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/send",
        json={"body_markdown": "any thoughts qa_engineer?", "addressed_to": ["qa_engineer"]},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    after_invocations = len(org_state.db.list_thread_invocations(tid))
    assert after_invocations == before_invocations + 1


# ---------------------------------------------------------------------------
# Task 25 — POST /threads/{id}/invite
# ---------------------------------------------------------------------------


def test_invite_adds_participant_and_bootstrap_invocation(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    _seed_agent(org_state, "qa_engineer")
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "s", "recipients": ["dev_agent"],
              "body_markdown": "hi", "addressed_to": ["@all"]},
        headers=auth_headers,
    ).json()
    tid = r["thread_id"]
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/invite",
        json={"agent_name": "qa_engineer"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    parts = [p.agent_name for p in org_state.db.list_thread_participants(tid)]
    assert "qa_engineer" in parts
    msgs = org_state.db.list_thread_messages(tid)
    sys_msgs = [m for m in msgs if m.kind.value == "system"]
    assert sys_msgs[-1].system_payload["kind_tag"] == "participant_added"
    pending = org_state.db.list_thread_invocations(tid)
    assert any(
        inv.agent_name == "qa_engineer" and inv.purpose.value == "bootstrap"
        for inv in pending
    )


def test_invite_already_participant_409(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "s", "recipients": ["dev_agent"],
              "body_markdown": "hi", "addressed_to": ["@all"]},
        headers=auth_headers,
    ).json()
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{r['thread_id']}/invite",
        json={"agent_name": "dev_agent"},
        headers=auth_headers,
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Task 26 — POST /threads/{id}/extend
# ---------------------------------------------------------------------------


def test_extend_increases_turn_cap(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "s", "recipients": ["dev_agent"],
              "body_markdown": "hi", "addressed_to": ["@all"]},
        headers=auth_headers,
    ).json()
    tid = r["thread_id"]
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/extend",
        json={"new_cap": 1000},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert org_state.db.get_thread(tid).turn_cap == 1000


def test_extend_rejects_non_increase(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "s", "recipients": ["dev_agent"],
              "body_markdown": "hi", "addressed_to": ["@all"]},
        headers=auth_headers,
    ).json()
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{r['thread_id']}/extend",
        json={"new_cap": 50},
        headers=auth_headers,
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Task 27 — POST /threads/{id}/abandon
# ---------------------------------------------------------------------------


def test_abandon_reaps_pending_and_writes_no_transcript(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    _seed_agent(org_state, "dev_agent")
    r = client.post(
        "/api/v1/orgs/alpha/threads",
        json={"subject": "s", "recipients": ["dev_agent"],
              "body_markdown": "hi", "addressed_to": ["@all"]},
        headers=auth_headers,
    ).json()
    tid = r["thread_id"]
    assert len(org_state.db.list_thread_invocations(tid)) == 1
    resp = client.post(
        f"/api/v1/orgs/alpha/threads/{tid}/abandon",
        json={"reason": "nothing useful"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    t = org_state.db.get_thread(tid)
    assert t.status.value == "abandoned"
    assert t.transcript_path is None
    from src.models import ThreadInvocationStatus
    pending = org_state.db.list_thread_invocations(tid, status=ThreadInvocationStatus.PENDING)
    assert pending == []
