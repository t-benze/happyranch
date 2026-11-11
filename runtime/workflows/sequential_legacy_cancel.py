"""Same-snapshot legacy consumers of an authenticated mixed business stop."""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from runtime.workflows.coding_authority_history import _Budget
from runtime.workflows.sequential_cancel import _check
from runtime.workflows.sequential_history import _ReadBudget, _sql_budget, read_sequential_history


@contextmanager
def legacy_cancellation_scope(conn: sqlite3.Connection, *, org_slug: str, task_id: str,
                              cancelled: bool = True, retained_budget: _Budget | None = None,
                              deadline: float | None = None) -> Iterator[None]:
    """Authenticate before original consumers; never relax standalone validation.

    Only cancellation's mounted lookup and original terminal/outer consumers
    enter here. The transaction keeps the authenticated rows on this connection;
    legacy consumers cannot change those sequential rows while using the scope.
    The complete changed target is authenticated after scope reset.
    """
    from runtime.infrastructure.workflow_schema import _sequential_legacy_read
    from runtime.infrastructure.workflow_sequential_schema import SEQUENTIAL_TABLES
    from runtime.workflows.sequential_engine import _engine_rows

    _check(conn.in_transaction and _sequential_legacy_read.get() is None)
    if conn.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='workflow_seq_runs'").fetchone() is None:
        yield
        return
    budget = _ReadBudget() if deadline is None else _ReadBudget(deadline=deadline)
    retained_budget = _Budget() if retained_budget is None else retained_budget
    history = read_sequential_history(conn, expected_org_slug=org_slug, task_id=task_id,
                                     retained_budget=retained_budget, deadline=budget.deadline)
    _check(history.task_history is not None)
    task = conn.execute('SELECT status,cancelled_at FROM tasks WHERE id=?', (task_id,)).fetchone()
    _check(task is not None and (not cancelled or (task[0] == 'cancelled' and task[1] is not None)))
    with _sql_budget(conn, budget):
        data = {table: _engine_rows(conn, table, budget, '1', ()) for table in SEQUENTIAL_TABLES}
    token = _sequential_legacy_read.set((conn, data))
    try:
        yield
        with _sql_budget(conn, budget):
            _check(all(_engine_rows(conn, table, budget, '1', ()) == rows for table, rows in data.items()))
    finally:
        _sequential_legacy_read.reset(token)
    read_sequential_history(conn, expected_org_slug=org_slug, task_id=task_id,
                            retained_budget=retained_budget, deadline=budget.deadline)
    budget.check()
