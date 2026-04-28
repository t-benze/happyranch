from __future__ import annotations

from pathlib import Path

import asyncio
import pytest

from src.config import Settings
from src.infrastructure.database import Database
from src.models import BlockKind, CompletionReport, NextStep, TaskRecord, TaskStatus
from src.orchestrator._paths import OrgPaths
from src.orchestrator.orchestrator import Orchestrator
from src.orchestrator.teams import TeamsRegistry
from src.runtime import RuntimeDir

_LAYOUT: dict[str, dict[str, object]] = {
    "engineering": {
        "manager": "engineering_head",
        "workers": ["product_manager", "dev_agent", "payment_agent", "qa_engineer"],
    },
    "content": {
        "manager": "content_manager",
        "workers": ["content_writer", "content_qa"],
    },
}


def test_registry_flags_cross_team_delegation() -> None:
    registry = TeamsRegistry._from_layout(_LAYOUT)
    # Content Manager trying to delegate to dev_agent (engineering team)
    caller_team = registry.team_for_manager("content_manager")
    target_team = registry.team_for_agent("dev_agent")
    assert caller_team == "content"
    assert target_team == "engineering"
    assert caller_team != target_team


@pytest.fixture
def paths(tmp_path: Path) -> OrgPaths:
    rt = RuntimeDir.init(tmp_path / "rt")
    op = OrgPaths(root=rt.orgs_dir / "test")
    op.teams_config_path.parent.mkdir(parents=True, exist_ok=True)
    op.teams_config_path.write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: engineering_head\n"
        "    workers: [product_manager, dev_agent, payment_agent, qa_engineer]\n"
        "  content:\n"
        "    manager: content_manager\n"
        "    workers: [content_writer, content_qa]\n"
    )
    return op


@pytest.fixture
def db(paths: OrgPaths) -> Database:
    return Database(paths.db_path)


def _make_result(success: bool = True):
    from src.orchestrator.executors import ExecutorResult
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


def test_cross_team_feedback_path(paths: OrgPaths, db: Database, monkeypatch) -> None:
    """When a manager delegates to a cross-team agent, run_step must:

    - Insert a task_result row with the feedback text.
    - Log an orchestration_step audit entry with action='feedback'.
    - Leave the task PENDING (not DELEGATED) so the manager gets another step.
    - NOT enqueue or start a session for dev_agent.
    """
    # Set up: content_manager task, content_writer workspace exists on disk.
    # dev_agent workspace also present so _validate_delegate passes before
    # the cross-team check fires.
    (paths.workspaces_dir / "content_manager").mkdir(parents=True)
    (paths.workspaces_dir / "content_writer").mkdir(parents=True)
    (paths.workspaces_dir / "dev_agent").mkdir(parents=True)

    db.insert_task(TaskRecord(
        id="T-1",
        brief="write a blog post",
        team="content",
        assigned_agent="content_manager",
    ))

    orch = Orchestrator(
        db=db,
        settings=Settings(max_orchestration_steps=10),
        org_paths=paths,
        slug="test",
        teams=TeamsRegistry.load(paths.root),
    )

    class _SlugQueue:
        def __init__(self) -> None:
            self.q: asyncio.Queue = asyncio.Queue()
        def put_nowait(self, slug: str, task_id: str) -> None:
            self.q.put_nowait((slug, task_id))
        def qsize(self) -> int:
            return self.q.qsize()
        def get_nowait(self):
            return self.q.get_nowait()

    q = _SlugQueue()
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
    assert q.get_nowait() == ("test", "T-1")
