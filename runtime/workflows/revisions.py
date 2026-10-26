"""One explicit document r1 return, a new real author, and fresh r2 review.

Layout I owns revision admission. G owns the staged bytes. Neither admission
nor staging supplies a callback, result, human signature or execution proof.
"""
from __future__ import annotations

import asyncio
import base64
import json
import sqlite3
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Any, Literal, TYPE_CHECKING

from pydantic import Field, StrictBool, StrictInt, ValidationError

from runtime.daemon.startup_recovery import publish_document_tasks, require_document_admission
from runtime.infrastructure.workflow_schema import (
    _DRAFT_MUTABLE, _handoff_json, _handoff_record, _records, _stored_json,
    require_document_revision_schema, validate_workflow_schema,
)
from runtime.models import TaskRecord
from runtime.workflows.activation import (
    AuthorityPin, ClosedRecord, SHA256, Token, WorkflowActivationError, _id, author_capacity_blocked,
)
from runtime.workflows.artifacts import context_contract, review_family
from runtime.workflows.draft_dispatch import append_event_uncommitted, canonical_bytes, digest
from runtime.workflows.submissions import _hex, _insert, _record, _source_result, validate_handoff_graph, AuthorHandoffGraph
from runtime.workflows.templates import WorkflowTemplatePrincipal


if TYPE_CHECKING:
    from runtime.daemon.startup_recovery import RecoveryOutcome


CODE = 'workflow_document_revision_storage_corrupt'


class DocumentRevisionCommand(ClosedRecord):
    format: Literal['workflow-document-revision-command@1']
    operation_key: Token
    instance_id: Token
    activation_id: Token
    expected_revision: StrictInt = Field(ge=1, le=1)
    predecessor_submission_id: Token
    predecessor_submission_digest: SHA256
    predecessor_round_id: Token
    authority: AuthorityPin


class AuthorRevisionCommand(ClosedRecord):
    format: Literal['workflow-author-revision-command@1']
    operation_key: Token
    revision_operation_id: Token
    expected_revision: StrictInt = Field(ge=2, le=2)
    artifact_kind: Literal['document']
    submission_bytes_base64: str = Field(min_length=4, max_length=1398104)
    submission_digest: SHA256


class DocumentRevisionCore(ClosedRecord):
    revision_operation_id: Token
    instance_id: Token
    activation_id: Token
    predecessor_submission_id: Token
    predecessor_round_id: Token
    expected_revision: StrictInt = Field(ge=1, le=1)
    target_revision: StrictInt = Field(ge=2, le=2)
    author_task_id: Token
    created_at: str = Field(min_length=27, max_length=27)


class RevisionCollection(ClosedRecord):
    state: Literal['collecting', 'needs_revision', 'ready_to_join', 'approved', 'held']
    approved_use: StrictBool
    validity: Literal['current', 'held']
    reason: str | None
    owner_principal: Literal['human:founder']
    required_action: str | None


class OwnerReviewOriginal(ClosedRecord):
    handoff_id: Token
    human_request_id: Token | None
    collection: RevisionCollection | None


class OwnerReviewSuccessor(ClosedRecord):
    revision_operation_id: Token
    human_request_id: Token | None


class OwnerReviewReturn(ClosedRecord):
    eligible: StrictBool
    reason: str | None
    required_action: str | None


class OwnerReviewSelection(ClosedRecord):
    format: Literal['workflow-owner-review-selection@1']
    activation_id: Token
    instance_id: Token
    original: OwnerReviewOriginal | None
    successor: OwnerReviewSuccessor | None
    return_: OwnerReviewReturn = Field(alias='return')


class DocumentRevisionCurrent(ClosedRecord):
    state: Literal['revision_author_queued', 'revision_author_running', 'revision_staged',
        'revision_reviewing', 'revision_complete', 'revision_failed', 'revision_cancelled', 'revision_held']
    reason: str | None
    reconciliation_required: StrictBool
    eligible: StrictBool
    submission_id: Token | None
    submission_digest: SHA256 | None
    graph: AuthorHandoffGraph | None
    collection: RevisionCollection | None


class DocumentRevisionReceipt(ClosedRecord):
    format: Literal['workflow-document-revision-receipt@1']
    core: DocumentRevisionCore
    replayed: StrictBool
    current: DocumentRevisionCurrent


def parse_revision(value: object, *, author: bool = False) -> dict:
    try:
        model = AuthorRevisionCommand if author else DocumentRevisionCommand
        parsed = model.model_validate(value).model_dump()
        if len(canonical_bytes(parsed)) > (2097152 if author else 1048576):
            raise ValueError('bounds')
        if author:
            content(parsed)
        return parsed
    except (ValidationError, ValueError, UnicodeError) as exc:
        raise WorkflowActivationError('workflow_document_revision_invalid_request') from exc


def content(command: dict) -> bytes:
    raw = base64.b64decode(command['submission_bytes_base64'], validate=True)
    raw.decode('utf8')
    if (not 1 <= len(raw) <= 1048576 or digest(raw) != command['submission_digest']
            or base64.b64encode(raw).decode('ascii') != command['submission_bytes_base64']):
        raise ValueError('workflow_document_revision_invalid_request')
    return raw


def exists(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='workflow_document_revision_operations'").fetchone() is not None


def successor_for_history(conn: sqlite3.Connection, instance_id: str) -> dict | None:
    """Conditional history selector; full I validation authenticates the whole successor.

    This does not recurse through the old graph or grant current effect authority.
    It checks the saved discriminator/identities before old oracles relax ONLY
    their mutable current-assignment/status assertion.
    """
    if not exists(conn):
        return None
    rows = _records(conn, 'SELECT * FROM workflow_document_revision_operations WHERE instance_id=?', (instance_id,))
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError(CODE)
    row = rows[0]
    body = _handoff_json(row['admission_bytes'], row['admission_digest'])
    intent = _record(conn, 'workflow_draft_dispatch_intents', row['draft_intent_id'])
    if (body.get('format') != 'workflow-document-revision-admission@1'
            or canonical_bytes(body.get('operation')) != canonical_bytes(_hex({k: v for k, v in row.items() if k not in ('admission_bytes', 'admission_digest')}))
            or intent['admission_kind'] != 'revision' or intent['instance_id'] != instance_id
            or intent['context_id'] != row['context_id'] or intent['activation_id'] != row['activation_id']
            or intent['request_bytes'] != row['request_bytes'] or intent['request_digest'] != row['request_digest']
            or row['expected_revision'] != 1 or row['target_revision'] != 2):
        raise ValueError(CODE)
    return row



def validate_author_history(conn: sqlite3.Connection, instance_id: str, records: list[dict]) -> None:
    """Every added author must have its own authenticated source/result closure."""
    from runtime.workflows.submissions import _reviewed_source_result
    successor = successor_for_history(conn, instance_id)
    if successor is None:
        raise ValueError(CODE)
    handoffs = _records(conn, 'SELECT * FROM workflow_author_handoffs WHERE submission_id=?', (successor['predecessor_submission_id'],))
    if len(handoffs) != 1:
        raise ValueError(CODE)
    first = handoffs[0]
    original = _record(conn, 'workflow_draft_dispatch_intents', first['intent_id'])
    result = _reviewed_source_result(conn, first, original)
    expected = [dict(instance_id=instance_id, principal=first['principal'], source_task_id=first['source_task_id'],
        source_session_id=first['source_session_id'], source_result_id=str(result['id']), contribution_kind='author')]
    intent = _record(conn, 'workflow_draft_dispatch_intents', successor['draft_intent_id'])
    links = _records(conn, 'SELECT l.* FROM workflow_submission_result_links l JOIN workflow_submissions s ON s.id=l.submission_id WHERE s.instance_id=? AND s.revision=2', (instance_id,))
    if links:
        subject = dict(submission_id=links[0]['submission_id'], source_task_id=intent['task_id'],
            source_session_id=intent['session_id'], principal=intent['assigned_principal'])
        result = _reviewed_source_result(conn, subject, intent)
        if len(links) != 1 or links[0]['task_result_id'] != result['id']:
            raise ValueError(CODE)
        expected.append(dict(instance_id=instance_id, principal=intent['assigned_principal'], source_task_id=intent['task_id'],
            source_session_id=intent['session_id'], source_result_id=str(result['id']), contribution_kind='author'))
    if canonical_bytes(sorted(records, key=lambda r: r['source_task_id'])) != canonical_bytes(sorted(expected, key=lambda r: r['source_task_id'])):
        raise ValueError(CODE)

def _predecessor(conn: sqlite3.Connection, handoff: dict, proof: dict, graph: dict) -> tuple[dict, list[dict]]:
    from runtime.workflows.review_receipts import recipient_receipt
    round_ = _record(conn, 'workflow_rounds', graph['round_id'])
    requests = [_record(conn, 'workflow_review_requests', qid) for qid in graph['request_ids']]
    if (round_['state'] != 'closed' or any(q['status'] == 'pending' for q in requests)
            or not any(q['status'] == 'changes_requested' for q in requests)):
        raise WorkflowActivationError('workflow_document_revision_predecessor_not_closed')
    receipts, feedback = [], []
    for request in requests:
        rows = _records(conn, 'SELECT * FROM workflow_review_receipts WHERE request_id=?', (request['id'],))
        if len(rows) != 1:
            raise ValueError(CODE)
        receipt = rows[0]
        scope = _stored_json(request['request_scope_bytes'], request['request_scope_digest'], code=CODE)
        safe = recipient_receipt(receipt)
        receipts.append(receipt)
        feedback.append(dict(role=scope['role'], request_id=request['id'], receipt_id=receipt['id'],
            outcome=receipt['outcome'], findings=dict(findings_bytes_base64=safe['findings_bytes_base64'],
                findings_digest=safe['findings_digest']), request_scope_digest=request['request_scope_digest'],
            receipt_digest=receipt['proof_digest']))
    return dict(handoff_id=handoff['id'], source_intent_id=handoff['intent_id'], submission_id=handoff['submission_id'],
        submission_digest=handoff['candidate_digest'], round_id=graph['round_id'], revision=1,
        request_ids=graph['request_ids'], receipt_ids=[p['id'] for p in receipts],
        receipt_digests=[p['proof_digest'] for p in receipts]), feedback


def _context(org: str, operation_id: str, source: dict, predecessor: dict, feedback: list[dict]) -> dict:
    original = _handoff_json(source['context_bytes'], source['context_digest'])
    return dict(format='workflow-document-revision-context@1', org_slug=org,
        instance_id=original['binding']['instance_id'], revision_operation_id=operation_id, target_revision=2,
        source_context_id=source['id'], source_context_digest=source['context_digest'],
        contract=context_contract(original), binding=original['binding'], authorization=original['authorization'],
        authority_snapshot=original['authority_snapshot'], inputs=original['inputs'],
        predecessor=predecessor, revision_feedback=feedback)


def revision_brief(context: dict, candidate: bytes) -> str:
    author = context['contract']['author_role']
    return ('Workflow document revision. Revise the immutable predecessor using all collected findings. '
        'Only your originally addressed inputs are supplied. Stage the exact new UTF8 bytes before '
        'your actual final callback; no delegation, coding, merge or signature authority follows.\n\n'
        + canonical_bytes(dict(revision_operation_id=context['revision_operation_id'], revision=2,
            output=context['contract']['output'], predecessor=context['predecessor'],
            submission_bytes_base64=base64.b64encode(candidate).decode('ascii'),
            revision_feedback=context['revision_feedback'],
            inputs=[item for item in context['inputs'] if author in item['pin']['recipients']])).decode())


def _pins(proof: dict, context: dict, intent: dict) -> dict:
    return dict(proof['pins'], context=dict(id=context['id'], bytes_hex=context['context_bytes'].hex(), digest=context['context_digest']),
        scope=dict(id=intent['task_id'], bytes_hex=intent['task_scope_bytes'].hex(), digest=intent['task_scope_digest']))


def validate_revision_record(conn: sqlite3.Connection, row: dict, *, expected_org_slug: str | None) -> dict:
    """Closed admission/context/task reader; no current pointer, writer or allocation."""
    try:
        command = parse_revision(_handoff_json(row['request_bytes'], row['request_digest']))
        proof = _handoff_json(row['admission_bytes'], row['admission_digest'])
        if (set(proof) != {'format', 'operation', 'client_request', 'predecessor', 'pins', 'admission_authority'}
                or proof['format'] != 'workflow-document-revision-admission@1'
                or canonical_bytes(command) != row['request_bytes'] or canonical_bytes(command) != canonical_bytes(proof['client_request'])
                or row['org_slug'] != expected_org_slug or row['principal'] != 'human:founder'
                or row['id'] != _id('workflow-document-revision', row['org_slug'], row['principal'], row['operation_key'])
                or canonical_bytes(proof['operation']) != canonical_bytes(_hex({k: v for k, v in row.items() if k not in ('admission_bytes', 'admission_digest')}))):
            raise ValueError(CODE)
        old = _records(conn, 'SELECT * FROM workflow_author_handoffs WHERE submission_id=?', (row['predecessor_submission_id'],))
        if len(old) != 1:
            raise ValueError(CODE)
        old = old[0]
        oldproof = _handoff_record(conn, old, expected_org_slug=expected_org_slug)
        graph = validate_handoff_graph(conn, old, oldproof)
        if graph is None:
            raise ValueError(CODE)
        predecessor, feedback = _predecessor(conn, old, oldproof, graph)
        first = _record(conn, 'workflow_draft_dispatch_intents', old['intent_id'])
        intent = _record(conn, 'workflow_draft_dispatch_intents', row['draft_intent_id'])
        source = _record(conn, 'workflow_contexts', first['context_id'])
        ctx = _context(row['org_slug'], row['id'], source, predecessor, feedback)
        context = _record(conn, 'workflow_contexts', row['context_id'])
        task = _record(conn, 'tasks', intent['task_id'])
        scope = dict(assigned_agent=first['assigned_principal'], team=_record(conn, 'tasks', first['task_id'])['team'],
                     brief=revision_brief(ctx, old['candidate_bytes']))
        admission = dict(org_slug=row['org_slug'], instance_id=row['instance_id'], activation_id=row['activation_id'],
            activation_revision=1, attempt_sequence=2, predecessor_intent_id=first['id'], admission_principal=row['principal'],
            operation_key=row['operation_key'], request_digest=row['request_digest'])
        expected_immutable = {k: v for k, v in first.items() if k not in _DRAFT_MUTABLE}
        expected_immutable.update(id=digest(canonical_bytes(admission)), attempt_sequence=2, predecessor_intent_id=first['id'],
            admission_kind='revision', operation_key=row['operation_key'], request_bytes=row['request_bytes'],
            request_digest=row['request_digest'], task_id=task['id'], context_id=context['id'], assignment_generation=2,
            task_scope_bytes=canonical_bytes(scope), task_scope_digest=digest(canonical_bytes(scope)),
            effect_key=f"workflow-initial-draft:{row['instance_id']}:2",
            host_execution_key='workflow-draft-host:' + row['draft_intent_id'], created_at=row['created_at'])
        if (canonical_bytes(_hex(expected_immutable)) != canonical_bytes(_hex({k: v for k, v in intent.items() if k not in _DRAFT_MUTABLE}))
                or context != dict(id=_id('workflow-context', row['instance_id'], digest(canonical_bytes(ctx))),
                    binding_snapshot_id=first['binding_snapshot_id'], context_bytes=canonical_bytes(ctx),
                    context_digest=digest(canonical_bytes(ctx)), source_kind='document-revision', source_id=row['id'])
                or len(context['context_bytes']) > 1048576 or len(row['admission_bytes']) > 16777216
                or row['activation_id'] != first['activation_id'] or row['instance_id'] != first['instance_id']
                or row['binding_snapshot_id'] != first['binding_snapshot_id']
                or row['authorization_revision_id'] != oldproof['pins']['authorization']['id']
                or row['predecessor_round_id'] != graph['round_id'] or first['is_current'] != 0
                or first['state'] != 'completed'
                or task['brief'] != scope['brief'] or task['assigned_agent'] != scope['assigned_agent']
                or task['team'] != scope['team'] or task['task_type'] != 'subtask' or task['parent_task_id'] is not None
                or task['active_chain'] is not None or task['active_fanout'] is not None
                or command != dict(format='workflow-document-revision-command@1', operation_key=row['operation_key'],
                    instance_id=row['instance_id'], activation_id=row['activation_id'], expected_revision=1,
                    predecessor_submission_id=old['submission_id'], predecessor_submission_digest=old['candidate_digest'],
                    predecessor_round_id=graph['round_id'], authority=dict(namespace='org/' + row['org_slug'],
                        generation=first['authority_generation'], snapshot_digest=first['authority_digest']))
                or canonical_bytes(proof['predecessor']) != canonical_bytes(predecessor)
                or canonical_bytes(proof['pins']) != canonical_bytes(_pins(oldproof, context, intent))
                or canonical_bytes(proof['admission_authority']) != canonical_bytes(dict(oldproof['admission_authority'],
                    actions=revision_actions(ctx)))):
            raise ValueError(CODE)
        from runtime.workflows.submissions import _reviewed_source_result
        _reviewed_source_result(conn, old, first)
        return proof
    except (ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise ValueError(CODE) from exc


def revision_actions(context: dict) -> list[str]:
    contract = context_contract(context)
    names = ('stage-immutable-artifact', 'submit-immutable-artifact') if contract['format'] == 'workflow-artifact-contract@4' else ('stage-immutable-document', 'submit-immutable-document')
    draft = 'draft-artifact' if contract['format'] == 'workflow-artifact-contract@4' else 'draft-document'
    return sorted({'return-to-author', draft, 'collect-review', 'approve-planning-input', *names})


def _stage(conn: sqlite3.Connection, row: dict, intent: dict) -> tuple[dict, dict, dict] | None:
    rows = _records(conn, 'SELECT * FROM workflow_submission_operations WHERE instance_id=? AND source_task_id=?',
                    (row['instance_id'], intent['task_id']))
    if not rows:
        if conn.execute('SELECT 1 FROM workflow_submissions WHERE instance_id=? AND revision=2', (row['instance_id'],)).fetchone():
            raise ValueError(CODE)
        return None
    if len(rows) != 1:
        raise ValueError(CODE)
    operation = rows[0]
    submission = _record(conn, 'workflow_submissions', operation['submission_id'])
    provenance = _handoff_json(operation['provenance_bytes'], operation['provenance_digest'])
    request = _handoff_json(bytes.fromhex(provenance['request_bytes']), operation['request_digest'])
    original = dict(request, format='workflow-author-revision-command@1')
    for key in ('submission_id', 'instance_id', 'revision'):
        original.pop(key)
    command = parse_revision(original, author=True)
    sid = _id('workflow-submission', row['instance_id'], 2, row['org_slug'], intent['assigned_principal'],
              command['operation_key'], 'workflow-author-revision@1')
    expected = dict(command, format='workflow-author-revision-request@1', submission_id=sid, instance_id=row['instance_id'], revision=2)
    if (canonical_bytes(request) != canonical_bytes(expected) or command['revision_operation_id'] != row['id']
            or submission != dict(id=sid, instance_id=row['instance_id'], revision=2, submission_bytes=content(command),
                submission_digest=command['submission_digest'], storage_ref=None, source_task_id=intent['task_id'],
                source_session_id=intent['session_id'], source_result_id=None, author_principal=intent['assigned_principal'])
            or operation['operation_key'] != command['operation_key'] or operation['org_slug'] != row['org_slug']
            or operation['principal'] != intent['assigned_principal']):
        raise ValueError(CODE)
    stamp = datetime.strptime(operation['created_at'], '%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=timezone.utc)
    if operation['created_at'] != stamp.strftime('%Y-%m-%dT%H:%M:%S.%fZ'):
        raise ValueError(CODE)
    launches = _records(conn, "SELECT * FROM workflow_draft_dispatch_events WHERE intent_id=? AND event_kind='launch_reserved'", (intent['id'],))
    callbacks = _records(conn, "SELECT * FROM workflow_draft_dispatch_events WHERE intent_id=? AND event_kind IN ('callback_recorded','cancel_requested')", (intent['id'],))
    if (len(launches) != 1 or stamp < datetime.fromisoformat(launches[0]['created_at'])
            or any(stamp > datetime.fromisoformat(e['created_at']) for e in callbacks)):
        raise ValueError(CODE)
    return submission, operation, command


def review_row(conn: sqlite3.Connection, operation: dict) -> dict | None:
    """Internal revision subject adapter; never persists or invents an H row."""
    intent = _record(conn, 'workflow_draft_dispatch_intents', operation['draft_intent_id'])
    stage = _stage(conn, operation, intent)
    if stage is None:
        return None
    submission, staged, _ = stage
    return dict(id=operation['id'], intent_id=intent['id'], instance_id=operation['instance_id'], org_slug=operation['org_slug'],
        principal=intent['assigned_principal'], source_task_id=intent['task_id'], source_session_id=intent['session_id'],
        operation_key=staged['operation_key'], submission_id=submission['id'], revision=2,
        candidate_bytes=submission['submission_bytes'], candidate_digest=submission['submission_digest'],
        request_bytes=bytes.fromhex(_handoff_json(staged['provenance_bytes'], staged['provenance_digest'])['request_bytes']),
        request_digest=staged['request_digest'], created_at=operation['created_at'])


def selected_subject(conn: sqlite3.Connection, scope: dict) -> tuple[dict, dict]:
    if review_family(scope) == 3:
        operation = _record(conn, 'workflow_document_revision_operations', scope['revision_operation_id'])
        proof = validate_revision_record(conn, operation, expected_org_slug=scope['org_slug'])
        row = review_row(conn, operation)
        if row is None:
            raise ValueError(CODE)
        return row, proof
    row = _record(conn, 'workflow_author_handoffs', scope['handoff_id'])
    return row, _handoff_record(conn, row, expected_org_slug=scope['org_slug'])


def revision_review_scope(*, row: dict, proof: dict, context: dict, contract: dict, role: str,
                          request_id: str, round_id: str, binding: dict, generation: int) -> dict:
    pins = proof['pins']
    return dict(format='workflow-review-request@3', org_slug=row['org_slug'], instance_id=row['instance_id'],
        revision_operation_id=row['id'], submission_id=row['submission_id'], submission_digest=row['candidate_digest'],
        revision=2, round_id=round_id, request_id=request_id, role=role, kind=contract['role_kinds'][role],
        principal=binding['principal'], team=binding['team'], assignment_generation=generation,
        template=pins['template'], activation=pins['activation'], authorization=pins['authorization']['id'],
        binding_snapshot_id=pins['binding']['id'], context_id=pins['context']['id'], output=contract['output'],
        outcomes=contract['outcomes'], purpose='Independently review the pinned immutable artifact; requests remain unsigned.',
        allowed_actions=['collect-review'], inputs=[i for i in context['inputs'] if role in i['pin']['recipients']],
        repository_resolution=None, artifact=dict(identity=dict(org_namespace=row['org_slug'], instance_id=row['instance_id'], output_slot='output'),
            submission_id=row['submission_id'], revision=2, kind='document', bytes_digest=row['candidate_digest'],
            subject=dict(content_digest=row['candidate_digest'])))


def prior_review(conn: sqlite3.Connection, scope: dict) -> dict:
    operation = _record(conn, 'workflow_document_revision_operations', scope['revision_operation_id'])
    proof = validate_revision_record(conn, operation, expected_org_slug=scope['org_slug'])
    matches = []
    for qid in proof['predecessor']['request_ids']:
        request = _record(conn, 'workflow_review_requests', qid)
        oldscope = _stored_json(request['request_scope_bytes'], request['request_scope_digest'], code=CODE)
        if oldscope['principal'] == scope['principal'] and oldscope['role'] == scope['role']:
            matches.append((request, oldscope))
    if len(matches) != 1:
        raise ValueError(CODE)
    request, oldscope = matches[0]
    submission = _record(conn, 'workflow_submissions', oldscope['submission_id'])
    from runtime.workflows.review_receipts import recipient_receipt
    receipts = _records(conn, 'SELECT * FROM workflow_review_receipts WHERE request_id=?', (request['id'],))
    if len(receipts) != 1:
        raise ValueError(CODE)
    return dict(request_id=request['id'], request_scope_digest=request['request_scope_digest'],
        submission_id=submission['id'], submission_digest=submission['submission_digest'],
        submission_bytes_base64=base64.b64encode(submission['submission_bytes']).decode('ascii'),
        receipt=recipient_receipt(receipts[0]))


def validate_revision_storage(conn: sqlite3.Connection, *, expected_org_slug: str | None) -> None:
    rows = _records(conn, 'SELECT * FROM workflow_document_revision_operations')
    owned = {r['draft_intent_id'] for r in rows}
    if owned != {r[0] for r in conn.execute("SELECT id FROM workflow_draft_dispatch_intents WHERE admission_kind='revision'")}:
        raise ValueError(CODE)
    for row in rows:
        proof = validate_revision_record(conn, row, expected_org_slug=expected_org_slug)
        intent = _record(conn, 'workflow_draft_dispatch_intents', row['draft_intent_id'])
        stage = _stage(conn, row, intent)
        rounds = _records(conn, 'SELECT * FROM workflow_rounds WHERE instance_id=? AND current_revision=2', (row['instance_id'],))
        if stage is None:
            if _record(conn, 'workflow_instances', row['instance_id'])['status'] != 'reviewing':
                raise ValueError(CODE)
            if rounds:
                raise ValueError(CODE)
            continue
        subject = review_row(conn, row)
        links = _records(conn, 'SELECT * FROM workflow_submission_result_links WHERE submission_id=?', (subject['submission_id'],))
        if not links:
            if _record(conn, 'workflow_instances', row['instance_id'])['status'] != 'reviewing':
                raise ValueError(CODE)
            if rounds or conn.execute('SELECT 1 FROM workflow_events WHERE instance_id=? AND revision=2', (row['instance_id'],)).fetchone():
                raise ValueError(CODE)
        elif validate_handoff_graph(conn, subject, proof) is None:
            raise ValueError(CODE)
    for submission in _records(conn, 'SELECT s.* FROM workflow_submissions s JOIN workflow_submission_operations o ON o.submission_id=s.id WHERE s.revision!=1'):
        if submission['revision'] != 2 or not any(r['instance_id'] == submission['instance_id'] for r in rows):
            raise ValueError(CODE)


class WorkflowRevisionStore:
    def __init__(self, org: Any) -> None:
        self.org, self.db = org, org.db
        self._startup_state = self._startup_org = self._startup_recovery = None
        self._lock = asyncio.Lock()

    def _owner(self, principal: WorkflowTemplatePrincipal, conn: sqlite3.Connection, instance_id: str) -> dict:
        actor = self.org.workflow_activations._principal(principal)
        rows = _records(conn, 'SELECT * FROM workflow_instances WHERE id=? AND owner_principal=?', (instance_id, actor))
        if len(rows) != 1:
            raise WorkflowActivationError('workflow_document_revision_not_found')
        return rows[0]

    def _authority(self, conn: sqlite3.Connection, intent: dict, capture: Any) -> dict:
        store = self.org.workflow_submissions
        saved_context = json.loads(_record(conn, 'workflow_contexts', intent['context_id'])['context_bytes'])
        if saved_context.get('format') in ('workflow-initial-draft-context@1', 'workflow-initial-draft-context@2'):
            raise WorkflowActivationError('workflow_document_revision_source_not_current')
        context, _, _ = store._selected_authority(conn, intent, capture, set(revision_actions(saved_context)))
        if (not store._current_activation(conn, intent)
                or capture.ready.generation != intent['authority_generation']
                or capture.ready.snapshot_digest != intent['authority_digest']
                or conn.execute('SELECT state FROM workflow_cutover_state WHERE singleton=1').fetchone()[0] != 'enabled'):
            raise WorkflowActivationError('workflow_activation_authority_stale')
        from runtime.workflows.review_join import captured_authority
        if intent['admission_kind'] == 'revision':
            operation = _records(conn, 'SELECT * FROM workflow_document_revision_operations WHERE draft_intent_id=?', (intent['id'],))
            if len(operation) != 1:
                raise ValueError(CODE)
            proof = validate_revision_record(conn, operation[0], expected_org_slug=self.org.slug)
        else:
            handoffs = _records(conn, 'SELECT * FROM workflow_author_handoffs WHERE intent_id=?', (intent['id'],))
            if len(handoffs) != 1:
                raise WorkflowActivationError('workflow_document_revision_source_not_current')
            proof = _handoff_record(conn, handoffs[0], expected_org_slug=self.org.slug)
        actions = revision_actions(context)
        if canonical_bytes(captured_authority(capture, actions)) != canonical_bytes(dict(proof['admission_authority'], actions=actions)):
            raise WorkflowActivationError('workflow_activation_authority_stale')
        original = intent if intent['admission_kind'] != 'revision' else _record(conn, 'workflow_draft_dispatch_intents', intent['predecessor_intent_id'])
        if _source_result(conn, original) is None:
            raise WorkflowActivationError('workflow_author_handoff_source_not_current')
        if intent['admission_kind'] == 'revision' and intent['state'] == 'completed' and _source_result(conn, intent) is None:
            raise WorkflowActivationError('workflow_author_handoff_source_not_current')
        return context

    def _return_source(self, conn: sqlite3.Connection, instance: dict, first: dict, context: dict, capture: Any,
                       command: dict | None = None) -> tuple[dict, dict, dict, dict, list[dict], dict]:
        """Complete read-only readiness; start consumes it inside its atomic writer."""
        contract = context_contract(context)
        if (instance['status'] != 'reviewing' or not first['is_current'] or first['state'] != 'completed'
                or _source_result(conn, first) is None or 'changes_requested' not in contract['outcomes']
                or not contract.get('request_changes') or successor_for_history(conn, instance['id']) is not None):
            raise WorkflowActivationError('workflow_document_revision_source_not_current')
        if command is None:
            old = _records(conn, 'SELECT * FROM workflow_author_handoffs WHERE instance_id=? AND intent_id=?',
                           (instance['id'], first['id']))
        else:
            old = _records(conn, 'SELECT * FROM workflow_author_handoffs WHERE instance_id=? AND submission_id=?',
                           (instance['id'], command['predecessor_submission_id']))
        if len(old) != 1:
            raise WorkflowActivationError('workflow_document_revision_source_not_current')
        old = old[0]
        oldproof = _handoff_record(conn, old, expected_org_slug=self.org.slug)
        graph = validate_handoff_graph(conn, old, oldproof)
        if graph is None:
            raise WorkflowActivationError('workflow_document_revision_source_not_current')
        predecessor, feedback = _predecessor(conn, old, oldproof, graph)
        if command is not None and (command['activation_id'] != first['activation_id'] or command['predecessor_round_id'] != graph['round_id']
                or command['predecessor_submission_digest'] != old['candidate_digest']
                or command['authority'] != dict(namespace='org/' + self.org.slug, generation=first['authority_generation'], snapshot_digest=first['authority_digest'])):
            raise WorkflowActivationError('workflow_document_revision_source_not_current')
        from runtime.workflows.review_join import captured_authority
        authority = captured_authority(capture, revision_actions(context))
        if canonical_bytes(authority) != canonical_bytes(dict(oldproof['admission_authority'], actions=revision_actions(context))):
            raise WorkflowActivationError('workflow_activation_authority_stale')
        if author_capacity_blocked(conn, author=first['assigned_principal'], task_id=first['task_id'], active_sessions=tuple(self.org.sessions.iter_active())):
            raise WorkflowActivationError('workflow_activation_author_pending')
        return old, oldproof, graph, predecessor, feedback, authority

    async def start(self, *, principal: WorkflowTemplatePrincipal, command: object) -> dict:
        require_document_admission(self)
        command = parse_revision(command)
        store = self.org.workflow_submissions
        with self.db._lock:
            require_document_admission(self)
            self._owner(principal, self.db._conn, command['instance_id'])
            first = _records(self.db._conn, 'SELECT * FROM workflow_draft_dispatch_intents WHERE instance_id=? AND attempt_sequence=1', (command['instance_id'],))
            if len(first) != 1:
                raise WorkflowActivationError('workflow_document_revision_not_found')
            store._read_grant(self.db._conn, first[0], {'return-to-author'}, principal)
        capture = self.org.workflow_authority.capture_admission()
        notifications = []
        async with store._lock:
            require_document_admission(self)
            async with self.org.db_lock:
                require_document_admission(self)
                with self.db._lock:
                    require_document_admission(self)
                    self._owner(principal, self.db._conn, command['instance_id'])
                    store._read_grant(self.db._conn, first[0], {'return-to-author'}, principal)
                with self.org.workflow_authority.admission_writer(capture) as conn:
                    require_document_admission(self)
                    instance = self._owner(principal, conn, command['instance_id'])
                    first = _record(conn, 'workflow_draft_dispatch_intents', first[0]['id'])
                    store._read_grant(conn, first, {'return-to-author'}, principal)
                    require_document_revision_schema(conn, expected_org_slug=self.org.slug)
                    context = self._authority(conn, first, capture)
                    raw = canonical_bytes(command)
                    existing = _records(conn, 'SELECT * FROM workflow_document_revision_operations WHERE org_slug=? AND principal=? AND operation_key=?',
                        (self.org.slug, principal.operation_principal, command['operation_key']))
                    if existing:
                        if len(existing) != 1 or existing[0]['request_bytes'] != raw:
                            raise WorkflowActivationError('workflow_document_revision_operation_conflict')
                        return self._projection(conn, existing[0], capture, replayed=True)
                    old, oldproof, graph, predecessor, feedback, authority = self._return_source(
                        conn, instance, first, context, capture, command)
                    opid = _id('workflow-document-revision', self.org.slug, principal.operation_principal, command['operation_key'])
                    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                    source = _record(conn, 'workflow_contexts', first['context_id'])
                    ctx = _context(self.org.slug, opid, source, predecessor, feedback)
                    ctxraw = canonical_bytes(ctx)
                    if len(ctxraw) > 1048576:
                        raise WorkflowActivationError('workflow_activation_context_too_large')
                    ctxid = _id('workflow-context', instance['id'], digest(ctxraw))
                    task_id = self.db.next_task_id()
                    task = TaskRecord(id=task_id, assigned_agent=first['assigned_principal'], team=_record(conn, 'tasks', first['task_id'])['team'],
                        brief=revision_brief(ctx, old['candidate_bytes']), task_type='subtask')
                    admission = dict(org_slug=self.org.slug, instance_id=instance['id'], activation_id=first['activation_id'], activation_revision=1,
                        attempt_sequence=2, predecessor_intent_id=first['id'], admission_principal=principal.operation_principal,
                        operation_key=command['operation_key'], request_digest=digest(raw))
                    iid = digest(canonical_bytes(admission))
                    scope = canonical_bytes(dict(assigned_agent=task.assigned_agent, team=task.team, brief=task.brief))
                    intent = dict(first, id=iid, attempt_sequence=2, predecessor_intent_id=first['id'], admission_kind='revision',
                        operation_key=command['operation_key'], request_bytes=raw, request_digest=digest(raw), task_id=task_id,
                        context_id=ctxid, assignment_generation=2, task_scope_bytes=scope, task_scope_digest=digest(scope),
                        effect_key=f"workflow-initial-draft:{instance['id']}:2", host_execution_key='workflow-draft-host:' + iid,
                        is_current=1, state='queued', cancellation_requested=0, claim_token=None, claim_owner=None,
                        host_launch_started=0, host_execution_id=None, session_id=None, final_result_id=None,
                        last_error=None, created_at=now, updated_at=now)
                    self.db._check_team_move_workflow_uncommitted(instance["id"])
                    self.db._insert_task_uncommitted(task)
                    ctxrow = dict(id=ctxid, binding_snapshot_id=first['binding_snapshot_id'], context_bytes=ctxraw,
                        context_digest=digest(ctxraw), source_kind='document-revision', source_id=opid)
                    _insert(conn, 'workflow_contexts', ctxrow)
                    events = _records(conn, "SELECT * FROM workflow_draft_dispatch_events WHERE intent_id=? AND event_kind IN ('completed','host_reconciled') ORDER BY event_seq", (first['id'],))
                    result = _stored_json(events[-1]['event_bytes'], events[-1]['event_digest'])['result']
                    self.org.workflow_drafts._event(conn, first, 'retired', changes={'is_current': 0}, result=result)
                    _insert(conn, 'workflow_draft_dispatch_intents', intent)
                    row = dict(id=opid, instance_id=instance['id'], org_slug=self.org.slug, principal=principal.operation_principal,
                        operation_key=command['operation_key'], predecessor_submission_id=old['submission_id'], predecessor_round_id=graph['round_id'],
                        expected_revision=1, target_revision=2, draft_intent_id=iid, activation_id=first['activation_id'],
                        binding_snapshot_id=first['binding_snapshot_id'], context_id=ctxid,
                        authorization_revision_id=oldproof['pins']['authorization']['id'], request_bytes=raw, request_digest=digest(raw), created_at=now)
                    proof = dict(format='workflow-document-revision-admission@1', operation=_hex(row), client_request=command,
                        predecessor=predecessor, pins=_pins(oldproof, ctxrow, intent), admission_authority=authority)
                    proofraw = canonical_bytes(proof)
                    if len(proofraw) > 16777216:
                        raise WorkflowActivationError('workflow_activation_context_too_large')
                    row.update(admission_bytes=proofraw, admission_digest=digest(proofraw))
                    _insert(conn, 'workflow_document_revision_operations', row)
                    append_event_uncommitted(conn, org_slug=self.org.slug, intent_id=iid, kind='admitted', before=None)
                    validate_workflow_schema(conn, expected_org_slug=self.org.slug)
                    receipt = self._projection(conn, row, capture, replayed=False)
                    notifications.append(task_id)
        queue = getattr(self.org.orchestrator, '_queue', None)
        if queue is not None:
            publish_document_tasks(self, queue, notifications)
        return receipt

    def _select(self, conn: sqlite3.Connection, operation_id: str, *, principal: WorkflowTemplatePrincipal | None,
                session_id: str | None) -> tuple[dict, dict]:
        if not exists(conn):
            raise WorkflowActivationError('workflow_document_revision_not_found')
        rows = _records(conn, 'SELECT * FROM workflow_document_revision_operations WHERE id=? AND org_slug=?', (operation_id, self.org.slug))
        if len(rows) != 1:
            raise WorkflowActivationError('workflow_document_revision_not_found')
        row = rows[0]
        intent = _record(conn, 'workflow_draft_dispatch_intents', row['draft_intent_id'])
        if principal is not None:
            self._owner(principal, conn, row['instance_id'])
        elif session_id is not None:
            task, agent = self.org.workflow_submissions._session(session_id)
            if (task, agent, session_id) != (intent['task_id'], intent['assigned_principal'], intent['session_id']):
                raise WorkflowActivationError('workflow_document_revision_not_found')
        else:
            raise WorkflowActivationError('workflow_document_revision_read_not_authorized')
        self.org.workflow_submissions._read_grant(conn, intent, {'return-to-author'}, principal)
        return row, intent

    async def get(self, *, operation_id: str, principal: WorkflowTemplatePrincipal | None, session_id: str | None) -> dict:
        if (principal is None) == (session_id is None):
            raise WorkflowActivationError('workflow_document_revision_invalid_request')
        store = self.org.workflow_submissions
        selection = dict(principal=principal, session_id=session_id)
        with self.db._lock:
            self._select(self.db._conn, operation_id, **selection)
        capture = self.org.workflow_authority.capture_admission()
        async with store._lock:
            async with self.org.db_lock:
                task_agent = store._session(session_id) if session_id else None
                with self.org.sessions.binding_lease(*task_agent) if task_agent else nullcontext():
                    with self.org.workflow_authority.admission_writer(capture) as conn:
                        row, intent = self._select(conn, operation_id, **selection)
                        store._selected_authority(conn, intent, capture, {'return-to-author'})
                        validate_revision_record(conn, row, expected_org_slug=self.org.slug)
                        result = self._projection(conn, row, capture, replayed=True)
                        self._select(conn, operation_id, **selection)
                        return result

    async def stage(self, *, session_id: str, command: object) -> dict:
        require_document_admission(self)
        command = parse_revision(command, author=True)
        store = self.org.workflow_submissions
        with self.db._lock:
            require_document_admission(self)
            self._select(self.db._conn, command['revision_operation_id'], principal=None, session_id=session_id)
        capture = self.org.workflow_authority.capture_admission()
        async with store._lock:
            require_document_admission(self)
            async with self.org.db_lock:
                require_document_admission(self)
                task, agent = store._session(session_id)
                with self.org.sessions.binding_lease(task, agent):
                    require_document_admission(self)
                    with self.org.workflow_authority.admission_writer(capture) as conn:
                        require_document_admission(self)
                        row, intent = self._select(conn, command['revision_operation_id'], principal=None, session_id=session_id)
                        context = self._authority(conn, intent, capture)
                        require_document_revision_schema(conn, expected_org_slug=self.org.slug)
                        validate_revision_record(conn, row, expected_org_slug=self.org.slug)
                        store._fresh(conn, intent, task, agent, session_id)
                        existing = _stage(conn, row, intent)
                        if existing:
                            if canonical_bytes(existing[2]) != canonical_bytes(command):
                                raise WorkflowActivationError('workflow_document_revision_operation_conflict')
                            return self._projection(conn, row, capture, replayed=True)
                        if conn.execute('SELECT 1 FROM workflow_submission_operations WHERE org_slug=? AND principal=? AND operation_key=?',
                                        (self.org.slug, agent, command['operation_key'])).fetchone():
                            raise WorkflowActivationError('workflow_document_revision_operation_conflict')
                        sid = _id('workflow-submission', row['instance_id'], 2, self.org.slug, agent, command['operation_key'], 'workflow-author-revision@1')
                        submission = dict(id=sid, instance_id=row['instance_id'], revision=2, submission_bytes=content(command),
                            submission_digest=command['submission_digest'], storage_ref=None, source_task_id=task,
                            source_session_id=session_id, source_result_id=None, author_principal=agent)
                        request = dict(command, format='workflow-author-revision-request@1', submission_id=sid, instance_id=row['instance_id'], revision=2)
                        now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                        operation = dict(submission_id=sid, org_slug=self.org.slug, principal=agent, operation_key=command['operation_key'],
                            request_digest=digest(canonical_bytes(request)), instance_id=row['instance_id'], source_task_id=task,
                            source_session_id=session_id, created_at=now)
                        provenance = canonical_bytes(dict(format='workflow-submission-operation@1', operation=operation,
                            submission=_hex(submission), source_intent=_hex({k: v for k, v in intent.items() if k not in _DRAFT_MUTABLE}),
                            request_bytes=canonical_bytes(request).hex()))
                        _insert(conn, 'workflow_submissions', submission)
                        _insert(conn, 'workflow_submission_operations', dict(operation, provenance_bytes=provenance, provenance_digest=digest(provenance)))
                        validate_workflow_schema(conn, expected_org_slug=self.org.slug)
                        self._select(conn, row['id'], principal=None, session_id=session_id)
                        return self._projection(conn, row, capture, replayed=False)

    def _projection(self, conn: sqlite3.Connection, row: dict, capture: Any, *, replayed: bool) -> dict:
        proof = validate_revision_record(conn, row, expected_org_slug=self.org.slug)
        intent = _record(conn, 'workflow_draft_dispatch_intents', row['draft_intent_id'])
        subject = review_row(conn, row)
        graph = None
        collection = dict(state='needs_revision', approved_use=False, validity='held',
            reason='noncurrent_successor', owner_principal='human:founder', required_action='await_revision_author')
        reason = None
        try:
            self._authority(conn, intent, capture)
            if not intent['is_current'] or intent['cancellation_requested']:
                reason = 'source_not_current'
        except WorkflowActivationError as exc:
            from runtime.workflows.review_join import HOLDS
            if exc.code not in HOLDS:
                raise
            reason = exc.code
        if intent['state'] == 'uncertain':
            reason = 'source_uncertain'
        state = 'revision_author_queued' if intent['state'] == 'queued' else 'revision_author_running'
        if subject is not None:
            state = 'revision_staged'
            links = _records(conn, 'SELECT * FROM workflow_submission_result_links WHERE submission_id=?', (subject['submission_id'],))
            if links:
                graph = validate_handoff_graph(conn, subject, proof)
                from runtime.workflows.review_join import validate_collection
                collected = validate_collection(conn, subject, proof, graph)
                collection = dict(state=collected, approved_use=collected == 'approved' and reason is None,
                    validity='held' if reason else 'current', reason=reason, owner_principal='human:founder',
                    required_action='reconcile_source_or_authority' if reason else ('revise_document' if collected == 'needs_revision' else None))
                state = 'revision_complete' if collected == 'approved' else 'revision_reviewing'
        if intent['state'] in ('failed', 'cancelled'):
            state = 'revision_' + intent['state']
            reason = 'source_' + intent['state']
        elif reason:
            state = 'revision_held'
        core = dict(revision_operation_id=row['id'], instance_id=row['instance_id'], activation_id=row['activation_id'],
            predecessor_submission_id=row['predecessor_submission_id'], predecessor_round_id=row['predecessor_round_id'],
            expected_revision=1, target_revision=2, author_task_id=intent['task_id'], created_at=row['created_at'])
        return dict(format='workflow-document-revision-receipt@1', core=core, replayed=replayed,
            current=dict(state=state, reason=reason, reconciliation_required=intent['state'] == 'uncertain' or reason is not None,
                eligible=reason is None and intent['state'] not in ('failed', 'cancelled'),
                submission_id=None if subject is None else subject['submission_id'],
                submission_digest=None if subject is None else subject['candidate_digest'], graph=graph, collection=collection))

    async def fulfill(self, operation_id: str) -> list[str]:
        require_document_admission(self)
        capture = self.org.workflow_authority.capture_admission()
        store = self.org.workflow_submissions
        async with store._lock:
            require_document_admission(self)
            async with self.org.db_lock:
                require_document_admission(self)
                return self._fulfill_core(operation_id, capture)[0]

    def _fulfill_core(self, operation_id: str, capture: Any, *, recovery: Any = None
                      ) -> tuple[list[str], Literal['settled', 'local_hold']]:
        require_document_admission(self, recovery=recovery)
        with self.org.workflow_authority.admission_writer(capture) as conn:
            require_document_admission(self, recovery=recovery)
            row = _record(conn, 'workflow_document_revision_operations', operation_id)
            proof = validate_revision_record(conn, row, expected_org_slug=self.org.slug)
            intent = _record(conn, 'workflow_draft_dispatch_intents', row['draft_intent_id'])
            from runtime.workflows.review_join import HOLDS
            try:
                context = self._authority(conn, intent, capture)
            except WorkflowActivationError as exc:
                if exc.code in HOLDS:
                    return [], 'local_hold'
                raise
            if not intent['is_current'] or intent['cancellation_requested']:
                return [], 'local_hold'
            stage = _stage(conn, row, intent)
            result = _source_result(conn, intent)
            if stage is None or result is None or result['decision_json'] is not None or result['waiting_on_job_ids'] is not None:
                return [], 'local_hold'
            subject = review_row(conn, row)
            if conn.execute('SELECT 1 FROM workflow_submission_result_links WHERE submission_id=?', (subject['submission_id'],)).fetchone():
                validate_handoff_graph(conn, subject, proof)
                return [], 'settled'
            require_document_admission(self, recovery=recovery)
            now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            raw = canonical_bytes(result)
            _insert(conn, 'workflow_submission_result_links', dict(submission_id=subject['submission_id'], task_result_id=result['id'],
                source_task_id=intent['task_id'], source_session_id=intent['session_id'], result_bytes=raw,
                result_digest=digest(raw), accepted_at=now))
            contribution = dict(principal=intent['assigned_principal'], source_task_id=intent['task_id'],
                source_session_id=intent['session_id'], source_result_id=str(result['id']), contribution_kind='author')
            _insert(conn, 'workflow_submission_contributors', dict(submission_id=subject['submission_id'], **contribution))
            _insert(conn, 'workflow_instance_contributors', dict(instance_id=row['instance_id'], **contribution))
            tasks = self._review_graph(conn, subject, proof, context, intent, now, recovery=recovery)
            validate_workflow_schema(conn, expected_org_slug=self.org.slug)
            return tasks, 'settled'

    def _review_graph(self, conn: sqlite3.Connection, subject: dict, proof: dict, context: dict, intent: dict, now: str,
                      *, recovery: Any = None) -> list[str]:
        require_document_admission(self, recovery=recovery)
        from runtime.workflows.submissions import review_brief
        contract = context_contract(context)
        self.db._check_team_move_workflow_uncommitted(subject["instance_id"])
        round_id = _id('workflow-round', subject['instance_id'], 2, subject['submission_id'])
        _insert(conn, 'workflow_rounds', dict(id=round_id, instance_id=subject['instance_id'], submission_id=subject['submission_id'], current_revision=2, state='reviewing'))
        request_ids, task_ids = [], []
        for role in contract['reviewer_roles']:
            selected = context['binding']['bindings'][role]
            # The authenticated r1 graph has just validated every retained generation.
            generations = [json.loads(q['request_scope_bytes'])['assignment_generation'] for q in _records(conn,
                'SELECT q.* FROM workflow_review_requests q JOIN workflow_rounds r ON r.id=q.round_id WHERE r.instance_id=? AND r.current_revision=1', (subject['instance_id'],))
                if json.loads(q['request_scope_bytes'])['role'] == role]
            if generations != [1]:
                raise ValueError(CODE)
            generation = 1 + max(generations)
            old = conn.execute('SELECT * FROM workflow_current_assignments WHERE instance_id=? AND role_key=?', (subject['instance_id'], role)).fetchone()
            if old is not None:
                conn.execute('DELETE FROM workflow_current_assignments WHERE instance_id=? AND role_key=? AND task_id=? AND generation=?',
                             (subject['instance_id'], role, old['task_id'], old['generation']))
            qid = _id('workflow-request', round_id, role, selected['principal'], generation)
            scope = revision_review_scope(row=subject, proof=proof, context=context, contract=contract, role=role,
                request_id=qid, round_id=round_id, binding=selected, generation=generation)
            raw = canonical_bytes(scope)
            if len(raw) > 1048576:
                raise WorkflowActivationError('workflow_activation_context_too_large')
            _insert(conn, 'workflow_review_requests', dict(id=qid, round_id=round_id, principal=selected['principal'], assignment_generation=generation,
                request_scope_bytes=raw, request_scope_digest=digest(raw), status='pending', supersedes_request_id=None))
            request_ids.append(qid)
            if contract['role_kinds'][role] == 'human':
                continue
            task_id = self.db.next_task_id()
            self.db._insert_task_uncommitted(TaskRecord(id=task_id, assigned_agent=selected['principal'], team=selected['team'],
                brief=review_brief(scope, subject['candidate_bytes']), task_type='subtask'))
            task_ids.append(task_id)
            opid = _id('workflow-dispatch', qid, generation)
            _insert(conn, 'workflow_dispatch_operations', dict(id=opid, org_slug=self.org.slug, principal=selected['principal'], operation_key=qid,
                request_digest=digest(raw), instance_id=subject['instance_id'], round_id=round_id, request_id=qid, state='admitted', created_at=now))
            _insert(conn, 'workflow_request_task_bridges', dict(request_id=qid, operation_id=opid, instance_id=subject['instance_id'], task_id=task_id,
                assigned_principal=selected['principal'], assignment_generation=generation, session_id=None, result_id=None, state='queued', created_at=now))
            _insert(conn, 'workflow_dispatch_outbox', dict(id=_id('workflow-outbox', opid), operation_id=opid, request_id=qid, effect_key='workflow-review:' + qid,
                authority_namespace=intent['authority_namespace'], authority_generation=intent['authority_generation'], authority_digest=intent['authority_digest'],
                artifact_revision=2, state='queued', claim_token=None, claim_owner=None, host_launch_started=0, host_execution_key='workflow-review-host:' + opid,
                host_execution_id=None, recovery_owner='workflow_recovery', last_error=None, created_at=now, updated_at=now))
        graph = dict(submission_id=subject['submission_id'], round_id=round_id, request_ids=request_ids, reviewer_task_ids=task_ids)
        raw = canonical_bytes(dict(event_kind='submitted', instance_id=subject['instance_id'], revision=2, submission_digest=subject['candidate_digest'], **graph))
        _insert(conn, 'workflow_events', dict(id=_id('workflow-event', subject['instance_id'], 2, 'submitted'), instance_id=subject['instance_id'],
            revision=2, event_kind='submitted', event_bytes=raw, event_digest=digest(raw), created_at=now))
        return task_ids

    def author_instruction(self, task_id: str, session_id: str) -> str:
        with self.db._lock:
            if not exists(self.db._conn):
                return ''
            rows = _records(self.db._conn, 'SELECT r.* FROM workflow_document_revision_operations r JOIN workflow_draft_dispatch_intents d ON d.id=r.draft_intent_id WHERE d.task_id=?', (task_id,))
            if not rows:
                return ''
            if len(rows) != 1:
                raise ValueError(CODE)
            row = rows[0]
            validate_revision_record(self.db._conn, row, expected_org_slug=self.org.slug)
        capture = self.org.workflow_authority.capture_admission()
        with self.db._lock:
            intent = _record(self.db._conn, 'workflow_draft_dispatch_intents', row['draft_intent_id'])
            self._authority(self.db._conn, intent, capture)
            if not intent['is_current'] or intent['host_launch_started'] or intent['state'] not in ('queued', 'claimed') or intent['cancellation_requested']:
                raise WorkflowActivationError('workflow_author_handoff_source_not_current')
        return (f'\nWorkflow new revision author: stage your immutable r2 before the final callback with '
            f'happyranch workflows revisions stage {row["id"]} --org {self.org.slug} --session-id {session_id} '
            '--from-file <absolute-command.json> --json. The closed command has format workflow-author-revision-command@1, '
            f'revision_operation_id {row["id"]}, expected_revision 2, artifact_kind document, operation_key (one stable original key), '
            'submission_bytes_base64 and submission_digest (SHA256 of exact UTF8 bytes). Preserve the same key/body after transport loss. '
            'Staging is unsigned and creates no result or review graph. Only your own genuine full INTEGER callback and committed '
            'successful terminal with Boolean host_quiescent=true can fulfill it under current authority. '
            'Use your actual task/session report-completion as the FINAL session action.\n')

    def _completed_revision_ids(self) -> list[str] | None:
        with self.db._lock:
            if not exists(self.db._conn):
                return
            ids = [r[0] for r in self.db._conn.execute('SELECT r.id FROM workflow_document_revision_operations r JOIN workflow_draft_dispatch_intents d ON d.id=r.draft_intent_id WHERE d.state=\'completed\'')]
        return ids

    async def discover(self, queue: Any) -> None:
        require_document_admission(self)
        if queue is not self._startup_state.queue:
            from runtime.daemon.startup_recovery import StartupUnavailable
            raise StartupUnavailable("startup_document_queue_changed")
        ids = self._completed_revision_ids()
        if ids is None:
            return
        from runtime.workflows.authority import WorkflowAuthorityError
        for operation_id in ids:
            try:
                tasks = await self.fulfill(operation_id)
            except WorkflowAuthorityError as exc:
                if exc.code != 'workflow_activation_authority_stale':
                    raise
                continue
            publish_document_tasks(self, queue, tasks)

    def discover_native(self, queue: Any, recovery: Any) -> RecoveryOutcome:
        from runtime.daemon.startup_recovery import RecoveryOutcome
        require_document_admission(self, recovery=recovery)
        if queue is not recovery.state.queue:
            raise RuntimeError('startup_workflow_queue_owner_unavailable')
        ids = self._completed_revision_ids()
        if ids is None:
            return RecoveryOutcome('settled', 'startup_revision_family_absent')
        from runtime.workflows.authority import WorkflowAuthorityError
        held = False
        for operation_id in ids:
            require_document_admission(self, recovery=recovery)
            try:
                tasks, disposition = self._fulfill_core(
                    operation_id, self.org.workflow_authority.capture_admission(), recovery=recovery)
            except WorkflowAuthorityError as exc:
                if exc.code != 'workflow_activation_authority_stale':
                    raise
                held = True
                continue
            if disposition not in ('settled', 'local_hold'):
                return RecoveryOutcome('unknown', 'startup_revision_outcome_unknown')
            held |= disposition == 'local_hold'
            for task_id in tasks:
                recovery.publish_workflow_task(self.org, task_id)
        require_document_admission(self, recovery=recovery)
        return RecoveryOutcome('local_hold' if held else 'settled')

    def completed(self, task_id: str) -> None:
        with self.org.workflow_drafts._completed_followup(task_id):
            require_document_admission(self)
            with self.db._lock:
                if not exists(self.db._conn):
                    return
                ids = [r[0] for r in self.db._conn.execute('SELECT r.id FROM workflow_document_revision_operations r JOIN workflow_draft_dispatch_intents d ON d.id=r.draft_intent_id WHERE d.task_id=?', (task_id,))]
            for operation_id in ids:
                require_document_admission(self)
                tasks = self.org.workflow_drafts._on_loop(self.fulfill(operation_id))
                queue = getattr(self.org.orchestrator, '_queue', None)
                if queue is not None:
                    publish_document_tasks(self, queue, tasks)
