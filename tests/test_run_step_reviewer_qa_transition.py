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


def _seed_agents(runtime: OrgPaths, names: tuple[str, ...]) -> None:
    """Write active AgentDef files so the live roster knows these names."""
    from tests.conftest import seed_test_agents
    seed_test_agents(runtime, names)


def _make_orch(db: Database, runtime: OrgPaths):
    from runtime.orchestrator.orchestrator import Orchestrator
    # THR-175: the reviewer_agents resolver validates names against the live
    # active-agent roster, so tests that touch reviewer identity need real
    # agent files (not just teams.yaml).
    _seed_agents(runtime, (
        "engineering_head", "product_manager", "dev_agent",
        "code_reviewer", "qa_engineer", "senior_dev", "payment_agent",
    ))
    orch = Orchestrator(
        db=db, settings=Settings(), paths=runtime, slug="test",
        teams=TeamsRegistry.load(runtime.root),
    )
    orch._queue = _SlugQueue()
    return orch


def _dev_review_qa_chain(*, step_index: int, step_audit_id: int = 1):
    from runtime.orchestrator.chain import ChainState
    return ChainState(
        step_index=step_index,
        first_leg_expect_verdict=None,
        legs=[
            ChainLeg(agent="code_reviewer", prompt="review the PR", expect_verdict="APPROVE"),
            ChainLeg(agent="qa_engineer", prompt="QA the PR", expect_verdict="PASS"),
        ],
        step_audit_id=step_audit_id,
    )


def _dev_review_qa_chain_omitted(*, step_index: int, step_audit_id: int = 1):
    """code_reviewer leg OMITS expect_verdict (the THR-175 hole)."""
    from runtime.orchestrator.chain import ChainState
    return ChainState(
        step_index=step_index,
        first_leg_expect_verdict=None,
        legs=[
            ChainLeg(agent="code_reviewer", prompt="review the PR", expect_verdict=None),
            ChainLeg(agent="qa_engineer", prompt="QA the PR", expect_verdict="PASS"),
        ],
        step_audit_id=step_audit_id,
    )


def _senior_dev_review_qa_chain_omitted(*, step_index: int, step_audit_id: int = 1):
    """senior_dev reviewer leg (tourism org) OMITS expect_verdict."""
    from runtime.orchestrator.chain import ChainState
    return ChainState(
        step_index=step_index,
        first_leg_expect_verdict=None,
        legs=[
            ChainLeg(agent="senior_dev", prompt="review the PR", expect_verdict=None),
            ChainLeg(agent="qa_engineer", prompt="QA the PR", expect_verdict="PASS"),
        ],
        step_audit_id=step_audit_id,
    )


def _non_reviewer_verdictless_chain(*, step_index: int, step_audit_id: int = 1):
    """A non-reviewer leg (senior_dev, NOT in default reviewer_agents) with
    omitted expectation and a downstream QA leg — ordinary semantics."""
    from runtime.orchestrator.chain import ChainState
    return ChainState(
        step_index=step_index,
        first_leg_expect_verdict=None,
        legs=[
            ChainLeg(agent="senior_dev", prompt="pair on the PR", expect_verdict=None),
            ChainLeg(agent="qa_engineer", prompt="QA the PR", expect_verdict="PASS"),
        ],
        step_audit_id=step_audit_id,
    )


def _set_reviewer_agents(db: Database, names: list[str]) -> None:
    import json as _json
    db.upsert_org_setting("reviewer_agents", _json.dumps(names))


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
    db.update_task_active_chain("T-PAR", _dev_review_qa_chain(step_index=1).serialize())
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
    db.update_task_active_chain("T-PAR", _dev_review_qa_chain(step_index=1).serialize())
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
    db.update_task_active_chain("T-PAR", _dev_review_qa_chain(step_index=1).serialize())
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
    db.update_task_active_chain("T-PAR", _dev_review_qa_chain(step_index=0).serialize())

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
    db.update_task_active_chain("T-PAR", _dev_review_qa_chain(step_index=1).serialize())
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


# ---------------------------------------------------------------------------
# THR-175: reviewer identity is the org-configured ``reviewer_agents`` setting,
# and a reviewer leg that OMITS expect_verdict must fail-closed at the real
# execution seam (never advance QA/downstream).
# ---------------------------------------------------------------------------


def test_reviewer_omitted_expectation_request_changes_spawns_no_qa(runtime, db):
    """A code_reviewer leg with OMITTED expect_verdict and a downstream QA leg
    returning REQUEST_CHANGES must NOT spawn/enqueue QA, must clear the chain,
    and must wake the parent exactly once (no chain_auto_advance audit)."""
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    _seed_parent(db)
    db.update_task_active_chain("T-PAR", _dev_review_qa_chain_omitted(step_index=1).serialize())
    _seed_completed_child(db, child_id="T-REV", parent_id="T-PAR",
                          agent="code_reviewer", verdict="REQUEST_CHANGES",
                          summary="needs changes")

    orch = _make_orch(db, runtime)
    _enqueue_parent_if_waiting(orch, "T-REV")

    assert _qa_children(db, "T-PAR") == []
    assert db.get_task("T-PAR").active_chain is None
    assert _chain_advances(db, "T-PAR") == []
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == "T-PAR"


def test_reviewer_omitted_expectation_missing_verdict_spawns_no_qa(runtime, db):
    """A code_reviewer leg with OMITTED expect_verdict reporting NO verdict
    must NOT spawn QA, must clear the chain, and wake the parent once."""
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    _seed_parent(db)
    db.update_task_active_chain("T-PAR", _dev_review_qa_chain_omitted(step_index=1).serialize())
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


def test_reviewer_omitted_expectation_approve_advances_qa_exactly_once(runtime, db):
    """A code_reviewer leg with OMITTED expect_verdict that returns an explicit
    APPROVE advances to exactly one QA child."""
    from runtime.orchestrator.chain import ChainState
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    _seed_parent(db)
    db.update_task_active_chain("T-PAR", _dev_review_qa_chain_omitted(step_index=1).serialize())
    _seed_completed_child(db, child_id="T-REV", parent_id="T-PAR",
                          agent="code_reviewer", verdict="APPROVE",
                          summary="looks good")

    orch = _make_orch(db, runtime)
    _enqueue_parent_if_waiting(orch, "T-REV")

    qa_ids = _qa_children(db, "T-PAR")
    assert len(qa_ids) == 1
    assert ChainState.deserialize(db.get_task("T-PAR").active_chain).step_index == 2
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == qa_ids[0]
    advances = _chain_advances(db, "T-PAR")
    assert len(advances) == 1


def test_tourism_senior_dev_omitted_expectation_request_changes_no_qa(runtime, db):
    """A tourism org whose reviewer_agents = ["senior_dev"] fails closed the
    SAME way: senior_dev omitted expectation + REQUEST_CHANGES never advances
    QA.  code_reviewer is NOT a reviewer in this org, so only senior_dev gates."""
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    _set_reviewer_agents(db, ["senior_dev"])
    _seed_parent(db)
    db.update_task_active_chain("T-PAR", _senior_dev_review_qa_chain_omitted(step_index=1).serialize())
    _seed_completed_child(db, child_id="T-REV", parent_id="T-PAR",
                          agent="senior_dev", verdict="REQUEST_CHANGES",
                          summary="needs changes")

    orch = _make_orch(db, runtime)
    _enqueue_parent_if_waiting(orch, "T-REV")

    assert _qa_children(db, "T-PAR") == []
    assert db.get_task("T-PAR").active_chain is None
    assert _chain_advances(db, "T-PAR") == []
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == "T-PAR"


def test_tourism_senior_dev_omitted_expectation_approve_advances_qa(runtime, db):
    """Explicit APPROVE from a configured senior_dev reviewer advances QA once."""
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    _set_reviewer_agents(db, ["senior_dev"])
    _seed_parent(db)
    db.update_task_active_chain("T-PAR", _senior_dev_review_qa_chain_omitted(step_index=1).serialize())
    _seed_completed_child(db, child_id="T-REV", parent_id="T-PAR",
                          agent="senior_dev", verdict="APPROVE",
                          summary="approved")

    orch = _make_orch(db, runtime)
    _enqueue_parent_if_waiting(orch, "T-REV")

    qa_ids = _qa_children(db, "T-PAR")
    assert len(qa_ids) == 1
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == qa_ids[0]


def test_verdictless_non_review_chain_still_advances(runtime, db):
    """A NON-reviewer leg (senior_dev, not in default reviewer_agents) with
    omitted expectation and a downstream leg still advances on ANY verdict —
    ordinary verdict-less chain semantics are preserved, not generalized."""
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    _seed_parent(db)
    db.update_task_active_chain("T-PAR", _non_reviewer_verdictless_chain(step_index=1).serialize())
    _seed_completed_child(db, child_id="T-SD", parent_id="T-PAR",
                          agent="senior_dev", verdict="REQUEST_CHANGES",
                          summary="notes for the next leg")

    orch = _make_orch(db, runtime)
    _enqueue_parent_if_waiting(orch, "T-SD")

    # senior_dev is NOT a reviewer → the leg advances to QA despite a
    # non-approve verdict (ordinary semantics).
    qa_ids = _qa_children(db, "T-PAR")
    assert len(qa_ids) == 1
    assert db.get_task(qa_ids[0]).assigned_agent == "qa_engineer"
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == qa_ids[0]


def test_code_reviewer_not_reviewer_when_setting_overridden(runtime, db):
    """When reviewer_agents is overridden to NOT include code_reviewer, a
    code_reviewer leg with omitted expectation advances (it is no longer a
    reviewer in this org)."""
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    _set_reviewer_agents(db, ["senior_dev"])  # code_reviewer NOT a reviewer now
    _seed_parent(db)
    db.update_task_active_chain("T-PAR", _dev_review_qa_chain_omitted(step_index=1).serialize())
    _seed_completed_child(db, child_id="T-REV", parent_id="T-PAR",
                          agent="code_reviewer", verdict="REQUEST_CHANGES",
                          summary="notes")

    orch = _make_orch(db, runtime)
    _enqueue_parent_if_waiting(orch, "T-REV")

    qa_ids = _qa_children(db, "T-PAR")
    assert len(qa_ids) == 1  # advanced — code_reviewer is not a reviewer here


# ---------------------------------------------------------------------------
# THR-175 fix-forward: reviewer_agents must validate membership in the real
# live agent roster wherever config can seed/backfill/persist it, and an
# unknown persisted value must resolve fail-closed to DEFAULT_REVIEWER_AGENTS.
# ---------------------------------------------------------------------------


def test_fired_sentinel_unknown_config_backfill_does_not_persist_ghost(runtime, db):
    """The reviewer's HIGH finding: with the .org_settings_seeded sentinel
    already fired and config.yaml ``reviewer_agents: [ghost_agent]``, the
    backfill must NOT persist the unknown name. It persists the code default
    ([code_reviewer]), so a code_reviewer reviewer leg with omitted
    expectation returning REQUEST_CHANGES fails closed (no QA child, no
    queue, no chain_auto_advance audit; active chain clears; parent woken)."""
    import json

    from runtime.orchestrator.org_config import (
        _ORG_SETTINGS_SEED_SENTINEL,
        backfill_reviewer_agents_setting,
    )
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    # Fired sentinel + config.yaml carrying an unknown reviewer agent.
    (runtime.root / _ORG_SETTINGS_SEED_SENTINEL).write_text("")
    runtime.org_config_path.write_text("reviewer_agents: [ghost_agent]\n")

    # Real roster — ghost_agent is genuinely absent.
    _seed_agents(runtime, (
        "engineering_head", "dev_agent", "code_reviewer", "qa_engineer", "senior_dev",
    ))

    # Bootstrap backfill (the one-time 5-knob seed is a sentinel no-op here).
    value = backfill_reviewer_agents_setting(runtime, db)
    assert json.loads(value) == ["code_reviewer"]
    assert json.loads(db.get_org_setting("reviewer_agents")) == ["code_reviewer"]

    # code_reviewer reviewer leg (omitted expectation) completing REQUEST_CHANGES.
    _seed_parent(db)
    db.update_task_active_chain("T-PAR", _dev_review_qa_chain_omitted(step_index=1).serialize())
    _seed_completed_child(db, child_id="T-REV", parent_id="T-PAR",
                          agent="code_reviewer", verdict="REQUEST_CHANGES",
                          summary="needs changes")

    orch = _make_orch(db, runtime)
    _enqueue_parent_if_waiting(orch, "T-REV")

    assert _qa_children(db, "T-PAR") == []
    assert db.get_task("T-PAR").active_chain is None
    assert _chain_advances(db, "T-PAR") == []
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == "T-PAR"


def test_persisted_unknown_reviewer_agents_resolves_to_default_fail_closed(runtime, db):
    """An ALREADY-persisted unknown reviewer_agents value (e.g. from a config
    persisted before this fix) resolves fail-closed to DEFAULT_REVIEWER_AGENTS
    — code_reviewer is never silently treated as a non-reviewer, so a
    REQUEST_CHANGES from a code_reviewer leg cannot bypass the QA gate."""
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    _set_reviewer_agents(db, ["ghost_agent"])  # already-persisted unknown value

    _seed_parent(db)
    db.update_task_active_chain("T-PAR", _dev_review_qa_chain_omitted(step_index=1).serialize())
    _seed_completed_child(db, child_id="T-REV", parent_id="T-PAR",
                          agent="code_reviewer", verdict="REQUEST_CHANGES",
                          summary="needs changes")

    orch = _make_orch(db, runtime)  # seeds the real roster (code_reviewer, ...)
    _enqueue_parent_if_waiting(orch, "T-REV")

    assert _qa_children(db, "T-PAR") == []
    assert db.get_task("T-PAR").active_chain is None
    assert _chain_advances(db, "T-PAR") == []
    assert orch._queue.qsize() == 1
    slug, tid = orch._queue.get_nowait()
    assert tid == "T-PAR"
