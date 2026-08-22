"""Tests for the portability capture + verification helpers (THR-187 Slice B).

Covers: SQLite backup (no WAL/SHM), schedule deactivation, B2 custom-skill
artifact cross-check, v2 fingerprint, source-file capture rejecting symlinks,
and SQLite integrity/FK verification.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from runtime.infrastructure.database import Database
from runtime.portability.capture import (
    CaptureError,
    canonical_v2_fingerprint,
    collect_source_files,
    compute_v2_fingerprint,
    deactivate_schedules,
    gather_legacy_skill_evidence,
    validate_b2_match,
    validate_legacy_evidence_match,
    verify_b2_custom_skills,
    verify_sqlite_integrity,
)
from runtime.portability.archive import (
    ArchiveValidationError,
    B2CustomSkillCheck,
    LegacySkillEvidence,
    sha256_bytes,
)
from runtime.portability.roots import resolve_legacy_skill_references


_VALID_SKILL_YAML = (
    "description: QA\n"
    "id: hr:qa-scroll-test\n"
    "name: QA\n"
    "owner: operator\n"
    "policy_class: standard_operational\n"
    "slug: qa-scroll-test\n"
    "source: user_authored\n"
    "status: enabled\n"
    "version: 0.1.0\n"
    "when_to_use: ''\n"
)


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_backup_to_produces_clean_snapshot_without_wal_shm(tmp_path: Path) -> None:
    db = Database(tmp_path / "org" / "happyranch.db")
    db.insert_task  # ensure module-level accessor exists (no-op sanity)
    from runtime.models import TaskRecord, TaskStatus
    db.insert_task(TaskRecord(
        id="T-1", brief="t", team="engineering",
        assigned_agent="dev_agent", status=TaskStatus.COMPLETED,
    ))
    dest = tmp_path / "snap" / "happyranch.db"
    db.backup_to(dest)
    assert dest.is_file()
    # No sidecar files copied to the destination.
    assert not (dest.parent / "happyranch.db-wal").exists()
    assert not (dest.parent / "happyranch.db-shm").exists()
    # Snapshot is a valid, readable DB.
    conn = sqlite3.connect(str(dest))
    row = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()
    conn.close()
    assert row[0] == 1
    db.close()


def test_deactivate_schedules_forces_active_zero(tmp_path: Path) -> None:
    from runtime.infrastructure.schedule_store import ScheduleStore
    from runtime.models import ScheduleKind, ScheduleRecord, ScheduleStatus
    db = Database(tmp_path / "happyranch.db")
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    db.schedules.insert(ScheduleRecord(
        id="SCHEDULE-001", agent_name="dev_agent", kind=ScheduleKind.ONE_SHOT,
        fire_at=now, normalized_brief="b", source_instruction="s",
        status=ScheduleStatus.ARMED, active=1, created_at=now,
    ))
    db.schedules.insert(ScheduleRecord(
        id="SCHEDULE-002", agent_name="dev_agent", kind=ScheduleKind.ONE_SHOT,
        fire_at=now, normalized_brief="b", source_instruction="s",
        status=ScheduleStatus.FIRED, active=0, created_at=now,
    ))
    backup = tmp_path / "snap.db"
    db.backup_to(backup)
    db.close()

    deactivated = deactivate_schedules(backup)
    assert deactivated == 1  # only the active=1 row changed

    conn = sqlite3.connect(str(backup))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, active, status FROM schedules ORDER BY id").fetchall()
    conn.close()
    assert rows[0]["id"] == "SCHEDULE-001"
    assert rows[0]["active"] == 0
    assert rows[0]["status"] == "armed"  # status semantics unchanged
    assert rows[1]["id"] == "SCHEDULE-002"
    assert rows[1]["active"] == 0
    assert rows[1]["status"] == "fired"


def test_verify_sqlite_integrity_passes_clean_db(tmp_path: Path) -> None:
    db = Database(tmp_path / "happyranch.db")
    dest = tmp_path / "snap.db"
    db.backup_to(dest)
    db.close()
    verify_sqlite_integrity(dest)  # must not raise


def test_verify_sqlite_integrity_rejects_corrupt_db(tmp_path: Path) -> None:
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is not a sqlite database at all")
    with pytest.raises(CaptureError, match="integrity_check"):
        verify_sqlite_integrity(bad)


def test_compute_v2_fingerprint_is_stable_and_shape_only(tmp_path: Path) -> None:
    db = Database(tmp_path / "happyranch.db")
    fp1 = compute_v2_fingerprint(db._conn)
    fp2 = compute_v2_fingerprint(db._conn)
    assert fp1 == fp2
    # Shape-only: data rows do not change the fingerprint.
    from runtime.models import TaskRecord, TaskStatus
    db.insert_task(TaskRecord(
        id="T-1", brief="t", team="engineering",
        assigned_agent="dev_agent", status=TaskStatus.COMPLETED,
    ))
    assert compute_v2_fingerprint(db._conn) == fp1
    db.close()


def test_collect_source_files_rejects_symlink(tmp_path: Path) -> None:
    org_root = tmp_path / "org"
    (org_root / "org").mkdir(parents=True)
    _write(org_root / "org" / "teams.yaml", "teams: {}\n")
    # A symlink inside an allow-listed root must fail closed.
    (org_root / "org" / "leak").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(CaptureError, match="symlink"):
        collect_source_files(org_root, ["org"])


def test_collect_source_files_maps_rel_paths(tmp_path: Path) -> None:
    org_root = tmp_path / "org"
    (org_root / "org").mkdir(parents=True)
    _write(org_root / "org" / "teams.yaml", "teams: {}\n")
    _write(org_root / "talks" / "TALK-1.md", "# talk\n")
    payload, counts = collect_source_files(org_root, ["org", "talks"])
    assert "payload/org/teams.yaml" in payload
    assert "payload/talks/TALK-1.md" in payload
    assert counts["org"] == 1
    assert counts["talks"] == 1


def test_b2_custom_skill_cross_check_passes_and_fails(tmp_path: Path) -> None:
    db = Database(tmp_path / "happyranch.db")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(parents=True)
    content = b"skill body"
    content_hash = sha256_bytes(content)
    key = "custom-skills/qa/1.0.0"
    (artifacts / key).parent.mkdir(parents=True)
    (artifacts / key).write_bytes(content)

    db.execute(
        "INSERT INTO custom_skills (id, org_slug, slug, name, description, "
        "policy_class, origin_kind, origin_agent, created_at, created_by, "
        "current_version_id) VALUES ('cs-1','alpha','qa','QA','','standard_operational',"
        "'human',NULL,'2026-01-01T00:00:00Z','founder',NULL)"
    )
    db.execute(
        "INSERT INTO custom_skill_versions (id, skill_id, content_hash, "
        "content_artifact_key, validation_state, created_at, author_kind, "
        "author_identity) VALUES (1,'cs-1',?,?,'valid','2026-01-01T00:00:00Z',"
        "'human','founder')", (content_hash, key),
    )
    db.execute(
        "UPDATE custom_skills SET current_version_id = 1 WHERE id = 'cs-1'"
    )
    db.commit()
    backup = tmp_path / "snap.db"
    db.backup_to(backup)
    db.close()

    checks = verify_b2_custom_skills(backup, artifacts)
    assert len(checks) == 1
    assert checks[0].valid is True
    assert checks[0].slug == "qa"

    # Tamper with the artifact → cross-check fails.
    (artifacts / key).write_bytes(b"tampered")
    checks = verify_b2_custom_skills(backup, artifacts)
    assert checks[0].valid is False
    assert checks[0].reason == "artifact_hash_mismatch"


def test_gather_legacy_skill_evidence(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    pkg = skills / "qa-scroll-test"
    pkg.mkdir(parents=True)
    _write(pkg / "SKILL.md", "# QA\n\nSee [the guide](references/guide.md).\n")
    _write(pkg / "skill.yaml",
           "description: QA\nid: hr:qa-scroll-test\nname: QA\nowner: o\n"
           "policy_class: standard_operational\nslug: qa-scroll-test\n"
           "source: user_authored\nstatus: enabled\nversion: 0.1.0\nwhen_to_use: ''\n")
    _write(pkg / "references" / "guide.md", "# Guide\n")
    evidence = gather_legacy_skill_evidence(skills, ["qa-scroll-test"])
    assert len(evidence) == 1
    e = evidence[0]
    assert e.slug == "qa-scroll-test"
    assert e.validation_result == "valid"
    assert "SKILL.md" in e.member_hashes
    assert "references/guide.md" in e.member_hashes
    assert e.references_resolved == ["references/guide.md"]


def test_gather_legacy_skill_evidence_rejects_symlink(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    pkg = skills / "qa-scroll-test"
    pkg.mkdir(parents=True)
    _write(pkg / "SKILL.md", "# QA\n")
    _write(pkg / "skill.yaml", "slug: qa-scroll-test\n")
    (pkg / "references").symlink_to(tmp_path / "outside")
    with pytest.raises(CaptureError, match="symlink"):
        gather_legacy_skill_evidence(skills, ["qa-scroll-test"])


def test_gather_legacy_skill_evidence_marks_invalid_identity(tmp_path: Path) -> None:
    """A package with a non-conforming identity (wrong source) is gathered but
    carries a non-'valid' validation_result (rather than being silently
    accepted)."""
    skills = tmp_path / "skills"
    pkg = skills / "qa-scroll-test"
    pkg.mkdir(parents=True)
    _write(pkg / "SKILL.md", "# QA\n\nbody\n")
    _write(pkg / "skill.yaml",
           "description: QA\nid: hr:qa-scroll-test\nname: QA\nowner: o\n"
           "policy_class: standard_operational\nslug: qa-scroll-test\n"
           "source: system_contract\nstatus: enabled\nversion: 0.1.0\n"
           "when_to_use: ''\n")  # source is not user_authored
    evidence = gather_legacy_skill_evidence(skills, ["qa-scroll-test"])
    assert len(evidence) == 1
    assert evidence[0].validation_result != "valid"


def test_validate_legacy_evidence_match_rejects_mismatch(tmp_path: Path) -> None:
    """The manifest's declared legacy-skill evidence must match the recomputed
    bytes exactly; a content-hash disagreement is rejected."""
    skills = tmp_path / "skills"
    pkg = skills / "qa-scroll-test"
    pkg.mkdir(parents=True)
    _write(pkg / "SKILL.md", "# QA\n\nbody\n")
    _write(pkg / "skill.yaml",
           "description: QA\nid: hr:qa-scroll-test\nname: QA\nowner: o\n"
           "policy_class: standard_operational\nslug: qa-scroll-test\n"
           "source: user_authored\nstatus: enabled\nversion: 0.1.0\nwhen_to_use: ''\n")
    actual = gather_legacy_skill_evidence(skills, ["qa-scroll-test"])
    claimed = [LegacySkillEvidence(
        slug="qa-scroll-test",
        metadata_hash=actual[0].metadata_hash,
        content_hash="0" * 64,  # wrong content hash
        member_hashes=actual[0].member_hashes,
        validation_result="valid",
        references_resolved=actual[0].references_resolved,
    )]
    with pytest.raises(ArchiveValidationError, match="content_hash mismatch"):
        validate_legacy_evidence_match(claimed, actual)


def test_validate_b2_match_rejects_mismatch() -> None:
    """The manifest's declared B2 evidence must match the recomputed checks; a
    differing content hash is rejected."""
    claimed = [B2CustomSkillCheck(
        skill_id="cs-1", slug="qa", version_id=1,
        content_artifact_key="k", content_hash="aaaa", valid=True, reason=None,
    )]
    actual = [B2CustomSkillCheck(
        skill_id="cs-1", slug="qa", version_id=1,
        content_artifact_key="k", content_hash="bbbb", valid=True, reason=None,
    )]
    with pytest.raises(ArchiveValidationError, match="content hash mismatch"):
        validate_b2_match(claimed, actual)


def test_canonical_v2_fingerprint_matches_fresh_database(tmp_path: Path) -> None:
    """The independent canonical fingerprint equals the schema shape of a
    freshly initialized Database (the runtime's own current-v2 bootstrap)."""
    db = Database(tmp_path / "happyranch.db")
    canonical = canonical_v2_fingerprint()
    assert canonical == compute_v2_fingerprint(db._conn)
    assert len(canonical) == 64
    db.close()


def test_canonical_v2_fingerprint_differs_from_old_shape(tmp_path: Path) -> None:
    """A v0-shaped DB (agent_enrollments table) produces a different schema
    fingerprint than the current-v2 canonical value."""
    old = tmp_path / "old.db"
    conn = sqlite3.connect(str(old))
    conn.execute("CREATE TABLE agent_enrollments (id TEXT PRIMARY KEY)")
    conn.commit()
    old_fp = compute_v2_fingerprint(conn)
    conn.close()
    assert old_fp != canonical_v2_fingerprint()


# ── Legacy-skill local reference parsing/resolution (repair) ───────────────


@pytest.mark.parametrize(
    "md_body, expect_reason",
    [
        ("# QA\n\n[leak](file:references/guide.md)\n", "file: URI"),
        ("# QA\n\n[leak](/etc/passwd)\n", "absolute path"),
        ("# QA\n\n[leak](../other/secret.md)\n", "parent-traversal"),
        ("# QA\n\n[leak](../../org/teams.yaml)\n", "parent-traversal"),
        ("# QA\n\n[leak](references/missing.md)\n", "missing/unhashed"),
        ("# QA\n\n[leak](..\\other\\secret.md)\n", "backslash"),
    ],
    ids=["file-uri", "absolute", "dotdot", "cross-package", "missing", "backslash"],
)
def test_gather_legacy_skill_evidence_rejects_unsafe_reference(
    tmp_path: Path, md_body: str, expect_reason: str,
) -> None:
    """A local reference that escapes the package (file:/absolute/../backslash)
    or targets a missing/unhashed file is refused — the package is gathered but
    marked non-valid (never carried as valid quarantine)."""
    skills = tmp_path / "skills"
    pkg = skills / "qa-scroll-test"
    pkg.mkdir(parents=True)
    _write(pkg / "SKILL.md", md_body)
    _write(pkg / "skill.yaml", _VALID_SKILL_YAML)
    _write(pkg / "references" / "guide.md", "# Guide\n")
    evidence = gather_legacy_skill_evidence(skills, ["qa-scroll-test"])
    assert len(evidence) == 1
    assert evidence[0].validation_result != "valid"
    assert expect_reason in evidence[0].validation_result


def test_gather_legacy_skill_evidence_remote_and_fragment_inert(tmp_path: Path) -> None:
    """Remote URLs (http/https/mailto) and fragment-only anchors are inert —
    they are not local references and do not reject a valid package."""
    skills = tmp_path / "skills"
    pkg = skills / "qa-scroll-test"
    pkg.mkdir(parents=True)
    _write(
        pkg / "SKILL.md",
        "# QA\n\nSee [remote](https://example.com/x), [mail](mailto:a@b.c), "
        "[anchor](#sec), and [guide](references/guide.md).\n",
    )
    _write(pkg / "skill.yaml", _VALID_SKILL_YAML)
    _write(pkg / "references" / "guide.md", "# Guide\n")
    evidence = gather_legacy_skill_evidence(skills, ["qa-scroll-test"])
    assert evidence[0].validation_result == "valid"
    assert evidence[0].references_resolved == ["references/guide.md"]


def test_gather_legacy_skill_evidence_rejects_yaml_file_uri(tmp_path: Path) -> None:
    """A file: URI embedded in a skill.yaml string value is a local reference
    and is refused (validation_result != valid)."""
    skills = tmp_path / "skills"
    pkg = skills / "qa-scroll-test"
    pkg.mkdir(parents=True)
    _write(pkg / "SKILL.md", "# QA\n\nbody\n")
    _write(pkg / "skill.yaml", _VALID_SKILL_YAML + "extra_ref: file:///etc/passwd\n")
    evidence = gather_legacy_skill_evidence(skills, ["qa-scroll-test"])
    assert evidence[0].validation_result != "valid"
    assert "file: URI" in evidence[0].validation_result


def test_resolve_legacy_skill_references_direct(tmp_path: Path) -> None:
    """The resolver returns only normalized same-package, listed references and
    raises on escape/missing targets."""
    pkg = tmp_path / "qa-scroll-test"
    pkg.mkdir(parents=True)
    _write(pkg / "SKILL.md", "# QA\n\n[a](references/a.md) [b](assets/b.svg)\n")
    _write(pkg / "skill.yaml", _VALID_SKILL_YAML)
    _write(pkg / "references" / "a.md", "# A\n")
    _write(pkg / "assets" / "b.svg", "<svg/>\n")
    members = {"SKILL.md", "skill.yaml", "references/a.md", "assets/b.svg"}
    assert resolve_legacy_skill_references(pkg, members) == [
        "assets/b.svg", "references/a.md",
    ]
    # missing target
    with pytest.raises(ValueError, match="missing/unhashed"):
        resolve_legacy_skill_references(
            pkg, {"SKILL.md", "skill.yaml", "references/a.md"},
        )
