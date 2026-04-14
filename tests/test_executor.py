from pathlib import Path
from unittest.mock import MagicMock, patch

from src.orchestrator.executor import AgentExecutor, ExecutorResult


@patch("src.orchestrator.executor.subprocess")
def test_run_agent_session_success(mock_subprocess, tmp_path):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()

    mock_process = MagicMock()
    mock_process.returncode = 0
    mock_process.stdout = "Agent output"
    mock_subprocess.run.return_value = mock_process

    executor = AgentExecutor(claude_cli_path="claude", permission_mode="auto")
    result = executor.run(
        workspace=workspace,
        prompt="Implement Alipay support",
        timeout_seconds=30,
    )

    assert result.success is True
    assert result.session_id is not None

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
