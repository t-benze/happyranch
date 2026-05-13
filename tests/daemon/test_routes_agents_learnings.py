"""Tests for GET /agents/{agent}/learnings/entries/* routes + 412 guard."""
from __future__ import annotations

from fastapi.testclient import TestClient


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
