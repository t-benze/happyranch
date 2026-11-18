"""Task verdict, carrier and authenticated terminal-report readers.

Shipping consumers and their patched globals remain in run_step.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from runtime.models import TaskRecord
    from runtime.orchestrator.orchestrator import Orchestrator


def _verdict_for_delegated(
    orch: "Orchestrator", task_id: str, *, success: bool,
) -> str:
    """Resolve the review_verdict string for a delegated subtask.

    An explicit structured ``CompletionReport.verdict`` is the worker's own
    workflow verdict and is preserved verbatim — including an explicitly blank
    value (readers treat blank/unknown as "unknown", never as approved). Only
    when no structured verdict is present does the caller's completion-status
    mapping apply (``success`` -> "approved", otherwise "rejected").
    """
    report = orch._db.get_latest_completion_report(task_id)
    if report is not None and report.verdict is not None:
        return report.verdict
    return "approved" if success else "rejected"


def _is_carrier(orch: "Orchestrator", parent: "TaskRecord") -> bool:
    """True only for a passive pipeline carrier in an enclosing fan-out.

    A fan-out can also dispatch a decision-capable manager.  Both are direct
    children of the fan-out parent, but the former retains the existing
    ``subtask`` type while the latter is minted as ``task`` and owns its own
    failure/revision decision.  Do not infer carrier status from ancestry
    alone: doing so would fail a real manager upward on its first child
    failure.
    """
    if parent.parent_task_id is None:
        return False
    grandparent = orch._db.get_task(parent.parent_task_id)
    if parent.task_type != "subtask" or grandparent is None:
        return False
    if grandparent.active_fanout is None:
        return False
    try:
        from runtime.orchestrator.fanout import FanoutState
        return parent.id in FanoutState.deserialize(
            grandparent.active_fanout
        ).children_ids
    except Exception:
        return False


def _owns_passive_chain(orch: "Orchestrator", task: "TaskRecord") -> bool:
    """An unfinished chain or passive fan-out parent cannot run a session.

    `_is_carrier` is a parent predicate: a plain fan-out worker also has the
    same type/ancestry, but has no children and must run its first session.
    Pipeline allocation atomically inserts the carrier and its first child.
    """
    return task.active_chain is not None or (
        bool(orch._db.get_children(task.id)) and _is_carrier(orch, task)
    )


def _progress_passive_chain_owner(orch: "Orchestrator", task: "TaskRecord") -> bool:
    """Handle a passive owner's dequeue without claiming an agent session.

    Landed-but-live legs keep their finalization owner. Terminal newest legs
    use the existing authenticated, at-most-once progression path. Historical
    terminal/cancelled owners are never repaired or resurrected here.
    """
    if not _owns_passive_chain(orch, task):
        return False
    from runtime.models import BlockKind, TaskStatus
    from runtime.orchestrator.run_step import TERMINAL_STATES, _enqueue_parent_if_waiting

    if (task.cancelled_at is None and task.status == TaskStatus.IN_PROGRESS
            and task.block_kind == BlockKind.DELEGATED):
        children = orch._db.get_children(task.id)
        newest = orch._db.get_task(children[-1]) if children else None
        if newest is not None and newest.status in TERMINAL_STATES:
            _enqueue_parent_if_waiting(orch, newest.id)
    return True


def _passive_parent_wake_handled(orch: "Orchestrator", parent: "TaskRecord") -> bool:
    """After sibling evaluation, retain passive owners or handle terminal aborts.

    Re-read after chain progression: its snapshot may still contain the chain
    that just cleared. A stale older-leg callback cannot wake an active owner.
    """
    from runtime.models import BlockKind, TaskStatus
    from runtime.orchestrator.run_step import (
        TERMINAL_STATES, _carrier_fail_immediate,
    )

    current = orch._db.get_task(parent.id)
    if (current is None or current.cancelled_at is not None
            or current.status != TaskStatus.IN_PROGRESS
            or current.block_kind != BlockKind.DELEGATED):
        return True
    if current.active_chain is not None:
        children = orch._db.get_children(current.id)
        newest = orch._db.get_task(children[-1]) if children else None
        if (newest is None or newest.status not in TERMINAL_STATES
                or newest.status == TaskStatus.COMPLETED):
            return True
        # Sibling evaluation has finished. A cancelled/superseded newest leg
        # cannot finish successfully, but must still release its owner through
        # the existing chain-clear and carrier-failure / manager-wake paths.
        if not orch._db.try_clear_chain(current.id, current.active_chain):
            return True
        return _carrier_fail_immediate(orch, current, newest.id)
    # NULL does not distinguish a matched final leg from an abort. The caller
    # that won the chain-clear CAS owns that outcome through terminal commit;
    # a concurrent dequeue must neither infer success nor run a carrier session.
    return _is_carrier(orch, current)


def _child_has_modern_fingerprint(child: "TaskRecord") -> bool:
    """True when ``child`` carries the modern ``(assigned_agent,
    current_session_id)`` fingerprint the historical completion contract
    requires.  Legacy rows (pre-THR-211) may lack either field."""
    return (
        child is not None
        and bool(child.assigned_agent)
        and bool(child.current_session_id)
    )


def _child_landed_terminal_report(orch: "Orchestrator", child: "TaskRecord"):
    """THR-211: return the exact authenticated CompletionReport for the
    child's CURRENT session, or None when it is not dispatch-terminal.

    Authority is deliberately narrow and session-safe: the exact
    ``(task_id, assigned_agent, current_session_id)`` triple — the same
    fingerprint the daemon boot sweep and zombie reaper use — with report
    status == ``completed``.  Fails closed on absent agent/session, no row,
    blocked reports (the blocked_on_job park is a live state owned by the
    resume flow), or any other status.  Never infers terminality from prose.

    The returned report is the one the chain/gate consumers must use: a newer
    unrelated (wrong-agent/wrong-session) row can never substitute for it.

    NOTE (TASK-5818): a None return is ambiguous — it covers both a genuinely
    legacy child with no ``(assigned_agent, current_session_id)`` fingerprint
    and a modern child whose exact report is missing/unacceptable.  Callers
    that decide between the legacy newest-row fallback and fail-closed
    behavior MUST distinguish via ``_child_has_modern_fingerprint``; only a
    genuine fingerprint absence may ever consult task-wide evidence for chain
    advancement.
    """
    if child is None or not child.assigned_agent or not child.current_session_id:
        return None
    report = orch._db.get_latest_completion_report(
        child.id, child.assigned_agent, child.current_session_id,
    )
    if report is None or report.status != "completed":
        return None
    if report.decision is not None and report.decision.action == 'workflow_start':
        # This genuine callback requests a coding start; it does not finish the
        # source task, including while readiness refuses allocation. Its own
        # waiting/closeout owner alone may release the source. Parent/Pause/
        # summary readers must not mistake the transport result for terminality.
        return None
    return report
