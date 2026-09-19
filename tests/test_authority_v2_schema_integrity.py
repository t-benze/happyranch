"""THR-229 checkpoint C3a — v2 constraint-sensitive schema-integrity seam tests.

These tests drive the ACTUAL production seam
(``capture_authority_policy_v2_schema_integrity`` /
``recheck_authority_policy_v2_schema_integrity``) against pristine disposable
schema models and the checked-in full historical fixture.  They never call the
fixture generator or re-implement the comparison oracle.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from runtime.infrastructure.database import Database
from runtime.orchestrator import authority
from runtime.orchestrator.authority import (
    V2_SCHEMA_INTEGRITY_CONTRACT,
    capture_authority_policy_v2_schema_integrity,
    recheck_authority_policy_v2_schema_integrity,
)
from tests.authority_v2_historical_schema import (
    historical_inventory_digest,
    load_historical_fixture,
    reconstruct_historical_database,
)

# Tables that a read-only integrity helper must never populate.  ``tasks`` and
# ``escalation_notifications`` guard against an invented task/notification
# admission; the authority tables guard against candidate/attempt/envelope
# evidence being manufactured by the helper.
_NO_INVENTION_TABLES = (
    "authority_candidates",
    "authority_evaluations",
    "authority_continue_envelopes",
    "authority_policy_v2_attempts",
    "escalation_notifications",
    "tasks",
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _pristine(tmp_path: Path, name: str = "model.db") -> Database:
    return Database(tmp_path / name)


def _capture(db: Database):
    return capture_authority_policy_v2_schema_integrity(db)


def _row_counts(db: Database, tables=_NO_INVENTION_TABLES) -> dict:
    counts = {}
    for table in tables:
        counts[table] = db._conn.execute(
            f'SELECT COUNT(*) FROM "{table}"'
        ).fetchone()[0]
    return counts


def _seed_preexisting_row(db: Database) -> None:
    db._conn.execute(
        "INSERT INTO processed_event_ids"
        "(org_slug, feishu_event_id, processed_at, outcome) "
        "VALUES ('seam-probe', 'evt-1', '2026-01-01T00:00:00Z', 'ok')"
    )
    db._conn.commit()


def _table_sql(db: Database, table: str) -> str:
    row = db._conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    assert row is not None and row[0]
    return row[0]


def _index_sql(db: Database, index: str) -> str:
    row = db._conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
        (index,),
    ).fetchone()
    assert row is not None and row[0]
    return row[0]


def _rebuild_table(db: Database, table: str, new_sql: str) -> None:
    """Rebuild a table from modified DDL, preserving its explicit indexes and
    triggers.  ``PRAGMA foreign_keys`` is cycled outside any transaction."""
    conn = db._conn
    index_sql = [
        row[0] for row in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? "
            "AND sql IS NOT NULL",
            (table,),
        )
    ]
    trigger_sql = [
        row[0] for row in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND tbl_name=?",
            (table,),
        )
    ]
    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(f'DROP TABLE "{table}"')
    conn.execute(new_sql)
    for sql in index_sql:
        conn.execute(sql)
    for sql in trigger_sql:
        conn.execute(sql)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()


def _replace_index(db: Database, index: str, new_sql: str) -> None:
    db._conn.execute(f'DROP INDEX "{index}"')
    db._conn.execute(new_sql)
    db._conn.commit()


def _assert_refused(
    outcome, *, objects: set[str], codes: set[str], v2: bool | None = None,
) -> None:
    assert outcome.evidence is None, outcome.evidence
    diagnostic = outcome.diagnostic
    assert diagnostic is not None
    assert diagnostic["object"] in objects, diagnostic
    assert diagnostic["code"] in codes, diagnostic
    if v2 is not None:
        assert diagnostic["v2"] is v2, diagnostic


# --------------------------------------------------------------------------
# Reference independence and the two accepted layouts
# --------------------------------------------------------------------------


def test_reference_accepts_fresh_and_migrated_with_distinct_raw_digests(tmp_path):
    fresh_db = _pristine(tmp_path, "fresh.db")
    try:
        fresh = _capture(fresh_db)
        assert fresh.evidence is not None, fresh.diagnostic
        assert fresh.evidence.contract_version == V2_SCHEMA_INTEGRITY_CONTRACT
        assert recheck_authority_policy_v2_schema_integrity(
            fresh.evidence, fresh_db,
        )
    finally:
        fresh_db._conn.close()

    historical = tmp_path / "historical.db"
    reconstruct_historical_database(historical)
    migrated_db = Database(historical)
    try:
        migrated = _capture(migrated_db)
        assert migrated.evidence is not None, migrated.diagnostic
        assert recheck_authority_policy_v2_schema_integrity(
            migrated.evidence, migrated_db,
        )
    finally:
        migrated_db._conn.close()

    # Both accepted layouts pass but their ACTUAL raw DDL digests differ.
    assert fresh.evidence.raw_digest != migrated.evidence.raw_digest
    assert fresh.evidence.inventory_digest != migrated.evidence.inventory_digest


def test_reference_construction_is_independent_of_the_candidate(tmp_path):
    references = authority._v2_build_reference_inventories()
    assert references is not None and len(references) == 2
    snapshot = json.dumps(references, sort_keys=True)

    # A wildly different candidate must not change the reference.
    bad = _pristine(tmp_path, "bad.db")
    try:
        bad._conn.execute("CREATE INDEX thr229_unexpected ON tasks(status)")
        bad._conn.commit()
        outcome = _capture(bad)
        assert outcome.evidence is None
    finally:
        bad._conn.close()
    assert json.dumps(authority._v2_build_reference_inventories(), sort_keys=True) == snapshot


# --------------------------------------------------------------------------
# R3 named negatives — each independently against a pristine disposable model
# --------------------------------------------------------------------------


def test_removed_authority_audit_candidate_fk_rejected(tmp_path):
    db = _pristine(tmp_path)
    try:
        _seed_preexisting_row(db)
        baseline = _row_counts(db)
        original = _table_sql(db, "authority_audit")
        assert "REFERENCES authority_candidates(id)" in original
        mutated = original.replace(" REFERENCES authority_candidates(id)", "")
        assert mutated != original
        _rebuild_table(db, "authority_audit", mutated)

        outcome = _capture(db)
        _assert_refused(
            outcome,
            objects={"authority_audit"},
            codes={"table_sql_mismatch", "table_foreign_key_mismatch"},
        )
        # No silent repair; the preexisting row and the mutation remain.
        assert "REFERENCES authority_candidates(id)" not in _table_sql(
            db, "authority_audit"
        )
        assert _row_counts(db) == baseline
    finally:
        db._conn.close()


def test_widened_authority_candidates_lifecycle_check_rejected(tmp_path):
    db = _pristine(tmp_path)
    try:
        _seed_preexisting_row(db)
        baseline = _row_counts(db)
        original = _table_sql(db, "authority_candidates")
        mutated = original.replace(
            "CHECK (lifecycle_state IN ('created','evaluated','consumed'))",
            "CHECK (lifecycle_state IN "
            "('created','evaluated','consumed','other'))",
        )
        assert mutated != original
        _rebuild_table(db, "authority_candidates", mutated)

        outcome = _capture(db)
        _assert_refused(
            outcome, objects={"authority_candidates"},
            codes={"table_sql_mismatch"},
        )
        assert "'consumed','other'" in _table_sql(db, "authority_candidates")
        assert _row_counts(db) == baseline
    finally:
        db._conn.close()


def test_removed_authority_candidates_lifecycle_check_rejected(tmp_path):
    db = _pristine(tmp_path)
    try:
        _seed_preexisting_row(db)
        baseline = _row_counts(db)
        original = _table_sql(db, "authority_candidates")
        check = (
            "\n                    CHECK (lifecycle_state IN "
            "('created','evaluated','consumed'))"
        )
        assert check in original
        mutated = original.replace(check, "")
        _rebuild_table(db, "authority_candidates", mutated)

        outcome = _capture(db)
        _assert_refused(
            outcome, objects={"authority_candidates"},
            codes={"table_sql_mismatch"},
        )
        assert "CHECK (lifecycle_state" not in _table_sql(
            db, "authority_candidates"
        )
        assert _row_counts(db) == baseline
    finally:
        db._conn.close()


def test_dropped_unique_thread_messages_thread_seq_rejected(tmp_path):
    db = _pristine(tmp_path)
    try:
        _seed_preexisting_row(db)
        baseline = _row_counts(db)
        _replace_index(
            db, "idx_thread_messages_thread_seq",
            "CREATE INDEX idx_thread_messages_thread_seq "
            "ON thread_messages(thread_id, seq)",
        )
        outcome = _capture(db)
        _assert_refused(
            outcome, objects={"idx_thread_messages_thread_seq"},
            codes={"table_index_metadata_mismatch", "index_xinfo_mismatch",
                   "index_sql_mismatch"},
        )
        # The unique flag is still gone (no silent repair).
        info = db._conn.execute(
            "PRAGMA index_list('thread_messages')"
        ).fetchall()
        unique = {
            row[1]: row[2] for row in info
        }["idx_thread_messages_thread_seq"]
        assert not unique
        assert _row_counts(db) == baseline
    finally:
        db._conn.close()


def test_changed_cser_index_expression_rejected(tmp_path):
    db = _pristine(tmp_path)
    try:
        _seed_preexisting_row(db)
        baseline = _row_counts(db)
        original = _index_sql(db, "idx_cser_current_scope_unique")
        mutated = original.replace(
            "COALESCE(scope_target, '')", "COALESCE(scope_target, 'x')"
        )
        assert mutated != original
        _replace_index(db, "idx_cser_current_scope_unique", mutated)

        outcome = _capture(db)
        _assert_refused(
            outcome, objects={"idx_cser_current_scope_unique"},
            codes={"index_sql_mismatch"},
        )
        assert "COALESCE(scope_target, 'x')" in _index_sql(
            db, "idx_cser_current_scope_unique"
        )
        assert _row_counts(db) == baseline
    finally:
        db._conn.close()


def test_inverted_cser_index_predicate_rejected(tmp_path):
    db = _pristine(tmp_path)
    try:
        _seed_preexisting_row(db)
        baseline = _row_counts(db)
        original = _index_sql(db, "idx_cser_current_scope_unique")
        mutated = original.replace(
            "WHERE superseded_at IS NULL", "WHERE superseded_at IS NOT NULL"
        )
        assert mutated != original
        _replace_index(db, "idx_cser_current_scope_unique", mutated)

        outcome = _capture(db)
        _assert_refused(
            outcome, objects={"idx_cser_current_scope_unique"},
            codes={"index_sql_mismatch"},
        )
        assert "WHERE superseded_at IS NOT NULL" in _index_sql(
            db, "idx_cser_current_scope_unique"
        )
        assert _row_counts(db) == baseline
    finally:
        db._conn.close()


def test_dropped_authority_evaluations_no_delete_trigger_rejected(tmp_path):
    db = _pristine(tmp_path)
    try:
        _seed_preexisting_row(db)
        baseline = _row_counts(db)
        db._conn.execute("DROP TRIGGER authority_evaluations_no_delete")
        db._conn.commit()

        outcome = _capture(db)
        _assert_refused(
            outcome, objects={"authority_evaluations_no_delete"},
            codes={"missing_object", "missing_v2_object"},
        )
        present = db._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='trigger' "
            "AND name='authority_evaluations_no_delete'"
        ).fetchone()
        assert present is None
        assert _row_counts(db) == baseline
    finally:
        db._conn.close()


def test_replaced_authority_evaluations_no_delete_body_rejected(tmp_path):
    db = _pristine(tmp_path)
    try:
        _seed_preexisting_row(db)
        baseline = _row_counts(db)
        db._conn.execute("DROP TRIGGER authority_evaluations_no_delete")
        db._conn.execute(
            "CREATE TRIGGER authority_evaluations_no_delete "
            "BEFORE DELETE ON authority_evaluations "
            "BEGIN SELECT 1; END"
        )
        db._conn.commit()

        outcome = _capture(db)
        _assert_refused(
            outcome, objects={"authority_evaluations_no_delete"},
            codes={"trigger_sql_mismatch"},
        )
        assert "SELECT 1" in db._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='authority_evaluations_no_delete'"
        ).fetchone()[0]
        assert _row_counts(db) == baseline
    finally:
        db._conn.close()


def test_unexpected_index_rejected(tmp_path):
    db = _pristine(tmp_path)
    try:
        _seed_preexisting_row(db)
        baseline = _row_counts(db)
        db._conn.execute("CREATE INDEX thr229_unexpected ON tasks(status)")
        db._conn.commit()

        outcome = _capture(db)
        _assert_refused(
            outcome, objects={"thr229_unexpected"},
            codes={"unexpected_object"},
        )
        # The unexpected index is never silently dropped.
        assert db._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' "
            "AND name='thr229_unexpected'"
        ).fetchone() is not None
        assert _row_counts(db) == baseline
    finally:
        db._conn.close()


def test_missing_or_corrupt_v2_object_rejected(tmp_path):
    dropped = _pristine(tmp_path, "dropped.db")
    try:
        dropped._conn.execute("DROP TABLE authority_policy_v2_attempts")
        dropped._conn.commit()
        outcome = _capture(dropped)
        _assert_refused(
            outcome, objects={"authority_policy_v2_attempts"},
            codes={"missing_v2_object", "missing_object"}, v2=True,
        )
    finally:
        dropped._conn.close()

    corrupt = _pristine(tmp_path, "corrupt.db")
    try:
        original = _table_sql(corrupt, "authority_policy_v2_attempts")
        mutated = original.replace(
            "owner_attempt_id TEXT NOT NULL",
            "owner_attempt_id TEXT",
        )
        assert mutated != original
        _rebuild_table(corrupt, "authority_policy_v2_attempts", mutated)
        outcome = _capture(corrupt)
        _assert_refused(
            outcome, objects={"authority_policy_v2_attempts"},
            codes={"table_sql_mismatch", "table_column_layout_mismatch"},
            v2=True,
        )
    finally:
        corrupt._conn.close()


# --------------------------------------------------------------------------
# Capture freezes the candidate's ACTUAL raw digest; later drift denies
# --------------------------------------------------------------------------


def test_capture_then_raw_digest_drift_denies_recheck(tmp_path):
    db = _pristine(tmp_path)
    try:
        evidence = _capture(db).evidence
        assert evidence is not None
        assert recheck_authority_policy_v2_schema_integrity(evidence, db)
        db._conn.execute("CREATE INDEX thr229_unexpected ON tasks(status)")
        db._conn.commit()
        assert not recheck_authority_policy_v2_schema_integrity(evidence, db)
    finally:
        db._conn.close()


def test_matching_a_different_accepted_layout_denies_recheck(tmp_path):
    db = _pristine(tmp_path)
    try:
        evidence = _capture(db).evidence
        assert evidence is not None
        assert recheck_authority_policy_v2_schema_integrity(evidence, db)

        # Swap the fresh threads/thread_messages layout for the OTHER accepted
        # migrated layout.  It is a valid accepted reference, but matching it
        # after capture cannot authorize the changed attempt.
        index_sql = [
            row[0] for row in db._conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' "
                "AND tbl_name IN ('threads','thread_messages') AND sql IS NOT NULL"
            )
        ]
        db._conn.commit()
        db._conn.execute("PRAGMA foreign_keys=OFF")
        db._conn.execute("DROP TABLE thread_messages")
        db._conn.execute("DROP TABLE threads")
        for table in ("threads", "thread_messages"):
            db._conn.execute(authority._V2_MIGRATED_TABLE_CREATE_SQL[table])
        for sql in index_sql:
            db._conn.execute(sql)
        db._conn.execute("PRAGMA foreign_keys=ON")
        db._conn.commit()

        migrated = _capture(db)
        assert migrated.evidence is not None, migrated.diagnostic
        assert migrated.evidence.raw_digest != evidence.raw_digest
        assert not recheck_authority_policy_v2_schema_integrity(evidence, db)
    finally:
        db._conn.close()


def test_failed_capture_never_becomes_a_successful_recheck(tmp_path):
    db = _pristine(tmp_path)
    try:
        assert not recheck_authority_policy_v2_schema_integrity(None, db)
        evidence = _capture(db).evidence
        assert evidence is not None
        wrong_contract = evidence.model_copy(
            update={"contract_version": "other-contract"}
        )
        assert not recheck_authority_policy_v2_schema_integrity(wrong_contract, db)
    finally:
        db._conn.close()


# --------------------------------------------------------------------------
# Full historical fixture: reconstruct, migrate, validate
# --------------------------------------------------------------------------


def test_full_historical_fixture_reconstructs_and_migrates(tmp_path):
    fixture = load_historical_fixture()
    assert fixture["provenance"]["source_commit"] == (
        "f39b4934611ca13ab7d8b7fa2d7be983a4bfb7a5"
    )
    assert len(fixture["provenance"]["runtime_source_sha256"]) == 64
    assert fixture["object_count"] == len(fixture["objects"])

    historical = tmp_path / "historical.db"
    reconstruct_historical_database(historical)

    # The reconstruction is byte-faithful to the recorded historical inventory.
    raw = sqlite3.connect(str(historical))
    try:
        inventory = authority._v2_capture_inventory(raw)
    finally:
        raw.close()
    assert historical_inventory_digest(inventory) == (
        fixture["historical_inventory_digest"]
    )

    # Historical task_results.output_summary / confidence_score are nullable
    # WITHOUT defaults; the reduced fixture's allowances must not appear.
    raw = sqlite3.connect(str(historical))
    try:
        xinfo = {
            row[1]: row for row in raw.execute(
                "PRAGMA table_xinfo('task_results')"
            )
        }
    finally:
        raw.close()
    assert xinfo["output_summary"][3] == 0  # notnull
    assert xinfo["output_summary"][4] is None  # dflt_value
    assert xinfo["confidence_score"][3] == 0
    assert xinfo["confidence_score"][4] is None
    task_results_sql = next(
        obj["sql"] for obj in fixture["objects"]
        if obj["type"] == "table" and obj["name"] == "task_results"
    )
    assert "output_summary TEXT" in task_results_sql
    assert "output_summary TEXT NOT NULL" not in task_results_sql
    assert "confidence_score INTEGER" in task_results_sql
    assert "confidence_score INTEGER NOT NULL" not in task_results_sql

    # Opening the whole old schema through the ACTUAL current Database path
    # migrates it and passes the new production seam.
    migrated_db = Database(historical)
    try:
        outcome = _capture(migrated_db)
        assert outcome.evidence is not None, outcome.diagnostic
        assert outcome.evidence.object_count > fixture["object_count"]
    finally:
        migrated_db._conn.close()


def test_unsupported_historical_defaults_and_nullability_rejected(tmp_path):
    historical = tmp_path / "historical.db"
    reconstruct_historical_database(historical)
    db = Database(historical)
    try:
        original = _table_sql(db, "task_results")
        mutated = original.replace(
            "output_summary TEXT,", "output_summary TEXT NOT NULL DEFAULT '',"
        ).replace(
            "confidence_score INTEGER,",
            "confidence_score INTEGER NOT NULL DEFAULT 80,",
        )
        assert mutated != original
        _rebuild_table(db, "task_results", mutated)

        outcome = _capture(db)
        _assert_refused(
            outcome, objects={"task_results"},
            codes={"table_sql_mismatch", "table_column_layout_mismatch"},
        )
        assert "DEFAULT 80" in _table_sql(db, "task_results")
    finally:
        db._conn.close()


def test_unsupported_historical_ordering_rejected(tmp_path):
    historical = tmp_path / "historical.db"
    reconstruct_historical_database(historical)
    db = Database(historical)
    try:
        original = _table_sql(db, "task_results")
        mutated = original.replace("output_summary TEXT,\n", "").replace(
            "local_ci TEXT)", "local_ci TEXT, output_summary TEXT)"
        )
        assert mutated != original
        _rebuild_table(db, "task_results", mutated)

        outcome = _capture(db)
        _assert_refused(
            outcome, objects={"task_results"},
            codes={"table_sql_mismatch", "table_column_layout_mismatch"},
        )
    finally:
        db._conn.close()


# --------------------------------------------------------------------------
# Integrity / foreign-key data checks and read failures
# --------------------------------------------------------------------------


def _shared_page_integrity_corruption(db: Database) -> None:
    """Register two explicit indexes on the same b-tree page (real SQLite
    integrity-check failure, schema inventory otherwise unchanged)."""
    conn = db._conn
    rows = conn.execute(
        "SELECT name, rootpage FROM sqlite_master "
        "WHERE type='index' AND rootpage>0 ORDER BY name LIMIT 2"
    ).fetchall()
    first, second = rows[0], rows[1]
    conn.commit()
    conn.execute("PRAGMA writable_schema=ON")
    conn.execute(
        "UPDATE sqlite_master SET rootpage=? WHERE name=?",
        (second[1], first[0]),
    )
    version = conn.execute("PRAGMA schema_version").fetchone()[0]
    conn.execute(f"PRAGMA schema_version={version + 1}")
    conn.execute("PRAGMA writable_schema=OFF")
    conn.commit()


def test_failed_foreign_key_data_check_fails_closed(tmp_path):
    db = _pristine(tmp_path)
    try:
        _seed_preexisting_row(db)
        baseline = _row_counts(db)
        db._conn.commit()
        db._conn.execute("PRAGMA foreign_keys=OFF")
        db._conn.execute(
            "INSERT INTO thread_messages"
            "(thread_id, seq, speaker, kind, created_at) "
            "VALUES ('missing-thread', 1, 'a', 'reply', '2026-01-01')"
        )
        db._conn.commit()
        db._conn.execute("PRAGMA foreign_keys=ON")

        outcome = _capture(db)
        _assert_refused(
            outcome, objects={None}, codes={"foreign_key_check_failed"},
        )
        # The helper neither repaired the data nor admitted anything.
        assert _row_counts(db) == baseline
    finally:
        db._conn.close()


def test_failed_integrity_check_fails_closed(tmp_path):
    db = _pristine(tmp_path)
    try:
        _seed_preexisting_row(db)
        baseline = _row_counts(db)
        _shared_page_integrity_corruption(db)

        outcome = _capture(db)
        _assert_refused(
            outcome, objects={None}, codes={"integrity_check_failed"},
        )
        assert _row_counts(db) == baseline
    finally:
        db._conn.close()


def test_unreadable_candidate_and_corrupt_page_fail_closed(tmp_path):
    # A read/query defect fails closed with a bounded diagnostic.
    db = _pristine(tmp_path, "readfail.db")
    try:
        original = authority._v2_capture_inventory

        def _boom(_conn):
            raise sqlite3.DatabaseError("simulated read defect")

        authority._v2_capture_inventory = _boom
        try:
            outcome = _capture(db)
        finally:
            authority._v2_capture_inventory = original
        _assert_refused(
            outcome, objects={None}, codes={"candidate_unreadable"},
        )
    finally:
        db._conn.close()

    # A page-header corruption survives the Database open and fails the
    # integrity read closed.
    corrupt = _pristine(tmp_path, "pagecorrupt.db")
    path = Path(corrupt.db_path)
    try:
        corrupt._conn.execute(
            "INSERT INTO processed_event_ids"
            "(org_slug, feishu_event_id, processed_at, outcome) "
            "VALUES ('seam-probe', 'evt-2', '2026-01-01T00:00:00Z', 'ok')"
        )
        corrupt._conn.commit()
        page_size = corrupt._conn.execute("PRAGMA page_size").fetchone()[0]
        root = corrupt._conn.execute(
            "SELECT rootpage FROM sqlite_master WHERE type='table' "
            "AND name='processed_event_ids'"
        ).fetchone()[0]
    finally:
        corrupt._conn.close()
    with path.open("r+b") as handle:
        handle.seek((root - 1) * page_size)
        handle.write(b"\xff" * 32)
    reopened = Database(path)
    try:
        outcome = _capture(reopened)
        _assert_refused(
            outcome, objects={None},
            codes={"integrity_check_unavailable", "integrity_check_failed"},
        )
    finally:
        reopened._conn.close()


def test_unavailable_reference_fails_closed(tmp_path, monkeypatch):
    db = _pristine(tmp_path)
    try:
        monkeypatch.setattr(
            authority, "_v2_build_reference_inventories", lambda: None,
        )
        outcome = _capture(db)
        _assert_refused(
            outcome, objects={None}, codes={"reference_unavailable"},
        )

        def _raise():
            raise RuntimeError("reference source unavailable")

        monkeypatch.setattr(
            authority, "_v2_build_reference_inventories", _raise,
        )
        outcome = _capture(db)
        _assert_refused(
            outcome, objects={None}, codes={"reference_unavailable"},
        )
    finally:
        db._conn.close()


# --------------------------------------------------------------------------
# Read-only proof: no invented admission, no repair
# --------------------------------------------------------------------------


def test_helper_is_read_only_and_invents_no_admission(tmp_path):
    db = _pristine(tmp_path)
    try:
        _seed_preexisting_row(db)
        before = _row_counts(db)
        assert before["authority_candidates"] == 0
        assert before["authority_policy_v2_attempts"] == 0
        assert before["escalation_notifications"] == 0
        assert before["tasks"] == 0

        # Capture (valid) and a refused capture (mutated) must both be no-ops.
        assert _capture(db).evidence is not None
        db._conn.execute("DROP TRIGGER authority_evaluations_no_delete")
        db._conn.commit()
        assert _capture(db).evidence is None

        after = _row_counts(db)
        assert after == before
        assert db._conn.execute(
            "SELECT COUNT(*) FROM processed_event_ids"
        ).fetchone()[0] == 1
    finally:
        db._conn.close()
