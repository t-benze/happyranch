"""Additive, fail-closed SQLite schema for generic remote jobs (S2).

This module deliberately contains persistence only.  It does not enroll or
authenticate runners, select workspaces, or activate the remote-job runtime.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable


MIGRATION_NAME = "generic_remote_jobs_s2_v1"
COMPLETE_STAGE = "complete"

TABLE_SQL: dict[str, str] = {
    "remote_runner_schema_migrations": """
        CREATE TABLE remote_runner_schema_migrations (
          name TEXT PRIMARY KEY, stage TEXT NOT NULL, updated_at TEXT NOT NULL
        )
    """,
    "remote_runners": """
        CREATE TABLE remote_runners (
          id TEXT PRIMARY KEY,
          org_slug TEXT NOT NULL,
          display_name TEXT NOT NULL,
          generation INTEGER NOT NULL CHECK(generation >= 1),
          state TEXT NOT NULL CHECK(state IN ('unavailable','available','busy','draining','revoked','unhealthy')),
          capacity INTEGER NOT NULL DEFAULT 1 CHECK(capacity = 1),
          protocol_min INTEGER NOT NULL DEFAULT 1,
          protocol_max INTEGER NOT NULL DEFAULT 1,
          capabilities_json TEXT NOT NULL,
          attestation_json TEXT NOT NULL,
          attestation_digest TEXT NOT NULL,
          attested_at TEXT NOT NULL,
          attestation_expires_at TEXT NOT NULL,
          cert_serial TEXT NOT NULL,
          cert_spki_sha256 TEXT NOT NULL,
          revocation_epoch INTEGER NOT NULL DEFAULT 0 CHECK(revocation_epoch >= 0),
          last_seen_at TEXT,
          unavailable_reason TEXT,
          unhealthy_reason TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          revoked_at TEXT,
          revoke_reason TEXT,
          UNIQUE(org_slug, display_name),
          UNIQUE(org_slug, cert_serial),
          UNIQUE(org_slug, cert_spki_sha256)
        )
    """,
    "remote_runner_workspaces": """
        CREATE TABLE remote_runner_workspaces (
          id TEXT PRIMARY KEY,
          runner_id TEXT NOT NULL,
          runner_generation INTEGER NOT NULL,
          agent_name TEXT NOT NULL,
          generation INTEGER NOT NULL CHECK(generation >= 1),
          state TEXT NOT NULL CHECK(state IN ('ready','leased','recreate_pending','uncertain','retired')),
          active_attempt_id TEXT,
          root_locator_hash TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          retired_at TEXT,
          UNIQUE(id, runner_id, runner_generation, generation),
          FOREIGN KEY(runner_id) REFERENCES remote_runners(id)
        )
    """,
    "remote_job_attempts": """
        CREATE TABLE remote_job_attempts (
          id TEXT PRIMARY KEY,
          job_id TEXT NOT NULL,
          ordinal INTEGER NOT NULL CHECK(ordinal >= 1),
          runner_id TEXT NOT NULL,
          runner_generation INTEGER NOT NULL,
          workspace_id TEXT NOT NULL,
          workspace_generation INTEGER NOT NULL,
          bundle_version INTEGER NOT NULL CHECK(bundle_version = 1),
          bundle_digest TEXT NOT NULL,
          state TEXT NOT NULL CHECK(state IN ('reserved','admitted','running','cancelling','reconciling','terminal')),
          fence_token TEXT NOT NULL UNIQUE,
          lease_generation INTEGER NOT NULL CHECK(lease_generation >= 1),
          lease_expires_at TEXT NOT NULL,
          connection_id TEXT,
          cancel_requested_at TEXT,
          cancel_accepted_at TEXT,
          public_status TEXT CHECK(public_status IN ('completed','failed','rejected')),
          primary_reason TEXT,
          terminal_digest TEXT,
          terminal_committed_at TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(job_id, ordinal),
          FOREIGN KEY(job_id) REFERENCES jobs(id),
          FOREIGN KEY(runner_id) REFERENCES remote_runners(id),
          FOREIGN KEY(workspace_id, runner_id, runner_generation, workspace_generation)
            REFERENCES remote_runner_workspaces(id, runner_id, runner_generation, generation)
        )
    """,
    "remote_phase_receipts": """
        CREATE TABLE remote_phase_receipts (
          id TEXT PRIMARY KEY,
          attempt_id TEXT NOT NULL,
          phase TEXT NOT NULL CHECK(phase IN ('pre_run','workspace_observation','run','post_run','finalization')),
          ordinal INTEGER NOT NULL CHECK(ordinal >= 1),
          outcome TEXT NOT NULL CHECK(outcome IN ('not_declared','started','succeeded','failed','timed_out','output_capped','cancelled','skipped','uncertain')),
          stable_reason TEXT,
          phase_digest TEXT,
          started_at TEXT,
          finished_at TEXT,
          runtime_limit_ms INTEGER,
          stdout_limit_bytes INTEGER,
          stderr_limit_bytes INTEGER,
          exit_code INTEGER,
          stdout_bytes INTEGER,
          stderr_bytes INTEGER,
          stdout_artifact_key TEXT,
          stderr_artifact_key TEXT,
          receipt_json TEXT NOT NULL,
          receipt_digest TEXT NOT NULL,
          accepted_frame_seq INTEGER NOT NULL,
          UNIQUE(attempt_id, phase, ordinal),
          FOREIGN KEY(attempt_id) REFERENCES remote_job_attempts(id)
        )
    """,
    "remote_pre_run_observations": """
        CREATE TABLE remote_pre_run_observations (
          id TEXT PRIMARY KEY,
          attempt_id TEXT NOT NULL,
          receipt_id TEXT NOT NULL,
          runner_id TEXT NOT NULL,
          runner_generation INTEGER NOT NULL,
          workspace_id TEXT NOT NULL,
          workspace_generation INTEGER NOT NULL,
          pre_run_digest TEXT NOT NULL,
          observation_policy_version INTEGER NOT NULL,
          exclusions_policy_digest TEXT NOT NULL,
          required_roots_json TEXT NOT NULL,
          observed_roots_json TEXT NOT NULL,
          observation_json TEXT NOT NULL,
          observation_digest TEXT NOT NULL,
          complete INTEGER NOT NULL CHECK(complete IN (0,1)),
          reusable INTEGER NOT NULL CHECK(reusable IN (0,1)),
          observed_at TEXT NOT NULL,
          FOREIGN KEY(attempt_id) REFERENCES remote_job_attempts(id),
          FOREIGN KEY(receipt_id) REFERENCES remote_phase_receipts(id),
          FOREIGN KEY(workspace_id, runner_id, runner_generation, workspace_generation)
            REFERENCES remote_runner_workspaces(id, runner_id, runner_generation, generation)
        )
    """,
    "remote_protocol_frames": """
        CREATE TABLE remote_protocol_frames (
          attempt_id TEXT NOT NULL,
          connection_id TEXT NOT NULL,
          frame_seq INTEGER NOT NULL,
          frame_type TEXT NOT NULL,
          payload_digest TEXT NOT NULL,
          disposition TEXT NOT NULL CHECK(disposition IN ('accepted','duplicate','rejected')),
          received_at TEXT NOT NULL,
          PRIMARY KEY(attempt_id, connection_id, frame_seq),
          FOREIGN KEY(attempt_id) REFERENCES remote_job_attempts(id)
        )
    """,
}

INDEX_SQL: dict[str, str] = {
    "remote_one_live_workspace": """
        CREATE UNIQUE INDEX remote_one_live_workspace
          ON remote_runner_workspaces(runner_id, runner_generation, agent_name)
          WHERE state <> 'retired'
    """,
    "remote_one_live_attempt_per_job": """
        CREATE UNIQUE INDEX remote_one_live_attempt_per_job ON remote_job_attempts(job_id)
          WHERE state <> 'terminal'
    """,
    "remote_one_live_attempt_per_runner": """
        CREATE UNIQUE INDEX remote_one_live_attempt_per_runner ON remote_job_attempts(runner_id)
          WHERE state <> 'terminal'
    """,
    "remote_reuse_lookup": """
        CREATE INDEX remote_reuse_lookup ON remote_pre_run_observations(
          runner_id, runner_generation, workspace_id, workspace_generation,
          pre_run_digest, exclusions_policy_digest, observation_digest
        ) WHERE complete=1 AND reusable=1
    """,
}

JOB_COLUMNS: tuple[tuple[str, str], ...] = (
    ("execution_backend", "TEXT"),
    ("selected_runner_id", "TEXT"),
    ("remote_bundle_json", "TEXT"),
    ("remote_bundle_digest", "TEXT"),
    ("current_remote_attempt_id", "TEXT"),
)

STAGES = (
    "create_runner_tables",
    "validate_workspace_identity_parent_key",
    "create_live_workspace_index",
    "create_attempt_receipt_observation_tables",
    "create_identity_scoped_reuse_index",
    *(f"add_jobs_{name}" for name, _ in JOB_COLUMNS),
    "validate_foreign_keys_and_indexes",
    COMPLETE_STAGE,
)


def _normalized(sql: str) -> str:
    value = sql.strip().rstrip(";")
    value = re.sub(r"\bIF\s+NOT\s+EXISTS\b", "", value, flags=re.IGNORECASE)
    value = re.sub(r"[\s\"`\[\]]+", "", value).lower()
    return value


def _schema_sql(conn: sqlite3.Connection, kind: str, name: str) -> str | None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type=? AND name=?", (kind, name)
    ).fetchone()
    return None if row is None else str(row[0])


def _validate_existing_shapes(conn: sqlite3.Connection) -> None:
    for name, expected in TABLE_SQL.items():
        actual = _schema_sql(conn, "table", name)
        if actual is not None and _normalized(actual) != _normalized(expected):
            raise sqlite3.DatabaseError(f"conflicting remote-job table: {name}")
    for name, expected in INDEX_SQL.items():
        actual = _schema_sql(conn, "index", name)
        if actual is not None and _normalized(actual) != _normalized(expected):
            raise sqlite3.DatabaseError(f"conflicting remote-job index: {name}")

    job_info = {str(row[1]): row for row in conn.execute("PRAGMA table_info(jobs)")}
    for name, declared_type in JOB_COLUMNS:
        row = job_info.get(name)
        if row is not None and (
            str(row[2]).upper() != declared_type or row[3] != 0 or row[4] is not None
        ):
            raise sqlite3.DatabaseError(f"conflicting jobs column: {name}")

    marker_sql = _schema_sql(conn, "table", "remote_runner_schema_migrations")
    if marker_sql is not None:
        rows = conn.execute(
            "SELECT name, stage FROM remote_runner_schema_migrations"
        ).fetchall()
        for name, stage in rows:
            if name == MIGRATION_NAME and stage not in STAGES:
                raise sqlite3.DatabaseError("conflicting remote-job migration stage")

    if (
        _schema_sql(conn, "table", "remote_runner_workspaces") is not None
        and _schema_sql(conn, "index", "remote_one_live_workspace") is None
    ):
        duplicate = conn.execute(
            "SELECT 1 FROM remote_runner_workspaces WHERE state <> 'retired' "
            "GROUP BY runner_id, runner_generation, agent_name HAVING COUNT(*) > 1 LIMIT 1"
        ).fetchone()
        if duplicate is not None:
            raise sqlite3.IntegrityError("duplicate live remote-runner workspace")


def validate_remote_job_schema_preflight(conn: sqlite3.Connection) -> None:
    """Refuse conflicting S2 objects before broader startup schema writes."""
    _validate_existing_shapes(conn)


def _record_stage(conn: sqlite3.Connection, stage: str) -> None:
    conn.execute(
        "INSERT INTO remote_runner_schema_migrations(name, stage, updated_at) "
        "VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now')) "
        "ON CONFLICT(name) DO UPDATE SET stage=excluded.stage, updated_at=excluded.updated_at",
        (MIGRATION_NAME, stage),
    )


def _run_stage(
    conn: sqlite3.Connection,
    stage: str,
    statements: tuple[str, ...],
    stage_hook: Callable[[str], None] | None,
) -> None:
    try:
        conn.execute("BEGIN")
        for statement in statements:
            conn.execute(statement)
        _record_stage(conn, stage)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    if stage_hook is not None:
        stage_hook(stage)


def migrate_remote_job_schema(
    conn: sqlite3.Connection,
    *,
    stage_hook: Callable[[str], None] | None = None,
) -> None:
    """Converge exact absent/partial S2 schema, refusing conflicts first."""
    _validate_existing_shapes(conn)

    marker = None
    if _schema_sql(conn, "table", "remote_runner_schema_migrations") is not None:
        marker = conn.execute(
            "SELECT stage FROM remote_runner_schema_migrations WHERE name=?",
            (MIGRATION_NAME,),
        ).fetchone()
    if marker is not None and marker[0] == COMPLETE_STAGE:
        missing = [
            name for name in TABLE_SQL
            if _schema_sql(conn, "table", name) is None
        ] + [
            name for name in INDEX_SQL
            if _schema_sql(conn, "index", name) is None
        ]
        job_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(jobs)")}
        missing.extend(name for name, _ in JOB_COLUMNS if name not in job_columns)
        if missing:
            raise sqlite3.DatabaseError(
                "remote-job migration marked complete with missing objects: "
                + ", ".join(missing)
            )
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise sqlite3.IntegrityError("remote-job schema foreign-key validation failed")
        return

    def missing_tables(*names: str) -> tuple[str, ...]:
        return tuple(
            TABLE_SQL[name] for name in names
            if _schema_sql(conn, "table", name) is None
        )

    def missing_indexes(*names: str) -> tuple[str, ...]:
        return tuple(
            INDEX_SQL[name] for name in names
            if _schema_sql(conn, "index", name) is None
        )

    _run_stage(
        conn,
        "create_runner_tables",
        missing_tables(
            "remote_runner_schema_migrations", "remote_runners", "remote_runner_workspaces"
        ),
        stage_hook,
    )
    _run_stage(conn, "validate_workspace_identity_parent_key", (), stage_hook)
    _run_stage(
        conn,
        "create_live_workspace_index",
        missing_indexes("remote_one_live_workspace"),
        stage_hook,
    )
    _run_stage(
        conn,
        "create_attempt_receipt_observation_tables",
        missing_tables(
            "remote_job_attempts", "remote_phase_receipts",
            "remote_pre_run_observations", "remote_protocol_frames",
        ) + missing_indexes(
            "remote_one_live_attempt_per_job", "remote_one_live_attempt_per_runner"
        ),
        stage_hook,
    )
    _run_stage(
        conn,
        "create_identity_scoped_reuse_index",
        missing_indexes("remote_reuse_lookup"),
        stage_hook,
    )
    for name, declared_type in JOB_COLUMNS:
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(jobs)")}
        statements = () if name in columns else (f"ALTER TABLE jobs ADD COLUMN {name} {declared_type}",)
        _run_stage(conn, f"add_jobs_{name}", statements, stage_hook)

    _validate_existing_shapes(conn)
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise sqlite3.IntegrityError("remote-job schema foreign-key validation failed")
    _run_stage(conn, "validate_foreign_keys_and_indexes", (), stage_hook)
    _run_stage(conn, COMPLETE_STAGE, (), stage_hook)
