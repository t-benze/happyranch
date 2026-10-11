"""Internal human and agent receipts with separate current admission and saved proof."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import Field, StrictInt, ValidationError

from runtime.infrastructure.workflow_schema import _records, _stored_json
from runtime.workflows.activation import ClosedRecord, SHA256, Token, WorkflowActivationError, _id
from runtime.workflows.draft_dispatch import canonical_bytes, digest
from runtime.workflows.submissions import _insert, _record
from runtime.workflows.templates import WorkflowTemplatePrincipal


class HumanReviewCommand(ClosedRecord):
    format: Literal['workflow-human-review-command@1']
    action: Literal['sign-review']
    operation_key: Token
    request_id: Token
    expected_revision: StrictInt = Field(ge=1, le=1)
    request_scope_digest: SHA256
    outcome: Literal['approved', 'changes_requested']
    findings_bytes_base64: str = Field(min_length=4, max_length=1398104)
    findings_digest: SHA256


class ReviewReceiptProjection(ClosedRecord):
    format: Literal['workflow-review-receipt@1']
    receipt_id: Token
    request_id: Token
    submission_id: Token
    submission_digest: SHA256
    assignment_generation: StrictInt = Field(ge=1, le=1)
    request_scope_digest: SHA256
    outcome: Literal['approved', 'changes_requested']
    findings_bytes_base64: str = Field(min_length=4, max_length=1398104)
    findings_digest: SHA256
    created_at: str = Field(min_length=1, max_length=64)


def recipient_receipt(receipt: dict) -> dict:
    """Project only a receipt already authenticated by its writer or complete graph."""
    proof = _stored_json(receipt['proof_bytes'], receipt['proof_digest'],
                         code='workflow_review_receipt_storage_corrupt')
    if proof['format'] == 'workflow-human-review-proof@1':
        command = proof['command']
    elif proof['format'] == 'workflow-agent-review-proof@1':
        command = proof['stage_event']['body']['stage']['command']
    else:
        raise ValueError('workflow_review_receipt_storage_corrupt')
    return ReviewReceiptProjection.model_validate(dict(
        format='workflow-review-receipt@1', receipt_id=receipt['id'],
        **{key: receipt[key] for key in ('request_id', 'submission_id', 'submission_digest',
            'assignment_generation', 'request_scope_digest', 'outcome', 'created_at')},
        findings_bytes_base64=command['findings_bytes_base64'],
        findings_digest=command['findings_digest'])).model_dump()


def parse_human_review(command: object) -> dict:
    # Reuse the existing raw UTF8/base64 bounds, without entering the agent stage lane.
    from runtime.workflows.review_dispatch import parse_stage
    try:
        parsed = HumanReviewCommand.model_validate(command).model_dump()
        parse_stage(dict(parsed, format='workflow-review-stage-command@1', action='stage-review'))
        if len(canonical_bytes(parsed)) > 2097152:
            raise ValueError('command bounds')
        return parsed
    except (ValidationError, ValueError) as exc:
        raise WorkflowActivationError('workflow_human_review_invalid_request') from exc


def _human_proof(request: dict, scope: dict, handoff: dict, command: dict, created_at: str) -> dict:
    return dict(format='workflow-human-review-proof@1',
        actor=dict(principal_kind='human', principal_id='founder', proof_kind='founder_bearer'),
        operation_principal='human:founder', command=command, request_digest=digest(canonical_bytes(command)),
        scope=scope, request_scope_digest=request['request_scope_digest'], pins=handoff['pins'],
        admission_authority=dict(handoff['admission_authority'], actions=['collect-review']), created_at=created_at)


def _human_receipt_row(request: dict, scope: dict, proof: dict) -> dict:
    raw = canonical_bytes(proof)
    if len(raw) > 16777216:
        raise ValueError('workflow_review_receipt_storage_corrupt')
    return dict(id=_id('workflow-review-receipt', request['id'], request['assignment_generation']),
        request_id=request['id'], submission_id=scope['submission_id'], submission_digest=scope['submission_digest'],
        assignment_generation=request['assignment_generation'], request_scope_digest=request['request_scope_digest'],
        proof_bytes=raw, proof_digest=digest(raw), outcome=proof['command']['outcome'],
        supersedes_receipt_id=None, created_at=proof['created_at'])


def _human_receipts_for_key(conn: sqlite3.Connection, *, org_slug: str, operation_key: str) -> list[dict]:
    rows = _records(conn, 'SELECT p.* FROM workflow_review_receipts p '
        'JOIN workflow_review_requests q ON q.id=p.request_id '
        'JOIN workflow_rounds r ON r.id=q.round_id '
        'JOIN workflow_submission_operations o ON o.submission_id=r.submission_id '
        "WHERE o.org_slug=? AND q.principal='founder'", (org_slug,))
    matches = []
    for receipt in rows:
        request = _record(conn, 'workflow_review_requests', receipt['request_id'])
        scope = _stored_json(request['request_scope_bytes'], request['request_scope_digest'],
                             code='workflow_review_receipt_storage_corrupt')
        if scope['kind'] != 'human':
            continue  # Kind is pinned data; an agent name cannot select the human lane.
        proof = _stored_json(receipt['proof_bytes'], receipt['proof_digest'],
                             code='workflow_review_receipt_storage_corrupt')
        if proof['format'] != 'workflow-human-review-proof@1' or proof['operation_principal'] != 'human:founder':
            raise ValueError('workflow_review_receipt_storage_corrupt')
        if proof['command']['operation_key'] == operation_key:
            matches.append(receipt)
    return matches


def validate_human_receipt(conn: sqlite3.Connection, request: dict, scope: dict, handoff: dict) -> None:
    """Authenticate closed historical data only; it supplies no current bearer authority."""
    from runtime.workflows.review_dispatch import _same_value
    code = 'workflow_review_receipt_storage_corrupt'
    if scope['kind'] != 'human' or scope['principal'] != 'founder':
        raise ValueError(code)
    receipts = _records(conn, 'SELECT * FROM workflow_review_receipts WHERE request_id=?', (request['id'],))
    # A human has neither a task/result/assignment nor an agent evidence bridge.
    if (conn.execute('SELECT 1 FROM workflow_current_assignments WHERE instance_id=? AND role_key=?',
                     (scope['instance_id'], scope['role'])).fetchone()
            or conn.execute('SELECT 1 FROM workflow_instance_tasks WHERE instance_id=? AND role_key=?',
                            (scope['instance_id'], scope['role'])).fetchone()
            or conn.execute("SELECT 1 FROM workflow_task_results WHERE instance_id=? AND principal='founder'",
                            (scope['instance_id'],)).fetchone()
            or conn.execute('SELECT 1 FROM workflow_receipt_evidence e JOIN workflow_review_receipts p '
                            'ON p.id=e.receipt_id WHERE p.request_id=?', (request['id'],)).fetchone()):
        raise ValueError(code)
    if request['status'] == 'pending':
        if receipts:
            raise ValueError(code)
        return
    if len(receipts) != 1:
        raise ValueError(code)
    receipt = receipts[0]
    if type(receipt['proof_bytes']) is not bytes or not 1 <= len(receipt['proof_bytes']) <= 16777216:
        raise ValueError(code)
    proof = _stored_json(receipt['proof_bytes'], receipt['proof_digest'], code=code)
    command = parse_human_review(proof['command'])
    created_at = proof['created_at']
    stamp = datetime.strptime(created_at, '%Y-%m-%dT%H:%M:%S.%fZ')
    submission = _record(conn, 'workflow_submissions', scope['submission_id'])
    contributors = {r[0] for r in conn.execute('SELECT principal FROM workflow_instance_contributors WHERE instance_id=?',
                                              (scope['instance_id'],))}
    contributors |= {r[0] for r in conn.execute('SELECT principal FROM workflow_submission_operations WHERE instance_id=?',
                                               (scope['instance_id'],))}
    if (created_at != stamp.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            or command['request_id'] != request['id'] or command['expected_revision'] != scope['revision']
            or command['request_scope_digest'] != request['request_scope_digest']
            or command['outcome'] not in scope['outcomes'] or request['status'] != command['outcome']
            or request['principal'] in contributors
            or submission['instance_id'] != scope['instance_id']
            or not _same_value(submission['revision'], scope['revision'])
            or submission['submission_digest'] != scope['submission_digest']
            or digest(submission['submission_bytes']) != scope['submission_digest']):
        raise ValueError(code)
    submission['submission_bytes'].decode('utf8')
    expected = _human_proof(request, scope, handoff, command, created_at)
    if (not _same_value(proof, expected)
            or not _same_value(receipt, _human_receipt_row(request, scope, expected))
            or not _same_value(_human_receipts_for_key(conn, org_slug=scope['org_slug'],
                                operation_key=command['operation_key']), [receipt])):
        raise ValueError(code)


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

    def _human_principal(self, principal: WorkflowTemplatePrincipal) -> None:
        if (type(principal) is not WorkflowTemplatePrincipal
                or principal.task_id is not None or principal.session_id is not None):
            raise WorkflowActivationError('role_binding_not_authorized')
        self.org.workflow_activations._principal(principal)

    async def admit_human(self, *, principal: WorkflowTemplatePrincipal, command: object) -> dict:
        """Sign only with current server bearer proof in the existing guarded writer."""
        from runtime.infrastructure.workflow_schema import _handoff_record, require_author_handoff_schema
        from runtime.workflows.review_dispatch import _same_value
        from runtime.workflows.submissions import validate_handoff_graph
        self._human_principal(principal)
        command = parse_human_review(command)
        store = self.org.workflow_submissions
        selection = dict(request_id=command['request_id'], task_agent=None, session_id=None,
                         human=True, owner=principal, include_signed=True)
        with self.db._lock:
            self._human_principal(principal)
            store._select_request(self.db._conn, **selection)
        capture = self.org.workflow_authority.capture_admission()
        async with store._lock:
            async with self.org.db_lock:
                # Recheck selected scope/grant after await, before private H decode.
                with self.db._lock:
                    self._human_principal(principal)
                    store._select_request(self.db._conn, **selection)
                with self.org.workflow_authority.admission_writer(capture) as conn:
                    self._human_principal(principal)
                    request, row, intent = store._select_request(conn, **selection)
                    store._authority(conn, intent, capture, {'collect-review'})
                    require_author_handoff_schema(conn, expected_org_slug=self.org.slug)
                    handoff = _handoff_record(conn, row, expected_org_slug=self.org.slug)
                    graph = validate_handoff_graph(conn, row, handoff)
                    if graph is None or request['id'] not in graph['request_ids']:
                        raise ValueError('workflow_review_receipt_storage_corrupt')
                    current = store._projection(conn, row, capture, replayed=True)['current']
                    if current['state'] != 'consumed' or current['reconciliation_required']:
                        raise WorkflowActivationError('workflow_author_handoff_source_not_current')
                    scope = _stored_json(request['request_scope_bytes'], request['request_scope_digest'],
                                         code='workflow_review_receipt_storage_corrupt')
                    if scope['kind'] != 'human' or scope['principal'] != principal.principal_id:
                        raise WorkflowActivationError('workflow_review_request_not_found')
                    # Current capture is checked by the owned writer and against the full
                    # saved H authority closure. Historical proof never replaces this check.
                    fields = {'dependency': ('kind', 'org_namespace', 'profile_name', 'consumer_identity', 'bound_generation', 'state'),
                              'store': ('kind', 'profile_name', 'generation', 'profile_digest', 'state'),
                              'registry': ('kind', 'profile_name', 'published_generation')}
                    authority = dict(ready=dict(namespace=capture.ready.namespace, generation=capture.ready.generation,
                        snapshot_bytes_hex=capture.ready.snapshot_bytes.hex(), snapshot_digest=capture.ready.snapshot_digest),
                        pointer=dict(zip(('current_generation', 'journal_id', 'snapshot_digest', 'state', 'profile_fence'),
                                         capture.pointer, strict=True)),
                        profiles=[dict(name=name, digest=dg) for name, dg in capture.profile_digests],
                        mirror=[dict(zip(fields[item[0]], item, strict=True)) for item in capture.profile_mirror],
                        cutover='enabled', actions=['collect-review'])
                    if not _same_value(authority, dict(handoff['admission_authority'], actions=['collect-review'])):
                        raise WorkflowActivationError('workflow_activation_authority_stale')
                    existing = _records(conn, 'SELECT * FROM workflow_review_receipts WHERE request_id=?', (request['id'],))
                    keys = _human_receipts_for_key(conn, org_slug=self.org.slug, operation_key=command['operation_key'])
                    if existing:
                        saved = _stored_json(existing[0]['proof_bytes'], existing[0]['proof_digest'],
                                             code='workflow_review_receipt_storage_corrupt')
                        if not _same_value(saved['command'], command) or not _same_value(keys, existing):
                            raise WorkflowActivationError('workflow_human_review_operation_conflict')
                        from runtime.workflows.review_join import collect_in_writer
                        collect_in_writer(store, conn, row['id'], capture)
                        self._human_principal(principal)
                        return existing[0]
                    if keys or request['status'] != 'pending':
                        raise WorkflowActivationError('workflow_human_review_operation_conflict')
                    if (command['expected_revision'] != scope['revision']
                            or command['request_scope_digest'] != request['request_scope_digest']
                            or command['outcome'] not in scope['outcomes']):
                        raise WorkflowActivationError('workflow_human_review_invalid_request')
                    created_at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                    proof = _human_proof(request, scope, handoff, command, created_at)
                    receipt = _human_receipt_row(request, scope, proof)
                    _insert(conn, 'workflow_review_receipts', receipt)
                    conn.execute('UPDATE workflow_review_requests SET status=? WHERE id=? AND status=?',
                                 (command['outcome'], request['id'], 'pending'))
                    validate_handoff_graph(conn, row, handoff)
                    from runtime.workflows.review_join import collect_in_writer
                    collect_in_writer(store, conn, row['id'], capture)
                    self._human_principal(principal)
                    return receipt

    async def admit_completed(self, task_id: str) -> str | None:
        """Consume authentic terminal proof and all-current collection in the existing writer."""
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
                    handoffs = _records(conn, 'SELECT * FROM workflow_author_handoffs WHERE submission_id=?',
                                        (scope['submission_id'],))
                    if len(handoffs) != 1:
                        raise ValueError('workflow_review_receipt_storage_corrupt')
                    from runtime.workflows.review_join import collect_in_writer
                    collect_in_writer(store, conn, handoffs[0]['id'], capture)
                    return receipt['id']

    async def discover_completed(self) -> None:
        with self.db._lock:
            tasks = [r[0] for r in self.db._conn.execute("SELECT b.task_id FROM workflow_request_task_bridges b "
                "JOIN workflow_review_requests q ON q.id=b.request_id WHERE b.state='completed' AND q.status='pending'")]
        from runtime.workflows.review_join import HOLDS
        from runtime.workflows.authority import WorkflowAuthorityError
        for task_id in tasks:
            try:
                await self.admit_completed(task_id)
            except (WorkflowActivationError, WorkflowAuthorityError) as exc:
                if exc.code not in HOLDS:
                    raise  # Corruption and unexpected failures remain visible.

    async def collection_current(self, handoff_id: str) -> dict | None:
        """Internal service view; public signed/owner projection is a following unit."""
        from runtime.workflows.review_join import collect_in_writer
        store = self.org.workflow_submissions
        capture = self.org.workflow_authority.capture_admission()
        async with store._lock:
            async with self.org.db_lock:
                with self.org.workflow_authority.admission_writer(capture) as conn:
                    return collect_in_writer(store, conn, handoff_id, capture)

    async def discover_collections(self) -> None:
        """Existing reaper recovers a committed last receipt with no service effect."""
        from runtime.workflows.review_join import HOLDS
        from runtime.workflows.authority import WorkflowAuthorityError
        with self.db._lock:
            handoffs = [r[0] for r in self.db._conn.execute(
                'SELECT h.id FROM workflow_author_handoffs h JOIN workflow_rounds r '
                "ON r.submission_id=h.submission_id WHERE r.state='reviewing' ORDER BY h.id")]
        for handoff_id in handoffs:
            try:
                await self.collection_current(handoff_id)
            except (WorkflowActivationError, WorkflowAuthorityError) as exc:
                if exc.code not in HOLDS:
                    raise
