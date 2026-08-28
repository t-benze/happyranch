"""THR-175 — reviewer identity is the org-configured ``reviewer_agents`` setting.

These tests pin the authoring-time HARD REJECT and the execution-seam
fail-closed for configured reviewer legs that omit ``expect_verdict``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.infrastructure.database import Database
from runtime.models import BlockKind, ChainLeg, NextStep, TaskRecord, TaskStatus
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.teams import TeamsRegistry
from runtime.runtime import RuntimeDir


@pytest.fixture
def runtime(tmp_path: Path) -> OrgPaths:
    rt = RuntimeDir.init(tmp_path / "rt")
    paths = OrgPaths(root=rt.orgs_dir / "test")
    paths.teams_config_path.parent.mkdir(parents=True, exist_ok=True)
    paths.teams_config_path.write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: engineering_head\n"
        "    workers: [product_manager, dev_agent, code_reviewer, qa_engineer, senior_dev]\n"
    )
    return paths


@pytest.fixture
def db(runtime: OrgPaths) -> Database:
    return Database(runtime.db_path)


def _make_orch(db: Database, runtime: OrgPaths):
    from runtime.orchestrator.orchestrator import Orchestrator
    orch = Orchestrator(
        db=db, settings=Settings(), paths=runtime, slug="test",
        teams=TeamsRegistry.load(runtime.root),
    )
    for a in ("dev_agent", "code_reviewer", "qa_engineer", "senior_dev"):
        (runtime.workspaces_dir / a).mkdir(parents=True, exist_ok=True)
    return orch


def _set_reviewer_agents(db: Database, names: list[str]) -> None:
    db.upsert_org_setting("reviewer_agents", json.dumps(names))


def test_validate_delegate_rejects_reviewer_then_leg_omitted_expectation(runtime, db):
    """A reviewer ``then`` leg with omitted expect_verdict is a HARD REJECT."""
    from runtime.orchestrator.run_step import _validate_delegate

    orch = _make_orch(db, runtime)
    decision = NextStep(
        action="delegate",
        agent="dev_agent",
        prompt="build it",
        then=[ChainLeg(agent="code_reviewer", prompt="review", expect_verdict=None)],
    )
    err = _validate_delegate(orch, decision)
    assert err is not None
    assert "HARD REJECT" in err
    assert "expect_verdict" in err
    assert "code_reviewer" in err


def test_validate_delegate_rejects_reviewer_first_leg_omitted_expectation(runtime, db):
    """A reviewer FIRST leg (decision.agent is a reviewer) with omitted
    expect_verdict is a HARD REJECT."""
    from runtime.orchestrator.run_step import _validate_delegate

    orch = _make_orch(db, runtime)
    decision = NextStep(
        action="delegate",
        agent="code_reviewer",
        prompt="review it",
        then=[ChainLeg(agent="qa_engineer", prompt="qa", expect_verdict="PASS")],
    )
    err = _validate_delegate(orch, decision)
    assert err is not None
    assert "HARD REJECT" in err


def test_validate_delegate_accepts_reviewer_leg_with_expectation(runtime, db):
    """A reviewer leg WITH expect_verdict=APPROVE passes validation."""
    from runtime.orchestrator.run_step import _validate_delegate

    orch = _make_orch(db, runtime)
    decision = NextStep(
        action="delegate",
        agent="dev_agent",
        prompt="build it",
        then=[ChainLeg(agent="code_reviewer", prompt="review", expect_verdict="APPROVE")],
    )
    assert _validate_delegate(orch, decision) is None


def test_validate_delegate_accepts_verdictless_non_reviewer(runtime, db):
    """A NON-reviewer leg with omitted expect_verdict is accepted (ordinary
    verdict-less chain semantics preserved)."""
    from runtime.orchestrator.run_step import _validate_delegate

    orch = _make_orch(db, runtime)
    decision = NextStep(
        action="delegate",
        agent="dev_agent",
        prompt="build it",
        then=[
            ChainLeg(agent="senior_dev", prompt="pair", expect_verdict=None),
            ChainLeg(agent="qa_engineer", prompt="qa", expect_verdict="PASS"),
        ],
    )
    assert _validate_delegate(orch, decision) is None


def test_validate_delegate_accepts_bare_single_leg_reviewer_delegate(runtime, db):
    """A bare single-leg reviewer delegate (no ``then``, no ``expect_verdict``)
    is NOT a chain and is NOT rejected — there is no downstream to gate."""
    from runtime.orchestrator.run_step import _validate_delegate

    orch = _make_orch(db, runtime)
    decision = NextStep(
        action="delegate",
        agent="code_reviewer",
        prompt="review this PR",
    )
    assert _validate_delegate(orch, decision) is None


class _SlugQueue:
    """Test adapter: wraps asyncio.Queue so put_nowait(slug, task_id) works."""

    def __init__(self) -> None:
        import asyncio as _asyncio
        self._q: _asyncio.Queue = _asyncio.Queue()

    def put_nowait(self, slug: str, task_id: str) -> None:
        self._q.put_nowait((slug, task_id))

    def qsize(self) -> int:
        return self._q.qsize()

    def get_nowait(self):
        return self._q.get_nowait()

    def drain(self) -> list:
        out = []
        while self._q.qsize():
            out.append(self._q.get_nowait())
        return out


def _make_result():
    from unittest.mock import MagicMock
    r = MagicMock()
    r.success = True
    r.error = None
    r.returncode = 0
    r.rate_limited = False
    r.token_usage = None
    r.session_id = "sess-1"
    return r


def _run_delegate_decision(
    orch, decision_payload: dict,
) -> None:
    """Drive the real run_step seam once with a manager delegate decision."""
    from unittest.mock import MagicMock
    from runtime.models import CompletionReport

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        report = CompletionReport(
            task_id=task_id, agent=agent, status="completed", confidence=80,
            output_summary=json.dumps(decision_payload),
        )
        return _make_result(), report

    orch._run_agent = fake_run_agent
    orch.run_step("T-DELEG-REJ")


def test_run_step_delegate_reviewer_then_leg_omitted_expectation_feedback_no_spawn(
    runtime, db, monkeypatch,
):
    """Regression for the confirmed handling-class defect (TASK-5921/5922): a
    manager delegate chain whose configured code_reviewer ``then`` leg omits
    expect_verdict must NOT terminalize the root task. The whole decision is
    denied before any child spawn: root stays PENDING (not FAILED), no child
    exists, no active_chain/fanout/park mutation, a feedback task result and a
    feedback orchestration audit step are recorded naming
    expect_verdict="APPROVE", the accumulated orchestration step count and
    prior audit history are preserved except for the new feedback step,
    exactly one self re-enqueue occurs, and the next manager step can run and
    proceed with a corrected decision."""
    from runtime.orchestrator.orchestrator import Orchestrator

    orch = _make_orch(db, runtime)
    orch._queue = _SlugQueue()
    db.insert_task(TaskRecord(
        id="T-DELEG-REJ", brief="root brief",
        assigned_agent="engineering_head", task_type="task",
        orchestration_step_count=3,
    ))
    # Prior orchestration history — must survive untouched except for the new
    # feedback step.
    orch._audit.log_orchestration_step(
        "T-DELEG-REJ", 1,
        {"action": "delegate", "agent": "dev_agent", "prompt": "p1"},
    )
    orch._audit.log_orchestration_step(
        "T-DELEG-REJ", 2,
        {"action": "delegate", "agent": "dev_agent", "prompt": "p2"},
    )

    # The manager authored the chain but FORGOT expect_verdict on the
    # code_reviewer ``then`` leg (the field is simply absent).
    _run_delegate_decision(orch, {
        "action": "delegate",
        "agent": "dev_agent",
        "prompt": "build it",
        "then": [{"agent": "code_reviewer", "prompt": "review the PR"}],
    })

    root = db.get_task("T-DELEG-REJ")
    # Root is PENDING (not FAILED) — the decision was denied, not the task.
    assert root.status == TaskStatus.PENDING
    assert root.block_kind is None
    assert root.completed_at is None
    assert root.brief == "root brief"
    # No child exists and no active chain/park mutation occurred.
    assert db.get_children("T-DELEG-REJ") == []
    assert root.active_chain is None
    assert root.active_fanout is None
    # The claimed step (3 -> 4) is consumed; nothing further is bumped.
    assert root.orchestration_step_count == 4

    # Feedback task result recorded with remediation naming expect_verdict.
    results = db.get_task_results("T-DELEG-REJ")
    assert len(results) == 1
    feedback = results[0]["output_summary"]
    assert "HARD REJECT" in feedback
    assert 'expect_verdict: "APPROVE"' in feedback
    assert "code_reviewer" in feedback

    # Feedback orchestration audit step keyed to the claimed step number;
    # prior orchestration history preserved exactly. The manager's own
    # decision audit row (action=delegate) is logged first, then the feedback
    # row — the same shape as the out-of-scope / retry-link feedback paths.
    logs = db.get_audit_logs("T-DELEG-REJ")
    step_rows = [l for l in logs if l["action"] == "orchestration_step"]
    assert len(step_rows) == 4  # 2 prior + 1 decision + 1 feedback
    assert [l["payload"]["step_number"] for l in step_rows] == [1, 2, 4, 4]
    decision_row = step_rows[-2]
    assert decision_row["payload"]["decision"]["action"] == "delegate"
    feedback_row = step_rows[-1]
    assert feedback_row["payload"]["decision"]["action"] == "feedback"
    assert "HARD REJECT" in feedback_row["payload"]["decision"]["reason"]
    assert 'expect_verdict: "APPROVE"' in feedback_row["payload"]["decision"]["reason"]

    # Exactly one self re-enqueue.
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == "T-DELEG-REJ"
    assert orch._queue.qsize() == 0

    # The next manager step can run and proceed with a corrected decision.
    _run_delegate_decision(orch, {
        "action": "delegate",
        "agent": "dev_agent",
        "prompt": "build it",
        "then": [{
            "agent": "code_reviewer", "prompt": "review the PR",
            "expect_verdict": "APPROVE",
        }],
    })
    children = db.get_children("T-DELEG-REJ")
    assert len(children) == 1
    child = db.get_task(children[0])
    assert child.assigned_agent == "dev_agent"
    parent = db.get_task("T-DELEG-REJ")
    assert parent.status == TaskStatus.IN_PROGRESS
    assert parent.block_kind == BlockKind.DELEGATED
    assert parent.active_chain is not None


def test_run_step_delegate_reviewer_first_leg_omitted_expectation_feedback_no_spawn(
    runtime, db, monkeypatch,
):
    """A configured reviewer FIRST leg (decision.agent is a reviewer) gating a
    downstream ``then`` leg and omitting expect_verdict is HARD REJECTED the
    same way: feedback + no spawn + PENDING + re-enqueue, never root failure."""
    from runtime.orchestrator.orchestrator import Orchestrator

    orch = _make_orch(db, runtime)
    orch._queue = _SlugQueue()
    db.insert_task(TaskRecord(
        id="T-DELEG-REJ", brief="root brief",
        assigned_agent="engineering_head", task_type="task",
    ))

    _run_delegate_decision(orch, {
        "action": "delegate",
        "agent": "code_reviewer",
        "prompt": "review it",
        "then": [{"agent": "qa_engineer", "prompt": "qa", "expect_verdict": "PASS"}],
    })

    root = db.get_task("T-DELEG-REJ")
    assert root.status == TaskStatus.PENDING
    assert db.get_children("T-DELEG-REJ") == []
    results = db.get_task_results("T-DELEG-REJ")
    assert len(results) == 1
    assert "HARD REJECT" in results[0]["output_summary"]
    assert 'expect_verdict: "APPROVE"' in results[0]["output_summary"]
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == "T-DELEG-REJ"


def test_run_step_delegate_missing_workspace_still_fails_task(runtime, db, monkeypatch):
    """A structurally unrecoverable invalid delegate (missing workspace) keeps
    hard terminal failure — it is never converted to feedback/re-enqueue."""
    from runtime.orchestrator.orchestrator import Orchestrator

    orch = _make_orch(db, runtime)
    orch._queue = _SlugQueue()
    db.insert_task(TaskRecord(
        id="T-DELEG-REJ", brief="root brief",
        assigned_agent="engineering_head", task_type="task",
    ))

    # ``ghost_agent`` has no workspace in this org — unrecoverable.
    _run_delegate_decision(orch, {
        "action": "delegate",
        "agent": "ghost_agent",
        "prompt": "build it",
    })

    root = db.get_task("T-DELEG-REJ")
    assert root.status == TaskStatus.FAILED
    assert root.note and "invalid delegate" in root.note
    assert "no workspace" in root.note
    assert db.get_children("T-DELEG-REJ") == []
    # No feedback result, no self re-enqueue — the task is terminal.
    assert db.get_task_results("T-DELEG-REJ") == []
    assert orch._queue.qsize() == 0


def test_fanout_carrier_reviewer_omitted_expectation_feedback_no_spawn(runtime, db, monkeypatch):
    """A fan-out pipeline carrier whose ``then`` leg is a configured reviewer
    with omitted expect_verdict is HARD REJECTED before any child spawns — as
    feedback + PENDING + one self re-enqueue (never a root failure), matching
    the inline delegate-chain handling-class correction (TASK-5922)."""
    from runtime.orchestrator.orchestrator import Orchestrator

    orch = _make_orch(db, runtime)
    orch._queue = _SlugQueue()
    from unittest.mock import MagicMock

    db.insert_task(TaskRecord(
        id="T-FANOUT-REJ", brief="fanout reject",
        assigned_agent="engineering_head", task_type="task",
    ))

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        r = MagicMock()
        r.success = True
        r.error = None
        r.returncode = 0
        r.rate_limited = False
        r.token_usage = None
        r.session_id = "sess-1"
        from runtime.models import CompletionReport
        report = CompletionReport(
            task_id=task_id, agent=agent, status="completed", confidence=80,
            output_summary=json.dumps({
                "action": "fanout",
                "children": [
                    {
                        "agent": "dev_agent", "prompt": "child 1",
                        "then": [{"agent": "code_reviewer", "prompt": "review"}],
                    },
                    {"agent": "qa_engineer", "prompt": "child 2"},
                ],
                "width_cap_ack": 2,
            }),
        )
        return r, report

    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)
    orch.run_step("T-FANOUT-REJ")

    parent = db.get_task("T-FANOUT-REJ")
    assert parent.status == TaskStatus.PENDING
    assert parent.block_kind is None
    assert parent.active_fanout is None
    assert db.get_children("T-FANOUT-REJ") == []
    results = db.get_task_results("T-FANOUT-REJ")
    assert len(results) == 1
    assert "HARD REJECT" in results[0]["output_summary"]
    assert 'expect_verdict: "APPROVE"' in results[0]["output_summary"]
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == "T-FANOUT-REJ"
