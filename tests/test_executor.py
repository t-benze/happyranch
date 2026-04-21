from pathlib import Path
from unittest.mock import MagicMock, patch

from src.config import Settings
from src.orchestrator.executors import ClaudeExecutor, CodexExecutor, ExecutorResult


@patch("src.orchestrator.executors.subprocess")
def test_claude_executor_launches_with_current_semantics(mock_subprocess, tmp_path):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()

    mock_process = MagicMock()
    mock_process.returncode = 0
    mock_process.stdout = "Agent output"
    mock_subprocess.run.return_value = mock_process

    executor = ClaudeExecutor(claude_cli_path="claude", permission_mode="auto")
    result = executor.run(
        workspace=workspace,
        prompt="Implement Alipay support",
        timeout_seconds=30,
    )

    assert result == ExecutorResult(
        success=True,
        duration_seconds=result.duration_seconds,
        session_id=result.session_id,
    )

    call_args = mock_subprocess.run.call_args
    cmd = call_args[0][0]
    assert cmd[:2] == ["claude", "-p"]
    assert "--permission-mode" in cmd
    assert "auto" in cmd
    assert "--allowedTools" in cmd
    assert "Bash(opc *)" in cmd


@patch("src.orchestrator.executors.subprocess")
def test_codex_executor_launches_exec_with_explicit_sandbox(mock_subprocess, tmp_path):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()

    mock_process = MagicMock()
    mock_process.returncode = 0
    mock_process.stdout = "Agent output"
    mock_subprocess.run.return_value = mock_process

    executor = CodexExecutor(codex_cli_path="codex", sandbox_mode="workspace-write")
    result = executor.run(
        workspace=workspace,
        prompt="Implement Alipay support",
        timeout_seconds=30,
    )

    assert result.success is True
    call_args = mock_subprocess.run.call_args
    cmd = call_args[0][0]
    assert cmd[:2] == ["codex", "exec"]
    assert "--sandbox" in cmd
    assert "workspace-write" in cmd
    assert "--skip-git-repo-check" in cmd
    assert "--json" in cmd
    assert cmd[-1] == "-"
    assert call_args.kwargs["input"] == "Implement Alipay support"


@patch("src.orchestrator.executors.subprocess")
def test_codex_executor_returns_failure_on_nonzero_exit(mock_subprocess, tmp_path):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()

    mock_process = MagicMock()
    mock_process.returncode = 2
    mock_process.stdout = ""
    mock_process.stderr = "fatal: missing workspace"
    mock_subprocess.run.return_value = mock_process

    executor = CodexExecutor(codex_cli_path="codex", sandbox_mode="workspace-write")
    result = executor.run(
        workspace=workspace,
        prompt="Implement Alipay support",
        timeout_seconds=30,
    )

    assert result.success is False
    assert result.error == "Command exited with code 2: fatal: missing workspace"


def test_settings_exposes_codex_executor_defaults() -> None:
    settings = Settings(project_root=Path("/tmp/project"))

    assert settings.codex_cli_path == "codex"
    assert settings.codex_sandbox_mode == "workspace-write"


@patch("src.orchestrator.executors.subprocess")
def test_codex_executor_timeout(mock_subprocess, tmp_path):
    import subprocess

    workspace = tmp_path / "dev_agent"
    workspace.mkdir()

    mock_subprocess.TimeoutExpired = subprocess.TimeoutExpired
    mock_subprocess.run.side_effect = subprocess.TimeoutExpired(cmd="codex", timeout=30)

    executor = CodexExecutor(codex_cli_path="codex", sandbox_mode="workspace-write")
    result = executor.run(
        workspace=workspace,
        prompt="Long task",
        timeout_seconds=30,
    )

    assert result.success is False
    assert "timed out" in result.error.lower()
