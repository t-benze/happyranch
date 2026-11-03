"""Disabled coding revision edges in the caller's synchronous transaction."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from typing import Any

from runtime.models import TaskRecord
from runtime.workflows.coding_bootstrap import encode_row, identity
from runtime.workflows.coding_revision import ROLES, attempt_identity, independent, returned_findings, revision_brief, revision_scope, scope_allowance, _copy_allowance
from runtime.workflows.coding_saved_records import _canonical, _check, _hash
from runtime.workflows.coding_start_records import selected_repository


def append_revision(dispatcher: Any, conn: Any, entry: dict, kind: str, facts: dict, output: dict,
                    now: str, attempt: dict | None = None, task: TaskRecord | None = None, *, budget: Any) -> None:
    current, run = entry['attempt'], entry['run']
    _check(conn is dispatcher.db._conn and conn.in_transaction)
    previous = entry['native']['history'][-1]
    successor = facts['format'] == 'workflow-coding-edge@6'
    if successor:
        _copy_allowance(facts, budget)
        _copy_allowance(output['subject'], budget)
    internal = dict(format='workflow-coding-edge-command@6' if successor else 'workflow-coding-edge-command@5', kind=kind,
        source_event_ids=[previous['id']], source_event_digests=[previous['event_digest']], subject=output['subject'], facts=facts)
    key = 'coding-internal:' + _hash(_canonical([run['instance_id'], kind, current['result_id'], internal]))
    principal = 'service:' + dispatcher.producer
    event_id = identity('event', dispatcher.org.slug, run['instance_id'], principal, key)
    for value in (run, entry['attempts'], entry['tasks'], entry['authority']):
        _copy_allowance(value, budget)
    before, after = dispatcher._projection(entry), deepcopy(dispatcher._projection(entry))
    after['run'].update(cas_version=run['cas_version'] + 1, last_event_id=event_id)
    if kind == 'stage_queued':
        _check(task is not None and attempt is not None)
        dispatcher.db._insert_task_row_uncommitted(task, ownership=('coding_attempt', attempt['id']))
        conn.execute('UPDATE tasks SET created_at=?,updated_at=? WHERE id=?', (now, now, task.id))
        names = ','.join(attempt)
        conn.execute(f"INSERT INTO workflow_coding_attempts ({names}) VALUES ({','.join('?' for _ in attempt)})", tuple(attempt.values()))
        entry['attempts'][attempt['id']] = attempt
        entry['tasks'][task.id] = dict(conn.execute('SELECT * FROM tasks WHERE id=?', (task.id,)).fetchone())
    if kind == 'advance':
        selected = entry['attempts'][facts['next_attempt_id']]
        after['run'].update(state=selected['stage'], current_attempt_id=selected['id'],
            current_revision=current['revision'] if current['stage'] == 'development' else run['current_revision'])
    if facts['next_attempt_id'] is None:
        after['run']['state'] = 'complete' if kind == 'manager_completed' and output['outcome'] == 'merged' else 'held'
    after['attempts'] = [dict(id=k, record=encode_row(a)) for k, a in sorted(entry['attempts'].items())]
    after['tasks'] = [dict(id=k, record=encode_row(t)) for k, t in sorted(entry['tasks'].items())]
    operation = dict(org_slug=dispatcher.org.slug, principal=principal, key=key, request_digest=_hash(_canonical(internal)))
    body = dict(format='workflow-coding-event@8' if successor else 'workflow-coding-event@7', id=event_id, instance_id=run['instance_id'],
        event_seq=previous['event_seq'] + 1, event_kind=kind, attempt_id=current['id'], operation=operation,
        previous_digest=previous['event_digest'], before=before, after=after, created_at=now,
        evidence=dict(kind=kind, command=internal, origin=dict(kind='settled_result', principal=current['principal'],
            task_id=current['task_id'], session_id=current['session_id'], result_id=current['result_id'], claim=entry['native']['claim']),
            authority=entry['authority'], rows=[], facts=facts, native_task_observations=entry['native'].get('pending_observations', [])))
    raw = _canonical(body)
    _check(len(raw) <= 16777216 and body['event_seq'] <= entry['source']['budgets']['coding_event_limit'])
    conn.execute('INSERT INTO workflow_coding_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (event_id, run['instance_id'], body['event_seq'], current['id'], dispatcher.org.slug, principal,
         key, operation['request_digest'], kind, previous['event_digest'], raw, _hash(raw), now))
    changed = conn.execute('UPDATE workflow_coding_runs SET cas_version=?,current_revision=?,state=?,current_attempt_id=?,last_event_id=? '
        'WHERE instance_id=? AND cas_version=? AND current_attempt_id=?',
        (after['run']['cas_version'], after['run']['current_revision'], after['run']['state'], after['run']['current_attempt_id'],
         event_id, run['instance_id'], run['cas_version'], current['id']))
    _check(changed.rowcount == 1)
    entry['run'] = after['run']
    entry['native']['history'].append(dict(id=event_id, event_seq=body['event_seq'], event_digest=_hash(raw)))
    entry['native']['pending_observations'] = []


def facts_for(entry: dict, output: dict, *, attempt: dict | None, admission: dict | None) -> dict:
    return dict(format='workflow-coding-edge@5', source_attempt_id=entry['attempt']['id'],
        result_id=entry['attempt']['result_id'], output_digest=_hash(entry['native']['callback']['output_summary'].encode('utf8')),
        outcome=output['outcome'], next_attempt_id=None if attempt is None else attempt['id'],
        next_task_id=None if attempt is None else attempt['task_id'],
        destination=None if attempt is None else attempt['stage'],
        target_revision=entry['attempt']['revision'] if attempt is None else attempt['revision'], admission=admission)


def advance_revision(dispatcher: Any, conn: Any, entry: dict, result_id: int, capture: Any,
                     sessions: tuple, references: tuple, budget: Any) -> str | None:
    from runtime.workflows.coding_dispatch import stamp
    from runtime.workflows.coding_progression import _admission, _settled, settled_upstreams
    from runtime.workflows.coding_start import CodingStartError
    current = entry['attempt']
    _check(conn.in_transaction)
    if current['result_id'] != result_id:
        raise CodingStartError('coding_result_conflict')
    output = _settled(entry, budget=budget)
    if output is None or current['stage'] == 'manager_closeout' or output['outcome'] == 'BLOCK':
        return None
    rejected = output['outcome'] in ('REQUEST_CHANGES', 'REVISE')
    destination = 'development' if rejected else dict(development='code_review', code_review='qa', qa='manager_closeout')[current['stage']]
    revision = current['revision'] + (1 if rejected else 0)
    _check(2 <= revision <= min(3, entry['source']['budgets']['coding_revision_limit']))
    independent(entry['attempts'], entry['roles'])
    admission = _admission(dispatcher, conn, entry, capture, sessions, budget, destination=destination, target=revision)
    now = stamp()
    task_id = dispatcher.db.next_task_id()
    binding = entry['roles'][ROLES[destination]]
    task = TaskRecord(id=task_id, task_type='subtask',
        parent_task_id=json.loads(entry['event']['event_bytes'])['evidence']['facts']['carrier_task_id'],
        assigned_agent=binding['principal'], team=binding['team'], brief=revision_brief(entry['command']['work'], destination),
        created_at=datetime.fromisoformat(now.replace('Z', '+00:00')), updated_at=datetime.fromisoformat(now.replace('Z', '+00:00')),
        session_timeout_seconds=entry['source']['budgets']['session_timeout_seconds'])
    dispatcher.db._check_team_move_task_uncommitted(task)
    generation = 1 + sum(a['stage'] == destination for a in entry['attempts'].values())
    sequence = 1 + sum(a['stage'] == destination and a['revision'] == revision for a in entry['attempts'].values())
    _check(sequence == 1)
    a = dict(id=attempt_identity(entry['run']['instance_id'], revision, destination, sequence, generation),
        instance_id=entry['run']['instance_id'], revision=revision, stage=destination,
        attempt_sequence=sequence, assignment_generation=generation, predecessor_id=current['id'], task_id=task_id,
        principal=binding['principal'], session_id=None, result_id=None, submission_id=None, review_request_id=None,
        state='queued')
    returns = returned_findings(entry['attempts'], entry['native']['stages'], entry['run']['instance_id'], entry['command']['work'], budget) if destination == 'development' else []
    upstream = settled_upstreams(entry, budget=budget)[0] if destination == 'manager_closeout' else None
    # Stage scopes carry exact own-current upstream identity, with no stale r1 selectors.
    if destination in ('code_review', 'qa'):
        from runtime.workflows.coding_revision_history import upstream_pins
        upstream = upstream_pins(entry['attempts'], entry['native']['stages'], revision, entry['run']['instance_id'],
            entry['command']['work'], output['subject'], budget, through='development' if destination == 'code_review' else 'code_review')
    scope_allowance(entry['attempts'], entry['command'], output['subject'], returns, upstream, budget)
    scope = _canonical(revision_scope(org=dispatcher.org.slug, instance=entry['run']['instance_id'],
        command=entry['command'], roles=entry['roles'], attempt=a, attempts=entry['attempts'], subject=output['subject'],
        returns=returns, upstream=upstream, repository_reference=selected_repository(entry['mapping'], entry['command']['work'])))
    _check(len(scope) <= 1048576)
    a.update(scope_bytes=scope, scope_digest=_hash(scope))
    facts = facts_for(entry, output, attempt=a, admission=admission)
    for kind in (('artifact_staged', 'contribution_recorded', 'stage_queued', 'advance') if current['stage'] == 'development'
                 else ('review_staged', 'stage_queued', 'advance')):
        append_revision(dispatcher, conn, entry, kind, facts, output, now,
            attempt=a if kind == 'stage_queued' else None, task=task if kind == 'stage_queued' else None, budget=budget)
    dispatcher.org.workflow_authority._validate_admission_uncommitted(conn, capture)
    from runtime.infrastructure.workflow_schema import _read_workflow_schema
    _, target = _read_workflow_schema(conn, expected_org_slug=dispatcher.org.slug, budget=budget)
    _check(target is not None)
    return task_id


def hold_revision(dispatcher: Any, conn: Any, entry: dict, output: dict) -> None:
    from runtime.workflows.coding_dispatch import stamp
    kind = 'manager_completed' if entry['attempt']['stage'] == 'manager_closeout' else 'review_staged'
    _check(kind == 'manager_completed' or output['outcome'] == 'BLOCK')
    facts = facts_for(entry, output, attempt=None, admission=None)
    from runtime.workflows.coding_revision import manager_no_action
    if kind == 'manager_completed' and (manager_no_action(output) or entry['attempt']['attempt_sequence'] > 1):
        facts['format'] = 'workflow-coding-edge@6'
    append_revision(dispatcher, conn, entry, kind, facts, output, stamp(), budget=entry['_budget'])


def advance_manager(dispatcher: Any, conn: Any, entry: dict, result_id: int, capture: Any,
                    sessions: tuple, budget: Any) -> str | None:
    """One new actor attempt, never a replay of its predecessor's local intent."""
    from runtime.workflows.coding_dispatch import stamp
    from runtime.workflows.coding_progression import _admission, _settled, settled_upstreams
    from runtime.workflows.coding_revision import manager_no_action, manager_successor_scope
    from runtime.workflows.coding_start import CodingStartError
    current, run = entry['attempt'], entry['run']
    _check(conn.in_transaction)
    if current['result_id'] != result_id:
        raise CodingStartError('coding_result_conflict')
    # The new saved hold marker is derived ONLY from additive event@8. Literal
    # old held records, and unknown native/external ownership, cannot enter.
    if entry['native'].get('manager_retry_hold') is None:
        return None
    output = _settled(entry, budget=budget, manager_retry=True)
    if output is None or not manager_no_action(output):
        return None
    limits = entry['source']['budgets']
    sequence = 1 + sum(a['stage'] == 'manager_closeout' and a['revision'] == current['revision']
                       for a in entry['attempts'].values())
    _check(sequence == 2 and sequence - 1 <= min(1, limits['coding_same_principal_retry_limit'])
           and len(entry['attempts']) + 1 <= min(10, limits['coding_attempt_limit']))
    pins, _ = settled_upstreams(entry, budget=budget)
    from runtime.workflows._coding_settings_execution import _read_json
    original_scope = _read_json(current['scope_bytes'], budget, 1048576)
    _check(output['upstream_results'] == original_scope['upstream_results'] == pins
           and output['subject'] == original_scope['subject']
           and current['principal'] == entry['roles']['agent_manager']['principal'])
    admission = _admission(dispatcher, conn, entry, capture, sessions, budget,
                           destination='manager_closeout', target=current['revision'], manager_retry=True)
    now, task_id = stamp(), dispatcher.db.next_task_id()
    binding = entry['roles']['agent_manager']
    task = TaskRecord(id=task_id, task_type='subtask',
        parent_task_id=json.loads(entry['event']['event_bytes'])['evidence']['facts']['carrier_task_id'],
        assigned_agent=binding['principal'], team=binding['team'], brief=revision_brief(entry['command']['work'], 'manager_closeout'),
        created_at=datetime.fromisoformat(now.replace('Z', '+00:00')), updated_at=datetime.fromisoformat(now.replace('Z', '+00:00')),
        session_timeout_seconds=limits['session_timeout_seconds'])
    dispatcher.db._check_team_move_task_uncommitted(task)
    generation = 1 + sum(a['stage'] == 'manager_closeout' for a in entry['attempts'].values())
    a = dict(id=attempt_identity(run['instance_id'], current['revision'], 'manager_closeout', sequence, generation),
        instance_id=run['instance_id'], revision=current['revision'], stage='manager_closeout',
        attempt_sequence=sequence, assignment_generation=generation, predecessor_id=current['id'], task_id=task_id,
        principal=binding['principal'], session_id=None, result_id=None, submission_id=None, review_request_id=None, state='queued')
    scope_allowance(entry['attempts'], entry['command'], output['subject'], [], pins, budget)
    raw = _canonical(manager_successor_scope(org=dispatcher.org.slug, instance=run['instance_id'],
        command=entry['command'], roles=entry['roles'], attempt=a, attempts=entry['attempts'], subject=output['subject'],
        upstream=pins, repository_reference=selected_repository(entry['mapping'], entry['command']['work']), budget=budget))
    _check(len(raw) <= 1048576)
    a.update(scope_bytes=raw, scope_digest=_hash(raw))
    facts = facts_for(entry, output, attempt=a, admission=admission)
    facts['format'] = 'workflow-coding-edge@6'
    for kind in ('stage_queued', 'advance'):
        append_revision(dispatcher, conn, entry, kind, facts, output, now,
            attempt=a if kind == 'stage_queued' else None, task=task if kind == 'stage_queued' else None, budget=budget)
    dispatcher.org.workflow_authority._validate_admission_uncommitted(conn, capture)
    from runtime.infrastructure.workflow_schema import _read_workflow_schema
    _, target = _read_workflow_schema(conn, expected_org_slug=dispatcher.org.slug, budget=budget)
    _check(target is not None)
    return task_id
