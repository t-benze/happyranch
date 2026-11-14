"""Explicit source-validation reuse, confined to one unpublished org load.

Only audited source wrappers and the three unchanged cutover/ownership/naming
startup targets borrow complete validation. Public reads and changed or unknown
intervals remain fresh, with the original cumulative budgets and deadlines.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
import re
import sqlite3
import time
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from itertools import count
from collections.abc import Callable
from typing import Any, Iterator

logger = logging.getLogger(__name__)
_current: ContextVar[_LoadValidation | None] = ContextVar('org_load_schema_validation', default=None)
_temp_cookie_reads = count()
_SQLITE_CONFIGS = tuple((name, getattr(sqlite3, name)) for name in dir(sqlite3)
                        if name.startswith('SQLITE_DBCONFIG_'))
_SQLITE_LIMITS = tuple((name, getattr(sqlite3, name)) for name in dir(sqlite3)
                       if name.startswith('SQLITE_LIMIT_'))
_SQL_PREFIX = re.compile(r'(?:\s+|--[^\n]*(?:\n|$)|/\*.*?\*/)*', re.DOTALL)
_SQL_WORD = re.compile(r'[A-Za-z_][A-Za-z_0-9]*')
_PRAGMA_NAME = re.compile(r'(?:(?:main|temp)\s*\.\s*)?([A-Za-z_][A-Za-z_0-9]*)', re.IGNORECASE)
_READ_PRAGMAS = frozenset((
    'database_list', 'table_list', 'table_info', 'table_xinfo', 'index_list',
    'index_info', 'index_xinfo', 'foreign_key_list', 'foreign_key_check',
    'integrity_check', 'quick_check', 'compile_options',
))
_CONFIG_PRAGMAS = frozenset((
    'data_version', 'schema_version', 'user_version', 'application_id',
    'foreign_keys', 'defer_foreign_keys', 'read_uncommitted', 'writable_schema',
    'ignore_check_constraints', 'recursive_triggers', 'trusted_schema',
    'query_only', 'journal_mode', 'locking_mode', 'synchronous', 'temp_store',
    'busy_timeout', 'cache_size', 'cache_spill', 'mmap_size', 'encoding',
))


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
        # Some native metadata PRAGMAs complete an implicit read transaction and
        # reset defer_foreign_keys. Refuse the original deferred state BEFORE
        # those reads; a later zero observation cannot certify its absence.
        if (conn.execute('PRAGMA foreign_keys').fetchone()[0] != 1
                or conn.execute('PRAGMA defer_foreign_keys').fetchone()[0] != 0):
            return None
        version = conn.execute('PRAGMA main.data_version').fetchone()[0]
        changes = conn.total_changes
        main = conn.execute('PRAGMA main.schema_version').fetchone()[0]
        # Reprepare the same native cookie read: cached TEMP PRAGMAs can retain
        # invalid native metadata after temp_store resets (SQLite 3.53.1).
        # This counter only varies SQL text; it retains no validation state.
        temp = conn.execute(f'PRAGMA temp.schema_version -- {next(_temp_cookie_reads)}').fetchone()[0]
        databases = tuple(tuple(row) for row in conn.execute('PRAGMA database_list'))
        if (not databases or databases[0][:2] != (0, 'main') or not databases[0][2]
                or any(row != (1, 'temp', '') for row in databases[1:])):
            return None
        config = tuple(conn.execute('PRAGMA ' + name).fetchone()[0] for name in (
            'foreign_keys', 'defer_foreign_keys', 'read_uncommitted', 'writable_schema', 'ignore_check_constraints',
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
                or config[:5] != (1, 0, 0, 0, 0)
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
        self.connection = database._conn
        self.execution = _execution()
        self.attachment = _attachment(database.db_path)
        self.sequential_saved: tuple | None = None
        self.certificate = 0
        # Strong object references prevent id recycling; associations never
        # leave this unpublished owner. Each new complete fact is charged once.
        self.ledgers: dict[int, tuple[Any, set[int], tuple[int, int, int]]] = {}
        self.statements = 0
        self.transaction_mode: str | None = None
        self.transaction_fresh = False
        self.transaction_changes = self.connection.total_changes
        self.org_slug = org_slug
        self.saved: tuple | None = None
        self.active = True
        self.full_runs = 0
        self.hits = 0
        self.full_seconds = 0.0
        self.trace: Any = None
        self.schema_epoch = 0
        self.observer_failed = False

    def observe_statement(self, statement: str) -> None:
        """Execution-time invalidation, including cached/cursor/script execution.

        This observes only. It changes no native authorizer or progress handler.
        Unknown syntax makes this interval unusable rather than guessing a hit.
        """
        self.statements += 1
        start = _SQL_PREFIX.match(statement).end()
        word = _SQL_WORD.match(statement, start)
        if word is None:
            if statement[start:].strip(' ;'):
                self.observer_failed = True
                self.saved = None
                self.sequential_saved = None
            return
        keyword = word.group().upper()
        if keyword == 'BEGIN':
            tail = statement[word.end():].strip().upper().rstrip(';').strip()
            self.transaction_mode = 'writer' if tail in ('IMMEDIATE', 'IMMEDIATE TRANSACTION') else 'read'
            self.transaction_fresh = True
            self.transaction_changes = self.connection.total_changes
        elif keyword in {'COMMIT', 'END', 'ROLLBACK'}:
            if keyword == 'ROLLBACK' and self.connection.total_changes != self.transaction_changes:
                # A complete check of uncommitted writes cannot survive their
                # rollback: total_changes is monotone, not a commit identity.
                self.clear()
            # ROLLBACK TO is not a new fresh transaction interval.
            self.transaction_mode = None
            self.transaction_fresh = False
        else:
            self.transaction_fresh = False
        if keyword in {'SELECT', 'WITH', 'INSERT', 'UPDATE', 'DELETE', 'REPLACE',
                       'BEGIN', 'COMMIT', 'END', 'ROLLBACK', 'SAVEPOINT', 'RELEASE'}:
            return
        if keyword == 'PRAGMA':
            name_start = _SQL_PREFIX.match(statement, word.end()).end()
            name = _PRAGMA_NAME.match(statement, name_start)
            if name is not None:
                pragma = name.group(1).lower()
                tail = statement[_SQL_PREFIX.match(statement, name.end()).end():].strip(' ;')
                if pragma in _READ_PRAGMAS:
                    return
                if pragma in _CONFIG_PRAGMAS:
                    if not tail:
                        return
                    if tail.startswith(('=', '(')):
                        self.schema_epoch += 1
                        self.saved = None
                        self.sequential_saved = None
                        return
        elif keyword in {'CREATE', 'DROP', 'ALTER', 'ATTACH', 'DETACH', 'VACUUM',
                         'ANALYZE', 'REINDEX'}:
            self.schema_epoch += 1
            self.saved = None
            self.sequential_saved = None
            return
        self.observer_failed = True
        self.saved = None
        self.sequential_saved = None

    def bookend(self, conn: sqlite3.Connection) -> tuple | None:
        if self.trace is None or self.trace.failed or self.observer_failed:
            return None
        serial = self.statements
        observed = _bookend(conn)
        if self.statements == serial:
            self.observer_failed = True
            self.clear()
        if observed is None or self.trace.failed or self.observer_failed:
            return None
        return observed, self.schema_epoch

    def clear(self) -> None:
        self.saved = None
        self.sequential_saved = None

    def attachment_matches(self) -> bool:
        current = _attachment(self.database.db_path)
        if self.attachment is not None and current != self.attachment:
            self.clear()
            raise ValueError('workflow_load_attachment_changed')
        return current is not None

    def compatible_budget(self, budget: Any) -> bool:
        if budget is None:
            return True
        if type(budget) is not _DEFAULT_BUDGET or set(vars(budget)) != {
                'consumed', 'accepted_rows', 'traversed'}:
            return False
        counters = _charges(budget)
        if (any(type(value) is not int or value < 0 for value in counters)
                or any(value > limit for value, limit in zip(counters, (
                    _retained._BYTE_LIMIT, _retained._CALL_ROW_LIMIT, _retained._TRAVERSAL_LIMIT)))):
            return False
        known = self.ledgers.get(id(budget))
        return (counters == (0, 0, 0) if known is None else
                known[0] is budget and all(a >= b for a, b in zip(counters, known[2])))

    def charge(self, budget: Any, certificate: int, charges: tuple[int, int, int]) -> None:
        if budget is None:
            return
        known = self.ledgers.get(id(budget))
        charged = set() if known is None else known[1]
        if certificate not in charged:
            budget._charge_reused(*charges)
            charged.add(certificate)
        self.ledgers[id(budget)] = (budget, charged, _charges(budget))

    def read_layout(self, conn: sqlite3.Connection, *, expected_org_slug: str | None,
                    legacy: Callable[[], Any], budget: Any = None,
                    writer_source: bool = False, load_target: bool = False) -> Any:
        from runtime.infrastructure.workflow_schema import _schema_snapshot
        from runtime.workflows.sequential_history import _ReadBudget

        # Acquisition time includes bookends, pinning and ledger import. A hit
        # never resets the original selector's absolute five-second deadline.
        selector = _ReadBudget()
        with self.database._lock:
            same_attachment = self.attachment_matches()
            eligible = (self.active and conn is self.connection is self.database._conn
                        and expected_org_slug == self.org_slug and _execution() == self.execution
                        and same_attachment and _default_policy() and self.compatible_budget(budget)
                        and (not conn.in_transaction or
                             (writer_source and self.transaction_mode == 'writer') or
                             (load_target and (self.transaction_mode == 'writer' or
                                               (self.transaction_mode == 'read' and self.transaction_fresh)))))
            if not eligible:
                self.clear()
                return read_org_layout(conn, expected_org_slug=expected_org_slug,
                                       legacy=legacy, budget=budget)
            # The fresh named borrowed interval may not yet have read main.
            # Pin before ANY metadata comparison; never end that transaction.
            if conn.in_transaction:
                conn.execute('SELECT rootpage FROM main.sqlite_schema LIMIT 1').fetchone()
            opening = self.bookend(conn)
            saved = self.sequential_saved
            if saved is not None and opening is not None and saved[0] == opening:
                owned = not conn.in_transaction
                primary: BaseException | None = None
                try:
                    if owned:
                        conn.execute('BEGIN')
                        conn.execute('SELECT rootpage FROM main.sqlite_schema LIMIT 1').fetchone()
                    pinned = self.bookend(conn)
                    closing = self.bookend(conn)
                    if owned:
                        conn.rollback()
                    outside = self.bookend(conn)
                    self.attachment_matches()
                    selector.check()
                    if (opening == pinned == closing == outside and self.active
                            and _execution() == self.execution and _default_policy()):
                        self.charge(budget, saved[2], saved[3])
                        selector.check()
                        self.hits += 1
                        return saved[1]
                except BaseException as exc:
                    primary = exc
                    self.clear()
                    raise
                finally:
                    if owned and conn.in_transaction:
                        try:
                            conn.rollback()
                        except BaseException:
                            self.observer_failed = True
                            self.clear()
                            if primary is None:
                                raise
                            primary.add_note('Workflow load snapshot cleanup failed')
                # One ordinary finite fallback. No race retry or allowance reset.
            self.sequential_saved = None
            retained = _DEFAULT_BUDGET() if budget is None else budget
            before = _charges(retained)
            with _schema_snapshot(conn):
                present = sequential_storage_present(conn, budget=selector)
                if present:
                    start = time.monotonic()
                    self.full_runs += 1
                    try:
                        result = _complete_sequential(conn, expected_org_slug=expected_org_slug,
                                                      budget=retained, selector=selector)
                    finally:
                        self.full_seconds += time.monotonic() - start
            if not present:
                # Keep legacy target result shapes and metadata-only fallbacks.
                return self.read(conn, expected_org_slug=expected_org_slug, budget=budget,
                                 writer_source=writer_source, load_target=load_target,
                                 fallback=legacy if load_target else None)
            selector.check()
            self.attachment_matches()
            closing = self.bookend(conn)
            if (opening is not None and closing == opening and self.active and _default_policy()
                    and _execution() == self.execution):
                self.certificate += 1
                delta = tuple(a - b for a, b in zip(_charges(retained), before))
                self.sequential_saved = (closing, result, self.certificate, delta)
                known = self.ledgers.get(id(retained))
                charged = set() if known is None else known[1]
                charged.add(self.certificate)  # The real reader already charged it.
                self.ledgers[id(retained)] = (retained, charged, _charges(retained))
            return result

    def full(self, conn: sqlite3.Connection, *, fallback: Callable[[], Any] | None = None, **kwargs: Any) -> Any:
        from runtime.infrastructure.workflow_schema import _read_workflow_schema
        start = time.monotonic()
        self.full_runs += 1
        try:
            return fallback() if fallback is not None else _read_workflow_schema(conn, **kwargs)
        finally:
            self.full_seconds += time.monotonic() - start

    def read(self, conn: sqlite3.Connection, *, writer_source: bool = False,
             load_target: bool = False, fallback: Callable[[], Any] | None = None,
             **kwargs: Any) -> Any:
        with self.database._lock:
            if (not self.active or conn is not self.database._conn or kwargs['expected_org_slug'] != self.org_slug
                    or (conn.in_transaction and not (writer_source or load_target))):
                self.saved = None
                return self.full(conn, fallback=fallback, **kwargs)
            opening = self.bookend(conn)
            if opening is None:
                self.saved = None
                return self.full(conn, fallback=fallback, **kwargs)
            if self.saved is None or self.saved[0] != opening:
                self.saved = None
                # Keep the original reader's snapshot ownership on a miss.
                # Equal outside bookends bracket the entire coherent full read,
                # including its transaction exit.
                result = self.full(conn, fallback=fallback, **kwargs)
                # A target fallback can be a metadata-only legacy reader. Never
                # promote it into a reusable complete-source validation.
                if fallback is None and self.bookend(conn) == opening:
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
                pinned = self.bookend(conn)
                closing = self.bookend(conn)
                if owned:
                    conn.rollback()
                outside = self.bookend(conn)
                if pinned == opening == closing == outside:
                    self.saved = (outside, result)
                    self.hits += 1
                    return result
                self.saved = None
                # A moved/unknown closure never returns speculative reuse.
                # One ordinary complete check, no retry loop or new budget.
                return self.full(conn, fallback=fallback, **kwargs)
            except BaseException:
                self.saved = None
                raise
            finally:
                if owned and conn.in_transaction:
                    conn.rollback()


@contextmanager
def org_load_validation(database: Any, *, org_slug: str) -> Iterator[None]:
    """Own one composed trace registration for this unpublished load only."""
    owner = _LoadValidation(database, org_slug)
    token = _current.set(owner)
    try:
        with ExitStack() as observers:
            try:
                try:
                    owner.trace = observers.enter_context(database.observe_trace(owner.observe_statement))
                except Exception:
                    # An unsupported/failed installation can only run full reads.
                    owner.observer_failed = True
                yield
            finally:
                # Disable before unregistering, including every BaseException.
                owner.active = False
                owner.clear()
                owner.ledgers.clear()
    finally:
        owner.active = False
        owner.clear()
        owner.ledgers.clear()
        _current.reset(token)
        logger.info('startup_workflow_validation org=%s guarded_full_runs=%d reused=%d full_seconds=%.6f',
                    org_slug, owner.full_runs, owner.hits, owner.full_seconds)


def sequential_storage_present(conn: sqlite3.Connection, *, budget: Any) -> bool:
    """Select sequential objects in the caller's coherent schema snapshot."""
    from runtime.infrastructure.workflow_schema import _structure_cells
    from runtime.workflows.sequential_history import _sql_budget

    present = False
    with _sql_budget(conn, budget):
        for schema in ('main', 'temp'):
            rows = _structure_cells(conn, f'SELECT rowid,name,tbl_name FROM {schema}.sqlite_schema',
                                    ('name', 'tbl_name'), ('rowid',), budget)
            if any(type(value) is str and value.casefold().startswith('workflow_seq_')
                   for row in rows for value in row):
                present = True
    return present


def read_org_layout(
    conn: sqlite3.Connection, *, expected_org_slug: str | None,
    legacy: Callable[[], Any], budget: Any = None,
) -> Any:
    """Select a complete sequential read or the caller's unchanged legacy path.

    Selection and sequential validation share one snapshot and absolute deadline.
    Only this read's authenticated rows/inventory may escape as its result;
    this public/default path always runs fresh, including inside a load owner. Absence leaves the original
    reader's transaction ownership, errors and source-reuse contract intact.
    """
    from runtime.infrastructure.workflow_schema import _schema_snapshot
    from runtime.workflows.sequential_history import _ReadBudget

    selector = _ReadBudget()
    with _schema_snapshot(conn):
        present = sequential_storage_present(conn, budget=selector)
        if present:
            validated = _complete_sequential(conn, expected_org_slug=expected_org_slug,
                                             budget=budget, selector=selector)
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
    legacy = lambda: _read_workflow_schema(conn, **kwargs)
    if owner is None:
        return read_org_layout(conn, expected_org_slug=expected_org_slug, budget=budget, legacy=legacy)
    return owner.read_layout(conn, expected_org_slug=expected_org_slug, budget=budget,
                             writer_source=writer_source, legacy=legacy)


def read_workflow_schema_target_for_org_load(
    conn: sqlite3.Connection, *, expected_org_slug: str,
    legacy: Callable[[], Any], budget: Any = None,
) -> Any:
    """Three explicit startup targets only; preserve their exact full fallback.

    Cutover owns a fresh BEGIN snapshot; ownership/naming own BEGIN IMMEDIATE.
    The selector pins that snapshot before bookends. Never admit an arbitrary
    pre-existing reader transaction here, or end the caller's transaction.
    A hit returns the identical complete tuple; each compatible caller imports
    its retained charge once. Changed targets debit that same original budget.
    """
    owner = _current.get()
    if owner is None:
        return read_org_layout(conn, expected_org_slug=expected_org_slug, budget=budget, legacy=legacy)
    return owner.read_layout(conn, expected_org_slug=expected_org_slug, budget=budget,
                             load_target=True, legacy=legacy)


def read_installed_for_org_load(conn: sqlite3.Connection, *, expected_org_slug: str, budget: Any) -> tuple:
    """Cutover's fresh writer-source read; preserve metadata semantics elsewhere."""
    if _current.get() is None:
        from runtime.infrastructure.workflow_schema import _read_installed
        return read_org_layout(conn, expected_org_slug=expected_org_slug, budget=budget,
            legacy=lambda: _read_installed(conn, expected_org_slug=expected_org_slug,
                                           validate_data=False, budget=budget))
    return read_workflow_schema_for_org_load(conn, expected_org_slug=expected_org_slug,
                                            writer_source=True, budget=budget)


def _execution() -> tuple:
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return threading.current_thread(), task


def _attachment(path: Any) -> tuple | None:
    try:
        resolved = Path(path).resolve(strict=True)
        stat = resolved.stat()
        return str(resolved), stat.st_dev, stat.st_ino
    except (OSError, ValueError, TypeError):
        return None


def _charges(budget: Any) -> tuple[int, int, int]:
    return budget.consumed, budget.accepted_rows, budget.traversed


def _complete_sequential(conn: sqlite3.Connection, *, expected_org_slug: str | None,
                         budget: Any, selector: Any) -> tuple:
    from runtime.workflows.sequential_history import read_sequential_history
    history = read_sequential_history(conn, expected_org_slug=expected_org_slug,
                                      retained_budget=budget, deadline=selector.deadline)
    validated = history.retained
    if validated is None or validated[1] is None:
        # Never route literal-I plus sequential through automatic Coding DDL.
        raise ValueError('workflow_schema_object_set_mismatch')
    return validated


# Named default validator/policy only. Alternate readers, subclasses, adjusted
# defaults or policy constants retain fresh validation and their own refusals.
from runtime.workflows import sequential_history as _sequential
from runtime.workflows import coding_authority_history as _retained
from runtime.infrastructure import workflow_schema as _schema
_DEFAULT_READER = _sequential.read_sequential_history
_DEFAULT_BUDGET = _retained._Budget
_DEFAULT_READ_BUDGET = _sequential._ReadBudget
_DEFAULT_POLICY = (_DEFAULT_READER.__kwdefaults__.copy(), _DEFAULT_READ_BUDGET.check,
                   _DEFAULT_READ_BUDGET.progress, _DEFAULT_BUDGET.step,
                   _DEFAULT_BUDGET.candidate, _DEFAULT_BUDGET.remaining_bytes,
                   _schema._read_workflow_schema, _retained._BYTE_LIMIT,
                   _retained._CALL_ROW_LIMIT, _retained._TRAVERSAL_LIMIT,
                   _DEFAULT_READ_BUDGET.__init__.__defaults__, _sequential._sql_budget)


def _default_policy() -> bool:
    return (_sequential.read_sequential_history is _DEFAULT_READER
            and _retained._Budget is _DEFAULT_BUDGET
            and _sequential._ReadBudget is _DEFAULT_READ_BUDGET
            and (_DEFAULT_READER.__kwdefaults__, _DEFAULT_READ_BUDGET.check,
                 _DEFAULT_READ_BUDGET.progress, _DEFAULT_BUDGET.step,
                 _DEFAULT_BUDGET.candidate, _DEFAULT_BUDGET.remaining_bytes,
                 _schema._read_workflow_schema, _retained._BYTE_LIMIT,
                 _retained._CALL_ROW_LIMIT, _retained._TRAVERSAL_LIMIT,
                 _DEFAULT_READ_BUDGET.__init__.__defaults__, _sequential._sql_budget) == _DEFAULT_POLICY)
