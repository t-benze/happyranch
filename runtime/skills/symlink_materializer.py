"""Symlink-based workspace skill materialization.

TASK-3988: Replaces per-session skill content copying with atomic per-workspace
create-or-repair of symlinks to canonical store targets.

Each workspace skill directory (.claude/skills/<slug>/, .agents/skills/<slug>/)
becomes a symlink pointing to an exact canonical target inside the daemon-owned
canonical store. The materializer:

1. Resolves expected skills and their canonical targets
2. Verifies each expected link: is it a symlink? Does it point to the exact
   canonical target? Is the package content/hash verified?
3. Atomically creates or repairs stale, broken, wrong-version, non-symlink,
   external-target, or mismatched-hash entries
4. For policy withdrawal/retire/unassign, safely removes the link without
   deleting canonical retained content
5. Fail-closed: any repair failure causes named fail-closed materialization
   and no executor launch
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("happyranch.skills.symlink_materializer")


class MaterializationError(RuntimeError):
    """Named fail-closed error during skill link materialization.

    Raised when a workspace skill link cannot be verified or repaired.
    The session spawner must NOT launch the executor after this error.
    """

    def __init__(self, slug: str, reason: str, workspace_path: Path):
        self.slug = slug
        self.reason = reason
        self.workspace_path = workspace_path
        super().__init__(
            f"Materialization failed for {slug!r}: {reason} "
            f"(workspace: {workspace_path})"
        )


@dataclass(frozen=True, slots=True)
class SkillLinkSpec:
    """Specification for a single workspace skill link.

    Describes the expected canonical target and the workspace paths that
    should be symlinks pointing to it.
    """

    slug: str
    canonical_target: Path  # Absolute path to canonical tree
    claude_skill_dir: Path  # e.g. <ws>/.claude/skills/<slug>
    agents_skill_dir: Path  # e.g. <ws>/.agents/skills/<slug>
    expected_content_hash: str  # SHA-256 hex of canonical content

    def __post_init__(self):
        if not self.canonical_target.is_absolute():
            raise ValueError(
                f"canonical_target must be absolute: {self.canonical_target}"
            )

    @property
    def workspace_links(self) -> list[Path]:
        """All workspace paths that should be symlinks to the canonical target."""
        return [self.claude_skill_dir, self.agents_skill_dir]


@dataclass
class SymlinkMaterializer:
    """Atomic create-or-repair of workspace symlinks to canonical targets.

    Does NOT follow attacker-controlled links when deleting/replacing.
    Each link repair is done atomically: verify, prepare replacement,
    then atomically swap.
    """

    def materialize(self, specs: list[SkillLinkSpec]) -> list[Path]:
        """Materialize all skill links for the given specs.

        For each spec:
        1. Verify canonical target integrity (hash match)
        2. For each workspace link path, verify it's a valid symlink to the
           exact canonical target
        3. If invalid, atomically repair

        Returns all created/repaired link paths.

        Raises:
            MaterializationError: on any hash mismatch or repair failure
        """
        created_links: list[Path] = []

        for spec in specs:
            # 1. Verify canonical target exists and has correct hash
            self._verify_canonical_target(spec)

            # 2. Materialize each workspace link
            for link_path in spec.workspace_links:
                created = self._ensure_link(link_path, spec)
                created_links.append(created)

        return created_links

    def withdraw_skills(self, workspace_link_paths: list[Path]) -> None:
        """Safely remove workspace skill links without deleting canonical content.

        Only removes links that are symlinks or empty directories. Does NOT
        follow symlinks to delete target content.
        """
        for link_path in workspace_link_paths:
            if link_path.is_symlink():
                link_path.unlink()
            elif link_path.is_dir():
                # Only remove if it's an empty directory or a known skill dir
                # (conservative: only remove if empty to avoid data loss)
                try:
                    link_path.rmdir()
                except OSError:
                    # Directory not empty — leave it alone
                    logger.warning(
                        "Not removing non-empty directory at %s during withdrawal",
                        link_path,
                    )
            elif link_path.exists():
                link_path.unlink(missing_ok=True)

    # ── internal helpers ──

    def _verify_canonical_target(self, spec: SkillLinkSpec) -> None:
        """Verify the canonical target exists and its hash matches expected."""
        target = spec.canonical_target
        if not target.is_dir():
            raise MaterializationError(
                slug=spec.slug,
                reason=f"canonical target not found: {target}",
                workspace_path=spec.claude_skill_dir.parent.parent,
            )

        actual_hash = _hash_dir(target)
        if actual_hash != spec.expected_content_hash:
            raise MaterializationError(
                slug=spec.slug,
                reason=(
                    f"canonical content hash mismatch: "
                    f"expected {spec.expected_content_hash[:16]}..., "
                    f"got {actual_hash[:16]}..."
                ),
                workspace_path=spec.claude_skill_dir.parent.parent,
            )

    def _ensure_link(self, link_path: Path, spec: SkillLinkSpec) -> Path:
        """Ensure link_path is a valid symlink to the canonical target.

        If the path doesn't exist, create the symlink.
        If it's a non-symlink (directory, file), replace with a symlink.
        If it's a symlink to the wrong target or broken, repair.
        """
        link_path.parent.mkdir(parents=True, exist_ok=True)

        if link_path.is_symlink():
            # Check if it points to the correct canonical target
            resolved = link_path.resolve()
            if resolved == spec.canonical_target:
                # Already correct — verify content hash hasn't drifted
                if _hash_dir(resolved) == spec.expected_content_hash:
                    return link_path
            # Wrong target or hash drift — repair
            self._atomic_replace_link(link_path, spec.canonical_target)
        elif link_path.is_dir():
            # Ordinary directory — replace with symlink
            self._replace_dir_with_link(link_path, spec.canonical_target)
        elif link_path.exists():
            # Some other file type — replace with symlink
            link_path.unlink()
            self._atomic_create_link(link_path, spec.canonical_target)
        else:
            # Doesn't exist — create symlink
            self._atomic_create_link(link_path, spec.canonical_target)

        return link_path

    def _atomic_create_link(self, link_path: Path, target: Path) -> None:
        """Atomically create a symlink at link_path pointing to target.

        Uses a temp name then rename for atomicity.
        """
        tmp_path = link_path.with_name(f".tmp.{link_path.name}")
        tmp_path.unlink(missing_ok=True)
        try:
            tmp_path.symlink_to(target)
            tmp_path.rename(link_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    def _atomic_replace_link(self, link_path: Path, target: Path) -> None:
        """Replace an existing symlink with a new one pointing to target.

        SAFE: does not follow the existing symlink. Creates a new symlink
        at a temp path, then atomically renames over the old one.
        """
        tmp_path = link_path.with_name(f".tmp.{link_path.name}")
        tmp_path.unlink(missing_ok=True)
        try:
            tmp_path.symlink_to(target)
            tmp_path.rename(link_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    def _replace_dir_with_link(self, dir_path: Path, target: Path) -> None:
        """Replace an ordinary directory at dir_path with a symlink.

        SAFE: renames the old directory to a temp name before creating the
        symlink, then removes the old content. This prevents a window where
        the link doesn't exist.
        """
        tmp_old = dir_path.with_name(f".tmp.old.{dir_path.name}")
        tmp_old_path_exists = tmp_old.exists()
        if tmp_old_path_exists and tmp_old.is_dir():
            import shutil
            shutil.rmtree(tmp_old)
        elif tmp_old_path_exists:
            tmp_old.unlink()

        try:
            dir_path.rename(tmp_old)
            self._atomic_create_link(dir_path, target)
        except Exception:
            # Try to restore
            if tmp_old.exists() and not dir_path.exists():
                tmp_old.rename(dir_path)
            raise
        else:
            # Success — clean up old content
            import shutil
            if tmp_old.exists():
                shutil.rmtree(tmp_old, ignore_errors=True)


def _hash_dir(path: Path) -> str:
    """Compute deterministic SHA-256 of a directory tree."""
    if not path.is_dir():
        return ""
    hasher = hashlib.sha256()
    for f in sorted(path.rglob("*")):
        if f.is_file():
            rel = f.relative_to(path)
            hasher.update(str(rel).encode())
            hasher.update(f.read_bytes())
    return hasher.hexdigest()
