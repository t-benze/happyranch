"""Distill adapter over retained capture, ordinary tasks and S2a publication.

Reconcile on knowledge reads/submission. The DB transaction binds a single
ordinary root before enqueue; a crash can only leave that SAME task pending.
No callback handler or execution/permission path is added here.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from fastapi import HTTPException

from runtime.infrastructure.audit_logger import AuditLogger
from runtime.knowledge.archives import ArchiveError, KnowledgeArchiveStore
from runtime.knowledge.curation import CurationError, build_curation_input
from runtime.knowledge.lifecycle_models import DistillOperation, DistillSubmission
from runtime.knowledge.sources import Coverage, RetiredSource, capture_retired_source
from runtime.models import TaskStatus
from runtime.orchestrator.orchestrator import completion_report_from_result_row

if TYPE_CHECKING:
    from runtime.daemon.org_state import OrgState
    from runtime.daemon.state import DaemonState


class DistillLifecycle:
    def __init__(self, org: OrgState, state: DaemonState) -> None:
        self.org = org
        self.state = state
        self.store = KnowledgeArchiveStore(org.db._conn, org.db._lock, org.slug)
        self.audit = AuditLogger(org.db)

    async def submit(self, body: DistillSubmission) -> DistillOperation:
        async with self.org.db_lock:
            operation = self.store.find_submission(body.submission_id)
            confirmation = body.model_dump(mode='json')
            if operation is not None:
                if json.loads(operation['confirmation_json']) != confirmation:
                    raise ArchiveError('knowledge_submission_conflict')
            else:
                capture = capture_retired_source(self.org.root, body.source_id)
                operation = self.store.submit(body.submission_id, capture, confirmation,
                                              processor_id=body.processor_id)
            if operation['state'] == 'Queued':
                self.audit.log_knowledge_distill(operation)
            self._reconcile(operation['operation_id'])
            return self.projection(operation['operation_id'])

    async def show(self, operation_id: str) -> DistillOperation:
        async with self.org.db_lock:
            self._reconcile(operation_id)
            return self.projection(operation_id)

    async def reconcile_all(self) -> None:
        async with self.org.db_lock:
            for operation in self.store.list_inflight():
                self._reconcile(operation['operation_id'])

    def _former_manager(self, source_id: str) -> str | None:
        # The retirement owner recorded its authenticated manager as agent.
        # Do not silently replace it with today's manager of a historical team.
        rows = self.org.db.get_audit_logs_by_action('agent_managed')
        actors = {row['agent'] for row in rows if isinstance(row['payload'], dict)
                  and row['payload'].get('action') == 'terminate'
                  and row['payload'].get('name') == source_id}
        return next(iter(actors)) if len(actors) == 1 else None

    def _dispatch(self, operation: dict) -> None:
        from runtime.daemon.routes.tasks import SubmitTask, persist_root_task
        capture = RetiredSource.model_validate_json(operation['capture_json'])
        processor = operation['processor_id'] or self._former_manager(operation['source_id'])
        if processor is None:
            self.store.fail(operation['operation_id'], 'processor_missing_choose_existing_owner')
            return
        self.store.select_processor(operation['operation_id'], processor)
        if processor == 'founder':
            self.store.fail(operation['operation_id'], 'processor_ineligible_choose_existing_owner')
            return
        try:
            brief = build_curation_input(capture)
            task = persist_root_task(self.org, SubmitTask(owner=processor, brief=brief),
                                     bind=lambda task: self.store.bind_task_uncommitted(
                                         operation['operation_id'], processor, task.id))
        except CurationError as error:
            self.store.fail(operation['operation_id'], error.code)
            return
        except HTTPException as error:
            # Native owner/team admission supplies the authentic, closed cause.
            code = error.detail.get('code') if isinstance(error.detail, dict) else None
            causes = {'unknown_owner', 'unknown_team', 'owner_team_mismatch', 'owner_required_for_human_team'}
            if code not in causes:
                raise
            self.store.fail(operation['operation_id'], 'processor_' + code)
            return
        self._enqueue(task.id)

    def _enqueue(self, task_id: str) -> None:
        from runtime.daemon.runner import enqueue_task
        self.state.queue.enqueue_if_absent(self.org.slug, task_id,
                                           publisher=lambda: enqueue_task(self.state, self.org.slug, task_id))

    def _reconcile(self, operation_id: str) -> None:
        # All native task/result reads and feature transitions share the SAME
        # database RLock. Route callers also hold the async admission lock.
        operation = self.store.get_operation(operation_id)
        if operation['state'] == 'Queued':
            self._dispatch(operation)
        with self.org.db._lock:
            operation = self.store.get_operation(operation_id)
            if operation['state'] == 'Processing':
                self._consume(operation)
            operation = self.store.get_operation(operation_id)
            archives = self.store.list_ready(source_id=operation['source_id']) if operation['state'] == 'Ready' else []
            archive = next((a for a in archives if a['operation_id'] == operation_id), None)
            self.audit.log_knowledge_distill(operation, archive)

    def _consume(self, operation: dict) -> None:
        db, operation_id = self.org.db, operation['operation_id']
        task = db.get_task(operation['task_id'])
        if task is None or task.assigned_agent != operation['processor_id'] or task.parent_task_id is not None:
            self.store.observe(operation_id, 'task_binding_unavailable', operation['session_id'])
            return
        session_id = task.current_session_id
        if session_id != operation['session_id'] or operation['phase'] == 'awaiting_task':
            self.store.observe(operation_id, 'awaiting_terminal_task', session_id)
        if task.status == TaskStatus.PENDING:
            # Repairs the commit->enqueue crash gap using the original task;
            # ordinary queue/claim ownership prevents duplicate execution.
            self._enqueue(task.id)
        if task.status in {TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.SUPERSEDED, TaskStatus.ESCALATED}:
            row = db.get_latest_task_result(task.id, operation['processor_id'], session_id) if session_id else None
            self.store.fail(operation_id, 'processor_task_' + task.status.value,
                            result_id=row['id'] if row else None)
            return
        if not session_id:
            return
        # A later session must not hide an earlier delegate/escalate/failure.
        # This ordinary root was allocated for ONE curation result. Inspect
        # its own persisted processor results, never task-wide latest fallback.
        rows = [row for row in db.get_task_results(task.id) if row['agent'] == operation['processor_id']]
        for candidate in rows:
            try:
                report = completion_report_from_result_row(task.id, candidate)
            except (ValueError, TypeError):
                self.store.observe(operation_id, 'completion_evidence_unavailable', session_id)
                return
            cause = None
            if candidate['status'] != 'completed':
                outcome = candidate['status'] if candidate['status'] in {'failed', 'cancelled', 'blocked', 'superseded'} else 'invalid_status'
                cause = 'processor_outcome_' + outcome
            elif report.decision is None or report.decision.action != 'done':
                action = report.decision.action if report.decision is not None else 'missing'
                cause = 'processor_decision_' + action
            if cause:
                self.store.observe(operation_id, 'processor_result_failed', candidate['session_id'])
                self.store.fail(operation_id, cause, result_id=candidate['id'])
                return
        if len(rows) != 1 or rows[0]['session_id'] != session_id:
            self.store.observe(operation_id, 'completion_evidence_unavailable' if task.status == TaskStatus.COMPLETED
                               else 'awaiting_terminal_task', session_id)
            return
        row = db.get_latest_task_result(task.id, operation['processor_id'], session_id)
        if row is None or row['id'] != rows[0]['id']:
            self.store.observe(operation_id, 'completion_evidence_unavailable', session_id)
            return
        if task.status != TaskStatus.COMPLETED:
            self.store.observe(operation_id, 'completion_received_awaiting_terminal', session_id)
            return
        # Consume ONLY persisted output_summary, never task.note, an output
        # file, decision.summary or caller-supplied result/task/session IDs.
        try:
            self.store.publish(operation_id, row['output_summary'], result_id=row['id'])
        except CurationError:
            pass  # S2a durably committed the closed validation failure.
        except ArchiveError as error:
            if str(error) != 'curation_publication_failed':
                raise

    def projection(self, operation_id: str) -> DistillOperation:
        operation = self.store.get_operation(operation_id)
        archive = next((a for a in self.store.list_ready(source_id=operation['source_id'])
                        if a['operation_id'] == operation_id), None) if operation['state'] == 'Ready' else None
        return DistillOperation(**{k: operation[k] for k in DistillOperation.model_fields
                                  if k not in {'coverage', 'failure', 'archive_id', 'archive_version'}},
                                coverage=[Coverage.model_validate(c) for c in json.loads(operation['capture_json'])['coverage']],
                                failure=json.loads(operation['failure_json']) if operation['failure_json'] else None,
                                archive_id=archive['archive_id'] if archive else None,
                                archive_version=archive['version'] if archive else None)
