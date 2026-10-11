"""Internal agent receipt admission from staged findings and actual F5 terminal proof."""
from __future__ import annotations

import sqlite3
from typing import Any

from runtime.infrastructure.workflow_schema import _records, _stored_json
from runtime.workflows.activation import WorkflowActivationError, _id
from runtime.workflows.draft_dispatch import canonical_bytes, digest
from runtime.workflows.submissions import _insert, _record


def _completed_proof(conn: sqlite3.Connection, request: dict, scope: dict) -> tuple[dict, dict, dict] | None:
    """Authenticate full F5 history; stage, callback and terminal are separate witnesses."""
    from runtime.workflows.review_dispatch import _same_value, validate_review_dispatch_storage
    validate_review_dispatch_storage(conn, request_id=request['id'])
    code = 'workflow_review_receipt_storage_corrupt'
    bridge = _record_bridge(conn, request['id'])
    events = _records(conn, 'SELECT * FROM workflow_dispatch_events WHERE operation_id=? ORDER BY event_seq',
                      (bridge['operation_id'],))
    stages = [e for e in events if e['event_kind'] == 'review_staged']
    if len(stages) != 1 or not events or events[-1]['event_kind'] != 'completed' or bridge['state'] != 'completed':
        return None
    terminal = events[-1]
    body = _stored_json(terminal['event_bytes'], terminal['event_digest'], code=code)
    stage = _stored_json(stages[0]['event_bytes'], stages[0]['event_digest'], code=code)
    accepted = body['result']
    if (type(accepted) is not dict or type(accepted['id']) is not int or accepted['id'] <= 0
            or not _same_value(body['terminal_evidence'], {'host_quiescent': True})):
        raise ValueError(code)
    result = _record(conn, 'task_results', accepted['id'])
    task = _record(conn, 'tasks', bridge['task_id'])
    if (not _same_value(result, accepted['record']) or bridge['result_id'] != str(result['id'])
            or result['status'] != 'completed' or result['task_id'] != bridge['task_id']
            or result['agent'] != bridge['assigned_principal'] or result['session_id'] != bridge['session_id']
            or task['status'] != 'completed' or task['cancelled_at'] is not None
            or task['assigned_agent'] != result['agent'] or task['current_session_id'] != result['session_id']
            or stage['stage']['session_id'] != result['session_id']):
        raise ValueError(code)
    # A successful transport with manager effects or unresolved work is unsigned.
    if result['decision_json'] is not None or result['waiting_on_job_ids'] is not None:
        return None
    submission = _record(conn, 'workflow_submissions', scope['submission_id'])
    if (submission['instance_id'] != scope['instance_id'] or submission['revision'] != scope['revision']
            or submission['submission_digest'] != scope['submission_digest']
            or digest(submission['submission_bytes']) != scope['submission_digest']):
        raise ValueError(code)
    contributors = {r[0] for r in conn.execute('SELECT principal FROM workflow_instance_contributors WHERE instance_id=?',
                                              (scope['instance_id'],))}
    contributors |= {r[0] for r in conn.execute('SELECT principal FROM workflow_submission_operations WHERE instance_id=?',
                                               (scope['instance_id'],))}
    if request['principal'] in contributors:
        raise ValueError(code)
    proof = dict(format='workflow-agent-review-proof@1', scope=scope,
        request_scope_digest=request['request_scope_digest'],
        stage_event=dict(id=stages[0]['id'], digest=stages[0]['event_digest'], body=stage),
        terminal_event=dict(id=terminal['id'], digest=terminal['event_digest']),
        callback=accepted, result_id=result['id'], task_id=result['task_id'], session_id=result['session_id'],
        principal=result['agent'], outcome=stage['stage']['command']['outcome'])
    return proof, result, terminal


def _record_bridge(conn: sqlite3.Connection, request_id: str) -> dict:
    rows = _records(conn, 'SELECT * FROM workflow_request_task_bridges WHERE request_id=?', (request_id,))
    if len(rows) != 1:
        raise ValueError('workflow_review_receipt_storage_corrupt')
    return rows[0]


def _receipt_rows(request: dict, scope: dict, proof: dict, result: dict, terminal: dict) -> dict[str, dict]:
    raw = canonical_bytes(proof)
    result_raw = canonical_bytes(result)
    receipt_id = _id('workflow-review-receipt', request['id'], request['assignment_generation'])
    native_id = str(result['id'])  # Only the genuine authenticated INTEGER result enters the TEXT bridge.
    identity = dict(instance_id=scope['instance_id'], task_id=result['task_id'], session_id=result['session_id'])
    assignment = dict(**identity, role_key=scope['role'], principal=result['agent'], result_id=native_id,
                      generation=request['assignment_generation'], lifecycle='completed')
    return {
        'workflow_instance_tasks': dict(**identity, role_key=scope['role'],
                                       generation=request['assignment_generation'], state='completed'),
        'workflow_task_results': dict(**identity, id=native_id, principal=result['agent'],
                                     generation=request['assignment_generation'], lifecycle='completed',
                                     result_bytes=result_raw, result_digest=digest(result_raw)),
        'workflow_current_assignments': assignment,
        'workflow_review_receipts': dict(id=receipt_id, request_id=request['id'], submission_id=scope['submission_id'],
            submission_digest=scope['submission_digest'], assignment_generation=request['assignment_generation'],
            request_scope_digest=request['request_scope_digest'], proof_bytes=raw, proof_digest=digest(raw),
            outcome=proof['outcome'], supersedes_receipt_id=None, created_at=terminal['created_at']),
        'workflow_receipt_evidence': dict(receipt_id=receipt_id, signer_principal=result['agent'],
            task_id=result['task_id'], session_id=result['session_id'], result_id=native_id,
            context_id=scope['context_id'], binding_snapshot_id=scope['binding_snapshot_id'],
            round_revision=scope['revision'], proof_bytes=raw, proof_digest=digest(raw)),
    }


def validate_agent_receipt(conn: sqlite3.Connection, request: dict, scope: dict) -> None:
    """Match all rows and full immutable proof, including absence for unsigned requests."""
    from runtime.workflows.review_dispatch import _same_value, validate_review_dispatch_storage
    code = 'workflow_review_receipt_storage_corrupt'
    receipts = _records(conn, 'SELECT * FROM workflow_review_receipts WHERE request_id=?', (request['id'],))
    bridge = _record_bridge(conn, request['id'])
    selectors = {
        'workflow_instance_tasks': ('instance_id=? AND task_id=?', (scope['instance_id'], bridge['task_id'])),
        'workflow_task_results': ('instance_id=? AND task_id=?', (scope['instance_id'], bridge['task_id'])),
        'workflow_current_assignments': ('instance_id=? AND role_key=?', (scope['instance_id'], scope['role'])),
        'workflow_receipt_evidence': ('task_id=?', (bridge['task_id'],)),
    }
    actual = {table: _records(conn, f'SELECT * FROM {table} WHERE {where}', args)
              for table, (where, args) in selectors.items()}
    if request['status'] == 'pending':
        validate_review_dispatch_storage(conn, request_id=request['id'])
        if receipts or any(actual.values()):
            raise ValueError(code)
        return
    completed = _completed_proof(conn, request, scope)
    if len(receipts) != 1 or completed is None:
        raise ValueError(code)
    expected = _receipt_rows(request, scope, *completed)
    if (request['status'] != expected['workflow_review_receipts']['outcome']
            or not _same_value(receipts, [expected['workflow_review_receipts']])
            or any(not _same_value(actual[table], [expected[table]]) for table in selectors)):
        raise ValueError(code)


class WorkflowReviewReceiptStore:
    def __init__(self, org: Any) -> None:
        self.org, self.db = org, org.db

    async def admit_completed(self, task_id: str) -> str | None:
        """Join committed terminal proof under the existing authority writer; never launch or join."""
        store = self.org.workflow_submissions
        # Native ownership selection precedes authority/schema/proof admission.
        with self.db._lock:
            store._read_schema_presence(self.db._conn)
            bridges = _records(self.db._conn, 'SELECT * FROM workflow_request_task_bridges WHERE task_id=?', (task_id,))
            if len(bridges) != 1:
                raise WorkflowActivationError('workflow_review_request_not_found')
            bridge = bridges[0]
        capture = self.org.workflow_authority.capture_admission()
        async with store._lock:
            async with self.org.db_lock:
                with self.org.workflow_authority.admission_writer(capture) as conn:
                    intent = self.org.workflow_reviews._intent(conn, task_id)
                    from runtime.workflows.review_dispatch import _same_value
                    if not _same_value(intent['bridge'], bridge) or intent['state'] != 'completed' or intent['cancellation_requested']:
                        return None
                    source = _record(conn, 'workflow_draft_dispatch_intents', intent['intent_id'])
                    try:
                        store._authority(conn, source, capture, {'collect-review'})
                    except WorkflowActivationError as exc:
                        if exc.code in {'workflow_author_handoff_action_not_authorized',
                                        'workflow_author_handoff_source_not_current', 'role_binding_not_authorized'}:
                            return None
                        raise
                    if (capture.ready.generation != intent['authority_generation']
                            or capture.ready.snapshot_digest != intent['authority_digest']
                            or conn.execute('SELECT state FROM workflow_cutover_state WHERE singleton=1').fetchone()[0] != 'enabled'):
                        return None
                    request = _record(conn, 'workflow_review_requests', intent['request_id'])
                    scope = intent['scope']
                    if scope['kind'] != 'agent':
                        raise WorkflowActivationError('workflow_review_request_not_found')
                    completed = _completed_proof(conn, request, scope)
                    if completed is None:
                        return None
                    rows = _receipt_rows(request, scope, *completed)
                    receipt = rows['workflow_review_receipts']
                    if request['status'] == 'pending':
                        for table, row in rows.items():
                            _insert(conn, table, row)
                        conn.execute('UPDATE workflow_review_requests SET status=? WHERE id=? AND status=?',
                                     (receipt['outcome'], request['id'], 'pending'))
                    validate_agent_receipt(conn, dict(request, status=receipt['outcome']), scope)
                    self.org.workflow_reviews._intent(conn, task_id)
                    return receipt['id']

    async def discover_completed(self) -> None:
        with self.db._lock:
            tasks = [r[0] for r in self.db._conn.execute("SELECT b.task_id FROM workflow_request_task_bridges b "
                "JOIN workflow_review_requests q ON q.id=b.request_id WHERE b.state='completed' AND q.status='pending'")]
        for task_id in tasks:
            await self.admit_completed(task_id)
