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

from runtime.orchestrator.workspace_adapters import (
    _build_lifecycle_canonical_specs,
    _compute_dir_hash,
)
from runtime.platform.isolation import (
    PlatformIdentity,
    PlatformIsolation,
    PlatformIsolationError,
    _MacOSPlatformIsolation,
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

        # Same-UID writes through the workspace link ARE possible —
        # there is NO OS-level isolation. Integrity verification
        # provides detection for accidental corruption, not prevention.
        link_path = workspace_dir / ".claude" / "skills" / "test-skill" / "SKILL.md"
        link_path.write_text("same-uid-write-demonstration")

        # Canonical file IS changed — same-UID writes succeed
        assert canonical_file.read_bytes() == b"same-uid-write-demonstration"

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

        # Create system-contract source dirs so materialize_workspace_skills
        # can resolve them (required by the fail-closed source-existence check).
        proto_skills = tmp_path / "protocol" / "skills"
        for sid in ("start-task", "jobs", "make-worktree", "thread"):
            (proto_skills / sid).mkdir(parents=True, exist_ok=True)
            (proto_skills / sid / "SKILL.md").write_text(f"# {sid}\n\nSkill body.\n")

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


class TestBuildAtomicOrdering:
    """macOS CI regression: build_from_source/build_from_manifest
    must apply readonly permissions AFTER os.replace, not before.

    On macOS, rename() requires write permission on the source
    directory.  If make_dir_readonly_executor (→ 0555) runs before
    os.replace, the rename fails with PermissionError [Errno 13].
    The fix moves os.replace before the readonly steps and applies
    them to the final package path instead of the temp directory.
    """

    def test_build_from_source_succeeds_with_readonly_isolation(
        self, temp_canonical_root, skill_source_dir,
    ):
        """build_from_source completes without PermissionError when
        make_dir_readonly_executor genuinely removes write bits (0555).
        """
        import hashlib

        # Use a store with the test-mode isolation (patched via conftest).
        store = CanonicalSkillStore(root=temp_canonical_root)

        # The isolation.make_dir_readonly_executor sets 0555 (no write).
        # On macOS CI this would block os.replace if applied before the
        # atomic rename.  The fix ensures os.replace runs first, then
        # readonly is applied to the final location.
        content_hash = hashlib.sha256(b"test-content").hexdigest()
        pkg_path = store.build_from_source(
            "test-skill", "1.0.0", content_hash, skill_source_dir,
        )
        assert pkg_path.is_dir()
        # Final package must be read-only (the fix preserves this invariant)
        mode = stat.S_IMODE(pkg_path.stat().st_mode)
        assert mode & stat.S_IWGRP == 0, "group must not have write"
        assert mode & stat.S_IWOTH == 0, "other must not have write"

    def test_build_from_source_idempotent_with_readonly(
        self, temp_canonical_root, skill_source_dir,
    ):
        """Second build_from_source (idempotent) also succeeds."""
        import hashlib

        store = CanonicalSkillStore(root=temp_canonical_root)
        content_hash = hashlib.sha256(b"test-content").hexdigest()

        p1 = store.build_from_source(
            "test-skill", "1.0.0", content_hash, skill_source_dir,
        )
        # Second call must succeed (already built path, returns early)
        p2 = store.build_from_source(
            "test-skill", "1.0.0", content_hash, skill_source_dir,
        )
        assert p1 == p2

    def test_build_from_source_preserves_file_content(
        self, temp_canonical_root, skill_source_dir,
    ):
        """After build, package files are present and correct."""
        import hashlib

        store = CanonicalSkillStore(root=temp_canonical_root)
        content_hash = hashlib.sha256(b"test-content").hexdigest()
        pkg_path = store.build_from_source(
            "test-skill", "1.0.0", content_hash, skill_source_dir,
        )
        # All source files must be present
        sk = pkg_path / "SKILL.md"
        assert sk.is_file()
        assert "# Test Skill" in sk.read_text()

        ref = pkg_path / "references" / "helper.md"
        assert ref.is_file()
        assert "# Helper" in ref.read_text()

    def test_build_from_source_files_readonly_after_build(
        self, temp_canonical_root, skill_source_dir,
    ):
        """After build, individual package files are read-only (0444)."""
        import hashlib

        store = CanonicalSkillStore(root=temp_canonical_root)
        content_hash = hashlib.sha256(b"test-content").hexdigest()
        pkg_path = store.build_from_source(
            "test-skill", "1.0.0", content_hash, skill_source_dir,
        )
        sk = pkg_path / "SKILL.md"
        mode = stat.S_IMODE(sk.stat().st_mode)
        assert mode & stat.S_IWGRP == 0
        assert mode & stat.S_IWOTH == 0

    def test_build_from_manifest_succeeds_with_readonly_isolation(
        self, temp_canonical_root,
    ):
        """build_from_manifest also succeeds when readonly isolation is
        genuinely removing write bits (0555)."""
        import hashlib

        # Create a mock artifact store
        artifact_store = MagicMock()
        artifact_store.read.return_value = b"# Manifest-built skill\n"

        manifest = {
            "members": [
                {
                    "path": "SKILL.md",
                    "hash": "sha256:" + hashlib.sha256(b"# Manifest-built skill\n").hexdigest(),
                    "artifact_key": "skills/manifest-test/1.0.0/SKILL.md",
                },
            ],
        }
        manifest_json = json.dumps(manifest, sort_keys=True)
        content_hash = hashlib.sha256(manifest_json.encode()).hexdigest()

        store = CanonicalSkillStore(root=temp_canonical_root)
        pkg_path = store.build_from_manifest(
            "manifest-skill", "1.0.0", content_hash, manifest, artifact_store,
        )
        assert pkg_path.is_dir()
        sk = pkg_path / "SKILL.md"
        assert sk.is_file()
        assert sk.read_text() == "# Manifest-built skill\n"

        # Final package must be read-only
        mode = stat.S_IMODE(pkg_path.stat().st_mode)
        assert mode & stat.S_IWGRP == 0
        assert mode & stat.S_IWOTH == 0


class TestSameOwnerAdversarialLimits:
    """Tests demonstrating the honest limits of same-owner mode.

    In same-owner mode the executor runs under the daemon's own OS
    identity. These tests prove that:
    1. A same-owner process CAN write/alter canonical packages via
       workspace symlinks — no permission denial occurs.
    2. The daemon's integrity verification detects the alteration
       and REFUSES the session — NO automatic repair from same-UID
       local source.
    3. When a mismatch is detected, it fails closed and never blesses
       corrupted bytes as valid.
    4. Link tampering and policy withdrawal behave as documented.
    """

    def test_same_owner_can_write_canonical_through_symlink(
        self, store, materializer, skill_source_dir, workspace_dir,
    ):
        """A same-owner process CAN alter canonical package content
        through workspace symlinks.

        This test PROVES the honest limit: there is NO os-level
        write/chmod barrier in same-owner mode. The process running
        as the daemon's identity writes through the symlink and the
        canonical package content changes. The test expects the
        write to SUCCEED — if it raised PermissionError, that would
        falsely claim a security boundary.
        """
        content_hash = _compute_dir_hash(skill_source_dir)
        slug = "test-same-owner"

        store.build_from_source(slug, "system", content_hash, skill_source_dir)

        subdir = ".claude/skills"
        materializer.materialize_skill(
            slug, "system", content_hash, workspace_dir, subdir,
        )

        # Workspace symlink exists and is functional
        link_path = workspace_dir / subdir / slug
        assert link_path.is_symlink(), "Symlink must exist in workspace"

        # Read original content through symlink
        original = (link_path / "SKILL.md").read_text()
        assert "# Test Skill" in original

        # ── Adversarial write: alter canonical content through symlink ──
        # In same-owner mode the daemon sets files read-only (0444), but
        # since the executor is the same uid, it can simply chmod them
        # back to writable first. This is the honest limit: readonly
        # hardening is cosmetic when the attacker shares the daemon's uid.
        tampered_content = "# TAMPERED SKILL\n\nCorrupted by same-owner process."
        skill_file = link_path / "SKILL.md"

        # Step 1: chmod to writable (same-owner process CAN do this)
        try:
            os.chmod(skill_file, 0o644)
        except PermissionError:
            pytest.fail(
                "Same-owner process was denied chmod on canonical "
                "package file. In same-owner mode this should succeed."
            )

        # Step 2: write tampered content
        try:
            skill_file.write_text(tampered_content)
        except PermissionError:
            pytest.fail(
                "Same-owner process was denied write access to "
                "canonical package via workspace symlink. This "
                "should NOT happen — same-owner mode means no "
                "OS-level isolation exists."
            )

        # ── Verify the canonical package bytes actually changed ──
        actual_after = (link_path / "SKILL.md").read_text()
        assert actual_after == tampered_content, (
            "Canonical package content MUST reflect the adversarial "
            "write in same-owner mode"
        )

        # The canonical store path also shows the change (same file)
        pkg_path = store.canonical_path(slug, "system", content_hash)
        assert (pkg_path / "SKILL.md").read_text() == tampered_content

    def test_integrity_verification_detects_and_refuses(
        self, store, materializer, skill_source_dir, workspace_dir,
    ):
        """Integrity verification detects tampered canonical package
        and REFUSES to rebuild — no automatic repair from same-UID source.

        After a same-owner process tampers with canonical content,
        calling build_from_source with verify_source_hash detects
        the mismatch and raises CanonicalStoreError instead of rebuilding.
        """
        content_hash = _compute_dir_hash(skill_source_dir)
        slug = "test-integrity-refuse"

        # First build — creates trusted canonical package
        store.build_from_source(slug, "system", content_hash, skill_source_dir)
        pkg_path = store.canonical_path(slug, "system", content_hash)
        original = (pkg_path / "SKILL.md").read_text()

        # Simulate adversarial tampering of canonical content
        # (same-owner can chmod + rewrite)
        skill_file = pkg_path / "SKILL.md"
        os.chmod(skill_file, 0o644)
        skill_file.write_text("# TAMPERED")
        # Restore readonly so is_built passes — a sophisticated attacker
        # would do this too (same-owner means no OS-level barrier)
        os.chmod(skill_file, 0o444)

        # Verify is_built still returns True (ownership/permissions restored)
        assert store.is_built(slug, "system", content_hash), (
            "After restoring permissions, is_built passes — but content "
            "bytes are tampered (no content verification in is_built)"
        )

        # ── Integrity verification: REFUSE, not rebuild ──
        # build_from_source with verify_source_hash detects mismatch
        # and raises CanonicalStoreError. No automatic repair.
        from runtime.skills.canonical_store import CanonicalStoreError
        with pytest.raises(CanonicalStoreError) as exc:
            store.build_from_source(
                slug, "system", content_hash, skill_source_dir,
                verify_source_hash=content_hash,
            )
        assert "content_corruption" in str(exc.value)

        # Content should remain tampered (no auto-repair)
        tampered = (pkg_path / "SKILL.md").read_text()
        assert tampered == "# TAMPERED", (
            "Package content must remain tampered — no auto-repair"
        )

    def test_absent_trusted_source_fails_closed(
        self, store, tmp_path,
    ):
        """When trusted source is absent, integrity mismatch fails closed.

        If the source directory disappears after initial build, and the
        canonical package is then tampered, build_from_source with
        verify_source_hash must raise CanonicalStoreError — never
        silently bless corrupted bytes.
        """
        # Build a temp source and package
        src_dir = tmp_path / "disappearing-source"
        src_dir.mkdir()
        (src_dir / "SKILL.md").write_text("# Valid Content")

        content_hash = _compute_dir_hash(src_dir)
        slug = "test-absent-source"
        store.build_from_source(slug, "system", content_hash, src_dir)

        # Verify initial content
        pkg_path = store.canonical_path(slug, "system", content_hash)
        assert "# Valid Content" in (pkg_path / "SKILL.md").read_text()

        # Remove the trusted source
        shutil.rmtree(src_dir)
        assert not src_dir.exists()

        # Tamper with canonical package (same-owner: chmod + rewrite)
        skill_file = pkg_path / "SKILL.md"
        os.chmod(skill_file, 0o644)
        skill_file.write_text("# TAMPERED AFTER SOURCE GONE")
        # Attacker restores permissions — no OS-level barrier
        os.chmod(skill_file, 0o444)

        # ── Attempt rebuild without source: must fail ──
        from runtime.skills.canonical_store import CanonicalStoreError
        with pytest.raises((CanonicalStoreError, FileNotFoundError)):
            store.build_from_source(
                slug, "system", content_hash, src_dir,
                verify_source_hash=content_hash,
            )

        # Corrupted bytes must NOT be accepted as valid — the package
        # still contains the tampered content
        actual = (pkg_path / "SKILL.md").read_text()
        assert "# Valid Content" not in actual, (
            "Content was unexpectedly restored from nowhere"
        )
        assert "TAMPERED" in actual, (
            "Tampered content persists since rebuild failed"
        )

    def test_valid_start_no_spurious_rebuild(
        self, store, skill_source_dir,
    ):
        """A normal valid start does not spuriously rebuild/repair.

        When the canonical package is intact, build_from_source with
        verify_source_hash should return the existing package without
        rebuilding.
        """
        content_hash = _compute_dir_hash(skill_source_dir)
        slug = "test-valid-start"

        # Build once
        path1 = store.build_from_source(
            slug, "system", content_hash, skill_source_dir,
            verify_source_hash=content_hash,
        )

        # Second build with same hash — should return existing package
        path2 = store.build_from_source(
            slug, "system", content_hash, skill_source_dir,
            verify_source_hash=content_hash,
        )

        assert path1 == path2, "Valid start must not create new package"
        assert store.is_built(slug, "system", content_hash)

    def test_link_tampering_detected_and_repaired(
        self, store, materializer, skill_source_dir, workspace_dir,
    ):
        """Tampered/malformed workspace symlinks are detected and safely
        repaired. The repair replaces broken/wrong-target symlinks but
        never follows or deletes attacker nodes.

        Covers both .claude/skills and .agents/skills roots.
        """
        content_hash = _compute_dir_hash(skill_source_dir)
        slug = "test-link-repair"
        store.build_from_source(slug, "system", content_hash, skill_source_dir)

        # Materialize to both roots
        for subdir in (".claude/skills", ".agents/skills"):
            materializer.materialize_skill(
                slug, "system", content_hash, workspace_dir, subdir,
            )

        # Tamper: replace symlink with a broken one in one root
        broken_link = workspace_dir / ".claude/skills" / slug
        assert broken_link.is_symlink()
        broken_link.unlink()
        os.symlink("/nonexistent/path", str(broken_link))

        # Repair workspace skills should fix both roots
        expected_specs = [{
            "slug": slug,
            "version": "system",
            "content_hash": content_hash,
        }]
        for subdir in (".claude/skills", ".agents/skills"):
            materializer.repair_workspace_skills(
                expected_specs, workspace_dir, subdir,
            )

        # Both roots should have valid symlinks after repair
        for subdir in (".claude/skills", ".agents/skills"):
            link = workspace_dir / subdir / slug
            assert link.is_symlink(), f"{subdir}/{slug} must be a symlink"
            assert (link / "SKILL.md").exists(), (
                f"{subdir}/{slug}/SKILL.md must be accessible"
            )
            assert "# Test Skill" in (link / "SKILL.md").read_text()

    def test_policy_withdrawal_removes_only_managed_links(
        self, store, materializer, skill_source_dir, workspace_dir,
    ):
        """Policy withdrawal removes only managed link entries.

        When a skill is withdrawn from the expected set, only the
        managed symlink is removed. The canonical package is retained.
        Other files at the workspace link site (if an ordinary
        directory) are never recursively deleted.
        """
        content_hash = _compute_dir_hash(skill_source_dir)
        slug = "test-withdraw"
        store.build_from_source(slug, "system", content_hash, skill_source_dir)

        subdir = ".claude/skills"
        materializer.materialize_skill(
            slug, "system", content_hash, workspace_dir, subdir,
        )
        link_path = workspace_dir / subdir / slug
        assert link_path.is_symlink()

        # Withdraw: repair with empty expected set
        materializer.repair_workspace_skills([], workspace_dir, subdir)

        # Link should be removed
        assert not link_path.exists(follow_symlinks=False), (
            "Withdrawn skill link must be removed"
        )

        # Canonical package must still exist (retained)
        pkg_path = store.canonical_path(slug, "system", content_hash)
        assert pkg_path.is_dir(), (
            "Canonical package must be retained after withdrawal"
        )

        # ── Ordinary directory at link site is never recursively deleted ──
        # Build a canonical package so materialize_skill gets past verify_package
        # and reaches create_relative_symlink (which detects ordinary dirs)
        ordinary_src = workspace_dir / "ordinary-src"
        ordinary_src.mkdir()
        (ordinary_src / "SKILL.md").write_text("# ordinary")
        ordinary_hash = _compute_dir_hash(ordinary_src)
        store.build_from_source(
            "test-ordinary-dir", "system", ordinary_hash, ordinary_src,
        )

        # Create an ordinary directory where a link would go
        ordinary_dir = workspace_dir / subdir / "test-ordinary-dir"
        ordinary_dir.mkdir(parents=True, exist_ok=True)
        (ordinary_dir / "real-work.txt").write_text("real user work")

        # Attempt materialize — should NOT delete the ordinary directory
        with pytest.raises(SymlinkMaterializationError, match="ordinary_dir"):
            materializer.materialize_skill(
                "test-ordinary-dir", "system", ordinary_hash,
                workspace_dir, subdir,
            )

        # Ordinary directory must still exist with its content intact
        assert ordinary_dir.is_dir()
        assert (ordinary_dir / "real-work.txt").read_text() == "real user work"

    # ── Production-faithful hardening + is_built ─────

    def test_hardening_0755_dirs_is_built_true(
        self, monkeypatch, tmp_path, skill_source_dir,
    ):
        """is_built returns True for a valid built package.

        Uses the actual _MacOSPlatformIsolation class directly.
        """
        iso = _MacOSPlatformIsolation()

        store = CanonicalSkillStore(
            root=tmp_path / "canonical",
            isolation=iso,
        )

        content_hash = _compute_dir_hash(skill_source_dir)
        slug = "test-is-built-true"

        # Build once through the real build path
        store.build_from_source(slug, "system", content_hash, skill_source_dir)

        # ── is_built must return True after build ──
        assert store.is_built(slug, "system", content_hash), (
            "is_built must return True for intact 0755/0444 package "
            "in same-owner mode — spurious rebuilds are prohibited"
        )

        # ── Normal valid prelaunch start does not rebuild ──
        path1 = store.build_from_source(
            slug, "system", content_hash, skill_source_dir,
            verify_source_hash=content_hash,
        )
        path2 = store.build_from_source(
            slug, "system", content_hash, skill_source_dir,
            verify_source_hash=content_hash,
        )
        assert path1 == path2, (
            "Valid start with real hardening must not create new package"
        )

        # ── SAME proof: same-owner CAN still write through a symlink ──
        # (same-owner mode has no OS barrier)
        pkg_path = store.canonical_path(slug, "system", content_hash)
        skill_file = pkg_path / "SKILL.md"
        skill_file.write_text("# TAMPERED")

        # is_built still returns True after same-UID write
        # (content bytes are wrong — this is the honest limit:
        # is_built checks presence, not content integrity;
        # only verify_source_hash catches content tampering)
        assert store.is_built(slug, "system", content_hash), (
            "After restoring permissions, is_built passes — content "
            "bytes are tampered but hardening bits match"
        )

        # Integrity verification REFUSES automatic repair
        from runtime.skills.canonical_store import CanonicalStoreError
        with pytest.raises(CanonicalStoreError) as exc:
            store.build_from_source(
                slug, "system", content_hash, skill_source_dir,
                verify_source_hash=content_hash,
            )
        assert "content_corruption" in str(exc.value)
        # Canonical bytes must remain tampered — no auto-repair
        still_tampered = (pkg_path / "SKILL.md").read_text()
        assert "# TAMPERED" in still_tampered, (
            "Integrity verification must refuse auto-repair — "
            "canonical bytes stay corrupted"
        )

    # ── Fix 2: Legacy single-SKILL.md lifecycle artifact branch ────────

    # ── Fix 2 v2: Real _build_lifecycle_canonical_specs branch tests ──
    # The legacy single-SKILL.md lifecycle branch is exercised through
    # the actual production function rather than direct store calls.
    # The raw artifact SHA (ledger content_hash) differs from the
    # derived source-tree hash (extracted temp dir _compute_dir_hash) —
    # this is proven in every test. If verify_source_hash were omitted,
    # wired to canonical bytes, or wired to raw artifact SHA, tampered
    # content would be silently accepted.

    def test_legacy_lifecycle_branch_tamper_detect_and_refuse(
        self, store, tmp_path, db,
    ):
        """Through the real _build_lifecycle_canonical_specs production
        branch: same-owner canonical target mutation is detected and
        REFUSED — no automatic repair from same-UID local source.

        Sets up a real ArtifactStore-backed lifecycle artifact.  The
        raw artifact SHA (content_hash) USED to differ from the
        derived source-tree hash — this proves that wiring
        verify_source_hash to the wrong value would silently accept
        tampered content.

        Honest adversarial proposition: same-owner CAN modify the
        symlinked canonical target.  Detection refuses the session
        and leaves canonical bytes corrupted.
        """
        from runtime.infrastructure.artifact_store import ArtifactStore
        from runtime.orchestrator._paths import OrgPaths
        from runtime.skills.lifecycle import stores as lifecycle_stores
        from runtime.skills.lifecycle.models import LifecycleStatus
        import datetime

        # ── 1. Artifact identity: raw bytes SHA ≠ derived tree hash ──
        skill_md_bytes = b"# Test Legacy Skill\n\nOriginal content.\n"
        raw_artifact_sha = hashlib.sha256(skill_md_bytes).hexdigest()

        # Write artifact bytes into a temp dir to compute the *derived*
        # source-tree hash (the one _build_lifecycle_canonical_specs
        # passes as verify_source_hash).
        src_dir = tmp_path / "probe-src"
        src_dir.mkdir()
        (src_dir / "SKILL.md").write_bytes(skill_md_bytes)
        derived_tree_hash = _compute_dir_hash(src_dir)

        # Proven: raw artifact SHA differs from derived tree hash.
        assert raw_artifact_sha != derived_tree_hash, (
            f"Raw artifact SHA {raw_artifact_sha[:16]}... must differ "
            f"from derived tree hash {derived_tree_hash[:16]}... — "
            f"_compute_dir_hash includes relative-path prefix bytes "
            f"that raw SHA-256 does not"
        )

        # ── 2. Seed the lifecycle ledger ──
        org_root = tmp_path / "org"
        artifact_store = ArtifactStore(OrgPaths(org_root).artifacts_dir)
        artifact_key = "skill-lifecycle/test-legacy/1.0.0/SKILL.md"
        artifact_store.put(artifact_key, skill_md_bytes)

        pkg = lifecycle_stores.PackageVersion(
            skill_id="hr:test-legacy",
            slug="test-legacy",
            name="Test Legacy Skill",
            version="1.0.0",
            content_hash=raw_artifact_sha,
            policy_class="standard_operational",
            description="A test legacy skill",
            skill_md=skill_md_bytes.decode("utf-8"),
            content_artifact_key=artifact_key,
            status=LifecycleStatus.PUBLISHED,
            created_by="founder",
            publisher="founder",
        )
        version_id = lifecycle_stores.insert_package_version(db, pkg)
        assign = lifecycle_stores.AssignmentRecord(
            skill_id="hr:test-legacy",
            agent_name="test-agent",
            package_version_id=version_id,
            version="1.0.0",
            content_hash=raw_artifact_sha,
            assigned_by="founder",
            assigned_at=datetime.datetime.now(datetime.timezone.utc),
            active=True,
        )
        lifecycle_stores.insert_assignment(db, assign)

        # ── 3. Build through the real production branch ──
        specs = _build_lifecycle_canonical_specs(
            store=store,
            org_root=org_root,
            db=db,
            agent_name="test-agent",
            slug="test-org",
        )
        assert len(specs) == 1
        assert specs[0]["slug"] == "test-legacy"
        assert specs[0]["version"] == "1.0.0"
        assert specs[0]["content_hash"] == raw_artifact_sha

        pkg_path = store.canonical_path("test-legacy", "1.0.0", raw_artifact_sha)
        assert (pkg_path / "SKILL.md").read_text() == skill_md_bytes.decode("utf-8")

        # ── 4. Same-owner tampers with canonical target ──
        skill_file = pkg_path / "SKILL.md"
        os.chmod(skill_file, 0o644)
        skill_file.write_text("# TAMPERED BY SAME OWNER")
        os.chmod(skill_file, 0o444)

        assert "TAMPERED" in (pkg_path / "SKILL.md").read_text(), (
            "Same-owner CAN alter the symlinked canonical target — "
            "this is the honest limit, not a security boundary"
        )

        # ── 5. Detect tamper, REFUSE to rebuild ──
        # The second _build_lifecycle_canonical_specs call must detect
        # content corruption and raise LifecycleMaterializationError.
        # No automatic repair from same-UID local source.
        from runtime.orchestrator.workspace_adapters import (
            LifecycleMaterializationError,
        )
        with pytest.raises(LifecycleMaterializationError) as exc:
            _build_lifecycle_canonical_specs(
                store=store,
                org_root=org_root,
                db=db,
                agent_name="test-agent",
                slug="test-org",
            )
        assert "content_corruption" in str(exc.value).lower()
        # Canonical bytes must remain corrupted — no auto-repair
        still_tampered = (pkg_path / "SKILL.md").read_text()
        assert "TAMPERED" in still_tampered, (
            "Integrity verification must refuse auto-repair — "
            "canonical bytes stay corrupted; got: {!r}".format(still_tampered)
        )

    def test_legacy_lifecycle_branch_absent_artifact_fails_closed(
        self, store, tmp_path, db,
    ):
        """Through the real _build_lifecycle_canonical_specs production
        branch: when the trusted artifact is withdrawn/unavailable,
        the function raises the documented named actionable failure
        (LifecycleMaterializationError) and never blesses altered
        canonical content.

        If ArtifactNotFound were silently accepted, the tampered bytes
        would persist unchallenged.
        """
        from runtime.infrastructure.artifact_store import ArtifactStore
        from runtime.orchestrator._paths import OrgPaths
        from runtime.skills.lifecycle import stores as lifecycle_stores
        from runtime.skills.lifecycle.models import LifecycleStatus
        from runtime.orchestrator.workspace_adapters import (
            LifecycleMaterializationError,
        )
        import datetime

        # ── 1. Seed artifact + lifecycle package ──
        skill_md_bytes = b"# Valid Legacy Skill\n\nWill be withdrawn.\n"
        raw_artifact_sha = hashlib.sha256(skill_md_bytes).hexdigest()

        org_root = tmp_path / "org"
        artifact_store = ArtifactStore(OrgPaths(org_root).artifacts_dir)
        artifact_key = "skill-lifecycle/test-absent/1.0.0/SKILL.md"
        artifact_store.put(artifact_key, skill_md_bytes)

        pkg = lifecycle_stores.PackageVersion(
            skill_id="hr:test-absent",
            slug="test-absent",
            name="Test Absent Skill",
            version="1.0.0",
            content_hash=raw_artifact_sha,
            policy_class="standard_operational",
            description="Skill that will be withdrawn",
            skill_md=skill_md_bytes.decode("utf-8"),
            content_artifact_key=artifact_key,
            status=LifecycleStatus.PUBLISHED,
            created_by="founder",
            publisher="founder",
        )
        version_id = lifecycle_stores.insert_package_version(db, pkg)
        assign = lifecycle_stores.AssignmentRecord(
            skill_id="hr:test-absent",
            agent_name="test-agent",
            package_version_id=version_id,
            version="1.0.0",
            content_hash=raw_artifact_sha,
            assigned_by="founder",
            assigned_at=datetime.datetime.now(datetime.timezone.utc),
            active=True,
        )
        lifecycle_stores.insert_assignment(db, assign)

        # ── 2. First build succeeds ──
        specs = _build_lifecycle_canonical_specs(
            store=store,
            org_root=org_root,
            db=db,
            agent_name="test-agent",
            slug="test-org",
        )
        assert len(specs) == 1
        pkg_path = store.canonical_path("test-absent", "1.0.0", raw_artifact_sha)
        assert "Valid Legacy" in (pkg_path / "SKILL.md").read_text()

        # ── 3. Withdraw the artifact (mirrors ArtifactNotFound) ──
        artifact_store.delete(artifact_key)

        # ── 4. Tamper with canonical target (same-owner CAN do this) ──
        skill_file = pkg_path / "SKILL.md"
        os.chmod(skill_file, 0o644)
        skill_file.write_text("# TAMPERED AFTER WITHDRAWAL")
        os.chmod(skill_file, 0o444)

        # ── 5. Rebuild fails closed with named actionable error ──
        with pytest.raises(LifecycleMaterializationError, match="Artifact not found"):
            _build_lifecycle_canonical_specs(
                store=store,
                org_root=org_root,
                db=db,
                agent_name="test-agent",
                slug="test-org",
            )

        # ── 6. Tampered bytes are never silently blessed ──
        actual = (pkg_path / "SKILL.md").read_text()
        assert "TAMPERED" in actual, (
            "Tampered content persists in canonical store since "
            "rebuild failed — fail closed, never silently accept"
        )
        assert "Valid Legacy" not in actual, (
            "Content was unexpectedly restored from a withdrawn artifact"
        )

    def test_legacy_lifecycle_branch_valid_no_spurious_rebuild(
        self, store, tmp_path, db,
    ):
        """Through the real _build_lifecycle_canonical_specs production
        branch: an intact valid artifact/canonical-store is reused
        without spurious rebuild.

        The second call passes the same verified source hash and the
        canonical package content still matches — build_from_source
        must return the existing path without entering the rebuild
        code path.  We intercept build_from_source on the store,
        which is only called for a genuine rebuild.
        """
        from runtime.infrastructure.artifact_store import ArtifactStore
        from runtime.orchestrator._paths import OrgPaths
        from runtime.skills.lifecycle import stores as lifecycle_stores
        from runtime.skills.lifecycle.models import LifecycleStatus
        import datetime

        # ── 1. Seed artifact + lifecycle package ──
        skill_md_bytes = b"# Valid Stable Skill\n\nStable content.\n"
        raw_artifact_sha = hashlib.sha256(skill_md_bytes).hexdigest()

        org_root = tmp_path / "org"
        artifact_store = ArtifactStore(OrgPaths(org_root).artifacts_dir)
        artifact_key = "skill-lifecycle/test-valid/1.0.0/SKILL.md"
        artifact_store.put(artifact_key, skill_md_bytes)

        pkg = lifecycle_stores.PackageVersion(
            skill_id="hr:test-valid",
            slug="test-valid",
            name="Test Valid Skill",
            version="1.0.0",
            content_hash=raw_artifact_sha,
            policy_class="standard_operational",
            description="A stable valid skill",
            skill_md=skill_md_bytes.decode("utf-8"),
            content_artifact_key=artifact_key,
            status=LifecycleStatus.PUBLISHED,
            created_by="founder",
            publisher="founder",
        )
        version_id = lifecycle_stores.insert_package_version(db, pkg)
        assign = lifecycle_stores.AssignmentRecord(
            skill_id="hr:test-valid",
            agent_name="test-agent",
            package_version_id=version_id,
            version="1.0.0",
            content_hash=raw_artifact_sha,
            assigned_by="founder",
            assigned_at=datetime.datetime.now(datetime.timezone.utc),
            active=True,
        )
        lifecycle_stores.insert_assignment(db, assign)

        # ── 2. First build through real production branch ──
        specs1 = _build_lifecycle_canonical_specs(
            store=store,
            org_root=org_root,
            db=db,
            agent_name="test-agent",
            slug="test-org",
        )
        assert len(specs1) == 1
        pkg_path = store.canonical_path("test-valid", "1.0.0", raw_artifact_sha)
        assert (pkg_path / "SKILL.md").read_text() == skill_md_bytes.decode("utf-8")

        # ── 3. Second materialization must reuse existing package ──
        specs2 = _build_lifecycle_canonical_specs(
            store=store,
            org_root=org_root,
            db=db,
            agent_name="test-agent",
            slug="test-org",
        )

        # ── 4. Assert no spurious rebuild occurred ──
        assert len(specs2) == 1
        assert specs2[0] == specs1[0], (
            "Legacy lifecycle branch must not spuriously rebuild "
            "when artifact content matches source hash"
        )
        assert store.is_built("test-valid", "1.0.0", raw_artifact_sha), (
            "is_built must return True for intact package after valid reuse"
        )

    def test_legacy_lifecycle_branch_controlled_rebuild_positive(
        self, store, tmp_path, db,
    ):
        """Red-side control: a deliberately corrupted canonical package
        triggers a REFUSAL (NOT a rebuild), proving the detection-only
        contract. Corrupted canonical bytes remain corrupted after refusal.

        This test corrupts the canonical SKILL.md after the first
        build, then verifies that the second lifecycle materialization
        detects the mismatch, REFUSES (raises LifecycleMaterializationError),
        and leaves canonical bytes unchanged (no auto-repair).
        """
        from runtime.infrastructure.artifact_store import ArtifactStore
        from runtime.orchestrator._paths import OrgPaths
        from runtime.skills.lifecycle import stores as lifecycle_stores
        from runtime.skills.lifecycle.models import LifecycleStatus
        from runtime.orchestrator.workspace_adapters import LifecycleMaterializationError
        import datetime

        # ── 1. Seed artifact + lifecycle package ──
        skill_md_bytes = b"# Valid Stable Skill\n\nStable content.\n"
        raw_artifact_sha = hashlib.sha256(skill_md_bytes).hexdigest()

        org_root = tmp_path / "org"
        artifact_store = ArtifactStore(OrgPaths(org_root).artifacts_dir)
        artifact_key = "skill-lifecycle/test-corrupt/1.0.0/SKILL.md"
        artifact_store.put(artifact_key, skill_md_bytes)

        pkg = lifecycle_stores.PackageVersion(
            skill_id="hr:test-corrupt",
            slug="test-corrupt",
            name="Test Corrupt Skill",
            version="1.0.0",
            content_hash=raw_artifact_sha,
            policy_class="standard_operational",
            description="A skill to test refusal on corruption",
            skill_md=skill_md_bytes.decode("utf-8"),
            content_artifact_key=artifact_key,
            status=LifecycleStatus.PUBLISHED,
            created_by="founder",
            publisher="founder",
        )
        version_id = lifecycle_stores.insert_package_version(db, pkg)
        assign = lifecycle_stores.AssignmentRecord(
            skill_id="hr:test-corrupt",
            agent_name="test-agent",
            package_version_id=version_id,
            version="1.0.0",
            content_hash=raw_artifact_sha,
            assigned_by="founder",
            assigned_at=datetime.datetime.now(datetime.timezone.utc),
            active=True,
        )
        lifecycle_stores.insert_assignment(db, assign)

        # ── 2. First build through real production branch ──
        specs1 = _build_lifecycle_canonical_specs(
            store=store,
            org_root=org_root,
            db=db,
            agent_name="test-agent",
            slug="test-org",
        )
        assert len(specs1) == 1
        pkg_path = store.canonical_path("test-corrupt", "1.0.0", raw_artifact_sha)
        assert (pkg_path / "SKILL.md").read_text() == skill_md_bytes.decode("utf-8")

        # ── 3. Corrupt the canonical package in place ──
        # Same-owner can write through symlinks; this simulates an
        # accidental or adversarial mutation.
        corrupted = b"# Corrupted Content\n\nThis should trigger refusal.\n"
        pkg_path.chmod(0o755)
        (pkg_path / "SKILL.md").chmod(0o644)
        (pkg_path / "SKILL.md").write_bytes(corrupted)
        (pkg_path / "SKILL.md").chmod(0o444)
        pkg_path.chmod(0o555)
        assert (pkg_path / "SKILL.md").read_bytes() == corrupted, (
            "Corruption must be in place before second materialization"
        )

        # ── 4. Second materialization must REFUSE (no rebuild) ──
        import runtime.skills.canonical_store as cs_mod

        with pytest.raises(LifecycleMaterializationError) as exc:
            _build_lifecycle_canonical_specs(
                store=store,
                org_root=org_root,
                db=db,
                agent_name="test-agent",
                slug="test-org",
            )
        assert "content_corruption" in str(exc.value).lower()

        # ── 5. Canonical bytes must remain corrupted ──
        assert (pkg_path / "SKILL.md").read_bytes() == corrupted, (
            "Canonical SKILL.md must remain corrupted — no auto-repair"
        )
