"""Closed org-only naming v1 layout. Generic Database never installs names."""
from __future__ import annotations

from runtime.infrastructure._workflow_reader import (
    _append as _reader_append,
)

from runtime.infrastructure._workflow_reader import (
    _list as _reader_list,
    _tuple as _reader_tuple,
)

from runtime.infrastructure._workflow_reader import (
    _all as _reader_all, _one as _reader_one, _rows as _reader_rows,
    _json as _reader_json, _json_work as _reader_json_work,
    _reserve as _reader_reserve, _work as _reader_work,
)

from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
import sqlite3
import time

TABLES = ("identity_name_schema", "identity_name_owners", "identity_name_claims")
MAX_ROWS = 65536
MAX_ROW_BYTES = 32 * 1024 * 1024
SCAN_SECONDS = 2.0


class NamingError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


NAMING_DDL = "CREATE TABLE identity_name_schema (\n  singleton INTEGER PRIMARY KEY CHECK(singleton=1),\n  version INTEGER NOT NULL CHECK(version=1),\n  org_slug TEXT NOT NULL CHECK(length(org_slug)>0)\n);\nCREATE TABLE identity_name_owners (\n  kind TEXT NOT NULL CHECK(kind IN ('agent','founder')),\n  canonical_id TEXT NOT NULL CHECK(length(canonical_id)>0),\n  lifecycle TEXT NOT NULL CHECK(lifecycle IN ('active','pending','terminated','absent','founder')),\n  current_label TEXT,\n  revision INTEGER NOT NULL CHECK(typeof(revision)='integer' AND revision>0),\n  PRIMARY KEY(kind,canonical_id),\n  CHECK((kind='founder' AND canonical_id='founder' AND lifecycle='founder')\n     OR (kind='agent' AND lifecycle!='founder')),\n  CHECK(current_label IS NULL OR\n    (current_label=canonical_id OR\n     (length(current_label) BETWEEN 1 AND 64\n      AND current_label NOT GLOB '*[^A-Za-z0-9_-]*'\n      AND substr(current_label,1,1) GLOB '[A-Za-z0-9]'))),\n  CHECK(lifecycle='absent' OR current_label IS NOT NULL)\n);\nCREATE TABLE identity_name_claims (\n  normalized_name TEXT PRIMARY KEY NOT NULL CHECK(length(normalized_name)>0),\n  owner_kind TEXT NOT NULL,\n  owner_id TEXT NOT NULL,\n  id_reserved INTEGER NOT NULL CHECK(id_reserved IN (0,1)),\n  permanent INTEGER NOT NULL CHECK(permanent IN (0,1)),\n  FOREIGN KEY(owner_kind,owner_id) REFERENCES identity_name_owners(kind,canonical_id),\n  CHECK(id_reserved=1 OR permanent=1),\n  CHECK(normalized_name=lower(normalized_name)\n    AND (normalized_name=lower(owner_id) OR\n      (length(normalized_name) BETWEEN 1 AND 64\n       AND normalized_name NOT GLOB '*[^a-z0-9_-]*'\n       AND substr(normalized_name,1,1) GLOB '[a-z0-9]')))\n);\n"

# Independently pinned accepted TASK10352 literal; never extracted from live/candidate SQL.
REFERENCE_DDL = "CREATE TABLE identity_name_schema (\n  singleton INTEGER PRIMARY KEY CHECK(singleton=1),\n  version INTEGER NOT NULL CHECK(version=1),\n  org_slug TEXT NOT NULL CHECK(length(org_slug)>0)\n);\nCREATE TABLE identity_name_owners (\n  kind TEXT NOT NULL CHECK(kind IN ('agent','founder')),\n  canonical_id TEXT NOT NULL CHECK(length(canonical_id)>0),\n  lifecycle TEXT NOT NULL CHECK(lifecycle IN ('active','pending','terminated','absent','founder')),\n  current_label TEXT,\n  revision INTEGER NOT NULL CHECK(typeof(revision)='integer' AND revision>0),\n  PRIMARY KEY(kind,canonical_id),\n  CHECK((kind='founder' AND canonical_id='founder' AND lifecycle='founder')\n     OR (kind='agent' AND lifecycle!='founder')),\n  CHECK(current_label IS NULL OR\n    (current_label=canonical_id OR\n     (length(current_label) BETWEEN 1 AND 64\n      AND current_label NOT GLOB '*[^A-Za-z0-9_-]*'\n      AND substr(current_label,1,1) GLOB '[A-Za-z0-9]'))),\n  CHECK(lifecycle='absent' OR current_label IS NOT NULL)\n);\nCREATE TABLE identity_name_claims (\n  normalized_name TEXT PRIMARY KEY NOT NULL CHECK(length(normalized_name)>0),\n  owner_kind TEXT NOT NULL,\n  owner_id TEXT NOT NULL,\n  id_reserved INTEGER NOT NULL CHECK(id_reserved IN (0,1)),\n  permanent INTEGER NOT NULL CHECK(permanent IN (0,1)),\n  FOREIGN KEY(owner_kind,owner_id) REFERENCES identity_name_owners(kind,canonical_id),\n  CHECK(id_reserved=1 OR permanent=1),\n  CHECK(normalized_name=lower(normalized_name)\n    AND (normalized_name=lower(owner_id) OR\n      (length(normalized_name) BETWEEN 1 AND 64\n       AND normalized_name NOT GLOB '*[^a-z0-9_-]*'\n       AND substr(normalized_name,1,1) GLOB '[a-z0-9]')))\n);\n"
def execute_literal(conn, literal):
    """Statement execution preserves the caller's transaction (no executescript)."""
    for statement in literal.split(';'):
        if statement.strip():
            conn.execute(statement)


def _inventory(conn, *, budget=None):
    _reader_reserve(budget, 0)
    rows = _reader_all(conn, "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name LIMIT ?", (_reader_reserve(budget, 80) or (MAX_ROWS + 1,)), budget=budget)
    if len(rows) > MAX_ROWS:
        raise NamingError("naming_schema_object_bound")
    objects = _reader_tuple((_reader_tuple(r, budget=budget) for r in rows if r[1].lower().startswith('identity_name_') or r[2] in TABLES), budget=budget)
    metadata = (_reader_reserve(budget, 64) or [])
    for table in TABLES:
        columns = _reader_tuple((_reader_tuple(r, budget=budget) for r in _reader_rows(conn, f'PRAGMA table_xinfo("{table}")', budget=budget)), budget=budget)
        foreign = _reader_tuple((_reader_tuple(r, budget=budget) for r in _reader_rows(conn, f'PRAGMA foreign_key_list("{table}")', budget=budget)), budget=budget)
        indexes = _reader_tuple((_reader_tuple(r, budget=budget) for r in _reader_rows(conn, f'PRAGMA index_list("{table}")', budget=budget)), budget=budget)
        details = _reader_tuple(((_reader_reserve(budget, 96) or (r[1], _reader_tuple((_reader_tuple(x, budget=budget) for x in _reader_rows(
            conn, 'SELECT * FROM pragma_index_xinfo(?) ORDER BY seqno', (_reader_reserve(budget, 80) or (r[1],))
        , budget=budget)), budget=budget))) for r in indexes), budget=budget)
        _reader_append(metadata, (_reader_reserve(budget, 144) or (table, columns, foreign, indexes, details)), budget=budget)
    return (_reader_reserve(budget, 96) or (objects, _reader_tuple(metadata, budget=budget)))


@lru_cache(maxsize=1)
def _cached_reference_inventory():
    conn = sqlite3.connect(':memory:')
    try:
        execute_literal(conn, REFERENCE_DDL)
        return _inventory(conn)
    finally:
        conn.close()


def _reference_inventory(*, budget=None):
    if budget is None:
        return _cached_reference_inventory()
    # Supplied allowances acquire the independent literal reference afresh;
    # an eager cached tuple cannot stand in for charged acquisition.
    reference = sqlite3.connect(":memory:")
    try:
        execute_literal(reference, REFERENCE_DDL)
        reference.execute('BEGIN')
        return _inventory(reference, budget=budget)
    finally:
        reference.close()


def naming_version(conn, *, org_slug: str | None = None, budget=None):
    """Select only absent or complete v1; unknown/partial naming never repairs."""
    _reader_reserve(budget, 0)
    actual = _inventory(conn, budget=budget)
    if not actual[0]:
        return 0
    if actual != _reference_inventory(budget=budget):
        raise NamingError('naming_schema_layout_mismatch')
    markers = _reader_list((_reader_tuple(r, budget=budget) for r in _reader_rows(conn, 'SELECT * FROM identity_name_schema LIMIT 2', budget=budget)), budget=budget)
    if (len(markers) != 1 or markers[0][:2] != (1, 1)
            or not isinstance(markers[0][2], str) or not markers[0][2]
            or (org_slug is not None and markers[0][2] != org_slug)):
        raise NamingError('naming_schema_marker_mismatch')
    return 1


def read_rows(conn, *, budget=None):
    _reader_reserve(budget, 0)
    deadline = time.monotonic() + SCAN_SECONDS
    result = (_reader_reserve(budget, 64) or [])
    total_bytes = 0
    for table in TABLES[1:]:
        columns = ('kind','canonical_id','lifecycle','current_label','revision') if table == TABLES[1] else ('normalized_name','owner_kind','owner_id','id_reserved','permanent')
        sizes = '+'.join(f'coalesce(length(CAST("{c}" AS BLOB)),0)' for c in columns)
        count = 0
        for (size,) in _reader_rows(conn, f'SELECT {sizes} FROM "{table}" LIMIT ?', (_reader_reserve(budget, 80) or (MAX_ROWS + 1,)), budget=budget):
            count += 1
            total_bytes += size
            if count > MAX_ROWS or total_bytes > MAX_ROW_BYTES:
                raise NamingError('naming_row_bound')
            if time.monotonic() > deadline:
                raise NamingError('naming_scan_deadline')
        rows = (_reader_reserve(budget, 64) or [])
        for row in _reader_rows(conn, f'SELECT * FROM "{table}" ORDER BY rowid LIMIT ?', (_reader_reserve(budget, 80) or (MAX_ROWS + 1,)), budget=budget):
            if time.monotonic() > deadline:
                raise NamingError('naming_scan_deadline')
            _reader_append(rows, _reader_tuple(row, budget=budget), budget=budget)
        _reader_append(result, _reader_tuple(rows, budget=budget), budget=budget)
    return _reader_tuple(result, budget=budget)


def validate_names(conn, *, org_slug: str | None = None, budget=None):
    _reader_work((_reader_reserve(budget, 80) or (org_slug,)), budget, copies=2)
    version = naming_version(conn, org_slug=org_slug, budget=budget)
    if version:
        from .registry import validate_rows
        validate_rows(*read_rows(conn, budget=budget), budget=budget)
        if _reader_one(conn, 'PRAGMA foreign_key_check(identity_name_claims)', budget=budget):
            raise NamingError('naming_foreign_key_corrupt')
    return version


def preflight(path: Path, *, org_slug: str):
    """Read-only naming diagnostic before generic construction; never admission."""
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
        try:
            validate_names(conn, org_slug=org_slug)
        finally:
            conn.close()
    except (ValueError, sqlite3.DatabaseError, OSError):
        return 'naming_preflight_unavailable'
    return None


@contextmanager
def transaction(db):
    """One synchronized short transaction, with BaseException rollback."""
    with db._lock:
        conn = db._conn
        if conn.in_transaction:
            raise NamingError('naming_caller_transaction')
        conn.execute('BEGIN IMMEDIATE')
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
