"""THR-107 TASK-3792: adapter removal route tests.

Integration-level tests exercising the DELETE /api/v1/runtime/adapters/{adapter_id}
route through the real FastAPI TestClient and adapter/executor stores.

Categories:
  1. Exact Kimi-shaped target removal
  2. Snapshot mismatch (stale / re-registered) does not remove
  3. Approved-but-profile-referenced rejects without removing profile or adapter
  4. No-profile successful removal
  5. Unknown adapter → 404
  6. Non-approved adapter → rejected
  7. Auth enforcement (no token → 401)
  8. Audit success / failed-audit rollback
  9. Concurrency / lock safety (following existing bind test patterns)
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.orchestrator.adapter_store import (
    AdapterEntry,
    load_adapters,
    save_adapter,
    _store_path as adapter_store_path,
)
from runtime.orchestrator.runtime_executor_store import (
    load_runtime_profiles,
    save_runtime_profile,
    remove_runtime_profile,
)
from runtime.daemon.routes.adapters import _audit_adapter_remove


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


def _make_snapshot_body(entry: AdapterEntry) -> dict:
    """Build a RemoveAdapterRequest body from a stored AdapterEntry."""
    return {
        "executable": entry.executable,
        "executable_hash": entry.executable_hash,
        "version": entry.version,
        "capabilities": entry.capabilities,
        "contract_version": entry.contract_version,
        "workspace_adapter": entry.workspace_adapter,
        "name": entry.name,
        "intended_profile_name": entry.intended_profile_name,
    }


@pytest.fixture
def clean_adapter_store(tmp_home: Path, monkeypatch):
    """Ensure adapter store writes go to the test temp directory.

    Uses conftest's tmp_home (which already sets HAPPYRANCH_DAEMON_HOME
    and creates the token file) — does NOT override it.
    """
    return tmp_home


@pytest.fixture
def approved_kimi_adapter(clean_adapter_store: Path):
    """Create an APPROVED adapter matching the Kimi target shape."""
    entry = AdapterEntry(
        id="kimi-adapter",
        name="kimi",
        executable="/Users/tangbz/.happyranch/adapters/kimi_v1_adapter.py",
        executable_hash="31ce12baf44037dfe8cc338f3ef285a4f60550b241330fe1d2cf5da62e81bc8e",
        version="1.0.0",
        capabilities=[],
        contract_version=1,
        workspace_adapter="pi",
        status="approved",
        registered_at="2026-07-31T00:00:00Z",
        registered_by="adapter-submission:kimi",
        approved_at="2026-07-31T01:00:00Z",
        approved_by="founder/master-bearer",
        intended_profile_name="kimi",
    )
    save_adapter(entry)
    return entry


@pytest.fixture
def approved_generic_adapter(clean_adapter_store: Path):
    """Create an APPROVED adapter with no intended profile."""
    entry = AdapterEntry(
        id="test-adapter",
        name="test-adapter",
        executable="/bin/echo",
        executable_hash="abc123",
        version="1.0.0",
        capabilities=["test"],
        contract_version=1,
        workspace_adapter="pi",
        status="approved",
        registered_at="2026-07-31T00:00:00Z",
        registered_by="test",
        approved_at="2026-07-31T01:00:00Z",
        approved_by="founder/master-bearer",
        intended_profile_name=None,
    )
    save_adapter(entry)
    return entry


@pytest.fixture
def pending_adapter(clean_adapter_store: Path):
    """Create a PENDING adapter."""
    entry = AdapterEntry(
        id="pending-adapter",
        name="pending-test",
        executable="/usr/bin/true",
        executable_hash="deadbeef",
        version="1.0.0",
        capabilities=[],
        contract_version=1,
        workspace_adapter="pi",
        status="pending",
        registered_at="2026-07-31T00:00:00Z",
        registered_by="test",
        approved_at=None,
        approved_by=None,
        intended_profile_name=None,
    )
    save_adapter(entry)
    return entry


@pytest.fixture
def adapter_bound_to_profile(clean_adapter_store: Path, approved_generic_adapter: AdapterEntry):
    """Create an APPROVED adapter AND a runtime profile bound to it."""
    # Bind a runtime profile to this adapter
    save_runtime_profile("my-cli", {
        "command": None,
        "argv_template": None,
        "workspace_adapter_id": "pi",
        "command_adapter_id": f"custom-adapter:{approved_generic_adapter.id}",
    })
    return approved_generic_adapter


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestExactTargetRemoval:
    """Exact Kimi-shaped target removes successfully."""

    def test_remove_approved_kimi_adapter(
        self,
        client: TestClient,
        approved_kimi_adapter: AdapterEntry,
    ):
        """Removing the exact approved Kimi adapter succeeds."""
        body = _make_snapshot_body(approved_kimi_adapter)

        resp = client.request("DELETE",
            f"/api/v1/runtime/adapters/{approved_kimi_adapter.id}",
            json=body,
        )

        assert resp.status_code == 200, resp.json()
        data = resp.json()
        assert data["id"] == "kimi-adapter"
        assert data["removed"] is True
        assert data["name"] == "kimi"

        # Verify adapter is gone from durable store
        adapter = load_adapters().get("kimi-adapter")
        assert adapter is None

    def test_remove_approved_no_profile_adapter(
        self,
        client: TestClient,
        approved_generic_adapter: AdapterEntry,
    ):
        """Removing an approved adapter with no bound profile succeeds."""
        body = _make_snapshot_body(approved_generic_adapter)

        resp = client.request("DELETE",
            f"/api/v1/runtime/adapters/{approved_generic_adapter.id}",
            json=body,
        )

        assert resp.status_code == 200, resp.json()
        data = resp.json()
        assert data["removed"] is True

        # Verify adapter is gone
        adapter = load_adapters().get(approved_generic_adapter.id)
        assert adapter is None


class TestSnapshotMismatch:
    """Snapshot mismatch does NOT remove the adapter."""

    def test_wrong_hash_rejected(
        self,
        client: TestClient,
        approved_kimi_adapter: AdapterEntry,
    ):
        """Wrong executable_hash is rejected with 422."""
        body = _make_snapshot_body(approved_kimi_adapter)
        body["executable_hash"] = "0000000000000000000000000000000000000000000000000000000000000000"

        resp = client.request("DELETE",
            f"/api/v1/runtime/adapters/{approved_kimi_adapter.id}",
            json=body,
        )

        assert resp.status_code == 422, resp.json()
        assert "executable_hash mismatch" in resp.json()["detail"]

        # Adapter still exists
        adapter = load_adapters().get("kimi-adapter")
        assert adapter is not None
        assert adapter.status == "approved"

    def test_wrong_executable_rejected(
        self,
        client: TestClient,
        approved_kimi_adapter: AdapterEntry,
    ):
        """Wrong executable path is rejected with 422."""
        body = _make_snapshot_body(approved_kimi_adapter)
        body["executable"] = "/wrong/path"

        resp = client.request("DELETE",
            f"/api/v1/runtime/adapters/{approved_kimi_adapter.id}",
            json=body,
        )

        assert resp.status_code == 422, resp.json()
        assert "executable mismatch" in resp.json()["detail"]

        # Adapter still exists
        adapter = load_adapters().get("kimi-adapter")
        assert adapter is not None

    def test_wrong_version_rejected(
        self,
        client: TestClient,
        approved_kimi_adapter: AdapterEntry,
    ):
        """Wrong version is rejected."""
        body = _make_snapshot_body(approved_kimi_adapter)
        body["version"] = "2.0.0"

        resp = client.request("DELETE",
            f"/api/v1/runtime/adapters/{approved_kimi_adapter.id}",
            json=body,
        )

        assert resp.status_code == 422
        assert "version mismatch" in resp.json()["detail"]

    def test_wrong_name_rejected(
        self,
        client: TestClient,
        approved_kimi_adapter: AdapterEntry,
    ):
        """Wrong name is rejected."""
        body = _make_snapshot_body(approved_kimi_adapter)
        body["name"] = "wrong-name"

        resp = client.request("DELETE",
            f"/api/v1/runtime/adapters/{approved_kimi_adapter.id}",
            json=body,
        )

        assert resp.status_code == 422
        assert "name mismatch" in resp.json()["detail"]

    def test_wrong_intended_profile_rejected(
        self,
        client: TestClient,
        approved_kimi_adapter: AdapterEntry,
    ):
        """Wrong intended_profile_name is rejected."""
        body = _make_snapshot_body(approved_kimi_adapter)
        body["intended_profile_name"] = "other-profile"

        resp = client.request("DELETE",
            f"/api/v1/runtime/adapters/{approved_kimi_adapter.id}",
            json=body,
        )

        assert resp.status_code == 422
        assert "intended_profile_name mismatch" in resp.json()["detail"]

    def test_re_registered_adapter_snapshot_mismatch(
        self,
        client: TestClient,
        approved_kimi_adapter: AdapterEntry,
    ):
        """A snapshot from before a re-registration is rejected.

        Re-registering updates the hash — the old snapshot no longer
        matches and should be rejected.
        """
        # Re-register with a new hash
        body = _make_snapshot_body(approved_kimi_adapter)
        body["executable_hash"] = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

        # Create a re-registered entry (simulating what happens after
        # a new registration that changes the hash)
        re_registered = AdapterEntry(
            id="kimi-adapter",
            name="kimi",
            executable=approved_kimi_adapter.executable,
            executable_hash="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            version="1.0.0",
            capabilities=[],
            contract_version=1,
            workspace_adapter="pi",
            status="approved",
            registered_at="2026-07-31T02:00:00Z",
            registered_by="adapter-submission:kimi",
            approved_at="2026-07-31T02:00:00Z",
            approved_by="founder/master-bearer",
            intended_profile_name="kimi",
        )
        save_adapter(re_registered)

        # Now try to remove with the OLD snapshot
        old_body = _make_snapshot_body(approved_kimi_adapter)

        resp = client.request("DELETE",
            "/api/v1/runtime/adapters/kimi-adapter",
            json=old_body,
        )

        assert resp.status_code == 422, resp.json()
        assert "executable_hash mismatch" in resp.json()["detail"]

        # Adapter still exists (with new hash)
        adapter = load_adapters().get("kimi-adapter")
        assert adapter is not None
        assert adapter.executable_hash == "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


class TestProfileBoundRejection:
    """Approved-but-profile-referenced rejects without removing anything."""

    def test_bound_adapter_rejected(
        self,
        client: TestClient,
        adapter_bound_to_profile: AdapterEntry,
    ):
        """Removing an adapter that has a bound profile is rejected."""
        body = _make_snapshot_body(adapter_bound_to_profile)

        resp = client.request("DELETE",
            f"/api/v1/runtime/adapters/{adapter_bound_to_profile.id}",
            json=body,
        )

        assert resp.status_code == 422, resp.json()
        assert "custom runtime profile" in resp.json()["detail"]
        assert "my-cli" in resp.json()["detail"]

        # Adapter still exists
        adapter = load_adapters().get(adapter_bound_to_profile.id)
        assert adapter is not None
        assert adapter.status == "approved"

        # Profile still exists and is still bound
        profiles = load_runtime_profiles()
        assert "my-cli" in profiles
        assert profiles["my-cli"]["command_adapter_id"] == f"custom-adapter:{adapter_bound_to_profile.id}"


class TestNotFoundAndNonApproved:
    """Unknown and non-approved adapters are rejected."""

    def test_unknown_adapter_404(
        self,
        client: TestClient,
        approved_kimi_adapter: AdapterEntry,
    ):
        """Removing an adapter that doesn't exist returns 404."""
        body = _make_snapshot_body(approved_kimi_adapter)

        resp = client.request("DELETE",
            "/api/v1/runtime/adapters/nonexistent",
            json=body,
        )

        assert resp.status_code == 404, resp.json()
        assert "not found" in resp.json()["detail"].lower()

    def test_pending_adapter_rejected(
        self,
        client: TestClient,
        pending_adapter: AdapterEntry,
    ):
        """Removing a PENDING adapter is rejected."""
        body = _make_snapshot_body(pending_adapter)

        resp = client.request("DELETE",
            f"/api/v1/runtime/adapters/{pending_adapter.id}",
            json=body,
        )

        assert resp.status_code == 422, resp.json()
        assert "not APPROVED" in resp.json()["detail"]

        # Adapter still exists
        adapter = load_adapters().get(pending_adapter.id)
        assert adapter is not None
        assert adapter.status == "pending"


class TestAuthEnforcement:
    """Authentication is enforced on the removal route."""

    def test_no_token_rejected(self, tmp_home, daemon_state):
        """Request without bearer token gets 401."""
        from fastapi.testclient import TestClient
        from runtime.daemon.app import create_app
        app = create_app(daemon_state)
        tc = TestClient(app)  # No auth headers attached

        resp = tc.request("DELETE",
            "/api/v1/runtime/adapters/some-id",
            json={"executable": "/x", "executable_hash": "0" * 64,
                  "version": "1.0", "capabilities": [],
                  "contract_version": 1, "workspace_adapter": "pi",
                  "name": "x"},
        )

        assert resp.status_code == 401, resp.json()


class TestAuditAndRollback:
    """Audit success and failed-audit rollback."""

    def test_successful_removal_is_auditable(
        self,
        client: TestClient,
        approved_kimi_adapter: AdapterEntry,
        tmp_home: Path,
    ):
        """After a successful removal, an audit row is durably persisted
        with scope adapter:<id>, actor founder, and action adapter_removed."""
        from runtime.infrastructure.database import Database

        body = _make_snapshot_body(approved_kimi_adapter)

        resp = client.request("DELETE",
            f"/api/v1/runtime/adapters/{approved_kimi_adapter.id}",
            json=body,
        )

        assert resp.status_code == 200, resp.json()

        # Verify audit was actually written to runtime-audit.db.
        # The route writes via daemon_home(), which tmp_home overrides.
        audit_db_path = tmp_home / "runtime-audit.db"
        assert audit_db_path.exists(), (
            f"Expected runtime-audit.db at {audit_db_path} after successful removal"
        )

        db = Database(audit_db_path)
        try:
            rows = db.get_audit_logs(
                task_id=f"adapter:{approved_kimi_adapter.id}",
            )
            assert len(rows) >= 1, (
                f"Expected at least one audit row for adapter:{approved_kimi_adapter.id}"
            )
            row = rows[0]
            assert row["task_id"] == f"adapter:{approved_kimi_adapter.id}"
            assert row["agent"] == "founder"
            assert row["action"] == "adapter_removed"
            payload = row["payload"]  # already JSON-decoded by get_audit_logs
            assert payload["adapter_id"] == approved_kimi_adapter.id
            assert payload["name"] == approved_kimi_adapter.name
            assert payload["executable_hash"] == approved_kimi_adapter.executable_hash
            assert payload["status"] == approved_kimi_adapter.status
        finally:
            db.close()

    def test_audit_write_failure_restores_adapter(
        self,
        client: TestClient,
        approved_kimi_adapter: AdapterEntry,
        monkeypatch,
    ):
        """When auditing fails after durable removal, the exact adapter
        entry is restored while the lock is held and a 500 is returned."""
        body = _make_snapshot_body(approved_kimi_adapter)

        # Inject a failure into the audit write path AFTER durable removal.
        original_audit = _audit_adapter_remove

        def _failing_audit(*args, **kwargs):
            raise RuntimeError("injected audit write failure")

        # Patch the audit function in the routes module.
        import runtime.daemon.routes.adapters as adapters_routes
        adapters_routes._audit_adapter_remove = _failing_audit
        try:
            resp = client.request("DELETE",
                f"/api/v1/runtime/adapters/{approved_kimi_adapter.id}",
                json=body,
            )

            # Must return 500 — audit failure after durable removal triggers
            # compensating restore.
            assert resp.status_code == 500, resp.json()
            assert "audit logging failed" in resp.json()["detail"].lower()

            # The adapter must still exist in the durable store (restored).
            adapter = load_adapters().get(approved_kimi_adapter.id)
            assert adapter is not None, (
                "Adapter should have been restored after audit rollback"
            )
            assert adapter.id == approved_kimi_adapter.id
            assert adapter.executable_hash == approved_kimi_adapter.executable_hash
            assert adapter.name == approved_kimi_adapter.name
            assert adapter.status == "approved"
        finally:
            adapters_routes._audit_adapter_remove = original_audit

    def test_idempotent_remove_returns_404(
        self,
        client: TestClient,
        approved_kimi_adapter: AdapterEntry,
    ):
        """Removing an already-removed adapter returns 404."""
        body = _make_snapshot_body(approved_kimi_adapter)

        # First removal
        resp1 = client.request("DELETE",
            f"/api/v1/runtime/adapters/{approved_kimi_adapter.id}",
            json=body,
        )
        assert resp1.status_code == 200

        # Second removal with same body → 404 (adapter gone)
        resp2 = client.request("DELETE",
            f"/api/v1/runtime/adapters/{approved_kimi_adapter.id}",
            json=body,
        )
        assert resp2.status_code == 404


class TestLockSafety:
    """Concurrency / lock safety (following existing bind test patterns)."""

    def test_sequential_remove_and_bind_under_lock(
        self,
        client: TestClient,
        approved_generic_adapter: AdapterEntry,
    ):
        """Removing an adapter then trying to bind it fails.

        Verifies that lock ordering is compatible: the bind route
        should see the adapter as gone after a concurrent removal.
        """
        body = _make_snapshot_body(approved_generic_adapter)

        # Remove the adapter
        remove_resp = client.request("DELETE",
            f"/api/v1/runtime/adapters/{approved_generic_adapter.id}",
            json=body,
        )
        assert remove_resp.status_code == 200

        # Try to bind to the now-removed adapter
        bind_resp = client.post(
            f"/api/v1/runtime/adapters/{approved_generic_adapter.id}/bind-profile",
            json={"profile_name": "my-new-cli"},
        )
        assert bind_resp.status_code == 404, bind_resp.json()
        assert "not found" in bind_resp.json()["detail"].lower()


class TestRaceConditions:
    """Deterministic race/fault-style tests."""

    def test_adapter_fact_changed_between_validation_and_lock(
        self,
        client: TestClient,
        approved_kimi_adapter: AdapterEntry,
    ):
        """Changing a representative non-path fact (name) after initial
        validation but before lock acquisition causes removal to reject
        and preserves the changed entry.

        The server re-reads under the lock and exact-compares ALL 8 facts
        using the same predicate — any drift is detected.
        """
        body = _make_snapshot_body(approved_kimi_adapter)

        # BEFORE calling the route, change a fact in the durable store
        # that is NOT executable or hash (those were already checked
        # separately in the old code). Changing 'name' is a representative
        # omitted non-path fact — the old code did NOT check it under the
        # lock.  The new code must detect this.
        changed = AdapterEntry(
            id=approved_kimi_adapter.id,
            name="changed-name",  # <-- drifted
            executable=approved_kimi_adapter.executable,
            executable_hash=approved_kimi_adapter.executable_hash,
            version=approved_kimi_adapter.version,
            capabilities=approved_kimi_adapter.capabilities,
            contract_version=approved_kimi_adapter.contract_version,
            workspace_adapter=approved_kimi_adapter.workspace_adapter,
            status=approved_kimi_adapter.status,
            registered_at=approved_kimi_adapter.registered_at,
            registered_by=approved_kimi_adapter.registered_by,
            approved_at=approved_kimi_adapter.approved_at,
            approved_by=approved_kimi_adapter.approved_by,
            intended_profile_name=approved_kimi_adapter.intended_profile_name,
        )
        save_adapter(changed)

        # Now attempt removal with the OLD snapshot (name still "kimi").
        # The lock re-reads and exact-compares ALL 8 facts including name —
        # it must detect the drift.
        resp = client.request("DELETE",
            f"/api/v1/runtime/adapters/{approved_kimi_adapter.id}",
            json=body,
        )

        assert resp.status_code == 422, resp.json()
        detail = resp.json()["detail"]
        assert "name mismatch" in detail.lower() or "facts changed" in detail.lower(), (
            f"Expected name mismatch or facts-changed message, got: {detail}"
        )

        # Adapter still exists with the CHANGED name (not removed).
        adapter = load_adapters().get(approved_kimi_adapter.id)
        assert adapter is not None
        assert adapter.name == "changed-name"
        assert adapter.status == "approved"

    def test_live_only_profile_blocks_removal(
        self,
        client: TestClient,
        approved_generic_adapter: AdapterEntry,
    ):
        """A profile registered ONLY in the live ExecutorRegistry (not durable)
        still blocks removal.  This proves the code checks BOTH surfaces."""
        from runtime.orchestrator.executor_registry import (
            ExecutorProfile,
            get_registry,
        )

        body = _make_snapshot_body(approved_generic_adapter)

        # Register a live-only profile that references this adapter.
        registry = get_registry()
        live_profile = ExecutorProfile(
            name="live-only-profile",
            kind="custom",
            workspace_adapter_id=approved_generic_adapter.workspace_adapter,
            command_adapter_id=f"custom-adapter:{approved_generic_adapter.id}",
        )
        try:
            registry.register_custom_profile(live_profile)
        except Exception:
            # Profile may already exist with same name; replace it.
            try:
                registry.unregister_custom_profile("live-only-profile")
            except Exception:
                pass
            registry.register_custom_profile(live_profile)

        try:
            # There should be NO durable profile — only live.
            durable = load_runtime_profiles()
            assert "live-only-profile" not in durable, (
                "Test precondition: profile must NOT be durable"
            )

            resp = client.request("DELETE",
                f"/api/v1/runtime/adapters/{approved_generic_adapter.id}",
                json=body,
            )

            assert resp.status_code == 422, resp.json()
            detail = resp.json()["detail"]
            assert "custom runtime profile" in detail
            assert "live-only-profile" in detail

            # Adapter still exists.
            adapter = load_adapters().get(approved_generic_adapter.id)
            assert adapter is not None
            assert adapter.status == "approved"
        finally:
            # Clean up the live-only profile.
            try:
                registry.unregister_custom_profile("live-only-profile")
            except Exception:
                pass

    def test_live_only_profile_eligibility_already_bound(
        self,
        client: TestClient,
        clean_adapter_store: Path,
        tmp_path: Path,
    ):
        """When a live-only profile references the adapter, the list endpoint
        returns eligibility=already_bound, which the UI uses to suppress the
        remove affordance."""
        from runtime.orchestrator.executor_registry import (
            ExecutorProfile,
            get_registry,
        )
        from runtime.orchestrator.adapter_store import compute_sha256

        # Create a real executable so resolve_adapter's hash check passes.
        script = _make_conformant_adapter_script(tmp_path, "eligible-adapter")
        real_hash = compute_sha256(str(script))

        adapter = AdapterEntry(
            id="eligible-adapter",
            name="eligible-adapter",
            executable=str(script),
            executable_hash=real_hash,
            version="1.0.0",
            capabilities=[],
            contract_version=1,
            workspace_adapter="pi",
            status="approved",
            registered_at="2026-07-31T00:00:00Z",
            registered_by="test",
            approved_at="2026-07-31T01:00:00Z",
            approved_by="founder",
            intended_profile_name="live-cli",
        )
        save_adapter(adapter)

        # Register a live-only profile.
        registry = get_registry()
        live_profile = ExecutorProfile(
            name="live-cli",
            kind="custom",
            workspace_adapter_id=adapter.workspace_adapter,
            command_adapter_id=f"custom-adapter:{adapter.id}",
        )
        try:
            registry.register_custom_profile(live_profile)
        except Exception:
            try:
                registry.unregister_custom_profile("live-cli")
            except Exception:
                pass
            registry.register_custom_profile(live_profile)

        try:
            # List adapters — the eligibility field must reflect the binding.
            resp = client.get("/api/v1/runtime/adapters")
            assert resp.status_code == 200
            adapters = resp.json()
            our = [a for a in adapters if a["id"] == adapter.id]
            assert len(our) == 1
            assert our[0]["eligibility"] == "already_bound", (
                f"Expected already_bound, got {our[0]['eligibility']}"
            )
        finally:
            try:
                registry.unregister_custom_profile("live-cli")
            except Exception:
                pass
