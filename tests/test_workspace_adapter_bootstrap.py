from __future__ import annotations

from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.workspace_adapters import ClaudeWorkspaceAdapter


@pytest.fixture
def adapter(tmp_path: Path) -> tuple[ClaudeWorkspaceAdapter, Path]:
    org_root = tmp_path / "orgs" / "test-org"
    org_root.mkdir(parents=True)
    paths = OrgPaths(org_root)
    adapter = ClaudeWorkspaceAdapter(Settings(), paths, slug="test-org")
    ws = org_root / "workspaces" / "agent_x"
    ws.mkdir(parents=True)
    return adapter, ws


def test_bootstrap_inlines_legacy_learnings_md(adapter, tmp_path: Path):
    a, ws = adapter
    (ws / "learnings.md").write_text("# Learnings: agent_x\n\n- legacy entry\n")
    a.write_claude_md(ws, agent_name="agent_x", system_prompt="prompt")
    body = (ws / "CLAUDE.md").read_text()
    assert "legacy entry" in body
    assert "_index.md" not in body  # not migrated yet


def test_bootstrap_inlines_index_after_migration(adapter, tmp_path: Path):
    a, ws = adapter
    (ws / "learnings").mkdir()
    (ws / "learnings" / "_index.md").write_text("# Learnings Index\n\n## workflow (1)\n\n- `LRN-001` — sample\n")
    a.write_claude_md(ws, agent_name="agent_x", system_prompt="prompt")
    body = (ws / "CLAUDE.md").read_text()
    assert "LRN-001" in body
    assert "sample" in body
    assert "happyranch learning get" in body  # references new CLI
