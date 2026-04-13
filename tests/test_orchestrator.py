import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.config import Settings
from src.infrastructure.database import Database
from src.models import (
    AgentName,
    CompletionReport,
    TaskStatus,
    TaskType,
)
from src.orchestrator.executor import ExecutorResult
from src.orchestrator.orchestrator import Orchestrator
from src.runtime import RuntimeDir


def _make_eh_decision(task_id: str, decision: dict) -> ExecutorResult:
    """Simulate the Engineering Head returning a NextStep decision."""
    return ExecutorResult(
        success=True,
        report=CompletionReport(
            task_id=task_id,
            agent="engineering_head",
            status="completed",
            confidence=90,
            output_summary=json.dumps(decision),
        ),
        duration_seconds=30,
        session_id="sess-eh",
    )


def _make_agent_result(task_id: str, agent: str, summary: str = "Work done") -> ExecutorResult:
    """Simulate a worker agent completing its task."""
    return ExecutorResult(
        success=True,
        report=CompletionReport(
            task_id=task_id,
            agent=agent,
            status="completed",
            confidence=85,
            output_summary=summary,
        ),
        duration_seconds=60,
        session_id="sess-worker",
    )


def _make_failed_result(task_id: str) -> ExecutorResult:
    return ExecutorResult(
        success=False,
        report=None,
        duration_seconds=10,
        session_id="sess-fail",
        error="Session failed",
    )


@pytest.fixture
def orchestrator(test_settings, test_runtime):
    db = Database(test_runtime.db_path)
    return Orchestrator(db=db, settings=test_settings, runtime=test_runtime)


def _setup_workspaces(runtime):
    """Create workspace dirs with recent_tasks.md for all agents."""
    for agent in AgentName:
        ws = runtime.workspaces_dir / agent.value
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "recent_tasks.md").write_text(f"# Recent Tasks: {agent.value}\n\n")


def test_create_task(orchestrator):
    task_id = orchestrator.create_task(TaskType.GENERAL, "Explore the codebase")
    assert task_id == "TASK-001"
    task = orchestrator._db.get_task(task_id)
    assert task.status == TaskStatus.PENDING
    assert task.brief == "Explore the codebase"


def test_create_task_with_type(orchestrator):
    task_id = orchestrator.create_task(TaskType.IMPLEMENT_FEATURE, "Add Alipay")
    task = orchestrator._db.get_task(task_id)
    assert task.type == TaskType.IMPLEMENT_FEATURE


@patch.object(Orchestrator, "_run_agent")
def test_eh_handles_directly(mock_run, orchestrator, test_runtime):
    """EH explores and returns done on first step -- no delegation."""
    _setup_workspaces(test_runtime)

    mock_run.return_value = _make_eh_decision("TASK-001", {
        "action": "done",
        "summary": "Explored the payment system. Refunds use Stripe API v3.",
    })

    task_id = orchestrator.create_task(TaskType.GENERAL, "How do refunds work?")
    result = orchestrator.run_task(task_id)

    assert result == "approved"
    assert mock_run.call_count == 1
    # Only EH was called, no workers
    call_agent = mock_run.call_args_list[0][0][1]
    assert call_agent == AgentName.ENGINEERING_HEAD


@patch.object(Orchestrator, "_run_agent")
def test_eh_delegates_then_done(mock_run, orchestrator, test_runtime):
    """EH delegates to dev_agent, then approves the result."""
    _setup_workspaces(test_runtime)

    call_count = 0

    def mock_side_effect(task_id, agent, prompt):
        nonlocal call_count
        call_count += 1
        if agent == AgentName.ENGINEERING_HEAD:
            if call_count == 1:
                return _make_eh_decision(task_id, {
                    "action": "delegate",
                    "agent": "dev_agent",
                    "prompt": "Implement the Alipay integration",
                })
            else:
                return _make_eh_decision(task_id, {
                    "action": "done",
                    "summary": "Dev agent implemented Alipay. Looks good.",
                })
        return _make_agent_result(task_id, agent.value)

    mock_run.side_effect = mock_side_effect

    task_id = orchestrator.create_task(TaskType.GENERAL, "Add Alipay support")
    result = orchestrator.run_task(task_id)

    assert result == "approved"
    assert call_count == 3  # EH decide + dev_agent work + EH decide


@patch.object(Orchestrator, "_run_agent")
def test_eh_multi_step_delegation(mock_run, orchestrator, test_runtime):
    """EH delegates to PM, then to Dev, then approves."""
    _setup_workspaces(test_runtime)

    eh_calls = 0

    def mock_side_effect(task_id, agent, prompt):
        nonlocal eh_calls
        if agent == AgentName.ENGINEERING_HEAD:
            eh_calls += 1
            if eh_calls == 1:
                return _make_eh_decision(task_id, {
                    "action": "delegate",
                    "agent": "product_manager",
                    "prompt": "Write a spec for Alipay integration",
                })
            elif eh_calls == 2:
                return _make_eh_decision(task_id, {
                    "action": "delegate",
                    "agent": "dev_agent",
                    "prompt": "Implement based on the spec",
                })
            else:
                return _make_eh_decision(task_id, {
                    "action": "done",
                    "summary": "Feature complete",
                })
        return _make_agent_result(task_id, agent.value)

    mock_run.side_effect = mock_side_effect

    task_id = orchestrator.create_task(TaskType.GENERAL, "Add Alipay support")
    result = orchestrator.run_task(task_id)

    assert result == "approved"
    task = orchestrator._db.get_task(task_id)
    assert task.status == TaskStatus.APPROVED


@patch.object(Orchestrator, "_run_agent")
def test_eh_escalates(mock_run, orchestrator, test_runtime):
    _setup_workspaces(test_runtime)

    mock_run.return_value = _make_eh_decision("TASK-001", {
        "action": "escalate",
        "reason": "This involves China/HK political content",
    })

    task_id = orchestrator.create_task(TaskType.GENERAL, "Write about HK relations")
    result = orchestrator.run_task(task_id)

    assert result == "escalated"
    task = orchestrator._db.get_task(task_id)
    assert task.status == TaskStatus.ESCALATED


@patch.object(Orchestrator, "_run_agent")
def test_max_steps_exceeded(mock_run, test_settings, test_runtime):
    """EH keeps delegating until max steps is reached."""
    test_settings.max_orchestration_steps = 3
    db = Database(test_runtime.db_path)
    orchestrator = Orchestrator(db=db, settings=test_settings, runtime=test_runtime)
    _setup_workspaces(test_runtime)

    def mock_side_effect(task_id, agent, prompt):
        if agent == AgentName.ENGINEERING_HEAD:
            return _make_eh_decision(task_id, {
                "action": "delegate",
                "agent": "dev_agent",
                "prompt": "Try again",
            })
        return _make_agent_result(task_id, agent.value)

    mock_run.side_effect = mock_side_effect

    task_id = orchestrator.create_task(TaskType.GENERAL, "Infinite loop task")
    result = orchestrator.run_task(task_id)

    assert result == "escalated"
    task = orchestrator._db.get_task(task_id)
    assert task.status == TaskStatus.ESCALATED


@patch.object(Orchestrator, "_run_agent")
def test_eh_session_fails(mock_run, orchestrator, test_runtime):
    """If the EH session itself fails, task is rejected."""
    _setup_workspaces(test_runtime)
    mock_run.return_value = _make_failed_result("TASK-001")

    task_id = orchestrator.create_task(TaskType.GENERAL, "Do something")
    result = orchestrator.run_task(task_id)

    assert result == "rejected"


@patch.object(Orchestrator, "_run_agent")
def test_delegate_agent_fails_eh_sees_failure(mock_run, orchestrator, test_runtime):
    """When a delegated agent fails, EH sees the failure and can decide."""
    _setup_workspaces(test_runtime)

    call_count = 0

    def mock_side_effect(task_id, agent, prompt):
        nonlocal call_count
        call_count += 1
        if agent == AgentName.ENGINEERING_HEAD:
            if call_count == 1:
                return _make_eh_decision(task_id, {
                    "action": "delegate",
                    "agent": "dev_agent",
                    "prompt": "Implement feature",
                })
            else:
                # EH sees the failure and escalates
                return _make_eh_decision(task_id, {
                    "action": "escalate",
                    "reason": "Dev agent failed, need human help",
                })
        # dev_agent fails
        return _make_failed_result(task_id)

    mock_run.side_effect = mock_side_effect

    task_id = orchestrator.create_task(TaskType.GENERAL, "Add feature")
    result = orchestrator.run_task(task_id)

    assert result == "escalated"


@patch.object(Orchestrator, "_run_agent")
def test_eh_plain_text_output_treated_as_done(mock_run, orchestrator, test_runtime):
    """If EH returns plain text (not JSON), treat it as done with that text."""
    _setup_workspaces(test_runtime)

    mock_run.return_value = ExecutorResult(
        success=True,
        report=CompletionReport(
            task_id="TASK-001",
            agent="engineering_head",
            status="completed",
            confidence=85,
            output_summary="I explored the codebase. The payment module uses Stripe.",
        ),
        duration_seconds=30,
        session_id="sess-eh",
    )

    task_id = orchestrator.create_task(TaskType.GENERAL, "Explore payments")
    result = orchestrator.run_task(task_id)

    assert result == "approved"


@patch.object(Orchestrator, "_run_agent")
def test_audit_log_records_orchestration_steps(mock_run, orchestrator, test_runtime):
    """Orchestration steps are logged to the audit trail."""
    _setup_workspaces(test_runtime)

    mock_run.return_value = _make_eh_decision("TASK-001", {
        "action": "done",
        "summary": "All good",
    })

    task_id = orchestrator.create_task(TaskType.GENERAL, "Check something")
    orchestrator.run_task(task_id)

    logs = orchestrator._db.get_audit_logs(task_id)
    orch_steps = [l for l in logs if l["action"] == "orchestration_step"]
    assert len(orch_steps) == 1
    assert orch_steps[0]["payload"]["decision"]["action"] == "done"


@patch.object(Orchestrator, "_run_agent")
def test_malformed_eh_json_escalates(mock_run, orchestrator, test_runtime):
    """Valid JSON with invalid schema should escalate, not auto-approve."""
    _setup_workspaces(test_runtime)

    # EH returns valid JSON but missing required 'agent' field for delegate action
    mock_run.return_value = ExecutorResult(
        success=True,
        report=CompletionReport(
            task_id="TASK-001",
            agent="engineering_head",
            status="completed",
            confidence=90,
            output_summary=json.dumps({"action": "delegate"}),
        ),
        duration_seconds=30,
        session_id="sess-eh",
    )

    task_id = orchestrator.create_task(TaskType.GENERAL, "Do something")
    result = orchestrator.run_task(task_id)

    # Should escalate (malformed decision), NOT auto-approve
    assert result == "escalated"
    task = orchestrator._db.get_task(task_id)
    assert task.status == TaskStatus.ESCALATED


@patch.object(Orchestrator, "_run_agent")
def test_review_verdicts_logged_for_delegated_agents(mock_run, orchestrator, test_runtime):
    """When EH approves, review_verdict entries are logged for delegated agents."""
    _setup_workspaces(test_runtime)

    call_count = 0

    def mock_side_effect(task_id, agent, prompt):
        nonlocal call_count
        call_count += 1
        if agent == AgentName.ENGINEERING_HEAD:
            if call_count == 1:
                return _make_eh_decision(task_id, {
                    "action": "delegate",
                    "agent": "dev_agent",
                    "prompt": "Implement feature",
                })
            else:
                return _make_eh_decision(task_id, {
                    "action": "done",
                    "summary": "Looks good",
                })
        return _make_agent_result(task_id, agent.value)

    mock_run.side_effect = mock_side_effect

    task_id = orchestrator.create_task(TaskType.GENERAL, "Add feature")
    orchestrator.run_task(task_id)

    logs = orchestrator._db.get_audit_logs(task_id)
    verdicts = [l for l in logs if l["action"] == "review_verdict"]
    assert len(verdicts) == 1
    assert verdicts[0]["payload"]["reviewed_agent"] == "dev_agent"
    assert verdicts[0]["payload"]["verdict"] == "approved"


def test_task_metadata_in_agent_prompt(orchestrator, test_runtime):
    """Agent prompts should include task_id and brief."""
    _setup_workspaces(test_runtime)

    task_id = orchestrator.create_task(TaskType.GENERAL, "Explore payments")

    # Mock the executor.run to capture the prompt and return a valid result
    with patch.object(orchestrator._executor, "run") as mock_executor_run:
        mock_executor_run.return_value = ExecutorResult(
            success=True,
            report=CompletionReport(
                task_id=task_id,
                agent="engineering_head",
                status="completed",
                confidence=90,
                output_summary=json.dumps({"action": "done", "summary": "Done"}),
            ),
            duration_seconds=30,
            session_id="sess-eh",
        )

        orchestrator.run_task(task_id)

        # Check that the prompt passed to executor.run includes task metadata
        call_kwargs = mock_executor_run.call_args
        prompt = call_kwargs[1]["prompt"] if "prompt" in call_kwargs[1] else call_kwargs[0][1]
        assert "Task ID: TASK-001" in prompt
        assert "Brief: Explore payments" in prompt
