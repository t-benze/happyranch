"""THR-229 checkpoint C3b — durable v2 candidate/pin claim and claim-audit stages.

Focused isolated evidence for the accepted C3b radius, driving the REAL
``Database``/``AuthorityPolicyStore`` seam (no copies of its logic):

  * one atomic first transaction creates the candidate (K) + pin (P) and
    advances the admitted attempt (J) to ``claimed`` — never the separate a1;
  * a second transaction appends exactly one candidate claim event (a1) plus
    the ``claim_audited`` result-stage evidence and advances J atomically;
  * a failure in either transaction leaves the exact R4 residue;
  * only the original uninterrupted live owner may advance; a reopen, fresh
    boot, changed owner/session, missing/mutated audit or exception refuses;
  * mechanical eligibility, identity integrity and pinned-history negative
    cases refuse with the prior rows preserved;
  * schema corruption/drift before the claim refuses.
"""
from __future__ import annotations

import json

import pytest

from runtime.infrastructure.database import Database
from runtime.models import (
    AuthorityPolicyV2Attempt,
    AuthorityPolicyV2Candidate,
    AuthorityPolicyV2Pin,
    TaskRecord,
    TaskStatus,
    authority_policy_v2_candidate_claim_preimage,
    authority_policy_v2_causal_result_digest,
    authority_policy_v2_contract_digest,
    authority_policy_v2_sha256,
)
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
from tests.test_authority_v2_attempt_admission import (
    MANAGER,
    SESSION_ID,
    TASK_ID,
    TEAM,
    _admit,
    _carrier_and_admission,
    _seed_bound_task,
    _store,
)


def _counts(db: Database) -> dict:
    def count(table: str) -> int:
        return db._conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]

    return {
        "results": count("task_results"),
        "attempts": count("authority_policy_v2_attempts"),
        "candidates": count("authority_policy_v2_candidates"),
        "pins": count("authority_policy_v2_pins"),
        "candidate_audit": count("authority_policy_v2_candidate_audit"),
    }


def _admitted(tmp_path, *, task_id: str = TASK_ID, session_id: str = SESSION_ID):
    store = _store(tmp_path)
    binding = _seed_bound_task(store, task_id=task_id, session_id=session_id)
    carrier, admission = _carrier_and_admission(binding)
    assert _admit(store, carrier, admission) is True
    row = store._db.get_latest_task_result(task_id, MANAGER, session_id)
    attempt = store._db.get_authority_policy_v2_attempt_for_result(row["id"])
    assert attempt is not None
    return store, binding, carrier, admission, row, attempt


def _claim(store, row, attempt, *, task_id: str = TASK_ID, session_id: str = SESSION_ID, **kw):
    kwargs = dict(
        root_task_id=task_id, manager_agent=MANAGER, manager_session_id=session_id,
        result_id=row["id"], origin_boot_id=attempt.origin_boot_id,
        owner_attempt_id=attempt.owner_attempt_id, max_revise_rounds=0,
    )
    kwargs.update(kw)
    return store.claim_v2_candidate(**kwargs)


def _audit(store, row, attempt, *, task_id: str = TASK_ID, session_id: str = SESSION_ID, **kw):
    kwargs = dict(
        root_task_id=task_id, manager_agent=MANAGER, manager_session_id=session_id,
        result_id=row["id"], origin_boot_id=attempt.origin_boot_id,
        owner_attempt_id=attempt.owner_attempt_id,
    )
    kwargs.update(kw)
    return store.audit_v2_candidate_claim(**kwargs)


def _raw_stage(store, result_id: int) -> str:
    return store._db._conn.execute(
        "SELECT stage FROM authority_policy_v2_attempts WHERE result_id=?",
        (result_id,),
    ).fetchone()[0]


def _stage_audits(store, task_id: str = TASK_ID) -> list[str]:
    return [
        row["payload"]["stage"]
        for row in store._db.list_authority_policy_v2_result_stage_audits(
            root_task_id=task_id, manager_agent=MANAGER,
        )
    ]


# ── happy path: two separate commits ─────────────────────────────────────


def test_claim_then_audit_creates_candidate_pin_and_stages(tmp_path):
    store, binding, _, _, row, attempt = _admitted(tmp_path)
    before = _counts(store._db)

    claimed = _claim(store, row, attempt)
    assert claimed.status == "claimed"
    assert claimed.attempt_id == attempt.attempt_id
    assert claimed.candidate_id and claimed.claim_key
    assert claimed.stage == "claimed"

    # K/P exist; NO a1 and NO claim_audited result-stage evidence yet.
    candidate = store.get_v2_candidate(claimed.candidate_id)
    pin = store.get_v2_pin(claimed.candidate_id)
    assert candidate is not None and pin is not None
    assert candidate.attempt_id == attempt.attempt_id
    assert candidate.result_id == row["id"]
    assert candidate.claim_key == claimed.claim_key
    assert candidate.candidate_id == f"APV2C-{candidate.claim_key}"
    assert candidate.causal_result_digest == authority_policy_v2_causal_result_digest(row["id"])
    assert candidate.contract_digest == authority_policy_v2_contract_digest()
    assert candidate.release_id == binding["release_id"]
    assert candidate.activation_id == binding["activation_id"]
    assert candidate.activation_epoch == binding["selector_epoch"]
    assert candidate.selector_id == binding["selector_id"]
    assert candidate.provider_id == binding["provider_id"]
    assert candidate.executor_kind == binding["executor_kind"]
    assert candidate.model_id == binding["model_id"]
    assert candidate.preimage() == authority_policy_v2_candidate_claim_preimage(
        activation_id=binding["activation_id"],
        activation_selector_epoch=binding["selector_epoch"],
        causal_result_digest=authority_policy_v2_causal_result_digest(row["id"]),
        causal_result_id=row["id"], contract_digest=binding["contract_digest"],
        executor_kind=binding["executor_kind"], manager_agent=MANAGER,
        manager_session_id=SESSION_ID, model_id=binding["model_id"],
        policy_digest=binding["policy_digest"], policy_version=binding["policy_version"],
        provider_id=binding["provider_id"], release_id=binding["release_id"],
        root_task_id=TASK_ID, team=TEAM,
    )
    assert pin.pin_id == candidate.candidate_id
    assert store.list_v2_candidate_audits(claimed.candidate_id) == []
    assert _stage_audits(store) == ["admitted"]
    assert store._db.get_authority_policy_v2_attempt_for_result(row["id"]).stage == "claimed"
    after_claim = _counts(store._db)
    assert after_claim["candidates"] == before["candidates"] + 1
    assert after_claim["pins"] == before["pins"] + 1
    assert after_claim["candidate_audit"] == before["candidate_audit"]
    # C3c added the approved evaluations (V) table on this same unmerged PR, but
    # the claim stage writes no evaluation row: V stays empty.  C3d2 has since
    # added the three remaining approved additive tables (envelope/notification/
    # root-dispatch), but the claim stage mints NO continuation: every one stays
    # empty and no E/N/D row exists after claim.
    created_tables = {
        row[0] for row in store._db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "authority_policy_v2_evaluations" in created_tables
    assert store._db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_v2_evaluations"
    ).fetchone()[0] == 0
    for table in (
        "authority_policy_v2_continue_envelopes",
        "authority_policy_v2_recovery_notifications",
        "authority_policy_v2_root_dispatch",
    ):
        assert table in created_tables
        assert store._db._conn.execute(
            f'SELECT COUNT(*) FROM "{table}"'
        ).fetchone()[0] == 0
    # Task/current session/receipt preserved.
    task = store._db.get_task(TASK_ID)
    assert task.status is TaskStatus.IN_PROGRESS
    assert task.current_session_id == SESSION_ID
    assert store._db.get_task_results(TASK_ID)[0]["id"] == row["id"]

    audited = _audit(store, row, attempt)
    assert audited.status == "claim_audited"
    assert audited.candidate_id == candidate.candidate_id
    assert store._db.get_authority_policy_v2_attempt_for_result(row["id"]).stage == "claim_audited"
    audits = store.list_v2_candidate_audits(candidate.candidate_id)
    assert len(audits) == 1
    assert audits[0]["event"] == "claimed"
    assert audits[0]["attempt_id"] == attempt.attempt_id
    assert audits[0]["result_id"] == row["id"]
    assert audits[0]["claim_key"] == candidate.claim_key
    assert json.loads(audits[0]["canonical_payload_json"])["event"] == "claimed"
    assert _stage_audits(store) == ["admitted", "claim_audited"]


def test_claim_and_audit_are_separate_transactions(tmp_path, monkeypatch):
    """A failed second transaction keeps K/P and the claimed stage (R4)."""
    store, _, _, _, row, attempt = _admitted(tmp_path)
    assert _claim(store, row, attempt).status == "claimed"
    before = _counts(store._db)

    def boom(*args, **kwargs):
        raise RuntimeError("audit sink down")

    monkeypatch.setattr(store._db, "insert_audit_log_uncommitted", boom)
    with pytest.raises(RuntimeError):
        _audit(store, row, attempt)

    after = _counts(store._db)
    assert after["candidates"] == before["candidates"] == 1
    assert after["pins"] == before["pins"] == 1
    assert after["candidate_audit"] == 0
    assert store._db.get_authority_policy_v2_attempt_for_result(row["id"]).stage == "claimed"
    assert _stage_audits(store) == ["admitted"]


# ── duplicate/loser discipline ───────────────────────────────────────────


def test_duplicate_claim_after_success_is_noop_refusal(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    assert _claim(store, row, attempt).status == "claimed"
    before = _counts(store._db)
    again = _claim(store, row, attempt)
    assert again.status == "refused" and again.refusal_code == "already_claimed"
    assert _counts(store._db) == before
    # A duplicate did not poison the winning owner: the audit still advances.
    assert _audit(store, row, attempt).status == "claim_audited"


def test_claim_after_claim_audited_refuses(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    assert _claim(store, row, attempt).status == "claimed"
    assert _audit(store, row, attempt).status == "claim_audited"
    before = _counts(store._db)
    again = _claim(store, row, attempt)
    assert again.status == "refused" and again.refusal_code == "already_claimed"
    assert _counts(store._db) == before


def test_duplicate_claim_audit_refuses(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    assert _claim(store, row, attempt).status == "claimed"
    assert _audit(store, row, attempt).status == "claim_audited"
    before = _counts(store._db)
    again = _audit(store, row, attempt)
    assert again.status == "refused" and again.refusal_code == "already_audited"
    assert _counts(store._db) == before
    assert len(store.list_v2_candidate_audits(
        store.get_v2_candidate_for_result(row["id"]).candidate_id)) == 1


def test_claim_without_claim_stage_audits_refused(tmp_path):
    """The second transaction requires the first (claim_audit_missing)."""
    store, _, _, _, row, attempt = _admitted(tmp_path)
    before = _counts(store._db)
    outcome = _audit(store, row, attempt)
    assert outcome.status == "refused"
    assert outcome.refusal_code == "claim_audit_missing"
    assert _counts(store._db) == before


# ── owner/liveness/identity negatives ────────────────────────────────────


def test_claim_with_wrong_owner_token_refuses_without_rows(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    before = _counts(store._db)
    outcome = _claim(store, row, attempt, owner_attempt_id="not-the-owner")
    assert outcome.status == "refused" and outcome.refusal_code == "owner_lost"
    assert _counts(store._db) == before


def test_claim_with_wrong_boot_refuses(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    outcome = _claim(store, row, attempt, origin_boot_id="boot-somewhere-else")
    assert outcome.status == "refused" and outcome.refusal_code == "owner_lost"


def test_reopened_database_cannot_advance(tmp_path):
    db_path = tmp_path / "reopen.db"
    first = AuthorityPolicyStore(Database(db_path))
    binding = _seed_bound_task(first)
    carrier, admission = _carrier_and_admission(binding)
    assert _admit(first, carrier, admission) is True
    row = first._db.get_latest_task_result(TASK_ID, MANAGER, SESSION_ID)
    attempt = first._db.get_authority_policy_v2_attempt_for_result(row["id"])

    reopened = AuthorityPolicyStore(Database(db_path))
    # Readable without authorization to advance.
    assert reopened.get_v2_attempt_for_result(row["id"]) is not None
    outcome = _claim(reopened, row, attempt)
    assert outcome.status == "refused" and outcome.refusal_code == "owner_lost"
    assert reopened._db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_v2_candidates"
    ).fetchone()[0] == 0


def test_missing_admitted_audit_refuses_claim(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    store._db._conn.execute(
        "DELETE FROM audit_log WHERE action='authority_policy_v2_result_stage'"
    )
    store._db._conn.commit()
    outcome = _claim(store, row, attempt)
    assert outcome.status == "refused" and outcome.refusal_code == "identity_mismatch"
    assert _counts(store._db)["candidates"] == 0


def test_duplicated_admitted_audit_refuses_claim(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    audits = store._db.list_authority_policy_v2_result_stage_audits(
        root_task_id=TASK_ID, manager_agent=MANAGER,
    )
    store._db.insert_audit_log_uncommitted(
        TASK_ID, MANAGER, "authority_policy_v2_result_stage", dict(audits[0]["payload"]),
    )
    store._db.commit()
    outcome = _claim(store, row, attempt)
    assert outcome.status == "refused" and outcome.refusal_code == "identity_mismatch"


def test_mutated_result_body_refuses_claim(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    store._db._conn.execute(
        """UPDATE task_results SET decision_json=json_set(
               decision_json, '$._manager_self_evaluation.what_to_escalate.confidence', 1)
           WHERE id=?""",
        (row["id"],),
    )
    store._db._conn.commit()
    outcome = _claim(store, row, attempt)
    assert outcome.status == "refused" and outcome.refusal_code == "identity_mismatch"
    assert _counts(store._db)["candidates"] == 0


def test_wrong_root_or_session_tuple_refuses(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    assert _claim(store, row, attempt, session_id="sess-other").refusal_code in {
        "identity_mismatch", "owner_lost",
    }
    assert _claim(store, row, attempt, task_id="TASK-OTHER").refusal_code == "identity_mismatch"
    assert _claim(store, row, attempt, result_id=row["id"] + 999).refusal_code == "identity_mismatch"
    assert _counts(store._db)["candidates"] == 0


# ── mechanical eligibility at the claim boundary ─────────────────────────


def _set_task(store, **columns):
    sets = ", ".join(f"{key}=?" for key in columns)
    store._db._conn.execute(
        f"UPDATE tasks SET {sets} WHERE id=?", (*columns.values(), TASK_ID),
    )
    store._db._conn.commit()


def test_cancelled_task_refuses_claim(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    _set_task(store, cancelled_at="2026-09-20T00:00:00+00:00")
    outcome = _claim(store, row, attempt)
    assert outcome.status == "refused" and outcome.refusal_code == "cancelled"
    assert _counts(store._db)["candidates"] == 0


def test_terminal_status_refuses_claim(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    _set_task(store, status="completed")
    assert _claim(store, row, attempt).refusal_code == "cancelled"


def test_blocked_task_refuses_claim(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    _set_task(store, block_kind="jobs")
    assert _claim(store, row, attempt).refusal_code == "cancelled"


def test_replaced_owner_session_refuses_claim(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    _set_task(store, current_session_id="sess-replacement")
    outcome = _claim(store, row, attempt)
    assert outcome.status == "refused" and outcome.refusal_code == "owner_lost"
    assert store._db.get_task(TASK_ID).current_session_id == "sess-replacement"


@pytest.mark.parametrize(
    "column,value",
    [
        ("revisit_of_task_id", "TASK-PARENT"),
        ("active_chain", "[]"),
        ("active_fanout", "{}"),
        ("blocked_on_job_ids", "[]"),
    ],
)
def test_lineage_chain_fanout_job_negatives_refuse(tmp_path, column, value):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    _set_task(store, **{column: value})
    outcome = _claim(store, row, attempt)
    assert outcome.status == "refused" and outcome.refusal_code == "claim_failed"
    assert _counts(store._db)["candidates"] == 0


def test_successor_root_refuses_claim(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    store._db.insert_task(TaskRecord(
        id="TASK-PRED", status=TaskStatus.COMPLETED, assigned_agent=MANAGER,
        team=TEAM, brief="predecessor", orchestration_step_count=1,
    ))
    store._db._conn.execute(
        """INSERT INTO manager_supersessions
           (predecessor_task_id, successor_task_id, original_root_task_id,
            actor_agent, actor_session_id, rationale, attestation_evidence,
            predecessor_brief, successor_brief, predecessor_brief_sha256,
            successor_brief_sha256, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("TASK-PRED", TASK_ID, "TASK-PRED", MANAGER, "sess-pred", "supersede",
         "evidence", "predecessor", "successor", "a" * 64, "b" * 64,
         "2026-09-20T00:00:00+00:00"),
    )
    store._db._conn.commit()
    outcome = _claim(store, row, attempt)
    assert outcome.status == "refused" and outcome.refusal_code == "claim_failed"


def test_exhausted_budget_refuses_claim(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    _set_task(store, revision_count=3)
    outcome = _claim(store, row, attempt, max_revise_rounds=3)
    assert outcome.status == "refused" and outcome.refusal_code == "claim_failed"
    assert _counts(store._db)["candidates"] == 0
    # A fresh admitted attempt under the cap is eligible.
    store2, _, _, _, row2, attempt2 = _admitted(tmp_path / "under-cap")
    _set_task(store2, revision_count=3)
    assert _claim(store2, row2, attempt2, max_revise_rounds=4).status == "claimed"


def test_adverse_review_and_partial_work_do_not_veto_claim(tmp_path):
    """Adverse-review/partial-work diagnostics are not a v2 policy veto."""
    store, _, _, _, row, attempt = _admitted(tmp_path)
    store._db.insert_task(TaskRecord(
        id="TASK-CHILD", status=TaskStatus.IN_PROGRESS, assigned_agent="dev_agent",
        team=TEAM, brief="child", orchestration_step_count=1,
        parent_task_id=TASK_ID,
    ))
    store._db._conn.execute(
        """INSERT INTO task_results (task_id, agent, session_id, output_summary,
               confidence_score, status, verdict, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        ("TASK-CHILD", "dev_agent", "sess-child", "adverse", 80, "completed",
         "REQUEST_CHANGES", "2026-09-20T00:00:00+00:00"),
    )
    _set_task(store, zombie_flagged_at="2026-09-20T00:00:00+00:00")
    store._db._conn.commit()
    assert _claim(store, row, attempt).status == "claimed"


# ── pinned history / mixed family ────────────────────────────────────────


def test_later_legitimate_activation_does_not_invalidate_pin(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    assert _claim(store, row, attempt).status == "claimed"
    candidate_id = store.get_v2_candidate_for_result(row["id"]).candidate_id

    selector = store.get_authority_selector(TEAM)
    store.create_and_activate_v2({
        "team": TEAM, "policy_id": "engineering-dual-text-second", "title": "Second",
        "create_request_id": "c3b-create-2", "activation_request_id": "c3b-activate-2",
        "based_on_selector_id": selector.selector_id,
        "expected_selector_id": selector.selector_id,
        "action": "activate", "what_to_escalate": "Escalate new.",
        "what_not_to_escalate": "Continue new.",
    })
    # The pin still authenticates through its own pinned epoch.
    assert store.get_v2_candidate(candidate_id) is not None
    assert store.get_v2_pin(candidate_id) is not None
    assert _audit(store, row, attempt).status == "claim_audited"


def test_corrupt_pinned_selector_history_refuses(tmp_path, monkeypatch):
    """A pinned selector that cannot be authenticated through its own epoch is
    a refusal with no fallback to today's policy and no invented authority.

    Real history rows are append-only and protected by immutable triggers, so
    the pinned reader is the single authentication seam: an unauthenticated
    pinned read (the observable result of corrupt pinned history) must refuse.
    """
    store, _, _, _, row, attempt = _admitted(tmp_path)
    monkeypatch.setattr(
        store._db, "get_authority_policy_selector_by_id",
        lambda team, selector_id: None,
    )
    outcome = _claim(store, row, attempt)
    assert outcome.status == "refused"
    assert outcome.refusal_code == "identity_mismatch"
    assert _counts(store._db)["candidates"] == 0
    assert _counts(store._db)["pins"] == 0


# ── schema drift ─────────────────────────────────────────────────────────


def test_schema_drift_before_claim_refuses(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    store._db._conn.execute("DROP TRIGGER authority_policy_v2_candidates_no_delete")
    store._db._conn.commit()
    outcome = _claim(store, row, attempt)
    assert outcome.status == "refused" and outcome.refusal_code == "schema_drift"
    assert _counts(store._db)["candidates"] == 0


def test_schema_drift_between_capture_and_claim_refuses(tmp_path, monkeypatch):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    from runtime.orchestrator import authority as authority_mod

    original = authority_mod.recheck_authority_policy_v2_schema_integrity

    def drift(evidence, db):
        # An independent connection commits legal-but-different DDL after the
        # capture but before the claim transaction commits.
        db._conn.execute("CREATE TABLE c3b_drift_marker (id INTEGER PRIMARY KEY)")
        db._conn.commit()
        return original(evidence, db)

    monkeypatch.setattr(
        authority_mod, "recheck_authority_policy_v2_schema_integrity", drift,
    )
    outcome = _claim(store, row, attempt)
    assert outcome.status == "refused" and outcome.refusal_code == "schema_drift"
    assert _counts(store._db)["candidates"] == 0


# ── injected failure at every write boundary ─────────────────────────────


class _FailingConn:
    def __init__(self, real, needle: str):
        self._real = real
        self._needle = needle

    def execute(self, sql, *args, **kwargs):
        if self._needle in sql:
            raise RuntimeError("injected write failure")
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _inject(store, needle: str):
    store._db._conn = _FailingConn(store._db._conn, needle)


@pytest.mark.parametrize(
    "needle",
    [
        "INSERT INTO authority_policy_v2_candidates",
        "INSERT INTO authority_policy_v2_pins",
        "UPDATE authority_policy_v2_attempts",
    ],
)
def test_claim_transaction_failure_residue(tmp_path, needle):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    _inject(store, needle)
    with pytest.raises(RuntimeError):
        _claim(store, row, attempt)
    store._db._conn = store._db._conn._real
    counts = _counts(store._db)
    assert counts["candidates"] == 0 and counts["pins"] == 0
    assert counts["candidate_audit"] == 0
    # J stays admitted with a0; the retry cannot become a new winning claim.
    assert store._db.get_authority_policy_v2_attempt_for_result(row["id"]).stage == "admitted"
    assert _stage_audits(store) == ["admitted"]
    retry = _claim(store, row, attempt)
    assert retry.status == "refused" and retry.refusal_code == "owner_lost"
    assert _counts(store._db)["candidates"] == 0


@pytest.mark.parametrize(
    "needle",
    [
        "INSERT INTO authority_policy_v2_candidate_audit",
        "INSERT INTO audit_log",
        "UPDATE authority_policy_v2_attempts",
    ],
)
def test_claim_audit_transaction_failure_residue(tmp_path, needle):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    assert _claim(store, row, attempt).status == "claimed"
    _inject(store, needle)
    with pytest.raises(RuntimeError):
        _audit(store, row, attempt)
    store._db._conn = store._db._conn._real
    counts = _counts(store._db)
    assert counts["candidates"] == 1 and counts["pins"] == 1
    assert counts["candidate_audit"] == 0
    assert store._db.get_authority_policy_v2_attempt_for_result(row["id"]).stage == "claimed"
    assert _stage_audits(store) == ["admitted"]
    retry = _audit(store, row, attempt)
    assert retry.status == "refused" and retry.refusal_code == "owner_lost"
    assert _counts(store._db)["candidate_audit"] == 0


def test_claim_rollback_preserves_task_result_and_admitted_audit(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    before = _counts(store._db)
    _inject(store, "INSERT INTO authority_policy_v2_candidates")
    with pytest.raises(RuntimeError):
        _claim(store, row, attempt)
    store._db._conn = store._db._conn._real
    assert _counts(store._db)["results"] == before["results"] == 1
    assert store._db.get_task(TASK_ID).status is TaskStatus.IN_PROGRESS
    assert store._db.get_task(TASK_ID).current_session_id == SESSION_ID
    assert store._db.get_authority_policy_v2_attempt_for_result(row["id"]) is not None


# ── immutable rows / append-only audit ───────────────────────────────────


def test_candidate_pin_and_audit_are_immutable(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    assert _claim(store, row, attempt).status == "claimed"
    assert _audit(store, row, attempt).status == "claim_audited"
    candidate_id = store.get_v2_candidate_for_result(row["id"]).candidate_id

    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        store._db._conn.execute(
            "UPDATE authority_policy_v2_candidates SET policy_version=99 WHERE candidate_id=?",
            (candidate_id,),
        )
    store._db._conn.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        store._db._conn.execute(
            "DELETE FROM authority_policy_v2_candidates WHERE candidate_id=?",
            (candidate_id,),
        )
    store._db._conn.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        store._db._conn.execute(
            "UPDATE authority_policy_v2_pins SET policy_version=99 WHERE candidate_id=?",
            (candidate_id,),
        )
    store._db._conn.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        store._db._conn.execute(
            "DELETE FROM authority_policy_v2_candidate_audit WHERE candidate_id=?",
            (candidate_id,),
        )
    store._db._conn.rollback()
    # Prior rows preserved after every refused mutation.
    assert store.get_v2_candidate(candidate_id) is not None
    assert store.get_v2_pin(candidate_id) is not None
    assert len(store.list_v2_candidate_audits(candidate_id)) == 1


def test_candidate_audit_requires_real_candidate_fk(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    assert _claim(store, row, attempt).status == "claimed"
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        store._db._conn.execute(
            """INSERT INTO authority_policy_v2_candidate_audit
               (candidate_id, team, root_task_id, manager_agent, manager_session_id,
                event, claim_key, attempt_id, result_id, owner_attempt_id,
                origin_boot_id, canonical_payload_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("APV2C-" + "0" * 64, TEAM, TASK_ID, MANAGER, SESSION_ID, "claimed",
             "1" * 64, attempt.attempt_id, row["id"], "owner", "boot", "{}",
             "2026-09-20T00:00:00+00:00"),
        )
    store._db._conn.rollback()


# ── concurrency / reopening ──────────────────────────────────────────────


def test_two_independent_connections_one_winning_candidate(tmp_path):
    store_a, _, _, _, row, attempt = _admitted(tmp_path)
    other = AuthorityPolicyStore(Database(tmp_path / "c2.db"))

    # The second connection holds no live-owner token and cannot claim or
    # append a1 for the winning owner's candidate.
    assert _claim(other, row, attempt).refusal_code == "owner_lost"
    assert _audit(other, row, attempt).refusal_code in {
        "claim_audit_missing", "owner_lost",
    }

    won = _claim(store_a, row, attempt)
    assert won.status == "claimed"
    assert _counts(store_a._db)["candidates"] == 1
    assert _claim(other, row, attempt).refusal_code in {"already_claimed", "owner_lost"}
    assert _counts(store_a._db)["candidates"] == 1
    assert _counts(store_a._db)["candidate_audit"] == 0
    assert _audit(store_a, row, attempt).status == "claim_audited"
    assert _counts(store_a._db)["candidate_audit"] == 1


def test_concurrent_claim_calls_one_winner(tmp_path):
    import threading

    store, _, _, _, row, attempt = _admitted(tmp_path)
    results: list = []
    barrier = threading.Barrier(4)

    def call():
        barrier.wait()
        results.append(_claim(store, row, attempt))

    threads = [threading.Thread(target=call) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 4
    assert sum(1 for r in results if r.status == "claimed") == 1
    assert _counts(store._db)["candidates"] == 1
    assert _counts(store._db)["pins"] == 1
    assert _counts(store._db)["candidate_audit"] == 0


def test_reopen_reads_candidate_pin_and_audits(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    claimed = _claim(store, row, attempt)
    assert _audit(store, row, attempt).status == "claim_audited"
    candidate_id = claimed.candidate_id

    reopened = AuthorityPolicyStore(Database(tmp_path / "c2.db"))
    assert reopened.get_v2_candidate(candidate_id) is not None
    assert reopened.get_v2_candidate_for_result(row["id"]).candidate_id == candidate_id
    assert reopened.get_v2_pin(candidate_id) is not None
    assert len(reopened.list_v2_candidate_audits(candidate_id)) == 1
    assert reopened.get_v2_attempt_for_result(row["id"]).stage == "claim_audited"
    assert [
        row_["payload"]["stage"]
        for row_ in reopened._db.list_authority_policy_v2_result_stage_audits(
            root_task_id=TASK_ID, manager_agent=MANAGER,
        )
    ] == ["admitted", "claim_audited"]
    # But it still cannot advance (the attempt is already terminal for this
    # checkpoint and no live-owner proof exists).
    assert _audit(reopened, row, attempt).refusal_code in {
        "already_audited", "owner_lost",
    }


# ── typed value strictness ───────────────────────────────────────────────


def test_strict_candidate_and_outcome_values():
    """Strict typed values reject extra fields and an unbound causal digest."""
    claim = authority_policy_v2_candidate_claim_preimage(
        activation_id="APV2A-" + "4" * 64, activation_selector_epoch=1,
        causal_result_digest=authority_policy_v2_causal_result_digest(1),
        causal_result_id=1, contract_digest=authority_policy_v2_contract_digest(),
        executor_kind="codex", manager_agent=MANAGER, manager_session_id=SESSION_ID,
        model_id="default", policy_digest="3" * 64, policy_version=1,
        provider_id="codex", release_id="APV2-" + "3" * 64, root_task_id=TASK_ID,
        team=TEAM,
    )
    claim_key = authority_policy_v2_sha256(claim)
    base = dict(
        candidate_id=f"APV2C-{claim_key}", claim_key=claim_key, team=TEAM,
        root_task_id=TASK_ID, manager_agent=MANAGER, manager_session_id=SESSION_ID,
        attempt_id="APV2R-" + "1" * 64, result_id=1, binding_id="APV2B-" + "2" * 64,
        contract_id="authority_policy_v2", contract_version="v2",
        contract_digest=authority_policy_v2_contract_digest(),
        release_id="APV2-" + "3" * 64, policy_version=1, policy_digest="3" * 64,
        activation_id="APV2A-" + "4" * 64, activation_epoch=1,
        selector_id="APS-" + "5" * 64, provider_id="codex", executor_kind="codex",
        model_id="default", causal_result_id=1, causal_result_digest="6" * 64,
        origin_boot_id="boot", owner_attempt_id="owner",
    )
    # Wrong causal digest for the causal result id is refused.
    with pytest.raises(Exception):
        AuthorityPolicyV2Candidate(**base)
    # Extra fields are forbidden.
    with pytest.raises(Exception):
        AuthorityPolicyV2Candidate(**{**base, "extra": "nope"})
    from runtime.models import AuthorityPolicyV2StageOutcome

    with pytest.raises(Exception):
        AuthorityPolicyV2StageOutcome(status="refused")
    with pytest.raises(Exception):
        AuthorityPolicyV2StageOutcome(status="claimed", refusal_code="claim_failed")
    with pytest.raises(Exception):
        AuthorityPolicyV2StageOutcome(status="refused", refusal_code="not_a_code")
    assert AuthorityPolicyV2StageOutcome(
        status="refused", refusal_code="owner_lost",
    ).refusal_code == "owner_lost"


# ── C3b correction 1: respect the caller's transaction ───────────────────
#
# Both public writers must reject transaction nesting BEFORE they would BEGIN,
# ROLLBACK or invalidate the live owner, leaving the caller transaction and its
# pending work untouched.  Rejecting after owning the write would discard the
# caller's work; moving the two stage commits into the caller transaction (or
# silently using a savepoint) would change their R4 durability meaning.


def test_claim_refuses_nested_transaction_and_preserves_caller(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    before = _counts(store._db)
    conn = store._db._conn
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("UPDATE tasks SET brief='caller pending write' WHERE id=?", (TASK_ID,))

    outcome = _claim(store, row, attempt)
    assert outcome.status == "refused" and outcome.refusal_code == "transaction_owned"
    # The caller's transaction and its pending write are untouched.
    assert conn.in_transaction is True
    assert conn.execute(
        "SELECT brief FROM tasks WHERE id=?", (TASK_ID,)
    ).fetchone()[0] == "caller pending write"
    # No owned write/rollback/owner-invalidation occurred.
    assert _counts(store._db)["candidates"] == before["candidates"]
    conn.rollback()
    assert conn.in_transaction is False
    assert _counts(store._db) == before
    # The authentic uninterrupted owner is NOT poisoned by the nesting refusal.
    assert _claim(store, row, attempt).status == "claimed"


def test_claim_nested_caller_can_commit_afterwards(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    conn = store._db._conn
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("UPDATE tasks SET brief='caller committed write' WHERE id=?", (TASK_ID,))
    assert _claim(store, row, attempt).refusal_code == "transaction_owned"
    conn.commit()
    assert conn.in_transaction is False
    assert conn.execute(
        "SELECT brief FROM tasks WHERE id=?", (TASK_ID,)
    ).fetchone()[0] == "caller committed write"
    assert _claim(store, row, attempt).status == "claimed"


def test_audit_refuses_nested_transaction_and_preserves_caller(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    assert _claim(store, row, attempt).status == "claimed"
    before = _counts(store._db)
    conn = store._db._conn
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("UPDATE tasks SET brief='caller audit write' WHERE id=?", (TASK_ID,))

    outcome = _audit(store, row, attempt)
    assert outcome.status == "refused" and outcome.refusal_code == "transaction_owned"
    assert conn.in_transaction is True
    assert conn.execute(
        "SELECT brief FROM tasks WHERE id=?", (TASK_ID,)
    ).fetchone()[0] == "caller audit write"
    assert _counts(store._db) == before
    conn.rollback()
    assert _counts(store._db) == before
    # The authentic owner is not poisoned: it can still complete a1 exactly once.
    assert _audit(store, row, attempt).status == "claim_audited"
    assert len(store.list_v2_candidate_audits(
        store.get_v2_candidate_for_result(row["id"]).candidate_id)) == 1


# ── C3b correction 2: reauthenticate the full evidence at the 2nd boundary ─


def test_result_body_drift_between_stages_refuses_audit(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    assert _claim(store, row, attempt).status == "claimed"
    candidate_id = store.get_v2_candidate_for_result(row["id"]).candidate_id
    before = _counts(store._db)
    store._db._conn.execute(
        """UPDATE task_results SET decision_json=json_set(
               decision_json, '$._manager_self_evaluation.what_to_escalate.confidence', 1)
           WHERE id=?""",
        (row["id"],),
    )
    store._db._conn.commit()
    outcome = _audit(store, row, attempt)
    assert outcome.status == "refused" and outcome.refusal_code == "identity_mismatch"
    assert _counts(store._db) == before
    assert store._db.get_authority_policy_v2_attempt_for_result(row["id"]).stage == "claimed"
    assert store.list_v2_candidate_audits(candidate_id) == []
    assert _stage_audits(store) == ["admitted"]


def test_result_row_identity_drift_between_stages_refuses_audit(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    assert _claim(store, row, attempt).status == "claimed"
    store._db._conn.execute(
        "UPDATE task_results SET session_id='sess-other' WHERE id=?", (row["id"],),
    )
    store._db._conn.commit()
    outcome = _audit(store, row, attempt)
    assert outcome.status == "refused" and outcome.refusal_code == "identity_mismatch"
    assert store._db.get_authority_policy_v2_attempt_for_result(row["id"]).stage == "claimed"


def test_missing_admitted_audit_between_stages_refuses_audit(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    assert _claim(store, row, attempt).status == "claimed"
    store._db._conn.execute(
        "DELETE FROM audit_log WHERE action='authority_policy_v2_result_stage'"
    )
    store._db._conn.commit()
    outcome = _audit(store, row, attempt)
    assert outcome.status == "refused" and outcome.refusal_code == "identity_mismatch"
    assert _raw_stage(store, row["id"]) == "claimed"


def test_duplicated_admitted_audit_between_stages_refuses_audit(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    assert _claim(store, row, attempt).status == "claimed"
    audits = store._db.list_authority_policy_v2_result_stage_audits(
        root_task_id=TASK_ID, manager_agent=MANAGER,
    )
    store._db.insert_audit_log_uncommitted(
        TASK_ID, MANAGER, "authority_policy_v2_result_stage", dict(audits[0]["payload"]),
    )
    store._db.commit()
    outcome = _audit(store, row, attempt)
    assert outcome.status == "refused" and outcome.refusal_code == "identity_mismatch"
    assert _raw_stage(store, row["id"]) == "claimed"


def test_cancellation_between_stages_prohibits_advancement(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    assert _claim(store, row, attempt).status == "claimed"
    store._db._conn.execute(
        "UPDATE tasks SET cancelled_at='2026-09-20T00:00:00+00:00' WHERE id=?",
        (TASK_ID,),
    )
    store._db._conn.commit()
    outcome = _audit(store, row, attempt)
    assert outcome.status == "refused" and outcome.refusal_code == "cancelled"
    # A genuine cancellation poisons the authentic owner: no later advancement.
    assert _audit(store, row, attempt).refusal_code == "owner_lost"
    assert store._db.get_authority_policy_v2_attempt_for_result(row["id"]).stage == "claimed"


def test_replacement_between_stages_prohibits_advancement(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    assert _claim(store, row, attempt).status == "claimed"
    store._db._conn.execute(
        "UPDATE tasks SET current_session_id='sess-replacement' WHERE id=?", (TASK_ID,),
    )
    store._db._conn.commit()
    outcome = _audit(store, row, attempt)
    assert outcome.status == "refused" and outcome.refusal_code == "owner_lost"
    assert _audit(store, row, attempt).refusal_code == "owner_lost"


# ── C3b correction 3: losing calls must not poison the winner ─────────────


def test_wrong_boot_loser_does_not_poison_winner(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    assert _claim(store, row, attempt).status == "claimed"
    loser = _audit(store, row, attempt, origin_boot_id="other-boot")
    assert loser.status == "refused" and loser.refusal_code == "owner_lost"
    # The original uninterrupted owner still completes a1 exactly once.
    assert _audit(store, row, attempt).status == "claim_audited"
    assert _audit(store, row, attempt).refusal_code == "already_audited"
    assert len(store.list_v2_candidate_audits(
        store.get_v2_candidate_for_result(row["id"]).candidate_id)) == 1


def test_wrong_owner_and_tuple_losers_do_not_poison_winner(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    assert _claim(store, row, attempt).status == "claimed"
    assert _audit(
        store, row, attempt, owner_attempt_id="not-the-owner",
    ).refusal_code == "owner_lost"
    assert _audit(
        store, row, attempt, result_id=row["id"] + 999,
    ).refusal_code == "identity_mismatch"
    assert _audit(store, row, attempt).status == "claim_audited"


def test_duplicate_claim_loser_does_not_poison_winner(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    won = _claim(store, row, attempt)
    assert won.status == "claimed"
    assert _claim(store, row, attempt).refusal_code == "already_claimed"
    assert _audit(store, row, attempt).status == "claim_audited"


# ── C3b correction 4: retain claim-time schema/permission evidence ────────


def test_claim_freezes_and_pin_mirrors_claim_time_evidence(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    claimed = _claim(store, row, attempt)
    candidate = store.get_v2_candidate(claimed.candidate_id)
    pin = store.get_v2_pin(claimed.candidate_id)
    assert candidate.schema_raw_digest and candidate.schema_inventory_digest
    assert candidate.schema_object_count > 0
    assert candidate.permission_surface_digest == "a" * 64
    # The pin is identity-equal on the frozen evidence.
    assert (
        pin.schema_raw_digest == candidate.schema_raw_digest
        and pin.schema_inventory_digest == candidate.schema_inventory_digest
        and pin.schema_object_count == candidate.schema_object_count
        and pin.permission_surface_digest == candidate.permission_surface_digest
    )
    # The evidence fields are NOT new claim-preimage inputs.
    assert candidate.preimage() == authority_policy_v2_candidate_claim_preimage(
        activation_id=candidate.activation_id,
        activation_selector_epoch=candidate.activation_epoch,
        causal_result_digest=candidate.causal_result_digest,
        causal_result_id=candidate.result_id,
        contract_digest=candidate.contract_digest,
        executor_kind=candidate.executor_kind,
        manager_agent=candidate.manager_agent,
        manager_session_id=candidate.manager_session_id,
        model_id=candidate.model_id,
        policy_digest=candidate.policy_digest,
        policy_version=candidate.policy_version,
        provider_id=candidate.provider_id,
        release_id=candidate.release_id,
        root_task_id=candidate.root_task_id,
        team=candidate.team,
    )


def test_schema_drift_between_claim_and_audit_refuses(tmp_path):
    """The manager probe's ``schema_drift_after_claim`` case: a new index after
    the K/P commit denies at the second boundary."""
    store, _, _, _, row, attempt = _admitted(tmp_path)
    assert _claim(store, row, attempt).status == "claimed"
    store._db._conn.execute("CREATE INDEX task8450_unreviewed ON tasks(brief)")
    store._db._conn.commit()
    before = _counts(store._db)
    outcome = _audit(store, row, attempt)
    assert outcome.status == "refused" and outcome.refusal_code == "schema_drift"
    assert _counts(store._db) == before
    assert store._db.get_authority_policy_v2_attempt_for_result(row["id"]).stage == "claimed"
    assert store.list_v2_candidate_audits(
        store.get_v2_candidate_for_result(row["id"]).candidate_id) == []


def test_schema_drift_between_stages_refuses_after_another_accepted_layout(tmp_path):
    """Switching to a DIFFERENT individually accepted layout after capture is
    still a denial: the comparison is against the exact frozen raw digest."""
    store, _, _, _, row, attempt = _admitted(tmp_path)
    assert _claim(store, row, attempt).status == "claimed"
    # Drop and recreate a legal candidate-table trigger so the raw DDL digest
    # changes while the inventory may still resemble a layout.
    store._db._conn.execute("DROP TRIGGER authority_policy_v2_pins_no_delete")
    store._db._conn.execute(
        """CREATE TRIGGER authority_policy_v2_pins_no_delete
           BEFORE DELETE ON authority_policy_v2_pins
           BEGIN SELECT RAISE(ABORT, 'v2 policy pin cannot be deleted'); END"""
    )
    store._db._conn.commit()
    outcome = _audit(store, row, attempt)
    assert outcome.status == "refused" and outcome.refusal_code == "schema_drift"


def test_permission_change_between_stages_refuses_audit(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    assert _claim(store, row, attempt).status == "claimed"
    store.bind_v2_permission_surface_reader(lambda agent: "b" * 64)
    outcome = _audit(store, row, attempt)
    assert outcome.status == "refused" and outcome.refusal_code == "evidence_drift"
    assert store._db.get_authority_policy_v2_attempt_for_result(row["id"]).stage == "claimed"


def test_permission_read_failure_fails_closed(tmp_path):
    def boom(agent):
        raise RuntimeError("permission surface unreadable")

    store, _, _, _, row, attempt = _admitted(tmp_path)
    store.bind_v2_permission_surface_reader(boom)
    outcome = _claim(store, row, attempt)
    assert outcome.status == "refused" and outcome.refusal_code == "evidence_drift"
    assert _counts(store._db)["candidates"] == 0


def test_permission_read_failure_between_stages_fails_closed(tmp_path):
    def boom(agent):
        raise RuntimeError("permission surface unreadable")

    store, _, _, _, row, attempt = _admitted(tmp_path)
    assert _claim(store, row, attempt).status == "claimed"
    store.bind_v2_permission_surface_reader(boom)
    outcome = _audit(store, row, attempt)
    assert outcome.status == "refused" and outcome.refusal_code == "evidence_drift"


def test_unbound_permission_reader_refuses_claim(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    store.bind_v2_permission_surface_reader(None)
    outcome = _claim(store, row, attempt)
    assert outcome.status == "refused" and outcome.refusal_code == "evidence_drift"
    assert _counts(store._db)["candidates"] == 0


def test_missing_or_empty_frozen_evidence_fails_closed(tmp_path):
    from runtime.models import (
        AuthorityPolicyV2PermissionSurface,
        AuthorityPolicyV2SchemaIntegrity,
    )
    from runtime.orchestrator.authority import (
        V2_PERMISSION_SURFACE_CONTRACT,
        V2_SCHEMA_INTEGRITY_CONTRACT,
        recheck_authority_policy_v2_permission_surface,
        recheck_authority_policy_v2_schema_integrity,
    )

    db = Database(tmp_path / "evidence.db")
    assert recheck_authority_policy_v2_schema_integrity(None, db) is False
    assert recheck_authority_policy_v2_permission_surface(None, db, MANAGER) is False
    empty = AuthorityPolicyV2SchemaIntegrity(
        contract_version=V2_SCHEMA_INTEGRITY_CONTRACT,
        raw_digest="", inventory_digest="", object_count=0,
    )
    assert recheck_authority_policy_v2_schema_integrity(empty, db) is False
    empty_permission = AuthorityPolicyV2PermissionSurface(
        contract_version=V2_PERMISSION_SURFACE_CONTRACT, digest="",
    )
    assert recheck_authority_policy_v2_permission_surface(
        empty_permission, db, MANAGER,
    ) is False


def test_candidate_column_canonical_evidence_mismatch_refuses(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    claimed = _claim(store, row, attempt)
    real = store._db._conn.execute(
        "SELECT * FROM authority_policy_v2_candidates WHERE candidate_id=?",
        (claimed.candidate_id,),
    ).fetchone()
    tampered = dict(real)
    tampered["permission_surface_digest"] = "c" * 64
    with pytest.raises(ValueError):
        store._db._authority_policy_v2_candidate_from_row(tampered)
    tampered2 = dict(real)
    tampered2["schema_raw_digest"] = "d" * 64
    with pytest.raises(ValueError):
        store._db._authority_policy_v2_candidate_from_row(tampered2)
