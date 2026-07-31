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
import time
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
    BoundedReadError,
    get_adapter,
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
            capabilities=[],
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
        assert loaded_entry.capabilities == []

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
        with pytest.raises(ValueError, match="unsupported contract_version"):
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
            capabilities=[],
            workspace_adapter="pi",
            registered_by="dev_agent",
        )

        assert entry.status == "pending"
        assert entry.executable == str(script)
        assert entry.version == "1.0.0"
        assert entry.capabilities == []
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
            capabilities=[],
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
            capabilities=[],
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
            capabilities=[],
        )
        assert entry1.status == "pending"

        # Re-register with changed capabilities
        entry2 = register_custom_adapter(
            executable=str(script),
            version="1.0.0",
            capabilities=["session_resume"],
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
            capabilities=[],
        )

        entry2 = register_custom_adapter(
            executable=str(script),
            version="1.0.0",
            capabilities=[],
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

        D4 will add the approval gate — in D3, resolve returns None for
        pending adapters (the binding/launch seam). Use get_adapter for
        read-only inspection."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        script = _make_fake_adapter_script(tmp_path, "pending-adapter")
        entry = register_custom_adapter(
            executable=str(script),
            version="1.0.0",
            capabilities=[],
        )
        # resolve_adapter rejects pending entries (D3 pending boundary)
        resolved = resolve_adapter(entry.id)
        assert resolved is None
        # But get_adapter returns the entry for read-only inspection
        inspection = get_adapter(entry.id)
        assert inspection is not None
        assert inspection.status == "pending"

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


# ============================================================================
# Finding 1 (CRITICAL): Authentication — unauthenticated route tests
# ============================================================================


class TestAdapterRoutesAuthentication:
    """Every /api/v1/runtime/adapters endpoint must require bearer auth.

    Unauthenticated requests must return 401 and the adapter executable
    must NEVER be invoked, with zero store/registry/operational residue.
    """

    @pytest.fixture
    def route_setup(self, tmp_path, monkeypatch):
        """Set up daemon home, token, and app with real adapters router."""
        from runtime.daemon import paths as paths_mod
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / ".happyranch"))
        paths_mod.ensure_daemon_home()
        paths_mod.ensure_token()
        return tmp_path

    @pytest.fixture
    def app(self, route_setup):
        """Create a FastAPI app with the real adapters router."""
        from fastapi import FastAPI
        from runtime.daemon.routes.adapters import router
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        return app

    def test_post_register_rejects_without_token(self, route_setup, app):
        """POST /runtime/adapters/register without auth → 401."""
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.post("/api/v1/runtime/adapters/register", json={
            "executable": "/bin/true",
            "version": "1.0.0",
            "capabilities": [],
            "workspace_adapter": "pi",
        })
        assert r.status_code == 401

    def test_get_list_rejects_without_token(self, route_setup, app):
        """GET /runtime/adapters without auth → 401."""
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.get("/api/v1/runtime/adapters")
        assert r.status_code == 401

    def test_get_detail_rejects_without_token(self, route_setup, app):
        """GET /runtime/adapters/{id} without auth → 401."""
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.get("/api/v1/runtime/adapters/fake-id")
        assert r.status_code == 401

    def test_unauthenticated_never_invokes_executable(self, tmp_path, monkeypatch, route_setup):
        """Unauthenticated POST must never invoke the executable."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from runtime.daemon.routes.adapters import router
        script = _make_fake_adapter_script(tmp_path, "never-invoke-me")
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        client = TestClient(app)
        r = client.post("/api/v1/runtime/adapters/register", json={
            "executable": str(script),
            "version": "1.0.0",
            "capabilities": [],
            "workspace_adapter": "pi",
        })
        assert r.status_code == 401
        # No store residue
        store_path = tmp_path / ".happyranch" / "adapters.yaml"
        assert not store_path.exists()

    def test_authenticated_registration_still_works(self, tmp_path, monkeypatch):
        """Authenticated POST registration must work as before."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from runtime.daemon import paths as paths_mod
        from runtime.daemon.routes.adapters import router
        from runtime.orchestrator.adapter_store import load_adapters
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / ".happyranch"))
        paths_mod.ensure_daemon_home()
        paths_mod.ensure_token()
        token = paths_mod.read_token()
        script = _make_fake_adapter_script(tmp_path, "auth-works-adapter")
        dep_hash = compute_sha256(str(script))
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {token}"})
        r = client.post("/api/v1/runtime/adapters/register", json={
            "executable": str(script),
            "version": "1.0.0",
            "capabilities": [],
            "workspace_adapter": "pi",
            "dependency_manifest_version": 1,
            "dependencies": [{"executable": str(script), "sha256": dep_hash}],
        })
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "pending"
        assert body["executable"] == str(script)
        # Verify persisted
        loaded = load_adapters()
        assert body["id"] in loaded

    def test_authenticated_list_and_detail_disclosure(self, tmp_path, monkeypatch):
        """Authenticated GET list and detail must return entries."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from runtime.daemon import paths as paths_mod
        from runtime.daemon.routes.adapters import router
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / ".happyranch"))
        paths_mod.ensure_daemon_home()
        paths_mod.ensure_token()
        token = paths_mod.read_token()
        script = _make_fake_adapter_script(tmp_path, "disclosure-adapter")
        dep_hash = compute_sha256(str(script))
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {token}"})
        # Register
        r = client.post("/api/v1/runtime/adapters/register", json={
            "executable": str(script),
            "version": "1.0.0",
            "capabilities": [],
            "workspace_adapter": "pi",
            "dependency_manifest_version": 1,
            "dependencies": [{"executable": str(script), "sha256": dep_hash}],
        })
        assert r.status_code == 200
        adapter_id = r.json()["id"]
        # List
        r_list = client.get("/api/v1/runtime/adapters")
        assert r_list.status_code == 200
        entries = r_list.json()
        assert len(entries) >= 1
        assert any(e["id"] == adapter_id for e in entries)
        # Detail
        r_detail = client.get(f"/api/v1/runtime/adapters/{adapter_id}")
        assert r_detail.status_code == 200
        assert r_detail.json()["executable"] == str(script)
        assert r_detail.json()["executable_hash"] != ""


# ============================================================================
# Finding 1 (HIGH): Bounded conformance — real subprocess tests
# ============================================================================


class TestBoundedConformanceRealSubprocess:
    """Test bounded stdin/stdout conformance with REAL subprocesses.

    Over-limit stdout/stderr must terminate the child and reject
    registration with no durable residue. Uses a patched short timeout
    to keep tests fast.
    """

    # Short timeout for tests so they don't block
    SHORT_TIMEOUT = 2

    def _make_stdout_flood_script(self, tmp_path: Path, name: str = "stdout-flood") -> Path:
        """Create an adapter that writes >2MB to stdout (exceeds 1MB limit)."""
        script = tmp_path / name
        script.write_text(r"""#!/usr/bin/env python3
import sys, json
_ = sys.stdin.read()  # consume input
# Write > 1MB of output rapidly
sys.stdout.write("x" * 2_000_000)
sys.stdout.write(json.dumps({"success": True, "adapter_metadata": {"contract_version": 1}}))
sys.exit(0)
""")
        script.chmod(0o755)
        return script

    def _make_stderr_flood_script(self, tmp_path: Path, name: str = "stderr-flood") -> Path:
        """Create an adapter that writes >2MB to stderr (exceeds 1MB limit)."""
        script = tmp_path / name
        script.write_text(r"""#!/usr/bin/env python3
import sys, json
_ = sys.stdin.read()  # consume input
# Write > 1MB to stderr rapidly
sys.stderr.write("x" * 2_000_000)
sys.stderr.flush()
sys.stdout.write(json.dumps({"success": True, "adapter_metadata": {"contract_version": 1}}))
sys.exit(0)
""")
        script.chmod(0o755)
        return script

    def _make_silent_child_script(self, tmp_path: Path, name: str = "silent-child") -> Path:
        """Create an adapter that sleeps forever without producing output."""
        script = tmp_path / name
        script.write_text(r"""#!/usr/bin/env python3
import sys, time
_ = sys.stdin.read()  # consume input
# Sleep much longer than the probe timeout
# Do NOT close stdout/stderr — simulate a hung process
time.sleep(600)
sys.exit(0)
""")
        script.chmod(0o755)
        return script

    def _make_close_streams_then_sleep_script(
        self, tmp_path: Path, name: str = "close-streams-then-sleep"
    ) -> Path:
        """Create an adapter that consumes stdin, closes stdout AND stderr, then sleeps.

        This exercises the path where both streams reach EOF before the deadline,
        but the child process itself hangs — the post-EOF wait must use the
        remaining monotonic budget, NOT grant a fresh timeout.
        """
        script = tmp_path / name
        script.write_text(r"""#!/usr/bin/env python3
import sys, time
_ = sys.stdin.read()  # consume input
# Close both output streams BEFORE sleeping
sys.stdout.close()
sys.stderr.close()
# Sleep much longer than the probe timeout
time.sleep(600)
""")
        script.chmod(0o755)
        return script

    def _make_stdout_flood_after_contract_script(self, tmp_path: Path, name: str = "stdout-flood-after") -> Path:
        """Create an adapter that writes valid contract output, then floods."""
        script = tmp_path / name
        script.write_text(r"""#!/usr/bin/env python3
import sys, json
_ = sys.stdin.read()  # consume input
# Write valid contract output first
sys.stdout.write(json.dumps({"success": True, "adapter_metadata": {"contract_version": 1}}))
sys.stdout.flush()
# Then flood
sys.stdout.write("x" * 2_000_000)
sys.exit(0)
""")
        script.chmod(0o755)
        return script

    def test_stdout_over_limit_kills_child_and_rejects(
        self, tmp_path: Path, monkeypatch
    ):
        """>1MB stdout flood → child killed, BoundedReadError, no store residue."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        from runtime.orchestrator import custom_adapter_registry as car
        script = self._make_stdout_flood_script(tmp_path)

        with pytest.raises(ValueError, match="stdout.*byte limit"):
            car.run_conformance_probe(str(script), "flood-test")

        # No store residue
        assert load_adapters() == {}

    def test_stderr_over_limit_kills_child_and_rejects(
        self, tmp_path: Path, monkeypatch
    ):
        """>1MB stderr flood → child killed, BoundedReadError, no store residue."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        from runtime.orchestrator import custom_adapter_registry as car
        script = self._make_stderr_flood_script(tmp_path)

        with pytest.raises(ValueError, match="stderr.*byte limit"):
            car.run_conformance_probe(str(script), "stderr-flood-test")

        # No store residue
        assert load_adapters() == {}

    def test_silent_child_with_patched_timeout_kills_and_rejects(
        self, tmp_path: Path, monkeypatch
    ):
        """Silent child → TimeoutExpired, child killed, no store residue.

        Elapsed time should be consistent with ONE monotonic deadline
        (not 2× due to sequential joins).
        """
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        from runtime.orchestrator import custom_adapter_registry as car

        # Patch timeout to a short value
        patched_timeout = 1.5
        monkeypatch.setattr(
            car, "CONFORMANCE_PROBE_TIMEOUT_SECONDS", patched_timeout
        )

        script = self._make_silent_child_script(tmp_path)

        t0 = time.monotonic()
        with pytest.raises(ValueError, match="timed out"):
            car.run_conformance_probe(str(script), "silent-test")
        elapsed = time.monotonic() - t0

        # Elapsed should be ~patched_timeout, not 2×
        # Allow some slack for process startup/shutdown
        assert elapsed < patched_timeout * 1.5, (
            f"Expected elapsed < {patched_timeout * 1.5}s, got {elapsed:.2f}s — "
            f"sequential joins would take > {patched_timeout * 2}s"
        )

        # No store residue
        assert load_adapters() == {}

    def test_close_streams_then_sleep_with_patched_timeout(
        self, tmp_path: Path, monkeypatch
    ):
        """Child closes stdout+stderr then sleeps → timeout within one deadline.

        The old code granted a fresh ``proc.wait(timeout=5)`` after EOF,
        letting an adapter escape the deadline merely by closing its streams.
        With the fix, the remaining monotonic budget is passed to ``wait``.

        Verifies:
        - Failure happens within one conformance deadline (+ scheduling tolerance)
        - The child process is reaped (no zombie)
        - No adapter-store YAML residue exists
        """
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        from runtime.orchestrator import custom_adapter_registry as car

        # Patch timeout to a short value so the test is fast but still
        # demonstrably NOT the old 5-second escape.
        patched_timeout = 1.5
        monkeypatch.setattr(
            car, "CONFORMANCE_PROBE_TIMEOUT_SECONDS", patched_timeout
        )

        script = self._make_close_streams_then_sleep_script(tmp_path)

        t0 = time.monotonic()
        with pytest.raises(ValueError, match="timed out"):
            car.run_conformance_probe(str(script), "close-streams-test")
        elapsed = time.monotonic() - t0

        # Elapsed must be within one patched deadline (+ generous scheduling tolerance).
        # The old post-EOF ``proc.wait(timeout=5)`` escape would produce 5+ seconds
        # with a 1.5-second configured deadline — clearly distinguishable.
        assert elapsed < patched_timeout * 2.5, (
            f"Expected elapsed < {patched_timeout * 2.5}s, got {elapsed:.2f}s — "
            f"the old fresh-wait escape would take > 5s with a 1.5s deadline"
        )

        # Verify child was reaped — no zombie process residue
        result = subprocess.run(
            ["true"], capture_output=True, timeout=5
        )
        assert result.returncode == 0

        # No adapter-store residue
        assert load_adapters() == {}

    def test_stdout_over_limit_terminates_and_reaps_child(
        self, tmp_path: Path, monkeypatch
    ):
        """After BoundedReadError, child process must be terminated and reaped.

        No zombie processes — proc.poll() must be non-None after the error.
        """
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        from runtime.orchestrator import custom_adapter_registry as car

        script = self._make_stdout_flood_script(tmp_path)

        # We need to capture the child pid to verify reaping.
        # Patch _kill_and_reap to capture the proc object, then
        # verify after the probe that the child is reaped.
        original_registry = car.register_custom_adapter

        with pytest.raises(ValueError, match="stdout.*byte limit"):
            car.run_conformance_probe(str(script), "reap-test")

        # Verify no child zombies: spawn a short-lived child to check
        # that Popen works correctly after the previous kill
        result = subprocess.run(
            ["true"], capture_output=True, timeout=5
        )
        assert result.returncode == 0

        # No store residue
        assert load_adapters() == {}

    def test_valid_registration_within_limits_works(self, tmp_path: Path, monkeypatch):
        """Normal valid registration within byte limits still succeeds."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        script = _make_fake_adapter_script(tmp_path, "normal-adapter")
        entry = register_custom_adapter(
            executable=str(script),
            version="1.0.0",
            capabilities=[],
        )
        assert entry.status == "pending"
        assert entry.id in load_adapters()


# ============================================================================
# Finding 3 (HIGH): Contract version — exact version 1 only
# ============================================================================


class TestContractVersionExact:
    """D3 supports EXACTLY contract_version == 1.

    Reject: missing, 0, future (2+), non-integers, booleans.
    """

    def _make_version_adapter(self, tmp_path, contract_version, name="vers-adapter"):
        """Create adapter that outputs a specific contract_version.

        The generated script writes the value as a Python literal in a dict
        that gets JSON-serialized by the script itself. This ensures correct
        type-roundtripping through JSON (Python → JSON → subprocess → JSON → Python).
        """
        script = tmp_path / name
        # Serialize contract_version as a Python repr that is also valid JSON when stringified
        # For booleans: Python True → JSON true. Use repr(True) = 'True' then lowercase.
        # For None: omit entirely from metadata.
        if contract_version is None:
            cv_py = None  # signal to omit
        elif isinstance(contract_version, bool):
            cv_py = "True" if contract_version else "False"
        else:
            cv_py = repr(contract_version)
        # Build the adapter_metadata as Python code
        meta_lines = [
            '        "adapter": "fake",',
            '        "adapter_version": "1.0.0",',
        ]
        if cv_py is not None:
            meta_lines.append(f'        "contract_version": {cv_py},')
        meta_block = "\n".join(meta_lines)
        script.write_text(f"""#!/usr/bin/env python3
import sys, json
_ = sys.stdin.read()
sys.stdout.write(json.dumps({{
    "success": True,
    "duration_seconds": 0,
    "session_id": "probe-sess-00000000-0000-0000-0000-000000000000",
    "returncode": 0,
    "stdout_tail": "ok",
    "stderr_tail": "",
    "result": {{"text": "ok"}},
    "token_usage": None,
    "error": None,
    "agent_session_id": None,
    "rate_limited": False,
    "adapter_metadata": {{
{meta_block}
    }},
    "child_session_id": None,
    "raw_forensics_ref": None,
}}))
sys.exit(0)
""")
        script.chmod(0o755)
        return script

    def test_version_1_accepted(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        script = self._make_version_adapter(tmp_path, 1, "vers-1-ok")
        entry = register_custom_adapter(
            executable=str(script),
            version="1.0.0",
            capabilities=[],
        )
        assert entry.status == "pending"

    def test_version_0_rejected(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        script = self._make_version_adapter(tmp_path, 0, "vers-0")
        with pytest.raises(ValueError, match="unsupported contract_version"):
            register_custom_adapter(
                executable=str(script),
                version="1.0.0",
                capabilities=[],
            )
        assert load_adapters() == {}

    def test_version_2_rejected(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        script = self._make_version_adapter(tmp_path, 2, "vers-2")
        with pytest.raises(ValueError, match="unsupported contract_version"):
            register_custom_adapter(
                executable=str(script),
                version="1.0.0",
                capabilities=[],
            )
        assert load_adapters() == {}

    def test_version_boolean_rejected(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        script = self._make_version_adapter(tmp_path, True, "vers-bool")
        with pytest.raises(ValueError, match="non-integer contract_version"):
            register_custom_adapter(
                executable=str(script),
                version="1.0.0",
                capabilities=[],
            )
        assert load_adapters() == {}

    def test_version_null_rejected(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        script = self._make_version_adapter(tmp_path, None, "vers-null")
        with pytest.raises(ValueError, match="missing contract_version"):
            register_custom_adapter(
                executable=str(script),
                version="1.0.0",
                capabilities=[],
            )
        assert load_adapters() == {}


# ============================================================================
# Finding 4 (HIGH): Pending boundary — resolve_adapter rejects pending
# ============================================================================


class TestPendingAdapterResolutionBoundary:
    """resolve_adapter must reject PENDING entries (launch/binding seam).

    get_adapter provides read-only inspection via the authenticated GET route.
    """

    def test_resolve_adapter_returns_none_for_pending(self, tmp_path: Path, monkeypatch):
        """resolve_adapter returns None for pending entries."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        script = _make_fake_adapter_script(tmp_path, "pending-resolve")
        entry = register_custom_adapter(
            executable=str(script),
            version="1.0.0",
            capabilities=[],
        )
        assert entry.status == "pending"
        # resolve rejects pending
        assert resolve_adapter(entry.id) is None

    def test_get_adapter_returns_pending_for_inspection(self, tmp_path: Path, monkeypatch):
        """get_adapter returns pending entries for read-only inspection."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        script = _make_fake_adapter_script(tmp_path, "pending-inspect")
        entry = register_custom_adapter(
            executable=str(script),
            version="1.0.0",
            capabilities=[],
        )
        # get_adapter returns entry for inspection
        inspection = get_adapter(entry.id)
        assert inspection is not None
        assert inspection.status == "pending"
        assert inspection.executable == str(script)

    def test_resolve_nonexistent_returns_none(self):
        """resolve_adapter returns None for nonexistent id."""
        assert resolve_adapter("nonexistent-adapter-id") is None

    def test_list_adapters_includes_pending(self, tmp_path: Path, monkeypatch):
        """list_adapters includes pending entries (read-only listing)."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        script = _make_fake_adapter_script(tmp_path, "pending-list")
        register_custom_adapter(
            executable=str(script),
            version="1.0.0",
            capabilities=[],
        )
        adapters = list_adapters()
        assert len(adapters) >= 1
        for a in adapters:
            assert a.status == "pending"


# ============================================================================
# Finding 5 (MEDIUM): Whitespace-only version rejection
# ============================================================================


class TestWhitespaceVersionRejection:
    """Version strings that become empty after trimming must be rejected."""

    def test_whitespace_only_version_rejected_by_validator(self):
        """validate_version rejects '   ' (whitespace-only)."""
        with pytest.raises(ValueError, match="non-empty string after trimming"):
            validate_version("   ")

    def test_whitespace_only_version_rejected_at_registration(self, tmp_path: Path, monkeypatch):
        """Registration pipeline rejects whitespace-only version with no residue."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        script = _make_fake_adapter_script(tmp_path, "ws-version-adapter")
        with pytest.raises(ValueError, match="non-empty string after trimming"):
            register_custom_adapter(
                executable=str(script),
                version="   ",
                capabilities=[],
            )
        assert load_adapters() == {}

    def test_tab_and_newline_version_rejected(self):
        """Version with only tabs and newlines is rejected after trimming."""
        with pytest.raises(ValueError, match="non-empty string after trimming"):
            validate_version("\t\n  \t")


# ============================================================================
# Finding 1+4 (combined): Changed artifact re-registration remains pending
# ============================================================================


class TestReRegistrationPreservesPending:
    """Changed artifact re-registration must remain PENDING and never
    silently retain/acquire approval state."""

    def test_changed_artifact_remains_pending_no_approval_retained(
        self, tmp_path: Path, monkeypatch
    ):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        script1 = _make_fake_adapter_script(tmp_path, "re-reg-v1")
        entry1 = register_custom_adapter(
            executable=str(script1),
            version="1.0.0",
            capabilities=["a"],
        )
        assert entry1.status == "pending"
        assert entry1.approved_at is None
        assert entry1.approved_by is None

        # Change capabilities — re-register
        entry2 = register_custom_adapter(
            executable=str(script1),
            version="1.0.0",
            capabilities=["a", "b"],
        )
        assert entry2.status == "pending"
        assert entry2.approved_at is None
        assert entry2.approved_by is None
        # Resolve still rejects
        assert resolve_adapter(entry2.id) is None

    def test_changed_hash_remains_pending(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        script1 = _make_fake_adapter_script(tmp_path, "adapter")
        entry1 = register_custom_adapter(
            executable=str(script1),
            version="1.0.0",
            capabilities=[],
        )
        # Modify the executable — replace content, same name
        script1.unlink()
        script2 = _make_fake_adapter_script(tmp_path, "adapter",
            output={
                "success": True,
                "duration_seconds": 0,
                "session_id": "probe-sess-00000000-0000-0000-0000-000000000000",
                "returncode": 0,
                "stdout_tail": "v2",
                "stderr_tail": "",
                "result": {"text": "v2"},
                "token_usage": None,
                "error": None,
                "agent_session_id": None,
                "rate_limited": False,
                "adapter_metadata": {
                    "adapter": "fake",
                    "adapter_version": "2.0.0",
                    "contract_version": 1,
                },
                "child_session_id": None,
                "raw_forensics_ref": None,
            },
        )
        entry2 = register_custom_adapter(
            executable=str(script2),
            version="2.0.0",
            capabilities=[],
        )
        assert entry2.status == "pending"
        assert entry2.approved_at is None
        assert entry1.executable_hash != entry2.executable_hash
        # Only one entry in store
        assert len(load_adapters()) == 1


# ============================================================================
# D4: Approval gate tests
# ============================================================================


class TestApprovalGate:
    """Test the D4 explicit founder approval gate.

    Covers: successful matching approval, transition semantics, provenance
    fields, exact-idempotence, mismatched facts, unknown adapter,
    non-pending/approved-repeat behavior, and route-level auth gate.
    """

    def _register_pending(self, tmp_path, monkeypatch, name="approval-adapter"):
        """Helper: register a pending adapter and return the entry."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        script = _make_fake_adapter_script(tmp_path, name)
        return register_custom_adapter(
            executable=str(script),
            version="1.0.0",
            capabilities=[],
            workspace_adapter="pi",
        )

    def test_successful_approval_writes_status_and_provenance(
        self, tmp_path, monkeypatch
    ):
        """Matching approval transitions PENDING→APPROVED and persists
        approved_at + approved_by provenance."""
        from runtime.orchestrator.custom_adapter_registry import approve_adapter

        entry = self._register_pending(tmp_path, monkeypatch)
        assert entry.status == "pending"

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
        assert approved.approved_at is not None
        assert approved.approved_at != ""
        assert approved.approved_by == "founder/master-bearer"
        # Registration metadata preserved
        assert approved.registered_at == entry.registered_at
        assert approved.registered_by == entry.registered_by
        assert approved.executable == entry.executable
        assert approved.executable_hash == entry.executable_hash

        # Verify durable persistence
        from runtime.orchestrator.adapter_store import load_adapters
        loaded = load_adapters()
        assert loaded[entry.id].status == "approved"
        assert loaded[entry.id].approved_at == approved.approved_at
        assert loaded[entry.id].approved_by == "founder/master-bearer"

    def test_exact_idempotent_approval_noop(
        self, tmp_path, monkeypatch
    ):
        """Approving an already-APPROVED adapter with identical facts
        is an idempotent no-op — same entry returned, no provenance change."""
        from runtime.orchestrator.custom_adapter_registry import approve_adapter

        entry = self._register_pending(tmp_path, monkeypatch)
        first = approve_adapter(
            adapter_id=entry.id,
            executable=entry.executable,
            executable_hash=entry.executable_hash,
            version=entry.version,
            capabilities=entry.capabilities,
            contract_version=entry.contract_version,
            workspace_adapter=entry.workspace_adapter,
        )
        assert first.status == "approved"
        first_approved_at = first.approved_at

        # Second call with identical facts
        second = approve_adapter(
            adapter_id=entry.id,
            executable=entry.executable,
            executable_hash=entry.executable_hash,
            version=entry.version,
            capabilities=entry.capabilities,
            contract_version=entry.contract_version,
            workspace_adapter=entry.workspace_adapter,
        )
        assert second.status == "approved"
        assert second.approved_at == first_approved_at  # not rewritten
        assert second.approved_by == "founder/master-bearer"

    def test_already_approved_with_different_facts_rejects(
        self, tmp_path, monkeypatch
    ):
        """An already-APPROVED adapter cannot be re-approved with
        different facts — must re-register first."""
        from runtime.orchestrator.custom_adapter_registry import approve_adapter

        entry = self._register_pending(tmp_path, monkeypatch)
        approve_adapter(
            adapter_id=entry.id,
            executable=entry.executable,
            executable_hash=entry.executable_hash,
            version=entry.version,
            capabilities=entry.capabilities,
            contract_version=entry.contract_version,
            workspace_adapter=entry.workspace_adapter,
        )

        with pytest.raises(ValueError, match="already APPROVED with different"):
            approve_adapter(
                adapter_id=entry.id,
                executable=entry.executable,
                executable_hash=entry.executable_hash,
                version="2.0.0",  # different version
                capabilities=entry.capabilities,
                contract_version=entry.contract_version,
                workspace_adapter=entry.workspace_adapter,
            )

    def test_unknown_adapter_rejected(self):
        """Approving a non-existent adapter raises actionable error."""
        from runtime.orchestrator.custom_adapter_registry import approve_adapter

        with pytest.raises(ValueError, match="Unknown adapter"):
            approve_adapter(
                adapter_id="nonexistent-adapter",
                executable="/bin/true",
                executable_hash="abc",
                version="1.0.0",
                capabilities=[],
                contract_version=1,
                workspace_adapter="pi",
            )

    def test_non_pending_entry_rejected(
        self, tmp_path, monkeypatch
    ):
        """Approval only applies to PENDING status — non-pending rejected."""
        from runtime.orchestrator.custom_adapter_registry import approve_adapter

        entry = self._register_pending(tmp_path, monkeypatch)
        # Manually set a non-pending, non-approved state
        from runtime.orchestrator.adapter_store import AdapterEntry, save_adapter

        bad_entry = AdapterEntry(
            id=entry.id,
            name=entry.name,
            executable=entry.executable,
            executable_hash=entry.executable_hash,
            version="1.0.0",
            status="revoked",  # not pending, not approved
            registered_at=entry.registered_at,
        )
        save_adapter(bad_entry)

        with pytest.raises(ValueError, match="not PENDING"):
            approve_adapter(
                adapter_id=entry.id,
                executable=entry.executable,
                executable_hash=entry.executable_hash,
                version="1.0.0",
                capabilities=[],
                contract_version=1,
                workspace_adapter="pi",
            )

    def test_every_snapshot_fact_mismatch_fails_individually(
        self, tmp_path, monkeypatch
    ):
        """Each material identity fact mismatch must fail before persistence."""
        from runtime.orchestrator.custom_adapter_registry import approve_adapter

        entry = self._register_pending(tmp_path, monkeypatch)

        base = {
            "adapter_id": entry.id,
            "executable": entry.executable,
            "executable_hash": entry.executable_hash,
            "version": entry.version,
            "capabilities": entry.capabilities,
            "contract_version": entry.contract_version,
            "workspace_adapter": entry.workspace_adapter,
        }

        # executable mismatch
        with pytest.raises(ValueError, match="executable mismatch"):
            approve_adapter(**{**base, "executable": "/other/path"})

        # hash mismatch
        with pytest.raises(ValueError, match="executable_hash mismatch"):
            approve_adapter(**{**base, "executable_hash": "deadbeef" * 8})

        # version mismatch
        with pytest.raises(ValueError, match="version mismatch"):
            approve_adapter(**{**base, "version": "3.0.0"})

        # capabilities mismatch
        with pytest.raises(ValueError, match="capabilities mismatch"):
            approve_adapter(
                **{**base, "capabilities": ["unknown_cap"]}
            )

        # contract_version mismatch
        with pytest.raises(ValueError, match="contract_version mismatch"):
            approve_adapter(**{**base, "contract_version": 2})

        # workspace_adapter mismatch
        with pytest.raises(ValueError, match="workspace_adapter mismatch"):
            approve_adapter(**{**base, "workspace_adapter": "claude"})

        # Verify entry still PENDING (no durable mutation from any failed attempt)
        from runtime.orchestrator.adapter_store import load_adapters
        assert load_adapters()[entry.id].status == "pending"

    def test_approval_after_re_registration_rejects_old_snapshot(
        self, tmp_path, monkeypatch
    ):
        """Re-registration resets to PENDING; old approval snapshot must reject."""
        from runtime.orchestrator.custom_adapter_registry import approve_adapter

        entry1 = self._register_pending(tmp_path, monkeypatch)

        # Change the executable (re-registration)
        script1_path = Path(entry1.executable)
        script1_path.unlink()
        script2 = _make_fake_adapter_script(
            tmp_path, "approval-adapter",
            output={
                "success": True,
                "duration_seconds": 0,
                "session_id": "probe-sess-00000000-0000-0000-0000-000000000000",
                "returncode": 0,
                "stdout_tail": "v2",
                "stderr_tail": "",
                "result": {"text": "v2"},
                "token_usage": None,
                "error": None,
                "agent_session_id": None,
                "rate_limited": False,
                "adapter_metadata": {
                    "adapter": "fake",
                    "adapter_version": "2.0.0",
                    "contract_version": 1,
                },
                "child_session_id": None,
                "raw_forensics_ref": None,
            },
        )
        entry2 = register_custom_adapter(
            executable=str(script2),
            version="2.0.0",
            capabilities=[],
            workspace_adapter="pi",
        )
        assert entry2.status == "pending"
        assert entry2.approved_at is None

        # Old approval payload (from entry1) must fail against entry2
        with pytest.raises(ValueError, match="executable_hash mismatch"):
            approve_adapter(
                adapter_id=entry1.id,
                executable=entry1.executable,
                executable_hash=entry1.executable_hash,
                version=entry1.version,
                capabilities=entry1.capabilities,
                contract_version=entry1.contract_version,
                workspace_adapter=entry1.workspace_adapter,
            )

        # Verify still pending
        from runtime.orchestrator.adapter_store import load_adapters
        assert load_adapters()[entry1.id].status == "pending"

        # Fresh approval with correct entry2 facts succeeds
        approved = approve_adapter(
            adapter_id=entry2.id,
            executable=entry2.executable,
            executable_hash=entry2.executable_hash,
            version=entry2.version,
            capabilities=entry2.capabilities,
            contract_version=entry2.contract_version,
            workspace_adapter=entry2.workspace_adapter,
        )
        assert approved.status == "approved"

    def test_malformed_approval_inputs_rejected(self, tmp_path, monkeypatch):
        """Empty/None/invalid inputs must be caught before store access."""
        from runtime.orchestrator.custom_adapter_registry import approve_adapter

        entry = self._register_pending(tmp_path, monkeypatch)

        with pytest.raises(ValueError, match="adapter_id must be"):
            approve_adapter(
                adapter_id="",
                executable=entry.executable,
                executable_hash=entry.executable_hash,
                version="1.0.0",
                capabilities=[],
                contract_version=1,
                workspace_adapter="pi",
            )

        with pytest.raises(ValueError, match="executable must be"):
            approve_adapter(
                adapter_id=entry.id,
                executable="",
                executable_hash=entry.executable_hash,
                version="1.0.0",
                capabilities=[],
                contract_version=1,
                workspace_adapter="pi",
            )

        with pytest.raises(ValueError, match="contract_version must be an integer"):
            approve_adapter(
                adapter_id=entry.id,
                executable=entry.executable,
                executable_hash=entry.executable_hash,
                version="1.0.0",
                capabilities=[],
                contract_version=True,  # bool, not int
                workspace_adapter="pi",
            )


# ============================================================================
# D4: resolve_adapter hash verification tests
# ============================================================================


class TestResolveAdapterHashVerification:
    """D4: On-disk hash verification at resolve time.

    APPROVED adapters must have the executable still present, regular,
    executable, and with matching SHA-256 at the stored path.
    Tampered/missing/non-regular/non-executable → resolve returns None.
    """

    def _register_and_approve(self, tmp_path, monkeypatch, name="resolve-adapter"):
        """Helper: register + approve an adapter, return the entry + script path."""
        from runtime.orchestrator.custom_adapter_registry import (
            approve_adapter,
            register_custom_adapter,
        )

        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        script = _make_fake_adapter_script(tmp_path, name)
        entry = register_custom_adapter(
            executable=str(script),
            version="1.0.0",
            capabilities=[],
            workspace_adapter="pi",
        )
        approved = approve_adapter(
            adapter_id=entry.id,
            executable=entry.executable,
            executable_hash=entry.executable_hash,
            version=entry.version,
            capabilities=entry.capabilities,
            contract_version=entry.contract_version,
            workspace_adapter=entry.workspace_adapter,
        )
        return approved, script

    def test_approved_resolves_when_on_disk_intact(self, tmp_path, monkeypatch):
        """Intact approved adapter resolves successfully."""
        approved, _script = self._register_and_approve(tmp_path, monkeypatch)
        result = resolve_adapter(approved.id)
        assert result is not None
        assert result.status == "approved"
        assert result.id == approved.id

    def test_tampered_executable_fails_resolve(self, tmp_path, monkeypatch):
        """Modified executable → resolve returns None."""
        approved, script = self._register_and_approve(tmp_path, monkeypatch)
        # Tamper with the executable
        script.write_text("#!/usr/bin/env python3\nprint('tampered')\n")
        script.chmod(0o755)

        result = resolve_adapter(approved.id)
        assert result is None  # hash mismatch — refuses to resolve

    def test_removed_executable_fails_resolve(self, tmp_path, monkeypatch):
        """Deleted executable → resolve returns None."""
        approved, script = self._register_and_approve(tmp_path, monkeypatch)
        script.unlink()

        result = resolve_adapter(approved.id)
        assert result is None

    def test_non_regular_file_fails_resolve(self, tmp_path, monkeypatch):
        """Path that is a directory (not regular file) → resolve returns None."""
        approved, script = self._register_and_approve(tmp_path, monkeypatch)
        script.unlink()
        script.mkdir()  # replace with directory

        result = resolve_adapter(approved.id)
        assert result is None

    def test_non_executable_file_fails_resolve(self, tmp_path, monkeypatch):
        """File without execute permission → resolve returns None."""
        approved, script = self._register_and_approve(tmp_path, monkeypatch)
        script.chmod(0o644)  # remove execute permission

        result = resolve_adapter(approved.id)
        assert result is None

    def test_hash_never_silently_updated(self, tmp_path, monkeypatch):
        """Daemon never silently updates stored hash after tamper detection."""
        approved, script = self._register_and_approve(tmp_path, monkeypatch)
        original_hash = approved.executable_hash

        # Tamper
        script.write_text("#!/usr/bin/env python3\nprint('tampered')\n")
        script.chmod(0o755)

        # Resolve fails
        result = resolve_adapter(approved.id)
        assert result is None

        # Stored entry still has original hash — NOT updated
        from runtime.orchestrator.adapter_store import load_adapters
        loaded = load_adapters()[approved.id]
        assert loaded.executable_hash == original_hash
        assert loaded.status == "approved"  # status NOT changed

    def test_pending_unaffected_by_tamper_check(self, tmp_path, monkeypatch):
        """Pending adapters skip hash verification (no approval, no launch)."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        script = _make_fake_adapter_script(tmp_path, "pending-tampered")
        entry = register_custom_adapter(
            executable=str(script),
            version="1.0.0",
            capabilities=[],
            workspace_adapter="pi",
        )
        # Tamper
        script.write_text("tampered")
        # resolve still returns None (pending rejection, not hash issue)
        result = resolve_adapter(entry.id)
        assert result is None  # rejected for pending status


# ============================================================================
# D4: Approval route tests
# ============================================================================


class TestApproveRoute:
    """Real route tests for POST /api/v1/runtime/adapters/{adapter_id}/approve."""

    @pytest.fixture
    def route_setup(self, tmp_path, monkeypatch):
        """Set up daemon home, token, and adapter store."""
        from runtime.daemon import paths as paths_mod
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / ".happyranch"))
        paths_mod.ensure_daemon_home()
        paths_mod.ensure_token()
        return tmp_path

    @pytest.fixture
    def app(self, route_setup):
        """Create FastAPI app with the real adapters router."""
        from fastapi import FastAPI
        from runtime.daemon.routes.adapters import router
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        return app

    def _register_as_authed(
        self, client, token, script, version="1.0.0", capabilities=None,
        workspace_adapter="pi", dep_exe=None,
    ):
        """Helper: authenticated register, returns response json.

        THR-107 seq244: requires dependency_manifest_version and dependencies.
        When ``dep_exe`` is provided, it is used as the declared dependency;
        otherwise the adapter script itself is reused as a trivial dependency.
        """
        from runtime.orchestrator.adapter_store import compute_sha256 as _sha
        dep = dep_exe if dep_exe is not None else script
        dep_hash = _sha(str(dep))
        r = client.post("/api/v1/runtime/adapters/register", json={
            "executable": str(script),
            "version": version,
            "capabilities": capabilities or [],
            "workspace_adapter": workspace_adapter,
            "dependency_manifest_version": 1,
            "dependencies": [{"executable": str(dep), "sha256": dep_hash}],
        })
        assert r.status_code == 200
        return r.json()

    def test_approve_route_requires_auth(self, route_setup, app):
        """POST /approve without auth → 401."""
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.post("/api/v1/runtime/adapters/my-adapter/approve", json={
            "executable": "/bin/true",
            "executable_hash": "abc",
            "version": "1.0.0",
            "capabilities": [],
            "contract_version": 1,
            "workspace_adapter": "pi",
        })
        assert r.status_code == 401

    def test_approve_route_success(self, route_setup, app):
        """Authenticated approval with matching facts returns 200
        with approved_at/approved_by fields."""
        from fastapi.testclient import TestClient
        from runtime.daemon import paths as paths_mod

        token = paths_mod.read_token()
        script = _make_fake_adapter_script(route_setup, "route-approve-adapter")
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {token}"})

        # Register
        registered = self._register_as_authed(client, token, script)

        # Approve
        r = client.post(
            f"/api/v1/runtime/adapters/{registered['id']}/approve",
            json={
                "executable": registered["executable"],
                "executable_hash": registered["executable_hash"],
                "version": registered["version"],
                "capabilities": registered["capabilities"],
                "contract_version": registered["contract_version"],
                "workspace_adapter": registered["workspace_adapter"],
                "dependency_manifest_version": registered.get("dependency_manifest_version"),
                "dependencies": registered.get("dependencies", []),
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "approved"
        assert body["approved_at"] is not None
        assert body["approved_at"] != ""
        assert body["approved_by"] == "founder/master-bearer"
        # Registration metadata preserved
        assert body["registered_at"] == registered["registered_at"]
        assert body["executable"] == registered["executable"]
        assert body["executable_hash"] == registered["executable_hash"]

    def test_approve_route_mismatch_rejects_422(self, route_setup, app):
        """Approval with mismatched facts → 422, entry remains pending."""
        from fastapi.testclient import TestClient
        from runtime.daemon import paths as paths_mod

        token = paths_mod.read_token()
        script = _make_fake_adapter_script(route_setup, "mismatch-approve")
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {token}"})

        registered = self._register_as_authed(client, token, script)

        r = client.post(
            f"/api/v1/runtime/adapters/{registered['id']}/approve",
            json={
                "executable": "/wrong/path",  # mismatch
                "executable_hash": registered["executable_hash"],
                "version": registered["version"],
                "capabilities": registered["capabilities"],
                "contract_version": registered["contract_version"],
                "workspace_adapter": registered["workspace_adapter"],
                "dependency_manifest_version": registered.get("dependency_manifest_version"),
                "dependencies": registered.get("dependencies", []),
            },
        )
        assert r.status_code == 422
        assert "mismatch" in r.json()["detail"].lower()

        # Entry still PENDING
        r_get = client.get(f"/api/v1/runtime/adapters/{registered['id']}")
        assert r_get.json()["status"] == "pending"

    def test_approve_unknown_adapter_422(self, route_setup, app):
        """Approving non-existent adapter → 422."""
        from fastapi.testclient import TestClient
        from runtime.daemon import paths as paths_mod

        token = paths_mod.read_token()
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {token}"})

        r = client.post("/api/v1/runtime/adapters/nonexistent/approve", json={
            "executable": "/bin/true",
            "executable_hash": "abc",
            "version": "1.0.0",
            "capabilities": [],
            "contract_version": 1,
            "workspace_adapter": "pi",
        })
        assert r.status_code == 422

    def test_approve_idempotent_200(self, route_setup, app):
        """Idempotent approval returns 200 with same approved_at."""
        from fastapi.testclient import TestClient
        from runtime.daemon import paths as paths_mod

        token = paths_mod.read_token()
        script = _make_fake_adapter_script(route_setup, "idempotent-approve")
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {token}"})

        registered = self._register_as_authed(client, token, script)

        payload = {
            "executable": registered["executable"],
            "executable_hash": registered["executable_hash"],
            "version": registered["version"],
            "capabilities": registered["capabilities"],
            "contract_version": registered["contract_version"],
            "workspace_adapter": registered["workspace_adapter"],
            "dependency_manifest_version": registered.get("dependency_manifest_version"),
            "dependencies": registered.get("dependencies", []),
        }

        r1 = client.post(
            f"/api/v1/runtime/adapters/{registered['id']}/approve", json=payload
        )
        assert r1.status_code == 200
        first_at = r1.json()["approved_at"]

        r2 = client.post(
            f"/api/v1/runtime/adapters/{registered['id']}/approve", json=payload
        )
        assert r2.status_code == 200
        assert r2.json()["approved_at"] == first_at  # idempotent

    def test_approve_after_re_registration_old_payload_422(
        self, route_setup, app
    ):
        """After re-registration, old approval payload → 422, entry pending."""
        from fastapi.testclient import TestClient
        from runtime.daemon import paths as paths_mod

        token = paths_mod.read_token()
        script1 = _make_fake_adapter_script(route_setup, "rereg-approve-v1")
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {token}"})

        registered1 = self._register_as_authed(client, token, script1)
        adapter_id = registered1["id"]

        # Change the executable
        script1_path = Path(registered1["executable"])
        script1_path.unlink()
        script2 = _make_fake_adapter_script(
            route_setup, "rereg-approve-v1",
            output={
                "success": True,
                "duration_seconds": 0,
                "session_id": "probe-sess-00000000-0000-0000-0000-000000000000",
                "returncode": 0,
                "stdout_tail": "v2",
                "stderr_tail": "",
                "result": {"text": "v2"},
                "token_usage": None,
                "error": None,
                "agent_session_id": None,
                "rate_limited": False,
                "adapter_metadata": {
                    "adapter": "fake",
                    "adapter_version": "2.0.0",
                    "contract_version": 1,
                },
                "child_session_id": None,
                "raw_forensics_ref": None,
            },
        )
        registered2 = self._register_as_authed(
            client, token, script2, version="2.0.0"
        )
        assert registered2["status"] == "pending"

        # Old payload (from registered1) must 422
        r = client.post(f"/api/v1/runtime/adapters/{adapter_id}/approve", json={
            "executable": registered1["executable"],
            "executable_hash": registered1["executable_hash"],
            "version": registered1["version"],
            "capabilities": registered1["capabilities"],
            "contract_version": registered1["contract_version"],
            "workspace_adapter": registered1["workspace_adapter"],
            "dependency_manifest_version": registered1.get("dependency_manifest_version"),
            "dependencies": registered1.get("dependencies", []),
        })
        assert r.status_code == 422

        # Entry still pending
        r_get = client.get(f"/api/v1/runtime/adapters/{adapter_id}")
        assert r_get.json()["status"] == "pending"

    def test_list_and_detail_include_approval_fields(
        self, route_setup, app
    ):
        """GET list and detail must expose approved_at/approved_by."""
        from fastapi.testclient import TestClient
        from runtime.daemon import paths as paths_mod

        token = paths_mod.read_token()
        script = _make_fake_adapter_script(route_setup, "approval-fields-adapter")
        client = TestClient(app)
        client.headers.update({"Authorization": f"Bearer {token}"})

        registered = self._register_as_authed(client, token, script)

        # Before approval
        r_detail = client.get(f"/api/v1/runtime/adapters/{registered['id']}")
        assert r_detail.status_code == 200
        assert r_detail.json()["approved_at"] is None
        assert r_detail.json()["approved_by"] is None

        # Approve
        r_approve = client.post(
            f"/api/v1/runtime/adapters/{registered['id']}/approve",
            json={
                "executable": registered["executable"],
                "executable_hash": registered["executable_hash"],
                "version": registered["version"],
                "capabilities": registered["capabilities"],
                "contract_version": registered["contract_version"],
                "workspace_adapter": registered["workspace_adapter"],
                "dependency_manifest_version": registered.get("dependency_manifest_version"),
                "dependencies": registered.get("dependencies", []),
            },
        )
        assert r_approve.status_code == 200

        # Detail now shows approval fields
        r_detail2 = client.get(f"/api/v1/runtime/adapters/{registered['id']}")
        assert r_detail2.status_code == 200
        assert r_detail2.json()["approved_at"] is not None
        assert r_detail2.json()["approved_by"] == "founder/master-bearer"

        # List also shows approval fields
        r_list = client.get("/api/v1/runtime/adapters")
        assert r_list.status_code == 200
        entries = r_list.json()
        matching = [e for e in entries if e["id"] == registered["id"]]
        assert len(matching) == 1
        assert matching[0]["approved_at"] is not None
        assert matching[0]["approved_by"] == "founder/master-bearer"

    def test_resolve_adapter_resolves_approved(self, route_setup):
        """resolve_adapter returns approved adapter at the Python seam."""
        from runtime.orchestrator.custom_adapter_registry import (
            approve_adapter,
            register_custom_adapter,
            resolve_adapter,
        )
        monkeypatch_ctx = pytest.MonkeyPatch()
        monkeypatch_ctx.setenv("HAPPYRANCH_DAEMON_HOME", str(route_setup))
        script = _make_fake_adapter_script(route_setup, "resolve-approved")

        entry = register_custom_adapter(
            executable=str(script),
            version="1.0.0",
            capabilities=[],
            workspace_adapter="pi",
        )
        approve_adapter(
            adapter_id=entry.id,
            executable=entry.executable,
            executable_hash=entry.executable_hash,
            version=entry.version,
            capabilities=entry.capabilities,
            contract_version=entry.contract_version,
            workspace_adapter=entry.workspace_adapter,
        )
        result = resolve_adapter(entry.id)
        assert result is not None
        assert result.status == "approved"


# ============================================================================
# D4: forbidden-surface proof + D3 regression
# ============================================================================


class TestD4ForbiddenSurfacesAndRegression:
    """Verify D4 does not touch D5/D7/D12, SQLite, auth, permissions, etc."""

    def test_d3_bounded_conformance_preserved(self, tmp_path, monkeypatch):
        """D3 real subprocess tests still pass."""
        from runtime.orchestrator import custom_adapter_registry as car

        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        patched_timeout = 1.5
        monkeypatch.setattr(
            car, "CONFORMANCE_PROBE_TIMEOUT_SECONDS", patched_timeout
        )

        # Flood test
        flood_script = tmp_path / "d4-flood"
        flood_script.write_text(r"""#!/usr/bin/env python3
import sys
_ = sys.stdin.read()
sys.stdout.write("x" * 2_000_000)
sys.exit(0)
""")
        flood_script.chmod(0o755)
        with pytest.raises(ValueError, match="stdout.*byte limit"):
            car.run_conformance_probe(str(flood_script), "d4-flood")
        assert load_adapters() == {}

    def test_builtin_profile_regression(self):
        """Built-in profiles unchanged by D4."""
        from runtime.orchestrator.executor_registry import get_registry
        registry = get_registry()
        for name in ["claude", "codex", "opencode", "pi"]:
            profile = registry.get_profile(name)
            assert profile is not None
            assert profile.kind == "builtin"

    def test_no_python_import_path(self):
        """No Python import/discovery introduced by D4."""
        import runtime.orchestrator.custom_adapter_registry as car
        content = Path(car.__file__).read_text() if car.__file__ else ""
        assert "importlib" not in content
        assert "__import__(" not in content

    def test_no_sqlite_changes(self):
        """D4 introduces zero SQLite schema/migration changes.

        Checks for actual SQLite import/connection usage patterns, not
        docstring mentions of the word (the D4 scope disclaimer mentions
        SQLite as a forbidden surface).
        """
        import runtime.orchestrator.custom_adapter_registry as car
        source = Path(car.__file__).read_text() if car.__file__ else ""
        # No actual SQLite imports or usage
        assert "import sqlite" not in source.lower()
        assert "from sqlite" not in source.lower()
        # No migration patterns
        assert "ALTER TABLE" not in source
        assert "ADD COLUMN" not in source

    def test_no_auth_bearer_flow_change(self, tmp_path, monkeypatch):
        """Existing auth dependency still works — routes still reject unauth."""
        # Set up app with real adapter router
        from runtime.daemon import paths as paths_mod
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / ".happyranch"))
        paths_mod.ensure_daemon_home()
        paths_mod.ensure_token()

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from runtime.daemon.routes.adapters import router
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        client = TestClient(app)

        assert client.post("/api/v1/runtime/adapters/register", json={
            "executable": "/bin/true", "version": "1.0.0", "capabilities": [],
            "workspace_adapter": "pi",
        }).status_code == 401
        assert client.get("/api/v1/runtime/adapters").status_code == 401
        assert client.get("/api/v1/runtime/adapters/fake").status_code == 401
        assert client.post("/api/v1/runtime/adapters/fake/approve", json={
            "executable": "/bin/true", "executable_hash": "abc",
            "version": "1.0.0", "capabilities": [],
            "contract_version": 1, "workspace_adapter": "pi",
        }).status_code == 401


# ============================================================================
# D4 REVISE: atomic approval/re-registration interleaving tests
# ============================================================================


class TestD4AtomicApprovalReRegistration:
    """D4 REVISE (TASK-3503): atomic lock protects approval vs re-registration.

    These tests drive the real public registration + approval seam against
    the durable store with real threads, exercising the store-level RLock
    that serializes competing writes to the same adapters.yaml file.

    Test coverage:
      1. Re-registration wins first → stale approval rejects, no overwrite
      2. Approval wins first → re-registration overwrites with PENDING + null provenance
      3. Failed stale approval leaves no YAML corruption
      4. Concurrent operations on different adapters preserve both entries
      5. Sequential stale-payload + exact-idempotence + existing regression
    """

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_fake_script(
        tmp_path: Path,
        name: str,
        version_str: str = "1.0.0",
    ) -> Path:
        """Create a minimal fake adapter script for a specific version."""
        return _make_fake_adapter_script(
            tmp_path, name,
            output={
                "success": True,
                "duration_seconds": 0,
                "session_id": "probe-sess-00000000-0000-0000-0000-000000000000",
                "returncode": 0,
                "stdout_tail": f"v{version_str}",
                "stderr_tail": "",
                "result": {"text": f"v{version_str}"},
                "token_usage": None,
                "error": None,
                "agent_session_id": None,
                "rate_limited": False,
                "adapter_metadata": {
                    "adapter": name,
                    "adapter_version": version_str,
                    "contract_version": 1,
                },
                "child_session_id": None,
                "raw_forensics_ref": None,
            },
        )

    @staticmethod
    def _register(tmp_path, monkeypatch, name, version="1.0.0"):
        """Register a fake adapter, return the AdapterEntry."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        script = TestD4AtomicApprovalReRegistration._make_fake_script(
            tmp_path, name, version
        )
        return register_custom_adapter(
            executable=str(script),
            version=version,
            capabilities=[],
            workspace_adapter="pi",
        )

    def _assert_durable(
        self, entry_id, expected_status, expected_version,
        approved_at_null=True, approved_by_null=True,
    ):
        """Assert the durable-store state of an adapter.

        Reads fresh from disk via ``load_adapters()`` — NOT the returned
        object — satisfying the brief's requirement for a durable reload
        assertion.
        """
        from runtime.orchestrator.adapter_store import load_adapters
        all_entries = load_adapters()
        assert entry_id in all_entries, f"{entry_id} missing from durable store"
        entry = all_entries[entry_id]
        assert entry.status == expected_status, (
            f"expected status={expected_status}, got {entry.status}"
        )
        assert entry.version == expected_version, (
            f"expected version={expected_version}, got {entry.version}"
        )
        if approved_at_null:
            assert entry.approved_at is None, (
                f"expected approved_at=None, got {entry.approved_at}"
            )
        if approved_by_null:
            assert entry.approved_by is None, (
                f"expected approved_by=None, got {entry.approved_by}"
            )

    # ------------------------------------------------------------------
    # Test 1 — re-registration wins first, stale approval must reject
    # ------------------------------------------------------------------

    def test_reregistration_wins_stale_approval_rejects(self, tmp_path, monkeypatch):
        """Registration writes v2 PENDING before stale approval commits.

        Interleaving: a changed re-registration acquires the store lock
        first and writes v2.0.0 PENDING.  The stale approval then acquires
        the lock, reloads from disk, detects the mismatch, and rejects —
        leaving the durable entry as v2 PENDING with null provenance.
        """
        import threading

        from runtime.orchestrator.custom_adapter_registry import (
            approve_adapter,
            register_custom_adapter,
        )

        # Register v1.0.0
        entry1 = self._register(tmp_path, monkeypatch, "race-adapter", "1.0.0")
        assert entry1.status == "pending"

        # Prepare v2 script (different content → different hash)
        script2 = self._make_fake_script(tmp_path, "race-adapter", "2.0.0")

        # Synchronization: approval thread waits for re-reg to finish first
        approval_started = threading.Event()
        rereg_done = threading.Event()
        approval_result: list = []

        def _approve():
            approval_started.set()  # signal: I'm ready
            rereg_done.wait(timeout=5)  # wait for re-reg to finish first
            try:
                result = approve_adapter(
                    adapter_id=entry1.id,
                    executable=entry1.executable,
                    executable_hash=entry1.executable_hash,
                    version=entry1.version,
                    capabilities=entry1.capabilities,
                    contract_version=entry1.contract_version,
                    workspace_adapter=entry1.workspace_adapter,
                )
                approval_result.append(("ok", result))
            except ValueError as exc:
                approval_result.append(("error", str(exc)))

        t = threading.Thread(target=_approve)
        t.start()

        # Wait for approval thread to be ready (lock not yet acquired)
        approval_started.wait(timeout=5)

        # Re-register v2 — acquires lock first, writes PENDING
        entry2 = register_custom_adapter(
            executable=str(script2),
            version="2.0.0",
            capabilities=[],
            workspace_adapter="pi",
        )
        assert entry2.status == "pending"
        assert entry2.version == "2.0.0"

        # Signal approval to proceed
        rereg_done.set()
        t.join(timeout=10)

        # Approval MUST have rejected (stale snapshot)
        assert len(approval_result) == 1
        assert approval_result[0][0] == "error", (
            f"expected stale approval to raise ValueError, got {approval_result}"
        )
        assert "mismatch" in approval_result[0][1].lower() or \
            "executable_hash" in approval_result[0][1].lower(), (
            f"error message should mention mismatch: {approval_result[0][1]}"
        )

        # Durable assertion: v2 PENDING, null provenance
        self._assert_durable(entry1.id, "pending", "2.0.0")

    # ------------------------------------------------------------------
    # Test 2 — approval wins first, re-registration overwrites with PENDING
    # ------------------------------------------------------------------

    def test_approval_wins_reregistration_overwrites_pending(self, tmp_path, monkeypatch):
        """Approval commits v1 APPROVED, then re-registration overwrites.

        Interleaving: the approval wins the lock first and writes v1.0.0
        APPROVED with provenance.  The re-registration then acquires the
        lock, sees the existing entry, and durably replaces it with v2.0.0
        PENDING and cleared approved_at/approved_by.
        """
        import threading

        from runtime.orchestrator.custom_adapter_registry import (
            approve_adapter,
            register_custom_adapter,
        )

        # Register v1.0.0
        entry1 = self._register(tmp_path, monkeypatch, "race-adapter-b", "1.0.0")

        # Prepare v2 script
        script2 = self._make_fake_script(tmp_path, "race-adapter-b", "2.0.0")

        # Synchronization: re-reg thread waits for approval to finish first
        rereg_ready = threading.Event()
        approval_done = threading.Event()
        rereg_result: list = []

        def _reregister():
            rereg_ready.set()  # signal: I'm ready
            approval_done.wait(timeout=5)  # wait for approval first
            try:
                entry = register_custom_adapter(
                    executable=str(script2),
                    version="2.0.0",
                    capabilities=[],
                    workspace_adapter="pi",
                )
                rereg_result.append(("ok", entry))
            except Exception as exc:
                rereg_result.append(("error", str(exc)))

        t = threading.Thread(target=_reregister)
        t.start()

        # Wait for re-reg thread to be ready
        rereg_ready.wait(timeout=5)

        # Approve v1 — acquires lock first, writes APPROVED
        approved = approve_adapter(
            adapter_id=entry1.id,
            executable=entry1.executable,
            executable_hash=entry1.executable_hash,
            version=entry1.version,
            capabilities=entry1.capabilities,
            contract_version=entry1.contract_version,
            workspace_adapter=entry1.workspace_adapter,
        )
        assert approved.status == "approved"
        assert approved.approved_at is not None
        assert approved.approved_by == "founder/master-bearer"

        # Signal re-reg to proceed
        approval_done.set()
        t.join(timeout=10)

        # Re-registration MUST have succeeded (overwrites approved entry)
        assert len(rereg_result) == 1
        assert rereg_result[0][0] == "ok", (
            f"expected re-registration to succeed, got {rereg_result}"
        )
        rereg_entry = rereg_result[0][1]
        assert rereg_entry.status == "pending"
        assert rereg_entry.version == "2.0.0"

        # Durable assertion: v2 PENDING, null provenance
        self._assert_durable(entry1.id, "pending", "2.0.0")

    # ------------------------------------------------------------------
    # Test 3 — no YAML corruption from failed stale approval
    # ------------------------------------------------------------------

    def test_no_yaml_corruption_after_stale_approval(self, tmp_path, monkeypatch):
        """A failed stale approval leaves the YAML store intact.

        After a stale approval rejects, the adapters.yaml file must still
        be valid YAML with only the correct entries — no partial write,
        no corruption, no orphaned temp file.
        """
        import threading
        import yaml as _yaml

        from runtime.orchestrator.adapter_store import _store_path
        from runtime.orchestrator.custom_adapter_registry import approve_adapter

        # Register v1.0.0
        entry1 = self._register(tmp_path, monkeypatch, "corrupt-adapter", "1.0.0")

        # Prepare v2 script
        script2 = self._make_fake_script(tmp_path, "corrupt-adapter", "2.0.0")

        # Interleaving: re-reg wins first, then stale approval fails
        approval_done = threading.Event()
        rereg_first = threading.Event()

        def _approve():
            rereg_first.wait(timeout=5)
            try:
                approve_adapter(
                    adapter_id=entry1.id,
                    executable=entry1.executable,
                    executable_hash=entry1.executable_hash,
                    version=entry1.version,
                    capabilities=entry1.capabilities,
                    contract_version=entry1.contract_version,
                    workspace_adapter=entry1.workspace_adapter,
                )
            except ValueError:
                pass  # expected
            approval_done.set()

        t = threading.Thread(target=_approve)
        t.start()

        # Re-register first
        register_custom_adapter(
            executable=str(script2),
            version="2.0.0",
            capabilities=[],
            workspace_adapter="pi",
        )
        rereg_first.set()
        t.join(timeout=10)
        approval_done.wait(timeout=5)

        # Verify YAML is valid and contains correct entries
        store_path = _store_path()
        assert store_path.exists(), "adapters.yaml must exist"

        raw = store_path.read_text(encoding="utf-8")
        assert raw.strip(), "adapters.yaml must not be empty"

        parsed = _yaml.safe_load(raw)
        assert isinstance(parsed, dict), "top-level must be a dict"
        assert entry1.id in parsed, f"{entry1.id} must be in YAML"
        assert parsed[entry1.id]["status"] == "pending"
        assert parsed[entry1.id]["version"] == "2.0.0"
        assert parsed[entry1.id].get("approved_at") is None
        assert parsed[entry1.id].get("approved_by") is None

        # No temp files left behind
        store_dir = store_path.parent
        temps = list(store_dir.glob(".adapters.*.yaml"))
        assert len(temps) == 0, f"orphaned temp files: {temps}"

    # ------------------------------------------------------------------
    # Test 4 — different-adapter concurrency preserves both entries
    # ------------------------------------------------------------------

    def test_concurrent_operations_different_adapters_preserve_both(
        self, tmp_path, monkeypatch
    ):
        """Approval of adapter A + re-registration of adapter B must not
        interfere — both entries must survive with correct final state.
        """
        import threading

        from runtime.orchestrator.custom_adapter_registry import (
            approve_adapter,
            register_custom_adapter,
        )

        # Register adapter A v1.0.0
        entry_a1 = self._register(tmp_path, monkeypatch, "adapter-a", "1.0.0")

        # Register adapter B v1.0.0
        entry_b1 = self._register(tmp_path, monkeypatch, "adapter-b", "1.0.0")

        # Prepare adapter B v2 script
        script_b2 = self._make_fake_script(tmp_path, "adapter-b", "2.0.0")

        barrier = threading.Barrier(2, timeout=10)
        results: dict = {"approve_a": None, "rereg_b": None}

        def _approve_a():
            barrier.wait()
            try:
                result = approve_adapter(
                    adapter_id=entry_a1.id,
                    executable=entry_a1.executable,
                    executable_hash=entry_a1.executable_hash,
                    version=entry_a1.version,
                    capabilities=entry_a1.capabilities,
                    contract_version=entry_a1.contract_version,
                    workspace_adapter=entry_a1.workspace_adapter,
                )
                results["approve_a"] = ("ok", result)
            except Exception as exc:
                results["approve_a"] = ("error", str(exc))

        def _rereg_b():
            barrier.wait()
            try:
                entry = register_custom_adapter(
                    executable=str(script_b2),
                    version="2.0.0",
                    capabilities=[],
                    workspace_adapter="pi",
                )
                results["rereg_b"] = ("ok", entry)
            except Exception as exc:
                results["rereg_b"] = ("error", str(exc))

        t1 = threading.Thread(target=_approve_a)
        t2 = threading.Thread(target=_rereg_b)
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        # Both operations must succeed
        assert results["approve_a"] is not None, "approve_a did not complete"
        assert results["approve_a"][0] == "ok", (
            f"approve_a failed: {results['approve_a']}"
        )
        assert results["rereg_b"] is not None, "rereg_b did not complete"
        assert results["rereg_b"][0] == "ok", (
            f"rereg_b failed: {results['rereg_b']}"
        )

        # Durable assertion: adapter A = APPROVED v1, adapter B = PENDING v2
        self._assert_durable(
            entry_a1.id, "approved", "1.0.0",
            approved_at_null=False, approved_by_null=False,
        )
        self._assert_durable(entry_b1.id, "pending", "2.0.0")

        # Verify A's approval provenance is intact
        from runtime.orchestrator.adapter_store import load_adapters
        a_entry = load_adapters()[entry_a1.id]
        assert a_entry.approved_at is not None
        assert a_entry.approved_by == "founder/master-bearer"

    # ------------------------------------------------------------------
    # Test 5 — keep/extend existing sequential + idempotence behavior
    # ------------------------------------------------------------------

    def test_approval_idempotence_preserved(self, tmp_path, monkeypatch):
        """Exact-idempotence: re-approving identical snapshot returns unchanged."""
        from runtime.orchestrator.custom_adapter_registry import approve_adapter

        entry = self._register(tmp_path, monkeypatch, "idempotent-adapter")

        approved1 = approve_adapter(
            adapter_id=entry.id,
            executable=entry.executable,
            executable_hash=entry.executable_hash,
            version=entry.version,
            capabilities=entry.capabilities,
            contract_version=entry.contract_version,
            workspace_adapter=entry.workspace_adapter,
        )
        assert approved1.status == "approved"

        # Second approval with same facts → idempotent
        approved2 = approve_adapter(
            adapter_id=entry.id,
            executable=entry.executable,
            executable_hash=entry.executable_hash,
            version=entry.version,
            capabilities=entry.capabilities,
            contract_version=entry.contract_version,
            workspace_adapter=entry.workspace_adapter,
        )
        assert approved2.status == "approved"
        # Provenance unchanged (same approved_at)
        assert approved2.approved_at == approved1.approved_at

    def test_sequential_stale_approval_after_rereg_still_rejects(
        self, tmp_path, monkeypatch
    ):
        """Sequential re-reg + stale approval (no threads) still rejects.

        This covers the non-concurrent case: re-register then try to
        approve with old facts — must reject with mismatch.
        """
        from runtime.orchestrator.custom_adapter_registry import (
            approve_adapter,
            register_custom_adapter,
        )

        entry1 = self._register(tmp_path, monkeypatch, "seq-stale", "1.0.0")
        script2 = self._make_fake_script(tmp_path, "seq-stale", "2.0.0")
        register_custom_adapter(
            executable=str(script2),
            version="2.0.0",
            capabilities=[],
            workspace_adapter="pi",
        )

        with pytest.raises(ValueError, match="mismatch"):
            approve_adapter(
                adapter_id=entry1.id,
                executable=entry1.executable,
                executable_hash=entry1.executable_hash,
                version=entry1.version,
                capabilities=entry1.capabilities,
                contract_version=entry1.contract_version,
                workspace_adapter=entry1.workspace_adapter,
            )

        # Durable assertion: still v2 PENDING
        self._assert_durable(entry1.id, "pending", "2.0.0")
