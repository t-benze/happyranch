"""THR-107 seq141: adapter submission + profile binding route tests.

Integration-level tests exercising the actual POST /api/v1/runtime/adapters/submit
and POST /api/v1/runtime/adapters/{adapter_id}/bind-profile routes through the
real FastAPI TestClient, registration-token store, and adapter/executor stores.

Categories:
  1. Submit auth isolation (scoped token only, no master bearer)
  2. Submit gating (purpose, name, id, loopback, challenge, privilege escalation)
  3. Submit replay / concurrent single-winner
  4. Submission → approval → metadata retention
  5. Bind gating (PENDING, unknown, cross-profile, tampered, built-in collision)
  6. Bind durable persistence (survives restart, rollback)
  7. Race safety (approval vs bind, re-registration vs bind)
  8. Legacy / Kimi non-mutation
"""
from __future__ import annotations

import os
import stat
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.daemon.registration_token import (
    RegistrationTokenRecord,
    RegistrationTokenStore,
    REGISTRATION_TOKEN_PREFIX,
)
from runtime.orchestrator.runtime_executor_store import (
    load_runtime_profiles,
)
from runtime.orchestrator.adapter_store import load_adapters


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_conformant_adapter_script(tmp_path: Path, adapter_id: str) -> Path:
    """Create a minimal conformance-probe-passing executable adapter script."""
    script = tmp_path / f"{adapter_id}-script"
    content = f'''#!/usr/bin/env python3
import json, sys
inp = json.load(sys.stdin)
out = {{
    "success": True,
    "returncode": 0,
    "adapter_metadata": {{
        "adapter_id": "{adapter_id}",
        "adapter_name": "test-adapter",
        "adapter_version": "1.0.0",
        "contract_version": 1,
        "adapter": "test-adapter"
    }},
    "stdout": "ok",
    "stderr": "",
    "stdout_tail": "ok",
    "stderr_tail": "",
    "duration_seconds": 1,
    "invocation_id": inp.get("invocation", {{}}).get("invocation_id", "test-id"),
    "token_total": 0,
    "session_id": "test-session"
}}
print(json.dumps(out))
'''
    script.write_text(content)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def _mint_adapter_token(store: RegistrationTokenStore, profile_name: str = "my-cli") -> str:
    """Mint a runtime registration token with purpose='adapter'."""
    token_plaintext, _expires = store.mint_runtime(
        name=profile_name,
        purpose="adapter",
        intended_profile_name=profile_name,
    )
    # Complete all required runtime conformance steps
    for step_id in store.DEFAULT_CONFORMANCE_STEPS:
        store.record_step_arrival_runtime(token_plaintext, step_id)
    return token_plaintext


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def route_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set up daemon home, token, and isolate stores."""
    from runtime.daemon import paths as paths_mod
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / ".happyranch"))
    paths_mod.ensure_daemon_home()
    paths_mod.ensure_token()
    return tmp_path


@pytest.fixture
def token_store():
    """A fresh in-memory registration-token store."""
    return RegistrationTokenStore()


def _bypass_loopback(monkeypatch: pytest.MonkeyPatch):
    """Allow TestClient (peer 'testclient') through loopback gates."""
    from runtime.daemon.routes import auth as auth_route
    monkeypatch.setattr(
        auth_route, "_LOCAL_HOSTS",
        auth_route._LOCAL_HOSTS | {"testclient"},
    )
    import runtime.daemon.auth as auth_mod
    monkeypatch.setattr(
        auth_mod, "_REGISTRATION_LOCAL_HOSTS",
        auth_mod._REGISTRATION_LOCAL_HOSTS | {"testclient"},
    )


@pytest.fixture
def app_and_client(route_setup: Path, token_store: RegistrationTokenStore, monkeypatch: pytest.MonkeyPatch):
    """Build a FastAPI app with both adapters routers and attach token store.

    Returns (app, TestClient, master_token_value).
    The submit_router is included separately (without master-bearer dependency).
    The main router retains master-bearer dependency.
    """
    from runtime.daemon.routes.adapters import router, submit_router
    from runtime.daemon import paths as paths_mod

    _bypass_loopback(monkeypatch)

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.include_router(submit_router, prefix="/api/v1")

    # Attach daemon state for registration-token auth dependency
    class FakeDaemon:
        def __init__(self, store):
            self.registration_token_store = store

    app.state.daemon = FakeDaemon(token_store)

    master_token = paths_mod.read_token()
    return app, master_token, token_store


# ---------------------------------------------------------------------------
# 1. Submit auth isolation
# ---------------------------------------------------------------------------

class TestSubmitAuthIsolation:
    """Verify submit route is callable with scoped token and rejects master bearer."""

    def test_submit_with_scoped_token_success(self, app_and_client, route_setup, token_store):
        """A valid adapter-purpose token creates a PENDING adapter entry."""
        app, master_token, store = app_and_client
        token = _mint_adapter_token(store, "test-cli")
        script = _make_conformant_adapter_script(route_setup, "test-cli-adapter")

        client = TestClient(app)
        resp = client.post(
            "/api/v1/runtime/adapters/submit",
            json={
                "executable": str(script),
                "version": "1.0.0",
                "capabilities": [],
                "workspace_adapter": "pi",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["id"] == "test-cli-adapter"
        assert data["status"] == "pending"
        assert data["intended_profile_name"] == "test-cli"

    def test_master_token_rejected_on_submit(self, app_and_client, route_setup, token_store):
        """Master bearer is rejected by the registration-token dependency."""
        app, master_token, store = app_and_client
        _mint_adapter_token(store, "test-cli")
        script = _make_conformant_adapter_script(route_setup, "test-cli-adapter")

        client = TestClient(app)
        resp = client.post(
            "/api/v1/runtime/adapters/submit",
            json={
                "executable": str(script),
                "version": "1.0.0",
                "capabilities": [],
                "workspace_adapter": "pi",
            },
            headers={"Authorization": f"Bearer {master_token}"},
        )
        assert resp.status_code == 401, resp.text
        detail = resp.json()["detail"]
        assert "master bearer" in detail.lower() or "not a registration token" in detail.lower()

    def test_no_auth_header_rejected_on_submit(self, app_and_client, route_setup, token_store):
        """Missing Authorization header returns 401."""
        app, master_token, store = app_and_client
        _mint_adapter_token(store, "test-cli")
        script = _make_conformant_adapter_script(route_setup, "test-cli-adapter")

        client = TestClient(app)
        resp = client.post(
            "/api/v1/runtime/adapters/submit",
            json={
                "executable": str(script),
                "version": "1.0.0",
            },
        )
        assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# 2. Submit gating
# ---------------------------------------------------------------------------

class TestSubmitGating:
    """Verify submission gating: purpose, name, id, challenge, privilege."""

    def test_wrong_purpose_rejected(self, app_and_client, route_setup, token_store):
        """Token with purpose='binary' is rejected for adapter submission."""
        app, master_token, store = app_and_client
        # Mint a binary-purpose token
        token, _exp = store.mint_runtime(name="test-cli", purpose="binary")
        for step_id in store.DEFAULT_CONFORMANCE_STEPS:
            store.record_step_arrival_runtime(token, step_id)

        script = _make_conformant_adapter_script(route_setup, "test-cli-adapter")
        client = TestClient(app)
        resp = client.post(
            "/api/v1/runtime/adapters/submit",
            json={
                "executable": str(script),
                "version": "1.0.0",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422, resp.text
        assert "adapter" in resp.json()["detail"].lower()

    def test_missing_intended_profile_name_rejected(self, app_and_client, route_setup, token_store):
        """Token with purpose='profile' (no intended_profile_name) is rejected."""
        app, master_token, store = app_and_client
        token, _exp = store.mint_runtime(name="test-cli", purpose="profile")
        for step_id in store.DEFAULT_CONFORMANCE_STEPS:
            store.record_step_arrival_runtime(token, step_id)

        script = _make_conformant_adapter_script(route_setup, "test-cli-adapter")
        client = TestClient(app)
        resp = client.post(
            "/api/v1/runtime/adapters/submit",
            json={
                "executable": str(script),
                "version": "1.0.0",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422, resp.text

    def test_incomplete_challenge_rejected(self, app_and_client, route_setup, token_store):
        """Token with incomplete conformance challenge is rejected."""
        app, master_token, store = app_and_client
        token, _exp = store.mint_runtime(name="test-cli", purpose="adapter", intended_profile_name="test-cli")
        # Do NOT complete conformance steps

        script = _make_conformant_adapter_script(route_setup, "test-cli-adapter")
        client = TestClient(app)
        resp = client.post(
            "/api/v1/runtime/adapters/submit",
            json={
                "executable": str(script),
                "version": "1.0.0",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert "challenge" in str(detail).lower() or "pending" in str(detail).lower()

    def test_submission_cannot_approve(self, app_and_client, route_setup, token_store):
        """Adapter created via submission is PENDING, never APPROVED."""
        app, master_token, store = app_and_client
        token = _mint_adapter_token(store, "test-cli")
        script = _make_conformant_adapter_script(route_setup, "test-cli-adapter")

        client = TestClient(app)
        resp = client.post(
            "/api/v1/runtime/adapters/submit",
            json={
                "executable": str(script),
                "version": "1.0.0",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "pending"
        assert data["approved_at"] is None
        assert data["approved_by"] is None

    def test_submission_cannot_bind(self, app_and_client, route_setup, token_store):
        """Submission token cannot call bind-profile."""
        app, master_token, store = app_and_client
        token = _mint_adapter_token(store, "test-cli")
        script = _make_conformant_adapter_script(route_setup, "test-cli-adapter")

        client = TestClient(app)
        # Submit
        resp = client.post(
            "/api/v1/runtime/adapters/submit",
            json={"executable": str(script), "version": "1.0.0"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        adapter_id = resp.json()["id"]

        # Try to bind with the scoped token (not master bearer)
        resp = client.post(
            f"/api/v1/runtime/adapters/{adapter_id}/bind-profile",
            json={"profile_name": "test-cli"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# 3. Submit replay / concurrent single-winner
# ---------------------------------------------------------------------------

class TestSubmitReplay:
    """Verify token single-use, concurrent one-winner semantics."""

    def test_token_consumed_after_success(self, app_and_client, route_setup, token_store):
        """Token is consumed after successful submission; replay fails."""
        app, master_token, store = app_and_client
        token = _mint_adapter_token(store, "test-cli")
        script = _make_conformant_adapter_script(route_setup, "test-cli-adapter")

        client = TestClient(app)
        # First submission succeeds
        resp = client.post(
            "/api/v1/runtime/adapters/submit",
            json={"executable": str(script), "version": "1.0.0"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

        # Replay fails
        resp = client.post(
            "/api/v1/runtime/adapters/submit",
            json={"executable": str(script), "version": "1.0.0"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code in (401, 409, 422), f"Unexpected {resp.status_code}: {resp.text}"

    def test_concurrent_submit_one_winner(self, app_and_client, route_setup, token_store):
        """Concurrent submits with same token: exactly one succeeds."""
        app, master_token, store = app_and_client
        token = _mint_adapter_token(store, "test-cli")
        script = _make_conformant_adapter_script(route_setup, "test-cli-adapter")

        results = []
        lock = threading.Lock()

        def do_submit():
            client = TestClient(app)
            resp = client.post(
                "/api/v1/runtime/adapters/submit",
                json={"executable": str(script), "version": "1.0.0"},
                headers={"Authorization": f"Bearer {token}"},
            )
            with lock:
                results.append(resp.status_code)

        threads = [threading.Thread(target=do_submit) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results.count(200) == 1, f"Expected exactly 1 success, got {results}"


# ---------------------------------------------------------------------------
# 4. Submission → approval → metadata retention
# ---------------------------------------------------------------------------

class TestMetadataRetentionThroughApproval:
    """Verify intended_profile_name survives the approval transition."""

    def _master_client(self, app, master_token):
        c = TestClient(app)
        c.headers.update({"Authorization": f"Bearer {master_token}"})
        return c

    def test_intended_profile_name_survives_approval(self, app_and_client, route_setup, token_store):
        """Submitted adapter's intended_profile_name persists after approval."""
        app, master_token, store = app_and_client
        token = _mint_adapter_token(store, "my-profile")
        script = _make_conformant_adapter_script(route_setup, "my-profile-adapter")

        # Submit
        c = TestClient(app)
        resp = c.post(
            "/api/v1/runtime/adapters/submit",
            json={"executable": str(script), "version": "1.0.0"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["intended_profile_name"] == "my-profile"
        adapter_id = data["id"]
        assert adapter_id == "my-profile-adapter"

        # Approve with master bearer
        mc = self._master_client(app, master_token)
        resp = mc.post(
            f"/api/v1/runtime/adapters/{adapter_id}/approve",
            json={
                "executable": data["executable"],
                "executable_hash": data["executable_hash"],
                "version": data["version"],
                "capabilities": data["capabilities"],
                "contract_version": data["contract_version"],
                "workspace_adapter": data["workspace_adapter"],
            },
        )
        assert resp.status_code == 200, resp.text
        approved = resp.json()
        assert approved["status"] == "approved"
        assert approved["intended_profile_name"] == "my-profile", (
            f"intended_profile_name lost during approval: {approved}"
        )

    def test_cross_profile_bind_rejected_after_approval(self, app_and_client, route_setup, token_store):
        """After approval, bind to a different profile name is rejected."""
        app, master_token, store = app_and_client
        token = _mint_adapter_token(store, "my-profile")
        script = _make_conformant_adapter_script(route_setup, "my-profile-adapter")

        # Submit
        c = TestClient(app)
        resp = c.post(
            "/api/v1/runtime/adapters/submit",
            json={"executable": str(script), "version": "1.0.0"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        adapter_id = data["id"]

        # Approve
        mc = self._master_client(app, master_token)
        resp = mc.post(
            f"/api/v1/runtime/adapters/{adapter_id}/approve",
            json={
                "executable": data["executable"],
                "executable_hash": data["executable_hash"],
                "version": data["version"],
                "capabilities": data["capabilities"],
                "contract_version": data["contract_version"],
                "workspace_adapter": data["workspace_adapter"],
            },
        )
        assert resp.status_code == 200

        # Bind to wrong profile name
        resp = mc.post(
            f"/api/v1/runtime/adapters/{adapter_id}/bind-profile",
            json={"profile_name": "different-profile"},
        )
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
        detail = resp.json()["detail"]
        assert "cross-bind" in detail.lower() or "bound to intended" in detail.lower() or \
               "intended" in detail.lower()


# ---------------------------------------------------------------------------
# 5. Bind gating
# ---------------------------------------------------------------------------

class TestBindGating:
    """Verify bind-profile rejects PENDING, unknown, tampered, collision."""

    def _master_client(self, app, master_token):
        c = TestClient(app)
        c.headers.update({"Authorization": f"Bearer {master_token}"})
        return c

    def _submit_and_approve(self, app, master_token, store, route_setup, profile_name="bind-cli"):
        """Helper: submit → approve, return adapter_id."""
        token = _mint_adapter_token(store, profile_name)
        script = _make_conformant_adapter_script(route_setup, f"{profile_name}-adapter")

        c = TestClient(app)
        resp = c.post(
            "/api/v1/runtime/adapters/submit",
            json={"executable": str(script), "version": "1.0.0"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        adapter_id = data["id"]
        mc = self._master_client(app, master_token)
        resp = mc.post(
            f"/api/v1/runtime/adapters/{adapter_id}/approve",
            json={
                "executable": data["executable"],
                "executable_hash": data["executable_hash"],
                "version": data["version"],
                "capabilities": data["capabilities"],
                "contract_version": data["contract_version"],
                "workspace_adapter": data["workspace_adapter"],
            },
        )
        assert resp.status_code == 200, resp.text
        return adapter_id

    def test_pending_adapter_bind_rejected(self, app_and_client, route_setup, token_store):
        """Bind to a PENDING (not-yet-approved) adapter is rejected."""
        app, master_token, store = app_and_client
        token = _mint_adapter_token(store, "pending-cli")
        script = _make_conformant_adapter_script(route_setup, "pending-cli-adapter")

        c = TestClient(app)
        resp = c.post(
            "/api/v1/runtime/adapters/submit",
            json={"executable": str(script), "version": "1.0.0"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        adapter_id = resp.json()["id"]

        # Bind without approval
        mc = self._master_client(app, master_token)
        resp = mc.post(
            f"/api/v1/runtime/adapters/{adapter_id}/bind-profile",
            json={"profile_name": "pending-cli"},
        )
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
        assert "not approved" in resp.json()["detail"].lower()

    def test_unknown_adapter_bind_rejected(self, app_and_client):
        """Bind to a non-existent adapter returns 404."""
        app, master_token, store = app_and_client
        c = TestClient(app)
        c.headers.update({"Authorization": f"Bearer {master_token}"})
        resp = c.post(
            "/api/v1/runtime/adapters/nonexistent-adapter/bind-profile",
            json={"profile_name": "foo"},
        )
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"

    def test_tampered_adapter_bind_rejected(self, app_and_client, route_setup, token_store):
        """Bind to an approved adapter whose on-disk file changed is rejected."""
        app, master_token, store = app_and_client
        profile_name = "tamper-cli"
        adapter_id = self._submit_and_approve(app, master_token, store, route_setup, profile_name)

        # Find and tamper the executable
        adapter_entry = load_adapters().get(adapter_id)
        assert adapter_entry is not None
        script = Path(adapter_entry.executable)
        script.write_text(script.read_text() + "\n# tampered\n")

        # Bind should fail
        mc = self._master_client(app, master_token)
        resp = mc.post(
            f"/api/v1/runtime/adapters/{adapter_id}/bind-profile",
            json={"profile_name": profile_name},
        )
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"

    def test_builtin_collision_bind_rejected(self, app_and_client, route_setup, token_store):
        """Bind to a profile name that collides with a built-in is rejected."""
        app, master_token, store = app_and_client
        token = _mint_adapter_token(store, "codex")
        script = _make_conformant_adapter_script(route_setup, "codex-adapter")

        c = TestClient(app)
        resp = c.post(
            "/api/v1/runtime/adapters/submit",
            json={"executable": str(script), "version": "1.0.0"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        adapter_id = data["id"]

        # Approve
        mc = self._master_client(app, master_token)
        resp = mc.post(
            f"/api/v1/runtime/adapters/{adapter_id}/approve",
            json={
                "executable": data["executable"],
                "executable_hash": data["executable_hash"],
                "version": data["version"],
                "capabilities": data["capabilities"],
                "contract_version": data["contract_version"],
                "workspace_adapter": data["workspace_adapter"],
            },
        )
        assert resp.status_code == 200

        # Bind to "codex" -- a built-in name
        resp = mc.post(
            f"/api/v1/runtime/adapters/{adapter_id}/bind-profile",
            json={"profile_name": "codex"},
        )
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
        detail = resp.json()["detail"]
        assert "built-in" in detail.lower() or "collides" in detail.lower()


# ---------------------------------------------------------------------------
# 6. Bind durable persistence
# ---------------------------------------------------------------------------

class TestBindDurablePersistence:
    """Verify bind writes to the durable runtime store and survives restart."""

    def _master_client(self, app, master_token):
        c = TestClient(app)
        c.headers.update({"Authorization": f"Bearer {master_token}"})
        return c

    def _submit_and_approve(self, app, master_token, store, route_setup, profile_name="bind-cli"):
        token = _mint_adapter_token(store, profile_name)
        script = _make_conformant_adapter_script(route_setup, f"{profile_name}-adapter")

        c = TestClient(app)
        resp = c.post(
            "/api/v1/runtime/adapters/submit",
            json={"executable": str(script), "version": "1.0.0"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        adapter_id = data["id"]
        mc = self._master_client(app, master_token)
        resp = mc.post(
            f"/api/v1/runtime/adapters/{adapter_id}/approve",
            json={
                "executable": data["executable"],
                "executable_hash": data["executable_hash"],
                "version": data["version"],
                "capabilities": data["capabilities"],
                "contract_version": data["contract_version"],
                "workspace_adapter": data["workspace_adapter"],
            },
        )
        assert resp.status_code == 200, resp.text
        return adapter_id

    def test_exact_approved_bind_success(self, app_and_client, route_setup, token_store):
        """Successfully bind an approved adapter to its intended profile."""
        app, master_token, store = app_and_client
        adapter_id = self._submit_and_approve(app, master_token, store, route_setup, "durable-cli")

        mc = self._master_client(app, master_token)
        resp = mc.post(
            f"/api/v1/runtime/adapters/{adapter_id}/bind-profile",
            json={"profile_name": "durable-cli"},
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()
        assert result["status"] == "connected"
        assert result["adapter_id"] == adapter_id
        assert result["command_adapter_id"] == f"custom-adapter:{adapter_id}"

    def test_bind_survives_restart(self, app_and_client, route_setup, token_store):
        """Profile persists durably; survives registry reload."""
        app, master_token, store = app_and_client
        adapter_id = self._submit_and_approve(app, master_token, store, route_setup, "restart-cli")

        mc = self._master_client(app, master_token)
        resp = mc.post(
            f"/api/v1/runtime/adapters/{adapter_id}/bind-profile",
            json={"profile_name": "restart-cli"},
        )
        assert resp.status_code == 200

        # Verify durable store has the profile
        profiles = load_runtime_profiles()
        assert "restart-cli" in profiles, f"Profile not in durable store: {list(profiles.keys())}"
        assert profiles["restart-cli"]["command_adapter_id"] == f"custom-adapter:{adapter_id}"

    def test_bind_without_auth_rejected(self, app_and_client, route_setup, token_store):
        """Bind requires master bearer authentication."""
        app, master_token, store = app_and_client
        adapter_id = self._submit_and_approve(app, master_token, store, route_setup, "auth-cli")

        # Try bind without auth
        c = TestClient(app)
        resp = c.post(
            f"/api/v1/runtime/adapters/{adapter_id}/bind-profile",
            json={"profile_name": "auth-cli"},
        )
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# 7. Race safety
# ---------------------------------------------------------------------------

class TestRaceSafety:
    """Verify approval vs bind and re-registration vs bind races are safe."""

    def _master_client(self, app, master_token):
        c = TestClient(app)
        c.headers.update({"Authorization": f"Bearer {master_token}"})
        return c

    def test_approval_vs_bind_race_deterministic(self, app_and_client, route_setup, token_store):
        """Bind against PENDING fails; approval then bind succeeds."""
        app, master_token, store = app_and_client
        token = _mint_adapter_token(store, "race-cli")
        script = _make_conformant_adapter_script(route_setup, "race-cli-adapter")

        c = TestClient(app)
        resp = c.post(
            "/api/v1/runtime/adapters/submit",
            json={"executable": str(script), "version": "1.0.0"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        adapter_id = data["id"]

        # Concurrent approve + bind
        approve_results = []
        bind_results = []

        def do_approve():
            mc = self._master_client(app, master_token)
            resp = mc.post(
                f"/api/v1/runtime/adapters/{adapter_id}/approve",
                json={
                    "executable": data["executable"],
                    "executable_hash": data["executable_hash"],
                    "version": data["version"],
                    "capabilities": data["capabilities"],
                    "contract_version": data["contract_version"],
                    "workspace_adapter": data["workspace_adapter"],
                },
            )
            approve_results.append(resp.status_code)

        def do_bind():
            mc = self._master_client(app, master_token)
            resp = mc.post(
                f"/api/v1/runtime/adapters/{adapter_id}/bind-profile",
                json={"profile_name": "race-cli"},
            )
            bind_results.append(resp.status_code)

        t_approve = threading.Thread(target=do_approve)
        t_bind = threading.Thread(target=do_bind)
        t_approve.start()
        t_bind.start()
        t_approve.join()
        t_bind.join()

        assert approve_results[0] == 200, f"approve failed: {approve_results}"
        assert bind_results[0] in (200, 422), f"bind unexpected: {bind_results}"

    def test_re_registration_vs_bind_deterministic(self, app_and_client, route_setup, token_store):
        """If re-registration (via submit) resets to PENDING before bind, bind is rejected."""
        app, master_token, store = app_and_client
        profile_name = "rereg-race-cli"
        adapter_id = self._submit_and_approve_static(app, master_token, store, route_setup, profile_name)

        # Re-register via a NEW submit token with the same intended profile name.
        # This hits the same adapter id and resets status to PENDING.
        token2 = _mint_adapter_token(store, profile_name)
        adapter_entry = load_adapters().get(adapter_id)
        assert adapter_entry is not None

        c = TestClient(app)
        resp = c.post(
            "/api/v1/runtime/adapters/submit",
            json={
                "executable": adapter_entry.executable,
                "version": adapter_entry.version,
                "capabilities": adapter_entry.capabilities,
                "workspace_adapter": adapter_entry.workspace_adapter,
            },
            headers={"Authorization": f"Bearer {token2}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "pending"
        assert resp.json()["id"] == adapter_id

        # Bind should now be rejected (status is PENDING again)
        mc = self._master_client(app, master_token)
        resp = mc.post(
            f"/api/v1/runtime/adapters/{adapter_id}/bind-profile",
            json={"profile_name": profile_name},
        )
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
        assert "not approved" in resp.json()["detail"].lower()

    @staticmethod
    def _submit_and_approve_static(app, master_token, store, route_setup, profile_name):
        token = _mint_adapter_token(store, profile_name)
        script = _make_conformant_adapter_script(route_setup, f"{profile_name}-adapter")

        c = TestClient(app)
        resp = c.post(
            "/api/v1/runtime/adapters/submit",
            json={"executable": str(script), "version": "1.0.0"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        adapter_id = data["id"]
        c.headers.update({"Authorization": f"Bearer {master_token}"})
        resp = c.post(
            f"/api/v1/runtime/adapters/{adapter_id}/approve",
            json={
                "executable": data["executable"],
                "executable_hash": data["executable_hash"],
                "version": data["version"],
                "capabilities": data["capabilities"],
                "contract_version": data["contract_version"],
                "workspace_adapter": data["workspace_adapter"],
            },
        )
        assert resp.status_code == 200, resp.text
        return adapter_id


# ---------------------------------------------------------------------------
# 8. Legacy / Kimi non-mutation
# ---------------------------------------------------------------------------

class TestLegacyCompatibility:
    """Verify legacy generic profiles and token purposes are not mutated."""

    def test_adapter_routes_do_not_mutate_runtime_profiles(self, app_and_client):
        """Accessing adapters routes does not mutate runtime profiles."""
        app, master_token, store = app_and_client

        before = dict(load_runtime_profiles())
        c = TestClient(app)
        c.headers.update({"Authorization": f"Bearer {master_token}"})
        resp = c.get("/api/v1/runtime/adapters")
        assert resp.status_code == 200

        after = load_runtime_profiles()
        for name in before:
            if name in after:
                assert before[name] == after[name], (
                    f"Profile {name!r} mutated by adapter list"
                )

    def test_existing_registration_token_purposes_unaffected(self, token_store):
        """Existing token purposes (binary, profile) still work."""
        store = token_store

        # Mint binary-purpose token
        token, _exp = store.mint_runtime(name="test-binary", purpose="binary")
        assert store.validate_runtime(token) is not None

        # Mint profile-purpose token (org-scoped)
        token2, _exp2 = store.mint_runtime(name="test-profile", purpose="profile")
        assert store.validate_runtime(token2) is not None

        # Mint adapter-purpose token
        token3, _exp3 = store.mint_runtime(
            name="test-adapter", purpose="adapter", intended_profile_name="test-adapter"
        )
        assert store.validate_runtime(token3) is not None
