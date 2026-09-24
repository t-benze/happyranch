"""THR-229 checkpoint C3d5a — automatic pre-final v2 hook dispatch.

Focused caller-level evidence for the accepted C3d5a radius, driving the REAL
``run_authority_hook`` over the real ``Database``/``AuthorityPolicyStore``
pre-final and final writers (no copied stage logic):

  * the policy family is selected from the AUTHENTICATED immutable launch
    binding BEFORE any legacy snapshot handling; a v2 binding runs the accepted
    claim+pin -> claim audit -> evaluation -> evaluation audit -> candidate
    consume -> consumed audit -> final continuation stages and returns the
    bounded ``v2_continued`` / ``v2_refused`` / ``v2_pending`` outcome that the
    directly coupled common consumer uses to suppress ordinary escalation;
  * the persisted normalized callback report is authoritative — a caller-provided
    ``manager_self_evaluation`` can neither replace nor manufacture it;
  * the existing pure dual-assessment derivation runs exactly once: a clear
    continue wins, while escalation/uncertainty/malformed all fail closed to a
    durable refusal with no envelope/notification/dispatch and no queue call;
  * a mid-stage failure requests refusal housekeeping while retaining the exact
    prior residue and never falls back to the ordinary escalation path;
  * a later legitimate selector activation never invalidates the pinned
    original session binding;
  * the v1/no-policy paths retain their existing semantics and a v2 binding with
    no authenticated admitted attempt is a bounded pending obligation, never an
    ordinary fallback.

This is isolated disposable-SQLite caller evidence.  The real subprocess
owned-RuntimeDir automatic path (manager launch -> shipping CLI -> HTTP ->
durable result -> hook -> stages -> finalization -> settlement -> publisher ->
TaskQueue/Dispatcher/run_step -> exactly one reserved next launch -> next CLI
callback -> spend/claim/normal done effect/applied receipt) lives in
``tests/test_authority_v2_shipping.py``.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from runtime.infrastructure.database import Database
from runtime.models import (
    TaskStatus,
    authority_policy_v2_canonical_json_bytes,
)
from runtime.orchestrator.active_authority_policy import (
    SESSION_POLICY_BINDING_ACTION,
)
from runtime.orchestrator.authority import (
    HOOK_V2_CONTINUED,
    HOOK_V2_PENDING,
    HOOK_V2_REFUSED,
    run_authority_hook,
)
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
from tests.test_authority_v2_attempt_admission import (
    MANAGER,
    SESSION_ID,
    TASK_ID,
    TEAM,
    _admit,
    _carrier_and_admission,
    _seed_bound_task,
    _store,
)


class _FakeTeams:
    def manager_for_team(self, team):
        assert team == TEAM
        return type("_M", (), {"name": MANAGER})()

    def is_team_manager(self, agent):
        return agent == MANAGER


class _RecordingQueue:
    def __init__(self):
        self.puts = []

    def put_nowait(self, slug, task_id, *, metadata=None):
        self.puts.append((slug, task_id, metadata))


class _FakeOrch:
    """Minimal real-Database orchestrator surface for the v2 hook path."""

    def __init__(self, db, queue):
        self._db = db
        self._queue = queue
        self._slug = "test-org"
        self.teams = _FakeTeams()
        self._paths = None
        self._authority_evaluator = None


def _orch(store, queue=None):
    return _FakeOrch(store._db, queue if queue is not None else _RecordingQueue())


def _admitted(tmp_path, *, carrier=None, admission=None, prebound=None):
    if prebound is None:
        store = _store(tmp_path)
        binding = _seed_bound_task(store)
    else:
        store, binding = prebound
    default_carrier, default_admission = _carrier_and_admission(binding)
    carrier = default_carrier if carrier is None else carrier
    admission = default_admission if admission is None else admission
    if carrier is not default_carrier:
        canonical = authority_policy_v2_canonical_json_bytes(carrier)
        admission["assessment_digest"] = hashlib.sha256(canonical).hexdigest()
        admission["assessment_canonical_json"] = canonical.decode("utf-8")
    assert _admit(store, carrier, admission) is True
    row = store._db.get_latest_task_result(TASK_ID, MANAGER, SESSION_ID)
    attempt = store._db.get_authority_policy_v2_attempt_for_result(row["id"])
    assert attempt is not None
    return store, binding, carrier, row, attempt


def _carrier_with(binding, *, escalate="does_not_apply", continue_="applies",
                  confidence=90, uncertainty=()):
    carrier, admission = _carrier_and_admission(binding)
    carrier["what_to_escalate"] = {
        "applicability": escalate, "confidence": confidence,
        "uncertainty_codes": list(uncertainty),
    }
    carrier["what_not_to_escalate"] = {
        "applicability": continue_, "confidence": confidence,
        "uncertainty_codes": [],
    }
    return carrier, admission


def _log_ordinary_completion(db, result_row_id, agent=MANAGER):
    """Reproduce the real ``Orchestrator._log_step_result`` v2 attribution."""
    from runtime.orchestrator.orchestrator import completion_report_from_result_row

    row = db._conn.execute(
        "SELECT * FROM task_results WHERE id=?", (result_row_id,)
    ).fetchone()
    report = completion_report_from_result_row(
        TASK_ID, dict(row), fallback_agent=agent,
    )
    db.insert_audit_log(
        task_id=TASK_ID, agent=row["agent"], action="completion_report",
        payload={
            **report.model_dump(),
            "_result_row_id": result_row_id,
            "_result_session_id": row["session_id"],
        },
    )


def _count(db, table: str) -> int:
    return db._conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]


def _run_hook(store, row, *, queue=None):
    orch = _orch(store, queue)
    task = store._db.get_task(TASK_ID)
    return run_authority_hook(
        orch, task, MANAGER, "escalate: protected boundary", row["id"],
    ), orch


# ── automatic continuation ───────────────────────────────────────────────


def test_v2_hook_clear_continue_finalizes_settles_and_publishes_once(tmp_path):
    store, binding, carrier, row, attempt = _admitted(tmp_path)
    db = store._db
    db.bind_authority_policy_v2_process_boot_id(attempt.origin_boot_id)
    _log_ordinary_completion(db, row["id"])
    queue = _RecordingQueue()

    outcome, _ = _run_hook(store, row, queue=queue)
    assert outcome == HOOK_V2_CONTINUED

    # Exactly one candidate/evaluation/envelope/generation.
    assert _count(db, "authority_policy_v2_candidates") == 1
    assert _count(db, "authority_policy_v2_pins") == 1
    assert _count(db, "authority_policy_v2_evaluations") == 1
    assert _count(db, "authority_policy_v2_continue_envelopes") == 1
    assert _count(db, "authority_policy_v2_recovery_notifications") == 1
    evaluation = db.get_authority_policy_v2_evaluation_for_result(row["id"])
    assert evaluation is not None and evaluation.outcome == "continue_applies"

    envelope = db.get_authority_policy_v2_continue_envelope_for_root(TASK_ID)
    assert envelope is not None and envelope.lifecycle_state == "active"
    assert envelope.spending_result_id is None
    notification = db.get_authority_policy_v2_recovery_notification_for_envelope(
        envelope.envelope_id,
    )
    # Publication committed the tagged generation (admission/settlement happen
    # only inside the real TaskQueue consumer, covered by the shipping venue).
    assert notification is not None and notification.state == "published"

    # The root returned to Pending with the causal owner/session preserved.
    pending = db.get_task(TASK_ID)
    assert pending.status is TaskStatus.PENDING
    assert pending.block_kind is None
    assert pending.current_session_id == SESSION_ID

    # The ordinary escalation/notification path never ran.
    assert not [a for a in db.get_audit_logs(TASK_ID) if a["action"] == "escalation"]
    assert len(queue.puts) == 1
    slug, task_id, metadata = queue.puts[0]
    assert (slug, task_id) == ("test-org", TASK_ID)
    assert metadata["authority_v2_generation"] == notification.notification_id

    # The attempt advanced through the full accepted stage sequence.
    raw = db._conn.execute(
        "SELECT stage, finalization_state FROM authority_policy_v2_attempts "
        "WHERE result_id=?", (row["id"],),
    ).fetchone()
    assert raw["stage"] == "consumed_audited"
    assert raw["finalization_state"] == "continued"


def test_v2_hook_ignores_caller_self_evaluation_and_uses_persisted(tmp_path):
    """A caller-supplied assessment cannot manufacture or replace the report."""
    store, binding, carrier, row, attempt = _admitted(tmp_path)
    db = store._db
    db.bind_authority_policy_v2_process_boot_id(attempt.origin_boot_id)
    _log_ordinary_completion(db, row["id"])
    forged = _carrier_and_admission(binding)[0]
    forged["what_to_escalate"] = {
        "applicability": "applies", "confidence": 99, "uncertainty_codes": [],
    }
    task = db.get_task(TASK_ID)
    outcome = run_authority_hook(
        _orch(store), task, MANAGER, "escalate", row["id"],
        manager_self_evaluation=forged,
    )
    assert outcome == HOOK_V2_CONTINUED
    evaluation = db.get_authority_policy_v2_evaluation_for_result(row["id"])
    assert evaluation.outcome == "continue_applies"


def test_v2_hook_already_final_replay_does_not_remint(tmp_path):
    store, binding, carrier, row, attempt = _admitted(tmp_path)
    db = store._db
    db.bind_authority_policy_v2_process_boot_id(attempt.origin_boot_id)
    _log_ordinary_completion(db, row["id"])
    first, _ = _run_hook(store, row)
    assert first == HOOK_V2_CONTINUED
    # Exact causal replay: the durable final evidence authenticates read-only;
    # no second envelope/generation/candidate is minted.
    second, _ = _run_hook(store, row)
    assert second == HOOK_V2_CONTINUED
    assert _count(db, "authority_policy_v2_candidates") == 1
    assert _count(db, "authority_policy_v2_continue_envelopes") == 1
    assert _count(db, "authority_policy_v2_recovery_notifications") == 1


# ── non-continue oracle outcomes fail closed to durable refusal ──────────


def test_v2_hook_clear_continue_accepts_arbitrary_noncanonical_reason(tmp_path):
    """V2 has no canonical-phrase / clause allowlist unlock (unlike v1)."""
    store, binding, carrier, row, attempt = _admitted(tmp_path)
    db = store._db
    db.bind_authority_policy_v2_process_boot_id(attempt.origin_boot_id)
    _log_ordinary_completion(db, row["id"])
    orch = _orch(store)
    task = db.get_task(TASK_ID)
    outcome = run_authority_hook(
        orch, task, MANAGER,
        "arbitrary noncanonical prose that is NOT the v1 routine phrase",
        row["id"],
    )
    assert outcome == HOOK_V2_CONTINUED


def test_v2_hook_ignores_adverse_and_partial_work_diagnostics(tmp_path):
    """The retained v1 adverse/partial-work diagnostics never veto v2.

    The separately established HISTORICAL raw-DDL inequality (the migrated
    owned-RuntimeDir venue in ``tests/test_authority_v2_shipping.py``) proves
    the v1 ``esc-schema-overloaded-column`` observation is likewise diagnostic
    rather than a v2 veto; the v2 path retains only the accepted
    constraint-sensitive protected-drift fence.
    """
    from runtime.models import TaskRecord

    store, binding, carrier, row, attempt = _admitted(tmp_path)
    db = store._db
    db.bind_authority_policy_v2_process_boot_id(attempt.origin_boot_id)
    _log_ordinary_completion(db, row["id"])

    # 1. An adverse child QA verdict (v1 esc-adverse-review-qa diagnostic).
    db.insert_task(TaskRecord(
        id="TASK-C3D5A-CHILD", status=TaskStatus.COMPLETED,
        assigned_agent="qa_engineer", team=TEAM, parent_task_id=TASK_ID,
        task_type="subtask", brief="child",
    ))
    db.insert_task_result(
        task_id="TASK-C3D5A-CHILD", agent="qa_engineer", session_id="sess-child",
        output_summary="needs changes", confidence_score=90, status="completed",
        verdict="REQUEST_CHANGES",
    )
    # 2. Partial-work evidence (v1 esc-partial-work diagnostic).
    db.execute(
        "UPDATE tasks SET zombie_flagged_at=? WHERE id=?",
        ("2026-09-21T00:00:00+00:00", TASK_ID),
    )
    db._conn.commit()

    queue = _RecordingQueue()
    outcome, _ = _run_hook(store, row, queue=queue)
    assert outcome == HOOK_V2_CONTINUED
    assert _count(db, "authority_policy_v2_continue_envelopes") == 1
    assert len(queue.puts) == 1


@pytest.mark.parametrize(
    ("escalate", "continue_", "uncertainty"),
    [
        ("applies", "does_not_apply", ()),
        ("applies", "applies", ()),
        ("does_not_apply", "does_not_apply", ()),
        ("does_not_apply", "applies", ("ambiguous_scope",)),
    ],
)
def test_v2_hook_non_continue_outcome_refuses_durably(
    tmp_path, escalate, continue_, uncertainty,
):
    store = _store(tmp_path)
    binding = _seed_bound_task(store)
    carrier, admission = _carrier_with(
        binding, escalate=escalate, continue_=continue_, uncertainty=uncertainty,
    )
    store, binding, carrier, row, attempt = _admitted(
        tmp_path, carrier=carrier, admission=admission,
        prebound=(store, binding),
    )
    db = store._db
    db.bind_authority_policy_v2_process_boot_id(attempt.origin_boot_id)
    queue = _RecordingQueue()
    outcome, _ = _run_hook(store, row, queue=queue)

    assert outcome == HOOK_V2_REFUSED
    # Durable terminal evidence, not a continuation.
    refused = db.get_task(TASK_ID)
    assert refused.status is TaskStatus.ESCALATED
    raw = db._conn.execute(
        "SELECT stage, finalization_state, refusal_code "
        "FROM authority_policy_v2_attempts WHERE result_id=?", (row["id"],),
    ).fetchone()
    assert raw["finalization_state"] in ("refused", "owner_lost")
    assert raw["refusal_code"] == "final_commit_failed"
    assert _count(db, "authority_policy_v2_continue_envelopes") == 0
    assert _count(db, "authority_policy_v2_recovery_notifications") == 0
    assert queue.puts == []
    assert db.get_active_authority_continue_envelope(TASK_ID) is None


def test_v2_hook_missing_diagnostic_refuses_durably(tmp_path):
    store = _store(tmp_path)
    binding = _seed_bound_task(store)
    carrier = {"_error_code": "missing_assessment"}
    canonical = authority_policy_v2_canonical_json_bytes(carrier)
    admission = {
        "team": TEAM, "binding_id": binding["binding_id"],
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
    store, binding, carrier, row, attempt = _admitted(
        tmp_path, carrier=carrier, admission=admission,
        prebound=(store, binding),
    )
    db = store._db
    db.bind_authority_policy_v2_process_boot_id(attempt.origin_boot_id)
    queue = _RecordingQueue()
    outcome, _ = _run_hook(store, row, queue=queue)
    assert outcome == HOOK_V2_REFUSED
    evaluation = db.get_authority_policy_v2_evaluation_for_result(row["id"])
    assert evaluation is not None and evaluation.outcome == "invalid"
    assert evaluation.diagnostic_code == "missing_assessment"
    assert _count(db, "authority_policy_v2_continue_envelopes") == 0
    assert queue.puts == []


# ── mid-stage failure: refusal-only, prior residue retained ──────────────


def test_v2_hook_stage_failure_requests_refusal_and_retains_residue(
    tmp_path, monkeypatch,
):
    store = _store(tmp_path)
    binding = _seed_bound_task(store)
    carrier, admission = _carrier_and_admission(binding)
    assert _admit(store, carrier, admission) is True
    row = store._db.get_latest_task_result(TASK_ID, MANAGER, SESSION_ID)
    attempt = store._db.get_authority_policy_v2_attempt_for_result(row["id"])
    db = store._db
    db.bind_authority_policy_v2_process_boot_id(attempt.origin_boot_id)

    def _boom(self, **kwargs):
        raise RuntimeError("injected evaluation transport failure")

    monkeypatch.setattr(AuthorityPolicyStore, "evaluate_v2_candidate", _boom)
    queue = _RecordingQueue()
    outcome, _ = _run_hook(store, row, queue=queue)

    # The hook never escalates ordinarily; refusal housekeeping is requested.
    assert outcome in (HOOK_V2_REFUSED, HOOK_V2_PENDING)
    # The exact preceding residue is retained: K/P claimed but no V, no envelope.
    assert _count(db, "authority_policy_v2_candidates") == 1
    assert _count(db, "authority_policy_v2_pins") == 1
    assert _count(db, "authority_policy_v2_evaluations") == 0
    assert _count(db, "authority_policy_v2_continue_envelopes") == 0
    assert queue.puts == []
    if outcome == HOOK_V2_REFUSED:
        assert db.get_task(TASK_ID).status is TaskStatus.ESCALATED
        raw = db._conn.execute(
            "SELECT finalization_state, refusal_code FROM authority_policy_v2_attempts "
            "WHERE result_id=?", (row["id"],),
        ).fetchone()
        assert raw["finalization_state"] in ("refused", "owner_lost")
    else:
        # Authentic pending: the prior durable state is untouched.
        assert db.get_task(TASK_ID).status is TaskStatus.IN_PROGRESS
        raw = db._conn.execute(
            "SELECT stage, finalization_state FROM authority_policy_v2_attempts "
            "WHERE result_id=?", (row["id"],),
        ).fetchone()
        assert raw["stage"] in ("claimed", "claim_audited")
        assert raw["finalization_state"] == "unfinalized"


# ── dispatch: v1/no-policy/v2-without-attempt ────────────────────────────


def test_v2_hook_without_admitted_attempt_is_bounded_pending(tmp_path):
    """A v2-bound session with no authenticated attempt never falls back."""
    store = _store(tmp_path)
    _seed_bound_task(store)
    queue = _RecordingQueue()
    orch = _orch(store, queue)
    task = store._db.get_task(TASK_ID)
    outcome = run_authority_hook(orch, task, MANAGER, "escalate", None)
    assert outcome == HOOK_V2_PENDING
    assert _count(store._db, "authority_policy_v2_continue_envelopes") == 0
    assert queue.puts == []
    # No ordinary escalation audit/notification was produced.
    assert not [a for a in store._db.get_audit_logs(TASK_ID) if a["action"] == "escalation"]


def test_no_policy_team_keeps_ordinary_fallback(tmp_path):
    from runtime.models import TaskRecord

    store = _store(tmp_path)
    store._db.insert_task(TaskRecord(
        id=TASK_ID, status=TaskStatus.IN_PROGRESS, assigned_agent="research_manager",
        team="research", brief="no policy team",
    ))
    store._db.update_task(TASK_ID, current_session_id=SESSION_ID)
    orch = _orch(store)
    task = store._db.get_task(TASK_ID)
    outcome = run_authority_hook(orch, task, "research_manager", "escalate", None)
    assert outcome == "escalate"
    assert _count(store._db, "authority_policy_v2_attempts") == 0


def test_static_binding_keeps_v1_path(tmp_path):
    """An explicit static/no-active binding never enters the v2 pipeline."""
    from runtime.models import TaskRecord
    from runtime.orchestrator.active_authority_policy import persist_session_policy_binding

    store = _store(tmp_path)
    persist_session_policy_binding(
        db=store._db, task_id=TASK_ID, session_id=SESSION_ID, agent_name=MANAGER,
        snapshot=None,
    )
    store._db.insert_task(TaskRecord(
        id=TASK_ID, status=TaskStatus.IN_PROGRESS, assigned_agent=MANAGER,
        team=TEAM, brief="static binding",
    ))
    store._db.update_task(TASK_ID, current_session_id=SESSION_ID)
    orch = _orch(store)
    task = store._db.get_task(TASK_ID)
    outcome = run_authority_hook(orch, task, MANAGER, "escalate", None)
    # The legacy path ran (and fails closed with no evaluator); no v2 rows.
    assert outcome == "escalate"
    assert _count(store._db, "authority_policy_v2_attempts") == 0


# ── original binding survives a later legitimate activation ──────────────


def test_later_activation_does_not_replace_pinned_binding(tmp_path):
    store, binding, carrier, row, attempt = _admitted(tmp_path)
    db = store._db
    db.bind_authority_policy_v2_process_boot_id(attempt.origin_boot_id)
    _log_ordinary_completion(db, row["id"])

    # A later legitimate v2 activation with a new epoch and a changed release.
    from tests.test_authority_v2_attempt_admission import WHAT_NOT, WHAT_TO

    selector = store.ensure_authority_selector(TEAM)
    store.create_and_activate_v2({
        "team": TEAM, "policy_id": "engineering-dual-text", "title": "Dual later",
        "create_request_id": "c3d5a-create-2",
        "activation_request_id": "c3d5a-activate-2",
        "based_on_selector_id": selector.selector_id,
        "expected_selector_id": selector.selector_id,
        "action": "activate",
        "what_to_escalate": WHAT_TO + " (later revision)",
        "what_not_to_escalate": WHAT_NOT,
    })
    queue = _RecordingQueue()
    outcome, _ = _run_hook(store, row, queue=queue)
    assert outcome == HOOK_V2_CONTINUED
    candidate = db.get_authority_policy_v2_candidate_for_result(row["id"])
    assert candidate is not None
    assert candidate.activation_id == binding["activation_id"]
    assert candidate.activation_epoch == binding["selector_epoch"]
    assert len(queue.puts) == 1


# ── every newly wired pre-final/final boundary fails closed ──────────────

_PRE_FINAL_BOUNDARIES = (
    # (forwarder the automatic hook calls, attempt stage retained if it fails)
    ("claim_v2_candidate", "admitted"),
    ("audit_v2_candidate_claim", "claimed"),
    ("evaluate_v2_candidate", "claim_audited"),
    ("audit_v2_candidate_evaluation", "evaluated"),
    ("consume_v2_candidate", "evaluation_audited"),
    ("audit_v2_candidate_consumption", "consumed"),
    ("finalize_v2_continuation", "consumed_audited"),
)


@pytest.mark.parametrize(("forwarder", "residue_stage"), _PRE_FINAL_BOUNDARIES)
def test_v2_hook_each_newly_wired_boundary_fails_closed_with_residue(
    tmp_path, monkeypatch, forwarder, residue_stage,
):
    """Injecting at each newly wired boundary: exact prior residue, no later
    stage/evaluation/envelope/mint/queue, and never the ordinary path."""
    store, binding, carrier, row, attempt = _admitted(tmp_path)
    db = store._db
    db.bind_authority_policy_v2_process_boot_id(attempt.origin_boot_id)
    _log_ordinary_completion(db, row["id"])

    def _boom(self, **kwargs):
        raise RuntimeError(f"injected {forwarder} failure")

    monkeypatch.setattr(AuthorityPolicyStore, forwarder, _boom)
    queue = _RecordingQueue()
    outcome, _ = _run_hook(store, row, queue=queue)

    # The hook never escapes to ordinary escalation/queue handling.
    assert outcome in (HOOK_V2_REFUSED, HOOK_V2_PENDING)
    assert queue.puts == []
    # No continuation work is ever minted, at any boundary.
    assert _count(db, "authority_policy_v2_continue_envelopes") == 0
    assert _count(db, "authority_policy_v2_recovery_notifications") == 0
    assert db.get_active_authority_continue_envelope(TASK_ID) is None
    # The greatest committed stage is retained; the failed boundary and every
    # later stage never advanced.
    raw = db._conn.execute(
        "SELECT stage, finalization_state FROM authority_policy_v2_attempts "
        "WHERE result_id=?", (row["id"],),
    ).fetchone()
    assert raw["stage"] == residue_stage, raw["stage"]
    assert raw["finalization_state"] in ("unfinalized", "refused", "owner_lost")
    if forwarder == "finalize_v2_continuation":
        # A failed final commit must not leave a half-minted envelope/dispatch.
        assert db.get_authority_policy_v2_root_dispatch(TASK_ID) is None


def test_v2_hook_refusal_commit_failure_is_bounded_pending_then_refusal_only(
    tmp_path, monkeypatch,
):
    """A refusal-transaction failure leaves prior state intact; a reopen can
    only refuse, never resume evaluation or mint a continuation."""
    store, binding, carrier, row, attempt = _admitted(tmp_path)
    db = store._db
    db.bind_authority_policy_v2_process_boot_id(attempt.origin_boot_id)

    def _evaluate_boom(self, **kwargs):
        raise RuntimeError("injected evaluation transport failure")

    monkeypatch.setattr(AuthorityPolicyStore, "evaluate_v2_candidate", _evaluate_boom)
    real_refusal = AuthorityPolicyStore.finalize_v2_attempt_refusal

    def _refusal_boom(self, **kwargs):
        raise RuntimeError("injected refusal transaction failure")

    monkeypatch.setattr(
        AuthorityPolicyStore, "finalize_v2_attempt_refusal", _refusal_boom,
    )
    queue = _RecordingQueue()
    outcome, _ = _run_hook(store, row, queue=queue)

    # Bounded pending: nothing final, nothing escalated ordinally, no queue.
    assert outcome == HOOK_V2_PENDING
    assert queue.puts == []
    assert db.get_task(TASK_ID).status is TaskStatus.IN_PROGRESS
    raw = db._conn.execute(
        "SELECT stage, finalization_state, refusal_code "
        "FROM authority_policy_v2_attempts WHERE result_id=?", (row["id"],),
    ).fetchone()
    assert raw["finalization_state"] == "unfinalized"
    assert raw["refusal_code"] is None
    assert raw["stage"] == "claim_audited"
    assert _count(db, "authority_policy_v2_continue_envelopes") == 0

    # Reopen on the SAME durable file through a second Database.  Refusal
    # housekeeping is restored, but the injected evaluation failure still
    # stands, so the retry can only end in refusal/pending — never continuation.
    monkeypatch.setattr(
        AuthorityPolicyStore, "finalize_v2_attempt_refusal", real_refusal,
    )
    second_db = Database(tmp_path / "c2.db")
    second_db.bind_authority_policy_v2_process_boot_id(attempt.origin_boot_id)
    second_queue = _RecordingQueue()
    second_outcome = run_authority_hook(
        _FakeOrch(second_db, second_queue), second_db.get_task(TASK_ID),
        MANAGER, "escalate: protected boundary", row["id"],
    )
    assert second_outcome in (HOOK_V2_REFUSED, HOOK_V2_PENDING)
    assert second_queue.puts == []
    assert _count(second_db, "authority_policy_v2_continue_envelopes") == 0
    assert _count(second_db, "authority_policy_v2_recovery_notifications") == 0


# ── malformed / mixed binding families never fall back to v1 ──────────────


def test_v2_hook_unknown_binding_family_fails_closed_without_v1(tmp_path):
    from runtime.models import TaskRecord

    store = _store(tmp_path)
    db = store._db
    db.insert_task(TaskRecord(
        id=TASK_ID, status=TaskStatus.IN_PROGRESS, assigned_agent=MANAGER,
        team=TEAM, brief="unknown binding family",
    ))
    db.update_task(TASK_ID, current_session_id=SESSION_ID)
    db.insert_audit_log(
        task_id=TASK_ID, agent=MANAGER, action=SESSION_POLICY_BINDING_ACTION,
        payload={"session_id": SESSION_ID, "mode": "mixed_unknown_family"},
    )
    queue = _RecordingQueue()
    orch = _orch(store, queue)
    outcome = run_authority_hook(orch, db.get_task(TASK_ID), MANAGER, "escalate", None)

    assert outcome == "escalate"
    assert queue.puts == []
    assert _count(db, "authority_policy_v2_attempts") == 0
    outcomes = [
        a["payload"].get("outcome") for a in db.get_audit_logs(TASK_ID)
        if a["action"] == "authority_hook"
    ]
    assert outcomes and outcomes[-1] == "capture_failure"


def test_v2_hook_mixed_v2_and_legacy_binding_fails_closed(tmp_path):
    store = _store(tmp_path)
    _seed_bound_task(store)
    db = store._db
    db.insert_audit_log(
        task_id=TASK_ID, agent=MANAGER, action=SESSION_POLICY_BINDING_ACTION,
        payload={"session_id": SESSION_ID, "mode": "legacy_static"},
    )
    queue = _RecordingQueue()
    orch = _orch(store, queue)
    outcome = run_authority_hook(orch, db.get_task(TASK_ID), MANAGER, "escalate", None)

    assert outcome == "escalate"
    assert queue.puts == []
    assert _count(db, "authority_policy_v2_attempts") == 0
    assert _count(db, "authority_policy_v2_continue_envelopes") == 0


# ── cancellation between caller stages preserves the winning task ─────────


def test_v2_hook_cancellation_between_stages_never_continues(tmp_path, monkeypatch):
    store, binding, carrier, row, attempt = _admitted(tmp_path)
    db = store._db
    db.bind_authority_policy_v2_process_boot_id(attempt.origin_boot_id)
    _log_ordinary_completion(db, row["id"])

    real_evaluate = AuthorityPolicyStore.evaluate_v2_candidate
    second_db = Database(tmp_path / "c2.db")

    def _cancel_then_evaluate(self, **kwargs):
        # A genuine second Database cancels the root between the audit stages.
        second_db.update_task(
            TASK_ID, status=TaskStatus.CANCELLED,
            cancelled_at="2026-09-21T00:00:00+00:00",
        )
        return real_evaluate(self, **kwargs)

    monkeypatch.setattr(
        AuthorityPolicyStore, "evaluate_v2_candidate", _cancel_then_evaluate,
    )
    queue = _RecordingQueue()
    outcome, _ = _run_hook(store, row, queue=queue)

    assert outcome in (HOOK_V2_REFUSED, HOOK_V2_PENDING)
    assert queue.puts == []
    assert _count(db, "authority_policy_v2_continue_envelopes") == 0
    assert _count(db, "authority_policy_v2_recovery_notifications") == 0
    # The winning (cancelled) task row is preserved exactly; no successor.
    preserved = db.get_task(TASK_ID)
    assert preserved.status is TaskStatus.CANCELLED
    assert preserved.current_session_id == SESSION_ID
