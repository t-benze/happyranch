from __future__ import annotations

from pathlib import Path

from runtime.config import Settings
from runtime.orchestrator.workspace_adapters import PersistentWorkspaceSetup


def _setup(tmp_path: Path) -> tuple[PersistentWorkspaceSetup, Path]:
    settings = Settings()
    ws = tmp_path / "workspaces" / "test_agent"
    return PersistentWorkspaceSetup(settings), ws


def test_ensure_brand_new_workspace_creates_learnings_dir(tmp_path: Path):
    setup, ws = _setup(tmp_path)
    setup.ensure(ws, "test_agent")
    assert (ws / "learnings").is_dir()
    assert (ws / "learnings" / "_index.md").exists()
    assert not (ws / "learnings.md").exists()


def test_ensure_legacy_workspace_with_flat_file_does_not_create_learnings_dir(tmp_path: Path):
    setup, ws = _setup(tmp_path)
    ws.mkdir(parents=True)
    (ws / "learnings.md").write_text("# Learnings: test_agent\n\n- existing entry\n")
    setup.ensure(ws, "test_agent")
    assert not (ws / "learnings").exists()
    assert (ws / "learnings.md").exists()


def test_ensure_migrated_workspace_regenerates_index_if_missing(tmp_path: Path):
    setup, ws = _setup(tmp_path)
    learnings_dir = ws / "learnings"
    learnings_dir.mkdir(parents=True)
    # No _index.md initially
    setup.ensure(ws, "test_agent")
    assert (learnings_dir / "_index.md").exists()


def test_ensure_legacy_with_only_header_still_does_not_create_learnings_dir(tmp_path: Path):
    """Even a placeholder-only learnings.md counts as 'has flat file' — the
    operator decides when migration runs. Safer to wait for the explicit task."""
    setup, ws = _setup(tmp_path)
    ws.mkdir(parents=True)
    (ws / "learnings.md").write_text("# Learnings: test_agent\n\n")
    setup.ensure(ws, "test_agent")
    assert not (ws / "learnings").exists()
