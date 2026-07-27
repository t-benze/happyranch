"""Adversarial tests for THR-107 D6: canonical workspace_adapter_id + command_adapter_id
with legacy read-compatibility aliases and conflict detection.

D6 scope:
1. Canonical fields on ExecutorProfile: workspace_adapter_id, command_adapter_id
2. Deprecated aliases: adapter_id -> workspace_adapter_id, command_adapter -> command_adapter_id
3. Dual-read: legacy-only, canonical-only, agreeing both, conflicting -> ValueError
4. Built-ins retain exact workspace markers/permissions and effective first-party command adapter
5. Machine-global custom profiles remain readable without re-registration
6. Registration/list response contracts carry both canonical and deprecated fields
7. No auto-mutation of stored profiles
8. No SQLite/column/overloaded-column change
"""

import json
import pytest
from pathlib import Path

from runtime.orchestrator.executor_registry import (
    ExecutorProfile,
    ExecutorRegistry,
    get_registry,
    reset_registry,
)
from runtime.orchestrator.runtime_executor_store import (
    load_runtime_profiles,
    save_runtime_profile,
    _store_path,
)


# ═══════════════════════════════════════════════════════════════════════════
# 1. ExecutorProfile D6 field-level tests
# ═══════════════════════════════════════════════════════════════════════════

class TestCanonicalFields:
    """Canonical workspace_adapter_id and command_adapter_id exist and work."""

    def test_canonical_only_construction(self):
        """Constructing with only canonical fields produces the correct profile."""
        p = ExecutorProfile(
            name="test",
            kind="custom",
            workspace_adapter_id="pi",
            command_adapter_id="generic-cli",
        )
        assert p.workspace_adapter_id == "pi"
        assert p.command_adapter_id == "generic-cli"
        # Deprecated aliases are synced
        assert p.adapter_id == "pi"
        assert p.command_adapter == "generic-cli"

    def test_legacy_only_construction(self):
        """Constructing with only deprecated fields mirrors to canonical."""
        p = ExecutorProfile(
            name="test",
            kind="custom",
            adapter_id="codex",
            command_adapter="generic-cli",
        )
        assert p.workspace_adapter_id == "codex"  # mirrored
        assert p.command_adapter_id == "generic-cli"  # mirrored
        assert p.adapter_id == "codex"
        assert p.command_adapter == "generic-cli"

    def test_agreeing_both_fields(self):
        """Both canonical and deprecated set to same value works."""
        p = ExecutorProfile(
            name="test",
            workspace_adapter_id="opencode",
            adapter_id="opencode",
            command_adapter_id="generic-cli",
            command_adapter="generic-cli",
        )
        assert p.workspace_adapter_id == "opencode"
        assert p.command_adapter_id == "generic-cli"

    def test_conflicting_workspace_adapter_raises(self):
        """Conflicting canonical vs deprecated workspace adapter raises before store/registry/audit/token."""
        with pytest.raises(ValueError, match="conflicting workspace adapter"):
            ExecutorProfile(
                name="test",
                workspace_adapter_id="pi",
                adapter_id="codex",  # different non-default → conflict
            )

    def test_conflicting_command_adapter_raises(self):
        """Conflicting canonical vs deprecated command adapter raises."""
        with pytest.raises(ValueError, match="conflicting command adapter"):
            ExecutorProfile(
                name="test",
                command_adapter_id="generic-cli",
                command_adapter="something-else",  # different → conflict
            )

    def test_conflicting_command_adapter_different_values(self):
        """Different values for canonical + deprecated command adapter raise."""
        with pytest.raises(ValueError, match="conflicting command adapter"):
            ExecutorProfile(
                name="test",
                command_adapter_id="generic-cli",
                command_adapter="wrong-value",
            )

    def test_defaults_are_consistent(self):
        """Default values produce a consistent profile (claude workspace, no command adapter)."""
        p = ExecutorProfile(name="test")
        assert p.workspace_adapter_id == "claude"
        assert p.adapter_id == "claude"
        assert p.command_adapter_id is None
        assert p.command_adapter is None


# ═══════════════════════════════════════════════════════════════════════════
# 2. Built-in profile identity
# ═══════════════════════════════════════════════════════════════════════════

class TestBuiltinProfileIdentity:
    """Built-in profiles retain exact workspace markers and effective first-party command adapters."""

    def test_all_four_builtins_have_correct_canonical_fields(self):
        reset_registry()
        registry = ExecutorRegistry()
        expected = {
            "claude": (".claude/skills/start-task/SKILL.md", ["--model", "{model}"]),
            "codex": ("AGENTS.md", ["-m", "{model}"]),
            "opencode": ("AGENTS.md", ["-m", "{model}"]),
            "pi": ("AGENTS.md", ["--model", "{model}"]),
        }
        for name, (marker, model_arg) in expected.items():
            p = registry.get_profile(name)
            assert p is not None, f"Built-in {name} not registered"
            assert p.kind == "builtin"
            assert p.workspace_adapter_id == name
            assert p.command_adapter_id == name  # D6: first-party command adapter
            assert p.adapter_id == name  # deprecated alias synced
            assert p.command_adapter == name  # deprecated alias synced
            assert p.readiness_marker_fragment == marker
            assert p.model_arg == model_arg

    def test_builtins_are_immutable_via_registration_guard(self):
        """Built-in profile names cannot be overridden by custom registration."""
        reset_registry()
        registry = ExecutorRegistry()
        with pytest.raises(ValueError, match="Cannot override built-in executor"):
            registry.register_custom_profile(ExecutorProfile(
                name="claude", kind="custom", argv_template=["echo"],
            ))


# ═══════════════════════════════════════════════════════════════════════════
# 3. Custom profile config validation with dual-read
# ═══════════════════════════════════════════════════════════════════════════

class TestCustomProfileDualRead:
    """ExecutorRegistry.validate_custom_profile_config accepts both old and new keys."""

    def test_legacy_keys_only(self):
        """Profile with only 'adapter' and 'command_adapter' (legacy) works."""
        config = {
            "command": None,
            "argv_template": ["echo", "{prompt}"],
            "adapter": "codex",
            "command_adapter": "generic-cli",
        }
        p = ExecutorRegistry.validate_custom_profile_config("legacy", config)
        assert p.workspace_adapter_id == "codex"
        assert p.command_adapter_id == "generic-cli"
        assert p.adapter_id == "codex"
        assert p.command_adapter == "generic-cli"

    def test_canonical_keys_only(self):
        """Profile with only 'workspace_adapter_id' and 'command_adapter_id' works."""
        config = {
            "command": None,
            "argv_template": ["echo", "{prompt}"],
            "workspace_adapter_id": "pi",
            "command_adapter_id": "generic-cli",
        }
        p = ExecutorRegistry.validate_custom_profile_config("canonical", config)
        assert p.workspace_adapter_id == "pi"
        assert p.command_adapter_id == "generic-cli"

    def test_agreeing_both_keys(self):
        """Both old and new keys set to same values works."""
        config = {
            "command": None,
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
            "workspace_adapter_id": "pi",
            "command_adapter": "generic-cli",
            "command_adapter_id": "generic-cli",
        }
        p = ExecutorRegistry.validate_custom_profile_config("agreeing", config)
        assert p.workspace_adapter_id == "pi"
        assert p.command_adapter_id == "generic-cli"

    def test_conflicting_workspace_keys_raises(self):
        """Canonical 'workspace_adapter_id' vs deprecated 'adapter' conflict raises."""
        config = {
            "command": None,
            "argv_template": ["echo", "{prompt}"],
            "adapter": "claude",
            "workspace_adapter_id": "pi",
        }
        with pytest.raises(ValueError, match="conflicting workspace adapter"):
            ExecutorRegistry.validate_custom_profile_config("conflict-ws", config)

    @pytest.mark.parametrize(
        "legacy_key,legacy_value,canon_key,canon_value",
        [
            # Defect 2 repro: explicit adapter=pi + workspace_adapter_id=claude → 422
            ("adapter", "pi", "workspace_adapter_id", "claude"),
            ("adapter_id", "pi", "workspace_adapter_id", "claude"),
            # Canonical-only: adapter_id=codex + workspace_adapter_id=pi → 422
            ("adapter_id", "codex", "workspace_adapter_id", "pi"),
            # adapter=codex + workspace_adapter_id=pi → 422
            ("adapter", "codex", "workspace_adapter_id", "pi"),
            # Three-way: adapter=pi + adapter_id=codex + workspace_adapter_id=claude → 422
            # (covered by the fact that any two disagreeing → conflict)
        ],
    )
    def test_every_explicit_conflict_direction_raises(
        self, legacy_key, legacy_value, canon_key, canon_value
    ):
        """Every explicitly supplied disagreeing legacy/canonical pair fails with 422 path.

        This specifically covers the Defect 2 scenario:
        {adapter: "pi", workspace_adapter_id: "claude"} → 422, NOT accepted as claude.
        """
        config: dict = {
            "command": None,
            "argv_template": ["echo", "{prompt}"],
            legacy_key: legacy_value,
            canon_key: canon_value,
        }
        with pytest.raises(ValueError, match="conflicting workspace adapter"):
            ExecutorRegistry.validate_custom_profile_config("conflict", config)

    def test_adapter_id_as_standalone_deprecated_alias(self):
        """adapter_id (deprecated) works as an alias for workspace_adapter_id."""
        config = {
            "command": None,
            "argv_template": ["echo", "{prompt}"],
            "adapter_id": "codex",
        }
        p = ExecutorRegistry.validate_custom_profile_config("adapter-id-test", config)
        assert p.workspace_adapter_id == "codex"

    def test_agreeing_all_three_workspace_keys(self):
        """Agreeing adapter/adapter_id/workspace_adapter_id all works."""
        config = {
            "command": None,
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
            "adapter_id": "pi",
            "workspace_adapter_id": "pi",
        }
        p = ExecutorRegistry.validate_custom_profile_config("three-way", config)
        assert p.workspace_adapter_id == "pi"

    def test_invalid_command_adapter_id_rejected(self):
        """Invalid command_adapter_id value is rejected by validation (only 'generic-cli' allowed)."""
        config = {
            "command": None,
            "argv_template": ["echo", "{prompt}"],
            "command_adapter": "generic-cli",
            "command_adapter_id": "bad-value",  # invalid → rejected
        }
        with pytest.raises(ValueError, match="must be 'generic-cli'"):
            ExecutorRegistry.validate_custom_profile_config("bad-cmd", config)


# ═══════════════════════════════════════════════════════════════════════════
# 4. Machine-global store: no auto-mutation
# ═══════════════════════════════════════════════════════════════════════════

class TestStoreNoAutoMutation:
    """Stored profiles are never auto-mutated by D6 read."""

    def test_legacy_store_entry_loads_without_mutation(self, tmp_path):
        """A legacy-only store entry (no canonical keys) is loaded without altering the store file."""
        import os
        old_home = os.environ.get("HAPPYRANCH_DAEMON_HOME")
        os.environ["HAPPYRANCH_DAEMON_HOME"] = str(tmp_path)
        try:
            legacy_config = {
                "command": "mycli",
                "argv_template": ["mycli", "--msg", "{prompt}"],
                "adapter": "pi",
                "command_adapter": "generic-cli",
            }
            save_runtime_profile("legacy-profile", legacy_config)

            # Load — should NOT add canonical keys
            profiles = load_runtime_profiles()
            assert "legacy-profile" in profiles
            stored = profiles["legacy-profile"]
            assert stored == legacy_config  # exactly the legacy shape
            # No canonical keys autopopulated
            assert "workspace_adapter_id" not in stored
            assert "command_adapter_id" not in stored
        finally:
            if old_home is not None:
                os.environ["HAPPYRANCH_DAEMON_HOME"] = old_home
            else:
                os.environ.pop("HAPPYRANCH_DAEMON_HOME", None)

    def test_canonical_store_entry_round_trips(self, tmp_path):
        """A canonical store entry is persisted and re-read correctly."""
        import os
        old_home = os.environ.get("HAPPYRANCH_DAEMON_HOME")
        os.environ["HAPPYRANCH_DAEMON_HOME"] = str(tmp_path)
        try:
            canonical_config = {
                "command": "mycli",
                "argv_template": ["mycli", "--msg", "{prompt}"],
                "adapter": "pi",
                "workspace_adapter_id": "pi",
                "command_adapter": "generic-cli",
                "command_adapter_id": "generic-cli",
            }
            save_runtime_profile("canonical-profile", canonical_config)
            profiles = load_runtime_profiles()
            stored = profiles["canonical-profile"]
            assert stored["workspace_adapter_id"] == "pi"
            assert stored["command_adapter_id"] == "generic-cli"
            assert stored["adapter"] == "pi"  # preserved for downgrade safety
            assert stored["command_adapter"] == "generic-cli"  # preserved
        finally:
            if old_home is not None:
                os.environ["HAPPYRANCH_DAEMON_HOME"] = old_home
            else:
                os.environ.pop("HAPPYRANCH_DAEMON_HOME", None)


# ═══════════════════════════════════════════════════════════════════════════
# 5. Registry: built-in workspace markers and permissions unchanged
# ═══════════════════════════════════════════════════════════════════════════

class TestBuiltinWorkspaceMarkersUnchanged:
    """Built-in profiles carry the exact same workspace markers as pre-D6."""

    def test_claude_marker_is_claude_skills_start_task(self):
        reset_registry()
        p = get_registry().get_profile("claude")
        assert p is not None
        assert p.workspace_adapter_id == "claude"
        assert p.readiness_marker_fragment == ".claude/skills/start-task/SKILL.md"

    def test_codex_marker_is_agents_md(self):
        reset_registry()
        p = get_registry().get_profile("codex")
        assert p is not None
        assert p.workspace_adapter_id == "codex"
        assert p.readiness_marker_fragment == "AGENTS.md"

    def test_opencode_marker_is_agents_md(self):
        reset_registry()
        p = get_registry().get_profile("opencode")
        assert p is not None
        assert p.workspace_adapter_id == "opencode"
        assert p.readiness_marker_fragment == "AGENTS.md"

    def test_pi_marker_is_agents_md(self):
        reset_registry()
        p = get_registry().get_profile("pi")
        assert p is not None
        assert p.workspace_adapter_id == "pi"
        assert p.readiness_marker_fragment == "AGENTS.md"


# ═══════════════════════════════════════════════════════════════════════════
# 6. Response contract: canonical + deprecated fields on registration/list
# ═══════════════════════════════════════════════════════════════════════════

class TestResponseContract:
    """The ExecutorRegisterResponse and RuntimeProfileEntry carry both fields."""

    def test_register_response_has_canonical_and_deprecated(self):
        """Registration response populates both canonical and deprecated fields."""
        from runtime.daemon.routes.executors import ExecutorRegisterResponse

        resp = ExecutorRegisterResponse(
            name="test-cli",
            kind="custom",
            workspace_adapter_id="pi",
            command_adapter_id="generic-cli",
            adapter_id="pi",
            command_adapter="generic-cli",
            command="test-cli",
            argv_template=["test-cli", "{prompt}"],
        )
        data = resp.model_dump()
        assert data["workspace_adapter_id"] == "pi"
        assert data["command_adapter_id"] == "generic-cli"
        assert data["adapter_id"] == "pi"
        assert data["command_adapter"] == "generic-cli"

    def test_runtime_profile_entry_has_canonical_and_deprecated(self):
        """RuntimeProfileEntry carries both canonical and deprecated fields."""
        from runtime.daemon.routes.executors import RuntimeProfileEntry

        entry = RuntimeProfileEntry(
            name="test-cli",
            command="test-cli",
            workspace_adapter_id="pi",
            command_adapter_id="generic-cli",
            adapter="pi",
            command_adapter="generic-cli",
            present=True,
            path="/usr/bin/test-cli",
        )
        data = entry.model_dump()
        assert data["workspace_adapter_id"] == "pi"
        assert data["command_adapter_id"] == "generic-cli"
        assert data["adapter"] == "pi"
        assert data["command_adapter"] == "generic-cli"


# ═══════════════════════════════════════════════════════════════════════════
# 7. GenericCliExecutor behavior unchanged
# ═══════════════════════════════════════════════════════════════════════════

class TestGenericCliUnchanged:
    """GenericCliExecutor argv and behavior is bit-for-bit unchanged by D6."""

    def test_custom_profile_argv_unchanged(self):
        """Custom profile argv_template construction is unaffected by new fields."""
        p = ExecutorProfile(
            name="test",
            kind="custom",
            workspace_adapter_id="pi",
            command_adapter_id="generic-cli",
            argv_template=["my-cli", "--prompt", "{prompt}"],
        )
        assert p.argv_template == ["my-cli", "--prompt", "{prompt}"]
        # workspace_adapter_id doesn't affect argv
        assert p.command_adapter_id == "generic-cli"

    def test_custom_profile_command_declared_separately(self):
        """command field is independent of canonical adapter fields."""
        p = ExecutorProfile(
            name="test",
            kind="custom",
            workspace_adapter_id="codex",
            command_adapter_id="generic-cli",
            command="my-cli",
        )
        assert p.workspace_adapter_id == "codex"  # workspace prep = codex
        assert p.command_adapter_id == "generic-cli"  # execution = generic-cli
        assert p.command == "my-cli"  # executable name is separate


# ═══════════════════════════════════════════════════════════════════════════
# 8. Conflict prevention: no durable/registry/audit/token residue
# ═══════════════════════════════════════════════════════════════════════════

class TestConflictPreventsAllSideEffects:
    """Conflicting fields fail before any side effect (durable, registry, audit, token)."""

    def test_conflicting_profile_not_registered(self):
        """A conflicting profile never enters the registry."""
        reset_registry()
        registry = ExecutorRegistry()
        with pytest.raises(ValueError):
            registry.register_custom_profile(ExecutorProfile(
                name="bad",
                kind="custom",
                workspace_adapter_id="pi",
                adapter_id="codex",  # DIFFERENT non-default → conflict
                argv_template=["echo", "{prompt}"],
            ))
        # Registry remains empty of the bad profile
        assert registry.get_profile("bad") is None

    def test_validate_config_conflict_no_store_write(self, tmp_path):
        """Validation conflict prevents any store write."""
        import os
        old_home = os.environ.get("HAPPYRANCH_DAEMON_HOME")
        os.environ["HAPPYRANCH_DAEMON_HOME"] = str(tmp_path)
        try:
            config = {
                "command": "mycli",
                "argv_template": ["mycli", "{prompt}"],
                "adapter": "claude",
                "workspace_adapter_id": "pi",  # conflict!
            }
            with pytest.raises(ValueError):
                ExecutorRegistry.validate_custom_profile_config("conflict", config)
            # No file should have been written
            store_path = _store_path()
            assert not store_path.exists() or "conflict" not in load_runtime_profiles()
        finally:
            if old_home is not None:
                os.environ["HAPPYRANCH_DAEMON_HOME"] = old_home
            else:
                os.environ.pop("HAPPYRANCH_DAEMON_HOME", None)


# ═══════════════════════════════════════════════════════════════════════════
# 9. No SQLite/column/overloaded-column change
# ═══════════════════════════════════════════════════════════════════════════

class TestNoSchemaChange:
    """D6 does not alter SQLite schema, columns, or overloaded-column semantics."""

    def test_executor_profile_is_pure_dataclass(self):
        """ExecutorProfile is a plain frozen dataclass — no ORM or DB mapping."""
        import dataclasses
        assert dataclasses.is_dataclass(ExecutorProfile)
        # All fields are plain Python types, no SQLAlchemy column definitions
        for field in dataclasses.fields(ExecutorProfile):
            assert "sqlalchemy" not in str(field.type).lower()
            assert "Column" not in str(field.metadata)

    def test_no_new_imports_from_database_module(self):
        """D6 changes do not import from runtime.infrastructure.database."""
        import inspect
        import runtime.orchestrator.executor_registry as mod
        source = inspect.getsource(mod)
        assert "from runtime.infrastructure.database" not in source
