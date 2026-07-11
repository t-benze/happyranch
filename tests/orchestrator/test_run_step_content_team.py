"""Unit tests for the Content Team MVP flow through run_step.

Tests the three paths defined in the spec:
  - PASS: CM → writer → QA(PASS) → CM done → COMPLETED
  - REVISE: CM → writer → QA(REVISE) → CM re-delegates writer → writer → QA(PASS) → done
  - REJECT: CM → writer → QA(REJECT) → CM escalate → escalated
"""
from __future__ import annotations

import asyncio

import dataclasses

from runtime.config import Settings
from runtime.infrastructure.database import Database
from runtime.models import NextStep, TaskRecord, TaskStatus
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.org_config import OrgConfig, load_org_config
from runtime.orchestrator.orchestrator import Orchestrator
from tests.orchestrator.conftest import ScriptedRunAgent, run_task_to_completion


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_orch(paths: OrgPaths, db: Database) -> Orchestrator:
    """Build an Orchestrator with a real async queue (needed by _enqueue_parent_if_waiting)."""
    from runtime.orchestrator.teams import TeamsRegistry

    class _SlugQueue:
        """Adapter so put_nowait(slug, task_id) works against a stdlib asyncio.Queue."""
        def __init__(self) -> None:
            self._q: asyncio.Queue = asyncio.Queue()
        def put_nowait(self, slug: str, task_id: str) -> None:
            self._q.put_nowait((slug, task_id))

    orch = Orchestrator(
        db=db,
        settings=Settings(max_orchestration_steps=15),
        paths=paths,
        slug="test",
        teams=TeamsRegistry.load(paths.root),
    )
    orch._queue = _SlugQueue()
    return orch


def _seed_workspaces(paths: OrgPaths) -> None:
    """Create the minimal workspace directories that run_step checks exist."""
    for agent in ("content_manager", "content_writer", "content_qa"):
        (paths.workspaces_dir / agent).mkdir(parents=True, exist_ok=True)


def _seed_task(db: Database, task_id: str = "TASK-C1") -> str:
    task = TaskRecord(
        id=task_id,
        brief="Write Macau visa guide",
        team="content",
        assigned_agent="content_manager",
    )
    db.insert_task(task)
    return task_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_pass_path_completes_task(paths: OrgPaths, db: Database, monkeypatch) -> None:
    """Happy path: CM → writer → QA(PASS) → CM done → task COMPLETED."""
    _seed_workspaces(paths)
    orch = _make_orch(paths, db)
    tid = _seed_task(db)

    scripted = ScriptedRunAgent()
    # CM step 1: delegate to content_writer
    scripted.enqueue(
        "content_manager",
        decision=NextStep(action="delegate", agent="content_writer", prompt="write the guide"),
        summary="delegating to writer",
    )
    # Writer step: produces draft, completes
    scripted.enqueue(
        "content_writer",
        summary="draft.md written",
        output_dir=f"output/{tid}",
    )
    # CM step 2: delegate to content_qa
    scripted.enqueue(
        "content_manager",
        decision=NextStep(action="delegate", agent="content_qa", prompt="review the draft"),
        summary="delegating to QA",
    )
    # QA step: PASS
    scripted.enqueue(
        "content_qa",
        summary="VERDICT: PASS — draft is accurate and well-structured.",
    )
    # CM step 3: done
    scripted.enqueue(
        "content_manager",
        decision=NextStep(action="done", summary="content approved and ready"),
        summary="content approved",
    )
    monkeypatch.setattr(orch, "_run_agent", scripted)

    run_task_to_completion(orch, tid, max_steps=15)

    task = db.get_task(tid)
    assert task is not None
    assert task.status == TaskStatus.COMPLETED, f"expected COMPLETED, got {task.status} (note={task.note!r})"


def test_revise_path_bumps_revision_count(paths: OrgPaths, db: Database, monkeypatch) -> None:
    """REVISE path: one QA rejection cycle; final PASS; revision_count == 1.

    The cycle is W1 → Q1 → W2 (revision) → Q2 (re-review). Only the W2
    re-delegation is a revision — the Q2 re-review must not also bump the
    counter (regression guard for the over-counting bug fixed alongside
    this test)."""
    _seed_workspaces(paths)
    orch = _make_orch(paths, db)
    tid = _seed_task(db)

    scripted = ScriptedRunAgent()
    # CM step 1: delegate to writer (first time)
    scripted.enqueue(
        "content_manager",
        decision=NextStep(action="delegate", agent="content_writer", prompt="write the guide"),
        summary="delegating to writer",
    )
    # Writer: v1 draft
    scripted.enqueue("content_writer", summary="v1 draft complete")
    # CM step 2: delegate to QA
    scripted.enqueue(
        "content_manager",
        decision=NextStep(action="delegate", agent="content_qa", prompt="review v1"),
        summary="delegating to QA",
    )
    # QA: REVISE
    scripted.enqueue("content_qa", summary="VERDICT: REVISE — section 3 is unclear.")
    # CM step 3: re-delegate to writer (revision)
    scripted.enqueue(
        "content_manager",
        decision=NextStep(action="delegate", agent="content_writer", prompt="revise section 3"),
        summary="requesting revision",
    )
    # Writer: v2 draft
    scripted.enqueue("content_writer", summary="v2 draft complete — section 3 rewritten")
    # CM step 4: delegate to QA again
    scripted.enqueue(
        "content_manager",
        decision=NextStep(action="delegate", agent="content_qa", prompt="review v2"),
        summary="delegating to QA for v2",
    )
    # QA: PASS
    scripted.enqueue("content_qa", summary="VERDICT: PASS — all issues resolved.")
    # CM step 5: done
    scripted.enqueue(
        "content_manager",
        decision=NextStep(action="done", summary="revision approved"),
        summary="content approved after revision",
    )
    monkeypatch.setattr(orch, "_run_agent", scripted)

    run_task_to_completion(orch, tid, max_steps=20)

    task = db.get_task(tid)
    assert task is not None
    assert task.status == TaskStatus.COMPLETED, f"expected COMPLETED, got {task.status} (note={task.note!r})"
    assert task.revision_count == 1, (
        f"expected revision_count == 1 after one REVISE cycle "
        f"(only the worker re-delegation should bump; QA re-review must not), "
        f"got {task.revision_count}"
    )


def test_reject_path_escalates(paths: OrgPaths, db: Database, monkeypatch) -> None:
    """REJECT path: QA rejects → CM escalates → task ends escalated."""
    _seed_workspaces(paths)
    orch = _make_orch(paths, db)
    tid = _seed_task(db)

    scripted = ScriptedRunAgent()
    # CM step 1: delegate to writer
    scripted.enqueue(
        "content_manager",
        decision=NextStep(action="delegate", agent="content_writer", prompt="write the guide"),
        summary="delegating to writer",
    )
    # Writer: draft
    scripted.enqueue("content_writer", summary="draft complete")
    # CM step 2: delegate to QA
    scripted.enqueue(
        "content_manager",
        decision=NextStep(action="delegate", agent="content_qa", prompt="review draft"),
        summary="delegating to QA",
    )
    # QA: REJECT
    scripted.enqueue("content_qa", summary="VERDICT: REJECT — politically sensitive content detected.")
    # CM step 3: escalate to founder
    scripted.enqueue(
        "content_manager",
        decision=NextStep(
            action="escalate",
            reason="content contains politically sensitive material — needs founder review",
            summary="escalating to founder",
        ),
        summary="escalating",
    )
    monkeypatch.setattr(orch, "_run_agent", scripted)

    run_task_to_completion(orch, tid, max_steps=15)

    task = db.get_task(tid)
    assert task is not None
    # Path B: escalation is the top-level ESCALATED status; block_kind cleared.
    assert task.status == TaskStatus.ESCALATED, f"expected ESCALATED, got {task.status}"
    assert task.block_kind is None, (
        f"expected block_kind cleared on escalate, got {task.block_kind!r}"
    )


# ---------------------------------------------------------------------------
# THR-026 seq33: per-slice revise-round budget
# ---------------------------------------------------------------------------


def test_revise_cap_trips_deliberate_stop_non_root(
    paths: OrgPaths, db: Database, monkeypatch,
) -> None:
    """cap=1: first revise proceeds (count 0→1), second revise trips deliberate
    stop.  Non-root task fails and NOT auto-revisited (no auto_revisit_of audit)."""
    _seed_workspaces(paths)
    orch = _make_orch(paths, db)

    # Create a parent task so the content-manager task is non-root.
    parent_task = TaskRecord(
        id="TASK-PARENT",
        brief="parent",
        team="content",
        assigned_agent="content_manager",
        task_type="task",
    )
    db.insert_task(parent_task)

    child_task = TaskRecord(
        id="TASK-C2",
        brief="Write Macau visa guide (non-root)",
        team="content",
        assigned_agent="content_manager",
        parent_task_id="TASK-PARENT",
    )
    db.insert_task(child_task)
    tid = child_task.id

    cfg = load_org_config(paths)
    cfg = dataclasses.replace(cfg, max_revise_rounds=1)
    monkeypatch.setattr(
        "runtime.orchestrator.run_step.load_org_config", lambda p: cfg,
    )

    scripted = ScriptedRunAgent()
    # CM step 1: delegate to writer (first time, revision_count=0)
    scripted.enqueue(
        "content_manager",
        decision=NextStep(action="delegate", agent="content_writer", prompt="write v1"),
        summary="delegating to writer",
    )
    # Writer: v1
    scripted.enqueue("content_writer", summary="v1 draft")
    # CM step 2: delegate to QA
    scripted.enqueue(
        "content_manager",
        decision=NextStep(action="delegate", agent="content_qa", prompt="review v1"),
        summary="delegating to QA",
    )
    # QA: REVISE
    scripted.enqueue("content_qa", summary="VERDICT: REVISE — fix section 3")
    # CM step 3: re-delegate to writer (REVISE #1, revision_count 0→1, cap=1, proceed)
    scripted.enqueue(
        "content_manager",
        decision=NextStep(action="delegate", agent="content_writer", prompt="revise v1"),
        summary="requesting revision",
    )
    # Writer: v2
    scripted.enqueue("content_writer", summary="v2 draft")
    # CM step 4: delegate to QA again
    scripted.enqueue(
        "content_manager",
        decision=NextStep(action="delegate", agent="content_qa", prompt="review v2"),
        summary="delegating to QA for v2",
    )
    # QA: REVISE again
    scripted.enqueue("content_qa", summary="VERDICT: REVISE — still unclear")
    # CM step 5: REVISE #2 attempt → cap trips (revision_count=1, cap=1)
    # The task is non-root; it should FAIL and NOT spawn auto_revisit.
    scripted.enqueue(
        "content_manager",
        decision=NextStep(action="delegate", agent="content_writer", prompt="revise v2"),
        summary="requesting second revision",
    )
    monkeypatch.setattr(orch, "_run_agent", scripted)

    run_task_to_completion(orch, tid, max_steps=20)

    task = db.get_task(tid)
    assert task is not None
    assert task.status == TaskStatus.FAILED, (
        f"expected FAILED (revise budget stop), got {task.status}"
    )
    assert "revise budget" in (task.note or ""), (
        f"note should mention revise budget, got {task.note!r}"
    )
    assert task.revision_count == 1, (
        f"revision_count should still be 1 (increment was skipped), got {task.revision_count}"
    )

    # CRITICAL: no auto_revisit_of audit row was written
    audit_rows = db.get_audit_logs(tid)
    for row in audit_rows:
        assert row["action"] != "auto_revisit_of", (
            f"auto_revisit_of should NOT be written on deliberate budget stop, "
            f"got action={row['action']!r}"
        )

    # No new revisit root task spawned (the task's children are writer/QA subtasks only)
    children = db.get_children(tid)
    for cid in children:
        child = db.get_task(cid)
        assert child is not None
        assert child.task_type == "subtask", (
            f"expected only subtask children, got task_type={child.task_type!r}"
        )


def test_revise_below_cap_proceeds_normally(
    paths: OrgPaths, db: Database, monkeypatch,
) -> None:
    """cap=2, one REVISE: revision_count 0→1 (< cap), normal increment + delegate."""
    _seed_workspaces(paths)
    orch = _make_orch(paths, db)
    tid = _seed_task(db)

    cfg = load_org_config(paths)
    cfg = dataclasses.replace(cfg, max_revise_rounds=2)
    monkeypatch.setattr(
        "runtime.orchestrator.run_step.load_org_config", lambda p: cfg,
    )

    scripted = ScriptedRunAgent()
    # CM step 1: delegate to writer
    scripted.enqueue(
        "content_manager",
        decision=NextStep(action="delegate", agent="content_writer", prompt="write v1"),
        summary="delegating to writer",
    )
    # Writer: v1
    scripted.enqueue("content_writer", summary="v1 draft")
    # CM step 2: delegate to QA
    scripted.enqueue(
        "content_manager",
        decision=NextStep(action="delegate", agent="content_qa", prompt="review v1"),
        summary="delegating to QA",
    )
    # QA: REVISE
    scripted.enqueue("content_qa", summary="VERDICT: REVISE — fix section 3")
    # CM step 3: re-delegate to writer (REVISE, revision_count 0→1, cap=2, proceed)
    scripted.enqueue(
        "content_manager",
        decision=NextStep(action="delegate", agent="content_writer", prompt="revise v1"),
        summary="requesting revision",
    )
    # Writer: v2
    scripted.enqueue("content_writer", summary="v2 draft")
    # CM step 4: delegate to QA
    scripted.enqueue(
        "content_manager",
        decision=NextStep(action="delegate", agent="content_qa", prompt="review v2"),
        summary="delegating to QA for v2",
    )
    # QA: PASS
    scripted.enqueue("content_qa", summary="VERDICT: PASS")
    # CM step 5: done
    scripted.enqueue(
        "content_manager",
        decision=NextStep(action="done", summary="approved"),
        summary="done",
    )
    monkeypatch.setattr(orch, "_run_agent", scripted)

    run_task_to_completion(orch, tid, max_steps=20)

    task = db.get_task(tid)
    assert task is not None
    assert task.status == TaskStatus.COMPLETED, (
        f"expected COMPLETED, got {task.status}"
    )
    assert task.revision_count == 1, (
        f"expected revision_count == 1 (one REVISE counted), got {task.revision_count}"
    )


def test_revise_cap_zero_disabled_never_trips(
    paths: OrgPaths, db: Database, monkeypatch,
) -> None:
    """cap=0: revise budget disabled, behavior byte-for-byte unchanged even with
    high revision_count."""
    _seed_workspaces(paths)
    orch = _make_orch(paths, db)
    tid = _seed_task(db)

    # Default max_revise_rounds=0 — no monkeypatch needed for OrgConfig default.
    # But to be explicit, set it to 0.
    cfg = load_org_config(paths)
    cfg = dataclasses.replace(cfg, max_revise_rounds=0)
    monkeypatch.setattr(
        "runtime.orchestrator.run_step.load_org_config", lambda p: cfg,
    )

    scripted = ScriptedRunAgent()
    # Two REVISE cycles — should all proceed normally with cap=0.
    for round_num in (1, 2):
        scripted.enqueue(
            "content_manager",
            decision=NextStep(action="delegate", agent="content_writer",
                              prompt=f"write v{round_num}"),
            summary=f"delegating round {round_num}",
        )
        scripted.enqueue("content_writer", summary=f"v{round_num} draft")
        scripted.enqueue(
            "content_manager",
            decision=NextStep(action="delegate", agent="content_qa",
                              prompt=f"review v{round_num}"),
            summary=f"delegating to QA round {round_num}",
        )
        scripted.enqueue("content_qa", summary="VERDICT: REVISE — needs work")
    # Final pass after two REVISE cycles
    scripted.enqueue(
        "content_manager",
        decision=NextStep(action="delegate", agent="content_writer", prompt="final v3"),
        summary="final delegation",
    )
    scripted.enqueue("content_writer", summary="v3 draft")
    scripted.enqueue(
        "content_manager",
        decision=NextStep(action="delegate", agent="content_qa", prompt="review v3"),
        summary="final QA delegation",
    )
    scripted.enqueue("content_qa", summary="VERDICT: PASS")
    scripted.enqueue(
        "content_manager",
        decision=NextStep(action="done", summary="approved"),
        summary="done",
    )
    monkeypatch.setattr(orch, "_run_agent", scripted)

    run_task_to_completion(orch, tid, max_steps=20)

    task = db.get_task(tid)
    assert task is not None
    assert task.status == TaskStatus.COMPLETED, (
        f"expected COMPLETED with cap=0, got {task.status}"
    )
    # 2 genuine REVISE cycles → revision_count should be 2
    assert task.revision_count == 2, (
        f"expected revision_count == 2 (cap=0 disabled), got {task.revision_count}"
    )


def test_revise_cap_off_by_one_k1_exactly_one_revise(
    paths: OrgPaths, db: Database, monkeypatch,
) -> None:
    """Off-by-one: cap=K=1 => exactly 1 revise proceeds, 2nd trips.
    revision_count runs 0→1 across the allowed revise, then at 1 >= cap, TRIPS."""
    _seed_workspaces(paths)
    orch = _make_orch(paths, db)

    # Non-root so the cap stop fails instead of escalating.
    parent_task = TaskRecord(
        id="TASK-PARENT-K1", brief="parent", team="content",
        assigned_agent="content_manager", task_type="task",
    )
    db.insert_task(parent_task)
    child_task = TaskRecord(
        id="TASK-K1", brief="Write guide (k1)", team="content",
        assigned_agent="content_manager", parent_task_id="TASK-PARENT-K1",
    )
    db.insert_task(child_task)
    tid = child_task.id

    cfg = load_org_config(paths)
    cfg = dataclasses.replace(cfg, max_revise_rounds=1)
    monkeypatch.setattr(
        "runtime.orchestrator.run_step.load_org_config", lambda p: cfg,
    )

    scripted = ScriptedRunAgent()
    # Round 1: initial delegation → QA REVISE → CM re-delegates (REVISE #1, revision_count 0→1, cap=1, proceed)
    scripted.enqueue(
        "content_manager",
        decision=NextStep(action="delegate", agent="content_writer", prompt="write v1"),
        summary="delegating to writer",
    )
    scripted.enqueue("content_writer", summary="v1 draft")
    scripted.enqueue(
        "content_manager",
        decision=NextStep(action="delegate", agent="content_qa", prompt="review v1"),
        summary="delegating to QA",
    )
    scripted.enqueue("content_qa", summary="VERDICT: REVISE — fix it")
    # CM re-delegate: REVISE #1, count 0→1, 0 < 1 → proceed
    scripted.enqueue(
        "content_manager",
        decision=NextStep(action="delegate", agent="content_writer", prompt="revise v1"),
        summary="requesting revision",
    )
    scripted.enqueue("content_writer", summary="v2 draft")
    scripted.enqueue(
        "content_manager",
        decision=NextStep(action="delegate", agent="content_qa", prompt="review v2"),
        summary="delegating to QA for v2",
    )
    # QA: REVISE again → CM tries REVISE #2, revision_count=1, cap=1 → TRIPS
    scripted.enqueue("content_qa", summary="VERDICT: REVISE — still bad")
    scripted.enqueue(
        "content_manager",
        decision=NextStep(action="delegate", agent="content_writer", prompt="revise v2"),
        summary="requesting second revision",
    )
    monkeypatch.setattr(orch, "_run_agent", scripted)

    run_task_to_completion(orch, tid, max_steps=20)

    task = db.get_task(tid)
    assert task is not None
    assert task.status == TaskStatus.FAILED, (
        f"expected FAILED (cap=1 exhausted on 2nd revise), got {task.status}"
    )
    # Exactly 1 revise counted; the 2nd was rejected before increment
    assert task.revision_count == 1, (
        f"expected revision_count == 1 (one revise allowed), got {task.revision_count}"
    )


def test_revise_cap_off_by_one_k2_exactly_two_revises(
    paths: OrgPaths, db: Database, monkeypatch,
) -> None:
    """Off-by-one: cap=K=2 => exactly 2 revises proceed, 3rd trips.
    count 0→1 (1st revise, ok), 1→2 (2nd revise, ok), 2 >= 2 → TRIPS on 3rd."""
    _seed_workspaces(paths)
    orch = _make_orch(paths, db)

    # Non-root so the cap stop fails instead of escalating.
    parent_task = TaskRecord(
        id="TASK-PARENT-K2", brief="parent", team="content",
        assigned_agent="content_manager", task_type="task",
    )
    db.insert_task(parent_task)
    child_task = TaskRecord(
        id="TASK-K2", brief="Write guide (k2)", team="content",
        assigned_agent="content_manager", parent_task_id="TASK-PARENT-K2",
    )
    db.insert_task(child_task)
    tid = child_task.id

    cfg = load_org_config(paths)
    cfg = dataclasses.replace(cfg, max_revise_rounds=2)
    monkeypatch.setattr(
        "runtime.orchestrator.run_step.load_org_config", lambda p: cfg,
    )

    scripted = ScriptedRunAgent()
    # Three REVISE cycles should be queued; the 3rd should trip.
    for round_num in (1, 2, 3):
        scripted.enqueue(
            "content_manager",
            decision=NextStep(action="delegate", agent="content_writer",
                              prompt=f"write r{round_num}"),
            summary=f"delegating round {round_num}",
        )
        scripted.enqueue("content_writer", summary=f"r{round_num} draft")
        scripted.enqueue(
            "content_manager",
            decision=NextStep(action="delegate", agent="content_qa",
                              prompt=f"review r{round_num}"),
            summary=f"delegating to QA round {round_num}",
        )
        scripted.enqueue("content_qa", summary="VERDICT: REVISE — still needs work")
    monkeypatch.setattr(orch, "_run_agent", scripted)

    run_task_to_completion(orch, tid, max_steps=20)

    task = db.get_task(tid)
    assert task is not None
    assert task.status == TaskStatus.FAILED, (
        f"expected FAILED (cap=2 exhausted on 3rd revise), got {task.status}"
    )
    # Exactly 2 revises counted; the 3rd was rejected before increment
    assert task.revision_count == 2, (
        f"expected revision_count == 2 (two revises allowed), got {task.revision_count}"
    )


def test_revise_cap_root_escalates(
    paths: OrgPaths, db: Database, monkeypatch,
) -> None:
    """Root task hits revise budget → escalates (not fails). Mirror section-2 root
    budget stop: log_escalation + notify_escalated + thread escalation."""
    _seed_workspaces(paths)
    orch = _make_orch(paths, db)
    tid = _seed_task(db, task_id="TASK-R1")

    # Make this a root task (no parent)
    task = db.get_task(tid)
    assert task is not None
    # Already root — _seed_task creates a task with no parent

    cfg = load_org_config(paths)
    cfg = dataclasses.replace(cfg, max_revise_rounds=1)
    monkeypatch.setattr(
        "runtime.orchestrator.run_step.load_org_config", lambda p: cfg,
    )

    # Stub notify_escalated to verify it is called
    notified: list = []
    def _fake_notify(task_id, agent, reason, last_summary=""):
        notified.append((task_id, agent, reason))
    monkeypatch.setattr(orch, "notify_escalated", _fake_notify)

    scripted = ScriptedRunAgent()
    # First delegate → QA REVISE → CM re-delegates (REVISE #1, revision_count 0→1, cap=1, proceed)
    scripted.enqueue(
        "content_manager",
        decision=NextStep(action="delegate", agent="content_writer", prompt="write v1"),
        summary="delegating to writer",
    )
    scripted.enqueue("content_writer", summary="v1 draft")
    scripted.enqueue(
        "content_manager",
        decision=NextStep(action="delegate", agent="content_qa", prompt="review v1"),
        summary="delegating to QA",
    )
    scripted.enqueue("content_qa", summary="VERDICT: REVISE — fix it")
    # CM re-delegate: REVISE #1, count 0→1, 0 < 1 → proceed
    scripted.enqueue(
        "content_manager",
        decision=NextStep(action="delegate", agent="content_writer", prompt="revise v1"),
        summary="requesting revision",
    )
    scripted.enqueue("content_writer", summary="v2 draft")
    scripted.enqueue(
        "content_manager",
        decision=NextStep(action="delegate", agent="content_qa", prompt="review v2"),
        summary="delegating to QA for v2",
    )
    # QA: REVISE again → CM tries REVISE #2, trips cap
    scripted.enqueue("content_qa", summary="VERDICT: REVISE — still bad")
    scripted.enqueue(
        "content_manager",
        decision=NextStep(action="delegate", agent="content_writer", prompt="revise v2"),
        summary="requesting second revision",
    )
    monkeypatch.setattr(orch, "_run_agent", scripted)

    run_task_to_completion(orch, tid, max_steps=20)

    task = db.get_task(tid)
    assert task is not None
    assert task.status == TaskStatus.ESCALATED, (
        f"expected ESCALATED (root revise budget exhaust), got {task.status}"
    )
    assert "revise budget" in (task.note or ""), (
        f"note should mention revise budget, got {task.note!r}"
    )
    assert task.revision_count == 1, (
        f"revision_count should still be 1, got {task.revision_count}"
    )

    # Verify escalation notification was triggered
    assert len(notified) == 1, f"expected 1 notify_escalated call, got {len(notified)}"
    assert notified[0][0] == tid
    assert notified[0][1] == "orchestrator"

    # No auto_revisit_of audit
    audit_rows = db.get_audit_logs(tid)
    for row in audit_rows:
        assert row["action"] != "auto_revisit_of", (
            f"auto_revisit_of should NOT be written on deliberate budget stop"
        )
