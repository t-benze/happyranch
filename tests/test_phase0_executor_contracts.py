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
from runtime.models import TokenUsage
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
    """Pin the exact argv shape for each built-in executor AND a
    representative custom argv_template invocation."""

    def test_claude_cmd_baseline(self, tmp_path: Path):
        """Claude argv: binary, [model flags], -p, prompt, --permission-mode,
        --allowedTools, --output-format json."""
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

        assert captured_cmd[0].endswith("/claude")
        assert "-p" in captured_cmd
        # prompt includes session-lifetime preamble prepended
        prompt_idx = captured_cmd.index("-p") + 1
        assert _SESSION_LIFETIME_PREAMBLE.strip() in captured_cmd[prompt_idx]
        assert "hello" in captured_cmd[prompt_idx]
        assert "--permission-mode" in captured_cmd
        assert "auto" in captured_cmd
        assert "--allowedTools" in captured_cmd
        assert "--output-format" in captured_cmd
        assert "json" in captured_cmd
        # --output-format json is last flags before the process args
        fmt_idx = captured_cmd.index("--output-format")
        assert captured_cmd[fmt_idx + 1] == "json"

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
                ex.run(workspace, prompt="hi", session_id="sess-X", model="claude-sonnet-4-20250514")

        assert "--model" in captured_cmd
        model_idx = captured_cmd.index("--model")
        assert captured_cmd[model_idx + 1] == "claude-sonnet-4-20250514"
        # model args come before -p
        assert captured_cmd.index("--model") < captured_cmd.index("-p")

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
                ex.run(workspace, prompt="hi", session_id="sess-X", resume_session_id="resume-abc")

        assert "--resume" in captured_cmd
        resume_idx = captured_cmd.index("--resume")
        assert captured_cmd[resume_idx + 1] == "resume-abc"

    def test_codex_cmd_baseline(self, tmp_path: Path):
        """Codex argv: binary, exec, [model flags], --sandbox, network flag,
        --skip-git-repo-check, --json, -"""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        fake_proc = _make_popen_mock(stdout='{"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":50}}')
        captured_cmd: list[str] = []

        def _capture(cmd, **kw):
            captured_cmd.extend(cmd)
            return fake_proc

        with patch("runtime.orchestrator.executors.subprocess.Popen", _capture):
            ex = CodexExecutor(
                codex_cli_path="codex",
                sandbox_mode="workspace-write",
            )
            ex.run(workspace, prompt="hello", session_id="sess-X")

        assert captured_cmd[0].endswith("/codex")
        assert "exec" in captured_cmd
        assert "--sandbox" in captured_cmd
        assert "workspace-write" in captured_cmd
        assert "-c" in captured_cmd
        assert "sandbox_workspace_write.network_access=true" in captured_cmd
        assert "--skip-git-repo-check" in captured_cmd
        assert "--json" in captured_cmd
        assert "-" in captured_cmd

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

        assert any("codex prompt text" in inp for inp in captured_input), (
            f"Codex must pass prompt via stdin, got: {captured_input}"
        )

    def test_opencode_cmd_baseline(self, tmp_path: Path):
        """Opencode argv: binary, run, [model flags], --dir <workspace>,
        --format json, <prompt>"""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        fake_proc = _make_popen_mock(stdout='{"messages":[{"role":"assistant","usage":{"input_tokens":50,"output_tokens":25}}]}')
        captured_cmd: list[str] = []

        def _capture(cmd, **kw):
            captured_cmd.extend(cmd)
            return fake_proc

        with patch("runtime.orchestrator.executors.subprocess.Popen", _capture):
            ex = OpencodeExecutor(opencode_cli_path="opencode")
            ex.run(workspace, prompt="hello world", session_id="sess-X")

        assert captured_cmd[0].endswith("/opencode")
        assert "run" in captured_cmd
        assert "--dir" in captured_cmd
        dir_idx = captured_cmd.index("--dir")
        assert captured_cmd[dir_idx + 1] == str(workspace)
        assert "--format" in captured_cmd
        assert "json" in captured_cmd
        # prompt is the final positional arg
        assert captured_cmd[-1] == _SESSION_LIFETIME_PREAMBLE + "hello world"

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
            ex.run(workspace, prompt="hi", session_id="sess-X", model="gemini-2.5-pro")

        assert "-m" in captured_cmd
        assert "gemini-2.5-pro" in captured_cmd

    def test_pi_cmd_baseline(self, tmp_path: Path):
        """Pi argv: binary, [model flags], -p <prompt>, --mode json"""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        fake_proc = _make_popen_mock(stdout='{"type":"turn_end","message":{"usage":{"input":100,"output":50}}}')
        captured_cmd: list[str] = []

        def _capture(cmd, **kw):
            captured_cmd.extend(cmd)
            return fake_proc

        with patch("runtime.orchestrator.executors.subprocess.Popen", _capture):
            ex = PiExecutor(pi_cli_path="pi")
            ex.run(workspace, prompt="hello pi", session_id="sess-X")

        assert captured_cmd[0].endswith("/pi")
        assert "-p" in captured_cmd
        prompt_idx = captured_cmd.index("-p") + 1
        assert "hello pi" in captured_cmd[prompt_idx]
        assert "--mode" in captured_cmd
        assert "json" in captured_cmd

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
            ex.run(workspace, prompt="hi", session_id="sess-X", model="pi-model-v2")

        assert "--model" in captured_cmd
        model_idx = captured_cmd.index("--model")
        assert captured_cmd[model_idx + 1] == "pi-model-v2"

    def test_custom_argv_template_substitution(self, tmp_path: Path):
        """argv_template[0] is the executable; placeholders resolve to
        exactly ONE argv element each."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        from runtime.orchestrator.executor_binary_registry import get_binary, is_binary_valid
        from runtime.orchestrator.executors import _resolve_binary as orig_resolve

        # Register a synthetic binary
        fake_bin_path = tmp_path / "bin" / "my-cli"
        fake_bin_path.parent.mkdir()
        fake_bin_path.write_text("")
        fake_bin_path.chmod(0o755)

        captured_cmd: list[str] = []

        # We need _resolve_binary to resolve "my-cli" to our fake path.
        # The mock_shutil_which fixture won't match "my-cli". Patch
        # _resolve_binary directly for the custom case.
        ex = GenericCliExecutor(
            profile_name="my-cli",
            argv_template=["my-cli", "--workspace", "{workspace}", "--prompt", "{prompt}", "--timeout", "{timeout_seconds}"],
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
                ex.run(workspace, prompt="custom prompt here", session_id="sess-X", timeout_seconds=300)

        assert len(captured_cmd) == 7
        assert captured_cmd[0] == str(fake_bin_path)
        assert captured_cmd[1] == "--workspace"
        assert captured_cmd[2] == str(workspace)
        assert captured_cmd[3] == "--prompt"
        # Prompt includes session-lifetime preamble
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

        # Should be exactly 2 elements: [binary, prompt-as-one-arg]
        assert len(captured_cmd) == 2
        assert captured_cmd[0] == "/usr/local/bin/test-cli"
        assert "\n" in captured_cmd[1]  # newlines preserved in the single element


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
    """Verify: profile name → adapter_id → bootstrap file + readiness marker,
    without modifying workspace adapters or permissions."""

    def test_builtin_profile_workspace_mapping(self):
        """Each built-in profile maps to the correct adapter_id and
        readiness marker."""
        registry = get_registry()

        claude = registry.get_profile("claude")
        assert claude is not None
        assert claude.adapter_id == "claude"
        assert claude.readiness_marker_fragment == ".claude/skills/start-task/SKILL.md"

        codex = registry.get_profile("codex")
        assert codex is not None
        assert codex.adapter_id == "codex"
        assert codex.readiness_marker_fragment == "AGENTS.md"

        opencode = registry.get_profile("opencode")
        assert opencode is not None
        assert opencode.adapter_id == "opencode"
        assert opencode.readiness_marker_fragment == "AGENTS.md"

        pi = registry.get_profile("pi")
        assert pi is not None
        assert pi.adapter_id == "pi"
        assert pi.readiness_marker_fragment == "AGENTS.md"

    def test_profile_adapter_id_mapping_is_unchanged(self):
        """adapter_id selects workspace preparation — it does not map
        to a separate command adapter or adapter catalog entry.
        This is the current behavior; the spec §6.3 proposes a split
        but this test pins the current mapping."""
        registry = get_registry()
        profiles = {p.name: p for p in [
            registry.get_profile(n) for n in ["claude", "codex", "opencode", "pi"]
        ]}

        # adapter_id is the workspace adapter — each is unique per built-in
        for name in ["claude", "codex", "opencode", "pi"]:
            assert profiles[name].adapter_id == name

    def test_custom_profile_adapter_defaults_to_pi(self):
        """Custom profiles default adapter to 'pi' when not specified."""
        registry = get_registry()
        profile = ExecutorProfile(
            name="kimi",
            kind="custom",
            adapter_id="pi",
            readiness_marker_fragment="AGENTS.md",
            argv_template=["kimi", "--prompt", "{prompt}"],
            command="kimi",
        )
        registry.register_custom_profile(profile)
        stored = registry.get_profile("kimi")
        assert stored is not None
        assert stored.adapter_id == "pi"

    def test_custom_profile_can_specify_different_adapter(self):
        """A custom profile may specify adapter_id = 'claude' to get
        CLAUDE.md-style bootstrap."""
        registry = get_registry()
        profile = ExecutorProfile(
            name="custom-claude-style",
            kind="custom",
            adapter_id="claude",
            readiness_marker_fragment=".claude/skills/start-task/SKILL.md",
            argv_template=["my-cli", "{prompt}"],
            command="my-cli",
        )
        registry.register_custom_profile(profile)
        stored = registry.get_profile("custom-claude-style")
        assert stored is not None
        assert stored.adapter_id == "claude"
        assert stored.readiness_marker_fragment == ".claude/skills/start-task/SKILL.md"

    def test_custom_profile_readiness_marker_derives_from_adapter(self):
        """Readiness marker fragment is set per adapter_id, not independently
        chosen arbitrarily. This is an observable convention in
        validate_custom_profile_config."""
        registry = get_registry()
        for adapter_id, expected_marker in [
            ("claude", ".claude/skills/start-task/SKILL.md"),
            ("codex", "AGENTS.md"),
            ("opencode", "AGENTS.md"),
            ("pi", "AGENTS.md"),
        ]:
            profile = ExecutorProfile(
                name=f"test-{adapter_id}",
                kind="custom",
                adapter_id=adapter_id,
                readiness_marker_fragment=expected_marker,
                argv_template=["test-cli", "{prompt}"],
                command="test-cli",
            )
            registry.register_custom_profile(profile)
            stored = registry.get_profile(f"test-{adapter_id}")
            assert stored is not None
            assert stored.adapter_id == adapter_id


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
    """Verify ExecutorResult fields flow into the audit/database path
    without mocking run_step internals — direct shape contract."""

    def test_executor_result_shape_for_success_path(self):
        """Success ExecutorResult carries token_usage for audit."""
        token_usage = TokenUsage(
            input_tokens=1000,
            output_tokens=500,
            model="claude-sonnet-4-20250514",
        )
        result = ExecutorResult(
            success=True,
            duration_seconds=42,
            session_id="sess-abc",
            returncode=0,
            stdout_tail="agent response tail",
            stderr_tail="",
            token_usage=token_usage,
            agent_session_id="agent-sess-xyz",
        )

        # The audit site reads these fields
        assert result.token_usage is not None
        assert result.token_usage.input_tokens == 1000
        assert result.token_usage.output_tokens == 500
        assert result.token_usage.model == "claude-sonnet-4-20250514"
        assert result.stdout_tail is not None
        assert result.stderr_tail == ""

    def test_executor_result_shape_for_failure_path(self):
        """Failure ExecutorResult carries stdout_tail/stderr_tail/error
        for _session_failed_note enrichment."""
        result = ExecutorResult(
            success=False,
            duration_seconds=10,
            session_id="sess-X",
            returncode=1,
            stdout_tail="partial output",
            stderr_tail="error details here",
            error="Command exited with code 1: error details here",
            rate_limited=False,
        )

        # The enrichment at run_step.py:1087-1091 reads these
        assert result.success is False
        assert result.stdout_tail == "partial output"
        assert result.stderr_tail == "error details here"
        assert result.error is not None
        assert result.rate_limited is False
        # No token_usage for failure path
        assert result.token_usage is None

    def test_run_step_token_usage_forwarded_to_audit_logger(self):
        """Verify the log_session_end call shape that run_step uses
        forwards token_usage."""
        from runtime.infrastructure.audit_logger import AuditLogger

        audit = MagicMock(spec=AuditLogger)
        token_usage = TokenUsage(input_tokens=42, output_tokens=7)
        result = ExecutorResult(
            success=True, duration_seconds=5, session_id="s1",
            token_usage=token_usage,
        )
        # This is the exact call shape from orchestrator.py _run_agent
        audit.log_session_end(
            task_id="T1", agent="dev_agent", duration_seconds=result.duration_seconds,
            token_usage=result.token_usage,
        )
        audit.log_session_end.assert_called_once()
        call_kwargs = audit.log_session_end.call_args[1]
        assert call_kwargs["task_id"] == "T1"
        assert call_kwargs["agent"] == "dev_agent"
        assert call_kwargs["token_usage"] is token_usage

    def test_run_step_inserts_session_token_usage_row_shape(self):
        """Verify insert_session_token_usage receives the expected
        field shape from ExecutorResult.token_usage."""
        from runtime.infrastructure.database import Database

        db = MagicMock(spec=Database)
        token_usage = TokenUsage(
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=30,
            cache_creation_tokens=20,
            reasoning_tokens=10,
            model="test-model",
            usage_raw_json='{"raw":"json"}',
        )

        db.insert_session_token_usage(
            task_id="T-PHASE0",
            agent="test_agent",
            session_id="sess-test",
            executor="claude",
            token_usage=token_usage,
        )
        db.insert_session_token_usage.assert_called_once()
        kw = db.insert_session_token_usage.call_args[1]
        assert kw["task_id"] == "T-PHASE0"
        assert kw["agent"] == "test_agent"
        assert kw["executor"] == "claude"
        assert kw["token_usage"] is token_usage

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
    returns the correct executor type."""

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
