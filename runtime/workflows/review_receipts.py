"""Internal human and agent receipts with separate current admission and saved proof."""
from __future__ import annotations

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
    _tuple as _reader_tuple,
)

from runtime.infrastructure._workflow_reader import (
    _all as _reader_all, _one as _reader_one, _rows as _reader_rows,
    _json as _reader_json, _reserve as _reader_reserve, _work as _reader_work,
    _sha256 as _reader_sha256, _fromhex as _reader_fromhex,
)

import sqlite3
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import Field, StrictInt, ValidationError

from runtime.infrastructure.workflow_schema import _records, _stored_json
from runtime.workflows.activation import ClosedRecord, SHA256, Token, WorkflowActivationError, _id
from runtime.workflows.artifacts import document_from_submission, require_document_subject, review_family
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


def recipient_receipt(receipt: dict, *, budget=None) -> dict:
    """Project only a receipt already authenticated by its writer or complete graph."""
    _reader_work((_reader_reserve(budget, 80) or (receipt,)), budget, copies=2)
    proof = _stored_json(receipt['proof_bytes'], receipt['proof_digest'],
                         code='workflow_review_receipt_storage_corrupt', budget=budget)
    if proof['format'] in ('workflow-human-review-proof@1', 'workflow-human-review-proof@2', 'workflow-human-review-proof@3'):
        command = proof['command']
    elif proof['format'] in ('workflow-agent-review-proof@1', 'workflow-agent-review-proof@2', 'workflow-agent-review-proof@3'):
        command = proof['stage_event']['body']['stage']['command']
    else:
        raise ValueError('workflow_review_receipt_storage_corrupt')
    family = review_family(proof['scope'], budget=budget)
    expected = f'workflow-human-review-proof@{family}' if 'command' in proof else f'workflow-agent-review-proof@{family}'
    if proof['format'] != expected:
        raise ValueError('workflow_review_receipt_storage_corrupt')
    model = (_reader_reserve(budget, 448) or {1: ReviewReceiptProjection, 2: ArtifactReviewReceiptProjection, 3: RevisionReviewReceiptProjection})[family]
    return model.model_validate(dict(
        format=f'workflow-review-receipt@{family}', receipt_id=receipt['id'],
        **_reader_dict_pairs(((_reader_reserve(budget, 96) or (key,  receipt[key] )) for key in ('request_id', 'submission_id', 'submission_digest',
            'assignment_generation', 'request_scope_digest', 'outcome', 'created_at')), budget=budget),
        findings_bytes_base64=command['findings_bytes_base64'],
        findings_digest=command['findings_digest'])).model_dump()


def parse_human_review(command: object, *, budget=None) -> dict:
    # Reuse the existing raw UTF8/base64 bounds, without entering the agent stage lane.
    _reader_work((_reader_reserve(budget, 80) or (command,)), budget, copies=2)
    from runtime.workflows.review_dispatch import parse_stage
    try:
        _reader_reserve(budget, 96)
        family = (_reader_reserve(budget, 448) or {'workflow-human-review-command@1': 1, 'workflow-human-review-command@2': 2,
                  'workflow-human-review-command@3': 3}).get(command.get('format'), 1) if isinstance(command, dict) else 1
        model = (_reader_reserve(budget, 448) or {1: HumanReviewCommand, 2: ArtifactHumanReviewCommand, 3: RevisionHumanReviewCommand})[family]
        parsed = model.model_validate(command).model_dump()
        parse_stage(dict(parsed, format=f'workflow-review-stage-command@{family}', action='stage-review'), budget=budget)
        if len(canonical_bytes(parsed, budget=budget)) > 2097152:
            raise ValueError('command bounds')
        return parsed
    except ((ValidationError, ValueError)) as exc:
        raise WorkflowActivationError('workflow_human_review_invalid_request') from exc


def _human_proof(request: dict, scope: dict, handoff: dict, command: dict, created_at: str, *, budget=None) -> dict:
    _reader_reserve(budget, 0)
    _reader_reserve(budget, 64 + 128 * (len(handoff["admission_authority"]) + 1))
    for key in handoff["admission_authority"]:
        _reader_work(key, budget)
    return (_reader_reserve(budget, 1344) or dict(format=f'workflow-human-review-proof@{review_family(scope, budget=budget)}',
        actor=(_reader_reserve(budget, 448) or dict(principal_kind='human', principal_id='founder', proof_kind='founder_bearer')),
        operation_principal='human:founder', command=command, request_digest=digest(canonical_bytes(command, budget=budget), budget=budget),
        scope=scope, request_scope_digest=request['request_scope_digest'], pins=handoff['pins'],
        admission_authority=dict(handoff['admission_authority'], actions=(_reader_reserve(budget, 192) or ['collect-review'])), created_at=created_at))


def _human_receipt_row(request: dict, scope: dict, proof: dict, *, budget=None) -> dict:
    _reader_reserve(budget, 0)
    raw = canonical_bytes(proof, budget=budget)
    if len(raw) > 16777216:
        raise ValueError('workflow_review_receipt_storage_corrupt')
    return (_reader_reserve(budget, 1472) or dict(id=_id('workflow-review-receipt', request['id'], request['assignment_generation'], budget=budget),
        request_id=request['id'], submission_id=scope['submission_id'], submission_digest=scope['submission_digest'],
        assignment_generation=request['assignment_generation'], request_scope_digest=request['request_scope_digest'],
        proof_bytes=raw, proof_digest=digest(raw, budget=budget), outcome=proof['command']['outcome'],
        supersedes_receipt_id=None, created_at=proof['created_at']))


def _human_receipts_for_key(conn: sqlite3.Connection, *, org_slug: str, operation_key: str, budget=None) -> list[dict]:
    _reader_work((_reader_reserve(budget, 96) or (org_slug,operation_key)), budget, copies=2)
    rows = _records(conn, 'SELECT p.* FROM workflow_review_receipts p '
        'JOIN workflow_review_requests q ON q.id=p.request_id '
        'JOIN workflow_rounds r ON r.id=q.round_id '
        'JOIN workflow_submission_operations o ON o.submission_id=r.submission_id '
        "WHERE o.org_slug=? AND q.principal='founder'", (_reader_reserve(budget, 80) or (org_slug,)), budget=budget)
    matches = (_reader_reserve(budget, 64) or [])
    for receipt in rows:
        request = _record(conn, 'workflow_review_requests', receipt['request_id'], budget=budget)
        scope = _stored_json(request['request_scope_bytes'], request['request_scope_digest'],
                             code='workflow_review_receipt_storage_corrupt', budget=budget)
        if scope['kind'] != 'human':
            continue  # Kind is pinned data; an agent name cannot select the human lane.
        proof = _stored_json(receipt['proof_bytes'], receipt['proof_digest'],
                             code='workflow_review_receipt_storage_corrupt', budget=budget)
        if proof['format'] != f'workflow-human-review-proof@{review_family(scope, budget=budget)}' or proof['operation_principal'] != 'human:founder':
            raise ValueError('workflow_review_receipt_storage_corrupt')
        if proof['command']['operation_key'] == operation_key:
            _reader_append(matches, receipt, budget=budget)
    return matches


def _validate_submission_subject(conn: sqlite3.Connection, submission: dict, scope: dict, *, budget=None) -> None:
    """Supply stored namespace/provenance to the pure adapter, with caller-owned errors."""
    _reader_work((_reader_reserve(budget, 96) or (submission,scope)), budget, copies=2)
    code = 'workflow_review_receipt_storage_corrupt'
    try:
        _reader_reserve(budget, 112)
        operations = _records(conn, 'SELECT * FROM workflow_submission_operations WHERE submission_id=?',
                              (_reader_reserve(budget, 80) or (submission['id'],)), budget=budget)
        links = _records(conn, 'SELECT * FROM workflow_submission_result_links WHERE submission_id=?',
                         (_reader_reserve(budget, 80) or (submission['id'],)), budget=budget)
        if len(operations) != 1 or len(links) != 1:
            raise ValueError(code)
        contributors = _reader_tuple(_records(conn, 'SELECT * FROM workflow_instance_contributors WHERE instance_id=?',
                                      (_reader_reserve(budget, 80) or (submission['instance_id'],)), budget=budget), budget=budget)
        artifact = document_from_submission(submission, org_namespace=operations[0]['org_slug'],
            pins=_reader_dict_pairs(((_reader_reserve(budget, 96) or (key,  scope[key] )) for key in ('template', 'activation', 'authorization',
                                              'binding_snapshot_id', 'context_id')), budget=budget),
            source_result_id=links[0]['task_result_id'], contributors=contributors, budget=budget)
        require_document_subject(artifact, scope, budget=budget)
    except ((KeyError, TypeError, ValueError)) as exc:
        raise ValueError(code) from exc


def validate_human_receipt(conn: sqlite3.Connection, request: dict, scope: dict, handoff: dict, *, budget=None) -> None:
    """Authenticate closed historical data only; it supplies no current bearer authority."""
    _reader_work((_reader_reserve(budget, 112) or (request,scope,handoff)), budget, copies=2)
    from runtime.workflows.review_dispatch import _same_value
    code = 'workflow_review_receipt_storage_corrupt'
    if scope['kind'] != 'human' or scope['principal'] != 'founder':
        raise ValueError(code)
    receipts = _records(conn, 'SELECT * FROM workflow_review_receipts WHERE request_id=?', (_reader_reserve(budget, 80) or (request['id'],)), budget=budget)
    # A human has neither a task/result/assignment nor an agent evidence bridge.
    if (_reader_one(conn, 'SELECT 1 FROM workflow_current_assignments WHERE instance_id=? AND role_key=?', (_reader_reserve(budget, 96) or (scope['instance_id'], scope['role'])), budget=budget)
            or _reader_one(conn, 'SELECT 1 FROM workflow_instance_tasks WHERE instance_id=? AND role_key=?', (_reader_reserve(budget, 96) or (scope['instance_id'], scope['role'])), budget=budget)
            or _reader_one(conn, "SELECT 1 FROM workflow_task_results WHERE instance_id=? AND principal='founder'", (_reader_reserve(budget, 80) or (scope['instance_id'],)), budget=budget)
            or _reader_one(conn, 'SELECT 1 FROM workflow_receipt_evidence e JOIN workflow_review_receipts p '
                            'ON p.id=e.receipt_id WHERE p.request_id=?', (_reader_reserve(budget, 80) or (request['id'],)), budget=budget)):
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
    proof = _stored_json(receipt['proof_bytes'], receipt['proof_digest'], code=code, budget=budget)
    command = parse_human_review(proof['command'], budget=budget)
    created_at = proof['created_at']
    stamp = datetime.strptime(created_at, '%Y-%m-%dT%H:%M:%S.%fZ')
    submission = _record(conn, 'workflow_submissions', scope['submission_id'], budget=budget)
    contributors = _reader_set((r[0] for r in _reader_rows(conn, 'SELECT principal FROM workflow_instance_contributors WHERE instance_id=?',
                                              (_reader_reserve(budget, 80) or (scope['instance_id'],)), budget=budget)), budget=budget)
    _reader_update(contributors, _reader_set((r[0] for r in _reader_rows(conn, 'SELECT principal FROM workflow_submission_operations WHERE instance_id=?',
                                               (_reader_reserve(budget, 80) or (scope['instance_id'],)), budget=budget)), budget=budget), budget=budget)
    if (command['format'] != f'workflow-human-review-command@{review_family(scope, budget=budget)}'
            or created_at != stamp.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            or command['request_id'] != request['id'] or command['expected_revision'] != scope['revision']
            or command['request_scope_digest'] != request['request_scope_digest']
            or command['outcome'] not in scope['outcomes'] or request['status'] != command['outcome']
            or request['principal'] in contributors):
        raise ValueError(code)
    _validate_submission_subject(conn, submission, scope, budget=budget)
    expected = _human_proof(request, scope, handoff, command, created_at, budget=budget)
    if (not _same_value(proof, expected, budget=budget)
            or not _same_value(receipt, _human_receipt_row(request, scope, expected, budget=budget), budget=budget)
            or not _same_value(_human_receipts_for_key(conn, org_slug=scope['org_slug'],
                                operation_key=command['operation_key'], budget=budget), (_reader_reserve(budget, 192) or [receipt]), budget=budget)):
        raise ValueError(code)


def _completed_proof(conn: sqlite3.Connection, request: dict, scope: dict, *, budget=None) -> tuple[dict, dict, dict] | None:
    """Authenticate full F5 history; stage, callback and terminal are separate witnesses."""
    _reader_work((_reader_reserve(budget, 96) or (request,scope)), budget, copies=2)
    from runtime.workflows.review_dispatch import _same_value, validate_review_dispatch_storage
    validate_review_dispatch_storage(conn, request_id=request['id'], budget=budget)
    code = 'workflow_review_receipt_storage_corrupt'
    bridge = _record_bridge(conn, request['id'], budget=budget)
    events = _records(conn, 'SELECT * FROM workflow_dispatch_events WHERE operation_id=? ORDER BY event_seq',
                      (_reader_reserve(budget, 80) or (bridge['operation_id'],)), budget=budget)
    stages = _reader_list((e for e in events if e['event_kind'] == 'review_staged'), budget=budget)
    if len(stages) != 1 or not events or events[-1]['event_kind'] != 'completed' or bridge['state'] != 'completed':
        return None
    terminal = events[-1]
    body = _stored_json(terminal['event_bytes'], terminal['event_digest'], code=code, budget=budget)
    stage = _stored_json(stages[0]['event_bytes'], stages[0]['event_digest'], code=code, budget=budget)
    accepted = body['result']
    if (type(accepted) is not dict or type(accepted['id']) is not int or accepted['id'] <= 0
            or not _same_value(body['terminal_evidence'], (_reader_reserve(budget, 192) or {'host_quiescent': True}), budget=budget)):
        raise ValueError(code)
    result = _record(conn, 'task_results', accepted['id'], budget=budget)
    task = _record(conn, 'tasks', bridge['task_id'], budget=budget)
    if (not _same_value(result, accepted['record'], budget=budget) or bridge['result_id'] != (_reader_reserve(budget, 128) or str(result['id']))
            or result['status'] != 'completed' or result['task_id'] != bridge['task_id']
            or result['agent'] != bridge['assigned_principal'] or result['session_id'] != bridge['session_id']
            or task['status'] != 'completed' or task['cancelled_at'] is not None
            or task['assigned_agent'] != result['agent'] or task['current_session_id'] != result['session_id']
            or stage['stage']['session_id'] != result['session_id']):
        raise ValueError(code)
    # A successful transport with manager effects or unresolved work is unsigned.
    if result['decision_json'] is not None or result['waiting_on_job_ids'] is not None:
        return None
    submission = _record(conn, 'workflow_submissions', scope['submission_id'], budget=budget)
    _validate_submission_subject(conn, submission, scope, budget=budget)
    contributors = _reader_set((r[0] for r in _reader_rows(conn, 'SELECT principal FROM workflow_instance_contributors WHERE instance_id=?',
                                              (_reader_reserve(budget, 80) or (scope['instance_id'],)), budget=budget)), budget=budget)
    _reader_update(contributors, _reader_set((r[0] for r in _reader_rows(conn, 'SELECT principal FROM workflow_submission_operations WHERE instance_id=?',
                                               (_reader_reserve(budget, 80) or (scope['instance_id'],)), budget=budget)), budget=budget), budget=budget)
    if request['principal'] in contributors:
        raise ValueError(code)
    proof = (_reader_reserve(budget, 1472) or dict(format=f'workflow-agent-review-proof@{review_family(scope, budget=budget)}', scope=scope,
        request_scope_digest=request['request_scope_digest'],
        stage_event=(_reader_reserve(budget, 448) or dict(id=stages[0]['id'], digest=stages[0]['event_digest'], body=stage)),
        terminal_event=(_reader_reserve(budget, 320) or dict(id=terminal['id'], digest=terminal['event_digest'])),
        callback=accepted, result_id=result['id'], task_id=result['task_id'], session_id=result['session_id'],
        principal=result['agent'], outcome=stage['stage']['command']['outcome']))
    return (_reader_reserve(budget, 112) or (proof, result, terminal))


def _record_bridge(conn: sqlite3.Connection, request_id: str, *, budget=None) -> dict:
    _reader_work((_reader_reserve(budget, 80) or (request_id,)), budget, copies=2)
    rows = _records(conn, 'SELECT * FROM workflow_request_task_bridges WHERE request_id=?', (_reader_reserve(budget, 80) or (request_id,)), budget=budget)
    if len(rows) != 1:
        raise ValueError('workflow_review_receipt_storage_corrupt')
    return rows[0]


def _receipt_rows(request: dict, scope: dict, proof: dict, result: dict, terminal: dict, *, budget=None) -> dict[str, dict]:
    _reader_reserve(budget, 0)
    _reader_reserve(budget, 128)  # Original bounded native INTEGER -> TEXT identity.
    raw = canonical_bytes(proof, budget=budget)
    result_raw = canonical_bytes(result, budget=budget)
    receipt_id = _id('workflow-review-receipt', request['id'], request['assignment_generation'], budget=budget)
    native_id = str(result['id'])  # Only the genuine authenticated INTEGER result enters the TEXT bridge.
    identity = (_reader_reserve(budget, 448) or dict(instance_id=scope['instance_id'], task_id=result['task_id'], session_id=result['session_id']))
    assignment = ((_reader_reserve(budget, 64 + 128 * (5 + len(identity))) if budget is not None else None) or dict(**identity, role_key=scope['role'], principal=result['agent'], result_id=native_id,
                      generation=request['assignment_generation'], lifecycle='completed'))
    return (_reader_reserve(budget, 704) or {
        'workflow_instance_tasks': ((_reader_reserve(budget, 64 + 128 * (3 + len(identity))) if budget is not None else None) or dict(**identity, role_key=scope['role'],
                                       generation=request['assignment_generation'], state='completed')),
        'workflow_task_results': ((_reader_reserve(budget, 64 + 128 * (6 + len(identity))) if budget is not None else None) or dict(**identity, id=native_id, principal=result['agent'],
                                     generation=request['assignment_generation'], lifecycle='completed',
                                     result_bytes=result_raw, result_digest=digest(result_raw, budget=budget))),
        'workflow_current_assignments': assignment,
        'workflow_review_receipts': (_reader_reserve(budget, 1472) or dict(id=receipt_id, request_id=request['id'], submission_id=scope['submission_id'],
            submission_digest=scope['submission_digest'], assignment_generation=request['assignment_generation'],
            request_scope_digest=request['request_scope_digest'], proof_bytes=raw, proof_digest=digest(raw, budget=budget),
            outcome=proof['outcome'], supersedes_receipt_id=None, created_at=terminal['created_at'])),
        'workflow_receipt_evidence': (_reader_reserve(budget, 1344) or dict(receipt_id=receipt_id, signer_principal=result['agent'],
            task_id=result['task_id'], session_id=result['session_id'], result_id=native_id,
            context_id=scope['context_id'], binding_snapshot_id=scope['binding_snapshot_id'],
            round_revision=scope['revision'], proof_bytes=raw, proof_digest=digest(raw, budget=budget))),
    })


def validate_agent_receipt(conn: sqlite3.Connection, request: dict, scope: dict, *, budget=None) -> None:
    """Match all rows and full immutable proof, including absence for unsigned requests."""
    _reader_work((_reader_reserve(budget, 96) or (request,scope)), budget, copies=2)
    from runtime.workflows.review_dispatch import _same_value, validate_review_dispatch_storage
    code = 'workflow_review_receipt_storage_corrupt'
    receipts = _records(conn, 'SELECT * FROM workflow_review_receipts WHERE request_id=?', (_reader_reserve(budget, 80) or (request['id'],)), budget=budget)
    bridge = _record_bridge(conn, request['id'], budget=budget)
    selectors = (_reader_reserve(budget, 576) or {
        'workflow_instance_tasks': (_reader_reserve(budget, 96) or ('instance_id=? AND task_id=?', (_reader_reserve(budget, 96) or (scope['instance_id'], bridge['task_id'])))),
        'workflow_task_results': (_reader_reserve(budget, 96) or ('instance_id=? AND task_id=?', (_reader_reserve(budget, 96) or (scope['instance_id'], bridge['task_id'])))),
        'workflow_current_assignments': (_reader_reserve(budget, 96) or ('instance_id=? AND role_key=?', (_reader_reserve(budget, 96) or (scope['instance_id'], scope['role'])))),
        'workflow_receipt_evidence': (_reader_reserve(budget, 96) or ('task_id=?', (_reader_reserve(budget, 80) or (bridge['task_id'],)))),
    })
    from runtime.workflows.submissions import history_successor
    if scope['revision'] == 1 and history_successor(conn, scope['instance_id'], fulfilled=True, budget=budget):
        selectors.pop('workflow_current_assignments')
    actual = _reader_dict_pairs(((_reader_reserve(budget, 96) or (table,  _records(conn, f'SELECT * FROM {table} WHERE {where}', args, budget=budget)
              )) for table, (where, args) in selectors.items()), budget=budget)
    if request['status'] == 'pending':
        validate_review_dispatch_storage(conn, request_id=request['id'], budget=budget)
        if receipts or _reader_any_true(actual.values(), budget=budget):
            raise ValueError(code)
        return
    completed = _completed_proof(conn, request, scope, budget=budget)
    if len(receipts) != 1 or completed is None:
        raise ValueError(code)
    expected = _receipt_rows(request, scope, *completed, budget=budget)
    if (request['status'] != expected['workflow_review_receipts']['outcome']
            or not _same_value(receipts, (_reader_reserve(budget, 192) or [expected['workflow_review_receipts']]), budget=budget)
            or _reader_any_true((not _same_value(actual[table], (_reader_reserve(budget, 192) or [expected[table]]), budget=budget) for table in selectors), budget=budget)):
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
                    from runtime.workflows.revisions import selected_subject
                    subject, _ = selected_subject(conn, scope)
                    from runtime.workflows.review_join import collect_in_writer
                    collect_in_writer(store, conn, subject['id'], capture)
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
            from runtime.workflows.revisions import exists
            if exists(self.db._conn):
                handoffs += [r[0] for r in self.db._conn.execute("SELECT v.id FROM workflow_document_revision_operations v JOIN workflow_rounds r ON r.instance_id=v.instance_id WHERE r.current_revision=2 AND r.state='reviewing'")]
        for handoff_id in handoffs:
            try:
                await self.collection_current(handoff_id)
            except (WorkflowActivationError, WorkflowAuthorityError) as exc:
                if exc.code not in HOLDS:
                    raise
