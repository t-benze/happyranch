"""In-process schedule fire integration: fake-executor schedule fire ->
schedules spawn -> spawned root task runs through the run_step loop, with
schedule token usage under the schedule scope.

This exercises the real runner (``run_schedule``), the real callback route
(``/schedules/{id}/spawn``), real server-side task creation, and the real
run_step loop together. It is deterministic (no wall-clock scheduler, no
subprocess daemon) so it is reliable under the session-timeout budget.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.daemon.queue import TaskQueue
from runtime.daemon.schedule_runner import run_schedule
from runtime.infrastructure.database import Database
from runtime.models import (
    ScheduleKind,
    ScheduleRecord,
    ScheduleStatus,
    TaskRecord,
    TaskStatus,
    TokenUsage,
)
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.orchestrator import Orchestrator
from runtime.orchestrator.teams import TeamsRegistry
from runtime.runtime import RuntimeDir

pytestmark = pytest.mark.integration

_AGENT_FILE = (
    "---\nname: dev_agent\nteam: engineering\nrole: worker\nexecutor: claude\n---\n\n"
    "You are a developer.\n"
)


class _FakeResult:
    def __init__(self, success: bool = True, error: str | None = None) -> None:
        self.success = success
        self.token_usage = TokenUsage(
            input_tokens=100, output_tokens=40, model="claude-opus",
        )
        self.agent_session_id = "sess-schedule-1"
        self.session_id = "sess-schedule-1"
        self.error = error


class _SpawningExecutor:
    """A fake schedule executor: instead of an LLM, it performs the schedule's
    one job — calling ``schedules spawn`` once — then returns a successful result
    with token usage (which run_schedule records under the schedule scope)."""

    def __init__(self, client, slug: str, schedule_id: str) -> None:
        self._client = client
        self._slug = slug
        self._schedule_id = schedule_id

    def run(self, *, workspace, prompt, session_id, timeout_seconds):
        assert "Schedule Fire" in prompt
        assert self._schedule_id in prompt
        resp = self._client.post(
            f"/api/v1/orgs/{self._slug}/schedules/{self._schedule_id}/spawn",
            json={"summary": "Dispatched the scheduled task."},
        )
        assert resp.status_code == 200, resp.text
        return _FakeResult()


class _NoCallbackExecutor:
    """A fake executor that exits successfully without calling the spawn
    callback — the runner should mark the schedule FAILED/no_callback."""
    def run(self, *, workspace, prompt, session_id, timeout_seconds):
        return _FakeResult(success=True)


class _FailingExecutor:
    """A fake executor that returns failure — the runner should mark FAULT."""
    def run(self, *, workspace, prompt, session_id, timeout_seconds):
        return _FakeResult(success=False, error="executor crashed")


class _FakeOrch:
    """Minimal fake orchestrator for integration tests."""
    def attach_queue(self, q):
        self._queue = q

    def attach_sessions(self, s):
        pass

    def attach_thread_queue(self, q, loop):
        pass


def _setup_org(tmp_path: Path, db: Database) -> tuple:
    """Set up a minimal org with an agent file and workspace."""
    org_dir = tmp_path / "orgs" / "test-org"
    org_dir.mkdir(parents=True)
    agents_dir = org_dir / "org" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "dev_agent.md").write_text(_AGENT_FILE)
    (org_dir / "org" / "teams.yaml").write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: engineering_head\n"
        "    workers: [dev_agent, qa_engineer]\n"
    )
    (org_dir / "workspaces" / "dev_agent").mkdir(parents=True)
    (org_dir / "org" / "config.yaml").write_text("timezone: UTC\n")
    return org_dir


def _insert_one_shot(db: Database, schedule_id: str, status=ScheduleStatus.FIRING) -> None:
    now = datetime.now(timezone.utc)
    db.schedules.insert(ScheduleRecord(
        id=schedule_id,
        agent_name="dev_agent",
        team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=now - timedelta(minutes=5),
        timezone="UTC",
        normalized_brief="Test brief from integration",
        source_instruction="Test source instruction",
        status=status,
    ))


def _assert_task_created(db: Database, brief: str, agent: str, team: str) -> str:
    tasks = db.list_tasks(limit=10)
    for task in tasks:
        if task.agent == agent:
            return task.id
    raise AssertionError(f"no task found for agent {agent}")


# ── integration tests ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_schedule_fire_creates_task_via_spawn(tmp_path, monkeypatch):
    """Full integration: schedule fire -> spawn callback -> task created."""
    settings = Settings()
    db = Database(tmp_path / "test.db")
    org_dir = _setup_org(tmp_path, db)
    _insert_one_shot(db, "SCHEDULE-001")

    from runtime.daemon.org_state import OrgState
    teams = TeamsRegistry.load(org_dir)
    fake_orch = _FakeOrch()

    org_state = OrgState(
        slug="test-org",
        root=org_dir,
        db=db,
        teams=teams,
        settings=settings,
        orchestrator=fake_orch,
    )

    from runtime.daemon.app import create_app
    from fastapi.testclient import TestClient
    from runtime.daemon.state import DaemonState
    from runtime.runtime import RuntimeDir
    from runtime.daemon import paths as daemon_paths

    # Create a minimal runtime so DaemonState.is_idle is False.
    rt = RuntimeDir.init(tmp_path / "rt")
    state = DaemonState.from_runtime(rt, settings)
    state.orgs["test-org"] = org_state
    state.queue._running = True
    app = create_app(state)
    client = TestClient(app, base_url="http://testserver")
    client.headers.update(
        {"Authorization": f"Bearer {daemon_paths.read_token()}"}
    )

    # Run the schedule with a spawning executor
    exec_factory = lambda *args, **kwargs: None
    await run_schedule(
        org_state=org_state,
        schedule_id="SCHEDULE-001",
        settings=settings,
        executor_factory=lambda name, settings, paths:
            _SpawningExecutor(client, "test-org", "SCHEDULE-001"),
    )

    # Verify schedule transitioned to FIRED
    record = db.schedules.get("SCHEDULE-001")
    assert record.status == ScheduleStatus.FIRED
    assert record.active == 0
    assert len(record.spawned_task_ids) == 1
    assert record.fire_count == 1

    # Verify task was created
    task_id = record.spawned_task_ids[0]
    task = db.get_task(task_id)
    assert task is not None
    assert task.assigned_agent == "dev_agent"
    assert task.team == "engineering"
    assert task.brief == "Test brief from integration"

    # Verify token usage was recorded with scope_type="schedule"
    # (Check directly through the db connection)
    token_rows = db._conn.execute(
        "SELECT * FROM session_token_usage WHERE scope_type = 'schedule'"
    ).fetchall()
    assert len(token_rows) == 1
    assert token_rows[0]["scope_id"] == "SCHEDULE-001"


@pytest.mark.asyncio
async def test_schedule_no_callback_marks_failed(tmp_path):
    """When the executor exits 0 without calling spawn, the schedule is FAILED."""
    settings = Settings()
    db = Database(tmp_path / "test.db")
    org_dir = _setup_org(tmp_path, db)
    _insert_one_shot(db, "SCHEDULE-001")

    from runtime.daemon.org_state import OrgState
    teams = TeamsRegistry.load(org_dir)
    fake_orch = _FakeOrch()

    org_state = OrgState(
        slug="test-org",
        root=org_dir,
        db=db,
        teams=teams,
        settings=settings,
        orchestrator=fake_orch,
    )

    await run_schedule(
        org_state=org_state,
        schedule_id="SCHEDULE-001",
        settings=settings,
        executor_factory=lambda name, settings, paths: _NoCallbackExecutor(),
    )

    record = db.schedules.get("SCHEDULE-001")
    assert record.status == ScheduleStatus.FAILED
    assert record.error == "no_callback"
    assert len(record.spawned_task_ids) == 0


@pytest.mark.asyncio
async def test_schedule_executor_failure_marks_failed(tmp_path):
    """When the executor returns failure, the schedule is FAILED."""
    settings = Settings()
    db = Database(tmp_path / "test.db")
    org_dir = _setup_org(tmp_path, db)
    _insert_one_shot(db, "SCHEDULE-002")

    from runtime.daemon.org_state import OrgState
    teams = TeamsRegistry.load(org_dir)
    fake_orch = _FakeOrch()

    org_state = OrgState(
        slug="test-org",
        root=org_dir,
        db=db,
        teams=teams,
        settings=settings,
        orchestrator=fake_orch,
    )

    await run_schedule(
        org_state=org_state,
        schedule_id="SCHEDULE-002",
        settings=settings,
        executor_factory=lambda name, settings, paths: _FailingExecutor(),
    )

    record = db.schedules.get("SCHEDULE-002")
    assert record.status == ScheduleStatus.FAILED
    assert record.error == "executor crashed"


@pytest.mark.asyncio
async def test_schedule_weekly_fire_rearms(tmp_path):
    """Weekly schedule fire -> spawn -> re-armed with next fire_at."""
    from runtime.orchestrator.schedule_rules import next_weekly_occurrence

    settings = Settings()
    db = Database(tmp_path / "test.db")
    org_dir = _setup_org(tmp_path, db)
    now = datetime.now(timezone.utc)
    recurrence = {"day": "Wed", "time": "09:00", "tz": "UTC"}
    next_fire = next_weekly_occurrence("Wed", "09:00", "UTC", after=now)

    db.schedules.insert(ScheduleRecord(
        id="SCHEDULE-003",
        agent_name="dev_agent",
        team="engineering",
        kind=ScheduleKind.WEEKLY,
        fire_at=now - timedelta(hours=1),
        recurrence=recurrence,
        timezone="UTC",
        normalized_brief="Weekly task",
        source_instruction="Run weekly",
        status=ScheduleStatus.FIRING,
        expires_at=None,
        indefinite=1,
    ))

    from runtime.daemon.org_state import OrgState
    teams = TeamsRegistry.load(org_dir)
    fake_orch = _FakeOrch()

    org_state = OrgState(
        slug="test-org",
        root=org_dir,
        db=db,
        teams=teams,
        settings=settings,
        orchestrator=fake_orch,
    )

    from runtime.daemon.app import create_app
    from fastapi.testclient import TestClient
    from runtime.daemon.state import DaemonState
    from runtime.daemon import paths as daemon_paths
    from runtime.runtime import RuntimeDir

    rt = RuntimeDir.init(tmp_path / "rt2")
    state = DaemonState.from_runtime(rt, settings)
    state.orgs["test-org"] = org_state
    state.queue._running = True
    app = create_app(state)
    client = TestClient(app, base_url="http://testserver")
    client.headers.update(
        {"Authorization": f"Bearer {daemon_paths.read_token()}"}
    )

    await run_schedule(
        org_state=org_state,
        schedule_id="SCHEDULE-003",
        settings=settings,
        executor_factory=lambda name, settings, paths:
            _SpawningExecutor(client, "test-org", "SCHEDULE-003"),
    )

    record = db.schedules.get("SCHEDULE-003")
    assert record.status == ScheduleStatus.ARMED
    assert record.active == 1
    assert record.fire_count == 1
    assert len(record.spawned_task_ids) == 1
