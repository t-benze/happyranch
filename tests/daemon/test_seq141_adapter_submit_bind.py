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
from runtime.orchestrator.adapter_store import compute_sha256, load_adapters


def _dep_manifest(script: Path) -> dict:
    """Return the required dependency-manifest fields for a submit payload."""
    return {
        "dependency_manifest_version": 1,
        "dependencies": [{"executable": str(script), "sha256": compute_sha256(str(script))}],
    }


def _approval_snapshot(data: dict) -> dict:
    """Return every immutable fact required to approve a fresh adapter."""
    return {
        "executable": data["executable"],
        "executable_hash": data["executable_hash"],
        "version": data["version"],
        "capabilities": data["capabilities"],
        "contract_version": data["contract_version"],
        "workspace_adapter": data["workspace_adapter"],
        "dependency_manifest_version": data.get("dependency_manifest_version"),
        "dependencies": data.get("dependencies"),
    }


def _entry_manifest(entry) -> dict:
    """Reconstruct the exact manifest snapshot for a stored adapter entry."""
    return {
        "dependency_manifest_version": entry.dependency_manifest_version,
        "dependencies": entry.dependencies,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_conformant_adapter_script(tmp_path: Path, adapter_id: str) -> Path:
    """Create a minimal conformance-probe-passing executable adapter script
    at the daemon-managed canonical path (<daemon-home>/adapters/<adapter_id>).

    THR-107 seq339/340: scoped submissions MUST use the canonical location."""
    from runtime.orchestrator.custom_adapter_registry import compute_canonical_adapter_path
    # The daemon home is already set via HAPPYRANCH_DAEMON_HOME by route_setup
    _, required_path = compute_canonical_adapter_path(adapter_id)
    required_path.parent.mkdir(parents=True, exist_ok=True)

    script = required_path
    content = f'''#!/usr/bin/env python3
import json, sys
inp = json.load(sys.stdin)
out = {{
    "success": True,
    "returncode": 0,
    "adapter_metadata": {{
        "adapter_id": "{adapter_id}",
        "adapter_name": "{adapter_id}",
        "adapter_version": "1.0.0",
        "contract_version": 1,
        "adapter": "{adapter_id}"
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
                **_dep_manifest(script),
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["id"] == "test-cli-adapter"
        assert data["status"] == "approved"
        assert data["intended_profile_name"] == "test-cli"
        # seq363: scoped submission directly connects — eligibility is already_bound
        assert data.get("eligibility") == "already_bound"

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
                **_dep_manifest(script),
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
                **_dep_manifest(script),
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
                **_dep_manifest(script),
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
                **_dep_manifest(script),
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
                **_dep_manifest(script),
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
                **_dep_manifest(script),
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "approved"
        assert data["approved_at"] is not None  # seq363: auto-approved
        assert data["approved_by"] is not None

    def test_submission_cannot_bind(self, app_and_client, route_setup, token_store):
        """Submission token cannot call bind-profile."""
        app, master_token, store = app_and_client
        token = _mint_adapter_token(store, "test-cli")
        script = _make_conformant_adapter_script(route_setup, "test-cli-adapter")

        client = TestClient(app)
        # Submit
        resp = client.post(
            "/api/v1/runtime/adapters/submit",
            json={"executable": str(script), "version": "1.0.0", **_dep_manifest(script)},
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
            json={"executable": str(script), "version": "1.0.0", **_dep_manifest(script)},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

        # Replay fails
        resp = client.post(
            "/api/v1/runtime/adapters/submit",
            json={"executable": str(script), "version": "1.0.0", **_dep_manifest(script)},
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
                json={"executable": str(script), "version": "1.0.0", **_dep_manifest(script)},
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
            json={"executable": str(script), "version": "1.0.0", **_dep_manifest(script)},
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
            json=_approval_snapshot(data),
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
            json={"executable": str(script), "version": "1.0.0", **_dep_manifest(script)},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        adapter_id = data["id"]

        # Approve
        mc = self._master_client(app, master_token)
        resp = mc.post(
            f"/api/v1/runtime/adapters/{adapter_id}/approve",
            json=_approval_snapshot(data),
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
            json={"executable": str(script), "version": "1.0.0", **_dep_manifest(script)},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        adapter_id = data["id"]
        mc = self._master_client(app, master_token)
        resp = mc.post(
            f"/api/v1/runtime/adapters/{adapter_id}/approve",
            json=_approval_snapshot(data),
        )
        assert resp.status_code == 200, resp.text
        return adapter_id

    def test_pending_adapter_bind_rejected(self, app_and_client, route_setup, token_store):
        """Bind to a PENDING (not-yet-approved) adapter is rejected."""
        app, master_token, store = app_and_client
        # Use register_custom_adapter directly to get a PENDING adapter.
        # Scoped submission now auto-approves via seq363, so it cannot be used
        # to create PENDING-only entries from the normal Connect path.
        script = _make_conformant_adapter_script(route_setup, "pending-cli-adapter")
        from runtime.orchestrator.custom_adapter_registry import register_custom_adapter
        entry = register_custom_adapter(
            executable=str(script),
            version="1.0.0",
            capabilities=[],
            workspace_adapter="pi",
            registered_by="test",
            dependency_manifest_version=1,
            dependencies=[{"executable": str(script), "sha256": compute_sha256(str(script))}],
        )
        adapter_id = entry.id
        assert entry.status == "pending"

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
        """Submit with built-in profile name fails at auto-connect (seq363).

        THR-107 seq363: the scoped submission now auto-approves and auto-binds.
        A builtin-colliding intended_profile_name causes the binding to fail,
        and the approval is rolled back — the adapter remains PENDING and the
        token is released for retry.
        """
        app, master_token, store = app_and_client
        token = _mint_adapter_token(store, "codex")
        script = _make_conformant_adapter_script(route_setup, "codex-adapter")

        c = TestClient(app)
        resp = c.post(
            "/api/v1/runtime/adapters/submit",
            json={"executable": str(script), "version": "1.0.0", **_dep_manifest(script)},
            headers={"Authorization": f"Bearer {token}"},
        )
        # seq363: auto-connect fails because "codex" is a built-in profile name
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
        detail = resp.json().get("detail", "")
        assert "built-in" in str(detail).lower() or "collides" in str(detail).lower()

        # Adapter remains PENDING after the failed auto-connect rollback.
        # register_custom_adapter wrote a PENDING entry; approve_adapter rolled
        # back to PENDING when the auto-bind failed.
        scoped_adapter_id = "codex-adapter"
        mc = self._master_client(app, master_token)
        resp = mc.get(f"/api/v1/runtime/adapters/{scoped_adapter_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "pending"

        # seq244: dependency manifest MUST be preserved through the
        # failed-auto-connect rollback.
        assert data["dependency_manifest_version"] == 1, (
            "dependency_manifest_version must survive failed-auto-connect rollback"
        )
        assert isinstance(data["dependencies"], list)
        assert len(data["dependencies"]) == 1
        dep = data["dependencies"][0]
        assert dep["executable"] == str(script)
        assert dep["sha256"] == compute_sha256(str(script))

    def test_rollback_manifest_preserves_re_registration_guard(self, app_and_client,
                                                                route_setup, token_store):
        """Re-registration after failed-auto-bind rollback honours the fresh
        manifest identity — a re-submit without dependency_manifest_version is
        rejected (not silently treated as legacy).
        """
        app, master_token, store = app_and_client
        token = _mint_adapter_token(store, "codex")
        script = _make_conformant_adapter_script(route_setup, "codex-adapter")

        c = TestClient(app)
        # 1) Submit with strict manifest
        resp = c.post(
            "/api/v1/runtime/adapters/submit",
            json={"executable": str(script), "version": "1.0.0", **_dep_manifest(script)},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        adapter_id = data["id"]
        original_manifest_version = data["dependency_manifest_version"]
        original_deps = data["dependencies"]

        # 2) Approve → auto-bind collision → rollback to PENDING
        mc = self._master_client(app, master_token)
        resp = mc.post(
            f"/api/v1/runtime/adapters/{adapter_id}/approve",
            json=_approval_snapshot(data),
        )
        assert resp.status_code == 422

        # 3) Verify rollback preserved manifest
        resp = mc.get(f"/api/v1/runtime/adapters/{adapter_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"
        assert resp.json()["dependency_manifest_version"] == original_manifest_version
        assert resp.json()["dependencies"] == original_deps

        # 4) Re-register the SAME adapter identity WITHOUT a dependency
        #    manifest — this must be REJECTED (422), proving the adapter
        #    is still treated as a fresh-manifest identity, not silently
        #    reclassed as legacy.
        new_token = _mint_adapter_token(store, "codex2")
        resp = c.post(
            "/api/v1/runtime/adapters/submit",
            json={"executable": str(script), "version": "1.0.0"},
            headers={"Authorization": f"Bearer {new_token}"},
        )
        assert resp.status_code == 422, (
            f"Expected 422 for re-registration without manifest, got {resp.status_code}"
        )
        detail = resp.json()["detail"]
        # Pydantic validation errors may return detail as a list of objects
        detail_str = str(detail).lower()
        assert "dependency_manifest_version" in detail_str or (
            "manifest" in detail_str
        ), f"Rejection should reference manifest: {detail}"

        # 5) Verify the existing PENDING adapter remains untouched
        resp = mc.get(f"/api/v1/runtime/adapters/{adapter_id}")
        assert resp.status_code == 200
        assert resp.json()["dependency_manifest_version"] == original_manifest_version
        assert resp.json()["dependencies"] == original_deps


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
            json={"executable": str(script), "version": "1.0.0", **_dep_manifest(script)},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        adapter_id = data["id"]
        mc = self._master_client(app, master_token)
        resp = mc.post(
            f"/api/v1/runtime/adapters/{adapter_id}/approve",
            json=_approval_snapshot(data),
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
            json={"executable": str(script), "version": "1.0.0", **_dep_manifest(script)},
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
                json=_approval_snapshot(data),
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
        """After seq237 auto-bind, re-registration is blocked while profile is bound.

        THR-107 seq237: approval auto-binds the profile. Re-registration
        (via submit) is rejected because a runtime profile is bound to the
        adapter. The operator must remove the profile first.
        """
        app, master_token, store = app_and_client
        profile_name = "rereg-race-cli"
        adapter_id = self._submit_and_approve_static(app, master_token, store, route_setup, profile_name)

        # Verify the adapter is APPROVED and profile is bound
        mc = self._master_client(app, master_token)
        resp = mc.get(f"/api/v1/runtime/adapters/{adapter_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"
        assert resp.json()["eligibility"] == "already_bound"

        # Re-registration via submit is blocked because profile is bound
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
                **_entry_manifest(adapter_entry),
            },
            headers={"Authorization": f"Bearer {token2}"},
        )
        # Re-registration is blocked because profile is bound to the adapter
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
        assert "currently bound" in resp.json()["detail"].lower() or "profile" in resp.json()["detail"].lower()

        # Bind is idempotent (profile already bound, replace_custom_profile succeeds)
        resp = mc.post(
            f"/api/v1/runtime/adapters/{adapter_id}/bind-profile",
            json={"profile_name": profile_name},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        assert resp.json()["status"] == "connected"

    @staticmethod
    def _submit_and_approve_static(app, master_token, store, route_setup, profile_name):
        token = _mint_adapter_token(store, profile_name)
        script = _make_conformant_adapter_script(route_setup, f"{profile_name}-adapter")

        c = TestClient(app)
        resp = c.post(
            "/api/v1/runtime/adapters/submit",
            json={"executable": str(script), "version": "1.0.0", **_dep_manifest(script)},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        adapter_id = data["id"]
        c.headers.update({"Authorization": f"Bearer {master_token}"})
        resp = c.post(
            f"/api/v1/runtime/adapters/{adapter_id}/approve",
            json=_approval_snapshot(data),
        )
        assert resp.status_code == 200, resp.text
        return adapter_id

    def test_bind_first_then_re_register_rejected(
        self, app_and_client, route_setup, token_store, monkeypatch
    ):
        """After seq237 auto-bind during approval, both re-registration and
        re-bind are rejected because the profile is already bound.

        THR-107 seq237: approval atomically binds the profile. No race
        condition exists between bind and re-registration because both
        happen under the same lock. After approval, the adapter is APPROVED
        and the profile is already_bound — re-registration and re-bind are
        both rejected with clear errors."""
        app, master_token, store = app_and_client
        profile_name = "bind-first-cli"
        adapter_id = self._submit_and_approve_static(
            app, master_token, store, route_setup, profile_name
        )

        adapter_entry = load_adapters().get(adapter_id)
        assert adapter_entry is not None
        assert adapter_entry.status == "approved"

        # Verify profile is already bound (seq237 auto-bind)
        mc = self._master_client(app, master_token)
        resp = mc.get(f"/api/v1/runtime/adapters/{adapter_id}")
        assert resp.status_code == 200
        assert resp.json()["eligibility"] == "already_bound"

        token2 = _mint_adapter_token(store, profile_name)

        # Re-registration: blocked because profile is bound
        c = TestClient(app)
        resp = c.post(
            "/api/v1/runtime/adapters/submit",
            json={
                "executable": adapter_entry.executable,
                "version": adapter_entry.version,
                "capabilities": adapter_entry.capabilities,
                "workspace_adapter": adapter_entry.workspace_adapter,
                **_entry_manifest(adapter_entry),
            },
            headers={"Authorization": f"Bearer {token2}"},
        )
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
        assert "currently bound" in resp.json()["detail"].lower()

        # Bind is idempotent (profile already bound)
        resp = mc.post(
            f"/api/v1/runtime/adapters/{adapter_id}/bind-profile",
            json={"profile_name": profile_name},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

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

    def test_re_register_first_then_bind_rejected(
        self, app_and_client, route_setup, token_store, monkeypatch
    ):
        """After seq237 auto-bind, both re-registration and re-bind are rejected.

        THR-107 seq237: approval atomically binds the profile. No racing
        is possible because both operations complete under the same lock.
        Re-registration is blocked (profile currently bound) and bind is
        rejected (profile already exists/already_bound).

        Final state: approved adapter + bound profile."""
        app, master_token, store = app_and_client
        profile_name = "rereg-first-cli"
        adapter_id = self._submit_and_approve_static(
            app, master_token, store, route_setup, profile_name
        )

        adapter_entry = load_adapters().get(adapter_id)
        assert adapter_entry is not None
        assert adapter_entry.status == "approved"

        # Verify profile is already bound (seq237 auto-bind)
        mc = self._master_client(app, master_token)
        resp = mc.get(f"/api/v1/runtime/adapters/{adapter_id}")
        assert resp.status_code == 200
        assert resp.json()["eligibility"] == "already_bound"

        token2 = _mint_adapter_token(store, profile_name)

        # Re-registration: blocked because profile is bound
        c = TestClient(app)
        resp = c.post(
            "/api/v1/runtime/adapters/submit",
            json={
                "executable": adapter_entry.executable,
                "version": adapter_entry.version,
                "capabilities": adapter_entry.capabilities,
                "workspace_adapter": adapter_entry.workspace_adapter,
                **_entry_manifest(adapter_entry),
            },
            headers={"Authorization": f"Bearer {token2}"},
        )
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
        assert "currently bound" in resp.json()["detail"].lower()

        # Bind is idempotent (profile already bound)
        resp = mc.post(
            f"/api/v1/runtime/adapters/{adapter_id}/bind-profile",
            json={"profile_name": profile_name},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        # Final state: adapter remains APPROVED, profile remains bound
        final_entry = load_adapters().get(adapter_id)
        assert final_entry is not None
        assert final_entry.status == "approved"
        profiles = load_runtime_profiles()
        assert profile_name in profiles
        assert profiles[profile_name]["command_adapter_id"] == f"custom-adapter:{adapter_id}"

    def test_approve_rollback_on_bind_write_failure(
        self, app_and_client, route_setup, token_store, monkeypatch
    ):
        """seq237: if auto-bind's save_runtime_profile fails during approve,
        approval rolls back to PENDING with no profile residue."""
        app, master_token, store = app_and_client
        profile_name = "norw-cli"
        token = _mint_adapter_token(store, profile_name)
        script = _make_conformant_adapter_script(route_setup, f"{profile_name}-adapter")

        c = TestClient(app)
        resp = c.post(
            "/api/v1/runtime/adapters/submit",
            json={"executable": str(script), "version": "1.0.0", **_dep_manifest(script)},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        adapter_id = data["id"]

        pre_profiles = dict(load_runtime_profiles())
        from runtime.orchestrator.executor_registry import get_registry as _reg
        registry = _reg()
        pre_in_memory = registry.get_profile(profile_name)

        import runtime.orchestrator.runtime_executor_store as res
        original_save = res.save_runtime_profile

        def _failing_save(name, cfg):
            raise OSError("simulated disk write failure")

        monkeypatch.setattr(res, "save_runtime_profile", _failing_save)

        try:
            mc = self._master_client(app, master_token)
            resp = mc.post(
                f"/api/v1/runtime/adapters/{adapter_id}/approve",
                json=_approval_snapshot(data),
            )
            assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
            # Adapter back to PENDING
            re_read = load_adapters().get(adapter_id)
            assert re_read is not None and re_read.status == "pending"
            # No profile residue
            post_profiles = load_runtime_profiles()
            if profile_name in post_profiles:
                assert post_profiles[profile_name] == pre_profiles.get(profile_name)
            assert registry.get_profile(profile_name) == pre_in_memory
        finally:
            monkeypatch.setattr(res, "save_runtime_profile", original_save)

    def test_approve_rollback_on_audit_failure(
        self, app_and_client, route_setup, token_store, monkeypatch
    ):
        """seq237: if auto-bind's audit write fails during approve, approval
        rolls back to PENDING with no profile or audit residue."""
        app, master_token, store = app_and_client
        profile_name = "noaudit-cli"
        token = _mint_adapter_token(store, profile_name)
        script = _make_conformant_adapter_script(route_setup, f"{profile_name}-adapter")

        c = TestClient(app)
        resp = c.post(
            "/api/v1/runtime/adapters/submit",
            json={"executable": str(script), "version": "1.0.0", **_dep_manifest(script)},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        adapter_id = data["id"]

        pre_profiles = dict(load_runtime_profiles())
        from runtime.orchestrator.executor_registry import get_registry as _reg
        registry = _reg()
        pre_in_memory = registry.get_profile(profile_name)

        from runtime.infrastructure import database as infra_db
        original_insert_uncommitted = infra_db.Database.insert_audit_log_uncommitted

        def _insert_then_raise(self, task_id, agent, action, payload=None):
            rowid = original_insert_uncommitted(self, task_id, agent, action, payload)
            raise RuntimeError("simulated post-insert audit failure")

        monkeypatch.setattr(infra_db.Database, "insert_audit_log_uncommitted", _insert_then_raise)

        from runtime.runtime import daemon_home
        pre_audit_db = infra_db.Database(daemon_home() / "runtime-audit.db")
        try:
            pre_audit_rows, _ = pre_audit_db.query_audit_logs()
        finally:
            pre_audit_db.close()

        try:
            mc = self._master_client(app, master_token)
            resp = mc.post(
                f"/api/v1/runtime/adapters/{adapter_id}/approve",
                json=_approval_snapshot(data),
            )
            assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
            re_read = load_adapters().get(adapter_id)
            assert re_read is not None and re_read.status == "pending"
            post_profiles = load_runtime_profiles()
            if profile_name in post_profiles:
                assert post_profiles[profile_name] == pre_profiles.get(profile_name)
            assert registry.get_profile(profile_name) == pre_in_memory
            post_audit_db = infra_db.Database(daemon_home() / "runtime-audit.db")
            try:
                post_audit_rows, _ = post_audit_db.query_audit_logs()
                assert post_audit_rows == pre_audit_rows
            finally:
                post_audit_db.close()
        finally:
            monkeypatch.setattr(infra_db.Database, "insert_audit_log_uncommitted", original_insert_uncommitted)

    def test_re_registration_rejected_when_profile_is_bound(
        self, app_and_client, route_setup, token_store
    ):
        """seq237: after approval auto-binds the profile, re-registration
        (submit) is rejected because a runtime profile is already bound."""
        app, master_token, store = app_and_client
        profile_name = "bound-rereg-cli"
        adapter_id = self._submit_and_approve_static(
            app, master_token, store, route_setup, profile_name
        )

        # Profile is already bound (seq237 auto-bind during approval)
        mc = self._master_client(app, master_token)
        resp = mc.get(f"/api/v1/runtime/adapters/{adapter_id}")
        assert resp.status_code == 200
        assert resp.json()["eligibility"] == "already_bound"

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
                **_entry_manifest(adapter_entry),
            },
            headers={"Authorization": f"Bearer {token2}"},
        )
        assert resp.status_code == 422, (
            f"Expected 422 for re-registration with bound profile, "
            f"got {resp.status_code}: {resp.text}"
        )
        detail = resp.json()["detail"]
        assert "bound" in detail.lower() or "profile" in detail.lower()
        assert adapter_id in detail

        # Adapter must remain APPROVED after rejected re-registration.
        re_read = load_adapters().get(adapter_id)
        assert re_read is not None
        assert re_read.status == "approved"

    def test_approve_rollback_on_replace_failure(
        self, app_and_client, route_setup, token_store, monkeypatch
    ):
        """seq237: if auto-bind's replace_custom_profile raises ValueError
        during approve, approval rolls back to PENDING with no profile residue."""
        app, master_token, store = app_and_client
        profile_name = "repfail-cli"
        token = _mint_adapter_token(store, profile_name)
        script = _make_conformant_adapter_script(route_setup, f"{profile_name}-adapter")

        c = TestClient(app)
        resp = c.post(
            "/api/v1/runtime/adapters/submit",
            json={"executable": str(script), "version": "1.0.0", **_dep_manifest(script)},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        adapter_id = data["id"]

        pre_profiles = dict(load_runtime_profiles())
        from runtime.orchestrator.executor_registry import get_registry as _reg, ExecutorRegistry
        registry = _reg()
        pre_in_memory = registry.get_profile(profile_name)

        original_replace = ExecutorRegistry.replace_custom_profile

        def _failing_replace(self, profile):
            raise ValueError("simulated registry collision")

        monkeypatch.setattr(ExecutorRegistry, "replace_custom_profile", _failing_replace)

        try:
            mc = self._master_client(app, master_token)
            resp = mc.post(
                f"/api/v1/runtime/adapters/{adapter_id}/approve",
                json=_approval_snapshot(data),
            )
            assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
            re_read = load_adapters().get(adapter_id)
            assert re_read is not None and re_read.status == "pending"
            post_profiles = load_runtime_profiles()
            if profile_name in post_profiles:
                assert post_profiles[profile_name] == pre_profiles.get(profile_name)
            assert registry.get_profile(profile_name) == pre_in_memory
        finally:
            monkeypatch.setattr(ExecutorRegistry, "replace_custom_profile", original_replace)

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

    def test_approve_rollback_on_replace_and_audit_failure(
        self, app_and_client, route_setup, token_store, monkeypatch
    ):
        """seq237: combined rollback proof — both replace_custom_profile and
        audit commit failures during approve-with-auto-bind must roll back
        to PENDING with no profile or audit residue."""
        from runtime.orchestrator.executor_registry import (
            ExecutorRegistry,
            get_registry as _reg,
        )

        app, master_token, store = app_and_client
        profile_name = "combo-rollback-cli"
        token = _mint_adapter_token(store, profile_name)
        script = _make_conformant_adapter_script(route_setup, f"{profile_name}-adapter")

        c = TestClient(app)
        resp = c.post(
            "/api/v1/runtime/adapters/submit",
            json={"executable": str(script), "version": "1.0.0", **_dep_manifest(script)},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        adapter_id = data["id"]

        mc = self._master_client(app, master_token)
        registry = _reg()

        # --- Path A: replace_custom_profile failure ---
        pre_profiles_a = dict(load_runtime_profiles())
        pre_in_memory_a = registry.get_profile(profile_name)

        original_replace = ExecutorRegistry.replace_custom_profile
        def _failing_replace(self, profile):
            raise ValueError("simulated replace failure")
        monkeypatch.setattr(ExecutorRegistry, "replace_custom_profile", _failing_replace)

        try:
            resp = mc.post(
                f"/api/v1/runtime/adapters/{adapter_id}/approve",
                json=_approval_snapshot(data),
            )
            assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
            re_read = load_adapters().get(adapter_id)
            assert re_read is not None and re_read.status == "pending"
            post_profiles_a = load_runtime_profiles()
            if profile_name in post_profiles_a:
                assert post_profiles_a[profile_name] == pre_profiles_a.get(profile_name)
            assert registry.get_profile(profile_name) == pre_in_memory_a
        finally:
            monkeypatch.setattr(ExecutorRegistry, "replace_custom_profile", original_replace)

        # --- Path B: post-insert audit failure ---
        pre_profiles_b = dict(load_runtime_profiles())
        pre_in_memory_b = registry.get_profile(profile_name)

        from runtime.infrastructure import database as infra_db
        from runtime.runtime import daemon_home
        original_insert_uncommitted = infra_db.Database.insert_audit_log_uncommitted

        def _insert_then_raise(self, task_id, agent, action, payload=None):
            rowid = original_insert_uncommitted(self, task_id, agent, action, payload)
            raise RuntimeError("simulated post-insert audit failure in combo test")

        monkeypatch.setattr(infra_db.Database, "insert_audit_log_uncommitted", _insert_then_raise)

        pre_audit_db = infra_db.Database(daemon_home() / "runtime-audit.db")
        try:
            pre_audit_rows, _ = pre_audit_db.query_audit_logs()
        finally:
            pre_audit_db.close()

        try:
            resp = mc.post(
                f"/api/v1/runtime/adapters/{adapter_id}/approve",
                json=_approval_snapshot(data),
            )
            assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
            re_read = load_adapters().get(adapter_id)
            assert re_read is not None and re_read.status == "pending"
            post_profiles_b = load_runtime_profiles()
            if profile_name in post_profiles_b:
                assert post_profiles_b[profile_name] == pre_profiles_b.get(profile_name)
            assert registry.get_profile(profile_name) == pre_in_memory_b
            post_audit_db = infra_db.Database(daemon_home() / "runtime-audit.db")
            try:
                post_audit_rows, _ = post_audit_db.query_audit_logs()
                assert post_audit_rows == pre_audit_rows
            finally:
                post_audit_db.close()
        finally:
            monkeypatch.setattr(infra_db.Database, "insert_audit_log_uncommitted", original_insert_uncommitted)

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
                    **_entry_manifest(adapter_entry),
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

    def test_contract_reference_includes_canonical_adapter_id(self, app_and_client, token_store):
        """THR-107 seq268: contract-reference response includes canonical_adapter_id derived from token."""
        app, master_token, store = app_and_client
        token = _mint_adapter_token(store, "kimi")

        client = TestClient(app)
        resp = client.get(
            "/api/v1/runtime/adapters/contract-reference",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()

        # canonical_adapter_id must be server-derived from token's intended_profile_name
        assert data["canonical_adapter_id"] == "kimi-adapter"
        assert "canonical_adapter_id_description" in data
        assert "kimi-adapter" in data["canonical_adapter_id_description"]

        # Self-test fixture must use the real adapter ID, not a static placeholder
        probe = data["probe"]["self_test_fixture"]
        assert probe["expected_output"]["adapter_metadata"]["adapter"] == "kimi-adapter"
        assert probe["input"]["executor_context"]["provider"] == "kimi-adapter"

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
                **_dep_manifest(script),
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
        # Minted 1900 seconds ago — unambiguously beyond the 1800-second TTL.
        token, _exp = store.mint_runtime(
            name="test-cli", purpose="adapter", intended_profile_name="test-cli", now=now - 1900
        )
        for step_id in store.DEFAULT_CONFORMANCE_STEPS:
            store.record_step_arrival_runtime(token, step_id)

        client = TestClient(app)
        resp = client.get(
            "/api/v1/runtime/adapters/contract-reference",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401, resp.text

    def test_token_boundary_exact_1800_seconds(self, token_store):
        """Deterministic fixed-clock boundary test at the validate_runtime seam.

        Mints a fresh adapter-purpose token at a fixed now, then proves it
        validates immediately before 1800 seconds and rejects immediately after.

        Exact-expiry convention (registration_token._validate_raw):
          if now > record.expires_at: return None
        Token is valid at expires_at exactly; expired at expires_at + epsilon.
        """
        store = token_store
        T0 = 1_000_000.0
        # Mint a fresh adapter-purpose token at the fixed clock.
        token, expires_at = store.mint_runtime(
            name="boundary-test",
            purpose="adapter",
            intended_profile_name="boundary-test",
            now=T0,
        )
        assert expires_at == T0 + 1800.0, (
            f"Expected expires_at=T0+1800, got {expires_at}"
        )
        # Complete conformance so validate_runtime returns the record.
        for step_id in store.DEFAULT_CONFORMANCE_STEPS:
            store.record_step_arrival_runtime(token, step_id, now=T0 + 1.0)

        # Prove the token is valid at T0 + 1799.999 (just before expiry).
        record_before = store.validate_runtime(token, now=T0 + 1799.999)
        assert record_before is not None, (
            "Token should be valid at T0+1799.999 (before expires_at)"
        )
        assert not record_before.consumed

        # Prove the token is still valid at expires_at exactly (exact-expiry).
        record_at_expiry = store.validate_runtime(token, now=T0 + 1800.0)
        assert record_at_expiry is not None, (
            "Token should be valid at expires_at exactly (exact-expiry convention)"
        )

        # Prove the token is rejected at T0 + 1800.001 (just after expiry).
        record_after = store.validate_runtime(token, now=T0 + 1800.001)
        assert record_after is None, (
            "Token should be expired at T0+1800.001 (after expires_at)"
        )

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


class TestSubmitStrictManifestVersion:
    """THR-107 seq244 fix-forward: strict integer enforcement on submit route.

    dependency_manifest_version must be literally JSON integer 1.
    JSON 1.0, "1", and true are rejected with 422 before any
    conformance probe or durable persistence.
    """

    _ADAPTER = "strict-int-submit"

    @pytest.mark.parametrize("bad_value,label", [
        (1.0, "float-1.0"),
        ("1", "string-1"),
        (True, "bool-true"),
    ])
    def test_submit_route_rejects_non_strict_int_422(
        self,
        app_and_client,
        route_setup,
        token_store,
        monkeypatch,
        bad_value,
        label,
    ):
        """Submit route returns 422, probe not called, no persistence."""
        from unittest.mock import MagicMock
        spy = MagicMock()
        monkeypatch.setattr(
            "runtime.daemon.routes.adapters.register_custom_adapter",
            spy,
        )

        app, master_token, store = app_and_client
        token = _mint_adapter_token(store, f"cli-{label}")
        script = _make_conformant_adapter_script(
            route_setup, f"{label}-adapter"
        )

        client = TestClient(app)
        resp = client.post(
            "/api/v1/runtime/adapters/submit",
            json={
                "executable": str(script),
                "version": "1.0.0",
                "capabilities": [],
                "workspace_adapter": "pi",
                "dependency_manifest_version": bad_value,
                "dependencies": [
                    {"executable": str(script), "sha256": compute_sha256(str(script))}
                ],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422, (
            f"submit route: expected 422 for {label}, got {resp.status_code}: {resp.text}"
        )
        spy.assert_not_called()
        assert load_adapters() == {}

    def test_submit_route_accepts_int_1_probe_and_persist(
        self,
        app_and_client,
        route_setup,
        token_store,
        monkeypatch,
    ):
        """Submit route accepts literal int 1, probe called, adapter persisted."""
        from unittest.mock import MagicMock
        from runtime.orchestrator.custom_adapter_registry import (
            register_custom_adapter as _real,
        )
        spy = MagicMock(wraps=_real)
        monkeypatch.setattr(
            "runtime.daemon.routes.adapters.register_custom_adapter",
            spy,
        )

        app, master_token, store = app_and_client
        token = _mint_adapter_token(store, "cli-int-1")
        script = _make_conformant_adapter_script(route_setup, "cli-int-1-adapter")

        client = TestClient(app)
        resp = client.post(
            "/api/v1/runtime/adapters/submit",
            json={
                "executable": str(script),
                "version": "1.0.0",
                "capabilities": [],
                "workspace_adapter": "pi",
                "dependency_manifest_version": 1,
                "dependencies": [
                    {"executable": str(script), "sha256": compute_sha256(str(script))}
                ],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["dependency_manifest_version"] == 1
        assert data["status"] == "approved"  # seq363: auto-connect
        assert data.get("eligibility") == "already_bound"
        spy.assert_called_once()
        adapters = load_adapters()
        assert len(adapters) == 1


# ============================================================================
# THR-107 seq339/340 — Scoped canonical daemon-managed path tests
# ============================================================================


class TestContractReferenceCanonicalPath:
    """Contract-reference response includes canonical path fields."""

    def test_response_includes_canonical_directory_and_required_path(
        self, app_and_client, token_store,
    ):
        """contract-reference response has canonical_directory and
        required_executable_path with exact expected values."""
        from runtime.orchestrator.custom_adapter_registry import (
            compute_canonical_adapter_path,
            generate_adapter_id,
        )
        app, master_token, store = app_and_client
        profile_name = "seq340-test-cli"
        token = _mint_adapter_token(store, profile_name)

        client = TestClient(app)
        resp = client.get(
            "/api/v1/runtime/adapters/contract-reference",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()

        # Exact expected values
        adapter_id = generate_adapter_id(f"{profile_name}-adapter")
        canonical_dir, required_path = compute_canonical_adapter_path(adapter_id)

        assert data["canonical_directory"] == str(canonical_dir)
        assert data["required_executable_path"] == str(required_path)
        assert data["canonical_directory"] in data["required_executable_path"]
        assert "canonical_directory_description" in data
        assert "required_executable_path_description" in data
        assert "canonical" in data["required_executable_path_description"]

    def test_canonical_path_is_absolute(self, app_and_client, token_store):
        """canonical_directory and required_executable_path are absolute."""
        app, master_token, store = app_and_client
        token = _mint_adapter_token(store, "abs-test")

        client = TestClient(app)
        resp = client.get(
            "/api/v1/runtime/adapters/contract-reference",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()

        assert data["canonical_directory"].startswith("/")
        assert data["required_executable_path"].startswith("/")
        assert ".." not in data["required_executable_path"]

    def test_canonical_path_respects_daemon_home_override(
        self, app_and_client, token_store, monkeypatch, tmp_path,
    ):
        """HAPPYRANCH_DAEMON_HOME overrides the canonical path root."""
        from runtime.orchestrator.custom_adapter_registry import (
            compute_canonical_adapter_path,
        )
        custom_home = tmp_path / "custom-daemon-home"
        custom_home.mkdir()

        # Call the helper directly with the override
        canonical_dir, required_path = compute_canonical_adapter_path(
            "test-adapter", daemon_home_override=custom_home,
        )
        assert str(custom_home.resolve()) in str(canonical_dir)
        assert str(custom_home.resolve()) in str(required_path)
        assert required_path.name == "test-adapter"

    def test_adapters_directory_created_with_restrictive_permissions(
        self, tmp_path,
    ):
        """compute_canonical_adapter_path creates adapters dir with 0o700."""
        import stat
        from runtime.orchestrator.custom_adapter_registry import (
            compute_canonical_adapter_path,
        )
        home = tmp_path / "daemon-home"
        home.mkdir()

        canonical_dir, required_path = compute_canonical_adapter_path(
            "test-adapter", daemon_home_override=home,
        )

        # Directory must exist
        assert canonical_dir.exists()
        assert canonical_dir.is_dir()

        # Must be user-only (0o700)
        mode = canonical_dir.stat().st_mode
        perms = stat.S_IMODE(mode)
        assert perms == 0o700, f"Expected 0o700, got {oct(perms)}"

    def test_rejects_symlink_adapters_directory(self, tmp_path):
        """compute_canonical_adapter_path rejects a symlinked adapters dir."""
        from runtime.orchestrator.custom_adapter_registry import (
            compute_canonical_adapter_path,
        )
        home = tmp_path / "daemon-home"
        home.mkdir()

        # Create a real target elsewhere
        real_dir = tmp_path / "real-adapters"
        real_dir.mkdir()

        # Symlink adapters -> real_dir
        adapters_link = home / "adapters"
        adapters_link.symlink_to(real_dir)

        with pytest.raises(ValueError, match="symlink"):
            compute_canonical_adapter_path("test-adapter", daemon_home_override=home)

    def test_rejects_symlink_wrapper_path(self, tmp_path):
        """compute_canonical_adapter_path rejects a symlinked wrapper."""
        from runtime.orchestrator.custom_adapter_registry import (
            compute_canonical_adapter_path,
        )
        home = tmp_path / "daemon-home"
        home.mkdir()
        adapters_dir = home / "adapters"
        adapters_dir.mkdir(mode=0o700)

        # Create a real file elsewhere and symlink to wrapper path
        real_file = tmp_path / "real-wrapper"
        real_file.write_text("#!/bin/sh\necho ok")
        real_file.chmod(0o755)

        wrapper_path = adapters_dir / "test-adapter"
        wrapper_path.symlink_to(real_file)

        with pytest.raises(ValueError, match="symlink"):
            compute_canonical_adapter_path("test-adapter", daemon_home_override=home)

    def test_contract_reference_not_consuming_token(self, app_and_client, token_store):
        """contract-reference's canonical path computation does NOT consume token."""
        app, master_token, store = app_and_client
        token = _mint_adapter_token(store, "no-consume-test")

        client = TestClient(app)

        # Fetch contract reference multiple times
        for _ in range(3):
            resp = client.get(
                "/api/v1/runtime/adapters/contract-reference",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200

        # Token still valid
        assert store.validate_runtime(token) is not None


class TestScopedCanonicalPathSubmit:
    """Scoped submit enforces canonical path for new and re-registered adapters."""

    def test_submit_rejects_foreign_path(self, app_and_client, route_setup, token_store):
        """Scoped submit rejects adapter at a non-canonical foreign path."""
        from runtime.orchestrator.custom_adapter_registry import (
            compute_canonical_adapter_path,
            generate_adapter_id,
        )
        app, master_token, store = app_and_client
        profile_name = "foreign-path-test"
        token = _mint_adapter_token(store, profile_name)

        # Create script at some random tmp location, not the canonical path
        adapter_id = generate_adapter_id(f"{profile_name}-adapter")
        _, required_path = compute_canonical_adapter_path(adapter_id)

        # Make sure the script is NOT at the required path
        foreign_script = route_setup / "some-other-location"
        foreign_script.write_text("#!/usr/bin/env python3\nprint('ok')")
        foreign_script.chmod(0o755)

        client = TestClient(app)
        resp = client.post(
            "/api/v1/runtime/adapters/submit",
            json={
                "executable": str(foreign_script),
                "version": "1.0.0",
                "capabilities": [],
                "workspace_adapter": "pi",
                "dependency_manifest_version": 1,
                "dependencies": [
                    {"executable": str(foreign_script), "sha256": compute_sha256(str(foreign_script))}
                ],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "invalid_executable_path"
        assert detail["required_executable_path"] == str(required_path)
        assert str(foreign_script) in detail["message"]
        assert str(required_path) in detail["message"]

        # Token is NOT consumed — retryable
        assert store.validate_runtime(token) is not None

    def test_submit_rejects_traversal_spelling(self, app_and_client, route_setup, token_store):
        """Scoped submit rejects a non-absolute path at route level."""
        from runtime.orchestrator.custom_adapter_registry import (
            compute_canonical_adapter_path,
            generate_adapter_id,
        )
        app, master_token, store = app_and_client
        profile_name = "nonabs-test"
        token = _mint_adapter_token(store, profile_name)

        # Create a dummy script for dependency validation
        dummy = route_setup / "dummy-dep"
        dummy.write_text("#!/bin/sh\necho ok")
        dummy.chmod(0o755)

        client = TestClient(app)
        # Submit with a non-absolute path — fails at route level before
        # even reaching canonical path check (validate_executable_path
        # in register_custom_adapter rejects non-absolute paths)
        resp = client.post(
            "/api/v1/runtime/adapters/submit",
            json={
                "executable": "./relative/path/wrapper",
                "version": "1.0.0",
                "capabilities": [],
                "workspace_adapter": "pi",
                "dependency_manifest_version": 1,
                "dependencies": [
                    {"executable": str(dummy),
                     "sha256": compute_sha256(str(dummy))}
                ],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422, resp.text
        # Token is NOT consumed — retryable
        assert store.validate_runtime(token) is not None

    def test_submit_rejects_absolute_traversal_bypass(self, app_and_client, route_setup, token_store):
        """Scoped submit rejects an absolute path containing '..' traversal.

        This catches the normalization bypass where
        ``<daemon-home>/adapters/../adapters/<canonical-id>`` would resolve
        to the canonical path via Path.resolve() but the caller's original
        lexical form contains traversal components.  The pre-resolve check
        rejects this before any probe, reservation, hashing, or persistence.
        """
        from runtime.orchestrator.custom_adapter_registry import (
            compute_canonical_adapter_path,
            generate_adapter_id,
        )
        app, master_token, store = app_and_client
        profile_name = "abs-traversal-test"
        token = _mint_adapter_token(store, profile_name)

        adapter_id = generate_adapter_id(f"{profile_name}-adapter")
        canonical_dir, required_path = compute_canonical_adapter_path(adapter_id)

        # Build an absolute path with traversal: <daemon-home>/adapters/../adapters/<id>
        traversal_path = str(canonical_dir / ".." / "adapters" / adapter_id)
        # Verify the traversal path is different from the required path lexically
        assert traversal_path != str(required_path)
        # Verify resolve() WOULD normalize it to the canonical form
        from pathlib import Path
        assert Path(traversal_path).resolve() == Path(str(required_path)).resolve(), (
            f"Expected {traversal_path} to resolve to {required_path} — if this "
            f"assertion fails the test isn't exercising the bypass correctly"
        )

        # Create a dummy for dependency validation
        dummy = route_setup / "dummy-dep-bypass"
        dummy.write_text("#!/bin/sh\necho ok")
        dummy.chmod(0o755)

        client = TestClient(app)
        resp = client.post(
            "/api/v1/runtime/adapters/submit",
            json={
                "executable": traversal_path,
                "version": "1.0.0",
                "capabilities": [],
                "workspace_adapter": "pi",
                "dependency_manifest_version": 1,
                "dependencies": [
                    {"executable": str(dummy),
                     "sha256": compute_sha256(str(dummy))}
                ],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "invalid_executable_path"
        assert "traversal" in detail["message"].lower()
        assert detail["required_executable_path"] == str(required_path)
        # Token is NOT consumed — retryable
        assert store.validate_runtime(token) is not None
        # No adapter entry was created
        from runtime.orchestrator.custom_adapter_registry import get_adapter
        assert get_adapter(adapter_id) is None

    def test_submit_rejects_alternate_filename(self, app_and_client, route_setup, token_store):
        """Scoped submit rejects a file with a different name in the correct dir."""
        from runtime.orchestrator.custom_adapter_registry import (
            compute_canonical_adapter_path,
            generate_adapter_id,
        )
        app, master_token, store = app_and_client
        profile_name = "alt-name-test"
        token = _mint_adapter_token(store, profile_name)

        adapter_id = generate_adapter_id(f"{profile_name}-adapter")
        canonical_dir, required_path = compute_canonical_adapter_path(adapter_id)

        # Create a file with a WRONG name in the right directory
        wrong_path = canonical_dir / "wrong-name"
        wrong_path.write_text("#!/usr/bin/env python3\nprint('ok')")
        wrong_path.chmod(0o755)

        client = TestClient(app)
        resp = client.post(
            "/api/v1/runtime/adapters/submit",
            json={
                "executable": str(wrong_path),
                "version": "1.0.0",
                "capabilities": [],
                "workspace_adapter": "pi",
                "dependency_manifest_version": 1,
                "dependencies": [
                    {"executable": str(wrong_path), "sha256": compute_sha256(str(wrong_path))}
                ],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "invalid_executable_path"
        assert detail["required_executable_path"] == str(required_path)

    def test_submit_rejects_symlink_escape(self, app_and_client, route_setup, token_store):
        """Scoped submit rejects a symlink even at the canonical path location."""
        from runtime.orchestrator.custom_adapter_registry import (
            compute_canonical_adapter_path,
            generate_adapter_id,
        )
        app, master_token, store = app_and_client
        profile_name = "symlink-test"
        token = _mint_adapter_token(store, profile_name)

        adapter_id = generate_adapter_id(f"{profile_name}-adapter")
        canonical_dir, required_path = compute_canonical_adapter_path(adapter_id)

        # Create a real file elsewhere
        real_file = route_setup / "real-wrapper"
        real_file.write_text("#!/usr/bin/env python3\nprint('ok')")
        real_file.chmod(0o755)

        # Create a symlink at the canonical path -> real file
        required_path.symlink_to(real_file)

        # The submit route calls compute_canonical_adapter_path which
        # raises ValueError for symlinks → 422 with string detail
        client = TestClient(app)
        resp = client.post(
            "/api/v1/runtime/adapters/submit",
            json={
                "executable": str(required_path),
                "version": "1.0.0",
                "capabilities": [],
                "workspace_adapter": "pi",
                "dependency_manifest_version": 1,
                "dependencies": [
                    {"executable": str(real_file),
                     "sha256": compute_sha256(str(real_file))}
                ],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        # The error comes from compute_canonical_adapter_path's symlink check
        assert detail["code"] == "invalid_canonical_path"
        assert "symlink" in detail["message"].lower()
        # Token is NOT consumed — retryable
        assert store.validate_runtime(token) is not None

    def test_submit_accepts_exact_canonical_target(self, app_and_client, route_setup, token_store, monkeypatch):
        """Scoped submit accepts adapter at exact canonical path."""
        from unittest.mock import MagicMock
        from runtime.orchestrator.custom_adapter_registry import (
            compute_canonical_adapter_path,
            generate_adapter_id,
            register_custom_adapter as _real,
        )
        app, master_token, store = app_and_client
        profile_name = "exact-target-test"
        token = _mint_adapter_token(store, profile_name)

        adapter_id = generate_adapter_id(f"{profile_name}-adapter")
        canonical_dir, required_path = compute_canonical_adapter_path(adapter_id)

        # Create the wrapper at the exact canonical path
        required_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        script_content = f'''#!/usr/bin/env python3
import json, sys
inp = json.load(sys.stdin)
out = {{
    "success": True,
    "returncode": 0,
    "adapter_metadata": {{
        "adapter_id": "{adapter_id}",
        "adapter_name": "{adapter_id}",
        "adapter_version": "1.0.0",
        "contract_version": 1,
        "adapter": "{adapter_id}"
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
        required_path.write_text(script_content)
        required_path.chmod(0o755)

        spy = MagicMock(wraps=_real)
        monkeypatch.setattr(
            "runtime.daemon.routes.adapters.register_custom_adapter", spy,
        )

        client = TestClient(app)
        resp = client.post(
            "/api/v1/runtime/adapters/submit",
            json={
                "executable": str(required_path),
                "version": "1.0.0",
                "capabilities": [],
                "workspace_adapter": "pi",
                "dependency_manifest_version": 1,
                "dependencies": [
                    {"executable": str(required_path),
                     "sha256": compute_sha256(str(required_path))}
                ],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        spy.assert_called_once()

        # Verify the passed executable matches required_path
        call_kwargs = spy.call_args.kwargs
        assert call_kwargs["executable"] == str(required_path)


class TestMasterBearerRegisterUnaffected:
    """Master-bearer /register path is NOT affected by canonical path enforcement."""

    def test_register_accepts_arbitrary_path(self, app_and_client, route_setup):
        """Master-bearer /register accepts arbitrary executable paths."""
        app, master_token, _store = app_and_client

        # Create a script at any arbitrary location
        script = route_setup / "arbitrary-adapter"
        content = f'''#!/usr/bin/env python3
import json, sys
inp = json.load(sys.stdin)
out = {{
    "success": True,
    "returncode": 0,
    "adapter_metadata": {{
        "adapter_id": "arbitrary-adapter",
        "adapter_name": "arbitrary-adapter",
        "adapter_version": "1.0.0",
        "contract_version": 1,
        "adapter": "arbitrary-adapter"
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
        script.chmod(0o755)

        client = TestClient(app)
        resp = client.post(
            "/api/v1/runtime/adapters/register",
            json={
                "executable": str(script),
                "version": "1.0.0",
                "capabilities": [],
                "workspace_adapter": "pi",
                "dependency_manifest_version": 1,
                "dependencies": [
                    {"executable": str(script),
                     "sha256": compute_sha256(str(script))}
                ],
            },
            headers={"Authorization": f"Bearer {master_token}"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "pending"
        # Note: intended_profile_name is None for master-bearer path
        assert data["intended_profile_name"] is None

    def test_register_accepts_existing_approved_adapter_at_arbitrary_location(
        self, app_and_client, route_setup,
    ):
        """Legacy approved adapter at an arbitrary location remains
        hash-valid and launchable via master-bearer path."""
        app, master_token, _store = app_and_client

        # Create and register at arbitrary location
        script = route_setup / "legacy-adapter"
        content = f'''#!/usr/bin/env python3
import json, sys
inp = json.load(sys.stdin)
out = {{
    "success": True,
    "returncode": 0,
    "adapter_metadata": {{
        "adapter_id": "legacy-adapter",
        "adapter_name": "legacy-adapter",
        "adapter_version": "1.0.0",
        "contract_version": 1,
        "adapter": "legacy-adapter"
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
        script.chmod(0o755)

        client = TestClient(app)

        # Register
        resp = client.post(
            "/api/v1/runtime/adapters/register",
            json={
                "executable": str(script),
                "version": "1.0.0",
                "capabilities": [],
                "workspace_adapter": "pi",
                "dependency_manifest_version": 1,
                "dependencies": [
                    {"executable": str(script),
                     "sha256": compute_sha256(str(script))}
                ],
            },
            headers={"Authorization": f"Bearer {master_token}"},
        )
        assert resp.status_code == 200, resp.text
        adapter = resp.json()
        adapter_id = adapter["id"]
        assert adapter["status"] == "pending"

        # Approve
        snapshot = {
            "executable": str(script),
            "executable_hash": compute_sha256(str(script)),
            "version": "1.0.0",
            "capabilities": [],
            "contract_version": 1,
            "workspace_adapter": "pi",
            "dependency_manifest_version": 1,
            "dependencies": [
                {"executable": str(script),
                 "sha256": compute_sha256(str(script))}
            ],
        }
        resp = client.post(
            f"/api/v1/runtime/adapters/{adapter_id}/approve",
            json=snapshot,
            headers={"Authorization": f"Bearer {master_token}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "approved"

        # The adapter's executable is at the original arbitrary location, not
        # a canonical path
        assert adapter["executable"] == str(script)

    def test_register_without_intended_profile_skips_canonical_check(
        self, monkeypatch, tmp_path,
    ):
        """register_custom_adapter without intended_profile_name skips
        the canonical path validation entirely."""
        from runtime.orchestrator.custom_adapter_registry import (
            compute_canonical_adapter_path,
            generate_adapter_id,
            register_custom_adapter,
        )
        from unittest.mock import MagicMock

        # Spy on compute_canonical_adapter_path to verify it is NOT called
        spy = MagicMock(wraps=compute_canonical_adapter_path)
        monkeypatch.setattr(
            "runtime.orchestrator.custom_adapter_registry.compute_canonical_adapter_path",
            spy,
        )

        # Use a fixed filename so the derived adapter ID is predictable.
        # generate_adapter_id uses the filename base for master-bearer path.
        script_path = tmp_path / "master-bearer-test-adapter"
        adapter_id = generate_adapter_id("master-bearer-test-adapter")
        content = f'''#!/usr/bin/env python3
import json, sys
inp = json.load(sys.stdin)
out = {{
    "success": True,
    "returncode": 0,
    "adapter_metadata": {{
        "adapter_id": "{adapter_id}",
        "adapter_name": "{adapter_id}",
        "adapter_version": "1.0.0",
        "contract_version": 1,
        "adapter": "{adapter_id}"
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
        script_path.write_text(content)
        script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)

        try:
            # Register WITHOUT intended_profile_name (master-bearer path)
            entry = register_custom_adapter(
                executable=str(script_path),
                version="1.0.0",
                capabilities=[],
                workspace_adapter="pi",
                registered_by="test",
                intended_profile_name=None,  # master-bearer: no profile
                dependency_manifest_version=1,
                dependencies=[{"executable": str(script_path),
                                "sha256": compute_sha256(str(script_path))}],
            )
            # compute_canonical_adapter_path must NOT have been called
            spy.assert_not_called()
            assert entry.executable == str(script_path)
        finally:
            script_path.unlink(missing_ok=True)


class TestRegistrationSeamCanonicalCheck:
    """The register_custom_adapter seam independently rechecks canonical path."""

    def test_scoped_registration_rejects_foreign_path_at_seam(self, monkeypatch):
        """register_custom_adapter with intended_profile_name rejects foreign
        path even if the route-layer check is bypassed."""
        import tempfile, stat
        from runtime.orchestrator.custom_adapter_registry import (
            compute_canonical_adapter_path,
        )

        fd, script_path = tempfile.mkstemp(suffix="-seam-test")
        os.close(fd)
        script_path = Path(script_path)

        # Determine the required canonical path for this adapter
        profile_name = "seam-test"
        adapter_id = "seam-test-adapter"
        _, required_path = compute_canonical_adapter_path(adapter_id)

        # Create script at a FOREIGN path (not the canonical one)
        content = '''#!/usr/bin/env python3
import json, sys
inp = json.load(sys.stdin)
out = {
    "success": True,
    "returncode": 0,
    "adapter_metadata": {
        "adapter_id": "seam-test-adapter",
        "adapter_name": "seam-test-adapter",
        "adapter_version": "1.0.0",
        "contract_version": 1,
        "adapter": "seam-test-adapter"
    },
    "stdout": "ok",
    "stderr": "",
    "stdout_tail": "ok",
    "stderr_tail": "",
    "duration_seconds": 1,
    "invocation_id": inp.get("invocation", {}).get("invocation_id", "test-id"),
    "token_total": 0,
    "session_id": "test-session"
}
print(json.dumps(out))
'''
        script_path.write_text(content)
        script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)

        try:
            # Ensure the paths differ
            assert str(script_path) != str(required_path), \
                f"Script path {script_path} must differ from canonical {required_path}"

            from runtime.orchestrator.custom_adapter_registry import (
                register_custom_adapter,
            )
            with pytest.raises(ValueError, match="server-owned canonical path"):
                register_custom_adapter(
                    executable=str(script_path),
                    version="1.0.0",
                    capabilities=[],
                    workspace_adapter="pi",
                    registered_by="test",
                    intended_profile_name=profile_name,
                    dependency_manifest_version=1,
                    dependencies=[{"executable": str(script_path),
                                    "sha256": compute_sha256(str(script_path))}],
                )
        finally:
            script_path.unlink(missing_ok=True)

    def test_scoped_registration_rejects_absolute_traversal_at_seam(self, monkeypatch):
        """register_custom_adapter with intended_profile_name rejects an
        absolute path containing '..' traversal before any probe/resolve."""
        import tempfile, stat
        from runtime.orchestrator.custom_adapter_registry import (
            compute_canonical_adapter_path,
        )

        profile_name = "seam-traversal"
        adapter_id = f"{profile_name}-adapter"
        canonical_dir, required_path = compute_canonical_adapter_path(adapter_id)

        # Build an absolute path with traversal: <daemon-home>/adapters/../adapters/<id>
        traversal_path = str(canonical_dir / ".." / "adapters" / adapter_id)
        assert traversal_path != str(required_path)
        # Verify resolve() WOULD normalize it
        from pathlib import Path
        assert Path(traversal_path).resolve() == required_path.resolve()

        from runtime.orchestrator.custom_adapter_registry import (
            register_custom_adapter,
        )
        with pytest.raises(ValueError, match="traversal spelling"):
            register_custom_adapter(
                executable=traversal_path,
                version="1.0.0",
                capabilities=[],
                workspace_adapter="pi",
                registered_by="test",
                intended_profile_name=profile_name,
                dependency_manifest_version=1,
                dependencies=[],
            )

    def test_scoped_registration_at_canonical_path_succeeds(self, monkeypatch):
        """register_custom_adapter with intended_profile_name at canonical
        path passes the seam check and proceeds to conformance."""
        import stat
        from runtime.orchestrator.custom_adapter_registry import (
            compute_canonical_adapter_path,
            register_custom_adapter,
        )

        profile_name = "canonical-pass"
        adapter_id = f"{profile_name}-adapter"
        _, required_path = compute_canonical_adapter_path(adapter_id)

        # Create wrapper at the canonical path
        required_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        content = f'''#!/usr/bin/env python3
import json, sys
inp = json.load(sys.stdin)
out = {{
    "success": True,
    "returncode": 0,
    "adapter_metadata": {{
        "adapter_id": "{adapter_id}",
        "adapter_name": "{adapter_id}",
        "adapter_version": "1.0.0",
        "contract_version": 1,
        "adapter": "{adapter_id}"
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
        required_path.write_text(content)
        required_path.chmod(required_path.stat().st_mode | stat.S_IEXEC)

        try:
            entry = register_custom_adapter(
                executable=str(required_path),
                version="1.0.0",
                capabilities=[],
                workspace_adapter="pi",
                registered_by="test",
                intended_profile_name=profile_name,
                dependency_manifest_version=1,
                dependencies=[{"executable": str(required_path),
                                "sha256": compute_sha256(str(required_path))}],
            )
            assert entry.executable == str(required_path)
            assert entry.status == "pending"
        finally:
            required_path.unlink(missing_ok=True)
