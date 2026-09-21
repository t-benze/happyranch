"""THR-229 checkpoint C3d4b — post-final recovery/startup reconciliation.

Focused regressions against the ACTUAL production callers
(``run_step._consume_accepted_completion_recovery`` and
``daemon.__main__._sweep_on_startup``), driving the REAL accepted
finalize/settle/publish storage seams (no copied logic and no helper-only
lookalikes):

  * accepted exact Q settles through the existing writer, then the exact
    pending generation is discovered/claimed/raw-queued/acknowledged once;
  * an already ``callback_consumed`` Q authenticates read-only and publishes;
  * a genuine ordinary continuation (no Q) settles on real completion evidence;
  * missing/conflicting settlement proof refuses with the prior residue, makes
    ZERO queue calls and never runs the ordinary decision body;
  * a lost in-memory queue after a committed ``published`` is rediscovered on a
    restarted boot; a same-boot live lease is never stolen;
  * a real queue-failure seam records the bounded audited failure, preserves the
    committed settlement rows and permits exactly one safe retry;
  * ``_sweep_on_startup`` publishes both the accepted-recovery and the ordinary
    finalized continuations, and the startup pass requests FULL coverage.

The broad integration suite is SKIPPED under founder THR-243 seq42, never PASS.
"""
from __future__ import annotations

import types

from runtime.models import AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION, TaskStatus
from runtime.orchestrator.authority import (
    POST_FINAL_NOT_FINALIZED,
    POST_FINAL_RECONCILED,
    POST_FINAL_SETTLEMENT_REFUSED,
    reconcile_authority_policy_v2_post_final,
)
from runtime.orchestrator.orchestrator import completion_report_from_result_row
from runtime.orchestrator.run_step import _consume_accepted_completion_recovery
from tests.test_authority_v2_finalization_settlement import (
    MANAGER,
    SESSION_ID,
    TASK_ID,
    _admitted,
    _counts,
    _drive,
    _finalize,
    _insert_ordinary_completion,
    _q,
    _seed_q,
    _settle,
)
from tests.test_authority_v2_generation_admission import (
    _RecordingQueue,
    _admit,
    _settle as _settle_generation,
)
from tests.test_authority_v2_publication_bookkeeping import (
    BOOT_A,
    BOOT_B,
    _dispatch,
    _finalized,
    _finalized_recovery,
    _notification,
    _stage_events,
)

SLUG = "test-org"


def _orch(store, queue):
    return types.SimpleNamespace(_db=store._db, _queue=queue, _slug=SLUG)


def _accepted_finalized(tmp_path, *, boot=BOOT_A):
    """Finalized continuation with a real ``callback_accepted`` recovery Q."""
    store, _, _, _, row, attempt = _admitted(tmp_path)
    _drive(store, row, attempt, "consumed_audited")
    outcome = _finalize(store, row, attempt)
    assert outcome.status == "continued", outcome
    _seed_q(store, row["id"], state="callback_accepted")
    store.bind_v2_process_boot_id(boot)
    return store, row, attempt, outcome


def _report(row):
    return completion_report_from_result_row(TASK_ID, row, fallback_agent=MANAGER)


def _dump(store):
    return "\n".join(store._db._conn.iterdump())


# ── actual accepted-recovery caller ──────────────────────────────────────


def test_accepted_causal_recovery_settles_and_publishes_once(tmp_path):
    store, row, _attempt, outcome = _accepted_finalized(tmp_path)
    db = store._db
    queue = _RecordingQueue()
    before_task = db.get_task(TASK_ID)
    assert before_task.status is TaskStatus.PENDING

    _consume_accepted_completion_recovery(
        _orch(store, queue), TASK_ID, _report(row),
        agent=MANAGER, session_id=SESSION_ID, result_row_id=row["id"],
    )

    # Exact recovery settlement through the EXISTING writer.
    q = _q(store)
    assert q["state"] == "callback_consumed"
    assert q["accepted_result_id"] == row["id"]
    # The exact pending generation was published once with the tagged metadata.
    assert _notification(store, outcome).state == "published"
    assert len(_stage_events(store, "published")) == 1
    assert queue.items == [(
        SLUG, TASK_ID,
        {"authority_v2_generation": outcome.notification_id, "publication_attempt": 1},
    )]
    # Still Pending: this is bookkeeping only, no ordinary effect / step.
    after_task = db.get_task(TASK_ID)
    assert after_task.status is TaskStatus.PENDING
    assert after_task.orchestration_step_count == before_task.orchestration_step_count
    assert _dispatch(store).state == "pending"


def test_consumed_causal_recovery_publishes_without_second_transition(tmp_path):
    store, row, _attempt, outcome = _finalized_recovery(tmp_path)
    queue = _RecordingQueue()
    counts_before = _counts(store._db)
    q_before = dict(_q(store))

    _consume_accepted_completion_recovery(
        _orch(store, queue), TASK_ID, _report(row),
        agent=MANAGER, session_id=SESSION_ID, result_row_id=row["id"],
    )

    # Already ``callback_consumed``: authenticate read-only, no new settlement
    # audits or allocation rows (only the publication audits are appended).
    assert dict(_q(store)) == q_before
    assert {
        key: value for key, value in _counts(store._db).items() if key != "audit"
    } == {
        key: value for key, value in counts_before.items() if key != "audit"
    }
    # ... but publication is independent of the Q transition.
    assert _notification(store, outcome).state == "published"
    assert len(queue.items) == 1
    assert queue.items[0][2]["authority_v2_generation"] == outcome.notification_id


def test_ordinary_finalized_recovery_settles_and_publishes(tmp_path):
    store, row, _attempt, outcome = _finalized(tmp_path)
    queue = _RecordingQueue()
    before = _dump(store)

    status = reconcile_authority_policy_v2_post_final(
        _orch(store, queue), root_task_id=TASK_ID,
    )

    assert status == POST_FINAL_RECONCILED
    assert _notification(store, outcome).state == "published"
    assert len(queue.items) == 1
    # Ordinary settlement wrote its real completion evidence (dump changed).
    assert _dump(store) != before


def test_missing_settlement_proof_refuses_with_prior_residue(tmp_path):
    store, row, _attempt, outcome = _accepted_finalized(tmp_path)
    db = store._db
    # Remove the recovery receipt entirely: no genuine Q and no ordinary
    # completion evidence exists, so neither settlement branch is authentic.
    db._conn.execute(
        "DELETE FROM task_completion_recoveries WHERE task_id=? AND agent=?",
        (TASK_ID, MANAGER),
    )
    db._conn.commit()
    queue = _RecordingQueue()
    before = _dump(store)

    status = reconcile_authority_policy_v2_post_final(
        _orch(store, queue), root_task_id=TASK_ID,
    )

    assert status == POST_FINAL_SETTLEMENT_REFUSED
    assert queue.items == []
    assert _notification(store, outcome).state == "needed"
    assert db.get_task(TASK_ID).status is TaskStatus.PENDING
    assert _dump(store) == before


def test_conflicting_recovery_receipts_refuse(tmp_path):
    store, row, _attempt, outcome = _accepted_finalized(tmp_path)
    db = store._db
    # A second, conflicting receipt for the same root/manager refuses read-only.
    _seed_q(
        store, row["id"], state="callback_accepted",
        recovery_session="sess-conflicting-owner",
        accepted_result_session="sess-conflicting-owner",
        origin_session="sess-origin-conflict",
    )
    queue = _RecordingQueue()
    before = _dump(store)
    q_before = [dict(r) for r in db._conn.execute(
        "SELECT * FROM task_completion_recoveries WHERE task_id=? AND agent=?",
        (TASK_ID, MANAGER),
    ).fetchall()]

    status = reconcile_authority_policy_v2_post_final(
        _orch(store, queue), root_task_id=TASK_ID,
    )

    assert status == POST_FINAL_SETTLEMENT_REFUSED
    assert queue.items == []
    q_after = [dict(r) for r in db._conn.execute(
        "SELECT * FROM task_completion_recoveries WHERE task_id=? AND agent=?",
        (TASK_ID, MANAGER),
    ).fetchall()]
    assert q_after == q_before
    assert _dump(store) == before


def test_non_finalized_root_is_not_touched(tmp_path):
    store, _a, _b, _c, row, attempt = _admitted(tmp_path)
    _drive(store, row, attempt, "consumed_audited")
    queue = _RecordingQueue()
    before = _dump(store)

    status = reconcile_authority_policy_v2_post_final(
        _orch(store, queue), root_task_id=TASK_ID,
    )

    assert status == POST_FINAL_NOT_FINALIZED
    assert queue.items == []
    assert _dump(store) == before


# ── restart / lost-queue / lease semantics ───────────────────────────────


def test_lost_queue_after_published_republishes_on_new_boot(tmp_path):
    store, _row, _attempt, outcome = _finalized_recovery(tmp_path)
    # First boot publishes, then the in-memory queue is lost on restart.
    first = _RecordingQueue()
    reconcile_authority_policy_v2_post_final(
        _orch(store, first), root_task_id=TASK_ID,
    )
    assert len(first.items) == 1
    assert _notification(store, outcome).publication_attempt == 1

    # Restarted process: a genuinely new boot marker re-discovers the pending(G)
    # at ``published`` and reclaims it exactly once (not the same live lease).
    store.bind_v2_process_boot_id(BOOT_B)
    second = _RecordingQueue()
    reconcile_authority_policy_v2_post_final(
        _orch(store, second), root_task_id=TASK_ID,
    )
    assert len(second.items) == 1
    assert second.items[0][2]["publication_attempt"] == 2
    assert _notification(store, outcome).state == "published"
    # The settled recovery receipt is unchanged by rediscovery.
    assert _q(store)["state"] == "callback_consumed"


def test_same_boot_live_lease_is_not_stolen(tmp_path):
    store, row, _attempt, outcome = _finalized_recovery(tmp_path)
    first = _RecordingQueue()
    reconcile_authority_policy_v2_post_final(
        _orch(store, first), root_task_id=TASK_ID,
    )
    assert len(first.items) == 1
    # Same bound boot, unexpired 30s lease: a second pass makes zero queue calls
    # and mutates nothing (the settlement is already committed).
    second = _RecordingQueue()
    before = _dump(store)
    reconcile_authority_policy_v2_post_final(
        _orch(store, second), root_task_id=TASK_ID,
    )
    assert second.items == []
    assert _notification(store, outcome).publication_attempt == 1
    assert _dump(store) == before


def test_queue_failure_seam_preserves_settlement_and_exact_retry(tmp_path):
    store, _row, _attempt, outcome = _finalized_recovery(tmp_path)
    failing = _RecordingQueue(fail=True)

    status = reconcile_authority_policy_v2_post_final(
        _orch(store, failing), root_task_id=TASK_ID,
    )
    assert status == POST_FINAL_RECONCILED  # settlement committed
    assert failing.items == []
    notification = _notification(store, outcome)
    assert notification.state == "publishing"
    assert notification.publisher_boot_id is None
    assert notification.lease_deadline is None
    assert len(_stage_events(store, "publish_failed")) == 1
    # The committed settlement survives the queue failure.
    assert _q(store)["state"] == "callback_consumed"

    # A restart safely reclaims and publishes exactly once.
    store.bind_v2_process_boot_id(BOOT_B)
    retry = _RecordingQueue()
    reconcile_authority_policy_v2_post_final(
        _orch(store, retry), root_task_id=TASK_ID,
    )
    assert len(retry.items) == 1
    assert retry.items[0][2]["publication_attempt"] == 2
    assert _notification(store, outcome).state == "published"


# ── actual startup sweep caller ──────────────────────────────────────────


def test_sweep_on_startup_publishes_accepted_recovery_root(tmp_path):
    from runtime.daemon.__main__ import _sweep_on_startup

    store, _row, _attempt, outcome = _accepted_finalized(tmp_path)
    queue = _RecordingQueue()
    orch = _orch(store, queue)

    _sweep_on_startup(store._db, queue, SLUG, orch)

    # The accepted-recovery branch settled and published before its `continue`.
    assert _q(store)["state"] == "callback_consumed"
    assert _notification(store, outcome).state == "published"
    assert any(
        meta and meta.get("authority_v2_generation") == outcome.notification_id
        for _slug, _task, meta in queue.items
    )


def test_sweep_on_startup_publishes_ordinary_finalized_root(tmp_path):
    from runtime.daemon.__main__ import _sweep_on_startup

    store, _row, _attempt, outcome = _finalized(tmp_path)
    queue = _RecordingQueue()
    orch = _orch(store, queue)

    _sweep_on_startup(store._db, queue, SLUG, orch)

    assert _notification(store, outcome).state == "published"
    assert queue.items == [(
        SLUG, TASK_ID,
        {"authority_v2_generation": outcome.notification_id, "publication_attempt": 1},
    )]


def test_startup_publication_requests_full_coverage(monkeypatch, tmp_path):
    """The startup pass must request EVERY eligible root (limit=None)."""
    from runtime.daemon import __main__ as daemon_main
    import runtime.orchestrator.authority as authority

    captured: dict = {}

    def _fake_publisher(orch, queue, *, limit=32, root_task_id=None):
        captured["limit"] = limit
        captured["root_task_id"] = root_task_id
        return []

    monkeypatch.setattr(
        authority, "publish_authority_policy_v2_notifications", _fake_publisher,
    )
    org = types.SimpleNamespace(
        slug=SLUG, orchestrator=types.SimpleNamespace(), orchestrator_present=True,
    )
    daemon_main._publish_v2_generations_on_startup(org, _RecordingQueue())
    assert captured == {"limit": None, "root_task_id": None}


# ── exact post-final outcome authentication (C3d4b correction A) ─────────


def _raw_delete(store, sql, params=(), *, drop_triggers=()):
    """Fixture-level removal of exact production rows (evidence loss only).

    Foreign keys and the immutable no-delete guards are disabled only on this
    disposable fixture database to seed synthetic corruption; this never
    describes or claims a production deletion path.
    """
    db = store._db
    db._conn.execute("PRAGMA foreign_keys=OFF")
    for trigger in drop_triggers:
        db._conn.execute(f"DROP TRIGGER {trigger}")
    cursor = db._conn.execute(sql, params)
    db._conn.commit()
    return cursor.rowcount


def test_settled_receipt_with_missing_notification_refuses(tmp_path):
    """A missing generation row is never affirmative settlement proof.

    Regression for the ``settled = not refused`` false positive: with the whole
    recovery-notification row gone, discovery legitimately yields zero targets,
    so an empty receipt list must refuse rather than reconcile.
    """
    store, _row, _attempt, _outcome = _finalized_recovery(tmp_path)
    assert _q(store)["state"] == "callback_consumed"
    assert _raw_delete(
        store, "DELETE FROM authority_policy_v2_recovery_notifications",
        drop_triggers=(
            "authority_policy_v2_recovery_notifications_no_delete",
        ),
    ) == 1
    queue = _RecordingQueue()
    before = _dump(store)

    status = reconcile_authority_policy_v2_post_final(
        _orch(store, queue), root_task_id=TASK_ID,
    )

    assert status == POST_FINAL_SETTLEMENT_REFUSED
    assert queue.items == []
    assert _dump(store) == before


def test_settled_receipt_with_missing_dispatch_pointer_refuses(tmp_path):
    """A missing root-dispatch pointer is never affirmative settlement proof."""
    store, _row, _attempt, _outcome = _finalized_recovery(tmp_path)
    assert _raw_delete(
        store, "DELETE FROM authority_policy_v2_root_dispatch",
        drop_triggers=("authority_policy_v2_root_dispatch_no_delete",),
    ) == 1
    queue = _RecordingQueue()
    before = _dump(store)

    status = reconcile_authority_policy_v2_post_final(
        _orch(store, queue), root_task_id=TASK_ID,
    )

    assert status == POST_FINAL_SETTLEMENT_REFUSED
    assert queue.items == []
    assert _dump(store) == before


def test_admitted_unsettled_generation_settles_bookkeeping_only(tmp_path):
    """An authentic admitted-but-unsettled G completes R4 step6 bookkeeping.

    The reopened owner settles the exact durable admission through the EXISTING
    admission-settlement writer: no republication, no reclaim, no second
    admission, no task status/session/step regression, and no queue call.
    """
    store, _row, _attempt, outcome = _finalized_recovery(tmp_path)
    db = store._db
    first = _RecordingQueue()
    assert reconcile_authority_policy_v2_post_final(
        _orch(store, first), root_task_id=TASK_ID,
    ) == POST_FINAL_RECONCILED
    assert _notification(store, outcome).state == "published"
    admission = _admit(store, outcome)
    assert admission.status == "claimed", admission
    assert _notification(store, outcome).state == "admitted"
    task_before = db.get_task(TASK_ID)
    queue = _RecordingQueue()
    before = _dump(store)

    status = reconcile_authority_policy_v2_post_final(
        _orch(store, queue), root_task_id=TASK_ID,
    )

    assert status == POST_FINAL_RECONCILED
    assert _notification(store, outcome).state == "settled"
    assert len(_stage_events(store, "notification_settled")) == 1
    assert queue.items == []  # never republished/reclaimed
    task_after = db.get_task(TASK_ID)
    assert task_after.status is task_before.status
    assert task_after.orchestration_step_count == task_before.orchestration_step_count
    assert task_after.current_session_id == task_before.current_session_id
    assert _dispatch(store).state == "admitted"
    assert _q(store)["state"] == "callback_consumed"
    assert _dump(store) != before


def test_settled_generation_with_deleted_claim_audit_refuses(tmp_path):
    """A settled generation whose claim audit was deleted never reconciles."""
    store, _row, _attempt, outcome = _finalized_recovery(tmp_path)
    db = store._db
    first = _RecordingQueue()
    assert reconcile_authority_policy_v2_post_final(
        _orch(store, first), root_task_id=TASK_ID,
    ) == POST_FINAL_RECONCILED
    assert _admit(store, outcome).status == "claimed"
    assert _settle_generation(store, outcome).status == "settled"
    deleted = _raw_delete(
        store,
        "DELETE FROM audit_log WHERE action=?"
        " AND json_extract(payload,'$.stage')='generation_claimed'",
        (AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,),
    )
    assert deleted == 1
    queue = _RecordingQueue()
    before = _dump(store)

    status = reconcile_authority_policy_v2_post_final(
        _orch(store, queue), root_task_id=TASK_ID,
    )

    assert status == POST_FINAL_SETTLEMENT_REFUSED
    assert queue.items == []
    assert _dump(store) == before


# ── actual caller: admitted-generation and corrupt-evidence coverage (B) ──


def _causal_recovery_via_caller(store, queue, row):
    """Drive the ACTUAL accepted-completion-recovery seam for causal R."""
    _consume_accepted_completion_recovery(
        _orch(store, queue), TASK_ID, _report(row),
        agent=MANAGER, session_id=SESSION_ID, result_row_id=row["id"],
    )


def test_caller_settles_admitted_generation_without_launch(tmp_path):
    """The real caller completes exact admitted bookkeeping, never launches."""
    store, row, _attempt, outcome = _finalized_recovery(tmp_path)
    db = store._db
    first = _RecordingQueue()
    _causal_recovery_via_caller(store, first, row)
    assert _notification(store, outcome).state == "published"
    assert _admit(store, outcome).status == "claimed"
    task_before = db.get_task(TASK_ID)
    queue = _RecordingQueue()
    before = _dump(store)

    _causal_recovery_via_caller(store, queue, row)

    assert _notification(store, outcome).state == "settled"
    assert len(_stage_events(store, "notification_settled")) == 1
    assert queue.items == []
    task_after = db.get_task(TASK_ID)
    assert task_after.status is task_before.status
    assert task_after.current_session_id == task_before.current_session_id
    assert task_after.orchestration_step_count == task_before.orchestration_step_count
    assert _dispatch(store).state == "admitted"
    assert _q(store)["state"] == "callback_consumed"
    assert _dump(store) != before


def test_caller_refuses_missing_notification_without_ordinary_fallback(tmp_path):
    """Corrupt causal evidence refuses read-only through the real caller."""
    store, row, _attempt, _outcome = _finalized_recovery(tmp_path)
    assert _raw_delete(
        store, "DELETE FROM authority_policy_v2_recovery_notifications",
        drop_triggers=(
            "authority_policy_v2_recovery_notifications_no_delete",
        ),
    ) == 1
    queue = _RecordingQueue()
    before = _dump(store)

    _causal_recovery_via_caller(store, queue, row)

    assert queue.items == []
    assert _q(store)["state"] == "callback_consumed"
    assert _dump(store) == before


def test_admitted_settlement_fault_refuses_with_prior_residue(tmp_path, monkeypatch):
    """An injected admission-settlement fault refuses with the prior residue."""
    store, _row, _attempt, outcome = _finalized_recovery(tmp_path)
    db = store._db
    first = _RecordingQueue()
    assert reconcile_authority_policy_v2_post_final(
        _orch(store, first), root_task_id=TASK_ID,
    ) == POST_FINAL_RECONCILED
    assert _admit(store, outcome).status == "claimed"
    queue = _RecordingQueue()
    before = _dump(store)
    calls = {"n": 0}
    real = db.settle_v2_continuation_generation_admission

    def _boom(**kwargs):
        calls["n"] += 1
        raise RuntimeError("injected admission-settlement failure")

    monkeypatch.setattr(
        db, "settle_v2_continuation_generation_admission", _boom,
    )

    status = reconcile_authority_policy_v2_post_final(
        _orch(store, queue), root_task_id=TASK_ID,
    )

    assert calls["n"] == 1  # the intended injection fired at the real seam
    assert status == POST_FINAL_SETTLEMENT_REFUSED
    assert queue.items == []
    assert _notification(store, outcome).state == "admitted"
    assert _dump(store) == before
