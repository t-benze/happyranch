from __future__ import annotations

from pathlib import Path

from src.orchestrator._paths import OrgPaths


def test_assets_dir_is_under_root(tmp_path: Path) -> None:
    paths = OrgPaths(root=tmp_path)
    assert paths.assets_dir == tmp_path / "assets"
