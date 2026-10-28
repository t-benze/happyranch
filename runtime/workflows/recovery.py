"""Exclusive draft/F5/ordinary task ownership before legacy recovery effects."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal, Any, TYPE_CHECKING

from runtime.infrastructure.workflow_schema import _read_workflow_schema, _schema_snapshot
from runtime.infrastructure.workflow_coding_schema import has_coding_schema_objects, _read_coding_data
from runtime.workflows.coding_authority_history import _Budget


if TYPE_CHECKING:
    from runtime.daemon.startup_recovery import RecoveryOutcome


@dataclass(frozen=True)
class WorkflowTaskOwnership:
    kind: Literal["legacy", "draft", "f5", "coding_source_waiting", "coding_carrier", "coding_attempt", "reconciliation_required"]
    intent_id: str | None = None
    state: str | None = None


def classify_task(db: Any, task_id: str, *, org_slug: str | None,
                  budget: _Budget | None = None) -> WorkflowTaskOwnership:
    """The persisted bridge is the discriminator; task names/types grant nothing.

    Direct/generic Database construction is still workflow-free. Missing,
    malformed or dual workflow closure remains owned and cannot fall through.
    This reader changes no task, event, claim, schema or legacy audit row.
    One original allowance spans this snapshot; document semantics compose
    immediately after complete validation without starting another history read.
    """
    db._lock.acquire(blocking=True)
    try:
        conn = db._conn
        with _schema_snapshot(conn):
            try:
                if budget is not None and type(budget) is not _Budget:
                    raise ValueError('workflow_coding_data_invalid_budget')
                budget = _Budget() if budget is None else budget
                # Complete setup/import/mapping originals validate before legacy recovery.
                # Native coding decisions/authority may be orphaned, with no old
                # root/draft/F5 alias or even any workflow tables. Refuse BEFORE
                # either legacy return, under the same lock and exception fence.
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_schema WHERE type='table'")
                          if row[0].startswith('workflow_')}
                coding_present = has_coding_schema_objects(conn)
                if coding_present:
                    if org_slug is None:
                        return WorkflowTaskOwnership("reconciliation_required")
                    # Unknown/case-changed tables, views, indexes and triggers must
                    # reach the complete reader before either legacy return.
                    _, inventory = _read_workflow_schema(conn, expected_org_slug=org_slug, budget=budget)
                else:
                    # Absent coding storage cannot accept setup metadata, even if native
                    # coding metadata has no workflow root/draft/F5 alias.
                    _read_coding_data(conn, layout='recovery', expected_org_slug=org_slug, budget=budget)
                if coding_present:
                    if inventory is None:
                        return WorkflowTaskOwnership("reconciliation_required")
                    saved = inventory.queued
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
                if not coding_present:
                    _read_workflow_schema(conn, expected_org_slug=org_slug, budget=budget)
                # Coding inventory already includes complete document validation
                # on this unchanged snapshot; do not reconstruct it again.
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
                    dispatcher._intent_from_validated_schema(conn, task_id)
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


def _publish_cold_document_task(dispatcher: Any, task_id: str, gate: Any,
                                 startup_owner: Any) -> RecoveryOutcome:
    """Native notification using the existing draft/F5 admission primitives.

    Keep the shipping notify_queued capture/live/intent/authority/Pause order,
    but expose each real hold instead of throwing away its None return. There
    is no second observation pass and no claim or asynchronous dispatch.
    """
    from runtime.daemon.startup_recovery import RecoveryOutcome
    from runtime.workflows.draft_dispatch import DraftOwnershipError
    from runtime.workflows.activation import WorkflowActivationError
    from runtime.workflows.authority import WorkflowAuthorityError
    from runtime.workflows.review_join import HOLDS
    from runtime.infrastructure.task_pause_controls import PauseControlError
    from runtime.daemon.startup_recovery import document_admission_validation
    validate = document_admission_validation(dispatcher, recovery=gate, sweep_owner=startup_owner)
    org = dispatcher.org
    with dispatcher._live_lock:
        validate()
        if task_id in dispatcher._live:
            return RecoveryOutcome('local_hold', 'startup_document_live_owner')
    try:
        with dispatcher.db._lock:
            validate()
            if dispatcher._intent(dispatcher.db._conn, task_id)['state'] != 'queued':
                return RecoveryOutcome('local_hold', 'startup_document_not_queued')
        validate()
        capture = org.workflow_authority.capture_admission()
        validate()
        sessions = tuple(org.sessions.iter_active())
        validate()
        with org.workflow_authority.admission_writer(capture, validate_admission=validate) as conn:
            validate()
            intent = dispatcher._intent(conn, task_id)
            if (intent['state'] != 'queued' or intent['host_launch_started']
                    or intent['session_id'] is not None or intent['host_execution_id'] is not None
                    or intent['final_result_id'] is not None):
                return RecoveryOutcome('local_hold', 'startup_document_owner_ineligible')
            pause = getattr(dispatcher.db, '_task_pause_store', None)
            if pause is not None:
                pause.held_uncommitted(task_id)
            dispatcher._eligible(conn, intent, capture, sessions)
    except (DraftOwnershipError, WorkflowActivationError, WorkflowAuthorityError, PauseControlError) as exc:
        if isinstance(exc, PauseControlError):
            detail = exc.detail
            code = detail.get('code') if isinstance(detail, dict) else None
        else:
            code = str(exc) if isinstance(exc, DraftOwnershipError) else exc.code
        if not isinstance(code, str) or code not in HOLDS | {'workflow_activation_author_pending', 'root_paused'}:
            raise
        return RecoveryOutcome('local_hold', code)
    validate()
    outcome = gate.publish_workflow_task(org, task_id, sweep_owner=startup_owner)
    # A refused renewal cannot undo an earlier actual insertion/SAME pending.
    validate()
    return outcome


def _recover_cold_owned_task(db: Any, queue: Any, slug: str, task_id: str,
                             orchestrator: Any, startup_owner: Any) -> bool:
    from runtime.daemon.startup_recovery import StartupRecovery, RecoveryOutcome
    gate = getattr(orchestrator, '_startup_recovery', None)
    org = gate.state.orgs.get(slug) if isinstance(gate, StartupRecovery) else None
    if org is None or org.db is not db or org.orchestrator is not orchestrator or gate.state.queue is not queue:
        raise RuntimeError('startup_workflow_attachment_unavailable')
    gate.require_workflow_owner(org, sweep_owner=startup_owner)
    # The retained sweep token closes before any subsequent owned effect on
    # stop/failure; its mutex does not span classification, writers or puts.
    with startup_owner.effect():
        ownership = classify_task(db, task_id, org_slug=slug)
        if ownership.kind == 'legacy':
            return False
        if ownership.kind == 'reconciliation_required':
            raise RuntimeError('startup_workflow_reconciliation_required')
        outcome = RecoveryOutcome('local_hold')
        if ownership.kind in ('coding_attempt', 'coding_source_waiting', 'coding_carrier'):
            coding = getattr(orchestrator, '_workflow_coding_dispatch', None)
            if coding is None or coding.org is not org or coding.db is not db:
                raise RuntimeError('startup_coding_owner_unavailable')
            # Complete classify_task above authenticates source/carrier/attempt.
            # P2 never notifies returned history, claims or advances a stage.
            if ownership.kind == 'coding_attempt':
                outcome = coding.notify_queued(task_id, queue, recovery=gate, startup_owner=startup_owner)
        else:
            if ownership.kind not in ('draft', 'f5'):
                raise RuntimeError('startup_workflow_owner_unknown')
            dispatcher = getattr(orchestrator, '_workflow_drafts' if ownership.kind == 'draft' else '_workflow_reviews', None)
            if dispatcher is None or dispatcher.org is not org or dispatcher.db is not db:
                raise RuntimeError('startup_document_owner_unavailable')
            current = ownership
            if ownership.state != 'queued':
                gate.require_workflow_owner(org, sweep_owner=startup_owner)
                dispatcher.reconcile(task_id, recovery=gate, startup_owner=startup_owner)
                current = classify_task(db, task_id, org_slug=slug)
                if current.kind != ownership.kind:
                    raise RuntimeError('startup_workflow_reconciliation_required')
            if current.state == 'queued':
                outcome = _publish_cold_document_task(dispatcher, task_id, gate, startup_owner)
        if not isinstance(outcome, RecoveryOutcome) or not outcome.positive:
            raise RuntimeError('startup_workflow_outcome_unavailable')
        if outcome.disposition == 'local_hold':
            startup_owner.local_hold()
        gate.require_workflow_owner(org, sweep_owner=startup_owner)
        return True


def recover_owned_task(db: Any, queue: Any, slug: str, task_id: str, *, orchestrator: Any=None,
                       startup_owner: Any=None) -> bool:
    if startup_owner is not None:
        return _recover_cold_owned_task(db, queue, slug, task_id, orchestrator, startup_owner)
    # A missing native binding cannot fall into ordinary progression at boot.
    from runtime.daemon.startup_recovery import require_ready
    require_ready(orchestrator)
    ownership = classify_task(db, task_id, org_slug=slug)
    if ownership.kind == "legacy":
        return False
    if ownership.kind in ('coding_attempt', 'coding_source_waiting', 'coding_carrier'):
        coding = getattr(orchestrator, '_workflow_coding_dispatch', None)
        from runtime.daemon.startup_recovery import StartupUnavailable, coding_admission_validation
        if (coding is None or coding.db is not db or coding.org.slug != slug
                or coding.org.orchestrator is not orchestrator):
            raise StartupUnavailable('startup_coding_attachment_changed')
        coding_admission_validation(coding)()
        if queue is not coding._startup_state.queue:
            raise StartupUnavailable('startup_coding_queue_changed')
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
    from runtime.daemon.startup_publication import current_publication
    publication = current_publication(coding.org.orchestrator)
    from runtime.daemon.startup_recovery import coding_admission_validation
    validate = coding_admission_validation(coding, publication=publication)
    db = coding.db
    with db.coherent_read_view() as conn:
        validate()
        if conn.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='workflow_coding_runs'").fetchone() is None:
            if publication is not None:
                publication.count('not_applicable')
            return
        from runtime.infrastructure.workflow_schema import _read_workflow_schema
        _, inventory = _read_workflow_schema(conn, expected_org_slug=coding.org.slug, budget=_Budget())
        if inventory is None:
            if publication is not None:
                publication.count('not_applicable')
            return
        returned = tuple(key for key, entry in inventory.queued.runs.items() if entry['native'].get('returned'))
        if not returned and publication is not None:
            publication.count('not_applicable')
    for instance in returned:
        validate()
        result = notify_returned(coding, instance, validate_admission=validate)
        if publication is not None and type(result) is not bool:
            raise RuntimeError('startup_returned_notification_outcome_unknown')
