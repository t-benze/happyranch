from __future__ import annotations

from datetime import datetime, timezone

from runtime.models import DreamRecord, DreamStatus


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
