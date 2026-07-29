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
    """validate_binary returns the supplied path (not resolved target) for THR-107.

    The operator-supplied spelling is preserved — especially important for
    stable Homebrew symlinks like /opt/homebrew/bin/claude.
    """
    from runtime.orchestrator.executor_binary_registry import validate_binary
    exe = tmp_path / "bin" / "myexecutor"
    exe.parent.mkdir()
    exe.touch(mode=0o755)
    result = validate_binary(str(exe))
    # THR-107: preserve the supplied path, not resolve()'s target
    assert result == str(exe)
    # Safety: validate_binary still confirms the path is resolvable
    assert os.access(str(exe), os.X_OK)


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


def test_validate_binary_preserves_symlink_path(tmp_path: Path):
    """validate_binary returns the supplied symlink path, not the resolved target.

    THR-107: Homebrew stable symlinks (/opt/homebrew/bin/claude →
    ../Cellar/.../bin/claude) must be stored as the operator-supplied
    symlink spelling so they survive version bumps in the Cellar target.
    """
    from runtime.orchestrator.executor_binary_registry import validate_binary
    # Create a real executable target
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    target = target_dir / "real-claude"
    target.touch(mode=0o755)

    # Create a symlink pointing to the target (like Homebrew does)
    symlink = tmp_path / "bin" / "claude"
    symlink.parent.mkdir()
    os.symlink(str(target), str(symlink))

    result = validate_binary(str(symlink))
    # THR-107: must preserve the symlink path, not resolve to target
    assert result == str(symlink)
    assert result != str(target.resolve())
    # Safety: validation still succeeds (path exists, is executable through symlink)
    assert os.access(str(symlink), os.X_OK)


def test_validate_binary_stale_symlink_rejected(tmp_path: Path):
    """When a symlink's target disappears, validate_binary still rejects.

    THR-107 staleness invariant: is_binary_valid must detect stale symlinks
    so _resolve_binary continues to raise ExecutorBinaryBlocked rather than
    silently falling back to PATH.
    """
    from runtime.orchestrator.executor_binary_registry import (
        is_binary_valid,
        validate_binary,
    )
    # Create target, make symlink, then delete target to simulate staleness
    target_dir = tmp_path / "cellar" / "claude" / "1.0" / "bin"
    target_dir.mkdir(parents=True)
    target = target_dir / "claude"
    target.touch(mode=0o755)

    symlink = tmp_path / "bin" / "claude"
    symlink.parent.mkdir()
    os.symlink(str(target), str(symlink))

    # Symlink is valid while target exists
    assert is_binary_valid(str(symlink)) is True

    # Delete the target to make symlink stale
    target.unlink()

    # Stale symlink must be rejected
    assert is_binary_valid(str(symlink)) is False
    with pytest.raises(ValueError, match="does not exist"):
        validate_binary(str(symlink))


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


def test_resolve_registered_symlink_stored_uses_symlink_path(tmp_path, monkeypatch):
    """When a kind is registered with a symlink path, _resolve_binary returns
    the symlink path as stored (THR-107 preserves operator-supplied spelling).
    """
    from runtime.orchestrator.executor_binary_registry import set_binary
    # Create target + symlink
    target_dir = tmp_path / "cellar" / "claude" / "1.0"
    target_dir.mkdir(parents=True)
    target = target_dir / "claude"
    target.touch(mode=0o755)
    symlink = tmp_path / "bin" / "claude"
    symlink.parent.mkdir()
    os.symlink(str(target), str(symlink))

    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / ".happyranch"))
    set_binary("claude", str(symlink))

    result = _resolve_binary("claude")
    # Must return the stored symlink path, not the resolved target
    assert result == str(symlink)
    assert result != str(target.resolve())


def test_resolve_registered_symlink_stale_triggers_block(tmp_path, monkeypatch):
    """When a stored symlink's target disappears, _resolve_binary raises
    ExecutorBinaryBlocked — NO silent PATH fallback (THR-107 staleness invariant).
    """
    from runtime.orchestrator.executor_binary_registry import set_binary
    # Create target + symlink + register, then delete target
    target_dir = tmp_path / "cellar" / "claude" / "1.0"
    target_dir.mkdir(parents=True)
    target = target_dir / "claude"
    target.touch(mode=0o755)
    symlink = tmp_path / "bin" / "claude"
    symlink.parent.mkdir()
    os.symlink(str(target), str(symlink))

    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / ".happyranch"))
    set_binary("claude", str(symlink))

    # Make symlink stale
    target.unlink()

    with pytest.raises(ExecutorBinaryBlocked) as exc_info:
        _resolve_binary("claude")
    msg = str(exc_info.value)
    assert "claude" in msg
    assert "not exist" in msg.lower() or "not executable" in msg.lower()


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


def test_resolve_unregistered_on_path_raises_blocked(tmp_path, monkeypatch):
    """When a kind is unregistered but on PATH, raise ExecutorBinaryBlocked
    — NO PATH fallback (THR-107 seq155)."""
    fake_bin = tmp_path / "onthepath" / "claude"
    fake_bin.parent.mkdir(parents=True)
    fake_bin.touch(mode=0o755)
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / ".happyranch"))
    monkeypatch.setenv("PATH", f"{fake_bin.parent}:/usr/bin:/bin")

    with pytest.raises(ExecutorBinaryBlocked) as exc_info:
        _resolve_binary("claude")
    msg = str(exc_info.value)
    assert "claude" in msg
    assert "not registered" in msg.lower()
    assert "register" in msg.lower()


def test_resolve_unregistered_not_on_path_raises_actionable_block(
    tmp_path, monkeypatch,
):
    """When a kind is unregistered AND not on PATH, raise ExecutorBinaryBlocked
    with an actionable message."""
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / ".happyranch"))

    with pytest.raises(ExecutorBinaryBlocked) as exc_info:
        _resolve_binary("pi")
    msg = str(exc_info.value)
    assert "pi" in msg
    assert "not registered" in msg.lower()
    assert "happyranch" in msg.lower()


def test_resolve_absolute_path_requires_registration():
    """Absolute filesystem paths are NOT resolved — only registered names
    work (THR-107 seq155 hard no-PATH cutover)."""
    with pytest.raises(ExecutorBinaryBlocked) as exc_info:
        _resolve_binary("/custom/path/to/my-executor")
    assert "not registered" in str(exc_info.value).lower()


def test_executor_binary_blocked_is_runtime_error():
    """ExecutorBinaryBlocked is a RuntimeError subclass for backward compat."""
    assert issubclass(ExecutorBinaryBlocked, RuntimeError)


# ─────────────────────────────────────────────────────────────────
# Resolution precedence tests (TDD for all 4 scenarios)
# ─────────────────────────────────────────────────────────────────


def test_registered_valid_vs_path_uses_registry(tmp_path, monkeypatch):
    """Scenario: registered binary path is the sole resolution source.
    An unregistered binary on PATH is never discovered (THR-107 seq155)."""
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
    monkeypatch.setenv("PATH", f"{path_bin.parent}:/usr/bin:/bin")
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
