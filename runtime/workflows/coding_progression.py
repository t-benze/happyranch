"""Coding-owned ordered edges through final manager; no generic delegate or external effect.

The native outer owner records settlement first. A separate current-authority
transaction reselects that original result and graph. Only committed queued
work is notified, and readiness remains unconditionally unavailable.
"""
from __future__ import annotations

from copy import deepcopy
import json
from datetime import datetime
from typing import Any

from runtime.models import TaskRecord
from runtime.workflows._coding_import_support import _capture
from runtime.workflows._coding_settings_execution import _current_configuration, _read_json
from runtime.workflows.coding_authority_history import _Budget, _decoded
from runtime.workflows.coding_bootstrap import effective_bindings, encode_row, identity, final_brief, final_scope, qa_brief, qa_scope, review_brief, review_scope
from runtime.workflows.coding_saved_records import _canonical, _check, _hash
from runtime.workflows.coding_start import CodingStartError, queued_storage, require_ready
from runtime.workflows.coding_start_history import rows
from runtime.workflows.coding_start_records import effective_mapping, parse_final_output, parse_stage_output, selected_repository


def settled_upstreams(entry: dict, *, budget: _Budget | None = None) -> tuple[dict, list]:
    """Original INTEGER callbacks, successful hosts AND positive native outer closure."""
    pins, results, subject, findings = {}, [], None, set()
    for stage, outcome in (('development', 'produced'), ('code_review', 'APPROVE'), ('qa', 'PASS')):
        attempt = next(a for a in entry['attempts'].values() if a['stage'] == stage)
        native = entry['native']['stages'][attempt['id']]
        result = native['callback']
        _check(attempt['state'] == 'completed' and native['source_settled']
               and native['cleanup_recorded'] and native['cleanup'] and native['terminal'] == 'success'
               and native['claim'] is not None and not native.get('ownership_unknown')
               and result is not None and type(result['output_summary']) is str
               and len(result['output_summary']) <= 65536)
        if budget is not None:
            budget.consumed += 12 * len(result['output_summary'])
            _check(budget.remaining_bytes >= 0)
        output = parse_stage_output(result, instance=entry['run']['instance_id'], attempt=attempt,
            work=entry['command']['work'], subject=subject)
        if budget is not None:
            _decoded(output, budget)
        _check(output['outcome'] == outcome and output['findings'] not in findings)
        subject = output['subject']
        findings.add(output['findings'])
        pins[stage] = dict(attempt_id=attempt['id'], task_id=attempt['task_id'],
            session_id=attempt['session_id'], principal=attempt['principal'], result_id=result['id'],
            outcome=outcome, output_digest=_hash(result['output_summary'].encode('utf8')))
        results.append(dict(identity=pins[stage], summary=result['output_summary'], verdict=result['verdict']))
    return pins, results


def final_selection(entry: dict, *, budget: _Budget | None = None) -> dict:
    from runtime.workflows.coding_start_records import CodingBoundFinalSelection, CodingFinalSelection
    pins, results = settled_upstreams(entry, budget=budget)
    a = entry['attempt']
    if budget is not None:
        budget.consumed += 4 * len(a['scope_bytes'])
        _check(budget.remaining_bytes >= 0)
    scope = json.loads(a['scope_bytes'])
    if budget is not None:
        _decoded(scope, budget)
    repaired = scope['format'] == 'workflow-coding-final-scope@2'
    bound = {}
    if repaired:
        reference = selected_repository(entry['mapping'], entry['command']['work'])
        _check(scope['repository_reference'] == reference and entry['native'].get('workspace') is not None)
        bound = dict(repository_reference=reference, workspace=entry['native']['workspace'])
    selected = (CodingBoundFinalSelection if repaired else CodingFinalSelection).model_validate(dict(
        format='workflow-coding-final-selection@2' if repaired else 'workflow-coding-final-selection@1', **bound,
        instance_id=entry['run']['instance_id'], attempt_id=a['id'], task_id=a['task_id'],
        session_id=a['session_id'], principal=a['principal'], revision=a['revision'],
        subject=scope['subject'], upstream_results=pins, results=results,
        release=entry['command']['release'], mapping=entry['command']['mapping'], scope_digest=a['scope_digest'])).model_dump()
    if budget is not None:
        _decoded(selected, budget)
    return selected


def _settled(entry: dict, *, budget: _Budget | None = None) -> dict | None:
    attempt, native = entry['attempt'], entry['native']
    if (entry['run']['state'] != attempt['stage'] or attempt['state'] != 'completed'
            or native.get('ownership_unknown') or not native['source_settled']
            or not native['cleanup_recorded'] or not native['cleanup'] or native['terminal'] != 'success'):
        return None
    result = native['callback']
    if result is None:
        return None
    scope = None if attempt['stage'] == 'development' else json.loads(attempt['scope_bytes'])
    if budget is not None:
        _check(type(result['output_summary']) is str and len(result['output_summary']) <= 65536)
        budget.consumed += 12 * len(result['output_summary'])
        _check(budget.remaining_bytes >= 0)
    if attempt['stage'] == 'manager_closeout':
        selected = final_selection(entry, budget=budget)
        output = parse_final_output(result, instance=entry['run']['instance_id'], attempt=attempt,
            work=entry['command']['work'], subject=scope['subject'], upstream=selected['upstream_results'], selected=selected)
        _check(output['evidence']['intent']['selected_digest'] == _hash(_canonical(selected)))
        _check(all(json.loads(v['summary'])['findings'] != output['findings'] for v in selected['results']))
        if budget is not None:
            _decoded(output, budget)
        return output
    output = parse_stage_output(result, instance=entry['run']['instance_id'], attempt=attempt,
        work=entry['command']['work'], subject=None if scope is None else scope['subject'])
    if budget is not None:
        _decoded(output, budget)
    if attempt['stage'] == 'qa':
        for stage, outcome in (('development', 'produced'), ('code_review', 'APPROVE')):
            upstream = next(a for a in entry['attempts'].values() if a['stage'] == stage)
            original = native['stages'][upstream['id']]['callback']
            _check(original is not None and type(original['output_summary']) is str
                   and len(original['output_summary']) <= 65536)
            if budget is not None:
                budget.consumed += 12 * len(original['output_summary'])
                _check(budget.remaining_bytes >= 0)
            prior = parse_stage_output(original, instance=entry['run']['instance_id'], attempt=upstream,
                work=entry['command']['work'], subject=output['subject'])
            if budget is not None:
                _decoded(prior, budget)
            _check(prior['outcome'] == outcome and prior['findings'] != output['findings'])
    return output


def _admission(dispatcher: Any, conn: Any, entry: dict, capture: Any, sessions: tuple,
               budget: _Budget) -> dict:
    from runtime.workflows.activation import author_capacity_blocked
    org, db = dispatcher.org, dispatcher.db
    _check(not org._runner_admission_closed and org.orchestrator._db is db)
    org.workflow_authority._validate_admission_uncommitted(conn, capture)
    mapping = entry['mapping']
    current = _read_json(capture.ready.snapshot_bytes, budget)
    _current_configuration(mapping, current, entry['command'], budget)
    roles, source = entry['roles'], entry['source']
    _check(org.teams.teams_for_manager(source['principal']) == (roles['agent_manager']['team'],)
           and len({b['principal'] for b in roles.values()}) == 4)
    root, pause = db._task_pause_store.held_uncommitted(source['task_id'])
    _check(pause is not None and root['id'] == source['root_ancestor_id'] and not pause['held'])
    pending, closure = [source['task_id']], {}
    while pending:
        budget.step()
        key = pending.pop()
        if key in closure:
            continue
        _check(len(closure) < 512)
        selected = list(rows(conn, 'tasks', budget, where='id=?', args=(key,), maximum=1))
        _check(len(selected) == 1)
        row = selected[0]
        closure[key] = row
        db._check_team_move_task_uncommitted(key)
        _check(row['cancelled_at'] is None and row['zombie_flagged_at'] is None
               and row['active_chain'] is None and row['active_fanout'] is None)
        pending.extend(r['id'] for r in rows(conn, 'tasks', budget,
            where='parent_task_id=? OR revisit_of_task_id=?', args=(key, key), maximum=512))
        pending.extend(row[k] for k in ('parent_task_id', 'revisit_of_task_id') if row[k] is not None)
    _check(not any(e['task_id'] in closure for e in pause['journal']['entries']))
    db._check_team_move_workflow_uncommitted(binding_bytes=effective_bindings(entry['run']['instance_id'], roles))
    qa = entry['attempt']['stage'] == 'code_review'
    final = entry['attempt']['stage'] == 'qa'
    reviewer = roles['agent_manager' if final else 'qa_engineer' if qa else 'code_reviewer']['principal']
    _check(entry['attempt']['stage'] in ('development', 'code_review', 'qa')
           and source['budgets']['coding_revision_limit'] >= 1
           and len(entry['attempts']) + 1 <= source['budgets']['coding_attempt_limit']
           and entry['run']['cas_version'] + (3 if qa or final else 4) <= source['budgets']['coding_event_limit'])
    _check(len(sessions) <= 1024)
    if author_capacity_blocked(conn, author=reviewer, task_id=None, active_sessions=sessions):
        raise CodingStartError('coding_capacity_unavailable')
    authority = _capture(capture, identity('edge-admission', entry['run']['instance_id'],
        entry['run']['cas_version']), _canonical(entry['command']))
    pin = authority['pin']
    record = dict(id=authority['admission_record_id'], namespace=pin['namespace'], generation=pin['generation'],
        request_digest=_hash(_canonical(entry['command'])), admitted_by='service:' + dispatcher.producer)
    move = org.orchestrator._team_moves.active_uncommitted()
    result = dict(format='workflow-coding-edge-admission@3' if final else 'workflow-coding-edge-admission@2' if qa else 'workflow-coding-edge-admission@1', authority=authority, record=record,
        tasks=[encode_row(closure[k]) for k in sorted(closure)],
        pause=encode_row({k: v for k, v in pause.items() if k != 'journal'}),
        move=None if move is None else encode_row(move), sessions=[list(s) for s in sessions],
        capacity_tasks=[encode_row(r) for r in rows(conn, 'tasks', budget,
            where="assigned_agent=? AND status='in_progress' AND block_kind IS NULL AND cancelled_at IS NULL AND current_session_id IS NOT NULL",
            args=(reviewer,))],
        work_hours=[encode_row(r) for r in rows(conn, 'work_hours', budget,
            where="agent_name=? AND status='running'", args=(reviewer,))],
        max_revise_rounds=source['budgets']['max_revise_rounds'])
    _check(len(_canonical(result)) <= 4194304)
    conn.execute('INSERT INTO workflow_admission_records VALUES (?,?,?,?,?)', tuple(record.values()))
    return result


def _append(dispatcher: Any, conn: Any, entry: dict, kind: str, facts: dict,
            output: dict, *, now: str, next_attempt: dict | None = None,
            task: TaskRecord | None = None) -> None:
    previous = entry['native']['history'][-1]
    current = entry['attempt']
    qa_edge = facts['format'] == 'workflow-coding-edge@2'
    final_edge = facts['format'] in ('workflow-coding-edge@3', 'workflow-coding-edge@4')
    repaired = facts['format'] == 'workflow-coding-edge@4'
    internal = dict(format='workflow-coding-edge-command@4' if repaired else 'workflow-coding-edge-command@3' if final_edge else 'workflow-coding-edge-command@2' if qa_edge else 'workflow-coding-edge-command@1', kind=kind,
        source_event_ids=[previous['id']], source_event_digests=[previous['event_digest']],
        subject=output['subject'], facts=facts)
    key = 'coding-internal:' + _hash(_canonical([entry['run']['instance_id'], kind, current['result_id'], internal]))
    principal = 'service:' + dispatcher.producer
    event_id = identity('event', dispatcher.org.slug, entry['run']['instance_id'], principal, key)
    before, after = dispatcher._projection(entry), deepcopy(dispatcher._projection(entry))
    after['run'].update(cas_version=entry['run']['cas_version'] + 1, last_event_id=event_id)
    if kind == 'stage_queued':
        _check(task is not None and next_attempt is not None)
        dispatcher.db._insert_task_row_uncommitted(task)
        conn.execute('UPDATE tasks SET created_at=?,updated_at=? WHERE id=?', (now, now, task.id))
        names = ','.join(next_attempt)
        conn.execute(f"INSERT INTO workflow_coding_attempts ({names}) VALUES ({','.join('?' for _ in next_attempt)})", tuple(next_attempt.values()))
        entry['attempts'][next_attempt['id']] = next_attempt
        entry['tasks'][task.id] = dict(conn.execute('SELECT * FROM tasks WHERE id=?', (task.id,)).fetchone())
    if kind == 'advance':
        after['run'].update(state='manager_closeout' if final_edge else 'qa' if qa_edge else 'code_review', current_revision=1,
            current_attempt_id=identity('attempt', entry['run']['instance_id'], 4 if final_edge else 3 if qa_edge else 2))
    if kind == 'review_staged' and not final_edge and (not qa_edge or current['stage'] == 'qa'):
        after['run']['state'] = 'held'
    if kind == 'manager_completed':
        after['run']['state'] = 'complete' if output['outcome'] == 'merged' else 'held'
    after['attempts'] = [dict(id=k, record=encode_row(a)) for k, a in sorted(entry['attempts'].items())]
    after['tasks'] = [dict(id=k, record=encode_row(t)) for k, t in sorted(entry['tasks'].items())]
    operation = dict(org_slug=dispatcher.org.slug, principal=principal, key=key, request_digest=_hash(_canonical(internal)))
    body = dict(format='workflow-coding-event@6' if repaired else 'workflow-coding-event@5' if final_edge else 'workflow-coding-event@4' if qa_edge else 'workflow-coding-event@3', id=event_id, instance_id=entry['run']['instance_id'],
        event_seq=previous['event_seq'] + 1, event_kind=kind, attempt_id=current['id'], operation=operation,
        previous_digest=previous['event_digest'], before=before, after=after, created_at=now,
        evidence=dict(kind=kind, command=internal, origin=dict(kind='settled_result', principal=current['principal'],
            task_id=current['task_id'], session_id=current['session_id'], result_id=current['result_id'], claim=entry['native']['claim']),
            authority=entry['authority'], rows=[], facts=facts,
            native_task_observations=entry['native'].get('pending_observations', [])))
    raw = _canonical(body)
    _check(len(raw) <= 16777216 and body['event_seq'] <= entry['source']['budgets']['coding_event_limit'])
    conn.execute('INSERT INTO workflow_coding_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (event_id, entry['run']['instance_id'], body['event_seq'], current['id'], dispatcher.org.slug, principal,
         key, operation['request_digest'], kind, previous['event_digest'], raw, _hash(raw), now))
    changed = conn.execute('UPDATE workflow_coding_runs SET cas_version=?,current_revision=?,state=?,current_attempt_id=?,last_event_id=? '
        'WHERE instance_id=? AND cas_version=? AND current_attempt_id=?',
        (after['run']['cas_version'], after['run']['current_revision'], after['run']['state'], after['run']['current_attempt_id'],
         event_id, entry['run']['instance_id'], entry['run']['cas_version'], current['id']))
    _check(changed.rowcount == 1)
    entry['run'] = after['run']
    entry['native']['history'].append(dict(id=event_id, event_seq=body['event_seq'], event_digest=_hash(raw)))
    entry['native']['pending_observations'] = []


def advance_in_writer(dispatcher: Any, conn: Any, *, instance: str, result_id: int,
                      capture: Any, sessions: tuple, references: tuple[str, ...]) -> str | None:
    """Only uncommitted native primitives; no await, I/O, enqueue or commit."""
    from runtime.workflows.coding_dispatch import stamp
    _check(conn is dispatcher.db._conn and conn.in_transaction and type(result_id) is int and result_id > 0)
    inventory = dispatcher.org.workflow_coding_start.support._storage(conn, references)
    budget, saved = inventory.budget, inventory.queued
    entry = saved.runs[instance]
    developer = next(a for a in entry['attempts'].values() if a['stage'] == 'development')
    reviewer = next((a for a in entry['attempts'].values() if a['stage'] == 'code_review'), None)
    qa_attempt = next((a for a in entry['attempts'].values() if a['stage'] == 'qa'), None)
    if reviewer is not None and result_id == developer['result_id']:
        return reviewer['task_id']  # Original accepted result/CAS replay, even after QA.
    if qa_attempt is not None and reviewer is not None and result_id == reviewer['result_id']:
        return qa_attempt['task_id']
    manager = next((a for a in entry['attempts'].values() if a['stage'] == 'manager_closeout'), None)
    if manager is not None and qa_attempt is not None and result_id == qa_attempt['result_id']:
        return manager['task_id']
    if entry['attempt']['result_id'] != result_id:
        raise CodingStartError('coding_result_conflict')
    output = _settled(entry, budget=budget)
    if output is None:
        return None
    stage = entry['attempt']['stage']
    qa = stage == 'code_review'
    final = stage == 'qa'
    if stage == 'manager_closeout' or (qa and output['outcome'] != 'APPROVE') or (final and output['outcome'] != 'PASS'):
        return None
    _check(stage in ('development', 'code_review', 'qa'))
    upstream = settled_upstreams(entry, budget=budget)[0] if final else None
    admission = _admission(dispatcher, conn, entry, capture, sessions, budget)
    now = stamp()
    task_id = dispatcher.db.next_task_id()
    binding = entry['roles']['agent_manager' if final else 'qa_engineer' if qa else 'code_reviewer']
    task = TaskRecord(id=task_id, task_type='subtask', parent_task_id=json.loads(entry['event']['event_bytes'])['evidence']['facts']['carrier_task_id'],
        assigned_agent=binding['principal'], team=binding['team'],
        brief=(final_brief if final else qa_brief if qa else review_brief)(entry['command']['work']),
        created_at=datetime.fromisoformat(now.replace('Z', '+00:00')),
        updated_at=datetime.fromisoformat(now.replace('Z', '+00:00')),
        session_timeout_seconds=entry['source']['budgets']['session_timeout_seconds'])
    dispatcher.db._check_team_move_task_uncommitted(task)
    scope = _canonical(final_scope(org=dispatcher.org.slug, instance=instance, command=entry['command'],
        roles=entry['roles'], qa=qa_attempt, subject=output['subject'], task_id=task_id, upstream=upstream,
        repository_reference=selected_repository(entry['mapping'], entry['command']['work']))
        if final else qa_scope(org=dispatcher.org.slug, instance=instance, command=entry['command'],
        roles=entry['roles'], developer=developer, reviewer=reviewer, subject=output['subject'], task_id=task_id)
        if qa else review_scope(org=dispatcher.org.slug, instance=instance, command=entry['command'],
        roles=entry['roles'], developer=developer, subject=output['subject']))
    attempt = dict(id=identity('attempt', instance, 4 if final else 3 if qa else 2), instance_id=instance, revision=1, stage='manager_closeout' if final else 'qa' if qa else 'code_review',
        attempt_sequence=1, assignment_generation=1, predecessor_id=entry['attempt']['id'], task_id=task_id,
        principal=binding['principal'], session_id=None, result_id=None, submission_id=None, review_request_id=None,
        state='queued', scope_bytes=scope, scope_digest=_hash(scope))
    facts = dict(format='workflow-coding-edge@1', developer_attempt_id=developer['id'], result_id=result_id,
        output_digest=_hash(entry['native']['callback']['output_summary'].encode('utf8')),
        contributor=developer['principal'], reviewer_task_id=task_id, admission=admission)
    if qa:
        facts = dict(format='workflow-coding-edge@2', developer_attempt_id=developer['id'],
            reviewer_attempt_id=reviewer['id'], result_id=result_id,
            output_digest=_hash(entry['native']['callback']['output_summary'].encode('utf8')),
            contributor=developer['principal'], qa_task_id=task_id, admission=admission)
    if final:
        facts = dict(format='workflow-coding-edge@4', upstream_results=upstream, result_id=result_id,
            output_digest=_hash(entry['native']['callback']['output_summary'].encode('utf8')),
            manager_task_id=task_id, admission=admission)
    for kind in (('review_staged', 'stage_queued', 'advance') if qa or final else
                 ('artifact_staged', 'contribution_recorded', 'stage_queued', 'advance')):
        _append(dispatcher, conn, entry, kind, facts, output, now=now,
                next_attempt=attempt if kind == 'stage_queued' else None, task=task if kind == 'stage_queued' else None)
    dispatcher.org.workflow_authority._validate_admission_uncommitted(conn, capture)
    # New state is independently reread with a fresh complete allowance after
    # the first mutation; no pre-write inventory is trusted as target closure.
    from runtime.infrastructure.workflow_schema import _read_workflow_schema
    layout, _ = _read_workflow_schema(conn, expected_org_slug=dispatcher.org.slug, budget=budget)
    _check(layout == 'J')
    return task_id


def record_review_in_writer(dispatcher: Any, conn: Any, entry: dict) -> None:
    from runtime.workflows.coding_dispatch import stamp
    output = _settled(entry, budget=entry.get('_budget'))
    if output is None or entry['attempt']['stage'] != 'code_review':
        return
    if output['outcome'] == 'APPROVE':
        return  # Separate current-authority writer owns the new QA edge.
    developer = next(a for a in entry['attempts'].values() if a['stage'] == 'development')
    facts = dict(format='workflow-coding-edge@1', developer_attempt_id=developer['id'],
        result_id=entry['attempt']['result_id'], output_digest=_hash(entry['native']['callback']['output_summary'].encode('utf8')),
        contributor=developer['principal'], reviewer_task_id=entry['attempt']['task_id'], admission=None)
    _append(dispatcher, conn, entry, 'review_staged', facts, output, now=stamp())


def record_qa_in_writer(dispatcher: Any, conn: Any, entry: dict) -> None:
    from runtime.workflows.coding_dispatch import stamp
    output = _settled(entry, budget=entry.get('_budget'))
    if output is None or entry['attempt']['stage'] != 'qa':
        return
    if output['outcome'] == 'PASS':
        return  # New current-authority transaction owns manager allocation.
    developer = next(a for a in entry['attempts'].values() if a['stage'] == 'development')
    reviewer = next(a for a in entry['attempts'].values() if a['stage'] == 'code_review')
    facts = dict(format='workflow-coding-edge@2', developer_attempt_id=developer['id'],
        reviewer_attempt_id=reviewer['id'], result_id=entry['attempt']['result_id'],
        output_digest=_hash(entry['native']['callback']['output_summary'].encode('utf8')),
        contributor=developer['principal'], qa_task_id=entry['attempt']['task_id'], admission=None)
    _append(dispatcher, conn, entry, 'review_staged', facts, output, now=stamp())


def record_final_in_writer(dispatcher: Any, conn: Any, entry: dict) -> None:
    """Record performed agent work only; never remote I/O, CI, merge or retry."""
    from runtime.workflows.coding_dispatch import stamp
    output = _settled(entry, budget=entry.get('_budget'))
    if output is None or entry['attempt']['stage'] != 'manager_closeout':
        return
    facts = dict(format='workflow-coding-edge@4' if output['format'] == 'workflow-coding-final-output@2' else 'workflow-coding-edge@3', upstream_results=output['upstream_results'],
        result_id=entry['attempt']['result_id'],
        output_digest=_hash(entry['native']['callback']['output_summary'].encode('utf8')),
        manager_task_id=entry['attempt']['task_id'], admission=None)
    _append(dispatcher, conn, entry, 'manager_completed', facts, output, now=stamp())



async def reconcile_settled_stage(dispatcher: Any, instance: str) -> str | None:
    require_ready()
    org, db = dispatcher.org, dispatcher.db
    with db._lock:
        entry = queued_storage(db._conn, org.slug).runs[instance]
    if (entry['attempt']['state'] == 'queued' and entry['run']['state'] == entry['attempt']['stage']
            and entry['native']['source_settled'] and entry['native']['claim'] is None
            and entry['native']['host'] is None and not entry['native'].get('ownership_unknown')):
        return entry['attempt']['task_id']
    output = _settled(entry, budget=entry.get('_budget'))
    if (output is None or entry['attempt']['stage'] not in ('development', 'code_review', 'qa')
            or (entry['attempt']['stage'] == 'code_review' and output['outcome'] != 'APPROVE')
            or (entry['attempt']['stage'] == 'qa' and output['outcome'] != 'PASS')):
        return None
    dispatcher._policies(entry)
    references = org.workflow_coding_start.support._schema_references()
    capture = org.workflow_authority.capture_admission()
    async with org.workflow_authority._async_writer_lock:
        dispatcher._policies(entry)
        async with org.teams_lock:
            dispatcher._policies(entry)
            async with org.db_lock:
                dispatcher._policies(entry)
                sessions = tuple(org.sessions.iter_active())
                with org.workflow_authority._admission_ownership(capture):
                    with db._task_pause_store.writer(entry['source']['task_id'], admission=True):
                        return advance_in_writer(dispatcher, db._conn, instance=instance,
                            result_id=entry['attempt']['result_id'], capture=capture, sessions=sessions, references=references)


def reconcile_run(dispatcher: Any, instance: str, queue: Any) -> None:
    try:
        require_ready()
    except CodingStartError:
        return
    from runtime.infrastructure.task_pause_controls import PauseControlError
    from runtime.infrastructure.team_move_store import TeamMoveError
    from runtime.orchestrator.team_move import DeferredTeamMove
    from runtime.workflows.authority import WorkflowAuthorityError
    from runtime.workflows.profile_coordinator import ProfileCoordinatorError
    try:
        task_id = dispatcher._on_loop(reconcile_settled_stage(dispatcher, instance))
    except (ValueError, PauseControlError, TeamMoveError, DeferredTeamMove, WorkflowAuthorityError, ProfileCoordinatorError) as exc:
        # Admission refusal after a committed positive outer record must not
        # rewrite that original settlement as a native teardown failure. The
        # original graph/result remains a drain blocker with its actual cause.
        import logging
        logging.getLogger(__name__).warning('coding progression held: instance=%s cause=%s',
            instance, getattr(exc, 'code', type(exc).__name__))
        return
    if task_id is not None and queue is not None:
        dispatcher.notify_queued(task_id, queue)
