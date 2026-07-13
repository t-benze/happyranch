from __future__ import annotations

from fastapi.testclient import TestClient


def _seed(state):
    state.db.insert_audit_log("TASK-001", "dev_agent", "session_start", {"w": "/tmp/a"})
    state.db.insert_audit_log("TASK-001", "dev_agent", "session_end", {"duration_seconds": 30})
    state.db.insert_audit_log("TASK-002", "engineering_head", "escalation", {"reason": "x"})


def test_audit_requires_token(tmp_home, app) -> None:
    r = TestClient(app).get("/api/v1/orgs/alpha/audit")
    assert r.status_code == 401


def test_audit_idle_returns_409(tmp_home, app_idle, auth_headers) -> None:
    r = TestClient(app_idle).get("/api/v1/orgs/alpha/audit", headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "no_active_runtime"


def test_audit_returns_all_entries(tmp_home, app, org_state, auth_headers) -> None:
    _seed(org_state)
    r = TestClient(app).get("/api/v1/orgs/alpha/audit", headers=auth_headers)
    assert r.status_code == 200
    entries = r.json()["entries"]
    # THR-095: the org_settings seed writes 4 audit rows (config:dreaming,
    # config:threads, config:session_timeout_seconds, config:working_hours)
    # before the 3 _seed rows → total 7.
    assert len(entries) == 7
    assert [e["id"] for e in entries] == [1, 2, 3, 4, 5, 6, 7]
    # The first _seed entry (session_start) should be at id 5.
    assert entries[4]["action"] == "session_start"
    assert entries[4]["payload"] == {"w": "/tmp/a"}


def test_audit_filters_by_task_id(tmp_home, app, org_state, auth_headers) -> None:
    _seed(org_state)
    r = TestClient(app).get(
        "/api/v1/orgs/alpha/audit", params={"task_id": "TASK-001"}, headers=auth_headers,
    )
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert {e["task_id"] for e in entries} == {"TASK-001"}
    assert len(entries) == 2


def test_audit_filters_by_action_and_agent(tmp_home, app, org_state, auth_headers) -> None:
    _seed(org_state)
    r = TestClient(app).get(
        "/api/v1/orgs/alpha/audit",
        params={"agent": "engineering_head", "action": "escalation"},
        headers=auth_headers,
    )
    entries = r.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["payload"] == {"reason": "x"}


def test_audit_limit_caps_to_most_recent(tmp_home, app, org_state, auth_headers) -> None:
    _seed(org_state)
    r = TestClient(app).get(
        "/api/v1/orgs/alpha/audit", params={"limit": 2}, headers=auth_headers,
    )
    body = r.json()
    entries = body["entries"]
    # THR-095: 4 seed audit rows + 3 _seed rows → most recent 2 are ids 6,7.
    assert [e["id"] for e in entries] == [6, 7]
    assert "next_cursor" in body


def test_audit_cursor_pagination_shape(tmp_home, app, org_state, auth_headers) -> None:
    """Response shape includes entries + next_cursor (non-null when more pages)."""
    for i in range(5):
        org_state.db.insert_audit_log(
            "TASK-{:03d}".format(i), "dev_agent", "cursor_test", None
        )
    r = TestClient(app).get(
        "/api/v1/orgs/alpha/audit", params={"limit": 3}, headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert "entries" in body
    assert "next_cursor" in body
    assert len(body["entries"]) == 3
    assert body["next_cursor"] is not None


def test_audit_cursor_exhaustion_returns_null(tmp_home, app, org_state, auth_headers) -> None:
    """When the result set is exhausted, next_cursor is null."""
    for i in range(3):
        org_state.db.insert_audit_log(
            "TASK-{:03d}".format(i), "dev_agent", "cursor_test", None
        )
    r = TestClient(app).get(
        "/api/v1/orgs/alpha/audit", params={"limit": 10}, headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["next_cursor"] is None


def test_audit_cursor_param_passes_through(tmp_home, app, org_state, auth_headers) -> None:
    """Providing a cursor to the route returns the next page."""
    for i in range(5):
        org_state.db.insert_audit_log(
            "TASK-{:03d}".format(i), "dev_agent", "cursor_test", None
        )
    # Get first page
    r1 = TestClient(app).get(
        "/api/v1/orgs/alpha/audit", params={"limit": 2}, headers=auth_headers,
    )
    cursor = r1.json()["next_cursor"]
    assert cursor is not None
    page1_ids = {e["id"] for e in r1.json()["entries"]}

    # Get second page via cursor
    r2 = TestClient(app).get(
        "/api/v1/orgs/alpha/audit",
        params={"limit": 2, "cursor": cursor},
        headers=auth_headers,
    )
    assert r2.status_code == 200
    page2_ids = {e["id"] for e in r2.json()["entries"]}
    # No overlap between pages
    assert page1_ids & page2_ids == set()


def test_audit_bad_cursor_returns_422(tmp_home, app, org_state, auth_headers) -> None:
    """Malformed cursor returns 422."""
    _seed(org_state)
    r = TestClient(app).get(
        "/api/v1/orgs/alpha/audit",
        params={"limit": 2, "cursor": "garbage"},
        headers=auth_headers,
    )
    assert r.status_code == 422
