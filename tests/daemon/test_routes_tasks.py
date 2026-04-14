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
