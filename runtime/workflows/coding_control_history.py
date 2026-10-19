"""Independent queued-only manager-control reconstruction; no execution adapter."""
from __future__ import annotations

import base64
from copy import deepcopy
from typing import Any

from runtime.workflows.coding_bootstrap import decode_row, encode_row, identity
from runtime.workflows.coding_saved_records import (
    _Ancestry, _canonical, _capture, _check, _closed, _hash, _integer, _json, _timestamp, _token,
)

RESTORE = 'restore already-authorized control admission or return unavailable to root'


def control_brief(instance: str, predecessor: str) -> str:
    return ('Coding manager control is queued; execution and selection are unavailable.\n'
            'Retain the failed predecessor and its original owners. No successor is authorized.\n'
            'Instance: ' + instance + '\nPredecessor: ' + predecessor)


def positive_failure(state: dict, attempt: dict, budget: Any = None) -> list[str] | None:
    """Positive original observations, never a task label or missing native owner."""
    result, host = state['callback'], state['host']
    if not (result is not None and type(result['id']) is int and result['status'] == 'failed'
            and result['id'] == attempt['result_id'] and attempt['state'] == 'failed'
            and state['terminal'] == 'failed' and state['cleanup_recorded'] and state['cleanup']
            and state['source_settled'] and not state.get('ownership_unknown')):
        return None
    receipt = None if host is None else host['terminal_receipt']
    if not (type(receipt) is dict and receipt.get('terminal_reason') == 'failure'
            and receipt.get('quiescent') is True and receipt.get('backend') == host['backend']
            and host['backend'] != 'passthrough' and receipt.get('survivors') == []
            and receipt.get('cleanup_status') != 'incomplete' and host['cleanup'] == dict(error=None)):
        return None
    witnesses = []
    for event in state['history']:
        if budget is not None:
            budget.step()
        if event['event_kind'] in ('callback_recorded', 'terminal_recorded', 'cleanup_settled'):
            witnesses.append(event['id'])
    # This slice has one development host and one original source invocation.
    # Every contributing cleanup is independently authenticated by read_native.
    if len(witnesses) != 4:
        return None
    return witnesses


def read_admission(conn: Any, org: str, budget: Any, storage: Any, value: Any, *,
                   source: dict, tasks: dict, roles: dict, command: dict, mapping: dict,
                   instance: str, previous: dict, ancestors: list) -> dict:
    """Original-time authority, full causal rows, native Pause and capacity."""
    from runtime.infrastructure.task_pause_controls import decode_journal
    from runtime.workflows._coding_role_records import configuration
    from runtime.workflows.coding_authority_history import _capture_admission
    from runtime.workflows.coding_bootstrap import effective_bindings
    from runtime.workflows.coding_native_history import _row
    from runtime.workflows.coding_start_history import same
    from runtime.workflows.coding_start_records import effective_mapping

    value = _closed(value, 'format authority record manager_teams tasks pause capacity '
                    'max_revise_rounds effective_bindings_base64')
    _check(value['format'] == 'workflow-coding-control-admission@1')
    grant, _ = _capture(value['authority'], org, _Ancestry())
    _check(grant == _canonical(command))
    pin = value['authority']['pin']
    record = dict(id=identity('control-admission', instance, previous['id']), namespace=pin['namespace'],
        generation=pin['generation'], request_digest=_hash(grant), admitted_by='service:coding-dispatcher:' + org)
    _check(same(value['record'], record) and value['authority']['admission_record_id'] == record['id'])
    _capture_admission(conn, record, value['authority'], budget)
    storage.add('workflow_admission_records', record)
    snapshot = _json(base64.b64decode(value['authority']['snapshot_bytes_base64'], validate=True), 1048576)
    configuration(mapping, snapshot)
    configuration(effective_mapping(mapping, command), snapshot)
    _check(roles == effective_mapping(mapping, command)['roles']
           and roles['agent_manager']['principal'] == source['principal']
           and value['manager_teams'] == [roles['agent_manager']['team']]
           and base64.b64decode(value['effective_bindings_base64'], validate=True) == effective_bindings(instance, roles))
    _check(type(value['tasks']) is list and 3 <= len(value['tasks']) <= 512)
    closure = {}
    for encoded in value['tasks']:
        budget.step()
        row = _row(encoded)
        _check(row['id'] not in closure)
        closure[row['id']] = row
    _check(set(closure) == set(tasks) | {r['id'] for r in ancestors})
    for key, task in tasks.items():
        _check(same(task, closure[key]) and task['cancelled_at'] is None and task['zombie_flagged_at'] is None)
    for row in ancestors:
        _check(same(closure[row['id']], row))
    current, seen = closure[source['task_id']], set()
    while True:
        budget.step()
        _check(current['id'] not in seen and len(seen) < 20
               and current['status'] == 'in_progress' and current['cancelled_at'] is None
               and current['zombie_flagged_at'] is None and current['team'] == roles['agent_manager']['team'])
        seen.add(current['id'])
        if current['parent_task_id'] is None:
            _check(current['id'] == source['root_ancestor_id'])
            break
        _check(current['parent_task_id'] in closure)
        current = closure[current['parent_task_id']]
    pause = _closed(decode_row(value['pause']), 'root_task_id schema_version held generation paused_at '
        'resumed_at updated_at actor release_pending_generation admission_journal_json')
    _integer(pause['schema_version'], 1)
    _integer(pause['generation'])
    _check(pause['schema_version'] == 1 and type(pause['held']) is int and pause['held'] == 0
           and pause['generation'] % 2 == 0 and pause['root_task_id'] == source['root_ancestor_id'])
    journal = decode_journal(pause['admission_journal_json'])
    _check(not any(e['task_id'] in tasks for e in journal['entries']))
    capacity = _closed(value['capacity'], 'sessions tasks work_hours')
    _check(type(capacity['sessions']) is list and len(capacity['sessions']) <= 1024
           and capacity['tasks'] == [] and capacity['work_hours'] == [])
    for session in capacity['sessions']:
        _check(type(session) is list and len(session) == 3 and all(type(v) is str and v for v in session)
               and session[1] != roles['agent_manager']['principal'])
    _integer(value['max_revise_rounds'])
    limits = source['budgets']
    _check(value['max_revise_rounds'] == limits['max_revise_rounds']
           and tasks[source['task_id']]['orchestration_step_count'] == limits['source_step']
           and tasks[source['task_id']]['revision_count'] == limits['source_revision']
           and (not limits['max_revise_rounds'] or limits['source_revision'] < limits['max_revise_rounds']))
    return value['authority']


def read_control(conn: Any, org: str, budget: Any, storage: Any, *, event: dict, body: dict,
                 previous: dict, state: dict, run: dict, attempt: dict, tasks: dict,
                 source: dict, roles: dict, command: dict, mapping: dict, authority: dict,
                 ancestors: list) -> tuple[dict, dict]:
    """Reconstruct BOTH the safe-failure hold and its sole pending control."""
    from runtime.workflows.coding_native_history import _row
    from runtime.workflows.coding_start_history import same

    kind, evidence = event['event_kind'], body['evidence']
    _check(body['format'] == 'workflow-coding-event@3' and kind in ('held', 'manager_control_queued'))
    internal = _closed(evidence['command'],
        'format kind source_event_ids source_event_digests subject producer_identity observation')
    _check(internal['format'] == 'workflow-coding-internal-command@3' and internal['kind'] == kind
           and internal['source_event_ids'] == [previous['id']]
           and internal['source_event_digests'] == [previous['event_digest']] and internal['subject'] is None)
    principal = 'service:coding-dispatcher:' + org
    producer = 'coding-dispatcher:' + org if kind == 'held' else state.get('safe_failure_held')
    control_id = None if kind == 'held' else 'coding-control:' + _hash(_canonical([
        run['instance_id'], attempt['id'], producer]))
    _check(internal['producer_identity'] == (producer if kind == 'held' else control_id))
    key = 'coding-internal:' + _hash(_canonical([run['instance_id'], kind, internal['producer_identity'],
        internal['source_event_ids'], internal['observation']]))
    _check(event['principal'] == principal and event['org_slug'] == org and event['operation_key'] == key
           and event['request_digest'] == _hash(_canonical(internal))
           and event['id'] == identity('event', org, run['instance_id'], principal, key)
           and body['operation'] == dict(org_slug=org, principal=principal, key=key, request_digest=event['request_digest'])
           and evidence['origin'] == dict(kind='service', principal=principal, task_id=None,
                session_id=None, result_id=None, claim=None))
    witnesses = positive_failure(state, attempt, budget)
    _check(witnesses is not None and run['state'] == 'held' and run['current_attempt_id'] == attempt['id']
           and state.get('control') is None)
    expected_run = {**run, 'cas_version': run['cas_version'] + 1, 'last_event_id': event['id']}
    _check(expected_run['cas_version'] <= source['budgets']['coding_event_limit']
           and source['budgets']['coding_attempt_limit'] >= 2)
    expected_tasks = deepcopy(tasks)
    facts = evidence['facts']
    if kind == 'held':
        _check(state.get('safe_failure_held') is None and internal['observation'] is None
               and evidence['authority'] == authority and evidence['rows'] == [])
        _check(same(facts, dict(reason='stage_safe_failure', owner_event_ids=witnesses,
            resume_state='development', required_owner=source['principal'], required_action=RESTORE)))
        state['safe_failure_held'] = event['id']
    else:
        _check(previous['id'] == producer and previous['event_kind'] == 'held')
        _closed(facts, 'control positive_witness_event_ids task_record scope_bytes_base64 scope_digest')
        observation = _closed(internal['observation'], 'admission')
        current = read_admission(conn, org, budget, storage, observation['admission'], source=source,
            tasks=tasks, roles=roles, command=command, mapping=mapping, instance=run['instance_id'],
            previous=previous, ancestors=ancestors)
        _check(evidence['authority'] == current and evidence['rows'] == [dict(
            table='workflow_admission_records', id=current['admission_record_id'],
            record=encode_row(observation['admission']['record']))])
        control = _closed(facts['control'], 'control_id control_task_id predecessor_attempt_id notification_event_id '
            'manager_principal manager_team business_purpose original_limits')
        task = _row(facts['task_record'])
        _token(task['id'])
        carrier = next(row for row in tasks.values() if row['parent_task_id'] == source['task_id'])
        _check(same(control, dict(control_id=control_id, control_task_id=task['id'], predecessor_attempt_id=attempt['id'],
            notification_event_id=event['id'], manager_principal=roles['agent_manager']['principal'],
            manager_team=roles['agent_manager']['team'], business_purpose='coding-replacement-selection@1',
            original_limits=source['budgets'])) and task['id'] not in tasks)
        _check(task['parent_task_id'] == carrier['id'] and task['task_type'] == 'subtask'
               and task['assigned_agent'] == control['manager_principal'] and task['team'] == control['manager_team']
               and task['status'] == 'pending' and task['brief'] == control_brief(run['instance_id'], attempt['id'])
               and task['orchestration_step_count'] == task['revision_count'] == 0
               and task['created_at'] == task['updated_at'] == event['created_at']
               and task['session_timeout_seconds'] == source['budgets']['session_timeout_seconds'])
        for name in ('block_kind', 'current_session_id', 'active_chain', 'active_fanout', 'revisit_of_task_id',
                     'dispatched_from_thread_id', 'blocked_on_job_ids', 'note', 'completed_at', 'final_output_summary',
                     'final_output_dir', 'executor_pid', 'cancelled_at', 'last_heartbeat', 'zombie_flagged_at'):
            _check(task[name] is None)
        for row in (tasks[source['task_id']], carrier):
            _check(row['status'] == 'in_progress' and row['block_kind'] == 'delegated')
        scope = _json(base64.b64decode(facts['scope_bytes_base64'], validate=True), 1048576)
        expected_scope = dict(format='workflow-coding-control-scope@1', control_id=control_id,
            instance_id=run['instance_id'], control_task_id=task['id'], predecessor_attempt_id=attempt['id'],
            predecessor_scope_digest=attempt['scope_digest'], held_event_id=producer,
            manager_binding=dict(**roles['agent_manager'], task_id=task['id']), release=command['release'],
            mapping_digest=command['mapping']['digest'], authority=current, positive_witness_event_ids=witnesses,
            eligible_targets=mapping['eligible_replacements']['implementer'], original_limits=source['budgets'],
            business_purpose='coding-replacement-selection@1')
        raw = _canonical(expected_scope)
        _check(scope == expected_scope and base64.b64decode(facts['scope_bytes_base64'], validate=True) == raw
               and facts['scope_digest'] == _hash(raw) and facts['positive_witness_event_ids'] == witnesses)
        expected_tasks[task['id']] = task
        state['control'] = dict(**control, scope=expected_scope, scope_digest=facts['scope_digest'],
                                notification_digest=event['event_digest'], notification_command=internal)
    before, after = body['before'], body['after']
    before_tasks = [dict(id=k, record=encode_row(v)) for k, v in sorted(tasks.items())]
    if kind == 'manager_control_queued':
        before_tasks.append(dict(id=control['control_task_id'], record=None))
        before_tasks.sort(key=lambda row: row['id'])
    _check(same(before['run'], run) and same(before['tasks'], before_tasks)
           and before['attempts'] == after['attempts'] == [dict(id=attempt['id'], record=encode_row(attempt))]
           and same(after['run'], expected_run) and same(after['tasks'],
                [dict(id=k, record=encode_row(v)) for k, v in sorted(expected_tasks.items())]))
    _timestamp(body['created_at'])
    return expected_run, expected_tasks
