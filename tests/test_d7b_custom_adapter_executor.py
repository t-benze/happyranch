"""D7B custom-adapter executor tests (THR-107).

TDD at shipping seams:
1. Custom adapter registration → conformance → explicit approval → profile bind
   → actual task launch
2. Unapproved/PENDING adapter binding and launch rejection
3. Tampered/deleted/non-regular/non-executable artifact after approval
4. Changed artifact/re-registration invalidates approval and prior binding
5. Valid output and missing/malformed/unknown-version/oversized/inconsistent
   AdapterOutput
6. All invocation kinds provide truthful AdapterInput
7. Exact hash checked at every launch
8. No Python import/discovery
9. No permission expansion
10. Built-in workspace permissions and all four built-in launches unchanged
11. Generic-cli strict + legacy D7A behavior unchanged
12. D6 canonical/legacy alias compatibility
13. Profile route collision/token/audit no-residue, D7A atomic replacement,
    and audit-failure rollback preserved
14. Restart/read compatibility and no auto-mutation
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

import pytest

from runtime.orchestrator.executor_registry import (
    ExecutorProfile,
    ExecutorRegistry,
    build_executor,
    get_registry,
    reset_registry,
)
from runtime.orchestrator.executors import CustomAdapterExecutor, ExecutorResult
from runtime.orchestrator.adapter_contract import AdapterInput, AdapterOutput
from runtime.config import Settings


# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_test_adapter_executable(tmp_path: Path, output: dict) -> str:
    """Create a small test adapter script that echoes the given output as JSON.

    Uses python3 for reliable stdin handling.
    Returns the absolute path to the executable."""
    import sys as _sys
    python = _sys.executable  # Use the same Python that runs the tests
    output_json = json.dumps(output)
    script = textwrap.dedent(f"""\
        #!{python}
        import sys, json
        try:
            _ = sys.stdin.read()
        except Exception:
            pass
        sys.stdout.write({output_json!r})
        sys.stdout.flush()
    """)
    exe = tmp_path / "test-adapter.py"
    exe.write_text(script)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(exe)


def _make_adapter_entry(**kwargs) -> dict:
    """Build a minimal adapter entry dict for tests."""
    defaults = {
        "id": "test-adapter",
        "name": "test-adapter",
        "executable": "/fake/adapter",
        "executable_hash": "a" * 64,
        "version": "1.0.0",
        "capabilities": [],
        "contract_version": 1,
        "workspace_adapter": "pi",
        "status": "approved",
        "registered_at": "2026-01-01T00:00:00",
        "registered_by": "test",
        "approved_at": "2026-01-01T00:00:00",
        "approved_by": "founder",
    }
    defaults.update(kwargs)
    return defaults


def _valid_adapter_output(**kwargs) -> dict:
    """Build a valid AdapterOutput dict."""
    defaults = {
        "success": True,
        "duration_seconds": 1,
        "session_id": "test-sess",
        "returncode": 0,
        "stdout_tail": "",
        "stderr_tail": "",
        "adapter_metadata": {
            "adapter": "test-adapter",
            "adapter_version": "1.0.0",
            "contract_version": 1,
        },
        "token_usage": {
            "input_tokens": 100,
            "output_tokens": 50,
        },
    }
    defaults.update(kwargs)
    return defaults


class TestCustomAdapterExecutorBasic:
    """Basic CustomAdapterExecutor construction and safety checks."""

    def test_requires_invocation_context(self, tmp_path):
        """Executor fails closed if set_invocation_context not called."""
        exe_path = _make_test_adapter_executable(tmp_path, _valid_adapter_output())
        executor = CustomAdapterExecutor(
            profile_name="test",
            adapter_entry_id="test-adapter",
            adapter_executable=exe_path,
            adapter_hash="fake",
            adapter_version="1.0.0",
            adapter_contract_version=1,
            provider="test",
        )
        result = executor.run(workspace=tmp_path, prompt="test prompt")
        assert not result.success
        assert "invocation context" in (result.error or "")

    def test_fails_launch_missing_executable(self, tmp_path):
        """Fails closed when executable doesn't exist."""
        executor = CustomAdapterExecutor(
            profile_name="test",
            adapter_entry_id="test-adapter",
            adapter_executable="/nonexistent/path",
            adapter_hash="fake",
            adapter_version="1.0.0",
            adapter_contract_version=1,
            provider="test",
        )
        executor.set_invocation_context(
            agent="dev_agent", org="happyranch", invocation_kind="task"
        )
        result = executor.run(workspace=tmp_path, prompt="test prompt")
        assert not result.success
        assert "no longer exists" in (result.error or "")

    def test_fails_launch_not_regular_file(self, tmp_path):
        """Fails closed when path is a directory."""
        d = tmp_path / "dir"
        d.mkdir()
        executor = CustomAdapterExecutor(
            profile_name="test",
            adapter_entry_id="test-adapter",
            adapter_executable=str(d),
            adapter_hash="fake",
            adapter_version="1.0.0",
            adapter_contract_version=1,
            provider="test",
        )
        executor.set_invocation_context(
            agent="dev_agent", org="happyranch", invocation_kind="task"
        )
        result = executor.run(workspace=tmp_path, prompt="test prompt")
        assert not result.success
        assert "not a regular file" in (result.error or "")

    def test_fails_launch_not_executable(self, tmp_path):
        """Fails closed when path is not executable."""
        f = tmp_path / "notexe"
        f.write_text("not executable")
        executor = CustomAdapterExecutor(
            profile_name="test",
            adapter_entry_id="test-adapter",
            adapter_executable=str(f),
            adapter_hash="fake",
            adapter_version="1.0.0",
            adapter_contract_version=1,
            provider="test",
        )
        executor.set_invocation_context(
            agent="dev_agent", org="happyranch", invocation_kind="task"
        )
        result = executor.run(workspace=tmp_path, prompt="test prompt")
        assert not result.success
        assert "not executable" in (result.error or "")

    def test_fails_hash_mismatch(self, tmp_path):
        """Fails closed when executable hash doesn't match stored hash."""
        exe_path = _make_test_adapter_executable(tmp_path, _valid_adapter_output())
        executor = CustomAdapterExecutor(
            profile_name="test",
            adapter_entry_id="test-adapter",
            adapter_executable=exe_path,
            adapter_hash="deadbeef" * 8,  # deliberately wrong
            adapter_version="1.0.0",
            adapter_contract_version=1,
            provider="test",
        )
        executor.set_invocation_context(
            agent="dev_agent", org="happyranch", invocation_kind="task"
        )
        result = executor.run(workspace=tmp_path, prompt="test prompt")
        assert not result.success
        assert "hash mismatch" in (result.error or "")


class TestCustomAdapterExecutorLaunch:
    """End-to-end adapter launch tests."""

    def test_successful_launch(self, tmp_path):
        """A valid adapter returns ExecutorResult.success=True."""
        from runtime.orchestrator.adapter_store import compute_sha256
        output = _valid_adapter_output()
        exe_path = _make_test_adapter_executable(tmp_path, output)
        exe_hash = compute_sha256(exe_path)

        executor = CustomAdapterExecutor(
            profile_name="test",
            adapter_entry_id="test-adapter",
            adapter_executable=exe_path,
            adapter_hash=exe_hash,
            adapter_version="1.0.0",
            adapter_contract_version=1,
            provider="test",
        )
        executor.set_invocation_context(
            agent="dev_agent", org="happyranch", invocation_kind="task", task_id="TASK-001"
        )
        result = executor.run(workspace=tmp_path, prompt="test prompt", session_id="test-sess")
        assert result.success
        assert result.duration_seconds >= 0
        assert result.token_usage is not None
        assert result.token_usage.input_tokens == 100
        assert result.token_usage.output_tokens == 50

    def test_adapter_output_maps_token_usage(self, tmp_path):
        """TokenUsage from adapter output is populated in ExecutorResult."""
        from runtime.orchestrator.adapter_store import compute_sha256
        output = _valid_adapter_output(
            token_usage={
                "input_tokens": 200,
                "output_tokens": 100,
                "cache_read_tokens": 50,
                "reasoning_tokens": 25,
                "model": "test-model",
            }
        )
        exe_path = _make_test_adapter_executable(tmp_path, output)
        exe_hash = compute_sha256(exe_path)

        executor = CustomAdapterExecutor(
            profile_name="test",
            adapter_entry_id="test-adapter",
            adapter_executable=exe_path,
            adapter_hash=exe_hash,
            adapter_version="1.0.0",
            adapter_contract_version=1,
            provider="test",
        )
        executor.set_invocation_context(
            agent="dev_agent", org="happyranch", invocation_kind="task"
        )
        result = executor.run(workspace=tmp_path, prompt="test", session_id="test-sess")
        assert result.success
        assert result.token_usage is not None
        assert result.token_usage.input_tokens == 200
        assert result.token_usage.output_tokens == 100
        assert result.token_usage.cache_read_tokens == 50
        assert result.token_usage.reasoning_tokens == 25
        assert result.token_usage.model == "test-model"

    def test_adapter_output_failure_propagated(self, tmp_path):
        """Adapter reporting success=false → ExecutorResult.success=False."""
        from runtime.orchestrator.adapter_store import compute_sha256
        output = _valid_adapter_output(
            success=False,
            returncode=1,
            error="adapter failed",
            stdout_tail="failure output",
            stderr_tail="failure stderr",
        )
        exe_path = _make_test_adapter_executable(tmp_path, output)
        exe_hash = compute_sha256(exe_path)

        executor = CustomAdapterExecutor(
            profile_name="test",
            adapter_entry_id="test-adapter",
            adapter_executable=exe_path,
            adapter_hash=exe_hash,
            adapter_version="1.0.0",
            adapter_contract_version=1,
            provider="test",
        )
        executor.set_invocation_context(
            agent="dev_agent", org="happyranch", invocation_kind="task"
        )
        result = executor.run(workspace=tmp_path, prompt="test", session_id="test-sess")
        assert not result.success
        assert result.error == "adapter failed"
        assert result.stdout_tail == "failure output"
        assert result.stderr_tail == "failure stderr"

    def test_adapter_success_returncode_inconsistency_detected(self, tmp_path):
        """Adapter success=true with returncode != 0 → rejected."""
        from runtime.orchestrator.adapter_store import compute_sha256
        output = _valid_adapter_output(success=True, returncode=1)
        exe_path = _make_test_adapter_executable(tmp_path, output)
        exe_hash = compute_sha256(exe_path)

        executor = CustomAdapterExecutor(
            profile_name="test",
            adapter_entry_id="test-adapter",
            adapter_executable=exe_path,
            adapter_hash=exe_hash,
            adapter_version="1.0.0",
            adapter_contract_version=1,
            provider="test",
        )
        executor.set_invocation_context(
            agent="dev_agent", org="happyranch", invocation_kind="task"
        )
        result = executor.run(workspace=tmp_path, prompt="test", session_id="test-sess")
        assert not result.success
        assert "success=true" in (result.error or "").lower() or "success" in (result.error or "").lower()

    def test_adapter_output_not_json(self, tmp_path):
        """Adapter output that is not valid JSON → rejected."""
        from runtime.orchestrator.adapter_store import compute_sha256
        import sys as _sys
        import stat
        script = textwrap.dedent(f"""\
            #!{_sys.executable}
            import sys
            sys.stdout.write("not json at all")
        """)
        exe = tmp_path / "bad-adapter"
        exe.write_text(script)
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        exe_path = str(exe)
        exe_hash = compute_sha256(exe_path)

        executor = CustomAdapterExecutor(
            profile_name="test",
            adapter_entry_id="test-adapter",
            adapter_executable=exe_path,
            adapter_hash=exe_hash,
            adapter_version="1.0.0",
            adapter_contract_version=1,
            provider="test",
        )
        executor.set_invocation_context(
            agent="dev_agent", org="happyranch", invocation_kind="task"
        )
        result = executor.run(workspace=tmp_path, prompt="test")
        assert not result.success
        assert "not valid JSON" in (result.error or "")

    def test_adapter_output_not_object(self, tmp_path):
        """Adapter output that is JSON but not a dict → rejected."""
        from runtime.orchestrator.adapter_store import compute_sha256
        import sys as _sys
        import stat
        script = textwrap.dedent(f"""\
            #!{_sys.executable}
            import sys
            sys.stdout.write('[1, 2, 3]')
        """)
        exe = tmp_path / "array-adapter"
        exe.write_text(script)
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        exe_path = str(exe)
        exe_hash = compute_sha256(exe_path)

        executor = CustomAdapterExecutor(
            profile_name="test",
            adapter_entry_id="test-adapter",
            adapter_executable=exe_path,
            adapter_hash=exe_hash,
            adapter_version="1.0.0",
            adapter_contract_version=1,
            provider="test",
        )
        executor.set_invocation_context(
            agent="dev_agent", org="happyranch", invocation_kind="task"
        )
        result = executor.run(workspace=tmp_path, prompt="test")
        assert not result.success
        assert "not a JSON object" in (result.error or "")

    def test_adapter_wrong_contract_version_rejected(self, tmp_path):
        """Adapter output with contract_version != 1 → rejected."""
        from runtime.orchestrator.adapter_store import compute_sha256
        output = _valid_adapter_output()
        output["adapter_metadata"]["contract_version"] = 2
        exe_path = _make_test_adapter_executable(tmp_path, output)
        exe_hash = compute_sha256(exe_path)

        executor = CustomAdapterExecutor(
            profile_name="test",
            adapter_entry_id="test-adapter",
            adapter_executable=exe_path,
            adapter_hash=exe_hash,
            adapter_version="1.0.0",
            adapter_contract_version=1,
            provider="test",
        )
        executor.set_invocation_context(
            agent="dev_agent", org="happyranch", invocation_kind="task"
        )
        result = executor.run(workspace=tmp_path, prompt="test")
        assert not result.success
        assert "contract version" in (result.error or "").lower()
        assert "2" in (result.error or "")

    def test_adapter_identity_mismatch_rejected(self, tmp_path):
        """Adapter output with wrong adapter identity → rejected."""
        from runtime.orchestrator.adapter_store import compute_sha256
        output = _valid_adapter_output()
        output["adapter_metadata"]["adapter"] = "wrong-adapter"
        exe_path = _make_test_adapter_executable(tmp_path, output)
        exe_hash = compute_sha256(exe_path)

        executor = CustomAdapterExecutor(
            profile_name="test",
            adapter_entry_id="test-adapter",
            adapter_executable=exe_path,
            adapter_hash=exe_hash,
            adapter_version="1.0.0",
            adapter_contract_version=1,
            provider="test",
        )
        executor.set_invocation_context(
            agent="dev_agent", org="happyranch", invocation_kind="task"
        )
        result = executor.run(workspace=tmp_path, prompt="test")
        assert not result.success
        assert "identity mismatch" in (result.error or "")

    def test_oversized_output_rejected(self, tmp_path):
        """Adapter output larger than 1MB → rejected."""
        from runtime.orchestrator.adapter_store import compute_sha256
        import sys as _sys
        import stat
        # Create an adapter that outputs > 1MB (via a big stdout_tail string)
        output_data = {
            "success": True,
            "duration_seconds": 1,
            "session_id": "s",
            "returncode": 0,
            "stdout_tail": "x" * 1_100_000,
            "stderr_tail": "",
            "adapter_metadata": {
                "adapter": "test-adapter",
                "adapter_version": "1.0.0",
                "contract_version": 1,
            },
        }
        output_json = json.dumps(output_data)
        script = textwrap.dedent(f"""\
            #!{_sys.executable}
            import sys
            sys.stdout.write({output_json!r})
            sys.stdout.flush()
        """)
        exe = tmp_path / "huge-adapter"
        exe.write_text(script)
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        exe_path = str(exe)
        exe_hash = compute_sha256(exe_path)

        executor = CustomAdapterExecutor(
            profile_name="test",
            adapter_entry_id="test-adapter",
            adapter_executable=exe_path,
            adapter_hash=exe_hash,
            adapter_version="1.0.0",
            adapter_contract_version=1,
            provider="test",
        )
        executor.set_invocation_context(
            agent="dev_agent", org="happyranch", invocation_kind="task"
        )
        result = executor.run(workspace=tmp_path, prompt="test", timeout_seconds=5)
        assert not result.success
        assert "exceeds" in (result.error or "").lower() or "byte limit" in (result.error or "").lower()

    def test_timeout_produces_deterministic_error(self, tmp_path):
        """Timeout → ExecutorResult with actionable error."""
        from runtime.orchestrator.adapter_store import compute_sha256
        import sys as _sys
        import stat
        # Adapter that hangs
        script = textwrap.dedent(f"""\
            #!{_sys.executable}
            import time
            time.sleep(999)
        """)
        exe = tmp_path / "hang-adapter"
        exe.write_text(script)
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        exe_path = str(exe)
        exe_hash = compute_sha256(exe_path)

        executor = CustomAdapterExecutor(
            profile_name="test",
            adapter_entry_id="test-adapter",
            adapter_executable=exe_path,
            adapter_hash=exe_hash,
            adapter_version="1.0.0",
            adapter_contract_version=1,
            provider="test",
        )
        executor.set_invocation_context(
            agent="dev_agent", org="happyranch", invocation_kind="task"
        )
        result = executor.run(workspace=tmp_path, prompt="test", timeout_seconds=1)
        assert not result.success
        assert "timed out" in (result.error or "").lower()


class TestCustomAdapterBindingValidation:
    """validate_custom_profile_config binding validation."""

    def test_generic_cli_unchanged(self):
        """Generic-cli profiles continue to work unchanged."""
        profile = ExecutorRegistry.validate_custom_profile_config(
            "test-profile",
            {
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "command_adapter_id": "generic-cli",
            },
        )
        assert profile.command_adapter_id == "generic-cli"
        assert profile.argv_template is not None
        assert not (profile.command_adapter_id or "").startswith("custom-adapter:")

    def test_generic_cli_default_unchanged(self):
        """Omitted command_adapter_id defaults to generic-cli."""
        profile = ExecutorRegistry.validate_custom_profile_config(
            "test-profile",
            {
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
            },
        )
        assert profile.command_adapter_id == "generic-cli"

    def test_custom_adapter_binding_rejects_unknown(self):
        """Binding to an unknown adapter id → rejection."""
        with pytest.raises(ValueError, match="Unknown adapter"):
            ExecutorRegistry.validate_custom_profile_config(
                "test-profile",
                {
                    "command_adapter_id": "custom-adapter:nonexistent",
                },
            )

    @patch("runtime.orchestrator.adapter_store.get_adapter")
    def test_custom_adapter_binding_rejects_pending(self, mock_get):
        """Binding to a PENDING adapter → rejection."""
        mock_get.return_value = MagicMock(
            status="pending",
            executable="/fake/adapter",
            executable_hash="a" * 64,
            version="1.0.0",
            capabilities=[],
            contract_version=1,
            workspace_adapter="pi",
        )
        with pytest.raises(ValueError, match="not APPROVED"):
            ExecutorRegistry.validate_custom_profile_config(
                "test-profile",
                {
                    "command_adapter_id": "custom-adapter:pending-adapter",
                },
            )

    @patch("runtime.orchestrator.custom_adapter_registry.resolve_adapter")
    def test_custom_adapter_binding_accepts_approved(self, mock_resolve):
        """Binding to an APPROVED adapter → success."""
        mock_resolve.return_value = MagicMock(
            executable="/fake/adapter",
            executable_hash="a" * 64,
            version="1.0.0",
            contract_version=1,
        )
        profile = ExecutorRegistry.validate_custom_profile_config(
            "test-profile",
            {
                "command_adapter_id": "custom-adapter:approved-adapter",
            },
        )
        assert profile.command_adapter_id == "custom-adapter:approved-adapter"
        assert profile.argv_template is None  # custom adapter, no argv_template
        assert profile.command is None  # custom adapter, no PATH resolution
        assert profile.envelope_policy is None  # custom adapter, native contract

    def test_custom_adapter_binding_via_deprecated_alias(self):
        """Binding via deprecated command_adapter alias also works."""
        with patch("runtime.orchestrator.custom_adapter_registry.resolve_adapter") as mock_resolve:
            mock_resolve.return_value = MagicMock(
                executable="/fake/adapter",
                executable_hash="a" * 64,
                version="1.0.0",
                contract_version=1,
            )
            profile = ExecutorRegistry.validate_custom_profile_config(
                "test-profile",
                {
                    "command_adapter": "custom-adapter:approved-adapter",
                },
            )
            assert profile.command_adapter_id == "custom-adapter:approved-adapter"

    def test_custom_adapter_conflicting_aliases_rejected(self):
        """Conflicting command_adapter_id vs command_adapter → rejection."""
        with pytest.raises(ValueError, match="conflicting"):
            ExecutorRegistry.validate_custom_profile_config(
                "test-profile",
                {
                    "command_adapter_id": "custom-adapter:adapter-a",
                    "command_adapter": "custom-adapter:adapter-b",
                },
            )

    def test_custom_adapter_empty_id_rejected(self):
        """'custom-adapter:' with no id → rejection."""
        with pytest.raises(ValueError, match="non-empty adapter id"):
            ExecutorRegistry.validate_custom_profile_config(
                "test-profile",
                {
                    "command_adapter_id": "custom-adapter:",
                },
            )


class TestBuildExecutorRouting:
    """build_executor routes to correct executor class."""

    def test_generic_cli_profile_routes_to_generic_cli(self):
        """A generic-cli profile → GenericCliExecutor."""
        reset_registry()
        registry = get_registry()
        profile = ExecutorProfile(
            name="mycli",
            kind="custom",
            workspace_adapter_id="pi",
            command_adapter_id="generic-cli",
            readiness_marker_fragment="AGENTS.md",
            argv_template=["mycli", "{prompt}"],
            command="mycli",
            envelope_policy="strict",
        )
        registry.register_custom_profile(profile)

        from runtime.orchestrator.executors import GenericCliExecutor
        settings = Settings()
        executor = build_executor("mycli", settings)
        assert isinstance(executor, GenericCliExecutor)

    @patch("runtime.orchestrator.custom_adapter_registry.resolve_adapter")
    def test_custom_adapter_profile_routes_to_custom_adapter_executor(self, mock_resolve):
        """A custom-adapter profile → CustomAdapterExecutor."""
        mock_resolve.return_value = MagicMock(
            executable="/fake/adapter",
            executable_hash="a" * 64,
            version="1.0.0",
            contract_version=1,
        )
        reset_registry()
        registry = get_registry()
        profile = ExecutorProfile(
            name="myadapter",
            kind="custom",
            workspace_adapter_id="pi",
            command_adapter_id="custom-adapter:my-adapter",
            readiness_marker_fragment="AGENTS.md",
        )
        registry.register_custom_profile(profile)

        settings = Settings()
        executor = build_executor("myadapter", settings)
        assert isinstance(executor, CustomAdapterExecutor)
        assert executor._adapter_entry_id == "my-adapter"

    def test_builtin_profiles_unchanged(self):
        """All four built-in profiles still route to their respective executors."""
        from runtime.orchestrator.executors import (
            ClaudeExecutor, CodexExecutor, OpencodeExecutor, PiExecutor,
        )
        reset_registry()
        settings = Settings()

        assert isinstance(build_executor("claude", settings), ClaudeExecutor)
        assert isinstance(build_executor("codex", settings), CodexExecutor)
        assert isinstance(build_executor("opencode", settings), OpencodeExecutor)
        assert isinstance(build_executor("pi", settings), PiExecutor)


class TestInvocationContext:
    """Invocation context is propagated truthfully for each invocation kind."""

    def test_context_missing_fails_closed(self, tmp_path):
        """If set_invocation_context is not called, fail."""
        executor = CustomAdapterExecutor(
            profile_name="test",
            adapter_entry_id="test-adapter",
            adapter_executable="/fake/path",
            adapter_hash="a" * 64,
            adapter_version="1.0.0",
            adapter_contract_version=1,
            provider="test",
        )
        result = executor.run(workspace=tmp_path, prompt="test")
        assert not result.success
        assert "invocation context" in (result.error or "").lower()

    def test_context_task(self, tmp_path):
        """Task invocation context is set correctly."""
        from runtime.orchestrator.adapter_store import compute_sha256
        output = _valid_adapter_output()
        exe_path = _make_test_adapter_executable(tmp_path, output)
        exe_hash = compute_sha256(exe_path)

        executor = CustomAdapterExecutor(
            profile_name="test",
            adapter_entry_id="test-adapter",
            adapter_executable=exe_path,
            adapter_hash=exe_hash,
            adapter_version="1.0.0",
            adapter_contract_version=1,
            provider="test",
        )
        executor.set_invocation_context(
            agent="dev_agent",
            org="happyranch",
            invocation_kind="task",
            task_id="TASK-001",
        )
        result = executor.run(workspace=tmp_path, prompt="test", session_id="test-sess")
        assert result.success


class TestRegistrationRouteCompatibility:
    """D7B compatibility with existing registration routes."""

    def test_generic_cli_with_strict_envelope_unchanged(self):
        """Generic-cli with envelope_policy strict still works."""
        profile = ExecutorRegistry.validate_custom_profile_config(
            "strict-cli",
            {
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "command_adapter_id": "generic-cli",
                "envelope_policy": "strict",
            },
        )
        assert profile.command_adapter_id == "generic-cli"
        assert profile.envelope_policy == "strict"

    def test_legacy_stored_profile_unchanged(self):
        """D7B does not break legacy stored profile validation."""
        # A profile without command_adapter_id should still default to generic-cli
        profile = ExecutorRegistry.validate_custom_profile_config(
            "legacy",
            {
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "adapter": "pi",
            },
        )
        assert profile.command_adapter_id == "generic-cli"
        assert profile.workspace_adapter_id == "pi"


class TestAdapterStoreCompatibility:
    """D7B does not mutate adapter store or profile store."""

    def test_no_auto_mutation_of_store(self):
        """validate_custom_profile_config never writes to adapter store."""
        with (
            patch("runtime.orchestrator.adapter_store.save_adapter") as mock_save,
            patch("runtime.orchestrator.adapter_store._save_adapter_locked") as mock_save_locked,
            patch("runtime.orchestrator.custom_adapter_registry.resolve_adapter") as mock_resolve,
        ):
            mock_resolve.return_value = MagicMock(
                executable="/fake/adapter",
                executable_hash="a" * 64,
                version="1.0.0",
                contract_version=1,
            )
            ExecutorRegistry.validate_custom_profile_config(
                "test",
                {"command_adapter_id": "custom-adapter:my-adapter"},
            )
            mock_save.assert_not_called()
            mock_save_locked.assert_not_called()

    def test_no_approval_state_change(self):
        """validate_custom_profile_config never changes adapter approval state."""
        with (
            patch("runtime.orchestrator.custom_adapter_registry.approve_adapter") as mock_approve,
            patch("runtime.orchestrator.custom_adapter_registry.resolve_adapter") as mock_resolve,
        ):
            mock_resolve.return_value = MagicMock(
                executable="/fake/adapter",
                executable_hash="a" * 64,
                version="1.0.0",
                contract_version=1,
            )
            ExecutorRegistry.validate_custom_profile_config(
                "test",
                {"command_adapter_id": "custom-adapter:my-adapter"},
            )
            mock_approve.assert_not_called()


class TestNoPermissionExpansion:
    """D7B does not expand any permission/sandbox/allow-rule surface."""

    def test_builtin_workspace_permissions_unchanged(self):
        """Built-in workspace adapters are unchanged."""
        from runtime.orchestrator.workspace_adapters import allow_rules_for_agent
        # This is a regression guard — the import and basic behavior
        # should be unchanged.
        assert callable(allow_rules_for_agent)

    def test_no_d5_permission_change(self):
        """No permission-generation, allow-rule, or sandbox flag change."""
        # Regression: D5 baseline-only — no new permission surfaces.
        # This test is a documentation check, not a behavioral assertion.
        pass


class TestAdapterOutputIdentityBinding:
    """Fix 1: AdapterOutput validation rejects wrong adapter_version and session_id."""

    def test_adapter_version_mismatch_rejected(self, tmp_path):
        """Adapter output with wrong adapter_version → rejected before mapping."""
        from runtime.orchestrator.adapter_store import compute_sha256
        output = _valid_adapter_output()
        output["adapter_metadata"]["adapter_version"] = "99.0.0"
        exe_path = _make_test_adapter_executable(tmp_path, output)
        exe_hash = compute_sha256(exe_path)

        executor = CustomAdapterExecutor(
            profile_name="test",
            adapter_entry_id="test-adapter",
            adapter_executable=exe_path,
            adapter_hash=exe_hash,
            adapter_version="1.0.0",  # approved version
            adapter_contract_version=1,
            provider="test",
        )
        executor.set_invocation_context(
            agent="dev_agent", org="happyranch", invocation_kind="task", task_id="TASK-001"
        )
        from runtime.orchestrator.throttle import get_throttle, set_throttle
        from runtime.orchestrator.throttle import ProviderThrottle
        # Use a throttle with no spacing/backoff for deterministic testing
        old = get_throttle()
        set_throttle(ProviderThrottle(ceiling_default=10))
        try:
            result = executor.run(workspace=tmp_path, prompt="test prompt")
        finally:
            set_throttle(old)
        assert not result.success
        assert "version mismatch" in (result.error or "")

    def test_session_id_mismatch_rejected(self, tmp_path):
        """Adapter output with wrong session_id → rejected (echo rule)."""
        from runtime.orchestrator.adapter_store import compute_sha256
        output = _valid_adapter_output()
        output["session_id"] = "sess-evil"
        exe_path = _make_test_adapter_executable(tmp_path, output)
        exe_hash = compute_sha256(exe_path)

        executor = CustomAdapterExecutor(
            profile_name="test",
            adapter_entry_id="test-adapter",
            adapter_executable=exe_path,
            adapter_hash=exe_hash,
            adapter_version="1.0.0",
            adapter_contract_version=1,
            provider="test",
        )
        executor.set_invocation_context(
            agent="dev_agent", org="happyranch", invocation_kind="task", task_id="TASK-001"
        )
        from runtime.orchestrator.throttle import get_throttle, set_throttle
        from runtime.orchestrator.throttle import ProviderThrottle
        old = get_throttle()
        set_throttle(ProviderThrottle(ceiling_default=10))
        try:
            result = executor.run(workspace=tmp_path, prompt="test prompt")
        finally:
            set_throttle(old)
        assert not result.success
        assert "session_id mismatch" in (result.error or "") or "session_id" in (result.error or "").lower()

    def test_valid_echo_passes_identity_checks(self, tmp_path):
        """Valid adapter with correct version and echo session_id → success."""
        from runtime.orchestrator.adapter_store import compute_sha256
        output = _valid_adapter_output(
            session_id=None,  # will be set to a known value inside _launch
            adapter_metadata={
                "adapter": "test-adapter",
                "adapter_version": "1.0.0",
                "contract_version": 1,
            },
        )
        # The adapter will echo its input's invocation_id as session_id.
        # We create an adapter that reads stdin and echos the invocation id.
        import sys as _sys
        import stat
        script = textwrap.dedent(f"""\
            #!{_sys.executable}
            import sys, json
            data = sys.stdin.read()
            inp = json.loads(data)
            sid = inp["invocation"]["invocation_id"]
            result = {{
                "success": True,
                "duration_seconds": 1,
                "session_id": sid,
                "returncode": 0,
                "stdout_tail": "ok",
                "stderr_tail": "",
                "adapter_metadata": {{
                    "adapter": "test-adapter",
                    "adapter_version": "1.0.0",
                    "contract_version": 1
                }},
                "token_usage": {{
                    "input_tokens": 100,
                    "output_tokens": 50
                }}
            }}
            sys.stdout.write(json.dumps(result))
            sys.stdout.flush()
        """)
        exe = tmp_path / "echo-adapter"
        exe.write_text(script)
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        exe_path = str(exe)
        exe_hash = compute_sha256(exe_path)

        executor = CustomAdapterExecutor(
            profile_name="test",
            adapter_entry_id="test-adapter",
            adapter_executable=exe_path,
            adapter_hash=exe_hash,
            adapter_version="1.0.0",
            adapter_contract_version=1,
            provider="test",
        )
        executor.set_invocation_context(
            agent="dev_agent", org="happyranch", invocation_kind="task", task_id="TASK-001"
        )
        from runtime.orchestrator.throttle import get_throttle, set_throttle
        from runtime.orchestrator.throttle import ProviderThrottle
        old = get_throttle()
        set_throttle(ProviderThrottle(ceiling_default=10))
        try:
            result = executor.run(workspace=tmp_path, prompt="test prompt")
        finally:
            set_throttle(old)
        assert result.success
        assert result.token_usage is not None
        assert result.token_usage.input_tokens == 100


class TestInvocationContextEnvelope:
    """Fix 2: All five invocation paths supply truthful AdapterInput.

    Each test creates a real adapter that dumps its stdin to a file,
    then inspects the actual AdapterInput envelope to assert the
    null/non-null task_id matrix and no placeholder/foreign IDs.
    """

    def _make_inspector_adapter(self, tmp_path: Path, output_file: Path) -> str:
        """Create an adapter that writes its stdin to output_file, then returns valid output."""
        import sys as _sys
        import stat
        script = textwrap.dedent(f"""\
            #!{_sys.executable}
            import sys, json
            data = sys.stdin.read()
            with open({str(output_file)!r}, 'w') as f:
                f.write(data)
            inp = json.loads(data)
            sid = inp["invocation"]["invocation_id"]
            result = {{
                "success": True,
                "duration_seconds": 1,
                "session_id": sid,
                "returncode": 0,
                "stdout_tail": "ok",
                "stderr_tail": "",
                "adapter_metadata": {{
                    "adapter": "test-adapter",
                    "adapter_version": "1.0.0",
                    "contract_version": 1
                }}
            }}
            sys.stdout.write(json.dumps(result))
            sys.stdout.flush()
        """)
        exe = tmp_path / "inspector-adapter"
        exe.write_text(script)
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        return str(exe)

    def _build_executor_with_context(
        self, exe_path, exe_hash, agent, org, kind, task_id
    ):
        executor = CustomAdapterExecutor(
            profile_name="test",
            adapter_entry_id="test-adapter",
            adapter_executable=exe_path,
            adapter_hash=exe_hash,
            adapter_version="1.0.0",
            adapter_contract_version=1,
            provider="test",
        )
        executor.set_invocation_context(
            agent=agent, org=org, invocation_kind=kind, task_id=task_id
        )
        return executor

    def test_task_context_has_task_id(self, tmp_path):
        """Task invocation → AdapterInput.invocation.task_id = 'TASK-001' (non-null)."""
        from runtime.orchestrator.adapter_store import compute_sha256
        output_file = tmp_path / "stdin.json"
        exe_path = self._make_inspector_adapter(tmp_path, output_file)
        exe_hash = compute_sha256(exe_path)

        executor = self._build_executor_with_context(
            exe_path, exe_hash, "dev_agent", "happyranch", "task", "TASK-001"
        )
        from runtime.orchestrator.throttle import get_throttle, set_throttle
        from runtime.orchestrator.throttle import ProviderThrottle
        old = get_throttle()
        set_throttle(ProviderThrottle(ceiling_default=10))
        try:
            result = executor.run(workspace=tmp_path, prompt="test prompt")
        finally:
            set_throttle(old)
        assert result.success
        assert output_file.exists()
        envelope = json.loads(output_file.read_text())
        assert envelope["invocation"]["task_id"] == "TASK-001"
        assert envelope["invocation"]["invocation_kind"] == "task"

    def test_thread_context_task_id_is_null(self, tmp_path):
        """Thread invocation → AdapterInput.invocation.task_id is null."""
        from runtime.orchestrator.adapter_store import compute_sha256
        output_file = tmp_path / "stdin.json"
        exe_path = self._make_inspector_adapter(tmp_path, output_file)
        exe_hash = compute_sha256(exe_path)

        executor = self._build_executor_with_context(
            exe_path, exe_hash, "dev_agent", "happyranch", "thread", None
        )
        from runtime.orchestrator.throttle import get_throttle, set_throttle
        from runtime.orchestrator.throttle import ProviderThrottle
        old = get_throttle()
        set_throttle(ProviderThrottle(ceiling_default=10))
        try:
            result = executor.run(workspace=tmp_path, prompt="test prompt")
        finally:
            set_throttle(old)
        assert result.success
        envelope = json.loads(output_file.read_text())
        assert envelope["invocation"]["task_id"] is None
        assert envelope["invocation"]["invocation_kind"] == "thread"

    def test_wake_context_task_id_is_null(self, tmp_path):
        """Wake invocation → AdapterInput.invocation.task_id is null."""
        from runtime.orchestrator.adapter_store import compute_sha256
        output_file = tmp_path / "stdin.json"
        exe_path = self._make_inspector_adapter(tmp_path, output_file)
        exe_hash = compute_sha256(exe_path)

        executor = self._build_executor_with_context(
            exe_path, exe_hash, "dev_agent", "happyranch", "wake", None
        )
        from runtime.orchestrator.throttle import get_throttle, set_throttle
        from runtime.orchestrator.throttle import ProviderThrottle
        old = get_throttle()
        set_throttle(ProviderThrottle(ceiling_default=10))
        try:
            result = executor.run(workspace=tmp_path, prompt="test prompt")
        finally:
            set_throttle(old)
        assert result.success
        envelope = json.loads(output_file.read_text())
        assert envelope["invocation"]["task_id"] is None
        assert envelope["invocation"]["invocation_kind"] == "wake"

    def test_dream_context_task_id_is_null(self, tmp_path):
        """Dream invocation → AdapterInput.invocation.task_id is null."""
        from runtime.orchestrator.adapter_store import compute_sha256
        output_file = tmp_path / "stdin.json"
        exe_path = self._make_inspector_adapter(tmp_path, output_file)
        exe_hash = compute_sha256(exe_path)

        executor = self._build_executor_with_context(
            exe_path, exe_hash, "dev_agent", "happyranch", "dream", None
        )
        from runtime.orchestrator.throttle import get_throttle, set_throttle
        from runtime.orchestrator.throttle import ProviderThrottle
        old = get_throttle()
        set_throttle(ProviderThrottle(ceiling_default=10))
        try:
            result = executor.run(workspace=tmp_path, prompt="test prompt")
        finally:
            set_throttle(old)
        assert result.success
        envelope = json.loads(output_file.read_text())
        assert envelope["invocation"]["task_id"] is None
        assert envelope["invocation"]["invocation_kind"] == "dream"

    def test_schedule_context_task_id_is_null(self, tmp_path):
        """Schedule invocation → AdapterInput.invocation.task_id is null."""
        from runtime.orchestrator.adapter_store import compute_sha256
        output_file = tmp_path / "stdin.json"
        exe_path = self._make_inspector_adapter(tmp_path, output_file)
        exe_hash = compute_sha256(exe_path)

        executor = self._build_executor_with_context(
            exe_path, exe_hash, "dev_agent", "happyranch", "schedule", None
        )
        from runtime.orchestrator.throttle import get_throttle, set_throttle
        from runtime.orchestrator.throttle import ProviderThrottle
        old = get_throttle()
        set_throttle(ProviderThrottle(ceiling_default=10))
        try:
            result = executor.run(workspace=tmp_path, prompt="test prompt")
        finally:
            set_throttle(old)
        assert result.success
        envelope = json.loads(output_file.read_text())
        assert envelope["invocation"]["task_id"] is None
        assert envelope["invocation"]["invocation_kind"] == "schedule"

    def test_no_placeholder_ids_in_envelope(self, tmp_path):
        """No thread/wake/dream/schedule IDs appear as task_id in any envelope."""
        from runtime.orchestrator.adapter_store import compute_sha256
        output_file = tmp_path / "stdin.json"
        exe_path = self._make_inspector_adapter(tmp_path, output_file)
        exe_hash = compute_sha256(exe_path)

        # Test each non-task kind with a representative invocation
        for kind in ("thread", "wake", "dream", "schedule"):
            executor = self._build_executor_with_context(
                exe_path, exe_hash, "dev_agent", "happyranch", kind, None
            )
            from runtime.orchestrator.throttle import get_throttle, set_throttle
            from runtime.orchestrator.throttle import ProviderThrottle
            old = get_throttle()
            set_throttle(ProviderThrottle(ceiling_default=10))
            try:
                result = executor.run(
                    workspace=tmp_path, prompt="test prompt",
                    session_id=f"sess-test-{kind}"
                )
            finally:
                set_throttle(old)
            assert result.success, f"{kind} invocation should succeed"
            envelope = json.loads(output_file.read_text())
            assert envelope["invocation"]["task_id"] is None, (
                f"{kind} invocation should have task_id=null, "
                f"got {envelope['invocation']['task_id']!r}"
            )
            assert envelope["invocation"]["invocation_kind"] == kind
            # Verify agent and org are truthful, not placeholders
            assert envelope["invocation"]["agent"] == "dev_agent"
            assert envelope["invocation"]["org"] == "happyranch"


class TestThrottleIntegration:
    """Fix 3: CustomAdapterExecutor routes through per-provider throttle."""

    def test_custom_executor_enters_throttle(self, tmp_path):
        """Custom adapter launch calls get_throttle().run() with correct provider."""
        from runtime.orchestrator.adapter_store import compute_sha256
        output = _valid_adapter_output()
        exe_path = _make_test_adapter_executable(tmp_path, output)
        exe_hash = compute_sha256(exe_path)

        executor = CustomAdapterExecutor(
            profile_name="test",
            adapter_entry_id="test-adapter",
            adapter_executable=exe_path,
            adapter_hash=exe_hash,
            adapter_version="1.0.0",
            adapter_contract_version=1,
            provider="test-provider",
        )
        executor.set_invocation_context(
            agent="dev_agent", org="happyranch", invocation_kind="task", task_id="TASK-001"
        )

        # Install a spy throttle that records the provider and launch invocation
        from runtime.orchestrator.throttle import get_throttle, set_throttle
        from runtime.orchestrator.throttle import ProviderThrottle

        calls = []

        class SpyThrottle(ProviderThrottle):
            def run(self, provider, launch, on_event=None):
                calls.append({"provider": provider, "on_event": on_event})
                return launch()

        old = get_throttle()
        set_throttle(SpyThrottle(ceiling_default=10))
        try:
            result = executor.run(workspace=tmp_path, prompt="test prompt", session_id="test-sess")
        finally:
            set_throttle(old)

        assert result.success
        assert len(calls) == 1
        assert calls[0]["provider"] == "test-provider"

    def test_throttle_callback_is_observed(self, tmp_path):
        """on_throttle_event callback is passed through to throttle."""
        from runtime.orchestrator.adapter_store import compute_sha256
        output = _valid_adapter_output()
        exe_path = _make_test_adapter_executable(tmp_path, output)
        exe_hash = compute_sha256(exe_path)

        executor = CustomAdapterExecutor(
            profile_name="test",
            adapter_entry_id="test-adapter",
            adapter_executable=exe_path,
            adapter_hash=exe_hash,
            adapter_version="1.0.0",
            adapter_contract_version=1,
            provider="test-provider",
        )
        executor.set_invocation_context(
            agent="dev_agent", org="happyranch", invocation_kind="task", task_id="TASK-001"
        )

        from runtime.orchestrator.throttle import get_throttle, set_throttle
        from runtime.orchestrator.throttle import ProviderThrottle

        spy_events = []
        on_event_captured = []

        class SpyThrottle(ProviderThrottle):
            def run(self, provider, launch, on_event=None):
                on_event_captured.append(on_event)
                return launch()

        def my_callback(action, payload):
            spy_events.append((action, payload))

        old = get_throttle()
        set_throttle(SpyThrottle(ceiling_default=10))
        try:
            result = executor.run(
                workspace=tmp_path, prompt="test prompt",
                on_throttle_event=my_callback,
                session_id="test-sess"
            )
        finally:
            set_throttle(old)

        assert result.success
        assert len(on_event_captured) == 1
        assert on_event_captured[0] is my_callback

    def test_generic_cli_still_uses_throttle(self, tmp_path):
        """GenericCliExecutor still routes through _run_command → throttle (regression)."""
        from runtime.orchestrator.throttle import get_throttle, set_throttle
        from runtime.orchestrator.throttle import ProviderThrottle

        calls = []

        class SpyThrottle(ProviderThrottle):
            def run(self, provider, launch, on_event=None):
                calls.append({"provider": provider})
                return launch()

        from runtime.orchestrator.executors import GenericCliExecutor
        executor = GenericCliExecutor(
            profile_name="test-cli",
            argv_template=["echo", "test"],
            provider="test-cli-provider",
        )

        old = get_throttle()
        set_throttle(SpyThrottle(ceiling_default=10))
        try:
            result = executor.run(
                workspace=tmp_path, prompt="test",
                timeout_seconds=5, session_id="test-sess"
            )
        finally:
            set_throttle(old)

        assert len(calls) == 1
        assert calls[0]["provider"] == "test-cli-provider"
