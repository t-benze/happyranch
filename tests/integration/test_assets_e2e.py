"""End-to-end asset flows against a live daemon.

Covers the full HTTP roundtrip:
  POST /assets → disk write → LIST /assets → GET /assets/{name} → audit entry.

The lifespan test verifies that starting the daemon with an org that has no
``assets/`` directory causes the daemon to create it (the startup loop in
``app.py`` calls ``(org.root / "assets").mkdir(exist_ok=True)``).
"""
from __future__ import annotations

import httpx
import pytest

from src.daemon import paths as paths_mod
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

    # ── 1. PUT the asset.
    put_resp = httpx.post(
        f"{base}/assets",
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
    asset_path = runtime / "assets" / "report.pdf"
    assert asset_path.exists(), f"asset not found on disk at {asset_path}"
    assert asset_path.read_bytes() == file_content

    # ── 3. LIST sees the new asset.
    list_resp = httpx.get(f"{base}/assets", headers=headers, timeout=10.0)
    assert list_resp.status_code == 200, list_resp.text
    names = [a["name"] for a in list_resp.json()["assets"]]
    assert "report.pdf" in names, f"report.pdf not in asset list: {names}"

    # ── 4. GET returns the exact bytes.
    get_resp = httpx.get(f"{base}/assets/report.pdf", headers=headers, timeout=10.0)
    assert get_resp.status_code == 200, get_resp.text
    assert get_resp.content == file_content

    # ── 5. Audit row exists for asset_put with the correct name in the payload.
    audit_resp = httpx.get(
        f"{base}/audit",
        params={"action": "asset_put"},
        headers=headers,
        timeout=10.0,
    )
    assert audit_resp.status_code == 200, audit_resp.text
    entries = audit_resp.json().get("entries", [])
    assert any(
        e.get("payload", {}).get("name") == "report.pdf"
        for e in entries
    ), f"no audit entry for report.pdf; entries={entries}"


def test_lifespan_creates_assets_dir_for_existing_org(
    live_daemon,
    runtime,
) -> None:
    """Daemon startup creates assets/ for orgs that don't have it yet.

    The ``runtime`` fixture does NOT create assets/. The daemon lifespan runs
    ``(org.root / "assets").mkdir(exist_ok=True)`` for every registered org on
    startup, so by the time ``live_daemon`` is ready the directory must exist.
    """
    # The daemon is already up (live_daemon yielded) — just assert the dir.
    assert (runtime / "assets").is_dir(), (
        f"expected assets/ dir to be created by daemon lifespan under {runtime}"
    )
