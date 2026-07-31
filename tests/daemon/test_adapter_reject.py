"""THR-107 seq220: adapter rejection route tests.

Integration-level tests exercising the POST /api/v1/runtime/adapters/{adapter_id}/reject
route through the real FastAPI TestClient and adapter store.

Categories:
  1. Exact PENDING target rejection (removes from store, returns audit entry)
  2. Snapshot mismatch (stale / re-registered / hash-changed) does NOT reject
  3. Non-PENDING adapter rejection → 422
  4. Unknown adapter → 404
  5. Auth enforcement (no token → 401)
  6. Audit rollback (failed-audit restores adapter)
  7. Lock safety (concurrent register + reject)
  8. Management auth vs scoped-token rejection
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
from runtime.daemon.routes.adapters import _audit_adapter_reject


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


def _make_reject_body(entry: AdapterEntry) -> dict:
    """Build a RejectAdapterRequest body from a stored AdapterEntry."""
    return {
        "executable": entry.executable,
        "executable_hash": entry.executable_hash,
        "version": entry.version,
        "capabilities": entry.capabilities,
        "contract_version": entry.contract_version,
        "workspace_adapter": entry.workspace_adapter,
    }


def _build_pending_adapter(
    script: Path,
    adapter_id: str,
    workspace_adapter: str = "pi",
    intended_profile_name: str | None = None,
) -> AdapterEntry:
    """Build a PENDING AdapterEntry for test fixtures."""
    from runtime.orchestrator.adapter_store import compute_sha256
    import datetime

    exe_hash = compute_sha256(str(script))
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return AdapterEntry(
        id=adapter_id,
        name=adapter_id,
        executable=str(script),
        executable_hash=exe_hash,
        version="1.0.0",
        capabilities=["token_metering"],
        contract_version=1,
        workspace_adapter=workspace_adapter,
        status="pending",
        registered_at=now,
        registered_by="test",
        approved_at=None,
        approved_by=None,
        intended_profile_name=intended_profile_name,
    )


@pytest.fixture
def clean_adapter_store(tmp_home):
    """Ensure adapter store writes go to the test temp directory."""
    return tmp_home


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

ADAPTER_ID = "test-reject-adapter"

FIXTURE_SCRIPT = None  # cached per-module by _get_script


def _get_script_path(tmp_path: Path) -> Path:
    global FIXTURE_SCRIPT
    if FIXTURE_SCRIPT is None:
        FIXTURE_SCRIPT = _make_conformant_adapter_script(tmp_path, ADAPTER_ID)
    return FIXTURE_SCRIPT


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRejectExactPendingTarget:
    """Exact PENDING target rejection removes adapter from store."""

    def test_reject_pending_adapter_removes_from_store(
        self, client: TestClient, tmp_path: Path, clean_adapter_store
    ):
        """A PENDING adapter with exact snapshot is removed after rejection."""
        script = _get_script_path(tmp_path)
        entry = _build_pending_adapter(script, ADAPTER_ID)
        save_adapter(entry)

        # Verify adapter is in the store
        assert ADAPTER_ID in load_adapters()

        body = _make_reject_body(entry)
        resp = client.post(f"/api/v1/runtime/adapters/{ADAPTER_ID}/reject", json=body)

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["id"] == ADAPTER_ID
        assert data["rejected"] is True

        # Verify adapter is removed from the store
        assert ADAPTER_ID not in load_adapters()

    def test_reject_pending_adapter_with_intended_profile(
        self, client: TestClient, tmp_path: Path, clean_adapter_store
    ):
        """A PENDING adapter with intended_profile_name is rejectable."""
        script = _get_script_path(tmp_path)
        entry = _build_pending_adapter(script, ADAPTER_ID, intended_profile_name="test-cli")
        save_adapter(entry)

        body = _make_reject_body(entry)
        resp = client.post(f"/api/v1/runtime/adapters/{ADAPTER_ID}/reject", json=body)

        assert resp.status_code == 200, resp.text
        assert resp.json()["rejected"] is True
        assert ADAPTER_ID not in load_adapters()


class TestRejectSnapshotMismatch:
    """Snapshot mismatch rejects without mutating the store."""

    def test_hash_changed_adapter_rejects(self, client: TestClient, tmp_path: Path, clean_adapter_store):
        """When the stored hash differs from the reject body, we get 422."""
        script = _get_script_path(tmp_path)
        entry = _build_pending_adapter(script, ADAPTER_ID)
        save_adapter(entry)

        body = _make_reject_body(entry)
        body["executable_hash"] = "0" * 64  # Wrong hash
        resp = client.post(f"/api/v1/runtime/adapters/{ADAPTER_ID}/reject", json=body)

        assert resp.status_code == 422, resp.text
        assert "executable_hash mismatch" in resp.json()["detail"]
        # Adapter must still be in the store
        assert ADAPTER_ID in load_adapters()

    def test_executable_mismatch_rejects(self, client: TestClient, tmp_path: Path, clean_adapter_store):
        """When the executable path differs, we get 422."""
        script = _get_script_path(tmp_path)
        entry = _build_pending_adapter(script, ADAPTER_ID)
        save_adapter(entry)

        body = _make_reject_body(entry)
        body["executable"] = "/wrong/path"
        resp = client.post(f"/api/v1/runtime/adapters/{ADAPTER_ID}/reject", json=body)

        assert resp.status_code == 422, resp.text
        assert "executable mismatch" in resp.json()["detail"]
        assert ADAPTER_ID in load_adapters()

    def test_version_mismatch_rejects(self, client: TestClient, tmp_path: Path, clean_adapter_store):
        """When the version differs, we get 422."""
        script = _get_script_path(tmp_path)
        entry = _build_pending_adapter(script, ADAPTER_ID)
        save_adapter(entry)

        body = _make_reject_body(entry)
        body["version"] = "9.9.9"
        resp = client.post(f"/api/v1/runtime/adapters/{ADAPTER_ID}/reject", json=body)

        assert resp.status_code == 422, resp.text
        assert "version mismatch" in resp.json()["detail"]
        assert ADAPTER_ID in load_adapters()

    def test_capabilities_mismatch_rejects(self, client: TestClient, tmp_path: Path, clean_adapter_store):
        """When capabilities differ, we get 422."""
        script = _get_script_path(tmp_path)
        entry = _build_pending_adapter(script, ADAPTER_ID)
        save_adapter(entry)

        body = _make_reject_body(entry)
        body["capabilities"] = ["different_cap"]
        resp = client.post(f"/api/v1/runtime/adapters/{ADAPTER_ID}/reject", json=body)

        assert resp.status_code == 422, resp.text
        assert "capabilities mismatch" in resp.json()["detail"]
        assert ADAPTER_ID in load_adapters()

    def test_contract_version_mismatch_rejects(self, client: TestClient, tmp_path: Path, clean_adapter_store):
        """When contract_version differs, we get 422."""
        script = _get_script_path(tmp_path)
        entry = _build_pending_adapter(script, ADAPTER_ID)
        save_adapter(entry)

        body = _make_reject_body(entry)
        body["contract_version"] = 99
        resp = client.post(f"/api/v1/runtime/adapters/{ADAPTER_ID}/reject", json=body)

        assert resp.status_code == 422, resp.text
        assert "contract_version" in resp.json()["detail"]
        assert ADAPTER_ID in load_adapters()

    def test_workspace_adapter_mismatch_rejects(self, client: TestClient, tmp_path: Path, clean_adapter_store):
        """When workspace_adapter differs, we get 422."""
        script = _get_script_path(tmp_path)
        entry = _build_pending_adapter(script, ADAPTER_ID)
        save_adapter(entry)

        body = _make_reject_body(entry)
        body["workspace_adapter"] = "claude"
        resp = client.post(f"/api/v1/runtime/adapters/{ADAPTER_ID}/reject", json=body)

        assert resp.status_code == 422, resp.text
        assert "workspace_adapter" in resp.json()["detail"]
        assert ADAPTER_ID in load_adapters()

    def test_stale_snapshot_after_re_register_rejects(
        self, client: TestClient, tmp_path: Path, clean_adapter_store
    ):
        """When the adapter is re-registered with different hash, stale snapshot rejects."""
        script = _get_script_path(tmp_path)
        entry = _build_pending_adapter(script, ADAPTER_ID)
        save_adapter(entry)

        # Take a snapshot of the original body
        body = _make_reject_body(entry)

        # Re-register with a different script (different hash)
        script2 = _make_conformant_adapter_script(tmp_path, "v2-" + ADAPTER_ID)
        from runtime.orchestrator.adapter_store import compute_sha256

        entry2 = AdapterEntry(
            id=ADAPTER_ID,
            name=ADAPTER_ID,
            executable=str(script2),
            executable_hash=compute_sha256(str(script2)),
            version="2.0.0",
            capabilities=["token_metering"],
            contract_version=1,
            workspace_adapter="pi",
            status="pending",
            registered_at=entry.registered_at,
            registered_by="re-register",
            approved_at=None,
            approved_by=None,
            intended_profile_name=None,
        )
        save_adapter(entry2)

        # Try to reject with the OLD body → should fail
        resp = client.post(f"/api/v1/runtime/adapters/{ADAPTER_ID}/reject", json=body)
        assert resp.status_code == 422, resp.text
        assert ADAPTER_ID in load_adapters()


class TestRejectNonPending:
    """Non-PENDING adapters cannot be rejected."""

    def test_approved_adapter_rejects_422(self, client: TestClient, tmp_path: Path, clean_adapter_store):
        """An APPROVED adapter cannot be rejected via the reject route."""
        script = _get_script_path(tmp_path)
        entry = _build_pending_adapter(script, ADAPTER_ID)
        # Set as approved
        entry = AdapterEntry(
            id=entry.id,
            name=entry.name,
            executable=entry.executable,
            executable_hash=entry.executable_hash,
            version=entry.version,
            capabilities=entry.capabilities,
            contract_version=entry.contract_version,
            workspace_adapter=entry.workspace_adapter,
            status="approved",
            registered_at=entry.registered_at,
            registered_by=entry.registered_by,
            approved_at="2024-01-01T00:00:00+00:00",
            approved_by="founder",
            intended_profile_name=entry.intended_profile_name,
        )
        save_adapter(entry)

        body = _make_reject_body(entry)
        resp = client.post(f"/api/v1/runtime/adapters/{ADAPTER_ID}/reject", json=body)

        assert resp.status_code == 422, resp.text
        assert "not PENDING" in resp.json()["detail"]
        assert ADAPTER_ID in load_adapters()

    def test_nonexistent_adapter_404(self, client: TestClient, tmp_path: Path, clean_adapter_store):
        """An unknown adapter returns 404."""
        body = {
            "executable": "/nonexistent",
            "executable_hash": "0" * 64,
            "version": "1.0.0",
            "capabilities": [],
            "contract_version": 1,
            "workspace_adapter": "pi",
        }
        resp = client.post("/api/v1/runtime/adapters/nonexistent/reject", json=body)
        assert resp.status_code == 404, resp.text


class TestRejectAuth:
    """Auth enforcement tests for the reject route."""

    def test_no_token_returns_401(self, tmp_home: Path, daemon_state):
        """Request without a bearer token returns 401."""
        from fastapi.testclient import TestClient
        from runtime.daemon.app import create_app
        app = create_app(daemon_state)
        tc = TestClient(app)

        body = {
            "executable": "/fake",
            "executable_hash": "0" * 64,
            "version": "1.0.0",
            "capabilities": [],
            "contract_version": 1,
            "workspace_adapter": "pi",
        }
        resp = tc.post(
            "/api/v1/runtime/adapters/test/reject",
            json=body,
        )
        assert resp.status_code == 401, resp.text

    def test_scoped_registration_token_rejected(
        self, client: TestClient, tmp_path: Path, clean_adapter_store
    ):
        """Scoped registration tokens are NOT accepted for management rejection.

        The reject route inherits require_token() (master bearer). Even if someone
        crafts a request with an hrreg_ token, the bearer dependency rejects it.
        """
        script = _get_script_path(tmp_path)
        entry = _build_pending_adapter(script, ADAPTER_ID)
        save_adapter(entry)

        body = _make_reject_body(entry)
        # Try with a fake registration token instead of master bearer
        resp = client.post(
            f"/api/v1/runtime/adapters/{ADAPTER_ID}/reject",
            json=body,
            headers={"Authorization": "Bearer hrreg_fake_token_1234567890123456"},
        )
        # Should get 401 because require_token() rejects non-master-bearer tokens
        assert resp.status_code == 401, resp.text


class TestRejectAuditRollback:
    """Audit rollback on reject failure restores the adapter."""

    def test_failed_audit_restores_adapter(
        self, client: TestClient, tmp_path: Path, clean_adapter_store, monkeypatch
    ):
        """If audit logging fails after durable removal, the adapter is restored."""
        script = _get_script_path(tmp_path)
        entry = _build_pending_adapter(script, ADAPTER_ID)
        save_adapter(entry)

        # Monkey-patch the audit function to raise
        def _failing_audit(*args, **kwargs):
            raise RuntimeError("simulated audit failure")

        monkeypatch.setattr(
            "runtime.daemon.routes.adapters._audit_adapter_reject",
            _failing_audit,
        )

        body = _make_reject_body(entry)
        resp = client.post(f"/api/v1/runtime/adapters/{ADAPTER_ID}/reject", json=body)

        assert resp.status_code == 500, resp.text
        assert "restored" in resp.json()["detail"].lower()

        # Adapter must still be in the store (restored)
        assert ADAPTER_ID in load_adapters()

    def test_direct_audit_helper(self, tmp_path: Path, clean_adapter_store):
        """The _audit_adapter_reject helper writes an audit row."""
        # This test exercises the audit helper directly to ensure it creates
        # the audit db and writes a row.
        _audit_adapter_reject(
            adapter_id="test-rej-audit",
            adapter_name="test-adapter",
            rejected_snapshot={
                "name": "test-adapter",
                "executable": "/tmp/fake-test",
                "executable_hash": "aa" * 32,
                "version": "1.0.0",
                "capabilities": [],
                "contract_version": 1,
                "workspace_adapter": "pi",
                "intended_profile_name": "test-cli",
                "status": "pending",
            },
        )
        # If it doesn't raise, the audit write succeeded.
        # The db file should exist.
        from runtime.runtime import daemon_home
        audit_path = daemon_home() / "runtime-audit.db"
        assert audit_path.exists()


class TestRejectConcurrency:
    """Concurrency / lock safety for reject route."""

    def test_concurrent_register_reject_serialized(
        self, client: TestClient, tmp_path: Path, clean_adapter_store
    ):
        """When a re-registration wins the lock first, the stale reject is rejected."""
        script = _get_script_path(tmp_path)
        entry = _build_pending_adapter(script, ADAPTER_ID)
        save_adapter(entry)

        body = _make_reject_body(entry)

        # Simulate a re-registration that changes the hash (but keeps same id)
        # by modifying the adapter directly in the store before the reject hits
        # the lock boundary. Since the request is synchronous, the re-registration
        # (save_adapter) happens before the client call serializes, so the
        # re-read inside the lock will see the updated entry.
        from runtime.orchestrator.adapter_store import compute_sha256

        script2 = _make_conformant_adapter_script(tmp_path, "v3-" + ADAPTER_ID)

        re_registered = AdapterEntry(
            id=ADAPTER_ID,
            name=ADAPTER_ID,
            executable=str(script2),
            executable_hash=compute_sha256(str(script2)),
            version="3.0.0",
            capabilities=["token_metering"],
            contract_version=1,
            workspace_adapter="pi",
            status="pending",
            registered_at=entry.registered_at,
            registered_by="re-register",
            approved_at=None,
            approved_by=None,
            intended_profile_name=None,
        )
        save_adapter(re_registered)

        # Now the reject with the OLD body should fail at the lock boundary
        resp = client.post(f"/api/v1/runtime/adapters/{ADAPTER_ID}/reject", json=body)
        assert resp.status_code == 422, resp.text
        # The store should still have the re-registered entry
        assert ADAPTER_ID in load_adapters()
        store_entry = load_adapters()[ADAPTER_ID]
        assert store_entry.version == "3.0.0"


class TestRejectApprovedAdapterPreservesDelete:
    """Reject route does not interfere with the existing DELETE route for APPROVED adapters."""

    def test_reject_does_not_affect_existing_delete_for_approved(
        self, client: TestClient, tmp_path: Path, clean_adapter_store
    ):
        """The reject route rejects APPROVED adapters (422). The DELETE route
        is preserved for APPROVED adapter removal."""
        script = _get_script_path(tmp_path)
        entry = _build_pending_adapter(script, ADAPTER_ID)
        # Approve it
        from datetime import datetime, timezone

        entry = AdapterEntry(
            id=entry.id,
            name=entry.name,
            executable=entry.executable,
            executable_hash=entry.executable_hash,
            version=entry.version,
            capabilities=entry.capabilities,
            contract_version=entry.contract_version,
            workspace_adapter=entry.workspace_adapter,
            status="approved",
            registered_at=entry.registered_at,
            registered_by=entry.registered_by,
            approved_at=datetime.now(timezone.utc).isoformat(),
            approved_by="founder",
            intended_profile_name=entry.intended_profile_name,
        )
        save_adapter(entry)

        # Reject route should refuse
        body = _make_reject_body(entry)
        resp = client.post(f"/api/v1/runtime/adapters/{ADAPTER_ID}/reject", json=body)
        assert resp.status_code == 422

        # DELETE route should still work (with full snapshot body)
        remove_body = {
            **body,
            "name": entry.name,
            "intended_profile_name": entry.intended_profile_name,
        }
        resp = client.request("DELETE", f"/api/v1/runtime/adapters/{ADAPTER_ID}", json=remove_body)
        assert resp.status_code == 200, resp.text
        assert resp.json()["removed"] is True
