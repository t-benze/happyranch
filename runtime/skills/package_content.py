"""Canonical skill content excludes only generated Python bytecode artifacts."""

from __future__ import annotations

import hashlib
from pathlib import Path


def is_bytecode_artifact(rel_path: str | Path) -> bool:
    """Ignore __pycache__ components and exact .pyc/.pyo suffixes only."""
    path = Path(rel_path)
    return "__pycache__" in path.parts or path.name.endswith((".pyc", ".pyo"))


def compute_skill_tree_hash(root: Path) -> str:
    """Hash sorted relative names and bytes using the original NUL framing.

    Trees without bytecode keep their exact pre-filter identity. Source
    selection and canonical verification must use this same definition.
    """
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if not is_bytecode_artifact(rel) and path.is_file():
            digest.update(str(rel).encode())
            digest.update(b"\x00")
            digest.update(path.read_bytes())
            digest.update(b"\x00")
    return digest.hexdigest()
