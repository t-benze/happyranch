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
    collect_source_files,
    compute_v2_fingerprint,
    deactivate_schedules,
    gather_legacy_skill_evidence,
    verify_b2_custom_skills,
    verify_sqlite_integrity,
)
from runtime.portability.archive import sha256_bytes


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
    _write(pkg / "SKILL.md", "# QA\n\nbody\n")
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
