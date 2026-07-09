"""Unit tests for executor_binary_registry (machine-local binary path store)
and _resolve_binary stored-path-first resolution (THR-085).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest

from runtime.orchestrator.executors import (
    ExecutorBinaryBlocked,
    _resolve_binary,
)


# ─────────────────────────────────────────────────────────────────
# executor_binary_registry tests
# ─────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_home_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".happyranch"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(home))
    return home


def test_load_registry_empty_when_no_file(tmp_home_path: Path):
    """When no registry file exists, load_registry returns {}."""
    from runtime.orchestrator.executor_binary_registry import load_registry
    assert load_registry() == {}


def test_load_registry_reads_file(tmp_home_path: Path):
    """load_registry reads a populated file."""
    from runtime.orchestrator.executor_binary_registry import load_registry
    data = {"claude": "/opt/homebrew/bin/claude", "codex": "/usr/local/bin/codex"}
    (tmp_home_path / "executors.json").write_text(json.dumps(data))
    result = load_registry()
    assert result == data


def test_load_registry_lowercases_keys(tmp_home_path: Path):
    """load_registry lowercases keys for case-insensitive lookup."""
    from runtime.orchestrator.executor_binary_registry import load_registry
    (tmp_home_path / "executors.json").write_text(
        json.dumps({"Claude": "/opt/homebrew/bin/claude"})
    )
    result = load_registry()
    assert result == {"claude": "/opt/homebrew/bin/claude"}


def test_load_registry_skips_non_string_values(tmp_home_path: Path):
    """load_registry skips entries with non-string values."""
    from runtime.orchestrator.executor_binary_registry import load_registry
    (tmp_home_path / "executors.json").write_text(
        json.dumps({"claude": 123, "codex": "/opt/homebrew/bin/codex"})
    )
    result = load_registry()
    assert result == {"codex": "/opt/homebrew/bin/codex"}


def test_save_registry_atomic_write(tmp_home_path: Path):
    """save_registry writes to a tmp file then renames — no partial writes."""
    from runtime.orchestrator.executor_binary_registry import (
        load_registry,
        save_registry,
    )
    save_registry({"claude": "/opt/homebrew/bin/claude"})
    result = load_registry()
    assert result == {"claude": "/opt/homebrew/bin/claude"}
    # No .tmp file left behind
    assert not (tmp_home_path / "executors.json.tmp").exists()


def test_save_registry_preserves_existing(tmp_home_path: Path):
    """save_registry adds/updates keys without dropping existing ones."""
    from runtime.orchestrator.executor_binary_registry import (
        load_registry,
        save_registry,
    )
    save_registry({"claude": "/a/claude"})
    save_registry({"codex": "/b/codex"})
    result = load_registry()
    assert result == {"claude": "/a/claude", "codex": "/b/codex"}


def test_save_registry_overwrites_existing_key(tmp_home_path: Path):
    """save_registry updates an existing key's value."""
    from runtime.orchestrator.executor_binary_registry import (
        load_registry,
        save_registry,
    )
    save_registry({"claude": "/old/claude"})
    save_registry({"claude": "/new/claude"})
    result = load_registry()
    assert result == {"claude": "/new/claude"}


def test_get_binary_returns_stored_path(tmp_home_path: Path):
    """get_binary returns the stored path for a registered kind."""
    from runtime.orchestrator.executor_binary_registry import get_binary, set_binary
    set_binary("claude", "/my/claude")
    assert get_binary("claude") == "/my/claude"


def test_get_binary_returns_none_for_unregistered(tmp_home_path: Path):
    """get_binary returns None for an unregistered kind."""
    from runtime.orchestrator.executor_binary_registry import get_binary
    assert get_binary("nonexistent") is None


def test_get_binary_case_insensitive(tmp_home_path: Path):
    """get_binary is case-insensitive on kind names."""
    from runtime.orchestrator.executor_binary_registry import get_binary, set_binary
    set_binary("CLAUDE", "/my/claude")
    assert get_binary("claude") == "/my/claude"


def test_remove_binary(tmp_home_path: Path):
    """remove_binary deletes a key from the registry."""
    from runtime.orchestrator.executor_binary_registry import (
        get_binary,
        remove_binary,
        set_binary,
    )
    set_binary("claude", "/my/claude")
    assert get_binary("claude") == "/my/claude"
    remove_binary("claude")
    assert get_binary("claude") is None


def test_remove_binary_noop_when_missing(tmp_home_path: Path):
    """remove_binary is a no-op when the kind is not registered."""
    from runtime.orchestrator.executor_binary_registry import remove_binary
    remove_binary("nonexistent")  # Should not raise


def test_validate_binary_absolute_path(tmp_path: Path):
    """validate_binary returns the resolved path for a valid executable."""
    from runtime.orchestrator.executor_binary_registry import validate_binary
    exe = tmp_path / "bin" / "myexecutor"
    exe.parent.mkdir()
    exe.touch(mode=0o755)
    result = validate_binary(str(exe))
    assert result == str(exe.resolve())


def test_validate_binary_rejects_relative_path():
    """validate_binary rejects relative paths."""
    from runtime.orchestrator.executor_binary_registry import validate_binary
    with pytest.raises(ValueError, match="absolute"):
        validate_binary("relative/path")


def test_validate_binary_rejects_nonexistent_file():
    """validate_binary rejects non-existent files."""
    from runtime.orchestrator.executor_binary_registry import validate_binary
    with pytest.raises(ValueError, match="does not exist"):
        validate_binary("/nonexistent/path/to/binary")


def test_validate_binary_rejects_non_executable(tmp_path: Path):
    """validate_binary rejects files that are not executable."""
    from runtime.orchestrator.executor_binary_registry import validate_binary
    f = tmp_path / "not_executable"
    f.touch(mode=0o644)
    with pytest.raises(ValueError, match="not executable"):
        validate_binary(str(f))


def test_is_binary_valid(tmp_path: Path):
    """is_binary_valid returns True for valid, False for invalid."""
    from runtime.orchestrator.executor_binary_registry import is_binary_valid
    exe = tmp_path / "valid_bin"
    exe.touch(mode=0o755)
    assert is_binary_valid(str(exe)) is True
    assert is_binary_valid("/nonexistent") is False


# ─────────────────────────────────────────────────────────────────
# _resolve_binary stored-path-first resolution tests
# ─────────────────────────────────────────────────────────────────


def test_resolve_registered_valid_uses_stored_path(tmp_path, monkeypatch):
    """When a kind is registered AND the stored path is valid, use it."""
    from runtime.orchestrator.executor_binary_registry import set_binary
    fake_bin = tmp_path / "registered" / "claude"
    fake_bin.parent.mkdir(parents=True)
    fake_bin.touch(mode=0o755)
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / ".happyranch"))
    set_binary("claude", str(fake_bin))

    result = _resolve_binary("claude")
    assert result == str(fake_bin)


def test_resolve_registered_invalid_raises_actionable_block(tmp_path, monkeypatch):
    """When a kind is registered but the stored path is stale, raise
    ExecutorBinaryBlocked — NO silent PATH fallback."""
    from runtime.orchestrator.executor_binary_registry import set_binary
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / ".happyranch"))
    set_binary("claude", "/nonexistent/path/to/claude")

    with pytest.raises(ExecutorBinaryBlocked) as exc_info:
        _resolve_binary("claude")
    msg = str(exc_info.value)
    assert "claude" in msg
    assert "/nonexistent/path/to/claude" in msg
    assert "not exist" in msg.lower() or "not executable" in msg.lower()
    assert "happyranch" in msg.lower()


def test_resolve_unregistered_on_path_resolves_non_silent(tmp_path, monkeypatch):
    """When a kind is unregistered but on PATH, resolve it WITH a log warning
    (non-silent fallback, invariant 3)."""
    fake_bin = tmp_path / "onthepath" / "claude"
    fake_bin.parent.mkdir(parents=True)
    fake_bin.touch(mode=0o755)
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / ".happyranch"))
    monkeypatch.setenv("PATH", f"{fake_bin.parent}:/usr/bin:/bin")

    # Capture the warning log
    from runtime.orchestrator import executors as ex_mod
    with _capture_log(ex_mod.logger, logging.WARNING) as log_entries:
        result = _resolve_binary("claude")
    assert result == str(fake_bin)
    assert len(log_entries) >= 1
    assert "no stored binary path" in log_entries[0]


def test_resolve_unregistered_not_on_path_raises_actionable_block(
    tmp_path, monkeypatch,
):
    """When a kind is unregistered AND not on PATH, raise ExecutorBinaryBlocked
    with an actionable message."""
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / ".happyranch"))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    with pytest.raises(ExecutorBinaryBlocked) as exc_info:
        _resolve_binary("pi")
    msg = str(exc_info.value)
    assert "pi" in msg
    assert "not registered" in msg.lower()
    assert "happyranch" in msg.lower()


def test_resolve_absolute_path_still_trusted(monkeypatch):
    """Absolute cli_path is still returned unchanged (existing behavior preserved)."""
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", "/nonexistent/home")
    result = _resolve_binary("/custom/path/to/my-executor")
    assert result == "/custom/path/to/my-executor"


def test_executor_binary_blocked_is_runtime_error():
    """ExecutorBinaryBlocked is a RuntimeError subclass for backward compat."""
    assert issubclass(ExecutorBinaryBlocked, RuntimeError)


# ─────────────────────────────────────────────────────────────────
# Resolution precedence tests (TDD for all 4 scenarios)
# ─────────────────────────────────────────────────────────────────


def test_registered_valid_vs_path_uses_registry(tmp_path, monkeypatch):
    """Scenario 1: registered path wins over PATH binary."""
    from runtime.orchestrator.executor_binary_registry import set_binary

    # Place a PATH binary
    path_bin = tmp_path / "path_bin" / "claude"
    path_bin.parent.mkdir(parents=True)
    path_bin.touch(mode=0o755)

    # Place a registered binary at a different location
    reg_bin = tmp_path / "reg_bin" / "claude"
    reg_bin.parent.mkdir(parents=True)
    reg_bin.touch(mode=0o755)

    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / ".happyranch"))
    monkeypatch.setenv("PATH", str(path_bin.parent))
    set_binary("claude", str(reg_bin))

    result = _resolve_binary("claude")
    # Must use registered path, NOT the PATH binary
    assert result == str(reg_bin)
    assert result != str(path_bin)


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────


class _capture_log:
    """Context manager to capture log messages at a given level from a logger."""

    def __init__(self, logger, level):
        self._logger = logger
        self._level = level
        self._handler: logging.Handler | None = None
        self._records: list[str] = []

    def __enter__(self):
        class _ListHandler(logging.Handler):
            def __init__(self, records):
                super().__init__()
                self.records = records

            def emit(self, record):
                self.records.append(record.getMessage())

        self._handler = _ListHandler(self._records)
        self._handler.setLevel(self._level)
        self._logger.addHandler(self._handler)
        return self._records

    def __exit__(self, *args):
        if self._handler:
            self._logger.removeHandler(self._handler)
