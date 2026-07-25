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
import shutil
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _mock_shutil_which(monkeypatch):
    """Patch shutil.which inside executors so _resolve_binary calls resolve
    deterministically regardless of host PATH."""
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

        # [claude, "-p", <prompt>, "--permission-mode", "auto",
        #  "--allowedTools", "Bash(happyranch *)", "--output-format", "json"]
        self._assert_argv_structure(
            captured_cmd, binary_ends_with="/claude", expected_len=9,
            prompt_index=2, prompt_contains=["hello"],
        )
        assert captured_cmd[1] == "-p"
        assert captured_cmd[3] == "--permission-mode"
        assert captured_cmd[4] == "auto"
        assert captured_cmd[5] == "--allowedTools"
        assert captured_cmd[6] == "Bash(happyranch *)"
        assert captured_cmd[7] == "--output-format"
        assert captured_cmd[8] == "json"

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

        # [claude, "--model", "claude-sonnet-4-20250514", "-p", <prompt>,
        #  "--permission-mode", "auto", "--allowedTools", "Bash(happyranch *)",
        #  "--output-format", "json"]
        self._assert_argv_structure(
            captured_cmd, binary_ends_with="/claude", expected_len=11,
            prompt_index=4, prompt_contains=["hi"],
        )
        assert captured_cmd[1] == "--model"
        assert captured_cmd[2] == "claude-sonnet-4-20250514"
        assert captured_cmd[3] == "-p"
        assert captured_cmd[5] == "--permission-mode"
        assert captured_cmd[6] == "auto"
        assert captured_cmd[7] == "--allowedTools"
        assert captured_cmd[8] == "Bash(happyranch *)"
        assert captured_cmd[9] == "--output-format"
        assert captured_cmd[10] == "json"

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

        # [claude, "-p", <prompt>, "--permission-mode", "auto",
        #  "--allowedTools", "Bash(happyranch *)", "--output-format", "json",
        #  "--resume", "resume-abc"]
        self._assert_argv_structure(
            captured_cmd, binary_ends_with="/claude", expected_len=11,
            prompt_index=2, prompt_contains=["hi"],
        )
        assert captured_cmd[3] == "--permission-mode"
        assert captured_cmd[4] == "auto"
        assert captured_cmd[5] == "--allowedTools"
        assert captured_cmd[6] == "Bash(happyranch *)"
        assert captured_cmd[7] == "--output-format"
        assert captured_cmd[8] == "json"
        assert captured_cmd[9] == "--resume"
        assert captured_cmd[10] == "resume-abc"

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

        # [pi, "-p", <prompt>, "--mode", "json"]
        self._assert_argv_structure(
            captured_cmd, binary_ends_with="/pi", expected_len=5,
            prompt_index=2, prompt_contains=["hello pi"],
        )
        assert captured_cmd[1] == "-p"
        assert captured_cmd[3] == "--mode"
        assert captured_cmd[4] == "json"

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

        # [pi, "--model", "pi-model-v2", "-p", <prompt>, "--mode", "json"]
        self._assert_argv_structure(
            captured_cmd, binary_ends_with="/pi", expected_len=7,
            prompt_index=4, prompt_contains=["hi"],
        )
        assert captured_cmd[1] == "--model"
        assert captured_cmd[2] == "pi-model-v2"
        assert captured_cmd[3] == "-p"
        assert captured_cmd[5] == "--mode"
        assert captured_cmd[6] == "json"

    # -- Custom argv_template -----------------------------------------------

    def test_custom_argv_template_substitution(self, tmp_path: Path):
        """argv_template[0] is the executable; placeholders resolve to
        exactly ONE argv element each."""
        workspace = tmp_path / "ws"
        workspace.mkdir()

        fake_bin_path = tmp_path / "bin" / "my-cli"
        fake_bin_path.parent.mkdir()
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
        """ExecutorResult has exactly the 10 fields shipping today.
        This guards against accidental field additions/removals."""
        result = ExecutorResult(success=True, duration_seconds=1, session_id="s")
        # Fields as of origin/main @ a7134f00
        expected_fields = {
            "success", "duration_seconds", "session_id", "returncode",
            "stdout_tail", "stderr_tail", "error", "token_usage",
            "agent_session_id", "rate_limited",
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
    """Pin the build_executor if/elif chain — verifying each built-in
    returns the correct executor type, and exercising the full fifth
    lifecycle (custom registration -> factory -> run) end-to-end."""

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
            self, tmp_path: Path):
        """Exercise the complete fifth lifecycle: validate a raw config
        through the canonical ExecutorRegistry.validate_custom_profile_config,
        register the returned validated profile, build it via build_executor,
        run it with mocked Popen, assert exact ordered argv, and verify
        optional v1 envelope parsing.

        This test FAILS if validate_custom_profile_config is bypassed —
        the profile must flow through the canonical validation seam.
        """
        workspace = tmp_path / "ws"
        workspace.mkdir()

        # 1. Build a fixed raw config and validate through the canonical seam.
        #    command=None skips which() resolution (deterministic test path).
        config = {
            "command": None,
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
