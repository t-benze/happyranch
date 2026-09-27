from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from runtime.infrastructure.database import Database
from runtime.infrastructure.workflow_schema import (
    CANONICAL_WORKFLOW_DDL,
    WorkflowCompatibilityStore,
    install_or_recover,
)


FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "workflow_u0"
    / "proposed_workflow_schema.sql"
)


def _workflow_snapshot(path: Path) -> tuple[object, ...]:
    conn = sqlite3.connect(path)
    try:
        objects = tuple(
            conn.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_schema "
                "WHERE type IN ('table','index','trigger') "
                "AND (name LIKE 'workflow_%' OR tbl_name LIKE 'workflow_%') "
                "ORDER BY type,name,tbl_name"
            )
        )
        rows: list[tuple[str, tuple[tuple[object, ...], ...]]] = []
        tables = tuple(
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' "
                "AND name LIKE 'workflow_%' ORDER BY name"
            )
        )
        for table in tables:
            quoted = table.replace('"', '""')
            rows.append((table, tuple(conn.execute(f'SELECT * FROM "{quoted}"'))))
        return objects, tuple(rows)
    finally:
        conn.close()


def _materialize_layout(path: Path, ddl: str) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(ddl)
        conn.execute("INSERT INTO workflow_adapter_versions VALUES (1)")
        conn.execute(
            "INSERT INTO workflow_cutover_state VALUES "
            "(1,1,'installed_legacy_only','workflow_cutover_reconciler',"
            "1,NULL,NULL,'2026-09-24T00:00:00+00:00')"
        )
        conn.commit()
    finally:
        conn.close()


def test_production_ddl_is_byte_identical_to_accepted_fixture() -> None:
    assert CANONICAL_WORKFLOW_DDL == FIXTURE.read_text()


def test_install_is_complete_and_records_only_initial_legacy_state(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "org.db")
    try:
        assert install_or_recover(db) == "installed_legacy_only"
        tables = db.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' "
            "AND name LIKE 'workflow_%' ORDER BY name"
        ).fetchall()
        assert len(tables) == 44
        assert [tuple(row) for row in db.execute(
            "SELECT version FROM workflow_adapter_versions"
        ).fetchall()] == [(1,)]
        assert [tuple(row) for row in db.execute(
            "SELECT schema_version,state,recovery_owner,generation,"
            "operation_key,disable_reason FROM workflow_cutover_state"
        ).fetchall()] == [
            (1, "installed_legacy_only", "workflow_cutover_reconciler", 1, None, None)
        ]
        event = [tuple(row) for row in db.execute(
            "SELECT event_seq,state_before,state_after,operation_key "
            "FROM workflow_cutover_events"
        ).fetchall()]
        assert event == [(1, None, "installed_legacy_only", None)]
    finally:
        db.close()


def test_repeated_install_and_two_cold_reopens_are_idempotent(
    tmp_path: Path,
) -> None:
    path = tmp_path / "org.db"
    first = Database(path)
    assert WorkflowCompatibilityStore(first).install_or_recover() == (
        "installed_legacy_only"
    )
    first.close()
    expected = _workflow_snapshot(path)

    for _ in range(2):
        reopened = Database(path)
        assert WorkflowCompatibilityStore(reopened).install_or_recover() == "reopened"
        reopened.close()
        assert _workflow_snapshot(path) == expected


def test_precommit_interruption_leaves_zero_workflow_residue(
    tmp_path: Path,
) -> None:
    path = tmp_path / "org.db"
    db = Database(path)

    def interrupt() -> None:
        raise RuntimeError("interrupt-before-workflow-commit")

    with pytest.raises(RuntimeError, match="interrupt-before-workflow-commit"):
        install_or_recover(db, before_commit=interrupt)
    db.close()
    assert _workflow_snapshot(path) == ((), ())

    reopened = Database(path)
    assert install_or_recover(reopened) == "installed_legacy_only"
    reopened.close()


def test_install_is_invisible_before_commit_and_complete_after_return(
    tmp_path: Path,
) -> None:
    path = tmp_path / "org.db"
    db = Database(path)
    observations: list[tuple[str, ...]] = []

    def observe_before_commit() -> None:
        reader = sqlite3.connect(path)
        try:
            observations.append(
                tuple(
                    row[0]
                    for row in reader.execute(
                        "SELECT name FROM sqlite_schema WHERE type='table' "
                        "AND name LIKE 'workflow_%' ORDER BY name"
                    )
                )
            )
        finally:
            reader.close()

    assert install_or_recover(db, before_commit=observe_before_commit) == (
        "installed_legacy_only"
    )
    db.close()
    assert observations == [()]
    assert len(_workflow_snapshot(path)[1]) == 44


def test_transaction_begins_before_schema_observation_and_refuses_caller_txn(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "trace.db")
    trace: list[str] = []
    db._conn.set_trace_callback(trace.append)
    install_or_recover(db)
    first_observation = next(
        index for index, statement in enumerate(trace) if "sqlite_schema" in statement
    )
    assert trace.index("BEGIN IMMEDIATE") < first_observation
    db._conn.set_trace_callback(None)
    db.close()

    caller = Database(tmp_path / "caller.db")
    caller.execute("BEGIN")
    with pytest.raises(
        ValueError,
        match="workflow_schema_caller_transaction_not_allowed",
    ):
        install_or_recover(caller)
    caller.execute("ROLLBACK")
    caller.close()
    assert _workflow_snapshot(tmp_path / "caller.db") == ((), ())


@pytest.mark.parametrize(
    "malform",
    [
        pytest.param(
            lambda ddl: ddl.replace(
                "workflow_adapter_versions (version INTEGER PRIMARY KEY",
                "workflow_adapter_versions (version TEXT PRIMARY KEY",
            ),
            id="column-type",
        ),
        pytest.param(
            lambda ddl: ddl.replace(
                "CHECK(version=1)",
                "CHECK(version IN (1,2))",
                1,
            ),
            id="full-name-set-wrong-check",
        ),
        pytest.param(
            lambda ddl: ddl.replace(
                "CREATE INDEX workflow_requests_round_idx",
                "-- removed CREATE INDEX workflow_requests_round_idx",
            ),
            id="missing-index",
        ),
        pytest.param(
            lambda ddl: ddl
            + "\nCREATE TRIGGER workflow_unexpected_trigger "
            "AFTER INSERT ON workflow_adapter_versions BEGIN SELECT 1; END;\n",
            id="extra-trigger",
        ),
    ],
)
def test_full_name_set_malformed_layout_fails_closed_without_writes(
    tmp_path: Path,
    malform: Callable[[str], str],
) -> None:
    path = tmp_path / "malformed.db"
    _materialize_layout(path, malform(CANONICAL_WORKFLOW_DDL))
    db = Database(path)
    before = _workflow_snapshot(path)
    with pytest.raises(
        ValueError,
        match="workflow_schema_(?:object_set|layout)_mismatch",
    ):
        install_or_recover(db)
    db.close()
    assert _workflow_snapshot(path) == before


@pytest.mark.parametrize(
    ("corruption", "error"),
    [
        (
            "UPDATE workflow_adapter_versions SET version=2",
            "unsupported_workflow_adapter_version",
        ),
        (
            "UPDATE workflow_cutover_state SET recovery_owner='other-owner'",
            "workflow_schema_marker_mismatch",
        ),
        (
            "UPDATE workflow_cutover_state SET state='enabled'",
            "workflow_schema_marker_mismatch",
        ),
    ],
)
def test_unknown_version_owner_or_noninitial_state_fails_without_writes(
    tmp_path: Path,
    corruption: str,
    error: str,
) -> None:
    path = tmp_path / "corrupt.db"
    db = Database(path)
    install_or_recover(db)
    db.close()

    corrupt = sqlite3.connect(path)
    corrupt.execute("PRAGMA ignore_check_constraints=ON")
    corrupt.execute(corruption)
    corrupt.commit()
    corrupt.close()

    before = _workflow_snapshot(path)
    reopened = Database(path)
    with pytest.raises(ValueError, match=error):
        install_or_recover(reopened)
    reopened.close()
    assert _workflow_snapshot(path) == before


def test_partial_or_extra_workflow_layout_is_never_adopted(
    tmp_path: Path,
) -> None:
    path = tmp_path / "partial.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE workflow_cutover_state(foreign_marker TEXT)")
    conn.execute("INSERT INTO workflow_cutover_state VALUES ('preserve-me')")
    conn.commit()
    conn.close()

    db = Database(path)
    before = _workflow_snapshot(path)
    with pytest.raises(ValueError, match="workflow_schema_object_set_mismatch"):
        install_or_recover(db)
    db.close()
    assert _workflow_snapshot(path) == before


def test_generic_runtime_audit_database_is_not_implicitly_installed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.daemon.routes.adapters import _audit_adapter_bind
    from runtime.daemon.routes.executors import _audit_runtime_removal

    daemon_home = tmp_path / "daemon-home"
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home))
    _audit_runtime_removal(
        profile_name="example",
        command_adapter_id="custom-adapter:example",
    )
    _audit_adapter_bind(
        profile_name="bound-example",
        adapter_id="example",
        workspace_adapter="codex",
    )
    path = daemon_home / "runtime-audit.db"
    audit = Database(path)
    rows = audit.execute(
        "SELECT task_id,action FROM audit_log ORDER BY id"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("executor:example", "executor_removed"),
        ("executor:bound-example", "executor_registered"),
    ]
    audit.close()
    before = path.read_bytes()
    assert _workflow_snapshot(path) == ((), ())

    # Creating/installing an unrelated org store has no path, filename, or
    # constructor side effect on the machine-global audit store.
    org = Database(tmp_path / "happyranch.db")
    install_or_recover(org)
    org.close()
    assert path.read_bytes() == before
    assert _workflow_snapshot(path) == ((), ())
