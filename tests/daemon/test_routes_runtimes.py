from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.daemon import runtimes as reg
from src.models import TaskRecord, TaskStatus
from src.runtime import RuntimeDir


def _make_runtime(base: Path, name: str) -> Path:
    rt = RuntimeDir.init(base / name, slug="test")
    # Seed teams.yaml so team manager lookups work without DEFAULT_LAYOUT.
    rt.teams_config_path.write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: engineering_head\n"
        "    workers: [product_manager, dev_agent, payment_agent, qa_engineer]\n"
        "  content:\n"
        "    manager: content_manager\n"
        "    workers: [content_writer, content_qa, seo_agent]\n"
    )
    return rt.root


def test_list_runtimes_empty(tmp_home, app_idle, auth_headers) -> None:
    r = TestClient(app_idle).get("/api/v1/runtimes", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"active": None, "registered": []}


def test_register_runtime(tmp_home, app_idle, auth_headers, tmp_path: Path) -> None:
    rt_path = _make_runtime(tmp_path, "rt-a")
    r = TestClient(app_idle).post(
        "/api/v1/runtimes/register",
        json={"path": str(rt_path)},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["active"] == str(rt_path)
    assert str(rt_path) in body["registered"]


def test_activate_unknown_path_404(tmp_home, app_idle, auth_headers) -> None:
    r = TestClient(app_idle).post(
        "/api/v1/runtimes/activate",
        json={"path": "/does/not/exist"},
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_activate_blocked_by_in_flight_task(
    tmp_home, app, daemon_state, auth_headers, tmp_path: Path,
) -> None:
    # Register a second runtime first.
    other = _make_runtime(tmp_path, "rt-other")
    reg.register(daemon_state.runtime.root)
    reg.register(other)
    reg.activate(daemon_state.runtime.root)

    # Insert an IN_PROGRESS task on the active runtime.
    task = TaskRecord(id="TASK-001", brief="x")
    daemon_state.db.insert_task(task)
    daemon_state.db.update_task("TASK-001", status=TaskStatus.IN_PROGRESS)

    r = TestClient(app).post(
        "/api/v1/runtimes/activate",
        json={"path": str(other)},
        headers=auth_headers,
    )
    assert r.status_code == 409
    body = r.json()
    assert body["detail"]["code"] == "active_tasks_in_flight"
    assert "TASK-001" in body["detail"]["task_ids"]


def test_activate_blocked_by_pending_task(
    tmp_home, app, daemon_state, auth_headers, tmp_path: Path,
) -> None:
    """A submitted-but-not-yet-running task must also block activation —
    its runner already holds the current runtime reference."""
    other = _make_runtime(tmp_path, "rt-other")
    reg.register(daemon_state.runtime.root)
    reg.register(other)
    reg.activate(daemon_state.runtime.root)

    # Insert a PENDING task — never marked IN_PROGRESS.
    task = TaskRecord(id="TASK-002", brief="y")
    daemon_state.db.insert_task(task)

    r = TestClient(app).post(
        "/api/v1/runtimes/activate",
        json={"path": str(other)},
        headers=auth_headers,
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "active_tasks_in_flight"
    assert "TASK-002" in r.json()["detail"]["task_ids"]


def test_unauthenticated_request_401(tmp_home, app_idle) -> None:
    r = TestClient(app_idle).get("/api/v1/runtimes")
    assert r.status_code == 401


def test_register_runtime_populates_teams(tmp_home, app_idle, auth_headers, tmp_path: Path) -> None:
    rt_path = _make_runtime(tmp_path, "rt-teams")
    r = TestClient(app_idle).post(
        "/api/v1/runtimes/register",
        json={"path": str(rt_path)},
        headers=auth_headers,
    )
    assert r.status_code == 200
    daemon = app_idle.state.daemon
    assert daemon.teams is not None
    assert daemon.teams.manager_for_team("engineering").name == "engineering_head"
    assert daemon.teams.manager_for_team("content").name == "content_manager"
