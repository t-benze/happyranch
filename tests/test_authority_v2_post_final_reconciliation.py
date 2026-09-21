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

import hashlib
import json
import types

import pytest

from runtime.infrastructure.database import Database
from runtime.models import (
    AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,
    TaskRecord,
    TaskStatus,
    authority_policy_v2_canonical_json_bytes,
)
from runtime.orchestrator.active_authority_policy import (
    load_session_policy_binding,
    persist_session_policy_binding,
    resolve_active_team_policy_snapshot,
)
from runtime.orchestrator.authority import (
    POST_FINAL_NOT_FINALIZED,
    POST_FINAL_RECONCILED,
    POST_FINAL_SETTLEMENT_REFUSED,
    publish_authority_policy_v2_notifications,
    reconcile_authority_policy_v2_post_final,
)
from runtime.orchestrator.orchestrator import completion_report_from_result_row
from runtime.orchestrator.run_step import _consume_accepted_completion_recovery
from tests.test_authority_v2_attempt_admission import (
    TEAM,
    _activate_v2,
    _assessment,
    _store,
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
    MANAGER,
    SESSION_ID,
    TASK_ID,
    _admitted,
    _counts,
    _drive,
    _drive_realistic_terminal_history,
    _finalize,
    _insert_ordinary_completion,
    _insert_q,
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


# ═════════════════════════════════════════════════════════════════════════
# TASK-8636 — C3d4b caller failure/restart matrix (brief requirements 1-3)
#
# These cases drive the REAL post-final callers over the REAL public
# finalize/settle/publish/admit storage seams.  Synthetic event-loss fixtures
# (raw deletes / injected store exceptions) are caller-unit evidence only and
# are labelled as such; they are never presented as genuine recovered CLI
# shipping.  Healthy controls sit beside every negative.
# ═════════════════════════════════════════════════════════════════════════


def _store_from_db(db):
    """Wrap an ALREADY-CONFIGURED ``Database`` in the public store forwarder.

    Used by the real-``OrgState`` reopen proof: the ``Database`` already carries
    the production owner bindings (``bind_authority_v2_owner``), so the store
    adds no fixture lambda of its own.
    """
    from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore

    return AuthorityPolicyStore(db)


def _carrier_admission(binding, task_id):
    """Parameterized copy of the attempt-admission carrier/evidence pair.

    The upstream helper hardcodes ``TASK_ID``; the matrix seeds several real
    roots in one database, so every immutable identity is threaded through.
    """
    assessment = _assessment(confidence=90)
    carrier = {
        "activation_epoch": binding["selector_epoch"],
        "activation_id": binding["activation_id"],
        "contract_digest": binding["contract_digest"],
        "contract_id": binding["contract_id"],
        "contract_version": binding["contract_version"],
        "executor_kind": binding["executor_kind"],
        "manager_session_id": binding["session_id"],
        "model_id": binding["model_id"],
        "policy_digest": binding["policy_digest"],
        "policy_version": binding["policy_version"],
        "provider_id": binding["provider_id"],
        "release_id": binding["release_id"],
        "root_task_id": task_id,
        **assessment,
    }
    canonical = authority_policy_v2_canonical_json_bytes(carrier)
    admission = {
        "team": binding["team"],
        "binding_id": binding["binding_id"],
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
    return carrier, admission


def _bind_root(store, *, task_id, session_id, activate):
    if activate:
        _activate_v2(store)
    snapshot = resolve_active_team_policy_snapshot(
        store=store, team=TEAM, agent_name=MANAGER, eligible=True,
    )
    assert snapshot is not None and snapshot.family == "v2"
    persist_session_policy_binding(
        db=store._db, task_id=task_id, session_id=session_id,
        agent_name=MANAGER, snapshot=snapshot,
        provider_id="codex", executor_kind="codex", model_id="default",
    )
    store._db.insert_task(TaskRecord(
        id=task_id, status=TaskStatus.IN_PROGRESS, assigned_agent=MANAGER,
        team=TEAM, brief="C3d4b caller matrix", orchestration_step_count=1,
    ))
    store._db.update_task(task_id, current_session_id=session_id)
    return load_session_policy_binding(
        db=store._db, task_id=task_id, session_id=session_id, agent_name=MANAGER,
    )


def _drive_root(store, *, task_id, session_id, row, attempt):
    for fn in (
        _stage_claim, _stage_claim_audit, _stage_evaluate,
        _stage_audit_evaluation, _stage_consume, _stage_audit_consumption,
    ):
        outcome = fn(store, row, attempt, task_id=task_id, session_id=session_id)
        assert outcome.status not in ("refused", "failed"), (fn.__name__, outcome)


def _seed_root(store, *, task_id, session_id, activate=False, boot=BOOT_A):
    """One genuine finalized continuation for ``task_id`` in ``store``."""
    binding = _bind_root(
        store, task_id=task_id, session_id=session_id, activate=activate,
    )
    carrier, admission = _carrier_admission(binding, task_id)
    assert store._db.admit_task_completion_callback(
        task_id=task_id, agent=MANAGER, session_id=session_id,
        output_summary="escalate", confidence_score=90, status="completed",
        decision_json=json.dumps(
            {"action": "escalate", "_manager_self_evaluation": carrier}
        ),
        v2_admission=admission,
    ) is True
    row = store._db.get_latest_task_result(task_id, MANAGER, session_id)
    attempt = store._db.get_authority_policy_v2_attempt_for_result(row["id"])
    assert attempt is not None
    _drive_root(
        store, task_id=task_id, session_id=session_id, row=row, attempt=attempt,
    )
    outcome = store.finalize_v2_continuation(
        root_task_id=task_id, manager_agent=MANAGER,
        manager_session_id=session_id, result_id=row["id"],
        origin_boot_id=attempt.origin_boot_id,
        owner_attempt_id=attempt.owner_attempt_id,
    )
    assert outcome.status == "continued", outcome
    store.bind_v2_process_boot_id(boot)
    return row, outcome


def _seed_q_root(store, *, task_id, result_id, session_id,
                 state="callback_accepted"):
    store._db._conn.execute(
        """INSERT INTO task_completion_recoveries
           (task_id, agent, origin_session_id, recovery_session_id,
            provider_session_id, claimed_at, expires_at, state,
            accepted_result_id, accepted_result_session_id)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (task_id, MANAGER, "sess-origin", session_id, "prov-1",
         "2026-01-01T00:00:00+00:00", "2999-01-01T00:00:00+00:00", state,
         result_id, session_id),
    )
    store._db._conn.commit()


def _settle_q_root(store, *, task_id, session_id, result_id):
    return store.settle_v2_continuation_receipt(
        root_task_id=task_id, manager_agent=MANAGER,
        manager_session_id=session_id, result_id=result_id,
        recovery_session_id=session_id, accepted_result_id=result_id,
        accepted_result_session_id=session_id,
    )


def _settled_root(store, *, task_id, session_id, activate=False, boot=BOOT_A):
    """A finalized root whose exact recovery Q is genuinely settled."""
    row, outcome = _seed_root(
        store, task_id=task_id, session_id=session_id, activate=activate, boot=boot,
    )
    _seed_q_root(store, task_id=task_id, result_id=row["id"], session_id=session_id)
    assert _settle_q_root(
        store, task_id=task_id, session_id=session_id, result_id=row["id"],
    ).status == "settled"
    store.bind_v2_process_boot_id(boot)
    return row, outcome


def _q_for(store, task_id):
    return store._db._conn.execute(
        "SELECT * FROM task_completion_recoveries WHERE task_id=? AND agent=?",
        (task_id, MANAGER),
    ).fetchone()


def _notification_for(store, generation_id):
    return store.get_v2_recovery_notification(generation_id)


# ── requirement 1: caller fault injection at every coupling ──────────────


def test_receipt_settlement_failure_refuses_with_exact_replayable_state(
    tmp_path, monkeypatch,
):
    """A raised receipt-settlement failure is a returned refusal, not a pass.

    Injected at the REAL ``settle_authority_policy_v2_continuation_receipt``
    coupling.  The caller catches it, keeps the prior committed final rows and
    the accepted Q, makes ZERO queue attempts (an unsettled receipt has no
    publication proof) and returns ``settlement_refused``.  Removing the
    injection lets exactly one exact retry settle and publish.
    """
    store, row, _attempt, outcome = _accepted_finalized(tmp_path)
    db = store._db
    queue = _RecordingQueue()
    before = _dump(store)
    calls = {"n": 0}
    real = db.settle_authority_policy_v2_continuation_receipt

    def _boom(**kwargs):
        calls["n"] += 1
        raise RuntimeError("injected receipt settlement failure")

    monkeypatch.setattr(
        db, "settle_authority_policy_v2_continuation_receipt", _boom,
    )

    status = reconcile_authority_policy_v2_post_final(
        _orch(store, queue), root_task_id=TASK_ID,
    )

    assert calls["n"] == 1  # the intended injection fired at the real seam
    assert status == POST_FINAL_SETTLEMENT_REFUSED
    assert queue.items == []
    assert _q(store)["state"] == "callback_accepted"
    assert _notification(store, outcome).state == "needed"
    assert db.get_task(TASK_ID).status is TaskStatus.PENDING
    assert _dump(store) == before

    monkeypatch.setattr(
        db, "settle_authority_policy_v2_continuation_receipt", real,
    )
    retry = _RecordingQueue()
    assert reconcile_authority_policy_v2_post_final(
        _orch(store, retry), root_task_id=TASK_ID,
    ) == POST_FINAL_RECONCILED
    assert _q(store)["state"] == "callback_consumed"
    assert len(retry.items) == 1
    assert retry.items[0][2]["authority_v2_generation"] == outcome.notification_id


def test_failed_claim_makes_zero_queue_attempts_and_keeps_residue(
    tmp_path, monkeypatch,
):
    """A raised publication-claim failure never reaches the raw queue put.

    Injected at the REAL ``claim_authority_policy_v2_notification_publication``
    coupling on a healthy settled root: the caller converts it into a bounded
    refusal receipt, the exact generation token is untouched and the dispatch
    pointer stays ``pending``.  This is a DIRECT-PUBLISHER unit test (synthetic
    store-exception evidence); the production-startup-entry variant is
    ``test_startup_caller_claim_failure_makes_zero_queue_attempts``.
    """
    store, row, _attempt, outcome = _finalized(tmp_path)
    db = store._db
    queue = _RecordingQueue()
    before = _dump(store)
    calls = {"n": 0}

    def _boom(**kwargs):
        calls["n"] += 1
        raise RuntimeError("injected publication-claim failure")

    monkeypatch.setattr(
        db, "claim_authority_policy_v2_notification_publication", _boom,
    )

    receipts = publish_authority_policy_v2_notifications(
        _orch(store, queue), queue, limit=None,
    )

    assert calls["n"] == 1
    assert queue.items == []
    assert any(
        r.get("status") == "publication_claim_failed"
        and r.get("notification_id") == outcome.notification_id
        for r in receipts
    ), receipts
    assert _notification(store, outcome).state == "needed"
    assert _dispatch(store).state == "pending"
    assert _dump(store) == before


def test_acknowledgement_failure_preserves_recoverable_publication_evidence(
    tmp_path, monkeypatch,
):
    """An ack failure leaves the raw put done and the lease reclaimable.

    The queue already received the exact tagged token, so the ack failure must
    not lose it, must not regress the notification and must not confer a second
    generation admission: a restart republishes (attempt 2) and the atomic
    admission fence still admits the generation exactly once.
    """
    store, _row, _attempt, outcome = _finalized(tmp_path)
    db = store._db
    real_ack = db.acknowledge_authority_policy_v2_notification_publication
    calls = {"n": 0}

    def _boom(**kwargs):
        calls["n"] += 1
        raise RuntimeError("injected acknowledgement failure")

    monkeypatch.setattr(
        db, "acknowledge_authority_policy_v2_notification_publication", _boom,
    )
    queue = _AttemptRecordingQueue()
    reconcile_authority_policy_v2_post_final(
        _orch(store, queue), root_task_id=TASK_ID,
    )

    assert calls["n"] == 1  # the intended injection fired at the real seam
    # An acknowledgement exception FOLLOWS a legitimate tagged put: exactly one
    # raw attempt and exactly one accepted enqueue (NOT zero queue attempts).
    assert queue.attempts == 1
    assert queue.items == [(
        SLUG, TASK_ID,
        {"authority_v2_generation": outcome.notification_id, "publication_attempt": 1},
    )]
    assert _notification(store, outcome).state == "publishing"

    monkeypatch.setattr(
        db, "acknowledge_authority_policy_v2_notification_publication", real_ack,
    )
    store.bind_v2_process_boot_id(BOOT_B)
    retry = _RecordingQueue()
    reconcile_authority_policy_v2_post_final(
        _orch(store, retry), root_task_id=TASK_ID,
    )
    assert len(retry.items) == 1
    assert retry.items[0][2]["publication_attempt"] == 2
    assert _notification(store, outcome).state == "published"

    # The generation admits exactly once from all that replay.
    assert _admit(store, outcome).status == "claimed"
    assert _admit(store, outcome).status != "claimed"


def test_startup_publication_continues_after_a_raised_target_failure(
    tmp_path, monkeypatch,
):
    """One bad target must not starve the unrelated eligible roots.

    Regression for the demonstrated propagation defect: the per-target loop in
    the real publisher previously let a raised claim exception abort the whole
    pass, so every later eligible root was starved on that startup.  The repair
    records a bounded per-target refusal and continues.  This is a
    DIRECT-PUBLISHER unit test; the production-startup-entry variant is
    ``test_startup_caller_bad_early_target_does_not_starve_later_roots``.
    """
    store = _store(tmp_path)
    seeded = {}
    for index in range(3):
        task_id = f"TASK-F{index}"
        session_id = f"sess-f{index}"
        row, outcome = _settled_root(
            store, task_id=task_id, session_id=session_id, activate=(index == 0),
        )
        seeded[task_id] = outcome
    db = store._db
    real_claim = db.claim_authority_policy_v2_notification_publication
    calls = {"n": 0}

    def _boom(**kwargs):
        calls["n"] += 1
        if kwargs.get("root_task_id") == "TASK-F0":
            raise RuntimeError("injected claim failure for the bad target")
        return real_claim(**kwargs)

    monkeypatch.setattr(
        db, "claim_authority_policy_v2_notification_publication", _boom,
    )
    queue = _RecordingQueue()

    receipts = publish_authority_policy_v2_notifications(
        _orch(store, queue), queue, limit=None,
    )

    assert calls["n"] == 3  # the bad target did not abort the pass
    published = {item[1] for item in queue.items}
    assert published == {"TASK-F1", "TASK-F2"}
    assert any(
        r.get("status") == "publication_claim_failed"
        and r.get("notification_id") == seeded["TASK-F0"].notification_id
        for r in receipts
    ), receipts
    # The two healthy roots each got their OWN valid tagged publication.
    by_root = {item[1]: item[2] for item in queue.items}
    assert by_root["TASK-F1"]["authority_v2_generation"] == (
        seeded["TASK-F1"].notification_id
    )
    assert by_root["TASK-F2"]["authority_v2_generation"] == (
        seeded["TASK-F2"].notification_id
    )
    # The bad target's own durable pointer/residue is untouched.
    assert _notification_for(store, seeded["TASK-F0"].notification_id).state == "needed"
    assert store.get_v2_root_dispatch("TASK-F0").state == "pending"


# ── requirement 2: identity, replacement and reopened state ──────────────


def test_cancelled_root_causal_recovery_refuses_read_only(tmp_path):
    """Affirmative cancellation is a predicate, never a permission.

    A real cancellation installed on the causal owner makes the post-final
    caller refuse with the prior residue and ZERO queue attempts.
    """
    store, row, _attempt, outcome = _accepted_finalized(tmp_path)
    db = store._db
    db._conn.execute(
        "UPDATE tasks SET cancelled_at=? WHERE id=?",
        ("2026-01-01T00:00:00+00:00", TASK_ID),
    )
    db._conn.commit()
    queue = _RecordingQueue()
    before = _dump(store)

    _causal_recovery_via_caller(store, queue, row)

    assert queue.items == []
    assert _notification(store, outcome).state == "needed"
    assert _dump(store) == before


def test_affirmative_owner_loss_refuses_at_the_caller(tmp_path):
    """A newer durable owner/session is never the old recovery's to settle."""
    store, row, _attempt, outcome = _accepted_finalized(tmp_path)
    db = store._db
    db._conn.execute(
        "UPDATE tasks SET current_session_id=? WHERE id=?",
        ("sess-newer-owner", TASK_ID),
    )
    db._conn.commit()
    queue = _RecordingQueue()
    before = _dump(store)

    _causal_recovery_via_caller(store, queue, row)

    assert queue.items == []
    assert _notification(store, outcome).state == "needed"
    assert _dump(store) == before


def test_malformed_admitted_identity_refuses_without_repair(tmp_path):
    """A corrupt admitted reservation identity never becomes a settlement.

    The exact reserved next-session is durable identity; a present-but-NULL
    value refuses read-only through the same caller with no repair-by-rewrite.
    """
    store, row, _attempt, outcome = _finalized_recovery(tmp_path)
    db = store._db
    first = _RecordingQueue()
    _causal_recovery_via_caller(store, first, row)
    assert _notification(store, outcome).state == "published"
    assert _admit(store, outcome).status == "claimed"
    assert _notification(store, outcome).state == "admitted"
    # Synthetic caller-unit corruption of the reserved session identity.
    _raw_delete_count = db._conn.execute(
        "UPDATE authority_policy_v2_recovery_notifications "
        "SET next_session_id=NULL WHERE notification_id=?",
        (outcome.notification_id,),
    ).rowcount
    db._conn.commit()
    assert _raw_delete_count == 1
    queue = _RecordingQueue()
    before = _dump(store)

    _causal_recovery_via_caller(store, queue, row)

    assert queue.items == []
    # The malformed row is refused by the typed reader (never repaired) and its
    # raw durable state is byte-identical.
    assert store.get_v2_recovery_notification(outcome.notification_id) is None
    assert db._conn.execute(
        "SELECT state, next_session_id FROM authority_policy_v2_recovery_notifications "
        "WHERE notification_id=?",
        (outcome.notification_id,),
    ).fetchone()["next_session_id"] is None
    assert _dump(store) == before


def test_current_generation_b_progresses_while_settled_a_stays_untouched(tmp_path):
    """Old A must not stand in for independently valid current B.

    B is produced through the real public successor lifecycle; a post-final
    pass on the root publishes B's OWN token (never A's) and leaves A's
    committed generation bytes untouched.  This is the base progression case;
    ``test_current_generation_b_progresses_on_its_own_path`` (later in this
    module) strengthens it with the explicit old-A read-only refusal replay.
    """
    from tests.test_authority_v2_decision_dispatch import (
        _drive_generation_b_pending,
        _spent_ready_b,
    )

    store, _row_a, outcome_a, r2_row, attempt_b = _spent_ready_b(tmp_path)
    generation_b = _drive_generation_b_pending(store, r2_row, attempt_b)
    assert generation_b != outcome_a.notification_id
    assert _notification(store, outcome_a).state == "settled"
    a_before = _notification(store, outcome_a)
    store.bind_v2_process_boot_id(BOOT_A)
    queue = _RecordingQueue()

    status = reconcile_authority_policy_v2_post_final(
        _orch(store, queue), root_task_id=TASK_ID,
    )

    assert status == POST_FINAL_RECONCILED
    assert [item[2]["authority_v2_generation"] for item in queue.items] == [
        generation_b
    ]
    # A's committed generation is byte-identical: B progressed, A did not move.
    assert _notification(store, outcome_a) == a_before


# ── requirement 3: real startup discovery beyond 32 roots ────────────────


def test_startup_publication_covers_every_eligible_root_beyond_32(tmp_path):
    """Startup discovery publishes EVERY eligible root, not the first 32.

    Seeds 34 genuinely finalized+settled roots through the real public stages
    and leaves the earliest one holding an authentic same-boot live lease (an
    ineligible early target).  The real startup publisher must still deliver
    each later root its own valid tagged publication; the early refusal is a
    bounded refusal, never starvation.
    """
    from runtime.daemon.__main__ import _publish_v2_generations_on_startup

    store = _store(tmp_path)
    total = 34
    # The early target first, so the first pass only ever sees it.
    row0, outcome0 = _settled_root(
        store, task_id="TASK-M00", session_id="sess-m00", activate=True,
    )
    warmup = _RecordingQueue()
    assert reconcile_authority_policy_v2_post_final(
        _orch(store, warmup), root_task_id="TASK-M00",
    ) == POST_FINAL_RECONCILED
    assert len(warmup.items) == 1

    for index in range(1, total):
        _settled_root(
            store, task_id=f"TASK-M{index:02d}",
            session_id=f"sess-m{index:02d}", activate=False,
        )

    queue = _RecordingQueue()
    org = types.SimpleNamespace(
        slug=SLUG, orchestrator=_orch(store, queue), orchestrator_present=True,
    )
    _publish_v2_generations_on_startup(org, queue)

    published = {item[1]: item[2] for item in queue.items}
    # Every later eligible root is present exactly once with its OWN token.
    assert "TASK-M00" not in published  # a live lease is never stolen
    assert len(published) == total - 1
    for index in range(1, total):
        task_id = f"TASK-M{index:02d}"
        assert published[task_id]["authority_v2_generation"].startswith("APV2N-")
        assert published[task_id]["publication_attempt"] == 1
    # Exact root/G isolation: distinct roots never share a generation token.
    tokens = [meta["authority_v2_generation"] for meta in published.values()]
    assert len(set(tokens)) == total - 1
    # Ordinary roots stay ordinary: pending pointer, Pending task, no admission.
    for index in range(1, total):
        task_id = f"TASK-M{index:02d}"
        assert store.get_v2_root_dispatch(task_id).state == "pending"
        assert store._db.get_task(task_id).status is TaskStatus.PENDING
    assert _notification(store, outcome0).state == "published"


# ── reopened state over the SAME persisted file ─────────────────────────


def _make_org_paths(tmp_path, *, slug="test"):
    """Create the real runtime layout once and return the org paths."""
    from runtime.orchestrator._paths import OrgPaths
    from runtime.runtime import RuntimeDir
    from tests.conftest import seed_test_agents

    runtime = RuntimeDir.init(tmp_path / "rt")
    paths = OrgPaths(root=runtime.orgs_dir / slug)
    paths.teams_config_path.parent.mkdir(parents=True, exist_ok=True)
    paths.teams_config_path.write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: engineering_manager\n"
        "    workers: [dev_agent]\n"
    )
    seed_test_agents(paths, ("engineering_manager", "dev_agent"))
    return paths


def _open_org(paths, *, slug="test"):
    """Open a REAL ``OrgState`` with the production owner bindings installed.

    Builds the actual runtime wiring (org paths -> teams -> ``Orchestrator`` ->
    ``OrgState``) so the reopened proof exercises the real server-owned
    permission-digest reader, not a fixture lambda.
    """
    from runtime.config import Settings
    from runtime.daemon.org_state import OrgState
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.teams import TeamsRegistry

    db = Database(paths.db_path)
    settings = Settings()
    teams = TeamsRegistry.load(paths.root)
    orchestrator = Orchestrator(
        db=db, settings=settings, paths=paths, slug=slug, teams=teams,
    )
    org = OrgState(
        slug=slug, root=paths.root, db=db, teams=teams,
        settings=settings, orchestrator=orchestrator,
    )
    org.bind_authority_v2_owner()
    return org


def test_reopened_orgstate_over_same_file_republishes_lost_queue(tmp_path):
    """A genuinely reopened Database+OrgState rediscovers a lost in-memory queue.

    Asserts object/connection inequality, the identical durable path, retained
    committed rows and the real ``OrgState.bind_authority_v2_owner`` permission
    binding on BOTH the original and the reopened owner.  The old owner is fully
    quiescent before the reopen (no live object is reused and the boot string is
    never reassigned on a live object).
    """
    paths = _make_org_paths(tmp_path)
    org = _open_org(paths)
    store = _store_from_db(org.db)
    row, outcome = _settled_root(
        store, task_id=TASK_ID, session_id=SESSION_ID, activate=True,
        boot=org.authority_v2_origin_boot_id,
    )
    first = _RecordingQueue()
    assert reconcile_authority_policy_v2_post_final(
        _orch(store, first), root_task_id=TASK_ID,
    ) == POST_FINAL_RECONCILED
    assert len(first.items) == 1

    # Old owner is quiescent; build a genuinely NEW Database + OrgState over the
    # SAME persisted runtime/database file.
    old_db, old_conn = org.db, org.db._conn
    reopened_org = _open_org(paths)
    reopened_db = reopened_org.db
    assert reopened_db is not old_db
    assert reopened_db._conn is not old_conn
    assert reopened_db.db_path == old_db.db_path
    assert reopened_org.authority_v2_origin_boot_id != (
        org.authority_v2_origin_boot_id
    )
    # Retained committed rows survive the reopen.
    retained = reopened_db.get_authority_policy_v2_continue_envelope_for_root(
        TASK_ID
    )
    assert retained is not None and retained.envelope_id == outcome.envelope_id
    reopened_store = _store_from_db(reopened_db)

    second = _RecordingQueue()
    assert reconcile_authority_policy_v2_post_final(
        _orch(reopened_store, second), root_task_id=TASK_ID,
    ) == POST_FINAL_RECONCILED
    assert len(second.items) == 1
    assert second.items[0][2]["publication_attempt"] == 2
    assert second.items[0][2]["authority_v2_generation"] == outcome.notification_id
    assert _notification(reopened_store, outcome).state == "published"


# ═════════════════════════════════════════════════════════════════════════
# TASK-8651 — C3d4b Q-discovery causal identity and the remaining Part B
# caller/reopen/startup assertions.
#
# Synthetic event-loss/corruption fixtures below are CALLER-UNIT evidence only
# (raw deletes, injected store exceptions, SQL-seeded current Q rows).  They are
# never presented as genuine recovered subprocess CLI shipping.  The broad
# integration suite is SKIPPED under founder THR-243 seq42, never PASS.
# ═════════════════════════════════════════════════════════════════════════


class _AttemptRecordingQueue:
    """Counts RAW queue attempts separately from ACCEPTED enqueues.

    ``attempts`` increments on every ``put_nowait`` call; ``items`` records only
    a completed call.  A failed claim or failed raw put therefore shows
    ``attempts == 1 and items == []``, while an acknowledgement failure after a
    legitimate tagged put shows ``attempts == 1 and items == [the tagged put]``.
    ``fail_for`` fails the raw put only for the named roots, so anti-starvation
    can be driven through the real startup caller.
    """

    def __init__(self, *, fail_put: bool = False, fail_for=()):
        self.attempts = 0
        self.items: list[tuple] = []
        self.fail_put = fail_put
        self.fail_for = set(fail_for)

    def put_nowait(self, slug, task_id, *, metadata=None):
        self.attempts += 1
        if self.fail_put or task_id in self.fail_for:
            raise RuntimeError("injected raw queue put failure")
        self.items.append((slug, task_id, metadata))


def _receipt_row(store, recovery_session):
    row = store._db._conn.execute(
        "SELECT * FROM task_completion_recoveries WHERE recovery_session_id=?",
        (recovery_session,),
    ).fetchone()
    return dict(row) if row is not None else None


def _startup_publish(store, queue):
    """Run the PRODUCTION per-org startup publication entry for ``store``."""
    from runtime.daemon.__main__ import _publish_v2_generations_on_startup

    org = types.SimpleNamespace(
        slug=SLUG, orchestrator=_orch(store, queue), orchestrator_present=True,
    )
    _publish_v2_generations_on_startup(org, queue)


# ── requirement 1: converged Q discovery on full causal identity ─────────


def test_accepted_current_q_ignores_unrelated_terminal_history(tmp_path):
    """A healthy current accepted Q settles despite unrelated terminal history.

    Regression for the manager step19 defect: the read-only Q discovery treated
    ``len(receipts) > 1`` as a blanket conflict BEFORE classifying causal
    relatedness, so an unrelated established terminal historical receipt refused
    a perfectly healthy current ``callback_accepted`` Q with zero queue calls.
    The current accepted Q here is synthetic caller-unit setup, NOT recovered
    subprocess shipping.
    """
    store, row, _attempt, outcome = _accepted_finalized(tmp_path)
    _insert_q(
        store, recovery_session="sess-historical",
        origin_session="sess-history-origin",
        accepted_result_id=row["id"] + 1000,
        accepted_result_session="sess-historical",
    )
    history_before = _receipt_row(store, "sess-historical")
    queue = _RecordingQueue()

    status = reconcile_authority_policy_v2_post_final(
        _orch(store, queue), root_task_id=TASK_ID,
    )

    assert status == POST_FINAL_RECONCILED
    assert _q(store)["state"] == "callback_consumed"
    assert _notification(store, outcome).state == "published"
    assert queue.items == [(
        SLUG, TASK_ID,
        {"authority_v2_generation": outcome.notification_id, "publication_attempt": 1},
    )]
    # The unrelated historical receipt is byte-for-byte unchanged.
    assert _receipt_row(store, "sess-historical") == history_before


def test_consumed_current_q_ignores_unrelated_terminal_history(tmp_path):
    """An already consumed current Q republishes read-only beside history."""
    store, row, _attempt, outcome = _finalized_recovery(tmp_path)
    _insert_q(
        store, recovery_session="sess-historical",
        origin_session="sess-history-origin",
        accepted_result_id=row["id"] + 1000,
        accepted_result_session="sess-historical",
    )
    current_before = dict(_q(store))
    history_before = _receipt_row(store, "sess-historical")
    queue = _RecordingQueue()

    status = reconcile_authority_policy_v2_post_final(
        _orch(store, queue), root_task_id=TASK_ID,
    )

    assert status == POST_FINAL_RECONCILED
    assert dict(_q(store)) == current_before  # no second transition
    assert _notification(store, outcome).state == "published"
    assert [item[2]["authority_v2_generation"] for item in queue.items] == [
        outcome.notification_id
    ]
    assert _receipt_row(store, "sess-historical") == history_before


def test_accepted_current_q_ignores_public_produced_terminal_history(tmp_path):
    """The same green path over a GENUINE public historical recovery chronology.

    ``_drive_realistic_terminal_history`` produces the historical terminal Q
    through the real public claim -> binding -> callback admission -> consumption
    seams (never a SQL-inserted fixture); the current result is finalized with
    its own accepted Q and driven through the ACTUAL accepted-completion-recovery
    caller (``run_step._consume_accepted_completion_recovery``).
    """
    store, row, attempt, historical = _drive_realistic_terminal_history(tmp_path)
    _drive(store, row, attempt, "consumed_audited")
    outcome = _finalize(store, row, attempt)
    assert outcome.status == "continued", outcome
    _seed_q(
        store, row["id"], state="callback_accepted",
        origin_session="sess-current-origin",
    )
    store.bind_v2_process_boot_id(BOOT_A)
    assert historical["id"] != row["id"]
    history_before = _receipt_row(store, "sess-hist-recovery")
    assert history_before is not None and history_before["state"] == "callback_consumed"
    queue = _RecordingQueue()

    _consume_accepted_completion_recovery(
        _orch(store, queue), TASK_ID, _report(row),
        agent=MANAGER, session_id=SESSION_ID, result_row_id=row["id"],
    )

    current = store._db._conn.execute(
        "SELECT * FROM task_completion_recoveries WHERE task_id=? AND agent=? "
        "AND recovery_session_id=?",
        (TASK_ID, MANAGER, SESSION_ID),
    ).fetchone()
    assert current is not None and current["state"] == "callback_consumed"
    assert _notification(store, outcome).state == "published"
    assert queue.items == [(
        SLUG, TASK_ID,
        {"authority_v2_generation": outcome.notification_id, "publication_attempt": 1},
    )]
    assert _receipt_row(store, "sess-hist-recovery") == history_before


def test_ordinary_completion_ignores_unrelated_terminal_history(tmp_path):
    """Genuine ordinary evidence is not blocked by unrelated terminal history."""
    store, row, _attempt, outcome = _finalized(tmp_path)
    _insert_q(
        store, recovery_session="sess-historical",
        origin_session="sess-history-origin",
        accepted_result_id=row["id"] + 1000,
        accepted_result_session="sess-historical",
    )
    history_before = _receipt_row(store, "sess-historical")
    queue = _RecordingQueue()

    status = reconcile_authority_policy_v2_post_final(
        _orch(store, queue), root_task_id=TASK_ID,
    )

    assert status == POST_FINAL_RECONCILED
    assert _notification(store, outcome).state == "published"
    assert [item[2]["authority_v2_generation"] for item in queue.items] == [
        outcome.notification_id
    ]
    # No recovery receipt was invented, transitioned or rewritten.
    assert _receipt_row(store, SESSION_ID) is None
    assert _receipt_row(store, "sess-historical") == history_before


def test_current_q_alongside_ordinary_evidence_settles_recovery(tmp_path):
    """A genuine current Q wins over coexisting ordinary completion evidence."""
    store, row, _attempt, outcome = _accepted_finalized(tmp_path)
    _insert_ordinary_completion(store, row["id"])
    ordinary_before = _receipt_row(store, SESSION_ID)
    queue = _RecordingQueue()

    status = reconcile_authority_policy_v2_post_final(
        _orch(store, queue), root_task_id=TASK_ID,
    )

    assert status == POST_FINAL_RECONCILED
    # The exact recovery branch consumed the Q exactly once and published.
    assert _q(store)["state"] == "callback_consumed"
    assert _notification(store, outcome).state == "published"
    assert [item[2]["authority_v2_generation"] for item in queue.items] == [
        outcome.notification_id
    ]
    assert ordinary_before is not None


@pytest.mark.parametrize(
    "mode,state,settled",
    [
        # exact current recovery session but a FOREIGN accepted result
        ("recovery_match_result_foreign", "callback_consumed", True),
        # exact current accepted result but a FOREIGN recovery session
        ("result_match_recovery_foreign", "callback_consumed", True),
        # malformed/opaque accepted-result identity
        ("malformed_result", "callback_consumed", True),
        # nonterminal state can never be established as unrelated
        ("nonterminal", "claimed", False),
        # unknown state fails closed
        ("unknown_state", "unknown_state", False),
    ],
)
def test_related_q_conflict_refuses_read_only(tmp_path, mode, state, settled):
    """A related/partial/malformed/nonterminal Q never disappears.

    It must refuse read-only with zero queue calls and no ordinary fallback, even
    beside otherwise valid ordinary completion evidence.
    """
    store, row, _attempt, outcome = _finalized(tmp_path)
    if mode == "recovery_match_result_foreign":
        _insert_q(
            store, recovery_session=SESSION_ID, state=state,
            accepted_result_id=row["id"] + 1000,
            accepted_result_session=SESSION_ID, settled=settled,
        )
    elif mode == "result_match_recovery_foreign":
        _insert_q(
            store, recovery_session="sess-foreign", state=state,
            accepted_result_id=row["id"],
            accepted_result_session="sess-foreign", settled=settled,
        )
    elif mode == "malformed_result":
        _insert_q(
            store, recovery_session="sess-foreign", state=state,
            accepted_result_id=str(row["id"]),
            accepted_result_session="sess-foreign", settled=settled,
        )
    else:
        _insert_q(
            store, recovery_session="sess-foreign", state=state,
            accepted_result_id=row["id"] + 1000,
            accepted_result_session="sess-foreign", settled=settled,
        )
    queue = _RecordingQueue()
    before = _dump(store)

    status = reconcile_authority_policy_v2_post_final(
        _orch(store, queue), root_task_id=TASK_ID,
    )

    assert status == POST_FINAL_SETTLEMENT_REFUSED
    assert queue.items == []
    assert _notification(store, outcome).state == "needed"
    assert _dump(store) == before


# ── requirement 2: caller fault matrix at the REAL callers ───────────────


def test_consume_caller_receipt_settlement_failure_is_a_refusal(
    tmp_path, monkeypatch,
):
    """The real accepted-completion caller converts a raised settlement fault.

    Injected at the REAL ``settle_authority_policy_v2_continuation_receipt``
    coupling on a healthy accepted Q.  The injection must actually fire, the
    caller must return (not raise), and a failed receipt settlement makes ZERO
    raw queue attempts.  Removing the injection yields exactly one settlement +
    publication.
    """
    store, row, _attempt, outcome = _accepted_finalized(tmp_path)
    db = store._db
    queue = _AttemptRecordingQueue()
    before = _dump(store)
    calls = {"n": 0}
    real = db.settle_authority_policy_v2_continuation_receipt

    def _boom(**kwargs):
        calls["n"] += 1
        raise RuntimeError("injected receipt settlement failure")

    monkeypatch.setattr(
        db, "settle_authority_policy_v2_continuation_receipt", _boom,
    )

    _causal_recovery_via_caller(store, queue, row)

    assert calls["n"] == 1  # the intended injection fired at the real seam
    assert queue.attempts == 0 and queue.items == []
    assert _q(store)["state"] == "callback_accepted"
    assert _notification(store, outcome).state == "needed"
    assert _dump(store) == before

    monkeypatch.setattr(
        db, "settle_authority_policy_v2_continuation_receipt", real,
    )
    retry = _AttemptRecordingQueue()
    _causal_recovery_via_caller(store, retry, row)
    assert retry.attempts == 1 and len(retry.items) == 1
    assert _q(store)["state"] == "callback_consumed"
    assert _notification(store, outcome).state == "published"


def test_consume_caller_admission_settlement_failure_is_a_refusal(
    tmp_path, monkeypatch,
):
    """The real caller converts a raised admission-settlement fault.

    Injected at the REAL ``settle_v2_continuation_generation_admission`` coupling
    after an authentic generation admission.  The injection must fire, the exact
    admitted residue is preserved, and there are ZERO raw queue attempts (a
    refused admitted bookkeeping step never republishes).
    """
    store, row, _attempt, outcome = _finalized_recovery(tmp_path)
    db = store._db
    first = _RecordingQueue()
    _causal_recovery_via_caller(store, first, row)
    assert _admit(store, outcome).status == "claimed"
    queue = _AttemptRecordingQueue()
    before = _dump(store)
    calls = {"n": 0}

    def _boom(**kwargs):
        calls["n"] += 1
        raise RuntimeError("injected admission-settlement failure")

    monkeypatch.setattr(
        db, "settle_v2_continuation_generation_admission", _boom,
    )

    _causal_recovery_via_caller(store, queue, row)

    assert calls["n"] == 1
    assert queue.attempts == 0 and queue.items == []
    assert _notification(store, outcome).state == "admitted"
    assert _dump(store) == before


def test_startup_caller_claim_failure_makes_zero_queue_attempts(
    tmp_path, monkeypatch,
):
    """A raised claim through the STARTUP caller never reaches the raw put.

    The direct-publisher analogue remains a publisher-unit test; this one drives
    the production per-org startup entry (``_publish_v2_generations_on_startup``).
    """
    store, _row, _attempt, outcome = _finalized(tmp_path)
    db = store._db
    queue = _AttemptRecordingQueue()
    before = _dump(store)
    calls = {"n": 0}

    def _boom(**kwargs):
        calls["n"] += 1
        raise RuntimeError("injected publication-claim failure")

    monkeypatch.setattr(
        db, "claim_authority_policy_v2_notification_publication", _boom,
    )

    _startup_publish(store, queue)

    assert calls["n"] == 1
    assert queue.attempts == 0 and queue.items == []
    assert _notification(store, outcome).state == "needed"
    assert _dispatch(store).state == "pending"
    assert _dump(store) == before


def test_startup_caller_raw_put_failure_keeps_reclaimable_residue(tmp_path):
    """A failed raw put keeps the audited claim reclaimable and retries once."""
    store, _row, _attempt, outcome = _finalized_recovery(tmp_path)
    queue = _AttemptRecordingQueue(fail_put=True)

    _startup_publish(store, queue)

    assert queue.attempts == 1 and queue.items == []
    notification = _notification(store, outcome)
    assert notification.state == "publishing"
    assert notification.publisher_boot_id is None
    assert notification.lease_deadline is None
    assert len(_stage_events(store, "publish_failed")) == 1
    # The independently committed settlement survives the raw put failure.
    assert _q(store)["state"] == "callback_consumed"

    # The injection is removed for the REAL permitted retry: a restarted boot
    # reclaims and publishes exactly once.
    store.bind_v2_process_boot_id(BOOT_B)
    retry = _AttemptRecordingQueue()
    _startup_publish(store, retry)
    assert retry.attempts == 1 and len(retry.items) == 1
    assert retry.items[0][2]["publication_attempt"] == 2
    assert _notification(store, outcome).state == "published"


def test_startup_caller_raw_put_and_bookkeeping_failure_is_bounded(
    tmp_path, monkeypatch,
):
    """NEW branch: a raw put failure AND a raising failure-bookkeeping write.

    The publisher must still return a bounded ``publish_failed`` receipt for the
    bad target, retain the prior ``publishing`` claim/lease (safely reclaimable)
    and let BOTH later eligible roots publish.  This is anti-starvation through
    the actual startup caller, driven by per-root raw-put failure.
    """
    store = _store(tmp_path)
    bad_row, bad_outcome = _settled_root(
        store, task_id="TASK-BAD", session_id="sess-bad", activate=True,
    )
    good = [
        _settled_root(
            store, task_id=f"TASK-GOOD{i}", session_id=f"sess-good{i}",
            activate=False,
        )
        for i in range(2)
    ]
    db = store._db
    calls = {"n": 0}

    def _boom(**kwargs):
        calls["n"] += 1
        raise RuntimeError("injected failure-bookkeeping failure")

    monkeypatch.setattr(
        db, "record_authority_policy_v2_notification_publication_failure", _boom,
    )
    queue = _AttemptRecordingQueue(fail_for={"TASK-BAD"})

    _startup_publish(store, queue)

    # The bad target attempted its raw put and its bookkeeping write; the bad
    # write is contained and the pass continues.
    assert queue.attempts == 3
    assert calls["n"] == 1
    published = {item[1]: item[2] for item in queue.items}
    assert set(published) == {"TASK-GOOD0", "TASK-GOOD1"}
    assert queue.attempts - len(queue.items) == 1  # exactly the bad target failed
    assert published["TASK-GOOD0"]["authority_v2_generation"] == (
        good[0][1].notification_id
    )
    assert published["TASK-GOOD1"]["authority_v2_generation"] == (
        good[1][1].notification_id
    )
    # The failed-bookkeeping target retains its exact prior claim/lease, and the
    # refusal is never authentication or permission.
    retained = _notification(store, bad_outcome)
    assert retained.state == "publishing"
    assert retained.publisher_boot_id == BOOT_A
    assert store.get_v2_root_dispatch("TASK-BAD").state == "pending"
    assert bad_row["id"] > 0


def test_startup_caller_bad_early_target_does_not_starve_later_roots(
    tmp_path, monkeypatch,
):
    """A raised claim on the EARLY startup target never aborts the pass.

    Through the actual startup caller: the bad early target is a bounded refusal
    and every later eligible root still receives its own valid tagged token.
    """
    store = _store(tmp_path)
    bad_row, bad_outcome = _settled_root(
        store, task_id="TASK-BAD", session_id="sess-bad", activate=True,
    )
    good = [
        _settled_root(
            store, task_id=f"TASK-GOOD{i}", session_id=f"sess-good{i}",
            activate=False,
        )
        for i in range(2)
    ]
    db = store._db
    real_claim = db.claim_authority_policy_v2_notification_publication
    calls = {"n": 0}

    def _boom(**kwargs):
        calls["n"] += 1
        if kwargs.get("root_task_id") == "TASK-BAD":
            raise RuntimeError("injected claim failure for the bad target")
        return real_claim(**kwargs)

    monkeypatch.setattr(
        db, "claim_authority_policy_v2_notification_publication", _boom,
    )
    queue = _AttemptRecordingQueue()

    _startup_publish(store, queue)

    assert calls["n"] == 3  # the bad target did not abort the pass
    assert queue.attempts == 2 and len(queue.items) == 2
    published = {item[1]: item[2] for item in queue.items}
    assert set(published) == {"TASK-GOOD0", "TASK-GOOD1"}
    assert published["TASK-GOOD0"]["authority_v2_generation"] == (
        good[0][1].notification_id
    )
    assert published["TASK-GOOD1"]["authority_v2_generation"] == (
        good[1][1].notification_id
    )
    assert _notification(store, bad_outcome).state == "needed"
    assert store.get_v2_root_dispatch("TASK-BAD").state == "pending"
    assert bad_row["id"] > 0


# ── requirement 3: identity/reopen/startup assertions ────────────────────


def test_reopened_orgstate_admitted_generation_settles_once(tmp_path):
    """A genuinely reopened owner settles an admitted G exactly once.

    New ``Database`` AND real ``OrgState`` over the SAME persisted file after the
    old owner is quiescent: object/connection inequality, retained exact rows,
    real new boot + permission reader.  The admitted-but-unsettled generation
    completes its R4 step6 bookkeeping with ZERO republish/reclaim/relaunch and
    no task/session/step regression.
    """
    paths = _make_org_paths(tmp_path)
    org = _open_org(paths)
    store = _store_from_db(org.db)
    _row, outcome = _settled_root(
        store, task_id=TASK_ID, session_id=SESSION_ID, activate=True,
        boot=org.authority_v2_origin_boot_id,
    )
    first = _RecordingQueue()
    assert reconcile_authority_policy_v2_post_final(
        _orch(store, first), root_task_id=TASK_ID,
    ) == POST_FINAL_RECONCILED
    assert len(first.items) == 1
    assert _admit(store, outcome).status == "claimed"
    assert _notification(store, outcome).state == "admitted"
    task_before = org.db.get_task(TASK_ID)
    q_before = dict(_q(store))

    old_db, old_conn = org.db, org.db._conn
    reopened = _open_org(paths)
    reopened_db = reopened.db
    assert reopened_db is not old_db
    assert reopened_db._conn is not old_conn
    assert reopened_db.db_path == old_db.db_path
    assert reopened.authority_v2_origin_boot_id != org.authority_v2_origin_boot_id
    reopened_store = _store_from_db(reopened_db)
    queue = _RecordingQueue()
    before = _dump(reopened_store)

    assert reconcile_authority_policy_v2_post_final(
        _orch(reopened_store, queue), root_task_id=TASK_ID,
    ) == POST_FINAL_RECONCILED

    assert queue.items == []  # bookkeeping only: no republish/reclaim/relaunch
    assert _notification(reopened_store, outcome).state == "settled"
    assert len(_stage_events(reopened_store, "notification_settled")) == 1
    assert dict(_q(reopened_store)) == q_before  # exact retained Q row
    task_after = reopened_db.get_task(TASK_ID)
    assert task_after.status is task_before.status
    assert task_after.current_session_id == task_before.current_session_id
    assert task_after.orchestration_step_count == task_before.orchestration_step_count
    assert reopened_store.get_v2_root_dispatch(TASK_ID).state == "admitted"
    assert _dump(reopened_store) != before  # exactly the settlement bookkeeping


def test_reopened_orgstate_settled_generation_replay_is_read_only(tmp_path):
    """A settled generation replay on a reopened owner writes nothing."""
    paths = _make_org_paths(tmp_path)
    org = _open_org(paths)
    store = _store_from_db(org.db)
    _row, outcome = _settled_root(
        store, task_id=TASK_ID, session_id=SESSION_ID, activate=True,
        boot=org.authority_v2_origin_boot_id,
    )
    first = _RecordingQueue()
    assert reconcile_authority_policy_v2_post_final(
        _orch(store, first), root_task_id=TASK_ID,
    ) == POST_FINAL_RECONCILED
    assert _admit(store, outcome).status == "claimed"
    assert _settle_generation(store, outcome).status == "settled"

    reopened = _open_org(paths)
    reopened_store = _store_from_db(reopened.db)
    queue = _RecordingQueue()
    before = _dump(reopened_store)

    assert reconcile_authority_policy_v2_post_final(
        _orch(reopened_store, queue), root_task_id=TASK_ID,
    ) == POST_FINAL_RECONCILED

    assert queue.items == []
    assert _notification(reopened_store, outcome).state == "settled"
    assert len(_stage_events(reopened_store, "notification_settled")) == 1
    assert _dump(reopened_store) == before  # read-only replay


def test_reopened_orgstate_corrupted_evidence_refuses_with_residue(tmp_path):
    """Corrupted admitted evidence refuses read-only across a genuine reopen."""
    paths = _make_org_paths(tmp_path)
    org = _open_org(paths)
    store = _store_from_db(org.db)
    _row, outcome = _settled_root(
        store, task_id=TASK_ID, session_id=SESSION_ID, activate=True,
        boot=org.authority_v2_origin_boot_id,
    )
    first = _RecordingQueue()
    assert reconcile_authority_policy_v2_post_final(
        _orch(store, first), root_task_id=TASK_ID,
    ) == POST_FINAL_RECONCILED
    assert _admit(store, outcome).status == "claimed"
    assert _raw_delete(
        store,
        "DELETE FROM audit_log WHERE action=?"
        " AND json_extract(payload,'$.stage')='generation_claimed'",
        (AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION,),
    ) == 1

    reopened = _open_org(paths)
    reopened_store = _store_from_db(reopened.db)
    queue = _RecordingQueue()
    before = _dump(reopened_store)

    assert reconcile_authority_policy_v2_post_final(
        _orch(reopened_store, queue), root_task_id=TASK_ID,
    ) == POST_FINAL_SETTLEMENT_REFUSED

    assert queue.items == []
    assert _notification(reopened_store, outcome).state == "admitted"
    assert _dump(reopened_store) == before  # preserved residue, no repair


def test_startup_publication_with_real_orgstate_beyond_32(tmp_path):
    """The PRODUCTION startup entry over a REAL OrgState covers every root.

    Seeds >32 genuinely finalized+settled roots through the real public stages
    with a server-owned boot/permission binding, leaves the earliest holding an
    authentic same-boot live lease (an ineligible early target), adds a real
    ordinary root control with NO v2 lineage, and counts PER-ROOT puts (a set
    alone can hide duplicates).  The ``limit=None`` plumbing test stays plumbing.
    """
    import collections

    from runtime.daemon.__main__ import _publish_v2_generations_on_startup
    from runtime.models import TaskRecord

    paths = _make_org_paths(tmp_path)
    org = _open_org(paths)
    store = _store_from_db(org.db)
    boot = org.authority_v2_origin_boot_id
    total = 34
    row0, outcome0 = _settled_root(
        store, task_id="TASK-M00", session_id="sess-m00", activate=True, boot=boot,
    )
    warmup = _RecordingQueue()
    assert reconcile_authority_policy_v2_post_final(
        _orch(store, warmup), root_task_id="TASK-M00",
    ) == POST_FINAL_RECONCILED
    assert len(warmup.items) == 1  # same-boot live lease, publication attempt 1
    for index in range(1, total):
        _settled_root(
            store, task_id=f"TASK-M{index:02d}", session_id=f"sess-m{index:02d}",
            activate=False, boot=boot,
        )
    store._db.insert_task(TaskRecord(
        id="TASK-ORDINARY", status=TaskStatus.PENDING, assigned_agent=MANAGER,
        team=TEAM, brief="ordinary control root", orchestration_step_count=0,
    ))
    ordinary_before = dict(store._db._conn.execute(
        "SELECT * FROM tasks WHERE id='TASK-ORDINARY'",
    ).fetchone())

    queue = _RecordingQueue()
    _publish_v2_generations_on_startup(org, queue)

    per_root = collections.Counter(item[1] for item in queue.items)
    assert per_root["TASK-M00"] == 0  # a live lease is never stolen
    assert "TASK-ORDINARY" not in per_root
    for index in range(1, total):
        assert per_root[f"TASK-M{index:02d}"] == 1
    tokens = [item[2]["authority_v2_generation"] for item in queue.items]
    assert len(set(tokens)) == total - 1  # exact root/G isolation
    assert all(item[2]["publication_attempt"] == 1 for item in queue.items)
    assert dict(store._db._conn.execute(
        "SELECT * FROM tasks WHERE id='TASK-ORDINARY'",
    ).fetchone()) == ordinary_before
    assert _notification(store, outcome0).state == "published"
    assert row0["id"] > 0


def test_current_generation_b_progresses_on_its_own_path(tmp_path):
    """Old A must not stand in for independently valid current B.

    B is produced through the real public successor lifecycle.  While B is
    current, an old-A late receipt/admission bookkeeping replay is performed and
    must be a RETURNED read-only refusal that leaves B, the task/session/step and
    every retained A row byte-identical; only then does a post-final pass publish
    B's OWN token (never A's).
    """
    from tests.test_authority_v2_decision_dispatch import (
        _drive_generation_b_pending,
        _spent_ready_b,
    )
    from tests.test_authority_v2_envelope_spend import RESERVED

    store, row_a, outcome_a, r2_row, attempt_b = _spent_ready_b(tmp_path)
    generation_b = _drive_generation_b_pending(store, r2_row, attempt_b)
    assert generation_b != outcome_a.notification_id
    store.bind_v2_process_boot_id(BOOT_A)

    def _one(sql, params):
        row = store._db._conn.execute(sql, params).fetchone()
        return dict(row) if row is not None else None

    def _a_evidence():
        return {
            "notification": _one(
                "SELECT * FROM authority_policy_v2_recovery_notifications "
                "WHERE notification_id=?", (outcome_a.notification_id,),
            ),
            "envelope": _one(
                "SELECT * FROM authority_policy_v2_continue_envelopes "
                "WHERE envelope_id=?", (outcome_a.envelope_id,),
            ),
            "attempt": _one(
                "SELECT * FROM authority_policy_v2_attempts WHERE result_id=?",
                (row_a["id"],),
            ),
            "result": _one("SELECT * FROM task_results WHERE id=?", (row_a["id"],)),
        }

    b_notification = store.get_v2_recovery_notification(generation_b)
    assert b_notification is not None
    b_envelope = store.get_v2_continue_envelope(b_notification.envelope_id)
    assert b_envelope is not None

    def _b_evidence():
        return {
            "notification": _one(
                "SELECT * FROM authority_policy_v2_recovery_notifications "
                "WHERE notification_id=?", (generation_b,),
            ),
            "envelope": _one(
                "SELECT * FROM authority_policy_v2_continue_envelopes "
                "WHERE envelope_id=?", (b_envelope.envelope_id,),
            ),
            "candidate": _one(
                "SELECT * FROM authority_policy_v2_candidates WHERE candidate_id=?",
                (b_envelope.candidate_id,),
            ),
            "dispatch": _one(
                "SELECT * FROM authority_policy_v2_root_dispatch WHERE root_task_id=?",
                (TASK_ID,),
            ),
            "task": _one("SELECT * FROM tasks WHERE id=?", (TASK_ID,)),
            "candidate_audits": [
                dict(r) for r in store._db._conn.execute(
                    "SELECT * FROM authority_policy_v2_candidate_audit "
                    "WHERE candidate_id=? ORDER BY id", (b_envelope.candidate_id,),
                ).fetchall()
            ],
        }

    a_before, b_before = _a_evidence(), _b_evidence()
    assert a_before["notification"]["state"] == "settled"
    assert b_before["dispatch"]["state"] == "pending"

    # Actual old-A recovery/late-bookkeeping replay: both durable replays are
    # RETURNED read-only refusals (`identity_mismatch`), never raised and never a
    # mutation, because the root pointer legitimately advanced to B.
    a_receipt = store.settle_v2_continuation_receipt(
        root_task_id=TASK_ID, manager_agent=MANAGER,
        manager_session_id=SESSION_ID, result_id=row_a["id"],
    )
    assert a_receipt.status == "settlement_pending"
    assert a_receipt.reason == "identity_mismatch"
    a_admission = store.settle_v2_continuation_generation_admission(
        root_task_id=TASK_ID, manager_agent=MANAGER,
        manager_session_id=SESSION_ID, result_id=row_a["id"],
        generation_id=outcome_a.notification_id, next_session_id=RESERVED,
    )
    assert a_admission.status == "settlement_pending"
    assert a_admission.reason == "identity_mismatch"
    assert _a_evidence() == a_before
    assert _b_evidence() == b_before

    queue = _RecordingQueue()
    status = reconcile_authority_policy_v2_post_final(
        _orch(store, queue), root_task_id=TASK_ID,
    )

    assert status == POST_FINAL_RECONCILED
    assert [item[2]["authority_v2_generation"] for item in queue.items] == [
        generation_b
    ]
    # B progressed on its own path; A's committed evidence, B's identity and the
    # task/session/step are all byte-identical.
    assert _a_evidence() == a_before
    after = _b_evidence()
    assert after["task"] == b_before["task"]
    assert after["dispatch"] == b_before["dispatch"]
    assert after["candidate"] == b_before["candidate"]
    assert after["candidate_audits"] == b_before["candidate_audits"]
    assert after["envelope"] == b_before["envelope"]
