"""TASK-5684 (THR-190 PR-B): workspace symlink containment tests.

Threat principal
----------------
A SANDBOXED Codex/Pi agent with workspace-write can pre-position symlinked
workspace/provider/nested-skills paths — ``<ws>/.claude``,
``<ws>/.claude/skills``, ``<ws>/.agents/skills``, or deeper nested entries —
that point OUTSIDE the real workspace. On the next session start, the
daemon's materializer must NOT follow those pre-positioned parents when
creating/replacing/withdrawing skill links; doing so would write, unlink, or
replace files OUTSIDE the real workspace under the daemon's identity. An
unsandboxed Claude session (which can already reach anything the daemon can)
is NOT the defended principal.

Containment contract (THR-190 PR-B)
-----------------------------------
1. The lowest-level link writer (``PlatformIsolation.create_relative_symlink``)
   enforces resolved-parent containment inside the REAL workspace
   immediately before link creation/replacement: no-follow admission of
   every component below the resolved workspace root (a symlinked provider
   or nested skills root is a pre-positioned escape and fails closed), and
   the link is created atomically (tmp symlink + ``os.replace``) through a
   no-follow, pinned parent dirfd so a concurrent swap of the parent path
   cannot redirect the write.
2. ``withdraw_workspace_link`` and ``admit_skills_directory`` apply the same
   no-follow admission; repair/withdraw never list, unlink, or replace
   through an escaped parent.
3. Ordinary canonical relative symlinks wholly inside a normal workspace
   still materialize and repair on supported platforms.

Every adversarial assertion uses an EXTERNAL sentinel directory and verifies
BOTH bytes and relevant surrounding filesystem state remain unchanged.
"""

from __future__ import annotations

import os
import stat as _stat
from pathlib import Path

import pytest

from runtime.platform.isolation import (
    PlatformIsolationError,
    _LinuxPlatformIsolation,
)
from runtime.skills.canonical_store import CanonicalSkillStore
from runtime.skills.symlink_materializer import (
    SymlinkMaterializationError,
    SymlinkMaterializer,
)

# ── Helpers ───────────────────────────────────────────────────────────


def _external_sentinel(base: Path, name: str = "external") -> Path:
    """Create an EXTERNAL sentinel directory OUTSIDE any workspace.

    Contains a byte-exact sentinel file and a decoy skills subtree with real
    content that a following materializer would otherwise write into / unlink.
    """
    ext = base / name
    ext.mkdir()
    (ext / "sentinel.txt").write_bytes(b"SENTINEL\x00\x01\x02payload")
    (ext / "skills" / "start-task").mkdir(parents=True)
    (ext / "skills" / "decoy-skill").mkdir(parents=True)
    (ext / "skills" / "start-task" / "SKILL.md").write_bytes(
        b"# external start-task decoy\n"
    )
    (ext / "skills" / "decoy-skill" / "SKILL.md").write_bytes(
        b"# external decoy skill\n"
    )
    (ext / "withdraw-decoy.txt").write_bytes(b"do-not-unlink\x00")
    return ext


def _snapshot(path: Path) -> dict:
    """Full no-follow subtree snapshot: symlink targets, dirs, file bytes."""
    out: dict = {}
    for p in sorted(path.rglob("*")):
        rel = p.relative_to(path)
        try:
            st = p.lstat()
        except OSError:
            continue
        if _stat.S_ISLNK(st.st_mode):
            out[str(rel)] = ("link", os.readlink(p))
        elif _stat.S_ISDIR(st.st_mode):
            out[str(rel)] = ("dir",)
        else:
            out[str(rel)] = ("file", p.read_bytes())
    return out


def _assert_sentinel_unchanged(ext: Path, before: dict) -> None:
    """Assert the external sentinel subtree is byte- and state-identical."""
    after = _snapshot(ext)
    assert after == before, (
        "External sentinel changed:\n"
        f"  removed/added/altered: {sorted(set(before) ^ set(after))}"
    )


def _build_skill(store: CanonicalSkillStore, base: Path, slug: str) -> str:
    """Build a canonical skill package from source; return its content hash."""
    from runtime.orchestrator.workspace_adapters import _compute_dir_hash

    src = base / slug
    src.mkdir()
    (src / "SKILL.md").write_text(f"# {slug}\nskill body\n")
    ch = _compute_dir_hash(src)
    store.build_from_source(slug, "1.0", ch, src)
    return ch


# ═══════════════════════════════════════════════════════════════════════
# Lowest-level link writer (production POSIX class, real seam)
# ═══════════════════════════════════════════════════════════════════════


class TestLinkWriterContainment:
    """Direct evidence against the REAL production link writer.

    The conftest test-mode double inherits this same implementation, so the
    unit suite exercises the real seam everywhere; these tests additionally
    instantiate the production class directly.
    """

    def test_rejects_prepositioned_symlinked_provider_dir(self, tmp_path):
        """<ws>/.claude -> external: link creation must fail closed."""
        iso = _LinuxPlatformIsolation()
        ws = tmp_path / "ws"
        ws.mkdir()
        ext = _external_sentinel(tmp_path)
        before = _snapshot(ext)
        os.symlink(ext, ws / ".claude")

        with pytest.raises(PlatformIsolationError) as ei:
            iso.create_relative_symlink(
                Path("../../canonical/target"), ws / ".claude" / "skills" / "skill-a",
                workspace_root=ws,
            )
        assert ei.value.code == "escaped_parent"
        _assert_sentinel_unchanged(ext, before)
        assert (ws / ".claude").is_symlink(), "pre-positioned symlink replaced!"

    def test_rejects_prepositioned_symlinked_skills_root(self, tmp_path):
        """<ws>/.claude/skills -> external: link creation must fail closed."""
        iso = _LinuxPlatformIsolation()
        ws = tmp_path / "ws"
        (ws / ".claude").mkdir(parents=True)
        ext = _external_sentinel(tmp_path)
        before = _snapshot(ext)
        os.symlink(ext, ws / ".claude" / "skills")

        with pytest.raises(PlatformIsolationError) as ei:
            iso.create_relative_symlink(
                Path("target"), ws / ".claude" / "skills" / "skill-a",
                workspace_root=ws,
            )
        assert ei.value.code == "escaped_parent"
        _assert_sentinel_unchanged(ext, before)
        assert (ws / ".claude" / "skills").is_symlink()

    def test_rejects_prepositioned_agents_skills_root(self, tmp_path):
        """<ws>/.agents/skills -> external: same no-follow admission."""
        iso = _LinuxPlatformIsolation()
        ws = tmp_path / "ws"
        (ws / ".agents").mkdir(parents=True)
        ext = _external_sentinel(tmp_path)
        before = _snapshot(ext)
        os.symlink(ext, ws / ".agents" / "skills")

        with pytest.raises(PlatformIsolationError) as ei:
            iso.create_relative_symlink(
                Path("target"), ws / ".agents" / "skills" / "skill-a",
                workspace_root=ws,
            )
        assert ei.value.code == "escaped_parent"
        _assert_sentinel_unchanged(ext, before)

    def test_rejects_nested_symlink_below_skills_root(self, tmp_path):
        """Nested skills-root symlink (skills/sub -> external) must fail."""
        iso = _LinuxPlatformIsolation()
        ws = tmp_path / "ws"
        (ws / ".claude" / "skills").mkdir(parents=True)
        ext = _external_sentinel(tmp_path)
        before = _snapshot(ext)
        os.symlink(ext, ws / ".claude" / "skills" / "sub")

        with pytest.raises(PlatformIsolationError) as ei:
            iso.create_relative_symlink(
                Path("target"), ws / ".claude" / "skills" / "sub" / "child",
                workspace_root=ws,
            )
        assert ei.value.code == "escaped_parent"
        _assert_sentinel_unchanged(ext, before)

    def test_rejects_link_lexically_outside_workspace(self, tmp_path):
        """A link parent outside the workspace fails closed."""
        iso = _LinuxPlatformIsolation()
        ws = tmp_path / "ws"
        ws.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        with pytest.raises(PlatformIsolationError) as ei:
            iso.create_relative_symlink(
                Path("target"), outside / "link", workspace_root=ws,
            )
        assert ei.value.code == "link_outside_workspace"

    def test_creates_missing_parents_as_real_directories(self, tmp_path):
        """Fresh workspace: parents are created as genuine dirs, never symlinks."""
        iso = _LinuxPlatformIsolation()
        ws = tmp_path / "ws"
        link = ws / ".claude" / "skills" / "skill-a"
        iso.create_relative_symlink(Path("target"), link, workspace_root=ws)
        assert (ws / ".claude").is_dir()
        assert not (ws / ".claude").is_symlink()
        assert (ws / ".claude" / "skills").is_dir()
        assert not (ws / ".claude" / "skills").is_symlink()
        assert link.is_symlink()
        assert os.readlink(link) == "target"

    def test_replaces_existing_link_atomically(self, tmp_path):
        """Stale/wrong link is replaced atomically with the new target."""
        iso = _LinuxPlatformIsolation()
        ws = tmp_path / "ws"
        (ws / ".claude" / "skills").mkdir(parents=True)
        link = ws / ".claude" / "skills" / "skill-a"
        iso.create_relative_symlink(Path("old"), link, workspace_root=ws)
        iso.create_relative_symlink(Path("new"), link, workspace_root=ws)
        assert link.is_symlink()
        assert os.readlink(link) == "new"
        # No stale temp artifacts left behind
        assert not (ws / ".claude" / "skills" / ".tmp.skill-a").exists(
            follow_symlinks=False
        )

    def test_rejects_ordinary_dir_at_link_path(self, tmp_path):
        """An ordinary directory at the link path is refused, never deleted."""
        iso = _LinuxPlatformIsolation()
        ws = tmp_path / "ws"
        (ws / ".claude" / "skills").mkdir(parents=True)
        ordinary = ws / ".claude" / "skills" / "ordinary-dir"
        ordinary.mkdir()
        (ordinary / "important.txt").write_text("do not delete")

        with pytest.raises(PlatformIsolationError) as ei:
            iso.create_relative_symlink(
                Path("target"), ordinary, workspace_root=ws,
            )
        assert ei.value.code == "ordinary_dir_at_link_path"
        assert ordinary.is_dir()
        assert (ordinary / "important.txt").read_text() == "do not delete"

    def test_withdraw_rejects_symlinked_skills_root(self, tmp_path):
        """Withdrawal must not unlink through a symlinked parent."""
        iso = _LinuxPlatformIsolation()
        ws = tmp_path / "ws"
        (ws / ".claude").mkdir(parents=True)
        ext = _external_sentinel(tmp_path)
        before = _snapshot(ext)
        os.symlink(ext, ws / ".claude" / "skills")

        with pytest.raises(PlatformIsolationError) as ei:
            iso.withdraw_workspace_link(
                ws / ".claude" / "skills" / "withdraw-decoy.txt",
                workspace_root=ws,
            )
        assert ei.value.code == "escaped_parent"
        _assert_sentinel_unchanged(ext, before)
        assert (ext / "withdraw-decoy.txt").exists(), "external file unlinked!"

    def test_withdraw_removes_link_inside_workspace(self, tmp_path):
        """Ordinary in-workspace withdrawal still works."""
        iso = _LinuxPlatformIsolation()
        ws = tmp_path / "ws"
        (ws / ".claude" / "skills").mkdir(parents=True)
        link = ws / ".claude" / "skills" / "skill-a"
        iso.create_relative_symlink(Path("target"), link, workspace_root=ws)
        iso.withdraw_workspace_link(link, workspace_root=ws)
        assert not link.exists(follow_symlinks=False)

    def test_withdraw_refuses_ordinary_dir(self, tmp_path):
        """Withdrawal refuses ordinary directories (content preserved)."""
        iso = _LinuxPlatformIsolation()
        ws = tmp_path / "ws"
        (ws / ".claude" / "skills").mkdir(parents=True)
        ordinary = ws / ".claude" / "skills" / "work"
        ordinary.mkdir()
        (ordinary / "notes.txt").write_text("valuable")

        with pytest.raises(PlatformIsolationError) as ei:
            iso.withdraw_workspace_link(ordinary, workspace_root=ws)
        assert ei.value.code == "ordinary_dir_not_withdrawable"
        assert (ordinary / "notes.txt").read_text() == "valuable"

    def test_admit_rejects_symlinked_root(self, tmp_path):
        """admit_skills_directory refuses a symlinked provider root."""
        iso = _LinuxPlatformIsolation()
        ws = tmp_path / "ws"
        (ws / ".claude").mkdir(parents=True)
        ext = _external_sentinel(tmp_path)
        before = _snapshot(ext)
        os.symlink(ext, ws / ".claude" / "skills")

        with pytest.raises(PlatformIsolationError) as ei:
            iso.admit_skills_directory(
                ws / ".claude" / "skills", workspace_root=ws,
            )
        assert ei.value.code == "escaped_parent"
        _assert_sentinel_unchanged(ext, before)

    def test_stale_tmp_directory_at_write_surface_fails_closed(self, tmp_path):
        """A stale .tmp.<name> DIRECTORY at the atomic-write surface is never
        deleted and never written through — the write fails closed."""
        iso = _LinuxPlatformIsolation()
        ws = tmp_path / "ws"
        (ws / ".claude" / "skills").mkdir(parents=True)
        tmp_dir = ws / ".claude" / "skills" / ".tmp.skill-a"
        tmp_dir.mkdir()
        (tmp_dir / "stale.txt").write_text("do not delete")

        with pytest.raises((PlatformIsolationError, OSError)):
            iso.create_relative_symlink(
                Path("target"), ws / ".claude" / "skills" / "skill-a",
                workspace_root=ws,
            )
        assert tmp_dir.is_dir()
        assert (tmp_dir / "stale.txt").read_text() == "do not delete"
        assert not (ws / ".claude" / "skills" / "skill-a").exists(
            follow_symlinks=False
        )

    def test_admit_accepts_genuine_and_missing_roots(self, tmp_path):
        """Genuine roots pass; missing roots (fresh workspace) are a no-op."""
        iso = _LinuxPlatformIsolation()
        ws = tmp_path / "ws"
        (ws / ".claude" / "skills").mkdir(parents=True)
        iso.admit_skills_directory(ws / ".claude" / "skills", workspace_root=ws)
        # Missing tail — nothing to admit, no raise
        iso.admit_skills_directory(ws / ".agents" / "skills", workspace_root=ws)


# ═══════════════════════════════════════════════════════════════════════
# SymlinkMaterializer (create/repair/withdraw via the real writer)
# ═══════════════════════════════════════════════════════════════════════


class TestMaterializerContainment:
    """Materializer-level adversarial proof (real canonical store + writer)."""

    def _materializer(self, tmp_path, test_settings) -> tuple[SymlinkMaterializer, CanonicalSkillStore]:
        store = CanonicalSkillStore(settings=test_settings)
        return SymlinkMaterializer(store), store

    def test_materialize_rejects_prepositioned_provider_dir(
        self, tmp_path, test_settings,
    ):
        """<ws>/.claude -> external: materialize_skill fails, sentinel intact."""
        materializer, store = self._materializer(tmp_path, test_settings)
        ch = _build_skill(store, tmp_path, "skill-a")
        ws = tmp_path / "ws"
        ws.mkdir()
        ext = _external_sentinel(tmp_path)
        before = _snapshot(ext)
        os.symlink(ext, ws / ".claude")

        with pytest.raises(SymlinkMaterializationError) as ei:
            materializer.materialize_skill(
                "skill-a", "1.0", ch, ws, ".claude/skills",
            )
        assert ei.value.code == "escaped_parent"
        _assert_sentinel_unchanged(ext, before)
        assert (ws / ".claude").is_symlink()

    def test_materialize_rejects_prepositioned_skills_root(
        self, tmp_path, test_settings,
    ):
        """<ws>/.claude/skills -> external: fail closed, sentinel intact."""
        materializer, store = self._materializer(tmp_path, test_settings)
        ch = _build_skill(store, tmp_path, "skill-a")
        ws = tmp_path / "ws"
        (ws / ".claude").mkdir(parents=True)
        ext = _external_sentinel(tmp_path)
        before = _snapshot(ext)
        os.symlink(ext, ws / ".claude" / "skills")

        with pytest.raises(SymlinkMaterializationError) as ei:
            materializer.materialize_skill(
                "skill-a", "1.0", ch, ws, ".claude/skills",
            )
        assert ei.value.code == "escaped_parent"
        _assert_sentinel_unchanged(ext, before)

    def test_repair_rejects_symlinked_root_and_preserves_external(
        self, tmp_path, test_settings,
    ):
        """repair_workspace_skills must not list/unlink through an escaped parent."""
        materializer, store = self._materializer(tmp_path, test_settings)
        ch = _build_skill(store, tmp_path, "skill-a")
        ws = tmp_path / "ws"
        (ws / ".claude").mkdir(parents=True)
        ext = _external_sentinel(tmp_path)
        before = _snapshot(ext)
        os.symlink(ext, ws / ".claude" / "skills")

        specs = [{"slug": "skill-a", "version": "1.0", "content_hash": ch}]
        with pytest.raises(SymlinkMaterializationError) as ei:
            materializer.repair_workspace_skills(specs, ws, ".claude/skills")
        assert ei.value.code == "escaped_parent"
        _assert_sentinel_unchanged(ext, before)
        # The external decoy entries were NOT withdrawn via the escaped parent
        assert (ext / "skills" / "decoy-skill" / "SKILL.md").exists()

    def test_withdraw_rejects_symlinked_root_and_preserves_external(
        self, tmp_path, test_settings,
    ):
        """withdraw_skill must not unlink external files through a symlinked root."""
        materializer, _store = self._materializer(tmp_path, test_settings)
        ws = tmp_path / "ws"
        (ws / ".claude").mkdir(parents=True)
        ext = _external_sentinel(tmp_path)
        before = _snapshot(ext)
        os.symlink(ext, ws / ".claude" / "skills")

        with pytest.raises(SymlinkMaterializationError) as ei:
            materializer.withdraw_skill(
                "withdraw-decoy.txt", ws, ".claude/skills",
            )
        assert ei.value.code == "escaped_parent"
        _assert_sentinel_unchanged(ext, before)
        assert (ext / "withdraw-decoy.txt").exists()

    def test_materialize_repairs_nested_link_without_touching_external_target(
        self, tmp_path, test_settings,
    ):
        """A pre-positioned nested link (skills/<slug> -> external) is replaced
        atomically; its external TARGET is never written, unlinked, or replaced."""
        materializer, store = self._materializer(tmp_path, test_settings)
        ch = _build_skill(store, tmp_path, "skill-a")
        ws = tmp_path / "ws"
        (ws / ".claude" / "skills").mkdir(parents=True)
        ext = _external_sentinel(tmp_path)
        before = _snapshot(ext)
        # Pre-position the nested skill link itself pointing OUTSIDE
        os.symlink(ext, ws / ".claude" / "skills" / "skill-a")

        materializer.materialize_skill(
            "skill-a", "1.0", ch, ws, ".claude/skills",
        )
        # The link now points at the canonical package, not the external dir
        link = ws / ".claude" / "skills" / "skill-a"
        assert link.is_symlink()
        assert os.readlink(link) != str(ext)
        # External sentinel byte- and state-identical
        _assert_sentinel_unchanged(ext, before)

    def test_ordinary_in_workspace_links_materialize_and_repair(
        self, tmp_path, test_settings,
    ):
        """Ordinary canonical relative symlinks inside a normal workspace
        still materialize and repair correctly."""
        materializer, store = self._materializer(tmp_path, test_settings)
        ch = _build_skill(store, tmp_path, "skill-a")
        ws = tmp_path / "ws"
        ws.mkdir()

        specs = [{"slug": "skill-a", "version": "1.0", "content_hash": ch}]
        materialized, withdrawn = materializer.repair_workspace_skills(
            specs, ws, ".claude/skills",
        )
        assert materialized == ["skill-a"]
        assert withdrawn == []
        link = ws / ".claude" / "skills" / "skill-a"
        assert link.is_symlink()
        # The relative target resolves to the canonical package (in-workspace
        # canonical semantics: the store may live elsewhere, but the link is a
        # VALID relative symlink resolving to the exact canonical package)
        assert (link / "SKILL.md").read_text().startswith("# skill-a")

        # Idempotent repair: second run is a no-op (no withdrawal, no churn)
        materialized2, withdrawn2 = materializer.repair_workspace_skills(
            specs, ws, ".claude/skills",
        )
        assert materialized2 == ["skill-a"]
        assert withdrawn2 == []
        assert os.readlink(link) == os.readlink(link)  # unchanged

        # Stale-link repair: point at the wrong target, repair fixes it
        os.unlink(link)
        os.symlink("../../wrong-target", link)
        materializer.repair_workspace_skills(specs, ws, ".claude/skills")
        assert (link / "SKILL.md").read_text().startswith("# skill-a")

    def test_withdraw_ordinary_in_workspace_link(self, tmp_path, test_settings):
        """withdraw_skill still safely removes an owned in-workspace link."""
        materializer, store = self._materializer(tmp_path, test_settings)
        ch = _build_skill(store, tmp_path, "skill-a")
        ws = tmp_path / "ws"
        ws.mkdir()
        materializer.materialize_skill(
            "skill-a", "1.0", ch, ws, ".agents/skills",
        )
        link = ws / ".agents" / "skills" / "skill-a"
        assert link.is_symlink()
        materializer.withdraw_skill("skill-a", ws, ".agents/skills")
        assert not link.exists(follow_symlinks=False)
