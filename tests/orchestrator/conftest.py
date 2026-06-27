"""Shared helpers for orchestrator unit tests."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

from runtime.infrastructure.database import Database
from runtime.models import BlockKind, CompletionReport, NextStep, TaskStatus
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.orchestrator import Orchestrator
from runtime.orchestrator.executors import ExecutorResult
from runtime.runtime import RuntimeDir


# ---------------------------------------------------------------------------
# ScriptedRunAgent — pops pre-declared (ExecutorResult, CompletionReport)
# tuples per-agent in FIFO order.
# ---------------------------------------------------------------------------

class ScriptedRunAgent:
    """Simulates _run_agent by returning pre-queued results per agent.

    Usage::

        scripted = ScriptedRunAgent()
        scripted.enqueue("content_manager",
                         decision=NextStep(action="delegate", agent="content_writer", prompt="x"),
                         summary="delegating")
        scripted.enqueue("content_writer", summary="draft done",
                         output_dir="output/TASK-C1")

        monkeypatch.setattr(orch, "_run_agent", scripted)

    The fake does NOT insert a task_result row — the orchestrator's `_run_agent`
    returns ``(result, report)`` directly, and `run_step_impl` uses that tuple
    without going back to the DB for the report.  `_log_step_result` just audits
    the report it already has in hand.
    """

    def __init__(self) -> None:
        # agent_name -> FIFO list of (ExecutorResult, CompletionReport | None)
        self._queues: dict[str, list[tuple[ExecutorResult, CompletionReport | None]]] = (
            defaultdict(list)
        )

    def enqueue(
        self,
        agent: str,
        *,
        decision: NextStep | None = None,
        summary: str = "",
        output_dir: str | None = None,
        success: bool = True,
    ) -> None:
        """Pre-declare what _run_agent returns the next time `agent` is called."""
        result = ExecutorResult(
            success=success,
            session_id=f"sess-fake-{agent}",
            duration_seconds=0,
        )
        report: CompletionReport | None
        if success:
            report = CompletionReport(
                task_id="",  # placeholder; run_step doesn't use this field from the return value
                agent=agent,
                status="completed",
                confidence=90,
                output_summary=summary,
                decision=decision,
                output_dir=output_dir,
            )
        else:
            report = None
        self._queues[agent].append((result, report))

    def __call__(
        self,
        task_id: str,
        agent: str,
        prompt: str,
        on_session_started: Any = None,
    ) -> tuple[ExecutorResult, CompletionReport | None]:
        queue = self._queues.get(agent)
        if not queue:
            raise AssertionError(
                f"ScriptedRunAgent: unexpected call for agent={agent!r} task={task_id!r} "
                f"(no enqueued responses left). Check test setup."
            )
        return queue.pop(0)


# ---------------------------------------------------------------------------
# run_task_to_completion — drives the full task tree without a real queue
# ---------------------------------------------------------------------------

_TERMINAL = frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED})


def _is_eligible(db: Database, tid: str) -> bool:
    """Return True if run_step would accept this task (mirrors run_step entry check).

    Path B: a parked-delegated parent is in_progress(delegated); the legacy
    blocked(delegated) shape is accepted too (dual-read), matching the real
    entry gate's _PARKED_CARRIER_STATUSES.
    """
    task = db.get_task(tid)
    if task is None:
        return False
    if task.status == TaskStatus.PENDING:
        return True
    if (task.status in (TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED)
            and task.block_kind == BlockKind.DELEGATED):
        children = [db.get_task(cid) for cid in db.get_children(tid)]
        return all(c is not None and c.status in _TERMINAL for c in children)
    return False


def _find_eligible_in_tree(db: Database, root_id: str) -> list[str]:
    """BFS the task tree, return all run_step-eligible task IDs (leaves first)."""
    visited: list[str] = []
    bfs_queue = [root_id]
    while bfs_queue:
        tid = bfs_queue.pop(0)
        task = db.get_task(tid)
        if task is None:
            continue
        visited.append(tid)
        bfs_queue.extend(db.get_children(tid))
    # Reverse so we process deepest (leaf) eligible tasks before their parents.
    return [tid for tid in reversed(visited) if _is_eligible(db, tid)]


def run_task_to_completion(orch: Orchestrator, task_id: str, max_steps: int = 20) -> None:
    """Drive the entire task tree until the root reaches a terminal state.

    The real daemon uses an async queue; this helper replicates that by
    scanning the tree for any PENDING task and calling run_step on it.
    """
    for _ in range(max_steps):
        root = orch._db.get_task(task_id)
        if root is None:
            return
        if root.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            return
        # Path B: an escalated root is the top-level ESCALATED status (legacy
        # blocked(escalated) accepted too — dual-read).
        if root.status == TaskStatus.ESCALATED or (
            root.status == TaskStatus.BLOCKED and root.block_kind == BlockKind.ESCALATED
        ):
            return
        # Find next eligible task in tree (leaves first so children run before parents resume).
        eligible = _find_eligible_in_tree(orch._db, task_id)
        if not eligible:
            return
        orch.run_step(eligible[0])
    raise AssertionError(
        f"task {task_id} did not terminate within {max_steps} steps"
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def paths(tmp_path: Path) -> OrgPaths:
    """An OrgPaths rooted at <tmp>/rt/orgs/test/ with a minimal teams.yaml.

    The RuntimeDir multi-org container is materialized at <tmp>/rt/ for tests
    that want to call ``RuntimeDir.load(...)``; the OrgPaths returned points
    at the single seeded ``test`` org root.
    """
    rt = RuntimeDir.init(tmp_path / "rt")
    org_root = rt.orgs_dir / "test"
    op = OrgPaths(root=org_root)
    op.teams_config_path.parent.mkdir(parents=True, exist_ok=True)
    op.teams_config_path.write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: engineering_head\n"
        "    workers: [product_manager, dev_agent, payment_agent, qa_engineer]\n"
        "  content:\n"
        "    manager: content_manager\n"
        "    workers: [content_writer, content_qa]\n"
    )
    return op


@pytest.fixture
def db(paths: OrgPaths) -> Database:
    return Database(paths.db_path)
