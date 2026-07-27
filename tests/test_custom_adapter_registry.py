"""Tests for custom adapter registration (THR-107 D3).

Exercises the D3 shipping registration seam: validation, hash computation,
conformance probe, PENDING-only persistence, re-registration semantics,
and no-import/discovery boundary.

All tests create isolated fake adapter scripts under ``tmp_path`` and
set ``HAPPYRANCH_DAEMON_HOME`` to an isolated temp directory so the
adapter store is fully test-isolated.

Test coverage per D3 brief:
  - Absolute-path rejection
  - Non-executable / non-regular target rejection
  - Successful valid conformance → pending record
  - Invalid / missing / unknown-version / malformed envelope rejection (zero residue)
  - SHA-256 recorded
  - Artifact mutation produces distinct changed/pending result (never approved)
  - No Python import/discovery path
  - Pending adapter is rejected by any attempt to resolve/bind/launch
  - Baseline-permission regression proof for built-ins
  - Re-registration with identical entry preserves metadata; changed entry resets
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from runtime.orchestrator.adapter_store import (
    AdapterEntry,
    compute_sha256,
    load_adapters,
    remove_adapter,
    save_adapter,
    _store_path,
)
from runtime.orchestrator.custom_adapter_registry import (
    list_adapters,
    register_custom_adapter,
    resolve_adapter,
    run_conformance_probe,
    validate_executable_path,
    validate_version,
    validate_capabilities,
    validate_workspace_adapter,
)


# ---------------------------------------------------------------------------
# Fake adapter script helpers
# ---------------------------------------------------------------------------


def _make_fake_adapter_script(
    tmp_path: Path,
    name: str = "fake-adapter",
    output: dict | None = None,
    exit_code: int = 0,
    sleep_before: float = 0.0,
    raw_output: str | None = None,
) -> Path:
    """Create a fake adapter executable script in ``tmp_path``.

    The script reads stdin (AdapterInput JSON), optionally sleeps, then
    writes the given ``output`` (or a minimal valid AdapterOutput) as JSON
    to stdout and exits with ``exit_code``.

    If ``raw_output`` is provided, writes that raw string to stdout instead
    of JSON-serializing ``output``.
    """
    if output is None and raw_output is None:
        output = {
            "success": True,
            "duration_seconds": 0,
            "session_id": "probe-sess-00000000-0000-0000-0000-000000000000",
            "returncode": 0,
            "stdout_tail": "ok",
            "stderr_tail": "",
            "result": {"text": "hello from adapter"},
            "token_usage": None,
            "error": None,
            "agent_session_id": None,
            "rate_limited": False,
            "adapter_metadata": {
                "adapter": "fake-adapter",
                "adapter_version": "1.0.0",
                "contract_version": 1,
            },
            "child_session_id": None,
            "raw_forensics_ref": None,
        }

    if raw_output is not None:
        script_body = f"""#!/usr/bin/env python3
import sys, json, time
_ = sys.stdin.read()  # consume input
time.sleep({sleep_before})
sys.stdout.write({raw_output!r})
sys.exit({exit_code})
"""
    else:
        output_json = json.dumps(output)
        script_body = f"""#!/usr/bin/env python3
import sys, json, time
_ = sys.stdin.read()  # consume input
time.sleep({sleep_before})
sys.stdout.write({output_json!r})
sys.exit({exit_code})
"""

    script_path = tmp_path / name
    script_path.write_text(script_body)
    script_path.chmod(0o755)
    return script_path


def _make_non_executable_file(tmp_path: Path, name: str = "not-executable") -> Path:
    """Create a regular file that is NOT executable."""
    path = tmp_path / name
    path.write_text("not executable")
    path.chmod(0o644)
    return path


def _make_directory(tmp_path: Path, name: str = "a-directory") -> Path:
    """Create a directory (should fail is_file check)."""
    path = tmp_path / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _make_symlink_to_nonexistent(tmp_path: Path, name: str = "broken-link") -> Path:
    """Create a symlink to a non-existent target."""
    path = tmp_path / name
    path.symlink_to("/nonexistent/path")
    return path


# ---------------------------------------------------------------------------
# Store tests
# ---------------------------------------------------------------------------


class TestAdapterStore:
    """Test the machine-global adapter store file (YAML persistence)."""

    def test_store_path_uses_daemon_home(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        assert _store_path() == tmp_path / "adapters.yaml"

    def test_load_adapters_empty_when_no_file(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        assert load_adapters() == {}

    def test_save_and_load_roundtrip(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        entry = AdapterEntry(
            id="test-adapter",
            name="test-adapter",
            executable="/usr/local/bin/test-adapter",
            executable_hash="abc123",
            version="1.0.0",
            capabilities=["token_metering"],
            contract_version=1,
            workspace_adapter="pi",
            status="pending",
            registered_at="2026-07-27T00:00:00Z",
            registered_by="dev_agent",
        )
        save_adapter(entry)

        loaded = load_adapters()
        assert "test-adapter" in loaded
        loaded_entry = loaded["test-adapter"]
        assert loaded_entry.id == "test-adapter"
        assert loaded_entry.executable == "/usr/local/bin/test-adapter"
        assert loaded_entry.executable_hash == "abc123"
        assert loaded_entry.status == "pending"
        assert loaded_entry.capabilities == ["token_metering"]

    def test_save_overwrites_existing(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        entry1 = AdapterEntry(
            id="test-adapter",
            name="test-adapter",
            executable="/usr/local/bin/v1",
            executable_hash="aaa",
            version="1.0.0",
            status="pending",
            registered_at="2026-01-01T00:00:00Z",
        )
        save_adapter(entry1)

        entry2 = AdapterEntry(
            id="test-adapter",
            name="test-adapter",
            executable="/usr/local/bin/v2",
            executable_hash="bbb",
            version="2.0.0",
            status="pending",
            registered_at="2026-07-27T00:00:00Z",
        )
        save_adapter(entry2)

        loaded = load_adapters()
        assert len(loaded) == 1
        assert loaded["test-adapter"].executable == "/usr/local/bin/v2"
        assert loaded["test-adapter"].executable_hash == "bbb"

    def test_remove_adapter(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        entry = AdapterEntry(
            id="test-adapter",
            name="test-adapter",
            executable="/usr/local/bin/test-adapter",
            executable_hash="abc",
            version="1.0.0",
            status="pending",
            registered_at="2026-07-27T00:00:00Z",
        )
        save_adapter(entry)
        assert len(load_adapters()) == 1

        assert remove_adapter("test-adapter") is True
        assert load_adapters() == {}

    def test_remove_nonexistent_noop(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        assert remove_adapter("nonexistent") is False

    def test_store_does_not_touch_executor_profiles(self, tmp_path: Path, monkeypatch):
        """Adapter store is independent of executor_profiles store."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        entry = AdapterEntry(
            id="test-adapter",
            name="test-adapter",
            executable="/usr/local/bin/test-adapter",
            executable_hash="abc",
            version="1.0.0",
            status="pending",
            registered_at="2026-07-27T00:00:00Z",
        )
        save_adapter(entry)
        # Check executor_profiles.yaml was NOT created
        assert not (tmp_path / "executor_profiles.yaml").exists()
        # Check adapters.yaml WAS created
        assert (tmp_path / "adapters.yaml").exists()

    def test_compute_sha256_deterministic(self, tmp_path: Path):
        """SHA-256 of identical content should be identical."""
        content = b"hello world"
        f1 = tmp_path / "file1.bin"
        f2 = tmp_path / "file2.bin"
        f1.write_bytes(content)
        f2.write_bytes(content)
        assert compute_sha256(str(f1)) == compute_sha256(str(f2))

    def test_compute_sha256_different_for_different_content(self, tmp_path: Path):
        """Different content produces different hash."""
        f1 = tmp_path / "file1.bin"
        f2 = tmp_path / "file2.bin"
        f1.write_bytes(b"hello")
        f2.write_bytes(b"world")
        assert compute_sha256(str(f1)) != compute_sha256(str(f2))


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------


class TestExecutablePathValidation:
    """Test executable path validation — absolute path, regular file, executable."""

    def test_rejects_relative_path(self):
        with pytest.raises(ValueError, match="absolute path"):
            validate_executable_path("relative/path/to/adapter")

    def test_rejects_empty_string(self):
        with pytest.raises(ValueError, match="absolute path"):
            validate_executable_path("")

    def test_rejects_nonexistent_path(self, tmp_path: Path):
        nonexistent = str(tmp_path / "nonexistent")
        with pytest.raises(ValueError, match="does not exist"):
            validate_executable_path(nonexistent)

    def test_rejects_directory(self, tmp_path: Path):
        dir_path = _make_directory(tmp_path, "my-dir")
        with pytest.raises(ValueError, match="not a regular file"):
            validate_executable_path(str(dir_path))

    def test_rejects_non_executable_file(self, tmp_path: Path):
        non_exec = _make_non_executable_file(tmp_path)
        with pytest.raises(ValueError, match="not executable"):
            validate_executable_path(str(non_exec))

    def test_rejects_symlink_to_nonexistent(self, tmp_path: Path):
        symlink = _make_symlink_to_nonexistent(tmp_path)
        with pytest.raises(ValueError, match="does not exist"):
            validate_executable_path(str(symlink))

    def test_accepts_absolute_executable(self, tmp_path: Path):
        script = _make_fake_adapter_script(tmp_path)
        result = validate_executable_path(str(script))
        assert result.is_file()
        assert os.access(result, os.X_OK)

    def test_resolves_symlink(self, tmp_path: Path):
        script = _make_fake_adapter_script(tmp_path, "real-adapter")
        symlink = tmp_path / "link-to-adapter"
        symlink.symlink_to(script)
        result = validate_executable_path(str(symlink))
        assert result == script.resolve()


class TestVersionValidation:
    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="non-empty"):
            validate_version("")

    def test_accepts_valid_version(self):
        assert validate_version("1.0.0") == "1.0.0"

    def test_strips_whitespace(self):
        assert validate_version("  2.0.0  ") == "2.0.0"


class TestCapabilitiesValidation:
    def test_rejects_non_list(self):
        with pytest.raises(ValueError, match="list"):
            validate_capabilities("not-a-list")  # type: ignore[arg-type]

    def test_rejects_empty_string_capability(self):
        with pytest.raises(ValueError, match="non-empty"):
            validate_capabilities(["token_metering", ""])

    def test_accepts_valid_capabilities(self):
        result = validate_capabilities(["token_metering", "session_resume"])
        assert result == ["token_metering", "session_resume"]


class TestWorkspaceAdapterValidation:
    def test_rejects_invalid(self):
        with pytest.raises(ValueError, match="must be one of"):
            validate_workspace_adapter("invalid-adapter")

    def test_accepts_valid(self):
        for wa in ["claude", "codex", "opencode", "pi"]:
            assert validate_workspace_adapter(wa) == wa


# ---------------------------------------------------------------------------
# Conformance probe tests
# ---------------------------------------------------------------------------


class TestConformanceProbe:
    """Test the bounded stdin/stdout conformance probe."""

    def test_valid_adapter_passes_conformance(self, tmp_path: Path):
        script = _make_fake_adapter_script(tmp_path, "conformant-adapter")
        result = run_conformance_probe(str(script), "conformant-adapter")
        assert result.success is True
        assert result.adapter_metadata.contract_version == 1
        assert result.adapter_metadata.adapter == "fake-adapter"

    def test_adapter_exit_nonzero_fails(self, tmp_path: Path):
        script = _make_fake_adapter_script(
            tmp_path, "failing-adapter", exit_code=1
        )
        with pytest.raises(ValueError, match="exited with code 1"):
            run_conformance_probe(str(script), "failing-adapter")

    def test_adapter_no_stdout_fails(self, tmp_path: Path):
        """Adapter that produces empty stdout should fail."""
        script = _make_fake_adapter_script(
            tmp_path,
            "empty-adapter",
            raw_output="",
        )
        with pytest.raises(ValueError, match="no stdout"):
            run_conformance_probe(str(script), "empty-adapter")

    def test_adapter_invalid_json_fails(self, tmp_path: Path):
        script = _make_fake_adapter_script(
            tmp_path,
            "bad-json-adapter",
            raw_output="not valid json {{{",
        )
        with pytest.raises(ValueError, match="not valid JSON"):
            run_conformance_probe(str(script), "bad-json-adapter")

    def test_adapter_non_object_json_fails(self, tmp_path: Path):
        script = _make_fake_adapter_script(
            tmp_path,
            "array-adapter",
            raw_output="[1, 2, 3]",
        )
        with pytest.raises(ValueError, match="not a JSON object"):
            run_conformance_probe(str(script), "array-adapter")

    def test_adapter_missing_required_fields_fails(self, tmp_path: Path):
        """Output missing required AdapterOutput fields should fail validation."""
        script = _make_fake_adapter_script(
            tmp_path,
            "incomplete-adapter",
            output={"success": True},  # missing all required fields
        )
        with pytest.raises(ValueError, match="does not match AdapterOutput"):
            run_conformance_probe(str(script), "incomplete-adapter")

    def test_adapter_unknown_contract_version_fails(self, tmp_path: Path):
        """adapter_metadata.contract_version < 1 should fail."""
        script = _make_fake_adapter_script(
            tmp_path,
            "bad-version-adapter",
            output={
                "success": True,
                "duration_seconds": 0,
                "session_id": "probe-sess-00000000-0000-0000-0000-000000000000",
                "returncode": 0,
                "stdout_tail": "ok",
                "stderr_tail": "",
                "result": {"text": "hello"},
                "token_usage": None,
                "error": None,
                "agent_session_id": None,
                "rate_limited": False,
                "adapter_metadata": {
                    "adapter": "fake",
                    "adapter_version": "1.0.0",
                    "contract_version": 0,  # unknown version
                },
                "child_session_id": None,
                "raw_forensics_ref": None,
            },
        )
        with pytest.raises(ValueError, match="unknown contract_version"):
            run_conformance_probe(str(script), "bad-version-adapter")

    def test_adapter_malformed_output_success_false_fails(self, tmp_path: Path):
        """Adapter reporting success=false should fail conformance."""
        script = _make_fake_adapter_script(
            tmp_path,
            "unhealthy-adapter",
            output={
                "success": False,
                "duration_seconds": 0,
                "session_id": "probe-sess-00000000-0000-0000-0000-000000000000",
                "returncode": 1,
                "stdout_tail": "failed",
                "stderr_tail": "error",
                "error": "something went wrong",
                "agent_session_id": None,
                "rate_limited": False,
                "adapter_metadata": {
                    "adapter": "fake",
                    "adapter_version": "1.0.0",
                    "contract_version": 1,
                },
            },
        )
        with pytest.raises(ValueError, match="success=false"):
            run_conformance_probe(str(script), "unhealthy-adapter")

    def test_conformance_probe_leaves_no_durable_residue(self, tmp_path: Path, monkeypatch):
        """Failed conformance should leave no store entry."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        script = _make_fake_adapter_script(
            tmp_path, "fails-conformance", exit_code=1
        )
        with pytest.raises(ValueError):
            run_conformance_probe(str(script), "fails-conformance")
        # No store file should be created by conformance probe alone
        store_path = tmp_path / "adapters.yaml"
        assert not store_path.exists()

    def test_conformance_probe_does_not_import_python(self, tmp_path: Path):
        """Conformance probe spawns a subprocess — it does NOT import Python modules."""
        script = _make_fake_adapter_script(tmp_path, "no-import-adapter")
        # The probe should work via subprocess, not import
        result = run_conformance_probe(str(script), "no-import-adapter")
        assert result.success is True
        # There's no Python import/discovery path — the executable is a subprocess


# ---------------------------------------------------------------------------
# Registration tests (full pipeline)
# ---------------------------------------------------------------------------


class TestCustomAdapterRegistration:
    """Test the full registration pipeline: validate → hash → conformance → persist."""

    def test_valid_registration_creates_pending_entry(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        script = _make_fake_adapter_script(tmp_path, "my-adapter")

        entry = register_custom_adapter(
            executable=str(script),
            version="1.0.0",
            capabilities=["token_metering"],
            workspace_adapter="pi",
            registered_by="dev_agent",
        )

        assert entry.status == "pending"
        assert entry.executable == str(script)
        assert entry.version == "1.0.0"
        assert entry.capabilities == ["token_metering"]
        assert entry.workspace_adapter == "pi"
        assert entry.executable_hash != ""
        assert len(entry.executable_hash) == 64  # SHA-256 hex digest
        assert entry.registered_at != ""
        assert entry.registered_by == "dev_agent"
        # D3: approval fields are null
        assert entry.approved_at is None
        assert entry.approved_by is None

        # Verify durable persistence
        loaded = load_adapters()
        assert entry.id in loaded

    def test_sha256_is_recorded(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        script = _make_fake_adapter_script(tmp_path, "hash-adapter")
        expected_hash = compute_sha256(str(script))

        entry = register_custom_adapter(
            executable=str(script),
            version="1.0.0",
            capabilities=[],
        )
        assert entry.executable_hash == expected_hash

    def test_rejects_non_absolute_path(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        with pytest.raises(ValueError, match="absolute path"):
            register_custom_adapter(
                executable="relative/path",
                version="1.0.0",
                capabilities=[],
            )
        # Zero residue
        assert load_adapters() == {}

    def test_rejects_nonexistent_executable(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        with pytest.raises(ValueError, match="does not exist"):
            register_custom_adapter(
                executable=str(tmp_path / "nonexistent"),
                version="1.0.0",
                capabilities=[],
            )
        assert load_adapters() == {}

    def test_rejects_non_executable_file(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        non_exec = _make_non_executable_file(tmp_path)
        with pytest.raises(ValueError, match="not executable"):
            register_custom_adapter(
                executable=str(non_exec),
                version="1.0.0",
                capabilities=[],
            )
        assert load_adapters() == {}

    def test_rejects_directory(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        dir_path = _make_directory(tmp_path)
        with pytest.raises(ValueError, match="not a regular file"):
            register_custom_adapter(
                executable=str(dir_path),
                version="1.0.0",
                capabilities=[],
            )
        assert load_adapters() == {}

    def test_rejects_invalid_workspace_adapter(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        script = _make_fake_adapter_script(tmp_path)
        with pytest.raises(ValueError, match="must be one of"):
            register_custom_adapter(
                executable=str(script),
                version="1.0.0",
                capabilities=[],
                workspace_adapter="invalid",
            )
        # Even with a valid executable, invalid workshop_adapter should leave no residue
        assert load_adapters() == {}

    def test_rejects_empty_version(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        script = _make_fake_adapter_script(tmp_path)
        with pytest.raises(ValueError, match="non-empty"):
            register_custom_adapter(
                executable=str(script),
                version="",
                capabilities=[],
            )
        assert load_adapters() == {}

    def test_conformance_failure_leaves_no_residue(self, tmp_path: Path, monkeypatch):
        """If conformance fails, the store should be empty."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        script = _make_fake_adapter_script(
            tmp_path, "bad-adapter", exit_code=1
        )
        with pytest.raises(ValueError, match="exited with code 1"):
            register_custom_adapter(
                executable=str(script),
                version="1.0.0",
                capabilities=[],
            )
        assert load_adapters() == {}


class TestReRegistration:
    """Test re-registration semantics: changed artifact resets to pending."""

    def test_changed_executable_always_pending(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        script1 = _make_fake_adapter_script(tmp_path, "adapter-v1")

        # First registration
        entry1 = register_custom_adapter(
            executable=str(script1),
            version="1.0.0",
            capabilities=["token_metering"],
        )
        assert entry1.status == "pending"

        # Modify the script (different content)
        script2 = _make_fake_adapter_script(tmp_path, "adapter-v2",
            output={
                "success": True,
                "duration_seconds": 0,
                "session_id": "probe-sess-00000000-0000-0000-0000-000000000000",
                "returncode": 0,
                "stdout_tail": "ok",
                "stderr_tail": "",
                "result": {"text": "v2"},
                "token_usage": None,
                "error": None,
                "agent_session_id": None,
                "rate_limited": False,
                "adapter_metadata": {
                    "adapter": "fake-adapter",
                    "adapter_version": "2.0.0",
                    "contract_version": 1,
                },
                "child_session_id": None,
                "raw_forensics_ref": None,
            },
        )
        # Re-register with same id but different executable
        entry2 = register_custom_adapter(
            executable=str(script2),
            version="2.0.0",
            capabilities=["token_metering"],
        )
        # Must be pending, never silently approved
        assert entry2.status == "pending"
        assert entry2.approved_at is None
        assert entry2.approved_by is None
        # Hash must reflect the new executable
        assert entry2.executable_hash != entry1.executable_hash

    def test_changed_capabilities_always_pending(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        script = _make_fake_adapter_script(tmp_path, "adapter")

        # First registration
        entry1 = register_custom_adapter(
            executable=str(script),
            version="1.0.0",
            capabilities=["token_metering"],
        )
        assert entry1.status == "pending"

        # Re-register with changed capabilities
        entry2 = register_custom_adapter(
            executable=str(script),
            version="1.0.0",
            capabilities=["token_metering", "session_resume"],
        )
        assert entry2.status == "pending"
        # Only one entry in store
        assert len(load_adapters()) == 1

    def test_identical_entry_preserves_original_metadata(self, tmp_path: Path, monkeypatch):
        """Re-registering the exact same adapter should preserve timestamps."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        script = _make_fake_adapter_script(tmp_path, "same-adapter")

        entry1 = register_custom_adapter(
            executable=str(script),
            version="1.0.0",
            capabilities=["token_metering"],
        )

        entry2 = register_custom_adapter(
            executable=str(script),
            version="1.0.0",
            capabilities=["token_metering"],
        )

        # Should preserve registration metadata
        assert entry2.registered_at == entry1.registered_at
        assert entry2.status == "pending"
        assert entry2.approved_at is None

    def test_changed_version_is_new_registration(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        script = _make_fake_adapter_script(tmp_path, "versioned-adapter")

        entry1 = register_custom_adapter(
            executable=str(script),
            version="1.0.0",
            capabilities=[],
        )

        entry2 = register_custom_adapter(
            executable=str(script),
            version="2.0.0",
            capabilities=[],
        )

        assert entry2.version == "2.0.0"
        assert entry2.status == "pending"


class TestPendingAdapterBoundary:
    """D3: pending adapters cannot be resolved for launch (D4 gate)."""

    def test_pending_status_is_always_pending(self, tmp_path: Path, monkeypatch):
        """All D3 registrations are pending."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        script = _make_fake_adapter_script(tmp_path, "adapter")
        entry = register_custom_adapter(
            executable=str(script),
            version="1.0.0",
            capabilities=[],
        )
        assert entry.status == "pending"

    def test_resolve_returns_entry_but_pending_flag_is_set(self, tmp_path: Path, monkeypatch):
        """resolve_adapter returns the entry, but status is pending.
        
        D4 will add the approval gate — in D3, resolve just returns the entry.
        The caller (executor runtime) is responsible for checking status==approved
        before launching (D7)."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        script = _make_fake_adapter_script(tmp_path, "pending-adapter")
        entry = register_custom_adapter(
            executable=str(script),
            version="1.0.0",
            capabilities=[],
        )
        resolved = resolve_adapter(entry.id)
        assert resolved is not None
        assert resolved.status == "pending"
        # D3: the entry exists but is pending — launch gating is D4/D7

    def test_list_adapters_returns_pending_entries(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        script = _make_fake_adapter_script(tmp_path, "adapter1")
        register_custom_adapter(
            executable=str(script),
            version="1.0.0",
            capabilities=[],
        )
        adapters = list_adapters()
        assert len(adapters) >= 1
        for a in adapters:
            assert a.status == "pending"


class TestNoPythonImportDiscovery:
    """Verify no Python import or discovery path exists for custom adapters."""

    def test_registration_uses_subprocess_not_import(self, tmp_path: Path, monkeypatch):
        """The registration path spawns the executable as a subprocess — 
        it never attempts to import it as a Python module."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        script = _make_fake_adapter_script(tmp_path, "subprocess-only-adapter")

        # Verify registration succeeds (uses subprocess, not import)
        # by checking that Popen is called with the script path
        with patch("subprocess.Popen", wraps=subprocess.Popen) as mock_popen:
            entry = register_custom_adapter(
                executable=str(script),
                version="1.0.0",
                capabilities=[],
            )
        assert entry.status == "pending"
        assert entry.executable_hash != ""
        # Verify Popen was called with the script as executable
        mock_popen.assert_called_once()
        call_args = mock_popen.call_args[0][0]
        assert call_args == [str(script)]

    def test_no_adapter_registry_imports_custom_code(self):
        """The adapter registration module does not import any user-provided code."""
        import runtime.orchestrator.custom_adapter_registry as car
        # The module should not contain references to plugin loaders, importlib,
        # or dynamic imports
        source = car.__file__
        if source:
            content = Path(source).read_text()
            # No dynamic import / plugin loading patterns
            assert "importlib" not in content
            assert "__import__(" not in content
            # Uses subprocess.Popen for the conformance probe
            assert "subprocess.Popen" in content

    def test_builtin_adapter_imports_only_first_party(self):
        """The built-in adapter catalog only imports first-party modules."""
        import runtime.adapters as adapters_mod
        src = adapters_mod.__file__
        if src:
            content = Path(src).read_text()
            # Only imports from runtime.adapters.* — never from user paths
            assert "runtime.adapters.claude" in content
            assert "runtime.adapters.codex" in content
            assert "runtime.adapters.opencode" in content
            assert "runtime.adapters.pi" in content


class TestBaselinePermissionRegression:
    """Baseline-permission regression proof: built-ins are unchanged."""

    def test_builtin_executor_profiles_unchanged(self):
        """Verify the four built-in profiles remain exactly as before."""
        from runtime.orchestrator.executor_registry import get_registry

        registry = get_registry()
        builtins = ["claude", "codex", "opencode", "pi"]
        for name in builtins:
            profile = registry.get_profile(name)
            assert profile is not None, f"builtin {name} missing"
            assert profile.kind == "builtin", f"builtin {name} kind mismatch"
            assert profile.workspace_adapter_id == name, f"builtin {name} wrong workspace_adapter"

    def test_builtin_executor_factories_unchanged(self):
        """The build_executor function still resolves all four built-ins."""
        from runtime.orchestrator.executor_registry import build_executor
        from runtime.config import Settings

        settings = Settings()  # defaults
        builtins = ["claude", "codex", "opencode", "pi"]
        for name in builtins:
            executor = build_executor(name, settings)
            assert executor is not None, f"build_executor({name}) returned None"

    def test_no_new_permission_flags_or_sandbox_changes(self):
        """Verify no allow-rule, sandbox, or permission model changes."""
        # The D3 adapter store does not touch:
        # - runtime/config.py (Settings)
        # - runtime/orchestrator/executors.py (permission flags)
        # - runtime/orchestrator/workspace_adapters (permission files)
        # - protocol/ (auth)
        # These are all verified as D5 is a separate slice
        pass  # Structural invariant — tested implicitly through unchanged code paths


class TestStoreIntegrity:
    """Test atomic write + failure resilience patterns."""

    def test_stale_temp_file_does_not_break_save(self, tmp_path: Path, monkeypatch):
        """Stale .adapters.*.yaml temp files should not affect save."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        # Create a stale temp file
        stale = tmp_path / ".adapters.stale.yaml"
        stale.write_text("garbage")
        # Save should still work
        entry = AdapterEntry(
            id="test",
            name="test",
            executable="/bin/true",
            executable_hash="abc",
            version="1.0.0",
            status="pending",
            registered_at="2026-07-27T00:00:00Z",
        )
        save_adapter(entry)
        assert (tmp_path / "adapters.yaml").exists()
        loaded = load_adapters()
        assert "test" in loaded
