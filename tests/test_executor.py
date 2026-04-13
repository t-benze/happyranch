import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.models import CompletionReport
from src.orchestrator.executor import AgentExecutor, ExecutorResult


def test_executor_result_from_completion_report():
    report = CompletionReport(
        task_id="TASK-001",
        agent="dev_agent",
        status="completed",
        confidence=85,
        output_summary="Done",
    )
    result = ExecutorResult(
        success=True,
        report=report,
        duration_seconds=60,
        session_id="sess-001",
    )
    assert result.success is True
    assert result.report.confidence == 85


def test_executor_result_when_no_report():
    result = ExecutorResult(
        success=False,
        report=None,
        duration_seconds=120,
        session_id="sess-002",
        error="No completion_report.json found",
    )
    assert result.success is False
    assert result.error == "No completion_report.json found"


def test_read_completion_report(tmp_path):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()
    report_data = {
        "task_id": "TASK-001",
        "agent": "dev_agent",
        "status": "completed",
        "confidence": 85,
        "output_summary": "Implemented feature",
        "risks_flagged": [],
        "dependencies": [],
        "suggested_reviewer_focus": [],
    }
    (workspace / "completion_report.json").write_text(json.dumps(report_data))

    executor = AgentExecutor(claude_cli_path="claude", permission_mode="auto")
    report = executor.read_completion_report(workspace)
    assert report is not None
    assert report.task_id == "TASK-001"
    assert report.confidence == 85


def test_read_completion_report_missing_file(tmp_path):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()

    executor = AgentExecutor(claude_cli_path="claude", permission_mode="auto")
    report = executor.read_completion_report(workspace)
    assert report is None


def test_read_completion_report_invalid_json(tmp_path):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()
    (workspace / "completion_report.json").write_text("not valid json")

    executor = AgentExecutor(claude_cli_path="claude", permission_mode="auto")
    report = executor.read_completion_report(workspace)
    assert report is None


@patch("src.orchestrator.executor.subprocess")
def test_run_agent_session_success(mock_subprocess, tmp_path):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()

    report_data = {
        "task_id": "TASK-001",
        "agent": "dev_agent",
        "status": "completed",
        "confidence": 85,
        "output_summary": "Done",
        "risks_flagged": [],
        "dependencies": [],
        "suggested_reviewer_focus": [],
    }
    (workspace / "completion_report.json").write_text(json.dumps(report_data))

    mock_process = MagicMock()
    mock_process.returncode = 0
    mock_process.stdout = "Agent output"

    def write_report_side_effect(*args, **kwargs):
        (workspace / "completion_report.json").write_text(json.dumps(report_data))
        return mock_process

    mock_subprocess.run.side_effect = write_report_side_effect

    executor = AgentExecutor(claude_cli_path="claude", permission_mode="auto")
    result = executor.run(
        workspace=workspace,
        prompt="Implement Alipay support",
        timeout_seconds=30,
    )

    assert result.success is True
    assert result.report is not None
    assert result.report.task_id == "TASK-001"

    call_args = mock_subprocess.run.call_args
    cmd = call_args[0][0]
    assert "claude" in cmd[0]
    assert "-p" in cmd
    assert "--permission-mode" in cmd
    assert "auto" in cmd


@patch("src.orchestrator.executor.subprocess")
def test_run_agent_session_timeout(mock_subprocess, tmp_path):
    import subprocess

    workspace = tmp_path / "dev_agent"
    workspace.mkdir()

    mock_subprocess.TimeoutExpired = subprocess.TimeoutExpired
    mock_subprocess.run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=30)

    executor = AgentExecutor(claude_cli_path="claude", permission_mode="auto")
    result = executor.run(
        workspace=workspace,
        prompt="Long task",
        timeout_seconds=30,
    )

    assert result.success is False
    assert "timed out" in result.error.lower()


@patch("src.orchestrator.executor.subprocess")
def test_run_cleans_old_report_before_session(mock_subprocess, tmp_path):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()

    old_report = {"task_id": "OLD-TASK", "agent": "dev_agent", "status": "completed",
                  "confidence": 50, "output_summary": "old"}
    (workspace / "completion_report.json").write_text(json.dumps(old_report))

    mock_process = MagicMock()
    mock_process.returncode = 0
    mock_process.stdout = ""
    mock_subprocess.run.return_value = mock_process

    executor = AgentExecutor(claude_cli_path="claude", permission_mode="auto")
    result = executor.run(workspace=workspace, prompt="New task", timeout_seconds=30)

    assert result.success is False
