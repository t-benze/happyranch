"""Async end-to-end: EH delegates → child runs → parent resumes → parent completes."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from src.config import Settings
from src.daemon.queue import TaskQueue
from src.infrastructure.database import Database
from src.models import TaskRecord, TaskStatus, TaskType
from src.orchestrator.orchestrator import Orchestrator
from src.runtime import RuntimeDir


@pytest.mark.asyncio
async def test_full_delegation_roundtrip(tmp_path: Path, monkeypatch):
    runtime = RuntimeDir.init(tmp_path / "rt")
    (runtime.workspaces_dir / "engineering_head" / ".claude" / "skills" / "start-task").mkdir(parents=True)
    (runtime.workspaces_dir / "engineering_head" / ".claude" / "skills" / "start-task" / "SKILL.md").touch()
    (runtime.workspaces_dir / "dev_agent" / ".claude" / "skills" / "start-task").mkdir(parents=True)
    (runtime.workspaces_dir / "dev_agent" / ".claude" / "skills" / "start-task" / "SKILL.md").touch()
    db = Database(runtime.db_path)

    orch = Orchestrator(db=db, settings=Settings(max_orchestration_steps=10), runtime=runtime)
    queue = TaskQueue()
    orch.attach_queue(queue)

    # Fake `_run_agent`: EH first returns delegate, second call returns done;
    # dev_agent returns done.
    call_log: list[tuple[str, str]] = []
    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        call_log.append((task_id, agent))
        from src.orchestrator.executor import ExecutorResult
        from src.models import CompletionReport
        if agent == "engineering_head":
            # First EH pass delegates; second is `done`.
            past_eh_calls = sum(1 for (_t, a) in call_log if a == "engineering_head")
            if past_eh_calls == 1:
                summary = json.dumps({
                    "action": "delegate",
                    "agent": "dev_agent",
                    "prompt": "Write feature",
                })
            else:
                summary = json.dumps({"action": "done", "summary": "Root done"})
        else:
            summary = json.dumps({"action": "done", "summary": "Child done"})
        return (
            ExecutorResult(success=True, session_id="s", duration_seconds=1),
            CompletionReport(task_id=task_id, agent=agent, status="completed",
                             confidence=80, output_summary=summary),
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    # Seed the root
    db.insert_task(TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="build"))
    queue.enqueue("TASK-001")

    # Drain in two passes — delegate creates a child and enqueues it, which
    # drain_sync will pick up on the same pass. But run_step is synchronous
    # inside drain, so one drain_sync call may not suffice; iterate until
    # queue is empty AND the root is terminal.
    for _ in range(6):
        await queue.drain_sync(orch)
        root = db.get_task("TASK-001")
        if root.status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
            break

    root = db.get_task("TASK-001")
    assert root.status == TaskStatus.COMPLETED
    assert root.note == "Root done"
    # Exactly one child, completed, with brief from the delegate prompt
    children = db.get_children("TASK-001")
    assert len(children) == 1
    child = db.get_task(children[0])
    assert child.status == TaskStatus.COMPLETED
    assert child.assigned_agent == "dev_agent"
