from __future__ import annotations

from fastapi.testclient import TestClient


def test_start_talk_creates_row(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    r = client.post("/api/v1/orgs/alpha/talks", json={"agent_name": "dev_agent"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["talk_id"] == "TALK-001"
    assert "started_at" in body

    detail = client.get(f"/api/v1/orgs/alpha/talks/{body['talk_id']}", headers=auth_headers).json()
    assert detail["status"] == "open"


def test_start_talk_idle_runtime(tmp_home, app_idle, auth_headers):
    client = TestClient(app_idle)
    r = client.post("/api/v1/orgs/alpha/talks", json={"agent_name": "dev_agent"}, headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "no_active_runtime"


def test_start_talk_conflict_when_open_exists(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    first = client.post("/api/v1/orgs/alpha/talks", json={"agent_name": "dev_agent"}, headers=auth_headers).json()
    second = client.post("/api/v1/orgs/alpha/talks", json={"agent_name": "dev_agent"}, headers=auth_headers)
    assert second.status_code == 409
    detail = second.json()["detail"]
    assert detail["code"] == "talk_already_open"
    assert detail["prior_open_talk_id"] == first["talk_id"]


def test_resume_open_talk(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid = client.post("/api/v1/orgs/alpha/talks", json={"agent_name": "dev_agent"}, headers=auth_headers).json()["talk_id"]
    r = client.post(f"/api/v1/orgs/alpha/talks/{tid}/resume", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["talk_id"] == tid


def test_resume_closed_talk_rejected(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid = client.post("/api/v1/orgs/alpha/talks", json={"agent_name": "dev_agent"}, headers=auth_headers).json()["talk_id"]
    client.post(f"/api/v1/orgs/alpha/talks/{tid}/abandon", json={"reason": "test"}, headers=auth_headers)
    r = client.post(f"/api/v1/orgs/alpha/talks/{tid}/resume", headers=auth_headers)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "talk_not_open"


def test_abandon_open_talk(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid = client.post("/api/v1/orgs/alpha/talks", json={"agent_name": "dev_agent"}, headers=auth_headers).json()["talk_id"]
    r = client.post(f"/api/v1/orgs/alpha/talks/{tid}/abandon", json={"reason": "orphan"}, headers=auth_headers)
    assert r.status_code == 200
    detail = client.get(f"/api/v1/orgs/alpha/talks/{tid}", headers=auth_headers).json()
    assert detail["status"] == "abandoned"
    assert detail["ended_at"] is not None


def test_abandon_already_closed(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid = client.post("/api/v1/orgs/alpha/talks", json={"agent_name": "dev_agent"}, headers=auth_headers).json()["talk_id"]
    client.post(f"/api/v1/orgs/alpha/talks/{tid}/abandon", json={"reason": "first"}, headers=auth_headers)
    r = client.post(f"/api/v1/orgs/alpha/talks/{tid}/abandon", json={"reason": "second"}, headers=auth_headers)
    assert r.status_code == 400


def test_abandon_missing_talk(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    r = client.post("/api/v1/orgs/alpha/talks/TALK-999/abandon", json={"reason": "x"}, headers=auth_headers)
    assert r.status_code == 404


def test_end_talk_persists_everything(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid = client.post("/api/v1/orgs/alpha/talks", json={"agent_name": "dev_agent"}, headers=auth_headers).json()["talk_id"]
    # Pre-create the workspace so the learnings-append has a directory
    workspaces_dir = org_state.root / "workspaces"
    (workspaces_dir / "dev_agent").mkdir(parents=True, exist_ok=True)

    body = {
        "summary": "Covered refund flow.",
        "topic_list": ["refunds"],
        "transcript_markdown": "## turn 1\n...",
        "learnings": [
            {"text": "Alipay refund sometimes 504s; retry 2x."},
            {"text": "Founder prefers Codex on infra tasks."},
        ],
        "kb_slugs": [],
    }
    r = client.post(f"/api/v1/orgs/alpha/talks/{tid}/end", json=body, headers=auth_headers)
    assert r.status_code == 200, r.text
    resp = r.json()
    assert resp["status"] == "closed"
    assert resp["new_learnings_count"] == 2
    assert resp["transcript_path"].endswith(f"{tid}.md")

    # Row state
    detail = client.get(f"/api/v1/orgs/alpha/talks/{tid}", headers=auth_headers).json()
    assert detail["status"] == "closed"
    assert detail["summary"] == "Covered refund flow."
    assert detail["topic_list"] == ["refunds"]
    assert detail["new_learnings_count"] == 2
    assert "Covered refund flow." in detail["transcript"]

    # Learnings file
    learnings = (workspaces_dir / "dev_agent" / "learnings.md").read_text()
    assert "Alipay refund sometimes 504s" in learnings
    assert "Founder prefers Codex" in learnings


def test_end_talk_kb_slugs_must_exist(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid = client.post("/api/v1/orgs/alpha/talks", json={"agent_name": "dev_agent"}, headers=auth_headers).json()["talk_id"]
    (org_state.root / "workspaces" / "dev_agent").mkdir(parents=True, exist_ok=True)
    body = {
        "summary": "ok", "topic_list": [], "transcript_markdown": "t",
        "learnings": [], "kb_slugs": ["never-written-slug"],
    }
    r = client.post(f"/api/v1/orgs/alpha/talks/{tid}/end", json=body, headers=auth_headers)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "unknown_kb_slug"


def test_end_talk_already_closed(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    tid = client.post("/api/v1/orgs/alpha/talks", json={"agent_name": "dev_agent"}, headers=auth_headers).json()["talk_id"]
    (org_state.root / "workspaces" / "dev_agent").mkdir(parents=True, exist_ok=True)
    body = {"summary": "s", "topic_list": [], "transcript_markdown": "t",
            "learnings": [], "kb_slugs": []}
    client.post(f"/api/v1/orgs/alpha/talks/{tid}/end", json=body, headers=auth_headers)
    r = client.post(f"/api/v1/orgs/alpha/talks/{tid}/end", json=body, headers=auth_headers)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "talk_not_open"


def test_list_talks_filters(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    a1 = client.post(
        "/api/v1/orgs/alpha/talks", json={"agent_name": "dev_agent"}, headers=auth_headers
    ).json()["talk_id"]
    (org_state.root / "workspaces" / "dev_agent").mkdir(parents=True, exist_ok=True)
    client.post(
        f"/api/v1/orgs/alpha/talks/{a1}/end",
        json={
            "summary": "s",
            "topic_list": [],
            "transcript_markdown": "t",
            "learnings": [],
            "kb_slugs": [],
        },
        headers=auth_headers,
    )
    a2 = client.post(
        "/api/v1/orgs/alpha/talks", json={"agent_name": "dev_agent"}, headers=auth_headers
    ).json()["talk_id"]
    b1 = client.post(
        "/api/v1/orgs/alpha/talks", json={"agent_name": "qa_engineer"}, headers=auth_headers
    ).json()["talk_id"]

    all_talks = client.get("/api/v1/orgs/alpha/talks", headers=auth_headers).json()["talks"]
    assert {t["talk_id"] for t in all_talks} == {a1, a2, b1}

    dev_only = client.get(
        "/api/v1/orgs/alpha/talks", params={"agent": "dev_agent"}, headers=auth_headers
    ).json()["talks"]
    assert {t["talk_id"] for t in dev_only} == {a1, a2}

    open_only = client.get(
        "/api/v1/orgs/alpha/talks", params={"status": "open"}, headers=auth_headers
    ).json()["talks"]
    assert {t["talk_id"] for t in open_only} == {a2, b1}

    closed_dev = client.get(
        "/api/v1/orgs/alpha/talks",
        params={"agent": "dev_agent", "status": "closed"},
        headers=auth_headers,
    ).json()["talks"]
    assert [t["talk_id"] for t in closed_dev] == [a1]


def test_list_talks_limit_cap(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    r = client.get("/api/v1/orgs/alpha/talks", params={"limit": 99999}, headers=auth_headers)
    assert r.status_code == 200  # DB caps at 500; route doesn't reject


def test_get_missing_talk(tmp_home, app, org_state, auth_headers):
    client = TestClient(app)
    r = client.get("/api/v1/orgs/alpha/talks/TALK-999", headers=auth_headers)
    assert r.status_code == 404
