"""TASK-4205: Adversarial pre-launch integrity validation tests.

Proves:
- Same-owner mutation to canonical bytes is possible (technically)
-Next launch integrity check detects mismatch BEFORE executor via hash
- Content-mutation with mode-restore (0444/0755) is detected by tree hash
- Same-owner mutation is technically possible (chmod+write is feasible)
- Durable audit event is written on mismatch
- Audit-write failure also blocks launch
- No auto-repair from same-UID local sources
- Both .claude/skills and .agents/skills are validated
- Executor switch path validates the resolved union
- Malicious/broken/unexpected links are detected
- Active-session TOCTOU residual risk is explicit
"""

import hashlib
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from runtime.infrastructure.database import Database
from runtime.orchestrator.workspace_adapters import (
    _compute_dir_hash,
    validate_workspace_skills_integrity,
    WorkspaceIntegrityError,
)
from runtime.skills.canonical_store import CanonicalSkillStore, CanonicalStoreError
from runtime.skills.symlink_materializer import SymlinkMaterializer


class TestBytecodePackageIntegrity:
    """Bytecode is incidental; ordinary package bytes remain fail-closed."""

    BYTECODE = (
        "loose.pyc", "loose.pyo", ".pyc",
        "scripts/__pycache__/x.cpython-312.pyc", "__pycache__/metadata",
    )
    CONTENT = (
        "SKILL.md", "scripts/run.py", "references/guide.md",
        "foo.pyc.txt", "pycache/data", "__pycache__.txt", "upper.PYC",
    )

    @pytest.fixture
    def package(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple:
        from runtime.infrastructure.artifact_store import ArtifactStore

        source = tmp_path / "source"
        for name in self.CONTENT:
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"original {name}\n")
        store = CanonicalSkillStore(root=tmp_path / "canonical")
        monkeypatch.setenv("HAPPYRANCH_CANONICAL_STORE_ROOT", str(store.root))
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        return source, store, workspace, ArtifactStore(tmp_path / "artifacts")

    @staticmethod
    def previous_hash(source: Path) -> str:
        # Frozen pre-fix algorithm: independent compatibility oracle only.
        digest = hashlib.sha256()
        for path in sorted(source.rglob("*")):
            if path.is_file():
                digest.update(str(path.relative_to(source)).encode())
                digest.update(b"\x00")
                digest.update(path.read_bytes())
                digest.update(b"\x00")
        return digest.hexdigest()

    @staticmethod
    def add_bytecode(root: Path) -> None:
        for name in TestBytecodePackageIntegrity.BYTECODE:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                path.chmod(0o644)
            path.write_bytes(b"generated Python cache")

    @staticmethod
    def materialize(store: CanonicalSkillStore, workspace: Path, identity: str, tree_hash: str) -> list[dict]:
        spec = dict(slug="bytecode-skill", version="system", content_hash=identity,
                    tree_hash=tree_hash)
        for subdir in (".claude/skills", ".agents/skills"):
            SymlinkMaterializer(store).materialize_skills_batch([spec], workspace, subdir)
        return [spec]

    def build(self, package: tuple, kind: str) -> tuple:
        source, store, workspace, artifacts = package
        tree_hash = _compute_dir_hash(source)
        manifest = None
        identity = tree_hash
        if kind == "manifest":
            import json

            members = []
            for index, path in enumerate(sorted(source.rglob("*"))):
                if path.is_file():
                    name = str(path.relative_to(source))
                    key = f"member-{index}.blob"
                    artifacts.put(key, path.read_bytes())
                    members.append(dict(path=name, artifact_key=key,
                                        hash="sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()))
            manifest = dict(members=members)
            identity = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
            target = store.build_from_manifest("bytecode-skill", "system", identity, manifest, artifacts)
        else:
            target = store.build_from_source("bytecode-skill", "system", identity, source,
                                             verify_source_hash=tree_hash)
        specs = self.materialize(store, workspace, identity, tree_hash)
        validate_workspace_skills_integrity(workspace, specs)
        return target, identity, tree_hash, specs, manifest

    def test_clean_tree_keeps_previous_identity(self, package: tuple) -> None:
        source, store, workspace, _ = package
        old_hash = self.previous_hash(source)
        assert _compute_dir_hash(source) == old_hash
        target, identity, _, specs, _ = self.build(package, "source")
        before = target.stat().st_ino
        assert identity == old_hash
        assert store.compute_tree_hash("bytecode-skill", "system", identity) == old_hash
        assert store.build_from_source("bytecode-skill", "system", identity, source,
                                       verify_source_hash=old_hash) == target
        assert target.stat().st_ino == before
        validate_workspace_skills_integrity(workspace, specs)

    @pytest.mark.parametrize("name", BYTECODE)
    def test_source_bytecode_keeps_identity(self, package: tuple, name: str) -> None:
        source, _, _, _ = package
        clean_hash = _compute_dir_hash(source)
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"generated Python cache")
        assert _compute_dir_hash(source) == clean_hash

    def test_source_bytecode_is_not_copied(self, package: tuple) -> None:
        source, _, _, _ = package
        self.add_bytecode(source)
        target, _, _, _, _ = self.build(package, "source")
        assert {str(p.relative_to(target)) for p in target.rglob("*") if p.is_file()} == set(self.CONTENT)
        assert not (target / "__pycache__").exists()
        assert not (target / "scripts/__pycache__").exists()

    @pytest.mark.parametrize("kind", ("source", "manifest"))
    def test_leaked_bytecode_passes_reuse_and_prelaunch(self, package: tuple, kind: str) -> None:
        source, store, workspace, artifacts = package
        target, identity, tree_hash, specs, manifest = self.build(package, kind)
        before = target.stat().st_ino
        self.add_bytecode(target)
        assert store.compute_tree_hash("bytecode-skill", "system", identity) == tree_hash
        if kind == "manifest":
            assert store.build_from_manifest("bytecode-skill", "system", identity, manifest, artifacts) == target
        else:
            assert store.build_from_source("bytecode-skill", "system", identity, source,
                                           verify_source_hash=tree_hash) == target
        assert target.stat().st_ino == before
        self.materialize(store, workspace, identity, tree_hash)
        validate_workspace_skills_integrity(workspace, specs)

    @pytest.mark.parametrize("kind", ("source", "manifest"))
    @pytest.mark.parametrize("name", CONTENT)
    def test_nonbytecode_tampering_refuses_reuse_and_prelaunch(self, package: tuple, kind: str, name: str) -> None:
        source, store, workspace, artifacts = package
        target, identity, tree_hash, specs, manifest = self.build(package, kind)
        path = target / name
        path.chmod(0o644)
        path.write_bytes(b"tampered content")
        path.chmod(0o444)
        with pytest.raises(CanonicalStoreError) as error:
            if kind == "manifest":
                store.build_from_manifest("bytecode-skill", "system", identity, manifest, artifacts)
            else:
                store.build_from_source("bytecode-skill", "system", identity, source,
                                        verify_source_hash=tree_hash)
        assert error.value.code == "content_corruption"
        with pytest.raises(WorkspaceIntegrityError) as error:
            validate_workspace_skills_integrity(workspace, specs)
        assert error.value.code == "integrity_mismatch"
        assert any("tree hash mismatch" in finding for finding in error.value.findings)
        assert path.read_bytes() == b"tampered content"  # Never healed.

    @pytest.mark.parametrize("name", ("extra.pyc.txt", "pycache/new.py"))
    def test_manifest_nonbytecode_extras_are_corruption(self, package: tuple, name: str) -> None:
        _, store, workspace, artifacts = package
        target, identity, _, specs, manifest = self.build(package, "manifest")
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.chmod(0o644)
        path.write_bytes(b"unexpected content")
        with pytest.raises(CanonicalStoreError, match="content_corruption"):
            store.build_from_manifest("bytecode-skill", "system", identity, manifest, artifacts)
        with pytest.raises(WorkspaceIntegrityError, match="integrity_mismatch"):
            validate_workspace_skills_integrity(workspace, specs)

    def test_bytecode_in_old_source_moves_identity_once(self, package: tuple) -> None:
        source, store, workspace, _ = package
        clean_hash = _compute_dir_hash(source)
        self.add_bytecode(source)
        old_hash = self.previous_hash(source)
        assert old_hash != clean_hash
        # Reproduce the old on-disk package, including its leaked cache bytes.
        old_target = store.build_from_source("bytecode-skill", "system", old_hash, source)
        self.add_bytecode(old_target)
        before = {str(p.relative_to(old_target)): p.read_bytes()
                  for p in old_target.rglob("*") if p.is_file()}
        self.materialize(store, workspace, old_hash, old_hash)
        target, identity, _, specs, _ = self.build(package, "source")
        assert identity == clean_hash
        assert target != old_target
        assert {str(p.relative_to(old_target)): p.read_bytes()
                for p in old_target.rglob("*") if p.is_file()} == before
        for subdir in (".claude/skills", ".agents/skills"):
            assert (workspace / subdir / "bytecode-skill").resolve() == target
        assert store.build_from_source("bytecode-skill", "system", identity, source,
                                       verify_source_hash=clean_hash) == target
        validate_workspace_skills_integrity(workspace, specs)

    def test_declared_bytecode_retains_manifest_provenance(self, package: tuple) -> None:
        from runtime.skills.canonical_store import _compute_tree_hash_from_manifest_members

        source, store, _, artifacts = package
        clean_hash = _compute_dir_hash(source)
        self.add_bytecode(source)
        target, identity, tree_hash, _, manifest = self.build(package, "manifest")
        expected = _compute_tree_hash_from_manifest_members(manifest, artifacts, skill_slug="bytecode-skill")
        assert expected == tree_hash == clean_hash
        # Declared members are preserved and checked, unlike undeclared extras.
        declared = target / "loose.pyc"
        declared.chmod(0o644)
        declared.write_bytes(b"changed declared member")
        with pytest.raises(CanonicalStoreError, match="content_corruption"):
            store.build_from_manifest("bytecode-skill", "system", identity, manifest, artifacts)
        key = next(member["artifact_key"] for member in manifest["members"] if member["path"] == "loose.pyc")
        artifacts.put(key, b"changed declared artifact")
        with pytest.raises(CanonicalStoreError, match="member_hash_mismatch"):
            _compute_tree_hash_from_manifest_members(manifest, artifacts, skill_slug="bytecode-skill")
        with pytest.raises(CanonicalStoreError, match="member_hash_mismatch"):
            store.build_from_manifest("bytecode-skill", "fresh", identity, manifest, artifacts)

    @pytest.mark.parametrize("inherited", (None, "", "0"))
    def test_executor_launch_environment_disables_bytecode(
        self, package: tuple, monkeypatch: pytest.MonkeyPatch, inherited: str | None,
    ) -> None:
        from runtime.orchestrator.executors import build_command_launch_spec

        _, _, workspace, _ = package
        if inherited is None:
            monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
        else:
            monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", inherited)
        spec = build_command_launch_spec(cmd=("python", "script.py"), workspace=workspace,
                                         input_text=None, org_slug="test", session_id="test-session")
        assert spec.env["PYTHONDONTWRITEBYTECODE"] == "1"


class TestPreLaunchIntegrityValidation:
    """Adversarial: same-owner process mutates canonical targets;
    next launch detects mismatch BEFORE executor; no auto-repair.

    The executor and daemon share the same OS identity — there is NO
    OS-level isolation. These tests exercise the chmod+mutate+restore
    attack pattern.
    """

    # ── helpers ────────────────────────────────────────────────────
    @staticmethod
    def _build_and_materialize(store, ws, slug, version, src_dir, subdirs):
        """Build from source with proper tree hash, materialize to subdirs."""
        ch = _compute_dir_hash(src_dir)
        store.build_from_source(slug, version, ch, src_dir)
        mat = SymlinkMaterializer(store)
        specs = [{"slug": slug, "version": version, "content_hash": ch}]
        for sd in subdirs:
            mat.materialize_skills_batch(specs, ws, sd)
        return specs

    # ── Hash-based detection tests (Finding 1) ────────────────────

    def test_same_owner_mutation_detected_by_hash(self, tmp_path):
        """Same-UID mutates content, restores permissions — hash catches it.

        A mutator who chmods writable, mutates content, then restores
        non-writable would evade permission-only checks. The tree hash
        comparison in the integrity validation catches the content change.
        """
        canonical_root = tmp_path / "canonical"
        os.environ["HAPPYRANCH_CANONICAL_STORE_ROOT"] = str(canonical_root)
        try:
            store = CanonicalSkillStore(root=canonical_root)

            src = tmp_path / "src"
            src.mkdir()
            (src / "SKILL.md").write_text("# Skill\n")
            import stat
            (src / "SKILL.md").chmod(0o644)

            ws = tmp_path / "ws"
            ws.mkdir()
            (ws / ".claude" / "skills").mkdir(parents=True)
            (ws / ".agents" / "skills").mkdir(parents=True)

            specs = self._build_and_materialize(
                store, ws, "sk", "1.0.0", src,
                [".claude/skills", ".agents/skills"],
            )

            # Pass initially — content matches
            validate_workspace_skills_integrity(ws, specs, agent_name="a")

            # Same-owner mutates canonical content
            target = store.canonical_path("sk", "1.0.0", specs[0]["content_hash"])
            skill_md = target / "SKILL.md"
            skill_md.chmod(0o644)  # same-owner can chmod
            orig = skill_md.read_bytes()
            corr = bytearray(orig)
            corr[0] ^= 0xFF
            skill_md.write_bytes(bytes(corr))
            # Restore 0o444 — mode check alone would pass!
            skill_md.chmod(0o444)
            assert skill_md.read_bytes() != orig, "Mutation failed"
            assert (skill_md.stat().st_mode & stat.S_IWUSR) == 0, \
                "Mode restored to non-writable"

            # Next validation MUST detect mismatch via hash, not mode
            with pytest.raises(WorkspaceIntegrityError) as e:
                validate_workspace_skills_integrity(ws, specs, agent_name="a")
            assert e.value.code == "integrity_mismatch"
            assert any(
                "Canonical package integrity failure" in f
                or "hash mismatch" in f.lower()
                for f in e.value.findings
            ), f"Findings: {e.value.findings}"
            assert "set-executor" in (e.value.recovery_command or "")
        finally:
            os.environ.pop("HAPPYRANCH_CANONICAL_STORE_ROOT", None)

    def test_same_owner_mutation_possible(self, tmp_path):
        """Prove same-owner mutation is technically possible (chmod+write).

        This test documents the residual TOCTOU risk: a same-owner executor
        can read/write canonical targets between integrity checks. The
        detective control in validate_workspace_skills_integrity catches
        mutations at the NEXT launch attempt — it does NOT prevent active-
        session modification or guarantee real-time isolation.
        """
        canonical_root = tmp_path / "canonical"
        os.environ["HAPPYRANCH_CANONICAL_STORE_ROOT"] = str(canonical_root)
        try:
            store = CanonicalSkillStore(root=canonical_root)
            src = tmp_path / "src"
            src.mkdir()
            (src / "SKILL.md").write_text("# TOCTOU\n")
            ch = _compute_dir_hash(src)
            store.build_from_source("toc", "1.0.0", ch, src)

            target = store.canonical_path("toc", "1.0.0", ch)
            skill_md = target / "SKILL.md"

            # Prove: same-owner CAN chmod + write canonical files
            import stat
            original_mode = stat.S_IMODE(skill_md.stat().st_mode)
            skill_md.chmod(0o644)
            assert (skill_md.stat().st_mode & stat.S_IWUSR) != 0, \
                "chmod to 0o644 should grant owner write"
            skill_md.write_bytes(b"mutated by same owner")
            assert skill_md.read_bytes() == b"mutated by same owner", \
                "Same-owner write succeeded — mutation is technically possible"
            # Restore
            skill_md.chmod(original_mode)
        finally:
            os.environ.pop("HAPPYRANCH_CANONICAL_STORE_ROOT", None)

    def test_mode_restore_evades_mode_check_but_hash_catches(self, tmp_path):
        """Mutate content + restore 0o444 → permission check passes, hash fails.

        This is the canonical adversary scenario: same-UID executor:
        1. chmod 0644 (owner writable)
        2. Mutate content
        3. chmod 0444 (restore compliant mode)
        → permission-only check can be evaded, but tree hash validation in
          validate_workspace_skills_integrity detects the content change.
        """
        canonical_root = tmp_path / "canonical"
        os.environ["HAPPYRANCH_CANONICAL_STORE_ROOT"] = str(canonical_root)
        try:
            store = CanonicalSkillStore(root=canonical_root)

            src = tmp_path / "src"
            src.mkdir()
            (src / "SKILL.md").write_text("# Original\n")
            import stat
            (src / "SKILL.md").chmod(0o644)

            ch = _compute_dir_hash(src)
            store.build_from_source("evade", "1.0.0", ch, src)
            pkg_path = store.canonical_path("evade", "1.0.0", ch)

            # Set up workspace and materialize
            ws = tmp_path / "ws"
            ws.mkdir()
            (ws / ".claude" / "skills").mkdir(parents=True)
            (ws / ".agents" / "skills").mkdir(parents=True)
            mat = SymlinkMaterializer(store)
            specs = [{"slug": "evade", "version": "1.0.0", "content_hash": ch}]
            mat.materialize_skills_batch(specs, ws, ".claude/skills")
            mat.materialize_skills_batch(specs, ws, ".agents/skills")

            # 1. Verify initial state passes
            validate_workspace_skills_integrity(ws, specs, agent_name="a")

            # 2. Same-owner attack sequence
            skill_md = pkg_path / "SKILL.md"
            skill_md.chmod(0o644)
            skill_md.write_bytes(b"Evil content!")
            skill_md.chmod(0o444)  # restore compliant mode

            # 3. Mode check alone passes (same-owner allows owner-writable)
            #    store.verify_package would pass here.
            #    But validate_workspace_skills_integrity includes hash → fails
            with pytest.raises(WorkspaceIntegrityError) as exc:
                validate_workspace_skills_integrity(ws, specs, agent_name="a")
            assert exc.value.code == "integrity_mismatch", \
                f"Expected integrity_mismatch, got {exc.value.code}"
        finally:
            os.environ.pop("HAPPYRANCH_CANONICAL_STORE_ROOT", None)

    # ── Audit / fail-closed tests ──────────────────────────────────

    def test_audit_event_emitted_on_mismatch(self, tmp_path):
        """Mismatch writes durable skill_validation_events row."""
        canonical_root = tmp_path / "canonical"
        os.environ["HAPPYRANCH_CANONICAL_STORE_ROOT"] = str(canonical_root)
        try:
            store = CanonicalSkillStore(root=canonical_root)

            src = tmp_path / "src"
            src.mkdir()
            (src / "SKILL.md").write_text("# Audit\n")

            ws = tmp_path / "ws"
            ws.mkdir()
            (ws / ".claude" / "skills").mkdir(parents=True)
            (ws / ".agents" / "skills").mkdir(parents=True)

            specs = self._build_and_materialize(
                store, ws, "aud", "1.0.0", src,
                [".claude/skills", ".agents/skills"],
            )

            db = Database(tmp_path / "test.db")
            target = store.canonical_path("aud", "1.0.0", specs[0]["content_hash"])
            skill_md = target / "SKILL.md"
            skill_md.chmod(0o644)
            skill_md.write_bytes(b"corrupted!")
            skill_md.chmod(0o444)

            with pytest.raises(WorkspaceIntegrityError):
                validate_workspace_skills_integrity(
                    ws, specs, db=db, agent_name="dev", task_id="T1",
                )

            evts = db.list_skill_validation_events(
                skill_id="hr:workspace-integrity", severity="error", limit=10,
            )
            assert len(evts) >= 1
            evt = evts[0]
            assert evt["source"] == "integrity_check"
            assert evt["severity"] == "error"
            assert evt["ok"] == 0
        finally:
            os.environ.pop("HAPPYRANCH_CANONICAL_STORE_ROOT", None)

    def test_audit_write_failure_blocks_launch(self, tmp_path):
        """Audit persistence failure also refuses launch."""
        canonical_root = tmp_path / "canonical"
        os.environ["HAPPYRANCH_CANONICAL_STORE_ROOT"] = str(canonical_root)
        try:
            store = CanonicalSkillStore(root=canonical_root)

            src = tmp_path / "src"
            src.mkdir()
            (src / "SKILL.md").write_text("# X\n")

            ws = tmp_path / "ws"
            ws.mkdir()
            (ws / ".claude" / "skills").mkdir(parents=True)
            (ws / ".agents" / "skills").mkdir(parents=True)

            ch = _compute_dir_hash(src)
            store.build_from_source("x", "1.0.0", ch, src)
            mat = SymlinkMaterializer(store)
            specs = [{"slug": "x", "version": "1.0.0", "content_hash": ch}]
            mat.materialize_skills_batch(specs, ws, ".claude/skills")

            target = store.canonical_path("x", "1.0.0", ch)
            skill_md = target / "SKILL.md"
            skill_md.chmod(0o644)
            skill_md.write_bytes(b"corrupted!")
            skill_md.chmod(0o444)

            mock_db = MagicMock()
            mock_db.insert_skill_validation_event.side_effect = RuntimeError("full")

            with pytest.raises(WorkspaceIntegrityError) as exc:
                validate_workspace_skills_integrity(
                    ws, specs, db=mock_db, agent_name="a",
                )
            assert any(
                "audit" in f.lower() or "Audit" in f for f in exc.value.findings
            )
        finally:
            os.environ.pop("HAPPYRANCH_CANONICAL_STORE_ROOT", None)

    # ── No-auto-repair tests ───────────────────────────────────────

    def test_no_auto_repair(self, tmp_path):
        """Mismatch is raised; canonical bytes stay corrupted (no auto-repair)."""
        canonical_root = tmp_path / "canonical"
        os.environ["HAPPYRANCH_CANONICAL_STORE_ROOT"] = str(canonical_root)
        try:
            store = CanonicalSkillStore(root=canonical_root)

            src = tmp_path / "src"
            src.mkdir()
            (src / "SKILL.md").write_text("# NoRepair\n")

            ws = tmp_path / "ws"
            ws.mkdir()
            (ws / ".claude" / "skills").mkdir(parents=True)
            (ws / ".agents" / "skills").mkdir(parents=True)

            ch = _compute_dir_hash(src)
            store.build_from_source("nr", "1.0.0", ch, src)
            mat = SymlinkMaterializer(store)
            specs = [{"slug": "nr", "version": "1.0.0", "content_hash": ch}]
            mat.materialize_skills_batch(specs, ws, ".claude/skills")

            target = store.canonical_path("nr", "1.0.0", ch)
            skill_md = target / "SKILL.md"
            assert skill_md.read_bytes() == b"# NoRepair\n"
            skill_md.chmod(0o644)
            skill_md.write_bytes(b"corrupted!")
            skill_md.chmod(0o444)

            with pytest.raises(WorkspaceIntegrityError):
                validate_workspace_skills_integrity(ws, specs, agent_name="a")

            # Still corrupted — no auto-repair from same-UID local sources
            assert (target / "SKILL.md").read_bytes() == b"corrupted!"
        finally:
            os.environ.pop("HAPPYRANCH_CANONICAL_STORE_ROOT", None)

    # ── Both-roots + unexpected-entry tests ────────────────────────

    def test_both_roots_validated(self, tmp_path):
        """Validation checks BOTH .claude/skills and .agents/skills."""
        canonical_root = tmp_path / "canonical"
        os.environ["HAPPYRANCH_CANONICAL_STORE_ROOT"] = str(canonical_root)
        try:
            store = CanonicalSkillStore(root=canonical_root)

            src = tmp_path / "src"
            src.mkdir()
            (src / "SKILL.md").write_text("# Both\n")

            ws = tmp_path / "ws"
            ws.mkdir()
            (ws / ".claude" / "skills").mkdir(parents=True)
            (ws / ".agents" / "skills").mkdir(parents=True)

            ch = _compute_dir_hash(src)
            store.build_from_source("both", "1.0.0", ch, src)
            mat = SymlinkMaterializer(store)
            specs = [{"slug": "both", "version": "1.0.0", "content_hash": ch}]
            # Materialize only .claude, not .agents
            mat.materialize_skills_batch(specs, ws, ".claude/skills")

            with pytest.raises(WorkspaceIntegrityError) as exc:
                validate_workspace_skills_integrity(ws, specs, agent_name="a")
            assert any(".agents/skills" in f for f in exc.value.findings)
        finally:
            os.environ.pop("HAPPYRANCH_CANONICAL_STORE_ROOT", None)

    def test_unexpected_entries_detected(self, tmp_path):
        """Unexpected entries in workspace skill dirs are flagged."""
        canonical_root = tmp_path / "canonical"
        os.environ["HAPPYRANCH_CANONICAL_STORE_ROOT"] = str(canonical_root)
        try:
            store = CanonicalSkillStore(root=canonical_root)

            src = tmp_path / "src"
            src.mkdir()
            (src / "SKILL.md").write_text("# C\n")

            ws = tmp_path / "ws"
            ws.mkdir()
            (ws / ".claude" / "skills").mkdir(parents=True)
            (ws / ".agents" / "skills").mkdir(parents=True)

            specs = self._build_and_materialize(
                store, ws, "c", "1.0.0", src,
                [".claude/skills", ".agents/skills"],
            )

            # Plant unexpected symlink
            os.symlink("/etc/hosts", str(ws / ".claude" / "skills" / "surprise"))

            with pytest.raises(WorkspaceIntegrityError) as exc:
                validate_workspace_skills_integrity(ws, specs, agent_name="a")
            assert any(
                "Unexpected" in f and "surprise" in f for f in exc.value.findings
            ), f"Got: {exc.value.findings}"
        finally:
            os.environ.pop("HAPPYRANCH_CANONICAL_STORE_ROOT", None)

    def test_malicious_external_link_detected(self, tmp_path):
        """Wrong/external link targets are detected."""
        canonical_root = tmp_path / "canonical"
        os.environ["HAPPYRANCH_CANONICAL_STORE_ROOT"] = str(canonical_root)
        try:
            store = CanonicalSkillStore(root=canonical_root)

            src = tmp_path / "src"
            src.mkdir()
            (src / "SKILL.md").write_text("# M\n")

            ws = tmp_path / "ws"
            ws.mkdir()
            (ws / ".claude" / "skills").mkdir(parents=True)
            (ws / ".agents" / "skills").mkdir(parents=True)

            ch = _compute_dir_hash(src)
            store.build_from_source("m", "1.0.0", ch, src)
            mat = SymlinkMaterializer(store)
            specs = [{"slug": "m", "version": "1.0.0", "content_hash": ch}]
            mat.materialize_skills_batch(specs, ws, ".claude/skills")

            # Replace symlink with external target
            link_path = ws / ".claude" / "skills" / "m"
            link_path.unlink()
            os.symlink("/tmp", str(link_path))

            with pytest.raises(WorkspaceIntegrityError) as exc:
                validate_workspace_skills_integrity(ws, specs, agent_name="a")
            assert any(
                "Wrong/mismatched" in f for f in exc.value.findings
            ), f"Got: {exc.value.findings}"
        finally:
            os.environ.pop("HAPPYRANCH_CANONICAL_STORE_ROOT", None)

    # ── Executor-switch validation tests (Finding 3) ───────────────

    def test_executor_switch_validates_union(self, tmp_path):
        """Executor switch path: validate_workspace_skills_integrity
        catches mismatches before reporting success."""
        canonical_root = tmp_path / "canonical"
        os.environ["HAPPYRANCH_CANONICAL_STORE_ROOT"] = str(canonical_root)
        try:
            store = CanonicalSkillStore(root=canonical_root)

            src = tmp_path / "src"
            src.mkdir()
            (src / "SKILL.md").write_text("# Switch\n")

            ws = tmp_path / "ws"
            ws.mkdir()
            (ws / ".claude" / "skills").mkdir(parents=True)
            (ws / ".agents" / "skills").mkdir(parents=True)

            specs = self._build_and_materialize(
                store, ws, "switch", "1.0.0", src,
                [".claude/skills", ".agents/skills"],
            )

            # Initial pass
            validate_workspace_skills_integrity(ws, specs, agent_name="a")

            # Corrupt canonical content (simulating same-owner mutation
            # between materialization and integrity check)
            target = store.canonical_path("switch", "1.0.0", specs[0]["content_hash"])
            skill_md = target / "SKILL.md"
            skill_md.chmod(0o644)
            skill_md.write_bytes(b"switched and corrupted!")
            skill_md.chmod(0o444)

            # Executor switch MUST fail
            with pytest.raises(WorkspaceIntegrityError) as e:
                validate_workspace_skills_integrity(ws, specs, agent_name="a")
            assert e.value.code == "integrity_mismatch"
            assert "set-executor" in (e.value.recovery_command or ""), \
                "Recovery command must document manual recovery"
        finally:
            os.environ.pop("HAPPYRANCH_CANONICAL_STORE_ROOT", None)

    # ── Claude + AGENTS root tests ─────────────────────────────────

    def test_claude_root_validated(self, tmp_path):
        """Validation checks .claude/skills root for Claude provider."""
        canonical_root = tmp_path / "canonical"
        os.environ["HAPPYRANCH_CANONICAL_STORE_ROOT"] = str(canonical_root)
        try:
            store = CanonicalSkillStore(root=canonical_root)

            src = tmp_path / "src"
            src.mkdir()
            (src / "SKILL.md").write_text("# Claude\n")

            ws = tmp_path / "ws"
            ws.mkdir()
            (ws / ".claude" / "skills").mkdir(parents=True)
            (ws / ".agents" / "skills").mkdir(parents=True)

            ch = _compute_dir_hash(src)
            store.build_from_source("cl", "1.0.0", ch, src)
            mat = SymlinkMaterializer(store)
            specs = [{"slug": "cl", "version": "1.0.0", "content_hash": ch}]
            # Materialize only .agents, not .claude
            mat.materialize_skills_batch(specs, ws, ".agents/skills")

            with pytest.raises(WorkspaceIntegrityError) as exc:
                validate_workspace_skills_integrity(ws, specs, agent_name="a")
            assert any(
                ".claude/skills" in f for f in exc.value.findings
            ), f"Expected .claude/skills finding, got: {exc.value.findings}"
        finally:
            os.environ.pop("HAPPYRANCH_CANONICAL_STORE_ROOT", None)

    def test_agents_root_validated(self, tmp_path):
        """Validation checks .agents/skills root for non-Claude provider."""
        canonical_root = tmp_path / "canonical"
        os.environ["HAPPYRANCH_CANONICAL_STORE_ROOT"] = str(canonical_root)
        try:
            store = CanonicalSkillStore(root=canonical_root)

            src = tmp_path / "src"
            src.mkdir()
            (src / "SKILL.md").write_text("# Agents\n")

            ws = tmp_path / "ws"
            ws.mkdir()
            (ws / ".claude" / "skills").mkdir(parents=True)
            (ws / ".agents" / "skills").mkdir(parents=True)

            ch = _compute_dir_hash(src)
            store.build_from_source("ag", "1.0.0", ch, src)
            mat = SymlinkMaterializer(store)
            specs = [{"slug": "ag", "version": "1.0.0", "content_hash": ch}]
            # Materialize only .claude, not .agents
            mat.materialize_skills_batch(specs, ws, ".claude/skills")

            with pytest.raises(WorkspaceIntegrityError) as exc:
                validate_workspace_skills_integrity(ws, specs, agent_name="a")
            assert any(
                ".agents/skills" in f for f in exc.value.findings
            ), f"Expected .agents/skills finding, got: {exc.value.findings}"
        finally:
            os.environ.pop("HAPPYRANCH_CANONICAL_STORE_ROOT", None)

    # ── Retry path tests ───────────────────────────────────────────

    def test_retry_integrity_check_catches_mutation(self, tmp_path):
        """Per-retry integrity check catches mutation between attempts."""
        canonical_root = tmp_path / "canonical"
        os.environ["HAPPYRANCH_CANONICAL_STORE_ROOT"] = str(canonical_root)
        try:
            store = CanonicalSkillStore(root=canonical_root)
            src = tmp_path / "src"
            src.mkdir()
            (src / "SKILL.md").write_text("# Retry\n")

            ws = tmp_path / "ws"
            ws.mkdir()
            (ws / ".claude" / "skills").mkdir(parents=True)
            (ws / ".agents" / "skills").mkdir(parents=True)

            specs = self._build_and_materialize(
                store, ws, "retry", "1.0.0", src,
                [".claude/skills", ".agents/skills"],
            )

            # First check passes
            validate_workspace_skills_integrity(ws, specs, agent_name="a")

            # Corrupt between "retries"
            target = store.canonical_path("retry", "1.0.0", specs[0]["content_hash"])
            skill_md = target / "SKILL.md"
            skill_md.chmod(0o644)
            skill_md.write_bytes(b"corrupted between retries!")
            skill_md.chmod(0o444)

            # Retry check MUST catch it
            with pytest.raises(WorkspaceIntegrityError) as e:
                validate_workspace_skills_integrity(ws, specs, agent_name="a")
            assert e.value.code == "integrity_mismatch"
            assert "set-executor" in (e.value.recovery_command or "")
        finally:
            os.environ.pop("HAPPYRANCH_CANONICAL_STORE_ROOT", None)

    # ── TOCTOU explicit documentation ──────────────────────────────

    def test_toctou_residual_risk_explicit(self, tmp_path):
        """Document TOCTOU risk: active-session mutation not prevented.

        This test DOES NOT fail — it documents the residual risk explicitly.
        A same-owner agent process running in an already-launched session
        can mutate canonical targets through workspace links. The integrity
        check runs before launch — it does NOT monitor the running process.
        """
        canonical_root = tmp_path / "canonical"
        os.environ["HAPPYRANCH_CANONICAL_STORE_ROOT"] = str(canonical_root)
        try:
            store = CanonicalSkillStore(root=canonical_root)
            src = tmp_path / "src"
            src.mkdir()
            (src / "SKILL.md").write_text("# Active\n")

            ch = _compute_dir_hash(src)
            store.build_from_source("active", "1.0.0", ch, src)

            # Simulate: integrity check passes → executor launches
            # During active session: same-owner executor mutates target
            target = store.canonical_path("active", "1.0.0", ch)
            skill_md = target / "SKILL.md"
            skill_md.chmod(0o644)
            skill_md.write_bytes(b"mutated during active session!")
            skill_md.chmod(0o444)

            # Prove: mutation SUCCEEDED during active session
            assert skill_md.read_bytes() == b"mutated during active session!", \
                "TOCTOU: mutation succeeded during active session window"

            # The integrity check at NEXT launch would catch it,
            # but the CURRENT session is already running with stale checks.
            # This is an explicitly documented residual risk of same-owner operation.
        finally:
            os.environ.pop("HAPPYRANCH_CANONICAL_STORE_ROOT", None)


    def test_recovery_command_ordering_authoritative_redeploy_first(self, tmp_path):
        """Recovery payload ordering: FIRST authoritative external re-sync/redeploy
        before EVERY set-executor, skills recover, and restart instruction.

        The emitted WorkspaceIntegrityError recovery_command is the operator-visible
        guidance string. It MUST order steps correctly:
        1. FIRST manual authoritative external re-sync/redeploy of release/custom
           artifacts (the prerequisite).
        2. ONLY THEN verified link repair (set-executor) OR skills recover + restart.
        3. Local same-UID sources are not automatically repaired or trusted.

        This test drives the real mismatch/refusal path and asserts positional
        ordering in the emitted payload.
        """
        canonical_root = tmp_path / "canonical"
        os.environ["HAPPYRANCH_CANONICAL_STORE_ROOT"] = str(canonical_root)
        try:
            store = CanonicalSkillStore(root=canonical_root)
            import stat
            src = tmp_path / "src"
            src.mkdir()
            (src / "SKILL.md").write_text("# Ordering Skill\n")
            (src / "SKILL.md").chmod(0o644)

            ws = tmp_path / "ws"
            ws.mkdir()
            (ws / ".claude" / "skills").mkdir(parents=True)
            (ws / ".agents" / "skills").mkdir(parents=True)

            specs = self._build_and_materialize(
                store, ws, "order", "1.0.0", src,
                [".claude/skills", ".agents/skills"],
            )

            # Initial validation passes
            validate_workspace_skills_integrity(ws, specs, agent_name="a")

            # Corrupt canonical content to trigger mismatch
            target = store.canonical_path("order", "1.0.0", specs[0]["content_hash"])
            skill_md = target / "SKILL.md"
            skill_md.chmod(0o644)
            skill_md.write_bytes(b"corrupted!")
            skill_md.chmod(0o444)

            # Drive the mismatch/refusal path
            with pytest.raises(WorkspaceIntegrityError) as exc:
                validate_workspace_skills_integrity(ws, specs, agent_name="a")

            assert exc.value.code == "integrity_mismatch"

            recovery = exc.value.recovery_command or ""
            assert recovery, "Recovery command must be non-empty"

            # ── Positional ordering assertions ──────────────────
            # FIRST authoritative external re-sync/redeploy appears
            # before EVERY repair/recover/restart action.
            assert "FIRST" in recovery, (
                f"Recovery command must lead with FIRST: {recovery[:80]}..."
            )
            assert "ONLY THEN" in recovery, (
                f"Recovery command must have ONLY THEN gate: {recovery[:80]}..."
            )

            # Authoritative external re-sync/redeploy appears BEFORE set-executor
            authoritative_pos = recovery.index(
                "authoritative external re-sync/redeploy")
            set_executor_pos = recovery.index("set-executor")
            assert authoritative_pos < set_executor_pos, (
                f"authoritative external re-sync/redeploy (pos {authoritative_pos}) "
                f"must precede set-executor (pos {set_executor_pos})"
            )

            # Authoritative external re-sync/redeploy appears BEFORE skills recover
            recover_pos = recovery.index("skills recover")
            assert authoritative_pos < recover_pos, (
                f"authoritative external re-sync/redeploy (pos {authoritative_pos}) "
                f"must precede skills recover (pos {recover_pos})"
            )

            # Authoritative external re-sync/redeploy appears BEFORE restart
            restart_pos = recovery.index("restart daemon")
            assert authoritative_pos < restart_pos, (
                f"authoritative external re-sync/redeploy (pos {authoritative_pos}) "
                f"must precede restart daemon (pos {restart_pos})"
            )

            # ── No automatic/trusted local same-UID repair ──────
            assert "not automatically repaired" in recovery, (
                f"Recovery must state local same-UID sources are not "
                f"automatically repaired: {recovery[-120:]}"
            )
            assert "not automatically" in recovery and "trusted" in recovery, (
                f"Recovery must state local same-UID sources are not trusted: "
                f"{recovery[-120:]}"
            )
        finally:
            os.environ.pop("HAPPYRANCH_CANONICAL_STORE_ROOT", None)
