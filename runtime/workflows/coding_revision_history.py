"""SELECT-only reconstruction of original rejection/revision/fresh-gate lineage.

No current authority, remote I/O, writer after-image or per-revision allowance.
"""
from __future__ import annotations

from typing import Any

from runtime.workflows.coding_authority_history import _decoded
from runtime.workflows.coding_bootstrap import decode_row, encode_row, identity
from runtime.workflows.coding_revision import ROLES, attempt_identity, current_stage, independent, returned_findings, revision_brief, revision_scope, scope_allowance, _copy_allowance
from runtime.workflows.coding_saved_records import _canonical, _check, _closed, _hash
from runtime.workflows.coding_start_records import parse_stage_output, parse_final_output, selected_repository


def upstream_pins(attempts: dict, stages: dict, revision: int, instance: str, work: dict,
                  subject: dict, budget: Any, *, through: str = 'qa') -> dict:
    pins, findings = {}, set()
    previous = None
    for stage, outcome in (('development', 'produced'), ('code_review', 'APPROVE'), ('qa', 'PASS')):
        for _ in attempts:
            budget.step()
        a = current_stage(attempts, revision, stage)
        _check(a is not None and a['id'] in stages)
        n = stages[a['id']]
        r = n['callback']
        _check(a['state'] == 'completed' and n['source_settled'] and n['cleanup_recorded'] and n['cleanup']
               and n['terminal'] == 'success' and n['claim'] is not None and not n.get('ownership_unknown')
               and r is not None and type(r['output_summary']) is str and len(r['output_summary']) <= 65536)
        budget.consumed += 12 * len(r['output_summary'])
        _check(budget.remaining_bytes >= 0)
        output = parse_stage_output(r, instance=instance, attempt=a, work=work, subject=subject)
        _decoded(output, budget)
        _check(output['outcome'] == outcome and output['findings'] not in findings
               and (previous is None or a['predecessor_id'] == previous['id']))
        findings.add(output['findings'])
        pins[stage] = dict(attempt_id=a['id'], task_id=a['task_id'], session_id=a['session_id'], principal=a['principal'],
            result_id=r['id'], outcome=outcome, output_digest=_hash(r['output_summary'].encode('utf8')))
        previous = a
        if stage == through:
            break
    return pins


def replay_revision(conn: Any, org: str, budget: Any, storage: Any, *, event: dict, body: dict,
                    previous: dict, initial: dict, run: dict, attempts: dict, tasks: dict, state: dict,
                    source: dict, roles: dict, mapping: dict, command: dict, settled_stages: dict,
                    future_tasks: set[str]) -> tuple:
    from runtime.workflows.coding_start_history import same
    from runtime.workflows.coding_progression_history import admission
    from runtime.workflows.coding_start_records import CodingBoundFinalSelection, CodingRevisionFinalSelection
    from runtime.workflows.coding_revision import manager_no_action, manager_successor_scope
    kind = event['event_kind']
    successor = body['format'] == 'workflow-coding-event@8'
    _check(successor or body['format'] == 'workflow-coding-event@7')
    evidence = _closed(body['evidence'], 'kind command origin authority rows facts native_task_observations')
    facts = _closed(evidence['facts'], 'format source_attempt_id result_id output_digest outcome next_attempt_id next_task_id destination target_revision admission')
    current = attempts[run['current_attempt_id']]
    _check(successor or current['attempt_sequence'] == 1)
    _check(event['attempt_id'] == current['id']
           and (run['state'] == current['stage'] or successor and run['state'] == 'held'
                and kind in ('stage_queued', 'advance') and state.get('manager_retry_hold') is not None)
           and current['state'] == 'completed' and state['source_settled'] and state['cleanup_recorded']
           and state['cleanup'] and state['terminal'] == 'success' and state['claim'] is not None
           and not state.get('ownership_unknown') and evidence['kind'] == kind
           and evidence['authority'] == initial['evidence']['authority'] and evidence['rows'] == [])
    independent(attempts, roles)
    callback = state['callback']
    _check(callback is not None and type(callback['output_summary']) is str and len(callback['output_summary']) <= 65536)
    budget.consumed += 12 * len(callback['output_summary'])
    _check(budget.remaining_bytes >= 0)
    scope = None if current['stage'] == 'development' else _read_scope(current, budget)
    stages = {**settled_stages, current['id']: state}
    if current['stage'] == 'manager_closeout':
        _check((current['revision'] > 1 and not successor and kind == 'manager_completed')
               or successor and kind in ('manager_completed', 'stage_queued', 'advance'))
        subject = scope['subject']
        pins = upstream_pins(attempts, stages, current['revision'], run['instance_id'], command['work'], subject, budget)
        results = [dict(identity=pin, summary=stages[pin['attempt_id']]['callback']['output_summary'],
            verdict=stages[pin['attempt_id']]['callback']['verdict']) for pin in pins.values()]
        selected = (CodingRevisionFinalSelection if current['revision'] > 1 else CodingBoundFinalSelection).model_validate(dict(
            format='workflow-coding-final-selection@3' if current['revision'] > 1 else 'workflow-coding-final-selection@2',
            instance_id=run['instance_id'], attempt_id=current['id'], task_id=current['task_id'], session_id=current['session_id'],
            principal=current['principal'], revision=current['revision'], subject=subject, upstream_results=pins, results=results,
            release=command['release'], mapping=command['mapping'], scope_digest=current['scope_digest'],
            repository_reference=selected_repository(mapping, command['work']), workspace=state['workspace'])).model_dump()
        _decoded(selected, budget)
        output = parse_final_output(callback, instance=run['instance_id'], attempt=current, work=command['work'],
            subject=subject, upstream=pins, selected=selected)
        _check(output['evidence']['intent']['selected_digest'] == _hash(_canonical(selected))
               and all(json_findings(r['summary'], budget) != output['findings'] for r in results))
    else:
        output = parse_stage_output(callback, instance=run['instance_id'], attempt=current, work=command['work'],
            subject=None if scope is None else scope['subject'])
        subject = output['subject']
        if current['stage'] == 'development' and current['revision'] > 1:
            own = _read_scope(current, budget)
            _check(subject['head'] != own['previous_subject']['head'])
        if current['stage'] == 'qa':
            pins = upstream_pins(attempts, stages, current['revision'], run['instance_id'], command['work'], subject, budget, through='code_review')
            _check(all(json_findings(stages[p['attempt_id']]['callback']['output_summary'], budget) != output['findings'] for p in pins.values()))
    _decoded(output, budget)
    rejected = output['outcome'] in ('REQUEST_CHANGES', 'REVISE')
    holds = facts['next_attempt_id'] is None if successor else output['outcome'] == 'BLOCK' or current['stage'] == 'manager_closeout'
    _check(current['revision'] > 1 or rejected or successor)
    if successor:
        _check(current['stage'] == 'manager_closeout'
               and scope['format'] in ('workflow-coding-final-scope@2', 'workflow-coding-final-scope@3', 'workflow-coding-final-scope@4'))
        if not holds:
            _check(manager_no_action(output) and same(state.get('manager_retry_hold'), output)
                   and run['state'] == 'held' and current['attempt_sequence'] == 1)
        else:
            _check(manager_no_action(output) or current['attempt_sequence'] == 2)
    destination = None if holds else 'manager_closeout' if successor else 'development' if rejected else dict(development='code_review', code_review='qa', qa='manager_closeout')[current['stage']]
    revision = current['revision'] + (1 if rejected else 0)
    if destination is not None:
        _check((1 if successor else 2) <= revision <= min(3, source['budgets']['coding_revision_limit']))
    # A sequence is stage-local within this content revision; generation is role-local across revisions.
    retained = {k: a for k, a in attempts.items() if k != facts['next_attempt_id']}
    sequence = 1 + sum(a['revision'] == revision and a['stage'] == destination for a in retained.values())
    generation = 1 + sum(a['stage'] == destination for a in retained.values())
    next_id = None if holds else attempt_identity(run['instance_id'], revision, destination, sequence, generation)
    if successor and not holds:
        _check(sequence == 2 and sequence - 1 <= min(1, source['budgets']['coding_same_principal_retry_limit'])
               and len(retained) + 1 <= min(10, source['budgets']['coding_attempt_limit'])
               and current['principal'] == roles['agent_manager']['principal']
               and output['subject'] == scope['subject'] and pins == scope['upstream_results'])
    expected = dict(format='workflow-coding-edge@6' if successor else 'workflow-coding-edge@5', source_attempt_id=current['id'], result_id=callback['id'],
        output_digest=_hash(callback['output_summary'].encode('utf8')), outcome=output['outcome'], next_attempt_id=next_id,
        next_task_id=facts['next_task_id'], destination=destination, target_revision=revision, admission=facts['admission'])
    _check(same(facts, expected) and type(facts['result_id']) is int)
    origin = dict(kind='settled_result', principal=current['principal'], task_id=current['task_id'], session_id=current['session_id'],
        result_id=callback['id'], claim=state['claim'])
    _check(same(evidence['origin'], origin))
    internal = dict(format='workflow-coding-edge-command@6' if successor else 'workflow-coding-edge-command@5', kind=kind, source_event_ids=[previous['id']],
        source_event_digests=[previous['event_digest']], subject=subject, facts=facts)
    _check(evidence['command'] == internal)
    principal = 'service:coding-dispatcher:' + org
    key = 'coding-internal:' + _hash(_canonical([run['instance_id'], kind, callback['id'], internal]))
    operation = dict(org_slug=org, principal=principal, key=key, request_digest=_hash(_canonical(internal)))
    _check(body['operation'] == operation and event['org_slug'] == org and event['principal'] == principal
           and event['operation_key'] == key and event['request_digest'] == operation['request_digest']
           and event['id'] == identity('event', org, run['instance_id'], principal, key))
    def projection(r: dict, a: dict, t: dict) -> dict:
        return dict(run=r, attempts=[dict(id=k, record=encode_row(a[k])) for k in sorted(a)],
            tasks=[dict(id=k, record=encode_row(t[k])) for k in sorted(t)], instances=initial['after']['instances'],
            rounds=[], requests=[], assignments=[])
    for value in (body['before'], body['after']):
        _closed(value, 'run attempts instances tasks rounds requests assignments')
    _check(same(body['before'], projection(run, attempts, tasks)))
    for value in (run, attempts, tasks):
        _copy_allowance(value, budget)
    target_run, target_attempts, target_tasks = dict(run), dict(attempts), dict(tasks)
    target_run.update(cas_version=run['cas_version'] + 1, last_event_id=event['id'])
    if holds:
        _check(facts['next_task_id'] is facts['admission'] is None and state.get('revision_hold') is None
               and kind == ('manager_completed' if current['stage'] == 'manager_closeout' else 'review_staged'))
        state['revision_hold'] = output
        if current['stage'] == 'manager_closeout':
            state['final_output'] = output
            if successor and manager_no_action(output):
                state['manager_retry_hold'] = output
        target_run['state'] = 'complete' if output['outcome'] == 'merged' else 'held'
    else:
        kinds = ('stage_queued', 'advance') if successor else ('artifact_staged', 'contribution_recorded', 'stage_queued', 'advance') if current['stage'] == 'development' else ('review_staged', 'stage_queued', 'advance')
        index_key = 'manager_edge_index' if successor else 'revision_edge_index'
        index = state.get(index_key, 0)
        _check(index < len(kinds) and kind == kinds[index] and sequence == (2 if successor else 1))
        if index == 0:
            _check(type(facts['next_task_id']) is str and facts['next_task_id'].startswith('TASK-') and facts['next_task_id'] not in tasks)
            admission(conn, org, budget, storage, facts['admission'], run=run, tasks=tasks, source=source, roles=roles,
                mapping=mapping, command=command, future_task_id=facts['next_task_id'],
                later_qa_tasks=future_tasks - tasks.keys() - {facts['next_task_id']},
                qa=current['stage'] == 'code_review', final=current['stage'] == 'qa',
                destination=destination, target=revision, attempt_count=len(attempts), manager_retry=successor)
            state['revision_edge_facts'] = facts
        else:
            _check(same(facts, state['revision_edge_facts']))
        state[index_key] = index + 1
        if not successor:
            state['revision_edge_length'] = len(kinds)
        if kind == 'stage_queued':
            first = decode_row(initial['after']['attempts'][0]['record'])
            pristine = next(decode_row(v['record']) for v in initial['after']['tasks'] if v['id'] == first['task_id'])
            binding = roles[ROLES[destination]]
            task = {**pristine, 'id': facts['next_task_id'], 'assigned_agent': binding['principal'], 'team': binding['team'],
                'brief': revision_brief(command['work'], destination), 'created_at': event['created_at'], 'updated_at': event['created_at']}
            a = {**first, 'id': next_id, 'revision': revision, 'stage': destination, 'attempt_sequence': sequence,
                'assignment_generation': generation, 'predecessor_id': current['id'], 'task_id': task['id'], 'principal': binding['principal']}
            returns = returned_findings(attempts, stages, run['instance_id'], command['work'], budget) if destination == 'development' else []
            upstream = None
            if destination != 'development':
                upstream = upstream_pins(attempts, stages, revision, run['instance_id'], command['work'], subject, budget,
                    through={'code_review': 'development', 'qa': 'code_review', 'manager_closeout': 'qa'}[destination])
            scope_allowance(attempts, command, subject, returns, upstream, budget)
            scope_builder = manager_successor_scope if successor else revision_scope
            scope_args = dict(budget=budget) if successor else dict(returns=returns)
            raw = _canonical(scope_builder(org=org, instance=run['instance_id'], command=command, roles=roles,
                attempt=a, attempts=attempts, subject=subject, upstream=upstream, **scope_args,
                repository_reference=selected_repository(mapping, command['work'])))
            _check(len(raw) <= 1048576 and next_id not in attempts and task['id'] not in tasks)
            a.update(scope_bytes=raw, scope_digest=_hash(raw))
            target_attempts[next_id] = a
            target_tasks[task['id']] = task
        if kind == 'advance':
            a = attempts[next_id]
            _check(a['predecessor_id'] == current['id'] and a['task_id'] == facts['next_task_id'])
            target_run.update(state=destination, current_attempt_id=next_id,
                current_revision=current['revision'] if current['stage'] == 'development' else run['current_revision'])
    _check(same(body['after'], projection(target_run, target_attempts, target_tasks)))
    return target_run, target_attempts, target_tasks


def _read_scope(attempt: dict, budget: Any) -> dict:
    from runtime.workflows._coding_settings_execution import _read_json
    return _read_json(attempt['scope_bytes'], budget, 1048576)


def json_findings(summary: str, budget: Any) -> str:
    from runtime.workflows._coding_settings_execution import _read_json
    return _read_json(summary.encode('utf8'), budget, 65536)['findings']
