"""Internal S5 wait sets, immutable delivery pages and result-owned consumption.

The caller owns the current-authority writer and genuine native result. Nothing
here admits a session, commits, runs a host, or mounts an orchestrator decision.
"""
from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from typing import Any

from runtime.infrastructure.workflow_task_ownership import selected_owner
from runtime.workflows.sequential_history import (
    _ReadBudget, _bytes, _decode, _hashed, _require, _result, _sql_budget,
)


TABLES = ('workflow_seq_source_wait_sets', 'workflow_seq_source_memberships',
    'workflow_seq_inbox', 'workflow_seq_inbox_deliveries',
    'workflow_seq_consumption_receipts', 'workflow_seq_consumed_notifications')


def observe_dependencies(conn: sqlite3.Connection, db: Any, task_id: str) -> dict | None:
    """Read dependencies in the caller's snapshot, without installing storage.

    Complete qualification retains its existing budgets; indexed observations
    use the existing bounded reader on that deadline. Invalid owned evidence never means
    an empty wait set. Ordinary callers with genuinely absent storage retain
    their historical behavior.
    """
    from runtime.workflows.sequential_history import read_sequential_history
    from runtime.workflows.sequential_start import _ordinary_dependencies
    _require(conn.in_transaction, 'initiator_requires_snapshot')
    if conn.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='workflow_task_ownership'").fetchone() is None:
        return None
    org_slug = getattr(db, '_task_ownership_org', None)
    owner = selected_owner(conn, task_id, org_slug)
    if owner[0] != 'seq_source_wait_set':
        return None
    budget = _ReadBudget()
    read_sequential_history(conn, expected_org_slug=org_slug, deadline=budget.deadline)
    with _sql_budget(conn, budget):
        tasks = _rows(conn, 'tasks', budget, 'id=?', (task_id,))
        _require(len(tasks) == 1, 'initiator_identity_conflict')
        task = tasks[0]
        _ordinary_dependencies(task, budget)
        members = _rows(conn, 'workflow_seq_source_memberships', budget, 'wait_set_id=?', (owner[1],))
        _require(bool(members), 'initiator_membership_missing')
        runs = []
        for member in members:
            selected = _rows(conn, 'workflow_seq_runs', budget, 'id=?', (member['run_id'],))
            _require(len(selected) == 1 and selected[0]['source_task_id'] == task_id,
                     'initiator_membership_conflict')
            runs.extend(selected)
        carriers = {run['carrier_task_id'] for run in runs}
        children = _rows(conn, 'tasks', budget, 'parent_task_id=?', (task_id,))
        _require(len(children) <= 256, 'initiator_dependency_bounds')
        terminal = {'completed', 'failed', 'cancelled', 'superseded'}
        ordinary = [child['id'] for child in children if child['id'] not in carriers
            and child['status'] not in terminal]
        # An unfinished native chain has future ordinary legs even when the
        # current leg just landed; an event wake must retain that barrier.
        if task['active_chain'] is not None:
            ordinary.append(task_id)
        inbox = _rows(conn, 'workflow_seq_inbox', budget, 'initiator_task_id=? AND NOT EXISTS '
            '(SELECT 1 FROM workflow_seq_consumed_notifications c WHERE '
            'c.initiator_task_id=workflow_seq_inbox.initiator_task_id '
            'AND c.notification_id=workflow_seq_inbox.notification_id)', (task_id,))
        unfinished = [run['id'] for run in runs if run['state'] != 'finished']
        final_result = None
        if not unfinished and not ordinary and task['current_session_id']:
            results = _rows(conn, 'task_results', budget, 'task_id=? AND agent=? AND session_id=?',
                (task_id, task['assigned_agent'], task['current_session_id']))
            if len(results) == 1:
                receipts = _rows(conn, 'workflow_seq_consumption_receipts', budget, 'result_id=?', (results[0]['id'],))
                decision = results[0]['decision_json']
                if receipts and decision and native_decision(decision.encode(), budget).get('action') in ('workflow_control', 'workflow_wait'):
                    final_result = results[0]['id']
        return dict(run_ids=[run['id'] for run in runs], unfinished_runs=unfinished,
            ordinary_outstanding=ordinary, pending=bool(inbox) or final_result is not None,
            continuation_result_id=final_result, carrier_task_ids=sorted(carriers),
            runs=[dict(run_id=run['id'], state=run['state'], progress=run['progress'],
                cas=run['cas'], held_event_id=run['held_event_id'], terminal_outcome=run['terminal_outcome'])
                for run in runs])


def _rows(conn: sqlite3.Connection, table: str, budget: _ReadBudget,
          where: str, args: tuple) -> list[dict]:
    from runtime.workflows.sequential_engine import _engine_rows
    return _engine_rows(conn, table, budget, where, args,
        omit=('estimated_cost',) if table == 'task_results' else ())


def _id(kind: str, *parts: str) -> str:
    from runtime.workflows.sequential_engine import _identity
    return _identity(kind, *parts)


def _audience(conn: sqlite3.Connection, task_id: str, principal: str, session_id: str,
              budget: _ReadBudget, *, current: bool) -> dict:
    _require(conn.in_transaction and all(type(value) is str and value and '\x00' not in value
        for value in (task_id, principal, session_id)), 'initiator_identity_invalid')
    tasks = _rows(conn, 'tasks', budget, 'id=?', (task_id,))
    _require(len(tasks) == 1 and tasks[0]['assigned_agent'] == principal, 'initiator_identity_conflict')
    task = tasks[0]
    if current:
        _require(task['current_session_id'] == session_id and task['status'] == 'in_progress'
            and task['cancelled_at'] is None and task['completed_at'] is None, 'initiator_session_conflict')
    wait = _rows(conn, 'workflow_seq_source_wait_sets', budget, 'source_task_id=?', (task_id,))
    _require(len(wait) == 1 and wait[0]['source_principal'] == principal
        and selected_owner(conn, task_id, wait[0]['org_slug']) == ('seq_source_wait_set', wait[0]['id'], None),
        'initiator_wait_set_conflict')
    return wait[0]


def _page(conn: sqlite3.Connection, task_id: str, session_id: str, budget: _ReadBudget) -> dict:
    deliveries = _rows(conn, 'workflow_seq_inbox_deliveries', budget,
        'initiator_task_id=? AND session_id=?', (task_id, session_id))
    _require(len(deliveries) <= 32, 'initiator_page_bounds')
    ids = sorted(row['notification_id'] for row in deliveries)
    events = []
    for key in ids:
        rows = _rows(conn, 'workflow_seq_inbox', budget,
            'initiator_task_id=? AND notification_id=?', (task_id, key))
        _require(len(rows) == 1, 'initiator_delivery_conflict')
        events.append(_decode(_hashed(rows[0]['notification_bytes'], rows[0]['notification_digest']), budget))
    return dict(notifications=events, cursor=ids[-1] if ids else None)


def delivered_page(conn: sqlite3.Connection, initiator_task_id: str, principal: str,
                   session_id: str) -> dict:
    """Read ONLY the actual SID's stored deliveries, including an empty page."""
    budget = _ReadBudget()
    with _sql_budget(conn, budget):
        _audience(conn, initiator_task_id, principal, session_id, budget, current=True)
        return _page(conn, initiator_task_id, session_id, budget)


def snapshot_delivery(conn: sqlite3.Connection, initiator_task_id: str, principal: str,
                      session_id: str, *, cursor: str | None = None) -> dict:
    """Call exactly ONCE per genuinely admitted SID, in its admission writer.

    Existing nonempty delivery rows replay without writes. Zero rows cannot
    distinguish an empty snapshot from no call: S6a's single call site owns that
    write-once contract. All later consumers MUST use delivered_page instead.
    Delivery is not consumption; unacknowledged events can reach a later SID.
    """
    budget = _ReadBudget()
    with _sql_budget(conn, budget):
        audience = _audience(conn, initiator_task_id, principal, session_id, budget, current=True)
        _require(cursor is None or (type(cursor) is str and cursor and '\x00' not in cursor), 'initiator_cursor_invalid')
        page = _page(conn, initiator_task_id, session_id, budget)
        if page['notifications']:
            return page
        # Indexed ascending notification order. The bounded acquisition retains
        # its original row/byte/SQL ledger; no unbounded page is fetched.
        from runtime.workflows.coding_authority_history import _bounded_rows
        keys = list(_bounded_rows(conn, 'workflow_seq_inbox', ('notification_id',), (('text',),), budget,
            where='initiator_task_id=? AND notification_id IN (SELECT i.notification_id FROM workflow_seq_inbox i '
                'WHERE i.initiator_task_id=? AND (? IS NULL OR i.notification_id>?) AND NOT EXISTS '
                '(SELECT 1 FROM workflow_seq_consumed_notifications c WHERE '
                'c.initiator_task_id=i.initiator_task_id AND c.notification_id=i.notification_id) '
                'ORDER BY i.notification_id LIMIT 32)', args=(initiator_task_id, initiator_task_id, cursor, cursor), maximum=32))
        for row in keys:
            conn.execute('INSERT INTO workflow_seq_inbox_deliveries VALUES (?,?,?)',
                         (initiator_task_id, session_id, row[0]))
        _validate_write(conn, audience['org_slug'], budget)
        return _page(conn, initiator_task_id, session_id, budget)


def _validate_write(conn: sqlite3.Connection, org_slug: str, budget: _ReadBudget) -> None:
    from runtime.workflows.sequential_control import validate_control_write
    validate_control_write(conn, org_slug=org_slug, deadline=budget.deadline, reserve_cancellation=True)


def native_decision(raw: bytes, budget: _ReadBudget) -> dict:
    """Preserve the existing native assessment carrier, including finite floats.

    Workflow command models remain integer-only. This is the retained native
    JSON/carrier decoder on the SAME caller budget, not a new parsing allowance.
    """
    from runtime.workflows.sequential_start_metadata import _carrier, _json
    value = _json(raw, budget)
    if '_manager_self_evaluation' in value:
        _carrier(value['_manager_self_evaluation'], budget)
    return value


def _decision(conn: sqlite3.Connection, task_id: str, principal: str, session_id: str,
              result_id: int, decision: bytes, budget: _ReadBudget) -> dict:
    _require(type(result_id) is int and result_id > 0 and type(decision) is bytes
        and 0 < len(decision) <= 1048576, 'initiator_result_invalid')
    _result(conn, result_id, task_id, principal, session_id, budget)
    value = native_decision(decision, budget)
    _require(_bytes(value) == decision, 'initiator_decision_canonical')
    rows = _rows(conn, 'task_results', budget, 'id=?', (result_id,),)
    _require(len(rows) == 1 and type(rows[0]['decision_json']) is str
        and _bytes(native_decision(rows[0]['decision_json'].encode('utf8'), budget)) == decision
        and rows[0]['status'] == 'completed', 'initiator_decision_conflict')
    return value


def consume_decision_uncommitted(conn: sqlite3.Connection, *, initiator_task_id: str, principal: str,
                                 session_id: str, result_id: int, decision: bytes,
                                 apply: Callable[[], dict]) -> bytes:
    """Authenticate deliveries, apply once, and receipt effects with the result.

    `apply` is the internal result consumer's effect writer, on this SAME
    transaction. Exact receipt replay returns saved effects without calling it.
    The caller rolls back the genuine result and effects together on any error.
    """
    _require(conn.in_transaction and conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1,
        'initiator_requires_owned_writer')
    budget = _ReadBudget()
    with _sql_budget(conn, budget):
        value = _decision(conn, initiator_task_id, principal, session_id, result_id, decision, budget)
        prior = _rows(conn, 'workflow_seq_consumption_receipts', budget, 'result_id=?', (result_id,))
        if prior:
            row = prior[0]
            _require((row['initiator_task_id'], row['principal'], row['session_id'], row['decision_bytes']) ==
                (initiator_task_id, principal, session_id, decision), 'initiator_receipt_conflict')
            _hashed(row['decision_bytes'], row['decision_digest'])
            return _hashed(row['receipt_bytes'], row['receipt_digest'])
        audience = _audience(conn, initiator_task_id, principal, session_id, budget, current=True)
        delivered = _rows(conn, 'workflow_seq_inbox_deliveries', budget,
            'initiator_task_id=? AND session_id=?', (initiator_task_id, session_id))
        ids = sorted(row['notification_id'] for row in delivered)
        consumed = value.get('consume_notifications', ids)
        _require(type(consumed) is list and len(consumed) <= 32
            and all(type(key) is str and key in ids for key in consumed) and len(set(consumed)) == len(consumed), 'initiator_consume_not_delivered')
        for key in consumed:
            _require(not _rows(conn, 'workflow_seq_consumed_notifications', budget,
                'initiator_task_id=? AND notification_id=?', (initiator_task_id, key)), 'initiator_already_consumed')
        effects = apply()
        _require(type(effects) is dict, 'initiator_effects_invalid')
        raw = _bytes(dict(format='workflow-sequential-decision-receipt@2',
            initiator_task_id=initiator_task_id, principal=principal, session_id=session_id,
            result_id=result_id, consume_notifications=consumed, effects=effects))
        _require(len(raw) <= 49152, 'initiator_receipt_bounds')
        budget.reserve(48 * len(raw) + 512)
        key = _id('receipt', initiator_task_id, session_id, str(result_id))
        conn.execute('INSERT INTO workflow_seq_consumption_receipts VALUES (?,?,?,?,?,?,?,?,?)',
            (key, initiator_task_id, principal, session_id, result_id, decision,
             hashlib.sha256(decision).hexdigest(), raw, hashlib.sha256(raw).hexdigest()))
        for notification in consumed:
            conn.execute('INSERT INTO workflow_seq_consumed_notifications VALUES (?,?,?,?)',
                         (initiator_task_id, notification, session_id, key))
        _validate_write(conn, audience['org_slug'], budget)
        return raw


def _wake_body(run: dict, event: dict, compiled: Any, visits: dict[str, dict], budget: _ReadBudget) -> dict | None:
    """Derive a bounded, causally authenticated wake from a stored event."""
    body = _decode(_hashed(event['event_bytes'], event['event_digest']), budget)
    kind, state, outcome, cause = None, None, None, None
    if event['kind'] == 'held':
        kind, state, cause = 'held', 'held', body['cause']
    elif event['kind'] == 'transition':
        edge = body['decision']['edge']
        if edge['action'] == 'hold':
            kind, state, cause = 'held', 'held', edge.get('cause') or 'agent_hold'
        elif edge['action'] in ('terminal', 'fail'):
            kind, state = ('failed' if edge['action'] == 'fail' else 'returned'), 'finished'
            outcome = 'failed' if edge['action'] == 'fail' else 'completed'
            cause = edge.get('code')
    if kind is None:
        return None
    visit = visits.get(event['source_visit_id'])
    _require(visit is not None, 'initiator_wake_visit_conflict')
    node = next(node for node in compiled.definition.nodes if node.id == visit['node_id'])
    return dict(format='workflow-sequential-initiator-wake@2', notification_id=_id('wake', event['id']),
        run_id=run['id'], event_id=event['id'], event_sequence=event['sequence'],
        initiator_task_id=run['source_task_id'], initiator_agent_id=run['source_principal'],
        kind=kind, state=state, terminal_outcome=outcome, node_id=node.id, role_id=node.role,
        visit_id=visit['id'], step_task_id=visit['task_id'], result_id=event['source_result_id'],
        cause=cause, diagnostic=cause or outcome, assignment_generation=visit['assignment_generation'],
        selected_output_refs=body.get('decision', {}).get('selections', []), effect_disposition='unverified')


def wake_uncommitted(conn: sqlite3.Connection, run: dict, event: dict, compiled: Any,
                     visits: dict[str, dict], budget: _ReadBudget) -> str | None:
    # S3/S4 singleton low-level histories remain supported without source adoption.
    memberships = _rows(conn, 'workflow_seq_source_memberships', budget, 'run_id=?', (run['id'],))
    if not memberships:
        return None
    body = _wake_body(run, event, compiled, visits, budget)
    if body is None:
        return None
    raw = _bytes(body)
    _require(len(raw) <= 16384, 'initiator_wake_bounds')
    prior = _rows(conn, 'workflow_seq_inbox', budget, 'event_id=?', (event['id'],))
    if prior:
        _require(prior[0]['notification_bytes'] == raw, 'initiator_wake_conflict')
    else:
        conn.execute('INSERT INTO workflow_seq_inbox VALUES (?,?,?,?,?,?,?)',
            (run['source_task_id'], body['notification_id'], run['source_principal'], run['id'],
             event['id'], raw, hashlib.sha256(raw).hexdigest()))
    return body['notification_id']


def hold_and_wake_uncommitted(conn: sqlite3.Connection, db: Any, *, org_slug: str, run_id: str,
                              visit_id: str, expected_cas: int, compiled: Any, authority: Any,
                              capture: Any, unavailable: bool = False) -> str:
    """S5 member hold wrapper: deferred event FK and wake share one COMMIT."""
    from runtime.workflows.sequential_business_hold import generic_hold_cause, hold_generic_uncommitted
    budget = _ReadBudget()
    with _sql_budget(conn, budget):
        run = _rows(conn, 'workflow_seq_runs', budget, 'id=?', (run_id,))[0]
        if _rows(conn, 'workflow_seq_source_memberships', budget, 'run_id=?', (run_id,)):
            visit = _rows(conn, 'workflow_seq_visits', budget, 'id=?', (visit_id,))[0]
            cause, proof = generic_hold_cause(conn, run=run, visit=visit, compiled=compiled,
                budget=budget, unavailable=unavailable)
            event_id = _id('event', run_id, str(run['event_count'] + 1))
            event_raw = _bytes(dict(cause=cause))
            event = dict(id=event_id, kind='held', sequence=run['event_count'] + 1,
                source_visit_id=visit_id, source_result_id=proof['result_id'], event_bytes=event_raw,
                event_digest=hashlib.sha256(event_raw).hexdigest())
            conn.execute('PRAGMA defer_foreign_keys=ON')
            wake_uncommitted(conn, run, event, compiled, {visit_id: visit}, budget)
        return hold_generic_uncommitted(conn, db, org_slug=org_slug, run_id=run_id,
            visit_id=visit_id, expected_cas=expected_cas, compiled=compiled, authority=authority,
            capture=capture, unavailable=unavailable)


def source_owner(conn: sqlite3.Connection, run: dict) -> bool:
    """Version-selected source relation; a wait set never selects one member."""
    owner = selected_owner(conn, run['source_task_id'], run['org_slug'])
    if owner == ('legacy', None, None):
        return True  # Retained unmounted S3/S4 singleton histories.
    if owner[0] != 'seq_source_wait_set':
        return False
    row = conn.execute('SELECT source_task_id,source_result_id,start_operation_key FROM '
        'workflow_seq_source_memberships WHERE wait_set_id=? AND run_id=?', (owner[1], run['id'])).fetchone()
    return row is not None and tuple(row) == (run['source_task_id'], run['source_result_id'], run['start_operation_key'])


def validate_initiator_history(conn: sqlite3.Connection, data: dict, runs: dict, visits: dict,
                               events: dict, definitions: dict, budget: _ReadBudget, org_slug: str) -> None:
    """Authenticate S5 normalized rows on the original complete-read budget."""
    waits = {row['id']: row for row in data['workflow_seq_source_wait_sets']}
    members = {row['run_id']: row for row in data['workflow_seq_source_memberships']}
    groups: dict[tuple[str, int], list[dict]] = {}
    for wait in waits.values():
        budget.step()
        _require(wait['org_slug'] == org_slug and wait['id'] == _id('wait-set', org_slug, wait['source_task_id'])
            and selected_owner(conn, wait['source_task_id'], org_slug) == ('seq_source_wait_set', wait['id'], None),
            'initiator_wait_set_conflict')
        _require(any(row['wait_set_id'] == wait['id'] for row in members.values()), 'initiator_membership_missing')
    for row in members.values():
        budget.step()
        run, wait = runs.get(row['run_id']), waits.get(row['wait_set_id'])
        _require(run is not None and wait is not None and run['engine_version'] == 2
            and (row['source_task_id'], row['source_result_id'], row['start_operation_key']) ==
                (run['source_task_id'], run['source_result_id'], run['start_operation_key'])
            and (wait['source_task_id'], wait['source_principal']) == (run['source_task_id'], run['source_principal']),
            'initiator_membership_conflict')
        groups.setdefault((run['source_task_id'], run['source_result_id']), []).append(run)
    for (_, result_id), group in groups.items():
        budget.work(len(group))
        _require(1 <= len(group) <= 8, 'initiator_batch_bounds')
        run = group[0]
        native = _rows(conn, 'task_results', budget, 'id=?', (result_id,))
        _require(len(native) == 1 and type(native[0]['decision_json']) is str
            and native[0]['status'] == 'completed' and native[0]['waiting_on_job_ids'] is None,
            'initiator_batch_result_conflict')
        value = native_decision(native[0]['decision_json'].encode('utf8'), budget)
        value.pop('_manager_self_evaluation', None)
        _require(set(value) in ({'action', 'workflows'}, {'action', 'workflows', 'consume_notifications'})
            and value['action'] == 'workflow_start' and type(value['workflows']) is list
            and len(value['workflows']) == len(group), 'initiator_batch_result_conflict')
        originals = {run['start_bytes'] for run in group}
        _require(len(originals) == len(group) and {_bytes(item) for item in value['workflows']} == originals,
            'initiator_batch_result_conflict')
    expected = {}
    for event in events.values():
        run = runs[event['run_id']]
        if run['id'] not in members:
            continue
        body = _wake_body(run, event, definitions[run['definition_id']], visits, budget)
        if body:
            expected[body['notification_id']] = body
    actual = {}
    for row in data['workflow_seq_inbox']:
        budget.step()
        body = _decode(_hashed(row['notification_bytes'], row['notification_digest']), budget)
        _require(len(row['notification_bytes']) <= 16384 and body == expected.get(row['notification_id'])
            and row['notification_bytes'] == _bytes(body)
            and (row['initiator_task_id'], row['initiator_principal'], row['run_id'], row['event_id']) ==
                (body['initiator_task_id'], body['initiator_agent_id'], body['run_id'], body['event_id']),
            'initiator_wake_conflict')
        actual[row['notification_id']] = body
    _require(actual.keys() == expected.keys(), 'initiator_wake_missing')
    deliveries = set()
    pages: dict[tuple[str, str], list[str]] = {}
    for row in data['workflow_seq_inbox_deliveries']:
        budget.step()
        body = actual.get(row['notification_id'])
        _require(body is not None and body['initiator_task_id'] == row['initiator_task_id'], 'initiator_delivery_conflict')
        deliveries.add((row['initiator_task_id'], row['session_id'], row['notification_id']))
        pages.setdefault((row['initiator_task_id'], row['session_id']), []).append(row['notification_id'])
    _require(all(len(ids) <= 32 for ids in pages.values()), 'initiator_page_bounds')
    expected_consumed = set()
    for row in data['workflow_seq_consumption_receipts']:
        budget.step()
        decision = _hashed(row['decision_bytes'], row['decision_digest'])
        value = _decision(conn, row['initiator_task_id'], row['principal'], row['session_id'], row['result_id'], decision, budget)
        raw = _hashed(row['receipt_bytes'], row['receipt_digest'])
        _require(len(raw) <= 49152, 'initiator_receipt_bounds')
        receipt = _decode(raw, budget)
        _require(set(receipt) == {'format', 'initiator_task_id', 'principal', 'session_id', 'result_id',
            'consume_notifications', 'effects'} and receipt['format'] == 'workflow-sequential-decision-receipt@2'
            and type(receipt['effects']) is dict and _bytes(receipt) == raw
            and all(receipt[key] == row[key] for key in ('initiator_task_id', 'principal', 'session_id', 'result_id'))
            and row['id'] == _id('receipt', row['initiator_task_id'], row['session_id'], str(row['result_id'])),
            'initiator_receipt_conflict')
        ids = receipt['consume_notifications']
        _require(type(ids) is list and len(ids) <= 32 and all(type(key) is str for key in ids) and len(set(ids)) == len(ids)
            and ids == value.get('consume_notifications', sorted(pages.get((row['initiator_task_id'], row['session_id']), []))),
            'initiator_receipt_conflict')
        for key in ids:
            _require((row['initiator_task_id'], row['session_id'], key) in deliveries, 'initiator_consume_not_delivered')
            expected_consumed.add((row['initiator_task_id'], key, row['session_id'], row['id']))
    _require({tuple(row[key] for key in ('initiator_task_id', 'notification_id', 'session_id', 'receipt_id'))
        for row in data['workflow_seq_consumed_notifications']} == expected_consumed, 'initiator_consumption_conflict')


def validate_source_outer(conn: sqlite3.Connection, run: dict, rows: list[dict], budget: _ReadBudget) -> None:
    """A source outer record retains its native source meaning, never a SID page."""
    _require(len(rows) <= 1, 'initiator_source_outer_conflict')
    for row in rows:
        value = _decode(_hashed(row['evidence_bytes'], row['evidence_digest']), budget)
        _require(row['kind'] == 'outer' and row['visit_id'] is None and value.keys() == {
            'format', 'invocation_id', 'task_id', 'principal', 'session_id', 'result_id', 'positive', 'cause'}
            and value['format'] == 'workflow-sequential-source-outer@2'
            and (value['task_id'], value['principal'], value['session_id'], value['result_id']) ==
                (run['source_task_id'], run['source_principal'], run['source_session_id'], run['source_result_id'])
            and type(value['invocation_id']) is str and bool(value['invocation_id'])
            and type(value['positive']) is bool and value['cause'] == (None if value['positive'] else 'source_outer_unsettled')
            and _bytes(value) == row['evidence_bytes'], 'initiator_source_outer_conflict')
        _require(source_owner(conn, run), 'initiator_source_outer_conflict')


def source_released(conn: sqlite3.Connection, run: dict) -> bool:
    from runtime.workflows.sequential_dispatch import _evidence
    body = _evidence(conn, run['id'], None, 'outer')
    return body is not None and body.get('positive') is True


def settle_source_runs(dispatcher: Any, invocation: Any, *, failed: bool, cleanup: Any,
                       native_entry: dict | None, close_disposition: dict | None,
                       release_disposition: dict | None) -> None:
    """One original source settlement releases every authenticated batch member."""
    from runtime.workflows.sequential_dispatch import _evidence
    from runtime.workflows.sequential_start import _source_outer_positive
    org, budget = dispatcher.org, _ReadBudget()
    ready = []
    with dispatcher._writer() as conn, _sql_budget(conn, budget):
        wait = _audience(conn, invocation.task.id, invocation.agent, invocation.session_id, budget, current=False)
        members = _rows(conn, 'workflow_seq_source_memberships', budget, 'wait_set_id=?', (wait['id'],))
        selected = []
        for member in members:
            runs = _rows(conn, 'workflow_seq_runs', budget, 'id=?', (member['run_id'],))
            _require(len(runs) == 1, 'initiator_source_outer_conflict')
            run = runs[0]
            _require((run['org_slug'], run['source_task_id'], run['source_principal'],
                run['source_result_id'], run['start_operation_key']) == (org.slug, invocation.task.id,
                invocation.agent, member['source_result_id'], member['start_operation_key'])
                and member['source_task_id'] == invocation.task.id, 'initiator_source_outer_conflict')
            if run['source_session_id'] != invocation.session_id:
                continue  # Earlier batches keep their original settlement.
            _result(conn, run['source_result_id'], invocation.task.id, invocation.agent, invocation.session_id, budget)
            selected.append(run)
        if not selected:
            return  # A later ordinary SID has no source batch to release.
        positive = _source_outer_positive(dispatcher, invocation, failed=failed, cleanup=cleanup,
            native_entry=native_entry, close_disposition=close_disposition, release_disposition=release_disposition)
        if positive:
            _audience(conn, invocation.task.id, invocation.agent, invocation.session_id, budget, current=True)
        for run in selected:
            prior = _evidence(conn, run['id'], None, 'outer')
            body = dict(format='workflow-sequential-source-outer@2', invocation_id=invocation.identity,
                task_id=invocation.task.id, principal=invocation.agent, session_id=invocation.session_id,
                result_id=run['source_result_id'], positive=positive,
                cause=None if positive else 'source_outer_unsettled')
            _evidence(conn, run['id'], None, 'outer', body)
            if prior is None and positive and run['state'] == 'running' and run['progress'] == 'queued':
                visit = _rows(conn, 'workflow_seq_visits', budget, 'id=?', (run['active_visit_id'],))[0]
                _require(visit['state'] == 'queued' and visit['session_id'] is None, 'initiator_source_outer_conflict')
                ready.append(visit['task_id'])
        _validate_write(conn, org.slug, budget)
    # Every proof commits before publication. Recovery replays the durable outbox;
    # queue insertion never acknowledges the source's inbox.
    for task_id in ready:
        dispatcher.notify_queued(task_id, org.orchestrator._queue)
