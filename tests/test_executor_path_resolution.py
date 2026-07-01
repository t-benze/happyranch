"""Tests for executor binary resolution (GH #254 — PATH-independent launch).

These tests are the TDD gate: they must FAIL before the fix is implemented,
then go GREEN after the executor resolution + env= logic lands.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from runtime.config import Settings
from runtime.orchestrator.executors import (
    ClaudeExecutor,
    CodexExecutor,
    ExecutorResult,
    OpencodeExecutor,
    PiExecutor,
    resolve_executor_binary,
)
from runtime.orchestrator._paths import OrgPaths
from runtime.runtime import RuntimeDir


@pytest.fixture
def runtime(tmp_path: Path) -> OrgPaths:
    rt = RuntimeDir.init(tmp_path / "rt")
    paths = OrgPaths(root=rt.orgs_dir / "x")
    from runtime.orchestrator.agent_def import AgentDef, render_agent_text
    from datetime import datetime, timezone
    eh = AgentDef(
        name="engineering_head",
        team="engineering",
        role="manager",
        executor="claude",
        allow_rules=("gh pr close", "gh pr comment", "gh issue close", "gh issue comment"),
        repos={},
        enrolled_by=None,
        enrolled_at_task=None,
        enrolled_at=datetime.now(timezone.utc),
        system_prompt="You are the Engineering Head.\n",
    )
    paths.agents_dir.mkdir(parents=True, exist_ok=True)
    (paths.agents_dir / "engineering_head.md").write_text(render_agent_text(eh))
    return paths


def _popen_mock(returncode: int = 0, stdout: str = "", stderr: str = "", pid: int = 4242):
    proc = MagicMock()
    proc.pid = pid
    proc.returncode = returncode
    proc.communicate.return_value = (stdout, stderr)
    return proc


# ── Test (i): executor binary resolves to ABSOLUTE path when PATH is stripped ──

@patch("runtime.orchestrator.executors.subprocess")
def test_executor_resolves_to_absolute_path_when_path_stripped(
    mock_subprocess, tmp_path, runtime,
):
    """Simulate Finder/launchd-launched daemon with PATH=/usr/bin:/bin.
    The executor binary MUST resolve to an absolute path, not a bare name."""
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()
    mock_subprocess.Popen.return_value = _popen_mock(stdout="ok")

    # Simulate stripped PATH — but shutil.which must still find the binary
    # through the prepended standard dirs (normalize_daemon_path).
    saved_path = os.environ.get("PATH", "")
    try:
        os.environ["PATH"] = "/usr/bin:/bin"
        from runtime.orchestrator.executors import normalize_daemon_path
        normalize_daemon_path()

        executor = ClaudeExecutor(
            claude_cli_path="claude", permission_mode="auto",
            settings=Settings(), paths=runtime,
        )
        result = executor.run(
            workspace=workspace, prompt="x", timeout_seconds=30,
        )

        assert result.success is True
        cmd = mock_subprocess.Popen.call_args[0][0]
        # cmd[0] must be an absolute path, not a bare "claude".
        assert os.path.isabs(cmd[0]), (
            f"Expected absolute path for executor binary, got {cmd[0]!r}"
        )
        assert cmd[0].endswith("claude") or "claude" in cmd[0]
    finally:
        os.environ["PATH"] = saved_path


@patch("runtime.orchestrator.executors.subprocess")
def test_executor_resolves_absolute_configured_path_as_is(
    mock_subprocess, tmp_path, runtime,
):
    """When the founder configures an explicit ABSOLUTE path (e.g.,
    /opt/homebrew/bin/claude), the executor MUST honor it directly
    without searching PATH.

    For testing, we use shutil.which to find a REAL binary then pass
    that absolute path — the executor must use it as-is."""
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()
    mock_subprocess.Popen.return_value = _popen_mock(stdout="ok")

    # Resolve a real binary (e.g., /bin/ls) to test absolute-path handling.
    import shutil
    real_binary = shutil.which("ls")
    assert real_binary is not None, "Test requires 'ls' on PATH"

    executor = ClaudeExecutor(
        claude_cli_path=real_binary, permission_mode="auto",
        settings=Settings(), paths=runtime,
    )
    result = executor.run(
        workspace=workspace, prompt="x", timeout_seconds=30,
    )

    assert result.success is True
    cmd = mock_subprocess.Popen.call_args[0][0]
    assert cmd[0] == real_binary


# ── Test (ii): unresolvable executor yields actionable diagnostic ──

def test_unresolvable_executor_raises_actionable_error():
    """An executor binary that cannot be found on PATH MUST raise a clear
    FileNotFoundError naming WHICH executor and WHICH dirs were searched —
    NOT a bare ENOENT from the OS."""
    with pytest.raises(FileNotFoundError) as exc_info:
        resolve_executor_binary("nonexistent-binary-zzz-not-real")
    msg = str(exc_info.value)
    assert "nonexistent-binary-zzz-not-real" in msg, (
        f"Error must name the unresolvable binary, got: {msg!r}"
    )
    assert "PATH" in msg or "Searched" in msg, (
        f"Error must mention searched dirs, got: {msg!r}"
    )


def test_unresolvable_executor_nonexistent_absolute_path_raises_actionable_error():
    """An absolute path that doesn't exist must also raise a clear diagnostic."""
    with pytest.raises(FileNotFoundError) as exc_info:
        resolve_executor_binary("/nonexistent/path/to/binary")
    msg = str(exc_info.value)
    assert "/nonexistent/path/to/binary" in msg


# ── Test: env= is passed to Popen (normalized PATH in subprocess) ──

@patch("runtime.orchestrator.executors.subprocess")
def test_run_command_passes_env_to_popen(mock_subprocess, tmp_path):
    """_run_command MUST pass env=os.environ.copy() to Popen so the
    subprocess inherits the daemon's normalized PATH, not the raw inherited
    PATH of the daemon process itself (which under launchd is /usr/bin:/bin)."""
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()
    mock_subprocess.Popen.return_value = _popen_mock(stdout="ok")

    executor = CodexExecutor(codex_cli_path="codex", sandbox_mode="workspace-write")
    result = executor.run(
        workspace=workspace, prompt="x", timeout_seconds=30,
    )

    assert result.success is True
    call_kwargs = mock_subprocess.Popen.call_args[1]
    assert "env" in call_kwargs, (
        "Popen must receive an explicit env= kwarg with the normalized PATH"
    )
    env = call_kwargs["env"]
    assert "PATH" in env, "env dict must contain PATH"
