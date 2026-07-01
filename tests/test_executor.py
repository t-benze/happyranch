import os
import shutil
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

# Executor binary names used in the tests that mock subprocess but don't
# install real agent CLI binaries (the CI runner environment).
_EXECUTOR_NAMES = frozenset({"claude", "codex", "opencode", "pi"})


@pytest.fixture(autouse=True)
def _mock_shutil_which(monkeypatch):
    """Patch shutil.which inside executors so the executor constructors'
    _resolve_binary calls resolve deterministically regardless of host PATH.

    The real shutil.which is consulted first: when it returns a path (host
    has the binary, or a test sets up a tmpdir on PATH), that real path is
    honoured.  Only when the real lookup returns None and the name is a
    recognised executor binary does this fixture inject a stable synthetic
    path so the existing Popen-mocked tests don't crash on CI.
    """
    import runtime.orchestrator.executors as _ex_mod

    _real_which = shutil.which

    def _patched_which(name, path=None):
        real = _real_which(name, path=path)
        if real is not None:
            return real
        if name in _EXECUTOR_NAMES:
            return f"/usr/local/bin/{os.path.basename(name)}"
        return None

    monkeypatch.setattr(_ex_mod.shutil, "which", _patched_which)


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
    assert cmd[0].endswith("claude")
    assert cmd[1] == "-p"
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
    assert cmd[0].endswith("codex")
    assert cmd[1] == "exec"
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
    assert cmd[0].endswith("opencode")
    assert cmd[1] == "run"
    assert "--dir" in cmd
    assert cmd[cmd.index("--dir") + 1] == str(workspace)
    assert "--format" in cmd
    assert cmd[cmd.index("--format") + 1] == "json"
    # opencode >= 1.14.0 uses positional prompt (issue #216); the prompt is the
    # last argument (after --format json).
    sent = cmd[-1]
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
    assert cmd[0].endswith("pi")
    assert cmd[1] == "-p"
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


# ── executor PATH resolution / binary normalisation (issue #254) ───────────


def test_normalize_path_restores_standard_tool_dirs(monkeypatch):
    """After _normalize_path, the executor search PATH includes standard tool
    directories even when the inherited PATH was minimal (/usr/bin:/bin).  This
    simulates a Finder/launchd-launched daemon."""
    from runtime.orchestrator.executors import _normalize_path

    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    _normalize_path()
    pathenv = os.environ["PATH"]
    paths = pathenv.split(":")
    # /opt/homebrew/bin and /usr/local/bin must be present.
    assert "/opt/homebrew/bin" in paths
    assert "/usr/local/bin" in paths
    # Original minimal dirs still present.
    assert "/usr/bin" in paths
    assert "/bin" in paths


def test_normalize_path_does_not_duplicate_existing_entries(monkeypatch):
    """Normalisation is idempotent: dirs already present are not duplicated."""
    from runtime.orchestrator.executors import _normalize_path

    monkeypatch.setenv("PATH", "/opt/homebrew/bin:/usr/bin")
    _normalize_path()
    pathenv = os.environ["PATH"]
    # Count occurrences of /opt/homebrew/bin
    assert pathenv.split(":").count("/opt/homebrew/bin") == 1


def test_resolve_binary_absolute_path_passthrough():
    """An absolute cli_path is returned unchanged — the founder configured it
    explicitly."""
    from runtime.orchestrator.executors import _resolve_binary

    result = _resolve_binary("/usr/local/bin/claude")
    assert result == "/usr/local/bin/claude"


def test_resolve_binary_bare_name_via_which(tmp_path, monkeypatch):
    """A bare name resolves to an absolute path via shutil.which when the
    binary exists on PATH."""
    from runtime.orchestrator.executors import _resolve_binary

    # Place a fake 'claude' binary in a tmp dir and add it to PATH.
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "claude").touch(mode=0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:/usr/bin:/bin")

    result = _resolve_binary("claude")
    assert result == str(fake_bin / "claude")
    assert os.path.isabs(result)


def test_resolve_binary_bare_name_stripped_path_still_finds_binary(monkeypatch, tmp_path):
    """When the inherited PATH is stripped to /usr/bin:/bin, the normalisation
    prepends standard dirs and a bare name still resolves to an absolute path
    (e.g., /opt/homebrew/bin/claude).  This is the precise failure mode from
    a Finder-launched daemon."""
    from runtime.orchestrator.executors import _resolve_binary, _normalize_path

    # Simulate a standard tool dir containing the binary.
    fake_homebrew = tmp_path / "opt" / "homebrew" / "bin"
    fake_homebrew.mkdir(parents=True)
    (fake_homebrew / "claude").touch(mode=0o755)
    (fake_homebrew / "codex").touch(mode=0o755)
    (fake_homebrew / "opencode").touch(mode=0o755)
    (fake_homebrew / "pi").touch(mode=0o755)

    # Override the standard-dir list so _normalize_path prepends our temp dir.
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    import runtime.orchestrator.executors as ex_mod
    original = ex_mod._STANDARD_TOOL_DIRS
    ex_mod._STANDARD_TOOL_DIRS = [str(fake_homebrew)]
    try:
        _normalize_path()

        result = _resolve_binary("claude")
        assert result == str(fake_homebrew / "claude")
        assert os.path.isabs(result)

        result2 = _resolve_binary("codex")
        assert result2 == str(fake_homebrew / "codex")
        assert os.path.isabs(result2)
    finally:
        ex_mod._STANDARD_TOOL_DIRS = original


def test_resolve_binary_unresolvable_raises_actionable_diagnostic():
    """An unresolvable binary raises an error that names WHICH executor and
    WHICH dirs were searched — not a bare ENOENT."""
    from runtime.orchestrator.executors import _resolve_binary, _normalize_path

    error_msg = None
    try:
        _resolve_binary("nonexistent-cli-tool-xyz")
    except RuntimeError as exc:
        error_msg = str(exc)

    assert error_msg is not None, "Expected RuntimeError for unresolvable binary"
    assert "nonexistent-cli-tool-xyz" in error_msg
    # Must mention search dirs.
    assert "PATH" in error_msg.lower() or "searched" in error_msg.lower() or "directory" in error_msg.lower()


@patch("runtime.orchestrator.executors.subprocess")
def test_executor_passes_explicit_env_to_popen(mock_subprocess, tmp_path):
    """After the PATH fix, _run_command passes an explicit env= dict to Popen
    so the subprocess does not ride the inherited (possibly stripped) PATH."""
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()
    mock_subprocess.Popen.return_value = _popen_mock(stdout="ok")

    executor = CodexExecutor(codex_cli_path="codex", sandbox_mode="workspace-write")
    executor.run(workspace=workspace, prompt="x", timeout_seconds=30)

    popen_kwargs = mock_subprocess.Popen.call_args[1]
    assert "env" in popen_kwargs, "Popen should receive an explicit env= dict"
    env_dict = popen_kwargs["env"]
    assert "PATH" in env_dict
    assert "/opt/homebrew/bin" in env_dict["PATH"] or "/usr/local/bin" in env_dict["PATH"]


@patch("runtime.orchestrator.executors.subprocess")
def test_absolute_cli_path_preserved_in_cmd_zero(mock_subprocess, tmp_path, runtime):
    """When claude_cli_path is an absolute path (founder-configured), it
    appears as-is in cmd[0]."""
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()
    mock_subprocess.Popen.return_value = _popen_mock(stdout="ok")

    executor = ClaudeExecutor(
        claude_cli_path="/opt/homebrew/bin/claude",
        permission_mode="auto",
        settings=Settings(),
        paths=runtime,
    )
    executor.run(workspace=workspace, prompt="x", timeout_seconds=30)

    cmd = mock_subprocess.Popen.call_args[0][0]
    assert cmd[0] == "/opt/homebrew/bin/claude"
