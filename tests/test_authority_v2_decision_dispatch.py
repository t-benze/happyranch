"""THR-229 checkpoint C3d3c2 — ordinary decision-dispatch claim/ack/refusal.

Drives the public ``AuthorityPolicyStore`` forwarders and the common-consumer
gate over the DB-owned synchronized transactions.  Every refusal asserts the
exact full SQLite ``iterdump`` (not a row count) is unchanged, so
corrupt/conflicting evidence can never be silently repaired.

The supplied completion is built through the REAL shipping reconstruction path
(``completion_report_from_result_row`` -> ``CompletionReport``/``NextStep``), so
the binding proof exercises the persisted projection a healthy real model
produces rather than invented helper attributes.  The broad integration suite is
SKIPPED under founder THR-243 seq42, never PASS.
"""

from __future__ import annotations

import json
import threading
import types
from unittest.mock import patch

import pytest

from runtime.infrastructure.database import Database
from runtime.models import CompletionReport, NextStep, TaskStatus
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
from runtime.orchestrator.orchestrator import completion_report_from_result_row
from runtime.orchestrator.active_authority_policy import load_session_policy_binding
from tests.test_authority_v2_attempt_admission import (
    MANAGER,
    SESSION_ID,
    TASK_ID,
    TEAM,
    _admit,
    _carrier_and_admission,
)
from tests.test_authority_v2_envelope_spend import (
    RESERVED,
    _BoundaryFailingConn,
    _RendezvousConn,
    _bind_reserved,
    _reserved_state,
    _row,
    _spend,
)
from tests.test_authority_v2_evaluation_stage import (
    _audit_consumption as _stage_audit_consumption,
    _audit_evaluation as _stage_audit_evaluation,
    _claim as _stage_claim,
    _claim_audit as _stage_claim_audit,
    _consume as _stage_consume,
    _evaluate as _stage_evaluate,
)
from tests.test_authority_v2_finalization_settlement import (
    _insert_ordinary_completion,
)
from tests.test_authority_v2_generation_admission import _published
from tests.test_authority_v2_publication_bookkeeping import (
    _append_stage_event,
    _dump,
    _stage_events,
)

CLAIMED = "decision_claimed"
APPLIED = "decision_applied"
INTERRUPTED = "decision_dispatch_interrupted"


# ── helpers ───────────────────────────────────────────────────────────────


def _spent_ready(tmp_path):
    """A spent, DEFAULT pointer-retired receipt in the ``ready`` state."""
    store, row, attempt, outcome, r2 = _reserved_state(tmp_path)
    result = _spend(store, row, outcome, r2)
    assert result.status == "spent", result
    return store, row, outcome, r2


def _report_for_result(store, result_id, **overrides) -> CompletionReport:
    """The REAL shipping reconstruction of one persisted result row."""
    base = completion_report_from_result_row(
        TASK_ID, _row(store, "task_results", "id=?", (result_id,)),
        fallback_agent=MANAGER,
    )
    if not overrides:
        return base
    if isinstance(overrides.get("decision"), dict):
        overrides = {**overrides, "decision": NextStep(**overrides["decision"])}
    return base.model_copy(update=overrides)


def _claim(store, row):
    return store.claim_v2_decision_dispatch(
        root_task_id=TASK_ID, manager_agent=MANAGER, result_id=row["id"],
    )


def _ack(store, row):
    return store.acknowledge_v2_decision_dispatch(
        root_task_id=TASK_ID, manager_agent=MANAGER, result_id=row["id"],
    )


def _refuse(store, row):
    return store.refuse_v2_decision_dispatch(
        root_task_id=TASK_ID, manager_agent=MANAGER, result_id=row["id"],
    )


def _receipt(store, r2):
    return store.get_v2_decision_receipt_for_result(
        root_task_id=TASK_ID, spending_result_id=r2,
    )


def _gate(store, report, result_row_id):
    from runtime.orchestrator.run_step import _v2_decision_dispatch_gate

    orch = types.SimpleNamespace(_db=store._db)
    return _v2_decision_dispatch_gate(orch, TASK_ID, report, result_row_id, MANAGER)


def _ordinary_state(tmp_path):
    """A real root with a persisted result and NO v2 lineage at all."""
    db = Database(tmp_path / "ordinary.db")
    db._conn.execute(
        "INSERT INTO tasks (id, status, assigned_agent, team, brief, task_type, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (
            TASK_ID, TaskStatus.IN_PROGRESS.value, MANAGER, TEAM, "b", "task",
            "2026-09-21T00:00:00+00:00", "2026-09-21T00:00:00+00:00",
        ),
    )
    db._conn.execute(
        "UPDATE tasks SET current_session_id=? WHERE id=?", (SESSION_ID, TASK_ID),
    )
    cursor = db._conn.execute(
        "INSERT INTO task_results "
        "(task_id, agent, session_id, status, output_summary, decision_json, "
        "confidence_score, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (TASK_ID, MANAGER, SESSION_ID, "completed", "ordinary",
         '{"action":"done"}', 90, "2026-09-21T00:00:00+00:00"),
    )
    db._conn.commit()
    return db, cursor.lastrowid


def _corrupt_claim_event(store, digest="0" * 64):
    event = _stage_events(store, CLAIMED)[0]
    event["report_digest"] = digest
    store._db._conn.execute(
        "UPDATE audit_log SET payload=? WHERE action='authority_policy_v2_result_stage' "
        "AND json_extract(payload,'$.stage')=?",
        (json.dumps(event), CLAIMED),
    )
    store._db._conn.commit()


# ── healthy claim -> ack and exact replay ─────────────────────────────────


def test_claim_then_ack_and_exact_replays(tmp_path):
    store, row, outcome, r2 = _spent_ready(tmp_path)
    preserved = {
        "r2": _row(store, "task_results", "id=?", (r2,)),
        "task": _row(store, "tasks", "id=?", (TASK_ID,)),
    }
    assert _receipt(store, r2)["decision_state"] == "ready"

    claimed = _claim(store, row)
    assert claimed.status == "claimed", claimed
    assert claimed.decision_state == "claimed"
    assert claimed.spending_result_id == r2
    assert _receipt(store, r2)["decision_state"] == "claimed"
    assert len(_stage_events(store, CLAIMED)) == 1
    # The claim authorizes a consumer entry but performs NO consumer/task effect.
    assert _row(store, "task_results", "id=?", (r2,)) == preserved["r2"]
    assert _row(store, "tasks", "id=?", (TASK_ID,)) == preserved["task"]

    # A duplicate claim after the committed claim never authorizes a consumer.
    dup = _claim(store, row)
    assert dup.status == "decision_pending" and dup.reason == "already_claimed"
    assert len(_stage_events(store, CLAIMED)) == 1

    applied = _ack(store, row)
    assert applied.status == "applied", applied
    assert applied.decision_state == "applied"
    assert _receipt(store, r2)["decision_state"] == "applied"
    assert len(_stage_events(store, APPLIED)) == 1

    # Exact applied replay is read-only.
    before = _dump(store)
    replay = _ack(store, row)
    assert replay.status == "already_applied_exact", replay
    assert _dump(store) == before

    # A claim after applied is refused; no second consumer authority.
    after = _claim(store, row)
    assert after.status == "decision_pending" and after.reason == "already_applied"
    assert _dump(store) == before


def test_claim_refuses_before_spend_and_for_foreign_result(tmp_path):
    store, row, attempt, outcome, r2 = _reserved_state(tmp_path)
    # Active (not yet spent) envelope is not a claimable decision receipt.
    before = _dump(store)
    result = _claim(store, row)
    assert result.status == "decision_pending", result
    assert result.reason in ("not_claimable", "identity_mismatch")
    assert _dump(store) == before

    _spend(store, row, outcome, r2)
    # An unknown spending result is provably the ordinary/v1 no-v2 path only
    # through the persisted-lineage classifier; the raw receipt reader still
    # returns None.  A report that does not match the persisted R2 body never
    # binds.
    assert _receipt(store, 999999) is None
    assert store.v2_decision_result_report_binds(
        root_task_id=TASK_ID, spending_result_id=r2,
        report=_report_for_result(store, r2),
    ) is True
    assert store.v2_decision_result_report_binds(
        root_task_id=TASK_ID, spending_result_id=r2,
        report=_report_for_result(store, r2, decision={"action": "delegate"}),
    ) is False


def test_claim_refuses_non_current_owner(tmp_path):
    store, row, outcome, r2 = _spent_ready(tmp_path)
    store._db._conn.execute(
        "UPDATE tasks SET current_session_id=? WHERE id=?", ("sess-other", TASK_ID),
    )
    store._db._conn.commit()
    before = _dump(store)
    result = _claim(store, row)
    assert result.status == "decision_pending" and result.reason == "owner_lost", result
    assert _dump(store) == before


def test_ack_requires_a_committed_claim(tmp_path):
    store, row, outcome, r2 = _spent_ready(tmp_path)
    before = _dump(store)
    result = _ack(store, row)
    assert result.status == "ack_pending" and result.reason == "not_claimed", result
    assert _dump(store) == before
    assert _stage_events(store, APPLIED) == []


def test_claim_refuses_caller_transaction_preserving_pending_mutation(tmp_path):
    store, row, outcome, r2 = _spent_ready(tmp_path)
    real = store._db._conn
    real.execute("BEGIN IMMEDIATE")
    real.execute(
        "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) "
        "VALUES (?,?,?,?,?)",
        (TASK_ID, MANAGER, "pending_probe", "{}", "2026-09-21T00:00:00+00:00"),
    )
    assert real.in_transaction
    before = _dump(store)
    result = _claim(store, row)
    # Caller-owned transaction is refused BEFORE any BEGIN/ROLLBACK, so the
    # pending mutation survives byte-for-byte.
    assert result.status == "decision_pending" and result.reason == "transaction_owned"
    assert real.in_transaction
    assert _dump(store) == before
    real.rollback()
    assert store._db._conn.in_transaction is False
    assert _dump(store) != before  # the probe rolled back too
    assert _claim(store, row).status == "claimed"


@pytest.mark.parametrize(
    "fragment",
    ["SET decision_state=?", ],
)
def test_claim_sql_boundary_failure_rolls_back(tmp_path, fragment):
    store, row, outcome, r2 = _spent_ready(tmp_path)
    before = _dump(store)
    real = store._db._conn
    wrapper = _BoundaryFailingConn(real, sql_fragment=fragment, occurrence=1)
    store._db._conn = wrapper
    try:
        result = _claim(store, row)
    finally:
        store._db._conn = real
    assert wrapper.fired == f"sql:{fragment}", wrapper.fired
    assert result.status == "decision_pending" and result.reason == "claim_failed"
    assert _dump(store) == before
    assert _stage_events(store, CLAIMED) == []
    assert _receipt(store, r2)["decision_state"] == "ready"
    # Exact claim retry after the boundary failure succeeds without a second spend.
    assert _claim(store, row).status == "claimed"
    assert len(_stage_events(store, "spent")) == 1


def test_claim_audit_and_commit_failure_roll_back_then_retry(tmp_path):
    for index, wrapper_kwargs in enumerate(
        ({"audit_stage": CLAIMED}, {"fail_commit": True})
    ):
        case_dir = tmp_path / f"case{index}"
        case_dir.mkdir()
        store, row, outcome, r2 = _spent_ready(case_dir)
        before = _dump(store)
        real = store._db._conn
        wrapper = _BoundaryFailingConn(real, **wrapper_kwargs)
        store._db._conn = wrapper
        try:
            result = _claim(store, row)
        finally:
            store._db._conn = real
        assert wrapper.fired is not None, wrapper_kwargs
        assert result.status == "decision_pending" and result.reason == "claim_failed"
        assert _dump(store) == before
        assert _stage_events(store, CLAIMED) == []
        assert _claim(store, row).status == "claimed"


def test_claim_two_connections_one_winner(tmp_path):
    store, row, outcome, r2 = _spent_ready(tmp_path)
    other = AuthorityPolicyStore(Database(store._db.db_path))
    barrier = threading.Barrier(2)
    results: dict[str, object] = {}
    store_real = store._db._conn
    other_real = other._db._conn
    store._db._conn = _RendezvousConn(store_real, barrier)
    other._db._conn = _RendezvousConn(other_real, barrier)

    def run(name, target):
        results[name] = _claim(target, row)

    threads = [
        threading.Thread(target=run, args=("a", store)),
        threading.Thread(target=run, args=("b", other)),
    ]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
    finally:
        store._db._conn = store_real
        other._db._conn = other_real
    statuses = sorted(result.status for result in results.values())
    assert statuses == ["claimed", "decision_pending"], statuses
    reasons = sorted(
        result.reason for result in results.values() if result.reason is not None
    )
    assert reasons == ["already_claimed"], reasons
    assert len(_stage_events(store, CLAIMED)) == 1


# ── interruption refusal ──────────────────────────────────────────────────


def test_refusal_after_claim_escalates_current_owner_and_preserves_receipt(tmp_path):
    store, row, outcome, r2 = _spent_ready(tmp_path)
    assert _claim(store, row).status == "claimed"
    r2_before = _row(store, "task_results", "id=?", (r2,))
    dispatch_before = _row(
        store, "authority_policy_v2_root_dispatch", "root_task_id=?", (TASK_ID,),
    )

    result = _refuse(store, row)
    assert result.status == "refused", result
    assert result.decision_state == "refused"
    assert result.refusal_code == INTERRUPTED
    assert _receipt(store, r2)["decision_state"] == "refused"
    assert len(_stage_events(store, INTERRUPTED)) == 1
    assert len(_stage_events(store, CLAIMED)) == 1
    # Spent envelope and causal evidence are preserved; only the pointer/obligation
    # bookkeeping is untouched (D stays retired).
    env = store.get_v2_continue_envelope(_receipt(store, r2)["envelope_id"])
    assert env.lifecycle_state == "consumed" and env.spending_result_id == r2
    assert _row(store, "task_results", "id=?", (r2,)) == r2_before
    assert _row(
        store, "authority_policy_v2_root_dispatch", "root_task_id=?", (TASK_ID,),
    ) == dispatch_before
    task = _row(store, "tasks", "id=?", (TASK_ID,))
    assert task["status"] == TaskStatus.ESCALATED.value
    assert task["block_kind"] is None

    before = _dump(store)
    replay = _refuse(store, row)
    assert replay.status == "already_refused", replay
    assert _dump(store) == before


def test_refusal_preserves_cancelled_task_exactly(tmp_path):
    store, row, outcome, r2 = _spent_ready(tmp_path)
    assert _claim(store, row).status == "claimed"
    store._db._conn.execute(
        "UPDATE tasks SET cancelled_at=?, status=? WHERE id=?",
        ("2026-09-21T00:00:00+00:00", TaskStatus.CANCELLED.value, TASK_ID),
    )
    store._db._conn.commit()
    cancelled = _row(store, "tasks", "id=?", (TASK_ID,))

    result = _refuse(store, row)
    assert result.status == "refused", result
    assert _row(store, "tasks", "id=?", (TASK_ID,)) == cancelled
    assert _receipt(store, r2)["decision_state"] == "refused"


def test_refusal_after_applied_is_refused_and_read_only(tmp_path):
    store, row, outcome, r2 = _spent_ready(tmp_path)
    assert _claim(store, row).status == "claimed"
    assert _ack(store, row).status == "applied"
    before = _dump(store)
    result = _refuse(store, row)
    assert result.status == "refusal_pending", result
    assert result.reason in ("already_applied", "not_claimable")
    assert _dump(store) == before


def test_refusal_requires_a_committed_claim(tmp_path):
    store, row, outcome, r2 = _spent_ready(tmp_path)
    before = _dump(store)
    result = _refuse(store, row)
    assert result.status == "refusal_pending" and result.reason == "not_claimable"
    assert _dump(store) == before


def test_refusal_audit_failure_retains_discoverable_claimed_state(tmp_path):
    store, row, outcome, r2 = _spent_ready(tmp_path)
    assert _claim(store, row).status == "claimed"
    before = _dump(store)
    real = store._db._conn
    wrapper = _BoundaryFailingConn(real, audit_stage=INTERRUPTED)
    store._db._conn = wrapper
    try:
        result = _refuse(store, row)
    finally:
        store._db._conn = real
    assert wrapper.fired == f"audit:{INTERRUPTED}", wrapper.fired
    assert result.status == "refusal_pending" and result.reason == "refusal_failed"
    assert _dump(store) == before
    # The claimed state stays discoverable and the refusal can be retried.
    assert _receipt(store, r2)["decision_state"] == "claimed"
    assert _refuse(store, row).status == "refused"


# ── corrupt/conflicting prior decision evidence ────────────────────────────


def test_claim_refuses_malformed_related_decision_event(tmp_path):
    store, row, outcome, r2 = _spent_ready(tmp_path)
    # An extra-key decision event with an exact causal reference is a conflict.
    _append_stage_event(store, {
        "stage": CLAIMED, "attempt_id": "APV2R-" + "0" * 64,
        "candidate_id": "APV2C-" + "0" * 64, "result_id": row["id"],
        "envelope_id": "APV2E-" + "0" * 64,
        "notification_id": outcome.notification_id,
        "generation_id": outcome.notification_id, "next_session_id": RESERVED,
        "spending_result_id": r2, "report_digest": "0" * 64, "extra": 1,
    })
    before = _dump(store)
    result = _claim(store, row)
    assert result.status == "decision_pending", result
    assert result.reason in ("evidence_drift", "claim_failed")
    assert _dump(store) == before
    assert _stage_events(store, "spent") != []


def test_claim_refuses_present_null_spending_identity(tmp_path):
    store, row, outcome, r2 = _spent_ready(tmp_path)
    # A decision event carrying a present-null spending result is a conflict,
    # never ordinary absence.
    _append_stage_event(store, {
        "stage": CLAIMED, "attempt_id": "APV2R-" + "0" * 64,
        "candidate_id": "APV2C-" + "0" * 64, "result_id": row["id"],
        "envelope_id": "APV2E-" + "0" * 64,
        "notification_id": outcome.notification_id,
        "generation_id": outcome.notification_id, "next_session_id": RESERVED,
        "spending_result_id": None, "report_digest": "0" * 64,
    })
    before = _dump(store)
    result = _claim(store, row)
    assert result.status == "decision_pending"
    assert _dump(store) == before


# ── complete closed evidence on every replay ──────────────────────────────


def test_applied_replay_refuses_corrupted_claim_digest(tmp_path):
    store, row, outcome, r2 = _spent_ready(tmp_path)
    assert _claim(store, row).status == "claimed"
    assert _ack(store, row).status == "applied"
    _corrupt_claim_event(store)
    before = _dump(store)
    replay = _ack(store, row)
    assert replay.status == "ack_pending" and replay.reason == "evidence_drift", replay
    assert _dump(store) == before


def test_refused_replay_refuses_corrupted_claim_digest(tmp_path):
    store, row, outcome, r2 = _spent_ready(tmp_path)
    assert _claim(store, row).status == "claimed"
    assert _refuse(store, row).status == "refused"
    _corrupt_claim_event(store)
    before = _dump(store)
    replay = _refuse(store, row)
    assert replay.status == "refusal_pending" and replay.reason == "evidence_drift"
    assert _dump(store) == before


def test_applied_replay_refuses_missing_or_duplicate_claim_event(tmp_path):
    for index, duplicate in enumerate((False, True)):
        case_dir = tmp_path / f"case{index}"
        case_dir.mkdir()
        store, row, outcome, r2 = _spent_ready(case_dir)
        assert _claim(store, row).status == "claimed"
        assert _ack(store, row).status == "applied"
        conn = store._db._conn
        if duplicate:
            event = _stage_events(store, CLAIMED)[0]
            conn.execute(
                "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) "
                "VALUES (?,?,?,?,?)",
                (TASK_ID, MANAGER, "authority_policy_v2_result_stage",
                 json.dumps(event), "2026-09-21T00:00:01+00:00"),
            )
        else:
            conn.execute(
                "DELETE FROM audit_log WHERE action='authority_policy_v2_result_stage' "
                "AND json_extract(payload,'$.stage')=?",
                (CLAIMED,),
            )
        conn.commit()
        before = _dump(store)
        replay = _ack(store, row)
        assert replay.status == "ack_pending" and replay.reason == "evidence_drift"
        assert _dump(store) == before


# ── authentic generation B through the ACTUAL public stages ───────────────
#
# The previous acceptance assertion staged B by cloning A's causal rows
# (``_point_dispatch_at_replacement``); B was never authenticated as evidence.
# These cases instead produce B through the real public lifecycle with its own
# reserved-session attempt/binding/candidate/evaluation/continuation/
# publication/admission identities and audits, then prove that A's late
# acknowledgement and A's interruption refusal settle ONLY A and preserve
# B/task/owner/audits byte-for-byte.  A's exact replay never remints or spends B.

_B_DECISION_STAGES = {
    "decision_claimed", "decision_applied", "decision_dispatch_interrupted",
}


def _admitted_reserved_result(store):
    """Admit the reserved session's REAL manager result (B's causal result)."""
    binding = load_session_policy_binding(
        db=store._db, task_id=TASK_ID, session_id=RESERVED, agent_name=MANAGER,
    )
    assert binding is not None
    carrier, admission = _carrier_and_admission(binding)
    assert _admit(store, carrier, admission, session_id=RESERVED) is True
    row = store._db.get_latest_task_result(TASK_ID, MANAGER, RESERVED)
    attempt = store._db.get_authority_policy_v2_attempt_for_result(row["id"])
    assert attempt is not None
    return row, attempt


def _finish_a_and_bind_reserved_b(store, row, outcome):
    """Admit+settle generation A, bind the reserved session, admit B's causal
    result and spend A, leaving D ``retired`` and the reserved R2 result ready.

    This is the shared, AUTHENTIC successor-generation seed: every step is a
    real public store transaction.  No row is cloned and no pointer is edited by
    hand.
    """
    admitted = store.try_claim_v2_continuation_generation(
        root_task_id=TASK_ID, manager_agent=MANAGER,
        manager_session_id=SESSION_ID, result_id=row["id"],
        generation_id=outcome.notification_id, next_session_id=RESERVED,
    )
    assert admitted.status == "claimed", admitted
    settled = store.settle_v2_continuation_generation_admission(
        root_task_id=TASK_ID, manager_agent=MANAGER,
        manager_session_id=SESSION_ID, result_id=row["id"],
        generation_id=outcome.notification_id, next_session_id=RESERVED,
    )
    assert settled.status == "settled", settled
    _bind_reserved(store)
    r2_row, attempt_b = _admitted_reserved_result(store)
    spent = _spend(store, row, outcome, r2_row["id"])
    assert spent.status == "spent", spent
    return r2_row, attempt_b


def _spent_ready_b(tmp_path):
    """A spent/retired generation A whose causal spending result IS an admitted
    manager result for the reserved session (the authentic generation-B seed)."""
    store, row, _attempt, outcome, _claimed = _published(tmp_path)
    r2_row, attempt_b = _finish_a_and_bind_reserved_b(store, row, outcome)
    return store, row, outcome, r2_row, attempt_b


def _drive_generation_b_pending(store, r2_row, attempt_b):
    """Drive AUTHENTIC B through the real public pre-publication stages and stop
    with a COHERENT ``D pending(B)`` / ``N needed`` root.

    B owns its reserved-session R2 attempt/binding/candidate/evaluation/
    continuation identities and audits; the retired A pointer is advanced by the
    genuine forward-only ``retired -> pending`` CAS inside finalization, never by
    cloned rows or an arbitrary pointer edit.  The returned generation is ready
    for the real publisher/claim (and later generation admission).
    """
    row, attempt = r2_row, attempt_b
    session = {"session_id": RESERVED}
    assert _stage_claim(store, row, attempt, **session).status == "claimed"
    assert _stage_claim_audit(store, row, attempt, **session).status == "claim_audited"
    assert _stage_evaluate(store, row, attempt, **session).status == "evaluated"
    assert _stage_audit_evaluation(store, row, attempt, **session).status == "evaluation_audited"
    assert _stage_consume(store, row, attempt, **session).status == "consumed"
    assert _stage_audit_consumption(store, row, attempt, **session).status == "consumed_audited"
    finalized = store.finalize_v2_continuation(
        root_task_id=TASK_ID, manager_agent=MANAGER, manager_session_id=RESERVED,
        result_id=row["id"], origin_boot_id=attempt.origin_boot_id,
        owner_attempt_id=attempt.owner_attempt_id,
    )
    assert finalized.status == "continued", finalized
    generation = finalized.notification_id
    _insert_ordinary_completion(store, row["id"])
    settled = store.settle_v2_continuation_receipt(
        root_task_id=TASK_ID, manager_agent=MANAGER, manager_session_id=RESERVED,
        result_id=row["id"],
    )
    assert settled.status == "settled", settled
    return generation


def _drive_generation_b(store, r2_row, attempt_b):
    """Produce generation B through the REAL public stages.

    B owns its reserved-session R2 attempt/binding/candidate/evaluation/
    continuation/publication/admission identities and audits; the retired A
    pointer is advanced by the genuine forward-only ``retired -> pending`` CAS,
    never by cloned rows or an arbitrary pointer edit.
    """
    row, attempt = r2_row, attempt_b
    generation = _drive_generation_b_pending(store, r2_row, attempt_b)
    store.bind_v2_process_boot_id(attempt.origin_boot_id)
    claimed = store.claim_v2_notification_publication(
        root_task_id=TASK_ID, manager_agent=MANAGER, manager_session_id=RESERVED,
        result_id=row["id"],
    )
    assert claimed.status == "claimed", claimed
    published = store.acknowledge_v2_notification_publication(
        root_task_id=TASK_ID, manager_agent=MANAGER, manager_session_id=RESERVED,
        result_id=row["id"], publication_attempt=claimed.publication_attempt,
        publisher_boot_id=claimed.publisher_boot_id,
    )
    assert published.status == "published", published
    next_b = "sess-authentic-generation-b"
    admitted = store.try_claim_v2_continuation_generation(
        root_task_id=TASK_ID, manager_agent=MANAGER, manager_session_id=RESERVED,
        result_id=row["id"], generation_id=generation, next_session_id=next_b,
    )
    assert admitted.status == "claimed", admitted
    settled_b = store.settle_v2_continuation_generation_admission(
        root_task_id=TASK_ID, manager_agent=MANAGER, manager_session_id=RESERVED,
        result_id=row["id"], generation_id=generation, next_session_id=next_b,
    )
    assert settled_b.status == "settled", settled_b
    dispatch = store.get_v2_root_dispatch(TASK_ID)
    assert dispatch.generation_id == generation and dispatch.state == "admitted"
    return generation


def _b_state(store, generation):
    """The complete B/task/owner evidence that A's settlement must preserve."""
    db = store._db
    notification = db.get_authority_policy_v2_recovery_notification(generation)
    envelope = store.get_v2_continue_envelope(notification.envelope_id)

    def one(sql, *params):
        row = db._conn.execute(sql, params).fetchone()
        return dict(row) if row is not None else None

    return {
        "notification": one(
            "SELECT * FROM authority_policy_v2_recovery_notifications "
            "WHERE notification_id=?", generation,
        ),
        "envelope": one(
            "SELECT * FROM authority_policy_v2_continue_envelopes "
            "WHERE envelope_id=?", notification.envelope_id,
        ),
        "dispatch": one(
            "SELECT * FROM authority_policy_v2_root_dispatch WHERE root_task_id=?",
            TASK_ID,
        ),
        "attempt": one(
            "SELECT * FROM authority_policy_v2_attempts WHERE result_id=?",
            notification.result_id,
        ),
        "candidate": one(
            "SELECT * FROM authority_policy_v2_candidates WHERE candidate_id=?",
            envelope.candidate_id,
        ),
        "task": one("SELECT * FROM tasks WHERE id=?", TASK_ID),
        "candidate_audits": [
            dict(r) for r in db._conn.execute(
                "SELECT * FROM authority_policy_v2_candidate_audit "
                "WHERE candidate_id=? ORDER BY id", (envelope.candidate_id,),
            ).fetchall()
        ],
        "related_stage_audits": [
            dict(a) for a in db.list_authority_policy_v2_result_stage_audits(
                root_task_id=TASK_ID, manager_agent=MANAGER,
            )
            if a["payload"].get("stage") not in _B_DECISION_STAGES
        ],
    }


def test_authentic_generation_b_late_acknowledgement_preserves_b(tmp_path):
    """A's LATE acknowledgement settles only A; B is preserved byte-for-byte."""
    store, row, _outcome, r2_row, attempt_b = _spent_ready_b(tmp_path)
    assert _claim(store, row).status == "claimed"
    generation = _drive_generation_b(store, r2_row, attempt_b)
    before = _b_state(store, generation)

    acked = _ack(store, row)
    assert acked.status == "applied", acked
    assert _receipt(store, r2_row["id"])["decision_state"] == "applied"
    assert len(_stage_events(store, APPLIED)) == 1
    assert _b_state(store, generation) == before

    # An exact A acknowledgement replay is read-only: it never remints, spends
    # or otherwise mutates the preserved generation B.
    assert _ack(store, row).status == "already_applied_exact"
    assert _b_state(store, generation) == before


def test_authentic_generation_b_interruption_refusal_preserves_b(tmp_path):
    """A's interruption refusal settles only A; B is preserved byte-for-byte."""
    store, row, _outcome, r2_row, attempt_b = _spent_ready_b(tmp_path)
    assert _claim(store, row).status == "claimed"
    generation = _drive_generation_b(store, r2_row, attempt_b)
    before = _b_state(store, generation)

    refused = _refuse(store, row)
    assert refused.status == "refused", refused
    assert _receipt(store, r2_row["id"])["decision_state"] == "refused"
    assert _b_state(store, generation) == before
    assert len(_stage_events(store, APPLIED)) == 0
    assert len(_stage_events(store, INTERRUPTED)) == 1


# ── persisted-report binding through the shipping representation ──────────


def test_report_binds_real_model_confidence_lists_and_local_ci(tmp_path):
    store, row, outcome, r2 = _spent_ready(tmp_path)
    report = _report_for_result(store, r2)
    assert isinstance(report, CompletionReport)
    assert report.confidence == 90
    assert report.decision is not None and report.decision.action == "done"
    assert store.v2_decision_result_report_binds(
        root_task_id=TASK_ID, spending_result_id=r2, report=report,
    ) is True
    # The legacy helper attribute is NOT the model field and never binds.
    legacy = types.SimpleNamespace(
        output_summary="reserved continuation", confidence_score=90,
        status="completed", output_dir=None, verdict=None, risks_flagged=None,
        decision={"action": "done"}, waiting_on_job_ids=None, local_ci=None,
    )
    assert store.v2_decision_result_report_binds(
        root_task_id=TASK_ID, spending_result_id=r2, report=legacy,
    ) is False


@pytest.mark.parametrize(
    "override",
    [
        {"output_summary": "drifted summary"},
        {"confidence": 12},
        {"status": "failed"},
        {"output_dir": "/tmp/drift"},
        {"verdict": "REQUEST_CHANGES"},
        {"risks_flagged": ["r1"]},
        {"waiting_on_job_ids": ["JOB-1"]},
        {"local_ci": {"command": "scripts/local_ci.sh all", "exit_code": 0}},
        {"decision": {"action": "done", "summary": "unpersisted effect"}},
        {"decision": {"action": "delegate", "agent": "dev_agent", "prompt": "p"}},
        {"decision": {"action": "delegate", "then": [
            {"agent": "dev_agent", "prompt": "p"},
        ]}},
        {"decision": {"action": "fanout", "children": [
            {"agent": "dev_agent", "prompt": "p"},
        ]}},
        {"decision": {"action": "delegate", "revisit_of_task_id": "TASK-1"}},
        {"decision": {"action": "delegate", "attachments": [
            {"storage_key": "upload-1"},
        ]}},
    ],
)
def test_report_binds_refuses_complete_decision_and_material_drift(tmp_path, override):
    store, row, outcome, r2 = _spent_ready(tmp_path)
    drifted = _report_for_result(store, r2, **override)
    assert store.v2_decision_result_report_binds(
        root_task_id=TASK_ID, spending_result_id=r2, report=drifted,
    ) is False


def test_report_binds_refuses_malformed_persisted_decision(tmp_path):
    store, row, outcome, r2 = _spent_ready(tmp_path)
    store._db._conn.execute(
        "UPDATE task_results SET decision_json=? WHERE id=?", ("{not json", r2),
    )
    store._db._conn.commit()
    report = _report_for_result(store, r2)
    assert store.v2_decision_result_report_binds(
        root_task_id=TASK_ID, spending_result_id=r2, report=report,
    ) is False


# ── common-consumer gate classification (real DB provenance) ──────────────


def test_gate_ordinary_on_fresh_ordinary_root(tmp_path):
    db, rid = _ordinary_state(tmp_path)
    row = dict(db._conn.execute(
        "SELECT * FROM task_results WHERE id=?", (rid,)
    ).fetchone())
    report = completion_report_from_result_row(TASK_ID, row, fallback_agent=MANAGER)
    orch = types.SimpleNamespace(_db=db)
    from runtime.orchestrator.run_step import _v2_decision_dispatch_gate

    assert _v2_decision_dispatch_gate(
        orch, TASK_ID, report, rid, MANAGER,
    ).kind == "ordinary"
    # A missing/mistyped identity on a genuinely ordinary root is ordinary too.
    assert _v2_decision_dispatch_gate(
        orch, TASK_ID, report, None, MANAGER,
    ).kind == "ordinary"


def test_gate_reserved_r2_spends_then_claims_once(tmp_path):
    store, row, attempt, outcome, r2 = _reserved_state(tmp_path)
    assert _stage_events(store, "spent") == []
    gate = _gate(store, _report_for_result(store, r2), r2)
    assert gate.kind == "admitted", gate
    # The real spend writer committed exactly once, then exactly one claim.
    assert len(_stage_events(store, "spent")) == 1
    assert len(_stage_events(store, CLAIMED)) == 1
    assert _receipt(store, r2)["decision_state"] == "claimed"
    # A restart/duplicate never admits a second consumer.
    assert _gate(store, _report_for_result(store, r2), r2).kind == "skip"


def test_gate_causal_and_foreign_never_ordinary(tmp_path):
    store, row, outcome, r2 = _spent_ready(tmp_path)
    # Causal R on a spent root: continuation bookkeeping only.
    assert _gate(store, _report_for_result(store, r2), row["id"]).kind == "skip"
    # Foreign integer / missing / bool identity on a live lineage: never ordinary.
    assert _gate(store, _report_for_result(store, r2), 999999).kind == "skip"
    assert _gate(store, _report_for_result(store, r2), None).kind == "skip"
    assert _gate(store, _report_for_result(store, r2), True).kind == "skip"


def test_gate_admits_ready_receipt_once_then_skips_restart(tmp_path):
    store, row, outcome, r2 = _spent_ready(tmp_path)
    gate = _gate(store, _report_for_result(store, r2), r2)
    assert gate.kind == "admitted", gate
    assert _receipt(store, r2)["decision_state"] == "claimed"
    assert len(_stage_events(store, CLAIMED)) == 1

    # A restart (or duplicate) sees ``claimed`` and never re-admits; the gate
    # performs the audited interruption refusal instead.
    assert _gate(store, _report_for_result(store, r2), r2).kind == "skip"
    assert _receipt(store, r2)["decision_state"] == "refused"
    assert len(_stage_events(store, INTERRUPTED)) == 1
    task = _row(store, "tasks", "id=?", (TASK_ID,))
    assert task["status"] == TaskStatus.ESCALATED.value

    # An applied receipt is terminal: read-only skip.
    applied_store, applied_row, _, applied_r2 = _spent_ready(
        _fresh_dir(tmp_path, "applied")
    )
    assert _claim(applied_store, applied_row).status == "claimed"
    assert _ack(applied_store, applied_row).status == "applied"
    assert _gate(
        applied_store, _report_for_result(applied_store, applied_r2), applied_r2,
    ).kind == "skip"


def test_gate_skips_conflicting_report_and_corrupt_receipt(tmp_path):
    store, row, outcome, r2 = _spent_ready(tmp_path)
    # A report whose full decision/material body drifts is never admitted.
    gate = _gate(
        store, _report_for_result(store, r2, decision={"action": "delegate"}), r2,
    )
    assert gate.kind == "skip"
    assert _receipt(store, r2)["decision_state"] == "ready"
    assert _stage_events(store, CLAIMED) == []

    # A corrupt canonical body is never ordinary permission.
    store._db._conn.execute(
        "UPDATE authority_policy_v2_continue_envelopes SET canonical_payload_json='{'"
    )
    store._db._conn.commit()
    assert _gate(store, _report_for_result(store, r2), r2).kind == "skip"


def test_gate_lookup_failure_or_absent_reader_is_never_ordinary(tmp_path, monkeypatch):
    store, row, outcome, r2 = _spent_ready(tmp_path)
    original = store._db.authority_policy_v2_completion_dispatch_context

    def boom(**kwargs):
        raise RuntimeError("read failure")

    monkeypatch.setattr(
        store._db, "authority_policy_v2_completion_dispatch_context", boom,
    )
    assert _gate(store, _report_for_result(store, r2), r2).kind == "skip"

    # A missing classifier (lightweight/fake store) is fail-closed, never the
    # ordinary path.
    monkeypatch.setattr(
        store._db, "authority_policy_v2_completion_dispatch_context", None,
    )
    assert _gate(store, _report_for_result(store, r2), r2).kind == "skip"
    monkeypatch.setattr(
        store._db, "authority_policy_v2_completion_dispatch_context", original,
    )


def _establish_later_result(store, *, session="sess-later-live", agent=MANAGER):
    """A GENUINE later result through the supported session + admission seams.

    ``_run_agent`` publishes the durable invocation identity with
    ``update_task(assigned_agent=..., current_session_id=...)`` immediately
    before launch; the completion is then admitted through the REAL callback
    admission ``admit_task_completion_callback``, which itself enforces that
    the agent/session match the task's current durable owner.  This is the
    supported later lifecycle -- not a bare row insert or arbitrary owner
    patching.
    """
    store._db.update_task(
        TASK_ID, assigned_agent=agent, current_session_id=session,
    )
    assert store._db.admit_task_completion_callback(
        task_id=TASK_ID, agent=agent, session_id=session,
        output_summary="later ordinary completion", confidence_score=80,
        decision_json=json.dumps({"action": "done"}),
    ) is True
    row = store._db.get_latest_task_result(TASK_ID, agent, session)
    assert row is not None
    return row["id"]


def test_gate_terminal_lineage_returns_later_result_to_ordinary(tmp_path):
    store, row, outcome, r2 = _spent_ready(tmp_path)
    assert _claim(store, row).status == "claimed"
    assert _ack(store, row).status == "applied"
    later = _establish_later_result(store)
    context = store._db.authority_policy_v2_completion_dispatch_context(
        root_task_id=TASK_ID, result_row_id=later,
    )
    assert context.kind == "later", context
    assert _gate(
        store, _report_for_result(store, later), later,
    ).kind == "ordinary"


def test_gate_terminal_invalid_later_provenance_is_foreign(tmp_path):
    """A bare/empty/wrong-agent session is NEVER ordinary on a terminal root."""
    for index, (name, agent, session) in enumerate((
        ("unbound_session", MANAGER, "sess-never-launched"),
        ("empty_session", MANAGER, ""),
        ("wrong_agent", "dev_agent", "sess-never-launched"),
    )):
        store, row, outcome, r2 = _spent_ready(_fresh_dir(tmp_path, f"prov-{index}"))
        assert _claim(store, row).status == "claimed"
        assert _ack(store, row).status == "applied"
        store._db.insert_task_result(
            task_id=TASK_ID, agent=agent, session_id=session,
            output_summary="unbound later", confidence_score=90,
            decision_json='{"action":"done"}',
        )
        row_id = store._db.get_task_results(TASK_ID)[-1]["id"]
        before = _dump(store)
        context = store._db.authority_policy_v2_completion_dispatch_context(
            root_task_id=TASK_ID, result_row_id=row_id,
        )
        assert context.kind == "foreign", (name, context)
        assert _gate(
            store, _report_for_result(store, row_id), row_id,
        ).kind == "skip", name
        assert _dump(store) == before, name


def test_gate_terminal_later_report_drift_never_ordinary(tmp_path):
    """A genuine later result with a drifted supplied report never authorizes."""
    store, row, outcome, r2 = _spent_ready(tmp_path)
    assert _claim(store, row).status == "claimed"
    assert _ack(store, row).status == "applied"
    later = _establish_later_result(store)
    # The full decision body drift is caught by the material-identity binding.
    assert store._db.authority_policy_v2_decision_result_report_binds(
        root_task_id=TASK_ID, spending_result_id=later,
        report=_report_for_result(
            store, later, decision={"action": "delegate", "agent": "dev_agent",
                                    "prompt": "different effect"},
        ),
    ) is False
    before = _dump(store)
    assert _gate(
        store,
        _report_for_result(
            store, later, decision={"action": "delegate", "agent": "dev_agent",
                                    "prompt": "different effect"},
        ),
        later,
    ).kind == "skip"
    assert _dump(store) == before
    # The exact persisted material identity is the only ordinary path.
    assert _gate(
        store, _report_for_result(store, later), later,
    ).kind == "ordinary"


def test_wrapper_terminal_invalid_later_zero_entries_valid_one(tmp_path):
    """Invalid later provenance yields ZERO normal-body entries with unchanged
    residue; the genuine later result yields exactly one."""
    import runtime.orchestrator.run_step as run_step

    invalid, irow, _ioutcome, _ir2 = _spent_ready(_fresh_dir(tmp_path, "zero"))
    assert _claim(invalid, irow).status == "claimed"
    assert _ack(invalid, irow).status == "applied"
    invalid._db.insert_task_result(
        task_id=TASK_ID, agent=MANAGER, session_id="sess-never-launched",
        output_summary="unbound later", confidence_score=90,
        decision_json='{"action":"done"}',
    )
    bad_id = invalid._db.get_task_results(TASK_ID)[-1]["id"]
    before = _dump(invalid)
    entries: list[dict] = []
    with patch.object(
        run_step, "_consume_completion_report_body",
        lambda *a, **kw: entries.append(kw),
    ):
        run_step._consume_completion_report(
            types.SimpleNamespace(_db=invalid._db), TASK_ID,
            _report_for_result(invalid, bad_id), result_row_id=bad_id,
        )
    assert entries == []
    assert _dump(invalid) == before

    valid, vrow, _voutcome, _vr2 = _spent_ready(_fresh_dir(tmp_path, "one"))
    assert _claim(valid, vrow).status == "claimed"
    assert _ack(valid, vrow).status == "applied"
    later = _establish_later_result(valid)
    entries = []
    with patch.object(
        run_step, "_consume_completion_report_body",
        lambda *a, **kw: entries.append(kw),
    ):
        run_step._consume_completion_report(
            types.SimpleNamespace(_db=valid._db), TASK_ID,
            _report_for_result(valid, later), result_row_id=later,
        )
    assert len(entries) == 1
    assert entries[0].get("result_row_id") == later


# ── common-consumer wrapper identity seam ─────────────────────────────────


def test_wrapper_runs_body_once_and_acks_causal_identity(tmp_path):
    import runtime.orchestrator.run_step as run_step

    store, row, outcome, r2 = _spent_ready(tmp_path)
    report = _report_for_result(store, r2)
    seen: list[object] = []

    def body(orch, task_id, report, **kwargs):
        seen.append(kwargs.get("result_row_id"))

    orch = types.SimpleNamespace(_db=store._db)
    with patch.object(run_step, "_consume_completion_report_body", body):
        run_step._consume_completion_report(orch, TASK_ID, report, result_row_id=r2)
    assert seen == [r2]
    # The acknowledgement used the CAUSAL identity, so ``applied`` committed.
    assert _receipt(store, r2)["decision_state"] == "applied"
    assert len(_stage_events(store, APPLIED)) == 1
    assert len(_stage_events(store, CLAIMED)) == 1


def test_wrapper_refuses_causal_identity_on_exception(tmp_path):
    import runtime.orchestrator.run_step as run_step

    store, row, outcome, r2 = _spent_ready(tmp_path)
    report = _report_for_result(store, r2)

    def body(orch, task_id, report, **kwargs):
        raise RuntimeError("isolated consumer failure")

    orch = types.SimpleNamespace(_db=store._db)
    with patch.object(run_step, "_consume_completion_report_body", body):
        with pytest.raises(RuntimeError):
            run_step._consume_completion_report(orch, TASK_ID, report, result_row_id=r2)
    # The refusal used the CAUSAL identity, so one interruption event committed.
    assert _receipt(store, r2)["decision_state"] == "refused"
    assert len(_stage_events(store, INTERRUPTED)) == 1
    assert len(_stage_events(store, APPLIED)) == 0


def test_gate_conflicting_q_refuses_even_with_ordinary_settlement(tmp_path):
    """A conflicting recovery receipt vetoes the receipt even when genuine
    ordinary settlement evidence already exists."""
    store, row, outcome, r2 = _spent_ready(tmp_path)
    store._db._conn.execute(
        "INSERT INTO task_completion_recoveries "
        "(task_id, agent, origin_session_id, recovery_session_id, "
        " provider_session_id, claimed_at, expires_at, state) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (TASK_ID, MANAGER, "sess-origin-x", SESSION_ID, "provider-x",
         "2026-09-21T00:00:00+00:00", "2026-09-21T01:00:00+00:00",
         "callback_accepted"),
    )
    store._db._conn.commit()
    before = _dump(store)
    gate = _gate(store, _report_for_result(store, r2), r2)
    assert gate.kind == "skip", gate
    assert _receipt(store, r2)["decision_state"] == "ready"
    assert _dump(store) == before


def test_gate_two_connections_one_consumer_admission(tmp_path):
    store, row, outcome, r2 = _spent_ready(tmp_path)
    other = AuthorityPolicyStore(Database(store._db.db_path))
    barrier = threading.Barrier(2)
    results: dict[str, object] = {}
    store_real = store._db._conn
    other_real = other._db._conn
    store._db._conn = _RendezvousConn(store_real, barrier)
    other._db._conn = _RendezvousConn(other_real, barrier)
    report = _report_for_result(store, r2)

    def run(name, target):
        results[name] = _gate(target, report, r2)

    threads = [
        threading.Thread(target=run, args=("a", store)),
        threading.Thread(target=run, args=("b", other)),
    ]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
    finally:
        store._db._conn = store_real
        other._db._conn = other_real
    kinds = sorted(result.kind for result in results.values())
    assert kinds == ["admitted", "skip"], kinds
    assert len(_stage_events(store, CLAIMED)) == 1
    assert _receipt(store, r2)["decision_state"] in ("claimed", "refused")


def test_accepted_recovery_routes_v2_lineage_before_special_effects(tmp_path):
    import runtime.orchestrator.run_step as run_step

    store, row, outcome, r2 = _spent_ready(tmp_path)
    report = _report_for_result(store, r2)
    calls: list[dict] = []

    def fake_consume(orch, task_id, report, **kwargs):
        calls.append(kwargs)

    orch = types.SimpleNamespace(_db=store._db)
    with patch.object(run_step, "_consume_completion_report", fake_consume):
        run_step._consume_accepted_completion_recovery(
            orch, TASK_ID, report, agent=MANAGER, session_id=SESSION_ID,
            result_row_id=r2,
        )
    assert calls and calls[0].get("result_row_id") == r2

    # A classification read failure is fail-closed: still routed, never a
    # silent special-branch fallthrough.
    calls.clear()
    original = store._db.authority_policy_v2_completion_dispatch_context

    def boom(**kwargs):
        raise RuntimeError("classification read failure")

    store._db.authority_policy_v2_completion_dispatch_context = boom
    try:
        with patch.object(run_step, "_consume_completion_report", fake_consume):
            run_step._consume_accepted_completion_recovery(
                orch, TASK_ID, report, agent=MANAGER, session_id=SESSION_ID,
                result_row_id=r2,
            )
    finally:
        store._db.authority_policy_v2_completion_dispatch_context = original
    assert calls and calls[0].get("result_row_id") == r2


# ── terminal classification: exact proof, never mere absence/flags ────────


def _insert_later_result(store):
    """A bare row whose session is NOT the root's durable owner.

    This is deliberately an INVALID later identity: the corrected classifier
    must treat it as ``foreign`` (never ordinary) because its ``sess-later``
    session does not match the task's current durable owner/session.
    """
    cursor = store._db._conn.execute(
        "INSERT INTO task_results "
        "(task_id, agent, session_id, status, output_summary, decision_json, "
        "confidence_score, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (TASK_ID, MANAGER, "sess-later", "completed", "later",
         '{"action":"done"}', 70, "2026-09-21T00:00:05+00:00"),
    )
    store._db._conn.commit()
    return cursor.lastrowid


def test_gate_terminal_invalid_identity_is_foreign_not_ordinary(tmp_path):
    """On a root with v2 history a malformed/unknown identity never becomes
    the ordinary/v1 absence path (manager terminal-probe red case)."""
    store, row, outcome, r2 = _spent_ready(tmp_path)
    assert _claim(store, row).status == "claimed"
    assert _ack(store, row).status == "applied"
    report = _report_for_result(store, r2)
    for identity in (None, True, False, "bogus", 999999, 0, -1):
        before = _dump(store)
        context = store._db.authority_policy_v2_completion_dispatch_context(
            root_task_id=TASK_ID, result_row_id=identity,
        )
        assert context.kind == "foreign", (identity, context)
        assert _gate(store, report, identity).kind == "skip", identity
        assert _dump(store) == before


def test_gate_terminal_corrupt_proof_never_ordinary_with_later_row(tmp_path):
    """A corrupted terminal decision proof behind a retired pointer keeps the
    lineage live even when a genuine later row exists -- and the exact ack
    still refuses ``evidence_drift`` with the DB byte-for-byte unchanged."""
    store, row, outcome, r2 = _spent_ready(tmp_path)
    assert _claim(store, row).status == "claimed"
    assert _ack(store, row).status == "applied"
    later = _insert_later_result(store)
    _corrupt_claim_event(store)
    before = _dump(store)
    assert _gate(
        store, _report_for_result(store, later), later,
    ).kind == "skip"
    assert _dump(store) == before
    replay = _ack(store, row)
    assert replay.status == "ack_pending" and replay.reason == "evidence_drift"
    assert _dump(store) == before


def test_gate_corrupt_terminal_applied_event_never_ordinary(tmp_path):
    """The terminal proof itself is re-authenticated: a mutated retained
    ``decision_applied`` event keeps the lineage live for a later row too."""
    store, row, outcome, r2 = _spent_ready(tmp_path)
    assert _claim(store, row).status == "claimed"
    assert _ack(store, row).status == "applied"
    later = _insert_later_result(store)
    event = _stage_events(store, APPLIED)[0]
    event["report_digest"] = "0" * 64
    store._db._conn.execute(
        "UPDATE audit_log SET payload=? WHERE action='authority_policy_v2_result_stage' "
        "AND json_extract(payload,'$.stage')=?",
        (json.dumps(event), APPLIED),
    )
    store._db._conn.commit()
    before = _dump(store)
    assert _gate(store, _report_for_result(store, later), later).kind == "skip"
    assert _dump(store) == before


def test_none_resolver_maps_to_latest_row_but_direct_invalid_identity_is_foreign(
    tmp_path,
):
    """Record the None-resolver behavior explicitly: the common entry resolves a
    supplied ``None`` to the latest persisted row (a real identity), while a
    directly supplied invalid identity on a v2 root is ''foreign''."""
    from runtime.orchestrator.run_step import _resolve_completion_result_row_id

    store, row, outcome, r2 = _spent_ready(tmp_path)
    assert _claim(store, row).status == "claimed"
    assert _ack(store, row).status == "applied"
    report = _report_for_result(store, r2)
    assert _resolve_completion_result_row_id(store._db, TASK_ID, report, None) == r2
    resolved = store._db.authority_policy_v2_completion_dispatch_context(
        root_task_id=TASK_ID, result_row_id=r2,
    )
    assert resolved.kind == "receipt"
    assert store._db.authority_policy_v2_completion_dispatch_context(
        root_task_id=TASK_ID, result_row_id=None,
    ).kind == "foreign"


def test_none_resolver_never_authorizes_latest_row_for_a_different_report(tmp_path):
    """The None resolver maps to the latest persisted row, but a report built
    from a DIFFERENT row can never ride that adoption into an ordinary effect."""
    from runtime.orchestrator.run_step import _resolve_completion_result_row_id

    store, row, outcome, r2 = _spent_ready(tmp_path)
    assert _claim(store, row).status == "claimed"
    assert _ack(store, row).status == "applied"
    later = _establish_later_result(store)
    stale_report = _report_for_result(store, row["id"])
    resolved = _resolve_completion_result_row_id(
        store._db, TASK_ID, stale_report, None,
    )
    assert resolved == later
    before = _dump(store)
    # The classifier accepts the resolved row as a genuine later result, but the
    # gate binds the SUPPLIED report to it and refuses the stale body.
    assert store._db.authority_policy_v2_completion_dispatch_context(
        root_task_id=TASK_ID, result_row_id=resolved,
    ).kind == "later"
    assert _gate(store, stale_report, resolved).kind == "skip"
    assert _dump(store) == before


def test_accepted_recovery_routes_valid_later_result_through_common(tmp_path):
    """Both entries honor the corrected later classification: the accepted-
    recovery entry routes a genuine later result (and a drifted one) through the
    common guarded consumer instead of a special recovery branch."""
    import runtime.orchestrator.run_step as run_step

    store, row, outcome, r2 = _spent_ready(tmp_path)
    assert _claim(store, row).status == "claimed"
    assert _ack(store, row).status == "applied"
    later = _establish_later_result(store)
    calls: list[dict] = []

    def fake_consume(orch, task_id, report, **kwargs):
        calls.append(kwargs)

    with patch.object(run_step, "_consume_completion_report", fake_consume):
        run_step._consume_accepted_completion_recovery(
            types.SimpleNamespace(_db=store._db), TASK_ID,
            _report_for_result(store, later), agent=MANAGER,
            session_id="sess-later-live", result_row_id=later,
        )
    assert calls and calls[0].get("result_row_id") == later


# ── PUBLIC ack/refusal transaction matrix ─────────────────────────────────

_ENVELOPE_UPDATE = "UPDATE authority_policy_v2_continue_envelopes"
_TASKS_UPDATE = "UPDATE tasks SET status=?"
_ESCALATION_PAYLOAD = "authority_v2_decision_dispatch_interrupted"


def test_ack_sql_audit_and_commit_boundary_failures_roll_back_then_retry(tmp_path):
    for index, (kwargs, expected) in enumerate((
        ({"sql_fragment": _ENVELOPE_UPDATE, "occurrence": 1}, f"sql:{_ENVELOPE_UPDATE}"),
        ({"audit_stage": APPLIED}, f"audit:{APPLIED}"),
        ({"fail_commit": True}, "commit"),
    )):
        store, row, outcome, r2 = _spent_ready(_fresh_dir(tmp_path, f"ack-{index}"))
        assert _claim(store, row).status == "claimed"
        before = _dump(store)
        real = store._db._conn
        wrapper = _BoundaryFailingConn(real, **kwargs)
        store._db._conn = wrapper
        try:
            result = _ack(store, row)
        finally:
            store._db._conn = real
        assert wrapper.fired == expected, (kwargs, wrapper.fired)
        assert result.status == "ack_pending" and result.reason == "ack_failed", result
        assert _dump(store) == before
        assert _receipt(store, r2)["decision_state"] == "claimed"
        assert _stage_events(store, APPLIED) == []
        # Remove the fault: exactly one prescribed retry, no second spend.
        assert _ack(store, row).status == "applied"
        assert len(_stage_events(store, "spent")) == 1
        assert len(_stage_events(store, APPLIED)) == 1


def test_refusal_sql_audit_and_commit_boundary_failures_roll_back_then_retry(tmp_path):
    for index, (kwargs, expected) in enumerate((
        ({"sql_fragment": _TASKS_UPDATE, "occurrence": 1}, f"sql:{_TASKS_UPDATE}"),
        ({"audit_stage": _ESCALATION_PAYLOAD}, f"audit:{_ESCALATION_PAYLOAD}"),
        ({"sql_fragment": _ENVELOPE_UPDATE, "occurrence": 1}, f"sql:{_ENVELOPE_UPDATE}"),
        ({"audit_stage": INTERRUPTED}, f"audit:{INTERRUPTED}"),
        ({"fail_commit": True}, "commit"),
    )):
        store, row, outcome, r2 = _spent_ready(_fresh_dir(tmp_path, f"ref-{index}"))
        assert _claim(store, row).status == "claimed"
        before = _dump(store)
        real = store._db._conn
        wrapper = _BoundaryFailingConn(real, **kwargs)
        store._db._conn = wrapper
        try:
            result = _refuse(store, row)
        finally:
            store._db._conn = real
        assert wrapper.fired == expected, (kwargs, wrapper.fired)
        assert result.status == "refusal_pending" and result.reason == "refusal_failed"
        assert _dump(store) == before
        assert _receipt(store, r2)["decision_state"] == "claimed"
        assert _stage_events(store, INTERRUPTED) == []
        # Remove the fault: exactly one prescribed refusal, no second spend.
        assert _refuse(store, row).status == "refused"
        assert len(_stage_events(store, "spent")) == 1
        assert len(_stage_events(store, INTERRUPTED)) == 1


def test_ack_and_refusal_refuse_caller_transaction_preserving_pending_mutation(
    tmp_path,
):
    for index, writer in enumerate(("ack", "refuse")):
        store, row, outcome, r2 = _spent_ready(_fresh_dir(tmp_path, f"txn-{index}"))
        assert _claim(store, row).status == "claimed"
        real = store._db._conn
        real.execute("BEGIN IMMEDIATE")
        real.execute(
            "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) "
            "VALUES (?,?,?,?,?)",
            (TASK_ID, MANAGER, "pending_probe", "{}", "2026-09-21T00:00:00+00:00"),
        )
        assert real.in_transaction
        before = _dump(store)
        result = _ack(store, row) if writer == "ack" else _refuse(store, row)
        assert result.reason == "transaction_owned", (writer, result)
        assert real.in_transaction
        assert _dump(store) == before
        real.rollback()
        assert store._db._conn.in_transaction is False
        if writer == "ack":
            assert _ack(store, row).status == "applied"
        else:
            assert _refuse(store, row).status == "refused"


def test_ack_failure_preserves_committed_effect_and_claimed_receipt(tmp_path):
    store, row, outcome, r2 = _spent_ready(tmp_path)
    assert _claim(store, row).status == "claimed"
    # A real, independently committed normal effect (task projection + a real
    # child-ish result row) that acknowledgement must never roll back.
    store._db._conn.execute(
        "UPDATE tasks SET note=? WHERE id=?", ("committed-effect", TASK_ID),
    )
    _insert_later_result(store)
    preserved_task = _row(store, "tasks", "id=?", (TASK_ID,))
    before = _dump(store)
    real = store._db._conn
    wrapper = _BoundaryFailingConn(real, fail_commit=True)
    store._db._conn = wrapper
    try:
        result = _ack(store, row)
    finally:
        store._db._conn = real
    assert wrapper.fired == "commit"
    assert result.status == "ack_pending" and result.reason == "ack_failed"
    assert _dump(store) == before
    assert _receipt(store, r2)["decision_state"] == "claimed"
    assert _row(store, "tasks", "id=?", (TASK_ID,)) == preserved_task
    # The fault removed: exactly one ack, no second spend/effect.
    assert _ack(store, row).status == "applied"
    assert len(_stage_events(store, "spent")) == 1
    assert len(_stage_events(store, APPLIED)) == 1


def test_refusal_failure_retains_discoverable_claimed_and_committed_effect(tmp_path):
    store, row, outcome, r2 = _spent_ready(tmp_path)
    assert _claim(store, row).status == "claimed"
    # A real, independently committed child effect the refusal must never touch.
    store._db._conn.execute(
        "INSERT INTO tasks (id, status, assigned_agent, team, brief, task_type, "
        "parent_task_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("TASK-CHILD-OWN", TaskStatus.PENDING.value, "dev_agent", TEAM, "child",
         "subtask", TASK_ID, "2026-09-21T00:00:00+00:00",
         "2026-09-21T00:00:00+00:00"),
    )
    store._db._conn.commit()
    before = _dump(store)
    real = store._db._conn
    wrapper = _BoundaryFailingConn(real, fail_commit=True)
    store._db._conn = wrapper
    try:
        result = _refuse(store, row)
    finally:
        store._db._conn = real
    assert wrapper.fired == "commit"
    assert result.status == "refusal_pending" and result.reason == "refusal_failed"
    assert _dump(store) == before
    assert _receipt(store, r2)["decision_state"] == "claimed"
    assert _row(store, "tasks", "id=?", ("TASK-CHILD-OWN",))["status"] == (
        TaskStatus.PENDING.value
    )
    # Exact retry settles the refusal; the committed child effect is preserved
    # and there is no second spend.
    assert _refuse(store, row).status == "refused"
    assert _row(store, "tasks", "id=?", ("TASK-CHILD-OWN",))["status"] == (
        TaskStatus.PENDING.value
    )
    assert len(_stage_events(store, "spent")) == 1


def test_refusal_preserves_terminal_replaced_and_malformed_owner_without_mutation(
    tmp_path,
):
    mutations = {
        "cancelled": "UPDATE tasks SET cancelled_at=?, status=? WHERE id=?",
        "terminal": "UPDATE tasks SET status=? WHERE id=?",
        "replaced": "UPDATE tasks SET assigned_agent=? WHERE id=?",
        "malformed": "UPDATE tasks SET assigned_agent=NULL WHERE id=?",
    }
    for index, (name, sql) in enumerate(mutations.items()):
        store, row, outcome, r2 = _spent_ready(_fresh_dir(tmp_path, f"own-{index}"))
        assert _claim(store, row).status == "claimed"
        if name == "cancelled":
            store._db._conn.execute(
                sql, ("2026-09-21T00:00:00+00:00", TaskStatus.CANCELLED.value, TASK_ID),
            )
        elif name == "terminal":
            store._db._conn.execute(sql, (TaskStatus.COMPLETED.value, TASK_ID))
        elif name == "replaced":
            store._db._conn.execute(sql, ("other_agent", TASK_ID))
        else:
            store._db._conn.execute(sql, (TASK_ID,))
        store._db._conn.commit()
        preserved = _row(store, "tasks", "id=?", (TASK_ID,))
        result = _refuse(store, row)
        assert result.status == "refused", (name, result)
        # The differing/affirmatively cancelled task row is preserved EXACTLY:
        # no task UPDATE, no restoration, no replacement mutation.
        assert _row(store, "tasks", "id=?", (TASK_ID,)) == preserved, name
        assert _receipt(store, r2)["decision_state"] == "refused"
        # No escalation audit was minted for the preserved (non-current) owner.
        assert [
            row for row in store._db.get_audit_logs(TASK_ID)
            if row["action"] == "escalation"
        ] == []


# ── ACTUAL reopen: an independent Database over the SAME persisted file ───


def _reopened_store(store):
    """A genuinely NEW Database connection over the SAME persisted file."""
    path = store._db.db_path
    old_conn = store._db._conn
    reopened = AuthorityPolicyStore(Database(path))
    assert reopened._db.db_path == path
    assert reopened._db._conn is not old_conn
    return reopened


def test_reopen_after_spend_before_claim_ready_claims_once(tmp_path):
    store, row, outcome, r2 = _spent_ready(tmp_path)
    assert _receipt(store, r2)["decision_state"] == "ready"
    reopened = _reopened_store(store)
    assert _receipt(reopened, r2)["decision_state"] == "ready"
    gate = _gate(reopened, _report_for_result(reopened, r2), r2)
    assert gate.kind == "admitted", gate
    assert _receipt(reopened, r2)["decision_state"] == "claimed"
    assert len(_stage_events(reopened, CLAIMED)) == 1
    # A second real reopen of the committed claim never admits a second
    # consumer: the restart path performs the audited interruption refusal.
    reopened_again = _reopened_store(reopened)
    assert reopened_again._db._conn is not reopened._db._conn
    assert _gate(
        reopened_again, _report_for_result(reopened_again, r2), r2,
    ).kind == "skip"
    assert _receipt(reopened_again, r2)["decision_state"] == "refused"
    assert len(_stage_events(reopened_again, INTERRUPTED)) == 1


def test_reopen_after_committed_claim_before_effect_refuses_zero_entry(tmp_path):
    store, row, outcome, r2 = _spent_ready(tmp_path)
    assert _claim(store, row).status == "claimed"
    reopened = _reopened_store(store)
    # ZERO consumer entry: no ordinary body call, only the audited refusal.
    assert _gate(reopened, _report_for_result(reopened, r2), r2).kind == "skip"
    assert _receipt(reopened, r2)["decision_state"] == "refused"
    assert len(_stage_events(reopened, INTERRUPTED)) == 1
    assert len(_stage_events(reopened, APPLIED)) == 0
    assert _row(reopened, "tasks", "id=?", (TASK_ID,))["status"] == (
        TaskStatus.ESCALATED.value
    )


def test_reopen_after_committed_effect_and_failed_ack_preserves(tmp_path):
    store, row, outcome, r2 = _spent_ready(tmp_path)
    assert _claim(store, row).status == "claimed"
    # A committed real normal child effect + a real committed task projection.
    store._db._conn.execute(
        "INSERT INTO tasks (id, status, assigned_agent, team, brief, task_type, "
        "parent_task_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("TASK-CHILD-RO", TaskStatus.PENDING.value, "dev_agent", TEAM, "child",
         "subtask", TASK_ID, "2026-09-21T00:00:00+00:00",
         "2026-09-21T00:00:00+00:00"),
    )
    store._db._conn.commit()
    real = store._db._conn
    wrapper = _BoundaryFailingConn(real, fail_commit=True)
    store._db._conn = wrapper
    try:
        ack = _ack(store, row)
    finally:
        store._db._conn = real
    assert wrapper.fired == "commit"
    assert ack.status == "ack_pending" and ack.reason == "ack_failed"

    reopened = _reopened_store(store)
    child_before = _row(reopened, "tasks", "id=?", ("TASK-CHILD-RO",))
    assert _gate(reopened, _report_for_result(reopened, r2), r2).kind == "skip"
    assert _receipt(reopened, r2)["decision_state"] == "refused"
    # The committed child effect is preserved and no duplicate child/enqueue
    # appears; the exact refusal replay is read-only.
    assert _row(reopened, "tasks", "id=?", ("TASK-CHILD-RO",)) == child_before
    assert len(_stage_events(reopened, INTERRUPTED)) == 1
    before = _dump(reopened)
    replay = _refuse(reopened, row)
    assert replay.status == "already_refused", replay
    assert _dump(reopened) == before
    assert _row(reopened, "tasks", "id=?", ("TASK-CHILD-RO",)) == child_before


def _fresh_dir(tmp_path, name):
    path = tmp_path / name
    path.mkdir()
    return path
