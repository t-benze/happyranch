"""TASK-4195: Adversarial pre-launch integrity validation tests.

Proves:
- Same-owner mutation to canonical bytes is possible
- Next launch integrity check detects mismatch BEFORE executor
- Durable audit event is written on mismatch
- Audit-write failure also blocks launch
- No auto-repair from same-UID local sources
- Both .claude/skills and .agents/skills are validated
- Malicious/broken/unexpected links are detected
"""

import hashlib
import os
from unittest.mock import MagicMock

import pytest

from runtime.infrastructure.database import Database
from runtime.orchestrator.workspace_adapters import (
    validate_workspace_skills_integrity,
    WorkspaceIntegrityError,
)
from runtime.skills.canonical_store import CanonicalSkillStore
from runtime.skills.symlink_materializer import SymlinkMaterializer


class TestPreLaunchIntegrityValidation:
    """Adversarial: same-owner process mutates canonical targets;
    next launch detects mismatch BEFORE executor; no auto-repair."""

    def test_same_owner_mutation_detected(self, tmp_path, monkeypatch):
        """Same-owner corrupts canonical bytes; next validation detects it."""
        canonical_root = tmp_path / "canonical"
        monkeypatch.setenv("HAPPYRANCH_CANONICAL_STORE_ROOT", str(canonical_root))
        store = CanonicalSkillStore(root=canonical_root)

        src = tmp_path / "src"
        src.mkdir()
        body = "# Skill\n"
        (src / "SKILL.md").write_text(body)
        ch = hashlib.sha256(body.encode()).hexdigest()
        store.build_from_source("sk", "1.0.0", ch, src)

        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / ".claude" / "skills").mkdir(parents=True)
        (ws / ".agents" / "skills").mkdir(parents=True)

        mat = SymlinkMaterializer(store)
        specs = [{"slug": "sk", "version": "1.0.0", "content_hash": ch}]
        mat.materialize_skills_batch(specs, ws, ".claude/skills")
        mat.materialize_skills_batch(specs, ws, ".agents/skills")

        # Pass initially
        validate_workspace_skills_integrity(ws, specs, agent_name="a")

        # Same-owner mutates canonical target (chmod+writable first
        # since canonical store applies readonly hardening after build, but
        # a same-owner process can chmod + write)
        target = store.canonical_path("sk", "1.0.0", ch)
        skill_md = target / "SKILL.md"
        skill_md.chmod(0o644)  # same-owner can chmod
        orig = skill_md.read_bytes()
        corr = bytearray(orig)
        corr[0] ^= 0xFF
        skill_md.write_bytes(bytes(corr))
        assert skill_md.read_bytes() != orig, "Mutation failed"

        # Next validation MUST detect mismatch
        with pytest.raises(WorkspaceIntegrityError) as e:
            validate_workspace_skills_integrity(ws, specs, agent_name="a")
        assert e.value.code == "integrity_mismatch"
        assert any(
            "Canonical package integrity failure" in f for f in e.value.findings
        )
        assert "set-executor" in (e.value.recovery_command or "")

    def test_audit_event_emitted_on_mismatch(self, tmp_path, monkeypatch):
        """Mismatch writes durable skill_validation_events row."""
        canonical_root = tmp_path / "canonical"
        monkeypatch.setenv("HAPPYRANCH_CANONICAL_STORE_ROOT", str(canonical_root))
        store = CanonicalSkillStore(root=canonical_root)

        src = tmp_path / "src"
        src.mkdir()
        (src / "SKILL.md").write_text("# Audit\n")
        ch = hashlib.sha256(b"# Audit\n").hexdigest()
        store.build_from_source("aud", "1.0.0", ch, src)

        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / ".claude" / "skills").mkdir(parents=True)
        (ws / ".agents" / "skills").mkdir(parents=True)

        mat = SymlinkMaterializer(store)
        specs = [{"slug": "aud", "version": "1.0.0", "content_hash": ch}]
        mat.materialize_skills_batch(specs, ws, ".claude/skills")
        mat.materialize_skills_batch(specs, ws, ".agents/skills")

        db = Database(tmp_path / "test.db")
        target = store.canonical_path("aud", "1.0.0", ch)
        skill_md = target / "SKILL.md"
        skill_md.chmod(0o644)  # same-owner can chmod
        skill_md.write_bytes(b"corrupted!")

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

    def test_audit_write_failure_blocks_launch(self, tmp_path, monkeypatch):
        """Audit persistence failure also refuses launch."""
        canonical_root = tmp_path / "canonical"
        monkeypatch.setenv("HAPPYRANCH_CANONICAL_STORE_ROOT", str(canonical_root))
        store = CanonicalSkillStore(root=canonical_root)

        src = tmp_path / "src"
        src.mkdir()
        (src / "SKILL.md").write_text("# X\n")
        ch = hashlib.sha256(b"# X\n").hexdigest()
        store.build_from_source("x", "1.0.0", ch, src)

        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / ".claude" / "skills").mkdir(parents=True)
        (ws / ".agents" / "skills").mkdir(parents=True)

        mat = SymlinkMaterializer(store)
        specs = [{"slug": "x", "version": "1.0.0", "content_hash": ch}]
        mat.materialize_skills_batch(specs, ws, ".claude/skills")

        target = store.canonical_path("x", "1.0.0", ch)
        skill_md = target / "SKILL.md"
        skill_md.chmod(0o644)  # same-owner can chmod
        skill_md.write_bytes(b"corrupted!")

        mock_db = MagicMock()
        mock_db.insert_skill_validation_event.side_effect = RuntimeError("full")

        with pytest.raises(WorkspaceIntegrityError) as exc:
            validate_workspace_skills_integrity(
                ws, specs, db=mock_db, agent_name="a",
            )
        assert any(
            "audit" in f.lower() or "Audit" in f for f in exc.value.findings
        )

    def test_no_auto_repair(self, tmp_path, monkeypatch):
        """Mismatch is raised; canonical bytes stay corrupted (no auto-repair)."""
        canonical_root = tmp_path / "canonical"
        monkeypatch.setenv("HAPPYRANCH_CANONICAL_STORE_ROOT", str(canonical_root))
        store = CanonicalSkillStore(root=canonical_root)

        src = tmp_path / "src"
        src.mkdir()
        (src / "SKILL.md").write_text("# N\n")
        ch = hashlib.sha256(b"# N\n").hexdigest()
        store.build_from_source("n", "1.0.0", ch, src)

        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / ".claude" / "skills").mkdir(parents=True)
        (ws / ".agents" / "skills").mkdir(parents=True)

        mat = SymlinkMaterializer(store)
        specs = [{"slug": "n", "version": "1.0.0", "content_hash": ch}]
        mat.materialize_skills_batch(specs, ws, ".claude/skills")

        target = store.canonical_path("n", "1.0.0", ch)
        skill_md = target / "SKILL.md"
        assert skill_md.read_bytes() == b"# N\n"
        skill_md.chmod(0o644)  # same-owner can chmod
        skill_md.write_bytes(b"corrupted!")

        with pytest.raises(WorkspaceIntegrityError):
            validate_workspace_skills_integrity(ws, specs, agent_name="a")

        # Still corrupted — no auto-repair from same-UID local sources
        assert (target / "SKILL.md").read_bytes() == b"corrupted!"

    def test_both_roots_validated(self, tmp_path, monkeypatch):
        """Validation checks BOTH .claude/skills and .agents/skills."""
        canonical_root = tmp_path / "canonical"
        monkeypatch.setenv("HAPPYRANCH_CANONICAL_STORE_ROOT", str(canonical_root))
        store = CanonicalSkillStore(root=canonical_root)

        src = tmp_path / "src"
        src.mkdir()
        (src / "SKILL.md").write_text("# B\n")
        ch = hashlib.sha256(b"# B\n").hexdigest()
        store.build_from_source("b", "1.0.0", ch, src)

        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / ".claude" / "skills").mkdir(parents=True)
        (ws / ".agents" / "skills").mkdir(parents=True)

        mat = SymlinkMaterializer(store)
        specs = [{"slug": "b", "version": "1.0.0", "content_hash": ch}]
        # Materialize only .claude, not .agents
        mat.materialize_skills_batch(specs, ws, ".claude/skills")

        # Must fail: .agents/skills link missing
        with pytest.raises(WorkspaceIntegrityError) as exc:
            validate_workspace_skills_integrity(ws, specs, agent_name="a")
        assert any(".agents/skills" in f for f in exc.value.findings)

    def test_unexpected_entries_detected(self, tmp_path, monkeypatch):
        """Unexpected entries in workspace skill dirs are flagged."""
        canonical_root = tmp_path / "canonical"
        monkeypatch.setenv("HAPPYRANCH_CANONICAL_STORE_ROOT", str(canonical_root))
        store = CanonicalSkillStore(root=canonical_root)

        src = tmp_path / "src"
        src.mkdir()
        (src / "SKILL.md").write_text("# C\n")
        ch = hashlib.sha256(b"# C\n").hexdigest()
        store.build_from_source("c", "1.0.0", ch, src)

        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / ".claude" / "skills").mkdir(parents=True)
        (ws / ".agents" / "skills").mkdir(parents=True)

        mat = SymlinkMaterializer(store)
        specs = [{"slug": "c", "version": "1.0.0", "content_hash": ch}]
        mat.materialize_skills_batch(specs, ws, ".claude/skills")
        mat.materialize_skills_batch(specs, ws, ".agents/skills")

        # Plant unexpected symlink
        os.symlink("/etc/hosts", str(ws / ".claude" / "skills" / "surprise"))

        with pytest.raises(WorkspaceIntegrityError) as exc:
            validate_workspace_skills_integrity(ws, specs, agent_name="a")
        assert any(
            "Unexpected" in f and "surprise" in f for f in exc.value.findings
        ), f"Got: {exc.value.findings}"

    def test_malicious_external_link_detected(self, tmp_path, monkeypatch):
        """Wrong/external link targets are detected."""
        canonical_root = tmp_path / "canonical"
        monkeypatch.setenv("HAPPYRANCH_CANONICAL_STORE_ROOT", str(canonical_root))
        store = CanonicalSkillStore(root=canonical_root)

        src = tmp_path / "src"
        src.mkdir()
        (src / "SKILL.md").write_text("# M\n")
        ch = hashlib.sha256(b"# M\n").hexdigest()
        store.build_from_source("m", "1.0.0", ch, src)

        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / ".claude" / "skills").mkdir(parents=True)
        (ws / ".agents" / "skills").mkdir(parents=True)

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
