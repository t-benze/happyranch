from __future__ import annotations

from datetime import datetime, timezone

from runtime.models import DreamKbCandidate, DreamRecord, DreamStatus


def _dt(hour: int) -> datetime:
    return datetime(2026, 6, 9, hour, 0, tzinfo=timezone.utc)


def test_complete_dream_persists_outputs(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    client = TestClient(app)

    org_state.db.insert_dream(DreamRecord(
        id="DREAM-001",
        agent_name="dev_agent",
        local_date="2026-06-09",
        scheduled_for=_dt(2),
        window_start=_dt(1),
        window_end=_dt(2),
        status=DreamStatus.RUNNING,
    ))
    (org_state.root / "workspaces" / "dev_agent" / "learnings").mkdir(parents=True, exist_ok=True)

    resp = client.post("/api/v1/orgs/alpha/dreams/DREAM-001/complete", json={
        "summary": "Private summary.",
        "learnings": [{
            "slug": "dream-learning",
            "title": "Dream learning",
            "topic": "workflow",
            "body": "Private durable learning.\n",
        }],
        "kb_candidates": [{
            "slug": "candidate-one",
            "title": "Candidate One",
            "topic": "workflow",
            "rationale": "Repeated pattern.",
            "body_markdown": "Candidate body.\n",
        }],
        "founder_thread": {"needed": False},
    }, headers=auth_headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dream_id"] == "DREAM-001"
    assert body["status"] == "completed"

    dream = org_state.db.get_dream("DREAM-001")
    assert dream.status == DreamStatus.COMPLETED
    assert dream.new_learnings_count == 1
    assert dream.kb_candidate_count == 1
    assert dream.transcript_path
    assert org_state.db.list_dream_kb_candidates(dream_id="DREAM-001")[0].slug == "candidate-one"


def test_complete_dream_creates_founder_thread(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    client = TestClient(app)

    org_state.db.insert_dream(DreamRecord(
        id="DREAM-001",
        agent_name="dev_agent",
        local_date="2026-06-09",
        scheduled_for=_dt(2),
        window_end=_dt(2),
        status=DreamStatus.RUNNING,
    ))
    (org_state.root / "workspaces" / "dev_agent" / "learnings").mkdir(parents=True, exist_ok=True)

    resp = client.post("/api/v1/orgs/alpha/dreams/DREAM-001/complete", json={
        "summary": "Private summary.",
        "learnings": [],
        "kb_candidates": [],
        "founder_thread": {
            "needed": True,
            "subject": "Nightly reflection: dev_agent",
            "body_markdown": "Founder-visible finding.",
        },
    }, headers=auth_headers)

    assert resp.status_code == 200, resp.text
    dream = org_state.db.get_dream("DREAM-001")
    assert dream.founder_thread_id is not None
    thread = org_state.db.get_thread(dream.founder_thread_id)
    assert thread is not None
    assert thread.composed_by == "dev_agent"

    # Routed through the compose infrastructure: the dreaming agent is the sole
    # participant; @founder is a recipient literal, never a participant row.
    participants = [p.agent_name for p in org_state.db.list_thread_participants(thread.id)]
    assert participants == ["dev_agent"]
    assert "@founder" not in participants

    # Compose infra emits thread_started + thread_message_sent on the thread.
    thread_actions = [r["action"] for r in org_state.db.get_audit_logs(thread.id)]
    assert "thread_started" in thread_actions
    assert "thread_message_sent" in thread_actions

    # The dream records its own founder-thread-created audit row.
    dream_actions = [r["action"] for r in org_state.db.get_audit_logs("DREAM-001")]
    assert "dream_founder_thread_created" in dream_actions

    # A4: dream-composed threads carry composed_from_dream_id marker.
    assert thread.composed_from_dream_id == "DREAM-001"


def test_complete_dream_no_thread_when_not_needed(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    client = TestClient(app)

    org_state.db.insert_dream(DreamRecord(
        id="DREAM-002",
        agent_name="dev_agent",
        local_date="2026-06-09",
        scheduled_for=_dt(2),
        window_end=_dt(2),
        status=DreamStatus.RUNNING,
    ))
    (org_state.root / "workspaces" / "dev_agent" / "learnings").mkdir(parents=True, exist_ok=True)

    resp = client.post("/api/v1/orgs/alpha/dreams/DREAM-002/complete", json={
        "summary": "Private summary.",
        "learnings": [],
        "kb_candidates": [],
        "founder_thread": {"needed": False},
    }, headers=auth_headers)

    assert resp.status_code == 200, resp.text
    dream = org_state.db.get_dream("DREAM-002")
    assert dream.founder_thread_id is None
    dream_actions = [r["action"] for r in org_state.db.get_audit_logs("DREAM-002")]
    assert "dream_founder_thread_created" not in dream_actions


def test_list_and_show_dreams(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    client = TestClient(app)

    org_state.db.insert_dream(DreamRecord(
        id="DREAM-001",
        agent_name="dev_agent",
        local_date="2026-06-09",
        scheduled_for=_dt(2),
        window_end=_dt(2),
    ))

    list_resp = client.get("/api/v1/orgs/alpha/dreams", headers=auth_headers)
    assert list_resp.status_code == 200
    assert list_resp.json()["dreams"][0]["dream_id"] == "DREAM-001"

    show_resp = client.get("/api/v1/orgs/alpha/dreams/DREAM-001", headers=auth_headers)
    assert show_resp.status_code == 200
    assert show_resp.json()["dream_id"] == "DREAM-001"


# --- Candidate accept / dismiss -------------------------------------------------

def test_accept_candidate_creates_kb_and_promotes(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    client = TestClient(app)

    org_state.db.insert_dream(DreamRecord(
        id="DREAM-001", agent_name="dev_agent", local_date="2026-06-09",
        scheduled_for=_dt(2), window_end=_dt(2),
    ))
    org_state.db.insert_dream_kb_candidate(DreamKbCandidate(
        dream_id="DREAM-001", agent_name="dev_agent",
        slug="candidate-one", title="Candidate One", topic="workflow",
        rationale="Observed.", body_markdown="Candidate body.\n",
    ))
    rows = org_state.db.list_dream_kb_candidates(dream_id="DREAM-001")
    candidate_id = rows[0].id

    resp = client.post(
        f"/api/v1/orgs/alpha/dreams/candidates/{candidate_id}/accept",
        json={}, headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "promoted"
    assert body["promoted_kb_slug"] == "candidate-one"

    # Verify KB entry was created
    from runtime.infrastructure.kb_store import KBStore
    store = KBStore(org_state.root / "kb")
    entry = store.read_entry("candidate-one")
    assert entry.title == "Candidate One"
    assert entry.topic == "workflow"
    assert entry.body == "Candidate body.\n"
    assert entry.source_task == "DREAM-001"


def test_dismiss_candidate_rejects(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    client = TestClient(app)

    org_state.db.insert_dream(DreamRecord(
        id="DREAM-001", agent_name="dev_agent", local_date="2026-06-09",
        scheduled_for=_dt(2), window_end=_dt(2),
    ))
    org_state.db.insert_dream_kb_candidate(DreamKbCandidate(
        dream_id="DREAM-001", agent_name="dev_agent",
        slug="candidate-two", title="Candidate Two", topic="ci",
        rationale="Not needed.", body_markdown="Body.\n",
    ))
    rows = org_state.db.list_dream_kb_candidates(dream_id="DREAM-001")
    candidate_id = rows[0].id

    resp = client.post(
        f"/api/v1/orgs/alpha/dreams/candidates/{candidate_id}/dismiss",
        json={}, headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "rejected"
    assert body.get("promoted_kb_slug") is None


def test_accept_already_promoted_idempotent(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    client = TestClient(app)

    org_state.db.insert_dream(DreamRecord(
        id="DREAM-001", agent_name="dev_agent", local_date="2026-06-09",
        scheduled_for=_dt(2), window_end=_dt(2),
    ))
    org_state.db.insert_dream_kb_candidate(DreamKbCandidate(
        dream_id="DREAM-001", agent_name="dev_agent",
        slug="candidate-one", title="Candidate One", topic="workflow",
        rationale="Observed.", body_markdown="Candidate body.\n",
    ))
    rows = org_state.db.list_dream_kb_candidates(dream_id="DREAM-001")
    candidate_id = rows[0].id

    # First accept
    resp1 = client.post(
        f"/api/v1/orgs/alpha/dreams/candidates/{candidate_id}/accept",
        json={}, headers=auth_headers,
    )
    assert resp1.status_code == 200

    # Second accept — idempotent: returns 200 with same state
    resp2 = client.post(
        f"/api/v1/orgs/alpha/dreams/candidates/{candidate_id}/accept",
        json={}, headers=auth_headers,
    )
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "promoted"


def test_dismiss_already_rejected_idempotent(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    client = TestClient(app)

    org_state.db.insert_dream(DreamRecord(
        id="DREAM-001", agent_name="dev_agent", local_date="2026-06-09",
        scheduled_for=_dt(2), window_end=_dt(2),
    ))
    org_state.db.insert_dream_kb_candidate(DreamKbCandidate(
        dream_id="DREAM-001", agent_name="dev_agent",
        slug="candidate-two", title="Candidate Two", topic="ci",
        rationale="Not needed.", body_markdown="Body.\n",
    ))
    rows = org_state.db.list_dream_kb_candidates(dream_id="DREAM-001")
    candidate_id = rows[0].id

    # First dismiss
    resp1 = client.post(
        f"/api/v1/orgs/alpha/dreams/candidates/{candidate_id}/dismiss",
        json={}, headers=auth_headers,
    )
    assert resp1.status_code == 200

    # Second dismiss — idempotent
    resp2 = client.post(
        f"/api/v1/orgs/alpha/dreams/candidates/{candidate_id}/dismiss",
        json={}, headers=auth_headers,
    )
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "rejected"


def test_accept_candidate_slug_exists_collision(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    client = TestClient(app)

    # Pre-create a KB entry with the same slug
    from runtime.infrastructure.kb_store import KBEntry, KBStore
    store = KBStore(org_state.root / "kb")
    store.write_entry(KBEntry(
        slug="candidate-one", title="Already Exists", type="reference",
        topic="workflow", body="Existing body.\n",
    ), agent="dev_agent")
    store.regenerate_index()

    org_state.db.insert_dream(DreamRecord(
        id="DREAM-001", agent_name="dev_agent", local_date="2026-06-09",
        scheduled_for=_dt(2), window_end=_dt(2),
    ))
    org_state.db.insert_dream_kb_candidate(DreamKbCandidate(
        dream_id="DREAM-001", agent_name="dev_agent",
        slug="candidate-one", title="Candidate One", topic="workflow",
        rationale="Observed.", body_markdown="Candidate body.\n",
    ))
    rows = org_state.db.list_dream_kb_candidates(dream_id="DREAM-001")
    candidate_id = rows[0].id

    resp = client.post(
        f"/api/v1/orgs/alpha/dreams/candidates/{candidate_id}/accept",
        json={}, headers=auth_headers,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "slug_exists"


def test_accept_after_dismiss_returns_400(tmp_home, app, org_state, auth_headers):
    """Accept of a dismissed ('rejected') candidate must return 400 and NOT create a KB entry."""
    from fastapi.testclient import TestClient
    from runtime.infrastructure.kb_store import KBStore
    client = TestClient(app)

    org_state.db.insert_dream(DreamRecord(
        id="DREAM-001", agent_name="dev_agent", local_date="2026-06-09",
        scheduled_for=_dt(2), window_end=_dt(2),
    ))
    org_state.db.insert_dream_kb_candidate(DreamKbCandidate(
        dream_id="DREAM-001", agent_name="dev_agent",
        slug="candidate-rej", title="Rejected Candidate", topic="ci",
        rationale="Not needed.", body_markdown="Body.\n",
    ))
    rows = org_state.db.list_dream_kb_candidates(dream_id="DREAM-001")
    candidate_id = rows[0].id

    # Dismiss it first
    org_state.db.update_dream_kb_candidate(candidate_id, status="rejected")

    # Now try to accept the already-dismissed candidate
    resp = client.post(
        f"/api/v1/orgs/alpha/dreams/candidates/{candidate_id}/accept",
        json={}, headers=auth_headers,
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["detail"]["code"] == "candidate_already_decided"
    assert body["detail"]["status"] == "rejected"

    # Verify NO KB entry was created
    from runtime.infrastructure.kb_store import NotFound
    store = KBStore(org_state.root / "kb")
    try:
        store.read_entry("candidate-rej")
        assert False, "KB entry should NOT have been created for a dismissed candidate"
    except NotFound:
        pass  # Expected — no entry created

    # Candidate should still be rejected
    updated = org_state.db.list_dream_kb_candidates(candidate_id=candidate_id)[0]
    assert updated.status == "rejected"


def test_accept_superseded_returns_400(tmp_home, app, org_state, auth_headers):
    """Accept of a superseded candidate must return 400."""
    from fastapi.testclient import TestClient
    from runtime.infrastructure.kb_store import KBStore
    client = TestClient(app)

    org_state.db.insert_dream(DreamRecord(
        id="DREAM-001", agent_name="dev_agent", local_date="2026-06-09",
        scheduled_for=_dt(2), window_end=_dt(2),
    ))
    org_state.db.insert_dream_kb_candidate(DreamKbCandidate(
        dream_id="DREAM-001", agent_name="dev_agent",
        slug="candidate-sup", title="Superseded Candidate", topic="ci",
        rationale="Old.", body_markdown="Body.\n",
    ))
    rows = org_state.db.list_dream_kb_candidates(dream_id="DREAM-001")
    candidate_id = rows[0].id

    # Set it to superseded
    org_state.db.update_dream_kb_candidate(candidate_id, status="superseded")

    resp = client.post(
        f"/api/v1/orgs/alpha/dreams/candidates/{candidate_id}/accept",
        json={}, headers=auth_headers,
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["detail"]["code"] == "candidate_already_decided"
    assert body["detail"]["status"] == "superseded"

    # Verify NO KB entry was created
    from runtime.infrastructure.kb_store import NotFound
    store = KBStore(org_state.root / "kb")
    try:
        store.read_entry("candidate-sup")
        assert False, "KB entry should NOT have been created for a superseded candidate"
    except NotFound:
        pass  # Expected — no entry created


def test_candidate_not_found(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    client = TestClient(app)

    resp = client.post(
        "/api/v1/orgs/alpha/dreams/candidates/999/accept",
        json={}, headers=auth_headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "candidate_not_found"
