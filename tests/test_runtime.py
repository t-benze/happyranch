from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.runtime import RuntimeDir


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def test_init_creates_marker_file(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "runtime")
    assert rt.marker_file.exists()


def test_init_creates_orgs_dir(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "runtime")
    assert rt.orgs_dir.is_dir()


def test_init_idempotent(tmp_path: Path) -> None:
    """Calling init twice must not destroy existing data."""
    rt_dir = tmp_path / "runtime"
    rt1 = RuntimeDir.init(rt_dir)

    # Place a sentinel file inside orgs to verify it survives.
    sentinel = rt1.orgs_dir / "sentinel.txt"
    sentinel.write_text("keep me")

    rt2 = RuntimeDir.init(rt_dir)

    assert rt2.marker_file.exists()
    assert rt2.orgs_dir.is_dir()
    assert sentinel.exists(), "Existing org data was destroyed by a second init"


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
# v2 multi-org runtime
# ---------------------------------------------------------------------------


def test_init_writes_v2_marker_without_slug(tmp_path: Path) -> None:
    """Fresh runtime gets schema_version 2 and no slug at the runtime level."""
    rt = RuntimeDir.init(tmp_path / "rt")
    data = yaml.safe_load(rt.marker_file.read_text())
    assert data["schema_version"] == 2
    assert data["type"] == "multi-org-runtime"
    assert "slug" not in data
    assert (rt.root / "orgs").is_dir()


def test_load_refuses_schema_v1(tmp_path: Path) -> None:
    """A v1 marker (with slug) is rejected with a clear migration message."""
    root = tmp_path / "legacy"
    root.mkdir()
    (root / "opc.yaml").write_text(
        "slug: hk-tourism\nschema_version: 1\ncreated_at: 2026-04-01T00:00:00Z\n"
    )
    with pytest.raises(ValueError, match="migrate-to-multi-org"):
        RuntimeDir.load(root)


def test_iter_org_roots_returns_subdirs(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    (rt.orgs_dir / "alpha").mkdir()
    (rt.orgs_dir / "alpha" / "org").mkdir()
    (rt.orgs_dir / "alpha" / "org" / "teams.yaml").write_text("teams: {}\n")
    (rt.orgs_dir / "beta").mkdir()
    (rt.orgs_dir / "beta" / "org").mkdir()
    (rt.orgs_dir / "beta" / "org" / "teams.yaml").write_text("teams: {}\n")
    (rt.orgs_dir / "_pending").mkdir()  # reserved name, must be skipped

    slugs = sorted(slug for slug, _ in rt.iter_org_roots())
    assert slugs == ["alpha", "beta"]


def test_iter_org_roots_skips_invalid_slug_dirs(tmp_path: Path) -> None:
    """Directories whose names fail _SLUG_RE are silently skipped."""
    rt = RuntimeDir.init(tmp_path / "rt")
    (rt.orgs_dir / "alpha").mkdir()
    (rt.orgs_dir / "alpha" / "org").mkdir()
    (rt.orgs_dir / "alpha" / "org" / "teams.yaml").write_text("teams: {}\n")
    (rt.orgs_dir / "Bad-Name").mkdir()
    (rt.orgs_dir / "Bad-Name" / "org").mkdir()
    (rt.orgs_dir / "Bad-Name" / "org" / "teams.yaml").write_text("teams: {}\n")

    slugs = sorted(slug for slug, _ in rt.iter_org_roots())
    assert slugs == ["alpha"]


def test_iter_org_roots_skips_dirs_without_teams_yaml(tmp_path: Path) -> None:
    """Directories that lack org/teams.yaml are silently skipped."""
    rt = RuntimeDir.init(tmp_path / "rt")
    (rt.orgs_dir / "alpha").mkdir()
    (rt.orgs_dir / "alpha" / "org").mkdir()
    (rt.orgs_dir / "alpha" / "org" / "teams.yaml").write_text("teams: {}\n")
    (rt.orgs_dir / "beta").mkdir()

    slugs = sorted(slug for slug, _ in rt.iter_org_roots())
    assert slugs == ["alpha"]
