"""Closed org-only naming v1 layout. Generic Database never installs names."""
from __future__ import annotations

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


def _inventory(conn):
    rows = conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name LIMIT ?", (MAX_ROWS + 1,)).fetchall()
    if len(rows) > MAX_ROWS:
        raise NamingError("naming_schema_object_bound")
    objects = tuple(tuple(r) for r in rows if r[1].lower().startswith('identity_name_') or r[2] in TABLES)
    metadata = []
    for table in TABLES:
        columns = tuple(tuple(r) for r in conn.execute(f'PRAGMA table_xinfo("{table}")'))
        foreign = tuple(tuple(r) for r in conn.execute(f'PRAGMA foreign_key_list("{table}")'))
        indexes = tuple(tuple(r) for r in conn.execute(f'PRAGMA index_list("{table}")'))
        details = tuple((r[1], tuple(tuple(x) for x in conn.execute(
            'SELECT * FROM pragma_index_xinfo(?) ORDER BY seqno', (r[1],)
        ))) for r in indexes)
        metadata.append((table, columns, foreign, indexes, details))
    return objects, tuple(metadata)


@lru_cache(maxsize=1)
def _reference_inventory():
    conn = sqlite3.connect(':memory:')
    try:
        execute_literal(conn, REFERENCE_DDL)
        return _inventory(conn)
    finally:
        conn.close()


def naming_version(conn, *, org_slug: str | None = None):
    """Select only absent or complete v1; unknown/partial naming never repairs."""
    actual = _inventory(conn)
    if not actual[0]:
        return 0
    if actual != _reference_inventory():
        raise NamingError('naming_schema_layout_mismatch')
    markers = [tuple(r) for r in conn.execute('SELECT * FROM identity_name_schema LIMIT 2')]
    if (len(markers) != 1 or markers[0][:2] != (1, 1)
            or not isinstance(markers[0][2], str) or not markers[0][2]
            or (org_slug is not None and markers[0][2] != org_slug)):
        raise NamingError('naming_schema_marker_mismatch')
    return 1


def read_rows(conn):
    deadline = time.monotonic() + SCAN_SECONDS
    result = []
    total_bytes = 0
    for table in TABLES[1:]:
        columns = ('kind','canonical_id','lifecycle','current_label','revision') if table == TABLES[1] else ('normalized_name','owner_kind','owner_id','id_reserved','permanent')
        sizes = '+'.join(f'coalesce(length(CAST("{c}" AS BLOB)),0)' for c in columns)
        count = 0
        for (size,) in conn.execute(f'SELECT {sizes} FROM "{table}" LIMIT ?', (MAX_ROWS + 1,)):
            count += 1
            total_bytes += size
            if count > MAX_ROWS or total_bytes > MAX_ROW_BYTES:
                raise NamingError('naming_row_bound')
            if time.monotonic() > deadline:
                raise NamingError('naming_scan_deadline')
        rows = []
        for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid LIMIT ?', (MAX_ROWS + 1,)):
            if time.monotonic() > deadline:
                raise NamingError('naming_scan_deadline')
            rows.append(tuple(row))
        result.append(tuple(rows))
    return tuple(result)


def validate_names(conn, *, org_slug: str | None = None):
    version = naming_version(conn, org_slug=org_slug)
    if version:
        from .registry import validate_rows
        validate_rows(*read_rows(conn))
        if conn.execute('PRAGMA foreign_key_check(identity_name_claims)').fetchone():
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
