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
