"""Snapshot-local retained absence proof for the separate mixed-write reader.

No positive retained row is authenticated here. A reference or an encoding we
cannot inspect selects the original complete reader, with its original ledgers.
"""
from __future__ import annotations

import base64
import json
import math
import sqlite3
from collections.abc import Iterator
from contextlib import closing

from runtime.infrastructure.workflow_coding_schema import (
    CODING_NAMESPACE_COLUMNS, CODING_PREFIXES, CODING_VALUES,
    _CodingInventory, _coding_metadata_candidate, _coding_scalar, _refuse_coding_metadata,
)
from runtime.infrastructure.workflow_schema import _structure_cells
from runtime.workflows.coding_authority_history import _Budget, _bounded_rows
from runtime.workflows.coding_start_history import QueuedStorage, _sequential_completion_audit
from runtime.workflows._read_exhaustion import exhausted
from runtime.workflows.sequential_history import _ReadBudget
from runtime.workflows.sequential_start_metadata import _reserve


class _UnknownReference(ValueError):
    pass


def _reference(value: object, *, name: str, texts: set[str], results: set[int], result_texts: set[str],
               budget: _Budget, clock: _ReadBudget, structured: bool = False) -> bool:
    """Conservative scalar/JSON walk, including original encoded native rows.

    IDs are never found by a substring/LIKE guess. Unknown encodings are not
    negative evidence. Prepay decode/copy workspace on the retained allowance.
    """
    if structured and value is not None and type(value) not in (str, bytes):
        raise _UnknownReference()
    pending = [(name, value, structured, 0)]

    def pairs(items: list[tuple[str, object]]) -> dict:
        result = {}
        for key, member in items:
            budget.step()
            if key in result:
                raise _UnknownReference()
            result[key] = member
        return result

    while pending:
        clock.check()
        budget.step()
        key, item, encoded, depth = pending.pop()
        if depth > 24:
            raise _UnknownReference()
        if key.endswith(('_base64', '_hex')) and type(item) not in (str, bytes):
            raise _UnknownReference()
        if type(item) is int:
            if (key == 'id' or 'result' in key) and item in results:
                return True
        elif type(item) in (str, bytes):
            if type(item) is str and (item in texts or
                    ((key == 'id' or 'result' in key) and item in result_texts)):
                return True
            if key.endswith(('_base64', '_hex')):
                _reserve(budget, 24 * len(item) + 512)
                try:
                    item = (base64.b64decode(item, validate=True) if key.endswith('_base64')
                            else bytes.fromhex(item))
                except (ValueError, TypeError):
                    raise _UnknownReference() from None
                encoded = True
            if type(item) is bytes:
                _reserve(budget, 4 * len(item) + 512)
                try:
                    item = item.decode('utf8')
                except UnicodeError:
                    raise _UnknownReference() from None
                if item in texts:
                    return True
            if encoded or item.lstrip().startswith(('{', '[')):
                _reserve(budget, 24 * len(item.encode('utf8')) + 512)
                try:
                    parsed = json.loads(item, object_pairs_hook=pairs)
                except (ValueError, RecursionError) as exc:
                    if exhausted(exc):
                        raise
                    raise _UnknownReference() from None
                if type(parsed) not in (dict, list):
                    raise _UnknownReference()
                pending.append((key, parsed, False, depth + 1))
        elif type(item) is dict:
            _reserve(budget, 128 * len(item) + 512)
            # NativeSourceRecord TEXT has base64 in a field named "value".
            if set(item) == {'name', 'storage_class', 'value'}:
                from runtime.workflows.coding_source_history import _AUDIT_COLUMNS, _RESULT_COLUMNS, _TASK_COLUMNS
                if (type(item['name']) is not str or item['name'] not in
                        (*_TASK_COLUMNS, *_RESULT_COLUMNS, *_AUDIT_COLUMNS)):
                    raise _UnknownReference()
                if item['storage_class'] == 'text':
                    if type(item['value']) is not str:
                        raise _UnknownReference()
                    _reserve(budget, 24 * len(item['value']) + 512)
                    try:
                        native = base64.b64decode(item['value'], validate=True).decode('utf8')
                    except (ValueError, TypeError, UnicodeError):
                        raise _UnknownReference() from None
                    pending.append((item['name'], native, False, depth + 1))
                    continue
                if item['storage_class'] not in ('integer', 'real', 'null'):
                    raise _UnknownReference()
                if type(item['value']) is not {'integer': int, 'real': float, 'null': type(None)}[item['storage_class']]:
                    raise _UnknownReference()
                pending.append((item['name'], item['value'], False, depth + 1))
                continue
            for child, member in item.items():
                pending.append((child, member, False, depth + 1))
                pending.append(('', child, False, depth + 1))
        elif type(item) is list:
            _reserve(budget, 128 * len(item) + 512)
            pending.extend((key, member, False, depth + 1) for member in item)
        elif type(item) is float and not math.isfinite(item):
            raise _UnknownReference()
        elif item is not None and type(item) not in (bool, float):
            raise _UnknownReference()
    return False


def read_absent_retained_inventory(conn: sqlite3.Connection, *, metadata: dict,
                                  data: dict[str, list[dict]], task_ids: set[str],
                                  run_ids: set[str], instance_ids: set[str], result_ids: set[int],
                                  budget: _Budget, clock: _ReadBudget) -> _CodingInventory | None:
    """Return a real EMPTY inventory only after exhaustive negative evidence.

    The complete literal layout has already matched in this transaction. Every
    column of every retained workflow table is inspected; native metadata,
    cancellation audits, ownership and Coding/document journals are included.
    None is an internal fallback signal, never a returned retained certificate.
    """
    _reserve(budget, 512 + 128 * (len(task_ids) + len(run_ids) + len(instance_ids) + len(result_ids)))
    texts = task_ids | run_ids | instance_ids
    result_texts = set()
    for result in result_ids:
        budget.step()
        result_texts.add(str(result))

    def ordered(values: set) -> tuple:
        for _ in range(len(values) * max(1, len(values).bit_length())):
            budget.step()
        return tuple(sorted(values))

    def references(name: str, value: object, *, structured: bool = False) -> bool:
        return _reference(value, name=name, texts=texts, results=result_ids, result_texts=result_texts,
                          budget=budget, clock=clock, structured=structured)

    def rows(table: str, columns: tuple[str, ...], *, where: str = '1', args: tuple = ()) -> Iterator[tuple]:
        # Exact schema admission precedes this use of names. Native candidate
        # projections retain the same finite value/row/byte gates too.
        yield from _bounded_rows(conn, table, columns,
            (('text', 'blob', 'integer', 'real', 'null'),) * len(columns), budget, where=where, args=args)

    def native_candidates(columns: tuple[str, ...]) -> tuple[str, tuple]:
        # Ordinary native rows are SQL-only negative candidates, as in the
        # complete discriminator. JSON1 sees escaped/duplicate members; bad
        # JSON, encoded cells and every reserved/unknown format remain suspect.
        # No ordinary output_summary is acquired to prove metadata absence.
        clauses, args = [], ()
        for column in columns:
            quoted = '"' + column.replace('"', '""') + '"'
            text_keys = ordered(texts)
            result_keys = ordered(result_ids) + ordered(result_texts)
            values = ordered({value for group in CODING_VALUES.values() for value in group})
            _reserve(budget, 1024 + 8 * (len(text_keys) + len(result_keys) + len(values))
                     + 2 * sum(len(value.encode('utf8')) for value in (*text_keys, *result_texts)))
            text_match = 'atom IN (' + ','.join('?' for _ in text_keys) + ')'
            result_match = "(key='id' OR instr(key,'result')>0) AND atom IN (" + ','.join('?' for _ in result_keys) + ')'
            reserved = 'atom IN (' + ','.join('?' for _ in values) + ')'
            clauses.append('(' + quoted + ' IS NOT NULL AND CASE '
                'WHEN typeof(' + quoted + ") NOT IN ('text','blob') THEN 1 "
                'WHEN length(CAST(' + quoted + ' AS BLOB))>1048576 THEN 1 '
                'WHEN NOT json_valid(' + quoted + ') THEN 1 ELSE EXISTS ('
                'SELECT 1 FROM json_tree(CASE WHEN json_valid(' + quoted + ') THEN ' + quoted + " ELSE '{}' END) "
                "WHERE key='format' OR key='storage_class' OR instr(key,'base64')>0 OR instr(key,'hex')>0 "
                "OR (key='action' AND (substr(lower(atom),1,7)='coding_' OR atom='workflow_start')) "
                "OR (key IN ('namespace','authority_namespace','org_namespace') AND instr(atom,'coding-setup')>0) "
                'OR ' + text_match + ' OR (' + result_match + ') OR ' + reserved + ') END)')
            args += text_keys + result_keys + values
        return ' OR '.join(clauses) or '0', args

    try:
        for table, columns in metadata.items():
            if not table.startswith('workflow_') or table.startswith('workflow_seq_'):
                continue
            names = tuple(row[1] for row in columns)
            where = "version=1 AND kind!='legacy'" if table == 'workflow_task_ownership' else '1'
            for row in rows(table, names, where=where):
                clock.check()
                for name, value in zip(names, row[:len(names)], strict=True):
                    if references(name, value, structured=name.endswith(('_bytes', '_json'))
                                  or name == 'captured_members'):
                        return None

        sources = {}
        # I deliberately does not follow native revisits. Neither an incoming
        # edge nor an outgoing old-family dependency may become a false EMPTY.
        # Outgoing non-null revisits are conservatively complete-only.
        keys = ordered(texts)
        _reserve(budget, 512 + 16 * len(keys))
        placeholders = ','.join('?' for _ in keys)
        for _ in rows('tasks', ('id', 'revisit_of_task_id'),
                where='revisit_of_task_id IN (' + placeholders + ') OR (id IN (' + placeholders
                      + ') AND revisit_of_task_id IS NOT NULL)', args=keys + keys):
            return None
        for table in ('tasks', 'task_results', 'audit_log'):
            columns = tuple(row[1] for row in _structure_cells(conn, 'SELECT * FROM pragma_table_xinfo(?)',
                ('cid', 'name', 'type', 'notnull', 'dflt_value', 'pk', 'hidden'), ('cid',), clock, (table,)))
            if not columns:
                return None
            scalar = set(CODING_PREFIXES) | set(CODING_VALUES) | set(CODING_NAMESPACE_COLUMNS)
            payloads = tuple(name for name in columns if name.endswith(('_bytes', '_json'))
                             or name in ('payload', 'author_principal', 'published_by', 'active_chain', 'active_fanout')
                             or name in scalar)
            if table == 'audit_log':
                payloads = ('payload',)
            selected = (columns if table == 'audit_log' else
                        tuple(dict.fromkeys(('id', *(('task_id',) if table == 'task_results' else ()), *payloads))))
            where, args = native_candidates(payloads)
            if table == 'audit_log':
                # Other native actions (including @2 result-stage bookkeeping)
                # enter retained history only through an embedded source/event
                # capture, already walked above. They are not retained roots.
                where = ("substr(lower(action),1,7)='coding_' OR substr(action,1,9)='workflow_' OR "
                         "(action IN ('completion_report','org_config_write') AND (" + where + '))')
            for raw in rows(table, selected, where=where, args=args):
                row = dict(zip(selected, raw[:len(selected)], strict=True))
                clock.check()
                if table == 'audit_log':
                    if row['action'] == 'completion_report':
                        recognized = _sequential_completion_audit(row, sources, budget)
                        _reserve(budget, 0)  # A declined recognition cannot swallow exhaustion.
                        if recognized:
                            continue
                    if row['action'].startswith(('coding_', 'workflow_')) and references('task_id', row['task_id']):
                        return None
                    values = [('payload', row['payload'])]
                else:
                    values = [(name, row[name]) for name in payloads]
                for name, value in values:
                    if value is None:
                        continue
                    if table == 'task_results' and name == 'decision_json' and _coding_metadata_candidate(
                            value, location=table + '.' + name):
                        try:
                            _refuse_coding_metadata(value, location=table + '.' + name, result_id=row['id'],
                                                   sequential=data, budget=budget)
                        except ValueError as exc:
                            if exhausted(exc):
                                raise
                        else:
                            from runtime.workflows.coding_source_history import _RESULT_COLUMNS, _RESULT_TYPES
                            source, = _bounded_rows(conn, table, _RESULT_COLUMNS, _RESULT_TYPES, budget,
                                where='id=?', args=(row['id'],), maximum=1)
                            sources[row['id']] = dict(zip(_RESULT_COLUMNS, source[:len(_RESULT_COLUMNS)], strict=True))
                            continue
                    native_task = row['id'] if table == 'tasks' else row['task_id']
                    candidate = (_coding_scalar(name, value) if name in scalar else
                                 _coding_metadata_candidate(value, location=table + '.' + name))
                    if native_task in task_ids and candidate:
                        return None  # The row's native task binding is itself a reference.
                    if references(name, value, structured=name not in scalar):
                        return None

        # The old Coding settings pointer is native storage too. An orphan or
        # future shape cannot be silently certified as unrelated.
        settings = _structure_cells(conn, "SELECT rowid,name FROM sqlite_schema WHERE type='table' "
            "AND name='org_settings'", ('name',), ('rowid',), clock)
        if settings:
            for row in rows('org_settings', ('section', 'value_json', 'updated_at', 'updated_by'),
                            where="section='coding'"):
                if references('value_json', row[1], structured=True):
                    return None

        present = _structure_cells(conn, "SELECT rowid,name FROM sqlite_schema WHERE type='table' "
            "AND name='task_pause_controls'", ('name',), ('rowid',), clock)
        if present:
            from runtime.infrastructure.task_pause_controls import PAUSE_SCHEMA_SQL, PauseControlError, decode_journal
            # Match the original Pause structural contract before interpreting
            # its reference grammar. Extra columns/objects cannot hide links.
            def pause_structure(connection: sqlite3.Connection) -> tuple:
                objects = _structure_cells(connection, "SELECT rowid,type,name,tbl_name,sql FROM sqlite_schema "
                    "WHERE tbl_name='task_pause_controls' OR name='task_pause_controls'",
                    ('type', 'name', 'tbl_name', 'sql'), ('rowid',), clock)
                parts = [tuple(sorted(objects))]
                for pragma, columns, keys in (
                    ('table_xinfo', ('cid', 'name', 'type', 'notnull', 'dflt_value', 'pk', 'hidden'), ('cid',)),
                    ('foreign_key_list', ('id', 'seq', 'table', 'from', 'to', 'on_update', 'on_delete', 'match'), ('id', 'seq')),
                    ('index_list', ('seq', 'name', 'unique', 'origin', 'partial'), ('seq',)),
                ):
                    parts.append(tuple(_structure_cells(connection, 'SELECT * FROM pragma_' + pragma + '(?)',
                        columns, keys, clock, ('task_pause_controls',))))
                return tuple(parts)

            clock.check()
            with closing(sqlite3.connect(':memory:')) as expected:
                expected.execute('CREATE TABLE tasks(id TEXT PRIMARY KEY)')
                expected.execute(PAUSE_SCHEMA_SQL)
                if pause_structure(conn) != pause_structure(expected):
                    return None
            clock.check()
            for row in rows('task_pause_controls', ('root_task_id', 'admission_journal_json')):
                _reserve(budget, 24 * len(row[1].encode('utf8')) + 512)
                try:
                    journal = decode_journal(row[1])
                except PauseControlError:
                    raise _UnknownReference() from None
                for entry in journal['entries']:
                    clock.check()
                    budget.step()
                    if entry['owner'] in ('coding', 'draft') and (
                            references('root_task_id', row[0]) or references('entry', entry)):
                        return None
    except _UnknownReference:
        return None
    # Every queued fact, including reverse point dependencies, is truly empty
    # for this closure. No positive row/release/mapping is fabricated.
    _reserve(budget, 0)
    clock.check()
    return _CodingInventory(budget, None, None, None, QueuedStorage())
