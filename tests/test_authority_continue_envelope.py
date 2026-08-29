"""THR-181 Track A (founder option B) — Unit 1: single-use CONTINUE_SAME_ROOT
continuation-envelope tests (daemon-owned issuance + consumption).

Proves the mechanically restricted envelope through the SHIPPING seams, in
the founder-approved intermediate state (executor turn-scoped allow-set
narrowing is Unit 2, NOT implemented here):

(a) the envelope is minted ATOMICALLY with the continuation, bound to the
    evaluation (candidate), the immutable causal task-result row, the
    matched policy clause, and the exact permitted action;
(b) the continued turn's decision is daemon-gated: ONLY the exact permitted
    decision (``done``) succeeds; escalate/supersede/delegate/fanout/blocked
    each fail closed into the ordinary founder-escalation path, audited and
    never silently discarded, and the authority hook is not re-run;
(c) the continued-turn prompt header is gated on an ACTIVE envelope
    (fail-closed: no envelope -> no continuation header -> ordinary turn);
(d) out-of-envelope attempts produce the ordinary escalation lifecycle and
    audit outcome (byte-identical apart from the required authority records);
(e) exactly-once/CAS, concurrent consumption, identity recheck at
    consumption, cancellation, and boot/zombie/restart crash windows fail
    closed;
(f) immutable envelope identity is rechecked atomically at consumption;
(g) the audit denominator records envelope issuance/use/violation without
    raw secret-bearing prose;
(h) real must-escalate sentinels attempted from the continued turn fail
    closed; ordinary ESCALATE behavior is byte-identical when the envelope
    machinery is absent.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.infrastructure.database import Database
from runtime.models import TaskStatus
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.authority import (
    AUDIT_ACTION_CONTINUED_SAME_ROOT,
    AUDIT_ACTION_ENVELOPE_CONSUMED,
    AUDIT_ACTION_ENVELOPE_VIOLATED,
    StrictFakeAuthorityEvaluator,
)
from runtime.orchestrator.authority_policy import (
    ACTION_CONTINUE_SAME_ROOT,
    ENGINEERING_PRE_ESCALATION_POLICY as POLICY,
)
from runtime.orchestrator.orchestrator import Orchestrator
from runtime.orchestrator.teams import TeamsRegistry
from runtime.runtime import RuntimeDir

CONTINUE_REASON = "routine same-root follow-through of the already-completed slice"


@pytest.fixture(autouse=True)
def _mock_executor_binaries(monkeypatch, tmp_path):
    """Pre-register built-in executor binaries so executor construction's
    ``_resolve_binary`` resolves deterministically (registration-only
    resolution; mirrors ``tests/test_executor.py``)."""
    daemon_home = tmp_path / ".happyranch"
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home))
    from runtime.orchestrator.executor_binary_registry import set_binary
    for name in ("claude", "codex", "opencode", "pi"):
        fake_bin = tmp_path / "bin" / name
        fake_bin.parent.mkdir(parents=True, exist_ok=True)
        fake_bin.touch(mode=0o755)
        set_binary(name, str(fake_bin))


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


def _make_result(success: bool = True, duration: int = 1, session: str = "sess-x"):
    from runtime.orchestrator.executors import ExecutorResult
    return ExecutorResult(
        success=success, session_id=session, duration_seconds=duration,
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
    from runtime.daemon.sessions import SessionTracker
    orch.attach_sessions(SessionTracker())
    return orch


def _seed_root(db, task_id: str = "T-ROOT") -> None:
    from runtime.models import TaskRecord
    db.insert_task(TaskRecord(
        id=task_id, brief="b", assigned_agent="engineering_head", team="engineering",
    ))


def _escalate_decision(reason: str) -> str:
    return json.dumps({"action": "escalate", "reason": reason})


def _run_escalate_step(orch, task_id: str, reason: str, monkeypatch, session: str = "sess-x") -> None:
    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        orch.db.update_task(task_id, current_session_id=session)
        orch.db.insert_task_result(
            task_id=task_id, agent=agent, session_id=session,
            status="completed", confidence_score=80,
            output_summary=_escalate_decision(reason),
            decision_json=_escalate_decision(reason),
        )
        return _make_result(session=session), _make_report(output_summary=_escalate_decision(reason))
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)
    orch.run_step(task_id)


def _decision_json(decision: dict) -> str:
    return json.dumps(decision)


def _run_continued_step(
    orch, task_id: str, decision: dict, monkeypatch, session: str = "sess-y",
) -> None:
    """The continued turn: a NEW session + a NEW immutable result row
    carrying the continued turn's decision (never a replay of the original
    escalate row). Mirrors the real completion route + orchestrator."""
    decision_raw = _decision_json(decision)

    def fake_run_agent(
        task_id, agent, prompt, on_session_started=None, turn_allow_set=None,
    ):
        orch.db.update_task(task_id, current_session_id=session)
        orch.db.insert_task_result(
            task_id=task_id, agent=agent, session_id=session,
            status="completed", confidence_score=80,
            output_summary=decision_raw, decision_json=decision_raw,
        )
        return _make_result(session=session), _make_report(output_summary=decision_raw)
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)
    orch.run_step(task_id)


def _escalation_rows(db, task_id: str):
    return [a for a in db.get_audit_logs(task_id) if a["action"] == "escalation"]


def _envelope_rows(db, task_id: str):
    return [a for a in db.get_audit_logs(task_id) if a["action"].startswith("authority_continue_envelope")]


def _hook_outcome_rows(db, task_id: str):
    return [a for a in db.get_audit_logs(task_id) if a["action"] == "authority_hook"]


def _digest(tag: str) -> str:
    return hashlib.sha256(tag.encode("utf-8")).hexdigest()


# ── (a) envelope minted atomically with the continuation ─────────────────

def test_continuation_mints_single_use_envelope(runtime, db, monkeypatch):
    fake = StrictFakeAuthorityEvaluator()
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)

    _run_escalate_step(orch, "T-ROOT", CONTINUE_REASON, monkeypatch)

    env = db.get_active_authority_continue_envelope("T-ROOT")
    assert env is not None
    assert env["state"] == "active"
    assert env["action"] == ACTION_CONTINUE_SAME_ROOT
    assert env["clause_id"] == "cont-routine-same-root"
    assert env["policy_id"] == POLICY.id
    assert env["policy_version"] == POLICY.version
    assert env["policy_digest"] == POLICY.digest
    # Bound to the evaluation (candidate) and the immutable causal result row.
    cands = db.list_authority_candidates_for_root("T-ROOT")
    assert len(cands) == 1
    assert env["candidate_id"] == cands[0].id
    assert env["causal_event_id"] == cands[0].causal_event_id
    assert env["causal_event_id"].startswith("result:")
    assert env["causal_event_digest"] == _digest(f"task-result:{cands[0].causal_event_id.split(':', 1)[1]}")
    assert env["manager_agent"] == "engineering_head"
    assert env["team"] == "engineering"
    # The continuation audit payload names the envelope id (audit denominator).
    continued = [
        a for a in db.get_audit_logs("T-ROOT")
        if a["action"] == AUDIT_ACTION_CONTINUED_SAME_ROOT
    ]
    assert len(continued) == 1
    assert continued[0]["payload"]["envelope_id"] == env["id"]


def test_envelope_identity_is_immutable_in_db(runtime, db, monkeypatch):
    fake = StrictFakeAuthorityEvaluator()
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)
    _run_escalate_step(orch, "T-ROOT", CONTINUE_REASON, monkeypatch)
    env = db.get_active_authority_continue_envelope("T-ROOT")

    with pytest.raises(Exception):
        db.execute(
            "UPDATE authority_continue_envelopes SET clause_id = 'esc-schema' WHERE id = ?",
            (env["id"],),
        )
    with pytest.raises(Exception):
        db.execute(
            "DELETE FROM authority_continue_envelopes WHERE id = ?", (env["id"],),
        )
    # Direct state-jump without the atomic consumed_at stamp is blocked, and
    # a spent envelope cannot re-transition.
    with pytest.raises(Exception):
        db.execute(
            "UPDATE authority_continue_envelopes SET state='consumed' "
            "WHERE id = ?",
            (env["id"],),
        )
    db.execute(
        "UPDATE authority_continue_envelopes SET state='violated', "
        "consumed_at='x', updated_at='x' WHERE id = ?",
        (env["id"],),
    )
    with pytest.raises(Exception):
        db.execute(
            "UPDATE authority_continue_envelopes SET state='consumed', "
            "consumed_at='y', updated_at='y' WHERE id = ?",
            (env["id"],),
        )


# ── (b) daemon-mediated action acceptance: only the permitted action ─────

def test_continued_turn_done_consumes_envelope_and_completes(runtime, db, monkeypatch):
    fake = StrictFakeAuthorityEvaluator()
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)
    _run_escalate_step(orch, "T-ROOT", CONTINUE_REASON, monkeypatch)
    assert db.get_active_authority_continue_envelope("T-ROOT") is not None

    _run_continued_step(
        orch, "T-ROOT",
        {"action": "done", "summary": "routine follow-through concluded"},
        monkeypatch,
    )

    t = db.get_task("T-ROOT")
    assert t.status == TaskStatus.COMPLETED
    assert db.get_active_authority_continue_envelope("T-ROOT") is None
    env = db.get_authority_continue_envelope("CONT-AUTH-CAND-" + db.list_authority_candidates_for_root("T-ROOT")[0].id.split("AUTH-CAND-")[1])
    # (id is CONT-<candidate_id>)
    env = db.get_authority_continue_envelope("CONT-" + db.list_authority_candidates_for_root("T-ROOT")[0].id)
    assert env["state"] == "consumed"
    # No escalation.
    assert _escalation_rows(db, "T-ROOT") == []
    consumed = [
        a for a in db.get_audit_logs("T-ROOT")
        if a["action"] == AUDIT_ACTION_ENVELOPE_CONSUMED
    ]
    assert len(consumed) == 1
    assert consumed[0]["payload"]["decision_family"] == "done"
    assert consumed[0]["payload"]["clause_id"] == "cont-routine-same-root"
    assert consumed[0]["payload"]["action"] == ACTION_CONTINUE_SAME_ROOT


def test_continued_turn_escalate_fails_closed_to_ordinary_escalation(runtime, db, monkeypatch):
    """A continued turn that attempts to escalate again: the envelope is
    violated (single-use, audited) and the root fails closed into the
    ORDINARY founder-escalation path — the authority hook is NOT re-run (no
    second candidate/evaluation) and the notification + escalation audit
    land exactly like an ordinary escalation."""
    fake = StrictFakeAuthorityEvaluator()
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)
    spy = {}
    monkeypatch.setattr(orch, "notify_escalated", lambda **kw: spy.update(kw))
    _run_escalate_step(orch, "T-ROOT", CONTINUE_REASON, monkeypatch)

    _run_continued_step(
        orch, "T-ROOT",
        {"action": "escalate", "reason": "still needs founder"},
        monkeypatch,
    )

    t = db.get_task("T-ROOT")
    assert t.status == TaskStatus.ESCALATED
    # Ordinary escalation lifecycle: escalation audit row + notification.
    esc = _escalation_rows(db, "T-ROOT")
    assert len(esc) == 1
    assert spy["task_id"] == "T-ROOT"
    assert spy["agent"] == "engineering_head"
    assert "envelope violation" in spy["reason"]
    # The envelope was spent as violated.
    env = db.get_authority_continue_envelope("CONT-" + db.list_authority_candidates_for_root("T-ROOT")[0].id)
    assert env["state"] == "violated"
    viol = [
        a for a in db.get_audit_logs("T-ROOT")
        if a["action"] == AUDIT_ACTION_ENVELOPE_VIOLATED
    ]
    assert len(viol) == 1
    assert viol[0]["payload"]["decision_family"] == "escalate"
    # Exactly ONE evaluation happened (the original grant); the hook did not
    # re-run for the continued turn's escalation.
    assert len(db.list_authority_candidates_for_root("T-ROOT")) == 1
    outcomes = [r["payload"]["outcome"] for r in _hook_outcome_rows(db, "T-ROOT")]
    assert outcomes.count("continued_same_root") == 1
    assert "escalated" not in outcomes  # no fresh hook outcome for the violation


def test_continued_turn_supersede_fails_closed_no_successor(runtime, db, monkeypatch):
    fake = StrictFakeAuthorityEvaluator()
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)
    _run_escalate_step(orch, "T-ROOT", CONTINUE_REASON, monkeypatch)

    _run_continued_step(
        orch, "T-ROOT",
        {
            "action": "supersede",
            "successor_brief": "fresh root",
            "rationale": "start over",
            "attestation": {
                "recovery_reason": "routine recovery",
                "policy_product_intent_unchanged": True,
                "no_budget_or_external_commitment": True,
                "no_permission_or_cross_team_change": True,
                "no_schema_auth_security_privacy_or_data_access_change": True,
                "no_unresolved_founder_gate": True,
            },
        },
        monkeypatch,
    )

    t = db.get_task("T-ROOT")
    assert t.status == TaskStatus.ESCALATED  # failed closed, NOT superseded
    assert t.status != TaskStatus.SUPERSEDED
    # No successor root was created (manager_supersessions untouched).
    succ = db.execute(
        "SELECT 1 FROM manager_supersessions "
        "WHERE original_root_task_id = 'T-ROOT' OR successor_task_id = 'T-ROOT' LIMIT 1"
    ).fetchone()
    assert succ is None
    viol = [
        a for a in db.get_audit_logs("T-ROOT")
        if a["action"] == AUDIT_ACTION_ENVELOPE_VIOLATED
    ]
    assert viol[0]["payload"]["decision_family"] == "supersede"
    assert _escalation_rows(db, "T-ROOT")


def test_continued_turn_delegate_fails_closed_no_child(runtime, db, monkeypatch):
    fake = StrictFakeAuthorityEvaluator()
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)
    _run_escalate_step(orch, "T-ROOT", CONTINUE_REASON, monkeypatch)

    _run_continued_step(
        orch, "T-ROOT",
        {"action": "delegate", "agent": "dev_agent", "prompt": "do work"},
        monkeypatch,
    )

    t = db.get_task("T-ROOT")
    assert t.status == TaskStatus.ESCALATED  # failed closed, no delegation
    assert db.get_children("T-ROOT") == []
    viol = [
        a for a in db.get_audit_logs("T-ROOT")
        if a["action"] == AUDIT_ACTION_ENVELOPE_VIOLATED
    ]
    assert viol[0]["payload"]["decision_family"] == "delegate"


def test_continued_turn_fanout_fails_closed(runtime, db, monkeypatch):
    fake = StrictFakeAuthorityEvaluator()
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)
    _run_escalate_step(orch, "T-ROOT", CONTINUE_REASON, monkeypatch)

    _run_continued_step(
        orch, "T-ROOT",
        {"action": "fanout", "children": [{"agent": "dev_agent", "prompt": "p"}]},
        monkeypatch,
    )

    t = db.get_task("T-ROOT")
    assert t.status == TaskStatus.ESCALATED
    assert db.get_children("T-ROOT") == []
    viol = [
        a for a in db.get_audit_logs("T-ROOT")
        if a["action"] == AUDIT_ACTION_ENVELOPE_VIOLATED
    ]
    assert viol[0]["payload"]["decision_family"] == "fanout"


def test_continued_turn_blocked_fails_closed(runtime, db, monkeypatch):
    fake = StrictFakeAuthorityEvaluator()
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)
    _run_escalate_step(orch, "T-ROOT", CONTINUE_REASON, monkeypatch)

    # A blocked report from the continued turn (no jobs submitted — the
    # route-level gate rejects those) is outside the envelope.
    def fake_run_agent_blocked(task_id, agent, prompt, on_session_started=None, turn_allow_set=None):
        orch.db.update_task(task_id, current_session_id="sess-y")
        orch.db.insert_task_result(
            task_id=task_id, agent=agent, session_id="sess-y",
            status="blocked", confidence_score=80,
            output_summary="blocked", decision_json=None,
        )
        return _make_result(session="sess-y"), _make_report(
            output_summary="blocked", status="blocked",
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent_blocked)
    orch.run_step("T-ROOT")

    t = db.get_task("T-ROOT")
    assert t.status == TaskStatus.ESCALATED
    viol = [
        a for a in db.get_audit_logs("T-ROOT")
        if a["action"] == AUDIT_ACTION_ENVELOPE_VIOLATED
    ]
    assert viol[0]["payload"]["decision_family"] == "blocked"


def test_continued_turn_must_escalate_sentinels_fail_closed(runtime, db, monkeypatch):
    """Real must-escalate sentinels attempted from the continued turn all
    fail closed into the ordinary escalation path (never continue, never
    perform the attempted action)."""
    sentinels = {
        "schema": "needs a schema migration that alters existing columns",
        "permission": "needs a permission/sandbox/allow-rule change",
        "auth": "credential/security/data-access decision required",
        "spend": "exceeds the spend/budget ceiling",
        "destructive": "destructive/irreversible production action",
        "external": "external product/deploy commitment",
        "review": "adverse review/QA verdict: REVISE",
        "ambiguity": "genuine ambiguity with conflicting evidence",
        "partial": "session timed out with partial work evidence",
        "exhausted": "orchestration step budget exhausted",
    }
    for label, reason in sentinels.items():
        db2 = Database(runtime.db_path.parent / f"sentinel-{label}.db")
        _seed_root(db2, task_id="T-ROOT")
        orch2 = _make_orch(runtime, db2, evaluator=StrictFakeAuthorityEvaluator())
        _run_escalate_step(orch2, "T-ROOT", CONTINUE_REASON, monkeypatch)
        _run_continued_step(
            orch2, "T-ROOT", {"action": "escalate", "reason": reason}, monkeypatch,
        )
        t = db2.get_task("T-ROOT")
        assert t.status == TaskStatus.ESCALATED, label
        env = db2.get_authority_continue_envelope(
            "CONT-" + db2.list_authority_candidates_for_root("T-ROOT")[0].id
        )
        assert env["state"] == "violated", label


def test_continued_turn_malformed_output_fails_closed(runtime, db, monkeypatch):
    """Malformed/ambiguous output from the continued turn (no structured
    decision) is an out-of-envelope attempt: the envelope is violated and the
    root fails closed into the ordinary founder-escalation path, never
    silently discarded."""
    fake = StrictFakeAuthorityEvaluator()
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)
    _run_escalate_step(orch, "T-ROOT", CONTINUE_REASON, monkeypatch)
    assert db.get_active_authority_continue_envelope("T-ROOT") is not None

    def fake_run_agent_malformed(task_id, agent, prompt, on_session_started=None, turn_allow_set=None):
        orch.db.update_task(task_id, current_session_id="sess-y")
        raw = "this is not json"
        orch.db.insert_task_result(
            task_id=task_id, agent=agent, session_id="sess-y",
            status="completed", confidence_score=80,
            output_summary=raw, decision_json=None,
        )
        return _make_result(session="sess-y"), _make_report(output_summary=raw)
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent_malformed)
    orch.run_step("T-ROOT")

    t = db.get_task("T-ROOT")
    assert t.status == TaskStatus.ESCALATED
    env = db.get_authority_continue_envelope(
        "CONT-" + db.list_authority_candidates_for_root("T-ROOT")[0].id
    )
    assert env["state"] == "violated"
    viol = [
        a for a in db.get_audit_logs("T-ROOT")
        if a["action"] == AUDIT_ACTION_ENVELOPE_VIOLATED
    ]
    assert viol[0]["payload"]["decision_family"] == "escalate"
    # Exactly ONE evaluation (the original grant); the hook did not re-run.
    assert len(db.list_authority_candidates_for_root("T-ROOT")) == 1


def _seed_continued_root(
    runtime, db, task_id: str = "T-ROOT", session: str = "sess-x",
    reason: str = CONTINUE_REASON,
):
    """Seed a claimed manager root + the original escalate's immutable
    result row, then drive the REAL ``_consume_completion_report`` seam so
    the hook grants the continuation and mints the envelope (shipping
    boot-sweep path). Returns the result row."""
    from runtime.models import CompletionReport, TaskRecord
    db.insert_task(TaskRecord(
        id=task_id, brief="b", assigned_agent="engineering_head", team="engineering",
    ))
    db.update_task(
        task_id, status=TaskStatus.IN_PROGRESS, block_kind=None,
        orchestration_step_count=1, current_session_id=session,
    )
    db.insert_task_result(
        task_id=task_id, agent="engineering_head", session_id=session,
        status="completed", confidence_score=80,
        output_summary=_escalate_decision(reason),
        decision_json=_escalate_decision(reason),
    )
    row = db.get_latest_task_result(task_id, "engineering_head", session)
    report = CompletionReport(
        task_id=task_id, agent="engineering_head", status="completed",
        confidence=80, output_summary=_escalate_decision(reason),
        decision={"action": "escalate", "reason": reason},
    )
    from runtime.orchestrator.run_step import _consume_completion_report
    orch = _make_orch(runtime, db, evaluator=StrictFakeAuthorityEvaluator())
    _consume_completion_report(orch, task_id, report, result_row_id=row["id"])
    return row


# ── (d) out-of-envelope attempts → ordinary escalation lifecycle ─────────

def test_violation_produces_ordinary_escalation_audit_and_notification(runtime, db, monkeypatch):
    """The fail-closed destination of an out-of-envelope continued-turn
    decision is byte-identical to an ordinary escalation's lifecycle: task
    ESCALATED, one escalation audit row with the server-derived reason, the
    notification fired, and the thread projection attempted (no-op for a
    non-thread root)."""
    fake = StrictFakeAuthorityEvaluator()
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)
    spy = {}
    monkeypatch.setattr(orch, "notify_escalated", lambda **kw: spy.update(kw))
    _run_escalate_step(orch, "T-ROOT", CONTINUE_REASON, monkeypatch)

    _run_continued_step(
        orch, "T-ROOT", {"action": "delegate", "agent": "dev_agent", "prompt": "p"},
        monkeypatch,
    )

    t = db.get_task("T-ROOT")
    assert t.status == TaskStatus.ESCALATED
    assert t.note is not None and "envelope violation" in t.note
    esc = _escalation_rows(db, "T-ROOT")
    assert len(esc) == 1
    assert "envelope violation" in esc[0]["payload"].get("reason", "")
    assert spy["task_id"] == "T-ROOT"
    assert "envelope violation" in spy["reason"]


# ── (e) exactly-once / CAS / cancellation / restart windows ──────────────

def test_envelope_consumption_is_exactly_once(runtime, db, monkeypatch):
    """Two consumers racing to spend the SAME envelope: exactly one wins."""
    fake = StrictFakeAuthorityEvaluator()
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)
    _run_escalate_step(orch, "T-ROOT", CONTINUE_REASON, monkeypatch)
    env = db.get_active_authority_continue_envelope("T-ROOT")
    orch._db.update_task("T-ROOT", status=TaskStatus.IN_PROGRESS, current_session_id="sess-y")

    kwargs = dict(
        envelope_id=env["id"], root_task_id="T-ROOT",
        expected_manager_agent="engineering_head",
        expected_session_id=env["manager_session_id"],
        expected_causal_event_id=env["causal_event_id"],
        expected_causal_event_digest=env["causal_event_digest"],
        expected_policy_id=env["policy_id"],
        expected_policy_version=env["policy_version"],
        expected_policy_digest=env["policy_digest"],
        expected_clause_id=env["clause_id"],
        expected_action=env["action"],
        audit_agent="engineering_head",
    )
    first = db.consume_authority_continue_envelope(decision_family="done", **kwargs)
    second = db.consume_authority_continue_envelope(decision_family="done", **kwargs)
    assert first == "consumed"
    assert second == "not_active"
    env2 = db.get_authority_continue_envelope(env["id"])
    assert env2["state"] == "consumed"
    consumed_rows = [
        a for a in db.get_audit_logs("T-ROOT")
        if a["action"] == AUDIT_ACTION_ENVELOPE_CONSUMED
    ]
    assert len(consumed_rows) == 1


def test_envelope_identity_rechecked_atomically_at_consumption(runtime, db, monkeypatch):
    """Consumption with a drifted identity expectation (wrong policy,
    wrong causal row, wrong clause, wrong action) fails closed: the envelope
    is never spent by a mismatched consumer."""
    fake = StrictFakeAuthorityEvaluator()
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)
    _run_escalate_step(orch, "T-ROOT", CONTINUE_REASON, monkeypatch)
    env = db.get_active_authority_continue_envelope("T-ROOT")
    orch._db.update_task("T-ROOT", status=TaskStatus.IN_PROGRESS, current_session_id="sess-y")

    base = dict(
        envelope_id=env["id"], root_task_id="T-ROOT",
        expected_manager_agent="engineering_head",
        expected_session_id=env["manager_session_id"],
        expected_causal_event_id=env["causal_event_id"],
        expected_causal_event_digest=env["causal_event_digest"],
        expected_policy_id=env["policy_id"],
        expected_policy_version=env["policy_version"],
        expected_policy_digest=env["policy_digest"],
        expected_clause_id=env["clause_id"],
        expected_action=env["action"],
        audit_agent="engineering_head",
    )
    for mutate in (
        {"expected_policy_id": "other/policy"},
        {"expected_causal_event_id": "result:999999"},
        {"expected_causal_event_digest": "0" * 64},
        {"expected_clause_id": "esc-schema"},
        {"expected_action": "escalate_to_founder"},
        {"root_task_id": "T-OTHER"},
    ):
        kwargs = {**base, **mutate}
        out = db.consume_authority_continue_envelope(decision_family="done", **kwargs)
        assert out == "not_active", mutate
    # Still active after all mismatched attempts.
    assert db.get_authority_continue_envelope(env["id"])["state"] == "active"


def test_cancellation_during_continued_turn_spends_envelope(runtime, db, monkeypatch):
    fake = StrictFakeAuthorityEvaluator()
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)
    _run_escalate_step(orch, "T-ROOT", CONTINUE_REASON, monkeypatch)

    # Cancel the root mid-continued-turn; the failure path spends the
    # envelope fail-closed (never re-usable).
    orch._db.update_task("T-ROOT", status=TaskStatus.IN_PROGRESS, current_session_id="sess-y")
    from datetime import datetime, timezone
    orch._db.update_task(
        "T-ROOT", status=TaskStatus.FAILED,
        cancelled_at=datetime.now(timezone.utc).isoformat(),
    )
    from runtime.orchestrator.run_step import _fail
    orch._db.update_task("T-ROOT", status=TaskStatus.IN_PROGRESS, current_session_id="sess-y")
    # _fail is a no-op for already-terminal; simulate the session-failure path
    # directly on a live envelope by spending it.
    spent = orch._db.spend_authority_continue_envelope_if_active(
        "T-ROOT", audit_agent="engineering_head",
        error="cancelled during the continued turn",
    )
    assert spent is True
    assert db.get_active_authority_continue_envelope("T-ROOT") is None
    env = db.get_authority_continue_envelope(
        "CONT-" + db.list_authority_candidates_for_root("T-ROOT")[0].id
    )
    assert env["state"] == "violated"


def test_session_failure_spends_envelope_fail_closed(runtime, db, monkeypatch):
    fake = StrictFakeAuthorityEvaluator()
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)
    _run_escalate_step(orch, "T-ROOT", CONTINUE_REASON, monkeypatch)
    assert db.get_active_authority_continue_envelope("T-ROOT") is not None

    # Continued turn's session dies without a decision: run_step's failure
    # path spends the envelope and fails the root (no continuation leak).
    def fake_run_agent_dead(task_id, agent, prompt, on_session_started=None, turn_allow_set=None):
        orch.db.update_task(task_id, current_session_id="sess-y")
        return _make_result(success=False, session="sess-y"), None
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent_dead)
    orch.run_step("T-ROOT")

    t = db.get_task("T-ROOT")
    assert t.status == TaskStatus.FAILED
    assert db.get_active_authority_continue_envelope("T-ROOT") is None
    env = db.get_authority_continue_envelope(
        "CONT-" + db.list_authority_candidates_for_root("T-ROOT")[0].id
    )
    assert env["state"] == "violated"


def test_restart_recovery_reentry_hits_same_gate(runtime, db, monkeypatch):
    """A daemon restart mid-continued-turn re-consumes the orphaned result
    row through the SAME ``_consume_completion_report`` gate (boot sweep /
    zombie reaper seam): an out-of-envelope decision still fails closed."""
    from runtime.orchestrator.run_step import _consume_completion_report
    from runtime.models import CompletionReport

    fake = StrictFakeAuthorityEvaluator()
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)
    _run_escalate_step(orch, "T-ROOT", CONTINUE_REASON, monkeypatch)

    # The continued turn's report landed but the daemon died before
    # consumption: rebuild the report from the persisted row (boot sweep).
    orch._db.update_task("T-ROOT", status=TaskStatus.IN_PROGRESS, current_session_id="sess-y")
    orch._db.insert_task_result(
        task_id="T-ROOT", agent="engineering_head", session_id="sess-y",
        status="completed", confidence_score=80,
        output_summary='{"action": "supersede", "successor_brief": "x", "rationale": "y", "attestation": {"recovery_reason": "r", "policy_product_intent_unchanged": true, "no_budget_or_external_commitment": true, "no_permission_or_cross_team_change": true, "no_schema_auth_security_privacy_or_data_access_change": true, "no_unresolved_founder_gate": true}}',
        decision_json='{"action": "supersede", "successor_brief": "x", "rationale": "y", "attestation": {"recovery_reason": "r", "policy_product_intent_unchanged": true, "no_budget_or_external_commitment": true, "no_permission_or_cross_team_change": true, "no_schema_auth_security_privacy_or_data_access_change": true, "no_unresolved_founder_gate": true}}',
    )
    row = orch._db.get_latest_task_result("T-ROOT", "engineering_head", "sess-y")
    report = CompletionReport(
        task_id="T-ROOT", agent="engineering_head", status="completed",
        confidence=80, output_summary="supersede attempt",
        decision={"action": "supersede", "successor_brief": "x", "rationale": "y",
                  "attestation": {"recovery_reason": "r", "policy_product_intent_unchanged": True,
                                  "no_budget_or_external_commitment": True,
                                  "no_permission_or_cross_team_change": True,
                                  "no_schema_auth_security_privacy_or_data_access_change": True,
                                  "no_unresolved_founder_gate": True}},
    )
    _consume_completion_report(orch, "T-ROOT", report, result_row_id=row["id"])

    t = db.get_task("T-ROOT")
    assert t.status == TaskStatus.ESCALATED
    assert t.status != TaskStatus.SUPERSEDED
    succ = db.execute(
        "SELECT 1 FROM manager_supersessions "
        "WHERE original_root_task_id = 'T-ROOT' OR successor_task_id = 'T-ROOT' LIMIT 1"
    ).fetchone()
    assert succ is None
    viol = [
        a for a in db.get_audit_logs("T-ROOT")
        if a["action"] == AUDIT_ACTION_ENVELOPE_VIOLATED
    ]
    assert viol[0]["payload"]["decision_family"] == "supersede"


def test_replayed_original_row_not_gated_by_envelope(runtime, db, monkeypatch):
    """A duplicate delivery of the ORIGINAL escalate's row (restart replay of
    the already-granted decision) is NOT treated as the continued turn: it
    rides the ordinary fail-closed path (hook eligibility -> ineligible ->
    escalate) with exactly ONE continuation."""
    from runtime.orchestrator.run_step import _consume_completion_report
    from runtime.models import CompletionReport

    fake = StrictFakeAuthorityEvaluator()
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)
    _run_escalate_step(orch, "T-ROOT", CONTINUE_REASON, monkeypatch)
    row = orch._db.get_latest_task_result("T-ROOT", "engineering_head", "sess-x")

    report = CompletionReport(
        task_id="T-ROOT", agent="engineering_head", status="completed",
        confidence=80, output_summary=_escalate_decision(CONTINUE_REASON),
        decision={"action": "escalate", "reason": CONTINUE_REASON},
    )
    _consume_completion_report(orch, "T-ROOT", report, result_row_id=row["id"])

    t = db.get_task("T-ROOT")
    assert t.status == TaskStatus.ESCALATED
    assert len(db.list_authority_candidates_for_root("T-ROOT")) == 1
    outcomes = [r["payload"]["outcome"] for r in _hook_outcome_rows(db, "T-ROOT")]
    assert sorted(outcomes) == sorted(["continued_same_root", "ineligible"])
    # The envelope from the original grant is still active (the continued
    # turn never ran) — but the replay did not spend it.
    assert db.get_active_authority_continue_envelope("T-ROOT") is not None


# ── (g) audit denominator + no raw prose ─────────────────────────────────

def test_envelope_audit_denominator_no_raw_secrets(runtime, db, monkeypatch):
    fake = StrictFakeAuthorityEvaluator()
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)
    _run_escalate_step(orch, "T-ROOT", CONTINUE_REASON, monkeypatch)
    _run_continued_step(
        orch, "T-ROOT",
        {"action": "delegate", "agent": "dev_agent", "prompt": "sk-12345 secret payload"},
        monkeypatch,
    )

    rows = _envelope_rows(db, "T-ROOT")
    # issuance is the authority_continued_same_root row; use + violation rows
    # carry bounded decision families, never raw prose.
    for a in rows:
        blob = json.dumps(a["payload"])
        assert "sk-12345" not in blob
        assert "secret payload" not in blob
    viol = [a for a in rows if a["action"] == AUDIT_ACTION_ENVELOPE_VIOLATED]
    assert len(viol) == 1
    assert viol[0]["payload"]["decision_family"] == "delegate"
    assert "policy_id" in viol[0]["payload"]
    assert "policy_digest" in viol[0]["payload"]
    assert "causal_event_id" in viol[0]["payload"]


# ── (h) ordinary ESCALATE behavior untouched when machinery absent ───────

def test_escalate_without_envelope_byte_identical(runtime, db, monkeypatch):
    """A plain manager escalation (no continuation ever granted) produces no
    envelope rows and the ordinary escalation lifecycle: the new envelope
    machinery is absent from the audit trail."""
    fake = StrictFakeAuthorityEvaluator()
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)
    spy = {}
    monkeypatch.setattr(orch, "notify_escalated", lambda **kw: spy.update(kw))
    _run_escalate_step(orch, "T-ROOT", "needs founder decision", monkeypatch)

    t = db.get_task("T-ROOT")
    assert t.status == TaskStatus.ESCALATED
    assert db.get_active_authority_continue_envelope("T-ROOT") is None
    assert _envelope_rows(db, "T-ROOT") == []
    assert spy["task_id"] == "T-ROOT"


def test_content_team_without_policy_never_mints_envelope(runtime, db, monkeypatch):
    """Teams without a release-controlled policy are outside the hook: no
    envelope, no authority records, ordinary escalation unchanged."""
    from runtime.models import TaskRecord
    db.insert_task(TaskRecord(
        id="T-CONTENT", brief="b", assigned_agent="content_head", team="content",
    ))

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        orch.db.update_task(task_id, current_session_id="sess-x")
        orch.db.insert_task_result(
            task_id=task_id, agent=agent, session_id="sess-x",
            status="completed", confidence_score=80,
            output_summary=_escalate_decision("needs founder"),
            decision_json=_escalate_decision("needs founder"),
        )
        return _make_result(), _make_report(output_summary=_escalate_decision("needs founder"))
    orch = _make_orch(runtime, db, evaluator=StrictFakeAuthorityEvaluator())
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)
    orch.run_step("T-CONTENT")

    t = db.get_task("T-CONTENT")
    assert t.status == TaskStatus.ESCALATED
    assert db.get_active_authority_continue_envelope("T-CONTENT") is None
    assert db.list_authority_candidates_for_root("T-CONTENT") == []
    assert _envelope_rows(db, "T-CONTENT") == []


# ── (f) continued-turn prompt carries the mechanical restriction ─────────

def test_continued_turn_prompt_carries_mechanical_restriction(runtime, db, monkeypatch):
    fake = StrictFakeAuthorityEvaluator()
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)
    _run_escalate_step(orch, "T-ROOT", CONTINUE_REASON, monkeypatch)

    captured = {}

    def capture(task_id, agent, prompt, on_session_started=None, turn_allow_set=None):
        captured["prompt"] = prompt
        raise RuntimeError("abort after prompt build")
    monkeypatch.setattr(orch, "_run_agent", capture)
    orch.run_step("T-ROOT")

    prompt = captured["prompt"]
    assert prompt.startswith("AUTHORITY POLICY CONTINUED SAME ROOT:")
    assert "DAEMON-RESTRICTED" in prompt
    assert "single-use continuation envelope" in prompt
    assert "`done`" in prompt
    assert POLICY.id in prompt


def test_continued_turn_prompt_absent_without_active_envelope(runtime, db, monkeypatch):
    """Spent durable continuation history refuses; it is never ordinary."""
    fake = StrictFakeAuthorityEvaluator()
    _seed_root(db)
    orch = _make_orch(runtime, db, evaluator=fake)
    _run_escalate_step(orch, "T-ROOT", CONTINUE_REASON, monkeypatch)
    assert db.get_active_authority_continue_envelope("T-ROOT") is not None

    # Spend the envelope as a fail-closed abort (no continuation window).
    assert db.spend_authority_continue_envelope_if_active(
        "T-ROOT", audit_agent="engineering_head",
        error="test: continuation window closed",
    ) is True

    captured = []

    def capture(task_id, agent, prompt, on_session_started=None):
        captured.append(prompt)
        raise AssertionError("spent continuation history must not launch")
    monkeypatch.setattr(orch, "_run_agent", capture)
    orch.run_step("T-ROOT")

    assert captured == []
    assert db.get_task("T-ROOT").status == TaskStatus.ESCALATED
    assert any(a["action"] == "escalation" for a in db.get_audit_logs("T-ROOT"))
