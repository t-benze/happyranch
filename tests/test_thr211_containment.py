from __future__ import annotations

from pathlib import Path

import pytest

from tests.thr211_containment import plan_environment, prepare_private_test_paths


def test_private_plan_paths_are_fresh_private_and_explicit(tmp_path: Path) -> None:
    paths = prepare_private_test_paths(tmp_path / "candidate")

    assert paths.plans.is_dir()
    assert paths.artifacts.is_dir()
    assert plan_environment(paths) == {"HAPPYRANCH_TEST_PLAN_DIR": str(paths.plans)}
    assert paths.plans.stat().st_mode & 0o077 == 0


def test_private_plan_paths_refuse_existing_root(tmp_path: Path) -> None:
    root = tmp_path / "candidate"
    root.mkdir()

    with pytest.raises(FileExistsError):
        prepare_private_test_paths(root)
