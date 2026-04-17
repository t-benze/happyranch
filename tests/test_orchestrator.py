import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.config import Settings
from src.infrastructure.database import Database
from src.models import (
    CompletionReport,
    TaskStatus,
    TaskType,
)
from src.orchestrator.executor import ExecutorResult
from src.orchestrator.orchestrator import Orchestrator
from src.runtime import RuntimeDir


def _make_eh_decision(task_id: str, decision: dict):
    """Simulate the Engineering Head returning a NextStep decision."""
    return (
        ExecutorResult(
            success=True,
            duration_seconds=30,
            session_id="sess-eh",
        ),
        CompletionReport(
            task_id=task_id,
            agent="engineering_head",
            status="completed",
            confidence=90,
            output_summary=json.dumps(decision),
        ),
    )


def _make_agent_result(task_id: str, agent: str, summary: str = "Work done"):
    """Simulate a worker agent completing its task."""
    return (
        ExecutorResult(
            success=True,
            duration_seconds=60,
            session_id="sess-worker",
        ),
        CompletionReport(
            task_id=task_id,
            agent=agent,
            status="completed",
            confidence=85,
            output_summary=summary,
        ),
    )


def _make_failed_result(task_id: str):
    return (
        ExecutorResult(
            success=False,
            duration_seconds=10,
            session_id="sess-fail",
            error="Session failed",
        ),
        None,
    )


@pytest.fixture
def orchestrator(test_settings, test_runtime):
    db = Database(test_runtime.db_path)
    return Orchestrator(db=db, settings=test_settings, runtime=test_runtime)


_DEFAULT_AGENTS = ["engineering_head", "product_manager", "dev_agent", "payment_agent"]

def _setup_workspaces(runtime, agents: list[str] | None = None):
    for agent in (agents or _DEFAULT_AGENTS):
        ws = runtime.workspaces_dir / agent
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "recent_tasks.md").write_text(f"# Recent Tasks: {agent}\n\n")
        skill = ws / ".claude" / "skills" / "start-task"
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text("# start-task\n")


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
    assert call_agent == "engineering_head"


@patch.object(Orchestrator, "_run_agent")
def test_eh_delegates_then_done(mock_run, orchestrator, test_runtime):
    """EH delegates to dev_agent, then approves the result."""
    _setup_workspaces(test_runtime)

    call_count = 0

    def mock_side_effect(task_id, agent, prompt):
        nonlocal call_count
        call_count += 1
        if agent == "engineering_head":
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
        return _make_agent_result(task_id, agent)

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
        if agent == "engineering_head":
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
        return _make_agent_result(task_id, agent)

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
        if agent == "engineering_head":
            return _make_eh_decision(task_id, {
                "action": "delegate",
                "agent": "dev_agent",
                "prompt": "Try again",
            })
        return _make_agent_result(task_id, agent)

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
        if agent == "engineering_head":
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
def test_delegate_blocked_report_treated_as_failure(mock_run, orchestrator, test_runtime):
    """A worker returning status=blocked must surface to EH as a failed step,
    not as a silent success."""
    _setup_workspaces(test_runtime)

    call_count = 0
    recorded_prior: list = []

    def mock_side_effect(task_id, agent, prompt):
        nonlocal call_count
        call_count += 1
        if agent == "engineering_head":
            if call_count == 1:
                return _make_eh_decision(task_id, {
                    "action": "delegate",
                    "agent": "dev_agent",
                    "prompt": "Implement feature",
                })
            # Capture what EH sees on its second decision round.
            recorded_prior.append(prompt)
            return _make_eh_decision(task_id, {
                "action": "escalate",
                "reason": "Worker blocked",
            })
        # dev_agent returns a blocked completion.
        return (
            ExecutorResult(success=True, duration_seconds=10, session_id="sess-dev"),
            CompletionReport(
                task_id=task_id,
                agent=agent,
                status="blocked",
                confidence=0,
                output_summary="needs missing credentials",
            ),
        )

    mock_run.side_effect = mock_side_effect

    task_id = orchestrator.create_task(TaskType.GENERAL, "Add feature")
    result = orchestrator.run_task(task_id)

    assert result == "escalated"
    # The second EH prompt must include the failed-step record so the EH can
    # react to the block. The prior-steps section prefixes failures explicitly.
    eh_second_prompt = recorded_prior[0]
    assert "blocked" in eh_second_prompt.lower()


@patch.object(Orchestrator, "_run_agent")
def test_eh_plain_text_output_treated_as_done(mock_run, orchestrator, test_runtime):
    """If EH returns plain text (not JSON), treat it as done with that text."""
    _setup_workspaces(test_runtime)

    mock_run.return_value = (
        ExecutorResult(
            success=True,
            duration_seconds=30,
            session_id="sess-eh",
        ),
        CompletionReport(
            task_id="TASK-001",
            agent="engineering_head",
            status="completed",
            confidence=85,
            output_summary="I explored the codebase. The payment module uses Stripe.",
        ),
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
    mock_run.return_value = (
        ExecutorResult(
            success=True,
            duration_seconds=30,
            session_id="sess-eh",
        ),
        CompletionReport(
            task_id="TASK-001",
            agent="engineering_head",
            status="completed",
            confidence=90,
            output_summary=json.dumps({"action": "delegate"}),
        ),
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
        if agent == "engineering_head":
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
        return _make_agent_result(task_id, agent)

    mock_run.side_effect = mock_side_effect

    task_id = orchestrator.create_task(TaskType.GENERAL, "Add feature")
    orchestrator.run_task(task_id)

    logs = orchestrator._db.get_audit_logs(task_id)
    verdicts = [l for l in logs if l["action"] == "review_verdict"]
    assert len(verdicts) == 1
    assert verdicts[0]["payload"]["reviewed_agent"] == "dev_agent"
    assert verdicts[0]["payload"]["verdict"] == "approved"


def test_task_metadata_in_agent_prompt(orchestrator, test_runtime, monkeypatch):
    """Agent prompts should include task_id, session_id, and brief."""
    _setup_workspaces(test_runtime)

    task_id = orchestrator.create_task(TaskType.GENERAL, "Explore payments")

    # Fix the session_id so we can pre-insert the DB row
    monkeypatch.setattr(orchestrator, "_build_session_id", lambda: "sess-eh")

    # Pre-insert the completion result that _read_completion_from_db will find
    orchestrator._db.insert_task_result(
        task_id,
        "engineering_head",
        "sess-eh",
        output_summary=json.dumps({"action": "done", "summary": "Done"}),
        confidence_score=90,
    )

    # Mock the executor.run to capture the prompt and return a valid result
    with patch.object(orchestrator._executor, "run") as mock_executor_run:
        mock_executor_run.return_value = ExecutorResult(
            success=True,
            duration_seconds=30,
            session_id="sess-eh",
        )

        orchestrator.run_task(task_id)

        # Check that the prompt passed to executor.run follows the start-task
        # SKILL parsing contract (see protocol/skills/start-task/SKILL.md).
        call_kwargs = mock_executor_run.call_args
        prompt = call_kwargs[1]["prompt"] if "prompt" in call_kwargs[1] else call_kwargs[0][1]
        assert "Use the start-task skill" in prompt
        assert "task_id: TASK-001" in prompt
        assert "brief: Explore payments" in prompt
        assert "session_id:" in prompt
        assert "role_guidance:" in prompt


def test_run_agent_fails_fast_when_workspace_missing_skill(orchestrator, test_runtime):
    """Workspace bootstrap is an explicit, operator-driven step. If the
    start-task skill file is missing, the orchestrator should raise an
    actionable error instead of silently marking the task rejected."""
    from src.orchestrator.orchestrator import WorkspaceNotInitialized

    task_id = orchestrator.create_task(TaskType.GENERAL, "ping")
    eh_workspace = test_runtime.workspaces_dir / "engineering_head"
    assert not eh_workspace.exists()

    with pytest.raises(WorkspaceNotInitialized) as exc_info:
        orchestrator.run_task(task_id)

    msg = str(exc_info.value)
    assert "engineering_head" in msg
    assert "opc init-agent engineering_head" in msg
    # The executor must never have been invoked against a broken workspace.
    assert not (eh_workspace / ".claude" / "skills" / "start-task" / "SKILL.md").exists()
