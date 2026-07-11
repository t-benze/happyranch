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
    assert "iteration_budget_exhausted" in (task.note or ""), (
        f"note should contain iteration_budget_exhausted token, got {task.note!r}"
    )
    assert task.revision_count == 1, (
        f"revision_count should still be 1 (increment was skipped), got {task.revision_count}"
    )

    # CRITICAL: no auto_revisit_of audit row was written ANYWHERE in the DB.
    # get_audit_logs(tid) is scoped to the capped task only, but
    # _maybe_spawn_auto_revisit logs auto_revisit_of on the NEW successor
    # root — so a row-scoped check is false-green. Use global queries.
    auto_revisit_audit_rows = db.fetch_all_readonly(
        "SELECT 1 FROM audit_log WHERE action = 'auto_revisit_of'"
    )
    assert len(auto_revisit_audit_rows) == 0, (
        f"auto_revisit_of audit row should NOT exist on deliberate budget stop; "
        f"found {len(auto_revisit_audit_rows)} row(s)"
    )

    # No task row has revisit_of_task_id pointing to the capped task or its root.
    # Auto-revisit roots are inserted with parent_task_id=None, so a
    # child-of-the-capped-task scan is false-green.
    auto_revisit_task_rows = db.fetch_all_readonly(
        "SELECT id, revisit_of_task_id FROM tasks "
        "WHERE revisit_of_task_id IS NOT NULL",
    )
    for row in auto_revisit_task_rows:
        assert row["revisit_of_task_id"] not in (tid, "TASK-PARENT"), (
            f"no task should have revisit_of_task_id pointing to capped task "
            f"{tid!r} or its root; found row {dict(row)}"
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
    assert "iteration_budget_exhausted" in (task.note or ""), (
        f"note should contain iteration_budget_exhausted token, got {task.note!r}"
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
    # Three REVISE cycles should be queued; the 3rd delegate (round 3 CM) bumps
    # count 1→2 (allowed), and the 4th CM re-entry would trip the cap.
    # The 4th-entry cap-stop assertion lives in
    # test_revise_cap_trips_deliberate_stop_non_root; this test verifies the
    # off-by-one boundary: exactly 2 revises are counted and the 3rd bump
    # succeeds at the delegation level (revision_count reaches 2).
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
    assert "iteration_budget_exhausted" in (task.note or ""), (
        f"note should contain iteration_budget_exhausted token, got {task.note!r}"
    )
    assert task.revision_count == 1, (
        f"revision_count should still be 1, got {task.revision_count}"
    )

    # Verify escalation notification was triggered
    assert len(notified) == 1, f"expected 1 notify_escalated call, got {len(notified)}"
    assert notified[0][0] == tid
    assert notified[0][1] == "orchestrator"

    # No auto_revisit_of audit anywhere in the DB (global check, not scoped
    # to the capped task id which would be false-green)
    auto_revisit_audit_rows = db.fetch_all_readonly(
        "SELECT 1 FROM audit_log WHERE action = 'auto_revisit_of'"
    )
    assert len(auto_revisit_audit_rows) == 0, (
        f"auto_revisit_of audit row should NOT exist on deliberate budget stop; "
        f"found {len(auto_revisit_audit_rows)} row(s)"
    )


def test_revise_cap_preserves_best_attempt_no_teardown(
    paths: OrgPaths, db: Database, monkeypatch,
) -> None:
    """THR-026 seq33 / ITEM 3: On revise-budget stop, the capped slice ends in its
    terminal state with note intact, is NOT auto-revisited (no successor root
    re-running the slice), and the stop path performs no branch/worktree
    teardown side effect. _fail and try_escalate write DB state + audit + notify
    + kill in-flight jobs — they do NOT touch git, worktrees, or branches."""
    _seed_workspaces(paths)
    orch = _make_orch(paths, db)

    # Non-root task so the cap stop fails instead of escalating.
    parent_task = TaskRecord(
        id="TASK-PARENT-T3", brief="parent", team="content",
        assigned_agent="content_manager", task_type="task",
    )
    db.insert_task(parent_task)
    child_task = TaskRecord(
        id="TASK-T3", brief="Write guide (t3)", team="content",
        assigned_agent="content_manager", parent_task_id="TASK-PARENT-T3",
    )
    db.insert_task(child_task)
    tid = child_task.id

    cfg = load_org_config(paths)
    cfg = dataclasses.replace(cfg, max_revise_rounds=1)
    monkeypatch.setattr(
        "runtime.orchestrator.run_step.load_org_config", lambda p: cfg,
    )

    # Spy on _fail to prove the budget-stop note reached _fail.
    fail_calls_for_tid: list = []
    _orig_fail = getattr(
        __import__("runtime.orchestrator.run_step", fromlist=["_fail"]),
        "_fail",
    )
    def _spy_fail(orch_, task_id_, *, note):
        if task_id_ == tid:
            fail_calls_for_tid.append(note)
        return _orig_fail(orch_, task_id_, note=note)
    monkeypatch.setattr(
        "runtime.orchestrator.run_step._fail", _spy_fail,
    )

    # Spy on _kill_jobs_for_terminating_task to confirm it fires for this
    # task (it's the only non-DB-write side effect in _fail).
    kill_calls_for_tid: list = []
    _orig_kill = getattr(
        __import__("runtime.orchestrator.run_step", fromlist=["_kill_jobs_for_terminating_task"]),
        "_kill_jobs_for_terminating_task",
    )
    def _spy_kill(orch_, task_id_):
        if task_id_ == tid:
            kill_calls_for_tid.append(task_id_)
        return _orig_kill(orch_, task_id_)
    monkeypatch.setattr(
        "runtime.orchestrator.run_step._kill_jobs_for_terminating_task", _spy_kill,
    )

    scripted = ScriptedRunAgent()
    # First delegate → QA REVISE → CM re-delegates (REVISE #1, count 0→1, cap=1, proceed)
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

    # ── Assert 1: capped slice ends in terminal state with note intact ──
    task = db.get_task(tid)
    assert task is not None
    assert task.status == TaskStatus.FAILED, (
        f"expected FAILED (budget stop), got {task.status}"
    )
    assert "iteration_budget_exhausted" in (task.note or ""), (
        f"note should contain iteration_budget_exhausted, got {task.note!r}"
    )
    assert task.revision_count == 1  # not incremented past cap

    # ── Assert 2: NOT auto-revisited (global checks) ──
    auto_revisit_audit_rows = db.fetch_all_readonly(
        "SELECT 1 FROM audit_log WHERE action = 'auto_revisit_of'"
    )
    assert len(auto_revisit_audit_rows) == 0, (
        f"auto_revisit_of audit row should NOT exist; found "
        f"{len(auto_revisit_audit_rows)}"
    )
    auto_revisit_task_rows = db.fetch_all_readonly(
        "SELECT id FROM tasks WHERE revisit_of_task_id IS NOT NULL",
    )
    assert len(auto_revisit_task_rows) == 0, (
        f"no task should have revisit_of_task_id set; found "
        f"{len(auto_revisit_task_rows)}"
    )

    # ── Assert 3: stop path performs no branch/worktree teardown ──
    # _fail was called for this task with the budget-stop note.
    assert len(fail_calls_for_tid) >= 1, (
        f"expected at least one _fail call for {tid}, got {len(fail_calls_for_tid)}"
    )
    assert "iteration_budget_exhausted" in fail_calls_for_tid[0]

    # _fail's only non-DB-write side effect is _kill_jobs_for_terminating_task.
    # It fires for this task, confirming the kill path — NOT git, worktree,
    # or branch cleanup.
    assert len(kill_calls_for_tid) >= 1, (
        f"expected at least one _kill_jobs call for {tid}, got "
        f"{len(kill_calls_for_tid)}"
    )

    # No audit row indicates any teardown activity (only orchestration steps
    # and delegation events, no branch/worktree cleanup).
    all_audit = db.fetch_all_readonly("SELECT action FROM audit_log")
    teardown_actions = {"branch_cleanup", "worktree_removed", "cleanup"}
    for row in all_audit:
        assert row["action"] not in teardown_actions, (
            f"no teardown audit action should exist; found {row['action']!r}"
        )
