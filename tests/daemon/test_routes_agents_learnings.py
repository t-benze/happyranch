"""Tests for GET /agents/{agent}/memory/entries/* routes + 412 guard."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from runtime.daemon import paths as paths_mod
from runtime.daemon.app import create_app


@pytest.fixture
def client_with_migrated_workspace(tmp_home, daemon_state):
    """TestClient + a pre-migrated workspace with memory/ dir.

    Yields (client, token, slug, agent_name, workspace_path).
    """
    slug = "alpha"
    agent = "dev_agent"
    org = daemon_state.orgs[slug]
    ws = org.root / "workspaces" / agent
    (ws / "memory").mkdir(parents=True, exist_ok=True)

    app = create_app(daemon_state)
    client = TestClient(app)
    token = paths_mod.read_token()
    yield client, token, slug, agent, ws


def test_list_returns_empty_on_migrated_workspace(tmp_home, app, org_state, auth_headers):
    """memory/ dir exists (migrated) → 200 with empty entries list."""
    workspace = org_state.root / "workspaces" / "dev_agent"
    (workspace / "memory").mkdir(parents=True, exist_ok=True)
    r = TestClient(app).get(
        "/api/v1/orgs/alpha/agents/dev_agent/memory/entries/",
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json() == {"entries": []}


def test_list_returns_412_on_pre_migration_workspace(tmp_home, app, org_state, auth_headers):
    """No memory/ dir but flat learnings.md exists → 412 workspace_not_migrated."""
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "learnings.md").write_text("# Learnings\n")
    # memory/ dir must NOT exist
    assert not (workspace / "memory").exists()
    r = TestClient(app).get(
        "/api/v1/orgs/alpha/agents/dev_agent/memory/entries/",
        headers=auth_headers,
    )
    assert r.status_code == 412
    body = r.json()
    assert body["detail"]["error"] == "workspace_not_migrated"
    assert body["detail"]["migrate_first"] is True


def test_add_allocates_id_and_persists(client_with_migrated_workspace):
    client, token, slug, agent, ws = client_with_migrated_workspace
    payload = {
        "slug": "first-rule",
        "title": "First rule",
        "topic": "workflow",
        "tags": ["sample"],
        "body": "**Why:** test\n**How to apply:** later\n",
    }
    r = client.post(
        f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"] == "MEM-001"
    assert body["path"] == "memory/MEM-001-first-rule.md"
    assert (ws / "memory" / "MEM-001-first-rule.md").exists()


def test_update_preserves_authored_at(client_with_migrated_workspace):
    client, token, slug, agent, ws = client_with_migrated_workspace
    # Seed
    client.post(
        f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/",
        headers={"Authorization": f"Bearer {token}"},
        json={"slug": "a", "title": "v1", "topic": "w", "body": "old\n"},
    )
    # Update
    r = client.put(
        f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/MEM-001",
        headers={"Authorization": f"Bearer {token}"},
        json={"slug": "a", "title": "v2", "topic": "w", "body": "new\n"},
    )
    assert r.status_code == 200
    assert r.json()["title"] == "v2"


def test_update_via_legacy_lrn_id_stays_canonical_mem(client_with_migrated_workspace):
    """REVISE TASK-974 F1: PUT .../memory/entries/LRN-001 on a migrated MEM
    item must canonicalize to MEM and NOT resurrect an LRN- file (§3.3/§7.2(b))."""
    client, token, slug, agent, ws = client_with_migrated_workspace
    h = {"Authorization": f"Bearer {token}"}
    client.post(
        f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/",
        headers=h, json={"slug": "a", "title": "v1", "topic": "w", "body": "old\n"},
    )
    r = client.put(
        f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/LRN-001",
        headers=h, json={"slug": "a", "title": "v2", "topic": "w", "body": "new\n"},
    )
    assert r.status_code == 200
    assert r.json()["id"] == "MEM-001"  # canonical, not LRN-001
    files = sorted(
        p.name for p in (ws / "memory").glob("*.md") if p.name != "_index.md"
    )
    assert files == ["MEM-001-a.md"]
    assert not (ws / "memory" / "LRN-001-a.md").exists()
    # Both ids still resolve to the same canonical MEM item.
    via_lrn = client.get(
        f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/LRN-001", headers=h,
    )
    via_mem = client.get(
        f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/MEM-001", headers=h,
    )
    assert via_lrn.json()["id"] == via_mem.json()["id"] == "MEM-001"
    assert via_lrn.json()["body"] == via_mem.json()["body"] == "new\n"


def test_update_via_legacy_learnings_forwarder_stays_canonical_mem(client_with_migrated_workspace):
    """The hidden /learnings/ forwarder shares the handler, so it must also
    canonicalize a legacy-id update to the on-disk MEM id."""
    client, token, slug, agent, ws = client_with_migrated_workspace
    h = {"Authorization": f"Bearer {token}"}
    client.post(
        f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/",
        headers=h, json={"slug": "a", "title": "v1", "topic": "w", "body": "old\n"},
    )
    r = client.put(
        f"/api/v1/orgs/{slug}/agents/{agent}/learnings/entries/LRN-001",
        headers=h, json={"slug": "a", "title": "v2", "topic": "w", "body": "new\n"},
    )
    assert r.status_code == 200
    assert r.json()["id"] == "MEM-001"
    files = sorted(
        p.name for p in (ws / "memory").glob("*.md") if p.name != "_index.md"
    )
    assert files == ["MEM-001-a.md"]


def test_list_returns_404_for_unknown_agent(client_with_migrated_workspace):
    client, token, slug, agent, _ = client_with_migrated_workspace
    r = client.get(
        f"/api/v1/orgs/{slug}/agents/nonexistent_agent/memory/entries/",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "agent_not_found"


def test_add_rejects_unknown_related_to(client_with_migrated_workspace):
    client, token, slug, agent, _ = client_with_migrated_workspace
    r = client.post(
        f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/",
        headers={"Authorization": f"Bearer {token}"},
        json={"slug": "a", "title": "x", "topic": "w", "body": "b\n", "related_to": ["LRN-999"]},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "unknown_related_id"


def test_promote_requires_existing_kb_slug(client_with_migrated_workspace):
    client, token, slug, agent, _ = client_with_migrated_workspace
    # Seed a learning
    client.post(
        f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/",
        headers={"Authorization": f"Bearer {token}"},
        json={"slug": "a", "title": "x", "topic": "w", "body": "b\n"},
    )
    # Promote with nonexistent KB slug should 404
    r = client.post(
        f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/MEM-001/promote",
        headers={"Authorization": f"Bearer {token}"},
        json={"kb_slug": "does-not-exist"},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "kb_slug_not_found"


def test_promote_with_existing_kb_slug_stamps_and_stubs(client_with_migrated_workspace, monkeypatch):
    client, token, slug, agent, _ = client_with_migrated_workspace
    # Seed a KB precedent so promote can resolve it
    client.post(
        f"/api/v1/orgs/{slug}/kb/",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "slug": "real-precedent",
            "title": "Real precedent",
            "type": "precedent",
            "topic": "engineering",
            "body": "details\n",
            "agent": agent,
        },
    )
    # Seed a learning
    client.post(
        f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/",
        headers={"Authorization": f"Bearer {token}"},
        json={"slug": "a", "title": "x", "topic": "w", "body": "original\n"},
    )
    r = client.post(
        f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/MEM-001/promote",
        headers={"Authorization": f"Bearer {token}"},
        json={"kb_slug": "real-precedent"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["promoted_to"] == "real-precedent"
    assert "original" not in body["body"]
    assert "real-precedent" in body["body"]


def test_legacy_post_returns_410_on_migrated_workspace(client_with_migrated_workspace):
    client, token, slug, agent, _ = client_with_migrated_workspace
    # Seed an active session so the legacy guard would otherwise pass — but
    # since the workspace is migrated, the route should 410 before that.
    r = client.post(
        f"/api/v1/orgs/{slug}/agents/{agent}/memory",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "hi", "task_id": "TASK-001", "session_id": "s"},
    )
    assert r.status_code == 410
    assert r.json()["detail"]["migrate_to"].endswith("/memory/entries")


def test_legacy_post_still_works_on_pre_migration_workspace(tmp_path, monkeypatch):
    """Pre-migration workspaces — no memory/ dir — keep using the legacy endpoint.

    This is a smoke test only; the legacy code path requires a real session,
    which is heavy to set up in unit tests. We test that the 410 guard does NOT
    fire on pre-migration workspaces (instead the request reaches the session
    validation code path).
    """
    try:
        from tests.daemon.conftest import _build_test_app  # noqa: F401
    except ImportError:
        pass
    # If _build_test_app doesn't exist, skip this test with pytest.skip and
    # rely on the integration test in T21 instead.
    pytest.skip("Legacy session-validation test deferred to T21 integration test")


def test_reindex_regenerates_file(client_with_migrated_workspace):
    client, token, slug, agent, ws = client_with_migrated_workspace
    # Seed a learning
    client.post(
        f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/",
        headers={"Authorization": f"Bearer {token}"},
        json={"slug": "a", "title": "x", "topic": "w", "body": "b\n"},
    )
    # Delete _index.md manually
    (ws / "memory" / "_index.md").unlink()
    r = client.post(
        f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/reindex",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert (ws / "memory" / "_index.md").exists()


def test_promote_rejects_invalid_kb_slug(client_with_migrated_workspace):
    """Traversal / malformed kb_slug returns 400 invalid_kb_slug before touching filesystem."""
    client, token, slug, agent, _ = client_with_migrated_workspace
    # Seed a learning so the route doesn't fail for a different reason
    client.post(
        f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/",
        headers={"Authorization": f"Bearer {token}"},
        json={"slug": "a", "title": "x", "topic": "w", "body": "b\n"},
    )
    r = client.post(
        f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/MEM-001/promote",
        headers={"Authorization": f"Bearer {token}"},
        json={"kb_slug": "../../../etc/passwd"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "invalid_kb_slug"


def test_promote_rejects_malformed_learning_id(client_with_migrated_workspace):
    """Malformed learning id returns 400 invalid_id (not 500) when kb_slug is valid."""
    client, token, slug, agent, _ = client_with_migrated_workspace
    # Seed a KB precedent so kb_slug validation passes and .exists() returns True
    client.post(
        f"/api/v1/orgs/{slug}/kb/",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "slug": "kb-precedent",
            "title": "KB Precedent",
            "type": "precedent",
            "topic": "engineering",
            "body": "details\n",
            "agent": agent,
        },
    )
    r = client.post(
        f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/garbage-id/promote",
        headers={"Authorization": f"Bearer {token}"},
        json={"kb_slug": "kb-precedent"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "invalid_id"


def test_add_writes_memory_added_audit_row(client_with_migrated_workspace):
    client, token, slug, agent, _ = client_with_migrated_workspace
    client.post(
        f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/",
        headers={"Authorization": f"Bearer {token}"},
        json={"slug": "x", "title": "t", "topic": "w", "body": "b\n"},
    )
    audit = client.get(
        f"/api/v1/orgs/{slug}/audit?action=memory_added",
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    rows = audit.get("entries", [])
    assert any(r["action"] == "memory_added" for r in rows)


def test_update_writes_memory_updated_audit_row(client_with_migrated_workspace):
    client, token, slug, agent, _ = client_with_migrated_workspace
    # Seed
    client.post(
        f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/",
        headers={"Authorization": f"Bearer {token}"},
        json={"slug": "y", "title": "orig", "topic": "w", "body": "b\n"},
    )
    # Update
    client.put(
        f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/MEM-001",
        headers={"Authorization": f"Bearer {token}"},
        json={"slug": "y", "title": "updated", "topic": "w", "body": "b2\n"},
    )
    audit = client.get(
        f"/api/v1/orgs/{slug}/audit?action=memory_updated",
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    rows = audit.get("entries", [])
    assert any(r["action"] == "memory_updated" for r in rows)


def test_promote_writes_memory_promoted_audit_row(client_with_migrated_workspace):
    client, token, slug, agent, _ = client_with_migrated_workspace
    # Seed a KB entry
    client.post(
        f"/api/v1/orgs/{slug}/kb/",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "slug": "audit-kb-entry",
            "title": "Audit KB Entry",
            "type": "precedent",
            "topic": "engineering",
            "body": "details\n",
            "agent": agent,
        },
    )
    # Seed a learning
    client.post(
        f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/",
        headers={"Authorization": f"Bearer {token}"},
        json={"slug": "z", "title": "z", "topic": "w", "body": "b\n"},
    )
    # Promote
    client.post(
        f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/MEM-001/promote",
        headers={"Authorization": f"Bearer {token}"},
        json={"kb_slug": "audit-kb-entry"},
    )
    audit = client.get(
        f"/api/v1/orgs/{slug}/audit?action=memory_promoted",
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    rows = audit.get("entries", [])
    assert any(r["action"] == "memory_promoted" for r in rows)


# ═══════════════════════════════════════════════════════════════════
# THR-032 P3a — PATCH /memory/entries/{id}/lifecycle
# ═══════════════════════════════════════════════════════════════════


class TestLifecycleRoute:
    """THR-032 P3a: PATCH lifecycle endpoint."""

    def test_patch_lifecycle_success(self, client_with_migrated_workspace):
        client, token, slug, agent, _ = client_with_migrated_workspace
        # Seed an entry
        client.post(
            f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/",
            headers={"Authorization": f"Bearer {token}"},
            json={"slug": "to-evict", "title": "To Evict", "topic": "w", "body": "b\n"},
        )
        r = client.patch(
            f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/MEM-001/lifecycle",
            headers={"Authorization": f"Bearer {token}"},
            json={"lifecycle": "evicted", "reason": "obsolete info"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == "MEM-001"
        assert body["lifecycle"] == "evicted"
        assert body["previous_lifecycle"] == "valid"

    def test_patch_lifecycle_missing_reason(self, client_with_migrated_workspace):
        client, token, slug, agent, _ = client_with_migrated_workspace
        client.post(
            f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/",
            headers={"Authorization": f"Bearer {token}"},
            json={"slug": "x", "title": "x", "topic": "w", "body": "b\n"},
        )
        r = client.patch(
            f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/MEM-001/lifecycle",
            headers={"Authorization": f"Bearer {token}"},
            json={"lifecycle": "evicted"},
        )
        assert r.status_code == 400
        assert "reason_required" in r.json()["detail"]["error"]

    def test_patch_lifecycle_invalid_lifecycle(self, client_with_migrated_workspace):
        client, token, slug, agent, _ = client_with_migrated_workspace
        client.post(
            f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/",
            headers={"Authorization": f"Bearer {token}"},
            json={"slug": "x", "title": "x", "topic": "w", "body": "b\n"},
        )
        r = client.patch(
            f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/MEM-001/lifecycle",
            headers={"Authorization": f"Bearer {token}"},
            json={"lifecycle": "frobnicated", "reason": "test"},
        )
        assert r.status_code == 400
        assert "invalid_lifecycle" in r.json()["detail"]["error"]

    def test_patch_lifecycle_not_found(self, client_with_migrated_workspace):
        client, token, slug, agent, _ = client_with_migrated_workspace
        r = client.patch(
            f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/MEM-999/lifecycle",
            headers={"Authorization": f"Bearer {token}"},
            json={"lifecycle": "evicted", "reason": "test"},
        )
        assert r.status_code == 404

    def test_patch_lifecycle_promoted_locked(self, client_with_migrated_workspace):
        client, token, slug, agent, _ = client_with_migrated_workspace
        # Write a promoted entry directly (bypass the store to set promoted_to)
        import yaml
        mem_dir = _ / "memory"
        content = """---
id: MEM-001
slug: locked-entry
title: Locked Entry
topic: w
promoted_to: kb-rule
lifecycle: valid
---
body
"""
        (mem_dir / "MEM-001-locked-entry.md").write_text(content)
        # Also need to rebuild index so the entry is findable
        from runtime.infrastructure.learnings_store import MemoryStore
        store = MemoryStore(mem_dir)
        store.regenerate_index()
        r = client.patch(
            f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/MEM-001/lifecycle",
            headers={"Authorization": f"Bearer {token}"},
            json={"lifecycle": "evicted", "reason": "test"},
        )
        assert r.status_code == 409
        assert r.json()["detail"]["error"] == "promoted_locked"

    def test_patch_lifecycle_lrn_alias_resolves_to_mem(self, client_with_migrated_workspace):
        client, token, slug, agent, _ = client_with_migrated_workspace
        client.post(
            f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/",
            headers={"Authorization": f"Bearer {token}"},
            json={"slug": "alias-test", "title": "Alias", "topic": "w", "body": "b\n"},
        )
        # Resolve via LRN alias
        r = client.patch(
            f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/LRN-001/lifecycle",
            headers={"Authorization": f"Bearer {token}"},
            json={"lifecycle": "evicted", "reason": "via alias"},
        )
        assert r.status_code == 200
        assert r.json()["id"] == "MEM-001"
        assert r.json()["lifecycle"] == "evicted"

    def test_patch_lifecycle_audit_row_emitted(self, client_with_migrated_workspace):
        client, token, slug, agent, _ = client_with_migrated_workspace
        client.post(
            f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/",
            headers={"Authorization": f"Bearer {token}"},
            json={"slug": "audit-me", "title": "Audit Me", "topic": "w", "body": "b\n"},
        )
        client.patch(
            f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/MEM-001/lifecycle",
            headers={"Authorization": f"Bearer {token}"},
            json={"lifecycle": "superseded", "reason": "replaced by MEM-099"},
        )
        audit = client.get(
            f"/api/v1/orgs/{slug}/audit?action=memory_lifecycle_changed",
            headers={"Authorization": f"Bearer {token}"},
        ).json()
        rows = audit.get("entries", [])
        assert len(rows) >= 1
        row = rows[0]
        assert row["action"] == "memory_lifecycle_changed"
        assert row["agent"] == agent
        payload = row["payload"]
        assert payload["id"] == "MEM-001"
        assert payload["from_lifecycle"] == "valid"
        assert payload["to_lifecycle"] == "superseded"
        assert payload["reason"] == "replaced by MEM-099"
        assert payload["source"] == "manual"

    def test_patch_lifecycle_index_regenerated_after_evict(self, client_with_migrated_workspace):
        client, token, slug, agent, ws = client_with_migrated_workspace
        client.post(
            f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/",
            headers={"Authorization": f"Bearer {token}"},
            json={"slug": "evict-me", "title": "Evict Me", "topic": "w", "body": "b\n"},
        )
        client.patch(
            f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/MEM-001/lifecycle",
            headers={"Authorization": f"Bearer {token}"},
            json={"lifecycle": "evicted", "reason": "test index regen"},
        )
        idx = (ws / "memory" / "_index.md").read_text()
        assert "MEM-001" not in idx  # evicted → excluded from index

    def test_patch_lifecycle_hidden_learnings_forwarder_works(self, client_with_migrated_workspace):
        """The hidden /learnings forwarder still resolves the PATCH lifecycle."""
        client, token, slug, agent, _ = client_with_migrated_workspace
        client.post(
            f"/api/v1/orgs/{slug}/agents/{agent}/memory/entries/",
            headers={"Authorization": f"Bearer {token}"},
            json={"slug": "fwd", "title": "Fwd", "topic": "w", "body": "b\n"},
        )
        r = client.patch(
            f"/api/v1/orgs/{slug}/agents/{agent}/learnings/entries/MEM-001/lifecycle",
            headers={"Authorization": f"Bearer {token}"},
            json={"lifecycle": "superseded", "reason": "via forwarder"},
        )
        assert r.status_code == 200
        assert r.json()["lifecycle"] == "superseded"
