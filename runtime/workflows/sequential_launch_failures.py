"""Original-owner no-launch dispositions; intent is a fence, never closure.

Consumers authenticate durable facts. They never reconstruct an invocation,
remove native ownership, or turn an unresolved disposition into a closed one.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from runtime.workflows.sequential_history import _ReadBudget, _bytes, _require

ACTION = 'workflow_seq_native_launch_failure'
FORMAT = 'workflow-sequential-native-launch-failure@1'
_COMMON = {'format', 'stage', 'org', 'run_id', 'visit_id', 'task_id', 'principal',
           'prospective_session_id', 'invocation_id', 'boundary', 'native_fingerprint_digest'}
_CLOSED = {'intent_id', 'intent_digest', 'commit_entered', 'launch_observed', 'possible_launch',
           'execution_unknown', 'cleanup_failed', 'native_close', 'native_release', 'preparation_digests'}
_BOUNDARIES = ('before_reservation', 'before_preparation_effects', 'preparation_uncertain',
               'preparation_effects', 'launch_commit', 'possible_launch')


def _digest(value: Any) -> str:
    return hashlib.sha256(_bytes(value)).hexdigest()


def history(conn: sqlite3.Connection, run: dict, visit: dict, budget: _ReadBudget) -> list[tuple[int, dict, str]]:
    budget.step()
    rows = conn.execute("SELECT id,agent,CASE WHEN typeof(payload)='text' AND "
        "length(CAST(payload AS BLOB))<=8192 THEN payload END FROM audit_log "
        "WHERE task_id=? AND action=? ORDER BY id LIMIT 3", (visit['task_id'], ACTION)).fetchall()
    _require(len(rows) <= 2, 'launch_failure_conflict')
    result = []
    for identity, agent, raw in rows:
        _require(type(raw) is str, 'launch_failure_invalid')
        budget.reserve(48 * len(raw) + 512)
        value = json.loads(raw)
        _require(type(value) is dict and value.get('stage') in ('intent', 'unsettled', 'closed')
            and value.keys() == _COMMON | (_CLOSED if value['stage'] == 'closed' else set())
            and value['format'] == FORMAT and value['org'] == run['org_slug']
            and (value['run_id'], value['visit_id'], value['task_id'], value['principal'], agent) ==
                (run['id'], visit['id'], visit['task_id'], visit['principal'], visit['principal'])
            and value['boundary'] in _BOUNDARIES
            and all(type(value[key]) is str and 0 < len(value[key]) <= 256
                for key in ('invocation_id', 'prospective_session_id'))
            and type(value['native_fingerprint_digest']) is str
            and len(value['native_fingerprint_digest']) == 64
            and all(c in '0123456789abcdef' for c in value['native_fingerprint_digest'])
            and raw.encode('utf8') == _bytes(value), 'launch_failure_invalid')
        result.append((identity, value, hashlib.sha256(raw.encode('utf8')).hexdigest()))
    if len(result) == 2:
        first, last = result[0][1], result[1][1]
        _require(first['stage'] == 'intent' and last['stage'] in ('closed', 'unsettled')
            and all(first[key] == last[key] for key in _COMMON - {'stage'}), 'launch_failure_conflict')
    return result


def closed_proof(conn: sqlite3.Connection, run: dict, visit: dict, budget: _ReadBudget) -> dict | None:
    rows = history(conn, run, visit, budget)
    if not rows:
        return None
    _require(len(rows) == 2 and rows[-1][1]['stage'] == 'closed', 'launch_failure_unsettled')
    identity, closed, digest = rows[-1]
    _require(closed['intent_id'] == rows[0][0] and type(closed['intent_id']) is int
        and closed['intent_digest'] == rows[0][2]
        and all(closed[key] is False for key in ('commit_entered', 'launch_observed', 'possible_launch',
            'execution_unknown', 'cleanup_failed'))
        and closed['boundary'] in ('before_reservation', 'before_preparation_effects'), 'launch_failure_invalid')
    disposition = 'not_created' if closed['boundary'] == 'before_reservation' else 'removed'
    expected = dict(disposition=disposition, invocation_id=closed['invocation_id'], task_id=visit['task_id'],
                    principal=visit['principal'], prospective_session_id=closed['prospective_session_id'])
    _require(closed['native_close'] == expected and closed['native_release'] == expected
        and type(closed['preparation_digests']) is list
        and len(closed['preparation_digests']) == (0 if disposition == 'not_created' else 1)
        and all(type(item) is str and len(item) == 64 and all(c in '0123456789abcdef' for c in item)
            for item in closed['preparation_digests']), 'launch_failure_invalid')
    from runtime.workflows.sequential_engine import _engine_rows
    native = _engine_rows(conn, 'tasks', budget, 'id=?', (visit['task_id'],))
    _require(len(native) == 1 and native[0]['status'] in ('pending', 'cancelled'), 'launch_failure_native_conflict')
    task = native[0]
    original = dict(status='pending', block_kind=task['block_kind'], count=task['orchestration_step_count'],
        session_id=task['current_session_id'], agent=task['assigned_agent'], fanout=task['active_fanout'],
        jobs=task['blocked_on_job_ids'], parent=task['parent_task_id'], brief_digest=hashlib.sha256(task['brief'].encode('utf8')).hexdigest())
    _require(closed['native_fingerprint_digest'] == _digest(original), 'launch_failure_native_conflict')
    from runtime.infrastructure.task_pause_controls import MAX_JOURNAL_BYTES, decode_journal
    current, seen = visit['task_id'], set()
    while current is not None:
        budget.step()
        _require(current not in seen and len(seen) < 256, 'launch_failure_native_conflict')
        seen.add(current)
        pause = conn.execute("SELECT CASE WHEN typeof(admission_journal_json)='text' AND "
            "length(CAST(admission_journal_json AS BLOB))<=? THEN admission_journal_json END "
            'FROM task_pause_controls WHERE root_task_id=?', (MAX_JOURNAL_BYTES, current)).fetchone()
        if pause is not None:
            _require(type(pause[0]) is str, 'launch_failure_native_conflict')
            budget.reserve(24 * len(pause[0].encode('utf8')) + 512)
            _require(not any(entry['task_id'] == visit['task_id'] for entry in decode_journal(pause[0])['entries']),
                'launch_failure_unsettled')
        parent = conn.execute('SELECT parent_task_id FROM tasks WHERE id=?', (current,)).fetchone()
        _require(parent is not None, 'launch_failure_native_conflict')
        current = parent[0]
    return dict(kind='launch_failed_before_start', result_id=None, receipt_id=identity, receipt_digest=digest)


def _append(conn: sqlite3.Connection, body: dict) -> int:
    return conn.execute('INSERT INTO audit_log(task_id,agent,action,payload,timestamp) VALUES (?,?,?,?,?)',
        (body['task_id'], body['principal'], ACTION, _bytes(body).decode('utf8'),
         datetime.now(timezone.utc).isoformat())).lastrowid


def observe_failure(invocation: Any) -> None:
    """Latch the actual original frame, including failed ExecutorResult returns."""
    if invocation.owner != 'sequential':
        return
    dispatcher = invocation.workflow_dispatcher
    try:
        with dispatcher._reader() as conn:
            run, _, _ = dispatcher._entry(conn, invocation.task.id)
            if run.get('engine_version', 1) != 2 or run['state'] != 'running' or run['progress'] == 'cancelling':
                return  # Existing admission/cancellation disposition owns this return.
    except (ValueError, sqlite3.DatabaseError):
        pass  # Unavailable ownership cannot become ordinary or a positive claim.
    invocation.sequential_failure = True
    invocation.workflow_dispatcher._launch_failure_owners[invocation.task.id] = invocation


def fenced(dispatcher: Any, conn: sqlite3.Connection, run: dict, visit: dict, invocation: Any = None) -> bool:
    if history(conn, run, visit, _ReadBudget()):
        return True
    original = dispatcher._launch_failure_owners.get(visit['task_id'])
    if original is not None:
        return True
    store = dispatcher.org.db._task_pause_store
    row = store.row_uncommitted(store.root_uncommitted(visit['task_id'])['id'])
    entries = [] if row is None else [e for e in row['journal']['entries'] if e['task_id'] == visit['task_id']]
    return any(invocation is None or entry['id'] != invocation.identity
        or entry['phase'] != 'prepared' or entry['context'].get('execution_unknown') for entry in entries)


def _body(invocation: Any, run: dict, visit: dict, stage: str) -> dict:
    from runtime.orchestrator.task_pause import fingerprint
    boundary = ('possible_launch' if invocation.launch_observed or invocation.admitted else
        'launch_commit' if invocation.commit_entered else
        'preparation_effects' if invocation.preparation_effects_entered else
        'preparation_uncertain' if (invocation.reserve_entered or invocation.journal_entered) and not invocation.prepare_complete else
        'before_preparation_effects' if invocation.prepare_complete else 'before_reservation')
    return dict(format=FORMAT, stage=stage, org=run['org_slug'], run_id=run['id'], visit_id=visit['id'],
        task_id=visit['task_id'], principal=visit['principal'], prospective_session_id=invocation.session_id,
        invocation_id=invocation.identity, boundary=boundary, native_fingerprint_digest=_digest(fingerprint(invocation.task)))


def _retained(invocation: Any, body: dict) -> None:
    """One durable original unresolved obligation, never an adoptable owner."""
    invocation.sequential_failure_retained = True
    reservation = invocation.move_reservation
    if reservation is not None:
        reservation.failed_cleanup()
    with invocation.store.writer(invocation.task.id) as (root, row):
        row = row or invocation.store.ensure_uncommitted(root['id'])
        entries = row['journal']['entries']
        entry = next((e for e in entries if e['id'] == invocation.identity), None)
        if entry is None:
            from runtime.orchestrator.task_pause import fingerprint
            entry = dict(id=invocation.identity, org=invocation.store.org_slug, root_task_id=root['id'],
                task_id=invocation.task.id, agent=invocation.agent, session_id=invocation.session_id,
                owner='sequential', owner_identity={}, fingerprint=fingerprint(invocation.task),
                generation=row['generation'], phase='unknown', retry={}, context={}, started_at=None,
                captured_generation=None)
            entries.append(entry)
        entry['phase'] = 'unknown'
        entry['context']['execution_unknown'] = True
        entry['started_at'] = entry['captured_generation'] = None
        invocation.store.save_journal_uncommitted(row)
        run, visit, _ = invocation.workflow_dispatcher._entry(invocation.store.db._conn, invocation.task.id)
        prior = history(invocation.store.db._conn, run, visit, _ReadBudget())
        if not prior or len(prior) == 1 and prior[0][1]['stage'] == 'intent':
            # Preserve the original intent identity/boundary on a failed close.
            unresolved = dict(prior[0][1] if prior else body, stage='unsettled')
            _append(invocation.store.db._conn, unresolved)
    invocation.store._live[invocation.identity] = invocation
    invocation.sequential_failure_settled = True


def settle_failure(invocation: Any, *, cleanup: Any, failed: bool, prepare_frame: bool = False) -> bool:
    """Called once by the original frame before the generic failed tail.

    True means this owner was positively retired OR remains fenced/unknown.
    P2 has no complete scratch-unwind certificate today and deliberately uses U.
    """
    if invocation.owner != 'sequential' or not getattr(invocation, 'sequential_failure', False):
        return False
    if getattr(invocation, 'sequential_failure_settled', False):
        return True
    dispatcher = invocation.workflow_dispatcher
    with dispatcher._reader() as conn:
        run, visit, _ = dispatcher._entry(conn, invocation.task.id)
        if run.get('engine_version', 1) != 2:
            return False
        # Started attempts may use only authentic T via the existing tail.
        if invocation.commit_entered and not failed and cleanup is not None and cleanup.kind == 'safe':
            terminal = conn.execute("SELECT 1 FROM workflow_seq_evidence WHERE run_id=? AND visit_id=? AND kind='terminal'",
                (run['id'], visit['id'])).fetchone()
            if terminal is not None:
                return False
        body = _body(invocation, run, visit, 'intent')
    reservation = invocation.move_reservation
    positive = (not invocation.commit_entered and not invocation.preparation_effects_entered
        and not invocation.launch_observed and not invocation.admitted and not invocation.unsettled
        and visit['session_id'] is None and visit['selected'] == 1 and run['state'] == 'running'
        and run['progress'] == 'queued' and run['active_visit_id'] == visit['id']
        and dispatcher.org.sessions.get_active(invocation.task.id, invocation.agent) is None)
    p0 = not invocation.reserve_entered and not invocation.journal_entered and reservation is None and prepare_frame
    p1 = (invocation.prepare_complete and reservation is not None and not reservation.cleanup_failed
        and not reservation.possible_launch and not reservation.repository_obligations
        and cleanup is not None and cleanup.kind == 'safe' and not cleanup.processes)
    positive = positive and (p0 or p1)
    try:
        if not positive:
            _retained(invocation, body)
            return True
        # The original native writer owns intent before any native removal.
        with invocation.store.writer(invocation.task.id) as (_, row):
            conn = invocation.store.db._conn
            current_run, current_visit, _ = dispatcher._entry(conn, invocation.task.id)
            _require(current_run['state'] == 'running' and current_run['progress'] == 'queued'
                and current_run['active_visit_id'] == current_visit['id'] and current_visit['selected'] == 1
                and current_visit['session_id'] is None and current_visit['result_id'] is None
                and not history(conn, current_run, current_visit, _ReadBudget()), 'launch_failure_conflict')
            digests = []
            if p1:
                entry = invocation._entry(row)
                _require(entry['phase'] == 'prepared' and not entry['context'].get('execution_unknown'), 'launch_failure_unsettled')
                digests = [_digest(entry)]
            intent_id = _append(conn, body)
        disposition = dict(disposition='not_created' if p0 else 'removed', invocation_id=invocation.identity,
            task_id=invocation.task.id, principal=invocation.agent, prospective_session_id=invocation.session_id)
        if p1:
            close = invocation.close(cleanup=cleanup)
            _require(close['removed'] is True, 'launch_failure_close_unsettled')
            with invocation.store.db._lock:
                owned = invocation.orch._team_moves._reservations.get(invocation.identity) is reservation
                invocation.orch._team_moves.release_task(invocation, failed=False)
                _require(owned and not reservation.owner_live and not reservation.cleanup_failed
                    and not reservation.possible_launch and not invocation.unsettled
                    and invocation.orch._team_moves._reservations.get(invocation.identity) is None,
                    'launch_failure_release_unsettled')
        closed = dict(body, stage='closed', intent_id=intent_id, intent_digest=_digest(body),
            commit_entered=False, launch_observed=False, possible_launch=False, execution_unknown=False,
            cleanup_failed=False, native_close=disposition, native_release=disposition, preparation_digests=digests)
        with invocation.store.writer(invocation.task.id) as _:
            conn = invocation.store.db._conn
            current_run, current_visit, _ = dispatcher._entry(conn, invocation.task.id)
            prior = history(conn, current_run, current_visit, _ReadBudget())
            _require(len(prior) == 1 and prior[0][0] == intent_id and prior[0][2] == closed['intent_digest'], 'launch_failure_conflict')
            _append(conn, closed)
            closed_proof(conn, current_run, current_visit, _ReadBudget())
        invocation.sequential_failure_settled = True
        invocation.sequential_failure_closed = True
        invocation.store._live.pop(invocation.identity, None)
        dispatcher._launch_failure_owners.pop(invocation.task.id, None)
        dispatcher._advance_generic(run['id'], visit_id=visit['id'])
        continuation = getattr(invocation.orch, '_workflow_continuation', None)
        if continuation is not None:
            continuation.settled(invocation.task.id)
        return True
    except BaseException:
        # No removal before durable intent; interrupted intent never grants P.
        if not getattr(invocation, 'sequential_failure_closed', False):
            _retained(invocation, body)
        raise
