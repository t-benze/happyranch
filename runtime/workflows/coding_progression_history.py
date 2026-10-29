"""Independent SELECT-only reconstruction of coding edges through final manager.

Original bootstrap/native families keep their literal readers. These records
carry no current authority and never supply a trusted writer after-image.
"""
from __future__ import annotations

from runtime.infrastructure._workflow_reader import (
    _add as _reader_add,
    _append as _reader_append,
    _extend as _reader_extend,
    _put as _reader_put,
    _update as _reader_update,
)

from runtime.infrastructure._workflow_reader import (
    _all_true as _reader_all_true,
    _any_true as _reader_any_true,
)

from runtime.infrastructure._workflow_reader import (
    _set as _reader_set,
)

from runtime.infrastructure._workflow_reader import _equal as _reader_equal

from runtime.infrastructure._workflow_reader import (
    _dict as _reader_dict,
    _list as _reader_list,
    _sorted as _reader_sorted,
)

from runtime.infrastructure._workflow_reader import _validate as _reader_validate

from runtime.infrastructure._workflow_reader import (
    _reserve as _reader_reserve,
    _work as _reader_work,
)


from runtime.infrastructure._workflow_reader import (
    _dump as _reader_dump,
    _encode as _reader_encode,
)

from typing import Any

from runtime.workflows.coding_authority_history import _capture_admission, _decoded
from runtime.workflows.coding_bootstrap import decode_row, encode_row, identity, final_brief, final_scope, qa_brief, qa_scope, review_brief, review_scope
from runtime.workflows._coding_settings_execution import _read_authority, _read_json
from runtime.workflows.coding_saved_records import _canonical, _check, _closed, _hash, _timestamp
from runtime.workflows.coding_start_records import effective_mapping, parse_final_output, parse_stage_output

EDGE_KINDS = ('artifact_staged', 'contribution_recorded', 'stage_queued', 'advance')
QA_EDGE_KINDS = ('review_staged', 'stage_queued', 'advance')
KINDS = (*EDGE_KINDS, 'review_staged', 'manager_completed', 'return')


def admission(conn: Any, org: str, budget: Any, storage: Any, value: dict, *,
              run: dict, tasks: dict, source: dict, roles: dict, mapping: dict, command: dict,
              future_task_id: str, later_qa_tasks: set[str], qa: bool = False, final: bool = False, destination: str | None = None,
              target: int | None = None, attempt_count: int | None = None, manager_retry: bool = False) -> None:
    """Reconstruct original authority, Pause, causal move and capacity predicates."""
    from runtime.infrastructure.task_pause_controls import decode_journal
    from runtime.workflows._coding_settings_execution import _current_configuration
    from runtime.workflows.coding_native_history import _row
    from runtime.workflows.coding_start_history import rows, same
    value = _closed(value, 'format authority record tasks pause move sessions capacity_tasks work_hours max_revise_rounds'
        + (' destination target_revision attempt_count' if destination is not None else ''), reader_budget=budget)
    _check(value['format'] == ('workflow-coding-edge-admission@5' if manager_retry else 'workflow-coding-edge-admission@4' if destination is not None else 'workflow-coding-edge-admission@3' if final else 'workflow-coding-edge-admission@2' if qa else 'workflow-coding-edge-admission@1'))
    grant, snapshot = _read_authority(value['authority'], org, budget)
    _check(grant == _canonical(command, reader_budget=budget))
    pin = value['authority']['pin']
    expected = (_reader_reserve(budget, 704) or dict(id=identity('edge-admission', run['instance_id'], run['cas_version'], budget=budget),
        namespace=pin['namespace'], generation=pin['generation'], request_digest=_hash(grant, reader_budget=budget),
        admitted_by='service:coding-dispatcher:' + org))
    _check(value['authority']['admission_record_id'] == expected['id'] and same(value['record'], expected, budget=budget))
    _capture_admission(conn, expected, value['authority'], budget)
    storage.add('workflow_admission_records', expected, budget=budget)
    _current_configuration(mapping, snapshot, command, budget)
    _check(roles == effective_mapping(mapping, command, budget=budget)['roles'])
    _check(type(value['tasks']) is list and 1 <= len(value['tasks']) <= 512)
    closure = (_reader_reserve(budget, 64) or {})
    for encoded in value['tasks']:
        row = _row(encoded, budget=budget)
        _check(row['id'] not in closure)
        _reader_put(row, closure, row['id'], budget=budget)
    for key, row in tasks.items():
        _check(key in closure and same(row, closure[key], budget=budget))
    for row in closure.values():
        _check(row['cancelled_at'] is None and row['zombie_flagged_at'] is None
               and row['active_chain'] is None and row['active_fanout'] is None)
        for key in ('parent_task_id', 'revisit_of_task_id'):
            _check(row[key] is None or row[key] in closure)
    # Re-enumerate the complete causal native inventory independently. The
    # current edge allocation and any independently replayed later QA allocation
    # are excluded at this original boundary; every original row/edge must still
    # exist. Owned rows use replay-time values,
    # while unchanged ordinary siblings cannot be invented in a saved capture.
    _check(future_task_id not in closure and not (later_qa_tasks & closure.keys()))
    pending, discovered = (_reader_reserve(budget, 96) or (_reader_reserve(budget, 192) or [source['task_id']], _reader_set(budget=budget)))
    while pending:
        budget.step()
        key = pending.pop()
        if key == future_task_id or key in later_qa_tasks or key in discovered:
            continue
        _check(len(discovered) < 512 and key in closure)
        actual = _reader_list(rows(conn, 'tasks', budget, where='id=?', args=(_reader_reserve(budget, 80) or (key,)), maximum=1), budget=budget)
        _check(len(actual) == 1)
        row = actual[0]
        if key not in tasks:
            from runtime.workflows._coding_cancellation_history import defer_ancestry
            defer_ancestry(row, closure[key], storage, budget)
        else:
            for name in ('id', 'assigned_agent', 'team', 'brief', 'parent_task_id', 'revisit_of_task_id'):
                _check(same(row[name], closure[key][name], budget=budget))
        _reader_add(discovered, key, budget=budget)
        for child in rows(conn, 'tasks', budget,
                where='parent_task_id=? OR revisit_of_task_id=?', args=(_reader_reserve(budget, 96) or (key, key)), maximum=512):
            if child['id'] not in closure and child['id'] != future_task_id and child['id'] not in later_qa_tasks:
                from datetime import datetime
                _check(storage.return_boundary is not None and
                    datetime.fromisoformat(child['created_at']) >
                    datetime.fromisoformat(storage.return_boundary.replace('Z', '+00:00')))
                continue  # Later ordinary caller work; return must still replay.
            _reader_append(pending, child['id'], budget=budget)
        _reader_extend(pending, (row[k] for k in ('parent_task_id', 'revisit_of_task_id') if row[k] is not None), budget=budget)
    _check(discovered == _reader_set(closure, budget=budget))
    current, seen = (_reader_reserve(budget, 96) or (closure[source['task_id']], _reader_set(budget=budget)))
    while True:
        _check(current['id'] not in seen and len(seen) < 20
               and current['status'] == 'in_progress'
               and current['team'] == roles['agent_manager']['team'])
        _reader_add(seen, current['id'], budget=budget)
        if current['parent_task_id'] is None:
            _check(current['id'] == source['root_ancestor_id'])
            break
        current = closure[current['parent_task_id']]
    pause = _closed(decode_row(value['pause'], budget=budget), 'root_task_id schema_version held generation paused_at resumed_at updated_at actor release_pending_generation admission_journal_json', reader_budget=budget)
    _check(pause['root_task_id'] == source['root_ancestor_id']
           and type(pause['schema_version']) is int and pause['schema_version'] == 1
           and type(pause['held']) is int and pause['held'] == 0
           and type(pause['generation']) is int and pause['generation'] >= 0 and pause['generation'] % 2 == 0)
    journal = decode_journal(pause['admission_journal_json'], budget=budget)
    _check(not _reader_any_true((e['task_id'] in closure for e in journal['entries']), budget=budget))
    _check(type(value['max_revise_rounds']) is int
           and value['max_revise_rounds'] == source['budgets']['max_revise_rounds']
           and source['budgets']['coding_revision_limit'] >= 1
           and source['budgets']['coding_attempt_limit'] >= (4 if final else 3 if qa else 2)
           and run['cas_version'] + (2 if manager_retry else 3 if qa or final else 4) <= source['budgets']['coding_event_limit'])
    _check(_reader_equal(value['capacity_tasks'], (_reader_reserve(budget, 64) or []), budget=budget) and _reader_equal(value['work_hours'], (_reader_reserve(budget, 64) or []), budget=budget)
           and type(value['sessions']) is list and len(value['sessions']) <= 1024)
    from runtime.workflows.coding_revision import ROLES
    if destination is not None:
        _check(value['destination'] == destination and type(value['target_revision']) is int
               and value['target_revision'] == target and (1 if manager_retry else 2) <= target <= min(3, source['budgets']['coding_revision_limit'])
               and type(value['attempt_count']) is int and value['attempt_count'] == attempt_count
               and attempt_count + 1 <= source['budgets']['coding_attempt_limit'])
    if manager_retry:
        _check(destination == 'manager_closeout' and run['state'] == 'held'
               and source['budgets']['coding_same_principal_retry_limit'] >= 1)
    reviewer = roles[ROLES[destination] if destination is not None else 'agent_manager' if final else 'qa_engineer' if qa else 'code_reviewer']['principal']
    for session in value['sessions']:
        _check(type(session) is list and len(session) == 3
               and _reader_all_true((type(item) is str and item for item in session), budget=budget) and session[1] != reviewer)
    if value['move'] is not None:
        from runtime.infrastructure.team_move_store import MoveRequest, _decode
        move = decode_row(value['move'], budget=budget)
        request = _decode(MoveRequest, move['request_bytes'], reader_budget=budget)
        actual = _reader_list(rows(conn, 'team_move_operations', budget, where='operation_id=?',
                           args=(_reader_reserve(budget, 80) or (move['operation_id'],)), maximum=1), budget=budget)
        _check(len(actual) == 1 and request.org_slug == org and move['active_slot'] == 1
               and actual[0]['request_bytes'] == move['request_bytes']
               and actual[0]['request_digest'] == move['request_digest'] == _hash(move['request_bytes'], reader_budget=budget))
        for binding in roles.values():
            _check(binding['team'] != request.source_team and binding['principal'] not in request.affected_agents)
        for row in closure.values():
            _check(row['team'] != request.source_team and row['assigned_agent'] not in request.affected_agents)


def replay(conn: Any, org: str, budget: Any, storage: Any, *, event: dict, body: dict,
           previous: dict, initial: dict, run: dict, attempts: dict, tasks: dict,
           state: dict, source: dict, roles: dict, mapping: dict, command: dict,
           later_qa_tasks: set[str], later_manager_tasks: set[str], settled_stages: dict) -> tuple:
    """Derive every target row from validated predecessors and actual callback."""
    from runtime.workflows.coding_start_history import same
    if body['format'] == 'workflow-coding-event@9' or event['event_kind'] == 'return':
        return replay_return(org, budget, event=event, body=body, previous=previous,
            initial=initial, run=run, attempts=attempts, tasks=tasks, state=state,
            source=source, command=command, settled_stages=settled_stages)
    if body['format'] in ('workflow-coding-event@7', 'workflow-coding-event@8'):
        from runtime.workflows.coding_revision_history import replay_revision
        return replay_revision(conn, org, budget, storage, event=event, body=body, previous=previous,
            initial=initial, run=run, attempts=attempts, tasks=tasks, state=state, source=source, roles=roles,
            mapping=mapping, command=command, settled_stages=settled_stages, future_tasks=later_qa_tasks | later_manager_tasks)
    if body['format'] in ('workflow-coding-event@5', 'workflow-coding-event@6'):
        return replay_final(conn, org, budget, storage, event=event, body=body, previous=previous,
            initial=initial, run=run, attempts=attempts, tasks=tasks, state=state, source=source,
            roles=roles, mapping=mapping, command=command, settled_stages=settled_stages,
            future_tasks=later_qa_tasks | later_manager_tasks)
    if body['format'] == 'workflow-coding-event@4':
        return replay_qa(conn, org, budget, storage, event=event, body=body, previous=previous,
            initial=initial, run=run, attempts=attempts, tasks=tasks, state=state,
            source=source, roles=roles, mapping=mapping, command=command, settled_stages=settled_stages,
            later_manager_tasks=later_manager_tasks)
    kind = event['event_kind']
    _check(kind in KINDS and body['format'] == 'workflow-coding-event@3')
    evidence = _closed(body['evidence'], 'kind command origin authority rows facts native_task_observations', reader_budget=budget)
    facts = _closed(evidence['facts'], 'format developer_attempt_id result_id output_digest contributor reviewer_task_id admission', reader_budget=budget)
    _check(facts['format'] == 'workflow-coding-edge@1' and evidence['kind'] == kind
           and _reader_equal(evidence['authority'], initial['evidence']['authority'], budget=budget) and _reader_equal(evidence['rows'], (_reader_reserve(budget, 64) or []), budget=budget))
    current = attempts[run['current_attempt_id']]
    _check(event['attempt_id'] == current['id'])
    callback = state['callback']
    _check(state['source_settled'] and state['cleanup_recorded'] and state['cleanup']
           and _reader_equal(state['terminal'], 'success', budget=budget) and callback is not None
           and not state.get('ownership_unknown') and current['state'] == 'completed')
    subject = None if current['stage'] == 'development' else _read_json(current['scope_bytes'], budget, 1048576)['subject']
    _check(type(callback['output_summary']) is str and len(callback['output_summary']) <= 65536)
    # Reserve conservative UTF8/decoded/canonical copies before nested parsing.
    _reader_reserve(budget, 12 * len(callback['output_summary']))
    output = parse_stage_output(callback, instance=run['instance_id'], attempt=current,
                               work=command['work'], subject=subject, budget=budget)
    _decoded(output, budget)
    developer = next(a for a in attempts.values() if a['stage'] == 'development')
    _check(facts['developer_attempt_id'] == developer['id']
           and type(facts['result_id']) is int and facts['result_id'] == callback['id']
           and facts['output_digest'] == _hash(_reader_encode(callback['output_summary'], 'utf8', budget=budget), reader_budget=budget)
           and facts['contributor'] == developer['principal']
           and roles['code_reviewer']['principal'] != developer['principal'])
    origin = (_reader_reserve(budget, 832) or dict(kind='settled_result', principal=current['principal'], task_id=current['task_id'],
                  session_id=current['session_id'], result_id=callback['id'], claim=state['claim']))
    _check(_reader_equal(evidence['origin'], origin, budget=budget))
    internal = (_reader_reserve(budget, 832) or dict(format='workflow-coding-edge-command@1', kind=kind,
        source_event_ids=(_reader_reserve(budget, 192) or [previous['id']]), source_event_digests=(_reader_reserve(budget, 192) or [previous['event_digest']]),
        subject=output['subject'], facts=facts))
    _check(_reader_equal(evidence['command'], internal, budget=budget))
    principal = 'service:coding-dispatcher:' + org
    key = 'coding-internal:' + _hash(_canonical((_reader_reserve(budget, 576) or [run['instance_id'], kind, callback['id'], internal]), reader_budget=budget), reader_budget=budget)
    operation = (_reader_reserve(budget, 576) or dict(org_slug=org, principal=principal, key=key, request_digest=_hash(_canonical(internal, reader_budget=budget), reader_budget=budget)))
    _check(_reader_equal(body['operation'], operation, budget=budget) and event['org_slug'] == org and event['principal'] == principal
           and event['operation_key'] == key and event['request_digest'] == operation['request_digest']
           and event['id'] == identity('event', org, run['instance_id'], principal, key, budget=budget))
    for projection in (_reader_reserve(budget, 96) or (body['before'], body['after'])):
        _closed(projection, 'run attempts instances tasks rounds requests assignments', reader_budget=budget)
        _check(_reader_equal(projection['instances'], initial['after']['instances'], budget=budget)
               and _reader_all_true((_reader_equal(projection[k], (_reader_reserve(budget, 64) or []), budget=budget) for k in ('rounds', 'requests', 'assignments')), budget=budget))
    def projection(r: dict, a: dict, t: dict) -> dict:
        return (_reader_reserve(budget, 960) or dict(run=r, attempts=_reader_list(((_reader_reserve(budget, 320) or dict(id=k, record=encode_row(a[k], budget=budget))) for k in _reader_sorted(a, budget=budget)), budget=budget),
            tasks=_reader_list(((_reader_reserve(budget, 320) or dict(id=k, record=encode_row(t[k], budget=budget))) for k in _reader_sorted(t, budget=budget)), budget=budget),
            instances=initial['after']['instances'], rounds=(_reader_reserve(budget, 64) or []), requests=(_reader_reserve(budget, 64) or []), assignments=(_reader_reserve(budget, 64) or [])))
    _check(same(body['before'], projection(run, attempts, tasks), budget=budget))
    target_run, target_attempts, target_tasks = (_reader_reserve(budget, 112) or (_reader_dict(run, budget=budget), _reader_dict(attempts, budget=budget), _reader_dict(tasks, budget=budget)))
    _reader_update(target_run, (_reader_reserve(budget, 320) or {'cas_version': run['cas_version'] + 1, 'last_event_id': event['id']}), budget=budget)
    if kind == 'review_staged':
        _check(current['stage'] == 'code_review' and state.get('review_output') is None
               and facts['reviewer_task_id'] == current['task_id'] and facts['admission'] is None)
        _reader_put(output, state, 'review_output', budget=budget)
        _reader_put('held', target_run, 'state', budget=budget)
    else:
        _check(current['stage'] == 'development' and run['state'] == 'development')
        edge_index = state.get('edge_index', 0)
        _check(edge_index < len(EDGE_KINDS) and kind == EDGE_KINDS[edge_index])
        if edge_index == 0:
            admission(conn, org, budget, storage, facts['admission'], run=run, tasks=tasks,
                      source=source, roles=roles, mapping=mapping, command=command,
                      future_task_id=facts['reviewer_task_id'], later_qa_tasks=later_qa_tasks)
            _reader_put(facts, state, 'edge_facts', budget=budget)
            _reader_put(output['subject'], state, 'subject', budget=budget)
        else:
            _check(same(facts, state['edge_facts'], budget=budget))
        _reader_put(edge_index + 1, state, 'edge_index', budget=budget)
        if kind == 'stage_queued':
            task_id = facts['reviewer_task_id']
            _check(type(task_id) is str and task_id.startswith('TASK-') and task_id not in tasks)
            binding = roles['code_reviewer']
            # Derive the native initial row from the original pristine developer
            # row, replacing only this separately allocated task's identity/pins.
            first = decode_row(initial['after']['attempts'][0]['record'], budget=budget)
            pristine = next(decode_row(v['record'], budget=budget) for v in initial['after']['tasks'] if v['id'] == first['task_id'])
            task = {**pristine, 'id': task_id, 'assigned_agent': binding['principal'], 'team': binding['team'],
                'brief': review_brief(command['work'], budget=budget), 'created_at': event['created_at'], 'updated_at': event['created_at']}
            scope = _canonical(review_scope(org=org, instance=run['instance_id'], command=command,
                roles=roles, developer=developer, subject=output['subject'], budget=budget), reader_budget=budget)
            next_attempt = {**first, 'id': identity('attempt', run['instance_id'], 2, budget=budget), 'stage': 'code_review',
                'predecessor_id': developer['id'], 'task_id': task_id, 'principal': binding['principal'],
                'scope_bytes': scope, 'scope_digest': _hash(scope, reader_budget=budget)}
            _check(next_attempt['id'] not in attempts and len(attempts) == 1)
            _reader_put(next_attempt, target_attempts, next_attempt['id'], budget=budget)
            _reader_put(task, target_tasks, task_id, budget=budget)
        if kind == 'advance':
            _check(len(attempts) == 2)
            next_attempt = attempts[identity('attempt', run['instance_id'], 2, budget=budget)]
            _check(next_attempt['task_id'] == facts['reviewer_task_id'])
            _reader_update(target_run, (_reader_reserve(budget, 448) or {'current_attempt_id': next_attempt['id'], 'current_revision': 1, 'state': 'code_review'}), budget=budget)
    _check(same(body['after'], projection(target_run, target_attempts, target_tasks), budget=budget))
    return (_reader_reserve(budget, 112) or (target_run, target_attempts, target_tasks))


def replay_return(org: str, budget: Any, *, event: dict, body: dict, previous: dict,
                  initial: dict, run: dict, attempts: dict, tasks: dict, state: dict,
                  source: dict, command: dict, settled_stages: dict) -> tuple:
    """Independently derive the sole structural return from authentic work."""
    from datetime import datetime
    from runtime.workflows.coding_start_history import same
    from runtime.workflows.coding_revision import _copy_allowance
    current = attempts[run['current_attempt_id']]
    _check(command['format'] == 'workflow-coding-start@2'
           and body['format'] == 'workflow-coding-event@9' and event['event_kind'] == 'return'
           and event['attempt_id'] == current['id'] and current['stage'] == 'manager_closeout'
           and current['state'] == 'completed' and run['state'] == 'complete'
           and previous['event_kind'] == 'manager_completed' and not state.get('returned')
           and state['final_output']['outcome'] == 'merged'
           and datetime.fromisoformat(event['created_at'].replace('Z', '+00:00')) >=
               datetime.fromisoformat(previous['created_at'].replace('Z', '+00:00')))
    for native in (_reader_reserve(budget, 128 + 32 * (1 + len(settled_stages.values()))) or (*settled_stages.values(), state)):
        _check(native['source_settled'] and native['cleanup_recorded'] and native['cleanup']
               and _reader_equal(native['terminal'], 'success', budget=budget) and not native.get('ownership_unknown'))
    callback = state['callback']
    _check(callback is not None and callback['id'] == current['result_id'])
    subject = state['final_output']['subject']
    note = ('Coding workflow merged: ' + subject['navigation']['pr_url'] + ' head=' + subject['head']
            + '; manager_result=' + (_reader_reserve(budget, 128) or str(callback['id'])))
    carrier_id = initial['evidence']['facts']['carrier_task_id']
    expected_facts = (_reader_reserve(budget, 704) or dict(format='workflow-coding-return@1', result_id=callback['id'],
        output_digest=_hash(_reader_encode(callback['output_summary'], 'utf8', budget=budget), reader_budget=budget),
        source_task_id=source['task_id'], carrier_task_id=carrier_id))
    evidence = _closed(body['evidence'], 'kind command origin authority rows facts native_task_observations', reader_budget=budget)
    facts = _closed(evidence['facts'], 'format result_id output_digest source_task_id carrier_task_id', reader_budget=budget)
    _check(same(facts, expected_facts, budget=budget) and evidence['kind'] == 'return'
           and _reader_equal(evidence['authority'], initial['evidence']['authority'], budget=budget) and _reader_equal(evidence['rows'], (_reader_reserve(budget, 64) or []), budget=budget))
    internal = (_reader_reserve(budget, 704) or dict(format='workflow-coding-return-command@1', kind='return',
        source_event_ids=(_reader_reserve(budget, 192) or [previous['id']]), source_event_digests=(_reader_reserve(budget, 192) or [previous['event_digest']]), facts=expected_facts))
    _check(same(evidence['command'], internal, budget=budget) and same(evidence['origin'], (_reader_reserve(budget, 832) or dict(kind='settled_result',
        principal=current['principal'], task_id=current['task_id'], session_id=current['session_id'],
        result_id=callback['id'], claim=state['claim'])), budget=budget))
    principal = 'service:coding-dispatcher:' + org
    key = 'coding-internal:' + _hash(_canonical((_reader_reserve(budget, 576) or [run['instance_id'], 'return', callback['id'], internal]), reader_budget=budget), reader_budget=budget)
    operation = (_reader_reserve(budget, 576) or dict(org_slug=org, principal=principal, key=key, request_digest=_hash(_canonical(internal, reader_budget=budget), reader_budget=budget)))
    _check(same(body['operation'], operation, budget=budget) and event['org_slug'] == org and event['principal'] == principal
           and event['operation_key'] == key and event['request_digest'] == operation['request_digest']
           and event['id'] == identity('event', org, run['instance_id'], principal, key, budget=budget))
    def projection(r: dict, t: dict) -> dict:
        return (_reader_reserve(budget, 960) or dict(run=r, attempts=_reader_list(((_reader_reserve(budget, 320) or dict(id=k, record=encode_row(attempts[k], budget=budget))) for k in _reader_sorted(attempts, budget=budget)), budget=budget),
            tasks=_reader_list(((_reader_reserve(budget, 320) or dict(id=k, record=encode_row(t[k], budget=budget))) for k in _reader_sorted(t, budget=budget)), budget=budget),
            instances=initial['after']['instances'], rounds=(_reader_reserve(budget, 64) or []), requests=(_reader_reserve(budget, 64) or []), assignments=(_reader_reserve(budget, 64) or [])))
    for value in (_reader_reserve(budget, 96) or (body['before'], body['after'])):
        _closed(value, 'run attempts instances tasks rounds requests assignments', reader_budget=budget)
    _check(same(body['before'], projection(run, tasks), budget=budget))
    for value in (_reader_reserve(budget, 96) or (run, tasks)):
        _copy_allowance(value, budget)
    target_run, target_tasks = (_reader_reserve(budget, 96) or (_reader_dict(run, budget=budget), _reader_dict(tasks, budget=budget)))
    _reader_update(target_run, (_reader_reserve(budget, 320) or {'cas_version': run['cas_version'] + 1, 'last_event_id': event['id']}), budget=budget)
    for task_id in (_reader_reserve(budget, 96) or (source['task_id'], carrier_id)):
        original = tasks[task_id]
        _check(original['status'] == 'in_progress' and original['block_kind'] == 'delegated'
               and original['cancelled_at'] is None and original['completed_at'] is None)
        _reader_put({**original, 'status': 'completed', 'block_kind': None,
            'completed_at': event['created_at'], 'updated_at': event['created_at'], 'note': note,
            'final_output_summary': callback['output_summary'], 'final_output_dir': callback['output_dir']}, target_tasks, task_id, budget=budget)
    _check(same(body['after'], projection(target_run, target_tasks), budget=budget))
    _reader_put(event['id'], state, 'returned', budget=budget)
    return (_reader_reserve(budget, 112) or (target_run, attempts, target_tasks))


def replay_qa(conn: Any, org: str, budget: Any, storage: Any, *, event: dict, body: dict,
              previous: dict, initial: dict, run: dict, attempts: dict, tasks: dict,
              state: dict, source: dict, roles: dict, mapping: dict, command: dict,
              settled_stages: dict, later_manager_tasks: set[str]) -> tuple:
    """New closed edge; event@3 review_staged retains its unconditional hold."""
    from runtime.workflows.coding_start_history import same
    kind = event['event_kind']
    _check(kind in QA_EDGE_KINDS and body['format'] == 'workflow-coding-event@4')
    evidence = _closed(body['evidence'], 'kind command origin authority rows facts native_task_observations', reader_budget=budget)
    facts = _closed(evidence['facts'], 'format developer_attempt_id reviewer_attempt_id result_id output_digest contributor qa_task_id admission', reader_budget=budget)
    _check(facts['format'] == 'workflow-coding-edge@2' and evidence['kind'] == kind
           and _reader_equal(evidence['authority'], initial['evidence']['authority'], budget=budget) and _reader_equal(evidence['rows'], (_reader_reserve(budget, 64) or []), budget=budget))
    current = attempts[run['current_attempt_id']]
    _check(event['attempt_id'] == current['id'] and run['state'] == current['stage']
           and current['state'] == 'completed' and state['source_settled']
           and state['cleanup_recorded'] and state['cleanup'] and _reader_equal(state['terminal'], 'success', budget=budget)
           and state['claim'] is not None and not state.get('ownership_unknown'))
    callback = state['callback']
    _check(callback is not None and type(callback['output_summary']) is str and len(callback['output_summary']) <= 65536)
    _reader_reserve(budget, 12 * len(callback['output_summary']))
    output = parse_stage_output(callback, instance=run['instance_id'], attempt=current,
        work=command['work'], subject=_read_json(current['scope_bytes'], budget, 1048576)['subject'], budget=budget)
    _decoded(output, budget)
    developer = attempts[identity('attempt', run['instance_id'], 1, budget=budget)]
    reviewer = attempts[identity('attempt', run['instance_id'], 2, budget=budget)]
    _check(developer['stage'] == 'development' and reviewer['stage'] == 'code_review'
           and developer['state'] == reviewer['state'] == 'completed'
           and reviewer['predecessor_id'] == developer['id']
           and len(_reader_set((b['principal'] for b in roles.values()), budget=budget)) == 4
           and developer['principal'] == roles['implementer']['principal']
           and reviewer['principal'] == roles['code_reviewer']['principal']
           and facts['developer_attempt_id'] == developer['id']
           and facts['reviewer_attempt_id'] == reviewer['id']
           and type(facts['result_id']) is int and facts['result_id'] == callback['id']
           and facts['output_digest'] == _hash(_reader_encode(callback['output_summary'], 'utf8', budget=budget), reader_budget=budget)
           and facts['contributor'] == developer['principal'])
    origin = (_reader_reserve(budget, 832) or dict(kind='settled_result', principal=current['principal'], task_id=current['task_id'],
        session_id=current['session_id'], result_id=callback['id'], claim=state['claim']))
    _check(_reader_equal(evidence['origin'], origin, budget=budget))
    internal = (_reader_reserve(budget, 832) or dict(format='workflow-coding-edge-command@2', kind=kind,
        source_event_ids=(_reader_reserve(budget, 192) or [previous['id']]), source_event_digests=(_reader_reserve(budget, 192) or [previous['event_digest']]),
        subject=output['subject'], facts=facts))
    _check(_reader_equal(evidence['command'], internal, budget=budget))
    principal = 'service:coding-dispatcher:' + org
    key = 'coding-internal:' + _hash(_canonical((_reader_reserve(budget, 576) or [run['instance_id'], kind, callback['id'], internal]), reader_budget=budget), reader_budget=budget)
    operation = (_reader_reserve(budget, 576) or dict(org_slug=org, principal=principal, key=key, request_digest=_hash(_canonical(internal, reader_budget=budget), reader_budget=budget)))
    _check(_reader_equal(body['operation'], operation, budget=budget) and event['org_slug'] == org and event['principal'] == principal
           and event['operation_key'] == key and event['request_digest'] == operation['request_digest']
           and event['id'] == identity('event', org, run['instance_id'], principal, key, budget=budget))
    for projection_value in (_reader_reserve(budget, 96) or (body['before'], body['after'])):
        _closed(projection_value, 'run attempts instances tasks rounds requests assignments', reader_budget=budget)
        _check(_reader_equal(projection_value['instances'], initial['after']['instances'], budget=budget)
               and _reader_all_true((_reader_equal(projection_value[k], (_reader_reserve(budget, 64) or []), budget=budget) for k in ('rounds', 'requests', 'assignments')), budget=budget))
    def projection(r: dict, a: dict, t: dict) -> dict:
        return (_reader_reserve(budget, 960) or dict(run=r, attempts=_reader_list(((_reader_reserve(budget, 320) or dict(id=k, record=encode_row(a[k], budget=budget))) for k in _reader_sorted(a, budget=budget)), budget=budget),
            tasks=_reader_list(((_reader_reserve(budget, 320) or dict(id=k, record=encode_row(t[k], budget=budget))) for k in _reader_sorted(t, budget=budget)), budget=budget),
            instances=initial['after']['instances'], rounds=(_reader_reserve(budget, 64) or []), requests=(_reader_reserve(budget, 64) or []), assignments=(_reader_reserve(budget, 64) or [])))
    _check(same(body['before'], projection(run, attempts, tasks), budget=budget))
    target_run, target_attempts, target_tasks = (_reader_reserve(budget, 112) or (_reader_dict(run, budget=budget), _reader_dict(attempts, budget=budget), _reader_dict(tasks, budget=budget)))
    _reader_update(target_run, (_reader_reserve(budget, 320) or {'cas_version': run['cas_version'] + 1, 'last_event_id': event['id']}), budget=budget)
    if kind == 'review_staged' and current['stage'] == 'qa':
        _check(current['stage'] == 'qa' and len(attempts) == 3 and state.get('qa_output') is None
               and facts['qa_task_id'] == current['task_id'] and facts['admission'] is None)
        for upstream, outcome in (_reader_reserve(budget, 96) or (_reader_reserve(budget, 96) or ((developer, 'produced'), _reader_reserve(budget, 96) or (reviewer, 'APPROVE')))):
            native = settled_stages[upstream['id']]
            original = native['callback']
            _check(native['source_settled'] and native['cleanup_recorded'] and native['cleanup']
                   and _reader_equal(native['terminal'], 'success', budget=budget) and original is not None
                   and type(original['output_summary']) is str and len(original['output_summary']) <= 65536)
            _reader_reserve(budget, 12 * len(original['output_summary']))
            prior = parse_stage_output(original, instance=run['instance_id'], attempt=upstream,
                work=command['work'], subject=output['subject'], budget=budget)
            _decoded(prior, budget)
            _check(prior['outcome'] == outcome and prior['findings'] != output['findings'])
        _reader_put(output, state, 'qa_output', budget=budget)
        _reader_put('held', target_run, 'state', budget=budget)
    else:
        _check(current['id'] == reviewer['id'] and output['outcome'] == 'APPROVE'
               and state.get('review_output') is None)
        edge_index = state.get('qa_edge_index', 0)
        _check(edge_index < len(QA_EDGE_KINDS) and kind == QA_EDGE_KINDS[edge_index])
        if edge_index == 0:
            admission(conn, org, budget, storage, facts['admission'], run=run, tasks=tasks,
                source=source, roles=roles, mapping=mapping, command=command,
                future_task_id=facts['qa_task_id'], later_qa_tasks=later_manager_tasks, qa=True)
            _reader_put(facts, state, 'qa_edge_facts', budget=budget)
        else:
            _check(same(facts, state['qa_edge_facts'], budget=budget))
        _reader_put(edge_index + 1, state, 'qa_edge_index', budget=budget)
        if kind == 'stage_queued':
            task_id = facts['qa_task_id']
            _check(type(task_id) is str and task_id.startswith('TASK-') and task_id not in tasks and len(attempts) == 2)
            binding = roles['qa_engineer']
            first = decode_row(initial['after']['attempts'][0]['record'], budget=budget)
            pristine = next(decode_row(v['record'], budget=budget) for v in initial['after']['tasks'] if v['id'] == first['task_id'])
            task = {**pristine, 'id': task_id, 'assigned_agent': binding['principal'], 'team': binding['team'],
                'brief': qa_brief(command['work'], budget=budget), 'created_at': event['created_at'], 'updated_at': event['created_at']}
            scope = _canonical(qa_scope(org=org, instance=run['instance_id'], command=command,
                roles=roles, developer=developer, reviewer=reviewer, subject=output['subject'], task_id=task_id, budget=budget), reader_budget=budget)
            next_attempt = {**first, 'id': identity('attempt', run['instance_id'], 3, budget=budget), 'stage': 'qa',
                'predecessor_id': reviewer['id'], 'task_id': task_id, 'principal': binding['principal'],
                'scope_bytes': scope, 'scope_digest': _hash(scope, reader_budget=budget)}
            _check(next_attempt['id'] not in attempts)
            _reader_put(next_attempt, target_attempts, next_attempt['id'], budget=budget)
            _reader_put(task, target_tasks, task_id, budget=budget)
        if kind == 'advance':
            _check(len(attempts) == 3)
            selected = attempts[identity('attempt', run['instance_id'], 3, budget=budget)]
            _check(selected['task_id'] == facts['qa_task_id'])
            _reader_update(target_run, (_reader_reserve(budget, 448) or {'current_attempt_id': selected['id'], 'current_revision': 1, 'state': 'qa'}), budget=budget)
    _check(same(body['after'], projection(target_run, target_attempts, target_tasks), budget=budget))
    return (_reader_reserve(budget, 112) or (target_run, target_attempts, target_tasks))


def replay_final(conn: Any, org: str, budget: Any, storage: Any, *, event: dict, body: dict,
                 previous: dict, initial: dict, run: dict, attempts: dict, tasks: dict,
                 state: dict, source: dict, roles: dict, mapping: dict, command: dict,
                 settled_stages: dict, future_tasks: set[str]) -> tuple:
    """Independent four-attempt reconstruction; old event@4 QA PASS stays held."""
    from runtime.workflows.coding_start_history import same
    from runtime.workflows.coding_start_records import CodingBoundFinalSelection, CodingFinalSelection, selected_repository
    kind = event['event_kind']
    repaired = body['format'] == 'workflow-coding-event@6'
    _check(body['format'] in ('workflow-coding-event@5', 'workflow-coding-event@6')
           and kind in ('review_staged', 'stage_queued', 'advance', 'manager_completed'))
    evidence = _closed(body['evidence'], 'kind command origin authority rows facts native_task_observations', reader_budget=budget)
    facts = _closed(evidence['facts'], 'format upstream_results result_id output_digest manager_task_id admission', reader_budget=budget)
    _check(facts['format'] == ('workflow-coding-edge@4' if repaired else 'workflow-coding-edge@3') and evidence['kind'] == kind
           and _reader_equal(evidence['authority'], initial['evidence']['authority'], budget=budget) and _reader_equal(evidence['rows'], (_reader_reserve(budget, 64) or []), budget=budget))
    current = attempts[run['current_attempt_id']]
    _check(event['attempt_id'] == current['id'] and run['state'] == current['stage']
           and current['state'] == 'completed' and state['source_settled']
           and state['cleanup_recorded'] and state['cleanup'] and _reader_equal(state['terminal'], 'success', budget=budget)
           and state['claim'] is not None and not state.get('ownership_unknown')
           and len(_reader_set((b['principal'] for b in roles.values()), budget=budget)) == 4)
    pins, results, subject, findings = (_reader_reserve(budget, 128) or (_reader_reserve(budget, 64) or {}, _reader_reserve(budget, 64) or [], None, _reader_set(budget=budget)))
    for index, (stage, outcome, role) in enumerate((('development', 'produced', 'implementer'),
            ('code_review', 'APPROVE', 'code_reviewer'), ('qa', 'PASS', 'qa_engineer')), 1):
        a = attempts[identity('attempt', run['instance_id'], index, budget=budget)]
        n = state if a['id'] == current['id'] else settled_stages[a['id']]
        r = n['callback']
        _check(a['stage'] == stage and a['principal'] == roles[role]['principal']
               and a['state'] == 'completed' and n['source_settled'] and n['cleanup_recorded']
               and n['cleanup'] and _reader_equal(n['terminal'], 'success', budget=budget) and n['claim'] is not None
               and not n.get('ownership_unknown') and r is not None
               and type(r['output_summary']) is str and len(r['output_summary']) <= 65536)
        _reader_reserve(budget, 12 * len(r['output_summary']))
        o = parse_stage_output(r, instance=run['instance_id'], attempt=a, work=command['work'], subject=subject, budget=budget)
        _decoded(o, budget)
        _check(o['outcome'] == outcome and o['findings'] not in findings)
        _reader_add(findings, o['findings'], budget=budget)
        subject = o['subject']
        _reader_put(_reader_reserve(budget, 960) or dict(attempt_id=a['id'], task_id=a['task_id'], session_id=a['session_id'],
            principal=a['principal'], result_id=r['id'], outcome=outcome,
            output_digest=_hash(_reader_encode(r['output_summary'], 'utf8', budget=budget), reader_budget=budget)), pins, stage, budget=budget)
        _reader_append(results, _reader_reserve(budget, 448) or dict(identity=pins[stage], summary=r['output_summary'], verdict=r['verdict']), budget=budget)
    _check(same(facts['upstream_results'], pins, budget=budget))
    callback = state['callback']
    _check(callback is not None and type(callback['output_summary']) is str and len(callback['output_summary']) <= 65536)
    _reader_reserve(budget, 12 * len(callback['output_summary']))
    if kind == 'manager_completed':
        _check(current['stage'] == 'manager_closeout' and len(attempts) == 4
               and state.get('final_output') is None and facts['admission'] is None
               and facts['manager_task_id'] == current['task_id'])
        scope = _read_json(current['scope_bytes'], budget, 1048576)
        _check(scope == final_scope(org=org, instance=run['instance_id'], command=command, roles=roles,
            qa=attempts[identity('attempt', run['instance_id'], 3, budget=budget)], subject=subject,
            task_id=current['task_id'], upstream=pins,
            repository_reference=selected_repository(mapping, command['work'], budget=budget) if repaired else None, budget=budget))
        bound = (_reader_reserve(budget, 320) or dict(repository_reference=selected_repository(mapping, command['work'], budget=budget),
                     workspace=state['workspace'])) if repaired else (_reader_reserve(budget, 64) or {})
        _reader_reserve(budget, 4096)
        selected = _reader_dump(_reader_validate((CodingBoundFinalSelection if repaired else CodingFinalSelection), dict(
            format='workflow-coding-final-selection@2' if repaired else 'workflow-coding-final-selection@1', **bound,
            instance_id=run['instance_id'], attempt_id=current['id'], task_id=current['task_id'],
            session_id=current['session_id'], principal=current['principal'], revision=current['revision'],
            subject=subject, upstream_results=pins, results=results, release=command['release'],
            mapping=command['mapping'], scope_digest=current['scope_digest']), budget=budget), budget=budget)
        _decoded(selected, budget)
        output = parse_final_output(callback, instance=run['instance_id'], attempt=current,
            work=command['work'], subject=subject, upstream=pins, selected=selected, budget=budget)
        _check(output['evidence']['intent']['selected_digest'] == _hash(_canonical(selected, reader_budget=budget), reader_budget=budget)
               and output['findings'] not in findings)
    else:
        _check(current['stage'] == 'qa' and state.get('qa_output') is None)
        output = parse_stage_output(callback, instance=run['instance_id'], attempt=current,
            work=command['work'], subject=subject, budget=budget)
        _check(output['outcome'] == 'PASS')
    _decoded(output, budget)
    _check(type(facts['result_id']) is int and facts['result_id'] == callback['id']
           and facts['output_digest'] == _hash(_reader_encode(callback['output_summary'], 'utf8', budget=budget), reader_budget=budget))
    origin = (_reader_reserve(budget, 832) or dict(kind='settled_result', principal=current['principal'], task_id=current['task_id'],
        session_id=current['session_id'], result_id=callback['id'], claim=state['claim']))
    _check(_reader_equal(evidence['origin'], origin, budget=budget))
    internal = (_reader_reserve(budget, 832) or dict(format='workflow-coding-edge-command@4' if repaired else 'workflow-coding-edge-command@3', kind=kind,
        source_event_ids=(_reader_reserve(budget, 192) or [previous['id']]), source_event_digests=(_reader_reserve(budget, 192) or [previous['event_digest']]), subject=subject, facts=facts))
    _check(_reader_equal(evidence['command'], internal, budget=budget))
    principal = 'service:coding-dispatcher:' + org
    key = 'coding-internal:' + _hash(_canonical((_reader_reserve(budget, 576) or [run['instance_id'], kind, callback['id'], internal]), reader_budget=budget), reader_budget=budget)
    operation = (_reader_reserve(budget, 576) or dict(org_slug=org, principal=principal, key=key, request_digest=_hash(_canonical(internal, reader_budget=budget), reader_budget=budget)))
    _check(_reader_equal(body['operation'], operation, budget=budget) and event['org_slug'] == org and event['principal'] == principal
           and event['operation_key'] == key and event['request_digest'] == operation['request_digest']
           and event['id'] == identity('event', org, run['instance_id'], principal, key, budget=budget))
    def projection(r: dict, a: dict, t: dict) -> dict:
        return (_reader_reserve(budget, 960) or dict(run=r, attempts=_reader_list(((_reader_reserve(budget, 320) or dict(id=k, record=encode_row(a[k], budget=budget))) for k in _reader_sorted(a, budget=budget)), budget=budget),
            tasks=_reader_list(((_reader_reserve(budget, 320) or dict(id=k, record=encode_row(t[k], budget=budget))) for k in _reader_sorted(t, budget=budget)), budget=budget),
            instances=initial['after']['instances'], rounds=(_reader_reserve(budget, 64) or []), requests=(_reader_reserve(budget, 64) or []), assignments=(_reader_reserve(budget, 64) or [])))
    _check(same(body['before'], projection(run, attempts, tasks), budget=budget))
    target_run, target_attempts, target_tasks = (_reader_reserve(budget, 112) or (_reader_dict(run, budget=budget), _reader_dict(attempts, budget=budget), _reader_dict(tasks, budget=budget)))
    _reader_update(target_run, (_reader_reserve(budget, 320) or {'cas_version': run['cas_version'] + 1, 'last_event_id': event['id']}), budget=budget)
    if kind == 'manager_completed':
        _reader_put(output, state, 'final_output', budget=budget)
        _reader_put('complete' if output['outcome'] == 'merged' else 'held', target_run, 'state', budget=budget)
    else:
        index = state.get('final_edge_index', 0)
        _check(index < 3 and kind == QA_EDGE_KINDS[index])
        if index == 0:
            admission(conn, org, budget, storage, facts['admission'], run=run, tasks=tasks,
                source=source, roles=roles, mapping=mapping, command=command,
                future_task_id=facts['manager_task_id'],
                later_qa_tasks=future_tasks - tasks.keys() - _reader_set((_reader_reserve(budget, 80) or (facts['manager_task_id'],)), budget=budget), final=True)
            _reader_put(facts, state, 'final_edge_facts', budget=budget)
        else:
            _check(same(facts, state['final_edge_facts'], budget=budget))
        _reader_put(index + 1, state, 'final_edge_index', budget=budget)
        if kind == 'stage_queued':
            task_id = facts['manager_task_id']
            _check(type(task_id) is str and task_id.startswith('TASK-') and task_id not in tasks and len(attempts) == 3)
            binding = roles['agent_manager']
            first = decode_row(initial['after']['attempts'][0]['record'], budget=budget)
            pristine = next(decode_row(v['record'], budget=budget) for v in initial['after']['tasks'] if v['id'] == first['task_id'])
            task = {**pristine, 'id': task_id, 'assigned_agent': binding['principal'], 'team': binding['team'],
                'brief': final_brief(command['work'], budget=budget), 'created_at': event['created_at'], 'updated_at': event['created_at']}
            scope = _canonical(final_scope(org=org, instance=run['instance_id'], command=command,
                roles=roles, qa=current, subject=subject, task_id=task_id, upstream=pins,
                repository_reference=selected_repository(mapping, command['work'], budget=budget) if repaired else None, budget=budget), reader_budget=budget)
            a = {**first, 'id': identity('attempt', run['instance_id'], 4, budget=budget), 'stage': 'manager_closeout',
                'predecessor_id': current['id'], 'task_id': task_id, 'principal': binding['principal'],
                'scope_bytes': scope, 'scope_digest': _hash(scope, reader_budget=budget)}
            _check(a['id'] not in attempts)
            _reader_put(a, target_attempts, a['id'], budget=budget)
            _reader_put(task, target_tasks, task_id, budget=budget)
        if kind == 'advance':
            _check(len(attempts) == 4)
            selected = attempts[identity('attempt', run['instance_id'], 4, budget=budget)]
            _check(selected['task_id'] == facts['manager_task_id'])
            _reader_update(target_run, (_reader_reserve(budget, 448) or {'current_attempt_id': selected['id'], 'current_revision': 1, 'state': 'manager_closeout'}), budget=budget)
    _check(same(body['after'], projection(target_run, target_attempts, target_tasks), budget=budget))
    return (_reader_reserve(budget, 112) or (target_run, target_attempts, target_tasks))
