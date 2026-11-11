"""Same-snapshot legacy consumers of an authenticated mixed business stop."""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from runtime.workflows.coding_authority_history import _Budget
from runtime.workflows.sequential_cancel import _check
from runtime.workflows.sequential_history import (
    SequentialHistory, _ReadBudget, _sql_budget, read_sequential_history,
)


@contextmanager
def legacy_cancellation_scope(conn: sqlite3.Connection, *, org_slug: str, task_id: str,
                              cancelled: bool = True,
                              deadline: float | None = None) -> Iterator[None]:
    """Authenticate before original consumers; never relax standalone validation.

    Original terminal/outer consumers enter here. The transaction keeps the
    authenticated rows on this connection;
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
    # R-M3-2: complete source and whole target reads own separate unchanged
    # D2 allowances; native consumer work retains its enclosing owner budget.
    before_budget, after_budget = _Budget(), _Budget()
    history = read_sequential_history(conn, expected_org_slug=org_slug, task_id=task_id,
                                     retained_budget=before_budget, deadline=budget.deadline)
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
                            retained_budget=after_budget, deadline=budget.deadline)
    budget.check()


@contextmanager
def _mixed_lookup_scope(conn: sqlite3.Connection, *, org_slug: str, task_id: str,
                        retained_budget: _Budget, budget: _ReadBudget) -> Iterator[SequentialHistory]:
    """Pin ONE complete read through the mounted writer's read-only planning.

    The owning BEGIN IMMEDIATE excludes other writers. query_only prevents this
    connection's DML/DDL, and RELEASE proves the original savepoint survived.
    Monotonic changes/schema cookies also refuse mutation if a consumer changes
    query_only temporarily. Thus entry/exit cannot authenticate different data,
    including valid or reverted DML that repeat readers could accept. No SQL
    authorizer is replaced. The caller closes this scope before its first DML
    and authenticates the whole changed target with its own unchanged D2
    allowance on the same original operation deadline (R-M3-2).
    """
    from runtime.infrastructure.workflow_schema import _sequential_legacy_read
    from runtime.infrastructure.workflow_sequential_schema import SEQUENTIAL_TABLES
    from runtime.workflows.sequential_engine import _engine_rows

    _check(conn.in_transaction and _sequential_legacy_read.get() is None)
    _check(conn.execute('PRAGMA query_only').fetchone()[0] == 0)
    changes = conn.total_changes
    schema = tuple(conn.execute(sql).fetchone()[0] for sql in (
        'PRAGMA main.schema_version', 'PRAGMA temp.schema_version'))
    conn.execute('SAVEPOINT workflow_seq_cancel_lookup')
    token = None
    try:
        conn.execute('PRAGMA query_only=ON')
        history = read_sequential_history(conn, expected_org_slug=org_slug, task_id=task_id,
                                         retained_budget=retained_budget, deadline=budget.deadline)
        _check(history.task_history is not None)
        with _sql_budget(conn, budget):
            data = {table: _engine_rows(conn, table, budget, '1', ()) for table in SEQUENTIAL_TABLES}
        token = _sequential_legacy_read.set((conn, data))
        yield history
        budget.check()
        _check(conn.in_transaction and conn.total_changes == changes
               and conn.execute('PRAGMA query_only').fetchone()[0] == 1
               and schema == tuple(conn.execute(sql).fetchone()[0] for sql in (
                   'PRAGMA main.schema_version', 'PRAGMA temp.schema_version')))
        with _sql_budget(conn, budget):
            _check(all(_engine_rows(conn, table, budget, '1', ()) == rows for table, rows in data.items()))
        conn.execute('RELEASE SAVEPOINT workflow_seq_cancel_lookup')
    finally:
        if token is not None:
            _sequential_legacy_read.reset(token)
        conn.execute('PRAGMA query_only=OFF')
