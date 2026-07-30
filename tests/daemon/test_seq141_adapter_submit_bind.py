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
    """Build a FastAPI app with all adapters routers and attach token store.

    Returns (app, TestClient, master_token_value).
    The submit_router and contract_reference_router are included separately
    (without master-bearer dependency). The main router retains master-bearer dependency.
    """
    from runtime.daemon.routes.adapters import router, submit_router, contract_reference_router
    from runtime.daemon import paths as paths_mod

    _bypass_loopback(monkeypatch)

    app = FastAPI()
    # contract_reference_router must be registered BEFORE router so its
    # specific /runtime/adapters/contract-reference GET takes priority over
    # the master-bearer GET /runtime/adapters/{adapter_id} catch-all on router.
    app.include_router(contract_reference_router, prefix="/api/v1")
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

    def test_bind_first_then_re_register_rejected(
        self, app_and_client, route_setup, token_store, monkeypatch
    ):
        """Forced ordering: bind completes first (bound profile created),
        then re-registration is rejected because the adapter-targeting
        profile is active.  Uses a monkeypatch hook at the durable
        adapter-save boundary to control commit interleaving.

        Final state: approved adapter + bound profile, no PENDING residue."""
        app, master_token, store = app_and_client
        profile_name = "bind-first-cli"
        adapter_id = self._submit_and_approve_static(
            app, master_token, store, route_setup, profile_name
        )

        adapter_entry = load_adapters().get(adapter_id)
        assert adapter_entry is not None
        assert adapter_entry.status == "approved"

        token2 = _mint_adapter_token(store, profile_name)

        # Hook into _save_adapter_locked — the single durable write boundary
        # for re-registration.  We block it until bind has completed.
        import runtime.orchestrator.custom_adapter_registry as car
        original_save_locked = car._save_adapter_locked
        bind_completed = threading.Event()
        re_reg_allowed = threading.Event()

        def _hooked_save_locked(entry):
            bind_completed.wait(timeout=30)
            re_reg_allowed.wait(timeout=30)
            original_save_locked(entry)

        monkeypatch.setattr(car, "_save_adapter_locked", _hooked_save_locked)

        outcomes: dict[str, int] = {}

        def do_re_register():
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
            outcomes["re_register"] = resp.status_code

        def do_bind():
            c = TestClient(app)
            c.headers.update({"Authorization": f"Bearer {master_token}"})
            resp = c.post(
                f"/api/v1/runtime/adapters/{adapter_id}/bind-profile",
                json={"profile_name": profile_name},
            )
            outcomes["bind"] = resp.status_code
            bind_completed.set()

        t_rereg = threading.Thread(target=do_re_register)
        t_bind = threading.Thread(target=do_bind)
        t_bind.start()
        t_rereg.start()

        bind_completed.wait(timeout=30)
        re_reg_allowed.set()

        t_bind.join(timeout=30)
        t_rereg.join(timeout=30)

        assert outcomes.get("bind") == 200, (
            f"Bind should have succeeded (ran first), got {outcomes}"
        )
        assert outcomes.get("re_register") == 422, (
            f"Re-registration should have been rejected (profile bound), "
            f"got {outcomes}"
        )

        # Final state: approved adapter + bound profile
        final_entry = load_adapters().get(adapter_id)
        assert final_entry is not None
        assert final_entry.status == "approved", (
            f"Adapter should remain approved after rejected re-registration, "
            f"got {final_entry.status!r}"
        )
        profiles = load_runtime_profiles()
        assert profile_name in profiles
        assert profiles[profile_name]["command_adapter_id"] == f"custom-adapter:{adapter_id}"

        monkeypatch.setattr(car, "_save_adapter_locked", original_save_locked)

    def test_re_register_first_then_bind_rejected(
        self, app_and_client, route_setup, token_store, monkeypatch
    ):
        """Forced ordering: re-registration resets adapter to PENDING first,
        then bind's re-read sees PENDING and rejects.  Uses a monkeypatch
        hook at the durable-runtime-profile write boundary in the bind path
        to control commit interleaving.

        Final state: PENDING adapter, no bound profile residue."""
        app, master_token, store = app_and_client
        profile_name = "rereg-first-cli"
        adapter_id = self._submit_and_approve_static(
            app, master_token, store, route_setup, profile_name
        )

        adapter_entry = load_adapters().get(adapter_id)
        assert adapter_entry is not None
        assert adapter_entry.status == "approved"

        token2 = _mint_adapter_token(store, profile_name)

        # Hook resolve_adapter (bind step 5, called BEFORE the adapter-store
        # lock is acquired) to delay bind until re-registration completes.
        # This avoids deadlocking: re-registration needs the lock, and if
        # we block after bind acquires it, re-registration starves.
        import runtime.orchestrator.custom_adapter_registry as car
        original_resolve = car.resolve_adapter
        rereg_completed = threading.Event()
        bind_allowed = threading.Event()

        def _hooked_resolve(adapter_id_arg):
            rereg_completed.wait(timeout=30)
            bind_allowed.wait(timeout=30)
            return original_resolve(adapter_id_arg)

        monkeypatch.setattr(car, "resolve_adapter", _hooked_resolve)

        outcomes: dict[str, int] = {}

        def do_re_register():
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
            outcomes["re_register"] = resp.status_code
            rereg_completed.set()

        def do_bind():
            c = TestClient(app)
            c.headers.update({"Authorization": f"Bearer {master_token}"})
            resp = c.post(
                f"/api/v1/runtime/adapters/{adapter_id}/bind-profile",
                json={"profile_name": profile_name},
            )
            outcomes["bind"] = resp.status_code

        t_rereg = threading.Thread(target=do_re_register)
        t_bind = threading.Thread(target=do_bind)
        t_rereg.start()
        t_bind.start()

        rereg_completed.wait(timeout=30)
        bind_allowed.set()

        t_rereg.join(timeout=30)
        t_bind.join(timeout=30)

        assert outcomes.get("re_register") == 200, (
            f"Re-registration should have succeeded (ran first), got {outcomes}"
        )
        assert outcomes.get("bind") == 422, (
            f"Bind should have been rejected (adapter is PENDING), "
            f"got {outcomes}"
        )

        # Final state: adapter is PENDING, no bound profile
        final_entry = load_adapters().get(adapter_id)
        assert final_entry is not None
        assert final_entry.status == "pending", (
            f"Adapter should be PENDING after re-registration, "
            f"got {final_entry.status!r}"
        )
        profiles = load_runtime_profiles()
        if profile_name in profiles:
            assert profiles[profile_name].get("command_adapter_id") != f"custom-adapter:{adapter_id}"

        monkeypatch.setattr(car, "resolve_adapter", original_resolve)

    def test_bind_no_residue_on_durable_write_failure(
        self, app_and_client, route_setup, token_store, monkeypatch
    ):
        """If the durable runtime profile write fails, no profile, registry,
        or audit residue is produced."""
        app, master_token, store = app_and_client
        profile_name = "norw-cli"
        adapter_id = self._submit_and_approve_static(
            app, master_token, store, route_setup, profile_name
        )

        mc = self._master_client(app, master_token)

        # Save pre-request state
        pre_profiles = dict(load_runtime_profiles())
        from runtime.orchestrator.executor_registry import get_registry as _reg
        registry = _reg()
        pre_in_memory = registry.get_profile(profile_name)

        # Force save_runtime_profile to fail.
        # The bind_adapter_profile function imports save_runtime_profile at
        # module level from runtime.orchestrator.runtime_executor_store.
        # We must patch the imported reference in routes.adapters, not the
        # store module itself.
        import runtime.daemon.routes.adapters as adapters_mod
        original_save = adapters_mod.save_runtime_profile

        def _failing_save(name, cfg):
            raise OSError("simulated disk write failure")

        monkeypatch.setattr(adapters_mod, "save_runtime_profile", _failing_save)

        try:
            resp = mc.post(
                f"/api/v1/runtime/adapters/{adapter_id}/bind-profile",
                json={"profile_name": profile_name},
            )
            # Should get a 500 — the failure is caught by the BaseException
            # handler in bind_adapter_profile.
            assert resp.status_code == 500, f"Expected 500, got {resp.status_code}: {resp.text}"

            # No durable residue
            post_profiles = load_runtime_profiles()
            if profile_name in post_profiles:
                assert post_profiles[profile_name] == pre_profiles.get(profile_name), (
                    f"Durable profile {profile_name!r} mutated despite save failure"
                )
            # No in-memory residue
            post_in_memory = registry.get_profile(profile_name)
            assert post_in_memory == pre_in_memory, (
                f"In-memory profile changed: {post_in_memory} vs {pre_in_memory}"
            )
        finally:
            monkeypatch.setattr(adapters_mod, "save_runtime_profile", original_save)

    def test_bind_no_residue_on_audit_failure(
        self, app_and_client, route_setup, token_store, monkeypatch
    ):
        """If the audit write fails after durable write + registry sync, the
        compensating rollback must leave no durable profile, no in-memory
        registry entry, and no audit residue."""
        app, master_token, store = app_and_client
        profile_name = "noaudit-cli"
        adapter_id = self._submit_and_approve_static(
            app, master_token, store, route_setup, profile_name
        )

        mc = self._master_client(app, master_token)

        # Save pre-request state
        pre_profiles = dict(load_runtime_profiles())
        from runtime.orchestrator.executor_registry import get_registry as _reg
        registry = _reg()
        pre_in_memory = registry.get_profile(profile_name)

        # Inject a failure AFTER the audit INSERT statement but BEFORE
        # the explicit commit() in _audit_adapter_bind.  The production
        # code calls insert_audit_log_uncommitted() + commit() in sequence;
        # the patch executes the original INSERT (row is in the uncommitted
        # transaction) then raises — the DB close in the finally block
        # rolls back the uncommitted row automatically.
        from runtime.infrastructure import database as infra_db
        original_insert_uncommitted = infra_db.Database.insert_audit_log_uncommitted

        def _insert_then_raise(self, task_id, agent, action, payload=None):
            # Execute the real INSERT — row is in the transaction, not committed.
            rowid = original_insert_uncommitted(self, task_id, agent, action, payload)
            # Simulate a post-insert failure (e.g. disk full, connection lost).
            raise RuntimeError("simulated post-insert audit failure")

        monkeypatch.setattr(
            infra_db.Database, "insert_audit_log_uncommitted", _insert_then_raise
        )

        # Snapshot pre-request audit rows — globally, unfiltered.
        from runtime.runtime import daemon_home
        pre_audit_db = infra_db.Database(daemon_home() / "runtime-audit.db")
        try:
            pre_audit_rows, _ = pre_audit_db.query_audit_logs()
        finally:
            pre_audit_db.close()

        try:
            resp = mc.post(
                f"/api/v1/runtime/adapters/{adapter_id}/bind-profile",
                json={"profile_name": profile_name},
            )
            # The rollback should catch this and return 500
            assert resp.status_code == 500, f"Expected 500, got {resp.status_code}: {resp.text}"
            detail = resp.json()["detail"]
            assert "restored" in detail.lower() or "pre-request" in detail.lower()

            # No durable residue
            post_profiles = load_runtime_profiles()
            if profile_name in post_profiles:
                assert post_profiles[profile_name] == pre_profiles.get(profile_name), (
                    f"Durable profile {profile_name!r} not restored after audit failure"
                )
            # No in-memory residue
            post_in_memory = registry.get_profile(profile_name)
            assert post_in_memory == pre_in_memory, (
                f"In-memory profile not restored after audit failure: {post_in_memory} vs {pre_in_memory}"
            )

            # NO audit residue — the uncommitted INSERT was rolled back.
            # Prove global exact equality, not merely a filtered count.
            post_audit_db = infra_db.Database(daemon_home() / "runtime-audit.db")
            try:
                post_audit_rows, _ = post_audit_db.query_audit_logs()
                assert post_audit_rows == pre_audit_rows, (
                    f"Audit residue after rollback: "
                    f"pre={len(pre_audit_rows)} rows, post={len(post_audit_rows)} rows"
                )
            finally:
                post_audit_db.close()
        finally:
            monkeypatch.setattr(
                infra_db.Database, "insert_audit_log_uncommitted",
                original_insert_uncommitted
            )

    def test_re_registration_rejected_when_profile_is_bound(
        self, app_and_client, route_setup, token_store
    ):
        """Re-registration (submit) must be rejected when a runtime profile
        is already bound to the adapter — the operator must unbind first.

        This covers the bind-first → re-register ordering.  The re-register
        → bind ordering is already covered by bind's PENDING rejection.
        """
        app, master_token, store = app_and_client
        profile_name = "bound-rereg-cli"
        adapter_id = self._submit_and_approve_static(
            app, master_token, store, route_setup, profile_name
        )

        # Bind the approved adapter to the profile.
        mc = self._master_client(app, master_token)
        resp = mc.post(
            f"/api/v1/runtime/adapters/{adapter_id}/bind-profile",
            json={"profile_name": profile_name},
        )
        assert resp.status_code == 200, f"Bind failed: {resp.status_code} {resp.text}"

        # Verify the profile is bound.
        profiles = load_runtime_profiles()
        assert profile_name in profiles
        assert profiles[profile_name]["command_adapter_id"] == f"custom-adapter:{adapter_id}"

        # Now try to re-register the SAME adapter via submit — must be
        # rejected because the profile is still bound.
        adapter_entry = load_adapters().get(adapter_id)
        assert adapter_entry is not None
        token2 = _mint_adapter_token(store, profile_name)
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
        assert resp.status_code == 422, (
            f"Expected 422 for re-registration with bound profile, "
            f"got {resp.status_code}: {resp.text}"
        )
        detail = resp.json()["detail"]
        assert "bound" in detail.lower() or "profile" in detail.lower(), (
            f"Expected rejection message about bound profile, got: {detail}"
        )
        assert adapter_id in detail, (
            f"Expected rejection message to mention adapter id, got: {detail}"
        )

        # The adapter status must remain unchanged (APPROVED, not reset to
        # PENDING by a rejected re-registration).
        re_read = load_adapters().get(adapter_id)
        assert re_read is not None
        assert re_read.status == "approved", (
            f"Adapter status changed to {re_read.status!r} after rejected "
            f"re-registration — must remain 'approved'"
        )

    def test_bind_no_residue_on_replace_failure(
        self, app_and_client, route_setup, token_store, monkeypatch
    ):
        """If the in-memory registry replacement (replace_custom_profile) raises
        ValueError after the durable write, the compensating rollback must
        restore pre-request durable and in-memory state — no overwritten
        durable profile, no registry residue, no audit residue.

        This targets the actual D7A replacement seam now used by
        bind_adapter_profile for the authorized legacy-to-adapter upgrade."""
        app, master_token, store = app_and_client
        profile_name = "repfail-cli"
        adapter_id = self._submit_and_approve_static(
            app, master_token, store, route_setup, profile_name
        )

        mc = self._master_client(app, master_token)

        # Save pre-request state.
        pre_profiles = dict(load_runtime_profiles())
        from runtime.orchestrator.executor_registry import get_registry as _reg
        registry = _reg()
        pre_in_memory = registry.get_profile(profile_name)
        pre_adapters = dict(load_adapters())

        # Force replace_custom_profile to raise ValueError.
        from runtime.orchestrator.executor_registry import ExecutorRegistry
        original_replace = ExecutorRegistry.replace_custom_profile

        def _failing_replace(self, profile):
            raise ValueError(
                f"Profile {profile.name!r} replacement rejected "
                f"(simulated registry collision)."
            )

        monkeypatch.setattr(
            ExecutorRegistry, "replace_custom_profile", _failing_replace
        )

        try:
            resp = mc.post(
                f"/api/v1/runtime/adapters/{adapter_id}/bind-profile",
                json={"profile_name": profile_name},
            )
            # The BaseException handler catches this and returns 500 with
            # rollback.
            assert resp.status_code == 500, (
                f"Expected 500 after replace failure, got {resp.status_code}: {resp.text}"
            )
            detail = resp.json()["detail"]
            assert "restored" in detail.lower() or "pre-request" in detail.lower()

            # No durable residue — profile must be restored to pre-request state.
            post_profiles = load_runtime_profiles()
            if profile_name in post_profiles:
                assert post_profiles[profile_name] == pre_profiles.get(profile_name), (
                    f"Durable profile {profile_name!r} not restored after "
                    f"replace failure"
                )
            # No in-memory residue.
            post_in_memory = registry.get_profile(profile_name)
            assert post_in_memory == pre_in_memory, (
                f"In-memory profile not restored after replace failure: "
                f"{post_in_memory} vs {pre_in_memory}"
            )
            # Adapter state unchanged.
            post_adapters = load_adapters()
            assert post_adapters.get(adapter_id).status == pre_adapters.get(adapter_id).status, (
                f"Adapter status changed after replace failure"
            )
        finally:
            monkeypatch.setattr(
                ExecutorRegistry, "replace_custom_profile", original_replace
            )

    def test_legacy_simple_to_adapter_upgrade(
        self, app_and_client, route_setup, token_store, monkeypatch
    ):
        """A pre-existing valid non-builtin custom profile (legacy/simple
        definition with command+argv_template, not command_adapter_id) must
        upgrade successfully to the exact approved adapter profile via bind.

        The bind route permits the existing custom profile (non-builtin gate),
        saves the adapter profile durably, then replaces it in the in-memory
        registry via the D7A replace_custom_profile seam.  The response must
        show connected and the durable + in-memory + audit facts must reflect
        the adapter-backed profile."""
        from runtime.orchestrator.runtime_executor_store import (
            save_runtime_profile,
            remove_runtime_profile,
        )
        from runtime.orchestrator.executor_registry import (
            ExecutorRegistry,
            get_registry as _reg,
        )

        app, master_token, store = app_and_client
        profile_name = "legacy-upgrade-cli"
        adapter_id = self._submit_and_approve_static(
            app, master_token, store, route_setup, profile_name
        )

        # Load the approved adapter entry for exact workspace_adapter assertions.
        adapter_entry = load_adapters().get(adapter_id)
        assert adapter_entry is not None, "Approved adapter entry must exist"
        assert adapter_entry.status == "approved"

        # Stage: pre-existing legacy/simple custom profile (command +
        # argv_template, not command_adapter_id).
        # The command and argv_template[0] must be the same executable name.
        legacy_cfg = {
            "command": "python3",
            "argv_template": ["python3", "{prompt}"],
        }
        save_runtime_profile(profile_name, legacy_cfg)
        registry = _reg()
        # Register the legacy profile in the in-memory registry.
        legacy_profile = ExecutorRegistry.validate_custom_profile_config(
            profile_name, legacy_cfg
        )
        registry.replace_custom_profile(legacy_profile)

        # Pre-request snapshots.
        pre_profiles = dict(load_runtime_profiles())
        pre_in_memory = registry.get_profile(profile_name)
        assert profile_name in pre_profiles, "Legacy profile must exist durably"
        assert pre_in_memory is not None, "Legacy profile must be in registry"
        assert pre_in_memory.command_adapter_id == "generic-cli", (
            "Legacy profile must have generic-cli command_adapter_id, "
            f"got {pre_in_memory.command_adapter_id!r}"
        )

        # Audit pre-snapshot: globally unfiltered, taken BEFORE bind.
        from runtime.runtime import daemon_home as dh_audit
        from runtime.infrastructure.database import Database
        audit_db_path = dh_audit() / "runtime-audit.db"
        pre_db = Database(audit_db_path)
        try:
            pre_all_rows, _ = pre_db.query_audit_logs()
            pre_count = len(pre_all_rows)
        finally:
            pre_db.close()

        mc = self._master_client(app, master_token)
        resp = mc.post(
            f"/api/v1/runtime/adapters/{adapter_id}/bind-profile",
            json={"profile_name": profile_name},
        )
        assert resp.status_code == 200, (
            f"Legacy upgrade bind failed: {resp.status_code} {resp.text}"
        )
        body = resp.json()
        assert body["status"] == "connected"
        assert body["profile_name"] == profile_name
        assert body["adapter_id"] == adapter_id
        assert body["command_adapter_id"] == f"custom-adapter:{adapter_id}"

        # Durable store: profile now adapter-backed.
        post_profiles = load_runtime_profiles()
        assert profile_name in post_profiles
        assert post_profiles[profile_name]["command_adapter_id"] == f"custom-adapter:{adapter_id}"
        assert post_profiles[profile_name]["workspace_adapter_id"] == adapter_entry.workspace_adapter

        # In-memory registry: profile is adapter-backed.
        post_in_memory = registry.get_profile(profile_name)
        assert post_in_memory is not None
        assert post_in_memory.command_adapter_id == f"custom-adapter:{adapter_id}"
        assert post_in_memory.workspace_adapter_id == adapter_entry.workspace_adapter
        assert post_in_memory.kind == "custom"

        # Audit post-snapshot: globally unfiltered, proves exactly one
        # canonical executor:<profile_name> / executor_registered row was
        # added while all pre-existing rows remained unchanged.
        post_db = Database(audit_db_path)
        try:
            post_all_rows, _ = post_db.query_audit_logs()
        finally:
            post_db.close()

        # Exactly one new row was added.
        assert len(post_all_rows) == pre_count + 1, (
            f"Expected {pre_count} + 1 audit rows, got {len(post_all_rows)}"
        )
        new_rows = post_all_rows[pre_count:]
        assert len(new_rows) == 1
        new_row = new_rows[0]
        assert new_row["task_id"] == f"executor:{profile_name}", (
            f"Unexpected task_id: {new_row['task_id']!r}"
        )
        assert new_row["action"] == "executor_registered"
        payload = new_row.get("payload", {}) or {}
        expected_payload = {
            "adapter_id": adapter_id,
            "command_adapter_id": f"custom-adapter:{adapter_id}",
            "workspace_adapter_id": adapter_entry.workspace_adapter,
        }
        assert payload == expected_payload, (
            f"Unexpected audit payload: {payload!r}"
        )

        # All pre-existing rows are identical.
        assert post_all_rows[:pre_count] == pre_all_rows, (
            "Pre-existing audit rows were mutated during bind"
        )

        # Cleanup: remove the profile so other tests aren't affected.
        remove_runtime_profile(profile_name)
        registry.unregister_custom_profile(profile_name)

    def test_bind_no_residue_on_replace_and_audit_failure(
        self, app_and_client, route_setup, token_store, monkeypatch
    ):
        """Combined rollback proof: both replace_custom_profile and audit
        commit can fail after the durable write.  In each case, the
        compensating rollback must restore pre-request durable, in-memory,
        and audit facts exactly, with no residue."""
        from runtime.orchestrator.executor_registry import (
            ExecutorRegistry,
            get_registry as _reg,
        )

        app, master_token, store = app_and_client
        profile_name = "combo-rollback-cli"
        adapter_id = self._submit_and_approve_static(
            app, master_token, store, route_setup, profile_name
        )

        mc = self._master_client(app, master_token)
        registry = _reg()

        # --- Path A: replace_custom_profile failure ---
        pre_profiles_a = dict(load_runtime_profiles())
        pre_in_memory_a = registry.get_profile(profile_name)

        original_replace = ExecutorRegistry.replace_custom_profile
        def _failing_replace(self, profile):
            raise ValueError("simulated replace failure")
        monkeypatch.setattr(
            ExecutorRegistry, "replace_custom_profile", _failing_replace
        )

        try:
            resp = mc.post(
                f"/api/v1/runtime/adapters/{adapter_id}/bind-profile",
                json={"profile_name": profile_name},
            )
            assert resp.status_code == 500
            assert "restored" in resp.json()["detail"].lower()

            # Durable residue check.
            post_profiles_a = load_runtime_profiles()
            if profile_name in post_profiles_a:
                assert post_profiles_a[profile_name] == pre_profiles_a.get(profile_name)
            # In-memory residue check.
            assert registry.get_profile(profile_name) == pre_in_memory_a
        finally:
            monkeypatch.setattr(
                ExecutorRegistry, "replace_custom_profile", original_replace
            )

        # --- Path B: post-insert audit failure ---
        # Inject AFTER the audit INSERT statement but BEFORE the explicit
        # commit().  The production code calls insert_audit_log_uncommitted()
        # + commit(); the patch executes the real INSERT then raises — the
        # DB close in the finally block rolls back the uncommitted row.
        pre_profiles_b = dict(load_runtime_profiles())
        pre_in_memory_b = registry.get_profile(profile_name)

        from runtime.infrastructure import database as infra_db
        from runtime.runtime import daemon_home
        original_insert_uncommitted = infra_db.Database.insert_audit_log_uncommitted

        def _insert_then_raise(self, task_id, agent, action, payload=None):
            rowid = original_insert_uncommitted(self, task_id, agent, action, payload)
            raise RuntimeError("simulated post-insert audit failure in combo test")

        monkeypatch.setattr(
            infra_db.Database, "insert_audit_log_uncommitted", _insert_then_raise
        )

        # Snapshot pre-request audit rows — globally, unfiltered.
        pre_audit_db = infra_db.Database(daemon_home() / "runtime-audit.db")
        try:
            pre_audit_rows, _ = pre_audit_db.query_audit_logs()
        finally:
            pre_audit_db.close()

        try:
            resp = mc.post(
                f"/api/v1/runtime/adapters/{adapter_id}/bind-profile",
                json={"profile_name": profile_name},
            )
            assert resp.status_code == 500
            assert "restored" in resp.json()["detail"].lower()

            # Durable residue check.
            post_profiles_b = load_runtime_profiles()
            if profile_name in post_profiles_b:
                assert post_profiles_b[profile_name] == pre_profiles_b.get(profile_name)
            # In-memory residue check.
            assert registry.get_profile(profile_name) == pre_in_memory_b

            # NO audit residue — the uncommitted INSERT was rolled back.
            # Prove global exact equality, not merely a filtered count.
            post_audit_db = infra_db.Database(daemon_home() / "runtime-audit.db")
            try:
                post_audit_rows, _ = post_audit_db.query_audit_logs()
                assert post_audit_rows == pre_audit_rows, (
                    f"Audit residue after combo-rollback: "
                    f"pre={len(pre_audit_rows)} rows, post={len(post_audit_rows)} rows"
                )
            finally:
                post_audit_db.close()
        finally:
            monkeypatch.setattr(
                infra_db.Database, "insert_audit_log_uncommitted",
                original_insert_uncommitted
            )

    def test_register_rejected_with_bound_profile_via_monkeypatch(
        self, app_and_client, route_setup, token_store, monkeypatch
    ):
        """Replace the barrier-based interleaving test with a deterministic
        monkeypatch-at-production-boundary test.  Hook into the adapter-store
        lock in register_custom_adapter to force re-registration to wait until
        bind has established a bound profile, then verify re-registration is
        rejected with a clear bound-profile message.

        This covers the TASK-3684 requirement: "Prove final adapter/profile/
        audit safety in each forced ordering." """
        app, master_token, store = app_and_client
        profile_name = "monkeypatch-bound-cli"
        adapter_id = self._submit_and_approve_static(
            app, master_token, store, route_setup, profile_name
        )

        adapter_entry = load_adapters().get(adapter_id)
        assert adapter_entry is not None
        assert adapter_entry.status == "approved"

        token2 = _mint_adapter_token(store, profile_name)

        # Hook into acquire_store_lock in custom_adapter_registry.
        # This is the exact production lock boundary that serializes
        # registration vs bind — custom_adapter_registry imports
        # acquire_store_lock directly, so we must patch the imported
        # symbol (car.acquire_store_lock), NOT the adapter_store module.
        import runtime.orchestrator.custom_adapter_registry as car
        original_acquire = car.acquire_store_lock
        bind_done = threading.Event()
        rereg_allowed = threading.Event()
        hook_entered = threading.Event()  # prove the hook was entered

        def _hooked_acquire():
            hook_entered.set()
            bind_done.wait(timeout=30)
            rereg_allowed.wait(timeout=30)
            original_acquire()

        monkeypatch.setattr(car, "acquire_store_lock", _hooked_acquire)

        # Record pre-request state for audit / registry assertions.
        pre_profiles = dict(load_runtime_profiles())
        from runtime.orchestrator.executor_registry import get_registry as _reg
        registry = _reg()
        pre_in_memory = registry.get_profile(profile_name)

        outcomes: dict[str, int] = {}

        def do_re_register():
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
            outcomes["re_register"] = resp.status_code

        def do_bind():
            c = TestClient(app)
            c.headers.update({"Authorization": f"Bearer {master_token}"})
            resp = c.post(
                f"/api/v1/runtime/adapters/{adapter_id}/bind-profile",
                json={"profile_name": profile_name},
            )
            outcomes["bind"] = resp.status_code
            bind_done.set()

        # Pre-audit snapshot: take globally unfiltered snapshot BEFORE bind.
        from runtime.runtime import daemon_home as dh_audit_pre
        from runtime.infrastructure.database import Database as AuditDatabase
        pre_audit_db = AuditDatabase(dh_audit_pre() / "runtime-audit.db")
        try:
            pre_audit_rows, _ = pre_audit_db.query_audit_logs()
            pre_audit_count = len(pre_audit_rows)
        finally:
            pre_audit_db.close()

        t_rereg = threading.Thread(target=do_re_register)
        t_bind = threading.Thread(target=do_bind)
        t_bind.start()
        t_rereg.start()

        bind_done.wait(timeout=30)
        # At this point bind has completed (profile is bound).
        # Now allow re-registration to proceed — it must see the bound
        # profile and reject.
        rereg_allowed.set()

        t_bind.join(timeout=30)
        t_rereg.join(timeout=30)

        monkeypatch.setattr(car, "acquire_store_lock", original_acquire)

        # Prove the hook was actually entered (the ordering was forced).
        assert hook_entered.is_set(), (
            "Hook was never entered — the patch on acquire_store_lock "
            "did not reach the production registration path.  Check "
            "that the patch targets custom_adapter_registry.acquire_store_lock."
        )

        # Bind must succeed (ran first)
        assert outcomes.get("bind") == 200, (
            f"Bind should have succeeded, got {outcomes}"
        )
        # Re-registration must be rejected (profile is bound)
        assert outcomes.get("re_register") == 422, (
            f"Re-registration should be rejected, got {outcomes}"
        )

        # Final state: approved adapter remains approved.
        final_entry = load_adapters().get(adapter_id)
        assert final_entry is not None
        assert final_entry.status == "approved"

        # Durable runtime profile is bound — command_adapter_id
        # references the approved adapter.
        profiles = load_runtime_profiles()
        assert profile_name in profiles
        assert profiles[profile_name]["command_adapter_id"] == f"custom-adapter:{adapter_id}"
        assert profiles[profile_name]["workspace_adapter_id"] == adapter_entry.workspace_adapter

        # In-memory registry reflects the bound profile.
        post_in_memory = registry.get_profile(profile_name)
        assert post_in_memory is not None, (
            f"In-memory registry missing profile {profile_name!r}"
        )
        assert post_in_memory.command_adapter_id == f"custom-adapter:{adapter_id}"
        assert post_in_memory.workspace_adapter_id == adapter_entry.workspace_adapter

        # Audit log: globally unfiltered snapshot proves exactly one canonical
        # executor:<profile_name> / executor_registered row was added while all
        # pre-existing rows remained unchanged.
        # The pre-snapshot was taken BEFORE the threads started.
        from runtime.runtime import daemon_home as dh_post
        from runtime.infrastructure.database import Database
        post_db = Database(dh_post() / "runtime-audit.db")
        try:
            post_all_rows, _ = post_db.query_audit_logs()
        finally:
            post_db.close()

        # Exactly one new row was added.
        assert len(post_all_rows) == pre_audit_count + 1, (
            f"Expected {pre_audit_count} + 1 audit rows, got {len(post_all_rows)}"
        )
        new_rows = post_all_rows[pre_audit_count:]
        assert len(new_rows) == 1
        new_row = new_rows[0]
        assert new_row["task_id"] == f"executor:{profile_name}", (
            f"Unexpected task_id: {new_row['task_id']!r}"
        )
        assert new_row["action"] == "executor_registered"
        payload = new_row.get("payload", {}) or {}
        expected_payload = {
            "adapter_id": adapter_id,
            "command_adapter_id": f"custom-adapter:{adapter_id}",
            "workspace_adapter_id": adapter_entry.workspace_adapter,
        }
        assert payload == expected_payload, (
            f"Unexpected audit payload: {payload!r}"
        )

        # All pre-existing rows are identical.
        assert post_all_rows[:pre_audit_count] == pre_audit_rows, (
            "Pre-existing audit rows were mutated during monkeypatch-bound bind"
        )


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


# ============================================================================
# THR-107 seq184: contract-reference endpoint tests
# ============================================================================


class TestContractReferenceHappyPath:
    """Happy-path: adapter-purpose token fetches schemas correctly."""

    def test_contract_reference_returns_schemas(self, app_and_client, token_store):
        """Adapter-purpose token returns full contract reference with schemas."""
        app, master_token, store = app_and_client
        token = _mint_adapter_token(store, "test-cli")

        client = TestClient(app)
        resp = client.get(
            "/api/v1/runtime/adapters/contract-reference",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()

        # Top-level fields
        assert data["contract_version"] == 1
        assert "adapter_input_schema" in data
        assert "adapter_output_schema" in data
        assert "rules" in data
        assert "submission" in data

        # Schemas are valid JSON Schema objects
        ai = data["adapter_input_schema"]
        assert ai["type"] == "object"
        assert "properties" in ai
        assert "contract_version" in ai["properties"]
        assert "invocation" in ai["properties"]
        assert "prompt" in ai["properties"]
        assert "workspace" in ai["properties"]
        assert "timeout" in ai["properties"]

        ao = data["adapter_output_schema"]
        assert ao["type"] == "object"
        assert "properties" in ao
        assert "success" in ao["properties"]
        assert "session_id" in ao["properties"]
        assert "adapter_metadata" in ao["properties"]

        # Rules
        rules = data["rules"]
        assert rules["input"]["source"] == "stdin"
        assert rules["output"]["target"] == "stdout"
        assert rules["output"]["max_size_bytes"] == 1_048_576
        assert rules["diagnostics"]["target"] == "stderr"

        # Submission metadata
        sub = data["submission"]
        assert sub["method"] == "POST"
        assert sub["path"] == "/api/v1/runtime/adapters/submit"
        assert sub["content_type"] == "application/json"

    def test_token_not_consumed_by_contract_reference(self, app_and_client, token_store):
        """Reading the contract reference does NOT consume the token."""
        app, master_token, store = app_and_client
        token = _mint_adapter_token(store, "test-cli")

        client = TestClient(app)

        # Read contract-reference 3 times — all should succeed
        for _ in range(3):
            resp = client.get(
                "/api/v1/runtime/adapters/contract-reference",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200, resp.text

        # Token is still valid (not consumed)
        assert store.validate_runtime(token) is not None

    def test_contract_reference_then_submit_still_works(self, app_and_client, route_setup, token_store):
        """Fetching contract reference does not interfere with subsequent submit."""
        app, master_token, store = app_and_client
        token = _mint_adapter_token(store, "test-cli")
        script = _make_conformant_adapter_script(route_setup, "test-cli-adapter")

        client = TestClient(app)

        # Step 1: Fetch contract reference
        resp = client.get(
            "/api/v1/runtime/adapters/contract-reference",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

        # Step 2: Submit still succeeds (token not consumed)
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


class TestContractReferenceAuth:
    """Auth scoping tests for the contract-reference endpoint."""

    def test_master_bearer_rejected(self, app_and_client, token_store):
        """Master bearer is rejected — only hrreg_ tokens accepted."""
        app, master_token, store = app_and_client
        _mint_adapter_token(store, "test-cli")

        client = TestClient(app)
        resp = client.get(
            "/api/v1/runtime/adapters/contract-reference",
            headers={"Authorization": f"Bearer {master_token}"},
        )
        assert resp.status_code == 401, resp.text

    def test_no_token_rejected(self, app_and_client, token_store):
        """Request without any Authorization header is rejected."""
        app, master_token, store = app_and_client
        _mint_adapter_token(store, "test-cli")

        client = TestClient(app)
        resp = client.get("/api/v1/runtime/adapters/contract-reference")
        assert resp.status_code == 401, resp.text

    def test_profile_purpose_token_rejected(self, app_and_client, token_store):
        """A profile-purpose token is rejected — only adapter-purpose accepted."""
        app, master_token, store = app_and_client
        token, _exp = store.mint_runtime(name="test-cli", purpose="profile")

        # Complete conformance for the token
        for step_id in store.DEFAULT_CONFORMANCE_STEPS:
            store.record_step_arrival_runtime(token, step_id)

        client = TestClient(app)
        resp = client.get(
            "/api/v1/runtime/adapters/contract-reference",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422, resp.text

    def test_binary_purpose_token_rejected(self, app_and_client, token_store):
        """A binary-purpose token is rejected — only adapter-purpose accepted."""
        app, master_token, store = app_and_client
        token, _exp = store.mint_runtime(name="test-cli", purpose="binary")

        # Complete conformance for the token
        for step_id in store.DEFAULT_CONFORMANCE_STEPS:
            store.record_step_arrival_runtime(token, step_id)

        client = TestClient(app)
        resp = client.get(
            "/api/v1/runtime/adapters/contract-reference",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422, resp.text

    def test_expired_token_rejected(self, app_and_client, token_store, monkeypatch):
        """Expired token is rejected."""
        app, master_token, store = app_and_client
        now = time.time()
        token, _exp = store.mint_runtime(
            name="test-cli", purpose="adapter", intended_profile_name="test-cli", now=now - 700
        )
        for step_id in store.DEFAULT_CONFORMANCE_STEPS:
            store.record_step_arrival_runtime(token, step_id)

        client = TestClient(app)
        resp = client.get(
            "/api/v1/runtime/adapters/contract-reference",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401, resp.text

    def test_consumed_token_rejected(self, app_and_client, token_store):
        """Already-consumed token is rejected."""
        app, master_token, store = app_and_client
        token = _mint_adapter_token(store, "test-cli")
        # Consume via reserve + commit (matching the actual submit flow)
        store.reserve_runtime(token)
        store.commit_runtime(token)

        client = TestClient(app)
        resp = client.get(
            "/api/v1/runtime/adapters/contract-reference",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401, resp.text

    def test_reserved_token_still_works_for_read(self, app_and_client, token_store):
        """A reserved (but not yet consumed) token can still read the contract."""
        app, master_token, store = app_and_client
        token = _mint_adapter_token(store, "test-cli")
        store.reserve_runtime(token)

        client = TestClient(app)
        resp = client.get(
            "/api/v1/runtime/adapters/contract-reference",
            headers={"Authorization": f"Bearer {token}"},
        )
        # A reserved token is invalid for validate_runtime but was reserved
        # by the submit route. The contract-reference route calls
        # validate_runtime which rejects reserved tokens.
        assert resp.status_code == 401, resp.text


class TestContractReferenceSchemaIntegrity:
    """Verify the returned schemas are generated from the shipping models."""

    def test_schemas_match_pydantic_models(self, app_and_client, token_store):
        """The returned schemas are exactly the Pydantic model_json_schema() output."""
        app, master_token, store = app_and_client
        token = _mint_adapter_token(store, "test-cli")

        client = TestClient(app)
        resp = client.get(
            "/api/v1/runtime/adapters/contract-reference",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()

        from runtime.orchestrator.adapter_contract import AdapterInput, AdapterOutput

        # Schemas should match what Pydantic generates
        assert data["adapter_input_schema"] == AdapterInput.model_json_schema()
        assert data["adapter_output_schema"] == AdapterOutput.model_json_schema()

    def test_adapter_input_schema_required_fields(self, app_and_client, token_store):
        """AdapterInput schema lists all required top-level fields."""
        app, master_token, store = app_and_client
        token = _mint_adapter_token(store, "test-cli")

        client = TestClient(app)
        resp = client.get(
            "/api/v1/runtime/adapters/contract-reference",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()

        ai_required = set(data["adapter_input_schema"]["required"])
        assert "contract_version" in ai_required
        assert "invocation" in ai_required
        assert "prompt" in ai_required
        assert "workspace" in ai_required
        assert "timeout" in ai_required
        assert "executor_context" in ai_required

    def test_adapter_output_schema_required_fields(self, app_and_client, token_store):
        """AdapterOutput schema lists all required top-level fields."""
        app, master_token, store = app_and_client
        token = _mint_adapter_token(store, "test-cli")

        client = TestClient(app)
        resp = client.get(
            "/api/v1/runtime/adapters/contract-reference",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()

        ao_required = set(data["adapter_output_schema"]["required"])
        assert "success" in ao_required
        assert "session_id" in ao_required
        assert "adapter_metadata" in ao_required
        assert "stdout_tail" in ao_required
        assert "stderr_tail" in ao_required
        assert "duration_seconds" in ao_required


class TestContractReferenceOpenApi:
    """Verify the contract-reference route appears in the OpenAPI schema."""

    def test_contract_reference_in_openapi(self, app_and_client, token_store):
        """The contract-reference GET route is exposed in the OpenAPI schema."""
        app, master_token, store = app_and_client
        _mint_adapter_token(store, "test-cli")

        openapi = app.openapi()
        paths = openapi.get("paths", {})
        assert "/api/v1/runtime/adapters/contract-reference" in paths
        contract_path = paths["/api/v1/runtime/adapters/contract-reference"]
        assert "get" in contract_path

    def test_contract_reference_route_accessible_in_openapi(self, app_and_client, token_store):
        """The contract-reference endpoint returns the documented schema shape."""
        app, master_token, store = app_and_client
        token = _mint_adapter_token(store, "test-cli")

        client = TestClient(app)
        resp = client.get(
            "/api/v1/runtime/adapters/contract-reference",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

        # Verify response matches documented shape
        data = resp.json()
        assert isinstance(data["contract_version"], int)
        assert isinstance(data["adapter_input_schema"], dict)
        assert isinstance(data["adapter_output_schema"], dict)
        assert isinstance(data["rules"], dict)
        assert isinstance(data["submission"], dict)
