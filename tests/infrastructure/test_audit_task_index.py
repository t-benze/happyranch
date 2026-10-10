"""THR-278 / TASK-10013 accepted C1-C9: real indexed audit consumers.

All corruption is input construction in individually owned offline databases.
No production reader, authenticator, recovery or schema oracle is replaced.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from runtime.daemon.__main__ import _sweep_on_startup
from runtime.daemon.queue import TaskQueue
from runtime.infrastructure.database import Database
from runtime.models import TaskRecord, TaskStatus
from runtime.orchestrator.authority import refuse_authority_policy_v2_pre_final_on_startup
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
from tests.test_authority_v2_attempt_admission import MANAGER, SESSION_ID, TASK_ID
from tests.test_authority_v2_evaluation_stage import _admitted, _claim
from tests.test_authority_v2_refusal_housekeeping import _FailingConn, _seed_receipt
from tests.test_authority_v2_startup_reaper import _reaper_orch


INDEX = "idx_audit_log_task_id"


def _assert_index(conn):
    sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (INDEX,),
    ).fetchone()
    assert sql_row is not None, "required idx_audit_log_task_id is absent"
    assert sql_row[0] == "CREATE INDEX idx_audit_log_task_id ON audit_log(task_id)"
    entries = [tuple(r) for r in conn.execute("PRAGMA index_list('audit_log')")]
    selected = [r for r in entries if r[1] == INDEX]
    assert len(selected) == 1
    assert selected[0][2:] == (0, "c", 0)  # nonunique, explicit, nonpartial
    assert [tuple(r)[2] for r in conn.execute(f"PRAGMA index_info('{INDEX}')")] == ["task_id"]
    keys = [tuple(r) for r in conn.execute(f"PRAGMA index_xinfo('{INDEX}')") if r[5]]
    assert [(r[2], r[3], r[4]) for r in keys] == [("task_id", 0, "BINARY")]


def _snapshot(db):
    """Retain complete SQL/metadata and literal persisted values independently."""
    conn = db._conn
    schema = [tuple(r) for r in conn.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
    )]
    tables = [r[1] for r in schema if r[0] == "table"]
    values = {}
    metadata = {}
    for table in tables:
        quoted = '"' + table.replace('"', '""') + '"'
        values[table] = sorted(
            [tuple(r) for r in conn.execute(f"SELECT * FROM {quoted}")], key=repr,
        )
        metadata[table] = {
            "columns": [tuple(r) for r in conn.execute(f"PRAGMA table_xinfo({quoted})")],
            "foreign_keys": [tuple(r) for r in conn.execute(f"PRAGMA foreign_key_list({quoted})")],
            "indexes": sorted([tuple(r)[1:] for r in conn.execute(f"PRAGMA index_list({quoted})")]),
        }
    return schema, metadata, values


def test_fresh_database_has_exact_audit_task_index(tmp_path):
    db = Database(tmp_path / "fresh.db")
    try:
        _assert_index(db._conn)
        db.insert_audit_log("TASK-9", "dev_agent", "same", {"n": 1})
        db.insert_audit_log("TASK-9", "dev_agent", "same", {"n": 1})
        assert len(db.get_audit_logs("TASK-9")) == 2
    finally:
        db.close()


@pytest.mark.parametrize("writer", ["committed", "uncommitted_commit", "uncommitted_rollback"])
def test_audit_task_index_reopen_preserves_rows(tmp_path, writer):
    path = tmp_path / "writer.db"
    db = Database(path)
    try:
        if writer == "committed":
            row_id = db.insert_audit_log("config:working_hours", "founder", "saved", {"n": 7})
        else:
            row_id = db.insert_audit_log_uncommitted("config:working_hours", "founder", "saved", {"n": 7})
            if writer == "uncommitted_commit":
                db.commit()
            else:
                db.rollback()
        # No initializer/other writer may commit before the first close.
        raw = tuple(db._conn.execute("SELECT * FROM audit_log WHERE id=?", (row_id,)).fetchone() or ())
    finally:
        db.close()
    for _ in range(2):
        reopened = Database(path)
        try:
            actual = [tuple(r) for r in reopened._conn.execute("SELECT * FROM audit_log")]
            if writer == "uncommitted_rollback":
                assert actual == []
            else:
                assert actual == [raw]
            _assert_index(reopened._conn)
        finally:
            reopened.close()


def test_preindex_current_database_upgrades_twice(tmp_path):
    path = tmp_path / "preindex.db"
    db = Database(path)
    db.insert_task(TaskRecord(id="TASK-history", brief="retained"))
    db.insert_audit_log("TASK-history", "dev_agent", "record", {"n": 1})
    db.insert_audit_log("artifact:asset-9", "founder", "record", {"n": 2})
    # Complete preceding layout, differing only in the newly added object.
    db._conn.execute(f"DROP INDEX IF EXISTS {INDEX}")
    db.commit()
    before = _snapshot(db)
    db.close()
    for _ in range(2):
        db = Database(path)
        try:
            _assert_index(db._conn)
            after = _snapshot(db)
            assert after[2] == before[2]
            assert [r for r in after[0] if r[1] != INDEX] == before[0]
            after_metadata = after[1]
            after_metadata["audit_log"]["indexes"] = [
                r for r in after_metadata["audit_log"]["indexes"] if r[0] != INDEX
            ]
            assert after_metadata == before[1]
            assert db._conn.execute("PRAGMA foreign_key_check").fetchall() == []
        finally:
            db.close()


def test_task_scoped_audit_query_uses_index_without_sort(tmp_path):
    db = Database(tmp_path / "plan.db")
    try:
        for n in range(100):
            db.insert_audit_log("TASK-9" if n % 5 == 0 else "TASK-90", "dev_agent", "row", {"n": n})
        statements = []
        db._conn.set_trace_callback(statements.append)
        assert len(db.get_audit_logs("TASK-9")) == 20
        db._conn.set_trace_callback(None)
        selects = [s for s in statements if s.startswith("SELECT")]
        assert selects == ["SELECT * FROM audit_log WHERE task_id = 'TASK-9' ORDER BY id"]
        plan = [r[3] for r in db._conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM audit_log WHERE task_id = ? ORDER BY id", ("TASK-9",),
        )]
        assert any("SEARCH audit_log" in r and INDEX in r for r in plan), plan
        assert not any("SCAN audit_log" in r or "TEMP B-TREE" in r for r in plan), plan
        _assert_index(db._conn)
    finally:
        db.close()


def _failed_claim(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    store.bind_v2_process_boot_id(attempt.origin_boot_id)
    real = store._db._conn
    store._db._conn = _FailingConn(real, "INSERT INTO authority_policy_v2_candidates")
    try:
        with pytest.raises(RuntimeError, match="injected write failure"):
            _claim(store, row, attempt)
    finally:
        store._db._conn = real
    assert real.execute("SELECT COUNT(*) FROM authority_policy_v2_candidates").fetchone()[0] == 0
    assert real.execute("SELECT COUNT(*) FROM authority_policy_v2_pins").fetchone()[0] == 0
    return store, row, attempt


def _dual_observation(store, row):
    error = None
    try:
        discovery = store.list_v2_unfinalized_attempts()
    except ValueError as exc:
        discovery = None
        error = str(exc)
    target = store.get_v2_housekeeping_target(
        root_task_id=TASK_ID, manager_agent=MANAGER,
        manager_session_id=SESSION_ID, result_id=row["id"],
    )
    return discovery, target, error


def _dual_read(store, row, attempt):
    discovery, target, error = _dual_observation(store, row)
    assert error is None, error
    assert len(discovery) == 1 and discovery[0].attempt_id == attempt.attempt_id
    assert target is not None and target.attempt_id == attempt.attempt_id
    assert discovery[0].candidate_id is None and target.candidate_id is None
    return discovery[0].obligation_code, target.obligation_code


OBLIGATION_INPUTS = [
    "authentic_original", "absent_obligation", "duplicate_authentic_same_root_attempt",
    "malformed_obligation_json", "malformed_other_action_same_root", "extra_payload_key",
    "wrong_result_id", "wrong_owner_attempt_id", "wrong_origin_boot_id", "unsupported_refusal_code",
    "obligation_moved_other_root", "wrong_audit_agent", "wrong_payload_attempt_id",
    "authentic_plus_duplicate_different_root",
]


@pytest.mark.parametrize("evidence", OBLIGATION_INPUTS)
def test_indexed_discovery_preserves_obligation_authentication(tmp_path, evidence):
    store, row, attempt = _failed_claim(tmp_path)
    db = store._db
    try:
        _seed_receipt(store, row["id"])
        _assert_index(db._conn)
        positive_snapshot = _snapshot(db)
        assert _dual_read(store, row, attempt) == ("claim_failed", "claim_failed")
        assert _snapshot(db) == positive_snapshot
        obligations = [r for r in db.get_audit_logs(TASK_ID) if r["action"] == "authority_policy_v2_housekeeping_obligation"]
        assert len(obligations) == 1
        original = obligations[0]
        payload = dict(original["payload"])
        if evidence == "absent_obligation":
            db._conn.execute("DELETE FROM audit_log WHERE id=?", (original["id"],))
        elif evidence in ("duplicate_authentic_same_root_attempt", "authentic_plus_duplicate_different_root"):
            db.insert_audit_log(
                TASK_ID if evidence == "duplicate_authentic_same_root_attempt" else TASK_ID + "-foreign",
                MANAGER, original["action"], payload,
            )
        elif evidence == "malformed_other_action_same_root":
            db._conn.execute(
                "INSERT INTO audit_log(task_id,agent,action,payload,timestamp) VALUES (?,?,?,?,?)",
                (TASK_ID, MANAGER, "other_action", "{broken", original["timestamp"]),
            )
        elif evidence == "obligation_moved_other_root":
            db._conn.execute("UPDATE audit_log SET task_id=? WHERE id=?", (TASK_ID + "-foreign", original["id"]))
        elif evidence == "wrong_audit_agent":
            db._conn.execute("UPDATE audit_log SET agent=? WHERE id=?", ("foreign", original["id"]))
        elif evidence != "authentic_original":
            if evidence == "extra_payload_key":
                payload["extra"] = "unexpected"
            elif evidence == "wrong_result_id":
                payload["result_id"] += 1000
            elif evidence == "unsupported_refusal_code":
                payload["refusal_code"] = "unsupported"
            elif evidence == "wrong_payload_attempt_id":
                payload["attempt_id"] = "foreign"
            elif evidence.startswith("wrong_"):
                payload[evidence.removeprefix("wrong_")] = "foreign"
            raw = "{broken" if evidence == "malformed_obligation_json" else json.dumps(payload)
            db._conn.execute("UPDATE audit_log SET payload=? WHERE id=?", (raw, original["id"]))
        db.commit()
        expected = "claim_failed" if evidence in ("authentic_original", "authentic_plus_duplicate_different_root") else None
        fixed_input = _snapshot(db)
        for observation in range(3):
            if observation:
                db.close()
                db = Database(tmp_path / "c2.db")
                store = AuthorityPolicyStore(db)
            _assert_index(db._conn)
            assert _snapshot(db) == fixed_input
            # Collect BOTH public results before assertions for attributable controls.
            discovery, target, error = _dual_observation(store, row)
            observed = (
                discovery[0].obligation_code if discovery else None,
                target.obligation_code if target is not None else None,
            )
            retained = _snapshot(db)
            print("C7", evidence, observation, "observed", observed, "discovery_error", error,
                  "target", target, "expected", (expected, expected))
            assert retained == fixed_input
            assert error is None, error
            assert discovery is not None and len(discovery) == 1 and discovery[0].attempt_id == attempt.attempt_id
            assert target is not None and target.attempt_id == attempt.attempt_id
            assert discovery[0].candidate_id is None and target.candidate_id is None
            assert observed == (expected, expected)
    finally:
        db.close()


def _corrupt_canonical_atomically(db, attempt):
    """D4: exact trigger restoration within ONE idle, exclusive transaction."""
    guard = "authority_policy_v2_attempts_finalization_guard"
    with db._lock:
        conn = db._conn
        assert not conn.in_transaction
        original = _snapshot(db)
        saved_sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (guard,)).fetchone()[0]
        assert "BEFORE UPDATE" in saved_sql and "finalization_state" in saved_sql
        # Demonstrate the unchanged normal guard; this probe is always rolled back.
        conn.execute("BEGIN")
        try:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("UPDATE authority_policy_v2_attempts SET canonical_payload_json=? WHERE attempt_id=?", ("{broken", attempt.attempt_id))
        finally:
            conn.rollback()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(f"DROP TRIGGER {guard}")
            cursor = conn.execute(
                "UPDATE authority_policy_v2_attempts SET canonical_payload_json=? WHERE attempt_id=?",
                ("{broken", attempt.attempt_id),
            )
            assert cursor.rowcount == 1
            conn.execute(saved_sql)  # executescript would implicitly commit
            restored = _snapshot(db)
            assert restored[:2] == original[:2]
            expected_rows = dict(original[2])
            columns = [r[1] for r in conn.execute("PRAGMA table_info(authority_policy_v2_attempts)")]
            index = columns.index("canonical_payload_json")
            expected_rows["authority_policy_v2_attempts"] = [
                r[:index] + ("{broken",) + r[index + 1:] for r in original[2]["authority_policy_v2_attempts"]
            ]
            assert restored[2] == expected_rows
            conn.commit()
        except BaseException:
            conn.rollback()
            assert _snapshot(db) == original
            raise
        assert _snapshot(db) == restored


@pytest.mark.parametrize("branch", ["accepted_recovery", "pid_failure", "ordinary_pending"])
def test_indexed_canonical_attempt_corruption_fences_startup_recovery(tmp_path, branch):
    store, row, attempt = _failed_claim(tmp_path)
    db = store._db
    try:
        _assert_index(db._conn)
        assert _dual_read(store, row, attempt) == ("claim_failed", "claim_failed")
        if branch == "accepted_recovery":
            _seed_receipt(store, row["id"])
        elif branch == "ordinary_pending":
            db.update_task(TASK_ID, status=TaskStatus.PENDING)
        # An otherwise eligible unrelated Pending root must also be protected.
        db.insert_task(TaskRecord(id="TASK-unrelated", brief="ordinary", status=TaskStatus.PENDING))
        _corrupt_canonical_atomically(db, attempt)
        fixed_input = _snapshot(db)
        for observation in range(3):
            if observation:
                db.close()
                db = Database(tmp_path / "c2.db")
                store = AuthorityPolicyStore(db)
            assert _snapshot(db) == fixed_input
            _assert_index(db._conn)
            discovery_error = None
            try:
                discovery = store.list_v2_unfinalized_attempts()
            except ValueError as exc:
                discovery_error = str(exc)
                discovery = None
            target = store.get_v2_housekeeping_target(root_task_id=TASK_ID, manager_agent=MANAGER, manager_session_id=SESSION_ID, result_id=row["id"])
            queue = TaskQueue()
            # Accepted receipt consumption has its maintained orchestrator
            # surface enabled; PID/Pending use supported ordinary sweep mode.
            orch = _reaper_orch(store) if branch == "accepted_recovery" else None
            if orch is not None:
                orch._queue = queue
            startup = refuse_authority_policy_v2_pre_final_on_startup(db, orchestrator=orch)
            _sweep_on_startup(db, queue, "test", orchestrator=orch)
            # All outcomes, actual sweep/queue and full persisted values precede assertions.
            retained = _snapshot(db)
            queued = list(queue._queue._queue)
            print("canonical", branch, observation, "discovery", discovery, "error", discovery_error,
                  "target", target, "startup", startup, "queue", queued)
            assert discovery_error is not None and "malformed" in discovery_error
            assert target is None and startup is None
            assert retained == fixed_input
            assert queued == []
    finally:
        db.close()
    # Positive ordinary sweep, using a separate authentic no-corruption database.
    positive = Database(tmp_path / "positive.db")
    try:
        positive.insert_task(TaskRecord(id="TASK-unrelated", brief="ordinary", status=TaskStatus.PENDING))
        queue = TaskQueue()
        _sweep_on_startup(positive, queue, "test")
        assert not queue._queue.empty()
        assert positive.get_task("TASK-unrelated").status is TaskStatus.PENDING
    finally:
        positive.close()
