"""Exclusive draft/F5/ordinary task ownership before legacy recovery effects."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal, Any

from runtime.infrastructure.workflow_schema import _schema_snapshot
from runtime.infrastructure.workflow_task_ownership import selected_owner
from runtime.workflows.coding_authority_history import _Budget


@dataclass(frozen=True)
class WorkflowTaskOwnership:
    kind: Literal["legacy", "draft", "f5", "coding_source_waiting", "coding_carrier", "coding_attempt", "reconciliation_required"]
    intent_id: str | None = None
    state: str | None = None


def classify_task(db: Any, task_id: str, *, org_slug: str | None,
                  budget: _Budget | None = None) -> WorkflowTaskOwnership:
    """Read the persisted mark and selected scalar bindings; never history.

    The optional existing allowance is not replenished or used to scan history.
    Full workflow validation remains with admission/mutation/recovery owners.
    Missing storage/record, dual bindings and read errors cannot become legacy.
    """
    if budget is not None and type(budget) is not _Budget:
        return WorkflowTaskOwnership("reconciliation_required")
    try:
        with db._lock, _schema_snapshot(db._conn):
            kind, reference, state = selected_owner(db._conn, task_id, org_slug)
            service_name = {"draft": "_workflow_drafts", "f5": "_workflow_reviews"}.get(kind)
            if service_name is not None:
                service = getattr(db, service_name, None)
                if getattr(getattr(service, "org", None), "slug", None) != org_slug:
                    return WorkflowTaskOwnership("reconciliation_required", reference, state)
            return WorkflowTaskOwnership(kind, reference, state)
    except (ValueError, TypeError, sqlite3.DatabaseError):
        return WorkflowTaskOwnership("reconciliation_required")


def route_owned_task(orch: Any, task_id: str) -> bool:
    """Refuse all legacy effects for owned work, including completion decisions."""
    ownership = classify_task(orch._db, task_id, org_slug=getattr(orch, "_slug", None))
    if ownership.kind == "legacy":
        return False
    dispatcher = getattr(orch, "_workflow_drafts", None)
    if ownership.kind == "draft" and dispatcher is not None:
        dispatcher.dispatch(task_id)
    elif ownership.kind == 'f5':
        reviewer = getattr(orch, '_workflow_reviews', None)
        if reviewer is not None:
            reviewer.dispatch(task_id)
    elif ownership.kind == "coding_attempt":
        coding = getattr(orch, "_workflow_coding_dispatch", None)
        if coding is not None and coding.queue_admission_allowed(task_id):
            coding.dispatch(task_id)
    # An unavailable dispatcher/host is pending execution, never permission
    # to call legacy launch, retry, delegation or parent propagation. Existing
    # F5 producers remain deferred and reconciliation-owned.
    return True


def recover_owned_task(db: Any, queue: Any, slug: str, task_id: str, *, orchestrator: Any=None) -> bool:
    ownership = classify_task(db, task_id, org_slug=slug)
    if ownership.kind == "legacy":
        return False
    if ownership.kind in ('coding_attempt', 'coding_source_waiting', 'coding_carrier'):
        coding = getattr(orchestrator, '_workflow_coding_dispatch', None)
        if coding is not None:
            from runtime.workflows.coding_progression import reconcile_run
            reconcile_run(coding, ownership.intent_id, queue)
            if ownership.kind == 'coding_attempt':
                coding.notify_queued(task_id, queue)
    elif ownership.kind in ('draft', 'f5') and ownership.state == "queued":
        # This is discovery of the committed intent, not reinsertion or host
        # launch. All ownership effects occur at the dispatcher claim seam.
        queue.enqueue_if_absent(slug, task_id)
    elif ownership.kind == "draft":
        dispatcher = getattr(orchestrator, "_workflow_drafts", None)
        if dispatcher is not None:
            dispatcher.reconcile(task_id)
            current = classify_task(db, task_id, org_slug=slug)
            if current.kind == "draft" and current.state == "queued":
                queue.enqueue_if_absent(slug, task_id)
    elif ownership.kind == 'f5':
        reviewer = getattr(orchestrator, '_workflow_reviews', None)
        if reviewer is not None:
            reviewer.reconcile(task_id)
            current = classify_task(db, task_id, org_slug=slug)
            if current.kind == 'f5' and current.state == 'queued':
                queue.enqueue_if_absent(slug, task_id)
    return True


def rediscover_returned(coding: Any) -> None:
    """Replay only committed caller notifications, never a stage or PR action.

    Terminal coding sources are absent from ordinary nonterminal startup scans.
    Complete reader validation precedes any notification; literal old complete
    records without the additive return are deliberately left alone.
    """
    from runtime.workflows.coding_authority_history import _Budget
    from runtime.workflows.coding_progression import notify_returned
    db = coding.db
    with db.coherent_read_view() as conn:
        if conn.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='workflow_coding_runs'").fetchone() is None:
            return
        from runtime.infrastructure.workflow_schema import _read_workflow_schema
        _, inventory = _read_workflow_schema(conn, expected_org_slug=coding.org.slug, budget=_Budget())
        if inventory is None:
            return
        returned = tuple(key for key, entry in inventory.queued.runs.items() if entry['native'].get('returned'))
    for instance in returned:
        notify_returned(coding, instance)
