from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.infrastructure.database import Database


def _seed_legacy_scripts_db(db_path: Path) -> None:
    """Hand-build a v0 DB containing only the legacy script_requests shape plus
    related audit/notification rows. Mirrors the rows a pre-rename org would have
    on disk the moment a new-daemon startup runs the migration.
    """
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE script_requests (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            title TEXT NOT NULL,
            rationale TEXT NOT NULL,
            script_text TEXT NOT NULL,
            interpreter TEXT NOT NULL,
            cwd_hint TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            exit_code INTEGER,
            stdout_head TEXT,
            stderr_head TEXT,
            stdout_path TEXT,
            stderr_path TEXT,
            duration_ms INTEGER,
            started_at TEXT,
            finished_at TEXT,
            reviewed_at TEXT,
            reviewed_by TEXT,
            reject_reason TEXT,
            cwd_resolved TEXT,
            timeout_seconds INTEGER NOT NULL DEFAULT 300,
            created_at TEXT NOT NULL
        );
        CREATE INDEX idx_script_requests_task   ON script_requests(task_id);
        CREATE INDEX idx_script_requests_status ON script_requests(status);

        CREATE TABLE audit_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id   TEXT NOT NULL,
            agent     TEXT NOT NULL,
            action    TEXT NOT NULL,
            payload   TEXT,
            timestamp TEXT NOT NULL
        );

        CREATE TABLE escalation_notifications (
            feishu_message_id TEXT PRIMARY KEY,
            org_slug          TEXT NOT NULL,
            task_id           TEXT NOT NULL,
            chat_id           TEXT NOT NULL,
            created_at        TEXT NOT NULL,
            expires_at        TEXT NOT NULL,
            consumed_at       TEXT,
            consumed_by       TEXT,
            kind              TEXT NOT NULL DEFAULT 'escalation'
        );
    """)

    # Three legacy rows: one completed, one rejected, one stuck in 'running'.
    rows = [
        ("SR-001", "TASK-010", "dev_agent", "close PR", "needs creds",
         "gh pr close 1\n", "bash", None, "completed", 0,
         "ok\n", None,
         "/runtime/orgs/sample/scripts/SR-001.out",
         "/runtime/orgs/sample/scripts/SR-001.err",
         1500, "2026-05-20T00:00:00Z", "2026-05-20T00:00:01Z",
         "2026-05-20T00:00:00Z", "founder", None, None, 300,
         "2026-05-19T23:59:59Z"),
        ("SR-002", "TASK-011", "dev_agent", "rotate key", "needs aws",
         "aws iam create-access-key\n", "bash", None, "rejected", None,
         None, None, None, None, None, None,
         "2026-05-20T01:00:00Z", "2026-05-20T01:00:00Z",
         "founder", "unsafe", None, 300,
         "2026-05-20T00:59:59Z"),
        ("SR-003", "TASK-012", "dev_agent", "long ssh", "needs prod",
         "ssh prod 'long task'\n", "bash", None, "running", None,
         "starting...\n", None,
         "/runtime/orgs/sample/scripts/SR-003.out",
         "/runtime/orgs/sample/scripts/SR-003.err",
         None, "2026-05-20T02:00:00Z", None,
         "2026-05-20T02:00:00Z", "founder", None, None, 600,
         "2026-05-20T01:59:59Z"),
    ]
    conn.executemany(
        "INSERT INTO script_requests VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )

    conn.executemany(
        "INSERT INTO audit_log (timestamp, task_id, agent, action, payload) "
        "VALUES (?,?,?,?,?)",
        [
            ("2026-05-20T00:00:00Z", "TASK-010", "dev_agent", "script_submitted",
             json.dumps({"script_id": "SR-001", "task_id": "TASK-010"})),
            ("2026-05-20T00:00:01Z", "TASK-010", "dev_agent", "script_completed",
             json.dumps({"script_id": "SR-001", "exit_code": 0})),
            ("2026-05-20T01:00:00Z", "TASK-011", "dev_agent", "script_rejected",
             json.dumps({"script_id": "SR-002", "reason": "unsafe"})),
        ],
    )

    conn.executemany(
        "INSERT INTO escalation_notifications "
        "(feishu_message_id, org_slug, task_id, chat_id, created_at, expires_at, kind) "
        "VALUES (?,?,?,?,?,?,?)",
        [
            ("msg-001", "sample", "SR-001", "chat-1",
             "2026-05-20T00:00:00Z", "2026-05-20T01:00:00Z", "script_request"),
            ("msg-002", "sample", "SR-002", "chat-1",
             "2026-05-20T01:00:00Z", "2026-05-20T02:00:00Z", "script_request"),
        ],
    )
    conn.commit()
    conn.close()


def test_migration_renames_table_and_rewrites_ids(tmp_path: Path) -> None:
    db_path = tmp_path / "grassland.db"
    _seed_legacy_scripts_db(db_path)

    # Initializing Database against the legacy file should run the migration.
    Database(db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        # 1. Table renamed
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        assert "jobs" in tables
        assert "script_requests" not in tables

        # 2. IDs rewritten
        ids = sorted(r[0] for r in conn.execute("SELECT id FROM jobs"))
        assert ids == ["JOB-001", "JOB-002", "JOB-003"]

        # 3. Output paths rewritten
        paths = dict(conn.execute(
            "SELECT id, stdout_path FROM jobs WHERE stdout_path IS NOT NULL"))
        assert paths["JOB-001"] == "/runtime/orgs/sample/jobs/JOB-001.out"
        assert paths["JOB-003"] == "/runtime/orgs/sample/jobs/JOB-003.out"

        # 4. Running row force-failed with reason=daemon_crash
        row = conn.execute(
            "SELECT status, reason FROM jobs WHERE id = 'JOB-003'").fetchone()
        assert row == ("failed", "daemon_crash")

        # 5. New columns present with correct legacy defaults
        cols = {r[1]: r for r in conn.execute("PRAGMA table_info(jobs)")}
        assert "review_required" in cols
        assert "persistent" in cols
        assert "max_output_bytes" in cols
        assert "max_runtime_seconds" in cols
        assert "reason" in cols
        # Legacy rows backfilled to review_required=1 (they were all script-requests)
        legacy_flags = list(conn.execute(
            "SELECT review_required, persistent FROM jobs"))
        for rr, pers in legacy_flags:
            assert rr == 1
            assert pers == 0

        # 6. Legacy timeout_seconds → max_runtime_seconds
        runtimes = dict(conn.execute(
            "SELECT id, max_runtime_seconds FROM jobs"))
        assert runtimes["JOB-001"] == 300
        assert runtimes["JOB-003"] == 600
        # And the old column is gone
        assert "timeout_seconds" not in cols

        # 7. Audit kinds rewritten
        kinds = sorted(r[0] for r in conn.execute(
            "SELECT DISTINCT action FROM audit_log"))
        assert kinds == ["job_completed", "job_rejected", "job_submitted"]

        # 8. Audit payloads rewritten (script_id → job_id, SR- → JOB-)
        payloads = [json.loads(r[0]) for r in conn.execute(
            "SELECT payload FROM audit_log ORDER BY id")]
        assert payloads[0] == {"job_id": "JOB-001", "task_id": "TASK-010"}
        assert payloads[1] == {"job_id": "JOB-001", "exit_code": 0}
        assert payloads[2] == {"job_id": "JOB-002", "reason": "unsafe"}

        # 9. Notification kind rewritten
        notif_kinds = sorted(r[0] for r in conn.execute(
            "SELECT DISTINCT kind FROM escalation_notifications"))
        assert notif_kinds == ["job_request"]
        # And the FK-ish task_id renamed for those rows
        notif_ids = sorted(r[0] for r in conn.execute(
            "SELECT task_id FROM escalation_notifications"))
        assert notif_ids == ["JOB-001", "JOB-002"]

        # 10. Indexes recreated under new names
        idx_names = sorted(r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'jobs_%'"))
        assert "jobs_status_idx" in idx_names
        assert "jobs_task_id_idx" in idx_names
    finally:
        conn.close()


def test_migration_is_idempotent(tmp_path: Path) -> None:
    """Running Database init twice on an already-migrated DB must be a no-op."""
    db_path = tmp_path / "grassland.db"
    _seed_legacy_scripts_db(db_path)
    Database(db_path)
    # Second init: must not crash and must not duplicate-migrate.
    Database(db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        # Still only the 3 rows we seeded; no duplicates from running migration twice.
        n = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        assert n == 3
    finally:
        conn.close()


def test_fresh_install_has_correct_defaults(tmp_path: Path) -> None:
    """A fresh DB (no legacy table) gets the jobs table with DEFAULT 0 for both flags."""
    db_path = tmp_path / "grassland.db"
    Database(db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        cols = {r[1]: r for r in conn.execute("PRAGMA table_info(jobs)")}
        # SQLite PRAGMA table_info returns (cid, name, type, notnull, dflt_value, pk)
        # dflt_value comes as a string for INTEGER DEFAULT columns.
        assert cols["review_required"][4] == "0"
        assert cols["persistent"][4] == "0"
    finally:
        conn.close()
