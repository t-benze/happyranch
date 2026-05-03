from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from src.config import Settings
from src.daemon.app import create_app
from src.daemon.state import DaemonState
from src.runtime import RuntimeDir


def _seed_org(org_root: Path) -> None:
    org_root.mkdir(parents=True)
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams: {}\n")


@pytest.fixture
def auth(monkeypatch, tmp_path):
    home = tmp_path / "opc-home"
    home.mkdir()
    monkeypatch.setenv("OPC_DAEMON_HOME", str(home))
    from src.daemon import paths
    token = paths.ensure_token()
    return {"Authorization": f"Bearer {token}"}


def test_list_orgs_returns_loaded(tmp_path: Path, auth) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_org(rt.orgs_dir / "alpha")
    _seed_org(rt.orgs_dir / "beta")
    state = DaemonState.from_runtime(rt, Settings())
    client = TestClient(create_app(state))
    r = client.get("/api/v1/orgs", headers=auth)
    assert r.status_code == 200
    assert sorted(o["slug"] for o in r.json()["orgs"]) == ["alpha", "beta"]


def test_init_org_creates_skeleton_and_loads(tmp_path: Path, auth) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    state = DaemonState.from_runtime(rt, Settings())
    client = TestClient(create_app(state))
    r = client.post("/api/v1/orgs", headers=auth, json={"slug": "alpha"})
    assert r.status_code == 200
    assert (rt.orgs_dir / "alpha" / "org" / "teams.yaml").is_file()
    assert "alpha" in state.orgs


def test_init_org_invalid_slug_400(tmp_path: Path, auth) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    state = DaemonState.from_runtime(rt, Settings())
    client = TestClient(create_app(state))
    r = client.post("/api/v1/orgs", headers=auth, json={"slug": "Bad Slug"})
    assert r.status_code == 400


def test_init_org_idempotent_returns_409(tmp_path: Path, auth) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_org(rt.orgs_dir / "alpha")
    state = DaemonState.from_runtime(rt, Settings())
    client = TestClient(create_app(state))
    r = client.post("/api/v1/orgs", headers=auth, json={"slug": "alpha"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "org_exists"


def test_unload_org_succeeds_when_no_active_work(tmp_path: Path, auth) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_org(rt.orgs_dir / "alpha")
    state = DaemonState.from_runtime(rt, Settings())
    client = TestClient(create_app(state))
    r = client.delete("/api/v1/orgs/alpha", headers=auth)
    assert r.status_code == 200, r.text
    assert "alpha" not in state.orgs


def test_unload_org_refuses_with_active_tasks(tmp_path: Path, auth) -> None:
    """DELETE /orgs/{slug} must not silently strand non-terminal tasks.
    Without this guard the dispatcher drops their re-enqueues as 'unknown
    org' and any in-flight agent callback hits a 404 because OrgDep can no
    longer resolve the slug."""
    from src.models import TaskRecord, TaskStatus
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_org(rt.orgs_dir / "alpha")
    state = DaemonState.from_runtime(rt, Settings())
    state.orgs["alpha"].db.insert_task(
        TaskRecord(id="TASK-001", brief="x", status=TaskStatus.IN_PROGRESS)
    )
    client = TestClient(create_app(state))
    r = client.delete("/api/v1/orgs/alpha", headers=auth)
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "active_tasks_in_flight"
    assert detail["slug"] == "alpha"
    assert "TASK-001" in detail["task_ids"]
    # Org is still loaded.
    assert "alpha" in state.orgs


def test_unload_org_refuses_with_blocked_tasks(tmp_path: Path, auth) -> None:
    """blocked is non-terminal — a blocked-escalated task is waiting on the
    founder. Unloading the org would make `opc resolve-escalation` hit a 404."""
    from src.models import BlockKind, TaskRecord, TaskStatus
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_org(rt.orgs_dir / "alpha")
    state = DaemonState.from_runtime(rt, Settings())
    state.orgs["alpha"].db.insert_task(
        TaskRecord(
            id="TASK-007", brief="x",
            status=TaskStatus.BLOCKED, block_kind=BlockKind.ESCALATED,
        )
    )
    client = TestClient(create_app(state))
    r = client.delete("/api/v1/orgs/alpha", headers=auth)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "active_tasks_in_flight"
    assert "alpha" in state.orgs
