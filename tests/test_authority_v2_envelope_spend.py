"""THR-229 checkpoint C3d3c1 — the ONE atomic next-result spend-to-ready writer.

Focused, isolated evidence for the accepted R4 next-result envelope spend
(case-design R4 line 1295 and the causal/spending replay at 1350-1358), driving
the REAL public ``Database``/``AuthorityPolicyStore`` operation (no copies of
its logic):

  * healthy ``active -> consumed`` with the result-keyed READY receipt,
    ``admitted -> retired`` and exactly one closed ``spent`` (``ax``) audit;
  * an exact same-R2 retry reads the authenticated receipt back with NO write;
  * causal-R replay, foreign/wrong-session/malformed/nonexistent R2, missing/
    wrong generation and a distinct later receipt attempt all refuse with the
    complete prior dump preserved;
  * a legitimate LATER active selector does not invalidate the pinned
    generation;
  * ordinary AND exact recovery settlement proofs are both accepted;
  * caller-transaction refusal preserves the exact pending mutation BEFORE the
    caller rollback;
  * every distinct E UPDATE / D UPDATE / audit INSERT / COMMIT failure rolls the
    whole transaction back, then an exact retry succeeds;
  * two real connections with a deterministic overlapping entry prove ONE spend
    and ONE ready receipt/audit; a reopen proves a read-only exact retry;
  * post-spend late acknowledgement/invalidation and a stale-A-after-B pointer
    are inspected for an allowed contract outcome with no pointer/task
    regression.

The broad integration suite is SKIPPED under founder THR-243 seq42, never PASS.
"""
from __future__ import annotations

import json
import threading

import pytest

from runtime.infrastructure.database import Database
from runtime.models import TaskStatus
from runtime.orchestrator.active_authority_policy import (
    persist_session_policy_binding,
    resolve_active_team_policy_snapshot,
)
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
from tests.test_authority_v2_attempt_admission import MANAGER, SESSION_ID, TASK_ID, TEAM
from tests.test_authority_v2_generation_admission import RESERVED, _published
from tests.test_authority_v2_publication_bookkeeping import (
    _append_stage_event,
    _delete_ordinary_completion,
    _dump,
    _point_dispatch_at_replacement,
    _stage_events,
    _TargetedFailingConn,
)

SPENT = "spent"


# ── helpers ───────────────────────────────────────────────────────────────


def _bind_reserved(store: AuthorityPolicyStore, *, session: str = RESERVED) -> None:
    snapshot = resolve_active_team_policy_snapshot(
        store=store, team=TEAM, agent_name=MANAGER, eligible=True,
    )
    assert snapshot is not None and snapshot.family == "v2"
    persist_session_policy_binding(
        db=store._db, task_id=TASK_ID, session_id=session, agent_name=MANAGER,
        snapshot=snapshot, provider_id="codex", executor_kind="codex",
        model_id="default",
    )


def _insert_spending_result(
    store: AuthorityPolicyStore, *, session: str = RESERVED,
    decision: dict | None = None, raw: str | None = None,
    agent: str = MANAGER, task_id: str = TASK_ID,
) -> int:
    payload = (
        json.dumps(decision if decision is not None else {"action": "done"})
        if raw is None else raw
    )
    cursor = store._db._conn.execute(
        """INSERT INTO task_results
           (task_id, agent, session_id, status, output_summary, decision_json,
            confidence_score, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (task_id, agent, session, "completed", "reserved continuation", payload,
         90, "2026-09-21T00:00:00+00:00"),
    )
    store._db._conn.commit()
    return cursor.lastrowid


def _reserved_state(tmp_path):
    """A settled, pointer-current generation with a real reserved R2 result."""
    store, row, attempt, outcome, claimed = _published(tmp_path)
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
    r2 = _insert_spending_result(store)
    return store, row, attempt, outcome, r2


def _spend(store, row, outcome, r2, **kw):
    kwargs = dict(
        root_task_id=TASK_ID, manager_agent=MANAGER,
        manager_session_id=SESSION_ID, result_id=row["id"],
        generation_id=outcome.notification_id, next_session_id=RESERVED,
        spending_result_id=r2,
    )
    kwargs.update(kw)
    return store.spend_v2_continue_envelope(**kwargs)


def _envelope(store, outcome):
    notification = store.get_v2_recovery_notification(outcome.notification_id)
    assert notification is not None
    return store.get_v2_continue_envelope(notification.envelope_id)


def _dispatch(store):
    return store.get_v2_root_dispatch(TASK_ID)


def _task(store):
    return store._db._conn.execute(
        "SELECT * FROM tasks WHERE id=?", (TASK_ID,)
    ).fetchone()


def _row(store, table, where, params):
    return dict(store._db._conn.execute(f"SELECT * FROM {table} WHERE {where}", params).fetchone())


def _assert_spent_receipt(store, outcome, r2):
    envelope = _envelope(store, outcome)
    assert envelope.lifecycle_state == "consumed"
    assert envelope.spending_result_id == r2
    assert envelope.decision_state == "ready"
    assert _dispatch(store).state == "retired"
    notification = store.get_v2_recovery_notification(outcome.notification_id)
    assert notification.state == "settled"
    events = _stage_events(store, SPENT)
    assert len(events) == 1, events
    event = events[0]
    assert event["spending_result_id"] == r2
    assert event["next_session_id"] == RESERVED
    assert event["stage"] == SPENT


# ── healthy spend and exact replay ────────────────────────────────────────


def test_spend_healthy_active_to_consumed_ready_receipt(tmp_path):
    store, row, attempt, outcome, r2 = _reserved_state(tmp_path)
    preserved = {
        "task": _row(store, "tasks", "id=?", (TASK_ID,)),
        "r2": _row(store, "task_results", "id=?", (r2,)),
        "causal_result": _row(store, "task_results", "id=?", (row["id"],)),
        "notification": _row(
            store, "authority_policy_v2_recovery_notifications",
            "notification_id=?", (outcome.notification_id,),
        ),
        "attempt": _row(
            store, "authority_policy_v2_attempts", "result_id=?", (row["id"],),
        ),
    }
    candidate = store.get_v2_candidate_for_result(row["id"])
    pin = store.get_v2_pin(candidate.candidate_id)
    evaluation = store.get_v2_evaluation(candidate.candidate_id)

    result = _spend(store, row, outcome, r2)
    assert result.status == "spent", result
    assert result.decision_state == "ready", result
    assert result.spending_result_id == r2 and result.result_id == row["id"]
    _assert_spent_receipt(store, outcome, r2)

    # The spend performs NO consumer/task/child/queue effect: the task remains
    # the exact in_progress reserved owner and R2 is retained byte-for-byte.
    assert _row(store, "tasks", "id=?", (TASK_ID,)) == preserved["task"]
    task = _task(store)
    assert task["status"] == TaskStatus.IN_PROGRESS.value
    assert task["current_session_id"] == RESERVED
    assert _row(store, "task_results", "id=?", (r2,)) == preserved["r2"]
    assert _row(store, "task_results", "id=?", (row["id"],)) == preserved["causal_result"]
    assert _row(
        store, "authority_policy_v2_recovery_notifications",
        "notification_id=?", (outcome.notification_id,),
    ) == preserved["notification"]
    assert _row(
        store, "authority_policy_v2_attempts", "result_id=?", (row["id"],),
    ) == preserved["attempt"]
    # K/P/V are byte-for-byte retained.
    assert store.get_v2_candidate_for_result(row["id"]).model_dump() == candidate.model_dump()
    assert store.get_v2_pin(candidate.candidate_id).model_dump() == pin.model_dump()
    assert store.get_v2_evaluation(
        candidate.candidate_id
    ).model_dump() == evaluation.model_dump()


def test_spend_exact_same_r2_retry_is_read_only(tmp_path):
    store, row, attempt, outcome, r2 = _reserved_state(tmp_path)
    assert _spend(store, row, outcome, r2).status == "spent"
    before = _dump(store)
    retry = _spend(store, row, outcome, r2)
    assert retry.status == "already_spent_exact", retry
    assert retry.decision_state == "ready"
    assert _dump(store) == before
    assert len(_stage_events(store, SPENT)) == 1


def test_spend_reopen_exact_retry_is_read_only(tmp_path):
    store, row, attempt, outcome, r2 = _reserved_state(tmp_path)
    assert _spend(store, row, outcome, r2).status == "spent"
    before = _dump(store)
    reopened = AuthorityPolicyStore(Database(store._db.db_path))
    retry = _spend(reopened, row, outcome, r2)
    assert retry.status == "already_spent_exact", retry
    assert _dump(reopened) == before


# ── refusals: the complete prior dump is preserved ────────────────────────


def _assert_refused(store, outcome, r2, *, reason, row, preexisting_spent=False, **kw):
    before = _dump(store)
    result = _spend(store, row, outcome, r2, **kw)
    assert result.status == "spend_pending", result
    if reason is not None:
        assert result.reason == reason, result
    assert _dump(store) == before
    if not preexisting_spent:
        assert _stage_events(store, SPENT) == []
    assert _envelope(store, outcome).lifecycle_state == "active"
    assert _dispatch(store).state == "admitted"
    return result


def test_spend_causal_replay_refuses(tmp_path):
    store, row, attempt, outcome, r2 = _reserved_state(tmp_path)
    _assert_refused(store, outcome, row["id"], reason="identity_mismatch", row=row)


def test_spend_nonexistent_result_refuses(tmp_path):
    store, row, attempt, outcome, r2 = _reserved_state(tmp_path)
    _assert_refused(store, outcome, 999999, reason="missing_result", row=row)


def test_spend_wrong_session_result_refuses(tmp_path):
    store, row, attempt, outcome, r2 = _reserved_state(tmp_path)
    foreign = _insert_spending_result(store, session="sess-foreign-session")
    _assert_refused(store, outcome, foreign, reason="missing_result", row=row)


def test_spend_foreign_root_result_refuses(tmp_path):
    store, row, attempt, outcome, r2 = _reserved_state(tmp_path)
    foreign = _insert_spending_result(store, task_id="TASK-foreign")
    _assert_refused(store, outcome, foreign, reason="missing_result", row=row)


def test_spend_malformed_payload_result_refuses(tmp_path):
    store, row, attempt, outcome, r2 = _reserved_state(tmp_path)
    malformed = _insert_spending_result(store, raw="{not-json")
    _assert_refused(store, outcome, malformed, reason="missing_result", row=row)
    non_object = _insert_spending_result(store, raw=json.dumps(["list"]))
    _assert_refused(store, outcome, non_object, reason="missing_result", row=row)


def test_spend_missing_or_mistyped_generation_refuses(tmp_path):
    store, row, attempt, outcome, r2 = _reserved_state(tmp_path)
    _assert_refused(
        store, outcome, r2, reason="identity_mismatch", row=row, generation_id=None,
    )
    _assert_refused(
        store, outcome, r2, reason="identity_mismatch", row=row,
        generation_id="APV2N-" + "0" * 64,
    )
    _assert_refused(
        store, outcome, r2, reason="identity_mismatch", row=row,
        next_session_id=None,
    )
    _assert_refused(
        store, outcome, r2, reason="identity_mismatch", row=row,
        spending_result_id=None,
    )


def test_spend_wrong_tagged_generation_refuses(tmp_path):
    store, row, attempt, outcome, r2 = _reserved_state(tmp_path)
    # A real but foreign generation token is never an ordinary-path permission.
    _assert_refused(
        store, outcome, r2, reason="identity_mismatch", row=row,
        generation_id="APV2N-" + "a" * 64,
    )


def test_spend_cancelled_owner_refuses(tmp_path):
    store, row, attempt, outcome, r2 = _reserved_state(tmp_path)
    store._db._conn.execute(
        "UPDATE tasks SET cancelled_at=? WHERE id=?",
        ("2026-09-21T00:00:00+00:00", TASK_ID),
    )
    store._db._conn.commit()
    _assert_refused(store, outcome, r2, reason="owner_lost", row=row)


def test_spend_replaced_owner_session_refuses(tmp_path):
    store, row, attempt, outcome, r2 = _reserved_state(tmp_path)
    store._db._conn.execute(
        "UPDATE tasks SET current_session_id=? WHERE id=?",
        ("sess-replaced", TASK_ID),
    )
    store._db._conn.commit()
    _assert_refused(store, outcome, r2, reason="owner_lost", row=row)


def test_spend_refuses_without_ordinary_settlement_proof(tmp_path):
    store, row, attempt, outcome, r2 = _reserved_state(tmp_path)
    _delete_ordinary_completion(store, row["id"])
    _assert_refused(store, outcome, r2, reason="evidence_drift", row=row)


def test_spend_refuses_conflicting_related_receipt(tmp_path):
    store, row, attempt, outcome, r2 = _reserved_state(tmp_path)
    # A nonterminal related recovery receipt blocks the ordinary proof.
    store._db._conn.execute(
        """INSERT INTO task_completion_recoveries
           (task_id, agent, origin_session_id, recovery_session_id,
            provider_session_id, claimed_at, expires_at, state,
            accepted_result_id, accepted_result_session_id)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (TASK_ID, MANAGER, "sess-origin", SESSION_ID, "prov-1",
         "2026-01-01T00:00:00+00:00", "2999-01-01T00:00:00+00:00",
         "callback_accepted", row["id"], SESSION_ID),
    )
    store._db._conn.commit()
    _assert_refused(store, outcome, r2, reason="evidence_drift", row=row)


def test_spend_refuses_preexisting_spent_event(tmp_path):
    store, row, attempt, outcome, r2 = _reserved_state(tmp_path)
    envelope = _envelope(store, outcome)
    candidate = store.get_v2_candidate_for_result(row["id"])
    event = {
        "stage": SPENT,
        "attempt_id": attempt.attempt_id,
        "candidate_id": candidate.candidate_id,
        "result_id": row["id"],
        "envelope_id": envelope.envelope_id,
        "notification_id": outcome.notification_id,
        "generation_id": outcome.notification_id,
        "next_session_id": RESERVED,
        "spending_result_id": r2,
    }
    # A duplicate related event is a conflict, never absence.
    _append_stage_event(store, event)
    _assert_refused(store, outcome, r2, reason="receipt_conflict", row=row, preexisting_spent=True)
    # An opaque body is never absence either.
    store._db._conn.execute(
        "DELETE FROM audit_log WHERE action='authority_policy_v2_result_stage' "
        "AND json_extract(payload,'$.stage')=?",
        (SPENT,),
    )
    store._db._conn.commit()
    _append_stage_event(store, "not-a-json-object")
    before = _dump(store)
    result = _spend(store, row, outcome, r2)
    # An opaque body can never be absence: fail closed with no change.
    assert result.status == "spend_pending", result
    assert result.reason in ("receipt_conflict", "identity_mismatch"), result
    assert _dump(store) == before
    assert _envelope(store, outcome).lifecycle_state == "active"


def test_spend_refuses_malformed_spent_discriminator(tmp_path):
    store, row, attempt, outcome, r2 = _reserved_state(tmp_path)
    envelope = _envelope(store, outcome)
    candidate = store.get_v2_candidate_for_result(row["id"])
    event = {
        "stage": SPENT,
        "attempt_id": attempt.attempt_id,
        "candidate_id": candidate.candidate_id,
        "result_id": row["id"],
        "envelope_id": envelope.envelope_id,
        "notification_id": outcome.notification_id,
        "generation_id": outcome.notification_id,
        "next_session_id": RESERVED,
        "spending_result_id": None,
    }
    # A present-but-null discriminator is a related conflict, not unrelated.
    _append_stage_event(store, event)
    _assert_refused(store, outcome, r2, reason="receipt_conflict", row=row, preexisting_spent=True)


def test_spend_refuses_extra_key_spent_event(tmp_path):
    store, row, attempt, outcome, r2 = _reserved_state(tmp_path)
    envelope = _envelope(store, outcome)
    candidate = store.get_v2_candidate_for_result(row["id"])
    event = {
        "stage": SPENT,
        "attempt_id": attempt.attempt_id,
        "candidate_id": candidate.candidate_id,
        "result_id": row["id"],
        "envelope_id": envelope.envelope_id,
        "notification_id": outcome.notification_id,
        "generation_id": outcome.notification_id,
        "next_session_id": RESERVED,
        "spending_result_id": r2,
        "extra": "value",
    }
    _append_stage_event(store, event)
    _assert_refused(store, outcome, r2, reason="receipt_conflict", row=row, preexisting_spent=True)


# ── selector independence and settlement variants ─────────────────────────


def test_spend_survives_later_legitimate_selector_activation(tmp_path):
    store, row, attempt, outcome, r2 = _reserved_state(tmp_path)
    selector = store.get_authority_selector(TEAM)
    store.create_and_activate_v2({
        "team": TEAM, "policy_id": "engineering-dual-text",
        "title": "Dual second", "create_request_id": "c3d3c1-create",
        "activation_request_id": "c3d3c1-activate",
        "based_on_selector_id": selector.selector_id,
        "expected_selector_id": selector.selector_id,
        "action": "activate", "what_to_escalate": "Second escalate text.",
        "what_not_to_escalate": "Second continue text.",
    })
    result = _spend(store, row, outcome, r2)
    assert result.status == "spent", result
    _assert_spent_receipt(store, outcome, r2)


def test_spend_accepts_exact_recovery_settlement_proof(tmp_path):
    from tests.test_authority_v2_publication_bookkeeping import _finalized_recovery

    store, row, attempt, outcome = _finalized_recovery(tmp_path)
    claimed = store.claim_v2_notification_publication(
        root_task_id=TASK_ID, manager_agent=MANAGER,
        manager_session_id=SESSION_ID, result_id=row["id"],
    )
    assert claimed.status == "claimed", claimed
    ack = store.acknowledge_v2_notification_publication(
        root_task_id=TASK_ID, manager_agent=MANAGER,
        manager_session_id=SESSION_ID, result_id=row["id"],
        publication_attempt=claimed.publication_attempt,
        publisher_boot_id=claimed.publisher_boot_id,
    )
    assert ack.status == "published", ack
    assert store.try_claim_v2_continuation_generation(
        root_task_id=TASK_ID, manager_agent=MANAGER, manager_session_id=SESSION_ID,
        result_id=row["id"], generation_id=outcome.notification_id,
        next_session_id=RESERVED,
    ).status == "claimed"
    assert store.settle_v2_continuation_generation_admission(
        root_task_id=TASK_ID, manager_agent=MANAGER, manager_session_id=SESSION_ID,
        result_id=row["id"], generation_id=outcome.notification_id,
        next_session_id=RESERVED,
    ).status == "settled"
    _bind_reserved(store)
    r2 = _insert_spending_result(store)
    result = _spend(store, row, outcome, r2)
    assert result.status == "spent", result
    _assert_spent_receipt(store, outcome, r2)


# ── caller transaction, boundaries, contention ────────────────────────────


def test_spend_refuses_caller_transaction_preserving_pending_mutation(tmp_path):
    store, row, attempt, outcome, r2 = _reserved_state(tmp_path)
    conn = store._db._conn
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("UPDATE tasks SET note='pending-caller-mutation' WHERE id=?", (TASK_ID,))
    pending = _row(store, "tasks", "id=?", (TASK_ID,))
    assert pending["note"] == "pending-caller-mutation"
    assert conn.in_transaction is True
    result = _spend(store, row, outcome, r2)
    assert result.status == "spend_pending" and result.reason == "transaction_owned"
    assert conn.in_transaction is True
    assert _row(store, "tasks", "id=?", (TASK_ID,)) == pending
    conn.rollback()
    assert _envelope(store, outcome).lifecycle_state == "active"
    assert _stage_events(store, SPENT) == []


@pytest.mark.parametrize(
    "fragment,occurrence",
    [
        ("UPDATE authority_policy_v2_continue_envelopes", 1),
        ("UPDATE authority_policy_v2_root_dispatch", 1),
    ],
)
def test_spend_sql_boundary_failure_rolls_back(tmp_path, fragment, occurrence):
    store, row, attempt, outcome, r2 = _reserved_state(tmp_path)
    before = _dump(store)
    real = store._db._conn
    store._db._conn = _TargetedFailingConn(
        real, sql_fragment=fragment, occurrence=occurrence,
    )
    try:
        result = _spend(store, row, outcome, r2)
    finally:
        store._db._conn = real
    assert result.status == "spend_pending" and result.reason == "spend_failed"
    assert _dump(store) == before
    assert _stage_events(store, SPENT) == []
    # Exact successful retry after the injected boundary failure.
    assert _spend(store, row, outcome, r2).status == "spent"


def test_spend_audit_and_commit_failure_roll_back_then_retry(tmp_path):
    for index, wrapper in enumerate((
        {"audit_stage": SPENT},
        {"fail_commit": True},
    )):
        case = tmp_path / f"case-{index}"
        case.mkdir()
        store, row, attempt, outcome, r2 = _reserved_state(case)
        before = _dump(store)
        real = store._db._conn
        store._db._conn = _TargetedFailingConn(real, **wrapper)
        try:
            result = _spend(store, row, outcome, r2)
        finally:
            store._db._conn = real
        assert result.status == "spend_pending", result
        assert result.reason == "spend_failed", result
        assert _dump(store) == before
        assert _stage_events(store, SPENT) == []
        assert _spend(store, row, outcome, r2).status == "spent"


class _RendezvousConn:
    """Test-only connection wrapper: rendezvous both real connections at BEGIN."""

    def __init__(self, real, barrier: threading.Barrier) -> None:
        self._real = real
        self._barrier = barrier

    def execute(self, sql, *args, **kwargs):
        if (
            isinstance(sql, str)
            and sql.strip().upper().startswith("BEGIN IMMEDIATE")
        ):
            self._barrier.wait(timeout=10)
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_spend_two_connections_one_receipt(tmp_path):
    store, row, attempt, outcome, r2 = _reserved_state(tmp_path)
    other = AuthorityPolicyStore(Database(store._db.db_path))
    barrier = threading.Barrier(2)
    results: dict[str, object] = {}

    store_real = store._db._conn
    other_real = other._db._conn
    store._db._conn = _RendezvousConn(store_real, barrier)
    other._db._conn = _RendezvousConn(other_real, barrier)

    def run(name, target):
        results[name] = _spend(target, row, outcome, r2)

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
    assert statuses == ["already_spent_exact", "spent"], statuses
    assert len(_stage_events(store, SPENT)) == 1
    assert _envelope(store, outcome).lifecycle_state == "consumed"


# ── post-spend permitted contract outcomes ────────────────────────────────


def test_spend_then_late_acknowledgement_safely_refuses(tmp_path):
    store, row, attempt, outcome, r2 = _reserved_state(tmp_path)
    assert _spend(store, row, outcome, r2).status == "spent"
    before = _dump(store)
    ack = store.acknowledge_v2_notification_publication(
        root_task_id=TASK_ID, manager_agent=MANAGER,
        manager_session_id=SESSION_ID, result_id=row["id"],
        publication_attempt=1, publisher_boot_id=None,
    )
    # A settled notification is never returned to a publication state.
    assert ack.status in ("ack_pending", "publish_returned"), ack
    assert _dump(store) == before
    assert _dispatch(store).state == "retired"


def test_spend_then_invalidation_keeps_pointer_and_task(tmp_path):
    store, row, attempt, outcome, r2 = _reserved_state(tmp_path)
    assert _spend(store, row, outcome, r2).status == "spent"
    after_spend = _dump(store)
    invalidated = store.invalidate_v2_notification_generation(
        root_task_id=TASK_ID, manager_agent=MANAGER,
        manager_session_id=SESSION_ID, result_id=row["id"],
    )
    # Either an authenticated historical observation or a safe refusal; never a
    # pointer/task regression of the spent generation.
    assert invalidated.status in (
        "invalidated", "already_invalidated", "invalidation_pending",
    ), invalidated
    if invalidated.status == "invalidation_pending":
        assert _dump(store) == after_spend
    dispatch = _dispatch(store)
    assert dispatch.state == "retired", dispatch
    assert dispatch.generation_id == outcome.notification_id
    assert _task(store)["status"] == TaskStatus.IN_PROGRESS.value


def test_spend_stale_a_after_replacement_generation_b_refuses(tmp_path):
    store, row, attempt, outcome, r2 = _reserved_state(tmp_path)
    notification = store.get_v2_recovery_notification(outcome.notification_id)
    _point_dispatch_at_replacement(store, notification)
    store._db._conn.commit()
    before = _dump(store)
    result = _spend(store, row, outcome, r2)
    assert result.status == "spend_pending", result
    assert _dump(store) == before
    assert _envelope(store, outcome).lifecycle_state == "active"
