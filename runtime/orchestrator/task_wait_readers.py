"""Bounded observations of direct child work; no execution/admission effects."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic
from types import SimpleNamespace
from typing import TYPE_CHECKING

from pydantic import ValidationError

from runtime.models import BlockKind, TaskRecord, TaskStatus
from runtime.orchestrator.task_terminal_readers import _child_landed_terminal_report

if TYPE_CHECKING:
    from runtime.infrastructure.database import Database


@dataclass
class ChildWaitBudget:
    """One response/page's additional observation budget, without refill.

    Bounds do not bound lock wait, SQL duration or existing response work.
    """
    inspected: int = 0
    spent: float = 0.0

    def exhausted(self, started: float) -> bool:
        return self.inspected >= 1000 or self.spent + monotonic() - started >= 0.250


def derive_child_wait(db: Database, parent: TaskRecord, budget: ChildWaitBudget) -> dict | None:
    """Caller reads/serializes parent in the SAME coherent_read_view.

    Reuse only dispatch-terminal recognition, never eligibility, admission,
    result consumption, Pause discovery or wakes. Operational errors propagate.
    """
    from runtime.orchestrator.run_step import TERMINAL_STATES

    if parent.status != TaskStatus.IN_PROGRESS or parent.block_kind != BlockKind.DELEGATED:
        return None
    started = monotonic()
    observed_at = datetime.now(timezone.utc).isoformat()
    def observation(state: str) -> dict:
        return {"state": state, "observed_at": observed_at}

    try:
        if budget.exhausted(started):
            return observation("unavailable")
        with db.coherent_read_view() as conn:
            ids = [row["id"] for row in conn.execute(
                "SELECT id FROM tasks WHERE parent_task_id = ? ORDER BY created_at, id LIMIT 257",
                (parent.id,),
            ).fetchall()]
            if len(ids) > 256 or len(ids) > 1000 - budget.inspected or budget.exhausted(started):
                return observation("unavailable")
            pending = nonpending = landed = False
            reader = SimpleNamespace(_db=db)
            for child_id in ids:
                if budget.exhausted(started):
                    return observation("unavailable")
                budget.inspected += 1
                try:
                    child = db.get_task(child_id)
                except ValidationError:
                    return observation("unavailable")
                if child is None or child.id != child_id or child.parent_task_id != parent.id:
                    return observation("unavailable")
                if child.status in TERMINAL_STATES:
                    continue
                if _child_landed_terminal_report(reader, child) is not None:
                    landed = True
                elif child.status == TaskStatus.PENDING:
                    pending = True
                else:
                    nonpending = True
            # A query/report may itself overrun the observation deadline.
            if budget.spent + monotonic() - started >= 0.250:
                return observation("unavailable")
        if nonpending:
            return observation("waiting_children")
        if pending:
            return observation("pending_children")
        if landed:
            return observation("child_results_received")
        return observation("continuation_pending")
    finally:
        # Existing response work between parents does not spend this budget.
        budget.spent += monotonic() - started
