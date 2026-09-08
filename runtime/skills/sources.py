"""Resolve release-owned skill sources without a checkout or docs dependency."""

from __future__ import annotations

from pathlib import Path


def bundled_skills_dir(package_root: Path | None = None) -> Path:
    """Return the sole bundled source; missing members fail at materialization.

    An explicitly configured package root is authoritative, including in tests.
    Never fall back to another release when its sources are missing or corrupt.
    The default works for both a checkout and an unpacked installed wheel.
    """
    if package_root is None:
        return Path(__file__).resolve().parent / "bundled"
    return package_root / "runtime" / "skills" / "bundled"
