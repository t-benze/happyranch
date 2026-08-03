"""Workspace-to-canonical symlink materializer.

Atomic create-or-repair of workspace symlinks to canonical package targets.
Handles stale, broken, wrong-version, wrong-target, external/malicious,
ordinary-directory, and missing-canonical entries. Safe withdrawal preserves
canonical content while removing workspace links.

Fail-closed: any repair failure surfaces a named materialization error and
prevents executor launch.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from runtime.platform.isolation import (
    PlatformIsolation,
    PlatformIsolationError,
    detect_platform_isolation,
)
from runtime.skills.canonical_store import CanonicalSkillStore, CanonicalStoreError

logger = logging.getLogger("happyranch.skills.symlink_materializer")


class SymlinkMaterializationError(Exception):
    """Raised when workspace link materialization fails.

    Terminal — no executor launch proceeds.
    """

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"[{code}] {detail}")


class SymlinkMaterializer:
    """Creates, repairs, and withdraws workspace symlinks to canonical packages.

    Every expected skill is resolved to its canonical path first. Then:
    - If the workspace link is a valid symlink to the correct canonical target:
      no-op (idempotent).
    - If the link is missing, stale, broken, wrong-target, external, or an
      ordinary directory: atomically remove the old entry and create a fresh
      relative symlink.
    - If the canonical target doesn't exist or is invalid: fail-closed.

    Safe withdrawal: removes workspace links without deleting canonical content.
    Poisoned repair: if repair fails for any reason, the entire materialization
    fails and no executor launches.
    """

    def __init__(
        self,
        store: CanonicalSkillStore,
        isolation: Optional[PlatformIsolation] = None,
    ) -> None:
        self._store = store
        self._isolation = isolation or detect_platform_isolation()

    def materialize_skill(
        self,
        skill_slug: str,
        version: str,
        content_hash: str,
        workspace: Path,
        skills_subdir: str,  # ".claude/skills" or ".agents/skills"
        *,
        canonical_root: Optional[Path] = None,
    ) -> None:
        """Ensure a workspace symlink exists pointing to the canonical package.

        Steps:
        1. Verify canonical package exists and has valid ownership.
        2. Resolve workspace link path: <workspace>/<skills_subdir>/<skill_slug>
        3. Compute relative target from link to canonical package dir.
        4. If existing link is valid → no-op.
        5. If existing entry is wrong/broken/stale → safely remove and recreate.
        6. Create validated relative symlink.

        Args:
            skill_slug: Skill directory name
            version: Package version
            content_hash: Content hash identifying the canonical package
            workspace: Agent workspace root
            skills_subdir: Provider-specific skills directory (e.g. ".claude/skills")
            canonical_root: Override canonical store root (for testing)

        Raises:
            SymlinkMaterializationError: on canonical missing, repair failure,
                or malicious entry.
        """
        # Verify canonical package
        try:
            self._store.verify_package(skill_slug, version, content_hash)
        except CanonicalStoreError as exc:
            raise SymlinkMaterializationError(
                "canonical_missing",
                f"Cannot materialize {skill_slug}@{version}: {exc}",
            ) from exc

        canonical_target = self._store.canonical_path(skill_slug, version, content_hash)
        croot = canonical_root or self._store.root
        link_path = workspace / skills_subdir / skill_slug

        # Relative target from link to canonical
        try:
            rel_target = Path(os.path.relpath(canonical_target, link_path.parent))
        except ValueError:
            raise SymlinkMaterializationError(
                "relpath_failed",
                f"Cannot compute relative path from {link_path.parent} to {canonical_target}",
            )

        # Check existing entry
        if link_path.exists(follow_symlinks=False):
            if link_path.is_symlink():
                if self._isolation.verify_workspace_link(
                    link_path, canonical_target, croot,
                ):
                    # Already valid — no-op
                    logger.debug(
                        "Workspace link %s → %s already valid",
                        link_path, canonical_target,
                    )
                    return
                # Stale/wrong symlink — remove safely
                logger.info(
                    "Removing stale/wrong symlink: %s", link_path,
                )
                link_path.unlink()
            elif link_path.is_dir():
                # Ordinary directory at expected link path — hostile state.
                # Must NEVER recursively delete attacker-controlled content.
                # This is a named materialization failure — no executor
                # launch proceeds.
                raise SymlinkMaterializationError(
                    "ordinary_dir_at_link_path",
                    f"Expected symlink at {link_path} but found ordinary directory. "
                    "Refusing to delete — remove manually or use withdraw_skill first "
                    "after verifying the directory does not contain real work.",
                )
            else:
                # File or other entry — remove
                logger.warning(
                    "Removing unexpected non-link entry: %s", link_path,
                )
                link_path.unlink()

        # Create the link
        try:
            self._isolation.create_relative_symlink(
                rel_target, link_path,
            )
            logger.info(
                "Created workspace link %s → %s", link_path, rel_target,
            )
        except PlatformIsolationError as exc:
            raise SymlinkMaterializationError(
                "link_creation_failed",
                f"Failed to create symlink {link_path} → {rel_target}: {exc}",
            ) from exc

    def withdraw_skill(
        self,
        skill_slug: str,
        workspace: Path,
        skills_subdir: str,
    ) -> None:
        """Safely remove a workspace symlink for a withdrawn/retired skill.

        Does NOT delete canonical content — only removes the workspace link.
        Before removal, verifies the entry is actually a symlink (not an
        ordinary directory with real content).

        Args:
            skill_slug: Skill directory name to remove
            workspace: Agent workspace root
            skills_subdir: Provider-specific skills directory

        Raises:
            SymlinkMaterializationError: if the entry is an ordinary directory
                (possibly containing real data that must not be deleted).
        """
        link_path = workspace / skills_subdir / skill_slug
        if not link_path.exists(follow_symlinks=False):
            return  # Already gone

        if link_path.is_symlink():
            link_path.unlink()
            logger.info("Withdrew workspace link: %s", link_path)
        elif link_path.is_dir():
            # Ordinary directory — NOT owned by symlink materializer.
            # Do NOT delete — it might contain real work.
            raise SymlinkMaterializationError(
                "ordinary_dir_not_withdrawable",
                f"Expected symlink at {link_path} but found ordinary directory "
                f"— refusing to delete potentially valuable content",
            )
        else:
            link_path.unlink()
            logger.info("Withdrew non-link entry: %s", link_path)

    def materialize_skills_batch(
        self,
        specs: list[dict],
        workspace: Path,
        skills_subdir: str,
    ) -> list[str]:
        """Materialize a batch of skill specs, returning list of materialized slugs.

        Each spec is a dict with keys: slug, version, content_hash.
        Fail-closed: if any materialization fails, the error propagates.
        """
        materialized: list[str] = []
        for spec in specs:
            self.materialize_skill(
                skill_slug=spec["slug"],
                version=spec["version"],
                content_hash=spec["content_hash"],
                workspace=workspace,
                skills_subdir=skills_subdir,
            )
            materialized.append(spec["slug"])
        return materialized

    def repair_workspace_skills(
        self,
        expected_specs: list[dict],
        workspace: Path,
        skills_subdir: str,
    ) -> tuple[list[str], list[str]]:
        """Materialize expected skills AND withdraw unexpected ones.

        Reconciles the workspace's managed skills directory against the
        expected set. Expected specs are materialized (created or repaired).
        Existing entries NOT in the expected set are safely withdrawn.

        Returns (materialized_slugs, withdrawn_slugs).

        Fail-closed: any failure raises, and partial repairs may leave the
        workspace in an intermediate state — but the error prevents executor
        launch (the fail-closed guarantee).
        """
        skills_dir = workspace / skills_subdir
        expected_slugs = {spec["slug"] for spec in expected_specs}

        # Identify existing entries
        existing_slugs: set[str] = set()
        if skills_dir.is_dir():
            for entry in skills_dir.iterdir():
                if entry.name.startswith(".tmp."):
                    continue  # Stale temp dirs from crashed materialization
                existing_slugs.add(entry.name)

        # Materialize expected
        materialized: list[str] = []
        for spec in expected_specs:
            self.materialize_skill(
                skill_slug=spec["slug"],
                version=spec["version"],
                content_hash=spec["content_hash"],
                workspace=workspace,
                skills_subdir=skills_subdir,
            )
            materialized.append(spec["slug"])

        # Withdraw unexpected (safely)
        withdrawn: list[str] = []
        to_withdraw = existing_slugs - expected_slugs
        for slug in sorted(to_withdraw):
            self.withdraw_skill(slug, workspace, skills_subdir)
            withdrawn.append(slug)

        return materialized, withdrawn


# Convenience import
import os  # noqa: E402 (used in relpath call above)
