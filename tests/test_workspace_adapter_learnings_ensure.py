from __future__ import annotations

from pathlib import Path

from runtime.config import Settings
from runtime.orchestrator.workspace_adapters import PersistentWorkspaceSetup


def _setup(tmp_path: Path) -> tuple[PersistentWorkspaceSetup, Path]:
    settings = Settings()
    ws = tmp_path / "workspaces" / "test_agent"
    return PersistentWorkspaceSetup(settings), ws


def test_ensure_brand_new_workspace_creates_memory_dir(tmp_path: Path):
    setup, ws = _setup(tmp_path)
    setup.ensure(ws, "test_agent")
    assert (ws / "memory").is_dir()
    assert (ws / "memory" / "_index.md").exists()
    assert not (ws / "learnings").exists()
    assert not (ws / "learnings.md").exists()


def test_ensure_legacy_workspace_with_flat_file_does_not_create_memory_dir(tmp_path: Path):
    setup, ws = _setup(tmp_path)
    ws.mkdir(parents=True)
    (ws / "learnings.md").write_text("# Learnings: test_agent\n\n- existing entry\n")
    setup.ensure(ws, "test_agent")
    # Flat-file legacy workspace is left untouched — no structured store yet.
    assert not (ws / "memory").exists()
    assert not (ws / "learnings").exists()
    assert (ws / "learnings.md").exists()


def test_ensure_migrates_legacy_learnings_dir_to_memory(tmp_path: Path):
    """THR-032 Phase R: a legacy learnings/ dir is moved to memory/ on setup."""
    setup, ws = _setup(tmp_path)
    learnings_dir = ws / "learnings"
    learnings_dir.mkdir(parents=True)
    (learnings_dir / "LRN-001-x.md").write_text(
        "---\nid: LRN-001\nslug: x\ntitle: X\ntopic: t\n---\n\nbody\n"
    )
    setup.ensure(ws, "test_agent")
    assert not (ws / "learnings").exists()  # moved
    assert (ws / "memory" / "MEM-001-x.md").exists()
    assert (ws / "memory" / "_index.md").exists()


def test_ensure_memory_workspace_regenerates_index_if_missing(tmp_path: Path):
    setup, ws = _setup(tmp_path)
    memory_dir = ws / "memory"
    memory_dir.mkdir(parents=True)
    # No _index.md initially
    setup.ensure(ws, "test_agent")
    assert (memory_dir / "_index.md").exists()


def test_ensure_legacy_with_only_header_still_does_not_create_memory_dir(tmp_path: Path):
    """Even a placeholder-only learnings.md counts as 'has flat file' — the
    operator decides when migration runs. Safer to wait for the explicit task."""
    setup, ws = _setup(tmp_path)
    ws.mkdir(parents=True)
    (ws / "learnings.md").write_text("# Learnings: test_agent\n\n")
    setup.ensure(ws, "test_agent")
    assert not (ws / "memory").exists()
