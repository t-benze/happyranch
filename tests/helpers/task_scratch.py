"""Scratch observations shared by the retained containment integration cases."""
from __future__ import annotations

import os
import stat
from pathlib import Path


def _proc(tmp_path: Path) -> Path:
    proc = tmp_path / "proc"
    (proc / "sys/kernel/random").mkdir(parents=True)
    (proc / "sys/kernel/random/boot_id").write_text("123e4567-e89b-42d3-a456-426614174000")
    item = proc / "42"
    (item / "fd").mkdir(parents=True)
    (item / "stat").write_text("42 (agent) S 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 9 0")
    (item / "root").symlink_to("/")
    (item / "cwd").symlink_to("/")
    return proc


def _snapshot(workspace):
    """Bytes and stable identities, including protected parents and siblings."""
    result = {}
    for path in [workspace, *workspace.rglob("*")]:
        info = path.lstat()
        value = (os.readlink(path) if path.is_symlink() else
                 path.read_bytes() if stat.S_ISREG(info.st_mode) else None)
        result[str(path.relative_to(workspace))] = (
            info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, value)
    return result
