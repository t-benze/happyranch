from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runtime.config import Settings
from runtime.daemon.app import create_app
from runtime.daemon.state import DaemonState
from runtime.runtime import RuntimeDir


class _CloseTrackingSessions:
    def __init__(self) -> None:
        self.close_calls = 0

    async def close_all(self) -> None:
        self.close_calls += 1


@pytest.fixture
def auth(monkeypatch, tmp_path):
    home = tmp_path / "happyranch-home"
    home.mkdir()
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(home))
    from runtime.daemon import paths
    return {"Authorization": f"Bearer {paths.ensure_token()}"}


def test_get_runtime_idle(tmp_path: Path, auth) -> None:
    state = DaemonState.idle(Settings())
    client = TestClient(create_app(state))
    r = client.get("/api/v1/runtime", headers=auth)
    assert r.status_code == 200
    assert r.json() == {"runtime": None}


def test_post_runtime_registers(tmp_path: Path, auth) -> None:
    state = DaemonState.idle(Settings())
    client = TestClient(create_app(state))
    target = tmp_path / "rt"
    r = client.post(
        "/api/v1/runtime", headers=auth,
        json={"path": str(target)},
    )
    assert r.status_code == 200
    assert r.json()["runtime"] == str(target.resolve())
    assert state.runtime is not None


def test_post_runtime_closes_assistant_session_on_swap(
    tmp_path: Path,
    auth,
) -> None:
    rt = RuntimeDir.init(tmp_path / "rt-current")
    _seed_org(rt, "alpha")
    state = DaemonState.from_runtime(rt, Settings())
    sessions = _CloseTrackingSessions()
    state.assistant_sessions = sessions
    client = TestClient(create_app(state))

    response = client.post(
        "/api/v1/runtime",
        headers=auth,
        json={"path": str(tmp_path / "rt-new")},
    )

    assert response.status_code == 200, response.text
    assert sessions.close_calls == 1


def _seed_org(rt: RuntimeDir, slug: str) -> None:
    org_root = rt.orgs_dir / slug
    org_root.mkdir(parents=True)
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams: {}\n")


def test_post_runtime_refuses_swap_with_active_tasks(tmp_path: Path, auth) -> None:
    """`happyranch init <new-path>` must not orphan in-flight work in the current
    container — same guard as `/runtime/use`. Without it the old org's
    in-progress tasks lose their OrgState mid-flight and get dropped by
    the dispatcher as 'unknown org'."""
    from runtime.models import TaskRecord, TaskStatus
    rt = RuntimeDir.init(tmp_path / "rt-current")
    _seed_org(rt, "alpha")
    state = DaemonState.from_runtime(rt, Settings())
    state.orgs["alpha"].db.insert_task(
        TaskRecord(id="TASK-001", brief="x", status=TaskStatus.IN_PROGRESS)
    )
    client = TestClient(create_app(state))
    r = client.post(
        "/api/v1/runtime", headers=auth,
        json={"path": str(tmp_path / "rt-new")},
    )
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "active_tasks_in_flight"
    assert detail["org"] == "alpha"
    assert "TASK-001" in detail["task_ids"]
    # Original runtime is still active; the swap did not happen.
    assert state.runtime is not None
    assert state.runtime.root == rt.root


def test_post_runtime_swap_idempotent_to_same_path(tmp_path: Path, auth) -> None:
    """Re-registering the *same* runtime path must not be blocked by its own
    in-flight tasks — that would make `happyranch init <existing>` a no-op fail.
    Skip the guard when the requested path resolves to the active root."""
    from runtime.models import TaskRecord, TaskStatus
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_org(rt, "alpha")
    state = DaemonState.from_runtime(rt, Settings())
    state.orgs["alpha"].db.insert_task(
        TaskRecord(id="TASK-001", brief="x", status=TaskStatus.IN_PROGRESS)
    )
    client = TestClient(create_app(state))
    r = client.post(
        "/api/v1/runtime", headers=auth,
        json={"path": str(rt.root)},
    )
    assert r.status_code == 200, r.text


def test_use_runtime_closes_assistant_session_on_swap(tmp_path: Path, auth) -> None:
    from runtime.daemon import runtimes as reg

    current = RuntimeDir.init(tmp_path / "rt-current")
    target = RuntimeDir.init(tmp_path / "rt-target")
    reg.register(target.root)
    _seed_org(current, "alpha")
    _seed_org(target, "beta")
    state = DaemonState.from_runtime(current, Settings())
    sessions = _CloseTrackingSessions()
    state.assistant_sessions = sessions
    client = TestClient(create_app(state))

    response = client.post(
        "/api/v1/runtime/use",
        headers=auth,
        json={"path": str(target.root)},
    )

    assert response.status_code == 200, response.text
    assert sessions.close_calls == 1
