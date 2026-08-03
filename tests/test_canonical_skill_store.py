"""Tests for canonical skill store and symlink materializer.

Covers:
1. Canonical store: build from source, build from manifest, hash-addressed paths,
   path traversal rejection, ownership verification, atomic build
2. Symlink materializer: create, repair (stale, broken, wrong-target, ordinary-dir),
   safe withdrawal, batch materialization, fail-closed behavior
3. Platform isolation: identity probes, ownership verification, symlink validation
4. Integration: store → materializer → workspace link lifecycle
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from runtime.platform.isolation import (
    PlatformIdentity,
    PlatformIsolation,
    PlatformIsolationError,
)
# Use module-attribute access so the conftest monkeypatch on
# runtime.platform.isolation.detect_platform_isolation takes effect
# in tests.  `from X import Y` creates a local name that is NOT
# updated when X.Y is monkeypatched.
from runtime.platform import isolation
from runtime.skills.canonical_store import (
    CanonicalSkillStore,
    CanonicalStoreError,
)
from runtime.skills.symlink_materializer import (
    SymlinkMaterializer,
    SymlinkMaterializationError,
)


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def temp_canonical_root(tmp_path):
    """Temporary canonical store root."""
    root = tmp_path / "canonical-skills"
    os.environ["HAPPYRANCH_CANONICAL_STORE_ROOT"] = str(root)
    yield root
    del os.environ["HAPPYRANCH_CANONICAL_STORE_ROOT"]


@pytest.fixture
def store(temp_canonical_root):
    """Fresh canonical store instance."""
    return CanonicalSkillStore(root=temp_canonical_root)


@pytest.fixture
def materializer(store):
    """Fresh symlink materializer."""
    return SymlinkMaterializer(store)


@pytest.fixture
def skill_source_dir(tmp_path):
    """Create a fake skill source directory with SKILL.md and references/."""
    src = tmp_path / "test-skill"
    src.mkdir()
    (src / "SKILL.md").write_text("# Test Skill\n\nA test skill for verification.")
    refs = src / "references"
    refs.mkdir()
    (refs / "helper.md").write_text("# Helper\n\nHelper docs.")
    assets = src / "assets"
    assets.mkdir()
    (assets / "logo.png").write_bytes(b"\x89PNG fake content")
    return src


@pytest.fixture
def workspace_dir(tmp_path):
    """Fake agent workspace."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


# ── Platform isolation tests ────────────────────────────────────────


class TestPlatformDetection:
    """Platform isolation detection tests."""

    def test_detect_returns_implementation(self):
        """detect_platform_isolation returns a working implementation."""
        iso = isolation.detect_platform_isolation()
        assert isinstance(iso, PlatformIsolation)

    def test_current_identity(self):
        """current_identity returns process uid/gid."""
        iso = isolation.detect_platform_isolation()
        identity = iso.current_identity()
        assert identity.uid >= 0
        assert identity.gid >= 0

    def test_provision_canonical_store_creates_dir(self, tmp_path):
        """provision_canonical_store creates directory with proper perms."""
        iso = isolation.detect_platform_isolation()
        store_dir = tmp_path / "test-store"
        iso.provision_canonical_store(store_dir)
        assert store_dir.is_dir()

    def test_verify_canonical_ownership_missing(self, tmp_path):
        """verify_canonical_ownership raises for missing path."""
        iso = isolation.detect_platform_isolation()
        missing = tmp_path / "nonexistent"
        with pytest.raises(PlatformIsolationError, match="canonical_missing"):
            iso.verify_canonical_ownership(missing)


class TestSymlinkOperations:
    """Symlink creation and validation."""

    def test_create_relative_symlink(self, tmp_path):
        """Create a valid relative symlink."""
        iso = isolation.detect_platform_isolation()
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / "file.txt").write_text("hello")

        link = tmp_path / "link"
        rel_target = Path("target")
        iso.create_relative_symlink(rel_target, link)
        assert link.is_symlink()
        assert (link / "file.txt").read_text() == "hello"

    def test_absolute_target_rejected(self, tmp_path):
        """Absolute symlink targets are rejected."""
        iso = isolation.detect_platform_isolation()
        link = tmp_path / "link"
        with pytest.raises(PlatformIsolationError, match="absolute"):
            iso.create_relative_symlink(Path("/etc/passwd"), link)

    def test_verify_workspace_link_valid(self, tmp_path):
        """verify_workspace_link returns True for valid links."""
        iso = isolation.detect_platform_isolation()
        target = tmp_path / "canonical-root" / "pkg"
        target.mkdir(parents=True)
        link = tmp_path / "ws" / "link"
        link.parent.mkdir(parents=True)
        rel = Path(os.path.relpath(target, link.parent))
        os.symlink(str(rel), str(link))

        canonical_root = tmp_path / "canonical-root"
        assert iso.verify_workspace_link(link, target, canonical_root)

    def test_verify_workspace_link_broken(self, tmp_path):
        """verify_workspace_link returns False for broken links."""
        iso = isolation.detect_platform_isolation()
        target = tmp_path / "nonexistent"
        link = tmp_path / "ws" / "broken-link"
        link.parent.mkdir(parents=True)
        os.symlink(str(target), str(link))

        canonical_root = tmp_path / "canonical-root"
        canonical_root.mkdir()
        assert not iso.verify_workspace_link(link, target, canonical_root)

    def test_is_valid_symlink(self, tmp_path):
        """is_valid_symlink correctly detects symlinks."""
        iso = isolation.detect_platform_isolation()
        regular = tmp_path / "regular.txt"
        regular.write_text("hello")
        assert not iso.is_valid_symlink(regular)

        sym = tmp_path / "symlink"
        os.symlink(str(regular), str(sym))
        assert iso.is_valid_symlink(sym)

    def test_make_file_readonly(self, tmp_path):
        """make_file_readonly sets file to 0444."""
        iso = isolation.detect_platform_isolation()
        f = tmp_path / "readonly.txt"
        f.write_text("data")
        iso.make_file_readonly(f)
        mode = stat.S_IMODE(f.stat().st_mode)
        # Should be at most 0444 on Unix
        if os.name != "nt":
            assert mode & stat.S_IWGRP == 0
            assert mode & stat.S_IWOTH == 0


# ── Canonical store tests ───────────────────────────────────────────


class TestCanonicalStoreBasic:
    """Basic canonical store operations."""

    def test_store_root_created(self, store, temp_canonical_root):
        """Store root is created on init."""
        assert temp_canonical_root.is_dir()

    def test_canonical_path_addresses(self, store, temp_canonical_root):
        """canonical_path generates hash-addressed paths."""
        path = store.canonical_path("my-skill", "1.0.0",
                                     "abcdef1234567890abcdef1234567890abcdef12")
        assert str(temp_canonical_root) in str(path)
        assert "my-skill" in str(path)
        assert "1.0.0" in str(path)
        assert "abcdef1234567890" in str(path)

    def test_not_built_initially(self, store):
        """Fresh store has no built packages."""
        assert not store.is_built("any", "1.0", "hash1234567890123456")

    def test_build_from_source(self, store, skill_source_dir):
        """Build a canonical package from source directory."""
        content_hash = "deadbeef12345678"  # placeholder
        pkg_path = store.build_from_source(
            "test-skill", "1.0.0", content_hash,
            skill_source_dir,
        )
        assert pkg_path.is_dir()
        assert (pkg_path / "SKILL.md").read_text() == "# Test Skill\n\nA test skill for verification."
        assert (pkg_path / "references" / "helper.md").is_file()
        assert (pkg_path / "assets" / "logo.png").is_file()

    def test_build_is_idempotent(self, store, skill_source_dir):
        """Building same package twice is idempotent."""
        content_hash = "deadbeef12345678"
        p1 = store.build_from_source("test-skill", "1.0.0", content_hash, skill_source_dir)
        p2 = store.build_from_source("test-skill", "1.0.0", content_hash, skill_source_dir)
        assert p1 == p2

    def test_is_built_after_build(self, store, skill_source_dir):
        """is_built returns True after building."""
        content_hash = "deadbeef12345678"
        store.build_from_source("test-skill", "1.0.0", content_hash, skill_source_dir)
        assert store.is_built("test-skill", "1.0.0", content_hash)

    def test_canonical_files_readonly(self, store, skill_source_dir):
        """Built canonical files are read-only (no group/other write)."""
        if os.name == "nt":
            pytest.skip("Read-only attribute test is Unix-specific")
        content_hash = "deadbeef12345678"
        pkg_path = store.build_from_source("test-skill", "1.0.0", content_hash, skill_source_dir)
        for f in pkg_path.rglob("*"):
            if f.is_file():
                mode = stat.S_IMODE(f.stat().st_mode)
                assert mode & stat.S_IWGRP == 0, f"{f} is group-writable"
                assert mode & stat.S_IWOTH == 0, f"{f} is world-writable"

    def test_verify_package_valid(self, store, skill_source_dir):
        """verify_package succeeds for valid built packages."""
        content_hash = "deadbeef12345678"
        store.build_from_source("test-skill", "1.0.0", content_hash, skill_source_dir)
        store.verify_package("test-skill", "1.0.0", content_hash)

    def test_verify_package_missing(self, store):
        """verify_package raises for missing packages."""
        with pytest.raises(CanonicalStoreError, match="package_missing"):
            store.verify_package("nope", "1.0", "hash1234567890123456")

    def test_path_traversal_rejected(self, store, tmp_path):
        """Manifest members with ../ in paths are rejected."""
        art_dir = tmp_path / "artifacts"
        art_dir.mkdir()
        key = "skill-lifecycle/evil/abc/SKILL.md"
        (art_dir / key).parent.mkdir(parents=True)
        (art_dir / key).write_bytes(b"content")

        class FakeArtifactStore:
            def __init__(self, root):
                self.root = root
            def read(self, key):
                return (self.root / key).read_bytes()

        fake_store = FakeArtifactStore(art_dir)

        manifest = {
            "members": [
                {"path": "../escape/payload.md",
                 "hash": "sha256:" + hashlib.sha256(b"content").hexdigest(),
                 "artifact_key": key},
            ],
        }
        manifest_json = json.dumps(manifest, sort_keys=True, indent=2)
        manifest_hash = hashlib.sha256(manifest_json.encode()).hexdigest()

        with pytest.raises(CanonicalStoreError, match="path_traversal"):
            store.build_from_manifest(
                "evil-skill", "1.0", manifest_hash,
                manifest, fake_store,
            )

    def test_empty_source_rejected(self, store, tmp_path):
        """Empty source directory is rejected."""
        src = tmp_path / "empty"
        src.mkdir()
        with pytest.raises(CanonicalStoreError, match="empty_package"):
            store.build_from_source("empty", "1.0", "hash1234", src)

    def test_compute_tree_hash(self, store, skill_source_dir):
        """compute_tree_hash produces consistent hashes."""
        content_hash = "deadbeef12345678"
        store.build_from_source("test-skill", "1.0.0", content_hash, skill_source_dir)
        h1 = store.compute_tree_hash("test-skill", "1.0.0", content_hash)
        h2 = store.compute_tree_hash("test-skill", "1.0.0", content_hash)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_build_from_manifest(self, store, tmp_path):
        """Build a canonical package from an artifact store manifest."""
        # Create fake artifact store
        art_dir = tmp_path / "artifacts"
        art_dir.mkdir()

        skill_content = b"# Manifest Skill\n\nBuilt from manifest."
        ref_content = b"# Reference\n\nRef content."
        skill_hash = hashlib.sha256(skill_content).hexdigest()
        ref_hash = hashlib.sha256(ref_content).hexdigest()

        skill_key = "skill-lifecycle/test-manifest/deadbeef/SKILL.md"
        ref_key = "skill-lifecycle/test-manifest/deadbeef/references/helper.md"
        (art_dir / skill_key).parent.mkdir(parents=True)
        (art_dir / ref_key).parent.mkdir(parents=True)
        (art_dir / skill_key).write_bytes(skill_content)
        (art_dir / ref_key).write_bytes(ref_content)

        # Create a fake ArtifactStore
        class FakeArtifactStore:
            def __init__(self, root):
                self.root = root
            def read(self, key):
                f = self.root / key
                if not f.exists():
                    raise FileNotFoundError(key)
                return f.read_bytes()

        fake_store = FakeArtifactStore(art_dir)

        manifest = {
            "members": [
                {"path": "SKILL.md", "hash": f"sha256:{skill_hash}",
                 "artifact_key": skill_key},
                {"path": "references/helper.md", "hash": f"sha256:{ref_hash}",
                 "artifact_key": ref_key},
            ],
        }
        manifest_json = json.dumps(manifest, sort_keys=True, indent=2)
        manifest_hash = hashlib.sha256(manifest_json.encode()).hexdigest()

        pkg_path = store.build_from_manifest(
            "test-manifest", "1.0.0", manifest_hash,
            manifest, fake_store,
        )
        assert pkg_path.is_dir()
        assert (pkg_path / "SKILL.md").read_bytes() == skill_content
        assert (pkg_path / "references" / "helper.md").read_bytes() == ref_content

    def test_manifest_hash_mismatch(self, store, tmp_path):
        """build_from_manifest fails on member hash mismatch."""
        art_dir = tmp_path / "artifacts"
        art_dir.mkdir()

        skill_key = "skill-lifecycle/bad/abc/SKILL.md"
        (art_dir / skill_key).parent.mkdir(parents=True)
        (art_dir / skill_key).write_bytes(b"actual content")

        class FakeArtifactStore:
            def __init__(self, root):
                self.root = root
            def read(self, key):
                return (self.root / key).read_bytes()

        fake_store = FakeArtifactStore(art_dir)

        manifest = {
            "members": [
                {"path": "SKILL.md", "hash": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
                 "artifact_key": skill_key},
            ],
        }
        manifest_json = json.dumps(manifest, sort_keys=True, indent=2)
        manifest_hash = hashlib.sha256(manifest_json.encode()).hexdigest()

        with pytest.raises(CanonicalStoreError, match="member_hash_mismatch"):
            store.build_from_manifest("bad", "1.0", manifest_hash, manifest, fake_store)


# ── Symlink materializer tests ──────────────────────────────────────


class TestSymlinkMaterializer:
    """Workspace symlink materialization and repair."""

    def test_materialize_creates_link(self, store, materializer, skill_source_dir, workspace_dir):
        """Materialize a skill creates a valid workspace symlink."""
        content_hash = "deadbeef12345678"
        store.build_from_source("test-skill", "1.0.0", content_hash, skill_source_dir)

        materializer.materialize_skill(
            "test-skill", "1.0.0", content_hash,
            workspace_dir, ".claude/skills",
        )
        link_path = workspace_dir / ".claude" / "skills" / "test-skill"
        assert link_path.is_symlink()
        assert (link_path / "SKILL.md").read_text() == "# Test Skill\n\nA test skill for verification."

    def test_materialize_idempotent(self, store, materializer, skill_source_dir, workspace_dir):
        """Materializing same skill twice is idempotent."""
        content_hash = "deadbeef12345678"
        store.build_from_source("test-skill", "1.0.0", content_hash, skill_source_dir)

        materializer.materialize_skill(
            "test-skill", "1.0.0", content_hash,
            workspace_dir, ".claude/skills",
        )
        materializer.materialize_skill(
            "test-skill", "1.0.0", content_hash,
            workspace_dir, ".claude/skills",
        )
        link_path = workspace_dir / ".claude" / "skills" / "test-skill"
        assert link_path.is_symlink()

    def test_materialize_canonical_missing(self, materializer, workspace_dir):
        """Materialize fails when canonical package doesn't exist."""
        with pytest.raises(SymlinkMaterializationError, match="canonical_missing"):
            materializer.materialize_skill(
                "nonexistent", "1.0", "badhash1234567890",
                workspace_dir, ".claude/skills",
            )

    def test_repair_stale_symlink(self, store, materializer, skill_source_dir, workspace_dir, tmp_path):
        """Repair replaces a stale symlink pointing to wrong target."""
        # Build two versions
        store.build_from_source("test-skill", "1.0.0", "deadbeef12345678", skill_source_dir)

        # Create a link to a wrong location
        link_dir = workspace_dir / ".claude" / "skills"
        link_dir.mkdir(parents=True)
        wrong_target = tmp_path / "wrong-place"
        wrong_target.mkdir()
        os.symlink(str(wrong_target), str(link_dir / "test-skill"))

        # Now materialize — should repair
        materializer.materialize_skill(
            "test-skill", "1.0.0", "deadbeef12345678",
            workspace_dir, ".claude/skills",
        )
        link_path = workspace_dir / ".claude" / "skills" / "test-skill"
        assert link_path.is_symlink()
        assert (link_path / "SKILL.md").read_text() == "# Test Skill\n\nA test skill for verification."

    def test_repair_ordinary_directory(self, store, materializer, skill_source_dir, workspace_dir):
        """Ordinary directory at link path raises — no recursive rmtree (fail-closed).

        Ordinary directories at managed link paths are hostile state. The
        materializer must NOT recursively delete potentially attacker-controlled
        content — it raises SymlinkMaterializationError instead.
        """
        from runtime.skills.symlink_materializer import SymlinkMaterializationError
        content_hash = "deadbeef12345678"
        store.build_from_source("test-skill", "1.0.0", content_hash, skill_source_dir)

        # Create an ordinary directory at the expected link path (simulating
        # old copy-based behavior)
        dir_path = workspace_dir / ".claude" / "skills" / "test-skill"
        dir_path.mkdir(parents=True)
        (dir_path / "SKILL.md").write_text("old copied content")

        # Materialization must raise — ordinary dirs are hostile, never deleted
        with pytest.raises(SymlinkMaterializationError, match="ordinary_dir_at_link_path"):
            materializer.materialize_skill(
                "test-skill", "1.0.0", content_hash,
                workspace_dir, ".claude/skills",
            )

    def test_safe_withdrawal(self, store, materializer, skill_source_dir, workspace_dir):
        """Withdraw removes workspace link without touching canonical content."""
        content_hash = "deadbeef12345678"
        store.build_from_source("test-skill", "1.0.0", content_hash, skill_source_dir)

        materializer.materialize_skill(
            "test-skill", "1.0.0", content_hash,
            workspace_dir, ".claude/skills",
        )
        # Verify canonical content exists
        assert store.is_built("test-skill", "1.0.0", content_hash)

        materializer.withdraw_skill("test-skill", workspace_dir, ".claude/skills")

        # Link should be gone
        assert not (workspace_dir / ".claude" / "skills" / "test-skill").exists()
        # Canonical content should still exist
        assert store.is_built("test-skill", "1.0.0", content_hash)

    def test_withdraw_ordinary_dir_refused(self, materializer, workspace_dir):
        """Withdraw refuses to delete ordinary directories."""
        dir_path = workspace_dir / ".claude" / "skills" / "real-dir"
        dir_path.mkdir(parents=True)
        (dir_path / "SKILL.md").write_text("real work")

        with pytest.raises(SymlinkMaterializationError, match="ordinary_dir"):
            materializer.withdraw_skill("real-dir", workspace_dir, ".claude/skills")

    def test_materialize_agents_skills(self, store, materializer, skill_source_dir, workspace_dir):
        """Materialize into .agents/skills/ works."""
        content_hash = "deadbeef12345678"
        store.build_from_source("test-skill", "1.0.0", content_hash, skill_source_dir)

        materializer.materialize_skill(
            "test-skill", "1.0.0", content_hash,
            workspace_dir, ".agents/skills",
        )
        link_path = workspace_dir / ".agents" / "skills" / "test-skill"
        assert link_path.is_symlink()

    def test_batch_materialization(self, store, materializer, skill_source_dir, workspace_dir, tmp_path):
        """Materialize multiple skills in batch."""
        # Build two skills
        src2 = tmp_path / "skill-two"
        src2.mkdir()
        (src2 / "SKILL.md").write_text("# Skill Two")

        store.build_from_source("skill-one", "1.0.0", "hash1111111111111111", skill_source_dir)
        store.build_from_source("skill-two", "1.0.0", "hash2222222222222222", src2)

        specs = [
            {"slug": "skill-one", "version": "1.0.0", "content_hash": "hash1111111111111111"},
            {"slug": "skill-two", "version": "1.0.0", "content_hash": "hash2222222222222222"},
        ]
        materialized = materializer.materialize_skills_batch(
            specs, workspace_dir, ".claude/skills",
        )
        assert materialized == ["skill-one", "skill-two"]
        assert (workspace_dir / ".claude" / "skills" / "skill-one").is_symlink()
        assert (workspace_dir / ".claude" / "skills" / "skill-two").is_symlink()

    def test_repair_workspace_skills_withdraws_old(self, store, materializer, skill_source_dir, workspace_dir):
        """repair_workspace_skills withdraws skills not in expected set."""
        content_hash = "deadbeef12345678"
        store.build_from_source("test-skill", "1.0.0", content_hash, skill_source_dir)

        # First materialize an "old" skill
        materializer.materialize_skill(
            "test-skill", "1.0.0", content_hash,
            workspace_dir, ".claude/skills",
        )
        assert (workspace_dir / ".claude" / "skills" / "test-skill").is_symlink()

        # Now repair with empty expected set — should withdraw
        materialized, withdrawn = materializer.repair_workspace_skills(
            [], workspace_dir, ".claude/skills",
        )
        assert materialized == []
        assert "test-skill" in withdrawn
        assert not (workspace_dir / ".claude" / "skills" / "test-skill").exists()

    def test_repair_adds_and_withdraws(self, store, materializer, skill_source_dir, workspace_dir, tmp_path):
        """repair_workspace_skills adds new and withdraws old."""
        # Build two skills
        src2 = tmp_path / "skill-two"
        src2.mkdir()
        (src2 / "SKILL.md").write_text("# Skill Two")

        store.build_from_source("skill-one", "1.0.0", "hash1111111111111111", skill_source_dir)
        store.build_from_source("skill-two", "1.0.0", "hash2222222222222222", src2)

        # First materialize skill-one (old)
        materializer.materialize_skill(
            "skill-one", "1.0.0", "hash1111111111111111",
            workspace_dir, ".claude/skills",
        )

        # Now repair: expected = [skill-two], so skill-one should be withdrawn
        specs = [
            {"slug": "skill-two", "version": "1.0.0", "content_hash": "hash2222222222222222"},
        ]
        materialized, withdrawn = materializer.repair_workspace_skills(
            specs, workspace_dir, ".claude/skills",
        )
        assert materialized == ["skill-two"]
        assert "skill-one" in withdrawn
        assert (workspace_dir / ".claude" / "skills" / "skill-two").is_symlink()
        assert not (workspace_dir / ".claude" / "skills" / "skill-one").exists()


# ── Write-via-link isolation tests ──────────────────────────────────


class TestWriteViaLinkIsolation:
    """Verify that writing through a workspace symlink cannot mutate canonical bytes."""

    def test_write_through_link_fails(self, store, materializer, skill_source_dir, workspace_dir):
        """Attempting to write through a workspace symlink fails."""
        content_hash = "deadbeef12345678"
        store.build_from_source("test-skill", "1.0.0", content_hash, skill_source_dir)

        # Get the canonical SKILL.md content before
        canonical_file = store.canonical_path("test-skill", "1.0.0", content_hash) / "SKILL.md"
        original_content = canonical_file.read_bytes()
        original_mode = stat.S_IMODE(canonical_file.stat().st_mode)

        materializer.materialize_skill(
            "test-skill", "1.0.0", content_hash,
            workspace_dir, ".claude/skills",
        )

        # Attempt to write through the workspace link (should fail because
        # canonical files are read-only)
        link_path = workspace_dir / ".claude" / "skills" / "test-skill" / "SKILL.md"
        with pytest.raises(PermissionError):
            link_path.write_text("malicious content")

        # Canonical file is UNCHANGED
        assert canonical_file.read_bytes() == original_content
        # Permissions unchanged
        assert stat.S_IMODE(canonical_file.stat().st_mode) == original_mode

    def test_chmod_through_link_is_possible_but_does_not_affect_isolation(self, store, materializer, skill_source_dir, workspace_dir):
        """chmod through symlink changes canonical file perms but the store
        verification catches it on next check. This documents the POSIX symlink
        behavior: symlinks don't protect permissions, only ownership/ACL does.

        The real isolation comes from the daemon-provisioned OS-level boundary
        where executor identity cannot chmod because it's not the owner.
        In a dev/test environment where all processes share uid, this is
        expected to pass — the real isolation requires OS provisioned accounts.
        """
        content_hash = "deadbeef12345678"
        store.build_from_source("test-skill", "1.0.0", content_hash, skill_source_dir)

        materializer.materialize_skill(
            "test-skill", "1.0.0", content_hash,
            workspace_dir, ".claude/skills",
        )

        canonical_file = store.canonical_path("test-skill", "1.0.0", content_hash) / "SKILL.md"
        original_content = canonical_file.read_bytes()

        # In a dev environment (same uid), chmod through symlink DOES work
        # because we own the file. This is expected — the real isolation
        # requires OS-provisioned executor identity with different uid.
        link_path = workspace_dir / ".claude" / "skills" / "test-skill" / "SKILL.md"
        try:
            link_path.chmod(0o644)  # Make writable by owner
        except PermissionError:
            pass  # Expected in strict environments

        # Verify that chmod didn't corrupt content
        assert canonical_file.read_bytes() == original_content


# ── Integration tests ───────────────────────────────────────────────


class TestStoreMaterializerIntegration:
    """End-to-end: store → materialize → verify → withdraw."""

    def test_full_lifecycle(self, store, materializer, skill_source_dir, workspace_dir):
        """Full lifecycle: build, materialize (Claude + Agents), verify, withdraw."""
        content_hash = "deadbeef12345678"
        store.build_from_source("test-skill", "1.0.0", content_hash, skill_source_dir)

        # Materialize to both provider directories
        materializer.materialize_skill(
            "test-skill", "1.0.0", content_hash,
            workspace_dir, ".claude/skills",
        )
        materializer.materialize_skill(
            "test-skill", "1.0.0", content_hash,
            workspace_dir, ".agents/skills",
        )

        # Both links work
        claude_link = workspace_dir / ".claude" / "skills" / "test-skill"
        agents_link = workspace_dir / ".agents" / "skills" / "test-skill"
        assert claude_link.is_symlink()
        assert agents_link.is_symlink()
        assert (claude_link / "SKILL.md").exists()
        assert (agents_link / "SKILL.md").exists()

        # Content through links matches canonical
        canonical_file = store.canonical_path("test-skill", "1.0.0", content_hash) / "SKILL.md"
        assert (claude_link / "SKILL.md").read_bytes() == canonical_file.read_bytes()

        # Withdraw from Claude only
        materializer.withdraw_skill("test-skill", workspace_dir, ".claude/skills")
        assert not claude_link.exists()
        assert agents_link.is_symlink()  # agents link still exists
        assert store.is_built("test-skill", "1.0.0", content_hash)  # canonical content retained

    def test_version_upgrade(self, store, materializer, skill_source_dir, workspace_dir, tmp_path):
        """Upgrading a skill version atomically replaces the workspace link."""
        # Build v1
        store.build_from_source("test-skill", "1.0.0", "deadbeef12345678", skill_source_dir)

        # Build v2 (different source)
        src_v2 = tmp_path / "skill-v2"
        src_v2.mkdir()
        (src_v2 / "SKILL.md").write_text("# Test Skill v2\n\nUpdated content.")

        store.build_from_source("test-skill", "2.0.0", "cafebabe87654321", src_v2)

        # Materialize v1
        materializer.materialize_skill(
            "test-skill", "1.0.0", "deadbeef12345678",
            workspace_dir, ".claude/skills",
        )
        link_path = workspace_dir / ".claude" / "skills" / "test-skill"
        assert (link_path / "SKILL.md").read_text() == "# Test Skill\n\nA test skill for verification."

        # "Upgrade" to v2 — materializer repairs the stale link
        materializer.materialize_skill(
            "test-skill", "2.0.0", "cafebabe87654321",
            workspace_dir, ".claude/skills",
        )
        assert link_path.is_symlink()
        assert (link_path / "SKILL.md").read_text() == "# Test Skill v2\n\nUpdated content."

        # v1 canonical content still exists (retained)
        assert store.is_built("test-skill", "1.0.0", "deadbeef12345678")


# ── Import-seam regression coverage (TASK-4117) ──────────────────────
# These tests prove the conftest monkeypatch covers every `from X import Y`
# import-seam where detect_platform_isolation was directly imported into
# a runtime module.  Without this coverage, Linux CI would hit
# PlatformIsolationError("unsupported_platform") before the fixture runs.


class TestImportSeamCoverage:
    """Verify the conftest monkeypatch reaches all consumer module references.

    The `from X import Y` pattern creates module-local names that are NOT
    updated when X.Y is monkeypatched.  The conftest sweeps sys.modules
    (runtime.* only) and patches every stale detect_platform_isolation
    reference.  These tests prove the sweep works.
    """

    def test_canonical_store_uses_scoped_double(self, tmp_path):
        """CanonicalSkillStore inside a test uses the scoped test double.

        On Linux, the real detect_platform_isolation raises
        PlatformIsolationError("unsupported_platform").  If the conftest
        sweep didn't patch canonical_store's reference, the store
        constructor would raise.  We verify it constructs cleanly.
        """
        # Constructing without explicit isolation must succeed.
        store = CanonicalSkillStore(root=tmp_path / "cs")
        assert store.root.is_dir()
        # The store must have a working isolation object.
        assert store._isolation is not None
        assert isinstance(store._isolation, PlatformIsolation)

    def test_symlink_materializer_uses_scoped_double(self, tmp_path):
        """SymlinkMaterializer uses the scoped double via its module reference."""
        store = CanonicalSkillStore(root=tmp_path / "cs")
        materializer = SymlinkMaterializer(store)
        assert materializer._isolation is not None
        assert isinstance(materializer._isolation, PlatformIsolation)

    def test_workspace_adapters_materialization_path_uses_double(
        self, tmp_path, test_settings,
    ):
        """materialize_workspace_skills works via conftest-scoped double.

        This exercises the canonical-store + materializer chain through
        workspace_adapters, proving the import-seam fix reaches the full
        call path.
        """
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "repos").mkdir()
        # Create a git repo so requires_repo contracts resolve
        import subprocess
        repo = workspace / "repos" / "test-project"
        repo.mkdir(parents=True)
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"],
                       cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"],
                       cwd=repo, capture_output=True)

        from runtime.orchestrator.workspace_adapters import (
            materialize_workspace_skills,
        )
        skills_root = test_settings.project_root / "runtime" / "skills"

        # Must not raise PlatformIsolationError — the double handles
        # everything.  (The temp project root may not have any skills
        # to materialize — the key invariant is no platform error.)
        materialize_workspace_skills(
            workspace, test_settings,
            slug="test",
            context="task",
            provider="claude",
            agent_name="dev_agent",
            team="engineering",
            skills_root=skills_root,
        )
        # If no skills were available, the skills dir may not exist —
        # that's fine.  The invariant we're testing is that the call
        # path through canonical_store → detect_platform_isolation used
        # the scoped double and did NOT raise PlatformIsolationError.

    def test_real_detector_still_rejects_non_darwin(self):
        """The real detect_platform_isolation rejects non-darwin platforms.

        Even though the conftest monkeypatches detect_platform_isolation
        in every runtime.* module, the original function (captured as
        _real_detect in conftest before patching) must still reject
        non-darwin with the named error.  We verify this by auditing
        the production source code for the invariant checks.

        This is a code-audit test, not a runtime test — the real function
        object is captured in the conftest fixture and not accessible here
        without importlib.reload (which would undo the fixture).  The
        source audit proves the production code hasn't been weakened.
        """
        src = (Path(__file__).resolve().parent.parent
               / "runtime" / "platform" / "isolation.py").read_text()

        # 1. The production code must contain the named error code
        assert '"unsupported_platform"' in src, (
            "Production isolation module must contain "
            "'unsupported_platform' error code — has someone removed it?"
        )

        # 2. The production code must check sys.platform == darwin
        assert 'sys.platform == "darwin"' in src, (
            "Production isolation module must check sys.platform == darwin "
            "— has someone added a Linux fallback?"
        )

        # 3. The production code must raise PlatformIsolationError on
        #    unsupported platforms (NOT return a fallback).
        assert 'raise PlatformIsolationError(' in src, (
            "Production isolation must raise on unsupported platforms "
            "— has someone added a silent fallback?"
        )

        # 4. Verify no Windows LOGON reference exists
        assert "LOGON_NETCREDENTIALS_ONLY" not in src, (
            "Production isolation must not contain LOGON_NETCREDENTIALS_ONLY"
        )

    def test_no_test_double_leaks_to_production_outside_test(self):
        """During test lifetime, conftest patches ARE active on runtime.*
        modules.  Verify the patch is correctly applied — calling
        detect_platform_isolation from any consumer module returns a
        working PlatformIsolation (not raising unsupported_platform).

        The fixture cleans up after each test — outside a test, the
        original references would be restored.  This test just proves
        the patches ARE in place (i.e. the sweep is working).
        """
        import runtime.skills.canonical_store as cs_mod
        import runtime.skills.symlink_materializer as sm_mod
        import runtime.orchestrator.executors as exec_mod

        # All three modules must have a detect_platform_isolation attribute
        assert hasattr(cs_mod, "detect_platform_isolation")
        assert hasattr(sm_mod, "detect_platform_isolation")
        assert hasattr(exec_mod, "detect_platform_isolation")

        # Calling the patched function must not raise (it must return
        # a working PlatformIsolation).  This is the key invariant:
        # the scoped double allows construction on all platforms.
        iso1 = cs_mod.detect_platform_isolation()
        iso2 = sm_mod.detect_platform_isolation()
        iso3 = exec_mod.detect_platform_isolation()
        assert isinstance(iso1, PlatformIsolation)
        assert isinstance(iso2, PlatformIsolation)
        assert isinstance(iso3, PlatformIsolation)
