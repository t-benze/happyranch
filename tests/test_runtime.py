from __future__ import annotations

from pathlib import Path

import pytest

from src.runtime import RuntimeDir


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def test_init_creates_marker_file(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "runtime")
    assert rt.marker_file.exists()


def test_init_creates_workspaces_dir(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "runtime")
    assert rt.workspaces_dir.is_dir()


def test_init_idempotent(tmp_path: Path) -> None:
    """Calling init twice must not destroy existing data."""
    rt_dir = tmp_path / "runtime"
    rt1 = RuntimeDir.init(rt_dir)

    # Place a sentinel file inside workspaces to verify it survives.
    sentinel = rt1.workspaces_dir / "sentinel.txt"
    sentinel.write_text("keep me")

    rt2 = RuntimeDir.init(rt_dir)

    assert rt2.marker_file.exists()
    assert rt2.workspaces_dir.is_dir()
    assert sentinel.exists(), "Existing workspace data was destroyed by a second init"


# ---------------------------------------------------------------------------
# load
# ---------------------------------------------------------------------------


def test_load_valid_runtime(tmp_path: Path) -> None:
    rt_dir = tmp_path / "runtime"
    RuntimeDir.init(rt_dir)

    loaded = RuntimeDir.load(rt_dir)
    assert loaded.root == rt_dir.resolve()
    assert loaded.is_valid()


def test_load_invalid_runtime_raises(tmp_path: Path) -> None:
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    with pytest.raises(ValueError, match="not a valid OPC runtime directory"):
        RuntimeDir.load(empty_dir)


# ---------------------------------------------------------------------------
# Derived paths
# ---------------------------------------------------------------------------


def test_db_path_derived_from_root(tmp_path: Path) -> None:
    rt = RuntimeDir(tmp_path)
    assert rt.db_path == tmp_path.resolve() / "opc.db"


def test_workspaces_dir_derived_from_root(tmp_path: Path) -> None:
    rt = RuntimeDir(tmp_path)
    assert rt.workspaces_dir == tmp_path.resolve() / "workspaces"


# ---------------------------------------------------------------------------
# teams.yaml seeding
# ---------------------------------------------------------------------------


def test_init_seeds_default_teams_yaml(tmp_path: Path) -> None:
    import yaml
    rt = RuntimeDir.init(tmp_path / "rt")
    assert rt.teams_config_path.exists()
    data = yaml.safe_load(rt.teams_config_path.read_text())
    assert set(data["teams"].keys()) == {"engineering", "content"}
    assert data["teams"]["engineering"]["manager"] == "engineering_head"
    assert data["teams"]["content"]["manager"] == "content_manager"


def test_init_does_not_overwrite_existing_teams_yaml(tmp_path: Path) -> None:
    import yaml
    rt = RuntimeDir.init(tmp_path / "rt")
    rt.teams_config_path.write_text(
        "teams:\n  custom:\n    manager: custom_mgr\n    workers: []\n"
    )
    # Second init must not overwrite an existing teams.yaml.
    RuntimeDir.init(tmp_path / "rt")
    data = yaml.safe_load(rt.teams_config_path.read_text())
    assert "custom" in data["teams"]
