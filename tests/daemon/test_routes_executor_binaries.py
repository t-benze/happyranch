"""Daemon route tests for executor-binary registry (THR-085).

Tests GET /api/v1/executor-binaries, POST /api/v1/executor-binaries/register,
and POST /api/v1/executor-binaries/validate.
"""
from __future__ import annotations

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
    """POST /register succeeds when path is absolute, exists, and executable."""
    valid_bin = tmp_path / "claude_bin"
    valid_bin.touch(mode=0o755)

    r = client.post(
        "/api/v1/executor-binaries/register",
        json={"kind": "claude", "path": str(valid_bin)},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "claude"
    assert body["path"] == str(valid_bin.resolve())
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
    """POST /register updates an existing entry."""
    bin1 = tmp_path / "claude_v1"
    bin2 = tmp_path / "claude_v2"
    bin1.touch(mode=0o755)
    bin2.touch(mode=0o755)

    r1 = client.post(
        "/api/v1/executor-binaries/register",
        json={"kind": "claude", "path": str(bin1)},
    )
    assert r1.status_code == 200
    assert r1.json()["path"] == str(bin1.resolve())

    r2 = client.post(
        "/api/v1/executor-binaries/register",
        json={"kind": "claude", "path": str(bin2)},
    )
    assert r2.status_code == 200
    assert r2.json()["path"] == str(bin2.resolve())

    # Verify the listing shows the updated path
    r3 = client.get("/api/v1/executor-binaries")
    entries = r3.json()["entries"]
    claude_entry = next(e for e in entries if e["kind"] == "claude")
    assert claude_entry["path"] == str(bin2.resolve())


def test_validate_valid_path(client, tmp_path):
    """POST /validate returns valid=True for a valid executable."""
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
    assert body["path"] == str(valid_bin.resolve())


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


def test_routes_require_auth(app):
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
