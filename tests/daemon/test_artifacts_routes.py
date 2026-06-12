from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from runtime.infrastructure.artifact_store import MAX_ARTIFACT_BYTES


# ---------------------------------------------------------------------------
# PUT /artifacts
# ---------------------------------------------------------------------------


def test_put_creates_artifact(tmp_home, app, org_state, auth_headers) -> None:
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/artifacts",
        params={"name": "report.pdf", "agent": "dev_agent"},
        files={"file": ("report.pdf", b"hello world", "application/pdf")},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "report.pdf"
    assert body["size_bytes"] == 11
    assert (org_state.root / "artifacts" / "report.pdf").read_bytes() == b"hello world"


def test_put_uses_uploaded_filename_when_name_omitted(tmp_home, app, org_state, auth_headers) -> None:
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/artifacts",
        params={"agent": "dev_agent"},
        files={"file": ("uploaded.bin", b"abc", "application/octet-stream")},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "uploaded.bin"


def test_put_rejects_invalid_name(tmp_home, app, org_state, auth_headers) -> None:
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/artifacts",
        params={"name": "../escape", "agent": "dev_agent"},
        files={"file": ("x", b"x", "application/octet-stream")},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_artifact_name"


def test_put_rejects_oversized(tmp_home, app, org_state, auth_headers) -> None:
    client = TestClient(app)
    big = b"x" * (MAX_ARTIFACT_BYTES + 1)
    r = client.post(
        "/api/v1/orgs/alpha/artifacts",
        params={"name": "big.bin", "agent": "dev_agent"},
        files={"file": ("big.bin", big, "application/octet-stream")},
        headers=auth_headers,
    )
    assert r.status_code == 413
    assert r.json()["detail"]["code"] == "artifact_too_large"


def test_put_requires_auth(tmp_home, app) -> None:
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/artifacts",
        params={"name": "x.txt", "agent": "dev_agent"},
        files={"file": ("x.txt", b"hi", "text/plain")},
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# GET /artifacts (list)
# ---------------------------------------------------------------------------


def test_list_returns_summaries(tmp_home, app, org_state, auth_headers) -> None:
    client = TestClient(app)
    client.post(
        "/api/v1/orgs/alpha/artifacts",
        params={"name": "a.txt", "agent": "x"},
        files={"file": ("a.txt", b"1", "text/plain")},
        headers=auth_headers,
    )
    client.post(
        "/api/v1/orgs/alpha/artifacts",
        params={"name": "b.txt", "agent": "x"},
        files={"file": ("b.txt", b"22", "text/plain")},
        headers=auth_headers,
    )
    r = client.get("/api/v1/orgs/alpha/artifacts", headers=auth_headers)
    assert r.status_code == 200
    items = r.json()["artifacts"]
    assert [a["name"] for a in items] == ["a.txt", "b.txt"]


def test_list_empty_when_no_artifacts(tmp_home, app, org_state, auth_headers) -> None:
    client = TestClient(app)
    r = client.get("/api/v1/orgs/alpha/artifacts", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["artifacts"] == []


# ---------------------------------------------------------------------------
# GET /artifacts/{name}
# ---------------------------------------------------------------------------


def test_get_returns_bytes(tmp_home, app, org_state, auth_headers) -> None:
    client = TestClient(app)
    client.post(
        "/api/v1/orgs/alpha/artifacts",
        params={"name": "a.txt", "agent": "x"},
        files={"file": ("a.txt", b"contents", "text/plain")},
        headers=auth_headers,
    )
    r = client.get("/api/v1/orgs/alpha/artifacts/a.txt", headers=auth_headers)
    assert r.status_code == 200
    assert r.content == b"contents"


def test_get_missing_returns_404(tmp_home, app, org_state, auth_headers) -> None:
    client = TestClient(app)
    r = client.get("/api/v1/orgs/alpha/artifacts/missing.txt", headers=auth_headers)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "artifact_not_found"


# ---------------------------------------------------------------------------
# DELETE /artifacts/{name}
# ---------------------------------------------------------------------------


def test_delete_removes_artifact(tmp_home, app, org_state, auth_headers) -> None:
    client = TestClient(app)
    client.post(
        "/api/v1/orgs/alpha/artifacts",
        params={"name": "gone.txt", "agent": "dev_agent"},
        files={"file": ("gone.txt", b"bye", "text/plain")},
        headers=auth_headers,
    )
    assert (org_state.root / "artifacts" / "gone.txt").exists()

    r = client.delete(
        "/api/v1/orgs/alpha/artifacts/gone.txt",
        params={"agent": "founder"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"name": "gone.txt", "deleted": True}
    assert not (org_state.root / "artifacts" / "gone.txt").exists()


def test_delete_missing_returns_404(tmp_home, app, org_state, auth_headers) -> None:
    client = TestClient(app)
    r = client.delete(
        "/api/v1/orgs/alpha/artifacts/missing.txt",
        params={"agent": "founder"},
        headers=auth_headers,
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "artifact_not_found"


def test_delete_rejects_invalid_name(tmp_home, app, org_state, auth_headers) -> None:
    client = TestClient(app)
    r = client.delete(
        "/api/v1/orgs/alpha/artifacts/bad%20name.txt",
        params={"agent": "founder"},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_artifact_name"


def test_delete_requires_auth(tmp_home, app) -> None:
    client = TestClient(app)
    r = client.delete(
        "/api/v1/orgs/alpha/artifacts/x.txt",
        params={"agent": "founder"},
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def test_put_writes_audit_event(tmp_home, app, org_state, auth_headers) -> None:
    client = TestClient(app)
    client.post(
        "/api/v1/orgs/alpha/artifacts",
        params={"name": "x.txt", "agent": "dev_agent"},
        files={"file": ("x.txt", b"hi", "text/plain")},
        headers=auth_headers,
    )
    r = client.get("/api/v1/orgs/alpha/audit", params={"action": "artifact_put"}, headers=auth_headers)
    assert r.status_code == 200
    entries = r.json().get("entries", [])
    assert any(e.get("payload", {}).get("name") == "x.txt" for e in entries)


def test_delete_writes_audit_event(tmp_home, app, org_state, auth_headers) -> None:
    client = TestClient(app)
    client.post(
        "/api/v1/orgs/alpha/artifacts",
        params={"name": "audit-me.txt", "agent": "dev_agent"},
        files={"file": ("audit-me.txt", b"hi", "text/plain")},
        headers=auth_headers,
    )
    client.delete(
        "/api/v1/orgs/alpha/artifacts/audit-me.txt",
        params={"agent": "founder"},
        headers=auth_headers,
    )
    r = client.get(
        "/api/v1/orgs/alpha/audit",
        params={"action": "artifact_delete"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    entries = r.json().get("entries", [])
    row = next(e for e in entries if e.get("payload", {}).get("name") == "audit-me.txt")
    # Exact shape: artifact:<name> scope, artifact_delete action, name-only payload.
    assert row["task_id"] == "artifact:audit-me.txt"
    assert row["action"] == "artifact_delete"
    assert row["agent"] == "founder"
    assert row["payload"] == {"name": "audit-me.txt"}
