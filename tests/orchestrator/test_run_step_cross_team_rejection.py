from __future__ import annotations

from pathlib import Path

import asyncio
import pytest

from src.config import Settings
from src.infrastructure.database import Database
from src.models import BlockKind, CompletionReport, NextStep, TaskRecord, TaskStatus
from src.orchestrator.orchestrator import Orchestrator
from src.orchestrator.teams import TeamsRegistry, DEFAULT_LAYOUT
from src.runtime import RuntimeDir


def test_registry_flags_cross_team_delegation() -> None:
    registry = TeamsRegistry._from_layout(DEFAULT_LAYOUT)
    # Content Manager trying to delegate to dev_agent (engineering team)
    caller_team = registry.team_for_manager("content_manager")
    target_team = registry.team_for_agent("dev_agent")
    assert caller_team == "content"
    assert target_team == "engineering"
    assert caller_team != target_team


@pytest.fixture
def runtime(tmp_path: Path) -> RuntimeDir:
    return RuntimeDir.init(tmp_path / "rt")


@pytest.fixture
def db(runtime: RuntimeDir) -> Database:
    return Database(runtime.db_path)


def _make_result(success: bool = True):
    from src.orchestrator.executor import ExecutorResult
    return ExecutorResult(success=success, session_id="sess-x", duration_seconds=1)


def _make_report_with_decision(agent: str, decision: NextStep) -> CompletionReport:
    return CompletionReport(
        task_id="T-IGNORED",
        agent=agent,
        status="completed",
        confidence=80,
        output_summary="delegating to cross-team agent",
        decision=decision,
    )


def test_cross_team_feedback_path(runtime: RuntimeDir, db: Database, monkeypatch) -> None:
    """When a manager delegates to a cross-team agent, run_step must:

    - Insert a task_result row with the feedback text.
    - Log an orchestration_step audit entry with action='feedback'.
    - Leave the task PENDING (not DELEGATED) so the manager gets another step.
    - NOT enqueue or start a session for dev_agent.
    """
    # Set up: content_manager task, content_writer workspace exists on disk.
    # dev_agent workspace also present so _validate_delegate passes before
    # the cross-team check fires.
    (runtime.workspaces_dir / "content_manager").mkdir(parents=True)
    (runtime.workspaces_dir / "content_writer").mkdir(parents=True)
    (runtime.workspaces_dir / "dev_agent").mkdir(parents=True)

    db.insert_task(TaskRecord(
        id="T-1",
        brief="write a blog post",
        team="content",
        assigned_agent="content_manager",
    ))

    orch = Orchestrator(db=db, settings=Settings(max_orchestration_steps=10), runtime=runtime)
    q: asyncio.Queue = asyncio.Queue()
    orch._queue = q

    # Stub the executor to return a cross-team delegation decision
    cross_team_decision = NextStep(action="delegate", agent="dev_agent", prompt="build something")

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report_with_decision(agent, cross_team_decision)

    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    orch.run_step("T-1")

    task = db.get_task("T-1")

    # Task must be PENDING — not DELEGATED, not FAILED
    assert task.status == TaskStatus.PENDING
    assert task.block_kind is None

    # A task_result row with the feedback text must exist
    results = db.get_task_results("T-1")
    assert len(results) >= 1
    latest = results[-1]
    assert "content" in latest["output_summary"]
    assert "engineering" in latest["output_summary"]
    assert latest["confidence_score"] == 0

    # Audit log must have an orchestration_step entry with decision.action='feedback'
    audit_logs = db.get_audit_logs("T-1")
    feedback_steps = [
        e for e in audit_logs
        if e["action"] == "orchestration_step"
        and isinstance(e["payload"].get("decision"), dict)
        and e["payload"]["decision"].get("action") == "feedback"
    ]
    assert len(feedback_steps) == 1, f"expected 1 feedback step, got: {audit_logs}"

    # dev_agent must NOT have been delegated to — no child tasks
    children = db.get_children("T-1")
    assert children == []

    # The task must be re-enqueued for the next step
    assert q.qsize() == 1
    assert q.get_nowait() == "T-1"
