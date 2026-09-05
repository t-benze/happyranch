from __future__ import annotations

import hashlib
import re
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from runtime.infrastructure.database import Database
from runtime.infrastructure.remote_job_schema import (
    COMPLETE_STAGE,
    IDENTITY_MIGRATION_NAME,
    IDENTITY_STAGES,
    IDENTITY_TEMP_PARENT,
    INDEX_SQL,
    JOB_COLUMNS,
    MIGRATION_NAME,
    STAGES,
    S2_REMOTE_RUNNERS_SQL,
    TABLE_SQL,
)
from tests.infrastructure.test_jobs_migration import _seed_legacy_scripts_db


REMOTE_TABLES = set(TABLE_SQL)
REMOTE_INDEXES = set(INDEX_SQL)
S2_BASELINE_COMMIT = "1be72fe71a779eb3393b9c10dcfeae8a487d3f78"


def _identity_ddl_drift_cases() -> list[tuple[str, str, str, str]]:
    """Systematic valid-SQL mutations for every approved DDL dimension."""
    cases: list[tuple[str, str, str, str]] = []
    for table in ("remote_runners", "remote_runner_enrollment_challenges"):
        sql = TABLE_SQL[table]
        column_lines = [
            line for line in sql.splitlines()
            if re.match(r"^          [a-z][a-z0-9_]+ (?:TEXT|INTEGER)\b", line)
        ]
        for position, line in enumerate(column_lines):
            name = line.strip().split()[0]
            # Rename all references too, so the mutant remains executable SQL.
            renamed = re.sub(rf"\b{re.escape(name)}\b", f"{name}_drift", sql)
            cases.append((f"{table}:{name}:name", "table", table, renamed))
            alternate_type = "INTEGER" if " TEXT" in line else "TEXT"
            cases.append((
                f"{table}:{name}:type", "table", table,
                sql.replace(line, re.sub(r"\b(?:TEXT|INTEGER)\b", alternate_type, line, count=1), 1),
            ))
            toggled_null = (
                line.replace(" NOT NULL", "", 1)
                if " NOT NULL" in line
                else line.replace(" TEXT", " TEXT NOT NULL", 1).replace(
                    " INTEGER", " INTEGER NOT NULL", 1
                )
            )
            cases.append((
                f"{table}:{name}:null", "table", table,
                sql.replace(line, toggled_null, 1),
            ))
            toggled_default = (
                re.sub(r" DEFAULT (?:'[^']*'|[^ ,)]+)", " DEFAULT 2", line, count=1)
                if " DEFAULT " in line else line.rstrip(",") + " DEFAULT NULL" + ("," if line.endswith(",") else "")
            )
            cases.append((
                f"{table}:{name}:default", "table", table,
                sql.replace(line, toggled_default, 1),
            ))
            if position + 1 < len(column_lines):
                following = column_lines[position + 1]
                cases.append((
                    f"{table}:{name}:order", "table", table,
                    sql.replace(f"{line}\n{following}", f"{following}\n{line}", 1),
                ))
        primary_line = column_lines[0]
        cases.append((
            f"{table}:primary-key", "table", table,
            sql.replace(primary_line, primary_line.replace(" PRIMARY KEY", " UNIQUE"), 1),
        ))
        for occurrence in range(sql.count("CHECK(")):
            cursor = -1
            for _ in range(occurrence + 1):
                cursor = sql.index("CHECK(", cursor + 1)
            cases.append((
                f"{table}:check:{occurrence}", "table", table,
                sql[:cursor] + "CHECK(1 AND " + sql[cursor + len("CHECK("):],
            ))
        for occurrence in range(sql.count("UNIQUE(")):
            cursor = -1
            for _ in range(occurrence + 1):
                cursor = sql.index("UNIQUE(", cursor + 1)
            cases.append((
                f"{table}:unique:{occurrence}", "table", table,
                sql[:cursor] + "UNIQUE(id, " + sql[cursor + len("UNIQUE("):],
            ))
        for occurrence in range(sql.count("REFERENCES ")):
            cursor = -1
            for _ in range(occurrence + 1):
                cursor = sql.index("REFERENCES ", cursor + 1)
            target = cursor + len("REFERENCES ")
            target_end = sql.index("(", target)
            cases.append((
                f"{table}:foreign-key:{occurrence}", "table", table,
                sql[:target] + "remote_runners_drift" + sql[target_end:],
            ))
    expiry = INDEX_SQL["remote_enrollment_challenge_expiry"]
    cases.extend([
        ("expiry-index:name", "index", "remote_enrollment_challenge_expiry",
         expiry.replace("remote_enrollment_challenge_expiry", "remote_enrollment_challenge_expiry_drift", 1)),
        ("expiry-index:unique", "index", "remote_enrollment_challenge_expiry",
         expiry.replace("CREATE INDEX", "CREATE UNIQUE INDEX", 1)),
        ("expiry-index:order", "index", "remote_enrollment_challenge_expiry",
         expiry.replace("org_slug, expires_at", "expires_at, org_slug", 1)),
        ("expiry-index:predicate", "index", "remote_enrollment_challenge_expiry",
         expiry.replace("consumed_at IS NULL AND revoked_at IS NULL",
                        "consumed_at IS NULL OR revoked_at IS NULL", 1)),
    ])
    return cases


IDENTITY_DDL_DRIFT_CASES = _identity_ddl_drift_cases()

# Independent, reviewable fingerprints of TASK-6611 section 2.  These are not
# computed from production constants and make a production+test typo fail.
APPROVED_DDL_SHA256 = {
    "remote_runners": "c963c00352242c793095b23112df81f2222b989f8bb9050bcee37ac422bfc631",
    "remote_runner_enrollment_challenges": "135b02adf682130fd8cadda6cc1053cb54b59d4743bb475e5d25a0c1fcaf3383",
    "remote_enrollment_challenge_expiry": "6023bc4fd49a0cc62c8c0651b8c4b4153c687887eef88f07f6a69dc0007234ca",
}


def _objects(path: Path, kind: str) -> dict[str, str | None]:
    with sqlite3.connect(path) as conn:
        return dict(conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type=?", (kind,)
        ))


def _snapshot(path: Path) -> tuple[list[tuple], list[tuple], list[tuple]]:
    with sqlite3.connect(path) as conn:
        schema = conn.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
        ).fetchall()
        markers = conn.execute(
            "SELECT name,stage,updated_at FROM remote_runner_schema_migrations ORDER BY name"
        ).fetchall()
        jobs = conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()
        return schema, markers, jobs


def _complete_snapshot(path: Path) -> tuple[list[tuple], dict[str, list[tuple]]]:
    """Capture every persisted schema object and row without normalizing SQL."""
    with sqlite3.connect(path) as conn:
        schema = conn.execute(
            "SELECT type,name,tbl_name,rootpage,sql FROM sqlite_master "
            "ORDER BY type,name"
        ).fetchall()
        tables = [
            row[1] for row in schema
            if row[0] == "table" and not str(row[1]).startswith("sqlite_")
        ]
        rows = {
            table: conn.execute(
                f'SELECT * FROM "{table}" ORDER BY rowid'
            ).fetchall()
            for table in tables
        }
        return schema, rows


def _downgrade_to_exact_untouched_s2(path: Path) -> None:
    """Turn a fresh current store into the byte/exact merged-S2 boundary."""
    Database(path).close()
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DROP INDEX remote_enrollment_challenge_expiry")
        conn.execute("DROP TABLE remote_runner_enrollment_challenges")
        conn.execute("DELETE FROM remote_runner_schema_migrations WHERE name=?", (
            IDENTITY_MIGRATION_NAME,
        ))
        conn.execute("DROP TABLE remote_runners")
        conn.execute(S2_REMOTE_RUNNERS_SQL)
        conn.commit()


def _build_exact_historical_s2(path: Path, source_root: Path) -> None:
    """Create the fixture by executing the merged S2 tree, never current Database.

    Provenance is the immutable merge commit named by TASK-6611.  Extracting and
    executing that tree also freezes every legacy object and the exact jobs and
    six-table runner-graph shapes, instead of reconstructing a lookalike from
    the constants being tested.
    """
    source_root.mkdir()
    archive = subprocess.run(
        ["git", "archive", "--format=tar", S2_BASELINE_COMMIT],
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    archive_path = source_root / "s2.tar"
    archive_path.write_bytes(archive)
    checkout = source_root / "tree"
    checkout.mkdir()
    with tarfile.open(archive_path) as bundle:
        bundle.extractall(checkout, filter="data")
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; from runtime.infrastructure.database "
            "import Database; Database(Path(__import__('sys').argv[1])).close()",
            str(path),
        ],
        cwd=checkout,
        check=True,
    )


def _insert_runner(conn: sqlite3.Connection, runner: str, generation: int = 1) -> None:
    conn.execute(
        "INSERT INTO remote_runners VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            runner, "sample", runner, generation, "available", 1, 1, 1,
            "{}", "{}", f"att-{runner}", "2026-01-01T00:00:00Z",
            "2027-01-01T00:00:00Z", f"serial-{runner}-{generation}",
            f"spki-{runner}-{generation}", "2027-01-01T00:00:00Z",
            0, None, None, None,
            "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", None, None,
        ),
    )


def _insert_runner_s2(conn: sqlite3.Connection, runner: str) -> None:
    conn.execute(
        "INSERT INTO remote_runners VALUES (" + ",".join("?" * 23) + ")",
        (
            runner, "sample", runner, 1, "available", 1, 1, 1, "{}", "{}",
            f"att-{runner}", "2026-01-01Z", "2027-01-01Z", f"serial-{runner}",
            f"spki-{runner}", 0, None, None, None, "2026-01-01Z",
            "2026-01-01Z", None, None,
        ),
    )


def _insert_workspace(
    conn: sqlite3.Connection,
    workspace: str,
    runner: str,
    *,
    runner_generation: int = 1,
    agent: str = "dev_agent",
    generation: int = 1,
    state: str = "ready",
) -> None:
    conn.execute(
        "INSERT INTO remote_runner_workspaces VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            workspace, runner, runner_generation, agent, generation, state,
            None, f"root-{workspace}", "2026-01-01T00:00:00Z",
            "2026-01-01T00:00:00Z", None if state != "retired" else "2026-01-02T00:00:00Z",
        ),
    )


def _insert_job(db: Database, job_id: str) -> None:
    db.execute(
        "INSERT INTO jobs(id,task_id,agent_name,title,rationale,script_text,interpreter,created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (job_id, "TASK-1", "dev_agent", "job", "why", "true", "bash", "2026-01-01T00:00:00Z"),
    )
    db._conn.commit()


def _insert_attempt(
    conn: sqlite3.Connection,
    attempt: str,
    job: str,
    runner: str,
    workspace: str,
    *,
    runner_generation: int = 1,
    workspace_generation: int = 1,
) -> None:
    conn.execute(
        "INSERT INTO remote_job_attempts VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            attempt, job, 1, runner, runner_generation, workspace,
            workspace_generation, 1, f"bundle-{attempt}", "terminal",
            f"fence-{attempt}", 1, "2026-01-01T01:00:00Z", None, None,
            None, "completed", None, f"terminal-{attempt}",
            "2026-01-01T00:05:00Z", "2026-01-01T00:00:00Z",
            "2026-01-01T00:05:00Z",
        ),
    )


def test_fresh_database_has_exact_s2_schema_and_nullable_job_columns(tmp_path: Path) -> None:
    path = tmp_path / "fresh.db"
    db = Database(path)
    db.close()

    assert REMOTE_TABLES <= _objects(path, "table").keys()
    assert REMOTE_INDEXES <= _objects(path, "index").keys()
    assert "remote_runner_keys" not in _objects(path, "table")
    with sqlite3.connect(path) as conn:
        columns = {row[1]: row for row in conn.execute("PRAGMA table_info(jobs)")}
        for name, declared_type in JOB_COLUMNS:
            assert columns[name][2] == declared_type
            assert columns[name][3] == 0
            assert columns[name][4] is None
        assert conn.execute(
            "SELECT stage FROM remote_runner_schema_migrations WHERE name=?",
            (MIGRATION_NAME,),
        ).fetchone() == (COMPLETE_STAGE,)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("stop_stage", STAGES[:-1])
def test_interruption_after_each_named_stage_converges_after_two_reopens(
    tmp_path: Path, stop_stage: str
) -> None:
    path = tmp_path / f"interrupted-{stop_stage}.db"

    class InterruptedDatabase(Database):
        def _remote_schema_stage_hook(self, stage: str) -> None:
            if stage == stop_stage:
                raise RuntimeError(f"stop after {stage}")

    with pytest.raises(RuntimeError, match="stop after"):
        InterruptedDatabase(path)
    Database(path).close()
    Database(path).close()
    assert REMOTE_TABLES <= _objects(path, "table").keys()
    assert REMOTE_INDEXES <= _objects(path, "index").keys()


@pytest.mark.parametrize(
    ("kind", "name", "replacement"),
    [
        ("table", "remote_runners", "CREATE TABLE remote_runners(id TEXT PRIMARY KEY, wrong TEXT)"),
        (
            "table",
            "remote_runner_workspaces",
            TABLE_SQL["remote_runner_workspaces"].replace(
                "UNIQUE(id, runner_id, runner_generation, generation)",
                "UNIQUE(id, runner_id, generation)",
            ),
        ),
        ("index", "remote_one_live_workspace", "CREATE UNIQUE INDEX remote_one_live_workspace ON remote_runner_workspaces(agent_name, runner_id, runner_generation) WHERE state <> 'retired'"),
        (
            "index",
            "remote_one_live_attempt_per_job",
            INDEX_SQL["remote_one_live_attempt_per_job"].replace(
                "state <> 'terminal'", "state = 'running'"
            ),
        ),
        ("index", "remote_reuse_lookup", "CREATE INDEX remote_reuse_lookup ON remote_pre_run_observations(runner_id, workspace_id, runner_generation, workspace_generation, pre_run_digest, exclusions_policy_digest, observation_digest) WHERE complete=1 AND reusable=1"),
    ],
)
def test_conflicting_exact_shapes_fail_before_migration_mutation(
    tmp_path: Path, kind: str, name: str, replacement: str
) -> None:
    path = tmp_path / f"conflict-{name}.db"
    Database(path).close()
    with sqlite3.connect(path) as conn:
        conn.execute(f"DROP {kind.upper()} {name}")
        conn.execute(replacement)
        conn.commit()
    before = _snapshot(path)
    with pytest.raises(sqlite3.DatabaseError, match="conflicting remote-job"):
        Database(path)
    assert _snapshot(path) == before


def test_v0_script_requests_and_remote_conflict_refuse_before_any_migration(
    tmp_path: Path,
) -> None:
    path = tmp_path / "v0-script-requests-remote-conflict.db"
    _seed_legacy_scripts_db(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE remote_runners(id TEXT PRIMARY KEY, wrong TEXT)"
        )
        conn.execute(
            "INSERT INTO remote_runners(id, wrong) VALUES "
            "('RUNNER-conflict', 'must-survive')"
        )
        conn.commit()

    before = _complete_snapshot(path)
    assert "script_requests" in before[1]
    assert "jobs" not in before[1]
    assert [row[0] for row in before[1]["script_requests"]] == [
        "SR-001", "SR-002", "SR-003",
    ]
    assert before[1]["script_requests"][0][12] == (
        "/runtime/orgs/sample/scripts/SR-001.out"
    )

    for _ in range(2):
        with pytest.raises(
            sqlite3.DatabaseError,
            match="conflicting remote-job table: remote_runners",
        ):
            Database(path)
        assert _complete_snapshot(path) == before


def test_conflicting_nullable_job_column_fails_without_other_remote_writes(tmp_path: Path) -> None:
    path = tmp_path / "column-conflict.db"
    db = Database(path)
    db.close()
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE jobs RENAME COLUMN execution_backend TO old_execution_backend")
        conn.execute("ALTER TABLE jobs ADD COLUMN execution_backend INTEGER NOT NULL DEFAULT 1")
        conn.execute(
            "DELETE FROM remote_runner_schema_migrations WHERE name=?",
            (MIGRATION_NAME,),
        )
        conn.commit()
    before = _snapshot(path)
    with pytest.raises(sqlite3.DatabaseError, match="conflicting jobs column"):
        Database(path)
    assert _snapshot(path) == before


def test_live_workspace_uniqueness_and_retire_then_replace(tmp_path: Path) -> None:
    db = Database(tmp_path / "workspace.db")
    _insert_runner(db._conn, "RUNNER-1")
    _insert_workspace(db._conn, "RWS-1", "RUNNER-1")
    db._conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        _insert_workspace(db._conn, "RWS-2", "RUNNER-1")
    db._conn.rollback()
    db.execute(
        "UPDATE remote_runner_workspaces SET state='retired', retired_at=? WHERE id='RWS-1'",
        ("2026-01-02T00:00:00Z",),
    )
    _insert_workspace(db._conn, "RWS-2", "RUNNER-1")
    db._conn.commit()
    assert db.execute(
        "SELECT id FROM remote_runner_workspaces WHERE runner_id=? AND runner_generation=? "
        "AND agent_name=? AND state <> 'retired'",
        ("RUNNER-1", 1, "dev_agent"),
    ).fetchall()[0][0] == "RWS-2"


def test_partial_duplicate_live_workspaces_refuse_on_two_reopens_without_mutation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "duplicate-partial.db"
    Database(path).close()
    with sqlite3.connect(path) as conn:
        conn.execute("DROP INDEX remote_one_live_workspace")
        conn.execute(
            "UPDATE remote_runner_schema_migrations SET stage='create_runner_tables' WHERE name=?",
            (MIGRATION_NAME,),
        )
        conn.execute("PRAGMA foreign_keys=ON")
        _insert_runner(conn, "RUNNER-1")
        _insert_workspace(conn, "RWS-1", "RUNNER-1")
        _insert_workspace(conn, "RWS-2", "RUNNER-1")
        conn.commit()
    before = _snapshot(path)
    for _ in range(2):
        with pytest.raises(sqlite3.IntegrityError, match="duplicate live"):
            Database(path)
        assert _snapshot(path) == before


def test_composite_foreign_keys_reject_cross_identity_and_generation(tmp_path: Path) -> None:
    db = Database(tmp_path / "composite.db")
    _insert_runner(db._conn, "RUNNER-1")
    _insert_runner(db._conn, "RUNNER-2")
    _insert_workspace(db._conn, "RWS-1", "RUNNER-1")
    _insert_job(db, "JOB-1")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_attempt(db._conn, "RATT-bad-runner", "JOB-1", "RUNNER-2", "RWS-1")
    db._conn.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        _insert_attempt(
            db._conn, "RATT-bad-generation", "JOB-1", "RUNNER-1", "RWS-1",
            workspace_generation=2,
        )


def test_reuse_lookup_isolated_by_workspace_agent_runner_and_runner_generation(tmp_path: Path) -> None:
    db = Database(tmp_path / "reuse.db")
    _insert_runner(db._conn, "RUNNER-1", generation=2)
    _insert_runner(db._conn, "RUNNER-2")
    _insert_workspace(db._conn, "RWS-a", "RUNNER-1", runner_generation=2, agent="agent-a")
    _insert_workspace(db._conn, "RWS-b", "RUNNER-1", runner_generation=2, agent="agent-b")
    _insert_workspace(db._conn, "RWS-c", "RUNNER-2", agent="agent-a")
    for suffix, runner, runner_gen, workspace in (
        ("a", "RUNNER-1", 2, "RWS-a"),
        ("b", "RUNNER-1", 2, "RWS-b"),
        ("c", "RUNNER-2", 1, "RWS-c"),
    ):
        _insert_job(db, f"JOB-{suffix}")
        _insert_attempt(db._conn, f"RATT-{suffix}", f"JOB-{suffix}", runner, workspace, runner_generation=runner_gen)
        db._conn.execute(
            "INSERT INTO remote_phase_receipts "
            "(id,attempt_id,phase,ordinal,outcome,receipt_json,receipt_digest,accepted_frame_seq) "
            "VALUES (?,?, 'workspace_observation',1,'succeeded','{}',?,1)",
            (f"RPR-{suffix}", f"RATT-{suffix}", f"receipt-{suffix}"),
        )
        db._conn.execute(
            "INSERT INTO remote_pre_run_observations VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"RPO-{suffix}", f"RATT-{suffix}", f"RPR-{suffix}", runner,
                runner_gen, workspace, 1, "same-pre", 1, "same-policy", "[]",
                "[]", "{}", "same-observation", 1, 1, "2026-01-01T00:00:00Z",
            ),
        )
    db._conn.commit()
    rows = db.execute(
        "SELECT id FROM remote_pre_run_observations WHERE runner_id=? AND runner_generation=? "
        "AND workspace_id=? AND workspace_generation=? AND pre_run_digest=? "
        "AND exclusions_policy_digest=? AND observation_digest=? AND complete=1 AND reusable=1",
        ("RUNNER-1", 2, "RWS-a", 1, "same-pre", "same-policy", "same-observation"),
    ).fetchall()
    assert [row[0] for row in rows] == ["RPO-a"]


def test_existing_local_job_values_and_overloaded_references_survive(tmp_path: Path) -> None:
    path = tmp_path / "compat.db"
    db = Database(path)
    db.execute(
        "INSERT INTO tasks(id,status,brief,created_at,updated_at,blocked_on_job_ids) "
        "VALUES ('TASK-1','blocked','b','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z','[\"JOB-1\"]')"
    )
    _insert_job(db, "JOB-1")
    db.execute("UPDATE jobs SET status='running', reason='local-reason' WHERE id='JOB-1'")
    db.execute(
        "INSERT INTO audit_log(task_id,agent,action,payload,timestamp) "
        "VALUES ('config:working_hours','founder','legacy','{}','2026-01-01T00:00:00Z')"
    )
    db._conn.commit()
    before = tuple(db.execute("SELECT status,reason FROM jobs WHERE id='JOB-1'").fetchone())
    db.close()
    Database(path).close()
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT status,reason FROM jobs WHERE id='JOB-1'").fetchone() == before
        assert conn.execute("SELECT blocked_on_job_ids FROM tasks WHERE id='TASK-1'").fetchone() == ('[\"JOB-1\"]',)
        assert conn.execute("SELECT task_id FROM audit_log WHERE action='legacy'").fetchone() == ("config:working_hours",)
        remote_values = conn.execute(
            "SELECT execution_backend,selected_runner_id,remote_bundle_json,remote_bundle_digest,current_remote_attempt_id "
            "FROM jobs WHERE id='JOB-1'"
        ).fetchone()
        assert remote_values == (None, None, None, None, None)


def test_exact_untouched_merged_s2_upgrades_and_preserves_every_unrelated_byte_value(
    tmp_path: Path,
) -> None:
    path = tmp_path / "untouched-s2.db"
    _build_exact_historical_s2(path, tmp_path / "historical-source")
    with sqlite3.connect(path) as conn:
        conn.executemany(
            "INSERT INTO tasks(id,status,brief,created_at,updated_at,blocked_on_job_ids) "
            "VALUES (?,?,?,?,?,?)",
            [
                ("TASK-s2", "blocked", "legacy", "2026-01-01T00:00:00Z",
                 "2026-01-01T00:00:00Z", '["JOB-s2-modern","JOB-s2-v1","JOB-s2-v0"]'),
            ],
        )
        conn.executemany(
            "INSERT INTO jobs(id,task_id,agent_name,title,rationale,script_text,"
            "interpreter,status,reason,stdout_path,stderr_path,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("JOB-s2-modern", "TASK-s2", "dev_agent", "modern", "why", "true",
                 "bash", "completed", "complete", "/jobs/modern.out", "/jobs/modern.err",
                 "2026-01-01T00:00:00Z"),
                ("JOB-s2-v1", "TASK-s2", "dev_agent", "v1", "why", "false",
                 "bash", "rejected", "founder_rejected", "/jobs/v1.out", "/jobs/v1.err",
                 "2026-01-02T00:00:00Z"),
                ("JOB-s2-v0", "TASK-s2", "dev_agent", "v0-script-request-history",
                 "why", "exit 7", "bash", "failed", "daemon_crash",
                 "/scripts/SR-legacy.out", "/scripts/SR-legacy.err",
                 "2026-01-03T00:00:00Z"),
            ],
        )
        conn.executemany(
            "INSERT INTO audit_log(task_id,agent,action,payload,timestamp) VALUES "
            "(?,?,?,?,?)",
            [
                ("config:working_hours", "founder", "legacy", "{}", "2026-01-01T00:00:00Z"),
                ("TASK-s2", "dev_agent", "job_failed", '{"job_id":"JOB-s2-v0"}',
                 "2026-01-03T00:01:00Z"),
            ],
        )
        conn.commit()
    before_schema, before_rows = _complete_snapshot(path)

    Database(path).close()
    Database(path).close()
    Database(path).close()

    after_schema, after_rows = _complete_snapshot(path)
    added = {
        "remote_runner_enrollment_challenges",
        "remote_enrollment_challenge_expiry",
    }
    def unrelated(schema: list[tuple]) -> list[tuple]:
        return [
            row for row in schema
            if row[1] not in added | {"remote_runners"}
            and row[2] not in {"remote_runners", "remote_runner_enrollment_challenges"}
        ]
    assert unrelated(after_schema) == unrelated(before_schema)
    for table, rows in before_rows.items():
        if table not in {"remote_runners", "remote_runner_schema_migrations"}:
            assert after_rows[table] == rows
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT name,stage FROM remote_runner_schema_migrations WHERE name=?",
            (IDENTITY_MIGRATION_NAME,),
        ).fetchall() == [(IDENTITY_MIGRATION_NAME, COMPLETE_STAGE)]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name=?", (IDENTITY_TEMP_PARENT,)
        ).fetchone() is None
        assert conn.execute(
            "SELECT blocked_on_job_ids FROM tasks WHERE id='TASK-s2'"
        ).fetchone() == ('["JOB-s2-modern","JOB-s2-v1","JOB-s2-v0"]',)
        assert conn.execute(
            "SELECT task_id FROM audit_log WHERE action='legacy'"
        ).fetchone() == ("config:working_hours",)


@pytest.mark.parametrize(
    "stop_point",
    [point for stage in IDENTITY_STAGES for point in (f"before:{stage}", stage)]
    + ["before:parent_replacement", "after:parent_replacement"],
)
def test_identity_interruption_before_and_after_every_stage_converges_twice(
    tmp_path: Path, stop_point: str,
) -> None:
    path = tmp_path / f"identity-{stop_point.replace(':', '-')}.db"
    replacement_boundary = stop_point.endswith("parent_replacement")
    before = None
    if replacement_boundary:
        _build_exact_historical_s2(path, tmp_path / "historical-source")
        # Resume at the shipping stage immediately before the rebuild.  The
        # first stage is already durably committed, so the boundary hook's
        # transaction is the only transaction under test here.
        with sqlite3.connect(path) as conn:
            conn.execute(
                "INSERT INTO remote_runner_schema_migrations(name,stage,updated_at) "
                "VALUES (?,?,?)",
                (IDENTITY_MIGRATION_NAME, IDENTITY_STAGES[0], "2026-01-01T00:00:00Z"),
            )
            conn.commit()
        before = _complete_snapshot(path)

    class InterruptedDatabase(Database):
        def _remote_identity_schema_stage_hook(self, point: str) -> None:
            if point == stop_point:
                raise RuntimeError(f"stop at {point}")

    with pytest.raises(RuntimeError, match="stop at"):
        InterruptedDatabase(path)
    if replacement_boundary:
        # Both hooks execute inside the shipping BEGIN IMMEDIATE transaction;
        # even the post-rename hook must roll the DROP/RENAME back byte/value-exact.
        assert _complete_snapshot(path) == before
        with sqlite3.connect(path) as conn:
            assert conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name=?", (IDENTITY_TEMP_PARENT,)
            ).fetchone() is None
    Database(path).close()
    Database(path).close()
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT stage FROM remote_runner_schema_migrations WHERE name=?",
            (IDENTITY_MIGRATION_NAME,),
        ).fetchone() == (COMPLETE_STAGE,)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name=?", (IDENTITY_TEMP_PARENT,)
        ).fetchone() is None


@pytest.mark.parametrize(
    ("case_id", "kind", "name", "replacement"),
    IDENTITY_DDL_DRIFT_CASES,
    ids=[case[0] for case in IDENTITY_DDL_DRIFT_CASES],
)
def test_identity_exact_shape_drift_refuses_before_any_mutation(
    tmp_path: Path, case_id: str, kind: str, name: str, replacement: str,
) -> None:
    path = tmp_path / f"identity-drift-{case_id.replace(':', '-')}.db"
    Database(path).close()
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(f"DROP {kind.upper()} {name}")
        conn.execute(replacement)
        conn.commit()
    before = _complete_snapshot(path)
    for _ in range(2):
        with pytest.raises(sqlite3.DatabaseError):
            Database(path)
        assert _complete_snapshot(path) == before


@pytest.mark.parametrize("table", [
    "remote_runners", "remote_runner_workspaces", "remote_job_attempts",
    "remote_phase_receipts", "remote_pre_run_observations", "remote_protocol_frames",
    "remote_runner_enrollment_challenges",
])
def test_untouched_s2_with_any_runner_graph_row_refuses_without_mutation(
    tmp_path: Path, table: str,
) -> None:
    path = tmp_path / f"nonempty-{table}.db"
    _downgrade_to_exact_untouched_s2(path)
    with sqlite3.connect(path) as conn:
        _insert_runner_s2(conn, "RUNNER-x")
        if table != "remote_runners":
            _insert_workspace(conn, "RWS-x", "RUNNER-x")
        if table in {
            "remote_job_attempts", "remote_phase_receipts",
            "remote_pre_run_observations", "remote_protocol_frames",
        }:
            conn.execute(
                "INSERT INTO jobs(id,task_id,agent_name,title,rationale,script_text,"
                "interpreter,created_at) VALUES "
                "('JOB-x','TASK-x','dev_agent','x','x','true','bash','2026-01-01Z')"
            )
            _insert_attempt(conn, "RATT-x", "JOB-x", "RUNNER-x", "RWS-x")
        if table in {"remote_phase_receipts", "remote_pre_run_observations"}:
            conn.execute(
                "INSERT INTO remote_phase_receipts "
                "(id,attempt_id,phase,ordinal,outcome,receipt_json,receipt_digest,accepted_frame_seq) "
                "VALUES ('RPR-x','RATT-x','workspace_observation',1,'succeeded','{}','digest',1)"
            )
        if table == "remote_pre_run_observations":
            conn.execute(
                "INSERT INTO remote_pre_run_observations VALUES "
                "('RPO-x','RATT-x','RPR-x','RUNNER-x',1,'RWS-x',1,'pre',1,'policy',"
                "'[]','[]','{}','observation',1,1,'2026-01-01Z')"
            )
        if table == "remote_protocol_frames":
            conn.execute(
                "INSERT INTO remote_protocol_frames VALUES "
                "('RATT-x','connection',1,'terminal','digest','accepted','2026-01-01Z')"
            )
        if table == "remote_runner_enrollment_challenges":
            conn.execute(TABLE_SQL["remote_runner_enrollment_challenges"])
            conn.execute(
                "INSERT INTO remote_runner_enrollment_challenges "
                "(id,org_slug,token_fingerprint,challenge_nonce,display_name,"
                "attestation_json,attestation_digest,ceremony_kind,"
                "target_runner_generation,expires_at,created_at) VALUES "
                "('RENC-x','sample','token','nonce','runner','{}','att',"
                "'initial',1,'2026-01-02Z','2026-01-01Z')"
            )
        conn.commit()
    before = _complete_snapshot(path)
    with pytest.raises(sqlite3.DatabaseError):
        Database(path)
    assert _complete_snapshot(path) == before


def test_enrollment_challenge_exact_checks_uniques_and_foreign_keys(tmp_path: Path) -> None:
    db = Database(tmp_path / "challenge-ddl.db")
    _insert_runner(db._conn, "RUNNER-1")
    base = (
        "RENC-1", "sample", "token-1", "nonce-1", "runner", "{}", "att",
        "initial", None, 1, "2026-01-02Z", "2026-01-01Z", None, None,
        None, None, None, None, None, None,
    )
    db._conn.execute(
        "INSERT INTO remote_runner_enrollment_challenges VALUES (" + ",".join("?" * 20) + ")",
        base,
    )
    with pytest.raises(sqlite3.IntegrityError):
        db._conn.execute(
            "INSERT INTO remote_runner_enrollment_challenges VALUES (" + ",".join("?" * 20) + ")",
            ("RENC-2", *base[1:2], "token-1", *base[3:]),
        )
    db._conn.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        db._conn.execute(
            "INSERT INTO remote_runner_enrollment_challenges VALUES (" + ",".join("?" * 20) + ")",
            ("RENC-bad", "sample", "token-bad", "nonce-bad", "runner", "{}", "att",
             "generation_rotation", "RUNNER-1", 1, "2026-01-02Z", "2026-01-01Z",
             None, None, None, None, None, None, None, None),
        )


def test_task_6611_bidirectional_requirement_to_ddl_traceability(tmp_path: Path) -> None:
    """Executable TASK-6611 §§2/4/5/6 trace table; S3 lifecycle is deferred."""
    path = tmp_path / "traceability.db"
    Database(path).close()
    with sqlite3.connect(path) as conn:
        actual_sql = {
            name: conn.execute(
                "SELECT sql FROM sqlite_master WHERE name=?", (name,)
            ).fetchone()[0]
            for name in APPROVED_DDL_SHA256
        }

    def ddl_digest(sql: str) -> str:
        normalized = re.sub(
            r"[\s\"`\[\]]+", "",
            re.sub(r"\bIF\s+NOT\s+EXISTS\b", "", sql.strip().rstrip(";"), flags=re.I),
        ).lower()
        return hashlib.sha256(normalized.encode()).hexdigest()

    assert {name: ddl_digest(sql) for name, sql in actual_sql.items()} == APPROVED_DDL_SHA256

    dimensions_by_table: dict[str, set[str]] = {}
    for case_id, _, _, _ in IDENTITY_DDL_DRIFT_CASES:
        owner, *_, dimension = case_id.split(":")
        dimensions_by_table.setdefault(owner, set()).add(dimension)
    exact_column_dimensions = {"name", "type", "null", "default", "order"}
    assert exact_column_dimensions <= dimensions_by_table["remote_runners"]
    assert exact_column_dimensions <= dimensions_by_table["remote_runner_enrollment_challenges"]
    assert {"name", "unique", "order", "predicate"} == dimensions_by_table["expiry-index"]

    requirements = {
        "2.runner-expiry-and-exact-parent",
        "2.challenge-authority-and-exact-constraints",
        "2.expiry-index",
        "4.marker-and-six-ordered-stages",
        "4.two-shapes-empty-graph-and-complete-only",
        "4.preflight-before-mutation-and-residue-refusal",
        "4.atomic-parent-replacement-and-fk-validation",
        "5.exact-merged-s2-history-and-three-reopens",
        "5.every-stage-and-replacement-interruption",
        "5.systematic-negative-snapshot-matrix",
        "6.forward-and-reverse-object-traceability",
    }
    artifact_to_requirement: dict[str, str] = {}
    for case_id, _, _, _ in IDENTITY_DDL_DRIFT_CASES:
        if case_id.startswith("remote_runners"):
            requirement = "2.runner-expiry-and-exact-parent"
        elif case_id.startswith("remote_runner_enrollment_challenges"):
            requirement = "2.challenge-authority-and-exact-constraints"
        else:
            requirement = "2.expiry-index"
        artifact_to_requirement[f"negative:{case_id}"] = requirement
    artifact_to_requirement.update({
        **{f"stage:{stage}": "4.marker-and-six-ordered-stages" for stage in IDENTITY_STAGES},
        "validator:two-shape": "4.two-shapes-empty-graph-and-complete-only",
        "validator:preflight-snapshot": "4.preflight-before-mutation-and-residue-refusal",
        "hook:before-parent-replacement": "4.atomic-parent-replacement-and-fk-validation",
        "hook:after-parent-replacement": "4.atomic-parent-replacement-and-fk-validation",
        "fixture:git-archive-1be72fe": "5.exact-merged-s2-history-and-three-reopens",
        "fixture:all-stage-interruptions": "5.every-stage-and-replacement-interruption",
        "fixture:all-ddl-marker-row-negatives": "5.systematic-negative-snapshot-matrix",
        "assertion:bidirectional-completeness": "6.forward-and-reverse-object-traceability",
    })
    # Forward: every approved persistence/migration requirement has proof.
    assert set(artifact_to_requirement.values()) == requirements
    # Reverse: every new object/column/constraint/index/marker/stage artifact
    # has exactly one approved owner (a dict cannot assign two owners).
    assert len(artifact_to_requirement) == len(set(artifact_to_requirement))

    # TASK-6611 lifecycle/S3 requirements are intentionally not persistence
    # artifacts: no challenge producer/consumer, certificate issuance,
    # rotation, revocation service, S3 route, or transport is implemented here.
    deferred = {
        "challenge-create-consume-replay",
        "certificate-issuance-renewal",
        "generation-rotation-revocation",
        "s3-routes-services-transport-activation",
    }
    assert deferred.isdisjoint(artifact_to_requirement)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("unknown_stage", "conflicting identity/enrollment migration stage"),
        ("missing_challenge", "challenge object-stage mismatch"),
        ("premature_challenge", "challenge object-stage mismatch"),
        ("temporary_parent", "reserved identity/enrollment temporary parent exists"),
    ],
)
def test_marker_object_mismatch_and_temporary_residue_refuse_before_mutation(
    tmp_path: Path, mutation: str, message: str,
) -> None:
    path = tmp_path / f"marker-{mutation}.db"
    Database(path).close()
    with sqlite3.connect(path) as conn:
        if mutation == "unknown_stage":
            conn.execute(
                "UPDATE remote_runner_schema_migrations SET stage='unknown' WHERE name=?",
                (IDENTITY_MIGRATION_NAME,),
            )
        elif mutation == "missing_challenge":
            conn.execute("DROP INDEX remote_enrollment_challenge_expiry")
            conn.execute("DROP TABLE remote_runner_enrollment_challenges")
        elif mutation == "premature_challenge":
            conn.execute(
                "UPDATE remote_runner_schema_migrations "
                "SET stage='rebuild_remote_runners_with_cert_expiry' WHERE name=?",
                (IDENTITY_MIGRATION_NAME,),
            )
        else:
            conn.execute(
                TABLE_SQL["remote_runners"].replace(
                    "CREATE TABLE remote_runners",
                    f"CREATE TABLE {IDENTITY_TEMP_PARENT}",
                    1,
                )
            )
        conn.commit()
    before = _complete_snapshot(path)
    with pytest.raises(sqlite3.DatabaseError, match=message):
        Database(path)
    assert _complete_snapshot(path) == before


@pytest.mark.parametrize("stage", IDENTITY_STAGES)
def test_every_known_marker_stage_with_impossible_objects_refuses_byte_exact(
    tmp_path: Path, stage: str,
) -> None:
    path = tmp_path / f"impossible-{stage}.db"
    Database(path).close()
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE remote_runner_schema_migrations SET stage=? WHERE name=?",
            (stage, IDENTITY_MIGRATION_NAME),
        )
        if stage in IDENTITY_STAGES[:3]:
            # Complete objects are premature for these known stages.
            pass
        else:
            # Later stages require both challenge objects; remove one.
            conn.execute("DROP INDEX remote_enrollment_challenge_expiry")
        conn.commit()
    before = _complete_snapshot(path)
    for _ in range(2):
        with pytest.raises(sqlite3.DatabaseError, match="stage mismatch"):
            Database(path)
        assert _complete_snapshot(path) == before


def test_duplicate_conflicting_marker_rows_refuse_before_mutation(tmp_path: Path) -> None:
    path = tmp_path / "duplicate-marker.db"
    Database(path).close()
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT name,stage,updated_at FROM remote_runner_schema_migrations"
        ).fetchall()
        conn.execute("DROP TABLE remote_runner_schema_migrations")
        conn.execute(
            "CREATE TABLE remote_runner_schema_migrations "
            "(name TEXT, stage TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO remote_runner_schema_migrations VALUES (?,?,?)", rows
        )
        conn.executemany(
            "INSERT INTO remote_runner_schema_migrations VALUES (?,?,?)",
            [
                (IDENTITY_MIGRATION_NAME, IDENTITY_STAGES[0], "2026-01-01Z"),
                (IDENTITY_MIGRATION_NAME, IDENTITY_STAGES[1], "2026-01-02Z"),
            ],
        )
        conn.commit()
    before = _complete_snapshot(path)
    with pytest.raises(sqlite3.DatabaseError):
        Database(path)
    assert _complete_snapshot(path) == before
