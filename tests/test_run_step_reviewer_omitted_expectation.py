"""Focused regression tests locking the reviewer -> QA transition when the
reviewer leg's ``expect_verdict`` is OMITTED (None).

PR #681 locked the transition for a reviewer leg that DECLARES
``expect_verdict=APPROVE``. This file closes the adjacent hole: an authored
``dev -> code_reviewer -> qa_engineer`` chain whose reviewer ChainLeg omits
``expect_verdict`` (None) historically advanced QA even after
REQUEST_CHANGES, because ``compute_advance_action`` only wakes on
``expected is not None and verdict != expected``. The omitted-expectation
reviewer leg is a review GATE by role; with a downstream leg it must be
fail-closed (wake/clear, never advance) rather than auto-dispatching QA on a
verdict it never gated.

Every test drives the REAL production seam — ``_enqueue_parent_if_waiting``
against a real ``Database`` + ``Orchestrator`` (never a ``compute_advance_action``
lookalike) — and locks durable state: child ids/count, ``active_chain``, queue
entries, and ``chain_auto_advance`` audit rows.
"""
from __future__ import annotations

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
        "    workers: [product_manager, dev_agent, code_reviewer, qa_engineer, payment_agent]\n"
    )
    return paths


@pytest.fixture
def db(runtime: OrgPaths) -> Database:
    return Database(runtime.db_path)


class _SlugQueue:
    """Test adapter for production's 2-arg ``put_nowait(slug, task_id)``."""

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


def _make_orch(db: Database, runtime: OrgPaths):
    from runtime.orchestrator.orchestrator import Orchestrator
    orch = Orchestrator(
        db=db, settings=Settings(), paths=runtime, slug="test",
        teams=TeamsRegistry.load(runtime.root),
    )
    orch._queue = _SlugQueue()
    return orch


def _omitted_review_chain(*, step_index: int, step_audit_id: int = 1):
    """dev (first leg, already completed) -> code_reviewer (expect_verdict
    OMITTED) -> qa_engineer (expect_verdict=PASS)."""
    from runtime.orchestrator.chain import ChainState
    return ChainState(
        step_index=step_index,
        first_leg_expect_verdict=None,
        legs=[
            ChainLeg(agent="code_reviewer", prompt="review the PR",
                     expect_verdict=None),
            ChainLeg(agent="qa_engineer", prompt="QA the PR",
                     expect_verdict="PASS"),
        ],
        step_audit_id=step_audit_id,
    )


def _seed_parent(db: Database, parent_id: str = "T-PAR") -> None:
    db.insert_task(TaskRecord(
        id=parent_id, brief="chain parent",
        assigned_agent="engineering_head", task_type="task",
    ))
    db.update_task(parent_id, status=TaskStatus.IN_PROGRESS,
                   block_kind=BlockKind.DELEGATED, note="waiting")


def _seed_completed_child(db: Database, *, child_id: str, parent_id: str,
                          agent: str, verdict: str | None,
                          summary: str = "done") -> None:
    db.insert_task(TaskRecord(
        id=child_id, brief=agent, assigned_agent=agent,
        parent_task_id=parent_id, task_type="subtask",
    ))
    db.update_task(child_id, status=TaskStatus.COMPLETED, note="done")
    db.insert_task_result(
        task_id=child_id, agent=agent, session_id="s",
        status="completed", confidence_score=85,
        output_summary=summary, verdict=verdict,
    )


def _qa_children(db: Database, parent_id: str) -> list[str]:
    return [c for c in db.get_children(parent_id)
            if db.get_task(c).assigned_agent == "qa_engineer"]


def _chain_advances(db: Database, parent_id: str) -> list[dict]:
    return [a for a in db.get_audit_logs(parent_id)
            if a["action"] == "chain_auto_advance"]


# ---------------------------------------------------------------------------
# (1) RED scenario: reviewer expect_verdict=None + REQUEST_CHANGES.
# ---------------------------------------------------------------------------
def test_omitted_reviewer_request_changes_spawns_no_qa(runtime, db):
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    _seed_parent(db)
    db.update_task_active_chain("T-PAR", _omitted_review_chain(step_index=1).serialize())
    _seed_completed_child(db, child_id="T-REV", parent_id="T-PAR",
                          agent="code_reviewer", verdict="REQUEST_CHANGES",
                          summary="needs changes")

    orch = _make_orch(db, runtime)
    _enqueue_parent_if_waiting(orch, "T-REV")

    assert _qa_children(db, "T-PAR") == []            # no QA child spawned
    assert db.get_task("T-PAR").active_chain is None   # chain cleared
    assert _chain_advances(db, "T-PAR") == []          # no auto-advance audit
    assert orch._queue.qsize() == 1                    # parent woken once
    slug, tid = orch._queue.get_nowait()
    assert tid == "T-PAR"


# ---------------------------------------------------------------------------
# (2) same omitted reviewer config with a MISSING verdict.
# ---------------------------------------------------------------------------
def test_omitted_reviewer_missing_verdict_spawns_no_qa(runtime, db):
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    _seed_parent(db)
    db.update_task_active_chain("T-PAR", _omitted_review_chain(step_index=1).serialize())
    _seed_completed_child(db, child_id="T-REV", parent_id="T-PAR",
                          agent="code_reviewer", verdict=None,
                          summary="reviewed")

    orch = _make_orch(db, runtime)
    _enqueue_parent_if_waiting(orch, "T-REV")

    assert _qa_children(db, "T-PAR") == []
    assert db.get_task("T-PAR").active_chain is None
    assert _chain_advances(db, "T-PAR") == []
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == "T-PAR"


# ---------------------------------------------------------------------------
# (3) an EXPLICIT reviewer APPROVE expectation still reaches exactly one QA
# child/queue/audit with prior-leg context (preserve).
# ---------------------------------------------------------------------------
def test_explicit_approve_reviewer_reaches_one_qa(runtime, db):
    from runtime.orchestrator.chain import ChainState
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    _seed_parent(db)
    chain = ChainState(
        step_index=1,
        first_leg_expect_verdict=None,
        legs=[
            ChainLeg(agent="code_reviewer", prompt="review the PR",
                     expect_verdict="APPROVE"),
            ChainLeg(agent="qa_engineer", prompt="QA the PR",
                     expect_verdict="PASS"),
        ],
        step_audit_id=1,
    )
    db.update_task_active_chain("T-PAR", chain.serialize())
    _seed_completed_child(db, child_id="T-REV", parent_id="T-PAR",
                          agent="code_reviewer", verdict="APPROVE",
                          summary="looks good")

    orch = _make_orch(db, runtime)
    _enqueue_parent_if_waiting(orch, "T-REV")

    qa_ids = _qa_children(db, "T-PAR")
    assert len(qa_ids) == 1
    qa = db.get_task(qa_ids[0])
    assert "Prior leg context" in qa.brief
    assert "Verdict:      APPROVE" in qa.brief

    cs2 = ChainState.deserialize(db.get_task("T-PAR").active_chain)
    assert cs2.step_index == 2

    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == qa.id

    advances = _chain_advances(db, "T-PAR")
    assert len(advances) == 1
    assert advances[0]["payload"]["spawned_child_id"] == qa.id
    assert advances[0]["payload"]["triggering_verdict"] == "APPROVE"


# ---------------------------------------------------------------------------
# (4) an ordinary NON-review leg with an omitted expectation still advances
# normally (do not broadly reinterpret None).
# ---------------------------------------------------------------------------
def test_non_review_leg_omitted_expectation_still_advances(runtime, db):
    from runtime.orchestrator.chain import ChainState
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    _seed_parent(db)
    # dev_agent (non-review, omitted expectation) -> qa_engineer.
    chain = ChainState(
        step_index=1,
        first_leg_expect_verdict=None,
        legs=[
            ChainLeg(agent="dev_agent", prompt="build", expect_verdict=None),
            ChainLeg(agent="qa_engineer", prompt="QA", expect_verdict="PASS"),
        ],
        step_audit_id=1,
    )
    db.update_task_active_chain("T-PAR", chain.serialize())
    _seed_completed_child(db, child_id="T-DEV", parent_id="T-PAR",
                          agent="dev_agent", verdict=None, summary="built")

    orch = _make_orch(db, runtime)
    _enqueue_parent_if_waiting(orch, "T-DEV")

    qa_ids = _qa_children(db, "T-PAR")
    assert len(qa_ids) == 1            # non-review leg advances normally
    assert _chain_advances(db, "T-PAR") != []
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == qa_ids[0]


# ---------------------------------------------------------------------------
# (5) duplicate/late terminal delivery after an omitted-reviewer rejection
# cannot create/queue QA or revive the cleared chain.
# ---------------------------------------------------------------------------
def test_late_terminal_after_omitted_reviewer_rejection_cannot_revive(runtime, db):
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    _seed_parent(db)
    db.update_task_active_chain("T-PAR", _omitted_review_chain(step_index=1).serialize())
    _seed_completed_child(db, child_id="T-REV", parent_id="T-PAR",
                          agent="code_reviewer", verdict="REQUEST_CHANGES",
                          summary="needs changes")

    orch = _make_orch(db, runtime)
    _enqueue_parent_if_waiting(orch, "T-REV")

    assert _qa_children(db, "T-PAR") == []
    assert db.get_task("T-PAR").active_chain is None
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == "T-PAR"

    # Late/duplicate terminal handling for the same reviewer.
    _enqueue_parent_if_waiting(orch, "T-REV")

    assert _qa_children(db, "T-PAR") == []
    assert db.get_task("T-PAR").active_chain is None
    assert _chain_advances(db, "T-PAR") == []
    for slug, tid in orch._queue.drain():
        assert tid == "T-PAR"          # only idempotent parent wake, never QA


# ---------------------------------------------------------------------------
# (6) validation rejects a NEW authored reviewer-with-downstream omission.
# ---------------------------------------------------------------------------
def _mk_workspace(runtime: OrgPaths, agent: str) -> None:
    (runtime.workspaces_dir / agent).mkdir(parents=True, exist_ok=True)


def test_validate_delegate_rejects_reviewer_then_leg_with_downstream_omission(runtime, db):
    from runtime.orchestrator.run_step import _validate_delegate

    for agent in ("dev_agent", "code_reviewer", "qa_engineer"):
        _mk_workspace(runtime, agent)

    orch = _make_orch(db, runtime)
    decision = NextStep(
        action="delegate", agent="dev_agent", prompt="build",
        then=[
            ChainLeg(agent="code_reviewer", prompt="review", expect_verdict=None),
            ChainLeg(agent="qa_engineer", prompt="qa", expect_verdict="PASS"),
        ],
    )
    err = _validate_delegate(orch, decision)
    assert err is not None
    assert "expect_verdict" in err


def test_validate_delegate_allows_reviewer_final_leg_omitted(runtime, db):
    """A code_reviewer FINAL leg with an omitted expectation is allowed — it
    already wakes chain-complete with no downstream leg to wrongly advance."""
    from runtime.orchestrator.run_step import _validate_delegate

    for agent in ("dev_agent", "code_reviewer"):
        _mk_workspace(runtime, agent)

    orch = _make_orch(db, runtime)
    decision = NextStep(
        action="delegate", agent="dev_agent", prompt="build",
        then=[
            ChainLeg(agent="code_reviewer", prompt="review", expect_verdict=None),
        ],
    )
    assert _validate_delegate(orch, decision) is None


def test_validate_delegate_rejects_first_leg_reviewer_with_downstream_omission(runtime, db):
    from runtime.orchestrator.run_step import _validate_delegate

    for agent in ("code_reviewer", "qa_engineer"):
        _mk_workspace(runtime, agent)

    orch = _make_orch(db, runtime)
    decision = NextStep(
        action="delegate", agent="code_reviewer", prompt="review", expect_verdict=None,
        then=[
            ChainLeg(agent="qa_engineer", prompt="qa", expect_verdict="PASS"),
        ],
    )
    err = _validate_delegate(orch, decision)
    assert err is not None
    assert "expect_verdict" in err
