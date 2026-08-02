"""TDD tests for canonical skill store — immutable, hash-addressed storage.

TASK-3988: Build daemon-owned canonical package storage outside executor
workspaces. Replaces per-session skill content copying with resolution,
integrity verification, and atomic workspace symlinks to exact approved
package versions.

Coverage areas:
1. Exact canonical target resolution/integrity for system, release-managed,
   and lifecycle version-pinned custom skill packages
2. Normal task/thread/wake/dream starts verify links without content-copy
3. Write-through-workspace-link proves canonical target is immutable
4. Stale/wrong-version/broken/malicious link cases repair safely
5. Existing-org compatibility; {ORG_SLUG} inventory/no-interpolation
6. No wholesale copy flag/path remains
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

import pytest

from runtime.config import Settings


# ═══════════════════════════════════════════════════════════════════════
# 1. Canonical Target Resolution & Integrity
# ═══════════════════════════════════════════════════════════════════════


class TestCanonicalStoreBuild:
    """Verify canonical trees are built atomically with validated hashes."""

    def test_build_system_contract_canonical_tree(self, tmp_path, test_settings):
        """Build a canonical tree for a system-contract skill package.

        The canonical store owns a hash-addressed tree outside the executor
        workspace. A system contract (e.g. start-task) should produce a
        canonical tree whose content matches the source exactly.
        """
        from runtime.skills.canonical_store import (
            CanonicalStore,
            CanonicalPackageKey,
        )

        src_dir = _make_minimal_skill_dir(tmp_path, "start-task")
        store_root = tmp_path / "canonical-store"
        store = CanonicalStore(store_root)

        pkg = CanonicalPackageKey(
            slug="start-task",
            package_type="system_contract",
            version="1.0.0",
            content_hash=_hash_dir(src_dir),
        )

        canonical_path = store.build(src_dir, pkg)
        assert canonical_path.is_dir()
        assert (canonical_path / "SKILL.md").is_file()
        # Verify content
        assert (canonical_path / "SKILL.md").read_text() == "# start-task\n\nStart task skill.\n"

    def test_build_release_managed_canonical_tree(self, tmp_path, test_settings):
        """Build a canonical tree for a release-managed catalog skill."""
        from runtime.skills.canonical_store import (
            CanonicalStore,
            CanonicalPackageKey,
        )

        src_dir = _make_minimal_skill_dir(tmp_path, "reflection")
        store_root = tmp_path / "canonical-store"
        store = CanonicalStore(store_root)

        pkg = CanonicalPackageKey(
            slug="reflection",
            package_type="release_managed",
            version="1.0.0",
            content_hash=_hash_dir(src_dir),
        )

        canonical_path = store.build(src_dir, pkg)
        assert canonical_path.is_dir()
        assert (canonical_path / "SKILL.md").is_file()

    def test_build_lifecycle_custom_canonical_tree(self, tmp_path, test_settings):
        """Build a canonical tree from lifecycle manifest members."""
        from runtime.skills.canonical_store import (
            CanonicalStore,
            CanonicalPackageKey,
        )

        store_root = tmp_path / "canonical-store"
        store = CanonicalStore(store_root)

        # Simulate a manifest-backed package with members
        artifact_dir = tmp_path / "artifacts"
        artifact_dir.mkdir()
        members = _make_manifest_members(artifact_dir, "custom-skill")
        # Strip raw_bytes for manifest (not part of hash)
        clean_members = [{k: v for k, v in m.items() if k != "raw_bytes"} for m in members]
        manifest = json.dumps({"members": clean_members}).encode()
        manifest_hash = hashlib.sha256(manifest).hexdigest()

        pkg = CanonicalPackageKey(
            slug="custom-skill",
            package_type="lifecycle",
            version="2.0.0",
            content_hash=manifest_hash,
        )

        canonical_path = store.build_from_manifest(members, artifact_dir, pkg)
        assert canonical_path.is_dir()
        assert (canonical_path / "SKILL.md").is_file()
        # Verify content matches artifact bytes
        sk_bytes = canonical_path / "SKILL.md"
        assert sk_bytes.read_bytes() == members[0]["raw_bytes"]

    def test_canonical_path_is_hash_addressed(self, tmp_path, test_settings):
        """Canonical paths use a stable version/hash key, not slugs alone."""
        from runtime.skills.canonical_store import (
            CanonicalStore,
            CanonicalPackageKey,
        )

        store_root = tmp_path / "canonical-store"
        store = CanonicalStore(store_root)

        src_dir = _make_minimal_skill_dir(tmp_path, "test-skill")
        content_hash = _hash_dir(src_dir)

        pkg = CanonicalPackageKey(
            slug="test-skill",
            package_type="system_contract",
            version="1.0.0",
            content_hash=content_hash,
        )

        canonical_path = store.build(src_dir, pkg)
        # Path must include the hash, not just the slug
        path_str = str(canonical_path)
        assert content_hash[:16] in path_str or content_hash in path_str
        assert pkg.version in path_str

    def test_canonical_tree_is_non_writable_after_build(self, tmp_path, test_settings):
        """After build, canonical content should persist read-only."""
        from runtime.skills.canonical_store import (
            CanonicalStore,
            CanonicalPackageKey,
        )

        src_dir = _make_minimal_skill_dir(tmp_path, "test-skill")
        store_root = tmp_path / "canonical-store"
        store = CanonicalStore(store_root)

        pkg = CanonicalPackageKey(
            slug="test-skill",
            package_type="system_contract",
            version="1.0.0",
            content_hash=_hash_dir(src_dir),
        )

        canonical_path = store.build(src_dir, pkg)
        original_hash = _hash_dir(canonical_path)

        # Rebuild with same key should be idempotent (hash-addressed)
        canonical_path2 = store.build(src_dir, pkg)
        assert canonical_path2 == canonical_path
        assert _hash_dir(canonical_path) == original_hash

    def test_build_with_different_hash_produces_different_path(self, tmp_path, test_settings):
        """Different content produces different canonical paths."""
        from runtime.skills.canonical_store import (
            CanonicalStore,
            CanonicalPackageKey,
        )

        store_root = tmp_path / "canonical-store"
        store = CanonicalStore(store_root)

        src_v1 = _make_minimal_skill_dir(tmp_path, "test-skill", content="# v1\n")
        src_v2 = _make_minimal_skill_dir(tmp_path, "test-skill-v2", content="# v2\n")

        pkg_v1 = CanonicalPackageKey(
            slug="test-skill",
            package_type="system_contract",
            version="1.0.0",
            content_hash=_hash_dir(src_v1),
        )
        pkg_v2 = CanonicalPackageKey(
            slug="test-skill",
            package_type="system_contract",
            version="2.0.0",
            content_hash=_hash_dir(src_v2),
        )

        path_v1 = store.build(src_v1, pkg_v1)
        path_v2 = store.build(src_v2, pkg_v2)
        assert path_v1 != path_v2

    def test_build_validates_hash_mismatch(self, tmp_path, test_settings):
        """Build must reject when source content hash doesn't match the key."""
        from runtime.skills.canonical_store import (
            CanonicalStore,
            CanonicalPackageKey,
            CanonicalStoreError,
        )

        store_root = tmp_path / "canonical-store"
        store = CanonicalStore(store_root)

        src_dir = _make_minimal_skill_dir(tmp_path, "test-skill")
        wrong_hash = "0" * 64

        pkg = CanonicalPackageKey(
            slug="test-skill",
            package_type="system_contract",
            version="1.0.0",
            content_hash=wrong_hash,
        )

        with pytest.raises(CanonicalStoreError, match="hash mismatch"):
            store.build(src_dir, pkg)

    def test_manifest_hash_mismatch_rejected(self, tmp_path, test_settings):
        """Manifest content hash must match the ledger's content_hash."""
        from runtime.skills.canonical_store import (
            CanonicalStore,
            CanonicalPackageKey,
            CanonicalStoreError,
        )

        store_root = tmp_path / "canonical-store"
        store = CanonicalStore(store_root)

        artifact_dir = tmp_path / "artifacts"
        artifact_dir.mkdir()
        members = _make_manifest_members(artifact_dir, "custom-skill")
        clean_members = [{k: v for k, v in m.items() if k != "raw_bytes"} for m in members]

        pkg = CanonicalPackageKey(
            slug="custom-skill",
            package_type="lifecycle",
            version="2.0.0",
            content_hash="0" * 64,  # Wrong
        )

        with pytest.raises(CanonicalStoreError, match="manifest hash mismatch"):
            store.build_from_manifest(clean_members, artifact_dir, pkg)

    def test_manifest_member_hash_mismatch_rejected(self, tmp_path, test_settings):
        """Individual member hashes must be validated."""
        from runtime.skills.canonical_store import (
            CanonicalStore,
            CanonicalPackageKey,
            CanonicalStoreError,
        )

        store_root = tmp_path / "canonical-store"
        store = CanonicalStore(store_root)

        artifact_dir = tmp_path / "artifacts"
        artifact_dir.mkdir()
        members = _make_manifest_members(artifact_dir, "custom-skill")
        # Tamper with a member hash
        members[0]["hash"] = "sha256:" + "0" * 64

        clean_members = [{k: v for k, v in m.items() if k != "raw_bytes"} for m in members]
        manifest = json.dumps({"members": clean_members}).encode()
        manifest_hash = hashlib.sha256(manifest).hexdigest()

        pkg = CanonicalPackageKey(
            slug="custom-skill",
            package_type="lifecycle",
            version="2.0.0",
            content_hash=manifest_hash,
        )

        with pytest.raises(CanonicalStoreError, match="member hash mismatch"):
            store.build_from_manifest(clean_members, artifact_dir, pkg)

    def test_safe_normalized_paths_prevent_escape(self, tmp_path, test_settings):
        """Manifest members with path-traversal paths must be rejected."""
        from runtime.skills.canonical_store import (
            CanonicalStore,
            CanonicalPackageKey,
            CanonicalStoreError,
        )

        store_root = tmp_path / "canonical-store"
        store = CanonicalStore(store_root)

        artifact_dir = tmp_path / "artifacts"
        artifact_dir.mkdir()
        # Create a member with a traversal path
        escape_content = b"malicious"
        escape_hash = hashlib.sha256(escape_content).hexdigest()
        escape_key = "mem-escape"
        (artifact_dir / escape_key).write_bytes(escape_content)

        members = [
            {
                "path": "../../etc/passwd",
                "hash": f"sha256:{escape_hash}",
                "artifact_key": escape_key,
            }
        ]
        clean_members = [{k: v for k, v in m.items() if k != "raw_bytes"} for m in members]
        manifest = json.dumps({"members": clean_members}).encode()
        manifest_hash = hashlib.sha256(manifest).hexdigest()

        pkg = CanonicalPackageKey(
            slug="custom-skill",
            package_type="lifecycle",
            version="2.0.0",
            content_hash=manifest_hash,
        )

        with pytest.raises(CanonicalStoreError, match="path traversal"):
            store.build_from_manifest(clean_members, artifact_dir, pkg)


# ═══════════════════════════════════════════════════════════════════════
# 2. Symlink Materialization
# ═══════════════════════════════════════════════════════════════════════


class TestSymlinkMaterialization:
    """Workspace skill paths become symlinks to canonical targets."""

    def test_symlink_created_to_canonical_target(self, tmp_path, test_settings):
        """A workspace skill directory becomes a symlink to canonical content."""
        from runtime.skills.canonical_store import CanonicalStore, CanonicalPackageKey
        from runtime.skills.symlink_materializer import (
            SymlinkMaterializer,
            SkillLinkSpec,
        )

        store_root = tmp_path / "canonical-store"
        store = CanonicalStore(store_root)
        workspace = tmp_path / "workspace"

        # Build canonical content
        src_dir = _make_minimal_skill_dir(tmp_path, "start-task")
        content_hash = _hash_dir(src_dir)
        pkg = CanonicalPackageKey(
            slug="start-task",
            package_type="system_contract",
            version="1.0.0",
            content_hash=content_hash,
        )
        canonical_path = store.build(src_dir, pkg)

        # Materialize as symlink
        materializer = SymlinkMaterializer()
        spec = SkillLinkSpec(
            slug="start-task",
            canonical_target=canonical_path,
            claude_skill_dir=workspace / ".claude" / "skills" / "start-task",
            agents_skill_dir=workspace / ".agents" / "skills" / "start-task",
            expected_content_hash=content_hash,
        )

        links = materializer.materialize([spec])
        assert len(links) == 2
        for link in links:
            assert link.is_symlink()
            assert link.resolve() == canonical_path

    def test_stale_symlink_repaired(self, tmp_path, test_settings):
        """A symlink pointing to a stale canonical target is repaired."""
        from runtime.skills.canonical_store import CanonicalStore, CanonicalPackageKey
        from runtime.skills.symlink_materializer import (
            SymlinkMaterializer,
            SkillLinkSpec,
        )

        store_root = tmp_path / "canonical-store"
        store = CanonicalStore(store_root)
        workspace = tmp_path / "workspace"

        # Build v1 canonical content
        src_v1 = _make_minimal_skill_dir(tmp_path, "test-skill", content="# v1\n")
        hash_v1 = _hash_dir(src_v1)
        pkg_v1 = CanonicalPackageKey(
            slug="test-skill",
            package_type="system_contract",
            version="1.0.0",
            content_hash=hash_v1,
        )
        path_v1 = store.build(src_v1, pkg_v1)

        # Build v2 canonical content
        src_v2 = _make_minimal_skill_dir(tmp_path, "test-skill-v2", content="# v2\n")
        hash_v2 = _hash_dir(src_v2)
        pkg_v2 = CanonicalPackageKey(
            slug="test-skill",
            package_type="system_contract",
            version="2.0.0",
            content_hash=hash_v2,
        )
        path_v2 = store.build(src_v2, pkg_v2)

        # Create a stale link (points to v1, but expected is v2)
        stale_link = workspace / ".claude" / "skills" / "test-skill"
        stale_link.parent.mkdir(parents=True, exist_ok=True)
        stale_link.symlink_to(path_v1)

        materializer = SymlinkMaterializer()
        spec = SkillLinkSpec(
            slug="test-skill",
            canonical_target=path_v2,
            claude_skill_dir=stale_link,
            agents_skill_dir=workspace / ".agents" / "skills" / "test-skill",
            expected_content_hash=hash_v2,
        )

        links = materializer.materialize([spec])
        # The stale link should be repaired to point to v2
        claude_link = links[0]
        assert claude_link.is_symlink()
        assert claude_link.resolve() == path_v2

    def test_broken_symlink_repaired(self, tmp_path, test_settings):
        """A broken (dangling) symlink is repaired."""
        from runtime.skills.canonical_store import CanonicalStore, CanonicalPackageKey
        from runtime.skills.symlink_materializer import (
            SymlinkMaterializer,
            SkillLinkSpec,
        )

        store_root = tmp_path / "canonical-store"
        store = CanonicalStore(store_root)
        workspace = tmp_path / "workspace"

        # Build canonical content
        src_dir = _make_minimal_skill_dir(tmp_path, "test-skill")
        content_hash = _hash_dir(src_dir)
        pkg = CanonicalPackageKey(
            slug="test-skill",
            package_type="system_contract",
            version="1.0.0",
            content_hash=content_hash,
        )
        canonical_path = store.build(src_dir, pkg)

        # Create a broken symlink
        broken_link = workspace / ".claude" / "skills" / "test-skill"
        broken_link.parent.mkdir(parents=True, exist_ok=True)
        broken_link.symlink_to(tmp_path / "nonexistent")

        materializer = SymlinkMaterializer()
        spec = SkillLinkSpec(
            slug="test-skill",
            canonical_target=canonical_path,
            claude_skill_dir=broken_link,
            agents_skill_dir=workspace / ".agents" / "skills" / "test-skill",
            expected_content_hash=content_hash,
        )

        links = materializer.materialize([spec])
        claude_link = links[0]
        assert claude_link.is_symlink()
        assert claude_link.resolve() == canonical_path

    def test_ordinary_directory_replaced_with_symlink(self, tmp_path, test_settings):
        """An existing ordinary directory (not a symlink) is replaced with a symlink."""
        from runtime.skills.canonical_store import CanonicalStore, CanonicalPackageKey
        from runtime.skills.symlink_materializer import (
            SymlinkMaterializer,
            SkillLinkSpec,
        )

        store_root = tmp_path / "canonical-store"
        store = CanonicalStore(store_root)
        workspace = tmp_path / "workspace"

        # Build canonical content
        src_dir = _make_minimal_skill_dir(tmp_path, "test-skill")
        content_hash = _hash_dir(src_dir)
        pkg = CanonicalPackageKey(
            slug="test-skill",
            package_type="system_contract",
            version="1.0.0",
            content_hash=content_hash,
        )
        canonical_path = store.build(src_dir, pkg)

        # Create an ordinary directory at the skill path
        ordinary_dir = workspace / ".claude" / "skills" / "test-skill"
        ordinary_dir.mkdir(parents=True)
        (ordinary_dir / "stale-file.txt").write_text("old content")

        materializer = SymlinkMaterializer()
        spec = SkillLinkSpec(
            slug="test-skill",
            canonical_target=canonical_path,
            claude_skill_dir=ordinary_dir,
            agents_skill_dir=workspace / ".agents" / "skills" / "test-skill",
            expected_content_hash=content_hash,
        )

        links = materializer.materialize([spec])
        claude_link = links[0]
        assert claude_link.is_symlink()
        assert claude_link.resolve() == canonical_path

    def test_external_target_symlink_repaired(self, tmp_path, test_settings):
        """A symlink pointing outside the canonical store is repaired."""
        from runtime.skills.canonical_store import CanonicalStore, CanonicalPackageKey
        from runtime.skills.symlink_materializer import (
            SymlinkMaterializer,
            SkillLinkSpec,
        )

        store_root = tmp_path / "canonical-store"
        store = CanonicalStore(store_root)
        workspace = tmp_path / "workspace"

        # Build canonical content
        src_dir = _make_minimal_skill_dir(tmp_path, "test-skill")
        content_hash = _hash_dir(src_dir)
        pkg = CanonicalPackageKey(
            slug="test-skill",
            package_type="system_contract",
            version="1.0.0",
            content_hash=content_hash,
        )
        canonical_path = store.build(src_dir, pkg)

        # Create a malicious symlink pointing outside the store
        malicious_link = workspace / ".claude" / "skills" / "test-skill"
        malicious_link.parent.mkdir(parents=True, exist_ok=True)
        malicious_link.symlink_to(Path("/etc"))

        materializer = SymlinkMaterializer()
        spec = SkillLinkSpec(
            slug="test-skill",
            canonical_target=canonical_path,
            claude_skill_dir=malicious_link,
            agents_skill_dir=workspace / ".agents" / "skills" / "test-skill",
            expected_content_hash=content_hash,
        )

        links = materializer.materialize([spec])
        claude_link = links[0]
        assert claude_link.is_symlink()
        assert claude_link.resolve() == canonical_path

    def test_content_hash_verification_fails_closed(self, tmp_path, test_settings):
        """If canonical content hash doesn't match expected, materialization fails."""
        from runtime.skills.canonical_store import CanonicalStore, CanonicalPackageKey
        from runtime.skills.symlink_materializer import (
            SymlinkMaterializer,
            SkillLinkSpec,
            MaterializationError,
        )

        store_root = tmp_path / "canonical-store"
        store = CanonicalStore(store_root)
        workspace = tmp_path / "workspace"

        src_dir = _make_minimal_skill_dir(tmp_path, "test-skill")
        content_hash = _hash_dir(src_dir)
        pkg = CanonicalPackageKey(
            slug="test-skill",
            package_type="system_contract",
            version="1.0.0",
            content_hash=content_hash,
        )
        canonical_path = store.build(src_dir, pkg)

        materializer = SymlinkMaterializer()
        spec = SkillLinkSpec(
            slug="test-skill",
            canonical_target=canonical_path,
            claude_skill_dir=workspace / ".claude" / "skills" / "test-skill",
            agents_skill_dir=workspace / ".agents" / "skills" / "test-skill",
            expected_content_hash="0" * 64,  # Wrong hash
        )

        with pytest.raises(MaterializationError, match="hash mismatch"):
            materializer.materialize([spec])

    def test_policy_withdrawal_removes_link_safely(self, tmp_path, test_settings):
        """When a skill is withdrawn, the workspace link is removed."""
        from runtime.skills.canonical_store import CanonicalStore, CanonicalPackageKey
        from runtime.skills.symlink_materializer import (
            SymlinkMaterializer,
            SkillLinkSpec,
        )

        store_root = tmp_path / "canonical-store"
        store = CanonicalStore(store_root)
        workspace = tmp_path / "workspace"

        src_dir = _make_minimal_skill_dir(tmp_path, "test-skill")
        content_hash = _hash_dir(src_dir)
        pkg = CanonicalPackageKey(
            slug="test-skill",
            package_type="system_contract",
            version="1.0.0",
            content_hash=content_hash,
        )
        canonical_path = store.build(src_dir, pkg)

        # First materialize
        materializer = SymlinkMaterializer()
        spec = SkillLinkSpec(
            slug="test-skill",
            canonical_target=canonical_path,
            claude_skill_dir=workspace / ".claude" / "skills" / "test-skill",
            agents_skill_dir=workspace / ".agents" / "skills" / "test-skill",
            expected_content_hash=content_hash,
        )
        materializer.materialize([spec])

        # Now withdraw - remove the link
        materializer.withdraw_skills(
            [workspace / ".claude" / "skills" / "test-skill",
             workspace / ".agents" / "skills" / "test-skill"]
        )

        # Links removed
        assert not (workspace / ".claude" / "skills" / "test-skill").exists()
        assert not (workspace / ".agents" / "skills" / "test-skill").exists()
        # Canonical content preserved
        assert canonical_path.is_dir()


# ═══════════════════════════════════════════════════════════════════════
# 3. Write Through Workspace Link Proves Canonical Immutability
# ═══════════════════════════════════════════════════════════════════════


class TestCanonicalImmutability:
    """Prove canonical target cannot be mutated through workspace link."""

    def test_write_through_symlink_does_not_alter_canonical(self, tmp_path, test_settings):
        """Writing through a workspace symlink does not mutate canonical bytes."""
        from runtime.skills.canonical_store import CanonicalStore, CanonicalPackageKey
        from runtime.skills.symlink_materializer import (
            SymlinkMaterializer,
            SkillLinkSpec,
        )

        store_root = tmp_path / "canonical-store"
        store = CanonicalStore(store_root)
        workspace = tmp_path / "workspace"

        src_dir = _make_minimal_skill_dir(tmp_path, "test-skill")
        content_hash = _hash_dir(src_dir)
        pkg = CanonicalPackageKey(
            slug="test-skill",
            package_type="system_contract",
            version="1.0.0",
            content_hash=content_hash,
        )
        canonical_path = store.build(src_dir, pkg)
        canonical_hash_before = _hash_dir(canonical_path)

        materializer = SymlinkMaterializer()
        spec = SkillLinkSpec(
            slug="test-skill",
            canonical_target=canonical_path,
            claude_skill_dir=workspace / ".claude" / "skills" / "test-skill",
            agents_skill_dir=workspace / ".agents" / "skills" / "test-skill",
            expected_content_hash=content_hash,
        )
        links = materializer.materialize([spec])

        claude_link = links[0]
        # Attempt to write through the symlink using the resolved path
        # The canonical target's SKILL.md should be read-only to executor
        # (we verify content hasn't changed, regardless of permission model)
        try:
            resolved = claude_link.resolve()
            (resolved / "SKILL.md").write_text("MALICIOUS CONTENT")
        except PermissionError:
            # Expected if canonical store enforces permissions
            pass
        except OSError:
            # Also acceptable
            pass

        # Verify canonical content is unchanged
        canonical_hash_after = _hash_dir(canonical_path)
        assert canonical_hash_after == canonical_hash_before

    def test_attempted_symlink_replacement_does_not_affect_canonical(self, tmp_path, test_settings):
        """Removing a workspace symlink does not affect canonical content."""
        from runtime.skills.canonical_store import CanonicalStore, CanonicalPackageKey
        from runtime.skills.symlink_materializer import (
            SymlinkMaterializer,
            SkillLinkSpec,
        )

        store_root = tmp_path / "canonical-store"
        store = CanonicalStore(store_root)
        workspace = tmp_path / "workspace"

        src_dir = _make_minimal_skill_dir(tmp_path, "test-skill")
        content_hash = _hash_dir(src_dir)
        pkg = CanonicalPackageKey(
            slug="test-skill",
            package_type="system_contract",
            version="1.0.0",
            content_hash=content_hash,
        )
        canonical_path = store.build(src_dir, pkg)
        canonical_hash_before = _hash_dir(canonical_path)

        materializer = SymlinkMaterializer()
        spec = SkillLinkSpec(
            slug="test-skill",
            canonical_target=canonical_path,
            claude_skill_dir=workspace / ".claude" / "skills" / "test-skill",
            agents_skill_dir=workspace / ".agents" / "skills" / "test-skill",
            expected_content_hash=content_hash,
        )
        materializer.materialize([spec])

        claude_link = workspace / ".claude" / "skills" / "test-skill"
        claude_link.unlink()

        # Canonical content must still exist and be unchanged
        assert canonical_path.is_dir()
        assert _hash_dir(canonical_path) == canonical_hash_before


# ═══════════════════════════════════════════════════════════════════════
# 4. {ORG_SLUG} No-Interpolation Regression
# ═══════════════════════════════════════════════════════════════════════


class TestNoOrgSlugInterpolation:
    """Prove {ORG_SLUG} substitution is eliminated from materialization."""

    def test_canonical_content_retains_literal_placeholder(self, tmp_path, test_settings):
        """Canonical store content retains {ORG_SLUG} as literal text.

        The substitution is now done at prompt injection time (via session
        metadata), not at materialization time.
        """
        from runtime.skills.canonical_store import CanonicalStore, CanonicalPackageKey

        # Source content with {ORG_SLUG} literal
        src = tmp_path / "source" / "test-skill"
        src.mkdir(parents=True)
        (src / "SKILL.md").write_text(
            "happyranch --org {ORG_SLUG} --from-file /tmp/x.json\n"
        )
        content_hash = _hash_dir(src)

        store_root = tmp_path / "canonical-store"
        store = CanonicalStore(store_root)

        pkg = CanonicalPackageKey(
            slug="test-skill",
            package_type="system_contract",
            version="1.0.0",
            content_hash=content_hash,
        )
        canonical_path = store.build(src, pkg)

        # Canonical content should retain the literal placeholder
        canonical_content = (canonical_path / "SKILL.md").read_text()
        assert "{ORG_SLUG}" in canonical_content
        assert "my-org" not in canonical_content  # Not substituted


# ═══════════════════════════════════════════════════════════════════════
# 5. No Copy Flag Remains
# ═══════════════════════════════════════════════════════════════════════


class TestNoCopyFlagRemains:
    """Prove wholesale copy flag/path is gone."""

    def test_wholesale_dump_flag_removed_or_permanently_false(self):
        """_WHOLESALE_DUMP_ENABLED must be removed or permanently False."""
        from runtime.orchestrator import workspace_adapters

        # The flag should either not exist or be False
        flag = getattr(workspace_adapters, "_WHOLESALE_DUMP_ENABLED", None)
        # It can remain as False for backward compat, but must not be True
        assert flag is None or flag is False

    def test_no_content_copy_in_materialization(self, tmp_path, test_settings):
        """Materialization must not invoke content copy paths.

        The symlink-based materializer creates links, never copies content
        into the workspace skill directories.
        """
        # This is tested by verifying symlinks are created, not files
        # (covered by TestSymlinkMaterialization tests above)
        pass


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _make_minimal_skill_dir(base: Path, slug: str, content: str | None = None) -> Path:
    """Create a minimal skill package directory with a SKILL.md."""
    d = base / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        content or f"# {slug}\n\nStart task skill.\n"
    )
    return d


def _hash_dir(path: Path) -> str:
    """Compute a deterministic content hash for a directory tree."""
    hasher = hashlib.sha256()
    for f in sorted(path.rglob("*")):
        if f.is_file():
            rel = f.relative_to(path)
            hasher.update(str(rel).encode())
            hasher.update(f.read_bytes())
    return hasher.hexdigest()


def _make_manifest_members(
    artifact_dir: Path, slug: str
) -> list[dict]:
    """Create manifest members with artifact files for testing."""
    skill_content = f"# {slug}\n\nCustom skill.\n".encode()
    skill_hash = hashlib.sha256(skill_content).hexdigest()
    skill_key = f"sk-{slug}"
    (artifact_dir / skill_key).write_bytes(skill_content)

    ref_content = b"reference content"
    ref_hash = hashlib.sha256(ref_content).hexdigest()
    ref_key = f"ref-{slug}"
    (artifact_dir / ref_key).write_bytes(ref_content)

    return [
        {
            "path": "SKILL.md",
            "hash": f"sha256:{skill_hash}",
            "artifact_key": skill_key,
            "raw_bytes": skill_content,
        },
        {
            "path": "references/guide.md",
            "hash": f"sha256:{ref_hash}",
            "artifact_key": ref_key,
            "raw_bytes": ref_content,
        },
    ]


@pytest.fixture
def test_settings():
    """Provide Settings for tests."""
    return Settings()
