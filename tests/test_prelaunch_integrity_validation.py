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
        """build_from_manifest reuse is rejected when canonical content
        is corrupted but artifact store remains clean.

        When is_built() returns True but canonical content was mutated
        (same-owner), build_from_manifest must detect the content mismatch
        via tree hash comparison and REBUILD from artifact store, not reuse.
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
        # content corruption via tree hash comparison, reject reuse, and
        # rebuild from clean artifact store
        pkg_path2 = store.build_from_manifest(
            "reuse-test", "1.0.0", manifest_hash,
            manifest, art_store,
        )
        # After rebuild, canonical content should be restored to original
        assert (pkg_path2 / "SKILL.md").read_bytes() == orig_skill, \
            "build_from_manifest must rebuild clean content after detecting corruption"

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
