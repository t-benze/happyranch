"""Selected-run business stop; native containment remains with original owners."""
from __future__ import annotations

import hashlib
import sqlite3
from contextlib import ExitStack
from datetime import datetime, timezone
from typing import Any

from runtime.infrastructure.workflow_schema import _structure_cells
from runtime.infrastructure.workflow_task_ownership import selected_owner
from runtime.models import BlockKind, TaskStatus
from runtime.workflows.sequential_definition import compile_definition
from runtime.workflows.sequential_engine import SequentialRefusal, _engine_rows, cancel_fence_uncommitted
from runtime.workflows.sequential_history import _ReadBudget, _bytes, _native_rows, _sql_budget
from runtime.workflows.sequential_run_validation import _selected_rows, validate_selected_run_uncommitted

_TERMINAL = frozenset(('completed', 'failed', 'cancelled', 'superseded'))
_KINDS = frozenset(('seq_source_waiting', 'seq_carrier', 'seq_visit'))
_TASK_COLUMNS = ('id', 'parent_task_id', 'status', 'block_kind', 'cancelled_at')


def _check(value: bool) -> None:
    if not value:
        raise SequentialRefusal('sequential_cancellation_reconciliation_required')


def _closure(conn: sqlite3.Connection, task_id: str, budget: _ReadBudget) -> dict[str, dict]:
    """Visit terminal dependency paths too; one allowance, 512 distinct rows."""
    metadata = tuple(_structure_cells(conn, 'SELECT * FROM pragma_table_xinfo(?)',
        ('cid', 'name', 'type', 'notnull', 'dflt_value', 'pk', 'hidden'), ('cid',), budget, ('tasks',)))
    columns = tuple(row[1] for row in metadata if row[1] in _TASK_COLUMNS)
    _check(set(columns) == set(_TASK_COLUMNS))
    found: dict[str, dict] = {}
    pending = [task_id]
    while pending:
        budget.step()
        key = pending.pop()
        _check(key not in found)
        rows = list(_selected_rows(conn, 'tasks', metadata, budget, columns=columns,
                                   where='id=?', args=(key,)))
        _check(len(rows) == 1 and len(found) < 512)
        found[key] = rows[0]
        # Refuse excess membership using a bounded scalar count BEFORE fetching
        # a 513th distinct task row. The parent index and original SQL budget
        # bound this observation too; it allocates no descendant task objects.
        remaining = 512 - len(found) - len(pending)
        budget.step()
        count = conn.execute('SELECT count(*) FROM (SELECT 1 FROM tasks '
            'WHERE parent_task_id=? LIMIT ?)', (key, remaining + 1)).fetchone()[0]
        budget.candidate()
        budget.reserve(32)
        _check(count <= remaining)
        for child in _selected_rows(conn, 'tasks', metadata, budget, columns=columns,
                                    where='parent_task_id=?', args=(key,)):
            _check(child['id'] not in found and child['id'] not in pending
                   and len(found) + len(pending) < 512)
            pending.append(child['id'])
    return found


def guard_ordinary_ancestor(org: Any, task_id: str) -> None:
    """Refuse sequential descendants before either Coding or ordinary mutation.

    Called under org.db_lock. Point stops also inspect the complete bounded
    subtree; this is a refusal gate, never a mixed cancellation implementation.
    """
    budget = _ReadBudget()
    with org.db.coherent_read_view() as conn, _sql_budget(conn, budget):
        for key in _closure(conn, task_id, budget):
            budget.step()
            _check(selected_owner(conn, key, org.slug)[0] not in _KINDS)


def _selected(conn: sqlite3.Connection, org_slug: str, task_id: str,
              budget: _ReadBudget) -> tuple[dict, dict, Any, dict[str, dict]]:
    kind, owner, _ = selected_owner(conn, task_id, org_slug)
    _check(kind in _KINDS)
    if kind == 'seq_visit':
        visits = _engine_rows(conn, 'workflow_seq_visits', budget, 'id=?', (owner,),
                             omit=('result_bytes', 'verified_checks_bytes'))
        _check(len(visits) == 1 and visits[0]['task_id'] == task_id)
        run_id = visits[0]['run_id']
    else:
        run_id = owner
    runs = _engine_rows(conn, 'workflow_seq_runs', budget, 'id=?', (run_id,))
    _check(len(runs) == 1 and runs[0]['org_slug'] == org_slug)
    run = runs[0]
    visits = _engine_rows(conn, 'workflow_seq_visits', budget, 'run_id=?', (run_id,),
                         omit=('result_bytes', 'verified_checks_bytes'))
    _check(bool(visits))
    current = max(visits, key=lambda row: row['ordinal'])
    _check((run['active_visit_id'] == current['id'] and current['selected'] == 1)
           or (run['state'] == 'cancelled' and run['active_visit_id'] is None
               and current['state'] == 'cancelled' and current['selected'] == 0))
    _check(kind != 'seq_visit' or owner == current['id'])
    pins = _engine_rows(conn, 'workflow_seq_definitions', budget, 'id=?', (run['definition_id'],))
    _check(len(pins) == 1)
    pin = pins[0]
    compiled = compile_definition(pin['definition_bytes'], source_digest=pin['source_digest'])
    _check(compiled.pin.definition_digest == pin['definition_digest'])
    tasks = _closure(conn, run['source_task_id'], budget)
    members = {run['source_task_id'], run['carrier_task_id'], *(visit['task_id'] for visit in visits)}
    _check(members <= tasks.keys() and task_id in members)
    live_members = {run['source_task_id'], run['carrier_task_id'], current['task_id']}
    for key, task in tasks.items():
        budget.step()
        TaskStatus(task['status'])
        if task['block_kind'] is not None:
            BlockKind(task['block_kind'])
        task_kind, task_owner, _ = selected_owner(conn, key, org_slug)
        if key in members:
            expected = ('seq_source_waiting', run_id) if key == run['source_task_id'] else (
                ('seq_carrier', run_id) if key == run['carrier_task_id'] else
                ('seq_visit', next(visit['id'] for visit in visits if visit['task_id'] == key)))
            _check((task_kind, task_owner) == expected)
        else:
            _check(task_kind == 'legacy')
        _check(task['status'] in _TERMINAL or key in live_members)
        _check(task['status'] in _TERMINAL or task['cancelled_at'] is None)
    return run, current, compiled, tasks


def _controls(org: Any, conn: sqlite3.Connection, source: str, tasks: dict[str, dict],
              budget: _ReadBudget) -> tuple[list[tuple], bool]:
    """Collect opaque controls only; never signal PIDs or clear native owners."""
    from runtime.orchestrator.task_pause import prepared_cancel_controls
    from runtime.infrastructure.task_pause_controls import decode_journal

    controls = []
    incomplete = False
    # Queue preparation can own a pending row before a control or PID exists.
    # Inspect its original root journal without clearing or settling it.
    ancestors: set[str] = set()
    key = source
    while True:
        _check(key not in ancestors and len(ancestors) < 512)
        ancestors.add(key)
        rows = _native_rows(conn, 'tasks', ('parent_task_id',), (('text', 'null'),), key, budget)
        _check(len(rows) == 1)
        if rows[0][0] is None:
            break
        key = rows[0][0]
    if getattr(org.db, '_task_pause_store', None) is not None:
        pauses = _engine_rows(conn, 'task_pause_controls', budget, 'root_task_id=?', (key,))
        _check(len(pauses) <= 1)
        for pause in pauses:
            for entry in decode_journal(pause['admission_journal_json'])['entries']:
                budget.step()
                incomplete |= entry['task_id'] in tasks
    for key, task in tasks.items():
        by_agent = dict(org.sessions.iter_task_cancel_controls(key))
        by_agent.update(prepared_cancel_controls(org.db, key))
        controls.extend((key, agent, control) for agent, control in by_agent.items())
        # An in-progress original owner has no positive close/release proof in
        # this stop operation. Even a successful control cannot establish it.
        incomplete |= task['status'] == 'in_progress' or bool(by_agent)
        incomplete |= bool(tuple(org.sessions.iter_task_pids(key)))
    return controls, incomplete


async def cancel_selected(org: Any, task_id: str, *, cascade: bool, actor: str,
                          rationale: str, transitions: Any) -> dict:
    """Fence and stamp one complete selected run in the owned admission writer."""
    from runtime.orchestrator.run_step import _capture_root_worktree_transition

    authority = org.workflow_authority
    _check(authority is not None)
    capture = authority.capture_admission()  # File/profile capture precedes writer locks.
    async with authority._async_writer_lock:
        async with org.teams_lock:
            async with org.db_lock:
                # Captures exit AFTER the admission writer commits or rolls back.
                with ExitStack() as captures:
                    with authority.admission_writer(capture) as conn:
                        budget = _ReadBudget()
                        with _sql_budget(conn, budget):
                            run, visit, compiled, tasks = _selected(conn, org.slug, task_id, budget)
                            key = 'seq-cancel-' + hashlib.sha256(_bytes(
                                [run['id'], task_id, cascade, actor, rationale])).hexdigest()
                            events = _engine_rows(conn, 'workflow_seq_events', budget,
                                'run_id=? AND operation_key=?', (run['id'], key))
                            live = {tid: task for tid, task in tasks.items() if task['status'] not in _TERMINAL}
                            if events:
                                _check(len(events) == 1 and events[0]['kind'] == 'cancelled'
                                       and run['state'] == 'cancelled' and not live
                                       and tasks[task_id]['status'] == 'cancelled')
                                expected_cas = events[0]['sequence'] - 2
                            else:
                                _check(run['state'] in ('active', 'held') and task_id in live)
                                _check(cascade or task_id == visit['task_id'] or set(live) == {task_id})
                                expected_cas = run['cas']
                            controls, incomplete = _controls(org, conn, run['source_task_id'], live, budget)
                        # The unchanged validator independently checks pins/history.
                        cancel_fence_uncommitted(conn, org.db, org_slug=org.slug, run_id=run['id'],
                            operation_key=key, expected_cas=expected_cas, compiled=compiled,
                            authority=authority, capture=capture)
                        now = datetime.now(timezone.utc).isoformat()
                        note = f'cancelled by {actor}: {rationale}' if rationale else f'cancelled by {actor}'
                        for tid in live:
                            captures.enter_context(_capture_root_worktree_transition(
                                org.orchestrator, tid, 'cancel_task', transitions))
                            conn.execute("UPDATE tasks SET status='cancelled',block_kind=NULL,note=?,"
                                'cancelled_at=?,completed_at=?,updated_at=? WHERE id=?', (note, now, now, now, tid))
                            org.db.insert_audit_log_uncommitted(tid, actor, 'task_cancelled',
                                                              dict(rationale=rationale, cascade=cascade))
                        validate_selected_run_uncommitted(conn, org_slug=org.slug, run_id=run['id'], scope='cancel')
                return dict(tasks=list(live), prior={tid: TaskStatus(t['status']) for tid, t in live.items()},
                    block_kinds={tid: BlockKind(t['block_kind']) if t['block_kind'] else None for tid, t in live.items()},
                    sequential_tasks=list(live), controls=controls, teardown_incomplete=incomplete)
