import json
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
def _mock_shutil_which(monkeypatch, tmp_path):
    """Pre-register built-in executor binaries in the machine-local registry
    so executor constructors' _resolve_binary calls resolve deterministically
    regardless of host PATH (THR-107 seq155: registration-only resolution).

    Creates fake binaries in a tmp dir and registers them in the binary
    registry keyed by each built-in executor name.
    """
    daemon_home = tmp_path / ".happyranch"
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home))

    from runtime.orchestrator.executor_binary_registry import set_binary
    for name in _EXECUTOR_NAMES:
        fake_bin = tmp_path / "bin" / name
        fake_bin.parent.mkdir(parents=True, exist_ok=True)
        fake_bin.touch(mode=0o755)
        set_binary(name, str(fake_bin))


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
    # THR-200: the prompt body travels via stdin, never argv.
    assert not any("Implement Alipay support" in el for el in cmd)
    assert "--permission-mode" in cmd
    assert "auto" in cmd
    assert "--allowedTools" in cmd
    allowed = cmd[cmd.index("--allowedTools") + 1]
    # Non-EH workspaces keep the narrow happyranch allowlist.
    assert "Bash(happyranch *)" in allowed
    assert "gh " not in allowed
    # Prompt delivered via stdin (communicate input), preamble included.
    sent = mock_subprocess.Popen.return_value.communicate.call_args.kwargs["input"]
    assert sent.endswith("Implement Alipay support")
    assert "<session-lifetime>" in sent


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
    # THR-200/TASK-6080: the prompt travels via stdin (input_text), never
    # argv — no positional message element on the command line.
    assert not any("Implement Alipay support" in el for el in cmd)
    sent = mock_subprocess.Popen.return_value.communicate.call_args.kwargs["input"]
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
    # THR-200: the prompt body travels via stdin, never argv.
    assert not any("Implement Alipay support" in el for el in cmd)
    assert "--mode" in cmd
    assert cmd[cmd.index("--mode") + 1] == "json"
    sent = mock_subprocess.Popen.return_value.communicate.call_args.kwargs["input"]
    assert sent.endswith("Implement Alipay support")
    assert "<session-lifetime>" in sent


def test_callee_env_redirects_large_tool_caches_into_owning_workspace(
    tmp_path, monkeypatch,
):
    from runtime.orchestrator.executors import _callee_env

    workspace = tmp_path / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    monkeypatch.setenv("TMPDIR", "/tmp/preserved-small-payloads")

    env = _callee_env(workspace=workspace)

    cache_root = workspace / ".happyranch" / "cache"
    assert env["TMPDIR"] == "/tmp/preserved-small-payloads"
    assert env["XDG_CACHE_HOME"] == str(cache_root / "xdg")
    assert env["UV_CACHE_DIR"] == str(cache_root / "uv")
    assert env["PIP_CACHE_DIR"] == str(cache_root / "pip")
    assert env["npm_config_cache"] == str(cache_root / "npm")
    assert env["NODE_COMPILE_CACHE"] == str(cache_root / "node-compile")
    assert env["GOCACHE"] == str(cache_root / "go-build")
    assert all(Path(path).is_dir() for path in (
        env["XDG_CACHE_HOME"], env["UV_CACHE_DIR"], env["PIP_CACHE_DIR"],
        env["npm_config_cache"], env["NODE_COMPILE_CACHE"], env["GOCACHE"],
    ))


def test_callee_env_isolates_concurrent_agents(tmp_path):
    from runtime.orchestrator.executors import _callee_env

    first = tmp_path / "workspaces" / "dev_agent"
    second = tmp_path / "workspaces" / "qa_engineer"
    first.mkdir(parents=True)
    second.mkdir(parents=True)

    first_env = _callee_env(workspace=first)
    second_env = _callee_env(workspace=second)

    assert first_env["UV_CACHE_DIR"] != second_env["UV_CACHE_DIR"]
    assert Path(first_env["UV_CACHE_DIR"]).is_relative_to(first)
    assert Path(second_env["UV_CACHE_DIR"]).is_relative_to(second)


def test_callee_env_cache_setup_error_does_not_fall_back_to_tmp(tmp_path):
    from runtime.orchestrator.executors import WorkspaceStorageError, _callee_env

    workspace = tmp_path / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / ".happyranch").write_text("blocks cache directory")

    with pytest.raises(WorkspaceStorageError, match="workspace-owned cache"):
        _callee_env(workspace=workspace)


@pytest.mark.parametrize(
    "symlink_component",
    [
        ".happyranch",
        ".happyranch/cache",
        ".happyranch/cache/uv",
    ],
)
def test_callee_env_rejects_symlinked_cache_path_components(
    tmp_path, symlink_component,
):
    from runtime.orchestrator.executors import WorkspaceStorageError, _callee_env

    workspace = tmp_path / "workspaces" / "dev_agent"
    outside = tmp_path / "outside"
    workspace.mkdir(parents=True)
    outside.mkdir()
    component = workspace / symlink_component
    component.parent.mkdir(parents=True, exist_ok=True)
    component.symlink_to(outside, target_is_directory=True)

    with pytest.raises(
        WorkspaceStorageError,
        match=r"unsafe workspace-owned cache path component: ",
    ):
        _callee_env(workspace=workspace)

    assert list(outside.iterdir()) == []


def test_callee_env_uses_canonical_workspace_root(tmp_path):
    from runtime.orchestrator.executors import _callee_env

    canonical_workspace = tmp_path / "workspaces" / "dev_agent"
    canonical_workspace.mkdir(parents=True)
    workspace_alias = tmp_path / "workspace-alias"
    workspace_alias.symlink_to(canonical_workspace, target_is_directory=True)

    env = _callee_env(workspace=workspace_alias)

    expected = canonical_workspace / ".happyranch" / "cache" / "uv"
    assert env["UV_CACHE_DIR"] == str(expected)
    assert expected.is_dir()


def test_callee_env_rejects_component_replaced_during_preparation(
    tmp_path, monkeypatch,
):
    from runtime.orchestrator import executors

    workspace = tmp_path / "workspaces" / "dev_agent"
    outside = tmp_path / "outside"
    workspace.mkdir(parents=True)
    outside.mkdir()
    real_open = executors.os.open
    cache_opens = 0

    def racing_open(path, flags, *args, **kwargs):
        nonlocal cache_opens
        if path == "cache":
            cache_opens += 1
            if cache_opens == 2:
                cache = workspace / ".happyranch" / "cache"
                cache.rename(workspace / ".happyranch" / "cache-replaced")
                cache.symlink_to(outside, target_is_directory=True)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(executors.os, "open", racing_open)

    with pytest.raises(
        executors.WorkspaceStorageError,
        match=r"unsafe workspace-owned cache path component: ",
    ):
        executors._callee_env(workspace=workspace)

    assert list(outside.iterdir()) == []


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
    # A provider-declared session cap is a truthful terminal classification,
    # not an automatic short-backoff retry signal.
    assert not is_rate_limit_signature(
        "You've hit your session limit · resets 7:10pm (Asia/Shanghai)"
    )


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


# ── bundled CLI PATH resolution (THR-085) ──────────────────────────────────


class TestBundledCliPathFrozen:
    """When ``sys.frozen`` is True (PyInstaller-bundled Mac app), the bundled
    CLI directory (``os.path.dirname(sys.executable)``) is prepended at the
    very front of PATH so bare-name ``happyranch`` resolves to the bundled
    binary, beating a stale ``~/.local/bin/happyranch``.

    The ``sys.frozen`` gate is the ONLY frozen-detection signal available —
    the Swift-side ``PACKAGING_MODE=bundled`` env var is stripped by
    EnvironmentSanitizer before the daemon child launches (THR-085).
    """

    def test_frozen_prepends_bundled_cli_dir_to_path(self, monkeypatch, tmp_path):
        """FROZEN: after _normalize_path, PATH starts with the bundled CLI dir."""
        from runtime.orchestrator.executors import _normalize_path

        bundled_dir = tmp_path / "Contents" / "Resources" / "daemon"
        bundled_dir.mkdir(parents=True)
        (bundled_dir / "happyranch").touch(mode=0o755)

        monkeypatch.setattr("sys.frozen", True, raising=False)
        monkeypatch.setattr("sys.executable", str(bundled_dir / "happyranch-daemon"), raising=False)
        monkeypatch.setenv("PATH", "/usr/bin:/bin")

        _normalize_path()

        pathenv = os.environ["PATH"]
        entries = pathenv.split(":")
        # Bundled CLI dir must be first — beats ~/.local/bin.
        assert entries[0] == str(bundled_dir), (
            f"Expected bundled dir {bundled_dir!s} first, got {entries[0]}"
        )

    def test_frozen_callee_env_carries_bundled_dir(self, monkeypatch, tmp_path):
        """FROZEN: _callee_env()['PATH'] includes the bundled dir ahead of
        ~/.local/bin so child processes resolve bare-name happyranch correctly."""
        from runtime.orchestrator.executors import _callee_env, _normalize_path

        bundled_dir = tmp_path / "Contents" / "Resources" / "daemon"
        bundled_dir.mkdir(parents=True)
        (bundled_dir / "happyranch").touch(mode=0o755)

        monkeypatch.setattr("sys.frozen", True, raising=False)
        monkeypatch.setattr("sys.executable", str(bundled_dir / "happyranch-daemon"), raising=False)
        monkeypatch.setenv("PATH", "/usr/bin:/bin")

        _normalize_path()

        callee = _callee_env()
        entries = callee["PATH"].split(":")
        assert entries[0] == str(bundled_dir), (
            f"Expected bundled dir {bundled_dir!s} first in callee env, got {entries[0]}"
        )
        # ~/.local/bin (from _STANDARD_TOOL_DIRS) must appear AFTER the bundled dir.
        local_bin = os.path.expanduser("~/.local/bin")
        bundled_idx = entries.index(str(bundled_dir))
        local_idx = entries.index(local_bin)
        assert bundled_idx < local_idx, (
            f"Bundled dir (idx {bundled_idx}) must precede ~/.local/bin (idx {local_idx})"
        )

    def test_frozen_idempotent_does_not_duplicate_bundled_dir(self, monkeypatch, tmp_path):
        """FROZEN: calling _normalize_path twice does not duplicate the bundled dir."""
        from runtime.orchestrator.executors import _normalize_path

        bundled_dir = tmp_path / "Contents" / "Resources" / "daemon"
        bundled_dir.mkdir(parents=True)
        (bundled_dir / "happyranch").touch(mode=0o755)

        monkeypatch.setattr("sys.frozen", True, raising=False)
        monkeypatch.setattr("sys.executable", str(bundled_dir / "happyranch-daemon"), raising=False)
        monkeypatch.setenv("PATH", "/usr/bin:/bin")

        _normalize_path()
        _normalize_path()

        pathenv = os.environ["PATH"]
        assert pathenv.split(":").count(str(bundled_dir)) == 1, (
            "Bundled dir must not be duplicated after second _normalize_path()"
        )

    def test_frozen_unconditional_front_when_bundled_dir_already_in_path(
        self, monkeypatch, tmp_path
    ):
        """FROZEN: when the bundled dir already appears later in PATH (e.g.
        behind ~/.local/bin from a stale editable install), the bundled dir
        still lands at index 0 exactly once — it must NOT be left behind the
        stale entry."""
        from runtime.orchestrator.executors import _normalize_path

        bundled_dir = tmp_path / "Contents" / "Resources" / "daemon"
        bundled_dir.mkdir(parents=True)
        (bundled_dir / "happyranch").touch(mode=0o755)

        local_bin = os.path.expanduser("~/.local/bin")
        # Seed PATH so the bundled dir is already present AFTER ~/.local/bin —
        # this is the exact scenario the old "if not in entries" guard mishandled.
        monkeypatch.setattr("sys.frozen", True, raising=False)
        monkeypatch.setattr("sys.executable", str(bundled_dir / "happyranch-daemon"), raising=False)
        monkeypatch.setenv(
            "PATH",
            f"{local_bin}:/usr/bin:/bin:{bundled_dir}",
        )

        _normalize_path()

        pathenv = os.environ["PATH"]
        entries = pathenv.split(":")
        # Bundled dir must be at index 0 — beats ~/.local/bin.
        assert entries[0] == str(bundled_dir), (
            f"Expected bundled dir {bundled_dir!s} at index 0, got {entries[0]}"
        )
        # Bundled dir must appear exactly once.
        assert entries.count(str(bundled_dir)) == 1, (
            f"Bundled dir must appear exactly once, got {entries.count(str(bundled_dir))}"
        )
        # Original entries still present (minux the old bundled copy).
        assert local_bin in entries
        assert "/usr/bin" in entries
        assert "/bin" in entries


class TestBundledCliPathDev:
    """When ``sys.frozen`` is absent or False (dev/headless/CI daemon), PATH
    resolution stays exactly as today — no bundled dir is injected."""

    def test_dev_path_unchanged_when_not_frozen(self, monkeypatch):
        """DEV: _normalize_path does NOT inject a bundled dir when not frozen."""
        from runtime.orchestrator.executors import _normalize_path

        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        # sys.frozen is absent (no setattr) — dev/headless/CI baseline.

        _normalize_path()

        pathenv = os.environ["PATH"]
        entries = pathenv.split(":")
        # Standard tool dirs are prepended, but NO bundled dir.
        assert "/opt/homebrew/bin" in entries
        assert "/usr/local/bin" in entries
        assert "happyranch-daemon" not in pathenv

    def test_dev_path_unchanged_when_frozen_false(self, monkeypatch):
        """DEV: sys.frozen=False behaves identically to absent."""
        from runtime.orchestrator.executors import _normalize_path

        monkeypatch.setattr("sys.frozen", False, raising=False)
        monkeypatch.setenv("PATH", "/usr/bin:/bin")

        _normalize_path()

        pathenv = os.environ["PATH"]
        entries = pathenv.split(":")
        assert "/opt/homebrew/bin" in entries
        assert "/usr/local/bin" in entries
        assert "happyranch-daemon" not in pathenv


def test_resolve_binary_absolute_path_blocked():
    """An absolute filesystem path is NOT resolved — registration-only
    resolution (THR-107 seq155 hard no-PATH cutover). Only executor/profile
    names that map to explicit executors.json entries are accepted."""
    from runtime.orchestrator.executors import _resolve_binary, ExecutorBinaryBlocked

    with pytest.raises(ExecutorBinaryBlocked) as exc_info:
        _resolve_binary("/usr/local/bin/claude")
    assert "not registered" in str(exc_info.value).lower()


def test_resolve_binary_bare_name_via_which(tmp_path, monkeypatch):
    """A bare name that is NOT registered raises ExecutorBinaryBlocked
    even when the binary exists on PATH (THR-107 seq155: no PATH discovery)."""
    from runtime.orchestrator.executors import _resolve_binary, ExecutorBinaryBlocked

    # Use an isolated daemon home so the auto-use fixture's registrations
    # don't pre-seed this test's registry.
    daemon_home = tmp_path / "isolated_home"
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home))

    # Place a fake 'claude' binary in a tmp dir and add it to PATH.
    fake_bin = tmp_path / "path_bin"
    fake_bin.mkdir()
    (fake_bin / "claude").touch(mode=0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:/usr/bin:/bin")

    with pytest.raises(ExecutorBinaryBlocked) as exc_info:
        _resolve_binary("claude")
    msg = str(exc_info.value)
    assert "claude" in msg
    assert "not registered" in msg.lower()
    assert "register" in msg.lower()


def test_resolve_binary_bare_name_stripped_path_still_finds_binary(monkeypatch, tmp_path):
    """When the inherited PATH is stripped, an unregistered bare name still
    raises ExecutorBinaryBlocked (THR-107 seq155: only registered binaries
    launch, regardless of PATH)."""
    from runtime.orchestrator.executors import _resolve_binary, _normalize_path, ExecutorBinaryBlocked

    # Use an isolated daemon home so the auto-use fixture's registrations
    # don't pre-seed this test's registry.
    daemon_home = tmp_path / "isolated_home"
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home))

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

        with pytest.raises(ExecutorBinaryBlocked) as exc_info:
            _resolve_binary("claude")
        msg = str(exc_info.value)
        assert "claude" in msg
        assert "not registered" in msg.lower()

        with pytest.raises(ExecutorBinaryBlocked) as exc_info2:
            _resolve_binary("codex")
        assert "codex" in str(exc_info2.value)
    finally:
        ex_mod._STANDARD_TOOL_DIRS = original


def test_resolve_binary_unresolvable_raises_actionable_diagnostic():
    """An unresolvable binary raises an error that names WHICH executor and
    provides the actionable registration command (THR-107 seq155)."""
    from runtime.orchestrator.executors import _resolve_binary, ExecutorBinaryBlocked

    with pytest.raises(ExecutorBinaryBlocked) as exc_info:
        _resolve_binary("nonexistent-cli-tool-xyz")
    error_msg = str(exc_info.value)

    assert "nonexistent-cli-tool-xyz" in error_msg
    # Must mention actionable guidance — the error tells the operator what to do.
    assert "not registered" in error_msg.lower()
    assert "register" in error_msg.lower()


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
def test_absolute_cli_path_resolved_from_registry(mock_subprocess, tmp_path, runtime):
    """When claude_cli_path is an absolute path (founder-configured), cmd[0]
    still comes from the machine-local binary registry keyed by the built-in
    name 'claude' — NOT from the Settings path (THR-107 seq155)."""
    from runtime.orchestrator.executor_binary_registry import set_binary
    # Register the built-in name with a real executable at a known path
    # different from the Settings value.
    fake_bin = tmp_path / "registered" / "claude"
    fake_bin.parent.mkdir(parents=True, exist_ok=True)
    fake_bin.touch(mode=0o755)
    set_binary("claude", str(fake_bin))
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
    # cmd[0] comes from the registry, NOT the Settings cli_path
    assert cmd[0] == str(fake_bin)


# ---------------------------------------------------------------------------
# Per-agent model selection — model_arg injection
# ---------------------------------------------------------------------------


@patch("runtime.orchestrator.executors.subprocess")
def test_claude_model_injected_when_model_set(mock_subprocess, tmp_path, runtime):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()
    mock_subprocess.Popen.return_value = _popen_mock(stdout="ok")

    executor = ClaudeExecutor(
        claude_cli_path="/opt/homebrew/bin/claude",
        permission_mode="auto",
        settings=Settings(),
        paths=runtime,
        model_arg=["--model", "{model}"],
    )
    executor.run(workspace=workspace, prompt="hello", model="claude-sonnet-5")

    cmd = mock_subprocess.Popen.call_args[0][0]
    # Model args should appear after binary, before -p
    assert cmd[1] == "--model"
    assert cmd[2] == "claude-sonnet-5"
    assert cmd[3] == "-p"  # prompt flag still there


@patch("runtime.orchestrator.executors.subprocess")
def test_claude_model_omitted_when_model_none(mock_subprocess, tmp_path, runtime):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()
    mock_subprocess.Popen.return_value = _popen_mock(stdout="ok")

    executor = ClaudeExecutor(
        claude_cli_path="/opt/homebrew/bin/claude",
        permission_mode="auto",
        settings=Settings(),
        paths=runtime,
        model_arg=["--model", "{model}"],
    )
    executor.run(workspace=workspace, prompt="hello", model=None)

    cmd = mock_subprocess.Popen.call_args[0][0]
    # No model args — cmd[1] should be -p
    assert cmd[1] == "-p"
    assert "--model" not in cmd


@patch("runtime.orchestrator.executors.subprocess")
def test_claude_model_omitted_when_model_arg_is_none(mock_subprocess, tmp_path, runtime):
    """When profile has no model_arg, model= is a no-op (CLI default)."""
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()
    mock_subprocess.Popen.return_value = _popen_mock(stdout="ok")

    executor = ClaudeExecutor(
        claude_cli_path="/opt/homebrew/bin/claude",
        permission_mode="auto",
        settings=Settings(),
        paths=runtime,
        model_arg=None,  # frozen profile default
    )
    executor.run(workspace=workspace, prompt="hello", model="gpt-5")

    cmd = mock_subprocess.Popen.call_args[0][0]
    assert cmd[1] == "-p"  # no model args injected
    assert "--model" not in cmd


@patch("runtime.orchestrator.executors.subprocess")
def test_codex_model_injected_with_m_flag(mock_subprocess, tmp_path):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()
    mock_subprocess.Popen.return_value = _popen_mock(stdout="ok")

    executor = CodexExecutor(
        codex_cli_path="/usr/local/bin/codex",
        sandbox_mode="workspace-write",
        model_arg=["-m", "{model}"],
    )
    executor.run(workspace=workspace, prompt="hello", model="o3")

    cmd = mock_subprocess.Popen.call_args[0][0]
    # Model args after binary+subcommand (exec), before sandbox
    assert cmd[2] == "-m"
    assert cmd[3] == "o3"
    assert cmd[4] == "--sandbox"  # sandbox flag still there


@patch("runtime.orchestrator.executors.subprocess")
def test_codex_model_omitted_when_unset(mock_subprocess, tmp_path):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()
    mock_subprocess.Popen.return_value = _popen_mock(stdout="ok")

    executor = CodexExecutor(
        codex_cli_path="/usr/local/bin/codex",
        sandbox_mode="workspace-write",
        model_arg=["-m", "{model}"],
    )
    executor.run(workspace=workspace, prompt="hello", model=None)

    cmd = mock_subprocess.Popen.call_args[0][0]
    # argv identical to today — model args absent
    assert "-m" not in [e for e in cmd if e == "-m"]


@patch("runtime.orchestrator.executors.subprocess")
def test_opencode_model_injected_with_m_flag(mock_subprocess, tmp_path):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()
    mock_subprocess.Popen.return_value = _popen_mock(stdout="ok")

    executor = OpencodeExecutor(
        opencode_cli_path="/usr/local/bin/opencode",
        model_arg=["-m", "{model}"],
    )
    executor.run(workspace=workspace, prompt="hello", model="openai/gpt-5")

    cmd = mock_subprocess.Popen.call_args[0][0]
    assert cmd[2] == "-m"
    assert cmd[3] == "openai/gpt-5"
    assert cmd[4] == "--dir"  # workspace flag still there


@patch("runtime.orchestrator.executors.subprocess")
def test_pi_model_injected_with_model_flag(mock_subprocess, tmp_path):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()
    mock_subprocess.Popen.return_value = _popen_mock(stdout="ok")

    executor = PiExecutor(
        pi_cli_path="/usr/local/bin/pi",
        model_arg=["--model", "{model}"],
    )
    executor.run(workspace=workspace, prompt="hello", model="gemini-pro")

    cmd = mock_subprocess.Popen.call_args[0][0]
    assert cmd[1] == "--model"
    assert cmd[2] == "gemini-pro"
    assert cmd[3] == "-p"  # prompt flag still there


@patch("runtime.orchestrator.executors.subprocess")
def test_pi_model_omitted_when_unset(mock_subprocess, tmp_path):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()
    mock_subprocess.Popen.return_value = _popen_mock(stdout="ok")

    executor = PiExecutor(
        pi_cli_path="/usr/local/bin/pi",
        model_arg=["--model", "{model}"],
    )
    executor.run(workspace=workspace, prompt="hello", model=None)

    cmd = mock_subprocess.Popen.call_args[0][0]
    # argv identical to today
    assert cmd[1] == "-p"  # no model args before -p
    assert "--model" not in cmd


# ── THR-116: _parse_claude_terminal_error unit tests ──────────────────


def test_parse_claude_terminal_error_session_limit():
    """type:result + error_during_execution + is_error:true with session-limit
    content → session_limit (valid documented terminal envelope)."""
    from runtime.orchestrator.executors import _parse_claude_terminal_error

    stdout = '{"type":"result","subtype":"error_during_execution","is_error":true,"result":"Session limit reached"}'
    reason = _parse_claude_terminal_error(stdout, "Workspace trust warning\n")
    assert reason == "session_limit"


def test_parse_claude_terminal_error_session_limit_from_result_field():
    """type:result without error_* subtype → None (not a terminal error).
    Only terminal error events (subtype starts with error_) are parsed."""
    from runtime.orchestrator.executors import _parse_claude_terminal_error

    stdout = '{"type":"result","result":"Error: session limit exceeded"}'
    reason = _parse_claude_terminal_error(stdout, "")
    assert reason is None


def test_parse_claude_terminal_error_certificate_from_result_field():
    """type:result without error_* subtype → None (not a terminal error).
    Certificate text in a non-terminal result field must not be parsed."""
    from runtime.orchestrator.executors import _parse_claude_terminal_error

    stdout = '{"type":"result","result":"UNKNOWN_CERTIFICATE_VERIFICATION_ERROR: unable to verify"}'
    reason = _parse_claude_terminal_error(stdout, "unrelated warning\n")
    assert reason is None


def test_parse_claude_terminal_error_generic_error_subtype_without_is_error():
    """type:result + error_during_execution WITHOUT is_error:true → None
    (missing the required terminal marker)."""
    from runtime.orchestrator.executors import _parse_claude_terminal_error

    stdout = '{"type":"result","subtype":"error_during_execution","result":"Tool error"}'
    reason = _parse_claude_terminal_error(stdout, "")
    assert reason is None


def test_parse_claude_terminal_error_success_ignored():
    """Type=result with success subtype → None (not an error)."""
    from runtime.orchestrator.executors import _parse_claude_terminal_error

    stdout = '{"type":"result","subtype":"success","result":"ok"}'
    reason = _parse_claude_terminal_error(stdout, "")
    assert reason is None


def test_parse_claude_terminal_error_empty_stdout_returns_none():
    """Empty stdout → None (fall back to existing error)."""
    from runtime.orchestrator.executors import _parse_claude_terminal_error

    reason = _parse_claude_terminal_error("", "some stderr noise")
    assert reason is None


def test_parse_claude_terminal_error_invalid_json_returns_none():
    """Non-JSON stdout → None."""
    from runtime.orchestrator.executors import _parse_claude_terminal_error

    reason = _parse_claude_terminal_error("not json at all", "stderr")
    assert reason is None


def test_parse_claude_terminal_error_error_object_pattern():
    """Arbitrary {error: {message: ...}} without type:result → None.
    Generic error objects are NOT terminal failure envelopes."""
    from runtime.orchestrator.executors import _parse_claude_terminal_error

    stdout = '{"error":{"message":"session limit hit"}}'
    reason = _parse_claude_terminal_error(stdout, "")
    assert reason is None


def test_parse_claude_terminal_error_errors_array_pattern():
    """Arbitrary {errors: [{message: ...}]} without type:result → None.
    Generic errors arrays are NOT terminal failure envelopes."""
    from runtime.orchestrator.executors import _parse_claude_terminal_error

    stdout = '{"errors":[{"message":"certificate verification failed"}]}'
    reason = _parse_claude_terminal_error(stdout, "")
    assert reason is None


def test_parse_claude_terminal_error_not_a_dict_returns_none():
    """JSON that parses as a non-dict → None."""
    from runtime.orchestrator.executors import _parse_claude_terminal_error

    reason = _parse_claude_terminal_error("[1, 2, 3]", "")
    assert reason is None


def test_parse_claude_terminal_error_success_subtype_with_certificate_text():
    """type:result + subtype:success with certificate result text → None.
    This is the exact false-precedence case from TASK-3438 review:
    {type: result, subtype: success, result: 'certificate verification failed'}."""
    from runtime.orchestrator.executors import _parse_claude_terminal_error

    stdout = '{"type":"result","subtype":"success","result":"certificate verification failed"}'
    reason = _parse_claude_terminal_error(stdout, "")
    assert reason is None


def test_parse_claude_terminal_error_progress_type_with_error_object():
    """type:progress with error object → None.
    This is the exact false-precedence case from TASK-3438 review:
    {type: progress, error: {message: 'session limit hit'}}."""
    from runtime.orchestrator.executors import _parse_claude_terminal_error

    stdout = '{"type":"progress","error":{"message":"session limit hit"}}'
    reason = _parse_claude_terminal_error(stdout, "")
    assert reason is None


def test_parse_claude_terminal_error_certificate_via_error_subtype_result():
    """type:result + subtype:error_during_execution + is_error:true with certificate
    result text → transport_error (valid terminal error envelope)."""
    from runtime.orchestrator.executors import _parse_claude_terminal_error

    stdout = '{"type":"result","subtype":"error_during_execution","is_error":true,"result":"UNKNOWN_CERTIFICATE_VERIFICATION_ERROR: unable to verify"}'
    reason = _parse_claude_terminal_error(stdout, "unrelated workspace trust warning\n")
    assert reason == "transport_error: UNKNOWN_CERTIFICATE_VERIFICATION_ERROR"


def test_parse_claude_terminal_error_certificate_via_error_subtype_errors():
    """type:result + subtype:error_during_execution + is_error:true with errors array
    containing certificate → transport_error (valid terminal error)."""
    from runtime.orchestrator.executors import _parse_claude_terminal_error

    stdout = '{"type":"result","subtype":"error_during_execution","is_error":true,"errors":[{"message":"UNKNOWN_CERTIFICATE_VERIFICATION_ERROR"}]}'
    reason = _parse_claude_terminal_error(stdout, "")
    assert reason == "transport_error: UNKNOWN_CERTIFICATE_VERIFICATION_ERROR"


def test_parse_claude_terminal_error_session_limit_via_error_subtype_result():
    """type:result + subtype:error_during_execution + is_error:true with session-limit
    result text → session_limit (valid terminal error envelope)."""
    from runtime.orchestrator.executors import _parse_claude_terminal_error

    stdout = '{"type":"result","subtype":"error_during_execution","is_error":true,"result":"Error: session limit exceeded"}'
    reason = _parse_claude_terminal_error(stdout, "")
    assert reason == "session_limit"


def test_parse_claude_terminal_error_unknown_error_subtype_returns_none():
    """type:result + error_unknown + is_error:true with no recognised error
    signal → None (no fabricated claude_<suffix> reason)."""
    from runtime.orchestrator.executors import _parse_claude_terminal_error

    stdout = '{"type":"result","subtype":"error_unknown","is_error":true,"result":"something went wrong"}'
    reason = _parse_claude_terminal_error(stdout, "")
    assert reason is None


def test_parse_claude_terminal_error_lookalike_subtype_returns_none():
    """type:result + error_lookalike + is_error:true with no recognised error
    signal → None (no fabricated claude_<suffix> reason)."""
    from runtime.orchestrator.executors import _parse_claude_terminal_error

    stdout = '{"type":"result","subtype":"error_lookalike","is_error":true,"result":"irrelevant"}'
    reason = _parse_claude_terminal_error(stdout, "")
    assert reason is None


def test_parse_claude_terminal_error_lookalike_without_is_error_returns_none():
    """type:result + error_lookalike WITHOUT is_error:true → None
    (missing the required terminal marker)."""
    from runtime.orchestrator.executors import _parse_claude_terminal_error

    stdout = '{"type":"result","subtype":"error_lookalike","result":"irrelevant"}'
    reason = _parse_claude_terminal_error(stdout, "")
    assert reason is None


# ── THR-116 adversarial subtype tests (formerly faulty branch) ────────
# These tests prove that unsupported error_* subtypes return None even
# when they carry recognised error signal text.  Under the earlier
# startswith("error_") check, both would have been INCORRECTLY classified.


def test_parse_claude_terminal_error_lookalike_session_limit_text():
    """error_lookalike + session-limit wording → None.
    This enters the formerly faulty classification condition: the subtype
    begins "error_" but is NOT the documented error_during_execution, and
    the result carries session-limit text that the text inspection would
    match.  With the stricter subtype check, the parser short-circuits
    before reaching text inspection.  (MEM-380 / MEM-377 coverage)."""
    from runtime.orchestrator.executors import _parse_claude_terminal_error

    stdout = (
        '{"type":"result","subtype":"error_lookalike","is_error":true,'
        '"result":"Session limit reached"}'
    )
    reason = _parse_claude_terminal_error(stdout, "")
    assert reason is None, (
        f"error_lookalike must return None even with session-limit text; got {reason!r}"
    )


def test_parse_claude_terminal_error_unknown_certificate_text():
    """error_unknown + certificate wording → None.
    This enters the formerly faulty classification condition: the subtype
    begins "error_" but is NOT the documented error_during_execution, and
    the result carries certificate text that the text inspection would
    match.  With the stricter subtype check, the parser short-circuits
    before reaching text inspection.  (MEM-380 / MEM-377 coverage)."""
    from runtime.orchestrator.executors import _parse_claude_terminal_error

    stdout = (
        '{"type":"result","subtype":"error_unknown","is_error":true,'
        '"result":"UNKNOWN_CERTIFICATE_VERIFICATION_ERROR: unable to verify"}'
    )
    reason = _parse_claude_terminal_error(stdout, "")
    assert reason is None, (
        f"error_unknown must return None even with certificate text; got {reason!r}"
    )


# ── THR-116: _run_command error_parser integration ────────────────────


@patch("runtime.orchestrator.executors.subprocess")
def test_run_command_populates_terminal_error_on_nonzero_with_parser(mock_subprocess, tmp_path):
    """When the process exits non-zero and an error_parser returns a
    classified reason, terminal_error is set on ExecutorResult."""
    from runtime.orchestrator.executors import _run_command

    workspace = tmp_path / "ws"
    workspace.mkdir()

    mock_subprocess.Popen.return_value = _popen_mock(
        returncode=1,
        stdout='{"type":"result","subtype":"error_during_execution"}',
        stderr="Workspace trust warning\n",
    )

    def _parser(stdout, stderr):
        import json
        try:
            obj = json.loads(stdout.strip())
            if obj.get("type") == "result" and isinstance(obj.get("subtype"), str):
                if obj["subtype"] == "error_during_execution":
                    return "session_limit"
        except Exception:
            pass
        return None

    result = _run_command(
        ["claude", "-p", "hello"],
        workspace,
        session_id="sess-test",
        timeout_seconds=30,
        provider="claude",
        error_parser=_parser,
    )

    assert result.success is False
    assert result.terminal_error == "session_limit"
    # The raw error still carries the stderr-based summary.
    assert "Command exited with code 1" in result.error
    assert "Workspace trust warning" in result.error


@patch("runtime.orchestrator.executors.subprocess")
def test_run_command_no_terminal_error_when_parser_returns_none(mock_subprocess, tmp_path):
    """When error_parser returns None, terminal_error is None."""
    from runtime.orchestrator.executors import _run_command

    workspace = tmp_path / "ws"
    workspace.mkdir()

    mock_subprocess.Popen.return_value = _popen_mock(
        returncode=2,
        stdout="not json",
        stderr="fatal: something broke\n",
    )

    def _parser(stdout, stderr):
        # No structured result → return None
        return None

    result = _run_command(
        ["codex", "exec"],
        workspace,
        session_id="sess-test",
        timeout_seconds=30,
        provider="codex",
        error_parser=_parser,
    )

    assert result.success is False
    assert result.terminal_error is None
    assert "Command exited with code 2" in result.error


@patch("runtime.orchestrator.executors.subprocess")
def test_run_command_no_error_parser_terminal_error_is_none(mock_subprocess, tmp_path):
    """When no error_parser is provided, terminal_error stays None."""
    from runtime.orchestrator.executors import _run_command

    workspace = tmp_path / "ws"
    workspace.mkdir()

    mock_subprocess.Popen.return_value = _popen_mock(
        returncode=1,
        stdout="some output",
        stderr="some error\n",
    )

    result = _run_command(
        ["opencode", "run", "hello"],
        workspace,
        session_id="sess-test",
        timeout_seconds=30,
        provider="opencode",
        # no error_parser
    )

    assert result.success is False
    assert result.terminal_error is None


@patch("runtime.orchestrator.executors.subprocess")
def test_run_command_terminal_error_not_set_on_success(mock_subprocess, tmp_path):
    """On successful execution, terminal_error stays None even with a parser."""
    from runtime.orchestrator.executors import _run_command

    workspace = tmp_path / "ws"
    workspace.mkdir()

    mock_subprocess.Popen.return_value = _popen_mock(
        returncode=0,
        stdout='{"type":"result","subtype":"success"}',
        stderr="",
    )

    def _parser(stdout, stderr):
        return "session_limit"  # would be returned, but not consulted on success

    result = _run_command(
        ["claude", "-p", "hello"],
        workspace,
        session_id="sess-test",
        timeout_seconds=30,
        provider="claude",
        error_parser=_parser,
    )

    assert result.success is True
    assert result.terminal_error is None


# ── THR-116 repair: real shipping-chain tests ─────────────────────────
# These go through the real executor (ClaudeExecutor.run) with the
# production _parse_claude_terminal_error parser and mocked subprocess,
# rather than lookalike result classes with prepopulated terminal_error.


@patch("runtime.orchestrator.executors.subprocess")
def test_claude_executor_session_limit_terminal_error(mock_subprocess, tmp_path, runtime):
    """Real-chain: mocked subprocess with session-limit terminal result +
    unrelated workspace-trust stderr → ClaudeExecutor.run() →
    production _parse_claude_terminal_error → ExecutorResult.terminal_error."""
    from runtime.config import Settings
    from runtime.orchestrator.executors import ClaudeExecutor

    workspace = tmp_path / "dev_agent"
    workspace.mkdir()

    mock_subprocess.Popen.return_value = _popen_mock(
        returncode=1,
        stdout='{"type":"result","subtype":"error_during_execution","is_error":true,"result":"Session limit reached"}',
        stderr=(
            "Running as unit: happyranch-session-THR-220-67724507.scope; "
            "invocation ID: abc123\n"
            "Ignoring 1 permissions.allow entry from .claude/settings.json: "
            "this workspace has not been trusted.\n"
        ),
    )

    executor = ClaudeExecutor(
        claude_cli_path="claude", permission_mode="auto",
        settings=Settings(), paths=runtime,
    )
    result = executor.run(
        workspace=workspace, prompt="hello",
        timeout_seconds=30, session_id="sess-test",
    )

    assert result.success is False
    assert result.terminal_error == "session_limit"
    assert "Session limit reached" in result.error
    assert "this workspace has not been trusted" in result.stderr_tail
    assert result.returncode == 1


@patch("runtime.orchestrator.executors.subprocess")
def test_claude_executor_real_session_limit_result_is_terminal_not_retryable(
    mock_subprocess, tmp_path, runtime,
):
    from runtime.config import Settings
    from runtime.orchestrator.executors import ClaudeExecutor

    workspace = tmp_path / "dev_agent"
    workspace.mkdir()
    notice = "You've hit your session limit · resets 7:10pm (Asia/Shanghai)"
    mock_subprocess.Popen.return_value = _popen_mock(
        returncode=1,
        stdout=json.dumps({
            "type": "result", "subtype": "error_during_execution",
            "is_error": True, "result": notice,
        }, ensure_ascii=False),
        stderr=(
            "Running as unit: happyranch-session-THR-220-67724507.scope; "
            "invocation ID: abc123\n"
            "Ignoring 1 permissions.allow entry from .claude/settings.json: "
            "this workspace has not been trusted.\n"
        ),
    )

    result = ClaudeExecutor(
        claude_cli_path="claude", permission_mode="auto",
        settings=Settings(), paths=runtime,
    ).run(
        workspace=workspace, prompt="hello", timeout_seconds=30,
        session_id="sess-test",
    )

    assert notice in result.stdout_tail
    assert "this workspace has not been trusted" in result.stderr_tail
    assert notice in result.error
    assert "happyranch-session-THR-220-a.scope" not in result.error
    assert result.terminal_error == "session_limit"
    assert result.rate_limited is False


@pytest.mark.parametrize("meaningful", [
    "fatal: this workspace has not been trusted by the remote API",
    "prefix Running as unit: happyranch-session-x.scope; invocation ID: abc123",
    "Ignoring permissions.allow entry failed while parsing settings",
])
@patch("runtime.orchestrator.executors.subprocess")
def test_claude_error_summary_keeps_benign_word_lookalikes(
    mock_subprocess, meaningful, tmp_path, runtime,
):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()
    mock_subprocess.Popen.return_value = _popen_mock(
        returncode=1,
        stdout='{"type":"result","subtype":"error_during_execution"}',
        stderr=(
            "Running as unit: happyranch-session-THR-220-a.scope; "
            f"invocation ID: abc123\n{meaningful}\n"
        ),
    )

    result = ClaudeExecutor(
        claude_cli_path="claude", permission_mode="auto",
        settings=Settings(), paths=runtime,
    ).run(workspace=workspace, prompt="hello", timeout_seconds=30)

    assert meaningful in result.error
    assert "happyranch-session-THR-220-a.scope" not in result.error


@patch("runtime.orchestrator.executors.subprocess")
def test_claude_executor_certificate_terminal_error(mock_subprocess, tmp_path, runtime):
    """Real-chain: mocked subprocess with certificate transport error +
    unrelated workspace-trust stderr → ClaudeExecutor.run() →
    production _parse_claude_terminal_error →
    ExecutorResult.terminal_error == transport_error."""
    from runtime.config import Settings
    from runtime.orchestrator.executors import ClaudeExecutor

    workspace = tmp_path / "dev_agent"
    workspace.mkdir()

    mock_subprocess.Popen.return_value = _popen_mock(
        returncode=1,
        stdout='{"type":"result","subtype":"error_during_execution","is_error":true,'
               '"result":"UNKNOWN_CERTIFICATE_VERIFICATION_ERROR: unable to verify"}',
        stderr="Workspace trust warning: untrusted directory\n",
    )

    executor = ClaudeExecutor(
        claude_cli_path="claude", permission_mode="auto",
        settings=Settings(), paths=runtime,
    )
    result = executor.run(
        workspace=workspace, prompt="hello",
        timeout_seconds=30, session_id="sess-test",
    )

    assert result.success is False
    assert result.terminal_error == "transport_error: UNKNOWN_CERTIFICATE_VERIFICATION_ERROR"
    assert "Workspace trust warning" in result.error
    assert result.returncode == 1


@patch("runtime.orchestrator.executors.subprocess")
def test_claude_executor_no_structured_result_falls_back_to_error(
    mock_subprocess, tmp_path, runtime,
):
    """Real-chain: mocked subprocess with no structured terminal result
    → terminal_error is None, raw error is preserved."""
    from runtime.config import Settings
    from runtime.orchestrator.executors import ClaudeExecutor

    workspace = tmp_path / "dev_agent"
    workspace.mkdir()

    mock_subprocess.Popen.return_value = _popen_mock(
        returncode=2,
        stdout="not json at all",
        stderr="fatal: something broke\n",
    )

    executor = ClaudeExecutor(
        claude_cli_path="claude", permission_mode="auto",
        settings=Settings(), paths=runtime,
    )
    result = executor.run(
        workspace=workspace, prompt="hello",
        timeout_seconds=30, session_id="sess-test",
    )

    assert result.success is False
    assert result.terminal_error is None
    assert "Command exited with code 2" in result.error
    assert "fatal: something broke" in result.error


@patch("runtime.orchestrator.executors.subprocess")
def test_claude_executor_success_subtype_does_not_produce_terminal_error(
    mock_subprocess, tmp_path, runtime,
):
    """Real-chain: mocked subprocess with {type:result, subtype:success,
    result: 'certificate verification failed'} — must NOT produce a
    terminal_error (the reviewer's false-precedence bug fix)."""
    from runtime.config import Settings
    from runtime.orchestrator.executors import ClaudeExecutor

    workspace = tmp_path / "dev_agent"
    workspace.mkdir()

    mock_subprocess.Popen.return_value = _popen_mock(
        returncode=1,
        stdout='{"type":"result","subtype":"success",'
               '"result":"certificate verification failed"}',
        stderr="Workspace trust warning\n",
    )

    executor = ClaudeExecutor(
        claude_cli_path="claude", permission_mode="auto",
        settings=Settings(), paths=runtime,
    )
    result = executor.run(
        workspace=workspace, prompt="hello",
        timeout_seconds=30, session_id="sess-test",
    )

    # The process exited non-zero, but subtype:success means no structured
    # terminal failure — terminal_error must be None.
    assert result.success is False
    assert result.terminal_error is None


@patch("runtime.orchestrator.executors.subprocess")
def test_claude_executor_error_lookalike_returns_none(
    mock_subprocess, tmp_path, runtime,
):
    """Real-chain: mocked subprocess with {type:result, subtype:error_lookalike,
    is_error:true, result: irrelevant} + unrelated stderr →
    terminal_error is None (unsupported error subtype, no synthetic reason)."""
    from runtime.config import Settings
    from runtime.orchestrator.executors import ClaudeExecutor

    workspace = tmp_path / "dev_agent"
    workspace.mkdir()

    mock_subprocess.Popen.return_value = _popen_mock(
        returncode=1,
        stdout='{"type":"result","subtype":"error_lookalike","is_error":true,'
               '"result":"some irrelevant message"}',
        stderr="Workspace trust warning: untrusted directory\n",
    )

    executor = ClaudeExecutor(
        claude_cli_path="claude", permission_mode="auto",
        settings=Settings(), paths=runtime,
    )
    result = executor.run(
        workspace=workspace, prompt="hello",
        timeout_seconds=30, session_id="sess-test",
    )

    assert result.success is False
    assert result.terminal_error is None
    assert "Workspace trust warning" in result.error


@patch("runtime.orchestrator.executors.subprocess")
def test_claude_executor_error_unknown_returns_none(
    mock_subprocess, tmp_path, runtime,
):
    """Real-chain: mocked subprocess with {type:result, subtype:error_unknown,
    is_error:true} + unrelated stderr →
    terminal_error is None (unsupported error subtype, no synthetic reason)."""
    from runtime.config import Settings
    from runtime.orchestrator.executors import ClaudeExecutor

    workspace = tmp_path / "dev_agent"
    workspace.mkdir()

    mock_subprocess.Popen.return_value = _popen_mock(
        returncode=2,
        stdout='{"type":"result","subtype":"error_unknown","is_error":true,'
               '"result":"something went wrong"}',
        stderr="fatal: something broke\n",
    )

    executor = ClaudeExecutor(
        claude_cli_path="claude", permission_mode="auto",
        settings=Settings(), paths=runtime,
    )
    result = executor.run(
        workspace=workspace, prompt="hello",
        timeout_seconds=30, session_id="sess-test",
    )

    assert result.success is False
    assert result.terminal_error is None
    assert "Command exited with code 2" in result.error


# ── THR-116 adversarial real-chain executor tests (formerly faulty) ───
# These tests prove that unsupported error_* subtypes with recognised signal
# text do NOT produce a terminal_error through the real ClaudeExecutor →
# _run_command → production _parse_claude_terminal_error chain.


@patch("runtime.orchestrator.executors.subprocess")
def test_claude_executor_error_lookalike_session_limit_text_raw_fallback(
    mock_subprocess, tmp_path, runtime,
):
    """Real-chain adversarial: error_lookalike + session-limit wording +
    unrelated stderr → terminal_error is None, raw error preserved.

    This enters the formerly faulty classification condition: under the
    earlier startswith("error_") check the parser would have classified
    the session-limit text.  With the strict subtype == error_during_execution
    check, the parser returns None and the raw stderr-first fallback wins.
    (MEM-380 coverage)."""
    from runtime.config import Settings
    from runtime.orchestrator.executors import ClaudeExecutor

    workspace = tmp_path / "dev_agent"
    workspace.mkdir()

    mock_subprocess.Popen.return_value = _popen_mock(
        returncode=1,
        stdout='{"type":"result","subtype":"error_lookalike","is_error":true,'
               '"result":"Session limit reached"}',
        stderr="Workspace trust warning: untrusted directory\n",
    )

    executor = ClaudeExecutor(
        claude_cli_path="claude", permission_mode="auto",
        settings=Settings(), paths=runtime,
    )
    result = executor.run(
        workspace=workspace, prompt="hello",
        timeout_seconds=30, session_id="sess-test",
    )

    assert result.success is False
    assert result.terminal_error is None, (
        f"error_lookalike must not produce terminal_error; got {result.terminal_error!r}"
    )
    # Raw stderr-first fallback preserved.
    assert "Workspace trust warning" in result.error
    assert "Command exited with code 1" in result.error


@patch("runtime.orchestrator.executors.subprocess")
def test_claude_executor_error_unknown_certificate_text_raw_fallback(
    mock_subprocess, tmp_path, runtime,
):
    """Real-chain adversarial: error_unknown + certificate wording +
    unrelated stderr → terminal_error is None, raw error preserved.

    This enters the formerly faulty classification condition: under the
    earlier startswith("error_") check the parser would have classified
    the certificate text.  With the strict subtype == error_during_execution
    check, the parser returns None and the raw stderr-first fallback wins.
    (MEM-377 coverage)."""
    from runtime.config import Settings
    from runtime.orchestrator.executors import ClaudeExecutor

    workspace = tmp_path / "dev_agent"
    workspace.mkdir()

    mock_subprocess.Popen.return_value = _popen_mock(
        returncode=1,
        stdout='{"type":"result","subtype":"error_unknown","is_error":true,'
               '"result":"UNKNOWN_CERTIFICATE_VERIFICATION_ERROR: unable to verify"}',
        stderr="Workspace trust warning: untrusted directory\n",
    )

    executor = ClaudeExecutor(
        claude_cli_path="claude", permission_mode="auto",
        settings=Settings(), paths=runtime,
    )
    result = executor.run(
        workspace=workspace, prompt="hello",
        timeout_seconds=30, session_id="sess-test",
    )

    assert result.success is False
    assert result.terminal_error is None, (
        f"error_unknown must not produce terminal_error; got {result.terminal_error!r}"
    )
    # Raw stderr-first fallback preserved.
    assert "Workspace trust warning" in result.error
    assert "Command exited with code 1" in result.error


# ── _sanitize_child_env and _callee_env sanitization ──────────────────


class TestCalleeEnvSanitization:
    """_sanitize_child_env strips VIRTUAL_ENV, UV_PROJECT_ENVIRONMENT,
    UV_PYTHON, and UV_SYSTEM_PYTHON; _callee_env() preserves PATH and
    HAPPYRANCH_* runtime variables."""

    def test_sanitize_strips_all_dangerous_vars(self, monkeypatch):
        from runtime.orchestrator.executors import _sanitize_child_env

        env = {
            "PATH": "/usr/bin:/bin",
            "VIRTUAL_ENV": "/fake/canonical/.venv",
            "UV_PROJECT_ENVIRONMENT": "/fake/project",
            "UV_PYTHON": "/fake/shared/.venv/bin/python",
            "UV_SYSTEM_PYTHON": "1",
            "HAPPYRANCH_ORG_SLUG": "testorg",
            "HAPPYRANCH_DAEMON_HOME": "/tmp/hr",
            "HOME": "/home/user",
            # Unrelated UV_* configuration must NOT be stripped.
            "UV_NO_CACHE": "1",
            "UV_COMPILE_BYTECODE": "1",
        }
        cleaned = _sanitize_child_env(dict(env))

        # Dangerous variables are stripped.
        assert "VIRTUAL_ENV" not in cleaned
        assert "UV_PROJECT_ENVIRONMENT" not in cleaned
        assert "UV_PYTHON" not in cleaned
        assert "UV_SYSTEM_PYTHON" not in cleaned

        # Unrelated UV_* config is preserved.
        assert cleaned.get("UV_NO_CACHE") == "1"
        assert cleaned.get("UV_COMPILE_BYTECODE") == "1"

        # Required variables are preserved with exact values.
        assert cleaned["PATH"] == "/usr/bin:/bin"
        assert cleaned["HAPPYRANCH_ORG_SLUG"] == "testorg"
        assert cleaned["HAPPYRANCH_DAEMON_HOME"] == "/tmp/hr"
        assert cleaned["HOME"] == "/home/user"

    def test_sanitize_is_idempotent(self):
        from runtime.orchestrator.executors import _sanitize_child_env

        env = {"PATH": "/bin", "HOME": "/home/user"}
        once = _sanitize_child_env(dict(env))
        twice = _sanitize_child_env(dict(once))
        assert once == twice
        assert "VIRTUAL_ENV" not in twice
        assert "UV_PROJECT_ENVIRONMENT" not in twice
        assert "UV_PYTHON" not in twice
        assert "UV_SYSTEM_PYTHON" not in twice

    def test_callee_env_strips_venv_inherited_from_os_environ(self, monkeypatch):
        """_callee_env() removes all dangerous vars inherited from os.environ."""
        from runtime.orchestrator.executors import _callee_env

        monkeypatch.setenv("PATH", "/fake/bin")
        monkeypatch.setenv("VIRTUAL_ENV", "/fake/canonical/.venv")
        monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "/fake/project")
        monkeypatch.setenv("UV_PYTHON", "/fake/shared/.venv/bin/python")
        monkeypatch.setenv("UV_SYSTEM_PYTHON", "1")
        monkeypatch.setenv("HAPPYRANCH_ORG_SLUG", "testorg")
        monkeypatch.setenv("HOME", "/home/user")

        callee = _callee_env(org_slug="testorg")

        assert "VIRTUAL_ENV" not in callee
        assert "UV_PROJECT_ENVIRONMENT" not in callee
        assert "UV_PYTHON" not in callee
        assert "UV_SYSTEM_PYTHON" not in callee
        assert callee["HAPPYRANCH_ORG_SLUG"] == "testorg"
        assert callee["PATH"] == "/fake/bin"
        assert callee["HOME"] == "/home/user"

    def test_callee_env_without_org_slug_still_sanitizes(self, monkeypatch):
        """_callee_env() without org_slug still strips venv vars."""
        from runtime.orchestrator.executors import _callee_env

        monkeypatch.setenv("VIRTUAL_ENV", "/fake/shared/.venv")
        monkeypatch.setenv("PATH", "/usr/bin")
        # Clear HAPPYRANCH_ORG_SLUG if set in the real env.
        monkeypatch.delenv("HAPPYRANCH_ORG_SLUG", raising=False)

        callee = _callee_env()

        assert "VIRTUAL_ENV" not in callee
        assert "HAPPYRANCH_ORG_SLUG" not in callee
        assert callee["PATH"] == "/usr/bin"

    def test_sanitize_does_not_mutate_original(self):
        """_sanitize_child_env returns the same dict but callers pass a fresh
        copy — confirm we don't accidentally mutate the daemon's os.environ."""
        from runtime.orchestrator.executors import _sanitize_child_env

        original = {
            "VIRTUAL_ENV": "/fake/.venv",
            "UV_PROJECT_ENVIRONMENT": "/fake/proj",
            "UV_PYTHON": "/fake/shared/.venv/bin/python",
            "UV_SYSTEM_PYTHON": "1",
            "PATH": "/bin",
        }
        before = dict(original)
        cleaned = _sanitize_child_env(dict(original))
        # Original must be unmutated.
        assert original == before
        assert "VIRTUAL_ENV" in original
        assert "UV_PYTHON" in original
        # Cleaned must be stripped.
        assert "VIRTUAL_ENV" not in cleaned
        assert "UV_PYTHON" not in cleaned


# ── production Popen-launch env sanitization assertions ──────────────


@patch("runtime.orchestrator.executors.subprocess")
def test_normal_executor_popen_strips_adversarial_venv_from_env(
    mock_subprocess, monkeypatch, tmp_path, runtime,
):
    """Normal executor (Codex) Popen must strip all four inherited
    installation-target selector variables (VIRTUAL_ENV,
    UV_PROJECT_ENVIRONMENT, UV_PYTHON, UV_SYSTEM_PYTHON) from the
    env= dict while preserving exact PATH and HAPPYRANCH_* values."""
    from runtime.config import Settings
    from runtime.orchestrator.executors import CodexExecutor

    # Inject adversarial inherited environment — all four dangerous
    # installation-target steering variables.
    monkeypatch.setenv("VIRTUAL_ENV", "/fake/canonical/.venv")
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "/fake/project")
    monkeypatch.setenv("UV_PYTHON", "/fake/shared/.venv/bin/python")
    monkeypatch.setenv("UV_SYSTEM_PYTHON", "1")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("HAPPYRANCH_ORG_SLUG", "testorg")
    # THR-204 issue 3: use a per-test temp daemon home, never a fixed shared
    # /tmp path, so parallel runs cannot collide on the registry write surface.
    hr_home = tmp_path / "hr-home"
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(hr_home))

    # Register a fake codex binary so the executor resolves.
    from runtime.orchestrator.executor_binary_registry import set_binary
    fake_bin = tmp_path / "fake-codex"
    fake_bin.touch(mode=0o755)
    set_binary("codex", str(fake_bin))

    workspace = tmp_path / "dev_agent"
    workspace.mkdir()
    mock_subprocess.Popen.return_value = _popen_mock(stdout="ok")

    executor = CodexExecutor(codex_cli_path="codex", sandbox_mode="workspace-write")
    executor.run(workspace=workspace, prompt="x", timeout_seconds=30)

    popen_kwargs = mock_subprocess.Popen.call_args[1]
    assert "env" in popen_kwargs, "Popen must receive explicit env= dict"
    env_dict = popen_kwargs["env"]

    # All four dangerous installation-target selectors MUST be absent.
    for var in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "UV_PYTHON", "UV_SYSTEM_PYTHON"):
        assert var not in env_dict, (
            f"{var} must be stripped; got: {env_dict.get(var)!r}"
        )

    # Exact PATH and HAPPYRANCH_* values MUST be present.
    assert env_dict.get("PATH") == "/usr/bin:/bin", (
        f"PATH must be exactly /usr/bin:/bin; got: {env_dict.get('PATH')!r}"
    )
    assert env_dict.get("HAPPYRANCH_ORG_SLUG") == "testorg", (
        f"HAPPYRANCH_ORG_SLUG must be testorg; got: {env_dict.get('HAPPYRANCH_ORG_SLUG')!r}"
    )
    assert env_dict.get("HAPPYRANCH_DAEMON_HOME") == str(hr_home), (
        f"HAPPYRANCH_DAEMON_HOME must be {hr_home}; got: {env_dict.get('HAPPYRANCH_DAEMON_HOME')!r}"
    )


@patch("runtime.orchestrator.executors.subprocess")
def test_custom_adapter_popen_strips_adversarial_venv_from_env(
    mock_subprocess, monkeypatch, tmp_path,
):
    """Custom adapter executor Popen must strip all four inherited
    installation-target selector variables (VIRTUAL_ENV,
    UV_PROJECT_ENVIRONMENT, UV_PYTHON, UV_SYSTEM_PYTHON) from the
    env= dict.  The custom adapter launch calls _callee_env() which
    returns a sanitized env copy."""
    from runtime.orchestrator.adapter_store import compute_sha256
    from runtime.orchestrator.executors import CustomAdapterExecutor

    # Create a minimal adapter executable.
    adapter_exe = tmp_path / "adapter"
    adapter_exe.write_text(
        "#!/bin/bash\necho '{\"contract_version\":1,\"identity\":{\"adapter\":\"test\",\"version\":\"1.0.0\",\"contract_version\":1},\"response\":{\"session_id\":\"sess-test\",\"success\":true,\"returncode\":0,\"stdout\":\"ok\"}}'\n"
    )
    adapter_exe.chmod(0o755)
    exe_hash = compute_sha256(adapter_exe)

    # Inject all four adversarial installation-target selectors.
    monkeypatch.setenv("VIRTUAL_ENV", "/fake/canonical/.venv")
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "/fake/project")
    monkeypatch.setenv("UV_PYTHON", "/fake/shared/.venv/bin/python")
    monkeypatch.setenv("UV_SYSTEM_PYTHON", "1")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("HAPPYRANCH_ORG_SLUG", "testorg")

    mock_subprocess.Popen.return_value = _popen_mock(stdout=(
        '{"contract_version":1,'
        '"identity":{"adapter":"test","version":"1.0.0","contract_version":1},'
        '"response":{"session_id":"sess-test","success":true,"returncode":0,"stdout":"ok"}}'
    ))

    executor = CustomAdapterExecutor(
        profile_name="test",
        adapter_entry_id="test-adapter",
        adapter_executable=str(adapter_exe),
        adapter_hash=exe_hash,
        adapter_version="1.0.0",
        adapter_contract_version=1,
        provider="test",
    )
    executor.set_invocation_context(
        agent="dev_agent", org="happyranch", invocation_kind="task"
    )
    executor.run(workspace=tmp_path, prompt="test", session_id="sess-test")

    popen_kwargs = mock_subprocess.Popen.call_args[1]
    assert "env" in popen_kwargs, "Custom adapter Popen must receive explicit env= dict"
    env_dict = popen_kwargs["env"]

    # All four dangerous installation-target selectors MUST be absent.
    for var in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "UV_PYTHON", "UV_SYSTEM_PYTHON"):
        assert var not in env_dict, (
            f"{var} must be stripped from custom adapter env; got: {env_dict.get(var)!r}"
        )

    # Exact PATH and HAPPYRANCH_ORG_SLUG value must be retained.
    assert env_dict.get("PATH") == "/usr/bin:/bin", (
        f"PATH must be exactly /usr/bin:/bin; got: {env_dict.get('PATH')!r}"
    )
    assert env_dict.get("HAPPYRANCH_ORG_SLUG") == "testorg", (
        f"HAPPYRANCH_ORG_SLUG must be testorg; got: {env_dict.get('HAPPYRANCH_ORG_SLUG')!r}"
    )
