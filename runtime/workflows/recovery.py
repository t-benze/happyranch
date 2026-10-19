"""Exclusive draft/F5/ordinary task ownership before legacy recovery effects."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal, Any

from runtime.infrastructure.workflow_schema import validate_workflow_schema
from runtime.infrastructure.workflow_coding_schema import has_coding_schema_objects, validate_coding_data


@dataclass(frozen=True)
class WorkflowTaskOwnership:
    kind: Literal["legacy", "draft", "f5", "coding_source_waiting", "coding_carrier", "coding_attempt", "reconciliation_required"]
    intent_id: str | None = None
    state: str | None = None


def classify_task(db: Any, task_id: str, *, org_slug: str | None) -> WorkflowTaskOwnership:
    """The persisted bridge is the discriminator; task names/types grant nothing.

    Direct/generic Database construction is still workflow-free. Missing,
    malformed or dual workflow closure remains owned and cannot fall through.
    This reader changes no task, event, claim, schema or legacy audit row.
    """
    db._lock.acquire(blocking=True)
    try:
        conn = db._conn
        try:
            # Complete setup/import/mapping originals validate before legacy recovery.
            # Native coding decisions/authority may be orphaned, with no old
            # root/draft/F5 alias or even any workflow tables. Refuse BEFORE
            # either legacy return, under the same lock and exception fence.
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_schema WHERE type='table'")
                      if row[0].startswith('workflow_')}
            if has_coding_schema_objects(conn):
                if org_slug is None:
                    return WorkflowTaskOwnership("reconciliation_required")
                # Unknown/case-changed tables, views, indexes and triggers must
                # reach the complete reader before either legacy return.
                validate_workflow_schema(conn, expected_org_slug=org_slug)
            else:
                # No J means no setup family can be accepted, even if native
                # coding metadata has no workflow root/draft/F5 alias.
                validate_coding_data(conn, layout='recovery', expected_org_slug=org_slug)
            if has_coding_schema_objects(conn):
                from runtime.workflows.coding_start import queued_storage
                saved = queued_storage(conn, org_slug)
                if task_id in saved.owners:
                    kind, instance = saved.owners[task_id]
                    return WorkflowTaskOwnership(kind, instance, saved.runs[instance]["attempt"]["state"])
            if not tables:
                return WorkflowTaskOwnership("legacy")
            roots = conn.execute("SELECT id FROM workflow_instances WHERE root_task_id=?", (task_id,)).fetchall()
            bridges = conn.execute("SELECT * FROM workflow_request_task_bridges WHERE task_id=?", (task_id,)).fetchall()
            drafts = (conn.execute("SELECT * FROM workflow_draft_dispatch_intents WHERE task_id=?", (task_id,)).fetchall()
                      if "workflow_draft_dispatch_intents" in tables else [])
            if not roots and not bridges and not drafts:
                return WorkflowTaskOwnership("legacy")
            if org_slug is None or (drafts and bridges) or len(drafts) > 1 or len(bridges) > 1:
                return WorkflowTaskOwnership("reconciliation_required")
            validate_workflow_schema(conn, expected_org_slug=org_slug)
            if drafts:
                intent = drafts[0]
                task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
                if task is None or (intent["state"] == "queued" and (
                        task["status"] != "pending" or task["current_session_id"] is not None)):
                    return WorkflowTaskOwnership("reconciliation_required", intent["id"], intent["state"])
                if intent['admission_kind'] == 'revision':
                    operation = conn.execute('SELECT * FROM workflow_document_revision_operations WHERE draft_intent_id=? AND org_slug=? AND principal=? AND operation_key=? AND request_digest=?',
                        (intent['id'], org_slug, intent['admission_principal'], intent['operation_key'], intent['request_digest'])).fetchone()
                else:
                    operation = conn.execute(
                        "SELECT * FROM workflow_activation_operations WHERE org_slug=? AND principal=? "
                        "AND operation_key=? AND activation_id=? AND request_digest=?",
                        (org_slug, intent["admission_principal"], intent["operation_key"],
                         intent["activation_id"], intent["request_digest"]),
                    ).fetchone()
                if operation is None:
                    return WorkflowTaskOwnership("reconciliation_required", intent["id"], intent["state"])
                # The generic E validator checks schema/event integrity. The
                # shipping activation owner also proves canonical semantics,
                # template/authority/context closure and the original task.
                # Never enqueue or dispatch a merely rehashed corrupt graph.
                dispatcher = getattr(db, "_workflow_drafts", None)
                if dispatcher is None or dispatcher.org.slug != org_slug:
                    return WorkflowTaskOwnership("reconciliation_required", intent["id"], intent["state"])
                dispatcher._intent(conn, task_id)
                return WorkflowTaskOwnership("draft", intent["id"], intent["state"])
            if bridges:
                bridge = bridges[0]
                closure = conn.execute(
                    "SELECT o.id FROM workflow_dispatch_operations o "
                    "JOIN workflow_dispatch_outbox x ON x.operation_id=o.id AND x.request_id=o.request_id "
                    "JOIN workflow_review_requests q ON q.id=o.request_id AND q.round_id=o.round_id "
                    "JOIN workflow_rounds r ON r.id=o.round_id AND r.instance_id=o.instance_id "
                    "JOIN workflow_submissions s ON s.id=r.submission_id AND s.instance_id=r.instance_id "
                    "WHERE o.id=? AND o.org_slug=? AND o.instance_id=? AND o.request_id=? "
                    "AND q.principal=? AND q.assignment_generation=?",
                    (bridge["operation_id"], org_slug, bridge["instance_id"], bridge["request_id"],
                     bridge["assigned_principal"], bridge["assignment_generation"]),
                ).fetchall()
                if len(closure) == 1:
                    reviewer = getattr(db, '_workflow_reviews', None)
                    if reviewer is None or reviewer.org.slug != org_slug:
                        return WorkflowTaskOwnership('reconciliation_required')
                    reviewer._intent(conn, task_id)
                    return WorkflowTaskOwnership("f5", state=bridge["state"])
            return WorkflowTaskOwnership("reconciliation_required")
        except (ValueError, TypeError, sqlite3.DatabaseError):
            return WorkflowTaskOwnership("reconciliation_required")
    finally:
        db._lock.release()


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
    if ownership.kind == 'coding_attempt':
        coding = getattr(orchestrator, '_workflow_coding_dispatch', None)
        if coding is not None:
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
