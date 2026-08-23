"""Tests for the all-org stale never-started pending-job observer (THR-195).

The observer is OBSERVATION ONLY: it must never mutate lifecycle state. It
scans every organization discovered via the supported runtime/org registry
(``RuntimeDir.iter_org_roots``) for ``status='pending' AND started_at IS NULL``
jobs older than a justified threshold, so a recurrence of the historical
submission-to-dispatch orphan is surfaced instead of silently stranding tasks.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from runtime.daemon.stale_pending_jobs import (
    STALE_PENDING_JOB_MAX_AGE,
    scan_all_org_stale_pending,
    scan_org_stale_pending,
)
from runtime.infrastructure.database import Database
from runtime.models import JobInterpreter, JobRecord, JobStatus, TaskRecord, TaskStatus
from runtime.runtime import RuntimeDir


def _z(iso: datetime) -> str:
    return iso.isoformat().replace("+00:00", "Z")


def _now() -> datetime:
    return datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)


def _seed_org(runtime: RuntimeDir, slug: str) -> Database:
    """Create an org dir with teams.yaml and return a Database over its DB."""
    org_root = runtime.orgs_dir / slug
    org_root.mkdir(parents=True)
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: engineering_head\n"
        "    workers: [dev_agent, qa_engineer]\n"
    )
    return Database(org_root / "happyranch.db")


def _seed_task(db: Database, task_id: str, status: TaskStatus) -> None:
    db.insert_task(TaskRecord(
        id=task_id,
        assigned_agent="qa_engineer",
        team="engineering",
        brief="fixture",
        status=status,
    ))


def _seed_job(
    db: Database,
    *,
    job_id: str,
    task_id: str,
    created_at: str,
    status: JobStatus = JobStatus.PENDING,
    started_at: str | None = None,
    review_required: bool = False,
) -> None:
    db.insert_job(JobRecord(
        id=job_id,
        task_id=task_id,
        agent_name="qa_engineer",
        title=f"fixture {job_id}",
        rationale="",
        script_text="echo hi",
        interpreter=JobInterpreter.BASH,
        status=status,
        started_at=started_at,
        review_required=review_required,
        created_at=created_at,
    ))


# ---------------------------------------------------------------------------
# Predicate boundaries (age / started / status)
# ---------------------------------------------------------------------------

def test_stale_pending_job_flagged(tmp_path: Path):
    db = Database(tmp_path / "happyranch.db")
    _seed_task(db, "TASK-001", TaskStatus.FAILED)
    _seed_job(
        db, job_id="JOB-001", task_id="TASK-001",
        created_at=_z(_now() - STALE_PENDING_JOB_MAX_AGE - timedelta(days=1)),
    )
    findings = scan_org_stale_pending(db, now=_now())
    assert [f["id"] for f in findings] == ["JOB-001"]


def test_fresh_pending_job_excluded(tmp_path: Path):
    db = Database(tmp_path / "happyranch.db")
    _seed_task(db, "TASK-001", TaskStatus.IN_PROGRESS)
    _seed_job(
        db, job_id="JOB-001", task_id="TASK-001",
        created_at=_z(_now() - timedelta(hours=1)),
    )
    assert scan_org_stale_pending(db, now=_now()) == []


def test_boundary_exactly_at_threshold_flagged(tmp_path: Path):
    db = Database(tmp_path / "happyranch.db")
    _seed_task(db, "TASK-001", TaskStatus.FAILED)
    _seed_job(
        db, job_id="JOB-001", task_id="TASK-001",
        created_at=_z(_now() - STALE_PENDING_JOB_MAX_AGE),
    )
    findings = scan_org_stale_pending(db, now=_now())
    assert [f["id"] for f in findings] == ["JOB-001"]


def test_started_pending_job_excluded(tmp_path: Path):
    """A pending row with started_at set was dispatched — never an orphan."""
    db = Database(tmp_path / "happyranch.db")
    _seed_task(db, "TASK-001", TaskStatus.FAILED)
    _seed_job(
        db, job_id="JOB-001", task_id="TASK-001",
        created_at=_z(_now() - timedelta(days=30)),
        started_at=_z(_now() - timedelta(days=29)),
    )
    assert scan_org_stale_pending(db, now=_now()) == []


def test_running_job_excluded(tmp_path: Path):
    db = Database(tmp_path / "happyranch.db")
    _seed_task(db, "TASK-001", TaskStatus.IN_PROGRESS)
    _seed_job(
        db, job_id="JOB-001", task_id="TASK-001",
        created_at=_z(_now() - timedelta(days=30)),
        status=JobStatus.RUNNING,
        started_at=_z(_now() - timedelta(minutes=5)),
    )
    assert scan_org_stale_pending(db, now=_now()) == []


def test_terminal_jobs_excluded(tmp_path: Path):
    db = Database(tmp_path / "happyranch.db")
    _seed_task(db, "TASK-001", TaskStatus.FAILED)
    for job_id, status in (("JOB-001", JobStatus.COMPLETED), ("JOB-002", JobStatus.FAILED)):
        _seed_job(
            db, job_id=job_id, task_id="TASK-001",
            created_at=_z(_now() - timedelta(days=30)),
            status=status,
            started_at=_z(_now() - timedelta(days=29)),
        )
    assert scan_org_stale_pending(db, now=_now()) == []


def test_review_required_stale_pending_flagged(tmp_path: Path):
    """Review-gated jobs are observed too — the consumer decides. A stale
    review-gated job on a terminal task is exactly the tourism-org shape."""
    db = Database(tmp_path / "happyranch.db")
    _seed_task(db, "TASK-001", TaskStatus.FAILED)
    _seed_job(
        db, job_id="JOB-001", task_id="TASK-001",
        created_at=_z(_now() - timedelta(days=60)),
        review_required=True,
    )
    findings = scan_org_stale_pending(db, now=_now())
    assert [f["id"] for f in findings] == ["JOB-001"]
    assert findings[0]["review_required"]  # sqlite INTEGER 1 == truthy


# ---------------------------------------------------------------------------
# Observation never mutates
# ---------------------------------------------------------------------------

def test_observation_does_not_mutate(tmp_path: Path):
    db = Database(tmp_path / "happyranch.db")
    _seed_task(db, "TASK-001", TaskStatus.FAILED)
    _seed_job(
        db, job_id="JOB-001", task_id="TASK-001",
        created_at=_z(_now() - timedelta(days=60)),
    )
    audit_before = db.get_audit_logs("TASK-001")
    scan_org_stale_pending(db, now=_now())
    row = db.get_job("JOB-001")
    assert row is not None
    assert row.status == JobStatus.PENDING  # unchanged
    assert row.started_at is None            # unchanged
    assert row.finished_at is None           # unchanged
    assert row.reason is None                # unchanged
    assert db.get_audit_logs("TASK-001") == audit_before  # no audit residue


# ---------------------------------------------------------------------------
# All-org scan: multi-org inclusion + family zero-row control
# ---------------------------------------------------------------------------

def test_scan_all_orgs_multi_org_inclusion(tmp_path: Path):
    rt = RuntimeDir.init(tmp_path / "runtime")
    tourism = _seed_org(rt, "tourism-org")
    happyranch = _seed_org(rt, "happyranch")
    _seed_org(rt, "family")  # zero-row control

    _seed_task(tourism, "TASK-516", TaskStatus.FAILED)
    for i in (2, 3, 4):
        _seed_job(
            tourism, job_id=f"JOB-00{i}", task_id="TASK-516",
            created_at=_z(_now() - timedelta(days=89)),
            review_required=True,
        )
    _seed_task(happyranch, "TASK-861", TaskStatus.COMPLETED)
    _seed_job(
        happyranch, job_id="JOB-155", task_id="TASK-861",
        created_at=_z(_now() - timedelta(days=61)),
    )

    results = scan_all_org_stale_pending(rt, now=_now())
    # tourism-org and happyranch report their candidates; family is absent
    # from findings (zero rows) but present in the scan.
    assert set(results.keys()) == {"tourism-org", "happyranch", "family"}
    assert [f["id"] for f in results["tourism-org"]] == ["JOB-002", "JOB-003", "JOB-004"]
    assert [f["id"] for f in results["happyranch"]] == ["JOB-155"]
    assert results["family"] == []


def test_scan_all_orgs_skips_reserved_and_uninitialized(tmp_path: Path):
    rt = RuntimeDir.init(tmp_path / "runtime")
    happyranch = _seed_org(rt, "happyranch")
    # Reserved name and a dir without teams.yaml must be skipped.
    (rt.orgs_dir / "_pending").mkdir(parents=True)
    (rt.orgs_dir / "half").mkdir(parents=True)
    (rt.orgs_dir / "half" / "org").mkdir()
    _seed_task(happyranch, "TASK-001", TaskStatus.FAILED)
    _seed_job(
        happyranch, job_id="JOB-001", task_id="TASK-001",
        created_at=_z(_now() - timedelta(days=60)),
    )
    results = scan_all_org_stale_pending(rt, now=_now())
    assert set(results.keys()) == {"happyranch"}
    assert [f["id"] for f in results["happyranch"]] == ["JOB-001"]


def test_scan_all_orgs_missing_db_reports_empty(tmp_path: Path):
    """An org dir without a happyranch.db must not crash the all-org scan."""
    rt = RuntimeDir.init(tmp_path / "runtime")
    org_root = rt.orgs_dir / "brand-new"
    org_root.mkdir(parents=True)
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams: {}\n")
    results = scan_all_org_stale_pending(rt, now=_now())
    assert results == {"brand-new": []}


def test_scan_all_orgs_missing_db_never_creates_a_store(tmp_path: Path):
    """Observation must not durably mutate: scanning a teams.yaml-only org
    (the ``orgs init`` skeleton materializes teams.yaml before any DB) must
    NOT create a happyranch.db or run schema migrations as a side effect.
    """
    rt = RuntimeDir.init(tmp_path / "runtime")
    org_root = rt.orgs_dir / "brand-new"
    org_root.mkdir(parents=True)
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams: {}\n")
    assert not (org_root / "happyranch.db").exists()

    results = scan_all_org_stale_pending(rt, now=_now())
    assert results == {"brand-new": []}
    # No DB file, no -wal/-shm sidecars: the scan opened nothing.
    assert not (org_root / "happyranch.db").exists()
    assert not (org_root / "happyranch.db-wal").exists()
    assert not (org_root / "happyranch.db-shm").exists()

    # Idempotent: a second scan still creates nothing.
    assert scan_all_org_stale_pending(rt, now=_now()) == {"brand-new": []}
    assert not (org_root / "happyranch.db").exists()


# ---------------------------------------------------------------------------
# Daemon-startup wiring: observation runs, logs, and never mutates
# ---------------------------------------------------------------------------

def test_startup_observation_logs_without_mutating(daemon_state, app, caplog):
    """The daemon-startup observation surfaces stale pending jobs as a warning
    and leaves every row untouched (observation is not a reaper)."""
    org = daemon_state.orgs["alpha"]
    _seed_task(org.db, "TASK-516", TaskStatus.FAILED)
    _seed_job(
        org.db, job_id="JOB-002", task_id="TASK-516",
        created_at=_z(_now() - timedelta(days=89)),
        review_required=True,
    )
    from fastapi.testclient import TestClient
    import logging
    with caplog.at_level(logging.WARNING, logger="happyranch.daemon"):
        with TestClient(app) as client:
            assert client.get("/healthz").status_code in (200, 404)
            # Observation never mutates — read BEFORE lifespan teardown closes
            # the org DB connection.
            row = org.db.get_job("JOB-002")
            assert row is not None
            assert row.status == JobStatus.PENDING
            assert row.started_at is None
            assert row.finished_at is None
            assert row.reason is None
    assert any(
        "stale never-started pending jobs" in r.message and "JOB-002" in r.message
        for r in caplog.records
    )
