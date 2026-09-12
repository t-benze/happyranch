"""Test-only filesystem preparation for the THR-211 Jenkins candidate.

This module deliberately creates no process ownership abstraction.  A private
directory can contain a test plan; it cannot identify, reap, or contain a
daemon or an escaped descendant.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PrivateTestPaths:
    """Fresh, private locations passed to fake plans as data."""

    root: Path
    plans: Path
    artifacts: Path


def prepare_private_test_paths(root: Path) -> PrivateTestPaths:
    """Create no-follow private plan/artifact directories below ``root``.

    Existing paths, symlinks, and non-directories fail closed.  The caller
    owns ``root`` (pytest's ``tmp_path`` or Pipeline's fresh workspace).
    """
    if root.exists() or root.is_symlink():
        raise FileExistsError(f"private test root already exists: {root}")
    root.mkdir(mode=0o700, parents=True)
    plans = root / "plans"
    artifacts = root / "artifacts"
    plans.mkdir(mode=0o700)
    artifacts.mkdir(mode=0o700)
    for path in (root, plans, artifacts):
        stat = path.stat(follow_symlinks=False)
        if not path.is_dir() or path.is_symlink() or stat.st_mode & 0o077:
            raise RuntimeError(f"unsafe private test path: {path}")
    return PrivateTestPaths(root=root, plans=plans, artifacts=artifacts)


def plan_environment(paths: PrivateTestPaths) -> dict[str, str]:
    """Return the narrow plan-path contract; no ambient TMPDIR inference."""
    return {"HAPPYRANCH_TEST_PLAN_DIR": os.fspath(paths.plans)}
