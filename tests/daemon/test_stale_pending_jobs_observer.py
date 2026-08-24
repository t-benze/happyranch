"""Tests for the all-org stale never-started pending-job observer (THR-195).

The observer is OBSERVATION ONLY: it must never mutate lifecycle state. It
scans every organization discovered via the supported runtime/org registry
(``RuntimeDir.iter_org_roots``) for ``status='pending' AND started_at IS NULL``
jobs older than a justified threshold, so a recurrence of the historical
submission-to-dispatch orphan is surfaced instead of silently stranding tasks.
"""
from __future__ import annotations

import hashlib
import sqlite3
import tempfile
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


# ---------------------------------------------------------------------------
# Adversarial read-only guarantees: existing/legacy/malformed stores untouched
# ---------------------------------------------------------------------------

def _snapshot_store(org_root: Path) -> dict:
    """Byte + schema + sidecar snapshot of an org store (read-only)."""
    db_path = org_root / "happyranch.db"
    files = sorted(
        p.name for p in org_root.iterdir() if p.is_file()
    )
    if not db_path.is_file():
        return {"bytes": None, "schema": None, "files": files}
    conn = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
    try:
        try:
            schema = conn.execute(
                "SELECT name, sql FROM sqlite_master ORDER BY name"
            ).fetchall()
        except sqlite3.DatabaseError:
            schema = None  # malformed store: bytes comparison still applies
    finally:
        conn.close()
    return {"bytes": db_path.read_bytes(), "schema": schema, "files": files}


def _legacy_script_requests_db(db_path: Path) -> None:
    """Hand-build a PRE-MIGRATION store: the legacy ``script_requests`` table
    that ``Database.__init__`` would rename to ``jobs`` and add columns to.
    A read-only observer must never run that migration."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE script_requests ("
        "id TEXT PRIMARY KEY, task_id TEXT, status TEXT, started_at TEXT, created_at TEXT)"
    )
    conn.execute(
        "INSERT INTO script_requests VALUES ("
        "'SR-001','TASK-001','pending',NULL,'2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()


def test_existing_db_bytes_schema_sidecars_unchanged_across_repeated_scans(tmp_path: Path):
    """An EXISTING migrated store is byte-identical (bytes + schema) and gains
    no ``-wal``/``-shm``/``-journal`` sidecars across repeated registry scans."""
    rt = RuntimeDir.init(tmp_path / "runtime")
    db = _seed_org(rt, "happyranch")
    _seed_task(db, "TASK-001", TaskStatus.FAILED)
    _seed_job(
        db, job_id="JOB-001", task_id="TASK-001",
        created_at=_z(_now() - timedelta(days=60)),
    )
    db.close()  # clean close: SQLite checkpoints and removes -wal/-shm
    org_root = rt.orgs_dir / "happyranch"
    before = _snapshot_store(org_root)

    for _ in range(2):
        results = scan_all_org_stale_pending(rt, now=_now())
        assert [f["id"] for f in results["happyranch"]] == ["JOB-001"]

    after = _snapshot_store(org_root)
    assert after["bytes"] == before["bytes"]
    assert after["schema"] == before["schema"]
    assert after["files"] == before["files"] == ["happyranch.db"]


def test_legacy_pre_migration_db_never_migrated_or_mutated(tmp_path: Path):
    """An EXISTING pre-migration store (legacy ``script_requests`` shape) is
    never migrated by observation: the scan fails closed (no ``jobs`` table)
    and leaves bytes/schema/sidecars untouched — where ``Database(db_path)``
    would have RENAMED the table and added columns (durable mutation)."""
    rt = RuntimeDir.init(tmp_path / "runtime")
    org_root = rt.orgs_dir / "happyranch"
    org_root.mkdir(parents=True)
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams: {}\n")
    _legacy_script_requests_db(org_root / "happyranch.db")
    before = _snapshot_store(org_root)

    with pytest.raises(sqlite3.OperationalError):
        scan_all_org_stale_pending(rt, now=_now())

    after = _snapshot_store(org_root)
    assert after["bytes"] == before["bytes"]
    assert after["schema"] == before["schema"]
    assert after["files"] == before["files"] == ["happyranch.db"]


def test_malformed_db_fails_closed_without_durable_mutation(tmp_path: Path):
    """A malformed/irrelevant store fails closed (raises) without durable
    mutation: no bytes change, no sidecars created, repeated scans idempotent."""
    rt = RuntimeDir.init(tmp_path / "runtime")
    org_root = rt.orgs_dir / "happyranch"
    org_root.mkdir(parents=True)
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams: {}\n")
    db_path = org_root / "happyranch.db"
    db_path.write_bytes(b"this is not a sqlite database at all\x00\x01" * 8)
    before = _snapshot_store(org_root)

    with pytest.raises(sqlite3.DatabaseError):
        scan_all_org_stale_pending(rt, now=_now())

    after = _snapshot_store(org_root)
    assert after["bytes"] == before["bytes"]
    assert after["files"] == before["files"] == ["happyranch.db"]


def test_irrelevant_legacy_store_no_jobs_table_fails_closed(tmp_path: Path):
    """A legacy store with only an unrelated table (no ``jobs`` at all) fails
    closed instead of fabricating candidates or mutating."""
    rt = RuntimeDir.init(tmp_path / "runtime")
    org_root = rt.orgs_dir / "happyranch"
    org_root.mkdir(parents=True)
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams: {}\n")
    conn = sqlite3.connect(str(org_root / "happyranch.db"))
    conn.execute("CREATE TABLE unrelated (id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO unrelated VALUES ('x')")
    conn.commit()
    conn.close()
    before = _snapshot_store(org_root)

    with pytest.raises(sqlite3.OperationalError):
        scan_all_org_stale_pending(rt, now=_now())

    after = _snapshot_store(org_root)
    assert after["bytes"] == before["bytes"]
    assert after["files"] == before["files"] == ["happyranch.db"]


# ---------------------------------------------------------------------------
# Daemon-startup wiring: registry-wide scan includes BROKEN org roots
# ---------------------------------------------------------------------------

def test_startup_observation_includes_broken_org_root(tmp_path: Path, tmp_home, caplog):
    """A current DB-bearing org root whose OrgState.load fails (broken_orgs)
    still reaches observation: the startup scan is registry-wide
    (``RuntimeDir.iter_org_roots``), not limited to ``state.orgs.values()``."""
    from fastapi.testclient import TestClient
    import logging

    from runtime.config import Settings
    from runtime.daemon.app import create_app
    from runtime.daemon.state import DaemonState
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.agent_def import AgentDef, render_agent_text

    rt = RuntimeDir.init(tmp_path / "runtime")
    # Healthy org with a stale pending job.
    alpha = _seed_org(rt, "alpha")
    _seed_task(alpha, "TASK-516", TaskStatus.FAILED)
    _seed_job(
        alpha, job_id="JOB-002", task_id="TASK-516",
        created_at=_z(_now() - timedelta(days=89)),
        review_required=True,
    )
    alpha.close()
    # Broken org: teams.yaml + drifted agent (OrgConsistencyError on load), but
    # WITH an existing DB carrying a stale pending job.
    broken_root = rt.orgs_dir / "broken"
    broken_root.mkdir(parents=True)
    (broken_root / "org").mkdir()
    (broken_root / "org" / "teams.yaml").write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: engineering_head\n"
        "    workers: [dev_agent]\n"
    )
    (broken_root / "org" / "agents").mkdir()
    paths = OrgPaths(root=broken_root)
    manager = AgentDef(
        name="solo_manager",
        team="missing_team",
        role="manager",
        executor="claude",
        allow_rules=(),
        repos={},
        enrolled_by="founder",
        enrolled_at_task=None,
        enrolled_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
        system_prompt="You are solo.\n",
        description="Solo",
    )
    (paths.agents_dir / "solo_manager.md").write_text(render_agent_text(manager))
    broken_db = Database(broken_root / "happyranch.db")
    _seed_task(broken_db, "TASK-777", TaskStatus.FAILED)
    _seed_job(
        broken_db, job_id="JOB-777", task_id="TASK-777",
        created_at=_z(_now() - timedelta(days=60)),
    )
    broken_db.close()

    state = DaemonState.from_runtime(rt, Settings())
    assert "broken" in state.broken_orgs  # OrgState.load refused it
    assert "broken" not in state.orgs
    app = create_app(state)

    with caplog.at_level(logging.WARNING, logger="happyranch.daemon"):
        with TestClient(app) as client:
            assert client.get("/healthz").status_code in (200, 404)
            # Observation never mutates the broken org's candidate.
            import sqlite3 as _sqlite3
            read = _sqlite3.connect(
                f"file:{broken_root / 'happyranch.db'}?immutable=1", uri=True
            )
            try:
                row = read.execute(
                    "SELECT status, started_at, finished_at, reason "
                    "FROM jobs WHERE id='JOB-777'"
                ).fetchone()
            finally:
                read.close()
            assert row is not None
            assert row[0] == "pending" and row[1] is None
            assert row[2] is None and row[3] is None

    messages = " ".join(r.message for r in caplog.records)
    assert "JOB-002" in messages  # healthy org still observed
    assert "JOB-777" in messages  # broken org's candidate reaches observation


# ---------------------------------------------------------------------------
# Active-WAL observation: DIRECT read-only connection on the source (TASK-5542,
# fourth-round founder correction). The temporary snapshot/copy machinery is
# retired entirely. SQLite's WAL reader — even ``mode=ro`` — updates
# shared-memory reader/lock/index state in the source ``-shm`` while a read is
# active (proved by TASK-5517); the founder contract EXPLICITLY permits
# transient source ``-shm`` reader/lock/index-byte changes and mtime changes
# during a read. The hard contract: source ``happyranch.db`` and
# ``happyranch.db-wal`` byte-identical before/after every observation, no
# job/task/audit row or schema write, and no snapshot/temp directory created
# anywhere (especially under an org root).
# ---------------------------------------------------------------------------


def _open_active_wal_org(rt: RuntimeDir, slug: str) -> Database:
    """Seed an org and return a KEPT-OPEN writer whose candidate is committed
    only to the WAL (``wal_autocheckpoint=0`` → the main DB never receives
    it, so the row provably lives in the ``-wal`` alone)."""
    db = _seed_org(rt, slug)
    db.execute("PRAGMA wal_autocheckpoint=0")
    _seed_task(db, "TASK-001", TaskStatus.FAILED)
    _seed_job(
        db, job_id="JOB-WAL", task_id="TASK-001",
        created_at=_z(_now() - timedelta(days=60)),
    )
    return db


def _store_hashes(org_root: Path) -> dict[str, str | None]:
    """SHA-256 of every source store file (DB, -wal, -shm)."""
    return {
        name: (
            hashlib.sha256((org_root / name).read_bytes()).hexdigest()
            if (org_root / name).exists() else None
        )
        for name in ("happyranch.db", "happyranch.db-wal", "happyranch.db-shm")
    }


def _store_mtimes(org_root: Path) -> dict[str, int | None]:
    """mtime (ns) of every source store file. The founder contract (TASK-5542)
    explicitly permits transient source ``-shm`` mtime changes during a read;
    the values are captured and REPORTED factually, never pinned byte-equal."""
    return {
        name: (org_root / name).stat().st_mtime_ns
        if (org_root / name).exists() else None
        for name in ("happyranch.db", "happyranch.db-wal", "happyranch.db-shm")
    }


def _wal_only_row_count(db_path: Path) -> list:
    """Candidate rows visible in a main-only (immutable=1, WAL-ignoring) view.
    ``OperationalError`` (no jobs table in the main file) is itself proof the
    table — and the candidate — live only in the WAL."""
    conn = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
    try:
        try:
            return conn.execute(
                "SELECT id FROM jobs WHERE id='JOB-WAL'"
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    finally:
        conn.close()


def test_active_wal_scan_observes_wal_only_candidate_without_source_mutation(
    tmp_path: Path,
):
    """MANDATORY deterministic proof at the ACTUAL production scanner seam
    (TASK-5542): hold a writer OPEN with a candidate committed only to the
    WAL; call the production all-org route; assert the candidate is observed;
    assert source ``happyranch.db`` and ``happyranch.db-wal`` SHA-256 are
    byte-identical before/after; assert row/schema/audit state unchanged;
    record the actual source ``-shm`` existence/hash/mtime behavior WITHOUT
    requiring byte equality (transient reader/lock/index-byte and mtime
    changes during a read are explicitly permitted); assert no snapshot/temp
    directory is created anywhere (especially under the org root).
    """
    rt = RuntimeDir.init(tmp_path / "runtime")
    writer = _open_active_wal_org(rt, "happyranch")
    org_root = rt.orgs_dir / "happyranch"
    db_path = org_root / "happyranch.db"
    assert (org_root / "happyranch.db-wal").exists()
    assert (org_root / "happyranch.db-shm").exists()

    # The candidate is committed ONLY to the WAL — the main DB never got it.
    assert _wal_only_row_count(db_path) == []

    before = _store_hashes(org_root)
    before_mtimes = _store_mtimes(org_root)
    shm_before_exists = (org_root / "happyranch.db-shm").exists()
    org_files_before = sorted(p.name for p in org_root.iterdir())
    leftovers_before = set(
        p.name for p in Path(tempfile.gettempdir()).glob("happyranch-stale-scan-*")
    )

    # Production all-org route (registry-wide, includes this org root).
    results = scan_all_org_stale_pending(rt, now=_now())
    assert [f["id"] for f in results["happyranch"]] == ["JOB-WAL"]
    assert results["happyranch"][0]["task_id"] == "TASK-001"

    after = _store_hashes(org_root)
    after_mtimes = _store_mtimes(org_root)

    # CONTRACT: source main DB and -wal byte-identical before/after.
    assert after["happyranch.db"] == before["happyranch.db"], (
        "source happyranch.db changed"
    )
    assert after["happyranch.db-wal"] == before["happyranch.db-wal"], (
        "source happyranch.db-wal changed"
    )

    # Factual -shm behavior: existence, hash and mtime are recorded WITHOUT
    # byte-equality assertions (transient reader/lock/index changes and mtime
    # changes during a read are explicitly permitted by the founder contract).
    shm = org_root / "happyranch.db-shm"
    assert shm.exists() == shm_before_exists, (
        f"source -shm existence changed: before={shm_before_exists} "
        f"after={shm.exists()}"
    )
    assert after["happyranch.db-shm"] is not None
    assert after_mtimes["happyranch.db-shm"] is not None
    print(
        "[active-wal-observer] source -shm factual behavior: "
        f"exists_before={shm_before_exists} exists_after={shm.exists()} "
        f"sha256_before={before['happyranch.db-shm']} "
        f"sha256_after={after['happyranch.db-shm']} "
        f"mtime_ns_before={before_mtimes['happyranch.db-shm']} "
        f"mtime_ns_after={after_mtimes['happyranch.db-shm']}"
    )

    # No snapshot/temp directory created anywhere (especially under org root)
    # — the snapshot machinery is retired.
    assert sorted(p.name for p in org_root.iterdir()) == org_files_before
    assert not list(org_root.rglob("happyranch-stale-scan-*"))
    leftovers_after = set(
        p.name for p in Path(tempfile.gettempdir()).glob("happyranch-stale-scan-*")
    )
    assert leftovers_after == leftovers_before

    # No source schema/row/audit change (writer-connection view; the observer
    # wrote nothing and did not checkpoint the candidate into the main DB).
    row = writer.get_job("JOB-WAL")
    assert row is not None
    assert row.status == JobStatus.PENDING and row.started_at is None
    assert writer.get_audit_logs("TASK-001") == []
    assert _wal_only_row_count(db_path) == []  # still WAL-only
    writer.close()


def test_active_wal_scan_reads_source_directly_with_no_snapshot(
    tmp_path: Path, monkeypatch,
):
    """The active-WAL route opens the SOURCE itself with a genuine read-only
    connection (``mode=ro``) — no snapshot copy, no temp DB, no URI pointing
    anywhere but the source store file."""
    import runtime.infrastructure.database as database_mod

    rt = RuntimeDir.init(tmp_path / "runtime")
    writer = _open_active_wal_org(rt, "happyranch")
    org_root = rt.orgs_dir / "happyranch"
    db_path = org_root / "happyranch.db"

    real_connect = database_mod.sqlite3.connect
    uris: list[str] = []

    def recording_connect(database, **kw):
        uris.append(str(database))
        return real_connect(database, **kw)

    monkeypatch.setattr(database_mod.sqlite3, "connect", recording_connect)

    results = scan_all_org_stale_pending(rt, now=_now())
    assert [f["id"] for f in results["happyranch"]] == ["JOB-WAL"]

    wal_read_uris = [u for u in uris if "mode=ro" in u]
    assert wal_read_uris, f"no mode=ro URI recorded: {uris}"
    assert all(
        u.startswith(f"file:{db_path}?mode=ro") for u in wal_read_uris
    ), f"reader opened a non-source URI: {wal_read_uris}"
    assert not any(
        "snapshot.db" in u or "stale-scan" in u for u in uris
    ), f"a snapshot/temp URI was opened: {uris}"
    writer.close()


def test_active_wal_scan_never_creates_temp_dirs_under_org_root(
    tmp_path: Path, monkeypatch,
):
    """Hostile TMPDIR regression (TASK-5539 HIGH finding, founder TASK-5542
    fourth-round ruling): the observer must create NO snapshot/temp directory
    anywhere — especially under an org root. This points the process temp
    directory INSIDE the org root and forces ``tempfile.mkdtemp`` to raise if
    called; the production active-WAL scan must neither create any file under
    the org root nor invoke the temp machinery at all.

    RED against the prior snapshot mechanism (head 337acd06): that code
    called ``tempfile.mkdtemp(prefix='happyranch-stale-scan-')`` and, when
    the temp parent resolved under an org root, created and wrote
    ``happyranch-stale-scan-*`` there (independent reviewer probe TASK-5539
    reproduced the write).
    """
    import tempfile as tempfile_mod

    rt = RuntimeDir.init(tmp_path / "runtime")
    writer = _open_active_wal_org(rt, "happyranch")
    org_root = rt.orgs_dir / "happyranch"

    # Hostile: resolve the process temp dir INSIDE the org root.
    hostile_temp = org_root / "tmp"
    hostile_temp.mkdir()
    monkeypatch.setattr(tempfile_mod, "gettempdir", lambda: str(hostile_temp))
    mkdtemp_calls = {"n": 0}

    def hostile_mkdtemp(*args, **kwargs):
        mkdtemp_calls["n"] += 1
        raise AssertionError(
            "observer must never call tempfile.mkdtemp (snapshot machinery retired)"
        )

    monkeypatch.setattr(tempfile_mod, "mkdtemp", hostile_mkdtemp)

    org_files_before = sorted(p.name for p in org_root.iterdir())
    results = scan_all_org_stale_pending(rt, now=_now())
    assert [f["id"] for f in results["happyranch"]] == ["JOB-WAL"]

    # No scanner temp dir anywhere under the org root; nothing new at all.
    assert mkdtemp_calls["n"] == 0
    assert sorted(p.name for p in org_root.iterdir()) == org_files_before
    assert not list(org_root.rglob("happyranch-stale-scan-*"))
    writer.close()


def test_active_wal_scan_read_error_fails_closed_without_source_mutation(
    tmp_path: Path, monkeypatch,
):
    """An I/O error while the direct reader is open propagates (fail closed)
    with zero source mutation: no bytes change, no files created."""
    import runtime.infrastructure.database as database_mod

    rt = RuntimeDir.init(tmp_path / "runtime")
    writer = _open_active_wal_org(rt, "happyranch")
    org_root = rt.orgs_dir / "happyranch"

    real_connect = database_mod.sqlite3.connect

    def failing_connect(database, **kw):
        if str(database).startswith("file:") and "mode=ro" in str(database):
            raise sqlite3.DatabaseError("injected read failure")
        return real_connect(database, **kw)

    monkeypatch.setattr(database_mod.sqlite3, "connect", failing_connect)

    before = _store_hashes(org_root)
    files_before = sorted(p.name for p in org_root.iterdir())
    with pytest.raises(sqlite3.DatabaseError):
        scan_all_org_stale_pending(rt, now=_now())
    after = _store_hashes(org_root)
    # Hard contract: source main DB and -wal byte-identical.
    assert after["happyranch.db"] == before["happyranch.db"]
    assert after["happyranch.db-wal"] == before["happyranch.db-wal"]
    assert sorted(p.name for p in org_root.iterdir()) == files_before
    writer.close()


def test_active_wal_malformed_store_fails_closed_direct_read(tmp_path: Path):
    """A malformed store that ALSO carries -wal/-shm sidecars (the active-WAL
    shape) still fails closed (DatabaseError) through the direct read-only
    reader. Hard contract: source main DB and ``-wal`` stay byte-identical;
    the ``-shm`` is the explicitly-permitted shared-memory surface — its
    existence/hash/mtime are recorded, never pinned byte-equal."""
    rt = RuntimeDir.init(tmp_path / "runtime")
    org_root = rt.orgs_dir / "happyranch"
    org_root.mkdir(parents=True)
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams: {}\n")
    (org_root / "happyranch.db").write_bytes(
        b"this is not a sqlite database at all\x00\x01" * 8
    )
    (org_root / "happyranch.db-wal").write_bytes(b"\x00" * 4096)
    before = _store_hashes(org_root)
    files_before = sorted(p.name for p in org_root.iterdir())
    shm_before_exists = (org_root / "happyranch.db-shm").exists()

    with pytest.raises(sqlite3.DatabaseError):
        scan_all_org_stale_pending(rt, now=_now())

    after = _store_hashes(org_root)
    assert after["happyranch.db"] == before["happyranch.db"]
    assert after["happyranch.db-wal"] == before["happyranch.db-wal"]
    # Accepted reader effect: SQLite WAL-init may create the source -shm when
    # it was absent (explicitly-permitted -shm existence change); no other
    # new file appears.
    assert set(sorted(p.name for p in org_root.iterdir())) - set(files_before) <= {
        "happyranch.db-shm"
    }
    print(
        "[active-wal-observer][malformed] source -shm factual behavior: "
        f"exists_before={shm_before_exists} "
        f"exists_after={(org_root / 'happyranch.db-shm').exists()}"
    )


def test_active_wal_pre_migration_store_fails_closed_direct_read(tmp_path: Path):
    """An EXISTING pre-migration store (legacy ``script_requests`` shape) that
    carries -wal/-shm sidecars fails closed (no jobs table) through the direct
    read-only reader and is never migrated/altered. Hard contract: source main
    DB and ``-wal`` stay byte-identical; the ``-shm`` is the explicitly-
    permitted shared-memory surface (existence/hash/mtime recorded, not
    pinned byte-equal)."""
    rt = RuntimeDir.init(tmp_path / "runtime")
    org_root = rt.orgs_dir / "happyranch"
    org_root.mkdir(parents=True)
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams: {}\n")
    _legacy_script_requests_db(org_root / "happyranch.db")
    # Simulate a legacy store that was left WAL-active (crash leftovers).
    (org_root / "happyranch.db-wal").write_bytes(b"\x00" * 4096)
    before = _store_hashes(org_root)
    files_before = sorted(p.name for p in org_root.iterdir())
    shm_before_exists = (org_root / "happyranch.db-shm").exists()

    with pytest.raises(sqlite3.OperationalError):
        scan_all_org_stale_pending(rt, now=_now())

    after = _store_hashes(org_root)
    assert after["happyranch.db"] == before["happyranch.db"]
    assert after["happyranch.db-wal"] == before["happyranch.db-wal"]
    assert set(sorted(p.name for p in org_root.iterdir())) - set(files_before) <= {
        "happyranch.db-shm"
    }
    print(
        "[active-wal-observer][pre-migration] source -shm factual behavior: "
        f"exists_before={shm_before_exists} "
        f"exists_after={(org_root / 'happyranch.db-shm').exists()}"
    )
