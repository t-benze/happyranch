"""Private task-directed acquisition in one unchanged native snapshot.

This is neither a complete workflow reader nor an effect/publication token.
Callers discard every row at their first write or transaction boundary.
"""
from __future__ import annotations

from runtime.infrastructure._workflow_reader import (
    _b64decode as _reader_b64decode,
    _b64encode as _reader_b64encode,
    _decode as _reader_decode,
    _dump as _reader_dump,
    _encode as _reader_encode,
    _fromhex as _reader_fromhex,
    _hex as _reader_hex,
    _json_dumps as _reader_json_dumps,
)

from runtime.infrastructure._workflow_reader import (
    _add as _reader_add,
    _append as _reader_append,
    _put as _reader_put,
    _setdefault as _reader_setdefault,
    _update as _reader_update,
)

from runtime.infrastructure._workflow_reader import (
    _all_true as _reader_all_true,
    _any_true as _reader_any_true,
)

from runtime.infrastructure._workflow_reader import (
    _dict as _reader_dict,
    _dict_pairs as _reader_dict_pairs,
    _set as _reader_set,
    _tuple as _reader_tuple,
)

from runtime.infrastructure._workflow_reader import (
    _equal as _reader_equal,
    _ReaderBudgetExhausted, _reserve as _reader_reserve,
    _list as _reader_list, _sorted as _reader_sorted,
    _json as _reader_json, _work as _reader_work, _sha256 as _reader_sha256,
)

import hashlib
import json
import math
import re
import base64
from datetime import datetime, timezone
import sqlite3

from runtime.infrastructure.workflow_schema import _candidate_structure, _candidate_reference
from runtime.workflows.coding_authority_history import _Budget, _check, _VALUE_LIMIT
from runtime.workflows.coding_source_history import (
    _TASK_COLUMNS, _TASK_TYPES, _RESULT_COLUMNS, _RESULT_TYPES, _AUDIT_COLUMNS, _AUDIT_TYPES,
)


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


class _OwnerIndexesRequired(ValueError):
    pass


class _Candidate:
    def __init__(self, conn: sqlite3.Connection, task: str, org: str | None, budget: _Budget):
        _check(conn.in_transaction and type(task) is str and bool(task))
        self.conn, self.task, self.org, self.budget = (_reader_reserve(budget, 128) or (conn, task, org, budget))
        actual, native, objects = _candidate_structure(conn, budget)
        self.objects = objects
        names = _reader_set((row[1] for row in actual[0]), budget=budget)
        self.coding = _reader_any_true((name.casefold().startswith('workflow_coding_')
                          or name.casefold() == 'workflow_coding' for row in objects for name in row[1:3]), budget=budget)
        self.layout = ('I' if 'workflow_document_revision_operations' in names else
                       'H' if 'workflow_author_handoffs' in names else
                       'G' if 'workflow_submission_schema_versions' in names else
                       'E' if 'workflow_draft_adapter_versions' in names else 'F')
        if self.coding and self.layout == 'I':
            self.layout = 'J'
        self.workflow = bool(actual[0]) or self.coding
        self.indexed = _reader_any_true((name.startswith('workflow_owner_') for name in names), budget=budget)
        if self.workflow:
            _check(org is not None and type(org) is str and bool(org))
            expected = _candidate_reference(self.layout, coding_extension=self.coding,
                                            owner_indexed=self.indexed, budget=budget)
            # References are independently built release definitions, never
            # reconstructed from these observed objects or their SQL.
            _check(_reader_equal(actual, expected, budget=budget))
            if not self.indexed:
                raise _OwnerIndexesRequired('workflow_owner_indexes_required')
        else:
            expected = ((), (), ())
        self.metadata = _reader_dict_pairs(((_reader_reserve(budget, 96) or (row[0],  row[1] )) for row in expected[1]), budget=budget)
        self.indexes = (_reader_reserve(budget, 64) or {})
        for table, index, detail in expected[2]:
            _reader_append(_reader_setdefault(self.indexes, table, _reader_reserve(budget, 64) or [], budget=budget), (_reader_reserve(budget, 96) or (index, detail)), budget=budget)
        self.foreign = _reader_dict_pairs(((_reader_reserve(budget, 96) or (row[0],  row[2] )) for row in expected[1]), budget=budget)
        _reader_update(self.metadata, _reader_dict_pairs(((_reader_reserve(budget, 96) or (table,  row[0] )) for table, row in native.items()), budget=budget), budget=budget)
        _reader_update(self.foreign, _reader_dict_pairs(((_reader_reserve(budget, 96) or (table,  row[1] )) for table, row in native.items()), budget=budget), budget=budget)
        for table, row in native.items():
            _reader_put(_reader_list(((_reader_reserve(budget, 96) or (index, detail)) for _, index, detail in row[2]), budget=budget), self.indexes, table, budget=budget)
        self._native_schema(native)
        self.acquired: dict[tuple[str, int], dict] = (_reader_reserve(budget, 64) or {})
        self.ranges: dict[tuple, tuple[dict, ...]] = (_reader_reserve(budget, 64) or {})
        self.local: dict[str, dict[int, dict]] = (_reader_reserve(budget, 64) or {})

    def _native_schema(self, native: dict) -> None:
        # Native metadata is inspected in place. Each real reconstruction below
        # is charged; entering this routine performs no deep copy of native.
        for table, columns, types, pk in (_reader_reserve(self.budget, 112) or (
            (_reader_reserve(self.budget, 128) or ('tasks', _TASK_COLUMNS, _TASK_TYPES, 'id')),
            (_reader_reserve(self.budget, 128) or ('task_results', _RESULT_COLUMNS, _RESULT_TYPES, 'id')),
            (_reader_reserve(self.budget, 128) or ('audit_log', _AUDIT_COLUMNS, _AUDIT_TYPES, 'id')),
        )):
            _check(table in native and not native[table][1])
            matches = _reader_list((row for row in self.objects if row[1].casefold() == table), budget=self.budget)
            _check(len(matches) == 1 and matches[0][:2] == (_reader_reserve(self.budget, 96) or ('table', table)))
            metadata = _reader_dict_pairs(((_reader_reserve(self.budget, 96) or (row[1],  row )) for row in native[table][0]), budget=self.budget)
            _check(_reader_set(metadata, budget=self.budget) == _reader_set(columns, budget=self.budget) and _reader_list((r[1] for r in metadata.values() if r[5]), budget=self.budget) == (_reader_reserve(self.budget, 192) or [pk]))
            for name, allowed in zip(columns, types, strict=True):
                row = metadata[name]
                _check(row[2].upper() == next(t.upper() for t in allowed if t != 'null') and row[6] == 0
                       and ('null' in allowed or name == pk or row[3] == 1))
            if table != 'tasks':
                _check(metadata[pk][2] == 'INTEGER' and 'AUTOINCREMENT' in matches[0][3].upper())
        if self.coding:
            for table, columns, types, primary in (
                ('org_settings', ('section', 'value_json', 'updated_at', 'updated_by'),
                 (('text',), ('text',), ('text',), ('null', 'text')), 'section'),
                ('work_hours', ('agent_name', 'status'), (('text',), ('text',)), 'id'),
            ):
                _check(table in native)
                objects = _reader_list((r for r in self.objects if r[1].casefold() == table), budget=self.budget)
                _check(len(objects) == 1 and objects[0][:2] == (_reader_reserve(self.budget, 96) or ('table', table)))
                metadata = _reader_dict_pairs(((_reader_reserve(self.budget, 96) or (r[1],  r )) for r in native[table][0]), budget=self.budget)
                _check(_reader_list((name for name, row in metadata.items() if row[5]), budget=self.budget) == (_reader_reserve(self.budget, 192) or [primary]))
                for name, allowed in zip(columns, types, strict=True):
                    row = metadata.get(name)
                    _check(row is not None and row[2].upper() == next(t.upper() for t in allowed if t != 'null')
                           and row[6] == 0 and ('null' in allowed or name == primary or row[3] == 1))
        # These exact release keys support native task/result/audit negative
        # ranges. Additional unrelated native indexes grant no alternative path.
        required = (_reader_reserve(self.budget, 448) or {
            'tasks': ('idx_tasks_parent', ('parent_task_id',)),
            'task_results': ('idx_cleanup_results_task_agent_id', ('task_id', 'agent', 'id')),
            'audit_log': ('idx_audit_log_task_id', ('task_id',)),
        })
        for table, (name, keys) in required.items():
            matches = _reader_list(((_reader_reserve(self.budget, 96) or (i, d)) for i, d in self.indexes[table] if i[1] == name), budget=self.budget)
            _check(len(matches) == 1)
            index, detail = matches[0]
            key_rows = _reader_list((r for r in detail if r[5]), budget=self.budget)
            _check(_reader_tuple((r[2] for r in key_rows), budget=self.budget) == keys and index[2:] == (0, 'c', 0)
                   and _reader_all_true((r[4] == 'BINARY' and r[3] == int(table == 'task_results' and r[2] == 'id') for r in key_rows), budget=self.budget))
        pause_names = _reader_list((row for row in self.objects if _reader_any_true((name.casefold().startswith('task_pause_') for name in row[1:3]), budget=self.budget)), budget=self.budget)
        if pause_names:
            from runtime.infrastructure.task_pause_controls import PAUSE_SCHEMA_SQL
            # Independent literal construction only; no org/native data read.
            reference = sqlite3.connect(':memory:')
            reference_failure = None
            try:
                reference.execute(PAUSE_SCHEMA_SQL)
                _, expected_native, expected_objects = _candidate_structure(reference, self.budget)
            except BaseException as exc:
                reference_failure = exc
                raise
            finally:
                try:
                    reference.close()
                except BaseException as exc:
                    if reference_failure is None:
                        raise
                    reference_failure.add_note('workflow candidate reference close failed: ' + type(exc).__name__)
            normalize = lambda rows: _reader_sorted(((_reader_reserve(self.budget, 128) or (k, n, t, None if s is None else ' '.join(s.split()))) for k, n, t, s in rows), budget=self.budget)
            _check(_reader_equal(normalize(pause_names), normalize(expected_objects), budget=self.budget)
                   and _reader_equal(native['task_pause_controls'], expected_native['task_pause_controls'], budget=self.budget))

    def _access(self, table: str, values: dict) -> tuple[str, str, tuple[str, ...]]:
        _reader_reserve(self.budget, 0)
        metadata = self.metadata[table]
        primary = _reader_list((r for r in metadata if r[5]), budget=self.budget)
        if len(primary) == 1 and primary[0][2] == 'INTEGER' and primary[0][1] in values:
            return (_reader_reserve(self.budget, 112) or ('', '', _reader_reserve(self.budget, 80) or (primary[0][1],)))
        candidates = (_reader_reserve(self.budget, 64) or [])
        for index, detail in self.indexes[table]:
            keys = _reader_list((r for r in detail if r[5]), budget=self.budget)
            if index[4] and index[1] not in ('workflow_draft_predecessor_idx', 'workflow_draft_result_idx'):
                continue
            prefix = (_reader_reserve(self.budget, 64) or [])
            for key in keys:
                if key[2] not in values or key[4] != 'BINARY':
                    break
                _reader_append(prefix, key[2], budget=self.budget)
            if prefix:
                _reader_append(candidates, (_reader_reserve(self.budget, 128) or (len(prefix), index[1], _reader_tuple(prefix, budget=self.budget), prefix[0] if index[4] else None)), budget=self.budget)
        _check(bool(candidates))
        _, name, prefix, nonnull = max(candidates)
        self.reserve(128 + 32 * (len(name) + len(nonnull or '')))
        return (_reader_reserve(self.budget, 112) or (' INDEXED BY ' + _quote(name),
                f' AND {_quote(nonnull)} IS NOT NULL' if nonnull else '', prefix))

    def rows(self, table: str, column: str, value: object) -> tuple[dict, ...]:
        _reader_reserve(self.budget, 0)
        return self.select(table, **(_reader_reserve(self.budget, 192) or {column: value}))

    def select(self, table: str, **values: object) -> tuple[dict, ...]:
        """Close one reference-authenticated leading-key range in this snapshot.

        This is a private production acquisition primitive, not a SQL adapter or
        a replacement connection. Every caller supplies literal relation keys.
        """
        _reader_reserve(self.budget, 0)
        if table not in self.metadata:
            return (_reader_reserve(self.budget, 64) or [])
        _check(bool(values) and _reader_all_true((type(v) in (_reader_reserve(self.budget, 96) or (str, int)) for v in values.values()), budget=self.budget))
        hint, extra, prefix = self._access(table, values)
        if _reader_set(values, budget=self.budget) != _reader_set(prefix, budget=self.budget):
            # SQL may seek only on a demonstrated leading prefix. Close that
            # whole range first; charge every local suffix comparison, including
            # nonmatches. A WHERE on an unindexed suffix would hide that work.
            rows = self.select(table, **_reader_dict_pairs(((_reader_reserve(self.budget, 96) or (name,  values[name] )) for name in prefix), budget=self.budget))
            found = (_reader_reserve(self.budget, 64) or [])
            for row in rows:
                self.budget.step()
                if _reader_all_true((type(row[name]) is type(value) and row[name] == value
                       for name, value in values.items()), budget=self.budget):
                    self.reserve(8)
                    _reader_append(found, row, budget=self.budget)
            return found
        key = (_reader_reserve(self.budget, 96) or (table, _reader_tuple(values.items(), budget=self.budget)))
        if key in self.ranges:
            return self.ranges[key]
        self.budget.step()
        columns = _reader_tuple((r[1] for r in self.metadata[table]), budget=self.budget)
        _check(_reader_all_true((k in columns for k in values), budget=self.budget))
        quote = _quote
        self.reserve(128)
        for c in columns:
            self.reserve(128 + 32 * len(c))
        quoted_columns = tuple(quote(c) for c in columns)
        self.reserve(128)
        for c in quoted_columns:
            self.reserve(128 + 8 * (40 + 2 * len(c)))
        scalar = ','.join(('rowid', *(f'typeof({c}),length(CAST({c} AS BLOB))' for c in quoted_columns)))
        self.reserve(128)
        for k in values:
            self.reserve(128 + 32 * len(k))
        quoted_keys = tuple(quote(k) for k in values)
        self.reserve(128)
        for k in quoted_keys:
            self.reserve(128 + 8 * (24 + len(k)))
        where = ' AND '.join(f'{k}=? COLLATE BINARY' for k in quoted_keys)
        self.reserve(128 + 32 * len(table))
        quoted_table = quote(table)
        cursor = self.conn.cursor()
        cursor.row_factory = None
        found = (_reader_reserve(self.budget, 64) or [])
        primary = None
        try:
            self.reserve(128 + 4 * (len(scalar) + len(quoted_table) + len(hint) + len(where) + len(extra) + 32))
            cursor.execute(f'SELECT {scalar} FROM {quoted_table}{hint} WHERE {where}{extra}', _reader_tuple(values.values(), budget=self.budget))
            while True:
                self.budget.step()
                # Reserve the possible row before fetch; an exhausted boundary
                # cannot be reinterpreted as an empty completed range.
                self.budget.candidate()
                self.reserve(256 * (2 * len(columns) + 1))
                row = cursor.fetchone()
                if row is None:
                    break
                rowid, cells = (_reader_reserve(self.budget, 96) or (row[0], row[1:]))
                types, sizes = (_reader_reserve(self.budget, 96) or (cells[::2], cells[1::2]))
                _check(type(rowid) is int and _reader_all_true((t in ('text', 'blob', 'integer', 'real', 'null') for t in types), budget=self.budget))
                _check(_reader_all_true((s is None or type(s) is int and 0 <= s <= _VALUE_LIMIT for s in sizes), budget=self.budget))
                cost = sum(s or 0 for t, s in zip(types, sizes, strict=True) if t in ('text', 'blob'))
                self.reserve(cost * 4 + 256 * len(columns) + 256)
                self.budget.candidate()
                self.budget.step()
                self.reserve(128)
                for c in quoted_columns:
                    self.reserve(128 + 8 * (96 + 3 * len(c)))
                projection = ','.join(f"CASE WHEN typeof({c})='text' THEN CAST({c} AS BLOB) ELSE {c} END" for c in quoted_columns)
                payload_cursor = self.conn.cursor()
                payload_cursor.row_factory = None
                payload_failure = None
                try:
                    self.reserve(128 + 4 * (len(projection) + len(quoted_table) + 32))
                    payload_cursor.execute(f'SELECT {projection} FROM {quoted_table} WHERE rowid=?', (_reader_reserve(self.budget, 80) or (rowid,)))
                    payload = payload_cursor.fetchone()
                    _check(payload is not None)
                    self.budget.step()
                    self.budget.candidate()
                    _check(payload_cursor.fetchone() is None)
                    decoded = (_reader_reserve(self.budget, 64) or {})
                    storage_types = (_reader_reserve(self.budget, 576) or {'TEXT': 'text', 'BLOB': 'blob', 'INTEGER': 'integer', 'REAL': 'real'})
                    native_types = (_reader_reserve(self.budget, 704) or {'text': bytes, 'blob': bytes, 'integer': int, 'real': float, 'null': type(None)})
                    for definition, cell, storage, size in zip(self.metadata[table], payload, types, sizes, strict=True):
                        self.budget.step()
                        expected = storage_types[definition[2].upper()]
                        _check(storage == expected or storage == 'null' and not definition[3] and not definition[5])
                        _check(type(cell) is native_types[storage])
                        if storage in ('text', 'blob'):
                            _check(len(cell) == size)
                        if storage == 'real':
                            _check(math.isfinite(cell))
                        _reader_put(_reader_decode(cell, 'utf8', budget=self.budget) if storage == 'text' else cell, decoded, definition[1], budget=self.budget)
                    _check(_reader_all_true((type(decoded[k]) is type(v) and decoded[k] == v for k, v in values.items()), budget=self.budget))
                    _reader_append(found, decoded, budget=self.budget)
                    _reader_put(decoded, self.acquired, (_reader_reserve(self.budget, 96) or (table, rowid)), budget=self.budget)
                    _reader_put(decoded, _reader_setdefault(self.local, table, _reader_reserve(self.budget, 64) or {}, budget=self.budget), rowid, budget=self.budget)
                except BaseException as exc:
                    payload_failure = exc
                    raise
                finally:
                    try:
                        payload_cursor.close()
                    except BaseException as exc:
                        if payload_failure is None:
                            raise
                        payload_failure.add_note('workflow candidate payload close failed: ' + type(exc).__name__)
        except BaseException as exc:
            primary = exc
            raise
        finally:
            try:
                cursor.close()
            except BaseException as exc:
                if primary is None:
                    raise
                primary.add_note('workflow candidate cursor close failed: ' + type(exc).__name__)
        _reader_put(found, self.ranges, key, budget=self.budget)
        return self.ranges[key]

    def reserve(self, size: int) -> None:
        _check(type(size) is int and 0 <= size)
        _reader_reserve(self.budget, size)

    def ordered(self, rows, *columns: str) -> list[dict]:
        _reader_reserve(self.budget, 0)
        _check(type(rows) in (_reader_reserve(self.budget, 96) or (list, tuple)))
        self.reserve(16 * len(rows) * (len(columns) + 1))
        for _ in range(len(rows) * max(1, len(rows).bit_length())):
            self.budget.step()
        return _reader_sorted(rows, key=lambda row: _reader_tuple((row[k] for k in columns), budget=self.budget), budget=self.budget)

    def one(self, table: str, key: object, column: str = 'id') -> dict:
        _reader_reserve(self.budget, 0)
        rows = self.rows(table, column, key)
        _check(len(rows) == 1)
        return rows[0]

    def json(self, raw: bytes, digest: str | None = None) -> object:
        _check(type(raw) is bytes and 0 < len(raw) <= _VALUE_LIMIT)
        # Reserve decode/parser/canonical/hash intermediates before parsing.
        self.reserve(6 * len(raw))
        def pairs(items):
            result = (_reader_reserve(self.budget, 64) or {})
            for key, value in items:
                self.budget.step()
                _check(key not in result)
                _reader_put(value, result, key, budget=self.budget)
            return result
        depth = 0
        quoted = escaped = False
        nodes = 1
        for offset, byte in enumerate(raw):
            if offset % 256 == 0:
                self.budget.step()
            if quoted:
                if escaped:
                    escaped = False
                elif byte == 92:
                    escaped = True
                elif byte == 34:
                    quoted = False
            elif byte == 34:
                quoted = True
            elif byte in (123, 91):
                depth += 1
                nodes += 1
                if depth > 64:
                    raise _ReaderBudgetExhausted('workflow_owner_json_depth')
            elif byte in (125, 93):
                depth -= 1
                _check(depth >= 0)
            elif byte in (44, 58):
                nodes += 1
        self.reserve(32 * nodes)
        for _ in range(nodes):
            self.budget.step()
        _check(depth == 0 and not quoted and not escaped)
        try:
            value = _reader_json(_reader_decode(raw, 'utf8', budget=self.budget), object_pairs_hook=pairs, parse_constant=lambda _: _check(False), budget=self.budget)
        except RecursionError as exc:
            raise _ReaderBudgetExhausted('workflow_owner_json_depth') from exc
        canonical = _reader_encode(_reader_json_dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':'), budget=self.budget), 'utf8', budget=self.budget)
        _check(canonical == raw and (digest is None or _reader_sha256(raw, budget=self.budget).hexdigest() == digest))
        return value

    def native_decision(self, raw: bytes) -> dict:
        from runtime.workflows.coding_start_records import parse_native_decision
        _check(type(raw) is bytes and 0 < len(raw) <= _VALUE_LIMIT)
        self.reserve(8 * len(raw))
        depth = 0
        quoted = escaped = False
        for offset, byte in enumerate(raw):
            if offset % 256 == 0:
                self.budget.step()
            if quoted:
                if escaped:
                    escaped = False
                elif byte == 92:
                    escaped = True
                elif byte == 34:
                    quoted = False
            elif byte == 34:
                quoted = True
            elif byte in (123, 91):
                depth += 1
                self.budget.step()
                _check(depth <= 64)
            elif byte in (125, 93):
                depth -= 1
                _check(depth >= 0)
        _check(depth == 0 and not quoted and not escaped)
        # Native callback JSON preserves its actual wire ordering/whitespace;
        # it was never the workflow canonical-byte storage family.
        return parse_native_decision(raw, budget=self.budget)

    def metadata_check(self, row: dict, table: str) -> None:
        _reader_work((_reader_reserve(self.budget, 96) or (row,table)), self.budget, copies=2)
        from runtime.infrastructure.workflow_coding_schema import _coding_scalar, _refuse_coding_metadata
        for key, value in row.items():
            self.budget.step()
            _check(not _coding_scalar(key, value))
            if key.endswith(('_bytes', '_json')) or key in ('payload', 'author_principal', 'published_by'):
                if key not in ('submission_bytes', 'candidate_bytes'):
                    if type(value) in (_reader_reserve(self.budget, 96) or (str, bytes)):
                        self.reserve(6 * (len(value) if type(value) is bytes else 4 * len(value)))
                    _refuse_coding_metadata(value, location=f'{table}.{key}', budget=self.budget)

    def native(self, task: str) -> tuple[dict, tuple[dict, ...]]:
        _reader_work((_reader_reserve(self.budget, 80) or (task,)), self.budget, copies=2)
        row = self.one('tasks', task)
        # Brief/prose is deliberately not metadata.
        for key in ('active_chain', 'active_fanout', 'blocked_on_job_ids'):
            self.metadata_check((_reader_reserve(self.budget, 192) or {key: row[key]}), 'tasks')
        results = self.rows('task_results', 'task_id', task)
        for result in results:
            raw = result['decision_json']
            if raw is not None:
                try:
                    _reader_reserve(self.budget, 96)
                    _reader_work(raw, self.budget)
                    decision = self.native_decision(_reader_encode(raw, 'utf8', budget=self.budget))
                except _ReaderBudgetExhausted:
                    raise
                except ((ValueError, UnicodeError)):
                    self.metadata_check((_reader_reserve(self.budget, 192) or {'decision_json': raw}), 'task_results')
                else:
                    if type(decision) is dict and decision.get('action') == 'workflow_start':
                        # Pending syntax is not committed ownership. Its actual
                        # complete start consumer still owns admission/spending.
                        _check(result['status'] == 'completed' and result['waiting_on_job_ids'] is None)
                        audits = self.rows('audit_log', 'task_id', task)
                        matching = _reader_list((a for a in audits if a['action'] == 'session_start'
                                    and type(a['payload']) is str and _reader_json(a['payload'], budget=self.budget).get('session_id') == result['session_id']), budget=self.budget)
                        _check(len(matching) == 1 and matching[0]['agent'] == result['agent'])
                        continue
                    self.metadata_check((_reader_reserve(self.budget, 192) or {'decision_json': raw}), 'task_results')
            self.metadata_check(_reader_dict_pairs(((_reader_reserve(self.budget, 96) or (k,  result[k] )) for k in ('risks_flagged', 'waiting_on_job_ids', 'local_ci')), budget=self.budget), 'task_results')
        for audit in self.rows('audit_log', 'task_id', task):
            self.metadata_check(audit, 'audit_log')
        return (_reader_reserve(self.budget, 96) or (row, results))

    def ancestry(self) -> None:
        _reader_reserve(self.budget, 0)
        from runtime.infrastructure.task_pause_controls import decode_journal
        seen, current = (_reader_reserve(self.budget, 96) or (_reader_set(budget=self.budget), self.task))
        while current is not None:
            self.budget.step()
            _check(current not in seen and len(seen) < 20)
            _reader_add(seen, current, budget=self.budget)
            row = self.one('tasks', current)
            parent = row['parent_task_id']
            if parent is None:
                if 'task_pause_controls' in self.metadata:
                    for pause in self.rows('task_pause_controls', 'root_task_id', current):
                        self.metadata_check(pause, 'task_pause_controls')
                        self.reserve(24 * len(pause['admission_journal_json']))
                        journal = decode_journal(pause['admission_journal_json'], budget=self.budget)
                        for entry in journal['entries']:
                            self.budget.step()
                            _check(entry['root_task_id'] == current and entry['org'] == self.org)
                            child, chain = (_reader_reserve(self.budget, 96) or (entry['task_id'], _reader_set(budget=self.budget)))
                            while child != current:
                                self.budget.step()
                                _check(child not in chain and len(chain) < 20)
                                _reader_add(chain, child, budget=self.budget)
                                child = self.one('tasks', child)['parent_task_id']
                                _check(child is not None)
                return
            current = parent

    def entry(self) -> tuple[bool, set[str]]:
        """Every scalar task/result/source entry; no current/state shortcuts."""
        _reader_reserve(self.budget, 0)
        task = self.one('tasks', self.task)
        results = self.rows('task_results', 'task_id', self.task)
        instances = _reader_set(budget=self.budget)
        owner = False
        for table in ('workflow_instances', 'workflow_coding_attempts', 'workflow_draft_dispatch_intents',
                      'workflow_request_task_bridges', 'workflow_instance_tasks', 'workflow_task_results',
                      'workflow_current_assignments', 'workflow_receipt_evidence', 'workflow_submissions',
                      'workflow_submission_contributors', 'workflow_instance_contributors',
                      'workflow_dispatch_callbacks', 'workflow_dispatch_effects', 'workflow_submission_operations',
                      'workflow_submission_result_links', 'workflow_author_handoffs'):
            column = 'root_task_id' if table == 'workflow_instances' else (
                'source_task_id' if table in ('workflow_submissions', 'workflow_submission_contributors',
                    'workflow_instance_contributors', 'workflow_submission_operations',
                    'workflow_submission_result_links', 'workflow_author_handoffs') else 'task_id')
            for row in self.rows(table, column, self.task):
                if table in ('workflow_instances', 'workflow_coding_attempts', 'workflow_draft_dispatch_intents',
                             'workflow_request_task_bridges'):
                    owner = True
                if table == 'workflow_instances':
                    _reader_add(instances, row['id'], budget=self.budget)
                elif 'instance_id' in row:
                    _reader_add(instances, row['instance_id'], budget=self.budget)
        for source in (_reader_reserve(self.budget, 128 + 32 * (1 + len(results))) or (self.task, *((_reader_reserve(self.budget, 128) or str(r['id'])) for r in results))):
            for context in self.rows('workflow_contexts', 'source_id', source):
                if context['source_kind'] == 'coding-manager-result':
                    owner = True
                for row in self.rows('workflow_instances', 'context_id', context['id']):
                    _reader_add(instances, row['id'], budget=self.budget)
                _check(self.rows('workflow_instances', 'context_id', context['id']))
        for result in results:
            number, literal = (_reader_reserve(self.budget, 96) or (result['id'], (_reader_reserve(self.budget, 128) or str(result['id']))))
            for table, column, value in (_reader_reserve(self.budget, 128 + 32 * (7 + len(('workflow_submissions', 'workflow_submission_contributors', 'workflow_instance_contributors')))) or (
                (_reader_reserve(self.budget, 112) or ('workflow_task_results', 'id', literal)), (_reader_reserve(self.budget, 112) or ('workflow_request_task_bridges', 'result_id', literal)),
                (_reader_reserve(self.budget, 112) or ('workflow_dispatch_callbacks', 'result_id', literal)),
                (_reader_reserve(self.budget, 112) or ('workflow_draft_dispatch_intents', 'final_result_id', number)),
                (_reader_reserve(self.budget, 112) or ('workflow_draft_dispatch_events', 'result_id', number)),
                (_reader_reserve(self.budget, 112) or ('workflow_submission_result_links', 'task_result_id', number)),
                (_reader_reserve(self.budget, 112) or ('workflow_coding_attempts', 'result_id', number)),
                *((_reader_reserve(self.budget, 112) or (t, 'source_result_id', literal)) for t in ('workflow_submissions',
                    'workflow_submission_contributors', 'workflow_instance_contributors')),
            )):
                for row in self.rows(table, column, value):
                    task_column = 'source_task_id' if 'source_task_id' in row else 'task_id'
                    if task_column in row:
                        _check(row[task_column] == self.task)
                    if 'instance_id' in row:
                        _reader_add(instances, row['instance_id'], budget=self.budget)
                    if table in ('workflow_request_task_bridges', 'workflow_dispatch_callbacks',
                                 'workflow_draft_dispatch_intents', 'workflow_draft_dispatch_events',
                                 'workflow_submission_result_links', 'workflow_coding_attempts'):
                        owner = True
        for (table, _), row in _reader_list(self.acquired.items(), budget=self.budget):
            self.budget.step()
            if table == 'workflow_receipt_evidence':
                receipt = self.one('workflow_review_receipts', row['receipt_id'])
                request = self.one('workflow_review_requests', receipt['request_id'])
                _reader_add(instances, self.one('workflow_rounds', request['round_id'])['instance_id'], budget=self.budget)
            elif table in ('workflow_dispatch_callbacks', 'workflow_dispatch_effects'):
                outbox = self.one('workflow_dispatch_outbox', row['outbox_id'])
                operation = self.one('workflow_dispatch_operations', outbox['operation_id'])
                _reader_add(instances, operation['instance_id'], budget=self.budget)
                owner = True
            elif table == 'workflow_draft_dispatch_events':
                _reader_add(instances, self.one('workflow_draft_dispatch_intents', row['intent_id'])['instance_id'], budget=self.budget)
                owner = True
            elif table in ('workflow_submission_contributors', 'workflow_submission_result_links'):
                _reader_add(instances, self.one('workflow_submissions', row['submission_id'])['instance_id'], budget=self.budget)
        if not owner:
            self.native(self.task)
            self.ancestry()
        return (_reader_reserve(self.budget, 96) or (owner, instances))

    def canonical(self, value: object) -> bytes:
        # Reserve each actual serialization before constructing its UTF8 copy.
        def size(item):
            self.budget.step()
            if type(item) is str:
                self.reserve(6 * len(item) + 2)
            elif type(item) in (_reader_reserve(self.budget, 112) or (dict, list, tuple)):
                self.reserve(2 + 2 * len(item))
                if type(item) is dict:
                    for key, member in item.items():
                        size(key)
                        size(member)
                else:
                    for member in item:
                        size(member)
            else:
                _check(item is None or type(item) in (_reader_reserve(self.budget, 112) or (bool, int, float)))
                self.reserve(32)
        size(value)
        return _reader_encode(_reader_json_dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':'), budget=self.budget), 'utf8', budget=self.budget)

    def hex(self, raw: bytes) -> str:
        _reader_reserve(self.budget, 0)
        _check(type(raw) is bytes)
        self.reserve(2 * len(raw))
        self.budget.step()
        return _reader_hex(raw, budget=self.budget)

    def unhex(self, value: str) -> bytes:
        _reader_reserve(self.budget, 0)
        _check(type(value) is str)
        self.reserve(len(value))
        self.budget.step()
        return _reader_fromhex(value, budget=self.budget)

    def unbase64(self, value: str) -> bytes:
        _reader_reserve(self.budget, 0)
        import base64
        _check(type(value) is str)
        self.reserve(2 * len(value))
        self.budget.step()
        return _reader_b64decode(value, validate=True, budget=self.budget)

    def union(self, table: str, *groups) -> list[dict]:
        _reader_work((_reader_reserve(self.budget, 80) or (table,)), self.budget, copies=2)
        keys = _reader_list((r[1] for r in self.metadata[table] if r[5]), budget=self.budget)
        seen = (_reader_reserve(self.budget, 64) or {})
        from runtime.workflows.review_dispatch import _same_value
        for rows in groups:
            for row in rows:
                self.budget.step()
                identity = _reader_tuple((row[k] for k in keys), budget=self.budget)
                if identity in seen:
                    _check(_same_value(seen[identity], row, budget=self.budget))
                else:
                    self.reserve(16 * (1 + len(keys)))
                    _reader_put(row, seen, identity, budget=self.budget)
        return _reader_list(seen.values(), budget=self.budget)

    def round_receipts(self, round_id: str) -> list[dict]:
        _reader_reserve(self.budget, 0)
        return _reader_list((p for q in self.rows('workflow_review_requests', 'round_id', round_id)
                for p in self.rows('workflow_review_receipts', 'request_id', q['id'])), budget=self.budget)

    def request_evidence(self, request_id: str) -> list[dict]:
        _reader_reserve(self.budget, 0)
        return _reader_list((e for p in self.rows('workflow_review_receipts', 'request_id', request_id)
                for e in self.rows('workflow_receipt_evidence', 'receipt_id', p['id'])), budget=self.budget)

    def instance_links(self, instance: str) -> list[dict]:
        _reader_reserve(self.budget, 0)
        return _reader_list((link for s in self.rows('workflow_submissions', 'instance_id', instance)
                for link in self.rows('workflow_submission_result_links', 'submission_id', s['id'])), budget=self.budget)

    def revision_links(self, instance: str) -> list[dict]:
        _reader_reserve(self.budget, 0)
        return _reader_list((link for s in self.select('workflow_submissions', instance_id=instance, revision=2)
                for link in self.rows('workflow_submission_result_links', 'submission_id', s['id'])), budget=self.budget)

    def subject_rounds(self, instance: str) -> list[dict]:
        _reader_reserve(self.budget, 0)
        result = (_reader_reserve(self.budget, 64) or [])
        for row in self.rows('workflow_rounds', 'instance_id', instance):
            subject = self.one('workflow_submissions', row['submission_id'])
            self.reserve(32 * len(row))
            _reader_append(result, dict(row, subject_instance=subject['instance_id'], subject_revision=subject['revision']), budget=self.budget)
        return result

    def orphan_draft_events(self, instance: str) -> list[dict]:
        _reader_reserve(self.budget, 0)
        result = (_reader_reserve(self.budget, 64) or [])
        for row in _reader_list(self.local.get('workflow_draft_dispatch_events', (_reader_reserve(self.budget, 64) or {})).values(), budget=self.budget):
            self.budget.step()
            intent = self.one('workflow_draft_dispatch_intents', row['intent_id'])
            _check(type(intent['instance_id']) is str)
        return result

    def template(self, *, identity_id: str, version: int,
                 expected_version_id: str | None = None):
        _reader_work((), self.budget, copies=2)
        from runtime.workflows.templates import WorkflowTemplateVersion, _validate_definition_pins
        identity = self.one('workflow_template_identities', identity_id)
        mapping = self.select('workflow_template_identity_versions', template_identity_id=identity_id, version=version)
        _check(len(mapping) == 1)
        row = self.one('workflow_template_versions', mapping[0]['template_version_id'])
        _check(row['namespace'] != 'release/happyranch' and row['namespace'] == identity['namespace']
               and row['template_name'] == identity['template_name'] and type(version) is int
               and row['version'] == version and mapping[0]['content_digest'] == row['definition_digest']
               and (expected_version_id is None or row['id'] == expected_version_id))
        value = self.json(row['definition_bytes'], row['definition_digest'])
        self.reserve(8 * len(row['definition_bytes']))
        _check(_validate_definition_pins(value, compiler_pin=row['compiler_pin'],
               validator_pin=row['validator_pin'], source_pin=row['source_pin'], budget=self.budget) == row['definition_bytes'])
        publisher = self.json(_reader_encode(row['published_by'], 'utf8', budget=self.budget))
        _check(type(publisher) is dict)
        return WorkflowTemplateVersion(identity_id=identity_id, version_id=row['id'],
            namespace=row['namespace'], template_name=row['template_name'], version=version,
            definition_bytes=row['definition_bytes'], definition_digest=row['definition_digest'],
            compiler_pin=row['compiler_pin'], validator_pin=row['validator_pin'], source_pin=row['source_pin'],
            publisher=publisher, published_at=row['published_at'])

    def publication(self, namespace: str, generation: int, snapshot_digest: str) -> list[dict]:
        # The original namespace/state/generation key permits a finite union
        # over all seven literal states. Never scan every org publication.
        _reader_work((_reader_reserve(self.budget, 112) or (namespace,generation,snapshot_digest)), self.budget, copies=2)
        states = ('prepared', 'file_phase_reserved', 'canonical_published',
                  'forward_recovery_required', 'pointer_committed', 'cache_installed', 'aborted')
        return _reader_list((row for state in states
                for row in self.select('workflow_publication_journals', namespace=namespace,
                                       state=state, generation=generation)
                if row['snapshot_digest'] == snapshot_digest), budget=self.budget)

    def stage_keys(self, *, org_slug: str, principal: str, operation_key: str) -> list[dict]:
        """Compare every REACHED K; unassociated foreign key absence is omitted."""
        _reader_reserve(self.budget, 0)
        result = (_reader_reserve(self.budget, 64) or [])
        for row in _reader_list(self.local.get('workflow_dispatch_events', (_reader_reserve(self.budget, 64) or {})).values(), budget=self.budget):
            self.budget.step()
            operation = self.one('workflow_dispatch_operations', row['operation_id'])
            if (row['event_kind'] == 'review_staged' and operation['org_slug'] == org_slug
                    and operation['principal'] == principal):
                body = self.json(row['event_bytes'], row['event_digest'])
                if body['stage']['command']['operation_key'] == operation_key:
                    _reader_append(result, row, budget=self.budget)
        return result

    def human_keys(self, *, org_slug: str, operation_key: str) -> list[dict]:
        """Compare every REACHED L in its distinct org/human:founder domain."""
        _reader_reserve(self.budget, 0)
        result = (_reader_reserve(self.budget, 64) or [])
        for row in _reader_list(self.local.get('workflow_review_receipts', (_reader_reserve(self.budget, 64) or {})).values(), budget=self.budget):
            self.budget.step()
            request = self.one('workflow_review_requests', row['request_id'])
            scope = self.json(request['request_scope_bytes'], request['request_scope_digest'])
            if scope.get('kind') != 'human':
                continue
            _check(request['principal'] == 'founder')
            round_ = self.one('workflow_rounds', request['round_id'])
            operations = self.rows('workflow_submission_operations', 'submission_id', round_['submission_id'])
            _check(len(operations) == 1)
            if operations[0]['org_slug'] == org_slug:
                proof = self.json(row['proof_bytes'], row['proof_digest'])
                _check(proof['format'] == f'workflow-human-review-proof@{review_family(scope, budget=self.budget)}'
                       and proof['operation_principal'] == 'human:founder')
                if proof['command']['operation_key'] == operation_key:
                    _reader_append(result, row, budget=self.budget)
        return result

    def handoff(self, raw: bytes, digest: str | None = None) -> dict:
        _reader_work((_reader_reserve(self.budget, 96) or (raw,digest)), self.budget, copies=2)
        value = self.json(raw, digest)
        _check(type(value) is dict)
        def strict(item):
            self.budget.step()
            _check(type(item) is not float)
            if type(item) is dict:
                for member in item.values():
                    strict(member)
            elif type(item) is list:
                for member in item:
                    strict(member)
        strict(value)
        return value

    def parents(self, table: str, row: dict) -> None:
        _reader_work((_reader_reserve(self.budget, 96) or (table,row)), self.budget, copies=2)
        grouped = (_reader_reserve(self.budget, 64) or {})
        for fk in self.foreign.get(table, ()):
            _reader_append(_reader_setdefault(grouped, fk[0], _reader_reserve(self.budget, 64) or [], budget=self.budget), fk, budget=self.budget)
        for foreign in grouped.values():
            self.budget.step()
            if _reader_any_true((row[fk[3]] is None for fk in foreign), budget=self.budget):
                continue
            target = foreign[0][2]
            parent = self.select(target, **_reader_dict_pairs(((_reader_reserve(self.budget, 96) or (fk[4],  row[fk[3]] )) for fk in foreign), budget=self.budget))
            _check(len(parent) == 1)

    def result_aliases(self, literal: str) -> None:
        _reader_reserve(self.budget, 0)
        self.rows('workflow_task_results', 'id', literal)
        for table, column in (
            ('workflow_contexts', 'source_id'), ('workflow_request_task_bridges', 'result_id'),
            ('workflow_dispatch_callbacks', 'result_id'), ('workflow_submissions', 'source_result_id'),
            ('workflow_submission_contributors', 'source_result_id'),
            ('workflow_instance_contributors', 'source_result_id'),
            ('workflow_receipt_evidence', 'result_id'), ('workflow_current_assignments', 'result_id'),
        ):
            self.rows(table, column, literal)
        identities = (_reader_reserve(self.budget, 64) or [])
        for row in self.rows('workflow_task_results', 'id', literal):
            _reader_append(identities, (_reader_reserve(self.budget, 112) or (row['task_id'], row['session_id'], row['principal'])), budget=self.budget)
        for table in ('workflow_submissions', 'workflow_submission_contributors', 'workflow_instance_contributors'):
            for row in self.rows(table, 'source_result_id', literal):
                principal = row['author_principal'] if table == 'workflow_submissions' else row['principal']
                _reader_append(identities, (_reader_reserve(self.budget, 112) or (row['source_task_id'], row['source_session_id'], principal)), budget=self.budget)
        for native in self.rows('task_results', 'task_id', self.task):
            if (_reader_reserve(self.budget, 128) or str(native['id'])) == literal:
                _check(native['status'] == 'completed')
                _reader_append(identities, (_reader_reserve(self.budget, 112) or (native['task_id'], native['session_id'], native['agent'])), budget=self.budget)
        if identities:
            _check(_reader_all_true((value == identities[0] for value in identities), budget=self.budget))

    def close_instances(self, instances: set[str]) -> None:
        """Directed producer closure; shared authority/template do not fan out."""
        _reader_work((_reader_reserve(self.budget, 80) or (instances,)), self.budget, copies=2)
        bindings = (_reader_reserve(self.budget, 64) or {})
        for instance_id in _reader_sorted(instances, budget=self.budget):
            instance = self.one('workflow_instances', instance_id)
            binding = self.one('workflow_binding_snapshots', instance['binding_snapshot_id'])
            context = self.one('workflow_contexts', instance['context_id'])
            _check(context['binding_snapshot_id'] == binding['id'])
            self.one('workflow_authorization_revisions', binding['authorization_revision_id'])
            version = self.one('workflow_template_versions', binding['template_version_id'])
            self.one('workflow_template_drafts', version['draft_id'])
            for alias in self.rows('workflow_instances', 'binding_snapshot_id', binding['id']):
                _check(alias['id'] == instance_id)
            self.rows('workflow_contexts', 'binding_snapshot_id', binding['id'])
            for table in ('workflow_instance_tasks', 'workflow_task_results', 'workflow_current_assignments',
                          'workflow_instance_contributors', 'workflow_submissions', 'workflow_rounds',
                          'workflow_events', 'workflow_operation_replays', 'workflow_activations',
                          'workflow_active_activations', 'workflow_dispatch_operations',
                          'workflow_request_task_bridges', 'workflow_draft_dispatch_intents',
                          'workflow_author_handoffs', 'workflow_document_revision_operations'):
                self.rows(table, 'instance_id', instance_id)
            # A coding witness is a positive owner-boundary selection, never U3.
            _check(not self.rows('workflow_coding_runs', 'instance_id', instance_id))
            _check(not self.rows('workflow_coding_attempts', 'instance_id', instance_id))
            _check(not self.rows('workflow_coding_events', 'instance_id', instance_id))
            _check(binding['id'] not in bindings)
            _reader_put(instance_id, bindings, binding['id'], budget=self.budget)
        seen = _reader_set(budget=self.budget)
        while True:
            # Build the snapshot before any acquisition can extend local.
            # Charge examined ALREADY-SEEN rows again on each closure pass.
            pending = (_reader_reserve(self.budget, 64) or [])
            self.reserve(64)
            for table, rows in self.local.items():
                self.budget.step()
                for key, row in rows.items():
                    self.budget.step()
                    if (_reader_reserve(self.budget, 96) or (table, key)) not in seen:
                        self.reserve(128)
                        _reader_append(pending, (_reader_reserve(self.budget, 112) or (table, key, row)), budget=self.budget)
            if not pending:
                break
            for table, key, row in pending:
                self.budget.step()
                _reader_add(seen, (_reader_reserve(self.budget, 96) or (table, key)), budget=self.budget)
                if table.startswith('workflow_'):
                    self.parents(table, row)
                    self.metadata_check(row, table)
                if 'instance_id' in row:
                    _check(row['instance_id'] in instances)
                if table == 'workflow_contexts':
                    _check(row['binding_snapshot_id'] in bindings)
                    for alias in self.rows('workflow_instances', 'context_id', row['id']):
                        _check(alias['id'] == bindings[row['binding_snapshot_id']])
                    for target, column in (
                        ('workflow_receipt_evidence', 'context_id'),
                        ('workflow_draft_dispatch_intents', 'context_id'),
                        ('workflow_document_revision_operations', 'context_id')):
                        self.rows(target, column, row['id'])
                elif table == 'workflow_binding_snapshots':
                    _check(row['id'] in bindings)
                    for target in ('workflow_receipt_evidence', 'workflow_draft_dispatch_intents',
                                   'workflow_document_revision_operations'):
                        self.rows(target, 'binding_snapshot_id', row['id'])
                elif table == 'workflow_activations':
                    self.rows('workflow_activation_operations', 'activation_id', row['id'])
                    self.rows('workflow_active_activations', 'activation_id', row['id'])
                    self.rows('workflow_draft_dispatch_intents', 'activation_id', row['id'])
                    self.rows('workflow_document_revision_operations', 'activation_id', row['id'])
                elif table == 'workflow_submissions':
                    for target, column in (('workflow_submission_contributors', 'submission_id'),
                        ('workflow_rounds', 'submission_id'), ('workflow_review_receipts', 'submission_id'),
                        ('workflow_submission_operations', 'submission_id'),
                        ('workflow_submission_result_links', 'submission_id'),
                        ('workflow_author_handoffs', 'submission_id'),
                        ('workflow_document_revision_operations', 'predecessor_submission_id'),
                        ('workflow_coding_attempts', 'submission_id')):
                        self.rows(target, column, row['id'])
                    if row['source_result_id'] is not None:
                        self.result_aliases(row['source_result_id'])
                elif table == 'workflow_rounds':
                    self.rows('workflow_review_requests', 'round_id', row['id'])
                    self.rows('workflow_document_revision_operations', 'predecessor_round_id', row['id'])
                elif table == 'workflow_review_requests':
                    for target, column in (('workflow_review_receipts', 'request_id'),
                        ('workflow_review_requests', 'supersedes_request_id'),
                        ('workflow_dispatch_operations', 'request_id'),
                        ('workflow_request_task_bridges', 'request_id'),
                        ('workflow_dispatch_outbox', 'request_id'),
                        ('workflow_coding_attempts', 'review_request_id')):
                        self.rows(target, column, row['id'])
                elif table == 'workflow_review_receipts':
                    self.rows('workflow_receipt_evidence', 'receipt_id', row['id'])
                    self.rows('workflow_review_receipts', 'supersedes_receipt_id', row['id'])
                elif table == 'workflow_task_results':
                    self.result_aliases(row['id'])
                elif table in ('workflow_submission_contributors', 'workflow_instance_contributors'):
                    self.result_aliases(row['source_result_id'])
                elif table == 'workflow_operation_replays':
                    _check(self.one('workflow_events', row['effect_id'])['instance_id'] == row['instance_id'])
                    self.rows('workflow_operation_replays', 'effect_id', row['effect_id'])
                elif table == 'workflow_dispatch_operations':
                    for target, column in (('workflow_request_task_bridges', 'operation_id'),
                        ('workflow_dispatch_outbox', 'operation_id'), ('workflow_dispatch_events', 'operation_id')):
                        self.rows(target, column, row['id'])
                elif table == 'workflow_dispatch_outbox':
                    self.rows('workflow_dispatch_callbacks', 'outbox_id', row['id'])
                    self.rows('workflow_dispatch_effects', 'outbox_id', row['id'])
                elif table == 'workflow_draft_dispatch_intents':
                    self.rows('workflow_draft_dispatch_intents', 'predecessor_intent_id', row['id'])
                    self.rows('workflow_draft_dispatch_events', 'intent_id', row['id'])
                    self.rows('workflow_author_handoffs', 'intent_id', row['id'])
                    self.rows('workflow_document_revision_operations', 'draft_intent_id', row['id'])
                elif table == 'workflow_events':
                    self.rows('workflow_operation_replays', 'effect_id', row['id'])
                if table in ('workflow_draft_dispatch_intents', 'workflow_request_task_bridges'):
                    # These native workers cannot create ordinary child work.
                    _check(not self.rows('tasks', 'parent_task_id', row['task_id']))
        for row in self.local.get('workflow_coding_attempts', (_reader_reserve(self.budget, 64) or {})).values():
            self.budget.step()
            _check(False)  # No retained coding execution can become inert history.

    def legacy_instance(self, instance_id: str) -> None:
        """Literal old F/F2 relations; no invented modern callback or receipt."""
        _reader_work((_reader_reserve(self.budget, 80) or (instance_id,)), self.budget, copies=2)
        from runtime.workflows.templates import _validate_definition_pins
        instance = self.one('workflow_instances', instance_id)
        binding = self.one('workflow_binding_snapshots', instance['binding_snapshot_id'])
        context = self.one('workflow_contexts', instance['context_id'])
        grant = self.one('workflow_authorization_revisions', binding['authorization_revision_id'])
        version = self.one('workflow_template_versions', binding['template_version_id'])
        draft = self.one('workflow_template_drafts', version['draft_id'])
        for row, raw, dg in (_reader_reserve(self.budget, 144) or ((_reader_reserve(self.budget, 112) or (binding, 'binding_bytes', 'binding_digest')),
                             (_reader_reserve(self.budget, 112) or (context, 'context_bytes', 'context_digest')),
                             (_reader_reserve(self.budget, 112) or (grant, 'authority_bytes', 'authority_digest')),
                             (_reader_reserve(self.budget, 112) or (version, 'definition_bytes', 'definition_digest')),
                             (_reader_reserve(self.budget, 112) or (draft, 'definition_bytes', 'definition_digest')))):
            value = self.json(row[raw], row[dg])
            if raw == 'definition_bytes':
                self.reserve(8 * len(row[raw]))
                _check(_validate_definition_pins(value, compiler_pin=row['compiler_pin'],
                    validator_pin=row['validator_pin'], source_pin=row['source_pin'], budget=self.budget) == row[raw])
        for name in ('namespace', 'template_name', 'definition_bytes', 'definition_digest',
                     'compiler_pin', 'validator_pin', 'source_pin'):
            self.budget.step()
            _check(version[name] == draft[name])
        _check(context['binding_snapshot_id'] == binding['id'])
        if version['compiler_pin'] in ('workflow-compiler@3', 'workflow-compiler@4'):
            _check(not self.rows('workflow_submissions', 'instance_id', instance_id))
        if context['source_kind'] == 'founder-activation':
            _check(bool(self.rows('workflow_draft_dispatch_intents', 'instance_id', instance_id)))
        # Only a supported old source representation enters this inert branch.
        _check(context['source_kind'] in ('task', 'founder-activation'))
        for alias in self.rows('workflow_contexts', 'binding_snapshot_id', binding['id']):
            _check(alias['id'] == context['id'])
        for task in self.rows('workflow_instance_tasks', 'instance_id', instance_id):
            self.budget.step()
            _check(task['state'] == 'completed' and type(task['generation']) is int and task['generation'] > 0
                   and _reader_all_true((type(task[k]) is str and bool(task[k]) for k in ('task_id', 'session_id', 'role_key')), budget=self.budget))
        for result in self.rows('workflow_task_results', 'instance_id', instance_id):
            self.budget.step()
            self.archival(result['result_bytes'])
            _check(result['lifecycle'] == 'completed' and _reader_sha256(result['result_bytes'], budget=self.budget).hexdigest() == result['result_digest'])
            tasks = self.select('workflow_instance_tasks', instance_id=instance_id, task_id=result['task_id'], session_id=result['session_id'])
            _check(len(tasks) == 1 and tasks[0]['generation'] == result['generation'])
        contributors = _reader_set((r['principal'] for r in self.rows('workflow_instance_contributors', 'instance_id', instance_id)), budget=self.budget)
        for assignment in self.rows('workflow_current_assignments', 'instance_id', instance_id):
            result = self.one('workflow_task_results', assignment['result_id'])
            task = self.select('workflow_instance_tasks', instance_id=instance_id,
                               task_id=assignment['task_id'], session_id=assignment['session_id'])
            _check(len(task) == 1 and task[0]['role_key'] == assignment['role_key'])
            if assignment['role_key'].startswith('finalizer:'):
                _check(assignment['role_key'] == 'finalizer:' + assignment['principal']
                       and assignment['principal'] not in contributors)
            _check(_reader_all_true((assignment[k] == result[k] for k in ('instance_id', 'principal', 'task_id', 'session_id', 'generation', 'lifecycle')), budget=self.budget))
        for submission in self.rows('workflow_submissions', 'instance_id', instance_id):
            _check((submission['source_result_id'] is not None or self.rows('workflow_submission_operations', 'submission_id', submission['id']))
                   and _reader_sha256(submission['submission_bytes'], budget=self.budget).hexdigest() == submission['submission_digest'])
            for contributor in self.rows('workflow_submission_contributors', 'submission_id', submission['id']):
                candidates = self.select('workflow_instance_contributors', instance_id=instance_id,
                    **_reader_dict_pairs(((_reader_reserve(self.budget, 96) or (k,  contributor[k] )) for k in ('principal', 'source_task_id', 'source_session_id', 'source_result_id')), budget=self.budget))
                _check(len(candidates) == 1 and candidates[0]['contribution_kind'] == contributor['contribution_kind'])
        for round_ in self.rows('workflow_rounds', 'instance_id', instance_id):
            submission = self.one('workflow_submissions', round_['submission_id'])
            _check(submission['instance_id'] == instance_id and submission['revision'] == round_['current_revision'])
            for request in self.rows('workflow_review_requests', 'round_id', round_['id']):
                self.archival(request['request_scope_bytes'])
                _check(_reader_sha256(request['request_scope_bytes'], budget=self.budget).hexdigest() == request['request_scope_digest']
                       and request['principal'] not in contributors)
                self.supersession('workflow_review_requests', request, 'supersedes_request_id')
                receipts = self.rows('workflow_review_receipts', 'request_id', request['id'])
                _check((request['status'] == 'pending' and not receipts) or
                       (request['status'] in ('approved', 'changes_requested', 'superseded') and len(receipts) == 1))
                for receipt in receipts:
                    self.archival(receipt['proof_bytes'])
                    self.supersession('workflow_review_receipts', receipt, 'supersedes_receipt_id')
                    _check(receipt['submission_id'] == submission['id']
                           and receipt['submission_digest'] == submission['submission_digest']
                           and receipt['assignment_generation'] == request['assignment_generation']
                           and receipt['request_scope_digest'] == request['request_scope_digest']
                           and (request['status'] == 'superseded' or request['status'] == receipt['outcome'])
                           and _reader_sha256(receipt['proof_bytes'], budget=self.budget).hexdigest() == receipt['proof_digest'])
                    evidence = self.one('workflow_receipt_evidence', receipt['id'], 'receipt_id')
                    result = self.one('workflow_task_results', evidence['result_id'])
                    assignments = self.select('workflow_current_assignments', instance_id=instance_id,
                                              role_key='reviewer:' + request['principal'])
                    _check(len(assignments) == 1)
                    assignment = assignments[0]
                    _check(evidence['signer_principal'] == request['principal'] == result['principal'] == assignment['principal'])
                    _check(evidence['context_id'] == context['id'] and evidence['binding_snapshot_id'] == binding['id']
                           and evidence['round_revision'] == round_['current_revision'])
                    _check(_reader_all_true((evidence[k] == assignment[k] for k in ('task_id', 'session_id', 'result_id')), budget=self.budget)
                           and _reader_all_true((evidence[k] == result[k] for k in ('task_id', 'session_id')), budget=self.budget)
                           and result['generation'] == receipt['assignment_generation'])
                    _check(evidence['proof_bytes'] == receipt['proof_bytes'] == result['result_bytes']
                           and evidence['proof_digest'] == receipt['proof_digest'] == result['result_digest'])
        for event in self.rows('workflow_events', 'instance_id', instance_id):
            revision = _local_event_revision(self, event)
            if 'revision' in event:
                _check(event['revision'] == revision)
        for replay in self.rows('workflow_operation_replays', 'instance_id', instance_id):
            event = self.one('workflow_events', replay['effect_id'])
            _check(event['instance_id'] == instance_id and replay['org_slug'] == self.org
                   and replay['request_digest'] == _reader_sha256(event['event_bytes'], budget=self.budget).hexdigest())

    def archival(self, raw: bytes) -> None:
        _reader_work((_reader_reserve(self.budget, 80) or (raw,)), self.budget, copies=2)
        from runtime.infrastructure.workflow_coding_schema import _metadata_string
        _check(type(raw) is bytes and bool(raw))
        self.reserve(6 * len(raw))
        text = _reader_decode(raw, json.detect_encoding(raw), errors='replace', budget=self.budget)
        index = 0
        prefixes = ('workflow-agent-review-proof@', 'workflow-human-review-proof@',
                    'workflow-review-request@', 'workflow-review-stage@')
        # Read actual member tokens, including duplicate/escaped/truncated
        # metadata; quoted free prose alone is not a modern discriminator.
        while index < len(text):
            self.budget.step()
            if text[index] != '"':
                index += 1
                continue
            key, following, _ = _metadata_string(text, index, self.budget)
            index = following
            while following < len(text) and text[following].isspace():
                following += 1
            if key != 'format' or following >= len(text) or text[following] != ':':
                continue
            following += 1
            while following < len(text) and text[following].isspace():
                following += 1
            if following < len(text) and text[following] == '"':
                value, _, complete = _metadata_string(text, following, self.budget)
                _check(not _reader_any_true((value.startswith(prefix) or not complete and prefix.startswith(value)
                               for prefix in prefixes if value), budget=self.budget))

    def supersession(self, table: str, row: dict, column: str) -> None:
        _reader_work((_reader_reserve(self.budget, 112) or (table,row,column)), self.budget, copies=2)
        seen = _reader_set(budget=self.budget)
        while row[column] is not None:
            self.budget.step()
            _check(row['id'] not in seen)
            _reader_add(seen, row['id'], budget=self.budget)
            prior = self.one(table, row[column])
            _check(prior['assignment_generation'] < row['assignment_generation'])
            if table == 'workflow_review_requests':
                old_round = self.one('workflow_rounds', prior['round_id'])
                new_round = self.one('workflow_rounds', row['round_id'])
                _check(prior['principal'] == row['principal'] and prior['status'] == 'superseded'
                       and old_round['instance_id'] == new_round['instance_id']
                       and old_round['current_revision'] <= new_round['current_revision'])
            else:
                old = self.one('workflow_review_requests', prior['request_id'])
                new = self.one('workflow_review_requests', row['request_id'])
                _check(old['principal'] == new['principal'] and new['supersedes_request_id'] == old['id'])
            row = prior

    def historical(self, instances: set[str]) -> None:
        # Each actual instance has its own directed closure. A shared authority
        # revision is not an instruction to collect all its users.
        _reader_work((_reader_reserve(self.budget, 80) or (instances,)), self.budget, copies=2)
        while True:
            self.close_instances(instances)
            # Selected semantic reconstruction may acquire additional pinned
            # parents or retained siblings. Repeat their directed closure and
            # all selected semantics until no new row was acquired. The count
            # controls traversal only; it certifies no row or global absence.
            # Every repeated operation consumes the same original remainder.
            before = len(self.acquired)
            for instance in _reader_sorted(instances, budget=self.budget):
                self.budget.step()
                _local_validate_draft_data(self, instance, expected_org_slug=self.org)
                if self.layout in ('G', 'H', 'I', 'J'):
                    _local_validate_submission_data(self, instance, expected_org_slug=self.org)
                for intent in self.rows('workflow_draft_dispatch_intents', 'instance_id', instance):
                    if intent['admission_kind'] == 'initial':
                        _local_activation(self, intent['activation_id'], actor=intent['admission_principal'])
                handoffs = self.rows('workflow_author_handoffs', 'instance_id', instance)
                if handoffs:
                    for row in handoffs:
                        proof = _local_handoff_record(self, row, expected_org_slug=self.org)
                        _local_validate_handoff_graph(self, row, proof)
                    for operation in self.rows('workflow_document_revision_operations', 'instance_id', instance):
                        proof = _local_validate_revision_record(self, operation, expected_org_slug=self.org)
                        row = _local_review_row(self, operation)
                        if row is not None:
                            _local_validate_handoff_graph(self, row, proof)
                else:
                    self.legacy_instance(instance)
            if len(self.acquired) == before:
                break
        # No optional historical parse branch may swallow an exhausted shared
        # allowance and later turn the observation into ordinary eligibility.
        _check(self.budget.remaining_bytes >= 0 and self.budget.accepted_rows <= 8192
               and self.budget.traversed <= 16384)



# These imports select pure serializers/models/comparisons. SQL-bearing full
# readers keep complete semantics and are never used as local shortcuts. Their
# explicit budget plumbing supplies accounting, never a candidate certificate.
from runtime.infrastructure.workflow_schema import _DRAFT_EDGES, _DRAFT_MUTABLE, _DRAFT_PROJECTION
from runtime.workflows.activation import (WorkflowActivationError, _id, ActivationRequest,
    DocumentActivationRequest, DocumentActivationRequest3, ArtifactActivationRequest,
    _contract_for_request, parse_request, _authorization, _binding, _context as activation_context,
    _author_role, _task_brief, CONTEXT_LIMIT)
from runtime.workflows.templates import WorkflowTemplateVersion
from runtime.workflows.authority import validate_authority_snapshot, snapshot_team_agents
from runtime.workflows.artifacts import (context_contract, document_from_handoff,
    document_from_submission, require_document_subject, review_family)
from runtime.workflows.draft_dispatch import digest
from runtime.workflows.review_dispatch import _same_value, _validate_projection, _validate_transition, parse_stage
from runtime.workflows.review_receipts import _human_proof, _human_receipt_row, _receipt_rows, parse_human_review
from runtime.workflows.submissions import _hex, review_brief, review_scope
from runtime.workflows.revisions import CODE, _context, _pins, content, parse_revision, revision_actions, revision_brief
from runtime.workflows.review_join import ACTIONS, SERVICE, _replay

# Selected invariants transcribed from runtime/infrastructure/workflow_schema.py:_validate_draft_data; acquisition uses Q.
def _local_validate_draft_data(c: _Candidate, instance_id: str, *, expected_org_slug: str | None) -> None:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
    _reader_work((_reader_reserve(c.budget, 80) or (instance_id,)), c.budget, copies=2)

    def refuse() -> None:
        raise ValueError('workflow_draft_data_corrupt')
    intents = c.ordered(c.select('workflow_draft_dispatch_intents', instance_id=instance_id), 'instance_id', 'attempt_sequence')
    if intents and (not expected_org_slug):
        refuse()
    previous_by_instance: dict[str, dict] = (_reader_reserve(c.budget, 64) or {})
    accepted_results: dict[str, dict] = (_reader_reserve(c.budget, 64) or {})
    for intent in intents:
        for key, value in intent.items():
            if isinstance(value, str) and (not value or value.strip() != value):
                refuse()
        request = c.json(intent['request_bytes'], intent['request_digest'])
        scope = c.json(intent['task_scope_bytes'], intent['task_scope_digest'])
        if not isinstance(request, dict) or not isinstance(scope, dict):
            refuse()
        instance = c.select('workflow_instances', id=intent['instance_id'])
        activation = c.select('workflow_activations', id=intent['activation_id'])
        task = c.select('tasks', id=intent['task_id'])
        context = c.select('workflow_contexts', id=intent['context_id'])
        binding = c.select('workflow_binding_snapshots', id=intent['binding_snapshot_id'])
        if not _reader_all_true((len(rows) == 1 for rows in (_reader_reserve(c.budget, 144) or (instance, activation, task, context, binding))), budget=c.budget):
            refuse()
        instance, activation, task, context, binding = (_reader_reserve(c.budget, 144) or (instance[0], activation[0], task[0], context[0], binding[0]))
        if activation['instance_id'] != intent['instance_id'] or activation['activation_revision'] != intent['activation_revision'] or activation['authority_namespace'] != intent['authority_namespace'] or (activation['authority_generation'] != intent['authority_generation']) or (activation['authority_digest'] != intent['authority_digest']) or (binding['template_version_id'] != activation['template_version_id']) or (context['binding_snapshot_id'] != intent['binding_snapshot_id']) or (task['assigned_agent'] != intent['assigned_principal']) or (scope != (_reader_reserve(c.budget, 448) or {'assigned_agent': task['assigned_agent'], 'team': task['team'], 'brief': task['brief']})) or (intent['effect_key'] != f"workflow-initial-draft:{intent['instance_id']}:{intent['attempt_sequence']}") or (intent['host_execution_key'] != f"workflow-draft-host:{intent['id']}"):
            refuse()
        admission = _reader_dict_pairs(((_reader_reserve(c.budget, 96) or (key,  intent[key] )) for key in ('instance_id', 'activation_id', 'activation_revision', 'attempt_sequence', 'predecessor_intent_id', 'admission_principal', 'operation_key', 'request_digest')), budget=c.budget)
        _reader_put(expected_org_slug, admission, 'org_slug', budget=c.budget)
        if intent['id'] != _reader_sha256(c.canonical(admission), budget=c.budget).hexdigest():
            refuse()
        previous = previous_by_instance.get(intent['instance_id'])
        if previous is None:
            if intent['attempt_sequence'] != 1 or instance['root_task_id'] != intent['task_id'] or instance['binding_snapshot_id'] != intent['binding_snapshot_id'] or (instance['context_id'] != intent['context_id']):
                refuse()
        elif intent['attempt_sequence'] != previous['attempt_sequence'] + 1 or intent['assignment_generation'] != previous['assignment_generation'] + 1 or intent['predecessor_intent_id'] != previous['id'] or (previous['is_current'] != 0) or (previous['state'] not in ('cancelled', 'failed', 'completed')) or (intent['admission_kind'] == 'retry' and previous['state'] != 'failed'):
            refuse()
        _reader_put(intent, previous_by_instance, intent['instance_id'], budget=c.budget)
        events = c.ordered(c.select('workflow_draft_dispatch_events', intent_id=intent['id']), 'event_seq')
        if not events or events[0]['created_at'] != intent['created_at']:
            refuse()
        projection = None
        digest = None
        immutable = _reader_dict_pairs(((_reader_reserve(c.budget, 96) or (key,  c.hex(value) if isinstance(value, bytes) else value )) for key, value in intent.items() if key not in _DRAFT_MUTABLE), budget=c.budget)
        for seq, event in enumerate(events, 1):
            payload = c.json(event['event_bytes'], event['event_digest'])
            if not isinstance(payload, dict) or _reader_set(payload, budget=c.budget) != _reader_set((_reader_reserve(c.budget, 208) or ('format', 'org_slug', 'event', 'previous_digest', 'intent', 'before', 'after', 'terminal_evidence', 'result')), budget=c.budget):
                refuse()
            event_identity = _reader_dict_pairs(((_reader_reserve(c.budget, 96) or (key,  event[key] )) for key in ('id', 'event_seq', 'event_kind', 'created_at')), budget=c.budget)
            before, after = (_reader_reserve(c.budget, 96) or (payload['before'], payload['after']))
            if payload['format'] != 'workflow-draft-event@1' or payload['org_slug'] != expected_org_slug or event['event_seq'] != seq or (event['id'] != f"{intent['id']}:{seq}") or (payload['event'] != event_identity) or (payload['previous_digest'] != digest) or (payload['intent'] != immutable) or (before != projection) or (not isinstance(after, dict)) or (_reader_set(after, budget=c.budget) != _reader_set(_DRAFT_PROJECTION, budget=c.budget)) or (event['state_before'] != (None if before is None else before['state'])) or (event['state_after'] != after['state']):
                refuse()
            kind = event['event_kind']
            try:
                _reader_reserve(c.budget, 112)
                timestamp = datetime.fromisoformat(event['created_at'])
                if timestamp.tzinfo is None or timestamp.utcoffset().total_seconds() != 0:
                    refuse()
            except ((ValueError, TypeError, AttributeError)):
                refuse()
            for key in ('is_current', 'cancellation_requested', 'host_launch_started'):
                if type(after[key]) is not int or after[key] not in (0, 1):
                    refuse()
            if after['state'] not in ('queued', 'claimed', 'running', 'uncertain', 'cancelled', 'failed', 'completed') or (after['is_current'] == 0 and after['state'] not in ('cancelled', 'failed', 'completed')) or (after['state'] in ('claimed', 'running', 'uncertain') and (not (after['claim_token'] and after['claim_owner']))) or (after['state'] == 'queued' and (after['host_launch_started'] or after['host_execution_id'] or after['session_id'])) or (after['state'] in ('running', 'completed') and (not (after['host_launch_started'] and after['host_execution_id'] and after['session_id']))) or (after['final_result_id'] is not None and (type(after['final_result_id']) is not int or not after['session_id'])):
                refuse()
            edge = (_reader_reserve(c.budget, 96) or (event['state_before'], event['state_after']))
            if kind in ('cancel_requested', 'callback_recorded', 'callback_rejected', 'retired'):
                if before is None or edge[0] != edge[1]:
                    refuse()
            elif edge not in _DRAFT_EDGES.get(kind, _reader_set(budget=c.budget)):
                refuse()
            if seq == 1 and after != (_reader_reserve(c.budget, 1216) or dict(state='queued', is_current=1, cancellation_requested=0, claim_token=None, claim_owner=None, host_launch_started=0, host_execution_id=None, session_id=None, final_result_id=None)):
                refuse()
            if before is not None:
                if before['host_launch_started'] and (not after['host_launch_started']) or (before['cancellation_requested'] and (not after['cancellation_requested'])) or (before['session_id'] is not None and before['session_id'] != after['session_id']) or (before['final_result_id'] is not None and before['final_result_id'] != after['final_result_id']) or (before['host_execution_id'] is not None and before['host_execution_id'] != after['host_execution_id']):
                    refuse()
            if before is not None and before['is_current'] != after['is_current'] and (kind != 'retired'):
                refuse()
            if before is not None and before['final_result_id'] != after['final_result_id'] and (kind != 'callback_recorded'):
                refuse()
            if kind in ('cancel_requested', 'retired', 'callback_rejected', 'callback_recorded'):
                permitted = (_reader_reserve(c.budget, 576) or {'cancel_requested': _reader_set((_reader_reserve(c.budget, 80) or ('cancellation_requested',)), budget=c.budget), 'retired': _reader_set((_reader_reserve(c.budget, 80) or ('is_current',)), budget=c.budget), 'callback_rejected': _reader_set(budget=c.budget), 'callback_recorded': _reader_set((_reader_reserve(c.budget, 96) or ('final_result_id', 'session_id')), budget=c.budget)})[kind]
                if _reader_any_true((before[key] != after[key] for key in _DRAFT_PROJECTION if key not in permitted), budget=c.budget):
                    refuse()
            if kind == 'retired' and (edge[0] not in ('cancelled', 'failed', 'completed') or after['is_current'] != 0):
                refuse()
            if kind == 'requeued' and (before['host_launch_started'] or before['session_id'] is not None):
                refuse()
            if kind == 'running' and (not before['host_launch_started']):
                refuse()
            if kind == 'launch_reserved' and (before['host_launch_started'] or not after['host_launch_started']):
                refuse()
            if kind == 'cancel_requested' and after['cancellation_requested'] != 1:
                refuse()
            if edge[1] in ('cancelled', 'failed', 'completed') and edge[0] != edge[1]:
                if after['host_launch_started']:
                    witness = payload['terminal_evidence']
                    if not isinstance(witness, dict) or _reader_set(witness, budget=c.budget) != _reader_set((_reader_reserve(c.budget, 80) or ('host_quiescent',)), budget=c.budget) or witness['host_quiescent'] is not True:
                        refuse()
                if edge[1] in ('failed', 'cancelled') and task['status'] != edge[1]:
                    refuse()
            if (kind in ('callback_recorded', 'callback_rejected')) != (event['result_id'] is not None):
                refuse()
            if kind == 'callback_rejected' and event['callback_accepted'] != 0:
                refuse()
            if event['result_id'] is not None:
                results = c.select('task_results', id=event['result_id'])
                if len(results) != 1:
                    refuse()
                result = results[0]
                result_closure = (_reader_reserve(c.budget, 704) or {'record': result, 'id': event['result_id'], 'digest': event['result_digest'], 'disposition': event['disposition'], 'accepted': event['callback_accepted']})
                if c.canonical(payload['result']) != c.canonical(result_closure) or _reader_sha256(c.canonical(result), budget=c.budget).hexdigest() != event['result_digest'] or result['task_id'] != intent['task_id'] or (result['agent'] != intent['assigned_principal']) or (result['session_id'] != event['session_id']) or (event['session_id'] != after['session_id']) or (_reader_list(c.select('workflow_dispatch_callbacks', result_id=(_reader_reserve(c.budget, 128) or str(event['result_id']))), budget=c.budget) + (_reader_reserve(c.budget, 192) or [None]))[0]:
                    refuse()
                if kind == 'callback_recorded':
                    if event['callback_accepted'] != 1 or after['final_result_id'] != result['id'] or intent['id'] in accepted_results or after['cancellation_requested']:
                        refuse()
                    _reader_put(result_closure, accepted_results, intent['id'], budget=c.budget)
            elif kind not in ('completed', 'host_reconciled', 'retired') and payload['result'] is not None:
                refuse()
            if after['state'] == 'completed':
                result = accepted_results.get(intent['id'])
                if after['cancellation_requested'] or result is None or result['record']['status'] != 'completed' or (result['id'] != after['final_result_id']) or (payload['result'] != result) or (task['status'] != 'completed'):
                    refuse()
            projection, digest = (_reader_reserve(c.budget, 96) or (after, event['event_digest']))
        if projection != _reader_dict_pairs(((_reader_reserve(c.budget, 96) or (key,  intent[key] )) for key in _DRAFT_PROJECTION), budget=c.budget) or events[-1]['created_at'] != intent['updated_at']:
            refuse()
    if _reader_any_true((not intent['is_current'] for intent in previous_by_instance.values()), budget=c.budget):
        refuse()
    if (_reader_list(c.orphan_draft_events(instance_id), budget=c.budget) + (_reader_reserve(c.budget, 192) or [None]))[0]:
        refuse()

# Selected invariants transcribed from runtime/infrastructure/workflow_schema.py:_event_revision; acquisition uses Q.
def _local_event_revision(c: _Candidate, event: dict) -> int:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
    _reader_work((_reader_reserve(c.budget, 80) or (event,)), c.budget, copies=2)
    raw = event['event_bytes']
    kind = event['event_kind']
    code = f"workflow_legacy_event_unmappable: event={event['id']} instance={event['instance_id']}; source owner must restore authentic submission/round/event/replay archival closure"
    if not isinstance(raw, bytes) or not raw:
        raise ValueError(code)
    ordinary = _reader_sha256(raw, budget=c.budget).hexdigest()
    historical_join = kind == 'joined' and _reader_sha256(b'joined' + raw, budget=c.budget).hexdigest() == event['event_digest']
    if event['event_digest'] != ordinary and (not historical_join):
        raise ValueError(code)
    replays = c.select('workflow_operation_replays', effect_id=event['id'])
    if _reader_any_true((r['instance_id'] != event['instance_id'] or r['request_digest'] != ordinary for r in replays), budget=c.budget):
        raise ValueError(code)
    subject = None
    try:
        _reader_reserve(c.budget, 96)
        parsed = c.json(raw)
        if isinstance(parsed, dict) and c.canonical(parsed) == raw:
            subject = parsed
    except _ReaderBudgetExhausted:
        raise
    except ((ValueError, UnicodeError)):
        pass
    if subject is not None and 'event_kind' in subject and (subject['event_kind'] != kind):
        raise ValueError(code)
    candidates = (_reader_reserve(c.budget, 64) or [])
    for round_ in c.select('workflow_rounds', instance_id=event['instance_id']):
        submissions = c.select('workflow_submissions', id=round_['submission_id'])
        if len(submissions) != 1:
            continue
        submission = submissions[0]
        revision = submission['revision']
        if submission['instance_id'] != event['instance_id'] or type(revision) is not int or revision <= 0 or (round_['current_revision'] != revision) or (not isinstance(submission['submission_bytes'], bytes)) or (_reader_sha256(submission['submission_bytes'], budget=c.budget).hexdigest() != submission['submission_digest']):
            continue
        if subject is not None:
            identities = (_reader_reserve(c.budget, 704) or {'instance_id': event['instance_id'], 'round_id': round_['id'], 'submission_id': submission['id'], 'revision': revision, 'submission_digest': submission['submission_digest']})
            if not _reader_any_true((k in subject for k in ('round_id', 'submission_id')), budget=c.budget) or _reader_any_true((subject[k] != v or (k == 'revision' and type(subject[k]) is not int) for k, v in identities.items() if k in subject), budget=c.budget):
                continue
        elif not historical_join:
            continue
        receipts = c.round_receipts(round_['id'])
        if _reader_any_true((r['submission_id'] != submission['id'] or r['submission_digest'] != submission['submission_digest'] for r in receipts), budget=c.budget):
            continue
        receipt_valid = True
        for receipt in receipts:
            requests = c.select('workflow_review_requests', id=receipt['request_id'])
            if len(requests) != 1:
                receipt_valid = False
                break
            request = requests[0]
            if not isinstance(receipt['proof_bytes'], bytes) or _reader_sha256(receipt['proof_bytes'], budget=c.budget).hexdigest() != receipt['proof_digest'] or (not isinstance(request['request_scope_bytes'], bytes)) or (_reader_sha256(request['request_scope_bytes'], budget=c.budget).hexdigest() != request['request_scope_digest']) or (request['request_scope_digest'] != receipt['request_scope_digest']) or (request['assignment_generation'] != receipt['assignment_generation']) or (historical_join and (receipt['outcome'] != 'approved' or request['status'] != 'approved')):
                receipt_valid = False
                break
        if not receipt_valid:
            continue
        if historical_join and (not replays or len(receipts) != 3):
            continue
        _reader_append(candidates, (_reader_reserve(c.budget, 112) or (round_['id'], submission['id'], revision)), budget=c.budget)
    if len(candidates) != 1:
        raise ValueError(code + f'; candidates={candidates}')
    return candidates[0][2]

# Selected invariants transcribed from runtime/infrastructure/workflow_schema.py:_validate_submission_data; acquisition uses Q.
def _local_validate_submission_data(c: _Candidate, instance_id: str, *, expected_org_slug: str | None) -> None:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
    _reader_work((_reader_reserve(c.budget, 80) or (instance_id,)), c.budget, copies=2)
    code = 'workflow_submission_data_corrupt'
    operations = _reader_dict_pairs(((_reader_reserve(c.budget, 96) or (r['submission_id'],  r )) for r in c.select('workflow_submission_operations', instance_id=instance_id)), budget=c.budget)
    for submission in c.select('workflow_submissions', instance_id=instance_id):
        if type(submission['revision']) is not int or submission['revision'] <= 0 or (not isinstance(submission['submission_bytes'], bytes)) or (_reader_sha256(submission['submission_bytes'], budget=c.budget).hexdigest() != submission['submission_digest']):
            raise ValueError(code)
        op = operations.get(submission['id'])
        if op is None:
            if submission['source_result_id'] is None:
                raise ValueError(code)
            continue
        if submission['source_result_id'] is not None or not expected_org_slug or op['org_slug'] != expected_org_slug or (op['instance_id'] != submission['instance_id']) or (op['principal'] != submission['author_principal']) or (op['source_task_id'] != submission['source_task_id']) or (op['source_session_id'] != submission['source_session_id']) or _reader_any_true((not isinstance(op[k], str) or not op[k] or op[k].strip() != op[k] for k in ('operation_key', 'principal', 'source_task_id', 'source_session_id', 'created_at')), budget=c.budget):
            raise ValueError(code)
        proof = c.json(op['provenance_bytes'], op['provenance_digest'])
        intents = c.select('workflow_draft_dispatch_intents', instance_id=op['instance_id'], task_id=op['source_task_id'], session_id=op['source_session_id'])
        if len(intents) != 1 or not isinstance(proof, dict) or _reader_set(proof, budget=c.budget) != _reader_set((_reader_reserve(c.budget, 144) or ('format', 'operation', 'submission', 'source_intent', 'request_bytes')), budget=c.budget) or (proof['format'] != 'workflow-submission-operation@1') or (c.canonical(proof['operation']) != c.canonical(_reader_dict_pairs(((_reader_reserve(c.budget, 96) or (k,  v )) for k, v in op.items() if k not in ('provenance_bytes', 'provenance_digest')), budget=c.budget))) or (c.canonical(proof['submission']) != c.canonical(_reader_dict_pairs(((_reader_reserve(c.budget, 96) or (k,  c.hex(v) if isinstance(v, bytes) else v )) for k, v in submission.items()), budget=c.budget))) or (c.canonical(proof['source_intent']) != c.canonical(_reader_dict_pairs(((_reader_reserve(c.budget, 96) or (k,  c.hex(v) if isinstance(v, bytes) else v )) for k, v in intents[0].items() if k not in _DRAFT_MUTABLE), budget=c.budget))) or (intents[0]['assigned_principal'] != op['principal']):
            raise ValueError(code)
        try:
            _reader_reserve(c.budget, 96)
            request = c.unhex(proof['request_bytes'])
        except ((TypeError, ValueError)):
            raise ValueError(code) from None
        payload = c.json(request, op['request_digest'])
        if not isinstance(payload, dict) or payload.get('submission_id') != submission['id'] or payload.get('submission_digest') != submission['submission_digest'] or (payload.get('instance_id') != submission['instance_id']) or (type(payload.get('revision')) is not int) or (payload['revision'] != submission['revision']):
            raise ValueError(code)
    for round_ in c.subject_rounds(instance_id):
        if round_['instance_id'] != round_['subject_instance'] or round_['current_revision'] != round_['subject_revision']:
            raise ValueError(code)
    for event in c.select('workflow_events', instance_id=instance_id):
        if type(event['revision']) is not int or event['revision'] != _local_event_revision(c, event):
            raise ValueError(code)
    for link in c.instance_links(instance_id):
        op = operations.get(link['submission_id'])
        if op is None or type(link['task_result_id']) is not int:
            raise ValueError(code)
        results = c.select('task_results', id=link['task_result_id'])
        intents = c.select('workflow_draft_dispatch_intents', task_id=link['source_task_id'], session_id=link['source_session_id'])
        if len(results) != 1 or len(intents) != 1:
            raise ValueError(code)
        result, intent = (_reader_reserve(c.budget, 96) or (results[0], intents[0]))
        payload = c.json(link['result_bytes'], link['result_digest'])
        callbacks = c.select('workflow_draft_dispatch_events', intent_id=intent['id'], event_kind='callback_recorded', result_id=result['id'])
        if c.canonical(payload) != c.canonical(result) or result['task_id'] != link['source_task_id'] or result['session_id'] != link['source_session_id'] or (result['agent'] != op['principal']) or (link['source_task_id'] != op['source_task_id']) or (link['source_session_id'] != op['source_session_id']) or (intent['final_result_id'] != result['id']) or (len(callbacks) != 1) or (callbacks[0]['result_digest'] != link['result_digest']):
            raise ValueError(code)

# Selected invariants transcribed from runtime/infrastructure/workflow_schema.py:_handoff_record; acquisition uses Q.
def _local_handoff_record(c: _Candidate, row: dict, *, expected_org_slug: str | None) -> dict:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
    _reader_work((_reader_reserve(c.budget, 80) or (row,)), c.budget, copies=2)
    code = 'workflow_author_handoff_storage_corrupt'

    def check(condition: bool) -> None:
        if not condition:
            raise ValueError(code)

    def same(value: object, expected: object) -> bool:
        return c.canonical(value) == c.canonical(expected)

    def closed(value: object, keys: str) -> dict:
        check(isinstance(value, dict) and _reader_set(value, budget=c.budget) == _reader_set(keys.split(), budget=c.budget))
        return value

    def one(table: str, identity: str) -> dict:
        rows = c.rows(table, 'id', identity)
        check(len(rows) == 1)
        return rows[0]

    def hex_bytes(value: object, limit: int) -> bytes:
        check(isinstance(value, str) and re.fullmatch('(?:[0-9a-f]{2})+', value) is not None)
        raw = c.unhex(value)
        check(1 <= len(raw) <= limit)
        return raw
    try:
        _reader_reserve(c.budget, 160)
        check(row['org_slug'] == expected_org_slug and re.fullmatch('[a-z0-9-]{1,40}', row['org_slug']) is not None)
        for key in ('id', 'intent_id', 'instance_id', 'principal', 'source_task_id', 'source_session_id', 'operation_key', 'submission_id'):
            check(isinstance(row[key], str) and re.fullmatch('[A-Za-z0-9._:-]{1,128}', row[key]) is not None)
        check(type(row['revision']) is int and row['revision'] == 1)
        for key, limit in (('candidate_bytes', 1048576), ('request_bytes', 2097152), ('admission_bytes', 16777216)):
            raw = row[key]
            check(isinstance(raw, bytes) and 1 <= len(raw) <= limit)
            digest_value = row[key.replace('_bytes', '_digest')]
            check(isinstance(digest_value, str) and re.fullmatch('[0-9a-f]{64}', digest_value) is not None and (_reader_sha256(raw, budget=c.budget).hexdigest() == digest_value))
        _reader_decode(row['candidate_bytes'], 'utf8', budget=c.budget)
        check(re.fullmatch('\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}\\.\\d{6}Z', row['created_at']) is not None)
        datetime.strptime(row['created_at'], '%Y-%m-%dT%H:%M:%S.%fZ')
        proof = c.handoff(row['admission_bytes'], row['admission_digest'])
        family = (_reader_reserve(c.budget, 320) or {'workflow-author-handoff@1': 1, 'workflow-author-handoff@2': 2}).get(proof.get('format'))
        check(family is not None)
        closed(proof, 'format handoff client_request source_intent source_generation pins admission_authority' + (' repository_resolution' if family == 2 else ''))
        if family == 2:
            check(proof['repository_resolution'] is None)
        template_family = 4 if family == 2 else 3
        expected_row = _reader_dict_pairs(((_reader_reserve(c.budget, 96) or (k,  c.hex(v) if isinstance(v, bytes) else v )) for k, v in row.items() if k not in ('admission_bytes', 'admission_digest')), budget=c.budget)
        check(same(proof['handoff'], expected_row))
        command = closed(proof['client_request'], 'format operation_key activation_id expected_revision submission_bytes_base64 submission_digest' + (' artifact_kind' if family == 2 else ''))
        if family == 2:
            check(command['artifact_kind'] == 'document')
        check(command['format'] == f'workflow-author-handoff-command@{family}' and type(command['expected_revision']) is int and (command['expected_revision'] == 1) and (command['operation_key'] == row['operation_key']))
        candidate = c.unbase64(command['submission_bytes_base64'])
        check(candidate == row['candidate_bytes'] and command['submission_digest'] == row['candidate_digest'] and (_reader_decode(_reader_b64encode(candidate, budget=c.budget), 'ascii', budget=c.budget) == command['submission_bytes_base64']))
        request = {**command, 'format': f'workflow-author-handoff-request@{family}', 'handoff_id': row['id'], 'submission_id': row['submission_id'], 'instance_id': row['instance_id'], 'revision': 1}
        check(c.canonical(request) == row['request_bytes'])
        check(row['id'] == 'workflow-author-handoff:' + _reader_sha256(c.canonical((_reader_reserve(c.budget, 448) or [row['org_slug'], row['principal'], row['operation_key']])), budget=c.budget).hexdigest())
        check(row['submission_id'] == 'workflow-submission:' + _reader_sha256(c.canonical((_reader_reserve(c.budget, 832) or [row['instance_id'], 1, row['org_slug'], row['principal'], row['operation_key'], f'workflow-author-handoff@{family}'])), budget=c.budget).hexdigest())
        intent = one('workflow_draft_dispatch_intents', row['intent_id'])
        check(same(proof['source_intent'], _reader_dict_pairs(((_reader_reserve(c.budget, 96) or (k,  c.hex(v) if isinstance(v, bytes) else v )) for k, v in intent.items() if k not in _DRAFT_MUTABLE), budget=c.budget)))
        check(len(proof['source_intent']) == 25 and intent['admission_kind'] == 'initial' and (intent['predecessor_intent_id'] is None) and (intent['activation_revision'] == 1) and (intent['attempt_sequence'] == 1) and (intent['assignment_generation'] == 1) and (intent['recovery_owner'] == 'workflow_recovery') and (intent['task_id'] == row['source_task_id']) and (intent['instance_id'] == row['instance_id']) and (intent['assigned_principal'] == row['principal']) and (command['activation_id'] == intent['activation_id']))
        for key in ('request_bytes', 'task_scope_bytes'):
            check(1 <= len(intent[key]) <= 1048576)
        generation = closed(proof['source_generation'], 'org_slug task_id principal session_id activation_revision assignment_generation attempt_sequence claim_token claim_owner host_execution_key host_execution_id state is_current host_launch_started cancellation_requested final_result_id')
        check(generation['org_slug'] == row['org_slug'] and generation['task_id'] == row['source_task_id'] and (generation['principal'] == row['principal']) and (generation['session_id'] == row['source_session_id']))
        for key in ('activation_revision', 'assignment_generation', 'attempt_sequence', 'is_current', 'host_launch_started'):
            check(type(generation[key]) is int and generation[key] == 1)
        check(type(generation['cancellation_requested']) is int and generation['cancellation_requested'] == 0 and (generation['final_result_id'] is None) and (generation['state'] in ('claimed', 'running')) and (generation['claim_owner'] == 'workflow_dispatcher') and (generation['host_execution_key'] == intent['host_execution_key']))
        for key in ('claim_token', 'claim_owner', 'host_execution_key'):
            check(isinstance(generation[key], str) and re.fullmatch('[A-Za-z0-9._:-]{1,128}', generation[key]) is not None)
        check(generation['host_execution_id'] is None or (isinstance(generation['host_execution_id'], str) and re.fullmatch('[A-Za-z0-9._:-]{1,128}', generation['host_execution_id']) is not None))
        events = c.ordered(c.select('workflow_draft_dispatch_events', intent_id=intent['id']), 'event_seq')
        witnessed = False
        for event in events:
            payload = c.json(event['event_bytes'], event['event_digest'])
            if payload['after'] == _reader_dict_pairs(((_reader_reserve(c.budget, 96) or (k,  generation[k] )) for k in _DRAFT_PROJECTION if k != 'session_id'), budget=c.budget) | (_reader_reserve(c.budget, 192) or {'session_id': generation['session_id']}):
                check(datetime.fromisoformat(event['created_at']) <= datetime.strptime(row['created_at'], '%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=timezone.utc))
                witnessed = True
        check(witnessed)
        pins = closed(proof['pins'], 'template activation authorization binding context scope contract')
        activation = one('workflow_activations', intent['activation_id'])
        instance = one('workflow_instances', row['instance_id'])
        check(same(pins['activation'], (_reader_reserve(c.budget, 448) or dict(id=intent['activation_id'], revision=1, request_digest=activation['request_digest']))))
        from runtime.workflows.templates import WorkflowTemplateStore, _review_contract
        mapping = c.select('workflow_template_identity_versions', template_version_id=activation['template_version_id'])
        check(len(mapping) == 1)
        template = c.template(identity_id=activation['template_identity_id'], version=mapping[0]['version'], expected_version_id=activation['template_version_id'])
        check(same(pins['template'], (_reader_reserve(c.budget, 960) or dict(identity_id=template.identity_id, version_id=template.version_id, version=template.version, definition_digest=template.definition_digest, compiler_pin=f'workflow-compiler@{template_family}', validator_pin=f'workflow-validator@{template_family}', source_pin=f'operator-input@{template_family}'))) and (_reader_reserve(c.budget, 112) or (template.compiler_pin, template.validator_pin, template.source_pin)) == (_reader_reserve(c.budget, 112) or (f'workflow-compiler@{template_family}', f'workflow-validator@{template_family}', f'operator-input@{template_family}')))
        contract = _review_contract(template, budget=c.budget)
        context = one('workflow_contexts', intent['context_id'])
        binding = one('workflow_binding_snapshots', intent['binding_snapshot_id'])
        authorization = one('workflow_authorization_revisions', binding['authorization_revision_id'])
        for kind, identity, raw, dg in (_reader_reserve(c.budget, 144) or ((_reader_reserve(c.budget, 128) or ('authorization', authorization['id'], authorization['authority_bytes'], authorization['authority_digest'])), (_reader_reserve(c.budget, 128) or ('binding', binding['id'], binding['binding_bytes'], binding['binding_digest'])), (_reader_reserve(c.budget, 128) or ('context', context['id'], context['context_bytes'], context['context_digest'])), (_reader_reserve(c.budget, 128) or ('scope', intent['task_id'], intent['task_scope_bytes'], intent['task_scope_digest'])), (_reader_reserve(c.budget, 128) or ('contract', template.version_id, c.canonical(contract), _reader_sha256(c.canonical(contract), budget=c.budget).hexdigest())))):
            pin = closed(pins[kind], 'id bytes_hex digest')
            check(same(pin, (_reader_reserve(c.budget, 448) or dict(id=identity, bytes_hex=c.hex(raw), digest=dg))) and len(identity) <= 256 and (1 <= len(raw) <= 1048576))
            c.handoff(hex_bytes(pin['bytes_hex'], 1048576), pin['digest'])
        grant = c.handoff(authorization['authority_bytes'], authorization['authority_digest'])
        bound = c.handoff(binding['binding_bytes'], binding['binding_digest'])
        ctx = c.handoff(context['context_bytes'], context['context_digest'])
        check(grant['format'] == f'workflow-authorization@{template_family}' and bound['format'] == f'workflow-binding@{template_family}' and (ctx['format'] == f'workflow-initial-draft-context@{template_family}') and (contract['format'] == ('workflow-artifact-contract@4' if family == 2 else 'workflow-document-contract@3')) and same(ctx['artifact_contract' if family == 2 else 'document_contract'], contract) and same(ctx['authorization'], grant) and same(ctx['binding'], bound) and (ctx['binding_snapshot_id'] == binding['id']) and (ctx['authorization_revision_id'] == authorization['id']) and (ctx['intent_id'] == intent['id']) and (ctx['org_slug'] == expected_org_slug) and (instance['root_task_id'] == intent['task_id']) and (instance['context_id'] == context['id']) and (instance['binding_snapshot_id'] == binding['id']) and (instance['owner_principal'] == intent['admission_principal']))
        if family == 2:
            closed(grant, 'format namespace instance_id activation_id activation_revision root_task_id original_request request_digest actor created_at template authority repository_binding')
            closed(bound, 'format instance_id activation_id activation_revision template_version_id authorization_revision_id bindings eligible_replacements scope authority repository_binding')
            closed(ctx, 'format org_slug request authority_snapshot template inputs authorization authorization_revision_id binding binding_snapshot_id intent_id attempt_sequence assignment_generation artifact_contract repository_binding')
            from runtime.workflows.activation import parse_request, _authorization, _binding, _context
            frozen = _reader_dump(parse_request(ctx['request'], budget=c.budget), by_alias=True, budget=c.budget)
            check(same(frozen, ctx['request']) and same(frozen, grant['original_request']))
            expected_grant = _authorization(org_slug=row['org_slug'], instance_id=row['instance_id'], activation_id=intent['activation_id'], task_id=intent['task_id'], request=frozen, request_digest=intent['request_digest'], template=template, timestamp=activation['created_at'], budget=c.budget)
            expected_bound = _binding(instance_id=row['instance_id'], activation_id=intent['activation_id'], authorization_id=authorization['id'], template=template, request=frozen, budget=c.budget)
            expected_context = _context(org_slug=row['org_slug'], request=frozen, snapshot=ctx['authority_snapshot'], template=template, inputs=ctx['inputs'], authorization=expected_grant, authorization_id=authorization['id'], binding=expected_bound, binding_id=binding['id'], intent_id=intent['id'], budget=c.budget)
            check(same(grant, expected_grant) and same(bound, expected_bound) and same(ctx, expected_context))
            check(contract['output']['accepted_kinds'] == (_reader_reserve(c.budget, 192) or ['document']) and grant['repository_binding'] is None and (bound['repository_binding'] is None) and (ctx['repository_binding'] is None) and (grant['original_request']['repository_access'] is None))
        handoff_actions = (_reader_reserve(c.budget, 320) or ['stage-immutable-artifact', 'submit-immutable-artifact']) if family == 2 else (_reader_reserve(c.budget, 320) or ['stage-immutable-document', 'submit-immutable-document'])
        captured = closed(proof['admission_authority'], 'ready pointer profiles mirror cutover actions')
        ready = closed(captured['ready'], 'namespace generation snapshot_bytes_hex snapshot_digest')
        snapshot = c.handoff(hex_bytes(ready['snapshot_bytes_hex'], 1048576), ready['snapshot_digest'])
        journal = c.publication(ready['namespace'], ready['generation'], ready['snapshot_digest'])
        check(len(journal) == 1 and c.hex(journal[0]['snapshot_bytes']) == ready['snapshot_bytes_hex'] and (journal[0]['state'] == 'cache_installed') and same(snapshot, ctx['authority_snapshot']) and (ready['namespace'] == f'org/{expected_org_slug}') and (type(ready['generation']) is int) and (ready['generation'] == intent['authority_generation']) and (ready['snapshot_digest'] == intent['authority_digest']))
        check(same(captured['pointer'], (_reader_reserve(c.budget, 704) or dict(current_generation=ready['generation'], journal_id=journal[0]['id'], snapshot_digest=ready['snapshot_digest'], state='ready', profile_fence=journal[0]['profile_fence']))))
        check(captured['cutover'] == 'enabled' and same(captured['actions'], handoff_actions) and (_reader_set(captured['actions'], budget=c.budget) <= _reader_set(grant['original_request']['allowed_actions'], budget=c.budget)))

        def namespace(value: object) -> None:
            check(type(value) is str and bool(value.strip()) and ('\x00' not in value) and (1 <= 4 * len(value) <= 256))

        def profile_name(value: object) -> None:
            check(type(value) is str and bool(value))

        def i0(value: object) -> None:
            check(type(value) is int and 0 <= value <= 9223372036854775807)

        def digest_value(value: object) -> None:
            check(type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None)
        snapshot_profiles = snapshot['machine_global_profiles']
        check(isinstance(snapshot_profiles, list) and len(snapshot_profiles) <= 1024)
        names: list[str] = (_reader_reserve(c.budget, 64) or [])
        for profile in snapshot_profiles:
            closed(profile, 'consumers generation profile_digest profile_name')
            profile_name(profile['profile_name'])
            i0(profile['generation'])
            digest_value(profile['profile_digest'])
            check(isinstance(profile['consumers'], list))
            for consumer in profile['consumers']:
                namespace(consumer)
            check(profile['consumers'] == _reader_sorted(_reader_set(profile['consumers'], budget=c.budget), budget=c.budget))
            _reader_append(names, profile['profile_name'], budget=c.budget)
        check(names == _reader_sorted(_reader_set(names, budget=c.budget), budget=c.budget))
        check(isinstance(captured['profiles'], list) and len(captured['profiles']) <= 1024)
        for profile in captured['profiles']:
            closed(profile, 'name digest')
            profile_name(profile['name'])
            digest_value(profile['digest'])
        check(same(captured['profiles'], _reader_list(((_reader_reserve(c.budget, 320) or dict(name=p['profile_name'], digest=p['profile_digest'])) for p in snapshot_profiles), budget=c.budget)))
        check(isinstance(captured['mirror'], list) and len(captured['mirror']) <= 16384)
        grouped: list[tuple] = (_reader_reserve(c.budget, 64) or [])
        dependencies: list[dict] = (_reader_reserve(c.budget, 64) or [])
        stores: dict[str, dict] = (_reader_reserve(c.budget, 64) or {})
        registry: dict[str, dict] = (_reader_reserve(c.budget, 64) or {})
        for item in captured['mirror']:
            kind = item.get('kind') if isinstance(item, dict) else None
            fields = (_reader_reserve(c.budget, 448) or {'dependency': 'kind org_namespace profile_name consumer_identity bound_generation state', 'store': 'kind profile_name generation profile_digest state', 'registry': 'kind profile_name published_generation'})
            check(type(kind) is str and kind in fields)
            closed(item, fields[kind])
            profile_name(item['profile_name'])
            if kind == 'dependency':
                namespace(item['org_namespace'])
                namespace(item['consumer_identity'])
                i0(item['bound_generation'])
                check(type(item['state']) is str and item['state'] in ('active', 'unbound', 'removed'))
                _reader_append(grouped, (_reader_reserve(c.budget, 128) or (0, item['org_namespace'], item['profile_name'], item['consumer_identity'])), budget=c.budget)
                _reader_append(dependencies, item, budget=c.budget)
            elif kind == 'store':
                i0(item['generation'])
                digest_value(item['profile_digest'])
                check(type(item['state']) is str and item['state'] in ('active', 'removed'))
                _reader_append(grouped, (_reader_reserve(c.budget, 96) or (1, item['profile_name'])), budget=c.budget)
                _reader_put(item, stores, item['profile_name'], budget=c.budget)
            else:
                i0(item['published_generation'])
                _reader_append(grouped, (_reader_reserve(c.budget, 96) or (2, item['profile_name'])), budget=c.budget)
                _reader_put(item, registry, item['profile_name'], budget=c.budget)
        check(grouped == _reader_sorted(_reader_set(grouped, budget=c.budget), budget=c.budget))
        required: dict[str, dict] = (_reader_reserve(c.budget, 64) or {})
        for dependency in dependencies:
            if dependency['org_namespace'] != ready['namespace'] or dependency['state'] == 'removed':
                continue
            name = dependency['profile_name']
            store = stores.get(name)
            published = registry.get(name)
            check(dependency['state'] == 'active' and store is not None and (published is not None))
            bound_generation = dependency['bound_generation']
            check(store['state'] == 'active' and store['generation'] == bound_generation and (published['published_generation'] == bound_generation))
            profile = _reader_setdefault(required, name, _reader_reserve(c.budget, 576) or dict(consumers=(_reader_reserve(c.budget, 64) or []), generation=bound_generation, profile_digest=store['profile_digest'], profile_name=name), budget=c.budget)
            check(profile['generation'] == bound_generation and profile['profile_digest'] == store['profile_digest'])
            _reader_append(profile['consumers'], dependency['consumer_identity'], budget=c.budget)
        check(same(_reader_list((required[name] for name in _reader_sorted(required, budget=c.budget)), budget=c.budget), snapshot_profiles))
        return proof
    except ((ValueError, KeyError, TypeError, AttributeError, UnicodeError, sqlite3.DatabaseError)) as exc:
        raise ValueError(code) from exc

# Selected invariants transcribed from runtime/workflows/submissions.py:_reviewed_source_result; acquisition uses Q.
def _local_reviewed_source_result(c: _Candidate, row: dict, intent: dict) -> dict:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
    _reader_work((_reader_reserve(c.budget, 96) or (row,intent)), c.budget, copies=2)
    from runtime.workflows.review_dispatch import _same_value
    code = 'workflow_author_handoff_storage_corrupt'
    links = c.select('workflow_submission_result_links', submission_id=row['submission_id'])
    if len(links) != 1 or type(links[0]['task_result_id']) is not int or links[0]['task_result_id'] <= 0:
        raise ValueError(code)
    link = links[0]
    result = c.one('task_results', link['task_result_id'])
    callbacks = c.select('workflow_draft_dispatch_events', intent_id=intent['id'], event_kind='callback_recorded')
    terminals = _reader_list((r for r in c.select('workflow_draft_dispatch_events', intent_id=intent['id']) if r['event_kind'] in ('completed', 'host_reconciled')), budget=c.budget)
    if len(callbacks) != 1 or result['status'] != 'completed' or result['task_id'] != row['source_task_id'] or (result['agent'] != row['principal']) or (result['session_id'] != row['source_session_id']) or (intent['final_result_id'] != result['id']) or (link['source_task_id'] != result['task_id']) or (link['source_session_id'] != result['session_id']) or (not _same_value(c.json(link['result_bytes'], link['result_digest']), result, budget=c.budget)):
        raise ValueError(code)
    callback = c.json(callbacks[0]['event_bytes'], callbacks[0]['event_digest'])
    accepted = callback['result']
    if type(accepted) is not dict or _reader_set(accepted, budget=c.budget) != _reader_set((_reader_reserve(c.budget, 144) or ('id', 'digest', 'record', 'accepted', 'disposition')), budget=c.budget) or type(accepted['accepted']) is not int or (accepted['accepted'] != 1) or (accepted['disposition'] not in ('accepted', 'pending_host_acknowledgment')) or (not _same_value(accepted['id'], result['id'], budget=c.budget)) or (not _same_value(accepted['record'], result, budget=c.budget)) or (accepted['digest'] != digest(c.canonical(result), budget=c.budget)) or (type(callbacks[0]['callback_accepted']) is not int) or (callbacks[0]['callback_accepted'] != 1) or (not _same_value(callbacks[0]['result_id'], result['id'], budget=c.budget)):
        raise ValueError(code)
    witnessed = False
    for terminal in terminals:
        body = c.json(terminal['event_bytes'], terminal['event_digest'])
        if body['after']['state'] == 'completed' and _same_value(body['after']['final_result_id'], result['id'], budget=c.budget) and _same_value(body['terminal_evidence'], (_reader_reserve(c.budget, 192) or {'host_quiescent': True}), budget=c.budget) and _same_value(body['result'], accepted, budget=c.budget):
            witnessed = True
    if not witnessed:
        raise ValueError(code)
    return result

# Selected invariants transcribed from runtime/workflows/submissions.py:history_successor; acquisition uses Q.
def _local_history_successor(c: _Candidate, instance_id: str, *, fulfilled: bool=False) -> bool:
    _reader_work((_reader_reserve(c.budget, 80) or (instance_id,)), c.budget, copies=2)
    row = _local_successor_for_history(c, instance_id)
    if row is None:
        return False
    if not fulfilled:
        return True
    return (_reader_list(c.revision_links(instance_id), budget=c.budget) + (_reader_reserve(c.budget, 192) or [None]))[0] is not None

# Selected invariants transcribed from runtime/workflows/submissions.py:_validate_handoff_graph; acquisition uses Q.
def _local_validate_handoff_graph(c: _Candidate, row: dict, proof: dict) -> dict | None:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
    _reader_work((_reader_reserve(c.budget, 96) or (row,proof)), c.budget, copies=2)
    code = 'workflow_author_handoff_storage_corrupt'
    revision = row['revision']
    generation = 2 if revision == 2 else 1
    operations = c.select('workflow_submission_operations', submission_id=row['submission_id'])
    rounds = c.select('workflow_rounds', instance_id=row['instance_id'], current_revision=revision)
    if revision == 2 and (not (_reader_list(c.select('workflow_submission_result_links', submission_id=row['submission_id']), budget=c.budget) + (_reader_reserve(c.budget, 192) or [None]))[0]):
        if rounds:
            raise ValueError(code)
        return None
    if not operations:
        if rounds or (_reader_list(c.select('workflow_submissions', id=row['submission_id']), budget=c.budget) + (_reader_reserve(c.budget, 192) or [None]))[0]:
            raise ValueError(code)
        return None
    if len(operations) != 1 or len(rounds) != 1:
        raise ValueError(code)
    intent = c.one('workflow_draft_dispatch_intents', row['intent_id'])
    result = _local_reviewed_source_result(c, row, intent)
    contributors = _reader_tuple(c.select('workflow_instance_contributors', instance_id=row['instance_id']), budget=c.budget)
    candidate = document_from_submission(c.one('workflow_submissions', row['submission_id']), org_namespace=row['org_slug'], pins=proof['pins'], source_result_id=result['id'], contributors=contributors, budget=c.budget) if revision == 2 else document_from_handoff(row, proof, source_result_id=result['id'], contributors=contributors, budget=c.budget)
    submission = document_from_submission(c.one('workflow_submissions', row['submission_id']), org_namespace=operations[0]['org_slug'], pins=proof['pins'], source_result_id=result['id'], contributors=contributors, budget=c.budget)
    _reader_reserve(c.budget, 512)
    _accounted_candidate_key = candidate.review_key
    _accounted_submission_key = submission.review_key
    _reader_work((_reader_reserve(c.budget, 96) or (_accounted_candidate_key, _accounted_submission_key)), c.budget)
    if _accounted_candidate_key != _accounted_submission_key:
        raise ValueError(code)
    round_ = rounds[0]
    if round_['submission_id'] != row['submission_id'] or round_['state'] not in ('reviewing', 'closed') or round_['id'] != _id('workflow-round', row['instance_id'], revision, row['submission_id'], budget=c.budget):
        raise ValueError(code)
    context = c.json(c.one('workflow_contexts', intent['context_id'])['context_bytes'])
    contract = context_contract(context, budget=c.budget)
    bindings = context['binding']['bindings']
    requests = c.select('workflow_review_requests', round_id=round_['id'])
    expected = (_reader_reserve(c.budget, 64) or [])
    tasks = (_reader_reserve(c.budget, 64) or [])
    if len(requests) != len(contract['reviewer_roles']):
        raise ValueError(code)
    for role in contract['reviewer_roles']:
        principal = bindings[role]['principal']
        selected = _reader_list((q for q in requests if q['principal'] == principal and q['assignment_generation'] == generation), budget=c.budget)
        if len(selected) != 1:
            raise ValueError(code)
        q = selected[0]
        scope = review_scope(row=row, proof=proof, context=context, contract=contract, role=role, request_id=q['id'], round_id=round_['id'], binding=bindings[role], generation=generation, budget=c.budget)
        if q['id'] != _id('workflow-request', round_['id'], role, principal, generation, budget=c.budget) or q['status'] not in ('pending', 'approved', 'changes_requested') or q['supersedes_request_id'] is not None or (c.canonical(scope) != q['request_scope_bytes']) or (digest(q['request_scope_bytes'], budget=c.budget) != q['request_scope_digest']):
            raise ValueError(code)
        _reader_append(expected, q['id'], budget=c.budget)
        bridges = c.select('workflow_request_task_bridges', request_id=q['id'])
        dispatch = c.select('workflow_dispatch_operations', request_id=q['id'])
        if contract['role_kinds'][role] == 'human':
            if bridges or dispatch:
                raise ValueError(code)
            _local_validate_human_receipt(c, q, scope, proof)
            continue
        if len(bridges) != 1 or len(dispatch) != 1:
            raise ValueError(code)
        bridge, op = (_reader_reserve(c.budget, 96) or (bridges[0], dispatch[0]))
        task = c.one('tasks', bridge['task_id'])
        outbox = c.select('workflow_dispatch_outbox', request_id=q['id'])
        brief = review_brief(scope, row['candidate_bytes'], budget=c.budget)
        if len(outbox) != 1 or outbox[0]['operation_id'] != op['id'] or bridge['operation_id'] != op['id'] or (op['id'] != _id('workflow-dispatch', q['id'], generation, budget=c.budget)) or (op['operation_key'] != q['id']) or (outbox[0]['id'] != _id('workflow-outbox', op['id'], budget=c.budget)) or (outbox[0]['effect_key'] != 'workflow-review:' + q['id']) or (outbox[0]['host_execution_key'] != 'workflow-review-host:' + op['id']) or (outbox[0]['recovery_owner'] != 'workflow_recovery') or (outbox[0]['artifact_revision'] != revision) or _reader_any_true((outbox[0][k] != intent[k] for k in ('authority_namespace', 'authority_generation', 'authority_digest')), budget=c.budget) or (bridge['instance_id'] != row['instance_id']) or (bridge['assigned_principal'] != principal) or (bridge['assignment_generation'] != generation) or (task['assigned_agent'] != principal) or (task['team'] != bindings[role]['team']) or (op['org_slug'] != row['org_slug']) or (task['brief'] != brief) or (task['task_type'] != 'subtask') or (task['active_chain'] is not None) or (task['active_fanout'] is not None) or (task['parent_task_id'] is not None) or (task['current_session_id'] != bridge['session_id'] and (not (outbox[0]['state'] in ('claimed', 'cancelled', 'uncertain') and (not outbox[0]['host_launch_started']) and (bridge['session_id'] is None)))) or (op['principal'] != principal) or (op['instance_id'] != row['instance_id']) or (op['round_id'] != round_['id']) or (op['request_digest'] != q['request_scope_digest']):
            raise ValueError(code)
        _reader_append(tasks, task['id'], budget=c.budget)
        _local_validate_agent_receipt(c, q, scope)
    if len(tasks) > 3 or len(_reader_set(tasks, budget=c.budget)) != len(tasks) or len(_reader_set(expected, budget=c.budget)) != len(expected):
        raise ValueError(code)
    events = c.select('workflow_events', instance_id=row['instance_id'], revision=revision, event_kind='submitted')
    links = c.select('workflow_submission_result_links', submission_id=row['submission_id'])
    if len(events) != 1 or len(links) != 1 or links[0]['task_result_id'] != intent['final_result_id']:
        raise ValueError(code)
    event = c.json(events[0]['event_bytes'])
    graph = (_reader_reserve(c.budget, 576) or dict(submission_id=row['submission_id'], round_id=round_['id'], request_ids=expected, reviewer_task_ids=tasks))
    _accounted_event = (_reader_reserve(c.budget, 64 + 128 * (4 + len(graph))) if c.budget is not None else None) or dict(event_kind='submitted', instance_id=row['instance_id'], revision=revision, submission_digest=row['candidate_digest'], **graph)
    _reader_work((_reader_reserve(c.budget, 96) or (event, _accounted_event)), c.budget)
    if event != _accounted_event:
        raise ValueError(code)
    contributor = (_reader_reserve(c.budget, 704) or dict(principal=row['principal'], source_task_id=row['source_task_id'], source_session_id=row['source_session_id'], source_result_id=(_reader_reserve(c.budget, 128) or str(intent['final_result_id'])), contribution_kind='author'))
    for table, key in (('workflow_submission_contributors', 'submission_id'), ('workflow_instance_contributors', 'instance_id')):
        identity = row['submission_id'] if key == 'submission_id' else row['instance_id']
        records = _reader_list((r for r in c.rows(table, key, identity) if r['contribution_kind'] == 'author'), budget=c.budget)
        if key == 'instance_id' and _local_history_successor(c, row['instance_id']):
            _local_validate_author_history(c, row['instance_id'], records)
            records = _reader_list((r for r in records if r['source_task_id'] == row['source_task_id']), budget=c.budget)
        _accounted_contributors = _reader_reserve(c.budget, 192) or [((_reader_reserve(c.budget, 64 + 128 * (1 + len(contributor))) if c.budget is not None else None) or {key: identity, **contributor})]
        _reader_work((_reader_reserve(c.budget, 96) or (records, _accounted_contributors)), c.budget)
        if records != _accounted_contributors:
            raise ValueError(code)
    _local_validate_collection(c, row, proof, graph)
    return graph

# Selected invariants transcribed from runtime/workflows/revisions.py:successor_for_history; acquisition uses Q.
def _local_successor_for_history(c: _Candidate, instance_id: str) -> dict | None:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
    _reader_work((_reader_reserve(c.budget, 80) or (instance_id,)), c.budget, copies=2)
    if not 'workflow_document_revision_operations' in c.metadata:
        return None
    rows = c.select('workflow_document_revision_operations', instance_id=instance_id)
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError(CODE)
    row = rows[0]
    body = c.handoff(row['admission_bytes'], row['admission_digest'])
    intent = c.one('workflow_draft_dispatch_intents', row['draft_intent_id'])
    if body.get('format') != 'workflow-document-revision-admission@1' or c.canonical(body.get('operation')) != c.canonical(_hex(_reader_dict_pairs(((_reader_reserve(c.budget, 96) or (k,  v )) for k, v in row.items() if k not in ('admission_bytes', 'admission_digest')), budget=c.budget), budget=c.budget)) or intent['admission_kind'] != 'revision' or (intent['instance_id'] != instance_id) or (intent['context_id'] != row['context_id']) or (intent['activation_id'] != row['activation_id']) or (intent['request_bytes'] != row['request_bytes']) or (intent['request_digest'] != row['request_digest']) or (row['expected_revision'] != 1) or (row['target_revision'] != 2):
        raise ValueError(CODE)
    return row

# Selected invariants transcribed from runtime/workflows/revisions.py:validate_author_history; acquisition uses Q.
def _local_validate_author_history(c: _Candidate, instance_id: str, records: list[dict]) -> None:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
    _reader_work((_reader_reserve(c.budget, 96) or (instance_id,records)), c.budget, copies=2)
    successor = _local_successor_for_history(c, instance_id)
    if successor is None:
        raise ValueError(CODE)
    handoffs = c.select('workflow_author_handoffs', submission_id=successor['predecessor_submission_id'])
    if len(handoffs) != 1:
        raise ValueError(CODE)
    first = handoffs[0]
    original = c.one('workflow_draft_dispatch_intents', first['intent_id'])
    result = _local_reviewed_source_result(c, first, original)
    expected = (_reader_reserve(c.budget, 192) or [(_reader_reserve(c.budget, 832) or dict(instance_id=instance_id, principal=first['principal'], source_task_id=first['source_task_id'], source_session_id=first['source_session_id'], source_result_id=(_reader_reserve(c.budget, 128) or str(result['id'])), contribution_kind='author'))])
    intent = c.one('workflow_draft_dispatch_intents', successor['draft_intent_id'])
    links = c.revision_links(instance_id)
    if links:
        subject = (_reader_reserve(c.budget, 576) or dict(submission_id=links[0]['submission_id'], source_task_id=intent['task_id'], source_session_id=intent['session_id'], principal=intent['assigned_principal']))
        result = _local_reviewed_source_result(c, subject, intent)
        if len(links) != 1 or links[0]['task_result_id'] != result['id']:
            raise ValueError(CODE)
        _reader_append(expected, _reader_reserve(c.budget, 832) or dict(instance_id=instance_id, principal=intent['assigned_principal'], source_task_id=intent['task_id'], source_session_id=intent['session_id'], source_result_id=(_reader_reserve(c.budget, 128) or str(result['id'])), contribution_kind='author'), budget=c.budget)
    if c.canonical(_reader_sorted(records, key=lambda r: r['source_task_id'], budget=c.budget)) != c.canonical(_reader_sorted(expected, key=lambda r: r['source_task_id'], budget=c.budget)):
        raise ValueError(CODE)

# Selected invariants transcribed from runtime/workflows/revisions.py:_predecessor; acquisition uses Q.
def _local_predecessor(c: _Candidate, handoff: dict, proof: dict, graph: dict) -> tuple[dict, list[dict]]:
    _reader_work((_reader_reserve(c.budget, 112) or (handoff,proof,graph)), c.budget, copies=2)
    from runtime.workflows.review_receipts import recipient_receipt
    round_ = c.one('workflow_rounds', graph['round_id'])
    requests = _reader_list((c.one('workflow_review_requests', qid) for qid in graph['request_ids']), budget=c.budget)
    if round_['state'] != 'closed' or _reader_any_true((q['status'] == 'pending' for q in requests), budget=c.budget) or (not _reader_any_true((q['status'] == 'changes_requested' for q in requests), budget=c.budget)):
        raise WorkflowActivationError('workflow_document_revision_predecessor_not_closed')
    receipts, feedback = (_reader_reserve(c.budget, 96) or ((_reader_reserve(c.budget, 64) or []), (_reader_reserve(c.budget, 64) or [])))
    for request in requests:
        rows = c.select('workflow_review_receipts', request_id=request['id'])
        if len(rows) != 1:
            raise ValueError(CODE)
        receipt = rows[0]
        scope = c.json(request['request_scope_bytes'], request['request_scope_digest'])
        safe = recipient_receipt(receipt, budget=c.budget)
        _reader_append(receipts, receipt, budget=c.budget)
        _reader_append(feedback, _reader_reserve(c.budget, 960) or dict(role=scope['role'], request_id=request['id'], receipt_id=receipt['id'], outcome=receipt['outcome'], findings=(_reader_reserve(c.budget, 320) or dict(findings_bytes_base64=safe['findings_bytes_base64'], findings_digest=safe['findings_digest'])), request_scope_digest=request['request_scope_digest'], receipt_digest=receipt['proof_digest']), budget=c.budget)
    return (_reader_reserve(c.budget, 96) or ((_reader_reserve(c.budget, 1216) or dict(handoff_id=handoff['id'], source_intent_id=handoff['intent_id'], submission_id=handoff['submission_id'], submission_digest=handoff['candidate_digest'], round_id=graph['round_id'], revision=1, request_ids=graph['request_ids'], receipt_ids=_reader_list((p['id'] for p in receipts), budget=c.budget), receipt_digests=_reader_list((p['proof_digest'] for p in receipts), budget=c.budget))), feedback))

# Selected invariants transcribed from runtime/workflows/revisions.py:validate_revision_record; acquisition uses Q.
def _local_validate_revision_record(c: _Candidate, row: dict, *, expected_org_slug: str | None) -> dict:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
    _reader_work((_reader_reserve(c.budget, 80) or (row,)), c.budget, copies=2)
    try:
        _reader_reserve(c.budget, 128)
        command = parse_revision(c.handoff(row['request_bytes'], row['request_digest']), budget=c.budget)
        proof = c.handoff(row['admission_bytes'], row['admission_digest'])
        if _reader_set(proof, budget=c.budget) != _reader_set((_reader_reserve(c.budget, 160) or ('format', 'operation', 'client_request', 'predecessor', 'pins', 'admission_authority')), budget=c.budget) or proof['format'] != 'workflow-document-revision-admission@1' or c.canonical(command) != row['request_bytes'] or (c.canonical(command) != c.canonical(proof['client_request'])) or (row['org_slug'] != expected_org_slug) or (row['principal'] != 'human:founder') or (row['id'] != _id('workflow-document-revision', row['org_slug'], row['principal'], row['operation_key'], budget=c.budget)) or (c.canonical(proof['operation']) != c.canonical(_hex(_reader_dict_pairs(((_reader_reserve(c.budget, 96) or (k,  v )) for k, v in row.items() if k not in ('admission_bytes', 'admission_digest')), budget=c.budget), budget=c.budget))):
            raise ValueError(CODE)
        old = c.select('workflow_author_handoffs', submission_id=row['predecessor_submission_id'])
        if len(old) != 1:
            raise ValueError(CODE)
        old = old[0]
        oldproof = _local_handoff_record(c, old, expected_org_slug=expected_org_slug)
        graph = _local_validate_handoff_graph(c, old, oldproof)
        if graph is None:
            raise ValueError(CODE)
        predecessor, feedback = _local_predecessor(c, old, oldproof, graph)
        first = c.one('workflow_draft_dispatch_intents', old['intent_id'])
        intent = c.one('workflow_draft_dispatch_intents', row['draft_intent_id'])
        source = c.one('workflow_contexts', first['context_id'])
        ctx = _context(row['org_slug'], row['id'], source, predecessor, feedback, budget=c.budget)
        context = c.one('workflow_contexts', row['context_id'])
        task = c.one('tasks', intent['task_id'])
        scope = (_reader_reserve(c.budget, 448) or dict(assigned_agent=first['assigned_principal'], team=c.one('tasks', first['task_id'])['team'], brief=revision_brief(ctx, old['candidate_bytes'], budget=c.budget)))
        admission = (_reader_reserve(c.budget, 1216) or dict(org_slug=row['org_slug'], instance_id=row['instance_id'], activation_id=row['activation_id'], activation_revision=1, attempt_sequence=2, predecessor_intent_id=first['id'], admission_principal=row['principal'], operation_key=row['operation_key'], request_digest=row['request_digest']))
        expected_immutable = _reader_dict_pairs(((_reader_reserve(c.budget, 96) or (k,  v )) for k, v in first.items() if k not in _DRAFT_MUTABLE), budget=c.budget)
        _reader_update(expected_immutable, (_reader_reserve(c.budget, 1984) or {'id': digest(c.canonical(admission), budget=c.budget), 'attempt_sequence': 2, 'predecessor_intent_id': first['id'], 'admission_kind': 'revision', 'operation_key': row['operation_key'], 'request_bytes': row['request_bytes'], 'request_digest': row['request_digest'], 'task_id': task['id'], 'context_id': context['id'], 'assignment_generation': 2, 'task_scope_bytes': c.canonical(scope), 'task_scope_digest': digest(c.canonical(scope), budget=c.budget), 'effect_key': f"workflow-initial-draft:{row['instance_id']}:2", 'host_execution_key': 'workflow-draft-host:' + row['draft_intent_id'], 'created_at': row['created_at']}), budget=c.budget)
        if c.canonical(_hex(expected_immutable, budget=c.budget)) != c.canonical(_hex(_reader_dict_pairs(((_reader_reserve(c.budget, 96) or (k,  v )) for k, v in intent.items() if k not in _DRAFT_MUTABLE), budget=c.budget), budget=c.budget)) or context != (_reader_reserve(c.budget, 832) or dict(id=_id('workflow-context', row['instance_id'], digest(c.canonical(ctx), budget=c.budget), budget=c.budget), binding_snapshot_id=first['binding_snapshot_id'], context_bytes=c.canonical(ctx), context_digest=digest(c.canonical(ctx), budget=c.budget), source_kind='document-revision', source_id=row['id'])) or len(context['context_bytes']) > 1048576 or (len(row['admission_bytes']) > 16777216) or (row['activation_id'] != first['activation_id']) or (row['instance_id'] != first['instance_id']) or (row['binding_snapshot_id'] != first['binding_snapshot_id']) or (row['authorization_revision_id'] != oldproof['pins']['authorization']['id']) or (row['predecessor_round_id'] != graph['round_id']) or (first['is_current'] != 0) or (first['state'] != 'completed') or (task['brief'] != scope['brief']) or (task['assigned_agent'] != scope['assigned_agent']) or (task['team'] != scope['team']) or (task['task_type'] != 'subtask') or (task['parent_task_id'] is not None) or (task['active_chain'] is not None) or (task['active_fanout'] is not None) or (command != (_reader_reserve(c.budget, 1216) or dict(format='workflow-document-revision-command@1', operation_key=row['operation_key'], instance_id=row['instance_id'], activation_id=row['activation_id'], expected_revision=1, predecessor_submission_id=old['submission_id'], predecessor_submission_digest=old['candidate_digest'], predecessor_round_id=graph['round_id'], authority=(_reader_reserve(c.budget, 448) or dict(namespace='org/' + row['org_slug'], generation=first['authority_generation'], snapshot_digest=first['authority_digest']))))) or (c.canonical(proof['predecessor']) != c.canonical(predecessor)) or (c.canonical(proof['pins']) != c.canonical(_pins(oldproof, context, intent, budget=c.budget))) or (c.canonical(proof['admission_authority']) != c.canonical(dict(oldproof['admission_authority'], actions=revision_actions(ctx, budget=c.budget)))):
            raise ValueError(CODE)
        _local_reviewed_source_result(c, old, first)
        return proof
    except ((ValueError, KeyError, TypeError, UnicodeError)) as exc:
        raise ValueError(CODE) from exc

# Selected invariants transcribed from runtime/workflows/revisions.py:_stage; acquisition uses Q.
def _local_stage(c: _Candidate, row: dict, intent: dict) -> tuple[dict, dict, dict] | None:
    _reader_work((_reader_reserve(c.budget, 96) or (row,intent)), c.budget, copies=2)
    rows = c.select('workflow_submission_operations', instance_id=row['instance_id'], source_task_id=intent['task_id'])
    if not rows:
        if (_reader_list(c.select('workflow_submissions', instance_id=row['instance_id'], revision=2), budget=c.budget) + (_reader_reserve(c.budget, 192) or [None]))[0]:
            raise ValueError(CODE)
        return None
    if len(rows) != 1:
        raise ValueError(CODE)
    operation = rows[0]
    submission = c.one('workflow_submissions', operation['submission_id'])
    provenance = c.handoff(operation['provenance_bytes'], operation['provenance_digest'])
    request = c.handoff(c.unhex(provenance['request_bytes']), operation['request_digest'])
    original = dict(request, format='workflow-author-revision-command@1')
    for key in ('submission_id', 'instance_id', 'revision'):
        original.pop(key)
    command = parse_revision(original, author=True, budget=c.budget)
    sid = _id('workflow-submission', row['instance_id'], 2, row['org_slug'], intent['assigned_principal'], command['operation_key'], 'workflow-author-revision@1', budget=c.budget)
    expected = dict(command, format='workflow-author-revision-request@1', submission_id=sid, instance_id=row['instance_id'], revision=2)
    if c.canonical(request) != c.canonical(expected) or command['revision_operation_id'] != row['id'] or submission != (_reader_reserve(c.budget, 1344) or dict(id=sid, instance_id=row['instance_id'], revision=2, submission_bytes=content(command, budget=c.budget), submission_digest=command['submission_digest'], storage_ref=None, source_task_id=intent['task_id'], source_session_id=intent['session_id'], source_result_id=None, author_principal=intent['assigned_principal'])) or (operation['operation_key'] != command['operation_key']) or (operation['org_slug'] != row['org_slug']) or (operation['principal'] != intent['assigned_principal']):
        raise ValueError(CODE)
    stamp = datetime.strptime(operation['created_at'], '%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=timezone.utc)
    if operation['created_at'] != stamp.strftime('%Y-%m-%dT%H:%M:%S.%fZ'):
        raise ValueError(CODE)
    launches = c.select('workflow_draft_dispatch_events', intent_id=intent['id'], event_kind='launch_reserved')
    callbacks = _reader_list((r for r in c.select('workflow_draft_dispatch_events', intent_id=intent['id']) if r['event_kind'] in ('callback_recorded', 'cancel_requested')), budget=c.budget)
    if len(launches) != 1 or stamp < datetime.fromisoformat(launches[0]['created_at']) or _reader_any_true((stamp > datetime.fromisoformat(e['created_at']) for e in callbacks), budget=c.budget):
        raise ValueError(CODE)
    return (_reader_reserve(c.budget, 112) or (submission, operation, command))

# Selected invariants transcribed from runtime/workflows/revisions.py:review_row; acquisition uses Q.
def _local_review_row(c: _Candidate, operation: dict) -> dict | None:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
    _reader_reserve(c.budget, 0)
    intent = c.one('workflow_draft_dispatch_intents', operation['draft_intent_id'])
    stage = _local_stage(c, operation, intent)
    if stage is None:
        return None
    submission, staged, _ = stage
    return (_reader_reserve(c.budget, 1984) or dict(id=operation['id'], intent_id=intent['id'], instance_id=operation['instance_id'], org_slug=operation['org_slug'], principal=intent['assigned_principal'], source_task_id=intent['task_id'], source_session_id=intent['session_id'], operation_key=staged['operation_key'], submission_id=submission['id'], revision=2, candidate_bytes=submission['submission_bytes'], candidate_digest=submission['submission_digest'], request_bytes=c.unhex(c.handoff(staged['provenance_bytes'], staged['provenance_digest'])['request_bytes']), request_digest=staged['request_digest'], created_at=operation['created_at']))

# Selected invariants transcribed from runtime/workflows/revisions.py:selected_subject; acquisition uses Q.
def _local_selected_subject(c: _Candidate, scope: dict) -> tuple[dict, dict]:
    _reader_reserve(c.budget, 0)
    if review_family(scope, budget=c.budget) == 3:
        operation = c.one('workflow_document_revision_operations', scope['revision_operation_id'])
        proof = _local_validate_revision_record(c, operation, expected_org_slug=scope['org_slug'])
        row = _local_review_row(c, operation)
        if row is None:
            raise ValueError(CODE)
        return (_reader_reserve(c.budget, 96) or (row, proof))
    row = c.one('workflow_author_handoffs', scope['handoff_id'])
    return (_reader_reserve(c.budget, 96) or (row, _local_handoff_record(c, row, expected_org_slug=scope['org_slug'])))

# Selected invariants transcribed from runtime/workflows/review_receipts.py:_validate_submission_subject; acquisition uses Q.
def _local_validate_submission_subject(c: _Candidate, submission: dict, scope: dict) -> None:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
    _reader_work((_reader_reserve(c.budget, 96) or (submission,scope)), c.budget, copies=2)
    code = 'workflow_review_receipt_storage_corrupt'
    try:
        _reader_reserve(c.budget, 112)
        operations = c.select('workflow_submission_operations', submission_id=submission['id'])
        links = c.select('workflow_submission_result_links', submission_id=submission['id'])
        if len(operations) != 1 or len(links) != 1:
            raise ValueError(code)
        contributors = _reader_tuple(c.select('workflow_instance_contributors', instance_id=submission['instance_id']), budget=c.budget)
        artifact = document_from_submission(submission, org_namespace=operations[0]['org_slug'], pins=_reader_dict_pairs(((_reader_reserve(c.budget, 96) or (key,  scope[key] )) for key in ('template', 'activation', 'authorization', 'binding_snapshot_id', 'context_id')), budget=c.budget), source_result_id=links[0]['task_result_id'], contributors=contributors, budget=c.budget)
        require_document_subject(artifact, scope, budget=c.budget)
    except ((KeyError, TypeError, ValueError)) as exc:
        raise ValueError(code) from exc

# Selected invariants transcribed from runtime/workflows/review_receipts.py:validate_human_receipt; acquisition uses Q.
def _local_validate_human_receipt(c: _Candidate, request: dict, scope: dict, handoff: dict) -> None:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
    _reader_work((_reader_reserve(c.budget, 112) or (request,scope,handoff)), c.budget, copies=2)
    from runtime.workflows.review_dispatch import _same_value
    code = 'workflow_review_receipt_storage_corrupt'
    if scope['kind'] != 'human' or scope['principal'] != 'founder':
        raise ValueError(code)
    receipts = c.select('workflow_review_receipts', request_id=request['id'])
    if (_reader_list(c.select('workflow_current_assignments', instance_id=scope['instance_id'], role_key=scope['role']), budget=c.budget) + (_reader_reserve(c.budget, 192) or [None]))[0] or (_reader_list(c.select('workflow_instance_tasks', instance_id=scope['instance_id'], role_key=scope['role']), budget=c.budget) + (_reader_reserve(c.budget, 192) or [None]))[0] or (_reader_list(c.select('workflow_task_results', instance_id=scope['instance_id'], principal='founder'), budget=c.budget) + (_reader_reserve(c.budget, 192) or [None]))[0] or (_reader_list(c.request_evidence(request['id']), budget=c.budget) + (_reader_reserve(c.budget, 192) or [None]))[0]:
        raise ValueError(code)
    if request['status'] == 'pending':
        if receipts:
            raise ValueError(code)
        return
    if len(receipts) != 1:
        raise ValueError(code)
    receipt = receipts[0]
    if type(receipt['proof_bytes']) is not bytes or not 1 <= len(receipt['proof_bytes']) <= 16777216:
        raise ValueError(code)
    proof = c.json(receipt['proof_bytes'], receipt['proof_digest'])
    command = parse_human_review(proof['command'], budget=c.budget)
    created_at = proof['created_at']
    stamp = datetime.strptime(created_at, '%Y-%m-%dT%H:%M:%S.%fZ')
    submission = c.one('workflow_submissions', scope['submission_id'])
    contributors = _reader_set((r[0] for r in _reader_list(((_reader_reserve(c.budget, 80) or (r['principal'],)) for r in c.select('workflow_instance_contributors', instance_id=scope['instance_id'])), budget=c.budget)), budget=c.budget)
    _reader_update(contributors, _reader_set((r[0] for r in _reader_list(((_reader_reserve(c.budget, 80) or (r['principal'],)) for r in c.select('workflow_submission_operations', instance_id=scope['instance_id'])), budget=c.budget)), budget=c.budget), budget=c.budget)
    if command['format'] != f'workflow-human-review-command@{review_family(scope, budget=c.budget)}' or created_at != stamp.strftime('%Y-%m-%dT%H:%M:%S.%fZ') or command['request_id'] != request['id'] or (command['expected_revision'] != scope['revision']) or (command['request_scope_digest'] != request['request_scope_digest']) or (command['outcome'] not in scope['outcomes']) or (request['status'] != command['outcome']) or (request['principal'] in contributors):
        raise ValueError(code)
    _local_validate_submission_subject(c, submission, scope)
    expected = _human_proof(request, scope, handoff, command, created_at, budget=c.budget)
    if not _same_value(proof, expected, budget=c.budget) or not _same_value(receipt, _human_receipt_row(request, scope, expected, budget=c.budget), budget=c.budget) or (not _same_value(c.human_keys(org_slug=scope['org_slug'], operation_key=command['operation_key']), (_reader_reserve(c.budget, 192) or [receipt]), budget=c.budget)):
        raise ValueError(code)

# Selected invariants transcribed from runtime/workflows/review_receipts.py:_completed_proof; acquisition uses Q.
def _local_completed_proof(c: _Candidate, request: dict, scope: dict) -> tuple[dict, dict, dict] | None:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
    _reader_work((_reader_reserve(c.budget, 96) or (request,scope)), c.budget, copies=2)
    from runtime.workflows.review_dispatch import _same_value
    _local_validate_review_dispatch_storage(c, request_id=request['id'])
    code = 'workflow_review_receipt_storage_corrupt'
    bridge = _local_record_bridge(c, request['id'])
    events = c.ordered(c.select('workflow_dispatch_events', operation_id=bridge['operation_id']), 'event_seq')
    stages = _reader_list((e for e in events if e['event_kind'] == 'review_staged'), budget=c.budget)
    if len(stages) != 1 or not events or events[-1]['event_kind'] != 'completed' or (bridge['state'] != 'completed'):
        return None
    terminal = events[-1]
    body = c.json(terminal['event_bytes'], terminal['event_digest'])
    stage = c.json(stages[0]['event_bytes'], stages[0]['event_digest'])
    accepted = body['result']
    if type(accepted) is not dict or type(accepted['id']) is not int or accepted['id'] <= 0 or (not _same_value(body['terminal_evidence'], (_reader_reserve(c.budget, 192) or {'host_quiescent': True}), budget=c.budget)):
        raise ValueError(code)
    result = c.one('task_results', accepted['id'])
    task = c.one('tasks', bridge['task_id'])
    if not _same_value(result, accepted['record'], budget=c.budget) or bridge['result_id'] != (_reader_reserve(c.budget, 128) or str(result['id'])) or result['status'] != 'completed' or (result['task_id'] != bridge['task_id']) or (result['agent'] != bridge['assigned_principal']) or (result['session_id'] != bridge['session_id']) or (task['status'] != 'completed') or (task['cancelled_at'] is not None) or (task['assigned_agent'] != result['agent']) or (task['current_session_id'] != result['session_id']) or (stage['stage']['session_id'] != result['session_id']):
        raise ValueError(code)
    if result['decision_json'] is not None or result['waiting_on_job_ids'] is not None:
        return None
    submission = c.one('workflow_submissions', scope['submission_id'])
    _local_validate_submission_subject(c, submission, scope)
    contributors = _reader_set((r[0] for r in _reader_list(((_reader_reserve(c.budget, 80) or (r['principal'],)) for r in c.select('workflow_instance_contributors', instance_id=scope['instance_id'])), budget=c.budget)), budget=c.budget)
    _reader_update(contributors, _reader_set((r[0] for r in _reader_list(((_reader_reserve(c.budget, 80) or (r['principal'],)) for r in c.select('workflow_submission_operations', instance_id=scope['instance_id'])), budget=c.budget)), budget=c.budget), budget=c.budget)
    if request['principal'] in contributors:
        raise ValueError(code)
    proof = (_reader_reserve(c.budget, 1472) or dict(format=f'workflow-agent-review-proof@{review_family(scope, budget=c.budget)}', scope=scope, request_scope_digest=request['request_scope_digest'], stage_event=(_reader_reserve(c.budget, 448) or dict(id=stages[0]['id'], digest=stages[0]['event_digest'], body=stage)), terminal_event=(_reader_reserve(c.budget, 320) or dict(id=terminal['id'], digest=terminal['event_digest'])), callback=accepted, result_id=result['id'], task_id=result['task_id'], session_id=result['session_id'], principal=result['agent'], outcome=stage['stage']['command']['outcome']))
    return (_reader_reserve(c.budget, 112) or (proof, result, terminal))

# Selected invariants transcribed from runtime/workflows/review_receipts.py:_record_bridge; acquisition uses Q.
def _local_record_bridge(c: _Candidate, request_id: str) -> dict:
    _reader_reserve(c.budget, 0)
    rows = c.select('workflow_request_task_bridges', request_id=request_id)
    if len(rows) != 1:
        raise ValueError('workflow_review_receipt_storage_corrupt')
    return rows[0]

# Selected invariants transcribed from runtime/workflows/review_receipts.py:validate_agent_receipt; acquisition uses Q.
def _local_validate_agent_receipt(c: _Candidate, request: dict, scope: dict) -> None:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
    _reader_work((_reader_reserve(c.budget, 96) or (request,scope)), c.budget, copies=2)
    from runtime.workflows.review_dispatch import _same_value
    code = 'workflow_review_receipt_storage_corrupt'
    receipts = c.select('workflow_review_receipts', request_id=request['id'])
    bridge = _local_record_bridge(c, request['id'])
    selectors = (_reader_reserve(c.budget, 576) or {'workflow_instance_tasks': (_reader_reserve(c.budget, 320) or dict(instance_id=scope['instance_id'], task_id=bridge['task_id'])), 'workflow_task_results': (_reader_reserve(c.budget, 320) or dict(instance_id=scope['instance_id'], task_id=bridge['task_id'])), 'workflow_current_assignments': (_reader_reserve(c.budget, 320) or dict(instance_id=scope['instance_id'], role_key=scope['role'])), 'workflow_receipt_evidence': (_reader_reserve(c.budget, 192) or dict(task_id=bridge['task_id']))})
    if scope['revision'] == 1 and _local_history_successor(c, scope['instance_id'], fulfilled=True):
        selectors.pop('workflow_current_assignments')
    actual = _reader_dict_pairs(((_reader_reserve(c.budget, 96) or (table,  c.select(table, **fields) )) for table, fields in selectors.items()), budget=c.budget)
    if request['status'] == 'pending':
        _local_validate_review_dispatch_storage(c, request_id=request['id'])
        if receipts or _reader_any_true(actual.values(), budget=c.budget):
            raise ValueError(code)
        return
    completed = _local_completed_proof(c, request, scope)
    if len(receipts) != 1 or completed is None:
        raise ValueError(code)
    expected = _receipt_rows(request, scope, *completed, budget=c.budget)
    if request['status'] != expected['workflow_review_receipts']['outcome'] or not _same_value(receipts, (_reader_reserve(c.budget, 192) or [expected['workflow_review_receipts']]), budget=c.budget) or _reader_any_true((not _same_value(actual[table], (_reader_reserve(c.budget, 192) or [expected[table]]), budget=c.budget) for table in selectors), budget=c.budget):
        raise ValueError(code)

# Selected invariants transcribed from runtime/workflows/review_dispatch.py:validate_stage; acquisition uses Q.
def _local_validate_stage(c: _Candidate, body: dict, *, outbox: dict, bridge: dict) -> dict:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
    _reader_work((_reader_reserve(c.budget, 80) or (body,)), c.budget, copies=2)
    code = 'workflow_review_storage_corrupt'
    stage = body['stage']
    if type(stage) is not dict or _reader_set(stage, budget=c.budget) != _reader_set((_reader_reserve(c.budget, 144) or ('command', 'request_digest', 'scope', 'outbox_id', 'session_id')), budget=c.budget):
        raise ValueError(code)
    command = parse_stage(stage['command'], budget=c.budget)
    request = c.one('workflow_review_requests', bridge['request_id'])
    scope = c.json(request['request_scope_bytes'], request['request_scope_digest'])
    before = body['before']
    if command['format'] != f'workflow-review-stage-command@{review_family(scope, budget=c.budget)}' or not _same_value(stage['scope'], scope, budget=c.budget) or scope['kind'] != 'agent' or (command['outcome'] not in scope['outcomes']) or (command['request_id'] != request['id']) or (command['expected_revision'] != scope['revision']) or (command['request_scope_digest'] != request['request_scope_digest']) or (stage['request_digest'] != digest(c.canonical(command), budget=c.budget)) or (stage['outbox_id'] != outbox['id']) or (stage['session_id'] != before['bridge']['session_id']) or (scope['principal'] != bridge['assigned_principal']) or (not _same_value(scope['assignment_generation'], bridge['assignment_generation'], budget=c.budget)) or (not stage['session_id']) or (before['bridge']['result_id'] is not None) or (before['outbox']['host_launch_started'] != 1) or (before['outbox']['state'] not in ('claimed', 'running')) or (not _same_value(before, body['after'], budget=c.budget)) or (body['result'] is not None) or (body['terminal_evidence'] is not None):
        raise ValueError(code)
    matches = c.stage_keys(org_slug=body['org_slug'], principal=scope['principal'], operation_key=command['operation_key'])
    if len(matches) != 1 or matches[0]['operation_id'] != bridge['operation_id']:
        raise ValueError(code)
    return stage

# Selected invariants transcribed from runtime/workflows/review_dispatch.py:_validate_review_dispatch_storage; acquisition uses Q.
def _local_validate_review_dispatch_storage(c: _Candidate, *, request_id: str) -> None:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
    _reader_work((), c.budget, copies=2)
    code = 'workflow_review_storage_corrupt'
    bridges = c.select('workflow_request_task_bridges', request_id=request_id)
    operations = c.select('workflow_dispatch_operations', request_id=request_id)
    outboxes = c.select('workflow_dispatch_outbox', request_id=request_id)
    if len(bridges) != 1 or len(operations) != 1 or len(outboxes) != 1:
        raise ValueError(code)
    bridge, operation, outbox = (_reader_reserve(c.budget, 112) or (bridges[0], operations[0], outboxes[0]))
    retained = (_reader_reserve(c.budget, 320) or {'outbox': outbox, 'bridge': bridge})
    _validate_projection(retained, outbox=outbox, bridge=bridge, budget=c.budget)
    initial = (_reader_reserve(c.budget, 320) or dict(outbox=dict(outbox, state='queued', claim_token=None, claim_owner=None, host_launch_started=0, host_execution_id=None, last_error=None, updated_at=outbox['created_at']), bridge=dict(bridge, state='queued', session_id=None, result_id=None)))
    events = c.ordered(c.select('workflow_dispatch_events', operation_id=operation['id']), 'event_seq')
    previous = None
    accepted = None
    cancelled = False
    staged = False
    for seq, event in enumerate(events, start=1):
        body = c.json(event['event_bytes'], event['event_digest'])
        _validate_projection(body['before'], outbox=outbox, bridge=bridge, budget=c.budget)
        _validate_projection(body['after'], outbox=outbox, bridge=bridge, budget=c.budget)
        is_stage = body['event_kind'] == 'review_staged'
        keys = _reader_set((_reader_reserve(c.budget, 256) or ('format', 'org_slug', 'operation_id', 'request_id', 'task_id', 'event_seq', 'event_kind', 'previous_digest', 'before', 'after', 'terminal_evidence', 'result')), budget=c.budget)
        if _reader_set(body, budget=c.budget) != keys | (_reader_set((_reader_reserve(c.budget, 80) or ('stage',)), budget=c.budget) if is_stage else _reader_set(budget=c.budget)) or body['format'] != ('workflow-review-stage@1' if is_stage else 'workflow-review-dispatch-event@1') or body['org_slug'] != operation['org_slug'] or (body['operation_id'] != operation['id']) or (body['request_id'] != request_id) or (body['task_id'] != bridge['task_id']) or (body['event_kind'] != event['event_kind']) or (body['event_seq'] != seq) or (type(body['event_seq']) is not int) or (event['event_seq'] != seq) or (event['id'] != f"{operation['id']}:{seq}") or (body['before']['outbox']['state'] != event['state_before']) or (body['after']['outbox']['state'] != event['state_after']) or (body['previous_digest'] != (None if previous is None else previous[0]['event_digest'])) or (not _same_value(body['before'], initial if previous is None else previous[1]['after'], budget=c.budget)):
            raise ValueError(code)
        if is_stage:
            if cancelled or staged:
                raise ValueError(code)
            _local_validate_stage(c, body, outbox=outbox, bridge=bridge)
            staged = True
        else:
            _validate_transition(body, cancelled=cancelled, budget=c.budget)
        cancelled = cancelled or body['event_kind'] == 'cancel_requested'
        if body['event_kind'] == 'callback_recorded':
            if accepted is not None or not isinstance(body['result'], dict) or _reader_set(body['result'], budget=c.budget) != _reader_set((_reader_reserve(c.budget, 144) or ('id', 'record', 'digest', 'accepted', 'disposition')), budget=c.budget) or (type(body['result']['id']) is not int) or (type(body['result']['accepted']) is not int) or (body['result']['accepted'] != 1):
                raise ValueError(code)
            accepted = body['result']
            result = c.one('task_results', accepted['id'])
            if not _same_value(accepted['record'], result, budget=c.budget) or accepted['digest'] != digest(c.canonical(result), budget=c.budget) or result['task_id'] != bridge['task_id'] or (result['agent'] != bridge['assigned_principal']) or (result['session_id'] != body['after']['bridge']['session_id']) or (body['after']['bridge']['result_id'] != (_reader_reserve(c.budget, 128) or str(result['id']))):
                raise ValueError(code)
            if accepted['disposition'] != ('accepted' if body['before']['outbox']['state'] == 'running' else 'pending_host_acknowledgment'):
                raise ValueError(code)
        if body['event_kind'] == 'completed' and (accepted is None or accepted['record']['status'] != 'completed' or (not _same_value(body['terminal_evidence'], (_reader_reserve(c.budget, 192) or {'host_quiescent': True}), budget=c.budget)) or (not _same_value(body['result'], accepted, budget=c.budget)) or (not body['after']['outbox']['host_execution_id'])):
            raise ValueError(code)
        previous = (_reader_reserve(c.budget, 96) or (event, body))
    if previous is None:
        if not _same_value(retained, initial, budget=c.budget):
            raise ValueError(code)
    elif not _same_value(previous[1]['after'], retained, budget=c.budget):
        raise ValueError(code)
    callbacks = c.select('workflow_dispatch_callbacks', outbox_id=outbox['id'])
    if len(callbacks) != int(accepted is not None):
        raise ValueError(code)
    if callbacks:
        callback = callbacks[0]
        if callback['task_id'] != bridge['task_id'] or callback['session_id'] != bridge['session_id'] or callback['result_id'] != bridge['result_id'] or (callback['result_digest'] != accepted['digest']) or (type(callback['accepted']) is not int) or (callback['accepted'] != 1) or (type(callback['observed_revision']) is not int) or (callback['observed_revision'] != outbox['artifact_revision']) or (callback['disposition'] != accepted['disposition']) or (callback['id'] != f"{operation['id']}:callback:{accepted['id']}"):
            raise ValueError(code)
    effects = c.select('workflow_dispatch_effects', outbox_id=outbox['id'])
    if len(effects) != int(outbox['host_execution_id'] is not None):
        raise ValueError(code)
    if effects and (effects[0]['host_execution_id'] != outbox['host_execution_id'] or effects[0]['task_id'] != bridge['task_id'] or effects[0]['session_id'] != bridge['session_id'] or (effects[0]['id'] != f"{operation['id']}:host") or (effects[0]['effect_key'] != outbox['effect_key']) or (effects[0]['effect_kind'] != 'host_launch_observed')):
        raise ValueError(code)
    expected_state = 'cancelled' if cancelled else 'completed' if outbox['state'] == 'completed' else 'admitted'
    if outbox['state'] != bridge['state'] or operation['state'] != expected_state:
        raise ValueError(code)

# Selected invariants transcribed from runtime/workflows/review_join.py:_responses; acquisition uses Q.
def _local_responses(c: _Candidate, graph: dict) -> list[dict]:
    _reader_reserve(c.budget, 0)
    return _reader_list((c.one('workflow_review_requests', qid) for qid in graph['request_ids']), budget=c.budget)

# Selected invariants transcribed from runtime/workflows/review_join.py:_event; acquisition uses Q.
def _local_event(c: _Candidate, row: dict, proof: dict, graph: dict, created_at: str) -> dict:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
    _reader_reserve(c.budget, 0)
    grant_pin = proof['pins']['authorization']
    grant = c.json(c.unhex(grant_pin['bytes_hex']), grant_pin['digest'])
    if not _reader_set(ACTIONS, budget=c.budget) <= _reader_set(grant['original_request']['allowed_actions'], budget=c.budget):
        raise ValueError('workflow_review_join_storage_corrupt')
    receipts, assignments = (_reader_reserve(c.budget, 96) or ((_reader_reserve(c.budget, 64) or []), (_reader_reserve(c.budget, 64) or [])))
    for request in _local_responses(c, graph):
        receipt = c.select('workflow_review_receipts', request_id=request['id'])
        if len(receipt) != 1 or request['status'] != 'approved' or receipt[0]['outcome'] != 'approved':
            raise ValueError('workflow_review_join_storage_corrupt')
        _reader_append(receipts, _hex(receipt[0], budget=c.budget), budget=c.budget)
        scope = c.json(request['request_scope_bytes'], request['request_scope_digest'])
        if scope['kind'] == 'agent':
            selected = c.select('workflow_current_assignments', instance_id=row['instance_id'], role_key=scope['role'])
            if len(selected) != 1:
                raise ValueError('workflow_review_join_storage_corrupt')
            _reader_append(assignments, selected[0], budget=c.budget)
    return (_reader_reserve(c.budget, 1856) or dict(format='workflow-review-join@3' if row['revision'] == 2 else 'workflow-review-join@2' if proof['format'] == 'workflow-author-handoff@2' else 'workflow-review-join@1', event_kind='joined', org_slug=row['org_slug'], instance_id=row['instance_id'], round_id=graph['round_id'], submission_id=row['submission_id'], revision=row['revision'], submission_digest=row['candidate_digest'], operation=(_reader_reserve(c.budget, 448) or dict(principal=SERVICE, operation_key=graph['round_id'], action='approve-planning-input')), pins=proof['pins'], admission_authority=((_reader_reserve(c.budget, 64 + 128 * (1 + len(proof['admission_authority']))) if c.budget is not None else None) or dict(proof['admission_authority'], actions=ACTIONS)), receipts=receipts, assignments=assignments, created_at=created_at))

# Selected invariants transcribed from runtime/workflows/review_join.py:validate_collection; acquisition uses Q.
def _local_validate_collection(c: _Candidate, row: dict, proof: dict, graph: dict) -> str:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
    _reader_work((_reader_reserve(c.budget, 112) or (row,proof,graph)), c.budget, copies=2)
    from runtime.workflows.review_dispatch import _same_value
    code = 'workflow_review_join_storage_corrupt'
    round_ = c.one('workflow_rounds', graph['round_id'])
    instance = c.one('workflow_instances', row['instance_id'])
    requests = _local_responses(c, graph)
    pending = _reader_any_true((q['status'] == 'pending' for q in requests), budget=c.budget)
    objection = _reader_any_true((q['status'] == 'changes_requested' for q in requests), budget=c.budget)
    events = c.select('workflow_events', instance_id=row['instance_id'], revision=row['revision'], event_kind='joined')
    replays = c.union('workflow_operation_replays', c.select('workflow_operation_replays', org_slug=row['org_slug'], principal=SERVICE, operation_key=graph['round_id']), c.rows('workflow_operation_replays', 'effect_id', _id('workflow-event', row['instance_id'], row['revision'], 'joined', budget=c.budget)))
    if not events:
        historical = row['revision'] == 1 and _local_history_successor(c, row['instance_id'])
        if replays or (instance['status'] != 'reviewing' and (not (historical and instance['status'] == 'complete'))) or round_['state'] not in ('reviewing', 'closed') or (round_['state'] == 'closed' and (pending or not objection)):
            raise ValueError(code)
        return 'collecting' if pending else 'needs_revision' if objection else 'ready_to_join'
    if len(events) != 1 or pending or objection or (round_['state'] != 'closed') or (instance['status'] != 'complete'):
        raise ValueError(code)
    event = events[0]
    body = c.json(event['event_bytes'], event['event_digest'])
    stamp = datetime.strptime(body['created_at'], '%Y-%m-%dT%H:%M:%S.%fZ')
    if body['created_at'] != stamp.strftime('%Y-%m-%dT%H:%M:%S.%fZ'):
        raise ValueError(code)
    expected = _local_event(c, row, proof, graph, body['created_at'])
    raw = c.canonical(expected)
    if not _same_value(body, expected, budget=c.budget) or not _same_value(event, (_reader_reserve(c.budget, 960) or dict(id=_id('workflow-event', row['instance_id'], row['revision'], 'joined', budget=c.budget), instance_id=row['instance_id'], revision=row['revision'], event_kind='joined', event_bytes=raw, event_digest=digest(raw, budget=c.budget), created_at=body['created_at'])), budget=c.budget) or (not _same_value(replays, (_reader_reserve(c.budget, 192) or [_replay(row, graph, event, budget=c.budget)]), budget=c.budget)):
        raise ValueError(code)
    return 'approved'


def _local_roles(c: _Candidate, request: ActivationRequest, snapshot: dict, template: WorkflowTemplateVersion | None=None) -> dict:
    """Selected activation comparisons after private candidate acquisition; no full-reader certificate."""
    _reader_work((_reader_reserve(c.budget, 112) or (request,snapshot,template)), c.budget, copies=2)
    validate_authority_snapshot(snapshot, budget=c.budget)
    if isinstance(request, DocumentActivationRequest):
        if template is None:
            raise WorkflowActivationError('workflow_activation_invalid_request')
        frozen = _reader_dump(request, by_alias=True, budget=c.budget)
        contract = _contract_for_request(frozen, template, budget=c.budget)
        bindings = frozen['bindings']
        replacements = frozen['eligible_replacements']
        kinds = contract['role_kinds']
        if _reader_set(bindings, budget=c.budget) != _reader_set(kinds, budget=c.budget) or _reader_set(replacements, budget=c.budget) != _reader_set(kinds, budget=c.budget):
            raise WorkflowActivationError('role_binding_not_authorized')
        capabilities = _reader_set((_reader_reserve(c.budget, 128) or ('draft-document', 'submit-immutable-document', 'collect-review', 'approve-planning-input')), budget=c.budget)
        if isinstance(request, DocumentActivationRequest3):
            _reader_add(capabilities, 'stage-immutable-document', budget=c.budget)
        if isinstance(request, ArtifactActivationRequest):
            capabilities = _reader_set((_reader_reserve(c.budget, 144) or ('draft-artifact', 'stage-immutable-artifact', 'submit-immutable-artifact', 'collect-review', 'approve-planning-input')), budget=c.budget)
        if contract['request_changes'] is not None:
            _reader_add(capabilities, 'return-to-author', budget=c.budget)
        if not _reader_set(request.allowed_actions, budget=c.budget) <= capabilities:
            raise WorkflowActivationError('role_binding_not_authorized')
        if _reader_any_true((not _reader_set(item.recipients, budget=c.budget) <= _reader_set(kinds, budget=c.budget) for item in request.inputs), budget=c.budget):
            raise WorkflowActivationError('workflow_activation_invalid_request')
        agents = _reader_dict_pairs(((_reader_reserve(c.budget, 96) or (item['name'],  item )) for item in snapshot['agents'] if item['status'] == 'active'), budget=c.budget)
        teams = _reader_dict_pairs(((_reader_reserve(c.budget, 96) or (item['name'],  item )) for item in snapshot['teams']), budget=c.budget)
        if len(_reader_set((item['principal'] for item in bindings.values()), budget=c.budget)) != len(bindings):
            raise WorkflowActivationError('role_binding_not_authorized')
        for role, kind in kinds.items():
            if kind == 'human':
                if bindings[role] != (_reader_reserve(c.budget, 448) or {'kind': 'human', 'principal': 'founder', 'team': None}) or replacements[role]:
                    raise WorkflowActivationError('role_binding_not_authorized')
                continue
            candidates = [bindings[role], *replacements[role]]
            if len(_reader_set((candidate['principal'] for candidate in candidates), budget=c.budget)) != len(candidates):
                raise WorkflowActivationError('role_binding_not_authorized')
            for candidate in candidates:
                agent = agents.get(candidate['principal'])
                team = teams.get(candidate['team'])
                if candidate['kind'] != 'agent' or agent is None or team is None or (agent['team'] != candidate['team']) or (candidate['principal'] not in snapshot_team_agents(snapshot, team, budget=c.budget)) or (candidate['principal'] in _reader_list((bindings[key]['principal'] for key in kinds if key != role), budget=c.budget)):
                    raise WorkflowActivationError('role_binding_not_authorized')
        return bindings
    bindings = _reader_dump(request.bindings, by_alias=True, budget=c.budget)
    replacements = _reader_dump(request.eligible_replacements, by_alias=True, budget=c.budget)
    agents = _reader_dict_pairs(((_reader_reserve(c.budget, 96) or (item['name'],  item )) for item in snapshot['agents'] if item['status'] == 'active'), budget=c.budget)
    teams = _reader_dict_pairs(((_reader_reserve(c.budget, 96) or (item['name'],  item )) for item in snapshot['teams']), budget=c.budget)
    if bindings['founder'] != (_reader_reserve(c.budget, 448) or {'kind': 'human', 'principal': 'founder', 'team': None}):
        raise WorkflowActivationError('role_binding_not_authorized')
    selected = _reader_list((bindings[role]['principal'] for role in ('product-lead', 'implementer', 'tester')), budget=c.budget)
    if len(_reader_set(selected, budget=c.budget)) != 3:
        raise WorkflowActivationError('role_binding_not_authorized')
    for role in ('product-lead', 'implementer', 'tester'):
        candidates = [bindings[role], *replacements[role]]
        if len(_reader_set((candidate['principal'] for candidate in candidates), budget=c.budget)) != len(candidates):
            raise WorkflowActivationError('role_binding_not_authorized')
        for candidate in candidates:
            agent = agents.get(candidate['principal'])
            team = teams.get(candidate['team'])
            if candidate['kind'] != 'agent' or agent is None or team is None or (agent['team'] != candidate['team']) or (candidate['principal'] not in snapshot_team_agents(snapshot, team, budget=c.budget)):
                raise WorkflowActivationError('role_binding_not_authorized')
            other = _reader_list((bindings[key]['principal'] for key in bindings if key != role), budget=c.budget)
            if candidate['principal'] in other:
                raise WorkflowActivationError('role_binding_not_authorized')
    return bindings

def _local_activation(c: _Candidate, activation_id: str, *, actor: str) -> dict:
    """Selected activation comparisons after private candidate acquisition; no full-reader certificate."""
    _reader_work((_reader_reserve(c.budget, 80) or (activation_id,)), c.budget, copies=2)
    if not c.conn.in_transaction:
        raise WorkflowActivationError('workflow_activation_storage_corrupt')
    operation = (_reader_list(c.select('workflow_activation_operations', activation_id=activation_id, org_slug=c.org, principal=actor), budget=c.budget) + (_reader_reserve(c.budget, 192) or [None]))[0]
    if operation is None:
        raise WorkflowActivationError('workflow_activation_not_found')
    try:
        _reader_reserve(c.budget, 144)
        activation = _reader_dict((_reader_list(c.select('workflow_activations', id=activation_id), budget=c.budget) + (_reader_reserve(c.budget, 192) or [None]))[0], budget=c.budget)
        instance = _reader_dict((_reader_list(c.select('workflow_instances', id=activation['instance_id']), budget=c.budget) + (_reader_reserve(c.budget, 192) or [None]))[0], budget=c.budget)
        intent = _reader_dict((_reader_list(c.select('workflow_draft_dispatch_intents', activation_id=activation_id, attempt_sequence=1), budget=c.budget) + (_reader_reserve(c.budget, 192) or [None]))[0], budget=c.budget)
        binding = _reader_dict((_reader_list(c.select('workflow_binding_snapshots', id=intent['binding_snapshot_id']), budget=c.budget) + (_reader_reserve(c.budget, 192) or [None]))[0], budget=c.budget)
        authorization = _reader_dict((_reader_list(c.select('workflow_authorization_revisions', id=binding['authorization_revision_id']), budget=c.budget) + (_reader_reserve(c.budget, 192) or [None]))[0], budget=c.budget)
        context = _reader_dict((_reader_list(c.select('workflow_contexts', id=intent['context_id']), budget=c.budget) + (_reader_reserve(c.budget, 192) or [None]))[0], budget=c.budget)
        grant = c.json(authorization['authority_bytes'])
        request = parse_request(c.json(intent['request_bytes']), budget=c.budget)
        frozen = _reader_dump(request, by_alias=True, budget=c.budget)
        namespace = f"org/{c.org}/workflow-instance/{instance['id']}"
        template = c.template(identity_id=request.template.identity_id, version=request.template.version, expected_version_id=activation['template_version_id'])
        timestamp = datetime.fromisoformat(activation['created_at'])
        if operation['request_digest'] != intent['request_digest'] or intent['request_digest'] != activation['request_digest'] or activation['activated_by'] != actor or (intent['admission_principal'] != actor) or (operation['operation_key'] != intent['operation_key']) or (request.operation_key != intent['operation_key']) or (instance['owner_principal'] != actor) or (authorization['namespace'] != namespace) or (authorization['revision'] != 1) or (activation['activation_revision'] != 1) or (authorization['source_pin'] != template.source_pin) or (authorization['created_at'] != activation['created_at']) or (binding['created_at'] != activation['created_at']) or (intent['created_at'] != activation['created_at']) or (timestamp.tzinfo is None) or (timestamp.utcoffset().total_seconds() != 0) or (intent['admission_kind'] != 'initial') or (intent['attempt_sequence'] != 1) or (intent['assignment_generation'] != 1) or (intent['predecessor_intent_id'] is not None) or (context['source_kind'] != 'founder-activation') or (context['source_id'] != instance['id']) or (instance['id'] != _id('workflow-instance', c.org, request.instance_id, budget=c.budget)) or (activation_id != _id('workflow-activation', instance['id'], 1, operation['request_digest'], budget=c.budget)) or (template.definition_digest != request.template.definition_digest) or (template.identity_id != activation['template_identity_id']) or (template.namespace != intent['authority_namespace']) or (not template.namespace.startswith(f'org/{c.org}/team/')) or (request.authority.namespace != f'org/{c.org}') or (request.authority.generation != intent['authority_generation']) or (request.authority.snapshot_digest != intent['authority_digest']) or (digest(intent['request_bytes'], budget=c.budget) != operation['request_digest']) or (c.canonical(frozen) != intent['request_bytes']):
            raise ValueError('closure')
        expected_grant = _authorization(org_slug=c.org, instance_id=instance['id'], activation_id=activation_id, task_id=instance['root_task_id'], request=frozen, request_digest=operation['request_digest'], template=template, timestamp=activation['created_at'], budget=c.budget)
        bound = _binding(instance_id=instance['id'], activation_id=activation_id, authorization_id=authorization['id'], template=template, request=frozen, budget=c.budget)
        stored_context = c.json(context['context_bytes'])
        journal = _reader_list(((_reader_reserve(c.budget, 80) or (r['snapshot_bytes'],)) for r in c.publication(request.authority.namespace, request.authority.generation, request.authority.snapshot_digest)), budget=c.budget)
        snapshot_bytes = c.canonical(stored_context['authority_snapshot'])
        if len(journal) != 1 or bytes(journal[0][0]) != snapshot_bytes or digest(snapshot_bytes, budget=c.budget) != request.authority.snapshot_digest:
            raise ValueError('closure')
        _local_roles(c, request, stored_context['authority_snapshot'], template)
        inputs = stored_context['inputs']
        if not isinstance(inputs, list) or len(inputs) != len(request.inputs):
            raise ValueError('closure')
        for item, source in zip(inputs, request.inputs, strict=True):
            if not isinstance(item, dict) or _reader_set(item, budget=c.budget) != _reader_set((_reader_reserve(c.budget, 96) or ('pin', 'bytes_base64')), budget=c.budget) or item['pin'] != _reader_dump(source, budget=c.budget) or (not _reader_set(source.recipients, budget=c.budget) <= _reader_set(frozen['bindings'], budget=c.budget)) or (len(_reader_set(source.recipients, budget=c.budget)) != len(source.recipients)):
                raise ValueError('closure')
            content = c.unbase64(item['bytes_base64'])
            if digest(content, budget=c.budget) != source.sha256 or _reader_decode(_reader_b64encode(content, budget=c.budget), 'ascii', budget=c.budget) != item['bytes_base64']:
                raise ValueError('closure')
        expected_context = activation_context(org_slug=c.org, request=frozen, snapshot=stored_context['authority_snapshot'], template=template, inputs=inputs, authorization=expected_grant, authorization_id=authorization['id'], binding=bound, binding_id=binding['id'], intent_id=intent['id'], budget=c.budget)
        author_role = _author_role(frozen, template, budget=c.budget)
        task_scope = (_reader_reserve(c.budget, 448) or dict(assigned_agent=frozen['bindings'][author_role]['principal'], team=frozen['bindings'][author_role]['team'], brief=_task_brief(frozen, inputs, template, budget=c.budget)))
        if c.canonical(expected_grant) != authorization['authority_bytes'] or c.canonical(bound) != binding['binding_bytes'] or c.canonical(expected_context) != context['context_bytes'] or (len(context['context_bytes']) > CONTEXT_LIMIT) or (c.canonical(task_scope) != intent['task_scope_bytes']) or (binding['id'] != _id('workflow-binding', instance['id'], digest(binding['binding_bytes'], budget=c.budget), budget=c.budget)) or (context['id'] != _id('workflow-context', instance['id'], digest(context['context_bytes'], budget=c.budget), budget=c.budget)) or (authorization['id'] != _id('workflow-authorization', instance['id'], 1, digest(authorization['authority_bytes'], budget=c.budget), budget=c.budget)):
            raise ValueError('closure')
        receipt = (_reader_reserve(c.budget, 2240) or dict(activation_id=activation_id, instance_id=instance['id'], instance_reference=request.instance_id, activation_revision=activation['activation_revision'], root_task_id=instance['root_task_id'], intent_id=intent['id'], template=grant['template'], authority=grant['authority'], bindings=frozen['bindings'], eligible_replacements=frozen['eligible_replacements'], allowed_actions=frozen['allowed_actions'], scope_digest=digest(c.canonical(frozen['scope']), budget=c.budget), context_digest=context['context_digest'], activated_by=grant['actor'], created_at=grant['created_at'], original_request_digest=operation['request_digest'], replayed=False))
        if isinstance(request, DocumentActivationRequest):
            _reader_put(frozen['format'].replace('activation-request', 'activation-receipt'), receipt, 'format', budget=c.budget)
        return receipt
    except ((ValueError, TypeError, KeyError, AttributeError, sqlite3.DatabaseError)) as exc:
        raise WorkflowActivationError('workflow_activation_storage_corrupt') from exc
