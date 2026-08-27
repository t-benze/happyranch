"""THR-211 completion-status-lag correction — dispatch/gate recognition tests.

The completion POST durably inserts a session-scoped ``task_results`` row but
the ``tasks.status`` row intentionally stays ``in_progress`` (block_kind NULL)
until the orchestrator consumes the report at executor/session finalization
(run_step_impl tail, daemon boot sweep, or zombie reaper). THR-211 makes the
parent-dispatch/gate seams recognize a durably-landed authoritative structured
terminal result during that lag window — at-most-once — without prematurely
terminalizing the task row.

The authority is deliberately narrow and session-safe: the exact
``(task_id, assigned_agent, current_session_id)`` triple (the same fingerprint
the boot sweep and zombie reaper use) with report status == ``completed``.
Stale prior-session rows, wrong-agent rows, blocked reports (the
blocked_on_job path parks the task in_progress by design), unknown statuses,
and genuinely-running tasks with no terminal result all fail closed.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.database import Database
from runtime.models import BlockKind, ChainLeg, TaskRecord, TaskStatus
from runtime.orchestrator.chain import ChainState
from runtime.orchestrator.run_step import (
    _child_has_landed_terminal_result,
    _enqueue_parent_if_waiting,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class _FakeQueue:
    """Collects enqueued task ids (put_nowait only — the shape run_step uses)."""

    def __init__(self) -> None:
        self.items: list[str] = []

    def put_nowait(self, slug: str, task_id: str) -> None:
        self.items.append(task_id)


def _make_orch(db: Database, queue: _FakeQueue | None = None) -> MagicMock:
    """Minimal Orchestrator test-double satisfying the dispatch seams."""
    orch = MagicMock()
    orch._db = db
    orch._audit = AuditLogger(db)
    orch._slug = "test-org"
    orch._queue = queue
    return orch


def _seed_chain_parent(
    db: Database,
    *,
    parent_id: str = "TASK-P",
    legs: list[tuple[str, str, str]] | None = None,
    step_index: int = 1,
) -> None:
    """Seed an in_progress(delegated) chain parent with an active chain.

    ``legs`` is a list of (agent, prompt, expect_verdict); the implicit first
    leg is assumed already completed (step_index >= 1).
    """
    db.insert_task(TaskRecord(id=parent_id, team="engineering", brief="parent"))
    db.update_task(parent_id, status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED)
    chain = ChainState(
        step_index=step_index,
        first_leg_expect_verdict=None,
        legs=[ChainLeg(agent=a, prompt=p, expect_verdict=v) for (a, p, v) in (legs or [])],
        step_audit_id=1,
    )
    db.update_task_active_chain(parent_id, chain.serialize())


def _seed_child_with_landed_result(
    db: Database,
    *,
    child_id: str,
    parent_id: str,
    agent: str,
    session_id: str,
    verdict: str | None = None,
    report_status: str = "completed",
) -> None:
    """Seed a child whose result has durably landed but whose row is still
    in_progress (the THR-211 lag window)."""
    db.insert_task(TaskRecord(
        id=child_id, team="engineering", brief=child_id,
        parent_task_id=parent_id, assigned_agent=agent,
    ))
    db.update_task(
        child_id,
        status=TaskStatus.IN_PROGRESS,
        current_session_id=session_id,
    )
    db.insert_task_result(
        task_id=child_id, agent=agent, session_id=session_id,
        status=report_status, confidence_score=90,
        output_summary=f"{child_id} done", verdict=verdict,
    )


# ---------------------------------------------------------------------------
# recognition authority (session-safe, fail-closed)
# ---------------------------------------------------------------------------

def test_helper_true_for_current_session_completed_result(tmp_path) -> None:
    db = Database(tmp_path / "x.db")
    _seed_child_with_landed_result(
        db, child_id="TASK-C", parent_id=None, agent="dev_agent",
        session_id="sess-current", verdict="APPROVE",
    )
    # parent_id=None leaves no parent — the child is standalone here.
    assert _child_has_landed_terminal_result(_make_orch(db), db.get_task("TASK-C")) is True


def test_helper_fails_closed_on_stale_prior_session(tmp_path) -> None:
    db = Database(tmp_path / "x.db")
    _seed_child_with_landed_result(
        db, child_id="TASK-C", parent_id=None, agent="dev_agent",
        session_id="sess-old",
    )
    db.update_task("TASK-C", current_session_id="sess-current")
    assert _child_has_landed_terminal_result(_make_orch(db), db.get_task("TASK-C")) is False


def test_helper_fails_closed_on_wrong_agent(tmp_path) -> None:
    db = Database(tmp_path / "x.db")
    _seed_child_with_landed_result(
        db, child_id="TASK-C", parent_id=None, agent="other_agent",
        session_id="sess-1",
    )
    db.update_task("TASK-C", assigned_agent="dev_agent", current_session_id="sess-1")
    assert _child_has_landed_terminal_result(_make_orch(db), db.get_task("TASK-C")) is False


def test_helper_fails_closed_on_blocked_report(tmp_path) -> None:
    db = Database(tmp_path / "x.db")
    _seed_child_with_landed_result(
        db, child_id="TASK-C", parent_id=None, agent="dev_agent",
        session_id="sess-1", report_status="blocked",
    )
    assert _child_has_landed_terminal_result(_make_orch(db), db.get_task("TASK-C")) is False


def test_helper_fails_closed_on_unknown_report_status(tmp_path) -> None:
    db = Database(tmp_path / "x.db")
    _seed_child_with_landed_result(
        db, child_id="TASK-C", parent_id=None, agent="dev_agent",
        session_id="sess-1", report_status="mystery",
    )
    assert _child_has_landed_terminal_result(_make_orch(db), db.get_task("TASK-C")) is False


def test_helper_fails_closed_without_session_or_no_result(tmp_path) -> None:
    db = Database(tmp_path / "x.db")
    db.insert_task(TaskRecord(id="TASK-C", team="engineering", brief="c"))
    db.update_task("TASK-C", status=TaskStatus.IN_PROGRESS)
    # No current_session_id and no result row → not terminal.
    assert _child_has_landed_terminal_result(_make_orch(db), db.get_task("TASK-C")) is False
    # With a session id but no result row → still not terminal.
    db.update_task("TASK-C", current_session_id="sess-1")
    assert _child_has_landed_terminal_result(_make_orch(db), db.get_task("TASK-C")) is False


# ---------------------------------------------------------------------------
# chain advance recognition (proof 1 + 2)
# ---------------------------------------------------------------------------

def test_landed_reviewer_verdict_advances_chain_while_row_in_progress(tmp_path) -> None:
    """A durably-landed APPROVE on the current chain leg advances to the QA
    leg even though the reviewer's row still reads in_progress."""
    db = Database(tmp_path / "x.db")
    _seed_chain_parent(
        db,
        legs=[("code_reviewer", "review brief", "APPROVE"),
              ("qa_engineer", "qa brief", "PASS")],
        step_index=1,
    )
    _seed_child_with_landed_result(
        db, child_id="TASK-R", parent_id="TASK-P", agent="code_reviewer",
        session_id="sess-r", verdict="APPROVE",
    )
    q = _FakeQueue()
    _enqueue_parent_if_waiting(_make_orch(db, q), "TASK-R")

    children = db.get_children("TASK-P")
    assert len(children) == 2, children
    next_child = db.get_task(children[1])
    assert next_child.assigned_agent == "qa_engineer"
    assert "qa brief" in next_child.brief
    chain = ChainState.deserialize(db.get_task("TASK-P").active_chain)
    assert chain.step_index == 2
    # Advance enqueues ONLY the next leg child (never the parent).
    assert q.items == [children[1]]


def test_landed_result_advance_is_at_most_once_across_delayed_consumption(tmp_path) -> None:
    """Recognition-advance followed by the delayed session-finalization
    consumption (child row now COMPLETED) must NOT spawn a duplicate leg or
    prematurely clear the chain."""
    db = Database(tmp_path / "x.db")
    _seed_chain_parent(
        db,
        legs=[("code_reviewer", "review brief", "APPROVE"),
              ("qa_engineer", "qa brief", "PASS")],
        step_index=1,
    )
    _seed_child_with_landed_result(
        db, child_id="TASK-R", parent_id="TASK-P", agent="code_reviewer",
        session_id="sess-r", verdict="APPROVE",
    )
    q = _FakeQueue()
    orch = _make_orch(db, q)

    # First consumer call during the lag window → recognition advance.
    _enqueue_parent_if_waiting(orch, "TASK-R")
    children_after_first = db.get_children("TASK-P")
    assert len(children_after_first) == 2

    # Delayed consumption: the row finally transitions to COMPLETED and the
    # same child triggers the wake path a second time.
    db.update_task("TASK-R", status=TaskStatus.COMPLETED)
    _enqueue_parent_if_waiting(orch, "TASK-R")

    children = db.get_children("TASK-P")
    assert len(children) == 2, f"duplicate leg spawned: {children}"
    # The chain must still point at the QA leg (not cleared mid-flight).
    chain = ChainState.deserialize(db.get_task("TASK-P").active_chain)
    assert chain.step_index == 2
    assert db.get_task("TASK-P").active_chain is not None
    # Only the QA leg was enqueued (once); the parent is never woken while
    # the QA leg is still pending.
    assert q.items == [children[1]]


def test_repeated_consumer_calls_advance_at_most_once(tmp_path) -> None:
    """N repeated consumer calls during the lag window produce exactly one
    chain advance (and no parent wake while the next leg is pending)."""
    db = Database(tmp_path / "x.db")
    _seed_chain_parent(
        db,
        legs=[("payment_agent", "review brief", "APPROVE"),
              ("qa_engineer", "qa brief", "PASS")],
        step_index=1,
    )
    _seed_child_with_landed_result(
        db, child_id="TASK-R", parent_id="TASK-P", agent="payment_agent",
        session_id="sess-r", verdict="APPROVE",
    )
    q = _FakeQueue()
    orch = _make_orch(db, q)
    for _ in range(5):
        _enqueue_parent_if_waiting(orch, "TASK-R")

    children = db.get_children("TASK-P")
    assert len(children) == 2, f"expected exactly one advance, got {children}"
    # Exactly one advance → exactly one next-leg enqueue.
    assert q.items == [children[1]]


def test_landed_matching_verdict_on_ungated_first_leg_advances(tmp_path) -> None:
    """A first-leg child with no expect_verdict but a landed report advances
    the chain (recognition mirrors the post-transition COMPLETED state)."""
    db = Database(tmp_path / "x.db")
    _seed_chain_parent(
        db,
        legs=[("code_reviewer", "review brief", "APPROVE")],
        step_index=0,
    )
    _seed_child_with_landed_result(
        db, child_id="TASK-D", parent_id="TASK-P", agent="dev_agent",
        session_id="sess-d", verdict=None,
    )
    q = _FakeQueue()
    _enqueue_parent_if_waiting(_make_orch(db, q), "TASK-D")
    children = db.get_children("TASK-P")
    assert len(children) == 2
    assert db.get_task(children[1]).assigned_agent == "code_reviewer"
    assert q.items == [children[1]]


# ---------------------------------------------------------------------------
# genuinely-running tasks are never suppressed (proof 3)
# ---------------------------------------------------------------------------

def test_running_child_without_result_keeps_parent_in_progress(tmp_path) -> None:
    """A genuinely-running child (in_progress, no terminal result) must NOT
    advance the chain or wake the parent."""
    db = Database(tmp_path / "x.db")
    _seed_chain_parent(
        db,
        legs=[("code_reviewer", "review brief", "APPROVE")],
        step_index=1,
    )
    db.insert_task(TaskRecord(
        id="TASK-R", team="engineering", brief="review",
        parent_task_id="TASK-P", assigned_agent="code_reviewer",
    ))
    db.update_task("TASK-R", status=TaskStatus.IN_PROGRESS, current_session_id="sess-r")
    q = _FakeQueue()
    _enqueue_parent_if_waiting(_make_orch(db, q), "TASK-R")
    assert db.get_children("TASK-P") == ["TASK-R"]
    assert q.items == []


def test_running_child_without_result_blocks_sibling_parent_wake(tmp_path) -> None:
    """A parent with one completed sibling and one genuinely-running child
    (no result) must NOT wake."""
    db = Database(tmp_path / "x.db")
    db.insert_task(TaskRecord(id="TASK-P", team="engineering", brief="p"))
    db.update_task("TASK-P", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED)
    db.insert_task(TaskRecord(
        id="TASK-A", team="engineering", brief="a",
        parent_task_id="TASK-P", assigned_agent="dev_agent",
    ))
    db.update_task("TASK-A", status=TaskStatus.COMPLETED)
    db.insert_task(TaskRecord(
        id="TASK-B", team="engineering", brief="b",
        parent_task_id="TASK-P", assigned_agent="dev_agent",
    ))
    db.update_task("TASK-B", status=TaskStatus.IN_PROGRESS, current_session_id="sess-b")
    q = _FakeQueue()
    _enqueue_parent_if_waiting(_make_orch(db, q), "TASK-A")
    assert q.items == []


# ---------------------------------------------------------------------------
# fail-closed recognition at the sibling gate (proof 4)
# ---------------------------------------------------------------------------

def test_stale_session_result_does_not_wake_parent(tmp_path) -> None:
    """A result row from a PRIOR session must not make a child count as
    terminal for the parent wake."""
    db = Database(tmp_path / "x.db")
    db.insert_task(TaskRecord(id="TASK-P", team="engineering", brief="p"))
    db.update_task("TASK-P", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED)
    db.insert_task(TaskRecord(
        id="TASK-A", team="engineering", brief="a",
        parent_task_id="TASK-P", assigned_agent="dev_agent",
    ))
    db.update_task("TASK-A", status=TaskStatus.COMPLETED)
    _seed_child_with_landed_result(
        db, child_id="TASK-B", parent_id="TASK-P", agent="dev_agent",
        session_id="sess-old",
    )
    db.update_task("TASK-B", current_session_id="sess-current")
    q = _FakeQueue()
    _enqueue_parent_if_waiting(_make_orch(db, q), "TASK-A")
    assert q.items == []


def test_blocked_report_child_does_not_wake_parent(tmp_path) -> None:
    """A self-blocked child (blocked report) is NOT dispatch-terminal — the
    blocked_on_job park is a live state owned by the resume flow."""
    db = Database(tmp_path / "x.db")
    db.insert_task(TaskRecord(id="TASK-P", team="engineering", brief="p"))
    db.update_task("TASK-P", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED)
    db.insert_task(TaskRecord(
        id="TASK-A", team="engineering", brief="a",
        parent_task_id="TASK-P", assigned_agent="dev_agent",
    ))
    db.update_task("TASK-A", status=TaskStatus.COMPLETED)
    _seed_child_with_landed_result(
        db, child_id="TASK-B", parent_id="TASK-P", agent="dev_agent",
        session_id="sess-b", report_status="blocked",
    )
    q = _FakeQueue()
    _enqueue_parent_if_waiting(_make_orch(db, q), "TASK-A")
    assert q.items == []


def test_non_passing_verdict_is_terminal_for_wake_but_never_advances(tmp_path) -> None:
    """A landed REQUEST_CHANGES verdict is dispatch-terminal (the parent must
    wake to handle the non-passing outcome) but must NOT spawn the QA leg."""
    db = Database(tmp_path / "x.db")
    _seed_chain_parent(
        db,
        legs=[("code_reviewer", "review brief", "APPROVE"),
              ("qa_engineer", "qa brief", "PASS")],
        step_index=1,
    )
    _seed_child_with_landed_result(
        db, child_id="TASK-R", parent_id="TASK-P", agent="code_reviewer",
        session_id="sess-r", verdict="REQUEST_CHANGES",
    )
    q = _FakeQueue()
    _enqueue_parent_if_waiting(_make_orch(db, q), "TASK-R")
    # No QA leg spawned; chain cleared; parent woken for the manager decision.
    assert db.get_children("TASK-P") == ["TASK-R"]
    assert db.get_task("TASK-P").active_chain is None
    assert q.items == ["TASK-P"]


def test_unknown_verdict_fails_closed_on_advance_but_still_wakes(tmp_path) -> None:
    """A landed completed report with an unknown/malformed verdict never
    advances (the verdict gate fails closed) and the parent wakes to decide."""
    db = Database(tmp_path / "x.db")
    _seed_chain_parent(
        db,
        legs=[("code_reviewer", "review brief", "APPROVE"),
              ("qa_engineer", "qa brief", "PASS")],
        step_index=1,
    )
    _seed_child_with_landed_result(
        db, child_id="TASK-R", parent_id="TASK-P", agent="code_reviewer",
        session_id="sess-r", verdict="maybe-ok",
    )
    q = _FakeQueue()
    _enqueue_parent_if_waiting(_make_orch(db, q), "TASK-R")
    assert db.get_children("TASK-P") == ["TASK-R"]
    assert db.get_task("TASK-P").active_chain is None
    assert q.items == ["TASK-P"]


# ---------------------------------------------------------------------------
# fan-out / parallel parent wake (proof 1 fanout + 2)
# ---------------------------------------------------------------------------

def test_fanout_parent_wakes_when_last_child_result_landed_but_row_in_progress(tmp_path) -> None:
    """A fan-out parent whose last child's result has durably landed (row still
    in_progress) is woken once; the delayed consumption does not double-wake."""
    db = Database(tmp_path / "x.db")
    db.insert_task(TaskRecord(id="TASK-P", team="engineering", brief="p"))
    db.update_task("TASK-P", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED)
    db.insert_task(TaskRecord(
        id="TASK-A", team="engineering", brief="a",
        parent_task_id="TASK-P", assigned_agent="dev_agent",
    ))
    db.update_task("TASK-A", status=TaskStatus.COMPLETED)
    _seed_child_with_landed_result(
        db, child_id="TASK-B", parent_id="TASK-P", agent="qa_engineer",
        session_id="sess-b", verdict="PASS",
    )
    q = _FakeQueue()

    # Sibling completion while B's row is still in_progress → parent wakes once.
    _enqueue_parent_if_waiting(_make_orch(db, q), "TASK-A")
    assert q.items == ["TASK-P"]

    # Delayed consumption of B → all siblings now terminal → a second enqueue,
    # which the run_step claim CAS absorbs (asserting single-application here
    # means the enqueue is the queue's put; idempotency is the CAS's job).
    db.update_task("TASK-B", status=TaskStatus.COMPLETED)
    _enqueue_parent_if_waiting(_make_orch(db, q), "TASK-B")
    assert q.items == ["TASK-P", "TASK-P"]


def test_fanout_parent_not_woken_while_a_child_genuinely_running(tmp_path) -> None:
    db = Database(tmp_path / "x.db")
    db.insert_task(TaskRecord(id="TASK-P", team="engineering", brief="p"))
    db.update_task("TASK-P", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED)
    db.insert_task(TaskRecord(
        id="TASK-A", team="engineering", brief="a",
        parent_task_id="TASK-P", assigned_agent="dev_agent",
    ))
    db.update_task("TASK-A", status=TaskStatus.COMPLETED)
    db.insert_task(TaskRecord(
        id="TASK-B", team="engineering", brief="b",
        parent_task_id="TASK-P", assigned_agent="qa_engineer",
    ))
    db.update_task("TASK-B", status=TaskStatus.IN_PROGRESS, current_session_id="sess-b")
    q = _FakeQueue()
    _enqueue_parent_if_waiting(_make_orch(db, q), "TASK-A")
    assert q.items == []


# ---------------------------------------------------------------------------
# mutation-exception rollback (proof 5)
# ---------------------------------------------------------------------------

def test_failed_chain_advance_rolls_back_without_partial_state(tmp_path, monkeypatch) -> None:
    """If the atomic chain-advance write fails, no child/link/audit/chain
    state is left behind and the parent falls through to the safe wake path."""
    db = Database(tmp_path / "x.db")
    _seed_chain_parent(
        db,
        legs=[("code_reviewer", "review brief", "APPROVE"),
              ("qa_engineer", "qa brief", "PASS")],
        step_index=1,
    )
    _seed_child_with_landed_result(
        db, child_id="TASK-R", parent_id="TASK-P", agent="code_reviewer",
        session_id="sess-r", verdict="APPROVE",
    )
    from runtime.orchestrator.run_step import _advance_chain_for_completed_child

    def _boom(*args, **kwargs):
        return False  # simulate the transactional write failing

    monkeypatch.setattr(
        "runtime.infrastructure.database.Database.try_advance_chain", _boom,
    )
    q = _FakeQueue()
    _enqueue_parent_if_waiting(_make_orch(db, q), "TASK-R")
    # No partial child or chain mutation: the transaction rolled back, the
    # child list is unchanged, and the chain stays at its prior step. The
    # bounded fallback wakes the parent ONCE so the manager can decide next
    # step (mirrors the pre-THR-211 failed-advance wake semantics).
    children = db.get_children("TASK-P")
    assert children == ["TASK-R"]
    chain = ChainState.deserialize(db.get_task("TASK-P").active_chain)
    assert chain.step_index == 1
    assert q.items == ["TASK-P"]


# ---------------------------------------------------------------------------
# mixed-row authenticity — a newer unrelated row can never gate the chain
# (TASK-5815 fix-forward of the TASK-5814 HIGH finding)
# ---------------------------------------------------------------------------

def test_mixed_rows_newer_wrong_agent_failing_cannot_override_auth_approve(tmp_path) -> None:
    """Direction A (reviewer reproduction): the exact-current authenticated
    APPROVE is the chain gate's evidence even when a NEWER wrong-agent /
    wrong-session REQUEST_CHANGES row exists. The chain must advance from the
    authenticated passing report — never clear or advance from the unrelated
    newer row (which is what the unscoped newest-row read would have done)."""
    db = Database(tmp_path / "x.db")
    _seed_chain_parent(
        db,
        legs=[("code_reviewer", "review brief", "APPROVE"),
              ("qa_engineer", "qa brief", "PASS")],
        step_index=1,
    )
    _seed_child_with_landed_result(
        db, child_id="TASK-R", parent_id="TASK-P", agent="code_reviewer",
        session_id="sess-r", verdict="APPROVE",
    )
    # Newer unrelated row: different agent AND different session, failing.
    db.insert_task_result(
        task_id="TASK-R", agent="intruder_agent", session_id="sess-intruder",
        status="completed", confidence_score=10,
        output_summary="unrelated newer row", verdict="REQUEST_CHANGES",
    )
    q = _FakeQueue()
    _enqueue_parent_if_waiting(_make_orch(db, q), "TASK-R")

    children = db.get_children("TASK-P")
    assert len(children) == 2, f"chain wrongly cleared: {children}"
    next_child = db.get_task(children[1])
    assert next_child.assigned_agent == "qa_engineer"
    # The prior-leg context must cite the AUTHENTICATED verdict (APPROVE),
    # not the unrelated newer row's REQUEST_CHANGES.
    assert "APPROVE" in next_child.brief
    assert "REQUEST_CHANGES" not in next_child.brief
    chain = ChainState.deserialize(db.get_task("TASK-P").active_chain)
    assert chain.step_index == 2
    assert q.items == [children[1]]
    # The audit row records the authenticated verdict.
    audit = db.get_audit_logs_by_action("chain_auto_advance")
    assert audit and audit[-1]["payload"]["triggering_verdict"] == "APPROVE"


def test_mixed_rows_newer_wrong_session_same_agent_cannot_override_auth_approve(tmp_path) -> None:
    """A newer row from the SAME agent but a DIFFERENT session is equally
    unrelated — the exact (task_id, assigned_agent, current_session_id)
    fingerprint must win, not merely (task_id, agent)."""
    db = Database(tmp_path / "x.db")
    _seed_chain_parent(
        db,
        legs=[("code_reviewer", "review brief", "APPROVE"),
              ("qa_engineer", "qa brief", "PASS")],
        step_index=1,
    )
    _seed_child_with_landed_result(
        db, child_id="TASK-R", parent_id="TASK-P", agent="code_reviewer",
        session_id="sess-r", verdict="APPROVE",
    )
    # Newer row, same agent, different (foreign) session, failing.
    db.insert_task_result(
        task_id="TASK-R", agent="code_reviewer", session_id="sess-foreign",
        status="completed", confidence_score=10,
        output_summary="unrelated session row", verdict="REQUEST_CHANGES",
    )
    q = _FakeQueue()
    _enqueue_parent_if_waiting(_make_orch(db, q), "TASK-R")

    children = db.get_children("TASK-P")
    assert len(children) == 2, f"chain wrongly cleared: {children}"
    assert db.get_task(children[1]).assigned_agent == "qa_engineer"
    chain = ChainState.deserialize(db.get_task("TASK-P").active_chain)
    assert chain.step_index == 2
    assert q.items == [children[1]]


def test_mixed_rows_newer_unrelated_passing_cannot_override_auth_failing(tmp_path) -> None:
    """Direction B: the exact-current authenticated REQUEST_CHANGES fails the
    chain closed (chain cleared + parent woken, no QA leg) even when a NEWER
    unrelated APPROVE row exists — a non-authoritative passing row can never
    advance the chain."""
    db = Database(tmp_path / "x.db")
    _seed_chain_parent(
        db,
        legs=[("code_reviewer", "review brief", "APPROVE"),
              ("qa_engineer", "qa brief", "PASS")],
        step_index=1,
    )
    _seed_child_with_landed_result(
        db, child_id="TASK-R", parent_id="TASK-P", agent="code_reviewer",
        session_id="sess-r", verdict="REQUEST_CHANGES",
    )
    # Newer unrelated row: wrong agent/session, PASSING verdict.
    db.insert_task_result(
        task_id="TASK-R", agent="intruder_agent", session_id="sess-intruder",
        status="completed", confidence_score=10,
        output_summary="unrelated newer row", verdict="APPROVE",
    )
    q = _FakeQueue()
    _enqueue_parent_if_waiting(_make_orch(db, q), "TASK-R")

    # No QA leg spawned; chain cleared; parent woken — fail closed.
    assert db.get_children("TASK-P") == ["TASK-R"]
    assert db.get_task("TASK-P").active_chain is None
    assert q.items == ["TASK-P"]


def test_mixed_rows_delayed_finalization_does_not_duplicate_advance(tmp_path) -> None:
    """Mixed-row variant of the at-most-once proof: after the recognition
    advance driven by the authenticated APPROVE, the delayed session
    finalization (child row now COMPLETED) must not spawn a duplicate leg or
    clear the chain — even with the unrelated newer row still present."""
    db = Database(tmp_path / "x.db")
    _seed_chain_parent(
        db,
        legs=[("code_reviewer", "review brief", "APPROVE"),
              ("qa_engineer", "qa brief", "PASS")],
        step_index=1,
    )
    _seed_child_with_landed_result(
        db, child_id="TASK-R", parent_id="TASK-P", agent="code_reviewer",
        session_id="sess-r", verdict="APPROVE",
    )
    db.insert_task_result(
        task_id="TASK-R", agent="intruder_agent", session_id="sess-intruder",
        status="completed", confidence_score=10,
        output_summary="unrelated newer row", verdict="REQUEST_CHANGES",
    )
    q = _FakeQueue()
    orch = _make_orch(db, q)

    # First consumer call during the lag window → recognition advance.
    _enqueue_parent_if_waiting(orch, "TASK-R")
    children_after_first = db.get_children("TASK-P")
    assert len(children_after_first) == 2

    # Delayed finalization + same child re-trigger → still exactly one leg.
    db.update_task("TASK-R", status=TaskStatus.COMPLETED)
    _enqueue_parent_if_waiting(orch, "TASK-R")

    children = db.get_children("TASK-P")
    assert len(children) == 2, f"duplicate leg spawned: {children}"
    chain = ChainState.deserialize(db.get_task("TASK-P").active_chain)
    assert chain.step_index == 2
    assert db.get_task("TASK-P").active_chain is not None
    assert q.items == [children_after_first[1]]


def test_mixed_rows_failed_advance_rolls_back_then_retry_advances_once(tmp_path, monkeypatch) -> None:
    """Retry/exception behavior under mixed rows: if the atomic chain-advance
    write fails, no partial child/link/audit/chain state remains and the
    parent wakes once; a later retry (write succeeds) produces exactly one
    advance from the SAME authenticated report."""
    db = Database(tmp_path / "x.db")
    _seed_chain_parent(
        db,
        legs=[("code_reviewer", "review brief", "APPROVE"),
              ("qa_engineer", "qa brief", "PASS")],
        step_index=1,
    )
    _seed_child_with_landed_result(
        db, child_id="TASK-R", parent_id="TASK-P", agent="code_reviewer",
        session_id="sess-r", verdict="APPROVE",
    )
    db.insert_task_result(
        task_id="TASK-R", agent="intruder_agent", session_id="sess-intruder",
        status="completed", confidence_score=10,
        output_summary="unrelated newer row", verdict="REQUEST_CHANGES",
    )
    real_try_advance_chain = Database.try_advance_chain
    state = {"fail": True}

    def _flaky(*args, **kwargs):
        if state["fail"]:
            return False  # simulate the transactional write failing
        return real_try_advance_chain(*args, **kwargs)

    monkeypatch.setattr(
        "runtime.infrastructure.database.Database.try_advance_chain", _flaky,
    )
    q = _FakeQueue()
    orch = _make_orch(db, q)

    # First attempt fails → rollback, chain intact at step 1, parent woken once.
    _enqueue_parent_if_waiting(orch, "TASK-R")
    assert db.get_children("TASK-P") == ["TASK-R"]
    chain = ChainState.deserialize(db.get_task("TASK-P").active_chain)
    assert chain.step_index == 1
    assert q.items == ["TASK-P"]

    # Retry succeeds → exactly one advance from the authenticated APPROVE.
    state["fail"] = False
    _enqueue_parent_if_waiting(orch, "TASK-R")
    children = db.get_children("TASK-P")
    assert len(children) == 2
    assert db.get_task(children[1]).assigned_agent == "qa_engineer"
    chain = ChainState.deserialize(db.get_task("TASK-P").active_chain)
    assert chain.step_index == 2
    assert q.items == ["TASK-P", children[1]]


# ---------------------------------------------------------------------------
# TASK-5818 fix-forward: fingerprint availability vs exact authenticated report.
# A modern child (assigned_agent + current_session_id present) whose exact
# authenticated report is missing or unacceptable (exact-row miss, wrong
# agent/session, malformed/unknown/blocked/nonterminal) must FAIL CLOSED for
# chain advancement — it must NEVER consult the task-wide newest-row read.
# Only a genuinely legacy child (no fingerprint) may use the unscoped
# fallback.  The authenticated exact report threading is unchanged.
# ---------------------------------------------------------------------------

def _seed_completed_child_with_fingerprint(
    db: Database, *, child_id: str, parent_id: str, agent: str, session_id: str,
) -> None:
    """Seed an already-COMPLETED child that carries a modern (assigned_agent,
    current_session_id) fingerprint — the TASK-5818 case where the row is
    terminal but the exact authenticated report is missing/unacceptable."""
    db.insert_task(TaskRecord(
        id=child_id, team="engineering", brief=child_id,
        parent_task_id=parent_id, assigned_agent=agent,
    ))
    db.update_task(
        child_id,
        status=TaskStatus.COMPLETED,
        current_session_id=session_id,
    )


def _assert_chain_failed_closed(db: Database, q: _FakeQueue) -> None:
    """The fail-closed outcome for a chain whose leg outcome is unverifiable:
    no next leg spawned, chain cleared, parent woken for the manager decision."""
    assert db.get_children("TASK-P") == ["TASK-R"]
    assert db.get_task("TASK-P").active_chain is None
    assert q.items == ["TASK-P"]


def test_modern_fingerprint_no_exact_row_wrong_agent_passing_row_must_not_spawn(tmp_path) -> None:
    """A COMPLETED child with a modern fingerprint but NO exact-row report must
    fail closed: an unrelated newest row (wrong agent AND wrong session,
    PASSING) must never spawn the next QA leg."""
    db = Database(tmp_path / "x.db")
    _seed_chain_parent(
        db,
        legs=[("code_reviewer", "review brief", "APPROVE"),
              ("qa_engineer", "qa brief", "PASS")],
        step_index=1,
    )
    _seed_completed_child_with_fingerprint(
        db, child_id="TASK-R", parent_id="TASK-P",
        agent="code_reviewer", session_id="sess-current",
    )
    # The ONLY task_results row is unrelated (wrong agent/session) and passing.
    db.insert_task_result(
        task_id="TASK-R", agent="intruder_agent", session_id="sess-intruder",
        status="completed", confidence_score=90,
        output_summary="unrelated passing row", verdict="APPROVE",
    )
    q = _FakeQueue()
    _enqueue_parent_if_waiting(_make_orch(db, q), "TASK-R")
    _assert_chain_failed_closed(db, q)


def test_modern_fingerprint_no_exact_row_wrong_agent_failing_row_must_not_spawn(tmp_path) -> None:
    """Same as above with a FAILING unrelated row: the task-wide evidence is
    never consulted in either verdict direction."""
    db = Database(tmp_path / "x.db")
    _seed_chain_parent(
        db,
        legs=[("code_reviewer", "review brief", "APPROVE"),
              ("qa_engineer", "qa brief", "PASS")],
        step_index=1,
    )
    _seed_completed_child_with_fingerprint(
        db, child_id="TASK-R", parent_id="TASK-P",
        agent="code_reviewer", session_id="sess-current",
    )
    db.insert_task_result(
        task_id="TASK-R", agent="intruder_agent", session_id="sess-intruder",
        status="completed", confidence_score=10,
        output_summary="unrelated failing row", verdict="REQUEST_CHANGES",
    )
    q = _FakeQueue()
    _enqueue_parent_if_waiting(_make_orch(db, q), "TASK-R")
    _assert_chain_failed_closed(db, q)


def test_modern_fingerprint_no_exact_row_wrong_session_passing_row_must_not_spawn(tmp_path) -> None:
    """A newer row from the SAME agent but a DIFFERENT session is equally
    unrelated — the exact (task_id, assigned_agent, current_session_id)
    fingerprint is the only acceptable evidence; a wrong-session PASSING row
    must not advance the chain."""
    db = Database(tmp_path / "x.db")
    _seed_chain_parent(
        db,
        legs=[("code_reviewer", "review brief", "APPROVE"),
              ("qa_engineer", "qa brief", "PASS")],
        step_index=1,
    )
    _seed_completed_child_with_fingerprint(
        db, child_id="TASK-R", parent_id="TASK-P",
        agent="code_reviewer", session_id="sess-current",
    )
    db.insert_task_result(
        task_id="TASK-R", agent="code_reviewer", session_id="sess-foreign",
        status="completed", confidence_score=90,
        output_summary="wrong-session passing row", verdict="APPROVE",
    )
    q = _FakeQueue()
    _enqueue_parent_if_waiting(_make_orch(db, q), "TASK-R")
    _assert_chain_failed_closed(db, q)


def test_modern_fingerprint_no_exact_row_wrong_session_failing_row_must_not_spawn(tmp_path) -> None:
    """Wrong-session FAILING row: fail closed, never consulted."""
    db = Database(tmp_path / "x.db")
    _seed_chain_parent(
        db,
        legs=[("code_reviewer", "review brief", "APPROVE"),
              ("qa_engineer", "qa brief", "PASS")],
        step_index=1,
    )
    _seed_completed_child_with_fingerprint(
        db, child_id="TASK-R", parent_id="TASK-P",
        agent="code_reviewer", session_id="sess-current",
    )
    db.insert_task_result(
        task_id="TASK-R", agent="code_reviewer", session_id="sess-foreign",
        status="completed", confidence_score=10,
        output_summary="wrong-session failing row", verdict="REQUEST_CHANGES",
    )
    q = _FakeQueue()
    _enqueue_parent_if_waiting(_make_orch(db, q), "TASK-R")
    _assert_chain_failed_closed(db, q)


def test_modern_fingerprint_exact_blocked_row_plus_unrelated_passing_row_fails_closed(tmp_path) -> None:
    """The exact fingerprint row EXISTS but is a blocked report (the
    blocked_on_job park is a live state, never dispatch-terminal).  Even with
    an unrelated passing newest row, the chain must fail closed — no spawn."""
    db = Database(tmp_path / "x.db")
    _seed_chain_parent(
        db,
        legs=[("code_reviewer", "review brief", "APPROVE"),
              ("qa_engineer", "qa brief", "PASS")],
        step_index=1,
    )
    _seed_completed_child_with_fingerprint(
        db, child_id="TASK-R", parent_id="TASK-P",
        agent="code_reviewer", session_id="sess-current",
    )
    db.insert_task_result(
        task_id="TASK-R", agent="code_reviewer", session_id="sess-current",
        status="blocked", confidence_score=10,
        output_summary="blocked exact row", verdict=None,
    )
    db.insert_task_result(
        task_id="TASK-R", agent="intruder_agent", session_id="sess-intruder",
        status="completed", confidence_score=90,
        output_summary="unrelated passing row", verdict="APPROVE",
    )
    q = _FakeQueue()
    _enqueue_parent_if_waiting(_make_orch(db, q), "TASK-R")
    _assert_chain_failed_closed(db, q)


def test_modern_fingerprint_exact_nonterminal_row_plus_unrelated_passing_row_fails_closed(tmp_path) -> None:
    """The exact fingerprint row EXISTS but with a nonterminal/unknown status
    (malformed report) — fail closed even with an unrelated passing row."""
    db = Database(tmp_path / "x.db")
    _seed_chain_parent(
        db,
        legs=[("code_reviewer", "review brief", "APPROVE"),
              ("qa_engineer", "qa brief", "PASS")],
        step_index=1,
    )
    _seed_completed_child_with_fingerprint(
        db, child_id="TASK-R", parent_id="TASK-P",
        agent="code_reviewer", session_id="sess-current",
    )
    db.insert_task_result(
        task_id="TASK-R", agent="code_reviewer", session_id="sess-current",
        status="mystery-status", confidence_score=10,
        output_summary="malformed exact row", verdict=None,
    )
    db.insert_task_result(
        task_id="TASK-R", agent="intruder_agent", session_id="sess-intruder",
        status="completed", confidence_score=90,
        output_summary="unrelated passing row", verdict="APPROVE",
    )
    q = _FakeQueue()
    _enqueue_parent_if_waiting(_make_orch(db, q), "TASK-R")
    _assert_chain_failed_closed(db, q)


def test_exact_current_passing_report_still_advances_once_across_delayed_calls(tmp_path) -> None:
    """The authenticated exact passing report keeps its authority: with an
    unrelated newer failing row present, repeated consumer calls (including
    the delayed-finalization re-trigger) advance exactly once — never from
    the unrelated row, never a duplicate leg."""
    db = Database(tmp_path / "x.db")
    _seed_chain_parent(
        db,
        legs=[("code_reviewer", "review brief", "APPROVE"),
              ("qa_engineer", "qa brief", "PASS")],
        step_index=1,
    )
    _seed_child_with_landed_result(
        db, child_id="TASK-R", parent_id="TASK-P", agent="code_reviewer",
        session_id="sess-r", verdict="APPROVE",
    )
    db.insert_task_result(
        task_id="TASK-R", agent="intruder_agent", session_id="sess-intruder",
        status="completed", confidence_score=10,
        output_summary="unrelated newer row", verdict="REQUEST_CHANGES",
    )
    q = _FakeQueue()
    orch = _make_orch(db, q)

    # Lag-window recognition advance (row still in_progress).
    _enqueue_parent_if_waiting(orch, "TASK-R")
    # Delayed finalization re-trigger (row now COMPLETED).
    db.update_task("TASK-R", status=TaskStatus.COMPLETED)
    _enqueue_parent_if_waiting(orch, "TASK-R")

    children = db.get_children("TASK-P")
    assert len(children) == 2, f"expected exactly one advance, got {children}"
    assert db.get_task(children[1]).assigned_agent == "qa_engineer"
    chain = ChainState.deserialize(db.get_task("TASK-P").active_chain)
    assert chain.step_index == 2
    assert q.items == [children[1]]


def test_exact_current_failing_report_still_clears_and_wakes_once(tmp_path) -> None:
    """The authenticated exact failing report keeps its authority: the chain
    is cleared and the parent woken — and a repeated call (delayed
    finalization) never spawns a leg and never revives the chain."""
    db = Database(tmp_path / "x.db")
    _seed_chain_parent(
        db,
        legs=[("code_reviewer", "review brief", "APPROVE"),
              ("qa_engineer", "qa brief", "PASS")],
        step_index=1,
    )
    _seed_child_with_landed_result(
        db, child_id="TASK-R", parent_id="TASK-P", agent="code_reviewer",
        session_id="sess-r", verdict="REQUEST_CHANGES",
    )
    db.insert_task_result(
        task_id="TASK-R", agent="intruder_agent", session_id="sess-intruder",
        status="completed", confidence_score=90,
        output_summary="unrelated newer row", verdict="APPROVE",
    )
    q = _FakeQueue()
    orch = _make_orch(db, q)
    db.update_task("TASK-R", status=TaskStatus.COMPLETED)

    _enqueue_parent_if_waiting(orch, "TASK-R")
    _assert_chain_failed_closed(db, q)
    # Delayed re-trigger: no leg spawn, chain stays cleared; the second
    # parent put is absorbed by the run-step claim CAS (at-most-once wake).
    _enqueue_parent_if_waiting(orch, "TASK-R")
    assert db.get_children("TASK-P") == ["TASK-R"]
    assert db.get_task("TASK-P").active_chain is None
    assert q.items == ["TASK-P", "TASK-P"]


def test_genuinely_legacy_no_fingerprint_completed_row_keeps_documented_fallback(tmp_path) -> None:
    """A genuinely legacy completed child (NO assigned_agent / NO
    current_session_id on the task row) retains the documented unscoped
    newest-row fallback: a passing newest row advances the chain."""
    db = Database(tmp_path / "x.db")
    _seed_chain_parent(
        db,
        legs=[("code_reviewer", "review brief", "APPROVE"),
              ("qa_engineer", "qa brief", "PASS")],
        step_index=1,
    )
    # Legacy child: no agent, no session — only the result row carries one.
    db.insert_task(TaskRecord(
        id="TASK-R", team="engineering", brief="legacy",
        parent_task_id="TASK-P",
    ))
    db.update_task("TASK-R", status=TaskStatus.COMPLETED)
    db.insert_task_result(
        task_id="TASK-R", agent="dev_agent", session_id="sess-legacy",
        status="completed", confidence_score=90,
        output_summary="legacy passing row", verdict="APPROVE",
    )
    q = _FakeQueue()
    _enqueue_parent_if_waiting(_make_orch(db, q), "TASK-R")

    # Documented fallback: newest-row read advances the chain.
    children = db.get_children("TASK-P")
    assert len(children) == 2
    assert db.get_task(children[1]).assigned_agent == "qa_engineer"
    assert q.items == [children[1]]


def test_genuinely_legacy_no_fingerprint_completed_row_failing_newest_row_fails_closed(tmp_path) -> None:
    """Legacy fallback in the failing direction: the newest-row read is a
    REQUEST_CHANGES → chain cleared + parent woken (documented behavior)."""
    db = Database(tmp_path / "x.db")
    _seed_chain_parent(
        db,
        legs=[("code_reviewer", "review brief", "APPROVE"),
              ("qa_engineer", "qa brief", "PASS")],
        step_index=1,
    )
    db.insert_task(TaskRecord(
        id="TASK-R", team="engineering", brief="legacy",
        parent_task_id="TASK-P",
    ))
    db.update_task("TASK-R", status=TaskStatus.COMPLETED)
    db.insert_task_result(
        task_id="TASK-R", agent="dev_agent", session_id="sess-legacy",
        status="completed", confidence_score=10,
        output_summary="legacy failing row", verdict="REQUEST_CHANGES",
    )
    q = _FakeQueue()
    _enqueue_parent_if_waiting(_make_orch(db, q), "TASK-R")
    _assert_chain_failed_closed(db, q)


def test_genuinely_legacy_no_fingerprint_completed_row_without_any_result_clears_chain(tmp_path) -> None:
    """Legacy completed child with NO result rows at all: the fallback read
    returns None → chain cleared + parent woken (no spawn, no crash)."""
    db = Database(tmp_path / "x.db")
    _seed_chain_parent(
        db,
        legs=[("code_reviewer", "review brief", "APPROVE"),
              ("qa_engineer", "qa brief", "PASS")],
        step_index=1,
    )
    db.insert_task(TaskRecord(
        id="TASK-R", team="engineering", brief="legacy",
        parent_task_id="TASK-P",
    ))
    db.update_task("TASK-R", status=TaskStatus.COMPLETED)
    q = _FakeQueue()
    _enqueue_parent_if_waiting(_make_orch(db, q), "TASK-R")
    _assert_chain_failed_closed(db, q)


def test_modern_fingerprint_fail_closed_delayed_finalization_spawns_no_leg(tmp_path) -> None:
    """Delayed-finalization no-duplicate proof for the fail-closed case: after
    the first fail-closed wake (chain cleared, no leg), a second consumer call
    spawns no leg and does not revive the chain; the extra parent put is
    absorbed by the run-step claim CAS (at-most-once wake)."""
    db = Database(tmp_path / "x.db")
    _seed_chain_parent(
        db,
        legs=[("code_reviewer", "review brief", "APPROVE"),
              ("qa_engineer", "qa brief", "PASS")],
        step_index=1,
    )
    _seed_completed_child_with_fingerprint(
        db, child_id="TASK-R", parent_id="TASK-P",
        agent="code_reviewer", session_id="sess-current",
    )
    db.insert_task_result(
        task_id="TASK-R", agent="intruder_agent", session_id="sess-intruder",
        status="completed", confidence_score=90,
        output_summary="unrelated passing row", verdict="APPROVE",
    )
    q = _FakeQueue()
    orch = _make_orch(db, q)

    _enqueue_parent_if_waiting(orch, "TASK-R")
    _assert_chain_failed_closed(db, q)
    # Delayed finalization re-trigger (child already COMPLETED).
    _enqueue_parent_if_waiting(orch, "TASK-R")
    assert db.get_children("TASK-P") == ["TASK-R"]
    assert db.get_task("TASK-P").active_chain is None
    assert q.items == ["TASK-P", "TASK-P"]


def test_modern_fingerprint_fail_closed_exception_rolls_back_then_retry_wakes_once(tmp_path, monkeypatch) -> None:
    """Exception/retry proof for the fail-closed case: a transient DB failure
    while clearing the chain leaves NO partial state (no leg, no wake), and a
    retry completes the fail-closed wake exactly once."""
    db = Database(tmp_path / "x.db")
    _seed_chain_parent(
        db,
        legs=[("code_reviewer", "review brief", "APPROVE"),
              ("qa_engineer", "qa brief", "PASS")],
        step_index=1,
    )
    _seed_completed_child_with_fingerprint(
        db, child_id="TASK-R", parent_id="TASK-P",
        agent="code_reviewer", session_id="sess-current",
    )
    db.insert_task_result(
        task_id="TASK-R", agent="intruder_agent", session_id="sess-intruder",
        status="completed", confidence_score=90,
        output_summary="unrelated passing row", verdict="APPROVE",
    )
    real_update = Database.update_task_active_chain
    state = {"fail": True}

    def _flaky(*args, **kwargs):
        if state["fail"]:
            raise RuntimeError("simulated transient DB failure")
        return real_update(*args, **kwargs)

    monkeypatch.setattr(
        "runtime.infrastructure.database.Database.update_task_active_chain",
        _flaky,
    )
    q = _FakeQueue()
    orch = _make_orch(db, q)

    # First attempt raises → no partial state: no leg, chain intact, no wake.
    with pytest.raises(RuntimeError):
        _enqueue_parent_if_waiting(orch, "TASK-R")
    assert db.get_children("TASK-P") == ["TASK-R"]
    assert db.get_task("TASK-P").active_chain is not None
    assert q.items == []

    # Retry succeeds → fail-closed wake once, no leg ever spawned.
    state["fail"] = False
    _enqueue_parent_if_waiting(orch, "TASK-R")
    _assert_chain_failed_closed(db, q)


def test_mutation_force_legacy_fallback_reproduces_unauthorized_spawn(tmp_path, monkeypatch) -> None:
    """Mutation-guard: forcing the fingerprint distinction wrong (treating a
    modern child as genuine legacy absence) reproduces the TASK-5817 HIGH
    finding — the unrelated passing row advances the chain and spawns the QA
    leg.  This test pins the sensitivity of the fail-closed sibling tests:
    the TASK-5818 fix is exactly the distinction that prevents this spawn."""
    from runtime.orchestrator.run_step import _child_has_modern_fingerprint

    monkeypatch.setattr(
        "runtime.orchestrator.run_step._child_has_modern_fingerprint",
        lambda child: False,  # force the fallback distinction wrong
    )
    db = Database(tmp_path / "x.db")
    _seed_chain_parent(
        db,
        legs=[("code_reviewer", "review brief", "APPROVE"),
              ("qa_engineer", "qa brief", "PASS")],
        step_index=1,
    )
    _seed_completed_child_with_fingerprint(
        db, child_id="TASK-R", parent_id="TASK-P",
        agent="code_reviewer", session_id="sess-current",
    )
    db.insert_task_result(
        task_id="TASK-R", agent="intruder_agent", session_id="sess-intruder",
        status="completed", confidence_score=90,
        output_summary="unrelated passing row", verdict="APPROVE",
    )
    q = _FakeQueue()
    _enqueue_parent_if_waiting(_make_orch(db, q), "TASK-R")

    # Under the mutation the newest-row fallback advances from the unrelated
    # passing row — the exact unauthorized spawn the fix must prevent.
    children = db.get_children("TASK-P")
    assert len(children) == 2
    assert db.get_task(children[1]).assigned_agent == "qa_engineer"


# ---------------------------------------------------------------------------
# TASK-5823 (third fix-forward): structurally malformed exact rows fail closed
# ---------------------------------------------------------------------------
# The exact-scoped `get_latest_completion_report(task_id, agent, session_id)`
# read (the sole reader of the modern fingerprint) must convert ONLY row
# deserialization/structural-validation failure — invalid JSON in the
# persisted `risks_flagged` / `waiting_on_job_ids` JSON text, or values
# failing the strict `CompletionReport` contract (wrong scalar/container
# shape, invalid element types, out-of-range confidence) — into the existing
# no-acceptable-authenticated-report fail-closed path (`_NO_AUTHENTICATED_
# REPORT`): chain cleared, parent woken once, task-wide evidence NEVER
# consulted.  SQLite/transaction/I/O/programming/operational exceptions still
# propagate.  A malformed exact row is never terminal evidence for a running
# child, and a well-formed exact report keeps its full authority.

def _corrupt_exact_result_row(
    db: Database, *, task_id: str, session_id: str,
    column: str, raw,
) -> None:
    """Overwrite one persisted column of the exact (task_id, session_id) row
    with a value that fails deserialization/structural validation.  ``column``
    is a test-controlled literal (risks_flagged / waiting_on_job_ids /
    confidence_score), never user input."""
    db._conn.execute(
        f"UPDATE task_results SET {column} = ? "
        "WHERE task_id = ? AND session_id = ?",
        (raw, task_id, session_id),
    )
    db._conn.commit()


def _seed_completed_child_with_malformed_exact_row(
    db: Database,
    *,
    corrupt_column: str,
    corrupt_raw,
    child_id: str = "TASK-R",
    parent_id: str = "TASK-P",
    agent: str = "code_reviewer",
    session_id: str = "sess-current",
) -> None:
    """Seed the TASK-5822 shape: a COMPLETED child carrying a modern
    (assigned_agent, current_session_id) fingerprint whose EXACT current
    task_results row is structurally malformed, PLUS an unrelated task-wide
    APPROVE row that must never gate the chain."""
    _seed_chain_parent(
        db,
        legs=[("code_reviewer", "review brief", "APPROVE"),
              ("qa_engineer", "qa brief", "PASS")],
        step_index=1,
    )
    _seed_completed_child_with_fingerprint(
        db, child_id=child_id, parent_id=parent_id,
        agent=agent, session_id=session_id,
    )
    db.insert_task_result(
        task_id=child_id, agent=agent, session_id=session_id,
        status="completed", confidence_score=90,
        output_summary="exact completed row", verdict="APPROVE",
    )
    _corrupt_exact_result_row(
        db, task_id=child_id, session_id=session_id,
        column=corrupt_column, raw=corrupt_raw,
    )
    # Unrelated task-wide APPROVE — the newest task row.  Must never be
    # consulted for chain advancement when the exact row is unacceptable.
    db.insert_task_result(
        task_id=child_id, agent="intruder_agent", session_id="sess-intruder",
        status="completed", confidence_score=90,
        output_summary="unrelated passing row", verdict="APPROVE",
    )


def test_modern_fingerprint_exact_row_invalid_risks_json_fails_closed_with_unrelated_approve(tmp_path) -> None:
    """TASK-5822 HIGH repro: invalid JSON in the exact row's persisted
    risks_flagged must NOT raise out of the dispatch seam — it fails closed
    (chain cleared, parent woken once, no leg) with an unrelated task-wide
    APPROVE present."""
    import json as _json

    db = Database(tmp_path / "x.db")
    _seed_completed_child_with_malformed_exact_row(
        db, corrupt_column="risks_flagged", corrupt_raw="not-json{{[",
    )
    q = _FakeQueue()
    _enqueue_parent_if_waiting(_make_orch(db, q), "TASK-R")
    _assert_chain_failed_closed(db, q)
    # Sanity: the raw value really is invalid JSON (shape class proof).
    with pytest.raises(_json.JSONDecodeError):
        _json.loads("not-json{{[")


def test_modern_fingerprint_exact_row_invalid_waiting_on_job_ids_json_fails_closed_with_unrelated_approve(tmp_path) -> None:
    """Invalid JSON in the exact row's persisted waiting_on_job_ids — same
    fail-closed outcome (second structured column, independent shape class)."""
    db = Database(tmp_path / "x.db")
    _seed_completed_child_with_malformed_exact_row(
        db, corrupt_column="waiting_on_job_ids", corrupt_raw="[unclosed",
    )
    q = _FakeQueue()
    _enqueue_parent_if_waiting(_make_orch(db, q), "TASK-R")
    _assert_chain_failed_closed(db, q)


def test_modern_fingerprint_exact_row_risks_scalar_shape_fails_closed_with_unrelated_approve(tmp_path) -> None:
    """Valid JSON of the WRONG scalar shape: risks_flagged is a JSON string
    rather than a list — the strict CompletionReport contract rejects it and
    the read fails closed."""
    db = Database(tmp_path / "x.db")
    _seed_completed_child_with_malformed_exact_row(
        db, corrupt_column="risks_flagged", corrupt_raw='"oops-not-a-list"',
    )
    q = _FakeQueue()
    _enqueue_parent_if_waiting(_make_orch(db, q), "TASK-R")
    _assert_chain_failed_closed(db, q)


def test_modern_fingerprint_exact_row_risks_dict_shape_fails_closed_with_unrelated_approve(tmp_path) -> None:
    """Valid JSON of the wrong CONTAINER shape: risks_flagged is a JSON object
    (dict) instead of a list — fail closed."""
    db = Database(tmp_path / "x.db")
    _seed_completed_child_with_malformed_exact_row(
        db, corrupt_column="risks_flagged", corrupt_raw='{"not": "a list"}',
    )
    q = _FakeQueue()
    _enqueue_parent_if_waiting(_make_orch(db, q), "TASK-R")
    _assert_chain_failed_closed(db, q)


def test_modern_fingerprint_exact_row_risks_non_string_elements_fails_closed_with_unrelated_approve(tmp_path) -> None:
    """Valid JSON list with INVALID element types: risks_flagged must be
    list[str], a list of ints violates the strict contract — fail closed."""
    db = Database(tmp_path / "x.db")
    _seed_completed_child_with_malformed_exact_row(
        db, corrupt_column="risks_flagged", corrupt_raw="[1, 2, 3]",
    )
    q = _FakeQueue()
    _enqueue_parent_if_waiting(_make_orch(db, q), "TASK-R")
    _assert_chain_failed_closed(db, q)


def test_modern_fingerprint_exact_row_confidence_out_of_range_fails_closed_with_unrelated_approve(tmp_path) -> None:
    """Structural validation failure beyond the JSON columns: confidence_score
    outside the strict CompletionReport range (0..100) fails closed too."""
    db = Database(tmp_path / "x.db")
    _seed_completed_child_with_malformed_exact_row(
        db, corrupt_column="confidence_score", corrupt_raw=150,
    )
    q = _FakeQueue()
    _enqueue_parent_if_waiting(_make_orch(db, q), "TASK-R")
    _assert_chain_failed_closed(db, q)


def test_in_progress_child_with_malformed_exact_row_does_not_count_as_landed(tmp_path) -> None:
    """A malformed exact row is NOT terminal evidence: an in_progress child
    whose exact row is structurally malformed stays 'genuinely running' — the
    parent is neither advanced nor woken (no suppression of live work, no
    premature dispatch from unverifiable data)."""
    db = Database(tmp_path / "x.db")
    _seed_chain_parent(
        db,
        legs=[("code_reviewer", "review brief", "APPROVE"),
              ("qa_engineer", "qa brief", "PASS")],
        step_index=1,
    )
    _seed_child_with_landed_result(
        db, child_id="TASK-R", parent_id="TASK-P",
        agent="code_reviewer", session_id="sess-current", verdict="APPROVE",
    )
    _corrupt_exact_result_row(
        db, task_id="TASK-R", session_id="sess-current",
        column="risks_flagged", raw="not-json{{[",
    )
    q = _FakeQueue()
    orch = _make_orch(db, q)
    assert _child_has_landed_terminal_result(orch, db.get_task("TASK-R")) is False
    _enqueue_parent_if_waiting(orch, "TASK-R")
    assert db.get_children("TASK-P") == ["TASK-R"]
    assert db.get_task("TASK-P").active_chain is not None
    assert q.items == []


def test_modern_fingerprint_malformed_exact_row_delayed_finalization_no_duplicate(tmp_path) -> None:
    """Delayed-finalization no-duplicate for the malformed-row fail-closed
    case: the first consumer call clears the chain and wakes the parent once;
    a second call (delayed session-finalization re-trigger) spawns no leg and
    does not revive the chain — the extra put is absorbed by the run-step
    claim CAS (at-most-once wake)."""
    db = Database(tmp_path / "x.db")
    _seed_completed_child_with_malformed_exact_row(
        db, corrupt_column="risks_flagged", corrupt_raw="not-json{{[",
    )
    q = _FakeQueue()
    orch = _make_orch(db, q)

    _enqueue_parent_if_waiting(orch, "TASK-R")
    _assert_chain_failed_closed(db, q)
    # Delayed re-trigger (child already COMPLETED).
    _enqueue_parent_if_waiting(orch, "TASK-R")
    assert db.get_children("TASK-P") == ["TASK-R"]
    assert db.get_task("TASK-P").active_chain is None
    assert q.items == ["TASK-P", "TASK-P"]


def test_modern_fingerprint_malformed_exact_row_operational_db_failure_propagates_then_retry_wakes_once(tmp_path) -> None:
    """Operational database exceptions are NOT converted to fail-closed: a
    sqlite3.OperationalError during the exact read propagates out of
    _enqueue_parent_if_waiting with NO partial chain/queue/audit mutation,
    and a retry completes the fail-closed wake exactly once."""
    import sqlite3 as _sqlite3

    db = Database(tmp_path / "x.db")
    _seed_completed_child_with_malformed_exact_row(
        db, corrupt_column="risks_flagged", corrupt_raw="not-json{{[",
    )

    real_conn = db._conn

    class _FailOnceTaskResultsRead:
        """Proxy the sqlite3.Connection: fail the FIRST task_results SELECT
        with an operational error, then delegate everything (retry-safety)."""

        def __init__(self, real) -> None:
            self._real = real
            self._fail = True

        def execute(self, sql, *parameters):
            if (
                self._fail
                and sql.lstrip().upper().startswith("SELECT")
                and "task_results" in sql
            ):
                self._fail = False
                raise _sqlite3.OperationalError("simulated operational DB failure")
            return self._real.execute(sql, *parameters)

        def __getattr__(self, name):
            return getattr(self._real, name)

    db._conn = _FailOnceTaskResultsRead(real_conn)
    q = _FakeQueue()
    orch = _make_orch(db, q)

    # First attempt: operational failure propagates — no partial mutation.
    with pytest.raises(_sqlite3.OperationalError):
        _enqueue_parent_if_waiting(orch, "TASK-R")
    assert db.get_children("TASK-P") == ["TASK-R"]
    assert db.get_task("TASK-P").active_chain is not None
    assert q.items == []
    audits, _ = db.query_audit_logs(task_id="TASK-P", limit=100)
    assert audits == []

    # Retry: the malformed row fail-closes exactly once (chain cleared, one
    # wake, no leg, no audit row).
    _enqueue_parent_if_waiting(orch, "TASK-R")
    _assert_chain_failed_closed(db, q)
    audits, _ = db.query_audit_logs(task_id="TASK-P", limit=100)
    assert audits == []


def test_positive_control_exact_valid_row_with_structured_fields_still_advances(tmp_path) -> None:
    """Positive control: a VALID exact current row — including populated
    risks_flagged / waiting_on_job_ids JSON that round-trips through the
    strict contract — still advances the chain normally.  The fix must never
    degrade a well-formed authenticated report."""
    db = Database(tmp_path / "x.db")
    _seed_chain_parent(
        db,
        legs=[("code_reviewer", "review brief", "APPROVE"),
              ("qa_engineer", "qa brief", "PASS")],
        step_index=1,
    )
    _seed_child_with_landed_result(
        db, child_id="TASK-R", parent_id="TASK-P",
        agent="code_reviewer", session_id="sess-r", verdict="APPROVE",
    )
    db._conn.execute(
        "UPDATE task_results SET risks_flagged = ?, waiting_on_job_ids = ? "
        "WHERE task_id = ? AND session_id = ?",
        ('["risk one", "risk two"]', '["JOB-1"]', "TASK-R", "sess-r"),
    )
    db._conn.commit()
    q = _FakeQueue()
    _enqueue_parent_if_waiting(_make_orch(db, q), "TASK-R")

    children = db.get_children("TASK-P")
    assert len(children) == 2
    assert db.get_task(children[1]).assigned_agent == "qa_engineer"
    assert q.items == [children[1]]


def test_mutation_malformed_exact_row_raising_strands_chain_reproduces_task5822(tmp_path, monkeypatch) -> None:
    """Mutation-guard: without the fail-closed boundary (the pre-TASK-5823
    reader re-raises the deserialization failure), the same malformed exact
    row strands active_chain and suppresses the parent wake — reproducing the
    TASK-5822 HIGH finding.  Pins the sensitivity of every malformed-row
    fail-closed test to the exact boundary this fix adds."""
    import json as _json

    from runtime.infrastructure import database as db_module

    def _no_boundary(self, task_id, agent=None, session_id=None):
        # Faithful pre-fix reader: raw row → CompletionReport conversion with
        # no deserialization/validation boundary.
        if agent is not None and session_id is not None:
            row = self._conn.execute(
                "SELECT * FROM task_results WHERE task_id = ? "
                "AND agent = ? AND session_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (task_id, agent, session_id),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT * FROM task_results WHERE task_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_completion_report(task_id, row)

    monkeypatch.setattr(
        db_module.Database, "get_latest_completion_report", _no_boundary,
    )
    db = Database(tmp_path / "x.db")
    _seed_completed_child_with_malformed_exact_row(
        db, corrupt_column="risks_flagged", corrupt_raw="not-json{{[",
    )
    q = _FakeQueue()

    with pytest.raises(_json.JSONDecodeError):
        _enqueue_parent_if_waiting(_make_orch(db, q), "TASK-R")
    # Stranded: chain intact, no parent wake, no successor dispatch.
    assert db.get_children("TASK-P") == ["TASK-R"]
    assert db.get_task("TASK-P").active_chain is not None
    assert q.items == []
