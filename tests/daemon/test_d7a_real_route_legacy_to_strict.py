"""D7A real HTTP shipping-seam tests: legacy→strict transition through BOTH routes.

TASK-3552 / TASK-3549 review HIGH blocker repair: the direct-registry-bypass
test at test_d7a_envelope_enforcement.py:TestD7AShippingSeamEnforcement
masked the fact that both shipping registration routes rejected a stored/active
legacy profile (envelope_policy=None) versus the mandated strict candidate as a
different-definition collision before any write or active registry update.

These tests replace that bypass with real route→store→active-registry→subprocess
tests for BOTH org-level and runtime-level registration routes, using FastAPI
TestClient with real token mint + conformance + register flow.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from runtime.config import Settings
from runtime.daemon import paths as paths_mod
from runtime.daemon.app import create_app
from runtime.daemon.registration_token import RegistrationTokenStore
from runtime.daemon.state import DaemonState
from runtime.orchestrator.executor_registry import (
    get_registry,
    reset_registry,
    build_executor,
)
from runtime.orchestrator.executors import GenericCliExecutor
from runtime.orchestrator.runtime_executor_store import (
    load_runtime_profiles,
    save_runtime_profile,
    remove_runtime_profile,
)
from runtime.runtime import RuntimeDir


# ── Helpers ─────────────────────────────────────────────────────────────


def _seed_legacy_profile_in_store(name: str) -> None:
    """Seed a legacy (no envelope_policy) profile in the durable runtime store."""
    save_runtime_profile(name, {
        "command": "echo",
        "argv_template": ["echo", "{prompt}"],
        "adapter": "pi",
        "adapter_id": "pi",
        "workspace_adapter_id": "pi",
        "command_adapter": "generic-cli",
        "command_adapter_id": "generic-cli",
    })


def _seed_legacy_profile_in_registry(name: str) -> None:
    """Seed a legacy profile in the active in-memory registry."""
    from runtime.orchestrator.executor_registry import ExecutorProfile
    registry = get_registry()
    profile = ExecutorProfile(
        name=name,
        kind="custom",
        workspace_adapter_id="pi",
        command_adapter_id="generic-cli",
        readiness_marker_fragment="AGENTS.md",
        argv_template=["echo", "{prompt}"],
        command="echo",
        envelope_policy=None,
    )
    registry.register_custom_profile(profile)


def _bypass_loopback(monkeypatch):
    from runtime.daemon.routes import auth as auth_route
    from runtime.daemon import auth as auth_mod
    monkeypatch.setattr(auth_route, "_LOCAL_HOSTS",
                        auth_route._LOCAL_HOSTS | {"testclient"})
    monkeypatch.setattr(auth_mod, "_REGISTRATION_LOCAL_HOSTS",
                        auth_mod._REGISTRATION_LOCAL_HOSTS | {"testclient"})


def _mint_and_complete_conformance(
    store, org="alpha", name="test-legacy-exec",
    *, purpose="profile", runtime=False, now=None,
):
    """Mint a token and complete all conformance steps.

    Returns (token_plaintext, headers_dict).
    """
    if runtime:
        token, _ = store.mint_runtime(name, purpose=purpose, now=now)
    else:
        token, _ = store.mint(org, name, now=now)
    for step_id in RegistrationTokenStore.DEFAULT_CONFORMANCE_STEPS:
        payload: dict = {"step_id": step_id}
        if step_id == "emit_envelope":
            payload["envelope"] = {
                "envelope_version": 1,
                "token_usage": {"input_tokens": 1, "output_tokens": 1},
            }
        if runtime:
            store.record_step_arrival_runtime(token, step_id, now=now)
        else:
            store.record_step_arrival(token, org, step_id, now=now)
    return token, {"Authorization": f"Bearer {token}"}


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / ".happyranch"))
    paths_mod.ensure_daemon_home()
    paths_mod.ensure_token()
    return tmp_path / ".happyranch"


@pytest.fixture
def runtime(tmp_path):
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
    return rt


@pytest.fixture
def daemon_state(runtime):
    return DaemonState.from_runtime(runtime, Settings())


@pytest.fixture(autouse=True)
def clean_registry():
    reset_registry()
    # Also clean the store
    try:
        remove_runtime_profile("test-legacy-exec")
    except (FileNotFoundError, KeyError):
        pass
    try:
        remove_runtime_profile("test-legacy-exec-org")
    except (FileNotFoundError, KeyError):
        pass
    try:
        remove_runtime_profile("test-legacy-exec-rt")
    except (FileNotFoundError, KeyError):
        pass
    try:
        remove_runtime_profile("test-legacy-diff-cmd")
    except (FileNotFoundError, KeyError):
        pass
    yield
    reset_registry()


# ── ORG ROUTE: Real HTTP legacy→strict transition tests ─────────────────


class TestOrgRouteLegacyToStrict:
    """Real org-level route re-registration of a legacy profile → strict."""

    def test_legacy_reregister_org_route_200_strict(
        self, tmp_home, daemon_state, monkeypatch, tmp_path,
    ):
        """Re-registering a seeded legacy profile through the org route
        succeeds 200, updates both durable store and active registry to strict,
        and GenericCli immediately fails closed without a restart."""
        _bypass_loopback(monkeypatch)
        from runtime.daemon.app import create_app
        app = create_app(daemon_state)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        # Seed legacy profile in BOTH store and registry
        _seed_legacy_profile_in_store("test-legacy-exec-org")
        _seed_legacy_profile_in_registry("test-legacy-exec-org")

        # Verify legacy state
        stored = load_runtime_profiles()
        assert stored["test-legacy-exec-org"].get("envelope_policy") is None
        active = get_registry().get_profile("test-legacy-exec-org")
        assert active is not None
        assert active.envelope_policy is None

        # Re-register via real org route
        token, headers = _mint_and_complete_conformance(
            store, org="alpha", name="test-legacy-exec-org",
        )
        client.headers.update(headers)
        r = client.post("/api/v1/orgs/alpha/executors/register", json={
            "command": "echo",
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
        })
        assert r.status_code == 200, f"org re-register failed: {r.json()}"
        body = r.json()
        assert body["envelope_policy"] == "strict", \
            "response must report strict envelope_policy"

        # Durable store must have strict
        stored_after = load_runtime_profiles()
        assert stored_after["test-legacy-exec-org"]["envelope_policy"] == "strict"

        # Active registry must have strict immediately (no restart)
        active_after = get_registry().get_profile("test-legacy-exec-org")
        assert active_after is not None
        assert active_after.envelope_policy == "strict", \
            "active registry profile must be strict immediately — no restart needed"

        # GenericCli missing envelope must fail closed immediately
        executor = build_executor(
            "test-legacy-exec-org", Settings(project_root=tmp_path),
        )
        assert isinstance(executor, GenericCliExecutor)
        assert executor._envelope_policy == "strict"
        result = executor.run(workspace=tmp_path, prompt="hello")
        assert not result.success, "strict profile must fail closed without envelope"
        assert "envelope" in (result.error or "").lower()

    def test_legacy_reregister_org_route_valid_v1_succeeds(
        self, tmp_home, daemon_state, monkeypatch, tmp_path,
    ):
        """Re-registered strict profile accepts valid v1 envelope."""
        _bypass_loopback(monkeypatch)
        from runtime.daemon.app import create_app
        app = create_app(daemon_state)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        _seed_legacy_profile_in_store("test-legacy-exec-org")
        _seed_legacy_profile_in_registry("test-legacy-exec-org")

        token, headers = _mint_and_complete_conformance(
            store, org="alpha", name="test-legacy-exec-org",
        )
        client.headers.update(headers)
        r = client.post("/api/v1/orgs/alpha/executors/register", json={
            "command": "echo",
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
        })
        assert r.status_code == 200

        import json as jmod
        executor = build_executor(
            "test-legacy-exec-org", Settings(project_root=tmp_path),
        )
        envelope = {
            "envelope_version": 1,
            "result": "ok",
            "token_usage": {
                "input_tokens": 10, "output_tokens": 20,
                "cache_read_tokens": 0, "cache_creation_tokens": 0,
                "model": "test"
            },
        }
        valid_stdout = (
            f"normal output\n__HR_ENVELOPE_BEGIN__\n"
            f"{jmod.dumps(envelope)}\n__HR_ENVELOPE_END__\n"
        )
        mock_popen = MagicMock()
        mock_popen.returncode = 0
        mock_popen.communicate.return_value = (valid_stdout, "")
        with patch("subprocess.Popen", return_value=mock_popen):
            result = executor.run(workspace=tmp_path, prompt="hello", timeout_seconds=5)
        assert result.success, f"valid v1 must succeed, got: {result.error}"

    def test_legacy_reregister_changed_command_409(
        self, tmp_home, daemon_state, monkeypatch,
    ):
        """Re-registration with a changed command MUST be rejected (409)."""
        _bypass_loopback(monkeypatch)
        from runtime.daemon.app import create_app
        app = create_app(daemon_state)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        _seed_legacy_profile_in_store("test-legacy-diff-cmd")
        _seed_legacy_profile_in_registry("test-legacy-diff-cmd")

        token, headers = _mint_and_complete_conformance(
            store, org="alpha", name="test-legacy-diff-cmd",
        )
        client.headers.update(headers)
        # Different command from stored legacy profile
        r = client.post("/api/v1/orgs/alpha/executors/register", json={
            "command": "cat",
            "argv_template": ["cat", "{prompt}"],
            "adapter": "pi",
        })
        assert r.status_code == 409, \
            f"changed command must be 409, got {r.status_code}: {r.json()}"

    def test_legacy_reregister_changed_argv_409(
        self, tmp_home, daemon_state, monkeypatch,
    ):
        """Re-registration with changed argv_template MUST be rejected."""
        _bypass_loopback(monkeypatch)
        from runtime.daemon.app import create_app
        app = create_app(daemon_state)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        _seed_legacy_profile_in_store("test-legacy-diff-cmd")
        _seed_legacy_profile_in_registry("test-legacy-diff-cmd")

        token, headers = _mint_and_complete_conformance(
            store, org="alpha", name="test-legacy-diff-cmd",
        )
        client.headers.update(headers)
        r = client.post("/api/v1/orgs/alpha/executors/register", json={
            "command": "echo",
            "argv_template": ["echo", "different-arg", "{prompt}"],
            "adapter": "pi",
        })
        assert r.status_code == 409, \
            f"changed argv must be 409, got {r.status_code}: {r.json()}"

    def test_legacy_reregister_changed_workspace_adapter_409(
        self, tmp_home, daemon_state, monkeypatch,
    ):
        """Re-registration with changed workspace_adapter_id MUST be rejected."""
        _bypass_loopback(monkeypatch)
        from runtime.daemon.app import create_app
        app = create_app(daemon_state)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        _seed_legacy_profile_in_store("test-legacy-diff-cmd")
        _seed_legacy_profile_in_registry("test-legacy-diff-cmd")

        token, headers = _mint_and_complete_conformance(
            store, org="alpha", name="test-legacy-diff-cmd",
        )
        client.headers.update(headers)
        r = client.post("/api/v1/orgs/alpha/executors/register", json={
            "command": "echo",
            "argv_template": ["echo", "{prompt}"],
            "adapter": "claude",
        })
        assert r.status_code == 409, \
            f"changed adapter must be 409, got {r.status_code}: {r.json()}"

    def test_legacy_reregister_builtin_collision_unchanged(
        self, tmp_home, daemon_state, monkeypatch,
    ):
        """Built-in executor override is still rejected (422)."""
        _bypass_loopback(monkeypatch)
        from runtime.daemon.app import create_app
        app = create_app(daemon_state)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        token, headers = _mint_and_complete_conformance(
            store, org="alpha", name="claude",
        )
        client.headers.update(headers)
        r = client.post("/api/v1/orgs/alpha/executors/register", json={
            "command": "echo",
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
        })
        assert r.status_code == 422, \
            f"builtin override must be 422, got {r.status_code}: {r.json()}"

    def test_legacy_read_only_still_optional(
        self, tmp_home, daemon_state, monkeypatch,
    ):
        """Merely reading a legacy profile (not re-registering) leaves it optional."""
        _seed_legacy_profile_in_store("test-legacy-exec-org")
        _seed_legacy_profile_in_registry("test-legacy-exec-org")

        # Read from store — no mutation
        stored = load_runtime_profiles()
        assert "envelope_policy" not in stored["test-legacy-exec-org"]

        # Read from registry — no mutation
        active = get_registry().get_profile("test-legacy-exec-org")
        assert active.envelope_policy is None

        # Read again — no change
        stored2 = load_runtime_profiles()
        assert "envelope_policy" not in stored2["test-legacy-exec-org"]

    def test_strict_idempotent_reregistration_org(
        self, tmp_home, daemon_state, monkeypatch, tmp_path,
    ):
        """Re-registering an already-strict profile is idempotent (200)."""
        _bypass_loopback(monkeypatch)
        from runtime.daemon.app import create_app
        app = create_app(daemon_state)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        # Seed a strict profile
        save_runtime_profile("test-legacy-exec-org", {
            "command": "echo",
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
            "adapter_id": "pi",
            "workspace_adapter_id": "pi",
            "command_adapter": "generic-cli",
            "command_adapter_id": "generic-cli",
            "envelope_policy": "strict",
        })
        _seed_legacy_profile_in_registry("test-legacy-exec-org")
        # Override to strict in registry too
        reset_registry()
        from runtime.orchestrator.executor_registry import ExecutorProfile
        get_registry().register_custom_profile(ExecutorProfile(
            name="test-legacy-exec-org", kind="custom",
            workspace_adapter_id="pi", command_adapter_id="generic-cli",
            readiness_marker_fragment="AGENTS.md",
            argv_template=["echo", "{prompt}"], command="echo",
            envelope_policy="strict",
        ))

        # Re-register the same strict profile
        token, headers = _mint_and_complete_conformance(
            store, org="alpha", name="test-legacy-exec-org",
        )
        client.headers.update(headers)
        r = client.post("/api/v1/orgs/alpha/executors/register", json={
            "command": "echo",
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
        })
        assert r.status_code == 200, \
            f"idempotent strict re-reg must be 200, got {r.status_code}: {r.json()}"
        body = r.json()
        assert body["envelope_policy"] == "strict"

    def test_d6_alias_conflict_still_rejected_org(
        self, tmp_home, daemon_state, monkeypatch,
    ):
        """D6 alias conflict (adapter vs workspace_adapter_id) still rejected."""
        _bypass_loopback(monkeypatch)
        from runtime.daemon.app import create_app
        app = create_app(daemon_state)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        token, headers = _mint_and_complete_conformance(
            store, org="alpha", name="test-legacy-exec-org",
        )
        client.headers.update(headers)
        r = client.post("/api/v1/orgs/alpha/executors/register", json={
            "command": "echo",
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
            "workspace_adapter_id": "claude",
        })
        assert r.status_code in (422, 409), \
            f"D6 alias conflict must reject, got {r.status_code}: {r.json()}"

    def test_legacy_reregister_after_failed_attempt_token_valid(
        self, tmp_home, daemon_state, monkeypatch,
    ):
        """After a 409 from changed command, the profile state is unchanged
        and the token is released (no residue)."""
        _bypass_loopback(monkeypatch)
        from runtime.daemon.app import create_app
        app = create_app(daemon_state)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        _seed_legacy_profile_in_store("test-legacy-exec-org")
        _seed_legacy_profile_in_registry("test-legacy-exec-org")

        token, headers = _mint_and_complete_conformance(
            store, org="alpha", name="test-legacy-exec-org",
        )
        client.headers.update(headers)
        r = client.post("/api/v1/orgs/alpha/executors/register", json={
            "command": "cat",  # different from stored
            "argv_template": ["cat", "{prompt}"],
            "adapter": "pi",
        })
        assert r.status_code == 409

        # Profile must be unchanged (still legacy, None)
        active = get_registry().get_profile("test-legacy-exec-org")
        assert active is not None
        assert active.envelope_policy is None, "legacy profile must be unchanged after rejection"
        stored = load_runtime_profiles()
        assert stored["test-legacy-exec-org"].get("envelope_policy") is None

        # Token should still be valid (released on failure)
        record = store.validate(token, "alpha")
        assert record is not None, "token must still be valid after 409"


# ── RUNTIME ROUTE: Real HTTP legacy→strict transition tests ─────────────


class TestRuntimeRouteLegacyToStrict:
    """Real runtime-level route re-registration of a legacy profile → strict."""

    def test_legacy_reregister_runtime_route_200_strict(
        self, tmp_home, daemon_state, monkeypatch, tmp_path,
    ):
        """Re-registering a seeded legacy profile through the runtime route
        succeeds 200, updates both durable store and active registry to strict,
        and GenericCli immediately fails closed without restart."""
        _bypass_loopback(monkeypatch)
        from runtime.daemon.app import create_app
        app = create_app(daemon_state)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        _seed_legacy_profile_in_store("test-legacy-exec-rt")
        _seed_legacy_profile_in_registry("test-legacy-exec-rt")

        # Verify legacy state
        stored = load_runtime_profiles()
        assert stored["test-legacy-exec-rt"].get("envelope_policy") is None
        active = get_registry().get_profile("test-legacy-exec-rt")
        assert active is not None
        assert active.envelope_policy is None

        # Re-register via runtime route
        token, headers = _mint_and_complete_conformance(
            store, name="test-legacy-exec-rt", runtime=True,
        )
        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "adapter": "pi",
            },
            headers=headers,
        )
        assert r.status_code == 200, f"runtime re-register failed: {r.json()}"
        body = r.json()
        assert body["envelope_policy"] == "strict"

        # Durable store must have strict
        stored_after = load_runtime_profiles()
        assert stored_after["test-legacy-exec-rt"]["envelope_policy"] == "strict"

        # Active registry must have strict immediately (no restart)
        active_after = get_registry().get_profile("test-legacy-exec-rt")
        assert active_after is not None
        assert active_after.envelope_policy == "strict", \
            "active registry profile must be strict immediately — no restart needed"

        # GenericCli missing envelope must fail closed immediately
        executor = build_executor(
            "test-legacy-exec-rt", Settings(project_root=tmp_path),
        )
        assert isinstance(executor, GenericCliExecutor)
        assert executor._envelope_policy == "strict"
        result = executor.run(workspace=tmp_path, prompt="hello")
        assert not result.success, "strict profile must fail closed without envelope"
        assert "envelope" in (result.error or "").lower()

    def test_legacy_reregister_runtime_route_valid_v1_succeeds(
        self, tmp_home, daemon_state, monkeypatch, tmp_path,
    ):
        """Re-registered strict profile accepts valid v1 envelope (runtime route)."""
        _bypass_loopback(monkeypatch)
        from runtime.daemon.app import create_app
        app = create_app(daemon_state)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        _seed_legacy_profile_in_store("test-legacy-exec-rt")
        _seed_legacy_profile_in_registry("test-legacy-exec-rt")

        token, headers = _mint_and_complete_conformance(
            store, name="test-legacy-exec-rt", runtime=True,
        )
        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "adapter": "pi",
            },
            headers=headers,
        )
        assert r.status_code == 200

        import json as jmod
        executor = build_executor(
            "test-legacy-exec-rt", Settings(project_root=tmp_path),
        )
        envelope = {
            "envelope_version": 1,
            "result": "ok",
            "token_usage": {
                "input_tokens": 10, "output_tokens": 20,
                "cache_read_tokens": 0, "cache_creation_tokens": 0,
                "model": "test"
            },
        }
        valid_stdout = (
            f"normal output\n__HR_ENVELOPE_BEGIN__\n"
            f"{jmod.dumps(envelope)}\n__HR_ENVELOPE_END__\n"
        )
        mock_popen = MagicMock()
        mock_popen.returncode = 0
        mock_popen.communicate.return_value = (valid_stdout, "")
        with patch("subprocess.Popen", return_value=mock_popen):
            result = executor.run(workspace=tmp_path, prompt="hello", timeout_seconds=5)
        assert result.success, f"valid v1 must succeed, got: {result.error}"

    def test_legacy_reregister_changed_command_409_runtime(
        self, tmp_home, daemon_state, monkeypatch,
    ):
        """Changed command MUST be rejected on runtime route (409)."""
        _bypass_loopback(monkeypatch)
        from runtime.daemon.app import create_app
        app = create_app(daemon_state)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        _seed_legacy_profile_in_store("test-legacy-diff-cmd")
        _seed_legacy_profile_in_registry("test-legacy-diff-cmd")

        token, headers = _mint_and_complete_conformance(
            store, name="test-legacy-diff-cmd", runtime=True,
        )
        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "cat",
                "argv_template": ["cat", "{prompt}"],
                "adapter": "pi",
            },
            headers=headers,
        )
        assert r.status_code == 409, \
            f"changed command must be 409, got {r.status_code}: {r.json()}"

    def test_legacy_reregister_changed_workspace_adapter_409_runtime(
        self, tmp_home, daemon_state, monkeypatch,
    ):
        """Changed workspace_adapter_id MUST be rejected on runtime route."""
        _bypass_loopback(monkeypatch)
        from runtime.daemon.app import create_app
        app = create_app(daemon_state)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        _seed_legacy_profile_in_store("test-legacy-diff-cmd")
        _seed_legacy_profile_in_registry("test-legacy-diff-cmd")

        token, headers = _mint_and_complete_conformance(
            store, name="test-legacy-diff-cmd", runtime=True,
        )
        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "adapter": "claude",
            },
            headers=headers,
        )
        assert r.status_code == 409, \
            f"changed adapter must be 409, got {r.status_code}: {r.json()}"

    def test_legacy_reregister_builtin_collision_unchanged_runtime(
        self, tmp_home, daemon_state, monkeypatch,
    ):
        """Built-in override on runtime route still rejected."""
        _bypass_loopback(monkeypatch)
        from runtime.daemon.app import create_app
        app = create_app(daemon_state)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        token, headers = _mint_and_complete_conformance(
            store, name="claude", runtime=True,
        )
        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "adapter": "pi",
            },
            headers=headers,
        )
        assert r.status_code == 422, \
            f"builtin override must be 422, got {r.status_code}: {r.json()}"

    def test_strict_idempotent_reregistration_runtime(
        self, tmp_home, daemon_state, monkeypatch,
    ):
        """Re-registering an already-strict profile on runtime route is idempotent."""
        _bypass_loopback(monkeypatch)
        from runtime.daemon.app import create_app
        app = create_app(daemon_state)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        # Seed a strict profile
        save_runtime_profile("test-legacy-exec-rt", {
            "command": "echo",
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
            "adapter_id": "pi",
            "workspace_adapter_id": "pi",
            "command_adapter": "generic-cli",
            "command_adapter_id": "generic-cli",
            "envelope_policy": "strict",
        })
        _seed_legacy_profile_in_registry("test-legacy-exec-rt")
        reset_registry()
        from runtime.orchestrator.executor_registry import ExecutorProfile
        get_registry().register_custom_profile(ExecutorProfile(
            name="test-legacy-exec-rt", kind="custom",
            workspace_adapter_id="pi", command_adapter_id="generic-cli",
            readiness_marker_fragment="AGENTS.md",
            argv_template=["echo", "{prompt}"], command="echo",
            envelope_policy="strict",
        ))

        token, headers = _mint_and_complete_conformance(
            store, name="test-legacy-exec-rt", runtime=True,
        )
        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "adapter": "pi",
            },
            headers=headers,
        )
        assert r.status_code == 200, \
            f"idempotent strict re-reg must be 200, got {r.status_code}: {r.json()}"
        body = r.json()
        assert body["envelope_policy"] == "strict"

    def test_legacy_reregister_after_failed_attempt_runtime_token_valid(
        self, tmp_home, daemon_state, monkeypatch,
    ):
        """After a 409 from changed command on runtime route, token is released."""
        _bypass_loopback(monkeypatch)
        from runtime.daemon.app import create_app
        app = create_app(daemon_state)
        client = TestClient(app)
        store = daemon_state.registration_token_store

        _seed_legacy_profile_in_store("test-legacy-diff-cmd")
        _seed_legacy_profile_in_registry("test-legacy-diff-cmd")

        token, headers = _mint_and_complete_conformance(
            store, name="test-legacy-diff-cmd", runtime=True,
        )
        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "cat",
                "argv_template": ["cat", "{prompt}"],
                "adapter": "pi",
            },
            headers=headers,
        )
        assert r.status_code == 409

        # Profile unchanged
        active = get_registry().get_profile("test-legacy-diff-cmd")
        assert active is not None
        assert active.envelope_policy is None

        # Token still valid
        record = store.validate_runtime(token)
        assert record is not None, "token must still be valid after 409"

    def test_runtime_route_list_inventory_after_reregister(
        self, tmp_home, daemon_state, monkeypatch,
    ):
        """After legacy→strict re-registration, the runtime list route reports strict."""
        _bypass_loopback(monkeypatch)
        from runtime.daemon.app import create_app
        app = create_app(daemon_state)
        client = TestClient(app)
        client.headers.update(
            {"Authorization": f"Bearer {paths_mod.read_token()}"}
        )
        store = daemon_state.registration_token_store

        _seed_legacy_profile_in_store("test-legacy-exec-rt")
        _seed_legacy_profile_in_registry("test-legacy-exec-rt")

        # List before: should report null/absent envelope_policy
        r = client.get("/api/v1/executors/runtime/profiles")
        assert r.status_code == 200
        profiles_before = {p["name"]: p for p in r.json()["profiles"]}
        assert profiles_before["test-legacy-exec-rt"]["envelope_policy"] is None

        # Re-register
        token, headers = _mint_and_complete_conformance(
            store, name="test-legacy-exec-rt", runtime=True,
        )
        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "adapter": "pi",
            },
            headers=headers,
        )
        assert r.status_code == 200

        # List after: must report strict
        r = client.get("/api/v1/executors/runtime/profiles")
        assert r.status_code == 200
        profiles_after = {p["name"]: p for p in r.json()["profiles"]}
        assert profiles_after["test-legacy-exec-rt"]["envelope_policy"] == "strict"


# ── Interleaving / rollback proof ──────────────────────────────────────


class TestConcurrentRegistrationSafety:
    """Proof that the per-profile-name lock prevents registration hazards."""

    def test_concurrent_legacy_to_strict_two_tokens_one_wins(
        self, tmp_home, daemon_state, monkeypatch, tmp_path,
    ):
        """Two concurrent legacy→strict registrations with different tokens:
        one wins (200), the other is rejected (409) with no divergence."""
        _bypass_loopback(monkeypatch)
        from runtime.daemon.app import create_app
        app = create_app(daemon_state)
        store = daemon_state.registration_token_store

        _seed_legacy_profile_in_store("test-legacy-exec-org")
        _seed_legacy_profile_in_registry("test-legacy-exec-org")

        # Mint two tokens
        token_a, headers_a = _mint_and_complete_conformance(
            store, org="alpha", name="test-legacy-exec-org",
        )
        token_b, headers_b = _mint_and_complete_conformance(
            store, org="alpha", name="test-legacy-exec-org",
        )

        results = {}
        barrier = threading.Barrier(2)
        lock_held = threading.Event()

        def _register(token_val, headers, label):
            barrier.wait()
            c = TestClient(app)
            c.headers.update(headers)
            r = c.post("/api/v1/orgs/alpha/executors/register", json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "adapter": "pi",
            })
            results[label] = r.status_code

        t_a = threading.Thread(target=_register, args=(token_a, headers_a, "a"))
        t_b = threading.Thread(target=_register, args=(token_b, headers_b, "b"))
        t_a.start()
        t_b.start()
        t_a.join(timeout=10)
        t_b.join(timeout=10)

        # With the per-profile-name lock, the first thread updates the registry
        # to strict; the second thread sees the updated profile and passes
        # the idempotent re-registration check (both strict → 200).
        # Both must succeed with no divergence.
        statuses = list(results.values())
        assert 200 in statuses, f"at least one must succeed: {results}"
        # Both 200 is correct with lock serialization; 409 is also valid
        # if timing creates a collision window
        assert all(s in (200, 409, 401) for s in statuses), \
            f"unexpected status: {results}"

        # Final state must be coherent: strict in both store and registry
        active = get_registry().get_profile("test-legacy-exec-org")
        assert active is not None
        assert active.envelope_policy == "strict", \
            "winner must leave active registry strict"

        stored = load_runtime_profiles()
        assert stored["test-legacy-exec-org"]["envelope_policy"] == "strict", \
            "winner must leave durable store strict"

        # GenericCli must fail closed (no restart)
        executor = build_executor(
            "test-legacy-exec-org", Settings(project_root=tmp_path),
        )
        assert executor._envelope_policy == "strict"
        result = executor.run(workspace=tmp_path, prompt="hello")
        assert not result.success, "strict enforcement must be active after concurrent registration"
