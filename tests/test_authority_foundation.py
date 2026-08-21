"""THR-181 Track A Slice 1 — durable authority candidate/evaluation/audit foundation.

These are the load-bearing database/infrastructure tests for the isolated,
additive authority foundation. They prove:

  * schema creation + idempotency (reopen is a no-op);
  * DB-level append-only protections (no-update/no-delete triggers);
  * candidate identity immutability and controlled state/disposition CHECKs;
  * digest-only persistence (no prose / raw response / credential surface);
  * the deterministic, barrier-ready CAS claim contract — exactly one durable
    winner per root/session/causal-event/policy-prompt-model tuple;
  * atomic rollback on injected failure;
  * v0 (flat historical) -> v1 (DB-backed) forward migration preserving legacy
    tasks, audit scope prefixes, and overloaded-column meanings;
  * rollback/reopen and crash/reopen round-trip reconciliation.

This slice must not invoke an evaluator and must not wire anything into a
runtime surface — every assertion here is against the real ``Database``
boundary and its dedicated ``authority_*`` tables only.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path

import pytest

from runtime.infrastructure.database import AuthorityAuditMigrationRefusal, Database
from runtime.models import (
    AuthorityDisposition,
    AuthorityLifecycleState,
    TaskRecord,
    TaskStatus,
)


# ── Claim-parameter factory (synthetic, deterministic per root) ──────────

def _digest(tag: str) -> str:
    """Deterministic sha256 hex digest for a tag (valid bounded-hex digest)."""
    return hashlib.sha256(tag.encode("utf-8")).hexdigest()


def _claim_kwargs(root: str = "TASK-0001", **overrides) -> dict:
    base = dict(
        root_task_id=root,
        team="engineering",
        manager_agent="engineering_manager",
        manager_session_id="sess-0001",
        causal_event_id=f"evt-{root}",
        causal_event_digest=_digest(f"causal-{root}"),
        causal_result_id=None,
        policy_id="policy/engineering/routine-gated-follow-through",
        policy_version="v1",
        policy_digest=_digest("policy-0001"),
        prompt_id="prompt/authority-evaluator",
        prompt_version="v1",
        prompt_digest=_digest("prompt-0001"),
        model_id="model/authority-evaluator",
        model_version="v1",
        model_digest=_digest("model-0001"),
        snapshot_digest=_digest(f"snapshot-{root}"),
        fence_results={"cancellation": {"passed": True, "code": None}},
    )
    base.update(overrides)
    return base


def _claim(db: Database, **overrides) -> tuple[str, bool]:
    return db.claim_authority_candidate(**_claim_kwargs(**overrides))


# ── Schema creation / idempotency ────────────────────────────────────────

def test_authority_tables_created(db):
    tables = db.list_tables()
    for name in ("authority_candidates", "authority_evaluations", "authority_audit"):
        assert name in tables


def test_authority_tables_have_expected_columns(db):
    cols = {r[1] for r in db._conn.execute("PRAGMA table_info(authority_candidates)").fetchall()}
    # Immutable identity sufficient for later shadow mode.
    for name in (
        "id", "claim_key", "root_task_id", "team", "manager_agent",
        "manager_session_id", "causal_event_id", "causal_event_digest",
        "causal_result_id", "policy_id", "policy_version", "policy_digest",
        "prompt_id", "prompt_version", "prompt_digest",
        "model_id", "model_version", "model_digest",
        "snapshot_digest", "snapshot_retention_class",
        "snapshot_redaction_class", "fence_results_json",
        "disposition", "lifecycle_state", "consumed_at",
        "created_at", "updated_at",
    ):
        assert name in cols
    # No raw-credential / prose / raw-response columns exist anywhere.
    for forbidden in ("credential", "bearer", "prose", "response_text", "raw_response", "model_exchange"):
        assert forbidden not in cols


def test_authority_tables_idempotent_over_restart(tmp_path):
    path = tmp_path / "restart.db"
    db1 = Database(path)
    db1.close()
    db2 = Database(path)  # second open must not raise duplicate-table/index errors
    assert "authority_candidates" in db2.list_tables()
    db2.close()


# ── Append-only / immutability DB-level protections ──────────────────────

def test_authority_audit_append_only(db):
    cid, _ = _claim(db)
    db.record_authority_audit(candidate_id=cid, event_type="candidate_claimed")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db._conn.execute("UPDATE authority_audit SET event_type='candidate_consumed'")
    db._conn.rollback()
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db._conn.execute("DELETE FROM authority_audit")
    db._conn.rollback()


def test_authority_evaluations_append_only(db):
    cid, _ = _claim(db)
    db.record_authority_evaluation(
        candidate_id=cid, disposition="escalate", disposition_code="escalate",
        response_digest=_digest("response-0001"),
    )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db._conn.execute("UPDATE authority_evaluations SET disposition='continue_same_root'")
    db._conn.rollback()
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db._conn.execute("DELETE FROM authority_evaluations")
    db._conn.rollback()


def test_authority_candidates_cannot_be_deleted(db):
    _claim(db)
    with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
        db._conn.execute("DELETE FROM authority_candidates")
    db._conn.rollback()


def test_authority_candidates_identity_immutable(db):
    cid, _ = _claim(db)
    for column in ("root_task_id", "claim_key", "policy_digest", "snapshot_digest"):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db._conn.execute(
                f"UPDATE authority_candidates SET {column} = 'tampered' WHERE id = ?",
                (cid,),
            )
        db._conn.rollback()
    # The candidate is unchanged after the failed updates.
    got = db.get_authority_candidate(cid)
    assert got.root_task_id == "TASK-0001"


# ── Controlled finite state / disposition values ─────────────────────────

def test_check_constraint_rejects_unknown_lifecycle_state(db):
    with pytest.raises(sqlite3.IntegrityError, match="lifecycle_state"):
        db._conn.execute(
            """INSERT INTO authority_candidates
               (id, claim_key, root_task_id, team, manager_agent, manager_session_id,
                causal_event_id, causal_event_digest, causal_result_id,
                policy_id, policy_version, policy_digest,
                prompt_id, prompt_version, prompt_digest,
                model_id, model_version, model_digest,
                snapshot_digest, snapshot_retention_class, snapshot_redaction_class,
                lifecycle_state, created_at, updated_at)
               VALUES ('X','Y','r','t','a','s','e','d',NULL,'p','1','pd',
                       'pr','1','prd','m','1','md','sd','digest_only','redacted',
                       'bogus','now','now')""",
        )
    db._conn.rollback()


def test_check_constraint_rejects_unknown_disposition(db):
    with pytest.raises(sqlite3.IntegrityError, match="disposition"):
        db._conn.execute(
            """INSERT INTO authority_candidates
               (id, claim_key, root_task_id, team, manager_agent, manager_session_id,
                causal_event_id, causal_event_digest, causal_result_id,
                policy_id, policy_version, policy_digest,
                prompt_id, prompt_version, prompt_digest,
                model_id, model_version, model_digest,
                snapshot_digest, snapshot_retention_class, snapshot_redaction_class,
                disposition, lifecycle_state, created_at, updated_at)
               VALUES ('X','Y','r','t','a','s','e','d',NULL,'p','1','pd',
                       'pr','1','prd','m','1','md','sd','digest_only','redacted',
                       'bogus','created','now','now')""",
        )
    db._conn.rollback()


def test_record_evaluation_rejects_unknown_disposition(db):
    cid, _ = _claim(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.record_authority_evaluation(
            candidate_id=cid, disposition="bogus", disposition_code="escalate",
            response_digest=_digest("response-0001"),
        )
    # Atomic rollback: the candidate was NOT transitioned by the failed insert.
    assert db.get_authority_candidate(cid).lifecycle_state == AuthorityLifecycleState.CREATED
    assert db._conn.execute("SELECT COUNT(*) FROM authority_evaluations").fetchone()[0] == 0


# ── Immutable / digest-only payload surface ──────────────────────────────

def test_candidate_persists_digests_not_prose(db):
    cid, won = _claim(db)
    assert won is True
    got = db.get_authority_candidate(cid)
    assert got.snapshot_digest == _digest("snapshot-TASK-0001")
    assert got.causal_event_digest == _digest("causal-TASK-0001")
    assert got.policy_digest == _digest("policy-0001")
    assert got.prompt_digest == _digest("prompt-0001")
    assert got.model_digest == _digest("model-0001")
    # Structured fence results round-trip; no prose field exists on the model.
    assert got.fence_results["cancellation"].passed is True
    assert got.manager_session_id == "sess-0001"


def test_evaluation_persists_response_digest_not_raw(db):
    cid, _ = _claim(db)
    db.record_authority_evaluation(
        candidate_id=cid, disposition="continue_same_root",
        disposition_code="continue_same_root", response_digest=_digest("response-0001"),
    )
    ev = db.get_authority_evaluation(cid)
    assert ev.response_digest == _digest("response-0001")
    assert ev.disposition == AuthorityDisposition.CONTINUE_SAME_ROOT
    # The raw response is never stored — the only response column is the digest.
    eval_cols = {r[1] for r in db._conn.execute("PRAGMA table_info(authority_evaluations)").fetchall()}
    assert "response_digest" in eval_cols
    for forbidden in ("response_text", "raw_response", "model_output", "prose"):
        assert forbidden not in eval_cols


# ── CAS claim/create contract ────────────────────────────────────────────

def test_claim_key_is_deterministic(db):
    kw = _claim_kwargs()
    cid1, _ = db.claim_authority_candidate(**kw)
    cid2, _ = db.claim_authority_candidate(**kw)  # loser: same deterministic id
    assert cid1 == cid2
    assert cid1.startswith("AUTH-CAND-")


def test_second_claim_loses_and_mints_no_second_row(db):
    cid, won1 = _claim(db)
    cid2, won2 = _claim(db)
    assert won1 is True
    assert won2 is False
    assert cid == cid2
    assert db._conn.execute("SELECT COUNT(*) FROM authority_candidates").fetchone()[0] == 1


def test_distinct_tuples_mint_distinct_candidates(db):
    cid_a, _ = _claim(db, root="TASK-A", manager_session_id="sess-A")
    cid_b, _ = _claim(db, root="TASK-A", manager_session_id="sess-B")
    cid_c, _ = _claim(db, root="TASK-B", manager_session_id="sess-A")
    assert len({cid_a, cid_b, cid_c}) == 3
    assert db._conn.execute("SELECT COUNT(*) FROM authority_candidates").fetchone()[0] == 3


# ── Barrier race: exactly one durable winner ─────────────────────────────

def test_claim_cas_concurrent_threads_one_winner(tmp_path):
    """Two OS threads race the real shared-Database RLock + UNIQUE claim_key.

    MEM-063 structure: a two-thread barrier so both claims are armed before
    either lands, then both hit the CAS. Exactly one wins; exactly one durable
    candidate row exists. Never asserts incidental thread ordering.
    """
    db = Database(tmp_path / "race.db")
    barrier = threading.Barrier(2)
    results: list[tuple[str, bool]] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def claimant():
        try:
            barrier.wait(timeout=10)
            cid, won = db.claim_authority_candidate(**_claim_kwargs())
            with lock:
                results.append((cid, won))
        except BaseException as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=claimant) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"race raised {errors}"
    winners = [r for r in results if r[1] is True]
    losers = [r for r in results if r[1] is False]
    assert len(winners) == 1, f"expected exactly one winner, got {results}"
    assert len(losers) == 1, f"expected exactly one loser, got {results}"
    assert winners[0][0] == losers[0][0]  # same deterministic candidate id
    assert db._conn.execute("SELECT COUNT(*) FROM authority_candidates").fetchone()[0] == 1


def test_claim_cas_two_separate_connections_one_winner(tmp_path):
    """Two independent SQLite connections to the same file race the claim.

    This is the closest real SQLite multi-connection harness: the two
    ``Database`` instances do not share an RLock, so the UNIQUE constraint on
    ``claim_key`` is the sole arbiter. Exactly one durable winner.
    """
    path = tmp_path / "race.db"
    db_a = Database(path)
    db_b = Database(path)
    barrier = threading.Barrier(2)
    results: list[tuple[str, bool]] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def claimant(db_handle):
        try:
            barrier.wait(timeout=10)
            cid, won = db_handle.claim_authority_candidate(**_claim_kwargs())
            with lock:
                results.append((cid, won))
        except BaseException as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    threads = [
        threading.Thread(target=claimant, args=(db_a,)),
        threading.Thread(target=claimant, args=(db_b,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"separate-connection race raised {errors}"
    assert sum(1 for _, won in results if won) == 1
    assert db_a._conn.execute("SELECT COUNT(*) FROM authority_candidates").fetchone()[0] == 1


# ── Atomic rollback on injected failure ──────────────────────────────────

def test_record_evaluation_rolls_back_when_candidate_already_evaluated(db):
    cid, _ = _claim(db)
    db.record_authority_evaluation(
        candidate_id=cid, disposition="escalate", disposition_code="escalate",
        response_digest=_digest("response-0001"),
    )
    # Second evaluation is a DB-level single-evaluation violation: the UNIQUE
    # candidate_id constraint raises before the transition, so the whole
    # transaction rolls back and the candidate is unchanged.
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        db.record_authority_evaluation(
            candidate_id=cid, disposition="continue_same_root",
            disposition_code="continue_same_root", response_digest=_digest("response-0002"),
        )
    assert db._conn.execute("SELECT COUNT(*) FROM authority_evaluations").fetchone()[0] == 1
    got = db.get_authority_candidate(cid)
    assert got.lifecycle_state == AuthorityLifecycleState.EVALUATED
    assert got.disposition == AuthorityDisposition.ESCALATE  # original, not overwritten


def test_record_evaluation_missing_candidate_rolls_back(db):
    # FK constraint on candidate_id rejects an evaluation for a nonexistent
    # candidate; the transaction rolls back with no residue.
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        db.record_authority_evaluation(
            candidate_id="AUTH-CAND-nonexistent", disposition="escalate",
            disposition_code="escalate", response_digest=_digest("response-0001"),
        )
    assert db._conn.execute("SELECT COUNT(*) FROM authority_evaluations").fetchone()[0] == 0


# ── Exactly-once consumption (no extra consumption) ──────────────────────

def test_consume_is_exactly_once_and_requires_evaluation(db):
    cid, _ = _claim(db)
    # A claimed-but-never-evaluated candidate (a partial record) cannot be
    # consumed — no partial record becomes a future continuation.
    assert db.consume_authority_candidate(cid) is False
    db.record_authority_evaluation(
        candidate_id=cid, disposition="continue_same_root",
        disposition_code="continue_same_root", response_digest=_digest("response-0001"),
    )
    assert db.consume_authority_candidate(cid) is True
    assert db.consume_authority_candidate(cid) is False  # no extra consumption
    got = db.get_authority_candidate(cid)
    assert got.lifecycle_state == AuthorityLifecycleState.CONSUMED
    assert got.consumed_at is not None


# ── v0 (flat historical) -> v1 (DB-backed) forward migration ─────────────

def _seed_legacy_flat_fixture(path: Path) -> Database:
    """Build a flat historical database with legacy tasks + audit rows carrying
    scope prefixes and overloaded-column meanings, then DROP the authority
    tables so the file is a faithful pre-authority ('v0') fixture."""
    db = Database(path)
    db.insert_task(TaskRecord(
        id="TASK-HIST-1", brief="legacy root", status=TaskStatus.COMPLETED,
        revisit_of_task_id="TASK-HIST-0",
    ))
    # blocked_on_job_ids is persisted via update_task (not insert_task), which
    # mirrors the production write path for the overloaded-column meaning.
    db.update_task("TASK-HIST-1", blocked_on_job_ids=json.dumps(["JOB-1", "JOB-2"]))
    db.insert_task(TaskRecord(
        id="TASK-HIST-2", brief="legacy child", status=TaskStatus.ESCALATED,
        parent_task_id="TASK-HIST-1",
    ))
    # audit_log rows carrying the namespaced scope prefixes that must survive.
    db.insert_audit_log(task_id="TASK-HIST-1", agent="founder", action="escalation", payload={"reason": "x"})
    db.insert_audit_log(task_id="config:working_hours", agent="founder", action="org_config_write", payload={"section": "working_hours"})
    db.insert_audit_log(task_id="artifact:report.pdf", agent="founder", action="artifact_put", payload={"size": 1})
    # Simulate a v0 file: drop the authority tables (and their triggers/indexes).
    db._conn.execute("DROP TABLE IF EXISTS authority_audit")
    db._conn.execute("DROP TABLE IF EXISTS authority_evaluations")
    db._conn.execute("DROP TABLE IF EXISTS authority_candidates")
    db._conn.commit()
    db.close()
    return db


def test_v0_flat_fixture_migrates_to_v1_preserving_legacy_data(tmp_path):
    path = tmp_path / "v0.db"
    _seed_legacy_flat_fixture(path)

    # Forward migration runs on reopen.
    db = Database(path)
    assert "authority_candidates" in db.list_tables()

    # Legacy tasks survive with their overloaded-column meanings intact.
    t1 = db.get_task("TASK-HIST-1")
    assert t1 is not None and t1.status == TaskStatus.COMPLETED
    assert json.loads(t1.blocked_on_job_ids) == ["JOB-1", "JOB-2"]
    assert t1.revisit_of_task_id == "TASK-HIST-0"
    t2 = db.get_task("TASK-HIST-2")
    assert t2.status == TaskStatus.ESCALATED and t2.parent_task_id == "TASK-HIST-1"

    # Audit rows including scope prefixes are byte-preserved.
    scope_ids = {row["task_id"] for row in db._conn.execute("SELECT task_id FROM audit_log").fetchall()}
    assert "TASK-HIST-1" in scope_ids
    assert "config:working_hours" in scope_ids
    assert "artifact:report.pdf" in scope_ids

    # v1 DB-backed authority data round-trips on the migrated file.
    cid, won = _claim(db, root="TASK-HIST-1")
    assert won is True
    assert db.get_authority_candidate(cid).root_task_id == "TASK-HIST-1"


def test_rollback_and_reopen_recreates_authority_tables(tmp_path):
    path = tmp_path / "rollback.db"
    db = Database(path)
    cid, _ = _claim(db)
    db.record_authority_audit(candidate_id=cid, event_type="candidate_claimed")
    # Rollback: drop the authority surface entirely.
    db._conn.execute("DROP TABLE authority_audit")
    db._conn.execute("DROP TABLE authority_evaluations")
    db._conn.execute("DROP TABLE authority_candidates")
    db._conn.commit()
    db.close()
    # Reopen re-runs the forward migration idempotently.
    db2 = Database(path)
    assert "authority_candidates" in db2.list_tables()
    assert db2.get_authority_candidate(cid) is None  # prior authority data gone, tables fresh
    assert db2._conn.execute("SELECT COUNT(*) FROM authority_candidates").fetchone()[0] == 0


# ── Crash / reopen round-trip at each durable stage ──────────────────────

def test_crash_reopen_round_trip_at_each_durable_stage(tmp_path):
    path = tmp_path / "roundtrip.db"

    # Stage 1: claim, then "crash" (close) and reopen.
    db = Database(path)
    cid, won = _claim(db, root="TASK-ROUND")
    assert won is True
    db.close()

    db = Database(path)
    got = db.get_authority_candidate(cid)
    assert got is not None and got.lifecycle_state == AuthorityLifecycleState.CREATED

    # Stage 2: record evaluation, crash, reopen.
    db.record_authority_evaluation(
        candidate_id=cid, disposition="continue_same_root",
        disposition_code="continue_same_root", response_digest=_digest("response-0001"),
    )
    db.close()

    db = Database(path)
    got = db.get_authority_candidate(cid)
    assert got.lifecycle_state == AuthorityLifecycleState.EVALUATED
    assert got.disposition == AuthorityDisposition.CONTINUE_SAME_ROOT
    ev = db.get_authority_evaluation(cid)
    assert ev is not None and ev.response_digest == _digest("response-0001")

    # Stage 3: consume, crash, reopen.
    assert db.consume_authority_candidate(cid) is True
    db.close()

    db = Database(path)
    got = db.get_authority_candidate(cid)
    assert got.lifecycle_state == AuthorityLifecycleState.CONSUMED
    assert got.consumed_at is not None
    # Original data and new authority data remain reconciled together.
    assert db.get_authority_evaluation(cid) is not None


def test_crash_before_evaluation_leaves_no_continuable_partial_record(tmp_path):
    path = tmp_path / "partial.db"
    db = Database(path)
    cid, won = _claim(db)
    assert won is True
    db.close()  # crash immediately after claim, before evaluation

    db = Database(path)
    got = db.get_authority_candidate(cid)
    assert got is not None and got.lifecycle_state == AuthorityLifecycleState.CREATED
    # A never-evaluated candidate cannot be consumed after reopen.
    assert db.consume_authority_candidate(cid) is False
    assert db.get_authority_candidate(cid).lifecycle_state == AuthorityLifecycleState.CREATED


# ── Legacy 405697a0-shaped authority_audit (no FK) retrofit ──────────────

def _seed_legacy_authority_db(
    path: Path, *, orphan: bool = False,
) -> tuple[str, str, str]:
    """Build a faithful 405697a0-shaped authority database file.

    Candidates and evaluations are seeded through the real persistence API
    (so the created / evaluated / consumed states are realistic), then
    ``authority_audit`` is rebuilt in the pre-corrective no-FK shape and audit
    rows are inserted raw — a 405697a0 writer had no FK to enforce. Returns
    the three candidate ids in (created, evaluated, consumed) order.
    """
    db = Database(path)
    c_created, won = _claim(db, root="TASK-LEG-CREATED")
    assert won is True
    c_evaluated, won = _claim(db, root="TASK-LEG-EVALUATED")
    assert won is True
    c_consumed, won = _claim(db, root="TASK-LEG-CONSUMED")
    assert won is True
    db.record_authority_evaluation(
        candidate_id=c_evaluated, disposition="escalate",
        disposition_code="escalate", response_digest=_digest("resp-evaluated"),
    )
    db.record_authority_evaluation(
        candidate_id=c_consumed, disposition="continue_same_root",
        disposition_code="continue_same_root", response_digest=_digest("resp-consumed"),
    )
    assert db.consume_authority_candidate(c_consumed) is True

    # Rebuild authority_audit in the legacy no-FK shape (as a 405697a0 file
    # has), keeping the index + append-only triggers the legacy head created.
    db._conn.execute("DROP TABLE authority_audit")
    db._conn.executescript(
        """
        CREATE TABLE authority_audit (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id TEXT NOT NULL,
            event_type   TEXT NOT NULL
                CHECK (event_type IN
                    ('candidate_claimed','candidate_claim_lost',
                     'evaluation_recorded','candidate_consumed')),
            payload_json TEXT,
            created_at   TEXT NOT NULL
        );
        CREATE INDEX idx_authority_audit_candidate ON authority_audit(candidate_id);
        CREATE TRIGGER authority_audit_no_update
            BEFORE UPDATE ON authority_audit
            BEGIN SELECT RAISE(ABORT, 'authority audit is append-only'); END;
        CREATE TRIGGER authority_audit_no_delete
            BEFORE DELETE ON authority_audit
            BEGIN SELECT RAISE(ABORT, 'authority audit is append-only'); END;
        """
    )
    now = "2026-08-21T00:00:00+00:00"
    audit_rows = [
        (c_created, "candidate_claimed", None),
        (c_evaluated, "candidate_claimed", None),
        (c_evaluated, "evaluation_recorded",
         '{"disposition":"escalate","retention_class":"digest_only"}'),
        (c_consumed, "candidate_claimed", None),
        (c_consumed, "evaluation_recorded",
         '{"disposition":"continue_same_root","retention_class":"digest_only"}'),
        (c_consumed, "candidate_consumed", None),
    ]
    for candidate_id, event_type, payload in audit_rows:
        db._conn.execute(
            "INSERT INTO authority_audit (candidate_id, event_type, payload_json, created_at) "
            "VALUES (?, ?, ?, ?)",
            (candidate_id, event_type, payload, now),
        )
    if orphan:
        db._conn.execute(
            "INSERT INTO authority_audit (candidate_id, event_type, payload_json, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("AUTH-CAND-nonexistent", "candidate_claimed", None, now),
        )
    db._conn.commit()
    db.close()
    return c_created, c_evaluated, c_consumed


def _authority_audit_schema_snapshot(db: Database) -> dict:
    """Semantic schema snapshot of ``authority_audit`` (columns, FK, index,
    trigger names) — robust to cosmetic SQL-formatting differences between the
    fresh ``_create_authority_tables`` DDL and the retrofit DDL."""
    conn = db._conn
    cols = [
        (r["name"], r["type"], r["notnull"], r["dflt_value"], r["pk"])
        for r in conn.execute("PRAGMA table_info(authority_audit)").fetchall()
    ]
    fks = [
        (r["table"], r["from"], r["to"])
        for r in conn.execute("PRAGMA foreign_key_list(authority_audit)").fetchall()
    ]
    indexes: dict[str, list[str]] = {}
    for idx in conn.execute("PRAGMA index_list(authority_audit)").fetchall():
        if idx["origin"] == "c":
            indexes[idx["name"]] = [
                r["name"]
                for r in conn.execute(f"PRAGMA index_info({idx['name']})").fetchall()
            ]
    triggers = sorted(
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='trigger' AND tbl_name='authority_audit'"
        ).fetchall()
    )
    return {"cols": cols, "fks": fks, "indexes": indexes, "triggers": triggers}


def test_legacy_authority_audit_without_fk_migrates_preserving_rows(tmp_path):
    path = tmp_path / "legacy.db"
    c_created, c_evaluated, c_consumed = _seed_legacy_authority_db(path)

    # Reopen: the retrofit runs and adds the FK.
    db = Database(path)

    # Corrected FK now present, pointing at authority_candidates.id.
    fks = db._conn.execute("PRAGMA foreign_key_list(authority_audit)").fetchall()
    assert [(f["table"], f["from"], f["to"]) for f in fks] == [
        ("authority_candidates", "candidate_id", "id")
    ]

    # Index + append-only triggers recreated.
    triggers = {r["name"] for r in db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='authority_audit'"
    ).fetchall()}
    assert {"authority_audit_no_update", "authority_audit_no_delete"} <= triggers
    indexes = {r["name"] for r in db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='authority_audit'"
    ).fetchall()}
    assert "idx_authority_audit_candidate" in indexes

    # Row identity / order / event types / payloads retained verbatim.
    rows = db._conn.execute(
        "SELECT id, candidate_id, event_type, payload_json "
        "FROM authority_audit ORDER BY id"
    ).fetchall()
    assert [r["id"] for r in rows] == [1, 2, 3, 4, 5, 6]
    assert [r["event_type"] for r in rows] == [
        "candidate_claimed",
        "candidate_claimed", "evaluation_recorded",
        "candidate_claimed", "evaluation_recorded", "candidate_consumed",
    ]
    assert rows[2]["payload_json"] == '{"disposition":"escalate","retention_class":"digest_only"}'

    # State retained across all three lifecycle stages.
    assert db.get_authority_candidate(c_created).lifecycle_state == AuthorityLifecycleState.CREATED
    assert db.get_authority_candidate(c_evaluated).lifecycle_state == AuthorityLifecycleState.EVALUATED
    assert db.get_authority_candidate(c_evaluated).disposition == AuthorityDisposition.ESCALATE
    assert db.get_authority_candidate(c_consumed).lifecycle_state == AuthorityLifecycleState.CONSUMED
    assert db.get_authority_candidate(c_consumed).consumed_at is not None
    ev = db.get_authority_evaluation(c_evaluated)
    assert ev is not None and ev.response_digest == _digest("resp-evaluated")

    # Post-migration adversarial raw orphan INSERT fails atomically, no row.
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        db.execute(
            "INSERT INTO authority_audit (candidate_id, event_type, created_at) "
            "VALUES ('AUTH-CAND-nonexistent', 'candidate_claimed', 'now')",
        )
    db._conn.rollback()
    assert db._conn.execute("SELECT COUNT(*) FROM authority_audit").fetchone()[0] == 6

    db.close()

    # Reopen twice — idempotent (no duplicate table/index/trigger errors).
    for _ in range(2):
        reopened = Database(path)
        assert reopened._conn.execute(
            "PRAGMA foreign_key_list(authority_audit)"
        ).fetchall()
        assert reopened._conn.execute(
            "SELECT COUNT(*) FROM authority_audit"
        ).fetchone()[0] == 6
        reopened.close()


def test_retrofitted_authority_audit_schema_matches_fresh(tmp_path):
    fresh = Database(tmp_path / "fresh.db")
    fresh_snapshot = _authority_audit_schema_snapshot(fresh)
    fresh.close()

    legacy_path = tmp_path / "legacy.db"
    _seed_legacy_authority_db(legacy_path)
    migrated = Database(legacy_path)
    migrated_snapshot = _authority_audit_schema_snapshot(migrated)
    migrated.close()

    # The retrofit must reproduce the exact corrected schema (columns, FK,
    # index, trigger names) of a freshly created database — no drift.
    assert migrated_snapshot == fresh_snapshot


def test_legacy_authority_audit_orphan_refuses_atomically(tmp_path):
    path = tmp_path / "legacy_orphan.db"
    _seed_legacy_authority_db(path, orphan=True)

    with pytest.raises(AuthorityAuditMigrationRefusal):
        Database(path)

    # Old schema/data left intact for inspection — no partial rebuild.
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    assert conn.execute("PRAGMA foreign_key_list(authority_audit)").fetchall() == []
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "authority_audit__new" not in tables
    assert "authority_audit" in tables
    triggers = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='authority_audit'"
    ).fetchall()}
    assert {"authority_audit_no_update", "authority_audit_no_delete"} <= triggers
    # The orphan row is intact — not deleted, rewritten, or re-parented.
    orphan_rows = conn.execute(
        "SELECT candidate_id, event_type FROM authority_audit "
        "WHERE candidate_id='AUTH-CAND-nonexistent'"
    ).fetchall()
    assert len(orphan_rows) == 1
    assert orphan_rows[0]["event_type"] == "candidate_claimed"
    # Total row count unchanged (6 valid + 1 orphan).
    assert conn.execute("SELECT COUNT(*) FROM authority_audit").fetchone()[0] == 7
    conn.close()

    # Deterministic: a second reopen refuses identically (no partial state).
    with pytest.raises(AuthorityAuditMigrationRefusal):
        Database(path)


def test_legacy_authority_audit_migration_failure_rolls_back_and_reruns(
    tmp_path, monkeypatch,
):
    path = tmp_path / "legacy_fail.db"
    c_consumed = _seed_legacy_authority_db(path)[2]

    real_connect = sqlite3.connect

    class _FailingConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if isinstance(sql, str) and "DROP TABLE authority_audit" in sql:
                raise RuntimeError("injected migration failure")
            return super().execute(sql, *args, **kwargs)

    def _connect(*args, **kwargs):
        kwargs["factory"] = _FailingConnection
        return real_connect(*args, **kwargs)

    with monkeypatch.context() as m:
        m.setattr("runtime.infrastructure.database.sqlite3.connect", _connect)
        with pytest.raises(RuntimeError, match="injected migration failure"):
            Database(path)

    # Patch undone. Inspect the file: no partial replacement, source intact.
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "authority_audit__new" not in tables
    assert "authority_audit" in tables
    # Source rows preserved; FK still absent (migration did not complete).
    assert conn.execute("SELECT COUNT(*) FROM authority_audit").fetchone()[0] == 6
    assert conn.execute("PRAGMA foreign_key_list(authority_audit)").fetchall() == []
    conn.close()

    # Rerun (normal connection) succeeds and preserves the durable state.
    db = Database(path)
    assert db._conn.execute("PRAGMA foreign_key_list(authority_audit)").fetchall()
    assert db._conn.execute("SELECT COUNT(*) FROM authority_audit").fetchone()[0] == 6
    assert db.get_authority_candidate(c_consumed).lifecycle_state == AuthorityLifecycleState.CONSUMED
    db.close()


# ── Defect A: strict typed closed records reject smuggled content ─────────

def test_claim_rejects_bearer_secret_with_no_residue(db):
    with pytest.raises(ValueError):
        _claim(db, snapshot_digest="Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature")
    assert db._conn.execute("SELECT COUNT(*) FROM authority_candidates").fetchone()[0] == 0


def test_claim_rejects_task_prose_with_no_residue(db):
    with pytest.raises(ValueError):
        _claim(db, snapshot_digest="The task escalated because the manager asked for a review.")
    assert db._conn.execute("SELECT COUNT(*) FROM authority_candidates").fetchone()[0] == 0


def test_evaluation_rejects_model_exchange_with_no_residue(db):
    cid, _ = _claim(db)
    with pytest.raises(ValueError):
        db.record_authority_evaluation(
            candidate_id=cid,
            disposition="escalate",
            disposition_code="escalate",
            response_digest='[{"role":"user","content":"what now"}]',
        )
    assert db._conn.execute("SELECT COUNT(*) FROM authority_evaluations").fetchone()[0] == 0
    assert db.get_authority_candidate(cid).lifecycle_state == AuthorityLifecycleState.CREATED


def test_claim_rejects_unknown_fence_code_with_no_residue(db):
    with pytest.raises(ValueError):
        _claim(db, fence_results={"cancellation": {"passed": True, "code": "bogus_code"}})
    assert db._conn.execute("SELECT COUNT(*) FROM authority_candidates").fetchone()[0] == 0


def test_claim_rejects_unknown_fence_field_with_no_residue(db):
    with pytest.raises(ValueError):
        _claim(db, fence_results={"cancellation": {"passed": True, "unexpected": 1}})
    assert db._conn.execute("SELECT COUNT(*) FROM authority_candidates").fetchone()[0] == 0


def test_claim_accepts_known_fence_code(db):
    cid, won = _claim(db, fence_results={"cancellation": {"passed": False, "code": "cancelled"}})
    assert won is True
    got = db.get_authority_candidate(cid)
    assert got.fence_results["cancellation"].passed is False
    assert got.fence_results["cancellation"].code.value == "cancelled"


def test_audit_rejects_nested_payload_with_no_residue(db):
    cid, _ = _claim(db)
    with pytest.raises(ValueError):
        db.record_authority_audit(
            candidate_id=cid, event_type="candidate_claimed",
            payload={"nested": {"arbitrary": "json"}},
        )
    with pytest.raises(ValueError):
        db.record_authority_audit(
            candidate_id=cid, event_type="candidate_claimed",
            payload={"digest": {"nested": "json"}},
        )
    assert db._conn.execute("SELECT COUNT(*) FROM authority_audit").fetchone()[0] == 0


# ── Defect B: DB-level lifecycle enforcement blocks raw-SQL fabrication ──

def test_raw_sql_cannot_skip_to_consumed_without_evaluation(db):
    cid, _ = _claim(db)
    with pytest.raises(sqlite3.IntegrityError, match="lifecycle"):
        db.execute(
            "UPDATE authority_candidates SET lifecycle_state='consumed', consumed_at='now' WHERE id=?",
            (cid,),
        )
    db._conn.rollback()
    assert db.get_authority_candidate(cid).lifecycle_state == AuthorityLifecycleState.CREATED


def test_raw_sql_cannot_fabricate_evaluated_without_evaluation_row(db):
    cid, _ = _claim(db)
    with pytest.raises(sqlite3.IntegrityError, match="lifecycle"):
        db.execute(
            "UPDATE authority_candidates SET lifecycle_state='evaluated', disposition='escalate' WHERE id=?",
            (cid,),
        )
    db._conn.rollback()
    assert db.get_authority_candidate(cid).lifecycle_state == AuthorityLifecycleState.CREATED
    assert db._conn.execute("SELECT COUNT(*) FROM authority_evaluations").fetchone()[0] == 0


def test_raw_sql_cannot_transition_backward(db):
    cid, _ = _claim(db)
    db.record_authority_evaluation(
        candidate_id=cid, disposition="escalate", disposition_code="escalate",
        response_digest=_digest("response-0001"),
    )
    with pytest.raises(sqlite3.IntegrityError, match="lifecycle"):
        db.execute("UPDATE authority_candidates SET lifecycle_state='created' WHERE id=?", (cid,))
    db._conn.rollback()
    assert db.get_authority_candidate(cid).lifecycle_state == AuthorityLifecycleState.EVALUATED


def test_raw_sql_cannot_mutate_disposition_after_evaluation(db):
    cid, _ = _claim(db)
    db.record_authority_evaluation(
        candidate_id=cid, disposition="escalate", disposition_code="escalate",
        response_digest=_digest("response-0001"),
    )
    with pytest.raises(sqlite3.IntegrityError, match="lifecycle"):
        db.execute(
            "UPDATE authority_candidates SET disposition='continue_same_root' WHERE id=?", (cid,)
        )
    db._conn.rollback()
    assert db.get_authority_candidate(cid).disposition == AuthorityDisposition.ESCALATE


def test_valid_api_round_trip_passes_lifecycle_guard(db):
    cid, won = _claim(db)
    assert won is True
    db.record_authority_evaluation(
        candidate_id=cid, disposition="continue_same_root",
        disposition_code="continue_same_root", response_digest=_digest("response-0001"),
    )
    assert db.get_authority_candidate(cid).lifecycle_state == AuthorityLifecycleState.EVALUATED
    assert db.consume_authority_candidate(cid) is True
    assert db.get_authority_candidate(cid).lifecycle_state == AuthorityLifecycleState.CONSUMED


# ── Defect C: audit candidate attribution is FK + API enforced ───────────

def test_audit_rejects_missing_candidate_and_leaves_no_orphan(db):
    # API validation rejects a missing candidate before any INSERT.
    with pytest.raises(ValueError):
        db.record_authority_audit(candidate_id="AUTH-CAND-nonexistent", event_type="candidate_claimed")
    # DB-level FK rejects the same fabrication through a raw execute.
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        db.execute(
            "INSERT INTO authority_audit (candidate_id, event_type, created_at) "
            "VALUES ('AUTH-CAND-nonexistent', 'candidate_claimed', 'now')",
        )
    db._conn.rollback()
    assert db._conn.execute("SELECT COUNT(*) FROM authority_audit").fetchone()[0] == 0


def test_audit_valid_append_only_still_works(db):
    cid, _ = _claim(db)
    db.record_authority_audit(candidate_id=cid, event_type="candidate_claimed")
    db.record_authority_audit(
        candidate_id=cid, event_type="evaluation_recorded",
        payload={"disposition": "escalate", "retention_class": "digest_only"},
    )
    rows = db.list_authority_audit(cid)
    assert len(rows) == 2
    assert rows[0].event_type.value == "candidate_claimed"
    assert rows[1].payload.disposition == AuthorityDisposition.ESCALATE
    assert rows[1].payload.retention_class.value == "digest_only"
