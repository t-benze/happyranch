from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from src.infrastructure.asset_store import MAX_ASSET_BYTES


# ---------------------------------------------------------------------------
# PUT /assets
# ---------------------------------------------------------------------------


def test_put_creates_asset(tmp_home, app, org_state, auth_headers) -> None:
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/assets",
        params={"name": "report.pdf", "agent": "dev_agent"},
        files={"file": ("report.pdf", b"hello world", "application/pdf")},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "report.pdf"
    assert body["size_bytes"] == 11
    assert (org_state.root / "assets" / "report.pdf").read_bytes() == b"hello world"


def test_put_uses_uploaded_filename_when_name_omitted(tmp_home, app, org_state, auth_headers) -> None:
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/assets",
        params={"agent": "dev_agent"},
        files={"file": ("uploaded.bin", b"abc", "application/octet-stream")},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "uploaded.bin"


def test_put_rejects_invalid_name(tmp_home, app, org_state, auth_headers) -> None:
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/assets",
        params={"name": "../escape", "agent": "dev_agent"},
        files={"file": ("x", b"x", "application/octet-stream")},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_asset_name"


def test_put_rejects_oversized(tmp_home, app, org_state, auth_headers) -> None:
    client = TestClient(app)
    big = b"x" * (MAX_ASSET_BYTES + 1)
    r = client.post(
        "/api/v1/orgs/alpha/assets",
        params={"name": "big.bin", "agent": "dev_agent"},
        files={"file": ("big.bin", big, "application/octet-stream")},
        headers=auth_headers,
    )
    assert r.status_code == 413
    assert r.json()["detail"]["code"] == "asset_too_large"


def test_put_requires_auth(tmp_home, app) -> None:
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/assets",
        params={"name": "x.txt", "agent": "dev_agent"},
        files={"file": ("x.txt", b"hi", "text/plain")},
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# GET /assets (list)
# ---------------------------------------------------------------------------


def test_list_returns_summaries(tmp_home, app, org_state, auth_headers) -> None:
    client = TestClient(app)
    client.post(
        "/api/v1/orgs/alpha/assets",
        params={"name": "a.txt", "agent": "x"},
        files={"file": ("a.txt", b"1", "text/plain")},
        headers=auth_headers,
    )
    client.post(
        "/api/v1/orgs/alpha/assets",
        params={"name": "b.txt", "agent": "x"},
        files={"file": ("b.txt", b"22", "text/plain")},
        headers=auth_headers,
    )
    r = client.get("/api/v1/orgs/alpha/assets", headers=auth_headers)
    assert r.status_code == 200
    items = r.json()["assets"]
    assert [a["name"] for a in items] == ["a.txt", "b.txt"]


def test_list_empty_when_no_assets(tmp_home, app, org_state, auth_headers) -> None:
    client = TestClient(app)
    r = client.get("/api/v1/orgs/alpha/assets", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["assets"] == []


# ---------------------------------------------------------------------------
# GET /assets/{name}
# ---------------------------------------------------------------------------


def test_get_returns_bytes(tmp_home, app, org_state, auth_headers) -> None:
    client = TestClient(app)
    client.post(
        "/api/v1/orgs/alpha/assets",
        params={"name": "a.txt", "agent": "x"},
        files={"file": ("a.txt", b"contents", "text/plain")},
        headers=auth_headers,
    )
    r = client.get("/api/v1/orgs/alpha/assets/a.txt", headers=auth_headers)
    assert r.status_code == 200
    assert r.content == b"contents"


def test_get_missing_returns_404(tmp_home, app, org_state, auth_headers) -> None:
    client = TestClient(app)
    r = client.get("/api/v1/orgs/alpha/assets/missing.txt", headers=auth_headers)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "asset_not_found"


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def test_put_writes_audit_event(tmp_home, app, org_state, auth_headers) -> None:
    client = TestClient(app)
    client.post(
        "/api/v1/orgs/alpha/assets",
        params={"name": "x.txt", "agent": "dev_agent"},
        files={"file": ("x.txt", b"hi", "text/plain")},
        headers=auth_headers,
    )
    r = client.get("/api/v1/orgs/alpha/audit", params={"action": "asset_put"}, headers=auth_headers)
    assert r.status_code == 200
    entries = r.json().get("entries", [])
    assert any(e.get("payload", {}).get("name") == "x.txt" for e in entries)
