"""Daemon route tests for executor-binary registry (THR-085).

Tests GET /api/v1/executor-binaries, POST /api/v1/executor-binaries/register,
and POST /api/v1/executor-binaries/validate.
"""
from __future__ import annotations

import os

from fastapi.testclient import TestClient


def test_list_binaries_empty(client):
    """GET /executor-binaries returns empty list when no entries registered."""
    r = client.get("/api/v1/executor-binaries")
    assert r.status_code == 200
    body = r.json()
    assert body["entries"] == []


def test_list_binaries_with_entries(client, tmp_path):
    """GET /executor-binaries returns registered entries with validity."""
    # Register a valid binary
    valid_bin = tmp_path / "valid_claude"
    valid_bin.touch(mode=0o755)
    r1 = client.post(
        "/api/v1/executor-binaries/register",
        json={"kind": "claude", "path": str(valid_bin)},
    )
    assert r1.status_code == 200

    # Now delete the binary to make it stale
    valid_bin.unlink()

    r = client.get("/api/v1/executor-binaries")
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert len(entries) == 1

    claude_entry = next(e for e in entries if e["kind"] == "claude")
    assert claude_entry["path"] is not None
    # The binary was just unlinked, so it's now invalid
    assert claude_entry["valid"] is False


def test_list_binaries_valid_entry(client, tmp_path):
    """GET /executor-binaries shows valid=True for existing+executable binaries."""
    valid_bin = tmp_path / "pi_bin"
    valid_bin.touch(mode=0o755)

    r1 = client.post(
        "/api/v1/executor-binaries/register",
        json={"kind": "pi", "path": str(valid_bin)},
    )
    assert r1.status_code == 200

    r = client.get("/api/v1/executor-binaries")
    entries = r.json()["entries"]
    pi_entry = next(e for e in entries if e["kind"] == "pi")
    assert pi_entry["valid"] is True


def test_register_valid_binary(client, tmp_path):
    """POST /register succeeds when path is absolute, exists, and executable.

    THR-107: the returned path preserves the operator-supplied spelling
    (not Path.resolve()'s versioned target).
    """
    valid_bin = tmp_path / "claude_bin"
    valid_bin.touch(mode=0o755)

    r = client.post(
        "/api/v1/executor-binaries/register",
        json={"kind": "claude", "path": str(valid_bin)},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "claude"
    assert body["path"] == str(valid_bin)
    assert body["valid"] is True


def test_register_rejects_relative_path(client):
    """POST /register rejects a relative path with 422."""
    r = client.post(
        "/api/v1/executor-binaries/register",
        json={"kind": "claude", "path": "relative/claude"},
    )
    assert r.status_code == 422
    assert "absolute" in r.json()["detail"].lower()


def test_register_rejects_nonexistent(client):
    """POST /register rejects a non-existent path with 422."""
    r = client.post(
        "/api/v1/executor-binaries/register",
        json={"kind": "claude", "path": "/nonexistent/claude"},
    )
    assert r.status_code == 422
    assert "does not exist" in r.json()["detail"]


def test_register_rejects_non_executable(client, tmp_path):
    """POST /register rejects a non-executable file with 422."""
    f = tmp_path / "not_exec"
    f.touch(mode=0o644)
    r = client.post(
        "/api/v1/executor-binaries/register",
        json={"kind": "claude", "path": str(f)},
    )
    assert r.status_code == 422
    assert "not executable" in r.json()["detail"].lower()


def test_register_updates_existing(client, tmp_path):
    """POST /register updates an existing entry.

    THR-107: stored path preserves operator-supplied spelling.
    """
    bin1 = tmp_path / "claude_v1"
    bin2 = tmp_path / "claude_v2"
    bin1.touch(mode=0o755)
    bin2.touch(mode=0o755)

    r1 = client.post(
        "/api/v1/executor-binaries/register",
        json={"kind": "claude", "path": str(bin1)},
    )
    assert r1.status_code == 200
    assert r1.json()["path"] == str(bin1)

    r2 = client.post(
        "/api/v1/executor-binaries/register",
        json={"kind": "claude", "path": str(bin2)},
    )
    assert r2.status_code == 200
    assert r2.json()["path"] == str(bin2)

    # Verify the listing shows the updated path
    r3 = client.get("/api/v1/executor-binaries")
    entries = r3.json()["entries"]
    claude_entry = next(e for e in entries if e["kind"] == "claude")
    assert claude_entry["path"] == str(bin2)


def test_validate_valid_path(client, tmp_path):
    """POST /validate returns valid=True for a valid executable.

    THR-107: returned path preserves the supplied spelling (not resolved target).
    """
    valid_bin = tmp_path / "valid_bin"
    valid_bin.touch(mode=0o755)

    r = client.post(
        "/api/v1/executor-binaries/validate",
        json={"path": str(valid_bin)},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is True
    assert body["error"] is None
    assert body["path"] == str(valid_bin)


def test_validate_invalid_path(client):
    """POST /validate returns valid=False for an invalid path."""
    r = client.post(
        "/api/v1/executor-binaries/validate",
        json={"path": "relative/path"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is False
    assert body["error"] is not None
    assert "absolute" in body["error"].lower()


def test_routes_require_auth(app, tmp_home):
    """All executor-binary routes require bearer auth."""
    # Unauthenticated
    from fastapi.testclient import TestClient
    client = TestClient(app)  # No auth headers

    r = client.get("/api/v1/executor-binaries")
    assert r.status_code == 401

    r = client.post(
        "/api/v1/executor-binaries/register",
        json={"kind": "claude", "path": "/some/claude"},
    )
    assert r.status_code == 401

    r = client.post(
        "/api/v1/executor-binaries/validate",
        json={"path": "/some/claude"},
    )
    assert r.status_code == 401


def test_register_case_insensitive_kind(client, tmp_path):
    """POST /register normalizes the kind to lowercase."""
    valid_bin = tmp_path / "my_claude"
    valid_bin.touch(mode=0o755)

    r = client.post(
        "/api/v1/executor-binaries/register",
        json={"kind": "CLAUDE", "path": str(valid_bin)},
    )
    assert r.status_code == 200
    assert r.json()["kind"] == "CLAUDE"  # Echoed back as-given

    # Verify stored as lowercase
    r2 = client.get("/api/v1/executor-binaries")
    entries = r2.json()["entries"]
    assert any(e["kind"] == "claude" for e in entries)


# ── THR-107: symlink path preservation tests ───────────────────────────


def test_register_symlink_preserves_path(client, tmp_path):
    """POST /register preserves a symlink path instead of resolving it.

    THR-107: stable Homebrew symlinks like /opt/homebrew/bin/claude →
    ../Cellar/.../bin/claude must be stored as the symlink spelling so
    stored paths survive version bumps in the Cellar target.
    """
    # Create a real executable target
    target = tmp_path / "target" / "real-claude"
    target.parent.mkdir()
    target.touch(mode=0o755)

    # Create a symlink pointing to the target
    symlink = tmp_path / "claude"
    os.symlink(str(target), str(symlink))

    r = client.post(
        "/api/v1/executor-binaries/register",
        json={"kind": "claude", "path": str(symlink)},
    )
    assert r.status_code == 200
    body = r.json()
    # Must return the symlink path, not the resolved target
    assert body["path"] == str(symlink)
    assert body["path"] != str(target.resolve())
    assert body["valid"] is True


def test_register_symlink_listed_as_symlink(client, tmp_path):
    """GET /executor-binaries returns the stored symlink path, not resolved."""
    target = tmp_path / "target" / "real-pi"
    target.parent.mkdir()
    target.touch(mode=0o755)
    symlink = tmp_path / "pi"
    os.symlink(str(target), str(symlink))

    client.post(
        "/api/v1/executor-binaries/register",
        json={"kind": "pi", "path": str(symlink)},
    )

    r = client.get("/api/v1/executor-binaries")
    entries = r.json()["entries"]
    pi_entry = next(e for e in entries if e["kind"] == "pi")
    assert pi_entry["path"] == str(symlink)
    assert pi_entry["path"] != str(target.resolve())
    assert pi_entry["valid"] is True


def test_register_symlink_becomes_stale_when_target_gone(client, tmp_path):
    """After a symlink's target is removed, the stored path becomes stale.

    THR-107 staleness invariant: is_binary_valid detects stale symlinks
    so _resolve_binary keeps the existing actionable no-PATH-fallback block.
    """
    import os
    target = tmp_path / "target" / "real-claude"
    target.parent.mkdir()
    target.touch(mode=0o755)
    symlink = tmp_path / "claude"
    os.symlink(str(target), str(symlink))

    client.post(
        "/api/v1/executor-binaries/register",
        json={"kind": "claude", "path": str(symlink)},
    )

    # Validate endpoint still sees it as valid
    r_val = client.post(
        "/api/v1/executor-binaries/validate",
        json={"path": str(symlink)},
    )
    assert r_val.json()["valid"] is True

    # Delete target → symlink becomes stale
    target.unlink()

    # List now shows valid=False
    r = client.get("/api/v1/executor-binaries")
    entries = r.json()["entries"]
    claude_entry = next(e for e in entries if e["kind"] == "claude")
    assert claude_entry["valid"] is False
    assert claude_entry["path"] == str(symlink)  # Path still preserved

    # Validate endpoint also detects staleness
    r_val2 = client.post(
        "/api/v1/executor-binaries/validate",
        json={"path": str(symlink)},
    )
    assert r_val2.json()["valid"] is False
    assert r_val2.json()["error"] is not None


# ═══════════════════════════════════════════════════════════════════════════
# DELETE /api/v1/executor-binaries/{kind} — guarded conditional removal
# ═══════════════════════════════════════════════════════════════════════════

import json


def test_delete_success_exact_match(client, tmp_path):
    """DELETE with matching expected_name + expected_path removes the entry."""
    bin_exe = tmp_path / "d7a-test-bin"
    bin_exe.touch(mode=0o755)

    # Register a test kind
    r = client.post(
        "/api/v1/executor-binaries/register",
        json={"kind": "d7a-tester", "path": str(bin_exe)},
    )
    assert r.status_code == 200

    # Delete with exact match (use request() for DELETE with body)
    r_del = client.request(
        "DELETE",
        "/api/v1/executor-binaries/d7a-tester",
        content=json.dumps({"expected_name": "d7a-tester", "expected_path": str(bin_exe)}),
        headers={"Content-Type": "application/json"},
    )
    assert r_del.status_code == 200
    body = r_del.json()
    assert body["kind"] == "d7a-tester"
    assert body["removed"] is True

    # Verify no longer listed
    r_list = client.get("/api/v1/executor-binaries")
    entries = r_list.json()["entries"]
    assert not any(e["kind"] == "d7a-tester" for e in entries)


def test_delete_expected_name_mismatch(client, tmp_path):
    """DELETE returns 422 when expected_name does not equal the URL kind."""
    bin_exe = tmp_path / "d7a-match-name"
    bin_exe.touch(mode=0o755)

    client.post(
        "/api/v1/executor-binaries/register",
        json={"kind": "d7a-match-name", "path": str(bin_exe)},
    )

    r = client.request(
        "DELETE",
        "/api/v1/executor-binaries/d7a-match-name",
        content=json.dumps({"expected_name": "different-name", "expected_path": str(bin_exe)}),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "expected_name" in detail.lower()
    assert "d7a-match-name" in detail

    # Entry must not be removed
    r_list = client.get("/api/v1/executor-binaries")
    entries = r_list.json()["entries"]
    assert any(e["kind"] == "d7a-match-name" for e in entries)


def test_delete_stale_expected_path_returns_409(client, tmp_path):
    """DELETE returns 409 when expected_path differs from the stored path.

    THR-107 race protection: a concurrent writer updated the entry
    between the operator's observation and the DELETE request, so we
    must reject to avoid deleting the wrong target.
    """
    bin1 = tmp_path / "d7a-stale-v1"
    bin2 = tmp_path / "d7a-stale-v2"
    bin1.touch(mode=0o755)
    bin2.touch(mode=0o755)

    client.post(
        "/api/v1/executor-binaries/register",
        json={"kind": "d7a-stale", "path": str(bin1)},
    )

    # Concurrent writer updates to bin2 (simulating race)
    client.post(
        "/api/v1/executor-binaries/register",
        json={"kind": "d7a-stale", "path": str(bin2)},
    )

    # Now try to delete with the STALE observed path (bin1)
    r = client.request(
        "DELETE",
        "/api/v1/executor-binaries/d7a-stale",
        content=json.dumps({"expected_name": "d7a-stale", "expected_path": str(bin1)}),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "does not match" in detail.lower() or "expected_path" in detail.lower()

    # Entry must still exist with the new path
    r_list = client.get("/api/v1/executor-binaries")
    entries = r_list.json()["entries"]
    d7a_entry = next(e for e in entries if e["kind"] == "d7a-stale")
    assert d7a_entry["path"] == str(bin2)


def test_delete_race_protection_atomic(client, tmp_path):
    """The compare+delete is atomic — a later-updated entry is never removed
    when the observed path is stale."""
    bin1 = tmp_path / "d7a-atomic-v1"
    bin2 = tmp_path / "d7a-atomic-v2"
    bin1.touch(mode=0o755)
    bin2.touch(mode=0o755)

    client.post(
        "/api/v1/executor-binaries/register",
        json={"kind": "d7a-atomic", "path": str(bin1)},
    )

    # Update to bin2
    client.post(
        "/api/v1/executor-binaries/register",
        json={"kind": "d7a-atomic", "path": str(bin2)},
    )

    # Delete with bin1 path (stale) → 409
    r = client.request(
        "DELETE",
        "/api/v1/executor-binaries/d7a-atomic",
        content=json.dumps({"expected_name": "d7a-atomic", "expected_path": str(bin1)}),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 409

    # Delete with bin2 path (current) → 200
    r2 = client.request(
        "DELETE",
        "/api/v1/executor-binaries/d7a-atomic",
        content=json.dumps({"expected_name": "d7a-atomic", "expected_path": str(bin2)}),
        headers={"Content-Type": "application/json"},
    )
    assert r2.status_code == 200
    assert r2.json()["removed"] is True

    # Now gone
    r_list = client.get("/api/v1/executor-binaries")
    entries = r_list.json()["entries"]
    assert not any(e["kind"] == "d7a-atomic" for e in entries)


def test_delete_builtin_kind_rejected(client, tmp_path):
    """DELETE of a built-in kind (claude, codex, opencode, pi) returns 422."""
    bin_exe = tmp_path / "claude-bin"
    bin_exe.touch(mode=0o755)

    # Register first (required for the test to be meaningful)
    client.post(
        "/api/v1/executor-binaries/register",
        json={"kind": "claude", "path": str(bin_exe)},
    )

    r = client.request(
        "DELETE",
        "/api/v1/executor-binaries/claude",
        content=json.dumps({"expected_name": "claude", "expected_path": str(bin_exe)}),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 422
    assert "built-in" in r.json()["detail"].lower() or "cannot remove" in r.json()["detail"].lower()


def test_delete_nonexistent_kind_returns_404(client):
    """DELETE for an unregistered kind returns 404."""
    r = client.request(
        "DELETE",
        "/api/v1/executor-binaries/no-such-kind",
        content=json.dumps({"expected_name": "no-such-kind", "expected_path": "/no/such/path"}),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 404


def test_delete_requires_auth(app, tmp_home):
    """DELETE route requires bearer auth."""
    from fastapi.testclient import TestClient
    client_noauth = TestClient(app)
    r = client_noauth.request(
        "DELETE",
        "/api/v1/executor-binaries/some-kind",
        content=json.dumps({"expected_name": "some-kind", "expected_path": "/some/path"}),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 401
