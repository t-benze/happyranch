"""Production-path tests for system-contract materialization hardening (TASK-2511, REVISE TASK-2525).

Covers ALL FOUR session contexts (task / thread / wake / dream) through their
actual production entry points, not just the helper function. An empty
post-redeploy workspace must STOP before executor spawn and surface the
explicit recoverable error — never a bare Errno 2, never a silent pass.

Findings 4 from code_reviewer TASK-2520: the prior suite only exercised
``ensure_system_contracts_materialized`` directly; the real spawn paths
(thread/dream) were broken by try/except pass wrappers that swallowed
the error. These tests prove the production paths.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from runtime.daemon.dream_runner import run_dream
from runtime.daemon.schedule_runner import run_schedule
from runtime.daemon.thread_runner import run_invocation
from runtime.daemon.wake_runner import run_wake
from runtime.models import (
    DreamRecord,
    DreamStatus,
    ScheduleKind,
    ScheduleRecord,
    ScheduleStatus,
    ThreadInvocationPurpose,
    ThreadInvocationStatus,
    ThreadMessageKind,
    ThreadRecord,
    WorkHourMode,
    WorkHourRecord,
    WorkHourStatus,
)
from runtime.config import Settings


@pytest.fixture(autouse=True)
def _seed_active_agents_for_system_contract_production_paths(org_state):
    """Task/thread/wake/dream launch is fail-closed: an active AgentDef is required.

    Legacy tests created only workspaces. Seed active frontmatter for the
    agents used in this module so launch guards admit them.
    """
    from runtime.orchestrator._paths import OrgPaths
    from tests.conftest import seed_test_agents
    seed_test_agents(
        OrgPaths(root=org_state.root),
        ("engineering_head", "content_manager", "dev_agent"),
    )


# ── Thread production path ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_thread_spawn_stops_on_materialization_error(org_state, tmp_path, monkeypatch):
    """run_invocation must fail BEFORE spawning the executor when
    materialization of workspace skills raises.

    Under the canonical store model, the correct unit seam for inducing
    a materialization failure is to inject an explicit
    SymlinkMaterializationError into the SymlinkMaterializer, not to rely
    on an empty protocol/skills/ directory (which the production code now
    skips with continue)."""
    db = org_state.db
    db.insert_thread(ThreadRecord(id="THR-001", subject="test"))
    db.add_thread_participant("THR-001", "dev_agent", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hello",
    )
    inv = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="dev_agent",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    # GitHub #688 Slice B: seed the delivery-state queued slot so the runner's
    # queued→running CAS succeeds (mirrors a queued coalesced wake).
    db._conn.execute(
        "INSERT INTO thread_reply_delivery_state "
        "(thread_id, agent_name, acknowledged_through_seq, required_through_seq, "
        "queued_invocation_token, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("THR-001", "dev_agent", 0, 1, inv.invocation_token,
         "2026-01-01T00:00:00+00:00"),
    )
    db._conn.commit()

    # Workspace setup: agent.yaml + repos so executor resolution works.
    ws = org_state.root / "workspaces" / "dev_agent"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "agent.yaml").write_text("executor: claude\n")
    (ws / "repos" / "test" / ".git").mkdir(parents=True, exist_ok=True)

    # Create source skill dirs so the canonical build has real content.
    settings = Settings(project_root=tmp_path)
    proto_skills = tmp_path / "protocol" / "skills"
    proto_skills.mkdir(parents=True, exist_ok=True)
    _make_skill_dir(proto_skills, "start-task")
    _make_skill_dir(proto_skills, "jobs")
    _make_skill_dir(proto_skills, "make-worktree")
    _make_skill_dir(proto_skills, "thread")

    # Inject explicit materialization error — canonical store builds
    # will succeed, but the symlink materializer will raise.
    from runtime.skills.symlink_materializer import (
        SymlinkMaterializer,
        SymlinkMaterializationError,
    )
    _orig_materialize = SymlinkMaterializer.materialize_skill

    def _failing_materialize(self, skill_slug, version, content_hash,
                             workspace, skills_subdir, **kwargs):
        raise SymlinkMaterializationError(
            "injected_failure",
            f"Injected materialization failure for {skill_slug}",
        )

    monkeypatch.setattr(SymlinkMaterializer, "materialize_skill", _failing_materialize)

    executor_spawned = False

    class _FakeExec:
        def __init__(self, **kwargs):
            pass

        def run(self, **kwargs):
            nonlocal executor_spawned
            executor_spawned = True
            from runtime.orchestrator.executors import ExecutorResult
            return ExecutorResult(success=True, duration_seconds=0, session_id="fake")

    # Replace executor factory so we can DETECT if spawn was attempted.
    monkeypatch.setattr(
        "runtime.daemon.thread_runner._build_executor_for_provider",
        lambda provider, s, paths: _FakeExec(),
    )

    await run_invocation(
        org_state=org_state,
        invocation_token=inv.invocation_token,
        settings=settings,
    )

    # Executor must NOT have been spawned.
    assert not executor_spawned, (
        "executor was spawned despite injected materialization error"
    )

    # Invocation must be marked FAILED.
    inv_after = db.get_invocation_any_status(inv.invocation_token)
    assert inv_after.status == ThreadInvocationStatus.FAILED, (
        f"expected FAILED, got {inv_after.status}"
    )
    # The error message names the materialization failure.
    reason = inv_after.decline_reason or ""
    assert "materialization" in reason.lower(), (
        f"decline_reason should mention materialization: {reason!r}"
    )


# ── Dream production path ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dream_spawn_stops_on_materialization_error(org_state, tmp_path, monkeypatch):
    """run_dream must fail BEFORE spawning the executor when
    materialization of workspace skills raises."""
    db = org_state.db

    def _dt(hour: int) -> datetime:
        return datetime(2026, 6, 9, hour, 0, tzinfo=timezone.utc)

    db.insert_dream(DreamRecord(
        id="DREAM-001",
        agent_name="dev_agent",
        local_date="2026-06-09",
        scheduled_for=_dt(2),
        window_start=_dt(1),
        window_end=_dt(2),
    ))
    ws = org_state.root / "workspaces" / "dev_agent"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "agent.yaml").write_text("executor: claude\n")
    (ws / "repos" / "test" / ".git").mkdir(parents=True, exist_ok=True)

    # Create source skill dirs so canonical store builds succeed.
    settings = Settings(project_root=tmp_path)
    proto_skills = tmp_path / "protocol" / "skills"
    proto_skills.mkdir(parents=True, exist_ok=True)
    _make_skill_dir(proto_skills, "jobs")
    _make_skill_dir(proto_skills, "make-worktree")
    _make_skill_dir(proto_skills, "dream")

    # Inject explicit materialization error.
    from runtime.skills.symlink_materializer import (
        SymlinkMaterializer,
        SymlinkMaterializationError,
    )

    def _failing_materialize(self, skill_slug, version, content_hash,
                             workspace, skills_subdir, **kwargs):
        raise SymlinkMaterializationError(
            "injected_failure",
            f"Injected materialization failure for {skill_slug}",
        )

    monkeypatch.setattr(SymlinkMaterializer, "materialize_skill", _failing_materialize)

    executor_spawned = False

    class _FakeExec:
        def __init__(self, **kwargs):
            pass

        def run(self, **kwargs):
            nonlocal executor_spawned
            executor_spawned = True
            from runtime.orchestrator.executors import ExecutorResult
            return ExecutorResult(success=True, duration_seconds=0, session_id="fake")

    await run_dream(
        org_state=org_state,
        dream_id="DREAM-001",
        settings=settings,
        executor_factory=lambda *a, **kw: _FakeExec(),
    )

    assert not executor_spawned, (
        "executor was spawned despite injected materialization error"
    )

    dream = db.get_dream("DREAM-001")
    assert dream.status == DreamStatus.FAILED, (
        f"expected FAILED, got {dream.status}"
    )
    assert "materialization" in (dream.error or "").lower(), (
        f"error should mention materialization: {dream.error!r}"
    )


# ── Wake production path ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wake_spawn_succeeds_when_contracts_present(org_state, tmp_path, monkeypatch):
    """run_wake with contracts present MUST succeed (executor is spawned).
    This is the positive case — wake already calls ensure_system_contracts_materialized
    without a try/except wrapper, so it's a hard precondition."""
    db = org_state.db
    now = datetime.now(timezone.utc)
    db.work_hours.insert(WorkHourRecord(
        id="WH-001",
        agent_name="dev_agent",
        local_date="2026-06-09",
        slot="09:00",
        mode=WorkHourMode.WINDOWED,
        scheduled_for=now,
        window_start=now,
        window_end=now,
        status=WorkHourStatus.PENDING,
        routine_count=1,
    ))

    # Agent definition file needed by load_agent()
    (org_state.root / "org" / "agents").mkdir(parents=True, exist_ok=True)
    (org_state.root / "org" / "agents" / "dev_agent.md").write_text(
        "---\n"
        "name: dev_agent\n"
        "team: engineering\n"
        "role: worker\n"
        "executor: claude\n"
        "---\n\n"
        "## Routine Tasks\n\n"
        "- Run routine check.\n"
    )

    ws = org_state.root / "workspaces" / "dev_agent"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "agent.yaml").write_text("executor: claude\n")
    (ws / "repos" / "test" / ".git").mkdir(parents=True, exist_ok=True)

    # Create protocol/skills/ with real contracts so materialization succeeds.
    settings = Settings(project_root=tmp_path)
    proto_skills = tmp_path / "protocol" / "skills"
    proto_skills.mkdir(parents=True, exist_ok=True)
    _make_skill_dir(proto_skills, "start-task")
    _make_skill_dir(proto_skills, "jobs")
    _make_skill_dir(proto_skills, "make-worktree")
    _make_skill_dir(proto_skills, "thread")
    _make_skill_dir(proto_skills, "dream")
    _make_skill_dir(proto_skills, "todos")
    _make_skill_dir(proto_skills, "create-skill")

    executor_spawned = False

    class _FakeExec:
        def __init__(self, **kwargs):
            pass

        def run(self, **kwargs):
            nonlocal executor_spawned
            executor_spawned = True
            from runtime.orchestrator.executors import ExecutorResult
            return ExecutorResult(success=True, duration_seconds=0, session_id="fake")

    await run_wake(
        org_state=org_state,
        work_hour_id="WH-001",
        settings=settings,
        executor_factory=lambda *a, **kw: _FakeExec(),
    )

    # Wake with contracts present → executor SHOULD spawn.
    assert executor_spawned, (
        "executor was NOT spawned even though contracts were present"
    )

    # Prove the now-universal todos SystemContract materialized through
    # the real production wake path (review finding 1, TASK-4319).
    todos_marker = ws / ".agents" / "skills" / "todos" / "SKILL.md"
    assert todos_marker.is_file(), (
        f"todos skill not materialized at {todos_marker}; "
        f"workspace skills dir contents: "
        f"{list((ws / '.agents' / 'skills').rglob('*')) if (ws / '.agents' / 'skills').is_dir() else 'missing'}"
    )


@pytest.mark.asyncio
async def test_wake_spawn_stops_on_materialization_error(org_state, tmp_path, monkeypatch):
    """run_wake must fail BEFORE spawning the executor when
    materialization of workspace skills raises."""
    db = org_state.db
    now = datetime.now(timezone.utc)
    db.work_hours.insert(WorkHourRecord(
        id="WH-002",
        agent_name="dev_agent",
        local_date="2026-06-09",
        slot="10:00",
        mode=WorkHourMode.WINDOWED,
        scheduled_for=now,
        window_start=now,
        window_end=now,
        status=WorkHourStatus.PENDING,
        routine_count=1,
    ))

    # Agent definition file needed by load_agent()
    (org_state.root / "org" / "agents").mkdir(parents=True, exist_ok=True)
    (org_state.root / "org" / "agents" / "dev_agent.md").write_text(
        "---\n"
        "name: dev_agent\n"
        "team: engineering\n"
        "role: worker\n"
        "executor: claude\n"
        "---\n\n"
        "## Routine Tasks\n\n"
        "- Run routine check.\n"
    )

    ws = org_state.root / "workspaces" / "dev_agent"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "agent.yaml").write_text("executor: claude\n")
    (ws / "repos" / "test" / ".git").mkdir(parents=True, exist_ok=True)

    # Create source skill dirs so canonical store builds succeed.
    settings = Settings(project_root=tmp_path)
    proto_skills = tmp_path / "protocol" / "skills"
    proto_skills.mkdir(parents=True, exist_ok=True)
    _make_skill_dir(proto_skills, "start-task")
    _make_skill_dir(proto_skills, "jobs")
    _make_skill_dir(proto_skills, "make-worktree")
    _make_skill_dir(proto_skills, "thread")

    # Inject explicit materialization error.
    from runtime.skills.symlink_materializer import (
        SymlinkMaterializer,
        SymlinkMaterializationError,
    )

    def _failing_materialize(self, skill_slug, version, content_hash,
                             workspace, skills_subdir, **kwargs):
        raise SymlinkMaterializationError(
            "injected_failure",
            f"Injected materialization failure for {skill_slug}",
        )

    monkeypatch.setattr(SymlinkMaterializer, "materialize_skill", _failing_materialize)

    executor_spawned = False

    class _FakeExec:
        def __init__(self, **kwargs):
            pass

        def run(self, **kwargs):
            nonlocal executor_spawned
            executor_spawned = True
            from runtime.orchestrator.executors import ExecutorResult
            return ExecutorResult(success=True, duration_seconds=0, session_id="fake")

    fake_exec = _FakeExec()

    await run_wake(
        org_state=org_state,
        work_hour_id="WH-002",
        settings=settings,
        executor_factory=lambda *a, **kw: fake_exec,
    )

    # Executor was NOT spawned (fail-closed pre-spawn)
    assert not executor_spawned, (
        "executor was spawned despite injected materialization error"
    )

    # Work hour is marked FAILED with a materialization error
    wh = db.work_hours.get("WH-002")
    assert wh is not None
    assert wh.status == WorkHourStatus.FAILED, (
        f"Expected FAILED status, got {wh.status}"
    )
    assert "materialization" in (wh.error or "").lower(), (
        f"Error message should reference materialization failure: {wh.error}"
    )


# ── Schedule production path ──────────────────────────────────────────


@pytest.mark.asyncio
def _make_host_supervisor():
    from runtime.orchestrator.host_supervisor import build_default_host_supervisor
    return build_default_host_supervisor()


async def test_schedule_spawn_stops_on_materialization_error(org_state, tmp_path, monkeypatch):
    """run_schedule must fail BEFORE spawning the executor when
    materialization of workspace skills raises.

    Also proves that context="schedule" (SessionContext.SCHEDULE) reaches
    materialize_workspace_skills from the schedule-fire production path —
    not "wake" or "task"."""
    db = org_state.db
    now = datetime.now(timezone.utc)

    # Insert a FIRING schedule record so run_schedule picks it up.
    db.schedules.insert(ScheduleRecord(
        id="SCHEDULE-001",
        agent_name="dev_agent",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=now,
        normalized_brief="Test brief",
        source_instruction="Test instruction",
        status=ScheduleStatus.FIRING,
        team="engineering",
    ))

    # Agent def in org/agents/ needed by load_agent()
    (org_state.root / "org" / "agents").mkdir(parents=True, exist_ok=True)
    (org_state.root / "org" / "agents" / "dev_agent.md").write_text(
        "---\n"
        "name: dev_agent\n"
        "team: engineering\n"
        "role: worker\n"
        "executor: claude\n"
        "---\n\n"
        "## Routine Tasks\n\n"
        "- Run routine check.\n"
    )

    ws = org_state.root / "workspaces" / "dev_agent"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "agent.yaml").write_text("executor: claude\n")
    (ws / "repos" / "test" / ".git").mkdir(parents=True, exist_ok=True)

    # Create source skill dirs so canonical store builds succeed.
    settings = Settings(project_root=tmp_path)
    proto_skills = tmp_path / "protocol" / "skills"
    proto_skills.mkdir(parents=True, exist_ok=True)
    _make_skill_dir(proto_skills, "start-task")
    _make_skill_dir(proto_skills, "jobs")
    _make_skill_dir(proto_skills, "make-worktree")
    _make_skill_dir(proto_skills, "thread")

    # Intercept materialize_workspace_skills to confirm context="schedule"
    # reaches it from the schedule-fire production path.
    # Must monkeypatch the schedule_runner module's own import binding,
    # not the source module, because schedule_runner does a direct import.
    captured_context = []
    from runtime.daemon import schedule_runner as sr_mod

    def _capturing_materialize(workspace, settings, *, slug, context,
                               provider, agent_name, team, skills_root,
                               org_root=None, db=None):
        captured_context.append(context)
        raise RuntimeError("injected_schedule_materialization_error")

    monkeypatch.setattr(sr_mod, "materialize_workspace_skills", _capturing_materialize)

    executor_spawned = False

    class _FakeExec:
        def __init__(self, **kwargs):
            pass

        def run(self, **kwargs):
            nonlocal executor_spawned
            executor_spawned = True
            from runtime.orchestrator.executors import ExecutorResult
            return ExecutorResult(success=True, duration_seconds=0, session_id="fake")

    monkeypatch.setattr(
        "runtime.daemon.schedule_runner._executor_name",
        lambda paths, agent_name: "claude",
    )
    monkeypatch.setattr(
        "runtime.daemon.schedule_runner._build_executor_for_provider",
        lambda provider, s, paths: _FakeExec(),
    )

    await run_schedule(
        org_state=org_state,
        schedule_id="SCHEDULE-001",
        settings=settings,
        host_supervisor=_make_host_supervisor(),
    )

    # Executor was NOT spawned (fail-closed pre-spawn)
    assert not executor_spawned, (
        "executor was spawned despite injected materialization error"
    )

    # Context must be "schedule", not "wake" or "task".
    assert captured_context == ["schedule"], (
        f"Expected context=['schedule'], got {captured_context}"
    )

    # Schedule must be FAILED with a materialization error.
    refreshed = db.schedules.get("SCHEDULE-001")
    assert refreshed is not None
    assert refreshed.status == ScheduleStatus.FAILED, (
        f"Expected FAILED, got {refreshed.status}"
    )
    assert "materialization" in (refreshed.error or "").lower(), (
        f"Error should mention materialization: {refreshed.error!r}"
    )


# ── Helpers ───────────────────────────────────────────────────────────


def _make_skill_dir(src_root: Path, skill_id: str) -> Path:
    d = src_root / skill_id
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"# {skill_id}\n\nSkill body for {skill_id}.\n")
    return d
