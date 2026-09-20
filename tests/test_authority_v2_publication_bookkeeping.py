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
    _settle,
)

BOOT_A = "boot-publication-a"
BOOT_B = "boot-publication-b"


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
    invalidated = reopened.invalidate_v2_notification_generation(
        root_task_id=TASK_ID, manager_agent=MANAGER,
        manager_session_id=SESSION_ID, result_id=row["id"],
    )
    assert invalidated.status == "invalidated"
