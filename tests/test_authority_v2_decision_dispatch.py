"""THR-229 checkpoint C3d3c2 — ordinary decision-dispatch claim/ack/refusal.

Drives ONLY the public ``AuthorityPolicyStore`` forwarders over the DB-owned
synchronized transactions.  Every refusal asserts the exact full SQLite
``iterdump`` (not a row count) is unchanged, so corrupt/conflicting evidence can
never be silently repaired.  These are storage-boundary proofs; the common
consumer integration lives in ``runtime/orchestrator/run_step.py``.
"""

from __future__ import annotations

import threading
import types

import pytest

from runtime.infrastructure.database import Database
from runtime.models import TaskStatus
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
from tests.test_authority_v2_attempt_admission import MANAGER, SESSION_ID, TASK_ID
from tests.test_authority_v2_envelope_spend import (
    RESERVED,
    _BoundaryFailingConn,
    _RendezvousConn,
    _reserved_state,
    _row,
    _spend,
)
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


def _report(**overrides):
    fields = dict(
        output_summary="reserved continuation", confidence_score=90,
        status="completed", output_dir=None, verdict=None, risks_flagged=None,
        decision={"action": "done"}, waiting_on_job_ids=None, local_ci=None,
    )
    fields.update(overrides)
    return types.SimpleNamespace(**fields)


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
    # An unknown spending result is provably the ordinary/v1 no-v2 path.
    assert _receipt(store, 999999) is None
    # A report that does not match the persisted R2 body never binds.
    assert store.v2_decision_result_report_binds(
        root_task_id=TASK_ID, spending_result_id=r2, report=_report(),
    ) is True
    assert store.v2_decision_result_report_binds(
        root_task_id=TASK_ID, spending_result_id=r2,
        report=_report(decision={"action": "delegate"}),
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


# ── common-consumer gate classification ───────────────────────────────────


def _gate(store, report, result_row_id):
    from runtime.orchestrator.run_step import (
        _V2_DECISION_DISPATCH_ADMITTED,
        _V2_DECISION_DISPATCH_ORDINARY,
        _V2_DECISION_DISPATCH_SKIP,
        _v2_decision_dispatch_gate,
    )
    orch = types.SimpleNamespace(_db=store._db)
    gate = _v2_decision_dispatch_gate(orch, TASK_ID, report, result_row_id, MANAGER)
    return gate, (
        _V2_DECISION_DISPATCH_ADMITTED,
        _V2_DECISION_DISPATCH_ORDINARY,
        _V2_DECISION_DISPATCH_SKIP,
    )


def test_gate_ordinary_when_no_v2_receipt(tmp_path):
    store, row, attempt, outcome, r2 = _reserved_state(tmp_path)
    gate, kinds = _gate(store, _report(), 424242)
    assert gate == kinds[1]  # ordinary: provably no-v2/v1 path


def test_gate_admits_ready_receipt_once_then_skips_restart(tmp_path):
    store, row, outcome, r2 = _spent_ready(tmp_path)
    gate, kinds = _gate(store, _report(), r2)
    assert gate == kinds[0], gate  # admitted: one consumer entry
    assert _receipt(store, r2)["decision_state"] == "claimed"
    assert len(_stage_events(store, CLAIMED)) == 1

    # A restart (or duplicate) sees ``claimed`` and never re-admits; the gate
    # performs the audited interruption refusal instead.
    gate2, _ = _gate(store, _report(), r2)
    assert gate2 == kinds[2], gate2
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
    gate3, _ = _gate(applied_store, _report(), applied_r2)
    assert gate3 == kinds[2]


def test_gate_skips_conflicting_report_and_corrupt_receipt(tmp_path):
    store, row, outcome, r2 = _spent_ready(tmp_path)
    # A report whose decision action drifts from the persisted R2 body is never
    # admitted (and the receipt stays ready).
    gate, kinds = _gate(store, _report(decision={"action": "delegate"}), r2)
    assert gate == kinds[2]
    assert _receipt(store, r2)["decision_state"] == "ready"
    assert _stage_events(store, CLAIMED) == []

    # A corrupt canonical body is never ordinary permission.
    store._db._conn.execute(
        "UPDATE authority_policy_v2_continue_envelopes SET canonical_payload_json='{'"
    )
    store._db._conn.commit()
    gate2, _ = _gate(store, _report(), r2)
    assert gate2 == kinds[2]


def test_gate_missing_identity_with_retired_generation_is_never_ordinary(tmp_path):
    store, row, outcome, r2 = _spent_ready(tmp_path)
    gate, kinds = _gate(store, _report(), None)
    assert gate == kinds[2]  # retired v2 generation + missing identity -> skip


def _fresh_dir(tmp_path, name):
    path = tmp_path / name
    path.mkdir()
    return path
