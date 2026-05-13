"""Tests for GET /agents/{agent}/learnings/entries/* routes + 412 guard."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.daemon import paths as paths_mod
from src.daemon.app import create_app


@pytest.fixture
def client_with_migrated_workspace(tmp_home, daemon_state):
    """TestClient + a pre-migrated workspace with learnings/ dir.

    Yields (client, token, slug, agent_name, workspace_path).
    """
    slug = "alpha"
    agent = "dev_agent"
    org = daemon_state.orgs[slug]
    ws = org.root / "workspaces" / agent
    (ws / "learnings").mkdir(parents=True, exist_ok=True)

    app = create_app(daemon_state)
    client = TestClient(app)
    token = paths_mod.read_token()
    yield client, token, slug, agent, ws


def test_list_returns_empty_on_migrated_workspace(tmp_home, app, org_state, auth_headers):
    """learnings/ dir exists (migrated) → 200 with empty entries list."""
    workspace = org_state.root / "workspaces" / "dev_agent"
    (workspace / "learnings").mkdir(parents=True, exist_ok=True)
    r = TestClient(app).get(
        "/api/v1/orgs/alpha/agents/dev_agent/learnings/entries/",
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json() == {"entries": []}


def test_list_returns_412_on_pre_migration_workspace(tmp_home, app, org_state, auth_headers):
    """No learnings/ dir but flat learnings.md exists → 412 workspace_not_migrated."""
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "learnings.md").write_text("# Learnings\n")
    # learnings/ dir must NOT exist
    assert not (workspace / "learnings").exists()
    r = TestClient(app).get(
        "/api/v1/orgs/alpha/agents/dev_agent/learnings/entries/",
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
        f"/api/v1/orgs/{slug}/agents/{agent}/learnings/entries/",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"] == "LRN-001"
    assert body["path"] == "learnings/LRN-001-first-rule.md"
    assert (ws / "learnings" / "LRN-001-first-rule.md").exists()


def test_update_preserves_authored_at(client_with_migrated_workspace):
    client, token, slug, agent, ws = client_with_migrated_workspace
    # Seed
    client.post(
        f"/api/v1/orgs/{slug}/agents/{agent}/learnings/entries/",
        headers={"Authorization": f"Bearer {token}"},
        json={"slug": "a", "title": "v1", "topic": "w", "body": "old\n"},
    )
    # Update
    r = client.put(
        f"/api/v1/orgs/{slug}/agents/{agent}/learnings/entries/LRN-001",
        headers={"Authorization": f"Bearer {token}"},
        json={"slug": "a", "title": "v2", "topic": "w", "body": "new\n"},
    )
    assert r.status_code == 200
    assert r.json()["title"] == "v2"


def test_add_rejects_unknown_related_to(client_with_migrated_workspace):
    client, token, slug, agent, _ = client_with_migrated_workspace
    r = client.post(
        f"/api/v1/orgs/{slug}/agents/{agent}/learnings/entries/",
        headers={"Authorization": f"Bearer {token}"},
        json={"slug": "a", "title": "x", "topic": "w", "body": "b\n", "related_to": ["LRN-999"]},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "unknown_related_id"
