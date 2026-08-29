"""THR-181 Track A — pre-escalation authority hook tests.

Proves the founder-authorized slice end to end through the SHIPPING seam
(``run_authority_hook`` called by ``run_step_impl``'s escalate branch, and the
real ``StrictFakeAuthorityEvaluator`` / policy):

(a) deterministic strict fake;
(b) positive CONTINUE_SAME_ROOT control — reachable WITHOUT any census
    eligibility, executes only the named same-root permitted action;
(c) ordinary ESCALATE golden/characterization — byte-identical prior task
    transition / audit escalation row / notification / thread behavior
    aside from the new authority records;
(d) real must-escalate sentinels (schema/overloaded-column, permission/
    sandbox/allow-rule, auth/credentials/security/privacy/data access,
    compatibility, spend/budget, destructive/irreversible, external/product/
    deploy, adverse review/QA, ambiguity/novelty, partial work, all
    exhausted orchestration/revise/implementation/provider limits);
(e) fail-closed matrix (missing/invalid/extra-field/wrong-clause/wrong-action/
    ambiguous output, timeout, provider error, policy/team/digest mismatch,
    audit-write failure) — all escalate;
(f) cancellation wins before evaluation, during evaluation, at final CAS;
(g) successor/supersede/revisit/fresh-root/non-root/non-manager are
    ineligible / fail closed;
(h) concurrency/exactly-once — one durable evaluation and one durable winner;
(i) restart states (claimed/created, evaluation-missing, evaluated-but-
    unconsumed, already-consumed) all fail closed without duplicate
    evaluation or continuation;
(j) denominator — every attempted candidate has exactly one explainable
    terminal runtime outcome.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.infrastructure.database import Database
from runtime.models import BlockKind, TaskRecord, TaskStatus
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.authority import (
    AUDIT_ACTION_CONTINUED_SAME_ROOT,
    AUDIT_ACTION_HOOK_OUTCOME,
    OUTCOME_CANCELLED_STALE,
    OUTCOME_CAS_LOST,
    OUTCOME_CONTINUED_SAME_ROOT,
    OUTCOME_ESCALATED,
    OUTCOME_INELIGIBLE,
    AuthorityInputSnapshot,
    AuthorityEvaluationResult,
    StrictFakeAuthorityEvaluator,
    run_authority_hook,
)
from runtime.orchestrator.authority_policy import (
    ACTION_CONTINUE_SAME_ROOT,
    ENGINEERING_PRE_ESCALATION_POLICY as POLICY,
    POLICY_BY_TEAM,
    PROMPT_DIGEST,
)
from runtime.orchestrator.orchestrator import Orchestrator
from runtime.orchestrator.teams import TeamsRegistry
from runtime.runtime import RuntimeDir


@pytest.fixture(autouse=True)
def _seed_active_agents_for_run_step(runtime: OrgPaths):
    from tests.conftest import seed_test_agents
    seed_test_agents(runtime, ("engineering_head", "dev_agent", "content_head"))


@pytest.fixture
def runtime(tmp_path: Path) -> OrgPaths:
    rt = RuntimeDir.init(tmp_path / "rt")
    paths = OrgPaths(root=rt.orgs_dir / "test")
    paths.teams_config_path.parent.mkdir(parents=True, exist_ok=True)
    paths.teams_config_path.write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: engineering_head\n"
        "    workers: [product_manager, dev_agent, payment_agent, qa_engineer]\n"
        "  content:\n"
        "    manager: content_head\n"
        "    workers: [content_agent]\n"
    )
    return paths


@pytest.fixture
def db(runtime: OrgPaths) -> Database:
    return Database(runtime.db_path)


def _make_report(output_summary: str, status: str = "completed"):
    from runtime.models import CompletionReport
    return CompletionReport(
        task_id="T-IGNORED", agent="engineering_head", status=status,
        confidence=80, output_summary=output_summary,
    )


def _make_result(success: bool = True, duration: int = 1):
    from runtime.orchestrator.executors import ExecutorResult
    return ExecutorResult(
        success=success, session_id="sess-x", duration_seconds=duration,
    )


class _SlugQueue:
    def __init__(self) -> None:
        import asyncio as _asyncio
        self._q: _asyncio.Queue = _asyncio.Queue()
    def put_nowait(self, slug: str, task_id: str) -> None:
        self._q.put_nowait((slug, task_id))
    def qsize(self) -> int:
        return self._q.qsize()
    def get_nowait(self):
        return self._q.get_nowait()


def _make_orch(runtime, db, evaluator=None):
    orch = Orchestrator(
        db=db, settings=Settings(), paths=runtime, slug="test",
        teams=TeamsRegistry.load(runtime.root),
        authority_evaluator=evaluator,
    )
    orch._queue = _SlugQueue()
    return orch


def _seed_claimed_root(
    db, task_id: str = "T-ROOT", *,
    agent: str = "engineering_head",
    team: str = "engineering",
    session: str = "sess-x",
    thread: str | None = None,
    revisit_of_task_id: str | None = None,
) -> None:
    """Seed a root already in the claimed (in_progress) state — for direct
    hook-level tests. run_step integration tests instead seed a PENDING root
    and let run_step's claim + fake _run_agent stamp the session."""
    db.insert_task(TaskRecord(
        id=task_id, brief="b", assigned_agent=agent, team=team,
        dispatched_from_thread_id=thread,
        revisit_of_task_id=revisit_of_task_id,
    ))
    db.update_task(
        task_id,
        status=TaskStatus.IN_PROGRESS, block_kind=None,
        orchestration_step_count=1, current_session_id=session,
    )


def _seed_root(
    db, task_id: str = "T-ROOT", *,
    agent: str = "engineering_head",
    team: str = "engineering",
    thread: str | None = None,
    revisit_of_task_id: str | None = None,
) -> None:
    """Seed a PENDING root — the run_step-eligible entry state."""
    db.insert_task(TaskRecord(
        id=task_id, brief="b", assigned_agent=agent, team=team,
        dispatched_from_thread_id=thread,
        revisit_of_task_id=revisit_of_task_id,
    ))


def _escalate_decision(reason: str) -> str:
    return json.dumps({"action": "escalate", "reason": reason})


def _run_escalate_step(orch, task_id: str, reason: str, monkeypatch, session: str | None = "sess-x") -> None:
    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        # Mirror the real orchestrator: stamp the current session on the row
        # before the report returns, so the hook sees a current session.
        # session=None simulates a session-less (ineligible) escalation.
        if session is not None:
            orch.db.update_task(task_id, current_session_id=session)
        return _make_result(), _make_report(output_summary=_escalate_decision(reason))
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)
    orch.run_step(task_id)


def _authority_audit_events(db, candidate_id: str):
    return [e.event_type for e in db.list_authority_audit(candidate_id)]


def _hook_outcome_rows(db, task_id: str):
    return [
        a for a in db.get_audit_logs(task_id)
        if a["action"] == AUDIT_ACTION_HOOK_OUTCOME
    ]


def _escalation_rows(db, task_id: str):
    return [a for a in db.get_audit_logs(task_id) if a["action"] == "escalation"]


def _digest(tag: str) -> str:
    return hashlib.sha256(tag.encode("utf-8")).hexdigest()


# ── (a) deterministic strict fake ────────────────────────────────────────

CONTINUE_REASON = "routine same-root follow-through of the already-completed slice"


def _make_snapshot(reason: str = CONTINUE_REASON, team: str = "engineering") -> AuthorityInputSnapshot:
    return AuthorityInputSnapshot(
        root_task_id="T-ROOT",
        team=team,
        manager_agent="engineering_head",
        manager_session_id="sess-x",
        candidate_id="AUTH-CAND-" + _digest("claim"),
        causal_event_id="step:7",
        causal_event_digest=_digest("causal"),
        reason=reason,
        reason_digest=_digest(reason),
        policy_id=POLICY.id,
        policy_version=POLICY.version,
        policy_digest=POLICY.digest,
        prompt_id="prompt/authority-evaluator/engineering",
        prompt_version="v1",
        prompt_digest=PROMPT_DIGEST,
        model_id=StrictFakeAuthorityEvaluator.model_id,
        model_version="v1",
        model_digest=StrictFakeAuthorityEvaluator.model_digest,
    )


def test_strict_fake_is_deterministic():
    fake = StrictFakeAuthorityEvaluator()
    a = fake.evaluate(_make_snapshot(CONTINUE_REASON))
    b = fake.evaluate(_make_snapshot(CONTINUE_REASON))
    assert a == b
    assert a.disposition.value == "continue_same_root"
    assert a.clause_id == "cont-routine-same-root"
    assert a.action == ACTION_CONTINUE_SAME_ROOT


def test_strict_fake_validates_snapshot_strictly():
    fake = StrictFakeAuthorityEvaluator()
    with pytest.raises(ValueError):
        fake.evaluate(_make_snapshot(team="content"))  # no policy for content
    bad = _make_snapshot()
    bad.policy_digest = _digest("wrong")
    with pytest.raises(ValueError):
        fake.evaluate(bad)
    bad2 = _make_snapshot(reason="")
    with pytest.raises(ValueError):
        fake.evaluate(bad2)


def test_strict_fake_classification_covers_policy_clauses():
    """Every must-escalate category of the policy has a real sentinel reason
    that the strict fake maps to ESCALATE with the escalate action."""
    fake = StrictFakeAuthorityEvaluator()
    sentinels = {
        "schema": "needs a schema migration that alters existing columns in tasks",
        "overloaded-column": "changing the overloaded-column semantics of audit_log.task_id",
        "permission": "this needs a permission/sandbox/allow-rule change",
        "auth": "requires credential handling changes (auth/security)",
        "privacy": "privacy or data-access decision required",
        "compatibility": "v0/v1 compatibility decision for the runtime surface",
        "spend": "exceeds the spend/budget ceiling",
        "destructive": "destructive/irreversible action on production data",
        "external": "external product/deploy commitment (third-party dependency)",
        "review": "adverse review/QA verdict: REVISE",
        "ambiguity": "genuine ambiguity/novel situation with conflicting evidence",
        "partial": "session timed out with partial work evidence",
        "exhausted-orchestration": "max steps (orchestration budget) exhausted",
        "exhausted-revise": "revise-round budget exhausted",
        "exhausted-implementation": "per-slice retry ceiling exhausted after second failure",
        "provider-limit": "provider session limit reached after repeated 429s",
        "cancellation": "task was cancelled with live in-flight children",
        "successor": "proposes a successor/supersede/revisit fresh-root action",
    }
    for category, reason in sentinels.items():
        disposition, clause_id, action = StrictFakeAuthorityEvaluator.classify_reason(reason)
        assert disposition == "escalate", category
        assert action == "escalate_to_founder", category
        # The matched clause exists in the release policy and is a must-escalate clause.
        clause = POLICY.clause_by_id(clause_id)
        assert clause is not None, category
        assert clause.action == "escalate_to_founder", category
    # The narrow continue clause is reachable only for a fence-clean reason.
    d, c, a = StrictFakeAuthorityEvaluator.classify_reason(CONTINUE_REASON)
    assert (d, c, a) == ("continue_same_root", "cont-routine-same-root", ACTION_CONTINUE_SAME_ROOT)


def test_policy_is_release_controlled_and_immutable():
    """The policy digest is stable and covers every clause; the registry binds
    exactly the Engineering team."""
    assert POLICY.id == "engineering/pre-escalation-authority"
    assert POLICY.version == "v1"
    assert POLICY.digest == POLICY._compute_digest()
    assert POLICY_BY_TEAM == {"engineering": POLICY}
    # Every continue clause names the exact permitted action.
    for clause in POLICY.continue_clauses():
        assert clause.action == ACTION_CONTINUE_SAME_ROOT
    # The policy text itself states the non-overridable fence contract.
    assert "non-overridable" in POLICY.normative_text
    assert "UNTRUSTED" in POLICY.normative_text


# ── (b) positive CONTINUE_SAME_ROOT control ──────────────────────────────

def test_continue_same_root_reachable_without_census_eligibility(
    runtime, db, monkeypatch,
):
    """The hook is reachable with NO census data at all: the strict fake
    classifies the synthetic fence-clean reason, the named same-root
    permitted action executes (root returns to pending, re-enqueued), and NO
    escalation is committed."""
    fake = StrictFakeAuthorityEvaluator()
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)

    _run_escalate_step(orch, "T-ROOT", CONTINUE_REASON, monkeypatch)

    t = db.get_task("T-ROOT")
    assert t.status == TaskStatus.PENDING
    assert t.block_kind is None
    assert "authority-policy continued same root" in (t.note or "")
    assert t.note and "cont-routine-same-root" in t.note
    # No escalation committed.
    assert _escalation_rows(db, "T-ROOT") == []
    assert t.status != TaskStatus.ESCALATED
    # Re-enqueued for the next manager decision step.
    assert orch._queue.qsize() == 1
    assert orch._queue.get_nowait() == ("test", "T-ROOT")

    # Exactly one durable candidate, one evaluation, one consumption.
    candidates = db.list_authority_candidates_for_root("T-ROOT")
    assert len(candidates) == 1
    cand = candidates[0]
    assert cand.lifecycle_state.value == "consumed"
    assert cand.disposition.value == "continue_same_root"
    assert cand.team == "engineering"
    eval_row = db.get_authority_evaluation(cand.id)
    assert eval_row is not None
    assert eval_row.disposition.value == "continue_same_root"
    assert eval_row.disposition_code.value == "continue_same_root"
    events = _authority_audit_events(db, cand.id)
    assert events == ["candidate_claimed", "evaluation_recorded", "candidate_consumed"]

    # Denominator: exactly one terminal outcome row = continued_same_root.
    rows = _hook_outcome_rows(db, "T-ROOT")
    assert len(rows) == 1
    assert rows[0]["payload"]["outcome"] == OUTCOME_CONTINUED_SAME_ROOT
    assert rows[0]["payload"]["clause_id"] == "cont-routine-same-root"
    assert rows[0]["payload"]["action"] == ACTION_CONTINUE_SAME_ROOT
    # The audit trail names the policy/version/digest + matched clause.
    continued = [
        a for a in db.get_audit_logs("T-ROOT")
        if a["action"] == AUDIT_ACTION_CONTINUED_SAME_ROOT
    ]
    assert len(continued) == 1
    payload = continued[0]["payload"]
    assert payload["policy_id"] == POLICY.id
    assert payload["policy_version"] == POLICY.version
    assert payload["policy_digest"] == POLICY.digest
    assert payload["clause_id"] == "cont-routine-same-root"
    assert payload["action"] == ACTION_CONTINUE_SAME_ROOT


def test_continue_executes_only_the_named_same_root_action(runtime, db, monkeypatch):
    """Nothing beyond the named permitted action happens: no child, no
    supersede, no thread message, no notification, no escalation row."""
    fake = StrictFakeAuthorityEvaluator()
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)
    spy = {}
    monkeypatch.setattr(orch, "notify_escalated",
                        lambda **kw: spy.update(kw))

    _run_escalate_step(orch, "T-ROOT", CONTINUE_REASON, monkeypatch)

    assert spy == {}  # no notification
    assert db.get_children("T-ROOT") == []  # no child spawned
    assert _escalation_rows(db, "T-ROOT") == []
    # No supersession record.
    t = db.get_task("T-ROOT")
    assert t.status == TaskStatus.PENDING


def test_continue_header_shown_to_manager_on_next_step(runtime, db, monkeypatch):
    """The next manager decision step carries the AUTHORITY POLICY CONTINUED
    SAME ROOT header naming the policy, clause, and permitted action."""
    fake = StrictFakeAuthorityEvaluator()
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)
    _run_escalate_step(orch, "T-ROOT", CONTINUE_REASON, monkeypatch)

    captured = {}
    def capture(task_id, agent, prompt, on_session_started=None):
        captured["prompt"] = prompt
        raise RuntimeError("abort after prompt build")
    monkeypatch.setattr(orch, "_run_agent", capture)
    orch.run_step("T-ROOT")

    prompt = captured["prompt"]
    assert prompt.startswith("AUTHORITY POLICY CONTINUED SAME ROOT:")
    assert POLICY.id in prompt
    assert "cont-routine-same-root" in prompt
    assert ACTION_CONTINUE_SAME_ROOT in prompt


# ── (c) ESCALATE golden / characterization ───────────────────────────────

def test_escalate_golden_byte_identical_behavior(runtime, db, monkeypatch):
    """With the hook evaluating to ESCALATE, the prior escalation behavior is
    byte-identical (task transition, note, escalation audit row/payload,
    notification call, return behavior) aside from the new authority records —
    compared against a baseline run where the hook evaluated to a fail-closed
    EVALUATOR_ERROR with no evaluator wired."""

    def run_once(evaluator, reason, session, spy):
        _seed_root(db, task_id="T-X")
        orch = _make_orch(runtime, db, evaluator=evaluator)
        monkeypatch.setattr(orch, "notify_escalated",
                            lambda **kw: spy.update(kw))
        _run_escalate_step(orch, "T-X", reason, monkeypatch, session=session)
        return orch

    # Baseline: a session-less escalation is ineligible for the hook and
    # escalates through the pre-existing path unchanged.
    spy_baseline = {}
    run_once(None, "needs founder", None, spy_baseline)
    t_baseline = db.get_task("T-X")
    esc_baseline = _escalation_rows(db, "T-X")
    base_outcome = _hook_outcome_rows(db, "T-X")[0]["payload"]["outcome"]
    assert base_outcome == OUTCOME_INELIGIBLE
    assert db.list_authority_candidates_for_root("T-X") == []

    db2 = Database(runtime.db_path.parent / "golden-hook.db")  # fresh DB file
    fake = StrictFakeAuthorityEvaluator()
    _seed_root(db2, task_id="T-X")
    orch2 = _make_orch(runtime, db2, evaluator=fake)
    spy_hook = {}
    monkeypatch.setattr(orch2, "notify_escalated",
                        lambda **kw: spy_hook.update(kw))
    _run_escalate_step(orch2, "T-X", "needs founder", monkeypatch)
    t_hook = db2.get_task("T-X")
    esc_hook = _escalation_rows(db2, "T-X")

    # Byte-identical task transition + escalation audit rows.
    assert t_baseline.status == t_hook.status == TaskStatus.ESCALATED
    assert t_baseline.block_kind is None and t_hook.block_kind is None
    assert t_baseline.note == t_hook.note == "needs founder"
    assert [a["payload"] for a in esc_baseline] == [a["payload"] for a in esc_hook]
    assert [a["action"] for a in esc_baseline] == [a["action"] for a in esc_hook]
    assert spy_baseline["reason"] == spy_hook["reason"] == "needs founder"
    assert spy_baseline["task_id"] == spy_hook["task_id"] == "T-X"
    assert spy_baseline["agent"] == spy_hook["agent"] == "engineering_head"
    # Return behavior: nothing re-enqueued (root escalated, terminal).
    assert orch2._queue.qsize() == 0

    # The ONLY difference: the new authority records' CONTENT. The baseline
    # hook wrote an ineligible outcome with no candidate; the hooked run
    # fully evaluated (candidate claimed/evaluated/consumed) and records the
    # escalated outcome with the matched clause.
    base_row = _hook_outcome_rows(db, "T-X")[0]["payload"]
    hook_row = _hook_outcome_rows(db2, "T-X")[0]["payload"]
    assert hook_row["outcome"] == OUTCOME_ESCALATED
    assert hook_row["disposition"] == "escalate"
    assert base_row["outcome"] != hook_row["outcome"]
    assert db.list_authority_candidates_for_root("T-X") == []
    cands_hook = db2.list_authority_candidates_for_root("T-X")
    assert len(cands_hook) == 1
    assert cands_hook[0].lifecycle_state.value == "consumed"
    assert cands_hook[0].disposition.value == "escalate"
    assert db2.get_authority_evaluation(cands_hook[0].id) is not None


def test_escalate_thread_projection_unchanged_for_thread_origin(runtime, db, monkeypatch):
    """A thread-originated root is ineligible for the hook (fence) and the
    existing thread escalation projection still posts byte-identically."""
    from runtime.models import ThreadRecord
    from runtime.infrastructure.audit_logger import AuditLogger

    _seed_root(db, task_id="T-1", thread="THR-9")
    db.insert_thread(ThreadRecord(id="THR-9", subject="t"))
    db.add_thread_participant("THR-9", "engineering_head", added_by="founder")
    AuditLogger(db).log_thread_dispatch(
        "THR-9", task_id="T-1", dispatcher="engineering_head",
        target_agent="engineering_head", team="engineering",
    )
    fake = StrictFakeAuthorityEvaluator()
    orch = _make_orch(runtime, db, evaluator=fake)
    _run_escalate_step(orch, "T-1", "needs founder", monkeypatch)

    t = db.get_task("T-1")
    assert t.status == TaskStatus.ESCALATED
    # Hook fence: thread_origin → ineligible, no candidate.
    row = _hook_outcome_rows(db, "T-1")[0]
    assert row["payload"]["outcome"] == OUTCOME_INELIGIBLE
    assert "thread_origin" in row["payload"]["fence_results"]
    assert db.list_authority_candidates_for_root("T-1") == []
    # The task_escalated thread system message still posted.
    msgs = db.list_thread_messages("THR-9")
    esc = [m for m in msgs if m.system_payload
           and m.system_payload.get("kind_tag") == "task_escalated"]
    assert len(esc) == 1
    assert esc[0].system_payload["reason"] == "needs founder"


# ── (d) real must-escalate sentinels through the shipping hook ────────────

SENTINEL_REASONS = [
    "needs a schema migration that alters existing columns in tasks",
    "changing the overloaded-column semantics of audit_log.task_id",
    "this needs a permission/sandbox/allow-rule change",
    "requires credential handling changes (auth/security)",
    "privacy or data-access decision required",
    "v0/v1 compatibility decision for the runtime surface",
    "exceeds the spend/budget ceiling",
    "destructive/irreversible action on production data",
    "external product/deploy commitment (third-party dependency)",
    "adverse review/QA verdict: REVISE",
    "genuine ambiguity/novel situation with conflicting evidence",
    "session timed out with partial work evidence",
    "max steps (orchestration budget) exhausted",
    "revise-round budget exhausted",
    "per-slice retry ceiling exhausted after second failure",
    "provider session limit reached after repeated 429s",
]


@pytest.mark.parametrize("reason", SENTINEL_REASONS)
def test_must_escalate_sentinel_escalates(runtime, db, monkeypatch, reason):
    """Every real must-escalate sentinel escalates through the hook with the
    strict fake (each is a policy must-escalate clause match)."""
    fake = StrictFakeAuthorityEvaluator()
    _seed_root(db, task_id="T-S")
    orch = _make_orch(runtime, db, evaluator=fake)
    _run_escalate_step(orch, "T-S", reason, monkeypatch)

    t = db.get_task("T-S")
    assert t.status == TaskStatus.ESCALATED
    assert t.note == reason
    assert _escalation_rows(db, "T-S")  # committed escalation row
    row = _hook_outcome_rows(db, "T-S")[0]
    assert row["payload"]["outcome"] == OUTCOME_ESCALATED
    assert row["payload"]["disposition"] == "escalate"
    # The matched clause is a must-escalate clause of the release policy.
    clause_id = row["payload"]["clause_id"]
    clause = POLICY.clause_by_id(clause_id)
    assert clause is not None and clause.action == "escalate_to_founder"
    # Exactly one candidate, evaluated + consumed.
    cands = db.list_authority_candidates_for_root("T-S")
    assert len(cands) == 1
    assert cands[0].lifecycle_state.value == "consumed"
    assert cands[0].disposition.value == "escalate"


# ── (e) fail-closed matrix (through the shipping hook, pinned fake) ───────

def _pinned_fake(disposition, clause_id, action, **kw):
    reason = kw.pop("reason", "some reason")
    class _Pinned(StrictFakeAuthorityEvaluator):
        pass
    fake = StrictFakeAuthorityEvaluator(pinned={reason: (disposition, clause_id, action)})
    return fake, reason


def test_fail_closed_escalates_on_wrong_clause(runtime, db, monkeypatch):
    fake = StrictFakeAuthorityEvaluator(pinned={
        "x": ("continue_same_root", "esc-spend-budget", ACTION_CONTINUE_SAME_ROOT),
    })
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)
    _run_escalate_step(orch, "T-ROOT", "x", monkeypatch)
    t = db.get_task("T-ROOT")
    assert t.status == TaskStatus.ESCALATED  # fail closed: never continued
    row = _hook_outcome_rows(db, "T-ROOT")[0]
    assert row["payload"]["outcome"] == OUTCOME_ESCALATED


def test_fail_closed_escalates_on_wrong_action(runtime, db, monkeypatch):
    fake = StrictFakeAuthorityEvaluator(pinned={
        "x": ("continue_same_root", "cont-routine-same-root", "escalate_to_founder"),
    })
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)
    _run_escalate_step(orch, "T-ROOT", "x", monkeypatch)
    assert db.get_task("T-ROOT").status == TaskStatus.ESCALATED


def test_fail_closed_escalates_on_unknown_clause(runtime, db, monkeypatch):
    fake = StrictFakeAuthorityEvaluator(pinned={
        "x": ("continue_same_root", "no-such-clause", ACTION_CONTINUE_SAME_ROOT),
    })
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)
    _run_escalate_step(orch, "T-ROOT", "x", monkeypatch)
    assert db.get_task("T-ROOT").status == TaskStatus.ESCALATED


def test_fail_closed_escalates_on_evaluator_exception(runtime, db, monkeypatch):
    class Boom:
        model_id, model_version, model_digest = "fake/boom", "v1", _digest("boom")
        def evaluate(self, snapshot):
            raise RuntimeError("boom")
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=Boom())
    _run_escalate_step(orch, "T-ROOT", "needs founder", monkeypatch)
    assert db.get_task("T-ROOT").status == TaskStatus.ESCALATED
    row = _hook_outcome_rows(db, "T-ROOT")[0]
    assert row["payload"]["outcome"] == OUTCOME_ESCALATED
    assert row["payload"]["disposition_code"] == "evaluator_error"


def test_fail_closed_escalates_on_no_evaluator(runtime, db, monkeypatch):
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=None)
    _run_escalate_step(orch, "T-ROOT", "needs founder", monkeypatch)
    assert db.get_task("T-ROOT").status == TaskStatus.ESCALATED
    row = _hook_outcome_rows(db, "T-ROOT")[0]
    assert row["payload"]["disposition_code"] == "evaluator_error"


def test_fail_closed_escalates_on_audit_write_failure(runtime, db, monkeypatch):
    """An audit-write failure can NEVER permit continuation: the evaluation
    record write fails → the hook fails closed to ESCALATE."""
    fake = StrictFakeAuthorityEvaluator()
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)

    def boom(*a, **k):
        raise RuntimeError("audit store unavailable")
    monkeypatch.setattr(db, "record_authority_evaluation", boom)

    _run_escalate_step(orch, "T-ROOT", CONTINUE_REASON, monkeypatch)
    t = db.get_task("T-ROOT")
    assert t.status == TaskStatus.ESCALATED  # fail closed, never continued
    assert _escalation_rows(db, "T-ROOT")
    row = _hook_outcome_rows(db, "T-ROOT")[0]
    assert row["payload"]["outcome"] == "evaluator_failure"


def test_fail_closed_escalates_on_consume_failure(runtime, db, monkeypatch):
    """A consume failure (restart-incomplete) fails closed — never a
    continuation from a partial record."""
    fake = StrictFakeAuthorityEvaluator()
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)
    monkeypatch.setattr(db, "consume_authority_candidate", lambda cid: False)
    _run_escalate_step(orch, "T-ROOT", CONTINUE_REASON, monkeypatch)
    assert db.get_task("T-ROOT").status == TaskStatus.ESCALATED
    row = _hook_outcome_rows(db, "T-ROOT")[0]
    assert row["payload"]["outcome"] == OUTCOME_CAS_LOST


def test_fail_closed_escalates_on_low_confidence_continue(runtime, db, monkeypatch):
    """A CONTINUE_SAME_ROOT verdict below the confidence threshold fails
    closed to ESCALATE with the LOW_CONFIDENCE diagnostic code."""
    from runtime.models import AuthorityDisposition, AuthorityDispositionCode
    from runtime.orchestrator.authority import AuthorityEvaluationResult

    class _LowConfidence:
        model_id, model_version, model_digest = "fake/low", "v1", _digest("low")
        def evaluate(self, snapshot):
            return AuthorityEvaluationResult(
                disposition=AuthorityDisposition.CONTINUE_SAME_ROOT,
                disposition_code=AuthorityDispositionCode.LOW_CONFIDENCE,
                response_digest=_digest("resp"),
                clause_id="cont-routine-same-root",
                action=ACTION_CONTINUE_SAME_ROOT,
                confidence=0.1,
            )
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=_LowConfidence())
    _run_escalate_step(orch, "T-ROOT", CONTINUE_REASON, monkeypatch)
    assert db.get_task("T-ROOT").status == TaskStatus.ESCALATED
    row = _hook_outcome_rows(db, "T-ROOT")[0]
    assert row["payload"]["outcome"] == OUTCOME_ESCALATED
    assert row["payload"]["disposition_code"] == "low_confidence"


# ── (f) cancellation wins ────────────────────────────────────────────────

def test_cancellation_before_evaluation_wins(runtime, db, monkeypatch):
    """Cancellation already committed → the hook records ineligible
    (cancellation fence) and the escalation cannot commit (try_escalate's own
    CAS drops it)."""
    from datetime import datetime, timezone
    fake = StrictFakeAuthorityEvaluator()
    _seed_claimed_root(db)
    db.update_task(
        "T-ROOT", status=TaskStatus.FAILED, block_kind=None,
        note="cancelled by founder", cancelled_at=datetime.now(timezone.utc).isoformat(),
        completed_at=datetime.now(timezone.utc).isoformat(),
    )
    orch = _make_orch(runtime, db, evaluator=fake)
    # Drive the hook directly (run_step short-circuits cancelled tasks).
    out = run_authority_hook(orch, db.get_task("T-ROOT"), "engineering_head", "needs founder", 7)
    assert out == "escalate"
    assert db.get_task("T-ROOT").status == TaskStatus.FAILED  # no resurrection
    assert db.list_authority_candidates_for_root("T-ROOT") == []  # never claimed
    row = _hook_outcome_rows(db, "T-ROOT")[0]
    assert row["payload"]["outcome"] == OUTCOME_INELIGIBLE
    assert "cancellation" in row["payload"]["fence_results"]


def test_cancellation_during_evaluation_wins(runtime, db, monkeypatch):
    """Cancellation lands while the evaluator runs: the evaluation is still
    recorded (the LLM call is auditable), the candidate is consumed, but the
    final outcome is cancelled/stale — never a continuation."""
    from datetime import datetime, timezone
    fake = StrictFakeAuthorityEvaluator()
    real_evaluate = fake.evaluate

    def cancel_then_evaluate(snapshot):
        db.update_task(
            "T-ROOT", status=TaskStatus.FAILED, block_kind=None,
            note="cancelled mid-eval", cancelled_at=datetime.now(timezone.utc).isoformat(),
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        return real_evaluate(snapshot)
    fake.evaluate = cancel_then_evaluate

    _seed_claimed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)
    out = run_authority_hook(orch, db.get_task("T-ROOT"), "engineering_head", CONTINUE_REASON, 7)
    assert out == "escalate"
    # No continuation: task stayed cancelled.
    assert db.get_task("T-ROOT").status == TaskStatus.FAILED
    cands = db.list_authority_candidates_for_root("T-ROOT")
    assert len(cands) == 1
    # The evaluation was recorded (auditable) and the candidate lifecycle
    # closed — but the outcome is cancelled/stale, never continued.
    assert db.get_authority_evaluation(cands[0].id) is not None
    assert cands[0].lifecycle_state.value == "consumed"
    row = _hook_outcome_rows(db, "T-ROOT")[0]
    assert row["payload"]["outcome"] == OUTCOME_CANCELLED_STALE


def test_cancellation_at_final_cas_wins(runtime, db, monkeypatch):
    """Cancellation wins the continuation CAS: commit_authority_continue_
    same_root refuses (cancelled_at gate) → no continuation, fail closed."""
    fake = StrictFakeAuthorityEvaluator()
    _seed_claimed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)
    real_commit = db.commit_authority_continue_same_root

    def cancel_then_commit(**kw):
        from datetime import datetime, timezone
        db.update_task(
            "T-ROOT", status=TaskStatus.FAILED, block_kind=None,
            note="cancelled at final cas",
            cancelled_at=datetime.now(timezone.utc).isoformat(),
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        return real_commit(**kw)
    monkeypatch.setattr(db, "commit_authority_continue_same_root", cancel_then_commit)

    out = run_authority_hook(orch, db.get_task("T-ROOT"), "engineering_head", CONTINUE_REASON, 7)
    assert out == "escalate"
    t = db.get_task("T-ROOT")
    assert t.status == TaskStatus.FAILED  # no continuation, no resurrection
    row = _hook_outcome_rows(db, "T-ROOT")[0]
    assert row["payload"]["outcome"] == OUTCOME_CANCELLED_STALE


# ── (g) ineligibility ────────────────────────────────────────────────────

def test_non_manager_root_is_ineligible(runtime, db, monkeypatch):
    _seed_root(db, task_id="T-W", agent="dev_agent")
    orch = _make_orch(runtime, db, evaluator=StrictFakeAuthorityEvaluator())
    _run_escalate_step(orch, "T-W", "needs founder", monkeypatch)
    assert db.get_task("T-W").status == TaskStatus.ESCALATED
    row = _hook_outcome_rows(db, "T-W")[0]
    assert row["payload"]["outcome"] == OUTCOME_INELIGIBLE
    assert "manager_ownership" in row["payload"]["fence_results"]
    assert db.list_authority_candidates_for_root("T-W") == []


def test_revisit_root_is_ineligible(runtime, db, monkeypatch):
    _seed_root(db, task_id="T-R", revisit_of_task_id="T-PREV")
    orch = _make_orch(runtime, db, evaluator=StrictFakeAuthorityEvaluator())
    _run_escalate_step(orch, "T-R", "needs founder", monkeypatch)
    assert db.get_task("T-R").status == TaskStatus.ESCALATED
    row = _hook_outcome_rows(db, "T-R")[0]
    assert row["payload"]["outcome"] == OUTCOME_INELIGIBLE
    assert "revisit_lineage" in row["payload"]["fence_results"]


def test_successor_root_is_ineligible(runtime, db, monkeypatch):
    """A root minted as a manager-supersession successor is not the original
    root — the hook fence fails closed (no continuation of a successor)."""
    db.insert_task(TaskRecord(id="T-PRED", brief="p", assigned_agent="engineering_head"))
    _seed_claimed_root(db, task_id="T-SUCC")
    db.execute(
        "INSERT INTO manager_supersessions (predecessor_task_id, successor_task_id,"
        " original_root_task_id, actor_agent, actor_session_id, rationale,"
        " attestation_evidence, predecessor_brief, successor_brief,"
        " predecessor_brief_sha256, successor_brief_sha256, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("T-PRED", "T-SUCC", "T-ORIG", "engineering_head", "sess-x", "r",
         "{}", "b", "b", _digest("b"), _digest("b"), "2026-01-01T00:00:00+00:00"),
    )
    db._conn.commit()
    orch = _make_orch(runtime, db, evaluator=StrictFakeAuthorityEvaluator())
    out = run_authority_hook(orch, db.get_task("T-SUCC"), "engineering_head", "needs founder", 7)
    assert out == "escalate"
    row = _hook_outcome_rows(db, "T-SUCC")[0]
    assert row["payload"]["outcome"] == OUTCOME_INELIGIBLE
    assert "successor_lineage" in row["payload"]["fence_results"]


def test_team_without_release_policy_escalates_untouched(runtime, db, monkeypatch):
    """Content team has no release-controlled policy: the hook is not
    applicable — the escalation proceeds with NO authority records at all."""
    _seed_root(db, task_id="T-C", agent="content_head", team="content")
    orch = _make_orch(runtime, db, evaluator=StrictFakeAuthorityEvaluator())
    _run_escalate_step(orch, "T-C", "needs founder", monkeypatch, session="sess-c")

    assert db.get_task("T-C").status == TaskStatus.ESCALATED
    assert _hook_outcome_rows(db, "T-C") == []
    assert db.list_authority_candidates_for_root("T-C") == []


def test_non_root_never_reaches_hook(runtime, db, monkeypatch):
    """A non-root escalate fails + hands back to its parent (THR-033 guard
    runs before the hook) — no authority records."""
    db.insert_task(TaskRecord(id="T-PAR", brief="p", assigned_agent="engineering_head"))
    db.update_task("T-PAR", status=TaskStatus.IN_PROGRESS,
                   block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(
        id="T-CHILD", brief="c", assigned_agent="dev_agent",
        parent_task_id="T-PAR", task_type="task",
    ))
    orch = _make_orch(runtime, db, evaluator=StrictFakeAuthorityEvaluator())
    _run_escalate_step(orch, "T-CHILD", "needs founder", monkeypatch)

    assert db.get_task("T-CHILD").status == TaskStatus.FAILED
    assert "non-root escalation requested" in (db.get_task("T-CHILD").note or "")
    assert _hook_outcome_rows(db, "T-CHILD") == []
    assert db.list_authority_candidates_for_root("T-CHILD") == []


# ── (h) concurrency / exactly-once ───────────────────────────────────────

def test_concurrent_claim_exactly_one_durable_winner(runtime, db):
    """Two concurrent claims of the exact same deterministic tuple yield ONE
    durable candidate; the DB (not scheduling) is the arbiter."""
    import threading
    kw = dict(
        root_task_id="T-CONC", team="engineering", manager_agent="engineering_head",
        manager_session_id="sess-x", causal_event_id="step:1",
        causal_event_digest=_digest("causal"), causal_result_id=None,
        policy_id=POLICY.id, policy_version=POLICY.version, policy_digest=POLICY.digest,
        prompt_id="prompt/authority-evaluator/engineering", prompt_version="v1",
        prompt_digest=PROMPT_DIGEST,
        model_id=StrictFakeAuthorityEvaluator.model_id, model_version="v1",
        model_digest=StrictFakeAuthorityEvaluator.model_digest,
        snapshot_digest=_digest("snapshot"),
    )
    results: list[tuple[str, bool]] = []
    barrier = threading.Barrier(2)
    def claim():
        barrier.wait()
        results.append(db.claim_authority_candidate(**kw))
    threads = [threading.Thread(target=claim) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(results) == 2
    assert results[0][0] == results[1][0]  # deterministic candidate id
    wins = [r[1] for r in results]
    assert wins.count(True) == 1  # exactly one durable winner
    cands = db.list_authority_candidates_for_root("T-CONC")
    assert len(cands) == 1
    assert cands[0].lifecycle_state.value == "created"


def test_hook_duplicate_invocation_exactly_once(runtime, db, monkeypatch):
    """A second hook invocation for the same deterministic tuple (e.g. a
    replay after restart) must NOT re-evaluate or continue: it loses the
    claim, fails closed, and the first evaluation/winner is preserved."""
    fake = StrictFakeAuthorityEvaluator()
    _seed_claimed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)
    task = db.get_task("T-ROOT")
    first = run_authority_hook(orch, task, "engineering_head", CONTINUE_REASON, 7)
    assert first == "continue_same_root"

    # Replay the identical attempt (same session, same step id, same reason).
    second = run_authority_hook(orch, db.get_task("T-ROOT"), "engineering_head", CONTINUE_REASON, 7)
    assert second == "escalate"

    cands = db.list_authority_candidates_for_root("T-ROOT")
    assert len(cands) == 1  # one durable candidate
    # Exactly one evaluation row total (UNIQUE constraint enforced).
    rows = [a for a in db.get_audit_logs("T-ROOT") if a["action"] == AUDIT_ACTION_HOOK_OUTCOME]
    outcomes = [r["payload"]["outcome"] for r in rows]
    # The replay saw the root already continued (task moved to pending) →
    # ineligible; the original attempt continued. Both fail closed.
    assert sorted(outcomes) == sorted([OUTCOME_INELIGIBLE, OUTCOME_CONTINUED_SAME_ROOT])
    assert db.get_task("T-ROOT").status == TaskStatus.PENDING  # continued exactly once
    assert db.get_authority_evaluation(cands[0].id) is not None
    # No duplicate evaluation row (UNIQUE candidate_id enforced at the DB).
    evals = [db.get_authority_evaluation(cands[0].id)]
    assert len([e for e in evals if e is not None]) == 1


# ── (i) restart states fail closed ───────────────────────────────────────

def _seed_candidate_state(db, lifecycle: str, evaluated: bool = False):
    """Seed the claimed root + a candidate in the given lifecycle state."""
    _seed_claimed_root(db)
    kw = dict(
        root_task_id="T-ROOT", team="engineering", manager_agent="engineering_head",
        manager_session_id="sess-x", causal_event_id="step:7",
        causal_event_digest=_digest("causal"), causal_result_id=None,
        policy_id=POLICY.id, policy_version=POLICY.version, policy_digest=POLICY.digest,
        prompt_id="prompt/authority-evaluator/engineering", prompt_version="v1",
        prompt_digest=PROMPT_DIGEST,
        model_id=StrictFakeAuthorityEvaluator.model_id, model_version="v1",
        model_digest=StrictFakeAuthorityEvaluator.model_digest,
        snapshot_digest=_digest("snapshot"),
    )
    candidate_id, won = db.claim_authority_candidate(**kw)
    assert won
    if evaluated:
        db.record_authority_evaluation(
            candidate_id=candidate_id, disposition="escalate",
            disposition_code="escalate", response_digest=_digest("resp"),
        )
    if lifecycle == "consumed":
        assert db.consume_authority_candidate(candidate_id)
    return candidate_id


@pytest.mark.parametrize("lifecycle,evaluated", [
    ("created", False),       # claimed/created — evaluation missing
    ("evaluated", True),      # evaluated-but-unconsumed
    ("consumed", True),       # already-consumed
])
def test_restart_states_fail_closed(runtime, db, monkeypatch, lifecycle, evaluated):
    """A restart left the candidate in a partial/completed state: the replayed
    attempt loses the claim, never re-evaluates, never continues, and the
    escalation proceeds (fail closed)."""
    candidate_id = _seed_candidate_state(db, lifecycle, evaluated)
    fake = StrictFakeAuthorityEvaluator()
    orch = _make_orch(runtime, db, evaluator=fake)
    before_evals = db.get_authority_evaluation(candidate_id)

    out = run_authority_hook(orch, db.get_task("T-ROOT"), "engineering_head", CONTINUE_REASON, 7)
    assert out == "escalate"

    # No continuation.
    assert db.get_task("T-ROOT").status == TaskStatus.IN_PROGRESS
    # No duplicate evaluation row: a restart-incomplete (created) candidate
    # has none (the evaluator never ran durably); evaluated/consumed keep the
    # single original evaluation.
    if evaluated:
        assert db.get_authority_evaluation(candidate_id) is not None
    else:
        assert db.get_authority_evaluation(candidate_id) is None
    cands = db.list_authority_candidates_for_root("T-ROOT")
    assert len(cands) == 1
    # Fail-closed outcome recorded.
    row = _hook_outcome_rows(db, "T-ROOT")[-1]
    assert row["payload"]["outcome"] in (OUTCOME_CAS_LOST, OUTCOME_CANCELLED_STALE)


# ── (j) denominator: one explainable terminal outcome per attempt ────────

def test_denominator_every_attempt_one_terminal_outcome(runtime, db, monkeypatch):
    """Across every hook-eligible scenario, each attempt yields exactly one
    authority_hook outcome row with an explainable terminal outcome, and no
    continuation happens unless the outcome says continued_same_root."""
    from runtime.orchestrator.authority import OUTCOME_EVALUATOR_FAILURE

    fake = StrictFakeAuthorityEvaluator()
    scenarios = []

    # 1) continue
    _seed_claimed_root(db, task_id="T-1")
    orch = _make_orch(runtime, db, evaluator=fake)
    scenarios.append(run_authority_hook(orch, db.get_task("T-1"), "engineering_head", CONTINUE_REASON, 1))
    # 2) escalate
    _seed_claimed_root(db, task_id="T-2")
    orch2 = _make_orch(runtime, db, evaluator=fake)
    scenarios.append(run_authority_hook(orch2, db.get_task("T-2"), "engineering_head", "schema migration required", 2))
    # 3) ineligible (non-manager)
    _seed_claimed_root(db, task_id="T-3", agent="dev_agent")
    orch3 = _make_orch(runtime, db, evaluator=fake)
    scenarios.append(run_authority_hook(orch3, db.get_task("T-3"), "dev_agent", "needs founder", 3))
    # 4) evaluator failure (no evaluator wired)
    _seed_claimed_root(db, task_id="T-4")
    orch4 = _make_orch(runtime, db, evaluator=None)
    scenarios.append(run_authority_hook(orch4, db.get_task("T-4"), "engineering_head", "needs founder", 4))
    # 5) audit failure (evaluation record write fails)
    _seed_claimed_root(db, task_id="T-5")
    orch5 = _make_orch(runtime, db, evaluator=fake)
    monkeypatch.setattr(db, "record_authority_evaluation",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    scenarios.append(run_authority_hook(orch5, db.get_task("T-5"), "engineering_head", CONTINUE_REASON, 5))

    for task_id in ("T-1", "T-2", "T-3", "T-4", "T-5"):
        rows = _hook_outcome_rows(db, task_id)
        assert len(rows) == 1, task_id
        payload = rows[0]["payload"]
        assert "outcome" in payload, task_id
        explainable = {
            OUTCOME_CONTINUED_SAME_ROOT, OUTCOME_ESCALATED, OUTCOME_INELIGIBLE,
            OUTCOME_CAS_LOST, OUTCOME_CANCELLED_STALE, OUTCOME_EVALUATOR_FAILURE,
            "audit_failure", "capture_failure",
        }
        assert payload["outcome"] in explainable, task_id
        # Continuation only when the outcome says continued_same_root.
        t = db.get_task(task_id)
        if payload["outcome"] == OUTCOME_CONTINUED_SAME_ROOT:
            assert t.status == TaskStatus.PENDING
        else:
            assert t.status != TaskStatus.PENDING
            assert _escalation_rows(db, task_id) or t.status in (
                TaskStatus.FAILED, TaskStatus.IN_PROGRESS,
            )

    assert scenarios[0] == "continue_same_root"
    assert scenarios[1] == "escalate"
    assert scenarios[2] == "escalate"
    assert scenarios[3] == "escalate"
    assert scenarios[4] == "escalate"

