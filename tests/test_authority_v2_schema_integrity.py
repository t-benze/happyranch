"""THR-229 checkpoint C3a — v2 constraint-sensitive schema-integrity seam tests.

These tests drive the ACTUAL production seam
(``capture_authority_policy_v2_schema_integrity`` /
``recheck_authority_policy_v2_schema_integrity``) against pristine disposable
schema models and the checked-in full historical fixture.  They never call the
fixture generator or re-implement the comparison oracle.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
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
    "authority_policy_v2_candidates",
    "authority_policy_v2_pins",
    "authority_policy_v2_candidate_audit",
    "authority_policy_v2_evaluations",
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


# --------------------------------------------------------------------------
# THR-229 C3a correction (TASK-8442): exact internal prefix and one coherent
# read view.  These drive the real production seam; they never re-implement the
# oracle or the fixture generator.
# --------------------------------------------------------------------------


def _raiser(exc: BaseException):
    def _boom(*_args, **_kwargs):
        raise exc

    return _boom


def _lock_free_cross_thread(db: Database, timeout: float = 0.5) -> bool:
    """True when another thread can take the Database lock (a same-thread RLock
    re-acquire would mask a leak)."""
    result: dict[str, bool] = {}

    def _attempt() -> None:
        got = db._lock.acquire(timeout=timeout)
        if got:
            db._lock.release()
        result["got"] = got

    thread = threading.Thread(target=_attempt, daemon=True)
    thread.start()
    thread.join(timeout=timeout + 2.0)
    return bool(result.get("got"))


def _independent_drop_trigger(path) -> None:
    """Commit a real DDL mutation from an INDEPENDENT sqlite3 connection."""
    other = sqlite3.connect(str(path))
    try:
        other.execute("DROP TRIGGER authority_evaluations_no_delete")
        other.commit()
    finally:
        other.close()


def test_reserved_internal_prefix_is_literal_and_case_insensitive():
    assert authority._v2_is_reserved_internal_name("sqlite_sequence")
    assert authority._v2_is_reserved_internal_name("sqlite_stat1")
    assert authority._v2_is_reserved_internal_name("SQLITE_stat1")
    # Legal user names that merely RESEMBLE the internal namespace stay user
    # objects; SQL `LIKE 'sqlite_%'` wrongly treats the `_` as a wildcard.
    for name in ("sqlite", "sqliteXunreviewed", "SQLiteXview", "SqLiTeXtrg",
                 "sqliteXidx"):
        assert not authority._v2_is_reserved_internal_name(name), name


def test_non_internal_sqliteX_table_is_refused_not_omitted(tmp_path):
    db = _pristine(tmp_path)
    try:
        _seed_preexisting_row(db)
        baseline = _row_counts(db)
        db._conn.execute("CREATE TABLE sqliteXunreviewed (id INTEGER)")
        db._conn.commit()

        # The object is really present and the inventory keeps it (the defect
        # omitted it via a SQL LIKE wildcard).
        assert "sqliteXunreviewed" in authority._v2_capture_inventory(db._conn)[
            "tables"
        ]

        outcome = _capture(db)
        _assert_refused(
            outcome, objects={"sqliteXunreviewed"},
            codes={"unexpected_object"}, v2=False,
        )
        # No repair/deletion and no invented authority/task/audit allocation.
        assert db._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='sqliteXunreviewed'"
        ).fetchone() is not None
        assert _row_counts(db) == baseline
    finally:
        db._conn.close()


@pytest.mark.parametrize(
    "name,ddl",
    [
        ("sqliteXidx", "CREATE INDEX sqliteXidx ON tasks(status)"),
        ("SQLiteXidx", "CREATE INDEX SQLiteXidx ON tasks(status)"),
        ("sqliteXtrg",
         "CREATE TRIGGER sqliteXtrg AFTER INSERT ON tasks BEGIN SELECT 1; END"),
        ("SqLiTeXtrg",
         "CREATE TRIGGER SqLiTeXtrg AFTER INSERT ON tasks BEGIN SELECT 1; END"),
        ("sqliteXview", "CREATE VIEW sqliteXview AS SELECT 1 AS x"),
        ("SQLiteXview", "CREATE VIEW SQLiteXview AS SELECT 1 AS x"),
    ],
)
def test_non_internal_sqliteX_index_trigger_view_refused(tmp_path, name, ddl):
    db = _pristine(tmp_path)
    try:
        _seed_preexisting_row(db)
        baseline = _row_counts(db)
        db._conn.execute(ddl)
        db._conn.commit()

        assert name in {
            key
            for kind in ("indexes", "triggers", "views")
            for key in authority._v2_capture_inventory(db._conn)[kind]
        }

        outcome = _capture(db)
        _assert_refused(
            outcome, objects={name}, codes={"unexpected_object"}, v2=False,
        )
        # The unexpected object is never silently dropped, and nothing is
        # admitted.
        assert db._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name=?", (name,)
        ).fetchone() is not None
        assert _row_counts(db) == baseline
    finally:
        db._conn.close()


def test_internal_objects_are_still_excluded_from_the_inventory(tmp_path):
    db = _pristine(tmp_path)
    try:
        inventory = authority._v2_capture_inventory(db._conn)
        names = {
            key
            for kind in ("tables", "indexes", "triggers", "views")
            for key in inventory[kind]
        }
        assert not any(
            authority._v2_is_reserved_internal_name(name) for name in names
        )
        # The AUTOINCREMENT internal table is real and still excluded.
        assert db._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'"
        ).fetchone() is not None
        assert "sqlite_sequence" not in names
        # Autoindex constraint metadata is retained inside the owning table
        # (the autoindex itself stays excluded from the top-level inventory).
        candidates = inventory["tables"]["authority_candidates"]
        origins = {meta["origin"] for meta in candidates["indexes"].values()}
        assert {"pk", "u"} <= origins
        autoindex_names = [
            key for key in candidates["indexes"] if key.startswith("sqlite_")
        ]
        assert autoindex_names
        assert not any(
            key in inventory["indexes"] for key in autoindex_names
        )
    finally:
        db._conn.close()


def test_independent_connection_mutation_before_inventory_refuses(tmp_path):
    db = _pristine(tmp_path)
    try:
        _seed_preexisting_row(db)
        baseline = _row_counts(db)
        # Committed before any candidate read: visible in the captured snapshot.
        _independent_drop_trigger(db.db_path)

        outcome = _capture(db)
        _assert_refused(
            outcome, objects={"authority_evaluations_no_delete"},
            codes={"missing_object", "missing_v2_object"},
        )
        assert _row_counts(db) == baseline
    finally:
        db._conn.close()


def test_interleave_between_inventory_and_integrity_keeps_coherent_snapshot(
    tmp_path, monkeypatch,
):
    db = _pristine(tmp_path)
    try:
        reference = authority._v2_build_reference_inventories()[0]
        original = authority._v2_capture_inventory

        def capture_then_mutate(conn):
            result = original(conn)
            if conn is db._conn:
                _independent_drop_trigger(db.db_path)
            return result

        with monkeypatch.context() as patch:
            patch.setattr(authority, "_v2_capture_inventory", capture_then_mutate)
            outcome = _capture(db)
        assert outcome.evidence is not None, outcome.diagnostic
        # The frozen evidence is the coherent pre-mutation snapshot ...
        assert outcome.evidence.inventory_digest == (
            authority._v2_inventory_digest(reference)
        )
        # ... and the independent commit is caught by the immediate recheck.
        assert not recheck_authority_policy_v2_schema_integrity(
            outcome.evidence, db,
        )
    finally:
        db._conn.close()


def test_interleave_between_integrity_and_digest_keeps_coherent_snapshot(
    tmp_path, monkeypatch,
):
    """The manager-supplied trigger-drop reproducer, now coherent."""
    db = _pristine(tmp_path)
    try:
        original = authority._v2_data_integrity_check

        def integrity_then_mutate(conn):
            result = original(conn)
            _independent_drop_trigger(db.db_path)
            return result

        with monkeypatch.context() as patch:
            patch.setattr(
                authority, "_v2_data_integrity_check", integrity_then_mutate,
            )
            outcome = _capture(db)
        # A mutation invisible to the pinned snapshot may still produce
        # evidence for that snapshot, but it can never recheck true.
        assert outcome.evidence is not None, outcome.diagnostic
        assert not recheck_authority_policy_v2_schema_integrity(
            outcome.evidence, db,
        )
        # The evidence's own raw digest and the live digest already disagree.
        assert outcome.evidence.raw_digest != authority._live_schema_digest(db)
    finally:
        db._conn.close()


def test_independent_mutation_after_capture_makes_recheck_refuse(tmp_path):
    db = _pristine(tmp_path)
    try:
        evidence = _capture(db).evidence
        assert evidence is not None
        assert recheck_authority_policy_v2_schema_integrity(evidence, db)
        _independent_drop_trigger(db.db_path)
        assert not recheck_authority_policy_v2_schema_integrity(evidence, db)
    finally:
        db._conn.close()


def test_shared_connection_writer_cannot_interleave_candidate_reads(
    tmp_path, monkeypatch,
):
    db = _pristine(tmp_path)
    writer_threads: list[threading.Thread] = []
    try:
        writer_attempted = threading.Event()
        writer_done = threading.Event()
        original = authority._v2_data_integrity_check

        def writer() -> None:
            writer_attempted.set()
            db.execute("CREATE INDEX thr229_contention ON tasks(status)")
            db._conn.commit()
            writer_done.set()

        def parked(conn):
            result = original(conn)
            thread = threading.Thread(target=writer, daemon=True)
            writer_threads.append(thread)
            thread.start()
            assert writer_attempted.wait(5.0)
            # A synchronized Database operation must NOT interleave the
            # capture: the view holds the shared lock for the whole read.
            assert not writer_done.wait(0.5)
            return result

        with monkeypatch.context() as patch:
            patch.setattr(authority, "_v2_data_integrity_check", parked)
            outcome = _capture(db)
        assert outcome.evidence is not None, outcome.diagnostic

        for thread in writer_threads:
            thread.join(timeout=10.0)
            assert not thread.is_alive()
        assert writer_done.is_set()
        # The serialized writer landed only after capture -> immediate refusal.
        assert not recheck_authority_policy_v2_schema_integrity(
            outcome.evidence, db,
        )
    finally:
        db._conn.close()


def test_standalone_capture_releases_view_and_lock_on_success_and_failure(
    tmp_path, monkeypatch,
):
    db = _pristine(tmp_path)
    try:
        assert _capture(db).evidence is not None
        assert db._conn.in_transaction is False
        assert _lock_free_cross_thread(db)

        # Candidate read failure.
        with monkeypatch.context() as patch:
            patch.setattr(
                authority, "_v2_capture_inventory",
                _raiser(sqlite3.DatabaseError("simulated read defect")),
            )
            outcome = _capture(db)
        assert outcome.evidence is None
        assert outcome.diagnostic["code"] == "candidate_unreadable"
        assert db._conn.in_transaction is False
        assert _lock_free_cross_thread(db)

        # Frozen-digest failure.
        with monkeypatch.context() as patch:
            patch.setattr(
                authority, "_live_schema_digest",
                _raiser(RuntimeError("simulated digest defect")),
            )
            outcome = _capture(db)
        assert outcome.evidence is None
        assert outcome.diagnostic["code"] == "candidate_unreadable"
        assert db._conn.in_transaction is False
        assert _lock_free_cross_thread(db)
    finally:
        db._conn.close()


def _begin_caller_row(db: Database, key: str) -> None:
    db._conn.execute("BEGIN")
    db._conn.execute(
        "INSERT INTO processed_event_ids"
        "(org_slug, feishu_event_id, processed_at, outcome) "
        "VALUES (?, ?, '2026-01-01T00:00:00Z', 'ok')",
        (f"caller-{key}", f"evt-{key}"),
    )
    assert db._conn.in_transaction is True


def _caller_row_count(db: Database, key: str) -> int:
    return db._conn.execute(
        "SELECT COUNT(*) FROM processed_event_ids WHERE org_slug=?",
        (f"caller-{key}",),
    ).fetchone()[0]


def test_capture_joins_open_caller_transaction_without_spurious_commit(tmp_path):
    db = _pristine(tmp_path)
    try:
        _begin_caller_row(db, "join")
        outcome = _capture(db)
        assert outcome.evidence is not None, outcome.diagnostic
        # No spurious commit/rollback: the caller still owns the transaction
        # and its uncommitted row is intact.
        assert db._conn.in_transaction is True
        assert _caller_row_count(db, "join") == 1
        assert _lock_free_cross_thread(db)

        db._conn.commit()
        assert db._conn.in_transaction is False
        assert _caller_row_count(db, "join") == 1
    finally:
        db._conn.close()


def test_capture_inside_caller_transaction_releases_on_failure(
    tmp_path, monkeypatch,
):
    db = _pristine(tmp_path)
    try:
        _begin_caller_row(db, "fail")
        with monkeypatch.context() as patch:
            patch.setattr(
                authority, "_v2_capture_inventory",
                _raiser(sqlite3.DatabaseError("simulated read defect")),
            )
            outcome = _capture(db)
        assert outcome.evidence is None
        assert outcome.diagnostic["code"] == "candidate_unreadable"
        # Caller ownership preserved; no lock/transaction leak.
        assert db._conn.in_transaction is True
        assert _caller_row_count(db, "fail") == 1
        assert _lock_free_cross_thread(db)

        db._conn.rollback()
        assert db._conn.in_transaction is False
        assert _caller_row_count(db, "fail") == 0
    finally:
        db._conn.close()


def test_legacy_v1_digest_helpers_unchanged_by_the_coherent_view(tmp_path):
    db = _pristine(tmp_path)
    try:
        digest = authority._live_schema_digest(db)
        rows = db.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
        ).fetchall()
        expected = hashlib.sha256(
            "\n".join(str(row[0]) for row in rows).encode("utf-8")
        ).hexdigest()
        assert digest == expected
        assert db._conn.in_transaction is False
        release = authority._release_schema_digest()
        assert isinstance(release, str) and len(release) == 64
    finally:
        db._conn.close()
