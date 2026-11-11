"""Unmounted original-owner cancellation disposition; never root clearance.

Only the native settle-live caller may supply its actual close/release arguments.
This SELECT-only interface neither persists them nor substitutes for aggregation
of every original owner and the existing root-worktree protection checks.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from runtime.infrastructure.task_pause_controls import decode_journal
from runtime.workflows.coding_authority_history import _Budget
from runtime.workflows.sequential_engine import _engine_rows
from runtime.workflows.sequential_history import _ReadBudget, _bytes, _require, _sql_budget, read_sequential_history
from runtime.workflows.sequential_mixed_history import MixedSequentialHistory


@dataclass(frozen=True)
class CancelledOwnerDisposition:
    """Ephemeral observation of ONE original owner, not a reusable receipt."""

    run_id: str
    task_id: str
    invocation_id: str
    settled: bool


def _selected(conn: sqlite3.Connection, org: Any, invocation: Any, budget: _ReadBudget,
              retained_budget: _Budget) -> tuple[str, str]:
    history = read_sequential_history(conn, expected_org_slug=org.slug,
        retained_budget=retained_budget, deadline=budget.deadline, task_id=invocation.task.id)
    _require(isinstance(history, MixedSequentialHistory) and history.task_history is not None,
             'cancelled_owner_history_required')
    native = history.task_history
    budget.reserve(512 * len(native.owners))
    budget.work(len(native.owners))
    owners = [owner for owner in native.owners if owner[0] == invocation.task.id]
    _require(len(owners) == 1 and owners[0][1] in ('seq_source_waiting', 'seq_visit'),
             'cancelled_owner_kind_invalid')
    _, kind, owner_id, run_id = owners[0]
    with _sql_budget(conn, budget):
        runs = _engine_rows(conn, 'workflow_seq_runs', budget, 'id=?', (run_id,))
        _require(len(runs) == 1 and runs[0]['state'] == 'cancelled', 'cancelled_owner_stop_required')
        run = runs[0]
        if kind == 'seq_source_waiting':
            expected = (run['source_task_id'], run['source_principal'], run['source_session_id'])
            _require(invocation.owner in ('ordinary', 'v2') and owner_id == run_id,
                     'cancelled_owner_binding_conflict')
        else:
            visits = _engine_rows(conn, 'workflow_seq_visits', budget, 'id=?', (owner_id,))
            _require(len(visits) == 1 and visits[0]['run_id'] == run_id
                     and invocation.owner == 'sequential'
                     and invocation.workflow_dispatcher is org.workflow_sequential_dispatch,
                     'cancelled_owner_binding_conflict')
            expected = (visits[0]['task_id'], visits[0]['principal'], visits[0]['session_id'])
    _require(expected == (invocation.task.id, invocation.agent, invocation.session_id)
             and type(invocation.session_id) is str and bool(invocation.session_id),
             'cancelled_owner_binding_conflict')
    columns = native.task_columns
    budget.reserve(512 * len(native.tasks) + 128 * len(columns))
    budget.work(len(native.tasks))
    rows = {row[0]: row for row in native.tasks}
    task = dict(zip(columns, rows[invocation.task.id], strict=True))
    _require((task['assigned_agent'], task['current_session_id']) == expected[1:]
             and task['status'] in ('completed', 'failed', 'cancelled', 'superseded')
             and (task['status'] != 'cancelled' or task['cancelled_at'] is not None),
             'cancelled_owner_native_conflict')
    root = invocation.task.id
    while rows[root][columns.index('parent_task_id')] is not None:
        budget.step()
        root = rows[root][columns.index('parent_task_id')]
    return run_id, root


def cancelled_owner_disposition_uncommitted(conn: sqlite3.Connection, org: Any, invocation: Any, *,
        failed: bool, cleanup: Any, native_entry: dict | None, close_disposition: dict | None,
        release_disposition: dict | None, budget: _ReadBudget, retained_budget: _Budget) -> CancelledOwnerDisposition:
    """Qualify original native settlement without writing or removing ownership.

    Caller owns the original DB lock/snapshot and passes its original allowances.
    Missing observations yield unsettled; malformed identity/history refuses.
    A positive result MUST NOT clear a batch's teardown-incomplete flag: other
    captured owners, controls, uncertainty and root protections still apply.
    No production caller is attached in U6b2a.
    """
    from runtime.orchestrator.task_pause import TaskInvocation, fingerprint
    from runtime.workflows.sequential_dispatch import _native_metadata
    from runtime.platform.session_backend import TerminalReason

    budget.reserve(512)
    _require(conn is org.db._conn and conn.in_transaction and org.db._lock._is_owned()
             and isinstance(invocation, TaskInvocation) and invocation.orch is org.orchestrator
             and invocation.store is org.db._task_pause_store
             and invocation.store.org_slug == org.slug
             and org.workflow_sequential_dispatch is org.orchestrator._workflow_sequential_dispatch
             and org.sessions is org.orchestrator._sessions and type(failed) is bool,
             'cancelled_owner_snapshot_required')
    run_id, root = _selected(conn, org, invocation, budget, retained_budget)

    def disposition(settled: bool) -> CancelledOwnerDisposition:
        budget.check()
        return CancelledOwnerDisposition(run_id, invocation.task.id, invocation.identity, settled)

    terminal = getattr(invocation, 'native_terminal_observation', None)
    if not (type(native_entry) is dict and type(close_disposition) is dict
            and type(release_disposition) is dict and type(terminal) is dict):
        return disposition(False)
    # Bound all supplied native metadata before encoding/decoding or comparing.
    # This uses the existing finite native metadata ceiling, never a new budget.
    size = _native_metadata(invocation.task.id, invocation.agent, invocation.session_id,
        dict(native=native_entry, close=close_disposition, release=release_disposition, terminal=terminal))
    budget.reserve(48 * size + 512)
    budget.work(size)
    entry = decode_journal(_bytes(dict(version=1, entries=[native_entry])).decode('utf8'))['entries'][0]
    _require((entry['id'], entry['org'], entry['root_task_id'], entry['task_id'], entry['agent'],
              entry['session_id'], entry['owner']) == (invocation.identity, org.slug, root,
              invocation.task.id, invocation.agent, invocation.session_id, invocation.owner)
             and entry['fingerprint'] == fingerprint(invocation.task)
             and entry['owner_identity'] == {key: invocation.metadata[key] for key in
                 ('authority_v2_generation', 'trigger', 'triggering_job_id') if key in invocation.metadata}
             and close_disposition.get('invocation') == native_entry
             and (release_disposition.get('owner_id'), release_disposition.get('task_id'),
                  release_disposition.get('agent'), release_disposition.get('session_id')) ==
                 (invocation.identity, invocation.task.id, invocation.agent, invocation.session_id),
             'cancelled_owner_outer_conflict')
    request, receipt = terminal.get('request'), terminal.get('receipt')
    if not (failed is False and cleanup is not None and cleanup.kind == 'safe' and not cleanup.processes
            and invocation.unsettled is False and invocation.admitted is True
            and invocation.attempt_committed is True and invocation.recovery_deadline is None
            and entry['phase'] == 'result_processing' and not entry['context'].get('execution_unknown')
            and close_disposition.get('removed') is True and release_disposition.get('removed') is True
            and release_disposition.get('owner_live') is False and release_disposition.get('cleanup_failed') is False
            and type(release_disposition.get('possible_launch')) is bool
            and (release_disposition['possible_launch'] is False or release_disposition.get('quiescent') is True)
            and type(request) is dict and type(receipt) is dict and terminal.get('cleanup_error') is None
            and terminal.get('terminal_reason') in tuple(reason.value for reason in TerminalReason)
            and receipt.get('terminal_reason') == terminal['terminal_reason']
            and receipt.get('backend') in ('linux-systemd-cgroup-v2', 'macos-process-group')
            and receipt.get('quiescent') is True and receipt.get('survivors') == []
            and receipt.get('cleanup_status') in ('clean', 'term', 'kill')):
        return disposition(False)
    _require((request.get('org'), request.get('invocation_kind'), request.get('agent_id')) ==
             (org.slug, 'task', invocation.agent) and type(terminal.get('attempt')) is int
             and terminal['attempt'] >= 0 and type(request.get('retry_attempt')) is int
             and request['retry_attempt'] == terminal['attempt'], 'cancelled_owner_terminal_conflict')
    if invocation.owner == 'sequential':
        original = getattr(invocation, 'coding_request', None)
        running = getattr(invocation, 'coding_running', None)
        if original is None or running is None:
            return disposition(False)
        _require((original.org, original.invocation_kind, original.logical_id, original.agent_id,
                  original.retry_attempt, running.request_id, running.backend) ==
                 (org.slug, 'task', invocation.draft_request_key, invocation.agent, 0,
                  invocation.draft_request_key, receipt['backend'])
                 and request == dict(org=original.org, invocation_kind=original.invocation_kind,
                     logical_id=original.logical_id, executor_profile=original.executor_profile,
                     enqueued_at=original.enqueued_at, retry_attempt=original.retry_attempt, agent_id=original.agent_id)
                 and terminal['attempt'] == 0
                 and type(running.token) is str and bool(running.token), 'cancelled_owner_terminal_conflict')
    else:
        _require(request.get('logical_id') == invocation.task.id, 'cancelled_owner_terminal_conflict')
    with _sql_budget(conn, budget):
        rows = _engine_rows(conn, 'task_pause_controls', budget, 'root_task_id=?', (root,))
        if not rows:
            return disposition(False)
        _require(len(rows) == 1, 'cancelled_owner_journal_conflict')
        budget.reserve(24 * len(rows[0]['admission_journal_json'].encode('utf8')) + 512)
        # Reuse the native control reader's generation/identity semantics. In
        # particular, original entry generation need not equal today's Pause.
        pause = invocation.store.row_uncommitted(root)
        _require(pause is not None and entry['generation'] <= pause['generation'],
                 'cancelled_owner_journal_conflict')
        for current in pause['journal']['entries']:
            budget.step()
            if current['id'] == invocation.identity or current['task_id'] == invocation.task.id:
                return disposition(False)
    reservation = invocation.move_reservation
    if reservation is None:
        return disposition(False)
    _require((reservation.owner_id, reservation.task_id, reservation.identity.agent_id, reservation.session_id) ==
             (invocation.identity, invocation.task.id, invocation.agent, invocation.session_id)
             and all(release_disposition[key] == getattr(reservation, key) for key in
                 ('owner_live', 'cleanup_failed', 'possible_launch', 'quiescent')),
             'cancelled_owner_release_conflict')
    if (invocation.identity in invocation.store._live
            or invocation.identity in org.orchestrator._team_moves._reservations
            or org.sessions.get_active(invocation.task.id, invocation.agent) is not None
            or org.sessions.iter_task_cancel_controls(invocation.task.id)
            or org.sessions.iter_task_pids(invocation.task.id)):
        return disposition(False)
    return disposition(True)
