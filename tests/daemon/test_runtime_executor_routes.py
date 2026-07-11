"""Integration tests for runtime-level (org-agnostic) registration routes.

Tests:
- POST /api/v1/auth/registration-token/runtime (mint) 
- POST /api/v1/executors/runtime/conformance-checkin
- POST /api/v1/executors/runtime/register
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from runtime.config import Settings
from runtime.daemon import paths as paths_mod
from runtime.daemon.app import create_app
from runtime.daemon.registration_token import (
    REGISTRATION_TOKEN_PREFIX,
    _RUNTIME_ORG,
)
from runtime.daemon.state import DaemonState
from runtime.orchestrator.runtime_executor_store import (
    load_runtime_profiles,
    save_runtime_profile,
)
from runtime.orchestrator.executor_registry import get_registry, reset_registry


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    """Seed a daemon home with a token file."""
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / ".happyranch"))
    paths_mod.ensure_daemon_home()
    paths_mod.ensure_token()
    return tmp_path / ".happyranch"


@pytest.fixture
def daemon_state(tmp_home):
    state = DaemonState.idle(Settings())
    return state


@pytest.fixture
def app(daemon_state):
    """Full FastAPI app with all routes mounted."""
    app = create_app(daemon_state)
    return app


@pytest.fixture
def client(app, tmp_home, monkeypatch):
    """TestClient with master bearer + loopback bypass for routes that check it."""
    from runtime.daemon.routes import auth as auth_route
    from runtime.daemon import auth as auth_mod

    monkeypatch.setattr(
        auth_route, "_LOCAL_HOSTS", auth_route._LOCAL_HOSTS | {"testclient"}
    )
    monkeypatch.setattr(
        auth_mod, "_REGISTRATION_LOCAL_HOSTS",
        auth_mod._REGISTRATION_LOCAL_HOSTS | {"testclient"},
    )

    tc = TestClient(app)
    tc.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})
    return tc


@pytest.fixture
def store(daemon_state):
    return daemon_state.registration_token_store


@pytest.fixture(autouse=True)
def clean_registry():
    """Reset the executor registry between tests."""
    reset_registry()
    yield
    reset_registry()


# ── Runtime Mint Route ──────────────────────────────────────────────────


class TestRuntimeMintRoute:
    """POST /api/v1/auth/registration-token/runtime"""

    def test_mint_runtime_succeeds(self, client, store):
        r = client.post("/api/v1/auth/registration-token/runtime", json={
            "name": "my-executor",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["token"].startswith(REGISTRATION_TOKEN_PREFIX)
        assert body["expires_at"] > time.time()

        # Verify it's a runtime token
        record = store.validate_runtime(body["token"])
        assert record is not None
        assert record.org == _RUNTIME_ORG
        assert record.name == "my-executor"

    def test_mint_runtime_token_not_valid_for_org(self, client, store):
        r = client.post("/api/v1/auth/registration-token/runtime", json={
            "name": "my-executor",
        })
        assert r.status_code == 200
        token = r.json()["token"]
        # Should NOT validate as org-scoped
        assert store.validate(token, "alpha") is None

    def test_mint_runtime_rejects_missing_master_bearer(self, app, tmp_home, monkeypatch):
        from runtime.daemon.routes import auth as auth_route
        monkeypatch.setattr(
            auth_route, "_LOCAL_HOSTS", auth_route._LOCAL_HOSTS | {"testclient"}
        )
        client = TestClient(app)
        r = client.post("/api/v1/auth/registration-token/runtime", json={
            "name": "my-executor",
        })
        assert r.status_code == 401

    def test_mint_runtime_rejects_non_loopback(self, app, tmp_home):
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})
        r = client.post("/api/v1/auth/registration-token/runtime", json={
            "name": "my-executor",
        })
        assert r.status_code == 403

    def test_mint_runtime_payload_validated(self, client):
        r = client.post("/api/v1/auth/registration-token/runtime", json={})
        assert r.status_code == 422


# ── Runtime Conformance Check-in Route ──────────────────────────────────


class TestRuntimeConformanceCheckinRoute:
    """POST /api/v1/executors/runtime/conformance-checkin"""

    def _mint_and_auth(self, client, store, monkeypatch=None) -> tuple[str, str]:
        """Mint a runtime token and return (token, profile_name)."""
        token, _ = store.mint_runtime("my-executor")
        return token, "my-executor"

    def test_checkin_accepted_with_valid_runtime_token(self, client, store, monkeypatch):
        token, _ = self._mint_and_auth(client, store)

        # Use the registration token for auth
        r = client.post(
            "/api/v1/executors/runtime/conformance-checkin",
            json={"step_id": "workspace_access"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["arrived"] is True
        assert "loopback_reachable" in body["pending"]

    def test_checkin_rejects_org_token(self, client, store):
        """Org-scoped token should NOT work on runtime routes."""
        token, _ = store.mint("alpha", "my-executor")
        r = client.post(
            "/api/v1/executors/runtime/conformance-checkin",
            json={"step_id": "workspace_access"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 401

    def test_checkin_rejects_invalid_step(self, client, store):
        token, _ = self._mint_and_auth(client, store)
        r = client.post(
            "/api/v1/executors/runtime/conformance-checkin",
            json={"step_id": "unknown_step"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400

    def test_checkin_returns_pending_steps(self, client, store):
        token, _ = self._mint_and_auth(client, store)

        # First check-in
        r = client.post(
            "/api/v1/executors/runtime/conformance-checkin",
            json={"step_id": "workspace_access"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        body = r.json()
        assert not body["all_complete"]
        assert len(body["pending"]) == 2

        # Complete all steps
        for step_id in ["loopback_reachable", "cli_callback"]:
            r = client.post(
                "/api/v1/executors/runtime/conformance-checkin",
                json={"step_id": step_id},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code == 200

        r = client.post(
            "/api/v1/executors/runtime/conformance-checkin",
            json={"step_id": "workspace_access"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["all_complete"]


# ── Runtime Register Route ──────────────────────────────────────────────


class TestRuntimeRegisterRoute:
    """POST /api/v1/executors/runtime/register"""

    def _mint_token_and_complete_conformance(self, client, store, monkeypatch):
        """Mint a runtime token and complete all conformance steps."""
        token, _ = store.mint_runtime("my-executor")
        headers = {"Authorization": f"Bearer {token}"}

        for step_id in ["workspace_access", "loopback_reachable", "cli_callback"]:
            r = client.post(
                "/api/v1/executors/runtime/conformance-checkin",
                json={"step_id": step_id},
                headers=headers,
            )
            assert r.status_code == 200

        return token, headers

    def test_register_succeeds(self, client, store, monkeypatch, tmp_path):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))

        reset_registry()
        token, headers = self._mint_token_and_complete_conformance(
            client, store, monkeypatch
        )

        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "echo",
                "argv_template": ["{prompt}"],
                "adapter": "pi",
            },
            headers=headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "my-executor"
        assert body["kind"] == "custom"
        assert body["adapter_id"] == "pi"

        # Verify it's in the registry
        registry = get_registry()
        assert registry.is_registered("my-executor")
        profile = registry.get_profile("my-executor")
        assert profile.kind == "custom"

        # Verify it's in the runtime store
        profiles = load_runtime_profiles()
        assert "my-executor" in profiles

    def test_register_rejects_without_conformance(self, client, store):
        token, _ = store.mint_runtime("my-executor")
        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "echo",
                "argv_template": ["{prompt}"],
                "adapter": "pi",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400
        assert "incomplete" in r.json()["detail"].lower()

    def test_register_rejects_consumed_token(self, client, store, monkeypatch, tmp_path):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))

        token, _ = store.mint_runtime("my-executor")
        # Consume the token directly
        store.consume_runtime(token)

        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "echo",
                "argv_template": ["{prompt}"],
                "adapter": "pi",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 401

    def test_register_rejects_org_token(self, client, store):
        """Org-scoped token should NOT work on runtime register route."""
        token, _ = store.mint("alpha", "my-executor")
        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "echo",
                "argv_template": ["{prompt}"],
                "adapter": "pi",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 401

    def test_register_rejects_builtin_name(self, client, store, monkeypatch, tmp_path):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))

        # Mint token for "claude" (which is a builtin)
        token, _ = store.mint_runtime("claude")
        headers = {"Authorization": f"Bearer {token}"}

        for step_id in ["workspace_access", "loopback_reachable", "cli_callback"]:
            r = client.post(
                "/api/v1/executors/runtime/conformance-checkin",
                json={"step_id": step_id},
                headers=headers,
            )
            assert r.status_code == 200

        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "echo",
                "argv_template": ["{prompt}"],
                "adapter": "pi",
            },
            headers=headers,
        )
        assert r.status_code == 422

    def test_register_rejects_invalid_adapter(self, client, store, monkeypatch):
        token, _ = store.mint_runtime("my-executor")
        headers = {"Authorization": f"Bearer {token}"}

        # Complete conformance first
        for step_id in ["workspace_access", "loopback_reachable", "cli_callback"]:
            r = client.post(
                "/api/v1/executors/runtime/conformance-checkin",
                json={"step_id": step_id},
                headers=headers,
            )
            assert r.status_code == 200

        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "echo",
                "argv_template": ["{prompt}"],
                "adapter": "invalid-adapter",
            },
            headers=headers,
        )
        assert r.status_code == 422

    def test_register_rejects_command_not_on_path(self, client, store, monkeypatch):
        token, _ = store.mint_runtime("my-executor")
        headers = {"Authorization": f"Bearer {token}"}

        # Complete conformance first
        for step_id in ["workspace_access", "loopback_reachable", "cli_callback"]:
            r = client.post(
                "/api/v1/executors/runtime/conformance-checkin",
                json={"step_id": step_id},
                headers=headers,
            )
            assert r.status_code == 200

        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "this-command-does-not-exist-anywhere",
                "argv_template": ["{prompt}"],
                "adapter": "pi",
            },
            headers=headers,
        )
        assert r.status_code == 422


# ── Startup Profile Loading ─────────────────────────────────────────────


class TestStartupProfileLoading:
    """Runtime profiles are loaded into the registry at daemon startup."""

    def test_profiles_loaded_at_startup(self, tmp_path, monkeypatch):
        """Simulate daemon startup with pre-existing runtime profiles."""
        # Write a profile to the runtime store
        daemon_home = tmp_path / ".happyranch"
        daemon_home.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home))

        entry = {
            "command": "echo",
            "argv_template": ["{prompt}"],
            "adapter": "pi",
        }
        save_runtime_profile("my-executor", entry)

        # Verify profile exists on disk
        profiles = load_runtime_profiles()
        assert "my-executor" in profiles

        # Reset the registry
        reset_registry()

        # Create an org directory so from_runtime has something to load
        org_dir = tmp_path / "runtime" / "orgs" / "test-org" / "org"
        org_dir.mkdir(parents=True, exist_ok=True)
        (org_dir / "teams.yaml").write_text("teams:\n  engineering:\n    lead: \"\"\n")
        # Write a minimal org config
        (org_dir.parent / "config.yaml").write_text("{}\n")

        # Create happyranch.yaml marker
        runtime_dir = tmp_path / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        import yaml
        from datetime import datetime, timezone
        marker = {
            "schema_version": 2,
            "type": "multi-org-runtime",
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        (runtime_dir / "happyranch.yaml").write_text(yaml.safe_dump(marker))

        from runtime.runtime import RuntimeDir
        runtime = RuntimeDir.load(runtime_dir)
        state = DaemonState.from_runtime(runtime, Settings())

        registry = get_registry()
        assert registry.is_registered("my-executor")
        profile = registry.get_profile("my-executor")
        assert profile is not None
        assert profile.kind == "custom"

        state.close_all()
