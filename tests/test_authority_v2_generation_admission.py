"""THR-229 checkpoint C3d3b — atomic generation admission + fallback fences.

Focused, isolated evidence for the accepted R4 publication/admission stages,
driving the REAL ``Database``/``AuthorityPolicyStore`` seam (no copies of its
logic) from genuine finalized/settled fixture state:

  * the atomic generation-admission claim from ``publishing`` (consumer before
    acknowledgement) and from ``published``, reserving one next runtime session,
    one monotonic step increment and one closed ``generation_claimed`` audit;
  * wrong/absent/null/mistyped/stale generation tokens refuse with no mutation;
  * cancelled/replaced owners refuse; duplicate admission refuses;
  * the separate admission-settlement transaction, its exact read-only replay
    and its refusal without a prior claim;
  * deterministic two-connection duplicate claim: one reservation, one step
    increment/audit, no loser mutation;
  * the mandatory ordinary ``try_claim_for_step`` fence on a pending v2 pointer
    and unchanged ordinary behavior without one;
  * the consumer-outruns-ack race: an admitted/settled generation acknowledges
    with only the exact ``publish_returned(P)`` observation, never a regression
    and never duplicate observations;
  * every new writer's caller-transaction nesting refusal and audit-failure
    rollback preserving exact prior residue.

The broad integration suite is SKIPPED under founder THR-243 seq42, never PASS.
"""
from __future__ import annotations

import types

import pytest

from runtime.infrastructure.database import Database
from runtime.models import TaskStatus
from runtime.orchestrator.authority import publish_authority_policy_v2_notifications
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
from tests.test_authority_v2_finalization_settlement import (
    MANAGER,
    SESSION_ID,
    TASK_ID,
    _counts,
)
from tests.test_authority_v2_publication_bookkeeping import (
    BOOT_A,
    BOOT_B,
    _TargetedFailingConn,
    _ack,
    _append_stage_event,
    _cancel_task,
    _claim,
    _delete_ordinary_completion,
    _delete_stage_event,
    _dispatch,
    _dump,
    _failure,
    _finalized,
    _finalized_recovery,
    _mutate_notification,
    _notification,
    _point_dispatch_at_replacement,
    _replace_owner_session,
    _stage_events,
    _write_dispatch,
)

RESERVED = "sess-reserved-generation"
_UNSET = object()


def _publishing(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    claimed = _claim(store, row)
    assert claimed.status == "claimed", claimed
    return store, row, attempt, outcome, claimed


def _published(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    claimed = _claim(store, row)
    ack = _ack(
        store, row, publication_attempt=claimed.publication_attempt,
        publisher_boot_id=claimed.publisher_boot_id,
    )
    assert ack.status == "published", ack
    return store, row, attempt, outcome, claimed


def _admit(store, outcome, *, generation=_UNSET, session=RESERVED):
    resolution = outcome.notification_id if generation is _UNSET else generation
    return store.try_claim_v2_continuation_generation(
        root_task_id=TASK_ID, manager_agent=MANAGER,
        manager_session_id=SESSION_ID,
        result_id=_reserved_result_id(store, outcome),
        generation_id=resolution,
        next_session_id=session,
    )


def _reserved_result_id(store, outcome):
    row = store._db._conn.execute(
        "SELECT result_id FROM authority_policy_v2_recovery_notifications "
        "WHERE notification_id=?",
        (outcome.notification_id,),
    ).fetchone()
    return row["result_id"]


def _settle(store, outcome, *, generation=None, session=RESERVED):
    return store.settle_v2_continuation_generation_admission(
        root_task_id=TASK_ID, manager_agent=MANAGER,
        manager_session_id=SESSION_ID,
        result_id=_reserved_result_id(store, outcome),
        generation_id=outcome.notification_id if generation is None else generation,
        next_session_id=session,
    )


def _task_row(store):
    return store._db._conn.execute(
        "SELECT * FROM tasks WHERE id=?", (TASK_ID,)
    ).fetchone()


def test_generation_claim_from_publishing_admits_and_reserves(tmp_path):
    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    before_task = _task_row(store)
    result = _admit(store, outcome)
    assert result.status == "claimed", result
    assert result.next_session_id == RESERVED
    assert result.orchestration_step_count == before_task["orchestration_step_count"] + 1
    assert result.generation_id == outcome.notification_id
    notification = _notification(store, outcome)
    assert notification.state == "admitted"
    assert notification.next_session_id == RESERVED
    dispatch = _dispatch(store)
    assert dispatch.state == "admitted"
    assert dispatch.generation_id == outcome.notification_id
    task = _task_row(store)
    assert task["status"] == TaskStatus.IN_PROGRESS.value
    assert task["block_kind"] is None
    assert task["assigned_agent"] == MANAGER
    assert task["current_session_id"] == RESERVED
    assert task["orchestration_step_count"] == result.orchestration_step_count
    events = _stage_events(store, "generation_claimed")
    assert len(events) == 1
    assert events[0]["next_session_id"] == RESERVED
    assert events[0]["generation_id"] == outcome.notification_id


def test_generation_claim_from_published_admits(tmp_path):
    store, row, attempt, outcome, claimed = _published(tmp_path)
    result = _admit(store, outcome)
    assert result.status == "claimed", result
    assert _notification(store, outcome).state == "admitted"
    assert _dispatch(store).state == "admitted"
    assert len(_stage_events(store, "generation_claimed")) == 1


def test_generation_claim_refuses_needed_not_yet_published(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    before = _dump(store)
    result = _admit(store, outcome)
    assert result.status == "generation_pending"
    assert result.reason == "evidence_drift"
    assert _dump(store) == before
    assert _notification(store, outcome).state == "needed"
    assert _task_row(store)["status"] == TaskStatus.PENDING.value


@pytest.mark.parametrize(
    "generation,expected_reason",
    [
        (None, "missing_generation"),
        ("", "missing_generation"),
        (123, "missing_generation"),
        ("APV2N-" + "b" * 64, "stale_generation"),
        ("not-a-generation-token", "stale_generation"),
    ],
)
def test_generation_claim_refuses_wrong_absent_null_mistyped_stale(
    tmp_path, generation, expected_reason
):
    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    before = _dump(store)
    result = _admit(store, outcome, generation=generation)
    assert result.status == "generation_pending"
    assert result.reason == expected_reason, result
    assert _dump(store) == before
    assert _notification(store, outcome).state == "publishing"
    assert _dispatch(store).state == "pending"
    assert _stage_events(store, "generation_claimed") == []


def test_generation_claim_refuses_cancelled_owner(tmp_path):
    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    store._db._conn.execute(
        "UPDATE tasks SET cancelled_at=? WHERE id=?",
        ("2026-09-20T00:00:00+00:00", TASK_ID),
    )
    store._db._conn.commit()
    before = _dump(store)
    result = _admit(store, outcome)
    assert result.status == "generation_pending"
    assert result.reason == "owner_lost"
    assert _dump(store) == before


def test_generation_claim_refuses_replaced_owner_session(tmp_path):
    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    store._db._conn.execute(
        "UPDATE tasks SET current_session_id='sess-replaced-owner' WHERE id=?",
        (TASK_ID,),
    )
    store._db._conn.commit()
    before = _dump(store)
    result = _admit(store, outcome)
    assert result.status == "generation_pending"
    assert result.reason == "owner_lost"
    assert _dump(store) == before


def test_generation_claim_refuses_duplicate_after_admission(tmp_path):
    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    assert _admit(store, outcome).status == "claimed"
    before = _dump(store)
    again = _admit(store, outcome, session="sess-other")
    assert again.status == "generation_pending"
    assert again.reason == "evidence_drift"
    assert _dump(store) == before
    assert len(_stage_events(store, "generation_claimed")) == 1


def test_settlement_settles_then_exact_replay(tmp_path):
    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    result = _admit(store, outcome)
    assert result.status == "claimed"
    settled = _settle(store, outcome)
    assert settled.status == "settled", settled
    assert _notification(store, outcome).state == "settled"
    assert _dispatch(store).state == "admitted"
    assert len(_stage_events(store, "notification_settled")) == 1
    before = _dump(store)
    replay = _settle(store, outcome)
    assert replay.status == "already_settled_exact"
    assert _dump(store) == before


def test_settlement_without_admission_refuses(tmp_path):
    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    before = _dump(store)
    result = _settle(store, outcome)
    assert result.status == "settlement_pending"
    assert result.reason in ("not_settleable", "identity_mismatch", "evidence_drift")
    assert _dump(store) == before


def test_settlement_wrong_reserved_session_refuses(tmp_path):
    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    assert _admit(store, outcome).status == "claimed"
    before = _dump(store)
    result = _settle(store, outcome, session="sess-not-reserved")
    assert result.status == "settlement_pending"
    assert result.reason == "identity_mismatch"
    assert _dump(store) == before
    assert _notification(store, outcome).state == "admitted"


def test_duplicate_generation_claim_two_connections_one_winner(tmp_path):
    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    result_id = _reserved_result_id(store, outcome)
    winner = store.try_claim_v2_continuation_generation(
        root_task_id=TASK_ID, manager_agent=MANAGER,
        manager_session_id=SESSION_ID, result_id=result_id,
        generation_id=outcome.notification_id, next_session_id=RESERVED,
    )
    assert winner.status == "claimed", winner
    after_winner = _dump(store)
    other = AuthorityPolicyStore(Database(tmp_path / "c2.db"))
    other.bind_v2_permission_surface_reader(lambda agent: "a" * 64)
    other.bind_v2_process_boot_id(BOOT_A)
    loser = other.try_claim_v2_continuation_generation(
        root_task_id=TASK_ID, manager_agent=MANAGER,
        manager_session_id=SESSION_ID, result_id=result_id,
        generation_id=outcome.notification_id, next_session_id="sess-loser",
    )
    assert loser.status == "generation_pending"
    assert loser.next_session_id is None
    assert _dump(store) == after_winner
    task = _task_row(store)
    assert task["current_session_id"] == RESERVED
    assert len(_stage_events(store, "generation_claimed")) == 1


def test_generation_claim_audit_failure_rolls_back_all_fields(tmp_path):
    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    before = _dump(store)
    before_task = _task_row(store)
    real = store._db._conn
    store._db._conn = _TargetedFailingConn(real, audit_stage="generation_claimed")
    try:
        result = _admit(store, outcome)
    finally:
        store._db._conn = real
    assert result.status == "generation_pending"
    assert result.reason == "generation_failed"
    assert _dump(store) == before
    notification = _notification(store, outcome)
    assert notification.state == "publishing"
    assert notification.next_session_id is None
    assert _dispatch(store).state == "pending"
    task = _task_row(store)
    assert task["status"] == before_task["status"] == TaskStatus.PENDING.value
    assert task["current_session_id"] == SESSION_ID
    assert task["orchestration_step_count"] == before_task["orchestration_step_count"]
    assert _stage_events(store, "generation_claimed") == []


def test_generation_claim_refuses_transaction_owned_caller(tmp_path):
    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    conn = store._db._conn
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "UPDATE tasks SET assigned_agent='caller-pending' WHERE id=?", (TASK_ID,)
        )
        result = _admit(store, outcome)
        assert result.status == "generation_pending"
        assert result.reason == "transaction_owned"
        assert conn.in_transaction
        assert conn.execute(
            "SELECT assigned_agent FROM tasks WHERE id=?", (TASK_ID,)
        ).fetchone()[0] == "caller-pending"
        assert _notification(store, outcome).state == "publishing"
    finally:
        conn.rollback()


def test_settlement_refuses_transaction_owned_caller(tmp_path):
    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    assert _admit(store, outcome).status == "claimed"
    conn = store._db._conn
    conn.execute("BEGIN IMMEDIATE")
    try:
        result = _settle(store, outcome)
        assert result.status == "settlement_pending"
        assert result.reason == "transaction_owned"
        assert conn.in_transaction
        assert _notification(store, outcome).state == "admitted"
    finally:
        conn.rollback()


def test_ordinary_claim_refuses_pending_v2_pointer(tmp_path):
    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    assert _dispatch(store).state == "pending"
    before = _dump(store)
    denied = store._db.try_claim_for_step(
        TASK_ID, expected_status=TaskStatus.PENDING,
        expected_block_kind=None, new_count=99,
    )
    assert denied is False
    assert _dump(store) == before
    assert _task_row(store)["status"] == TaskStatus.PENDING.value


def test_ordinary_claim_allows_root_without_pending_pointer(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    _write_dispatch(
        store, _dispatch(store).model_copy(update={"state": "retired"})
    )
    granted = store._db.try_claim_for_step(
        TASK_ID, expected_status=TaskStatus.PENDING,
        expected_block_kind=None, new_count=7,
    )
    assert granted is True
    task = _task_row(store)
    assert task["status"] == TaskStatus.IN_PROGRESS.value
    assert task["orchestration_step_count"] == 7


def test_ack_after_settled_admission_records_one_publish_returned(tmp_path):
    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    assert _admit(store, outcome).status == "claimed"
    assert _settle(store, outcome).status == "settled"
    before = _dump(store)
    ack = _ack(
        store, row, publication_attempt=claimed.publication_attempt,
        publisher_boot_id=claimed.publisher_boot_id,
    )
    assert ack.status == "publish_returned", ack
    assert ack.state == "settled"
    assert _notification(store, outcome).state == "settled"
    events = _stage_events(store, "publish_returned")
    assert len(events) == 1
    after_first = _dump(store)
    assert after_first != before
    duplicate = _ack(
        store, row, publication_attempt=claimed.publication_attempt,
        publisher_boot_id=claimed.publisher_boot_id,
    )
    assert duplicate.status == "publish_returned"
    assert len(_stage_events(store, "publish_returned")) == 1
    assert _dump(store) == after_first


def test_ack_after_publishing_admission_without_settlement(tmp_path):
    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    assert _admit(store, outcome).status == "claimed"
    before = _dump(store)
    ack = _ack(
        store, row, publication_attempt=claimed.publication_attempt,
        publisher_boot_id=claimed.publisher_boot_id,
    )
    assert ack.status == "publish_returned", ack
    assert ack.state == "admitted"
    assert _notification(store, outcome).state == "admitted"
    assert len(_stage_events(store, "publish_returned")) == 1
    assert _dump(store) != before


def test_ack_after_admission_refuses_stale_publication_attempt(tmp_path):
    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    assert _admit(store, outcome).status == "claimed"
    before = _dump(store)
    ack = _ack(
        store, row, publication_attempt=claimed.publication_attempt + 1,
        publisher_boot_id=claimed.publisher_boot_id,
    )
    assert ack.status == "ack_pending"
    assert ack.reason == "stale_claim"
    assert _dump(store) == before
    assert _stage_events(store, "publish_returned") == []


def test_generation_claim_after_recovery_settled_finalization(tmp_path):
    store, row, attempt, outcome = _finalized_recovery(tmp_path)
    claimed = _claim(store, row)
    assert claimed.status == "claimed"
    result = _admit(store, outcome)
    assert result.status == "claimed", result
    settled = _settle(store, outcome)
    assert settled.status == "settled"
    assert _notification(store, outcome).state == "settled"


def test_generation_claim_state_survives_reopen(tmp_path):
    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    assert _admit(store, outcome).status == "claimed"
    assert _settle(store, outcome).status == "settled"
    reopened = AuthorityPolicyStore(Database(tmp_path / "c2.db"))
    notification = reopened.get_v2_recovery_notification(outcome.notification_id)
    assert notification is not None
    assert notification.state == "settled"
    assert notification.next_session_id == RESERVED
    dispatch = reopened.get_v2_root_dispatch(TASK_ID)
    assert dispatch is not None and dispatch.state == "admitted"


class _RecordingQueue:
    def __init__(self, *, fail: bool = False):
        self.items: list[tuple] = []
        self.fail = fail

    def put_nowait(self, slug, task_id, *, metadata=None):
        if self.fail:
            raise RuntimeError("injected queue failure")
        self.items.append((slug, task_id, metadata))


class _PublishOrch:
    def __init__(self, db, slug="test-org"):
        self._db = db
        self._slug = slug


def test_publisher_claims_queues_and_acknowledges(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    store.bind_v2_process_boot_id(BOOT_A)
    queue = _RecordingQueue()
    receipts = publish_authority_policy_v2_notifications(
        _PublishOrch(store._db), queue,
    )
    assert receipts == [{
        "status": "published", "reason": None,
        "notification_id": outcome.notification_id,
        "generation_id": outcome.notification_id,
        "publication_attempt": 1,
    }]
    assert queue.items == [(
        "test-org", TASK_ID,
        {
            "authority_v2_generation": outcome.notification_id,
            "publication_attempt": 1,
        },
    )]
    assert _notification(store, outcome).state == "published"
    assert len(_stage_events(store, "published")) == 1
    # The exact published metadata is then admitted by the tagged generation
    # claim; the caller-side flow is covered by run_step/shipping tests.
    granted = store.try_claim_v2_continuation_generation(
        root_task_id=TASK_ID, manager_agent=MANAGER,
        manager_session_id=SESSION_ID, result_id=outcome.__dict__.get(
            "result_id", _reserved_result_id(store, outcome)
        ),
        generation_id=queue.items[0][2]["authority_v2_generation"],
        next_session_id=RESERVED,
    )
    assert granted.status == "claimed", granted


def test_publisher_queue_failure_records_reclaimable_state(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    store.bind_v2_process_boot_id(BOOT_A)
    receipts = publish_authority_policy_v2_notifications(
        _PublishOrch(store._db), _RecordingQueue(fail=True),
    )
    assert receipts[0]["status"] == "publish_failed"
    assert receipts[0]["publication_attempt"] == 1
    notification = _notification(store, outcome)
    assert notification.state == "publishing"
    assert notification.publisher_boot_id is None
    assert notification.lease_deadline is None
    assert len(_stage_events(store, "publish_failed")) == 1
    # Safely reclaimable by a restarting publisher; never stuck.
    store.bind_v2_process_boot_id(BOOT_B)
    reclaimed = _claim(store, row)
    assert reclaimed.status == "claimed", reclaimed
    assert reclaimed.publication_attempt == 2


def test_publisher_makes_no_queue_call_for_failed_claim(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    store.bind_v2_process_boot_id(BOOT_A)
    live = _claim(store, row)
    assert live.status == "claimed"
    queue = _RecordingQueue()
    before = _dump(store)
    receipts = publish_authority_policy_v2_notifications(
        _PublishOrch(store._db), queue,
    )
    assert receipts[0]["status"] == "publication_pending"
    assert receipts[0]["reason"] == "lease_live"
    assert queue.items == []
    assert _dump(store) == before


def test_publisher_skips_generation_already_admitted(tmp_path):
    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    assert _admit(store, outcome).status == "claimed"
    queue = _RecordingQueue()
    before = _dump(store)
    receipts = publish_authority_policy_v2_notifications(
        _PublishOrch(store._db), queue,
    )
    assert receipts == []
    assert queue.items == []
    assert _dump(store) == before


def test_huge_publication_attempt_history_refuses_bounded_work(tmp_path):
    """A coherent huge N.P must refuse without allocating an integer-sized set.

    The real retained P1 evidence is preserved, then N.P is mutated to the
    largest accepted value.  The completeness check now uses cardinality plus
    already-unique in-range keys, so the request stays proportional to the
    retained audit events actually read and refuses with the exact DB content
    unchanged.
    """
    store, row, attempt, outcome = _finalized(tmp_path)
    claimed = _claim(store, row)
    assert claimed.publication_attempt == 1
    _mutate_notification(
        store, outcome.notification_id, publication_attempt=2147483646,
    )
    before = _dump(store)
    result = _ack(
        store, row, publication_attempt=2147483646,
        publisher_boot_id=claimed.publisher_boot_id,
    )
    assert result.status == "ack_pending"
    assert result.reason == "evidence_drift"
    assert _dump(store) == before
    assert _notification(store, outcome).state == "publishing"


def test_huge_publication_attempt_history_missing_event_refuses(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    claimed = _claim(store, row)
    _delete_stage_event(store, "publish_claimed")
    _mutate_notification(
        store, outcome.notification_id, publication_attempt=2147483646,
    )
    before = _dump(store)
    result = _ack(
        store, row, publication_attempt=2147483646,
        publisher_boot_id=claimed.publisher_boot_id,
    )
    assert result.status == "ack_pending"
    assert result.reason == "evidence_drift"
    assert _dump(store) == before


def _direct_run_step_negatives(tmp_path, metadata, *, expect_pointer=True):
    from runtime.orchestrator.run_step import run_step_impl

    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    calls: list = []

    class _Orch:
        _db = store._db
        _audit = types.SimpleNamespace()
        _settings = types.SimpleNamespace(max_orchestration_steps=10)
        _queue = types.SimpleNamespace(put_nowait=lambda *a, **k: calls.append(a))

        def _run_agent(self, *a, **k):
            calls.append("run_agent")
            return None, None

    run_step_impl(_Orch(), TASK_ID, metadata=metadata)
    assert calls == []
    task = _task_row(store)
    if expect_pointer:
        assert task["status"] == TaskStatus.PENDING.value
        assert _dispatch(store).state == "pending"
        assert _notification(store, outcome).state == "publishing"


def test_direct_run_step_untagged_item_refuses_pending_v2_pointer(tmp_path):
    """A missed/legacy producer cannot dispatch a root awaiting v2 continuation."""
    _direct_run_step_negatives(tmp_path, None)


def test_direct_run_step_tagged_item_with_absent_unknown_generation_refuses(tmp_path):
    _direct_run_step_negatives(
        tmp_path, {"authority_v2_generation": "APV2N-" + "b" * 64},
    )


def test_direct_run_step_tagged_item_with_null_generation_refuses(tmp_path):
    _direct_run_step_negatives(tmp_path, {"authority_v2_generation": None})


# ── TASK-8552 correction: complete row/evidence pre-state ────────────────


def _preexisting_event(store, outcome, attempt, stage, *, session=RESERVED):
    return store._db._v2_admission_event_payload(
        stage=stage, attempt_id=attempt.attempt_id,
        candidate_id=outcome.candidate_id, result_id=_reserved_result_id(store, outcome),
        envelope_id=outcome.envelope_id, notification_id=outcome.notification_id,
        generation_id=outcome.notification_id, next_session_id=session,
    )


def test_generation_claim_healthy_publishing_control(tmp_path):
    """Positive control: genuine publishing-before-ack state still admits."""
    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    before = _dump(store)
    result = _admit(store, outcome)
    assert result.status == "claimed", result
    assert _dump(store) != before


def test_generation_claim_healthy_published_control(tmp_path):
    """Positive control: genuine published state still admits."""
    store, row, attempt, outcome, claimed = _published(tmp_path)
    before = _dump(store)
    result = _admit(store, outcome)
    assert result.status == "claimed", result
    assert _dump(store) != before


@pytest.mark.parametrize("case", [
    "missing_publish_claimed", "missing_published", "duplicate_publish_claimed",
])
def test_generation_admission_refuses_damaged_retained_publication_evidence(
    tmp_path, case,
):
    """A missing/duplicated retained publication event refuses with no mutation."""
    store, row, attempt, outcome, claimed = (
        _published(tmp_path) if case == "missing_published" else _publishing(tmp_path)
    )
    if case == "missing_publish_claimed":
        _delete_stage_event(store, "publish_claimed")
    elif case == "missing_published":
        _delete_stage_event(store, "published")
    else:
        _append_stage_event(store, dict(_stage_events(store, "publish_claimed")[0]))
    before = _dump(store)
    result = _admit(store, outcome)
    assert result.status == "generation_pending", result
    assert result.reason == "evidence_drift", result
    assert _dump(store) == before
    assert _stage_events(store, "generation_claimed") == []


@pytest.mark.parametrize("stage", ["generation_claimed", "notification_settled"])
def test_generation_admission_refuses_preexisting_admission_or_settlement_event(
    tmp_path, stage,
):
    """A preexisting related admission/settlement event is never ignored."""
    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    event = _preexisting_event(store, outcome, attempt, stage)
    _append_stage_event(store, event)
    before = _dump(store)
    result = _admit(store, outcome)
    assert result.status == "generation_pending", result
    assert result.reason == "evidence_drift", result
    assert _dump(store) == before
    # The prior event is preserved exactly and never repaired by another insert.
    assert len(_stage_events(store, stage)) == 1
    if stage == "notification_settled":
        assert _stage_events(store, "generation_claimed") == []


def test_generation_claim_refuses_delayed_a_after_replacement_b_pending(tmp_path):
    """A delayed admission for A cannot adopt a pointer that now names B."""
    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    _point_dispatch_at_replacement(store, _notification(store, outcome))
    before = _dump(store)
    result = _admit(store, outcome)
    assert result.status == "generation_pending", result
    assert result.reason in ("evidence_drift", "identity_mismatch"), result
    assert _dump(store) == before
    assert _stage_events(store, "generation_claimed") == []


def test_settlement_refuses_missing_retained_publication_evidence(tmp_path):
    """Deleting a retained publication claim holds settlement."""
    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    assert _admit(store, outcome).status == "claimed"
    _delete_stage_event(store, "publish_claimed")
    before = _dump(store)
    result = _settle(store, outcome)
    assert result.status == "settlement_pending", result
    assert result.reason == "evidence_drift", result
    assert _dump(store) == before
    assert _notification(store, outcome).state == "admitted"
    assert _stage_events(store, "notification_settled") == []


def test_settlement_refuses_missing_ordinary_completion_proof(tmp_path):
    """Deleting the exact ordinary completion holds settlement."""
    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    assert _admit(store, outcome).status == "claimed"
    _delete_ordinary_completion(store, row["id"])
    before = _dump(store)
    result = _settle(store, outcome)
    assert result.status == "settlement_pending", result
    assert result.reason == "evidence_drift", result
    assert _dump(store) == before
    assert _notification(store, outcome).state == "admitted"
    assert _stage_events(store, "notification_settled") == []


def test_settlement_refuses_preexisting_settlement_event(tmp_path):
    """A stray preexisting settlement event blocks the first settle."""
    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    assert _admit(store, outcome).status == "claimed"
    _append_stage_event(
        store, _preexisting_event(store, outcome, attempt, "notification_settled"),
    )
    before = _dump(store)
    result = _settle(store, outcome)
    assert result.status == "settlement_pending", result
    assert result.reason == "evidence_drift", result
    assert _dump(store) == before
    assert len(_stage_events(store, "notification_settled")) == 1


@pytest.mark.parametrize("mutate", ["cancel", "replace"])
def test_settlement_refuses_cancelled_or_replaced_owner(tmp_path, mutate):
    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    assert _admit(store, outcome).status == "claimed"
    if mutate == "cancel":
        _cancel_task(store)
    else:
        _replace_owner_session(store)
    before = _dump(store)
    result = _settle(store, outcome)
    assert result.status == "settlement_pending", result
    assert result.reason == "owner_lost", result
    assert _dump(store) == before
    assert _notification(store, outcome).state == "admitted"


def test_settlement_replay_after_reopen_is_read_only(tmp_path):
    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    assert _admit(store, outcome).status == "claimed"
    assert _settle(store, outcome).status == "settled"
    result_id = _reserved_result_id(store, outcome)
    other = AuthorityPolicyStore(Database(tmp_path / "c2.db"))
    other.bind_v2_permission_surface_reader(lambda agent: "a" * 64)
    other.bind_v2_process_boot_id(BOOT_A)
    before = _dump(store)
    replay = other.settle_v2_continuation_generation_admission(
        root_task_id=TASK_ID, manager_agent=MANAGER,
        manager_session_id=SESSION_ID, result_id=result_id,
        generation_id=outcome.notification_id, next_session_id=RESERVED,
    )
    assert replay.status == "already_settled_exact", replay
    assert _dump(store) == before


@pytest.mark.parametrize("fragment,occurrence", [
    ("UPDATE authority_policy_v2_recovery_notifications", 1),
    ("UPDATE authority_policy_v2_root_dispatch", 1),
    ("UPDATE tasks", 1),
])
def test_generation_claim_sql_boundary_failure_rolls_back(
    tmp_path, fragment, occurrence,
):
    """Each exact N/D/task UPDATE boundary rolls every field back."""
    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    before = _dump(store)
    real = store._db._conn
    store._db._conn = _TargetedFailingConn(
        real, sql_fragment=fragment, occurrence=occurrence,
    )
    try:
        result = _admit(store, outcome)
    finally:
        store._db._conn = real
    assert result.status == "generation_pending", result
    assert result.reason == "generation_failed", result
    assert _dump(store) == before
    assert _stage_events(store, "generation_claimed") == []


def test_generation_claim_commit_failure_rolls_back(tmp_path):
    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    before = _dump(store)
    real = store._db._conn
    store._db._conn = _TargetedFailingConn(real, fail_commit=True)
    try:
        result = _admit(store, outcome)
    finally:
        store._db._conn = real
    assert result.status == "generation_pending", result
    assert result.reason == "generation_failed", result
    assert _dump(store) == before
    assert _stage_events(store, "generation_claimed") == []


def test_settlement_sql_boundary_failure_rolls_back(tmp_path):
    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    assert _admit(store, outcome).status == "claimed"
    before = _dump(store)
    real = store._db._conn
    store._db._conn = _TargetedFailingConn(
        real, sql_fragment="UPDATE authority_policy_v2_recovery_notifications",
        occurrence=1,
    )
    try:
        result = _settle(store, outcome)
    finally:
        store._db._conn = real
    assert result.status == "settlement_pending", result
    assert result.reason == "settlement_failed", result
    assert _dump(store) == before
    assert _notification(store, outcome).state == "admitted"
    assert _stage_events(store, "notification_settled") == []


def test_settlement_audit_failure_rolls_back(tmp_path):
    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    assert _admit(store, outcome).status == "claimed"
    before = _dump(store)
    real = store._db._conn
    store._db._conn = _TargetedFailingConn(real, audit_stage="notification_settled")
    try:
        result = _settle(store, outcome)
    finally:
        store._db._conn = real
    assert result.status == "settlement_pending", result
    assert result.reason == "settlement_failed", result
    assert _dump(store) == before
    assert _stage_events(store, "notification_settled") == []


def test_settlement_commit_failure_rolls_back(tmp_path):
    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    assert _admit(store, outcome).status == "claimed"
    before = _dump(store)
    real = store._db._conn
    store._db._conn = _TargetedFailingConn(real, fail_commit=True)
    try:
        result = _settle(store, outcome)
    finally:
        store._db._conn = real
    assert result.status == "settlement_pending", result
    assert result.reason == "settlement_failed", result
    assert _dump(store) == before
    assert _stage_events(store, "notification_settled") == []


class _RunStepOrch:
    def __init__(self, store, calls):
        self._db = store._db
        self._calls = calls
        self._audit = types.SimpleNamespace()
        self._settings = types.SimpleNamespace(max_orchestration_steps=10)
        self._queue = types.SimpleNamespace()

    def _build_session_id(self):
        return RESERVED

    def _run_agent(self, *args, **kwargs):
        self._calls.append("run_agent")
        return None, None


def test_direct_run_step_corrupted_publication_prerequisite_does_not_launch(
    tmp_path,
):
    """A corrupted retained claim at the tagged path launches nothing at all."""
    from runtime.orchestrator.run_step import run_step_impl

    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    _delete_stage_event(store, "publish_claimed")
    calls: list = []
    run_step_impl(
        _RunStepOrch(store, calls), TASK_ID,
        metadata={"authority_v2_generation": outcome.notification_id},
    )
    assert calls == []
    assert _task_row(store)["status"] == TaskStatus.PENDING.value
    assert _dispatch(store).state == "pending"
    assert _notification(store, outcome).state == "publishing"
    assert _stage_events(store, "generation_claimed") == []


def test_direct_run_step_settlement_failure_holds_launch_and_replay(tmp_path):
    """A settlement failure after a valid claim holds launch; a replay cannot
    launch the already-admitted invocation a second time."""
    from runtime.orchestrator.run_step import run_step_impl

    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    calls: list = []
    # ``run_step`` resolves the Database via ``orch._db``; patch that exact
    # instance method so the injected corruption happens at the real settlement
    # boundary inside the real draft.
    original = store._db.settle_v2_continuation_generation_admission
    state = {"first": True}

    def _fail_settlement_once(**kwargs):
        if state["first"]:
            state["first"] = False
            _delete_ordinary_completion(store, row["id"])
        return original(**kwargs)

    store._db.settle_v2_continuation_generation_admission = _fail_settlement_once
    try:
        metadata = {"authority_v2_generation": outcome.notification_id}
        run_step_impl(_RunStepOrch(store, calls), TASK_ID, metadata=metadata)
        assert calls == []
        task = _task_row(store)
        assert task["status"] == TaskStatus.IN_PROGRESS.value
        assert task["current_session_id"] == RESERVED
        assert _notification(store, outcome).state == "admitted"
        assert _stage_events(store, "notification_settled") == []
        # No replay/reopen may launch the already-admitted invocation again.
        run_step_impl(_RunStepOrch(store, calls), TASK_ID, metadata=metadata)
        assert calls == []
        assert _stage_events(store, "notification_settled") == []
    finally:
        del store._db.settle_v2_continuation_generation_admission


# ── C3d3b correction: omitted post-admission acknowledgement boundary ────
#
# TASK-8555 closes the admitted/settled acknowledgement early-return branch.
# Every admitted/settled ack (and exact replay) must authenticate its COMPLETE
# coherent stage pre-state and classify every POTENTIALLY related
# ``publish_returned`` observation three ways: zero -> exactly one insert,
# one exact -> read-only replay, anything else -> refuse with the exact prior
# residue.  The nine manager ack-probe invalids are reproduced here as
# asserting tests on the immutable 26f06b5c base, plus representative
# absent/null/wrong-type/distinct-discriminator/extra-key/opaque cases.


def _claimed_event(store):
    events = _stage_events(store, "publish_claimed")
    assert len(events) == 1, events
    return dict(events[0])


def _returned_payload(store, claimed, **overrides):
    """The exact closed ``publish_returned(P)`` payload for this generation."""
    base = _claimed_event(store)
    observed = {
        "stage": "publish_returned",
        "attempt_id": base["attempt_id"],
        "candidate_id": base["candidate_id"],
        "result_id": base["result_id"],
        "envelope_id": base["envelope_id"],
        "notification_id": base["notification_id"],
        "generation_id": base["generation_id"],
        "publication_attempt": claimed.publication_attempt,
        "publisher_boot_id": claimed.publisher_boot_id,
    }
    observed.update(overrides)
    return observed


def _admitted_stage(tmp_path, *, settle: bool = False):
    store, row, attempt, outcome, claimed = _publishing(tmp_path)
    assert _admit(store, outcome).status == "claimed"
    if settle:
        assert _settle(store, outcome).status == "settled"
    return store, row, attempt, outcome, claimed


def _ack_admitted(store, row, claimed):
    return _ack(
        store, row, publication_attempt=claimed.publication_attempt,
        publisher_boot_id=claimed.publisher_boot_id,
    )


class _OverlapConn:
    """Test-only connection wrapper that rendezvouses TWO real connections at
    their first ``BEGIN IMMEDIATE``.

    The shared barrier guarantees both acknowledgements are genuinely
    in-flight concurrently before either transaction proceeds, so the
    two-connection proof records actual overlap instead of a sequential
    replay.  A deterministic barrier never relies on sleeps.
    """

    def __init__(self, real, barrier, entered, tag, lock):
        self._real = real
        self._barrier = barrier
        self._entered = entered
        self._tag = tag
        self._lock = lock
        self._rendezvoused = False

    def execute(self, sql, *args, **kwargs):
        if (
            not self._rendezvoused
            and sql.strip().upper().startswith("BEGIN IMMEDIATE")
        ):
            self._rendezvoused = True
            with self._lock:
                self._entered.append(self._tag)
            self._barrier.wait(timeout=30)
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _insert_extra_related_receipt(store, result_id):
    """A second POTENTIALLY related recovery receipt (conflict, not absence)."""
    store._db._conn.execute(
        """INSERT INTO task_completion_recoveries
           (task_id, agent, origin_session_id, recovery_session_id,
            provider_session_id, claimed_at, expires_at, state,
            accepted_result_id, accepted_result_session_id)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            TASK_ID, MANAGER, "sess-other-origin", "sess-other-recovery",
            "prov-2", "2026-01-01T00:00:00+00:00",
            "2999-01-01T00:00:00+00:00", "callback_accepted",
            result_id, "sess-other-recovery",
        ),
    )
    store._db._conn.commit()


_ACK_CORRUPTION_CASES = [
    ("admitted", "missing_completion"),
    ("settled", "missing_completion"),
    ("admitted", "conflicting_publish_returned"),
    ("settled", "conflicting_publish_returned"),
    ("admitted", "duplicate_publish_returned"),
    ("settled", "duplicate_publish_returned"),
    ("admitted", "conflicting_publish_failed"),
    ("settled", "conflicting_publish_failed"),
    ("admitted", "preexisting_notification_settled"),
]


@pytest.mark.parametrize("state,case", _ACK_CORRUPTION_CASES)
def test_ack_after_admission_refuses_corrupted_stage_evidence(tmp_path, state, case):
    """The nine manager ack-probe invalids now refuse with exact prior residue.

    Reproduces manager step5 ack-probe.py cases at the PUBLIC ack method: a
    deleted ordinary completion, a conflicting/duplicate ``publish_returned``
    observation, a conflicting current-P ``publish_failed`` and a preexisting
    ``notification_settled`` while admitted all refuse and never append.
    """
    store, row, attempt, outcome, claimed = _admitted_stage(
        tmp_path, settle=(state == "settled")
    )
    if case == "missing_completion":
        _delete_ordinary_completion(store, row["id"])
    elif case in ("conflicting_publish_returned", "duplicate_publish_returned"):
        assert _ack_admitted(store, row, claimed).status == "publish_returned"
        event = dict(_stage_events(store, "publish_returned")[0])
        if case == "conflicting_publish_returned":
            event["publisher_boot_id"] = "different-boot"
        _append_stage_event(store, event)
    elif case == "conflicting_publish_failed":
        event = dict(_stage_events(store, "publish_claimed")[0])
        event["stage"] = "publish_failed"
        _append_stage_event(store, event)
    elif case == "preexisting_notification_settled":
        event = dict(_stage_events(store, "generation_claimed")[0])
        event["stage"] = "notification_settled"
        _append_stage_event(store, event)
    else:  # pragma: no cover - defensive
        raise AssertionError(case)

    before = _dump(store)
    returned_before = len(_stage_events(store, "publish_returned"))
    refusal = _ack_admitted(store, row, claimed)
    assert refusal.status == "ack_pending", refusal
    assert refusal.reason == "evidence_drift", refusal
    assert _dump(store) == before
    assert len(_stage_events(store, "publish_returned")) == returned_before
    # A refused append can never become a later accepted duplicate.
    again = _ack_admitted(store, row, claimed)
    assert again.status == "ack_pending", again
    assert again.reason == "evidence_drift", again
    assert _dump(store) == before
    assert len(_stage_events(store, "publish_returned")) == returned_before


def _assert_refuses_related_returned(tmp_path, *, settle, observed_overrides):
    store, row, attempt, outcome, claimed = _admitted_stage(tmp_path, settle=settle)
    _append_stage_event(store, _returned_payload(store, claimed, **observed_overrides))
    before = _dump(store)
    refusal = _ack_admitted(store, row, claimed)
    assert refusal.status == "ack_pending", refusal
    assert refusal.reason == "evidence_drift", refusal
    assert _dump(store) == before
    assert len(_stage_events(store, "publish_returned")) == 1


@pytest.mark.parametrize("settle", [False, True])
def test_ack_refuses_null_related_publish_returned(tmp_path, settle):
    _assert_refuses_related_returned(
        tmp_path, settle=settle, observed_overrides={"publisher_boot_id": None}
    )


@pytest.mark.parametrize("settle", [False, True])
def test_ack_refuses_wrong_type_related_publish_returned(tmp_path, settle):
    _assert_refuses_related_returned(
        tmp_path, settle=settle, observed_overrides={"publication_attempt": "1"}
    )


@pytest.mark.parametrize("settle", [False, True])
def test_ack_refuses_distinct_discriminator_with_exact_reference(tmp_path, settle):
    _assert_refuses_related_returned(
        tmp_path, settle=settle, observed_overrides={"publication_attempt": 2}
    )


@pytest.mark.parametrize("settle", [False, True])
def test_ack_refuses_extra_key_related_publish_returned(tmp_path, settle):
    _assert_refuses_related_returned(
        tmp_path, settle=settle, observed_overrides={"extra_key": True}
    )


@pytest.mark.parametrize("settle", [False, True])
def test_ack_refuses_opaque_related_publication_evidence(tmp_path, settle):
    store, row, attempt, outcome, claimed = _admitted_stage(tmp_path, settle=settle)
    _append_stage_event(store, "opaque-non-object-body")
    before = _dump(store)
    refusal = _ack_admitted(store, row, claimed)
    assert refusal.status == "ack_pending", refusal
    # The fail-closed post-final reader may refuse an opaque identity-scoped row
    # before the ack branch itself classifies it; either way zero mutation.
    assert refusal.reason in ("evidence_drift", "identity_mismatch"), refusal
    assert _dump(store) == before


@pytest.mark.parametrize("settle", [False, True])
def test_ack_refuses_missing_retained_claim_evidence(tmp_path, settle):
    store, row, attempt, outcome, claimed = _admitted_stage(tmp_path, settle=settle)
    _delete_stage_event(store, "publish_claimed")
    before = _dump(store)
    refusal = _ack_admitted(store, row, claimed)
    assert refusal.status == "ack_pending", refusal
    assert refusal.reason == "evidence_drift", refusal
    assert _dump(store) == before


@pytest.mark.parametrize("settle", [False, True])
def test_ack_refuses_duplicate_retained_claim_evidence(tmp_path, settle):
    store, row, attempt, outcome, claimed = _admitted_stage(tmp_path, settle=settle)
    _append_stage_event(store, _claimed_event(store))
    before = _dump(store)
    refusal = _ack_admitted(store, row, claimed)
    assert refusal.status == "ack_pending", refusal
    assert refusal.reason == "evidence_drift", refusal
    assert _dump(store) == before


@pytest.mark.parametrize("settle", [False, True])
def test_ack_refuses_missing_generation_claimed_evidence(tmp_path, settle):
    store, row, attempt, outcome, claimed = _admitted_stage(tmp_path, settle=settle)
    _delete_stage_event(store, "generation_claimed")
    before = _dump(store)
    refusal = _ack_admitted(store, row, claimed)
    assert refusal.status == "ack_pending", refusal
    assert refusal.reason == "admission_evidence_missing", refusal
    assert _dump(store) == before


def test_ack_after_settled_refuses_missing_notification_settled(tmp_path):
    store, row, attempt, outcome, claimed = _admitted_stage(tmp_path, settle=True)
    _delete_stage_event(store, "notification_settled")
    before = _dump(store)
    refusal = _ack_admitted(store, row, claimed)
    assert refusal.status == "ack_pending", refusal
    assert refusal.reason == "admission_evidence_missing", refusal
    assert _dump(store) == before


@pytest.mark.parametrize("settle", [False, True])
def test_ack_refuses_malformed_settlement_evidence(tmp_path, settle):
    store, row, attempt, outcome, claimed = _admitted_stage(tmp_path, settle=settle)
    store._db._conn.execute(
        "UPDATE audit_log SET payload=json_set(payload,'$._result_row_id',999999) "
        "WHERE action='completion_report'"
    )
    store._db._conn.commit()
    before = _dump(store)
    refusal = _ack_admitted(store, row, claimed)
    assert refusal.status == "ack_pending", refusal
    assert refusal.reason == "evidence_drift", refusal
    assert _dump(store) == before


@pytest.mark.parametrize("settle", [False, True])
def test_ack_first_observation_then_exact_replay_is_read_only(tmp_path, settle):
    """Healthy positive: exactly ONE observation, then a read-only replay."""
    store, row, attempt, outcome, claimed = _admitted_stage(tmp_path, settle=settle)
    before = _dump(store)
    first = _ack_admitted(store, row, claimed)
    assert first.status == "publish_returned", first
    assert first.state == ("settled" if settle else "admitted")
    assert len(_stage_events(store, "publish_returned")) == 1
    after_first = _dump(store)
    assert after_first != before
    replay = _ack_admitted(store, row, claimed)
    assert replay.status == "publish_returned", replay
    assert _dump(store) == after_first
    assert len(_stage_events(store, "publish_returned")) == 1


def test_ack_after_settled_exact_replay_survives_reopen(tmp_path):
    store, row, attempt, outcome, claimed = _admitted_stage(tmp_path, settle=True)
    assert _ack_admitted(store, row, claimed).status == "publish_returned"
    after_first = _dump(store)
    reopened = AuthorityPolicyStore(Database(tmp_path / "c2.db"))
    reopened.bind_v2_permission_surface_reader(lambda agent: "a" * 64)
    reopened.bind_v2_process_boot_id(BOOT_A)
    replay = _ack_admitted(reopened, row, claimed)
    assert replay.status == "publish_returned", replay
    assert _dump(store) == after_first
    assert len(_stage_events(store, "publish_returned")) == 1


def test_ack_after_recovery_settled_records_one_publish_returned(tmp_path):
    """The genuine exact recovery Q is an accepted settlement proof."""
    store, row, attempt, outcome = _finalized_recovery(tmp_path)
    claimed = _claim(store, row)
    assert claimed.status == "claimed"
    assert _admit(store, outcome).status == "claimed"
    assert _settle(store, outcome).status == "settled"
    ack = _ack(
        store, row, publication_attempt=claimed.publication_attempt,
        publisher_boot_id=claimed.publisher_boot_id,
    )
    assert ack.status == "publish_returned", ack
    assert ack.state == "settled"
    assert len(_stage_events(store, "publish_returned")) == 1


def test_ack_after_recovery_settled_refuses_related_receipt_conflict(tmp_path):
    """A second POTENTIALLY related receipt is a conflict, not absence."""
    store, row, attempt, outcome = _finalized_recovery(tmp_path)
    claimed = _claim(store, row)
    assert claimed.status == "claimed"
    assert _admit(store, outcome).status == "claimed"
    assert _settle(store, outcome).status == "settled"
    _insert_extra_related_receipt(store, row["id"])
    before = _dump(store)
    refusal = _ack(
        store, row, publication_attempt=claimed.publication_attempt,
        publisher_boot_id=claimed.publisher_boot_id,
    )
    assert refusal.status == "ack_pending", refusal
    assert refusal.reason == "evidence_drift", refusal
    assert _dump(store) == before
    assert _stage_events(store, "publish_returned") == []


def test_ack_after_admitted_p1_p2_reclaim_history(tmp_path):
    """A legitimate P1 failure -> P2 reclaim remains valid prior history."""
    store, row, attempt, outcome = _finalized(tmp_path)
    first = _claim(store, row)
    assert first.status == "claimed"
    recorded = _failure(
        store, row, publication_attempt=first.publication_attempt,
        publisher_boot_id=BOOT_A,
    )
    assert recorded.status == "failure_recorded", recorded
    store.bind_v2_process_boot_id(BOOT_B)
    second = _claim(store, row)
    assert second.status == "claimed", second
    assert second.publication_attempt == 2
    assert second.publisher_boot_id == BOOT_B
    assert _admit(store, outcome).status == "claimed"
    ack = _ack(
        store, row, publication_attempt=second.publication_attempt,
        publisher_boot_id=BOOT_B,
    )
    assert ack.status == "publish_returned", ack
    assert ack.publication_attempt == 2
    returned = _stage_events(store, "publish_returned")
    assert len(returned) == 1
    assert returned[0]["publication_attempt"] == 2
    assert returned[0]["publisher_boot_id"] == BOOT_B


@pytest.mark.parametrize("settle", [False, True])
def test_ack_after_admission_refuses_caller_transaction_nesting(tmp_path, settle):
    """The nesting refusal is classified BEFORE any BEGIN/ROLLBACK and the
    caller's still-uncommitted transaction and exact pending mutation survive
    the public call for both the admitted and settled states."""
    store, row, attempt, outcome, claimed = _admitted_stage(tmp_path, settle=settle)
    durable_before = _dump(store)
    conn = store._db._conn
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "UPDATE tasks SET assigned_agent='caller-pending' WHERE id=?", (TASK_ID,)
        )
        pending_before = _dump(store)
        result = _ack_admitted(store, row, claimed)
        # Asserted BEFORE the finally-rollback: the method must not have
        # begun, committed, rolled back or changed the caller's open
        # transaction or its exact pending work.
        assert conn.in_transaction is True
        assert _dump(store) == pending_before
        assert conn.execute(
            "SELECT assigned_agent FROM tasks WHERE id=?", (TASK_ID,)
        ).fetchone()["assigned_agent"] == "caller-pending"
    finally:
        conn.rollback()
    assert result.status == "ack_pending", result
    assert result.reason == "transaction_owned", result
    assert conn.in_transaction is False
    assert _stage_events(store, "publish_returned") == []
    # The uncommitted caller mutation never became durable and the exact
    # durable pre-state survives the refusal.
    assert _dump(store) == durable_before
    assert store._db.get_task(TASK_ID).assigned_agent != "caller-pending"


@pytest.mark.parametrize("settle", [False, True])
def test_ack_admitted_publish_returned_audit_failure_rolls_back(tmp_path, settle):
    store, row, attempt, outcome, claimed = _admitted_stage(tmp_path, settle=settle)
    before = _dump(store)
    real = store._db._conn
    store._db._conn = _TargetedFailingConn(real, audit_stage="publish_returned")
    try:
        result = _ack_admitted(store, row, claimed)
    finally:
        store._db._conn = real
    assert result.status == "ack_pending", result
    assert result.reason == "ack_failed", result
    assert _dump(store) == before
    assert _stage_events(store, "publish_returned") == []
    # One safe exact retry after the injected failure.
    retry = _ack_admitted(store, row, claimed)
    assert retry.status == "publish_returned", retry
    assert len(_stage_events(store, "publish_returned")) == 1


@pytest.mark.parametrize("settle", [False, True])
def test_ack_admitted_commit_failure_rolls_back(tmp_path, settle):
    store, row, attempt, outcome, claimed = _admitted_stage(tmp_path, settle=settle)
    before = _dump(store)
    real = store._db._conn
    store._db._conn = _TargetedFailingConn(real, fail_commit=True)
    try:
        result = _ack_admitted(store, row, claimed)
    finally:
        store._db._conn = real
    assert result.status == "ack_pending", result
    assert result.reason == "ack_failed", result
    assert _dump(store) == before
    assert _stage_events(store, "publish_returned") == []
    retry = _ack_admitted(store, row, claimed)
    assert retry.status == "publish_returned", retry
    assert len(_stage_events(store, "publish_returned")) == 1


def test_ack_after_admitted_two_connections_one_observation(tmp_path):
    """Two REAL connections acknowledge the same generation with a
    deterministic rendezvous at ``BEGIN IMMEDIATE`` so both acknowledgements
    are genuinely in-flight: exactly one durable ``publish_returned``
    observation commits and the loser is a read-only replay with no state
    regression."""
    import threading

    store, row, attempt, outcome, claimed = _admitted_stage(tmp_path)
    other = AuthorityPolicyStore(Database(tmp_path / "c2.db"))
    other.bind_v2_permission_surface_reader(lambda agent: "a" * 64)
    other.bind_v2_process_boot_id(BOOT_A)

    barrier = threading.Barrier(2)
    entered: list[str] = []
    lock = threading.Lock()
    for tag, s in (("a", store), ("b", other)):
        s._db._conn = _OverlapConn(s._db._conn, barrier, entered, tag, lock)

    results: dict[str, object] = {}

    def _run(tag, s):
        results[tag] = _ack_admitted(s, row, claimed)

    threads = [
        threading.Thread(target=_run, args=("a", store)),
        threading.Thread(target=_run, args=("b", other)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    for thread in threads:
        assert not thread.is_alive(), "ack did not complete"

    # Recorded actual overlap: both connections reached their own
    # BEGIN IMMEDIATE and rendezvoused at the barrier before either
    # transaction proceeded.
    with lock:
        assert sorted(entered) == ["a", "b"], entered
    assert set(results) == {"a", "b"}
    assert all(r.status == "publish_returned" for r in results.values()), results
    # Exactly one durable observation: no loser append and no state
    # regression (the generation stays admitted and no published audit is
    # minted by the outrun-acknowledgement observation).
    assert len(_stage_events(store, "publish_returned")) == 1
    assert _notification(store, outcome).state == "admitted"
    assert _stage_events(store, "published") == []
