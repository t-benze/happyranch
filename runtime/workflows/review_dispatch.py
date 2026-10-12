"""F5 request owner using the unchanged native host lifecycle interface."""
from __future__ import annotations

import base64
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import Field, StrictInt, ValidationError

from runtime.infrastructure.workflow_schema import _records, _stored_json
from runtime.workflows.activation import ClosedRecord, SHA256, Token, WorkflowActivationError, author_capacity_blocked
from runtime.workflows.draft_dispatch import DraftOwnershipError, WorkflowDraftDispatcher, canonical_bytes, digest
from runtime.workflows.artifacts import review_family
from runtime.workflows.submissions import _insert, _record, validate_handoff_graph


_OUTBOX_MUTABLE = {'state', 'claim_token', 'claim_owner', 'host_launch_started',
                   'host_execution_id', 'last_error', 'updated_at'}
_BRIDGE_MUTABLE = {'state', 'session_id', 'result_id'}


class ReviewStageCommand(ClosedRecord):
    format: Literal['workflow-review-stage-command@1']
    action: Literal['stage-review']
    operation_key: Token
    request_id: Token
    expected_revision: StrictInt = Field(ge=1, le=1)
    request_scope_digest: SHA256
    outcome: Literal['approved', 'changes_requested']
    findings_bytes_base64: str = Field(min_length=4, max_length=1398104)
    findings_digest: SHA256


class ArtifactReviewStageCommand(ReviewStageCommand):
    format: Literal['workflow-review-stage-command@2']


class RevisionReviewStageCommand(ReviewStageCommand):
    format: Literal['workflow-review-stage-command@3']
    expected_revision: StrictInt = Field(ge=2, le=2)


def parse_stage(command: dict) -> dict:
    try:
        model = ({'workflow-review-stage-command@1': ReviewStageCommand,
                  'workflow-review-stage-command@2': ArtifactReviewStageCommand,
                  'workflow-review-stage-command@3': RevisionReviewStageCommand}.get(command.get('format'), ReviewStageCommand)
                 if isinstance(command, dict) else ReviewStageCommand)
        parsed = model.model_validate(command).model_dump()
        raw = base64.b64decode(parsed['findings_bytes_base64'], validate=True)
        raw.decode('utf8')
        if (not 1 <= len(raw) <= 1048576 or digest(raw) != parsed['findings_digest']
                or base64.b64encode(raw).decode('ascii') != parsed['findings_bytes_base64']):
            raise ValueError('findings')
        return parsed
    except (ValidationError, ValueError, UnicodeError) as exc:
        raise WorkflowActivationError('workflow_review_stage_invalid_request') from exc


def validate_stage(conn: sqlite3.Connection, body: dict, *, outbox: dict, bridge: dict) -> dict:
    """Closed pure-data stage; the exact retained request selects the reviewer kind."""
    code = 'workflow_review_storage_corrupt'
    stage = body['stage']
    if type(stage) is not dict or set(stage) != {'command', 'request_digest', 'scope', 'outbox_id', 'session_id'}:
        raise ValueError(code)
    command = parse_stage(stage['command'])
    request = _record(conn, 'workflow_review_requests', bridge['request_id'])
    scope = _stored_json(request['request_scope_bytes'], request['request_scope_digest'], code=code)
    before = body['before']
    if (command['format'] != f'workflow-review-stage-command@{review_family(scope)}'
            or not _same_value(stage['scope'], scope) or scope['kind'] != 'agent'
            or command['outcome'] not in scope['outcomes']
            or command['request_id'] != request['id'] or command['expected_revision'] != scope['revision']
            or command['request_scope_digest'] != request['request_scope_digest']
            or stage['request_digest'] != digest(canonical_bytes(command))
            or stage['outbox_id'] != outbox['id'] or stage['session_id'] != before['bridge']['session_id']
            or scope['principal'] != bridge['assigned_principal']
            or not _same_value(scope['assignment_generation'], bridge['assignment_generation'])
            or not stage['session_id'] or before['bridge']['result_id'] is not None
            or before['outbox']['host_launch_started'] != 1
            or before['outbox']['state'] not in ('claimed', 'running')
            or not _same_value(before, body['after'])
            or body['result'] is not None or body['terminal_evidence'] is not None):
        raise ValueError(code)
    matches = _stages_for_key(conn, org_slug=body['org_slug'], principal=scope['principal'],
                              operation_key=command['operation_key'])
    if len(matches) != 1 or matches[0]['operation_id'] != bridge['operation_id']:
        raise ValueError(code)
    return stage


def _stages_for_key(conn: sqlite3.Connection, *, org_slug: str, principal: str, operation_key: str) -> list[dict]:
    rows = _records(conn, "SELECT e.* FROM workflow_dispatch_events e JOIN workflow_dispatch_operations o "
                    "ON o.id=e.operation_id WHERE o.org_slug=? AND o.principal=? AND e.event_kind='review_staged'",
                    (org_slug, principal))
    return [e for e in rows if _stored_json(e['event_bytes'], e['event_digest'],
            code='workflow_review_storage_corrupt')['stage']['command']['operation_key'] == operation_key]


def _same_value(left: Any, right: Any) -> bool:
    """Compare new F5 witnesses recursively without Python's bool/int coercion."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same_value(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_same_value(a, b) for a, b in zip(left, right))
    return left == right


def _validate_projection(projection: dict, *, outbox: dict, bridge: dict) -> None:
    """Anchor every witness, including intermediate rows, to retained native values."""
    code = 'workflow_review_storage_corrupt'
    if (type(projection) is not dict or set(projection) != {'outbox', 'bridge'}
            or type(projection['outbox']) is not dict or type(projection['bridge']) is not dict
            or set(projection['outbox']) != set(outbox) or set(projection['bridge']) != set(bridge)):
        raise ValueError(code)
    projected_outbox, projected_bridge = projection['outbox'], projection['bridge']
    if (any(not _same_value(projected_outbox[k], outbox[k]) for k in outbox if k not in _OUTBOX_MUTABLE)
            or any(not _same_value(projected_bridge[k], bridge[k]) for k in bridge if k not in _BRIDGE_MUTABLE)
            or type(projected_outbox['host_launch_started']) is not int
            or projected_outbox['host_launch_started'] not in (0, 1)
            or any(type(projected_outbox[k]) is not str for k in ('state', 'updated_at'))
            or type(projected_bridge['state']) is not str
            or any(projected_outbox[k] is not None and type(projected_outbox[k]) is not str
                   for k in ('claim_token', 'claim_owner', 'host_execution_id', 'last_error'))
            or any(projected_bridge[k] is not None and type(projected_bridge[k]) is not str
                   for k in ('session_id', 'result_id'))):
        raise ValueError(code)


def validate_review_dispatch_storage(conn: sqlite3.Connection, *, request_id: str) -> None:
    try:
        _validate_review_dispatch_storage(conn, request_id=request_id)
    except (KeyError, TypeError, AttributeError, ValueError, sqlite3.DatabaseError) as exc:
        raise ValueError('workflow_review_storage_corrupt') from exc


def _validate_transition(body: dict, *, cancelled: bool) -> None:
    """Closed lifecycle grammar; event hashes alone are not ownership proof."""
    code = 'workflow_review_storage_corrupt'
    kind = body['event_kind']
    before, after = body['before'], body['after']
    old, new = before['outbox'], after['outbox']
    old_bridge, new_bridge = before['bridge'], after['bridge']
    changes = {
        'claimed': ({'state', 'claim_token', 'claim_owner', 'updated_at'}, {'state'}),
        'launch_reserved': ({'host_launch_started', 'updated_at'}, {'session_id'}),
        'requeued': ({'state', 'claim_token', 'claim_owner', 'updated_at'}, {'state'}),
        'running': ({'state', 'host_execution_id', 'updated_at'}, {'state'}),
        'callback_recorded': (set(), {'result_id'}),
        'uncertain': ({'state', 'last_error', 'updated_at'}, {'state'}),
        'failed': ({'state', 'last_error', 'updated_at'}, {'state'}),
        'cancel_requested': (set(), set()),
        'cancelled': ({'state', 'updated_at'}, {'state'}),
        'completed': ({'state', 'updated_at'}, {'state'}),
    }
    edges = {
        'claimed': {('queued', 'claimed')}, 'launch_reserved': {('claimed', 'claimed')},
        'requeued': {('claimed', 'queued')}, 'running': {('claimed', 'running')},
        'callback_recorded': {(s, s) for s in ('claimed', 'running')},
        'uncertain': {(s, 'uncertain') for s in ('claimed', 'running')},
        'failed': {(s, 'uncertain') for s in ('claimed', 'running', 'uncertain')},
        'cancel_requested': {(s, s) for s in ('queued', 'claimed', 'running', 'uncertain')},
        'cancelled': {(s, 'cancelled') for s in ('queued', 'claimed', 'running', 'uncertain')},
        'completed': {(s, 'completed') for s in ('running', 'uncertain')},
    }
    if (kind not in changes or (old['state'], new['state']) not in edges[kind]
            or old['state'] != old_bridge['state'] or new['state'] != new_bridge['state']
            or any(not _same_value(old[k], new[k]) for k in old if k not in changes[kind][0])
            or any(not _same_value(old_bridge[k], new_bridge[k]) for k in old_bridge if k not in changes[kind][1])
            or type(new['host_launch_started']) is not int
            or new['host_launch_started'] not in (0, 1)):
        raise ValueError(code)
    if kind == 'claimed' and (not isinstance(new['claim_token'], str) or not new['claim_token']
                              or new['claim_owner'] != 'workflow_dispatcher' or old['host_launch_started']):
        raise ValueError(code)
    if kind == 'requeued' and (old['host_launch_started'] or new['claim_token'] is not None
                               or new['claim_owner'] is not None or new_bridge['session_id'] is not None):
        raise ValueError(code)
    if kind == 'launch_reserved' and (old['host_launch_started'] != 0 or new['host_launch_started'] != 1
                                      or old_bridge['session_id'] is not None
                                      or not isinstance(new_bridge['session_id'], str) or not new_bridge['session_id']):
        raise ValueError(code)
    if kind == 'running' and (not old['host_launch_started'] or old['host_execution_id'] is not None
                              or not new_bridge['session_id'] or not isinstance(new['host_execution_id'], str)
                              or len(new['host_execution_id']) != 64):
        raise ValueError(code)
    if kind == 'callback_recorded' and (cancelled or not old['host_launch_started']
                                       or not old_bridge['session_id'] or old_bridge['result_id'] is not None):
        raise ValueError(code)
    if kind == 'cancel_requested' and cancelled:
        raise ValueError(code)
    if kind == 'cancelled' and not cancelled:
        raise ValueError(code)
    if kind == 'completed' and cancelled:
        raise ValueError(code)
    if kind not in ('callback_recorded', 'completed') and body['result'] is not None:
        raise ValueError(code)
    if kind not in ('completed', 'cancelled', 'failed') and body['terminal_evidence'] is not None:
        raise ValueError(code)
    if kind in ('cancelled', 'failed') and not _same_value(body['terminal_evidence'],
            {'host_quiescent': True} if old['host_launch_started'] else None):
        raise ValueError(code)


def _validate_review_dispatch_storage(conn: sqlite3.Connection, *, request_id: str) -> None:
    """Authenticate the entire sequenced F5 owner, full callbacks and terminal evidence."""
    code = 'workflow_review_storage_corrupt'
    bridges = _records(conn, 'SELECT * FROM workflow_request_task_bridges WHERE request_id=?', (request_id,))
    operations = _records(conn, 'SELECT * FROM workflow_dispatch_operations WHERE request_id=?', (request_id,))
    outboxes = _records(conn, 'SELECT * FROM workflow_dispatch_outbox WHERE request_id=?', (request_id,))
    if len(bridges) != 1 or len(operations) != 1 or len(outboxes) != 1:
        raise ValueError(code)
    bridge, operation, outbox = bridges[0], operations[0], outboxes[0]
    retained = {'outbox': outbox, 'bridge': bridge}
    _validate_projection(retained, outbox=outbox, bridge=bridge)
    initial = dict(outbox=dict(outbox, state='queued', claim_token=None, claim_owner=None,
                              host_launch_started=0, host_execution_id=None, last_error=None,
                              updated_at=outbox['created_at']),
                   bridge=dict(bridge, state='queued', session_id=None, result_id=None))
    events = _records(conn, 'SELECT * FROM workflow_dispatch_events WHERE operation_id=? ORDER BY event_seq', (operation['id'],))
    previous = None
    accepted = None
    cancelled = False
    staged = False
    for seq, event in enumerate(events, start=1):
        body = _stored_json(event['event_bytes'], event['event_digest'], code=code)
        _validate_projection(body['before'], outbox=outbox, bridge=bridge)
        _validate_projection(body['after'], outbox=outbox, bridge=bridge)
        is_stage = body['event_kind'] == 'review_staged'
        keys = {'format', 'org_slug', 'operation_id', 'request_id', 'task_id', 'event_seq', 'event_kind',
                'previous_digest', 'before', 'after', 'terminal_evidence', 'result'}
        if (set(body) != keys | ({'stage'} if is_stage else set())
                or body['format'] != ('workflow-review-stage@1' if is_stage else 'workflow-review-dispatch-event@1')
                or body['org_slug'] != operation['org_slug']
                or body['operation_id'] != operation['id'] or body['request_id'] != request_id
                or body['task_id'] != bridge['task_id'] or body['event_kind'] != event['event_kind']
                or body['event_seq'] != seq or type(body['event_seq']) is not int or event['event_seq'] != seq
                or event['id'] != f"{operation['id']}:{seq}"
                or body['before']['outbox']['state'] != event['state_before']
                or body['after']['outbox']['state'] != event['state_after']
                or body['previous_digest'] != (None if previous is None else previous[0]['event_digest'])
                or not _same_value(body['before'], initial if previous is None else previous[1]['after'])):
            raise ValueError(code)
        if is_stage:
            if cancelled or staged:
                raise ValueError(code)
            validate_stage(conn, body, outbox=outbox, bridge=bridge)
            staged = True
        else:
            _validate_transition(body, cancelled=cancelled)
        cancelled = cancelled or body['event_kind'] == 'cancel_requested'
        if body['event_kind'] == 'callback_recorded':
            if (accepted is not None or not isinstance(body['result'], dict)
                    or set(body['result']) != {'id', 'record', 'digest', 'accepted', 'disposition'}
                    or type(body['result']['id']) is not int
                    or type(body['result']['accepted']) is not int or body['result']['accepted'] != 1):
                raise ValueError(code)
            accepted = body['result']
            result = _record(conn, 'task_results', accepted['id'])
            if (not _same_value(accepted['record'], result)
                    or accepted['digest'] != digest(canonical_bytes(result))
                    or result['task_id'] != bridge['task_id'] or result['agent'] != bridge['assigned_principal']
                    or result['session_id'] != body['after']['bridge']['session_id']
                    or body['after']['bridge']['result_id'] != str(result['id'])):
                raise ValueError(code)
            if accepted['disposition'] != ('accepted' if body['before']['outbox']['state'] == 'running'
                                            else 'pending_host_acknowledgment'):
                raise ValueError(code)
        if body['event_kind'] == 'completed' and (
                accepted is None or accepted['record']['status'] != 'completed'
                or not _same_value(body['terminal_evidence'], {'host_quiescent': True})
                or not _same_value(body['result'], accepted)
                or not body['after']['outbox']['host_execution_id']):
            raise ValueError(code)
        previous = event, body
    if previous is None:
        if not _same_value(retained, initial):
            raise ValueError(code)
    elif not _same_value(previous[1]['after'], retained):
        raise ValueError(code)
    callbacks = _records(conn, 'SELECT * FROM workflow_dispatch_callbacks WHERE outbox_id=?', (outbox['id'],))
    if len(callbacks) != int(accepted is not None):
        raise ValueError(code)
    if callbacks:
        callback = callbacks[0]
        if (callback['task_id'] != bridge['task_id'] or callback['session_id'] != bridge['session_id']
                or callback['result_id'] != bridge['result_id'] or callback['result_digest'] != accepted['digest']
                or type(callback['accepted']) is not int or callback['accepted'] != 1
                or type(callback['observed_revision']) is not int or callback['observed_revision'] != outbox['artifact_revision']
                or callback['disposition'] != accepted['disposition']
                or callback['id'] != f"{operation['id']}:callback:{accepted['id']}"):
            raise ValueError(code)
    effects = _records(conn, 'SELECT * FROM workflow_dispatch_effects WHERE outbox_id=?', (outbox['id'],))
    if len(effects) != int(outbox['host_execution_id'] is not None):
        raise ValueError(code)
    if effects and (effects[0]['host_execution_id'] != outbox['host_execution_id']
                    or effects[0]['task_id'] != bridge['task_id'] or effects[0]['session_id'] != bridge['session_id']
                    or effects[0]['id'] != f"{operation['id']}:host"
                    or effects[0]['effect_key'] != outbox['effect_key']
                    or effects[0]['effect_kind'] != 'host_launch_observed'):
        raise ValueError(code)
    expected_state = 'cancelled' if cancelled else 'completed' if outbox['state'] == 'completed' else 'admitted'
    if outbox['state'] != bridge['state'] or operation['state'] != expected_state:
        raise ValueError(code)


class WorkflowReviewDispatcher(WorkflowDraftDispatcher):
    """The F5 adapter projects real rows; it creates no draft intent or alternate host API."""

    def reviewer_instruction(self, task_id: str, agent: str, session_id: str) -> str:
        """Render the selected F5 contract before the forthcoming session is published.

        The invocation identity labels instructions only. Relational selection
        uses the retained bridge; CURRENT-session admission remains stage-owned.
        No lifecycle writer, session registration or receipt effect runs here.
        """
        store = self.org.workflow_submissions

        def selected(conn: sqlite3.Connection) -> tuple[dict, dict]:
            bridges = _records(conn, 'SELECT * FROM workflow_request_task_bridges WHERE task_id=?', (task_id,))
            if (len(bridges) != 1 or bridges[0]['assigned_principal'] != agent
                    or bridges[0]['assignment_generation'] not in (1, 2)):
                raise DraftOwnershipError('workflow_review_storage_corrupt')
            bridge = bridges[0]
            request, _, source = store._select_request(conn, request_id=bridge['request_id'],
                task_agent=(task_id, agent), session_id=bridge['session_id'], human=False)
            return request, source

        with self.db._lock:
            # Ordinary and author invocations on historical layouts stay inert.
            if not self.db._conn.execute("SELECT 1 FROM sqlite_schema WHERE type='table' "
                                         "AND name='workflow_request_task_bridges'").fetchone():
                return ''
            if not self.db._conn.execute('SELECT 1 FROM workflow_request_task_bridges WHERE task_id=?',
                                         (task_id,)).fetchone():
                return ''
            selected(self.db._conn)  # Current relational recipient/grant before private decode.
        capture = self.org.workflow_authority.capture_admission()
        with self.db._lock:
            request, source = selected(self.db._conn)
            store._authority(self.db._conn, source, capture, {'collect-review'})
            intent = self._intent(self.db._conn, task_id)
            task = _record(self.db._conn, 'tasks', task_id)
            scope = intent['scope']
            if (scope['kind'] != 'agent' or scope['principal'] != agent
                    or scope['request_id'] != request['id'] or not intent['is_current']
                    or intent['cancellation_requested'] or intent['final_result_id'] is not None
                    or (intent['state'], task['status']) not in {('queued', 'pending'), ('claimed', 'in_progress')}
                    or intent['host_launch_started'] or task['assigned_agent'] != agent
                    or task['cancelled_at'] is not None
                    or capture.ready.generation != intent['authority_generation']
                    or capture.ready.snapshot_digest != intent['authority_digest']
                    or self.db._conn.execute('SELECT state FROM workflow_cutover_state WHERE singleton=1').fetchone()[0] != 'enabled'):
                raise DraftOwnershipError('workflow_activation_authority_stale')
            family = review_family(scope)
            revision = scope['revision']
            outcomes = json.dumps(scope['outcomes'])
            request_id, scope_digest = request['id'], request['request_scope_digest']
        return (f'\nWorkflow current reviewer: independently assess only the selected pinned immutable '
                f'document and recipient-filtered inputs. Read them with happyranch workflows submissions '
                f'request-show {request_id} --org {self.org.slug} --session-id {session_id} --json. '
                f'The selected request is {request_id}, request_scope_digest {scope_digest}, revision {revision}; '
                f'declared outcomes are {outcomes}. Before your genuine final callback write one closed JSON '
                f'command: format workflow-review-stage-command@{family}, action stage-review, operation_key '
                f'(one stable original key), request_id {request_id}, expected_revision {revision}, '
                f'request_scope_digest {scope_digest}, outcome (one declared value), findings_bytes_base64 '
                '(canonical base64 of exact UTF8 findings) and findings_digest (SHA256 of those raw bytes). '
                f'Call happyranch workflows reviews stage --org {self.org.slug} --session-id {session_id} '
                '--from-file <absolute-command.json> --json before report-completion. Preserve the same '
                'key/body after transport loss; do not retry automatically. The five-field acknowledgement '
                'is unsigned; request, acknowledgement or callback alone supplies no signature. Only genuine '
                'INTEGER/full callback and committed successful terminal with Boolean host_quiescent=true '
                'under current authority can admit a receipt. Retain failure/BLOCK evidence, never invent '
                'approval, result, session or signature. No delegation, coding, execution or merge grant '
                'follows. Use your actual task/session report-completion as the FINAL session action.\n')

    async def stage_review(self, *, session_id: str, command: dict) -> dict:
        command = parse_stage(command)
        store = self.org.workflow_submissions
        task, agent = store._session(session_id)
        selection = dict(request_id=command['request_id'], task_agent=(task, agent), session_id=session_id, human=False)
        # Preserve selected identity/grant refusal before authority/schema decoding.
        with self.org.sessions.binding_lease(task, agent):
            with self.db._lock:
                store._session(session_id)
                store._select_request(self.db._conn, **selection)
        capture = self.org.workflow_authority.capture_admission()
        async with store._lock:
            async with self.org.db_lock:
                with self.org.sessions.binding_lease(task, agent):
                    with self.db._lock:
                        store._session(session_id)
                        store._select_request(self.db._conn, **selection)
                    with self.org.workflow_authority.admission_writer(capture) as conn:
                        store._session(session_id)
                        request, _, source = store._select_request(conn, **selection)
                        store._authority(conn, source, capture, {'collect-review'})
                        intent = self._intent(conn, task)
                        store._fresh(conn, intent, task, agent, session_id)
                        if (command['format'] != f"workflow-review-stage-command@{review_family(intent['scope'])}"
                                or command['expected_revision'] != intent['scope']['revision']
                                or command['request_scope_digest'] != request['request_scope_digest']
                                or command['outcome'] not in intent['scope']['outcomes']):
                            raise WorkflowActivationError('workflow_review_stage_invalid_request')
                        if (capture.ready.generation != intent['authority_generation']
                                or capture.ready.snapshot_digest != intent['authority_digest']):
                            raise WorkflowActivationError('workflow_activation_authority_stale')
                        if conn.execute('SELECT state FROM workflow_cutover_state WHERE singleton=1').fetchone()[0] != 'enabled':
                            raise WorkflowActivationError('workflow_new_runs_disabled')
                        keyed = _stages_for_key(conn, org_slug=self.org.slug, principal=agent,
                                                operation_key=command['operation_key'])
                        if keyed and (len(keyed) != 1 or keyed[0]['operation_id'] != intent['operation_id']):
                            raise WorkflowActivationError('workflow_review_stage_operation_conflict')
                        stages = _records(conn, "SELECT * FROM workflow_dispatch_events WHERE operation_id=? AND event_kind='review_staged'",
                                          (intent['operation_id'],))
                        if stages:
                            body = _stored_json(stages[0]['event_bytes'], stages[0]['event_digest'], code='workflow_review_storage_corrupt')
                            if not _same_value(body['stage']['command'], command) or body['stage']['session_id'] != session_id:
                                raise WorkflowActivationError('workflow_review_stage_operation_conflict')
                            event_id = stages[0]['id']
                        else:
                            self._event(conn, intent, 'review_staged', stage=dict(command=command,
                                request_digest=digest(canonical_bytes(command)), scope=intent['scope'],
                                outbox_id=intent['id'], session_id=session_id))
                            event_id = conn.execute('SELECT id FROM workflow_dispatch_events WHERE operation_id=? ORDER BY event_seq DESC LIMIT 1',
                                                    (intent['operation_id'],)).fetchone()[0]
                        validate_review_dispatch_storage(conn, request_id=request['id'])
                        return dict(format=f"workflow-review-stage-ack@{review_family(intent['scope'])}", stage_event_id=event_id,
                                    request_digest=digest(canonical_bytes(command)), replayed=bool(stages), signed=False)

    def _intent(self, conn: sqlite3.Connection, task_id: str) -> dict[str, Any]:
        bridges = _records(conn, 'SELECT * FROM workflow_request_task_bridges WHERE task_id=?', (task_id,))
        if len(bridges) != 1 or conn.execute('SELECT 1 FROM workflow_draft_dispatch_intents WHERE task_id=?', (task_id,)).fetchone():
            raise DraftOwnershipError('workflow_review_storage_corrupt')
        bridge = bridges[0]
        operation = _record(conn, 'workflow_dispatch_operations', bridge['operation_id'])
        request = _record(conn, 'workflow_review_requests', bridge['request_id'])
        outboxes = _records(conn, 'SELECT * FROM workflow_dispatch_outbox WHERE operation_id=?', (operation['id'],))
        if len(outboxes) != 1:
            raise DraftOwnershipError('workflow_review_storage_corrupt')
        outbox = outboxes[0]
        scope = json.loads(request['request_scope_bytes'])
        from runtime.workflows.revisions import selected_subject
        row, proof = selected_subject(conn, scope)
        graph = validate_handoff_graph(conn, row, proof)
        if graph is None or request['id'] not in graph['request_ids'] or task_id not in graph['reviewer_task_ids']:
            raise DraftOwnershipError('workflow_review_storage_corrupt')
        # These are source adapter fields consumed by the existing lifecycle
        # owner. Their identities are the actual F5 bridge and outbox identities.
        return dict(**outbox, task_id=task_id, assigned_principal=bridge['assigned_principal'],
            assignment_generation=bridge['assignment_generation'], session_id=bridge['session_id'],
            final_result_id=None if bridge['result_id'] is None else int(bridge['result_id']),
            cancellation_requested=int(operation['state'] == 'cancelled'),
            is_current=int(request['status'] == 'pending' and _record(conn, 'workflow_draft_dispatch_intents', row['intent_id'])['is_current']),
            instance_id=bridge['instance_id'], scope=scope, bridge=bridge, operation=operation,
            intent_id=row['intent_id'], activation_id=proof['pins']['activation']['id'],
            activation_revision=1, attempt_sequence=1)

    def _eligible(self, conn: sqlite3.Connection, intent: dict, capture: Any,
                  active_sessions: tuple[tuple[str, str, str], ...]) -> None:
        source = _record(conn, 'workflow_draft_dispatch_intents', intent['intent_id'])
        self.org.workflow_submissions._authority(conn, source, capture, {'collect-review'})
        if (not intent['is_current'] or intent['cancellation_requested']
                or capture.ready.generation != intent['authority_generation']
                or capture.ready.snapshot_digest != intent['authority_digest']
                or conn.execute('SELECT state FROM workflow_cutover_state WHERE singleton=1').fetchone()[0] != 'enabled'):
            raise DraftOwnershipError('workflow_activation_authority_stale')
        if author_capacity_blocked(conn, author=intent['assigned_principal'], task_id=intent['task_id'], active_sessions=active_sessions):
            raise DraftOwnershipError('workflow_activation_author_pending')

    def _event(self, conn: sqlite3.Connection, intent: dict, kind: str, *, changes: dict | None = None,
               terminal_evidence: dict | None = None, result: dict | None = None, stage: dict | None = None) -> None:
        changes = dict(changes or {})
        now = datetime.now(timezone.utc).isoformat()
        if changes.get('state') == 'failed':
            changes['state'] = 'uncertain'
            changes['last_error'] = 'workflow_review_source_failed'
        allowed = {'state', 'claim_token', 'claim_owner', 'host_launch_started', 'host_execution_id',
                   'last_error', 'session_id', 'final_result_id', 'cancellation_requested'}
        if set(changes) - allowed:
            raise DraftOwnershipError('workflow_review_storage_corrupt')
        outbox_changes = {k: v for k, v in changes.items() if k not in ('session_id', 'final_result_id', 'cancellation_requested')}
        if outbox_changes:
            conn.execute('UPDATE workflow_dispatch_outbox SET ' + ','.join(f'{k}=?' for k in outbox_changes) + ',updated_at=? WHERE id=?',
                         (*outbox_changes.values(), now, intent['id']))
        bridge_changes = {k: v for k, v in changes.items() if k in ('state', 'session_id')}
        if 'final_result_id' in changes:
            bridge_changes['result_id'] = str(changes['final_result_id'])
        if bridge_changes:
            conn.execute('UPDATE workflow_request_task_bridges SET ' + ','.join(f'{k}=?' for k in bridge_changes) + ' WHERE request_id=?',
                         (*bridge_changes.values(), intent['request_id']))
        if changes.get('cancellation_requested'):
            conn.execute("UPDATE workflow_dispatch_operations SET state='cancelled' WHERE id=?", (intent['operation_id'],))
        if changes.get('state') == 'completed':
            conn.execute("UPDATE workflow_dispatch_operations SET state='completed' WHERE id=?", (intent['operation_id'],))
        previous = conn.execute('SELECT event_seq,event_digest FROM workflow_dispatch_events WHERE operation_id=? ORDER BY event_seq DESC LIMIT 1',
                                (intent['operation_id'],)).fetchone()
        seq = 1 if previous is None else previous['event_seq'] + 1
        after_outbox = _record(conn, 'workflow_dispatch_outbox', intent['id'])
        after_bridge = dict(conn.execute('SELECT * FROM workflow_request_task_bridges WHERE request_id=?', (intent['request_id'],)).fetchone())
        payload = dict(format='workflow-review-stage@1' if stage is not None else 'workflow-review-dispatch-event@1', org_slug=self.org.slug,
            operation_id=intent['operation_id'], request_id=intent['request_id'], task_id=intent['task_id'],
            event_seq=seq, event_kind=kind, previous_digest=None if previous is None else previous['event_digest'],
            before=dict(outbox={k: v for k, v in intent.items() if k in after_outbox}, bridge=intent['bridge']),
            after=dict(outbox=after_outbox, bridge=after_bridge), terminal_evidence=terminal_evidence, result=result)
        if stage is not None:
            payload['stage'] = stage
        raw = canonical_bytes(payload)
        _insert(conn, 'workflow_dispatch_events', dict(id=f"{intent['operation_id']}:{seq}", operation_id=intent['operation_id'],
            event_seq=seq, event_kind=kind, state_before=intent['state'], state_after=after_outbox['state'],
            event_bytes=raw, event_digest=digest(raw), created_at=now))
        if kind == 'callback_recorded' and result is not None:
            _insert(conn, 'workflow_dispatch_callbacks', dict(id=f"{intent['operation_id']}:callback:{result['id']}",
                outbox_id=intent['id'], task_id=intent['task_id'], session_id=after_bridge['session_id'],
                result_id=str(result['id']), result_digest=result['digest'], observed_revision=intent['scope']['revision'],
                accepted=1, disposition=result['disposition'], created_at=now))
        if kind == 'running' and after_outbox['host_execution_id'] is not None:
            _insert(conn, 'workflow_dispatch_effects', dict(id=f"{intent['operation_id']}:host",
                outbox_id=intent['id'], effect_key=intent['effect_key'], effect_kind='host_launch_observed',
                task_id=intent['task_id'], session_id=after_bridge['session_id'],
                host_execution_id=after_outbox['host_execution_id'], created_at=now))

    def _accepted_result(self, conn: sqlite3.Connection, intent: dict) -> dict | None:
        rows = _records(conn, "SELECT * FROM workflow_dispatch_events WHERE operation_id=? AND event_kind='callback_recorded'",
                        (intent['operation_id'],))
        if len(rows) != 1:
            return None
        return json.loads(rows[0]['event_bytes'])['result']

    def terminal(self, task_id: str, agent: str, session_id: str, outcome: Any, *, expected_request: Any = None) -> None:
        super().terminal(task_id, agent, session_id, outcome, expected_request=expected_request)
        # The original native owner has committed and released its locks.
        # Tracker clear and supervisor capacity release still follow this hook.
        with self.db._lock:
            bridge = _records(self.db._conn, 'SELECT * FROM workflow_request_task_bridges WHERE task_id=?', (task_id,))
            completed = (len(bridge) == 1 and bridge[0]['state'] == 'completed'
                         and bridge[0]['assigned_principal'] == agent and bridge[0]['session_id'] == session_id)
        if completed:
            self._on_loop(self.org.workflow_review_receipts.admit_completed(task_id))

    def cancel(self, task_id: str) -> dict:
        from runtime.orchestrator.run_step import _capture_root_worktree_transition, _RootWorktreeTransitions, _root_worktree_batch
        root_transitions = _RootWorktreeTransitions()
        staged_transitions = _RootWorktreeTransitions()
        root_tail_failed = False
        try:
            with self._writer() as conn:
                with _capture_root_worktree_transition(self.org.orchestrator, task_id, 'review_dispatch.cancel', staged_transitions):
                    intent = self._intent(conn, task_id)
                    if intent['state'] in ('cancelled', 'completed'):
                        return self.projection(intent)
                    if not intent['cancellation_requested']:
                        self._event(conn, intent, 'cancel_requested', changes={'cancellation_requested': 1})
                        intent = self._intent(conn, task_id)
                    if not intent['host_launch_started']:
                        now = datetime.now(timezone.utc).isoformat()
                        conn.execute("UPDATE tasks SET status='cancelled',cancelled_at=?,completed_at=?,updated_at=?,block_kind=NULL WHERE id=?",
                                     (now, now, now, task_id))
                        self._event(conn, intent, 'cancelled', changes={'state': 'cancelled'})
            # Publish only the original successfully committed transition.
            root_transitions.extend(staged_transitions)
            for _agent, control in self.org.sessions.iter_task_cancel_controls(task_id):
                control()
            with self.db._lock:
                return self.projection(self._intent(self.db._conn, task_id))
        except BaseException:
            root_tail_failed = True
            raise
        finally:
            if root_transitions:
                _root_worktree_batch(self.org.orchestrator, root_transitions, teardown_incomplete=root_tail_failed)
