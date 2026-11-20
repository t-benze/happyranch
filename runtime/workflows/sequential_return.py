"""Committed sequential caller returns and guarded ordinary terminal thread tails.

The unchanged complete reader admits history before bounded indexed selection
in the same snapshot. Replaying an immutable outbox never acknowledges/deletes
it, and queue publication is not a durable caller execution acknowledgement.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from runtime.infrastructure.workflow_schema_load import sequential_storage_present
from runtime.workflows.coding_authority_history import _decoded
from runtime.workflows.sequential_definition import _no_constant, _unique_object
from runtime.workflows.sequential_engine import _engine_rows
from runtime.workflows.sequential_history import _ReadBudget, _sql_budget, read_sequential_history


def _returned(conn: sqlite3.Connection, run_id: str, budget: _ReadBudget) -> dict | None:
    """Select only an obligation already admitted by the complete reader."""
    runs = _engine_rows(conn, 'workflow_seq_runs', budget, 'id=?', (run_id,))
    if len(runs) != 1 or runs[0]['state'] != 'completed':
        return None
    run = runs[0]
    events = _engine_rows(conn, 'workflow_seq_events', budget, 'id=?', (run['last_event_id'],))
    if len(events) != 1 or events[0]['kind'] != 'returned' or events[0]['run_id'] != run_id:
        return None
    outbox = _engine_rows(conn, 'workflow_seq_outbox', budget, 'event_id=?', (events[0]['id'],))
    if (len(outbox) != 1 or outbox[0]['kind'] != 'run_return'
            or outbox[0]['run_id'] != run_id or outbox[0]['visit_id'] is not None
            or outbox[0]['task_id'] != run['source_task_id']):
        return None
    return run


def returned_source(db: Any, org_slug: str, run_id: str, task_id: str) -> bool:
    """Independently authorize the original source at the shared parent seam."""
    with db.coherent_read_view() as conn:
        history = read_sequential_history(conn, expected_org_slug=org_slug)
        if run_id not in history.run_ids:
            return False
        budget = _ReadBudget()
        with _sql_budget(conn, budget):
            run = _returned(conn, run_id, budget)
            return run is not None and run['source_task_id'] == task_id


def returned_run_ids(dispatcher: Any) -> tuple[str, ...]:
    """Rediscover actual returned obligations, including terminal sources."""
    with dispatcher.org.db.coherent_read_view() as conn:
        if not sequential_storage_present(conn, budget=_ReadBudget()):
            return ()
        history = read_sequential_history(conn, expected_org_slug=dispatcher.org.slug)
        budget = _ReadBudget()
        with _sql_budget(conn, budget):
            return tuple(run_id for run_id in history.run_ids if _returned(conn, run_id, budget) is not None)


class _ReturnedCallerQueue:
    """Adapt the supplied recovery queues without replacing the live owner."""

    def __init__(self, orchestrator: Any, queue: Any) -> None:
        self._original = orchestrator
        self._queue = queue
        self._thread_queue = queue.thread_queue

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)


def _marker_payload(raw: str, budget: _ReadBudget) -> dict:
    """Read ordinary writer JSON, whose whitespace is not a workflow ABI."""
    if type(raw) is not str or not 0 < len(raw) <= 1048576:
        raise ValueError('sequential_return_thread_marker_unavailable')
    budget.reserve(48 * len(raw) + 512)
    try:
        if len(raw.encode('utf8')) > 1048576:
            raise ValueError('sequential_return_thread_marker_unavailable')
        value = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_no_constant)
        if type(value) is not dict:
            raise ValueError('sequential_return_thread_marker_unavailable')
        _decoded(value, budget)
        return value
    except (UnicodeError, RecursionError) as exc:
        raise ValueError('sequential_return_thread_marker_unavailable') from exc


def _terminal_thread_tail(orch: Any, source_id: str) -> None:
    """Serialize this return producer against its stored ordinary SYSTEM marker.

    A marker records an attempted tail, including a partial cap/mint/queue tail.
    Unknown marker evidence refuses; it never authorizes a second attempt.
    """
    from runtime.infrastructure.thread_archive import notify_composed_threads_task_terminal
    from runtime.models import TaskStatus
    from runtime.orchestrator.run_step import _maybe_post_thread_followup

    db = orch._db
    with db._lock:
        task = db.get_task(source_id)
        if task is None or task.status not in (
                TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.SUPERSEDED):
            return
        if task.parent_task_id is None:
            try:
                budget = _ReadBudget()
                with _sql_budget(db._conn, budget):
                    key, seen, original = source_id, set(), None
                    while key is not None:
                        budget.step()
                        if key in seen or len(seen) >= 200:
                            raise ValueError('sequential_return_thread_lineage_unavailable')
                        seen.add(key)
                        rows = _engine_rows(db._conn, 'tasks', budget, 'id=?', (key,))
                        if len(rows) != 1:
                            raise ValueError('sequential_return_thread_lineage_unavailable')
                        original = rows[0]
                        key = original['revisit_of_task_id']
                    thread_id = original['dispatched_from_thread_id']
                    marked = False
                    if thread_id is not None:
                        for row in _engine_rows(db._conn, 'thread_messages', budget,
                                "thread_id=? AND kind='system'", (thread_id,)):
                            raw = row['system_payload_json']
                            if raw is None:
                                continue
                            payload = _marker_payload(raw, budget)
                            if payload.get('task_id') == source_id and payload.get('status') == task.status.value:
                                marked = True
                    # Complete the bounded read even after finding a marker:
                    # later unreadable evidence is not an absence assertion.
                if marked:
                    notify_composed_threads_task_terminal(orch, source_id)
                    return
            except (ValueError, sqlite3.Error):
                notify_composed_threads_task_terminal(orch, source_id)
                raise
        # The ordinary facade retains root-only posting and composed archive
        # evaluation. Non-root sources never post for their ordinary ancestors.
        _maybe_post_thread_followup(orch, source_id, status=task.status, auto_revisit_spawned=False)


def notify_returned(dispatcher: Any, run_id: str, *, queue: Any = None) -> bool:
    """Rebuild histories, wake the ordinary parent, then attempt the terminal tail."""
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    org = dispatcher.org
    with org.db.coherent_read_view() as conn:
        history = read_sequential_history(conn, expected_org_slug=org.slug)
        if run_id not in history.run_ids:
            return False
        budget = _ReadBudget()
        with _sql_budget(conn, budget):
            run = _returned(conn, run_id, budget)
            if run is None:
                return False
            source_id, carrier_id = run['source_task_id'], run['carrier_task_id']
    orch = org.orchestrator
    if queue is not None:
        orch = _ReturnedCallerQueue(orch, queue)
    orch._update_task_history(carrier_id)
    orch._update_task_history(source_id)
    _enqueue_parent_if_waiting(orch, source_id, queue=queue)
    _terminal_thread_tail(orch, source_id)
    return True


def close_member_carrier_uncommitted(conn: sqlite3.Connection, run: dict, action: str,
                                     budget: _ReadBudget) -> None:
    """@2 return ends only this carrier; source/parent continuation is S6a."""
    from datetime import datetime, timezone
    members = _engine_rows(conn, 'workflow_seq_source_memberships', budget, 'run_id=?', (run['id'],))
    if not members:
        return
    stamp = datetime.now(timezone.utc).isoformat()
    conn.execute('UPDATE tasks SET status=?,completed_at=?,updated_at=? WHERE id=?',
        ('failed' if action == 'fail' else 'completed', stamp, stamp, run['carrier_task_id']))
