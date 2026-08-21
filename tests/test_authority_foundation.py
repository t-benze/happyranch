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

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from runtime.infrastructure.database import Database
from runtime.models import (
    AuthorityDisposition,
    AuthorityLifecycleState,
    TaskRecord,
    TaskStatus,
)


# ── Claim-parameter factory (synthetic, deterministic per root) ──────────

def _claim_kwargs(root: str = "TASK-0001", **overrides) -> dict:
    base = dict(
        root_task_id=root,
        team="engineering",
        manager_agent="engineering_manager",
        manager_session_id="sess-0001",
        causal_event_id=f"evt-{root}",
        causal_event_digest=f"digest-{root}",
        causal_result_id=None,
        policy_id="policy/engineering/routine-gated-follow-through",
        policy_version="v1",
        policy_digest="policy-digest-0001",
        prompt_id="prompt/authority-evaluator",
        prompt_version="v1",
        prompt_digest="prompt-digest-0001",
        model_id="model/authority-evaluator",
        model_version="v1",
        model_digest="model-digest-0001",
        snapshot_digest="snapshot-digest-0001",
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
        response_digest="response-digest-0001",
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
            response_digest="response-digest-0001",
        )
    # Atomic rollback: the candidate was NOT transitioned by the failed insert.
    assert db.get_authority_candidate(cid).lifecycle_state == AuthorityLifecycleState.CREATED
    assert db._conn.execute("SELECT COUNT(*) FROM authority_evaluations").fetchone()[0] == 0


# ── Immutable / digest-only payload surface ──────────────────────────────

def test_candidate_persists_digests_not_prose(db):
    cid, won = _claim(db)
    assert won is True
    got = db.get_authority_candidate(cid)
    assert got.snapshot_digest == "snapshot-digest-0001"
    assert got.causal_event_digest == "digest-TASK-0001"
    assert got.policy_digest == "policy-digest-0001"
    assert got.prompt_digest == "prompt-digest-0001"
    assert got.model_digest == "model-digest-0001"
    # Structured fence results round-trip; no prose field exists on the model.
    assert got.fence_results["cancellation"].passed is True
    assert got.manager_session_id == "sess-0001"


def test_evaluation_persists_response_digest_not_raw(db):
    cid, _ = _claim(db)
    db.record_authority_evaluation(
        candidate_id=cid, disposition="continue_same_root",
        disposition_code="continue_same_root", response_digest="response-digest-0001",
    )
    ev = db.get_authority_evaluation(cid)
    assert ev.response_digest == "response-digest-0001"
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
        response_digest="response-digest-0001",
    )
    # Second evaluation is a DB-level single-evaluation violation: the UNIQUE
    # candidate_id constraint raises before the transition, so the whole
    # transaction rolls back and the candidate is unchanged.
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        db.record_authority_evaluation(
            candidate_id=cid, disposition="continue_same_root",
            disposition_code="continue_same_root", response_digest="response-digest-0002",
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
            disposition_code="escalate", response_digest="response-digest-0001",
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
        disposition_code="continue_same_root", response_digest="response-digest-0001",
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
        disposition_code="continue_same_root", response_digest="response-digest-0001",
    )
    db.close()

    db = Database(path)
    got = db.get_authority_candidate(cid)
    assert got.lifecycle_state == AuthorityLifecycleState.EVALUATED
    assert got.disposition == AuthorityDisposition.CONTINUE_SAME_ROOT
    ev = db.get_authority_evaluation(cid)
    assert ev is not None and ev.response_digest == "response-digest-0001"

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
