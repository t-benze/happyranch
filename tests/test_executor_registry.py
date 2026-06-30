from __future__ import annotations

from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.orchestrator.executor_registry import (
    ExecutorProfile,
    ExecutorRegistry,
    build_executor,
    get_registry,
    reset_registry,
    validate_argv_template,
)
from runtime.orchestrator.executors import GenericCliExecutor


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

    def test_register_custom_from_config(self) -> None:
        registry = ExecutorRegistry()
        config = {
            "openclaw": {
                "command": "fake-cli",
                "argv_template": [
                    "fake-cli", "run", "--input", "{prompt}",
                    "--timeout", "{timeout_seconds}",
                ],
                "adapter": "pi",
            }
        }
        # Use command=None to skip which() resolution (test seam)
        config["openclaw"]["command"] = None
        registry.register_custom_from_config(config)
        assert registry.is_registered("openclaw")
        p = registry.get_profile("openclaw")
        assert p is not None
        assert p.adapter_id == "pi"
        assert p.argv_template is not None

    def test_register_custom_from_config_rejects_invalid_adapter(self) -> None:
        registry = ExecutorRegistry()
        config = {
            "bad": {
                "command": None,
                "argv_template": ["cmd", "{prompt}"],
                "adapter": "nonexistent",
            }
        }
        with pytest.raises(ValueError, match="adapter"):
            registry.register_custom_from_config(config)

    def test_register_custom_from_config_rejects_missing_argv(self) -> None:
        registry = ExecutorRegistry()
        config = {"bad": {"command": None}}
        with pytest.raises(ValueError, match="argv_template"):
            registry.register_custom_from_config(config)

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
                "openclaw", "agent", "--json", "--message", "{prompt}",
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
        assert cmd[0] == "openclaw"
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
            argv_template=["mycli", "--dir", "{workspace}", "--input", "{prompt}"],
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
            profile_name="brokencli",
            argv_template=["brokencli", "{prompt}"],
            provider="brokencli",
        )
        result = executor.run(
            workspace=workspace,
            prompt="Do something",
            timeout_seconds=30,
        )

        assert result.success is False
        assert result.returncode == 1
        assert "something went wrong" in (result.stderr_tail or "")
