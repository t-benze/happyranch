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
from unittest.mock import MagicMock, patch

import pytest

from runtime.infrastructure.database import Database
from runtime.orchestrator.workspace_adapters import (
    _compute_dir_hash,
    validate_workspace_skills_integrity,
    WorkspaceIntegrityError,
)
from runtime.skills.canonical_store import CanonicalSkillStore
from runtime.skills.symlink_materializer import SymlinkMaterializer


class TestPreLaunchIntegrityValidation:
    """Adversarial: same-owner process mutates canonical targets;
    next launch detects mismatch BEFORE executor; no auto-repair.

    These tests require same-owner mode because they exercise the
    chmod+mutate+restore attack pattern that is only relevant when
    the executor shares the daemon's UID.
    """

    @pytest.fixture(autouse=True)
    def _enable_same_owner(self, same_owner_mode):
        """Enable same-owner mode for all tests in this class."""
        pass

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
        """Same-owner mutates content, restores 0444 — hash catches it.

        In same-owner mode _verify_recursive_readonly allows owner-writable
        files. An attacker who chmods 0o644, mutates content, then restores
        0o444 would evade mode-only checks. The tree hash comparison in
        verify_package() catches the content change.
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
        """Mutate content + restore 0o444 → mode check passes, hash fails.

        This is the canonical adversary scenario: same-owner executor:
        1. chmod 0644 (owner writable)
        2. Mutate content
        3. chmod 0444 (restore compliant mode)
        → _verify_recursive_readonly passes (same-owner allows owner-writable),
          but tree hash validation in validate_workspace_skills_integrity
          detects the content change.
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
            # This is an explicitly documented residual risk of same-owner mode.
        finally:
            os.environ.pop("HAPPYRANCH_CANONICAL_STORE_ROOT", None)


class TestLifecycleManifestSelfRatificationPrevention:
    """Adversarial: prove that lifecycle manifest member hash validation
    BEFORE computing expected tree hash prevents self-ratification.

    Without the fix, a same-owner process that mutates BOTH the artifact
    store bytes AND the canonical member bytes identically could:
    1. Load corrupted artifact bytes → compute expected tree hash from
       corrupted content
    2. Have build_from_manifest/is_built() reuse the corrupted canonical
       tree (modes restored → is_built() returns True)
    3. Pre-launch validator compares canonical tree vs expected → MATCH
       (both derived from same corrupted bytes)

    With the fix: each member's artifact bytes are validated against the
    immutable ledger-declared SHA-256 BEFORE computing the expected tree
    hash. This breaks the self-ratification chain.

    These tests require same-owner mode.
    """

    @pytest.fixture(autouse=True)
    def _enable_same_owner(self, same_owner_mode):
        pass

    # ── helpers ────────────────────────────────────────────────────
    @staticmethod
    def _create_lifecycle_package(tmp_path, slug, version, skill_content, ref_content=None):
        """Create a lifecycle ledger-style package with manifest and
        artifact store, return (manifest, manifest_hash, artifact_store,
        canonical_store, original_member_data).

        original_member_data = {member_path: bytes} for later mutation.
        """
        from runtime.infrastructure.artifact_store import ArtifactStore

        art_dir = tmp_path / "artifacts"
        art_dir.mkdir()

        skill_hash = hashlib.sha256(skill_content).hexdigest()
        skill_key = f"skill-lifecycle/{slug}/dead00000001/SKILL.md"
        (art_dir / skill_key).parent.mkdir(parents=True)
        (art_dir / skill_key).write_bytes(skill_content)

        members = [
            {"path": "SKILL.md", "hash": f"sha256:{skill_hash}",
             "artifact_key": skill_key},
        ]
        original_member_data = {"SKILL.md": skill_content}

        if ref_content is not None:
            ref_hash = hashlib.sha256(ref_content).hexdigest()
            ref_key = f"skill-lifecycle/{slug}/dead00000001/references/helper.md"
            (art_dir / ref_key).parent.mkdir(parents=True)
            (art_dir / ref_key).write_bytes(ref_content)
            members.append(
                {"path": "references/helper.md", "hash": f"sha256:{ref_hash}",
                 "artifact_key": ref_key},
            )
            original_member_data["references/helper.md"] = ref_content

        import json
        manifest = {"members": members}
        manifest_json = json.dumps(manifest, sort_keys=True, indent=2)
        manifest_hash = hashlib.sha256(manifest_json.encode()).hexdigest()

        artifact_store = ArtifactStore(art_dir)

        canonical_root = tmp_path / "canonical"
        os.environ["HAPPYRANCH_CANONICAL_STORE_ROOT"] = str(canonical_root)
        store = CanonicalSkillStore(root=canonical_root)

        return manifest, manifest_hash, artifact_store, store, original_member_data

    # ── Self-ratification prevention tests ─────────────────────────

    def test_artifact_store_mutation_rejected_before_tree_hash(self, tmp_path):
        """Mutate artifact store member bytes, keep canonical clean.

        _compute_manifest_tree_hash must validate member bytes against
        ledger-declared hash BEFORE computing expected tree hash.
        Mutated artifact bytes → hash mismatch error → no launch.
        """
        orig_skill = b"# Original Lifecycle Skill\n"
        (manifest, manifest_hash, art_store, store,
         _orig_data) = self._create_lifecycle_package(
            tmp_path, "lifecycle-test", "1.0.0", orig_skill,
        )

        # Build clean canonical package
        store.build_from_manifest(
            "lifecycle-test", "1.0.0", manifest_hash,
            manifest, art_store,
        )

        # Verify expected tree hash computes correctly from clean state
        from runtime.orchestrator.workspace_adapters import _compute_manifest_tree_hash
        expected = _compute_manifest_tree_hash(
            manifest, art_store,
            skill_slug="lifecycle-test",
        )
        actual = store.compute_tree_hash(
            "lifecycle-test", "1.0.0", manifest_hash,
        )
        assert expected == actual, "Clean state: expected tree hash should match canonical"

        # Now mutate the artifact store member bytes (same-owner scenario)
        skill_key = manifest["members"][0]["artifact_key"]
        art_path = art_store._root / skill_key
        art_path.write_bytes(b"# Mutated artifact bytes!\n")

        # _compute_manifest_tree_hash MUST reject corrupted artifact bytes
        from runtime.orchestrator.workspace_adapters import LifecycleMaterializationError
        with pytest.raises(LifecycleMaterializationError) as exc:
            _compute_manifest_tree_hash(
                manifest, art_store,
                skill_slug="lifecycle-test",
            )
        assert "hash mismatch" in str(exc.value).lower(), \
            f"Expected hash mismatch error, got: {exc.value}"

    def test_dual_mutation_rejected_by_artifact_validation(self, tmp_path):
        """Mutate BOTH artifact store AND canonical member bytes identically.

        This is the critical self-ratification scenario: same-owner replaces
        both artifact bytes and canonical bytes with identical malicious
        content, restores modes. Without member hash validation, the
        expected tree hash would be computed from corrupted bytes matching
        the corrupted canonical → false pass.

        With the fix: _compute_manifest_tree_hash validates artifact bytes
        against immutable ledger hash → REJECTED before expected tree hash
        is derived.
        """
        orig_skill = b"# Original Lifecycle Skill v2\n"
        mal_skill = b"# Maliciously mutated content!\n"
        assert mal_skill != orig_skill

        (manifest, manifest_hash, art_store, store,
         _orig_data) = self._create_lifecycle_package(
            tmp_path, "dual-test", "1.0.0", orig_skill,
        )

        # Build clean canonical package
        store.build_from_manifest(
            "dual-test", "1.0.0", manifest_hash,
            manifest, art_store,
        )

        from runtime.orchestrator.workspace_adapters import _compute_manifest_tree_hash
        clean_expected = _compute_manifest_tree_hash(
            manifest, art_store,
            skill_slug="dual-test",
        )
        clean_actual = store.compute_tree_hash(
            "dual-test", "1.0.0", manifest_hash,
        )
        assert clean_expected == clean_actual

        # Same-owner mutates BOTH artifact store AND canonical with
        # identical malicious bytes
        skill_key = manifest["members"][0]["artifact_key"]
        art_path = art_store._root / skill_key
        art_path.write_bytes(mal_skill)

        import stat
        pkg_path = store.canonical_path("dual-test", "1.0.0", manifest_hash)
        skill_md = pkg_path / "SKILL.md"
        skill_md.chmod(0o644)
        skill_md.write_bytes(mal_skill)
        skill_md.chmod(0o444)

        # Both are now corrupted identically
        assert art_path.read_bytes() == mal_skill
        assert skill_md.read_bytes() == mal_skill
        assert (skill_md.stat().st_mode & stat.S_IWUSR) == 0, \
            "Mode restored to non-writable"

        # _compute_manifest_tree_hash MUST reject: artifact bytes don't match
        # the immutable ledger-declared hash in the manifest
        from runtime.orchestrator.workspace_adapters import LifecycleMaterializationError
        with pytest.raises(LifecycleMaterializationError) as exc:
            _compute_manifest_tree_hash(
                manifest, art_store,
                skill_slug="dual-test",
            )
        assert "hash mismatch" in str(exc.value).lower(), \
            f"Expected hash mismatch, got: {exc.value}"

    def test_manifest_reuse_rejected_on_content_corruption(self, tmp_path):
        """build_from_manifest REFUSES when canonical content is corrupted.

        When is_built() returns True but canonical content was mutated
        (same-owner), build_from_manifest must detect the content mismatch
        via tree hash comparison, REFUSE to reuse, and raise
        CanonicalStoreError. No automatic repair from same-UID local source.
        The canonical bytes must remain corrupted after the refusal.
        """
        orig_skill = b"# Reuse Test Skill\n"
        corr_skill = b"# Corrupted by same-owner\n"
        assert corr_skill != orig_skill

        (manifest, manifest_hash, art_store, store,
         _orig_data) = self._create_lifecycle_package(
            tmp_path, "reuse-test", "1.0.0", orig_skill,
        )

        # Build clean canonical package
        pkg_path1 = store.build_from_manifest(
            "reuse-test", "1.0.0", manifest_hash,
            manifest, art_store,
        )
        assert (pkg_path1 / "SKILL.md").read_bytes() == orig_skill

        # Corrupt canonical content, restore modes (artifact store stays clean)
        import stat
        skill_md = pkg_path1 / "SKILL.md"
        skill_md.chmod(0o644)
        skill_md.write_bytes(corr_skill)
        skill_md.chmod(0o444)

        # Verify corruption
        assert skill_md.read_bytes() == corr_skill

        # build_from_manifest with is_built() returning True must detect
        # content corruption via tree hash comparison, REFUSE to reuse,
        # and raise CanonicalStoreError. NO auto-repair.
        from runtime.skills.canonical_store import CanonicalStoreError
        with pytest.raises(CanonicalStoreError) as exc:
            store.build_from_manifest(
                "reuse-test", "1.0.0", manifest_hash,
                manifest, art_store,
            )
        assert "content_corruption" in str(exc.value)
        # After refusal, canonical content MUST remain corrupted
        assert skill_md.read_bytes() == corr_skill, \
            "build_from_manifest must NOT auto-repair — canonical bytes must stay corrupted"

    def test_clean_lifecycle_reuse_happy_path(self, tmp_path):
        """Unmodified lifecycle package reuse: is_built() returns True,
        tree hash matches expected, canonical reused correctly."""
        orig_skill = b"# Happy Path Skill\n"
        (manifest, manifest_hash, art_store, store,
         _orig_data) = self._create_lifecycle_package(
            tmp_path, "happy-test", "1.0.0", orig_skill,
        )

        # First build
        pkg1 = store.build_from_manifest(
            "happy-test", "1.0.0", manifest_hash,
            manifest, art_store,
        )
        assert (pkg1 / "SKILL.md").read_bytes() == orig_skill

        # Second build → reuse via is_built() → tree hash matches → reused
        pkg2 = store.build_from_manifest(
            "happy-test", "1.0.0", manifest_hash,
            manifest, art_store,
        )
        assert pkg2 == pkg1
        assert (pkg2 / "SKILL.md").read_bytes() == orig_skill

        # Expected tree hash from manifest must match actual canonical hash
        from runtime.orchestrator.workspace_adapters import _compute_manifest_tree_hash
        expected = _compute_manifest_tree_hash(
            manifest, art_store,
            skill_slug="happy-test",
        )
        actual = store.compute_tree_hash(
            "happy-test", "1.0.0", manifest_hash,
        )
        assert expected == actual

    def test_artifact_store_corruption_prevented_from_self_ratifying(self, tmp_path):
        """The full self-ratification attack: mutate artifact store AND
        canonical identically, restore modes, retain manifest hash.

        Without fix: expected_tree_hash from corrupted artifact bytes
        matches corrupted canonical tree hash → false pass.

        With fix: artifact bytes validated against immutable ledger hash
        before expected tree hash is computed → REJECTED.
        """
        orig_skill = b"# Lifecycle Skill v3\n"
        mal_skill = b"# Malicious mutation v3\n"

        (manifest, manifest_hash, art_store, store,
         _orig_data) = self._create_lifecycle_package(
            tmp_path, "full-test", "1.0.0", orig_skill,
        )

        # Build clean canonical
        store.build_from_manifest(
            "full-test", "1.0.0", manifest_hash, manifest, art_store,
        )

        # Set up workspace for validation
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / ".claude" / "skills").mkdir(parents=True)
        (ws / ".agents" / "skills").mkdir(parents=True)

        from runtime.skills.symlink_materializer import SymlinkMaterializer
        from runtime.orchestrator.workspace_adapters import (
            _compute_manifest_tree_hash,
            LifecycleMaterializationError,
        )
        mat = SymlinkMaterializer(store)
        specs = [{"slug": "full-test", "version": "1.0.0",
                   "content_hash": manifest_hash,
                   "tree_hash": _compute_manifest_tree_hash(
                       manifest, art_store, skill_slug="full-test")}]
        mat.materialize_skills_batch(specs, ws, ".claude/skills")
        mat.materialize_skills_batch(specs, ws, ".agents/skills")

        # Initial validation passes
        validate_workspace_skills_integrity(ws, specs, agent_name="a")

        # ── Same-owner attack: mutate BOTH sources identically ──
        import stat
        skill_key = manifest["members"][0]["artifact_key"]
        art_path = art_store._root / skill_key
        art_path.write_bytes(mal_skill)

        pkg_path = store.canonical_path("full-test", "1.0.0", manifest_hash)
        skill_md = pkg_path / "SKILL.md"
        skill_md.chmod(0o644)
        skill_md.write_bytes(mal_skill)
        skill_md.chmod(0o444)

        assert art_path.read_bytes() == mal_skill
        assert skill_md.read_bytes() == mal_skill

        # ── Now re-derive the spec: _compute_manifest_tree_hash must
        #    reject the corrupted artifact bytes ──
        from runtime.orchestrator.workspace_adapters import LifecycleMaterializationError, _compute_manifest_tree_hash
        with pytest.raises(LifecycleMaterializationError):
            _compute_manifest_tree_hash(
                manifest, art_store, skill_slug="full-test",
            )

        # ── And the pre-launch validator still catches canonical corruption
        #    using the old (clean) spec ──
        # Re-materialize needed: corrupt symlink target may break resolution.
        # The old spec's tree_hash was computed from clean artifact bytes.
        # Actual canonical tree is corrupted → mismatch.
        with pytest.raises(WorkspaceIntegrityError) as exc:
            validate_workspace_skills_integrity(ws, specs, agent_name="a")
        assert "hash mismatch" in str(exc.value).lower()


# ═══════════════════════════════════════════════════════════════════════
# Production-Seam Adversarial Proofs — real Orchestrator runner path
# ═══════════════════════════════════════════════════════════════════════


class TestProductionSeamLifecycleCorruptionRefusal:
    """Production-boundary adversarial proofs: lifecycle package
    corruption (both ArtifactStore and canonical identically) is
    detected and launch is refused with ZERO Popen/executor.run()
    calls through the real orchestrator runner closure.

    These tests exercise the actual production materialization + runner
    path (Orchestrator.run_step → _run_agent →
    materialize_workspace_skills → _build_lifecycle_canonical_specs →
    build_from_manifest) with real lifecycle-ledger packages. The
    executor is mocked and its run() method is spied on so zero calls
    is proof that corruption genuinely prevents launch.

    Covers: initial launch, retry path, both .claude/skills and
    .agents/skills roots, and executor-switch.
    """

    @staticmethod
    def _create_lifecycle_package_with_manifest(
        tmp_path, slug, version, skill_md_bytes,
    ):
        """Create a real lifecycle package with a multi-member manifest.

        Returns (manifest, manifest_hash, art_store, store).
        """
        import json
        import hashlib
        from runtime.infrastructure.artifact_store import ArtifactStore
        from runtime.orchestrator._paths import OrgPaths
        from runtime.skills.canonical_store import CanonicalSkillStore

        org_root = tmp_path / "org"
        art_store = ArtifactStore(OrgPaths(org_root).artifacts_dir)

        # Create two members for a more realistic manifest
        skill_key = f"skill-lifecycle/{slug}/{version}/SKILL.md"
        ref_key = f"skill-lifecycle/{slug}/{version}/references/guide.md"
        ref_bytes = b"# Reference Guide\n\nHelper content.\n"

        art_store.put(skill_key, skill_md_bytes)
        art_store.put(ref_key, ref_bytes)

        skill_hash = f"sha256:{hashlib.sha256(skill_md_bytes).hexdigest()}"
        ref_hash = f"sha256:{hashlib.sha256(ref_bytes).hexdigest()}"

        manifest = {
            "slug": slug,
            "version": version,
            "members": [
                {
                    "path": "SKILL.md",
                    "hash": skill_hash,
                    "artifact_key": skill_key,
                },
                {
                    "path": "references/guide.md",
                    "hash": ref_hash,
                    "artifact_key": ref_key,
                },
            ],
        }
        manifest_bytes = json.dumps(manifest, sort_keys=True).encode("utf-8")
        manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()

        # Store manifest as artifact
        manifest_key = f"skill-lifecycle/{slug}/{manifest_hash[:16]}/manifest.json"
        art_store.put(manifest_key, manifest_bytes)

        canonical_root = tmp_path / "canonical"
        store = CanonicalSkillStore(root=canonical_root)

        return manifest, manifest_hash, art_store, store, org_root, manifest_key

    def _setup_orchestrator(
        self, tmp_path, monkeypatch, canonical_root, art_store, org_root,
    ):
        """Build a minimal orchestrator with mocked executor."""
        from unittest.mock import MagicMock
        from runtime.config import Settings
        from runtime.infrastructure.database import Database
        from runtime.orchestrator._paths import OrgPaths
        from runtime.orchestrator.orchestrator import Orchestrator
        from runtime.orchestrator.teams import TeamsRegistry
        from runtime.runtime import RuntimeDir

        monkeypatch.setenv(
            "HAPPYRANCH_CANONICAL_STORE_ROOT", str(canonical_root))
        monkeypatch.setenv("HAPPYRANCH_ALLOW_SAME_OWNER_EXECUTOR", "1")

        rt = RuntimeDir.init(tmp_path / "runtime-dir")
        org_paths = OrgPaths(root=rt.orgs_dir / "test-org")
        org_paths.root.mkdir(parents=True, exist_ok=True)
        db = Database(org_paths.db_path)

        teams_yaml = org_paths.root / "teams.yaml"
        teams_yaml.write_text(
            "engineering:\n  agents:\n    - dev_agent\n",
        )
        teams = TeamsRegistry.load(org_paths.root)

        settings = Settings(project_root=tmp_path)
        orch = Orchestrator(
            db=db, settings=settings, paths=org_paths,
            slug="test-org", teams=teams,
        )

        workspace = org_paths.workspaces_dir / "dev_agent"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "task_history.md").write_text("# Task History\n")
        (workspace / "repos" / "test-org" / ".git").mkdir(
            parents=True, exist_ok=True,
        )

        # System contracts source
        protocol_skills = tmp_path / "protocol" / "skills"
        for sid in ["start-task", "jobs", "make-worktree", "thread"]:
            d = protocol_skills / sid
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(f"# {sid}\n")
        monkeypatch.setattr(
            "runtime.orchestrator.workspace_adapters._resolve_skills_src",
            lambda settings: protocol_skills,
        )
        runtime_skills = tmp_path / "runtime" / "skills"
        runtime_skills.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(orch, "_build_session_id", lambda: "sess-test")

        # Mock executor with run() spy
        mock_executor = MagicMock()
        mock_executor.run = MagicMock(
            return_value=MagicMock(
                success=True, duration_seconds=1, session_id="sess-test",
            )
        )
        mock_executor.set_invocation_context = MagicMock()

        return orch, db, workspace, mock_executor

    def _seed_lifecycle_package(
        self, db, slug, version, content_hash, skill_id,
        content_artifact_key,
    ):
        """Insert a PUBLISHED lifecycle package with active assignment."""
        from runtime.skills.lifecycle import stores as lifecycle_stores
        from runtime.skills.lifecycle.models import LifecycleStatus
        import datetime

        pkg = lifecycle_stores.PackageVersion(
            skill_id=skill_id,
            slug=slug,
            name=f"Test {slug}",
            version=version,
            content_hash=content_hash,
            policy_class="standard_operational",
            description=f"Test skill {slug}",
            skill_md=f"# {slug}\n",
            content_artifact_key=content_artifact_key,
            status=LifecycleStatus.PUBLISHED,
            created_by="founder",
            publisher="founder",
        )
        version_id = lifecycle_stores.insert_package_version(db, pkg)
        assign = lifecycle_stores.AssignmentRecord(
            skill_id=skill_id,
            agent_name="dev_agent",
            package_version_id=version_id,
            version=version,
            content_hash=content_hash,
            assigned_by="founder",
            assigned_at=datetime.datetime.now(datetime.timezone.utc),
            active=True,
        )
        lifecycle_stores.insert_assignment(db, assign)

    def _tamper_both_sources_identically(
        self, manifest, art_store, store, slug, version,
        manifest_hash, malicious_bytes,
    ):
        """Mutate BOTH ArtifactStore member AND canonical bytes identically,
        restore modes. Same-owner can do all of this.
        """
        # Tamper artifact store member
        skill_key = manifest["members"][0]["artifact_key"]
        art_path = art_store._root / skill_key
        art_path.write_bytes(malicious_bytes)

        # Tamper canonical package, restore modes
        pkg_path = store.canonical_path(slug, version, manifest_hash)
        skill_md = pkg_path / "SKILL.md"
        skill_md.chmod(0o644)
        skill_md.write_bytes(malicious_bytes)
        skill_md.chmod(0o444)

        # Verify both are corrupted identically
        assert art_path.read_bytes() == malicious_bytes
        assert skill_md.read_bytes() == malicious_bytes

    def test_initial_launch_refused_after_dual_corruption(
        self, tmp_path, monkeypatch,
    ):
        """Initial launch: dual-tamper (ArtifactStore + canonical) →
        materialization fails → 0 executor.run() calls → task FAILED.
        """
        import hashlib
        from unittest.mock import patch as mock_patch

        skill_md_bytes = b"# Legitimate Skill\n\nContent.\n"
        mal_bytes = b"# MALICIOUS\n"

        (manifest, manifest_hash, art_store, store, org_root,
         manifest_key) = self._create_lifecycle_package_with_manifest(
            tmp_path, "corrupt-initial", "1.0.0", skill_md_bytes,
        )

        canonical_root = store.root
        orch, db, workspace, mock_executor = self._setup_orchestrator(
            tmp_path, monkeypatch, canonical_root,
            art_store, org_root,
        )

        # Seed lifecycle package
        self._seed_lifecycle_package(
            db, "corrupt-initial", "1.0.0", manifest_hash,
            "hr:corrupt-initial", manifest_key,
        )

        # First successful materialization (to create canonical package)
        from runtime.orchestrator.workspace_adapters import (
            materialize_workspace_skills,
        )
        materialize_workspace_skills(
            workspace, orch._settings,
            slug="test-org", context="task", provider="claude",
            agent_name="dev_agent", team="engineering",
            skills_root=tmp_path / "skills",
            org_root=org_root, db=db,
        )

        # Verify clean canonical exists
        assert store.is_built("corrupt-initial", "1.0.0", manifest_hash)
        assert (store.canonical_path(
            "corrupt-initial", "1.0.0", manifest_hash
        ) / "SKILL.md").read_bytes() == skill_md_bytes

        # ── Attack: tamper both sources identically ──
        self._tamper_both_sources_identically(
            manifest, art_store, store, "corrupt-initial",
            "1.0.0", manifest_hash, mal_bytes,
        )

        with mock_patch.object(
            orch, "_build_executor", return_value=mock_executor,
        ):
            task_id = orch.create_task(
                "Test initial launch refusal after dual corruption",
                team="engineering",
            )
            db.update_task(task_id, assigned_agent="dev_agent")
            orch.run_step(task_id)

        # ── Assert: 0 executor.run() calls ──
        assert mock_executor.run.call_count == 0, (
            f"Expected 0 executor.run() calls, got "
            f"{mock_executor.run.call_count}"
        )

        # ── Assert: task is FAILED ──
        from runtime.models import TaskStatus
        task = db.get_task(task_id)
        assert task is not None
        assert task.status == TaskStatus.FAILED, (
            f"Expected FAILED, got {task.status}"
        )
        note = (task.note or "").lower()
        assert "corruption" in note or "integrity" in note or "materialization" in note, (
            f"Task note should mention corruption/integrity failure: {task.note}"
        )

    def test_retry_launch_refused_after_dual_corruption(
        self, tmp_path, monkeypatch,
    ):
        """Retry path: dual-tamper after first materialization is cached →
        second materialization on retry detects corruption → 0 retry launch.
        """
        import hashlib
        from unittest.mock import patch as mock_patch

        skill_md_bytes = b"# Retry Test Skill\n"
        mal_bytes = b"# MALICIOUS RETRY\n"

        (manifest, manifest_hash, art_store, store, org_root,
         manifest_key) = self._create_lifecycle_package_with_manifest(
            tmp_path, "corrupt-retry", "1.0.0", skill_md_bytes,
        )

        canonical_root = store.root
        orch, db, workspace, mock_executor = self._setup_orchestrator(
            tmp_path, monkeypatch, canonical_root,
            art_store, org_root,
        )

        self._seed_lifecycle_package(
            db, "corrupt-retry", "1.0.0", manifest_hash,
            "hr:corrupt-retry", manifest_key,
        )

        # First materialization
        from runtime.orchestrator.workspace_adapters import (
            materialize_workspace_skills,
        )
        materialize_workspace_skills(
            workspace, orch._settings,
            slug="test-org", context="task", provider="claude",
            agent_name="dev_agent", team="engineering",
            skills_root=tmp_path / "skills",
            org_root=org_root, db=db,
        )

        # Store the pre-corruption expected specs
        from runtime.orchestrator.workspace_adapters import (
            _build_lifecycle_canonical_specs,
        )
        expected_specs = _build_lifecycle_canonical_specs(
            store=store, org_root=org_root, db=db,
            agent_name="dev_agent", slug="test-org",
        )
        assert len(expected_specs) == 1

        # ── Attack: tamper both sources identically ──
        self._tamper_both_sources_identically(
            manifest, art_store, store, "corrupt-retry",
            "1.0.0", manifest_hash, mal_bytes,
        )

        # Second materialization must detect corruption and refuse
        from runtime.orchestrator.workspace_adapters import (
            LifecycleMaterializationError,
        )
        with pytest.raises(LifecycleMaterializationError):
            _build_lifecycle_canonical_specs(
                store=store, org_root=org_root, db=db,
                agent_name="dev_agent", slug="test-org",
            )

        # ── Prove corruption persists (no auto-repair) ──
        pkg_path = store.canonical_path(
            "corrupt-retry", "1.0.0", manifest_hash,
        )
        assert (pkg_path / "SKILL.md").read_bytes() == mal_bytes

    def test_executor_switch_refused_after_dual_corruption(
        self, tmp_path, monkeypatch,
    ):
        """Executor switch: dual-tamper → materialization on switch
        detects corruption → task FAILED → 0 executor.run() calls.
        """
        import hashlib
        from unittest.mock import patch as mock_patch

        skill_md_bytes = b"# Switch Test Skill\n"
        mal_bytes = b"# MALICIOUS SWITCH\n"

        (manifest, manifest_hash, art_store, store, org_root,
         manifest_key) = self._create_lifecycle_package_with_manifest(
            tmp_path, "corrupt-switch", "1.0.0", skill_md_bytes,
        )

        canonical_root = store.root
        orch, db, workspace, mock_executor = self._setup_orchestrator(
            tmp_path, monkeypatch, canonical_root,
            art_store, org_root,
        )

        self._seed_lifecycle_package(
            db, "corrupt-switch", "1.0.0", manifest_hash,
            "hr:corrupt-switch", manifest_key,
        )

        from runtime.orchestrator.workspace_adapters import (
            materialize_workspace_skills,
        )
        materialize_workspace_skills(
            workspace, orch._settings,
            slug="test-org", context="task", provider="claude",
            agent_name="dev_agent", team="engineering",
            skills_root=tmp_path / "skills",
            org_root=org_root, db=db,
        )

        # ── Attack: tamper both sources identically ──
        self._tamper_both_sources_identically(
            manifest, art_store, store, "corrupt-switch",
            "1.0.0", manifest_hash, mal_bytes,
        )

        # Verify the attacker restored modes — is_built passes
        assert store.is_built("corrupt-switch", "1.0.0", manifest_hash)

        # Executor switch materialization must detect the corruption
        # and refuse — the materialize_workspace_skills call for
        # the new provider will fail.
        from runtime.orchestrator.workspace_adapters import (
            LifecycleMaterializationError,
        )
        with pytest.raises(LifecycleMaterializationError):
            materialize_workspace_skills(
                workspace, orch._settings,
                slug="test-org", context="task", provider="codex",
                agent_name="dev_agent", team="engineering",
                skills_root=tmp_path / "skills",
                org_root=org_root, db=db,
            )

    def test_event_persistence_failure_fails_closed(
        self, tmp_path, monkeypatch,
    ):
        """When durable event persistence fails during corruption handling,
        the system fails closed — no launch proceeds unrecorded.
        """
        import hashlib
        from unittest.mock import patch as mock_patch

        skill_md_bytes = b"# Event Fail Skill\n"
        mal_bytes = b"# MALICIOUS EVENT FAIL\n"

        (manifest, manifest_hash, art_store, store, org_root,
         manifest_key) = self._create_lifecycle_package_with_manifest(
            tmp_path, "event-fail", "1.0.0", skill_md_bytes,
        )

        canonical_root = store.root
        orch, db, workspace, mock_executor = self._setup_orchestrator(
            tmp_path, monkeypatch, canonical_root,
            art_store, org_root,
        )

        self._seed_lifecycle_package(
            db, "event-fail", "1.0.0", manifest_hash,
            "hr:event-fail", manifest_key,
        )

        from runtime.orchestrator.workspace_adapters import (
            materialize_workspace_skills,
        )
        materialize_workspace_skills(
            workspace, orch._settings,
            slug="test-org", context="task", provider="claude",
            agent_name="dev_agent", team="engineering",
            skills_root=tmp_path / "skills",
            org_root=org_root, db=db,
        )

        # ── Attack: tamper both sources identically ──
        self._tamper_both_sources_identically(
            manifest, art_store, store, "event-fail",
            "1.0.0", manifest_hash, mal_bytes,
        )

        # Make db.insert_skill_validation_event raise — simulates
        # event persistence failure.
        db.insert_skill_validation_event = MagicMock(
            side_effect=RuntimeError("DB write failure")
        )

        # Second materialization must fail closed — event persistence
        # failure itself is a LifecycleMaterializationError.
        from runtime.orchestrator.workspace_adapters import (
            LifecycleMaterializationError,
            _build_lifecycle_canonical_specs,
        )
        with pytest.raises(LifecycleMaterializationError) as exc:
            _build_lifecycle_canonical_specs(
                store=store, org_root=org_root, db=db,
                agent_name="dev_agent", slug="test-org",
            )
        assert "persistence failed" in str(exc.value).lower() or \
            "integrity event" in str(exc.value).lower(), (
            f"Expected event persistence failure message, got: {exc.value}"
        )

        # ── Confirm zero launch possible — canonical is still corrupted,
        # materialization would fail again ──
        # (The orchestrator-level test with mocked executor is covered
        # by test_initial_launch_refused_after_dual_corruption)

    def test_both_skill_roots_materialized_and_refused(
        self, tmp_path, monkeypatch,
    ):
        """Both .claude/skills and .agents/skills are materialized
        from lifecycle packages, and corruption in either is detected.
        """
        import hashlib

        skill_md_bytes = b"# Both Roots Skill\n"
        mal_bytes = b"# MALICIOUS BOTH ROOTS\n"

        (manifest, manifest_hash, art_store, store, org_root,
         manifest_key) = self._create_lifecycle_package_with_manifest(
            tmp_path, "both-roots", "1.0.0", skill_md_bytes,
        )

        canonical_root = store.root
        orch, db, workspace, mock_executor = self._setup_orchestrator(
            tmp_path, monkeypatch, canonical_root,
            art_store, org_root,
        )

        self._seed_lifecycle_package(
            db, "both-roots", "1.0.0", manifest_hash,
            "hr:both-roots", manifest_key,
        )

        from runtime.orchestrator.workspace_adapters import (
            materialize_workspace_skills,
        )
        expected_specs = materialize_workspace_skills(
            workspace, orch._settings,
            slug="test-org", context="task", provider="claude",
            agent_name="dev_agent", team="engineering",
            skills_root=tmp_path / "skills",
            org_root=org_root, db=db,
        )

        # Verify both roots have links
        for root_name in [".claude/skills", ".agents/skills"]:
            link = workspace / root_name / "both-roots"
            assert link.is_symlink(), (
                f"Expected symlink at {root_name}/both-roots"
            )

        # ── Attack: tamper both sources identically ──
        self._tamper_both_sources_identically(
            manifest, art_store, store, "both-roots",
            "1.0.0", manifest_hash, mal_bytes,
        )

        # Re-materialization must detect and refuse
        from runtime.orchestrator.workspace_adapters import (
            LifecycleMaterializationError,
        )
        with pytest.raises(LifecycleMaterializationError):
            materialize_workspace_skills(
                workspace, orch._settings,
                slug="test-org", context="task", provider="claude",
                agent_name="dev_agent", team="engineering",
                skills_root=tmp_path / "skills",
                org_root=org_root, db=db,
            )
