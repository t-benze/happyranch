"""THR-229 checkpoint C3d2 — atomic continuation finalization and settlement.

Focused, isolated evidence for the accepted R4 finalize/settle steps 1-2,
driving the REAL ``Database``/``AuthorityPolicyStore`` seam (no copies of its
logic):

  * ONE final continuation transaction that inserts the active envelope (E),
    the final candidate/task/hook and closed ``continued`` result-stage audits,
    the needed notification (N), the pending root-dispatch generation (D),
    changes the task to Pending/null block_kind preserving the causal
    owner/session and CASes the attempt journal (J) to ``continued``;
  * the persisted V ``continue_applies`` outcome is required (never re-derived);
  * exact successful causal replay authenticates the final evidence read-only
    and never allocates another E/N/D or spends E;
  * every E/N/D/audit/update failure rolls the whole final transaction back,
    retaining the previously committed consumed K/P/V/a0..a3 and in-progress
    task, and poisons only the authentic winner for refusal-only housekeeping;
  * a SEPARATE exact post-final receipt-settlement contract for the real exact
    recovery receipt Q and for genuine ordinary completion evidence;
  * D absent and exact-retired-predecessor CAS, and pending/admitted conflicts.

The broad integration suite is SKIPPED under founder THR-243 seq42, never PASS.
"""
from __future__ import annotations

import json
import types

import pytest

from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.database import Database
from runtime.models import CompletionReport, NextStep, TaskRecord, TaskStatus
from runtime.orchestrator.active_authority_policy import (
    load_session_policy_binding,
    persist_session_policy_binding,
    resolve_active_team_policy_snapshot,
)
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
from tests.test_authority_v2_attempt_admission import (
    MANAGER,
    SESSION_ID,
    TASK_ID,
    TEAM,
    _activate_v2,
    _admit,
    _admitted as _admitted_core,
    _carrier_and_admission,
    _store,
)
from tests.test_authority_v2_evaluation_stage import (
    _admitted,
    _audit_consumption,
    _audit_evaluation,
    _claim,
    _claim_audit,
    _consume,
    _evaluate,
)

RECOVERY_SETTLED = "authority_policy_v2_recovery_settled"


# ── helpers ──────────────────────────────────────────────────────────────


def _drive(store, row, attempt, stage: str):
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


def _final_kwargs(row, attempt, *, owner_attempt_id=None, origin_boot_id=None):
    return dict(
        root_task_id=TASK_ID, manager_agent=MANAGER, manager_session_id=SESSION_ID,
        result_id=row["id"],
        origin_boot_id=attempt.origin_boot_id if origin_boot_id is None else origin_boot_id,
        owner_attempt_id=(
            attempt.owner_attempt_id if owner_attempt_id is None else owner_attempt_id
        ),
    )


def _finalize(store, row, attempt, **kw):
    return store.finalize_v2_continuation(**_final_kwargs(row, attempt, **kw))


def _settle(store, row, **kw):
    kwargs = dict(
        root_task_id=TASK_ID, manager_agent=MANAGER, manager_session_id=SESSION_ID,
        result_id=row["id"],
    )
    kwargs.update(kw)
    return store.settle_v2_continuation_receipt(**kwargs)


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
        "envelopes": count("authority_policy_v2_continue_envelopes"),
        "notifications": count("authority_policy_v2_recovery_notifications"),
        "dispatch": count("authority_policy_v2_root_dispatch"),
        "v1_envelopes": count("authority_continue_envelopes"),
        "audit": count("audit_log"),
    }


def _stage_audits(store) -> list[str]:
    return [
        row["payload"]["stage"]
        for row in store._db.list_authority_policy_v2_result_stage_audits(
            root_task_id=TASK_ID, manager_agent=MANAGER,
        )
    ]


def _payload_audits(store, action: str) -> list[dict]:
    return [
        row["payload"] for row in store._db.get_audit_logs(TASK_ID)
        if row["action"] == action and isinstance(row["payload"], dict)
    ]


def _attempt_row(store, result_id):
    return store._db._conn.execute(
        "SELECT * FROM authority_policy_v2_attempts WHERE result_id=?", (result_id,)
    ).fetchone()


def _seed_q(store, result_id, *, state="callback_accepted", recovery_session=SESSION_ID,
            accepted_result_id=None, accepted_result_session=SESSION_ID,
            origin_session="sess-origin"):
    store._db._conn.execute(
        """INSERT INTO task_completion_recoveries
           (task_id, agent, origin_session_id, recovery_session_id,
            provider_session_id, claimed_at, expires_at, state,
            accepted_result_id, accepted_result_session_id)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (TASK_ID, MANAGER, origin_session, recovery_session, "prov-1",
         "2026-01-01T00:00:00+00:00", "2999-01-01T00:00:00+00:00", state,
         result_id if accepted_result_id is None else accepted_result_id,
         accepted_result_session),
    )
    store._db._conn.commit()


def _q(store):
    return store._db._conn.execute(
        "SELECT * FROM task_completion_recoveries WHERE task_id=? AND agent=?",
        (TASK_ID, MANAGER),
    ).fetchone()


def _result_row(store, result_id):
    return store._db._conn.execute(
        "SELECT * FROM task_results WHERE id=?", (result_id,)
    ).fetchone()


class _ProducerStub:
    """Minimal host for the REAL ``Orchestrator._log_step_result`` producer."""

    def __init__(self, store):
        self._db = store._db
        self._audit = AuditLogger(store._db)


def _produce_ordinary_completion(store, result_row, *, session_id=None):
    """Write the ordinary completion audit through the REAL producer seam.

    Exercises ``Orchestrator._log_step_result`` (the production completion
    audit producer) against the real persisted result/attempt evidence rather
    than fabricating a hand-built stand-in.
    """
    from runtime.orchestrator.orchestrator import (
        Orchestrator,
        completion_report_from_result_row,
    )

    report = completion_report_from_result_row(
        TASK_ID, dict(result_row), fallback_agent=MANAGER,
    )
    stub = types.SimpleNamespace(
        session_id=session_id or result_row["session_id"],
    )
    Orchestrator._log_step_result(
        _ProducerStub(store), TASK_ID, stub, report,
        result_row_id=result_row["id"],
    )


def _insert_ordinary_completion(store, result_id):
    _produce_ordinary_completion(store, _result_row(store, result_id))


def _audit_rows(store, action: str) -> list:
    return store._db._conn.execute(
        "SELECT * FROM audit_log WHERE action=? ORDER BY id", (action,),
    ).fetchall()


def _mutate_audit_payload(store, action: str, fn, *, index: int = 0):
    rows = _audit_rows(store, action)
    payload = json.loads(rows[index]["payload"])
    fn(payload)
    store._db._conn.execute(
        "UPDATE audit_log SET payload=? WHERE id=?",
        (json.dumps(payload), rows[index]["id"]),
    )
    store._db._conn.commit()


def _duplicate_audit_payload(store, action: str, fn, *, index: int = 0):
    row = _audit_rows(store, action)[index]
    payload = json.loads(row["payload"])
    fn(payload)
    store._db._conn.execute(
        "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) "
        "VALUES (?,?,?,?,?)",
        (row["task_id"], row["agent"], row["action"], json.dumps(payload),
         row["timestamp"]),
    )
    store._db._conn.commit()


class _TargetedFailingConn:
    """Fail exactly ONE settlement write boundary (audit action or commit)."""

    def __init__(self, real, *, audit_action: str | None = None,
                 fail_commit: bool = False):
        self._real = real
        self._audit_action = audit_action
        self._fail_commit = fail_commit

    def execute(self, sql, *args, **kwargs):
        params = args[0] if args else None
        if (
            self._audit_action is not None
            and "INSERT INTO audit_log" in sql
            and isinstance(params, (tuple, list)) and len(params) >= 3
            and params[2] == self._audit_action
        ):
            raise RuntimeError("injected settlement write failure")
        return self._real.execute(sql, *args, **kwargs)

    def commit(self):
        if self._fail_commit:
            raise RuntimeError("injected commit failure")
        return self._real.commit()

    def __getattr__(self, name):
        return getattr(self._real, name)


def _reopen(tmp_path, boot_id="boot-other"):
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


def _drop_trigger(store, name: str):
    store._db._conn.execute(f"DROP TRIGGER {name}")


def _mutate_bypassing_trigger(store, *, trigger: str, statement: str, params=()):
    """Run one mutation with the named trigger removed, then restore it EXACTLY.

    The trigger SQL is captured and re-executed verbatim, so the raw schema
    digest (and therefore the frozen schema-integrity recheck) is unchanged and
    the test exercises the EVIDENCE authentication rather than schema drift.
    """
    sql = store._db._conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (trigger,)
    ).fetchone()[0]
    store._db._conn.execute(f"DROP TRIGGER {trigger}")
    store._db._conn.execute(statement, params)
    store._db._conn.execute(sql)
    store._db._conn.commit()


# ── R4 final success ─────────────────────────────────────────────────────


def test_final_continuation_commits_exact_pending_state(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    _drive(store, row, attempt, "consumed_audited")
    before = _counts(store._db)

    outcome = _finalize(store, row, attempt)
    assert outcome.status == "continued", outcome
    assert outcome.finalization_state == "continued"
    assert outcome.envelope_id is not None
    assert outcome.notification_id == outcome.generation_id

    final = _attempt_row(store, row["id"])
    assert final["stage"] == "consumed_audited"
    assert final["finalization_state"] == "continued"
    assert final["refusal_code"] is None

    task = store._db.get_task(TASK_ID)
    assert task.status is TaskStatus.PENDING
    assert task.block_kind is None
    assert task.assigned_agent == MANAGER
    assert task.current_session_id == SESSION_ID

    candidate = store.get_v2_candidate_for_result(row["id"])
    envelope = store.get_v2_continue_envelope_for_candidate(candidate.candidate_id)
    assert envelope is not None and envelope.lifecycle_state == "active"
    assert envelope.spending_result_id is None
    assert envelope.envelope_id == outcome.envelope_id
    notification = store.get_v2_recovery_notification_for_envelope(envelope.envelope_id)
    assert notification is not None and notification.state == "needed"
    assert notification.publication_attempt == 0
    assert notification.publisher_boot_id is None
    dispatch = store.get_v2_root_dispatch(TASK_ID)
    assert dispatch is not None and dispatch.state == "pending"
    assert dispatch.generation_id == outcome.generation_id
    assert dispatch.envelope_id == envelope.envelope_id
    assert dispatch.expected_manager_agent == MANAGER
    assert dispatch.expected_manager_session_id == SESSION_ID

    assert [
        a["event"] for a in store.list_v2_candidate_audits(candidate.candidate_id)
    ] == ["claimed", "evaluated", "consumed", "final"]
    assert _stage_audits(store) == [
        "admitted", "claim_audited", "evaluation_audited", "consumed_audited",
        "continued",
    ]
    assert len(_payload_audits(store, "authority_policy_v2_final_task")) == 1
    assert len(_payload_audits(store, "authority_policy_v2_final_hook")) == 1

    after = _counts(store._db)
    for key in ("results", "attempts", "candidates", "pins", "evaluations"):
        assert after[key] == before[key]
    assert after["envelopes"] == 1
    assert after["notifications"] == 1
    assert after["dispatch"] == 1
    # No v1 envelope and no receipt settlement/queue call here.
    assert after["v1_envelopes"] == 0
    assert _q(store) is None


def test_final_continuation_replay_is_read_only(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    _drive(store, row, attempt, "consumed_audited")
    assert _finalize(store, row, attempt).status == "continued"
    before = _counts(store._db)
    replay = _finalize(store, row, attempt)
    assert replay.status == "already_continued", replay
    after = _counts(store._db)
    assert after == before
    assert store._db.get_task(TASK_ID).status is TaskStatus.PENDING


def test_final_replay_after_reopen_authenticates_without_restoring_owner(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    _drive(store, row, attempt, "consumed_audited")
    assert _finalize(store, row, attempt).status == "continued"

    reopened = _reopen(tmp_path)
    task_before = reopened._db.get_task(TASK_ID)
    before = _counts(reopened._db)
    replay = reopened.finalize_v2_continuation(**_final_kwargs(row, attempt))
    assert replay.status == "already_continued", replay
    assert _counts(reopened._db) == before
    task_after = reopened._db.get_task(TASK_ID)
    assert task_after.status is TaskStatus.PENDING
    assert task_after.assigned_agent == task_before.assigned_agent
    assert task_after.current_session_id == task_before.current_session_id
    # A reopened process cannot restore pre-final ownership or remint.
    refreshed = reopened._db.get_authority_policy_v2_attempt_for_result(row["id"])
    assert refreshed.finalization_state == "continued"


def test_later_selector_activation_does_not_invalidate_pinned_final_tuple(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    store._db.bind_authority_policy_v2_process_boot_id(attempt.origin_boot_id)
    _drive(store, row, attempt, "consumed_audited")
    assert _finalize(store, row, attempt).status == "continued"
    candidate = store.get_v2_candidate_for_result(row["id"])
    envelope = store.get_v2_continue_envelope_for_candidate(candidate.candidate_id)

    selector = store.get_authority_selector("engineering")
    store.create_and_activate_v2({
        "team": "engineering", "policy_id": "engineering-dual-text",
        "title": "Dual second", "create_request_id": "c2-create-2",
        "activation_request_id": "c2-activate-2",
        "based_on_selector_id": selector.selector_id,
        "expected_selector_id": selector.selector_id,
        "action": "activate", "what_to_escalate": "Second escalate text.",
        "what_not_to_escalate": "Second continue text.",
    })
    replay = _finalize(store, row, attempt)
    assert replay.status == "already_continued", replay
    assert replay.envelope_id == envelope.envelope_id
    pinned = store.get_v2_continue_envelope(envelope.envelope_id)
    assert pinned.selector_id == envelope.selector_id
    assert pinned.release_id == envelope.release_id


def test_final_requires_persisted_continue_outcome(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    _drive(store, row, attempt, "consumed_audited")
    candidate = store.get_v2_candidate_for_result(row["id"])
    _mutate_bypassing_trigger(
        store, trigger="authority_policy_v2_evaluations_no_update",
        statement="UPDATE authority_policy_v2_evaluations SET outcome=? WHERE candidate_id=?",
        params=("escalate_applies", candidate.candidate_id),
    )

    outcome = _finalize(store, row, attempt)
    assert outcome.status == "finalization_pending", outcome
    assert outcome.reason == "evidence_drift"
    assert _counts(store._db)["envelopes"] == 0
    assert store._db.get_task(TASK_ID).status is TaskStatus.IN_PROGRESS
    assert _attempt_row(store, row["id"])["finalization_state"] == "unfinalized"


@pytest.mark.parametrize("stage", ["admitted", "claim_audited", "evaluation_audited", "consumed"])
def test_final_refuses_before_consumed_audited(tmp_path, stage):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    if stage == "admitted":
        current = attempt
    else:
        _drive(store, row, attempt, stage)
        current = store._db.get_authority_policy_v2_attempt_for_result(row["id"])
    outcome = _finalize(store, row, current)
    assert outcome.status == "finalization_pending", outcome
    assert _counts(store._db)["envelopes"] == 0
    assert _counts(store._db)["notifications"] == 0
    assert _counts(store._db)["dispatch"] == 0
    assert store._db.get_task(TASK_ID).status is TaskStatus.IN_PROGRESS


def test_final_requires_live_owner_and_valid_winner_survives(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    store._db.bind_authority_policy_v2_process_boot_id(attempt.origin_boot_id)
    _drive(store, row, attempt, "consumed_audited")

    loser = _finalize(store, row, attempt, owner_attempt_id="not-the-owner")
    assert loser.status == "finalization_pending", loser
    assert loser.reason == "owner_lost"
    assert _counts(store._db)["envelopes"] == 0

    winner = _finalize(store, row, attempt)
    assert winner.status == "continued", winner


def test_final_rejects_transaction_owned_without_touching_caller(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    _drive(store, row, attempt, "consumed_audited")
    store._db._conn.execute("BEGIN IMMEDIATE")
    try:
        outcome = _finalize(store, row, attempt)
        assert outcome.status == "finalization_pending"
        assert outcome.reason == "transaction_owned"
        assert store._db._conn.in_transaction
        assert _counts(store._db)["envelopes"] == 0
    finally:
        store._db._conn.rollback()


def test_final_rejects_ineligible_task_without_allocation(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    _drive(store, row, attempt, "consumed_audited")
    store._db._conn.execute(
        "UPDATE tasks SET active_chain=? WHERE id=?", ("[]", TASK_ID)
    )
    store._db._conn.commit()
    outcome = _finalize(store, row, attempt)
    assert outcome.status == "finalization_pending", outcome
    assert _counts(store._db)["envelopes"] == 0
    assert _attempt_row(store, row["id"])["stage"] == "consumed_audited"


def test_final_rejects_missing_prior_candidate_audit(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    _drive(store, row, attempt, "consumed_audited")
    candidate = store.get_v2_candidate_for_result(row["id"])
    _mutate_bypassing_trigger(
        store, trigger="authority_policy_v2_candidate_audit_no_delete",
        statement=(
            "DELETE FROM authority_policy_v2_candidate_audit "
            "WHERE candidate_id=? AND event=?"
        ),
        params=(candidate.candidate_id, "consumed"),
    )
    outcome = _finalize(store, row, attempt)
    assert outcome.status == "finalization_pending", outcome
    assert outcome.reason == "evidence_drift"
    assert _counts(store._db)["envelopes"] == 0


@pytest.mark.parametrize(
    "needle",
    [
        "INSERT INTO authority_policy_v2_continue_envelopes",
        "INSERT INTO authority_policy_v2_recovery_notifications",
        "INSERT INTO authority_policy_v2_root_dispatch",
        "UPDATE tasks SET status=?",
        "INSERT INTO audit_log",
        "UPDATE authority_policy_v2_attempts",
    ],
)
def test_final_rolls_back_every_write_on_injected_failure(tmp_path, needle):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    store._db.bind_authority_policy_v2_process_boot_id(attempt.origin_boot_id)
    _drive(store, row, attempt, "consumed_audited")
    before = _counts(store._db)
    candidate = store.get_v2_candidate_for_result(row["id"])

    real = store._db._conn
    store._db._conn = _FailingConn(real, needle)
    try:
        with pytest.raises(RuntimeError):
            _finalize(store, row, attempt)
    finally:
        store._db._conn = real

    after = _counts(store._db)
    assert after["envelopes"] == before["envelopes"] == 0
    assert after["notifications"] == before["notifications"] == 0
    assert after["dispatch"] == before["dispatch"] == 0
    # The only permitted new audit is the bounded best-effort failed-stage
    # obligation (and it may itself be absent when the injected failure hit the
    # audit write).  No final continuation audit is ever committed.
    obligations = [
        row for row in store._db.get_audit_logs(TASK_ID)
        if row["action"] == "authority_policy_v2_housekeeping_obligation"
    ]
    assert after["audit"] - before["audit"] == len(obligations) <= 1
    assert _payload_audits(store, "authority_policy_v2_final_task") == []
    assert _payload_audits(store, "authority_policy_v2_final_hook") == []
    assert _stage_audits(store) == [
        "admitted", "claim_audited", "evaluation_audited", "consumed_audited",
    ]
    assert _attempt_row(store, row["id"])["finalization_state"] == "unfinalized"
    assert _attempt_row(store, row["id"])["stage"] == "consumed_audited"
    assert store.get_v2_candidate(candidate.candidate_id).lifecycle_stage == "consumed"
    assert store.get_v2_evaluation(candidate.candidate_id) is not None
    assert store._db.get_task(TASK_ID).status is TaskStatus.IN_PROGRESS
    assert [
        a["event"] for a in store.list_v2_candidate_audits(candidate.candidate_id)
    ] == ["claimed", "evaluated", "consumed"]
    assert _stage_audits(store) == [
        "admitted", "claim_audited", "evaluation_audited", "consumed_audited",
    ]

    # Refusal-only housekeeping (never a remint) is the allowed next consumer.
    refused = store.finalize_v2_attempt_refusal(
        root_task_id=TASK_ID, manager_agent=MANAGER, manager_session_id=SESSION_ID,
        result_id=row["id"], refusal_code="final_commit_failed",
        owner_attempt_id=attempt.owner_attempt_id,
    )
    assert refused.status == "refused", refused
    assert refused.refusal_code == "final_commit_failed"
    assert store._db.get_task(TASK_ID).status is TaskStatus.ESCALATED
    assert _counts(store._db)["envelopes"] == 0


def test_final_cas_accepts_exact_retired_predecessor(tmp_path):
    import sqlite3

    from runtime.models import AuthorityPolicyV2RootDispatch

    store, _, _, _, row, attempt = _admitted(tmp_path)
    _drive(store, row, attempt, "consumed_audited")
    assert _finalize(store, row, attempt).status == "continued"
    first = store.get_v2_root_dispatch(TASK_ID)
    assert first.state == "pending"

    # A pending OR admitted pointer is NEVER replaced: only an exact retired
    # predecessor may be CASed to a new generation.
    for state in ("pending", "admitted"):
        store._db._conn.execute(
            "UPDATE authority_policy_v2_root_dispatch SET state=? WHERE root_task_id=?",
            (state, TASK_ID),
        )
        store._db._conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            store._db._conn.execute(
                "UPDATE authority_policy_v2_root_dispatch "
                "SET generation_id=? WHERE root_task_id=?",
                ("APV2N-" + "a" * 64, TASK_ID),
            )
        store._db._conn.rollback()
        raw = store._db._conn.execute(
            "SELECT generation_id, state FROM authority_policy_v2_root_dispatch "
            "WHERE root_task_id=?",
            (TASK_ID,),
        ).fetchone()
        assert raw["generation_id"] == first.generation_id
        assert raw["state"] == state

    # A retired predecessor is CASed in place to the new pending generation.
    store._db._conn.execute(
        "UPDATE authority_policy_v2_root_dispatch SET state='retired' WHERE root_task_id=?",
        (TASK_ID,),
    )
    store._db._conn.commit()
    new_generation = "APV2N-" + "b" * 64
    new_envelope = "APV2E-" + "e" * 64
    store._db._conn.execute("PRAGMA foreign_keys=OFF")
    try:
        store._db._conn.execute(
            "INSERT INTO authority_policy_v2_recovery_notifications "
            "(notification_id, envelope_id, candidate_id, result_id, root_task_id, "
            " manager_agent, manager_session_id, selector_id, state, "
            " publication_attempt, canonical_payload_json, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,'needed',0,'{}',?,?)",
            (new_generation, new_envelope, "APV2C-" + "c" * 64,
             row["id"], TASK_ID, MANAGER, SESSION_ID, "APS-" + "d" * 64,
             "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )
    finally:
        store._db._conn.execute("PRAGMA foreign_keys=ON")
    dispatch = AuthorityPolicyV2RootDispatch(
        root_task_id=TASK_ID, generation_id=new_generation,
        envelope_id=new_envelope, state="pending",
        expected_manager_agent=MANAGER, expected_manager_session_id=SESSION_ID,
    )
    store._db._upsert_v2_root_dispatch_pending_uncommitted(
        dispatch, prior_generation_id=first.generation_id,
    )
    store._db._conn.commit()
    updated = store.get_v2_root_dispatch(TASK_ID)
    assert updated.state == "pending"
    assert updated.generation_id == new_generation


# ── R4 settlement ────────────────────────────────────────────────────────


def test_settlement_recovery_exact_then_read_only_retry(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    _drive(store, row, attempt, "consumed_audited")
    assert _finalize(store, row, attempt).status == "continued"
    _seed_q(store, row["id"])

    settled = _settle(
        store, row, recovery_session_id=SESSION_ID,
        accepted_result_id=row["id"], accepted_result_session_id=SESSION_ID,
    )
    assert settled.status == "settled", settled
    assert settled.recovery is True and settled.receipt_settled is True
    assert _q(store)["state"] == "callback_consumed"
    completion = [
        p for p in _payload_audits(store, "completion_report")
        if p.get("_recovery_session_id") == SESSION_ID
    ]
    assert len(completion) == 1
    assert completion[0]["_result_row_id"] == row["id"]
    assert len(_payload_audits(store, "authority_policy_v2_recovery_settled")) == 1

    before = _counts(store._db)
    retry = _settle(
        store, row, recovery_session_id=SESSION_ID,
        accepted_result_id=row["id"], accepted_result_session_id=SESSION_ID,
    )
    assert retry.status == "already_settled_exact", retry
    assert retry.receipt_settled is True
    assert _counts(store._db) == before
    assert _q(store)["state"] == "callback_consumed"


def test_settlement_explicit_recovery_without_q_refuses(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    _drive(store, row, attempt, "consumed_audited")
    assert _finalize(store, row, attempt).status == "continued"
    before = _counts(store._db)
    outcome = _settle(
        store, row, recovery_session_id=SESSION_ID,
        accepted_result_id=row["id"], accepted_result_session_id=SESSION_ID,
    )
    assert outcome.status == "settlement_pending", outcome
    assert outcome.reason == "receipt_missing"
    assert _counts(store._db) == before
    assert _q(store) is None
    assert _payload_audits(store, "authority_policy_v2_recovery_settled") == []


def test_settlement_unrelated_q_is_never_ordinary(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    _drive(store, row, attempt, "consumed_audited")
    assert _finalize(store, row, attempt).status == "continued"
    _seed_q(
        store, row["id"], recovery_session="sess-other",
        accepted_result_session="sess-other",
    )
    ordinary = _settle(store, row)
    assert ordinary.status == "settlement_pending", ordinary
    assert ordinary.reason == "identity_mismatch"
    assert _q(store)["state"] == "callback_accepted"


def test_settlement_ordinary_requires_real_completion_evidence(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    _drive(store, row, attempt, "consumed_audited")
    assert _finalize(store, row, attempt).status == "continued"

    missing = _settle(store, row)
    assert missing.status == "settlement_pending", missing
    assert missing.reason == "completion_evidence_missing"

    _insert_ordinary_completion(store, row["id"])
    before = _counts(store._db)
    settled = _settle(store, row)
    assert settled.status == "settled", settled
    assert settled.recovery is False and settled.receipt_settled is False
    assert _counts(store._db) == before


@pytest.mark.parametrize(
    "needle", ["UPDATE task_completion_recoveries", "INSERT INTO audit_log"],
)
def test_settlement_write_failure_retains_q_accepted_and_final_rows(tmp_path, needle):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    _drive(store, row, attempt, "consumed_audited")
    assert _finalize(store, row, attempt).status == "continued"
    _seed_q(store, row["id"])
    before = _counts(store._db)

    real = store._db._conn
    store._db._conn = _FailingConn(real, needle)
    try:
        with pytest.raises(RuntimeError):
            _settle(
                store, row, recovery_session_id=SESSION_ID,
                accepted_result_id=row["id"],
                accepted_result_session_id=SESSION_ID,
            )
    finally:
        store._db._conn = real

    assert _q(store)["state"] == "callback_accepted"
    after = _counts(store._db)
    assert after["envelopes"] == before["envelopes"] == 1
    assert after["notifications"] == before["notifications"] == 1
    assert after["dispatch"] == before["dispatch"] == 1
    assert after["audit"] == before["audit"]
    assert store._db.get_task(TASK_ID).status is TaskStatus.PENDING

    retry = _settle(
        store, row, recovery_session_id=SESSION_ID,
        accepted_result_id=row["id"], accepted_result_session_id=SESSION_ID,
    )
    assert retry.status == "settled", retry
    assert _q(store)["state"] == "callback_consumed"


def test_settlement_consumed_q_with_missing_audit_refuses(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    _drive(store, row, attempt, "consumed_audited")
    assert _finalize(store, row, attempt).status == "continued"
    _seed_q(store, row["id"])
    assert _settle(
        store, row, recovery_session_id=SESSION_ID,
        accepted_result_id=row["id"], accepted_result_session_id=SESSION_ID,
    ).status == "settled"

    store._db._conn.execute(
        "DELETE FROM audit_log WHERE action=?",
        ("authority_policy_v2_recovery_settled",),
    )
    store._db._conn.commit()
    before = _counts(store._db)
    outcome = _settle(
        store, row, recovery_session_id=SESSION_ID,
        accepted_result_id=row["id"], accepted_result_session_id=SESSION_ID,
    )
    assert outcome.status == "settlement_pending", outcome
    assert outcome.reason == "identity_mismatch"
    assert _counts(store._db) == before


def test_settlement_requires_committed_final_continuation(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    _drive(store, row, attempt, "consumed_audited")
    before = _counts(store._db)
    outcome = _settle(store, row)
    assert outcome.status == "settlement_pending", outcome
    assert _counts(store._db) == before


def test_settlement_rejects_transaction_owned(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    _drive(store, row, attempt, "consumed_audited")
    assert _finalize(store, row, attempt).status == "continued"
    _seed_q(store, row["id"])
    store._db._conn.execute("BEGIN IMMEDIATE")
    try:
        outcome = _settle(
            store, row, recovery_session_id=SESSION_ID,
            accepted_result_id=row["id"], accepted_result_session_id=SESSION_ID,
        )
        assert outcome.status == "settlement_pending"
        assert outcome.reason == "transaction_owned"
        assert _q(store)["state"] == "callback_accepted"
    finally:
        store._db._conn.rollback()


# ── post-final refusal cannot rewrite the continued state ────────────────


def test_successful_finalization_prevents_later_refusal_rewrite(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    store._db.bind_authority_policy_v2_process_boot_id(attempt.origin_boot_id)
    _drive(store, row, attempt, "consumed_audited")
    assert _finalize(store, row, attempt).status == "continued"
    before = _counts(store._db)
    task_before = store._db.get_task(TASK_ID)

    outcome = store.finalize_v2_attempt_refusal(
        root_task_id=TASK_ID, manager_agent=MANAGER, manager_session_id=SESSION_ID,
        result_id=row["id"], refusal_code="interrupted_pre_final",
        owner_attempt_id=attempt.owner_attempt_id,
    )
    assert outcome.status == "housekeeping_pending", outcome
    assert _counts(store._db) == before
    task_after = store._db.get_task(TASK_ID)
    assert task_after.status is TaskStatus.PENDING
    assert task_after.status == task_before.status
    assert _attempt_row(store, row["id"])["finalization_state"] == "continued"


# ── C3d2 correction: complete post-final evidence on replay/settlement ────


def _drive_finalized(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    _drive(store, row, attempt, "consumed_audited")
    assert _finalize(store, row, attempt).status == "continued"
    return store, row, attempt


def _settle_recovery(store, row):
    return _settle(
        store, row, recovery_session_id=SESSION_ID,
        accepted_result_id=row["id"], accepted_result_session_id=SESSION_ID,
    )


def _delete_stage_audit(store, stage):
    store._db._conn.execute(
        "DELETE FROM audit_log WHERE action='authority_policy_v2_result_stage' "
        "AND json_extract(payload,'$.stage')=?",
        (stage,),
    )
    store._db._conn.commit()


def _insert_audit(store, action, payload):
    store._db._conn.execute(
        "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) "
        "VALUES (?,?,?,?,?)",
        (TASK_ID, MANAGER, action, json.dumps(payload), "2026-01-01T00:00:00+00:00"),
    )
    store._db._conn.commit()


def test_final_replay_and_settlement_refuse_missing_admitted_audit(tmp_path):
    store, row, attempt = _drive_finalized(tmp_path)
    _delete_stage_audit(store, "admitted")
    _seed_q(store, row["id"])
    before = _counts(store._db)

    replay = _finalize(store, row, attempt)
    assert replay.status == "finalization_pending", replay
    settle = _settle_recovery(store, row)
    assert settle.status == "settlement_pending", settle
    assert _q(store)["state"] == "callback_accepted"
    assert _counts(store._db) == before


def test_final_replay_and_settlement_refuse_changed_result_session(tmp_path):
    store, row, attempt = _drive_finalized(tmp_path)
    store._db._conn.execute(
        "UPDATE task_results SET session_id=? WHERE id=?",
        ("sess-foreign", row["id"]),
    )
    store._db._conn.commit()
    _seed_q(store, row["id"])
    before = _counts(store._db)

    replay = _finalize(store, row, attempt)
    assert replay.status == "finalization_pending", replay
    settle = _settle_recovery(store, row)
    assert settle.status == "settlement_pending", settle
    assert _q(store)["state"] == "callback_accepted"
    assert _counts(store._db) == before


def test_final_replay_refuses_mutated_result_decision(tmp_path):
    store, row, attempt = _drive_finalized(tmp_path)
    store._db._conn.execute(
        "UPDATE task_results SET decision_json=? WHERE id=?",
        (json.dumps({"action": "done"}), row["id"]),
    )
    store._db._conn.commit()
    before = _counts(store._db)
    outcome = _finalize(store, row, attempt)
    assert outcome.status == "finalization_pending", outcome
    assert _counts(store._db) == before


@pytest.mark.parametrize("event", ["claimed", "evaluated", "consumed"])
def test_final_replay_refuses_missing_candidate_prior_audit(tmp_path, event):
    store, row, attempt = _drive_finalized(tmp_path)
    candidate = store.get_v2_candidate_for_result(row["id"])
    _mutate_bypassing_trigger(
        store, trigger="authority_policy_v2_candidate_audit_no_delete",
        statement=(
            "DELETE FROM authority_policy_v2_candidate_audit "
            "WHERE candidate_id=? AND event=?"
        ),
        params=(candidate.candidate_id, event),
    )
    outcome = _finalize(store, row, attempt)
    assert outcome.status == "finalization_pending", outcome
    assert _counts(store._db)["envelopes"] == 1


@pytest.mark.parametrize(
    "stage", ["claim_audited", "evaluation_audited", "consumed_audited"]
)
def test_final_replay_refuses_missing_result_stage_audit(tmp_path, stage):
    store, row, attempt = _drive_finalized(tmp_path)
    _delete_stage_audit(store, stage)
    outcome = _finalize(store, row, attempt)
    assert outcome.status == "finalization_pending", outcome


def test_final_replay_refuses_duplicate_admitted_audit(tmp_path):
    store, row, attempt = _drive_finalized(tmp_path)
    _duplicate_audit_payload(
        store, "authority_policy_v2_result_stage", lambda payload: None, index=0,
    )
    outcome = _finalize(store, row, attempt)
    assert outcome.status == "finalization_pending", outcome


def test_final_replay_refuses_evaluation_outcome_drift(tmp_path):
    store, row, attempt = _drive_finalized(tmp_path)
    candidate = store.get_v2_candidate_for_result(row["id"])
    _mutate_bypassing_trigger(
        store, trigger="authority_policy_v2_evaluations_no_update",
        statement=(
            "UPDATE authority_policy_v2_evaluations SET outcome=? WHERE candidate_id=?"
        ),
        params=("escalate_applies", candidate.candidate_id),
    )
    outcome = _finalize(store, row, attempt)
    assert outcome.status == "finalization_pending", outcome


def test_final_replay_refuses_candidate_pin_drift(tmp_path):
    store, row, attempt = _drive_finalized(tmp_path)
    candidate = store.get_v2_candidate_for_result(row["id"])
    _mutate_bypassing_trigger(
        store, trigger="authority_policy_v2_pins_no_update",
        statement="UPDATE authority_policy_v2_pins SET model_id=? WHERE candidate_id=?",
        params=("drifted-model", candidate.candidate_id),
    )
    outcome = _finalize(store, row, attempt)
    assert outcome.status == "finalization_pending", outcome


# ── C3d2 correction: exact settlement audit contents (item 2) ─────────────


def _settled_exact(store, row):
    _seed_q(store, row["id"])
    assert _settle_recovery(store, row).status == "settled"
    assert _q(store)["state"] == "callback_consumed"


def _assert_exact_retry_refuses(store, row):
    before = _counts(store._db)
    retry = _settle_recovery(store, row)
    assert retry.status == "settlement_pending", retry
    assert _counts(store._db) == before
    assert _q(store)["state"] == "callback_consumed"


@pytest.mark.parametrize(
    "field,value",
    [
        ("envelope_id", "APV2E-" + "0" * 64),
        ("notification_id", "APV2N-" + "0" * 64),
        ("generation_id", "APV2N-" + "0" * 64),
        ("candidate_id", "APV2C-" + "0" * 64),
        ("attempt_id", "APV2R-" + "0" * 64),
        ("result_id", 999999),
        ("_result_row_id", 999999),
        ("root_task_id", "TASK-foreign"),
        ("manager_session_id", "sess-foreign"),
        ("recovery_session_id", "sess-foreign"),
    ],
)
def test_settlement_exact_retry_rejects_settled_field_mutation(
    tmp_path, field, value,
):
    store, row, _ = _drive_finalized(tmp_path)
    _settled_exact(store, row)
    _mutate_audit_payload(
        store, RECOVERY_SETTLED, lambda payload: payload.__setitem__(field, value),
    )
    _assert_exact_retry_refuses(store, row)


def test_settlement_exact_retry_rejects_settled_missing_key(tmp_path):
    store, row, _ = _drive_finalized(tmp_path)
    _settled_exact(store, row)
    _mutate_audit_payload(
        store, RECOVERY_SETTLED, lambda payload: payload.pop("generation_id"),
    )
    _assert_exact_retry_refuses(store, row)


@pytest.mark.parametrize(
    "field,value",
    [
        ("decision", {"action": "done"}),
        ("status", "failed"),
        ("confidence", True),
        ("confidence", "90"),
        ("output_summary", "tampered"),
        ("session_id", "sess-foreign"),
        ("result_id", 999999),
        ("_result_row_id", 999999),
        ("_recovery_session_id", "sess-foreign"),
    ],
)
def test_settlement_exact_retry_rejects_completion_body_mutation(
    tmp_path, field, value,
):
    store, row, _ = _drive_finalized(tmp_path)
    _settled_exact(store, row)
    _mutate_audit_payload(
        store, "completion_report",
        lambda payload: payload.__setitem__(field, value),
    )
    _assert_exact_retry_refuses(store, row)


def test_settlement_exact_retry_rejects_duplicate_settled_row(tmp_path):
    store, row, _ = _drive_finalized(tmp_path)
    _settled_exact(store, row)
    _duplicate_audit_payload(
        store, RECOVERY_SETTLED,
        lambda payload: payload.__setitem__("recovery_session_id", "sess-other"),
    )
    _assert_exact_retry_refuses(store, row)


def test_settlement_exact_retry_rejects_duplicate_completion_row(tmp_path):
    store, row, _ = _drive_finalized(tmp_path)
    _settled_exact(store, row)
    _duplicate_audit_payload(
        store, "completion_report",
        lambda payload: payload.__setitem__("_recovery_session_id", "sess-other"),
    )
    _assert_exact_retry_refuses(store, row)


def test_settlement_exact_retry_rejects_malformed_settled_payload(tmp_path):
    store, row, _ = _drive_finalized(tmp_path)
    _settled_exact(store, row)
    rows = _audit_rows(store, RECOVERY_SETTLED)
    store._db._conn.execute(
        "UPDATE audit_log SET payload=? WHERE id=?", ("[]", rows[0]["id"]),
    )
    store._db._conn.commit()
    _assert_exact_retry_refuses(store, row)


def test_settlement_exact_retry_ignores_unrelated_history(tmp_path):
    store, row, attempt = _drive_finalized(tmp_path)
    _settled_exact(store, row)
    # A genuine historical settlement for a DIFFERENT result is unrelated and
    # must never be counted as a duplicate of the current exact receipt.
    other_id = row["id"] + 1000
    _insert_audit(store, RECOVERY_SETTLED, {
        "_result_row_id": other_id, "result_id": other_id,
        "recovery_session_id": "sess-history", "attempt_id": "APV2R-" + "1" * 64,
        "candidate_id": "APV2C-" + "1" * 64, "envelope_id": "APV2E-" + "1" * 64,
        "notification_id": "APV2N-" + "1" * 64, "generation_id": "APV2N-" + "1" * 64,
        "root_task_id": TASK_ID, "manager_agent": MANAGER,
        "manager_session_id": "sess-history",
    })
    before = _counts(store._db)
    retry = _settle_recovery(store, row)
    assert retry.status == "already_settled_exact", retry
    assert _counts(store._db) == before


# ── C3d2 correction: initial settlement pre-state (item 3) ────────────────


def _settlement_evidence_payloads(store, row):
    attempt = store.get_v2_attempt_for_result(row["id"])
    candidate = store.get_v2_candidate_for_result(row["id"])
    envelope = store.get_v2_continue_envelope_for_candidate(candidate.candidate_id)
    notification = store.get_v2_recovery_notification_for_envelope(envelope.envelope_id)
    settled = store._db._v2_settlement_settled_payload(
        attempt=attempt, candidate=candidate, envelope=envelope,
        notification=notification,
    )
    completion = store._db._v2_settlement_completion_payload(
        root_task_id=TASK_ID, manager_agent=MANAGER,
        result_row=_result_row(store, row["id"]), result_id=row["id"],
        manager_session_id=SESSION_ID,
    )
    return settled, completion


@pytest.mark.parametrize(
    "kind", ["garbled_completion", "exact_completion", "settled_only", "both"],
)
def test_settlement_initial_refuses_preexisting_settlement_evidence(tmp_path, kind):
    store, row, attempt = _drive_finalized(tmp_path)
    _seed_q(store, row["id"])
    settled, completion = _settlement_evidence_payloads(store, row)
    if kind == "garbled_completion":
        _insert_audit(store, "completion_report", {
            "_recovery_session_id": SESSION_ID, "_result_row_id": row["id"],
            "status": "garbled",
        })
    if kind in ("exact_completion", "both"):
        _insert_audit(store, "completion_report", completion)
    if kind in ("settled_only", "both"):
        _insert_audit(store, RECOVERY_SETTLED, settled)
    before = _counts(store._db)

    outcome = _settle_recovery(store, row)
    assert outcome.status == "settlement_pending", outcome
    assert _q(store)["state"] == "callback_accepted"
    assert _counts(store._db) == before
    assert _attempt_row(store, row["id"])["finalization_state"] == "continued"


def test_settlement_initial_ignores_unrelated_history(tmp_path):
    store, row, attempt = _drive_finalized(tmp_path)
    _seed_q(store, row["id"])
    other_id = row["id"] + 1000
    _insert_audit(store, "completion_report", {
        "_recovery_session_id": "sess-history", "_result_row_id": other_id,
        "status": "completed",
    })
    _insert_audit(store, RECOVERY_SETTLED, {
        "_result_row_id": other_id, "result_id": other_id,
        "recovery_session_id": "sess-history", "attempt_id": "APV2R-" + "1" * 64,
        "candidate_id": "APV2C-" + "1" * 64, "envelope_id": "APV2E-" + "1" * 64,
        "notification_id": "APV2N-" + "1" * 64, "generation_id": "APV2N-" + "1" * 64,
        "root_task_id": TASK_ID, "manager_agent": MANAGER,
        "manager_session_id": "sess-history",
    })
    before = _counts(store._db)
    settled = _settle_recovery(store, row)
    assert settled.status == "settled", settled
    assert _q(store)["state"] == "callback_consumed"
    assert _counts(store._db)["audit"] == before["audit"] + 2


@pytest.mark.parametrize(
    "mode", ["completion_audit", "settled_audit", "commit"],
)
def test_settlement_targeted_write_failure_retains_q_and_retries(tmp_path, mode):
    store, row, attempt = _drive_finalized(tmp_path)
    _seed_q(store, row["id"])
    before = _counts(store._db)

    real = store._db._conn
    if mode == "commit":
        store._db._conn = _TargetedFailingConn(real, fail_commit=True)
    else:
        action = "completion_report" if mode == "completion_audit" else RECOVERY_SETTLED
        store._db._conn = _TargetedFailingConn(real, audit_action=action)
    try:
        with pytest.raises(RuntimeError):
            _settle_recovery(store, row)
    finally:
        store._db._conn = real

    assert _q(store)["state"] == "callback_accepted"
    after = _counts(store._db)
    assert after["audit"] == before["audit"]
    assert after["envelopes"] == before["envelopes"] == 1
    assert after["notifications"] == before["notifications"] == 1
    assert after["dispatch"] == before["dispatch"] == 1
    assert store._db.get_task(TASK_ID).status is TaskStatus.PENDING
    assert _attempt_row(store, row["id"])["finalization_state"] == "continued"

    retry = _settle_recovery(store, row)
    assert retry.status == "settled", retry
    assert _q(store)["state"] == "callback_consumed"


# ── C3d2 correction: ordinary completion evidence scope (item 4) ──────────


def _prior_manager_completion(store, *, summary="Earlier legitimate manager turn"):
    AuditLogger(store._db).log_completion_report(CompletionReport(
        task_id=TASK_ID, agent=MANAGER, status="completed",
        output_summary=summary, confidence=75,
        decision=NextStep(action="delegate", agent="dev_agent", prompt="earlier work"),
    ))


def test_settlement_ordinary_accepts_current_after_prior_manager_history(tmp_path):
    store, row, attempt = _drive_finalized(tmp_path)
    # A prior attributed completion whose result AND session references are both
    # well-typed and DIFFERENT is provably unrelated history.  (A distinct result
    # id carrying the EXACT current session is conflicting evidence, not
    # unrelated history — see the Cartesian relatedness matrix above.)
    _produce_ordinary_completion(store, _result_row(store, row["id"]))

    def _retarget_prior(payload):
        payload["_result_row_id"] = row["id"] + 1000
        payload["_result_session_id"] = "sess-other"

    _mutate_audit_payload(store, "completion_report", _retarget_prior, index=0)
    _prior_manager_completion(store)
    _insert_ordinary_completion(store, row["id"])

    before = _counts(store._db)
    settled = _settle(store, row)
    assert settled.status == "settled", settled
    assert settled.recovery is False and settled.receipt_settled is False
    assert _counts(store._db) == before
    # Unrelated historical evidence remains intact and readable.
    assert len(_payload_audits(store, "completion_report")) == 3


def test_settlement_ordinary_refuses_identical_old_body(tmp_path):
    store, row, attempt = _drive_finalized(tmp_path)
    _insert_ordinary_completion(store, row["id"])
    # The ONLY attributed row is retargeted to a foreign result: the current
    # session has no exact current audit, and an identical body must not match.
    _mutate_audit_payload(
        store, "completion_report",
        lambda payload: payload.__setitem__("_result_row_id", row["id"] + 1000),
    )
    before = _counts(store._db)
    outcome = _settle(store, row)
    assert outcome.status == "settlement_pending", outcome
    assert outcome.reason == "completion_evidence_missing"
    assert _counts(store._db) == before


def test_settlement_ordinary_refuses_duplicate_current_audit(tmp_path):
    store, row, attempt = _drive_finalized(tmp_path)
    _insert_ordinary_completion(store, row["id"])
    _duplicate_audit_payload(store, "completion_report", lambda payload: None)
    outcome = _settle(store, row)
    assert outcome.status == "settlement_pending", outcome


def test_settlement_ordinary_refuses_malformed_current_audit(tmp_path):
    store, row, attempt = _drive_finalized(tmp_path)
    _insert_ordinary_completion(store, row["id"])
    _mutate_audit_payload(
        store, "completion_report", lambda payload: payload.pop("status"),
    )
    outcome = _settle(store, row)
    assert outcome.status == "settlement_pending", outcome
    assert outcome.reason == "completion_evidence_missing"


def test_settlement_ordinary_refuses_recovery_shaped_current_without_q(tmp_path):
    store, row, attempt = _drive_finalized(tmp_path)
    _insert_ordinary_completion(store, row["id"])
    _insert_audit(store, "completion_report", {
        "_recovery_session_id": SESSION_ID, "_result_row_id": row["id"],
        "status": "completed",
    })
    outcome = _settle(store, row)
    assert outcome.status == "settlement_pending", outcome
    assert outcome.reason == "completion_evidence_missing"


# ── C3d2 evidence-classification correction: presence BEFORE filtering ────
#
# The manager's five step22 production-method observations are converted into
# asserting RED-at-9ed5c894 / GREEN regressions.  Every potentially related row
# is classified from FIELD PRESENCE: a present identity field that matches the
# exact causal identity OR is malformed is never ordinary absence, and only a
# row whose every present identity is well-typed and provably DIFFERENT is
# independently established as unrelated.

_MARKER_MISSING = object()


def _insert_raw_audit(store, action, raw_payload, *, agent=MANAGER):
    """Insert one audit row with a raw (possibly non-object) JSON payload."""
    store._db._conn.execute(
        "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) "
        "VALUES (?,?,?,?,?)",
        (TASK_ID, agent, action, raw_payload, "2026-01-01T00:00:00+00:00"),
    )
    store._db._conn.commit()


def _replace_audit_payload(store, action, raw_payload, *, index=0):
    rows = _audit_rows(store, action)
    store._db._conn.execute(
        "UPDATE audit_log SET payload=? WHERE id=?", (raw_payload, rows[index]["id"]),
    )
    store._db._conn.commit()


# Observation 1: duplicate settled completion with a null/malformed recovery
# marker and the exact integer result reference must not vanish.
@pytest.mark.parametrize(
    "marker",
    [_MARKER_MISSING, None, True, False, 7, ["x"], {"k": 1}, "sess-foreign"],
)
def test_settlement_exact_retry_rejects_malformed_recovery_marker(tmp_path, marker):
    store, row, _ = _drive_finalized(tmp_path)
    _settled_exact(store, row)

    def mutate(payload):
        if marker is _MARKER_MISSING:
            payload.pop("_recovery_session_id", None)
        else:
            payload["_recovery_session_id"] = marker

    _duplicate_audit_payload(store, "completion_report", mutate)
    _assert_exact_retry_refuses(store, row)


# Observation 2: a malformed completion before initial accepted-Q settlement
# must refuse (never add two audits and consume Q).
@pytest.mark.parametrize(
    "marker", [_MARKER_MISSING, None, True, 7, ["x"], "sess-foreign"],
)
def test_settlement_initial_rejects_malformed_recovery_marker(tmp_path, marker):
    store, row, attempt = _drive_finalized(tmp_path)
    _seed_q(store, row["id"])
    garbled = {
        "_result_row_id": row["id"], "result_id": row["id"],
        "session_id": SESSION_ID, "status": "garbled",
    }
    if marker is not _MARKER_MISSING:
        garbled["_recovery_session_id"] = marker
    _insert_audit(store, "completion_report", garbled)
    before = _counts(store._db)

    outcome = _settle_recovery(store, row)
    assert outcome.status == "settlement_pending", outcome
    assert _q(store)["state"] == "callback_accepted"
    assert _counts(store._db) == before
    assert _attempt_row(store, row["id"])["finalization_state"] == "continued"


# Observation 3: duplicate recovery-settled row whose result references are
# mistyped while every other exact identity survives must refuse.
_SETTLED_IDENTITY_MUTATIONS = [
    ("_result_row_id", "str"), ("result_id", "str"),
    ("attempt_id", None), ("attempt_id", 12), ("candidate_id", ["x"]),
    ("envelope_id", True), ("notification_id", []), ("generation_id", {"a": 1}),
    ("recovery_session_id", None), ("recovery_session_id", 5),
    ("manager_session_id", False),
]


@pytest.mark.parametrize("field,kind", _SETTLED_IDENTITY_MUTATIONS)
def test_settlement_exact_retry_rejects_malformed_settled_identities(
    tmp_path, field, kind,
):
    store, row, _ = _drive_finalized(tmp_path)
    _settled_exact(store, row)

    def mutate(payload):
        payload[field] = "1" if kind == "str" else kind

    _duplicate_audit_payload(store, RECOVERY_SETTLED, mutate)
    _assert_exact_retry_refuses(store, row)


@pytest.mark.parametrize(
    "field",
    ["_result_row_id", "attempt_id", "candidate_id", "envelope_id",
     "notification_id", "generation_id", "recovery_session_id"],
)
def test_settlement_exact_retry_rejects_missing_settled_identity(tmp_path, field):
    store, row, _ = _drive_finalized(tmp_path)
    _settled_exact(store, row)
    _duplicate_audit_payload(
        store, RECOVERY_SETTLED, lambda payload: payload.pop(field, None),
    )
    _assert_exact_retry_refuses(store, row)


# Observation 3 (exact production probe): BOTH result references stringified
# while attempt/candidate/envelope/generation/session stay exact.
def test_settlement_exact_retry_rejects_mistyped_settled_result_refs(tmp_path):
    store, row, _ = _drive_finalized(tmp_path)
    _settled_exact(store, row)
    _duplicate_audit_payload(
        store, RECOVERY_SETTLED,
        lambda p: p.update(
            _result_row_id=str(p["_result_row_id"]), result_id=str(p["result_id"]),
        ),
    )
    _assert_exact_retry_refuses(store, row)


# Observation 4 (exact production probe): an ordinary duplicate whose
# ``_result_row_id`` is stringified while the exact ``_result_session_id``
# survives is still current-result evidence.
def test_settlement_ordinary_refuses_mistyped_result_ref_with_exact_session(tmp_path):
    store, row, _ = _drive_finalized(tmp_path)
    _insert_ordinary_completion(store, row["id"])
    _duplicate_audit_payload(
        store, "completion_report",
        lambda p: p.update(_result_row_id=str(p["_result_row_id"])),
    )
    before = _counts(store._db)
    outcome = _settle(store, row)
    assert outcome.status == "settlement_pending", outcome
    assert outcome.reason == "completion_evidence_missing", outcome
    assert _counts(store._db) == before


# Completion-row partial/conflicting identity: a removed or malformed result
# reference with an exact recovery/session identity must still refuse.
@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.pop("_result_row_id", None),
        lambda p: p.update(_result_row_id=999999),
        lambda p: p.update(_result_row_id="1", _recovery_session_id="sess-other"),
        lambda p: p.update(result_id="1", _result_row_id=999999),
        lambda p: (p.pop("_result_row_id", None), p.pop("result_id", None)),
    ],
)
def test_settlement_exact_retry_rejects_conflicting_completion_identities(
    tmp_path, mutate,
):
    store, row, _ = _drive_finalized(tmp_path)
    _settled_exact(store, row)
    _duplicate_audit_payload(store, "completion_report", mutate)
    _assert_exact_retry_refuses(store, row)


# Observation 4 (and the raw non-object body matrix): opaque bodies are never
# ordinary absence.
@pytest.mark.parametrize("raw", ['"garbled"', "[1, 2]", "42"])
def test_settlement_exact_retry_rejects_opaque_duplicate_bodies(tmp_path, raw):
    store, row, _ = _drive_finalized(tmp_path)
    _settled_exact(store, row)
    _insert_raw_audit(store, "completion_report", raw)
    _assert_exact_retry_refuses(store, row)
    _insert_raw_audit(store, RECOVERY_SETTLED, raw)
    _assert_exact_retry_refuses(store, row)


@pytest.mark.parametrize("raw", ['"garbled"', "[1, 2]", "42", "null"])
@pytest.mark.parametrize("action", ["completion_report", RECOVERY_SETTLED])
def test_settlement_exact_retry_rejects_lone_opaque_body(tmp_path, action, raw):
    store, row, _ = _drive_finalized(tmp_path)
    _settled_exact(store, row)
    _replace_audit_payload(store, action, raw)
    _assert_exact_retry_refuses(store, row)


@pytest.mark.parametrize(
    "action,raw",
    [("completion_report", '"garbled"'), ("completion_report", "[1]"),
     (RECOVERY_SETTLED, "42"), (RECOVERY_SETTLED, "null")],
)
def test_settlement_initial_rejects_opaque_evidence(tmp_path, action, raw):
    store, row, attempt = _drive_finalized(tmp_path)
    _seed_q(store, row["id"])
    _insert_raw_audit(store, action, raw)
    before = _counts(store._db)
    outcome = _settle_recovery(store, row)
    assert outcome.status == "settlement_pending", outcome
    assert _q(store)["state"] == "callback_accepted"
    assert _counts(store._db) == before


# Positive coexistence: the legitimate ordinary producer audit and the
# recovery-owned settlement evidence must both survive without a false duplicate.
def test_settlement_exact_retry_accepts_ordinary_plus_recovery_evidence(tmp_path):
    store, row, attempt = _drive_finalized(tmp_path)
    _insert_ordinary_completion(store, row["id"])
    _seed_q(store, row["id"])
    assert _settle_recovery(store, row).status == "settled"
    before = _counts(store._db)
    retry = _settle_recovery(store, row)
    assert retry.status == "already_settled_exact", retry
    assert _counts(store._db) == before
    assert len(_payload_audits(store, "completion_report")) == 2


# Independent unrelated history still succeeds read-only (unchanged retry).
def test_settlement_exact_retry_ignores_malformed_foreign_completion(tmp_path):
    store, row, _ = _drive_finalized(tmp_path)
    _settled_exact(store, row)
    _insert_audit(store, "completion_report", {
        "_recovery_session_id": "sess-history", "_result_row_id": 987654,
        "result_id": 987654, "session_id": "sess-history", "status": "completed",
    })
    before = _counts(store._db)
    retry = _settle_recovery(store, row)
    assert retry.status == "already_settled_exact", retry
    assert _counts(store._db) == before


# ── C3d2 receipt classification: unrelated terminal history is not a veto ──


def _insert_q(
    store, *, recovery_session, origin_session="sess-origin",
    state="callback_consumed", accepted_result_id=None,
    accepted_result_session=None, settled=True,
):
    store._db._conn.execute(
        """INSERT INTO task_completion_recoveries
           (task_id, agent, origin_session_id, recovery_session_id,
            provider_session_id, claimed_at, expires_at, state,
            accepted_result_id, accepted_result_session_id, settled_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (TASK_ID, MANAGER, origin_session, recovery_session, "prov-1",
         "2026-01-01T00:00:00+00:00", "2999-01-01T00:00:00+00:00", state,
         accepted_result_id, accepted_result_session,
         "2026-01-01T00:05:00+00:00" if settled else None),
    )
    store._db._conn.commit()


def _q_rows(store):
    return store._db._conn.execute(
        "SELECT * FROM task_completion_recoveries WHERE task_id=? AND agent=? ORDER BY id",
        (TASK_ID, MANAGER),
    ).fetchall()


def _prepare_ordinary_current(store, row):
    _insert_ordinary_completion(store, row["id"])


def test_settlement_ordinary_accepts_unrelated_terminal_receipt(tmp_path):
    store, row, attempt = _drive_finalized(tmp_path)
    _prepare_ordinary_current(store, row)
    _insert_q(
        store, recovery_session="sess-hist",
        accepted_result_id=row["id"] + 1000,
        accepted_result_session="sess-hist",
    )
    before = _counts(store._db)
    q_before = [tuple(r) for r in _q_rows(store)]
    audit_before = _audit_rows(store, RECOVERY_SETTLED)
    outcome = _settle(store, row)
    assert outcome.status == "settled", outcome
    assert outcome.recovery is False and outcome.receipt_settled is False
    assert _counts(store._db) == before
    assert [tuple(r) for r in _q_rows(store)] == q_before
    assert _audit_rows(store, RECOVERY_SETTLED) == audit_before


def test_settlement_ordinary_accepts_unrelated_terminal_receipt_after_reopen(tmp_path):
    store, row, attempt = _drive_finalized(tmp_path)
    _prepare_ordinary_current(store, row)
    _insert_q(
        store, recovery_session="sess-hist", origin_session="sess-origin",
        accepted_result_id=row["id"] + 1000,
        accepted_result_session="sess-hist",
    )
    before = _counts(store._db)
    store._db._conn.commit()
    store._db.close()
    reopened = _reopen(tmp_path)
    reopened_row = _result_row(reopened, row["id"])
    outcome = _settle(reopened, reopened_row)
    assert outcome.status == "settled", outcome
    assert _counts(reopened._db) == before


@pytest.mark.parametrize(
    "overrides,state",
    [
        # exact current recovery session (accepted result is foreign)
        ({"recovery_session": SESSION_ID, "accepted_result": "FOREIGN",
          "accepted_result_session": "sess-r"}, "callback_consumed"),
        # exact current accepted result (sessions are disjoint)
        ({"recovery_session": "sess-r", "accepted_result": "CURRENT",
          "accepted_result_session": "sess-r"}, "callback_consumed"),
        # exact current accepted-result session (accepted result is foreign)
        ({"recovery_session": "sess-r", "accepted_result": "FOREIGN",
          "accepted_result_session": SESSION_ID}, "superseded"),
        # exact current origin session (accepted result is foreign)
        ({"recovery_session": "sess-r", "origin_session": SESSION_ID,
          "accepted_result": "FOREIGN", "accepted_result_session": "sess-r"},
         "expired"),
        # nonterminal states cannot be established as unrelated
        ({"recovery_session": "sess-r", "accepted_result": "FOREIGN",
          "accepted_result_session": "sess-r"}, "claimed"),
        ({"recovery_session": "sess-r", "accepted_result": "FOREIGN",
          "accepted_result_session": "sess-r"}, "callback_accepted"),
        # unknown state fails closed
        ({"recovery_session": "sess-r", "accepted_result": "FOREIGN",
          "accepted_result_session": "sess-r"}, "unknown_state"),
    ],
)
def test_settlement_ordinary_refuses_related_receipt(tmp_path, overrides, state):
    store, row, attempt = _drive_finalized(tmp_path)
    _prepare_ordinary_current(store, row)
    overrides = dict(overrides)
    accepted = overrides.pop("accepted_result")
    overrides["accepted_result_id"] = (
        row["id"] if accepted == "CURRENT" else row["id"] + 1000
    )
    _insert_q(
        store, state=state,
        settled=(state not in {"claimed", "callback_accepted"}),
        **overrides,
    )
    before = _counts(store._db)
    outcome = _settle(store, row)
    assert outcome.status == "settlement_pending", outcome
    assert outcome.reason == "identity_mismatch", outcome
    assert _counts(store._db) == before


def test_settlement_ordinary_refuses_duplicate_related_receipts(tmp_path):
    store, row, attempt = _drive_finalized(tmp_path)
    _prepare_ordinary_current(store, row)
    _insert_q(
        store, recovery_session="sess-r1", origin_session="sess-o1",
        accepted_result_id=row["id"], accepted_result_session="sess-r1",
    )
    _insert_q(
        store, recovery_session="sess-r2", origin_session="sess-o2",
        accepted_result_id=row["id"], accepted_result_session="sess-r2",
    )
    outcome = _settle(store, row)
    assert outcome.status == "settlement_pending", outcome
    assert outcome.reason == "identity_mismatch", outcome


def test_settlement_ordinary_refuses_malformed_related_receipt(tmp_path):
    store, row, attempt = _drive_finalized(tmp_path)
    _prepare_ordinary_current(store, row)
    store._db._conn.execute(
        """INSERT INTO task_completion_recoveries
           (task_id, agent, origin_session_id, recovery_session_id,
            provider_session_id, claimed_at, expires_at, state,
            accepted_result_id, accepted_result_session_id, settled_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (TASK_ID, MANAGER, "sess-o", "sess-r", "prov-1",
         "2026-01-01T00:00:00+00:00", "2999-01-01T00:00:00+00:00",
         "callback_consumed", str(row["id"]), "sess-r",
         "2026-01-01T00:05:00+00:00"),
    )
    store._db._conn.commit()
    outcome = _settle(store, row)
    assert outcome.status == "settlement_pending", outcome
    assert outcome.reason == "identity_mismatch", outcome


def test_settlement_explicit_recovery_with_unrelated_history_refuses(tmp_path):
    store, row, attempt = _drive_finalized(tmp_path)
    _insert_q(
        store, recovery_session="sess-hist",
        accepted_result_id=row["id"] + 1000,
        accepted_result_session="sess-hist",
    )
    before = _counts(store._db)
    outcome = _settle_recovery(store, row)
    assert outcome.status == "settlement_pending", outcome
    assert outcome.reason == "receipt_missing", outcome
    assert _counts(store._db) == before
    assert _q(store)["state"] == "callback_consumed"


def test_settlement_ordinary_refuses_exact_current_consumed_receipt(tmp_path):
    store, row, attempt = _drive_finalized(tmp_path)
    _prepare_ordinary_current(store, row)
    _insert_q(
        store, recovery_session=SESSION_ID, origin_session="sess-origin",
        accepted_result_id=row["id"], accepted_result_session=SESSION_ID,
    )
    outcome = _settle(store, row)
    assert outcome.status == "settlement_pending", outcome
    assert outcome.reason == "identity_mismatch", outcome


# Realistic chronology: a genuine terminal consumed recovery receipt produced
# through the supported claim -> publish -> admit -> consume seams for a
# different origin/recovery session, followed by a later ordinary manager result
# on the same root, must not veto that ordinary completion.
def _drive_realistic_terminal_history(tmp_path):
    store = _store(tmp_path)
    _activate_v2(store)
    store._db.insert_task(TaskRecord(
        id=TASK_ID, status=TaskStatus.IN_PROGRESS, assigned_agent=MANAGER,
        team=TEAM, brief="historical recovery chronology", orchestration_step_count=1,
    ))
    store._db.update_task(TASK_ID, current_session_id="sess-origin")
    assert store._db.claim_task_completion_recovery(
        task_id=TASK_ID, agent=MANAGER, origin_session_id="sess-origin",
        recovery_session_id="sess-hist-recovery", provider_session_id="prov-1",
        claimed_at="2026-01-01T00:00:00+00:00",
        expires_at="2999-01-01T00:00:00+00:00",
    )
    assert store._db.publish_task_completion_recovery_binding(
        task_id=TASK_ID, agent=MANAGER, origin_session_id="sess-origin",
        recovery_session_id="sess-hist-recovery",
    )
    assert store._db.admit_task_completion_callback(
        task_id=TASK_ID, agent=MANAGER, session_id="sess-hist-recovery",
        output_summary="historical recovery result", confidence_score=70,
        decision_json=json.dumps({"action": "escalate", "reason": "historical"}),
    )
    historical = store._db.get_latest_task_result(
        TASK_ID, MANAGER, "sess-hist-recovery",
    )
    store._db.update_task(TASK_ID, status=TaskStatus.COMPLETED)
    assert store._db.mark_task_completion_recovery_callback_consumed(
        task_id=TASK_ID, agent=MANAGER, session_id="sess-hist-recovery",
        result_row_id=historical["id"], settled_at="2026-01-01T00:05:00+00:00",
    )
    receipt = _q(store)
    assert receipt["state"] == "callback_consumed"
    assert receipt["recovery_session_id"] == "sess-hist-recovery"
    assert receipt["accepted_result_id"] == historical["id"]

    store._db.update_task(
        TASK_ID, status=TaskStatus.IN_PROGRESS, current_session_id=SESSION_ID,
    )
    snapshot = resolve_active_team_policy_snapshot(
        store=store, team=TEAM, agent_name=MANAGER, eligible=True,
    )
    assert snapshot is not None and snapshot.family == "v2"
    persist_session_policy_binding(
        db=store._db, task_id=TASK_ID, session_id=SESSION_ID, agent_name=MANAGER,
        snapshot=snapshot, provider_id="codex", executor_kind="codex",
        model_id="default",
    )
    binding = load_session_policy_binding(
        db=store._db, task_id=TASK_ID, session_id=SESSION_ID, agent_name=MANAGER,
    )
    carrier, admission = _carrier_and_admission(binding)
    assert _admit(store, carrier, admission) is True
    row = store._db.get_latest_task_result(TASK_ID, MANAGER, SESSION_ID)
    attempt = store._db.get_authority_policy_v2_attempt_for_result(row["id"])
    assert attempt is not None and attempt.result_id != historical["id"]
    return store, row, attempt, historical


def test_settlement_ordinary_succeeds_after_real_terminal_history(tmp_path):
    store, row, attempt, historical = _drive_realistic_terminal_history(tmp_path)
    _drive(store, row, attempt, "consumed_audited")
    assert _finalize(store, row, attempt).status == "continued"
    _prepare_ordinary_current(store, row)
    before = _counts(store._db)
    q_before = [tuple(r) for r in _q_rows(store)]
    audit_before = _audit_rows(store, RECOVERY_SETTLED)

    outcome = _settle(store, row)
    assert outcome.status == "settled", outcome
    assert outcome.recovery is False and outcome.receipt_settled is False
    assert _counts(store._db) == before
    assert [tuple(r) for r in _q_rows(store)] == q_before
    assert _audit_rows(store, RECOVERY_SETTLED) == audit_before


def test_settlement_ordinary_refuses_old_body_without_current_audit(tmp_path):
    store, row, attempt = _drive_finalized(tmp_path)
    _insert_ordinary_completion(store, row["id"])
    _mutate_audit_payload(
        store, "completion_report",
        lambda payload: payload.__setitem__("_result_row_id", row["id"] + 1000),
    )
    before = _counts(store._db)
    outcome = _settle(store, row)
    assert outcome.status == "settlement_pending", outcome
    assert outcome.reason == "completion_evidence_missing", outcome
    assert _counts(store._db) == before


# ── C3d3a prerequisite: ordinary-evidence Cartesian relatedness matrix ────
#
# The public settlement boundary classifies an ordinary producer completion row
# from FIELD PRESENCE over the exact causal tuple.  The result reference
# (exact / distinct / missing / malformed) is crossed with the session reference
# (exact / distinct / missing / malformed).  A row is identity-RELATED whenever
# ANY present reference matches the exact current result/session or is malformed;
# it is provably UNRELATED only when every present reference is well-typed and a
# different value.  A DISTINCT result reference never vetoes a surviving exact or
# malformed session reference — the exact defect manager step23-probe.py
# observations (a) and (b) demonstrate (a well-typed-but-conflicting result id
# with the exact current session, and with a null session, were both wrongly
# accepted as ordinary evidence).

_RESULT_REFERENCE_CASES = {
    "exact": lambda result_id, session_id: {"_result_row_id": result_id},
    "distinct": lambda result_id, session_id: {"_result_row_id": result_id + 999999},
    "missing": lambda result_id, session_id: {},
    "malformed": lambda result_id, session_id: {"_result_row_id": None},
}
_SESSION_REFERENCE_CASES = {
    "exact": lambda result_id, session_id: {"_result_session_id": session_id},
    "distinct": lambda result_id, session_id: {"_result_session_id": "sess-other"},
    "missing": lambda result_id, session_id: {},
    "malformed": lambda result_id, session_id: {"_result_session_id": None},
}
# Every PRESENT reference is well-typed and provably a different value; no
# present reference is matching or malformed, so the row is independently
# unrelated history (never broadened into a valid evidence row).
_PROVABLY_UNRELATED_REFERENCE_COMBINATIONS = frozenset({
    ("distinct", "distinct"),
    ("distinct", "missing"),
    ("missing", "distinct"),
    ("missing", "missing"),
})


def _mutate_ordinary_completion_references(store, row, result_state, session_state):
    def mutate(payload):
        payload.pop("_result_row_id", None)
        payload.pop("_result_session_id", None)
        payload.update(_RESULT_REFERENCE_CASES[result_state](row["id"], SESSION_ID))
        payload.update(_SESSION_REFERENCE_CASES[session_state](row["id"], SESSION_ID))

    _mutate_audit_payload(store, "completion_report", mutate)


def _duplicate_ordinary_completion_with_references(
    store, row, result_state, session_state,
):
    def mutate(payload):
        payload.pop("_result_row_id", None)
        payload.pop("_result_session_id", None)
        payload.update(_RESULT_REFERENCE_CASES[result_state](row["id"], SESSION_ID))
        payload.update(_SESSION_REFERENCE_CASES[session_state](row["id"], SESSION_ID))

    _duplicate_audit_payload(store, "completion_report", mutate)


@pytest.mark.parametrize("result_state", sorted(_RESULT_REFERENCE_CASES))
@pytest.mark.parametrize("session_state", sorted(_SESSION_REFERENCE_CASES))
def test_ordinary_duplicate_cartesian_relatedness_matrix(
    tmp_path, result_state, session_state,
):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    _drive(store, row, attempt, "consumed_audited")
    assert _finalize(store, row, attempt).status == "continued"
    _insert_ordinary_completion(store, row["id"])
    assert _settle(store, row).status == "settled"

    _duplicate_ordinary_completion_with_references(
        store, row, result_state, session_state,
    )
    before = _counts(store._db)
    outcome = _settle(store, row)
    if (result_state, session_state) in _PROVABLY_UNRELATED_REFERENCE_COMBINATIONS:
        # An unrelated extra historical row neither supplies current authority
        # nor blocks the one exact ordinary evidence row.
        assert outcome.status == "settled", outcome
    else:
        assert outcome.status == "settlement_pending", outcome
        assert outcome.reason == "completion_evidence_missing", outcome
    # No refused case ever mutates task/J/K/P/V/E/N/D/Q/audit counts.
    assert _counts(store._db) == before


@pytest.mark.parametrize(
    "session_state", ["exact", "malformed"],
    ids=["exact-session-probe-a", "null-session-probe-b"],
)
def test_ordinary_conflicting_result_lone_candidate_refuses(tmp_path, session_state):
    """Lone-candidate form of the two demonstrated manager probe conflicts.

    A well-typed but DIFFERENT result reference combined with the exact current
    session or a malformed/null session is completion evidence for THIS causal
    result/session and can never be accepted as ordinary absence, whether it is
    an extra duplicate or the only surviving row.
    """
    store, _, _, _, row, attempt = _admitted(tmp_path)
    _drive(store, row, attempt, "consumed_audited")
    assert _finalize(store, row, attempt).status == "continued"
    _insert_ordinary_completion(store, row["id"])

    def mutate(payload):
        payload["_result_row_id"] = row["id"] + 999999
        payload["_result_session_id"] = (
            SESSION_ID if session_state == "exact" else None
        )

    _mutate_audit_payload(store, "completion_report", mutate)
    before = _counts(store._db)
    outcome = _settle(store, row)
    assert outcome.status == "settlement_pending", outcome
    assert outcome.reason == "completion_evidence_missing", outcome
    assert _counts(store._db) == before


def test_ordinary_conflicting_duplicate_refuses_after_repeat_and_reopen(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    _drive(store, row, attempt, "consumed_audited")
    assert _finalize(store, row, attempt).status == "continued"
    _insert_ordinary_completion(store, row["id"])
    assert _settle(store, row).status == "settled"
    _duplicate_ordinary_completion_with_references(store, row, "distinct", "exact")

    before = _counts(store._db)
    first = _settle(store, row)
    assert first.status == "settlement_pending", first
    assert _counts(store._db) == before

    reopened = _reopen(tmp_path)
    again = _settle(reopened, row)
    assert again.status == "settlement_pending", again
    assert _counts(reopened._db) == before
