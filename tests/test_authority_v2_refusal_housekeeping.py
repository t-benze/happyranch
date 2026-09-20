"""THR-229 checkpoint C3d1 — durable pre-final refusal and exact replay.

Focused, isolated evidence for the accepted R4 pre-final refusal protocol,
driving the REAL ``Database``/``AuthorityPolicyStore`` seam (no copies of its
logic):

  * one Database-owned synchronized BEGIN IMMEDIATE refusal transaction over
    exact J/R, the current task and the optional exact recovery receipt Q;
  * terminal ``refused``/``owner_lost`` finalization retaining the greatest
    committed stage, exact immutable identities and canonical evidence;
  * still-current escalation vs preserved winning task for a losing owner;
  * live-owner safety (authentic token, old boot, server-written failed-stage
    obligation) and bounded ``housekeeping_pending`` when attribution is unsafe;
  * read-only exact replay, no repair of deleted/duplicated/mutated evidence;
  * read-only discovery including a failed claim with no candidate K;
  * caller-transaction nesting refusal and full-transaction rollback on an
    injected write/audit failure.

The broad integration suite is SKIPPED under founder THR-243 seq42, never PASS.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from runtime.infrastructure.database import Database
from runtime.models import TaskStatus
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
from tests.test_authority_v2_attempt_admission import (
    MANAGER,
    SESSION_ID,
    TASK_ID,
    _admit,
    _carrier_and_admission,
    _seed_bound_task,
    _store,
)
from tests.test_authority_v2_evaluation_stage import (
    _admitted,
    _audit_consumption,
    _audit_evaluation,
    _claim,
    _claim_audit,
    _claim_stages,
    _consume,
    _evaluate,
)


# ── helpers ──────────────────────────────────────────────────────────────


def _target_stage(store, row, stage: str):
    """Drive the REAL production methods to the named committed pre-final stage."""
    attempt = store._db.get_authority_policy_v2_attempt_for_result(row["id"])
    assert _claim(store, row, attempt).status == "claimed"
    if stage == "claimed":
        return
    assert _claim_audit(store, row, attempt).status == "claim_audited"
    if stage == "claim_audited":
        return
    assert _evaluate(store, row, attempt).status == "evaluated"
    if stage == "evaluated":
        return
    assert _audit_evaluation(store, row, attempt).status == "evaluation_audited"
    if stage == "evaluation_audited":
        return
    assert _consume(store, row, attempt).status == "consumed"
    if stage == "consumed":
        return
    assert _audit_consumption(store, row, attempt).status == "consumed_audited"


def _refuse(store, row, attempt, *, code="interrupted_pre_final", owner=None, **kw):
    return store.finalize_v2_attempt_refusal(
        root_task_id=TASK_ID, manager_agent=MANAGER,
        manager_session_id=SESSION_ID, result_id=row["id"],
        refusal_code=code,
        owner_attempt_id=attempt.owner_attempt_id if owner is None else owner,
        **kw,
    )


def _attempt_row(store, result_id):
    return store._db._conn.execute(
        "SELECT * FROM authority_policy_v2_attempts WHERE result_id=?", (result_id,)
    ).fetchone()


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
        "audit": count("audit_log"),
    }


def _stage_audits(store, result_id=None) -> list[str]:
    return [
        row["payload"]["stage"]
        for row in store._db.list_authority_policy_v2_result_stage_audits(
            root_task_id=TASK_ID, manager_agent=MANAGER,
        )
    ]


def _seed_receipt(store, result_id, *, state="callback_accepted"):
    store._db._conn.execute(
        """INSERT INTO task_completion_recoveries
           (task_id, agent, origin_session_id, recovery_session_id,
            provider_session_id, claimed_at, expires_at, state,
            accepted_result_id, accepted_result_session_id)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (TASK_ID, MANAGER, "sess-origin", SESSION_ID, "provider-1",
         "2026-01-01T00:00:00+00:00", "2999-01-01T00:00:00+00:00", state,
         result_id, SESSION_ID),
    )
    store._db._conn.commit()


def _receipt(store):
    return store._db._conn.execute(
        "SELECT * FROM task_completion_recoveries WHERE task_id=? AND agent=?",
        (TASK_ID, MANAGER),
    ).fetchone()


def _reopen(store, tmp_path, boot_id="boot-c2"):
    reopened = AuthorityPolicyStore(Database(tmp_path / "c2.db"))
    reopened.bind_v2_permission_surface_reader(lambda agent: "a" * 64)
    reopened.bind_v2_process_boot_id(boot_id)
    return reopened


class _FailingConn:
    def __init__(self, real, needle: str):
        self._real = real
        self._needle = needle

    def execute(self, sql, *args, **kwargs):
        if self._needle in sql:
            raise RuntimeError("injected write failure")
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


# ── refusal from every committed pre-final stage (ordinary Q absent) ─────


@pytest.mark.parametrize(
    "stage",
    ["admitted", "claimed", "claim_audited", "evaluated",
     "evaluation_audited", "consumed", "consumed_audited"],
)
def test_refusal_from_each_committed_stage_escalates_and_retains_stage(tmp_path, stage):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    if stage != "admitted":
        _target_stage(store, row, stage)
    before = _counts(store._db)
    staged_attempt = store._db.get_authority_policy_v2_attempt_for_result(row["id"])
    assert staged_attempt.stage == stage

    outcome = _refuse(store, row, staged_attempt)
    assert outcome.status == "refused"
    assert outcome.finalization_state == "refused"
    assert outcome.refusal_code == "interrupted_pre_final"
    assert outcome.receipt_settled is False

    # Greatest committed stage retained; J terminal; task escalated.
    final = _attempt_row(store, row["id"])
    assert final["stage"] == stage
    assert final["finalization_state"] == "refused"
    assert final["refusal_code"] == "interrupted_pre_final"
    task = store._db.get_task(TASK_ID)
    assert task.status is TaskStatus.ESCALATED
    assert task.block_kind is None
    assert "authority_v2_refusal" in (task.note or "")

    # The complete result-stage refusal event plus bounded audits exist exactly
    # once; no successor/envelope/notification/dispatch and no policy re-derivation.
    audits = _stage_audits(store)
    assert audits[-1] == "refused"
    completion = [
        a for a in store._db.get_audit_logs(TASK_ID)
        if a["action"] == "completion_report"
        and isinstance(a["payload"], dict)
        and a["payload"].get("attempt_id") == staged_attempt.attempt_id
    ]
    assert len(completion) == 1
    assert completion[0]["payload"]["refusal_code"] == "interrupted_pre_final"
    escalation = [
        a for a in store._db.get_audit_logs(TASK_ID) if a["action"] == "escalation"
    ]
    assert len(escalation) == 1
    after = _counts(store._db)
    assert after["results"] == before["results"]
    assert after["attempts"] == before["attempts"]
    assert after["evaluations"] == before["evaluations"]
    assert after["candidates"] == before["candidates"]
    if stage == "admitted":
        assert after["candidates"] == 0
        assert len(store.list_v2_candidate_audits("APV2C-" + "0" * 64)) == 0
    else:
        candidate = store.get_v2_candidate_for_result(row["id"])
        events = [a["event"] for a in store.list_v2_candidate_audits(candidate.candidate_id)]
        assert events[-1] == "refused"

    # Terminal: the finalized attempt cannot be admitted for new policy work.
    blocked = _claim(store, row, staged_attempt)
    assert blocked.status == "refused"
    assert blocked.refusal_code == "owner_lost"


def test_refusal_does_not_create_a_candidate_for_a_failed_claim(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    outcome = _refuse(store, row, attempt, code="claim_failed")
    assert outcome.status == "refused"
    assert outcome.candidate_id is None
    assert _counts(store._db)["candidates"] == 0
    assert _attempt_row(store, row["id"])["finalization_state"] == "refused"


# ── exact recovery receipt (Q) settlement in the SAME transaction ────────


def test_refusal_settles_exact_accepted_recovery_receipt(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    _seed_receipt(store, row["id"])
    assert _receipt(store)["state"] == "callback_accepted"

    outcome = _refuse(store, row, attempt)
    assert outcome.status == "refused"
    assert outcome.receipt_settled is True
    receipt = _receipt(store)
    assert receipt["state"] == "callback_consumed"
    assert receipt["accepted_result_session_id"] == SESSION_ID
    completion = [
        a for a in store._db.get_audit_logs(TASK_ID)
        if a["action"] == "completion_report"
        and isinstance(a["payload"], dict)
        and a["payload"].get("attempt_id") == attempt.attempt_id
    ]
    assert len(completion) == 1
    assert completion[0]["payload"]["_recovery_session_id"] == SESSION_ID

    # Read-only exact replay: a second call authenticates the existing evidence
    # and never mints a second audit or re-settles the receipt.
    before = _counts(store._db)
    replay = _refuse(store, row, attempt)
    assert replay.status == "already_refused"
    assert replay.refusal_code == "interrupted_pre_final"
    after = _counts(store._db)
    assert after["audit"] == before["audit"]
    assert _receipt(store)["state"] == "callback_consumed"


def test_refusal_rejects_unrelated_receipt_without_touching_it(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    # A receipt for a DIFFERENT recovery session must never be labelled
    # ordinary/absent nor settled as a replacement.
    store._db._conn.execute(
        """INSERT INTO task_completion_recoveries
           (task_id, agent, origin_session_id, recovery_session_id,
            provider_session_id, claimed_at, expires_at, state,
            accepted_result_id, accepted_result_session_id)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (TASK_ID, MANAGER, "sess-origin", "sess-other", "provider-1",
         "2026-01-01T00:00:00+00:00", "2999-01-01T00:00:00+00:00",
         "callback_accepted", row["id"], SESSION_ID),
    )
    store._db._conn.commit()

    outcome = _refuse(store, row, attempt)
    assert outcome.status == "housekeeping_pending"
    assert _receipt(store)["state"] == "callback_accepted"
    assert store._db.get_task(TASK_ID).status is TaskStatus.IN_PROGRESS
    assert _attempt_row(store, row["id"])["finalization_state"] == "unfinalized"


# ── losing owner: cancellation / terminal / replacement ─────────────────


def test_refusal_preserves_cancelled_winning_task_row(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    store._db.update_task(TASK_ID, cancelled_at="2026-01-01T00:00:00+00:00",
                          status=TaskStatus.CANCELLED)
    outcome = _refuse(store, row, attempt)
    assert outcome.status == "owner_lost"
    assert outcome.refusal_code == "cancelled"
    row_after = _attempt_row(store, row["id"])
    assert row_after["finalization_state"] == "owner_lost"
    assert row_after["stage"] == "admitted"
    task = store._db.get_task(TASK_ID)
    assert task.status is TaskStatus.CANCELLED
    assert task.cancelled_at is not None
    # No escalation audit for the losing owner.
    assert not [
        a for a in store._db.get_audit_logs(TASK_ID) if a["action"] == "escalation"
    ]


def test_refusal_records_replacement_owner_lost_without_touching_winner(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    store._db.update_task(TASK_ID, current_session_id="sess-new-owner")
    outcome = _refuse(store, row, attempt)
    assert outcome.status == "owner_lost"
    assert outcome.refusal_code == "owner_lost"
    assert _attempt_row(store, row["id"])["finalization_state"] == "owner_lost"
    task = store._db.get_task(TASK_ID)
    assert task.status is TaskStatus.IN_PROGRESS
    assert task.current_session_id == "sess-new-owner"


# ── live-owner safety ───────────────────────────────────────────────────


def test_second_database_instance_same_boot_cannot_finalize_live_winner(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    # A second Database instance in the same daemon process: no in-memory owner
    # token, same trusted boot id.  It cannot prove the uninterrupted winner is
    # dead, so it returns bounded pending and must not touch task/Q/J.
    second = _reopen(store, tmp_path, boot_id="boot-c2")
    outcome = second.finalize_v2_attempt_refusal(
        root_task_id=TASK_ID, manager_agent=MANAGER,
        manager_session_id=SESSION_ID, result_id=row["id"],
        refusal_code="interrupted_pre_final",
    )
    assert outcome.status == "housekeeping_pending"
    assert outcome.refusal_code == "owner_lost"
    assert _attempt_row(store, row["id"])["finalization_state"] == "unfinalized"
    assert store._db.get_task(TASK_ID).status is TaskStatus.IN_PROGRESS
    # The retained winner in the original instance can still advance.
    assert _claim(store, row, attempt).status == "claimed"


def test_old_boot_attempt_can_be_refused(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    store.bind_v2_process_boot_id("boot-a-new-daemon")
    store._db._v2_live_attempt_owners.clear()
    outcome = _refuse(store, row, attempt)
    assert outcome.status == "refused"
    assert _attempt_row(store, row["id"])["finalization_state"] == "refused"


def test_durable_failed_stage_obligation_authorizes_housekeeping(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    store.bind_v2_process_boot_id("boot-c2")
    # An owned-stage failure poisons the live token and leaves a durable
    # server-written obligation (bound boot == attempt origin boot).
    store._db._conn = _FailingConn(
        store._db._conn, "INSERT INTO authority_policy_v2_candidates"
    )
    with pytest.raises(RuntimeError):
        _claim(store, row, attempt)
    store._db._conn = store._db._conn._real
    obligations = [
        a for a in store._db.get_audit_logs(TASK_ID)
        if a["action"] == "authority_policy_v2_housekeeping_obligation"
    ]
    assert len(obligations) == 1
    # No live token now; only the obligation authorizes refusal.
    outcome = _refuse(store, row, attempt, owner=None)
    assert outcome.status == "refused"
    assert _attempt_row(store, row["id"])["finalization_state"] == "refused"


def test_unbound_process_context_without_token_is_pending(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    store._db._v2_live_attempt_owners.clear()
    outcome = _refuse(store, row, attempt)
    # bound boot id is still "boot-c2" from _store? The base _store does not
    # bind a process boot id, so no old-boot attribution is possible.
    assert outcome.status == "housekeeping_pending"
    assert _attempt_row(store, row["id"])["finalization_state"] == "unfinalized"


# ── discovery ───────────────────────────────────────────────────────────


def test_discovery_lists_unfinalized_attempts_including_failed_claim(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    store.bind_v2_process_boot_id("boot-c2")
    targets = store.list_v2_unfinalized_attempts()
    assert [t.attempt_id for t in targets] == [attempt.attempt_id]
    assert targets[0].candidate_id is None
    assert targets[0].obligation_code is None
    target = store.get_v2_housekeeping_target(
        root_task_id=TASK_ID, manager_agent=MANAGER,
        manager_session_id=SESSION_ID, result_id=row["id"],
    )
    assert target is not None and target.attempt_id == attempt.attempt_id

    # A failed claim with no K remains discoverable.
    store._db._conn = _FailingConn(
        store._db._conn, "INSERT INTO authority_policy_v2_candidates"
    )
    with pytest.raises(RuntimeError):
        _claim(store, row, attempt)
    store._db._conn = store._db._conn._real
    targets = store.list_v2_unfinalized_attempts()
    assert len(targets) == 1
    assert targets[0].candidate_id is None
    assert targets[0].obligation_code == "claim_failed"

    # After a successful refusal it is no longer discoverable as unfinalized.
    assert _refuse(store, row, attempt, owner=None).status == "refused"
    assert store.list_v2_unfinalized_attempts() == []


def test_discovery_read_is_nonmutating(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    before = _counts(store._db)
    store.list_v2_unfinalized_attempts()
    store.get_v2_housekeeping_target(
        root_task_id=TASK_ID, manager_agent=MANAGER,
        manager_session_id=SESSION_ID, result_id=row["id"],
    )
    after = _counts(store._db)
    assert after == before


# ── closed code allowlist / nesting / failure rollback ──────────────────


def test_unknown_refusal_code_is_rejected(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    with pytest.raises(ValueError):
        _refuse(store, row, attempt, code="free prose diagnostic")
    assert _attempt_row(store, row["id"])["finalization_state"] == "unfinalized"


def test_caller_owned_transaction_is_rejected_without_mutation(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    store._db._conn.execute("BEGIN IMMEDIATE")
    outcome = _refuse(store, row, attempt)
    assert outcome.status == "housekeeping_pending"
    assert outcome.refusal_code == "transaction_owned"
    assert store._db._conn.in_transaction
    store._db._conn.rollback()
    assert _attempt_row(store, row["id"])["finalization_state"] == "unfinalized"
    assert store._db.get_task(TASK_ID).status is TaskStatus.IN_PROGRESS


@pytest.mark.parametrize(
    "needle",
    [
        "INSERT INTO audit_log",
        "UPDATE authority_policy_v2_attempts",
        "UPDATE tasks SET status",
        "UPDATE task_completion_recoveries",
    ],
)
def test_refusal_transaction_failure_rolls_back_entire_transaction(tmp_path, needle):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    _seed_receipt(store, row["id"])
    before = _counts(store._db)
    before_audits = _stage_audits(store)
    store._db._conn = _FailingConn(store._db._conn, needle)
    with pytest.raises(RuntimeError):
        _refuse(store, row, attempt)
    store._db._conn = store._db._conn._real
    assert _attempt_row(store, row["id"])["finalization_state"] == "unfinalized"
    assert store._db.get_task(TASK_ID).status is TaskStatus.IN_PROGRESS
    assert _receipt(store)["state"] == "callback_accepted"
    assert _stage_audits(store) == before_audits
    after = _counts(store._db)
    assert after["results"] == before["results"]
    assert after["attempts"] == before["attempts"]
    assert after["candidates"] == before["candidates"]
    assert after["evaluations"] == before["evaluations"]

    # Later successful housekeeping runs exactly once, then read-only replay.
    assert _refuse(store, row, attempt).status == "refused"
    assert _refuse(store, row, attempt).status == "already_refused"


# ── terminal evidence authentication / schema guard / reopen ────────────


def _delete_refusal_stage_audit(store, attempt_id):
    store._db._conn.execute(
        """DELETE FROM audit_log WHERE action='authority_policy_v2_result_stage'
           AND json_extract(payload,'$.attempt_id')=?
           AND json_extract(payload,'$.stage')='refused'""",
        (attempt_id,),
    )
    store._db._conn.commit()


def test_deleted_terminal_evidence_never_replays_or_repairs(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    assert _refuse(store, row, attempt).status == "refused"
    _delete_refusal_stage_audit(store, attempt.attempt_id)
    before = _counts(store._db)
    outcome = _refuse(store, row, attempt)
    assert outcome.status == "housekeeping_pending"
    assert _counts(store._db)["audit"] == before["audit"]
    assert _attempt_row(store, row["id"])["finalization_state"] == "refused"


def test_deleted_completion_evidence_never_replays_or_repairs(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    assert _refuse(store, row, attempt).status == "refused"
    store._db._conn.execute(
        """DELETE FROM audit_log WHERE action='completion_report'
           AND json_extract(payload,'$.attempt_id')=?""",
        (attempt.attempt_id,),
    )
    store._db._conn.commit()
    outcome = _refuse(store, row, attempt)
    assert outcome.status == "housekeeping_pending"


def test_schema_guard_prevents_reopening_finalized_attempt(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    assert _refuse(store, row, attempt).status == "refused"
    with pytest.raises(sqlite3.IntegrityError):
        store._db._conn.execute(
            "UPDATE authority_policy_v2_attempts SET stage='claimed' WHERE attempt_id=?",
            (attempt.attempt_id,),
        )
    store._db._conn.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        store._db._conn.execute(
            "UPDATE authority_policy_v2_attempts SET finalization_state='unfinalized' "
            "WHERE attempt_id=?",
            (attempt.attempt_id,),
        )
    store._db._conn.rollback()


def test_reopen_authenticates_terminal_outcome_and_never_reopens(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    assert _refuse(store, row, attempt).status == "refused"
    reopened = _reopen(store, tmp_path)
    read = reopened.get_v2_attempt(
        root_task_id=TASK_ID, manager_agent=MANAGER,
        manager_session_id=SESSION_ID, result_id=row["id"],
    )
    assert read is not None
    assert read.finalization_state == "refused"
    assert read.refusal_code == "interrupted_pre_final"
    # Reopen does not turn durable IDs into liveness and cannot reopen J.
    replay = reopened.finalize_v2_attempt_refusal(
        root_task_id=TASK_ID, manager_agent=MANAGER,
        manager_session_id=SESSION_ID, result_id=row["id"],
        refusal_code="interrupted_pre_final",
    )
    assert replay.status == "already_refused"


def test_reopen_without_obligation_cannot_finalize_same_boot_attempt(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    reopened = _reopen(store, tmp_path, boot_id="boot-c2")
    outcome = reopened.finalize_v2_attempt_refusal(
        root_task_id=TASK_ID, manager_agent=MANAGER,
        manager_session_id=SESSION_ID, result_id=row["id"],
        refusal_code="interrupted_pre_final",
    )
    assert outcome.status == "housekeeping_pending"
    assert _attempt_row(store, row["id"])["finalization_state"] == "unfinalized"


def test_housekeeping_does_not_require_the_missing_claim_audit(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    assert _claim(store, row, attempt).status == "claimed"
    # Remove the a0 admission audit whose absence would block continuation.
    store._db._conn.execute(
        """DELETE FROM audit_log WHERE action='authority_policy_v2_result_stage'
           AND json_extract(payload,'$.stage')='admitted'"""
    )
    store._db._conn.commit()
    outcome = _refuse(store, row, attempt)
    assert outcome.status == "refused"
    assert _attempt_row(store, row["id"])["finalization_state"] == "refused"


def test_housekeeping_does_not_require_valid_assessment_or_decision(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    store._db._conn.execute(
        "UPDATE task_results SET decision_json=? WHERE id=?",
        (json.dumps({"action": "done", "broken": True}), row["id"]),
    )
    store._db._conn.commit()
    outcome = _refuse(store, row, attempt, code="evaluation_failed")
    assert outcome.status == "refused"
    assert _attempt_row(store, row["id"])["refusal_code"] == "evaluation_failed"
