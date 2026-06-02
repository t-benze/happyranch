from __future__ import annotations

from pathlib import Path

from runtime.orchestrator._paths import OrgPaths


def test_artifacts_dir_is_under_root(tmp_path: Path) -> None:
    paths = OrgPaths(root=tmp_path)
    assert paths.artifacts_dir == tmp_path / "artifacts"
