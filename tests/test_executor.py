from pathlib import Path
from unittest.mock import MagicMock, patch

from src.config import Settings
from src.orchestrator.executors import (
    AgentExecutor,
    ClaudeExecutor,
    CodexExecutor,
    ExecutorResult,
)


def _popen_mock(returncode: int = 0, stdout: str = "", stderr: str = "", pid: int = 4242):
    proc = MagicMock()
    proc.pid = pid
    proc.returncode = returncode
    proc.communicate.return_value = (stdout, stderr)
    return proc


@patch("src.orchestrator.executors.subprocess")
def test_claude_executor_launches_with_current_semantics(mock_subprocess, tmp_path):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()

    mock_subprocess.Popen.return_value = _popen_mock(stdout="Agent output")

    executor = ClaudeExecutor(claude_cli_path="claude", permission_mode="auto", settings=Settings())
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

    call_args = mock_subprocess.Popen.call_args
    cmd = call_args[0][0]
    assert cmd[:2] == ["claude", "-p"]
    assert "--permission-mode" in cmd
    assert "auto" in cmd
    assert "--allowedTools" in cmd
    allowed = cmd[cmd.index("--allowedTools") + 1]
    # Non-EH workspaces keep the narrow opc allowlist.
    assert "Bash(opc *)" in allowed
    assert "gh " not in allowed


@patch("src.orchestrator.executors.subprocess")
def test_claude_executor_grants_engineering_head_gh_resolve_rules(
    mock_subprocess, tmp_path,
):
    """EH's headless session needs explicit --allowedTools entries for the
    `gh pr close`/`gh issue close` cleanup flow. Settings.json is ignored in
    headless mode (see TASK-007/008/009 post-mortem), so the CLI flag is the
    only enforcement surface that matters at runtime."""
    workspace = tmp_path / "engineering_head"
    workspace.mkdir()

    mock_subprocess.Popen.return_value = _popen_mock(stdout="EH output")

    executor = ClaudeExecutor(claude_cli_path="claude", permission_mode="auto", settings=Settings())
    executor.run(workspace=workspace, prompt="decide next step", timeout_seconds=30)

    cmd = mock_subprocess.Popen.call_args[0][0]
    allowed = cmd[cmd.index("--allowedTools") + 1]
    assert "Bash(opc *)" in allowed
    assert "Bash(gh pr close *)" in allowed
    assert "Bash(gh pr comment *)" in allowed
    assert "Bash(gh issue close *)" in allowed
    assert "Bash(gh issue comment *)" in allowed
    # Guardrail mirrors the settings.json test.
    assert "gh pr merge" not in allowed
    assert "gh pr create" not in allowed


@patch("src.orchestrator.executors.subprocess")
def test_codex_executor_launches_exec_with_explicit_sandbox(mock_subprocess, tmp_path):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()

    mock_subprocess.Popen.return_value = _popen_mock(stdout="Agent output")

    executor = CodexExecutor(codex_cli_path="codex", sandbox_mode="workspace-write")
    result = executor.run(
        workspace=workspace,
        prompt="Implement Alipay support",
        timeout_seconds=30,
    )

    assert result.success is True
    assert result.session_id is not None

    call_args = mock_subprocess.Popen.call_args
    cmd = call_args[0][0]
    assert cmd[:2] == ["codex", "exec"]
    assert "--sandbox" in cmd
    assert "workspace-write" in cmd
    assert "--skip-git-repo-check" in cmd
    assert "--json" in cmd
    assert cmd[-1] == "-"
    # Prompt is passed through communicate(input=...), not Popen(input=...).
    assert mock_subprocess.Popen.return_value.communicate.call_args.kwargs[
        "input"
    ] == "Implement Alipay support"


@patch("src.orchestrator.executors.subprocess")
def test_codex_executor_returns_failure_on_nonzero_exit(mock_subprocess, tmp_path):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()

    mock_subprocess.Popen.return_value = _popen_mock(
        returncode=2, stdout="", stderr="fatal: missing workspace",
    )

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

    mock_process = MagicMock()
    mock_process.pid = 4242
    # First communicate() call raises TimeoutExpired; second (after kill)
    # drains the pipes successfully.
    mock_process.communicate.side_effect = [
        subprocess.TimeoutExpired(cmd="codex", timeout=30),
        ("", ""),
    ]
    mock_subprocess.TimeoutExpired = subprocess.TimeoutExpired
    mock_subprocess.Popen.return_value = mock_process

    executor = CodexExecutor(codex_cli_path="codex", sandbox_mode="workspace-write")
    result = executor.run(
        workspace=workspace,
        prompt="Long task",
        timeout_seconds=30,
    )

    assert result.success is False
    assert "timed out" in result.error.lower()
    assert mock_process.kill.called


@patch("src.orchestrator.executors.subprocess")
def test_run_invokes_on_started_with_pid(mock_subprocess, tmp_path):
    """The /cancel feature depends on the executor handing the pid over to
    SessionTracker BEFORE communicate() blocks. Pin that contract for both
    executor classes — the common shape is in _run_command."""
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()

    mock_subprocess.Popen.return_value = _popen_mock(pid=9123)

    executor = AgentExecutor(claude_cli_path="claude", permission_mode="auto", settings=Settings())
    received: list[int] = []
    executor.run(
        workspace=workspace,
        prompt="x",
        timeout_seconds=30,
        on_started=lambda pid: received.append(pid),
    )

    assert received == [9123]
