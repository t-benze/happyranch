"""Internal human and agent receipts with separate current admission and saved proof."""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Literal, TYPE_CHECKING

from pydantic import Field, StrictInt, ValidationError

from runtime.daemon.startup_recovery import document_admission_validation
from runtime.infrastructure.workflow_schema import _records, _stored_json
from runtime.workflows.activation import ClosedRecord, SHA256, Token, WorkflowActivationError, _id
from runtime.workflows.artifacts import document_from_submission, require_document_subject, review_family
from runtime.workflows.draft_dispatch import canonical_bytes, digest
from runtime.workflows.submissions import _insert, _record
from runtime.workflows.templates import WorkflowTemplatePrincipal


if TYPE_CHECKING:
    from runtime.daemon.startup_recovery import RecoveryOutcome


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


class ArtifactHumanReviewCommand(HumanReviewCommand):
    format: Literal['workflow-human-review-command@2']


class RevisionHumanReviewCommand(HumanReviewCommand):
    format: Literal['workflow-human-review-command@3']
    expected_revision: StrictInt = Field(ge=2, le=2)


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


class ArtifactReviewReceiptProjection(ReviewReceiptProjection):
    format: Literal['workflow-review-receipt@2']


class RevisionReviewReceiptProjection(ReviewReceiptProjection):
    format: Literal['workflow-review-receipt@3']
    assignment_generation: StrictInt = Field(ge=2)


def recipient_receipt(receipt: dict) -> dict:
    """Project only a receipt already authenticated by its writer or complete graph."""
    proof = _stored_json(receipt['proof_bytes'], receipt['proof_digest'],
                         code='workflow_review_receipt_storage_corrupt')
    if proof['format'] in ('workflow-human-review-proof@1', 'workflow-human-review-proof@2', 'workflow-human-review-proof@3'):
        command = proof['command']
    elif proof['format'] in ('workflow-agent-review-proof@1', 'workflow-agent-review-proof@2', 'workflow-agent-review-proof@3'):
        command = proof['stage_event']['body']['stage']['command']
    else:
        raise ValueError('workflow_review_receipt_storage_corrupt')
    family = review_family(proof['scope'])
    expected = f'workflow-human-review-proof@{family}' if 'command' in proof else f'workflow-agent-review-proof@{family}'
    if proof['format'] != expected:
        raise ValueError('workflow_review_receipt_storage_corrupt')
    model = {1: ReviewReceiptProjection, 2: ArtifactReviewReceiptProjection, 3: RevisionReviewReceiptProjection}[family]
    return model.model_validate(dict(
        format=f'workflow-review-receipt@{family}', receipt_id=receipt['id'],
        **{key: receipt[key] for key in ('request_id', 'submission_id', 'submission_digest',
            'assignment_generation', 'request_scope_digest', 'outcome', 'created_at')},
        findings_bytes_base64=command['findings_bytes_base64'],
        findings_digest=command['findings_digest'])).model_dump()


def parse_human_review(command: object) -> dict:
    # Reuse the existing raw UTF8/base64 bounds, without entering the agent stage lane.
    from runtime.workflows.review_dispatch import parse_stage
    try:
        family = {'workflow-human-review-command@1': 1, 'workflow-human-review-command@2': 2,
                  'workflow-human-review-command@3': 3}.get(command.get('format'), 1) if isinstance(command, dict) else 1
        model = {1: HumanReviewCommand, 2: ArtifactHumanReviewCommand, 3: RevisionHumanReviewCommand}[family]
        parsed = model.model_validate(command).model_dump()
        parse_stage(dict(parsed, format=f'workflow-review-stage-command@{family}', action='stage-review'))
        if len(canonical_bytes(parsed)) > 2097152:
            raise ValueError('command bounds')
        return parsed
    except (ValidationError, ValueError) as exc:
        raise WorkflowActivationError('workflow_human_review_invalid_request') from exc


def _human_proof(request: dict, scope: dict, handoff: dict, command: dict, created_at: str) -> dict:
    return dict(format=f'workflow-human-review-proof@{review_family(scope)}',
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
        if proof['format'] != f'workflow-human-review-proof@{review_family(scope)}' or proof['operation_principal'] != 'human:founder':
            raise ValueError('workflow_review_receipt_storage_corrupt')
        if proof['command']['operation_key'] == operation_key:
            matches.append(receipt)
    return matches


def _validate_submission_subject(conn: sqlite3.Connection, submission: dict, scope: dict) -> None:
    """Supply stored namespace/provenance to the pure adapter, with caller-owned errors."""
    code = 'workflow_review_receipt_storage_corrupt'
    try:
        operations = _records(conn, 'SELECT * FROM workflow_submission_operations WHERE submission_id=?',
                              (submission['id'],))
        links = _records(conn, 'SELECT * FROM workflow_submission_result_links WHERE submission_id=?',
                         (submission['id'],))
        if len(operations) != 1 or len(links) != 1:
            raise ValueError(code)
        contributors = tuple(_records(conn, 'SELECT * FROM workflow_instance_contributors WHERE instance_id=?',
                                      (submission['instance_id'],)))
        artifact = document_from_submission(submission, org_namespace=operations[0]['org_slug'],
            pins={key: scope[key] for key in ('template', 'activation', 'authorization',
                                              'binding_snapshot_id', 'context_id')},
            source_result_id=links[0]['task_result_id'], contributors=contributors)
        require_document_subject(artifact, scope)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(code) from exc


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
    if (command['format'] != f'workflow-human-review-command@{review_family(scope)}'
            or created_at != stamp.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            or command['request_id'] != request['id'] or command['expected_revision'] != scope['revision']
            or command['request_scope_digest'] != request['request_scope_digest']
            or command['outcome'] not in scope['outcomes'] or request['status'] != command['outcome']
            or request['principal'] in contributors):
        raise ValueError(code)
    _validate_submission_subject(conn, submission, scope)
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
    _validate_submission_subject(conn, submission, scope)
    contributors = {r[0] for r in conn.execute('SELECT principal FROM workflow_instance_contributors WHERE instance_id=?',
                                              (scope['instance_id'],))}
    contributors |= {r[0] for r in conn.execute('SELECT principal FROM workflow_submission_operations WHERE instance_id=?',
                                               (scope['instance_id'],))}
    if request['principal'] in contributors:
        raise ValueError(code)
    proof = dict(format=f'workflow-agent-review-proof@{review_family(scope)}', scope=scope,
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
    from runtime.workflows.submissions import history_successor
    if scope['revision'] == 1 and history_successor(conn, scope['instance_id'], fulfilled=True):
        selectors.pop('workflow_current_assignments')
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
        self._startup_state = self._startup_org = self._startup_recovery = None

    def _human_principal(self, principal: WorkflowTemplatePrincipal) -> None:
        if (type(principal) is not WorkflowTemplatePrincipal
                or principal.task_id is not None or principal.session_id is not None):
            raise WorkflowActivationError('role_binding_not_authorized')
        self.org.workflow_activations._principal(principal)

    async def admit_human(self, *, principal: WorkflowTemplatePrincipal, command: object) -> dict:
        """Sign only with current server bearer proof in the existing guarded writer."""
        validate = document_admission_validation(self)
        from runtime.infrastructure.workflow_schema import _handoff_record, require_author_handoff_schema
        from runtime.workflows.review_dispatch import _same_value
        from runtime.workflows.submissions import validate_handoff_graph
        self._human_principal(principal)
        command = parse_human_review(command)
        store = self.org.workflow_submissions
        selection = dict(request_id=command['request_id'], task_agent=None, session_id=None,
                         human=True, owner=principal, include_signed=True)
        with self.db._lock:
            validate()
            self._human_principal(principal)
            store._select_request(self.db._conn, **selection)
        capture = self.org.workflow_authority.capture_admission()
        validate()
        async with store._lock:
            validate()
            async with self.org.db_lock:
                # Recheck selected scope/grant after await, before private H decode.
                validate()
                with self.db._lock:
                    validate()
                    self._human_principal(principal)
                    store._select_request(self.db._conn, **selection)
                with self.org.workflow_authority.admission_writer(capture, validate_admission=validate) as conn:
                    validate()
                    self._human_principal(principal)
                    request, row, intent = store._select_request(conn, **selection)
                    historical = not intent['is_current'] and request['status'] != 'pending'
                    if historical:
                        store._selected_authority(conn, intent, capture, {'collect-review'})
                    else:
                        store._authority(conn, intent, capture, {'collect-review'})
                    require_author_handoff_schema(conn, expected_org_slug=self.org.slug)
                    from runtime.workflows.submissions import subject_proof
                    handoff = subject_proof(conn, row)
                    graph = validate_handoff_graph(conn, row, handoff)
                    if graph is None or request['id'] not in graph['request_ids']:
                        raise ValueError('workflow_review_receipt_storage_corrupt')
                    current = store._projection(conn, row, capture, replayed=True)['current']
                    if not historical and (current['state'] != 'consumed' or current['reconciliation_required']):
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
                        if not historical:
                            collect_in_writer(store, conn, row['id'], capture)
                        self._human_principal(principal)
                        return existing[0]
                    if historical or keys or request['status'] != 'pending':
                        raise WorkflowActivationError('workflow_human_review_operation_conflict')
                    if (command['format'] != f'workflow-human-review-command@{review_family(scope)}'
                                or command['expected_revision'] != scope['revision']
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

    async def admit_completed(self, task_id: str, *, validate_admission: Callable[[], None] | None = None) -> str | None:
        """Consume authentic terminal proof under the original async locks."""
        validate = document_admission_validation(self) if validate_admission is None else validate_admission
        validate()
        store = self.org.workflow_submissions
        bridge = self._completed_bridge(task_id)
        validate()
        capture = self.org.workflow_authority.capture_admission()
        validate()
        async with store._lock:
            validate()
            async with self.org.db_lock:
                validate()
                return self._admit_completed_core(task_id, capture, bridge, validate_admission=validate)[0]

    def _completed_bridge(self, task_id: str) -> dict:
        # Native ownership selection precedes authority/schema/proof admission.
        with self.db._lock:
            self.org.workflow_submissions._read_schema_presence(self.db._conn)
            bridges = _records(self.db._conn, 'SELECT * FROM workflow_request_task_bridges WHERE task_id=?', (task_id,))
            if len(bridges) != 1:
                raise WorkflowActivationError('workflow_review_request_not_found')
            bridge = bridges[0]
        return bridge

    def _admit_completed_core(self, task_id: str, capture: Any, bridge: dict, *, recovery: Any = None,
                              validate_admission: Callable[[], None] | None = None
                              ) -> tuple[str | None, Literal['settled', 'local_hold', 'unknown']]:
        validate = document_admission_validation(self, recovery=recovery) if validate_admission is None else validate_admission
        validate()
        store = self.org.workflow_submissions
        with self.org.workflow_authority.admission_writer(capture, validate_admission=validate) as conn:
            validate()
            intent = self.org.workflow_reviews._intent(conn, task_id)
            from runtime.workflows.review_dispatch import _same_value
            if not _same_value(intent['bridge'], bridge) or intent['state'] != 'completed' or intent['cancellation_requested']:
                return None, 'local_hold'
            source = _record(conn, 'workflow_draft_dispatch_intents', intent['intent_id'])
            try:
                store._authority(conn, source, capture, {'collect-review'})
            except WorkflowActivationError as exc:
                if exc.code in {'workflow_author_handoff_action_not_authorized',
                                'workflow_author_handoff_source_not_current', 'role_binding_not_authorized'}:
                    return None, 'local_hold'
                raise
            if (capture.ready.generation != intent['authority_generation']
                    or capture.ready.snapshot_digest != intent['authority_digest']
                    or conn.execute('SELECT state FROM workflow_cutover_state WHERE singleton=1').fetchone()[0] != 'enabled'):
                return None, 'local_hold'
            request = _record(conn, 'workflow_review_requests', intent['request_id'])
            scope = intent['scope']
            if scope['kind'] != 'agent':
                raise WorkflowActivationError('workflow_review_request_not_found')
            completed = _completed_proof(conn, request, scope)
            if completed is None:
                return None, 'local_hold'
            validate()
            rows = _receipt_rows(request, scope, *completed)
            receipt = rows['workflow_review_receipts']
            if request['status'] == 'pending':
                for table, row in rows.items():
                    _insert(conn, table, row)
                conn.execute('UPDATE workflow_review_requests SET status=? WHERE id=? AND status=?',
                             (receipt['outcome'], request['id'], 'pending'))
            validate_agent_receipt(conn, dict(request, status=receipt['outcome']), scope)
            self.org.workflow_reviews._intent(conn, task_id)
            from runtime.workflows.revisions import selected_subject
            subject, _ = selected_subject(conn, scope)
            from runtime.workflows.review_join import collect_in_writer
            collection = collect_in_writer(store, conn, subject['id'], capture)
            return receipt['id'], self._collection_disposition(collection)

    def _completed_review_tasks(self) -> list[str] | None:
        with self.db._lock:
            tasks = [r[0] for r in self.db._conn.execute("SELECT b.task_id FROM workflow_request_task_bridges b "
                "JOIN workflow_review_requests q ON q.id=b.request_id WHERE b.state='completed' AND q.status='pending'")]
        return tasks

    async def discover_completed(self, *, validate_admission: Callable[[], None] | None = None) -> None:
        validate = document_admission_validation(self) if validate_admission is None else validate_admission
        validate()
        tasks = self._completed_review_tasks()
        validate()
        if tasks is None:
            return
        from runtime.workflows.review_join import HOLDS
        from runtime.workflows.authority import WorkflowAuthorityError
        for task_id in tasks:
            try:
                await self.admit_completed(task_id, validate_admission=validate)
            except (WorkflowActivationError, WorkflowAuthorityError) as exc:
                if exc.code not in HOLDS:
                    raise  # Corruption and unexpected failures remain visible.

    def discover_completed_native(self, recovery: Any) -> RecoveryOutcome:
        from runtime.daemon.startup_recovery import RecoveryOutcome
        validate = document_admission_validation(self, recovery=recovery)
        tasks = self._completed_review_tasks()
        validate()
        if tasks is None:
            return RecoveryOutcome('unknown', 'startup_receipt_selection_missing')
        from runtime.workflows.review_join import HOLDS
        from runtime.workflows.authority import WorkflowAuthorityError
        held = False
        for task_id in tasks:
            validate()
            try:
                bridge = self._completed_bridge(task_id)
                validate()
                _, disposition = self._admit_completed_core(
                    task_id, self.org.workflow_authority.capture_admission(), bridge, recovery=recovery, validate_admission=validate)
            except (WorkflowActivationError, WorkflowAuthorityError) as exc:
                if exc.code not in HOLDS:
                    raise
                held = True
                continue
            if disposition not in ('settled', 'local_hold'):
                return RecoveryOutcome('unknown', 'startup_receipt_outcome_unknown')
            held |= disposition == 'local_hold'
        validate()
        return RecoveryOutcome('local_hold' if held else 'settled')

    async def collection_current(self, handoff_id: str, *, validate_admission: Callable[[], None] | None = None) -> dict | None:
        """Internal service view; public signed/owner projection is a following unit."""
        validate = document_admission_validation(self) if validate_admission is None else validate_admission
        validate()
        # Preserve ordinary import-before-capture order; the core shares the
        # same writer for native startup without acquiring persistent locks.
        from runtime.workflows.review_join import collect_in_writer
        store = self.org.workflow_submissions
        capture = self.org.workflow_authority.capture_admission()
        validate()
        async with store._lock:
            validate()
            async with self.org.db_lock:
                validate()
                return self._collection_current_core(handoff_id, capture, validate_admission=validate)[0]

    @staticmethod
    def _collection_disposition(collection: dict | None) -> Literal['settled', 'local_hold', 'unknown']:
        # Classify the actual validated collector return, never a second SQL
        # pass. A selected operation without a collection outcome stays unknown.
        if collection is None:
            return 'unknown'
        if collection['validity'] == 'held' or collection['state'] in ('held', 'collecting', 'needs_revision'):
            return 'local_hold'
        if collection['validity'] == 'current' and collection['state'] == 'approved':
            return 'settled'
        return 'unknown'

    def _collection_current_core(self, handoff_id: str, capture: Any, *, recovery: Any = None,
                              validate_admission: Callable[[], None] | None = None
                                 ) -> tuple[dict | None, Literal['settled', 'local_hold', 'unknown']]:
        validate = document_admission_validation(self, recovery=recovery) if validate_admission is None else validate_admission
        validate()
        store = self.org.workflow_submissions
        from runtime.workflows.review_join import collect_in_writer
        with self.org.workflow_authority.admission_writer(capture, validate_admission=validate) as conn:
            validate()
            collection = collect_in_writer(store, conn, handoff_id, capture)
            return collection, self._collection_disposition(collection)

    def _collection_handoffs(self) -> list[str] | None:
        with self.db._lock:
            handoffs = [r[0] for r in self.db._conn.execute(
                'SELECT h.id FROM workflow_author_handoffs h JOIN workflow_rounds r '
                "ON r.submission_id=h.submission_id WHERE r.state='reviewing' ORDER BY h.id")]
            from runtime.workflows.revisions import exists
            if exists(self.db._conn):
                handoffs += [r[0] for r in self.db._conn.execute("SELECT v.id FROM workflow_document_revision_operations v JOIN workflow_rounds r ON r.instance_id=v.instance_id WHERE r.current_revision=2 AND r.state='reviewing'")]
        return handoffs

    async def discover_collections(self, *, validate_admission: Callable[[], None] | None = None) -> None:
        """Existing reaper recovers a committed last receipt with no service effect."""
        validate = document_admission_validation(self) if validate_admission is None else validate_admission
        validate()
        from runtime.workflows.review_join import HOLDS
        from runtime.workflows.authority import WorkflowAuthorityError
        handoffs = self._collection_handoffs()
        validate()
        if handoffs is None:
            return
        for handoff_id in handoffs:
            try:
                await self.collection_current(handoff_id, validate_admission=validate)
            except (WorkflowActivationError, WorkflowAuthorityError) as exc:
                if exc.code not in HOLDS:
                    raise

    def discover_collections_native(self, recovery: Any) -> RecoveryOutcome:
        """Original collection selection, after native receipt admission."""
        from runtime.daemon.startup_recovery import RecoveryOutcome
        validate = document_admission_validation(self, recovery=recovery)
        from runtime.workflows.review_join import HOLDS
        from runtime.workflows.authority import WorkflowAuthorityError
        handoffs = self._collection_handoffs()
        validate()
        if handoffs is None:
            return RecoveryOutcome('unknown', 'startup_collection_selection_missing')
        held = False
        for handoff_id in handoffs:
            validate()
            try:
                _, disposition = self._collection_current_core(
                    handoff_id, self.org.workflow_authority.capture_admission(), recovery=recovery, validate_admission=validate)
            except (WorkflowActivationError, WorkflowAuthorityError) as exc:
                if exc.code not in HOLDS:
                    raise
                held = True
                continue
            if disposition not in ('settled', 'local_hold'):
                return RecoveryOutcome('unknown', 'startup_collection_outcome_unknown')
            held |= disposition == 'local_hold'
        validate()
        return RecoveryOutcome('local_hold' if held else 'settled')
