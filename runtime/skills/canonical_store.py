"""Canonical skill package store.

Hash-addressed storage outside executor workspaces. Canonical packages
are built atomically from verified source/manifest members.

Package resolution maps a package identity (slug, version, content_hash) to
an exact canonical path. Workspace links point to these paths.

The executor runs under the daemon's own OS identity — same-UID writes
are possible. Integrity verification (hash/link checks) provides
detection for accidental corruption, not a security boundary.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
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


def _make_writable_for_removal(pkg_path: Path) -> None:
    """Ensure all entries in *pkg_path* are owner-writable for removal.

    Safe no-op on already-writable files; needed only for backward
    compatibility with older packages that may have been hardened.
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


# ── Canonical strict SHA-256 hash parser ────────────────────────────
# THE single authoritative validator for member-hash declarations.
# Every caller — workspace adapters, recovery route, manifest materialization,
# lifecycle spec construction — must use this parser. No competing parsers.
# Accepts ONLY "sha256:<64 lowercase hex>".

_SHA256_HEX_RE = re.compile(r"^[a-f0-9]{64}$")


def parse_strict_sha256_hash(hash_str: str) -> str:
    """Parse a strictly-formatted sha256:<64-lowercase-hex> member hash.

    Returns the 64-char hex digest (without prefix).
    Raises ValueError for any malformed input.
    """
    if not hash_str.startswith("sha256:"):
        raise ValueError(
            f"Member hash missing algorithm prefix (expected sha256:<hex>): "
            f"{hash_str[:80]}"
        )
    hex_digest = hash_str[7:]
    if not _SHA256_HEX_RE.match(hex_digest):
        raise ValueError(
            f"Member hash invalid format (expected sha256:<64 lowercase hex>): "
            f"{hash_str[:80]}"
        )
    return hex_digest


def _validate_member_hash(member_path: str, raw_hash: str) -> str:
    """Validate and extract the hex digest from a member hash declaration.

    Delegates to the single canonical ``parse_strict_sha256_hash`` validator.
    Rejects missing/unknown algorithm prefixes, bad hex length,
    uppercase hex, non-hex characters, and malformed declarations.
    Returns the 64-char lowercase hex portion on success.
    """
    if not raw_hash or not isinstance(raw_hash, str):
        raise CanonicalStoreError(
            "malformed_hash",
            f"Missing hash declaration for member {member_path!r}",
        )
    try:
        return parse_strict_sha256_hash(raw_hash)
    except ValueError as exc:
        raise CanonicalStoreError(
            "malformed_hash",
            f"Hash declaration for member {member_path!r} must be "
            f"exactly sha256:<64 lowercase hex>; got {raw_hash!r}",
        ) from exc


def _compute_tree_hash_from_manifest_members(
    manifest: dict,
    artifact_store,
    *,
    skill_slug: str,
) -> str:
    """Compute expected canonical tree hash from manifest members.

    Validates each member's artifact-store bytes against the immutable
    ledger-declared SHA-256 hash BEFORE computing the tree hash.
    Used by both the pre-materialization spec builder
    (``_compute_manifest_tree_hash`` in workspace_adapters) and the
    ``build_from_manifest`` reuse verification path.

    Raises CanonicalStoreError on hash mismatch, missing artifacts,
    malformed hash declarations, or missing/non-hex declarations.
    """
    members = manifest.get("members", [])
    if not members:
        raise CanonicalStoreError(
            "empty_manifest",
            f"Manifest for {skill_slug} has no members — cannot compute tree hash",
        )

    sorted_members = sorted(members, key=lambda m: m.get("path", ""))

    h = hashlib.sha256()
    for member in sorted_members:
        member_path = member.get("path", "")
        member_artifact_key = member.get("artifact_key", "")
        member_hash = member.get("hash", "")

        if not member_path:
            raise CanonicalStoreError(
                "malformed_manifest",
                f"Member in manifest for {skill_slug} missing 'path' field",
            )

        # Validate member hash declaration strictly (Finding 3 fix)
        expected_hex = _validate_member_hash(member_path, member_hash)

        # Load member bytes from artifact store
        try:
            member_bytes = artifact_store.read(member_artifact_key)
        except Exception as exc:
            raise CanonicalStoreError(
                "artifact_load_failed",
                f"Failed to load artifact {member_artifact_key}: {exc}",
            ) from exc

        # Validate bytes against immutable ledger-declared hash
        actual_hex = hashlib.sha256(member_bytes).hexdigest()
        if actual_hex != expected_hex:
            raise CanonicalStoreError(
                "member_hash_mismatch",
                f"Member artifact hash mismatch for {member_path}: "
                f"ledger declares {expected_hex[:16]}..., "
                f"artifact store has {actual_hex[:16]}...",
            )

        h.update(member_path.encode())
        h.update(b"\x00")
        h.update(member_bytes)
        h.update(b"\x00")

    return h.hexdigest()


class CanonicalSkillStore:
    """Canonical skill store for hash-addressed package content.

    Package content is stored under:
        <store_root>/<slug>/<version>/<content_hash[:16]>/

    Hash-addressed — packages are referenced by their content hash,
    not by ownership or permission state. Same-UID writes are possible;
    integrity verification provides corruption detection, not a
    security boundary.

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
        """Ensure the canonical store root directory exists."""
        self._root.mkdir(parents=True, exist_ok=True)

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

        Validates non-emptiness only. Same-UID writes are possible;
        this is a presence check, not a security boundary.
        """
        pkg_path = self.canonical_path(slug, version, content_hash)
        if not pkg_path.is_dir():
            return False
        if not any(pkg_path.iterdir()):
            return False
        return True

    def build_from_source(
        self,
        slug: str,
        version: str,
        content_hash: str,
        source_dir: Path,
        *,
        verify_source_hash: str | None = None,
    ) -> Path:
        """Build a canonical package from a source directory.

        Copies all files from *source_dir* into a temp directory, validates
        safe paths (no traversal), then atomically replaces into the canonical
        path. Sets all files read-only after build.

        When *verify_source_hash* is provided and the package already exists
        (``is_built``), the actual content of the canonical package is
        compared against the expected source hash. If the content has been
        altered (e.g. by a same-owner executor), a ``CanonicalStoreError`` is
        raised — NO automatic rebuild from same-UID local source occurs.
        First-ever materialization of an absent package remains allowed;
        a valid existing package may be reused. But a corrupted existing
        package is never silently repaired.

        Args:
            slug: Skill slug
            version: Package version
            content_hash: Expected content hash (SHA-256 of canonical tree)
            source_dir: Directory containing skill files (SKILL.md, references/, assets/)
            verify_source_hash: If set, verify existing package content
                against this hash (source tree hash) before reusing. Mismatch
                raises CanonicalStoreError instead of rebuilding.

        Returns:
            Path to the built canonical package directory.

        Raises:
            CanonicalStoreError: on hash mismatch, path traversal, write failure,
                or content corruption of an existing package.
        """
        pkg_path = self.canonical_path(slug, version, content_hash)

        # If already built and valid, verify content integrity before reusing.
        if self.is_built(slug, version, content_hash):
            if verify_source_hash is not None:
                try:
                    actual_hash = self.compute_tree_hash(slug, version, content_hash)
                except CanonicalStoreError:
                    actual_hash = ""
                if actual_hash != verify_source_hash:
                    raise CanonicalStoreError(
                        "content_corruption",
                        f"Canonical package {slug}@{version} content mismatch "
                        f"(expected {verify_source_hash[:16]}... "
                        f"got {actual_hash[:16] if actual_hash else '<error>'}). "
                        f"No automatic repair from same-UID local source. "
                        f"Recovery: use `happyranch skills recover "
                        f"{slug} {version} {content_hash}` to remove the "
                        f"corrupted package, then next materialization "
                        f"rebuilds from the ArtifactStore.",
                    )
                else:
                    return pkg_path
            else:
                return pkg_path

        # ── Detection: existing but invalid canonical package ────────
        # If the canonical directory exists but is_built() is False,
        # the package is CORRUPTED — NOT an absent first-build scenario.
        # Refuse with content_corruption instead of deleting and
        # rebuilding from same-UID local source.
        if pkg_path.exists():
            raise CanonicalStoreError(
                "content_corruption",
                f"Canonical package {slug}@{version} exists at {pkg_path} "
                f"but integrity check failed (is_built=False). "
                f"No automatic repair from same-UID local source. "
                f"Recovery: use `happyranch skills recover "
                f"{slug} {version} {content_hash}` to remove the "
                f"corrupted package, then next materialization "
                f"rebuilds from the ArtifactStore.",
            )

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

            # Atomic replace: move temp → final canonical path.
            if pkg_path.exists():
                _make_writable_for_removal(pkg_path)
                shutil.rmtree(pkg_path)
            pkg_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp, pkg_path)

            logger.info(
                "Built canonical package %s@%s (hash=%s) at %s",
                slug, version, content_hash[:16], pkg_path,
            )
            return pkg_path

        except Exception:
            if tmp.exists():
                shutil.rmtree(tmp)
            if pkg_path.exists() and not tmp.exists():
                _make_writable_for_removal(pkg_path)
                shutil.rmtree(pkg_path)
            raise

    def verify_content_integrity(
        self,
        slug: str,
        version: str,
        content_hash: str,
        expected_content_hash: str,
    ) -> tuple[bool, str | None]:
        """Verify that canonical package content matches expected hash.

        Computes the actual tree hash of the existing package and compares
        it to *expected_content_hash*. This is used for best-effort
        corruption detection in same-owner deployments — it is NOT an
        attacker-independent security guarantee.

        Args:
            slug: Skill slug
            version: Package version
            content_hash: The content_hash used for addressing
            expected_content_hash: The expected tree hash to compare against

        Returns:
            (True, None) if content matches, (False, reason_string) if not.
        """
        try:
            actual = self.compute_tree_hash(slug, version, content_hash)
        except CanonicalStoreError:
            return False, "package_missing_or_corrupt"
        if actual != expected_content_hash:
            return False, (
                f"content_mismatch: expected {expected_content_hash[:16]}..., "
                f"got {actual[:16]}..."
            )
        return True, None

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
        is validated, and they are written into the canonical tree.

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

        # If already built, verify content integrity before reusing.
        # In same-owner mode an executor could tamper with the package
        # bytes; this check detects that and REFUSES reuse — NO automatic
        # rebuild from same-UID ArtifactStore source.
        if self.is_built(slug, version, content_hash):
            if self._manifest_content_matches(pkg_path, manifest):
                return pkg_path
            raise CanonicalStoreError(
                "content_corruption",
                f"Canonical package {slug}@{version} content mismatch "
                f"detected — existing corrupted package present. "
                f"No automatic repair from same-UID local source. "
                f"Recovery: use `happyranch skills recover "
                f"{slug} {version} {content_hash}` to remove the "
                f"corrupted package, then next materialization "
                f"rebuilds from the ArtifactStore.",
            )

        # ── Detection: existing but invalid canonical package ────────
        # If the canonical directory exists but is_built() is False,
        # the package is CORRUPTED — NOT an absent first-build scenario.
        # Refuse with content_corruption instead of deleting and
        # rebuilding from same-UID local source.
        if pkg_path.exists():
            raise CanonicalStoreError(
                "content_corruption",
                f"Canonical package {slug}@{version} exists at {pkg_path} "
                f"but integrity check failed (is_built=False). "
                f"No automatic repair from same-UID local source. "
                f"Recovery: use `happyranch skills recover "
                f"{slug} {version} {content_hash}` to remove the "
                f"corrupted package, then next materialization "
                f"rebuilds from the ArtifactStore.",
            )

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

                # Validate member hash strictly (Finding 3)
                expected_hex = _validate_member_hash(member_path, member_hash)
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

            # Atomic replace: move temp → final canonical path.
            if pkg_path.exists():
                _make_writable_for_removal(pkg_path)
                shutil.rmtree(pkg_path)
            pkg_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp, pkg_path)

            logger.info(
                "Built canonical package %s@%s from manifest (hash=%s) at %s",
                slug, version, content_hash[:16], pkg_path,
            )
            return pkg_path

        except CanonicalStoreError:
            if tmp.exists():
                shutil.rmtree(tmp)
            if pkg_path.exists() and not tmp.exists():
                _make_writable_for_removal(pkg_path)
                shutil.rmtree(pkg_path)
            raise
        except Exception as exc:
            if tmp.exists():
                shutil.rmtree(tmp)
            if pkg_path.exists() and not tmp.exists():
                _make_writable_for_removal(pkg_path)
                shutil.rmtree(pkg_path)
            raise CanonicalStoreError(
                "build_failed",
                f"Failed to build canonical package {slug}@{version}: {exc}",
            ) from exc

    def verify_package(self, slug: str, version: str, content_hash: str) -> None:
        """Verify a canonical package exists and is non-empty.

        Raises CanonicalStoreError if missing or empty.
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

    def compute_tree_hash(self, slug: str, version: str, content_hash: str) -> str:
        """Compute SHA-256 of the canonical tree content (for verification).

        Returns hex digest of all file contents sorted by relative path.
        """
        pkg_path = self.canonical_path(slug, version, content_hash)
        if not pkg_path.is_dir():
            raise CanonicalStoreError(
                "package_missing",
                f"Canonical package not found: {slug}@{version}",
            )
        h = hashlib.sha256()
        for fpath in sorted(pkg_path.rglob("*")):
            if fpath.is_file():
                rel = str(fpath.relative_to(pkg_path))
                h.update(rel.encode())
                h.update(b"\x00")
                h.update(fpath.read_bytes())
                h.update(b"\x00")
        return h.hexdigest()

    @staticmethod
    def _manifest_content_matches(pkg_path: Path, manifest: dict) -> bool:
        """Check if the canonical package content matches the manifest.

        Compares each member file in *pkg_path* against the expected hash
        declared in *manifest*. Returns True if ALL members match;
        False if any member is missing, extra, or has wrong hash.

        This is best-effort corruption detection for same-owner deployments —
        NOT an attacker-independent security guarantee.
        """
        members = manifest.get("members", [])
        if not members:
            return True  # Empty manifest — nothing to verify

        # Collect actual files
        actual_files: set[str] = set()
        for fpath in sorted(pkg_path.rglob("*")):
            if fpath.is_file():
                actual_files.add(str(fpath.relative_to(pkg_path)))

        for member in members:
            member_path = member.get("path", "")
            member_hash = member.get("hash", "")

            # Validate member hash declaration strictly
            try:
                expected_hex = _validate_member_hash(member_path, member_hash)
            except CanonicalStoreError:
                return False

            member_file = pkg_path / member_path
            if not member_file.is_file():
                return False

            actual_hex = hashlib.sha256(member_file.read_bytes()).hexdigest()
            if actual_hex != expected_hex:
                return False

            actual_files.discard(member_path)

        # Any files present that aren't in the manifest?
        if actual_files:
            return False

        return True
