"""Focused regression tests locking the reviewer -> QA production transition.

Historical cases TASK-5148 and TASK-5171 saw `qa_engineer` auto-dispatched
after `code_reviewer` returned REQUEST_CHANGES. This file pins the corrected
transition by driving the REAL production seam — `_enqueue_parent_if_waiting`
against a real ``Database`` + ``Orchestrator`` — NOT the pure
``compute_advance_action`` helper, and NOT a test-double of an obsolete API.

The stored engineering chain convention under test is:

    dev (ungated, first leg)
        -> code_reviewer (expect_verdict=APPROVE)
        -> qa_engineer   (expect_verdict=PASS)

Durable state locked by each test: child ids/count, active_chain, queue
entries, and chain_auto_advance audit rows.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.infrastructure.database import Database
from runtime.models import BlockKind, ChainLeg, TaskRecord, TaskStatus
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


def _dev_review_qa_chain(*, step_index: int, step_audit_id: int = 1,
                         in_flight_child_id: str | None = None):
    from runtime.orchestrator.chain import ChainState
    return ChainState(
        step_index=step_index,
        first_leg_expect_verdict=None,
        legs=[
            ChainLeg(agent="code_reviewer", prompt="review the PR", expect_verdict="APPROVE"),
            ChainLeg(agent="qa_engineer", prompt="QA the PR", expect_verdict="PASS"),
        ],
        step_audit_id=step_audit_id,
        in_flight_child_id=in_flight_child_id,
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


def test_reviewer_approve_advances_to_exactly_one_qa_child(runtime, db):
    """(1) reviewer APPROVE matching expect_verdict=APPROVE advances to exactly
    one qa_engineer child, with prior-leg context, advanced chain state, one
    queue entry for QA, and one chain_auto_advance audit row."""
    from runtime.orchestrator.chain import ChainState
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    _seed_parent(db)
    db.update_task_active_chain("T-PAR", _dev_review_qa_chain(step_index=1, in_flight_child_id="T-REV").serialize())
    _seed_completed_child(db, child_id="T-REV", parent_id="T-PAR",
                          agent="code_reviewer", verdict="APPROVE",
                          summary="looks good")

    orch = _make_orch(db, runtime)
    _enqueue_parent_if_waiting(orch, "T-REV")

    # Exactly one QA child spawned, typed subtask, under the parent.
    qa_ids = _qa_children(db, "T-PAR")
    assert len(qa_ids) == 1, f"expected exactly one QA child, got {qa_ids}"
    qa = db.get_task(qa_ids[0])
    assert qa.task_type == "subtask"
    assert qa.parent_task_id == "T-PAR"

    # Prior-leg context carries the reviewer's identity and APPROVE verdict.
    assert "Prior leg context" in qa.brief
    assert "Verdict:      APPROVE" in qa.brief
    assert "code_reviewer" in qa.brief

    # Chain advanced to step_index=2 (qa leg in flight).
    cs2 = ChainState.deserialize(db.get_task("T-PAR").active_chain)
    assert cs2.step_index == 2
    assert cs2.in_flight_child_id == qa.id  # durable ownership rotated atomically

    # Exactly one queue entry, and it is the QA child.
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == qa.id

    # One chain_auto_advance audit row pointing at the QA child.
    advances = _chain_advances(db, "T-PAR")
    assert len(advances) == 1
    payload = advances[0]["payload"]
    assert payload["spawned_child_id"] == qa.id
    assert payload["triggering_child_id"] == "T-REV"
    assert payload["triggering_verdict"] == "APPROVE"
    assert payload["leg_index"] == 2


def test_reviewer_request_changes_spawns_no_qa_clears_chain_wakes_parent(runtime, db):
    """(2a) reviewer REQUEST_CHANGES must NOT spawn/enqueue a QA child, must
    clear active_chain, and must wake the parent exactly once."""
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    _seed_parent(db)
    db.update_task_active_chain("T-PAR", _dev_review_qa_chain(step_index=1, in_flight_child_id="T-REV").serialize())
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


def test_reviewer_missing_verdict_spawns_no_qa_clears_chain_wakes_parent(runtime, db):
    """(2b) a second mismatch shape — reviewer reports no verdict at all —
    behaves identically: no QA, chain cleared, parent woken once."""
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    _seed_parent(db)
    db.update_task_active_chain("T-PAR", _dev_review_qa_chain(step_index=1, in_flight_child_id="T-REV").serialize())
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


def test_full_dev_then_reviewer_approve_reaches_qa_exactly_once(runtime, db):
    """(3) normal dev completion (ungated) -> fresh reviewer APPROVE reaches
    QA exactly once, with no duplicate dispatch."""
    from runtime.orchestrator.chain import ChainState
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    _seed_parent(db)
    db.update_task_active_chain("T-PAR", _dev_review_qa_chain(step_index=0, in_flight_child_id="T-DEV").serialize())

    # --- dev (first leg, ungated) completes ---
    _seed_completed_child(db, child_id="T-DEV", parent_id="T-PAR",
                          agent="dev_agent", verdict=None, summary="built PR")

    orch = _make_orch(db, runtime)
    _enqueue_parent_if_waiting(orch, "T-DEV")

    # Reviewer spawned, chain at step_index=1, one queue entry for the reviewer.
    reviewer_ids = [c for c in db.get_children("T-PAR")
                    if db.get_task(c).assigned_agent == "code_reviewer"]
    assert len(reviewer_ids) == 1
    reviewer_id = reviewer_ids[0]
    assert ChainState.deserialize(db.get_task("T-PAR").active_chain).step_index == 1
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == reviewer_id

    # --- reviewer approves ---
    db.update_task(reviewer_id, status=TaskStatus.COMPLETED, note="approved")
    db.insert_task_result(
        task_id=reviewer_id, agent="code_reviewer", session_id="s",
        status="completed", confidence_score=90,
        output_summary="approved", verdict="APPROVE",
    )
    _enqueue_parent_if_waiting(orch, reviewer_id)

    # QA spawned exactly once.
    qa_ids = _qa_children(db, "T-PAR")
    assert len(qa_ids) == 1
    assert db.get_task(qa_ids[0]).task_type == "subtask"
    assert ChainState.deserialize(db.get_task("T-PAR").active_chain).step_index == 2
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == qa_ids[0]

    # Exactly two chain_auto_advance rows: dev->reviewer, reviewer->qa.
    advances = _chain_advances(db, "T-PAR")
    assert [a["payload"]["spawned_child_id"] for a in advances] == [reviewer_id, qa_ids[0]]


def test_late_terminal_handling_after_mismatch_cannot_spawn_qa(runtime, db):
    """(3) repeated/late terminal handling after a mismatch cannot enqueue QA
    or mutate the already-cleared chain."""
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    _seed_parent(db)
    db.update_task_active_chain("T-PAR", _dev_review_qa_chain(step_index=1, in_flight_child_id="T-REV").serialize())
    _seed_completed_child(db, child_id="T-REV", parent_id="T-PAR",
                          agent="code_reviewer", verdict="REQUEST_CHANGES",
                          summary="needs changes")

    orch = _make_orch(db, runtime)
    _enqueue_parent_if_waiting(orch, "T-REV")

    # First pass: chain cleared, parent woken once, no QA.
    assert _qa_children(db, "T-PAR") == []
    assert db.get_task("T-PAR").active_chain is None
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == "T-PAR"

    # Late/duplicate terminal handling for the same reviewer.
    _enqueue_parent_if_waiting(orch, "T-REV")

    # Still no QA child, chain still cleared, no auto-advance audit.
    assert _qa_children(db, "T-PAR") == []
    assert db.get_task("T-PAR").active_chain is None
    assert _chain_advances(db, "T-PAR") == []
    # Whatever is enqueued is never a QA child — only the idempotent parent wake.
    for slug, tid in orch._queue.drain():
        assert tid == "T-PAR"


def test_reviewer_approve_duplicate_delivery_is_noop_while_qa_pending(runtime, db):
    """(4) at-most-once terminal consumption: after an explicit reviewer
    APPROVE advances to QA, a sequential DUPLICATE delivery of the SAME
    reviewer terminal event — while QA is still pending — must be a NO-OP.

    It must not reinterpret the old reviewer report as if it were the current
    QA leg (which reaches verdict_mismatch and clears active_chain, later
    letting QA bypass its PASS gate). After the duplicate: exactly one QA
    child, one QA queue item, one chain_auto_advance audit, active_chain still
    on the QA leg, and no parent wake/queue or new audit. QA PASS then
    consumes through the normal final-leg path, clearing the chain and waking
    the parent exactly once."""
    from runtime.orchestrator.chain import ChainState
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    _seed_parent(db)
    db.update_task_active_chain("T-PAR", _dev_review_qa_chain(step_index=1, in_flight_child_id="T-REV").serialize())
    _seed_completed_child(db, child_id="T-REV", parent_id="T-PAR",
                          agent="code_reviewer", verdict="APPROVE",
                          summary="looks good")

    orch = _make_orch(db, runtime)

    # First delivery: advances to QA exactly once.
    _enqueue_parent_if_waiting(orch, "T-REV")
    qa_ids = _qa_children(db, "T-PAR")
    assert len(qa_ids) == 1
    qa = db.get_task(qa_ids[0])
    assert qa.status == TaskStatus.PENDING
    assert ChainState.deserialize(db.get_task("T-PAR").active_chain).step_index == 2
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == qa.id
    assert len(_chain_advances(db, "T-PAR")) == 1

    # Duplicate delivery of the SAME reviewer terminal event while QA pending.
    _enqueue_parent_if_waiting(orch, "T-REV")

    # No-op: still exactly one QA child, active_chain STILL on the QA leg,
    # no new audit, no parent wake/queue.
    assert _qa_children(db, "T-PAR") == qa_ids
    active_chain_after_dup = db.get_task("T-PAR").active_chain
    assert active_chain_after_dup is not None, (
        "duplicate reviewer delivery incorrectly cleared active_chain"
    )
    assert ChainState.deserialize(active_chain_after_dup).step_index == 2
    assert len(_chain_advances(db, "T-PAR")) == 1
    assert orch._queue.qsize() == 0  # duplicate produced no parent wake/queue

    # Then QA PASS is consumed through the normal final-leg path.
    db.update_task(qa.id, status=TaskStatus.COMPLETED, note="passed")
    db.insert_task_result(
        task_id=qa.id, agent="qa_engineer", session_id="s",
        status="completed", confidence_score=95,
        output_summary="passed", verdict="PASS",
    )
    _enqueue_parent_if_waiting(orch, qa.id)

    # Final-leg path clears the chain and wakes the parent exactly once.
    assert db.get_task("T-PAR").active_chain is None
    assert len(_chain_advances(db, "T-PAR")) == 1  # still only reviewer->qa
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == "T-PAR"


def test_late_reviewer_terminal_cannot_mutate_completed_chain(runtime, db):
    """(5) an OLD reviewer terminal event delivered after the chain has fully
    completed (QA PASS consumed, active_chain cleared) must not mutate the
    completed terminal state — no re-spawned QA, no re-cleared/revived chain,
    no new auto-advance audit. Only the idempotent parent wake is possible."""
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    _seed_parent(db)
    db.update_task_active_chain("T-PAR", _dev_review_qa_chain(step_index=1, in_flight_child_id="T-REV").serialize())
    _seed_completed_child(db, child_id="T-REV", parent_id="T-PAR",
                          agent="code_reviewer", verdict="APPROVE",
                          summary="looks good")

    orch = _make_orch(db, runtime)
    _enqueue_parent_if_waiting(orch, "T-REV")  # -> QA
    qa = db.get_task(_qa_children(db, "T-PAR")[0])
    orch._queue.drain()

    # QA completes with PASS -> chain cleared, parent woken once.
    db.update_task(qa.id, status=TaskStatus.COMPLETED, note="passed")
    db.insert_task_result(
        task_id=qa.id, agent="qa_engineer", session_id="s",
        status="completed", confidence_score=95,
        output_summary="passed", verdict="PASS",
    )
    _enqueue_parent_if_waiting(orch, qa.id)
    assert db.get_task("T-PAR").active_chain is None
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == "T-PAR"

    # Late reviewer terminal delivery: cannot mutate the completed chain.
    _enqueue_parent_if_waiting(orch, "T-REV")

    assert db.get_task("T-PAR").active_chain is None  # still cleared
    assert len(_qa_children(db, "T-PAR")) == 1          # still exactly one QA
    assert len(_chain_advances(db, "T-PAR")) == 1       # no new auto-advance
    # Whatever is enqueued is never a QA child — only the idempotent parent wake.
    for slug, tid in orch._queue.drain():
        assert tid == "T-PAR"


def test_reviewer_replay_after_qa_terminal_but_undelivered_is_noop(runtime, db):
    """(6) The REVISE round-3 structural finding. A terminal report may be
    consumed only while its child's id EXACTLY matches the chain's durable
    ``in_flight_child_id``. Scenario: reviewer APPROVE advances to QA; QA then
    writes its terminal PASS/report (COMPLETED) but is NOT yet delivered; a
    replay of the OLD reviewer terminal (a valid recovery/duplicate ordering)
    must be a NO-OP — it must not reinterpret the reviewer against the QA leg's
    PASS expectation (which reaches verdict_mismatch, clears active_chain, and
    lets QA bypass its gate)."""
    from runtime.orchestrator.chain import ChainState
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    _seed_parent(db)
    db.update_task_active_chain(
        "T-PAR", _dev_review_qa_chain(step_index=1, in_flight_child_id="T-REV").serialize()
    )
    _seed_completed_child(db, child_id="T-REV", parent_id="T-PAR",
                          agent="code_reviewer", verdict="APPROVE",
                          summary="looks good")

    orch = _make_orch(db, runtime)

    # reviewer APPROVE -> advance to QA exactly once.
    _enqueue_parent_if_waiting(orch, "T-REV")
    qa_ids = _qa_children(db, "T-PAR")
    assert len(qa_ids) == 1
    qa = db.get_task(qa_ids[0])
    orch._queue.drain()

    # QA writes terminal PASS/report but is NOT delivered yet.
    db.update_task(qa.id, status=TaskStatus.COMPLETED, note="passed")
    db.insert_task_result(
        task_id=qa.id, agent="qa_engineer", session_id="s",
        status="completed", confidence_score=95,
        output_summary="passed", verdict="PASS",
    )

    # Replay the OLD reviewer terminal BEFORE QA delivery.
    _enqueue_parent_if_waiting(orch, "T-REV")

    # No-op: chain remains owned by QA, not cleared, no extra child/queue/audit.
    active_chain_after_replay = db.get_task("T-PAR").active_chain
    assert active_chain_after_replay is not None, (
        "old reviewer replay cleared the QA-owned chain"
    )
    cs = ChainState.deserialize(active_chain_after_replay)
    assert cs.step_index == 2
    assert cs.in_flight_child_id == qa.id
    assert _qa_children(db, "T-PAR") == qa_ids       # no extra QA child
    assert len(_chain_advances(db, "T-PAR")) == 1    # no extra advance audit
    assert orch._queue.qsize() == 0                  # no parent wake from replay

    # Then QA delivery runs its PASS gate and wakes/clears exactly once.
    _enqueue_parent_if_waiting(orch, qa.id)
    assert db.get_task("T-PAR").active_chain is None
    assert len(_chain_advances(db, "T-PAR")) == 1
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == "T-PAR"


def test_duplicate_qa_delivery_after_final_completion_is_noop(runtime, db):
    """(7) after QA PASS consumes the final leg (chain cleared, parent woken),
    a duplicate QA terminal delivery must not re-spawn, re-clear, or re-wake
    anything beyond the idempotent parent wake."""
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    _seed_parent(db)
    db.update_task_active_chain(
        "T-PAR", _dev_review_qa_chain(step_index=2, in_flight_child_id="T-QA").serialize()
    )
    _seed_completed_child(db, child_id="T-QA", parent_id="T-PAR",
                          agent="qa_engineer", verdict="PASS", summary="passed")

    orch = _make_orch(db, runtime)
    _enqueue_parent_if_waiting(orch, "T-QA")
    assert db.get_task("T-PAR").active_chain is None
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == "T-PAR"

    # Duplicate QA delivery: chain already cleared -> sibling-check path; only
    # an idempotent parent wake, never a new QA child or advance audit.
    _enqueue_parent_if_waiting(orch, "T-QA")
    assert db.get_task("T-PAR").active_chain is None
    assert _qa_children(db, "T-PAR") == ["T-QA"]   # still just the one QA
    assert _chain_advances(db, "T-PAR") == []
    for slug, tid in orch._queue.drain():
        assert tid == "T-PAR"


def test_atomic_advance_failure_preserves_prior_ownership(runtime, db, monkeypatch):
    """(8) atomic ownership rotation: an injected try_advance_chain transaction
    failure leaves the prior chain id/state intact and spawns no child, audit,
    or queue entry for the would-be next leg."""
    from runtime.orchestrator.chain import ChainState
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    _seed_parent(db)
    db.update_task_active_chain(
        "T-PAR", _dev_review_qa_chain(step_index=1, in_flight_child_id="T-REV").serialize()
    )
    _seed_completed_child(db, child_id="T-REV", parent_id="T-PAR",
                          agent="code_reviewer", verdict="APPROVE",
                          summary="looks good")

    orch = _make_orch(db, runtime)

    def _fail_advance(*args, **kwargs):
        return False

    monkeypatch.setattr(orch._db, "try_advance_chain", _fail_advance)
    _enqueue_parent_if_waiting(orch, "T-REV")

    # Prior ownership preserved, no child/audit/queue for a next leg.
    cs = ChainState.deserialize(db.get_task("T-PAR").active_chain)
    assert cs.step_index == 1
    assert cs.in_flight_child_id == "T-REV"
    assert _qa_children(db, "T-PAR") == []
    assert _chain_advances(db, "T-PAR") == []
    for slug, tid in orch._queue.drain():
        assert tid == "T-PAR"  # only the parent wake, never a would-be child


def test_same_agent_consecutive_legs_use_exact_ownership(runtime, db):
    """(9) two consecutive legs of the SAME agent are distinguished by the
    exact in_flight_child_id, never agent equality. A stale terminal from the
    first same-agent leg is a no-op against the second."""
    from runtime.orchestrator.chain import ChainState
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    _seed_parent(db)
    chain = ChainState(
        step_index=1,
        first_leg_expect_verdict=None,
        legs=[
            ChainLeg(agent="dev_agent", prompt="build part 1", expect_verdict=None),
            ChainLeg(agent="dev_agent", prompt="build part 2", expect_verdict=None),
        ],
        step_audit_id=1,
        in_flight_child_id="T-DEV1",
    )
    db.update_task_active_chain("T-PAR", chain.serialize())
    _seed_completed_child(db, child_id="T-DEV1", parent_id="T-PAR",
                          agent="dev_agent", verdict=None, summary="part 1")

    orch = _make_orch(db, runtime)
    _enqueue_parent_if_waiting(orch, "T-DEV1")

    dev2_ids = [c for c in db.get_children("T-PAR") if c != "T-DEV1"]
    assert len(dev2_ids) == 1
    dev2 = db.get_task(dev2_ids[0])
    assert dev2.assigned_agent == "dev_agent"
    cs = ChainState.deserialize(db.get_task("T-PAR").active_chain)
    assert cs.step_index == 2
    assert cs.in_flight_child_id == dev2.id
    orch._queue.drain()

    # Replay the FIRST dev_agent terminal: no-op despite the same agent.
    _enqueue_parent_if_waiting(orch, "T-DEV1")
    assert db.get_task("T-PAR").active_chain is not None
    assert ChainState.deserialize(db.get_task("T-PAR").active_chain).in_flight_child_id == dev2.id
    assert len(db.get_children("T-PAR")) == 2   # only dev1 + dev2
    assert len(_chain_advances(db, "T-PAR")) == 1
    assert orch._queue.qsize() == 0


def test_legacy_chain_missing_in_flight_child_id_ambiguous_fails_closed(runtime, db):
    """(10) a legacy active_chain payload WITHOUT in_flight_child_id, with all
    siblings terminal (ownership ambiguous), fails closed: clears the chain and
    wakes the parent with NO downstream spawn. It never infers the current leg
    from sibling terminal state (which misclassifies an old-leg replay as the
    current leg once the next leg is also terminal)."""
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    _seed_parent(db)
    # Legacy chain (no ownership marker) at the reviewer leg.
    db.update_task_active_chain("T-PAR", _dev_review_qa_chain(step_index=1).serialize())
    # reviewer COMPLETED + a later QA child already COMPLETED (replay ordering).
    _seed_completed_child(db, child_id="T-REV", parent_id="T-PAR",
                          agent="code_reviewer", verdict="APPROVE",
                          summary="looks good")
    _seed_completed_child(db, child_id="T-QA", parent_id="T-PAR",
                          agent="qa_engineer", verdict="PASS", summary="passed")

    orch = _make_orch(db, runtime)
    _enqueue_parent_if_waiting(orch, "T-REV")

    # Fail closed: chain cleared, no NEW QA child, no advance audit, one wake.
    assert db.get_task("T-PAR").active_chain is None
    assert _qa_children(db, "T-PAR") == ["T-QA"]   # no NEW QA spawned
    assert _chain_advances(db, "T-PAR") == []
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == "T-PAR"


def test_legacy_chain_missing_in_flight_child_id_later_leg_in_flight_is_noop(runtime, db):
    """(11) a legacy active_chain payload WITHOUT in_flight_child_id, with a
    LATER leg still in flight (non-terminal), treats the completed child as a
    stale duplicate — no-op, never clearing the chain while the later leg runs."""
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    _seed_parent(db)
    db.update_task_active_chain("T-PAR", _dev_review_qa_chain(step_index=1).serialize())
    _seed_completed_child(db, child_id="T-REV", parent_id="T-PAR",
                          agent="code_reviewer", verdict="APPROVE",
                          summary="looks good")
    # A later QA leg is still pending (in flight).
    db.insert_task(TaskRecord(
        id="T-QA", brief="QA", assigned_agent="qa_engineer",
        parent_task_id="T-PAR", task_type="subtask",
    ))
    db.update_task("T-QA", status=TaskStatus.PENDING)

    orch = _make_orch(db, runtime)
    _enqueue_parent_if_waiting(orch, "T-REV")

    # No-op: chain still present, no clear, no spawn, no queue, no audit.
    assert db.get_task("T-PAR").active_chain is not None
    assert _qa_children(db, "T-PAR") == ["T-QA"]
    assert _chain_advances(db, "T-PAR") == []
    assert orch._queue.qsize() == 0
