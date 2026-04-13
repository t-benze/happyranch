import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.config import Settings
from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database
from src.models import (
    AgentName,
    CompletionReport,
    PerformanceTier,
    ReviewVerdict,
    TaskStatus,
    TaskType,
)
from src.orchestrator.executor import ExecutorResult
from src.orchestrator.orchestrator import Orchestrator


def _make_executor_result(task_id: str, agent: str, verdict: str = "completed") -> ExecutorResult:
    return ExecutorResult(
        success=True,
        report=CompletionReport(
            task_id=task_id,
            agent=agent,
            status=verdict,
            confidence=85,
            output_summary="Work completed",
        ),
        duration_seconds=60,
        session_id="sess-test",
    )


def _make_review_result(task_id: str, verdict: str, feedback: str | None = None) -> ExecutorResult:
    """Simulate Engineering Head returning a review verdict via completion report."""
    return ExecutorResult(
        success=True,
        report=CompletionReport(
            task_id=task_id,
            agent="engineering_head",
            status="completed",
            confidence=90,
            output_summary=json.dumps({
                "verdict": verdict,
                "feedback": feedback,
                "target_agent": "dev_agent",
            }),
        ),
        duration_seconds=30,
        session_id="sess-review",
    )


@pytest.fixture
def orchestrator(test_settings):
    db = Database(test_settings.get_db_path())
    return Orchestrator(db=db, settings=test_settings)


def test_create_task(orchestrator):
    task_id = orchestrator.create_task(
        task_type=TaskType.IMPLEMENT_FEATURE,
        brief="Add Alipay support",
    )
    assert task_id == "TASK-001"
    task = orchestrator._db.get_task(task_id)
    assert task.status == TaskStatus.PENDING
    assert task.brief == "Add Alipay support"


def test_create_task_increments_id(orchestrator):
    id1 = orchestrator.create_task(TaskType.IMPLEMENT_FEATURE, "Feature 1")
    id2 = orchestrator.create_task(TaskType.BUG_FIX, "Bug 1")
    assert id1 == "TASK-001"
    assert id2 == "TASK-002"


def test_build_chain_uses_tiers(orchestrator):
    chain = orchestrator.build_chain(TaskType.IMPLEMENT_FEATURE)
    agents = [s.agent for s in chain]
    assert agents == [
        AgentName.PRODUCT_MANAGER,
        AgentName.DEV_AGENT,
        AgentName.ENGINEERING_HEAD,
    ]


@patch.object(Orchestrator, "_run_agent_step")
def test_run_task_approved_flow(mock_run_step, orchestrator, test_settings):
    """Test the happy path: PM writes spec, Dev implements, Eng Head approves."""
    for agent in ["engineering_head", "product_manager", "dev_agent"]:
        ws = test_settings.get_workspaces_dir() / agent
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "recent_tasks.md").write_text(f"# Recent Tasks: {agent}\n\n")

    call_count = 0

    def mock_step(task_id, step, prior_output):
        nonlocal call_count
        call_count += 1
        agent = step.agent.value
        if step.action == "review":
            return _make_review_result(task_id, "approve")
        return _make_executor_result(task_id, agent)

    mock_run_step.side_effect = mock_step

    task_id = orchestrator.create_task(TaskType.IMPLEMENT_FEATURE, "Add feature")
    result = orchestrator.run_task(task_id)

    assert result == "approved"
    task = orchestrator._db.get_task(task_id)
    assert task.status == TaskStatus.APPROVED
    assert call_count == 3  # PM + Dev + review


@patch.object(Orchestrator, "_run_agent_step")
def test_run_task_revise_then_approve(mock_run_step, orchestrator, test_settings):
    """Test: Eng Head rejects first, Dev revises, then approved."""
    for agent in ["engineering_head", "product_manager", "dev_agent"]:
        ws = test_settings.get_workspaces_dir() / agent
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "recent_tasks.md").write_text(f"# Recent Tasks: {agent}\n\n")

    review_call = 0

    def mock_step(task_id, step, prior_output):
        nonlocal review_call
        agent = step.agent.value
        if step.action == "review":
            review_call += 1
            if review_call == 1:
                return _make_review_result(task_id, "revise", "Fix error handling")
            return _make_review_result(task_id, "approve")
        return _make_executor_result(task_id, agent)

    mock_run_step.side_effect = mock_step

    task_id = orchestrator.create_task(TaskType.IMPLEMENT_FEATURE, "Add feature")
    result = orchestrator.run_task(task_id)

    assert result == "approved"
    task = orchestrator._db.get_task(task_id)
    assert task.revision_count == 1


@patch.object(Orchestrator, "_run_agent_step")
def test_run_task_escalates_after_max_revisions(mock_run_step, orchestrator, test_settings):
    for agent in ["engineering_head", "product_manager", "dev_agent"]:
        ws = test_settings.get_workspaces_dir() / agent
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "recent_tasks.md").write_text(f"# Recent Tasks: {agent}\n\n")

    def mock_step(task_id, step, prior_output):
        agent = step.agent.value
        if step.action == "review":
            return _make_review_result(task_id, "revise", "Still not right")
        return _make_executor_result(task_id, agent)

    mock_run_step.side_effect = mock_step

    task_id = orchestrator.create_task(TaskType.IMPLEMENT_FEATURE, "Add feature")
    result = orchestrator.run_task(task_id)

    assert result == "escalated"
    task = orchestrator._db.get_task(task_id)
    assert task.status == TaskStatus.ESCALATED
    assert task.revision_count == 2


@patch.object(Orchestrator, "_run_agent_step")
def test_payment_change_logs_cross_audit_stub(mock_run_step, orchestrator, test_settings):
    for agent in ["engineering_head", "payment_agent"]:
        ws = test_settings.get_workspaces_dir() / agent
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "recent_tasks.md").write_text(f"# Recent Tasks: {agent}\n\n")

    # Also create workspaces for other agents (recent_tasks update iterates all)
    for agent in ["product_manager", "dev_agent"]:
        ws = test_settings.get_workspaces_dir() / agent
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "recent_tasks.md").write_text(f"# Recent Tasks: {agent}\n\n")

    def mock_step(task_id, step, prior_output):
        agent = step.agent.value
        if step.action == "review":
            return _make_review_result(task_id, "approve")
        return _make_executor_result(task_id, agent)

    mock_run_step.side_effect = mock_step

    task_id = orchestrator.create_task(TaskType.PAYMENT_CHANGE, "Add WeChat Pay")
    orchestrator.run_task(task_id)

    logs = orchestrator._db.get_audit_logs(task_id)
    cross_audit = [l for l in logs if l["action"] == "cross_audit_requested"]
    assert len(cross_audit) == 1
    assert cross_audit[0]["payload"]["auto_approved"] is True
