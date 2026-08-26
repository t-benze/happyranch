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
        assert len(body["pending"]) == 3

        # Complete remaining non-emit steps
        for step_id in ["loopback_reachable", "cli_callback"]:
            r = client.post(
                "/api/v1/executors/runtime/conformance-checkin",
                json={"step_id": step_id},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code == 200

        r = client.post(
            "/api/v1/executors/runtime/conformance-checkin",
            json={
                "step_id": "emit_envelope",
                "envelope": {"envelope_version": 1, "token_usage": {"input_tokens": 1, "output_tokens": 1}},
            },
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
                "argv_template": ["echo", "{prompt}"],
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
                "argv_template": ["echo", "{prompt}"],
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
                "argv_template": ["echo", "{prompt}"],
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
                "argv_template": ["echo", "{prompt}"],
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

        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "adapter": "pi",
            },
            headers=headers,
        )
        assert r.status_code == 422

    def test_register_rejects_invalid_adapter(self, client, store, monkeypatch):
        token, _ = store.mint_runtime("my-executor")
        headers = {"Authorization": f"Bearer {token}"}

        # Complete conformance first
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

        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "adapter": "invalid-adapter",
            },
            headers=headers,
        )
        assert r.status_code == 422

    def test_register_rejects_command_not_on_path(self, client, store, monkeypatch):
        token, _ = store.mint_runtime("my-executor")
        headers = {"Authorization": f"Bearer {token}"}

        # Complete conformance first
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

        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "this-command-does-not-exist-anywhere",
                "argv_template": ["echo", "{prompt}"],
                "adapter": "pi",
            },
            headers=headers,
        )
        assert r.status_code == 422


# ── Startup Profile Loading ─────────────────────────────────────────────


# ── Runtime Registration Audit (THR-088 Slice B) ────────────────────


class TestRuntimeRegisterAudit:
    """Runtime-level registration writes an audit row to runtime-audit.db."""

    def test_register_audits_on_success(self, client, store, monkeypatch, tmp_path):
        """A successful runtime register writes an audit_log row with
        the expected scope-prefix task_id, action, and payload."""
        from runtime.infrastructure.database import Database

        daemon_home_path = tmp_path / ".happyranch"
        daemon_home_path.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home_path))

        # Seed the daemon home for paths_mod.ensure_daemon_home / paths_mod.ensure_token
        from runtime.daemon import paths as paths_mod
        paths_mod.ensure_daemon_home()
        paths_mod.ensure_token()

        reset_registry()
        token, _ = store.mint_runtime("my-executor")
        headers = {"Authorization": f"Bearer {token}"}

        # Complete conformance
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

        # Perform register
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

        # Verify audit row in runtime-audit.db
        audit_db_path = daemon_home_path / "runtime-audit.db"
        assert audit_db_path.exists(), (
            f"runtime-audit.db should have been created at {audit_db_path}"
        )

        audit_db = Database(audit_db_path)
        try:
            rows = audit_db.get_audit_logs("executor:my-executor")
            assert len(rows) == 1, f"Expected 1 audit row, got {len(rows)}"

            row = rows[0]
            assert row["task_id"] == "executor:my-executor"
            assert row["action"] == "executor_registered"
            assert row["agent"] == "founder"

            payload = row["payload"]
            assert payload["command"] == "echo"
            assert payload["argv_template"] == ["echo", "{prompt}"]
            assert payload["adapter"] == "pi"
        finally:
            audit_db.close()

    def test_register_failure_produces_no_audit_row(self, client, store, monkeypatch, tmp_path):
        """A FAILED runtime register (token released) produces NO audit row."""
        from runtime.infrastructure.database import Database

        daemon_home_path = tmp_path / ".happyranch"
        daemon_home_path.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home_path))

        from runtime.daemon import paths as paths_mod
        paths_mod.ensure_daemon_home()
        paths_mod.ensure_token()

        reset_registry()
        token, _ = store.mint_runtime("bad-executor")

        # Do NOT complete conformance — register should fail
        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "adapter": "pi",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400  # conformance incomplete

        # No audit db should exist (or no row if db was created)
        audit_db_path = daemon_home_path / "runtime-audit.db"
        if audit_db_path.exists():
            audit_db = Database(audit_db_path)
            try:
                rows = audit_db.get_audit_logs("executor:bad-executor")
                assert len(rows) == 0, (
                    f"Expected 0 audit rows for failed register, got {len(rows)}"
                )
            finally:
                audit_db.close()


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
            "argv_template": ["echo", "{prompt}"],
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


# ── Runtime Register-Binary Route (THR-088) ─────────────────────────


class TestRuntimeRegisterBinaryRoute:
    """POST /api/v1/executors/runtime/register-binary

    Security-critical: token-purpose fence, kind-isolation, binary validation.
    """

    @pytest.fixture(autouse=True)
    def _clean_registry_file(self, tmp_path, monkeypatch):
        """Ensure a clean executors.json for each test."""
        daemon_home = tmp_path / ".happyranch"
        daemon_home.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home))
        # Re-seed the daemon home so paths resolve
        from runtime.daemon import paths as paths_mod
        paths_mod.ensure_daemon_home()
        paths_mod.ensure_token()
        # Remove any pre-existing executors.json
        reg_path = daemon_home / "executors.json"
        if reg_path.exists():
            reg_path.unlink()
        return daemon_home

    def _mint_binary_token_and_complete_conformance(
        self, client, store, monkeypatch, kind="claude"
    ):
        """Mint a binary-purpose runtime token and complete all conformance steps."""
        token, _ = store.mint_runtime(kind, purpose="binary")
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

        return token, headers

    def _create_valid_executable(self, tmp_path):
        """Create a valid executable binary for testing."""
        exe = tmp_path / "bin" / "myexecutor"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.touch(mode=0o755)
        return str(exe)

    # ── Happy path ──────────────────────────────────────────────────

    def test_register_binary_happy_path(self, client, store, monkeypatch, tmp_path):
        """Binary-purpose token + complete conformance -> set_binary succeeds.

        THR-107: stored and returned path preserves the operator-supplied
        spelling (not Path.resolve()'s versioned target).
        """
        token, headers = self._mint_binary_token_and_complete_conformance(
            client, store, monkeypatch, kind="claude"
        )
        exe_path = self._create_valid_executable(tmp_path)

        r = client.post(
            "/api/v1/executors/runtime/register-binary",
            json={"path": exe_path},
            headers=headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["kind"] == "claude"
        assert body["valid"] is True
        # THR-107: returned path equals the supplied path (not resolved)
        assert body["path"] == exe_path

        # Verify it was written to the registry with the supplied path
        from runtime.orchestrator.executor_binary_registry import load_registry
        registry = load_registry()
        assert "claude" in registry
        assert registry["claude"] == exe_path

    def test_register_binary_token_single_use(self, client, store, monkeypatch, tmp_path):
        """Second attempt with same token -> 401 (token consumed)."""
        token, headers = self._mint_binary_token_and_complete_conformance(
            client, store, monkeypatch, kind="codex"
        )
        exe_path = self._create_valid_executable(tmp_path)

        r1 = client.post(
            "/api/v1/executors/runtime/register-binary",
            json={"path": exe_path},
            headers=headers,
        )
        assert r1.status_code == 200

        r2 = client.post(
            "/api/v1/executors/runtime/register-binary",
            json={"path": exe_path},
            headers=headers,
        )
        assert r2.status_code == 401

    # ── Security fence: purpose isolation ────────────────────────────

    def test_profile_purpose_token_rejected_for_register_binary(
        self, client, store, monkeypatch, tmp_path
    ):
        """A profile-purpose token POSTed to register-binary is REJECTED."""
        exe_path = self._create_valid_executable(tmp_path)
        # Mint a PROFILE-purpose token (the default)
        token, _ = store.mint_runtime("claude", purpose="profile")
        headers = {"Authorization": f"Bearer {token}"}

        # Complete conformance (token is still profile-purpose)
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

        r = client.post(
            "/api/v1/executors/runtime/register-binary",
            json={"path": exe_path},
            headers=headers,
        )
        # Must reject — wrong purpose
        assert r.status_code in (401, 403), (
            f"Expected 401 or 403 for profile-purpose token on register-binary, "
            f"got {r.status_code}"
        )

    def test_binary_purpose_token_rejected_for_register_profile(
        self, client, store, monkeypatch, tmp_path
    ):
        """A binary-purpose token POSTed to /register (profile mint) is REJECTED."""
        token, headers = self._mint_binary_token_and_complete_conformance(
            client, store, monkeypatch, kind="claude"
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
        # Must reject — binary-purpose token should not work for profile register
        assert r.status_code in (401, 403), (
            f"Expected 401 or 403 for binary-purpose token on profile register, "
            f"got {r.status_code}"
        )

    def test_register_binary_writes_only_token_kind(
        self, client, store, monkeypatch, tmp_path
    ):
        """register-binary writes ONLY the token's own kind (record.name).

        There is NO body kind — so it cannot write another kind's path.
        """
        # Mint token for "opencode"
        token, headers = self._mint_binary_token_and_complete_conformance(
            client, store, monkeypatch, kind="opencode"
        )
        exe_path = self._create_valid_executable(tmp_path)

        r = client.post(
            "/api/v1/executors/runtime/register-binary",
            json={"path": exe_path},
            headers=headers,
        )
        assert r.status_code == 200
        body = r.json()
        # Kind must be "opencode" (from the token), not something else
        assert body["kind"] == "opencode"

        # Verify the registry ONLY has "opencode", not some other kind
        from runtime.orchestrator.executor_binary_registry import load_registry
        registry = load_registry()
        assert "opencode" in registry
        # No other kind could have been written
        assert len(registry) == 1

    def test_register_binary_kind_pinned_to_token_name(
        self, client, store, monkeypatch, tmp_path
    ):
        """Each binary-purpose token can only write its own kind.

        Two different tokens for different kinds write independent entries.
        """
        exe_path = self._create_valid_executable(tmp_path)

        # Token for "claude"
        token_c, headers_c = self._mint_binary_token_and_complete_conformance(
            client, store, monkeypatch, kind="claude"
        )
        r = client.post(
            "/api/v1/executors/runtime/register-binary",
            json={"path": exe_path},
            headers=headers_c,
        )
        assert r.status_code == 200
        assert r.json()["kind"] == "claude"

        # Token for "codex"
        token_x, headers_x = self._mint_binary_token_and_complete_conformance(
            client, store, monkeypatch, kind="codex"
        )
        r = client.post(
            "/api/v1/executors/runtime/register-binary",
            json={"path": exe_path},
            headers=headers_x,
        )
        assert r.status_code == 200
        assert r.json()["kind"] == "codex"

        # Registry has both
        from runtime.orchestrator.executor_binary_registry import load_registry
        registry = load_registry()
        assert "claude" in registry
        assert "codex" in registry

    # ── Validation failure: retryable, no write, no consume ──────────

    def test_register_binary_validate_failure_not_absolute(
        self, client, store, monkeypatch
    ):
        """Non-absolute path -> 422, token NOT consumed (retryable)."""
        token, headers = self._mint_binary_token_and_complete_conformance(
            client, store, monkeypatch, kind="pi"
        )

        r = client.post(
            "/api/v1/executors/runtime/register-binary",
            json={"path": "relative/path/to/pi"},
            headers=headers,
        )
        assert r.status_code == 422

        # Token should NOT be consumed — retryable with valid path
        assert store.validate_runtime(token) is not None, (
            "Token should still be valid after validation failure"
        )

    def test_register_binary_validate_failure_nonexistent(
        self, client, store, monkeypatch
    ):
        """Non-existent path -> 422, token NOT consumed (retryable)."""
        token, headers = self._mint_binary_token_and_complete_conformance(
            client, store, monkeypatch, kind="pi"
        )

        r = client.post(
            "/api/v1/executors/runtime/register-binary",
            json={"path": "/nonexistent/path/to/binary"},
            headers=headers,
        )
        assert r.status_code == 422

        # Token should NOT be consumed
        assert store.validate_runtime(token) is not None

    def test_register_binary_validate_failure_not_executable(
        self, client, store, monkeypatch, tmp_path
    ):
        """Non-executable path -> 422, token NOT consumed (retryable)."""
        token, headers = self._mint_binary_token_and_complete_conformance(
            client, store, monkeypatch, kind="pi"
        )

        f = tmp_path / "not_executable"
        f.touch(mode=0o644)

        r = client.post(
            "/api/v1/executors/runtime/register-binary",
            json={"path": str(f)},
            headers=headers,
        )
        assert r.status_code == 422

        # Token should NOT be consumed
        assert store.validate_runtime(token) is not None

    def test_register_binary_validate_failure_no_registry_write(
        self, client, store, monkeypatch, tmp_path
    ):
        """Validation failure produces NO registry write (no file created)."""
        token, headers = self._mint_binary_token_and_complete_conformance(
            client, store, monkeypatch, kind="pi"
        )

        r = client.post(
            "/api/v1/executors/runtime/register-binary",
            json={"path": "/nonexistent/path"},
            headers=headers,
        )
        assert r.status_code == 422

        # executors.json should NOT have been written
        from runtime.orchestrator.executor_binary_registry import load_registry
        registry = load_registry()
        assert "pi" not in registry, (
            "Registry should be empty after validation failure"
        )

    def test_register_binary_retry_after_validation_failure_succeeds(
        self, client, store, monkeypatch, tmp_path
    ):
        """After a validation failure, the same token can retry with a valid path."""
        exe_path = self._create_valid_executable(tmp_path)
        token, headers = self._mint_binary_token_and_complete_conformance(
            client, store, monkeypatch, kind="pi"
        )

        # First attempt: bad path
        r1 = client.post(
            "/api/v1/executors/runtime/register-binary",
            json={"path": "/nonexistent/path"},
            headers=headers,
        )
        assert r1.status_code == 422

        # Second attempt: valid path — should succeed
        r2 = client.post(
            "/api/v1/executors/runtime/register-binary",
            json={"path": exe_path},
            headers=headers,
        )
        assert r2.status_code == 200
        assert r2.json()["kind"] == "pi"
        assert r2.json()["valid"] is True

    # ── Conformance gating ───────────────────────────────────────────

    def test_register_binary_rejects_without_conformance(
        self, client, store, monkeypatch, tmp_path
    ):
        """Binary-purpose token without complete conformance is rejected."""
        exe_path = self._create_valid_executable(tmp_path)
        token, _ = store.mint_runtime("claude", purpose="binary")
        headers = {"Authorization": f"Bearer {token}"}

        r = client.post(
            "/api/v1/executors/runtime/register-binary",
            json={"path": exe_path},
            headers=headers,
        )
        assert r.status_code == 400
        assert "incomplete" in r.json()["detail"].lower()

        # Token should still be valid (not consumed)
        assert store.validate_runtime(token) is not None

    # ── Invalid token gating ─────────────────────────────────────────

    def test_register_binary_rejects_org_token(self, client, store, monkeypatch, tmp_path):
        """Org-scoped token should NOT work on runtime register-binary."""
        exe_path = self._create_valid_executable(tmp_path)
        token, _ = store.mint("alpha", "claude")
        headers = {"Authorization": f"Bearer {token}"}

        r = client.post(
            "/api/v1/executors/runtime/register-binary",
            json={"path": exe_path},
            headers=headers,
        )
        assert r.status_code == 401

    def test_register_binary_rejects_missing_token(self, client, monkeypatch, tmp_path):
        """No token -> 401."""
        exe_path = self._create_valid_executable(tmp_path)

        r = client.post(
            "/api/v1/executors/runtime/register-binary",
            json={"path": exe_path},
            # No Authorization header
        )
        assert r.status_code == 401

    def test_register_binary_rejects_master_bearer(self, client, monkeypatch, tmp_path):
        """Master bearer token rejected on register-binary route."""
        exe_path = self._create_valid_executable(tmp_path)

        r = client.post(
            "/api/v1/executors/runtime/register-binary",
            json={"path": exe_path},
            headers={"Authorization": f"Bearer {paths_mod.read_token()}"},
        )
        # require_registration_token rejects master bearer
        assert r.status_code == 401

    # ── THR-107: symlink path preservation tests ────────────────────────

    def test_register_binary_symlink_preserves_path(
        self, client, store, monkeypatch, tmp_path
    ):
        """Runtime register-binary preserves a symlink path (THR-107).

        Like the bearer route, the scoped-token register-binary must store
        the symlink spelling, not the resolved target.
        """
        import os
        # Create target and symlink
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        target = target_dir / "real-pi"
        target.touch(mode=0o755)
        symlink = tmp_path / "pi"
        os.symlink(str(target), str(symlink))

        token, headers = self._mint_binary_token_and_complete_conformance(
            client, store, monkeypatch, kind="pi"
        )

        r = client.post(
            "/api/v1/executors/runtime/register-binary",
            json={"path": str(symlink)},
            headers=headers,
        )
        assert r.status_code == 200
        body = r.json()
        # Must preserve the symlink path
        assert body["path"] == str(symlink)
        assert body["path"] != str(target.resolve())
        assert body["valid"] is True

        # Registry stores the symlink path
        from runtime.orchestrator.executor_binary_registry import load_registry
        registry = load_registry()
        assert registry["pi"] == str(symlink)

    def test_register_binary_symlink_stale_after_target_gone(
        self, client, store, monkeypatch, tmp_path
    ):
        """Runtime register-binary symlink becomes stale when target removed.

        THR-107 staleness invariant: stale detection still works through
        symlinks — _resolve_binary will raise ExecutorBinaryBlocked.
        """
        import os
        target = tmp_path / "target" / "claude"
        target.parent.mkdir()
        target.touch(mode=0o755)
        symlink = tmp_path / "claude"
        os.symlink(str(target), str(symlink))

        token, headers = self._mint_binary_token_and_complete_conformance(
            client, store, monkeypatch, kind="claude"
        )

        r = client.post(
            "/api/v1/executors/runtime/register-binary",
            json={"path": str(symlink)},
            headers=headers,
        )
        assert r.status_code == 200

        # Delete target → symlink becomes stale
        target.unlink()

        # is_binary_valid must now return False
        from runtime.orchestrator.executor_binary_registry import is_binary_valid
        assert is_binary_valid(str(symlink)) is False

    # ── Mint route: purpose parameter ─────────────────────────────────

    def test_mint_runtime_binary_purpose(self, client, store, monkeypatch):
        """POST /auth/registration-token/runtime with purpose='binary' mints a
        binary-purpose token."""
        r = client.post("/api/v1/auth/registration-token/runtime", json={
            "name": "claude",
            "purpose": "binary",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["token"].startswith(REGISTRATION_TOKEN_PREFIX)

        # Verify it's a binary-purpose runtime token
        record = store.validate_runtime(body["token"])
        assert record is not None
        assert record.org == _RUNTIME_ORG
        assert record.name == "claude"
        assert record.purpose == "binary"

    def test_mint_runtime_default_purpose_is_profile(self, client, store):
        """Minting without purpose defaults to 'profile' (back-compat)."""
        r = client.post("/api/v1/auth/registration-token/runtime", json={
            "name": "my-executor",
        })
        assert r.status_code == 200
        body = r.json()

        record = store.validate_runtime(body["token"])
        assert record is not None
        assert record.purpose == "profile"

    def test_mint_runtime_rejects_invalid_purpose(self, client, store):
        """Minting with an invalid purpose value returns 422."""
        r = client.post("/api/v1/auth/registration-token/runtime", json={
            "name": "claude",
            "purpose": "invalid",
        })
        assert r.status_code == 422

    def test_mint_runtime_expires_prior_binary_token_same_name(self, client, store):
        """Minting a new binary token for the same name expires the prior one."""
        r1 = client.post("/api/v1/auth/registration-token/runtime", json={
            "name": "claude",
            "purpose": "binary",
        })
        assert r1.status_code == 200
        token1 = r1.json()["token"]

        r2 = client.post("/api/v1/auth/registration-token/runtime", json={
            "name": "claude",
            "purpose": "binary",
        })
        assert r2.status_code == 200
        token2 = r2.json()["token"]

        # token1 should now be consumed (expired by the newer mint)
        assert store.validate_runtime(token1) is None
        assert store.validate_runtime(token2) is not None

    def test_mint_runtime_binary_and_profile_independent(self, client, store):
        """Binary and profile tokens for the same name DON'T expire each other
        (different purposes)."""
        r1 = client.post("/api/v1/auth/registration-token/runtime", json={
            "name": "claude",
            "purpose": "binary",
        })
        assert r1.status_code == 200
        token_bin = r1.json()["token"]

        r2 = client.post("/api/v1/auth/registration-token/runtime", json={
            "name": "claude",
            "purpose": "profile",
        })
        assert r2.status_code == 200
        token_prof = r2.json()["token"]

        # Both should be valid — different purposes
        assert store.validate_runtime(token_bin) is not None
        assert store.validate_runtime(token_prof) is not None

    def test_mint_runtime_rejects_empty_name(self, client, store):
        """Empty name rejected."""
        r = client.post("/api/v1/auth/registration-token/runtime", json={
            "name": "",
            "purpose": "binary",
        })
        assert r.status_code == 422


# ── THR-107 seq352: OpenCode race & error-detail tests ──────────────────


class TestRuntimeRegisterBinaryRaceAndErrors:
    """OpenCode-style race + actionable error regressions (THR-107 seq352)."""

    def _mint_binary_token_and_complete_conformance(
        self, client, store, monkeypatch, kind="claude"
    ):
        """Mint a binary-purpose runtime token and complete all conformance steps."""
        token, _ = store.mint_runtime(kind, purpose="binary")
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

        return token, headers

    def _create_valid_executable(self, tmp_path):
        """Create a valid executable binary for testing."""
        exe = tmp_path / "bin" / "myexecutor"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.touch(mode=0o755)
        return str(exe)

    # ── OpenCode race reproduction ─────────────────────────────────

    def test_register_binary_fails_before_fourth_checkin_all_complete(
        self, client, store, monkeypatch, tmp_path
    ):
        """OpenCode race: register-binary after 3 check-ins fails with 400,
        no write, no consume. Then 4th check-in + register-binary succeeds."""
        token, _ = store.mint_runtime("claude", purpose="binary")
        headers = {"Authorization": f"Bearer {token}"}

        # Create a valid executable
        exe = tmp_path / "bin" / "claude"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.touch(mode=0o755)
        exe_path = str(exe)

        # Complete only the first 3 check-ins (NOT emit_envelope)
        for step_id in ("workspace_access", "loopback_reachable", "cli_callback"):
            r = client.post(
                "/api/v1/executors/runtime/conformance-checkin",
                json={"step_id": step_id},
                headers=headers,
            )
            assert r.status_code == 200

        # Attempt register-binary — must fail (conformance incomplete)
        r = client.post(
            "/api/v1/executors/runtime/register-binary",
            json={"path": exe_path},
            headers=headers,
        )
        assert r.status_code == 400
        detail = r.json()["detail"]
        assert "incomplete" in detail.lower()
        assert "emit_envelope" in detail
        assert "all_complete" in detail

        # Token must NOT be consumed
        assert store.validate_runtime(token) is not None

        # No registry write
        from runtime.orchestrator.executor_binary_registry import load_registry
        registry = load_registry()
        assert "claude" not in registry

        # Now complete the fourth check-in (emit_envelope)
        envelope = {
            "envelope_version": 1,
            "token_usage": {"input_tokens": 1, "output_tokens": 1},
        }
        r4 = client.post(
            "/api/v1/executors/runtime/conformance-checkin",
            json={"step_id": "emit_envelope", "envelope": envelope},
            headers=headers,
        )
        assert r4.status_code == 200
        assert r4.json()["all_complete"] is True

        # Now register-binary succeeds
        r_reg = client.post(
            "/api/v1/executors/runtime/register-binary",
            json={"path": exe_path},
            headers=headers,
        )
        assert r_reg.status_code == 200
        assert r_reg.json()["valid"] is True

    # ── Actionable error detail assertions ─────────────────────────

    def test_conformance_incomplete_error_names_pending_steps(
        self, client, store, tmp_path
    ):
        """Incomplete conformance → 400 naming pending steps + all_complete."""
        token, _ = store.mint_runtime("pi", purpose="binary")
        headers = {"Authorization": f"Bearer {token}"}
        exe = tmp_path / "bin" / "pi"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.touch(mode=0o755)

        # No check-ins at all
        r = client.post(
            "/api/v1/executors/runtime/register-binary",
            json={"path": str(exe)},
            headers=headers,
        )
        assert r.status_code == 400
        detail = r.json()["detail"]
        assert "incomplete" in detail.lower()
        assert "workspace_access" in detail
        assert "all_complete" in detail

    def test_non_absolute_path_error_is_actionable(
        self, client, store, monkeypatch, tmp_path
    ):
        """Non-absolute path → 422 with clear correction guidance."""
        token, _ = store.mint_runtime("pi", purpose="binary")
        headers = {"Authorization": f"Bearer {token}"}
        # Complete all conformance steps
        for step_id in ("workspace_access", "loopback_reachable", "cli_callback"):
            r = client.post(
                "/api/v1/executors/runtime/conformance-checkin",
                json={"step_id": step_id},
                headers=headers,
            )
            assert r.status_code == 200
        env = {"envelope_version": 1, "token_usage": {"input_tokens": 1, "output_tokens": 1}}
        r = client.post(
            "/api/v1/executors/runtime/conformance-checkin",
            json={"step_id": "emit_envelope", "envelope": env},
            headers=headers,
        )
        assert r.status_code == 200

        r = client.post(
            "/api/v1/executors/runtime/register-binary",
            json={"path": "relative/path"},
            headers=headers,
        )
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert "absolute" in detail.lower()

    def test_nonexistent_path_error_is_clear(
        self, client, store, monkeypatch, tmp_path
    ):
        """Non-existent path → 422 telling the candidate the file doesn't exist."""
        token, _ = store.mint_runtime("pi", purpose="binary")
        headers = {"Authorization": f"Bearer {token}"}
        for step_id in ("workspace_access", "loopback_reachable", "cli_callback"):
            r = client.post(
                "/api/v1/executors/runtime/conformance-checkin",
                json={"step_id": step_id},
                headers=headers,
            )
            assert r.status_code == 200
        env = {"envelope_version": 1, "token_usage": {"input_tokens": 1, "output_tokens": 1}}
        r = client.post(
            "/api/v1/executors/runtime/conformance-checkin",
            json={"step_id": "emit_envelope", "envelope": env},
            headers=headers,
        )
        assert r.status_code == 200

        r = client.post(
            "/api/v1/executors/runtime/register-binary",
            json={"path": "/nonexistent/path/to/nowhere"},
            headers=headers,
        )
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert ("not exist" in detail.lower() or
                "does not exist" in detail.lower())

    def test_invalid_token_error_is_401_and_actionable(self, client, tmp_path):
        """Invalid/unknown token → 401 with regenerate guidance (THR-107 seq352)."""
        exe = tmp_path / "bin" / "claude"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.touch(mode=0o755)
        r = client.post(
            "/api/v1/executors/runtime/register-binary",
            json={"path": str(exe)},
            headers={"Authorization": "Bearer hrreg_fake_invalid_token"},
        )
        assert r.status_code == 401
        detail = r.json()["detail"]
        assert "token" in detail.lower()
        assert "regenerate" in detail.lower()

    def test_consumed_token_error_mentions_regenerate(
        self, client, store, monkeypatch, tmp_path
    ):
        """Consumed (already-used) token → 401 with regenerate guidance."""
        exe_path = self._create_valid_executable(tmp_path)
        token, headers = self._mint_binary_token_and_complete_conformance(
            client, store, monkeypatch, kind="claude"
        )
        # First use: succeeds and consumes the token
        r1 = client.post(
            "/api/v1/executors/runtime/register-binary",
            json={"path": exe_path},
            headers=headers,
        )
        assert r1.status_code == 200
        # Token is now consumed
        assert store.validate_runtime(token) is None

        # Second use with consumed token → 401
        r2 = client.post(
            "/api/v1/executors/runtime/register-binary",
            json={"path": exe_path},
            headers=headers,
        )
        assert r2.status_code == 401
        detail = r2.json()["detail"]
        assert "regenerate" in detail.lower()

    def test_master_bearer_error_mentions_regenerate(self, client, monkeypatch, tmp_path):
        """Master bearer token on registration route → 401 with regenerate guidance."""
        exe = tmp_path / "bin" / "claude"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.touch(mode=0o755)
        r = client.post(
            "/api/v1/executors/runtime/register-binary",
            json={"path": str(exe)},
            headers={"Authorization": f"Bearer {paths_mod.read_token()}"},
        )
        assert r.status_code == 401
        detail = r.json()["detail"]
        assert "regenerate" in detail.lower()

    def test_non_hrreg_token_error_mentions_regenerate(self, client, tmp_path):
        """Non-hrreg_ token → 401 with regenerate guidance."""
        exe = tmp_path / "bin" / "claude"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.touch(mode=0o755)
        r = client.post(
            "/api/v1/executors/runtime/register-binary",
            json={"path": str(exe)},
            headers={"Authorization": "Bearer not_a_registration_token_12345"},
        )
        assert r.status_code == 401
        detail = r.json()["detail"]
        assert "regenerate" in detail.lower()

    def test_missing_bearer_error_mentions_regenerate(self, client, tmp_path):
        """No bearer token → 401 with regenerate guidance."""
        exe = tmp_path / "bin" / "claude"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.touch(mode=0o755)
        r = client.post(
            "/api/v1/executors/runtime/register-binary",
            json={"path": str(exe)},
        )
        assert r.status_code == 401
        detail = r.json()["detail"]
        assert "regenerate" in detail.lower()

    def test_reserve_race_token_error_mentions_regenerate(
        self, client, store, monkeypatch, tmp_path
    ):
        """Reserve race: after an external reserve, register-binary → 401
        with regenerate guidance."""
        token, headers = self._mint_binary_token_and_complete_conformance(
            client, store, monkeypatch, kind="claude"
        )
        exe_path = self._create_valid_executable(tmp_path)

        # Simulate a concurrent reservation
        reserved = store.reserve_runtime(token)
        assert reserved is not None, "Reserve must succeed for the race test"

        # Now try register-binary with the externally reserved token
        r = client.post(
            "/api/v1/executors/runtime/register-binary",
            json={"path": exe_path},
            headers=headers,
        )
        assert r.status_code == 401
        detail = r.json()["detail"]
        assert "regenerate" in detail.lower()

        # Token remains reserved (not consumed, not released by our route)
        # Clean up
        store.release_runtime(token)

    # ── Fault injection: set_binary write failure ──────────────────

    def test_set_binary_write_failure_safe_500(
        self, client, store, monkeypatch, tmp_path
    ):
        """set_binary raises Exception → 500 with safe detail, token
        released/not consumed, no durable write, retry succeeds (THR-107 seq352)."""
        token, headers = self._mint_binary_token_and_complete_conformance(
            client, store, monkeypatch, kind="claude"
        )
        exe_path = self._create_valid_executable(tmp_path)

        # Monkeypatch set_binary in the routes module where it's imported
        from runtime.daemon.routes import executors as executors_routes
        original_set_binary = executors_routes.set_binary
        call_count = [0]

        def _failing_set_binary(kind, path):
            call_count[0] += 1
            raise OSError("Simulated disk write failure")

        monkeypatch.setattr(
            executors_routes, "set_binary", _failing_set_binary
        )

        r = client.post(
            "/api/v1/executors/runtime/register-binary",
            json={"path": exe_path},
            headers=headers,
        )
        assert r.status_code == 500
        detail = r.json()["detail"]
        # Safe detail: no raw traceback, no internal paths, no secret
        assert "traceback" not in detail.lower()
        assert "oserror" not in detail.lower()
        assert "simulated" not in detail.lower()
        assert "persist" in detail.lower() or "fail" in detail.lower()

        # Token released (not consumed)
        assert store.validate_runtime(token) is not None

        # No durable write (registry has no "claude" entry)
        from runtime.orchestrator.executor_binary_registry import load_registry
        registry = load_registry()
        assert "claude" not in registry

        # Restore set_binary and retry with same token → succeeds
        monkeypatch.setattr(
            executors_routes, "set_binary", original_set_binary
        )
        r2 = client.post(
            "/api/v1/executors/runtime/register-binary",
            json={"path": exe_path},
            headers=headers,
        )
        assert r2.status_code == 200
        assert r2.json()["valid"] is True

        # Now token IS consumed
        assert store.validate_runtime(token) is None

        # Registry now has the entry
        registry2 = load_registry()
        assert "claude" in registry2

    def test_token_purpose_mismatch_is_403(
        self, client, store, monkeypatch, tmp_path
    ):
        """Profile-purpose token → register-binary returns 403."""
        token, _ = store.mint_runtime("claude", purpose="profile")
        headers = {"Authorization": f"Bearer {token}"}
        exe = tmp_path / "bin" / "claude"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.touch(mode=0o755)

        # Complete conformance for the profile token
        for step_id in ("workspace_access", "loopback_reachable", "cli_callback"):
            r = client.post(
                "/api/v1/executors/runtime/conformance-checkin",
                json={"step_id": step_id},
                headers=headers,
            )
            assert r.status_code == 200
        env = {"envelope_version": 1, "token_usage": {"input_tokens": 1, "output_tokens": 1}}
        r = client.post(
            "/api/v1/executors/runtime/conformance-checkin",
            json={"step_id": "emit_envelope", "envelope": env},
            headers=headers,
        )
        assert r.status_code == 200

        r = client.post(
            "/api/v1/executors/runtime/register-binary",
            json={"path": str(exe)},
            headers=headers,
        )
        assert r.status_code in (401, 403)
        detail = r.json()["detail"]
        if isinstance(detail, dict):
            assert detail.get("code") == "token_purpose_mismatch"

    # ── Shell execution regression: fail-closed script ────────────


# ── Runtime Profile Management Routes (THR-107 S4a) ─────────────────────


def _entry(command: str = "echo", adapter: str = "pi") -> dict:
    return {
        "command": command,
        "argv_template": ["echo", "{prompt}"],
        "adapter": adapter,
    }


class TestRuntimeProfilesListRoute:
    """GET /api/v1/executors/runtime/profiles"""

    def test_list_empty_store(self, client, tmp_home):
        r = client.get("/api/v1/executors/runtime/profiles")
        assert r.status_code == 200
        assert r.json() == {"profiles": []}

    def test_list_populated_sorted_by_name(self, client, tmp_home):
        save_runtime_profile("zeta-exec", _entry(command="zeta-cli"))
        save_runtime_profile("alpha-exec", _entry(command="alpha-cli", adapter="codex"))

        r = client.get("/api/v1/executors/runtime/profiles")
        assert r.status_code == 200
        profiles = r.json()["profiles"]
        assert [p["name"] for p in profiles] == ["alpha-exec", "zeta-exec"]
        alpha = profiles[0]
        assert alpha["command"] == "alpha-cli"
        assert alpha["adapter"] == "codex"
        # Non-resolvable command — honest signal: present false, path null
        assert alpha["present"] is False
        assert alpha["path"] is None

    def test_list_present_path_from_registered_binary(self, client, tmp_home):
        """Custom profiles derive present/path from the machine-local
        binary registry keyed by profile name (THR-107 seq155)."""
        # Register the binary for the profile name
        from runtime.orchestrator.executor_binary_registry import set_binary
        set_binary("true-exec", "/usr/bin/true")
        save_runtime_profile("true-exec", _entry(command="true"))

        r = client.get("/api/v1/executors/runtime/profiles")
        assert r.status_code == 200
        (profile,) = r.json()["profiles"]
        assert profile["name"] == "true-exec"
        assert profile["present"] is True
        assert profile["path"] is not None
        assert profile["path"].startswith("/")

    def test_list_unresolvable_command_present_false(self, client, tmp_home):
        """A custom profile whose declared command does not resolve on PATH
        reports present false, path null."""
        save_runtime_profile("ghost-exec", _entry(command="no-such-cli-xyzzy"))

        r = client.get("/api/v1/executors/runtime/profiles")
        assert r.status_code == 200
        (profile,) = r.json()["profiles"]
        assert profile["name"] == "ghost-exec"
        assert profile["command"] == "no-such-cli-xyzzy"
        assert profile["present"] is False
        assert profile["path"] is None

    def test_list_missing_command_present_false(self, client, tmp_home):
        """A custom profile with no 'command' field in its stored entry
        reports present false, path null (malformed store entry)."""
        # Write a store entry that omits the command field entirely
        from runtime.orchestrator.runtime_executor_store import save_runtime_profile
        save_runtime_profile("no-cmd", {"adapter": "pi"})

        r = client.get("/api/v1/executors/runtime/profiles")
        assert r.status_code == 200
        (profile,) = r.json()["profiles"]
        assert profile["name"] == "no-cmd"
        assert profile["command"] is None
        assert profile["present"] is False
        assert profile["path"] is None

    def test_list_requires_bearer_auth(self, app, tmp_home):
        unauth = TestClient(app)  # no Authorization header
        r = unauth.get("/api/v1/executors/runtime/profiles")
        assert r.status_code == 401


class TestRuntimeProfileDeleteRoute:
    """DELETE /api/v1/executors/runtime/profiles/{name}"""

    def test_delete_unbound_direct_connect_adapter_removes_and_audits_it(self, client, tmp_home):
        """A direct-connect adapter is owned by its removed profile."""
        from runtime.infrastructure.database import Database
        from runtime.orchestrator.adapter_store import AdapterEntry, get_adapter, save_adapter

        adapter = AdapterEntry(
            id="direct-profile-adapter",
            name="direct-profile",
            executable="/tmp/direct-profile-adapter",
            executable_hash="direct-hash",
            version="1.0.0",
            workspace_adapter="codex",
            status="approved",
            registered_at="2026-08-13T00:00:00Z",
            registered_by="direct-connect",
            approved_at="2026-08-13T00:00:00Z",
            approved_by="direct-connect",
            intended_profile_name="direct-profile",
        )
        save_adapter(adapter)
        profile = _entry()
        profile["command_adapter_id"] = "custom-adapter:direct-profile-adapter"
        save_runtime_profile("direct-profile", profile)

        response = client.delete("/api/v1/executors/runtime/profiles/direct-profile")

        assert response.status_code == 200
        assert get_adapter(adapter.id) is None
        audit_db = Database(tmp_home / "runtime-audit.db")
        try:
            rows = audit_db.get_audit_logs("adapter:direct-profile-adapter")
            assert len(rows) == 1
            assert rows[0]["action"] == "adapter_removed"
            assert rows[0]["payload"]["executable_hash"] == "direct-hash"
        finally:
            audit_db.close()

    def test_delete_restores_direct_connect_adapter_when_nested_audit_fails(
        self, client, monkeypatch,
    ):
        """Nested cleanup keeps a restorable adapter if its audit write fails."""
        from runtime.daemon.routes import adapters as adapters_routes
        from runtime.orchestrator.adapter_store import AdapterEntry, get_adapter, save_adapter

        adapter = AdapterEntry(
            id="audit-failure-direct-adapter",
            name="audit-failure-direct",
            executable="/tmp/audit-failure-direct-adapter",
            executable_hash="audit-failure-hash",
            version="1.0.0",
            workspace_adapter="codex",
            status="approved",
            registered_at="2026-08-13T00:00:00Z",
            registered_by="direct-connect",
            approved_at="2026-08-13T00:00:00Z",
            approved_by="direct-connect",
            intended_profile_name="audit-failure-direct",
        )
        original_snapshot = adapter.to_dict()
        save_adapter(adapter)
        profile = _entry()
        profile["command_adapter_id"] = f"custom-adapter:{adapter.id}"
        save_runtime_profile("audit-failure-direct", profile)

        monkeypatch.setattr(
            adapters_routes,
            "_audit_adapter_remove",
            lambda **kwargs: (_ for _ in ()).throw(RuntimeError("injected audit failure")),
        )

        response = client.delete("/api/v1/executors/runtime/profiles/audit-failure-direct")

        # Profile removal is durable before this best-effort nested cleanup,
        # so a 500 would falsely claim the profile still exists.
        assert response.status_code == 200, response.json()
        restored = get_adapter(adapter.id)
        assert restored is not None
        assert restored.to_dict() == original_snapshot

    def test_delete_keeps_manual_submission_adapter(self, client):
        """Manual-submission adapters may be intentionally rebound later."""
        from runtime.orchestrator.adapter_store import AdapterEntry, get_adapter, save_adapter

        adapter = AdapterEntry(
            id="manual-profile-adapter",
            name="manual-profile",
            executable="/tmp/manual-profile-adapter",
            executable_hash="manual-hash",
            version="1.0.0",
            workspace_adapter="codex",
            status="approved",
            registered_at="2026-08-13T00:00:00Z",
            registered_by="adapter-submission:manual-profile",
            approved_at="2026-08-13T00:00:00Z",
            approved_by="founder",
            intended_profile_name="manual-profile",
        )
        save_adapter(adapter)
        profile = _entry()
        profile["command_adapter_id"] = "custom-adapter:manual-profile-adapter"
        save_runtime_profile("manual-profile", profile)

        response = client.delete("/api/v1/executors/runtime/profiles/manual-profile")

        assert response.status_code == 200
        assert get_adapter(adapter.id) is not None

    def test_delete_keeps_direct_connect_adapter_bound_by_another_profile(self, client):
        from runtime.orchestrator.adapter_store import AdapterEntry, get_adapter, save_adapter

        adapter = AdapterEntry(
            id="shared-direct-adapter",
            name="first-profile",
            executable="/tmp/shared-direct-adapter",
            executable_hash="shared-hash",
            version="1.0.0",
            workspace_adapter="codex",
            status="approved",
            registered_at="2026-08-13T00:00:00Z",
            registered_by="direct-connect",
            approved_at="2026-08-13T00:00:00Z",
            approved_by="direct-connect",
            intended_profile_name="first-profile",
        )
        save_adapter(adapter)
        for name in ("first-profile", "second-profile"):
            profile = _entry()
            profile["command_adapter_id"] = "custom-adapter:shared-direct-adapter"
            save_runtime_profile(name, profile)

        response = client.delete("/api/v1/executors/runtime/profiles/first-profile")

        assert response.status_code == 200
        assert get_adapter(adapter.id) is not None

    def test_delete_present_removes_from_store(self, client, tmp_home):
        save_runtime_profile("doomed", _entry())

        r = client.delete("/api/v1/executors/runtime/profiles/doomed")
        assert r.status_code == 200
        assert r.json() == {"name": "doomed", "removed": True}
        assert "doomed" not in load_runtime_profiles()

        # List no longer shows it
        r = client.get("/api/v1/executors/runtime/profiles")
        assert r.json() == {"profiles": []}

        # Second delete → 404
        r = client.delete("/api/v1/executors/runtime/profiles/doomed")
        assert r.status_code == 404

    def test_delete_absent_404(self, client, tmp_home):
        r = client.delete("/api/v1/executors/runtime/profiles/never-existed")
        assert r.status_code == 404

    def test_delete_clears_durable_store_and_in_memory_registry(self, client, tmp_home):
        """THR-107 S4a symmetry: the durable store is the source of truth
        and register publishes to BOTH surfaces — DELETE must clear both,
        or the removed profile lingers in-process until restart."""
        from runtime.orchestrator.executor_registry import ExecutorProfile

        save_runtime_profile("both-exec", _entry())
        registry = get_registry()
        registry.register_custom_profile(ExecutorProfile(
            name="both-exec",
            kind="custom",
            adapter_id="pi",
            readiness_marker_fragment="AGENTS.md",
            argv_template=["echo", "{prompt}"],
            command="echo",
        ))
        assert registry.is_registered("both-exec")

        r = client.delete("/api/v1/executors/runtime/profiles/both-exec")
        assert r.status_code == 200

        assert "both-exec" not in load_runtime_profiles()
        assert not registry.is_registered("both-exec")
        assert registry.get_profile("both-exec") is None

    def test_delete_builtin_name_rejected(self, client, tmp_home):
        """Pathological hand-edited store carrying a built-in name: the
        route refuses (422) and never unregisters the built-in."""
        save_runtime_profile("claude", _entry())

        r = client.delete("/api/v1/executors/runtime/profiles/claude")
        assert r.status_code == 422

        assert get_registry().is_registered("claude")
        assert get_registry().get_profile("claude").kind == "builtin"

    def test_delete_writes_audit_row(self, client, tmp_home):
        """Removal audits to runtime-audit.db mirroring the registration
        row shape: task_id='executor:<name>', payload {command,
        argv_template, adapter}; action is 'executor_removed'."""
        from runtime.infrastructure.database import Database

        save_runtime_profile("audited-exec", _entry(command="audit-cli"))

        r = client.delete("/api/v1/executors/runtime/profiles/audited-exec")
        assert r.status_code == 200

        audit_db_path = tmp_home / "runtime-audit.db"
        assert audit_db_path.exists()
        audit_db = Database(audit_db_path)
        try:
            rows = audit_db.get_audit_logs("executor:audited-exec")
            assert len(rows) == 1
            row = rows[0]
            assert row["task_id"] == "executor:audited-exec"
            assert row["action"] == "executor_removed"
            assert row["agent"] == "founder"
            payload = row["payload"]
            assert payload["command"] == "audit-cli"
            assert payload["argv_template"] == ["echo", "{prompt}"]
            assert payload["adapter"] == "pi"
        finally:
            audit_db.close()

    def test_delete_404_writes_no_audit_row(self, client, tmp_home):
        r = client.delete("/api/v1/executors/runtime/profiles/ghost")
        assert r.status_code == 404
        audit_db_path = tmp_home / "runtime-audit.db"
        if audit_db_path.exists():
            from runtime.infrastructure.database import Database
            audit_db = Database(audit_db_path)
            try:
                assert audit_db.get_audit_logs("executor:ghost") == []
            finally:
                audit_db.close()

    def test_delete_requires_bearer_auth(self, app, tmp_home):
        unauth = TestClient(app)  # no Authorization header
        r = unauth.delete("/api/v1/executors/runtime/profiles/anything")
        assert r.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
# THR-107 D9 / Phase 3: command_adapter adversarial runtime route tests
# ═══════════════════════════════════════════════════════════════════════════


class TestRuntimeCommandAdapterRejection:
    """Runtime register route: adversarial command_adapter rejection.

    Proves that unsupported command_adapter values reject BEFORE any
    durable runtime-store mutation, in-memory registry mutation, audit-row
    residue, or token-reservation/consumption residue.
    """

    def _mint_and_complete(self, client, store):
        """Helper: mint a runtime token and complete all conformance steps."""
        token, _ = store.mint_runtime("cmd-adapter-test")
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

        return token, headers

    def test_rejects_unsupported_command_adapter_no_store_no_audit(
        self, client, store, monkeypatch, tmp_path,
    ):
        """Unsupported command_adapter → 422, no runtime-store write,
        no in-memory registry mutation, no audit row, token NOT consumed."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        from runtime.daemon import paths as paths_mod
        paths_mod.ensure_daemon_home()
        paths_mod.ensure_token()

        reset_registry()
        token, headers = self._mint_and_complete(client, store)

        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "adapter": "pi",
                "command_adapter": "custom-adapter-v2",
            },
            headers=headers,
        )
        assert r.status_code == 422, r.json()
        assert "command_adapter" in r.json()["detail"] or "generic-cli" in r.json()["detail"]

        # No runtime-store write
        from runtime.orchestrator.runtime_executor_store import load_runtime_profiles
        profiles = load_runtime_profiles()
        assert "cmd-adapter-test" not in profiles, (
            "Rejected registration must not write to runtime store"
        )

        # No in-memory registry mutation
        assert not get_registry().is_registered("cmd-adapter-test"), (
            "Rejected registration must not mutate in-memory registry"
        )

        # Token NOT consumed
        assert store.validate_runtime(token) is not None, (
            "Token must be valid (released) after failed registration"
        )

        # No audit row residue
        audit_db_path = tmp_path / "runtime-audit.db"
        if audit_db_path.exists():
            from runtime.infrastructure.database import Database
            audit_db = Database(audit_db_path)
            try:
                rows = audit_db.get_audit_logs("executor:cmd-adapter-test")
                assert len(rows) == 0, (
                    f"Rejected registration must not produce audit rows, got {len(rows)}"
                )
            finally:
                audit_db.close()

    def test_rejects_non_string_command_adapter_no_residue(
        self, client, store, monkeypatch, tmp_path,
    ):
        """Non-string command_adapter (int 42) → 422, no residue."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        from runtime.daemon import paths as paths_mod
        paths_mod.ensure_daemon_home()
        paths_mod.ensure_token()

        reset_registry()
        token, headers = self._mint_and_complete(client, store)

        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "adapter": "pi",
                "command_adapter": 42,
            },
            headers=headers,
        )
        assert r.status_code == 422, r.json()
        # Pydantic validation error detail is a list of dicts (not a string)
        detail = r.json()["detail"]
        if isinstance(detail, list):
            detail_str = str(detail).lower()
        else:
            detail_str = detail.lower()
        assert "command_adapter" in detail_str or "string" in detail_str

        from runtime.orchestrator.runtime_executor_store import load_runtime_profiles
        assert "cmd-adapter-test" not in load_runtime_profiles()
        assert not get_registry().is_registered("cmd-adapter-test")
        assert store.validate_runtime(token) is not None

        # No audit row residue
        audit_db_path = tmp_path / "runtime-audit.db"
        if audit_db_path.exists():
            from runtime.infrastructure.database import Database
            audit_db = Database(audit_db_path)
            try:
                rows = audit_db.get_audit_logs("executor:cmd-adapter-test")
                assert len(rows) == 0, (
                    f"Rejected non-string registration must not produce audit rows, got {len(rows)}"
                )
            finally:
                audit_db.close()

    def test_explicit_generic_cli_round_trips_through_runtime_register_to_list(
        self, client, store, monkeypatch, tmp_path,
    ):
        """Explicit command_adapter='generic-cli' round-trips through
        runtime register → durable store → list endpoint.
        Assert returned command_adapter is 'generic-cli' and adapter is
        independent."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        from runtime.daemon import paths as paths_mod
        paths_mod.ensure_daemon_home()
        paths_mod.ensure_token()

        reset_registry()
        token, headers = self._mint_and_complete(client, store)

        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "adapter": "pi",
                "command_adapter": "generic-cli",
            },
            headers=headers,
        )
        assert r.status_code == 200, r.json()
        body = r.json()
        assert body["name"] == "cmd-adapter-test"
        assert body["command_adapter"] == "generic-cli"
        assert body["adapter_id"] == "pi"  # workspace adapter independent

        # Durable store holds profile with command_adapter
        from runtime.orchestrator.runtime_executor_store import load_runtime_profiles
        profiles = load_runtime_profiles()
        assert "cmd-adapter-test" in profiles
        assert profiles["cmd-adapter-test"]["command_adapter"] == "generic-cli"
        assert profiles["cmd-adapter-test"]["adapter"] == "pi"

        # List endpoint returns command_adapter.
        # The registration token is consumed by the successful register,
        # so we must use the master bearer for the list management route.
        r2 = client.get(
            "/api/v1/executors/runtime/profiles",
            headers={"Authorization": f"Bearer {paths_mod.read_token()}"},
        )
        assert r2.status_code == 200, r2.json()
        list_body = r2.json()
        listed = [p for p in list_body["profiles"] if p["name"] == "cmd-adapter-test"]
        assert len(listed) == 1
        assert listed[0]["command_adapter"] == "generic-cli"
        assert listed[0]["adapter"] == "pi"

        # Token consumed
        assert store.validate_runtime(token) is None

    def test_null_command_adapter_accepted_defaults_to_generic_cli(
        self, client, store, monkeypatch, tmp_path,
    ):
        """Null command_adapter in body → accepted, defaults to
        'generic-cli' in response."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        from runtime.daemon import paths as paths_mod
        paths_mod.ensure_daemon_home()
        paths_mod.ensure_token()

        reset_registry()
        token, headers = self._mint_and_complete(client, store)

        r = client.post(
            "/api/v1/executors/runtime/register",
            json={
                "command": "echo",
                "argv_template": ["echo", "{prompt}"],
                "adapter": "claude",
                "command_adapter": None,
            },
            headers=headers,
        )
        assert r.status_code == 200, r.json()
        body = r.json()
        assert body["command_adapter"] == "generic-cli"
        assert body["adapter_id"] == "claude"  # workspace adapter independent

        from runtime.orchestrator.runtime_executor_store import load_runtime_profiles
        profiles = load_runtime_profiles()
        assert "cmd-adapter-test" in profiles
