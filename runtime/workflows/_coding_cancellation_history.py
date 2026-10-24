"""Independent closed cancellation replay; no writer after-image is authority."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from runtime.workflows.coding_bootstrap import decode_row, identity
from runtime.workflows.coding_saved_records import _canonical, _check, _closed, _hash, _timestamp
from runtime.workflows._coding_settings_execution import _read_json

TERMINAL = ('completed', 'failed', 'cancelled', 'superseded')


def replay(conn: Any, org: str, budget: Any, storage: Any, *, event: dict,
           previous: dict, body: dict, run: dict, attempts: dict, tasks: dict,
           observations: list, source: dict, source_invocation: dict, states: dict) -> tuple:
    from runtime.workflows.coding_start_history import rows, same
    from runtime.workflows.coding_native_history import _row
    _check(type(event['event_bytes']) is bytes and _hash(event['event_bytes']) == event['event_digest'])
    _closed(body, 'format id instance_id event_seq event_kind attempt_id operation previous_digest before after evidence created_at')
    _check(body['format'] == 'workflow-coding-cancellation-event@1'
           and _canonical(body) == event['event_bytes'] and event['event_kind'] == 'cancelled'
           and event['event_seq'] == run['cas_version'] + 1 == previous['event_seq'] + 1
           and event['previous_digest'] == previous['event_digest']
           and event['instance_id'] == run['instance_id'] and event['attempt_id'] == run['current_attempt_id']
           and run['state'] not in ('complete', 'cancelled'))
    for key in ('id', 'instance_id', 'event_seq', 'event_kind', 'attempt_id', 'previous_digest', 'created_at'):
        _check(same(body[key], event[key]))
    evidence = _closed(body['evidence'], 'command audits native_task_observations')
    command = _closed(evidence['command'],
        'format target_task_id cascade actor rationale cancelled_at tasks owners native_owners')
    _check(command['format'] == 'workflow-coding-cancellation-command@1'
           and type(command['cascade']) is bool and type(command['actor']) is str
           and bool(command['actor']) and command['actor'].strip() == command['actor']
           and type(command['rationale']) is str and command['rationale'].strip() == command['rationale']
           and command['cancelled_at'] == event['created_at']
           and same(evidence['native_task_observations'], observations))
    _timestamp(command['cancelled_at'])
    digest = _hash(_canonical(command))
    principal, key = 'service:coding-cancellation:' + org, 'coding-cancel:' + digest
    _check(body['operation'] == dict(org_slug=org, principal=principal, key=key, request_digest=digest)
           and event['org_slug'] == org and event['principal'] == principal
           and event['operation_key'] == key and event['request_digest'] == digest
           and event['id'] == identity('event', org, run['instance_id'], principal, key))
    _check(type(command['tasks']) is list and 1 <= len(command['tasks']) <= 512)
    originals = {}
    for item in command['tasks']:
        budget.step()
        _closed(item, 'id record')
        row = _row(item['record'])
        _check(row['id'] == item['id'] and item['id'] not in originals)
        originals[item['id']] = row
    _check(list(originals) == sorted(originals) and command['target_task_id'] in originals
           and originals[command['target_task_id']]['status'] not in TERMINAL
           and all(k in originals and same(v, originals[k]) for k, v in tasks.items()))
    from runtime.infrastructure.task_pause_controls import decode_journal
    _check(type(command['native_owners']) is list and len(command['native_owners']) <= 512)
    _check([n['id'] for n in command['native_owners']] == sorted({n['id'] for n in command['native_owners']}))
    for native in command['native_owners']:
        budget.step()
        decode_journal(_canonical(dict(version=1, entries=[native])).decode('utf8'))
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
                selected_attempts = [a for a in attempts.values() if a['task_id'] == native['task_id']]
                _check(len(selected_attempts) == 1)
                a = selected_attempts[0]
                claim = states[a['id']]['claim']
                _check(native['owner'] == 'coding' and native['owner_identity'] == {})
                if claim is not None:
                    _check(native['id'] == claim['invocation_id'] and native['generation'] == claim['pause_generation']
                           and native['fingerprint'] == claim['task_fingerprint'] and native['session_id'] == a['session_id'])
                else:
                    _check(a['state'] == 'queued' and native['phase'] in ('prepared', 'unknown')
                           and native['fingerprint'] == dict(status=row['status'], block_kind=row['block_kind'],
                               count=row['orchestration_step_count'], session_id=row['current_session_id'],
                               agent=row['assigned_agent'], fanout=row['active_fanout'], jobs=row['blocked_on_job_ids'],
                               parent=row['parent_task_id'], brief_digest=_hash(row['brief'].encode('utf8'))))
    live = {k: v for k, v in originals.items() if v['status'] not in TERMINAL}
    _check(live and type(evidence['audits']) is list and len(evidence['audits']) == len(live))
    audit_tasks = []
    for item in evidence['audits']:
        budget.step()
        _closed(item, 'table id record')
        audit = decode_row(item['record'])
        found = list(rows(conn, 'audit_log', budget, where='id=?', args=(item['id'],), maximum=1))
        _check(item['table'] == 'audit_log' and len(found) == 1 and same(found[0], audit)
               and type(item['id']) is int and item['id'] > 0 and item['id'] == audit['id']
               and audit['action'] == 'task_cancelled' and audit['agent'] == command['actor']
               and audit['task_id'] in live)
        native_payload = _closed(_read_json(audit['payload'].encode('utf8'), budget, 1048576), 'rationale cascade')
        _check(same(native_payload, dict(rationale=command['rationale'], cascade=command['cascade'])))
        from runtime.workflows.coding_source_history import _timestamp as native_timestamp
        native_timestamp(audit['timestamp'])
        audit_tasks.append(audit['task_id'])
        storage.add('audit_log', audit)
    _check(audit_tasks == sorted(live))
    note = ('cancelled by ' + command['actor'] + (': ' + command['rationale'] if command['rationale'] else ''))
    from runtime.workflows.coding_revision import _copy_allowance
    _copy_allowance(dict(tasks=tasks, attempts=attempts), budget)
    expected_tasks = deepcopy(tasks)
    for task_id in expected_tasks:
        if task_id in live:
            expected_tasks[task_id].update(status='cancelled', block_kind=None, note=note,
                cancelled_at=command['cancelled_at'], completed_at=command['cancelled_at'], updated_at=command['cancelled_at'])
    expected_attempts = deepcopy(attempts)
    for a in expected_attempts.values():
        if a['task_id'] in live:
            a['state'] = 'cancelled'
    expected_run = {**run, 'state': 'cancelled', 'cas_version': event['event_seq'], 'last_event_id': event['id']}
    _check(same(body['before'], dict(cas_version=run['cas_version'], last_event_id=previous['id']))
           and same(body['after'], dict(state='cancelled')))
    record = dict(command=command, audits=evidence['audits'], originals=originals, event=event)
    storage.cancellations.setdefault(digest, {})[run['instance_id']] = record
    storage.add('workflow_coding_events', event)
    return expected_run, expected_attempts, expected_tasks, record


def ordinary_overlay(org: str, command: dict, audits: list, saved: Any, instances: set) -> dict:
    """Distinct ordinary operation; never a second coding cancellation event."""
    digest = _hash(_canonical(command))
    return dict(format='workflow-coding-ordinary-cancellation@1',
        operation=dict(org_slug=org, principal='service:coding-cancellation:' + org,
            key='coding-ordinary-cancel:' + digest, request_digest=digest),
        command=command, audits=audits,
        retained=[dict(instance_id=i, event_id=saved.runs[i]['native']['cancellation']['event']['id'],
            event_digest=saved.runs[i]['native']['cancellation']['event']['event_digest']) for i in sorted(instances)])


def _ordinary_batches(conn: Any, org: str, budget: Any, storage: Any) -> list:
    """Independently authenticate all ordinary overlays, including orphan rows.

    Existing literal coding event readers are unchanged. This separate closed
    audit family admits only an ordinary operation over already cancelled graphs.
    """
    from runtime.workflows.coding_start_history import rows, same
    from runtime.workflows.coding_native_history import _row
    from runtime.workflows.coding_source_history import _timestamp as native_timestamp
    from runtime.infrastructure.task_pause_controls import decode_journal
    batches, operations = [], set()
    for audit in rows(conn, 'audit_log', budget, where="action='coding_ordinary_cancellation'"):
        _check(type(audit['payload']) is str)
        value = _closed(_read_json(audit['payload'].encode('utf8'), budget, 1048576),
            'format operation command audits retained')
        _check(value['format'] == 'workflow-coding-ordinary-cancellation@1')
        command = _closed(value['command'],
            'format target_task_id cascade actor rationale cancelled_at tasks owners native_owners')
        _check(command['format'] == 'workflow-coding-cancellation-command@1'
               and command['cascade'] is True and type(command['actor']) is str
               and bool(command['actor']) and command['actor'].strip() == command['actor']
               and type(command['rationale']) is str and command['rationale'].strip() == command['rationale'])
        _timestamp(command['cancelled_at'])
        digest = _hash(_canonical(command))
        _check(same(value['operation'], dict(org_slug=org, principal='service:coding-cancellation:' + org,
                   key='coding-ordinary-cancel:' + digest, request_digest=digest))
               and digest not in operations and audit['agent'] == command['actor']
               and audit['task_id'] == command['target_task_id'] and type(audit['id']) is int and audit['id'] > 0)
        native_timestamp(audit['timestamp'])
        operations.add(digest)
        _check(type(command['tasks']) is list and 1 <= len(command['tasks']) <= 512)
        originals = {}
        for item in command['tasks']:
            budget.step()
            _closed(item, 'id record')
            row = _row(item['record'])
            _check(row['id'] == item['id'] and item['id'] not in originals)
            originals[item['id']] = row
        _check(list(originals) == sorted(originals) and command['target_task_id'] in originals
               and command['target_task_id'] not in storage.owners
               and originals[command['target_task_id']]['status'] not in TERMINAL)
        live = {k: v for k, v in originals.items() if v['status'] not in TERMINAL}
        _check(live and not (live.keys() & storage.owners.keys())
               and type(value['audits']) is list and len(value['audits']) == len(live))
        tasks, ids = [], []
        for item in value['audits']:
            budget.step()
            _closed(item, 'table id record')
            original = decode_row(item['record'])
            found = list(rows(conn, 'audit_log', budget, where='id=?', args=(item['id'],), maximum=1))
            _check(item['table'] == 'audit_log' and type(item['id']) is int and 0 < item['id'] < audit['id']
                   and len(found) == 1 and same(found[0], original) and original['id'] == item['id']
                   and original['action'] == 'task_cancelled' and original['agent'] == command['actor']
                   and original['task_id'] in live and type(original['payload']) is str)
            native_timestamp(original['timestamp'])
            payload = _closed(_read_json(original['payload'].encode('utf8'), budget, 1048576), 'rationale cascade')
            _check(same(payload, dict(rationale=command['rationale'], cascade=True)))
            tasks.append(original['task_id'])
            ids.append(item['id'])
            storage.add('audit_log', original)
        _check(tasks == sorted(live) and ids == sorted(set(ids)))
        _check(type(command['native_owners']) is list and len(command['native_owners']) <= 512)
        _check([n['id'] for n in command['native_owners']] == sorted({n['id'] for n in command['native_owners']}))
        for native in command['native_owners']:
            budget.step()
            decode_journal(_canonical(dict(version=1, entries=[native])).decode('utf8'))
            _check(native['org'] == org and native['task_id'] in originals)
            row = originals[native['task_id']]
            _check(native['agent'] == row['assigned_agent']
                   and (row['current_session_id'] is None or native['session_id'] == row['current_session_id']))
            owner = storage.owners.get(native['task_id'])
            if owner is not None:
                earlier = storage.runs[owner[1]]['native'].get('cancellation')
                _check(earlier is not None)
                original = [n for n in earlier['command']['native_owners'] if n['id'] == native['id']]
                _check(len(original) == 1)
                for key in ('id', 'org', 'root_task_id', 'task_id', 'agent', 'session_id',
                            'owner', 'owner_identity', 'fingerprint', 'generation'):
                    _check(same(native[key], original[0][key]))
        _check(type(value['retained']) is list and 1 <= len(value['retained']) <= 512)
        instances = []
        for anchor in value['retained']:
            budget.step()
            _closed(anchor, 'instance_id event_id event_digest')
            instance = anchor['instance_id']
            _check(instance in storage.runs and storage.runs[instance]['run']['state'] == 'cancelled')
            earlier = storage.runs[instance]['native'].get('cancellation')
            _check(earlier is not None and anchor['event_id'] == earlier['event']['id']
                   and anchor['event_digest'] == earlier['event']['event_digest']
                   and max(a['id'] for a in earlier['audits']) < min(ids))
            instances.append(instance)
        _check(instances == sorted(set(instances)))
        storage.add('audit_log', audit)
        batches.append(dict(command=command, audits=value['audits'], originals=originals,
                            retained=set(instances), capture_audit=audit))
    return batches


def native_checkpoint(history: list, event: dict | None, tasks: dict,
                      observations: list, budget: Any) -> None:
    """Retain reader-derived rows only, after closed native replay succeeds.

    Each copy and its traversal consume the enclosing original allowance.
    Nothing here is persisted or accepted from a writer/caller projection.
    """
    from runtime.workflows.coding_revision import _copy_allowance
    point = dict(event=None if event is None else dict(id=event['id'],
        event_seq=event['event_seq'], event_digest=event['event_digest'],
        previous_digest=event['previous_digest'], created_at=event['created_at']),
        tasks=tasks, observations=observations)
    _copy_allowance(point, budget)
    history.append(deepcopy(point))


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
        audit = decode_row(item['record'])
        instant = datetime.fromisoformat(audit['timestamp'])
        _check(instant >= boundary)
        capture_end = max(capture_end, instant)
    if 'capture_audit' in sample:
        instant = datetime.fromisoformat(sample['capture_audit']['timestamp'])
        _check(instant >= capture_end)
        capture_end = instant
    _copy_allowance(history[0]['tasks'], budget)
    current = deepcopy(history[0]['tasks'])
    selected = None
    crossed = False
    previous = None
    previous_time = None
    for point in history:
        budget.step()
        # fold_heartbeats authenticates each per-task chain. Merge their audit
        # order only after charging the intermediate traversal/allocation.
        audits = []
        for observation in point['observations']:
            budget.step()
            for audit in observation['records']:
                budget.step()
                audits.append(audit)
        _copy_allowance(audits, budget)
        for audit in sorted(audits, key=lambda a: a['id']):
            budget.step()
            _check(audit['id'] > prior_audit and not first <= audit['id'] <= last)
            prior_audit = audit['id']
            before_boundary = audit['id'] < first
            instant = datetime.fromisoformat(audit['timestamp'])
            _check(instant < boundary if before_boundary else instant > capture_end)
            _check(not crossed or not before_boundary)
            if not before_boundary and not crossed:
                _copy_allowance(current, budget)
                selected, crossed = deepcopy(current), True
            body = _read_json(audit['payload'].encode('utf8'), budget, 1048576)
            before = _row(body['before_record'])
            _copy_allowance(before, budget)
            after = {**before, 'last_heartbeat': body['observed_at'], 'updated_at': body['observed_at']}
            _check(before['id'] in current and same(before, current[before['id']])
                   and same(after, _row(body['after_record'])))
            _copy_allowance(after, budget)
            current[before['id']] = after
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
                selected, crossed = deepcopy(current), True
            previous, previous_time = event, instant
            _copy_allowance(point['tasks'], budget)
            current = deepcopy(point['tasks'])
        else:
            _check(same(current, point['tasks']))
    _check(previous['id'] == entry['native']['history'][-1]['id']
           and same(current, entry['tasks']))
    for key, row in current.items():
        budget.step()
        actual = list(rows(conn, 'tasks', budget, where='id=?', args=(key,), maximum=1))
        _check(len(actual) == 1 and same(actual[0], row))
    return current if selected is None else selected


def complete(conn: Any, org: str, budget: Any, storage: Any) -> None:
    """Cross-graph/legacy closure, including partial and orphan batch refusal."""
    from runtime.workflows.coding_start_history import rows, same
    ordinary = _ordinary_batches(conn, org, budget, storage)
    batches = [(records, next(iter(records.values()))) for records in storage.cancellations.values()]
    batches.extend(({}, record) for record in ordinary)
    for records, sample in batches:
        command, originals = sample['command'], sample['originals']
        pending, retained, visited, instances = [command['target_task_id']], [], set(), set()
        while pending or retained:
            budget.step()
            retaining = not pending
            key = retained.pop() if retaining else pending.pop()
            if key in visited:
                continue
            _check(key in originals and len(visited) < 512)
            visited.add(key)
            row = originals[key]
            _check(not retaining or row['status'] in TERMINAL)
            owner = storage.owners.get(key)
            if owner is not None and owner[1] not in instances:
                # A terminal ordinary path can retain a genuine earlier fence,
                # never introduce a fresh live graph into this operation.
                _check(not retaining or owner[1] not in records)
                instances.add(owner[1])
                entry = storage.runs[owner[1]]
                if owner[1] not in records:
                    # A later ancestor stop retains this earlier terminal
                    # graph byte-for-byte; no second event/control is owed.
                    earlier = entry['native'].get('cancellation')
                    _check(earlier is not None and entry['run']['state'] == 'cancelled'
                           and max(a['id'] for a in earlier['audits']) < min(a['id'] for a in sample['audits']))
                    historical = retained_boundary(conn, entry, sample, budget)
                    for task_id, task in historical.items():
                        budget.step()
                        _check(task['status'] in TERMINAL and task_id in originals
                               and same(task, originals[task_id]))
                (retained if retaining else pending).extend(entry['tasks'])
            if command['cascade'] and row['status'] not in TERMINAL:
                pending.extend(r['id'] for r in rows(conn, 'tasks', budget,
                    where='parent_task_id=?', args=(key,), maximum=512))
            elif command['cascade'] and owner is None and key in storage.cancellation_dependencies:
                # Recompute the retained census independently, using original
                # terminal rows and the complete authenticated dependency set.
                for child in rows(conn, 'tasks', budget,
                        where='parent_task_id=?', args=(key,), maximum=512):
                    budget.step()
                    if child['id'] in storage.cancellation_dependencies or child['id'] in storage.owners:
                        retained.append(child['id'])
        _check(visited == set(originals) and set(records) <= instances)
        if 'retained' in sample:
            _check(instances == sample['retained'])
        expected_owners = [dict(task_id=k, kind=v[0], instance_id=v[1])
            for k, v in sorted(storage.owners.items()) if v[1] in instances]
        _check(same(command['owners'], expected_owners))
        for record in records.values():
            _check(same(command, record['command']) and same(sample['audits'], record['audits']))
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
            found = list(rows(conn, 'tasks', budget, where='id=?', args=(key,), maximum=1))
            _check(len(found) == 1 and same(found[0], row))

    # No row may receive two independent live-to-cancelled overlays. Replaying
    # multiple graph events for ONE batch is distinct from another batch.
    overlays = {}
    for _, record in batches:
        for key, original in record['originals'].items():
            budget.step()
            if original['status'] not in TERMINAL:
                _check(key not in overlays)
                overlays[key] = record
    for historical in storage.cancellation_comparisons:
        budget.step()
        found = list(rows(conn, 'tasks', budget, where='id=?', args=(historical['id'],), maximum=1))
        _check(len(found) == 1 and historical['id'] in overlays)
        ancestry(found[0], historical, overlays[historical['id']])


def outer(value: Any, *, original: dict | None, claim: dict | None, source: dict,
          source_tail: bool, task: dict, org: str, host: dict | None, attempt_id: str) -> None:
    """Retain adverse/missing observer facts; no safe inference on held paths."""
    from runtime.infrastructure.task_pause_controls import decode_journal
    if value is None:
        return
    value = _closed(value, 'failed cleanup invocation terminal close release tracker_clear unsettled')
    _check(all(type(value[k]) is bool for k in ('failed', 'tracker_clear', 'unsettled')))
    if value['cleanup'] is not None:
        cleanup = _closed(value['cleanup'], 'kind processes')
        _check(cleanup['kind'] in ('safe', 'unknown') and type(cleanup['processes']) is list)
    native = value['invocation']
    if native is not None:
        decode_journal(_canonical(dict(version=1, entries=[native])).decode('utf8'))
        _check(native['org'] == org and native['task_id'] == task['id']
               and native['agent'] == task['assigned_agent'] and native['root_task_id'] == source['root_ancestor_id'])
        if source_tail:
            _check(native['id'] == source['result_claim']['owner_invocation_id']
                   and native['session_id'] == source['session_id'] and native['owner'] in ('ordinary', 'v2'))
        elif claim is not None:
            _check(native['id'] == claim['invocation_id'] and native['generation'] == claim['pause_generation']
                   and native['fingerprint'] == claim['task_fingerprint'] and native['owner'] == 'coding'
                   and native['session_id'] == task['current_session_id'])
        else:
            _check(original is not None)
            for key in ('id', 'org', 'root_task_id', 'task_id', 'agent', 'session_id', 'owner', 'owner_identity', 'fingerprint', 'generation'):
                _check(native[key] == original[key])
    if value['close'] is not None:
        closed = _closed(value['close'], 'removed invocation')
        _check(type(closed['removed']) is bool and native is not None and closed['invocation'] == native)
    if value['release'] is not None:
        release = _closed(value['release'], 'owner_id task_id agent session_id owner_live cleanup_failed possible_launch quiescent removed')
        expected = native or original
        # A missing current journal is itself adverse. The genuine previously
        # committed claim still identifies its original release observation;
        # this identity comparison creates no replacement native entry.
        expected_identity = ((expected['id'], expected['task_id'], expected['agent'], expected['session_id'])
            if expected is not None else
            (source['result_claim']['owner_invocation_id'], source['task_id'], source['principal'], source['session_id'])
            if source_tail else (claim['invocation_id'], task['id'], task['assigned_agent'], task['current_session_id'])
            if claim is not None else None)
        _check(expected_identity is not None and (release['owner_id'], release['task_id'], release['agent'], release['session_id']) == expected_identity
               and all(type(release[k]) is bool for k in ('owner_live', 'cleanup_failed', 'possible_launch', 'quiescent', 'removed')))
    if value['terminal'] is not None:
        terminal = _closed(value['terminal'], 'request terminal_reason attempt receipt cleanup_error')
        request = _closed(terminal['request'],
            'org invocation_kind logical_id executor_profile enqueued_at retry_attempt agent_id')
        from runtime.platform.session_backend import TerminalReason
        _check(type(terminal['terminal_reason']) is str
               and terminal['terminal_reason'] in tuple(reason.value for reason in TerminalReason)
               and request['invocation_kind'] == 'task' and type(request['retry_attempt']) is int
               and request['retry_attempt'] >= 0 and type(request['enqueued_at']) in (int, float)
               and request['enqueued_at'] >= 0 and type(request['executor_profile']) is str
               and (terminal['cleanup_error'] is None or type(terminal['cleanup_error']) is str)
               and (terminal['receipt'] is None or type(terminal['receipt']) is dict))
        _check(type(terminal['attempt']) is int and terminal['attempt'] >= 0
               and terminal['request']['org'] == org and terminal['request']['agent_id'] == task['assigned_agent'])
        expected_request = (source['task_id'] if source_tail else host['logical_request_key'] if host is not None
            else identity('host', attempt_id, (native or original)['id']) if native is not None or original is not None else None)
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
    storage.cancellation_dependencies.add(historical['id'])
    if same(actual, historical):
        return
    from runtime.workflows.coding_revision import _copy_allowance
    _copy_allowance(historical, budget)
    storage.cancellation_comparisons.append(historical)


def ancestry(actual: dict, historical: dict, cancellation: dict | None) -> None:
    from runtime.workflows.coding_start_history import same
    expected = historical
    if cancellation is not None:
        _check(historical['status'] not in TERMINAL
               and historical['id'] in cancellation['originals']
               and same(cancellation['originals'][historical['id']], historical))
        command = cancellation['command']
        note = 'cancelled by ' + command['actor'] + (': ' + command['rationale'] if command['rationale'] else '')
        expected = {**historical, 'status': 'cancelled', 'block_kind': None, 'note': note,
            'cancelled_at': command['cancelled_at'], 'completed_at': command['cancelled_at'],
            'updated_at': command['cancelled_at']}
    _check(same(actual, expected))
