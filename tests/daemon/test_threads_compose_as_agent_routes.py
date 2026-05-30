"""Route tests for POST /threads/compose-as-agent (agent-initiated threads)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from src.models import TalkRecord, TalkStatus, TaskRecord, TaskStatus


def _seed_agent(org_state, name: str, *, team: str = "engineering") -> None:
    """Create the agent's frontmatter file and workspace dir."""
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


def test_compose_as_agent_route_rejects_empty_subject(tmp_home, app, org_state, auth_headers):
    _seed_agent(org_state, "engineering_head")
    _seed_agent(org_state, "payment_agt")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head",
            "subject": "",
            "recipients": ["payment_agt"],
            "body_markdown": "hi",
            "task_id": "TASK-1", "session_id": "abc",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "empty_subject"


def test_compose_as_agent_route_rejects_empty_body(tmp_home, app, org_state, auth_headers):
    _seed_agent(org_state, "engineering_head")
    _seed_agent(org_state, "payment_agt")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head",
            "subject": "s",
            "recipients": ["payment_agt"],
            "body_markdown": "   ",
            "task_id": "TASK-1", "session_id": "abc",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "empty_body"


def test_compose_as_agent_route_rejects_empty_recipients(tmp_home, app, org_state, auth_headers):
    _seed_agent(org_state, "engineering_head")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head",
            "subject": "s",
            "recipients": [],
            "body_markdown": "hi",
            "task_id": "TASK-1", "session_id": "abc",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "empty_recipients"


def test_compose_as_agent_rejects_missing_binding(tmp_home, app, org_state, auth_headers):
    _seed_agent(org_state, "engineering_head")
    _seed_agent(org_state, "payment_agt")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head",
            "subject": "s",
            "recipients": ["payment_agt"],
            "body_markdown": "b",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "binding_required"


def test_compose_as_agent_rejects_dual_binding(tmp_home, app, org_state, auth_headers):
    _seed_agent(org_state, "engineering_head")
    _seed_agent(org_state, "payment_agt")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head",
            "subject": "s",
            "recipients": ["payment_agt"],
            "body_markdown": "b",
            "task_id": "TASK-1", "session_id": "abc",
            "talk_id": "TALK-1",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "binding_ambiguous"


def test_compose_as_agent_rejects_unknown_composer(tmp_home, app, org_state, auth_headers):
    _seed_agent(org_state, "payment_agt")  # composer "nobody" is NOT seeded
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "nobody",
            "subject": "s",
            "recipients": ["payment_agt"],
            "body_markdown": "b",
            "task_id": "TASK-1", "session_id": "abc",
        },
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "unknown_composer"


def test_compose_as_agent_task_binding_missing_session_id(tmp_home, app, org_state, auth_headers):
    """task_id without session_id is invalid."""
    _seed_agent(org_state, "engineering_head")
    _seed_agent(org_state, "payment_agt")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head",
            "subject": "s",
            "recipients": ["payment_agt"],
            "body_markdown": "b",
            "task_id": "TASK-1",
            # session_id intentionally omitted
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "binding_required"


def test_compose_as_agent_task_path_rejects_unowned_task(tmp_home, app, org_state, auth_headers):
    _seed_agent(org_state, "engineering_head")
    _seed_agent(org_state, "payment_agt")
    # Seed a task assigned to payment_agt, but composer claims engineering_head.
    org_state.db.insert_task(TaskRecord(
        id="TASK-50", brief="x", team="engineering", assigned_agent="payment_agt",
    ))
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head", "subject": "s",
            "recipients": ["payment_agt"], "body_markdown": "b",
            "task_id": "TASK-50", "session_id": "abc",
        },
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "composer_not_task_owner"


def test_compose_as_agent_task_path_rejects_unknown_task(tmp_home, app, org_state, auth_headers):
    _seed_agent(org_state, "engineering_head")
    _seed_agent(org_state, "payment_agt")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head", "subject": "s",
            "recipients": ["payment_agt"], "body_markdown": "b",
            "task_id": "TASK-9999", "session_id": "abc",
        },
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "unknown_task"


def test_compose_as_agent_task_path_rejects_session_mismatch(tmp_home, app, org_state, auth_headers, daemon_state):
    _seed_agent(org_state, "engineering_head")
    _seed_agent(org_state, "payment_agt")
    org_state.db.insert_task(TaskRecord(
        id="TASK-51", brief="x", team="engineering", assigned_agent="engineering_head",
    ))
    daemon_state.orgs["alpha"].sessions.set_active("TASK-51", "engineering_head", "real-session")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head", "subject": "s",
            "recipients": ["payment_agt"], "body_markdown": "b",
            "task_id": "TASK-51", "session_id": "wrong",
        },
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "session_mismatch"


def test_compose_as_agent_talk_path_rejects_unknown_talk(tmp_home, app, org_state, auth_headers):
    _seed_agent(org_state, "engineering_head")
    _seed_agent(org_state, "payment_agt")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head", "subject": "s",
            "recipients": ["payment_agt"], "body_markdown": "b",
            "talk_id": "TALK-9999",
        },
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "unknown_talk"


def test_compose_as_agent_talk_path_rejects_closed_talk(tmp_home, app, org_state, auth_headers):
    _seed_agent(org_state, "engineering_head")
    _seed_agent(org_state, "payment_agt")
    org_state.db.insert_talk(TalkRecord(
        id="TALK-9", agent_name="engineering_head", status=TalkStatus.CLOSED,
    ))
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head", "subject": "s",
            "recipients": ["payment_agt"], "body_markdown": "b",
            "talk_id": "TALK-9",
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "talk_not_open"


def test_compose_as_agent_talk_path_rejects_unowned_talk(tmp_home, app, org_state, auth_headers):
    _seed_agent(org_state, "engineering_head")
    _seed_agent(org_state, "payment_agt")
    org_state.db.insert_talk(TalkRecord(
        id="TALK-10", agent_name="payment_agt", status=TalkStatus.OPEN,
    ))
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head", "subject": "s",
            "recipients": ["payment_agt"], "body_markdown": "b",
            "talk_id": "TALK-10",
        },
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "composer_not_talk_owner"


def test_compose_as_agent_task_path_rejects_completed_task(
    tmp_home, app, org_state, auth_headers,
):
    """Task already in a terminal state (completed/failed) is rejected as task_not_active.

    In production a completed task has no active session entry — the status
    gate must run BEFORE the session gate so the caller sees the accurate
    `task_not_active` reason rather than a misleading `session_mismatch`.
    """
    _seed_agent(org_state, "engineering_head")
    _seed_agent(org_state, "payment_agt")
    org_state.db.insert_task(TaskRecord(
        id="TASK-60", brief="x", team="engineering",
        assigned_agent="engineering_head", status=TaskStatus.COMPLETED,
    ))
    # No session_id pre-seeded — mirrors what happens after the original
    # session completes in production.
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head", "subject": "s",
            "recipients": ["payment_agt"], "body_markdown": "b",
            "task_id": "TASK-60", "session_id": "stale-sid",
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "task_not_active"
    assert r.json()["detail"]["status"] == "completed"


def _seed_active_task(
    org_state, daemon_state, agent: str,
    task_id: str = "TASK-200", sid: str = "sid-1",
) -> tuple[str, str]:
    org_state.db.insert_task(TaskRecord(
        id=task_id, brief="x", team="engineering", assigned_agent=agent,
    ))
    daemon_state.orgs["alpha"].sessions.set_active(task_id, agent, sid)
    return task_id, sid


def test_compose_as_agent_rejects_self_only(tmp_home, app, org_state, auth_headers, daemon_state):
    _seed_agent(org_state, "engineering_head")
    task_id, sid = _seed_active_task(org_state, daemon_state, "engineering_head")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head", "subject": "s",
            "recipients": ["engineering_head"], "body_markdown": "b",
            "task_id": task_id, "session_id": sid,
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "empty_external_recipients"


def test_compose_as_agent_rejects_unknown_recipient(tmp_home, app, org_state, auth_headers, daemon_state):
    _seed_agent(org_state, "engineering_head")
    task_id, sid = _seed_active_task(org_state, daemon_state, "engineering_head")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head", "subject": "s",
            "recipients": ["who_is_this"], "body_markdown": "b",
            "task_id": task_id, "session_id": sid,
        },
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "unknown_agent"


def test_compose_as_agent_accepts_at_founder_literal(tmp_home, app, org_state, auth_headers, daemon_state):
    """@founder is a permitted recipient — skips the agent existence check."""
    _seed_agent(org_state, "engineering_head")
    task_id, sid = _seed_active_task(org_state, daemon_state, "engineering_head")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head", "subject": "s",
            "recipients": ["@founder"], "body_markdown": "b",
            "task_id": task_id, "session_id": sid,
        },
    )
    assert r.status_code != 404, r.text
    assert r.status_code == 200


def test_compose_as_agent_at_all_expands_to_include_founder(
    tmp_home, app, org_state, auth_headers, daemon_state,
):
    """recipients=[composer, @founder] with addressed_to=[@all] must accept —
    @all expansion covers @founder, so external-recipients rule passes via
    the founder-addressed leg even though `external` only has @founder."""
    _seed_agent(org_state, "engineering_head")
    task_id, sid = _seed_active_task(org_state, daemon_state, "engineering_head")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head", "subject": "s",
            "recipients": ["engineering_head", "@founder"], "body_markdown": "b",
            "task_id": task_id, "session_id": sid,
        },
    )
    assert r.status_code == 200, r.text


def test_compose_as_agent_happy_path_returns_thread(
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
            "recipients": ["payment_agt"], "body_markdown": "hi",
            "task_id": task_id, "session_id": sid,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["thread_id"].startswith("THR-")
    assert body["composed_by"] == "engineering_head"
    assert body["composed_from_task_id"] == task_id
    assert body["composed_from_talk_id"] is None
    assert body["pending_replies"] == ["payment_agt"]


def test_compose_as_agent_adds_composer_as_participant(
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
            "recipients": ["payment_agt"], "body_markdown": "hi",
            "task_id": task_id, "session_id": sid,
        },
    )
    assert r.status_code == 200, r.text
    thread_id = r.json()["thread_id"]
    parts = {p.agent_name for p in org_state.db.list_thread_participants(thread_id)}
    assert parts == {"engineering_head", "payment_agt"}


def test_compose_as_agent_at_all_excludes_composer_and_founder_from_invocations(
    tmp_home, app, org_state, auth_headers, daemon_state,
):
    """addressed_to=@all expands to all participants, but invocations are
    minted only for concrete OTHER agents — not composer, not @founder."""
    _seed_agent(org_state, "engineering_head")
    _seed_agent(org_state, "payment_agt")
    task_id, sid = _seed_active_task(org_state, daemon_state, "engineering_head")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head", "subject": "all",
            "recipients": ["payment_agt", "@founder"], "body_markdown": "hi",
            "task_id": task_id, "session_id": sid,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pending_replies"] == ["payment_agt"]


def test_compose_as_agent_audit_records_composer(
    tmp_home, app, org_state, auth_headers, daemon_state,
):
    """thread_started audit row includes composer attribution."""
    import json as _json
    _seed_agent(org_state, "engineering_head")
    _seed_agent(org_state, "payment_agt")
    task_id, sid = _seed_active_task(org_state, daemon_state, "engineering_head")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head", "subject": "x",
            "recipients": ["payment_agt"], "body_markdown": "y",
            "task_id": task_id, "session_id": sid,
        },
    )
    thread_id = r.json()["thread_id"]
    row = org_state.db._conn.execute(
        "SELECT payload FROM audit_log WHERE task_id = ? AND action = 'thread_started'",
        (thread_id,),
    ).fetchone()
    payload = _json.loads(row["payload"])
    assert payload["composed_by"] == "engineering_head"
    assert payload["composed_from_task_id"] == task_id


def test_compose_as_agent_liberal_authority_cross_team(
    tmp_home, app, org_state, auth_headers, daemon_state,
):
    """Spec §4.1: any agent → any agent, no team or role gate.

    An engineering-team worker composes a thread addressing the content-team
    manager. The route MUST accept — no role/team check fires.
    """
    _seed_agent(org_state, "dev_agent", team="engineering")
    _seed_agent(org_state, "content_manager", team="content")
    task_id, sid = _seed_active_task(
        org_state, daemon_state, "dev_agent",
        task_id="TASK-CROSS", sid="sid-cross",
    )
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "dev_agent", "subject": "cross-team coordination",
            "recipients": ["content_manager"], "body_markdown": "loop you in",
            "task_id": task_id, "session_id": sid,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["composed_by"] == "dev_agent"
    assert r.json()["pending_replies"] == ["content_manager"]


def test_compose_as_agent_single_recipient_mints_one_invocation(
    tmp_home, app, org_state, auth_headers, daemon_state,
):
    """When recipients lists a single agent, that agent gets exactly one REPLY
    invocation. Broadcast doesn't double-mint per duplicate recipient (the
    participant set is deduplicated)."""
    _seed_agent(org_state, "engineering_head")
    _seed_agent(org_state, "payment_agt")
    task_id, sid = _seed_active_task(org_state, daemon_state, "engineering_head")
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/threads/compose-as-agent",
        headers=auth_headers,
        json={
            "composer": "engineering_head", "subject": "s",
            "recipients": ["payment_agt", "payment_agt"], "body_markdown": "b",
            "task_id": task_id, "session_id": sid,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Exactly one entry in pending_replies and exactly one row in the DB.
    assert body["pending_replies"] == ["payment_agt"]
    invs = org_state.db.list_thread_invocations(body["thread_id"])
    assert len(invs) == 1
    assert invs[0].agent_name == "payment_agt"


