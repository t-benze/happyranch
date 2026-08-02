"""Tests for THR-107 seq244: dependency manifest extension.

Tests the dependency-manifest contract enforcement for custom adapter
registration, persistence, snapshot comparison, and pre-launch revalidation.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from runtime.orchestrator.adapter_store import (
    AdapterEntry,
    load_adapters,
    compute_sha256,
    save_adapter,
    remove_adapter,
)
from runtime.orchestrator.custom_adapter_registry import (
    register_custom_adapter,
    validate_dependency_manifest,
    validate_dependency_record,
    approve_adapter,
    resolve_adapter,
    validate_executable_path,
)
from runtime.orchestrator.adapter_contract import (
    DependencyManifest,
    DependencyRecord,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_exe(tmp_path: Path, name: str, content: str = "#!/bin/sh\necho ok\n") -> Path:
    """Create a fake executable file in tmp_path."""
    p = tmp_path / name
    p.write_text(content)
    p.chmod(0o755)
    return p


def _make_fake_adapter_with_deps(
    tmp_path: Path,
    name: str,
    dep_exes: list[Path] | None = None,
    has_token_metering: bool = False,
    adapter_id: str | None = None,
) -> Path:
    """Create a fake adapter script that passes conformance with optional deps and token_usage."""
    if adapter_id is None:
        adapter_id = name
    dep_section = ""
    if dep_exes:
        dep_json = json.dumps([{"executable": str(d), "sha256": compute_sha256(str(d))} for d in dep_exes])
        dep_section = f"""
import hashlib, json

declared = {dep_json}
for d in declared:
    with open(d["executable"], "rb") as f:
        actual = hashlib.sha256(f.read()).hexdigest()
    if actual != d["sha256"]:
        print(json.dumps({{"success": False, "error": "dep-hash-mismatch"}}))
        sys.exit(1)
"""

    token_usage = "None"
    if has_token_metering:
        token_usage = '{"input_tokens": 100, "output_tokens": 50, "model": "test-model"}'

    script_body = f"""#!/usr/bin/env python3
import sys, json
raw = sys.stdin.read()
input_data = json.loads(raw) if raw else {{}}
session_id = input_data.get("invocation", {{}}).get("invocation_id", "probe-sess-00000000-0000-0000-0000-000000000000")
{dep_section}
output = {{
    "success": True,
    "duration_seconds": 0,
    "session_id": session_id,
    "returncode": 0,
    "stdout_tail": "ok",
    "stderr_tail": "",
    "result": {{"text": "hello"}},
    "token_usage": {token_usage},
    "error": None,
    "agent_session_id": None,
    "rate_limited": False,
    "adapter_metadata": {{
        "adapter": "{adapter_id}",
        "adapter_version": "1.0.0",
        "contract_version": 1,
    }},
    "child_session_id": None,
    "raw_forensics_ref": None,
}}
sys.stdout.write(json.dumps(output))
sys.exit(0)
"""
    p = tmp_path / name
    p.write_text(script_body)
    p.chmod(0o755)
    return p


# ---------------------------------------------------------------------------
# Dependency Manifest Validation Tests
# ---------------------------------------------------------------------------


class TestDependencyManifestValidation:
    """Test validation of dependency manifest declarations."""

    def test_legacy_none_is_accepted(self):
        v, deps = validate_dependency_manifest(None, None)
        assert v is None
        assert deps == []

    def test_legacy_none_with_empty_deps_is_accepted(self):
        v, deps = validate_dependency_manifest(None, [])
        assert v is None
        assert deps == []

    def test_manifest_version_requires_non_empty_deps(self):
        with pytest.raises(ValueError, match="non-empty list"):
            validate_dependency_manifest(1, None)

    def test_manifest_version_requires_non_empty_list(self):
        with pytest.raises(ValueError, match="non-empty list"):
            validate_dependency_manifest(1, [])

    def test_invalid_manifest_version_type_string(self):
        with pytest.raises(ValueError, match="integer"):
            validate_dependency_manifest("1", [{"executable": "/bin/sh", "sha256": "a" * 64}])

    def test_invalid_manifest_version_type_float(self):
        with pytest.raises(ValueError, match="integer"):
            validate_dependency_manifest(1.0, [{"executable": "/bin/sh", "sha256": "a" * 64}])

    def test_invalid_manifest_version_type_bool(self):
        with pytest.raises(ValueError, match="integer"):
            validate_dependency_manifest(True, [{"executable": "/bin/sh", "sha256": "a" * 64}])

    def test_version_zero_rejected(self):
        with pytest.raises(ValueError, match="exactly 1"):
            validate_dependency_manifest(0, [{"executable": "/bin/sh", "sha256": "a" * 64}])

    def test_version_two_rejected(self):
        """Manifest version 2 is rejected — only version 1 is supported."""
        with pytest.raises(ValueError, match="exactly 1"):
            validate_dependency_manifest(2, [{"executable": "/bin/sh", "sha256": "a" * 64}])

    def test_version_negative_rejected(self):
        """Negative manifest version is rejected."""
        with pytest.raises(ValueError, match="exactly 1"):
            validate_dependency_manifest(-1, [{"executable": "/bin/sh", "sha256": "a" * 64}])

    def test_valid_dependency_record(self, tmp_path: Path):
        exe = _make_fake_exe(tmp_path, "dep1")
        h = compute_sha256(str(exe))
        result = validate_dependency_record({"executable": str(exe), "sha256": h})
        assert result["executable"] == str(exe)
        assert result["sha256"] == h

    def test_relative_path_rejected(self):
        with pytest.raises(ValueError, match="absolute path"):
            validate_dependency_record({"executable": "relative/path", "sha256": "a" * 64})

    def test_missing_file_rejected(self, tmp_path: Path):
        nonexistent = tmp_path / "nonexistent"
        with pytest.raises(ValueError, match="does not exist"):
            validate_dependency_record({"executable": str(nonexistent), "sha256": "a" * 64})

    def test_non_regular_file_rejected(self, tmp_path: Path):
        d = tmp_path / "mydir"
        d.mkdir()
        with pytest.raises(ValueError, match="regular file"):
            validate_dependency_record({"executable": str(d), "sha256": "a" * 64})

    def test_non_executable_rejected(self, tmp_path: Path):
        p = tmp_path / "notexe"
        p.write_text("hello")
        p.chmod(0o644)
        with pytest.raises(ValueError, match="executable"):
            validate_dependency_record({"executable": str(p), "sha256": "a" * 64})

    def test_hash_mismatch_rejected(self, tmp_path: Path):
        exe = _make_fake_exe(tmp_path, "dep2")
        with pytest.raises(ValueError, match="sha256 mismatch"):
            validate_dependency_record({"executable": str(exe), "sha256": "a" * 64})

    def test_invalid_sha256_length(self, tmp_path: Path):
        exe = _make_fake_exe(tmp_path, "dep3")
        with pytest.raises(ValueError, match="64-char"):
            validate_dependency_record({"executable": str(exe), "sha256": "short"})

    def test_non_hex_sha256_rejected(self, tmp_path: Path):
        exe = _make_fake_exe(tmp_path, "dep4")
        with pytest.raises(ValueError, match="valid hex"):
            validate_dependency_record({"executable": str(exe), "sha256": "z" * 64})

    def test_duplicate_dependencies_rejected(self, tmp_path: Path):
        exe = _make_fake_exe(tmp_path, "dup")
        h = compute_sha256(str(exe))
        with pytest.raises(ValueError, match="Duplicate"):
            validate_dependency_manifest(1, [
                {"executable": str(exe), "sha256": h},
                {"executable": str(exe), "sha256": h},
            ])


# ---------------------------------------------------------------------------
# Registration with Dependency Manifest Tests
# ---------------------------------------------------------------------------


class TestRegistrationWithDependencyManifest:
    """Test registration with dependency manifest extension."""

    def test_registration_with_valid_manifest_succeeds(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        dep_exe = _make_fake_exe(tmp_path, "child-cli")
        dep_hash = compute_sha256(str(dep_exe))
        adapter = _make_fake_adapter_with_deps(tmp_path, "my-adapter", dep_exes=[dep_exe])

        entry = register_custom_adapter(
            executable=str(adapter),
            version="1.0.0",
            capabilities=[],
            dependency_manifest_version=1,
            dependencies=[{"executable": str(dep_exe), "sha256": dep_hash}],
        )

        assert entry.status == "pending"
        assert entry.dependency_manifest_version == 1
        assert len(entry.dependencies) == 1
        assert entry.dependencies[0]["executable"] == str(dep_exe)
        assert entry.dependencies[0]["sha256"] == dep_hash

    def test_route_level_rejects_absent_or_null_manifest(self):
        """Pydantic schema rejects absent, null, or empty manifest at the route boundary."""
        from runtime.daemon.routes.adapters import AdapterRegisterRequest, AdapterSubmitRequest
        from pydantic import ValidationError

        # Absent (omitted) fields → Pydantic rejects
        with pytest.raises(ValidationError):
            AdapterRegisterRequest(
                executable="/usr/bin/echo",
                version="1.0.0",
                capabilities=[],
                # dependency_manifest_version omitted
                # dependencies omitted
            )

        with pytest.raises(ValidationError):
            AdapterSubmitRequest(
                executable="/usr/bin/echo",
                version="1.0.0",
                capabilities=[],
                # dependency_manifest_version omitted
                # dependencies omitted
            )

        # Null dependency_manifest_version → Pydantic rejects (int expected, got None)
        with pytest.raises(ValidationError):
            AdapterRegisterRequest(
                executable="/usr/bin/echo",
                version="1.0.0",
                capabilities=[],
                dependency_manifest_version=None,
                dependencies=[{"executable": "/usr/bin/python3", "sha256": "a" * 64}],
            )

        # Empty dependencies list → Pydantic rejects (min_length=1)
        with pytest.raises(ValidationError):
            AdapterRegisterRequest(
                executable="/usr/bin/echo",
                version="1.0.0",
                capabilities=[],
                dependency_manifest_version=1,
                dependencies=[],
            )

        # Invalid dependency_manifest_version (0, below ge=1)
        with pytest.raises(ValidationError):
            AdapterRegisterRequest(
                executable="/usr/bin/echo",
                version="1.0.0",
                capabilities=[],
                dependency_manifest_version=0,
                dependencies=[{"executable": "/usr/bin/python3", "sha256": "a" * 64}],
            )

        # Valid request passes Pydantic validation (validation of dep contents
        # happens downstream in validate_dependency_manifest)
        req = AdapterRegisterRequest(
            executable="/usr/bin/echo",
            version="1.0.0",
            capabilities=[],
            dependency_manifest_version=1,
            dependencies=[{"executable": "/usr/bin/python3", "sha256": "a" * 64}],
        )
        assert req.dependency_manifest_version == 1
        assert len(req.dependencies) == 1

    def test_empty_deps_with_version_rejected(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        adapter = _make_fake_adapter_with_deps(tmp_path, "bad-adapter")

        with pytest.raises(ValueError, match="non-empty"):
            register_custom_adapter(
                executable=str(adapter),
                version="1.0.0",
                capabilities=[],
                dependency_manifest_version=1,
                dependencies=[],
            )

    def test_invalid_dependency_rejected_at_registration(self, tmp_path: Path, monkeypatch):
        """Registration with hAsh mismatch in dependency rejects."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        adapter = _make_fake_adapter_with_deps(tmp_path, "bad-dep-adapter")
        dep_exe = _make_fake_exe(tmp_path, "child")

        with pytest.raises(ValueError, match="sha256 mismatch"):
            register_custom_adapter(
                executable=str(adapter),
                version="1.0.0",
                capabilities=[],
                dependency_manifest_version=1,
                dependencies=[{"executable": str(dep_exe), "sha256": "a" * 64}],
            )

        # Zero residue
        assert load_adapters() == {}

    def test_dependency_manifest_persisted_in_store(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        dep_exe = _make_fake_exe(tmp_path, "child-cli-2")
        dep_hash = compute_sha256(str(dep_exe))
        adapter = _make_fake_adapter_with_deps(tmp_path, "persist-adapter", dep_exes=[dep_exe])

        entry = register_custom_adapter(
            executable=str(adapter),
            version="1.0.0",
            capabilities=[],
            dependency_manifest_version=1,
            dependencies=[{"executable": str(dep_exe), "sha256": dep_hash}],
        )

        # Reload from disk
        loaded = load_adapters()
        assert entry.id in loaded
        persisted = loaded[entry.id]
        assert persisted.dependency_manifest_version == 1
        assert len(persisted.dependencies) == 1
        assert persisted.dependencies[0]["executable"] == str(dep_exe)
        assert persisted.dependencies[0]["sha256"] == dep_hash


# ---------------------------------------------------------------------------
# Token-Metering Truthfulness Tests
# ---------------------------------------------------------------------------


class TestTokenMeteringTruthfulness:
    """Test that token_metering capability enforces truthful token_usage at conformance."""

    def test_token_metering_adapter_with_valid_usage_succeeds(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        adapter = _make_fake_adapter_with_deps(tmp_path, "metered-adapter", has_token_metering=True)
        dep_exe = _make_fake_exe(tmp_path, "meter-child")
        dep_hash = compute_sha256(str(dep_exe))

        entry = register_custom_adapter(
            executable=str(adapter),
            version="1.0.0",
            capabilities=["token_metering"],
            dependency_manifest_version=1,
            dependencies=[{"executable": str(dep_exe), "sha256": dep_hash}],
        )
        assert entry.status == "pending"
        assert "token_metering" in entry.capabilities

    def test_token_metering_adapter_with_null_usage_fails(self, tmp_path: Path, monkeypatch):
        """Adapter declares token_metering but conformance probe returns null token_usage."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        # Use regular adapter (no token_usage)
        adapter = _make_fake_adapter_with_deps(tmp_path, "null-usage-adapter", has_token_metering=False)
        dep_exe = _make_fake_exe(tmp_path, "null-usage-child")
        dep_hash = compute_sha256(str(dep_exe))

        with pytest.raises(ValueError, match="token_usage"):
            register_custom_adapter(
                executable=str(adapter),
                version="1.0.0",
                capabilities=["token_metering"],
                dependency_manifest_version=1,
                dependencies=[{"executable": str(dep_exe), "sha256": dep_hash}],
            )

    def test_adapter_without_token_metering_remains_valid(self, tmp_path: Path, monkeypatch):
        """Adapter without token_metering capability is valid with null token_usage."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        adapter = _make_fake_adapter_with_deps(tmp_path, "no-meter-adapter", has_token_metering=False)
        dep_exe = _make_fake_exe(tmp_path, "no-meter-child")
        dep_hash = compute_sha256(str(dep_exe))

        entry = register_custom_adapter(
            executable=str(adapter),
            version="1.0.0",
            capabilities=[],
            dependency_manifest_version=1,
            dependencies=[{"executable": str(dep_exe), "sha256": dep_hash}],
        )
        assert entry.status == "pending"


# ---------------------------------------------------------------------------
# Snapshot Comparison Tests
# ---------------------------------------------------------------------------


class TestDependencySnapshotComparison:
    """Test that dependency facts are included in re-registration identity checks."""

    def test_dependency_change_forces_pending_re_registration(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        dep_exe = _make_fake_exe(tmp_path, "dep-a")
        dep_hash = compute_sha256(str(dep_exe))
        adapter = _make_fake_adapter_with_deps(tmp_path, "snapshot-adapter", dep_exes=[dep_exe])

        # First registration with dep
        entry1 = register_custom_adapter(
            executable=str(adapter),
            version="1.0.0",
            capabilities=[],
            dependency_manifest_version=1,
            dependencies=[{"executable": str(dep_exe), "sha256": dep_hash}],
        )
        assert entry1.dependency_manifest_version == 1

        # Re-register with different deps (still valid for idempotence check)
        dep2 = _make_fake_exe(tmp_path, "dep-b")
        dep2_hash = compute_sha256(str(dep2))
        entry2 = register_custom_adapter(
            executable=str(adapter),
            version="1.0.0",
            capabilities=[],
            dependency_manifest_version=1,
            dependencies=[{"executable": str(dep2), "sha256": dep2_hash}],
        )
        assert entry2.status == "pending"  # Always pending on change
        assert entry2.dependencies[0]["executable"] == str(dep2)

    def test_identical_deps_is_idempotent(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        dep_exe = _make_fake_exe(tmp_path, "idem-dep")
        dep_hash = compute_sha256(str(dep_exe))
        adapter = _make_fake_adapter_with_deps(tmp_path, "idem-adapter", dep_exes=[dep_exe])

        entry1 = register_custom_adapter(
            executable=str(adapter),
            version="1.0.0",
            capabilities=[],
            dependency_manifest_version=1,
            dependencies=[{"executable": str(dep_exe), "sha256": dep_hash}],
        )
        # Identical re-registration
        entry2 = register_custom_adapter(
            executable=str(adapter),
            version="1.0.0",
            capabilities=[],
            dependency_manifest_version=1,
            dependencies=[{"executable": str(dep_exe), "sha256": dep_hash}],
        )
        assert entry2.status == "pending"
        assert entry2.registered_at == entry1.registered_at  # Preserved metadata

    def test_legacy_persisted_adapter_with_no_manifest_retains_behavior(self, tmp_path: Path, monkeypatch):
        """Legacy adapter loaded from persisted store (no manifest fields) retains behavior.

        This is the deserialization path — a pre-existing AdapterEntry record
        that lacks the dependency_manifest_version/dependencies fields is loaded
        with None/[] and retains its exact launch behavior without mutation.
        """
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        adapter = _make_fake_adapter_with_deps(tmp_path, "legacy-no-manifest")
        adapter_hash = compute_sha256(str(adapter))
        adapter_id = f"legacy-no-manifest-adapter-{adapter_hash[:12]}"

        # Construct a legacy entry directly (simulating a persisted record
        # that was created before seq244 was deployed).
        from runtime.orchestrator.adapter_store import save_adapter as _save
        from datetime import datetime, timezone
        legacy_entry = AdapterEntry(
            id=adapter_id,
            name="legacy-no-manifest",
            executable=str(adapter),
            executable_hash=adapter_hash,
            version="1.0.0",
            capabilities=[],
            contract_version=1,
            workspace_adapter="pi",
            status="pending",
            registered_at=datetime.now(timezone.utc).isoformat(),
            registered_by="legacy-importer",
            # No dependency_manifest_version or dependencies — legacy
        )
        _save(legacy_entry)

        # Load it back — from_dict should produce None / []
        loaded = load_adapters()
        assert adapter_id in loaded
        entry = loaded[adapter_id]
        assert entry.dependency_manifest_version is None
        assert entry.dependencies == []

        # Approve the legacy adapter (with matching None/empty facts)
        approved = approve_adapter(
            adapter_id=entry.id,
            executable=entry.executable,
            executable_hash=entry.executable_hash,
            version=entry.version,
            capabilities=entry.capabilities,
            contract_version=entry.contract_version,
            workspace_adapter=entry.workspace_adapter,
        )
        assert approved.status == "approved"
        # Legacy fields remain None
        assert approved.dependency_manifest_version is None
        assert approved.dependencies == []

    def test_legacy_persisted_adapter_round_trips_through_store(self, tmp_path: Path, monkeypatch):
        """A legacy adapter serialized without manifest fields deserializes correctly."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        adapter = _make_fake_adapter_with_deps(tmp_path, "roundtrip-legacy")
        adapter_hash = compute_sha256(str(adapter))
        adapter_id = f"roundtrip-legacy-adapter-{adapter_hash[:12]}"

        from runtime.orchestrator.adapter_store import save_adapter as _save
        from datetime import datetime, timezone

        # Construct entry with explicit None/[] manifest fields
        legacy_entry = AdapterEntry(
            id=adapter_id,
            name="roundtrip-legacy",
            executable=str(adapter),
            executable_hash=adapter_hash,
            version="1.0.0",
            capabilities=[],
            contract_version=1,
            workspace_adapter="pi",
            status="pending",
            registered_at=datetime.now(timezone.utc).isoformat(),
            registered_by="test",
            dependency_manifest_version=None,
            dependencies=[],
        )

        # to_dict should omit None/empty manifest fields
        d = legacy_entry.to_dict()
        assert "dependency_manifest_version" not in d
        assert "dependencies" not in d

        # from_dict of the serialized dict should restore None/[]
        restored = AdapterEntry.from_dict(d)
        assert restored.dependency_manifest_version is None
        assert restored.dependencies == []

        # Persist and reload from disk
        _save(legacy_entry)
        loaded = load_adapters()
        reloaded = loaded[adapter_id]
        assert reloaded.dependency_manifest_version is None
        assert reloaded.dependencies == []


# ---------------------------------------------------------------------------
# Pre-launch Dependency Revalidation Tests
# ---------------------------------------------------------------------------


class TestPreLaunchDependencyRevalidation:
    """Test that dependency revalidation occurs before each launch."""

    def test_tampered_dependency_blocks_launch(self, tmp_path: Path, monkeypatch):
        """Verify CustomAdapterExecutor fails when a dependency is tampered."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        dep_exe = _make_fake_exe(tmp_path, "tamper-dep")
        dep_hash = compute_sha256(str(dep_exe))
        adapter = _make_fake_adapter_with_deps(tmp_path, "tamper-adapter", dep_exes=[dep_exe], adapter_id="tamper-adapter")

        entry = register_custom_adapter(
            executable=str(adapter),
            version="1.0.0",
            capabilities=[],
            dependency_manifest_version=1,
            dependencies=[{"executable": str(dep_exe), "sha256": dep_hash}],
        )

        # Approve the adapter (with manifest facts)
        approved = approve_adapter(
            adapter_id=entry.id,
            executable=entry.executable,
            executable_hash=entry.executable_hash,
            version=entry.version,
            capabilities=entry.capabilities,
            contract_version=entry.contract_version,
            workspace_adapter=entry.workspace_adapter,
            dependency_manifest_version=entry.dependency_manifest_version,
            dependencies=entry.dependencies,
        )
        assert approved.status == "approved"

        # Tamper with the dependency
        dep_exe.write_text("#!/bin/sh\necho tampered\n")
        dep_exe.chmod(0o755)

        # resolve_adapter should still work (adapter itself is intact)
        resolved = resolve_adapter(entry.id)
        assert resolved is not None

        # But CustomAdapterExecutor pre-launch check will detect tampered dep
        from runtime.orchestrator.executors import CustomAdapterExecutor
        executor = CustomAdapterExecutor(
            profile_name="test-profile",
            adapter_entry_id=entry.id,
            adapter_executable=entry.executable,
            adapter_hash=entry.executable_hash,
            adapter_version=entry.version,
            adapter_contract_version=entry.contract_version,
            provider="test",
        )
        executor.set_dependency_manifest(1, [{"executable": str(dep_exe), "sha256": dep_hash}])
        executor.set_invocation_context(
            agent="dev_agent",
            org="happyranch",
            invocation_kind="task",
        )

        workspace = tmp_path / "ws"
        workspace.mkdir()
        result = executor.run(
            workspace=workspace,
            prompt="test",
            timeout_seconds=10,
        )

        assert not result.success
        assert "dependency" in result.error.lower() or "hash mismatch" in result.error.lower()

    def test_nonexistent_dependency_blocks_launch(self, tmp_path: Path, monkeypatch):
        """Dependency that was deleted after registration blocks launch."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        dep_exe = _make_fake_exe(tmp_path, "deletable-dep")
        dep_hash = compute_sha256(str(dep_exe))
        adapter = _make_fake_adapter_with_deps(tmp_path, "delete-dep-adapter", dep_exes=[dep_exe], adapter_id="delete-dep-adapter")

        entry = register_custom_adapter(
            executable=str(adapter),
            version="1.0.0",
            capabilities=[],
            dependency_manifest_version=1,
            dependencies=[{"executable": str(dep_exe), "sha256": dep_hash}],
        )

        approved = approve_adapter(
            adapter_id=entry.id,
            executable=entry.executable,
            executable_hash=entry.executable_hash,
            version=entry.version,
            capabilities=entry.capabilities,
            contract_version=entry.contract_version,
            workspace_adapter=entry.workspace_adapter,
            dependency_manifest_version=entry.dependency_manifest_version,
            dependencies=entry.dependencies,
        )
        assert approved.status == "approved"

        # Delete the dependency
        dep_exe.unlink()

        from runtime.orchestrator.executors import CustomAdapterExecutor
        executor = CustomAdapterExecutor(
            profile_name="test-profile",
            adapter_entry_id=entry.id,
            adapter_executable=entry.executable,
            adapter_hash=entry.executable_hash,
            adapter_version=entry.version,
            adapter_contract_version=entry.contract_version,
            provider="test",
        )
        executor.set_dependency_manifest(1, [{"executable": str(dep_exe), "sha256": dep_hash}])
        executor.set_invocation_context(
            agent="dev_agent",
            org="happyranch",
            invocation_kind="task",
        )

        workspace = tmp_path / "ws2"
        workspace.mkdir()
        result = executor.run(
            workspace=workspace,
            prompt="test",
            timeout_seconds=10,
        )

        assert not result.success
        assert "no longer exists" in result.error.lower()

    def test_valid_dependency_permits_launch(self, tmp_path: Path, monkeypatch):
        """Valid declared dependency enables a successful launch."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        dep_exe = _make_fake_exe(tmp_path, "valid-dep")
        dep_hash = compute_sha256(str(dep_exe))
        # Use a name that will generate the correct adapter id
        adapter = _make_fake_adapter_with_deps(tmp_path, "valid-adapter", dep_exes=[dep_exe], adapter_id="valid-adapter")

        entry = register_custom_adapter(
            executable=str(adapter),
            version="1.0.0",
            capabilities=[],
            dependency_manifest_version=1,
            dependencies=[{"executable": str(dep_exe), "sha256": dep_hash}],
        )

        approved = approve_adapter(
            adapter_id=entry.id,
            executable=entry.executable,
            executable_hash=entry.executable_hash,
            version=entry.version,
            capabilities=entry.capabilities,
            contract_version=entry.contract_version,
            workspace_adapter=entry.workspace_adapter,
            dependency_manifest_version=entry.dependency_manifest_version,
            dependencies=entry.dependencies,
        )
        assert approved.status == "approved"

        from runtime.orchestrator.executors import CustomAdapterExecutor
        executor = CustomAdapterExecutor(
            profile_name="test-profile",
            adapter_entry_id=entry.id,
            adapter_executable=entry.executable,
            adapter_hash=entry.executable_hash,
            adapter_version=entry.version,
            adapter_contract_version=entry.contract_version,
            provider="test",
        )
        executor.set_dependency_manifest(1, [{"executable": str(dep_exe), "sha256": dep_hash}])
        executor.set_invocation_context(
            agent="dev_agent",
            org="happyranch",
            invocation_kind="task",
        )

        workspace = tmp_path / "ws3"
        workspace.mkdir()
        result = executor.run(
            workspace=workspace,
            prompt="test",
            timeout_seconds=10,
        )

        assert result.success

    def test_callback_command_in_adapter_path(self, tmp_path: Path, monkeypatch):
        """TASK-3973: Manifest adapter launch PATH includes happyranch callback directory.

        When a dependency manifest is declared, the adapter subprocess PATH
        must include the happyranch CLI directory (so the adapter's child agent
        can invoke `happyranch report-completion`) alongside the scrubbed
        /usr/bin:/bin base.
        """
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        dep_exe = _make_fake_exe(tmp_path, "cb-dep")
        dep_hash = compute_sha256(str(dep_exe))
        adapter = _make_fake_adapter_with_deps(
            tmp_path, "cb-adapter", dep_exes=[dep_exe], adapter_id="cb-adapter"
        )

        entry = register_custom_adapter(
            executable=str(adapter),
            version="1.0.0",
            capabilities=[],
            dependency_manifest_version=1,
            dependencies=[{"executable": str(dep_exe), "sha256": dep_hash}],
        )

        approved = approve_adapter(
            adapter_id=entry.id,
            executable=entry.executable,
            executable_hash=entry.executable_hash,
            version=entry.version,
            capabilities=entry.capabilities,
            contract_version=entry.contract_version,
            workspace_adapter=entry.workspace_adapter,
            dependency_manifest_version=entry.dependency_manifest_version,
            dependencies=entry.dependencies,
        )
        assert approved.status == "approved"

        # Create a fake happyranch CLI in a temp directory and put it on PATH
        happyranch_dir = tmp_path / "fake-happyranch-bin"
        happyranch_dir.mkdir()
        hrc = happyranch_dir / "happyranch"
        hrc.write_text("#!/bin/sh\necho fake-happyranch\n")
        hrc.chmod(0o755)
        monkeypatch.setenv("PATH", f"{happyranch_dir}:/usr/bin:/bin")

        from runtime.orchestrator.executors import CustomAdapterExecutor
        executor = CustomAdapterExecutor(
            profile_name="test-profile",
            adapter_entry_id=entry.id,
            adapter_executable=entry.executable,
            adapter_hash=entry.executable_hash,
            adapter_version=entry.version,
            adapter_contract_version=entry.contract_version,
            provider="test",
        )
        executor.set_dependency_manifest(
            1, [{"executable": str(dep_exe), "sha256": dep_hash}]
        )
        executor.set_invocation_context(
            agent="dev_agent", org="happyranch", invocation_kind="task",
        )

        # Patch _resolve_happyranch_callback_path to return our fake dir
        import runtime.orchestrator.executors as exec_mod
        original_resolve = exec_mod._resolve_happyranch_callback_path
        monkeypatch.setattr(
            exec_mod, "_resolve_happyranch_callback_path",
            lambda: str(happyranch_dir),
        )

        workspace = tmp_path / "ws-cb"
        workspace.mkdir()
        result = executor.run(
            workspace=workspace,
            prompt="test",
            timeout_seconds=10,
        )

        assert result.success

    def test_executor_path_not_discoverable_in_manifest_adapter_launch(self, tmp_path: Path, monkeypatch):
        """TASK-3973: Executor binaries are NOT discoverable on manifest adapter PATH.

        When a dependency manifest is declared, the adapter launch PATH must NOT
        include directories containing executor binaries (claude, codex, opencode,
        pi) — only /usr/bin:/bin plus the happyranch callback directory.
        """
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        dep_exe = _make_fake_exe(tmp_path, "no-exec-dep")
        dep_hash = compute_sha256(str(dep_exe))
        adapter = _make_fake_adapter_with_deps(
            tmp_path, "no-exec-adapter", dep_exes=[dep_exe], adapter_id="no-exec-adapter"
        )

        entry = register_custom_adapter(
            executable=str(adapter),
            version="1.0.0",
            capabilities=[],
            dependency_manifest_version=1,
            dependencies=[{"executable": str(dep_exe), "sha256": dep_hash}],
        )

        approved = approve_adapter(
            adapter_id=entry.id,
            executable=entry.executable,
            executable_hash=entry.executable_hash,
            version=entry.version,
            capabilities=entry.capabilities,
            contract_version=entry.contract_version,
            workspace_adapter=entry.workspace_adapter,
            dependency_manifest_version=entry.dependency_manifest_version,
            dependencies=entry.dependencies,
        )
        assert approved.status == "approved"

        # Create executor-like binaries in a directory that should NOT appear on PATH
        executor_dir = tmp_path / "fake-executor-bin"
        executor_dir.mkdir()
        for exe_name in ("claude", "codex", "opencode", "pi"):
            p = executor_dir / exe_name
            p.write_text("#!/bin/sh\necho fake\n")
            p.chmod(0o755)

        # Put executor_dir on the daemon's normalized PATH
        happyranch_dir = tmp_path / "fake-happyranch2"
        happyranch_dir.mkdir()
        hrc = happyranch_dir / "happyranch"
        hrc.write_text("#!/bin/sh\necho fake-happyranch\n")
        hrc.chmod(0o755)
        monkeypatch.setenv("PATH", f"{happyranch_dir}:{executor_dir}:/usr/bin:/bin")

        from runtime.orchestrator.executors import CustomAdapterExecutor
        executor = CustomAdapterExecutor(
            profile_name="test-profile",
            adapter_entry_id=entry.id,
            adapter_executable=entry.executable,
            adapter_hash=entry.executable_hash,
            adapter_version=entry.version,
            adapter_contract_version=entry.contract_version,
            provider="test",
        )
        executor.set_dependency_manifest(
            1, [{"executable": str(dep_exe), "sha256": dep_hash}]
        )
        executor.set_invocation_context(
            agent="dev_agent", org="happyranch", invocation_kind="task",
        )

        # The real _resolve_happyranch_callback_path sees both executor_dir
        # and happyranch_dir; it must pick happyranch_dir (the first hit
        # for the happyranch name).  Verify it does not return executor_dir.
        import runtime.orchestrator.executors as exec_mod
        callback_dir = exec_mod._resolve_happyranch_callback_path()
        assert callback_dir == str(happyranch_dir), (
            f"_resolve_happyranch_callback_path returned {callback_dir!r}, "
            f"expected {str(happyranch_dir)!r} — it must find happyranch, "
            f"not executor binaries"
        )
        # The callback directory must NOT be the executor directory
        assert callback_dir != str(executor_dir), (
            "_resolve_happyranch_callback_path must NOT return "
            "an executor binary directory"
        )

        # Now verify that a real launch with our fake happyranch succeeds
        monkeypatch.setattr(
            exec_mod, "_resolve_happyranch_callback_path",
            lambda: str(happyranch_dir),
        )
        workspace = tmp_path / "ws-no-exec"
        workspace.mkdir()
        result = executor.run(
            workspace=workspace,
            prompt="test",
            timeout_seconds=10,
        )

        assert result.success

    def test_legacy_adapter_launches_with_normal_env(self, tmp_path: Path, monkeypatch):
        """Legacy adapter (no manifest, loaded from persisted store) uses normal env."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        adapter = _make_fake_adapter_with_deps(tmp_path, "legacy-env-adapter", adapter_id="legacy-env-adapter")
        adapter_hash = compute_sha256(str(adapter))
        # adapter_id must match adapter_metadata.adapter in the fake script
        adapter_id = "legacy-env-adapter"

        from runtime.orchestrator.adapter_store import save_adapter as _save
        from datetime import datetime, timezone

        # Construct legacy entry via fixture (pre-existing store record)
        legacy_entry = AdapterEntry(
            id=adapter_id,
            name="legacy-env-adapter",
            executable=str(adapter),
            executable_hash=adapter_hash,
            version="1.0.0",
            capabilities=[],
            contract_version=1,
            workspace_adapter="pi",
            status="pending",
            registered_at=datetime.now(timezone.utc).isoformat(),
            registered_by="legacy-importer",
        )
        _save(legacy_entry)

        loaded = load_adapters()
        entry = loaded[adapter_id]
        assert entry.dependency_manifest_version is None

        approved = approve_adapter(
            adapter_id=entry.id,
            executable=entry.executable,
            executable_hash=entry.executable_hash,
            version=entry.version,
            capabilities=entry.capabilities,
            contract_version=entry.contract_version,
            workspace_adapter=entry.workspace_adapter,
        )

        from runtime.orchestrator.executors import CustomAdapterExecutor
        executor = CustomAdapterExecutor(
            profile_name="test-profile",
            adapter_entry_id=entry.id,
            adapter_executable=entry.executable,
            adapter_hash=entry.executable_hash,
            adapter_version=entry.version,
            adapter_contract_version=entry.contract_version,
            provider="test",
        )
        # No set_dependency_manifest call → legacy behavior
        executor.set_invocation_context(
            agent="dev_agent",
            org="happyranch",
            invocation_kind="task",
        )

        workspace = tmp_path / "ws-legacy"
        workspace.mkdir()
        result = executor.run(
            workspace=workspace,
            prompt="test",
            timeout_seconds=10,
        )

        assert result.success


# ---------------------------------------------------------------------------
# Pydantic Model Tests
# ---------------------------------------------------------------------------


class TestDependencyManifestModels:
    """Test the Pydantic models for dependency manifest."""

    def test_dependency_record_model_valid(self):
        rec = DependencyRecord(executable="/usr/bin/python3", sha256="a" * 64)
        assert rec.executable == "/usr/bin/python3"

    def test_dependency_record_sha256_length_validation(self):
        with pytest.raises(Exception):
            DependencyRecord(executable="/bin/sh", sha256="short")

    def test_dependency_manifest_model_valid(self):
        manifest = DependencyManifest(
            dependency_manifest_version=1,
            dependencies=[DependencyRecord(executable="/bin/sh", sha256="a" * 64)],
        )
        assert manifest.dependency_manifest_version == 1
        assert len(manifest.dependencies) == 1

    def test_dependency_manifest_empty_deps_rejected(self):
        with pytest.raises(Exception):
            DependencyManifest(dependency_manifest_version=1, dependencies=[])

    def test_dependency_manifest_json_schema(self):
        schema = DependencyManifest.model_json_schema()
        assert "dependency_manifest_version" in str(schema)
        assert "dependencies" in str(schema)

    def test_dependency_record_json_schema(self):
        schema = DependencyRecord.model_json_schema()
        assert "executable" in str(schema)
        assert "sha256" in str(schema)
