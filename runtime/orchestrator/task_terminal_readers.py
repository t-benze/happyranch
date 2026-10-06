"""Unchanged task verdict, carrier and authenticated terminal-report readers.

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
    return report
