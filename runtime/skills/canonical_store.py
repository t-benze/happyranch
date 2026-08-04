"""Canonical skill package store.

Daemon-owned, hash-addressed storage outside executor workspaces.
Canonical packages are built atomically from verified source/manifest members.

In distinct-identity mode, files are read-only (0444) after build — the
DISTINCT executor identity can only read, never write, delete, rename, chmod,
or chown. Directories are 0755 so the daemon can rebuild.

In same-owner mode (``HAPPYRANCH_ALLOW_SAME_OWNER_EXECUTOR=1``), the executor
shares the daemon uid. A same-UID executor can chmod + write canonical files.
Integrity is enforced by synchronous pre-launch hash detection in
``validate_workspace_skills_integrity``, not by OS-level immutability.

Package resolution maps a package identity (slug, version, content_hash) to
an exact canonical path. Workspace links point to these paths.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import stat
from pathlib import Path
from typing import Optional

from runtime.platform.isolation import (
    PlatformIsolation,
    PlatformIsolationError,
    detect_platform_isolation,
)

logger = logging.getLogger("happyranch.skills.canonical_store")

# Canonical store lives under the runtime root, outside any executor workspace.
# Environment variable override for testing.
_CANONICAL_STORE_ROOT_ENV = "HAPPYRANCH_CANONICAL_STORE_ROOT"


class CanonicalStoreError(Exception):
    """Raised when canonical store operations fail (missing, hash mismatch,
    path traversal, write error). Always fail-closed — no partial state.
    """

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"[{code}] {detail}")


def _verify_recursive_readonly(
    pkg_path: Path,
    *,
    is_same_owner_mode: bool = False,
) -> None:
    """Recursively verify the package root and every file and directory
    under *pkg_path* is NOT group-writable or other-writable.

    Directory S_IWUSR (0755) is always allowed — the daemon owner must
    retain write so new canonical packages can be built. In distinct-identity
    mode the executor has a different uid and Unix user/group model prevents
    write, not the directory mode bit.

    File S_IWUSR policy depends on isolation mode:
    - Distinct-identity: files must be 0444 (not owner-writable). The
      executor identity is a different uid and cannot chmod to regain
      write.
    - Same-owner: files may be owner-writable (0644). The executor runs
      as the same uid as the daemon and CAN chmod + write. Integrity is
      deferred to hash comparison in ``verify_package()``.

    An insufficiently hardened package (hardening failed after os.replace)
    will fail these checks.  This prevents is_built() from returning True
    for a package whose readonly protection was never fully applied.

    Raises CanonicalStoreError on any writable bit found.
    """
    # Check the root directory itself first (dirs: allow S_IWUSR always)
    try:
        root_mode = stat.S_IMODE(pkg_path.stat().st_mode)
    except OSError:
        raise CanonicalStoreError(
            "insufficient_hardening",
            f"Cannot stat package root: {pkg_path}",
        )
    if root_mode & stat.S_IWGRP:
        raise CanonicalStoreError(
            "insufficient_hardening",
            f"Package root is group-writable: {pkg_path}",
        )
    if root_mode & stat.S_IWOTH:
        raise CanonicalStoreError(
            "insufficient_hardening",
            f"Package root is world-writable: {pkg_path}",
        )
    # Check all members recursively
    for entry in sorted(pkg_path.rglob("*")):
        try:
            mode = stat.S_IMODE(entry.stat().st_mode)
        except OSError:
            continue
        if entry.is_dir():
            # Directories: allow S_IWUSR (daemon must rebuild), only
            # reject S_IWGRP / S_IWOTH.
            if mode & stat.S_IWGRP:
                raise CanonicalStoreError(
                    "insufficient_hardening",
                    f"Package directory is group-writable: {entry}",
                )
            if mode & stat.S_IWOTH:
                raise CanonicalStoreError(
                    "insufficient_hardening",
                    f"Package directory is world-writable: {entry}",
                )
        else:
            # Files: distinct-identity mode requires 0444 (no S_IWUSR).
            # Same-owner mode allows 0644 (executor IS owner, can chmod).
            if not is_same_owner_mode and mode & stat.S_IWUSR:
                raise CanonicalStoreError(
                    "insufficient_hardening",
                    f"Package file is owner-writable: {entry}",
                )
            if mode & stat.S_IWGRP:
                raise CanonicalStoreError(
                    "insufficient_hardening",
                    f"Package file is group-writable: {entry}",
                )
            if mode & stat.S_IWOTH:
                raise CanonicalStoreError(
                    "insufficient_hardening",
                    f"Package file is world-writable: {entry}",
                )


def _apply_readonly_hardening(
    isolation: PlatformIsolation, pkg_path: Path,
) -> None:
    """Apply readonly hardening to a published canonical package.

    Sets all files 0444 and all directories 0555 (read+traverse) for
    the executor identity.  This runs AFTER os.replace has published
    the package at its final location.

    Raises the original OSError if hardening fails — callers must
    compensate by quarantining/removing the unsafe published package.
    """
    # Make all files read-only
    for fpath in pkg_path.rglob("*"):
        if fpath.is_file():
            isolation.make_file_readonly(fpath)
    # Make all dirs read+traverse for executor
    for dpath in pkg_path.rglob("*"):
        if dpath.is_dir():
            isolation.make_dir_readonly_executor(dpath)
    isolation.make_dir_readonly_executor(pkg_path)


def _make_writable_for_removal(pkg_path: Path) -> None:
    """Make all files and directories in *pkg_path* owner-writable
    so they can be removed by shutil.rmtree.

    Canonical packages have files at 0444 and directories at 0555.
    Before rmtree can delete them, we must restore write permission.
    """
    for entry in pkg_path.rglob("*"):
        try:
            entry.chmod(entry.stat().st_mode | stat.S_IWUSR)
        except OSError:
            pass
    try:
        pkg_path.chmod(pkg_path.stat().st_mode | stat.S_IWUSR)
    except OSError:
        pass


def _safe_remove_published_package(
    pkg_path: Path, slug: str, version: str, content_hash: str,
) -> None:
    """Safely remove a published-but-insufficiently-hardened package.

    Called when the readonly hardening step fails AFTER os.replace has
    already moved the temp directory into the final canonical location.
    The package must be removed so it cannot be reused by a later build.

    Only removes the package directory itself — never recursively follows
    or deletes untrusted nodes.

    Raises:
        CanonicalStoreError: if removal fails (compensation cannot establish
            safe state). The error includes the underlying cause.
    """
    try:
        # Verify the path is still a directory and is within the canonical
        # store root (defense-in-depth against path manipulation)
        if not pkg_path.is_dir():
            return
        # Remove only the package directory tree
        shutil.rmtree(pkg_path)
        logger.warning(
            "Removed insufficiently hardened canonical package "
            "%s@%s (hash=%s) at %s",
            slug, version, content_hash[:16], pkg_path,
        )
    except Exception as cleanup_exc:
        raise CanonicalStoreError(
            "compensation_failed",
            f"Failed to remove insufficiently hardened canonical package "
            f"{slug}@{version} (hash={content_hash[:16]}) at {pkg_path}: "
            f"{cleanup_exc}. Unsafe package may remain on disk.",
        ) from cleanup_exc


def _get_canonical_store_root(settings=None) -> Path:
    """Resolve the canonical store root directory.

    Priority:
    1. HAPPYRANCH_CANONICAL_STORE_ROOT env var (test override)
    2. Settings-derived daemon home /canonical-skills/
    """
    env = os.environ.get(_CANONICAL_STORE_ROOT_ENV)
    if env:
        return Path(env)

    if settings is not None:
        return settings.daemon_home / "canonical-skills"

    # Fallback: ~/.happyranch/canonical-skills/
    return Path.home() / ".happyranch" / "canonical-skills"


def _compute_tree_hash_from_manifest_members(
    manifest: dict,
    artifact_store,
    *,
    skill_slug: str,
) -> str:
    """Compute expected canonical tree hash from manifest members.

    Validates each member's artifact-store bytes against the immutable
    ledger-declared hash (``member["hash"]``) BEFORE computing the
    tree hash.  Used both by the pre-materialization spec builder
    (``_compute_manifest_tree_hash`` in workspace_adapters) and the
    ``build_from_manifest`` reuse verification path.

    Raises CanonicalStoreError on hash mismatch, missing artifacts,
    or malformed hash declarations.
    """
    members = manifest.get("members", [])
    if not members:
        raise CanonicalStoreError(
            "empty_manifest",
            f"Manifest for {skill_slug} has no members — cannot compute tree hash",
        )

    sorted_members = sorted(members, key=lambda m: m["path"])

    h = hashlib.sha256()
    for member in sorted_members:
        member_path = member["path"]
        member_artifact_key = member["artifact_key"]
        member_hash = member.get("hash", "")

        # Validate member hash declaration is well-formed
        expected_hex = (
            member_hash.split(":", 1)[-1] if ":" in member_hash
            else member_hash
        )
        if not expected_hex or len(expected_hex) < 16:
            raise CanonicalStoreError(
                "malformed_hash",
                f"Malformed or missing hash declaration for member "
                f"{member_path}: {member_hash!r}",
            )

        # Load member bytes from artifact store
        try:
            member_bytes = artifact_store.read(member_artifact_key)
        except Exception as exc:
            raise CanonicalStoreError(
                "artifact_load_failed",
                f"Failed to load artifact {member_artifact_key}: {exc}",
            ) from exc

        # Validate bytes against immutable ledger-declared hash
        actual_hash = hashlib.sha256(member_bytes).hexdigest()
        if actual_hash != expected_hex:
            raise CanonicalStoreError(
                "member_hash_mismatch",
                f"Member artifact hash mismatch for {member_path}: "
                f"ledger declares {expected_hex[:16]}..., "
                f"artifact store has {actual_hash[:16]}...",
            )

        h.update(member_path.encode())
        h.update(b"\x00")
        h.update(member_bytes)
        h.update(b"\x00")

    return h.hexdigest()


class CanonicalSkillStore:
    """Daemon-owned immutable store for skill package content.

    Package content is stored under:
        <store_root>/<slug>/<version>/<content_hash[:16]>/

    Each package directory is:
    - Owned by the daemon identity
    - Read-only (0444 files, 0555 dirs) for executor identity
    - Never writable through workspace symlinks

    The store builds packages atomically: all members are written to a temp
    directory, hashes are validated, and only then is the package atomically
    moved (os.replace) into the canonical path. This ensures a reader never
    sees a partially-written package.
    """

    def __init__(
        self,
        root: Optional[Path] = None,
        isolation: Optional[PlatformIsolation] = None,
        settings=None,
    ) -> None:
        self._root = root or _get_canonical_store_root(settings)
        self._isolation = isolation or detect_platform_isolation()
        self._ensure_store_initialized()

    @property
    def root(self) -> Path:
        return self._root

    def _ensure_store_initialized(self) -> None:
        """Provision the canonical store root with correct ownership/ACL."""
        self._root.mkdir(parents=True, exist_ok=True)
        self._isolation.provision_canonical_store(self._root)

    def canonical_path(
        self, slug: str, version: str, content_hash: str,
    ) -> Path:
        """Return the canonical path for a package with given identity.

        Hash-addressed: content_hash[:16] is used as the directory name
        so different versions/content can coexist.
        """
        safe_hash = content_hash[:16] if len(content_hash) >= 16 else content_hash
        return self._root / slug / version / safe_hash

    def is_built(
        self, slug: str, version: str, content_hash: str,
    ) -> bool:
        """Check if a canonical package is already built and valid.

        Validates ownership at the root, non-emptiness, AND recursively
        verifies that every file and directory has had readonly hardening
        applied (no owner, group, or other write bits).  This prevents reuse of a
        package published by os.replace but whose hardening (make_file_readonly
        / make_dir_readonly_executor) failed after the atomic move.
        """
        pkg_path = self.canonical_path(slug, version, content_hash)
        if not pkg_path.is_dir():
            return False
        # Verify root ownership
        try:
            self._isolation.verify_canonical_ownership(pkg_path)
        except PlatformIsolationError:
            return False
        if not any(pkg_path.iterdir()):
            return False
        # Recursively verify every file and directory is NOT group/other
        # writable.  An insufficiently hardened package (hardening failed
        # after os.replace) will fail these checks.
        try:
            # is_built() always uses strict mode checking — the hardening
            # step should have set files to 0444 regardless of isolation
            # mode. If hardening failed, the package is NOT built.
            _verify_recursive_readonly(pkg_path)
        except CanonicalStoreError:
            return False
        return True

    def build_from_source(
        self,
        slug: str,
        version: str,
        content_hash: str,
        source_dir: Path,
    ) -> Path:
        """Build a canonical package from a source directory.

        Copies all files from *source_dir* into a temp directory, validates
        safe paths (no traversal), then atomically replaces into the canonical
        path. Sets all files read-only after build.

        Args:
            slug: Skill slug
            version: Package version
            content_hash: Expected content hash (SHA-256 of canonical tree)
            source_dir: Directory containing skill files (SKILL.md, references/, assets/)

        Returns:
            Path to the built canonical package directory.

        Raises:
            CanonicalStoreError: on hash mismatch, path traversal, write failure.
        """
        pkg_path = self.canonical_path(slug, version, content_hash)

        # If already built and valid, return
        if self.is_built(slug, version, content_hash):
            return pkg_path

        # Collect files from source
        members: list[tuple[str, bytes]] = []
        for fpath in sorted(source_dir.rglob("*")):
            if not fpath.is_file():
                continue
            rel = str(fpath.relative_to(source_dir))
            # Validate no path traversal in relative name
            if ".." in rel.split(os.sep):
                raise CanonicalStoreError(
                    "path_traversal",
                    f"Path traversal in member: {rel}",
                )
            members.append((rel, fpath.read_bytes()))

        if not members:
            raise CanonicalStoreError(
                "empty_package",
                f"No files found in source directory for {slug}@{version}",
            )

        # Build in temp directory
        tmp = pkg_path.with_name(f".tmp.{content_hash[:8]}")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True, exist_ok=True)

        try:
            for rel, data in members:
                dest = tmp / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)

            # Verify tree hash (optional: can be validated by caller)
            # The content_hash in the ledger is manifest-based, not tree-based.
            # We preserve the member hashes separately.

            # Provision ownership on temp before atomic replace.
            # (Do NOT make readonly yet — on macOS, rename() requires
            # write permission on the source directory.)
            self._isolation.provision_canonical_store(tmp)

            # Atomic replace: move temp → final canonical path first,
            # then apply readonly to the final location.
            if pkg_path.exists():
                _make_writable_for_removal(pkg_path)
                shutil.rmtree(pkg_path)
            pkg_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp, pkg_path)

            # Apply readonly hardening at the final location.
            # If hardening fails after os.replace has already published
            # pkg_path, we must compensate — the package must not remain
            # as a candidate for later reuse.
            _apply_readonly_hardening(self._isolation, pkg_path)

            logger.info(
                "Built canonical package %s@%s (hash=%s) at %s",
                slug, version, content_hash[:16], pkg_path,
            )
            return pkg_path

        except Exception:
            if tmp.exists():
                shutil.rmtree(tmp)
            # If os.replace already published pkg_path, attempt safe removal.
            # The package is insufficiently hardened and must not be a
            # candidate for later is_built()/reuse.
            if pkg_path.exists() and not tmp.exists():
                _safe_remove_published_package(pkg_path, slug, version,
                                               content_hash)
            raise

    def build_from_manifest(
        self,
        slug: str,
        version: str,
        content_hash: str,
        manifest: dict,
        artifact_store,
    ) -> Path:
        """Build a canonical package from a manifest and artifact store.

        The manifest is a JSON dict with a ``members`` list:
            {"members": [{"path": "SKILL.md", "hash": "sha256:abc...",
                          "artifact_key": "skill-lifecycle/..."}, ...]}

        Each member's bytes are loaded from the artifact store, their hash
        is validated against the immutable ledger-declared hash, and they
        are written into the canonical tree.

        When ``is_built()`` reports True (canonical package already exists),
        this method does NOT blindly reuse it: it validates member artifact
        bytes against their ledger-declared hashes, computes the expected
        tree hash, and compares it against the canonical tree BEFORE
        accepting reuse. A mismatched canonical tree triggers a rebuild
        from artifact-store bytes.

        The *content_hash* is the package-version content hash from the
        lifecycle ledger. It is the SHA-256 of the manifest JSON itself
        (binding full-package provenance, distinct from individual member
        hashes). We preserve and verify the manifest hash separately.

        Args:
            slug: Skill slug
            version: Package version
            content_hash: Content hash from lifecycle ledger (SHA-256 of manifest)
            manifest: Parsed manifest dict with members list
            artifact_store: ArtifactStore instance for loading member bytes

        Returns:
            Path to built canonical package directory.
        """
        pkg_path = self.canonical_path(slug, version, content_hash)

        if self.is_built(slug, version, content_hash):
            # Defense-in-depth: verify canonical content matches the
            # manifest BEFORE accepting reuse.  is_built() only checks
            # ownership and mode bits — same-owner mutation can corrupt
            # content while leaving modes intact.  We validate member
            # bytes from the artifact store against ledger-declared
            # hashes, compute the expected tree hash, and compare
            # against the canonical tree.
            members = manifest.get("members", [])
            if members:
                expected_tree = _compute_tree_hash_from_manifest_members(
                    manifest, artifact_store,
                    skill_slug=slug,
                )
                actual_tree = self.compute_tree_hash(slug, version, content_hash)
                if actual_tree == expected_tree:
                    return pkg_path
                # Tree hash mismatch — canonical content corrupted.
                # Fall through to rebuild from artifact store.
                logger.warning(
                    "Canonical package %s@%s (hash=%s) tree hash mismatch "
                    "on reuse — rebuilding from artifact store. "
                    "expected=%s..., actual=%s...",
                    slug, version, content_hash[:16],
                    expected_tree[:16], actual_tree[:16],
                )
            else:
                return pkg_path

        members = manifest.get("members", [])
        if not members:
            raise CanonicalStoreError(
                "empty_manifest",
                f"Manifest for {slug}@{version} has no members",
            )

        # Build in temp directory
        tmp = pkg_path.with_name(f".tmp.{content_hash[:8]}")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True, exist_ok=True)

        try:
            for member in members:
                member_path = member["path"]
                member_hash = member["hash"]  # "sha256:abc123..."
                member_artifact_key = member["artifact_key"]

                # Validate path safety
                if ".." in member_path.split(os.sep) or member_path.startswith("/"):
                    raise CanonicalStoreError(
                        "path_traversal",
                        f"Unsafe member path: {member_path}",
                    )

                # Load member content from artifact store
                try:
                    member_bytes = artifact_store.read(member_artifact_key)
                except Exception as exc:
                    raise CanonicalStoreError(
                        "artifact_load_failed",
                        f"Failed to load artifact {member_artifact_key}: {exc}",
                    ) from exc

                # Validate member hash
                expected_hex = member_hash.split(":", 1)[-1] if ":" in member_hash else member_hash
                actual_hash = hashlib.sha256(member_bytes).hexdigest()
                if actual_hash != expected_hex:
                    raise CanonicalStoreError(
                        "member_hash_mismatch",
                        f"Hash mismatch for {member_path}: expected "
                        f"{expected_hex[:16]}..., got {actual_hash[:16]}...",
                    )

                # Write to temp
                dest = tmp / member_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(member_bytes)

            # Provision ownership on temp before atomic replace.
            # (Do NOT make readonly yet — on macOS, rename() requires
            # write permission on the source directory.)
            self._isolation.provision_canonical_store(tmp)

            # Atomic replace: move temp → final canonical path first,
            # then apply readonly to the final location.
            if pkg_path.exists():
                _make_writable_for_removal(pkg_path)
                shutil.rmtree(pkg_path)
            pkg_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp, pkg_path)

            # Apply readonly hardening at the final location.
            # If hardening fails after os.replace has already published
            # pkg_path, we must compensate — the package must not remain
            # as a candidate for later reuse.
            _apply_readonly_hardening(self._isolation, pkg_path)

            logger.info(
                "Built canonical package %s@%s from manifest (hash=%s) at %s",
                slug, version, content_hash[:16], pkg_path,
            )
            return pkg_path

        except CanonicalStoreError:
            if tmp.exists():
                shutil.rmtree(tmp)
            if pkg_path.exists() and not tmp.exists():
                _safe_remove_published_package(pkg_path, slug, version,
                                               content_hash)
            raise
        except Exception as exc:
            if tmp.exists():
                shutil.rmtree(tmp)
            if pkg_path.exists() and not tmp.exists():
                _safe_remove_published_package(pkg_path, slug, version,
                                               content_hash)
            raise CanonicalStoreError(
                "build_failed",
                f"Failed to build canonical package {slug}@{version}: {exc}",
            ) from exc

    def verify_package(self, slug: str, version: str, content_hash: str) -> None:
        """Verify a canonical package exists, has correct ownership,
        and every member is read-only (immutable invariant enforced at
        the materialization gate).

        This checks presence, ownership, and mode invariants. Content
        integrity (hash comparison) is performed separately by
        ``validate_workspace_skills_integrity`` using tree hashes
        computed at materialization time.

        Raises CanonicalStoreError if missing, tampered, or insufficiently
        hardened.
        """
        pkg_path = self.canonical_path(slug, version, content_hash)
        if not pkg_path.is_dir():
            raise CanonicalStoreError(
                "package_missing",
                f"Canonical package not found: {slug}@{version} (hash={content_hash[:16]})",
            )
        if not any(pkg_path.iterdir()):
            raise CanonicalStoreError(
                "package_empty",
                f"Canonical package is empty: {slug}@{version}",
            )
        try:
            self._isolation.verify_canonical_ownership(pkg_path)
        except PlatformIsolationError as exc:
            raise CanonicalStoreError(
                "ownership_violation",
                f"Canonical package ownership invalid for {slug}@{version}: {exc}",
            ) from exc
        # Enforce the full immutable invariant at the materialization gate.
        # A package whose hardening failed after os.replace must never be
        # materialized into a workspace link.
        _verify_recursive_readonly(
            pkg_path,
            is_same_owner_mode=self._isolation.is_same_owner_mode,
        )

    def compute_tree_hash(self, slug: str, version: str, content_hash: str) -> str:
        """Compute SHA-256 of the canonical tree content (for verification).

        Returns hex digest of all file contents sorted by relative path.
        """
        pkg_path = self.canonical_path(slug, version, content_hash)
        h = hashlib.sha256()
        for fpath in sorted(pkg_path.rglob("*")):
            if fpath.is_file():
                rel = str(fpath.relative_to(pkg_path))
                h.update(rel.encode())
                h.update(b"\x00")
                h.update(fpath.read_bytes())
                h.update(b"\x00")
        return h.hexdigest()
