"""Independent closed cancellation replay; no writer after-image is authority."""
from __future__ import annotations

from runtime.infrastructure._workflow_reader import (
    _add as _reader_add,
    _append as _reader_append,
    _extend as _reader_extend,
    _put as _reader_put,
    _setdefault as _reader_setdefault,
    _update as _reader_update,
)

from runtime.infrastructure._workflow_reader import (
    _all_true as _reader_all_true,
)

from runtime.infrastructure._workflow_reader import (
    _dict_pairs as _reader_dict_pairs,
    _set as _reader_set,
    _tuple as _reader_tuple,
)

from runtime.infrastructure._workflow_reader import _equal as _reader_equal

from runtime.infrastructure._workflow_reader import (
    _list as _reader_list,
    _sorted as _reader_sorted,
)

from runtime.infrastructure._workflow_reader import (
    _reserve as _reader_reserve,
)


from runtime.infrastructure._workflow_reader import (
    _copy as _reader_copy,
    _decode as _reader_decode,
    _encode as _reader_encode,
)

from runtime.infrastructure._workflow_reader import (
    _work as _reader_work, _one as _reader_one,
)

from copy import deepcopy
from datetime import datetime
from typing import Any

from runtime.workflows.coding_bootstrap import decode_row, identity
from runtime.workflows.coding_saved_records import _canonical, _check, _closed, _hash, _timestamp
from runtime.workflows._coding_settings_execution import _read_json

TERMINAL = ('completed', 'failed', 'cancelled', 'superseded')


def _returned_event(entry: dict, budget: Any) -> dict:
    """Select only the final return from complete independent native replay.

    A complete label is insufficient. Callers supply the complete reader's
    reconstruction, never a writer after-image or a caller-certified cache.
    """
    native = entry['native']
    _check(entry['run']['state'] == 'complete' and native.get('returned') is not None
           and native.get('cancellation') is None and native['source_settled']
           and not native.get('ownership_unknown') and native['history'])
    event = native['history'][-1]
    _check(event['id'] == native['returned'] == entry['run']['last_event_id']
           and event['event_kind'] == 'return' and event['event_seq'] == entry['run']['cas_version'])
    for state in native['stages'].values():
        budget.step()
        _check(state['source_settled'] and state['cleanup_recorded'] and state['cleanup']
               and _reader_equal(state['terminal'], 'success', budget=budget) and not state.get('ownership_unknown'))
    for task in entry['tasks'].values():
        budget.step()
        _check(task['status'] in TERMINAL)
    return event


def replay(conn: Any, org: str, budget: Any, storage: Any, *, event: dict,
           previous: dict, body: dict, run: dict, attempts: dict, tasks: dict,
           observations: list, source: dict, source_invocation: dict, states: dict) -> tuple:
    from runtime.workflows.coding_start_history import rows, same
    from runtime.workflows.coding_native_history import _row
    _check(type(event['event_bytes']) is bytes and _hash(event['event_bytes'], reader_budget=budget) == event['event_digest'])
    _closed(body, 'format id instance_id event_seq event_kind attempt_id operation previous_digest before after evidence created_at', reader_budget=budget)
    _check(body['format'] in ('workflow-coding-cancellation-event@1', 'workflow-coding-cancellation-event@2')
           and _canonical(body, reader_budget=budget) == event['event_bytes'] and event['event_kind'] == 'cancelled'
           and event['event_seq'] == run['cas_version'] + 1 == previous['event_seq'] + 1
           and event['previous_digest'] == previous['event_digest']
           and event['instance_id'] == run['instance_id'] and event['attempt_id'] == run['current_attempt_id']
           and run['state'] not in ('complete', 'cancelled'))
    for key in ('id', 'instance_id', 'event_seq', 'event_kind', 'attempt_id', 'previous_digest', 'created_at'):
        _check(same(body[key], event[key], budget=budget))
    evidence = _closed(body['evidence'], 'command audits native_task_observations', reader_budget=budget)
    command = _closed(evidence['command'],
        'format target_task_id cascade actor rationale cancelled_at tasks owners native_owners', reader_budget=budget)
    version = body['format'][-1]
    _check(command['format'] == 'workflow-coding-cancellation-command@' + version
           and type(command['cascade']) is bool and type(command['actor']) is str
           and bool(command['actor']) and command['actor'].strip() == command['actor']
           and type(command['rationale']) is str and command['rationale'].strip() == command['rationale']
           and command['cancelled_at'] == event['created_at']
           and same(evidence['native_task_observations'], observations, budget=budget))
    _timestamp(command['cancelled_at'], reader_budget=budget)
    digest = _hash(_canonical(command, reader_budget=budget), reader_budget=budget)
    principal, key = (_reader_reserve(budget, 96) or ('service:coding-cancellation:' + org, 'coding-cancel:' + digest))
    _check(_reader_equal(body['operation'], (_reader_reserve(budget, 576) or dict(org_slug=org, principal=principal, key=key, request_digest=digest)), budget=budget)
           and event['org_slug'] == org and event['principal'] == principal
           and event['operation_key'] == key and event['request_digest'] == digest
           and event['id'] == identity('event', org, run['instance_id'], principal, key, budget=budget))
    _check(type(command['tasks']) is list and 1 <= len(command['tasks']) <= 512)
    originals = (_reader_reserve(budget, 64) or {})
    for item in command['tasks']:
        budget.step()
        _closed(item, 'id record', reader_budget=budget)
        row = _row(item['record'], budget=budget)
        _check(row['id'] == item['id'] and item['id'] not in originals)
        _reader_put(row, originals, item['id'], budget=budget)
    _check(_reader_list(originals, budget=budget) == _reader_sorted(originals, budget=budget) and command['target_task_id'] in originals
           and originals[command['target_task_id']]['status'] not in TERMINAL
           and _reader_all_true((k in originals and same(v, originals[k], budget=budget) for k, v in tasks.items()), budget=budget))
    from runtime.infrastructure.task_pause_controls import decode_journal
    _check(type(command['native_owners']) is list and len(command['native_owners']) <= 512)
    _check(_reader_list((n['id'] for n in command['native_owners']), budget=budget) == _reader_sorted(_reader_set((n['id'] for n in command['native_owners']), budget=budget), budget=budget))
    for native in command['native_owners']:
        budget.step()
        decode_journal(_reader_decode(_canonical((_reader_reserve(budget, 320) or dict(version=1, entries=(_reader_reserve(budget, 192) or [native]))), reader_budget=budget), 'utf8', budget=budget), budget=budget)
        _check(native['org'] == org and native['task_id'] in originals)
        row = originals[native['task_id']]
        _check(native['agent'] == row['assigned_agent'] and
               (row['current_session_id'] is None or native['session_id'] == row['current_session_id']))
        if native['task_id'] in tasks:
            _check(native['root_task_id'] == source['root_ancestor_id'])
            if native['task_id'] == source['task_id']:
                for key in ('id', 'org', 'root_task_id', 'task_id', 'agent', 'session_id', 'owner', 'owner_identity', 'fingerprint', 'generation'):
                    _check(native[key] == source_invocation[key])
            else:
                selected_attempts = _reader_list((a for a in attempts.values() if a['task_id'] == native['task_id']), budget=budget)
                _check(len(selected_attempts) == 1)
                a = selected_attempts[0]
                claim = states[a['id']]['claim']
                _check(native['owner'] == 'coding' and _reader_equal(native['owner_identity'], (_reader_reserve(budget, 64) or {}), budget=budget))
                if claim is not None:
                    _check(native['id'] == claim['invocation_id'] and native['generation'] == claim['pause_generation']
                           and _reader_equal(native['fingerprint'], claim['task_fingerprint'], budget=budget) and native['session_id'] == a['session_id'])
                else:
                    _check(a['state'] == 'queued' and native['phase'] in ('prepared', 'unknown')
                           and _reader_equal(native['fingerprint'], (_reader_reserve(budget, 1216) or dict(status=row['status'], block_kind=row['block_kind'],
                               count=row['orchestration_step_count'], session_id=row['current_session_id'],
                               agent=row['assigned_agent'], fanout=row['active_fanout'], jobs=row['blocked_on_job_ids'],
                               parent=row['parent_task_id'], brief_digest=_hash(_reader_encode(row['brief'], 'utf8', budget=budget), reader_budget=budget))), budget=budget))
    live = _reader_dict_pairs(((_reader_reserve(budget, 96) or (k,  v )) for k, v in originals.items() if v['status'] not in TERMINAL), budget=budget)
    _check(live and type(evidence['audits']) is list and len(evidence['audits']) == len(live))
    audit_tasks = (_reader_reserve(budget, 64) or [])
    for item in evidence['audits']:
        budget.step()
        _closed(item, 'table id record', reader_budget=budget)
        audit = decode_row(item['record'], budget=budget)
        found = _reader_list(rows(conn, 'audit_log', budget, where='id=?', args=(_reader_reserve(budget, 80) or (item['id'],)), maximum=1), budget=budget)
        _check(item['table'] == 'audit_log' and len(found) == 1 and same(found[0], audit, budget=budget)
               and type(item['id']) is int and item['id'] > 0 and item['id'] == audit['id']
               and audit['action'] == 'task_cancelled' and audit['agent'] == command['actor']
               and audit['task_id'] in live)
        native_payload = _closed(_read_json(_reader_encode(audit['payload'], 'utf8', budget=budget), budget, 1048576), 'rationale cascade', reader_budget=budget)
        _check(same(native_payload, (_reader_reserve(budget, 320) or dict(rationale=command['rationale'], cascade=command['cascade'])), budget=budget))
        from runtime.workflows.coding_source_history import _timestamp as native_timestamp
        native_timestamp(audit['timestamp'])
        _reader_append(audit_tasks, audit['task_id'], budget=budget)
        storage.add('audit_log', audit, budget=budget)
    _check(audit_tasks == _reader_sorted(live, budget=budget))
    if version == '2':
        ids = _reader_list((a['id'] for a in evidence['audits']), budget=budget)
        _check(ids == _reader_sorted(_reader_set(ids, budget=budget), budget=budget) and command['cascade'] is True)
    note = ('cancelled by ' + command['actor'] + (': ' + command['rationale'] if command['rationale'] else ''))
    from runtime.workflows.coding_revision import _copy_allowance
    _copy_allowance((_reader_reserve(budget, 320) or dict(tasks=tasks, attempts=attempts)), budget)
    expected_tasks = _reader_copy(tasks, budget=budget)
    for task_id in expected_tasks:
        if task_id in live:
            _reader_update(expected_tasks[task_id], (_reader_reserve(budget, 832) or {'status': 'cancelled', 'block_kind': None, 'note': note, 'cancelled_at': command['cancelled_at'], 'completed_at': command['cancelled_at'], 'updated_at': command['cancelled_at']}), budget=budget)
    expected_attempts = _reader_copy(attempts, budget=budget)
    for a in expected_attempts.values():
        if a['task_id'] in live:
            _reader_put('cancelled', a, 'state', budget=budget)
    expected_run = {**run, 'state': 'cancelled', 'cas_version': event['event_seq'], 'last_event_id': event['id']}
    _check(same(body['before'], (_reader_reserve(budget, 320) or dict(cas_version=run['cas_version'], last_event_id=previous['id'])), budget=budget)
           and same(body['after'], (_reader_reserve(budget, 192) or dict(state='cancelled')), budget=budget))
    record = (_reader_reserve(budget, 576) or dict(command=command, audits=evidence['audits'], originals=originals, event=event))
    _reader_put(record, _reader_setdefault(storage.cancellations, digest, _reader_reserve(budget, 64) or {}, budget=budget), run['instance_id'], budget=budget)
    storage.add('workflow_coding_events', event, budget=budget)
    return (_reader_reserve(budget, 128) or (expected_run, expected_attempts, expected_tasks, record))


def ordinary_overlay(org: str, command: dict, audits: list, saved: Any, instances: set, budget: Any) -> dict:
    """Distinct ordinary operation; never a second coding cancellation event."""
    digest = _hash(_canonical(command, reader_budget=budget), reader_budget=budget)
    anchors = (_reader_reserve(budget, 64) or [])
    for instance in _reader_sorted(instances, budget=budget):
        budget.step()
        entry = saved.runs[instance]
        event = (_returned_event(entry, budget) if entry['run']['state'] == 'complete'
                 else entry['native']['cancellation']['event'])
        _reader_append(anchors, _reader_reserve(budget, 448) or dict(instance_id=instance, event_id=event['id'], event_digest=event['event_digest']), budget=budget)
    return (_reader_reserve(budget, 704) or dict(format='workflow-coding-ordinary-cancellation@' + command['format'][-1],
        operation=(_reader_reserve(budget, 576) or dict(org_slug=org, principal='service:coding-cancellation:' + org,
            key='coding-ordinary-cancel:' + digest, request_digest=digest)),
        command=command, audits=audits,
        retained=anchors))


def _ordinary_batches(conn: Any, org: str, budget: Any, storage: Any) -> list:
    """Independently authenticate all ordinary overlays, including orphan rows.

    Existing literal @1 retained anchors admit only cancelled graphs. Additive @2 requires at least one
    independently returned graph, optionally alongside earlier cancellations.
    """
    from runtime.workflows.coding_start_history import rows, same
    from runtime.workflows.coding_native_history import _row
    from runtime.workflows.coding_source_history import _timestamp as native_timestamp
    from runtime.infrastructure.task_pause_controls import decode_journal
    batches, operations = (_reader_reserve(budget, 96) or (_reader_reserve(budget, 64) or [], _reader_set(budget=budget)))
    for audit in rows(conn, 'audit_log', budget, where="action='coding_ordinary_cancellation'"):
        _check(type(audit['payload']) is str)
        value = _closed(_read_json(_reader_encode(audit['payload'], 'utf8', budget=budget), budget, 1048576),
            'format operation command audits retained', reader_budget=budget)
        _check(value['format'] in ('workflow-coding-ordinary-cancellation@1', 'workflow-coding-ordinary-cancellation@2'))
        version = value['format'][-1]
        command = _closed(value['command'],
            'format target_task_id cascade actor rationale cancelled_at tasks owners native_owners', reader_budget=budget)
        _check(command['format'] == 'workflow-coding-cancellation-command@' + version
               and command['cascade'] is True and type(command['actor']) is str
               and bool(command['actor']) and command['actor'].strip() == command['actor']
               and type(command['rationale']) is str and command['rationale'].strip() == command['rationale'])
        _timestamp(command['cancelled_at'], reader_budget=budget)
        digest = _hash(_canonical(command, reader_budget=budget), reader_budget=budget)
        _check(same(value['operation'], (_reader_reserve(budget, 576) or dict(org_slug=org, principal='service:coding-cancellation:' + org,
                   key='coding-ordinary-cancel:' + digest, request_digest=digest)), budget=budget)
               and digest not in operations and audit['agent'] == command['actor']
               and audit['task_id'] == command['target_task_id'] and type(audit['id']) is int and audit['id'] > 0)
        native_timestamp(audit['timestamp'])
        _reader_add(operations, digest, budget=budget)
        _check(type(command['tasks']) is list and 1 <= len(command['tasks']) <= 512)
        originals = (_reader_reserve(budget, 64) or {})
        for item in command['tasks']:
            budget.step()
            _closed(item, 'id record', reader_budget=budget)
            row = _row(item['record'], budget=budget)
            _check(row['id'] == item['id'] and item['id'] not in originals)
            _reader_put(row, originals, item['id'], budget=budget)
        _check(_reader_list(originals, budget=budget) == _reader_sorted(originals, budget=budget) and command['target_task_id'] in originals
               and command['target_task_id'] not in storage.owners
               and originals[command['target_task_id']]['status'] not in TERMINAL)
        live = _reader_dict_pairs(((_reader_reserve(budget, 96) or (k,  v )) for k, v in originals.items() if v['status'] not in TERMINAL), budget=budget)
        _check(live and not (live.keys() & storage.owners.keys())
               and type(value['audits']) is list and len(value['audits']) == len(live))
        tasks, ids = (_reader_reserve(budget, 96) or (_reader_reserve(budget, 64) or [], _reader_reserve(budget, 64) or []))
        for item in value['audits']:
            budget.step()
            _closed(item, 'table id record', reader_budget=budget)
            original = decode_row(item['record'], budget=budget)
            found = _reader_list(rows(conn, 'audit_log', budget, where='id=?', args=(_reader_reserve(budget, 80) or (item['id'],)), maximum=1), budget=budget)
            _check(item['table'] == 'audit_log' and type(item['id']) is int and 0 < item['id'] < audit['id']
                   and len(found) == 1 and same(found[0], original, budget=budget) and original['id'] == item['id']
                   and original['action'] == 'task_cancelled' and original['agent'] == command['actor']
                   and original['task_id'] in live and type(original['payload']) is str)
            native_timestamp(original['timestamp'])
            payload = _closed(_read_json(_reader_encode(original['payload'], 'utf8', budget=budget), budget, 1048576), 'rationale cascade', reader_budget=budget)
            _check(same(payload, (_reader_reserve(budget, 320) or dict(rationale=command['rationale'], cascade=True)), budget=budget))
            _reader_append(tasks, original['task_id'], budget=budget)
            _reader_append(ids, item['id'], budget=budget)
            storage.add('audit_log', original, budget=budget)
        _check(tasks == _reader_sorted(live, budget=budget) and ids == _reader_sorted(_reader_set(ids, budget=budget), budget=budget))
        _check(type(command['native_owners']) is list and len(command['native_owners']) <= 512)
        _check(_reader_list((n['id'] for n in command['native_owners']), budget=budget) == _reader_sorted(_reader_set((n['id'] for n in command['native_owners']), budget=budget), budget=budget))
        for native in command['native_owners']:
            budget.step()
            decode_journal(_reader_decode(_canonical((_reader_reserve(budget, 320) or dict(version=1, entries=(_reader_reserve(budget, 192) or [native]))), reader_budget=budget), 'utf8', budget=budget), budget=budget)
            _check(native['org'] == org and native['task_id'] in originals)
            row = originals[native['task_id']]
            _check(native['agent'] == row['assigned_agent']
                   and (row['current_session_id'] is None or native['session_id'] == row['current_session_id']))
            owner = storage.owners.get(native['task_id'])
            if owner is not None:
                earlier = storage.runs[owner[1]]['native'].get('cancellation')
                _check(earlier is not None)
                original = _reader_list((n for n in earlier['command']['native_owners'] if n['id'] == native['id']), budget=budget)
                _check(len(original) == 1)
                for key in ('id', 'org', 'root_task_id', 'task_id', 'agent', 'session_id',
                            'owner', 'owner_identity', 'fingerprint', 'generation'):
                    _check(same(native[key], original[0][key], budget=budget))
        _check(type(value['retained']) is list and 1 <= len(value['retained']) <= 512)
        instances, returned = (_reader_reserve(budget, 96) or (_reader_reserve(budget, 64) or [], _reader_set(budget=budget)))
        for anchor in value['retained']:
            budget.step()
            _closed(anchor, 'instance_id event_id event_digest', reader_budget=budget)
            instance = anchor['instance_id']
            _check(instance in storage.runs)
            entry = storage.runs[instance]
            if version == '2' and entry['run']['state'] == 'complete':
                event = _returned_event(entry, budget)
                _reader_add(returned, instance, budget=budget)
            else:
                _check(entry['run']['state'] == 'cancelled')
                earlier = entry['native'].get('cancellation')
                _check(earlier is not None and max(a['id'] for a in earlier['audits']) < min(ids))
                event = earlier['event']
            _check(anchor['event_id'] == event['id'] and anchor['event_digest'] == event['event_digest'])
            _reader_append(instances, instance, budget=budget)
        _check(instances == _reader_sorted(_reader_set(instances, budget=budget), budget=budget) and bool(returned) is (version == '2'))
        storage.add('audit_log', audit, budget=budget)
        _reader_append(batches, _reader_reserve(budget, 704) or dict(command=command, audits=value['audits'], originals=originals,
                            retained=_reader_set(instances, budget=budget), capture_audit=audit), budget=budget)
    return batches


def native_checkpoint(history: list, event: dict | None, tasks: dict,
                      observations: list, budget: Any) -> None:
    """Retain reader-derived rows only, after closed native replay succeeds.

    Each copy and its traversal consume the enclosing original allowance.
    Nothing here is persisted or accepted from a writer/caller projection.
    """
    from runtime.workflows.coding_revision import _copy_allowance
    point = (_reader_reserve(budget, 448) or dict(event=None if event is None else (_reader_reserve(budget, 704) or dict(id=event['id'],
        event_seq=event['event_seq'], event_digest=event['event_digest'],
        previous_digest=event['previous_digest'], created_at=event['created_at'])),
        tasks=tasks, observations=observations))
    _copy_allowance(point, budget)
    _reader_append(history, _reader_copy(point, budget=budget), budget=budget)


def retained_boundary(conn: Any, entry: dict, sample: dict, budget: Any) -> dict:
    """Compose an authenticated later cancellation with the entire native tail.

    The boundary comes from the already authenticated cancellation operation,
    never a caller cutoff or its captured task rows. Event-chain order, native
    audit IDs and exact heartbeat replay must agree with the operation time.
    All suffix events were independently replayed by read_native; no suffix or
    mutable field is exempt from that reader or the final current-SQL equality.
    """
    from runtime.workflows.coding_start_history import rows, same
    from runtime.workflows.coding_native_history import _row
    from runtime.workflows.coding_revision import _copy_allowance
    if entry['run']['state'] == 'complete':
        return _returned_boundary(conn, entry, sample, budget)
    earlier = entry['native']['cancellation']
    history = entry['native']['cancellation_history']
    _check(history and history[0]['event']['id'] == earlier['event']['id']
           and history[0]['event']['event_digest'] == earlier['event']['event_digest'])
    first = min(a['id'] for a in sample['audits'])
    last = max(a['id'] for a in sample['audits'])
    prior_audit = max(a['id'] for a in earlier['audits'])
    _check(prior_audit < first)
    boundary = datetime.fromisoformat(sample['command']['cancelled_at'].replace('Z', '+00:00'))
    capture_end = boundary
    for item in sample['audits']:
        budget.step()
        _copy_allowance(item['record'], budget)
        audit = decode_row(item['record'], budget=budget)
        instant = datetime.fromisoformat(audit['timestamp'])
        _check(instant >= boundary)
        capture_end = max(capture_end, instant)
    if 'capture_audit' in sample:
        instant = datetime.fromisoformat(sample['capture_audit']['timestamp'])
        _check(instant >= capture_end)
        capture_end = instant
    _copy_allowance(history[0]['tasks'], budget)
    current = _reader_copy(history[0]['tasks'], budget=budget)
    selected = None
    crossed = False
    previous = None
    previous_time = None
    for point in history:
        budget.step()
        # fold_heartbeats authenticates each per-task chain. Merge their audit
        # order only after charging the intermediate traversal/allocation.
        audits = (_reader_reserve(budget, 64) or [])
        for observation in point['observations']:
            budget.step()
            for audit in observation['records']:
                budget.step()
                _reader_append(audits, audit, budget=budget)
        _copy_allowance(audits, budget)
        for audit in _reader_sorted(audits, key=lambda a: a['id'], budget=budget):
            budget.step()
            _check(audit['id'] > prior_audit and not first <= audit['id'] <= last)
            prior_audit = audit['id']
            before_boundary = audit['id'] < first
            instant = datetime.fromisoformat(audit['timestamp'])
            _check(instant < boundary if before_boundary else instant > capture_end)
            _check(not crossed or not before_boundary)
            if not before_boundary and not crossed:
                _copy_allowance(current, budget)
                selected, crossed = (_reader_reserve(budget, 96) or (_reader_copy(current, budget=budget), True))
            body = _read_json(_reader_encode(audit['payload'], 'utf8', budget=budget), budget, 1048576)
            before = _row(body['before_record'], budget=budget)
            _copy_allowance(before, budget)
            after = {**before, 'last_heartbeat': body['observed_at'], 'updated_at': body['observed_at']}
            _check(before['id'] in current and same(before, current[before['id']], budget=budget)
                   and same(after, _row(body['after_record'], budget=budget), budget=budget))
            _copy_allowance(after, budget)
            _reader_put(after, current, before['id'], budget=budget)
        event = point['event']
        if event is not None:
            instant = datetime.fromisoformat(event['created_at'].replace('Z', '+00:00'))
            _check((instant < boundary or instant > capture_end)
                   and (previous_time is None or instant >= previous_time))
            if previous is not None:
                _check(event['event_seq'] == previous['event_seq'] + 1
                       and event['previous_digest'] == previous['event_digest'])
            before_boundary = instant < boundary
            _check(not crossed or not before_boundary)
            # A prefix event cannot consume a suffix heartbeat. Conversely,
            # heartbeat audit order may locate the boundary before this event.
            _check(not before_boundary or prior_audit < first)
            if not before_boundary and not crossed:
                _copy_allowance(current, budget)
                selected, crossed = (_reader_reserve(budget, 96) or (_reader_copy(current, budget=budget), True))
            previous, previous_time = (_reader_reserve(budget, 96) or (event, instant))
            _copy_allowance(point['tasks'], budget)
            current = _reader_copy(point['tasks'], budget=budget)
        else:
            _check(same(current, point['tasks'], budget=budget))
    _check(previous['id'] == entry['native']['history'][-1]['id']
           and same(current, entry['tasks'], budget=budget))
    for key, row in current.items():
        budget.step()
        actual = _reader_list(rows(conn, 'tasks', budget, where='id=?', args=(_reader_reserve(budget, 80) or (key,)), maximum=1), budget=budget)
        _check(len(actual) == 1 and same(actual[0], row, budget=budget))
    return current if selected is None else selected


def _returned_boundary(conn: Any, entry: dict, sample: dict, budget: Any) -> dict:
    """A returned graph has no suffix writer; authenticate its prior ordering.

    Native replay already derives every task and all return/native evidence.
    Check the immutable event chain and original native audit IDs against this
    later capture, then reread full SQL rows, including executor_pid.
    """
    from runtime.workflows.coding_start_history import rows, same
    _check(sample['command']['format'] == 'workflow-coding-cancellation-command@2')
    event = _returned_event(entry, budget)
    end = datetime.fromisoformat(event['created_at'].replace('Z', '+00:00'))
    boundary = datetime.fromisoformat(sample['command']['cancelled_at'].replace('Z', '+00:00'))
    _check(end < boundary)
    first = min(a['id'] for a in sample['audits'])
    prior = None
    for original in entry['native']['history']:
        budget.step()
        instant = datetime.fromisoformat(original['created_at'].replace('Z', '+00:00'))
        _check(instant <= end and (prior is None or instant >= prior))
        prior = instant
    capture_end = boundary
    for item in sample['audits']:
        budget.step()
        audit = decode_row(item['record'], budget=budget)
        instant = datetime.fromisoformat(audit['timestamp'])
        _check(instant >= boundary)
        capture_end = max(capture_end, instant)
    if 'capture_audit' in sample:
        instant = datetime.fromisoformat(sample['capture_audit']['timestamp'])
        _check(instant >= capture_end)
    for key, task in entry['tasks'].items():
        budget.step()
        # These literal native actions were independently read by the source,
        # stage and heartbeat readers. No unrelated audit action is redefined.
        for audit in rows(conn, 'audit_log', budget,
                where="task_id=? AND action IN ('session_start','coding_task_heartbeat')", args=(_reader_reserve(budget, 80) or (key,))):
            budget.step()
            _check(audit['id'] < first and datetime.fromisoformat(audit['timestamp']) <= end)
        actual = _reader_list(rows(conn, 'tasks', budget, where='id=?', args=(_reader_reserve(budget, 80) or (key,)), maximum=1), budget=budget)
        _check(len(actual) == 1 and same(actual[0], task, budget=budget))
    return entry['tasks']


def complete(conn: Any, org: str, budget: Any, storage: Any) -> None:
    """Cross-graph/legacy closure, including partial and orphan batch refusal."""
    from runtime.workflows.coding_start_history import rows, same
    ordinary = _ordinary_batches(conn, org, budget, storage)
    batches = _reader_list(((_reader_reserve(budget, 96) or (records, next(iter(records.values())))) for records in storage.cancellations.values()), budget=budget)
    _reader_extend(batches, ((_reader_reserve(budget, 96) or ((_reader_reserve(budget, 64) or {}), record)) for record in ordinary), budget=budget)
    for records, sample in batches:
        command, originals = (_reader_reserve(budget, 96) or (sample['command'], sample['originals']))
        pending, retained, visited, instances = (_reader_reserve(budget, 128) or (_reader_reserve(budget, 192) or [command['target_task_id']], _reader_reserve(budget, 64) or [], _reader_set(budget=budget), _reader_set(budget=budget)))
        returned = _reader_set(budget=budget)
        while pending or retained:
            budget.step()
            retaining = not pending
            key = retained.pop() if retaining else pending.pop()
            if key in visited:
                continue
            _check(key in originals and len(visited) < 512)
            _reader_add(visited, key, budget=budget)
            row = originals[key]
            _check(not retaining or row['status'] in TERMINAL)
            owner = storage.owners.get(key)
            if owner is not None and owner[1] not in instances:
                # A terminal ordinary path can retain a genuine earlier fence,
                # never introduce a fresh live graph into this operation.
                _check(not retaining or owner[1] not in records)
                _reader_add(instances, owner[1], budget=budget)
                entry = storage.runs[owner[1]]
                if owner[1] not in records:
                    # A later ancestor stop retains this earlier terminal
                    # graph byte-for-byte; no second event/control is owed.
                    if entry['run']['state'] == 'complete':
                        _check(command['format'] == 'workflow-coding-cancellation-command@2')
                        _returned_event(entry, budget)
                        _reader_add(returned, owner[1], budget=budget)
                    else:
                        earlier = entry['native'].get('cancellation')
                        _check(earlier is not None and entry['run']['state'] == 'cancelled'
                               and max(a['id'] for a in earlier['audits']) < min(a['id'] for a in sample['audits']))
                    historical = retained_boundary(conn, entry, sample, budget)
                    for task_id, task in historical.items():
                        budget.step()
                        _check(task['status'] in TERMINAL and task_id in originals
                               and same(task, originals[task_id], budget=budget))
                _reader_extend(retained if retaining else pending, entry['tasks'], budget=budget)
            if command['cascade'] and row['status'] not in TERMINAL:
                _reader_extend(pending, (r['id'] for r in rows(conn, 'tasks', budget,
                    where='parent_task_id=?', args=(_reader_reserve(budget, 80) or (key,)), maximum=512)), budget=budget)
            elif command['cascade'] and owner is None and key in storage.cancellation_dependencies:
                # Recompute the retained census independently, using original
                # terminal rows and the complete authenticated dependency set.
                for child in rows(conn, 'tasks', budget,
                        where='parent_task_id=?', args=(_reader_reserve(budget, 80) or (key,)), maximum=512):
                    budget.step()
                    if child['id'] in storage.cancellation_dependencies or child['id'] in storage.owners:
                        _reader_append(retained, child['id'], budget=budget)
        _check(visited == _reader_set(originals, budget=budget) and _reader_set(records, budget=budget) <= instances
               and bool(returned) is (command['format'] == 'workflow-coding-cancellation-command@2'))
        if returned:
            for native in command['native_owners']:
                budget.step()
                owner = storage.owners.get(native['task_id'])
                _check(owner is None or owner[1] not in returned)
        if 'retained' in sample:
            _check(instances == sample['retained'])
        expected_owners = _reader_list(((_reader_reserve(budget, 448) or dict(task_id=k, kind=v[0], instance_id=v[1]))
            for k, v in _reader_sorted(storage.owners.items(), budget=budget) if v[1] in instances), budget=budget)
        _check(same(command['owners'], expected_owners, budget=budget))
        for record in records.values():
            _check(same(command, record['command'], budget=budget) and same(sample['audits'], record['audits'], budget=budget))
        # Complete saved coding readers already check all mutable coding rows.
        # Legacy members must still equal their own exact cancellation overlay.
        note = 'cancelled by ' + command['actor'] + (': ' + command['rationale'] if command['rationale'] else '')
        for key, row in originals.items():
            budget.step()
            if key in storage.owners:
                continue
            if row['status'] not in TERMINAL:
                row = {**row, 'status': 'cancelled', 'block_kind': None, 'note': note,
                    'cancelled_at': command['cancelled_at'], 'completed_at': command['cancelled_at'],
                    'updated_at': command['cancelled_at']}
            found = _reader_list(rows(conn, 'tasks', budget, where='id=?', args=(_reader_reserve(budget, 80) or (key,)), maximum=1), budget=budget)
            _check(len(found) == 1 and same(found[0], row, budget=budget))

    # No row may receive two independent live-to-cancelled overlays. Replaying
    # multiple graph events for ONE batch is distinct from another batch.
    overlays = (_reader_reserve(budget, 64) or {})
    for _, record in batches:
        for key, original in record['originals'].items():
            budget.step()
            if original['status'] not in TERMINAL:
                _check(key not in overlays)
                _reader_put(record, overlays, key, budget=budget)
    for historical in storage.cancellation_comparisons:
        budget.step()
        found = _reader_list(rows(conn, 'tasks', budget, where='id=?', args=(_reader_reserve(budget, 80) or (historical['id'],)), maximum=1), budget=budget)
        _check(len(found) == 1 and historical['id'] in overlays)
        ancestry(found[0], historical, overlays[historical['id']], budget=budget)


def outer(value: Any, *, original: dict | None, claim: dict | None, source: dict,
          source_tail: bool, task: dict, org: str, host: dict | None, attempt_id: str, budget=None) -> None:
    """Retain adverse/missing observer facts; no safe inference on held paths."""
    from runtime.infrastructure.task_pause_controls import decode_journal
    if value is None:
        return
    value = _closed(value, 'failed cleanup invocation terminal close release tracker_clear unsettled', reader_budget=budget)
    _check(_reader_all_true((type(value[k]) is bool for k in ('failed', 'tracker_clear', 'unsettled')), budget=budget))
    if value['cleanup'] is not None:
        cleanup = _closed(value['cleanup'], 'kind processes', reader_budget=budget)
        _check(cleanup['kind'] in ('safe', 'unknown') and type(cleanup['processes']) is list)
    native = value['invocation']
    if native is not None:
        decode_journal(_reader_decode(_canonical((_reader_reserve(budget, 320) or dict(version=1, entries=(_reader_reserve(budget, 192) or [native]))), reader_budget=budget), 'utf8', budget=budget), budget=budget)
        _check(native['org'] == org and native['task_id'] == task['id']
               and native['agent'] == task['assigned_agent'] and native['root_task_id'] == source['root_ancestor_id'])
        if source_tail:
            _check(native['id'] == source['result_claim']['owner_invocation_id']
                   and native['session_id'] == source['session_id'] and native['owner'] in ('ordinary', 'v2'))
        elif claim is not None:
            _check(native['id'] == claim['invocation_id'] and native['generation'] == claim['pause_generation']
                   and _reader_equal(native['fingerprint'], claim['task_fingerprint'], budget=budget) and native['owner'] == 'coding'
                   and native['session_id'] == task['current_session_id'])
        else:
            _check(original is not None)
            for key in ('id', 'org', 'root_task_id', 'task_id', 'agent', 'session_id', 'owner', 'owner_identity', 'fingerprint', 'generation'):
                _check(native[key] == original[key])
    if value['close'] is not None:
        closed = _closed(value['close'], 'removed invocation', reader_budget=budget)
        _check(type(closed['removed']) is bool and native is not None and closed['invocation'] == native)
    if value['release'] is not None:
        release = _closed(value['release'], 'owner_id task_id agent session_id owner_live cleanup_failed possible_launch quiescent removed', reader_budget=budget)
        expected = native or original
        # A missing current journal is itself adverse. The genuine previously
        # committed claim still identifies its original release observation;
        # this identity comparison creates no replacement native entry.
        expected_identity = ((_reader_reserve(budget, 128) or (expected['id'], expected['task_id'], expected['agent'], expected['session_id']))
            if expected is not None else
            (_reader_reserve(budget, 128) or (source['result_claim']['owner_invocation_id'], source['task_id'], source['principal'], source['session_id']))
            if source_tail else (_reader_reserve(budget, 128) or (claim['invocation_id'], task['id'], task['assigned_agent'], task['current_session_id']))
            if claim is not None else None)
        _check(expected_identity is not None and _reader_equal((_reader_reserve(budget, 128) or (release['owner_id'], release['task_id'], release['agent'], release['session_id'])), expected_identity, budget=budget)
               and _reader_all_true((type(release[k]) is bool for k in ('owner_live', 'cleanup_failed', 'possible_launch', 'quiescent', 'removed')), budget=budget))
    if value['terminal'] is not None:
        terminal = _closed(value['terminal'], 'request terminal_reason attempt receipt cleanup_error', reader_budget=budget)
        request = _closed(terminal['request'],
            'org invocation_kind logical_id executor_profile enqueued_at retry_attempt agent_id', reader_budget=budget)
        from runtime.platform.session_backend import TerminalReason
        _check(type(terminal['terminal_reason']) is str
               and terminal['terminal_reason'] in _reader_tuple((reason.value for reason in TerminalReason), budget=budget)
               and request['invocation_kind'] == 'task' and type(request['retry_attempt']) is int
               and request['retry_attempt'] >= 0 and type(request['enqueued_at']) in (_reader_reserve(budget, 96) or (int, float))
               and request['enqueued_at'] >= 0 and type(request['executor_profile']) is str
               and (terminal['cleanup_error'] is None or type(terminal['cleanup_error']) is str)
               and (terminal['receipt'] is None or type(terminal['receipt']) is dict))
        _check(type(terminal['attempt']) is int and terminal['attempt'] >= 0
               and terminal['request']['org'] == org and terminal['request']['agent_id'] == task['assigned_agent'])
        expected_request = (source['task_id'] if source_tail else host['logical_request_key'] if host is not None
            else identity('host', attempt_id, (native or original)['id'], budget=budget) if native is not None or original is not None else None)
        _check(expected_request is not None and terminal['request']['logical_id'] == expected_request)


def defer_ancestry(actual: dict, historical: dict, storage: Any, budget: Any) -> None:
    """Retain an unresolved equality obligation, never a validated row cache.

    Full replay can encounter an older graph before the later ancestor batch.
    Original predicates still use the unchanged capture. Complete batch closure
    below must authenticate the sole overlay and freshly reread current SQL.
    """
    from runtime.workflows.coding_start_history import same
    # This census is independent of mutation cascade and includes unchanged
    # causal rows. The actual locked cancellation writer consumes it too.
    budget.step()
    _reader_add(storage.cancellation_dependencies, historical['id'], budget=budget)
    if same(actual, historical, budget=budget):
        return
    from runtime.workflows.coding_revision import _copy_allowance
    _copy_allowance(historical, budget)
    _reader_append(storage.cancellation_comparisons, historical, budget=budget)


def ancestry(actual: dict, historical: dict, cancellation: dict | None, *, budget=None) -> None:
    _reader_work((_reader_reserve(budget, 112) or (actual, historical, cancellation)), budget)
    from runtime.workflows.coding_start_history import same
    expected = historical
    if cancellation is not None:
        _check(historical['status'] not in TERMINAL
               and historical['id'] in cancellation['originals']
               and same(cancellation['originals'][historical['id']], historical, budget=budget))
        command = cancellation['command']
        note = 'cancelled by ' + command['actor'] + (': ' + command['rationale'] if command['rationale'] else '')
        expected = {**historical, 'status': 'cancelled', 'block_kind': None, 'note': note,
            'cancelled_at': command['cancelled_at'], 'completed_at': command['cancelled_at'],
            'updated_at': command['cancelled_at']}
    _check(same(actual, expected, budget=budget))
