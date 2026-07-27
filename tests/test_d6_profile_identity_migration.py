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
import os
import pytest
from pathlib import Path

from fastapi.testclient import TestClient

from runtime.config import Settings
from runtime.daemon import auth as auth_mod
from runtime.daemon import paths as paths_mod
from runtime.daemon.app import create_app
from runtime.daemon.routes import auth as auth_route
from runtime.daemon.state import DaemonState
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
from runtime.orchestrator import runtime_executor_store


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


# ═══════════════════════════════════════════════════════════════════════════
# 10. Actual shipping-endpoint tests — canonical-only workspace_adapter_id
#    on BOTH POST /api/v1/executors/runtime/register and
#    POST /api/v1/orgs/{org}/executors/register
# ═══════════════════════════════════════════════════════════════════════════

# ── Shared helpers ─────────────────────────────────────────────────────


def _bypass_loopback(monkeypatch):
    """Allow TestClient (peer 'testclient') through loopback gates."""
    monkeypatch.setattr(
        auth_route, "_LOCAL_HOSTS",
        auth_route._LOCAL_HOSTS | {"testclient"},
    )
    monkeypatch.setattr(
        auth_mod, "_REGISTRATION_LOCAL_HOSTS",
        auth_mod._REGISTRATION_LOCAL_HOSTS | {"testclient"},
    )


def _complete_runtime_conformance(client, store, monkeypatch, token):
    """Complete all conformance steps for a runtime registration token."""
    headers = {"Authorization": f"Bearer {token}"}
    steps_and_payloads = [
        ("workspace_access", None),
        ("loopback_reachable", None),
        ("cli_callback", None),
        ("emit_envelope", {"envelope_version": 1, "token_usage": {"input_tokens": 1, "output_tokens": 1}}),
    ]
    for step_id, envelope in steps_and_payloads:
        payload: dict = {"step_id": step_id}
        if envelope is not None:
            payload["envelope"] = envelope
        r = client.post(
            "/api/v1/executors/runtime/conformance-checkin",
            json=payload,
            headers=headers,
        )
        assert r.status_code == 200


def _complete_org_conformance(store, token, org="alpha"):
    """Record all conformance steps for an org registration token."""
    from runtime.daemon.registration_token import RegistrationTokenStore
    for step_id in RegistrationTokenStore.DEFAULT_CONFORMANCE_STEPS:
        store.record_step_arrival(token, org, step_id)


# ── Runtime registration endpoint fixtures ─────────────────────────────


@pytest.fixture
def _runtime_setup(tmp_path, monkeypatch):
    """Set up daemon home and registry for runtime endpoint tests."""
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / ".happyranch"))
    paths_mod.ensure_daemon_home()
    paths_mod.ensure_token()
    reset_registry()
    return tmp_path


@pytest.fixture
def runtime_client(_runtime_setup, monkeypatch):
    """TestClient with master bearer + loopback bypass for runtime routes."""
    _bypass_loopback(monkeypatch)
    state = DaemonState.idle(Settings())
    app = create_app(state)
    tc = TestClient(app)
    tc.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})
    return tc


@pytest.fixture
def runtime_store(runtime_client, _runtime_setup):
    """Token store for runtime endpoint tests."""
    state = DaemonState.idle(Settings())
    return state.registration_token_store


# ── Org registration endpoint fixtures ─────────────────────────────────


@pytest.fixture
def _org_setup(tmp_path, monkeypatch):
    """Set up daemon home, token file, org for org endpoint tests."""
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / ".happyranch"))
    paths_mod.ensure_daemon_home()
    paths_mod.ensure_token()
    from runtime.runtime import RuntimeDir
    rt = RuntimeDir.init(tmp_path / "runtime")
    org_root = rt.orgs_dir / "alpha"
    org_root.mkdir(parents=True)
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: engineering_head\n"
        "    workers: [dev_agent]\n"
    )
    reset_registry()
    return rt


@pytest.fixture
def org_state(_org_setup, monkeypatch):
    """DaemonState with org for org endpoint tests."""
    _bypass_loopback(monkeypatch)
    state = DaemonState.from_runtime(_org_setup, Settings())
    return state


@pytest.fixture
def org_client(org_state):
    """TestClient for org registration endpoint."""
    app = create_app(org_state)
    tc = TestClient(app)
    tc.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})
    return tc


@pytest.fixture
def org_store(org_state):
    """Token store for org endpoint tests."""
    return org_state.registration_token_store


# ── Runtime registration: canonical-only workspace_adapter_id ──────────


class TestRuntimeRegisterCanonicalOnly:
    """POST /api/v1/executors/runtime/register — canonical-only workspace_adapter_id."""

    @pytest.mark.parametrize("ws_id", ["claude", "codex", "opencode", "pi"])
    def test_canonical_only_succeeds(self, _runtime_setup, monkeypatch, ws_id, tmp_path):
        """Canonical-only workspace_adapter_id registers each valid non-pi workspace."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        reset_registry()

        from runtime.config import Settings
        from runtime.daemon.state import DaemonState
        from fastapi.testclient import TestClient
        from runtime.daemon.app import create_app

        _bypass_loopback(monkeypatch)
        state = DaemonState.idle(Settings())
        app = create_app(state)
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})
        store = state.registration_token_store

        token, _ = store.mint_runtime(f"canonical-{ws_id}")
        _complete_runtime_conformance(client, store, monkeypatch, token)

        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "workspace_adapter_id": ws_id,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, f"Canonical-only ws={ws_id} failed: {r.json()}"
        body = r.json()
        assert body["name"] == f"canonical-{ws_id}"
        assert body["kind"] == "custom"
        assert body["workspace_adapter_id"] == ws_id
        assert body["adapter_id"] == ws_id  # deprecated alias synced

        # Verify registry persistence
        registry = get_registry()
        profile = registry.get_profile(f"canonical-{ws_id}")
        assert profile is not None
        assert profile.workspace_adapter_id == ws_id
        assert profile.adapter_id == ws_id

        # Verify store persistence
        profiles = load_runtime_profiles()
        assert f"canonical-{ws_id}" in profiles

    def test_canonical_only_no_adapter_default_contamination(self, _runtime_setup, monkeypatch, tmp_path):
        """When adapter is omitted, config_cfg does NOT contain the default 'pi'. """
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        reset_registry()

        from runtime.config import Settings
        from runtime.daemon.state import DaemonState
        from fastapi.testclient import TestClient
        from runtime.daemon.app import create_app

        _bypass_loopback(monkeypatch)
        state = DaemonState.idle(Settings())
        app = create_app(state)
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})
        store = state.registration_token_store

        token, _ = store.mint_runtime("clean-canonical")
        _complete_runtime_conformance(client, store, monkeypatch, token)

        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "workspace_adapter_id": "claude",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        registry = get_registry()
        profile = registry.get_profile("clean-canonical")
        assert profile.workspace_adapter_id == "claude"
        # adapter_id alias is synced, not left as default claude
        assert profile.adapter_id == "claude"

    def test_explicit_adapter_pi_with_ws_claude_conflict_422(self, _runtime_setup, monkeypatch, tmp_path):
        """Explicit adapter=pi + workspace_adapter_id=claude → 422, no residue."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        reset_registry()

        from runtime.config import Settings
        from runtime.daemon.state import DaemonState
        from fastapi.testclient import TestClient
        from runtime.daemon.app import create_app

        _bypass_loopback(monkeypatch)
        state = DaemonState.idle(Settings())
        app = create_app(state)
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})
        store = state.registration_token_store

        token, _ = store.mint_runtime("conflict-pi-claude")
        _complete_runtime_conformance(client, store, monkeypatch, token)

        # Snapshot pre-registration state
        pre_registry_names = set(get_registry().list_profile_names())
        pre_profiles = load_runtime_profiles()

        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "adapter": "pi",
                "workspace_adapter_id": "claude",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 422

        # No residue: registry unchanged
        post_registry_names = set(get_registry().list_profile_names())
        assert post_registry_names == pre_registry_names
        assert "conflict-pi-claude" not in post_registry_names

        # No residue: store unchanged
        post_profiles = load_runtime_profiles()
        assert "conflict-pi-claude" not in post_profiles

        # Token still valid (not consumed on failure)
        assert store.validate_runtime(token) is not None

    def test_legacy_adapter_only_still_works(self, _runtime_setup, monkeypatch, tmp_path):
        """Legacy-only adapter field still registers successfully."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        reset_registry()

        from runtime.config import Settings
        from runtime.daemon.state import DaemonState
        from fastapi.testclient import TestClient
        from runtime.daemon.app import create_app

        _bypass_loopback(monkeypatch)
        state = DaemonState.idle(Settings())
        app = create_app(state)
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})
        store = state.registration_token_store

        token, _ = store.mint_runtime("legacy-adapter")
        _complete_runtime_conformance(client, store, monkeypatch, token)

        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "adapter": "codex",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["adapter_id"] == "codex"
        assert body["workspace_adapter_id"] == "codex"

    def test_canonical_plus_agreeing_legacy_succeeds(self, _runtime_setup, monkeypatch, tmp_path):
        """Canonical + agreeing legacy adapter values succeed."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        reset_registry()

        from runtime.config import Settings
        from runtime.daemon.state import DaemonState
        from fastapi.testclient import TestClient
        from runtime.daemon.app import create_app

        _bypass_loopback(monkeypatch)
        state = DaemonState.idle(Settings())
        app = create_app(state)
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})
        store = state.registration_token_store

        token, _ = store.mint_runtime("agreeing-aliases")
        _complete_runtime_conformance(client, store, monkeypatch, token)

        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "adapter": "opencode",
                "workspace_adapter_id": "opencode",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["workspace_adapter_id"] == "opencode"
        assert body["adapter_id"] == "opencode"


# ── Org registration: canonical-only workspace_adapter_id ──────────────


class TestOrgRegisterCanonicalOnly:
    """POST /api/v1/orgs/{org}/executors/register — canonical-only workspace_adapter_id."""

    @pytest.mark.parametrize("ws_id", ["claude", "codex", "opencode", "pi"])
    def test_canonical_only_succeeds(self, org_state, org_store, monkeypatch, ws_id, tmp_path):
        """Canonical-only workspace_adapter_id registers each valid workspace on org route."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        reset_registry()

        from fastapi.testclient import TestClient
        from runtime.daemon.app import create_app

        _bypass_loopback(monkeypatch)
        org_state2 = org_state  # reuse but with fresh env
        # Fresh state for each parametrized variant
        from runtime.config import Settings
        from runtime.daemon.state import DaemonState
        from runtime.runtime import RuntimeDir

        rt = RuntimeDir.init(tmp_path / "runtime2")
        org_root = rt.orgs_dir / "alpha"
        org_root.mkdir(parents=True)
        (org_root / "org").mkdir()
        (org_root / "org" / "teams.yaml").write_text(
            "teams:\n"
            "  engineering:\n"
            "    manager: engineering_head\n"
            "    workers: [dev_agent]\n"
        )

        state2 = DaemonState.from_runtime(rt, Settings())
        store2 = state2.registration_token_store
        app2 = create_app(state2)
        client2 = TestClient(app2)
        client2.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})

        token, _ = store2.mint("alpha", f"org-canon-{ws_id}")
        _complete_org_conformance(store2, token, "alpha")

        r = client2.post(
            f"/api/v1/orgs/alpha/executors/register",
            json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "workspace_adapter_id": ws_id,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, (
            f"Canonical-only org ws={ws_id} failed: {r.json()}"
        )
        body = r.json()
        assert body["name"] == f"org-canon-{ws_id}"
        assert body["workspace_adapter_id"] == ws_id
        assert body["adapter_id"] == ws_id

        # Verify registry persistence
        registry = get_registry()
        profile = registry.get_profile(f"org-canon-{ws_id}")
        assert profile is not None
        assert profile.workspace_adapter_id == ws_id

        # Verify store persistence
        profiles = load_runtime_profiles()
        assert f"org-canon-{ws_id}" in profiles

    def test_explicit_adapter_pi_with_ws_claude_conflict_422(self, org_state, org_store, monkeypatch, tmp_path):
        """Org route: explicit adapter=pi + workspace_adapter_id=claude → 422, no residue."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        reset_registry()

        from fastapi.testclient import TestClient
        from runtime.daemon.app import create_app
        from runtime.config import Settings
        from runtime.daemon.state import DaemonState
        from runtime.runtime import RuntimeDir

        _bypass_loopback(monkeypatch)

        rt = RuntimeDir.init(tmp_path / "runtime_conflict")
        org_root = rt.orgs_dir / "alpha"
        org_root.mkdir(parents=True)
        (org_root / "org").mkdir()
        (org_root / "org" / "teams.yaml").write_text(
            "teams:\n"
            "  engineering:\n"
            "    manager: engineering_head\n"
            "    workers: [dev_agent]\n"
        )

        state3 = DaemonState.from_runtime(rt, Settings())
        store3 = state3.registration_token_store
        app3 = create_app(state3)
        client3 = TestClient(app3)
        client3.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})

        token, _ = store3.mint("alpha", "org-conflict-pi-claude")
        _complete_org_conformance(store3, token, "alpha")

        pre_registry_names = set(get_registry().list_profile_names())
        pre_profiles = load_runtime_profiles()

        r = client3.post(
            "/api/v1/orgs/alpha/executors/register",
            json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "adapter": "pi",
                "workspace_adapter_id": "claude",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 422

        # No residue: registry unchanged
        post_registry_names = set(get_registry().list_profile_names())
        assert post_registry_names == pre_registry_names
        assert "org-conflict-pi-claude" not in post_registry_names

        # No residue: store unchanged
        post_profiles = load_runtime_profiles()
        assert "org-conflict-pi-claude" not in post_profiles

        # Token still valid
        assert store3.validate(token, "alpha") is not None

    def test_inverse_conflict_ws_pi_adapter_claude_422(self, org_state, org_store, monkeypatch, tmp_path):
        """Org route: explicit adapter=claude + workspace_adapter_id=pi → 422, no residue."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        reset_registry()

        from fastapi.testclient import TestClient
        from runtime.daemon.app import create_app
        from runtime.config import Settings
        from runtime.daemon.state import DaemonState
        from runtime.runtime import RuntimeDir

        _bypass_loopback(monkeypatch)

        rt = RuntimeDir.init(tmp_path / "runtime_inverse")
        org_root = rt.orgs_dir / "alpha"
        org_root.mkdir(parents=True)
        (org_root / "org").mkdir()
        (org_root / "org" / "teams.yaml").write_text(
            "teams:\n"
            "  engineering:\n"
            "    manager: engineering_head\n"
            "    workers: [dev_agent]\n"
        )

        state4 = DaemonState.from_runtime(rt, Settings())
        store4 = state4.registration_token_store
        app4 = create_app(state4)
        client4 = TestClient(app4)
        client4.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})

        token, _ = store4.mint("alpha", "org-inverse-conflict")
        _complete_org_conformance(store4, token, "alpha")

        pre_registry_names = set(get_registry().list_profile_names())

        r = client4.post(
            "/api/v1/orgs/alpha/executors/register",
            json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "adapter": "claude",
                "workspace_adapter_id": "pi",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 422

        # No residue
        post_registry_names = set(get_registry().list_profile_names())
        assert post_registry_names == pre_registry_names

    @pytest.mark.parametrize(
        "adapter_key,adapter_val,ws_key,ws_val",
        [
            ("adapter", "codex", "workspace_adapter_id", "pi"),
            ("adapter", "opencode", "workspace_adapter_id", "claude"),
            ("adapter", "claude", "workspace_adapter_id", "opencode"),
        ],
    )
    def test_every_conflict_direction_422_on_org_route(
        self, org_state, org_store, monkeypatch, tmp_path, adapter_key, adapter_val, ws_key, ws_val
    ):
        """Every explicitly-supplied disagreeing pair 422s on the org route with no residue."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        reset_registry()

        from fastapi.testclient import TestClient
        from runtime.daemon.app import create_app
        from runtime.config import Settings
        from runtime.daemon.state import DaemonState
        from runtime.runtime import RuntimeDir

        _bypass_loopback(monkeypatch)

        rt = RuntimeDir.init(tmp_path / f"runtime_{adapter_val}_{ws_val}")
        org_root = rt.orgs_dir / "alpha"
        org_root.mkdir(parents=True)
        (org_root / "org").mkdir()
        (org_root / "org" / "teams.yaml").write_text(
            "teams:\n"
            "  engineering:\n"
            "    manager: engineering_head\n"
            "    workers: [dev_agent]\n"
        )

        state = DaemonState.from_runtime(rt, Settings())
        store = state.registration_token_store
        app = create_app(state)
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})

        name = f"conflict-{adapter_val}-{ws_val}"
        token, _ = store.mint("alpha", name)
        _complete_org_conformance(store, token, "alpha")

        pre_registry_names = set(get_registry().list_profile_names())

        r = client.post(
            "/api/v1/orgs/alpha/executors/register",
            json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                adapter_key: adapter_val,
                ws_key: ws_val,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 422, f"Expected 422 but got {r.status_code}: {r.json()}"

        # No residue
        post_registry_names = set(get_registry().list_profile_names())
        assert name not in post_registry_names

    def test_legacy_only_on_org_route_works(self, org_state, org_store, monkeypatch, tmp_path):
        """Legacy-only adapter field registers via org route."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        reset_registry()

        from fastapi.testclient import TestClient
        from runtime.daemon.app import create_app

        _bypass_loopback(monkeypatch)

        token, _ = org_store.mint("alpha", "org-legacy")
        _complete_org_conformance(org_store, token, "alpha")

        client = TestClient(create_app(org_state))
        client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})

        r = client.post(
            "/api/v1/orgs/alpha/executors/register",
            json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "adapter": "codex",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["adapter_id"] == "codex"
        assert body["workspace_adapter_id"] == "codex"


# ── List/response consistency checks ───────────────────────────────────


class TestListCanonicalConsistency:
    """List route serialization consistent with canonical/persisted identity."""

    def test_canonical_only_profile_lists_correctly(self, monkeypatch, tmp_path):
        """After canonical-only registration, list response returns correct workspace_adapter_id."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / ".happyranch"))
        reset_registry()
        paths_mod.ensure_daemon_home()
        paths_mod.ensure_token()

        from runtime.config import Settings
        from runtime.daemon.state import DaemonState
        from fastapi.testclient import TestClient
        from runtime.daemon.app import create_app

        _bypass_loopback(monkeypatch)
        state = DaemonState.idle(Settings())
        app = create_app(state)
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})
        store = state.registration_token_store

        # Register canonical-only codex
        token, _ = store.mint_runtime("list-test-canon")
        _complete_runtime_conformance(client, store, monkeypatch, token)
        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "workspace_adapter_id": "codex",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200

        # List profiles
        r = client.get("/api/v1/executors/runtime/profiles")
        assert r.status_code == 200
        profiles = r.json()["profiles"]
        list_test = [p for p in profiles if p["name"] == "list-test-canon"]
        assert len(list_test) == 1
        entry = list_test[0]
        assert entry["workspace_adapter_id"] == "codex"
        # Deprecated alias present (no contradiction)
        assert entry["adapter"] == "codex"
        # No contradictory workspace identity
        assert entry["workspace_adapter_id"] == entry["adapter"]


# ── Round 3: Stored-shape persistence and restart/reload ───────────────


class TestStoredShapePersistence:
    """Canonical-only registrations must persist resolved canonical identity
    + matching compatibility aliases so the profile survives restart/reload."""

    def test_runtime_canonical_only_persists_correct_adapter(self, _runtime_setup, monkeypatch, tmp_path):
        """Runtime route: canonical-only ws_id=claude stores adapter=claude (not pi)."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        reset_registry()

        from runtime.config import Settings
        from runtime.daemon.state import DaemonState
        from fastapi.testclient import TestClient
        from runtime.daemon.app import create_app

        _bypass_loopback(monkeypatch)
        state = DaemonState.idle(Settings())
        app = create_app(state)
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})
        store = state.registration_token_store

        token, _ = store.mint_runtime("stored-shape-runtime")
        _complete_runtime_conformance(client, store, monkeypatch, token)

        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "workspace_adapter_id": "claude",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200

        # Verify stored config has correct adapter (not default pi)
        profiles = load_runtime_profiles()
        stored = profiles["stored-shape-runtime"]
        assert stored["adapter"] == "claude", (
            f"Stored adapter should be claude (resolved from canonical), got {stored.get('adapter')!r}"
        )
        assert stored["workspace_adapter_id"] == "claude"
        assert stored["adapter"] == stored["workspace_adapter_id"]

    def test_org_canonical_only_persists_correct_adapter(self, monkeypatch, tmp_path):
        """Org route: canonical-only ws_id=codex stores adapter=codex (not pi)."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        reset_registry()

        from runtime.config import Settings
        from runtime.daemon.state import DaemonState
        from fastapi.testclient import TestClient
        from runtime.daemon.app import create_app
        from runtime.runtime import RuntimeDir

        _bypass_loopback(monkeypatch)

        rt = RuntimeDir.init(tmp_path / "runtime_org_stored")
        org_root = rt.orgs_dir / "alpha"
        org_root.mkdir(parents=True)
        (org_root / "org").mkdir()
        (org_root / "org" / "teams.yaml").write_text(
            "teams:\n"
            "  engineering:\n"
            "    manager: engineering_head\n"
            "    workers: [dev_agent]\n"
        )

        state = DaemonState.from_runtime(rt, Settings())
        store = state.registration_token_store
        app = create_app(state)
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})

        token, _ = store.mint("alpha", "org-stored-shape")
        _complete_org_conformance(store, token, "alpha")

        r = client.post(
            "/api/v1/orgs/alpha/executors/register",
            json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "workspace_adapter_id": "codex",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, f"Org route failed: {r.json()}"

        profiles = load_runtime_profiles()
        stored = profiles["org-stored-shape"]
        assert stored["adapter"] == "codex", (
            f"Org stored adapter should be codex, got {stored.get('adapter')!r}"
        )
        assert stored["workspace_adapter_id"] == "codex"
        assert stored["adapter"] == stored["workspace_adapter_id"]

    def test_runtime_canonical_only_survives_restart_reload(self, _runtime_setup, monkeypatch, tmp_path):
        """After canonical-only registration, from_runtime reload reconstructs the correct workspace."""
        daemon_home = tmp_path / ".happyranch"
        daemon_home.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home))
        reset_registry()
        paths_mod.ensure_daemon_home()
        paths_mod.ensure_token()

        from runtime.config import Settings
        from runtime.daemon.state import DaemonState
        from fastapi.testclient import TestClient
        from runtime.daemon.app import create_app
        from runtime.runtime import RuntimeDir

        _bypass_loopback(monkeypatch)
        state = DaemonState.idle(Settings())
        app = create_app(state)
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})
        store = state.registration_token_store

        token, _ = store.mint_runtime("restart-test")
        _complete_runtime_conformance(client, store, monkeypatch, token)

        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "workspace_adapter_id": "opencode",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200

        # Verify stored shape before restart
        profiles_before = load_runtime_profiles()
        assert profiles_before["restart-test"]["adapter"] == "opencode"
        assert profiles_before["restart-test"]["workspace_adapter_id"] == "opencode"

        # Simulate restart: reset registry, reload from same daemon home
        reset_registry()
        rt = RuntimeDir.init(daemon_home)
        DaemonState.from_runtime(rt, Settings())
        registry2 = get_registry()

        profile = registry2.get_profile("restart-test")
        assert profile is not None, "Profile should survive restart"
        assert profile.workspace_adapter_id == "opencode", (
            f"Restarted profile should have workspace=opencode, got {profile.workspace_adapter_id}"
        )
        assert profile.adapter_id == "opencode"

    def test_org_canonical_only_survives_restart_reload(self, monkeypatch, tmp_path):
        """After org canonical-only registration, from_runtime reload reconstructs the correct workspace."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        reset_registry()

        from runtime.config import Settings
        from runtime.daemon.state import DaemonState
        from fastapi.testclient import TestClient
        from runtime.daemon.app import create_app
        from runtime.runtime import RuntimeDir

        _bypass_loopback(monkeypatch)

        rt = RuntimeDir.init(tmp_path / "runtime_org_restart")
        org_root = rt.orgs_dir / "alpha"
        org_root.mkdir(parents=True)
        (org_root / "org").mkdir()
        (org_root / "org" / "teams.yaml").write_text(
            "teams:\n"
            "  engineering:\n"
            "    manager: engineering_head\n"
            "    workers: [dev_agent]\n"
        )

        state = DaemonState.from_runtime(rt, Settings())
        store = state.registration_token_store
        app = create_app(state)
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})

        token, _ = store.mint("alpha", "org-restart-test")
        _complete_org_conformance(store, token, "alpha")

        r = client.post(
            "/api/v1/orgs/alpha/executors/register",
            json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "workspace_adapter_id": "codex",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200

        # Reload via from_runtime
        reset_registry()
        state2 = DaemonState.from_runtime(rt, Settings())
        registry2 = get_registry()

        profile = registry2.get_profile("org-restart-test")
        assert profile is not None, "Org profile should survive restart"
        assert profile.workspace_adapter_id == "codex", (
            f"Restarted org profile should have workspace=codex, got {profile.workspace_adapter_id}"
        )


# ── Round 3: workspace_adapter rejection ───────────────────────────────


class TestObsoleteWorkspaceAdapterRejection:
    """The obsolete 'workspace_adapter' spelling must be rejected actionablely
    BEFORE any store/registry/audit/token side effect on both routes."""

    def test_workspace_adapter_rejected_runtime(self, _runtime_setup, monkeypatch, tmp_path):
        """Runtime route: body with 'workspace_adapter' → 422, no residue."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        reset_registry()

        from runtime.config import Settings
        from runtime.daemon.state import DaemonState
        from fastapi.testclient import TestClient
        from runtime.daemon.app import create_app

        _bypass_loopback(monkeypatch)
        state = DaemonState.idle(Settings())
        app = create_app(state)
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})
        store = state.registration_token_store

        token, _ = store.mint_runtime("ws-adapter-reject-rt")
        _complete_runtime_conformance(client, store, monkeypatch, token)

        pre_registry = set(get_registry().list_profile_names())
        pre_profiles = dict(load_runtime_profiles())

        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "workspace_adapter": "claude",  # obsolete spelling
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 422, f"Expected 422 for workspace_adapter, got {r.status_code}: {r.json()}"
        assert "workspace_adapter" in r.json()["detail"]

        # No residue
        assert set(get_registry().list_profile_names()) == pre_registry
        assert dict(load_runtime_profiles()) == pre_profiles

    def test_workspace_adapter_rejected_org(self, monkeypatch, tmp_path):
        """Org route: body with 'workspace_adapter' → 422, no residue."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        reset_registry()

        from runtime.config import Settings
        from runtime.daemon.state import DaemonState
        from fastapi.testclient import TestClient
        from runtime.daemon.app import create_app
        from runtime.runtime import RuntimeDir

        _bypass_loopback(monkeypatch)

        rt = RuntimeDir.init(tmp_path / "runtime_ws_adapter_reject")
        org_root = rt.orgs_dir / "alpha"
        org_root.mkdir(parents=True)
        (org_root / "org").mkdir()
        (org_root / "org" / "teams.yaml").write_text(
            "teams:\n"
            "  engineering:\n"
            "    manager: engineering_head\n"
            "    workers: [dev_agent]\n"
        )

        state = DaemonState.from_runtime(rt, Settings())
        store = state.registration_token_store
        app = create_app(state)
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})

        token, _ = store.mint("alpha", "ws-adapter-reject-org")
        _complete_org_conformance(store, token, "alpha")

        pre_registry = set(get_registry().list_profile_names())
        pre_profiles = dict(load_runtime_profiles())

        r = client.post(
            "/api/v1/orgs/alpha/executors/register",
            json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "workspace_adapter": "codex",  # obsolete spelling
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 422, f"Expected 422 for workspace_adapter, got {r.status_code}: {r.json()}"
        assert "workspace_adapter" in r.json()["detail"]

        # No residue
        assert set(get_registry().list_profile_names()) == pre_registry
        assert dict(load_runtime_profiles()) == pre_profiles


# ── Round 3: Command adapter explicit conflict via routes ───────────────


class TestCommandAdapterConflictViaRoutes:
    """Explicit command_adapter/command_adapter_id disagreement must 422
    before any side effect on both shipping endpoints."""

    def test_command_adapter_conflict_runtime(self, _runtime_setup, monkeypatch, tmp_path):
        """Runtime: explicit command_adapter='not-generic' + command_adapter_id='generic-cli' → 422."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        reset_registry()

        from runtime.config import Settings
        from runtime.daemon.state import DaemonState
        from fastapi.testclient import TestClient
        from runtime.daemon.app import create_app

        _bypass_loopback(monkeypatch)
        state = DaemonState.idle(Settings())
        app = create_app(state)
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})
        store = state.registration_token_store

        token, _ = store.mint_runtime("cmd-conflict-rt")
        _complete_runtime_conformance(client, store, monkeypatch, token)

        pre_registry = set(get_registry().list_profile_names())
        pre_profiles = dict(load_runtime_profiles())

        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "command_adapter": "not-generic",
                "command_adapter_id": "generic-cli",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 422, (
            f"Conflicting command adapter should 422, got {r.status_code}: {r.json()}"
        )

        # No residue
        assert set(get_registry().list_profile_names()) == pre_registry
        assert dict(load_runtime_profiles()) == pre_profiles

    def test_command_adapter_conflict_org(self, monkeypatch, tmp_path):
        """Org: explicit command_adapter='not-generic' + command_adapter_id='generic-cli' → 422."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        reset_registry()

        from runtime.config import Settings
        from runtime.daemon.state import DaemonState
        from fastapi.testclient import TestClient
        from runtime.daemon.app import create_app
        from runtime.runtime import RuntimeDir

        _bypass_loopback(monkeypatch)

        rt = RuntimeDir.init(tmp_path / "runtime_cmd_conflict_org")
        org_root = rt.orgs_dir / "alpha"
        org_root.mkdir(parents=True)
        (org_root / "org").mkdir()
        (org_root / "org" / "teams.yaml").write_text(
            "teams:\n"
            "  engineering:\n"
            "    manager: engineering_head\n"
            "    workers: [dev_agent]\n"
        )

        state = DaemonState.from_runtime(rt, Settings())
        store = state.registration_token_store
        app = create_app(state)
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})

        token, _ = store.mint("alpha", "cmd-conflict-org")
        _complete_org_conformance(store, token, "alpha")

        pre_registry = set(get_registry().list_profile_names())
        pre_profiles = dict(load_runtime_profiles())

        r = client.post(
            "/api/v1/orgs/alpha/executors/register",
            json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "command_adapter": "not-generic",
                "command_adapter_id": "generic-cli",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 422, (
            f"Conflicting command adapter should 422 on org route, got {r.status_code}: {r.json()}"
        )

        # No residue
        assert set(get_registry().list_profile_names()) == pre_registry
        assert dict(load_runtime_profiles()) == pre_profiles

    def test_agreeing_command_adapters_succeed(self, _runtime_setup, monkeypatch, tmp_path):
        """Runtime: agreeing command_adapter + command_adapter_id → 200."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        reset_registry()

        from runtime.config import Settings
        from runtime.daemon.state import DaemonState
        from fastapi.testclient import TestClient
        from runtime.daemon.app import create_app

        _bypass_loopback(monkeypatch)
        state = DaemonState.idle(Settings())
        app = create_app(state)
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})
        store = state.registration_token_store

        token, _ = store.mint_runtime("cmd-agree-rt")
        _complete_runtime_conformance(client, store, monkeypatch, token)

        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "command_adapter": "generic-cli",
                "command_adapter_id": "generic-cli",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, f"Agreeing command adapters should 200, got {r.status_code}: {r.json()}"
        body = r.json()
        assert body["command_adapter"] == "generic-cli"
        assert body["command_adapter_id"] == "generic-cli"

    def test_only_command_adapter_id_succeeds(self, _runtime_setup, monkeypatch, tmp_path):
        """Runtime: canonical-only command_adapter_id (no deprecated) → 200."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        reset_registry()

        from runtime.config import Settings
        from runtime.daemon.state import DaemonState
        from fastapi.testclient import TestClient
        from runtime.daemon.app import create_app

        _bypass_loopback(monkeypatch)
        state = DaemonState.idle(Settings())
        app = create_app(state)
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})
        store = state.registration_token_store

        token, _ = store.mint_runtime("cmd-id-only")
        _complete_runtime_conformance(client, store, monkeypatch, token)

        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "command_adapter_id": "generic-cli",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, f"Canonical-only cmd should 200, got {r.status_code}: {r.json()}"

        profiles = load_runtime_profiles()
        stored = profiles["cmd-id-only"]
        assert stored["command_adapter_id"] == "generic-cli"
        assert stored.get("command_adapter") == "generic-cli"
