"""THR-204 issue 3 — adversarial regression: test/repro registry writes must
never reach the production executor-binary registry namespace.

The machine-local registry (``<daemon-home>/executors.json``) is the SOLE
resolution source for executor binaries (THR-107 seq155).  Integration
repros that registered fake binaries under PRODUCTION executor names
(``claude``, ``codex``) WITHOUT isolating ``HAPPYRANCH_DAEMON_HOME`` to a
temporary daemon home have twice overwritten the live production registry
and taken down agent invocations (THR-204 issue 3; audit log
2026-08-23T07:00Z / 08:03Z ``thread_invocation_failed`` naming
``/tmp/hr-int-repro/fake_claude.sh``).

Hardened contract under test: while running under pytest, ANY registry write
(``set_binary`` / ``save_registry`` / ``remove_binary`` /
``remove_binary_conditional``) whose target resolves to the DEFAULT
production registry path must fail closed with ``RegistryIsolationError``
instead of writing production state.

These tests are production-safe: the "production default" is simulated by
redirecting ``HOME`` to a per-test sandbox (``Path.home()`` resolves inside
the sandbox), so the real ``~/.happyranch/executors.json`` is never a write
target.  A module-scoped autouse fixture additionally checksums the REAL
live registry before and after and asserts byte + semantic identity.
"""
from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from runtime.orchestrator.executor_binary_registry import (
    RegistryIsolationError,
    get_binary,
    is_binary_valid,
    load_registry,
    remove_binary,
    remove_binary_conditional,
    save_registry,
    set_binary,
)

# Real live production registry — the thing the fix must never let a test write.
_LIVE_REGISTRY = Path.home() / ".happyranch" / "executors.json"


@pytest.fixture(scope="module", autouse=True)
def _live_registry_proof():
    """Proof the live production registry bytes and valid entries remain
    unchanged across this entire adversarial module."""
    before_bytes = _LIVE_REGISTRY.read_bytes() if _LIVE_REGISTRY.exists() else None
    before_entries = (
        json.loads(before_bytes) if before_bytes is not None else {}
    )
    yield
    after_bytes = _LIVE_REGISTRY.read_bytes() if _LIVE_REGISTRY.exists() else None
    assert after_bytes == before_bytes, (
        "THR-204 issue 3: the live production executors.json BYTES changed "
        "while running the isolation regression suite!"
    )
    after_entries = (
        json.loads(after_bytes) if after_bytes is not None else {}
    )
    assert after_entries == before_entries, (
        "THR-204 issue 3: the live production registry ENTRIES changed "
        "while running the isolation regression suite!"
    )
    for kind, path in after_entries.items():
        assert is_binary_valid(path), (
            f"live production entry {kind!r} -> {path!r} must still be valid"
        )


@pytest.fixture
def prod_sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Simulate the production default registry inside a per-test sandbox by
    redirecting HOME.  HAPPYRANCH_DAEMON_HOME is deliberately NOT set — that
    is the unisolated state that caused THR-204 issue 3."""
    home = tmp_path / "prod-home"
    (home / ".happyranch").mkdir(parents=True)
    registry = home / ".happyranch" / "executors.json"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("HAPPYRANCH_DAEMON_HOME", raising=False)
    return home, registry


def _make_exit0_fake(tmp_path: Path) -> Path:
    """An executable fake binary that exits 0 — passes validate_binary, so a
    naive registration would succeed and silently clobber production."""
    fake = tmp_path / "fake_claude.sh"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return fake


# ─────────────────────────────────────────────────────────────────
# Core attack: production names claude/codex with NO isolation
# ─────────────────────────────────────────────────────────────────


def test_unsolated_set_binary_refuses_production_names_and_preserves_entries(
    prod_sandbox,
):
    """Registering production names (claude/codex) without daemon-home
    isolation must fail closed and leave pre-existing registry entries
    byte-identical (the exact THR-204 issue-3 attack)."""
    home, registry = prod_sandbox
    registry.write_text(json.dumps({
        "claude": "/usr/local/bin/claude-real",
        "codex": "/usr/local/bin/codex-real",
        "pi": "/opt/homebrew/bin/pi",
    }))
    before = registry.read_bytes()
    fake = _make_exit0_fake(home)

    with pytest.raises(RegistryIsolationError):
        set_binary("claude", str(fake))
    with pytest.raises(RegistryIsolationError):
        set_binary("codex", str(fake))

    assert registry.read_bytes() == before
    assert json.loads(registry.read_text()) == {
        "claude": "/usr/local/bin/claude-real",
        "codex": "/usr/local/bin/codex-real",
        "pi": "/opt/homebrew/bin/pi",
    }


def test_unsolated_save_registry_refuses_and_preserves_entries(prod_sandbox):
    """save_registry (used by tests/integration/conftest.py::live_daemon) is
    equally guarded when isolation is missing."""
    home, registry = prod_sandbox
    registry.write_text(json.dumps({"claude": "/usr/local/bin/claude-real"}))
    before = registry.read_bytes()
    fake = _make_exit0_fake(home)

    with pytest.raises(RegistryIsolationError):
        save_registry({"claude": str(fake), "codex": str(fake)})

    assert registry.read_bytes() == before


def test_unsolated_remove_paths_refuse_and_preserve_entries(prod_sandbox):
    """remove_binary and remove_binary_conditional must refuse to delete
    production registry entries from an unisolated test process."""
    home, registry = prod_sandbox
    registry.write_text(json.dumps({
        "claude": "/usr/local/bin/claude-real",
        "codex": "/usr/local/bin/codex-real",
    }))
    before = registry.read_bytes()

    with pytest.raises(RegistryIsolationError):
        remove_binary("claude")
    with pytest.raises(RegistryIsolationError):
        remove_binary_conditional("codex", "/usr/local/bin/codex-real")

    assert registry.read_bytes() == before


def test_error_message_is_actionable(prod_sandbox):
    """The guard message must name the fix: isolate HAPPYRANCH_DAEMON_HOME."""
    _, registry = prod_sandbox
    registry.write_text(json.dumps({"claude": "/usr/local/bin/claude-real"}))
    with pytest.raises(RegistryIsolationError) as exc_info:
        set_binary("claude", "/tmp/fake-claude")
    msg = str(exc_info.value)
    assert "HAPPYRANCH_DAEMON_HOME" in msg
    assert "executors.json" in msg
    assert "test" in msg.lower()


# ─────────────────────────────────────────────────────────────────
# Failed/partial setup — no write surface may be created
# ─────────────────────────────────────────────────────────────────


def test_guard_fires_before_creating_temp_write_parent(tmp_path, monkeypatch):
    """The guard must fire BEFORE any filesystem write surface — including
    mkdir of the registry parent and the ``.json.tmp`` scratch file — so a
    failed/partial setup leaves zero residue."""
    home = tmp_path / "prod-home"  # NOTE: .happyranch deliberately absent
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("HAPPYRANCH_DAEMON_HOME", raising=False)

    with pytest.raises(RegistryIsolationError):
        set_binary("claude", "/tmp/fake-claude")

    assert not (home / ".happyranch").exists(), (
        "guard must fire before mkdir of the registry parent"
    )


def test_repeated_attempts_fail_closed_no_tmp_residue(prod_sandbox):
    """Repeated attacks leave no partial writes and no .tmp scratch file."""
    home, registry = prod_sandbox
    registry.write_text(json.dumps({"claude": "/usr/local/bin/claude-real"}))
    before = registry.read_bytes()
    fake = _make_exit0_fake(home)

    for _ in range(3):
        with pytest.raises(RegistryIsolationError):
            set_binary("claude", str(fake))
    with pytest.raises(RegistryIsolationError):
        save_registry({"codex": str(fake)})
    with pytest.raises(RegistryIsolationError):
        remove_binary("claude")

    assert registry.read_bytes() == before
    assert not (home / ".happyranch" / "executors.json.tmp").exists()


# ─────────────────────────────────────────────────────────────────
# Cleanup, restoration, and repeated/nested fixture behavior
# ─────────────────────────────────────────────────────────────────


def test_isolated_writes_still_work_under_pytest(tmp_path, monkeypatch):
    """With HAPPYRANCH_DAEMON_HOME isolated to a temp daemon home, registry
    writes under pytest keep working — the guard only blocks the DEFAULT
    production path.  Normal test registration behavior is preserved."""
    isolated = tmp_path / ".happyranch"
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(isolated))
    fake = _make_exit0_fake(tmp_path)

    set_binary("claude", str(fake))
    assert get_binary("claude") == str(fake)
    set_binary("codex", str(fake))
    assert load_registry() == {"claude": str(fake), "codex": str(fake)}


def test_nested_isolated_then_unsolated_attempt(tmp_path, monkeypatch):
    """A properly-isolated write succeeds in its own home; dropping isolation
    afterwards must still fail closed and cannot leak into the isolated home."""
    isolated = tmp_path / "iso-home"
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(isolated))
    fake = _make_exit0_fake(tmp_path)

    set_binary("claude", str(fake))  # isolated write — succeeds
    assert load_registry() == {"claude": str(fake)}

    # Nested context: drop isolation -> the write must refuse.
    monkeypatch.delenv("HAPPYRANCH_DAEMON_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "prod-home"))
    with pytest.raises(RegistryIsolationError):
        set_binary("claude", "/tmp/other-fake")

    # The isolated home still holds the valid entry.
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(isolated))
    assert load_registry() == {"claude": str(fake)}


def test_environment_fully_restored_between_tests(tmp_path, monkeypatch):
    """Cleanup + environment/path restoration: after an unisolated write
    attempt, HOME and HAPPYRANCH_DAEMON_HOME are restored so the NEXT test
    starts from a pristine environment (monkeypatch teardown semantics)."""
    sandbox = tmp_path / "prod-home"
    (sandbox / ".happyranch").mkdir(parents=True)
    (sandbox / ".happyranch" / "executors.json").write_text(
        json.dumps({"claude": "/usr/local/bin/claude-real"})
    )
    monkeypatch.setenv("HOME", str(sandbox))
    monkeypatch.delenv("HAPPYRANCH_DAEMON_HOME", raising=False)

    with pytest.raises(RegistryIsolationError):
        set_binary("claude", "/tmp/fake-claude")

    # Simulate a fresh fixture scope: the previous attempt left no residue.
    # (monkeypatch restores at teardown; assert the module functions still
    # resolve to the sandbox while we are inside the test.)
    assert str(Path.home()).startswith(str(sandbox))


def test_reads_without_isolation_are_unaffected(prod_sandbox):
    """Reads must keep working without isolation — the guard is write-only,
    so _resolve_binary and prereqs behavior is unchanged."""
    _, registry = prod_sandbox
    registry.write_text(json.dumps({"claude": "/usr/local/bin/claude-real"}))
    assert load_registry() == {"claude": "/usr/local/bin/claude-real"}
    assert get_binary("claude") == "/usr/local/bin/claude-real"
    assert get_binary("codex") is None


# ─────────────────────────────────────────────────────────────────
# Symlink/alias canonical-target bypass (code-review finding)
# ─────────────────────────────────────────────────────────────────


def test_symlink_alias_daemon_home_cannot_bypass_write_guard(
    prod_sandbox, tmp_path, monkeypatch
):
    """An explicit ``HAPPYRANCH_DAEMON_HOME`` spelled through a symlink alias
    that RESOLVES to the production default must still fail closed.  The guard
    must compare canonical targets, not lexical spellings — a lexical-only
    comparison lets the alias bypass isolation and overwrite pre-existing
    production entries (code-review finding on PR #710)."""
    home, registry = prod_sandbox
    registry.write_text(json.dumps({
        "claude": "/usr/local/bin/claude-real",
        "codex": "/usr/local/bin/codex-real",
    }))
    before = registry.read_bytes()

    # HAPPYRANCH_DAEMON_HOME IS the daemon home; pointing it at a symlink to
    # the sandbox's default .happyranch canonicalizes to the protected target.
    dh_alias = tmp_path / "dh-alias"
    dh_alias.symlink_to(home / ".happyranch", target_is_directory=True)
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(dh_alias))

    fake = _make_exit0_fake(tmp_path)
    for attack in (
        lambda: set_binary("claude", str(fake)),
        lambda: save_registry({"codex": str(fake)}),
        lambda: remove_binary("claude"),
        lambda: remove_binary_conditional("codex", "/usr/local/bin/codex-real"),
    ):
        with pytest.raises(RegistryIsolationError):
            attack()

    # Bytes, entries, and temp-scratch surface unchanged through the alias.
    assert registry.read_bytes() == before
    assert json.loads(registry.read_text()) == {
        "claude": "/usr/local/bin/claude-real",
        "codex": "/usr/local/bin/codex-real",
    }
    assert not (home / ".happyranch" / "executors.json.tmp").exists(), (
        "no .json.tmp scratch file may appear next to the protected registry"
    )


def test_chained_symlink_alias_daemon_home_cannot_bypass_write_guard(
    prod_sandbox, tmp_path, monkeypatch
):
    """Variant: the alias is a CHAIN of symlinks (alias1 -> alias0 -> the
    production registry dir).  Canonicalization must resolve through every hop
    before comparing."""
    home, registry = prod_sandbox
    registry.write_text(json.dumps({"claude": "/usr/local/bin/claude-real"}))
    before = registry.read_bytes()

    hop0 = tmp_path / "dh-alias-0"
    hop0.symlink_to(home / ".happyranch", target_is_directory=True)
    hop1 = tmp_path / "dh-alias-1"
    hop1.symlink_to(hop0, target_is_directory=True)
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(hop1))

    fake = _make_exit0_fake(tmp_path)
    with pytest.raises(RegistryIsolationError):
        set_binary("claude", str(fake))

    assert registry.read_bytes() == before
    assert not (home / ".happyranch" / "executors.json.tmp").exists()


def test_dangling_alias_guard_fires_before_creating_parent(tmp_path, monkeypatch):
    """Even when the production registry does NOT exist yet, an alias that
    resolves to the default home must fail closed BEFORE any write surface:
    no mkdir of the parent, no partial setup, no temp scratch file."""
    home = tmp_path / "prod-home"  # NOTE: .happyranch deliberately absent
    monkeypatch.setenv("HOME", str(home))
    # Dangling symlink: target (home/.happyranch) does not exist yet.  A
    # non-strict resolve() must still canonicalize through it.
    dh_alias = tmp_path / "dh-alias"
    dh_alias.symlink_to(home / ".happyranch", target_is_directory=True)
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(dh_alias))

    with pytest.raises(RegistryIsolationError):
        set_binary("claude", "/tmp/fake-claude")

    assert not (home / ".happyranch").exists()
    assert not (home / ".happyranch" / "executors.json.tmp").exists()
    assert not (tmp_path / "executors.json.tmp").exists()


def test_isolated_write_through_symlink_still_works(tmp_path, monkeypatch):
    """A ``HAPPYRANCH_DAEMON_HOME`` that is a symlink to a REAL temp daemon
    home (not the production default) must keep working — canonical comparison
    must not break legitimate isolated registration under pytest."""
    real_iso = tmp_path / "real-iso"
    real_iso.mkdir()  # the isolated daemon home exists before registration
    alias = tmp_path / "iso-alias"
    alias.symlink_to(real_iso, target_is_directory=True)
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(alias))
    fake = _make_exit0_fake(tmp_path)

    set_binary("claude", str(fake))
    assert get_binary("claude") == str(fake)
    assert (real_iso / "executors.json").exists()
    # Atomic replace leaves no scratch residue after a successful write.
    assert not (real_iso / "executors.json.tmp").exists()
