"""Explicit source-validation reuse, confined to one unpublished org load.

Sequential layouts use complete validation; writer targets never reuse sources.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Callable
from typing import Any, Iterator

logger = logging.getLogger(__name__)
_current: ContextVar[_LoadValidation | None] = ContextVar('org_load_schema_validation', default=None)
_SQLITE_CONFIGS = tuple((name, getattr(sqlite3, name)) for name in dir(sqlite3)
                        if name.startswith('SQLITE_DBCONFIG_'))
_SQLITE_LIMITS = tuple((name, getattr(sqlite3, name)) for name in dir(sqlite3)
                       if name.startswith('SQLITE_LIMIT_'))


def _bookend(conn: sqlite3.Connection) -> tuple | None:
    """Coherent metadata observation; unsupported/failed observations disable reuse.

    data_version brackets the observation itself. TEMP is deliberately observed
    before database_list, so even the first empty TEMP cookie has an identity.
    The owner holds Database._lock; independent writers require SQLite fences too.
    """
    try:
        if (conn.isolation_level != '' or conn.autocommit != sqlite3.LEGACY_TRANSACTION_CONTROL
                or conn.text_factory is not str):
            return None
        version = conn.execute('PRAGMA main.data_version').fetchone()[0]
        changes = conn.total_changes
        main = conn.execute('PRAGMA main.schema_version').fetchone()[0]
        temp = conn.execute('PRAGMA temp.schema_version').fetchone()[0]
        databases = tuple(tuple(row) for row in conn.execute('PRAGMA database_list'))
        if (not databases or databases[0][:2] != (0, 'main') or not databases[0][2]
                or any(row != (1, 'temp', '') for row in databases[1:])):
            return None
        config = tuple(conn.execute('PRAGMA ' + name).fetchone()[0] for name in (
            'foreign_keys', 'read_uncommitted', 'writable_schema', 'ignore_check_constraints',
            'recursive_triggers', 'trusted_schema', 'query_only',
        ))
        storage_config = tuple(conn.execute('PRAGMA ' + name).fetchone()[0] for name in (
            'journal_mode', 'locking_mode', 'synchronous', 'temp_store',
        ))
        native_config = tuple((name, conn.getconfig(option)) for name, option in _SQLITE_CONFIGS)
        limits = tuple((name, conn.getlimit(option)) for name, option in _SQLITE_LIMITS)
        if (any(type(value) is not int for value in (version, changes, main, temp, *config))
                or min(version, changes, main, temp) < 0
                or any(type(value) is not bool for _, value in native_config)
                or any(type(value) is not int for _, value in limits)
                or config[:4] != (1, 0, 0, 0)
                or conn.execute('PRAGMA main.data_version').fetchone()[0] != version
                or conn.total_changes != changes):
            return None
        return (conn, version, changes, main, temp, databases, config, storage_config, native_config, limits,
                conn.row_factory, conn.text_factory, conn.isolation_level, conn.autocommit)
    except Exception:
        return None


class _LoadValidation:
    def __init__(self, database: Any, org_slug: str) -> None:
        self.database = database
        self.org_slug = org_slug
        self.saved: tuple | None = None
        self.active = True
        self.full_runs = 0
        self.hits = 0
        self.full_seconds = 0.0

    def full(self, conn: sqlite3.Connection, **kwargs: Any) -> tuple:
        from runtime.infrastructure.workflow_schema import _read_workflow_schema
        start = time.monotonic()
        self.full_runs += 1
        try:
            return _read_workflow_schema(conn, **kwargs)
        finally:
            self.full_seconds += time.monotonic() - start

    def read(self, conn: sqlite3.Connection, *, writer_source: bool, **kwargs: Any) -> tuple:
        with self.database._lock:
            if (not self.active or conn is not self.database._conn or kwargs['expected_org_slug'] != self.org_slug
                    or (conn.in_transaction and not writer_source)):
                self.saved = None
                return self.full(conn, **kwargs)
            opening = _bookend(conn)
            if opening is None:
                self.saved = None
                return self.full(conn, **kwargs)
            if self.saved is None or self.saved[0] != opening:
                self.saved = None
                # Keep the original reader's snapshot ownership on a miss.
                # Equal outside bookends bracket the entire coherent full read,
                # including its transaction exit.
                result = self.full(conn, **kwargs)
                if _bookend(conn) == opening:
                    self.saved = (opening, result)
                return result
            owned = not conn.in_transaction
            result = self.saved[1]
            try:
                if owned:
                    conn.execute('BEGIN')
                    # Pin main before comparing the snapshot's metadata with
                    # the outside observation. A commit in the acquisition gap
                    # cannot attach an old validation to a newer snapshot.
                    conn.execute('SELECT rootpage FROM main.sqlite_schema LIMIT 1').fetchone()
                pinned = _bookend(conn)
                closing = _bookend(conn)
                if owned:
                    conn.rollback()
                outside = _bookend(conn)
                if pinned == opening == closing == outside:
                    self.saved = (outside, result)
                    self.hits += 1
                    return result
                self.saved = None
                # A moved/unknown closure never returns speculative reuse.
                # One ordinary complete check, no retry loop or new budget.
                return self.full(conn, **kwargs)
            except BaseException:
                self.saved = None
                raise
            finally:
                if owned and conn.in_transaction:
                    conn.rollback()


@contextmanager
def org_load_validation(database: Any, *, org_slug: str) -> Iterator[None]:
    """No persisted state, cross-load reuse or change to Database ownership."""
    owner = _LoadValidation(database, org_slug)
    token = _current.set(owner)
    try:
        yield
    finally:
        owner.active = False
        owner.saved = None
        _current.reset(token)
        logger.info('startup_workflow_validation org=%s guarded_full_runs=%d reused=%d full_seconds=%.6f',
                    org_slug, owner.full_runs, owner.hits, owner.full_seconds)


def read_org_layout(
    conn: sqlite3.Connection, *, expected_org_slug: str | None,
    legacy: Callable[[], Any], budget: Any = None,
) -> Any:
    """Select a complete sequential read or the caller's unchanged legacy path.

    Selection and sequential validation share one snapshot and absolute deadline.
    Only this read's authenticated rows/inventory may escape as its result; a
    writer target always calls anew after mutation. Absence leaves the original
    reader's transaction ownership, errors and source-reuse contract intact.
    """
    from runtime.infrastructure.workflow_schema import _schema_snapshot, _structure_cells
    from runtime.workflows.sequential_history import _ReadBudget, _sql_budget, read_sequential_history

    selector = _ReadBudget()
    with _schema_snapshot(conn):
        present = False
        with _sql_budget(conn, selector):
            for schema in ('main', 'temp'):
                rows = _structure_cells(conn, f'SELECT rowid,name,tbl_name FROM {schema}.sqlite_schema',
                                        ('name', 'tbl_name'), ('rowid',), selector)
                if any(type(value) is str and value.casefold().startswith('workflow_seq_')
                       for row in rows for value in row):
                    present = True
        if present:
            history = read_sequential_history(conn, expected_org_slug=expected_org_slug,
                retained_budget=budget, deadline=selector.deadline)
            validated = history.retained
            if validated is None or validated[1] is None:
                # Never reach install_or_recover's automatic Coding DDL branch
                # for a never-booted literal-I plus sequential layout.
                raise ValueError('workflow_schema_object_set_mismatch')
    if not present:
        # End only our own selection snapshot BEFORE the original expression.
        return legacy()
    selector.check()
    return validated


def read_workflow_schema_for_org_load(
    conn: sqlite3.Connection, *, expected_org_slug: str | None,
    writer_source: bool = False, budget: Any = None,
) -> tuple:
    """Source-only: writer_source asserts a fresh BEGIN IMMEDIATE, no own writes.

    Arbitrary caller-owned read transactions are never eligible. Outside the
    OrgState.load scope legacy layouts use the original complete reader.
    """
    owner = _current.get()
    kwargs = dict(expected_org_slug=expected_org_slug, budget=budget)
    from runtime.infrastructure.workflow_schema import _read_workflow_schema
    return read_org_layout(conn, expected_org_slug=expected_org_slug, budget=budget,
        legacy=lambda: (_read_workflow_schema(conn, **kwargs) if owner is None else
                        owner.read(conn, writer_source=writer_source, **kwargs)))


def read_installed_for_org_load(conn: sqlite3.Connection, *, expected_org_slug: str, budget: Any) -> tuple:
    """Cutover's fresh writer-source read; preserve metadata semantics elsewhere."""
    if _current.get() is None:
        from runtime.infrastructure.workflow_schema import _read_installed
        return read_org_layout(conn, expected_org_slug=expected_org_slug, budget=budget,
            legacy=lambda: _read_installed(conn, expected_org_slug=expected_org_slug,
                                           validate_data=False, budget=budget))
    return read_workflow_schema_for_org_load(conn, expected_org_slug=expected_org_slug,
                                            writer_source=True, budget=budget)
