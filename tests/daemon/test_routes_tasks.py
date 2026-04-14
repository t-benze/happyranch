from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def stub_runner(monkeypatch):
    """Don't actually run tasks during route tests."""
    async def fake_run(self, task_id):
        return None
    monkeypatch.setattr("src.daemon.runner.TaskRunner.run", fake_run)


def test_submit_task_returns_id(tmp_home, app, auth_headers) -> None:
    r = TestClient(app).post(
        "/api/v1/tasks",
        json={"type": "general", "brief": "test"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["task_id"].startswith("TASK-")


def test_submit_task_idle_returns_409(tmp_home, app_idle, auth_headers) -> None:
    r = TestClient(app_idle).post(
        "/api/v1/tasks",
        json={"type": "general", "brief": "x"},
        headers=auth_headers,
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "no_active_runtime"


def test_list_tasks_returns_list(tmp_home, app, auth_headers) -> None:
    TestClient(app).post(
        "/api/v1/tasks", json={"type": "general", "brief": "x"}, headers=auth_headers,
    )
    r = TestClient(app).get("/api/v1/tasks", headers=auth_headers)
    assert r.status_code == 200
    items = r.json()["tasks"]
    assert len(items) >= 1


def test_get_task_detail_404_when_missing(tmp_home, app, auth_headers) -> None:
    r = TestClient(app).get("/api/v1/tasks/TASK-999", headers=auth_headers)
    assert r.status_code == 404


def test_submit_task_invalid_type_returns_422(tmp_home, app, auth_headers) -> None:
    r = TestClient(app).post(
        "/api/v1/tasks",
        json={"type": "garbage", "brief": "x"},
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_completion_requires_session_id(tmp_home, app, auth_headers) -> None:
    # Create a task first
    sub = TestClient(app).post(
        "/api/v1/tasks",
        json={"type": "general", "brief": "x"},
        headers=auth_headers,
    )
    task_id = sub.json()["task_id"]

    r = TestClient(app).post(
        f"/api/v1/tasks/{task_id}/completion",
        json={"agent": "dev_agent", "status": "completed", "confidence": 90,
              "output_summary": "ok"},
        headers=auth_headers,
    )
    assert r.status_code == 422  # missing session_id


def test_completion_session_mismatch_409(tmp_home, app, daemon_state, auth_headers) -> None:
    sub = TestClient(app).post(
        "/api/v1/tasks",
        json={"type": "general", "brief": "x"},
        headers=auth_headers,
    )
    task_id = sub.json()["task_id"]

    # Mark a different session_id as active.
    daemon_state.sessions.set_active(task_id, "dev_agent", "sess-real")

    r = TestClient(app).post(
        f"/api/v1/tasks/{task_id}/completion",
        json={"session_id": "sess-stale", "agent": "dev_agent",
              "status": "completed", "confidence": 90, "output_summary": "ok"},
        headers=auth_headers,
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "session_mismatch"


def test_completion_unknown_session_409(tmp_home, app, daemon_state, auth_headers) -> None:
    """If the daemon never registered a session for (task, agent), reject —
    do not silently persist a fabricated completion."""
    sub = TestClient(app).post(
        "/api/v1/tasks",
        json={"type": "general", "brief": "x"},
        headers=auth_headers,
    )
    task_id = sub.json()["task_id"]
    # Note: no set_active() call — tracker is empty for (task_id, dev_agent).

    r = TestClient(app).post(
        f"/api/v1/tasks/{task_id}/completion",
        json={"session_id": "fabricated", "agent": "dev_agent",
              "status": "completed", "confidence": 90, "output_summary": "ok"},
        headers=auth_headers,
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "unknown_session"
    # And nothing was persisted.
    assert daemon_state.db.get_task_results(task_id) == []


def test_completion_persists_when_session_matches(tmp_home, app, daemon_state, auth_headers) -> None:
    sub = TestClient(app).post(
        "/api/v1/tasks",
        json={"type": "general", "brief": "x"},
        headers=auth_headers,
    )
    task_id = sub.json()["task_id"]
    daemon_state.sessions.set_active(task_id, "dev_agent", "sess-1")

    r = TestClient(app).post(
        f"/api/v1/tasks/{task_id}/completion",
        json={"session_id": "sess-1", "agent": "dev_agent",
              "status": "completed", "confidence": 90, "output_summary": "ok"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    rows = daemon_state.db.get_task_results(task_id)
    assert any(r["session_id"] == "sess-1" for r in rows)


def test_completion_clears_session_so_duplicate_rejected(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    """After a successful completion POST, the tracker must be cleared so that a
    second POST with the same session id is rejected as unknown_session rather
    than silently persisting a duplicate row."""
    sub = TestClient(app).post(
        "/api/v1/tasks",
        json={"type": "general", "brief": "x"},
        headers=auth_headers,
    )
    task_id = sub.json()["task_id"]
    daemon_state.sessions.set_active(task_id, "dev_agent", "sess-1")

    payload = {
        "session_id": "sess-1", "agent": "dev_agent",
        "status": "completed", "confidence": 90, "output_summary": "ok",
    }
    first = TestClient(app).post(
        f"/api/v1/tasks/{task_id}/completion", json=payload, headers=auth_headers,
    )
    assert first.status_code == 200

    second = TestClient(app).post(
        f"/api/v1/tasks/{task_id}/completion", json=payload, headers=auth_headers,
    )
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "unknown_session"
    # And the second POST did not persist a duplicate row.
    rows = daemon_state.db.get_task_results(task_id)
    assert len([r for r in rows if r["session_id"] == "sess-1"]) == 1


def test_completion_preserves_empty_risks_flagged(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    """An empty risks_flagged list submitted by the agent must round-trip as an
    empty list, not be coerced to NULL/None by the DB layer."""
    sub = TestClient(app).post(
        "/api/v1/tasks",
        json={"type": "general", "brief": "x"},
        headers=auth_headers,
    )
    task_id = sub.json()["task_id"]
    daemon_state.sessions.set_active(task_id, "dev_agent", "sess-1")

    r = TestClient(app).post(
        f"/api/v1/tasks/{task_id}/completion",
        json={"session_id": "sess-1", "agent": "dev_agent",
              "status": "completed", "confidence": 90, "output_summary": "ok",
              "risks_flagged": []},
        headers=auth_headers,
    )
    assert r.status_code == 200
    latest = daemon_state.db.get_latest_task_result(task_id, "dev_agent", "sess-1")
    assert latest is not None
    assert latest["risks_flagged"] == []


def test_events_stream_yields_completion(tmp_home, app, daemon_state, auth_headers) -> None:
    sub = TestClient(app).post(
        "/api/v1/tasks",
        json={"type": "general", "brief": "x"},
        headers=auth_headers,
    )
    task_id = sub.json()["task_id"]

    # Set the task to a terminal status so history_loader synthesizes a
    # task_complete event on subscribe — the stream closes immediately without
    # needing to publish into an empty bus.
    from src.models import TaskStatus
    daemon_state.db.update_task(task_id, status=TaskStatus.APPROVED)

    with TestClient(app).stream(
        "GET", f"/api/v1/tasks/{task_id}/events", headers=auth_headers,
    ) as r:
        assert r.status_code == 200
        body = b"".join(r.iter_bytes())
    assert b"task_complete" in body
