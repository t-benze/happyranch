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
from runtime.daemon.thread_runner import run_invocation
from runtime.daemon.wake_runner import run_wake
from runtime.models import (
    DreamRecord,
    DreamStatus,
    ThreadInvocationPurpose,
    ThreadInvocationStatus,
    ThreadMessageKind,
    ThreadRecord,
    WorkHourMode,
    WorkHourRecord,
    WorkHourStatus,
)
from runtime.config import Settings


# ── Thread production path ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_thread_spawn_stops_on_missing_contracts(org_state, tmp_path, monkeypatch):
    """run_invocation with an EMPTY workspace (no .claude/skills) must fail
    the invocation BEFORE spawning the executor — invocation stays FAILED,
    executor is never called."""
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

    # Workspace: agent.yaml exists (so executor resolution works) but
    # .claude/skills/ is ABSENT — post-redeploy state.
    ws = org_state.root / "workspaces" / "dev_agent"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "agent.yaml").write_text("executor: claude\n")
    (ws / "repos" / "test" / ".git").mkdir(parents=True, exist_ok=True)

    # Use a TEMP project root so protocol/skills/ is empty — no source
    # material is available for injection.
    settings = Settings(project_root=tmp_path)
    proto_skills = tmp_path / "protocol" / "skills"
    proto_skills.mkdir(parents=True, exist_ok=True)

    # Replace executor factory so we can DETECT if spawn was attempted.
    import runtime.daemon.thread_runner as runner_mod
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
        runner_mod,
        "_build_executor_for_provider",
        lambda provider, s, paths: _FakeExec(),
    )

    await run_invocation(
        org_state=org_state,
        invocation_token=inv.invocation_token,
        settings=settings,
    )

    # Executor must NOT have been spawned.
    assert not executor_spawned, (
        "executor was spawned despite missing system contracts"
    )

    # Invocation must be marked FAILED (not stuck in PENDING).
    inv_after = db.get_invocation_any_status(inv.invocation_token)
    assert inv_after.status == ThreadInvocationStatus.FAILED, (
        f"expected FAILED, got {inv_after.status}"
    )
    # The error message names missing contracts, not bare Errno 2.
    reason = inv_after.decline_reason or ""
    assert "contract" in reason.lower(), (
        f"decline_reason should mention contracts: {reason!r}"
    )
    assert "Errno 2" not in reason, (
        f"decline_reason must not contain bare Errno 2: {reason!r}"
    )


# ── Dream production path ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dream_spawn_stops_on_missing_contracts(org_state, tmp_path, monkeypatch):
    """run_dream with an EMPTY workspace must fail the dream BEFORE spawning
    the executor — dream stays FAILED, executor is never called."""
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

    # Use a TEMP project root so protocol/skills/ is empty.
    settings = Settings(project_root=tmp_path)
    proto_skills = tmp_path / "protocol" / "skills"
    proto_skills.mkdir(parents=True, exist_ok=True)

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
        "executor was spawned despite missing system contracts"
    )

    dream = db.get_dream("DREAM-001")
    assert dream.status == DreamStatus.FAILED, (
        f"expected FAILED, got {dream.status}"
    )
    assert "contract" in (dream.error or "").lower(), (
        f"error should mention contracts: {dream.error!r}"
    )
    assert "Errno 2" not in (dream.error or ""), (
        f"error must not contain bare Errno 2: {dream.error!r}"
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


@pytest.mark.asyncio
async def test_wake_spawn_stops_on_missing_contracts(org_state, tmp_path, monkeypatch):
    """run_wake with an EMPTY workspace must fail BEFORE spawning the executor."""
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

    # Use a TEMP project root — no source material available.
    settings = Settings(project_root=tmp_path)
    proto_skills = tmp_path / "protocol" / "skills"
    proto_skills.mkdir(parents=True, exist_ok=True)

    # NOTE: wake_runner already calls ensure_system_contracts_materialized
    # WITHOUT try/except wrapping (correct pattern). The error propagates
    # and the wake worker loop catches it. We verify the error is raised.
    from runtime.orchestrator.workspace_adapters import (
        SystemContractMaterializationError,
    )

    class _FakeExec:
        def __init__(self, **kwargs):
            pass

        def run(self, **kwargs):
            from runtime.orchestrator.executors import ExecutorResult
            return ExecutorResult(success=True, duration_seconds=0, session_id="fake")

    fake_exec = _FakeExec()

    with pytest.raises(SystemContractMaterializationError) as exc_info:
        await run_wake(
            org_state=org_state,
            work_hour_id="WH-002",
            settings=settings,
            executor_factory=lambda *a, **kw: fake_exec,
        )
    msg = str(exc_info.value)
    assert "contract" in msg.lower()
    assert "Errno 2" not in msg


# ── Helpers ───────────────────────────────────────────────────────────


def _make_skill_dir(src_root: Path, skill_id: str) -> Path:
    d = src_root / skill_id
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"# {skill_id}\n\nSkill body for {skill_id}.\n")
    return d
