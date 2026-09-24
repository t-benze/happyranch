"""Unit tests for Orchestrator.run_step — the single primitive that advances
a task one subprocess call at a time under the new async execution model."""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.config import Settings
from runtime.daemon import workspace_cleanup_scheduler as wcs
from runtime.daemon.sessions import SessionTracker
from runtime.infrastructure.database import Database
from runtime.models import (
    BlockKind,
    JobInterpreter,
    JobRecord,
    JobStatus,
    TaskRecord,
    TaskStatus,
)
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.teams import TeamsRegistry
from runtime.runtime import RuntimeDir
from tests.test_workspace_cleanup_scheduler import _FakeQueue


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, text=True, capture_output=True, check=True,
    )


def _terminal_worktree(
    runtime: OrgPaths,
    db: Database,
    task_id: str,
    *,
    status: TaskStatus = TaskStatus.PENDING,
    agent: str = "dev_agent",
    parent_task_id: str | None = None,
    task_type: str = "task",
    create_candidate: bool = True,
):
    """Create one disposable canonical primary + linked task worktree."""
    from runtime.orchestrator.orchestrator import Orchestrator

    primary = runtime.workspaces_dir / agent / "repos" / "happyranch"
    primary.mkdir(parents=True)
    _git(primary, "init", "-b", "main")
    _git(primary, "config", "user.email", "tests@example.invalid")
    _git(primary, "config", "user.name", "HappyRanch tests")
    (primary / "tracked.txt").write_text("base\n")
    _git(primary, "add", "tracked.txt")
    _git(primary, "commit", "-m", "test base")
    _git(primary, "update-ref", "refs/remotes/origin/main", "HEAD")
    candidate = primary / ".claude" / "worktrees" / task_id
    if create_candidate:
        candidate.parent.mkdir(parents=True)
        _git(primary, "worktree", "add", "-b", f"task/{task_id}", str(candidate))

    db.insert_task(TaskRecord(
        id=task_id,
        brief="terminal worktree",
        assigned_agent=agent,
        status=status,
        parent_task_id=parent_task_id,
        task_type=task_type,
    ))
    orch = Orchestrator(
        db=db,
        settings=Settings(),
        paths=runtime,
        slug="test",
        teams=TeamsRegistry.load(runtime.root),
    )
    orch.attach_sessions(SessionTracker())
    return orch, primary, candidate


def _admit_terminal_worktree(monkeypatch) -> None:
    """Keep the real local-git probes while making remote/process facts exact."""
    from runtime.orchestrator import run_step as run_step_module

    real_run = run_step_module._run_terminal_worktree_command

    def fake_run(args, *, cwd, timeout):
        if args[0] == "gh":
            return subprocess.CompletedProcess(args, 0, "[]\n", "")
        return real_run(args, cwd=cwd, timeout=timeout)

    monkeypatch.setattr(run_step_module, "_run_terminal_worktree_command", fake_run)
    monkeypatch.setattr(
        run_step_module,
        "_terminal_worktree_process_reference",
        lambda candidate, deadline: None,
    )


def _record_terminal_worktree_outcomes(monkeypatch):
    """Observe the real helper while a production terminal seam owns the call."""
    from runtime.orchestrator import run_step as run_step_module

    original = run_step_module._reclaim_terminal_task_worktree
    outcomes = []

    def observed(*args, **kwargs):
        outcome = original(*args, **kwargs)
        outcomes.append(outcome)
        return outcome

    monkeypatch.setattr(
        run_step_module, "_reclaim_terminal_task_worktree", observed,
    )
    return outcomes


@pytest.fixture(autouse=True)
def _seed_active_agents_for_run_step(runtime: OrgPaths):
    """Task launch is fail-closed: an active AgentDef is required.

    Legacy tests created only a workspace. Seed active frontmatter for the
    agents used in this module so launch/token-usage resolution admits them.
    """
    from tests.conftest import seed_test_agents
    seed_test_agents(runtime, ("engineering_head", "dev_agent", "content_head", "content_agent"))


@pytest.fixture
def runtime(tmp_path: Path) -> OrgPaths:
    rt = RuntimeDir.init(tmp_path / "rt")
    paths = OrgPaths(root=rt.orgs_dir / "test")
    # Seed managers for two teams so manager-root decisions can prove team scope.
    paths.teams_config_path.parent.mkdir(parents=True, exist_ok=True)
    paths.teams_config_path.write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: engineering_head\n"
        "    workers: [product_manager, dev_agent, payment_agent, qa_engineer]\n"
        "  content:\n"
        "    manager: content_head\n"
        "    workers: [content_agent]\n"
    )
    return paths


@pytest.fixture
def db(runtime: OrgPaths) -> Database:
    return Database(runtime.db_path)


def test_run_step_silent_noop_when_task_missing(runtime, db):
    from runtime.orchestrator.orchestrator import Orchestrator
    settings = Settings(max_orchestration_steps=3)
    orch = Orchestrator(db=db, settings=settings, paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    # Just must not raise
    orch.run_step("TASK-NOPE")


def test_workspace_cleanup_hook_is_disabled_without_shared_config(runtime, db, monkeypatch):
    """The pre-agent hook has no selector, consumer, or audit side effect by default."""
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.run_step import _prepare_workspace_cleanup_reclamation_context

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    monkeypatch.setattr(
        db, "select_workspace_cleanup_reclamation_candidates",
        lambda **_: pytest.fail("disabled hook must not query the selector"),
    )

    assert _prepare_workspace_cleanup_reclamation_context(
        orch, SimpleNamespace(id="TASK-HOOK"), "dev_agent",
        stale_orchestration_step_count=0, claimed_next_step_count=1,
    ) == ""
    assert not [
        row for row in db.get_audit_logs("TASK-HOOK")
        if row["action"] == "workspace_cleanup_reclamation_attempt"
    ]


def test_workspace_cleanup_hook_starts_budget_after_enabled_load_and_uses_org_workspace(
    runtime, db, monkeypatch,
):
    """The shipping hook supplies the selector's first seven timed admissions.

    This directly exercises the hook rather than a selector-only model: the
    initial enabled config is outside the one-second window, and canonical and
    authoritative workspace arguments are the one OrgPaths-derived value.
    """
    from runtime.infrastructure.database import WorkspaceCleanupReclamationSelection
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.run_step import _prepare_workspace_cleanup_reclamation_context

    runtime.org_config_path.write_text(
        "workspace_cleanup:\n  reclamation_actions_enabled: true\n"
    )
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    observed: list[str] = []

    def selector(**kwargs):
        assert kwargs["owner_task_id"] == "TASK-HOOK"
        assert kwargs["stale_orchestration_step_count"] == 0
        assert kwargs["claimed_next_step_count"] == 1
        assert kwargs["canonical_workspace"] == runtime.workspaces_dir / "dev_agent"
        assert kwargs["authoritative_workspace"] is kwargs["canonical_workspace"]
        for name in ("owner", "marker", "history", "newer_owner", "candidates", "graph_tasks", "graph_edges"):
            assert kwargs["admit_observation"](name)
            observed.append(name)
        return WorkspaceCleanupReclamationSelection(
            owner_task_id="TASK-HOOK", candidates=(), read_observations=tuple(observed),
        )

    monkeypatch.setattr(db, "select_workspace_cleanup_reclamation_candidates", selector)
    assert _prepare_workspace_cleanup_reclamation_context(
        orch, SimpleNamespace(id="TASK-HOOK"), "dev_agent",
        stale_orchestration_step_count=0, claimed_next_step_count=1,
    ) == ""
    assert observed == ["owner", "marker", "history", "newer_owner", "candidates", "graph_tasks", "graph_edges"]


def test_workspace_cleanup_hook_records_literal_none_result_before_agent_launch(
    runtime, db, monkeypatch,
):
    """An invoked consumer's ``None`` is one literal owner audit and prompt fact."""
    from runtime.infrastructure.database import (
        WorkspaceCleanupReclamationCandidate,
        WorkspaceCleanupReclamationSelection,
    )
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.run_step import _prepare_workspace_cleanup_reclamation_context

    runtime.org_config_path.write_text(
        "workspace_cleanup:\n  reclamation_actions_enabled: true\n"
    )
    db.insert_task(TaskRecord(id="TASK-HOOK", brief="cleanup", assigned_agent="dev_agent"))
    db.update_task("TASK-HOOK", status=TaskStatus.IN_PROGRESS, block_kind=None,
                   orchestration_step_count=1)
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._sessions = object()
    selection = WorkspaceCleanupReclamationSelection(
        owner_task_id="TASK-HOOK",
        candidates=(WorkspaceCleanupReclamationCandidate(
            task_id="TASK-OLD", session_id="session-old",
            scratch_path=runtime.workspaces_dir / "dev_agent" / ".happyranch" / "task-tmp" / "TASK-OLD",
            result={"status": "completed"},
        ),),
        read_observations=(),
    )
    monkeypatch.setattr(db, "select_workspace_cleanup_reclamation_candidates", lambda **_: selection)
    monkeypatch.setattr(
        "runtime.daemon.task_scratch_reclamation.collect_revalidate_seal_consume_disposable",
        lambda **_: None,
    )

    prompt = _prepare_workspace_cleanup_reclamation_context(
        orch, SimpleNamespace(id="TASK-HOOK"), "dev_agent",
        stale_orchestration_step_count=0, claimed_next_step_count=1,
    )
    assert "refused_or_unavailable" in prompt
    assert "target=TASK-OLD refused_or_unavailable" in prompt
    audits = [row for row in db.get_audit_logs("TASK-HOOK")
              if row["action"] == "workspace_cleanup_reclamation_attempt"]
    assert len(audits) == 1
    payload = audits[0]["payload"]
    assert payload == {
        "target_task_id": "TASK-OLD", "outcome": "none", "claimed_bytes": 0,
        "claimed_inodes": 0, "remainder": None, "reason": None,
        "publication": "attempted",
    }


def test_run_step_noop_on_blocked_escalated(runtime, db):
    """A task in ESCALATED isn't eligible for run_step — it waits
    for /resolve-escalation to transition it first. Second-hand enqueue
    must be silently ignored."""
    from runtime.orchestrator.orchestrator import Orchestrator
    db.insert_task(TaskRecord(id="T-1", brief="x"))
    db.update_task("T-1", status=TaskStatus.ESCALATED, block_kind=None,
                   note="halted")
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    orch.run_step("T-1")
    t = db.get_task("T-1")
    assert t.status == TaskStatus.ESCALATED
    assert t.block_kind is None


@pytest.mark.parametrize("prior_count", [50, 51, 500])
def test_run_step_beyond_legacy_cap_claims_and_runs(runtime, db, monkeypatch, prior_count):
    from runtime.orchestrator.orchestrator import Orchestrator
    settings = Settings(max_orchestration_steps=3)
    db.insert_task(TaskRecord(
        id="T-1", brief="x", assigned_agent="engineering_head",
    ))
    db.update_task("T-1", orchestration_step_count=prior_count)

    orch = Orchestrator(db=db, settings=settings, paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    monkeypatch.setattr(orch, "_run_agent", lambda *args, **kwargs: (
        _make_result(), _make_report(output_summary=json.dumps({"action": "done", "summary": "done"})),
    ))
    orch.run_step("T-1")

    t = db.get_task("T-1")
    assert t.status == TaskStatus.COMPLETED
    assert t.block_kind is None
    assert t.orchestration_step_count == prior_count + 1
    assert not [a for a in db.get_audit_logs("T-1") if a["action"] == "escalation"]


def test_run_step_transitions_pending_to_in_progress_and_increments_count(
    runtime, db, monkeypatch,
):
    """On pickup, run_step must flip to in_progress, clear block fields,
    and increment the step counter exactly once — BEFORE invoking the agent."""
    from runtime.orchestrator.orchestrator import Orchestrator, WorkspaceNotInitialized

    db.insert_task(TaskRecord(
        id="T-1", brief="x", assigned_agent="engineering_head",
    ))
    orch = Orchestrator(db=db, settings=Settings(max_orchestration_steps=10), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))

    # Force _run_agent to raise so we can inspect the DB state mid-flight.
    captured: dict = {}
    def fail(task_id, agent, prompt, on_session_started=None):
        t = db.get_task(task_id)
        captured["status"] = t.status
        captured["count"] = t.orchestration_step_count
        captured["block_kind"] = t.block_kind
        captured["note"] = t.note
        raise WorkspaceNotInitialized("fake")
    monkeypatch.setattr(orch, "_run_agent", fail)

    orch.run_step("T-1")

    assert captured["status"] == TaskStatus.IN_PROGRESS
    assert captured["count"] == 1
    assert captured["block_kind"] is None
    assert captured["note"] is None


def _make_report(output_summary: str, status: str = "completed",
                 output_dir: str | None = None, verdict: str | None = None):
    from runtime.models import CompletionReport
    return CompletionReport(
        task_id="T-IGNORED", agent="engineering_head", status=status,
        confidence=80, output_summary=output_summary, output_dir=output_dir,
        verdict=verdict,
    )


def _make_result(success: bool = True, duration: int = 1):
    from runtime.orchestrator.executors import ExecutorResult
    return ExecutorResult(
        success=success, session_id="sess-x", duration_seconds=duration,
    )

class _SlugQueue:
    """Test adapter: wraps asyncio.Queue so put_nowait(slug, task_id) works.
    
    Production code calls _queue.put_nowait(slug, task_id), but tests use a
    stdlib asyncio.Queue. This shim accepts the 2-arg form and stores the
    (slug, task_id) tuple on the underlying queue.
    """
    def __init__(self) -> None:
        import asyncio as _asyncio
        self._q: _asyncio.Queue = _asyncio.Queue()
    def put_nowait(self, slug: str, task_id: str) -> None:
        self._q.put_nowait((slug, task_id))
    def qsize(self) -> int:
        return self._q.qsize()
    def get_nowait(self):
        return self._q.get_nowait()


def _consume_manager_supersede(orch, task_id: str, agent: str = "engineering_head") -> None:
    from runtime.models import CompletionReport, NextStep
    from runtime.orchestrator.run_step import _consume_completion_report

    _consume_completion_report(
        orch,
        task_id,
        CompletionReport(
            task_id=task_id,
            agent=agent,
            status="completed",
            confidence=90,
            output_summary="replace the plan",
            decision=NextStep(
                action="supersede",
                successor_brief="replacement plan",
                rationale="new evidence",
                attestation={
                    "recovery_reason": "Evidence invalidated the old plan.",
                    "policy_product_intent_unchanged": True,
                    "no_budget_or_external_commitment": True,
                    "no_permission_or_cross_team_change": True,
                    "no_schema_auth_security_privacy_or_data_access_change": True,
                    "no_unresolved_founder_gate": True,
                },
            ),
        ),
    )


def _claimed_manager_root(
    db,
    task_id: str = "T-SUP",
    *,
    team: str = "engineering",
    agent: str = "engineering_head",
) -> None:
    db.insert_task(TaskRecord(
        id=task_id,
        brief="original plan",
        team=team,
        assigned_agent=agent,
        status=TaskStatus.IN_PROGRESS,
        current_session_id="session-sup",
    ))


def test_persisted_null_supersede_attestation_never_reaches_write_path(runtime, db):
    """A malformed persisted callback must not create a successor or audit row."""
    from runtime.orchestrator.orchestrator import Orchestrator

    _claimed_manager_root(db)
    db.insert_task_result(
        task_id="T-SUP",
        agent="engineering_head",
        session_id="session-sup",
        status="completed",
        output_summary="replace the plan",
        confidence_score=90,
        decision_json='{"action":"supersede","successor_brief":"replacement plan",'
                      '"rationale":"new evidence","attestation":null}',
    )
    orch = Orchestrator(
        db=db, settings=Settings(), paths=runtime, slug="test",
        teams=TeamsRegistry.load(runtime.root),
    )

    report = orch._read_completion_from_db("T-SUP", "engineering_head", "session-sup")

    assert report is not None
    assert report.decision is None
    assert db.get_task("T-SUP").status is TaskStatus.IN_PROGRESS
    assert db.execute("SELECT COUNT(*) FROM tasks WHERE id != 'T-SUP'").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM manager_supersessions").fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action = 'manager_supersession'"
    ).fetchone()[0] == 0


def test_completion_consumer_allows_any_team_without_legacy_supersession_env_gates(
    runtime, db, monkeypatch,
):
    from runtime.orchestrator.orchestrator import Orchestrator

    monkeypatch.delenv("HAPPYRANCH_MANAGER_SUPERSESSION_ENABLED", raising=False)
    monkeypatch.delenv("HAPPYRANCH_MANAGER_SUPERSESSION_PILOT_TEAM", raising=False)
    _claimed_manager_root(db, team="content", agent="content_head")
    orch = Orchestrator(
        db=db, settings=Settings(), paths=runtime, slug="test",
        teams=TeamsRegistry.load(runtime.root),
    )
    orch._queue = _SlugQueue()

    _consume_manager_supersede(orch, "T-SUP", agent="content_head")

    successor = db.get_task("TASK-001")
    assert db.get_task("T-SUP").status is TaskStatus.SUPERSEDED
    assert successor is not None and successor.status is TaskStatus.PENDING
    assert successor.assigned_agent == "content_head"


def test_completion_consumer_ignores_legacy_supersession_env_gate(runtime, db, monkeypatch):
    from runtime.orchestrator.orchestrator import Orchestrator

    monkeypatch.setenv("HAPPYRANCH_MANAGER_SUPERSESSION_ENABLED", "0")
    monkeypatch.setenv("HAPPYRANCH_MANAGER_SUPERSESSION_PILOT_TEAM", "other")
    _claimed_manager_root(db)
    orch = Orchestrator(
        db=db, settings=Settings(), paths=runtime, slug="test",
        teams=TeamsRegistry.load(runtime.root),
    )
    orch._queue = _SlugQueue()

    _consume_manager_supersede(orch, "T-SUP")

    assert db.get_task("T-SUP").status is TaskStatus.SUPERSEDED
    assert db.get_task("TASK-001").assigned_agent == "engineering_head"
    source = Path(__file__).resolve().parents[1] / "runtime/orchestrator/run_step.py"
    assert "HAPPYRANCH_MANAGER_SUPERSESSION" not in source.read_text()


@pytest.mark.parametrize(
    ("field", "value", "expected_status"),
    [
        ("assigned_agent", "dev_agent", TaskStatus.FAILED),
        ("current_session_id", None, TaskStatus.FAILED),
        ("parent_task_id", "T-PARENT", TaskStatus.IN_PROGRESS),
    ],
    ids=["current_manager", "current_session", "root"],
)
def test_completion_consumer_enforces_manager_session_and_root_gates(
    runtime, db, field: str, value: str | None, expected_status: TaskStatus,
):
    from runtime.orchestrator.orchestrator import Orchestrator

    _claimed_manager_root(db)
    db.execute(f"UPDATE tasks SET {field} = ? WHERE id = 'T-SUP'", (value,))
    db._conn.commit()
    orch = Orchestrator(
        db=db, settings=Settings(), paths=runtime, slug="test",
        teams=TeamsRegistry.load(runtime.root),
    )
    orch._queue = _SlugQueue()

    _consume_manager_supersede(orch, "T-SUP")

    assert db.get_task("T-SUP").status is expected_status
    assert db.execute("SELECT COUNT(*) FROM manager_supersessions").fetchone()[0] == 0
    assert orch._queue.qsize() == 0


def test_completion_consumer_escalates_thread_origin_rejection_once(runtime, db):
    from runtime.orchestrator.orchestrator import Orchestrator

    _claimed_manager_root(db)
    db.execute("UPDATE tasks SET dispatched_from_thread_id = 'THR-152' WHERE id = 'T-SUP'")
    db._conn.commit()
    orch = Orchestrator(
        db=db, settings=Settings(), paths=runtime, slug="test",
        teams=TeamsRegistry.load(runtime.root),
    )
    orch._queue = _SlugQueue()
    founder_notifications: list[dict] = []
    orch.notify_escalated = lambda **kwargs: founder_notifications.append(kwargs)

    _consume_manager_supersede(orch, "T-SUP")

    task = db.get_task("T-SUP")
    assert task.status is TaskStatus.ESCALATED
    assert task.note == (
        "manager supersession rejected: thread-origin roots are not eligible "
        "for supersession; founder action required"
    )
    assert db.execute("SELECT COUNT(*) FROM manager_supersessions").fetchone()[0] == 0
    logs = db.get_audit_logs("T-SUP")
    assert [row["action"] for row in logs].count("orchestration_step") == 1
    escalation = next(row for row in logs if row["action"] == "escalation")
    assert escalation["payload"] == {"reason": task.note}
    authority = next(row for row in logs if row["action"] == "authority_hook")
    assert authority["payload"] == {
        "outcome": "not_applicable",
        "reason_code": "runtime_manager_supersession_thread_origin_ineligible",
        "reason": "runtime-raised escalation is not an authority decision",
        "causal_escalation_audit_id": escalation["id"],
    }
    assert founder_notifications == [{
        "task_id": "T-SUP",
        "agent": "engineering_head",
        "reason": task.note,
        "last_summary": "replace the plan",
    }]
    assert orch._queue.qsize() == 0


@pytest.mark.parametrize(
    ("status", "block_kind"),
    [
        (TaskStatus.PENDING, None),
        (TaskStatus.IN_PROGRESS, BlockKind.DELEGATED),
        (TaskStatus.CANCELLED, None),
    ],
    ids=["pending", "blocked", "cancelled"],
)
def test_completion_consumer_thread_origin_rejection_preserves_competing_state(
    runtime, db, monkeypatch, status: TaskStatus, block_kind: BlockKind | None,
):
    """A transition immediately before the rejection CAS remains authoritative."""
    from runtime.orchestrator.orchestrator import Orchestrator

    _claimed_manager_root(db)
    db.execute("UPDATE tasks SET dispatched_from_thread_id = 'THR-152' WHERE id = 'T-SUP'")
    db._conn.commit()
    orch = Orchestrator(
        db=db, settings=Settings(), paths=runtime, slug="test",
        teams=TeamsRegistry.load(runtime.root),
    )
    orch._queue = _SlugQueue()
    founder_notifications: list[dict] = []
    thread_projections: list[tuple] = []
    orch.notify_escalated = lambda **kwargs: founder_notifications.append(kwargs)
    monkeypatch.setattr(
        "runtime.orchestrator.run_step._maybe_post_thread_escalation",
        lambda *args, **kwargs: thread_projections.append((args, kwargs)),
    )
    real_reject = db.try_reject_thread_origin_manager_supersede

    def competing_transition_wins(*args, **kwargs):
        db.update_task(
            "T-SUP", status=status, block_kind=block_kind,
            note="competing transition",
            **({
                "cancelled_at": "2026-09-06T00:00:00+00:00",
                "completed_at": "2026-09-06T00:00:00+00:00",
            } if status is TaskStatus.CANCELLED else {}),
        )
        return real_reject(*args, **kwargs)

    monkeypatch.setattr(
        db, "try_reject_thread_origin_manager_supersede", competing_transition_wins,
    )

    _consume_manager_supersede(orch, "T-SUP")

    task = db.get_task("T-SUP")
    assert task.status is status
    assert task.block_kind is block_kind
    assert task.note == "competing transition"
    assert not [
        row for row in db.get_audit_logs("T-SUP")
        if row["action"] in {"escalation", "authority_hook"}
    ]
    assert founder_notifications == []
    assert thread_projections == []
    assert db.execute("SELECT COUNT(*) FROM manager_supersessions").fetchone()[0] == 0


@pytest.mark.parametrize(
    "live_work",
    ["descendant_task", "pending_root_job", "running_descendant_job"],
)
def test_completion_consumer_thread_origin_rejection_preserves_competing_live_work(
    runtime, db, monkeypatch, live_work: str,
):
    """Family work landing immediately before the rejection CAS wins silently."""
    from runtime.orchestrator.orchestrator import Orchestrator

    _claimed_manager_root(db)
    db.execute("UPDATE tasks SET dispatched_from_thread_id = 'THR-152' WHERE id = 'T-SUP'")
    db._conn.commit()
    orch = Orchestrator(
        db=db, settings=Settings(), paths=runtime, slug="test",
        teams=TeamsRegistry.load(runtime.root),
    )
    orch._queue = _SlugQueue()
    founder_notifications: list[dict] = []
    thread_projections: list[tuple] = []
    orch.notify_escalated = lambda **kwargs: founder_notifications.append(kwargs)
    monkeypatch.setattr(
        "runtime.orchestrator.run_step._maybe_post_thread_escalation",
        lambda *args, **kwargs: thread_projections.append((args, kwargs)),
    )
    real_reject = db.try_reject_thread_origin_manager_supersede

    def live_work_lands(*args, **kwargs):
        descendant_id = "T-LIVE"
        if live_work in {"descendant_task", "running_descendant_job"}:
            db.insert_task(TaskRecord(
                id=descendant_id,
                status=(
                    TaskStatus.PENDING
                    if live_work == "descendant_task"
                    else TaskStatus.COMPLETED
                ),
                assigned_agent="dev_agent",
                team="engineering",
                brief="competing family work",
                parent_task_id="T-SUP",
                task_type="subtask",
            ))
        if live_work != "descendant_task":
            job_status = "pending" if live_work == "pending_root_job" else "running"
            job_task_id = "T-SUP" if live_work == "pending_root_job" else descendant_id
            db.execute(
                """INSERT INTO jobs
                   (id, task_id, agent_name, title, script_text, interpreter, status, created_at)
                   VALUES ('JOB-LIVE', ?, 'dev_agent', 'live', 'true', 'bash', ?,
                           '2026-09-06T00:00:00+00:00')""",
                (job_task_id, job_status),
            )
            db._conn.commit()
        return real_reject(*args, **kwargs)

    monkeypatch.setattr(
        db, "try_reject_thread_origin_manager_supersede", live_work_lands,
    )

    _consume_manager_supersede(orch, "T-SUP")

    task = db.get_task("T-SUP")
    assert task.status is TaskStatus.IN_PROGRESS
    assert task.block_kind is None
    if live_work == "descendant_task":
        assert db.get_task("T-LIVE").status is TaskStatus.PENDING
    else:
        assert db.get_job_status("JOB-LIVE") == (
            "pending" if live_work == "pending_root_job" else "running"
        )
    assert not [
        row for row in db.get_audit_logs("T-SUP")
        if row["action"] in {
            "escalation", "authority_hook", "manager_supersession",
        }
    ]
    assert founder_notifications == []
    assert thread_projections == []
    assert orch._queue.qsize() == 0
    assert db.execute("SELECT COUNT(*) FROM manager_supersessions").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM tasks WHERE id = 'TASK-001'").fetchone()[0] == 0


@pytest.mark.parametrize("enqueue_fails", [False, True], ids=["enqueue", "recovery"])
def test_completion_consumer_supersedes_and_leaves_successor_recoverable(
    runtime, db, monkeypatch, enqueue_fails: bool,
):
    from runtime.orchestrator.orchestrator import Orchestrator

    _claimed_manager_root(db)
    orch = Orchestrator(
        db=db, settings=Settings(), paths=runtime, slug="test",
        teams=TeamsRegistry.load(runtime.root),
    )
    orch._queue = _SlugQueue()
    founder_notifications: list[dict] = []
    orch.notify_escalated = lambda **kwargs: founder_notifications.append(kwargs)
    if enqueue_fails:
        def fail_enqueue(slug: str, task_id: str) -> None:
            raise RuntimeError("injected queue outage")
        monkeypatch.setattr(orch._queue, "put_nowait", fail_enqueue)

    _consume_manager_supersede(orch, "T-SUP")

    successor = db.get_task("TASK-001")
    assert db.get_task("T-SUP").status is TaskStatus.SUPERSEDED
    assert successor is not None and successor.status is TaskStatus.PENDING
    assert successor.assigned_agent == "engineering_head"
    assert founder_notifications == []
    assert orch._queue.qsize() == (0 if enqueue_fails else 1)



def test_run_step_done_completes_task_and_enqueues_parent(
    runtime, db, monkeypatch,
):
    import asyncio
    import json
    from runtime.orchestrator.orchestrator import Orchestrator

    # Parent in in_progress(delegated), child in pending.
    db.insert_task(TaskRecord(id="T-PAR", brief="parent",
                              assigned_agent="engineering_head"))
    db.update_task("T-PAR", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(
        id="T-CHD", brief="child",
        assigned_agent="engineering_head", parent_task_id="T-PAR",
    ))

    orch = Orchestrator(db=db, settings=Settings(max_orchestration_steps=10),
                        paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    # Wire a fake queue
    q = _SlugQueue()
    orch._queue = q

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary=json.dumps({"action": "done", "summary": "Looks great"}),
            output_dir="output/run-1",
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    orch.run_step("T-CHD")

    child = db.get_task("T-CHD")
    assert child.status == TaskStatus.COMPLETED
    assert child.note == "Looks great"
    assert child.final_output_dir == "output/run-1"

    # Parent should be enqueued
    assert q.qsize() == 1
    assert q.get_nowait() == ("test", "T-PAR")


def test_run_step_nonroot_escalate_fails_and_routes_to_parent(
    runtime, db, monkeypatch,
):
    """THR-033 Change A: a NON-root task whose decision is `escalate` must NOT
    escalate directly to the founder — it fails and hands back to its parent,
    which is woken for a bounded-recovery decision step. Constructed with a
    task_type='task' non-root so the decision pipeline is exercised (in
    production, children are task_type='subtask' and never decide, so this
    branch is defensive lock-in).
    """
    import json
    from runtime.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-PAR", brief="p",
                              assigned_agent="engineering_head"))
    db.update_task("T-PAR", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(
        id="T-CHD", brief="c",
        assigned_agent="engineering_head", parent_task_id="T-PAR",
        task_type="task",
    ))

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    q = _SlugQueue()
    orch._queue = q

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary=json.dumps({"action": "escalate", "reason": "needs founder"}),
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    orch.run_step("T-CHD")

    child = db.get_task("T-CHD")
    # Non-root never reaches escalated — it FAILS.
    assert child.status == TaskStatus.FAILED
    assert child.block_kind is None
    assert "non-root escalation requested" in (child.note or "")
    assert "needs founder" in (child.note or "")

    # No escalation audit row was written for the child.
    escalations = [a for a in db.get_audit_logs("T-CHD") if a["action"] == "escalation"]
    assert escalations == []

    # Parent woken for a bounded-recovery decision step (1 failed child < bound).
    assert q.qsize() == 1
    assert q.get_nowait() == ("test", "T-PAR")
    assert db.get_task("T-PAR").status == TaskStatus.IN_PROGRESS


def test_run_step_root_escalate_parks_escalated(
    runtime, db, monkeypatch,
):
    """THR-033 Change A: a ROOT task whose decision is `escalate` parks
    in escalated for the founder — root escalation is unchanged."""
    import json
    from runtime.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-ROOT", brief="r",
                              assigned_agent="engineering_head"))

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    q = _SlugQueue()
    orch._queue = q

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary=json.dumps({"action": "escalate", "reason": "needs founder"}),
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    orch.run_step("T-ROOT")

    t = db.get_task("T-ROOT")
    assert t.parent_task_id is None  # it is a root
    assert t.status == TaskStatus.ESCALATED  # Path B: top-level status
    assert t.block_kind is None
    assert t.note == "needs founder"

    escalations = [a for a in db.get_audit_logs("T-ROOT") if a["action"] == "escalation"]
    assert any("needs founder" in e["payload"]["reason"] for e in escalations)


def test_run_step_delegate_spawns_child_and_blocks_self(
    runtime, db, monkeypatch,
):
    import asyncio
    import json
    from runtime.orchestrator.orchestrator import Orchestrator

    (runtime.workspaces_dir / "dev_agent").mkdir(parents=True)

    db.insert_task(TaskRecord(id="T-1", brief="root",
                              assigned_agent="engineering_head"))
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    q = _SlugQueue()
    orch._queue = q

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary=json.dumps({
                "action": "delegate",
                "agent": "dev_agent",
                "prompt": "Write a PR",
            }),
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    orch.run_step("T-1")

    # Parent now in_progress(DELEGATED) — Path B: a parent waiting on its own
    # child is in progress, with the waiting reason kept in block_kind.
    parent = db.get_task("T-1")
    assert parent.status == TaskStatus.IN_PROGRESS
    assert parent.block_kind == BlockKind.DELEGATED
    assert "dev_agent" in (parent.note or "")

    # Exactly one child exists, is pending, and is enqueued
    children = db.get_children("T-1")
    assert len(children) == 1
    child_id = children[0]
    child = db.get_task(child_id)
    assert child.status == TaskStatus.PENDING
    assert child.assigned_agent == "dev_agent"
    assert child.brief == "Write a PR"
    assert child.parent_task_id == "T-1"
    assert q.get_nowait() == ("test", child_id)


def test_run_step_delegate_inherits_session_timeout(runtime, db, monkeypatch):
    """A delegated child copies the parent's session_timeout_seconds so a
    revisit-time bump propagates down the whole lineage."""
    import asyncio
    import json
    from runtime.orchestrator.orchestrator import Orchestrator

    (runtime.workspaces_dir / "dev_agent").mkdir(parents=True)

    db.insert_task(TaskRecord(
        id="T-1", brief="root", assigned_agent="engineering_head",
        session_timeout_seconds=7200,
    ))
    orch = Orchestrator(
        db=db, settings=Settings(),
        paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root),
    )
    orch._queue = _SlugQueue()

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary=json.dumps({
                "action": "delegate", "agent": "dev_agent", "prompt": "Do it",
            }),
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    orch.run_step("T-1")

    children = db.get_children("T-1")
    assert len(children) == 1
    child = db.get_task(children[0])
    assert child.session_timeout_seconds == 7200


def test_run_step_invalid_delegate_fails_task(runtime, db, monkeypatch):
    """A delegate with no agent name is unrecoverable — fail the task and
    notify the parent (which may itself be root — no-op in that case)."""
    import asyncio
    import json
    from runtime.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-1", brief="x",
                              assigned_agent="engineering_head"))
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary=json.dumps({"action": "delegate", "prompt": "x"}),
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    orch.run_step("T-1")
    t = db.get_task("T-1")
    assert t.status == TaskStatus.FAILED
    assert t.note and "invalid delegate" in t.note


def test_run_step_session_failure_cascades_to_parent_no_retry(
    runtime, db, monkeypatch,
):
    """TASK-573 bounded failure-recovery: when a delegated subtask fails,
    the parent gets a bounded manager-wake decision step (enqueued),
    NOT cascade-failed. TASK-3604: no auto-revisit successor is spawned."""
    import asyncio
    from runtime.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-PAR", brief="p",
                              assigned_agent="engineering_head",
                              task_type="task"))
    db.update_task("T-PAR", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(
        id="T-CHD", brief="c",
        assigned_agent="engineering_head", parent_task_id="T-PAR",
        task_type="subtask",
    ))

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    q = _SlugQueue()
    orch._queue = q

    monkeypatch.setattr(orch, "_run_agent",
                        lambda *a, **k: (_make_result(success=False), None))

    orch.run_step("T-CHD")

    child = db.get_task("T-CHD")
    assert child.status == TaskStatus.FAILED
    assert "session failed" in (child.note or "")

    # Parent stays in_progress(delegated) for bounded manager-wake (TASK-573).
    parent = db.get_task("T-PAR")
    assert parent.status == TaskStatus.IN_PROGRESS
    assert parent.block_kind == BlockKind.DELEGATED
    # TASK-3604: queue holds ONLY the parent re-enqueue (decision step).
    # No auto-revisit successor root is spawned.
    assert q.qsize() == 1
    slug1, tid1 = q.get_nowait()
    assert slug1 == "test"
    assert tid1 == "T-PAR"


def test_run_step_session_failure_cascades_up_chain(
    runtime, db, monkeypatch,
):
    """TASK-573 bounded failure-recovery: a failing grandchild wakes its
    immediate parent for a decision step (not cascade-fail). The chain no
    longer bubbles FAILED status up — each parent wakes independently."""
    import asyncio
    from runtime.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-ROOT", brief="r",
                              assigned_agent="engineering_head",
                              task_type="task"))
    db.update_task("T-ROOT", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(
        id="T-MID", brief="m",
        assigned_agent="engineering_head", parent_task_id="T-ROOT",
        task_type="task",
    ))
    db.update_task("T-MID", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(
        id="T-LEAF", brief="l",
        assigned_agent="dev_agent", parent_task_id="T-MID",
        task_type="subtask",
    ))

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()
    monkeypatch.setattr(orch, "_run_agent",
                        lambda *a, **k: (_make_result(success=False), None))

    orch.run_step("T-LEAF")

    # T-LEAF is FAILED (opaque session failure).
    assert db.get_task("T-LEAF").status == TaskStatus.FAILED
    # T-MID stays in_progress(delegated) — bounded manager-wake.
    assert db.get_task("T-MID").status == TaskStatus.IN_PROGRESS
    assert db.get_task("T-MID").block_kind == BlockKind.DELEGATED
    # T-ROOT stays in_progress(delegated) — not reachable until T-MID advances.
    assert db.get_task("T-ROOT").status == TaskStatus.IN_PROGRESS
    assert db.get_task("T-ROOT").block_kind == BlockKind.DELEGATED
    # TASK-3604: queue holds ONLY T-MID bounded-wake enqueue.
    # No auto-revisit successor root is spawned.
    assert orch._queue.qsize() == 1
    slug_mid, tid_mid = orch._queue.get_nowait()
    assert tid_mid == "T-MID"


def test_run_step_session_failure_note_includes_diagnostics(
    runtime, db, monkeypatch,
):
    """The `agent session failed` note must include rc and a stderr tail
    so post-mortems don't need to grep daemon.log. TASK-044/045 class of
    failure (subprocess exits without calling back) is the motivating case.
    """
    from runtime.orchestrator.executors import ExecutorResult
    from runtime.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-1", brief="x",
                              assigned_agent="engineering_head"))
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    import asyncio
    orch._queue = _SlugQueue()

    result = ExecutorResult(
        success=True,  # rc=0 but no report — the TASK-045 signature
        duration_seconds=703,
        session_id="sess-x",
        returncode=0,
        stdout_tail="wrote ExplorePage.tsx\n",
        stderr_tail="",
    )
    monkeypatch.setattr(orch, "_run_agent", lambda *a, **k: (result, None))

    orch.run_step("T-1")

    note = db.get_task("T-1").note or ""
    assert "rc=0" in note
    assert "no completion callback" in note
    assert "wrote ExplorePage.tsx" in note


def test_run_step_persists_observed_claude_session_limit_reason(
    runtime, db, monkeypatch, tmp_path,
):
    """Shipping executor parsing feeds the persisted task failure note."""
    from unittest.mock import MagicMock

    from runtime.orchestrator.executors import (
        _parse_claude_session_limit_notice, _parse_claude_terminal_error, _run_command,
    )
    from runtime.orchestrator.orchestrator import Orchestrator
    import runtime.orchestrator.executors as executors

    db.insert_task(TaskRecord(id="T-1", brief="x", assigned_agent="engineering_head"))
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()
    proc = MagicMock(pid=4242, returncode=1)
    fixture = Path(__file__).parent / "fixtures" / "claude-task6941-result.sanitized.json"
    envelope = json.loads(fixture.read_text())
    # The full checked-in fixture supplies the observed shape.  Place a long
    # later property after its result to prove the selected notice is not an
    # incidental stdout-tail substring.
    envelope["later_diagnostic"] = "x" * 3_000
    notice = envelope["result"]
    proc.communicate.return_value = (
        json.dumps(envelope, ensure_ascii=False),
        "Ignoring 1 permissions.allow entry from .claude/settings.json: this workspace has not been trusted.\n",
    )
    monkeypatch.setattr(executors.subprocess, "Popen", lambda *a, **k: proc)
    result = _run_command(
        ["claude", "-p", "x"], tmp_path, "sess-limit", 30,
        error_parser=_parse_claude_terminal_error,
        terminal_error_notice_parser=_parse_claude_session_limit_notice,
    )
    monkeypatch.setattr(orch, "_run_agent", lambda *a, **k: (result, None))

    orch.run_step("T-1")

    note = db.get_task("T-1").note or ""
    assert "session_limit" in note
    assert notice in note
    assert "this workspace has not been trusted" not in note
    assert result.rate_limited is False


def test_run_step_meaningful_stderr_keeps_structured_reset_notice(
    runtime, db, monkeypatch, tmp_path,
):
    """Human stderr wins, while stdout retains the session reset notice."""
    from unittest.mock import MagicMock

    from runtime.orchestrator.executors import (
        _parse_claude_session_limit_notice, _parse_claude_terminal_error, _run_command,
    )
    from runtime.orchestrator.orchestrator import Orchestrator
    import runtime.orchestrator.executors as executors

    db.insert_task(TaskRecord(id="T-1", brief="x", assigned_agent="engineering_head"))
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()
    proc = MagicMock(pid=4242, returncode=1)
    notice = "You've hit your session limit · resets 12:20am (Asia/Shanghai)"
    proc.communicate.return_value = (
        '{"type":"result","subtype":"success","is_error":true,'
        '"terminal_reason":"api_error","api_error_status":429,'
        f'"result":"{notice}"}}',
        "API Error: 529 Overloaded\n" + (
            "Ignoring 1 permissions.allow entry from .claude/settings.json: "
            "this workspace has not been trusted.\n"
        ) * 50,
    )
    monkeypatch.setattr(executors.subprocess, "Popen", lambda *a, **k: proc)
    result = _run_command(
        ["claude", "-p", "x"], tmp_path, "sess-limit", 30,
        error_parser=_parse_claude_terminal_error,
        terminal_error_notice_parser=_parse_claude_session_limit_notice,
    )
    monkeypatch.setattr(orch, "_run_agent", lambda *a, **k: (result, None))

    orch.run_step("T-1")

    note = db.get_task("T-1").note or ""
    assert note.index("API Error: 529 Overloaded") < note.index("terminal_error: session_limit")
    assert notice in note
    assert result.human_error == "API Error: 529 Overloaded"


def test_run_step_all_benign_full_stderr_uses_terminal_reason_not_tail(
    runtime, db, monkeypatch, tmp_path,
):
    """A cap-boundary benign tail cannot replace selected empty stderr."""
    from unittest.mock import MagicMock
    from runtime.orchestrator.executors import (
        _parse_claude_session_limit_notice, _parse_claude_terminal_error, _run_command,
    )
    from runtime.orchestrator.orchestrator import Orchestrator
    import runtime.orchestrator.executors as executors

    db.insert_task(TaskRecord(id="T-1", brief="x", assigned_agent="engineering_head"))
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()
    fixture = Path(__file__).parent / "fixtures" / "claude-task6941-result.sanitized.json"
    payload = json.loads(fixture.read_text())
    # Ensure the notice is selected from the complete result before tailing.
    payload["later_diagnostic"] = "x" * 3000
    proc = MagicMock(pid=4242, returncode=1)
    proc.communicate.return_value = (json.dumps(payload), "Set hasTrustDialogAccepted to true to trust this workspace.\n" * 50)
    monkeypatch.setattr(executors.subprocess, "Popen", lambda *a, **k: proc)
    result = _run_command(["claude", "-p", "x"], tmp_path, "sess-limit", 30,
        error_parser=_parse_claude_terminal_error,
        terminal_error_notice_parser=_parse_claude_session_limit_notice)
    monkeypatch.setattr(orch, "_run_agent", lambda *a, **k: (result, None))
    orch.run_step("T-1")
    note = db.get_task("T-1").note or ""
    assert result.human_error is None and result.human_error_inspected is True
    assert "stderr:" not in note
    assert "terminal_error: session_limit" in note
    assert "resets 12:20am" in note
    assert note.index("terminal_error: session_limit") < note.index("stdout:")


def test_run_step_opaque_failure_no_auto_revisit(
    runtime, db, monkeypatch,
):
    """TASK-3604: When a delegated subtask hits an opaque failure, the task
    is marked FAILED but NO auto-revisit successor root is created. The
    failure note includes diagnostics (rc, missing callback flag, stdout
    tail) so post-mortems don't need to grep daemon.log."""
    import asyncio
    from runtime.orchestrator.executors import ExecutorResult
    from runtime.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-PAR", brief="parent brief",
                              team="engineering",
                              assigned_agent="engineering_head"))
    db.update_task("T-PAR", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(
        id="T-CHD", brief="c", team="engineering",
        assigned_agent="dev_agent", parent_task_id="T-PAR",
    ))

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    failing_result = ExecutorResult(
        success=True,  # rc=0 but no callback — TASK-045 class
        duration_seconds=120,
        session_id="sess-x",
        returncode=0,
        stdout_tail="wrote ExplorePage.tsx\n",
        stderr_tail="",
    )
    monkeypatch.setattr(orch, "_run_agent",
                        lambda *a, **k: (failing_result, None))

    orch.run_step("T-CHD")

    # Child is FAILED with diagnostic note.
    child = db.get_task("T-CHD")
    assert child.status == TaskStatus.FAILED
    assert "no completion callback" in (child.note or "")
    assert "wrote ExplorePage.tsx" in (child.note or "")

    # TASK-3604: NO auto-revisit successor root.
    # Queue holds ONLY the parent re-enqueue for bounded-wake.
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert slug == "test"
    assert tid == "T-PAR"

    # No auto_revisit_of audit row anywhere.
    all_audit = db.fetch_all_readonly(
        "SELECT action FROM audit_log "
        "WHERE action IN ('auto_revisit_of', 'revisit_spawned')"
    )
    assert len(all_audit) == 0


def test_run_step_opaque_failure_on_root_manager_no_auto_revisit(
    runtime, db, monkeypatch,
):
    """TASK-3604: Manager-level opaque failure (root task itself crashes)
    marks the task FAILED but spawns NO auto-revisit successor."""
    import asyncio
    from runtime.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-ROOT", brief="root brief",
                              team="engineering",
                              assigned_agent="engineering_head"))

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    monkeypatch.setattr(orch, "_run_agent",
                        lambda *a, **k: (_make_result(success=False), None))

    orch.run_step("T-ROOT")

    # Task is FAILED.
    assert db.get_task("T-ROOT").status == TaskStatus.FAILED

    # TASK-3604: no successor root is spawned.
    assert orch._queue.qsize() == 0

    # No auto_revisit_of audit anywhere.
    all_audit = db.fetch_all_readonly(
        "SELECT action FROM audit_log "
        "WHERE action IN ('auto_revisit_of', 'revisit_spawned')"
    )
    assert len(all_audit) == 0


def test_run_step_opaque_failure_on_exception_no_auto_revisit(
    runtime, db, monkeypatch,
):
    """TASK-3604: Exception escaping _run_agent marks the task FAILED
    but spawns NO auto-revisit successor."""
    import asyncio
    from runtime.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-1", brief="x",
                              assigned_agent="engineering_head"))
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    def boom(task_id, agent, prompt, on_session_started=None):
        raise RuntimeError("workspace not initialized")

    monkeypatch.setattr(orch, "_run_agent", boom)

    orch.run_step("T-1")

    # Task is FAILED with diagnostic note.
    t = db.get_task("T-1")
    assert t.status == TaskStatus.FAILED
    assert "agent invocation failed" in (t.note or "")
    assert "workspace not initialized" in (t.note or "")

    # TASK-3604: no successor root is spawned.
    assert orch._queue.qsize() == 0

    # No auto_revisit_of audit anywhere.
    all_audit = db.fetch_all_readonly(
        "SELECT action FROM audit_log "
        "WHERE action IN ('auto_revisit_of', 'revisit_spawned')"
    )
    assert len(all_audit) == 0


def test_run_step_terminal_failed_no_successor_with_legacy_audit(
    runtime, db, monkeypatch,
):
    """After 2 prior auto-revisits in the audit chain, opaque failure is
    terminal FAILED with no successor spawned. Legacy audit entries
    (auto_revisit_of) are preserved as historical fixtures but the terminal
    failure creates no new revisit. Since TASK-3604, auto-revisit spawning is
    removed; the queue stays empty."""
    import asyncio
    from runtime.orchestrator.orchestrator import Orchestrator

    # Chain: T-ORIG <- T-AR1 (auto-revisit of T-ORIG) <- T-AR2 (auto of T-AR1)
    # T-AR2 is the current task; if it fails, no more auto-revisits.
    db.insert_task(TaskRecord(id="T-ORIG", brief="b",
                              assigned_agent="engineering_head",
                              status=TaskStatus.FAILED))
    db.insert_task(TaskRecord(
        id="T-AR1", brief="b", assigned_agent="engineering_head",
        revisit_of_task_id="T-ORIG", status=TaskStatus.FAILED,
    ))
    db.insert_task(TaskRecord(
        id="T-AR2", brief="b", assigned_agent="engineering_head",
        revisit_of_task_id="T-AR1",
    ))
    # Mark T-AR1 and T-AR2 as auto-revisits in the audit log (historical
    # fixtures — TASK-3604 removed auto-revisit spawning).
    from runtime.infrastructure.audit_logger import AuditLogger
    audit = AuditLogger(db)
    audit.log_auto_revisit_of(
        task_id="T-AR1", predecessor_root="T-ORIG",
        failed_task="T-ORIG", failed_agent="engineering_head",
        cascade=["T-ORIG"],
        failure_kind="session_failed",
        error_context={"mode": "session_failure"},
        attempt=1,
    )
    audit.log_auto_revisit_of(
        task_id="T-AR2", predecessor_root="T-AR1",
        failed_task="T-AR1", failed_agent="engineering_head",
        cascade=["T-AR1"],
        failure_kind="session_failed",
        error_context={"mode": "session_failure"},
        attempt=2,
    )

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()
    monkeypatch.setattr(orch, "_run_agent",
                        lambda *a, **k: (_make_result(success=False), None))

    orch.run_step("T-AR2")

    # T-AR2 fails; no further auto-revisit is spawned.
    assert db.get_task("T-AR2").status == TaskStatus.FAILED
    assert orch._queue.qsize() == 0


def test_run_step_self_blocked_does_not_spawn_auto_revisit(
    runtime, db, monkeypatch,
):
    """Self-blocked is a deliberate agent decision — not an opaque failure.
    No auto-revisit should be spawned."""
    import asyncio
    from runtime.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-1", brief="x",
                              assigned_agent="engineering_head"))
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    monkeypatch.setattr(orch, "_run_agent",
                        lambda *a, **k: (_make_result(),
                                         _make_report("blocked on prereq",
                                                      status="blocked")))

    orch.run_step("T-1")

    assert db.get_task("T-1").status == TaskStatus.FAILED
    assert orch._queue.qsize() == 0


def test_run_step_auto_revisit_header_injected_on_first_step(
    runtime, db, monkeypatch,
):
    """The team manager's first prompt on the auto-revisit root must
    include AUTO-REVISIT CONTEXT with the structured error payload."""
    import asyncio
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.run_step import _build_agent_prompt

    db.insert_task(TaskRecord(id="T-PAR", brief="parent brief",
                              team="engineering",
                              assigned_agent="engineering_head",
                              status=TaskStatus.FAILED))
    db.insert_task(TaskRecord(
        id="T-NEW", brief="parent brief", team="engineering",
        assigned_agent="engineering_head",
        revisit_of_task_id="T-PAR",
    ))
    from runtime.infrastructure.audit_logger import AuditLogger
    AuditLogger(db).log_auto_revisit_of(
        task_id="T-NEW", predecessor_root="T-PAR",
        failed_task="T-CHD", failed_agent="dev_agent",
        cascade=["T-PAR", "T-CHD"],
        failure_kind="no_callback",
        error_context={
            "mode": "session_failure", "rc": 0, "missing_callback": True,
            "stderr_tail": "", "stdout_tail": "wrote files",
            "executor_error": None,
        },
        attempt=1,
    )

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    task = db.get_task("T-NEW")
    prompt = _build_agent_prompt(orch, task, "engineering_head")
    assert "AUTO-REVISIT CONTEXT" in prompt
    assert "T-PAR" in prompt
    assert "T-CHD" in prompt
    assert "dev_agent" in prompt
    assert "no completion callback" in prompt
    assert "wrote files" in prompt
    # Shared discipline tail (TALK-028): manager must status-assess and choose
    # execute-with-divergence-note vs escalate, not improvise.
    assert "Status-assess before acting" in prompt
    assert "Do NOT improvise" in prompt


def test_build_agent_prompt_subtask_includes_blocked_jobs_resume_header(
    runtime, db,
):
    """A resumed delegated subtask receives its job-outcome pointer."""
    from runtime.infrastructure.audit_logger import AuditLogger
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.run_step import _build_agent_prompt

    db.insert_task(TaskRecord(
        id="T-SUB", brief="wait for JOB-1", team="engineering",
        assigned_agent="dev_agent", task_type="subtask", parent_task_id="T-PAR",
    ))
    AuditLogger(db).log_task_resumed_from_jobs(
        task_id="T-SUB",
        blocking_job_ids=["JOB-1"],
        trigger="job_terminal",
        triggering_job_id="JOB-1",
        job_outcomes={"JOB-1": "completed"},
    )

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    task = db.get_task("T-SUB")
    prompt = _build_agent_prompt(orch, task, "dev_agent")

    assert "BLOCKED-JOBS-RESULTS" in prompt
    assert "JOB-1" in prompt


def test_run_step_worker_self_blocked_fails_task(runtime, db, monkeypatch):
    import asyncio
    from runtime.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-1", brief="x",
                              assigned_agent="engineering_head"))
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    monkeypatch.setattr(orch, "_run_agent",
                        lambda *a, **k: (_make_result(), _make_report(
                            output_summary="ran out of tokens", status="blocked")))

    orch.run_step("T-1")
    t = db.get_task("T-1")
    assert t.status == TaskStatus.FAILED
    assert t.note and t.note.startswith("self-blocked:")


def test_run_step_worker_completion_is_done_not_parsed_as_eh_decision(
    runtime, db, monkeypatch,
):
    """P1 regression: workers don't speak the NextStep JSON protocol. A plain
    prose output_summary from a delegated worker must be treated as `done`,
    not escalated as "non-JSON EH decision"."""
    import asyncio
    from runtime.orchestrator.orchestrator import Orchestrator

    # Parent (EH) delegated to dev_agent (worker).
    db.insert_task(TaskRecord(id="T-PAR", brief="p",
                              assigned_agent="engineering_head"))
    db.update_task("T-PAR", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(
        id="T-CHD", brief="c",
        assigned_agent="dev_agent", parent_task_id="T-PAR",
        task_type="subtask",
    ))

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    q = _SlugQueue()
    orch._queue = q

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary="Shipped the PR — see branch feat/x",
            output_dir="output/run-1",
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    orch.run_step("T-CHD")

    child = db.get_task("T-CHD")
    assert child.status == TaskStatus.COMPLETED
    assert child.block_kind is None
    assert child.note == "Shipped the PR — see branch feat/x"
    assert child.final_output_dir == "output/run-1"
    # Parent wakes on the child terminal.
    assert q.get_nowait() == ("test", "T-PAR")


def test_run_step_delegated_worker_emits_review_verdict(
    runtime, db, monkeypatch,
):
    """P1 regression: tiers are computed from review_verdict audit rows. When
    a delegated worker reaches a terminal state, the EH's implicit verdict
    (approved on COMPLETED, rejected on FAILED) must be logged — otherwise
    every delegated agent stays on stale performance data."""
    import asyncio
    from runtime.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-PAR", brief="p",
                              assigned_agent="engineering_head"))
    db.update_task("T-PAR", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(
        id="T-OK", brief="ok",
        assigned_agent="dev_agent", parent_task_id="T-PAR",
        task_type="subtask",
    ))
    db.insert_task(TaskRecord(
        id="T-BAD", brief="bad",
        assigned_agent="dev_agent", parent_task_id="T-PAR",
        task_type="subtask",
    ))

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    # Success path.
    monkeypatch.setattr(orch, "_run_agent",
                        lambda *a, **k: (_make_result(), _make_report(
                            output_summary="done")))
    orch.run_step("T-OK")

    # Failure path (session failed, no report).
    monkeypatch.setattr(orch, "_run_agent",
                        lambda *a, **k: (_make_result(success=False), None))
    orch.run_step("T-BAD")

    ok_verdicts = [a for a in db.get_audit_logs("T-OK")
                   if a["action"] == "review_verdict"]
    bad_verdicts = [a for a in db.get_audit_logs("T-BAD")
                    if a["action"] == "review_verdict"]
    assert len(ok_verdicts) == 1
    assert ok_verdicts[0]["agent"] == "engineering_head"
    assert ok_verdicts[0]["payload"]["verdict"] == "approved"
    assert ok_verdicts[0]["payload"]["reviewed_agent"] == "dev_agent"
    assert len(bad_verdicts) == 1
    assert bad_verdicts[0]["payload"]["verdict"] == "rejected"
    assert bad_verdicts[0]["payload"]["reviewed_agent"] == "dev_agent"


def test_run_step_delegated_reviewer_request_changes_verdict(runtime, db, monkeypatch):
    """A delegated non-manager reviewer that reports status=completed with an
    explicit structured verdict=REQUEST_CHANGES must finish COMPLETED (the
    completion status is a distinct fact) while its review_verdict audit
    payload carries the reported REQUEST_CHANGES — never an inferred approved."""
    import asyncio
    from runtime.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-PAR", brief="p",
                              assigned_agent="engineering_head"))
    db.update_task("T-PAR", status=TaskStatus.IN_PROGRESS,
                   block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(
        id="T-REV", brief="review",
        assigned_agent="qa_engineer", parent_task_id="T-PAR",
        task_type="subtask",
    ))

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    # Production seam: the agent callback persists the completion report
    # (task_results) before run_step classifies the terminal transition.
    def _run_agent_persisting(*a, **k):
        db.insert_task_result(
            task_id="T-REV", agent="qa_engineer", session_id="sess-x",
            status="completed", confidence_score=80,
            output_summary="needs changes", verdict="REQUEST_CHANGES",
        )
        return _make_result(), _make_report(
            output_summary="needs changes", verdict="REQUEST_CHANGES")
    monkeypatch.setattr(orch, "_run_agent", _run_agent_persisting)

    orch.run_step("T-REV")

    child = db.get_task("T-REV")
    assert child.status == TaskStatus.COMPLETED
    assert child.note == "needs changes"

    verdicts = [a for a in db.get_audit_logs("T-REV")
                if a["action"] == "review_verdict"]
    assert len(verdicts) == 1
    assert verdicts[0]["payload"]["verdict"] == "REQUEST_CHANGES"
    assert verdicts[0]["payload"]["reviewed_agent"] == "qa_engineer"


def test_run_step_delegated_completed_no_verdict_falls_back_approved(
    runtime, db, monkeypatch,
):
    """A delegated worker reporting completed WITHOUT a structured verdict keeps
    the legacy implicit mapping (approved)."""
    from runtime.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-PAR", brief="p",
                              assigned_agent="engineering_head"))
    db.update_task("T-PAR", status=TaskStatus.IN_PROGRESS,
                   block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(
        id="T-OK", brief="ok",
        assigned_agent="dev_agent", parent_task_id="T-PAR",
        task_type="subtask",
    ))

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    monkeypatch.setattr(orch, "_run_agent",
                        lambda *a, **k: (_make_result(), _make_report(
                            output_summary="done")))
    orch.run_step("T-OK")

    verdicts = [a for a in db.get_audit_logs("T-OK")
                if a["action"] == "review_verdict"]
    assert len(verdicts) == 1
    assert verdicts[0]["payload"]["verdict"] == "approved"


def test_run_step_root_eh_task_skips_review_verdict(runtime, db, monkeypatch):
    """Root tasks (no parent) are EH-assigned and must NOT produce verdict
    rows — the EH is not reviewing itself."""
    import asyncio
    import json
    from runtime.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-ROOT", brief="r",
                              assigned_agent="engineering_head"))
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    monkeypatch.setattr(orch, "_run_agent",
                        lambda *a, **k: (_make_result(), _make_report(
                            output_summary=json.dumps(
                                {"action": "done", "summary": "ok"}))))
    orch.run_step("T-ROOT")

    verdicts = [a for a in db.get_audit_logs("T-ROOT")
                if a["action"] == "review_verdict"]
    assert verdicts == []


def test_run_step_skips_task_with_cancelled_at(runtime, db, monkeypatch):
    """Entry guard: once /cancel stamps cancelled_at on a row, a late queue
    entry must be a silent no-op — no in_progress transition, no _run_agent
    call, no step-count increment. The row stays exactly as /cancel left it."""
    from datetime import datetime, timezone

    from runtime.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(
        id="T-CNL", brief="x",
        assigned_agent="engineering_head",
    ))
    # /cancel's phase-1 writes: FAILED + cancelled_at + founder note.
    now = datetime.now(timezone.utc).isoformat()
    db.update_task(
        "T-CNL",
        status=TaskStatus.FAILED,
        block_kind=None,
        note="cancelled by founder: enough",
        cancelled_at=now,
        completed_at=now,
    )

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    called = {"n": 0}
    def sentinel(*a, **k):
        called["n"] += 1
        raise AssertionError("_run_agent must not be called after cancel")
    monkeypatch.setattr(orch, "_run_agent", sentinel)

    orch.run_step("T-CNL")

    t = db.get_task("T-CNL")
    assert t.status == TaskStatus.FAILED
    assert t.note == "cancelled by founder: enough"
    assert t.cancelled_at is not None
    assert t.orchestration_step_count == 0
    assert called["n"] == 0


def test_fail_idempotent_on_terminal_task(runtime, db):
    """The post-Popen classifier must not overwrite the founder's note.
    After /cancel flips the row to FAILED, a stray _fail() call (from the
    run_step that was mid-flight when SIGTERM arrived) must no-op."""
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.run_step import _fail

    db.insert_task(TaskRecord(id="T-1", brief="x",
                              assigned_agent="dev_agent"))
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    db.update_task("T-1", status=TaskStatus.FAILED, block_kind=None,
                   note="cancelled by founder: stop", cancelled_at=now,
                   completed_at=now)

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    _fail(orch, "T-1", note="agent session failed rc=-15")

    t = db.get_task("T-1")
    assert t.status == TaskStatus.FAILED
    assert t.note == "cancelled by founder: stop"  # unchanged


def test_complete_idempotent_on_terminal_task(runtime, db):
    """If the subprocess happened to finish cleanly just before SIGTERM,
    _complete must not resurrect the cancelled row back to COMPLETED."""
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.run_step import _complete

    db.insert_task(TaskRecord(id="T-1", brief="x",
                              assigned_agent="dev_agent"))
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    db.update_task("T-1", status=TaskStatus.FAILED, block_kind=None,
                   note="cancelled by founder: stop", cancelled_at=now,
                   completed_at=now)

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    _complete(orch, "T-1", note="looks great", output_dir="output/run-1")

    t = db.get_task("T-1")
    assert t.status == TaskStatus.FAILED
    assert t.note == "cancelled by founder: stop"
    assert t.final_output_dir is None  # unchanged


def test_terminal_worktree_skill_contract_is_durable_before_final_callback() -> None:
    root = Path(__file__).parents[1]
    start_task = (root / "runtime/skills/bundled/start-task/SKILL.md").read_text()
    make_worktree = (root / "runtime/skills/bundled/make-worktree/SKILL.md").read_text()

    assert "8. **Cleanup or record deferral.**" in start_task
    assert "9. **Report completion.**" in start_task
    assert start_task.index("8. **Cleanup or record deferral.**") < start_task.index(
        "9. **Report completion.**"
    )
    assert "report-completion is the final action" in start_task
    cleanup = make_worktree.split("## Cleanup", 1)[1]
    assert "git worktree remove .claude/worktrees/<task_id>" in cleanup
    assert "--force" not in cleanup
    assert "git branch -D" not in cleanup
    assert "worktree-deferred: <specific reason>" in cleanup


def test_complete_reclaims_clean_durable_terminal_worktree(
    runtime, db, monkeypatch,
):
    from runtime.orchestrator.run_step import _complete

    task_id = "TASK-9001"
    orch, primary, candidate = _terminal_worktree(runtime, db, task_id)
    _admit_terminal_worktree(monkeypatch)
    outcomes = _record_terminal_worktree_outcomes(monkeypatch)

    assert _complete(orch, task_id, note="done") is True

    assert db.get_task(task_id).status is TaskStatus.COMPLETED
    assert outcomes == [("removed", "eligible")]
    assert not candidate.exists()
    assert _git(
        primary, "show-ref", "--verify", f"refs/heads/task/{task_id}",
    ).returncode == 0


def test_fail_reclaims_clean_durable_terminal_worktree(runtime, db, monkeypatch):
    from runtime.orchestrator.run_step import _fail

    task_id = "TASK-9002"
    orch, _primary, candidate = _terminal_worktree(runtime, db, task_id)
    _admit_terminal_worktree(monkeypatch)
    outcomes = _record_terminal_worktree_outcomes(monkeypatch)

    _fail(orch, task_id, note="failed")

    assert db.get_task(task_id).status is TaskStatus.FAILED
    assert outcomes == [("removed", "eligible")]
    assert not candidate.exists()


@pytest.mark.parametrize(
    "dirty_kind, task_id",
    [
        ("unstaged", "TASK-9011"),
        ("staged", "TASK-9012"),
        ("untracked", "TASK-9013"),
    ],
)
def test_fail_preserves_every_dirty_worktree_form(
    runtime, db, monkeypatch, dirty_kind, task_id,
):
    from runtime.orchestrator.run_step import _fail

    orch, _primary, candidate = _terminal_worktree(runtime, db, task_id)
    _admit_terminal_worktree(monkeypatch)
    outcomes = _record_terminal_worktree_outcomes(monkeypatch)
    if dirty_kind == "unstaged":
        (candidate / "tracked.txt").write_text("changed\n")
    elif dirty_kind == "staged":
        (candidate / "tracked.txt").write_text("changed\n")
        _git(candidate, "add", "tracked.txt")
    else:
        (candidate / "untracked.txt").write_text("new\n")

    _fail(orch, task_id, note="failed")

    assert db.get_task(task_id).status is TaskStatus.FAILED
    assert outcomes == [("preserved", "worktree-dirty")]
    assert candidate.exists()


def test_terminal_worktree_preserves_unpushed_commit(runtime, db, monkeypatch):
    from runtime.orchestrator.run_step import _fail

    task_id = "TASK-9004"
    orch, _primary, candidate = _terminal_worktree(runtime, db, task_id)
    _admit_terminal_worktree(monkeypatch)
    outcomes = _record_terminal_worktree_outcomes(monkeypatch)
    (candidate / "tracked.txt").write_text("published later\n")
    _git(candidate, "add", "tracked.txt")
    _git(candidate, "commit", "-m", "local only")

    _fail(orch, task_id, note="failed")

    assert outcomes == [("preserved", "commit-not-durable")]
    assert candidate.exists()


@pytest.mark.parametrize(
    "gh_result",
    [
        subprocess.CompletedProcess(
            ["gh"], 0, '[{"number": 887, "state": "OPEN"}]\n', "",
        ),
        subprocess.CompletedProcess(["gh"], 1, "", "auth unavailable"),
        subprocess.CompletedProcess(["gh"], 0, "not-json", ""),
    ],
    ids=["open-pr", "probe-error", "malformed"],
)
def test_terminal_worktree_preserves_open_pr_or_remote_uncertainty(
    runtime, db, monkeypatch, gh_result,
):
    from runtime.orchestrator import run_step as run_step_module
    from runtime.orchestrator.run_step import _fail

    task_id = "TASK-9005"
    orch, _primary, candidate = _terminal_worktree(runtime, db, task_id)
    real_run = run_step_module._run_terminal_worktree_command
    calls = []

    def fake_run(args, *, cwd, timeout):
        if args[0] == "gh":
            calls.append(tuple(args))
            return gh_result
        return real_run(args, cwd=cwd, timeout=timeout)

    monkeypatch.setattr(run_step_module, "_run_terminal_worktree_command", fake_run)
    monkeypatch.setattr(
        run_step_module, "_terminal_worktree_process_reference",
        lambda candidate, deadline: None,
    )
    outcomes = _record_terminal_worktree_outcomes(monkeypatch)

    _fail(orch, task_id, note="failed")

    assert candidate.exists()
    assert len(calls) == 1
    expected_reason = (
        "unmerged-pull-request" if gh_result.returncode == 0
        and gh_result.stdout.startswith("[") else "pull-request-probe-unknown"
    )
    if gh_result.stdout == "not-json":
        expected_reason = "pull-request-probe-unknown"
    assert outcomes == [("preserved", expected_reason)]


def test_terminal_worktree_preserves_live_session_without_process_probe(
    runtime, db, monkeypatch,
):
    from runtime.orchestrator import run_step as run_step_module
    from runtime.orchestrator.run_step import _fail

    task_id = "TASK-9006"
    orch, _primary, candidate = _terminal_worktree(runtime, db, task_id)
    _admit_terminal_worktree(monkeypatch)
    outcomes = _record_terminal_worktree_outcomes(monkeypatch)
    orch._sessions.set_active(task_id, "dev_agent", "sess-live")
    monkeypatch.setattr(
        run_step_module,
        "_terminal_worktree_process_reference",
        lambda *_: pytest.fail("live tracker must preserve before /proc probing"),
    )

    _fail(orch, task_id, note="failed")

    assert outcomes == [("preserved", "live-session")]
    assert candidate.exists()


def test_terminal_worktree_preserves_recorded_deferral(runtime, db, monkeypatch):
    from runtime.orchestrator.run_step import _fail

    task_id = "TASK-9007"
    orch, _primary, candidate = _terminal_worktree(runtime, db, task_id)
    _admit_terminal_worktree(monkeypatch)
    outcomes = _record_terminal_worktree_outcomes(monkeypatch)
    db.insert_task_result(
        task_id=task_id,
        agent="dev_agent",
        session_id="sess-finished",
        status="completed",
        confidence_score=80,
        output_summary="done",
        risks_flagged=["worktree-deferred: open PR"],
    )

    _fail(orch, task_id, note="failed")

    assert outcomes == [("preserved", "recorded-deferral")]
    assert candidate.exists()


def test_terminal_worktree_remove_failure_is_one_shot_and_preserves_branch(
    runtime, db, monkeypatch,
):
    from runtime.orchestrator import run_step as run_step_module
    from runtime.orchestrator.run_step import _fail

    task_id = "TASK-9008"
    orch, primary, candidate = _terminal_worktree(runtime, db, task_id)
    real_run = run_step_module._run_terminal_worktree_command
    removals = []

    def fake_run(args, *, cwd, timeout):
        if args[0] == "gh":
            return subprocess.CompletedProcess(args, 0, "[]\n", "")
        if "worktree" in args and "remove" in args:
            removals.append(tuple(args))
            return subprocess.CompletedProcess(args, 1, "", "busy")
        return real_run(args, cwd=cwd, timeout=timeout)

    monkeypatch.setattr(run_step_module, "_run_terminal_worktree_command", fake_run)
    monkeypatch.setattr(
        run_step_module,
        "_terminal_worktree_process_reference",
        lambda candidate, deadline: None,
    )
    outcomes = _record_terminal_worktree_outcomes(monkeypatch)

    _fail(orch, task_id, note="failed")

    assert candidate.exists()
    assert outcomes == [("preserved", "remove-failed")]
    assert len(removals) == 1
    assert "--force" not in removals[0]
    assert _git(
        primary, "show-ref", "--verify", f"refs/heads/task/{task_id}",
    ).returncode == 0


@pytest.mark.parametrize(
    "tracker_fact, expected_reason",
    [("control", "live-control"), ("pid", "live-pid")],
)
def test_terminal_worktree_preserves_live_tracker_facts(
    runtime, db, monkeypatch, tracker_fact, expected_reason,
):
    from runtime.orchestrator.run_step import _fail

    task_id = f"TASK-902-{tracker_fact}"
    orch, _primary, candidate = _terminal_worktree(runtime, db, task_id)
    _admit_terminal_worktree(monkeypatch)
    outcomes = _record_terminal_worktree_outcomes(monkeypatch)
    orch._sessions.set_active(task_id, "dev_agent", "sess-finished")
    if tracker_fact == "control":
        orch._sessions.set_cancel_control(
            task_id, "dev_agent", "sess-finished", lambda: None,
        )
    else:
        orch._sessions.set_pid(task_id, "dev_agent", "sess-finished", 4242)
    monkeypatch.setattr(orch._sessions, "iter_active", lambda: [])

    _fail(orch, task_id, note="failed")

    assert outcomes == [("preserved", expected_reason)]
    assert candidate.exists()


def test_terminal_worktree_live_process_reference_is_one_shot(
    runtime, db, monkeypatch,
):
    from runtime.orchestrator import run_step as run_step_module
    from runtime.orchestrator.executors import ExecutorResult

    task_id = "TASK-9020"
    orch, _primary, candidate = _terminal_worktree(runtime, db, task_id)
    _admit_terminal_worktree(monkeypatch)
    probes = []
    monkeypatch.setattr(
        run_step_module,
        "_terminal_worktree_process_reference",
        lambda *_: probes.append("probe") or "live-process-reference",
    )
    outcomes = _record_terminal_worktree_outcomes(monkeypatch)
    monkeypatch.setattr(
        orch, "_run_agent",
        lambda *args, **kwargs: (
            ExecutorResult(
                success=False, duration_seconds=1, session_id="sess-timeout",
                error="Session timed out",
            ),
            None,
        ),
    )

    orch.run_step(task_id)

    assert db.get_task(task_id).status is TaskStatus.FAILED
    assert outcomes == [("preserved", "live-process-reference")]
    assert probes == ["probe"]
    assert candidate.exists()


def test_terminal_worktree_deadline_expiry_is_contained_and_not_retried(
    runtime, db, monkeypatch,
):
    from runtime.orchestrator import run_step as run_step_module
    from runtime.orchestrator.run_step import _fail

    task_id = "TASK-9021"
    orch, _primary, candidate = _terminal_worktree(runtime, db, task_id)
    monkeypatch.setattr(
        run_step_module,
        "_terminal_worktree_remaining",
        lambda _deadline: (_ for _ in ()).throw(TimeoutError("expired")),
    )
    outcomes = _record_terminal_worktree_outcomes(monkeypatch)

    _fail(orch, task_id, note="failed")

    assert outcomes == [("preserved", "deadline-expired")]
    assert candidate.exists()


def test_terminal_worktree_probe_exception_cannot_change_terminal_semantics(
    runtime, db, monkeypatch,
):
    from runtime.orchestrator import run_step as run_step_module
    from runtime.orchestrator.run_step import _fail

    task_id = "TASK-9022"
    orch, _primary, candidate = _terminal_worktree(runtime, db, task_id)
    monkeypatch.setattr(
        run_step_module,
        "_run_terminal_worktree_command",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("probe broke")),
    )
    outcomes = _record_terminal_worktree_outcomes(monkeypatch)

    _fail(orch, task_id, note="exact terminal note")

    task = db.get_task(task_id)
    assert task.status is TaskStatus.FAILED
    assert task.note == "exact terminal note"
    assert outcomes == [("preserved", "probe-error")]
    assert candidate.exists()


def test_terminal_worktree_branch_mismatch_is_preserved(
    runtime, db, monkeypatch,
):
    from runtime.orchestrator.run_step import _fail

    task_id = "TASK-9023"
    orch, _primary, candidate = _terminal_worktree(runtime, db, task_id)
    _git(candidate, "branch", "-m", "task/FOREIGN")
    _admit_terminal_worktree(monkeypatch)
    outcomes = _record_terminal_worktree_outcomes(monkeypatch)

    _fail(orch, task_id, note="failed")

    assert outcomes == [("preserved", "worktree-identity-mismatch")]
    assert candidate.exists()


def test_terminal_worktree_unregistered_agent_is_preserved_without_git_probe(
    runtime, db, monkeypatch,
):
    from runtime.orchestrator import run_step as run_step_module
    from runtime.orchestrator.run_step import _fail

    task_id = "TASK-9024"
    orch, _primary, candidate = _terminal_worktree(
        runtime, db, task_id, agent="foreign_agent",
    )
    monkeypatch.setattr(
        run_step_module,
        "_run_terminal_worktree_command",
        lambda *args, **kwargs: pytest.fail("unregistered agent must not probe git"),
    )
    outcomes = _record_terminal_worktree_outcomes(monkeypatch)

    _fail(orch, task_id, note="failed")

    assert outcomes == [("preserved", "agent-unregistered")]
    assert candidate.exists()


def test_terminal_worktree_symlink_candidate_is_preserved(
    runtime, db, monkeypatch,
):
    from runtime.orchestrator import run_step as run_step_module
    from runtime.orchestrator.run_step import _fail

    task_id = "TASK-9025"
    orch, _primary, candidate = _terminal_worktree(runtime, db, task_id)
    moved = candidate.with_name(f"{task_id}-moved")
    candidate.rename(moved)
    candidate.symlink_to(moved, target_is_directory=True)
    monkeypatch.setattr(
        run_step_module,
        "_run_terminal_worktree_command",
        lambda *args, **kwargs: pytest.fail("symlink gate must precede git probes"),
    )
    outcomes = _record_terminal_worktree_outcomes(monkeypatch)

    _fail(orch, task_id, note="failed")

    assert outcomes == [("preserved", "worktree-symlinked")]
    assert candidate.is_symlink()


@pytest.mark.parametrize("decoy_kind", ["primary", "nested", "scratch", "foreign"])
def test_terminal_worktree_never_scans_task_like_decoys(
    runtime, db, monkeypatch, decoy_kind,
):
    from runtime.orchestrator import run_step as run_step_module
    from runtime.orchestrator.run_step import _fail

    task_id = f"TASK-DEC-{decoy_kind}"
    orch, primary, candidate = _terminal_worktree(
        runtime, db, task_id, create_candidate=False,
    )
    if decoy_kind == "primary":
        decoy = primary
    elif decoy_kind == "nested":
        decoy = primary / "nested" / ".claude" / "worktrees" / task_id
        decoy.mkdir(parents=True)
    elif decoy_kind == "scratch":
        decoy = (
            runtime.workspaces_dir / "dev_agent" / ".happyranch"
            / "scratch" / "worktrees" / task_id
        )
        decoy.mkdir(parents=True)
    else:
        decoy = (
            runtime.workspaces_dir / "engineering_head" / "repos"
            / "happyranch" / ".claude" / "worktrees" / task_id
        )
        decoy.mkdir(parents=True)
    monkeypatch.setattr(
        run_step_module,
        "_run_terminal_worktree_command",
        lambda *args, **kwargs: pytest.fail("absent canonical candidate must not scan"),
    )
    outcomes = _record_terminal_worktree_outcomes(monkeypatch)

    _fail(orch, task_id, note="failed")

    assert outcomes == [("preserved", "worktree-absent")]
    assert not candidate.exists()
    assert decoy.exists()


@pytest.mark.parametrize("timeout_stage", ["remote", "remove"])
def test_terminal_worktree_command_timeout_preserves_without_retry(
    runtime, db, monkeypatch, timeout_stage,
):
    from runtime.orchestrator import run_step as run_step_module
    from runtime.orchestrator.run_step import _fail

    task_id = f"TASK-TIMEOUT-{timeout_stage}"
    orch, _primary, candidate = _terminal_worktree(runtime, db, task_id)
    real_run = run_step_module._run_terminal_worktree_command
    timed_calls = []

    def fake_run(args, *, cwd, timeout):
        is_target = (
            timeout_stage == "remote" and args[0] == "gh"
        ) or (
            timeout_stage == "remove" and "worktree" in args and "remove" in args
        )
        if is_target:
            timed_calls.append(tuple(args))
            raise subprocess.TimeoutExpired(args, timeout)
        if args[0] == "gh":
            return subprocess.CompletedProcess(args, 0, "[]\n", "")
        return real_run(args, cwd=cwd, timeout=timeout)

    monkeypatch.setattr(run_step_module, "_run_terminal_worktree_command", fake_run)
    monkeypatch.setattr(
        run_step_module, "_terminal_worktree_process_reference", lambda *_: None,
    )
    outcomes = _record_terminal_worktree_outcomes(monkeypatch)

    _fail(orch, task_id, note="failed")

    assert outcomes == [("preserved", "probe-timeout")]
    assert len(timed_calls) == 1
    assert candidate.exists()


def test_terminal_worktree_duplicate_attempt_observes_absence_without_retry(
    runtime, db, monkeypatch,
):
    from runtime.orchestrator.run_step import (
        _complete,
        _reclaim_terminal_task_worktree,
    )

    task_id = "TASK-9031"
    orch, _primary, candidate = _terminal_worktree(runtime, db, task_id)
    _admit_terminal_worktree(monkeypatch)
    outcomes = _record_terminal_worktree_outcomes(monkeypatch)

    assert _complete(orch, task_id, note="done") is True
    duplicate = _reclaim_terminal_task_worktree(orch, task_id)

    assert outcomes == [("removed", "eligible")]
    assert duplicate == ("preserved", "worktree-absent")
    assert not candidate.exists()


def test_terminal_worktree_attempt_is_scoped_to_exact_task_identity(
    runtime, db, monkeypatch,
):
    from runtime.orchestrator.run_step import _fail

    task_id = "TASK-9026"
    other_id = "TASK-9027"
    orch, primary, candidate = _terminal_worktree(runtime, db, task_id)
    other = primary / ".claude" / "worktrees" / other_id
    _git(primary, "worktree", "add", "-b", f"task/{other_id}", str(other))
    db.insert_task(TaskRecord(
        id=other_id, brief="other", assigned_agent="dev_agent",
        status=TaskStatus.COMPLETED, current_session_id="shared-session",
    ))
    db.update_task(task_id, current_session_id="shared-session")
    _admit_terminal_worktree(monkeypatch)
    outcomes = _record_terminal_worktree_outcomes(monkeypatch)

    _fail(orch, task_id, note="failed")

    assert outcomes == [("removed", "eligible")]
    assert not candidate.exists()
    assert other.exists()


def test_blocked_on_job_is_not_reclaimed_until_later_terminal_transition(
    runtime, db, monkeypatch,
):
    from runtime.models import CompletionReport
    from runtime.orchestrator.run_step import _complete, _consume_completion_report

    task_id = "TASK-9028"
    orch, _primary, candidate = _terminal_worktree(runtime, db, task_id)
    now = datetime.now(timezone.utc).isoformat()
    db.insert_job(JobRecord(
        id="JOB-9028", task_id=task_id, agent_name="dev_agent",
        title="wait", rationale="test", script_text="true",
        interpreter=JobInterpreter.BASH, status=JobStatus.RUNNING,
        created_at=now,
    ))
    _admit_terminal_worktree(monkeypatch)
    outcomes = _record_terminal_worktree_outcomes(monkeypatch)

    _consume_completion_report(
        orch, task_id,
        CompletionReport(
            task_id=task_id, agent="dev_agent", status="blocked",
            confidence=80, output_summary="waiting",
            waiting_on_job_ids=["JOB-9028"],
        ),
    )

    parked = db.get_task(task_id)
    assert parked.status is TaskStatus.IN_PROGRESS
    assert parked.block_kind is BlockKind.BLOCKED_ON_JOB
    assert outcomes == []
    assert candidate.exists()

    db.update_task(task_id, status=TaskStatus.IN_PROGRESS, block_kind=None)
    assert _complete(orch, task_id, note="job finished") is True
    assert outcomes == [("removed", "eligible")]
    assert not candidate.exists()


def test_delegated_child_reclamation_never_considers_parent_worktree(
    runtime, db, monkeypatch,
):
    from runtime.orchestrator.run_step import _complete

    parent_id = "TASK-9029"
    db.insert_task(TaskRecord(
        id=parent_id, brief="parent", assigned_agent="engineering_head",
        status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
    ))
    task_id = "TASK-9030"
    orch, primary, candidate = _terminal_worktree(
        runtime, db, task_id, parent_task_id=parent_id, task_type="subtask",
    )
    parent_candidate = primary / ".claude" / "worktrees" / parent_id
    _git(
        primary, "worktree", "add", "-b", f"task/{parent_id}",
        str(parent_candidate),
    )
    _admit_terminal_worktree(monkeypatch)
    outcomes = _record_terminal_worktree_outcomes(monkeypatch)

    assert _complete(orch, task_id, note="child done") is True

    assert outcomes == [("removed", "eligible")]
    assert not candidate.exists()
    assert parent_candidate.exists()


def test_superseded_worktree_is_ineligible_before_any_probe(runtime, db, monkeypatch):
    from runtime.orchestrator import run_step as run_step_module
    from runtime.orchestrator.run_step import _reclaim_terminal_task_worktree

    task_id = "TASK-9009"
    orch, _primary, candidate = _terminal_worktree(
        runtime, db, task_id, status=TaskStatus.SUPERSEDED,
    )
    monkeypatch.setattr(
        run_step_module,
        "_run_terminal_worktree_command",
        lambda *a, **k: pytest.fail("SUPERSEDED must invoke no probe"),
    )

    outcome = _reclaim_terminal_task_worktree(orch, task_id)

    assert outcome.kind == "preserved"
    assert outcome.reason == "status-ineligible"
    assert candidate.exists()


def test_manager_supersede_shipping_seam_never_calls_reclamation(
    runtime, db, monkeypatch,
):
    task_id = "TASK-9014"
    orch, _primary, candidate = _terminal_worktree(
        runtime, db, task_id, agent="engineering_head",
    )
    db.update_task(
        task_id,
        status=TaskStatus.IN_PROGRESS,
        block_kind=None,
        current_session_id="sess-manager",
    )
    monkeypatch.setattr(
        "runtime.orchestrator.run_step._reclaim_terminal_task_worktree",
        lambda *args: pytest.fail("manager supersession must not reclaim"),
    )

    _consume_manager_supersede(orch, task_id)

    assert db.get_task(task_id).status is TaskStatus.SUPERSEDED
    assert candidate.exists()


def test_accepted_completion_recovery_never_reclaims_worktree(
    runtime, db, monkeypatch,
):
    from runtime.orchestrator.run_step import _consume_completion_report

    task_id = "TASK-9010"
    orch, _primary, candidate = _terminal_worktree(runtime, db, task_id)
    db.update_task(task_id, status=TaskStatus.IN_PROGRESS, block_kind=None)
    calls = []
    monkeypatch.setattr(
        "runtime.orchestrator.run_step._reclaim_terminal_task_worktree",
        lambda *args: calls.append(args),
    )

    _consume_completion_report(
        orch,
        task_id,
        _make_report(output_summary=json.dumps({"action": "done", "summary": "done"})),
        reclaim_terminal_worktree=False,
    )

    assert db.get_task(task_id).status is TaskStatus.COMPLETED
    assert candidate.exists()
    assert calls == []


def test_run_step_revisit_header_injected_on_first_step(
    runtime, db, monkeypatch,
):
    """New-root task with a revisit_of audit entry and no orchestration_step
    entry: EH prompt must start with the revisit context header."""
    from runtime.orchestrator.orchestrator import Orchestrator
    db.insert_task(TaskRecord(
        id="TASK-072", brief="Add Alipay support",
        assigned_agent="engineering_head",
    ))
    db.insert_audit_log(
        task_id="TASK-072", agent="founder", action="revisit_of",
        payload={
            "predecessor_root": "TASK-052",
            "flagged": "TASK-058",
            "cascade": ["TASK-052", "TASK-053", "TASK-058"],
            "prior_status": "failed",
            "founder_note": "PR #103 already merged",
        },
    )
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))

    captured = {}
    def capture(task_id, agent, prompt, on_session_started=None):
        captured["prompt"] = prompt
        raise RuntimeError("abort after prompt build")
    monkeypatch.setattr(orch, "_run_agent", capture)
    orch.run_step("TASK-072")

    prompt = captured["prompt"]
    assert prompt.startswith("REVISIT CONTEXT:")
    assert "TASK-052" in prompt
    assert "failed" in prompt
    assert "TASK-058" in prompt
    assert "TASK-052 -> TASK-053 -> TASK-058" in prompt or \
           "TASK-052 → TASK-053 → TASK-058" in prompt
    assert "PR #103 already merged" in prompt
    # Shared discipline tail (TALK-028).
    assert "Status-assess before acting" in prompt
    assert "Do NOT improvise" in prompt


def test_run_step_revisit_header_absent_on_second_step(
    runtime, db, monkeypatch,
):
    """After the first orchestration_step audit entry lands, the header must
    disappear — subsequent EH cycles see a vanilla capabilities prompt."""
    from runtime.orchestrator.orchestrator import Orchestrator
    db.insert_task(TaskRecord(
        id="TASK-072", brief="x",
        assigned_agent="engineering_head",
    ))
    db.update_task("TASK-072", orchestration_step_count=1)
    db.insert_audit_log(
        task_id="TASK-072", agent="founder", action="revisit_of",
        payload={
            "predecessor_root": "TASK-052", "flagged": "TASK-052",
            "cascade": ["TASK-052"], "prior_status": "failed",
            "founder_note": None,
        },
    )
    db.insert_audit_log(
        task_id="TASK-072", agent="orchestrator", action="orchestration_step",
        payload={"step_number": 1, "decision": {"action": "done"}},
    )
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))

    captured = {}
    def capture(task_id, agent, prompt, on_session_started=None):
        captured["prompt"] = prompt
        raise RuntimeError("abort")
    monkeypatch.setattr(orch, "_run_agent", capture)
    orch.run_step("TASK-072")

    assert not captured["prompt"].startswith("REVISIT CONTEXT:")


def test_run_step_revisit_header_omits_note_line_when_none(
    runtime, db, monkeypatch,
):
    """founder_note == None => no 'Founder note:' line in the header."""
    from runtime.orchestrator.orchestrator import Orchestrator
    db.insert_task(TaskRecord(
        id="TASK-072", brief="x",
        assigned_agent="engineering_head",
    ))
    db.insert_audit_log(
        task_id="TASK-072", agent="founder", action="revisit_of",
        payload={
            "predecessor_root": "TASK-052", "flagged": "TASK-052",
            "cascade": ["TASK-052"], "prior_status": "failed",
            "founder_note": None,
        },
    )
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))

    captured = {}
    def capture(task_id, agent, prompt, on_session_started=None):
        captured["prompt"] = prompt
        raise RuntimeError("abort")
    monkeypatch.setattr(orch, "_run_agent", capture)
    orch.run_step("TASK-072")

    assert "Founder note:" not in captured["prompt"]


def test_run_step_resolved_escalation_header_injected_after_continue(
    runtime, db, monkeypatch,
):
    """After /resolve-escalation --continue, the task is re-enqueued (PENDING).
    On the manager's next decision step, the prompt must start with the
    ESCALATION RESOLVED header so the manager sees the founder's verdict."""
    from runtime.orchestrator.orchestrator import Orchestrator
    db.insert_task(TaskRecord(
        id="TASK-080", brief="Refund $800?",
        assigned_agent="engineering_head",
    ))
    db.update_task("TASK-080", orchestration_step_count=1)
    db.insert_audit_log(
        task_id="TASK-080", agent="orchestrator", action="orchestration_step",
        payload={"step_number": 1, "decision": {"action": "escalate"}},
    )
    db.insert_audit_log(
        task_id="TASK-080", agent="founder", action="escalation_resolved",
        payload={"decision": "continue", "rationale": "approved one-time exception"},
    )
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))

    captured = {}
    def capture(task_id, agent, prompt, on_session_started=None):
        captured["prompt"] = prompt
        raise RuntimeError("abort after prompt build")
    monkeypatch.setattr(orch, "_run_agent", capture)
    orch.run_step("TASK-080")

    prompt = captured["prompt"]
    assert prompt.startswith("ESCALATION RESOLVED:")
    assert "approved one-time exception" in prompt
    assert "founder continued" in prompt


def test_run_step_resolved_escalation_header_absent_after_next_step(
    runtime, db, monkeypatch,
):
    """Once the manager has taken a decision step after the resolution, the
    header must disappear — its trigger is `latest escalation_resolved id >
    latest orchestration_step id`."""
    from runtime.orchestrator.orchestrator import Orchestrator
    db.insert_task(TaskRecord(
        id="TASK-081", brief="x",
        assigned_agent="engineering_head",
    ))
    db.update_task("TASK-081", orchestration_step_count=2)
    db.insert_audit_log(
        task_id="TASK-081", agent="orchestrator", action="orchestration_step",
        payload={"step_number": 1, "decision": {"action": "escalate"}},
    )
    db.insert_audit_log(
        task_id="TASK-081", agent="founder", action="escalation_resolved",
        payload={"decision": "continue", "rationale": "ok"},
    )
    db.insert_audit_log(
        task_id="TASK-081", agent="orchestrator", action="orchestration_step",
        payload={"step_number": 2, "decision": {"action": "done"}},
    )
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))

    captured = {}
    def capture(task_id, agent, prompt, on_session_started=None):
        captured["prompt"] = prompt
        raise RuntimeError("abort")
    monkeypatch.setattr(orch, "_run_agent", capture)
    orch.run_step("TASK-081")

    assert not captured["prompt"].startswith("ESCALATION RESOLVED:")


def test_run_step_concurrent_claim_spawns_only_one_agent(
    runtime, db, monkeypatch,
):
    """Regression: when two workers pop the same task_id (e.g. a multi-child
    fan-in race double-enqueued the parent), exactly one must claim the step
    and call _run_agent. The other must observe the claimed state and
    silently no-op.

    Without an atomic CAS on the in_progress(delegated) → in_progress(NULL) transition,
    both threads pass the eligibility check at run_step steps 1 and both
    write IN_PROGRESS at step 3 → both _run_agent calls fire, producing two
    EH subprocesses on the same brief.
    """
    import json
    import threading
    from runtime.orchestrator.orchestrator import Orchestrator

    # Parent in_progress(delegated) with two children, both terminal → eligible
    # for exactly one EH decision step.
    db.insert_task(TaskRecord(id="T-PAR", brief="p",
                              assigned_agent="engineering_head"))
    db.insert_task(TaskRecord(id="T-C1", brief="c1",
                              assigned_agent="dev_agent", parent_task_id="T-PAR"))
    db.insert_task(TaskRecord(id="T-C2", brief="c2",
                              assigned_agent="dev_agent", parent_task_id="T-PAR"))
    db.update_task("T-C1", status=TaskStatus.COMPLETED)
    db.update_task("T-C2", status=TaskStatus.COMPLETED)
    db.update_task("T-PAR", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
                   note="waiting", orchestration_step_count=500)

    orch = Orchestrator(db=db, settings=Settings(max_orchestration_steps=10),
                        paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))

    # Barrier-sync the two threads AFTER each has read the parent row at the
    # top of run_step_impl — both then observe in_progress(delegated) before either
    # writes IN_PROGRESS. This is the exact race window we're closing.
    barrier = threading.Barrier(2, timeout=5.0)
    original_get_task = db.get_task
    par_reads = [0]
    par_reads_lock = threading.Lock()
    def synced_get_task(task_id):
        result = original_get_task(task_id)
        if task_id == "T-PAR":
            with par_reads_lock:
                par_reads[0] += 1
                should_sync = par_reads[0] <= 2
            if should_sync:
                try:
                    barrier.wait()
                except threading.BrokenBarrierError:
                    pass
        return result
    monkeypatch.setattr(db, "get_task", synced_get_task)

    agent_calls: list[tuple[str, str]] = []
    agent_calls_lock = threading.Lock()
    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        with agent_calls_lock:
            agent_calls.append((task_id, agent))
        return _make_result(), _make_report(
            output_summary=json.dumps({"action": "done", "summary": "ok"})
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    errs: list[BaseException] = []
    def worker():
        try:
            orch.run_step("T-PAR")
        except BaseException as e:
            errs.append(e)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start(); t2.start()
    t1.join(timeout=5.0); t2.join(timeout=5.0)

    assert not t1.is_alive() and not t2.is_alive(), "worker thread hung"
    assert not errs, f"worker threads raised: {errs}"
    # The assertion: exactly one EH subprocess spawned, not two.
    assert len(agent_calls) == 1, (
        f"expected 1 _run_agent call, got {len(agent_calls)}: {agent_calls}"
    )
    # The successful high-count claim advances telemetry once; the losing CAS
    # cannot add another count, lifecycle audit, or parent-queue wake.
    par = db.get_task("T-PAR")
    assert par.orchestration_step_count == 501, (
        f"expected orchestration_step_count=501, got {par.orchestration_step_count}"
    )
    assert len([row for row in db.get_audit_logs("T-PAR")
                if row["action"] == "orchestration_step"]) == 1
    assert orch._queue is None  # root completion has no parent wake to enqueue


def test_revisit_header_includes_sr_summary(runtime, db):
    """When the predecessor task submitted SRs, revisit header lists them."""
    from datetime import datetime, timezone

    from runtime.infrastructure.audit_logger import AuditLogger
    from runtime.models import (
        JobInterpreter,
        JobRecord,
        JobStatus,
        TaskRecord,
        TaskStatus,
    )
    from runtime.orchestrator.run_step import _revisit_header_if_applicable

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    predecessor = TaskRecord(
        id="TASK-001",
        assigned_agent="engineering_head",
        team="engineering",
        brief="orig",
        status=TaskStatus.FAILED,
    )
    revisit = TaskRecord(
        id="TASK-002",
        assigned_agent="engineering_head",
        team="engineering",
        brief="retry",
        status=TaskStatus.IN_PROGRESS,
    )
    db.insert_task(predecessor)
    db.insert_task(revisit)

    # Seed an SR submitted by the predecessor.
    sr = JobRecord(
        id="SR-019",
        task_id="TASK-001",
        agent_name="engineering_head",
        title="Close PR #247 with approval comment",
        rationale="r",
        script_text="echo x",
        interpreter=JobInterpreter.BASH,
        status=JobStatus.COMPLETED,
        created_at=now,
    )
    db.insert_job(sr)

    # Audit: script_submitted on predecessor, revisit_of on revisit.
    audit = AuditLogger(db)
    audit.log_job_submitted(
        task_id="TASK-001",
        job_id="SR-019",
        agent="engineering_head",
        title="Close PR #247 with approval comment",
        interpreter="bash",
        cwd_hint=None,
        byte_size=10,
        line_count=1,
    )
    db.insert_audit_log(
        task_id="TASK-002",
        agent="founder",
        action="revisit_of",
        payload={
            "predecessor_root": "TASK-001",
            "flagged": "TASK-001",
            "prior_status": "failed",
            "cascade": ["TASK-001"],
            "founder_note": "retry",
        },
    )

    # Mock orchestrator: just needs ._db.
    class _MockOrch:
        def __init__(self, d):
            self._db = d

    header = _revisit_header_if_applicable(_MockOrch(db), "TASK-002")
    assert header is not None
    assert "SR-019" in header
    assert "Close PR #247" in header
    assert "happyranch jobs show SR-019" in header
    assert "happyranch jobs output SR-019" in header


# ---- Cancel-race Guard B: post-_run_agent re-check ----
# See docs/superpowers/specs/2026-05-26-cancel-race-design.md §5.2.

def test_run_step_drops_delegate_when_cancelled_during_session(runtime, db, monkeypatch):
    """Guard B: /cancel can land between try_claim_for_step and subprocess exit.
    The l.41 entry guard only catches NEW enqueues. When `_run_agent` returns
    with a delegate decision but the task is now cancelled, no child task may
    be spawned and the founder-set status / note must remain intact.

    This is the regression check for the TASK-497 cancel race documented in
    docs/superpowers/specs/2026-05-26-cancel-race-design.md.
    """
    import json
    from datetime import datetime, timezone
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.models import TokenUsage

    # Workspace must exist so _validate_delegate doesn't error out and
    # take us through the (already-idempotent) _fail path instead of the
    # (not-yet-guarded) delegate path we're testing.
    (runtime.workspaces_dir / "dev_agent").mkdir(parents=True)

    db.insert_task(TaskRecord(
        id="T-RACE", brief="x", assigned_agent="engineering_head",
    ))
    db.update_task("T-RACE", orchestration_step_count=500)
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    def cancel_then_delegate(*a, **k):
        # Simulate /cancel landing while the subprocess was running. By the
        # time _run_agent returns, the founder has already stamped the row.
        now = datetime.now(timezone.utc).isoformat()
        db.update_task(
            "T-RACE",
            status=TaskStatus.FAILED,
            block_kind=None,
            note="cancelled by founder: stop",
            cancelled_at=now,
            completed_at=now,
        )
        # The EH session would have produced its decision before SIGTERM
        # took effect; we model that by returning a delegate decision anyway.
        result = _make_result()
        result.token_usage = TokenUsage(
            input_tokens=10, output_tokens=20, model="claude-opus",
        )
        report = _make_report(
            output_summary=json.dumps({
                "action": "delegate", "agent": "dev_agent", "prompt": "ship it",
            }),
        )
        return result, report

    monkeypatch.setattr(orch, "_run_agent", cancel_then_delegate)

    orch.run_step("T-RACE")

    t = db.get_task("T-RACE")
    # Founder's terminal state preserved.
    assert t.status == TaskStatus.FAILED
    assert t.note == "cancelled by founder: stop"
    assert t.cancelled_at is not None
    # The successful pre-session claim advances telemetry once; cancellation
    # cannot resurrect a second claim or launch/queue effect.
    assert t.orchestration_step_count == 501
    # No child task spawned by the delegate decision.
    assert db.get_children("T-RACE") == []
    # Queue stays empty — nothing to dispatch.
    assert orch._queue.qsize() == 0
    # Token usage IS persisted regardless of cancel (spec §5.2 — provider
    # really charged for the session; /tokens rollups must reflect spend).
    usage_rows = db.list_session_token_usage(task_id="T-RACE")
    assert len(usage_rows) == 1
    assert usage_rows[0]["input_tokens"] == 10
    assert usage_rows[0]["output_tokens"] == 20


@pytest.mark.parametrize(
    ("task_id", "field", "origin_id"),
    [
        ("T-THREAD", "dispatched_from_thread_id", "THR-001"),
    ],
)
def test_run_step_token_usage_carries_task_origin_scope(
    runtime, db, monkeypatch, task_id, field, origin_id,
):
    from datetime import datetime, timezone
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.models import TokenUsage

    task_kwargs = {
        "id": task_id,
        "brief": "x",
        "assigned_agent": "engineering_head",
        field: origin_id,
    }
    db.insert_task(TaskRecord(**task_kwargs))
    orch = Orchestrator(
        db=db, settings=Settings(), paths=runtime, slug="test",
        teams=TeamsRegistry.load(runtime.root),
    )
    orch._queue = _SlugQueue()

    def cancel_with_usage(*a, **k):
        now = datetime.now(timezone.utc).isoformat()
        db.update_task(
            task_id,
            status=TaskStatus.FAILED,
            note="cancelled by founder: stop",
            cancelled_at=now,
            completed_at=now,
        )
        result = _make_result()
        result.token_usage = TokenUsage(input_tokens=3, output_tokens=4)
        return result, _make_report(output_summary="ignored")

    monkeypatch.setattr(orch, "_run_agent", cancel_with_usage)

    orch.run_step(task_id)

    rows = db.list_session_token_usage(task_id=task_id)
    assert len(rows) == 1
    assert rows[0]["scope_type"] == "task"
    assert rows[0]["scope_id"] == task_id
    assert rows[0]["thread_id"] == (
        origin_id if field == "dispatched_from_thread_id" else None
    )


# ---- Cancel-race Guard C: shared terminal predicate ----
# See docs/superpowers/specs/2026-05-26-cancel-race-design.md §5.3.

def test_is_already_terminal_predicate(runtime, db):
    """Single source of truth for the `done` / `delegate` / `escalate` / `_fail`
    / `_complete` idempotence guards. Returns True for missing tasks, for
    terminal statuses (COMPLETED, FAILED), and for cancelled rows even if their
    status hasn't yet flipped to FAILED.
    """
    from datetime import datetime, timezone
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.run_step import _is_already_terminal

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))

    # Missing task → True (treat as terminal; nothing to act on).
    assert _is_already_terminal(orch, "T-NOPE") is True

    # PENDING → False.
    db.insert_task(TaskRecord(id="T-A", brief="a"))
    assert _is_already_terminal(orch, "T-A") is False

    # IN_PROGRESS → False.
    db.update_task("T-A", status=TaskStatus.IN_PROGRESS)
    assert _is_already_terminal(orch, "T-A") is False

    # IN_PROGRESS(delegated) → False. (Parent in_progress(delegated) is waiting
    # on a child, not terminal; a fresh manager step is allowed.)
    db.update_task("T-A", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED)
    assert _is_already_terminal(orch, "T-A") is False

    # COMPLETED → True.
    db.update_task("T-A", status=TaskStatus.COMPLETED, block_kind=None)
    assert _is_already_terminal(orch, "T-A") is True

    # FAILED → True.
    db.insert_task(TaskRecord(id="T-B", brief="b"))
    db.update_task("T-B", status=TaskStatus.FAILED)
    assert _is_already_terminal(orch, "T-B") is True

    # Cancelled even if status hasn't yet been flipped to FAILED — defense
    # in depth against a future code path that stamps cancelled_at without
    # touching status. Per spec §5.3.
    db.insert_task(TaskRecord(id="T-C", brief="c"))
    now = datetime.now(timezone.utc).isoformat()
    db.update_task("T-C", status=TaskStatus.IN_PROGRESS, cancelled_at=now)
    assert _is_already_terminal(orch, "T-C") is True


def test_run_step_delegate_atomic_against_cancel_between_recheck_and_cas(
    runtime, db, monkeypatch,
):
    """Codex P1 on PR #34: even after Guard B's re-fetch passes, /cancel can
    land between the re-fetch and the delegate's insert+update. The atomic
    CAS in db.try_delegate must close this window — no child created, parent
    state preserved.

    Simulated by monkey-patching db.try_delegate to invoke /cancel just before
    its conditional UPDATE runs. This reproduces the worst-case interleaving
    that the Python-level check-then-act would have lost.
    """
    import json
    from datetime import datetime, timezone
    from runtime.orchestrator.orchestrator import Orchestrator

    (runtime.workspaces_dir / "dev_agent").mkdir(parents=True)

    db.insert_task(TaskRecord(
        id="T-RACE2", brief="x", assigned_agent="engineering_head",
    ))
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    # _run_agent returns a delegate without cancelling — Guard B re-fetch
    # will pass. The cancel races in via the monkey-patched try_delegate.
    monkeypatch.setattr(orch, "_run_agent",
                        lambda *a, **k: (_make_result(), _make_report(
                            output_summary=json.dumps({
                                "action": "delegate", "agent": "dev_agent",
                                "prompt": "ship it",
                            }),
                        )))

    # Wrap try_delegate so the cancel lands at the worst moment: AFTER Guard B
    # re-checks but BEFORE the CAS write. The atomic SELECT inside try_delegate
    # should observe the cancel and return False.
    real_try_delegate = db.try_delegate
    def racy_try_delegate(parent_id, child, *, parent_note, attachments=None, active_chain_json=None, uploaded_by="orchestrator"):
        # Simulate founder cancel landing just before the CAS SELECT.
        now = datetime.now(timezone.utc).isoformat()
        db.update_task(
            parent_id,
            status=TaskStatus.FAILED, block_kind=None,
            note="cancelled by founder: stop",
            cancelled_at=now, completed_at=now,
        )
        return real_try_delegate(parent_id, child, parent_note=parent_note,
                                  attachments=attachments, active_chain_json=active_chain_json,
                                  uploaded_by=uploaded_by)
    monkeypatch.setattr(db, "try_delegate", racy_try_delegate)

    orch.run_step("T-RACE2")

    t = db.get_task("T-RACE2")
    assert t.status == TaskStatus.FAILED
    assert t.note == "cancelled by founder: stop"
    assert t.cancelled_at is not None
    # CRITICAL: no child created — the atomic CAS observed the cancel and bailed.
    assert db.get_children("T-RACE2") == []
    assert orch._queue.qsize() == 0


def test_run_step_escalate_atomic_against_cancel_between_recheck_and_cas(
    runtime, db, monkeypatch,
):
    """Codex P2 on PR #34: same race shape for the escalate branch — cancel
    landing between Guard B and the conditional UPDATE must not resurrect a
    cancelled row into escalated."""
    import json
    from datetime import datetime, timezone
    from runtime.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(
        id="T-ESC", brief="x", assigned_agent="engineering_head",
    ))
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    monkeypatch.setattr(orch, "_run_agent",
                        lambda *a, **k: (_make_result(), _make_report(
                            output_summary=json.dumps({
                                "action": "escalate", "reason": "blocked on creds",
                            }),
                        )))

    real_try_escalate = db.try_escalate
    def racy_try_escalate(task_id, *, reason):
        now = datetime.now(timezone.utc).isoformat()
        db.update_task(
            task_id,
            status=TaskStatus.FAILED, block_kind=None,
            note="cancelled by founder: stop",
            cancelled_at=now, completed_at=now,
        )
        return real_try_escalate(task_id, reason=reason)
    monkeypatch.setattr(db, "try_escalate", racy_try_escalate)

    orch.run_step("T-ESC")

    t = db.get_task("T-ESC")
    assert t.status == TaskStatus.FAILED
    assert t.note == "cancelled by founder: stop"
    assert t.cancelled_at is not None
    # block_kind stays None (cancel cleared it); not escalated.
    assert t.block_kind is None


def _seed_open_thread_dispatch(db, *, thread_id, task_id, dispatcher, target):
    # Sibling: _seed_dispatched_root in test_thread_task_followup.py also inserts
    # the TaskRecord; this one does not — callers insert the task themselves.
    from runtime.models import ThreadRecord
    from runtime.infrastructure.audit_logger import AuditLogger
    db.insert_thread(ThreadRecord(id=thread_id, subject="t"))
    db.add_thread_participant(thread_id, dispatcher, added_by="founder")
    AuditLogger(db).log_thread_dispatch(
        thread_id, task_id=task_id, dispatcher=dispatcher,
        target_agent=target, team="engineering",
    )


def test_run_step_escalate_surfaces_in_thread(runtime, db, monkeypatch):
    import json
    from runtime.models import ThreadInvocationPurpose
    from runtime.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(
        id="T-1", brief="x", assigned_agent="engineering_head",
        dispatched_from_thread_id="THR-9",
    ))
    _seed_open_thread_dispatch(db, thread_id="THR-9", task_id="T-1",
                               dispatcher="engineering_head", target="engineering_head")

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()  # mirror existing escalate test; not used on escalate path

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary=json.dumps({"action": "escalate", "reason": "needs founder auth"}),
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    orch.run_step("T-1")

    t = db.get_task("T-1")
    assert t.status == TaskStatus.ESCALATED and t.block_kind is None  # Path B
    msgs = db.list_thread_messages("THR-9")
    esc = [m for m in msgs if m.system_payload
           and m.system_payload.get("kind_tag") == "task_escalated"]
    assert len(esc) == 1
    assert esc[0].system_payload["reason"] == "needs founder auth"
    invs = db.list_thread_invocations("THR-9")
    assert any(i.purpose == ThreadInvocationPurpose.TASK_FOLLOWUP for i in invs)


def test_run_step_beyond_legacy_cap_does_not_escalate_thread(runtime, db, monkeypatch):
    from runtime.models import ThreadInvocationPurpose
    from runtime.orchestrator.orchestrator import Orchestrator
    settings = Settings(max_orchestration_steps=3)
    db.insert_task(TaskRecord(
        id="T-1", brief="x", assigned_agent="engineering_head",
        dispatched_from_thread_id="THR-9",
    ))
    db.update_task("T-1", orchestration_step_count=3)  # already at the cap
    _seed_open_thread_dispatch(db, thread_id="THR-9", task_id="T-1",
                               dispatcher="engineering_head", target="engineering_head")

    orch = Orchestrator(db=db, settings=settings, paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    monkeypatch.setattr(orch, "_run_agent", lambda *args, **kwargs: (
        _make_result(), _make_report(output_summary=json.dumps({"action": "done", "summary": "done"})),
    ))
    orch.run_step("T-1")

    msgs = db.list_thread_messages("THR-9")
    esc = [m for m in msgs if m.system_payload
           and m.system_payload.get("kind_tag") == "task_escalated"]
    assert esc == []
    assert db.get_task("T-1").orchestration_step_count == 4


def test_non_manager_owner_of_task_type_emits_decision(runtime, db, monkeypatch):
    """A type=task owned by a NON-manager parses its decision (done here)."""
    import json
    from runtime.orchestrator.orchestrator import Orchestrator
    db.insert_task(TaskRecord(
        id="T-1", brief="root", assigned_agent="dev_agent", task_type="task",
    ))
    orch = Orchestrator(db=db, settings=Settings(max_orchestration_steps=10),
                        paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary=json.dumps({"action": "done", "summary": "did it"}),
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    orch.run_step("T-1")

    t = db.get_task("T-1")
    assert t.status == TaskStatus.COMPLETED
    assert t.note == "did it"


def test_subtask_owner_is_leaf_even_if_decision_present(runtime, db, monkeypatch):
    """A type=subtask owner does NOT orchestrate: a delegate decision in its
    report is ignored and the task simply completes (leaf path)."""
    import json
    from runtime.orchestrator.orchestrator import Orchestrator
    db.insert_task(TaskRecord(
        id="T-2", brief="leaf", assigned_agent="engineering_head",
        task_type="subtask",
    ))
    orch = Orchestrator(db=db, settings=Settings(max_orchestration_steps=10),
                        paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        # Even though this is a manager AND emits a delegate, the subtask
        # gate forces leaf completion.
        return _make_result(), _make_report(
            output_summary=json.dumps(
                {"action": "delegate", "agent": "dev_agent", "prompt": "go"}),
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    orch.run_step("T-2")
    t = db.get_task("T-2")
    assert t.status == TaskStatus.COMPLETED          # leaf — no child spawned
    assert db.get_children("T-2") == []


def test_delegated_child_is_typed_subtask(runtime, db, monkeypatch):
    import json
    from runtime.orchestrator.orchestrator import Orchestrator
    (runtime.workspaces_dir / "dev_agent").mkdir(parents=True)
    db.insert_task(TaskRecord(
        id="T-1", brief="root", assigned_agent="engineering_head",
        task_type="task",
    ))
    orch = Orchestrator(db=db, settings=Settings(max_orchestration_steps=10),
                        paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary=json.dumps(
                {"action": "delegate", "agent": "dev_agent", "prompt": "build"}),
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    orch.run_step("T-1")
    children = db.get_children("T-1")
    assert len(children) == 1
    assert db.get_task(children[0]).task_type == "subtask"


def test_non_manager_self_delegation_is_allowed(runtime, db, monkeypatch):
    """dev_agent owns a type=task and delegates to ITSELF → child spawned."""
    import json
    from runtime.orchestrator.orchestrator import Orchestrator
    (runtime.workspaces_dir / "dev_agent").mkdir(parents=True, exist_ok=True)
    db.insert_task(TaskRecord(id="T-1", brief="root",
                              assigned_agent="dev_agent", task_type="task"))
    orch = Orchestrator(db=db, settings=Settings(max_orchestration_steps=10),
                        paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    def fake(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary=json.dumps(
                {"action": "delegate", "agent": "dev_agent", "prompt": "phase 2"}))
    monkeypatch.setattr(orch, "_run_agent", fake)

    orch.run_step("T-1")
    children = db.get_children("T-1")
    assert len(children) == 1
    assert db.get_task(children[0]).assigned_agent == "dev_agent"


def test_non_manager_cross_agent_delegation_is_rejected(runtime, db, monkeypatch):
    """dev_agent owning a type=task may NOT delegate to product_manager →
    feedback step, task re-enqueued PENDING, no child."""
    import json
    from runtime.orchestrator.orchestrator import Orchestrator
    (runtime.workspaces_dir / "product_manager").mkdir(parents=True, exist_ok=True)
    db.insert_task(TaskRecord(id="T-1", brief="root",
                              assigned_agent="dev_agent", task_type="task"))
    orch = Orchestrator(db=db, settings=Settings(max_orchestration_steps=10),
                        paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    def fake(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary=json.dumps(
                {"action": "delegate", "agent": "product_manager", "prompt": "x"}))
    monkeypatch.setattr(orch, "_run_agent", fake)

    orch.run_step("T-1")
    assert db.get_children("T-1") == []
    assert db.get_task("T-1").status == TaskStatus.PENDING   # re-enqueued for re-decide


def test_manager_self_target_does_not_bump_revision_count(runtime, db, monkeypatch):
    """A manager re-delegating to ITSELF is sequencing, not a revise loop —
    revision_count must stay 0 so escalate-after-2-rounds doesn't misfire."""
    import json
    from runtime.orchestrator.orchestrator import Orchestrator
    (runtime.workspaces_dir / "engineering_head").mkdir(parents=True, exist_ok=True)
    # One already-completed self-child makes engineering_head the worker-of-record.
    db.insert_task(TaskRecord(id="T-1", brief="root",
                              assigned_agent="engineering_head", task_type="task"))
    db.insert_task(TaskRecord(id="T-1-c1", brief="c1",
                              assigned_agent="engineering_head",
                              parent_task_id="T-1", task_type="subtask"))
    db.update_task("T-1-c1", status=TaskStatus.COMPLETED)

    orch = Orchestrator(db=db, settings=Settings(max_orchestration_steps=10),
                        paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    def fake(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary=json.dumps(
                {"action": "delegate", "agent": "engineering_head",
                 "prompt": "phase 2"}))
    monkeypatch.setattr(orch, "_run_agent", fake)

    orch.run_step("T-1")
    assert db.get_task("T-1").revision_count == 0


def test_build_agent_prompt_leaf_subtask_is_empty(runtime, db):
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.run_step import _build_agent_prompt
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    t = TaskRecord(id="T-1", brief="x", assigned_agent="dev_agent",
                   task_type="subtask")
    assert _build_agent_prompt(orch, t, "dev_agent") == ""


def test_build_agent_prompt_non_manager_task_is_self_only(runtime, db):
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.run_step import _build_agent_prompt
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    t = TaskRecord(id="T-1", brief="x", assigned_agent="dev_agent",
                   task_type="task")
    p = _build_agent_prompt(orch, t, "dev_agent")
    assert "Available Agents" not in p
    assert "dev_agent" in p


def test_build_agent_prompt_manager_roster_includes_self(runtime, db):
    """Spec §3: a manager may self-target. The roster must advertise self so the
    manager knows it can delegate a sub-task to itself (it is not in teams.yaml
    `workers`, so it would otherwise be absent)."""
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.run_step import _build_agent_prompt
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    t = TaskRecord(id="T-1", brief="x", assigned_agent="engineering_head",
                   task_type="task")
    p = _build_agent_prompt(orch, t, "engineering_head")
    assert "Available Agents" in p          # full roster prompt
    assert "engineering_head" in p          # self advertised in the roster
    assert "yourself" in p


# ═══════════════════════════════════════════════════════════════════
# TASK-573 — bounded failure-recovery tests
# ═══════════════════════════════════════════════════════════════════


def test_failed_child_wakes_parent_for_decision_step_not_cascade(
    runtime, db, monkeypatch,
):
    """One failed child → parent gets a manager decision step (enqueued),
    NOT cascade-failed. The old behavior unconditionally _fail'd the parent;
    the new contract wakes it for a bounded re-decision."""
    import asyncio
    from runtime.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-PAR", brief="parent brief",
                              assigned_agent="engineering_head",
                              task_type="task"))
    db.update_task("T-PAR", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(
        id="T-CHD", brief="child brief",
        assigned_agent="dev_agent", parent_task_id="T-PAR",
        task_type="subtask",
    ))
    # Child is FAILED — the scenario the orchestrator must handle.
    db.update_task("T-CHD", status=TaskStatus.FAILED,
                   note="reviewer found issues: REQUEST_CHANGES")

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting
    _enqueue_parent_if_waiting(orch, "T-CHD")

    # Parent must NOT be FAILED — it gets a decision step.
    parent = db.get_task("T-PAR")
    assert parent.status == TaskStatus.IN_PROGRESS, (
        f"parent should stay in_progress(delegated) for decision step, got {parent.status}"
    )
    assert parent.block_kind == BlockKind.DELEGATED

    # Parent is enqueued for its next decision step.
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == "T-PAR"


def test_two_failed_children_wakes_owner_not_escalate(runtime, db, monkeypatch):
    """THR-078: two failed children with no revisit lineage (different
    slices, each failing for the first time) wakes the owner for
    adjudication, NOT escalated.  The old count-based _FAILURE_ROUND_BOUND
    auto-escalation is retired."""
    import asyncio
    from runtime.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-PAR", brief="parent brief",
                              assigned_agent="engineering_head",
                              task_type="task"))
    db.update_task("T-PAR", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED, note="waiting")

    # Two different slices, each failing for the first time.
    db.insert_task(TaskRecord(
        id="T-F1", brief="first failed child",
        assigned_agent="dev_agent", parent_task_id="T-PAR",
        task_type="subtask",
    ))
    db.update_task("T-F1", status=TaskStatus.FAILED,
                   note="first failure")

    db.insert_task(TaskRecord(
        id="T-F2", brief="second failed child",
        assigned_agent="dev_agent", parent_task_id="T-PAR",
        task_type="subtask",
    ))
    db.update_task("T-F2", status=TaskStatus.FAILED,
                   note="second failure — different slice")

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting
    _enqueue_parent_if_waiting(orch, "T-F2")

    parent = db.get_task("T-PAR")
    # No revisit lineage → owner is woken, not escalated.
    assert parent.status == TaskStatus.IN_PROGRESS, (
        f"owner should be woken for adjudication, got {parent.status}"
    )
    assert parent.block_kind == BlockKind.DELEGATED

    # Parent is enqueued for its next decision step.
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == "T-PAR"


def test_chain_leg_failure_wakes_parent_not_cascade(runtime, db, monkeypatch):
    """A failed chain leg clears the chain and hands back to the parent's
    manager decision step (subject to the same 2-round bound), NOT cascade-fail."""
    import asyncio
    from runtime.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-PAR", brief="chain parent",
                              assigned_agent="engineering_head",
                              task_type="task"))
    db.update_task("T-PAR", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED, note="waiting")

    # Set up an active chain on the parent.
    from runtime.orchestrator.chain import ChainState, ChainLeg
    chain = ChainState(
        step_index=0, first_leg_expect_verdict=None,
        legs=[ChainLeg(agent="code_reviewer", prompt="review",
                       expect_verdict="APPROVE")],
        step_audit_id=1,
    )
    db.update_task_active_chain("T-PAR", chain.serialize())

    # Chain leg child FAILED.
    db.insert_task(TaskRecord(
        id="T-LEG", brief="review",
        assigned_agent="code_reviewer", parent_task_id="T-PAR",
        task_type="subtask",
    ))
    db.update_task("T-LEG", status=TaskStatus.FAILED,
                   note="self-blocked: REQUEST_CHANGES")

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting
    _enqueue_parent_if_waiting(orch, "T-LEG")

    parent = db.get_task("T-PAR")
    # Chain cleared; parent gets a decision step, not cascade-failed.
    assert parent.active_chain is None, "chain must be cleared on leg failure"
    assert parent.status == TaskStatus.IN_PROGRESS
    assert parent.block_kind == BlockKind.DELEGATED
    # Parent is enqueued.
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == "T-PAR"


def test_regression_all_children_completed_wakes_parent_unchanged(
    runtime, db, monkeypatch,
):
    """REGRESSION GUARD: happy path — all children COMPLETED → parent enqueued
    for next decision step. This behavior MUST NOT change."""
    import asyncio
    from runtime.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-PAR", brief="parent brief",
                              assigned_agent="engineering_head",
                              task_type="task"))
    db.update_task("T-PAR", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(
        id="T-CHD", brief="child brief",
        assigned_agent="dev_agent", parent_task_id="T-PAR",
        task_type="subtask",
    ))
    db.update_task("T-CHD", status=TaskStatus.COMPLETED, note="all good")

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting
    _enqueue_parent_if_waiting(orch, "T-CHD")

    parent = db.get_task("T-PAR")
    assert parent.status == TaskStatus.IN_PROGRESS
    assert parent.block_kind == BlockKind.DELEGATED
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == "T-PAR"


def test_regression_revise_verdict_chain_advance_unchanged(
    runtime, db, monkeypatch,
):
    """REGRESSION GUARD: REVISE-verdict auto-advance in chains is UNCHANGED.
    A COMPLETED child with REVISE verdict must still advance the chain."""
    import asyncio
    from runtime.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-PAR", brief="chain parent",
                              assigned_agent="engineering_head",
                              task_type="task"))
    db.update_task("T-PAR", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED, note="waiting")

    from runtime.orchestrator.chain import ChainState, ChainLeg
    chain = ChainState(
        step_index=0, first_leg_expect_verdict="APPROVE",
        legs=[
            ChainLeg(agent="code_reviewer", prompt="review",
                     expect_verdict="APPROVE"),
            ChainLeg(agent="dev_agent", prompt="revise",
                     expect_verdict=None),
        ],
        step_audit_id=1,
    )
    db.update_task_active_chain("T-PAR", chain.serialize())

    # Chain leg child COMPLETED with REVISE verdict.
    db.insert_task(TaskRecord(
        id="T-LEG", brief="review",
        assigned_agent="code_reviewer", parent_task_id="T-PAR",
        task_type="subtask",
    ))
    db.update_task("T-LEG", status=TaskStatus.COMPLETED, note="done")
    db.insert_task_result(
        task_id="T-LEG", agent="code_reviewer", session_id="s",
        status="completed", confidence_score=85,
        output_summary="found issues, REVISE",
        verdict="REVISE",
    )

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting
    _enqueue_parent_if_waiting(orch, "T-LEG")

    # Chain auto-advance: REVISE verdict matches first_leg_expect_verdict=APPROVE?
    # No — REVISE does NOT match APPROVE, so the chain should clear and the
    # parent should wake. The REVISE auto-advance works by having
    # expect_verdict=None on the follow-up leg (the advance_action returns
    # advance regardless of verdict when expect_verdict is None).
    # In this test: first_leg_expect_verdict="APPROVE", child verdict="REVISE"
    # → mismatch → wake. The parent gets enqueued for a decision step.
    parent = db.get_task("T-PAR")
    assert parent.status == TaskStatus.IN_PROGRESS
    assert parent.block_kind == BlockKind.DELEGATED
    assert parent.active_chain is None  # chain cleared on mismatch
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == "T-PAR"


@pytest.mark.parametrize("prior_count", [50, 51, 500])
def test_run_step_nonroot_beyond_legacy_cap_claims_once(runtime, db, monkeypatch, prior_count):
    """A non-root beyond the retired cap completes through the normal seam.

    The retained counter remains monotonic and the usual parent wake occurs;
    neither a failure nor an escalation is manufactured by step count.
    """
    from runtime.orchestrator.orchestrator import Orchestrator
    settings = Settings(max_orchestration_steps=3)

    db.insert_task(TaskRecord(id="T-PAR", brief="p",
                              assigned_agent="engineering_head"))
    db.update_task("T-PAR", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(
        id="T-CHD", brief="c", assigned_agent="dev_agent",
        parent_task_id="T-PAR", task_type="subtask",
    ))
    db.update_task("T-CHD", orchestration_step_count=prior_count)

    orch = Orchestrator(db=db, settings=settings, paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    q = _SlugQueue()
    orch._queue = q
    monkeypatch.setattr(orch, "_run_agent", lambda *args, **kwargs: (
        _make_result(), _make_report(output_summary="done"),
    ))

    orch.run_step("T-CHD")

    child = db.get_task("T-CHD")
    assert child.orchestration_step_count == prior_count + 1
    assert child.status == TaskStatus.COMPLETED
    assert child.block_kind is None
    assert not [a for a in db.get_audit_logs("T-CHD") if a["action"] == "escalation"]
    assert q.qsize() == 1  # ordinary completed-child wake, not a cap failure


def test_run_step_nonroot_beyond_legacy_cap_duplicate_claim_is_at_most_once(runtime, db, monkeypatch):
    """Duplicate delivery of the same at-cap non-root row wakes the parent
    exactly once. The normal atomic claim makes the second delivery a no-op."""
    from runtime.orchestrator.orchestrator import Orchestrator
    settings = Settings(max_orchestration_steps=3)

    db.insert_task(TaskRecord(id="T-PAR", brief="p",
                              assigned_agent="engineering_head"))
    db.update_task("T-PAR", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(
        id="T-CHD", brief="c", assigned_agent="dev_agent",
        parent_task_id="T-PAR", task_type="subtask",
    ))
    # Exercise the actual retired-cap boundary, rather than a small synthetic
    # count: a duplicate delivery must not turn telemetry into a second launch.
    db.update_task("T-CHD", orchestration_step_count=51)

    orch = Orchestrator(db=db, settings=settings, paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    q = _SlugQueue()
    orch._queue = q
    monkeypatch.setattr(orch, "_run_agent", lambda *args, **kwargs: (
        _make_result(), _make_report(output_summary="done"),
    ))

    orch.run_step("T-CHD")
    orch.run_step("T-CHD")  # duplicate delivery

    assert db.get_task("T-CHD").status == TaskStatus.COMPLETED
    assert db.get_task("T-CHD").orchestration_step_count == 52
    assert q.qsize() == 1  # duplicate delivery did not add another wake


def test_run_step_reopen_retains_high_count_and_claims_once(runtime, db, monkeypatch):
    """Restart/recovery retains telemetry; the next real claim increments once."""
    from runtime.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-REOPEN", brief="x", assigned_agent="engineering_head"))
    db.update_task("T-REOPEN", orchestration_step_count=500)
    db.close()
    reopened = Database(runtime.db_path)
    orch = Orchestrator(
        db=reopened, settings=Settings(max_orchestration_steps=3), paths=runtime,
        slug="test", teams=TeamsRegistry.load(runtime.root),
    )
    monkeypatch.setattr(orch, "_run_agent", lambda *args, **kwargs: (
        _make_result(), _make_report(output_summary=json.dumps({"action": "done", "summary": "done"})),
    ))

    orch.run_step("T-REOPEN")
    orch.run_step("T-REOPEN")  # recovery duplicate after the terminal transition

    task = reopened.get_task("T-REOPEN")
    assert task.status == TaskStatus.COMPLETED
    assert task.orchestration_step_count == 501
    assert len([row for row in reopened.get_audit_logs("T-REOPEN") if row["action"] == "orchestration_step"]) == 1
    reopened.close()


@pytest.mark.parametrize("prior_count", [50, 51, 500])
def test_run_step_root_beyond_legacy_cap_does_not_escalate(runtime, db, monkeypatch, prior_count):
    """A root beyond the retired cap finishes normally without escalation."""
    from runtime.orchestrator.orchestrator import Orchestrator
    settings = Settings(max_orchestration_steps=3)
    db.insert_task(TaskRecord(
        id="T-ROOT", brief="x", assigned_agent="engineering_head",
    ))
    db.update_task("T-ROOT", orchestration_step_count=prior_count)

    orch = Orchestrator(db=db, settings=settings, paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    monkeypatch.setattr(orch, "_run_agent", lambda *args, **kwargs: (
        _make_result(), _make_report(output_summary=json.dumps({"action": "done", "summary": "done"})),
    ))
    orch.run_step("T-ROOT")

    t = db.get_task("T-ROOT")
    assert t.parent_task_id is None
    assert t.status == TaskStatus.COMPLETED
    assert t.orchestration_step_count == prior_count + 1


def test_run_step_nonroot_self_block_never_escalated(runtime, db, monkeypatch):
    """Regression (THR-033 Change A confirm-no-change): a NON-root self-block
    (report.status=blocked, empty waiting_on_job_ids) fails through the parent
    and is NEVER escalated."""
    from runtime.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-PAR", brief="p",
                              assigned_agent="engineering_head"))
    db.update_task("T-PAR", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(
        id="T-CHD", brief="c", assigned_agent="dev_agent",
        parent_task_id="T-PAR", task_type="subtask",
    ))

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    q = _SlugQueue()
    orch._queue = q

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary="cannot proceed", status="blocked",
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    orch.run_step("T-CHD")

    child = db.get_task("T-CHD")
    assert child.status == TaskStatus.FAILED
    assert child.block_kind is None

    escalations = [
        a for a in db.get_audit_logs("T-CHD") if a["action"] == "escalation"
    ]
    assert escalations == []
    # Parent woken via bounded recovery.
    assert q.qsize() == 1
    assert q.get_nowait() == ("test", "T-PAR")


def test_enqueue_parent_exhaustion_nonroot_parent_routes_upward(runtime, db):
    """THR-033 Change A lock-in: if an exhausted-bound parent were itself a
    NON-root (impossible in production today — only roots have children), it
    must NOT escalate directly. It fails and routes the failure to its own
    parent. Locks the defensive is_root(parent) guard in
    _enqueue_parent_if_waiting."""
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    # Grandparent (root), delegated + waiting.
    db.insert_task(TaskRecord(id="T-GP", brief="gp",
                              assigned_agent="engineering_head"))
    db.update_task("T-GP", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED, note="waiting")
    # Middle parent: NON-root (child of T-GP) but itself delegating + blocked.
    db.insert_task(TaskRecord(
        id="T-MID", brief="mid", assigned_agent="engineering_head",
        parent_task_id="T-GP", task_type="task",
    ))
    db.update_task("T-MID", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED, note="waiting")
    # Two failed children of T-MID with no revisit lineage.
    db.insert_task(TaskRecord(
        id="T-F1", brief="f1", assigned_agent="dev_agent",
        parent_task_id="T-MID", task_type="subtask",
    ))
    db.update_task("T-F1", status=TaskStatus.FAILED, note="first failure")
    db.insert_task(TaskRecord(
        id="T-F2", brief="f2", assigned_agent="dev_agent",
        parent_task_id="T-MID", task_type="subtask",
    ))
    db.update_task("T-F2", status=TaskStatus.FAILED,
                   note="second failure — different slice")

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    _enqueue_parent_if_waiting(orch, "T-F2")

    mid = db.get_task("T-MID")
    # THR-078: no revisit lineage → non-root parent is woken for
    # adjudication, not failed.  Only per-slice ceiling exhaustion
    # (a child retry that fails again) would fail the non-root.
    assert mid.status == TaskStatus.IN_PROGRESS
    assert mid.block_kind == BlockKind.DELEGATED

    # Non-root parent is enqueued.
    assert orch._queue.qsize() == 1
    assert orch._queue.get_nowait() == ("test", "T-MID")


# --- THR-078: per-slice retry ceiling, owner-adjudication primary ---


def test_fanout_mixed_outcome_wakes_owner_not_escalate(runtime, db, monkeypatch):
    """THR-078: any fan-out round with >=1 non-clean slice wakes the root
    owner with per-slice join context, NOT auto-escalated to founder.

    Even with 2+ failed siblings (old _FAILURE_ROUND_BOUND trigger),
    the new design wakes the owner to adjudicate."""
    import asyncio
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    # Root parent with 3 fan-out children: 2 failed, 1 completed.
    db.insert_task(TaskRecord(
        id="T-MIXED", brief="fan-out parent",
        assigned_agent="engineering_head",
        task_type="task",
    ))
    db.update_task("T-MIXED", status=TaskStatus.IN_PROGRESS,
                   block_kind=BlockKind.DELEGATED, note="fan-out in flight")

    # Child 1: failed (REQUEST_CHANGES via carrier verdict mismatch)
    db.insert_task(TaskRecord(
        id="T-MIXED-C1", brief="build feature",
        assigned_agent="dev_agent", parent_task_id="T-MIXED",
        task_type="subtask",
    ))
    db.update_task("T-MIXED-C1", status=TaskStatus.FAILED,
                   note="carrier verdict mismatch: expected 'APPROVE', got 'REQUEST_CHANGES'")
    # Child 2: failed (no-op self-block)
    db.insert_task(TaskRecord(
        id="T-MIXED-C2", brief="no-op task",
        assigned_agent="dev_agent", parent_task_id="T-MIXED",
        task_type="subtask",
    ))
    db.update_task("T-MIXED-C2", status=TaskStatus.FAILED,
                   note="self-blocked: already shipped, nothing to do")
    # Child 3: completed cleanly
    db.insert_task(TaskRecord(
        id="T-MIXED-C3", brief="test",
        assigned_agent="qa_engineer", parent_task_id="T-MIXED",
        task_type="subtask",
    ))
    db.update_task("T-MIXED-C3", status=TaskStatus.COMPLETED)
    # Add completion report for child 3.
    db.insert_task_result(
        task_id="T-MIXED-C3", agent="qa_engineer", session_id="s",
        status="completed", confidence_score=90, output_summary="Done",
        risks_flagged=[],
    )

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    # Fire on the last terminal child (C2).
    _enqueue_parent_if_waiting(orch, "T-MIXED-C2")

    parent = db.get_task("T-MIXED")
    # Parent is woken (re-enqueued), NOT escalated.
    assert parent.status == TaskStatus.IN_PROGRESS, (
        f"owner should be woken, not escalated; got status {parent.status}"
    )
    assert parent.block_kind == BlockKind.DELEGATED

    # Parent is enqueued for its decision step.
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == "T-MIXED"


def test_per_slice_retry_ceiling_returns_second_failure_to_owner(runtime, db, monkeypatch):
    """THR-078: per-slice retry ceiling = 1.  A slice that fails, gets
    re-dispatched by the owner (revisit_of_task_id set), and fails again
    wakes the owning manager exactly once — even when the TOTAL failed
    sibling count is only 1. The manager, not the runtime, owns any later
    THR-181 escalation proposal."""
    import asyncio
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    # Root parent.
    db.insert_task(TaskRecord(
        id="T-RETRY2", brief="fan-out parent",
        assigned_agent="engineering_head",
        task_type="task",
    ))
    db.update_task("T-RETRY2", status=TaskStatus.IN_PROGRESS,
                   block_kind=BlockKind.DELEGATED, note="fan-out in flight")

    # Original slice FAILED (first attempt was not clean).
    # Per-slice ceiling: exactly one retry after the FIRST FAILURE.
    db.insert_task(TaskRecord(
        id="T-RETRY2-C1", brief="build feature X",
        assigned_agent="dev_agent", parent_task_id="T-RETRY2",
        task_type="subtask",
    ))
    db.update_task("T-RETRY2-C1", status=TaskStatus.FAILED,
                   note="first failure of this slice")
    db.insert_task_result(
        task_id="T-RETRY2-C1", agent="dev_agent", session_id="s",
        status="failed", confidence_score=0, output_summary="Failed",
        risks_flagged=[],
    )

    # Another child completed cleanly.
    db.insert_task(TaskRecord(
        id="T-RETRY2-C2", brief="build feature Y",
        assigned_agent="dev_agent", parent_task_id="T-RETRY2",
        task_type="subtask",
    ))
    db.update_task("T-RETRY2-C2", status=TaskStatus.COMPLETED)
    db.insert_task_result(
        task_id="T-RETRY2-C2", agent="dev_agent", session_id="s",
        status="completed", confidence_score=90, output_summary="Done",
        risks_flagged=[],
    )

    # Re-dispatched slice: revisit_of_task_id points to the COMPLETED C1.
    # The owner re-delegated this slice post-fanout-join; it fails (2nd
    # failure of this slice). Total failed siblings = 1 (just this one).
    db.insert_task(TaskRecord(
        id="T-RETRY2-C1-R", brief="re-dispatched: build feature X",
        assigned_agent="dev_agent", parent_task_id="T-RETRY2",
        revisit_of_task_id="T-RETRY2-C1",
        task_type="subtask",
    ))
    db.update_task("T-RETRY2-C1-R", status=TaskStatus.FAILED,
                   note="second failure of this slice — ceiling exhausted")

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    # Fire on the re-dispatched child's failure.
    _enqueue_parent_if_waiting(orch, "T-RETRY2-C1-R")

    parent = db.get_task("T-RETRY2")
    assert parent.status == TaskStatus.IN_PROGRESS
    assert parent.block_kind == BlockKind.DELEGATED
    assert orch._queue.qsize() == 1
    assert orch._queue.get_nowait() == ("test", "T-RETRY2")

    # The retry ceiling never performs a runtime escalation or creates an
    # authority-hook side effect; both FAILED child rows remain durable.
    rows = db.get_audit_logs("T-RETRY2")
    escalations = [row for row in rows if row["action"] == "escalation"]
    outcomes = [row for row in rows if row["action"] == "authority_hook"]
    assert not escalations
    assert not outcomes
    assert db.get_task("T-RETRY2-C1").status == TaskStatus.FAILED
    assert db.get_task("T-RETRY2-C1-R").status == TaskStatus.FAILED

    # Recovery re-entry can enqueue again, but does not make an escalation.
    _enqueue_parent_if_waiting(orch, "T-RETRY2-C1-R")
    replay_rows = db.get_audit_logs("T-RETRY2")
    assert not [row for row in replay_rows if row["action"] == "escalation"]
    assert not [row for row in replay_rows if row["action"] == "authority_hook"]


def test_runtime_retry_ceiling_never_enters_escalation_audit_commit(runtime, db):
    """The retry routing seam keeps fanout state and has no escalation audit."""
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    db.insert_task(TaskRecord(
        id="T-RETRY-ROLLBACK", brief="fan-out parent",
        assigned_agent="engineering_head", task_type="task",
    ))
    db.update_task(
        "T-RETRY-ROLLBACK", status=TaskStatus.IN_PROGRESS,
        block_kind=BlockKind.DELEGATED,
    )
    db.update_task_active_fanout("T-RETRY-ROLLBACK", '{"children": []}')
    db.insert_task(TaskRecord(
        id="T-RETRY-ROLLBACK-A", brief="first failure", assigned_agent="dev_agent",
        parent_task_id="T-RETRY-ROLLBACK", task_type="subtask",
    ))
    db.update_task("T-RETRY-ROLLBACK-A", status=TaskStatus.FAILED)
    db.insert_task(TaskRecord(
        id="T-RETRY-ROLLBACK-B", brief="retry failure", assigned_agent="dev_agent",
        parent_task_id="T-RETRY-ROLLBACK", revisit_of_task_id="T-RETRY-ROLLBACK-A",
        task_type="subtask",
    ))
    db.update_task("T-RETRY-ROLLBACK-B", status=TaskStatus.FAILED)

    orch = Orchestrator(
        db=db, settings=Settings(), paths=runtime, slug="test",
        teams=TeamsRegistry.load(runtime.root),
    )
    orch._queue = _SlugQueue()
    _enqueue_parent_if_waiting(orch, "T-RETRY-ROLLBACK-B")

    parent = db.get_task("T-RETRY-ROLLBACK")
    assert parent.status == TaskStatus.IN_PROGRESS
    assert parent.block_kind == BlockKind.DELEGATED
    assert parent.active_fanout == '{"children": []}'
    assert not [
        row for row in db.get_audit_logs("T-RETRY-ROLLBACK")
        if row["action"] in {"escalation", "authority_hook"}
    ]
    assert orch._queue.get_nowait() == ("test", "T-RETRY-ROLLBACK")


def test_runtime_escalation_write_failure_has_no_audit_residue(db):
    """A task-state write failure occurs before either audit append commits."""
    import sqlite3

    db.insert_task(TaskRecord(id="T-RUNTIME-WRITE-FAIL", brief="root"))
    db.execute("""
        CREATE TRIGGER reject_runtime_escalation
        BEFORE UPDATE OF status ON tasks
        WHEN NEW.id = 'T-RUNTIME-WRITE-FAIL' AND NEW.status = 'escalated'
        BEGIN SELECT RAISE(ABORT, 'injected escalation write failure'); END
    """)
    with pytest.raises(sqlite3.IntegrityError, match="escalation write failure"):
        db.try_escalate_runtime(
            "T-RUNTIME-WRITE-FAIL", reason="runtime failure",
            agent="orchestrator", reason_code="runtime_retry_ceiling",
        )
    assert db.get_task("T-RUNTIME-WRITE-FAIL").status == TaskStatus.PENDING
    assert not db.get_audit_logs("T-RUNTIME-WRITE-FAIL")


def test_regression_all_completed_clean_fanout_still_happy_path(runtime, db, monkeypatch):
    """THR-078 regression: a clean all-completed fan-out still takes the
    happy path and wakes the owner for its next decision step."""
    import asyncio
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    db.insert_task(TaskRecord(
        id="T-CLEAN", brief="fan-out parent",
        assigned_agent="engineering_head",
        task_type="task",
    ))
    db.update_task("T-CLEAN", status=TaskStatus.IN_PROGRESS,
                   block_kind=BlockKind.DELEGATED, note="fan-out in flight")

    # All children completed.
    for i, (cid, agent) in enumerate([
        ("T-CLEAN-C1", "dev_agent"),
        ("T-CLEAN-C2", "dev_agent"),
        ("T-CLEAN-C3", "qa_engineer"),
    ]):
        db.insert_task(TaskRecord(
            id=cid, brief=f"task {i}",
            assigned_agent=agent, parent_task_id="T-CLEAN",
            task_type="subtask",
        ))
        db.update_task(cid, status=TaskStatus.COMPLETED)
        db.insert_task_result(
            task_id=cid, agent=agent, session_id="s",
            status="completed", confidence_score=90, output_summary="Done",
            risks_flagged=[],
        )

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    _enqueue_parent_if_waiting(orch, "T-CLEAN-C3")

    parent = db.get_task("T-CLEAN")
    assert parent.status == TaskStatus.IN_PROGRESS
    assert parent.block_kind == BlockKind.DELEGATED
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == "T-CLEAN"


def test_retry_of_completed_predecessor_does_not_escalate_on_first_failure(runtime, db, monkeypatch):
    """THR-078 Fix 1 negative: a retry of a previously COMPLETED (successful)
    slice does NOT exhaust the ceiling on its first failure.  Ceiling=1 means
    exactly ONE retry after a slice's FIRST FAILURE; a COMPLETED predecessor
    means this is a fresh dispatch, not a retry — so the ceiling is not
    triggered.

    RED test: current code treats ANY same-parent ancestor as exhausting the
    ceiling, so this test will FAIL against the unfixed code."""
    import asyncio
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    db.insert_task(TaskRecord(
        id="T-CMPL", brief="fan-out parent",
        assigned_agent="engineering_head",
        task_type="task",
    ))
    db.update_task("T-CMPL", status=TaskStatus.IN_PROGRESS,
                   block_kind=BlockKind.DELEGATED, note="fan-out in flight")

    # Original slice COMPLETED (clean first attempt).
    db.insert_task(TaskRecord(
        id="T-CMPL-C1", brief="build feature X",
        assigned_agent="dev_agent", parent_task_id="T-CMPL",
        task_type="subtask",
    ))
    db.update_task("T-CMPL-C1", status=TaskStatus.COMPLETED)
    db.insert_task_result(
        task_id="T-CMPL-C1", agent="dev_agent", session_id="s",
        status="completed", confidence_score=90, output_summary="Done",
        risks_flagged=[],
    )

    # Another child completed cleanly.
    db.insert_task(TaskRecord(
        id="T-CMPL-C2", brief="build feature Y",
        assigned_agent="dev_agent", parent_task_id="T-CMPL",
        task_type="subtask",
    ))
    db.update_task("T-CMPL-C2", status=TaskStatus.COMPLETED)
    db.insert_task_result(
        task_id="T-CMPL-C2", agent="dev_agent", session_id="s",
        status="completed", confidence_score=90, output_summary="Done",
        risks_flagged=[],
    )

    # Re-dispatched slice: revisit_of_task_id points to the COMPLETED C1.
    # The owner re-delegated this slice post-fanout-join; it fails (first
    # failure of this slice). This is NOT an exhaustion — the predecessor
    # was COMPLETED, not FAILED.
    db.insert_task(TaskRecord(
        id="T-CMPL-C1-R", brief="re-dispatched: build feature X",
        assigned_agent="dev_agent", parent_task_id="T-CMPL",
        revisit_of_task_id="T-CMPL-C1",
        task_type="subtask",
    ))
    db.update_task("T-CMPL-C1-R", status=TaskStatus.FAILED,
                   note="first failure of this re-dispatched slice")

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    _enqueue_parent_if_waiting(orch, "T-CMPL-C1-R")

    parent = db.get_task("T-CMPL")
    # COMPLETED predecessor → NOT escalated, owner is woken instead.
    assert parent.status == TaskStatus.IN_PROGRESS, (
        f"COMPLETED predecessor should NOT trigger escalation; "
        f"owner should be woken; got status {parent.status}"
    )
    assert parent.block_kind == BlockKind.DELEGATED
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == "T-CMPL"


def test_delegate_without_revisit_of_task_id_when_failed_sibling_is_rejected(runtime, db, monkeypatch):
    """THR-078 Fix 4: a delegate that re-targets the agent of a FAILED sibling
    WITHOUT revisit_of_task_id is HARD-REJECTED.  The retry-link field is
    MANDATORY — even the first retry of a failed slice is DISALLOWED
    without the field.

    RED test: current code silently allows the delegate through, treating
    it as an unlinked fresh dispatch that resets the ceiling."""
    import json
    from runtime.orchestrator.orchestrator import Orchestrator

    (runtime.workspaces_dir / "dev_agent").mkdir(parents=True)

    db.insert_task(TaskRecord(
        id="T-NOLINK", brief="fan-out parent",
        assigned_agent="engineering_head",
        task_type="task",
    ))

    # A FAILED child targeting dev_agent.
    db.insert_task(TaskRecord(
        id="T-NOLINK-C1", brief="build feature X",
        assigned_agent="dev_agent", parent_task_id="T-NOLINK",
        task_type="subtask",
    ))
    db.update_task("T-NOLINK-C1", status=TaskStatus.FAILED,
                   note="failed to build feature X")

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    # Mock the executor: owner returns delegate to dev_agent WITHOUT
    # revisit_of_task_id even though dev_agent has a failed sibling.
    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary=json.dumps({
                "action": "delegate",
                "agent": "dev_agent",
                "prompt": "retry build feature X",
                # OMIT revisit_of_task_id — should be REJECTED.
            }),
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    # Run the step — the delegate handler should REJECT before spawning.
    orch.run_step("T-NOLINK")

    # After rejection, parent should still be PENDING (re-enqueued for retry),
    # NOT in_progress(delegated).
    parent = db.get_task("T-NOLINK")
    assert parent.status == TaskStatus.PENDING, (
        f"Parent should be PENDING after reject; got {parent.status}"
    )
    assert parent.block_kind is None, (
        f"block_kind should be None after reject; got {parent.block_kind}"
    )

    # No NEW child should have been spawned (only T-NOLINK-C1 was pre-existing).
    children = db.get_children("T-NOLINK")
    assert len(children) == 1, (
        f"No new child should be spawned; got {len(children)} children"
    )
    assert children[0] == "T-NOLINK-C1"

    # Parent should be re-enqueued for another decision step.
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == "T-NOLINK"


def test_fanout_without_revisit_of_task_id_when_failed_sibling_is_rejected(runtime, db, monkeypatch):
    """THR-078: fanout rejects the whole decision if any retry lacks its link."""
    import json
    from runtime.orchestrator.orchestrator import Orchestrator

    (runtime.workspaces_dir / "dev_agent").mkdir(parents=True)
    (runtime.workspaces_dir / "qa_engineer").mkdir(parents=True)
    db.insert_task(TaskRecord(
        id="T-FANOUT-NOLINK", brief="fan-out parent",
        assigned_agent="engineering_head", task_type="task",
    ))
    db.insert_task(TaskRecord(
        id="T-FANOUT-NOLINK-C1", brief="build feature X",
        assigned_agent="dev_agent", parent_task_id="T-FANOUT-NOLINK",
        task_type="subtask",
    ))
    db.update_task("T-FANOUT-NOLINK-C1", status=TaskStatus.FAILED)

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(output_summary=json.dumps({
            "action": "fanout",
            "children": [
                {"agent": "dev_agent", "prompt": "retry feature X"},
                {"agent": "qa_engineer", "prompt": "test feature Y"},
            ],
            "width_cap_ack": 2,
        }))

    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)
    orch.run_step("T-FANOUT-NOLINK")

    parent = db.get_task("T-FANOUT-NOLINK")
    assert parent.status == TaskStatus.PENDING
    assert parent.block_kind is None
    assert db.get_children("T-FANOUT-NOLINK") == ["T-FANOUT-NOLINK-C1"]
    assert orch._queue.qsize() == 1
    _, queued_id = orch._queue.get_nowait()
    assert queued_id == "T-FANOUT-NOLINK"


def test_delegate_with_invalid_revisit_link_is_rejected(runtime, db, monkeypatch):
    """THR-078: a retry link must name this parent's FAILED same-agent child."""
    import json
    from runtime.orchestrator.orchestrator import Orchestrator

    (runtime.workspaces_dir / "dev_agent").mkdir(parents=True)
    db.insert_task(TaskRecord(
        id="T-BADLINK", brief="parent", assigned_agent="engineering_head",
        task_type="task",
    ))
    db.insert_task(TaskRecord(
        id="T-BADLINK-C1", brief="failed feature", assigned_agent="dev_agent",
        parent_task_id="T-BADLINK", task_type="subtask",
    ))
    db.update_task("T-BADLINK-C1", status=TaskStatus.FAILED)

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(output_summary=json.dumps({
            "action": "delegate",
            "agent": "dev_agent",
            "prompt": "retry feature",
            "revisit_of_task_id": "TASK-NOT-A-FAILED-SIBLING",
        }))

    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)
    orch.run_step("T-BADLINK")

    parent = db.get_task("T-BADLINK")
    assert parent.status == TaskStatus.PENDING
    assert parent.block_kind is None
    assert db.get_children("T-BADLINK") == ["T-BADLINK-C1"]
    assert orch._queue.qsize() == 1


@pytest.mark.parametrize("invalid_link, target_agent", [
    ("T-OTHER-FAILED", "dev_agent"),
    ("T-BADLINK-C1", "qa_engineer"),
])
def test_delegate_rejects_wrong_parent_or_agent_retry_link(
    runtime, db, monkeypatch, invalid_link, target_agent,
):
    """Retry provenance must be this parent's failed child for that agent."""
    from runtime.orchestrator.orchestrator import Orchestrator

    for name in ("dev_agent", "qa_engineer", "engineering_head"):
        (runtime.workspaces_dir / name).mkdir(parents=True, exist_ok=True)
    db.insert_task(TaskRecord(
        id="T-BADLINK", brief="parent", assigned_agent="engineering_head",
        task_type="task",
    ))
    db.insert_task(TaskRecord(
        id="T-BADLINK-C1", brief="failed child", assigned_agent="dev_agent",
        parent_task_id="T-BADLINK", task_type="subtask",
    ))
    db.insert_task(TaskRecord(
        id="T-OTHER", brief="other parent", assigned_agent="engineering_head", task_type="task",
    ))
    db.insert_task(TaskRecord(
        id="T-OTHER-FAILED", brief="foreign failure", assigned_agent="dev_agent",
        parent_task_id="T-OTHER", task_type="subtask",
    ))
    for task_id in ("T-BADLINK-C1", "T-OTHER-FAILED"):
        db.update_task(task_id, status=TaskStatus.FAILED)
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()
    monkeypatch.setattr(orch, "_run_agent", lambda *args, **kwargs: (
        _make_result(), _make_report(output_summary=json.dumps({
            "action": "delegate", "agent": target_agent, "prompt": "bad retry",
            "revisit_of_task_id": invalid_link,
        })),
    ))

    orch.run_step("T-BADLINK")
    assert db.get_children("T-BADLINK") == ["T-BADLINK-C1"]
    assert db.get_task("T-BADLINK").status == TaskStatus.PENDING
    assert orch._queue.qsize() == 1


def test_fanout_retry_link_reaches_second_failure_escalation(runtime, db, monkeypatch):
    """A live sibling blocks the linked-second-failure owner wake.

    Once that sibling settles, the real claim/prompt seam runs exactly once
    and carries the failed leaf's durable context to the decision owner.
    """
    import json
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    (runtime.workspaces_dir / "dev_agent").mkdir(parents=True)
    (runtime.workspaces_dir / "qa_engineer").mkdir(parents=True)
    db.insert_task(TaskRecord(
        id="T-FANOUT-RETRY", brief="fan-out parent",
        assigned_agent="engineering_head", task_type="task",
    ))

    responses = [
        {
            "action": "fanout",
            "children": [
                {"agent": "dev_agent", "prompt": "build feature X"},
                {"agent": "qa_engineer", "prompt": "test feature Y"},
            ],
            "width_cap_ack": 2,
        },
        {
            "action": "fanout",
            "children": [
                {
                    "agent": "dev_agent",
                    "prompt": "retry feature X",
                    "revisit_of_task_id": "REPLACED-BEFORE-SECOND-ROUND",
                },
                {"agent": "qa_engineer", "prompt": "test feature Z"},
            ],
            "width_cap_ack": 2,
        },
        {"action": "done", "summary": "owner handled linked failure"},
    ]
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    prompts: list[str] = []

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        response = responses.pop(0)
        if task_id == "T-FANOUT-RETRY":
            prompts.append(prompt)
        if response.get("children") and response["children"][0].get("revisit_of_task_id"):
            response["children"][0]["revisit_of_task_id"] = failed_slice_id
        return _make_result(), _make_report(output_summary=json.dumps(response))

    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)
    orch.run_step("T-FANOUT-RETRY")

    first_round = [db.get_task(cid) for cid in db.get_children("T-FANOUT-RETRY")]
    failed_slice = next(child for child in first_round if child.assigned_agent == "dev_agent")
    failed_slice_id = failed_slice.id
    first_round_qa = next(child for child in first_round if child.assigned_agent == "qa_engineer")
    db.update_task(failed_slice.id, status=TaskStatus.FAILED)
    db.update_task(first_round_qa.id, status=TaskStatus.COMPLETED)

    # All first-round siblings are terminal, so the owner may fan out again.
    orch.run_step("T-FANOUT-RETRY")
    all_children = [db.get_task(cid) for cid in db.get_children("T-FANOUT-RETRY")]
    retry_slice = next(
        child for child in all_children
        if child.assigned_agent == "dev_agent" and child.id != failed_slice.id
    )
    second_round_qa = next(
        child for child in all_children
        if child.assigned_agent == "qa_engineer" and child.id != first_round_qa.id
    )
    assert retry_slice.revisit_of_task_id == failed_slice.id

    db.update_task(retry_slice.id, status=TaskStatus.FAILED, note="terminal linked failure")
    db.insert_task_result(
        task_id=retry_slice.id, agent="dev_agent", session_id="fanout-retry",
        status="failed", confidence_score=0, output_summary="terminal linked failure",
        verdict="FAIL",
    )
    # The linked retry has failed, but its second-round sibling is still live:
    # the barrier must not wake the owner prematurely.
    queued_before_live_barrier = orch._queue.qsize()
    _enqueue_parent_if_waiting(orch, retry_slice.id)
    assert orch._queue.qsize() == queued_before_live_barrier

    db.update_task(second_round_qa.id, status=TaskStatus.COMPLETED)
    _enqueue_parent_if_waiting(orch, retry_slice.id)
    orch.run_step("T-FANOUT-RETRY")

    parent = db.get_task("T-FANOUT-RETRY")
    assert parent.status == TaskStatus.COMPLETED
    final_prompt = prompts[-1]
    assert f"task_id={retry_slice.id}" in final_prompt
    assert "status=failed" in final_prompt
    assert "verdict=FAIL" in final_prompt
    assert "reason=terminal linked failure" in final_prompt


def test_delegate_with_revisit_of_task_id_e2e_ceiling_wakes_owner(runtime, db, monkeypatch):
    """THR-078 Fix 2: end-to-end.  Parent with a failed slice + uncleared
    active_fanout wakes → join context is injected.  Simulate the second
    phase: the retry child (which carries revisit_of_task_id pointing at the
    FAILED predecessor) itself fails → the owner is woken.

    This validates the real _enqueue_parent_if_waiting path: the revisited
    FAILED ancestor under the same parent triggers the ceiling."""
    import asyncio
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    db.insert_task(TaskRecord(
        id="T-E2E", brief="fan-out parent",
        assigned_agent="engineering_head",
        task_type="task",
    ))
    db.update_task("T-E2E", status=TaskStatus.IN_PROGRESS,
                   block_kind=BlockKind.DELEGATED, note="fan-out in flight")

    # FAILED predecessor (original slice, first failure).
    db.insert_task(TaskRecord(
        id="T-E2E-C1-ORIG", brief="build feature Y (original)",
        assigned_agent="dev_agent", parent_task_id="T-E2E",
        task_type="subtask",
    ))
    db.update_task("T-E2E-C1-ORIG", status=TaskStatus.FAILED,
                   note="first failure")

    # Another child completed cleanly.
    db.insert_task(TaskRecord(
        id="T-E2E-C2", brief="build feature Z",
        assigned_agent="qa_engineer", parent_task_id="T-E2E",
        task_type="subtask",
    ))
    db.update_task("T-E2E-C2", status=TaskStatus.COMPLETED)

    # Retry child: revisit_of_task_id points at the FAILED predecessor.
    # This is the retry after the first failure — when IT fails, the ceiling
    # fires (1 retry exhausted).
    db.insert_task(TaskRecord(
        id="T-E2E-C1-R", brief="retry: build feature Y",
        assigned_agent="dev_agent", parent_task_id="T-E2E",
        revisit_of_task_id="T-E2E-C1-ORIG",
        task_type="subtask",
    ))
    db.update_task("T-E2E-C1-R", status=TaskStatus.FAILED,
                   note="second failure — ceiling exhausted")

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    _enqueue_parent_if_waiting(orch, "T-E2E-C1-R")

    parent = db.get_task("T-E2E")
    assert parent.status == TaskStatus.IN_PROGRESS
    assert parent.block_kind == BlockKind.DELEGATED
    assert orch._queue.qsize() == 1


# ═══════════════════════════════════════════════════════════════════
# THR-183 / TASK-5235 — retry-ceiling escalation attribution fix
# ═══════════════════════════════════════════════════════════════════


def _escalation_audit_rows(db: Database, task_id: str) -> list[dict]:
    return [a for a in db.get_audit_logs(task_id) if a["action"] == "escalation"]


def test_thr183_completed_child_after_retired_failed_lineage_does_not_escalate(
    runtime, db,
):
    """A later COMPLETED descendant retires earlier FAILED retry attempts.
    A normal parent wake initiated by the completed child MUST NOT scan the
    stale failed siblings and escalate using their notes, and must not mutate
    the parent's active fan-out state."""
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.fanout import FanoutState
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    db.insert_task(TaskRecord(
        id="T-RET1", brief="parent", assigned_agent="engineering_head",
        task_type="task",
    ))
    db.update_task(
        "T-RET1", status=TaskStatus.IN_PROGRESS,
        block_kind=BlockKind.DELEGATED, note="waiting",
    )

    # Seed a meaningful active_fanout so the no-state-change contract is
    # actually exercised (active_chain stays None for this completed-child
    # signature; the chain branch is not entered).
    fanout = FanoutState(
        children_ids=["T-RET1-C"],
        children_details=[{"agent": "qa_engineer", "prompt": "other slice"}],
        width=1,
        manager_agent="engineering_head",
    )
    db.update_task_active_fanout("T-RET1", fanout.serialize())

    # Original quota failure.
    db.insert_task(TaskRecord(
        id="T-RET1-A", brief="slice A", assigned_agent="dev_agent",
        parent_task_id="T-RET1", task_type="subtask",
    ))
    db.update_task("T-RET1-A", status=TaskStatus.FAILED, note="quota exceeded")

    # Retry also failed (ceiling exhausted at this point in real life).
    db.insert_task(TaskRecord(
        id="T-RET1-A-R", brief="retry slice A", assigned_agent="dev_agent",
        parent_task_id="T-RET1", revisit_of_task_id="T-RET1-A",
        task_type="subtask",
    ))
    db.update_task(
        "T-RET1-A-R", status=TaskStatus.FAILED,
        note="second failure — review rejected",
    )

    # Owner resolved and a later retry completed, retiring the lineage.
    db.insert_task(TaskRecord(
        id="T-RET1-A-R2", brief="final retry slice A", assigned_agent="dev_agent",
        parent_task_id="T-RET1", revisit_of_task_id="T-RET1-A-R",
        task_type="subtask",
    ))
    db.update_task("T-RET1-A-R2", status=TaskStatus.COMPLETED, note="done")

    # Another completed child triggers the parent wake.
    db.insert_task(TaskRecord(
        id="T-RET1-C", brief="other slice", assigned_agent="qa_engineer",
        parent_task_id="T-RET1", task_type="subtask",
    ))
    db.update_task("T-RET1-C", status=TaskStatus.COMPLETED)

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    parent_before = db.get_task("T-RET1")
    _enqueue_parent_if_waiting(orch, "T-RET1-C")

    parent = db.get_task("T-RET1")
    assert parent.status == TaskStatus.IN_PROGRESS, (
        f"completed-child wake with retired lineage must not escalate; got {parent.status}"
    )
    assert parent.block_kind == BlockKind.DELEGATED
    assert parent.note == parent_before.note
    assert parent.active_fanout == parent_before.active_fanout
    assert _escalation_audit_rows(db, "T-RET1") == []
    assert orch._queue.qsize() == 1
    assert orch._queue.get_nowait() == ("test", "T-RET1")


def test_thr183_recovered_lineage_does_not_re_escalate_on_startup_style_ancestor_wake(
    runtime, db,
):
    """Startup/recovery or any caller may invoke _enqueue_parent_if_waiting with
    a recovered FAILED ancestor. A later SUPERSEDED descendant in the same
    revisit_of_task_id lineage must retire the earlier failures, prevent re-
    escalation, and must not mutate parent state — including meaningful non-null
    active_chain and active_fanout metadata."""
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.chain import ChainState
    from runtime.orchestrator.fanout import FanoutState
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    db.insert_task(TaskRecord(
        id="T-REC2", brief="parent", assigned_agent="engineering_head",
        task_type="task",
    ))
    db.update_task(
        "T-REC2", status=TaskStatus.IN_PROGRESS,
        block_kind=BlockKind.DELEGATED, note="waiting",
    )

    # Seed meaningful, valid active_chain and active_fanout metadata. The bug
    # is that the FAILED-chain branch used to clear active_chain before the
    # retired-lineage check could prove the lineage was resolved.
    chain = ChainState(
        step_index=0,
        first_leg_expect_verdict="PASS",
        legs=[],
        step_audit_id=1,
    )
    fanout = FanoutState(
        children_ids=["T-REC2-C"],
        children_details=[{"agent": "qa_engineer", "prompt": "other slice"}],
        width=1,
        manager_agent="engineering_head",
    )
    db.update_task_active_chain("T-REC2", chain.serialize())
    db.update_task_active_fanout("T-REC2", fanout.serialize())

    # Exhausted historical FAILED lineage.
    db.insert_task(TaskRecord(
        id="T-REC2-A", brief="slice A", assigned_agent="dev_agent",
        parent_task_id="T-REC2", task_type="subtask",
    ))
    db.update_task("T-REC2-A", status=TaskStatus.FAILED, note="quota exceeded")

    db.insert_task(TaskRecord(
        id="T-REC2-A-R", brief="retry slice A", assigned_agent="dev_agent",
        parent_task_id="T-REC2", revisit_of_task_id="T-REC2-A",
        task_type="subtask",
    ))
    db.update_task(
        "T-REC2-A-R", status=TaskStatus.FAILED,
        note="second failure — review rejected",
    )

    # Later descendant SUPERSEDED via revisit_of_task_id → lineage retired.
    db.insert_task(TaskRecord(
        id="T-REC2-A-R2", brief="superseded retry slice A", assigned_agent="dev_agent",
        parent_task_id="T-REC2", revisit_of_task_id="T-REC2-A-R",
        task_type="subtask",
    ))
    db.update_task("T-REC2-A-R2", status=TaskStatus.SUPERSEDED, note="replaced by owner")

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    parent_before = db.get_task("T-REC2")
    # Simulate a startup/recovery path invoking on the stale ancestor.
    _enqueue_parent_if_waiting(orch, "T-REC2-A")

    parent = db.get_task("T-REC2")
    assert parent.status == TaskStatus.IN_PROGRESS, (
        f"recovered-ancestor wake with SUPERSEDED descendant must not escalate; got {parent.status}"
    )
    assert parent.block_kind == BlockKind.DELEGATED
    assert parent.note == parent_before.note
    assert parent.active_chain == parent_before.active_chain
    assert parent.active_fanout == parent_before.active_fanout
    assert _escalation_audit_rows(db, "T-REC2") == []
    assert orch._queue.qsize() == 1
    assert orch._queue.get_nowait() == ("test", "T-REC2")

    # A second recovery-style call on the same retired ancestor must remain a
    # bounded no-op: parent state unchanged, another normal wake queued.
    _enqueue_parent_if_waiting(orch, "T-REC2-A")
    parent_after = db.get_task("T-REC2")
    assert parent_after.status == TaskStatus.IN_PROGRESS
    assert parent_after.active_chain == parent_before.active_chain
    assert parent_after.active_fanout == parent_before.active_fanout
    assert _escalation_audit_rows(db, "T-REC2") == []
    assert orch._queue.qsize() == 1
    assert orch._queue.get_nowait() == ("test", "T-REC2")


def test_thr183_genuine_unresolved_second_failure_wakes_owner_without_escalation(
    runtime, db, monkeypatch,
):
    """A true unresolved second failure wakes its owner without a runtime
    escalation; the durable leaf report remains the causal context."""
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.run_step import (
        _build_prior_steps_from_db,
        _enqueue_parent_if_waiting,
    )

    db.insert_task(TaskRecord(
        id="T-GENU", brief="parent", assigned_agent="engineering_head",
        task_type="task",
    ))
    db.update_task(
        "T-GENU", status=TaskStatus.IN_PROGRESS,
        block_kind=BlockKind.DELEGATED, note="waiting",
    )

    db.insert_task(TaskRecord(
        id="T-GENU-A", brief="slice A", assigned_agent="dev_agent",
        parent_task_id="T-GENU", task_type="subtask",
    ))
    db.update_task("T-GENU-A", status=TaskStatus.FAILED, note="quota exceeded")

    db.insert_task(TaskRecord(
        id="T-GENU-A-R", brief="retry slice A", assigned_agent="dev_agent",
        parent_task_id="T-GENU", revisit_of_task_id="T-GENU-A",
        task_type="subtask",
    ))
    db.update_task(
        "T-GENU-A-R", status=TaskStatus.FAILED,
        note="review rejected",
    )
    db.insert_task_result(
        task_id="T-GENU-A-R", agent="dev_agent", session_id="s",
        status="failed", confidence_score=0, output_summary="review rejected",
        verdict="FAIL",
    )

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    # The manager's shipping prompt history carries the causal leaf's durable
    # identity, terminal status, verdict, reason, and mechanical revisit link.
    leaf_history = _build_prior_steps_from_db(orch, "T-GENU")[-1]
    assert leaf_history.action.startswith("delegate [T-GENU-A-R]:")
    assert "task_id=T-GENU-A-R" in leaf_history.result_summary
    assert "status=failed" in leaf_history.result_summary
    assert "verdict=FAIL" in leaf_history.result_summary
    assert "revisit_of_task_id=T-GENU-A" in leaf_history.result_summary
    assert "reason=review rejected" in leaf_history.result_summary

    _enqueue_parent_if_waiting(orch, "T-GENU-A-R")

    parent = db.get_task("T-GENU")
    assert parent.status == TaskStatus.IN_PROGRESS
    assert parent.block_kind == BlockKind.DELEGATED
    assert db.get_task("T-GENU-A-R").note == "review rejected"
    assert _escalation_audit_rows(db, "T-GENU") == []
    assert orch._queue.qsize() == 1

    # A duplicate terminal/recovery callback may legitimately leave a second
    # queue delivery.  Consume both through the real run_step claim seam: the
    # first claims the parked manager and the second must be a harmless stale
    # delivery, not a second manager invocation or decision side effect.
    _enqueue_parent_if_waiting(orch, "T-GENU-A-R")
    assert orch._queue.qsize() == 2
    manager_calls: list[str] = []
    monkeypatch.setattr(orch, "_run_agent", lambda task_id, *args, **kwargs: (
        manager_calls.append(task_id),
        _make_result(),
        _make_report(output_summary=json.dumps({"action": "done", "summary": "handled"})),
    )[1:])
    orch.run_step(orch._queue.get_nowait()[1])
    orch.run_step(orch._queue.get_nowait()[1])
    assert manager_calls == ["T-GENU"]
    assert db.get_task("T-GENU").status == TaskStatus.COMPLETED
    assert len([row for row in db.get_audit_logs("T-GENU")
                if row["action"] == "orchestration_step"]) == 1
    assert _escalation_audit_rows(db, "T-GENU") == []


def test_fanout_dispatched_manager_owns_failed_child_not_carrier(runtime, db, monkeypatch):
    """A fanout-dispatched manager owns linked failures locally, not as a carrier."""
    from runtime.orchestrator.orchestrator import Orchestrator

    for name in ("engineering_head", "dev_agent"):
        (runtime.workspaces_dir / name).mkdir(parents=True, exist_ok=True)
    db.insert_task(TaskRecord(id="T-OWNER-ROOT", brief="root", assigned_agent="engineering_head"))
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()
    owner_turns = 0

    def run(task_id, *args, **kwargs):
        nonlocal owner_turns
        if task_id == "T-OWNER-ROOT":
            decision = {"action": "fanout", "width_cap_ack": 2, "children": [
                {"agent": "engineering_head", "prompt": "nested decision owner"},
                {"agent": "dev_agent", "prompt": "live sibling"},
            ]}
        elif db.get_task(task_id).task_type == "task":
            owner_turns += 1
            if owner_turns == 1:
                decision = {"action": "delegate", "agent": "dev_agent", "prompt": "bounded child"}
            elif owner_turns == 2:
                decision = {
                    "action": "delegate", "agent": "dev_agent", "prompt": "linked recovery",
                    "revisit_of_task_id": db.get_children(task_id)[-1],
                }
            else:
                decision = {"action": "done", "summary": "local manager decision"}
        else:
            return _make_result(success=False), None
        return _make_result(), _make_report(output_summary=json.dumps(decision))

    monkeypatch.setattr(orch, "_run_agent", run)
    orch.run_step("T-OWNER-ROOT")
    owner = next(db.get_task(cid) for cid in db.get_children("T-OWNER-ROOT") if db.get_task(cid).task_type == "task")
    orch.run_step(owner.id)
    leaf = db.get_children(owner.id)[0]
    orch.run_step(leaf)

    assert db.get_task(leaf).status == TaskStatus.FAILED
    assert db.get_task(owner.id).status == TaskStatus.IN_PROGRESS
    assert db.get_task(owner.id).block_kind == BlockKind.DELEGATED
    orch.run_step(owner.id)
    retry = db.get_children(owner.id)[-1]
    assert db.get_task(retry).revisit_of_task_id == leaf
    orch.run_step(retry)
    assert db.get_task(retry).status == TaskStatus.FAILED
    orch.run_step(owner.id)
    assert owner_turns == 3
    assert db.get_task(owner.id).status == TaskStatus.COMPLETED
    outer = db.get_task("T-OWNER-ROOT")
    assert outer.status == TaskStatus.IN_PROGRESS
    assert outer.block_kind == BlockKind.DELEGATED


def test_serial_second_failure_wakes_owner_with_real_terminal_and_revised_dispatch(
    runtime, db, monkeypatch,
):
    """A real terminal sequence keeps both failed rows and returns control.

    This is intentionally a shipping-seam test rather than seeded FAILED
    rows: the first delegated child fails through ``run_step``, the owner
    consumes that wake and dispatches a valid linked revision, and that child
    also fails through ``run_step``.  The final owner prompt therefore proves
    the durable causal id/status/verdict/reason/revisit context available to
    the manager, while duplicate delivery cannot invoke it twice.
    """
    from runtime.orchestrator.orchestrator import Orchestrator

    (runtime.workspaces_dir / "engineering_head").mkdir(parents=True, exist_ok=True)
    (runtime.workspaces_dir / "dev_agent").mkdir(parents=True, exist_ok=True)
    db.insert_task(TaskRecord(
        id="T-SERIAL", brief="root", assigned_agent="engineering_head",
        task_type="task",
    ))
    orch = Orchestrator(
        db=db, settings=Settings(), paths=runtime, slug="test",
        teams=TeamsRegistry.load(runtime.root),
    )
    orch._queue = _SlugQueue()
    prompts: list[str] = []
    owner_turns = 0

    def run(task_id, agent, prompt, **kwargs):
        nonlocal owner_turns
        if task_id == "T-SERIAL":
            owner_turns += 1
            prompts.append(prompt)
            if owner_turns == 1:
                decision = {"action": "delegate", "agent": "dev_agent", "prompt": "original"}
            elif owner_turns == 2:
                original = db.get_children("T-SERIAL")[0]
                decision = {
                    "action": "delegate", "agent": "dev_agent", "prompt": "revised",
                    "revisit_of_task_id": original,
                }
            else:
                # This is the post-second-failure owner turn.  It must be a
                # real linked recovery decision, not an early ``done``.
                decision = {
                    "action": "delegate", "agent": "dev_agent", "prompt": "third revision",
                    "revisit_of_task_id": db.get_children("T-SERIAL")[-1],
                }
            return _make_result(), _make_report(output_summary=json.dumps(decision))
        ordinal = len(db.get_children("T-SERIAL"))
        return _make_result(), _make_report(
            output_summary=f"terminal failure {ordinal}", status="blocked", verdict="FAIL",
        )

    monkeypatch.setattr(orch, "_run_agent", run)
    orch.run_step("T-SERIAL")
    original = db.get_children("T-SERIAL")[0]
    orch.run_step(original)
    assert db.get_task(original).status is TaskStatus.FAILED
    db.insert_task_result(
        task_id=original, agent="dev_agent", session_id="serial-original",
        status="failed", confidence_score=0, output_summary="terminal failure 1",
        verdict="FAIL",
    )
    original_note = db.get_task(original).note

    # Consume the first manager wake and dispatch a linked, revised child.
    orch.run_step("T-SERIAL")
    revised = db.get_children("T-SERIAL")[-1]
    assert db.get_task(revised).revisit_of_task_id == original
    orch.run_step(revised)
    assert db.get_task(revised).status is TaskStatus.FAILED
    db.insert_task_result(
        task_id=revised, agent="dev_agent", session_id="serial-revised",
        status="failed", confidence_score=0, output_summary="terminal failure 2",
        verdict="FAIL",
    )
    revised_note = db.get_task(revised).note

    # A duplicate terminal callback may enqueue a stale delivery; the real
    # claim seam admits only one manager invocation.
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting
    _enqueue_parent_if_waiting(orch, revised)
    orch.run_step("T-SERIAL")
    orch.run_step("T-SERIAL")

    children = db.get_children("T-SERIAL")
    third = children[-1]
    assert owner_turns == 3
    assert len(children) == 3
    assert db.get_task(third).revisit_of_task_id == revised
    assert db.get_task(original).status is TaskStatus.FAILED
    assert db.get_task(revised).status is TaskStatus.FAILED
    assert db.get_task(original).note == original_note
    assert db.get_task(revised).note == revised_note
    final_prompt = prompts[-1]
    assert f"task_id={revised}" in final_prompt
    assert "status=failed" in final_prompt
    assert "verdict=FAIL" in final_prompt
    assert "reason=self-blocked: terminal failure 2" in final_prompt
    assert f"revisit_of_task_id={original}" in final_prompt
    assert not [
        row for row in db.get_audit_logs("T-SERIAL")
        if row["action"] in {"escalation", "authority_hook"}
    ]


def test_passive_carrier_join_keeps_causal_leaf_details(runtime, db, monkeypatch):
    """The actual outer-manager prompt names both carrier and causal leaf."""
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    for name in ("engineering_head", "dev_agent", "qa_engineer"):
        (runtime.workspaces_dir / name).mkdir(parents=True, exist_ok=True)
    db.insert_task(TaskRecord(id="T-CARRIER-ROOT", brief="root", assigned_agent="engineering_head"))
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()
    decision = {"action": "fanout", "width_cap_ack": 2, "children": [
        {"agent": "dev_agent", "prompt": "pipeline", "then": [{"agent": "qa_engineer", "prompt": "qa", "expect_verdict": "PASS"}]},
        {"agent": "dev_agent", "prompt": "live sibling"},
    ]}
    monkeypatch.setattr(orch, "_run_agent", lambda *a, **kw: (_make_result(), _make_report(output_summary=json.dumps(decision))))
    orch.run_step("T-CARRIER-ROOT")
    carrier = next(db.get_task(cid) for cid in db.get_children("T-CARRIER-ROOT") if db.get_task(cid).active_chain)
    leaf = db.get_children(carrier.id)[0]
    db.update_task(leaf, status=TaskStatus.FAILED, note="causal leaf diagnostic XYZ")
    db.insert_task_result(task_id=leaf, agent="dev_agent", session_id="leaf-session", status="completed", confidence_score=80, output_summary="causal leaf diagnostic XYZ", verdict="FAIL")
    _enqueue_parent_if_waiting(orch, leaf)
    sibling = next(cid for cid in db.get_children("T-CARRIER-ROOT") if cid != carrier.id)
    db.update_task(sibling, status=TaskStatus.COMPLETED, note="unrelated successful sibling")
    _enqueue_parent_if_waiting(orch, sibling)
    prompts: list[str] = []
    monkeypatch.setattr(orch, "_run_agent", lambda task_id, agent, prompt, **kw: (prompts.append(prompt), _make_result(), _make_report(output_summary=json.dumps({"action": "done", "summary": "handled"})))[1:])
    orch.run_step("T-CARRIER-ROOT")
    assert len(prompts) == 1
    assert f"carrier chain leg {leaf} failed" in prompts[0]
    assert "causal_leaf_id=" + leaf in prompts[0]
    assert "causal leaf diagnostic XYZ" in prompts[0]
    assert "causal_verdict=FAIL" in prompts[0]


def test_thr183_stale_lineage_does_not_escalate_a_fresh_failure(
    runtime, db,
):
    """A fresh failure of a recovered/replaced slice must be evaluated on its
    own merits; stale FAILED ancestors retired by a COMPLETED descendant must
    not force escalation and must not leak into the reason."""
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    db.insert_task(TaskRecord(
        id="T-FRESH", brief="parent", assigned_agent="engineering_head",
        task_type="task",
    ))
    db.update_task(
        "T-FRESH", status=TaskStatus.IN_PROGRESS,
        block_kind=BlockKind.DELEGATED, note="waiting",
    )

    # Old exhausted lineage retired by a completed descendant.
    db.insert_task(TaskRecord(
        id="T-FRESH-A", brief="slice A", assigned_agent="dev_agent",
        parent_task_id="T-FRESH", task_type="subtask",
    ))
    db.update_task("T-FRESH-A", status=TaskStatus.FAILED, note="quota exceeded")

    db.insert_task(TaskRecord(
        id="T-FRESH-A-R", brief="retry slice A", assigned_agent="dev_agent",
        parent_task_id="T-FRESH", revisit_of_task_id="T-FRESH-A",
        task_type="subtask",
    ))
    db.update_task(
        "T-FRESH-A-R", status=TaskStatus.FAILED,
        note="second failure — review rejected",
    )

    db.insert_task(TaskRecord(
        id="T-FRESH-A-R2", brief="final retry slice A", assigned_agent="dev_agent",
        parent_task_id="T-FRESH", revisit_of_task_id="T-FRESH-A-R",
        task_type="subtask",
    ))
    db.update_task("T-FRESH-A-R2", status=TaskStatus.COMPLETED, note="done")

    # A fresh retry of the now-completed slice fails for the first time.
    db.insert_task(TaskRecord(
        id="T-FRESH-B", brief="retry after completion", assigned_agent="dev_agent",
        parent_task_id="T-FRESH", revisit_of_task_id="T-FRESH-A-R2",
        task_type="subtask",
    ))
    db.update_task(
        "T-FRESH-B", status=TaskStatus.FAILED,
        note="review rejected on fresh retry",
    )

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    parent_before = db.get_task("T-FRESH")
    _enqueue_parent_if_waiting(orch, "T-FRESH-B")

    parent = db.get_task("T-FRESH")
    assert parent.status == TaskStatus.IN_PROGRESS, (
        f"fresh first failure after completed predecessor must wake, not escalate; got {parent.status}"
    )
    assert parent.block_kind == BlockKind.DELEGATED
    assert parent.note == parent_before.note
    assert _escalation_audit_rows(db, "T-FRESH") == []
    assert orch._queue.qsize() == 1
    assert orch._queue.get_nowait() == ("test", "T-FRESH")


# ── workspace-cleanup reclamation hook: scheduler → CAS → consumer ──────

_SCRATCH_OLD_NS = 1_700_000_000_000_000_000


def _make_fake_proc_root(tmp_path: Path) -> Path:
    proc = tmp_path / "proc"
    (proc / "sys/kernel/random").mkdir(parents=True)
    (proc / "sys/kernel/random/boot_id").write_text(
        "123e4567-e89b-42d3-a456-426614174000\n"
    )
    process = proc / "42"
    (process / "fd").mkdir(parents=True)
    (process / "stat").write_text(
        "42 (agent) S 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 9 0"
    )
    (process / "root").symlink_to("/")
    (process / "cwd").symlink_to("/")
    return proc


def _prepare_manifested_scratch(workspace, task_id, session_id, *, old_ns, links=200):
    from runtime.orchestrator.task_scratch import prepare_task_scratch

    contract = prepare_task_scratch(
        workspace=workspace, task_id=task_id, producer_kind="agent",
        producer_id=session_id,
    )
    payload = contract.root / "nested" / "file"
    payload.parent.mkdir(parents=True, exist_ok=True)
    payload.write_bytes(b"x" * 8192)
    for index in range(links):
        os.link(payload, contract.root / "nested" / f"payload-link-{index}")
    for path in (payload, contract.root / "nested", contract.root):
        os.utime(path, ns=(old_ns, old_ns), follow_symlinks=False)
    return contract


def _insert_terminal_task_with_result(db, *, task_id, agent, session_id, created_at,
                                      completed_at, brief="prior"):
    db.insert_task(TaskRecord(
        id=task_id, brief=brief, assigned_agent=agent, status=TaskStatus.COMPLETED,
        current_session_id=session_id, created_at=created_at, completed_at=completed_at,
    ))
    db.insert_task_result(task_id, agent, session_id, "done", 1, status="completed")
    db.insert_job(JobRecord(
        id=f"JOB-{task_id}", task_id=task_id, agent_name=agent, title="terminal",
        rationale="test", script_text="true", interpreter=JobInterpreter.BASH,
        status=JobStatus.COMPLETED, created_at=completed_at.isoformat(),
    ))


def _enable_reclamation_actions(runtime: OrgPaths) -> None:
    runtime.org_config_path.write_text(
        "workspace_cleanup:\n  reclamation_actions_enabled: true\n"
    )


def _build_cleanup_org(runtime: OrgPaths, db: Database, settings: Settings):
    from runtime.daemon.org_state import OrgState

    return OrgState(
        slug="test", root=runtime.root, db=db,
        teams=TeamsRegistry.load(runtime.root), settings=settings,
        orchestrator=None, sessions=SessionTracker(),
    )


def _seed_cleanup_target_and_history(db, *, agent, old_ns, now):
    """One removable third-run target plus older marker-bearing cleanup rows."""
    completed_target = datetime.fromtimestamp(
        (old_ns + 120_000_000_000) / 1_000_000_000, tz=timezone.utc,
    )
    _insert_terminal_task_with_result(
        db, task_id="TASK-100", agent=agent, session_id="session-100",
        created_at=now - timedelta(days=40), completed_at=completed_target,
        brief=wcs._CLEANUP_BRIEF_MARKER + "\nprior cleanup run",
    )
    _insert_terminal_task_with_result(
        db, task_id="TASK-101", agent=agent, session_id="session-101",
        created_at=now - timedelta(days=30), completed_at=now - timedelta(days=29),
        brief=wcs._CLEANUP_BRIEF_MARKER + "\nprior cleanup run",
    )


def _install_real_consumer_wrapper(monkeypatch, *, proc_root, now_ns, calls):
    import runtime.daemon.task_scratch_reclamation as reclamation

    real = reclamation.collect_revalidate_seal_consume_disposable

    def wrapper(*, db, sessions, workspace, task_id, agent_name,
                monotonic_now, daemon_started_monotonic):
        calls.append(task_id)
        return real(
            db=db, sessions=sessions, workspace=workspace, task_id=task_id,
            agent_name=agent_name, proc_root=proc_root, now_ns=now_ns,
            daemon_started_monotonic=0, monotonic_now=31,
        )

    monkeypatch.setattr(
        reclamation, "collect_revalidate_seal_consume_disposable", wrapper,
    )
    return real


def _protected_cleanup_snapshot(workspace: Path, contract, sibling) -> dict:
    return {
        "manifest": contract.manifest_path.read_bytes(),
        "lock": contract.manifest_path.with_suffix(".lock").read_bytes(),
        "sibling_root": sibling.root.is_dir(),
        "sibling_file": (sibling.root / "sibling.txt").read_bytes(),
        "workspace": workspace.is_dir(),
    }


@pytest.mark.asyncio
async def test_workspace_cleanup_hook_real_scheduler_cas_consumer_removes_target(
    runtime, db, test_settings, monkeypatch, tmp_path,
):
    """Real scheduler third owner → real CAS → unchanged consumer removal.

    The owner is allocated/enqueued by the shipping scheduler trigger (run #3,
    marker audit), claimed by the shipping ``run_step`` CAS (stale0/durable1),
    and the shipping hook then invokes the unchanged consumer on the older
    manifested cleanup target. Only the consumer's proc/clock inputs are
    controlled; db/sessions/workspace/task/agent are the real shipping values.
    """
    from runtime.orchestrator.orchestrator import Orchestrator

    _enable_reclamation_actions(runtime)
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    workspace = runtime.workspaces_dir / "dev_agent"
    workspace.mkdir(parents=True, exist_ok=True)
    proc = _make_fake_proc_root(tmp_path)
    now = datetime.now(timezone.utc)
    _seed_cleanup_target_and_history(db, agent="dev_agent", old_ns=_SCRATCH_OLD_NS, now=now)
    contract = _prepare_manifested_scratch(
        workspace, "TASK-100", "session-100", old_ns=_SCRATCH_OLD_NS,
    )
    sibling = _prepare_manifested_scratch(
        workspace, "TASK-2", "session-2", old_ns=_SCRATCH_OLD_NS,
    )
    (sibling.root / "sibling.txt").write_text("keep sibling")

    expected_bytes = sum(
        os.lstat(path).st_blocks * 512
        for path in (contract.root, *contract.root.rglob("*"))
    )
    expected_inodes = len([contract.root, *contract.root.rglob("*")])

    org = _build_cleanup_org(runtime, db, test_settings)
    queue = _FakeQueue()
    owner_id = await wcs.trigger_cleanup(org, agent="dev_agent", enqueue=queue.enqueue)
    assert owner_id is not None
    assert queue.items == [("test", owner_id)]
    assert db.get_task(owner_id).brief.startswith(wcs._CLEANUP_BRIEF_MARKER)

    calls: list[str] = []
    _install_real_consumer_wrapper(
        monkeypatch, proc_root=proc, now_ns=_SCRATCH_OLD_NS + 121_000_000_000, calls=calls,
    )
    protected_before = _protected_cleanup_snapshot(workspace, contract, sibling)

    orch = Orchestrator(db=db, settings=Settings(max_orchestration_steps=5),
                        paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._sessions = SessionTracker()
    orch._queue = _SlugQueue()
    captured: dict = {}

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        captured["prompt"] = prompt
        owner = db.get_task(task_id)
        captured["owner_status"] = owner.status
        captured["owner_block"] = owner.block_kind
        captured["owner_count"] = owner.orchestration_step_count
        return _make_result(), _make_report(
            output_summary=json.dumps({"action": "done", "summary": "cleanup done"}),
        )

    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)
    orch.run_step(owner_id)

    # Durable CAS state observed immediately before agent launch.
    assert captured["owner_status"] == TaskStatus.IN_PROGRESS
    assert captured["owner_block"] is None
    assert captured["owner_count"] == 1

    # The real consumer actually removed the manifested target root.
    assert calls[0] == "TASK-100"
    assert not contract.root.exists()
    assert _protected_cleanup_snapshot(workspace, contract, sibling) == protected_before

    assert "outcome=completed" in captured["prompt"]
    assert f"claimed_bytes={expected_bytes}" in captured["prompt"]
    assert f"claimed_inodes={expected_inodes}" in captured["prompt"]

    audits = [row for row in db.get_audit_logs(owner_id)
              if row["action"] == "workspace_cleanup_reclamation_attempt"]
    completed = [row for row in audits
                 if row["payload"]["target_task_id"] == "TASK-100"]
    assert len(completed) == 1
    payload = completed[0]["payload"]
    assert payload["outcome"] == "completed"
    assert payload["claimed_bytes"] == expected_bytes
    assert payload["claimed_inodes"] == expected_inodes
    assert payload["publication"] == "attempted"


async def _make_third_run_owner(runtime, db, test_settings, monkeypatch):
    """Real scheduler allocation of a run #3 cleanup owner for dev_agent."""
    _enable_reclamation_actions(runtime)
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    workspace = runtime.workspaces_dir / "dev_agent"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "occupancy.bin").write_bytes(b"x")
    now = datetime.now(timezone.utc)
    for index in range(2):
        _insert_terminal_task_with_result(
            db, task_id=f"TASK-{110 + index}", agent="dev_agent",
            session_id=f"session-{110 + index}",
            created_at=now - timedelta(days=40 - index),
            completed_at=now - timedelta(days=39 - index),
            brief=wcs._CLEANUP_BRIEF_MARKER + "\nprior cleanup run",
        )
    org = _build_cleanup_org(runtime, db, test_settings)
    queue = _FakeQueue()
    owner_id = await wcs.trigger_cleanup(org, agent="dev_agent", enqueue=queue.enqueue)
    assert owner_id is not None
    assert queue.items == [("test", owner_id)]
    marker = [row for row in db.get_audit_logs(owner_id)
              if row["action"] == "workspace_cleanup_triggered"]
    assert marker and marker[0]["payload"]["run_number"] == 3
    return owner_id, workspace


def _claim_owner_and_orchestrator(runtime, db, owner_id):
    from runtime.orchestrator.orchestrator import Orchestrator

    assert db.try_claim_for_step(
        owner_id, expected_status=TaskStatus.PENDING,
        expected_block_kind=None, new_count=1,
    )
    orch = Orchestrator(db=db, settings=Settings(max_orchestration_steps=5),
                        paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._sessions = SessionTracker()
    orch._queue = _SlugQueue()
    return orch, db.get_task(owner_id)


def _add_disposable_target(db, workspace):
    contract = _prepare_manifested_scratch(
        workspace, "TASK-100", "session-100", old_ns=_SCRATCH_OLD_NS,
    )
    completed = datetime.fromtimestamp(
        (_SCRATCH_OLD_NS + 120_000_000_000) / 1_000_000_000, tz=timezone.utc,
    )
    db.insert_task(TaskRecord(
        id="TASK-100", brief="prior cleanup target", assigned_agent="dev_agent",
        status=TaskStatus.COMPLETED, current_session_id="session-100",
        created_at=completed, completed_at=completed,
    ))
    db.insert_task_result("TASK-100", "dev_agent", "session-100", "done", 1, status="completed")
    db.insert_job(JobRecord(
        id="JOB-TASK-100", task_id="TASK-100", agent_name="dev_agent", title="terminal",
        rationale="test", script_text="true", interpreter=JobInterpreter.BASH,
        status=JobStatus.COMPLETED, created_at=completed.isoformat(),
    ))
    return contract


def _reclamation_audits(db, owner_id):
    return [row for row in db.get_audit_logs(owner_id)
            if row["action"] == "workspace_cleanup_reclamation_attempt"]


def _config_wrapper(monkeypatch, *, when, before=None, after=None, raises=False):
    import runtime.orchestrator.run_step as run_step_module

    real = run_step_module.load_org_config
    state = {"n": 0}

    def wrapper(paths):
        state["n"] += 1
        at = state["n"] == when
        if at and raises:
            from runtime.orchestrator.org_config import OrgConfigError
            raise OrgConfigError("per-target load failure")
        if at and before is not None:
            before()
        config = real(paths)
        if at and after is not None:
            after()
        return config

    monkeypatch.setattr(run_step_module, "load_org_config", wrapper)
    return state


@pytest.mark.asyncio
@pytest.mark.parametrize("seeded", [0, 1])
async def test_workspace_cleanup_hook_first_and_second_runs_are_ordinary_only(
    runtime, db, test_settings, monkeypatch, tmp_path, seeded,
):
    """Run #1/#2 owners claim normally but never reach selector/consumer/audit."""
    from runtime.orchestrator.run_step import _prepare_workspace_cleanup_reclamation_context

    _enable_reclamation_actions(runtime)
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    workspace = runtime.workspaces_dir / "dev_agent"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "occupancy.bin").write_bytes(b"x")
    now = datetime.now(timezone.utc)
    for index in range(seeded):
        _insert_terminal_task_with_result(
            db, task_id=f"TASK-{120 + index}", agent="dev_agent",
            session_id=f"session-{120 + index}",
            created_at=now - timedelta(days=40 - index),
            completed_at=now - timedelta(days=39 - index),
            brief=wcs._CLEANUP_BRIEF_MARKER + "\nprior cleanup run",
        )
    org = _build_cleanup_org(runtime, db, test_settings)
    queue = _FakeQueue()
    owner_id = await wcs.trigger_cleanup(org, agent="dev_agent", enqueue=queue.enqueue)
    assert owner_id is not None

    calls: list[str] = []
    _install_real_consumer_wrapper(
        monkeypatch, proc_root=_make_fake_proc_root(tmp_path),
        now_ns=_SCRATCH_OLD_NS + 121_000_000_000, calls=calls,
    )
    orch, owner = _claim_owner_and_orchestrator(runtime, db, owner_id)
    prompt = _prepare_workspace_cleanup_reclamation_context(
        orch, owner, "dev_agent", stale_orchestration_step_count=0,
        claimed_next_step_count=1,
    )
    assert prompt == ""
    assert calls == []
    assert _reclamation_audits(db, owner_id) == []
    assert db.get_task(owner_id).orchestration_step_count == 1


@pytest.mark.asyncio
async def test_workspace_cleanup_hook_later_count2_claim_is_ordinary_only(
    runtime, db, test_settings, monkeypatch, tmp_path,
):
    _enable_reclamation_actions(runtime)
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    workspace = runtime.workspaces_dir / "dev_agent"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "occupancy.bin").write_bytes(b"x")
    now = datetime.now(timezone.utc)
    for index in range(2):
        _insert_terminal_task_with_result(
            db, task_id=f"TASK-{110 + index}", agent="dev_agent",
            session_id=f"session-{110 + index}",
            created_at=now - timedelta(days=40 - index),
            completed_at=now - timedelta(days=39 - index),
            brief=wcs._CLEANUP_BRIEF_MARKER + "\nprior cleanup run",
        )
    owner_id = await wcs.trigger_cleanup(
        _build_cleanup_org(runtime, db, test_settings),
        agent="dev_agent", enqueue=lambda *_: None,
    )
    db.update_task(owner_id, status=TaskStatus.IN_PROGRESS,
                   block_kind=BlockKind.DELEGATED, orchestration_step_count=1)

    calls: list[str] = []
    _install_real_consumer_wrapper(
        monkeypatch, proc_root=_make_fake_proc_root(tmp_path),
        now_ns=_SCRATCH_OLD_NS + 121_000_000_000, calls=calls,
    )
    from runtime.orchestrator.orchestrator import Orchestrator
    orch = Orchestrator(db=db, settings=Settings(max_orchestration_steps=5),
                        paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._sessions = SessionTracker()
    orch._queue = _SlugQueue()
    captured: dict = {}

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        captured["prompt"] = prompt
        captured["count"] = db.get_task(task_id).orchestration_step_count
        return _make_result(), _make_report(
            output_summary=json.dumps({"action": "done", "summary": "cleanup done"}),
        )

    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)
    orch.run_step(owner_id)
    assert captured["count"] == 2
    assert "Workspace cleanup reclamation" not in captured["prompt"]
    assert calls == []
    assert _reclamation_audits(db, owner_id) == []


@pytest.mark.asyncio
async def test_workspace_cleanup_hook_duplicate_live_claim_is_refused_at_existing_entry(
    runtime, db, test_settings, monkeypatch, tmp_path,
):
    """A live in_progress/NULL owner is never re-admitted by the existing CAS entry."""
    _enable_reclamation_actions(runtime)
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    workspace = runtime.workspaces_dir / "dev_agent"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "occupancy.bin").write_bytes(b"x")
    now = datetime.now(timezone.utc)
    for index in range(2):
        _insert_terminal_task_with_result(
            db, task_id=f"TASK-{110 + index}", agent="dev_agent",
            session_id=f"session-{110 + index}",
            created_at=now - timedelta(days=40 - index),
            completed_at=now - timedelta(days=39 - index),
            brief=wcs._CLEANUP_BRIEF_MARKER + "\nprior cleanup run",
        )
    owner_id = await wcs.trigger_cleanup(
        _build_cleanup_org(runtime, db, test_settings),
        agent="dev_agent", enqueue=lambda *_: None,
    )
    assert db.try_claim_for_step(owner_id, expected_status=TaskStatus.PENDING,
                                 expected_block_kind=None, new_count=1)

    calls: list[str] = []
    _install_real_consumer_wrapper(
        monkeypatch, proc_root=_make_fake_proc_root(tmp_path),
        now_ns=_SCRATCH_OLD_NS + 121_000_000_000, calls=calls,
    )
    from runtime.orchestrator.orchestrator import Orchestrator
    orch = Orchestrator(db=db, settings=Settings(max_orchestration_steps=5),
                        paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._sessions = SessionTracker()
    orch.run_step(owner_id)
    assert calls == []
    assert db.get_task(owner_id).orchestration_step_count == 1
    assert _reclamation_audits(db, owner_id) == []


def _set_actions_enabled(runtime, enabled: bool) -> None:
    runtime.org_config_path.write_text(
        "workspace_cleanup:\n"
        f"  reclamation_actions_enabled: {str(enabled).lower()}\n"
    )


def _install_never_consumer(monkeypatch, calls):
    """A consumer wrapper that is only observable if it is wrongly reached."""
    import runtime.daemon.task_scratch_reclamation as reclamation

    def wrapper(**kwargs):
        calls.append(kwargs.get("task_id"))
        raise AssertionError("consumer must not run")

    monkeypatch.setattr(
        reclamation, "collect_revalidate_seal_consume_disposable", wrapper,
    )


def _owner_read_fault(monkeypatch, db, owner_id, *, on_call, raises=False):
    real = db.get_task
    state = {"n": 0}

    def wrapper(task_id, *args, **kwargs):
        if task_id == owner_id:
            state["n"] += 1
            if state["n"] == on_call:
                if raises:
                    raise ValueError("owner read failed")
                return None
        return real(task_id, *args, **kwargs)

    monkeypatch.setattr(db, "get_task", wrapper)
    return state


async def _third_run_owner_and_newer_owner(runtime, db, test_settings, monkeypatch):
    owner_id, workspace = await _make_third_run_owner(
        runtime, db, test_settings, monkeypatch,
    )
    newer_id = await wcs.trigger_cleanup(
        _build_cleanup_org(runtime, db, test_settings),
        agent="dev_agent", enqueue=lambda *_: None,
    )
    assert newer_id is not None and newer_id != owner_id
    return owner_id, newer_id, workspace


@pytest.mark.asyncio
@pytest.mark.parametrize("newer_status", [TaskStatus.PENDING, TaskStatus.IN_PROGRESS])
async def test_workspace_cleanup_hook_refuses_newer_live_owner(
    runtime, db, test_settings, monkeypatch, newer_status,
):
    """A later live marker-bearing cleanup owner is a whole selection refusal."""
    from runtime.orchestrator.run_step import _prepare_workspace_cleanup_reclamation_context

    owner_id, newer_id, _ws = await _third_run_owner_and_newer_owner(
        runtime, db, test_settings, monkeypatch,
    )
    db.update_task(newer_id, status=newer_status)
    calls: list[str] = []
    _install_never_consumer(monkeypatch, calls)
    orch, owner = _claim_owner_and_orchestrator(runtime, db, owner_id)
    prompt = _prepare_workspace_cleanup_reclamation_context(
        orch, owner, "dev_agent", stale_orchestration_step_count=0,
        claimed_next_step_count=1,
    )
    assert prompt == ""
    assert calls == []
    assert _reclamation_audits(db, owner_id) == []


@pytest.mark.asyncio
async def test_workspace_cleanup_hook_refuses_newer_terminal_owner(
    runtime, db, test_settings, monkeypatch,
):
    from runtime.orchestrator.run_step import _prepare_workspace_cleanup_reclamation_context

    owner_id, newer_id, _ws = await _third_run_owner_and_newer_owner(
        runtime, db, test_settings, monkeypatch,
    )
    db.update_task(newer_id, status=TaskStatus.COMPLETED)
    calls: list[str] = []
    _install_never_consumer(monkeypatch, calls)
    orch, owner = _claim_owner_and_orchestrator(runtime, db, owner_id)
    assert _prepare_workspace_cleanup_reclamation_context(
        orch, owner, "dev_agent", stale_orchestration_step_count=0,
        claimed_next_step_count=1,
    ) == ""
    assert calls == []
    assert _reclamation_audits(db, owner_id) == []


@pytest.mark.asyncio
async def test_workspace_cleanup_hook_refuses_unreadable_newer_owner(
    runtime, db, test_settings, monkeypatch,
):
    """An orphan marker audit (no task row) is an unreadable newer owner."""
    from runtime.orchestrator.run_step import _prepare_workspace_cleanup_reclamation_context

    owner_id, _ws = await _make_third_run_owner(runtime, db, test_settings, monkeypatch)
    db.insert_audit_log(
        "TASK-999", "dev_agent", "workspace_cleanup_triggered",
        {"run_number": 99, "brief_kind": "cleanup"},
    )
    calls: list[str] = []
    _install_never_consumer(monkeypatch, calls)
    orch, owner = _claim_owner_and_orchestrator(runtime, db, owner_id)
    assert _prepare_workspace_cleanup_reclamation_context(
        orch, owner, "dev_agent", stale_orchestration_step_count=0,
        claimed_next_step_count=1,
    ) == ""
    assert calls == []
    assert _reclamation_audits(db, owner_id) == []


@pytest.mark.asyncio
async def test_workspace_cleanup_hook_refuses_duplicate_owner_marker(
    runtime, db, test_settings, monkeypatch,
):
    from runtime.orchestrator.run_step import _prepare_workspace_cleanup_reclamation_context

    owner_id, _ws = await _make_third_run_owner(runtime, db, test_settings, monkeypatch)
    db.insert_audit_log(
        owner_id, "dev_agent", "workspace_cleanup_triggered",
        {"run_number": 3, "brief_kind": "cleanup"},
    )
    calls: list[str] = []
    _install_never_consumer(monkeypatch, calls)
    orch, owner = _claim_owner_and_orchestrator(runtime, db, owner_id)
    assert _prepare_workspace_cleanup_reclamation_context(
        orch, owner, "dev_agent", stale_orchestration_step_count=0,
        claimed_next_step_count=1,
    ) == ""
    assert calls == []
    assert _reclamation_audits(db, owner_id) == []


@pytest.mark.asyncio
async def test_workspace_cleanup_hook_delayed_original_with_newer_ordinary_task_succeeds(
    runtime, db, test_settings, monkeypatch, tmp_path,
):
    """Only marker-bearing newer owners refuse; a newer ordinary task does not."""
    from runtime.orchestrator.run_step import _prepare_workspace_cleanup_reclamation_context

    owner_id, workspace = await _make_third_run_owner(
        runtime, db, test_settings, monkeypatch,
    )
    contract = _add_disposable_target(db, workspace)
    later = datetime.now(timezone.utc) + timedelta(minutes=5)
    _insert_terminal_task_with_result(
        db, task_id="TASK-130", agent="dev_agent", session_id="session-130",
        created_at=later, completed_at=later + timedelta(minutes=1),
        brief="ordinary newer task",
    )
    calls: list[str] = []
    _install_real_consumer_wrapper(
        monkeypatch, proc_root=_make_fake_proc_root(tmp_path),
        now_ns=_SCRATCH_OLD_NS + 121_000_000_000, calls=calls,
    )
    orch, owner = _claim_owner_and_orchestrator(runtime, db, owner_id)
    prompt = _prepare_workspace_cleanup_reclamation_context(
        orch, owner, "dev_agent", stale_orchestration_step_count=0,
        claimed_next_step_count=1,
    )
    assert calls and calls[0] == "TASK-100"
    assert not contract.root.exists()
    assert "outcome=completed" in prompt
    assert len(_reclamation_audits(db, owner_id)) >= 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["cancelled", "agent", "count", "state", "block"])
async def test_workspace_cleanup_hook_fresh_owner_invalidation_stops_next_admission(
    runtime, db, test_settings, monkeypatch, mutation,
):
    """A fresh owner read that no longer matches refuses with zero consumer/audit."""
    from runtime.orchestrator.run_step import _prepare_workspace_cleanup_reclamation_context

    owner_id, _ws = await _make_third_run_owner(runtime, db, test_settings, monkeypatch)

    def mutate():
        if mutation == "cancelled":
            db.update_task(owner_id, cancelled_at=datetime.now(timezone.utc))
        elif mutation == "agent":
            db.update_task(owner_id, assigned_agent="content_agent")
        elif mutation == "count":
            db.update_task(owner_id, orchestration_step_count=2)
        elif mutation == "state":
            db.update_task(owner_id, status=TaskStatus.COMPLETED)
        elif mutation == "block":
            db.update_task(owner_id, block_kind=BlockKind.BLOCKED_ON_JOB)

    calls: list[str] = []
    _install_never_consumer(monkeypatch, calls)
    _config_wrapper(monkeypatch, when=2, after=mutate)
    orch, owner = _claim_owner_and_orchestrator(runtime, db, owner_id)
    assert _prepare_workspace_cleanup_reclamation_context(
        orch, owner, "dev_agent", stale_orchestration_step_count=0,
        claimed_next_step_count=1,
    ) == ""
    assert calls == []
    assert _reclamation_audits(db, owner_id) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("raises", [False, True])
async def test_workspace_cleanup_hook_unreadable_owner_read_is_a_refusal(
    runtime, db, test_settings, monkeypatch, raises,
):
    from runtime.orchestrator.run_step import _prepare_workspace_cleanup_reclamation_context

    owner_id, _ws = await _make_third_run_owner(runtime, db, test_settings, monkeypatch)
    calls: list[str] = []
    _install_never_consumer(monkeypatch, calls)
    orch, owner = _claim_owner_and_orchestrator(runtime, db, owner_id)
    _owner_read_fault(monkeypatch, db, owner_id, on_call=2, raises=raises)
    assert _prepare_workspace_cleanup_reclamation_context(
        orch, owner, "dev_agent", stale_orchestration_step_count=0,
        claimed_next_step_count=1,
    ) == ""
    assert calls == []
    assert _reclamation_audits(db, owner_id) == []


@pytest.mark.asyncio
async def test_workspace_cleanup_hook_held_latch_disable_stops_next_admission(
    runtime, db, test_settings, monkeypatch,
):
    """A fresh config admission that now reads disabled performs zero next action."""
    from runtime.orchestrator.run_step import _prepare_workspace_cleanup_reclamation_context

    owner_id, _ws = await _make_third_run_owner(runtime, db, test_settings, monkeypatch)
    calls: list[str] = []
    _install_never_consumer(monkeypatch, calls)
    _config_wrapper(monkeypatch, when=2, before=lambda: _set_actions_enabled(runtime, False))
    orch, owner = _claim_owner_and_orchestrator(runtime, db, owner_id)
    assert _prepare_workspace_cleanup_reclamation_context(
        orch, owner, "dev_agent", stale_orchestration_step_count=0,
        claimed_next_step_count=1,
    ) == ""
    assert calls == []
    assert _reclamation_audits(db, owner_id) == []


@pytest.mark.asyncio
async def test_workspace_cleanup_hook_failed_per_target_config_does_not_escape(
    runtime, db, test_settings, monkeypatch,
):
    """A per-target load failure is one bounded refusal, not an escape."""
    from runtime.orchestrator.run_step import _prepare_workspace_cleanup_reclamation_context

    owner_id, _ws = await _make_third_run_owner(runtime, db, test_settings, monkeypatch)
    calls: list[str] = []
    _install_never_consumer(monkeypatch, calls)
    _config_wrapper(monkeypatch, when=2, raises=True)
    orch, owner = _claim_owner_and_orchestrator(runtime, db, owner_id)
    assert _prepare_workspace_cleanup_reclamation_context(
        orch, owner, "dev_agent", stale_orchestration_step_count=0,
        claimed_next_step_count=1,
    ) == ""
    assert calls == []
    assert _reclamation_audits(db, owner_id) == []


@pytest.mark.asyncio
async def test_workspace_cleanup_hook_post_admission_disable_keeps_admitted_consumer(
    runtime, db, test_settings, monkeypatch, tmp_path,
):
    """Disabling after the fresh admission does not cancel the admitted call."""
    from runtime.orchestrator.run_step import _prepare_workspace_cleanup_reclamation_context

    owner_id, workspace = await _make_third_run_owner(
        runtime, db, test_settings, monkeypatch,
    )
    contract = _add_disposable_target(db, workspace)
    calls: list[str] = []
    _install_real_consumer_wrapper(
        monkeypatch, proc_root=_make_fake_proc_root(tmp_path),
        now_ns=_SCRATCH_OLD_NS + 121_000_000_000, calls=calls,
    )
    _config_wrapper(monkeypatch, when=2, after=lambda: _set_actions_enabled(runtime, False))
    orch, owner = _claim_owner_and_orchestrator(runtime, db, owner_id)
    prompt = _prepare_workspace_cleanup_reclamation_context(
        orch, owner, "dev_agent", stale_orchestration_step_count=0,
        claimed_next_step_count=1,
    )
    assert calls and calls[0] == "TASK-100"
    assert not contract.root.exists()
    assert "outcome=completed" in prompt


@pytest.mark.asyncio
async def test_workspace_cleanup_hook_retains_earlier_fact_when_next_admission_disabled(
    runtime, db, test_settings, monkeypatch, tmp_path,
):
    """Earlier known facts survive a later fresh-config refusal."""
    from runtime.orchestrator.run_step import _prepare_workspace_cleanup_reclamation_context

    owner_id, workspace = await _make_third_run_owner(
        runtime, db, test_settings, monkeypatch,
    )
    contract = _add_disposable_target(db, workspace)
    calls: list[str] = []
    _install_real_consumer_wrapper(
        monkeypatch, proc_root=_make_fake_proc_root(tmp_path),
        now_ns=_SCRATCH_OLD_NS + 121_000_000_000, calls=calls,
    )
    # Candidate order is TASK-100 (removable) then TASK-110/TASK-111.  Disable
    # before the second candidate's fresh config admission.
    _config_wrapper(monkeypatch, when=3, before=lambda: _set_actions_enabled(runtime, False))
    orch, owner = _claim_owner_and_orchestrator(runtime, db, owner_id)
    prompt = _prepare_workspace_cleanup_reclamation_context(
        orch, owner, "dev_agent", stale_orchestration_step_count=0,
        claimed_next_step_count=1,
    )
    assert calls == ["TASK-100"]
    assert not contract.root.exists()
    assert "outcome=completed" in prompt
    audits = _reclamation_audits(db, owner_id)
    assert len(audits) == 1
    assert audits[0]["payload"]["target_task_id"] == "TASK-100"


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["maybe", 1])
async def test_workspace_cleanup_hook_invalid_initial_shared_config_still_escapes(
    runtime, db, test_settings, monkeypatch, invalid,
):
    """The initial shared-config load keeps its ordinary escaping error."""
    from runtime.orchestrator.org_config import OrgConfigError
    from runtime.orchestrator.run_step import _prepare_workspace_cleanup_reclamation_context

    owner_id, _ws = await _make_third_run_owner(runtime, db, test_settings, monkeypatch)
    runtime.org_config_path.write_text(
        "workspace_cleanup:\n"
        f"  reclamation_actions_enabled: {invalid}\n"
    )
    calls: list[str] = []
    _install_never_consumer(monkeypatch, calls)
    orch, owner = _claim_owner_and_orchestrator(runtime, db, owner_id)
    with pytest.raises(OrgConfigError):
        _prepare_workspace_cleanup_reclamation_context(
            orch, owner, "dev_agent", stale_orchestration_step_count=0,
            claimed_next_step_count=1,
        )
    assert calls == []


# ---------------------------------------------------------------------------
# Groups 4/5/6: action-phase read/load budget, controlled monotonic clock and
# real-selector refusal parity at the shipping hook (repair correction 4).
# Group 5/6 prior selector regression evidence is mapped in the handoff; the
# cases here exercise selection/graph boundaries through the real hook.
# ---------------------------------------------------------------------------


class _CleanupMonotonicClock:
    """Controlled ``time.monotonic`` as seen by ``runtime.orchestrator.run_step``.

    ``expire_at_call`` returns a value past the hook's one-second deadline for
    every 1-based call at/after that index; ``expire`` flips the same behaviour
    on demand (used to model an already-admitted consumer that overruns 1s).
    """

    def __init__(self, *, start: float = 1000.0, expire_at_call: int | None = None):
        self.start = start
        self.calls = 0
        self.expire = False
        self.expire_at_call = expire_at_call

    def __call__(self) -> float:
        self.calls += 1
        if self.expire or (
            self.expire_at_call is not None and self.calls >= self.expire_at_call
        ):
            return self.start + 10.0
        return self.start


class _RunStepTimeProxy:
    """Replaces only ``run_step``'s module-level ``time`` binding.

    The stdlib module is shared, so patching ``time.monotonic`` globally would
    also perturb the Database lock's own timing.  This proxy exposes the
    controlled monotonic to the hook while every other ``time`` attribute (and
    every other module's ``import time``) stays real.
    """

    def __init__(self, monotonic):
        self._monotonic = monotonic

    def monotonic(self) -> float:
        return self._monotonic()

    def __getattr__(self, name):
        import time as real_time

        return getattr(real_time, name)


def _install_run_step_clock(monkeypatch, clock):
    import runtime.orchestrator.run_step as run_step_module

    monkeypatch.setattr(run_step_module, "time", _RunStepTimeProxy(clock))
    return clock


def _install_counting_config_loads(monkeypatch):
    """Count the hook's actual ``load_org_config`` calls (1 initial + 1/slot)."""
    import runtime.orchestrator.run_step as run_step_module

    real = run_step_module.load_org_config
    loads: list[int] = []

    def wrapper(paths):
        loads.append(len(loads) + 1)
        return real(paths)

    monkeypatch.setattr(run_step_module, "load_org_config", wrapper)
    return loads


def _install_admission_counter(monkeypatch, db):
    """Wrap the real selector and count its ``admit_observation`` admissions.

    The names recorded are exactly the hook's own config/owner admissions and
    the real selector's owner/marker/history/newer_owner/candidates/graph_tasks/
    graph_edges/result reads.  Consumer-internal collector admissions and test
    verification reads are deliberately not routed through this callback.
    """
    real = db.select_workspace_cleanup_reclamation_candidates
    state: dict = {"names": [], "raw_admit": None}

    def wrapper(**kwargs):
        raw_admit = kwargs["admit_observation"]
        state["raw_admit"] = raw_admit

        def admit(name):
            ok = raw_admit(name)
            state["names"].append((name, ok))
            return ok

        kwargs["admit_observation"] = admit
        return real(**kwargs)

    monkeypatch.setattr(db, "select_workspace_cleanup_reclamation_candidates", wrapper)
    return state


def _install_owner_read_counter(monkeypatch, db, owner_id):
    """Count ``get_task(owner_id)`` reads (selector owner read + fresh hook reads)."""
    real = db.get_task
    state = {"count": 0}

    def wrapper(task_id, *args, **kwargs):
        if task_id == owner_id:
            state["count"] += 1
        return real(task_id, *args, **kwargs)

    monkeypatch.setattr(db, "get_task", wrapper)
    return state


async def _third_run_owner_with_five_slots(runtime, db, test_settings, monkeypatch):
    """Real run-#3 owner whose two marker history rows plus three ordinary
    terminal rows give the selector exactly five eligible slots."""
    owner_id, workspace = await _make_third_run_owner(
        runtime, db, test_settings, monkeypatch,
    )
    now = datetime.now(timezone.utc)
    for index in range(3):
        _insert_terminal_task_with_result(
            db, task_id=f"TASK-{120 + index}", agent="dev_agent",
            session_id=f"session-{120 + index}",
            created_at=now - timedelta(days=20 + index),
            completed_at=now - timedelta(days=19 + index),
            brief=f"ordinary older target {index}",
        )
    return owner_id, workspace


_FIVE_SLOT_ORDER = ["TASK-110", "TASK-111", "TASK-122", "TASK-121", "TASK-120"]


@pytest.mark.asyncio
async def test_workspace_cleanup_hook_exact_23_reads_admits_fifth_consumer(
    runtime, db, test_settings, monkeypatch, tmp_path,
):
    """Five eligible slots reach exactly 23 reads/loads; the fifth call is allowed.

    Read 1 is the initial enabled config load, taken outside the one-second
    clock.  The real selector then admits owner, marker, history, newer_owner,
    candidates, graph_tasks, graph_edges and five persisted-result reads (12).
    Each of the five candidates then admits one fresh config and one fresh owner
    (10).  ``admit_observation`` is invoked 22 times, so 1 + 22 = 23 actual
    reads/loads; a prospective 24th admission is refused by the same latch.
    """
    from runtime.orchestrator.run_step import _prepare_workspace_cleanup_reclamation_context

    owner_id, workspace = await _third_run_owner_with_five_slots(
        runtime, db, test_settings, monkeypatch,
    )
    admissions = _install_admission_counter(monkeypatch, db)
    loads = _install_counting_config_loads(monkeypatch)
    calls: list[str] = []
    _install_real_consumer_wrapper(
        monkeypatch, proc_root=_make_fake_proc_root(tmp_path),
        now_ns=_SCRATCH_OLD_NS + 121_000_000_000, calls=calls,
    )
    orch, owner = _claim_owner_and_orchestrator(runtime, db, owner_id)
    # Installed only for the action phase: the selector's one owner read plus
    # the hook's five fresh owner reads.
    owner_reads = _install_owner_read_counter(monkeypatch, db, owner_id)
    prompt = _prepare_workspace_cleanup_reclamation_context(
        orch, owner, "dev_agent", stale_orchestration_step_count=0,
        claimed_next_step_count=1,
    )

    selector_names = [name for name, _ok in admissions["names"]]
    assert [ok for _name, ok in admissions["names"]] == [True] * 12
    assert selector_names == [
        "owner", "marker", "history", "newer_owner", "candidates", "graph_tasks",
        "graph_edges", "result:TASK-110", "result:TASK-111", "result:TASK-122",
        "result:TASK-121", "result:TASK-120",
    ]
    assert len(loads) == 6  # 1 initial + 5 fresh per-candidate loads
    # 6 config loads + 12 real selector admissions + 5 fresh owner reads = 23.
    fresh_owner_reads = owner_reads["count"] - 1  # selector already read the owner
    assert fresh_owner_reads == 5
    assert len(loads) + len(admissions["names"]) + fresh_owner_reads == 23
    assert calls == _FIVE_SLOT_ORDER  # fifth consumer call admitted at read 23
    # 1 initial config + 22 admitted callback observations = exactly 23 reads.
    assert admissions["raw_admit"]("probe") is False  # no 24th observation
    assert len(_reclamation_audits(db, owner_id)) == 5
    assert prompt.count("refused_or_unavailable") == 5


@pytest.mark.asyncio
async def test_workspace_cleanup_hook_initial_config_load_is_outside_the_clock(
    runtime, db, test_settings, monkeypatch,
):
    """The initial enabled config load precedes the one-second deadline."""
    import runtime.orchestrator.run_step as run_step_module
    from runtime.orchestrator.run_step import _prepare_workspace_cleanup_reclamation_context

    owner_id, _ws = await _make_third_run_owner(runtime, db, test_settings, monkeypatch)
    orch, owner = _claim_owner_and_orchestrator(runtime, db, owner_id)
    clock = _CleanupMonotonicClock()
    _install_run_step_clock(monkeypatch, clock)
    real_load = run_step_module.load_org_config
    loads_at: list[int] = []

    def load(paths):
        loads_at.append(clock.calls)
        return real_load(paths)

    monkeypatch.setattr(run_step_module, "load_org_config", load)
    monkeypatch.setattr(db, "select_workspace_cleanup_reclamation_candidates", lambda **_: None)

    assert _prepare_workspace_cleanup_reclamation_context(
        orch, owner, "dev_agent", stale_orchestration_step_count=0,
        claimed_next_step_count=1,
    ) == ""
    assert loads_at == [0]   # enabled decision made before any clock read
    assert clock.calls == 1  # only the deadline start is taken


@pytest.mark.asyncio
@pytest.mark.parametrize("stop,expire_at_call,expect_selector,expect_loads", [
    ("selector_read", 4, 3, 1),
    ("fresh_config", 14, 12, 1),
    ("fresh_owner", 15, 12, 2),
    ("consumer_call", 16, 12, 2),
])
async def test_workspace_cleanup_hook_deadline_stops_next_read_or_call(
    runtime, db, test_settings, monkeypatch, stop, expire_at_call, expect_selector, expect_loads,
):
    """A clock past the deadline refuses the next observation/call and stops.

    Monotonic call order on the five-slot fixture: 1 deadline; 2..13 the twelve
    real selector admissions; then per candidate config(14/18/...),
    owner(15/19/...), the pre-consumer deadline check(16/20/...) and the
    consumer ``monotonic_now``(17/21/...).  The parametrised index expires
    exactly at the named boundary.
    """
    from runtime.orchestrator.run_step import _prepare_workspace_cleanup_reclamation_context

    owner_id, _ws = await _third_run_owner_with_five_slots(
        runtime, db, test_settings, monkeypatch,
    )
    orch, owner = _claim_owner_and_orchestrator(runtime, db, owner_id)
    clock = _CleanupMonotonicClock(expire_at_call=expire_at_call)
    _install_run_step_clock(monkeypatch, clock)
    admissions = _install_admission_counter(monkeypatch, db)
    loads = _install_counting_config_loads(monkeypatch)
    calls: list[str] = []
    _install_never_consumer(monkeypatch, calls)

    assert _prepare_workspace_cleanup_reclamation_context(
        orch, owner, "dev_agent", stale_orchestration_step_count=0,
        claimed_next_step_count=1,
    ) == ""
    assert len(admissions["names"]) == expect_selector
    if stop == "selector_read":
        # The boundary selector admission itself was refused by the deadline.
        assert admissions["names"][-1][1] is False
    else:
        assert all(ok for _name, ok in admissions["names"])
    assert len(loads) == expect_loads
    assert calls == []
    assert _reclamation_audits(db, owner_id) == []


@pytest.mark.asyncio
async def test_workspace_cleanup_hook_admitted_consumer_overrun_keeps_result_then_stops(
    runtime, db, test_settings, monkeypatch,
):
    """An already-admitted consumer that overruns 1s keeps its result/audit,
    then no later candidate observation or call starts."""
    import runtime.daemon.task_scratch_reclamation as reclamation
    from runtime.daemon.task_scratch_reclamation import Accounting, ReclamationResult
    from runtime.orchestrator.run_step import _prepare_workspace_cleanup_reclamation_context

    owner_id, _ws = await _third_run_owner_with_five_slots(
        runtime, db, test_settings, monkeypatch,
    )
    orch, owner = _claim_owner_and_orchestrator(runtime, db, owner_id)
    clock = _CleanupMonotonicClock()
    _install_run_step_clock(monkeypatch, clock)
    admissions = _install_admission_counter(monkeypatch, db)
    loads = _install_counting_config_loads(monkeypatch)
    calls: list[str] = []

    def overrunning(**kwargs):
        calls.append(kwargs["task_id"])
        clock.expire = True  # crossed the deadline while the admitted call ran
        return ReclamationResult(
            kwargs["task_id"], "completed", None,
            Accounting(100, 100, 1), Accounting(0, 0, 0), 100, 1,
        )

    monkeypatch.setattr(
        reclamation, "collect_revalidate_seal_consume_disposable", overrunning,
    )

    prompt = _prepare_workspace_cleanup_reclamation_context(
        orch, owner, "dev_agent", stale_orchestration_step_count=0,
        claimed_next_step_count=1,
    )
    assert calls == ["TASK-110"]
    audits = _reclamation_audits(db, owner_id)
    assert len(audits) == 1
    assert audits[0]["payload"]["outcome"] == "completed"
    assert audits[0]["payload"]["claimed_bytes"] == 100
    assert "target=TASK-110" in prompt and "outcome=completed" in prompt
    # 12 real selector admissions, then only the first candidate's fresh config;
    # the next candidate's fresh-config admission is refused by the deadline.
    assert len(admissions["names"]) == 12
    assert len(loads) == 2  # 1 initial + the one admitted fresh config


# ---------------------------------------------------------------------------
# Group 5/6: selection and graph boundaries observed through the real selector
# at the shipping hook.  The deeper selector regressions (complete 1000/1001,
# raw sixth without refill, bytewise ties, graph row/edge sentinels, relevant
# foreign-live components) live in tests/test_database.py and are mapped in the
# handoff; these cases prove the hook turns the same refusal into zero consumer
# calls and zero action audits.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workspace_cleanup_hook_real_selector_sixth_raw_candidate_refuses(
    runtime, db, test_settings, monkeypatch,
):
    """Six raw canonical terminal rows refuse the whole phase (no refill)."""
    from runtime.orchestrator.run_step import _prepare_workspace_cleanup_reclamation_context

    owner_id, _ws = await _make_third_run_owner(runtime, db, test_settings, monkeypatch)
    now = datetime.now(timezone.utc)
    for index in range(4):
        _insert_terminal_task_with_result(
            db, task_id=f"TASK-{130 + index}", agent="dev_agent",
            session_id=f"session-{130 + index}",
            created_at=now - timedelta(days=20 + index),
            completed_at=now - timedelta(days=19 + index),
            brief=f"ordinary older target {index}",
        )
    calls: list[str] = []
    _install_never_consumer(monkeypatch, calls)
    orch, owner = _claim_owner_and_orchestrator(runtime, db, owner_id)
    assert _prepare_workspace_cleanup_reclamation_context(
        orch, owner, "dev_agent", stale_orchestration_step_count=0,
        claimed_next_step_count=1,
    ) == ""
    assert calls == []
    assert _reclamation_audits(db, owner_id) == []


@pytest.mark.asyncio
async def test_workspace_cleanup_hook_real_selector_relevant_foreign_live_relative_refuses(
    runtime, db, test_settings, monkeypatch,
):
    """A relevant foreign live relative refuses the owner/candidate component."""
    from runtime.orchestrator.run_step import _prepare_workspace_cleanup_reclamation_context

    owner_id, workspace = await _make_third_run_owner(runtime, db, test_settings, monkeypatch)
    _add_disposable_target(db, workspace)
    db.insert_task(TaskRecord(
        id="TASK-200", brief="foreign live relative", assigned_agent="content_agent",
        status=TaskStatus.PENDING, parent_task_id="TASK-100",
    ))
    calls: list[str] = []
    _install_never_consumer(monkeypatch, calls)
    orch, owner = _claim_owner_and_orchestrator(runtime, db, owner_id)
    assert _prepare_workspace_cleanup_reclamation_context(
        orch, owner, "dev_agent", stale_orchestration_step_count=0,
        claimed_next_step_count=1,
    ) == ""
    assert calls == []
    assert _reclamation_audits(db, owner_id) == []


# ---------------------------------------------------------------------------
# Group 7: literal returned-field transport, remainder from the real
# ``after`` accounting, preserved reason, publication_unavailable and the
# no-fabricated-None contract (repair correction 5).
# ---------------------------------------------------------------------------


def _hook_owner_with_mocked_selection(runtime, db, monkeypatch, *, candidates):
    """Owner in the exact claimed shape plus a canned selection."""
    from runtime.infrastructure.database import (
        WorkspaceCleanupReclamationCandidate, WorkspaceCleanupReclamationSelection,
    )
    from runtime.orchestrator.orchestrator import Orchestrator

    _enable_reclamation_actions(runtime)
    db.insert_task(TaskRecord(id="TASK-HOOK", brief="cleanup", assigned_agent="dev_agent"))
    db.update_task("TASK-HOOK", status=TaskStatus.IN_PROGRESS, block_kind=None,
                   orchestration_step_count=1)
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._sessions = object()
    selection = WorkspaceCleanupReclamationSelection(
        owner_task_id="TASK-HOOK",
        candidates=tuple(WorkspaceCleanupReclamationCandidate(
            task_id=task_id, session_id=f"session-{task_id}",
            scratch_path=runtime.workspaces_dir / "dev_agent" / ".happyranch"
            / "task-tmp" / task_id,
            result={"status": "completed"},
        ) for task_id in candidates),
        read_observations=(),
    )
    monkeypatch.setattr(
        db, "select_workspace_cleanup_reclamation_candidates", lambda **_: selection,
    )
    return orch, SimpleNamespace(id="TASK-HOOK")


def _install_result_consumer(monkeypatch, results):
    """Canned consumer sequence; returns the observed call list."""
    import runtime.daemon.task_scratch_reclamation as reclamation

    calls: list[str] = []

    def wrapper(**kwargs):
        calls.append(kwargs["task_id"])
        return results[len(calls) - 1]

    monkeypatch.setattr(
        reclamation, "collect_revalidate_seal_consume_disposable", wrapper,
    )
    return calls


def _manual_third_run_owner(runtime, db, *, owner_id="TASK-120", target_id="TASK-050"):
    """Run #3 owner with two older non-terminal marker rows and one target.

    Used where a case needs exactly one selected slot without the scheduler's
    terminal history rows becoming candidates themselves.
    """
    now = datetime.now(timezone.utc)
    for index in range(2):
        db.insert_task(TaskRecord(
            id=f"TASK-{110 + index}",
            brief=wcs._CLEANUP_BRIEF_MARKER + "\nprior cleanup run",
            assigned_agent="dev_agent", status=TaskStatus.PENDING,
            created_at=now - timedelta(days=40 - index),
        ))
    completed = datetime.fromtimestamp(
        (_SCRATCH_OLD_NS + 120_000_000_000) / 1_000_000_000, tz=timezone.utc,
    )
    db.insert_task(TaskRecord(
        id=target_id, brief="prior cleanup target", assigned_agent="dev_agent",
        status=TaskStatus.COMPLETED, current_session_id=f"session-{target_id}",
        created_at=completed, completed_at=completed,
    ))
    db.insert_task_result(target_id, "dev_agent", f"session-{target_id}", "done", 1,
                          status="completed")
    db.insert_job(JobRecord(
        id=f"JOB-{target_id}", task_id=target_id, agent_name="dev_agent",
        title="terminal", rationale="test", script_text="true",
        interpreter=JobInterpreter.BASH, status=JobStatus.COMPLETED,
        created_at=completed.isoformat(),
    ))
    db.insert_task(TaskRecord(
        id=owner_id, brief=wcs._CLEANUP_BRIEF_MARKER + "\ncleanup run",
        assigned_agent="dev_agent", created_at=now,
    ))
    db.insert_audit_log(owner_id, "dev_agent", "workspace_cleanup_triggered",
                        {"run_number": 3, "brief_kind": "cleanup"})
    return owner_id, target_id


@pytest.mark.parametrize("outcome,after_tuple,reason,claimed_bytes,claimed_inodes,expected_remainder", [
    ("completed", (0, 0, 0), None, 8192, 3,
     {"allocated_bytes": 0, "apparent_bytes": 0, "inodes": 0}),
    ("failed", (4096, 8192, 1), "fail-closed filesystem error", 0, 0,
     {"allocated_bytes": 4096, "apparent_bytes": 8192, "inodes": 1}),
    ("failed", None, "fail-closed filesystem error", 0, 0, None),
])
def test_workspace_cleanup_hook_transports_remainder_and_reason(
    runtime, db, monkeypatch, outcome, after_tuple, reason, claimed_bytes,
    claimed_inodes, expected_remainder,
):
    """Returned fields are literal; remainder is the serialized real ``after``
    accounting (``None`` when ``after`` is None) and the prompt keeps reason."""
    from runtime.daemon.task_scratch_reclamation import Accounting, ReclamationResult
    from runtime.orchestrator.run_step import _prepare_workspace_cleanup_reclamation_context

    orch, owner = _hook_owner_with_mocked_selection(
        runtime, db, monkeypatch, candidates=["TASK-OLD"],
    )
    after = Accounting(*after_tuple) if after_tuple is not None else None
    result = ReclamationResult(
        "TASK-OLD", outcome, reason, Accounting(8192, 8192, 3), after,
        claimed_bytes, claimed_inodes,
    )
    calls = _install_result_consumer(monkeypatch, [result])
    prompt = _prepare_workspace_cleanup_reclamation_context(
        orch, owner, "dev_agent", stale_orchestration_step_count=0,
        claimed_next_step_count=1,
    )
    assert calls == ["TASK-OLD"]
    audits = _reclamation_audits(db, "TASK-HOOK")
    assert len(audits) == 1
    assert audits[0]["payload"] == {
        "target_task_id": "TASK-OLD", "outcome": outcome,
        "claimed_bytes": claimed_bytes, "claimed_inodes": claimed_inodes,
        "remainder": expected_remainder, "reason": reason,
        "publication": "attempted",
    }
    assert f"target=TASK-OLD outcome={outcome}" in prompt
    assert f"reason={reason}" in prompt
    assert f"claimed_bytes={claimed_bytes}" in prompt
    assert f"claimed_inodes={claimed_inodes}" in prompt
    # The prompt fact transports the actual returned ``after`` accounting
    # (or the explicit ``null`` for unknown after-accounting) -- not only the
    # audit payload -- so a known partial remainder survives even when owner
    # audit publication fails.
    if expected_remainder is None:
        assert "remainder=null" in prompt
    else:
        assert (
            "remainder=" + json.dumps(expected_remainder, sort_keys=True)
        ) in prompt
    # The hook publishes only the audit/prompt facts; it never writes a
    # synthetic completion summary for the owner.
    assert db.get_task_results("TASK-HOOK") == []


def test_workspace_cleanup_hook_escaped_consumer_exception_publishes_no_audit_and_stops(
    runtime, db, monkeypatch,
):
    """An escaped ordinary consumer failure is unknown: no fabricated None
    audit/claims and no later target starts."""
    import runtime.daemon.task_scratch_reclamation as reclamation
    from runtime.orchestrator.run_step import _prepare_workspace_cleanup_reclamation_context

    orch, owner = _hook_owner_with_mocked_selection(
        runtime, db, monkeypatch, candidates=["TASK-OLD", "TASK-OLD-2"],
    )
    calls: list[str] = []

    def boom(**kwargs):
        calls.append(kwargs["task_id"])
        raise RuntimeError("consumer exploded")

    monkeypatch.setattr(
        reclamation, "collect_revalidate_seal_consume_disposable", boom,
    )
    assert _prepare_workspace_cleanup_reclamation_context(
        orch, owner, "dev_agent", stale_orchestration_step_count=0,
        claimed_next_step_count=1,
    ) == ""
    assert calls == ["TASK-OLD"]
    assert _reclamation_audits(db, "TASK-HOOK") == []
    # Unknown outcome is never reconstructed into a summary or result row.
    assert db.get_task_results("TASK-HOOK") == []


def test_workspace_cleanup_hook_baseexception_interruption_escapes(
    runtime, db, monkeypatch,
):
    """BaseException-class interruption is never swallowed as a refusal."""
    import runtime.daemon.task_scratch_reclamation as reclamation
    from runtime.orchestrator.run_step import _prepare_workspace_cleanup_reclamation_context

    class _Interruption(BaseException):
        pass

    orch, owner = _hook_owner_with_mocked_selection(
        runtime, db, monkeypatch, candidates=["TASK-OLD", "TASK-OLD-2"],
    )
    calls: list[str] = []

    def interrupt(**kwargs):
        calls.append(kwargs["task_id"])
        raise _Interruption("cancelled")

    monkeypatch.setattr(
        reclamation, "collect_revalidate_seal_consume_disposable", interrupt,
    )
    with pytest.raises(_Interruption):
        _prepare_workspace_cleanup_reclamation_context(
            orch, owner, "dev_agent", stale_orchestration_step_count=0,
            claimed_next_step_count=1,
        )
    assert calls == ["TASK-OLD"]
    assert _reclamation_audits(db, "TASK-HOOK") == []


@pytest.mark.parametrize("after_tuple", [(4096, 8192, 1), None])
def test_workspace_cleanup_hook_audit_failure_keeps_known_facts_and_stops(
    runtime, db, monkeypatch, after_tuple,
):
    """A known return whose audit publication fails transports only the known
    facts -- including the literal ``after`` remainder (or ``null``) -- plus
    ``publication_unavailable`` and starts no later target."""
    from runtime.daemon.task_scratch_reclamation import Accounting, ReclamationResult
    from runtime.orchestrator.run_step import _prepare_workspace_cleanup_reclamation_context

    orch, owner = _hook_owner_with_mocked_selection(
        runtime, db, monkeypatch, candidates=["TASK-OLD", "TASK-OLD-2"],
    )
    after = Accounting(*after_tuple) if after_tuple is not None else None
    result = ReclamationResult(
        "TASK-OLD", "failed", "fail-closed filesystem error",
        Accounting(8192, 8192, 3), after, 0, 0,
    )
    calls = _install_result_consumer(monkeypatch, [result, result])

    def failing_audit(*args, **kwargs):
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(db, "insert_audit_log", failing_audit)
    prompt = _prepare_workspace_cleanup_reclamation_context(
        orch, owner, "dev_agent", stale_orchestration_step_count=0,
        claimed_next_step_count=1,
    )
    assert calls == ["TASK-OLD"]
    assert "target=TASK-OLD outcome=failed" in prompt
    assert "reason=fail-closed filesystem error" in prompt
    assert "publication_unavailable" in prompt
    if after is None:
        assert "remainder=null" in prompt
    else:
        assert (
            "remainder=" + json.dumps(
                {"allocated_bytes": 4096, "apparent_bytes": 8192, "inodes": 1},
                sort_keys=True,
            )
        ) in prompt
    assert "TASK-OLD-2" not in prompt
    assert _reclamation_audits(db, "TASK-HOOK") == []


@pytest.mark.asyncio
async def test_workspace_cleanup_hook_real_between_ec_mutation_returns_one_none_audit(
    runtime, db, test_settings, monkeypatch, tmp_path,
):
    """A real mutation between the consumer's compared E/C observations invokes
    ``None`` with no executor and exactly one literal None audit."""
    from dataclasses import replace
    import runtime.daemon.task_scratch_reclamation as reclamation
    from runtime.orchestrator.run_step import _prepare_workspace_cleanup_reclamation_context

    _enable_reclamation_actions(runtime)
    owner_id, target_id = _manual_third_run_owner(runtime, db)
    workspace = runtime.workspaces_dir / "dev_agent"
    workspace.mkdir(parents=True, exist_ok=True)
    contract = _prepare_manifested_scratch(
        workspace, target_id, f"session-{target_id}", old_ns=_SCRATCH_OLD_NS,
    )
    calls: list[str] = []
    _install_real_consumer_wrapper(
        monkeypatch, proc_root=_make_fake_proc_root(tmp_path),
        now_ns=_SCRATCH_OLD_NS + 121_000_000_000, calls=calls,
    )
    real_coverage = reclamation._collect_private_coverage
    coverage_calls: list = []

    def mutated(**kwargs):
        value = real_coverage(**kwargs)
        coverage_calls.append(value)
        if len(coverage_calls) == 1:
            snapshot = replace(
                value.snapshot,
                populations=(*value.snapshot.populations, ("injected", ("42",))),
            )
            return replace(value, snapshot=snapshot)
        return value

    monkeypatch.setattr(reclamation, "_collect_private_coverage", mutated)
    executed: list = []
    monkeypatch.setattr(reclamation, "execute_ledger", lambda rows: executed.extend(rows))
    orch, owner = _claim_owner_and_orchestrator(runtime, db, owner_id)
    prompt = _prepare_workspace_cleanup_reclamation_context(
        orch, owner, "dev_agent", stale_orchestration_step_count=0,
        claimed_next_step_count=1,
    )
    assert calls == [target_id]
    assert not executed
    assert contract.root.is_dir()  # no executor ran, so the literal root survives
    audits = _reclamation_audits(db, owner_id)
    assert len(audits) == 1
    assert audits[0]["payload"] == {
        "target_task_id": target_id, "outcome": "none", "claimed_bytes": 0,
        "claimed_inodes": 0, "remainder": None, "reason": None,
        "publication": "attempted",
    }
    assert f"target={target_id} refused_or_unavailable" in prompt


@pytest.mark.asyncio
async def test_workspace_cleanup_hook_harmless_pre_e1_mutation_is_not_a_refusal(
    runtime, db, test_settings, monkeypatch, tmp_path,
):
    """A completed change observed consistently from E1 onward still removes
    the root; it is not automatically a mismatch refusal."""
    from runtime.orchestrator.run_step import _prepare_workspace_cleanup_reclamation_context

    _enable_reclamation_actions(runtime)
    owner_id, target_id = _manual_third_run_owner(runtime, db)
    workspace = runtime.workspaces_dir / "dev_agent"
    workspace.mkdir(parents=True, exist_ok=True)
    contract = _prepare_manifested_scratch(
        workspace, target_id, f"session-{target_id}", old_ns=_SCRATCH_OLD_NS,
    )
    extra = contract.root / "nested" / "extra-before-e1"
    extra.write_bytes(b"y" * 4096)
    for path in (extra, contract.root / "nested", contract.root):
        os.utime(path, ns=(_SCRATCH_OLD_NS, _SCRATCH_OLD_NS), follow_symlinks=False)
    calls: list[str] = []
    _install_real_consumer_wrapper(
        monkeypatch, proc_root=_make_fake_proc_root(tmp_path),
        now_ns=_SCRATCH_OLD_NS + 121_000_000_000, calls=calls,
    )
    orch, owner = _claim_owner_and_orchestrator(runtime, db, owner_id)
    prompt = _prepare_workspace_cleanup_reclamation_context(
        orch, owner, "dev_agent", stale_orchestration_step_count=0,
        claimed_next_step_count=1,
    )
    assert calls == [target_id]
    assert not contract.root.exists()
    audits = _reclamation_audits(db, owner_id)
    assert len(audits) == 1
    assert audits[0]["payload"]["outcome"] == "completed"
    assert f"target={target_id} outcome=completed" in prompt


@pytest.mark.asyncio
async def test_workspace_cleanup_hook_action_audit_precedes_prompt_and_latest_five(
    runtime, db, test_settings, monkeypatch, tmp_path,
):
    """Action -> owner audit -> prompt ordering, and the target-only reclamation
    audit never becomes a trigger-marked latest-five row."""
    from runtime.orchestrator.orchestrator import Orchestrator

    _enable_reclamation_actions(runtime)
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    workspace = runtime.workspaces_dir / "dev_agent"
    workspace.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    _seed_cleanup_target_and_history(db, agent="dev_agent", old_ns=_SCRATCH_OLD_NS, now=now)
    _prepare_manifested_scratch(workspace, "TASK-100", "session-100", old_ns=_SCRATCH_OLD_NS)
    calls: list[str] = []
    _install_real_consumer_wrapper(
        monkeypatch, proc_root=_make_fake_proc_root(tmp_path),
        now_ns=_SCRATCH_OLD_NS + 121_000_000_000, calls=calls,
    )
    owner_id = await wcs.trigger_cleanup(
        _build_cleanup_org(runtime, db, test_settings),
        agent="dev_agent", enqueue=lambda *_: None,
    )
    assert owner_id is not None

    orch = Orchestrator(db=db, settings=Settings(max_orchestration_steps=5),
                        paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._sessions = SessionTracker()
    orch._queue = _SlugQueue()
    observed: dict = {}

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        records = _reclamation_audits(db, owner_id)
        observed["targets_at_prompt"] = [
            row["payload"]["target_task_id"] for row in records
        ]
        observed["prompt"] = prompt
        # Simulate the ordinary post-launch completion callback, which is the
        # real writer of the durable task_results summary.
        db.insert_task_result(
            task_id, agent, "session-owner",
            json.dumps({"action": "done", "summary": "cleanup done"}),
            1, status="completed",
        )
        return _make_result(), _make_report(
            output_summary=json.dumps({"action": "done", "summary": "cleanup done"}),
        )

    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)
    orch.run_step(owner_id)

    # Every owner-side audit (including TASK-100's completed removal) is durable
    # before the prompt reaches the agent.
    assert "TASK-100" in observed["targets_at_prompt"]
    assert "target=TASK-100 outcome=completed" in observed["prompt"]
    # The ordinary completion summary is persisted for the owner.
    reports = db.get_task_results(owner_id)
    assert reports
    assert reports[-1]["output_summary"] == json.dumps(
        {"action": "done", "summary": "cleanup done"}
    )
    # latest-five is trigger-marker scoped: the target-only reclamation audit
    # neither adds a row nor displaces the owner.
    activity = db.list_workspace_cleanup_activity("dev_agent", limit=5)
    assert [row["task_id"] for row in activity] == [owner_id]


# ---------------------------------------------------------------------------
# TASK-8417 correction 1: the registered-owner guard is the caller's duty.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workspace_cleanup_hook_unregistered_owner_is_refused_before_selection(
    runtime, db, test_settings, monkeypatch, tmp_path,
):
    """An enqueued scheduled third owner whose agent is absent from the
    in-memory TeamsRegistry reaches no selector/consumer call or action audit,
    and the scratch stays intact: canonical path construction is not a
    registration check."""
    from runtime.orchestrator.orchestrator import Orchestrator

    owner_id, workspace = await _make_third_run_owner(
        runtime, db, test_settings, monkeypatch,
    )
    contract = _add_disposable_target(db, workspace)
    calls: list[str] = []
    _install_real_consumer_wrapper(
        monkeypatch, proc_root=_make_fake_proc_root(tmp_path),
        now_ns=_SCRATCH_OLD_NS + 121_000_000_000, calls=calls,
    )
    teams = TeamsRegistry.load(runtime.root)
    teams.remove_worker("engineering", "dev_agent")
    assert teams.team_for_agent("dev_agent") is None
    assert "dev_agent" not in teams.all_agents()

    orch = Orchestrator(db=db, settings=Settings(max_orchestration_steps=5),
                        paths=runtime, slug="test", teams=teams)
    orch._sessions = SessionTracker()
    orch._queue = _SlugQueue()
    prompts: list[str] = []

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        prompts.append(prompt)
        return _make_result(), _make_report(
            output_summary=json.dumps({"action": "done", "summary": "done"}),
        )

    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)
    orch.run_step(owner_id)

    assert calls == []
    assert contract.root.is_dir()
    assert _reclamation_audits(db, owner_id) == []
    assert all("Workspace cleanup reclamation" not in text for text in prompts)


# ---------------------------------------------------------------------------
# TASK-8417 correction 3: ordinary callback / completion-result loss windows.
# ---------------------------------------------------------------------------


class _RecordingCompletionEventBus:
    def __init__(self) -> None:
        self.published: list[tuple] = []

    async def publish(self, task_id, event) -> None:
        self.published.append((task_id, event))


class _RaisingCompletionEventBus:
    """Disposable double that fails after the real callback commit."""

    async def publish(self, task_id, event) -> None:
        raise RuntimeError("callback transport failed after commit")


async def _drive_ordinary_completion(
    db, *, task_id: str, agent: str, session_id: str, summary: str, event_bus,
):
    """Drive the real ordinary completion boundary (route + DB transaction).

    The route guards, the SessionTracker binding lease, and
    ``admit_task_completion_callback`` are the shipping seams; only the
    post-commit event bus is a disposable double.
    """
    import asyncio

    from runtime.daemon.routes.tasks import CompletionBody, submit_completion

    sessions = SessionTracker()
    sessions.set_active(task_id, agent, session_id)
    org = SimpleNamespace(
        db=db, sessions=sessions, db_lock=asyncio.Lock(), event_bus=event_bus,
    )
    body = CompletionBody(
        session_id=session_id, agent=agent, status="completed",
        confidence=80, output_summary=summary,
    )
    return await submit_completion(task_id, body, org), org


def _completed_action(runtime, db, monkeypatch):
    """Run the hook once with one canned completed consumer result."""
    from runtime.daemon.task_scratch_reclamation import Accounting, ReclamationResult
    from runtime.orchestrator.run_step import _prepare_workspace_cleanup_reclamation_context

    orch, owner = _hook_owner_with_mocked_selection(
        runtime, db, monkeypatch, candidates=["TASK-OLD"],
    )
    result = ReclamationResult(
        "TASK-OLD", "completed", None, Accounting(8192, 8192, 3),
        Accounting(0, 0, 0), 8192, 3,
    )
    calls = _install_result_consumer(monkeypatch, [result])
    prompt = _prepare_workspace_cleanup_reclamation_context(
        orch, owner, "dev_agent", stale_orchestration_step_count=0,
        claimed_next_step_count=1,
    )
    assert calls == ["TASK-OLD"]
    assert len(_reclamation_audits(db, "TASK-HOOK")) == 1
    return orch, calls, prompt


@pytest.mark.asyncio
async def test_workspace_cleanup_hook_precommit_callback_failure_has_no_owner_summary(
    runtime, db, monkeypatch,
):
    """A callback whose uncommitted result insert fails rolls the real
    transaction back: no durable owner summary and no fabricated report, while
    the reclamation action/audit evidence is untouched."""
    orch, calls, _prompt = _completed_action(runtime, db, monkeypatch)

    def precommit_boom(**kwargs):
        raise RuntimeError("precommit insert failed")

    monkeypatch.setattr(db, "_insert_task_result", precommit_boom)
    with pytest.raises(RuntimeError):
        await _drive_ordinary_completion(
            db, task_id="TASK-HOOK", agent="dev_agent",
            session_id="session-owner",
            summary=json.dumps({"action": "done", "summary": "cleanup done"}),
            event_bus=_RecordingCompletionEventBus(),
        )

    assert db.get_task_results("TASK-HOOK") == []
    assert db.get_latest_task_result("TASK-HOOK", "dev_agent", "session-owner") is None
    assert orch._read_completion_from_db(
        "TASK-HOOK", "dev_agent", "session-owner",
    ) is None
    # Action/audit evidence preserved; the action is never retried/rescanned.
    assert calls == ["TASK-OLD"]
    assert len(_reclamation_audits(db, "TASK-HOOK")) == 1


@pytest.mark.asyncio
async def test_workspace_cleanup_hook_postcommit_callback_failure_retains_actual_result(
    runtime, db, monkeypatch,
):
    """A callback that fails after the real commit retains exactly the durable
    same-agent/session result; the ordinary read reconstructs it and no
    synthetic summary is produced."""
    orch, calls, _prompt = _completed_action(runtime, db, monkeypatch)
    summary = json.dumps({"action": "done", "summary": "cleanup done"})

    with pytest.raises(RuntimeError):
        await _drive_ordinary_completion(
            db, task_id="TASK-HOOK", agent="dev_agent",
            session_id="session-owner", summary=summary,
            event_bus=_RaisingCompletionEventBus(),
        )

    row = db.get_latest_task_result("TASK-HOOK", "dev_agent", "session-owner")
    assert row is not None
    assert row["output_summary"] == summary
    report = orch._read_completion_from_db("TASK-HOOK", "dev_agent", "session-owner")
    assert report is not None
    assert report.output_summary == summary
    assert len(db.get_task_results("TASK-HOOK")) == 1
    assert calls == ["TASK-OLD"]
    assert len(_reclamation_audits(db, "TASK-HOOK")) == 1


@pytest.mark.asyncio
async def test_workspace_cleanup_hook_result_read_loss_retains_actual_durable_result(
    runtime, db, monkeypatch,
):
    """A lost subsequent completion-result read never fabricates a summary and
    never changes the actual durable same-agent/session result row."""
    orch, calls, _prompt = _completed_action(runtime, db, monkeypatch)
    summary = json.dumps({"action": "done", "summary": "cleanup done"})
    response, _org = await _drive_ordinary_completion(
        db, task_id="TASK-HOOK", agent="dev_agent",
        session_id="session-owner", summary=summary,
        event_bus=_RecordingCompletionEventBus(),
    )
    assert response == {"ok": True}

    # The subsequent completion-result read is lost while the durable row stays.
    monkeypatch.setattr(db, "get_latest_task_result", lambda *a, **k: None)
    assert orch._read_completion_from_db(
        "TASK-HOOK", "dev_agent", "session-owner",
    ) is None
    rows = db.get_task_results("TASK-HOOK")
    assert len(rows) == 1
    assert rows[0]["agent"] == "dev_agent"
    assert rows[0]["session_id"] == "session-owner"
    assert rows[0]["output_summary"] == summary
    assert calls == ["TASK-OLD"]
    assert len(_reclamation_audits(db, "TASK-HOOK")) == 1


# ---------------------------------------------------------------------------
# TASK-8417 correction 4: finite shared-config seam matrix.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("config_text", [
    "",
    "workspace_cleanup:\n",
    "workspace_cleanup: {}\n",
])
def test_workspace_cleanup_hook_missing_null_block_is_disabled(
    runtime, db, monkeypatch, config_text,
):
    """Missing file, null block, and empty block all default the action key to
    false: no selector query, no consumer call, no action audit."""
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.run_step import _prepare_workspace_cleanup_reclamation_context

    if config_text:
        runtime.org_config_path.write_text(config_text)
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    monkeypatch.setattr(
        db, "select_workspace_cleanup_reclamation_candidates",
        lambda **_: pytest.fail("disabled hook must not query the selector"),
    )
    assert _prepare_workspace_cleanup_reclamation_context(
        orch, SimpleNamespace(id="TASK-HOOK"), "dev_agent",
        stale_orchestration_step_count=0, claimed_next_step_count=1,
    ) == ""
    assert _reclamation_audits(db, "TASK-HOOK") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("config_text", [
    "workspace_cleanup:\n  reclamation_actions_enabled:\n",
    "workspace_cleanup: {\n",
])
async def test_workspace_cleanup_hook_invalid_initial_config_forms_escape(
    runtime, db, test_settings, monkeypatch, config_text,
):
    """A null action key and malformed YAML keep the shared loader's
    OrgConfigError escape at the initial hook read: zero consumer calls and
    zero action audits."""
    from runtime.orchestrator.org_config import OrgConfigError
    from runtime.orchestrator.run_step import _prepare_workspace_cleanup_reclamation_context

    owner_id, _ws = await _make_third_run_owner(runtime, db, test_settings, monkeypatch)
    runtime.org_config_path.write_text(config_text)
    calls: list[str] = []
    _install_never_consumer(monkeypatch, calls)
    orch, owner = _claim_owner_and_orchestrator(runtime, db, owner_id)
    with pytest.raises(OrgConfigError):
        _prepare_workspace_cleanup_reclamation_context(
            orch, owner, "dev_agent", stale_orchestration_step_count=0,
            claimed_next_step_count=1,
        )
    assert calls == []
    assert _reclamation_audits(db, owner_id) == []


@pytest.mark.asyncio
async def test_workspace_cleanup_hook_config_read_oserror_escapes(
    runtime, db, test_settings, monkeypatch,
):
    """``Path.read_text()`` OSError is not a YAML error: the shared loader (and
    therefore the hook) lets it escape with zero consumer calls/audits."""
    from runtime.orchestrator.run_step import _prepare_workspace_cleanup_reclamation_context

    owner_id, _ws = await _make_third_run_owner(runtime, db, test_settings, monkeypatch)
    # A directory at the config path makes read_text() raise IsADirectoryError.
    runtime.org_config_path.unlink()
    runtime.org_config_path.mkdir()
    calls: list[str] = []
    _install_never_consumer(monkeypatch, calls)
    orch, owner = _claim_owner_and_orchestrator(runtime, db, owner_id)
    with pytest.raises(OSError):
        _prepare_workspace_cleanup_reclamation_context(
            orch, owner, "dev_agent", stale_orchestration_step_count=0,
            claimed_next_step_count=1,
        )
    assert calls == []
    assert _reclamation_audits(db, owner_id) == []


@pytest.mark.asyncio
async def test_workspace_cleanup_hook_later_fresh_load_failure_retains_prior_fact(
    runtime, db, test_settings, monkeypatch, tmp_path,
):
    """A fresh per-target load failure after an earlier known action is a
    bounded refusal: it starts no next action and retains the prior fact."""
    from runtime.orchestrator.run_step import _prepare_workspace_cleanup_reclamation_context

    owner_id, workspace = await _make_third_run_owner(
        runtime, db, test_settings, monkeypatch,
    )
    contract = _add_disposable_target(db, workspace)
    calls: list[str] = []
    _install_real_consumer_wrapper(
        monkeypatch, proc_root=_make_fake_proc_root(tmp_path),
        now_ns=_SCRATCH_OLD_NS + 121_000_000_000, calls=calls,
    )
    # Load #1 is the initial enabled read; #2 is TASK-100's fresh load (which
    # succeeds and acts); #3 is the next candidate's fresh load and fails.
    _config_wrapper(monkeypatch, when=3, raises=True)
    orch, owner = _claim_owner_and_orchestrator(runtime, db, owner_id)
    prompt = _prepare_workspace_cleanup_reclamation_context(
        orch, owner, "dev_agent", stale_orchestration_step_count=0,
        claimed_next_step_count=1,
    )
    assert calls == ["TASK-100"]
    assert not contract.root.exists()
    assert "outcome=completed" in prompt
    audits = _reclamation_audits(db, owner_id)
    assert len(audits) == 1
    assert audits[0]["payload"]["target_task_id"] == "TASK-100"


@pytest.mark.asyncio
async def test_workspace_cleanup_hook_initial_config_failure_aborts_step_before_action(
    runtime, db, test_settings, monkeypatch, tmp_path,
):
    """An upstream shared-loader failure on the shipping ``run_step`` path
    raises before any action/audit and never promises an ordinary report."""
    from runtime.orchestrator.org_config import OrgConfigError
    from runtime.orchestrator.orchestrator import Orchestrator

    owner_id, workspace = await _make_third_run_owner(
        runtime, db, test_settings, monkeypatch,
    )
    contract = _add_disposable_target(db, workspace)
    calls: list[str] = []
    _install_real_consumer_wrapper(
        monkeypatch, proc_root=_make_fake_proc_root(tmp_path),
        now_ns=_SCRATCH_OLD_NS + 121_000_000_000, calls=calls,
    )
    runtime.org_config_path.write_text("workspace_cleanup: {\n")
    orch = Orchestrator(db=db, settings=Settings(max_orchestration_steps=5),
                        paths=runtime, slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._sessions = SessionTracker()
    orch._queue = _SlugQueue()
    with pytest.raises(OrgConfigError):
        orch.run_step(owner_id)
    assert calls == []
    assert contract.root.is_dir()
    assert _reclamation_audits(db, owner_id) == []
    assert db.get_task_results(owner_id) == []
