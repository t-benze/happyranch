"""Committed sequential caller returns; no visit recovery or thread effects.

The unchanged complete reader admits history before bounded indexed selection
in the same snapshot. Replaying an immutable outbox never acknowledges/deletes
it, and queue publication is not a durable caller execution acknowledgement.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from runtime.infrastructure.workflow_schema_load import sequential_storage_present
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


def notify_returned(dispatcher: Any, run_id: str, *, queue: Any = None) -> bool:
    """Rebuild native histories and request the source's ordinary parent wake."""
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
    orch._update_task_history(carrier_id)
    orch._update_task_history(source_id)
    _enqueue_parent_if_waiting(orch, source_id, queue=queue)
    return True
