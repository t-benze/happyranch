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
    require_author_handoff_schema,
)
from runtime.models import TaskRecord
from runtime.workflows.activation import (
    ClosedRecord, SHA256, Token, WorkflowActivationError, _id,
)
from runtime.workflows.artifacts import (
    document_from_handoff, document_from_submission, document_review_brief, document_review_scope,
    artifact_review_scope, artifact_review_brief, context_contract, review_family,
)
from runtime.workflows.draft_dispatch import canonical_bytes, digest
from runtime.workflows.templates import WorkflowTemplatePrincipal, WorkflowTemplateStore, _review_contract


class AuthorHandoffCommand(ClosedRecord):
    format: Literal['workflow-author-handoff-command@1']
    operation_key: Token
    activation_id: Token
    expected_revision: StrictInt = Field(ge=1, le=1)
    submission_bytes_base64: str = Field(min_length=4, max_length=1398104)
    submission_digest: SHA256


class ArtifactHandoffCommand(AuthorHandoffCommand):
    format: Literal['workflow-author-handoff-command@2']
    artifact_kind: Literal['document', 'code-change']


def parse_handoff(command: object) -> dict:
    try:
        model = (ArtifactHandoffCommand if isinstance(command, dict) and
                 command.get('format') == 'workflow-author-handoff-command@2' else AuthorHandoffCommand)
        return model.model_validate(command).model_dump()
    except ValidationError as exc:
        raise WorkflowActivationError('workflow_author_handoff_invalid_request') from exc


def handoff_version(command: dict) -> int:
    version = {'workflow-author-handoff-command@1': 1, 'workflow-author-handoff-command@2': 2}.get(command.get('format'))
    if version is None:
        raise WorkflowActivationError('workflow_author_handoff_invalid_request')
    return version


def source_actions(intent: dict) -> set[str]:
    if intent['admission_kind'] == 'revision':
        # The selected context/grant is read by the caller; original family is pinned.
        return {'return-to-author'}
    request = json.loads(intent['request_bytes'])
    if request.get('format') == 'workflow-activation-request@4':
        return {'stage-immutable-artifact', 'submit-immutable-artifact'}
    return {'stage-immutable-document', 'submit-immutable-document'}


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
                    'source_failed', 'source_cancelled', 'consumed', 'noncurrent_successor']
    graph: AuthorHandoffGraph | None


class AuthorHandoffReceipt(ClosedRecord):
    format: Literal['workflow-author-handoff-receipt@1']
    core: AuthorHandoffCore
    replayed: StrictBool
    current: AuthorHandoffCurrent


class ArtifactHandoffCore(AuthorHandoffCore):
    artifact_kind: Literal['document']


class ArtifactHandoffReceipt(AuthorHandoffReceipt):
    format: Literal['workflow-author-handoff-receipt@2']
    core: ArtifactHandoffCore


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
    contract = _review_contract(template)
    if contract is None or contract['format'] not in ('workflow-document-contract@3', 'workflow-artifact-contract@4'):
        raise WorkflowActivationError('workflow_author_handoff_action_not_authorized')
    context_contract(json.loads(context['context_bytes']))
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
            or canonical_bytes(accepted['record']) != canonical_bytes(result) or type(accepted['id']) is not int or accepted['id'] != result['id']
            or accepted['digest'] != digest(canonical_bytes(result))
            or last['after']['state'] != 'completed' or last['after']['final_result_id'] != result['id']
            or canonical_bytes(last['terminal_evidence']) != canonical_bytes({'host_quiescent': True})
            or last['result'] != accepted or task['status'] != 'completed'
            or intent['cancellation_requested'] or task['cancelled_at'] is not None
            or task['current_session_id'] != intent['session_id'] or task['assigned_agent'] != intent['assigned_principal']):
        return None
    return result


def _reviewed_source_result(conn: sqlite3.Connection, row: dict, intent: dict) -> dict:
    """Authenticate the retained successful source; never reinterpret it as current validity."""
    from runtime.workflows.review_dispatch import _same_value
    code = 'workflow_author_handoff_storage_corrupt'
    links = _records(conn, 'SELECT * FROM workflow_submission_result_links WHERE submission_id=?', (row['submission_id'],))
    if len(links) != 1 or type(links[0]['task_result_id']) is not int or links[0]['task_result_id'] <= 0:
        raise ValueError(code)
    link = links[0]
    result = _record(conn, 'task_results', link['task_result_id'])
    callbacks = _records(conn, "SELECT * FROM workflow_draft_dispatch_events WHERE intent_id=? AND event_kind='callback_recorded'",
                         (intent['id'],))
    terminals = _records(conn, "SELECT * FROM workflow_draft_dispatch_events WHERE intent_id=? AND event_kind IN ('completed','host_reconciled')",
                         (intent['id'],))
    if (len(callbacks) != 1 or result['status'] != 'completed'
            or result['task_id'] != row['source_task_id'] or result['agent'] != row['principal']
            or result['session_id'] != row['source_session_id'] or intent['final_result_id'] != result['id']
            or link['source_task_id'] != result['task_id'] or link['source_session_id'] != result['session_id']
            or not _same_value(_stored_json(link['result_bytes'], link['result_digest'], code=code), result)):
        raise ValueError(code)
    callback = _stored_json(callbacks[0]['event_bytes'], callbacks[0]['event_digest'], code=code)
    accepted = callback['result']
    if (type(accepted) is not dict or set(accepted) != {'id', 'digest', 'record', 'accepted', 'disposition'}
            or type(accepted['accepted']) is not int or accepted['accepted'] != 1
            or accepted['disposition'] not in ('accepted', 'pending_host_acknowledgment')
            or not _same_value(accepted['id'], result['id'])
            or not _same_value(accepted['record'], result)
            or accepted['digest'] != digest(canonical_bytes(result))
            or type(callbacks[0]['callback_accepted']) is not int or callbacks[0]['callback_accepted'] != 1
            or not _same_value(callbacks[0]['result_id'], result['id'])):
        raise ValueError(code)
    witnessed = False
    for terminal in terminals:
        body = _stored_json(terminal['event_bytes'], terminal['event_digest'], code=code)
        if (body['after']['state'] == 'completed' and _same_value(body['after']['final_result_id'], result['id'])
                and _same_value(body['terminal_evidence'], {'host_quiescent': True})
                and _same_value(body['result'], accepted)):
            witnessed = True
    if not witnessed:
        raise ValueError(code)
    return result


def review_scope(*, row: dict, proof: dict, context: dict, contract: dict, role: str,
                 request_id: str, round_id: str, binding: dict, generation: int = 1) -> dict:
    if row['revision'] == 2:
        from runtime.workflows.revisions import revision_review_scope
        return revision_review_scope(row=row, proof=proof, context=context, contract=contract, role=role,
            request_id=request_id, round_id=round_id, binding=binding, generation=generation)
    serializer = artifact_review_scope if contract['format'] == 'workflow-artifact-contract@4' else document_review_scope
    return serializer(row=row, proof=proof, context=context, contract=contract,
                                 role=role, request_id=request_id, round_id=round_id, binding=binding)


def subject_proof(conn: sqlite3.Connection, row: dict) -> dict:
    if row['revision'] == 2:
        from runtime.workflows.revisions import validate_revision_record
        operation = _record(conn, 'workflow_document_revision_operations', row['id'])
        return validate_revision_record(conn, operation, expected_org_slug=row['org_slug'])
    return _handoff_record(conn, row, expected_org_slug=row['org_slug'])


def history_successor(conn: sqlite3.Connection, instance_id: str, *, fulfilled: bool = False) -> bool:
    from runtime.workflows.revisions import successor_for_history
    row = successor_for_history(conn, instance_id)
    if row is None:
        return False
    if not fulfilled:
        return True
    return conn.execute('SELECT 1 FROM workflow_submission_result_links l JOIN workflow_submissions s ON s.id=l.submission_id WHERE s.instance_id=? AND s.revision=2', (instance_id,)).fetchone() is not None


def validate_handoff_graph(conn: sqlite3.Connection, row: dict, proof: dict) -> dict | None:
    try:
        return _validate_handoff_graph(conn, row, proof)
    except (KeyError, TypeError, AttributeError, ValueError, sqlite3.DatabaseError) as exc:
        raise ValueError('workflow_author_handoff_storage_corrupt') from exc


def _validate_handoff_graph(conn: sqlite3.Connection, row: dict, proof: dict) -> dict | None:
    """Closed graph oracle: all declared requests and all actual agent tasks in role order."""
    code = 'workflow_author_handoff_storage_corrupt'
    revision = row['revision']
    generation = 2 if revision == 2 else 1
    operations = _records(conn, 'SELECT * FROM workflow_submission_operations WHERE submission_id=?', (row['submission_id'],))
    rounds = _records(conn, 'SELECT * FROM workflow_rounds WHERE instance_id=? AND current_revision=?', (row['instance_id'], revision))
    if revision == 2 and not conn.execute('SELECT 1 FROM workflow_submission_result_links WHERE submission_id=?', (row['submission_id'],)).fetchone():
        if rounds:
            raise ValueError(code)
        return None
    if not operations:
        if rounds or conn.execute('SELECT 1 FROM workflow_submissions WHERE id=?', (row['submission_id'],)).fetchone():
            raise ValueError(code)
        return None
    if len(operations) != 1 or len(rounds) != 1:
        raise ValueError(code)
    intent = _record(conn, 'workflow_draft_dispatch_intents', row['intent_id'])
    # A saved review graph uses immutable linked source evidence. Current
    # source validity belongs to mutation/approved-use admission, not history.
    result = _reviewed_source_result(conn, row, intent)
    # Pure integrity projection only; keep full H/F5/receipt oracles in their callers.
    contributors = tuple(_records(conn, 'SELECT * FROM workflow_instance_contributors WHERE instance_id=?',
                                  (row['instance_id'],)))
    candidate = (document_from_submission(_record(conn, 'workflow_submissions', row['submission_id']),
        org_namespace=row['org_slug'], pins=proof['pins'], source_result_id=result['id'], contributors=contributors)
        if revision == 2 else document_from_handoff(row, proof, source_result_id=result['id'], contributors=contributors))
    submission = document_from_submission(_record(conn, 'workflow_submissions', row['submission_id']),
        org_namespace=operations[0]['org_slug'], pins=proof['pins'], source_result_id=result['id'],
        contributors=contributors)
    if candidate.review_key != submission.review_key:
        raise ValueError(code)
    round_ = rounds[0]
    if (round_['submission_id'] != row['submission_id'] or round_['state'] not in ('reviewing', 'closed')
            or round_['id'] != _id('workflow-round', row['instance_id'], revision, row['submission_id'])):
        raise ValueError(code)
    context = json.loads(_record(conn, 'workflow_contexts', intent['context_id'])['context_bytes'])
    contract = context_contract(context)
    bindings = context['binding']['bindings']
    requests = _records(conn, 'SELECT * FROM workflow_review_requests WHERE round_id=?', (round_['id'],))
    expected = []
    tasks = []
    if len(requests) != len(contract['reviewer_roles']):
        raise ValueError(code)
    for role in contract['reviewer_roles']:
        principal = bindings[role]['principal']
        selected = [q for q in requests if q['principal'] == principal and q['assignment_generation'] == generation]
        if len(selected) != 1:
            raise ValueError(code)
        q = selected[0]
        scope = review_scope(row=row, proof=proof, context=context, contract=contract, role=role,
                             request_id=q['id'], round_id=round_['id'], binding=bindings[role], generation=generation)
        if (q['id'] != _id('workflow-request', round_['id'], role, principal, generation)
                or q['status'] not in ('pending', 'approved', 'changes_requested') or q['supersedes_request_id'] is not None
                or canonical_bytes(scope) != q['request_scope_bytes']
                or digest(q['request_scope_bytes']) != q['request_scope_digest']):
            raise ValueError(code)
        expected.append(q['id'])
        bridges = _records(conn, 'SELECT * FROM workflow_request_task_bridges WHERE request_id=?', (q['id'],))
        dispatch = _records(conn, 'SELECT * FROM workflow_dispatch_operations WHERE request_id=?', (q['id'],))
        if contract['role_kinds'][role] == 'human':
            if bridges or dispatch:
                raise ValueError(code)
            from runtime.workflows.review_receipts import validate_human_receipt
            validate_human_receipt(conn, q, scope, proof)
            continue
        if len(bridges) != 1 or len(dispatch) != 1:
            raise ValueError(code)
        bridge, op = bridges[0], dispatch[0]
        task = _record(conn, 'tasks', bridge['task_id'])
        outbox = _records(conn, 'SELECT * FROM workflow_dispatch_outbox WHERE request_id=?', (q['id'],))
        brief = review_brief(scope, row['candidate_bytes'])
        if (len(outbox) != 1 or outbox[0]['operation_id'] != op['id'] or bridge['operation_id'] != op['id']
                or op['id'] != _id('workflow-dispatch', q['id'], generation) or op['operation_key'] != q['id']
                or outbox[0]['id'] != _id('workflow-outbox', op['id'])
                or outbox[0]['effect_key'] != 'workflow-review:' + q['id']
                or outbox[0]['host_execution_key'] != 'workflow-review-host:' + op['id']
                or outbox[0]['recovery_owner'] != 'workflow_recovery' or outbox[0]['artifact_revision'] != revision
                or any(outbox[0][k] != intent[k] for k in ('authority_namespace', 'authority_generation', 'authority_digest'))
                or bridge['instance_id'] != row['instance_id'] or bridge['assigned_principal'] != principal
                or bridge['assignment_generation'] != generation or task['assigned_agent'] != principal
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
        from runtime.workflows.review_receipts import validate_agent_receipt
        validate_agent_receipt(conn, q, scope)
    if len(tasks) > 3 or len(set(tasks)) != len(tasks) or len(set(expected)) != len(expected):
        raise ValueError(code)
    events = _records(conn, "SELECT * FROM workflow_events WHERE instance_id=? AND revision=? AND event_kind='submitted'",
                      (row['instance_id'], revision))
    links = _records(conn, 'SELECT * FROM workflow_submission_result_links WHERE submission_id=?', (row['submission_id'],))
    if len(events) != 1 or len(links) != 1 or links[0]['task_result_id'] != intent['final_result_id']:
        raise ValueError(code)
    event = json.loads(events[0]['event_bytes'])
    graph = dict(submission_id=row['submission_id'], round_id=round_['id'], request_ids=expected, reviewer_task_ids=tasks)
    if event != dict(event_kind='submitted', instance_id=row['instance_id'], revision=revision,
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
        if key == 'instance_id' and history_successor(conn, row['instance_id']):
            from runtime.workflows.revisions import validate_author_history
            validate_author_history(conn, row['instance_id'], records)
            records = [r for r in records if r['source_task_id'] == row['source_task_id']]
        if records != [{key: identity, **contributor}]:
            raise ValueError(code)
    from runtime.workflows.review_join import validate_collection
    validate_collection(conn, row, proof, graph)
    return graph


def review_brief(scope: dict, candidate: bytes) -> str:
    if review_family(scope) == 3:
        return ('Workflow revision review. Independently assess the pinned r2 and only your selected inputs. '
            'Stage workflow-review-stage-command@3 under one stable key before your genuine final callback. '
            'No task, callback or stage is a signature; no delegation/coding/merge grant follows.\n\n'
            + canonical_bytes(dict(scope=scope, submission_bytes_base64=base64.b64encode(candidate).decode('ascii'))).decode())
    return artifact_review_brief(scope, candidate) if review_family(scope) == 2 else document_review_brief(scope, candidate)


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
        if not intent['is_current'] or intent['cancellation_requested']:
            raise WorkflowActivationError('workflow_author_handoff_source_not_current')
        if intent['admission_kind'] == 'revision':
            self.org.workflow_revisions._authority(conn, intent, capture)
        selected = self._selected_authority(conn, intent, capture, actions)
        if not self._current_activation(conn, intent):
            raise WorkflowActivationError('workflow_author_handoff_source_not_current')
        return selected

    @staticmethod
    def _current_activation(conn: sqlite3.Connection, intent: dict) -> bool:
        active = conn.execute('SELECT activation_id,activation_revision FROM workflow_active_activations WHERE instance_id=?',
                              (intent['instance_id'],)).fetchone()
        return active is not None and tuple(active) == (intent['activation_id'], intent['activation_revision'])

    def _selected_authority(self, conn: sqlite3.Connection, intent: dict, capture: Any,
                            actions: set[str]) -> tuple[dict, dict, dict]:
        """Validate the selected grant and full closure against coherent CURRENT authority.

        Only the independently selected current owner may read a historical
        activation through this helper; all other callers use _authority.
        """
        activation, context_row, binding, template, authorization = _source(conn, intent)
        grant = json.loads(authorization['authority_bytes'])
        pointer = conn.execute('SELECT authorization_revision_id FROM workflow_active_authorizations WHERE namespace=?',
                               (authorization['namespace'],)).fetchone()
        if (pointer is None or pointer[0] != authorization['id'] or not actions <= set(grant['original_request']['allowed_actions'])):
            raise WorkflowActivationError('workflow_author_handoff_action_not_authorized')
        from runtime.workflows.activation import parse_request
        self.org.workflow_activations._roles(parse_request(grant['original_request']), json.loads(capture.ready.snapshot_bytes), template)
        self.org.workflow_activations._closure(conn, activation['id'], actor=intent['admission_principal'])
        context = json.loads(context_row['context_bytes'])
        principals = {context['binding']['bindings'][role]['principal'] for role in context_contract(context)['reviewer_roles']}
        contributors = {r[0] for r in conn.execute('SELECT principal FROM workflow_instance_contributors WHERE instance_id=?', (intent['instance_id'],))}
        contributors |= {r[0] for r in conn.execute('SELECT principal FROM workflow_submission_operations WHERE instance_id=?', (intent['instance_id'],))}
        contributors |= {r[0] for r in conn.execute('SELECT assigned_principal FROM workflow_draft_dispatch_intents WHERE instance_id=?', (intent['instance_id'],))}
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
        command = parse_handoff(command)
        task, agent = self._session(session_id)
        capture = self.org.workflow_authority.capture_admission()
        with self.org.sessions.binding_lease(task, agent):
            with self.db._lock:
                self._session(session_id)
                selected = self.org.workflow_drafts._intent(self.db._conn, task)
                self._fresh(self.db._conn, selected, task, agent, session_id)
                if command['activation_id'] != selected['activation_id']:
                    raise WorkflowActivationError('workflow_author_handoff_source_not_current')
                context, _, _ = self._authority(self.db._conn, selected, capture, source_actions(selected))
                self._matched_handoff(command, context)
        try:
            candidate = base64.b64decode(command['submission_bytes_base64'], validate=True)
            candidate.decode('utf8')
            if (not 1 <= len(candidate) <= 1048576 or digest(candidate) != command['submission_digest']
                    or base64.b64encode(candidate).decode('ascii') != command['submission_bytes_base64']):
                raise ValueError('candidate')
        except (ValueError, UnicodeError) as exc:
            raise WorkflowActivationError('workflow_author_handoff_invalid_request') from exc
        async with self._lock:
            async with self.org.db_lock:
                with self.org.sessions.binding_lease(task, agent):
                    with self.org.workflow_authority.admission_writer(capture) as conn:
                        self._session(session_id)
                        intent = self.org.workflow_drafts._intent(conn, task)
                        self._fresh(conn, intent, task, agent, session_id)
                        if command['activation_id'] != intent['activation_id']:
                            raise WorkflowActivationError('workflow_author_handoff_source_not_current')
                        context, _, _ = self._authority(conn, intent, capture, source_actions(intent))
                        self._matched_handoff(command, context)
                        if (capture.ready.generation != intent['authority_generation']
                                or capture.ready.snapshot_digest != intent['authority_digest']):
                            raise WorkflowActivationError('workflow_activation_authority_stale')
                        require_author_handoff_schema(conn, expected_org_slug=self.org.slug, _database=self.db)
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
                            self.db._workflow_validate(conn, expected_org_slug=self.org.slug)
                            receipt = self._projection(conn, row, capture, replayed=False)
        return receipt

    @staticmethod
    def _matched_handoff(command: dict, context: dict) -> None:
        contract = context_contract(context)
        expected = 2 if contract['format'] == 'workflow-artifact-contract@4' else 1
        if handoff_version(command) != expected or (expected == 2 and command['artifact_kind'] != 'document'):
            raise WorkflowActivationError('workflow_author_handoff_invalid_request')

    def _reservation(self, conn: sqlite3.Connection, intent: dict, command: dict, candidate: bytes, capture: Any) -> dict:
        timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        handoff_id = _id('workflow-author-handoff', self.org.slug, intent['assigned_principal'], command['operation_key'])
        submission_id = _id('workflow-submission', intent['instance_id'], 1, self.org.slug,
                            intent['assigned_principal'], command['operation_key'], f'workflow-author-handoff@{handoff_version(command)}')
        request = canonical_bytes({**command, 'format': f'workflow-author-handoff-request@{handoff_version(command)}',
            'handoff_id': handoff_id, 'submission_id': submission_id, 'instance_id': intent['instance_id'], 'revision': 1})
        row = dict(id=handoff_id, intent_id=intent['id'], instance_id=intent['instance_id'], org_slug=self.org.slug,
                   principal=intent['assigned_principal'], source_task_id=intent['task_id'], source_session_id=intent['session_id'],
                   operation_key=command['operation_key'], revision=1, submission_id=submission_id,
                   candidate_bytes=candidate, candidate_digest=digest(candidate), request_bytes=request,
                   request_digest=digest(request), created_at=timestamp)
        activation, context, binding, template, authorization = _source(conn, intent)
        contract = canonical_bytes(_review_contract(template))
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
        proof = dict(**({'repository_resolution': None} if handoff_version(command) == 2 else {}),
                     format=f'workflow-author-handoff@{handoff_version(command)}', handoff=_hex(row), client_request=command,
                     source_intent=_hex({k: v for k, v in intent.items() if k not in _DRAFT_MUTABLE}),
                     source_generation=generation, pins=pins,
                     admission_authority=dict(ready=dict(namespace=capture.ready.namespace, generation=capture.ready.generation,
                         snapshot_bytes_hex=capture.ready.snapshot_bytes.hex(), snapshot_digest=capture.ready.snapshot_digest),
                         pointer=dict(zip(('current_generation', 'journal_id', 'snapshot_digest', 'state', 'profile_fence'), capture.pointer, strict=True)),
                         profiles=[dict(name=name, digest=dg) for name, dg in capture.profile_digests], mirror=mirror,
                         cutover='enabled', actions=sorted(source_actions(intent))))
        raw = canonical_bytes(proof)
        if len(request) > 2097152 or len(raw) > 16777216:
            raise WorkflowActivationError('workflow_author_handoff_invalid_request')
        return {**row, 'admission_bytes': raw, 'admission_digest': digest(raw)}

    def _projection(self, conn: sqlite3.Connection, row: dict, capture: Any, *, replayed: bool) -> dict:
        if row['revision'] == 2:
            operation = _record(conn, 'workflow_document_revision_operations', row['id'])
            projected = self.org.workflow_revisions._projection(conn, operation, capture, replayed=replayed)
            current = projected['current']
            return dict(current=dict(state='consumed' if current['graph'] is not None and current['eligible'] else 'held',
                reason=current['reason'] or 'consumed', reconciliation_required=current['reconciliation_required'],
                pending=current['graph'] is None, graph=current['graph']))
        proof = _handoff_record(conn, row, expected_org_slug=self.org.slug)
        intent = _record(conn, 'workflow_draft_dispatch_intents', row['intent_id'])
        graph = validate_handoff_graph(conn, row, proof)
        # Reconciliation describes actual proof uncertainty independently of
        # whichever higher-precedence hold currently hides the graph.
        source_task = _record(conn, 'tasks', row['source_task_id'])
        source_changed = (source_task['cancelled_at'] is not None
            or source_task['current_session_id'] != row['source_session_id']
            or source_task['assigned_agent'] != row['principal']
            or (graph is not None and source_task['status'] != 'completed'))
        reconcile = (intent['state'] == 'uncertain'
                     or (intent['state'] == 'completed' and _source_result(conn, intent) is None))
        state, reason = 'pending', 'awaiting_callback'
        if intent['state'] in ('failed', 'cancelled'):
            state, reason = intent['state'], 'source_' + intent['state']
            reconcile = bool(intent['host_launch_started'] and not conn.execute(
                "SELECT 1 FROM workflow_draft_dispatch_events WHERE intent_id=? AND event_kind=?",
                (intent['id'], intent['state'])).fetchone())
        elif conn.execute('SELECT state FROM workflow_cutover_state WHERE singleton=1').fetchone()[0] != 'enabled':
            state, reason = 'held', 'new_runs_disabled'
        elif (source_changed or not intent['is_current'] or intent['cancellation_requested']
              or not self._current_activation(conn, intent)):
            state, reason = 'held', 'source_not_current'
        elif capture.ready.generation != intent['authority_generation'] or capture.ready.snapshot_digest != intent['authority_digest']:
            state, reason = 'held', 'authority_not_current'
        elif intent['state'] == 'uncertain':
            state, reason = 'held', 'source_uncertain'
        elif graph is not None:
            state, reason = 'consumed', 'consumed'
        elif intent['final_result_id'] is not None or intent['state'] == 'completed':
            reason = 'awaiting_terminal_proof'
        if graph is not None and history_successor(conn, row['instance_id']):
            state, reason = 'held', 'noncurrent_successor'
        core = dict(handoff_id=row['id'], reserved_submission_id=row['submission_id'], instance_id=row['instance_id'],
                    revision=1, candidate_digest=row['candidate_digest'], request_digest=row['request_digest'], created_at=row['created_at'])
        if proof['format'] == 'workflow-author-handoff@2':
            core['artifact_kind'] = 'document'
        return dict(format='workflow-author-handoff-receipt@2' if proof['format'] == 'workflow-author-handoff@2' else 'workflow-author-handoff-receipt@1', core=core, replayed=replayed,
                    current=dict(state=state, pending=state in ('pending', 'held'), reconciliation_required=reconcile,
                                 reason=reason, graph=graph if state == 'consumed' or reason == 'noncurrent_successor' else None))

    async def consume_completed_handoff(self, handoff_id: str) -> list[str]:
        capture = self.org.workflow_authority.capture_admission()
        notifications = []
        async with self._lock:
            async with self.org.db_lock:
                with self.org.workflow_authority.admission_writer(capture) as conn:
                    require_author_handoff_schema(conn, expected_org_slug=self.org.slug, _database=self.db)
                    row = _record(conn, 'workflow_author_handoffs', handoff_id)
                    proof = _handoff_record(conn, row, expected_org_slug=self.org.slug)
                    intent = _record(conn, 'workflow_draft_dispatch_intents', row['intent_id'])
                    try:
                        context, binding, grant = self._authority(conn, intent, capture,
                            source_actions(intent) | {'collect-review'})
                    except WorkflowActivationError as exc:
                        # A normal hold belongs to THIS reservation. Full H
                        # validation above has already rejected corruption.
                        if exc.code in {'workflow_author_handoff_action_not_authorized',
                                        'workflow_author_handoff_source_not_current', 'role_binding_not_authorized'}:
                            return []
                        raise
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
                    self.db._workflow_validate(conn, expected_org_slug=self.org.slug)
        return notifications

    def _graph(self, conn: sqlite3.Connection, row: dict, proof: dict, context: dict, binding: dict,
               grant: dict, intent: dict, result: dict) -> list[str]:
        self.db._check_team_move_workflow_uncommitted(row["instance_id"])
        now = datetime.now(timezone.utc).isoformat()
        if conn.execute('SELECT 1 FROM workflow_submission_operations WHERE '
                        '(org_slug=? AND principal=? AND operation_key=?) OR instance_id=?',
                        (self.org.slug, row['principal'], row['operation_key'], row['instance_id'])).fetchone():
            raise WorkflowActivationError('workflow_author_handoff_operation_conflict')
        submission = dict(id=row['submission_id'], instance_id=row['instance_id'], revision=1,
                          submission_bytes=row['candidate_bytes'], submission_digest=row['candidate_digest'], storage_ref=None,
                          source_task_id=row['source_task_id'], source_session_id=row['source_session_id'], source_result_id=None,
                          author_principal=row['principal'])
        # Existing conflict precedence is retained; validate before the first insert.
        candidate = document_from_handoff(row, proof, source_result_id=result['id'])
        projected = document_from_submission(submission, org_namespace=self.org.slug,
                                             pins=proof['pins'], source_result_id=result['id'])
        if candidate.review_key != projected.review_key:
            raise ValueError('workflow_author_handoff_storage_corrupt')
        if not self.db._workflow_effect(conn):
            return []
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
        contract = context_contract(context)
        request_ids, task_ids = [], []
        for role in contract['reviewer_roles']:
            selected = binding['bindings'][role]
            qid = _id('workflow-request', round_id, role, selected['principal'], 1)
            scope = review_scope(row=row, proof=proof, context=context, contract=contract, role=role,
                                 request_id=qid, round_id=round_id, binding=selected)
            raw = canonical_bytes(scope)
            if len(raw) > 1048576:
                raise WorkflowActivationError('workflow_activation_context_too_large')
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
        self.org.workflow_revisions.completed(task_id)
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
        revision = self.org.workflow_revisions.author_instruction(task_id, session_id)
        if revision:
            return revision
        with self.db._lock:
            # OrgState attaches this service on retained F/E/G as well. The
            # optional @3 supplement must leave ordinary F tasks workflow-free.
            if not self.db._conn.execute(
                    "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='workflow_author_handoffs'").fetchone():
                return ''
            rows = _records(self.db._conn, 'SELECT * FROM workflow_draft_dispatch_intents WHERE task_id=?', (task_id,))
            if len(rows) != 1:
                return ''
            intent = rows[0]
            context = json.loads(_record(self.db._conn, 'workflow_contexts', intent['context_id'])['context_bytes'])
            if context.get('format') not in ('workflow-initial-draft-context@3', 'workflow-initial-draft-context@4'):
                return ''
            actions = context['authorization']['original_request']['allowed_actions']
            if not source_actions(intent) <= set(actions):
                return ''
            activation_id = intent['activation_id']
            family = 2 if context['format'] == 'workflow-initial-draft-context@4' else 1
        return (f'\nWorkflow original-author handoff: before your completion callback, stage the exact UTF8 document with '
                f'happyranch workflows submissions stage --org {self.org.slug} --session-id {session_id} '
                f'--from-file <absolute-command.json>. The closed command has format workflow-author-handoff-command@{family}, '
                + ('artifact_kind document, ' if family == 2 else '') +
                f'operation_key (one stable original key), activation_id {activation_id}, expected_revision 1, '
                'submission_bytes_base64 and submission_digest (SHA256 of exact bytes). Preserve the same key/body '
                'after a lost response. Staging reserves immutable bytes only; it creates no submitted graph or signature. '
                'Then use your actual task/session report-completion as the FINAL session action. The owned service '
                'can fulfill the same handoff only after genuine callback and committed successful/quiescent terminal proof.\n')

    def _read_schema_presence(self, conn: sqlite3.Connection) -> None:
        # Only absent H needs operator guidance here. Present H is fully
        # validated AFTER selected scope/grant/current-authority admission.
        if not conn.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='workflow_author_handoffs'").fetchone():
            require_author_handoff_schema(conn, expected_org_slug=self.org.slug, _database=self.db)

    def _read_grant(self, conn: sqlite3.Connection, intent: dict, actions: set[str],
                    owner: WorkflowTemplatePrincipal | None = None) -> None:
        binding = _record(conn, 'workflow_binding_snapshots', intent['binding_snapshot_id'])
        authorization = _record(conn, 'workflow_authorization_revisions', binding['authorization_revision_id'])
        pointer = conn.execute('SELECT authorization_revision_id FROM workflow_active_authorizations WHERE namespace=?',
                               (authorization['namespace'],)).fetchone()
        if pointer is None or pointer[0] != authorization['id']:
            raise WorkflowActivationError('workflow_author_handoff_read_not_authorized')
        try:
            grant = json.loads(authorization['authority_bytes'])
            if owner is not None and grant['actor'] != owner.provenance:
                raise WorkflowActivationError('workflow_author_handoff_not_found')
            if not actions <= set(grant['original_request']['allowed_actions']):
                raise WorkflowActivationError('workflow_author_handoff_read_not_authorized')
        except WorkflowActivationError:
            # Domain refusals inherit ValueError; preserve their 403/404 mapping.
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('workflow_author_handoff_storage_corrupt') from exc

    def _select_handoff(self, conn: sqlite3.Connection, *, handoff_id: str, lane: str,
                        task_agent: tuple[str, str] | None, session_id: str | None,
                        selector: str | None, owner: WorkflowTemplatePrincipal | None) -> tuple[dict, dict, set[str]]:
        self._read_schema_presence(conn)
        rows = _records(conn, 'SELECT * FROM workflow_author_handoffs WHERE id=? AND org_slug=?',
                        (handoff_id, self.org.slug))
        if len(rows) != 1:
            raise WorkflowActivationError('workflow_author_handoff_not_found')
        row = rows[0]
        # Select from relational identities, never from the private admission
        # proof or decoded graph. Full closure follows successful admission.
        if lane == 'source-agent' and (task_agent != (row['source_task_id'], row['principal'])
                                      or session_id != row['source_session_id']):
            raise WorkflowActivationError('workflow_author_handoff_not_found')
        if lane == 'request-agent':
            requests = _records(conn, 'SELECT q.* FROM workflow_review_requests q JOIN workflow_rounds r ON r.id=q.round_id '
                'WHERE q.id=? AND r.submission_id=? AND r.instance_id=? AND r.current_revision=1 '
                "AND q.status='pending' AND q.supersedes_request_id IS NULL AND q.assignment_generation=1",
                (selector, row['submission_id'], row['instance_id']))
            bridges = _records(conn, 'SELECT * FROM workflow_request_task_bridges WHERE request_id=?', (selector,))
            if (len(requests) != 1 or len(bridges) != 1
                    or task_agent != (bridges[0]['task_id'], bridges[0]['assigned_principal'])
                    or session_id != bridges[0]['session_id'] or bridges[0]['assignment_generation'] != 1
                    or requests[0]['principal'] != bridges[0]['assigned_principal']):
                raise WorkflowActivationError('workflow_author_handoff_not_found')
        if lane == 'owner-human':
            instances = _records(conn, 'SELECT * FROM workflow_instances WHERE id=? AND owner_principal=?',
                                 (row['instance_id'], owner.operation_principal))
            if len(instances) != 1:
                raise WorkflowActivationError('workflow_author_handoff_not_found')
        intent = _record(conn, 'workflow_draft_dispatch_intents', row['intent_id'])
        actions = {'collect-review'}
        if lane == 'source-agent':
            if (selector != intent['activation_id'] or intent['session_id'] != row['source_session_id']
                    or intent['assigned_principal'] != row['principal'] or not intent['is_current']
                    or intent['activation_revision'] != 1 or intent['assignment_generation'] != 1 or intent['attempt_sequence'] != 1):
                raise WorkflowActivationError('workflow_author_handoff_not_found')
            actions = source_actions(intent)
        elif lane == 'request-agent':
            if not intent['is_current'] or not self._current_activation(conn, intent):
                raise WorkflowActivationError('workflow_author_handoff_not_found')
        elif lane == 'owner-human':
            activation = _record(conn, 'workflow_activations', intent['activation_id'])
            if (intent['admission_principal'] != owner.operation_principal
                    or activation['activated_by'] != owner.operation_principal):
                raise WorkflowActivationError('workflow_author_handoff_not_found')
        else:
            raise WorkflowActivationError('workflow_author_handoff_invalid_request')
        self._read_grant(conn, intent, actions, owner)
        if lane == 'source-agent' and not self._current_activation(conn, intent):
            raise WorkflowActivationError('workflow_author_handoff_source_not_current')
        return row, intent, actions

    def _select_request(self, conn: sqlite3.Connection, *, request_id: str,
                        task_agent: tuple[str, str] | None, session_id: str | None,
                        human: bool, owner: WorkflowTemplatePrincipal | None = None,
                        include_signed: bool = False) -> tuple[dict, dict, dict]:
        self._read_schema_presence(conn)
        status = "status IN ('pending','approved','changes_requested')" if include_signed else "status='pending'"
        requests = _records(conn, f'SELECT * FROM workflow_review_requests WHERE id=? AND {status} AND supersedes_request_id IS NULL', (request_id,))
        if len(requests) != 1:
            raise WorkflowActivationError('workflow_review_request_not_found')
        request = requests[0]
        bridges = _records(conn, 'SELECT * FROM workflow_request_task_bridges WHERE request_id=?', (request_id,))
        if human:
            if request['principal'] != 'founder' or bridges:
                raise WorkflowActivationError('workflow_review_request_not_found')
        elif (len(bridges) != 1 or task_agent != (bridges[0]['task_id'], bridges[0]['assigned_principal'])
                or session_id != bridges[0]['session_id'] or bridges[0]['assignment_generation'] != request['assignment_generation']
                or request['principal'] != bridges[0]['assigned_principal']):
            raise WorkflowActivationError('workflow_review_request_not_found')
        round_ = _record(conn, 'workflow_rounds', request['round_id'])
        if round_['current_revision'] == 1:
            rows = _records(conn, 'SELECT * FROM workflow_author_handoffs WHERE submission_id=? AND instance_id=? AND org_slug=?',
                (round_['submission_id'], round_['instance_id'], self.org.slug))
            if len(rows) != 1 or request['assignment_generation'] != 1:
                raise WorkflowActivationError('workflow_review_request_not_found')
            row = rows[0]
        elif round_['current_revision'] == 2:
            from runtime.workflows.revisions import exists, review_row
            if not exists(conn):
                raise WorkflowActivationError('workflow_review_request_not_found')
            rows = _records(conn, 'SELECT * FROM workflow_document_revision_operations WHERE instance_id=? AND org_slug=?',
                            (round_['instance_id'], self.org.slug))
            if len(rows) != 1 or request['assignment_generation'] != 2:
                raise WorkflowActivationError('workflow_review_request_not_found')
            # Relational grant/recipient admitted before decoding revision storage.
            intent = _record(conn, 'workflow_draft_dispatch_intents', rows[0]['draft_intent_id'])
            self._read_grant(conn, intent, {'collect-review'}, owner)
            row = review_row(conn, rows[0])
            if row is None or row['submission_id'] != round_['submission_id']:
                raise ValueError('workflow_author_handoff_storage_corrupt')
        else:
            raise WorkflowActivationError('workflow_review_request_not_found')
        intent = _record(conn, 'workflow_draft_dispatch_intents', row['intent_id'])
        if (not self._current_activation(conn, intent)
                or (not intent['is_current'] and not (human and include_signed and request['status'] != 'pending'
                    and history_successor(conn, row['instance_id'])))):
            raise WorkflowActivationError('workflow_review_request_not_found')
        self._read_grant(conn, intent, {'collect-review'}, owner)
        return request, row, intent

    def selected_human_request(self, conn: sqlite3.Connection, row: dict, graph: dict | None,
                               context: dict, owner: WorkflowTemplatePrincipal) -> str | None:
        """Navigation only, after complete graph validation and current owner admission."""
        if graph is None:
            return None
        contract = context_contract(context)
        roles = [role for role in contract['reviewer_roles'] if contract['role_kinds'][role] == 'human']
        if not roles:
            return None
        matches = []
        for qid in graph['request_ids']:
            request = _record(conn, 'workflow_review_requests', qid)
            scope = _stored_json(request['request_scope_bytes'], request['request_scope_digest'],
                                 code='workflow_author_handoff_storage_corrupt')
            if scope['role'] == roles[0]:
                selected, subject, _ = self._select_request(conn, request_id=qid, task_agent=None,
                    session_id=None, human=True, owner=owner, include_signed=True)
                if (len(roles) != 1 or scope['kind'] != 'human' or scope['principal'] != 'founder'
                        or subject['id'] != row['id'] or selected['round_id'] != graph['round_id']):
                    raise ValueError('workflow_author_handoff_storage_corrupt')
                matches.append(qid)
        if len(matches) != 1:
            raise ValueError('workflow_author_handoff_storage_corrupt')
        return matches[0]

    async def get_handoff(self, *, handoff_id: str, lane: str, session_id: str | None,
                          selector: str | None, owner: WorkflowTemplatePrincipal | None = None) -> dict:
        task_agent = self._session(session_id) if session_id is not None else None
        if lane == 'owner-human':
            if owner is None:
                raise WorkflowActivationError('workflow_author_handoff_read_not_authorized')
            self.org.workflow_activations._principal(owner)
        elif owner is not None:
            raise WorkflowActivationError('workflow_author_handoff_invalid_request')
        selection = dict(handoff_id=handoff_id, lane=lane, task_agent=task_agent,
                         session_id=session_id, selector=selector, owner=owner)
        # Discovery is independent of current-authority readiness and H proof
        # decoding. Repeat all selected facts at the actual writer boundary.
        with self.org.sessions.binding_lease(*task_agent) if task_agent is not None else nullcontext():
            with self.db._lock:
                if session_id is not None:
                    self._session(session_id)
                if owner is not None:
                    self.org.workflow_activations._principal(owner)
                self._select_handoff(self.db._conn, **selection)
        capture = self.org.workflow_authority.capture_admission()
        async with self._lock:
            async with self.org.db_lock:
                with self.org.sessions.binding_lease(*task_agent) if task_agent is not None else nullcontext():
                    # Locks may have awaited after discovery. Recheck native
                    # identity/scope/grant before writer readiness can refuse.
                    with self.db._lock:
                        if owner is not None:
                            self.org.workflow_activations._principal(owner)
                        if session_id is not None:
                            self._session(session_id)
                        self._select_handoff(self.db._conn, **selection)
                    with self.org.workflow_authority.admission_writer(capture) as conn:
                        if owner is not None:
                            self.org.workflow_activations._principal(owner)
                        if session_id is not None:
                            self._session(session_id)
                        row, intent, actions = self._select_handoff(conn, **selection)
                        if owner is not None:
                            self._selected_authority(conn, intent, capture, actions)
                        else:
                            self._authority(conn, intent, capture, actions)
                        require_author_handoff_schema(conn, expected_org_slug=self.org.slug, _database=self.db)
                        proof = _handoff_record(conn, row, expected_org_slug=self.org.slug)
                        if lane == 'request-agent':
                            graph = validate_handoff_graph(conn, row, proof)
                            if graph is None or selector not in graph['request_ids']:
                                raise ValueError('workflow_author_handoff_storage_corrupt')
                            self.org.workflow_reviews._intent(conn, task_agent[0])
                        return self._projection(conn, row, capture, replayed=True)

    async def get_request(self, *, request_id: str, session_id: str | None, human: bool,
                          principal: WorkflowTemplatePrincipal | None = None) -> dict:
        if human:
            if principal is None or session_id is not None:
                raise WorkflowActivationError('workflow_author_handoff_read_not_authorized')
            self.org.workflow_review_receipts._human_principal(principal)
        elif principal is not None or session_id is None:
            raise WorkflowActivationError('workflow_author_handoff_read_not_authorized')
        task_agent = self._session(session_id) if session_id is not None else None
        selection = dict(request_id=request_id, task_agent=task_agent, session_id=session_id,
                         human=human, include_signed=True)
        with self.org.sessions.binding_lease(*task_agent) if task_agent is not None else nullcontext():
            with self.db._lock:
                if session_id is not None:
                    self._session(session_id)
                if principal is not None:
                    self.org.workflow_review_receipts._human_principal(principal)
                self._select_request(self.db._conn, **selection)
        capture = self.org.workflow_authority.capture_admission()
        async with self._lock:
            async with self.org.db_lock:
                with self.org.sessions.binding_lease(*task_agent) if task_agent is not None else nullcontext():
                    with self.db._lock:
                        if session_id is not None:
                            self._session(session_id)
                        if principal is not None:
                            self.org.workflow_review_receipts._human_principal(principal)
                        self._select_request(self.db._conn, **selection)
                    with self.org.workflow_authority.admission_writer(capture) as conn:
                        if session_id is not None:
                            self._session(session_id)
                        if principal is not None:
                            self.org.workflow_review_receipts._human_principal(principal)
                        request, row, intent = self._select_request(conn, **selection)
                        self._selected_authority(conn, intent, capture, {'collect-review'}) if human else self._authority(conn, intent, capture, {'collect-review'})
                        require_author_handoff_schema(conn, expected_org_slug=self.org.slug, _database=self.db)
                        scope = json.loads(request['request_scope_bytes'])
                        if scope['kind'] != ('human' if human else 'agent'):
                            raise ValueError('workflow_author_handoff_storage_corrupt')
                        proof = subject_proof(conn, row)
                        graph = validate_handoff_graph(conn, row, proof)
                        if graph is None or request_id not in graph['request_ids']:
                            raise ValueError('workflow_author_handoff_storage_corrupt')
                        if not human:
                            self.org.workflow_reviews._intent(conn, task_agent[0])
                        current = self._projection(conn, row, capture, replayed=True)['current']
                        signed = request['status'] != 'pending'
                        receipt = None
                        if signed:
                            from runtime.workflows.review_receipts import recipient_receipt
                            receipts = _records(conn, 'SELECT * FROM workflow_review_receipts WHERE request_id=?', (request_id,))
                            if len(receipts) != 1:
                                raise ValueError('workflow_author_handoff_storage_corrupt')
                            receipt = recipient_receipt(receipts[0])
                        result = dict(format=f'workflow-review-request-receipt@{review_family(scope)}', request_id=request_id,
                            scope=scope, request_scope_digest=request['request_scope_digest'], status=request['status'],
                            signed=signed, pending=not signed, receipt=receipt,
                            reconciliation_required=current['reconciliation_required'],
                            submission_bytes_base64=base64.b64encode(row['candidate_bytes']).decode('ascii'))
                        if review_family(scope) == 3:
                            from runtime.workflows.revisions import prior_review
                            result['prior_review'] = prior_review(conn, scope)
                        self._select_request(conn, **selection)
                        return result

    async def discover_completed_handoffs(self, queue: Any) -> None:
        with self.db._lock:
            if not self.db._conn.execute("SELECT 1 FROM sqlite_schema WHERE name='workflow_author_handoffs'").fetchone():
                return
            handoffs = [r[0] for r in self.db._conn.execute('SELECT h.id FROM workflow_author_handoffs h '
                'JOIN workflow_draft_dispatch_intents d ON d.id=h.intent_id WHERE d.state=\'completed\' AND NOT EXISTS '
                '(SELECT 1 FROM workflow_submission_operations o WHERE o.submission_id=h.submission_id)')]
        from runtime.workflows.authority import WorkflowAuthorityError
        for handoff_id in handoffs:
            try:
                tasks = await self.consume_completed_handoff(handoff_id)
            except WorkflowAuthorityError as exc:
                if exc.code != 'workflow_activation_authority_stale':
                    raise
                continue
            for task_id in tasks:
                queue.enqueue_if_absent(self.org.slug, task_id)
        # Reuse lifespan/reaper discovery for result-before-consumption recovery.
        # No new scheduler and no replay of an uncertain host launch.
        await self.org.workflow_revisions.discover(queue)
        await self.org.workflow_review_receipts.discover_completed()
        await self.org.workflow_review_receipts.discover_collections()
