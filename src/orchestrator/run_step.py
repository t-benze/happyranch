"""Implementation of Orchestrator.run_step — the single primitive that advances
a task one subprocess call at a time. Separate from orchestrator.py so the
algorithm has its own test surface.

Entry contract: task MUST be either
  (a) status=pending, or
  (b) status=blocked AND block_kind=DELEGATED AND all children are terminal.
Any other state = stale enqueue, silent no-op.

Exit contract: task ends in exactly one of {in_progress-then-crashed,
completed, failed, blocked(DELEGATED), blocked(ESCALATED)}.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.models import BlockKind, TaskStatus

if TYPE_CHECKING:
    from src.orchestrator.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

TERMINAL_STATES = frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED})


def run_step_impl(orch: "Orchestrator", task_id: str) -> None:
    db = orch._db
    task = db.get_task(task_id)
    if task is None:
        return

    # ---- 1. Verify entry state ----
    if task.status == TaskStatus.PENDING:
        pass  # eligible
    elif task.status == TaskStatus.BLOCKED and task.block_kind == BlockKind.DELEGATED:
        children = [db.get_task(cid) for cid in db.get_children(task_id)]
        if any(c is None or c.status not in TERMINAL_STATES for c in children):
            logger.debug("run_step %s: child still running, skipping", task_id)
            return
    else:
        logger.debug(
            "run_step %s: not eligible (status=%s, block_kind=%s)",
            task_id, task.status, task.block_kind,
        )
        return

    # ---- 2. Budget guard (persisted, survives restarts) ----
    max_steps = orch._settings.max_orchestration_steps
    next_count = task.orchestration_step_count + 1
    if next_count > max_steps:
        reason = f"max steps ({max_steps}) exceeded"
        db.update_task(
            task_id,
            status=TaskStatus.BLOCKED,
            block_kind=BlockKind.ESCALATED,
            note=reason,
        )
        orch._audit.log_escalation(task_id, "orchestrator", reason)
        return

    # ---- 3. Atomic transition: unblock + increment + mark in_progress ----
    db.update_task(
        task_id,
        status=TaskStatus.IN_PROGRESS,
        block_kind=None,
        note=None,
        orchestration_step_count=next_count,
    )

    # ---- 4. Run the agent subprocess ----
    agent = task.assigned_agent or _default_agent_for_root(task)
    if task.assigned_agent is None:
        db.update_task(task_id, assigned_agent=agent)

    prompt = _build_agent_prompt(orch, task, agent)
    try:
        result, report = orch._run_agent(task_id, agent, prompt)
    except Exception as exc:
        _fail(orch, task_id, note=f"agent invocation failed: {exc}")
        _enqueue_parent_if_waiting(orch, task_id)
        return

    # ---- 5. Classify outcome ----
    if not result.success or report is None:
        _fail(orch, task_id, note="agent session failed")
        _enqueue_parent_if_waiting(orch, task_id)
        return

    orch._log_step_result(task_id, result, report)

    if report.status == "blocked":
        _fail(orch, task_id, note=f"self-blocked: {report.output_summary}")
        _enqueue_parent_if_waiting(orch, task_id)
        return

    # ---- 6. Parse next step (reuses the existing parser) ----
    decision = orch._parse_next_step(report)

    orch._audit.log_orchestration_step(
        task_id, next_count, decision.model_dump(exclude_none=True),
    )

    # ---- 7. Dispatch on action ----
    if decision.action == "done":
        _complete(
            orch, task_id,
            note=decision.summary or report.output_summary,
            artifact_dir=report.artifact_dir,
        )
        orch._tracker.update_scorecard(agent)
        _enqueue_parent_if_waiting(orch, task_id)
        return

    if decision.action == "escalate":
        reason = decision.reason or "Escalated"
        db.update_task(
            task_id,
            status=TaskStatus.BLOCKED,
            block_kind=BlockKind.ESCALATED,
            note=reason,
        )
        orch._audit.log_escalation(task_id, agent, reason)
        # parent stays blocked(DELEGATED) until this task reaches a terminal.
        return

    if decision.action == "delegate":
        err = _validate_delegate(orch, decision)
        if err is not None:
            _fail(orch, task_id, note=f"invalid delegate: {err}")
            _enqueue_parent_if_waiting(orch, task_id)
            return
        from src.models import TaskRecord
        child_id = db.next_task_id()
        db.insert_task(TaskRecord(
            id=child_id,
            type=task.type,
            brief=decision.prompt or "",
            assigned_agent=decision.agent,
            parent_task_id=task_id,
            status=TaskStatus.PENDING,
        ))
        db.update_task(
            task_id,
            status=TaskStatus.BLOCKED,
            block_kind=BlockKind.DELEGATED,
            note=f"Delegated to {decision.agent} (child={child_id})",
        )
        if orch._queue is not None:
            orch._queue.put_nowait(child_id)
        return

    # ---- 8. Unknown action ----
    _fail(orch, task_id, note=f"unknown action: {decision.action}")
    _enqueue_parent_if_waiting(orch, task_id)


def _validate_delegate(orch: "Orchestrator", decision) -> str | None:
    """Return a human-readable error string if the delegate decision is
    unusable, or None if it's good to spawn."""
    if not decision.agent:
        return "missing agent name"
    workspace = orch._runtime.workspaces_dir / decision.agent
    if not workspace.exists():
        return f"no workspace for agent {decision.agent!r}"
    return None


def _default_agent_for_root(task) -> str:
    """Root tasks default to the Engineering Head as their assigned agent."""
    return "engineering_head"


def _build_agent_prompt(orch: "Orchestrator", task, agent: str) -> str:
    """Build the capabilities prompt for an EH decision step, or pass the
    brief verbatim for a worker. Prior steps are rebuilt from the DB so this
    works identically on first pickup and on post-delegation resumption."""
    from src.orchestrator.capabilities import build_capabilities_prompt
    if agent != "engineering_head":
        return task.brief
    agent_names, tiers = _list_candidate_agents(orch)
    agents_for_prompt = []
    for name in agent_names:
        enrollment = orch._db.get_enrollment(name)
        desc = enrollment["description"] if enrollment else name
        tier = tiers.get(name)
        agents_for_prompt.append({
            "name": name,
            "description": desc,
            "tier": tier.value if tier else "green",
        })
    prior_steps = _build_prior_steps_from_db(orch, task.id)
    return build_capabilities_prompt(
        brief=task.brief,
        agents=agents_for_prompt,
        step_number=task.orchestration_step_count + 1,  # 1-indexed for EH display
        max_steps=orch._settings.max_orchestration_steps,
        prior_steps=prior_steps,
    )


def _list_candidate_agents(orch: "Orchestrator"):
    """Return (agent_names, tiers_map) — same shape as orchestrator used."""
    if orch._runtime.workspaces_dir.exists():
        names = [
            d.name for d in orch._runtime.workspaces_dir.iterdir()
            if d.is_dir() and d.name != "engineering_head"
        ]
    else:
        names = []
    tiers = orch._tracker.get_all_tiers(names)
    return names, tiers


def _build_prior_steps_from_db(orch: "Orchestrator", task_id: str):
    """Reconstruct StepRecord[] for the EH by reading children's terminal
    outcomes from the DB. Only direct children of `task_id` count — each child
    is one past orchestration step. Order: creation order, 1-indexed."""
    from src.models import StepRecord
    steps: list[StepRecord] = []
    for i, child_id in enumerate(orch._db.get_children(task_id), start=1):
        child = orch._db.get_task(child_id)
        if child is None:
            continue
        success = child.status == TaskStatus.COMPLETED
        steps.append(StepRecord(
            step_number=i,
            agent=child.assigned_agent or "unknown",
            action=f"delegate: {(child.brief or '')[:100]}",
            result_summary=child.note or "(no summary)",
            success=success,
        ))
    return steps


def _complete(orch: "Orchestrator", task_id: str, *, note: str, artifact_dir: str | None = None) -> None:
    from datetime import datetime, timezone
    orch._db.update_task(
        task_id,
        status=TaskStatus.COMPLETED,
        block_kind=None,
        note=note,
        final_artifact_dir=artifact_dir,
        completed_at=datetime.now(timezone.utc).isoformat(),
    )
    orch._update_task_history(task_id)


def _fail(orch: "Orchestrator", task_id: str, *, note: str) -> None:
    from datetime import datetime, timezone
    orch._db.update_task(
        task_id,
        status=TaskStatus.FAILED,
        block_kind=None,
        note=note,
        completed_at=datetime.now(timezone.utc).isoformat(),
    )
    orch._update_task_history(task_id)


def _enqueue_parent_if_waiting(orch: "Orchestrator", task_id: str) -> None:
    """Idempotent: enqueue the parent only if it is actually waiting on
    THIS lineage (blocked+DELEGATED) AND all its children are now terminal."""
    task = orch._db.get_task(task_id)
    if task is None or task.parent_task_id is None:
        return
    parent = orch._db.get_task(task.parent_task_id)
    if parent is None or parent.status != TaskStatus.BLOCKED:
        return
    if parent.block_kind != BlockKind.DELEGATED:
        return
    siblings = [orch._db.get_task(cid) for cid in orch._db.get_children(parent.id)]
    if any(s is None or s.status not in TERMINAL_STATES for s in siblings):
        return
    queue = getattr(orch, "_queue", None)
    if queue is not None:
        queue.put_nowait(parent.id)
