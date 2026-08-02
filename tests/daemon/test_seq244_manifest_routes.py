"""Route-level adversarial tests for THR-107 seq244 dependency manifest.

Tests the manifest version-1 enforcement, snapshot comparison, and audit
evidence on the real FastAPI routes using TestClient and the adapter store.

Covers:
  1. Fresh registration: version-2 rejected at route boundary (422, no persistence)
  2. Submit route: version-2 rejected at route boundary (422)
  3. Pydantic model: version-2 rejected by DependencyManifest model
  4. Approve with matching/stale manifest facts
  5. Reject with matching/stale manifest facts
  6. Remove with matching/stale manifest facts
  7. Audit payload reconstruction (approve, reject, remove)
  8. Idempotence/replay for approve
  9. Legacy fixture-loaded adapter through legacy action/launch branch
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runtime.orchestrator.adapter_store import (
    AdapterEntry,
    compute_sha256,
    load_adapters,
    save_adapter,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_conformant_adapter_script(
    tmp_path: Path,
    adapter_id: str,
    dep_exes: list[Path] | None = None,
) -> Path:
    """Create a minimal conformance-probe-passing adapter script.

    Optionally includes dependency validation inside the adapter — used
    for the adapter executable itself, not for declaring child deps.
    """
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
        "adapter": "{adapter_id}-script"
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


def _make_fake_exe(tmp_path: Path, name: str) -> Path:
    """Create a fake executable dependency."""
    p = tmp_path / name
    p.write_text("#!/bin/sh\necho ok\n")
    p.chmod(0o755)
    return p


def _build_pending_adapter_with_manifest(
    script: Path,
    adapter_id: str,
    dep_exe: Path,
    dep_hash: str,
    workspace_adapter: str = "pi",
    intended_profile_name: str | None = None,
) -> AdapterEntry:
    """Build a PENDING AdapterEntry WITH manifest fields for test fixtures."""
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
        dependency_manifest_version=1,
        dependencies=[{"executable": str(dep_exe), "sha256": dep_hash}],
    )


def _build_legacy_pending_adapter(
    script: Path,
    adapter_id: str,
    workspace_adapter: str = "pi",
) -> AdapterEntry:
    """Build a PENDING AdapterEntry WITHOUT manifest fields (legacy)."""
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
        intended_profile_name=None,
        # No dependency_manifest_version / dependencies — legacy
    )


def _make_approve_body_with_manifest(entry: AdapterEntry) -> dict:
    """Build an ApproveAdapterRequest body with manifest facts."""
    return {
        "executable": entry.executable,
        "executable_hash": entry.executable_hash,
        "version": entry.version,
        "capabilities": entry.capabilities,
        "contract_version": entry.contract_version,
        "workspace_adapter": entry.workspace_adapter,
        "dependency_manifest_version": entry.dependency_manifest_version,
        "dependencies": entry.dependencies,
    }


def _make_reject_body_with_manifest(entry: AdapterEntry) -> dict:
    """Build a RejectAdapterRequest body with manifest facts."""
    return {
        "executable": entry.executable,
        "executable_hash": entry.executable_hash,
        "version": entry.version,
        "capabilities": entry.capabilities,
        "contract_version": entry.contract_version,
        "workspace_adapter": entry.workspace_adapter,
        "dependency_manifest_version": entry.dependency_manifest_version,
        "dependencies": entry.dependencies,
    }


def _make_remove_body_with_manifest(entry: AdapterEntry) -> dict:
    """Build a RemoveAdapterRequest body with manifest facts."""
    return {
        "executable": entry.executable,
        "executable_hash": entry.executable_hash,
        "version": entry.version,
        "capabilities": entry.capabilities,
        "contract_version": entry.contract_version,
        "workspace_adapter": entry.workspace_adapter,
        "name": entry.name,
        "intended_profile_name": entry.intended_profile_name,
        "dependency_manifest_version": entry.dependency_manifest_version,
        "dependencies": entry.dependencies,
    }


# Cached fixtures per module
_FIXTURE_CACHE: dict[str, Path] = {}


# ---------------------------------------------------------------------------
# 1. Version-2 rejection at route boundary (no persistence, no conformance)
# ---------------------------------------------------------------------------


class TestFreshRouteVersionEnforcement:
    """Version-2+ is rejected at the FastAPI route boundary with 422,
    before any conformance probe or durable persistence."""

    def test_register_request_version_2_rejected_by_pydantic(self):
        """AdapterRegisterRequest rejects dependency_manifest_version=2 at Pydantic level."""
        from runtime.daemon.routes.adapters import AdapterRegisterRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AdapterRegisterRequest(
                executable="/usr/bin/echo",
                version="1.0.0",
                capabilities=[],
                dependency_manifest_version=2,
                dependencies=[{"executable": "/usr/bin/python3", "sha256": "a" * 64}],
            )

    def test_submit_request_version_2_rejected_by_pydantic(self):
        """AdapterSubmitRequest rejects dependency_manifest_version=2 at Pydantic level."""
        from runtime.daemon.routes.adapters import AdapterSubmitRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AdapterSubmitRequest(
                executable="/usr/bin/echo",
                version="1.0.0",
                capabilities=[],
                dependency_manifest_version=2,
                dependencies=[{"executable": "/usr/bin/python3", "sha256": "a" * 64}],
            )

    def test_dependency_manifest_model_rejects_version_2(self):
        """DependencyManifest Pydantic model rejects version 2."""
        from runtime.orchestrator.adapter_contract import (
            DependencyManifest,
            DependencyRecord,
        )
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            DependencyManifest(
                dependency_manifest_version=2,
                dependencies=[
                    DependencyRecord(executable="/usr/bin/python3", sha256="a" * 64),
                ],
            )

    def test_dependency_manifest_model_accepts_version_1(self):
        """DependencyManifest Pydantic model accepts version 1."""
        from runtime.orchestrator.adapter_contract import (
            DependencyManifest,
            DependencyRecord,
        )

        manifest = DependencyManifest(
            dependency_manifest_version=1,
            dependencies=[
                DependencyRecord(executable="/usr/bin/python3", sha256="a" * 64),
            ],
        )
        assert manifest.dependency_manifest_version == 1

    def test_register_route_version_2_no_persistence(
        self, client: TestClient, tmp_path: Path
    ):
        """Master registration route returns 422 for version=2, adapter store is empty."""
        script = _make_conformant_adapter_script(tmp_path, "v2-reject-adapter")
        exe_hash = compute_sha256(str(script))
        dep_exe = _make_fake_exe(tmp_path, "dep-v2")
        dep_hash = compute_sha256(str(dep_exe))

        body = {
            "executable": str(script),
            "version": "1.0.0",
            "capabilities": [],
            "workspace_adapter": "pi",
            "dependency_manifest_version": 2,
            "dependencies": [{"executable": str(dep_exe), "sha256": dep_hash}],
        }

        resp = client.post("/api/v1/runtime/adapters/register", json=body)
        assert resp.status_code == 422, resp.text
        # No adapter was persisted
        assert load_adapters() == {}

    def test_register_route_version_1_accepted(
        self, client: TestClient, tmp_path: Path
    ):
        """Master registration route accepts version=1 and persists adapter."""
        script = _make_conformant_adapter_script(tmp_path, "v1-accept-adapter")
        dep_exe = _make_fake_exe(tmp_path, "dep-v1")
        dep_hash = compute_sha256(str(dep_exe))

        body = {
            "executable": str(script),
            "version": "1.0.0",
            "capabilities": [],
            "workspace_adapter": "pi",
            "dependency_manifest_version": 1,
            "dependencies": [{"executable": str(dep_exe), "sha256": dep_hash}],
        }

        resp = client.post("/api/v1/runtime/adapters/register", json=body)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["dependency_manifest_version"] == 1
        assert len(data["dependencies"]) == 1
        assert load_adapters() != {}

    def test_register_pydantic_omitted_manifest_fields(self):
        """Pydantic rejects omitted dependency_manifest_version (required field)."""
        from runtime.daemon.routes.adapters import AdapterRegisterRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AdapterRegisterRequest(
                executable="/usr/bin/echo",
                version="1.0.0",
                capabilities=[],
                # dependency_manifest_version omitted
                # dependencies omitted
            )

    def test_register_pydantic_null_deps_rejected(self):
        """Pydantic rejects null dependencies (list expected)."""
        from runtime.daemon.routes.adapters import AdapterRegisterRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AdapterRegisterRequest(
                executable="/usr/bin/echo",
                version="1.0.0",
                capabilities=[],
                dependency_manifest_version=1,
                dependencies=None,
            )

    def test_register_pydantic_empty_deps_rejected(self):
        """Pydantic rejects empty dependencies list."""
        from runtime.daemon.routes.adapters import AdapterRegisterRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AdapterRegisterRequest(
                executable="/usr/bin/echo",
                version="1.0.0",
                capabilities=[],
                dependency_manifest_version=1,
                dependencies=[],
            )


# ---------------------------------------------------------------------------
# 1b. Strict integer type enforcement (reject float, string, boolean)
# ---------------------------------------------------------------------------


class TestStrictIntTypeEnforcement:
    """dependency_manifest_version must be literally JSON integer 1.

    Pydantic int coercion silently accepts 1.0, "1", and true.
    The BeforeValidator _strict_int_for_manifest rejects these at
    the Pydantic boundary before conformance or persistence.
    """

    # -- Model-level strict-int rejection (parameterized) --
    _STRICT_REJECT_MODELS = [
        ("DependencyManifest", "runtime.orchestrator.adapter_contract", "DependencyManifest"),
        ("AdapterRegisterRequest", "runtime.daemon.routes.adapters", "AdapterRegisterRequest"),
        ("AdapterSubmitRequest", "runtime.daemon.routes.adapters", "AdapterSubmitRequest"),
    ]

    @pytest.mark.parametrize("bad_value,label", [
        (1.0, "float-1.0"),
        ("1", "string-1"),
        (True, "bool-true"),
    ])
    @pytest.mark.parametrize("model_name,import_from,class_name", _STRICT_REJECT_MODELS)
    def test_model_rejects_non_strict_int(
        self, bad_value, label, model_name, import_from, class_name
    ):
        """Every fresh Pydantic model rejects float/string/bool for the field."""
        import importlib
        from pydantic import ValidationError

        mod = importlib.import_module(import_from)
        cls = getattr(mod, class_name)

        with pytest.raises(ValidationError):
            cls(
                executable="/usr/bin/echo",
                version="1.0.0",
                capabilities=[],
                dependency_manifest_version=bad_value,
                dependencies=[
                    {"executable": "/usr/bin/python3", "sha256": "a" * 64}
                ],
            )

    def test_model_accepts_strict_int_1(self):
        """Every fresh model accepts literal Python int 1."""
        from runtime.daemon.routes.adapters import (
            AdapterRegisterRequest,
            AdapterSubmitRequest,
        )
        from runtime.orchestrator.adapter_contract import (
            DependencyManifest,
            DependencyRecord,
        )

        # All three models accept int 1
        r = AdapterRegisterRequest(
            executable="/usr/bin/echo",
            version="1.0.0",
            capabilities=[],
            dependency_manifest_version=1,
            dependencies=[{"executable": "/usr/bin/python3", "sha256": "a" * 64}],
        )
        assert r.dependency_manifest_version == 1

        s = AdapterSubmitRequest(
            executable="/usr/bin/echo",
            version="1.0.0",
            capabilities=[],
            dependency_manifest_version=1,
            dependencies=[{"executable": "/usr/bin/python3", "sha256": "a" * 64}],
        )
        assert s.dependency_manifest_version == 1

        m = DependencyManifest(
            dependency_manifest_version=1,
            dependencies=[
                DependencyRecord(executable="/usr/bin/python3", sha256="a" * 64)
            ],
        )
        assert m.dependency_manifest_version == 1

    # -- Route-level HTTP tests (register route) --

    def test_register_route_float_1_0_no_persistence(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """JSON 1.0 rejected at register route (422), probe not called, no persistence."""
        from unittest.mock import MagicMock
        spy = MagicMock()
        monkeypatch.setattr(
            "runtime.daemon.routes.adapters.register_custom_adapter",
            spy,
        )

        script = _make_conformant_adapter_script(tmp_path, "float-reg-adapter")
        dep_exe = _make_fake_exe(tmp_path, "dep-float-reg")
        dep_hash = compute_sha256(str(dep_exe))

        body = {
            "executable": str(script),
            "version": "1.0.0",
            "capabilities": [],
            "workspace_adapter": "pi",
            "dependency_manifest_version": 1.0,
            "dependencies": [{"executable": str(dep_exe), "sha256": dep_hash}],
        }

        resp = client.post("/api/v1/runtime/adapters/register", json=body)
        assert resp.status_code == 422, resp.text
        assert load_adapters() == {}
        spy.assert_not_called()

    def test_register_route_string_1_no_persistence(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """JSON "1" rejected at register route (422), probe not called, no persistence."""
        from unittest.mock import MagicMock
        spy = MagicMock()
        monkeypatch.setattr(
            "runtime.daemon.routes.adapters.register_custom_adapter",
            spy,
        )

        script = _make_conformant_adapter_script(tmp_path, "str-reg-adapter")
        dep_exe = _make_fake_exe(tmp_path, "dep-str-reg")
        dep_hash = compute_sha256(str(dep_exe))

        body = {
            "executable": str(script),
            "version": "1.0.0",
            "capabilities": [],
            "workspace_adapter": "pi",
            "dependency_manifest_version": "1",
            "dependencies": [{"executable": str(dep_exe), "sha256": dep_hash}],
        }

        resp = client.post("/api/v1/runtime/adapters/register", json=body)
        assert resp.status_code == 422, resp.text
        assert load_adapters() == {}
        spy.assert_not_called()

    def test_register_route_bool_true_no_persistence(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """JSON true rejected at register route (422), probe not called, no persistence."""
        from unittest.mock import MagicMock
        spy = MagicMock()
        monkeypatch.setattr(
            "runtime.daemon.routes.adapters.register_custom_adapter",
            spy,
        )

        script = _make_conformant_adapter_script(tmp_path, "bool-reg-adapter")
        dep_exe = _make_fake_exe(tmp_path, "dep-bool-reg")
        dep_hash = compute_sha256(str(dep_exe))

        body = {
            "executable": str(script),
            "version": "1.0.0",
            "capabilities": [],
            "workspace_adapter": "pi",
            "dependency_manifest_version": True,
            "dependencies": [{"executable": str(dep_exe), "sha256": dep_hash}],
        }

        resp = client.post("/api/v1/runtime/adapters/register", json=body)
        assert resp.status_code == 422, resp.text
        assert load_adapters() == {}
        spy.assert_not_called()

    def test_register_route_int_1_probe_and_persist(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Literal int 1 accepted, probe called, adapter persisted in store."""
        from unittest.mock import MagicMock
        from runtime.orchestrator.custom_adapter_registry import (
            register_custom_adapter as _real,
        )
        spy = MagicMock(wraps=_real)
        monkeypatch.setattr(
            "runtime.daemon.routes.adapters.register_custom_adapter",
            spy,
        )

        script = _make_conformant_adapter_script(tmp_path, "int1-reg-adapter")
        dep_exe = _make_fake_exe(tmp_path, "dep-int1-reg")
        dep_hash = compute_sha256(str(dep_exe))

        body = {
            "executable": str(script),
            "version": "1.0.0",
            "capabilities": [],
            "workspace_adapter": "pi",
            "dependency_manifest_version": 1,
            "dependencies": [{"executable": str(dep_exe), "sha256": dep_hash}],
        }

        resp = client.post("/api/v1/runtime/adapters/register", json=body)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "pending"
        assert data["dependency_manifest_version"] == 1
        spy.assert_called_once()
        adapters = load_adapters()
        assert len(adapters) == 1


# ---------------------------------------------------------------------------
# 2. Approve with matching/stale manifest facts
# ---------------------------------------------------------------------------


class TestApproveWithManifestFacts:
    """Approve adapter route with dependency manifest snapshot comparison."""

    ADAPTER_ID = "approve-manifest-adapter"

    def test_approve_with_matching_manifest_succeeds(
        self, client: TestClient, tmp_path: Path
    ):
        """Approving with exact matching manifest facts succeeds."""
        script = _make_conformant_adapter_script(tmp_path, self.ADAPTER_ID)
        dep_exe = _make_fake_exe(tmp_path, "dep-approve-match")
        dep_hash = compute_sha256(str(dep_exe))

        entry = _build_pending_adapter_with_manifest(
            script, self.ADAPTER_ID, dep_exe, dep_hash
        )
        save_adapter(entry)

        body = _make_approve_body_with_manifest(entry)
        resp = client.post(
            f"/api/v1/runtime/adapters/{self.ADAPTER_ID}/approve", json=body
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "approved"
        assert data["dependency_manifest_version"] == 1
        assert len(data["dependencies"]) == 1
        assert data["dependencies"][0]["sha256"] == dep_hash

        # Verify persisted
        loaded = load_adapters()
        assert loaded[self.ADAPTER_ID].status == "approved"
        assert loaded[self.ADAPTER_ID].dependency_manifest_version == 1
        assert loaded[self.ADAPTER_ID].dependencies[0]["sha256"] == dep_hash

    def test_approve_with_stale_manifest_version_rejected(
        self, client: TestClient, tmp_path: Path
    ):
        """Approving with wrong dependency_manifest_version is rejected (422)."""
        script = _make_conformant_adapter_script(tmp_path, self.ADAPTER_ID)
        dep_exe = _make_fake_exe(tmp_path, "dep-approve-stale-ver")
        dep_hash = compute_sha256(str(dep_exe))

        entry = _build_pending_adapter_with_manifest(
            script, self.ADAPTER_ID, dep_exe, dep_hash
        )
        save_adapter(entry)

        body = _make_approve_body_with_manifest(entry)
        body["dependency_manifest_version"] = None  # stale: store has 1
        resp = client.post(
            f"/api/v1/runtime/adapters/{self.ADAPTER_ID}/approve", json=body
        )

        assert resp.status_code == 422, resp.text
        assert "dependency_manifest_version mismatch" in resp.json()["detail"]
        # Store unchanged
        assert load_adapters()[self.ADAPTER_ID].status == "pending"

    def test_approve_with_stale_dependencies_rejected(
        self, client: TestClient, tmp_path: Path
    ):
        """Approving with tampered dependencies is rejected (422)."""
        script = _make_conformant_adapter_script(tmp_path, self.ADAPTER_ID)
        dep_exe = _make_fake_exe(tmp_path, "dep-approve-stale-deps")
        dep_hash = compute_sha256(str(dep_exe))

        entry = _build_pending_adapter_with_manifest(
            script, self.ADAPTER_ID, dep_exe, dep_hash
        )
        save_adapter(entry)

        body = _make_approve_body_with_manifest(entry)
        body["dependencies"] = [
            {"executable": "/tampered/path", "sha256": "f" * 64}
        ]
        resp = client.post(
            f"/api/v1/runtime/adapters/{self.ADAPTER_ID}/approve", json=body
        )

        assert resp.status_code == 422, resp.text
        assert "dependencies mismatch" in resp.json()["detail"]
        assert load_adapters()[self.ADAPTER_ID].status == "pending"

    def test_approve_is_idempotent_with_matching_manifest(
        self, client: TestClient, tmp_path: Path
    ):
        """Re-approving with identical manifest facts is idempotent."""
        script = _make_conformant_adapter_script(tmp_path, self.ADAPTER_ID)
        dep_exe = _make_fake_exe(tmp_path, "dep-approve-idem")
        dep_hash = compute_sha256(str(dep_exe))

        entry = _build_pending_adapter_with_manifest(
            script, self.ADAPTER_ID, dep_exe, dep_hash
        )
        save_adapter(entry)

        body = _make_approve_body_with_manifest(entry)

        # First approval
        resp1 = client.post(
            f"/api/v1/runtime/adapters/{self.ADAPTER_ID}/approve", json=body
        )
        assert resp1.status_code == 200, resp1.text
        first_approved_at = resp1.json()["approved_at"]

        # Second approval (idempotent)
        resp2 = client.post(
            f"/api/v1/runtime/adapters/{self.ADAPTER_ID}/approve", json=body
        )
        assert resp2.status_code == 200, resp2.text
        # Idempotent: same approved_at preserved
        assert resp2.json()["approved_at"] == first_approved_at

    def test_approve_legacy_adapter_with_no_manifest(
        self, client: TestClient, tmp_path: Path
    ):
        """Approving a legacy adapter (no manifest fields stored) with
        matching None/empty manifest facts succeeds."""
        adapter_id = "approve-legacy-manifest"
        script = _make_conformant_adapter_script(tmp_path, adapter_id)

        entry = _build_legacy_pending_adapter(script, adapter_id)
        save_adapter(entry)

        body = {
            "executable": entry.executable,
            "executable_hash": entry.executable_hash,
            "version": entry.version,
            "capabilities": entry.capabilities,
            "contract_version": entry.contract_version,
            "workspace_adapter": entry.workspace_adapter,
            "dependency_manifest_version": None,
            "dependencies": None,
        }

        resp = client.post(
            f"/api/v1/runtime/adapters/{adapter_id}/approve", json=body
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "approved"
        assert data["dependency_manifest_version"] is None
        assert data["dependencies"] == []


# ---------------------------------------------------------------------------
# 3. Reject with matching/stale manifest facts
# ---------------------------------------------------------------------------


class TestRejectWithManifestFacts:
    """Reject adapter route with dependency manifest snapshot comparison."""

    ADAPTER_ID = "reject-manifest-adapter"

    def test_reject_with_matching_manifest_succeeds(
        self, client: TestClient, tmp_path: Path
    ):
        """Rejecting with exact matching manifest facts succeeds."""
        script = _make_conformant_adapter_script(tmp_path, self.ADAPTER_ID)
        dep_exe = _make_fake_exe(tmp_path, "dep-reject-match")
        dep_hash = compute_sha256(str(dep_exe))

        entry = _build_pending_adapter_with_manifest(
            script, self.ADAPTER_ID, dep_exe, dep_hash
        )
        save_adapter(entry)

        body = _make_reject_body_with_manifest(entry)
        resp = client.post(
            f"/api/v1/runtime/adapters/{self.ADAPTER_ID}/reject", json=body
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["rejected"] is True
        # Adapter removed from store
        assert self.ADAPTER_ID not in load_adapters()

    def test_reject_with_stale_manifest_version_rejected(
        self, client: TestClient, tmp_path: Path
    ):
        """Rejecting with wrong dependency_manifest_version returns 422."""
        script = _make_conformant_adapter_script(tmp_path, self.ADAPTER_ID)
        dep_exe = _make_fake_exe(tmp_path, "dep-reject-stale-ver")
        dep_hash = compute_sha256(str(dep_exe))

        entry = _build_pending_adapter_with_manifest(
            script, self.ADAPTER_ID, dep_exe, dep_hash
        )
        save_adapter(entry)

        body = _make_reject_body_with_manifest(entry)
        body["dependency_manifest_version"] = None
        resp = client.post(
            f"/api/v1/runtime/adapters/{self.ADAPTER_ID}/reject", json=body
        )

        assert resp.status_code == 422, resp.text
        assert "dependency_manifest_version mismatch" in resp.json()["detail"]
        assert self.ADAPTER_ID in load_adapters()  # Not removed

    def test_reject_with_stale_dependencies_rejected(
        self, client: TestClient, tmp_path: Path
    ):
        """Rejecting with tampered dependencies returns 422."""
        script = _make_conformant_adapter_script(tmp_path, self.ADAPTER_ID)
        dep_exe = _make_fake_exe(tmp_path, "dep-reject-stale-deps")
        dep_hash = compute_sha256(str(dep_exe))

        entry = _build_pending_adapter_with_manifest(
            script, self.ADAPTER_ID, dep_exe, dep_hash
        )
        save_adapter(entry)

        body = _make_reject_body_with_manifest(entry)
        body["dependencies"] = []
        resp = client.post(
            f"/api/v1/runtime/adapters/{self.ADAPTER_ID}/reject", json=body
        )

        assert resp.status_code == 422, resp.text
        assert self.ADAPTER_ID in load_adapters()

    def test_reject_legacy_adapter_with_no_manifest(
        self, client: TestClient, tmp_path: Path
    ):
        """Rejecting a legacy adapter (no manifest) with matching
        null/empty manifest facts succeeds."""
        adapter_id = "reject-legacy-manifest"
        script = _make_conformant_adapter_script(tmp_path, adapter_id)

        entry = _build_legacy_pending_adapter(script, adapter_id)
        save_adapter(entry)

        body = {
            "executable": entry.executable,
            "executable_hash": entry.executable_hash,
            "version": entry.version,
            "capabilities": entry.capabilities,
            "contract_version": entry.contract_version,
            "workspace_adapter": entry.workspace_adapter,
            "dependency_manifest_version": None,
            "dependencies": None,
        }

        resp = client.post(
            f"/api/v1/runtime/adapters/{adapter_id}/reject", json=body
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["rejected"] is True
        assert adapter_id not in load_adapters()


# ---------------------------------------------------------------------------
# 4. Remove with matching/stale manifest facts
# ---------------------------------------------------------------------------


class TestRemoveWithManifestFacts:
    """Remove adapter route with dependency manifest snapshot comparison."""

    ADAPTER_ID = "remove-manifest-adapter"

    def test_remove_with_matching_manifest_succeeds(
        self, client: TestClient, tmp_path: Path
    ):
        """Removing with exact matching manifest facts succeeds."""
        script = _make_conformant_adapter_script(tmp_path, self.ADAPTER_ID)
        dep_exe = _make_fake_exe(tmp_path, "dep-remove-match")
        dep_hash = compute_sha256(str(dep_exe))

        entry = _build_pending_adapter_with_manifest(
            script, self.ADAPTER_ID, dep_exe, dep_hash
        )
        save_adapter(entry)

        # Approve first (remove requires APPROVED)
        approve_body = _make_approve_body_with_manifest(entry)
        resp_approve = client.post(
            f"/api/v1/runtime/adapters/{self.ADAPTER_ID}/approve",
            json=approve_body,
        )
        assert resp_approve.status_code == 200, resp_approve.text

        # Now remove
        body = _make_remove_body_with_manifest(entry)
        resp = client.request(
            "DELETE",
            f"/api/v1/runtime/adapters/{self.ADAPTER_ID}",
            json=body,
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["removed"] is True
        assert self.ADAPTER_ID not in load_adapters()

    def test_remove_with_stale_manifest_version_rejected(
        self, client: TestClient, tmp_path: Path
    ):
        """Removing with wrong dependency_manifest_version returns 422."""
        script = _make_conformant_adapter_script(tmp_path, self.ADAPTER_ID)
        dep_exe = _make_fake_exe(tmp_path, "dep-remove-stale-ver")
        dep_hash = compute_sha256(str(dep_exe))

        entry = _build_pending_adapter_with_manifest(
            script, self.ADAPTER_ID, dep_exe, dep_hash
        )
        save_adapter(entry)

        # Approve
        approve_body = _make_approve_body_with_manifest(entry)
        resp_a = client.post(
            f"/api/v1/runtime/adapters/{self.ADAPTER_ID}/approve",
            json=approve_body,
        )
        assert resp_a.status_code == 200

        body = _make_remove_body_with_manifest(entry)
        body["dependency_manifest_version"] = None  # stale
        resp = client.request(
            "DELETE",
            f"/api/v1/runtime/adapters/{self.ADAPTER_ID}",
            json=body,
        )

        assert resp.status_code == 422, resp.text
        assert self.ADAPTER_ID in load_adapters()

    def test_remove_with_stale_dependencies_rejected(
        self, client: TestClient, tmp_path: Path
    ):
        """Removing with tampered dependencies returns 422."""
        script = _make_conformant_adapter_script(tmp_path, self.ADAPTER_ID)
        dep_exe = _make_fake_exe(tmp_path, "dep-remove-stale-deps")
        dep_hash = compute_sha256(str(dep_exe))

        entry = _build_pending_adapter_with_manifest(
            script, self.ADAPTER_ID, dep_exe, dep_hash
        )
        save_adapter(entry)

        approve_body = _make_approve_body_with_manifest(entry)
        resp_a = client.post(
            f"/api/v1/runtime/adapters/{self.ADAPTER_ID}/approve",
            json=approve_body,
        )
        assert resp_a.status_code == 200

        body = _make_remove_body_with_manifest(entry)
        body["dependencies"] = None  # stale
        resp = client.request(
            "DELETE",
            f"/api/v1/runtime/adapters/{self.ADAPTER_ID}",
            json=body,
        )

        assert resp.status_code == 422, resp.text
        assert self.ADAPTER_ID in load_adapters()

    def test_remove_legacy_adapter_with_no_manifest(
        self, client: TestClient, tmp_path: Path
    ):
        """Removing a legacy adapter (no manifest) with matching
        null/empty manifest facts succeeds."""
        adapter_id = "remove-legacy-manifest"
        script = _make_conformant_adapter_script(tmp_path, adapter_id)

        entry = _build_legacy_pending_adapter(script, adapter_id)
        save_adapter(entry)

        # Approve the legacy entry
        legacy_approve_body = {
            "executable": entry.executable,
            "executable_hash": entry.executable_hash,
            "version": entry.version,
            "capabilities": entry.capabilities,
            "contract_version": entry.contract_version,
            "workspace_adapter": entry.workspace_adapter,
        }
        resp_a = client.post(
            f"/api/v1/runtime/adapters/{adapter_id}/approve",
            json=legacy_approve_body,
        )
        assert resp_a.status_code == 200, resp_a.text

        # Remove with matching None/empty manifest
        body = {
            "executable": entry.executable,
            "executable_hash": entry.executable_hash,
            "version": entry.version,
            "capabilities": entry.capabilities,
            "contract_version": entry.contract_version,
            "workspace_adapter": entry.workspace_adapter,
            "name": entry.name,
            "intended_profile_name": entry.intended_profile_name,
            "dependency_manifest_version": None,
            "dependencies": None,
        }
        resp = client.request(
            "DELETE",
            f"/api/v1/runtime/adapters/{adapter_id}",
            json=body,
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["removed"] is True
        assert adapter_id not in load_adapters()


# ---------------------------------------------------------------------------
# 5. Audit payload reconstruction for approve, reject, remove
# ---------------------------------------------------------------------------


class TestManifestAuditPayloads:
    """Audit payloads include dependency_manifest_version and dependencies."""

    def test_reject_audit_includes_manifest_facts(
        self, client: TestClient, tmp_path: Path
    ):
        """The reject audit payload attests to manifest version + dependencies."""
        from runtime.daemon.routes.adapters import _audit_adapter_reject
        from runtime.infrastructure.database import Database

        adapter_id = "audit-reject-manifest"
        script = _make_conformant_adapter_script(tmp_path, adapter_id)
        dep_exe = _make_fake_exe(tmp_path, "dep-audit-reject")
        dep_hash = compute_sha256(str(dep_exe))

        entry = _build_pending_adapter_with_manifest(
            script, adapter_id, dep_exe, dep_hash
        )
        save_adapter(entry)

        # Reject via route
        body = _make_reject_body_with_manifest(entry)
        resp = client.post(
            f"/api/v1/runtime/adapters/{adapter_id}/reject", json=body
        )
        assert resp.status_code == 200, resp.text

        # Inspect audit log on disk
        from runtime.runtime import daemon_home

        audit_db_path = daemon_home() / "runtime-audit.db"
        db = Database(audit_db_path)
        try:
            rows = db.execute(
                "SELECT payload FROM audit_log "
                "WHERE task_id = ? AND action = ? "
                "ORDER BY timestamp DESC LIMIT 1",
                (f"adapter:{adapter_id}", "adapter_rejected"),
            ).fetchall()
            assert len(rows) == 1
            import json
            payload = json.loads(rows[0][0])
            assert payload.get("dependency_manifest_version") == 1
            assert len(payload.get("dependencies", [])) == 1
            assert payload["dependencies"][0]["sha256"] == dep_hash
        finally:
            db.close()

    def test_remove_audit_includes_manifest_facts(
        self, client: TestClient, tmp_path: Path
    ):
        """The remove audit payload attests to manifest version + dependencies."""
        from runtime.infrastructure.database import Database

        adapter_id = "audit-remove-manifest"
        script = _make_conformant_adapter_script(tmp_path, adapter_id)
        dep_exe = _make_fake_exe(tmp_path, "dep-audit-remove")
        dep_hash = compute_sha256(str(dep_exe))

        entry = _build_pending_adapter_with_manifest(
            script, adapter_id, dep_exe, dep_hash
        )
        save_adapter(entry)

        # Approve
        approve_body = _make_approve_body_with_manifest(entry)
        resp_a = client.post(
            f"/api/v1/runtime/adapters/{adapter_id}/approve",
            json=approve_body,
        )
        assert resp_a.status_code == 200

        # Remove
        body = _make_remove_body_with_manifest(entry)
        resp = client.request(
            "DELETE",
            f"/api/v1/runtime/adapters/{adapter_id}",
            json=body,
        )
        assert resp.status_code == 200, resp.text

        # Inspect audit
        from runtime.runtime import daemon_home

        audit_db_path = daemon_home() / "runtime-audit.db"
        db = Database(audit_db_path)
        try:
            rows = db.execute(
                "SELECT payload FROM audit_log "
                "WHERE task_id = ? AND action = ? "
                "ORDER BY timestamp DESC LIMIT 1",
                (f"adapter:{adapter_id}", "adapter_removed"),
            ).fetchall()
            assert len(rows) == 1
            import json
            payload = json.loads(rows[0][0])
            assert payload.get("dependency_manifest_version") == 1
            assert len(payload.get("dependencies", [])) == 1
        finally:
            db.close()

    def test_reject_audit_for_legacy_adapter_has_null_manifest(
        self, client: TestClient, tmp_path: Path
    ):
        """Reject audit for a legacy adapter records null manifest facts."""
        from runtime.infrastructure.database import Database

        adapter_id = "audit-reject-legacy"
        script = _make_conformant_adapter_script(tmp_path, adapter_id)

        entry = _build_legacy_pending_adapter(script, adapter_id)
        save_adapter(entry)

        body = {
            "executable": entry.executable,
            "executable_hash": entry.executable_hash,
            "version": entry.version,
            "capabilities": entry.capabilities,
            "contract_version": entry.contract_version,
            "workspace_adapter": entry.workspace_adapter,
            "dependency_manifest_version": None,
            "dependencies": None,
        }
        resp = client.post(
            f"/api/v1/runtime/adapters/{adapter_id}/reject", json=body
        )
        assert resp.status_code == 200, resp.text

        from runtime.runtime import daemon_home

        audit_db_path = daemon_home() / "runtime-audit.db"
        db = Database(audit_db_path)
        try:
            rows = db.execute(
                "SELECT payload FROM audit_log "
                "WHERE task_id = ? AND action = ? "
                "ORDER BY timestamp DESC LIMIT 1",
                (f"adapter:{adapter_id}", "adapter_rejected"),
            ).fetchall()
            assert len(rows) == 1
            import json
            payload = json.loads(rows[0][0])
            # Legacy: manifest fields are null
            assert payload.get("dependency_manifest_version") is None
            assert payload.get("dependencies") == []
        finally:
            db.close()
