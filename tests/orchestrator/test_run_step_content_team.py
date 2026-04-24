"""Unit tests for the Content Team MVP flow through run_step.

Tests the three paths defined in the spec:
  - PASS: CM → writer → QA(PASS) → CM done → COMPLETED
  - REVISE: CM → writer → QA(REVISE) → CM re-delegates writer → writer → QA(PASS) → done
  - REJECT: CM → writer → QA(REJECT) → CM escalate → BLOCKED(ESCALATED)
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.config import Settings
from src.infrastructure.database import Database
from src.models import BlockKind, NextStep, TaskRecord, TaskStatus
from src.orchestrator.orchestrator import Orchestrator
from src.runtime import RuntimeDir
from tests.orchestrator.conftest import ScriptedRunAgent, run_task_to_completion


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_orch(runtime: RuntimeDir, db: Database) -> Orchestrator:
    """Build an Orchestrator with a real async queue (needed by _enqueue_parent_if_waiting)."""
    orch = Orchestrator(
        db=db,
        settings=Settings(max_orchestration_steps=15),
        runtime=runtime,
    )
    orch._queue = asyncio.Queue()
    return orch


def _seed_workspaces(runtime: RuntimeDir) -> None:
    """Create the minimal workspace directories that run_step checks exist."""
    for agent in ("content_manager", "content_writer", "content_qa"):
        (runtime.workspaces_dir / agent).mkdir(parents=True, exist_ok=True)


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

def test_pass_path_completes_task(runtime: RuntimeDir, db: Database, monkeypatch) -> None:
    """Happy path: CM → writer → QA(PASS) → CM done → task COMPLETED."""
    _seed_workspaces(runtime)
    orch = _make_orch(runtime, db)
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
        artifact_dir=f"artifacts/{tid}",
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


def test_revise_path_bumps_revision_count(runtime: RuntimeDir, db: Database, monkeypatch) -> None:
    """REVISE path: one QA rejection cycle; final PASS; revision_count >= 1."""
    _seed_workspaces(runtime)
    orch = _make_orch(runtime, db)
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
    assert task.revision_count >= 1, (
        f"expected revision_count >= 1 after a REVISE cycle, got {task.revision_count}"
    )


def test_reject_path_escalates(runtime: RuntimeDir, db: Database, monkeypatch) -> None:
    """REJECT path: QA rejects → CM escalates → task ends BLOCKED(ESCALATED)."""
    _seed_workspaces(runtime)
    orch = _make_orch(runtime, db)
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
    assert task.status == TaskStatus.BLOCKED, f"expected BLOCKED, got {task.status}"
    assert task.block_kind is not None and task.block_kind.value == "escalated", (
        f"expected block_kind='escalated', got {task.block_kind!r}"
    )
