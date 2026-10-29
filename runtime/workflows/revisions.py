"""One explicit document r1 return, a new real author, and fresh r2 review.

Layout I owns revision admission. G owns the staged bytes. Neither admission
nor staging supplies a callback, result, human signature or execution proof.
"""
from __future__ import annotations

from runtime.infrastructure._workflow_reader import (
    _b64decode as _reader_b64decode,
    _b64encode as _reader_b64encode,
    _decode as _reader_decode,
    _dump as _reader_dump,
    _hex as _reader_hex,
)

from runtime.infrastructure._workflow_reader import (
    _append as _reader_append,
    _update as _reader_update,
)

from runtime.infrastructure._workflow_reader import (
    _any_true as _reader_any_true,
)

from runtime.infrastructure._workflow_reader import (
    _dict_pairs as _reader_dict_pairs,
    _list as _reader_list,
    _set as _reader_set,
    _sorted as _reader_sorted,
)

from runtime.infrastructure._workflow_reader import (
    _all as _reader_all, _one as _reader_one, _rows as _reader_rows,
    _json as _reader_json, _reserve as _reader_reserve, _work as _reader_work,
    _sha256 as _reader_sha256, _fromhex as _reader_fromhex,
)

import asyncio
import base64
import json
import sqlite3
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import Field, StrictBool, StrictInt, ValidationError

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


def parse_revision(value: object, *, author: bool = False, budget=None) -> dict:
    _reader_work((_reader_reserve(budget, 96) or (value,author)), budget, copies=2)
    try:
        _reader_reserve(budget, 112)
        model = AuthorRevisionCommand if author else DocumentRevisionCommand
        parsed = _reader_dump(model.model_validate(value), budget=budget)
        if len(canonical_bytes(parsed, budget=budget)) > (2097152 if author else 1048576):
            raise ValueError('bounds')
        if author:
            content(parsed, budget=budget)
        return parsed
    except ((ValidationError, ValueError, UnicodeError)) as exc:
        raise WorkflowActivationError('workflow_document_revision_invalid_request') from exc


def content(command: dict, *, budget=None) -> bytes:
    _reader_work((_reader_reserve(budget, 80) or (command,)), budget, copies=2)
    raw = _reader_b64decode(command['submission_bytes_base64'], validate=True, budget=budget)
    _reader_decode(raw, 'utf8', budget=budget)
    if (not 1 <= len(raw) <= 1048576 or digest(raw, budget=budget) != command['submission_digest']
            or _reader_decode(_reader_b64encode(raw, budget=budget), 'ascii', budget=budget) != command['submission_bytes_base64']):
        raise ValueError('workflow_document_revision_invalid_request')
    return raw


def exists(conn: sqlite3.Connection, *, budget=None) -> bool:
    if budget is not None:
        budget.step()
    return _reader_one(conn, "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='workflow_document_revision_operations'", budget=budget) is not None


def successor_for_history(conn: sqlite3.Connection, instance_id: str, *, budget=None) -> dict | None:
    """Conditional history selector; full I validation authenticates the whole successor.

    This does not recurse through the old graph or grant current effect authority.
    It checks the saved discriminator/identities before old oracles relax ONLY
    their mutable current-assignment/status assertion.
    """
    _reader_work((_reader_reserve(budget, 80) or (instance_id,)), budget, copies=2)
    if not exists(conn, budget=budget):
        return None
    rows = _records(conn, 'SELECT * FROM workflow_document_revision_operations WHERE instance_id=?', (_reader_reserve(budget, 80) or (instance_id,)), budget=budget)
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError(CODE)
    row = rows[0]
    body = _handoff_json(row['admission_bytes'], row['admission_digest'], budget=budget)
    intent = _record(conn, 'workflow_draft_dispatch_intents', row['draft_intent_id'], budget=budget)
    if (body.get('format') != 'workflow-document-revision-admission@1'
            or canonical_bytes(body.get('operation'), budget=budget) != canonical_bytes(_hex(_reader_dict_pairs(((_reader_reserve(budget, 96) or (k,  v )) for k, v in row.items() if k not in ('admission_bytes', 'admission_digest')), budget=budget), budget=budget), budget=budget)
            or intent['admission_kind'] != 'revision' or intent['instance_id'] != instance_id
            or intent['context_id'] != row['context_id'] or intent['activation_id'] != row['activation_id']
            or intent['request_bytes'] != row['request_bytes'] or intent['request_digest'] != row['request_digest']
            or row['expected_revision'] != 1 or row['target_revision'] != 2):
        raise ValueError(CODE)
    return row



def validate_author_history(conn: sqlite3.Connection, instance_id: str, records: list[dict], *, budget=None) -> None:
    """Every added author must have its own authenticated source/result closure."""
    _reader_work((_reader_reserve(budget, 96) or (instance_id,records)), budget, copies=2)
    from runtime.workflows.submissions import _reviewed_source_result
    successor = successor_for_history(conn, instance_id, budget=budget)
    if successor is None:
        raise ValueError(CODE)
    handoffs = _records(conn, 'SELECT * FROM workflow_author_handoffs WHERE submission_id=?', (_reader_reserve(budget, 80) or (successor['predecessor_submission_id'],)), budget=budget)
    if len(handoffs) != 1:
        raise ValueError(CODE)
    first = handoffs[0]
    original = _record(conn, 'workflow_draft_dispatch_intents', first['intent_id'], budget=budget)
    result = _reviewed_source_result(conn, first, original, budget=budget)
    expected = (_reader_reserve(budget, 192) or [(_reader_reserve(budget, 832) or dict(instance_id=instance_id, principal=first['principal'], source_task_id=first['source_task_id'],
        source_session_id=first['source_session_id'], source_result_id=(_reader_reserve(budget, 128) or str(result['id'])), contribution_kind='author'))])
    intent = _record(conn, 'workflow_draft_dispatch_intents', successor['draft_intent_id'], budget=budget)
    links = _records(conn, 'SELECT l.* FROM workflow_submission_result_links l JOIN workflow_submissions s ON s.id=l.submission_id WHERE s.instance_id=? AND s.revision=2', (_reader_reserve(budget, 80) or (instance_id,)), budget=budget)
    if links:
        subject = (_reader_reserve(budget, 576) or dict(submission_id=links[0]['submission_id'], source_task_id=intent['task_id'],
            source_session_id=intent['session_id'], principal=intent['assigned_principal']))
        result = _reviewed_source_result(conn, subject, intent, budget=budget)
        if len(links) != 1 or links[0]['task_result_id'] != result['id']:
            raise ValueError(CODE)
        _reader_append(expected, _reader_reserve(budget, 832) or dict(instance_id=instance_id, principal=intent['assigned_principal'], source_task_id=intent['task_id'],
            source_session_id=intent['session_id'], source_result_id=(_reader_reserve(budget, 128) or str(result['id'])), contribution_kind='author'), budget=budget)
    if canonical_bytes(_reader_sorted(records, key=lambda r: r['source_task_id'], budget=budget), budget=budget) != canonical_bytes(_reader_sorted(expected, key=lambda r: r['source_task_id'], budget=budget), budget=budget):
        raise ValueError(CODE)

def _predecessor(conn: sqlite3.Connection, handoff: dict, proof: dict, graph: dict, *, budget=None) -> tuple[dict, list[dict]]:
    _reader_work((_reader_reserve(budget, 112) or (handoff,proof,graph)), budget, copies=2)
    from runtime.workflows.review_receipts import recipient_receipt
    round_ = _record(conn, 'workflow_rounds', graph['round_id'], budget=budget)
    requests = _reader_list((_record(conn, 'workflow_review_requests', qid, budget=budget) for qid in graph['request_ids']), budget=budget)
    if (round_['state'] != 'closed' or _reader_any_true((q['status'] == 'pending' for q in requests), budget=budget)
            or not _reader_any_true((q['status'] == 'changes_requested' for q in requests), budget=budget)):
        raise WorkflowActivationError('workflow_document_revision_predecessor_not_closed')
    receipts, feedback = (_reader_reserve(budget, 96) or (_reader_reserve(budget, 64) or [], _reader_reserve(budget, 64) or []))
    for request in requests:
        rows = _records(conn, 'SELECT * FROM workflow_review_receipts WHERE request_id=?', (_reader_reserve(budget, 80) or (request['id'],)), budget=budget)
        if len(rows) != 1:
            raise ValueError(CODE)
        receipt = rows[0]
        scope = _stored_json(request['request_scope_bytes'], request['request_scope_digest'], code=CODE, budget=budget)
        safe = recipient_receipt(receipt, budget=budget)
        _reader_append(receipts, receipt, budget=budget)
        _reader_append(feedback, _reader_reserve(budget, 960) or dict(role=scope['role'], request_id=request['id'], receipt_id=receipt['id'],
            outcome=receipt['outcome'], findings=(_reader_reserve(budget, 320) or dict(findings_bytes_base64=safe['findings_bytes_base64'],
                findings_digest=safe['findings_digest'])), request_scope_digest=request['request_scope_digest'],
            receipt_digest=receipt['proof_digest']), budget=budget)
    return (_reader_reserve(budget, 96) or (_reader_reserve(budget, 1216) or dict(handoff_id=handoff['id'], source_intent_id=handoff['intent_id'], submission_id=handoff['submission_id'], submission_digest=handoff['candidate_digest'], round_id=graph['round_id'], revision=1, request_ids=graph['request_ids'], receipt_ids=_reader_list((p['id'] for p in receipts), budget=budget), receipt_digests=_reader_list((p['proof_digest'] for p in receipts), budget=budget)), feedback))


def _context(org: str, operation_id: str, source: dict, predecessor: dict, feedback: list[dict], *, budget=None) -> dict:
    _reader_reserve(budget, 0)
    original = _handoff_json(source['context_bytes'], source['context_digest'], budget=budget)
    return (_reader_reserve(budget, 1856) or dict(format='workflow-document-revision-context@1', org_slug=org,
        instance_id=original['binding']['instance_id'], revision_operation_id=operation_id, target_revision=2,
        source_context_id=source['id'], source_context_digest=source['context_digest'],
        contract=context_contract(original, budget=budget), binding=original['binding'], authorization=original['authorization'],
        authority_snapshot=original['authority_snapshot'], inputs=original['inputs'],
        predecessor=predecessor, revision_feedback=feedback))


def revision_brief(context: dict, candidate: bytes, *, budget=None) -> str:
    _reader_work((_reader_reserve(budget, 96) or (context,candidate)), budget, copies=2)
    author = context['contract']['author_role']
    return ('Workflow document revision. Revise the immutable predecessor using all collected findings. '
        'Only your originally addressed inputs are supplied. Stage the exact new UTF8 bytes before '
        'your actual final callback; no delegation, coding, merge or signature authority follows.\n\n'
        + _reader_decode(canonical_bytes((_reader_reserve(budget, 960) or dict(revision_operation_id=context['revision_operation_id'], revision=2,
            output=context['contract']['output'], predecessor=context['predecessor'],
            submission_bytes_base64=_reader_decode(_reader_b64encode(candidate, budget=budget), 'ascii', budget=budget),
            revision_feedback=context['revision_feedback'],
            inputs=_reader_list((item for item in context['inputs'] if author in item['pin']['recipients']), budget=budget))), budget=budget), budget=budget))


def _pins(proof: dict, context: dict, intent: dict, *, budget=None) -> dict:
    _reader_reserve(budget, 0)
    _reader_reserve(budget, 64 + 128 * (len(proof["pins"]) + 2))
    for key in proof["pins"]:
        _reader_work(key, budget)
    return dict(proof['pins'], context=(_reader_reserve(budget, 448) or dict(id=context['id'], bytes_hex=_reader_hex(context['context_bytes'], budget=budget), digest=context['context_digest'])),
        scope=(_reader_reserve(budget, 448) or dict(id=intent['task_id'], bytes_hex=_reader_hex(intent['task_scope_bytes'], budget=budget), digest=intent['task_scope_digest'])))


def validate_revision_record(conn: sqlite3.Connection, row: dict, *, expected_org_slug: str | None, budget=None) -> dict:
    """Closed admission/context/task reader; no current pointer, writer or allocation."""
    _reader_work((_reader_reserve(budget, 96) or (row,expected_org_slug)), budget, copies=2)
    try:
        _reader_reserve(budget, 128)
        command = parse_revision(_handoff_json(row['request_bytes'], row['request_digest'], budget=budget), budget=budget)
        proof = _handoff_json(row['admission_bytes'], row['admission_digest'], budget=budget)
        if (_reader_set(proof, budget=budget) != _reader_set((_reader_reserve(budget, 160) or ('format', 'operation', 'client_request', 'predecessor', 'pins', 'admission_authority')), budget=budget)
                or proof['format'] != 'workflow-document-revision-admission@1'
                or canonical_bytes(command, budget=budget) != row['request_bytes'] or canonical_bytes(command, budget=budget) != canonical_bytes(proof['client_request'], budget=budget)
                or row['org_slug'] != expected_org_slug or row['principal'] != 'human:founder'
                or row['id'] != _id('workflow-document-revision', row['org_slug'], row['principal'], row['operation_key'], budget=budget)
                or canonical_bytes(proof['operation'], budget=budget) != canonical_bytes(_hex(_reader_dict_pairs(((_reader_reserve(budget, 96) or (k,  v )) for k, v in row.items() if k not in ('admission_bytes', 'admission_digest')), budget=budget), budget=budget), budget=budget)):
            raise ValueError(CODE)
        old = _records(conn, 'SELECT * FROM workflow_author_handoffs WHERE submission_id=?', (_reader_reserve(budget, 80) or (row['predecessor_submission_id'],)), budget=budget)
        if len(old) != 1:
            raise ValueError(CODE)
        old = old[0]
        oldproof = _handoff_record(conn, old, expected_org_slug=expected_org_slug, budget=budget)
        graph = validate_handoff_graph(conn, old, oldproof, budget=budget)
        if graph is None:
            raise ValueError(CODE)
        predecessor, feedback = _predecessor(conn, old, oldproof, graph, budget=budget)
        first = _record(conn, 'workflow_draft_dispatch_intents', old['intent_id'], budget=budget)
        intent = _record(conn, 'workflow_draft_dispatch_intents', row['draft_intent_id'], budget=budget)
        source = _record(conn, 'workflow_contexts', first['context_id'], budget=budget)
        ctx = _context(row['org_slug'], row['id'], source, predecessor, feedback, budget=budget)
        context = _record(conn, 'workflow_contexts', row['context_id'], budget=budget)
        task = _record(conn, 'tasks', intent['task_id'], budget=budget)
        scope = (_reader_reserve(budget, 448) or dict(assigned_agent=first['assigned_principal'], team=_record(conn, 'tasks', first['task_id'], budget=budget)['team'],
                     brief=revision_brief(ctx, old['candidate_bytes'], budget=budget)))
        admission = (_reader_reserve(budget, 1216) or dict(org_slug=row['org_slug'], instance_id=row['instance_id'], activation_id=row['activation_id'],
            activation_revision=1, attempt_sequence=2, predecessor_intent_id=first['id'], admission_principal=row['principal'],
            operation_key=row['operation_key'], request_digest=row['request_digest']))
        expected_immutable = _reader_dict_pairs(((_reader_reserve(budget, 96) or (k,  v )) for k, v in first.items() if k not in _DRAFT_MUTABLE), budget=budget)
        _reader_update(expected_immutable, (_reader_reserve(budget, 1984) or {'id': digest(canonical_bytes(admission, budget=budget), budget=budget), 'attempt_sequence': 2, 'predecessor_intent_id': first['id'], 'admission_kind': 'revision', 'operation_key': row['operation_key'], 'request_bytes': row['request_bytes'], 'request_digest': row['request_digest'], 'task_id': task['id'], 'context_id': context['id'], 'assignment_generation': 2, 'task_scope_bytes': canonical_bytes(scope, budget=budget), 'task_scope_digest': digest(canonical_bytes(scope, budget=budget), budget=budget), 'effect_key': f"workflow-initial-draft:{row['instance_id']}:2", 'host_execution_key': 'workflow-draft-host:' + row['draft_intent_id'], 'created_at': row['created_at']}), budget=budget)
        if (canonical_bytes(_hex(expected_immutable, budget=budget), budget=budget) != canonical_bytes(_hex(_reader_dict_pairs(((_reader_reserve(budget, 96) or (k,  v )) for k, v in intent.items() if k not in _DRAFT_MUTABLE), budget=budget), budget=budget), budget=budget)
                or context != (_reader_reserve(budget, 832) or dict(id=_id('workflow-context', row['instance_id'], digest(canonical_bytes(ctx, budget=budget), budget=budget), budget=budget),
                    binding_snapshot_id=first['binding_snapshot_id'], context_bytes=canonical_bytes(ctx, budget=budget),
                    context_digest=digest(canonical_bytes(ctx, budget=budget), budget=budget), source_kind='document-revision', source_id=row['id']))
                or len(context['context_bytes']) > 1048576 or len(row['admission_bytes']) > 16777216
                or row['activation_id'] != first['activation_id'] or row['instance_id'] != first['instance_id']
                or row['binding_snapshot_id'] != first['binding_snapshot_id']
                or row['authorization_revision_id'] != oldproof['pins']['authorization']['id']
                or row['predecessor_round_id'] != graph['round_id'] or first['is_current'] != 0
                or first['state'] != 'completed'
                or task['brief'] != scope['brief'] or task['assigned_agent'] != scope['assigned_agent']
                or task['team'] != scope['team'] or task['task_type'] != 'subtask' or task['parent_task_id'] is not None
                or task['active_chain'] is not None or task['active_fanout'] is not None
                or command != (_reader_reserve(budget, 1216) or dict(format='workflow-document-revision-command@1', operation_key=row['operation_key'],
                    instance_id=row['instance_id'], activation_id=row['activation_id'], expected_revision=1,
                    predecessor_submission_id=old['submission_id'], predecessor_submission_digest=old['candidate_digest'],
                    predecessor_round_id=graph['round_id'], authority=(_reader_reserve(budget, 448) or dict(namespace='org/' + row['org_slug'],
                        generation=first['authority_generation'], snapshot_digest=first['authority_digest']))))
                or canonical_bytes(proof['predecessor'], budget=budget) != canonical_bytes(predecessor, budget=budget)
                or canonical_bytes(proof['pins'], budget=budget) != canonical_bytes(_pins(oldproof, context, intent, budget=budget), budget=budget)
                or canonical_bytes(proof['admission_authority'], budget=budget) != canonical_bytes(dict(oldproof['admission_authority'],
                    actions=revision_actions(ctx, budget=budget)), budget=budget)):
            raise ValueError(CODE)
        from runtime.workflows.submissions import _reviewed_source_result
        _reviewed_source_result(conn, old, first, budget=budget)
        return proof
    except ((ValueError, KeyError, TypeError, UnicodeError)) as exc:
        raise ValueError(CODE) from exc


def revision_actions(context: dict, *, budget=None) -> list[str]:
    _reader_reserve(budget, 0)
    contract = context_contract(context, budget=budget)
    names = ('stage-immutable-artifact', 'submit-immutable-artifact') if contract['format'] == 'workflow-artifact-contract@4' else ('stage-immutable-document', 'submit-immutable-document')
    draft = 'draft-artifact' if contract['format'] == 'workflow-artifact-contract@4' else 'draft-document'
    return _reader_sorted((_reader_reserve(budget, 128 + 256 * (4 + len(names))) or {'return-to-author', draft, 'collect-review', 'approve-planning-input', *names}), budget=budget)


def _stage(conn: sqlite3.Connection, row: dict, intent: dict, *, budget=None) -> tuple[dict, dict, dict] | None:
    _reader_work((_reader_reserve(budget, 96) or (row,intent)), budget, copies=2)
    rows = _records(conn, 'SELECT * FROM workflow_submission_operations WHERE instance_id=? AND source_task_id=?',
                    (_reader_reserve(budget, 96) or (row['instance_id'], intent['task_id'])), budget=budget)
    if not rows:
        if _reader_one(conn, 'SELECT 1 FROM workflow_submissions WHERE instance_id=? AND revision=2', (_reader_reserve(budget, 80) or (row['instance_id'],)), budget=budget):
            raise ValueError(CODE)
        return None
    if len(rows) != 1:
        raise ValueError(CODE)
    operation = rows[0]
    submission = _record(conn, 'workflow_submissions', operation['submission_id'], budget=budget)
    provenance = _handoff_json(operation['provenance_bytes'], operation['provenance_digest'], budget=budget)
    request = _handoff_json(_reader_fromhex(provenance['request_bytes'], budget=budget), operation['request_digest'], budget=budget)
    original = dict(request, format='workflow-author-revision-command@1')
    for key in ('submission_id', 'instance_id', 'revision'):
        original.pop(key)
    command = parse_revision(original, author=True, budget=budget)
    sid = _id('workflow-submission', row['instance_id'], 2, row['org_slug'], intent['assigned_principal'],
              command['operation_key'], 'workflow-author-revision@1', budget=budget)
    expected = dict(command, format='workflow-author-revision-request@1', submission_id=sid, instance_id=row['instance_id'], revision=2)
    if (canonical_bytes(request, budget=budget) != canonical_bytes(expected, budget=budget) or command['revision_operation_id'] != row['id']
            or submission != (_reader_reserve(budget, 1344) or dict(id=sid, instance_id=row['instance_id'], revision=2, submission_bytes=content(command, budget=budget),
                submission_digest=command['submission_digest'], storage_ref=None, source_task_id=intent['task_id'],
                source_session_id=intent['session_id'], source_result_id=None, author_principal=intent['assigned_principal']))
            or operation['operation_key'] != command['operation_key'] or operation['org_slug'] != row['org_slug']
            or operation['principal'] != intent['assigned_principal']):
        raise ValueError(CODE)
    stamp = datetime.strptime(operation['created_at'], '%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=timezone.utc)
    if operation['created_at'] != stamp.strftime('%Y-%m-%dT%H:%M:%S.%fZ'):
        raise ValueError(CODE)
    launches = _records(conn, "SELECT * FROM workflow_draft_dispatch_events WHERE intent_id=? AND event_kind='launch_reserved'", (_reader_reserve(budget, 80) or (intent['id'],)), budget=budget)
    callbacks = _records(conn, "SELECT * FROM workflow_draft_dispatch_events WHERE intent_id=? AND event_kind IN ('callback_recorded','cancel_requested')", (_reader_reserve(budget, 80) or (intent['id'],)), budget=budget)
    if (len(launches) != 1 or stamp < datetime.fromisoformat(launches[0]['created_at'])
            or _reader_any_true((stamp > datetime.fromisoformat(e['created_at']) for e in callbacks), budget=budget)):
        raise ValueError(CODE)
    return (_reader_reserve(budget, 112) or (submission, operation, command))


def review_row(conn: sqlite3.Connection, operation: dict, *, budget=None) -> dict | None:
    """Internal revision subject adapter; never persists or invents an H row."""
    _reader_reserve(budget, 0)
    intent = _record(conn, 'workflow_draft_dispatch_intents', operation['draft_intent_id'], budget=budget)
    stage = _stage(conn, operation, intent, budget=budget)
    if stage is None:
        return None
    submission, staged, _ = stage
    return (_reader_reserve(budget, 1984) or dict(id=operation['id'], intent_id=intent['id'], instance_id=operation['instance_id'], org_slug=operation['org_slug'],
        principal=intent['assigned_principal'], source_task_id=intent['task_id'], source_session_id=intent['session_id'],
        operation_key=staged['operation_key'], submission_id=submission['id'], revision=2,
        candidate_bytes=submission['submission_bytes'], candidate_digest=submission['submission_digest'],
        request_bytes=_reader_fromhex(_handoff_json(staged['provenance_bytes'], staged['provenance_digest'], budget=budget)['request_bytes'], budget=budget),
        request_digest=staged['request_digest'], created_at=operation['created_at']))


def selected_subject(conn: sqlite3.Connection, scope: dict, *, budget=None) -> tuple[dict, dict]:
    _reader_reserve(budget, 0)
    if review_family(scope, budget=budget) == 3:
        operation = _record(conn, 'workflow_document_revision_operations', scope['revision_operation_id'], budget=budget)
        proof = validate_revision_record(conn, operation, expected_org_slug=scope['org_slug'], budget=budget)
        row = review_row(conn, operation, budget=budget)
        if row is None:
            raise ValueError(CODE)
        return (_reader_reserve(budget, 96) or (row, proof))
    row = _record(conn, 'workflow_author_handoffs', scope['handoff_id'], budget=budget)
    return (_reader_reserve(budget, 96) or (row, _handoff_record(conn, row, expected_org_slug=scope['org_slug'], budget=budget)))


def revision_review_scope(*, row: dict, proof: dict, context: dict, contract: dict, role: str,
                          request_id: str, round_id: str, binding: dict, generation: int, budget=None) -> dict:
    _reader_work((_reader_reserve(budget, 208) or (row,proof,context,contract,role,request_id,round_id,binding,generation)), budget, copies=2)
    pins = proof['pins']
    return (_reader_reserve(budget, 3392) or dict(format='workflow-review-request@3', org_slug=row['org_slug'], instance_id=row['instance_id'],
        revision_operation_id=row['id'], submission_id=row['submission_id'], submission_digest=row['candidate_digest'],
        revision=2, round_id=round_id, request_id=request_id, role=role, kind=contract['role_kinds'][role],
        principal=binding['principal'], team=binding['team'], assignment_generation=generation,
        template=pins['template'], activation=pins['activation'], authorization=pins['authorization']['id'],
        binding_snapshot_id=pins['binding']['id'], context_id=pins['context']['id'], output=contract['output'],
        outcomes=contract['outcomes'], purpose='Independently review the pinned immutable artifact; requests remain unsigned.',
        allowed_actions=(_reader_reserve(budget, 192) or ['collect-review']), inputs=_reader_list((i for i in context['inputs'] if role in i['pin']['recipients']), budget=budget),
        repository_resolution=None, artifact=(_reader_reserve(budget, 832) or dict(identity=(_reader_reserve(budget, 448) or dict(org_namespace=row['org_slug'], instance_id=row['instance_id'], output_slot='output')),
            submission_id=row['submission_id'], revision=2, kind='document', bytes_digest=row['candidate_digest'],
            subject=(_reader_reserve(budget, 192) or dict(content_digest=row['candidate_digest']))))))


def prior_review(conn: sqlite3.Connection, scope: dict, *, budget=None) -> dict:
    _reader_work((_reader_reserve(budget, 80) or (scope,)), budget, copies=2)
    operation = _record(conn, 'workflow_document_revision_operations', scope['revision_operation_id'], budget=budget)
    proof = validate_revision_record(conn, operation, expected_org_slug=scope['org_slug'], budget=budget)
    matches = (_reader_reserve(budget, 64) or [])
    for qid in proof['predecessor']['request_ids']:
        request = _record(conn, 'workflow_review_requests', qid, budget=budget)
        oldscope = _stored_json(request['request_scope_bytes'], request['request_scope_digest'], code=CODE, budget=budget)
        if oldscope['principal'] == scope['principal'] and oldscope['role'] == scope['role']:
            _reader_append(matches, (_reader_reserve(budget, 96) or (request, oldscope)), budget=budget)
    if len(matches) != 1:
        raise ValueError(CODE)
    request, oldscope = matches[0]
    submission = _record(conn, 'workflow_submissions', oldscope['submission_id'], budget=budget)
    from runtime.workflows.review_receipts import recipient_receipt
    receipts = _records(conn, 'SELECT * FROM workflow_review_receipts WHERE request_id=?', (_reader_reserve(budget, 80) or (request['id'],)), budget=budget)
    if len(receipts) != 1:
        raise ValueError(CODE)
    return (_reader_reserve(budget, 832) or dict(request_id=request['id'], request_scope_digest=request['request_scope_digest'],
        submission_id=submission['id'], submission_digest=submission['submission_digest'],
        submission_bytes_base64=_reader_decode(_reader_b64encode(submission['submission_bytes'], budget=budget), 'ascii', budget=budget),
        receipt=recipient_receipt(receipts[0], budget=budget)))


def validate_revision_storage(conn: sqlite3.Connection, *, expected_org_slug: str | None, budget=None) -> None:
    _reader_reserve(budget, 0)
    rows = _records(conn, 'SELECT * FROM workflow_document_revision_operations', budget=budget)
    owned = _reader_set((r['draft_intent_id'] for r in rows), budget=budget)
    if owned != _reader_set((r[0] for r in _reader_rows(conn, "SELECT id FROM workflow_draft_dispatch_intents WHERE admission_kind='revision'", budget=budget)), budget=budget):
        raise ValueError(CODE)
    for row in rows:
        proof = validate_revision_record(conn, row, expected_org_slug=expected_org_slug, budget=budget)
        intent = _record(conn, 'workflow_draft_dispatch_intents', row['draft_intent_id'], budget=budget)
        stage = _stage(conn, row, intent, budget=budget)
        rounds = _records(conn, 'SELECT * FROM workflow_rounds WHERE instance_id=? AND current_revision=2', (_reader_reserve(budget, 80) or (row['instance_id'],)), budget=budget)
        if stage is None:
            if _record(conn, 'workflow_instances', row['instance_id'], budget=budget)['status'] != 'reviewing':
                raise ValueError(CODE)
            if rounds:
                raise ValueError(CODE)
            continue
        subject = review_row(conn, row, budget=budget)
        links = _records(conn, 'SELECT * FROM workflow_submission_result_links WHERE submission_id=?', (_reader_reserve(budget, 80) or (subject['submission_id'],)), budget=budget)
        if not links:
            if _record(conn, 'workflow_instances', row['instance_id'], budget=budget)['status'] != 'reviewing':
                raise ValueError(CODE)
            if rounds or _reader_one(conn, 'SELECT 1 FROM workflow_events WHERE instance_id=? AND revision=2', (_reader_reserve(budget, 80) or (row['instance_id'],)), budget=budget):
                raise ValueError(CODE)
        elif validate_handoff_graph(conn, subject, proof, budget=budget) is None:
            raise ValueError(CODE)
    for submission in _records(conn, 'SELECT s.* FROM workflow_submissions s JOIN workflow_submission_operations o ON o.submission_id=s.id WHERE s.revision!=1', budget=budget):
        if submission['revision'] != 2 or not _reader_any_true((r['instance_id'] == submission['instance_id'] for r in rows), budget=budget):
            raise ValueError(CODE)


class WorkflowRevisionStore:
    def __init__(self, org: Any) -> None:
        self.org, self.db = org, org.db
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
        command = parse_revision(command)
        store = self.org.workflow_submissions
        with self.db._lock:
            self._owner(principal, self.db._conn, command['instance_id'])
            first = _records(self.db._conn, 'SELECT * FROM workflow_draft_dispatch_intents WHERE instance_id=? AND attempt_sequence=1', (command['instance_id'],))
            if len(first) != 1:
                raise WorkflowActivationError('workflow_document_revision_not_found')
            store._read_grant(self.db._conn, first[0], {'return-to-author'}, principal)
        capture = self.org.workflow_authority.capture_admission()
        notifications = []
        async with store._lock:
            async with self.org.db_lock:
                with self.db._lock:
                    self._owner(principal, self.db._conn, command['instance_id'])
                    store._read_grant(self.db._conn, first[0], {'return-to-author'}, principal)
                with self.org.workflow_authority.admission_writer(capture) as conn:
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
            for task_id in notifications:
                queue.enqueue_if_absent(self.org.slug, task_id)
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
        command = parse_revision(command, author=True)
        store = self.org.workflow_submissions
        with self.db._lock:
            self._select(self.db._conn, command['revision_operation_id'], principal=None, session_id=session_id)
        capture = self.org.workflow_authority.capture_admission()
        async with store._lock:
            async with self.org.db_lock:
                task, agent = store._session(session_id)
                with self.org.sessions.binding_lease(task, agent):
                    with self.org.workflow_authority.admission_writer(capture) as conn:
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
        capture = self.org.workflow_authority.capture_admission()
        store = self.org.workflow_submissions
        async with store._lock:
            async with self.org.db_lock:
                with self.org.workflow_authority.admission_writer(capture) as conn:
                    row = _record(conn, 'workflow_document_revision_operations', operation_id)
                    proof = validate_revision_record(conn, row, expected_org_slug=self.org.slug)
                    intent = _record(conn, 'workflow_draft_dispatch_intents', row['draft_intent_id'])
                    from runtime.workflows.review_join import HOLDS
                    try:
                        context = self._authority(conn, intent, capture)
                    except WorkflowActivationError as exc:
                        if exc.code in HOLDS:
                            return []
                        raise
                    if not intent['is_current'] or intent['cancellation_requested']:
                        return []
                    stage = _stage(conn, row, intent)
                    result = _source_result(conn, intent)
                    if stage is None or result is None or result['decision_json'] is not None or result['waiting_on_job_ids'] is not None:
                        return []
                    subject = review_row(conn, row)
                    if conn.execute('SELECT 1 FROM workflow_submission_result_links WHERE submission_id=?', (subject['submission_id'],)).fetchone():
                        validate_handoff_graph(conn, subject, proof)
                        return []
                    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                    raw = canonical_bytes(result)
                    _insert(conn, 'workflow_submission_result_links', dict(submission_id=subject['submission_id'], task_result_id=result['id'],
                        source_task_id=intent['task_id'], source_session_id=intent['session_id'], result_bytes=raw,
                        result_digest=digest(raw), accepted_at=now))
                    contribution = dict(principal=intent['assigned_principal'], source_task_id=intent['task_id'],
                        source_session_id=intent['session_id'], source_result_id=str(result['id']), contribution_kind='author')
                    _insert(conn, 'workflow_submission_contributors', dict(submission_id=subject['submission_id'], **contribution))
                    _insert(conn, 'workflow_instance_contributors', dict(instance_id=row['instance_id'], **contribution))
                    tasks = self._review_graph(conn, subject, proof, context, intent, now)
                    validate_workflow_schema(conn, expected_org_slug=self.org.slug)
                    return tasks

    def _review_graph(self, conn: sqlite3.Connection, subject: dict, proof: dict, context: dict, intent: dict, now: str) -> list[str]:
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

    async def discover(self, queue: Any) -> None:
        with self.db._lock:
            if not exists(self.db._conn):
                return
            ids = [r[0] for r in self.db._conn.execute('SELECT r.id FROM workflow_document_revision_operations r JOIN workflow_draft_dispatch_intents d ON d.id=r.draft_intent_id WHERE d.state=\'completed\'')]
        from runtime.workflows.authority import WorkflowAuthorityError
        for operation_id in ids:
            try:
                tasks = await self.fulfill(operation_id)
            except WorkflowAuthorityError as exc:
                if exc.code != 'workflow_activation_authority_stale':
                    raise
                continue
            for task_id in tasks:
                queue.enqueue_if_absent(self.org.slug, task_id)

    def completed(self, task_id: str) -> None:
        with self.db._lock:
            if not exists(self.db._conn):
                return
            ids = [r[0] for r in self.db._conn.execute('SELECT r.id FROM workflow_document_revision_operations r JOIN workflow_draft_dispatch_intents d ON d.id=r.draft_intent_id WHERE d.task_id=?', (task_id,))]
        for operation_id in ids:
            tasks = self.org.workflow_drafts._on_loop(self.fulfill(operation_id))
            queue = getattr(self.org.orchestrator, '_queue', None)
            if queue is not None:
                for task in tasks:
                    queue.enqueue_if_absent(self.org.slug, task)
