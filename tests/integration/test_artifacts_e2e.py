"""End-to-end artifact flows against a live daemon.

Covers the full HTTP roundtrip:
  POST /artifacts → disk write → LIST /artifacts → GET /artifacts/{name} → audit entry.

The lifespan test verifies that starting the daemon with an org that has no
``artifacts/`` directory causes the daemon to create it (the startup loop in
``app.py`` calls ``(org.root / "artifacts").mkdir(exist_ok=True)``).
"""
from __future__ import annotations

import httpx
import pytest

from runtime.daemon import paths as paths_mod
from tests.integration.conftest import DEFAULT_TEST_SLUG

pytestmark = pytest.mark.integration


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {paths_mod.read_token()}"}


def test_put_list_get_roundtrip(
    live_daemon,
    runtime,
) -> None:
    """POST → disk → LIST → GET → audit roundtrip against the live daemon."""
    port = live_daemon
    slug = DEFAULT_TEST_SLUG
    base = f"http://127.0.0.1:{port}/api/v1/orgs/{slug}"
    headers = _auth_headers()

    file_content = b"pdf-content-here"

    # ── 1. PUT the artifact.
    put_resp = httpx.post(
        f"{base}/artifacts",
        params={"name": "report.pdf", "agent": "dev_agent"},
        files={"file": ("report.pdf", file_content, "application/pdf")},
        headers=headers,
        timeout=10.0,
    )
    assert put_resp.status_code == 200, put_resp.text
    put_body = put_resp.json()
    assert put_body["name"] == "report.pdf"
    assert put_body["size_bytes"] == len(file_content)

    # ── 2. Verify the file is on disk at the expected path.
    artifact_path = runtime / "artifacts" / "report.pdf"
    assert artifact_path.exists(), f"artifact not found on disk at {artifact_path}"
    assert artifact_path.read_bytes() == file_content

    # ── 3. LIST sees the new artifact.
    list_resp = httpx.get(f"{base}/artifacts", headers=headers, timeout=10.0)
    assert list_resp.status_code == 200, list_resp.text
    names = [a["name"] for a in list_resp.json()["artifacts"]]
    assert "report.pdf" in names, f"report.pdf not in artifact list: {names}"

    # ── 4. GET returns the exact bytes.
    get_resp = httpx.get(f"{base}/artifacts/report.pdf", headers=headers, timeout=10.0)
    assert get_resp.status_code == 200, get_resp.text
    assert get_resp.content == file_content

    # ── 5. Audit row exists for artifact_put with the correct name in the payload.
    audit_resp = httpx.get(
        f"{base}/audit",
        params={"action": "artifact_put"},
        headers=headers,
        timeout=10.0,
    )
    assert audit_resp.status_code == 200, audit_resp.text
    entries = audit_resp.json().get("entries", [])
    assert any(
        e.get("payload", {}).get("name") == "report.pdf"
        for e in entries
    ), f"no audit entry for report.pdf; entries={entries}"


def test_delete_roundtrip(
    live_daemon,
    runtime,
) -> None:
    """PUT → disk → DELETE → gone-from-disk → list-omits → delete-audit roundtrip."""
    port = live_daemon
    slug = DEFAULT_TEST_SLUG
    base = f"http://127.0.0.1:{port}/api/v1/orgs/{slug}"
    headers = _auth_headers()

    # ── 1. PUT the artifact.
    put_resp = httpx.post(
        f"{base}/artifacts",
        params={"name": "doomed.txt", "agent": "dev_agent"},
        files={"file": ("doomed.txt", b"delete-me", "text/plain")},
        headers=headers,
        timeout=10.0,
    )
    assert put_resp.status_code == 200, put_resp.text
    artifact_path = runtime / "artifacts" / "doomed.txt"
    assert artifact_path.exists()

    # ── 2. DELETE removes it.
    del_resp = httpx.request(
        "DELETE",
        f"{base}/artifacts/doomed.txt",
        params={"agent": "founder"},
        headers=headers,
        timeout=10.0,
    )
    assert del_resp.status_code == 200, del_resp.text
    assert del_resp.json() == {"name": "doomed.txt", "deleted": True}
    assert not artifact_path.exists()

    # ── 3. LIST no longer shows it.
    list_resp = httpx.get(f"{base}/artifacts", headers=headers, timeout=10.0)
    assert list_resp.status_code == 200, list_resp.text
    names = [a["name"] for a in list_resp.json()["artifacts"]]
    assert "doomed.txt" not in names, f"deleted artifact still listed: {names}"

    # ── 4. A second DELETE is a 404.
    miss_resp = httpx.request(
        "DELETE",
        f"{base}/artifacts/doomed.txt",
        params={"agent": "founder"},
        headers=headers,
        timeout=10.0,
    )
    assert miss_resp.status_code == 404
    assert miss_resp.json()["detail"]["code"] == "artifact_not_found"

    # ── 5. Audit row exists for artifact_delete with the correct name.
    audit_resp = httpx.get(
        f"{base}/audit",
        params={"action": "artifact_delete"},
        headers=headers,
        timeout=10.0,
    )
    assert audit_resp.status_code == 200, audit_resp.text
    entries = audit_resp.json().get("entries", [])
    assert any(
        e.get("payload", {}).get("name") == "doomed.txt"
        for e in entries
    ), f"no audit entry for artifact_delete; entries={entries}"


def test_lifespan_creates_artifacts_dir_for_existing_org(
    live_daemon,
    runtime,
) -> None:
    """Daemon startup creates artifacts/ for orgs that don't have it yet.

    The ``runtime`` fixture does NOT create artifacts/. The daemon lifespan runs
    ``(org.root / "artifacts").mkdir(exist_ok=True)`` for every registered org on
    startup, so by the time ``live_daemon`` is ready the directory must exist.
    """
    # The daemon is already up (live_daemon yielded) — just assert the dir.
    assert (runtime / "artifacts").is_dir(), (
        f"expected artifacts/ dir to be created by daemon lifespan under {runtime}"
    )
