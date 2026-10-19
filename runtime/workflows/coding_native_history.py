"""Independent SELECT-only first-development native event reconstruction.

This is a coding-specific closed transition reader, never a generic task change
bus. Unsupported mutations refuse. Original run_started and native captures stay
byte-identical; current access and executable readiness are separate decisions.
"""
from __future__ import annotations

import base64
import json
from copy import deepcopy
from typing import Any

from runtime.workflows.coding_authority_history import _decoded
from runtime.workflows.coding_bootstrap import decode_row, encode_row, identity
from runtime.workflows.coding_saved_records import (
    _canonical, _check, _closed, _hash, _integer, _json, _timestamp, _token,
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


def _host(value: Any, claim: dict, sid: str, task: str, org: str) -> dict:
    host = _closed(value, HOST_KEYS)
    _check(host['claim_token'] == claim['token'] and host['claim_owner'] == claim['owner']
           and host['invocation_id'] == claim['invocation_id'] and host['session_id'] == sid
           and host['host_attempt'] == 0 and type(host['host_attempt']) is int
           and host['launch_started'] is True)
    raw = base64.b64decode(host['request_bytes_base64'], validate=True)
    request = _closed(_json(raw, 1048576),
                      'org invocation_kind logical_id executor_profile enqueued_at retry_attempt agent_id')
    _check(host['request_digest'] == _hash(raw) and _canonical(request) == raw
           and request['org'] == org and request['invocation_kind'] == 'task'
           and request['logical_id'] == host['logical_request_key']
           and request['retry_attempt'] == 0 and type(request['retry_attempt']) is int)
    _token(host['logical_request_key'])
    _check(host['execution_key'] == host['logical_request_key'])
    _check(request['agent_id'] == task)
    for name in ('handle_id', 'backend'):
        if host[name] is not None:
            _token(host[name])
    return host


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
            _check(audit['task_id'] == task_id and audit['agent'] == 'orchestrator')
            body = _closed(_json(audit['payload'].encode('utf8'), 1048576),
                'format instance_id task_id queue_owner session_id previous_audit_id anchor_event_id '
                'anchor_task_digest before_record after_record observed_at')
            _check(body['format'] == 'workflow-coding-task-heartbeat@1'
                   and body['instance_id'] == storage.heartbeat_instance
                   and body['task_id'] == task_id and body['previous_audit_id'] == last_id
                   and body['anchor_event_id'] == anchor_id and body['anchor_task_digest'] == anchor_digest)
            _decoded(body, budget)
            owner = _closed(body['queue_owner'], 'kind owner_id queue_generation claim_identity')
            _check(owner['kind'] in ('source', 'attempt') and owner['kind'] != 'carrier')
            _token(owner['owner_id'])
            _token(owner['claim_identity'])
            _integer(owner['queue_generation'], 1)
            expected_kind, expected_claim = storage.heartbeat_claims[task_id]
            _check(owner['kind'] == expected_kind
                   and (expected_claim is None or owner['claim_identity'] == expected_claim))
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
                events: list, tasks: dict, source: dict, roles: dict, authority: dict) -> dict:
    """Replay admitted first-development kinds without trusting writer projections."""
    from runtime.infrastructure.task_pause_controls import decode_journal
    from runtime.workflows.coding_start_history import rows, same
    storage.heartbeat_instance = start['instance_id']
    initial = _json(start['event_bytes'], 16777216)
    run = deepcopy(initial['after']['run'])
    attempt = decode_row(initial['after']['attempts'][0]['record'])
    leaf = attempt['task_id']
    anchors = {key: (start['id'], _hash(_canonical(encode_row(value))), None) for key, value in tasks.items()}
    storage.heartbeat_claims = {key: ('source' if key == source['task_id'] else 'attempt' if key == leaf else 'carrier',
        source['result_claim']['owner_invocation_id'] if key == source['task_id'] else None) for key in tasks}
    state = dict(claim=None, host=None, terminal=None, source_settled=False, source_cleanup=None, cleanup=False, cleanup_recorded=False,
                 callback=None, invocation=None, history=[start])
    previous = start
    _check(len(events) <= min(4096, source['budgets']['coding_event_limit']))
    for event in events:
        budget.step()
        kind = event['event_kind']
        _check(kind in KINDS and event['instance_id'] == run['instance_id'])
        _check(type(event['event_bytes']) is bytes and _hash(event['event_bytes']) == event['event_digest'])
        body = _closed(_json(event['event_bytes'], 16777216),
            'format id instance_id event_seq event_kind attempt_id operation previous_digest before after evidence created_at')
        _decoded(body, budget)
        _check(_canonical(body) == event['event_bytes'] and body['format'] == 'workflow-coding-event@1')
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
        _check(command['format'] == 'workflow-coding-internal-command@1' and command['kind'] == kind
               and command['source_event_ids'] == [previous['id']]
               and command['source_event_digests'] == [previous['event_digest']] and command['subject'] is None)
        observation = _closed(command['observation'], 'facts invocation queue_owner')
        _check(same(observation['facts'], facts))
        origin = _closed(evidence['origin'], 'kind principal task_id session_id result_id claim')
        _check(origin['kind'] == 'service' and origin['result_id'] is None)
        _token(command['producer_identity'])
        expected_key = 'coding-internal:' + _hash(_canonical([
            run['instance_id'], kind, command['producer_identity'], command['source_event_ids'], observation]))
        operation = dict(org_slug=org, principal=event['principal'], key=expected_key,
                         request_digest=_hash(_canonical(command)))
        _check(body['operation'] == operation and event['org_slug'] == org
               and event['operation_key'] == expected_key and event['request_digest'] == operation['request_digest']
               and event['id'] == identity('event', org, run['instance_id'], event['principal'], expected_key))
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
        _check(same(before['run'], run) and before['attempts'] == [dict(id=attempt['id'], record=encode_row(attempt))]
               and same(before['tasks'], [dict(id=k, record=encode_row(tasks[k])) for k in sorted(tasks)]))
        expected_run = {**run, 'cas_version': run['cas_version'] + 1, 'last_event_id': event['id']}
        expected_attempt, expected_tasks = deepcopy(attempt), deepcopy(tasks)
        native = observation['invocation']
        if native is not None:
            decode_journal(_canonical(dict(version=1, entries=[native])).decode('utf8'))
            _check(native['org'] == org and native['root_task_id'] == source['root_ancestor_id'])
        source_tail = origin['task_id'] == source['task_id']
        if source_tail:
            _check(kind == 'cleanup_settled' and origin['session_id'] == source['session_id']
                   and origin['principal'] == source['principal'] and origin['claim'] is None
                   and command['producer_identity'] == source['result_claim']['owner_invocation_id'])
        else:
            _check(origin['task_id'] == leaf and origin['principal'] == attempt['principal'])
        if kind == 'session_bound':
            _check(state['claim'] is None and attempt['state'] == 'queued' and state['source_settled'])
            claim = _closed(facts['claim'], CLAIM_KEYS)
            for name in ('token', 'owner', 'invocation_id', 'pause_entry_id'):
                _token(claim[name])
            _integer(claim['pause_generation'])
            _timestamp(claim['claimed_at'])
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
            state['claim'] = claim
            storage.heartbeat_claims[leaf] = ('attempt', claim['invocation_id'])
        elif kind == 'launch_reserved':
            _check(state['claim'] is not None and state['host'] is None and attempt['state'] == 'claimed'
                   and facts['retry'] == native['retry'])
            host = _host(facts['host'], state['claim'], attempt['session_id'], attempt['principal'], org)
            _check(host['handle_id'] is host['backend'] is host['terminal_receipt'] is host['cleanup'] is None)
            state['host'] = host
        elif kind == 'launch_observed':
            _check(state['host'] is not None and state['host']['handle_id'] is None)
            host = _host(facts['host'], state['claim'], attempt['session_id'], attempt['principal'], org)
            _check(host['handle_id'] is not None and host['backend'] is not None)
            _check(same({**host, 'handle_id': None, 'backend': None}, state['host']))
            expected_attempt['state'] = 'running'
            state['host'] = host
        elif kind == 'host_reconciled':
            _check(state['host'] is not None and facts['source_owner_event_ids'] == [previous['id']]
                   and facts['recovery_owner'] == command['producer_identity'])
            raw = base64.b64decode(facts['observation_bytes_base64'], validate=True)
            _check(_hash(raw) == facts['observation_digest'])
            observed = _closed(_json(raw, 1048576), 'task_id agent session_id pid')
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
            host = _host(facts['host'], state['claim'], attempt['session_id'], attempt['principal'], org)
            _check(same({**host, 'terminal_receipt': None, 'cleanup': None}, state['host']))
            receipt = host['terminal_receipt']
            safe = (type(receipt) is dict and receipt.get('quiescent') is True
                    and receipt.get('backend') == host['backend'] and host['backend'] != 'passthrough'
                    and receipt.get('cleanup_status') != 'incomplete' and receipt.get('survivors') == []
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
            disposition = _closed(_json(raw, 1048576), 'kind reason processes invocation terminal tracker_clear reconciliation_complete task_tail_complete root_worktree_batch_complete settle_live_complete')
            _check(type(facts['settled']) is bool and type(facts['execution_unknown']) is bool)
            _check(disposition['invocation'] == native)
            _check(facts['settled'] is (disposition['kind'] == 'safe'))
            safe = (disposition['kind'] == 'safe' and disposition['processes'] == []
                    and all(disposition[name] is True for name in ('tracker_clear', 'reconciliation_complete',
                        'task_tail_complete', 'root_worktree_batch_complete', 'settle_live_complete'))
                    and not facts['execution_unknown'] and facts['settlement_error'] is None)
            if not safe:
                expected_run['state'] = 'held'
            if source_tail:
                _check(state['source_cleanup'] is None and facts['pause_entry_id'] == command['producer_identity']
                       and facts['claim_token'] is None and facts['host_identity'] is None)
                _check(native is not None and native['id'] == source['result_claim']['owner_invocation_id']
                       and native['task_id'] == source['task_id'] and native['agent'] == source['principal']
                       and native['session_id'] == source['session_id'] and native['owner'] in ('ordinary', 'v2'))
                terminal = disposition['terminal']
                if safe:
                    _closed(terminal, 'request terminal_reason attempt receipt cleanup_error')
                    receipt = terminal['receipt']
                    _check(type(terminal['attempt']) is int and terminal['attempt'] >= 0
                           and terminal['request']['logical_id'] == source['task_id']
                           and terminal['request']['org'] == org and terminal['request']['agent_id'] == source['principal']
                           and type(receipt) is dict and receipt.get('quiescent') is True
                           and receipt.get('backend') != 'passthrough' and receipt.get('cleanup_status') != 'incomplete'
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

                else:
                    expected_run['state'] = 'held'
                state['cleanup'] = safe
                state['cleanup_recorded'] = True
        elif kind == 'held':
            _check(facts['owner_event_ids'] == [previous['id']] and facts['resume_state'] == 'development'
                   and type(facts['reason']) is str and facts['required_owner'] == command['producer_identity']
                   and type(facts['required_action']) is str)
            expected_run['state'] = 'held'
        if not source_tail and state['claim'] is not None:
            _check(origin['claim'] == state['claim'] and origin['session_id'] == expected_attempt['session_id']
                   and command['producer_identity'] == state['claim']['owner'])
        _check(same(after['run'], expected_run)
               and after['attempts'] == [dict(id=attempt['id'], record=encode_row(expected_attempt))]
               and same(after['tasks'], [dict(id=k, record=encode_row(expected_tasks[k])) for k in sorted(expected_tasks)]))
        run, attempt, tasks = expected_run, expected_attempt, expected_tasks
        for key, value in tasks.items():
            anchors[key] = (event['id'], _hash(_canonical(encode_row(value))), anchors[key][2])
        previous = event
        state['history'].append(event)
        storage.add('workflow_coding_events', event)
    tasks, pending = fold_heartbeats(conn, org, budget, storage, tasks, anchors)
    state.update(run=run, attempt=attempt, tasks=tasks, anchors=anchors, pending_observations=pending)
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
    selected = set()
    for row in rows(conn, 'task_pause_controls', budget):
        journal = decode_journal(row['admission_journal_json'])
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
            entry = storage.runs[owner[1]]
            attempt, state = entry['attempt'], entry['native']
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
            else:
                claim = state['claim']
                _check(native['id'] == claim['invocation_id'] and native['session_id'] == attempt['session_id']
                       and native['fingerprint'] == claim['task_fingerprint'])
        storage.add('task_pause_controls', row)
    for entry in storage.runs.values():
        state = entry['native']
        # Missing native ownership never proves teardown. Retain the original
        # J claim/host identity and expose uncertainty until its actual late
        # owner commits a positive cleanup observation.
        state['ownership_unknown'] = (state['claim'] is not None
            and not (state['cleanup_recorded'] and state['cleanup'])
            and entry['attempt']['task_id'] not in selected)
