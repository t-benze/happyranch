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
from tests.test_authority_v2_attempt_admission import (
    MANAGER,
    SESSION_ID,
    TASK_ID,
    TEAM,
)
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
    _point_dispatch_at_replacement,
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


def test_historical_ack_survives_replacement_generation_b(tmp_path):
    """A's exact acknowledgement must not depend on a still-current retired D.

    The replacement pointer names a REAL notification/generation B (staged by
    the accepted fixture clone); A's own settlement is authenticated from A's
    exact evidence and the durable ``spent`` audit, never from today's pointer.
    """
    store, row, outcome, r2 = _spent_ready(tmp_path)
    assert _claim(store, row).status == "claimed"
    notification = store.get_v2_recovery_notification(outcome.notification_id)
    _point_dispatch_at_replacement(
        store, notification,
        envelope_overrides={
            "spending_result_id": None, "decision_state": None,
            "lifecycle_state": "active",
        },
    )
    acked = _ack(store, row)
    assert acked.status == "applied", acked
    assert _receipt(store, r2)["decision_state"] == "applied"
    assert len(_stage_events(store, APPLIED)) == 1


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


def test_gate_terminal_lineage_returns_later_result_to_ordinary(tmp_path):
    store, row, outcome, r2 = _spent_ready(tmp_path)
    assert _claim(store, row).status == "claimed"
    assert _ack(store, row).status == "applied"
    cursor = store._db._conn.execute(
        "INSERT INTO task_results "
        "(task_id, agent, session_id, status, output_summary, decision_json, "
        "confidence_score, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (TASK_ID, MANAGER, "sess-later", "completed", "later",
         '{"action":"done"}', 70, "2026-09-21T00:00:05+00:00"),
    )
    store._db._conn.commit()
    later = cursor.lastrowid
    assert _gate(
        store, _report_for_result(store, later), later,
    ).kind == "ordinary"


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


def _fresh_dir(tmp_path, name):
    path = tmp_path / name
    path.mkdir()
    return path
