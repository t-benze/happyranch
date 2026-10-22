"""Coding cancellation composes the existing native stop, never execution clearance.

The public task route owns its authentication and post-COMMIT controls/tails.
This owner rediscovers the complete task/coding closure in its actual writer.
"""
from __future__ import annotations

from contextlib import ExitStack
from typing import Any

from runtime.models import BlockKind, TaskStatus
from runtime.workflows.coding_authority_history import _Budget
from runtime.workflows.coding_bootstrap import encode_row, identity
from runtime.workflows.coding_revision import _copy_allowance
from runtime.workflows.coding_saved_records import _canonical, _check, _hash
from runtime.workflows.coding_start_history import rows

TERMINAL = ('completed', 'failed', 'cancelled', 'superseded')


def affected(conn: Any, task_id: str, cascade: bool, saved: Any, budget: _Budget) -> tuple[dict, set]:
    """Fixed-point task descendants AND complete coding owners; no caller proof."""
    pending, selected, instances = [task_id], {}, set()
    while pending:
        budget.step()
        key = pending.pop()
        if key in selected:
            continue
        found = list(rows(conn, 'tasks', budget, where='id=?', args=(key,), maximum=1))
        _check(len(found) == 1 and len(selected) < 512)
        row = found[0]
        selected[key] = row
        owner = saved.owners.get(key)
        if owner is not None and owner[1] not in instances:
            instances.add(owner[1])
            pending.extend(saved.runs[owner[1]]['tasks'])
        if cascade and row['status'] not in TERMINAL:
            pending.extend(r['id'] for r in rows(conn, 'tasks', budget,
                where='parent_task_id=?', args=(key,), maximum=512))
    return selected, instances


def cancel_locked(org: Any, task_id: str, *, cascade: bool, rationale: str,
                  actor: str, transitions: Any) -> dict | None:
    """Return None for legacy-only work; no task write occurs on that path.

    Called under org.db_lock. The native lock and immediate transaction cover
    discovery, original audit, all graphs and fresh independent target validation.
    No current start authority/readiness is required to stop retained ownership.
    """
    from runtime.workflows.coding_dispatch import stamp
    from runtime.orchestrator.run_step import _capture_root_worktree_transition
    from runtime.infrastructure.workflow_schema import _read_workflow_schema
    db = org.db
    with db._lock:
        conn = db._conn
        if conn.execute("SELECT 1 FROM sqlite_schema WHERE name='workflow_coding_runs' AND type='table'").fetchone() is None:
            from runtime.infrastructure.workflow_coding_schema import validate_coding_data
            validate_coding_data(conn, layout='recovery', expected_org_slug=org.slug)
            return None
        _check(not conn.in_transaction)
        conn.execute('BEGIN IMMEDIATE')
        budget = _Budget()
        try:
            # Full schema + native ownership, not just a run selected by target.
            _, inventory = _read_workflow_schema(conn, expected_org_slug=org.slug, budget=budget)
            _check(inventory is not None)
            saved = inventory.queued
            # Reverse dependencies come from complete authenticated start/edge
            # captures, independently of the requested mutation traversal.
            _check(cascade or task_id in saved.owners
                   or task_id not in saved.cancellation_dependencies)
            selected, instances = affected(conn, task_id, cascade, saved, budget)
            if not instances:
                _check(not (selected.keys() & saved.cancellation_dependencies))
                conn.rollback()
                return None
            root = selected[task_id]
            if root['status'] in TERMINAL:
                _check(root['status'] == 'cancelled' and task_id in saved.owners)
                _check(saved.runs[saved.owners[task_id][1]]['native'].get('cancellation') is not None)
                conn.rollback()
                return dict(tasks=[], prior={}, block_kinds={}, coding_tasks=[])
            # Direct document owners retain their own route. An ancestor must
            # never mutate their graph through this coding-only transaction.
            for key in selected:
                budget.step()
                for table in ('workflow_draft_dispatch_intents', 'workflow_request_task_bridges'):
                    _check(conn.execute(f'SELECT 1 FROM {table} WHERE task_id=? LIMIT 1', (key,)).fetchone() is None)
                if key not in saved.owners:
                    _check(conn.execute('SELECT 1 FROM workflow_instances WHERE root_task_id=? LIMIT 1', (key,)).fetchone() is None)
            live = {k: v for k, v in selected.items() if v['status'] not in TERMINAL}
            _check(live)
            fresh = {i for i in instances if saved.runs[i]['run']['state'] != 'cancelled'}
            # Retained graphs still belong to the complete closure, but never
            # receive another cancellation event, task write or control.
            for instance in instances - fresh:
                _check(all(t['status'] in TERMINAL for t in saved.runs[instance]['tasks'].values()))
            for instance in fresh:
                entry = saved.runs[instance]
                _check(entry['run']['state'] != 'complete'
                       and entry['run']['cas_version'] < min(4096, entry['source']['budgets']['coding_event_limit']))
            from runtime.infrastructure.task_pause_controls import decode_journal
            native_owners = []
            if conn.execute("SELECT 1 FROM sqlite_schema WHERE name='task_pause_controls' AND type='table'").fetchone() is not None:
                for pause in rows(conn, 'task_pause_controls', budget):
                    for native in decode_journal(pause['admission_journal_json'])['entries']:
                        budget.step()
                        if native['task_id'] in selected:
                            native_owners.append(native)
            _check(len(native_owners) <= 512 and len({n['id'] for n in native_owners}) == len(native_owners))
            native_owners.sort(key=lambda n: n['id'])
            # Native preparation has effects before a control/PID is published.
            # Neither its absence nor a later successful control settles it.
            teardown_incomplete = bool(native_owners)
            for instance in instances:
                native = saved.runs[instance]['native']
                teardown_incomplete |= not native['source_settled'] or native.get('ownership_unknown', False)
                for state in native['stages'].values():
                    budget.step()
                    owned = state['claim'] is not None or state.get('queue_owner') is not None or state['host'] is not None
                    teardown_incomplete |= owned and not (state['cleanup_recorded'] and state['cleanup'])
            now = stamp()
            command = dict(format='workflow-coding-cancellation-command@1', target_task_id=task_id,
                cascade=cascade, actor=actor, rationale=rationale, cancelled_at=now, native_owners=native_owners,
                tasks=[dict(id=k, record=encode_row(v)) for k, v in sorted(selected.items())],
                owners=[dict(task_id=k, kind=owner[0], instance_id=owner[1])
                    for k, owner in sorted(saved.owners.items()) if owner[1] in instances])
            _copy_allowance(command, budget)
            note = f'cancelled by {actor}: {rationale}' if rationale else f'cancelled by {actor}'
            audits = []
            # Keep capture contexts alive THROUGH COMMIT. A failed validator
            # rolls back before capture exits, so no terminal tail is published.
            with ExitStack() as captures:
                for key in sorted(live):
                    captures.enter_context(_capture_root_worktree_transition(org.orchestrator, key,
                        'coding_cancellation', transitions))
                captures.callback(lambda: conn.rollback() if conn.in_transaction else None)
                for key in sorted(live):
                    conn.execute("UPDATE tasks SET status='cancelled',block_kind=NULL,note=?,cancelled_at=?,completed_at=?,updated_at=? WHERE id=?",
                        (note, now, now, now, key))
                    audit_id = db.insert_audit_log_uncommitted(key, actor, 'task_cancelled',
                        dict(rationale=rationale, cascade=cascade))
                    audit = list(rows(conn, 'audit_log', budget, where='id=?', args=(audit_id,), maximum=1))
                    _check(len(audit) == 1)
                    audits.append(dict(table='audit_log', id=audit_id, record=encode_row(audit[0])))
                for instance in sorted(fresh):
                    entry = saved.runs[instance]
                    previous = entry['native']['history'][-1]
                    principal = 'service:coding-cancellation:' + org.slug
                    digest = _hash(_canonical(command))
                    key = 'coding-cancel:' + digest
                    event_id = identity('event', org.slug, instance, principal, key)
                    # The additive family stores the original complete task
                    # closure once. Replay derives every target row, rather than
                    # persisting duplicate full after-images as supposed proof.
                    for a in entry['attempts'].values():
                        if a['task_id'] in live:
                            conn.execute("UPDATE workflow_coding_attempts SET state='cancelled' WHERE id=?", (a['id'],))
                    payload = dict(format='workflow-coding-cancellation-event@1', id=event_id, instance_id=instance,
                        event_seq=previous['event_seq'] + 1, event_kind='cancelled', attempt_id=entry['attempt']['id'],
                        operation=dict(org_slug=org.slug, principal=principal, key=key, request_digest=digest),
                        previous_digest=previous['event_digest'],
                        before=dict(cas_version=entry['run']['cas_version'], last_event_id=previous['id']),
                        after=dict(state='cancelled'),
                        evidence=dict(command=command, audits=audits,
                            native_task_observations=entry['native']['pending_observations']), created_at=now)
                    _copy_allowance(payload, budget)
                    raw = _canonical(payload)
                    _check(len(raw) <= 16777216)
                    conn.execute('INSERT INTO workflow_coding_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (event_id, instance, payload['event_seq'], entry['attempt']['id'], org.slug, principal, key,
                         digest, 'cancelled', previous['event_digest'], raw, _hash(raw), now))
                    conn.execute("UPDATE workflow_coding_runs SET state='cancelled',cas_version=?,last_event_id=? WHERE instance_id=?",
                        (payload['event_seq'], event_id, instance))
                if not fresh:
                    # No coding graph may be fenced twice just to carry an
                    # ordinary ancestor stop. Its own closed task-scoped audit
                    # anchors the earlier genuine fences without modifying them.
                    from runtime.workflows._coding_cancellation_history import ordinary_overlay
                    overlay = ordinary_overlay(org.slug, command, audits, saved, instances)
                    _copy_allowance(overlay, budget)
                    _check(len(_canonical(overlay)) <= 1048576)
                    db.insert_audit_log_uncommitted(task_id, actor, 'coding_ordinary_cancellation', overlay)
                _read_workflow_schema(conn, expected_org_slug=org.slug, budget=budget)
                conn.commit()
            return dict(teardown_incomplete=teardown_incomplete, tasks=sorted(live), coding_tasks=sorted(set(live) & saved.owners.keys()), prior={k: TaskStatus(v['status']) for k, v in live.items()},
                        block_kinds={k: None if v['block_kind'] is None else BlockKind(v['block_kind']) for k, v in live.items()})
        except BaseException:
            conn.rollback()
            raise
