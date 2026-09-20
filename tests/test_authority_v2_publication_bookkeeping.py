"""THR-229 checkpoint C3d3a — authenticated publication bookkeeping.

Focused, isolated evidence for the accepted R4 discovery/publication-claim/
acknowledgement/failure/invalidation storage seams, driving the REAL
``Database``/``AuthorityPolicyStore`` boundary (no copies of its logic):

  * read-only discovery of every needed/publishing/published N whose current
    root dispatch pointer is pending(G), independent of any recovery transition;
  * ONE synchronized claim/reclaim transaction (needed -> publishing, or a
    dead/restarted publisher or an expired 30-second server lease), the bounded
    positive publication attempt and the closed ``publish_claimed`` audit;
  * the exact acknowledgement (publishing -> published + closed ``published``
    audit), exact read-only retry and stale-token refusal;
  * the bounded audited queue-failure bookkeeping that preserves a safely
    reclaimable publishing state;
  * exact cancellation/replacement-owner invalidation of one old generation,
    retiring the root pointer ONLY while it still names that generation;
  * caller-transaction nesting refusal, two-connection contention and reopen.

These methods perform NO queue call, task mutation, reevaluation, remint or
generation admission: the real publisher plus the non-bypassable generation
admission remain later units.  The broad integration suite is SKIPPED under
founder THR-243 seq42, never PASS.

Fixture note: notifications staged into ``admitted``/``settled`` represent a
future generation-admission state.  Because that real producer is not
implemented in this checkpoint, the acknowledgement path for it must refuse
fail-closed; the staged rows prove only the refusal, never real admission.
"""
from __future__ import annotations

import json

import pytest

from runtime.infrastructure.database import Database
from runtime.models import (
    AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
    TaskStatus,
    authority_policy_v2_canonical_json_bytes,
)
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
from tests.test_authority_v2_finalization_settlement import (
    MANAGER,
    SESSION_ID,
    TASK_ID,
    _admitted,
    _counts,
    _drive,
    _finalize,
    _insert_ordinary_completion,
    _reopen,
    _seed_q,
    _settle,
)

BOOT_A = "boot-publication-a"
BOOT_B = "boot-publication-b"
# A shape-valid replacement generation token (APV2N- + 64 hex), matching the
# real immutable D/N identity contract.
REPLACEMENT_GENERATION = "APV2N-" + "b" * 64


# ── helpers ──────────────────────────────────────────────────────────────


def _finalized(tmp_path, *, boot=BOOT_A):
    """A committed continuation with real ordinary settlement evidence."""
    store, _, _, _, row, attempt = _admitted(tmp_path)
    _drive(store, row, attempt, "consumed_audited")
    outcome = _finalize(store, row, attempt)
    assert outcome.status == "continued", outcome
    _insert_ordinary_completion(store, row["id"])
    assert _settle(store, row).status == "settled"
    store.bind_v2_process_boot_id(boot)
    return store, row, attempt, outcome


def _finalized_recovery(tmp_path, *, boot=BOOT_A):
    """A committed continuation whose proof is the exact settled recovery Q."""
    store, _, _, _, row, attempt = _admitted(tmp_path)
    _drive(store, row, attempt, "consumed_audited")
    outcome = _finalize(store, row, attempt)
    assert outcome.status == "continued", outcome
    _seed_q(store, row["id"], state="callback_accepted")
    settled = _settle(
        store, row, recovery_session_id=SESSION_ID,
        accepted_result_id=row["id"], accepted_result_session_id=SESSION_ID,
    )
    assert settled.status == "settled", settled
    store.bind_v2_process_boot_id(boot)
    return store, row, attempt, outcome


def _notification(store, outcome):
    notification = store.get_v2_recovery_notification(outcome.notification_id)
    assert notification is not None
    return notification


def _dispatch(store):
    dispatch = store.get_v2_root_dispatch(TASK_ID)
    assert dispatch is not None
    return dispatch


def _claim(store, row, **kw):
    return store.claim_v2_notification_publication(
        root_task_id=TASK_ID, manager_agent=MANAGER,
        manager_session_id=SESSION_ID, result_id=row["id"], **kw,
    )


def _ack(store, row, *, publication_attempt, publisher_boot_id):
    return store.acknowledge_v2_notification_publication(
        root_task_id=TASK_ID, manager_agent=MANAGER,
        manager_session_id=SESSION_ID, result_id=row["id"],
        publication_attempt=publication_attempt,
        publisher_boot_id=publisher_boot_id,
    )


def _failure(store, row, *, publication_attempt, publisher_boot_id):
    return store.record_v2_notification_publication_failure(
        root_task_id=TASK_ID, manager_agent=MANAGER,
        manager_session_id=SESSION_ID, result_id=row["id"],
        publication_attempt=publication_attempt,
        publisher_boot_id=publisher_boot_id,
    )


def _invalidate(store, row):
    return store.invalidate_v2_notification_generation(
        root_task_id=TASK_ID, manager_agent=MANAGER,
        manager_session_id=SESSION_ID, result_id=row["id"],
    )


def _stage_events(store, stage):
    return [
        payload
        for payload in (
            row["payload"] for row in store._db.get_audit_logs(TASK_ID)
            if row["action"] == AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION
            and isinstance(row["payload"], dict)
        )
        if payload.get("stage") == stage
    ]


def _mutate_notification(store, notification_id, **updates):
    """Directly stage one notification column state (fixture-level only)."""
    row = store._db._conn.execute(
        "SELECT * FROM authority_policy_v2_recovery_notifications "
        "WHERE notification_id=?",
        (notification_id,),
    ).fetchone()
    notification = store._db._authority_policy_v2_notification_from_row(row)
    updated = notification.model_copy(update=updates)
    snapshot = updated.model_dump(mode="json")
    canonical = authority_policy_v2_canonical_json_bytes(snapshot).decode("utf-8")
    columns = {
        "state": snapshot["state"],
        "publication_attempt": snapshot["publication_attempt"],
        "publisher_boot_id": snapshot["publisher_boot_id"],
        "lease_deadline": snapshot["lease_deadline"],
        "next_session_id": snapshot["next_session_id"],
        "canonical_payload_json": canonical,
        "updated_at": snapshot["updated_at"],
    }
    store._db._conn.execute(
        """UPDATE authority_policy_v2_recovery_notifications
              SET state=?, publication_attempt=?, publisher_boot_id=?,
                  lease_deadline=?, next_session_id=?, canonical_payload_json=?,
                  updated_at=?
            WHERE notification_id=?""",
        (
            columns["state"], columns["publication_attempt"],
            columns["publisher_boot_id"], columns["lease_deadline"],
            columns["next_session_id"], columns["canonical_payload_json"],
            columns["updated_at"], notification_id,
        ),
    )
    store._db._conn.commit()


def _delete_audits(store, predicate):
    """Fixture-level removal of exact audit rows (evidence-loss simulation)."""
    for row in [
        row for row in store._db.get_audit_logs(TASK_ID) if predicate(row)
    ]:
        store._db._conn.execute("DELETE FROM audit_log WHERE id=?", (row["id"],))
    store._db._conn.commit()


def _delete_stage_event(store, stage):
    _delete_audits(
        store,
        lambda row: (
            row["action"] == AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION
            and isinstance(row["payload"], dict)
            and row["payload"].get("stage") == stage
        ),
    )


def _delete_ordinary_completion(store, result_id):
    _delete_audits(
        store,
        lambda row: (
            row["action"] == "completion_report"
            and isinstance(row["payload"], dict)
            and "_recovery_session_id" not in row["payload"]
            and row["payload"].get("_result_row_id") == result_id
        ),
    )


def _append_stage_event(store, payload):
    """Append one raw result-stage audit row (fixture-level conflict)."""
    store._db._conn.execute(
        """INSERT INTO audit_log (task_id, agent, action, payload, timestamp)
           VALUES (?, ?, ?, ?, ?)""",
        (
            TASK_ID, MANAGER, AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
            json.dumps(payload) if not isinstance(payload, str) else payload,
            "2026-09-20T00:00:00+00:00",
        ),
    )
    store._db._conn.commit()


def _cancel_task(store):
    store._db._conn.execute(
        "UPDATE tasks SET cancelled_at=? WHERE id=?",
        ("2026-09-20T00:00:00+00:00", TASK_ID),
    )
    store._db._conn.commit()


def _replace_owner_session(store, session_id="sess-replaced-owner"):
    store._db._conn.execute(
        "UPDATE tasks SET current_session_id=? WHERE id=?", (session_id, TASK_ID)
    )
    store._db._conn.commit()


def _write_dispatch(store, dispatch):
    snapshot = dispatch.model_dump(mode="json")
    canonical = authority_policy_v2_canonical_json_bytes(snapshot).decode("utf-8")
    store._db._conn.execute(
        """UPDATE authority_policy_v2_root_dispatch
              SET generation_id=?, envelope_id=?, state=?,
                  expected_manager_agent=?, expected_manager_session_id=?,
                  canonical_payload_json=?, updated_at=?
            WHERE root_task_id=?""",
        (
            snapshot["generation_id"], snapshot["envelope_id"], snapshot["state"],
            snapshot["expected_manager_agent"],
            snapshot["expected_manager_session_id"], canonical,
            snapshot["updated_at"], TASK_ID,
        ),
    )


def _clone_row(store, table, pk_column, source_pk, overrides):
    """Clone one fixture row under new identities (INSERT only, no updates)."""
    conn = store._db._conn
    columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    source = conn.execute(
        f"SELECT * FROM {table} WHERE {pk_column}=?", (source_pk,)
    ).fetchone()
    assert source is not None, (table, source_pk)
    values = {column: source[column] for column in columns}
    values.update(overrides)
    cursor = conn.execute(
        f"INSERT INTO {table} ({','.join(columns)}) "
        f"VALUES ({','.join('?' for _ in columns)})",
        [values[column] for column in columns],
    )
    return cursor.lastrowid


def _point_dispatch_at_replacement(
    store, notification, generation_id=None,
):
    """Fixture-level replacement root pointer (D names a REAL generation B).

    The root-dispatch FK requires the pointer to name a real notification, so a
    future replacement generation is staged by cloning the causal generation's
    result/attempt/candidate/envelope/notification rows under new identities.
    The cloned B is never authenticated as evidence; it exists only so the
    genuine forward-only retired -> pending pointer transition can be exercised.
    """
    if generation_id is None:
        generation_id = REPLACEMENT_GENERATION
    conn = store._db._conn
    envelope = conn.execute(
        "SELECT * FROM authority_policy_v2_continue_envelopes WHERE envelope_id=?",
        (notification.envelope_id,),
    ).fetchone()
    assert envelope is not None
    session_id = "sess-replacement-b"
    attempt_id = "APV2R-replacement-b"
    candidate_id = "APV2C-replacement-b"
    envelope_id = "APV2E-replacement-b"
    # B owns its own causal result row so the original result's candidate/attempt
    # lookups stay single-valued and unambiguous.
    replacement_result_id = _clone_row(
        store, "task_results", "id", notification.result_id,
        {"id": None, "session_id": session_id},
    )
    _clone_row(
        store, "authority_policy_v2_attempts", "attempt_id", envelope["attempt_id"],
        {
            "attempt_id": attempt_id, "manager_session_id": session_id,
            "result_id": replacement_result_id,
        },
    )
    _clone_row(
        store, "authority_policy_v2_candidates", "candidate_id",
        envelope["candidate_id"],
        {
            "candidate_id": candidate_id, "claim_key": "claim-replacement-b",
            "manager_session_id": session_id, "attempt_id": attempt_id,
            "result_id": replacement_result_id,
        },
    )
    _clone_row(
        store, "authority_policy_v2_continue_envelopes", "envelope_id",
        envelope["envelope_id"],
        {
            "envelope_id": envelope_id, "candidate_id": candidate_id,
            "claim_key": "claim-replacement-b", "manager_session_id": session_id,
            "attempt_id": attempt_id, "result_id": replacement_result_id,
        },
    )
    _clone_row(
        store, "authority_policy_v2_recovery_notifications", "notification_id",
        notification.notification_id,
        {
            "notification_id": generation_id, "envelope_id": envelope_id,
            "candidate_id": candidate_id, "manager_session_id": session_id,
            "result_id": replacement_result_id,
        },
    )
    row = conn.execute(
        "SELECT * FROM authority_policy_v2_root_dispatch WHERE root_task_id=?",
        (TASK_ID,),
    ).fetchone()
    dispatch = store._db._authority_policy_v2_root_dispatch_from_row(row)
    _write_dispatch(store, dispatch.model_copy(update={"state": "retired"}))
    _write_dispatch(
        store,
        dispatch.model_copy(
            update={"state": "pending", "generation_id": generation_id}
        ),
    )
    conn.commit()


class _TargetedFailingConn:
    def __init__(self, real, *, audit_stage: str | None = None, fail_commit=False):
        self._real = real
        self._audit_stage = audit_stage
        self._fail_commit = fail_commit

    def execute(self, sql, *args, **kwargs):
        params = args[0] if args else None
        if (
            self._audit_stage is not None
            and "INSERT INTO audit_log" in sql
            and isinstance(params, (tuple, list)) and len(params) >= 4
            and isinstance(params[3], str) and self._audit_stage in params[3]
        ):
            raise RuntimeError("injected publication audit failure")
        return self._real.execute(sql, *args, **kwargs)

    def commit(self):
        if self._fail_commit:
            raise RuntimeError("injected commit failure")
        return self._real.commit()

    def __getattr__(self, name):
        return getattr(self._real, name)


# ── discovery ────────────────────────────────────────────────────────────


def test_discovery_lists_pending_generation_after_finalization(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    targets = store.list_v2_publication_targets()
    assert [t.notification_id for t in targets] == [outcome.notification_id]
    target = targets[0]
    assert target.notification_id == outcome.notification_id
    assert target.state == "needed"
    assert target.root_task_id == TASK_ID
    assert target.manager_agent == MANAGER
    assert target.manager_session_id == SESSION_ID
    assert target.publication_attempt == 0
    assert target.publisher_boot_id is None
    # Discovery is read-only.
    assert _notification(store, outcome).state == "needed"


def test_discovery_lists_publishing_and_published_and_excludes_admitted(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    assert _claim(store, row).status == "claimed"
    assert [t.state for t in store.list_v2_publication_targets()] == ["publishing"]
    claimed = _notification(store, outcome)
    assert _ack(
        store, row, publication_attempt=claimed.publication_attempt,
        publisher_boot_id=claimed.publisher_boot_id,
    ).status == "published"
    assert [t.state for t in store.list_v2_publication_targets()] == ["published"]

    # A fixture-level future admitted state is no longer publishable discovery.
    _mutate_notification(
        store, outcome.notification_id, state="admitted",
        next_session_id="sess-reserved-next",
    )
    assert store.list_v2_publication_targets() == []


# ── claim ────────────────────────────────────────────────────────────────


def test_claim_cas_to_publishing_with_lease_and_closed_audit(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    before_task = store._db.get_task(TASK_ID)
    outcome_claim = _claim(store, row)
    assert outcome_claim.status == "claimed", outcome_claim
    assert outcome_claim.publication_attempt == 1
    assert outcome_claim.publisher_boot_id == BOOT_A
    assert outcome_claim.lease_deadline is not None

    notification = _notification(store, outcome)
    assert notification.state == "publishing"
    assert notification.publication_attempt == 1
    assert notification.publisher_boot_id == BOOT_A
    assert notification.lease_deadline == outcome_claim.lease_deadline
    events = _stage_events(store, "publish_claimed")
    assert len(events) == 1
    assert events[0]["publication_attempt"] == 1
    assert events[0]["publisher_boot_id"] == BOOT_A
    assert events[0]["generation_id"] == outcome.generation_id
    # No task/J/E/D/Candidate lifecycle change: this is not admission.
    after_task = store._db.get_task(TASK_ID)
    assert after_task.status is TaskStatus.PENDING
    assert after_task.status == before_task.status
    assert _dispatch(store).state == "pending"
    assert _notification(store, outcome).next_session_id is None


def test_claim_requires_bound_daemon_boot_identity(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    store.bind_v2_process_boot_id(None)
    refusal = _claim(store, row)
    assert refusal.status == "publication_pending"
    assert refusal.reason == "boot_unbound"
    assert _notification(store, outcome).state == "needed"


def test_claim_rejects_live_same_boot_lease_without_poisoning(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    assert _claim(store, row).status == "claimed"
    before = _counts(store._db)
    second = _claim(store, row)
    assert second.status == "publication_pending"
    assert second.reason == "lease_live"
    assert _counts(store._db) == before
    notification = _notification(store, outcome)
    assert notification.publication_attempt == 1
    assert notification.state == "publishing"
    assert len(_stage_events(store, "publish_claimed")) == 1


def test_claim_reclaims_after_publisher_restart(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    assert _claim(store, row).status == "claimed"
    store.bind_v2_process_boot_id(BOOT_B)
    reclaimed = _claim(store, row)
    assert reclaimed.status == "claimed", reclaimed
    assert reclaimed.publication_attempt == 2
    assert reclaimed.publisher_boot_id == BOOT_B
    assert len(_stage_events(store, "publish_claimed")) == 2


def test_claim_reclaims_after_lease_expiry(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    assert _claim(store, row).status == "claimed"
    _mutate_notification(
        store, outcome.notification_id,
        lease_deadline="2000-01-01T00:00:00+00:00",
    )
    reclaimed = _claim(store, row)
    assert reclaimed.status == "claimed", reclaimed
    assert reclaimed.publication_attempt == 2


def test_claim_refuses_attempt_overflow(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    _mutate_notification(
        store, outcome.notification_id,
        state="publishing", publication_attempt=2147483647,
    )
    before = _counts(store._db)
    refusal = _claim(store, row)
    assert refusal.status == "publication_pending"
    assert refusal.reason == "attempt_overflow"
    assert _counts(store._db) == before


def test_claim_refuses_transaction_owned_without_touching_caller(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    store._db._conn.execute("BEGIN IMMEDIATE")
    try:
        refusal = _claim(store, row)
        assert refusal.status == "publication_pending"
        assert refusal.reason == "transaction_owned"
        assert _notification(store, outcome).state == "needed"
    finally:
        store._db._conn.rollback()


def test_claim_refuses_non_pending_task_and_wrong_identity(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    store._db._conn.execute(
        "UPDATE tasks SET status=? WHERE id=?",
        (TaskStatus.IN_PROGRESS.value, TASK_ID),
    )
    store._db._conn.commit()
    before = _counts(store._db)
    refusal = _claim(store, row)
    assert refusal.status == "publication_pending"
    assert _counts(store._db) == before

    store._db._conn.execute(
        "UPDATE tasks SET status=? WHERE id=?",
        (TaskStatus.PENDING.value, TASK_ID),
    )
    store._db._conn.commit()
    wrong = store.claim_v2_notification_publication(
        root_task_id=TASK_ID, manager_agent=MANAGER,
        manager_session_id="sess-wrong", result_id=row["id"],
    )
    assert wrong.status == "publication_pending"
    assert _notification(store, outcome).state == "needed"


# ── acknowledgement / failure ────────────────────────────────────────────


def test_acknowledge_published_exact_retry_is_read_only(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    claimed = _claim(store, row)
    first = _ack(
        store, row, publication_attempt=claimed.publication_attempt,
        publisher_boot_id=claimed.publisher_boot_id,
    )
    assert first.status == "published", first
    assert first.state == "published"
    assert len(_stage_events(store, "published")) == 1
    before = _counts(store._db)
    retry = _ack(
        store, row, publication_attempt=claimed.publication_attempt,
        publisher_boot_id=claimed.publisher_boot_id,
    )
    assert retry.status == "published", retry
    assert _counts(store._db) == before
    assert len(_stage_events(store, "published")) == 1


def test_stale_claim_cannot_acknowledge_newer_generation(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    first = _claim(store, row)
    store.bind_v2_process_boot_id(BOOT_B)
    second = _claim(store, row)
    assert second.publication_attempt == 2
    stale = _ack(
        store, row, publication_attempt=first.publication_attempt,
        publisher_boot_id=first.publisher_boot_id,
    )
    assert stale.status == "ack_pending"
    assert stale.reason == "stale_claim"
    notification = _notification(store, outcome)
    assert notification.publication_attempt == 2
    assert notification.publisher_boot_id == BOOT_B
    ok = _ack(
        store, row, publication_attempt=second.publication_attempt,
        publisher_boot_id=second.publisher_boot_id,
    )
    assert ok.status == "published"


def test_acknowledge_without_claim_refuses(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    before = _counts(store._db)
    refusal = _ack(store, row, publication_attempt=1, publisher_boot_id=BOOT_A)
    assert refusal.status == "ack_pending"
    assert refusal.reason == "stale_claim"
    assert _counts(store._db) == before


def test_acknowledge_refuses_staged_admitted_without_admission_evidence(tmp_path):
    """Fixture-level staged admitted state: the real admission producer does not
    exist yet, so the acknowledgement must refuse without regressing the row."""
    store, row, attempt, outcome = _finalized(tmp_path)
    claimed = _claim(store, row)
    assert _ack(
        store, row, publication_attempt=claimed.publication_attempt,
        publisher_boot_id=claimed.publisher_boot_id,
    ).status == "published"
    _mutate_notification(
        store, outcome.notification_id, state="admitted",
        next_session_id="sess-reserved-next",
    )
    before = _counts(store._db)
    refusal = _ack(
        store, row, publication_attempt=claimed.publication_attempt,
        publisher_boot_id=claimed.publisher_boot_id,
    )
    assert refusal.status == "ack_pending"
    assert refusal.reason == "admission_evidence_missing"
    assert _counts(store._db) == before
    assert _notification(store, outcome).state == "admitted"
    assert len(_stage_events(store, "published")) == 1
    assert _stage_events(store, "publish_returned") == []


def test_publication_failure_records_bounded_reclaimable_state(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    claimed = _claim(store, row)
    recorded = _failure(
        store, row, publication_attempt=claimed.publication_attempt,
        publisher_boot_id=claimed.publisher_boot_id,
    )
    assert recorded.status == "failure_recorded", recorded
    assert recorded.state == "publishing"
    notification = _notification(store, outcome)
    assert notification.state == "publishing"
    assert notification.publication_attempt == 1
    assert notification.publisher_boot_id is None
    assert notification.lease_deadline is None
    assert len(_stage_events(store, "publish_failed")) == 1

    # Exact read-only replay.
    before = _counts(store._db)
    replay = _failure(
        store, row, publication_attempt=claimed.publication_attempt,
        publisher_boot_id=claimed.publisher_boot_id,
    )
    assert replay.status == "failure_recorded"
    assert _counts(store._db) == before

    # The retry state is safely reclaimable by any publisher.
    reclaimed = _claim(store, row)
    assert reclaimed.status == "claimed"
    assert reclaimed.publication_attempt == 2


def test_publication_failure_rolls_back_and_prior_lease_stays_reclaimable(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    claimed = _claim(store, row)
    before = _counts(store._db)
    real = store._db._conn
    store._db._conn = _TargetedFailingConn(real, audit_stage="publish_failed")
    try:
        failed = _failure(
            store, row, publication_attempt=claimed.publication_attempt,
            publisher_boot_id=claimed.publisher_boot_id,
        )
    finally:
        store._db._conn = real
    assert failed.status == "failure_pending"
    assert failed.reason == "failure_failed"
    notification = _notification(store, outcome)
    assert notification.state == "publishing"
    assert notification.publisher_boot_id == BOOT_A
    assert notification.lease_deadline == claimed.lease_deadline
    assert _counts(store._db) == before
    assert _stage_events(store, "publish_failed") == []


# ── invalidation ─────────────────────────────────────────────────────────


def test_invalidate_needed_generation_retires_exact_dispatch(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    before_task = store._db.get_task(TASK_ID)
    # A healthy exact causal owner/pointer is NOT invalidatable.
    healthy = _invalidate(store, row)
    assert healthy.status == "invalidation_pending"
    assert healthy.reason == "not_invalidatable"
    assert _notification(store, outcome).state == "needed"
    assert _dispatch(store).state == "pending"
    # A real permitted cause (cancelled causal task) permits exact invalidation.
    _cancel_task(store)
    invalidated = _invalidate(store, row)
    assert invalidated.status == "invalidated", invalidated
    assert invalidated.notification_state == "invalidated"
    assert invalidated.dispatch_state == "retired"
    assert _notification(store, outcome).state == "invalidated"
    assert _dispatch(store).state == "retired"
    assert len(_stage_events(store, "invalidated")) == 1
    # No task/ownership mutation and no replacement generation.
    assert store._db.get_task(TASK_ID).status == before_task.status
    assert _dispatch(store).generation_id == outcome.generation_id

    replay = _invalidate(store, row)
    assert replay.status == "already_invalidated"
    assert store.list_v2_publication_targets() == []


def test_invalidate_publishing_generation_is_terminal_and_not_publishable(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    claimed = _claim(store, row)
    healthy = _invalidate(store, row)
    assert healthy.status == "invalidation_pending"
    assert healthy.reason == "not_invalidatable"
    assert _notification(store, outcome).state == "publishing"
    _cancel_task(store)
    invalidated = _invalidate(store, row)
    assert invalidated.status == "invalidated"
    after = _claim(store, row)
    assert after.status == "publication_pending"
    assert after.reason in ("not_publishable", "evidence_drift")
    assert _ack(
        store, row, publication_attempt=claimed.publication_attempt,
        publisher_boot_id=claimed.publisher_boot_id,
    ).status == "ack_pending"
    assert _notification(store, outcome).state == "invalidated"


def test_invalidation_refuses_transaction_owned(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    store._db._conn.execute("BEGIN IMMEDIATE")
    try:
        refusal = _invalidate(store, row)
        assert refusal.status == "invalidation_pending"
        assert refusal.reason == "transaction_owned"
        assert _notification(store, outcome).state == "needed"
    finally:
        store._db._conn.rollback()


def test_invalidation_audit_failure_rolls_back_notification_and_dispatch(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    _cancel_task(store)
    before = _counts(store._db)
    real = store._db._conn
    store._db._conn = _TargetedFailingConn(real, audit_stage="invalidated")
    try:
        failed = _invalidate(store, row)
    finally:
        store._db._conn = real
    assert failed.status == "invalidation_pending"
    assert failed.reason == "invalidation_failed"
    assert _notification(store, outcome).state == "needed"
    assert _dispatch(store).state == "pending"
    assert _counts(store._db) == before
    assert _stage_events(store, "invalidated") == []


# ── contention / reopen ──────────────────────────────────────────────────


def test_two_connections_one_claim_winner_no_loser_poisoning(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    assert _claim(store, row).status == "claimed"

    other = AuthorityPolicyStore(Database(tmp_path / "c2.db"))
    other.bind_v2_process_boot_id(BOOT_B)
    before = _counts(store._db)
    loser = other.claim_v2_notification_publication(
        root_task_id=TASK_ID, manager_agent=MANAGER,
        manager_session_id=SESSION_ID, result_id=row["id"],
    )
    # BOOT_B differs from the live publisher boot, so the second connection is a
    # legitimate restart reclaim; the winner's audit stays intact either way.
    assert loser.status in ("claimed", "publication_pending")
    assert len(_stage_events(store, "publish_claimed")) >= 1
    if loser.status == "publication_pending":
        assert _counts(store._db) == before


def test_reopen_authenticates_publication_and_invalidation(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    claimed = _claim(store, row)
    assert _ack(
        store, row, publication_attempt=claimed.publication_attempt,
        publisher_boot_id=claimed.publisher_boot_id,
    ).status == "published"

    reopened = _reopen(tmp_path)
    again = reopened.acknowledge_v2_notification_publication(
        root_task_id=TASK_ID, manager_agent=MANAGER,
        manager_session_id=SESSION_ID, result_id=row["id"],
        publication_attempt=claimed.publication_attempt,
        publisher_boot_id=claimed.publisher_boot_id,
    )
    assert again.status == "published", again
    _cancel_task(reopened)
    invalidated = reopened.invalidate_v2_notification_generation(
        root_task_id=TASK_ID, manager_agent=MANAGER,
        manager_session_id=SESSION_ID, result_id=row["id"],
    )
    assert invalidated.status == "invalidated"


# ── C3d3a correction: settlement proof before publication ────────────────


def test_claim_refuses_deleted_ordinary_completion(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    _delete_ordinary_completion(store, row["id"])
    before = _counts(store._db)
    refusal = _claim(store, row)
    assert refusal.status == "publication_pending"
    assert refusal.reason == "evidence_drift"
    assert _counts(store._db) == before
    assert _notification(store, outcome).state == "needed"
    assert _stage_events(store, "publish_claimed") == []


def test_ack_refuses_completion_deleted_after_claim(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    claimed = _claim(store, row)
    _delete_ordinary_completion(store, row["id"])
    before = _counts(store._db)
    ack = _ack(
        store, row, publication_attempt=claimed.publication_attempt,
        publisher_boot_id=claimed.publisher_boot_id,
    )
    assert ack.status == "ack_pending"
    assert ack.reason == "evidence_drift"
    assert _counts(store._db) == before
    assert _notification(store, outcome).state == "publishing"
    assert _stage_events(store, "published") == []


def test_failure_recording_refuses_completion_deleted(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    claimed = _claim(store, row)
    _delete_ordinary_completion(store, row["id"])
    before = _counts(store._db)
    failed = _failure(
        store, row, publication_attempt=claimed.publication_attempt,
        publisher_boot_id=claimed.publisher_boot_id,
    )
    assert failed.status == "failure_pending"
    assert failed.reason == "evidence_drift"
    assert _counts(store._db) == before
    assert _notification(store, outcome).state == "publishing"
    assert _stage_events(store, "publish_failed") == []


# ── C3d3a correction: retained publication-stage evidence ────────────────


def test_claim_refuses_deleted_retained_claim_audit(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    first = _claim(store, row)
    _delete_stage_event(store, "publish_claimed")
    store.bind_v2_process_boot_id(BOOT_B)
    before = _counts(store._db)
    refusal = _claim(store, row)
    assert refusal.status == "publication_pending"
    assert refusal.reason == "evidence_drift"
    assert _counts(store._db) == before
    notification = _notification(store, outcome)
    assert notification.publication_attempt == first.publication_attempt
    assert notification.publisher_boot_id == BOOT_A
    assert notification.lease_deadline == first.lease_deadline


def test_failure_recording_refuses_deleted_retained_claim_audit(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    claimed = _claim(store, row)
    _delete_stage_event(store, "publish_claimed")
    before = _counts(store._db)
    failed = _failure(
        store, row, publication_attempt=claimed.publication_attempt,
        publisher_boot_id=claimed.publisher_boot_id,
    )
    assert failed.status == "failure_pending"
    assert failed.reason == "evidence_drift"
    assert _counts(store._db) == before
    notification = _notification(store, outcome)
    assert notification.publisher_boot_id == BOOT_A
    assert notification.lease_deadline == claimed.lease_deadline
    assert _stage_events(store, "publish_failed") == []


def test_null_lease_without_audited_failure_is_not_reclaimable(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    claimed = _claim(store, row)
    # A cleared lease/boot WITHOUT the required publish_failed audit is not proof.
    _mutate_notification(
        store, outcome.notification_id,
        publisher_boot_id=None, lease_deadline=None,
    )
    before = _counts(store._db)
    refusal = _claim(store, row)
    assert refusal.status == "publication_pending"
    assert refusal.reason == "evidence_drift"
    assert _counts(store._db) == before
    assert _notification(store, outcome).publication_attempt == 1


# ── C3d3a correction: reader classifies conflicts before filtering ───────


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_generation",
        "missing_generation",
        "null_generation",
        "null_publication_attempt",
        "missing_publication_attempt",
        "null_attempt_id",
        "missing_attempt_id",
        "distinct_attempt_id",
        "opaque_body",
    ],
)
def test_ack_refuses_conflicting_claim_evidence(tmp_path, mutation):
    store, row, attempt, outcome = _finalized(tmp_path)
    claimed = _claim(store, row)
    conflicting = dict(_stage_events(store, "publish_claimed")[0])
    if mutation == "wrong_generation":
        conflicting["generation_id"] = "APV2N-deadbeef"
        _append_stage_event(store, conflicting)
    elif mutation == "missing_generation":
        conflicting.pop("generation_id")
        _append_stage_event(store, conflicting)
    elif mutation == "null_generation":
        conflicting["generation_id"] = None
        _append_stage_event(store, conflicting)
    elif mutation == "null_publication_attempt":
        conflicting["publication_attempt"] = None
        _append_stage_event(store, conflicting)
    elif mutation == "missing_publication_attempt":
        conflicting.pop("publication_attempt")
        _append_stage_event(store, conflicting)
    elif mutation == "null_attempt_id":
        conflicting["attempt_id"] = None
        _append_stage_event(store, conflicting)
    elif mutation == "missing_attempt_id":
        conflicting.pop("attempt_id")
        _append_stage_event(store, conflicting)
    elif mutation == "distinct_attempt_id":
        conflicting["attempt_id"] = "APV2R-conflicting-attempt"
        _append_stage_event(store, conflicting)
    else:
        _append_stage_event(store, "[]")
    before = _counts(store._db)
    ack = _ack(
        store, row, publication_attempt=claimed.publication_attempt,
        publisher_boot_id=claimed.publisher_boot_id,
    )
    assert ack.status == "ack_pending"
    assert ack.reason == "evidence_drift"
    assert _counts(store._db) == before
    assert _notification(store, outcome).state == "publishing"
    assert _stage_events(store, "published") == []


# ── C3d3a correction: invalidation requires an established cause ─────────


@pytest.mark.parametrize("stage", ["needed", "publishing", "published"])
def test_invalidation_refuses_healthy_exact_owner(tmp_path, stage):
    store, row, attempt, outcome = _finalized(tmp_path)
    claimed = None
    if stage in ("publishing", "published"):
        claimed = _claim(store, row)
    if stage == "published":
        assert _ack(
            store, row, publication_attempt=claimed.publication_attempt,
            publisher_boot_id=claimed.publisher_boot_id,
        ).status == "published"
    before = _counts(store._db)
    refusal = _invalidate(store, row)
    assert refusal.status == "invalidation_pending"
    assert refusal.reason == "not_invalidatable"
    assert _counts(store._db) == before
    assert _notification(store, outcome).state == stage
    assert _dispatch(store).state == "pending"
    assert _stage_events(store, "invalidated") == []


@pytest.mark.parametrize("field", ["assigned_agent", "current_session_id"])
def test_invalidation_refuses_null_current_identity(tmp_path, field):
    """A nulled owner/session field alone is NOT affirmative replacement proof."""
    store, row, attempt, outcome = _finalized(tmp_path)
    store._db._conn.execute(
        f"UPDATE tasks SET {field}=NULL WHERE id=?", (TASK_ID,)
    )
    store._db._conn.commit()
    before = _counts(store._db)
    refusal = _invalidate(store, row)
    assert refusal.status == "invalidation_pending"
    assert refusal.reason == "not_invalidatable"
    assert _counts(store._db) == before
    assert _notification(store, outcome).state == "needed"
    assert _dispatch(store).state == "pending"
    assert _stage_events(store, "invalidated") == []


@pytest.mark.parametrize("cause", ["cancelled_task", "replaced_owner", "replacement_pointer"])
def test_invalidation_established_cause(tmp_path, cause):
    store, row, attempt, outcome = _finalized(tmp_path)
    if cause == "cancelled_task":
        _cancel_task(store)
    elif cause == "replaced_owner":
        _replace_owner_session(store)
    else:
        _point_dispatch_at_replacement(store, _notification(store, outcome))
    invalidated = _invalidate(store, row)
    assert invalidated.status == "invalidated", invalidated
    assert _notification(store, outcome).state == "invalidated"
    dispatch = _dispatch(store)
    if cause == "replacement_pointer":
        assert dispatch.generation_id == REPLACEMENT_GENERATION
        assert dispatch.state == "pending"
    else:
        assert dispatch.state == "retired"
    replay = _invalidate(store, row)
    assert replay.status == "already_invalidated"


def test_invalidation_not_permitted_by_publication_failure_alone(tmp_path):
    store, row, attempt, outcome = _finalized(tmp_path)
    claimed = _claim(store, row)
    assert _failure(
        store, row, publication_attempt=claimed.publication_attempt,
        publisher_boot_id=claimed.publisher_boot_id,
    ).status == "failure_recorded"
    before = _counts(store._db)
    refusal = _invalidate(store, row)
    assert refusal.status == "invalidation_pending"
    assert refusal.reason == "not_invalidatable"
    assert _counts(store._db) == before
    assert _notification(store, outcome).state == "publishing"


# ── C3d3a correction: reclaim/failure replay classify conflicting claims ──


def test_reclaim_refuses_conflicting_claim_attempt(tmp_path):
    """A duplicate retained claim with a null attempt id cannot be filtered out."""
    store, row, attempt, outcome = _finalized(tmp_path)
    first = _claim(store, row)
    conflicting = dict(_stage_events(store, "publish_claimed")[0])
    conflicting["attempt_id"] = None
    _append_stage_event(store, conflicting)
    store.bind_v2_process_boot_id(BOOT_B)
    before = _counts(store._db)
    refusal = _claim(store, row)
    assert refusal.status == "publication_pending"
    assert refusal.reason == "evidence_drift"
    assert _counts(store._db) == before
    notification = _notification(store, outcome)
    assert notification.publication_attempt == first.publication_attempt
    assert notification.publisher_boot_id == BOOT_A
    assert _stage_events(store, "publish_claimed") != []


def test_failure_replay_refuses_conflicting_claim_attempt(tmp_path):
    """The exact failure replay also authenticates the retained claim set."""
    store, row, attempt, outcome = _finalized(tmp_path)
    claimed = _claim(store, row)
    assert _failure(
        store, row, publication_attempt=claimed.publication_attempt,
        publisher_boot_id=claimed.publisher_boot_id,
    ).status == "failure_recorded"
    conflicting = dict(_stage_events(store, "publish_claimed")[0])
    conflicting["attempt_id"] = "APV2R-conflicting-attempt"
    _append_stage_event(store, conflicting)
    before = _counts(store._db)
    replay = _failure(
        store, row, publication_attempt=claimed.publication_attempt,
        publisher_boot_id=claimed.publisher_boot_id,
    )
    assert replay.status == "failure_pending"
    assert replay.reason == "evidence_drift"
    assert _counts(store._db) == before
    assert _notification(store, outcome).state == "publishing"


# ── C3d3a correction: settlement proof cannot be hidden by ordinary evidence ──


def test_claim_refuses_ordinary_with_related_nonterminal_receipt(tmp_path):
    """A genuine ordinary completion plus a related accepted Q still refuses.

    The ordinary branch is available only while no potentially related receipt
    blocks it, so replayed ordinary evidence cannot hide an unsettled Q.
    """
    store, row, attempt, outcome = _finalized(tmp_path)
    _seed_q(store, row["id"], state="callback_accepted")
    before = _counts(store._db)
    refusal = _claim(store, row)
    assert refusal.status == "publication_pending"
    assert refusal.reason == "evidence_drift"
    assert _counts(store._db) == before
    assert _notification(store, outcome).state == "needed"
    assert _stage_events(store, "publish_claimed") == []


def test_claim_refuses_ordinary_with_conflicting_related_receipt(tmp_path):
    """A related Q whose accepted result/session conflicts is never hidden."""
    store, row, attempt, outcome = _finalized(tmp_path)
    _seed_q(
        store, row["id"], state="callback_consumed",
        accepted_result_session="sess-other",
    )
    before = _counts(store._db)
    refusal = _claim(store, row)
    assert refusal.status == "publication_pending"
    assert refusal.reason == "evidence_drift"
    assert _counts(store._db) == before
    assert _notification(store, outcome).state == "needed"


def test_claim_allows_unrelated_established_terminal_receipt(tmp_path):
    """An unrelated ESTABLISHED TERMINAL receipt never vetoes ordinary proof."""
    store, row, attempt, outcome = _finalized(tmp_path)
    _seed_q(
        store, row["id"], state="callback_consumed",
        recovery_session="sess-unrelated",
        accepted_result_id=row["id"] + 999,
        accepted_result_session="sess-other",
    )
    claimed = _claim(store, row)
    assert claimed.status == "claimed", claimed
    assert _notification(store, outcome).state == "publishing"


# ── C3d3a correction: the exact recovery proof rejects other conflicts ────


def test_claim_allows_exact_recovery_settlement(tmp_path):
    """The exact real callback_consumed Q plus both settlement audits publishes."""
    store, row, attempt, outcome = _finalized_recovery(tmp_path)
    _seed_q(
        store, row["id"], state="callback_consumed",
        recovery_session="sess-unrelated",
        accepted_result_id=row["id"] + 999,
        accepted_result_session="sess-other",
        origin_session="sess-unrelated-origin",
    )
    claimed = _claim(store, row)
    assert claimed.status == "claimed", claimed
    assert _notification(store, outcome).state == "publishing"


def test_claim_refuses_recovery_with_second_related_receipt(tmp_path):
    """An extra related receipt alongside the exact Q is a conflict, not hidden."""
    store, row, attempt, outcome = _finalized_recovery(tmp_path)
    _seed_q(
        store, row["id"], state="callback_consumed",
        recovery_session="sess-related-other",
        accepted_result_id=row["id"],
        accepted_result_session="sess-related-other",
        origin_session="sess-related-origin-consumed",
    )
    before = _counts(store._db)
    refusal = _claim(store, row)
    assert refusal.status == "publication_pending"
    assert refusal.reason == "evidence_drift"
    assert _counts(store._db) == before
    assert _notification(store, outcome).state == "needed"
    assert _stage_events(store, "publish_claimed") == []


def test_claim_refuses_recovery_with_unsettled_related_receipt(tmp_path):
    """A related nonterminal Q alongside the exact Q still refuses."""
    store, row, attempt, outcome = _finalized_recovery(tmp_path)
    _seed_q(
        store, row["id"], state="callback_accepted",
        recovery_session="sess-related-other",
        accepted_result_id=row["id"],
        accepted_result_session="sess-related-other",
        origin_session="sess-related-origin-accepted",
    )
    before = _counts(store._db)
    refusal = _claim(store, row)
    assert refusal.status == "publication_pending"
    assert refusal.reason == "evidence_drift"
    assert _counts(store._db) == before
    assert _notification(store, outcome).state == "needed"
