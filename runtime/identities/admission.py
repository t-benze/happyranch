"""Installer-only structural naming admission (N-SAC-1).

Validation only: the caller owns the active transaction and captures the absolute
selected DB path before locking. No filesystem I/O, transaction or pragma changes.
Global release-reference consumers retain their separate, stronger contract.
"""
from __future__ import annotations

import re
import sqlite3
import time

from .schema import MAX_ROWS, MAX_ROW_BYTES, SCAN_SECONDS, TABLES, NamingError, validate_names

_IDENTIFIER = r'(?:[A-Za-z_][A-Za-z_0-9]*|"(?:[^"]|"")+"|`(?:[^`]|``)+`|\[[^\]]+\])'
_AUDIT_TYPES = {
    'id': 'INTEGER PRIMARY KEY AUTOINCREMENT',
    'task_id': 'TEXT NOT NULL', 'agent': 'TEXT NOT NULL',
    'action': 'TEXT NOT NULL', 'payload': 'TEXT', 'timestamp': 'TEXT NOT NULL',
}
_DEPENDENCIES = frozenset((*TABLES, 'tasks', 'audit_log', 'task_pause_controls', 'team_move_operations'))


def _refuse() -> None:
    raise NamingError('naming_database_unavailable')


def _identifier(token: str) -> str:
    if token.startswith('"'):
        return token[1:-1].replace('""', '"').lower()
    if token.startswith('`'):
        return token[1:-1].replace('``', '`').lower()
    if token.startswith('['):
        return token[1:-1].lower()
    return token.lower()


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _ordinary(conn: sqlite3.Connection, table: str, *, rowid: bool = False) -> None:
    rows = list(conn.execute('PRAGMA main.table_list'))
    matches = [r for r in rows if r[1].lower() == table]
    if len(matches) != 1 or matches[0][2] != 'table' or (rowid and matches[0][4] != 0):
        _refuse()


def _task_shape(conn: sqlite3.Connection) -> None:
    _ordinary(conn, 'tasks')
    columns = list(conn.execute('PRAGMA main.table_xinfo(tasks)'))
    selected = {r[1].lower(): r for r in columns}
    ident, parent = selected.get('id'), selected.get('parent_task_id')
    if (ident is None or parent is None or ident[2].upper() != 'TEXT'
            or ident[5:] != (1, 0) or sum(bool(r[5]) for r in columns) != 1
            or parent[2].upper() != 'TEXT' or parent[3] != 0 or parent[6] != 0):
        _refuse()
    primary = [r for r in conn.execute('PRAGMA main.index_list(tasks)') if r[3] == 'pk']
    if len(primary) != 1 or not primary[0][2] or primary[0][4]:
        _refuse()
    keys = [r for r in conn.execute(f'PRAGMA main.index_xinfo({_quote(primary[0][1])})') if r[5]]
    if len(keys) != 1 or keys[0][2].lower() != 'id' or keys[0][1] != ident[0] or keys[0][4] != 'BINARY':
        _refuse()
    conn.execute('SELECT id,parent_task_id FROM main.tasks LIMIT 0')


def _audit_shape(conn: sqlite3.Connection, objects: list[tuple]) -> None:
    _ordinary(conn, 'audit_log', rowid=True)
    columns = list(conn.execute('PRAGMA main.table_xinfo(audit_log)'))
    if len(columns) != 6 or {r[1].lower() for r in columns} != set(_AUDIT_TYPES) or any(r[6] for r in columns):
        _refuse()
    definition = next((r[3] for r in objects if r[0] == 'table' and r[1].lower() == 'audit_log'), None)
    if not isinstance(definition, str):
        _refuse()
    match = re.fullmatch(rf'\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?({_IDENTIFIER})\s*\((.*)\)\s*;?\s*', definition, re.I | re.S)
    if not match or _identifier(match[1]) != 'audit_log':
        _refuse()
    seen = set()
    for declaration in match[2].split(','):
        column = re.fullmatch(rf'\s*({_IDENTIFIER})\s+([A-Za-z]+(?:\s+[A-Za-z]+)*)\s*', declaration)
        if not column:
            _refuse()
        name = _identifier(column[1])
        if name in seen or name not in _AUDIT_TYPES or ' '.join(column[2].upper().split()) != _AUDIT_TYPES[name]:
            _refuse()
        seen.add(name)
    if seen != set(_AUDIT_TYPES) or list(conn.execute('PRAGMA main.foreign_key_list(audit_log)')):
        _refuse()
    for row in columns:
        if (row[2].upper() != ('INTEGER' if row[1].lower() == 'id' else 'TEXT')
                or row[3] != int(row[1].lower() not in ('id', 'payload')) or row[4] is not None
                or row[5] != int(row[1].lower() == 'id')):
            _refuse()
    if any(r[0] == 'trigger' and r[2].lower() == 'audit_log' for r in objects):
        _refuse()
    for index in conn.execute('PRAGMA main.index_list(audit_log)'):
        if index[2] or index[3] != 'c':
            _refuse()
        keys = [r for r in conn.execute(f'PRAGMA main.index_xinfo({_quote(index[1])})') if r[5]]
        if not keys or any(r[1] < 0 or r[2].lower() not in _AUDIT_TYPES or r[4] != 'BINARY' for r in keys):
            _refuse()
        if index[4]:
            sql = next((r[3] for r in objects if r[:2] == ('index', index[1])), '')
            predicate = re.search(r'\bWHERE\b(.*)', sql, re.I | re.S)
            allowed = re.fullmatch(rf'\s*({_IDENTIFIER})\s*=\s*\'workspace_cleanup_triggered\'\s*;?\s*', predicate[1]) if predicate else None
            if not allowed or _identifier(allowed[1]) != 'action':
                _refuse()


def _interaction_scan(conn: sqlite3.Connection) -> list[tuple]:
    deadline = time.monotonic() + SCAN_SECONDS
    total_objects = total_bytes = 0
    main = []
    for schema in ('main', 'temp'):
        count, size = conn.execute(
            f'SELECT count(*),coalesce(sum(length(CAST(name AS BLOB))+length(CAST(tbl_name AS BLOB))+coalesce(length(CAST(sql AS BLOB)),0)),0) FROM {schema}.sqlite_schema'
        ).fetchone()
        total_objects += count
        total_bytes += size
        if total_objects > MAX_ROWS or total_bytes > MAX_ROW_BYTES:
            _refuse()
        objects = [tuple(r) for r in conn.execute(f'SELECT type,name,tbl_name,sql FROM {schema}.sqlite_schema LIMIT ?', (MAX_ROWS + 1,))]
        for kind, name, table, sql in objects:
            if time.monotonic() > deadline:
                _refuse()
            if schema == 'temp' and (name.lower() in _DEPENDENCIES or table.lower() in _DEPENDENCIES
                                     or name.lower().startswith('workflow_') or table.lower().startswith('workflow_')):
                _refuse()
            owned = schema == 'main' and table in TABLES
            if not owned and any('identity_name_' in str(v).lower() for v in (name, table, sql or '')):
                _refuse()
            if kind == 'table':
                for fk in conn.execute(f'PRAGMA {schema}.foreign_key_list({_quote(name)})'):
                    if not owned and 'identity_name_' in fk[2].lower():
                        _refuse()
            if time.monotonic() > deadline:
                _refuse()
        if schema == 'main':
            main = objects
    return main


def validate_naming_install_admission(
    conn: sqlite3.Connection, *, org_slug: str, expected_db_path: str,
) -> int:
    """Validate NS1–NS4 under the caller's transaction; return absent/v1."""
    try:
        if (not isinstance(org_slug, str) or not org_slug or not conn.in_transaction
                or not isinstance(expected_db_path, str) or not expected_db_path.startswith('/')
                or conn.execute('PRAGMA foreign_keys').fetchone()[0] != 1):
            _refuse()
        databases = [tuple(r) for r in conn.execute('PRAGMA database_list')]
        main = [r for r in databases if r[1] == 'main']
        if (len(main) != 1 or not main[0][2] or main[0][2] != expected_db_path
                or any(r[1] not in ('main', 'temp') for r in databases)):
            _refuse()
        objects = _interaction_scan(conn)
        _task_shape(conn)
        _audit_shape(conn, objects)
        # Callable-local imports avoid workflow -> naming reader import cycles.
        from runtime.infrastructure.workflow_schema import validate_workflow_schema
        from runtime.infrastructure.task_pause_controls import validate_pause_schema
        from runtime.infrastructure.team_move_store import validate_team_move_schema
        validate_workflow_schema(conn, expected_org_slug=org_slug)
        validate_pause_schema(conn, org_slug=org_slug, allow_absent=True)
        validate_team_move_schema(conn, org_slug=org_slug)
        return validate_names(conn, org_slug=org_slug)
    except NamingError:
        raise
    except Exception as exc:
        # Dependency exceptions and SQLite details never enter public categories.
        raise NamingError('naming_database_unavailable') from exc
