"""Unit tests for Orchestrator.run_step — the single primitive that advances
a task one subprocess call at a time under the new async execution model."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.config import Settings
from src.infrastructure.database import Database
from src.models import BlockKind, TaskRecord, TaskStatus, TaskType
from src.runtime import RuntimeDir


@pytest.fixture
def runtime(tmp_path: Path) -> RuntimeDir:
    return RuntimeDir.init(tmp_path / "rt")


@pytest.fixture
def db(runtime: RuntimeDir) -> Database:
    return Database(runtime.db_path)


def test_run_step_silent_noop_when_task_missing(runtime, db):
    from src.orchestrator.orchestrator import Orchestrator
    settings = Settings(max_orchestration_steps=3)
    orch = Orchestrator(db=db, settings=settings, runtime=runtime)
    # Just must not raise
    orch.run_step("TASK-NOPE")


def test_run_step_noop_on_blocked_escalated(runtime, db):
    """A task in blocked(ESCALATED) isn't eligible for run_step — it waits
    for /resolve-escalation to transition it first. Second-hand enqueue
    must be silently ignored."""
    from src.orchestrator.orchestrator import Orchestrator
    db.insert_task(TaskRecord(id="T-1", type=TaskType.GENERAL, brief="x"))
    db.update_task("T-1", status=TaskStatus.BLOCKED, block_kind=BlockKind.ESCALATED,
                   note="halted")
    orch = Orchestrator(db=db, settings=Settings(), runtime=runtime)
    orch.run_step("T-1")
    t = db.get_task("T-1")
    assert t.status == TaskStatus.BLOCKED
    assert t.block_kind == BlockKind.ESCALATED


def test_run_step_over_budget_parks_escalated(runtime, db):
    from src.orchestrator.orchestrator import Orchestrator
    settings = Settings(max_orchestration_steps=3)
    db.insert_task(TaskRecord(
        id="T-1", type=TaskType.GENERAL, brief="x", assigned_agent="engineering_head",
    ))
    db.update_task("T-1", orchestration_step_count=3)  # already at the cap

    orch = Orchestrator(db=db, settings=settings, runtime=runtime)
    orch.run_step("T-1")

    t = db.get_task("T-1")
    assert t.status == TaskStatus.BLOCKED
    assert t.block_kind == BlockKind.ESCALATED
    assert t.note and "max steps" in t.note
    # Audit row
    escalations = [
        a for a in db.get_audit_logs("T-1") if a["action"] == "escalation"
    ]
    assert len(escalations) == 1
    assert "max steps" in escalations[0]["payload"]["reason"]


def test_run_step_transitions_pending_to_in_progress_and_increments_count(
    runtime, db, monkeypatch,
):
    """On pickup, run_step must flip to in_progress, clear block fields,
    and increment the step counter exactly once — BEFORE invoking the agent."""
    from src.orchestrator.orchestrator import Orchestrator, WorkspaceNotInitialized

    db.insert_task(TaskRecord(
        id="T-1", type=TaskType.GENERAL, brief="x", assigned_agent="engineering_head",
    ))
    orch = Orchestrator(db=db, settings=Settings(max_orchestration_steps=10), runtime=runtime)

    # Force _run_agent to raise so we can inspect the DB state mid-flight.
    captured: dict = {}
    def fail(task_id, agent, prompt, on_session_started=None):
        t = db.get_task(task_id)
        captured["status"] = t.status
        captured["count"] = t.orchestration_step_count
        captured["block_kind"] = t.block_kind
        captured["note"] = t.note
        raise WorkspaceNotInitialized("fake")
    monkeypatch.setattr(orch, "_run_agent", fail)

    orch.run_step("T-1")

    assert captured["status"] == TaskStatus.IN_PROGRESS
    assert captured["count"] == 1
    assert captured["block_kind"] is None
    assert captured["note"] is None


def _make_report(output_summary: str, status: str = "completed",
                 artifact_dir: str | None = None):
    from src.models import CompletionReport
    return CompletionReport(
        task_id="T-IGNORED", agent="engineering_head", status=status,
        confidence=80, output_summary=output_summary, artifact_dir=artifact_dir,
    )


def _make_result(success: bool = True, duration: int = 1):
    from src.orchestrator.executor import ExecutorResult
    return ExecutorResult(
        success=success, session_id="sess-x", duration_seconds=duration,
    )


def test_run_step_done_completes_task_and_enqueues_parent(
    runtime, db, monkeypatch,
):
    import asyncio
    import json
    from src.orchestrator.orchestrator import Orchestrator

    # Parent in blocked(DELEGATED), child in pending.
    db.insert_task(TaskRecord(id="T-PAR", type=TaskType.GENERAL, brief="parent",
                              assigned_agent="engineering_head"))
    db.update_task("T-PAR", status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(
        id="T-CHD", type=TaskType.GENERAL, brief="child",
        assigned_agent="engineering_head", parent_task_id="T-PAR",
    ))

    orch = Orchestrator(db=db, settings=Settings(max_orchestration_steps=10),
                        runtime=runtime)
    # Wire a fake queue
    q: asyncio.Queue = asyncio.Queue()
    orch._queue = q

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary=json.dumps({"action": "done", "summary": "Looks great"}),
            artifact_dir="artifacts/run-1",
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    orch.run_step("T-CHD")

    child = db.get_task("T-CHD")
    assert child.status == TaskStatus.COMPLETED
    assert child.note == "Looks great"
    assert child.final_artifact_dir == "artifacts/run-1"

    # Parent should be enqueued
    assert q.qsize() == 1
    assert q.get_nowait() == "T-PAR"


def test_run_step_escalate_parks_blocked_and_leaves_parent_parked(
    runtime, db, monkeypatch,
):
    import asyncio
    import json
    from src.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-PAR", type=TaskType.GENERAL, brief="p",
                              assigned_agent="engineering_head"))
    db.update_task("T-PAR", status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(
        id="T-CHD", type=TaskType.GENERAL, brief="c",
        assigned_agent="engineering_head", parent_task_id="T-PAR",
    ))

    orch = Orchestrator(db=db, settings=Settings(), runtime=runtime)
    q: asyncio.Queue = asyncio.Queue()
    orch._queue = q

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary=json.dumps({"action": "escalate", "reason": "needs founder"}),
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    orch.run_step("T-CHD")

    child = db.get_task("T-CHD")
    assert child.status == TaskStatus.BLOCKED
    assert child.block_kind == BlockKind.ESCALATED
    assert child.note == "needs founder"

    # Parent stays parked — escalation is NOT a terminal for sibling-summing.
    assert q.qsize() == 0
    assert db.get_task("T-PAR").status == TaskStatus.BLOCKED

    # Audit row
    escalations = [a for a in db.get_audit_logs("T-CHD") if a["action"] == "escalation"]
    assert any("needs founder" in e["payload"]["reason"] for e in escalations)


def test_run_step_delegate_spawns_child_and_blocks_self(
    runtime, db, monkeypatch,
):
    import asyncio
    import json
    from src.orchestrator.orchestrator import Orchestrator

    (runtime.workspaces_dir / "dev_agent").mkdir(parents=True)

    db.insert_task(TaskRecord(id="T-1", type=TaskType.GENERAL, brief="root",
                              assigned_agent="engineering_head"))
    orch = Orchestrator(db=db, settings=Settings(), runtime=runtime)
    q: asyncio.Queue = asyncio.Queue()
    orch._queue = q

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary=json.dumps({
                "action": "delegate",
                "agent": "dev_agent",
                "prompt": "Write a PR",
            }),
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    orch.run_step("T-1")

    # Parent now blocked(DELEGATED)
    parent = db.get_task("T-1")
    assert parent.status == TaskStatus.BLOCKED
    assert parent.block_kind == BlockKind.DELEGATED
    assert "dev_agent" in (parent.note or "")

    # Exactly one child exists, is pending, and is enqueued
    children = db.get_children("T-1")
    assert len(children) == 1
    child_id = children[0]
    child = db.get_task(child_id)
    assert child.status == TaskStatus.PENDING
    assert child.assigned_agent == "dev_agent"
    assert child.brief == "Write a PR"
    assert child.parent_task_id == "T-1"
    assert q.get_nowait() == child_id


def test_run_step_invalid_delegate_fails_task(runtime, db, monkeypatch):
    """A delegate with no agent name is unrecoverable — fail the task and
    notify the parent (which may itself be root — no-op in that case)."""
    import asyncio
    import json
    from src.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-1", type=TaskType.GENERAL, brief="x",
                              assigned_agent="engineering_head"))
    orch = Orchestrator(db=db, settings=Settings(), runtime=runtime)
    orch._queue = asyncio.Queue()

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary=json.dumps({"action": "delegate", "prompt": "x"}),
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    orch.run_step("T-1")
    t = db.get_task("T-1")
    assert t.status == TaskStatus.FAILED
    assert t.note and "invalid delegate" in t.note


def test_run_step_session_failure_fails_task_and_notifies_parent(
    runtime, db, monkeypatch,
):
    import asyncio
    from src.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-PAR", type=TaskType.GENERAL, brief="p",
                              assigned_agent="engineering_head"))
    db.update_task("T-PAR", status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(
        id="T-CHD", type=TaskType.GENERAL, brief="c",
        assigned_agent="engineering_head", parent_task_id="T-PAR",
    ))

    orch = Orchestrator(db=db, settings=Settings(), runtime=runtime)
    q: asyncio.Queue = asyncio.Queue()
    orch._queue = q

    monkeypatch.setattr(orch, "_run_agent",
                        lambda *a, **k: (_make_result(success=False), None))

    orch.run_step("T-CHD")
    child = db.get_task("T-CHD")
    assert child.status == TaskStatus.FAILED
    assert "session failed" in (child.note or "")
    assert q.get_nowait() == "T-PAR"


def test_run_step_worker_self_blocked_fails_task(runtime, db, monkeypatch):
    import asyncio
    from src.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-1", type=TaskType.GENERAL, brief="x",
                              assigned_agent="engineering_head"))
    orch = Orchestrator(db=db, settings=Settings(), runtime=runtime)
    orch._queue = asyncio.Queue()

    monkeypatch.setattr(orch, "_run_agent",
                        lambda *a, **k: (_make_result(), _make_report(
                            output_summary="ran out of tokens", status="blocked")))

    orch.run_step("T-1")
    t = db.get_task("T-1")
    assert t.status == TaskStatus.FAILED
    assert t.note and t.note.startswith("self-blocked:")
