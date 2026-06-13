from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from runtime.config import Settings
from runtime.orchestrator.executors import (
    AgentExecutor,
    ClaudeExecutor,
    CodexExecutor,
    ExecutorResult,
    OpencodeExecutor,
    PiExecutor,
)
from runtime.orchestrator._paths import OrgPaths
from runtime.runtime import RuntimeDir


@pytest.fixture
def runtime(tmp_path: Path) -> OrgPaths:
    """A minimal OrgPaths with engineering_head.md pre-seeded."""
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


@patch("runtime.orchestrator.executors.subprocess")
def test_claude_executor_launches_with_current_semantics(mock_subprocess, tmp_path, runtime):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()

    mock_subprocess.Popen.return_value = _popen_mock(stdout="Agent output")

    executor = ClaudeExecutor(claude_cli_path="claude", permission_mode="auto", settings=Settings(), paths=runtime)
    result = executor.run(
        workspace=workspace,
        prompt="Implement Alipay support",
        timeout_seconds=30,
    )

    assert result.success is True
    assert result.session_id is not None
    assert result.error is None

    call_args = mock_subprocess.Popen.call_args
    cmd = call_args[0][0]
    assert cmd[:2] == ["claude", "-p"]
    # The executor prepends the shared session-lifetime preamble to every prompt.
    sent = cmd[2]
    assert sent.endswith("Implement Alipay support")
    assert "<session-lifetime>" in sent
    assert "--permission-mode" in cmd
    assert "auto" in cmd
    assert "--allowedTools" in cmd
    allowed = cmd[cmd.index("--allowedTools") + 1]
    # Non-EH workspaces keep the narrow happyranch allowlist.
    assert "Bash(happyranch *)" in allowed
    assert "gh " not in allowed


@patch("runtime.orchestrator.executors.subprocess")
def test_claude_executor_grants_engineering_head_gh_resolve_rules(
    mock_subprocess, tmp_path, runtime,
):
    """EH's headless session needs explicit --allowedTools entries for the
    `gh pr close`/`gh issue close` cleanup flow. Settings.json is ignored in
    headless mode (see TASK-007/008/009 post-mortem), so the CLI flag is the
    only enforcement surface that matters at runtime."""
    workspace = tmp_path / "engineering_head"
    workspace.mkdir()

    mock_subprocess.Popen.return_value = _popen_mock(stdout="EH output")

    executor = ClaudeExecutor(claude_cli_path="claude", permission_mode="auto", settings=Settings(), paths=runtime)
    executor.run(workspace=workspace, prompt="decide next step", timeout_seconds=30)

    cmd = mock_subprocess.Popen.call_args[0][0]
    allowed = cmd[cmd.index("--allowedTools") + 1]
    assert "Bash(happyranch *)" in allowed
    assert "Bash(gh pr close *)" in allowed
    assert "Bash(gh pr comment *)" in allowed
    assert "Bash(gh issue close *)" in allowed
    assert "Bash(gh issue comment *)" in allowed
    # Guardrail mirrors the settings.json test.
    assert "gh pr merge" not in allowed
    assert "gh pr create" not in allowed


@patch("runtime.orchestrator.executors.subprocess")
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
    # `workspace-write` blocks localhost by default; the override is required
    # so agents can call back into the daemon via `happyranch report-completion`.
    # See TASK-080 post-mortem in CLAUDE.md.
    assert "-c" in cmd
    c_index = cmd.index("-c")
    assert cmd[c_index + 1] == "sandbox_workspace_write.network_access=true"
    # Prompt is passed through communicate(input=...), not Popen(input=...).
    # The executor prepends the shared session-lifetime preamble to every prompt.
    sent = mock_subprocess.Popen.return_value.communicate.call_args.kwargs["input"]
    assert sent.endswith("Implement Alipay support")
    assert "<session-lifetime>" in sent


@patch("runtime.orchestrator.executors.subprocess")
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


def test_settings_exposes_opencode_executor_defaults() -> None:
    settings = Settings(project_root=Path("/tmp/project"))

    assert settings.opencode_cli_path == "opencode"


def test_settings_exposes_pi_executor_defaults() -> None:
    settings = Settings(project_root=Path("/tmp/project"))

    assert settings.pi_cli_path == "pi"


@patch("runtime.orchestrator.executors.subprocess")
def test_opencode_executor_launches_run_with_workspace_dir(mock_subprocess, tmp_path):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()

    mock_subprocess.Popen.return_value = _popen_mock(stdout="event stream")

    executor = OpencodeExecutor(opencode_cli_path="opencode")
    result = executor.run(
        workspace=workspace,
        prompt="Implement Alipay support",
        timeout_seconds=30,
    )

    assert result.success is True
    assert result.session_id is not None

    cmd = mock_subprocess.Popen.call_args[0][0]
    assert cmd[:2] == ["opencode", "run"]
    assert "--dir" in cmd
    assert cmd[cmd.index("--dir") + 1] == str(workspace)
    assert "--format" in cmd
    assert cmd[cmd.index("--format") + 1] == "json"
    assert "--prompt" in cmd
    # The executor prepends the shared session-lifetime preamble to every prompt.
    sent = cmd[cmd.index("--prompt") + 1]
    assert sent.endswith("Implement Alipay support")
    assert "<session-lifetime>" in sent
    # Permission discipline lives in opencode.json — bypass flag must NOT be present.
    assert "--dangerously-skip-permissions" not in cmd


@patch("runtime.orchestrator.executors.subprocess")
def test_pi_executor_launches_print_mode_with_json_events(mock_subprocess, tmp_path):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()

    mock_subprocess.Popen.return_value = _popen_mock(stdout='{"type":"result"}\n')

    executor = PiExecutor(pi_cli_path="pi")
    result = executor.run(
        workspace=workspace,
        prompt="Implement Alipay support",
        timeout_seconds=30,
    )

    assert result.success is True
    assert result.session_id is not None

    cmd = mock_subprocess.Popen.call_args[0][0]
    assert cmd[:2] == ["pi", "-p"]
    # The executor prepends the shared session-lifetime preamble to every prompt.
    sent = cmd[cmd.index("-p") + 1]
    assert sent.endswith("Implement Alipay support")
    assert "<session-lifetime>" in sent
    assert "--mode" in cmd
    assert cmd[cmd.index("--mode") + 1] == "json"


@patch("runtime.orchestrator.executors.subprocess")
def test_opencode_executor_returns_failure_on_nonzero_exit(mock_subprocess, tmp_path):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()

    mock_subprocess.Popen.return_value = _popen_mock(
        returncode=3, stdout="", stderr="permission denied: rm *",
    )

    executor = OpencodeExecutor(opencode_cli_path="opencode")
    result = executor.run(
        workspace=workspace,
        prompt="x",
        timeout_seconds=30,
    )

    assert result.success is False
    assert result.returncode == 3
    assert "permission denied" in (result.stderr_tail or "")


@patch("runtime.orchestrator.executors.subprocess")
def test_opencode_executor_timeout(mock_subprocess, tmp_path):
    import subprocess

    workspace = tmp_path / "dev_agent"
    workspace.mkdir()

    proc = MagicMock()
    proc.pid = 5151
    proc.communicate.side_effect = [
        subprocess.TimeoutExpired(cmd="opencode", timeout=30),
        ("", ""),
    ]
    mock_subprocess.TimeoutExpired = subprocess.TimeoutExpired
    mock_subprocess.Popen.return_value = proc

    executor = OpencodeExecutor(opencode_cli_path="opencode")
    result = executor.run(workspace=workspace, prompt="long task", timeout_seconds=30)

    assert result.success is False
    assert "timed out" in (result.error or "").lower()
    assert proc.kill.called


@patch("runtime.orchestrator.executors.subprocess")
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


@patch("runtime.orchestrator.executors.subprocess")
def test_run_invokes_on_started_with_pid(mock_subprocess, tmp_path, runtime):
    """The /cancel feature depends on the executor handing the pid over to
    SessionTracker BEFORE communicate() blocks. Pin that contract for both
    executor classes — the common shape is in _run_command."""
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()

    mock_subprocess.Popen.return_value = _popen_mock(pid=9123)

    executor = AgentExecutor(claude_cli_path="claude", permission_mode="auto", settings=Settings(), paths=runtime)
    received: list[int] = []
    executor.run(
        workspace=workspace,
        prompt="x",
        timeout_seconds=30,
        on_started=lambda pid: received.append(pid),
    )

    assert received == [9123]


# -- Diagnostic plumbing (rc + stdout_tail + stderr_tail) -----------------
# These fields let _session_failed_note in run_step.py render self-diagnosing
# audit notes when a subprocess exits cleanly but never calls back (the
# TASK-077 signature). Without them the note degrades to "rc=?" with no
# preview, which is exactly what was observed for senior_dev's first Codex
# session.


@patch("runtime.orchestrator.executors.subprocess")
def test_claude_executor_populates_returncode_and_stdout_tail_on_success(
    mock_subprocess, tmp_path, runtime,
):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()

    mock_subprocess.Popen.return_value = _popen_mock(
        returncode=0, stdout="wrote ExplorePage.tsx\nbuild ok\n", stderr="",
    )

    executor = ClaudeExecutor(claude_cli_path="claude", permission_mode="auto", settings=Settings(), paths=runtime)
    result = executor.run(workspace=workspace, prompt="x", timeout_seconds=30)

    assert result.success is True
    assert result.returncode == 0
    assert "wrote ExplorePage.tsx" in result.stdout_tail
    assert result.stderr_tail == ""


@patch("runtime.orchestrator.executors.subprocess")
def test_codex_executor_populates_returncode_and_stderr_tail_on_failure(
    mock_subprocess, tmp_path,
):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()

    mock_subprocess.Popen.return_value = _popen_mock(
        returncode=2, stdout="", stderr="fatal: missing workspace\n",
    )

    executor = CodexExecutor(codex_cli_path="codex", sandbox_mode="workspace-write")
    result = executor.run(workspace=workspace, prompt="x", timeout_seconds=30)

    assert result.success is False
    assert result.returncode == 2
    assert "fatal: missing workspace" in result.stderr_tail
    assert result.stdout_tail == ""


@patch("runtime.orchestrator.executors.subprocess")
def test_timeout_leaves_returncode_none_and_preserves_error(
    mock_subprocess, tmp_path,
):
    """Timeouts kill the proc before an exit code is observed. We shouldn't
    fabricate a return code — the enriched note will render `rc=?` in that
    case, which is correct, while the `error` string carries the timeout."""
    import subprocess

    workspace = tmp_path / "dev_agent"
    workspace.mkdir()

    proc = MagicMock()
    proc.pid = 4242
    proc.communicate.side_effect = [
        subprocess.TimeoutExpired(cmd="codex", timeout=30),
        ("", ""),
    ]
    mock_subprocess.TimeoutExpired = subprocess.TimeoutExpired
    mock_subprocess.Popen.return_value = proc

    executor = CodexExecutor(codex_cli_path="codex", sandbox_mode="workspace-write")
    result = executor.run(workspace=workspace, prompt="x", timeout_seconds=30)

    assert result.success is False
    assert result.returncode is None
    assert "timed out" in (result.error or "").lower()


@patch("runtime.orchestrator.executors.subprocess")
def test_claude_executor_captures_session_id_from_json(mock_subprocess, tmp_path, runtime):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()
    mock_subprocess.Popen.return_value = _popen_mock(
        stdout='{"type":"result","result":"ok","session_id":"claude-abc-123",'
               '"usage":{"input_tokens":10,"output_tokens":5},"model":"claude"}',
    )
    executor = ClaudeExecutor(claude_cli_path="claude", permission_mode="auto", settings=Settings(), paths=runtime)
    result = executor.run(workspace=workspace, prompt="x", timeout_seconds=30)

    assert result.success is True
    assert result.agent_session_id == "claude-abc-123"
    # The HappyRanch session id is unchanged and distinct.
    assert result.session_id != "claude-abc-123"


@patch("runtime.orchestrator.executors.subprocess")
def test_claude_executor_appends_resume_flag_when_requested(mock_subprocess, tmp_path, runtime):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()
    mock_subprocess.Popen.return_value = _popen_mock(
        stdout='{"type":"result","session_id":"claude-new-999"}',
    )
    executor = ClaudeExecutor(claude_cli_path="claude", permission_mode="auto", settings=Settings(), paths=runtime)
    result = executor.run(
        workspace=workspace, prompt="delta only", timeout_seconds=30,
        resume_session_id="claude-prior-555",
    )

    cmd = mock_subprocess.Popen.call_args[0][0]
    assert "--resume" in cmd
    assert cmd[cmd.index("--resume") + 1] == "claude-prior-555"
    assert result.agent_session_id == "claude-new-999"


@patch("runtime.orchestrator.executors.subprocess")
def test_claude_executor_omits_resume_flag_by_default(mock_subprocess, tmp_path, runtime):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()
    mock_subprocess.Popen.return_value = _popen_mock(stdout='{"session_id":"s"}')
    executor = ClaudeExecutor(claude_cli_path="claude", permission_mode="auto", settings=Settings(), paths=runtime)
    executor.run(workspace=workspace, prompt="x", timeout_seconds=30)
    assert "--resume" not in mock_subprocess.Popen.call_args[0][0]


# -- rate_limited normalization (issue #85) -------------------------------
# _run_command sniffs every provider's stdout/stderr for the shared rate-limit
# signature and sets ExecutorResult.rate_limited, so the classifier and the
# throttle get one normalized field regardless of which executor ran.


def test_is_rate_limit_signature_matches_known_phrases():
    from runtime.orchestrator.executors import is_rate_limit_signature

    assert is_rate_limit_signature("Claude: hit your limit · resets at 6:30pm")
    assert is_rate_limit_signature("HTTP 429: rate limit exceeded")
    assert is_rate_limit_signature("RATE LIMIT")  # case-insensitive
    # "hit your limit" without "reset" is NOT a match (mirrors the classifier).
    assert not is_rate_limit_signature("you hit your limit of free retries")
    assert not is_rate_limit_signature("all good, wrote files")
    assert not is_rate_limit_signature("")


@patch("runtime.orchestrator.executors.subprocess")
def test_claude_executor_sets_rate_limited_from_stdout(mock_subprocess, tmp_path, runtime):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()
    # Claude prints the limit notice on stdout and exits 0.
    mock_subprocess.Popen.return_value = _popen_mock(
        returncode=0, stdout="hit your limit · resets at 6:30pm Pacific",
    )
    executor = ClaudeExecutor(claude_cli_path="claude", permission_mode="auto", settings=Settings(), paths=runtime)
    result = executor.run(workspace=workspace, prompt="x", timeout_seconds=30)
    assert result.rate_limited is True


@patch("runtime.orchestrator.executors.subprocess")
def test_codex_executor_sets_rate_limited_from_stderr(mock_subprocess, tmp_path):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()
    mock_subprocess.Popen.return_value = _popen_mock(
        returncode=1, stdout="", stderr="error: rate limit reached, retry later",
    )
    executor = CodexExecutor(codex_cli_path="codex", sandbox_mode="workspace-write")
    result = executor.run(workspace=workspace, prompt="x", timeout_seconds=30)
    assert result.rate_limited is True


@patch("runtime.orchestrator.executors.subprocess")
def test_opencode_executor_sets_rate_limited_from_stderr(mock_subprocess, tmp_path):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()
    mock_subprocess.Popen.return_value = _popen_mock(
        returncode=1, stdout="", stderr="429 rate limit",
    )
    executor = OpencodeExecutor(opencode_cli_path="opencode")
    result = executor.run(workspace=workspace, prompt="x", timeout_seconds=30)
    assert result.rate_limited is True


@patch("runtime.orchestrator.executors.subprocess")
def test_pi_executor_sets_rate_limited_from_stdout(mock_subprocess, tmp_path):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()
    mock_subprocess.Popen.return_value = _popen_mock(
        returncode=0, stdout='{"type":"result"} rate limit',
    )
    executor = PiExecutor(pi_cli_path="pi")
    result = executor.run(workspace=workspace, prompt="x", timeout_seconds=30)
    assert result.rate_limited is True


@patch("runtime.orchestrator.executors.subprocess")
def test_clean_run_is_not_rate_limited(mock_subprocess, tmp_path, runtime):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()
    mock_subprocess.Popen.return_value = _popen_mock(returncode=0, stdout="all good")
    executor = ClaudeExecutor(claude_cli_path="claude", permission_mode="auto", settings=Settings(), paths=runtime)
    result = executor.run(workspace=workspace, prompt="x", timeout_seconds=30)
    assert result.rate_limited is False
