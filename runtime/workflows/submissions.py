"""Original-current author reservations and proof-bound same-operation consumption."""
from __future__ import annotations

import asyncio
import base64
import json
import sqlite3
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import Field, StrictBool, StrictInt, ValidationError

from runtime.infrastructure.workflow_schema import (
    _DRAFT_MUTABLE, _handoff_record, _records, _stored_json,
    require_author_handoff_schema, validate_workflow_schema,
)
from runtime.models import TaskRecord
from runtime.workflows.activation import (
    ClosedRecord, SHA256, Token, WorkflowActivationError, _id,
)
from runtime.workflows.draft_dispatch import canonical_bytes, digest
from runtime.workflows.templates import WorkflowTemplateStore, _document_contract


class AuthorHandoffCommand(ClosedRecord):
    format: Literal['workflow-author-handoff-command@1']
    operation_key: Token
    activation_id: Token
    expected_revision: StrictInt = Field(ge=1, le=1)
    submission_bytes_base64: str = Field(min_length=4, max_length=1398104)
    submission_digest: SHA256


class AuthorHandoffCore(ClosedRecord):
    handoff_id: Token
    reserved_submission_id: Token
    instance_id: Token
    revision: StrictInt = Field(ge=1, le=1)
    candidate_digest: SHA256
    request_digest: SHA256
    created_at: str = Field(min_length=27, max_length=27)


class AuthorHandoffGraph(ClosedRecord):
    submission_id: Token
    round_id: Token
    request_ids: list[Token] = Field(min_length=1, max_length=3)
    reviewer_task_ids: list[Token] = Field(max_length=3)


class AuthorHandoffCurrent(ClosedRecord):
    state: Literal['pending', 'held', 'consumed', 'failed', 'cancelled']
    pending: StrictBool
    reconciliation_required: StrictBool
    reason: Literal['awaiting_callback', 'awaiting_terminal_proof', 'authority_not_current',
                    'source_not_current', 'new_runs_disabled', 'source_uncertain',
                    'source_failed', 'source_cancelled', 'consumed']
    graph: AuthorHandoffGraph | None


class AuthorHandoffReceipt(ClosedRecord):
    format: Literal['workflow-author-handoff-receipt@1']
    core: AuthorHandoffCore
    replayed: StrictBool
    current: AuthorHandoffCurrent


def _record(conn: sqlite3.Connection, table: str, identity: str) -> dict:
    rows = _records(conn, f'SELECT * FROM {table} WHERE id=?', (identity,))
    if len(rows) != 1:
        raise WorkflowActivationError('workflow_author_handoff_storage_corrupt')
    return rows[0]


def _hex(record: dict) -> dict:
    return {k: v.hex() if isinstance(v, bytes) else v for k, v in record.items()}


def _insert(conn: sqlite3.Connection, table: str, record: dict) -> None:
    conn.execute(f"INSERT INTO {table} ({','.join(record)}) VALUES ({','.join('?' for _ in record)})",
                 tuple(record.values()))


def _source(conn: sqlite3.Connection, intent: dict) -> tuple[dict, dict, dict, Any, dict]:
    activation = _record(conn, 'workflow_activations', intent['activation_id'])
    context = _record(conn, 'workflow_contexts', intent['context_id'])
    binding = _record(conn, 'workflow_binding_snapshots', intent['binding_snapshot_id'])
    authorization = _record(conn, 'workflow_authorization_revisions', binding['authorization_revision_id'])
    grant = json.loads(authorization['authority_bytes'])
    template = WorkflowTemplateStore._read_version(conn, identity_id=activation['template_identity_id'],
        version=grant['template']['version'], expected_version_id=activation['template_version_id'])
    contract = _document_contract(template)
    if contract is None or contract['format'] != 'workflow-document-contract@3':
        raise WorkflowActivationError('workflow_author_handoff_action_not_authorized')
    return activation, context, binding, template, authorization


def _source_result(conn: sqlite3.Connection, intent: dict) -> dict | None:
    """Authenticate the original INTEGER callback and committed terminal, never PID absence."""
    if intent['state'] != 'completed' or type(intent['final_result_id']) is not int:
        return None
    rows = _records(conn, 'SELECT * FROM task_results WHERE id=?', (intent['final_result_id'],))
    if len(rows) != 1:
        return None
    result = rows[0]
    if (result['task_id'] != intent['task_id'] or result['agent'] != intent['assigned_principal']
            or result['session_id'] != intent['session_id'] or result['status'] != 'completed'):
        return None
    callback = _records(conn, "SELECT * FROM workflow_draft_dispatch_events WHERE intent_id=? AND event_kind='callback_recorded'",
                        (intent['id'],))
    terminal = _records(conn, "SELECT * FROM workflow_draft_dispatch_events WHERE intent_id=? AND event_kind IN ('completed','host_reconciled') ORDER BY event_seq",
                        (intent['id'],))
    if len(callback) != 1 or not terminal:
        return None
    accepted = _stored_json(callback[0]['event_bytes'], callback[0]['event_digest'],
                            code='workflow_author_handoff_storage_corrupt')['result']
    last = _stored_json(terminal[-1]['event_bytes'], terminal[-1]['event_digest'],
                        code='workflow_author_handoff_storage_corrupt')
    task = _record(conn, 'tasks', intent['task_id'])
    if (callback[0]['result_id'] != result['id'] or callback[0]['callback_accepted'] != 1
            or accepted['record'] != result or accepted['id'] != result['id']
            or accepted['digest'] != digest(canonical_bytes(result))
            or last['after']['state'] != 'completed' or last['after']['final_result_id'] != result['id']
            or last['terminal_evidence'] != {'host_quiescent': True}
            or last['result'] != accepted or task['status'] != 'completed'
            or intent['cancellation_requested']):
        return None
    return result


def review_scope(*, row: dict, proof: dict, context: dict, contract: dict, role: str,
                 request_id: str, round_id: str, binding: dict) -> dict:
    """Only the selected recipient's input bytes and pins enter its scope."""
    inputs = [item for item in context['inputs'] if role in item['pin']['recipients']]
    return dict(format='workflow-review-request@1', org_slug=row['org_slug'], instance_id=row['instance_id'],
                handoff_id=row['id'], submission_id=row['submission_id'], submission_digest=row['candidate_digest'],
                revision=1, round_id=round_id, request_id=request_id, role=role,
                kind=contract['role_kinds'][role], principal=binding['principal'], team=binding['team'],
                assignment_generation=1, template=proof['pins']['template'],
                activation=proof['pins']['activation'], authorization=proof['pins']['authorization']['id'],
                binding_snapshot_id=proof['pins']['binding']['id'], context_id=proof['pins']['context']['id'],
                output=contract['output'], outcomes=contract['outcomes'],
                purpose='Independently review the pinned immutable document; requests remain unsigned.',
                allowed_actions=['collect-review'], inputs=inputs)


def validate_handoff_graph(conn: sqlite3.Connection, row: dict, proof: dict) -> dict | None:
    try:
        return _validate_handoff_graph(conn, row, proof)
    except (KeyError, TypeError, AttributeError, ValueError, sqlite3.DatabaseError) as exc:
        raise ValueError('workflow_author_handoff_storage_corrupt') from exc


def _validate_handoff_graph(conn: sqlite3.Connection, row: dict, proof: dict) -> dict | None:
    """Closed graph oracle: all declared requests and all actual agent tasks in role order."""
    code = 'workflow_author_handoff_storage_corrupt'
    operations = _records(conn, 'SELECT * FROM workflow_submission_operations WHERE submission_id=?', (row['submission_id'],))
    rounds = _records(conn, 'SELECT * FROM workflow_rounds WHERE instance_id=? AND current_revision=1', (row['instance_id'],))
    if not operations:
        if rounds or conn.execute('SELECT 1 FROM workflow_submissions WHERE id=?', (row['submission_id'],)).fetchone():
            raise ValueError(code)
        return None
    if len(operations) != 1 or len(rounds) != 1:
        raise ValueError(code)
    intent = _record(conn, 'workflow_draft_dispatch_intents', row['intent_id'])
    if _source_result(conn, intent) is None:
        raise ValueError(code)
    round_ = rounds[0]
    if (round_['submission_id'] != row['submission_id'] or round_['state'] != 'reviewing'
            or round_['id'] != _id('workflow-round', row['instance_id'], 1, row['submission_id'])):
        raise ValueError(code)
    context = json.loads(_record(conn, 'workflow_contexts', intent['context_id'])['context_bytes'])
    contract = context['document_contract']
    bindings = context['binding']['bindings']
    requests = _records(conn, 'SELECT * FROM workflow_review_requests WHERE round_id=?', (round_['id'],))
    expected = []
    tasks = []
    if len(requests) != len(contract['reviewer_roles']):
        raise ValueError(code)
    for role in contract['reviewer_roles']:
        principal = bindings[role]['principal']
        selected = [q for q in requests if q['principal'] == principal and q['assignment_generation'] == 1]
        if len(selected) != 1:
            raise ValueError(code)
        q = selected[0]
        scope = review_scope(row=row, proof=proof, context=context, contract=contract, role=role,
                             request_id=q['id'], round_id=round_['id'], binding=bindings[role])
        if (q['id'] != _id('workflow-request', round_['id'], role, principal, 1)
                or q['status'] != 'pending' or q['supersedes_request_id'] is not None
                or canonical_bytes(scope) != q['request_scope_bytes']
                or digest(q['request_scope_bytes']) != q['request_scope_digest']):
            raise ValueError(code)
        expected.append(q['id'])
        bridges = _records(conn, 'SELECT * FROM workflow_request_task_bridges WHERE request_id=?', (q['id'],))
        dispatch = _records(conn, 'SELECT * FROM workflow_dispatch_operations WHERE request_id=?', (q['id'],))
        if contract['role_kinds'][role] == 'human':
            if bridges or dispatch:
                raise ValueError(code)
            continue
        if len(bridges) != 1 or len(dispatch) != 1:
            raise ValueError(code)
        bridge, op = bridges[0], dispatch[0]
        task = _record(conn, 'tasks', bridge['task_id'])
        outbox = _records(conn, 'SELECT * FROM workflow_dispatch_outbox WHERE request_id=?', (q['id'],))
        brief = review_brief(scope, row['candidate_bytes'])
        if (len(outbox) != 1 or outbox[0]['operation_id'] != op['id'] or bridge['operation_id'] != op['id']
                or op['id'] != _id('workflow-dispatch', q['id'], 1) or op['operation_key'] != q['id']
                or outbox[0]['id'] != _id('workflow-outbox', op['id'])
                or outbox[0]['effect_key'] != 'workflow-review:' + q['id']
                or outbox[0]['host_execution_key'] != 'workflow-review-host:' + op['id']
                or outbox[0]['recovery_owner'] != 'workflow_recovery' or outbox[0]['artifact_revision'] != 1
                or any(outbox[0][k] != intent[k] for k in ('authority_namespace', 'authority_generation', 'authority_digest'))
                or bridge['instance_id'] != row['instance_id'] or bridge['assigned_principal'] != principal
                or bridge['assignment_generation'] != 1 or task['assigned_agent'] != principal
                or task['team'] != bindings[role]['team'] or op['org_slug'] != row['org_slug']
                or task['brief'] != brief or task['task_type'] != 'subtask'
                or task['active_chain'] is not None or task['active_fanout'] is not None
                or task['parent_task_id'] is not None
                or (task['current_session_id'] != bridge['session_id']
                    and not (outbox[0]['state'] in ('claimed', 'cancelled', 'uncertain')
                             and not outbox[0]['host_launch_started']
                             and bridge['session_id'] is None))
                or op['principal'] != principal or op['instance_id'] != row['instance_id']
                or op['round_id'] != round_['id'] or op['request_digest'] != q['request_scope_digest']):
            raise ValueError(code)
        tasks.append(task['id'])
        from runtime.workflows.review_dispatch import validate_review_dispatch_storage
        validate_review_dispatch_storage(conn, request_id=q['id'])
    if len(tasks) > 3 or len(set(tasks)) != len(tasks) or len(set(expected)) != len(expected):
        raise ValueError(code)
    events = _records(conn, "SELECT * FROM workflow_events WHERE instance_id=? AND revision=1 AND event_kind='submitted'",
                      (row['instance_id'],))
    links = _records(conn, 'SELECT * FROM workflow_submission_result_links WHERE submission_id=?', (row['submission_id'],))
    if len(events) != 1 or len(links) != 1 or links[0]['task_result_id'] != intent['final_result_id']:
        raise ValueError(code)
    event = json.loads(events[0]['event_bytes'])
    graph = dict(submission_id=row['submission_id'], round_id=round_['id'], request_ids=expected, reviewer_task_ids=tasks)
    if event != dict(event_kind='submitted', instance_id=row['instance_id'], revision=1,
                     submission_digest=row['candidate_digest'], **graph):
        raise ValueError(code)
    contributor = dict(principal=row['principal'], source_task_id=row['source_task_id'],
                       source_session_id=row['source_session_id'], source_result_id=str(intent['final_result_id']),
                       contribution_kind='author')
    for table, key in (('workflow_submission_contributors', 'submission_id'),
                       ('workflow_instance_contributors', 'instance_id')):
        identity = row['submission_id'] if key == 'submission_id' else row['instance_id']
        records = _records(conn, f'SELECT * FROM {table} WHERE {key}=? AND contribution_kind=?',
                           (identity, 'author'))
        if records != [{key: identity, **contributor}]:
            raise ValueError(code)
    return graph


def review_brief(scope: dict, candidate: bytes) -> str:
    return ("Workflow document review. Independently assess only the pinned document and your authorized inputs. "
            "Do not delegate, implement code, merge, approve planning input, or sign on behalf of another principal. "
            "Report your findings through your actual task/session completion callback as the final session action. "
            "This request and its callback remain unsigned; genuine review receipts and joins are later units.\n\n"
            + canonical_bytes(dict(scope=scope,
                submission_bytes_base64=base64.b64encode(candidate).decode('ascii'))).decode())


class WorkflowSubmissionStore:
    def __init__(self, org: Any) -> None:
        self.org, self.db = org, org.db
        self._lock = asyncio.Lock()

    def _session(self, session_id: str) -> tuple[str, str]:
        context = self.org.sessions.get_context_by_session(session_id)
        if context is None:
            raise WorkflowActivationError('unknown_session')
        slug, task, agent = context
        if slug != self.org.slug:
            raise WorkflowActivationError('cross_org_session')
        if self.org.sessions.get_active(task, agent) != session_id:
            raise WorkflowActivationError('session_not_current')
        if self.org.sessions.is_recovery_session(task, agent, session_id):
            raise WorkflowActivationError('recovery_purpose_forbidden')
        return task, agent

    def _authority(self, conn: sqlite3.Connection, intent: dict, capture: Any, actions: set[str]) -> tuple[dict, dict, dict]:
        activation, context_row, binding, template, authorization = _source(conn, intent)
        self.org.workflow_activations._closure(conn, activation['id'], actor=intent['admission_principal'])
        grant = json.loads(authorization['authority_bytes'])
        pointer = conn.execute('SELECT authorization_revision_id FROM workflow_active_authorizations WHERE namespace=?',
                               (authorization['namespace'],)).fetchone()
        active = conn.execute('SELECT activation_id,activation_revision FROM workflow_active_activations WHERE instance_id=?',
                              (intent['instance_id'],)).fetchone()
        if (pointer is None or pointer[0] != authorization['id'] or not actions <= set(grant['original_request']['allowed_actions'])):
            raise WorkflowActivationError('workflow_author_handoff_action_not_authorized')
        if active is None or tuple(active) != (intent['activation_id'], intent['activation_revision']):
            raise WorkflowActivationError('workflow_author_handoff_source_not_current')
        from runtime.workflows.activation import parse_request
        self.org.workflow_activations._roles(parse_request(grant['original_request']), json.loads(capture.ready.snapshot_bytes), template)
        context = json.loads(context_row['context_bytes'])
        principals = {context['binding']['bindings'][role]['principal'] for role in context['document_contract']['reviewer_roles']}
        contributors = {r[0] for r in conn.execute('SELECT principal FROM workflow_instance_contributors WHERE instance_id=?', (intent['instance_id'],))}
        contributors |= {r[0] for r in conn.execute('SELECT principal FROM workflow_submission_operations WHERE instance_id=?', (intent['instance_id'],))}
        if principals & (contributors | {intent['assigned_principal']}):
            raise WorkflowActivationError('role_binding_not_authorized')
        return context, json.loads(binding['binding_bytes']), grant

    @staticmethod
    def _fresh(conn: sqlite3.Connection, intent: dict, task: str, agent: str, session_id: str) -> None:
        source = _record(conn, 'tasks', task)
        if (intent['assigned_principal'] != agent or intent['session_id'] != session_id
                or intent['state'] not in ('claimed', 'running') or not intent['is_current']
                or intent['cancellation_requested'] or intent['final_result_id'] is not None
                or not intent['host_launch_started'] or source['status'] != 'in_progress'
                or source['current_session_id'] != session_id or source['cancelled_at'] is not None):
            raise WorkflowActivationError('workflow_author_handoff_source_not_current')

    async def stage_author_handoff(self, *, session_id: str, command: dict) -> dict:
        try:
            parsed = AuthorHandoffCommand.model_validate(command)
        except ValidationError as exc:
            raise WorkflowActivationError('workflow_author_handoff_invalid_request') from exc
        command = parsed.model_dump()
        try:
            candidate = base64.b64decode(command['submission_bytes_base64'], validate=True)
            candidate.decode('utf8')
            if (not 1 <= len(candidate) <= 1048576 or digest(candidate) != command['submission_digest']
                    or base64.b64encode(candidate).decode('ascii') != command['submission_bytes_base64']):
                raise ValueError('candidate')
        except (ValueError, UnicodeError) as exc:
            raise WorkflowActivationError('workflow_author_handoff_invalid_request') from exc
        task, agent = self._session(session_id)
        capture = self.org.workflow_authority.capture_admission()
        async with self._lock:
            async with self.org.db_lock:
                with self.org.sessions.binding_lease(task, agent):
                    with self.org.workflow_authority.admission_writer(capture) as conn:
                        self._session(session_id)
                        intent = self.org.workflow_drafts._intent(conn, task)
                        self._fresh(conn, intent, task, agent, session_id)
                        if command['activation_id'] != intent['activation_id']:
                            raise WorkflowActivationError('workflow_author_handoff_source_not_current')
                        self._authority(conn, intent, capture, {'stage-immutable-document', 'submit-immutable-document'})
                        if (capture.ready.generation != intent['authority_generation']
                                or capture.ready.snapshot_digest != intent['authority_digest']):
                            raise WorkflowActivationError('workflow_activation_authority_stale')
                        require_author_handoff_schema(conn, expected_org_slug=self.org.slug)
                        if conn.execute('SELECT state FROM workflow_cutover_state WHERE singleton=1').fetchone()[0] != 'enabled':
                            raise WorkflowActivationError('workflow_new_runs_disabled')
                        conflicts = _records(conn, 'SELECT * FROM workflow_author_handoffs WHERE '
                            '(org_slug=? AND principal=? AND operation_key=?) OR (instance_id=? AND revision=1)',
                            (self.org.slug, agent, command['operation_key'], intent['instance_id']))
                        operations = _records(conn, 'SELECT * FROM workflow_submission_operations WHERE '
                            '(org_slug=? AND principal=? AND operation_key=?) OR instance_id=?',
                            (self.org.slug, agent, command['operation_key'], intent['instance_id']))
                        if conflicts or operations:
                            if len(conflicts) != 1 or operations:
                                raise WorkflowActivationError('workflow_author_handoff_operation_conflict')
                            row = conflicts[0]
                            proof = _handoff_record(conn, row, expected_org_slug=self.org.slug)
                            if (proof['client_request'] != command or row['source_task_id'] != task
                                    or row['source_session_id'] != session_id or row['principal'] != agent):
                                raise WorkflowActivationError('workflow_author_handoff_operation_conflict')
                            receipt = self._projection(conn, row, capture, replayed=True)
                        else:
                            row = self._reservation(conn, intent, command, candidate, capture)
                            _insert(conn, 'workflow_author_handoffs', row)
                            validate_workflow_schema(conn, expected_org_slug=self.org.slug)
                            receipt = self._projection(conn, row, capture, replayed=False)
        return receipt

    def _reservation(self, conn: sqlite3.Connection, intent: dict, command: dict, candidate: bytes, capture: Any) -> dict:
        timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        handoff_id = _id('workflow-author-handoff', self.org.slug, intent['assigned_principal'], command['operation_key'])
        submission_id = _id('workflow-submission', intent['instance_id'], 1, self.org.slug,
                            intent['assigned_principal'], command['operation_key'], 'workflow-author-handoff@1')
        request = canonical_bytes({**command, 'format': 'workflow-author-handoff-request@1',
            'handoff_id': handoff_id, 'submission_id': submission_id, 'instance_id': intent['instance_id'], 'revision': 1})
        row = dict(id=handoff_id, intent_id=intent['id'], instance_id=intent['instance_id'], org_slug=self.org.slug,
                   principal=intent['assigned_principal'], source_task_id=intent['task_id'], source_session_id=intent['session_id'],
                   operation_key=command['operation_key'], revision=1, submission_id=submission_id,
                   candidate_bytes=candidate, candidate_digest=digest(candidate), request_bytes=request,
                   request_digest=digest(request), created_at=timestamp)
        activation, context, binding, template, authorization = _source(conn, intent)
        contract = canonical_bytes(_document_contract(template))
        pins = dict(template=dict(identity_id=template.identity_id, version_id=template.version_id, version=template.version,
                                  definition_digest=template.definition_digest, compiler_pin=template.compiler_pin,
                                  validator_pin=template.validator_pin, source_pin=template.source_pin),
                    activation=dict(id=activation['id'], revision=1, request_digest=activation['request_digest']))
        for name, identity, raw, dg in (
                ('authorization', authorization['id'], authorization['authority_bytes'], authorization['authority_digest']),
                ('binding', binding['id'], binding['binding_bytes'], binding['binding_digest']),
                ('context', context['id'], context['context_bytes'], context['context_digest']),
                ('scope', intent['task_id'], intent['task_scope_bytes'], intent['task_scope_digest']),
                ('contract', template.version_id, contract, digest(contract))):
            pins[name] = dict(id=identity, bytes_hex=raw.hex(), digest=dg)
        fields = {'dependency': ('kind', 'org_namespace', 'profile_name', 'consumer_identity', 'bound_generation', 'state'),
                  'store': ('kind', 'profile_name', 'generation', 'profile_digest', 'state'),
                  'registry': ('kind', 'profile_name', 'published_generation')}
        mirror = [dict(zip(fields[item[0]], item, strict=True)) for item in capture.profile_mirror]
        generation = dict(org_slug=self.org.slug, task_id=intent['task_id'], principal=intent['assigned_principal'],
                          **{k: intent[k] for k in ('session_id', 'activation_revision', 'assignment_generation',
                              'attempt_sequence', 'claim_token', 'claim_owner', 'host_execution_key', 'host_execution_id',
                              'state', 'is_current', 'host_launch_started', 'cancellation_requested', 'final_result_id')})
        proof = dict(format='workflow-author-handoff@1', handoff=_hex(row), client_request=command,
                     source_intent=_hex({k: v for k, v in intent.items() if k not in _DRAFT_MUTABLE}),
                     source_generation=generation, pins=pins,
                     admission_authority=dict(ready=dict(namespace=capture.ready.namespace, generation=capture.ready.generation,
                         snapshot_bytes_hex=capture.ready.snapshot_bytes.hex(), snapshot_digest=capture.ready.snapshot_digest),
                         pointer=dict(zip(('current_generation', 'journal_id', 'snapshot_digest', 'state', 'profile_fence'), capture.pointer, strict=True)),
                         profiles=[dict(name=name, digest=dg) for name, dg in capture.profile_digests], mirror=mirror,
                         cutover='enabled', actions=['stage-immutable-document', 'submit-immutable-document']))
        raw = canonical_bytes(proof)
        if len(request) > 2097152 or len(raw) > 16777216:
            raise WorkflowActivationError('workflow_author_handoff_invalid_request')
        return {**row, 'admission_bytes': raw, 'admission_digest': digest(raw)}

    def _projection(self, conn: sqlite3.Connection, row: dict, capture: Any, *, replayed: bool) -> dict:
        proof = _handoff_record(conn, row, expected_org_slug=self.org.slug)
        intent = _record(conn, 'workflow_draft_dispatch_intents', row['intent_id'])
        graph = validate_handoff_graph(conn, row, proof)
        state, reason, reconcile = 'pending', 'awaiting_callback', False
        if intent['state'] in ('failed', 'cancelled'):
            state, reason = intent['state'], 'source_' + intent['state']
            reconcile = bool(intent['host_launch_started'] and not conn.execute(
                "SELECT 1 FROM workflow_draft_dispatch_events WHERE intent_id=? AND event_kind=?",
                (intent['id'], intent['state'])).fetchone())
        elif intent['state'] == 'uncertain':
            state, reason, reconcile = 'held', 'source_uncertain', True
        elif not intent['is_current'] or intent['cancellation_requested']:
            state, reason = 'held', 'source_not_current'
        elif capture.ready.generation != intent['authority_generation'] or capture.ready.snapshot_digest != intent['authority_digest']:
            state, reason = 'held', 'authority_not_current'
        elif conn.execute('SELECT state FROM workflow_cutover_state WHERE singleton=1').fetchone()[0] != 'enabled':
            state, reason = 'held', 'new_runs_disabled'
        elif graph is not None:
            state, reason = 'consumed', 'consumed'
        elif intent['final_result_id'] is not None:
            reason, reconcile = 'awaiting_terminal_proof', intent['state'] == 'completed' and _source_result(conn, intent) is None
        core = dict(handoff_id=row['id'], reserved_submission_id=row['submission_id'], instance_id=row['instance_id'],
                    revision=1, candidate_digest=row['candidate_digest'], request_digest=row['request_digest'], created_at=row['created_at'])
        return dict(format='workflow-author-handoff-receipt@1', core=core, replayed=replayed,
                    current=dict(state=state, pending=state in ('pending', 'held'), reconciliation_required=reconcile,
                                 reason=reason, graph=graph if state == 'consumed' else None))

    async def consume_completed_handoff(self, handoff_id: str) -> list[str]:
        capture = self.org.workflow_authority.capture_admission()
        notifications = []
        async with self._lock:
            async with self.org.db_lock:
                with self.org.workflow_authority.admission_writer(capture) as conn:
                    require_author_handoff_schema(conn, expected_org_slug=self.org.slug)
                    row = _record(conn, 'workflow_author_handoffs', handoff_id)
                    proof = _handoff_record(conn, row, expected_org_slug=self.org.slug)
                    intent = _record(conn, 'workflow_draft_dispatch_intents', row['intent_id'])
                    context, binding, grant = self._authority(conn, intent, capture,
                        {'stage-immutable-document', 'submit-immutable-document', 'collect-review'})
                    if self._projection(conn, row, capture, replayed=True)['current']['reason'] in (
                            'source_failed', 'source_cancelled', 'source_uncertain', 'source_not_current',
                            'authority_not_current', 'new_runs_disabled'):
                        return []
                    result = _source_result(conn, intent)
                    if result is None:
                        return []
                    if validate_handoff_graph(conn, row, proof) is not None:
                        return []
                    notifications = self._graph(conn, row, proof, context, binding, grant, intent, result)
                    validate_workflow_schema(conn, expected_org_slug=self.org.slug)
        return notifications

    def _graph(self, conn: sqlite3.Connection, row: dict, proof: dict, context: dict, binding: dict,
               grant: dict, intent: dict, result: dict) -> list[str]:
        now = datetime.now(timezone.utc).isoformat()
        if conn.execute('SELECT 1 FROM workflow_submission_operations WHERE '
                        '(org_slug=? AND principal=? AND operation_key=?) OR instance_id=?',
                        (self.org.slug, row['principal'], row['operation_key'], row['instance_id'])).fetchone():
            raise WorkflowActivationError('workflow_author_handoff_operation_conflict')
        submission = dict(id=row['submission_id'], instance_id=row['instance_id'], revision=1,
                          submission_bytes=row['candidate_bytes'], submission_digest=row['candidate_digest'], storage_ref=None,
                          source_task_id=row['source_task_id'], source_session_id=row['source_session_id'], source_result_id=None,
                          author_principal=row['principal'])
        _insert(conn, 'workflow_submissions', submission)
        operation = dict(submission_id=row['submission_id'], org_slug=self.org.slug, principal=row['principal'],
                         operation_key=row['operation_key'], request_digest=row['request_digest'], instance_id=row['instance_id'],
                         source_task_id=row['source_task_id'], source_session_id=row['source_session_id'], created_at=now)
        provenance = canonical_bytes(dict(format='workflow-submission-operation@1', operation=operation,
            submission=_hex(submission), source_intent=proof['source_intent'], request_bytes=row['request_bytes'].hex()))
        _insert(conn, 'workflow_submission_operations', {**operation, 'provenance_bytes': provenance, 'provenance_digest': digest(provenance)})
        result_bytes = canonical_bytes(result)
        _insert(conn, 'workflow_submission_result_links', dict(submission_id=row['submission_id'], task_result_id=result['id'],
            source_task_id=row['source_task_id'], source_session_id=row['source_session_id'],
            result_bytes=result_bytes, result_digest=digest(result_bytes), accepted_at=now))
        contribution = dict(principal=row['principal'], source_task_id=row['source_task_id'], source_session_id=row['source_session_id'],
                            source_result_id=str(result['id']), contribution_kind='author')
        _insert(conn, 'workflow_submission_contributors', dict(submission_id=row['submission_id'], **contribution))
        _insert(conn, 'workflow_instance_contributors', dict(instance_id=row['instance_id'], **contribution))
        round_id = _id('workflow-round', row['instance_id'], 1, row['submission_id'])
        _insert(conn, 'workflow_rounds', dict(id=round_id, instance_id=row['instance_id'], submission_id=row['submission_id'], current_revision=1, state='reviewing'))
        contract = context['document_contract']
        request_ids, task_ids = [], []
        for role in contract['reviewer_roles']:
            selected = binding['bindings'][role]
            qid = _id('workflow-request', round_id, role, selected['principal'], 1)
            scope = review_scope(row=row, proof=proof, context=context, contract=contract, role=role,
                                 request_id=qid, round_id=round_id, binding=selected)
            raw = canonical_bytes(scope)
            _insert(conn, 'workflow_review_requests', dict(id=qid, round_id=round_id, principal=selected['principal'],
                assignment_generation=1, request_scope_bytes=raw, request_scope_digest=digest(raw), status='pending', supersedes_request_id=None))
            request_ids.append(qid)
            if contract['role_kinds'][role] == 'human':
                continue
            task_id = self.db.next_task_id()
            brief = review_brief(scope, row['candidate_bytes'])
            self.db._insert_task_uncommitted(TaskRecord(id=task_id, assigned_agent=selected['principal'], team=selected['team'], brief=brief, task_type='subtask'))
            task_ids.append(task_id)
            opid = _id('workflow-dispatch', qid, 1)
            _insert(conn, 'workflow_dispatch_operations', dict(id=opid, org_slug=self.org.slug, principal=selected['principal'],
                operation_key=qid, request_digest=digest(raw), instance_id=row['instance_id'], round_id=round_id,
                request_id=qid, state='admitted', created_at=now))
            _insert(conn, 'workflow_request_task_bridges', dict(request_id=qid, operation_id=opid, instance_id=row['instance_id'],
                task_id=task_id, assigned_principal=selected['principal'], assignment_generation=1, session_id=None,
                result_id=None, state='queued', created_at=now))
            _insert(conn, 'workflow_dispatch_outbox', dict(id=_id('workflow-outbox', opid), operation_id=opid, request_id=qid,
                effect_key='workflow-review:' + qid, authority_namespace=intent['authority_namespace'],
                authority_generation=intent['authority_generation'], authority_digest=intent['authority_digest'],
                artifact_revision=1, state='queued', claim_token=None, claim_owner=None, host_launch_started=0,
                host_execution_key='workflow-review-host:' + opid, host_execution_id=None, recovery_owner='workflow_recovery',
                last_error=None, created_at=now, updated_at=now))
        graph = dict(submission_id=row['submission_id'], round_id=round_id, request_ids=request_ids, reviewer_task_ids=task_ids)
        event = canonical_bytes(dict(event_kind='submitted', instance_id=row['instance_id'], revision=1,
                                     submission_digest=row['candidate_digest'], **graph))
        _insert(conn, 'workflow_events', dict(id=_id('workflow-event', row['instance_id'], 1, 'submitted'),
            instance_id=row['instance_id'], revision=1, event_kind='submitted', event_bytes=event, event_digest=digest(event), created_at=now))
        conn.execute("UPDATE workflow_instances SET status='reviewing' WHERE id=?", (row['instance_id'],))
        return task_ids

    def completed(self, task_id: str) -> None:
        """Called after draft terminal releases locks, while the native hook retains capacity."""
        with self.db._lock:
            tables = {r[0] for r in self.db._conn.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
            if 'workflow_author_handoffs' not in tables:
                return
            ids = [r[0] for r in self.db._conn.execute('SELECT id FROM workflow_author_handoffs WHERE source_task_id=?', (task_id,))]
        for handoff_id in ids:
            tasks = self.org.workflow_drafts._on_loop(self.consume_completed_handoff(handoff_id))
            queue = getattr(self.org.orchestrator, '_queue', None)
            if queue is not None:
                for reviewer_task in tasks:
                    queue.enqueue_if_absent(self.org.slug, reviewer_task)

    def author_instruction(self, task_id: str, session_id: str) -> str:
        with self.db._lock:
            rows = _records(self.db._conn, 'SELECT * FROM workflow_draft_dispatch_intents WHERE task_id=?', (task_id,))
            if len(rows) != 1:
                return ''
            intent = rows[0]
            context = json.loads(_record(self.db._conn, 'workflow_contexts', intent['context_id'])['context_bytes'])
            if context.get('format') != 'workflow-initial-draft-context@3':
                return ''
            actions = context['authorization']['original_request']['allowed_actions']
            if not {'stage-immutable-document', 'submit-immutable-document'} <= set(actions):
                return ''
            activation_id = intent['activation_id']
        return (f'\nWorkflow original-author handoff: before your completion callback, stage the exact UTF8 document with '
                f'happyranch workflows submissions stage --org {self.org.slug} --session-id {session_id} '
                '--from-file <absolute-command.json>. The closed command has format workflow-author-handoff-command@1, '
                f'operation_key (one stable original key), activation_id {activation_id}, expected_revision 1, '
                'submission_bytes_base64 and submission_digest (SHA256 of exact bytes). Preserve the same key/body '
                'after a lost response. Staging reserves immutable bytes only; it creates no submitted graph or signature. '
                'Then use your actual task/session report-completion as the FINAL session action. The owned service '
                'can fulfill the same handoff only after genuine callback and committed successful/quiescent terminal proof.\n')

    async def get_handoff(self, *, handoff_id: str, lane: str, session_id: str | None,
                          selector: str | None) -> dict:
        task_agent = self._session(session_id) if session_id is not None else None
        capture = self.org.workflow_authority.capture_admission()
        async with self._lock:
            async with self.org.db_lock:
                lease = self.org.sessions.binding_lease(*task_agent) if task_agent is not None else nullcontext()
                with lease:
                    with self.org.workflow_authority.admission_writer(capture) as conn:
                        if session_id is not None:
                            self._session(session_id)
                        rows = _records(conn, 'SELECT * FROM workflow_author_handoffs WHERE id=?', (handoff_id,))
                        if len(rows) != 1 or rows[0]['org_slug'] != self.org.slug:
                            raise WorkflowActivationError('workflow_author_handoff_not_found')
                        row = rows[0]
                        proof = _handoff_record(conn, row, expected_org_slug=self.org.slug)
                        intent = _record(conn, 'workflow_draft_dispatch_intents', row['intent_id'])
                        actions = {'collect-review'}
                        if lane == 'source-agent':
                            if (task_agent != (row['source_task_id'], row['principal']) or session_id != row['source_session_id']
                                    or selector != intent['activation_id'] or intent['session_id'] != row['source_session_id']
                                    or intent['assigned_principal'] != row['principal'] or not intent['is_current']
                                    or intent['activation_revision'] != 1 or intent['assignment_generation'] != 1 or intent['attempt_sequence'] != 1):
                                raise WorkflowActivationError('workflow_author_handoff_not_found')
                            actions = {'stage-immutable-document', 'submit-immutable-document'}
                        elif lane == 'request-agent':
                            graph = validate_handoff_graph(conn, row, proof)
                            if graph is None or selector not in graph['request_ids']:
                                raise WorkflowActivationError('workflow_author_handoff_not_found')
                            bridges = _records(conn, 'SELECT * FROM workflow_request_task_bridges WHERE request_id=?', (selector,))
                            if (len(bridges) != 1 or task_agent != (bridges[0]['task_id'], bridges[0]['assigned_principal'])
                                    or session_id != bridges[0]['session_id'] or bridges[0]['assignment_generation'] != 1):
                                raise WorkflowActivationError('workflow_author_handoff_not_found')
                            self.org.workflow_reviews._intent(conn, bridges[0]['task_id'])
                        elif lane == 'owner-human':
                            instance = _record(conn, 'workflow_instances', row['instance_id'])
                            grant = json.loads(_record(conn, 'workflow_authorization_revisions', proof['pins']['authorization']['id'])['authority_bytes'])
                            if (instance['owner_principal'] != 'human:founder' or intent['admission_principal'] != 'human:founder'
                                    or grant['actor'] != dict(principal_kind='human', principal_id='founder', proof_kind='founder_bearer')):
                                raise WorkflowActivationError('workflow_author_handoff_not_found')
                        else:
                            raise WorkflowActivationError('workflow_author_handoff_invalid_request')
                        try:
                            self._authority(conn, intent, capture, actions)
                        except WorkflowActivationError as exc:
                            if exc.code == 'workflow_author_handoff_action_not_authorized':
                                raise WorkflowActivationError('workflow_author_handoff_read_not_authorized') from exc
                            raise
                        receipt = self._projection(conn, row, capture, replayed=True)
        return receipt

    async def get_request(self, *, request_id: str, session_id: str | None, human: bool) -> dict:
        task_agent = self._session(session_id) if session_id is not None else None
        capture = self.org.workflow_authority.capture_admission()
        async with self._lock:
            async with self.org.db_lock:
                with self.org.sessions.binding_lease(*task_agent) if task_agent is not None else nullcontext():
                    with self.org.workflow_authority.admission_writer(capture) as conn:
                        if session_id is not None:
                            self._session(session_id)
                        requests = _records(conn, 'SELECT * FROM workflow_review_requests WHERE id=?', (request_id,))
                        if len(requests) != 1:
                            raise WorkflowActivationError('workflow_review_request_not_found')
                        request = requests[0]
                        scope = json.loads(request['request_scope_bytes'])
                        if scope['org_slug'] != self.org.slug or request['status'] != 'pending':
                            raise WorkflowActivationError('workflow_review_request_not_found')
                        row = _record(conn, 'workflow_author_handoffs', scope['handoff_id'])
                        proof = _handoff_record(conn, row, expected_org_slug=self.org.slug)
                        validate_handoff_graph(conn, row, proof)
                        if human:
                            if scope['kind'] != 'human' or request['principal'] != 'founder':
                                raise WorkflowActivationError('workflow_review_request_not_found')
                        else:
                            bridges = _records(conn, 'SELECT * FROM workflow_request_task_bridges WHERE request_id=?', (request_id,))
                            if (len(bridges) != 1 or scope['kind'] != 'agent'
                                    or task_agent != (bridges[0]['task_id'], bridges[0]['assigned_principal'])
                                    or session_id != bridges[0]['session_id']):
                                raise WorkflowActivationError('workflow_review_request_not_found')
                            self.org.workflow_reviews._intent(conn, bridges[0]['task_id'])
                        intent = _record(conn, 'workflow_draft_dispatch_intents', row['intent_id'])
                        self._authority(conn, intent, capture, {'collect-review'})
                        current = self._projection(conn, row, capture, replayed=True)['current']
                        response = dict(format='workflow-review-request-receipt@1', request_id=request_id,
                            scope=scope, request_scope_digest=request['request_scope_digest'], status='pending',
                            signed=False, pending=True, reconciliation_required=current['reconciliation_required'],
                            submission_bytes_base64=base64.b64encode(row['candidate_bytes']).decode('ascii'))
        return response

    async def discover_completed_handoffs(self, queue: Any) -> None:
        with self.db._lock:
            if not self.db._conn.execute("SELECT 1 FROM sqlite_schema WHERE name='workflow_author_handoffs'").fetchone():
                return
            handoffs = [r[0] for r in self.db._conn.execute('SELECT h.id FROM workflow_author_handoffs h '
                'JOIN workflow_draft_dispatch_intents d ON d.id=h.intent_id WHERE d.state=\'completed\' AND NOT EXISTS '
                '(SELECT 1 FROM workflow_submission_operations o WHERE o.submission_id=h.submission_id)')]
        for handoff_id in handoffs:
            tasks = await self.consume_completed_handoff(handoff_id)
            for task_id in tasks:
                queue.enqueue_if_absent(self.org.slug, task_id)
