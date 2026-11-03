"""Exclusive draft/F5/ordinary task ownership before legacy recovery effects."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal, Any, TYPE_CHECKING

from runtime.infrastructure.workflow_schema import _schema_snapshot
from runtime.infrastructure.workflow_task_ownership import selected_owner
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


def rediscover_returned(coding: Any, *, queue: Any = None) -> None:
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
        result = notify_returned(coding, instance, queue=queue, validate_admission=validate)
        if publication is not None and type(result) is not bool:
            raise RuntimeError('startup_returned_notification_outcome_unknown')
