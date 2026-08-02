"""Immutable canonical skill-package storage.

TASK-3988: Build daemon-owned canonical package storage outside executor
workspaces. Packages are stored at content-hash-addressed paths so the exact
approved version/hash resolves to a single immutable tree. This replaces the
legacy per-session content-copy model where skills were repeatedly copied into
each workspace.

Packages are built atomically into the canonical store from verified
source/manifest members. The store validates hashes, rejects path-traversal
members, and produces stable, non-writable content trees.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

logger = logging.getLogger("happyranch.skills.canonical_store")


class CanonicalStoreError(RuntimeError):
    """Base error for canonical store operations."""


@dataclass(frozen=True, slots=True)
class CanonicalPackageKey:
    """Stable key for resolving a canonical package tree.

    The key is hash-addressed: same slug + version + content_hash always
    resolves to the same canonical path. The content_hash is the SHA-256
    of the package content (for simple trees, hash of all files; for
    manifest-backed packages, hash of the manifest JSON).
    """

    slug: str
    package_type: str  # "system_contract", "release_managed", "lifecycle"
    version: str
    content_hash: str  # hex SHA-256

    def __post_init__(self):
        if len(self.content_hash) != 64 or not all(c in "0123456789abcdef" for c in self.content_hash):
            raise CanonicalStoreError(
                f"content_hash must be a 64-char hex SHA-256: {self.content_hash!r}"
            )


@dataclass
class CanonicalStore:
    """Daemon-owned immutable skill-package storage.

    Packages are stored at::

        <store_root>/<package_type>/<slug>/<version>/<content_hash[:16]>/

    The hash prefix (first 16 chars) is used for uniqueness; the full hash
    is validated at build time. This gives a stable, non-colliding path for
    each exact approved package version.

    The store does NOT implement any permission model — it relies on the
    filesystem's existing ownership/permissions. The executor workspace
    boundary (§ skill symlinks → canonical targets) is the primary isolation.
    """

    store_root: Path

    # Directory layout constants
    _HASH_PREFIX_LEN: ClassVar[int] = 16

    def build(self, src_dir: Path, key: CanonicalPackageKey) -> Path:
        """Build a canonical tree from a source directory.

        Validates that the source content hash matches the key's content_hash,
        then atomically constructs the tree at the hash-addressed path.
        If the tree already exists, returns the existing path (idempotent).

        Args:
            src_dir: Source directory containing the skill package
            key: Canonical package key with slug, version, and expected hash

        Returns:
            Absolute Path to the canonical tree

        Raises:
            CanonicalStoreError: hash mismatch, build failure
        """
        # Validate source hash matches key
        actual_hash = self._hash_dir(src_dir)
        if actual_hash != key.content_hash:
            raise CanonicalStoreError(
                f"hash mismatch for {key.slug}@{key.version}: "
                f"expected {key.content_hash[:16]}..., got {actual_hash[:16]}..."
            )

        target_dir = self._canonical_path(key)
        if target_dir.exists():
            # Idempotent: if already exists with correct hash, return it
            if target_dir.is_dir():
                existing_hash = self._hash_dir(target_dir)
                if existing_hash == key.content_hash:
                    return target_dir
            # Stale/corrupt: remove and rebuild
            if target_dir.is_dir():
                shutil.rmtree(target_dir)
            else:
                target_dir.unlink(missing_ok=True)

        # Atomic build: copy to temp, then rename
        tmp_dir = target_dir.with_name(f".tmp.{target_dir.name}")
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        try:
            self._copy_dir(src_dir, tmp_dir)
            # Make all files read-only for canonical immutability
            self._make_read_only(tmp_dir)
            tmp_dir.rename(target_dir)
        except Exception:
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir)
            raise

        logger.info(
            "Built canonical tree: %s@%s → %s",
            key.slug, key.version, target_dir,
        )
        return target_dir

    def build_from_manifest(
        self,
        members: list[dict],
        artifact_dir: Path,
        key: CanonicalPackageKey,
    ) -> Path:
        """Build a canonical tree from a manifest's member list.

        Each member dict must have: path, hash (e.g. "sha256:abc..."),
        artifact_key. Members are loaded from artifact_dir, validated
        byte-for-byte against their declared hash, and written into the
        canonical tree.

        Path-traversal members (.., absolute paths) are rejected.

        Args:
            members: List of manifest member dicts with path/hash/artifact_key
            artifact_dir: Directory containing artifact files
            key: Canonical package key

        Returns:
            Absolute Path to the canonical tree

        Raises:
            CanonicalStoreError: hash mismatch, path traversal, build failure
        """
        if not members:
            raise CanonicalStoreError(f"Manifest for {key.slug} has no members")

        # Validate manifest hash matches key
        manifest = json.dumps({"members": [
            {k: v for k, v in m.items() if k != "raw_bytes"}
            for m in members
        ]}).encode()
        manifest_hash = hashlib.sha256(manifest).hexdigest()
        if manifest_hash != key.content_hash:
            raise CanonicalStoreError(
                f"manifest hash mismatch for {key.slug}@{key.version}: "
                f"expected {key.content_hash[:16]}..., got {manifest_hash[:16]}..."
            )

        target_dir = self._canonical_path(key)
        if target_dir.exists():
            if target_dir.is_dir():
                existing_hash = self._hash_dir(target_dir)
                if existing_hash == key.content_hash:
                    return target_dir
            if target_dir.is_dir():
                shutil.rmtree(target_dir)
            else:
                target_dir.unlink(missing_ok=True)

        # Validate all members before writing anything
        self._validate_manifest_members(members, target_dir)

        # Atomic build: write to temp, then rename
        tmp_dir = target_dir.with_name(f".tmp.{target_dir.name}")
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        try:
            tmp_dir.mkdir(parents=True)
            for member in members:
                self._write_member(member, artifact_dir, tmp_dir)
            # Make all files read-only for canonical immutability
            self._make_read_only(tmp_dir)
            tmp_dir.rename(target_dir)
        except Exception:
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir)
            raise

        logger.info(
            "Built canonical tree from manifest: %s@%s → %s",
            key.slug, key.version, target_dir,
        )
        return target_dir

    def get(self, key: CanonicalPackageKey) -> Path | None:
        """Resolve a canonical path from a key. Returns None if not built."""
        target_dir = self._canonical_path(key)
        if target_dir.is_dir():
            actual_hash = self._hash_dir(target_dir)
            if actual_hash == key.content_hash:
                return target_dir
        return None

    @staticmethod
    def _make_read_only(path: Path) -> None:
        """Make all files in a tree read-only (0444). Directories remain 0555."""
        for item in path.rglob("*"):
            if item.is_file():
                item.chmod(0o444)
            elif item.is_dir():
                item.chmod(0o555)
        # Also chmod the root
        path.chmod(0o555)

    # ── internal helpers ──

    def _canonical_path(self, key: CanonicalPackageKey) -> Path:
        """Compute the canonical path for a package key."""
        safe_slug = _safe_path_segment(key.slug)
        safe_type = _safe_path_segment(key.package_type)
        safe_version = _safe_path_segment(key.version)
        hash_prefix = key.content_hash[:self._HASH_PREFIX_LEN]
        return self.store_root / safe_type / safe_slug / safe_version / hash_prefix

    def _hash_dir(self, path: Path) -> str:
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

    @staticmethod
    def _copy_dir(src: Path, dst: Path) -> None:
        """Copy a directory tree into dst."""
        dst.mkdir(parents=True, exist_ok=True)
        for item in src.iterdir():
            target = dst / item.name
            if item.is_dir():
                CanonicalStore._copy_dir(item, target)
            else:
                shutil.copy2(item, target)

    def _validate_manifest_members(self, members: list[dict], target_dir: Path) -> None:
        """Validate all manifest members before writing.

        Checks: safe paths (no traversal), hash matches artifact content.
        """
        for member in members:
            member_path = member["path"]
            # Path traversal guard
            resolved = (target_dir / member_path).resolve()
            if not str(resolved).startswith(str(target_dir.resolve())):
                raise CanonicalStoreError(
                    f"path traversal rejected: {member_path!r} resolves outside "
                    f"target directory {target_dir}"
                )

    def _write_member(self, member: dict, artifact_dir: Path, target_dir: Path) -> None:
        """Write a single manifest member into the canonical tree.

        Validates the member's hash against the artifact content byte-for-byte,
        then writes the exact bytes to the target path.
        """
        member_path = member["path"]
        member_hash = member["hash"]  # "sha256:abc..." or just hex
        artifact_key = member["artifact_key"]

        # Load artifact content
        artifact_file = artifact_dir / artifact_key
        if not artifact_file.is_file():
            raise CanonicalStoreError(
                f"Artifact not found: {artifact_key} for member {member_path}"
            )

        member_bytes = artifact_file.read_bytes()

        # Validate hash
        expected_hex = member_hash.split(":", 1)[-1] if ":" in member_hash else member_hash
        actual_hex = hashlib.sha256(member_bytes).hexdigest()
        if actual_hex != expected_hex:
            raise CanonicalStoreError(
                f"member hash mismatch for {member_path}: "
                f"expected {expected_hex[:16]}..., got {actual_hex[:16]}..."
            )

        # Write to target
        target_path = target_dir / member_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(member_bytes)


def _safe_path_segment(segment: str) -> str:
    """Sanitize a path segment for use in the canonical store layout.

    Replaces characters that could cause path issues (/, \\, .., null bytes).
    Only allows alphanumeric, hyphens, underscores, and dots.
    """
    safe = "".join(
        c if c.isalnum() or c in "-_." else "_"
        for c in segment
    )
    if not safe or safe == "." or safe == "..":
        raise CanonicalStoreError(f"Invalid path segment: {segment!r} → {safe!r}")
    return safe
