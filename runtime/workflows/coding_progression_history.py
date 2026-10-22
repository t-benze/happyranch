"""Independent SELECT-only reconstruction of the first ordered coding edge.

Original bootstrap/native families keep their literal readers. These records
carry no current authority and never supply a trusted writer after-image.
"""
from __future__ import annotations

import base64
from typing import Any

from runtime.workflows.coding_authority_history import _capture_admission, _decoded
from runtime.workflows.coding_bootstrap import decode_row, encode_row, identity, review_brief, review_scope
from runtime.workflows.coding_saved_records import _Ancestry, _canonical, _capture, _check, _closed, _hash, _json, _timestamp
from runtime.workflows.coding_start_records import effective_mapping, parse_stage_output

EDGE_KINDS = ('artifact_staged', 'contribution_recorded', 'stage_queued', 'advance')
KINDS = (*EDGE_KINDS, 'review_staged')


def admission(conn: Any, org: str, budget: Any, storage: Any, value: dict, *,
              run: dict, tasks: dict, source: dict, roles: dict, mapping: dict, command: dict, future_task_id: str) -> None:
    """Reconstruct original authority, Pause, causal move and capacity predicates."""
    from runtime.infrastructure.task_pause_controls import decode_journal
    from runtime.workflows._coding_role_records import configuration
    from runtime.workflows.coding_native_history import _row
    from runtime.workflows.coding_start_history import rows, same
    value = _closed(value, 'format authority record tasks pause move sessions capacity_tasks work_hours max_revise_rounds')
    _check(value['format'] == 'workflow-coding-edge-admission@1')
    budget.consumed += 4 * len(value['authority']['snapshot_bytes_base64'])
    _check(budget.remaining_bytes >= 0)
    grant, _ = _capture(value['authority'], org, _Ancestry())
    _check(grant == _canonical(command))
    pin = value['authority']['pin']
    expected = dict(id=identity('edge-admission', run['instance_id'], run['cas_version']),
        namespace=pin['namespace'], generation=pin['generation'], request_digest=_hash(grant),
        admitted_by='service:coding-dispatcher:' + org)
    _check(value['authority']['admission_record_id'] == expected['id'] and same(value['record'], expected))
    _capture_admission(conn, expected, value['authority'], budget)
    storage.add('workflow_admission_records', expected)
    snapshot = _json(base64.b64decode(value['authority']['snapshot_bytes_base64'], validate=True), 1048576)
    _decoded(snapshot, budget)
    configuration(mapping, snapshot)
    configuration(effective_mapping(mapping, command), snapshot)
    _check(roles == effective_mapping(mapping, command)['roles'])
    _check(type(value['tasks']) is list and 1 <= len(value['tasks']) <= 512)
    closure = {}
    for encoded in value['tasks']:
        row = _row(encoded)
        _check(row['id'] not in closure)
        closure[row['id']] = row
    for key, row in tasks.items():
        _check(key in closure and same(row, closure[key]))
    for row in closure.values():
        _check(row['cancelled_at'] is None and row['zombie_flagged_at'] is None
               and row['active_chain'] is None and row['active_fanout'] is None)
        for key in ('parent_task_id', 'revisit_of_task_id'):
            _check(row[key] is None or row[key] in closure)
    # Re-enumerate the complete causal native inventory independently. The
    # sole later allocation is excluded at this original boundary; every other
    # original row/edge must still exist. Owned rows use replay-time values,
    # while unchanged ordinary siblings cannot be invented in a saved capture.
    _check(future_task_id not in closure)
    pending, discovered = [source['task_id']], set()
    while pending:
        budget.step()
        key = pending.pop()
        if key == future_task_id or key in discovered:
            continue
        _check(len(discovered) < 512 and key in closure)
        actual = list(rows(conn, 'tasks', budget, where='id=?', args=(key,), maximum=1))
        _check(len(actual) == 1)
        row = actual[0]
        if key not in tasks:
            _check(same(row, closure[key]))
        else:
            for name in ('id', 'assigned_agent', 'team', 'brief', 'parent_task_id', 'revisit_of_task_id'):
                _check(same(row[name], closure[key][name]))
        discovered.add(key)
        pending.extend(r['id'] for r in rows(conn, 'tasks', budget,
            where='parent_task_id=? OR revisit_of_task_id=?', args=(key, key), maximum=512))
        pending.extend(row[k] for k in ('parent_task_id', 'revisit_of_task_id') if row[k] is not None)
    _check(discovered == set(closure))
    current, seen = closure[source['task_id']], set()
    while True:
        _check(current['id'] not in seen and len(seen) < 20
               and current['status'] == 'in_progress'
               and current['team'] == roles['agent_manager']['team'])
        seen.add(current['id'])
        if current['parent_task_id'] is None:
            _check(current['id'] == source['root_ancestor_id'])
            break
        current = closure[current['parent_task_id']]
    pause = _closed(decode_row(value['pause']), 'root_task_id schema_version held generation paused_at resumed_at updated_at actor release_pending_generation admission_journal_json')
    _check(pause['root_task_id'] == source['root_ancestor_id']
           and type(pause['schema_version']) is int and pause['schema_version'] == 1
           and type(pause['held']) is int and pause['held'] == 0
           and type(pause['generation']) is int and pause['generation'] >= 0 and pause['generation'] % 2 == 0)
    journal = decode_journal(pause['admission_journal_json'])
    _check(not any(e['task_id'] in closure for e in journal['entries']))
    _check(type(value['max_revise_rounds']) is int
           and value['max_revise_rounds'] == source['budgets']['max_revise_rounds']
           and source['budgets']['coding_revision_limit'] >= 1
           and source['budgets']['coding_attempt_limit'] >= 2
           and run['cas_version'] + 4 <= source['budgets']['coding_event_limit'])
    _check(value['capacity_tasks'] == [] and value['work_hours'] == []
           and type(value['sessions']) is list and len(value['sessions']) <= 1024)
    reviewer = roles['code_reviewer']['principal']
    for session in value['sessions']:
        _check(type(session) is list and len(session) == 3
               and all(type(item) is str and item for item in session) and session[1] != reviewer)
    if value['move'] is not None:
        from runtime.infrastructure.team_move_store import MoveRequest, _decode
        move = decode_row(value['move'])
        request = _decode(MoveRequest, move['request_bytes'])
        actual = list(rows(conn, 'team_move_operations', budget, where='operation_id=?',
                           args=(move['operation_id'],), maximum=1))
        _check(len(actual) == 1 and request.org_slug == org and move['active_slot'] == 1
               and actual[0]['request_bytes'] == move['request_bytes']
               and actual[0]['request_digest'] == move['request_digest'] == _hash(move['request_bytes']))
        for binding in roles.values():
            _check(binding['team'] != request.source_team and binding['principal'] not in request.affected_agents)
        for row in closure.values():
            _check(row['team'] != request.source_team and row['assigned_agent'] not in request.affected_agents)


def replay(conn: Any, org: str, budget: Any, storage: Any, *, event: dict, body: dict,
           previous: dict, initial: dict, run: dict, attempts: dict, tasks: dict,
           state: dict, source: dict, roles: dict, mapping: dict, command: dict) -> tuple:
    """Derive every target row from validated predecessors and actual callback."""
    from runtime.workflows.coding_start_history import same
    kind = event['event_kind']
    _check(kind in KINDS and body['format'] == 'workflow-coding-event@3')
    evidence = _closed(body['evidence'], 'kind command origin authority rows facts native_task_observations')
    facts = _closed(evidence['facts'], 'format developer_attempt_id result_id output_digest contributor reviewer_task_id admission')
    _check(facts['format'] == 'workflow-coding-edge@1' and evidence['kind'] == kind
           and evidence['authority'] == initial['evidence']['authority'] and evidence['rows'] == [])
    current = attempts[run['current_attempt_id']]
    _check(event['attempt_id'] == current['id'])
    callback = state['callback']
    _check(state['source_settled'] and state['cleanup_recorded'] and state['cleanup']
           and state['terminal'] == 'success' and callback is not None
           and not state.get('ownership_unknown') and current['state'] == 'completed')
    subject = None if current['stage'] == 'development' else _json(current['scope_bytes'], 1048576)['subject']
    _check(type(callback['output_summary']) is str and len(callback['output_summary']) <= 65536)
    # Reserve conservative UTF8/decoded/canonical copies before nested parsing.
    budget.consumed += 12 * len(callback['output_summary'])
    _check(budget.remaining_bytes >= 0)
    output = parse_stage_output(callback, instance=run['instance_id'], attempt=current,
                               work=command['work'], subject=subject)
    _decoded(output, budget)
    developer = next(a for a in attempts.values() if a['stage'] == 'development')
    _check(facts['developer_attempt_id'] == developer['id']
           and type(facts['result_id']) is int and facts['result_id'] == callback['id']
           and facts['output_digest'] == _hash(callback['output_summary'].encode('utf8'))
           and facts['contributor'] == developer['principal']
           and roles['code_reviewer']['principal'] != developer['principal'])
    origin = dict(kind='settled_result', principal=current['principal'], task_id=current['task_id'],
                  session_id=current['session_id'], result_id=callback['id'], claim=state['claim'])
    _check(evidence['origin'] == origin)
    internal = dict(format='workflow-coding-edge-command@1', kind=kind,
        source_event_ids=[previous['id']], source_event_digests=[previous['event_digest']],
        subject=output['subject'], facts=facts)
    _check(evidence['command'] == internal)
    principal = 'service:coding-dispatcher:' + org
    key = 'coding-internal:' + _hash(_canonical([run['instance_id'], kind, callback['id'], internal]))
    operation = dict(org_slug=org, principal=principal, key=key, request_digest=_hash(_canonical(internal)))
    _check(body['operation'] == operation and event['org_slug'] == org and event['principal'] == principal
           and event['operation_key'] == key and event['request_digest'] == operation['request_digest']
           and event['id'] == identity('event', org, run['instance_id'], principal, key))
    for projection in (body['before'], body['after']):
        _closed(projection, 'run attempts instances tasks rounds requests assignments')
        _check(projection['instances'] == initial['after']['instances']
               and all(projection[k] == [] for k in ('rounds', 'requests', 'assignments')))
    def projection(r: dict, a: dict, t: dict) -> dict:
        return dict(run=r, attempts=[dict(id=k, record=encode_row(a[k])) for k in sorted(a)],
            tasks=[dict(id=k, record=encode_row(t[k])) for k in sorted(t)],
            instances=initial['after']['instances'], rounds=[], requests=[], assignments=[])
    _check(same(body['before'], projection(run, attempts, tasks)))
    target_run, target_attempts, target_tasks = dict(run), dict(attempts), dict(tasks)
    target_run.update(cas_version=run['cas_version'] + 1, last_event_id=event['id'])
    if kind == 'review_staged':
        _check(current['stage'] == 'code_review' and state.get('review_output') is None
               and facts['reviewer_task_id'] == current['task_id'] and facts['admission'] is None)
        state['review_output'] = output
        target_run['state'] = 'held'
    else:
        _check(current['stage'] == 'development' and run['state'] == 'development')
        edge_index = state.get('edge_index', 0)
        _check(edge_index < len(EDGE_KINDS) and kind == EDGE_KINDS[edge_index])
        if edge_index == 0:
            admission(conn, org, budget, storage, facts['admission'], run=run, tasks=tasks,
                      source=source, roles=roles, mapping=mapping, command=command, future_task_id=facts['reviewer_task_id'])
            state['edge_facts'] = facts
            state['subject'] = output['subject']
        else:
            _check(same(facts, state['edge_facts']))
        state['edge_index'] = edge_index + 1
        if kind == 'stage_queued':
            task_id = facts['reviewer_task_id']
            _check(type(task_id) is str and task_id.startswith('TASK-') and task_id not in tasks)
            binding = roles['code_reviewer']
            # Derive the native initial row from the original pristine developer
            # row, replacing only this separately allocated task's identity/pins.
            first = decode_row(initial['after']['attempts'][0]['record'])
            pristine = next(decode_row(v['record']) for v in initial['after']['tasks'] if v['id'] == first['task_id'])
            task = {**pristine, 'id': task_id, 'assigned_agent': binding['principal'], 'team': binding['team'],
                'brief': review_brief(command['work']), 'created_at': event['created_at'], 'updated_at': event['created_at']}
            scope = _canonical(review_scope(org=org, instance=run['instance_id'], command=command,
                roles=roles, developer=developer, subject=output['subject']))
            next_attempt = {**first, 'id': identity('attempt', run['instance_id'], 2), 'stage': 'code_review',
                'predecessor_id': developer['id'], 'task_id': task_id, 'principal': binding['principal'],
                'scope_bytes': scope, 'scope_digest': _hash(scope)}
            _check(next_attempt['id'] not in attempts and len(attempts) == 1)
            target_attempts[next_attempt['id']] = next_attempt
            target_tasks[task_id] = task
        if kind == 'advance':
            _check(len(attempts) == 2)
            next_attempt = attempts[identity('attempt', run['instance_id'], 2)]
            _check(next_attempt['task_id'] == facts['reviewer_task_id'])
            target_run.update(current_attempt_id=next_attempt['id'], current_revision=1, state='code_review')
    _check(same(body['after'], projection(target_run, target_attempts, target_tasks)))
    return target_run, target_attempts, target_tasks
