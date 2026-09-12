"""Test-only filesystem preparation for the THR-211 Jenkins candidate.

This module deliberately creates no process ownership abstraction.  A private
directory can contain a test plan; it cannot identify, reap, or contain a
daemon or an escaped descendant.
"""
from __future__ import annotations

import os
import stat
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
    parent = root.parent
    # The caller supplies a canonical absolute private boundary (on macOS this
    # means the resolved temporary-directory path, never a /var -> /private/var
    # alias).  Walk every component from its filesystem anchor with O_NOFOLLOW,
    # then create children through pinned descriptors.  This detects existing
    # symlink/non-directory ancestry; it is not hostile same-UID race isolation.
    if root.name in {"", ".", ".."}:
        raise ValueError("private test root needs a simple child name")
    if not parent.is_absolute():
        raise ValueError("private test root must be absolute")
    parent_fd = os.open(parent.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in parent.parts[1:]:
            next_fd = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
            os.close(parent_fd)
            parent_fd = next_fd
        try:
            os.mkdir(root.name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            raise FileExistsError(f"private test root already exists: {root}") from None
        root_fd = os.open(root.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)
    try:
        for child in ("plans", "artifacts"):
            os.mkdir(child, 0o700, dir_fd=root_fd)
            child_fd = os.open(child, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            try:
                child_stat = os.fstat(child_fd)
                if not stat.S_ISDIR(child_stat.st_mode) or child_stat.st_mode & 0o077:
                    raise RuntimeError(f"unsafe private test path: {root / child}")
            finally:
                os.close(child_fd)
    finally:
        os.close(root_fd)
    plans = root / "plans"
    artifacts = root / "artifacts"
    return PrivateTestPaths(root=root, plans=plans, artifacts=artifacts)


def plan_environment(paths: PrivateTestPaths) -> dict[str, str]:
    """Return the narrow plan-path contract; no ambient TMPDIR inference."""
    return {"HAPPYRANCH_TEST_PLAN_DIR": os.fspath(paths.plans)}
