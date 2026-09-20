"""THR-229 checkpoint C3c — durable evaluation and single consumption stages.

Focused isolated evidence for the accepted C3c radius, driving the REAL
``Database``/``AuthorityPolicyStore`` seam (no copies of its logic):

  * the evaluation transaction authenticates the persisted sanitized manager
    assessment, derives the clause-free outcome ONCE and persists one immutable
    V (identity equals the candidate ID) while advancing K ``created`` ->
    ``evaluated`` and J ``claim_audited`` -> ``evaluated``;
  * the evaluation-audit transaction appends exactly one candidate ``evaluated``
    event (a2) plus the ``evaluation_audited`` result-stage evidence and advances
    J, never re-deriving the outcome;
  * the consumption transaction CASes K/J to ``consumed`` exactly once using the
    stored V with no second model call or repeated derivation;
  * the consumed-audit transaction appends the ``consumed`` event (a3) plus the
    ``consumed_audited`` result-stage evidence;
  * the complete accepted applicability/low-confidence/uncertainty/invalid table,
    bound-identity negatives, tampered V and every atomic-failure residue;
  * caller-transaction preservation and reopen refusal.

The broad integration suite is SKIPPED under founder THR-243 seq42, never PASS.
"""
from __future__ import annotations

import json

import pytest

from runtime.infrastructure.database import Database
from runtime.models import TaskStatus
from runtime.orchestrator import authority_policy
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


# ── helpers ──────────────────────────────────────────────────────────────


def _counts(db: Database) -> dict:
    def count(table: str) -> int:
        return db._conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]

    return {
        "results": count("task_results"),
        "attempts": count("authority_policy_v2_attempts"),
        "candidates": count("authority_policy_v2_candidates"),
        "pins": count("authority_policy_v2_pins"),
        "candidate_audit": count("authority_policy_v2_candidate_audit"),
        "evaluations": count("authority_policy_v2_evaluations"),
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


def _claim_audit(store, row, attempt, *, task_id: str = TASK_ID, session_id: str = SESSION_ID, **kw):
    kwargs = dict(
        root_task_id=task_id, manager_agent=MANAGER, manager_session_id=session_id,
        result_id=row["id"], origin_boot_id=attempt.origin_boot_id,
        owner_attempt_id=attempt.owner_attempt_id,
    )
    kwargs.update(kw)
    return store.audit_v2_candidate_claim(**kwargs)


def _claim_stages(store, row, attempt):
    assert _claim(store, row, attempt).status == "claimed"
    assert _claim_audit(store, row, attempt).status == "claim_audited"
    candidate = store.get_v2_candidate_for_result(row["id"])
    return candidate


def _stage_kwargs(row, attempt, *, task_id=TASK_ID, session_id=SESSION_ID, **kw):
    kwargs = dict(
        root_task_id=task_id, manager_agent=MANAGER, manager_session_id=session_id,
        result_id=row["id"], origin_boot_id=attempt.origin_boot_id,
        owner_attempt_id=attempt.owner_attempt_id,
    )
    kwargs.update(kw)
    return kwargs


def _evaluate(store, row, attempt, **kw):
    return store.evaluate_v2_candidate(**_stage_kwargs(row, attempt, **kw))


def _audit_evaluation(store, row, attempt, **kw):
    return store.audit_v2_candidate_evaluation(**_stage_kwargs(row, attempt, **kw))


def _consume(store, row, attempt, **kw):
    return store.consume_v2_candidate(**_stage_kwargs(row, attempt, **kw))


def _audit_consumption(store, row, attempt, **kw):
    return store.audit_v2_candidate_consumption(**_stage_kwargs(row, attempt, **kw))


def _stage(store, result_id: int) -> str:
    return store._db._conn.execute(
        "SELECT stage FROM authority_policy_v2_attempts WHERE result_id=?",
        (result_id,),
    ).fetchone()[0]


def _lifecycle(store, candidate_id: str) -> str:
    return store._db._conn.execute(
        "SELECT lifecycle_stage FROM authority_policy_v2_candidates WHERE candidate_id=?",
        (candidate_id,),
    ).fetchone()[0]


def _stage_audits(store, task_id: str = TASK_ID) -> list[str]:
    return [
        row["payload"]["stage"]
        for row in store._db.list_authority_policy_v2_result_stage_audits(
            root_task_id=task_id, manager_agent=MANAGER,
        )
    ]


def _audit_events(store, candidate_id: str) -> list[str]:
    return [
        row["event"]
        for row in store.list_v2_candidate_audits(candidate_id)
    ]


def _ready(tmp_path):
    """Return a store stopped at the accepted ``claim_audited`` boundary."""
    store, binding, carrier, admission, row, attempt = _admitted(tmp_path)
    candidate = _claim_stages(store, row, attempt)
    return store, binding, row, attempt, candidate


# ── happy path: four independent commits ─────────────────────────────────


def test_four_stages_advance_k_and_j_and_persist_one_evaluation(tmp_path):
    store, binding, row, attempt, candidate = _ready(tmp_path)
    before = _counts(store._db)

    evaluated = _evaluate(store, row, attempt)
    assert evaluated.status == "evaluated"
    assert evaluated.candidate_id == candidate.candidate_id
    assert evaluated.stage == "evaluated"
    evaluation = store.get_v2_evaluation(candidate.candidate_id)
    assert evaluation is not None
    assert evaluation.evaluation_id == candidate.candidate_id
    assert evaluation.candidate_id == candidate.candidate_id
    assert evaluation.outcome == "continue_applies"
    assert evaluation.diagnostic_code is None
    assert evaluation.assessment_digest == attempt.assessment_digest
    assert evaluation.what_to_escalate_json is not None
    assert _lifecycle(store, candidate.candidate_id) == "evaluated"
    assert _stage(store, row["id"]) == "evaluated"
    assert _audit_events(store, candidate.candidate_id) == ["claimed"]
    # Task and recovery receipt are unchanged; no envelope/notification exists.
    task = store._db.get_task(TASK_ID)
    assert task.status is TaskStatus.IN_PROGRESS
    assert task.current_session_id == SESSION_ID
    tables = {
        r[0] for r in store._db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert not any(
        name in tables for name in (
            "authority_policy_v2_continue_envelopes",
            "authority_policy_v2_recovery_notifications",
            "authority_policy_v2_root_dispatch",
        )
    )
    after_eval = _counts(store._db)
    assert after_eval["evaluations"] == before["evaluations"] + 1
    assert after_eval["candidates"] == before["candidates"]

    audited = _audit_evaluation(store, row, attempt)
    assert audited.status == "evaluation_audited"
    assert _stage(store, row["id"]) == "evaluation_audited"
    assert _audit_events(store, candidate.candidate_id) == ["claimed", "evaluated"]
    assert _stage_audits(store) == ["admitted", "claim_audited", "evaluation_audited"]
    # The stored V is unchanged by the audit.
    assert store.get_v2_evaluation(candidate.candidate_id) == evaluation

    consumed = _consume(store, row, attempt)
    assert consumed.status == "consumed"
    assert _lifecycle(store, candidate.candidate_id) == "consumed"
    assert _stage(store, row["id"]) == "consumed"
    assert _audit_events(store, candidate.candidate_id) == ["claimed", "evaluated"]
    assert store.get_v2_evaluation(candidate.candidate_id) == evaluation

    consumed_audited = _audit_consumption(store, row, attempt)
    assert consumed_audited.status == "consumed_audited"
    assert _stage(store, row["id"]) == "consumed_audited"
    assert _audit_events(store, candidate.candidate_id) == [
        "claimed", "evaluated", "consumed",
    ]
    assert _stage_audits(store) == [
        "admitted", "claim_audited", "evaluation_audited", "consumed_audited",
    ]
    assert _counts(store._db)["evaluations"] == 1


# ── accepted applicability / confidence / uncertainty / invalid table ─────


def _carrier_with(tmp_path, *, confidence=90, uncertainty=(), escalate="does_not_apply",
                  continue_="applies"):
    store = _store(tmp_path)
    binding = _seed_bound_task(store)
    carrier, admission = _carrier_and_admission(binding)
    carrier["what_to_escalate"] = {
        "applicability": escalate, "confidence": confidence,
        "uncertainty_codes": list(uncertainty),
    }
    carrier["what_not_to_escalate"] = {
        "applicability": continue_, "confidence": confidence,
        "uncertainty_codes": [],
    }
    # Re-admit under the exact knowledge that the persisted digest changes.
    from runtime.models import authority_policy_v2_canonical_json_bytes
    import hashlib
    canonical = authority_policy_v2_canonical_json_bytes(carrier)
    admission["assessment_digest"] = hashlib.sha256(canonical).hexdigest()
    admission["assessment_canonical_json"] = canonical.decode("utf-8")
    assert _admit(store, carrier, admission) is True
    row = store._db.get_latest_task_result(TASK_ID, MANAGER, SESSION_ID)
    attempt = store._db.get_authority_policy_v2_attempt_for_result(row["id"])
    _claim_stages(store, row, attempt)
    return store, row, attempt


@pytest.mark.parametrize(
    ("escalate", "continue_", "expected"),
    [
        ("does_not_apply", "applies", "continue_applies"),
        ("applies", "does_not_apply", "escalate_applies"),
        ("applies", "applies", "escalate_applies"),
        ("does_not_apply", "does_not_apply", "neither_apply"),
    ],
)
def test_applicability_table_persists_each_outcome(tmp_path, escalate, continue_, expected):
    store, row, attempt = _carrier_with(
        tmp_path, escalate=escalate, continue_=continue_,
    )
    outcome = _evaluate(store, row, attempt)
    assert outcome.status == "evaluated"
    assert store.get_v2_evaluation_for_result(row["id"]).outcome == expected


@pytest.mark.parametrize("confidence", [0, 79])
def test_low_confidence_is_uncertain(tmp_path, confidence):
    store, row, attempt = _carrier_with(tmp_path, confidence=confidence)
    assert _evaluate(store, row, attempt).status == "evaluated"
    assert store.get_v2_evaluation_for_result(row["id"]).outcome == "uncertain"


@pytest.mark.parametrize(
    "code",
    ["ambiguous_scope", "missing_context", "conflicting_evidence",
     "unknown_authorization", "insufficient_confidence", "unsupported_version"],
)
def test_any_uncertainty_code_is_uncertain(tmp_path, code):
    store, row, attempt = _carrier_with(tmp_path, uncertainty=(code,))
    assert _evaluate(store, row, attempt).status == "evaluated"
    assert store.get_v2_evaluation_for_result(row["id"]).outcome == "uncertain"


def test_uncertain_applicability_is_uncertain(tmp_path):
    store, row, attempt = _carrier_with(tmp_path, escalate="uncertain")
    assert _evaluate(store, row, attempt).status == "evaluated"
    assert store.get_v2_evaluation_for_result(row["id"]).outcome == "uncertain"


def test_invalid_diagnostic_carrier_is_stored_honestly(tmp_path):
    """A diagnostic carrier is never turned into a valid assessment."""
    store = _store(tmp_path)
    binding = _seed_bound_task(store)
    # Persist an admitted diagnostic carrier through the real callback writer.
    from runtime.models import authority_policy_v2_canonical_json_bytes
    import hashlib
    carrier = {"_error_code": "missing_assessment"}
    canonical = authority_policy_v2_canonical_json_bytes(carrier)
    admission = {
        "team": TEAM, "binding_id": binding["binding_id"],
        "contract_id": binding["contract_id"],
        "contract_version": binding["contract_version"],
        "contract_digest": binding["contract_digest"],
        "release_id": binding["release_id"],
        "activation_id": binding["activation_id"],
        "activation_epoch": binding["selector_epoch"],
        "selector_id": binding["selector_id"],
        "assessment_digest": hashlib.sha256(canonical).hexdigest(),
        "assessment_canonical_json": canonical.decode("utf-8"),
        "origin_boot_id": "boot-c2",
    }
    assert _admit(store, carrier, admission) is True
    row = store._db.get_latest_task_result(TASK_ID, MANAGER, SESSION_ID)
    attempt = store._db.get_authority_policy_v2_attempt_for_result(row["id"])
    _claim_stages(store, row, attempt)
    assert _evaluate(store, row, attempt).status == "evaluated"
    evaluation = store.get_v2_evaluation_for_result(row["id"])
    assert evaluation.outcome == "invalid"
    assert evaluation.diagnostic_code == "missing_assessment"
    assert evaluation.what_to_escalate_json is None
    assert evaluation.what_not_to_escalate_json is None


# ── the pure helper runs once for evaluation, zero later ─────────────────


def test_pure_helper_invoked_once_for_evaluation_and_never_again(tmp_path, monkeypatch):
    store, binding, row, attempt, candidate = _ready(tmp_path)
    calls = {"n": 0}
    original = authority_policy.derive_authority_policy_v2_assessment_outcome

    def counting(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(
        authority_policy, "derive_authority_policy_v2_assessment_outcome", counting,
    )
    assert _evaluate(store, row, attempt).status == "evaluated"
    assert calls["n"] == 1
    duplicate = _evaluate(store, row, attempt)
    assert duplicate.status == "refused" and duplicate.refusal_code == "already_evaluated"
    assert calls["n"] == 1
    assert _audit_evaluation(store, row, attempt).status == "evaluation_audited"
    assert _consume(store, row, attempt).status == "consumed"
    assert _audit_consumption(store, row, attempt).status == "consumed_audited"
    assert calls["n"] == 1


# ── duplicate / out-of-order refusals ────────────────────────────────────


def test_audit_before_evaluation_refuses(tmp_path):
    store, _, row, attempt, candidate = _ready(tmp_path)
    before = _counts(store._db)
    outcome = _audit_evaluation(store, row, attempt)
    assert outcome.status == "refused" and outcome.refusal_code == "evaluation_missing"
    assert _counts(store._db) == before
    assert _stage(store, row["id"]) == "claim_audited"


def test_consume_before_evaluation_audit_refuses(tmp_path):
    store, _, row, attempt, candidate = _ready(tmp_path)
    assert _evaluate(store, row, attempt).status == "evaluated"
    outcome = _consume(store, row, attempt)
    assert outcome.status == "refused" and outcome.refusal_code == "evaluation_audit_missing"
    assert _stage(store, row["id"]) == "evaluated"


def test_duplicate_evaluation_refuses_without_rows(tmp_path):
    store, _, row, attempt, candidate = _ready(tmp_path)
    assert _evaluate(store, row, attempt).status == "evaluated"
    before = _counts(store._db)
    again = _evaluate(store, row, attempt)
    assert again.status == "refused" and again.refusal_code == "already_evaluated"
    assert _counts(store._db) == before


def test_duplicate_evaluation_audit_refuses(tmp_path):
    store, _, row, attempt, candidate = _ready(tmp_path)
    assert _evaluate(store, row, attempt).status == "evaluated"
    assert _audit_evaluation(store, row, attempt).status == "evaluation_audited"
    before = _counts(store._db)
    again = _audit_evaluation(store, row, attempt)
    assert again.status == "refused" and again.refusal_code == "already_audited"
    assert _counts(store._db) == before


def test_duplicate_consumption_refuses(tmp_path):
    store, _, row, attempt, candidate = _ready(tmp_path)
    assert _evaluate(store, row, attempt).status == "evaluated"
    assert _audit_evaluation(store, row, attempt).status == "evaluation_audited"
    assert _consume(store, row, attempt).status == "consumed"
    before = _counts(store._db)
    again = _consume(store, row, attempt)
    assert again.status == "refused" and again.refusal_code == "already_consumed"
    assert _counts(store._db) == before


def test_duplicate_consumed_audit_refuses(tmp_path):
    store, _, row, attempt, candidate = _ready(tmp_path)
    assert _evaluate(store, row, attempt).status == "evaluated"
    assert _audit_evaluation(store, row, attempt).status == "evaluation_audited"
    assert _consume(store, row, attempt).status == "consumed"
    assert _audit_consumption(store, row, attempt).status == "consumed_audited"
    before = _counts(store._db)
    again = _audit_consumption(store, row, attempt)
    assert again.status == "refused" and again.refusal_code == "already_audited"
    assert _counts(store._db) == before


# ── owner / liveness / identity ──────────────────────────────────────────


def test_wrong_owner_token_and_boot_refuse(tmp_path):
    store, _, row, attempt, candidate = _ready(tmp_path)
    wrong_owner = _evaluate(store, row, attempt, owner_attempt_id="not-the-owner")
    assert wrong_owner.status == "refused" and wrong_owner.refusal_code == "owner_lost"
    wrong_boot = _evaluate(store, row, attempt, origin_boot_id="boot-elsewhere")
    assert wrong_boot.status == "refused" and wrong_boot.refusal_code == "owner_lost"
    assert _counts(store._db)["evaluations"] == 0


def test_reopened_database_cannot_advance(tmp_path):
    db_path = tmp_path / "reopen.db"
    first = AuthorityPolicyStore(Database(db_path))
    first.bind_v2_permission_surface_reader(lambda agent: "a" * 64)
    binding = _seed_bound_task(first)
    carrier, admission = _carrier_and_admission(binding)
    assert _admit(first, carrier, admission) is True
    row = first._db.get_latest_task_result(TASK_ID, MANAGER, SESSION_ID)
    attempt = first._db.get_authority_policy_v2_attempt_for_result(row["id"])
    _claim_stages(first, row, attempt)

    reopened = AuthorityPolicyStore(Database(db_path))
    reopened.bind_v2_permission_surface_reader(lambda agent: "a" * 64)
    assert reopened.get_v2_candidate_for_result(row["id"]) is not None
    outcome = _evaluate(reopened, row, attempt)
    assert outcome.status == "refused" and outcome.refusal_code == "owner_lost"
    assert reopened._db._conn.execute(
        "SELECT COUNT(*) FROM authority_policy_v2_evaluations"
    ).fetchone()[0] == 0


def test_cancelled_task_refuses_evaluation(tmp_path):
    store, _, row, attempt, candidate = _ready(tmp_path)
    store._db.update_task(TASK_ID, status=TaskStatus.CANCELLED)
    outcome = _evaluate(store, row, attempt)
    assert outcome.status == "refused" and outcome.refusal_code == "cancelled"
    assert _counts(store._db)["evaluations"] == 0


def test_tampered_result_assessment_refuses(tmp_path):
    store, _, row, attempt, candidate = _ready(tmp_path)
    store._db._conn.execute(
        """UPDATE task_results SET decision_json=json_set(
               decision_json, '$._manager_self_evaluation.what_to_escalate.confidence', 1)
           WHERE id=?""",
        (row["id"],),
    )
    store._db._conn.commit()
    outcome = _evaluate(store, row, attempt)
    assert outcome.status == "refused" and outcome.refusal_code == "identity_mismatch"


def test_mixed_release_identity_refuses(tmp_path):
    store, _, row, attempt, candidate = _ready(tmp_path)
    # Re-point the candidate release to a mismatching digest while keeping the
    # canonical payload consistent (a hostile rewrite of frozen evidence).
    snapshot = json.loads(store._db._conn.execute(
        "SELECT canonical_payload_json FROM authority_policy_v2_candidates WHERE candidate_id=?",
        (candidate.candidate_id,),
    ).fetchone()[0])
    snapshot["policy_digest"] = "0" * 64
    snapshot["release_id"] = "APV2-" + "0" * 64
    from runtime.models import authority_policy_v2_canonical_json_bytes
    store._db._conn.execute("DROP TRIGGER authority_policy_v2_candidates_lifecycle_guard")
    store._db._conn.execute(
        "UPDATE authority_policy_v2_candidates SET policy_digest=?, release_id=?, canonical_payload_json=? WHERE candidate_id=?",
        (snapshot["policy_digest"], snapshot["release_id"],
         authority_policy_v2_canonical_json_bytes(snapshot).decode("utf-8"),
         candidate.candidate_id),
    )
    store._db._conn.commit()
    outcome = _evaluate(store, row, attempt)
    assert outcome.status == "refused"
    assert outcome.refusal_code in {"identity_mismatch", "schema_drift"}


# ── tampered V ───────────────────────────────────────────────────────────


def test_evaluation_row_is_immutable(tmp_path):
    import sqlite3
    store, _, row, attempt, candidate = _ready(tmp_path)
    assert _evaluate(store, row, attempt).status == "evaluated"
    with pytest.raises(sqlite3.IntegrityError):
        store._db._conn.execute(
            "UPDATE authority_policy_v2_evaluations SET outcome='escalate_applies' WHERE candidate_id=?",
            (candidate.candidate_id,),
        )


def test_dropping_evaluation_trigger_then_tampering_refuses(tmp_path):
    store, _, row, attempt, candidate = _ready(tmp_path)
    assert _evaluate(store, row, attempt).status == "evaluated"
    store._db._conn.execute("DROP TRIGGER authority_policy_v2_evaluations_no_update")
    store._db._conn.execute(
        "UPDATE authority_policy_v2_evaluations SET outcome='escalate_applies' WHERE candidate_id=?",
        (candidate.candidate_id,),
    )
    store._db._conn.commit()
    outcome = _audit_evaluation(store, row, attempt)
    assert outcome.status == "refused"
    assert outcome.refusal_code in {"identity_mismatch", "schema_drift"}


def test_missing_evaluation_refuses_audit(tmp_path):
    store, _, row, attempt, candidate = _ready(tmp_path)
    assert _evaluate(store, row, attempt).status == "evaluated"
    store._db._conn.execute("DROP TRIGGER authority_policy_v2_evaluations_no_delete")
    store._db._conn.execute(
        "DELETE FROM authority_policy_v2_evaluations WHERE candidate_id=?",
        (candidate.candidate_id,),
    )
    store._db._conn.commit()
    outcome = _audit_evaluation(store, row, attempt)
    assert outcome.status == "refused"
    # Deleting the immutable row required dropping its trigger, so the frozen
    # schema recheck additionally refuses the now-missing evidence.
    assert outcome.refusal_code in {"evaluation_missing", "identity_mismatch", "schema_drift"}


# ── injected atomic failures: exact R4 residue ───────────────────────────


def test_evaluation_failure_rolls_back_v_and_both_stages(tmp_path, monkeypatch):
    store, _, row, attempt, candidate = _ready(tmp_path)

    def boom(*args, **kwargs):
        raise RuntimeError("lifecycle advance failed")

    monkeypatch.setattr(store._db, "_advance_v2_candidate_lifecycle_uncommitted", boom)
    with pytest.raises(RuntimeError):
        _evaluate(store, row, attempt)
    assert _counts(store._db)["evaluations"] == 0
    assert _lifecycle(store, candidate.candidate_id) == "created"
    assert _stage(store, row["id"]) == "claim_audited"


def test_evaluation_audit_failure_keeps_v_and_evaluated(tmp_path, monkeypatch):
    store, _, row, attempt, candidate = _ready(tmp_path)
    assert _evaluate(store, row, attempt).status == "evaluated"

    def boom(*args, **kwargs):
        raise RuntimeError("audit sink down")

    monkeypatch.setattr(store._db, "insert_audit_log_uncommitted", boom)
    with pytest.raises(RuntimeError):
        _audit_evaluation(store, row, attempt)
    assert store.get_v2_evaluation(candidate.candidate_id) is not None
    assert _lifecycle(store, candidate.candidate_id) == "evaluated"
    assert _stage(store, row["id"]) == "evaluated"
    assert _audit_events(store, candidate.candidate_id) == ["claimed"]


def test_consume_failure_keeps_evaluated_audited(tmp_path, monkeypatch):
    store, _, row, attempt, candidate = _ready(tmp_path)
    assert _evaluate(store, row, attempt).status == "evaluated"
    assert _audit_evaluation(store, row, attempt).status == "evaluation_audited"

    def boom(*args, **kwargs):
        raise RuntimeError("stage advance failed")

    monkeypatch.setattr(store._db, "_advance_v2_attempt_stage_uncommitted", boom)
    with pytest.raises(RuntimeError):
        _consume(store, row, attempt)
    assert _lifecycle(store, candidate.candidate_id) == "evaluated"
    assert _stage(store, row["id"]) == "evaluation_audited"
    assert store.get_v2_evaluation(candidate.candidate_id) is not None


def test_consumed_audit_failure_keeps_consumed(tmp_path, monkeypatch):
    store, _, row, attempt, candidate = _ready(tmp_path)
    assert _evaluate(store, row, attempt).status == "evaluated"
    assert _audit_evaluation(store, row, attempt).status == "evaluation_audited"
    assert _consume(store, row, attempt).status == "consumed"

    def boom(*args, **kwargs):
        raise RuntimeError("audit sink down")

    monkeypatch.setattr(store._db, "insert_audit_log_uncommitted", boom)
    with pytest.raises(RuntimeError):
        _audit_consumption(store, row, attempt)
    assert _lifecycle(store, candidate.candidate_id) == "consumed"
    assert _stage(store, row["id"]) == "consumed"
    assert _audit_events(store, candidate.candidate_id) == ["claimed", "evaluated"]
    assert store.get_v2_evaluation(candidate.candidate_id) is not None


# ── caller transaction ownership ─────────────────────────────────────────


@pytest.mark.parametrize("method", ["evaluate", "audit_evaluation", "consume", "audit_consumption"])
def test_caller_transaction_is_preserved(tmp_path, method):
    store, _, row, attempt, candidate = _ready(tmp_path)
    if method in {"consume", "audit_consumption"}:
        assert _evaluate(store, row, attempt).status == "evaluated"
        assert _audit_evaluation(store, row, attempt).status == "evaluation_audited"
        if method == "audit_consumption":
            assert _consume(store, row, attempt).status == "consumed"
    elif method == "audit_evaluation":
        assert _evaluate(store, row, attempt).status == "evaluated"

    store._db._conn.execute("BEGIN IMMEDIATE")
    store._db._conn.execute(
        "INSERT INTO processed_event_ids(org_slug, feishu_event_id, processed_at, outcome) "
        "VALUES ('caller-org','evt-caller','2026-01-01T00:00:00Z','ok')"
    )
    calls = {
        "evaluate": _evaluate, "audit_evaluation": _audit_evaluation,
        "consume": _consume, "audit_consumption": _audit_consumption,
    }
    outcome = calls[method](store, row, attempt)
    assert outcome.status == "refused" and outcome.refusal_code == "transaction_owned"
    # The caller's pending write is untouched and can be committed.
    pending = store._db._conn.execute(
        "SELECT COUNT(*) FROM processed_event_ids WHERE feishu_event_id='evt-caller'"
    ).fetchone()[0]
    assert pending == 1
    store._db._conn.commit()
    assert store._db._conn.execute(
        "SELECT COUNT(*) FROM processed_event_ids WHERE feishu_event_id='evt-caller'"
    ).fetchone()[0] == 1


# ── schema drift ─────────────────────────────────────────────────────────


def test_post_claim_schema_drift_refuses_evaluation(tmp_path):
    store, _, row, attempt, candidate = _ready(tmp_path)
    store._db._conn.execute("CREATE INDEX thr229_c3c_drift ON tasks(status)")
    store._db._conn.commit()
    outcome = _evaluate(store, row, attempt)
    assert outcome.status == "refused" and outcome.refusal_code == "schema_drift"
    assert _counts(store._db)["evaluations"] == 0
