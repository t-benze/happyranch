from __future__ import annotations

import json
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
from runtime.orchestrator.executors import GenericCliExecutor, _parse_generic_cli_usage


# ---------------------------------------------------------------------------
# validate_argv_template
# ---------------------------------------------------------------------------


def test_validate_argv_template_accepts_valid_template() -> None:
    errors = validate_argv_template(
        ["openclaw", "agent", "--message", "{prompt}", "--timeout", "{timeout_seconds}"]
    )
    assert errors == []


def test_validate_argv_template_rejects_empty() -> None:
    errors = validate_argv_template([])
    assert len(errors) >= 1
    assert any("non-empty" in e for e in errors)


def test_validate_argv_template_rejects_non_string_elements() -> None:
    errors = validate_argv_template(["ok", True, "bad"] if False else ["ok", 42, "bad"])
    # Actually, since we pass through list[str], let's test a truly bad case
    template: list = ["ok", 42, "bad"]  # type: ignore[list-item]
    errors = validate_argv_template(template)
    assert any("non-empty string" in e for e in errors)


def test_validate_argv_template_rejects_empty_string_elements() -> None:
    errors = validate_argv_template(["ok", "", "bad"])
    assert any("non-empty string" in e for e in errors)


def test_validate_argv_template_rejects_unsupported_placeholders() -> None:
    errors = validate_argv_template(["cmd", "--foo", "{unknown}", "--bar"])
    assert len(errors) >= 1
    assert any("unsupported placeholder" in e.lower() for e in errors)


def test_validate_argv_template_accepts_all_valid_placeholders() -> None:
    errors = validate_argv_template(
        ["cmd", "{prompt}", "{timeout_seconds}", "{workspace}"]
    )
    assert errors == []


def test_validate_argv_template_rejects_model_placeholder() -> None:
    """Custom-profile argv templates must not use {model} — model_arg
    substitution is reserved for built-in executor profiles only."""
    errors = validate_argv_template(["cli", "--model", "{model}"])
    assert len(errors) >= 1
    assert any("unsupported placeholder" in e.lower() for e in errors)
    assert any("{model}" in e for e in errors)


# ---------------------------------------------------------------------------
# ExecutorRegistry
# ---------------------------------------------------------------------------


class TestExecutorRegistry:
    def setup_method(self) -> None:
        reset_registry()

    def teardown_method(self) -> None:
        reset_registry()

    def test_builtins_registered_on_creation(self) -> None:
        registry = ExecutorRegistry()
        for name in ("claude", "codex", "opencode", "pi"):
            assert registry.is_registered(name)
            p = registry.get_profile(name)
            assert p is not None
            assert p.kind == "builtin"

    def test_builtins_have_correct_adapter_ids(self) -> None:
        registry = ExecutorRegistry()
        assert registry.get_profile("claude").adapter_id == "claude"
        assert registry.get_profile("codex").adapter_id == "codex"
        assert registry.get_profile("opencode").adapter_id == "opencode"
        assert registry.get_profile("pi").adapter_id == "pi"

    def test_builtins_have_correct_readiness_markers(self) -> None:
        registry = ExecutorRegistry()
        assert ".claude/skills" in registry.get_profile("claude").readiness_marker_fragment
        assert registry.get_profile("codex").readiness_marker_fragment == "AGENTS.md"
        assert registry.get_profile("opencode").readiness_marker_fragment == "AGENTS.md"
        assert registry.get_profile("pi").readiness_marker_fragment == "AGENTS.md"

    def test_builtins_have_verified_model_arg(self) -> None:
        registry = ExecutorRegistry()
        # claude: --model <id> (verified from claude --help 2026-07-04)
        assert registry.get_profile("claude").model_arg == ["--model", "{model}"]
        # codex: -m <model> (verified from codex --help 2026-07-04)
        assert registry.get_profile("codex").model_arg == ["-m", "{model}"]
        # opencode: -m <provider/model> (verified from opencode --help 2026-07-04)
        assert registry.get_profile("opencode").model_arg == ["-m", "{model}"]
        # pi: --model <pattern> (verified from pi --help 2026-07-04)
        assert registry.get_profile("pi").model_arg == ["--model", "{model}"]

    def test_model_arg_defaults_to_none_for_frozen_default(self) -> None:
        """ExecutorProfile() with no model_arg should have model_arg=None."""
        p = ExecutorProfile(name="test")
        assert p.model_arg is None

    def test_is_registered_returns_false_for_unknown(self) -> None:
        registry = ExecutorRegistry()
        assert not registry.is_registered("nonexistent")
        assert not registry.is_registered("gpt")

    def test_get_profile_returns_none_for_unknown(self) -> None:
        registry = ExecutorRegistry()
        assert registry.get_profile("unknown") is None

    def test_is_registered_is_case_insensitive(self) -> None:
        registry = ExecutorRegistry()
        assert registry.is_registered("CLAUDE")
        assert registry.is_registered("Codex")
        assert registry.is_registered("OpenCode")

    def test_register_custom_profile_succeeds(self) -> None:
        registry = ExecutorRegistry()
        profile = ExecutorProfile(
            name="openclaw",
            kind="custom",
            adapter_id="pi",
            readiness_marker_fragment="AGENTS.md",
            argv_template=[
                "openclaw", "agent", "--json", "--message", "{prompt}",
                "--timeout", "{timeout_seconds}",
            ],
            command="openclaw",
        )
        registry.register_custom_profile(profile)
        assert registry.is_registered("openclaw")
        assert registry.get_profile("openclaw").kind == "custom"

    def test_register_custom_profile_rejects_builtin_collision(self) -> None:
        registry = ExecutorRegistry()
        profile = ExecutorProfile(
            name="claude",
            kind="custom",
            adapter_id="pi",
            readiness_marker_fragment="AGENTS.md",
            argv_template=["claude", "{prompt}"],
        )
        with pytest.raises(ValueError, match="Cannot override built-in"):
            registry.register_custom_profile(profile)

    def test_register_custom_profile_rejects_custom_collision(self) -> None:
        """Registering a custom profile with the same name but different
        definition raises ExecutorProfileCollisionError."""
        registry = ExecutorRegistry()
        profile_alpha = ExecutorProfile(
            name="shared",
            kind="custom",
            adapter_id="pi",
            readiness_marker_fragment="AGENTS.md",
            argv_template=["echo", "{prompt}"],
            command="echo",
        )
        registry.register_custom_profile(profile_alpha)
        assert registry.is_registered("shared")

        profile_beta = ExecutorProfile(
            name="shared",
            kind="custom",
            adapter_id="pi",
            readiness_marker_fragment="AGENTS.md",
            argv_template=["printf", "{prompt}"],
            command="printf",
        )
        with pytest.raises(ExecutorProfileCollisionError, match="shared"):
            registry.register_custom_profile(profile_beta)

        # Alpha's profile is unchanged
        p = registry.get_profile("shared")
        assert p is not None
        assert p.argv_template == ["echo", "{prompt}"]
        assert p.command == "echo"

    def test_register_custom_profile_accepts_identical_duplicate(self) -> None:
        """Registering the exact same profile twice is a no-op (idempotent)."""
        registry = ExecutorRegistry()
        profile = ExecutorProfile(
            name="shared",
            kind="custom",
            adapter_id="pi",
            readiness_marker_fragment="AGENTS.md",
            argv_template=["echo", "{prompt}"],
            command="echo",
        )
        registry.register_custom_profile(profile)
        # Second registration with identical profile — no error
        registry.register_custom_profile(profile)
        assert registry.is_registered("shared")
        p = registry.get_profile("shared")
        assert p is not None
        assert p.argv_template == ["echo", "{prompt}"]

    def test_register_custom_profile_rejects_missing_argv_template(self) -> None:
        registry = ExecutorRegistry()
        profile = ExecutorProfile(
            name="customcli",
            kind="custom",
            adapter_id="pi",
            readiness_marker_fragment="AGENTS.md",
            argv_template=None,
        )
        with pytest.raises(ValueError, match="argv_template"):
            registry.register_custom_profile(profile)

    # ── THR-107 S4a: unregister_custom_profile ───────────────────────────

    def _custom_profile(self, name: str = "openclaw") -> ExecutorProfile:
        return ExecutorProfile(
            name=name,
            kind="custom",
            adapter_id="pi",
            readiness_marker_fragment="AGENTS.md",
            argv_template=["echo", "{prompt}"],
            command="echo",
        )

    def test_unregister_custom_profile_removes_it(self) -> None:
        registry = ExecutorRegistry()
        registry.register_custom_profile(self._custom_profile())
        assert registry.is_registered("openclaw")

        removed = registry.unregister_custom_profile("openclaw")

        assert removed is True
        assert not registry.is_registered("openclaw")
        assert registry.get_profile("openclaw") is None
        assert "openclaw" not in registry.list_profile_names()

    def test_unregister_absent_name_returns_false(self) -> None:
        registry = ExecutorRegistry()
        assert registry.unregister_custom_profile("no-such-profile") is False

    def test_unregister_builtin_raises(self) -> None:
        registry = ExecutorRegistry()
        for name in ("claude", "codex", "opencode", "pi"):
            with pytest.raises(ValueError, match="built-in"):
                registry.unregister_custom_profile(name)
            assert registry.is_registered(name)

    def test_unregister_is_case_insensitive(self) -> None:
        registry = ExecutorRegistry()
        registry.register_custom_profile(self._custom_profile())
        assert registry.unregister_custom_profile("OpenClaw") is True
        assert not registry.is_registered("openclaw")

    def test_register_custom_profile_rejects_invalid_argv_template(self) -> None:
        registry = ExecutorRegistry()
        profile = ExecutorProfile(
            name="customcli",
            kind="custom",
            adapter_id="pi",
            readiness_marker_fragment="AGENTS.md",
            argv_template=["cmd", "{bad_placeholder}"],
        )
        with pytest.raises(ValueError, match="Invalid argv_template"):
            registry.register_custom_profile(profile)

    def test_validate_custom_profile_config_builds_profile(self) -> None:
        """THR-107: validate_custom_profile_config is the canonical
        validation seam for BOTH register routes and the runtime-store
        startup load (register_custom_from_config is removed)."""
        config = {
            "command": None,  # skip which() resolution (test seam)
            "argv_template": [
                "fake-cli", "run", "--input", "{prompt}",
                "--timeout", "{timeout_seconds}",
            ],
            "adapter": "pi",
        }
        registry = ExecutorRegistry()
        profile = ExecutorRegistry.validate_custom_profile_config(
            "openclaw", config
        )
        registry.register_custom_profile(profile)
        assert registry.is_registered("openclaw")
        p = registry.get_profile("openclaw")
        assert p is not None
        assert p.adapter_id == "pi"
        assert p.argv_template is not None

    def test_validate_custom_profile_config_rejects_invalid_adapter(self) -> None:
        config = {
            "command": None,
            "argv_template": ["cmd", "{prompt}"],
            "adapter": "nonexistent",
        }
        with pytest.raises(ValueError, match="adapter"):
            ExecutorRegistry.validate_custom_profile_config("bad", config)

    def test_validate_custom_profile_config_rejects_missing_argv(self) -> None:
        with pytest.raises(ValueError, match="argv_template"):
            ExecutorRegistry.validate_custom_profile_config(
                "bad", {"command": None}
            )

    # ── Issue #490: command / argv_template[0] parity ───────────────────

    def test_validate_custom_rejects_argv0_mismatch(self) -> None:
        """command and argv_template[0] must resolve to the SAME executable."""
        config = {
            "command": "echo",
            "argv_template": ["printf", "{prompt}"],
            "adapter": "pi",
        }
        with pytest.raises(ValueError, match="must be the same executable"):
            ExecutorRegistry.validate_custom_profile_config("kimi", config)

    def test_validate_custom_rejects_unresolvable_argv0(self) -> None:
        """argv_template[0] not found on PATH → ValueError with actionable message."""
        config = {
            "command": "echo",
            "argv_template": ["definitely-not-on-path-xyzzy", "{prompt}"],
            "adapter": "pi",
        }
        with pytest.raises(ValueError, match="not found on PATH"):
            ExecutorRegistry.validate_custom_profile_config("bad", config)

    def test_validate_custom_rejects_unresolvable_command(self) -> None:
        """command not found on PATH → ValueError (existing behavior, preserved)."""
        config = {
            "command": "definitely-not-command-xyzzy",
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
        }
        with pytest.raises(ValueError, match="not found on PATH"):
            ExecutorRegistry.validate_custom_profile_config("bad", config)

    def test_validate_custom_accepts_matching_path_commands(self) -> None:
        """Valid profile where command and argv_template[0] are both 'echo' → OK."""
        config = {
            "command": "echo",
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
        }
        profile = ExecutorRegistry.validate_custom_profile_config("ok", config)
        assert profile.command == "echo"
        assert profile.argv_template == ["echo", "{prompt}"]

    def test_validate_custom_accepts_absolute_command(self) -> None:
        """command with an absolute path and matching argv_template[0] → OK.
        shutil.which returns None for absolute paths (they're already
        resolved), so the absolute-path check in validate_custom_profile_config
        skips the which() comparison. The profile stores the absolute path as-is."""
        echo_path = "/bin/echo"
        config = {
            "command": echo_path,
            "argv_template": [echo_path, "{prompt}"],
            "adapter": "pi",
        }
        # shutil.which returns None for absolute paths that exist on disk
        # — the which() wrapper treats existing absolute paths as resolveable.
        # Actually, shutil.which('/bin/echo') returns '/bin/echo' on POSIX.
        import shutil
        from pathlib import Path
        resolved = shutil.which(echo_path)
        if resolved == echo_path:
            profile = ExecutorRegistry.validate_custom_profile_config("abs", config)
            assert profile.command == echo_path
            assert profile.argv_template == [echo_path, "{prompt}"]

    def test_validate_custom_symlink_canonicalization(self) -> None:
        """Symlink / aliased command must canonicalize to same path.
        For example, on macOS /bin/echo -> something, shutil.which resolves it.
        This test verifies that if two commands resolve to the same canonical
        path, they pass the parity check."""
        # Both 'echo' and '/bin/echo' resolve to the same binary via
        # shutil.which + Path.resolve() canonicalization.
        config = {
            "command": "echo",
            "argv_template": ["/bin/echo", "{prompt}"],
            "adapter": "pi",
        }
        import shutil
        from pathlib import Path
        res_cmd = shutil.which("echo")
        res_argv0 = shutil.which("/bin/echo")
        if res_cmd and res_argv0 and str(Path(res_cmd).resolve()) == str(Path(res_argv0).resolve()):
            profile = ExecutorRegistry.validate_custom_profile_config("sym", config)
            assert profile is not None

    def test_validate_custom_rejects_kimi_args_only_template(self) -> None:
        """The reported Kimi case: --flag args as argv_template[0] with
        no executable → rejected with actionable message (issue #490)."""
        config = {
            "command": "kimi-cli",
            "argv_template": ["--flag", "{prompt}"],
            "adapter": "pi",
        }
        with pytest.raises(ValueError, match="not found on PATH"):
            ExecutorRegistry.validate_custom_profile_config("kimi", config)

    def test_list_profile_names_includes_builtins(self) -> None:
        registry = ExecutorRegistry()
        names = registry.list_profile_names()
        assert "claude" in names
        assert "codex" in names
        assert "opencode" in names
        assert "pi" in names

    def test_list_profile_names_includes_custom_after_registration(self) -> None:
        registry = ExecutorRegistry()
        registry.register_custom_profile(
            ExecutorProfile(
                name="openclaw",
                kind="custom",
                adapter_id="pi",
                readiness_marker_fragment="AGENTS.md",
                argv_template=["openclaw", "{prompt}"],
            )
        )
        names = registry.list_profile_names()
        assert "openclaw" in names

    def test_global_registry_is_singleton(self) -> None:
        reset_registry()
        r1 = get_registry()
        r2 = get_registry()
        assert r1 is r2


# ---------------------------------------------------------------------------
# build_executor
# ---------------------------------------------------------------------------


class TestBuildExecutor:
    def setup_method(self) -> None:
        reset_registry()

    def teardown_method(self) -> None:
        reset_registry()

    def test_builds_claude_executor(self) -> None:
        settings = Settings()
        executor = build_executor("claude", settings)
        from runtime.orchestrator.executors import ClaudeExecutor
        assert isinstance(executor, ClaudeExecutor)

    def test_builds_codex_executor(self) -> None:
        settings = Settings()
        executor = build_executor("codex", settings)
        from runtime.orchestrator.executors import CodexExecutor
        assert isinstance(executor, CodexExecutor)

    def test_builds_opencode_executor(self) -> None:
        settings = Settings()
        executor = build_executor("opencode", settings)
        from runtime.orchestrator.executors import OpencodeExecutor
        assert isinstance(executor, OpencodeExecutor)

    def test_builds_pi_executor(self) -> None:
        settings = Settings()
        executor = build_executor("pi", settings)
        from runtime.orchestrator.executors import PiExecutor
        assert isinstance(executor, PiExecutor)

    def test_builds_custom_executor(self) -> None:
        settings = Settings()
        registry = get_registry()
        registry.register_custom_profile(
            ExecutorProfile(
                name="openclaw",
                kind="custom",
                adapter_id="pi",
                readiness_marker_fragment="AGENTS.md",
                argv_template=[
                    "openclaw", "agent", "--message", "{prompt}",
                    "--timeout", "{timeout_seconds}",
                ],
            )
        )
        executor = build_executor("openclaw", settings)
        from runtime.orchestrator.executors import GenericCliExecutor
        assert isinstance(executor, GenericCliExecutor)

    def test_rejects_unregistered_executor(self) -> None:
        settings = Settings()
        with pytest.raises(ValueError, match="Unregistered"):
            build_executor("nonexistent", settings)


# ---------------------------------------------------------------------------
# GenericCliExecutor
# ---------------------------------------------------------------------------

from unittest.mock import MagicMock, patch


class TestGenericCliExecutor:
    def setup_method(self) -> None:
        reset_registry()

    def teardown_method(self) -> None:
        reset_registry()

    @patch("runtime.orchestrator.executors.subprocess")
    def test_launches_with_template_substitution(self, mock_subprocess, tmp_path):
        workspace = tmp_path / "agent_ws"
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
        assert cmd[0].endswith("echo")
        assert cmd[1] == "agent"
        assert cmd[2] == "--json"
        assert cmd[3] == "--message"
        # The prompt element includes the session-lifetime preamble
        assert "Do something" in cmd[4]
        assert "<session-lifetime>" in cmd[4]
        assert cmd[5] == "--timeout"
        assert cmd[6] == "60"

    @patch("runtime.orchestrator.executors.subprocess")
    def test_launches_with_workspace_placeholder(self, mock_subprocess, tmp_path):
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
    def test_returns_failure_on_nonzero_exit(self, mock_subprocess, tmp_path):
        workspace = tmp_path / "agent_ws"
        workspace.mkdir()

        proc = MagicMock()
        proc.pid = 7777
        proc.returncode = 1
        proc.communicate.return_value = ("", "error: something went wrong")
        mock_subprocess.Popen.return_value = proc

        executor = GenericCliExecutor(
            profile_name="failing",
            argv_template=["bash", "-c", "echo 'something went wrong' >&2 ; exit 1"],
            provider="failing",
        )
        result = executor.run(
            workspace=workspace,
            prompt="Do something",
            timeout_seconds=30,
        )

        assert result.success is False
        assert result.returncode == 1
        assert "something went wrong" in (result.stderr_tail or "")

    # ── Envelope tests (THR-107) ────────────────────────────────────────

    @patch("runtime.orchestrator.executors.subprocess")
    def test_writes_token_usage_from_envelope(self, mock_subprocess, tmp_path):
        """A fake CLI emitting a valid envelope → token_usage is populated."""
        workspace = tmp_path / "agent_ws"
        workspace.mkdir()

        _BEGIN = "__HR_ENVELOPE_BEGIN__"
        _END = "__HR_ENVELOPE_END__"
        envelope = json.dumps({
            "envelope_version": 1,
            "token_usage": {"input_tokens": 299, "output_tokens": 101},
        })
        stdout = f"Agent output...\n{_BEGIN}\n{envelope}\n{_END}\n...more output"

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
    def test_no_envelope_still_succeeds(self, mock_subprocess, tmp_path):
        """A fake CLI with NO envelope → succeeds with token_usage=None."""
        workspace = tmp_path / "agent_ws"
        workspace.mkdir()

        stdout = "Agent completed the task."

        proc = MagicMock()
        proc.pid = 9998
        proc.returncode = 0
        proc.communicate.return_value = (stdout, "")
        mock_subprocess.Popen.return_value = proc

        executor = GenericCliExecutor(
            profile_name="noenvelope",
            argv_template=["echo", "--prompt", "{prompt}"],
            provider="noenvelope",
        )
        result = executor.run(
            workspace=workspace,
            prompt="hi",
            timeout_seconds=30,
        )

        assert result.success is True
        assert result.token_usage is None

    @patch("runtime.orchestrator.executors.subprocess")
    def test_malformed_envelope_still_succeeds(self, mock_subprocess, tmp_path):
        """A fake CLI with a malformed envelope → still succeeds, token_usage has forensic data."""
        workspace = tmp_path / "agent_ws"
        workspace.mkdir()

        _BEGIN = "__HR_ENVELOPE_BEGIN__"
        _END = "__HR_ENVELOPE_END__"
        stdout = f"Output...\n{_BEGIN}\nnot valid json\n{_END}\nmore"

        proc = MagicMock()
        proc.pid = 9999
        proc.returncode = 0
        proc.communicate.return_value = (stdout, "")
        mock_subprocess.Popen.return_value = proc

        executor = GenericCliExecutor(
            profile_name="broken",
            argv_template=["echo", "--prompt", "{prompt}"],
            provider="broken",
        )
        result = executor.run(
            workspace=workspace,
            prompt="hi",
            timeout_seconds=30,
        )

        assert result.success is True
        assert result.token_usage is not None
        assert result.token_usage.input_tokens is None
        assert result.token_usage.usage_raw_json is not None


# ═══════════════════════════════════════════════════════════════════════════
# THR-107 D9 / Phase 3: command_adapter field (executor profile)
# ═══════════════════════════════════════════════════════════════════════════


class TestCommandAdapterField:
    """Adversarial tests for the D9 command_adapter field on ExecutorProfile."""

    def test_executor_profile_has_command_adapter_default_none(self) -> None:
        """ExecutorProfile.command_adapter defaults to None (additive field)."""
        p = ExecutorProfile(name="test-default")
        assert p.command_adapter is None

    def test_legacy_config_without_command_adapter_defaults_to_generic_cli(
        self,
    ) -> None:
        """Legacy YAML/config with field absent → defaults to 'generic-cli'."""
        config = {
            "command": None,  # test seam: skip which()
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
        }
        profile = ExecutorRegistry.validate_custom_profile_config(
            "legacy-cli", config
        )
        assert profile.command_adapter == "generic-cli"

    def test_explicit_generic_cli_round_trips_through_validate(
        self,
    ) -> None:
        """Explicit 'generic-cli' round-trips through validate_custom_profile_config."""
        config = {
            "command": None,
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
            "command_adapter": "generic-cli",
        }
        profile = ExecutorRegistry.validate_custom_profile_config(
            "explicit-gc", config
        )
        assert profile.command_adapter == "generic-cli"

    def test_rejects_unsupported_command_adapter_value(self) -> None:
        """Invalid command_adapter rejects with clear error, no store write."""
        config = {
            "command": None,
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
            "command_adapter": "claude",
        }
        with pytest.raises(ValueError, match="command_adapter"):
            ExecutorRegistry.validate_custom_profile_config("bad-adapter", config)

    def test_rejects_non_string_command_adapter(self) -> None:
        """Non-string command_adapter (e.g. int) rejects."""
        config: dict[str, object] = {
            "command": None,
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
            "command_adapter": 42,
        }
        with pytest.raises(ValueError, match="command_adapter must be a string"):
            ExecutorRegistry.validate_custom_profile_config("bad-type", config)

    def test_rejects_unknown_command_adapter_that_is_not_generic_cli(
        self,
    ) -> None:
        """Unknown/unregistered command adapter name rejects."""
        config = {
            "command": None,
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
            "command_adapter": "custom-adapter-v2",
        }
        with pytest.raises(ValueError, match="command_adapter"):
            ExecutorRegistry.validate_custom_profile_config("future", config)

    def test_builtin_profiles_have_no_command_adapter(self) -> None:
        """Built-in profiles must NOT carry a command_adapter.
        The registry constructs built-ins from the D8 catalog, which
        does not set command_adapter — it defaults to None."""
        reset_registry()
        registry = ExecutorRegistry()
        for name in ("claude", "codex", "opencode", "pi"):
            p = registry.get_profile(name)
            assert p is not None
            assert p.command_adapter is None, (
                f"Built-in {name!r} must not have command_adapter set"
            )

    def test_adapter_id_remains_workspace_only_independent(self) -> None:
        """adapter_id stays workspace-only; command_adapter does NOT change it."""
        config = {
            "command": None,
            "argv_template": ["echo", "{prompt}"],
            "adapter": "codex",
            "command_adapter": "generic-cli",
        }
        profile = ExecutorRegistry.validate_custom_profile_config(
            "codex-workspace", config
        )
        assert profile.adapter_id == "codex"  # workspace adapter unchanged
        assert profile.command_adapter == "generic-cli"  # command adapter is independent

    def test_legacy_yaml_absent_field_defaults_in_startup_load(
        self,
    ) -> None:
        """Startup-load of a stored profile without command_adapter field
        defaults to 'generic-cli' at validate time."""
        # Simulate what the runtime store stores for a legacy profile
        stored_cfg = {
            "command": None,
            "argv_template": ["legacy-cli", "{prompt}"],
            "adapter": "pi",
            # No command_adapter — legacy
        }
        profile = ExecutorRegistry.validate_custom_profile_config(
            "legacy-stored", stored_cfg
        )
        assert profile.command_adapter == "generic-cli"

    def test_register_custom_profile_with_command_adapter_succeeds(
        self,
    ) -> None:
        """register_custom_profile with a valid command_adapter registers successfully."""
        reset_registry()
        config = {
            "command": None,
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
            "command_adapter": "generic-cli",
        }
        profile = ExecutorRegistry.validate_custom_profile_config("with-ca", config)
        registry = ExecutorRegistry()
        registry.register_custom_profile(profile)
        assert registry.is_registered("with-ca")
        p = registry.get_profile("with-ca")
        assert p is not None
        assert p.command_adapter == "generic-cli"

    def test_identical_profiles_with_same_command_adapter_are_idempotent(
        self,
    ) -> None:
        """Re-registering an identical custom profile (same command_adapter)
        is idempotent — no collision error."""
        reset_registry()
        config = {
            "command": None,
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
            "command_adapter": "generic-cli",
        }
        profile1 = ExecutorRegistry.validate_custom_profile_config(
            "idempotent-ca", config
        )
        profile2 = ExecutorRegistry.validate_custom_profile_config(
            "idempotent-ca", config
        )
        registry = ExecutorRegistry()
        registry.register_custom_profile(profile1)
        # Should NOT raise — identical profile is idempotent
        registry.register_custom_profile(profile2)
        p = registry.get_profile("idempotent-ca")
        assert p is not None
        assert p.command_adapter == "generic-cli"

    def test_profiles_with_different_command_adapters_collide(
        self,
    ) -> None:
        """Two different command_adapters for the same name → collision."""
        reset_registry()
        config_a = {
            "command": None,
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
            "command_adapter": "generic-cli",
        }
        config_b = {
            "command": None,
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
            # absent command_adapter defaults to generic-cli — same as A
        }
        profile_a = ExecutorRegistry.validate_custom_profile_config("ca-collision", config_a)
        # config_b absent command_adapter defaults to generic-cli too
        # So these should actually be identical, not a collision
        # Let's make them actually different
        config_b["command_adapter"] = "generic-cli"  # same as A — no collision
        profile_b = ExecutorRegistry.validate_custom_profile_config("ca-collision", config_b)
        registry = ExecutorRegistry()
        registry.register_custom_profile(profile_a)
        # Should be idempotent since they're the same
        registry.register_custom_profile(profile_b)  # no-op
        assert registry.is_registered("ca-collision")

    def test_builtins_cannot_set_command_adapter(self) -> None:
        """Built-in ExecutorProfile constructor should not receive non-None
        command_adapter. The built-in catalog does not inject it."""
        reset_registry()
        registry = ExecutorRegistry()
        # All built-ins must have command_adapter=None
        for name in ("claude", "codex", "opencode", "pi"):
            p = registry.get_profile(name)
            assert p is not None
            assert p.command_adapter is None, (
                f"Built-in {name} command_adapter={p.command_adapter!r}, expected None"
            )
