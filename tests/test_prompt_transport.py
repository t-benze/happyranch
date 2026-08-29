"""THR-200 prompt-transport containment tests.

Covers:
- (A) the portable pre-spawn oversized-argv guard: deterministic normalized
  ``prompt_transport_too_large`` failure BEFORE Popen, no truncation, known
  smaller argv executions preserved, boundary at the platform-safe limit.
- (B) Claude/Pi prompt delivery via stdin (``input_text``): the prompt body
  never appears in argv; exact bytes travel through the subprocess stdin;
  output/model/permission/resume flags and parsers are unchanged; a
  >=512 KiB UTF-8 prompt launches via stdin while the same payload on an
  argv transport is rejected by the guard.
- Codex stays byte-identical (stdin ``-`` already); OpenCode and generic CLI
  remain argv-based and behaviorally unchanged except for the new guard.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from runtime.config import Settings
from runtime.orchestrator.executors import (
    ClaudeExecutor,
    OpencodeExecutor,
    PiExecutor,
    _LINUX_MAX_ARGV_ELEMENT_BYTES,
    _SESSION_LIFETIME_PREAMBLE,
    _max_argv_element_bytes,
    _prompt_transport_too_large_error,
    is_prompt_transport_too_large,
)
from runtime.orchestrator._paths import OrgPaths
from runtime.runtime import RuntimeDir

_EXECUTOR_NAMES = frozenset({"claude", "codex", "opencode", "pi"})


@pytest.fixture(autouse=True)
def _mock_binary_registry(monkeypatch, tmp_path):
    """Resolve built-in executor binaries deterministically (mirrors
    tests/test_executor.py)."""
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
    rt = RuntimeDir.init(tmp_path / "rt")
    return OrgPaths(root=rt.orgs_dir / "x")


def _popen_mock(returncode: int = 0, stdout: str = "", stderr: str = ""):
    proc = MagicMock()
    proc.pid = 4242
    proc.returncode = returncode
    proc.communicate.return_value = (stdout, stderr)
    return proc


def _big_prompt(min_bytes: int) -> str:
    """Deterministic UTF-8 prompt of AT LEAST ``min_bytes`` encoded bytes,
    including multi-byte characters and newlines (transport-exactness)."""
    unit = "ünïcödé 测试 line\n"
    unit_bytes = len(unit.encode("utf-8"))
    repeats = min_bytes // unit_bytes + 2
    out = unit * repeats
    while len(out.encode("utf-8")) < min_bytes:
        out += unit
    return out + "\nEND_MARKER\n"


def _exact_prompt_bytes(target_bytes: int) -> str:
    """UTF-8 prompt whose encoded size is exactly ``target_bytes`` (used for
    the at-the-limit boundary case).
    """
    unit = "abcdefghij\n"  # 11 ASCII bytes per unit
    suffix = "END_MARKER\n"
    remaining = target_bytes - len(suffix.encode("utf-8"))
    n, rem = divmod(remaining, len(unit))
    out = (unit * n) + ("a" * rem) + suffix
    assert len(out.encode("utf-8")) == target_bytes
    return out


def _huge_prompt_for_stdin() -> str:
    """>=512 KiB UTF-8 prompt — the payload class that E2BIG'd via argv."""
    return _big_prompt(530_000)


# ── (A) pre-spawn argv guard ────────────────────────────────────────────


@patch("runtime.orchestrator.executors.subprocess.Popen")
def test_argv_guard_rejects_oversized_prompt_before_spawn(mock_popen, tmp_path, runtime):
    """An argv transport (generic-CLI template) with a prompt over the
    platform-safe limit fails deterministically with the normalized category
    and Popen is never reached. (opencode is stdin-capable since TASK-6080
    and is no longer covered by the argv guard.)"""
    from runtime.orchestrator.executors import GenericCliExecutor

    workspace = tmp_path / "ws"
    workspace.mkdir()
    over = _big_prompt(_max_argv_element_bytes() + 1)

    ex = GenericCliExecutor(
        profile_name="test-cli",
        argv_template=["test-cli", "--prompt", "{prompt}"],
        provider="test-cli",
    )
    with patch(
        "runtime.orchestrator.executors._resolve_binary",
        return_value=str(tmp_path / "bin" / "test-cli"),
    ):
        result = ex.run(workspace=workspace, prompt=over, session_id="sess-x")

    assert result.success is False
    assert is_prompt_transport_too_large(result)
    assert "prompt_transport_too_large" in (result.error or "")
    # Never truncated — the diagnostic says so explicitly and the payload is
    # never re-shipped.
    assert "NOT truncated" in (result.error or "")
    mock_popen.assert_not_called()


@patch("runtime.orchestrator.executors.subprocess.Popen")
def test_argv_guard_boundary_at_limit_is_preserved(mock_popen, tmp_path, runtime):
    """A prompt exactly AT the platform-safe limit still launches (guard is
    strictly `>`): known smaller/equal argv executions are preserved. Uses
    generic-CLI — the remaining argv transport (opencode is stdin-capable
    since TASK-6080)."""
    from runtime.orchestrator.executors import GenericCliExecutor

    workspace = tmp_path / "ws"
    workspace.mkdir()
    # The executor prepends the session-lifetime preamble to the prompt, so
    # the FULL argv element (preamble + prompt) must sit exactly at the limit.
    element_limit = _max_argv_element_bytes()
    preamble_bytes = len(_SESSION_LIFETIME_PREAMBLE.encode("utf-8"))
    at_limit = _exact_prompt_bytes(element_limit - preamble_bytes)
    assert len((_SESSION_LIFETIME_PREAMBLE + at_limit).encode("utf-8")) == element_limit
    mock_popen.return_value = _popen_mock(stdout="ok")

    ex = GenericCliExecutor(
        profile_name="test-cli",
        argv_template=["test-cli", "--prompt", "{prompt}"],
        provider="test-cli",
    )
    with patch(
        "runtime.orchestrator.executors._resolve_binary",
        return_value=str(tmp_path / "bin" / "test-cli"),
    ):
        result = ex.run(workspace=workspace, prompt=at_limit, session_id="sess-x")

    assert result.success is True
    mock_popen.assert_called_once()


@patch("runtime.orchestrator.executors.subprocess.Popen")
def test_argv_guard_small_prompt_unaffected(mock_popen, tmp_path, runtime):
    """Small prompts behave exactly as before (opencode delivers via stdin
    since TASK-6080; small prompts travel through the pipe, not argv)."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    mock_popen.return_value = _popen_mock(stdout="ok")

    ex = OpencodeExecutor(opencode_cli_path="opencode")
    result = ex.run(workspace=workspace, prompt="hello opencode", session_id="sess-x")

    assert result.success is True
    cmd = mock_popen.call_args[0][0]
    assert cmd[0].endswith("opencode")
    assert not any("hello opencode" in el for el in cmd)
    sent = mock_popen.return_value.communicate.call_args.kwargs["input"]
    assert sent.endswith("hello opencode")


@patch("runtime.orchestrator.executors.subprocess.Popen")
def test_argv_guard_generic_cli_argv_template_covered(mock_popen, tmp_path, runtime):
    """Generic-CLI profiles that substitute {prompt} into argv are covered by
    the same pre-spawn guard (their transport is profile-defined)."""
    from runtime.orchestrator.executors import GenericCliExecutor

    workspace = tmp_path / "ws"
    workspace.mkdir()
    ex = GenericCliExecutor(
        profile_name="test-cli",
        argv_template=["test-cli", "--prompt", "{prompt}"],
        provider="test-cli",
    )
    over = _big_prompt(_max_argv_element_bytes() + 1)

    with patch(
        "runtime.orchestrator.executors._resolve_binary",
        return_value=str(tmp_path / "bin" / "test-cli"),
    ):
        result = ex.run(workspace=workspace, prompt=over, session_id="sess-x")

    assert result.success is False
    assert is_prompt_transport_too_large(result)
    mock_popen.assert_not_called()


def test_prompt_transport_too_large_is_transport_only():
    """The normalized error carries the bytes-are-transport-only doctrine:
    never a cost/reset policy, never a truncation."""
    err = _prompt_transport_too_large_error(131071)
    assert "transport" in err.lower()
    assert "NOT truncated" in err
    assert "cost" in err.lower() or "policy" in err.lower()


# ── (B) Claude/Pi stdin transport ───────────────────────────────────────


@patch("runtime.orchestrator.executors.subprocess.Popen")
def test_claude_prompt_via_stdin_not_argv(mock_popen, tmp_path, runtime):
    """Claude's prompt body moves to stdin; the argv keeps every
    output/model/permission/resume flag and parser contract."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    mock_popen.return_value = _popen_mock(
        stdout='{"type":"result","session_id":"claude-sess-1","result":"ok"}'
    )

    ex = ClaudeExecutor(
        claude_cli_path="claude", permission_mode="auto", settings=Settings(),
        paths=runtime, model_arg=["--model", "{model}"],
    )
    result = ex.run(
        workspace=workspace, prompt="hello claude", session_id="sess-x",
        model="claude-opus-4-1", resume_session_id="resume-abc",
    )

    assert result.success is True
    call = mock_popen.call_args
    cmd = call[0][0]
    # Flags preserved exactly; prompt element absent.
    assert cmd[0].endswith("claude")
    assert cmd[1] == "--model"
    assert cmd[2] == "claude-opus-4-1"
    assert cmd[3] == "-p"
    assert cmd[4] == "--permission-mode"
    assert cmd[5] == "auto"
    assert cmd[6] == "--allowedTools"
    assert cmd[7] == "Bash(happyranch *)"
    assert cmd[8] == "--output-format"
    assert cmd[9] == "json"
    assert cmd[10] == "--resume"
    assert cmd[11] == "resume-abc"
    assert not any("hello claude" in el for el in cmd), (
        "prompt must never appear in argv"
    )
    # Prompt delivered via stdin (communicate input).
    proc = mock_popen.return_value
    communicated = proc.communicate.call_args
    assert communicated.kwargs.get("input") is not None
    sent = communicated.kwargs["input"]
    assert sent.endswith("hello claude")
    assert "<session-lifetime>" in sent
    # Session id still parsed from stdout JSON.
    assert result.agent_session_id == "claude-sess-1"


@patch("runtime.orchestrator.executors.subprocess.Popen")
def test_pi_prompt_via_stdin_not_argv(mock_popen, tmp_path, runtime):
    """Pi's prompt body moves to stdin; argv keeps `-p` + json mode and the
    model flag."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    mock_popen.return_value = _popen_mock(
        stdout='{"type":"turn_end","message":{"usage":{"input":10,"output":5}}}'
    )

    ex = PiExecutor(pi_cli_path="pi", model_arg=["--model", "{model}"])
    result = ex.run(
        workspace=workspace, prompt="hello pi", session_id="sess-x",
        model="deepseek-v4-flash",
    )

    assert result.success is True
    cmd = mock_popen.call_args[0][0]
    assert cmd[0].endswith("pi")
    assert cmd[1] == "--model"
    assert cmd[2] == "deepseek-v4-flash"
    assert cmd[3] == "-p"
    assert cmd[4] == "--mode"
    assert cmd[5] == "json"
    assert not any("hello pi" in el for el in cmd)
    sent = mock_popen.return_value.communicate.call_args.kwargs["input"]
    assert sent.endswith("hello pi")
    assert "<session-lifetime>" in sent


@patch("runtime.orchestrator.executors.subprocess.Popen")
def test_claude_large_prompt_via_stdin_launches(mock_popen, tmp_path, runtime):
    """A >=512 KiB UTF-8 prompt (the E2BIG class) launches via stdin for
    Claude — no guard rejection, no truncation, exact bytes delivered."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    mock_popen.return_value = _popen_mock(
        stdout='{"type":"result","session_id":"claude-big","result":"ok"}'
    )

    ex = ClaudeExecutor(
        claude_cli_path="claude", permission_mode="auto", settings=Settings(),
        paths=runtime,
    )
    big = _huge_prompt_for_stdin()
    assert len(big.encode("utf-8")) >= 512 * 1024
    result = ex.run(workspace=workspace, prompt=big, session_id="sess-x")

    assert result.success is True
    mock_popen.assert_called_once()
    sent = mock_popen.return_value.communicate.call_args.kwargs["input"]
    assert sent.endswith("END_MARKER\n")
    assert "测试" in sent
    assert result.agent_session_id == "claude-big"


@patch("runtime.orchestrator.executors.subprocess.Popen")
def test_pi_large_prompt_via_stdin_launches(mock_popen, tmp_path, runtime):
    """Pi's stdin transport carries the >=512 KiB UTF-8 prompt class."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    mock_popen.return_value = _popen_mock(
        stdout='{"type":"agent_end","messages":[]}'
    )

    ex = PiExecutor(pi_cli_path="pi")
    big = _huge_prompt_for_stdin()
    result = ex.run(workspace=workspace, prompt=big, session_id="sess-x")

    assert result.success is True
    mock_popen.assert_called_once()
    sent = mock_popen.return_value.communicate.call_args.kwargs["input"]
    assert sent.endswith("END_MARKER\n")


# ── Codex unchanged / OpenCode+generic argv unchanged ───────────────────


@patch("runtime.orchestrator.executors.subprocess.Popen")
def test_codex_unchanged_stdin_dash(mock_popen, tmp_path, runtime):
    """Codex keeps its existing `-` + input_text contract (regression)."""
    from runtime.orchestrator.executors import CodexExecutor

    workspace = tmp_path / "ws"
    workspace.mkdir()
    mock_popen.return_value = _popen_mock(stdout="{}")
    ex = CodexExecutor(codex_cli_path="codex", sandbox_mode="workspace-write")
    result = ex.run(workspace=workspace, prompt="codex prompt", session_id="sess-x")
    assert result.success is True
    cmd = mock_popen.call_args[0][0]
    assert cmd[-1] == "-"
    assert not any("codex prompt" in el for el in cmd)
    sent = mock_popen.return_value.communicate.call_args.kwargs["input"]
    assert sent.endswith("codex prompt")


@patch("runtime.orchestrator.executors.subprocess.Popen")
def test_opencode_prompt_via_stdin_not_argv(mock_popen, tmp_path, runtime):
    """OpenCode 1.18.25 (TASK-6080 audit): the prompt body travels via
    stdin (input_text); argv keeps run/-s/--dir/--format json with no prompt
    element and stdin DEVNULL is replaced by PIPE."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    mock_popen.return_value = _popen_mock(stdout="ok")
    ex = OpencodeExecutor(opencode_cli_path="opencode")
    result = ex.run(workspace=workspace, prompt="opencode prompt", session_id="sess-x",
                    resume_session_id="ses_abc")
    assert result.success is True
    cmd = mock_popen.call_args[0][0]
    assert cmd[0].endswith("opencode")
    assert not any("opencode prompt" in el for el in cmd)
    # stdin transport: communicate got the prompt, stdin=PIPE.
    proc = mock_popen.return_value
    sent = proc.communicate.call_args.kwargs["input"]
    assert sent.endswith("opencode prompt")
    call_kwargs = mock_popen.call_args.kwargs
    assert call_kwargs.get("stdin") == subprocess.PIPE


def test_max_argv_element_bytes_platform_safe():
    """The guard limit is platform-aware and never above the Linux floor."""
    limit = _max_argv_element_bytes()
    assert limit == _LINUX_MAX_ARGV_ELEMENT_BYTES  # this host is Linux
    assert limit > 0
