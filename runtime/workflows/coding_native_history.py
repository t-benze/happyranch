"""Independent SELECT-only development, review, QA and final-manager reconstruction.

This is a coding-specific closed transition reader, never a generic task change
bus. Unsupported mutations refuse. Original run_started and native captures stay
byte-identical; current access and executable readiness are separate decisions.
"""
from __future__ import annotations

import base64
import json
from copy import deepcopy
from typing import Any

from runtime.platform.session_backend import CleanupStatus, TerminalReason
from runtime.workflows.coding_authority_history import _decoded
from runtime.workflows.coding_bootstrap import decode_row, encode_row, identity
from runtime.workflows._coding_settings_execution import _read_authority, _read_json
from runtime.workflows.coding_saved_records import (
    _canonical, _check, _closed, _hash, _integer, _timestamp, _token,
)
from runtime.workflows.coding_source_history import _TASK_COLUMNS, _TASK_TYPES

KINDS = ('session_bound', 'launch_reserved', 'launch_observed', 'callback_recorded',
         'terminal_recorded', 'cleanup_settled', 'held', 'host_reconciled')
FACTS = {
    'session_bound': 'claim session_start_audit_id session_purpose session_id',
    'launch_reserved': 'host retry',
    'launch_observed': 'host',
    'callback_recorded': 'accepted attempt_id session_id callback_digest historical_only',
    'terminal_recorded': 'host accepted_result_id terminal_class positive_no_launch outer_cleanup_pending',
    'cleanup_settled': 'host_identity claim_token pause_entry_id disposition_bytes_base64 disposition_digest settled execution_unknown settlement_error',
    'held': 'reason owner_event_ids resume_state required_owner required_action',
    'host_reconciled': 'host source_owner_event_ids observation_bytes_base64 observation_digest recovery_owner',
}
HOST_KEYS = ('logical_request_key execution_key invocation_id host_attempt session_id claim_token claim_owner '
             'launch_started request_bytes_base64 request_digest handle_id backend terminal_receipt cleanup')
CLAIM_KEYS = 'token owner invocation_id pause_entry_id pause_generation task_fingerprint claimed_at'


def _row(value: Any) -> dict:
    row = decode_row(value)
    _check(set(row) == set(_TASK_COLUMNS))
    for name, types in zip(_TASK_COLUMNS, _TASK_TYPES, strict=True):
        item = row[name]
        actual = ('null' if item is None else 'integer' if type(item) is int else
                  'real' if type(item) is float else 'text' if type(item) is str else 'invalid')
        _check(actual in types)
    return row


def _audit(conn: Any, budget: Any, row: dict, storage: Any) -> None:
    from runtime.workflows.coding_start_history import rows, same
    _integer(row['id'], 1)
    actual = list(rows(conn, 'audit_log', budget, where='id=?', args=(row['id'],), maximum=1))
    _check(len(actual) == 1 and same(actual[0], row))
    storage.add('audit_log', row)


def _host(value: Any, claim: dict, sid: str, principal: str, org: str, attempt_id: str, budget: Any) -> dict:
    host = _closed(value, HOST_KEYS)
    _check(host['claim_token'] == claim['token'] and host['claim_owner'] == claim['owner']
           and host['invocation_id'] == claim['invocation_id'] and host['session_id'] == sid
           and host['host_attempt'] == 0 and type(host['host_attempt']) is int
           and host['launch_started'] is True)
    raw = base64.b64decode(host['request_bytes_base64'], validate=True)
    request = _closed(_read_json(raw, budget, 1048576),
                      'org invocation_kind logical_id executor_profile enqueued_at retry_attempt agent_id')
    _check(host['request_digest'] == _hash(raw) and _canonical(request) == raw
           and request['org'] == org and request['invocation_kind'] == 'task'
           and request['logical_id'] == host['logical_request_key']
           and request['retry_attempt'] == 0 and type(request['retry_attempt']) is int)
    _token(host['logical_request_key'])
    _check(host['execution_key'] == host['logical_request_key'])
    _check(request['agent_id'] == principal
           and host['logical_request_key'] == identity('host', attempt_id, claim['invocation_id'])
           and type(request['enqueued_at']) in (int, float) and request['enqueued_at'] >= 0)
    for name in ('handle_id', 'backend'):
        if host[name] is not None:
            _token(host[name])
    return host


def _queue_owner(value: Any, *, kind: str, task: dict, source: dict,
                 org: str, expected: dict | None, source_invocation: dict) -> dict:
    from runtime.infrastructure.task_pause_controls import decode_journal
    owner = _closed(value, 'owner_id queue_generation claim_identity native')
    native = owner['native']
    decode_journal(_canonical(dict(version=1, entries=[native])).decode('utf8'))
    _check(native['owner'] in (('ordinary', 'v2') if kind == 'source' else ('coding',))
           and native['id'] == owner['owner_id'] == owner['claim_identity']
           and native['generation'] == owner['queue_generation'] and native['phase'] == 'prepared'
           and native['task_id'] == task['id'] and native['agent'] == task['assigned_agent']
           and native['org'] == org and native['root_task_id'] == source['root_ancestor_id']
           and not native['context'].get('execution_unknown'))
    _integer(owner['queue_generation'])
    if kind == 'source':
        for key in ('id', 'owner', 'session_id', 'fingerprint', 'generation', 'owner_identity'):
            _check(native[key] == source_invocation[key])
        _check(native['id'] == source['result_claim']['owner_invocation_id'])
    else:
        f = native['fingerprint']
        _check(f['brief_digest'] == _hash(task['brief'].encode('utf8')) and f['parent'] == task['parent_task_id']
               and f['agent'] == task['assigned_agent'] and native['owner_identity'] == {})
        if task['current_session_id'] is None:
            _check(task['status'] == f['status'] == 'pending' and f['block_kind'] is task['block_kind'] is None
                   and f['session_id'] is None and f['count'] == task['orchestration_step_count'])
        else:
            _check(task['current_session_id'] == native['session_id']
                   and task['orchestration_step_count'] == f['count'] + 1)
    if expected is not None:
        _check(owner == expected)
    return owner


def _admission(conn: Any, org: str, budget: Any, value: Any, *, callback: bool,
               command: dict, mapping: dict, roles: dict, source: dict, tasks: dict,
               run: dict, attempt: dict, native: dict, claim: dict | None, source_ancestors: list, storage: Any) -> None:
    """Independent original-time predicates; no current roster substitution."""
    from runtime.workflows._coding_settings_execution import _current_configuration, _native_format, _settings_family
    from runtime.workflows.coding_start_records import effective_mapping
    from runtime.workflows.coding_authority_history import _capture_admission
    from runtime.infrastructure.task_pause_controls import decode_journal
    from runtime.workflows.coding_start_history import rows, same
    new = _settings_family(command)
    value = _closed(value, 'format phase authority manager_teams tasks bindings pause invocation move capacity '
                    'max_revise_rounds effective_bindings_base64 record'
                    + (' settings mapping' if new and attempt['stage'] == 'development' else '')
                    + (' attempt_id revision' if attempt['revision'] > 1 or attempt['attempt_sequence'] > 1 else ''))
    if new and attempt['stage'] == 'development':
        _check(same(value['settings'], command['settings']) and same(value['mapping'], command['mapping']))
    if attempt['revision'] > 1 or attempt['attempt_sequence'] > 1:
        _check(value['attempt_id'] == attempt['id'] and type(value['revision']) is int and value['revision'] == attempt['revision'])
    _check(value['format'] == ('workflow-coding-successor-native-admission@1' if attempt['attempt_sequence'] > 1 else 'workflow-coding-revision-native-admission@1' if attempt['revision'] > 1 else _native_format(command, attempt['stage']))
           and value['phase'] == ('callback' if callback else 'launch'))
    original_native = value['invocation']
    _check(same(original_native, native))
    native = original_native
    authority = value['authority']
    grant, snapshot = _read_authority(authority, org, budget)
    _check(grant == _canonical(command) and authority['admission_record_id'] == identity(
        'native-admission', attempt['id'], native['id'], run['cas_version']))
    pin = authority['pin']
    expected_record = dict(id=authority['admission_record_id'], namespace=pin['namespace'],
        generation=pin['generation'], request_digest=_hash(grant), admitted_by='service:coding-dispatcher:' + org)
    _check(same(value['record'], expected_record))
    _capture_admission(conn, expected_record, authority, budget)
    storage.add('workflow_admission_records', expected_record)
    _current_configuration(mapping, snapshot, command, budget)
    _check(roles == effective_mapping(mapping, command)['roles']
           and value['manager_teams'] == [roles['agent_manager']['team']]
           and roles['agent_manager']['principal'] == source['principal'])
    from runtime.workflows.coding_bootstrap import effective_bindings
    _check(base64.b64decode(value['effective_bindings_base64'], validate=True) ==
           effective_bindings(run['instance_id'], roles))
    _check(type(value['tasks']) is list and 1 <= len(value['tasks']) <= 512)
    closure = {}
    for encoded in value['tasks']:
        row = _row(encoded)
        _check(row['id'] not in closure)
        closure[row['id']] = row
    for key, row in tasks.items():
        _check(key in closure and same(closure[key], row) and row['cancelled_at'] is None
               and row['zombie_flagged_at'] is None)
    leaf = tasks[attempt['task_id']]
    role = {'development': 'implementer', 'code_review': 'code_reviewer',
            'qa': 'qa_engineer', 'manager_closeout': 'agent_manager'}[attempt['stage']]
    _check((leaf['assigned_agent'], leaf['team']) ==
           (roles[role]['principal'], roles[role]['team']))
    chain, seen = [], set()
    current = leaf
    while True:
        _check(current['id'] not in seen and len(chain) < 23)
        seen.add(current['id'])
        chain.append(current)
        _check(current['cancelled_at'] is None and current['zombie_flagged_at'] is None
               and current['team'] == (roles[role]['team'] if new and current['id'] == leaf['id']
                                       else roles['agent_manager']['team'])
               and current['status'] not in ('completed', 'failed', 'cancelled', 'superseded'))
        if current['parent_task_id'] is None:
            break
        _check(current['parent_task_id'] in closure)
        current = closure[current['parent_task_id']]
    _check(chain[-1]['id'] == source['root_ancestor_id'] and chain[2]['id'] == source['task_id']
           and same(chain[3:], source_ancestors))
    for task in closure.values():
        for key in ('parent_task_id', 'revisit_of_task_id'):
            _check(task[key] is None or task[key] in closure)
    pause = _closed(decode_row(value['pause']), 'root_task_id schema_version held generation paused_at resumed_at updated_at actor release_pending_generation admission_journal_json')
    _check(type(pause['schema_version']) is int)
    _check(pause['root_task_id'] == source['root_ancestor_id'] and pause['schema_version'] == 1)
    _integer(pause['generation'])
    _integer(pause['held'])
    _check(pause['held'] in (0, 1) and pause['held'] == pause['generation'] % 2
           and native['generation'] <= pause['generation'])
    journal = decode_journal(pause['admission_journal_json'])
    matches = [e for e in journal['entries'] if e['id'] == native['id']]
    _check(matches == [native] and native['owner'] == 'coding' and native['owner_identity'] == {}
           and native['task_id'] == leaf['id'] and native['agent'] == attempt['principal']
           and native['org'] == org and native['root_task_id'] == source['root_ancestor_id']
           and not native['context'].get('execution_unknown') and not native['context'].get('producer_settled'))
    f = native['fingerprint']
    _check(f['brief_digest'] == _hash(leaf['brief'].encode('utf8')) and f['parent'] == leaf['parent_task_id']
           and f['agent'] == leaf['assigned_agent'] == attempt['principal'])
    limits = source['budgets']
    _check(type(value['max_revise_rounds']) is int and value['max_revise_rounds'] == limits['max_revise_rounds']
           and 1 <= attempt['revision'] <= min(3, limits['coding_revision_limit'])
           and (attempt['attempt_sequence'] == 1 or attempt['attempt_sequence'] == 2
                and attempt['stage'] == 'manager_closeout' and limits['coding_same_principal_retry_limit'] >= 1)
           and limits['coding_attempt_limit'] >= ({'manager_closeout': 4, 'qa': 3, 'code_review': 2}.get(attempt['stage'], 1))
           and leaf['session_timeout_seconds'] == limits['session_timeout_seconds']
           and tasks[source['task_id']]['orchestration_step_count'] == limits['source_step']
           and tasks[source['task_id']]['revision_count'] == limits['source_revision']
           and run['state'] == attempt['stage'] and run['cas_version'] + (1 if callback else 2) <= limits['coding_event_limit'])
    if callback:
        _check(value['capacity'] is None and claim is not None
               and native['id'] == claim['invocation_id'] == claim['pause_entry_id']
               and native['fingerprint'] == claim['task_fingerprint'] and native['generation'] == claim['pause_generation']
               and leaf['status'] == 'in_progress' and leaf['block_kind'] is None
               and leaf['current_session_id'] == native['session_id'] == attempt['session_id']
               and leaf['orchestration_step_count'] == f['count'] + 1
               and native['phase'] in ('possible_launch', 'running', 'result_processing'))
    else:
        _check(pause['held'] == 0 and native['generation'] == pause['generation']
               and native['phase'] == 'prepared' and leaf['status'] == 'pending'
               and leaf['current_session_id'] is None and leaf['block_kind'] is None
               and f['count'] == leaf['orchestration_step_count'] and f['session_id'] is None)
        capacity = _closed(value['capacity'], 'sessions tasks work_hours')
        _check(type(capacity['sessions']) is list and len(capacity['sessions']) <= 1024
               and capacity['tasks'] == [] and capacity['work_hours'] == [])
        for session in capacity['sessions']:
            _check(type(session) is list and len(session) == 3 and all(type(v) is str and v for v in session)
                   and (session[1] != attempt['principal'] or session[0] == leaf['id']))
    if value['move'] is not None:
        from runtime.infrastructure.team_move_store import MoveRequest, _decode
        move = decode_row(value['move'])
        request = _decode(MoveRequest, move['request_bytes'])
        actual = list(rows(conn, 'team_move_operations', budget,
            where='operation_id=?', args=(move['operation_id'],), maximum=1))
        _check(len(actual) == 1 and actual[0]['request_bytes'] == move['request_bytes']
               and actual[0]['request_digest'] == move['request_digest'] == _hash(move['request_bytes'])
               and request.org_slug == org and move['active_slot'] == 1)
        affected = set(request.affected_agents)
        def referenced(item):
            if type(item) is dict:
                return (any(item.get(k) in affected for k in ('agent', 'manager_agent', 'principal')
                            if type(item.get(k)) is str) or item.get('team') == request.source_team
                        or any(referenced(v) for v in item.values()))
            return type(item) is list and any(referenced(v) for v in item)
        _check(not referenced(roles))
        for task in closure.values():
            _check(task['team'] != request.source_team and task['assigned_agent'] not in affected)
            if task['assigned_agent'] is None and task['status'] not in ('completed', 'failed', 'cancelled', 'superseded'):
                team = next(t for t in snapshot['teams'] if t['name'] == task['team'])
                manager = team['manager']
                _check((manager if type(manager) is str else manager['principal']) not in affected)
            for name in ('active_chain', 'active_fanout'):
                if task[name] is not None:
                    _check(not referenced(json.loads(task[name])))
        _check(type(value['bindings']) is list and len(value['bindings']) <= 512)
        for bound in value['bindings']:
            _closed(bound, 'instance_id binding')
            binding = decode_row(bound['binding'])
            _check(_hash(binding['binding_bytes']) == binding['binding_digest'])
            payload = _read_json(binding['binding_bytes'], budget, 1048576)
            # Coding bindings carry unselected replacements as history. Only
            # the independently reconstructed effective four-role set is live.
            _check(not referenced(roles if bound['instance_id'] == run['instance_id'] else payload))


def fold_heartbeats(conn: Any, org: str, budget: Any, storage: Any, tasks: dict,
                    anchors: dict, *, through: int | None = None) -> tuple[dict, list]:
    """Full native rows; only the actual fixed heartbeat action can overlay clocks."""
    from runtime.workflows.coding_start_history import rows, same
    tasks = deepcopy(tasks)
    observations = []
    for task_id in sorted(tasks):
        anchor_id, anchor_digest, last_id = anchors[task_id]
        records = []
        args = (task_id, last_id or 0)
        where = "task_id=? AND action='coding_task_heartbeat' AND id>?"
        if through is not None:
            where += ' AND id<=?'
            args = (*args, through)
        for audit in rows(conn, 'audit_log', budget, where=where, args=args, maximum=1024):
            _check(len(records) < 1024)
            _integer(audit['id'], 1)
            from runtime.workflows.coding_source_history import _timestamp as native_timestamp
            native_timestamp(audit['timestamp'])
            _check(audit['task_id'] == task_id and audit['agent'] == 'orchestrator')
            body = _closed(_read_json(audit['payload'].encode('utf8'), budget, 1048576),
                'format instance_id task_id queue_owner session_id previous_audit_id anchor_event_id '
                'anchor_task_digest before_record after_record observed_at')
            _check(body['format'] == 'workflow-coding-task-heartbeat@2'
                   and body['instance_id'] == storage.heartbeat_instance
                   and body['task_id'] == task_id and body['previous_audit_id'] == last_id
                   and body['anchor_event_id'] == anchor_id and body['anchor_task_digest'] == anchor_digest)
            owner = _closed(body['queue_owner'], 'kind owner_id queue_generation claim_identity native')
            _check(owner['kind'] in ('source', 'attempt') and owner['kind'] != 'carrier')
            _token(owner['owner_id'])
            _token(owner['claim_identity'])
            _integer(owner['queue_generation'])
            expected_kind, expected_claim = storage.heartbeat_claims[task_id]
            _check(owner['kind'] == expected_kind
                   and (expected_claim is None or owner['claim_identity'] == expected_claim))
            captured = _queue_owner({k: v for k, v in owner.items() if k != 'kind'}, kind=expected_kind,
                task=tasks[task_id], source=storage.native_source, org=org,
                expected=storage.heartbeat_owners.get(task_id), source_invocation=storage.source_invocation)
            storage.heartbeat_owners[task_id] = captured
            before, after = _row(body['before_record']), _row(body['after_record'])
            _check(same(before, tasks[task_id]) and body['session_id'] == before['current_session_id'])
            _timestamp(body['observed_at'])
            _check(after['last_heartbeat'] == after['updated_at'] == body['observed_at'])
            _check(same(after, {**before, 'last_heartbeat': body['observed_at'], 'updated_at': body['observed_at']}))
            tasks[task_id] = after
            records.append(audit)
            storage.add('audit_log', audit)
            last_id = audit['id']
        if records:
            observations.append(dict(task_id=task_id, baseline_event_id=anchor_id,
                from_audit_id=anchors[task_id][2], to_audit_id=last_id, records=records))
        anchors[task_id] = (anchor_id, anchor_digest, last_id)
    return tasks, observations


def read_native(conn: Any, org: str, budget: Any, storage: Any, *, start: dict,
                events: list, tasks: dict, source: dict, roles: dict, authority: dict,
                command_value: dict, mapping: dict, source_invocation: dict, source_ancestors: list) -> dict:
    """Replay each admitted stage without trusting writer projections."""
    from runtime.infrastructure.task_pause_controls import decode_journal
    from runtime.workflows.coding_start_history import rows, same
    storage.heartbeat_instance = start['instance_id']
    storage.native_source, storage.source_invocation = source, source_invocation
    storage.heartbeat_owners = {}
    initial = _read_json(start['event_bytes'], budget, 16777216)
    run = deepcopy(initial['after']['run'])
    attempt = decode_row(initial['after']['attempts'][0]['record'])
    leaf = attempt['task_id']
    anchors = {key: (start['id'], _hash(_canonical(encode_row(value))), None) for key, value in tasks.items()}
    storage.heartbeat_claims = {key: ('source' if key == source['task_id'] else 'attempt' if key == leaf else 'carrier',
        source['result_claim']['owner_invocation_id'] if key == source['task_id'] else None) for key in tasks}
    state = dict(claim=None, host=None, terminal=None, source_settled=False, source_cleanup=None, cleanup=False, cleanup_recorded=False,
                 callback=None, invocation=None, history=[start])
    attempts = {attempt['id']: attempt}
    completed_states = {}
    previous = start
    expected_principal = 'service:coding-dispatcher:' + org
    _check(len(events) <= min(4096, source['budgets']['coding_event_limit']))
    # Explicit future QA/manager/revision allocations may be absent from an
    # earlier snapshot. Provisional discovery grants no validity: ordered replay
    # below must derive exactly these allocations and their complete native rows.
    later_qa_tasks, later_manager_tasks, later_revision_tasks, later_successor_tasks = set(), set(), set(), set()
    for event in events:
        budget.step()
        if event['event_kind'] != 'stage_queued':
            continue
        candidate = _closed(_read_json(event['event_bytes'], budget, 16777216),
            'format id instance_id event_seq event_kind attempt_id operation previous_digest before after evidence created_at')
        if candidate['format'] in ('workflow-coding-event@7', 'workflow-coding-event@8'):
            evidence = _closed(candidate['evidence'], 'kind command origin authority rows facts native_task_observations')
            facts = _closed(evidence['facts'], 'format source_attempt_id result_id output_digest outcome next_attempt_id next_task_id destination target_revision admission')
            task_id = facts['next_task_id']
            successor = candidate['format'] == 'workflow-coding-event@8'
            selected_tasks = later_successor_tasks if successor else later_revision_tasks
            _check(facts['format'] == ('workflow-coding-edge@6' if successor else 'workflow-coding-edge@5') and type(task_id) is str
                   and task_id.startswith('TASK-') and task_id not in selected_tasks)
            selected_tasks.add(task_id)
        elif candidate['format'] == 'workflow-coding-event@4':
            evidence = _closed(candidate['evidence'], 'kind command origin authority rows facts native_task_observations')
            facts = _closed(evidence['facts'], 'format developer_attempt_id reviewer_attempt_id result_id output_digest contributor qa_task_id admission')
            _check(facts['format'] == 'workflow-coding-edge@2')
            task_id = facts['qa_task_id']
            _check(type(task_id) is str and task_id.startswith('TASK-') and not later_qa_tasks)
            later_qa_tasks.add(task_id)
        elif candidate['format'] in ('workflow-coding-event@5', 'workflow-coding-event@6'):
            evidence = _closed(candidate['evidence'], 'kind command origin authority rows facts native_task_observations')
            facts = _closed(evidence['facts'], 'format upstream_results result_id output_digest manager_task_id admission')
            _check(facts['format'] == ('workflow-coding-edge@4' if candidate['format'] == 'workflow-coding-event@6' else 'workflow-coding-edge@3'))
            task_id = facts['manager_task_id']
            _check(type(task_id) is str and task_id.startswith('TASK-') and not later_manager_tasks)
            later_manager_tasks.add(task_id)
    for event in events:
        budget.step()
        kind = event['event_kind']
        from runtime.workflows.coding_progression_history import KINDS as EDGE_KINDS, replay
        if kind in EDGE_KINDS:
            _check(event['instance_id'] == run['instance_id'] and type(event['event_bytes']) is bytes
                   and _hash(event['event_bytes']) == event['event_digest'])
            body = _closed(_read_json(event['event_bytes'], budget, 16777216),
                'format id instance_id event_seq event_kind attempt_id operation previous_digest before after evidence created_at')
            _check(_canonical(body) == event['event_bytes']
                   and event['event_seq'] == previous['event_seq'] + 1
                   and event['previous_digest'] == previous['event_digest'])
            for name in ('id', 'instance_id', 'event_seq', 'event_kind', 'attempt_id', 'previous_digest', 'created_at'):
                _check(same(body[name], event[name]))
            _timestamp(event['created_at'])
            observations = body['evidence']['native_task_observations']
            _check(type(observations) is list)
            end = max((o['to_audit_id'] for o in observations), default=0)
            tasks, observed = fold_heartbeats(conn, org, budget, storage, tasks, anchors, through=end)
            _check(same(observed, observations))
            old_id = attempt['id']
            run, attempts, tasks = replay(conn, org, budget, storage, event=event, body=body,
                previous=previous, initial=initial, run=run, attempts=attempts, tasks=tasks,
                state=state, source=source, roles=roles, mapping=mapping, command=command_value,
                later_qa_tasks=later_qa_tasks | later_manager_tasks | later_revision_tasks | later_successor_tasks,
                later_manager_tasks=later_manager_tasks | later_revision_tasks | later_successor_tasks, settled_stages=completed_states)
            attempt = attempts[run['current_attempt_id']]
            if attempt['id'] != old_id:
                completed_states[old_id] = {k: v for k, v in state.items() if k != 'history'}
                state = dict(claim=None, host=None, terminal=None, callback=None, invocation=None,
                    source_settled=state['source_settled'], source_cleanup=state['source_cleanup'],
                    cleanup=False, cleanup_recorded=False, history=state['history'])
                leaf = attempt['task_id']
                storage.heartbeat_claims[leaf] = ('attempt', None)
            for key, value in tasks.items():
                anchors[key] = (event['id'], _hash(_canonical(encode_row(value))), anchors.get(key, (None, None, None))[2])
            previous = event
            state['history'].append(event)
            storage.add('workflow_coding_events', event)
            continue
        _check(kind in KINDS and event['instance_id'] == run['instance_id'])
        _check(type(event['event_bytes']) is bytes and _hash(event['event_bytes']) == event['event_digest'])
        body = _closed(_read_json(event['event_bytes'], budget, 16777216),
            'format id instance_id event_seq event_kind attempt_id operation previous_digest before after evidence created_at')
        repaired = False
        if attempt['stage'] == 'manager_closeout':
            scope = _read_json(attempt['scope_bytes'], budget, 1048576)
            repaired = scope['format'] in ('workflow-coding-final-scope@2', 'workflow-coding-final-scope@3', 'workflow-coding-final-scope@4')
        uncertainty = body['format'] in ('workflow-coding-native-manager-event@3', 'workflow-coding-revision-native-event@2', 'workflow-coding-successor-native-event@2')
        if uncertainty:
            _check((repaired or attempt['revision'] > 1) and kind == 'held')
        manager_version = '3' if uncertainty else '2' if repaired else '1'
        _check(_canonical(body) == event['event_bytes'] and body['format'] == (
            ('workflow-coding-successor-native-event@2' if uncertainty else 'workflow-coding-successor-native-event@1') if attempt['attempt_sequence'] > 1 else ('workflow-coding-revision-native-event@2' if uncertainty else 'workflow-coding-revision-native-event@1') if attempt['revision'] > 1 else {
            'development': 'workflow-coding-event@2', 'code_review': 'workflow-coding-native-review-event@1',
            'qa': 'workflow-coding-native-qa-event@1', 'manager_closeout': 'workflow-coding-native-manager-event@' + manager_version}[attempt['stage']]))
        for name in ('id', 'instance_id', 'event_seq', 'event_kind', 'attempt_id', 'previous_digest', 'created_at'):
            _check(same(body[name], event[name]))
        _check(event['event_seq'] == previous['event_seq'] + 1
               and event['previous_digest'] == previous['event_digest'] and event['attempt_id'] == attempt['id'])
        _timestamp(body['created_at'])
        evidence = _closed(body['evidence'], 'kind command origin authority rows facts native_task_observations')
        _check(evidence['kind'] == kind)
        facts = _closed(evidence['facts'], FACTS[kind])
        command = _closed(evidence['command'],
            'format kind source_event_ids source_event_digests subject producer_identity observation')
        _check(command['format'] == (('workflow-coding-successor-native-command@2' if uncertainty else 'workflow-coding-successor-native-command@1') if attempt['attempt_sequence'] > 1 else ('workflow-coding-revision-native-command@2' if uncertainty else 'workflow-coding-revision-native-command@1') if attempt['revision'] > 1 else {'development': 'workflow-coding-internal-command@2',
            'code_review': 'workflow-coding-native-review-command@1', 'qa': 'workflow-coding-native-qa-command@1', 'manager_closeout': 'workflow-coding-native-manager-command@' + manager_version}[attempt['stage']]) and command['kind'] == kind
               and command['source_event_ids'] == [previous['id']]
               and command['source_event_digests'] == [previous['event_digest']] and command['subject'] is None)
        observation = _closed(command['observation'], 'facts invocation queue_owner admission' + (' workspace' if repaired else ''))
        _check(same(observation['facts'], facts))
        if kind not in ('session_bound', 'callback_recorded'):
            _check(observation['admission'] is None)
        origin = _closed(evidence['origin'], 'kind principal task_id session_id result_id claim')
        _check(origin['kind'] == 'service' and origin['result_id'] is None)
        _token(command['producer_identity'])
        expected_key = 'coding-internal:' + _hash(_canonical([
            run['instance_id'], kind, command['producer_identity'], command['source_event_ids'], observation]))
        operation = dict(org_slug=org, principal=expected_principal, key=expected_key,
                         request_digest=_hash(_canonical(command)))
        _check(event['principal'] == expected_principal and body['operation'] == operation and event['org_slug'] == org
               and event['operation_key'] == expected_key and event['request_digest'] == operation['request_digest']
               and event['id'] == identity('event', org, run['instance_id'], expected_principal, expected_key))
        _check(evidence['authority'] == authority)
        _check(evidence['rows'] == [] or kind in ('session_bound', 'callback_recorded'))
        before, after = body['before'], body['after']
        for projection in (before, after):
            _closed(projection, 'run attempts instances tasks rounds requests assignments')
            _check(projection['instances'] == initial['after']['instances'])
            for name in ('rounds', 'requests', 'assignments'):
                _check(projection[name] == [])
        # Evidence closes exactly the heartbeat prefix at this capture; later
        # heartbeats are folded only after the last event in this snapshot.
        observations = evidence['native_task_observations']
        _check(type(observations) is list)
        end = max((o['to_audit_id'] for o in observations), default=0)
        tasks, actual_observations = fold_heartbeats(conn, org, budget, storage, tasks, anchors, through=end)
        _check(same(actual_observations, observations))
        _check(same(before['run'], run) and before['attempts'] == [dict(id=k, record=encode_row(attempts[k])) for k in sorted(attempts)]
               and same(before['tasks'], [dict(id=k, record=encode_row(tasks[k])) for k in sorted(tasks)]))
        expected_run = {**run, 'cas_version': run['cas_version'] + 1, 'last_event_id': event['id']}
        expected_attempt, expected_tasks = deepcopy(attempt), deepcopy(tasks)
        native = observation['invocation']
        if native is not None:
            decode_journal(_canonical(dict(version=1, entries=[native])).decode('utf8'))
            _check(native['org'] == org and native['root_task_id'] == source['root_ancestor_id'])
        source_tail = origin['task_id'] == source['task_id']
        if source_tail:
            _check(kind in ('cleanup_settled', 'held') and origin['session_id'] == source['session_id']
                   and origin['principal'] == source['principal'] and origin['claim'] is None
                   and command['producer_identity'] == source['result_claim']['owner_invocation_id'])
        else:
            _check(origin['task_id'] == leaf and origin['principal'] == attempt['principal'])
        if observation['queue_owner'] is not None:
            task_id = origin['task_id']
            queue_owner = _queue_owner(observation['queue_owner'], kind='source' if source_tail else 'attempt',
                task=tasks[task_id], source=source, org=org, expected=storage.heartbeat_owners.get(task_id),
                source_invocation=source_invocation)
            storage.heartbeat_owners[task_id] = queue_owner
        prebinding = uncertainty and facts['reason'] == 'coding_native_preparation_unsettled'
        missing_close = uncertainty and facts['reason'] == 'coding_native_close_unavailable'
        if uncertainty:
            _check(not source_tail and (prebinding or missing_close)
                   and facts['required_action'] == 'original_owner_reconciliation')
        if prebinding:
            # This is a preparation observation, never a successful session,
            # launch, claim, workspace or cleanup witness.
            _check(state['claim'] is state['host'] is state.get('workspace') is None
                   and observation.get('workspace') is None and origin['claim'] is None
                   and attempt['state'] == 'queued' and attempt['session_id'] is attempt['result_id'] is None
                   and observation['queue_owner'] is not None)
            prepared = observation['queue_owner']['native']
            _check(command['producer_identity'] == 'coding-dispatcher:' + org + ':' + prepared['id']
                   and origin['session_id'] == prepared['session_id'])
            if native is not None:
                for key in ('id', 'org', 'root_task_id', 'task_id', 'agent', 'session_id',
                            'owner', 'owner_identity', 'fingerprint', 'generation'):
                    _check(native[key] == prepared[key])
        elif repaired:
            from runtime.workflows.coding_start_records import CodingFinalWorkspace
            workspace = CodingFinalWorkspace.model_validate(observation['workspace']).model_dump()
            _decoded(workspace, budget)
            # Reconstruct the original native task/SID/invocation and full OrgPaths relationship.
            _check(workspace['org_slug'] == org and workspace['task_id'] == attempt['task_id']
                   and workspace['principal'] == attempt['principal'] and workspace['attempt_id'] == attempt['id'])
            if kind == 'session_bound':
                _check(state.get('workspace') is None and native is not None and not source_tail
                       and workspace['invocation_id'] == native['id']
                       and workspace['session_id'] == native['session_id'] == facts['session_id'])
                state['workspace'] = workspace
            else:
                _check(workspace == state.get('workspace'))
            if missing_close:
                _check(native is None and state['claim'] is not None and state['host'] is not None
                       and not state['cleanup_recorded']
                       and workspace['invocation_id'] == state['claim']['invocation_id'] == state['claim']['pause_entry_id']
                       and workspace['session_id'] == attempt['session_id'] == origin['session_id'])
                if observation['queue_owner'] is not None:
                    prepared = observation['queue_owner']['native']
                    _check(prepared['id'] == workspace['invocation_id']
                           and prepared['session_id'] == workspace['session_id']
                           and prepared['fingerprint'] == state['claim']['task_fingerprint']
                           and prepared['generation'] == state['claim']['pause_generation'])
            elif not source_tail:
                _check(native is not None and workspace['invocation_id'] == native['id']
                       and workspace['session_id'] == native['session_id'] == origin['session_id'])
        elif missing_close:
            _check(attempt['revision'] > 1 and native is None and state['claim'] is not None
                   and state['host'] is not None and not state['cleanup_recorded']
                   and state['claim']['invocation_id'] == state['claim']['pause_entry_id']
                   and origin['session_id'] == attempt['session_id'])
            if observation['queue_owner'] is not None:
                prepared = observation['queue_owner']['native']
                _check(prepared['id'] == state['claim']['invocation_id']
                       and prepared['session_id'] == attempt['session_id']
                       and prepared['fingerprint'] == state['claim']['task_fingerprint']
                       and prepared['generation'] == state['claim']['pause_generation'])
        if kind in ('session_bound', 'callback_recorded'):
            _check(native is not None and observation['queue_owner'] is not None)
            _admission(conn, org, budget, observation['admission'], callback=kind == 'callback_recorded',
                command=command_value, mapping=mapping, roles=roles, source=source, tasks=tasks,
                run=run, attempt=attempt, native=native, claim=state['claim'], source_ancestors=source_ancestors, storage=storage)
        if kind == 'session_bound':
            _check(state['claim'] is None and attempt['state'] == 'queued' and state['source_settled'])
            claim = _closed(facts['claim'], CLAIM_KEYS)
            for name in ('token', 'owner', 'invocation_id', 'pause_entry_id'):
                _token(claim[name])
            _integer(claim['pause_generation'])
            _timestamp(claim['claimed_at'])
            _check(claim['owner'] == 'coding-dispatcher:' + org + ':' + claim['invocation_id']
                   and len(claim['token']) == 32 and all(c in '0123456789abcdef' for c in claim['token']))
            _check(native is not None and claim['invocation_id'] == claim['pause_entry_id'] == native['id']
                   and claim['task_fingerprint'] == native['fingerprint']
                   and claim['pause_generation'] == native['generation'] and native['owner'] == 'coding'
                   and native['task_id'] == leaf and native['agent'] == attempt['principal']
                   and native['session_id'] == facts['session_id'] and native['phase'] == 'prepared'
                   and not native['context'].get('execution_unknown')
                   and not native['context'].get('producer_settled')
                   and native['fingerprint']['count'] == tasks[leaf]['orchestration_step_count']
                   and native['fingerprint']['session_id'] == tasks[leaf]['current_session_id']
                   and native['fingerprint']['brief_digest'] == _hash(tasks[leaf]['brief'].encode('utf8')))
            decode_journal(_canonical(dict(version=1, entries=[native])).decode('utf8'))
            _check(facts['session_purpose'] == 'worker_execution' and type(facts['session_start_audit_id']) is int)
            _check(len(evidence['rows']) == 1 and evidence['rows'][0]['table'] == 'audit_log')
            audit = decode_row(evidence['rows'][0]['record'])
            _audit(conn, budget, audit, storage)
            payload = json.loads(audit['payload'])
            _check(audit['id'] == facts['session_start_audit_id'] and audit['task_id'] == leaf
                   and audit['agent'] == attempt['principal'] and audit['action'] == 'session_start'
                   and payload['session_id'] == facts['session_id'] and payload['invocation_purpose'] == 'worker_execution')
            expected_attempt.update(state='claimed', session_id=facts['session_id'])
            expected_tasks[leaf].update(status='in_progress', current_session_id=facts['session_id'],
                orchestration_step_count=tasks[leaf]['orchestration_step_count'] + 1, updated_at=event['created_at'])
            queued = storage.heartbeat_owners[leaf]
            _check(queued['claim_identity'] == claim['invocation_id']
                   and queued['queue_generation'] == claim['pause_generation']
                   and queued['native']['fingerprint'] == claim['task_fingerprint']
                   and queued['native']['session_id'] == facts['session_id'])
            state['claim'] = claim
            storage.heartbeat_claims[leaf] = ('attempt', claim['invocation_id'])
        elif kind == 'launch_reserved':
            _check(state['claim'] is not None and state['host'] is None and attempt['state'] == 'claimed'
                   and facts['retry'] == native['retry'])
            host = _host(facts['host'], state['claim'], attempt['session_id'], attempt['principal'], org, attempt['id'], budget)
            _check(host['handle_id'] is host['backend'] is host['terminal_receipt'] is host['cleanup'] is None)
            state['host'] = host
        elif kind == 'launch_observed':
            _check(state['host'] is not None and state['host']['handle_id'] is None)
            host = _host(facts['host'], state['claim'], attempt['session_id'], attempt['principal'], org, attempt['id'], budget)
            _check(host['handle_id'] is not None and host['backend'] is not None)
            _check(same({**host, 'handle_id': None, 'backend': None}, state['host']))
            expected_attempt['state'] = 'running'
            state['host'] = host
        elif kind == 'host_reconciled':
            _check(state['host'] is not None and facts['source_owner_event_ids'] == [previous['id']]
                   and facts['recovery_owner'] == command['producer_identity'])
            raw = base64.b64decode(facts['observation_bytes_base64'], validate=True)
            _check(_hash(raw) == facts['observation_digest'])
            observed = _closed(_read_json(raw, budget, 1048576), 'task_id agent session_id pid')
            _integer(observed['pid'], 1)
            _check(observed == dict(task_id=leaf, agent=attempt['principal'], session_id=attempt['session_id'], pid=observed['pid'])
                   and facts['host'] == state['host'] and state['host']['handle_id'] is not None)
            expected_tasks[leaf]['executor_pid'] = observed['pid']
        elif kind == 'callback_recorded':
            _check(state['claim'] is not None and state['host'] is not None and attempt['result_id'] is None
                   and facts['attempt_id'] == attempt['id'] and facts['session_id'] == attempt['session_id']
                   and facts['historical_only'] is False)
            accepted = _closed(facts['accepted'], 'id record')
            _integer(accepted['id'], 1)
            result = decode_row(accepted['record'])
            actual = list(rows(conn, 'task_results', budget, where='id=?', args=(accepted['id'],), maximum=1))
            _check(len(actual) == 1 and same(actual[0], result) and result['id'] == accepted['id']
                   and (result['task_id'], result['agent'], result['session_id']) == (leaf, attempt['principal'], attempt['session_id'])
                   and result['decision_json'] is None and result['waiting_on_job_ids'] is None
                   and result['status'] in ('completed', 'failed', 'blocked')
                   and facts['callback_digest'] == _hash(_canonical(encode_row(result))))
            _check(evidence['rows'] == [dict(table='task_results', id=result['id'], record=encode_row(result))])
            storage.add('task_results', result)
            expected_attempt['result_id'] = result['id']
            state['callback'] = result
        elif kind == 'terminal_recorded':
            _check(state['host'] is not None and state['terminal'] is None
                   and facts['positive_no_launch'] is None and facts['outer_cleanup_pending'] is True
                   and facts['accepted_result_id'] == attempt['result_id'])
            host = _host(facts['host'], state['claim'], attempt['session_id'], attempt['principal'], org, attempt['id'], budget)
            _check(same({**host, 'terminal_receipt': None, 'cleanup': None}, state['host']))
            receipt = host['terminal_receipt']
            # Only launch_observed can supply this identity; terminal replay
            # must not turn a handleless reservation into successful execution.
            # Positive receipt facts must come from the supported containment
            # backends and native enums; missing or unknown is never clean.
            safe = (host['handle_id'] is not None and host['backend'] is not None
                    and type(receipt) is dict and receipt.get('quiescent') is True
                    and type(receipt.get('backend')) is str
                    and receipt['backend'] in ('linux-systemd-cgroup-v2', 'macos-process-group')
                    and receipt['backend'] == host['backend']
                    and type(receipt.get('terminal_reason')) is str
                    and receipt['terminal_reason'] in tuple(reason.value for reason in TerminalReason)
                    and type(receipt.get('cleanup_status')) is str
                    and receipt['cleanup_status'] in (CleanupStatus.CLEAN.value, CleanupStatus.TERM.value, CleanupStatus.KILL.value)
                    and receipt.get('survivors') == []
                    and host['cleanup'] == dict(error=None))
            result = state['callback']
            classification = ('success' if safe and receipt['terminal_reason'] == 'success' and result is not None and result['status'] == 'completed'
                              else 'failed' if safe and result is not None and result['status'] == 'failed'
                              else 'blocked' if safe and result is not None and result['status'] == 'blocked' else 'unknown')
            _check(facts['terminal_class'] == classification)
            if classification != 'success':
                expected_attempt['state'] = {'unknown': 'uncertain', 'failed': 'failed', 'blocked': 'blocked'}[classification]
                expected_run['state'] = 'held'
            if classification in ('success', 'failed'):
                expected_tasks[leaf].update(status='completed' if classification == 'success' else 'failed',
                    completed_at=event['created_at'], updated_at=event['created_at'], block_kind=None,
                    final_output_summary=result['output_summary'], final_output_dir=result['output_dir'])
            state['host'], state['terminal'] = host, classification
        elif kind == 'cleanup_settled':
            raw = base64.b64decode(facts['disposition_bytes_base64'], validate=True)
            _check(_hash(raw) == facts['disposition_digest'])
            disposition = _closed(_read_json(raw, budget, 1048576), 'kind reason processes invocation terminal tracker_clear reconciliation_complete task_tail_complete root_worktree_batch_complete settle_live_complete close release')
            _check(type(facts['settled']) is bool and type(facts['execution_unknown']) is bool)
            _check(disposition['invocation'] == native and native is not None)
            original = source_invocation if source_tail else state['claim']
            _check(original is not None)
            if source_tail:
                for key in ('id', 'org', 'root_task_id', 'task_id', 'agent', 'session_id', 'owner', 'owner_identity', 'fingerprint', 'generation'):
                    _check(native[key] == original[key])
            else:
                _check(native['id'] == original['invocation_id'] == original['pause_entry_id']
                       and native['fingerprint'] == original['task_fingerprint']
                       and native['generation'] == original['pause_generation'] and native['owner'] == 'coding'
                       and native['owner_identity'] == {} and native['task_id'] == leaf
                       and native['agent'] == attempt['principal'] and native['session_id'] == attempt['session_id'])
            close, release = disposition['close'], disposition['release']
            if close is not None:
                _closed(close, 'removed invocation')
                _check(type(close['removed']) is bool and close['invocation'] == native)
            if release is not None:
                _closed(release, 'owner_id task_id agent session_id owner_live cleanup_failed possible_launch quiescent removed')
                _check((release['owner_id'], release['task_id'], release['agent'], release['session_id']) ==
                       (native['id'], native['task_id'], native['agent'], native['session_id'])
                       and all(type(release[k]) is bool for k in ('owner_live', 'cleanup_failed', 'possible_launch', 'quiescent', 'removed')))
            _check(facts['settled'] is (disposition['kind'] == 'safe'))
            safe = (disposition['kind'] == 'safe' and disposition['processes'] == []
                    and all(disposition[name] is True for name in ('tracker_clear', 'reconciliation_complete',
                        'task_tail_complete', 'root_worktree_batch_complete', 'settle_live_complete'))
                    and not facts['execution_unknown'] and facts['settlement_error'] is None
                    and native['phase'] == 'result_processing' and not native['context'].get('execution_unknown')
                    and close is not None and close['removed'] is True
                    and release is not None and release['removed'] is True
                    and not release['owner_live'] and not release['cleanup_failed']
                    and (not release['possible_launch'] or release['quiescent']))
            _check(facts['settled'] is safe and facts['execution_unknown'] is (not safe))
            if not safe:
                expected_run['state'] = 'held'
                if not source_tail:
                    expected_attempt['state'] = 'uncertain'
            if source_tail:
                _check(state['source_cleanup'] is None and facts['pause_entry_id'] == command['producer_identity']
                       and facts['claim_token'] is None and facts['host_identity'] is None)
                _check(native is not None and native['id'] == source['result_claim']['owner_invocation_id']
                       and native['task_id'] == source['task_id'] and native['agent'] == source['principal']
                       and native['session_id'] == source['session_id'] and native['owner'] in ('ordinary', 'v2'))
                terminal = disposition['terminal']
                if safe:
                    # Outer closure alone cannot supply missing host cleanup.
                    _closed(terminal, 'request terminal_reason attempt receipt cleanup_error')
                    receipt = terminal['receipt']
                    _check(type(terminal['attempt']) is int and terminal['attempt'] >= 0
                           and terminal['request']['logical_id'] == source['task_id']
                           and terminal['request']['org'] == org and terminal['request']['agent_id'] == source['principal']
                           and type(receipt) is dict and receipt.get('quiescent') is True
                           and type(receipt.get('backend')) is str
                           and receipt['backend'] in ('linux-systemd-cgroup-v2', 'macos-process-group')
                           and type(receipt.get('terminal_reason')) is str
                           and receipt['terminal_reason'] in tuple(reason.value for reason in TerminalReason)
                           and receipt['terminal_reason'] == terminal['terminal_reason']
                           and type(receipt.get('cleanup_status')) is str
                           and receipt['cleanup_status'] in (CleanupStatus.CLEAN.value, CleanupStatus.TERM.value, CleanupStatus.KILL.value)
                           and receipt.get('survivors') == [] and terminal['cleanup_error'] is None
                           and not native['context'].get('execution_unknown'))
                state['source_cleanup'] = facts
                state['source_settled'] = safe
            else:
                _check(not state['cleanup_recorded'] and state['claim'] is not None and facts['claim_token'] == state['claim']['token']
                       and facts['pause_entry_id'] == state['claim']['pause_entry_id']
                       and facts['host_identity'] == state['host']['logical_request_key'])
                if safe and state['terminal'] == 'success':
                    expected_attempt['state'] = 'completed'
                    from runtime.workflows.coding_revision import fulfilled_revision
                    if fulfilled_revision(attempt, state['callback'], command_value['work'], budget):
                        expected_run['current_revision'] = attempt['revision']

                else:
                    expected_run['state'] = 'held'
                state['cleanup'] = safe
                state['cleanup_recorded'] = True
        elif kind == 'held':
            _check(facts['owner_event_ids'] == [previous['id']] and facts['resume_state'] == attempt['stage']
                   and type(facts['reason']) is str and facts['required_owner'] == command['producer_identity']
                   and type(facts['required_action']) is str)
            expected_run['state'] = 'held'
        if not source_tail and state['claim'] is not None:
            _check(origin['claim'] == state['claim'] and origin['session_id'] == expected_attempt['session_id']
                   and command['producer_identity'] == state['claim']['owner'])
        _check(same(after['run'], expected_run)
               and after['attempts'] == [dict(id=k, record=encode_row(expected_attempt if k == attempt['id'] else attempts[k])) for k in sorted(attempts)]
               and same(after['tasks'], [dict(id=k, record=encode_row(expected_tasks[k])) for k in sorted(expected_tasks)]))
        run, attempt, tasks = expected_run, expected_attempt, expected_tasks
        attempts[attempt['id']] = attempt
        for key, value in tasks.items():
            anchors[key] = (event['id'], _hash(_canonical(encode_row(value))), anchors[key][2])
        previous = event
        state['history'].append(event)
        storage.add('workflow_coding_events', event)
    _check(state['claim'] is None or state['host'] is not None)
    tasks, pending = fold_heartbeats(conn, org, budget, storage, tasks, anchors)
    # A committed edge is complete: no half-written acceptance/contribution or
    # allocation prefix may be admitted on cold load.
    for historical in [*completed_states.values(), state]:
        _check(historical.get('edge_index', 0) in (0, 4) and historical.get('qa_edge_index', 0) in (0, 3)
               and historical.get('final_edge_index', 0) in (0, 3)
               and historical.get('revision_edge_index', 0) in (0, historical.get('revision_edge_length', -1))
               and historical.get('manager_edge_index', 0) in (0, 2))
    _check(later_successor_tasks == {a['task_id'] for a in attempts.values() if a['attempt_sequence'] > 1})
    _check(later_revision_tasks == {a['task_id'] for a in attempts.values() if a['revision'] > 1 and a['attempt_sequence'] == 1})
    _check(later_qa_tasks == {a['task_id'] for a in attempts.values() if a['stage'] == 'qa' and a['revision'] == 1})
    _check(later_manager_tasks == {a['task_id'] for a in attempts.values() if a['stage'] == 'manager_closeout' and a['revision'] == 1 and a['attempt_sequence'] == 1})
    states = {**completed_states, attempt['id']: {k: v for k, v in state.items() if k != 'history'}}
    for key, value in states.items():
        value.update(attempt=attempts[key], queue_owner=storage.heartbeat_owners.get(attempts[key]['task_id']),
                     source_invocation=source_invocation, history=state['history'])
    state.update(run=run, attempt=attempt, attempts=attempts, stages=states, tasks=tasks,
                 anchors=anchors, pending_observations=pending,
                 queue_owner=storage.heartbeat_owners.get(leaf), source_invocation=source_invocation)
    return state


def validate_native_journals(conn: Any, org: str, budget: Any, storage: Any) -> None:
    """Current native coding owners must close into an authenticated J leaf.

    Shape decoding alone must not admit a coding owner on I, an orphan, a
    structural/source alias or a foreign SID. Historical event snapshots remain
    separate from current mutable Pause phase/generation observations.
    """
    from runtime.infrastructure.task_pause_controls import decode_journal
    from runtime.workflows.coding_start_history import rows
    if conn.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='task_pause_controls'").fetchone() is None:
        _check(not any(entry['native']['claim'] is not None for entry in storage.runs.values()))
        return
    selected, uncertain = set(), set()
    for row in rows(conn, 'task_pause_controls', budget):
        journal = decode_journal(row['admission_journal_json'])
        for entry in storage.runs.values():
            state = entry['native']
            source_id = entry['source']['result_claim']['owner_invocation_id']
            original = [n for n in journal['entries'] if n['id'] == source_id]
            if original:
                for key in ('id', 'org', 'root_task_id', 'task_id', 'agent', 'session_id', 'owner', 'owner_identity', 'fingerprint', 'generation'):
                    _check(original[0][key] == state['source_invocation'][key])
                # A positive owned close removed this exact entry. Any retained
                # owner contradicts it; unknown must never be hidden by safe J.
                _check(not state['source_settled'])
        coding = [entry for entry in journal['entries'] if entry['owner'] == 'coding']
        if not coding:
            continue
        _check(type(row['schema_version']) is int and row['schema_version'] == 1
               and type(row['generation']) is int and row['generation'] >= 0
               and type(row['held']) is int and row['held'] == row['generation'] % 2)
        for native in coding:
            budget.step()
            owner = storage.owners.get(native['task_id'])
            _check(owner is not None and owner[0] == 'coding_attempt' and native['task_id'] not in selected)
            selected.add(native['task_id'])
            if native['phase'] == 'unknown' or native['context'].get('execution_unknown'):
                uncertain.add(native['task_id'])
            entry = storage.runs[owner[1]]
            attempt = next(a for a in entry['attempts'].values() if a['task_id'] == native['task_id'])
            state = entry['native']['stages'][attempt['id']]
            _check(native['agent'] == attempt['principal'] and native['org'] == org
                   and native['root_task_id'] == row['root_task_id'] == entry['source']['root_ancestor_id']
                   and native['generation'] <= row['generation'] and native['owner_identity'] == {})
            if state['claim'] is None:
                task = entry['tasks'][attempt['task_id']]
                _check(attempt['state'] == 'queued' and native['phase'] in ('prepared', 'unknown')
                       and native['fingerprint'] == dict(status=task['status'], block_kind=task['block_kind'],
                           count=task['orchestration_step_count'], session_id=task['current_session_id'],
                           agent=task['assigned_agent'], fanout=task['active_fanout'], jobs=task['blocked_on_job_ids'],
                           parent=task['parent_task_id'], brief_digest=_hash(task['brief'].encode('utf8'))))
                queued = state.get('queue_owner')
                if queued is not None:
                    _check(native['id'] == queued['claim_identity'] and native['generation'] == queued['queue_generation']
                           and native['session_id'] == queued['native']['session_id']
                           and native['fingerprint'] == queued['native']['fingerprint'])
            else:
                claim = state['claim']
                _check(native['id'] == claim['invocation_id'] and native['session_id'] == attempt['session_id']
                       and native['fingerprint'] == claim['task_fingerprint']
                       and native['generation'] == claim['pause_generation'])
                _check(not (state['cleanup_recorded'] and state['cleanup']))
        storage.add('task_pause_controls', row)
    for entry in storage.runs.values():
        for attempt in entry['attempts'].values():
            state = entry['native']['stages'][attempt['id']]
            # Missing native ownership never proves teardown, including an
            # earlier attempt after the current pointer has advanced.
            state['ownership_unknown'] = (attempt['task_id'] in uncertain or ((state['claim'] is not None or state['queue_owner'] is not None)
                and not (state['cleanup_recorded'] and state['cleanup'])
                and attempt['task_id'] not in selected))
        current = entry['native']['stages'][entry['attempt']['id']]
        entry['native']['ownership_unknown'] = any(s['ownership_unknown'] for s in entry['native']['stages'].values())
        entry['native']['queue_owner'] = current['queue_owner']
