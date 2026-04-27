from __future__ import annotations

from pathlib import Path

import pytest

from src.runtime import RuntimeDir


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def test_init_creates_marker_file(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "runtime", slug="test")
    assert rt.marker_file.exists()


def test_init_creates_workspaces_dir(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "runtime", slug="test")
    assert rt.workspaces_dir.is_dir()


def test_init_idempotent(tmp_path: Path) -> None:
    """Calling init twice must not destroy existing data."""
    rt_dir = tmp_path / "runtime"
    rt1 = RuntimeDir.init(rt_dir, slug="test")

    # Place a sentinel file inside workspaces to verify it survives.
    sentinel = rt1.workspaces_dir / "sentinel.txt"
    sentinel.write_text("keep me")

    rt2 = RuntimeDir.init(rt_dir, slug="test")

    assert rt2.marker_file.exists()
    assert rt2.workspaces_dir.is_dir()
    assert sentinel.exists(), "Existing workspace data was destroyed by a second init"


# ---------------------------------------------------------------------------
# load
# ---------------------------------------------------------------------------


def test_load_valid_runtime(tmp_path: Path) -> None:
    rt_dir = tmp_path / "runtime"
    RuntimeDir.init(rt_dir, slug="test")

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


def test_init_seeds_empty_teams_yaml(tmp_path: Path) -> None:
    import yaml
    rt = RuntimeDir.init(tmp_path / "rt", slug="test")
    assert rt.teams_config_path.exists()
    data = yaml.safe_load(rt.teams_config_path.read_text())
    assert data == {"teams": {}}


def test_init_does_not_overwrite_existing_teams_yaml(tmp_path: Path) -> None:
    import yaml
    rt = RuntimeDir.init(tmp_path / "rt", slug="test")
    rt.teams_config_path.write_text(
        "teams:\n  custom:\n    manager: custom_mgr\n    workers: []\n"
    )
    RuntimeDir.init(tmp_path / "rt", slug="test")
    data = yaml.safe_load(rt.teams_config_path.read_text())
    assert set(data["teams"].keys()) == {"custom"}


# ---------------------------------------------------------------------------
# slug + org folder
# ---------------------------------------------------------------------------


def test_init_writes_slug_to_opc_yaml(tmp_path: Path) -> None:
    import yaml
    rt = RuntimeDir.init(tmp_path / "rt", slug="hk-tourism")
    data = yaml.safe_load(rt.marker_file.read_text())
    assert data["slug"] == "hk-tourism"
    assert data["schema_version"] == 1
    assert "created_at" in data


def test_slug_property_reads_opc_yaml(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="hk-tourism")
    loaded = RuntimeDir.load(rt.root)
    assert loaded.slug == "hk-tourism"


def test_init_creates_org_skeleton(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    assert rt.org_dir.is_dir()
    assert rt.agents_dir.is_dir()
    assert rt.pending_agents_dir.is_dir()


def test_teams_config_path_under_org(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt", slug="x")
    assert rt.teams_config_path == rt.root / "org" / "teams.yaml"


def test_init_idempotent_keeps_slug(tmp_path: Path) -> None:
    rt1 = RuntimeDir.init(tmp_path / "rt", slug="alpha")
    rt2 = RuntimeDir.init(tmp_path / "rt", slug="beta")  # second call ignored
    assert rt2.slug == "alpha"
