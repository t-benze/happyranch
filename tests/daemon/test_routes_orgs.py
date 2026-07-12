from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from runtime.config import Settings
from runtime.daemon.app import create_app
from runtime.daemon.routes.orgs import _seed_skeleton
from runtime.daemon.state import DaemonState
from runtime.orchestrator.org_validation import OrgConsistencyError
from runtime.runtime import RuntimeDir


def _seed_org(org_root: Path) -> None:
    org_root.mkdir(parents=True)
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams: {}\n")


@pytest.fixture
def auth(monkeypatch, tmp_path):
    home = tmp_path / "happyranch-home"
    home.mkdir()
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(home))
    from runtime.daemon import paths
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
    body = r.json()
    assert sorted(o["slug"] for o in body["orgs"]) == ["alpha", "beta"]
    assert body["broken"] == []


def test_list_orgs_surfaces_broken_orgs(tmp_path: Path, auth) -> None:
    """A drifted org appears under 'broken' so the founder isn't left guessing."""
    from datetime import datetime, timezone
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.agent_def import AgentDef, render_agent_text

    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_org(rt.orgs_dir / "alpha")
    broken_root = rt.orgs_dir / "broken"
    _seed_org(broken_root)
    (broken_root / "org" / "agents").mkdir()
    manager = AgentDef(
        name="solo_manager",
        team="missing_team",
        role="manager",
        executor="claude",
        allow_rules=(),
        repos={},
        enrolled_by="founder",
        enrolled_at_task=None,
        enrolled_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
        system_prompt="You are solo.\n",
        description="Solo",
    )
    (OrgPaths(root=broken_root).agents_dir / "solo_manager.md").write_text(
        render_agent_text(manager),
    )

    state = DaemonState.from_runtime(rt, Settings())
    client = TestClient(create_app(state))
    r = client.get("/api/v1/orgs", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert [o["slug"] for o in body["orgs"]] == ["alpha"]
    assert len(body["broken"]) == 1
    assert body["broken"][0]["slug"] == "broken"
    assert "missing_team" in body["broken"][0]["error"]


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
    from runtime.models import TaskRecord, TaskStatus
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
    """escalated is non-terminal — an escalated task is waiting on the founder.
    Unloading the org would make `happyranch resolve-escalation` hit a 404.
    (Path B: the awaiting-founder state is the top-level ESCALATED status.)"""
    from runtime.models import TaskRecord, TaskStatus
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_org(rt.orgs_dir / "alpha")
    state = DaemonState.from_runtime(rt, Settings())
    state.orgs["alpha"].db.insert_task(
        TaskRecord(
            id="TASK-007", brief="x",
            status=TaskStatus.ESCALATED, block_kind=None,
        )
    )
    client = TestClient(create_app(state))
    r = client.delete("/api/v1/orgs/alpha", headers=auth)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "active_tasks_in_flight"
    assert "alpha" in state.orgs


# ── THR-088: org-creation dead-end trap + fix ────────────────────────────

def _seed_pristine_skeleton(org_root: Path) -> None:
    """Reproduce the exact skeleton _seed_skeleton lays down for an empty org."""
    _seed_skeleton(org_root, from_example=None)


# STEP 0 — formerly the trap: pristine skeleton used to 409 org_dir_exists
# permanently. Now it is silently reclaimed.
@pytest.mark.asyncio
async def test_init_org_pristine_skeleton_is_reclaimed_not_trapped(
    tmp_path: Path, auth
) -> None:
    """A pristine skeleton left by a prior failed create is silently
    reclaimed — create succeeds, curing the permanent trap."""
    rt = RuntimeDir.init(tmp_path / "rt")
    state = DaemonState.from_runtime(rt, Settings())

    # Seed a pristine skeleton on disk (no loaded org).
    org_root = rt.orgs_dir / "trap"
    _seed_pristine_skeleton(org_root)
    assert org_root.exists()
    assert "trap" not in state.orgs

    client = TestClient(create_app(state))
    r = client.post("/api/v1/orgs", headers=auth, json={"slug": "trap"})
    assert r.status_code == 200
    assert "trap" in state.orgs


# PART 1 — transactional create: rollback on failure
@pytest.mark.asyncio
async def test_init_org_failed_create_rolls_back_stale_dir(
    tmp_path: Path, auth, monkeypatch
) -> None:
    """When add_org fails AFTER _seed_skeleton created the fresh skeleton,
    the just-seeded directory must be removed — no stale dir left on disk."""
    rt = RuntimeDir.init(tmp_path / "rt")
    state = DaemonState.from_runtime(rt, Settings())
    org_root = rt.orgs_dir / "rollback-test"

    # Patch add_org to raise OrgConsistencyError on the target slug.
    original_add_org = state.add_org
    async def failing_add_org(slug: str):
        if slug == "rollback-test":
            raise OrgConsistencyError("simulated teams.yaml drift")
        return await original_add_org(slug)
    monkeypatch.setattr(state, "add_org", failing_add_org)

    client = TestClient(create_app(state))
    # OrgConsistencyError is not an HTTPException; TestClient re-raises
    # unhandled exceptions. Catch it to verify the rollback happened.
    with pytest.raises(OrgConsistencyError, match="simulated teams.yaml drift"):
        client.post("/api/v1/orgs", headers=auth, json={"slug": "rollback-test"})
    # The just-seeded directory is gone.
    assert not org_root.exists(), (
        "rollback must remove the skeleton that THIS call seeded"
    )


# PART 1b — transactional create: valid org still succeeds (no regression)
@pytest.mark.asyncio
async def test_init_org_transactional_create_leaves_valid_org_intact(
    tmp_path: Path, auth
) -> None:
    """A successful create must still produce the skeleton and load the org."""
    rt = RuntimeDir.init(tmp_path / "rt")
    state = DaemonState.from_runtime(rt, Settings())
    client = TestClient(create_app(state))
    r = client.post("/api/v1/orgs", headers=auth, json={"slug": "alpha"})
    assert r.status_code == 200
    assert (rt.orgs_dir / "alpha" / "org" / "teams.yaml").is_file()
    assert "alpha" in state.orgs


# PART 2a — recovery of a previously-stuck pristine slug
@pytest.mark.asyncio
async def test_init_org_recovers_stuck_pristine_skeleton(
    tmp_path: Path, auth
) -> None:
    """A previously-stuck pristine skeleton (no real data) is silently
    reclaimed — the create succeeds as if the dir never existed."""
    rt = RuntimeDir.init(tmp_path / "rt")
    state = DaemonState.from_runtime(rt, Settings())

    # Seed a pristine skeleton to simulate a prior failed create.
    org_root = rt.orgs_dir / "recoverable"
    _seed_pristine_skeleton(org_root)
    assert org_root.exists()
    assert "recoverable" not in state.orgs

    client = TestClient(create_app(state))
    r = client.post("/api/v1/orgs", headers=auth, json={"slug": "recoverable"})
    # Recovery is transparent — create succeeds.
    assert r.status_code == 200, r.text
    assert "recoverable" in state.orgs
    assert r.json()["slug"] == "recoverable"


# PART 2b — data-bearing dir is NEVER removed
@pytest.mark.asyncio
async def test_init_org_data_bearing_dir_returns_protective_409(
    tmp_path: Path, auth
) -> None:
    """A dir with real data (a non-skeleton agent file) is NEVER auto-deleted.
    Returns a distinct protective 409 'org_dir_has_data'."""
    rt = RuntimeDir.init(tmp_path / "rt")
    state = DaemonState.from_runtime(rt, Settings())

    # Seed a pristine skeleton first, then add a real agent file.
    org_root = rt.orgs_dir / "has-data"
    _seed_pristine_skeleton(org_root)
    # Add a real agent file beyond _pending.
    agents_dir = org_root / "org" / "agents"
    (agents_dir / "founder.md").write_text("# founder agent")
    assert org_root.exists()
    assert "has-data" not in state.orgs

    client = TestClient(create_app(state))
    r = client.post("/api/v1/orgs", headers=auth, json={"slug": "has-data"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "org_dir_has_data"
    # Dir is NOT removed — protect the data.
    assert org_root.exists()
    # Org is not loaded.
    assert "has-data" not in state.orgs


# PART 2b-extra — dir with populated DB is protected
@pytest.mark.asyncio
async def test_init_org_dir_with_tasks_db_returns_protective_409(
    tmp_path: Path, auth
) -> None:
    """A dir with a happyranch.db containing tasks is NEVER auto-deleted."""
    import sqlite3
    rt = RuntimeDir.init(tmp_path / "rt")
    state = DaemonState.from_runtime(rt, Settings())

    # Seed skeleton, then create a db with a task in it.
    org_root = rt.orgs_dir / "db-org"
    _seed_pristine_skeleton(org_root)
    db_path = org_root / "happyranch.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT, brief TEXT, status TEXT)")
    conn.execute("INSERT INTO tasks VALUES ('TASK-001', 'x', 'in_progress')")
    conn.commit()
    conn.close()
    assert org_root.exists()
    assert "db-org" not in state.orgs

    client = TestClient(create_app(state))
    r = client.post("/api/v1/orgs", headers=auth, json={"slug": "db-org"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "org_dir_has_data"
    assert org_root.exists()
    assert "db-org" not in state.orgs


# PART 2b-extra — dir with audit_log row (zero tasks) is protected
@pytest.mark.asyncio
async def test_init_org_dir_with_audit_log_row_returns_protective_409(
    tmp_path: Path, auth
) -> None:
    """A dir whose happyranch.db has an audit_log row (and zero task rows)
    must return the protective 409 'org_dir_has_data' AND the directory
    REMAINS on disk. This is the regression test for the code_reviewer
    CRITICAL: _is_reclaimable_partial previously only checked the tasks
    table, missing other durable non-task tables."""
    import sqlite3
    rt = RuntimeDir.init(tmp_path / "rt")
    state = DaemonState.from_runtime(rt, Settings())

    org_root = rt.orgs_dir / "audit-org"
    _seed_pristine_skeleton(org_root)
    db_path = org_root / "happyranch.db"
    conn = sqlite3.connect(str(db_path))
    # Create both tables — tasks (empty) + audit_log (populated).
    conn.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT, brief TEXT, status TEXT)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS audit_log "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, agent TEXT, "
        "action TEXT, payload TEXT, timestamp TEXT)"
    )
    conn.execute(
        "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) "
        "VALUES ('TASK-001', 'dev_agent', 'create', '{}', '2026-07-12T00:00:00Z')"
    )
    conn.commit()
    conn.close()
    assert org_root.exists()
    assert "audit-org" not in state.orgs

    client = TestClient(create_app(state))
    r = client.post("/api/v1/orgs", headers=auth, json={"slug": "audit-org"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "org_dir_has_data"
    # Dir is NOT removed — protect the data.
    assert org_root.exists()
    assert "audit-org" not in state.orgs


# PART 2b-extra — dir with jobs row (zero tasks) is protected
@pytest.mark.asyncio
async def test_init_org_dir_with_jobs_row_returns_protective_409(
    tmp_path: Path, auth
) -> None:
    """A dir whose happyranch.db has a jobs row (and zero task rows)
    must return the protective 409 'org_dir_has_data'."""
    import sqlite3
    rt = RuntimeDir.init(tmp_path / "rt")
    state = DaemonState.from_runtime(rt, Settings())

    org_root = rt.orgs_dir / "jobs-org"
    _seed_pristine_skeleton(org_root)
    db_path = org_root / "happyranch.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT, brief TEXT, status TEXT)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS jobs "
        "(id TEXT PRIMARY KEY, task_id TEXT, agent_name TEXT, title TEXT, "
        "rationale TEXT, script_text TEXT, interpreter TEXT, status TEXT, "
        "review_required INTEGER, persistent INTEGER, created_at TEXT)"
    )
    conn.execute(
        "INSERT INTO jobs (id, task_id, agent_name, title, script_text, "
        "interpreter, status, review_required, persistent, created_at) "
        "VALUES ('JOB-001', 'TASK-002', 'dev_agent', 'test job', 'echo hi', "
        "'bash', 'pending', 0, 0, '2026-07-12T00:00:00Z')"
    )
    conn.commit()
    conn.close()
    assert org_root.exists()
    assert "jobs-org" not in state.orgs

    client = TestClient(create_app(state))
    r = client.post("/api/v1/orgs", headers=auth, json={"slug": "jobs-org"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "org_dir_has_data"
    assert org_root.exists()
    assert "jobs-org" not in state.orgs


# PART 2b-extra — dir with empty DB (no tasks) is reclaimable
@pytest.mark.asyncio
async def test_init_org_dir_with_empty_db_is_reclaimable(
    tmp_path: Path, auth
) -> None:
    """A dir with a happyranch.db that has zero tasks is still reclaimable."""
    import sqlite3
    rt = RuntimeDir.init(tmp_path / "rt")
    state = DaemonState.from_runtime(rt, Settings())

    org_root = rt.orgs_dir / "empty-db-org"
    _seed_pristine_skeleton(org_root)
    db_path = org_root / "happyranch.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT, brief TEXT, status TEXT)")
    conn.commit()
    conn.close()
    assert org_root.exists()
    assert "empty-db-org" not in state.orgs

    client = TestClient(create_app(state))
    r = client.post("/api/v1/orgs", headers=auth, json={"slug": "empty-db-org"})
    assert r.status_code == 200, r.text
    assert "empty-db-org" in state.orgs


# PART 2 — slug in state.orgs still 409s org_exists (healthy org guard)
@pytest.mark.asyncio
async def test_init_org_loaded_org_still_409_org_exists(
    tmp_path: Path, auth
) -> None:
    """If the slug is in state.orgs (healthy loaded org), the existing
    'org_exists' 409 is returned — we do NOT probe the dir at all."""
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_org(rt.orgs_dir / "alpha")
    state = DaemonState.from_runtime(rt, Settings())
    client = TestClient(create_app(state))
    r = client.post("/api/v1/orgs", headers=auth, json={"slug": "alpha"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "org_exists"
