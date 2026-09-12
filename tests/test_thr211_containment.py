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


def test_private_plan_paths_refuse_symlink_ancestor_without_touching_target(tmp_path: Path) -> None:
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(foreign, target_is_directory=True)

    with pytest.raises(OSError):
        prepare_private_test_paths(alias / "candidate")

    assert list(foreign.iterdir()) == []


def test_private_plan_paths_refuse_nested_symlink_ancestor_without_touching_target(tmp_path: Path) -> None:
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "alias").symlink_to(foreign, target_is_directory=True)

    with pytest.raises(OSError):
        prepare_private_test_paths(nested / "alias" / "candidate")

    assert list(foreign.iterdir()) == []


def test_private_plan_paths_refuse_non_directory_ancestor(tmp_path: Path) -> None:
    ancestor = tmp_path / "not-a-directory"
    ancestor.write_text("foreign")

    with pytest.raises(NotADirectoryError):
        prepare_private_test_paths(ancestor / "candidate")
