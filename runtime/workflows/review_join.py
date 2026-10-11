"""Template-driven collection and service join; history grants no current authority."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from runtime.infrastructure.workflow_schema import _handoff_record, _records, _stored_json, require_author_handoff_schema
from runtime.workflows.activation import WorkflowActivationError, _id
from runtime.workflows.draft_dispatch import canonical_bytes, digest
from runtime.workflows.submissions import _hex, _insert, _record


SERVICE = 'service:workflow_review_join'
ACTIONS = ['collect-review', 'approve-planning-input']
HOLDS = frozenset({'workflow_author_handoff_action_not_authorized',
    'workflow_author_handoff_read_not_authorized', 'workflow_author_handoff_source_not_current',
    'workflow_activation_authority_stale', 'role_binding_not_authorized'})


def captured_authority(capture: Any, actions: list[str]) -> dict:
    fields = {'dependency': ('kind', 'org_namespace', 'profile_name', 'consumer_identity', 'bound_generation', 'state'),
              'store': ('kind', 'profile_name', 'generation', 'profile_digest', 'state'),
              'registry': ('kind', 'profile_name', 'published_generation')}
    return dict(ready=dict(namespace=capture.ready.namespace, generation=capture.ready.generation,
        snapshot_bytes_hex=capture.ready.snapshot_bytes.hex(), snapshot_digest=capture.ready.snapshot_digest),
        pointer=dict(zip(('current_generation', 'journal_id', 'snapshot_digest', 'state', 'profile_fence'),
                         capture.pointer, strict=True)),
        profiles=[dict(name=name, digest=dg) for name, dg in capture.profile_digests],
        mirror=[dict(zip(fields[item[0]], item, strict=True)) for item in capture.profile_mirror],
        cutover='enabled', actions=actions)


def _responses(conn: sqlite3.Connection, graph: dict) -> list[dict]:
    return [_record(conn, 'workflow_review_requests', qid) for qid in graph['request_ids']]


def _event(conn: sqlite3.Connection, row: dict, proof: dict, graph: dict, created_at: str) -> dict:
    """Only called after every receipt has passed the complete graph oracle."""
    receipts, assignments = [], []
    for request in _responses(conn, graph):
        receipt = _records(conn, 'SELECT * FROM workflow_review_receipts WHERE request_id=?', (request['id'],))
        if len(receipt) != 1 or request['status'] != 'approved' or receipt[0]['outcome'] != 'approved':
            raise ValueError('workflow_review_join_storage_corrupt')
        receipts.append(_hex(receipt[0]))
        scope = _stored_json(request['request_scope_bytes'], request['request_scope_digest'],
                             code='workflow_review_join_storage_corrupt')
        if scope['kind'] == 'agent':
            selected = _records(conn, 'SELECT * FROM workflow_current_assignments WHERE instance_id=? AND role_key=?',
                                (row['instance_id'], scope['role']))
            if len(selected) != 1:
                raise ValueError('workflow_review_join_storage_corrupt')
            assignments.append(selected[0])
    return dict(format='workflow-review-join@1', event_kind='joined', org_slug=row['org_slug'],
        instance_id=row['instance_id'], round_id=graph['round_id'], submission_id=row['submission_id'],
        revision=row['revision'], submission_digest=row['candidate_digest'],
        operation=dict(principal=SERVICE, operation_key=graph['round_id'], action='approve-planning-input'),
        pins=proof['pins'], admission_authority=dict(proof['admission_authority'], actions=ACTIONS),
        receipts=receipts, assignments=assignments, created_at=created_at)


def _replay(row: dict, graph: dict, event: dict) -> dict:
    return dict(org_slug=row['org_slug'], principal=SERVICE, operation_key=graph['round_id'],
                request_digest=event['event_digest'], instance_id=row['instance_id'], effect_id=event['id'])


def validate_collection(conn: sqlite3.Connection, row: dict, proof: dict, graph: dict) -> str:
    """Validate saved closure AFTER receipts; never recurse through schema/graph admission."""
    from runtime.workflows.review_dispatch import _same_value
    code = 'workflow_review_join_storage_corrupt'
    round_ = _record(conn, 'workflow_rounds', graph['round_id'])
    instance = _record(conn, 'workflow_instances', row['instance_id'])
    requests = _responses(conn, graph)
    pending = any(q['status'] == 'pending' for q in requests)
    objection = any(q['status'] == 'changes_requested' for q in requests)
    events = _records(conn, "SELECT * FROM workflow_events WHERE instance_id=? AND revision=? AND event_kind='joined'",
                      (row['instance_id'], row['revision']))
    replays = _records(conn, 'SELECT * FROM workflow_operation_replays WHERE '
        '(org_slug=? AND principal=? AND operation_key=?) OR effect_id=?',
        (row['org_slug'], SERVICE, graph['round_id'], _id('workflow-event', row['instance_id'], row['revision'], 'joined')))
    if not events:
        if (replays or instance['status'] != 'reviewing' or round_['state'] not in ('reviewing', 'closed')
                or (round_['state'] == 'closed' and (pending or not objection))):
            raise ValueError(code)
        return 'collecting' if pending else ('needs_revision' if objection else 'ready_to_join')
    if len(events) != 1 or pending or objection or round_['state'] != 'closed' or instance['status'] != 'complete':
        raise ValueError(code)
    event = events[0]
    body = _stored_json(event['event_bytes'], event['event_digest'], code=code)
    stamp = datetime.strptime(body['created_at'], '%Y-%m-%dT%H:%M:%S.%fZ')
    if body['created_at'] != stamp.strftime('%Y-%m-%dT%H:%M:%S.%fZ'):
        raise ValueError(code)
    expected = _event(conn, row, proof, graph, body['created_at'])
    raw = canonical_bytes(expected)
    if (not _same_value(body, expected) or not _same_value(event, dict(
            id=_id('workflow-event', row['instance_id'], row['revision'], 'joined'),
            instance_id=row['instance_id'], revision=row['revision'], event_kind='joined',
            event_bytes=raw, event_digest=digest(raw), created_at=body['created_at']))
            or not _same_value(replays, [_replay(row, graph, event)])):
        raise ValueError(code)
    return 'approved'


def current_collection(store: Any, conn: sqlite3.Connection, row: dict, proof: dict, graph: dict, capture: Any) -> dict:
    """Internal approved-use/owner view; saved joins are immutable, current validity is separate."""
    from runtime.workflows.review_dispatch import _same_value
    state = validate_collection(conn, row, proof, graph)
    intent = _record(conn, 'workflow_draft_dispatch_intents', row['intent_id'])
    reason = None
    try:
        store._authority(conn, intent, capture, set(ACTIONS if state in ('approved', 'ready_to_join') else ['collect-review']))
    except WorkflowActivationError as exc:
        if exc.code not in HOLDS:
            raise
        reason = exc.code
    current = store._projection(conn, row, capture, replayed=True)['current']
    if current['state'] != 'consumed' or current['reconciliation_required']:
        reason = current['reason']
    actions = ACTIONS if state in ('approved', 'ready_to_join') else ['collect-review']
    if not _same_value(captured_authority(capture, actions), dict(proof['admission_authority'], actions=actions)):
        reason = 'authority_not_current'
    return dict(state=state, approved_use=state == 'approved' and reason is None,
                validity='held' if reason else 'current', reason=reason,
                owner_principal=_record(conn, 'workflow_instances', row['instance_id'])['owner_principal'],
                required_action='reconcile_source_or_authority' if reason else
                    ('revise_document' if state == 'needs_revision' else None))


def collect_in_writer(store: Any, conn: sqlite3.Connection, handoff_id: str, capture: Any) -> dict | None:
    """Consume all configured responses in the caller's existing guarded writer."""
    from runtime.workflows.submissions import validate_handoff_graph
    row = _record(conn, 'workflow_author_handoffs', handoff_id)
    intent = _record(conn, 'workflow_draft_dispatch_intents', row['intent_id'])
    # Selected grant admission precedes decoding private saved storage.
    try:
        store._read_grant(conn, intent, {'collect-review'})
        store._authority(conn, intent, capture, {'collect-review'})
    except WorkflowActivationError as exc:
        if exc.code in HOLDS:
            return dict(state='held', approved_use=False, validity='held', reason=exc.code,
                owner_principal=_record(conn, 'workflow_instances', row['instance_id'])['owner_principal'],
                required_action='reconcile_source_or_authority')
        raise
    require_author_handoff_schema(conn, expected_org_slug=store.org.slug)
    proof = _handoff_record(conn, row, expected_org_slug=store.org.slug)
    graph = validate_handoff_graph(conn, row, proof)
    if graph is None:
        return None
    current = current_collection(store, conn, row, proof, graph, capture)
    if current['validity'] == 'held' or current['state'] in ('collecting', 'approved'):
        return current
    if current['state'] == 'needs_revision':
        conn.execute("UPDATE workflow_rounds SET state='closed' WHERE id=? AND state='reviewing'", (graph['round_id'],))
    else:
        # current_collection checked approve-planning-input, full capture, source,
        # contributor independence and every typed receipt inside this writer.
        now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        raw = canonical_bytes(_event(conn, row, proof, graph, now))
        event = dict(id=_id('workflow-event', row['instance_id'], row['revision'], 'joined'),
            instance_id=row['instance_id'], revision=row['revision'], event_kind='joined',
            event_bytes=raw, event_digest=digest(raw), created_at=now)
        _insert(conn, 'workflow_events', event)
        _insert(conn, 'workflow_operation_replays', _replay(row, graph, event))
        conn.execute("UPDATE workflow_rounds SET state='closed' WHERE id=? AND state='reviewing'", (graph['round_id'],))
        conn.execute("UPDATE workflow_instances SET status='complete' WHERE id=? AND status='reviewing'", (row['instance_id'],))
    validate_handoff_graph(conn, row, proof)
    return current_collection(store, conn, row, proof, graph, capture)
