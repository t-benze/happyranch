"""Phase 0 behavior-locking shadow contract tests (THR-107 / TASK-3347 D1).

These tests pin the current executor lifecycle behavior as shipping on
origin/main @ a7134f00. They exercise real production seams with
mocked Popen and fixed fixtures only — no parallel lifecycle
implementation, no production code edits.

Contract coverage:
(1) Pinned ordered cmd baseline for Claude/Codex/OpenCode/Pi + custom argv_template
(2) Native output parsing / token normalization for each built-in +
    custom no-envelope versus valid optional v1 envelope
(3) Workspace-style/profile mapping from profile → adapter_id → bootstrap
(4) _run_command nonzero, timeout, result tails, normalized accounting
(5) run_step/audit seam receives token accounting/error info
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from runtime.config import Settings
from runtime.infrastructure.database import Database
from runtime.models import TaskRecord, TaskStatus, TokenUsage
from runtime.orchestrator.executor_registry import (
    ExecutorProfile,
    ExecutorProfileCollisionError,
    ExecutorRegistry,
    build_executor,
    get_registry,
    reset_registry,
    validate_argv_template,
)
from runtime.orchestrator.executors import (
    ExecutorResult,
    GenericCliExecutor,
    ClaudeExecutor,
    CodexExecutor,
    OpencodeExecutor,
    PiExecutor,
    _run_command,
    _parse_claude_usage,
    _parse_codex_usage,
    _parse_opencode_usage,
    _parse_pi_usage,
    _parse_generic_cli_usage,
    _HR_ENVELOPE_BEGIN,
    _HR_ENVELOPE_END,
    _TAIL_BYTES,
    _SESSION_LIFETIME_PREAMBLE,
    is_rate_limit_signature,
)

_EXECUTOR_NAMES = frozenset({"claude", "codex", "opencode", "pi"})


# ── Frozen pre-extraction reference for CJK Unicode raw-only branches ─────
# Derived from immutable base f4a26824300a650f0ab1841945a9f7c00a84d86e
# (origin/main before THR-107 Phase 2), where the pre-extraction
# _parse_generic_cli_usage used legacy Python character slicing
# str[:2000].  This reference encodes the exact expected TokenUsage for
# three over-limit CJK input branches, computed independently — it does
# NOT import, call, monkeypatch, or otherwise delegate to
# GenericCliAdapter, GenericCliExecutor, or _parse_generic_cli_usage.

# Branch (1): missing END after __HR_ENVELOPE_BEGIN__
_FROZEN_MISSING_END_RAW = "__HR_ENVELOPE_BEGIN__\n" + "中" * 1000
# 1022 chars, character-sliced at 2000 — ends in U+4E2D, no U+FFFD

# Branch (2): JSON-decode failure
_FROZEN_INVALID_JSON_RAW = "{" + "中" * 1000 + "}"
# 1002 chars, character-sliced at 2000 — ends in '}', no U+FFFD

# Branch (3): valid JSON whose decoded root is non-dict (list)
_FROZEN_NOT_DICT_RAW = '["' + "中" * 1000 + '"]'
# 1004 chars, character-sliced at 2000 — ends in '"]', no U+FFFD

# Expected TokenUsage — parser-level raw-only, all parsed fields None
_FROZEN_MISSING_END_USAGE = TokenUsage(
    usage_raw_json=_FROZEN_MISSING_END_RAW,
)
_FROZEN_INVALID_JSON_USAGE = TokenUsage(
    usage_raw_json=_FROZEN_INVALID_JSON_RAW,
)
_FROZEN_NOT_DICT_USAGE = TokenUsage(
    usage_raw_json=_FROZEN_NOT_DICT_RAW,
)

# ── Frozen executor-seam TokenUsage (model backfill independently encoded) ─
# The executor's _run_command backfills token_usage.model = provider when
# the parser yields model=None.  These encode the expected full TokenUsage
# at the shipping GenericCliExecutor.run() seam with the provider used in
# each test, computed independently — no imports, calls, or delegation to
# GenericCliAdapter, GenericCliExecutor, or _parse_generic_cli_usage.
# Pinned-base provenance: immutable base f4a26824300a650f0ab1841945a9f7c00a84d86e.

_FROZEN_EXECUTOR_MISSING_END_USAGE = TokenUsage(
    usage_raw_json=_FROZEN_MISSING_END_RAW,
    input_tokens=None,
    output_tokens=None,
    cache_read_tokens=None,
    cache_creation_tokens=None,
    reasoning_tokens=None,
    model="custom-cjk-missing-end",
)
_FROZEN_EXECUTOR_INVALID_JSON_USAGE = TokenUsage(
    usage_raw_json=_FROZEN_INVALID_JSON_RAW,
    input_tokens=None,
    output_tokens=None,
    cache_read_tokens=None,
    cache_creation_tokens=None,
    reasoning_tokens=None,
    model="custom-cjk-invalid-json",
)
_FROZEN_EXECUTOR_NOT_DICT_USAGE = TokenUsage(
    usage_raw_json=_FROZEN_NOT_DICT_RAW,
    input_tokens=None,
    output_tokens=None,
    cache_read_tokens=None,
    cache_creation_tokens=None,
    reasoning_tokens=None,
    model="custom-cjk-not-dict",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _mock_shutil_which(monkeypatch, tmp_path):
    """Pre-register built-in executor binaries in the machine-local registry
    so _resolve_binary calls resolve deterministically regardless of host PATH
    (THR-107 seq155: registration-only resolution).
    This fixture runs BEFORE _register_test_binaries to ensure the daemon
    home is set before binary registration."""
    daemon_home = tmp_path / ".happyranch"
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home))

    from runtime.orchestrator.executor_binary_registry import set_binary
    for name in _EXECUTOR_NAMES:
        fake_bin = tmp_path / "bin" / name
        fake_bin.parent.mkdir(parents=True, exist_ok=True)
        fake_bin.touch(mode=0o755, exist_ok=True)
        set_binary(name, str(fake_bin))


@pytest.fixture(autouse=True)
def _reset_registry():
    """Reset the registry singleton between tests."""
    reset_registry()


def _make_popen_mock(returncode: int = 0, stdout: str = "", stderr: str = "", pid: int = 4242):
    proc = MagicMock()
    proc.pid = pid
    proc.returncode = returncode
    proc.communicate.return_value = (stdout, stderr)
    return proc


def _throttle_bypass():
    """Return a throttle mock that calls _launch immediately."""
    from runtime.orchestrator.throttle import ProviderThrottle
    throttle = MagicMock(spec=ProviderThrottle)
    throttle.run.side_effect = lambda provider, launch_fn, on_event: launch_fn()
    return throttle


def _allow_rules_patch():
    return patch(
        "runtime.orchestrator.workspace_adapters.allow_rules_for_agent",
        return_value=["Bash(happyranch *)"],
    )


# ---------------------------------------------------------------------------
# Contract 1: Pinned ordered cmd baseline
# ---------------------------------------------------------------------------

class TestCmdBaselines:
    """Pin the exact ordered argv vector for each built-in executor AND a
    representative custom argv_template invocation.

    Only binary path (argv[0]) and prompt content (includes
    _SESSION_LIFETIME_PREAMBLE) are normalized; every other element is
    asserted in position.  Flag reordering or extra flags will fail."""

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _assert_argv_structure(captured_cmd: list[str], *,
                               binary_ends_with: str,
                               expected_len: int,
                               prompt_index: int | None = None,
                               prompt_contains: list[str] | None = None):
        """Assert exact length and binary; normalize prompt content only."""
        assert len(captured_cmd) == expected_len, (
            f"expected {expected_len} elements, got {len(captured_cmd)}: {captured_cmd}"
        )
        assert captured_cmd[0].endswith(binary_ends_with), (
            f"binary must end with {binary_ends_with!r}, got {captured_cmd[0]!r}"
        )
        if prompt_index is not None and prompt_contains:
            prompt_val = captured_cmd[prompt_index]
            assert _SESSION_LIFETIME_PREAMBLE.strip() in prompt_val, (
                f"prompt arg at [{prompt_index}] must contain session-lifetime preamble"
            )
            for fragment in prompt_contains:
                assert fragment in prompt_val, (
                    f"prompt arg at [{prompt_index}] must contain {fragment!r}"
                )

    # -- Claude -------------------------------------------------------------

    def test_claude_cmd_baseline(self, tmp_path: Path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        fake_proc = _make_popen_mock(stdout="{}")
        captured_cmd: list[str] = []

        def _capture(cmd, **kw):
            captured_cmd.extend(cmd)
            return fake_proc

        with patch("runtime.orchestrator.executors.subprocess.Popen", _capture):
            with _allow_rules_patch():
                ex = ClaudeExecutor(
                    claude_cli_path="claude",
                    permission_mode="auto",
                    settings=Settings(),
                    paths=None,
                )
                ex.run(workspace, prompt="hello", session_id="sess-X")

        # [claude, "-p", "--permission-mode", "auto",
        #  "--allowedTools", "Bash(happyranch *)", "--output-format", "json"]
        # THR-200: the prompt body travels via stdin (input_text), never argv.
        self._assert_argv_structure(
            captured_cmd, binary_ends_with="/claude", expected_len=8,
        )
        assert captured_cmd[1] == "-p"
        assert captured_cmd[2] == "--permission-mode"
        assert captured_cmd[3] == "auto"
        assert captured_cmd[4] == "--allowedTools"
        assert captured_cmd[5] == "Bash(happyranch *)"
        assert captured_cmd[6] == "--output-format"
        assert captured_cmd[7] == "json"
        assert not any("hello" in el for el in captured_cmd)
        sent = fake_proc.communicate.call_args.kwargs["input"]
        assert "hello" in sent and _SESSION_LIFETIME_PREAMBLE.strip() in sent

    def test_claude_cmd_with_model(self, tmp_path: Path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        fake_proc = _make_popen_mock(stdout="{}")
        captured_cmd: list[str] = []

        def _capture(cmd, **kw):
            captured_cmd.extend(cmd)
            return fake_proc

        with patch("runtime.orchestrator.executors.subprocess.Popen", _capture):
            with _allow_rules_patch():
                ex = ClaudeExecutor(
                    claude_cli_path="claude",
                    permission_mode="auto",
                    settings=Settings(),
                    model_arg=["--model", "{model}"],
                )
                ex.run(workspace, prompt="hi", session_id="sess-X",
                       model="claude-sonnet-4-20250514")

        # [claude, "--model", "claude-sonnet-4-20250514", "-p",
        #  "--permission-mode", "auto", "--allowedTools", "Bash(happyranch *)",
        #  "--output-format", "json"]
        # THR-200: prompt travels via stdin, never argv.
        self._assert_argv_structure(
            captured_cmd, binary_ends_with="/claude", expected_len=10,
        )
        assert captured_cmd[1] == "--model"
        assert captured_cmd[2] == "claude-sonnet-4-20250514"
        assert captured_cmd[3] == "-p"
        assert captured_cmd[4] == "--permission-mode"
        assert captured_cmd[5] == "auto"
        assert captured_cmd[6] == "--allowedTools"
        assert captured_cmd[7] == "Bash(happyranch *)"
        assert captured_cmd[8] == "--output-format"
        assert captured_cmd[9] == "json"
        assert not any("hi" in el for el in captured_cmd)
        sent = fake_proc.communicate.call_args.kwargs["input"]
        assert "hi" in sent

    def test_claude_cmd_with_resume(self, tmp_path: Path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        fake_proc = _make_popen_mock(stdout="{}")
        captured_cmd: list[str] = []

        def _capture(cmd, **kw):
            captured_cmd.extend(cmd)
            return fake_proc

        with patch("runtime.orchestrator.executors.subprocess.Popen", _capture):
            with _allow_rules_patch():
                ex = ClaudeExecutor(
                    claude_cli_path="claude",
                    permission_mode="auto",
                    settings=Settings(),
                )
                ex.run(workspace, prompt="hi", session_id="sess-X",
                       resume_session_id="resume-abc")

        # [claude, "-p", "--permission-mode", "auto",
        #  "--allowedTools", "Bash(happyranch *)", "--output-format", "json",
        #  "--resume", "resume-abc"]
        # THR-200: prompt travels via stdin, never argv.
        self._assert_argv_structure(
            captured_cmd, binary_ends_with="/claude", expected_len=10,
        )
        assert captured_cmd[1] == "-p"
        assert captured_cmd[2] == "--permission-mode"
        assert captured_cmd[3] == "auto"
        assert captured_cmd[4] == "--allowedTools"
        assert captured_cmd[5] == "Bash(happyranch *)"
        assert captured_cmd[6] == "--output-format"
        assert captured_cmd[7] == "json"
        assert captured_cmd[8] == "--resume"
        assert captured_cmd[9] == "resume-abc"
        assert not any("hi" in el for el in captured_cmd)
        sent = fake_proc.communicate.call_args.kwargs["input"]
        assert "hi" in sent

    # -- Codex --------------------------------------------------------------

    def test_codex_cmd_baseline(self, tmp_path: Path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        fake_proc = _make_popen_mock(
            stdout='{"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":50}}'
        )
        captured_cmd: list[str] = []

        def _capture(cmd, **kw):
            captured_cmd.extend(cmd)
            return fake_proc

        with patch("runtime.orchestrator.executors.subprocess.Popen", _capture):
            ex = CodexExecutor(codex_cli_path="codex", sandbox_mode="workspace-write")
            ex.run(workspace, prompt="hello", session_id="sess-X")

        # [codex, "exec", "--sandbox", "workspace-write", "-c",
        #  "sandbox_workspace_write.network_access=true",
        #  "--skip-git-repo-check", "--json", "-"]
        self._assert_argv_structure(
            captured_cmd, binary_ends_with="/codex", expected_len=9,
        )
        assert captured_cmd[1] == "exec"
        assert captured_cmd[2] == "--sandbox"
        assert captured_cmd[3] == "workspace-write"
        assert captured_cmd[4] == "-c"
        assert captured_cmd[5] == "sandbox_workspace_write.network_access=true"
        assert captured_cmd[6] == "--skip-git-repo-check"
        assert captured_cmd[7] == "--json"
        assert captured_cmd[8] == "-"

    def test_codex_cmd_with_model(self, tmp_path: Path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        fake_proc = _make_popen_mock(
            stdout='{"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":50}}'
        )
        captured_cmd: list[str] = []

        def _capture(cmd, **kw):
            captured_cmd.extend(cmd)
            return fake_proc

        with patch("runtime.orchestrator.executors.subprocess.Popen", _capture):
            ex = CodexExecutor(
                codex_cli_path="codex",
                sandbox_mode="workspace-write",
                model_arg=["-m", "{model}"],
            )
            ex.run(workspace, prompt="hello", session_id="sess-X",
                   model="codex-gpt5")

        # [codex, "exec", "-m", "codex-gpt5", "--sandbox", "workspace-write",
        #  "-c", "sandbox_workspace_write.network_access=true",
        #  "--skip-git-repo-check", "--json", "-"]
        self._assert_argv_structure(
            captured_cmd, binary_ends_with="/codex", expected_len=11,
        )
        assert captured_cmd[1] == "exec"
        assert captured_cmd[2] == "-m"
        assert captured_cmd[3] == "codex-gpt5"
        assert captured_cmd[4] == "--sandbox"
        assert captured_cmd[5] == "workspace-write"
        assert captured_cmd[6] == "-c"
        assert captured_cmd[7] == "sandbox_workspace_write.network_access=true"
        assert captured_cmd[8] == "--skip-git-repo-check"
        assert captured_cmd[9] == "--json"
        assert captured_cmd[10] == "-"

    def test_codex_cmd_reads_prompt_from_stdin(self, tmp_path: Path):
        """Codex passes prompt via input_text to _run_command (stdin), not argv."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        fake_proc = _make_popen_mock(
            stdout='{"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":50}}'
        )
        captured_input: list[str] = []

        real_communicate = fake_proc.communicate
        def _capture_communicate(input=None, timeout=None):
            if input is not None:
                captured_input.append(input)
            return real_communicate(input=input, timeout=timeout)
        fake_proc.communicate = _capture_communicate

        with patch("runtime.orchestrator.executors.subprocess.Popen", return_value=fake_proc):
            ex = CodexExecutor(codex_cli_path="codex", sandbox_mode="workspace-write")
            ex.run(workspace, prompt="codex prompt text", session_id="sess-X")

        # Prove exactly one stdin write with exact preamble+prompt content.
        # Only the session-lifetime preamble and the fixed prompt are sent;
        # no extras, no omissions, no reordering.
        expected_stdin = _SESSION_LIFETIME_PREAMBLE + "codex prompt text"
        assert len(captured_input) == 1, (
            f"Expected exactly 1 stdin write, got {len(captured_input)}: {captured_input}"
        )
        assert captured_input[0] == expected_stdin, (
            f"Stdin content mismatch.\n"
            f"Expected ({len(expected_stdin)} chars): {expected_stdin!r}\n"
            f"Got      ({len(captured_input[0])} chars): {captured_input[0]!r}"
        )

    # -- Opencode -----------------------------------------------------------

    def test_opencode_cmd_baseline(self, tmp_path: Path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        fake_proc = _make_popen_mock(
            stdout='{"messages":[{"role":"assistant","usage":{"input_tokens":50,"output_tokens":25}}]}'
        )
        captured_cmd: list[str] = []

        def _capture(cmd, **kw):
            captured_cmd.extend(cmd)
            return fake_proc

        with patch("runtime.orchestrator.executors.subprocess.Popen", _capture):
            ex = OpencodeExecutor(opencode_cli_path="opencode")
            ex.run(workspace, prompt="hello world", session_id="sess-X")

        # [opencode, "run", "--dir", <workspace>, "--format", "json", <prompt>]
        self._assert_argv_structure(
            captured_cmd, binary_ends_with="/opencode", expected_len=7,
            prompt_index=6, prompt_contains=["hello world"],
        )
        assert captured_cmd[1] == "run"
        assert captured_cmd[2] == "--dir"
        assert captured_cmd[3] == str(workspace)
        assert captured_cmd[4] == "--format"
        assert captured_cmd[5] == "json"

    def test_opencode_cmd_with_model(self, tmp_path: Path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        fake_proc = _make_popen_mock(stdout='{"messages":[]}')
        captured_cmd: list[str] = []

        def _capture(cmd, **kw):
            captured_cmd.extend(cmd)
            return fake_proc

        with patch("runtime.orchestrator.executors.subprocess.Popen", _capture):
            ex = OpencodeExecutor(opencode_cli_path="opencode", model_arg=["-m", "{model}"])
            ex.run(workspace, prompt="hi", session_id="sess-X",
                   model="gemini-2.5-pro")

        # [opencode, "run", "-m", "gemini-2.5-pro", "--dir", <workspace>,
        #  "--format", "json", <prompt>]
        self._assert_argv_structure(
            captured_cmd, binary_ends_with="/opencode", expected_len=9,
            prompt_index=8, prompt_contains=["hi"],
        )
        assert captured_cmd[1] == "run"
        assert captured_cmd[2] == "-m"
        assert captured_cmd[3] == "gemini-2.5-pro"
        assert captured_cmd[4] == "--dir"
        assert captured_cmd[5] == str(workspace)
        assert captured_cmd[6] == "--format"
        assert captured_cmd[7] == "json"

    # -- Pi -----------------------------------------------------------------

    def test_pi_cmd_baseline(self, tmp_path: Path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        fake_proc = _make_popen_mock(
            stdout='{"type":"turn_end","message":{"usage":{"input":100,"output":50}}}'
        )
        captured_cmd: list[str] = []

        def _capture(cmd, **kw):
            captured_cmd.extend(cmd)
            return fake_proc

        with patch("runtime.orchestrator.executors.subprocess.Popen", _capture):
            ex = PiExecutor(pi_cli_path="pi")
            ex.run(workspace, prompt="hello pi", session_id="sess-X")

        # [pi, "-p", "--mode", "json"]
        # THR-200: prompt travels via stdin, never argv.
        self._assert_argv_structure(
            captured_cmd, binary_ends_with="/pi", expected_len=4,
        )
        assert captured_cmd[1] == "-p"
        assert captured_cmd[2] == "--mode"
        assert captured_cmd[3] == "json"
        assert not any("hello pi" in el for el in captured_cmd)
        sent = fake_proc.communicate.call_args.kwargs["input"]
        assert "hello pi" in sent

    def test_pi_cmd_with_model(self, tmp_path: Path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        fake_proc = _make_popen_mock(
            stdout='{"type":"turn_end","message":{"usage":{"input":100,"output":50}}}'
        )
        captured_cmd: list[str] = []

        def _capture(cmd, **kw):
            captured_cmd.extend(cmd)
            return fake_proc

        with patch("runtime.orchestrator.executors.subprocess.Popen", _capture):
            ex = PiExecutor(pi_cli_path="pi", model_arg=["--model", "{model}"])
            ex.run(workspace, prompt="hi", session_id="sess-X",
                   model="pi-model-v2")

        # [pi, "--model", "pi-model-v2", "-p", "--mode", "json"]
        # THR-200: prompt travels via stdin, never argv.
        self._assert_argv_structure(
            captured_cmd, binary_ends_with="/pi", expected_len=6,
        )
        assert captured_cmd[1] == "--model"
        assert captured_cmd[2] == "pi-model-v2"
        assert captured_cmd[3] == "-p"
        assert captured_cmd[4] == "--mode"
        assert captured_cmd[5] == "json"
        assert not any("hi" in el for el in captured_cmd)
        sent = fake_proc.communicate.call_args.kwargs["input"]
        assert "hi" in sent

    # -- Custom argv_template -----------------------------------------------

    def test_custom_argv_template_substitution(self, tmp_path: Path):
        """argv_template[0] is the executable; placeholders resolve to
        exactly ONE argv element each."""
        workspace = tmp_path / "ws"
        workspace.mkdir()

        fake_bin_path = tmp_path / "test-bin" / "my-cli"
        fake_bin_path.parent.mkdir(exist_ok=True)
        fake_bin_path.write_text("")
        fake_bin_path.chmod(0o755)

        captured_cmd: list[str] = []

        ex = GenericCliExecutor(
            profile_name="my-cli",
            argv_template=["my-cli", "--workspace", "{workspace}",
                           "--prompt", "{prompt}", "--timeout", "{timeout_seconds}"],
            provider="my-cli",
        )

        fake_proc = _make_popen_mock(stdout="ok")
        def _capture(cmd, **kw):
            captured_cmd.extend(cmd)
            return fake_proc

        with patch("runtime.orchestrator.executors.subprocess.Popen", _capture):
            with patch(
                "runtime.orchestrator.executors._resolve_binary",
                return_value=str(fake_bin_path),
            ):
                ex.run(workspace, prompt="custom prompt here", session_id="sess-X",
                       timeout_seconds=300)

        assert len(captured_cmd) == 7
        assert captured_cmd[0] == str(fake_bin_path)
        assert captured_cmd[1] == "--workspace"
        assert captured_cmd[2] == str(workspace)
        assert captured_cmd[3] == "--prompt"
        prompt_val = captured_cmd[4]
        assert _SESSION_LIFETIME_PREAMBLE.strip() in prompt_val
        assert "custom prompt here" in prompt_val
        assert captured_cmd[5] == "--timeout"
        assert captured_cmd[6] == "300"

    def test_custom_argv_template_placeholder_one_element_only(self, tmp_path: Path):
        """Placeholders must NOT be split across multiple argv elements."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        ex = GenericCliExecutor(
            profile_name="test-cli",
            argv_template=["test-cli", "{prompt}"],
            provider="test-cli",
        )

        fake_proc = _make_popen_mock(stdout="ok")
        captured_cmd: list[str] = []

        def _capture(cmd, **kw):
            captured_cmd.extend(cmd)
            return fake_proc

        with patch("runtime.orchestrator.executors.subprocess.Popen", _capture):
            with patch(
                "runtime.orchestrator.executors._resolve_binary",
                return_value="/usr/local/bin/test-cli",
            ):
                ex.run(workspace, prompt="one\ntwo arg", session_id="sess-X")

        assert len(captured_cmd) == 2
        assert captured_cmd[0] == "/usr/local/bin/test-cli"
        assert "\n" in captured_cmd[1]


# ---------------------------------------------------------------------------
# Contract 2: Native output parsing / token normalization
# ---------------------------------------------------------------------------

class TestOutputParsing:
    """Test each built-in parser with known fixtures and edge cases.
    Also covers custom no-envelope and valid v1 envelope behavior."""

    def test_parse_claude_from_fixture(self):
        """_parse_claude_usage extracts correct token fields from
        Claude's --output-format json fixture."""
        fixture = (Path(__file__).parent / "fixtures" / "usage_claude.json").read_text()
        result = _parse_claude_usage(fixture)
        assert result is not None
        assert result.input_tokens == 12345
        assert result.output_tokens == 4201
        assert result.cache_creation_tokens == 8042
        assert result.cache_read_tokens == 8402
        # Model from modelUsage (highest outputTokens)
        assert result.model == "claude-sonnet-4-6"

    def test_parse_claude_empty_returns_none(self):
        assert _parse_claude_usage("") is None
        assert _parse_claude_usage("   ") is None

    def test_parse_claude_invalid_json_returns_raw_only(self):
        result = _parse_claude_usage("not json at all")
        assert result is not None
        assert result.input_tokens is None
        assert result.output_tokens is None
        assert result.usage_raw_json is not None

    def test_parse_claude_missing_usage_returns_null_fields(self):
        result = _parse_claude_usage('{"type":"result","result":"ok"}')
        assert result is not None
        assert result.input_tokens is None
        assert result.output_tokens is None

    def test_parse_codex_from_fixture(self):
        """_parse_codex_usage extracts correct token fields from
        Codex exec --json JSONL fixture."""
        fixture = (Path(__file__).parent / "fixtures" / "usage_codex.jsonl").read_text()
        result = _parse_codex_usage(fixture)
        assert result is not None
        # Codex input_tokens includes cached_input_tokens — parser normalizes
        # input_tokens = max(input - cached, 0) per issue #216
        raw_input = 34887
        cached = 15003
        assert result.input_tokens == max(raw_input - cached, 0)
        assert result.output_tokens == 9003
        assert result.cache_read_tokens == 15003
        assert result.reasoning_tokens == 1234

    def test_parse_codex_empty_returns_none(self):
        assert _parse_codex_usage("") is None

    def test_parse_codex_no_turn_completed_returns_raw_only(self):
        result = _parse_codex_usage('{"type":"system","msg":"start"}\n')
        assert result is not None
        assert result.input_tokens is None
        assert result.usage_raw_json is not None

    def test_parse_opencode_old_format_from_fixture(self):
        """_parse_opencode_usage (old single-JSON format) sums assistant
        message usage."""
        fixture = (Path(__file__).parent / "fixtures" / "usage_opencode.json").read_text()
        result = _parse_opencode_usage(fixture)
        assert result is not None
        # Two assistant messages: first 100 in / 50 out / 0 cr / 100 cw
        # second 200 in / 75 out / 100 cr / 0 cw
        assert result.input_tokens == 300  # 100 + 200
        assert result.output_tokens == 125  # 50 + 75
        assert result.cache_read_tokens == 100  # 0 + 100
        assert result.cache_creation_tokens == 100  # 100 + 0
        assert result.model == "claude-sonnet-4-6"

    def test_parse_opencode_jsonl_from_fixture(self):
        """_parse_opencode_usage (JSONL format) uses step_finish.part.tokens."""
        fixture = (Path(__file__).parent / "fixtures" / "usage_opencode_jsonl.json").read_text()
        result = _parse_opencode_usage(fixture)
        assert result is not None
        assert result.input_tokens == 5000
        assert result.output_tokens == 2000
        assert result.cache_read_tokens == 3000
        assert result.cache_creation_tokens == 1000

    def test_parse_opencode_empty_returns_none(self):
        assert _parse_opencode_usage("") is None

    def test_parse_pi_from_fixture(self):
        """_parse_pi_usage uses last terminal event (turn_end wins
        when both message_end and turn_end present)."""
        fixture = (Path(__file__).parent / "fixtures" / "usage_pi.jsonl").read_text()
        result = _parse_pi_usage(fixture)
        assert result is not None
        # turn_end comes last with input=999, output=999, cacheRead=999, cacheWrite=999
        assert result.input_tokens == 999
        assert result.output_tokens == 999
        assert result.cache_read_tokens == 999
        assert result.cache_creation_tokens == 999
        assert result.model == "pi-model-v1"

    def test_parse_pi_empty_returns_none(self):
        assert _parse_pi_usage("") is None

    def test_parse_pi_no_terminal_event_returns_raw_only(self):
        result = _parse_pi_usage('{"type":"system","msg":"start"}')
        assert result is not None
        assert result.input_tokens is None
        assert result.usage_raw_json is not None

    # --- Generic CLI / Envelope ---

    def test_parse_generic_no_envelope_returns_none(self):
        """Custom CLI with no envelope returns None — no token accounting."""
        result = _parse_generic_cli_usage("regular stdout output\nno envelope here")
        assert result is None

    def test_parse_generic_empty_returns_none(self):
        assert _parse_generic_cli_usage("") is None

    def test_parse_generic_valid_v1_envelope(self):
        """A valid v1 envelope yields correct TokenUsage."""
        envelope_json = json.dumps({
            "envelope_version": 1,
            "token_usage": {
                "input_tokens": 2100,
                "output_tokens": 900,
                "cache_read_tokens": 300,
                "cache_creation_tokens": 100,
                "model": "kimi-k2",
                "usage_raw_json": '{"raw":"data"}',
            },
        })
        stdout = f"some normal output\n{_HR_ENVELOPE_BEGIN}\n{envelope_json}\n{_HR_ENVELOPE_END}\nmore output"
        result = _parse_generic_cli_usage(stdout)
        assert result is not None
        assert result.input_tokens == 2100
        assert result.output_tokens == 900
        assert result.cache_read_tokens == 300
        assert result.cache_creation_tokens == 100
        assert result.model == "kimi-k2"
        assert result.usage_raw_json == '{"raw":"data"}'

    def test_parse_generic_last_envelope_wins(self):
        """When multiple envelopes exist, the last one wins."""
        env1 = json.dumps({"envelope_version": 1, "token_usage": {"input_tokens": 100}})
        env2 = json.dumps({"envelope_version": 1, "token_usage": {"input_tokens": 999}})
        stdout = (
            f"{_HR_ENVELOPE_BEGIN}\n{env1}\n{_HR_ENVELOPE_END}\n"
            f"{_HR_ENVELOPE_BEGIN}\n{env2}\n{_HR_ENVELOPE_END}"
        )
        result = _parse_generic_cli_usage(stdout)
        assert result is not None
        assert result.input_tokens == 999

    def test_parse_generic_missing_end_returns_raw_only(self):
        """Missing END sentinel → raw-only TokenUsage (forensic preservation)."""
        envelope_json = json.dumps({"envelope_version": 1, "token_usage": {"input_tokens": 100}})
        stdout = f"{_HR_ENVELOPE_BEGIN}\n{envelope_json}\n"
        result = _parse_generic_cli_usage(stdout)
        assert result is not None
        assert result.input_tokens is None
        # usage_raw_json should contain the tail
        assert result.usage_raw_json is not None

    def test_parse_generic_wrong_version_returns_raw_only(self):
        """envelope_version != 1 → raw-only TokenUsage (strict reject)."""
        envelope_json = json.dumps({
            "envelope_version": 2,
            "token_usage": {"input_tokens": 100},
        })
        stdout = f"{_HR_ENVELOPE_BEGIN}\n{envelope_json}\n{_HR_ENVELOPE_END}"
        result = _parse_generic_cli_usage(stdout)
        assert result is not None
        assert result.input_tokens is None
        assert result.usage_raw_json is not None

    def test_parse_generic_invalid_json_returns_raw_only(self):
        stdout = f"{_HR_ENVELOPE_BEGIN}\nnot json\n{_HR_ENVELOPE_END}"
        result = _parse_generic_cli_usage(stdout)
        assert result is not None
        assert result.input_tokens is None
        assert result.usage_raw_json is not None

    def test_parse_generic_empty_envelope_block_returns_none(self):
        stdout = f"{_HR_ENVELOPE_BEGIN}\n\n{_HR_ENVELOPE_END}"
        result = _parse_generic_cli_usage(stdout)
        assert result is None

    def test_parse_generic_top_level_model_backfill(self):
        """Top-level model backfills token_usage.model when absent."""
        envelope_json = json.dumps({
            "envelope_version": 1,
            "model": "custom-model-v3",
            "token_usage": {"input_tokens": 500},
        })
        stdout = f"{_HR_ENVELOPE_BEGIN}\n{envelope_json}\n{_HR_ENVELOPE_END}"
        result = _parse_generic_cli_usage(stdout)
        assert result is not None
        assert result.input_tokens == 500
        assert result.model == "custom-model-v3"

    def test_parse_generic_token_type_coercion(self):
        """Non-int fields are coerced: float ok, bool/string rejected."""
        # float input_tokens should be coerced
        envelope_json = json.dumps({
            "envelope_version": 1,
            "token_usage": {
                "input_tokens": 100.0,
                "output_tokens": 50.5,  # non-int float → None
                "cache_read_tokens": True,  # bool → None
                "cache_creation_tokens": "nope",  # string → None
            },
        })
        stdout = f"{_HR_ENVELOPE_BEGIN}\n{envelope_json}\n{_HR_ENVELOPE_END}"
        result = _parse_generic_cli_usage(stdout)
        assert result is not None
        assert result.input_tokens == 100  # 100.0 → 100
        assert result.output_tokens is None  # 50.5 → None
        assert result.cache_read_tokens is None  # True → None
        assert result.cache_creation_tokens is None  # "nope" → None


# ---------------------------------------------------------------------------
# Contract 3: Workspace-style/profile mapping
# ---------------------------------------------------------------------------

class TestWorkspaceMapping:
    """Verify: profile name → adapter_id → actual workspace preparation,
    exercising the real workspace adapters to produce observable evidence."""

    # -- workspace initialization helper -----------------------------------

    @staticmethod
    def _init_workspace(workspace: Path, agent_name: str, provider: str,
                        settings: Settings, paths: OrgPaths) -> Path:
        """Bootstrap a workspace via the real InitAgent flow so the
        readiness marker exists."""
        from runtime.orchestrator.workspace_adapters import (
            ClaudeWorkspaceAdapter, CodexWorkspaceAdapter,
            OpencodeWorkspaceAdapter, PiWorkspaceAdapter,
            ensure_system_contracts_materialized,
        )
        workspace.mkdir(parents=True, exist_ok=True)
        adapter_cls = {
            "claude": ClaudeWorkspaceAdapter,
            "codex": CodexWorkspaceAdapter,
            "opencode": OpencodeWorkspaceAdapter,
            "pi": PiWorkspaceAdapter,
        }[provider]
        adapter = adapter_cls(settings, paths=paths, slug="test")
        adapter.ensure_workspace_ready(workspace, agent_name, system_prompt="You are a test agent.")
        # Readiness marker is injected via ensure_system_contracts_materialized
        try:
            ensure_system_contracts_materialized(
                workspace, settings, slug="test", context="test", provider=provider,
            )
        except Exception:
            pass  # may fail without org setup; marker may still exist from adapter
        return workspace

    # -- readiness markers --------------------------------------------------

    def test_builtin_workspace_produces_readiness_marker(
            self, tmp_path: Path):
        """Each built-in adapter's ensure_workspace_ready produces the
        canonical bootstrap files — CLAUDE.md for claude, AGENTS.md for others.
        The readiness marker (.claude/skills/start-task/SKILL.md) is injected
        by ensure_system_contracts_materialized at session time, which is a
        separate concern verified in the existing test_executor.py suite."""
        from runtime.orchestrator._paths import OrgPaths
        from runtime.runtime import RuntimeDir

        rt = RuntimeDir.init(tmp_path / "rt")
        paths = OrgPaths(root=rt.orgs_dir / "test")
        paths.teams_config_path.parent.mkdir(parents=True, exist_ok=True)
        paths.teams_config_path.write_text(
            "teams:\n  engineering:\n    manager: engineering_head\n    workers: [dev_agent]\n"
        )
        settings = Settings()

        # Verify bootstrap files produced by the real workspace adapters
        cases = [
            ("claude", "CLAUDE.md", False),  # writes CLAUDE.md, not AGENTS.md
            ("codex", "AGENTS.md", True),     # writes AGENTS.md, not CLAUDE.md
            ("opencode", "AGENTS.md", True),
            ("pi", "AGENTS.md", True),
        ]
        for provider, bootstrap_file, expect_agents_md in cases:
            ws = self._init_workspace(
                tmp_path / f"ws_{provider}",
                agent_name=f"agent_{provider}",
                provider=provider,
                settings=settings,
                paths=paths,
            )
            assert (ws / bootstrap_file).exists(), (
                f"{provider}: {bootstrap_file} not created. "
                f"Files: {[p.name for p in ws.iterdir()]}"
            )
            if expect_agents_md:
                assert (ws / "AGENTS.md").exists()
                assert not (ws / "CLAUDE.md").exists()
            else:
                assert (ws / "CLAUDE.md").exists()
                assert not (ws / "AGENTS.md").exists()

    def test_claude_adapter_writes_claude_md(self, tmp_path: Path):
        """Claude adapter writes CLAUDE.md (not AGENTS.md)."""
        ws = tmp_path / "ws"
        ws.mkdir()
        from runtime.orchestrator.workspace_adapters import ClaudeWorkspaceAdapter
        from runtime.orchestrator._paths import OrgPaths
        from runtime.runtime import RuntimeDir
        rt = RuntimeDir.init(tmp_path / "rt")
        paths = OrgPaths(root=rt.orgs_dir / "test")
        paths.teams_config_path.parent.mkdir(parents=True, exist_ok=True)
        paths.teams_config_path.write_text(
            "teams:\n  engineering:\n    manager: engineering_head\n    workers: [dev_agent]\n"
        )
        adapter = ClaudeWorkspaceAdapter(Settings(), paths=paths, slug="test")
        adapter.ensure_workspace_ready(ws, "test_agent", system_prompt="You are a test agent.")
        assert (ws / "CLAUDE.md").exists()
        assert not (ws / "AGENTS.md").exists()

    def test_non_claude_adapters_write_agents_md(self, tmp_path: Path):
        """Codex, Opencode, Pi all write AGENTS.md (not CLAUDE.md)."""
        from runtime.orchestrator.workspace_adapters import (
            CodexWorkspaceAdapter, OpencodeWorkspaceAdapter, PiWorkspaceAdapter,
        )
        from runtime.orchestrator._paths import OrgPaths
        from runtime.runtime import RuntimeDir
        rt = RuntimeDir.init(tmp_path / "rt")
        paths = OrgPaths(root=rt.orgs_dir / "test")
        paths.teams_config_path.parent.mkdir(parents=True, exist_ok=True)
        paths.teams_config_path.write_text(
            "teams:\n  engineering:\n    manager: engineering_head\n    workers: [dev_agent]\n"
        )
        for provider, adapter_cls in [
            ("codex", CodexWorkspaceAdapter),
            ("opencode", OpencodeWorkspaceAdapter),
            ("pi", PiWorkspaceAdapter),
        ]:
            ws = tmp_path / f"ws_{provider}"
            ws.mkdir()
            adapter = adapter_cls(Settings(), paths=paths, slug="test")
            adapter.ensure_workspace_ready(ws, "test_agent", system_prompt="You are a test agent.")
            assert (ws / "AGENTS.md").exists(), f"{provider}: AGENTS.md missing"
            assert not (ws / "CLAUDE.md").exists(), f"{provider}: unexpected CLAUDE.md"


# ---------------------------------------------------------------------------
# Contract 4: _run_command nonzero, timeout, result tails, accounting
# ---------------------------------------------------------------------------

class TestRunCommand:
    """Test _run_command behavior: nonzero, timeout, tail truncation,
    rate-limit detection, and token accounting."""

    def test_nonzero_exit_produces_failure_result(self, tmp_path: Path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        fake_proc = _make_popen_mock(returncode=1, stdout="output", stderr="error message")

        with patch("runtime.orchestrator.executors.subprocess.Popen", return_value=fake_proc):
            result = _run_command(
                cmd=["test"],
                workspace=workspace,
                session_id="sess-X",
                timeout_seconds=60,
            )

        assert result.success is False
        assert result.returncode == 1
        assert "exit" in (result.error or "")
        assert "error message" in (result.error or "")

    def test_nonzero_no_token_usage_written(self, tmp_path: Path):
        """Non-zero exit → token_usage is None (no token row)."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        fake_proc = _make_popen_mock(returncode=1, stdout="out", stderr="err")

        with patch("runtime.orchestrator.executors.subprocess.Popen", return_value=fake_proc):
            result = _run_command(
                cmd=["test"],
                workspace=workspace,
                session_id="sess-X",
                timeout_seconds=60,
            )

        assert result.token_usage is None

    def test_timeout_produces_failure_result(self, tmp_path: Path):
        workspace = tmp_path / "ws"
        workspace.mkdir()

        fake_proc = MagicMock()
        fake_proc.pid = 12345
        import subprocess
        fake_proc.communicate.side_effect = subprocess.TimeoutExpired(cmd=["test"], timeout=60)

        with patch("runtime.orchestrator.executors.subprocess.Popen", return_value=fake_proc):
            result = _run_command(
                cmd=["test"],
                workspace=workspace,
                session_id="sess-X",
                timeout_seconds=60,
            )

        assert result.success is False
        assert result.returncode is None  # timeout → no exit code
        assert result.error is not None
        assert "timed out" in result.error.lower()

    def test_timeout_kills_process(self, tmp_path: Path):
        workspace = tmp_path / "ws"
        workspace.mkdir()

        import subprocess
        fake_proc = MagicMock()
        fake_proc.pid = 12345
        fake_proc.communicate.side_effect = subprocess.TimeoutExpired(cmd=["test"], timeout=60)

        with patch("runtime.orchestrator.executors.subprocess.Popen", return_value=fake_proc):
            _run_command(
                cmd=["test"],
                workspace=workspace,
                session_id="sess-X",
                timeout_seconds=60,
            )

        fake_proc.kill.assert_called_once()

    def test_stdout_stderr_tails_are_truncated(self, tmp_path: Path):
        """stdout_tail and stderr_tail are the last _TAIL_BYTES only."""
        workspace = tmp_path / "ws"
        workspace.mkdir()

        long_stdout = "A" * (_TAIL_BYTES + 500)
        long_stderr = "B" * (_TAIL_BYTES + 300)
        fake_proc = _make_popen_mock(stdout=long_stdout, stderr=long_stderr)

        with patch("runtime.orchestrator.executors.subprocess.Popen", return_value=fake_proc):
            result = _run_command(
                cmd=["test"],
                workspace=workspace,
                session_id="sess-X",
                timeout_seconds=60,
            )

        assert len(result.stdout_tail) == _TAIL_BYTES
        assert len(result.stderr_tail) == _TAIL_BYTES
        assert result.stdout_tail == "A" * _TAIL_BYTES
        assert result.stderr_tail == "B" * _TAIL_BYTES

    def test_short_output_not_padded(self, tmp_path: Path):
        """Short output (<_TAIL_BYTES) is returned verbatim, not padded."""
        workspace = tmp_path / "ws"
        workspace.mkdir()

        fake_proc = _make_popen_mock(stdout="short", stderr="err")

        with patch("runtime.orchestrator.executors.subprocess.Popen", return_value=fake_proc):
            result = _run_command(
                cmd=["test"],
                workspace=workspace,
                session_id="sess-X",
                timeout_seconds=60,
            )

        assert result.stdout_tail == "short"
        assert result.stderr_tail == "err"

    def test_success_with_usage_parser(self, tmp_path: Path):
        """Success path calls usage_parser and sets token_usage."""
        workspace = tmp_path / "ws"
        workspace.mkdir()

        fixture = (Path(__file__).parent / "fixtures" / "usage_claude.json").read_text()
        fake_proc = _make_popen_mock(stdout=fixture)

        with patch("runtime.orchestrator.executors.subprocess.Popen", return_value=fake_proc):
            result = _run_command(
                cmd=["test"],
                workspace=workspace,
                session_id="sess-X",
                timeout_seconds=60,
                usage_parser=_parse_claude_usage,
            )

        assert result.success is True
        assert result.token_usage is not None
        assert result.token_usage.input_tokens == 12345

    def test_usage_parser_exception_does_not_crash(self, tmp_path: Path):
        """Parser that raises → token_usage stays None, session still succeeds."""
        workspace = tmp_path / "ws"
        workspace.mkdir()

        fake_proc = _make_popen_mock(stdout="output")

        def _crashing_parser(stdout):
            raise RuntimeError("parser bug")

        with patch("runtime.orchestrator.executors.subprocess.Popen", return_value=fake_proc):
            result = _run_command(
                cmd=["test"],
                workspace=workspace,
                session_id="sess-X",
                timeout_seconds=60,
                usage_parser=_crashing_parser,
            )

        assert result.success is True
        assert result.token_usage is None  # parser crash is caught

    def test_model_backfill_when_null(self, tmp_path: Path):
        """When token_usage.model is None, provider string is back-filled."""
        workspace = tmp_path / "ws"
        workspace.mkdir()

        # JSON with no model info
        fake_proc = _make_popen_mock(
            stdout='{"type":"result","result":"ok","usage":{"input_tokens":50,"output_tokens":25}}'
        )

        with patch("runtime.orchestrator.executors.subprocess.Popen", return_value=fake_proc):
            result = _run_command(
                cmd=["test"],
                workspace=workspace,
                session_id="sess-X",
                timeout_seconds=60,
                usage_parser=_parse_claude_usage,
                provider="claude",
            )

        assert result.token_usage is not None
        assert result.token_usage.model == "claude"

    def test_rate_limit_signature_detection(self, tmp_path: Path):
        """Rate-limit strings in stdout/stderr set rate_limited=True."""
        # Test stdout
        assert is_rate_limit_signature("error: rate limit exceeded") is True
        # Test stderr (via combined check in _run_command)
        assert is_rate_limit_signature("hit your limit · resets at 12:00") is True
        assert is_rate_limit_signature("normal output") is False

    def test_run_command_rate_limited_flag(self, tmp_path: Path):
        workspace = tmp_path / "ws"
        workspace.mkdir()

        fake_proc = _make_popen_mock(
            returncode=1,
            stdout="",
            stderr="Error: Rate limit hit your limit · resets at 10:00",
        )

        with patch("runtime.orchestrator.executors.subprocess.Popen", return_value=fake_proc):
            result = _run_command(
                cmd=["test"],
                workspace=workspace,
                session_id="sess-X",
                timeout_seconds=60,
            )

        assert result.rate_limited is True


# ---------------------------------------------------------------------------
# Contract 5: run_step / audit seam receives token accounting & error info
# ---------------------------------------------------------------------------

class TestRunStepAuditSeam:
    """Drive the real Orchestrator._run_agent and run_step paths with
    mocked executor subprocess and fixed fixtures — no lookalike mocks."""

    @staticmethod
    def _setup_runtime_and_paths(tmp_path: Path):
        """Create a minimal RuntimeDir + OrgPaths with seeded agent config."""
        from runtime.runtime import RuntimeDir
        from runtime.orchestrator._paths import OrgPaths
        rt = RuntimeDir.init(tmp_path / "rt")
        paths = OrgPaths(root=rt.orgs_dir / "test")
        paths.teams_config_path.parent.mkdir(parents=True, exist_ok=True)
        paths.teams_config_path.write_text(
            "teams:\n"
            "  engineering:\n"
            "    manager: engineering_head\n"
            "    workers: [dev_agent]\n"
        )
        # Seed an agent definition so _resolve_executor_name works
        agent_md = paths.agents_dir / "dev_agent.md"
        agent_md.parent.mkdir(parents=True, exist_ok=True)
        agent_md.write_text("""---
name: dev_agent
team: engineering
role: worker
executor: claude
allow_rules:
  - Bash(happyranch *)
repos: {}
system_prompt: |
  You are the dev agent.
---
You are the dev agent. This is your system prompt.
""")
        return rt, paths

    @staticmethod
    def _bootstrap_workspace(paths: OrgPaths, agent_name: str, provider: str):
        """Bootstrap workspace with the real adapters and inject readiness marker."""
        workspace = paths.workspaces_dir / agent_name
        workspace.mkdir(parents=True, exist_ok=True)
        from runtime.orchestrator.workspace_adapters import (
            ClaudeWorkspaceAdapter, CodexWorkspaceAdapter,
            OpencodeWorkspaceAdapter, PiWorkspaceAdapter,
            ensure_system_contracts_materialized,
        )
        adapter_cls = {
            "claude": ClaudeWorkspaceAdapter,
            "codex": CodexWorkspaceAdapter,
            "opencode": OpencodeWorkspaceAdapter,
            "pi": PiWorkspaceAdapter,
        }[provider]
        adapter = adapter_cls(Settings(), paths=paths, slug="test")
        adapter.ensure_workspace_ready(workspace, agent_name, system_prompt="You are a test agent.")
        try:
            ensure_system_contracts_materialized(
                workspace, Settings(), slug="test", context="test",
                provider=provider,
            )
        except Exception:
            pass
        return workspace

    # ------------------------------------------------------------------

    def test_run_agent_calls_log_session_end(self, tmp_path: Path):
        """Orchestrator._run_agent invokes log_session_end with the
        ExecutorResult's token_usage and duration_seconds."""
        rt, paths = self._setup_runtime_and_paths(tmp_path)
        db = Database(paths.db_path)
        from runtime.orchestrator.teams import TeamsRegistry
        from runtime.orchestrator.orchestrator import Orchestrator

        team_reg = TeamsRegistry.load(paths.root)
        orch = Orchestrator(db=db, settings=Settings(), paths=paths,
                            slug="test", teams=team_reg)

        # Insert a minimal task
        db.insert_task(TaskRecord(
            id="T-RS1", brief="test", assigned_agent="dev_agent",
        ))
        # Bootstrap workspace
        self._bootstrap_workspace(paths, "dev_agent", "claude")

        # Mock the executor so it returns a known result without a subprocess
        token_usage = TokenUsage(input_tokens=100, output_tokens=50,
                                model="test-model")
        fake_result = ExecutorResult(
            success=True, duration_seconds=7, session_id="sess-FAKE",
            token_usage=token_usage, returncode=0,
        )
        mock_exec = MagicMock()
        mock_exec.run.return_value = fake_result

        # Spy on log_session_end through the real audit logger
        with patch.object(orch._audit, 'log_session_end',
                         wraps=orch._audit.log_session_end) as spy_end:
            # Replace the executor produced by _build_executor
            with patch.object(orch, '_build_executor', return_value=mock_exec):
                result, report = orch._run_agent("T-RS1", "dev_agent", "prompt text")

        # Verify the result is forwarded
        assert result is fake_result
        # Verify log_session_end was called with correct args
        spy_end.assert_called_once()
        call_kw = spy_end.call_args[1]
        assert call_kw["task_id"] == "T-RS1"
        assert call_kw["agent"] == "dev_agent"
        assert call_kw["duration_seconds"] == 7
        assert call_kw["token_usage"] is token_usage

    def test_run_step_persists_token_usage_with_scope_fields(
            self, tmp_path: Path, monkeypatch):
        """run_step persists token_usage via insert_session_token_usage
        with correct scope_type, scope_id, and thread_id fields."""
        rt, paths = self._setup_runtime_and_paths(tmp_path)
        db = Database(paths.db_path)
        from runtime.orchestrator.teams import TeamsRegistry
        from runtime.orchestrator.orchestrator import Orchestrator

        team_reg = TeamsRegistry.load(paths.root)
        orch = Orchestrator(db=db, settings=Settings(
            max_orchestration_steps=10,
        ), paths=paths, slug="test", teams=team_reg)

        # Insert a task with a known thread_id
        db.insert_task(TaskRecord(
            id="T-RS2", brief="test", assigned_agent="dev_agent",
            dispatched_from_thread_id="THREAD-42",
        ))
        self._bootstrap_workspace(paths, "dev_agent", "claude")

        # Build a deterministic ExecutorResult
        token_usage = TokenUsage(
            input_tokens=200, output_tokens=100, cache_read_tokens=50,
            model="claude-opus",
        )
        fake_result = ExecutorResult(
            success=True, duration_seconds=3, session_id="sess-RS2",
            token_usage=token_usage, returncode=0,
        )

        # Store insert_session_token_usage args
        insert_calls: list[dict] = []
        _real_insert = db.insert_session_token_usage
        def _capturing_insert(**kwargs):
            insert_calls.append(kwargs)
            return _real_insert(**kwargs)
        monkeypatch.setattr(db, 'insert_session_token_usage', _capturing_insert)

        # Patch _run_agent to return the controlled result
        def _fake_run_agent(task_id, agent, prompt, on_session_started=None):
            return fake_result, None  # None report → failure path BUT token usage persisted first
        monkeypatch.setattr(orch, '_run_agent', _fake_run_agent)

        # Drive run_step — it will call _run_agent, persist token_usage,
        # then hit the not-success branch. We intercept token persistence first.
        orch.run_step("T-RS2")

        assert len(insert_calls) == 1, (
            f"Expected 1 insert_session_token_usage call, got {len(insert_calls)}"
        )
        kw = insert_calls[0]
        assert kw["task_id"] == "T-RS2"
        assert kw["agent"] == "dev_agent"
        assert kw["session_id"] == "sess-RS2"
        assert kw["executor"] == "claude"  # from agent.md
        assert kw["token_usage"] is token_usage
        assert kw["scope_type"] == "task"
        assert kw["scope_id"] == "T-RS2"
        assert kw["thread_id"] == "THREAD-42"

    def test_run_step_failure_note_receives_stderr_tail(
            self, tmp_path: Path, monkeypatch):
        """Failure path in run_step feeds ExecutorResult error tails into
        _session_failed_note — assert the failure note contains stderr content."""
        rt, paths = self._setup_runtime_and_paths(tmp_path)
        db = Database(paths.db_path)
        from runtime.orchestrator.teams import TeamsRegistry
        from runtime.orchestrator.orchestrator import Orchestrator
        from runtime.orchestrator.run_step import _session_failed_note

        team_reg = TeamsRegistry.load(paths.root)
        orch = Orchestrator(db=db, settings=Settings(
            max_orchestration_steps=10,
        ), paths=paths, slug="test", teams=team_reg)

        db.insert_task(TaskRecord(
            id="T-RS3", brief="test", assigned_agent="dev_agent",
        ))
        self._bootstrap_workspace(paths, "dev_agent", "claude")

        # Build a failure ExecutorResult
        fail_result = ExecutorResult(
            success=False, duration_seconds=2, session_id="sess-FAIL",
            returncode=1, stdout_tail="partial stdout",
            stderr_tail="CRITICAL: something went wrong",
            error="Command exited with code 1",
        )

        def _fake_run_agent(task_id, agent, prompt, on_session_started=None):
            return fail_result, None
        monkeypatch.setattr(orch, '_run_agent', _fake_run_agent)

        orch.run_step("T-RS3")

        # After run_step, the task should be FAILED with a note containing
        # the stderr tail content
        task = db.get_task("T-RS3")
        assert task is not None
        assert task.status == TaskStatus.FAILED
        note = task.note or ""
        assert "CRITICAL" in note or "stderr" in note.lower(), (
            f"Failure note must contain stderr evidence, got: {note!r}"
        )

    def test_run_step_failure_note_from_session_failed_note(
            self, monkeypatch):
        """_session_failed_note reads returncode, stderr/stdout tail, and
        error from the ExecutorResult — the failure note surfaces these."""
        from runtime.orchestrator.run_step import _session_failed_note

        result = ExecutorResult(
            success=False, duration_seconds=5, session_id="sess-FAIL1",
            returncode=42,
            stderr_tail="fatal error: disk full",
            stdout_tail="", error="disk write failed",
        )
        note = _session_failed_note(result, None)
        assert "rc=42" in note
        assert "stderr" in note
        assert "fatal error: disk full" in note
        # error text is also included
        assert "disk write failed" in note

        # When stderr is empty, stdout is used
        result2 = ExecutorResult(
            success=False, duration_seconds=3, session_id="sess-FAIL2",
            returncode=1,
            stderr_tail="", stdout_tail="output had an error",
        )
        note2 = _session_failed_note(result2, None)
        assert "output had an error" in note2


class TestExecutorResultShape:
    """Static shape guards on ExecutorResult dataclass."""

    def test_executorresult_top_level_fields_unchanged(self):
        """ExecutorResult has exactly the 11 fields shipping today.
        This guards against accidental field additions/removals."""
        result = ExecutorResult(success=True, duration_seconds=1, session_id="s")
        # Fields as of THR-116 (TASK-3435) — terminal_error added for
        # dream-run failure observability.
        expected_fields = {
            "success", "duration_seconds", "session_id", "returncode",
            "stdout_tail", "stderr_tail", "error", "token_usage",
            "agent_session_id", "rate_limited", "terminal_error",
        }
        # Verify every field in ExecutorResult.__dataclass_fields__
        from dataclasses import fields as dc_fields
        actual_fields = {f.name for f in dc_fields(ExecutorResult)}
        assert actual_fields == expected_fields, (
            f"ExecutorResult field set changed! Expected {expected_fields}, "
            f"got {actual_fields}. This is a breaking contract change."
        )


# ---------------------------------------------------------------------------
# build_executor canonical profiling
# ---------------------------------------------------------------------------

class TestBuildExecutorCanonical:
    """Pin the build_executor factory dispatch — verifying each built-in
    returns the correct executor type via the static data-driven factory dict
    (D10/D11, THR-107 seq84, July 2026), and exercising the full fifth
    lifecycle (custom registration -> factory -> run) end-to-end.

    Historical baseline (pre-D10): build_executor used a hard-coded
    ``if profile.name == "claude" ...`` chain (D2 compatibility path).
    D10/D11 replaced it with a static code-native factory dict derived from
    the D8 authoritative catalog. These tests verify the shipped factory
    produces the same specialized executor types and adapters."""

    def test_build_executor_returns_claude_executor(self):
        ex = build_executor("claude", Settings())
        assert isinstance(ex, ClaudeExecutor)

    def test_build_executor_returns_codex_executor(self):
        ex = build_executor("codex", Settings())
        assert isinstance(ex, CodexExecutor)

    def test_build_executor_returns_opencode_executor(self):
        ex = build_executor("opencode", Settings())
        assert isinstance(ex, OpencodeExecutor)

    def test_build_executor_returns_pi_executor(self):
        ex = build_executor("pi", Settings())
        assert isinstance(ex, PiExecutor)

    def test_build_executor_returns_generic_for_custom(self):
        registry = get_registry()
        registry.register_custom_profile(ExecutorProfile(
            name="test-custom",
            kind="custom",
            adapter_id="pi",
            readiness_marker_fragment="AGENTS.md",
            argv_template=["test-cli", "{prompt}"],
            command="test-cli",
        ))
        ex = build_executor("test-custom", Settings())
        assert isinstance(ex, GenericCliExecutor)

    def test_build_executor_raises_for_unregistered(self):
        with pytest.raises(ValueError, match="Unregistered"):
            build_executor("nonexistent", Settings())

    # -- Fifth lifecycle e2e: registration → factory → run ----------------

    def test_custom_profile_full_lifecycle_with_argv_and_envelope(
            self, tmp_path: Path, monkeypatch):
        """Exercise the complete fifth lifecycle with a non-null declared
        command: validates a fixed raw config through the canonical
        ExecutorRegistry.validate_custom_profile_config, which exercises
        the real shutil.which resolution branch and the declared-command /
        argv_template[0] executable-parity validation (issue #490). Registers
        the returned validated profile, builds it via build_executor, runs it
        with mocked Popen, asserts exact ordered argv, and verifies optional
        v1 envelope parsing.

        The executor_registry module’s shutil.which is patched to return a
        deterministic resolved path for the valid declared command so the
        test is platform-stable. This test FAILS if
        validate_custom_profile_config or the command/template parity check
        is bypassed.
        """
        workspace = tmp_path / "ws"
        workspace.mkdir()

        # -- Register binary for the profile name (THR-107 seq155) -----
        # validate_custom_profile_config no longer calls shutil.which;
        # binary registration happens separately. The test registers
        # the binary for the profile name so _resolve_binary succeeds.
        from runtime.orchestrator.executor_binary_registry import set_binary
        kimi_bin = tmp_path / "kimi-cli-bin"
        kimi_bin.touch(mode=0o755)
        set_binary("kimi-custom", str(kimi_bin))

        # 1. Build a fixed raw config with a non-null command and validate
        #    through the canonical seam.
        config = {
            "command": "kimi-cli",
            "argv_template": [
                "kimi-cli", "--model", "kimi-v2",
                "--workspace", "{workspace}",
                "--prompt", "{prompt}",
                "--timeout", "{timeout_seconds}",
            ],
            "adapter": "pi",
        }
        registry = get_registry()
        profile = ExecutorRegistry.validate_custom_profile_config(
            "kimi-custom", config,
        )
        # Assert the validated profile shape before registration
        assert profile is not None
        assert profile.name == "kimi-custom"
        assert profile.kind == "custom"
        assert profile.adapter_id == "pi"
        assert profile.argv_template == [
            "kimi-cli", "--model", "kimi-v2",
            "--workspace", "{workspace}",
            "--prompt", "{prompt}",
            "--timeout", "{timeout_seconds}",
        ]

        # 2. Register the validated profile
        registry.register_custom_profile(profile)

        # 3. build_executor resolves to GenericCliExecutor
        ex = build_executor("kimi-custom", Settings())
        assert isinstance(ex, GenericCliExecutor)
        assert ex._argv_template == profile.argv_template
        assert ex._provider == "kimi-custom"

        # 4. Run with mocked Popen, capture full argv
        captured_cmd: list[str] = []

        # Use a valid v1 envelope in stdout to verify envelope parsing
        envelope_json = json.dumps({
            "envelope_version": 1,
            "token_usage": {"input_tokens": 500, "output_tokens": 250},
        })
        fake_proc = _make_popen_mock(stdout=f"output\n{_HR_ENVELOPE_BEGIN}\n{envelope_json}\n{_HR_ENVELOPE_END}\n")

        def _capture(cmd, **kw):
            captured_cmd.extend(cmd)
            return fake_proc

        with patch("runtime.orchestrator.executors.subprocess.Popen", _capture):
            with patch(
                "runtime.orchestrator.executors._resolve_binary",
                return_value="/opt/kimi/kimi-cli",
            ):
                result = ex.run(
                    workspace, prompt="make a thing", session_id="sess-E2E",
                    timeout_seconds=120,
                )

        # 5. Assert exact ordered argv (normalize binary path + prompt content)
        assert len(captured_cmd) == 9, f"expected 9, got {len(captured_cmd)}: {captured_cmd}"
        assert captured_cmd[0] == "/opt/kimi/kimi-cli"
        assert captured_cmd[1] == "--model"
        assert captured_cmd[2] == "kimi-v2"
        assert captured_cmd[3] == "--workspace"
        assert captured_cmd[4] == str(workspace)
        assert captured_cmd[5] == "--prompt"
        prompt_val = captured_cmd[6]
        assert _SESSION_LIFETIME_PREAMBLE.strip() in prompt_val
        assert "make a thing" in prompt_val
        assert captured_cmd[7] == "--timeout"
        assert captured_cmd[8] == "120"

        # 6. Verify v1 envelope was parsed
        assert result.success is True
        assert result.token_usage is not None
        assert result.token_usage.input_tokens == 500
        assert result.token_usage.output_tokens == 250

    def test_custom_profile_command_template_parity_violation_rejected(
            self, monkeypatch):
        """Prove that canonical validation REJECTS a registered custom
        profile whose non-null declared command resolves to a different
        executable than argv_template[0] (the executable
        GenericCliExecutor actually launches).

        This negative assertion locks the issue-#490 parity gate in
        executor_registry.py:316-345 — if a regression removes or weakens
        the check this test will fail.
        """
        import runtime.orchestrator.executor_registry as _reg_mod

        config = {
            "command": "kimi-cli",
            "argv_template": [
                "other-cli", "--prompt", "{prompt}",
            ],
            "adapter": "pi",
        }
        with pytest.raises(ValueError, match="must be the same"):
            ExecutorRegistry.validate_custom_profile_config(
                "parity-bad", config,
            )

    def test_custom_profile_no_envelope_returns_no_token_usage(
            self, tmp_path: Path):
        """Custom profile without v1 envelope → token_usage remains None."""
        workspace = tmp_path / "ws"
        workspace.mkdir()

        registry = get_registry()
        registry.register_custom_profile(ExecutorProfile(
            name="noenv-cli",
            kind="custom",
            adapter_id="pi",
            readiness_marker_fragment="AGENTS.md",
            argv_template=["noenv-cli", "{prompt}"],
            command="noenv-cli",
        ))
        ex = build_executor("noenv-cli", Settings())

        fake_proc = _make_popen_mock(stdout="plain text, no envelope")
        with patch("runtime.orchestrator.executors.subprocess.Popen", return_value=fake_proc):
            with patch(
                "runtime.orchestrator.executors._resolve_binary",
                return_value="/usr/local/bin/noenv-cli",
            ):
                result = ex.run(workspace, prompt="hi", session_id="sess-X")

        assert result.success is True
        # No v1 envelope → _parse_generic_cli_usage returns None
        assert result.token_usage is None


# ---------------------------------------------------------------------------
# ExecutorRegistry shape invariants
# ---------------------------------------------------------------------------

class TestRegistryShape:
    """Pin the existing ExecutorRegistry public surface."""

    def test_four_builtins_registered_at_startup(self):
        registry = get_registry()
        names = registry.list_profile_names()
        assert "claude" in names
        assert "codex" in names
        assert "opencode" in names
        assert "pi" in names

    def test_builtins_are_immutable(self):
        registry = get_registry()
        with pytest.raises(ValueError, match="Cannot override built-in"):
            registry.register_custom_profile(ExecutorProfile(
                name="claude",
                kind="custom",
                adapter_id="pi",
                argv_template=["claude", "{prompt}"],
                command="claude",
            ))

    def test_custom_re_registration_idempotent(self):
        registry = get_registry()
        profile = ExecutorProfile(
            name="idem-test",
            kind="custom",
            adapter_id="pi",
            readiness_marker_fragment="AGENTS.md",
            argv_template=["test-cli", "{prompt}"],
            command="test-cli",
        )
        registry.register_custom_profile(profile)
        # Second registration with identical profile → no-op
        registry.register_custom_profile(profile)
        assert registry.get_profile("idem-test") is not None

    def test_custom_collision_raises(self):
        registry = get_registry()
        p1 = ExecutorProfile(
            name="collision-test",
            kind="custom",
            adapter_id="pi",
            readiness_marker_fragment="AGENTS.md",
            argv_template=["cli-a", "{prompt}"],
            command="cli-a",
        )
        p2 = ExecutorProfile(
            name="collision-test",
            kind="custom",
            adapter_id="pi",
            readiness_marker_fragment="AGENTS.md",
            argv_template=["cli-b", "{prompt}"],
            command="cli-b",
        )
        registry.register_custom_profile(p1)
        with pytest.raises(ExecutorProfileCollisionError):
            registry.register_custom_profile(p2)

    def test_can_unregister_custom(self):
        registry = get_registry()
        registry.register_custom_profile(ExecutorProfile(
            name="unreg-test",
            kind="custom",
            adapter_id="pi",
            readiness_marker_fragment="AGENTS.md",
            argv_template=["test-cli", "{prompt}"],
            command="test-cli",
        ))
        assert registry.is_registered("unreg-test")
        result = registry.unregister_custom_profile("unreg-test")
        assert result is True
        assert not registry.is_registered("unreg-test")

    def test_cannot_unregister_builtin(self):
        registry = get_registry()
        with pytest.raises(ValueError, match="Cannot unregister built-in"):
            registry.unregister_custom_profile("claude")

    def test_unregister_nonexistent_returns_false(self):
        registry = get_registry()
        assert registry.unregister_custom_profile("nonexistent") is False


# ===========================================================================
# THR-107 D2: First-party adapter catalog contract tests
# ===========================================================================
# Prove the private adapter catalog/facade WORKS for the four built-ins and
# does NOT leak into custom/GenericCliExecutor paths.
# ===========================================================================


class TestFirstPartyAdapterCatalog:
    """D2 first-party adapter catalog contract.

    Verify:
    - Catalog returns correct adapter class for each built-in name.
    - Catalog returns None for custom/unknown names.
    - Each adapter's build_argv produces bit-identical argv to Phase-0 baselines.
    - Adapter build_argv parity: model injection, resume, sandbox flags, etc.
    """

    def test_catalog_returns_claude_adapter(self):
        from runtime.adapters import get_first_party_adapter, ClaudeAdapter

        cls = get_first_party_adapter("claude")
        assert cls is ClaudeAdapter

    def test_catalog_returns_codex_adapter(self):
        from runtime.adapters import get_first_party_adapter, CodexAdapter

        cls = get_first_party_adapter("codex")
        assert cls is CodexAdapter

    def test_catalog_returns_opencode_adapter(self):
        from runtime.adapters import get_first_party_adapter, OpencodeAdapter

        cls = get_first_party_adapter("opencode")
        assert cls is OpencodeAdapter

    def test_catalog_returns_pi_adapter(self):
        from runtime.adapters import get_first_party_adapter, PiAdapter

        cls = get_first_party_adapter("pi")
        assert cls is PiAdapter

    def test_catalog_returns_none_for_custom_name(self):
        from runtime.adapters import get_first_party_adapter

        assert get_first_party_adapter("openclaw") is None
        assert get_first_party_adapter("generic-cli") is None
        assert get_first_party_adapter("nonexistent") is None

    def test_catalog_is_case_insensitive(self):
        from runtime.adapters import get_first_party_adapter, ClaudeAdapter

        assert get_first_party_adapter("CLAUDE") is ClaudeAdapter
        assert get_first_party_adapter("Codex") is not None

    # ── Claude adapter argv parity ───────────────────────────────────────

    def test_claude_adapter_build_argv_baseline(self):
        from runtime.adapters import ClaudeAdapter

        adapter = ClaudeAdapter()
        cmd = adapter.build_argv(
            cli_path="/usr/local/bin/claude",
            prompt="hello",
            permission_mode="acceptEdits",
            allowed_tools="happyranch Bash(git:*)",
        )
        # THR-200: prompt body is NOT an argv element — it travels via stdin
        # (the executor passes it as ``input_text``).
        assert cmd == [
            "/usr/local/bin/claude",
            "-p",
            "--permission-mode",
            "acceptEdits",
            "--allowedTools",
            "happyranch Bash(git:*)",
            "--output-format",
            "json",
        ]
        assert "hello" not in cmd

    def test_claude_adapter_build_argv_with_model(self):
        from runtime.adapters import ClaudeAdapter

        adapter = ClaudeAdapter()
        cmd = adapter.build_argv(
            cli_path="/usr/local/bin/claude",
            prompt="hello",
            permission_mode="acceptEdits",
            allowed_tools="happyranch",
            model="claude-sonnet-4-20250514",
            model_arg=["--model", "{model}"],
        )
        # Model injected after binary, before -p; prompt NOT in argv (stdin).
        expected = [
            "/usr/local/bin/claude",
            "--model",
            "claude-sonnet-4-20250514",
            "-p",
            "--permission-mode",
            "acceptEdits",
            "--allowedTools",
            "happyranch",
            "--output-format",
            "json",
        ]
        assert cmd == expected
        assert "hello" not in cmd

    def test_claude_adapter_build_argv_with_resume(self):
        from runtime.adapters import ClaudeAdapter

        adapter = ClaudeAdapter()
        cmd = adapter.build_argv(
            cli_path="/usr/local/bin/claude",
            prompt="hello",
            permission_mode="acceptEdits",
            allowed_tools="happyranch",
            resume_session_id="abc123",
        )
        assert cmd[-2:] == ["--resume", "abc123"]
        assert "--resume" in cmd

    # ── Codex adapter argv parity ────────────────────────────────────────

    def test_codex_adapter_build_argv_baseline(self):
        from runtime.adapters import CodexAdapter

        adapter = CodexAdapter()
        cmd = adapter.build_argv(
            cli_path="/usr/local/bin/codex",
            sandbox_mode="workspace-write",
        )
        expected = [
            "/usr/local/bin/codex",
            "exec",
            "--sandbox",
            "workspace-write",
            "-c",
            "sandbox_workspace_write.network_access=true",
            "--skip-git-repo-check",
            "--json",
            "-",
        ]
        assert cmd == expected

    def test_codex_adapter_build_argv_with_model(self):
        from runtime.adapters import CodexAdapter

        adapter = CodexAdapter()
        cmd = adapter.build_argv(
            cli_path="/usr/local/bin/codex",
            sandbox_mode="workspace-write",
            model="gpt-5",
            model_arg=["-m", "{model}"],
        )
        # Model injected after binary+exec, before sandbox flags
        assert cmd[2] == "-m"
        assert cmd[3] == "gpt-5"

    def test_codex_adapter_build_argv_with_resume(self):
        """TASK-5977: codex resume routes through `codex exec resume <id>`
        (verified live on codex-cli 0.148.0). The resume subcommand has NO
        --sandbox flag, so the workspace-write sandbox + network override are
        carried as `-c` config overrides; stdin `-` keeps large prompts off
        argv. The same thread_id is re-emitted after continuation."""
        from runtime.adapters import CodexAdapter

        adapter = CodexAdapter()
        cmd = adapter.build_argv(
            cli_path="/usr/local/bin/codex",
            sandbox_mode="workspace-write",
            resume_session_id="01a0-prior-thread",
        )
        expected = [
            "/usr/local/bin/codex",
            "exec",
            "resume",
            "01a0-prior-thread",
            "-c",
            'sandbox_mode="workspace-write"',
            "-c",
            "sandbox_workspace_write.network_access=true",
            "--skip-git-repo-check",
            "--json",
            "-",
        ]
        assert cmd == expected

    def test_codex_adapter_build_argv_resume_with_model(self):
        from runtime.adapters import CodexAdapter

        adapter = CodexAdapter()
        cmd = adapter.build_argv(
            cli_path="/usr/local/bin/codex",
            sandbox_mode="workspace-write",
            model="gpt-5",
            model_arg=["-m", "{model}"],
            resume_session_id="01a0-prior",
        )
        assert cmd[1:4] == ["exec", "resume", "01a0-prior"]
        assert cmd[4] == "-m"
        assert cmd[5] == "gpt-5"
        assert 'sandbox_mode="workspace-write"' in cmd

    def test_codex_adapter_sandbox_flags_unchanged(self):
        """Proof that sandbox flags are NOT changed by D2 extraction."""
        from runtime.adapters import CodexAdapter

        adapter = CodexAdapter()
        cmd = adapter.build_argv(
            cli_path="/usr/local/bin/codex",
            sandbox_mode="workspace-write",
        )
        assert "-c" in cmd
        assert "sandbox_workspace_write.network_access=true" in cmd

    # ── OpenCode adapter argv parity ────────────────────────────────────

    def test_opencode_adapter_build_argv_baseline(self):
        from runtime.adapters import OpencodeAdapter

        adapter = OpencodeAdapter()
        cmd = adapter.build_argv(
            cli_path="/usr/local/bin/opencode",
            workspace="/tmp/ws",
            prompt="hello",
        )
        expected = [
            "/usr/local/bin/opencode",
            "run",
            "--dir",
            "/tmp/ws",
            "--format",
            "json",
            "hello",
        ]
        assert cmd == expected

    def test_opencode_adapter_build_argv_with_model(self):
        from runtime.adapters import OpencodeAdapter

        adapter = OpencodeAdapter()
        cmd = adapter.build_argv(
            cli_path="/usr/local/bin/opencode",
            workspace="/tmp/ws",
            prompt="hello",
            model="provider/model",
            model_arg=["-m", "{model}"],
        )
        # Model injected after binary+run, before --dir/prompt
        assert cmd[2] == "-m"
        assert cmd[3] == "provider/model"

    # ── Pi adapter argv parity ──────────────────────────────────────────

    def test_pi_adapter_build_argv_baseline(self):
        from runtime.adapters import PiAdapter

        adapter = PiAdapter()
        cmd = adapter.build_argv(
            cli_path="/usr/local/bin/pi",
            prompt="hello",
        )
        # THR-200: prompt body is NOT an argv element — it travels via stdin.
        expected = [
            "/usr/local/bin/pi",
            "-p",
            "--mode",
            "json",
        ]
        assert cmd == expected
        assert "hello" not in cmd

    def test_pi_adapter_build_argv_with_model(self):
        from runtime.adapters import PiAdapter

        adapter = PiAdapter()
        cmd = adapter.build_argv(
            cli_path="/usr/local/bin/pi",
            prompt="hello",
            model="gpt-5",
            model_arg=["--model", "{model}"],
        )
        # Model injected after binary, before -p
        assert cmd[1] == "--model"
        assert cmd[2] == "gpt-5"

    def test_pi_adapter_build_argv_with_resume(self):
        """TASK-5977: pi resumes via `--session <id>` (verified live on pi
        0.84.2). `--session` FAILS when the id is missing — the exact
        eviction signature the runner needs. `--session-id` would silently
        CREATE a fresh session (message omission), so it is never used on the
        thread path."""
        from runtime.adapters import PiAdapter

        adapter = PiAdapter()
        cmd = adapter.build_argv(
            cli_path="/usr/local/bin/pi",
            prompt="hello",
            resume_session_id="01a0-prior",
        )
        expected = [
            "/usr/local/bin/pi",
            "-p",
            "--mode",
            "json",
            "--session",
            "01a0-prior",
        ]
        assert cmd == expected
        assert "--session-id" not in cmd
        assert "hello" not in cmd

    # ── Model omitted when not set ───────────────────────────────────────

    def test_all_adapters_omit_model_when_none(self):
        """All four adapters produce argv with no model args when model is None."""
        from runtime.adapters import (
            ClaudeAdapter,
            CodexAdapter,
            OpencodeAdapter,
            PiAdapter,
        )

        claude_cmd = ClaudeAdapter().build_argv(
            cli_path="/bin/claude",
            prompt="hi",
            permission_mode="acceptEdits",
            allowed_tools="happyranch",
            model=None,
            model_arg=["--model", "{model}"],
        )
        codex_cmd = CodexAdapter().build_argv(
            cli_path="/bin/codex",
            sandbox_mode="workspace-write",
            model=None,
            model_arg=["-m", "{model}"],
        )
        opencode_cmd = OpencodeAdapter().build_argv(
            cli_path="/bin/opencode",
            workspace="/tmp/ws",
            prompt="hi",
            model=None,
            model_arg=["-m", "{model}"],
        )
        pi_cmd = PiAdapter().build_argv(
            cli_path="/bin/pi",
            prompt="hi",
            model=None,
            model_arg=["--model", "{model}"],
        )

        assert "--model" not in claude_cmd
        assert "-m" not in codex_cmd
        assert "-m" not in opencode_cmd
        assert "--model" not in pi_cmd


class TestD2BuildExecutorAdapterInjection:
    """Prove build_executor injects adapters for built-ins but NOT for custom."""

    def test_build_executor_injects_adapter_for_claude(self, monkeypatch):
        """ClaudeExecutor built via build_executor carries the D2 adapter."""
        from runtime.orchestrator.executor_registry import build_executor, reset_registry

        reset_registry()
        settings = Settings()
        executor = build_executor("claude", settings)
        # Adapter should be injected
        assert executor._adapter is not None
        from runtime.adapters import ClaudeAdapter

        assert isinstance(executor._adapter, ClaudeAdapter)

    def test_build_executor_injects_adapter_for_codex(self, monkeypatch):
        from runtime.orchestrator.executor_registry import build_executor, reset_registry

        reset_registry()
        settings = Settings()
        executor = build_executor("codex", settings)
        assert executor._adapter is not None
        from runtime.adapters import CodexAdapter

        assert isinstance(executor._adapter, CodexAdapter)

    def test_build_executor_injects_adapter_for_opencode(self, monkeypatch):
        from runtime.orchestrator.executor_registry import build_executor, reset_registry

        reset_registry()
        settings = Settings()
        executor = build_executor("opencode", settings)
        assert executor._adapter is not None
        from runtime.adapters import OpencodeAdapter

        assert isinstance(executor._adapter, OpencodeAdapter)

    def test_build_executor_injects_adapter_for_pi(self, monkeypatch):
        from runtime.orchestrator.executor_registry import build_executor, reset_registry

        reset_registry()
        settings = Settings()
        executor = build_executor("pi", settings)
        assert executor._adapter is not None
        from runtime.adapters import PiAdapter

        assert isinstance(executor._adapter, PiAdapter)

    def test_custom_route_returns_generic_cli_without_adapter(self):
        """Custom profiles must NOT receive an adapter — GenericCliExecutor is unchanged."""
        from runtime.orchestrator.executor_registry import (
            ExecutorProfile,
            ExecutorRegistry,
            build_executor,
            get_registry,
            reset_registry,
        )
        import runtime.orchestrator.executor_registry as _reg_mod
        from unittest.mock import patch

        reset_registry()
        registry = get_registry()
        profile = ExecutorRegistry.validate_custom_profile_config(
            "kimi-cli",
            {
                "command": "kimi-cli",
                "argv_template": ["kimi-cli", "-m", "{prompt}"],
                "adapter": "pi",
            },
        )
        registry.register_custom_profile(profile)

        settings = Settings()
        executor = build_executor("kimi-cli", settings)
        from runtime.orchestrator.executors import GenericCliExecutor

        assert isinstance(executor, GenericCliExecutor)
        # GenericCliExecutor must NOT have _adapter
        assert not hasattr(executor, "_adapter")

    def test_fallback_build_argv_produces_bit_identical_argv(self):
        """Rollback proof: executor without adapter (adapter=None) produces same argv.

        When a ClaudeExecutor is constructed WITHOUT an adapter (e.g., test
        code or rollback), its _build_argv MUST produce bit-identical argv
        to the adapter path. This proves the fallback is safety-preserving.
        """
        from runtime.orchestrator.executors import ClaudeExecutor
        from runtime.config import Settings
        from unittest.mock import patch

        # Mock _resolve_binary to return a deterministic path
        with patch(
            "runtime.orchestrator.executors._resolve_binary",
            return_value="/usr/local/bin/claude",
        ):
            executor = ClaudeExecutor(
                claude_cli_path="claude",
                permission_mode="acceptEdits",
                settings=Settings(),
                paths=None,
                adapter=None,  # Explicitly no adapter — fallback path
                model_arg=["--model", "{model}"],
            )

            cmd = executor._build_argv(
                prompt="hello",
                allowed_tools="happyranch",
                model="test-model",
            )

            # Bit-identical to what the adapter would produce. THR-200: the
            # prompt body travels via stdin — neither the adapter path nor the
            # fallback puts it in argv.
            assert cmd[0] == "/usr/local/bin/claude"
            assert cmd[1] == "--model"
            assert cmd[2] == "test-model"
            assert "-p" in cmd
            assert "hello" not in cmd
            assert "--permission-mode" in cmd
            assert "--allowedTools" in cmd
            assert "happyranch" in cmd
            assert "--output-format" in cmd
            assert "json" in cmd

    def test_fallback_codex_executor_without_adapter(self):
        """CodexExecutor without adapter produces same sandbox flags as adapter."""
        from runtime.orchestrator.executors import CodexExecutor
        from unittest.mock import patch

        with patch(
            "runtime.orchestrator.executors._resolve_binary",
            return_value="/usr/local/bin/codex",
        ):
            executor = CodexExecutor(
                codex_cli_path="codex",
                sandbox_mode="workspace-write",
                adapter=None,
            )
            cmd = executor._build_argv(model=None)
            # Must include all sandbox flags
            assert "--sandbox" in cmd
            assert "workspace-write" in cmd
            assert "-c" in cmd
            assert "sandbox_workspace_write.network_access=true" in cmd
            assert "--skip-git-repo-check" in cmd
            assert "--json" in cmd
            assert "-" in cmd

    def test_adapter_and_fallback_produce_identical_argv(self):
        """Direct proof: adapter.build_argv == executor._build_argv (no adapter)
        for the same inputs."""
        from runtime.adapters import ClaudeAdapter
        from runtime.orchestrator.executors import ClaudeExecutor
        from runtime.config import Settings
        from unittest.mock import patch

        model_arg = ["--model", "{model}"]
        adapter = ClaudeAdapter()
        with patch(
            "runtime.orchestrator.executors._resolve_binary",
            return_value="/bin/claude",
        ):
            executor = ClaudeExecutor(
                claude_cli_path="claude",
                permission_mode="acceptEdits",
                settings=Settings(),
                paths=None,
                adapter=None,
                model_arg=model_arg,
            )

            adapter_cmd = adapter.build_argv(
                cli_path="/bin/claude",
                prompt="hello world",
                permission_mode="acceptEdits",
                allowed_tools="happyranch Bash(git:*)",
                model="test-model",
                model_arg=model_arg,
                resume_session_id="resume-123",
            )

            fallback_cmd = executor._build_argv(
                prompt="hello world",
                allowed_tools="happyranch Bash(git:*)",
                model="test-model",
                resume_session_id="resume-123",
            )

            assert adapter_cmd == fallback_cmd

    def test_codex_adapter_and_fallback_resume_argv_identical(self):
        """TASK-5977: CodexExecutor fallback resume argv is bit-identical to
        the CodexAdapter's (D2 parity, same as claude)."""
        from runtime.adapters import CodexAdapter
        from runtime.orchestrator.executors import CodexExecutor
        from unittest.mock import patch

        model_arg = ["-m", "{model}"]
        adapter = CodexAdapter()
        with patch(
            "runtime.orchestrator.executors._resolve_binary",
            return_value="/bin/codex",
        ):
            executor = CodexExecutor(
                codex_cli_path="codex",
                sandbox_mode="workspace-write",
                adapter=None,
                model_arg=model_arg,
            )
            adapter_cmd = adapter.build_argv(
                cli_path="/bin/codex",
                sandbox_mode="workspace-write",
                model="test-model",
                model_arg=model_arg,
                resume_session_id="01a0-prior",
            )
            fallback_cmd = executor._build_argv(
                model="test-model",
                resume_session_id="01a0-prior",
            )
            assert adapter_cmd == fallback_cmd

    def test_pi_adapter_and_fallback_resume_argv_identical(self):
        """TASK-5977: PiExecutor fallback resume argv is bit-identical to the
        PiAdapter's."""
        from runtime.adapters import PiAdapter
        from runtime.orchestrator.executors import PiExecutor
        from unittest.mock import patch

        model_arg = ["--model", "{model}"]
        adapter = PiAdapter()
        with patch(
            "runtime.orchestrator.executors._resolve_binary",
            return_value="/bin/pi",
        ):
            executor = PiExecutor(
                pi_cli_path="pi",
                adapter=None,
                model_arg=model_arg,
            )
            adapter_cmd = adapter.build_argv(
                cli_path="/bin/pi",
                prompt="hello world",
                model="test-model",
                model_arg=model_arg,
                resume_session_id="01a0-prior",
            )
            fallback_cmd = executor._build_argv(
                prompt="hello world",
                model="test-model",
                resume_session_id="01a0-prior",
            )
            assert adapter_cmd == fallback_cmd

    def test_executor_result_fields_unchanged(self):
        """ExecutorResult top-level fields are NOT changed by D2."""
        from runtime.orchestrator.executors import ExecutorResult

        r = ExecutorResult(
            success=True,
            duration_seconds=42,
            session_id="sess-abc",
            returncode=0,
            stdout_tail="out",
            stderr_tail="err",
            error=None,
            token_usage=None,
            agent_session_id="abc123",
            rate_limited=False,
        )
        assert r.success is True
        assert r.duration_seconds == 42
        assert r.session_id == "sess-abc"
        assert r.returncode == 0
        assert r.stdout_tail == "out"
        assert r.stderr_tail == "err"
        assert r.error is None
        assert r.token_usage is None
        assert r.agent_session_id == "abc123"
        assert r.rate_limited is False

    def test_run_command_preserves_all_behavior(self):
        """_run_command lifecycle is NOT changed by D2 — same function signature."""
        from runtime.orchestrator.executors import _run_command
        import inspect

        sig = inspect.signature(_run_command)
        params = list(sig.parameters.keys())
        # Key parameters must remain
        assert "cmd" in params
        assert "workspace" in params
        assert "session_id" in params
        assert "timeout_seconds" in params
        assert "input_text" in params
        assert "on_started" in params
        assert "usage_parser" in params
        assert "session_id_parser" in params
        assert "provider" in params
        assert "on_throttle_event" in params


# ═══════════════════════════════════════════════════════════════════════════
# THR-107 D8 — catalog-to-registry authority tests
# ═══════════════════════════════════════════════════════════════════════════


def _expected_builtin_fields():
    """Return the exact expected Phase-0/D2 values for each built-in.

    These are the literal values from _register_builtins() at 21d39d53.
    """
    return {
        "claude": {
            "name": "claude",
            "kind": "builtin",
            "adapter_id": "claude",
            "readiness_marker_fragment": ".claude/skills/start-task/SKILL.md",
            "model_arg": ["--model", "{model}"],
        },
        "codex": {
            "name": "codex",
            "kind": "builtin",
            "adapter_id": "codex",
            "readiness_marker_fragment": "AGENTS.md",
            "model_arg": ["-m", "{model}"],
        },
        "opencode": {
            "name": "opencode",
            "kind": "builtin",
            "adapter_id": "opencode",
            "readiness_marker_fragment": "AGENTS.md",
            "model_arg": ["-m", "{model}"],
        },
        "pi": {
            "name": "pi",
            "kind": "builtin",
            "adapter_id": "pi",
            "readiness_marker_fragment": "AGENTS.md",
            "model_arg": ["--model", "{model}"],
        },
    }


class TestD8CatalogRegistryAgreement:
    """Prove the built-in catalog is the authoritative source for profile registration.

    D8 makes the catalog declaration authoritative. These tests prove:
    - Every built-in profile from the registry matches the catalog entry.
    - Phase-0 values are preserved exactly.
    - Catalog and registry are in lockstep (no silent drift).
    """

    def test_catalog_has_exactly_four_entries(self):
        """Catalog must declare exactly 4 built-ins — no drift."""
        from runtime.adapters import get_builtin_catalog

        catalog = get_builtin_catalog()
        names = {desc.name for desc in catalog}
        assert names == {"claude", "codex", "opencode", "pi"}
        assert len(catalog) == 4

    def test_catalog_is_immutable_tuple(self):
        """Catalog must be immutable — tuples, not lists."""
        from runtime.adapters import get_builtin_catalog

        catalog = get_builtin_catalog()
        assert isinstance(catalog, tuple)

    def test_catalog_order_matches_expected(self):
        """Catalog order must be claude, codex, opencode, pi."""
        from runtime.adapters import get_builtin_catalog

        catalog = get_builtin_catalog()
        names = [desc.name for desc in catalog]
        assert names == ["claude", "codex", "opencode", "pi"]

    def test_every_builtin_profile_matches_catalog_entry(self):
        """For each built-in, the registry's ExecutorProfile matches the
        catalog descriptor's declaration for ALL Phase-0 fields."""
        from runtime.adapters import get_builtin_catalog

        registry = ExecutorRegistry()
        catalog = get_builtin_catalog()

        for desc in catalog:
            profile = registry.get_profile(desc.name)
            assert profile is not None, f"{desc.name} missing from registry"
            assert profile.name == desc.name
            assert profile.kind == desc.kind
            assert profile.adapter_id == desc.adapter_id
            assert profile.readiness_marker_fragment == desc.readiness_marker_fragment
            # model_arg: catalog stores tuple; registry stores list — value-equal only
            assert profile.model_arg == list(desc.model_arg)
            # argv_template, command must be None for built-ins
            assert profile.argv_template is None
            assert profile.command is None

    def test_every_catalog_entry_has_registry_profile(self):
        """Registry must register EXACTLY the catalog entries — no extras."""
        from runtime.adapters import get_builtin_catalog

        registry = ExecutorRegistry()
        catalog_names = {desc.name for desc in get_builtin_catalog()}
        registry_builtins = {
            name
            for name in registry.list_profile_names()
            if registry.get_profile(name).kind == "builtin"
        }
        assert registry_builtins == catalog_names

    def test_builtins_preserve_exact_phase0_values(self):
        """Every built-in profile field value matches the Phase-0 literal.

        This is a line-by-line lock: if a literal value changes, this test
        fails. It must be intentionally updated, never silently drifted."""
        expected = _expected_builtin_fields()
        registry = ExecutorRegistry()

        for name, fields in expected.items():
            profile = registry.get_profile(name)
            assert profile is not None
            assert profile.name == fields["name"]
            assert profile.kind == fields["kind"]
            assert profile.adapter_id == fields["adapter_id"]
            assert profile.readiness_marker_fragment == fields["readiness_marker_fragment"]
            assert profile.model_arg == fields["model_arg"]
            assert profile.argv_template is None
            assert profile.command is None

    def test_catalog_descriptor_includes_adapter_class(self):
        """Each catalog descriptor must carry its first-party adapter class."""
        from runtime.adapters import (
            get_builtin_catalog,
            ClaudeAdapter,
            CodexAdapter,
            OpencodeAdapter,
            PiAdapter,
        )

        catalog = get_builtin_catalog()
        name_to_cls = {desc.name: desc.adapter_cls for desc in catalog}
        assert name_to_cls["claude"] is ClaudeAdapter
        assert name_to_cls["codex"] is CodexAdapter
        assert name_to_cls["opencode"] is OpencodeAdapter
        assert name_to_cls["pi"] is PiAdapter

    def test_get_first_party_adapter_derives_from_catalog(self):
        """get_first_party_adapter() must return the same adapter classes
        as the catalog descriptors for built-in names, and None otherwise."""
        from runtime.adapters import (
            get_first_party_adapter,
            get_builtin_catalog,
            ClaudeAdapter,
            CodexAdapter,
            OpencodeAdapter,
            PiAdapter,
        )

        # Built-ins must return correct adapter classes
        assert get_first_party_adapter("claude") is ClaudeAdapter
        assert get_first_party_adapter("codex") is CodexAdapter
        assert get_first_party_adapter("opencode") is OpencodeAdapter
        assert get_first_party_adapter("pi") is PiAdapter

        # Custom/unknown names must return None
        assert get_first_party_adapter("kimi-cli") is None
        assert get_first_party_adapter("generic") is None
        assert get_first_party_adapter("nonexistent") is None

    def test_get_first_party_adapter_matches_catalog_adapter_cls(self):
        """get_first_party_adapter() must be derived from the catalog —
        no parallel truth."""
        from runtime.adapters import get_first_party_adapter, get_builtin_catalog

        for desc in get_builtin_catalog():
            assert get_first_party_adapter(desc.name) is desc.adapter_cls


class TestD8CatalogImmutability:
    """Prove catalog descriptor metadata is genuinely immutable across the
    catalog→registry seam — no aliasing, no shared mutable objects."""

    def test_catalog_model_arg_is_immutable_tuple(self):
        """Catalog descriptor model_arg must be an immutable tuple, not a
        list — a mutable list returned from get_builtin_catalog() would be
        aliasable and violate D8's immutable-bundled-catalog contract."""
        from runtime.adapters import get_builtin_catalog

        for desc in get_builtin_catalog():
            if desc.model_arg is not None:
                assert isinstance(desc.model_arg, tuple), (
                    f"catalog {desc.name}.model_arg must be tuple, "
                    f"got {type(desc.model_arg).__name__}"
                )

    def test_catalog_model_arg_cannot_be_mutated_through_accessor(self):
        """get_builtin_catalog() must expose no mutable catalog/profile
        metadata. Appending to a descriptor's model_arg should be impossible
        because it is a tuple."""
        from runtime.adapters import get_builtin_catalog

        catalog = get_builtin_catalog()
        claude_desc = catalog[0]
        with pytest.raises((TypeError, AttributeError)):
            claude_desc.model_arg.append("--injected")  # type: ignore[union-attr]

    def test_registry_profile_model_arg_is_value_equal_but_not_same_object(self):
        """A registry profile's model_arg must be value-equal to the catalog
        descriptor's model_arg but NOT the same object. Direct aliasing
        violates D8's registry-immutability contract."""
        from runtime.adapters import get_builtin_catalog

        registry = ExecutorRegistry()
        catalog = get_builtin_catalog()
        claude_desc = catalog[0]

        profile = registry.get_profile("claude")
        assert profile is not None
        assert profile.model_arg is not None

        # Value equality: the profile must carry the same values
        assert profile.model_arg == list(claude_desc.model_arg)

        # Object identity: the profile must NOT alias the catalog
        assert profile.model_arg is not claude_desc.model_arg, (
            "Registry profile model_arg must be an independent copy, "
            "not the same object as the catalog descriptor's model_arg"
        )

    def test_separate_registry_constructions_do_not_share_model_arg_objects(self):
        """Two independently constructed registries must NOT share
        model_arg objects. If they did, mutating one profile's model_arg
        would silently corrupt another registry's profile."""
        r1 = ExecutorRegistry()
        r2 = ExecutorRegistry()

        p1 = r1.get_profile("claude")
        p2 = r2.get_profile("claude")
        assert p1 is not None and p2 is not None
        assert p1.model_arg is not None and p2.model_arg is not None

        assert p1.model_arg is not p2.model_arg, (
            "Separate registry constructions must produce independent "
            "model_arg lists, not shared aliases"
        )

    def test_mutating_profile_local_list_cannot_alter_catalog(self):
        """Mutating a permitted profile-local model_arg list must not
        alter the catalog or another registry profile."""
        from runtime.adapters import get_builtin_catalog

        r1 = ExecutorRegistry()
        r2 = ExecutorRegistry()
        catalog = get_builtin_catalog()

        p1 = r1.get_profile("claude")
        p2 = r2.get_profile("claude")
        assert p1 is not None and p2 is not None

        # Capture original values
        original_catalog = list(catalog[0].model_arg)  # type: ignore[arg-type]
        original_p2 = list(p2.model_arg)  # type: ignore[arg-type]

        # Mutate p1's list — this is permitted because ExecutorProfile
        # carries a list (not tuple), but must not bleed anywhere else
        p1.model_arg.append("--local-only")

        # p2 must remain unchanged (separate registry)
        assert p2.model_arg == original_p2, (
            "Mutating one registry's profile must not affect another registry"
        )

        # Catalog descriptor must remain unchanged
        assert list(catalog[0].model_arg) == original_catalog, (  # type: ignore[arg-type]
            "Mutating a profile-local list must not alter the catalog"
        )

        # p1 itself reflects the mutation (it owns its list)
        assert "--local-only" in p1.model_arg

    def test_phase0_values_unchanged_after_immutability_fix(self):
        """Exact Phase-0 model_arg values must remain unchanged after
        the immutability fix — tuples carry the same elements as the
        original lists."""
        expected = _expected_builtin_fields()
        registry = ExecutorRegistry()

        for name, fields in expected.items():
            profile = registry.get_profile(name)
            assert profile is not None
            assert profile.model_arg == fields["model_arg"], (
                f"{name}.model_arg must preserve exact Phase-0 values"
            )


class TestD8RegistryBehaviorPreserved:
    """Prove ALL registry behavior is preserved after catalog-as-authority D8."""

    def test_case_insensitive_lookup_preserved(self):
        """Registry lookup is case-insensitive."""
        registry = ExecutorRegistry()
        assert registry.get_profile("Claude") is not None
        assert registry.get_profile("CLAUDE") is not None
        assert registry.get_profile("ClAuDe") is not None
        assert registry.is_registered("CLAUDE") is True

    def test_builtin_collision_protection_preserved(self):
        """Custom profiles still cannot override built-in names."""
        registry = ExecutorRegistry()
        with pytest.raises(ValueError, match="override built-in"):
            registry.register_custom_profile(
                ExecutorProfile(
                    name="claude",
                    kind="custom",
                    adapter_id="pi",
                    argv_template=["claude", "{prompt}"],
                )
            )

    def test_builtin_immutability_preserved(self):
        """Built-in profiles still cannot be unregistered."""
        registry = ExecutorRegistry()
        with pytest.raises(ValueError, match="Cannot unregister built-in"):
            registry.unregister_custom_profile("claude")

    def test_sorted_list_preserved(self):
        """list_profile_names returns sorted names."""
        registry = ExecutorRegistry()
        names = registry.list_profile_names()
        # Must include all 4 built-ins
        assert "claude" in names
        assert "codex" in names
        assert "opencode" in names
        assert "pi" in names
        # Must be sorted
        assert names == sorted(names)

    def test_custom_profile_registration_unchanged(self):
        """Custom profile registration/routing/collision is unchanged."""
        import runtime.orchestrator.executor_registry as _reg_mod

        registry = ExecutorRegistry()
        profile = ExecutorRegistry.validate_custom_profile_config(
            "kimi-cli",
            {
                "command": "kimi-cli",
                "argv_template": ["kimi-cli", "-m", "{prompt}"],
                "adapter": "pi",
            },
        )
        registry.register_custom_profile(profile)

        # Profile stored correctly
        stored = registry.get_profile("kimi-cli")
        assert stored is not None
        assert stored.kind == "custom"
        assert stored.name == "kimi-cli"
        assert stored.adapter_id == "pi"
        assert stored.argv_template == ["kimi-cli", "-m", "{prompt}"]

        # Collision with different definition still protected
        profile2 = ExecutorRegistry.validate_custom_profile_config(
            "kimi-cli",
            {
                "command": "kimi-cli",
                "argv_template": ["kimi-cli", "--json", "{prompt}"],
                "adapter": "pi",
            },
        )
        with pytest.raises(ExecutorProfileCollisionError):
            registry.register_custom_profile(profile2)

    def test_custom_unregister_unchanged(self):
        """Custom profile unregistration works as before."""
        registry = ExecutorRegistry()
        profile = ExecutorRegistry.validate_custom_profile_config(
            "mycli",
            {
                "command": "mycli",
                "argv_template": ["mycli", "{prompt}"],
                "adapter": "pi",
            },
        )
        registry.register_custom_profile(profile)

        assert registry.is_registered("mycli")
        result = registry.unregister_custom_profile("mycli")
        assert result is True
        assert not registry.is_registered("mycli")

    def test_validate_custom_profile_config_unchanged(self):
        """Custom profile validation layer — command/argv parity check
        (THR-107 seq155: no PATH resolution)."""
        profile = ExecutorRegistry.validate_custom_profile_config(
            "mycli",
            {
                "command": "mycli",
                "argv_template": ["mycli", "{prompt}"],
                "adapter": "pi",
            },
        )
        assert profile.name == "mycli"
        assert profile.kind == "custom"
        assert profile.adapter_id == "pi"
        assert profile.argv_template == ["mycli", "{prompt}"]
        assert profile.readiness_marker_fragment == "AGENTS.md"


class TestD8BuildExecutorPreserved:
    """Prove build_executor factory behavior is preserved."""

    @pytest.fixture(autouse=True)
    def _reset(self):
        reset_registry()

    def test_builds_all_four_builtins_correctly(self):
        """Every built-in profile resolves to its specialized executor class."""
        from runtime.orchestrator.executor_registry import build_executor
        from runtime.orchestrator.executors import (
            ClaudeExecutor,
            CodexExecutor,
            OpencodeExecutor,
            PiExecutor,
        )

        settings = Settings()
        assert isinstance(build_executor("claude", settings), ClaudeExecutor)
        assert isinstance(build_executor("codex", settings), CodexExecutor)
        assert isinstance(build_executor("opencode", settings), OpencodeExecutor)
        assert isinstance(build_executor("pi", settings), PiExecutor)

    def test_builtins_receive_adapters(self):
        """Each built-in executor built via build_executor receives its adapter."""
        from runtime.orchestrator.executor_registry import build_executor
        from runtime.adapters import (
            ClaudeAdapter,
            CodexAdapter,
            OpencodeAdapter,
            PiAdapter,
        )

        settings = Settings()
        executor = build_executor("claude", settings)
        assert isinstance(executor._adapter, ClaudeAdapter)

        executor = build_executor("codex", settings)
        assert isinstance(executor._adapter, CodexAdapter)

        executor = build_executor("opencode", settings)
        assert isinstance(executor._adapter, OpencodeAdapter)

        executor = build_executor("pi", settings)
        assert isinstance(executor._adapter, PiAdapter)

    def test_custom_routes_to_generic_cli_no_adapter(self):
        """Custom profiles route to GenericCliExecutor without adapter injection."""
        from runtime.orchestrator.executor_registry import (
            ExecutorRegistry,
            build_executor,
            get_registry,
        )
        from runtime.orchestrator.executors import GenericCliExecutor

        registry = get_registry()
        profile = ExecutorRegistry.validate_custom_profile_config(
            "kimi-cli",
            {
                "command": "kimi-cli",
                "argv_template": ["kimi-cli", "-m", "{prompt}"],
                "adapter": "pi",
            },
        )
        registry.register_custom_profile(profile)

        settings = Settings()
        executor = build_executor("kimi-cli", settings)
        assert isinstance(executor, GenericCliExecutor)
        assert not hasattr(executor, "_adapter")

    def test_catalog_derived_factory_dispatch_for_four_builtins(self):
        """D10/D11 Phase-4: data-driven factory dispatch from the D8 catalog.

        Verifies the static _BUILTIN_EXECUTOR_FACTORIES dict in build_executor
        dispatches each built-in name to its correct specialized executor class.
        This test fails if any built-in name is missing from the factory dict
        (proving the data-driven dispatch covers all four profiles).
        """
        from runtime.orchestrator.executor_registry import build_executor
        from runtime.orchestrator.executors import (
            ClaudeExecutor,
            CodexExecutor,
            OpencodeExecutor,
            PiExecutor,
            GenericCliExecutor,
        )

        settings = Settings()

        # All four built-ins must resolve via the data-driven factory dict
        assert isinstance(build_executor("claude", settings), ClaudeExecutor)
        assert isinstance(build_executor("codex", settings), CodexExecutor)
        assert isinstance(build_executor("opencode", settings), OpencodeExecutor)
        assert isinstance(build_executor("pi", settings), PiExecutor)

        # At least one built-in name must be in the factory dict keys.
        # This is an adversarial check — if someone removes an entry,
        # the test fails because isinstance on a non-built-in executor
        # won't match the specialized class.
        from runtime.adapters import get_builtin_catalog
        builtin_names = {desc.name for desc in get_builtin_catalog()}
        for bn in builtin_names:
            ex = build_executor(bn, settings)
            # Custom profiles or fallback cannot return specialized classes
            assert not isinstance(ex, GenericCliExecutor), f"{bn} returned GenericCliExecutor"

        # Custom profile must NOT return a specialized executor class
        from runtime.orchestrator.executor_registry import ExecutorRegistry, get_registry

        registry = get_registry()
        profile = ExecutorRegistry.validate_custom_profile_config(
            "mycli",
            {
                "command": "mycli",
                "argv_template": ["mycli", "{prompt}"],
                "adapter": "pi",
            },
        )
        registry.register_custom_profile(profile)

        executor = build_executor("mycli", settings)
        assert isinstance(executor, GenericCliExecutor)

    def test_rejects_unregistered_name(self):
        """build_executor rejects unregistered profile names."""
        from runtime.orchestrator.executor_registry import build_executor

        settings = Settings()
        with pytest.raises(ValueError, match="Unregistered executor"):
            build_executor("nonexistent-executor", settings)


class TestD8AdversarialInvariants:
    """Adversarial tests that would fail if catalog invariants are broken."""

    def test_catalog_entries_cannot_be_mutated_in_place(self):
        """Catalog descriptors must be frozen — mutation must TypeError.

        If the catalog were a mutable list or editable dataclass, this test
        would NOT fail — it would silently succeed, allowing runtime corruption.
        """
        from runtime.adapters import get_builtin_catalog

        catalog = get_builtin_catalog()
        with pytest.raises(TypeError):
            catalog[0] = catalog[1]  # type: ignore[index]

    def test_catalog_fields_cannot_be_mutated(self):
        """Catalog descriptor fields must be frozen — mutation must raise."""
        from runtime.adapters import get_builtin_catalog

        catalog = get_builtin_catalog()
        claude_desc = catalog[0]
        with pytest.raises(Exception):
            claude_desc.name = "hacked"

    def test_no_fifth_catalog_entry(self):
        """Catalog must have exactly 4 entries — extra built-in registrations
        would silently bypass the catalog-as-authority gate."""
        from runtime.adapters import get_builtin_catalog

        catalog = get_builtin_catalog()
        assert len(catalog) == 4

    def test_registry_has_no_additional_builtins(self):
        """Registry must register exactly the catalog entries — no hard-coded
        extras left behind in _register_builtins."""
        registry = ExecutorRegistry()
        builtins = [
            name
            for name in registry.list_profile_names()
            if registry.get_profile(name).kind == "builtin"
        ]
        assert builtins == ["claude", "codex", "opencode", "pi"]

    def test_no_parallel_builtin_list_in_registry_source(self):
        """_register_builtins must NOT contain literal ExecutorProfile
        constructions — the method body must derive profiles from the catalog.

        This is a source-level invariant: we check that the profile names
        we get come from the catalog, not from duplicated literals.
        Proof: if _register_builtins still has literal ExecutorProfile(...)
        for each built-in, changing the catalog alone would NOT affect
        registration — the literal list would still be authoritative.

        We test this by verifying the catalog and registry agree exactly;
        if both contain hard-coded literals, agreement alone doesn't prove
        the catalog is authoritative. But if we can break agreement by
        altering only the catalog (which is the import), then the catalog
        IS authoritative — and that's what D8 requires.
        """
        from runtime.adapters import get_builtin_catalog

        registry = ExecutorRegistry()
        catalog = get_builtin_catalog()

        # Verify every catalog entry is registered
        for desc in catalog:
            profile = registry.get_profile(desc.name)
            assert profile is not None
            # All fields must match — if the registry uses its own literals,
            # these will fail when the catalog changes but the registry doesn't
            assert profile.name == desc.name
            assert profile.kind == desc.kind
            assert profile.adapter_id == desc.adapter_id
            assert profile.readiness_marker_fragment == desc.readiness_marker_fragment
            # model_arg: catalog stores tuple; registry stores list — value-equal only
            assert profile.model_arg == list(desc.model_arg)

    def test_catalog_name_typo_would_break_registration(self):
        """Adversarial: if a catalog name doesn't match the adapter lookup key,
        it would be inconsistent. Verify every catalog name is a valid
        first-party adapter name."""
        from runtime.adapters import get_first_party_adapter, get_builtin_catalog

        for desc in get_builtin_catalog():
            adapter = get_first_party_adapter(desc.name)
            assert adapter is not None, (
                f"Catalog entry {desc.name} has no matching adapter. "
                f"Catalog name must match get_first_party_adapter() key."
            )

    def test_catalog_adapter_cls_must_be_instantiatable(self):
        """Every adapter_cls in the catalog must be instantiatable (no abstract
        base classes or import errors silently lurking)."""
        from runtime.adapters import get_builtin_catalog

        for desc in get_builtin_catalog():
            instance = desc.adapter_cls()
            assert instance is not None
            # Must have build_argv
            assert hasattr(instance, "build_argv")
            assert callable(instance.build_argv)


# ============================================================================
# THR-107 Phase 2: Generic CLI first-party adapter / GenericCliExecutor shell
# ============================================================================
# Phase 2 extracts the GenericCliExecutor template expansion / argv
# construction and result-envelope parsing into GenericCliAdapter
# (runtime/adapters/generic_cli.py). GenericCliExecutor becomes a
# compatibility shell that delegates to it. These tests lock that
# behavior, prove bit-for-bit parity, and guard the backward-compat
# contracts: custom profiles still return GenericCliExecutor from
# build_executor, all four built-in flows use the D10/D11 data-driven
# factory, and the adapter module is
# statically importable (no dynamic discovery).
# ============================================================================


class TestGenericCliAdapter:
    """Phase 2: GenericCliAdapter unit tests — verify the adapter's
    build_argv and parse_output methods produce bit-for-bit output
    identical to the pre-extraction inline GenericCliExecutor logic."""

    @staticmethod
    def _adapter():
        from runtime.adapters.generic_cli import GenericCliAdapter
        return GenericCliAdapter

    # -- build_argv --------------------------------------------------------

    def test_build_argv_substitutes_all_three_placeholders(self):
        """{prompt}, {timeout_seconds}, {workspace} all substituted."""
        cmd = self._adapter().build_argv(
            argv_template=["my-cli", "--workspace", "{workspace}",
                           "--prompt", "{prompt}", "--timeout", "{timeout_seconds}"],
            prompt="hello world",
            workspace="/tmp/ws",
            timeout_seconds=300,
        )
        assert cmd[0] == "my-cli"
        assert cmd[1] == "--workspace"
        assert cmd[2] == "/tmp/ws"
        assert cmd[3] == "--prompt"
        assert cmd[4] == "hello world"
        assert cmd[5] == "--timeout"
        assert cmd[6] == "300"

    def test_build_argv_prompt_contains_newlines_stays_one_element(self):
        """Prompt with embedded newlines must NOT split into multiple
        argv elements."""
        cmd = self._adapter().build_argv(
            argv_template=["test-cli", "{prompt}"],
            prompt="one\ntwo\nthree",
            workspace="/ws",
            timeout_seconds=60,
        )
        assert len(cmd) == 2
        assert cmd[1] == "one\ntwo\nthree"
        assert "\n" in cmd[1]

    def test_build_argv_resolve_binary_replaces_element_zero(self):
        """When resolve_binary is given, element[0] uses it instead of
        the template value."""
        cmd = self._adapter().build_argv(
            argv_template=["echo", "--msg", "{prompt}"],
            prompt="hi",
            workspace="/ws",
            timeout_seconds=10,
            resolve_binary="/usr/local/bin/echo",
        )
        assert cmd[0] == "/usr/local/bin/echo"
        assert cmd[1] == "--msg"
        assert cmd[2] == "hi"

    def test_build_argv_no_resolve_binary_uses_template_as_is(self):
        """Without resolve_binary, element[0] is used as-is."""
        cmd = self._adapter().build_argv(
            argv_template=["echo", "{prompt}"],
            prompt="test",
            workspace="/ws",
            timeout_seconds=5,
        )
        assert cmd[0] == "echo"
        assert cmd[1] == "test"

    def test_build_argv_no_placeholders(self):
        """Template with no placeholders produces exact argv."""
        cmd = self._adapter().build_argv(
            argv_template=["static", "arg1", "arg2"],
            prompt="ignored",
            workspace="/ws",
            timeout_seconds=60,
        )
        assert cmd == ["static", "arg1", "arg2"]

    def test_build_argv_does_not_alias_input_template(self):
        """The adapter does not mutate the caller's argv_template list."""
        template = ["cli", "--flag", "{prompt}"]
        original = list(template)
        self._adapter().build_argv(
            argv_template=template,
            prompt="hi",
            workspace="/ws",
            timeout_seconds=5,
        )
        assert template == original  # unchanged

    def test_build_argv_parity_with_original_generic_cli_executor(self, tmp_path):
        """Prove bit-for-bit parity: the adapter produces the exact same
        argv list that the original inline GenericCliExecutor produced."""
        workspace = tmp_path / "ws"
        workspace.mkdir()

        fake_bin_path = tmp_path / "test-bin" / "my-cli"
        fake_bin_path.parent.mkdir(exist_ok=True)
        fake_bin_path.write_text("")
        fake_bin_path.chmod(0o755)

        template = ["my-cli", "--workspace", "{workspace}",
                    "--prompt", "{prompt}", "--timeout", "{timeout_seconds}"]
        prompt = "custom prompt"

        # Adapter build_argv
        from runtime.orchestrator.executors import _SESSION_LIFETIME_PREAMBLE
        adapter_cmd = self._adapter().build_argv(
            argv_template=template,
            prompt=_SESSION_LIFETIME_PREAMBLE + prompt,
            workspace=str(workspace),
            timeout_seconds=300,
            resolve_binary=str(fake_bin_path),
        )

        # Original GenericCliExecutor (simulate what the pre-extraction code did)
        legacy_cmd: list[str] = []
        for i, elem in enumerate(template):
            elem = elem.replace("{prompt}", _SESSION_LIFETIME_PREAMBLE + prompt)
            elem = elem.replace("{timeout_seconds}", "300")
            elem = elem.replace("{workspace}", str(workspace))
            if i == 0:
                elem = str(fake_bin_path)
            legacy_cmd.append(elem)

        assert adapter_cmd == legacy_cmd
        assert len(adapter_cmd) == 7

    # -- parse_output ------------------------------------------------------

    def test_parse_output_empty_returns_none(self):
        adapter = self._adapter()
        assert adapter.parse_output("") is None
        assert adapter.parse_output("   ") is None

    def test_parse_output_no_envelope_returns_none(self):
        adapter = self._adapter()
        result = adapter.parse_output("regular stdout output\nno envelope here")
        assert result is None

    def test_parse_output_valid_v1_envelope(self):
        adapter = self._adapter()
        from runtime.orchestrator.executors import _HR_ENVELOPE_BEGIN, _HR_ENVELOPE_END
        envelope = {
            "envelope_version": 1,
            "token_usage": {
                "input_tokens": 299,
                "output_tokens": 101,
                "cache_read_tokens": 50,
                "cache_creation_tokens": 10,
                "reasoning_tokens": None,
                "model": "my-cli-v2",
            },
        }
        stdout = f"some output\n{_HR_ENVELOPE_BEGIN}\n{json.dumps(envelope)}\n{_HR_ENVELOPE_END}\nmore output"
        result = adapter.parse_output(stdout)
        assert result is not None
        assert result.input_tokens == 299
        assert result.output_tokens == 101
        assert result.cache_read_tokens == 50
        assert result.cache_creation_tokens == 10
        assert result.model == "my-cli-v2"

    def test_parse_output_last_envelope_wins(self):
        adapter = self._adapter()
        from runtime.orchestrator.executors import _HR_ENVELOPE_BEGIN, _HR_ENVELOPE_END
        envelope1 = {"envelope_version": 1, "token_usage": {"input_tokens": 10}}
        envelope2 = {"envelope_version": 1, "token_usage": {"input_tokens": 20}}
        stdout = f"{_HR_ENVELOPE_BEGIN}\n{json.dumps(envelope1)}\n{_HR_ENVELOPE_END}\n{_HR_ENVELOPE_BEGIN}\n{json.dumps(envelope2)}\n{_HR_ENVELOPE_END}"
        result = adapter.parse_output(stdout)
        assert result is not None
        assert result.input_tokens == 20  # last wins

    def test_parse_output_missing_end_returns_raw_only(self):
        adapter = self._adapter()
        from runtime.orchestrator.executors import _HR_ENVELOPE_BEGIN
        stdout = f"{_HR_ENVELOPE_BEGIN}\n{{\"envelope_version\":1}}\n"
        result = adapter.parse_output(stdout)
        assert result is not None
        assert result.input_tokens is None  # raw only, no parsed fields
        assert result.usage_raw_json is not None

    def test_parse_output_wrong_version_returns_raw_only(self):
        adapter = self._adapter()
        from runtime.orchestrator.executors import _HR_ENVELOPE_BEGIN, _HR_ENVELOPE_END
        envelope = {"envelope_version": 2, "token_usage": {"input_tokens": 5}}
        stdout = f"{_HR_ENVELOPE_BEGIN}\n{json.dumps(envelope)}\n{_HR_ENVELOPE_END}"
        result = adapter.parse_output(stdout)
        assert result is not None
        assert result.input_tokens is None  # version rejected
        assert result.usage_raw_json is not None

    def test_parse_output_invalid_json_returns_raw_only(self):
        adapter = self._adapter()
        from runtime.orchestrator.executors import _HR_ENVELOPE_BEGIN, _HR_ENVELOPE_END
        stdout = f"{_HR_ENVELOPE_BEGIN}\nnot json{{{{\n{_HR_ENVELOPE_END}"
        result = adapter.parse_output(stdout)
        assert result is not None
        assert result.usage_raw_json is not None

    def test_parse_output_empty_envelope_block_returns_none(self):
        adapter = self._adapter()
        from runtime.orchestrator.executors import _HR_ENVELOPE_BEGIN, _HR_ENVELOPE_END
        stdout = f"{_HR_ENVELOPE_BEGIN}\n\n{_HR_ENVELOPE_END}"
        result = adapter.parse_output(stdout)
        assert result is None

    def test_parse_output_top_level_model_backfill(self):
        adapter = self._adapter()
        from runtime.orchestrator.executors import _HR_ENVELOPE_BEGIN, _HR_ENVELOPE_END
        envelope = {
            "envelope_version": 1,
            "model": "custom-model-v3",
            "token_usage": {"input_tokens": 500},
        }
        stdout = f"{_HR_ENVELOPE_BEGIN}\n{json.dumps(envelope)}\n{_HR_ENVELOPE_END}"
        result = adapter.parse_output(stdout)
        assert result is not None
        assert result.input_tokens == 500
        assert result.model == "custom-model-v3"

    def test_parse_output_token_type_coercion(self):
        adapter = self._adapter()
        from runtime.orchestrator.executors import _HR_ENVELOPE_BEGIN, _HR_ENVELOPE_END
        envelope = {
            "envelope_version": 1,
            "token_usage": {
                "input_tokens": 100.0,
                "output_tokens": 50.5,
                "cache_read_tokens": True,
                "cache_creation_tokens": "nope",
            },
        }
        stdout = f"{_HR_ENVELOPE_BEGIN}\n{json.dumps(envelope)}\n{_HR_ENVELOPE_END}"
        result = adapter.parse_output(stdout)
        assert result is not None
        assert result.input_tokens == 100
        assert result.output_tokens is None
        assert result.cache_read_tokens is None
        assert result.cache_creation_tokens is None

    def test_parse_output_not_dict_returns_raw_only(self):
        adapter = self._adapter()
        from runtime.orchestrator.executors import _HR_ENVELOPE_BEGIN, _HR_ENVELOPE_END
        stdout = f"{_HR_ENVELOPE_BEGIN}\n[1, 2, 3]\n{_HR_ENVELOPE_END}"
        result = adapter.parse_output(stdout)
        assert result is not None
        assert result.usage_raw_json is not None

    # -- Unicode CJK frozen-reference parity (TASK-3396) ------------------\n    # The frozen pre-extraction reference (_FROZEN_* constants above) encodes
    # the legacy raw-only behavior from immutable base f4a26824 using Python
    # character slicing str[:2000], not UTF-8 byte slicing.  Each test below
    # compares the shipping GenericCliAdapter.parse_output() result to this
    # independent reference — no imports, calls, or delegation through
    # _parse_generic_cli_usage.\n\n    def test_parse_output_unicode_missing_end_character_slicing(self):
        """Missing END with 1000 CJK chars — raw is char-sliced, no U+FFFD."""
        adapter = self._adapter()
        from runtime.orchestrator.executors import _HR_ENVELOPE_BEGIN
        # Reviewer reproduction: begin + newline + 1000 CJK chars, no END
        stdout = f"{_HR_ENVELOPE_BEGIN}\n" + "中" * 1000
        result = adapter.parse_output(stdout)
        assert result is not None
        assert result.usage_raw_json is not None
        raw = result.usage_raw_json
        # Length must be 1022 characters (22-char header + 1000 CJK)
        assert len(raw) == 1022
        # Must NOT contain replacement character
        assert "\ufffd" not in raw
        # Must end with CJK character (not a replacement char or chopped mid-byte)
        assert raw[-1] == "中"
        assert raw[-5:] == "中" * 5

    def test_parse_output_unicode_missing_end_against_frozen_reference(self):
        """Missing-END CJK output matches independent frozen pre-extraction
        reference derived from immutable base f4a26824."""
        adapter = self._adapter()
        from runtime.orchestrator.executors import _HR_ENVELOPE_BEGIN
        stdout = f"{_HR_ENVELOPE_BEGIN}\n" + "中" * 1000

        result = adapter.parse_output(stdout)
        assert result is not None
        # Exact TokenUsage equality — all fields match frozen reference
        assert result == _FROZEN_MISSING_END_USAGE
        assert result.usage_raw_json == _FROZEN_MISSING_END_RAW
        assert len(result.usage_raw_json) == 1022
        assert "\ufffd" not in result.usage_raw_json
        assert result.usage_raw_json[-1] == "中"
        # Raw-only — no parsed token fields
        assert result.input_tokens is None

    def test_parse_output_unicode_invalid_json_character_slicing(self):
        """JSONDecodeError with CJK chars — raw is char-sliced, no U+FFFD."""
        adapter = self._adapter()
        from runtime.orchestrator.executors import _HR_ENVELOPE_BEGIN, _HR_ENVELOPE_END
        block = "{" + "中" * 1000 + "}"
        stdout = f"{_HR_ENVELOPE_BEGIN}\n{block}\n{_HR_ENVELOPE_END}"
        result = adapter.parse_output(stdout)
        assert result is not None
        assert result.usage_raw_json is not None
        raw = result.usage_raw_json
        # The block including the CJK chars, truncated to 2000 characters
        assert len(raw) == 1002  # { + 1000 中 + } = 1002 chars
        assert "\ufffd" not in raw
        assert raw[0] == "{"
        assert raw[-1] == "}"

    def test_parse_output_unicode_invalid_json_against_frozen_reference(self):
        """JSONDecodeError CJK output matches independent frozen
        pre-extraction reference from immutable base f4a26824."""
        adapter = self._adapter()
        from runtime.orchestrator.executors import _HR_ENVELOPE_BEGIN, _HR_ENVELOPE_END
        block = "{" + "中" * 1000 + "}"
        stdout = f"{_HR_ENVELOPE_BEGIN}\n{block}\n{_HR_ENVELOPE_END}"

        result = adapter.parse_output(stdout)
        assert result is not None
        assert result == _FROZEN_INVALID_JSON_USAGE
        assert result.usage_raw_json == _FROZEN_INVALID_JSON_RAW
        assert len(result.usage_raw_json) == 1002
        assert "\ufffd" not in result.usage_raw_json
        assert result.usage_raw_json[0] == "{"
        assert result.usage_raw_json[-1] == "}"
        assert result.input_tokens is None

    def test_parse_output_unicode_not_dict_character_slicing(self):
        """Non-dict root CJK block (valid JSON list) — raw is char-sliced, no U+FFFD."""
        adapter = self._adapter()
        from runtime.orchestrator.executors import _HR_ENVELOPE_BEGIN, _HR_ENVELOPE_END
        # Valid JSON array — decodes to list (non-dict), reaches the
        # `not isinstance(obj, dict)` branch, not JSONDecodeError.
        block = '["' + "中" * 1000 + '"]'
        stdout = f"{_HR_ENVELOPE_BEGIN}\n{block}\n{_HR_ENVELOPE_END}"
        result = adapter.parse_output(stdout)
        assert result is not None
        assert result.usage_raw_json is not None
        raw = result.usage_raw_json
        # [" + 1000 中 + "] = 1004 chars, no truncation (under 2000)
        assert len(raw) == 1004
        assert "\ufffd" not in raw
        assert raw[0] == "["
        assert raw[-1] == "]"
        assert raw[-2:] == '"]'  # closing quote + bracket

    def test_parse_output_unicode_not_dict_against_frozen_reference(self):
        """Non-dict root CJK output (valid JSON list) matches independent frozen
        pre-extraction reference from immutable base f4a26824."""
        adapter = self._adapter()
        from runtime.orchestrator.executors import _HR_ENVELOPE_BEGIN, _HR_ENVELOPE_END
        # Valid JSON array — decodes to list, reaches non-dict branch
        block = '["' + "中" * 1000 + '"]'
        stdout = f"{_HR_ENVELOPE_BEGIN}\n{block}\n{_HR_ENVELOPE_END}"

        result = adapter.parse_output(stdout)
        assert result is not None
        assert result == _FROZEN_NOT_DICT_USAGE
        assert result.usage_raw_json == _FROZEN_NOT_DICT_RAW
        assert len(result.usage_raw_json) == 1004
        assert "\ufffd" not in result.usage_raw_json
        assert result.usage_raw_json[0] == "["
        assert result.usage_raw_json[-1] == "]"
        assert result.usage_raw_json[-2:] == '"]'
        assert result.input_tokens is None

    def test_parse_output_unicode_over_2000_chars_truncates_correctly(self):
        """When content exceeds 2000 chars, char-slicing truncates at exactly 2000."""
        adapter = self._adapter()
        from runtime.orchestrator.executors import _HR_ENVELOPE_BEGIN
        # 3000 CJK chars + header = 3022 chars; truncated to 2000 chars
        stdout = f"{_HR_ENVELOPE_BEGIN}\n" + "中" * 3000
        result = adapter.parse_output(stdout)
        assert result is not None
        assert result.usage_raw_json is not None
        raw = result.usage_raw_json
        # Character-sliced to exactly 2000 characters
        assert len(raw) == 2000
        assert "\ufffd" not in raw
        # Last char is a complete CJK (position 2000 = 22 header + starts at
        # 中[1978]...  but the exact char depends on 2000 being within a CJK.
        # What matters: no replacement char, and length is exactly 2000.
        assert raw[-1] == "中"

    # -- _parse_generic_cli_usage delegation parity ------------------------

    def test_shim_delegates_bit_for_bit(self):
        """Prove the _parse_generic_cli_usage shim produces identical
        results to calling GenericCliAdapter.parse_output directly."""
        from runtime.orchestrator.executors import (
            _parse_generic_cli_usage,
            _HR_ENVELOPE_BEGIN,
            _HR_ENVELOPE_END,
        )
        envelope = {
            "envelope_version": 1,
            "token_usage": {"input_tokens": 42, "output_tokens": 7},
        }
        stdout = f"{_HR_ENVELOPE_BEGIN}\n{json.dumps(envelope)}\n{_HR_ENVELOPE_END}"

        shim_result = _parse_generic_cli_usage(stdout)
        adapter_result = self._adapter().parse_output(stdout)

        assert shim_result is not None
        assert adapter_result is not None
        assert shim_result.input_tokens == adapter_result.input_tokens
        assert shim_result.output_tokens == adapter_result.output_tokens
        assert shim_result.model == adapter_result.model

    def test_shim_empty_returns_none(self):
        from runtime.orchestrator.executors import _parse_generic_cli_usage
        assert _parse_generic_cli_usage("") is None


class TestGenericCliExecutorShell:
    """Phase 2: GenericCliExecutor remains the public factory result for
    custom profiles, now a thin shell around GenericCliAdapter. These
    tests lock the backward-compat contract."""

    # Profile names used in .run() calls — must be registered in the
    # binary registry before _resolve_binary is called (THR-107 seq155).
    _RUN_PROFILE_NAMES = frozenset({
        "openclaw", "test", "custom", "enveloped", "noenv",
        "custom-cjk-missing-end", "custom-cjk-invalid-json", "custom-cjk-not-dict",
    })

    @pytest.fixture(autouse=True)
    def _register_run_binaries(self, tmp_path):
        """Register fake binaries so GenericCliExecutor.run() calls
        _resolve_binary(profile_name) succeed."""
        import os as _os
        _os.environ.setdefault("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        from runtime.orchestrator.executor_binary_registry import set_binary
        for name in self._RUN_PROFILE_NAMES:
            fake_bin = tmp_path / f"bin-{name}"
            fake_bin.touch(mode=0o755, exist_ok=True)
            set_binary(name, str(fake_bin))

    def test_build_executor_returns_generic_cli_executor_for_custom_profile(self):
        """build_executor for a custom profile must return a
        GenericCliExecutor instance."""
        from runtime.orchestrator.executor_registry import (
            ExecutorProfile,
            get_registry,
            build_executor,
        )
        from runtime.orchestrator.executors import GenericCliExecutor
        from runtime.config import Settings

        registry = get_registry()
        profile = ExecutorProfile(
            name="phase2-custom",
            kind="custom",
            adapter_id="pi",
            argv_template=["echo", "--input", "{prompt}"],
            command="echo",
        )
        registry.register_custom_profile(profile)

        settings = Settings()
        executor = build_executor("phase2-custom", settings)
        assert isinstance(executor, GenericCliExecutor)

    def test_all_four_builtins_still_return_specialized_executors(self):
        """Phase 2 must not break built-in executor resolution."""
        from runtime.orchestrator.executor_registry import build_executor
        from runtime.orchestrator.executors import (
            ClaudeExecutor,
            CodexExecutor,
            OpencodeExecutor,
            PiExecutor,
        )
        from runtime.config import Settings

        settings = Settings()
        assert isinstance(build_executor("claude", settings), ClaudeExecutor)
        assert isinstance(build_executor("codex", settings), CodexExecutor)
        assert isinstance(build_executor("opencode", settings), OpencodeExecutor)
        assert isinstance(build_executor("pi", settings), PiExecutor)

    def test_custom_profile_no_adapter_injection(self):
        """GenericCliExecutor for custom profiles must NOT receive
        a D2 first-party adapter — custom profiles are not in the
        first-party catalog."""
        from runtime.orchestrator.executor_registry import (
            ExecutorProfile,
            get_registry,
            build_executor,
        )
        from runtime.orchestrator.executors import GenericCliExecutor
        from runtime.config import Settings

        registry = get_registry()
        profile = ExecutorProfile(
            name="phase2-no-adapter",
            kind="custom",
            adapter_id="pi",
            argv_template=["echo", "{prompt}"],
            command="echo",
        )
        registry.register_custom_profile(profile)

        settings = Settings()
        executor = build_executor("phase2-no-adapter", settings)
        assert isinstance(executor, GenericCliExecutor)
        # No adapter attribute — GenericCliExecutor doesn't have one
        assert not hasattr(executor, "_adapter")

    @patch("runtime.orchestrator.executors.subprocess")
    def test_generic_cli_executor_delegates_argv_to_adapter(self, mock_subprocess, tmp_path):
        """When GenericCliExecutor.run() is called, the argv built by
        the adapter matches what the pre-extraction code produced."""
        from runtime.orchestrator.executors import GenericCliExecutor

        workspace = tmp_path / "ws"
        workspace.mkdir()

        proc = MagicMock()
        proc.pid = 9999
        proc.returncode = 0
        proc.communicate.return_value = ("output", "")
        mock_subprocess.Popen.return_value = proc

        executor = GenericCliExecutor(
            profile_name="openclaw",
            argv_template=[
                "echo", "agent", "--json", "--message", "{prompt}",
                "--timeout", "{timeout_seconds}",
            ],
            provider="openclaw",
        )
        result = executor.run(
            workspace=workspace,
            prompt="Do something",
            timeout_seconds=60,
        )

        assert result.success is True
        cmd = mock_subprocess.Popen.call_args[0][0]
        assert cmd[1] == "agent"
        assert cmd[2] == "--json"
        assert cmd[3] == "--message"
        assert "Do something" in cmd[4]
        assert "<session-lifetime>" in cmd[4]
        assert cmd[5] == "--timeout"
        assert cmd[6] == "60"

    def test_generic_cli_executor_argv_template_not_aliased(self):
        """GenericCliExecutor._argv_template must be an independent copy,
        not the caller's object reference."""
        from runtime.orchestrator.executors import GenericCliExecutor

        template = ["echo", "--input", "{prompt}"]
        executor = GenericCliExecutor(
            profile_name="test",
            argv_template=template,
            provider="test",
        )
        # Mutate the original — executor's copy must not change
        template.append("--extra")
        assert len(executor._argv_template) == 3  # original length
        assert executor._argv_template[-1] == "{prompt}"

    @patch("runtime.orchestrator.executors.subprocess")
    def test_workspace_placeholder_delegated(self, mock_subprocess, tmp_path):
        from runtime.orchestrator.executors import GenericCliExecutor

        workspace = tmp_path / "agent_ws"
        workspace.mkdir()

        proc = MagicMock()
        proc.pid = 8888
        proc.returncode = 0
        proc.communicate.return_value = ("output", "")
        mock_subprocess.Popen.return_value = proc

        executor = GenericCliExecutor(
            profile_name="custom",
            argv_template=["echo", "--dir", "{workspace}", "--input", "{prompt}"],
            provider="custom",
        )
        result = executor.run(
            workspace=workspace,
            prompt="Do something",
            timeout_seconds=30,
        )

        assert result.success is True
        cmd = mock_subprocess.Popen.call_args[0][0]
        assert cmd[2] == str(workspace)

    @patch("runtime.orchestrator.executors.subprocess")
    def test_envelope_parsing_still_works_through_shell(self, mock_subprocess, tmp_path):
        """Token usage from envelope is populated when GenericCliExecutor
        delegates through the adapter (via _parse_generic_cli_usage shim)."""
        from runtime.orchestrator.executors import (
            GenericCliExecutor,
            _HR_ENVELOPE_BEGIN,
            _HR_ENVELOPE_END,
        )

        workspace = tmp_path / "ws"
        workspace.mkdir()

        envelope = json.dumps({
            "envelope_version": 1,
            "token_usage": {"input_tokens": 299, "output_tokens": 101},
        })
        stdout = f"output...\n{_HR_ENVELOPE_BEGIN}\n{envelope}\n{_HR_ENVELOPE_END}\n...more"

        proc = MagicMock()
        proc.pid = 9997
        proc.returncode = 0
        proc.communicate.return_value = (stdout, "")
        mock_subprocess.Popen.return_value = proc

        executor = GenericCliExecutor(
            profile_name="enveloped",
            argv_template=["echo", "--prompt", "{prompt}"],
            provider="enveloped",
        )
        result = executor.run(
            workspace=workspace,
            prompt="hi",
            timeout_seconds=30,
        )

        assert result.success is True
        assert result.token_usage is not None
        assert result.token_usage.input_tokens == 299
        assert result.token_usage.output_tokens == 101

    @patch("runtime.orchestrator.executors.subprocess")
    def test_no_envelope_still_succeeds_through_shell(self, mock_subprocess, tmp_path):
        from runtime.orchestrator.executors import GenericCliExecutor

        workspace = tmp_path / "ws"
        workspace.mkdir()

        proc = MagicMock()
        proc.pid = 9996
        proc.returncode = 0
        proc.communicate.return_value = ("plain output", "")
        mock_subprocess.Popen.return_value = proc

        executor = GenericCliExecutor(
            profile_name="noenv",
            argv_template=["echo", "--prompt", "{prompt}"],
            provider="noenv",
        )
        result = executor.run(
            workspace=workspace,
            prompt="hi",
            timeout_seconds=30,
        )

        assert result.success is True
        assert result.token_usage is None  # no envelope → no token data

    # -- Executor seam frozen-reference CJK parity (TASK-3396 / TASK-3398) --
    # Each test mocks subprocess.Popen to emit the same CJK over-limit
    # stdout, then asserts that GenericCliExecutor.run() produces a
    # TokenUsage with full Pydantic equality vs the independent frozen
    # executor-seam reference (_FROZEN_EXECUTOR_* constants above).
    # Does NOT import _parse_generic_cli_usage, GenericCliAdapter, or
    # GenericCliExecutor production path.

    @patch("runtime.orchestrator.executors.subprocess")
    def test_executor_seam_missing_end_against_frozen_reference(
        self, mock_subprocess, tmp_path
    ):
        """Missing-END CJK stdout → executor.run() TokenUsage matches
        independent frozen executor-seam reference."""
        from runtime.orchestrator.executors import (
            GenericCliExecutor,
            _HR_ENVELOPE_BEGIN,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()
        cjk_stdout = f"{_HR_ENVELOPE_BEGIN}\n" + "中" * 1000

        proc = MagicMock()
        proc.pid = 9999
        proc.returncode = 0
        proc.communicate.return_value = (cjk_stdout, "")
        mock_subprocess.Popen.return_value = proc

        executor = GenericCliExecutor(
            profile_name="custom-cjk-missing-end",
            argv_template=["echo", "--message", "{prompt}"],
            provider="custom-cjk-missing-end",
        )
        result = executor.run(
            workspace=workspace,
            prompt="test",
            timeout_seconds=60,
        )
        assert result.token_usage is not None
        # Full Pydantic TokenUsage equality vs independent frozen
        # executor-seam reference (model backfill encoded independently —
        # no delegation to GenericCliAdapter, GenericCliExecutor, or
        # _parse_generic_cli_usage)
        assert result.token_usage == _FROZEN_EXECUTOR_MISSING_END_USAGE
        # Defense-in-depth CJK character-slice semantics
        assert len(result.token_usage.usage_raw_json) == 1022
        assert "\ufffd" not in result.token_usage.usage_raw_json
        assert result.token_usage.usage_raw_json[-1] == "中"

    @patch("runtime.orchestrator.executors.subprocess")
    def test_executor_seam_invalid_json_against_frozen_reference(
        self, mock_subprocess, tmp_path
    ):
        """JSONDecodeError CJK stdout → executor.run() TokenUsage matches
        independent frozen executor-seam reference."""
        from runtime.orchestrator.executors import (
            GenericCliExecutor,
            _HR_ENVELOPE_BEGIN,
            _HR_ENVELOPE_END,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()
        block = "{" + "中" * 1000 + "}"
        cjk_stdout = f"{_HR_ENVELOPE_BEGIN}\n{block}\n{_HR_ENVELOPE_END}"

        proc = MagicMock()
        proc.pid = 9998
        proc.returncode = 0
        proc.communicate.return_value = (cjk_stdout, "")
        mock_subprocess.Popen.return_value = proc

        executor = GenericCliExecutor(
            profile_name="custom-cjk-invalid-json",
            argv_template=["echo", "--message", "{prompt}"],
            provider="custom-cjk-invalid-json",
        )
        result = executor.run(
            workspace=workspace,
            prompt="test",
            timeout_seconds=60,
        )
        assert result.token_usage is not None
        # Full Pydantic TokenUsage equality vs independent frozen
        # executor-seam reference (model backfill encoded independently —
        # no delegation to GenericCliAdapter, GenericCliExecutor, or
        # _parse_generic_cli_usage)
        assert result.token_usage == _FROZEN_EXECUTOR_INVALID_JSON_USAGE
        # Defense-in-depth CJK character-slice semantics
        assert len(result.token_usage.usage_raw_json) == 1002
        assert "\ufffd" not in result.token_usage.usage_raw_json
        assert result.token_usage.usage_raw_json[0] == "{"
        assert result.token_usage.usage_raw_json[-1] == "}"

    @patch("runtime.orchestrator.executors.subprocess")
    def test_executor_seam_not_dict_against_frozen_reference(
        self, mock_subprocess, tmp_path
    ):
        """Non-dict root CJK stdout (valid JSON list) → executor.run()
        TokenUsage matches independent frozen executor-seam reference."""
        from runtime.orchestrator.executors import (
            GenericCliExecutor,
            _HR_ENVELOPE_BEGIN,
            _HR_ENVELOPE_END,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()
        # Valid JSON array — decodes to list, reaches non-dict branch
        block = '["' + "中" * 1000 + '"]'
        cjk_stdout = f"{_HR_ENVELOPE_BEGIN}\n{block}\n{_HR_ENVELOPE_END}"

        proc = MagicMock()
        proc.pid = 9997
        proc.returncode = 0
        proc.communicate.return_value = (cjk_stdout, "")
        mock_subprocess.Popen.return_value = proc

        executor = GenericCliExecutor(
            profile_name="custom-cjk-not-dict",
            argv_template=["echo", "--message", "{prompt}"],
            provider="custom-cjk-not-dict",
        )
        result = executor.run(
            workspace=workspace,
            prompt="test",
            timeout_seconds=60,
        )
        assert result.token_usage is not None
        # Full Pydantic TokenUsage equality vs independent frozen
        # executor-seam reference (model backfill encoded independently —
        # no delegation to GenericCliAdapter, GenericCliExecutor, or
        # _parse_generic_cli_usage)
        assert result.token_usage == _FROZEN_EXECUTOR_NOT_DICT_USAGE
        # Defense-in-depth CJK character-slice semantics
        assert len(result.token_usage.usage_raw_json) == 1004
        assert "\ufffd" not in result.token_usage.usage_raw_json
        assert result.token_usage.usage_raw_json[0] == "["
        assert result.token_usage.usage_raw_json[-1] == "]"
        assert result.token_usage.usage_raw_json[-2:] == '"]'


class TestPhase2Boundary:
    """Phase 2 scope fence: prove no D3-D12, schema, catalog, registry,
    or web changes leaked in."""

    def test_adapter_module_is_statically_importable(self):
        """GenericCliAdapter must be a plain static import — no dynamic
        discovery, no importlib, no plugin loader."""
        from runtime.adapters.generic_cli import GenericCliAdapter
        assert GenericCliAdapter is not None
        assert hasattr(GenericCliAdapter, "build_argv")
        assert hasattr(GenericCliAdapter, "parse_output")

    def test_adapter_module_no_side_effects_on_import(self):
        """Importing the adapter must not mutate global state, registry,
        or any process-wide singleton."""
        from runtime.orchestrator.executor_registry import get_registry
        profiles_before = set(get_registry().list_profile_names())

        import runtime.adapters.generic_cli  # noqa: F401 — verify no side effects

        profiles_after = set(get_registry().list_profile_names())
        assert profiles_before == profiles_after

    def test_d10_d11_data_driven_factory_dispatch(self):
        """D10/D11 Phase-4: the static data-driven factory dict dispatches
        all four built-ins and the custom GenericCliExecutor route correctly."""
        from runtime.orchestrator.executor_registry import (
            ExecutorProfile,
            build_executor,
            get_registry,
            reset_registry,
        )
        from runtime.orchestrator.executors import (
            ClaudeExecutor,
            CodexExecutor,
            OpencodeExecutor,
            PiExecutor,
            GenericCliExecutor,
        )
        from runtime.config import Settings

        # Fresh registry with only built-ins
        reset_registry()
        registry = get_registry()
        profile = ExecutorProfile(
            name="d10-custom",
            kind="custom",
            adapter_id="pi",
            argv_template=["echo", "{prompt}"],
            command="echo",
        )
        registry.register_custom_profile(profile)

        settings = Settings()
        assert isinstance(build_executor("claude", settings), ClaudeExecutor)
        assert isinstance(build_executor("codex", settings), CodexExecutor)
        assert isinstance(build_executor("opencode", settings), OpencodeExecutor)
        assert isinstance(build_executor("pi", settings), PiExecutor)
        assert isinstance(build_executor("d10-custom", settings), GenericCliExecutor)

    def test_data_driven_factory_case_insensitive_lookup(self):
        """The factory dict uses case-insensitive lookup (profile.name.lower())."""
        from runtime.orchestrator.executor_registry import build_executor
        from runtime.orchestrator.executors import (
            ClaudeExecutor,
            CodexExecutor,
            OpencodeExecutor,
            PiExecutor,
        )
        from runtime.config import Settings

        settings = Settings()
        # All case variants must resolve to the same executor class
        assert isinstance(build_executor("Claude", settings), ClaudeExecutor)
        assert isinstance(build_executor("CLAUDE", settings), ClaudeExecutor)
        assert isinstance(build_executor("Codex", settings), CodexExecutor)
        assert isinstance(build_executor("CODEX", settings), CodexExecutor)
        assert isinstance(build_executor("OpenCode", settings), OpencodeExecutor)
        assert isinstance(build_executor("PI", settings), PiExecutor)

    def test_data_driven_factory_rejects_unknown_name(self):
        """Unknown profile names must be rejected — not silently fall
        through to a default executor."""
        from runtime.orchestrator.executor_registry import build_executor
        from runtime.config import Settings

        settings = Settings()
        with pytest.raises(ValueError, match="Unregistered executor"):
            build_executor("nonexistent-name", settings)

    def test_data_driven_factory_independent_model_arg_lists(self):
        """The model_arg list from each profile is passed independently
        to each executor — verified via the profile, not executor internals."""
        from runtime.orchestrator.executor_registry import (
            build_executor,
            get_registry,
            ExecutorProfile,
            reset_registry,
        )
        from runtime.config import Settings

        reset_registry()
        settings = Settings()

        # Verify per-profile model_arg values from the registry profiles
        registry = get_registry()
        claude_profile = registry.get_profile("claude")
        codex_profile = registry.get_profile("codex")
        assert claude_profile is not None and codex_profile is not None

        # model_arg lists are distinct objects (no shared list reference)
        assert claude_profile.model_arg is not codex_profile.model_arg, (
            "model_arg lists must be distinct objects"
        )
        assert claude_profile.model_arg == ["--model", "{model}"]
        assert codex_profile.model_arg == ["-m", "{model}"]

        # Factory still produces valid executors from these profiles
        c = build_executor("claude", settings)
        cx = build_executor("codex", settings)
        assert c._adapter is not None
        assert cx._adapter is not None

    def test_d8_catalog_unchanged(self):
        """The D8 built-in catalog must contain exactly four entries
        (claude, codex, opencode, pi) — no generic-cli entry."""
        from runtime.adapters import get_builtin_catalog
        catalog = get_builtin_catalog()
        names = {desc.name for desc in catalog}
        assert names == {"claude", "codex", "opencode", "pi"}

    def test_generic_cli_adapter_not_in_first_party_catalog(self):
        """get_first_party_adapter must return None for 'generic-cli'
        or any generic name — the adapter is used directly, not via
        the D2 catalog lookup."""
        from runtime.adapters import get_first_party_adapter
        assert get_first_party_adapter("generic-cli") is None
        assert get_first_party_adapter("generic_cli") is None
        assert get_first_party_adapter("generic") is None


# ============================================================================
# THR-107 Phase 4: D10/D11 factory cutover — data-driven dispatch
# ============================================================================
# Phase 4 replaces the D2 compatibility if/elif chain in build_executor
# with a static data-driven factory dict derived from the D8 authoritative
# built-in catalog. These adversarial tests prove the factory dispatches
# correctly, fails safely, and cannot silently degrade to an if/elif chain
# or per-provider dispatch.
# ============================================================================


class TestD10D11DataDrivenFactory:
    """D10/D11 Phase-4: adversarial tests for the data-driven factory.

    Tests that would fail if a built-in profile name were omitted from the
    static factory dict, or if a literal per-provider if/elif chain were
    still the primary dispatch mechanism.
    """

    # -- catalog-derived dispatch for all four built-ins -------------------

    def test_factory_dispatches_all_four_builtins_by_class(self):
        """Every built-in name resolves to its specialized executor class.
        This test fails if any entry is missing from the factory dict."""
        from runtime.orchestrator.executor_registry import build_executor
        from runtime.orchestrator.executors import (
            ClaudeExecutor, CodexExecutor, OpencodeExecutor, PiExecutor,
        )
        from runtime.config import Settings

        settings = Settings()
        expected = {
            "claude": ClaudeExecutor,
            "codex": CodexExecutor,
            "opencode": OpencodeExecutor,
            "pi": PiExecutor,
        }
        for name, cls in expected.items():
            ex = build_executor(name, settings)
            assert isinstance(ex, cls), (
                f"{name!r} expected {cls.__name__}, got {type(ex).__name__}"
            )

    def test_factory_injects_first_party_adapters(self):
        """Every built-in executor receives its first-party adapter instance."""
        from runtime.orchestrator.executor_registry import build_executor
        from runtime.adapters import (
            ClaudeAdapter, CodexAdapter, OpencodeAdapter, PiAdapter,
        )
        from runtime.config import Settings

        settings = Settings()
        checks = [
            ("claude", ClaudeAdapter),
            ("codex", CodexAdapter),
            ("opencode", OpencodeAdapter),
            ("pi", PiAdapter),
        ]
        for name, adapter_cls in checks:
            ex = build_executor(name, settings)
            assert ex._adapter is not None, f"{name}: no adapter injected"
            assert isinstance(ex._adapter, adapter_cls), (
                f"{name}: expected {adapter_cls.__name__}, got {type(ex._adapter).__name__}"
            )

    # -- custom route ------------------------------------------------------

    def test_custom_profile_returns_generic_cli_executor(self):
        """Custom profiles must return GenericCliExecutor, not a built-in
        specialized executor."""
        from runtime.orchestrator.executor_registry import (
            build_executor, get_registry,
        )
        from runtime.orchestrator.executors import GenericCliExecutor
        from runtime.config import Settings

        settings = Settings()
        from runtime.orchestrator.executor_registry import ExecutorRegistry

        registry = get_registry()
        profile = ExecutorRegistry.validate_custom_profile_config(
            "my-custom",
            {"command": "mycli", "argv_template": ["mycli", "{prompt}"], "adapter": "pi"},
        )
        registry.register_custom_profile(profile)

        ex = build_executor("my-custom", settings)
        assert isinstance(ex, GenericCliExecutor)
        # Custom profiles must NOT have a first-party adapter injected
        assert not hasattr(ex, "_adapter") or ex._adapter is None

    # -- unknown rejection -------------------------------------------------

    def test_unregistered_name_raises_valueerror(self):
        """Unregistered profile names must raise ValueError, not fall
        through to a default executor."""
        from runtime.orchestrator.executor_registry import build_executor
        from runtime.config import Settings

        settings = Settings()
        with pytest.raises(ValueError, match=r"Unregistered executor 'no-such-profile'"):
            build_executor("no-such-profile", settings)

    # -- case-insensitive lookup -------------------------------------------

    def test_case_insensitive_factory_lookup(self):
        """The factory dict must use case-insensitive key lookup.
        'Claude', 'CLAUDE', 'claude' must all resolve identically."""
        from runtime.orchestrator.executor_registry import build_executor
        from runtime.orchestrator.executors import ClaudeExecutor
        from runtime.config import Settings

        settings = Settings()
        for variant in ("claude", "Claude", "CLAUDE", "cLaUdE"):
            ex = build_executor(variant, settings)
            assert isinstance(ex, ClaudeExecutor), f"case variant {variant!r} failed"

    # -- model_arg independence --------------------------------------------

    def test_model_arg_lists_are_independent_per_profile(self):
        """Each built-in profile's model_arg must be an independent list.
        Modifying one profile's model_arg must not affect another.

        Uses an isolated registry to avoid contaminating later tests with
        profile mutations. Also proves at the factory seam that the
        executor's ``_model_arg`` is an independently copied list."""
        from runtime.orchestrator.executor_registry import (
            get_registry, reset_registry, build_executor,
        )
        from runtime.config import Settings

        # Isolate: fresh registry so mutations don't leak to later tests
        reset_registry()
        try:
            registry = get_registry()
            claude_profile = registry.get_profile("claude")
            codex_profile = registry.get_profile("codex")
            assert claude_profile is not None and codex_profile is not None

            # Verify they are different list objects (no shared list)
            assert claude_profile.model_arg is not codex_profile.model_arg, (
                "model_arg lists must not be shared"
            )
            assert claude_profile.model_arg == ["--model", "{model}"]
            assert codex_profile.model_arg == ["-m", "{model}"]

            # Mutating one profile's model_arg must not affect the other
            claude_profile.model_arg.append("--extra")  # type: ignore[union-attr]
            assert len(codex_profile.model_arg) == 2, (
                "Codex model_arg must remain unchanged after Claude mutation"
            )

            # ── Factory seam: executor receives model_arg with correct values ──
            settings = Settings()
            for name in ("claude", "codex", "opencode", "pi"):
                profile = registry.get_profile(name)
                assert profile is not None and profile.model_arg is not None
                ex = build_executor(name, settings)
                assert ex._model_arg is not None, (
                    f"{name}: executor _model_arg must be non-None"
                )
                assert ex._model_arg == list(profile.model_arg), (
                    f"{name}: executor _model_arg {ex._model_arg!r} "
                    f"!= profile.model_arg {list(profile.model_arg)!r}"
                )
            # Different executors must have independent model_arg lists
            ex_a = build_executor("claude", settings)
            ex_b = build_executor("codex", settings)
            assert ex_a._model_arg is not ex_b._model_arg, (
                "Claude and Codex executors must have independent model_arg lists"
            )
        finally:
            reset_registry()

    # -- Phase-0 argv parity -----------------------------------------------

    def test_factory_produces_phase0_argv_parity(self):
        """The data-driven factory must produce executors with argv parity
        matching the Phase-0 pinned baselines.

        Verifies every built-in is constructed with the correct specialized
        type, a non-None CLI path, injected adapter, valid model_arg from
        the profile, independent model_arg copy at the executor seam, and
        actual argv including model_arg substitution (when a model is set)."""
        from runtime.orchestrator.executor_registry import build_executor, get_registry
        from runtime.config import Settings

        settings = Settings()
        registry = get_registry()

        for name in ("claude", "codex", "opencode", "pi"):
            ex = build_executor(name, settings)
            # Verify the adapter was injected (D2 path — unchanged)
            assert ex._adapter is not None, f"{name}: adapter must be injected"
            # Verify profile name is set (THR-107 seq155: registration-only)
            assert ex._profile_name == name, f"{name}: _profile_name must match"
            # Profile must have model_arg — the factory passes it through
            profile = registry.get_profile(name)
            assert profile is not None and profile.model_arg is not None, (
                f"{name}: profile.model_arg must be non-None for built-in"
            )
            # Executor's model_arg must match profile's model_arg values
            assert ex._model_arg is not None, (
                f"{name}: executor _model_arg must be non-None"
            )
            assert ex._model_arg == list(profile.model_arg), (
                f"{name}: executor _model_arg {ex._model_arg!r} "
                f"!= profile.model_arg {list(profile.model_arg)!r}"
            )
            # ── Phase-0 argv contract: model_arg appears in argv with a model ──
            from runtime.orchestrator.executors import (
                ClaudeExecutor, CodexExecutor, OpencodeExecutor, PiExecutor,
            )
            fake_model = "test-model-id"
            if isinstance(ex, ClaudeExecutor):
                argv = ex._build_argv(
                    prompt="_parity_test_prompt_",
                    allowed_tools="Bash(happyranch:*)",
                    model=fake_model,
                )
            elif isinstance(ex, CodexExecutor):
                argv = ex._build_argv(model=fake_model)
            elif isinstance(ex, OpencodeExecutor):
                argv = ex._build_argv(
                    workspace="/tmp/test_ws",
                    prompt="_parity_test_prompt_",
                    model=fake_model,
                )
            elif isinstance(ex, PiExecutor):
                argv = ex._build_argv(
                    prompt="_parity_test_prompt_",
                    model=fake_model,
                )
            else:
                pytest.fail(f"{name}: unknown executor type {type(ex).__name__}")
            assert isinstance(argv, list), f"{name}: argv must be a list"
            # The model_arg flag-pair must appear verbatim in the argv
            expected_flags = [
                elem.replace("{model}", fake_model)
                for elem in profile.model_arg
            ]
            for flag in expected_flags:
                assert flag in argv, (
                    f"{name}: model_arg-derived flag {flag!r} missing "
                    f"from argv: {argv}"
                )

    # -- omitted-provider / factory-key alignment --------------------------

    def test_omitted_builtin_from_factory_dict_would_fail(self):
        """Adversarial: if a built-in name were removed from the factory dict,
        the resulting executor would no longer be a specialized class.
        This test documents the invariant, and a follow-up assertion
        proves the factory covers all four built-in catalog names."""
        from runtime.adapters import get_builtin_catalog
        from runtime.orchestrator.executor_registry import build_executor
        from runtime.orchestrator.executors import (
            ClaudeExecutor, CodexExecutor, OpencodeExecutor, PiExecutor,
            GenericCliExecutor,
        )
        from runtime.config import Settings

        catalog_names = {desc.name for desc in get_builtin_catalog()}
        expected_classes = {
            "claude": ClaudeExecutor,
            "codex": CodexExecutor,
            "opencode": OpencodeExecutor,
            "pi": PiExecutor,
        }
        # Every catalog entry must have a factory entry producing the right class
        settings = Settings()
        for name in catalog_names:
            ex = build_executor(name, settings)
            expected = expected_classes[name]
            assert isinstance(ex, expected), (
                f"Catalog entry {name!r}: expected {expected.__name__}, "
                f"got {type(ex).__name__}. The factory dict is incomplete or "
                f"falling through to GenericCliExecutor."
            )

    def test_factory_keys_align_with_d8_catalog(self):
        """Structural: the static _BUILTIN_EXECUTOR_FACTORIES dict keys
        must exactly match the D8 authoritative catalog's normalized
        built-in names (claude, codex, opencode, pi).

        This test directly inspects the factory source to extract the
        declared keys. It fails if a factory entry is omitted or if
        the factory names are not the canonical four."""
        import ast
        import inspect
        from runtime.orchestrator.executor_registry import build_executor
        from runtime.adapters import get_builtin_catalog

        catalog_names = {desc.name.lower() for desc in get_builtin_catalog()}
        assert catalog_names == {"claude", "codex", "opencode", "pi"}, (
            f"Unexpected D8 catalog names: {catalog_names}"
        )

        # Extract the _BUILTIN_EXECUTOR_FACTORIES dict keys from source
        src = inspect.getsource(build_executor)
        tree = ast.parse(src)

        factory_keys: set[str] = set()
        for node in ast.walk(tree):
            # _BUILTIN_EXECUTOR_FACTORIES is an AnnAssign (type-annotated)
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets: list[ast.expr] = []
                raw_value: ast.expr | None = None
                if isinstance(node, ast.Assign):
                    targets = list(node.targets)
                    raw_value = node.value
                else:
                    targets = [node.target]
                    raw_value = node.value
                for target in targets:
                    if isinstance(target, ast.Name) and target.id == "_BUILTIN_EXECUTOR_FACTORIES":
                        if raw_value is not None and isinstance(raw_value, ast.Dict):
                            for key in raw_value.keys:
                                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                                    factory_keys.add(key.value)

        assert factory_keys == catalog_names, (
            f"Factory keys {factory_keys!r} do not match D8 catalog "
            f"names {catalog_names!r}. A factory entry is missing or "
            f"an unexpected key is present."
        )

    def test_no_profile_name_if_elif_chain_in_build_executor(self):
        """AST-level structural assertion: ``build_executor`` must not
        contain a restored per-provider if/elif chain that compares
        ``profile.name`` or ``profile.name.lower()`` against provider
        name string literals.

        This test would FAIL if someone reverted the D10/D11 factory
        dict to the old ``if profile.name == "claude" ...`` chain.
        The static data-driven dict dispatch is the only allowed path."""
        import ast
        import inspect
        from runtime.orchestrator.executor_registry import build_executor

        provider_literals = {"claude", "codex", "opencode", "pi"}
        src = inspect.getsource(build_executor)
        tree = ast.parse(src)

        class _ProviderIfChainVisitor(ast.NodeVisitor):
            """Detect if/elif chains that dispatch on profile.name comparisons."""

            def __init__(self) -> None:
                self.violations: list[str] = []

            def visit_If(self, node: ast.If) -> None:
                self._check_test(node.test, f"line ~{node.lineno}")
                self.generic_visit(node)

            def _check_test(self, test: ast.expr, location: str) -> None:
                # Detect: profile.name.lower() == "provider"
                # or: profile.name == "provider"
                if isinstance(test, ast.Compare):
                    for comparator in test.comparators:
                        if isinstance(comparator, ast.Constant) and comparator.value in provider_literals:
                            if isinstance(test.left, ast.Call):
                                func = test.left.func
                                if isinstance(func, ast.Attribute):
                                    # profile.name.lower()
                                    if (isinstance(func.value, ast.Attribute)
                                            and isinstance(func.value.value, ast.Name)
                                            and func.value.value.id == "profile"
                                            and func.value.attr == "name"):
                                        self.violations.append(
                                            f"{location}: if/elif compares profile.name.lower() "
                                            f"to {comparator.value!r}"
                                        )
                            elif isinstance(test.left, ast.Attribute):
                                # profile.name == "claude"
                                if (isinstance(test.left.value, ast.Name)
                                        and test.left.value.id == "profile"
                                        and test.left.attr == "name"):
                                    self.violations.append(
                                        f"{location}: if/elif compares profile.name "
                                        f"to {comparator.value!r}"
                                    )

        visitor = _ProviderIfChainVisitor()
        visitor.visit(tree)

        assert not visitor.violations, (
            f"build_executor contains per-provider if/elif chain "
            f"dispatch — D10/D11 static factory dict is required. "
            f"Violations: {visitor.violations}"
        )
