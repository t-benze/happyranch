"""Integration test: learnings lifecycle against a real daemon.

Drives the full stack end-to-end:
  add -> list -> get -> search -> update -> promote (+ promote-locked guard)

No agent sessions are involved — all requests go via httpx against the live
daemon, mirroring how the daemon route tests work but with a real running
process instead of a TestClient in-process stub.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth_headers() -> dict:
    from src.daemon import paths

    return {"Authorization": f"Bearer {paths.read_token()}"}


def _base(port: str, slug: str = "test") -> str:
    return f"http://127.0.0.1:{port}/api/v1/orgs/{slug}"


def _seed_learnings_dir(org_root: Path, agent: str) -> Path:
    """Create workspace + learnings/ dir to simulate a migrated workspace."""
    ws = org_root / "workspaces" / agent
    ws.mkdir(parents=True, exist_ok=True)
    learnings = ws / "learnings"
    learnings.mkdir(exist_ok=True)
    return learnings


def _seed_kb_entry(base: str, headers: dict, *, kb_slug: str) -> None:
    """Seed a KB precedent entry so promote can reference it."""
    r = httpx.post(
        f"{base}/kb",
        json={
            "agent": "dev_agent",
            "slug": kb_slug,
            "title": "Seeded precedent for promote test",
            "type": "precedent",
            "topic": "testing",
            "tags": ["test"],
            "body": "# Seeded precedent\n\nThis entry was seeded by the integration test.\n",
        },
        headers=headers,
        timeout=5.0,
    )
    assert r.status_code == 200, f"kb seed failed: {r.text}"


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


def test_learnings_full_lifecycle(live_daemon, runtime):
    """End-to-end learnings lifecycle: add -> list -> get -> search -> update -> promote."""
    port = live_daemon
    base = _base(port)
    headers = _auth_headers()
    agent = "dev_agent"

    # Step 1: simulate a migrated workspace by creating the learnings/ dir
    _seed_learnings_dir(runtime, agent)

    # Step 2: seed a KB entry the promote step will reference
    kb_slug = "test-precedent-for-learnings"
    _seed_kb_entry(base, headers, kb_slug=kb_slug)

    # Step 3: add a learning entry
    add_payload = {
        "slug": "from-payload-keyword-test",
        "title": "Integration test learning",
        "topic": "testing",
        "body": "# Integration test learning\n\nThis body contains from-payload-keyword for search.\n",
        "tags": ["integration", "test"],
        "source_task": None,
    }
    r = httpx.post(
        f"{base}/agents/{agent}/learnings/entries/",
        json=add_payload,
        headers=headers,
        timeout=5.0,
    )
    assert r.status_code == 201, f"add failed: {r.text}"
    add_resp = r.json()
    learning_id = add_resp["id"]
    assert learning_id.startswith("LRN-"), f"expected LRN-NNN, got {learning_id!r}"
    assert "path" in add_resp

    # Step 4: list -> verify entry shows
    r = httpx.get(
        f"{base}/agents/{agent}/learnings/entries/",
        headers=headers,
        timeout=5.0,
    )
    assert r.status_code == 200, r.text
    entries = r.json()["entries"]
    assert any(e["id"] == learning_id for e in entries), (
        f"{learning_id} not in list: {entries}"
    )

    # Step 5: get by ID -> verify body content
    r = httpx.get(
        f"{base}/agents/{agent}/learnings/entries/{learning_id}",
        headers=headers,
        timeout=5.0,
    )
    assert r.status_code == 200, r.text
    entry = r.json()
    assert entry["id"] == learning_id
    assert "from-payload-keyword" in entry["body"]
    assert entry["topic"] == "testing"
    assert entry.get("promoted_to") is None

    # Step 6: search -> verify hit
    r = httpx.post(
        f"{base}/agents/{agent}/learnings/entries/search",
        json={"query": "from-payload-keyword", "limit": 10},
        headers=headers,
        timeout=5.0,
    )
    assert r.status_code == 200, r.text
    hits = r.json()["hits"]
    assert any(h["id"] == learning_id for h in hits), (
        f"{learning_id} not in search hits: {hits}"
    )

    # Step 7: update -> change title, keep same slug
    update_payload = {
        "slug": "from-payload-keyword-test",
        "title": "Updated integration test learning",
        "topic": "testing",
        "body": "# Updated integration test learning\n\nBody updated.\n",
        "tags": ["integration", "test", "updated"],
    }
    r = httpx.put(
        f"{base}/agents/{agent}/learnings/entries/{learning_id}",
        json=update_payload,
        headers=headers,
        timeout=5.0,
    )
    assert r.status_code == 200, f"update failed: {r.text}"
    updated = r.json()
    assert updated["title"] == "Updated integration test learning"
    assert "updated" in updated["tags"]

    # Step 8: promote -> link to KB entry
    r = httpx.post(
        f"{base}/agents/{agent}/learnings/entries/{learning_id}/promote",
        json={"kb_slug": kb_slug},
        headers=headers,
        timeout=5.0,
    )
    assert r.status_code == 200, f"promote failed: {r.text}"
    promoted = r.json()
    assert promoted["promoted_to"] == kb_slug, (
        f"expected promoted_to={kb_slug!r}, got {promoted.get('promoted_to')!r}"
    )

    # Step 9: get again -> verify promoted_to is set
    r = httpx.get(
        f"{base}/agents/{agent}/learnings/entries/{learning_id}",
        headers=headers,
        timeout=5.0,
    )
    assert r.status_code == 200, r.text
    entry_after = r.json()
    assert entry_after["promoted_to"] == kb_slug

    # Step 10: update after promote -> must be rejected with promoted_locked
    r = httpx.put(
        f"{base}/agents/{agent}/learnings/entries/{learning_id}",
        json=update_payload,
        headers=headers,
        timeout=5.0,
    )
    assert r.status_code == 409, (
        f"expected 409 promoted_locked, got {r.status_code}: {r.text}"
    )
    detail = r.json()["detail"]
    assert detail.get("error") == "promoted_locked", detail
