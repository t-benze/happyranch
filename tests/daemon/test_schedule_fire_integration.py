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
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

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
from runtime.orchestrator.host_supervisor import (
    AdmissionController,
    HostSessionSupervisor,
    canary_policy,
)
from runtime.orchestrator.orchestrator import Orchestrator
from runtime.orchestrator.teams import TeamsRegistry
from runtime.platform.session_backend import CleanupStatus
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

    def run(self, *, workspace, prompt, session_id, timeout_seconds, **_kwargs):
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
    def run(self, *, workspace, prompt, session_id, timeout_seconds, **_kwargs):
        return _FakeResult(success=True)


class _FailingExecutor:
    """A fake executor that returns failure — the runner should mark FAULT."""
    def run(self, *, workspace, prompt, session_id, timeout_seconds, **_kwargs):
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


def _make_host_supervisor():
    """THR-207 real-caller wiring: schedule fires run through the daemon-wide
    HostSessionSupervisor (honest no-enforcement passthrough backend)."""
    from runtime.orchestrator.host_supervisor import build_default_host_supervisor
    return build_default_host_supervisor()


def _write_daemon_token(tmp_path: Path) -> None:
    """The autouse ``_isolate_canonical_store`` conftest points
    ``HAPPYRANCH_DAEMON_HOME`` at ``<tmp>/.happyranch``, which contains no
    ``daemon.token`` — so the spawn route rejects every request with
    "daemon token file missing". Write a test token so the real spawn
    callback (and the THR-207 real-producer acceptance) can run."""
    home = tmp_path / ".happyranch"
    home.mkdir(parents=True, exist_ok=True)
    (home / "daemon.token").write_text("test-daemon-token")


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
    _write_daemon_token(tmp_path)
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
        host_supervisor=_make_host_supervisor(),
    )

    # Verify schedule transitioned to FIRED
    record = db.schedules.get("SCHEDULE-001")
    assert record.status == ScheduleStatus.FIRED
    assert record.active == 0
    assert len(record.spawned_task_ids) == 1
    assert record.fire_count == 1

    # Verify transcript_path is set and transcript file exists.
    assert record.transcript_path is not None
    assert Path(record.transcript_path).exists()
    transcript_content = Path(record.transcript_path).read_text()
    assert "status: fired" in transcript_content
    assert "schedule_id: SCHEDULE-001" in transcript_content

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
        host_supervisor=_make_host_supervisor(),
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
        host_supervisor=_make_host_supervisor(),
    )

    record = db.schedules.get("SCHEDULE-002")
    assert record.status == ScheduleStatus.FAILED
    assert record.error == "executor crashed"


@pytest.mark.asyncio
async def test_schedule_weekly_fire_rearms(tmp_path):
    """Weekly schedule fire -> spawn -> re-armed with next fire_at."""
    _write_daemon_token(tmp_path)
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
        host_supervisor=_make_host_supervisor(),
    )

    record = db.schedules.get("SCHEDULE-003")
    assert record.status == ScheduleStatus.ARMED
    assert record.active == 1
    assert record.fire_count == 1
    assert len(record.spawned_task_ids) == 1

    # Verify transcript_path is set for re-armed weekly fire.
    assert record.transcript_path is not None
    assert Path(record.transcript_path).exists()
    transcript_content = Path(record.transcript_path).read_text()
    assert "status: fired" in transcript_content
    assert "schedule_id: SCHEDULE-003" in transcript_content


@pytest.mark.asyncio
async def test_schedule_weekly_fire_expired_callback_preserved(tmp_path):
    """Regression: weekly fire -> spawn -> EXPIRED (past expires_at) is NOT
    overwritten to FAILED/no_callback by run_schedule.

    The spawn callback resolves the row to EXPIRED after a successful fire
    whose next occurrence exceeds expires_at.  run_schedule must recognize
    EXPIRED as a valid callback-resolved terminal state and leave it alone."""
    _write_daemon_token(tmp_path)
    from runtime.orchestrator.schedule_rules import next_weekly_occurrence

    settings = Settings()
    db = Database(tmp_path / "test.db")
    org_dir = _setup_org(tmp_path, db)
    now = datetime.now(timezone.utc)
    recurrence = {"day": "Wed", "time": "09:00", "tz": "UTC"}
    # Expires at a time BEFORE the next weekly occurrence — the callback
    # will see past_expires_at and transition to EXPIRED.
    next_fire = next_weekly_occurrence("Wed", "09:00", "UTC", after=now)
    # Set expires_at to now (which is before next_fire).
    expires_at = now

    db.schedules.insert(ScheduleRecord(
        id="SCHEDULE-004",
        agent_name="dev_agent",
        team="engineering",
        kind=ScheduleKind.WEEKLY,
        fire_at=now - timedelta(hours=1),
        recurrence=recurrence,
        timezone="UTC",
        normalized_brief="Expiring weekly task",
        source_instruction="Run weekly, expiring",
        status=ScheduleStatus.FIRING,
        expires_at=expires_at,
        indefinite=0,
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

    rt = RuntimeDir.init(tmp_path / "rt3")
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
        schedule_id="SCHEDULE-004",
        settings=settings,
        executor_factory=lambda name, settings, paths:
            _SpawningExecutor(client, "test-org", "SCHEDULE-004"),
        host_supervisor=_make_host_supervisor(),
    )

    record = db.schedules.get("SCHEDULE-004")
    # The spawn callback should have marked it EXPIRED.
    assert record.status == ScheduleStatus.EXPIRED, (
        f"Expected EXPIRED, got {record.status}"
    )
    assert record.active == 0
    assert record.fire_count == 1
    assert len(record.spawned_task_ids) == 1

    # Verify the task was created.
    task_id = record.spawned_task_ids[0]
    task = db.get_task(task_id)
    assert task is not None
    assert task.assigned_agent == "dev_agent"
    assert task.team == "engineering"
    assert task.brief == "Expiring weekly task"

    # Verify schedule_expired audit was written.
    audit_rows = db._conn.execute(
        "SELECT * FROM audit_log WHERE task_id = ? AND action = 'schedule_expired'",
        ("SCHEDULE-004",),
    ).fetchall()
    assert len(audit_rows) >= 1, "expected schedule_expired audit row"

    # Verify schedule_spawned audit was written.
    spawn_rows = db._conn.execute(
        "SELECT * FROM audit_log WHERE task_id = ? AND action = 'schedule_spawned'",
        ("SCHEDULE-004",),
    ).fetchall()
    assert len(spawn_rows) >= 1, "expected schedule_spawned audit row"

    # Verify schedule_completed audit was written.
    comp_rows = db._conn.execute(
        "SELECT * FROM audit_log WHERE task_id = ? AND action = 'schedule_completed'",
        ("SCHEDULE-004",),
    ).fetchall()
    assert len(comp_rows) >= 1, "expected schedule_completed audit row"

    # Verify no schedule_failed row was written.
    failed_rows = db._conn.execute(
        "SELECT * FROM audit_log WHERE task_id = ? AND action = 'schedule_failed'",
        ("SCHEDULE-004",),
    ).fetchall()
    assert len(failed_rows) == 0, "schedule_failed must not be written for a successful expired fire"

    # Verify the schedule_fired audit (start of lifecycle) was also written.
    fired_rows = db._conn.execute(
        "SELECT * FROM audit_log WHERE task_id = ? AND action = 'schedule_fired'",
        ("SCHEDULE-004",),
    ).fetchall()
    assert len(fired_rows) >= 1, "expected schedule_fired audit row"

    # Verify transcript_path is set and the transcript file exists on disk.
    assert record.transcript_path is not None, (
        "transcript_path must be set for expired weekly fire"
    )
    transcript_file = Path(record.transcript_path)
    assert transcript_file.exists(), (
        f"transcript file must exist: {record.transcript_path}"
    )
    transcript_content = transcript_file.read_text()
    # Frontmatter must reflect expired — not fired — for terminal expired.
    assert "status: expired" in transcript_content, (
        f"transcript frontmatter must show status=expired, got:\n{transcript_content}"
    )
    assert "schedule_id: SCHEDULE-004" in transcript_content
    assert "agent_name: dev_agent" in transcript_content
    assert "spawned_task_ids:" in transcript_content


# ── Issue #568: AgentDef.model forwarding to executor.run ──────────────

@pytest.mark.asyncio
async def test_run_schedule_forwards_model_to_executor_run(tmp_path):
    """When AgentDef.model is set, schedule runner passes it to executor.run(model=...)."""
    from runtime.daemon.org_state import OrgState
    from runtime.orchestrator._paths import OrgPaths as _OrgPaths
    db = Database(tmp_path / "hr.db")
    agents_dir = tmp_path / "org" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "dev_agent.md").write_text(
        "---\nname: dev_agent\nteam: engineering\nrole: worker\n"
        "executor: claude\nmodel: gpt-5.6-terra\n---\n\n"
        "You are a test agent.\n"
    )
    (tmp_path / "workspaces" / "dev_agent").mkdir(parents=True)
    sched = ScheduleRecord(
        id="SCHEDULE-MODEL-1",
        agent_name="dev_agent",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=datetime(2026, 6, 15, 1, 0, tzinfo=timezone.utc),
        timezone="Asia/Shanghai",
        normalized_brief="Test scheduled task",
        source_instruction="Test source instruction",
        status=ScheduleStatus.FIRING,
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
    )
    db.schedules.insert(sched)
    captured_model = {}

    class _CapturingExec:
        def run(self, **kwargs):
            captured_model["model"] = kwargs.get("model")
            return _FakeResult()

    def factory(executor_name, settings, paths):
        return _CapturingExec()

    org_state = OrgState(
        db=db, root=tmp_path, slug="test",
        teams=MagicMock(), settings=Settings(), orchestrator=MagicMock(),
    )
    await run_schedule(
        org_state=org_state,
        schedule_id="SCHEDULE-MODEL-1",
        settings=Settings(),
        executor_factory=factory,
        host_supervisor=_make_host_supervisor(),
    )

    assert captured_model.get("model") == "gpt-5.6-terra", (
        f"expected model='gpt-5.6-terra', got {captured_model.get('model')!r}"
    )


@pytest.mark.asyncio
async def test_run_schedule_refreshes_repos_before_executor_run(tmp_path, monkeypatch):
    from runtime.daemon.org_state import OrgState

    db = Database(tmp_path / "hr.db")
    agents_dir = tmp_path / "org" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "dev_agent.md").write_text(_AGENT_FILE)
    workspace = tmp_path / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    db.schedules.insert(ScheduleRecord(
        id="SCHEDULE-REFRESH", agent_name="dev_agent", kind=ScheduleKind.ONE_SHOT,
        fire_at=datetime(2026, 6, 15, 1, 0, tzinfo=timezone.utc),
        timezone="Asia/Shanghai", normalized_brief="Test scheduled task",
        source_instruction="Test source instruction", status=ScheduleStatus.FIRING,
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
    ))
    events: list[str] = []
    import runtime.daemon.schedule_runner as runner_mod
    monkeypatch.setattr(
        runner_mod, "refresh_workspace_repos",
        lambda ws: (events.append("refresh_workspace_repos"), {"happyranch": True})[1],
    )

    class _Executor:
        def run(self, **_kwargs):
            events.append("executor.run")
            return _FakeResult()

    org_state = OrgState(
        db=db, root=tmp_path, slug="test", teams=MagicMock(),
        settings=Settings(), orchestrator=MagicMock(),
    )
    await run_schedule(
        org_state=org_state, schedule_id="SCHEDULE-REFRESH", settings=Settings(),
        executor_factory=lambda *_args, **_kwargs: _Executor(),
        host_supervisor=_make_host_supervisor(),
    )

    assert events == ["refresh_workspace_repos", "executor.run"]


@pytest.mark.asyncio
async def test_run_schedule_no_model_preserves_default_behavior(tmp_path):
    """When AgentDef.model is absent, schedule runner passes model=None."""
    from runtime.daemon.org_state import OrgState
    db = Database(tmp_path / "hr.db")
    agents_dir = tmp_path / "org" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "dev_agent.md").write_text(
        "---\nname: dev_agent\nteam: engineering\nrole: worker\n"
        "executor: claude\n---\n\n"
        "You are a test agent.\n"
    )
    (tmp_path / "workspaces" / "dev_agent").mkdir(parents=True)
    sched = ScheduleRecord(
        id="SCHEDULE-MODEL-2",
        agent_name="dev_agent",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=datetime(2026, 6, 15, 1, 0, tzinfo=timezone.utc),
        timezone="Asia/Shanghai",
        normalized_brief="Test scheduled task",
        source_instruction="Test source instruction",
        status=ScheduleStatus.FIRING,
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
    )
    db.schedules.insert(sched)
    captured_model = {}

    class _CapturingExec:
        def run(self, **kwargs):
            captured_model["model"] = kwargs.get("model")
            return _FakeResult()

    def factory(executor_name, settings, paths):
        return _CapturingExec()

    from unittest.mock import MagicMock
    org_state = OrgState(
        db=db, root=tmp_path, slug="test",
        teams=MagicMock(), settings=Settings(), orchestrator=MagicMock(),
    )
    await run_schedule(
        org_state=org_state,
        schedule_id="SCHEDULE-MODEL-2",
        settings=Settings(),
        executor_factory=factory,
        host_supervisor=_make_host_supervisor(),
    )

    assert captured_model.get("model") is None, (
        f"model should be None when AgentDef has no model, "
        f"got {captured_model.get('model')!r}"
    )


# ── THR-207 real-producer acceptance: schedule fires through the wired
# ── HostSessionSupervisor ──────────────────────────────────────────────────


class _RecordingSpawnExecutor:
    """Fake schedule executor that records whether it was invoked and, when
    ``spawn=True``, calls the real ``schedules spawn`` callback through the
    TestClient (the full end-to-end fire path)."""

    def __init__(self, client, slug: str, schedule_id: str, *, spawn: bool = False,
                 entered: threading.Event | None = None,
                 release: threading.Event | None = None) -> None:
        self._client = client
        self._slug = slug
        self._schedule_id = schedule_id
        self._spawn = spawn
        self.entered = entered
        self.release = release
        self.called = 0

    def run(self, *, workspace, prompt, session_id, timeout_seconds, **_kwargs):
        self.called += 1
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            # The body is blocked mid-flight (like a real communicate loop);
            # the fake backend's finish() releases it (teardown unblocks the
            # loop). Returns success, exactly like a fire whose spawn
            # callback never fired -> run_schedule marks no_callback.
            self.release.wait(timeout=10)
        if self._spawn:
            resp = self._client.post(
                f"/api/v1/orgs/{self._slug}/schedules/{self._schedule_id}/spawn",
                json={"summary": "Dispatched the scheduled task."},
            )
            assert resp.status_code == 200, resp.text
        return _FakeResult(success=True)


class _ReleaseOnFinishBackend:
    """Fake SessionBackend whose ``finish`` releases the executor body —
    the deterministic stand-in for "containment teardown unblocks the
    communicate loop" (the wired supervisor runs with this backend injected
    so the acceptance test controls the launch/finish transitions)."""

    def __init__(self, release: threading.Event | None = None) -> None:
        self.release = release
        self.calls = {"prepare": 0, "launch": 0, "finish": 0, "abandon": 0}
        self.finish_reasons: list[str] = []
        self.launched_pid = 0

    def probe(self):
        from runtime.platform.session_backend import CapabilityReport
        return CapabilityReport(backend="acceptance", backend_version="1.0", capabilities={})

    def prepare(self, request, policy):
        self.calls["prepare"] += 1
        from runtime.platform.session_backend import PendingHandle
        return PendingHandle(backend="acceptance", token="acc-1", request_id=request.logical_id)

    def launch(self, pending, spec):
        self.calls["launch"] += 1
        from runtime.platform.session_backend import RunningHandle
        self.launched_pid = 4242 + self.calls["launch"]
        return RunningHandle(
            backend="acceptance", token=pending.token, request_id=pending.request_id,
            root_pid=self.launched_pid, start_identity="boot-1", process=None,
        )

    def finish(self, running, terminal_reason, grace_seconds, samples=None):
        self.calls["finish"] += 1
        self.finish_reasons.append(terminal_reason)
        if self.release is not None:
            self.release.set()
        from runtime.platform.session_backend import Receipt
        return Receipt(
            backend="acceptance", terminal_reason=terminal_reason,
            cleanup_status=CleanupStatus.CLEAN, cleanup_duration_seconds=0.1,
            quiescent=True, wall_time_seconds=1.0,
        )

    def abandon(self, pending):
        self.calls["abandon"] += 1

    def sample(self, running):
        from runtime.platform.session_backend import ResourceSample
        return ResourceSample(sampled_at=time.monotonic())

    def recover(self, handle_token):
        from runtime.platform.session_backend import RecoveryResult
        return RecoveryResult(recovered=False, evidence="fake")


async def _wait_for_event(event, timeout: float = 8.0) -> None:
    """Poll a threading.Event with yields so the asyncio loop keeps running
    (a blocking ``Event.wait`` on the loop thread starves the tasks under
    test)."""
    deadline = time.monotonic() + timeout
    while not event.is_set() and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    assert event.is_set(), "event not set in time"


class _PausingAdmission(AdmissionController):
    """Deterministic grant-to-first-gate window for the real producer: the
    lease is granted (ownership transferred) and the run pauses before the
    attempt's first gate, so a real ``supervisor.shutdown()`` lands in the
    window the reviewer found (TASK-5600 [HIGH])."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.granted = threading.Event()
        self.release = threading.Event()

    def acquire(self, request, timeout=None):
        lease = super().acquire(request, timeout=timeout)
        if lease is not None:
            self.granted.set()
            assert self.release.wait(timeout=10)
        return lease


@pytest.mark.asyncio
async def test_schedule_fire_acceptance_normal_path_releases_lease_exactly_once(tmp_path):
    """Real producer acceptance: a schedule fire runs through the wired
    HostSessionSupervisor end to end (admission -> launch body -> spawn
    callback -> terminal). One bounded receipt publishes and the admission
    lease releases exactly once; the ownership registry is empty."""
    _write_daemon_token(tmp_path)
    settings = Settings()
    db = Database(tmp_path / "test.db")
    org_dir = _setup_org(tmp_path, db)
    _insert_one_shot(db, "SCHEDULE-ACC-1")

    from runtime.daemon.org_state import OrgState
    teams = TeamsRegistry.load(org_dir)
    org_state = OrgState(
        slug="test-org", root=org_dir, db=db, teams=teams,
        settings=settings, orchestrator=_FakeOrch(),
    )
    from runtime.daemon.app import create_app
    from fastapi.testclient import TestClient
    from runtime.daemon.state import DaemonState
    from runtime.runtime import RuntimeDir
    from runtime.daemon import paths as daemon_paths
    rt = RuntimeDir.init(tmp_path / "rt")
    state = DaemonState.from_runtime(rt, settings)
    state.orgs["test-org"] = org_state
    state.queue._running = True
    client = TestClient(create_app(state), base_url="http://testserver")
    client.headers.update({"Authorization": f"Bearer {daemon_paths.read_token()}"})

    receipts = []
    backend = _ReleaseOnFinishBackend()
    supervisor = HostSessionSupervisor(
        backend=backend, policy=canary_policy(), publisher=receipts.append,
    )
    exec_factory = lambda name, settings, paths: _RecordingSpawnExecutor(
        client, "test-org", "SCHEDULE-ACC-1", spawn=True,
    )
    await run_schedule(
        org_state=org_state, schedule_id="SCHEDULE-ACC-1", settings=settings,
        executor_factory=exec_factory, host_supervisor=supervisor,
    )

    record = db.schedules.get("SCHEDULE-ACC-1")
    assert record.status == ScheduleStatus.FIRED
    # The fire went through the supervisor: exactly one lease released, the
    # ownership registry is empty (no leaked active registration), and one
    # bounded receipt published for the successful fire.
    assert supervisor._admission.released_total() == 1
    assert supervisor._admission.active_count() == 0
    assert supervisor.active_count() == 0
    assert len(receipts) == 1
    assert receipts[0].terminal_reason == "success"
    assert backend.calls["launch"] == 1
    assert backend.calls["finish"] == 1


@pytest.mark.asyncio
async def test_schedule_fire_acceptance_shutdown_post_grant_never_launches(tmp_path):
    """Real producer acceptance: the daemon drain fires between the admission
    grant and the attempt's first gate (the TASK-5600 [HIGH] window). The
    fire never launches the executor, the row is left FIRING for daemon-
    restart recovery, and the lease releases exactly once with no receipt."""
    settings = Settings()
    db = Database(tmp_path / "test.db")
    org_dir = _setup_org(tmp_path, db)
    _insert_one_shot(db, "SCHEDULE-ACC-2")

    from runtime.daemon.org_state import OrgState
    teams = TeamsRegistry.load(org_dir)
    org_state = OrgState(
        slug="test-org", root=org_dir, db=db, teams=teams,
        settings=settings, orchestrator=_FakeOrch(),
    )

    executor_box = {}
    exec_factory = lambda name, settings, paths: (
        executor_box.setdefault("exec", _RecordingSpawnExecutor(
            None, "test-org", "SCHEDULE-ACC-2", spawn=False,
        ))
    )
    receipts = []
    admission = _PausingAdmission(cap=8, monotonic=time.monotonic)
    supervisor = HostSessionSupervisor(
        backend=_ReleaseOnFinishBackend(),
        policy=canary_policy(),
        publisher=receipts.append,
        admission=admission,
    )

    async def _fire():
        await run_schedule(
            org_state=org_state, schedule_id="SCHEDULE-ACC-2", settings=settings,
            executor_factory=exec_factory, host_supervisor=supervisor,
        )

    task = asyncio.create_task(_fire())
    # Poll with yields — a blocking Event.wait() on the loop thread would
    # starve the task itself.
    await _wait_for_event(admission.granted)
    supervisor.shutdown()  # the real daemon drain
    admission.release.set()
    await asyncio.wait_for(task, timeout=10)

    record = db.schedules.get("SCHEDULE-ACC-2")
    # Pre-launch shutdown winner: nothing ran; the row stays FIRING for
    # daemon-restart recovery (recover_firing) — identical to a shutdown
    # that cancels the worker mid-run pre-wiring.
    assert record.status == ScheduleStatus.FIRING
    assert supervisor._admission.released_total() == 1
    assert supervisor._admission.active_count() == 0
    assert supervisor.active_count() == 0
    assert receipts == []
    # The executor never launched, and no handle was prepared (the ownership
    # gate refused before prepare).
    assert executor_box["exec"].called == 0


@pytest.mark.asyncio
async def test_schedule_fire_acceptance_shutdown_mid_body_finishes_exactly_once(tmp_path):
    """Real producer acceptance: the daemon drain lands while the fire's
    executor body is in flight. The durable SHUTDOWN winner freezes
    first-wins; containment (backend.finish) runs exactly once with reason
    ``shutdown`` and releases the body; one receipt publishes; the lease
    releases exactly once; the row reaches its normal no_callback terminal
    (the fire ran but never spawned)."""
    settings = Settings()
    db = Database(tmp_path / "test.db")
    org_dir = _setup_org(tmp_path, db)
    _insert_one_shot(db, "SCHEDULE-ACC-3")

    from runtime.daemon.org_state import OrgState
    teams = TeamsRegistry.load(org_dir)
    org_state = OrgState(
        slug="test-org", root=org_dir, db=db, teams=teams,
        settings=settings, orchestrator=_FakeOrch(),
    )

    entered = threading.Event()
    release = threading.Event()
    receipts = []
    backend = _ReleaseOnFinishBackend(release=release)
    supervisor = HostSessionSupervisor(
        backend=backend, policy=canary_policy(), publisher=receipts.append,
    )
    exec_factory = lambda name, settings, paths: _RecordingSpawnExecutor(
        None, "test-org", "SCHEDULE-ACC-3", spawn=False,
        entered=entered, release=release,
    )

    async def _fire():
        await run_schedule(
            org_state=org_state, schedule_id="SCHEDULE-ACC-3", settings=settings,
            executor_factory=exec_factory, host_supervisor=supervisor,
        )

    task = asyncio.create_task(_fire())
    await _wait_for_event(entered)  # the executor body is in flight
    supervisor.shutdown()  # the real daemon drain
    await asyncio.wait_for(task, timeout=10)

    record = db.schedules.get("SCHEDULE-ACC-3")
    # The body ran (no spawn callback) -> run_schedule's normal no_callback
    # transition; the supervisor's containment finished exactly once with
    # the durable SHUTDOWN winner.
    assert record.status == ScheduleStatus.FAILED
    assert record.error == "no_callback"
    assert backend.finish_reasons == ["shutdown"]
    assert len(receipts) == 1
    assert receipts[0].terminal_reason == "shutdown"
    assert supervisor._admission.released_total() == 1
    assert supervisor._admission.active_count() == 0
    assert supervisor.active_count() == 0
