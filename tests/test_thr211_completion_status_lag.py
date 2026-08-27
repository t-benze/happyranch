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
