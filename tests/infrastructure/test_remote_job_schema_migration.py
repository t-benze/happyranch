from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from runtime.infrastructure.database import Database
from runtime.infrastructure.remote_job_schema import (
    COMPLETE_STAGE,
    INDEX_SQL,
    JOB_COLUMNS,
    MIGRATION_NAME,
    STAGES,
    TABLE_SQL,
)


REMOTE_TABLES = set(TABLE_SQL)
REMOTE_INDEXES = set(INDEX_SQL)


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


def _insert_runner(conn: sqlite3.Connection, runner: str, generation: int = 1) -> None:
    conn.execute(
        "INSERT INTO remote_runners VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            runner, "sample", runner, generation, "available", 1, 1, 1,
            "{}", "{}", f"att-{runner}", "2026-01-01T00:00:00Z",
            "2027-01-01T00:00:00Z", f"serial-{runner}-{generation}",
            f"spki-{runner}-{generation}", 0, None, None, None,
            "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", None, None,
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


def test_conflicting_nullable_job_column_fails_without_other_remote_writes(tmp_path: Path) -> None:
    path = tmp_path / "column-conflict.db"
    db = Database(path)
    db.close()
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE jobs RENAME COLUMN execution_backend TO old_execution_backend")
        conn.execute("ALTER TABLE jobs ADD COLUMN execution_backend INTEGER NOT NULL DEFAULT 1")
        conn.execute("DELETE FROM remote_runner_schema_migrations")
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
