"""Private task-directed acquisition in one unchanged native snapshot.

This is neither a complete workflow reader nor an effect/publication token.
Callers discard every row at their first write or transaction boundary.
Only task/result/root/source/bridge and reached scalar reverse relations expand
membership. Local retained-proof comparisons preserve historical participation;
complete readers and their accounting stay at the original workflow boundary.
The baseline _Budget bounds this acquisition and local work cooperatively; it
makes no SQLite-engine, RSS or wall-time guarantee.
"""
from __future__ import annotations
import hashlib
import json
import math
import re
import base64
from datetime import datetime, timezone
import sqlite3
from runtime.infrastructure.workflow_schema import _candidate_structure, _candidate_reference, _candidate_reserve
from runtime.workflows.coding_authority_history import _Budget, _check, _VALUE_LIMIT
from runtime.workflows.coding_source_history import _TASK_COLUMNS, _TASK_TYPES, _RESULT_COLUMNS, _RESULT_TYPES, _AUDIT_COLUMNS, _AUDIT_TYPES

def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'

class _CandidateBudgetExhausted(ValueError):
    pass

class _OwnerIndexesRequired(ValueError):
    pass

class _Candidate:

    def __init__(self, conn: sqlite3.Connection, task: str, org: str | None, budget: _Budget):
        _check(conn.in_transaction and type(task) is str and bool(task))
        self.conn, self.task, self.org, self.budget = (conn, task, org, budget)
        actual, native, objects = _candidate_structure(conn, budget)
        self.objects = objects
        names = set((row[1] for row in actual[0]))
        self.coding = any((name.casefold().startswith('workflow_coding_') or name.casefold() == 'workflow_coding' for row in objects for name in row[1:3]))
        self.layout = 'I' if 'workflow_document_revision_operations' in names else 'H' if 'workflow_author_handoffs' in names else 'G' if 'workflow_submission_schema_versions' in names else 'E' if 'workflow_draft_adapter_versions' in names else 'F'
        if self.coding and self.layout == 'I':
            self.layout = 'J'
        self.workflow = bool(actual[0]) or self.coding
        self.indexed = any((name.startswith('workflow_owner_') for name in names))
        if self.workflow:
            _check(org is not None and type(org) is str and bool(org))
            expected = _candidate_reference(self.layout, coding_extension=self.coding, owner_indexed=self.indexed, budget=budget)
            _check(actual == expected)
            if not self.indexed:
                raise _OwnerIndexesRequired('workflow_owner_indexes_required')
        else:
            expected = ((), (), ())
        self.metadata = dict(((row[0], row[1]) for row in expected[1]))
        self.indexes = {}
        for table, index, detail in expected[2]:
            self.indexes.setdefault(table, []).append((index, detail))
        self.foreign = dict(((row[0], row[2]) for row in expected[1]))
        self.metadata.update(dict(((table, row[0]) for table, row in native.items())))
        self.foreign.update(dict(((table, row[1]) for table, row in native.items())))
        for table, row in native.items():
            self.indexes[table] = list(((index, detail) for _, index, detail in row[2]))
        self._native_schema(native)
        self.acquired: dict[tuple[str, int], dict] = {}
        self.ranges: dict[tuple, tuple[dict, ...]] = {}
        self.local: dict[str, dict[int, dict]] = {}

    def _native_schema(self, native: dict) -> None:
        for table, columns, types, pk in (('tasks', _TASK_COLUMNS, _TASK_TYPES, 'id'), ('task_results', _RESULT_COLUMNS, _RESULT_TYPES, 'id'), ('audit_log', _AUDIT_COLUMNS, _AUDIT_TYPES, 'id')):
            _check(table in native and (not native[table][1]))
            matches = list((row for row in self.objects if row[1].casefold() == table))
            _check(len(matches) == 1 and matches[0][:2] == ('table', table))
            metadata = dict(((row[1], row) for row in native[table][0]))
            _check(set(metadata) == set(columns) and list((r[1] for r in metadata.values() if r[5])) == [pk])
            for name, allowed in zip(columns, types, strict=True):
                row = metadata[name]
                _check(row[2].upper() == next((t.upper() for t in allowed if t != 'null')) and row[6] == 0 and ('null' in allowed or name == pk or row[3] == 1))
            if table != 'tasks':
                _check(metadata[pk][2] == 'INTEGER' and 'AUTOINCREMENT' in matches[0][3].upper())
        required = {'tasks': ('idx_tasks_parent', ('parent_task_id',)), 'task_results': ('idx_cleanup_results_task_agent_id', ('task_id', 'agent', 'id')), 'audit_log': ('idx_audit_log_task_id', ('task_id',))}
        for table, (name, keys) in required.items():
            matches = list(((i, d) for i, d in self.indexes[table] if i[1] == name))
            _check(len(matches) == 1)
            index, detail = matches[0]
            key_rows = list((r for r in detail if r[5]))
            _check(tuple((r[2] for r in key_rows)) == keys and index[2:] == (0, 'c', 0) and all((r[4] == 'BINARY' and r[3] == int(table == 'task_results' and r[2] == 'id') for r in key_rows)))
        pause_names = list((row for row in self.objects if any((name.casefold().startswith('task_pause_') for name in row[1:3]))))
        if pause_names:
            from runtime.infrastructure.task_pause_controls import PAUSE_SCHEMA_SQL
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
            normalize = lambda rows: sorted(((k, n, t, None if s is None else ' '.join(s.split())) for k, n, t, s in rows))
            _check(normalize(pause_names) == normalize(expected_objects) and native['task_pause_controls'] == expected_native['task_pause_controls'])

    def _access(self, table: str, values: dict) -> tuple[str, str, tuple[str, ...]]:
        metadata = self.metadata[table]
        primary = list((r for r in metadata if r[5]))
        if len(primary) == 1 and primary[0][2] == 'INTEGER' and (primary[0][1] in values):
            return ('', '', (primary[0][1],))
        candidates = []
        for index, detail in self.indexes[table]:
            keys = list((r for r in detail if r[5]))
            if index[4] and index[1] not in ('workflow_draft_predecessor_idx', 'workflow_draft_result_idx'):
                continue
            prefix = []
            for key in keys:
                if key[2] not in values or key[4] != 'BINARY':
                    break
                prefix.append(key[2])
            if prefix:
                candidates.append((len(prefix), index[1], tuple(prefix), prefix[0] if index[4] else None))
        _check(bool(candidates))
        _, name, prefix, nonnull = max(candidates)
        self.reserve(128 + 32 * (len(name) + len(nonnull or '')))
        return (' INDEXED BY ' + _quote(name), f' AND {_quote(nonnull)} IS NOT NULL' if nonnull else '', prefix)

    def rows(self, table: str, column: str, value: object) -> tuple[dict, ...]:
        return self.select(table, **{column: value})

    def select(self, table: str, **values: object) -> tuple[dict, ...]:
        """Close one reference-authenticated leading-key range in this snapshot.

        This is a private production acquisition primitive, not a SQL adapter or
        a replacement connection. Every caller supplies literal relation keys.
        """
        if table not in self.metadata:
            return []
        _check(bool(values) and all((type(v) in (str, int) for v in values.values())))
        hint, extra, prefix = self._access(table, values)
        if set(values) != set(prefix):
            rows = self.select(table, **dict(((name, values[name]) for name in prefix)))
            found = []
            for row in rows:
                self.budget.step()
                if all((type(row[name]) is type(value) and row[name] == value for name, value in values.items())):
                    self.reserve(8)
                    found.append(row)
            return found
        key = (table, tuple(values.items()))
        if key in self.ranges:
            return self.ranges[key]
        self.budget.step()
        columns = tuple((r[1] for r in self.metadata[table]))
        _check(all((k in columns for k in values)))
        quote = _quote
        self.reserve(128)
        for c in columns:
            self.reserve(128 + 32 * len(c))
        quoted_columns = tuple((quote(c) for c in columns))
        self.reserve(128)
        for c in quoted_columns:
            self.reserve(128 + 8 * (40 + 2 * len(c)))
        scalar = ','.join(('rowid', *(f'typeof({c}),length(CAST({c} AS BLOB))' for c in quoted_columns)))
        self.reserve(128)
        for k in values:
            self.reserve(128 + 32 * len(k))
        quoted_keys = tuple((quote(k) for k in values))
        self.reserve(128)
        for k in quoted_keys:
            self.reserve(128 + 8 * (24 + len(k)))
        where = ' AND '.join((f'{k}=? COLLATE BINARY' for k in quoted_keys))
        self.reserve(128 + 32 * len(table))
        quoted_table = quote(table)
        cursor = self.conn.cursor()
        cursor.row_factory = None
        found = []
        primary = None
        try:
            self.reserve(128 + 4 * (len(scalar) + len(quoted_table) + len(hint) + len(where) + len(extra) + 32))
            cursor.execute(f'SELECT {scalar} FROM {quoted_table}{hint} WHERE {where}{extra}', tuple(values.values()))
            while True:
                self.budget.step()
                self.budget.candidate()
                self.reserve(256 * (2 * len(columns) + 1))
                row = cursor.fetchone()
                if row is None:
                    break
                rowid, cells = (row[0], row[1:])
                types, sizes = (cells[::2], cells[1::2])
                _check(type(rowid) is int and all((t in ('text', 'blob', 'integer', 'real', 'null') for t in types)))
                _check(all((s is None or (type(s) is int and 0 <= s <= _VALUE_LIMIT) for s in sizes)))
                cost = sum((s or 0 for t, s in zip(types, sizes, strict=True) if t in ('text', 'blob')))
                self.reserve(cost * 4 + 256 * len(columns) + 256)
                self.budget.candidate()
                self.budget.step()
                self.reserve(128)
                for c in quoted_columns:
                    self.reserve(128 + 8 * (96 + 3 * len(c)))
                projection = ','.join((f"CASE WHEN typeof({c})='text' THEN CAST({c} AS BLOB) ELSE {c} END" for c in quoted_columns))
                payload_cursor = self.conn.cursor()
                payload_cursor.row_factory = None
                payload_failure = None
                try:
                    self.reserve(128 + 4 * (len(projection) + len(quoted_table) + 32))
                    payload_cursor.execute(f'SELECT {projection} FROM {quoted_table} WHERE rowid=?', (rowid,))
                    payload = payload_cursor.fetchone()
                    _check(payload is not None)
                    self.budget.step()
                    self.budget.candidate()
                    _check(payload_cursor.fetchone() is None)
                    decoded = {}
                    storage_types = {'TEXT': 'text', 'BLOB': 'blob', 'INTEGER': 'integer', 'REAL': 'real'}
                    native_types = {'text': bytes, 'blob': bytes, 'integer': int, 'real': float, 'null': type(None)}
                    for definition, cell, storage, size in zip(self.metadata[table], payload, types, sizes, strict=True):
                        self.budget.step()
                        expected = storage_types[definition[2].upper()]
                        _check(storage == expected or (storage == 'null' and (not definition[3]) and (not definition[5])))
                        _check(type(cell) is native_types[storage])
                        if storage in ('text', 'blob'):
                            _check(len(cell) == size)
                        if storage == 'real':
                            _check(math.isfinite(cell))
                        decoded[definition[1]] = cell.decode('utf8') if storage == 'text' else cell
                    _check(all((type(decoded[k]) is type(v) and decoded[k] == v for k, v in values.items())))
                    found.append(decoded)
                    self.acquired[table, rowid] = decoded
                    self.local.setdefault(table, {})[rowid] = decoded
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
        self.ranges[key] = found
        return self.ranges[key]

    def reserve(self, size: int) -> None:
        _candidate_reserve(self.budget, size)

    def ordered(self, rows, *columns: str) -> list[dict]:
        _check(type(rows) in (list, tuple))
        self.reserve(16 * len(rows) * (len(columns) + 1))
        for _ in range(len(rows) * max(1, len(rows).bit_length())):
            self.budget.step()
        return sorted(rows, key=lambda row: tuple((row[k] for k in columns)))

    def one(self, table: str, key: object, column: str='id') -> dict:
        rows = self.rows(table, column, key)
        _check(len(rows) == 1)
        return rows[0]

    def json(self, raw: bytes, digest: str | None=None) -> object:
        _check(type(raw) is bytes and 0 < len(raw) <= _VALUE_LIMIT)
        self.reserve(6 * len(raw))

        def pairs(items):
            result = {}
            for key, value in items:
                self.budget.step()
                _check(key not in result)
                result[key] = value
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
                    raise _CandidateBudgetExhausted('workflow_owner_json_depth')
            elif byte in (125, 93):
                depth -= 1
                _check(depth >= 0)
            elif byte in (44, 58):
                nodes += 1
        self.reserve(32 * nodes)
        for _ in range(nodes):
            self.budget.step()
        _check(depth == 0 and (not quoted) and (not escaped))
        try:
            value = json.loads(raw.decode('utf8'), object_pairs_hook=pairs, parse_constant=lambda _: _check(False))
        except RecursionError as exc:
            raise _CandidateBudgetExhausted('workflow_owner_json_depth') from exc
        canonical = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':')).encode('utf8')
        _check(canonical == raw and (digest is None or hashlib.sha256(raw).hexdigest() == digest))
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
        _check(depth == 0 and (not quoted) and (not escaped))
        return parse_native_decision(raw)

    def metadata_check(self, row: dict, table: str) -> None:
        from runtime.infrastructure.workflow_coding_schema import _coding_scalar, _refuse_coding_metadata
        for key, value in row.items():
            self.budget.step()
            _check(not _coding_scalar(key, value))
            if key.endswith(('_bytes', '_json')) or key in ('payload', 'author_principal', 'published_by'):
                if key not in ('submission_bytes', 'candidate_bytes'):
                    if type(value) in (str, bytes):
                        self.reserve(6 * (len(value) if type(value) is bytes else 4 * len(value)))
                    _refuse_coding_metadata(value, location=f'{table}.{key}', budget=self.budget)

    def native(self, task: str) -> tuple[dict, tuple[dict, ...]]:
        row = self.one('tasks', task)
        for key in ('active_chain', 'active_fanout', 'blocked_on_job_ids'):
            self.metadata_check({key: row[key]}, 'tasks')
        results = self.rows('task_results', 'task_id', task)
        for result in results:
            raw = result['decision_json']
            if raw is not None:
                try:
                    decision = self.native_decision(raw.encode('utf8'))
                except _CandidateBudgetExhausted:
                    raise
                except (ValueError, UnicodeError):
                    self.metadata_check({'decision_json': raw}, 'task_results')
                else:
                    if type(decision) is dict and decision.get('action') == 'workflow_start':
                        _check(result['status'] == 'completed' and result['waiting_on_job_ids'] is None)
                        audits = self.rows('audit_log', 'task_id', task)
                        matching = list((a for a in audits if a['action'] == 'session_start' and type(a['payload']) is str and (json.loads(a['payload']).get('session_id') == result['session_id'])))
                        _check(len(matching) == 1 and matching[0]['agent'] == result['agent'])
                        continue
                    self.metadata_check({'decision_json': raw}, 'task_results')
            self.metadata_check(dict(((k, result[k]) for k in ('risks_flagged', 'waiting_on_job_ids', 'local_ci'))), 'task_results')
        for audit in self.rows('audit_log', 'task_id', task):
            self.metadata_check(audit, 'audit_log')
        return (row, results)

    def ancestry(self) -> None:
        from runtime.infrastructure.task_pause_controls import decode_journal
        seen, current = (set(), self.task)
        while current is not None:
            self.budget.step()
            _check(current not in seen and len(seen) < 20)
            seen.add(current)
            row = self.one('tasks', current)
            parent = row['parent_task_id']
            if parent is None:
                if 'task_pause_controls' in self.metadata:
                    for pause in self.rows('task_pause_controls', 'root_task_id', current):
                        self.metadata_check(pause, 'task_pause_controls')
                        self.reserve(24 * len(pause['admission_journal_json']))
                        journal = decode_journal(pause['admission_journal_json'])
                        for entry in journal['entries']:
                            self.budget.step()
                            _check(entry['root_task_id'] == current and entry['org'] == self.org)
                            child, chain = (entry['task_id'], set())
                            while child != current:
                                self.budget.step()
                                _check(child not in chain and len(chain) < 20)
                                chain.add(child)
                                child = self.one('tasks', child)['parent_task_id']
                                _check(child is not None)
                return
            current = parent

    def entry(self) -> tuple[bool, set[str]]:
        """Every scalar task/result/source entry; no current/state shortcuts."""
        task = self.one('tasks', self.task)
        results = self.rows('task_results', 'task_id', self.task)
        instances = set()
        owner = False
        for table in ('workflow_instances', 'workflow_coding_attempts', 'workflow_draft_dispatch_intents', 'workflow_request_task_bridges', 'workflow_instance_tasks', 'workflow_task_results', 'workflow_current_assignments', 'workflow_receipt_evidence', 'workflow_submissions', 'workflow_submission_contributors', 'workflow_instance_contributors', 'workflow_dispatch_callbacks', 'workflow_dispatch_effects', 'workflow_submission_operations', 'workflow_submission_result_links', 'workflow_author_handoffs'):
            column = 'root_task_id' if table == 'workflow_instances' else 'source_task_id' if table in ('workflow_submissions', 'workflow_submission_contributors', 'workflow_instance_contributors', 'workflow_submission_operations', 'workflow_submission_result_links', 'workflow_author_handoffs') else 'task_id'
            for row in self.rows(table, column, self.task):
                if table in ('workflow_instances', 'workflow_coding_attempts', 'workflow_draft_dispatch_intents', 'workflow_request_task_bridges'):
                    owner = True
                if table == 'workflow_instances':
                    instances.add(row['id'])
                elif 'instance_id' in row:
                    instances.add(row['instance_id'])
        for source in (self.task, *(str(r['id']) for r in results)):
            for context in self.rows('workflow_contexts', 'source_id', source):
                if context['source_kind'] == 'coding-manager-result':
                    owner = True
                for row in self.rows('workflow_instances', 'context_id', context['id']):
                    instances.add(row['id'])
                _check(self.rows('workflow_instances', 'context_id', context['id']))
        for result in results:
            number, literal = (result['id'], str(result['id']))
            for table, column, value in (('workflow_task_results', 'id', literal), ('workflow_request_task_bridges', 'result_id', literal), ('workflow_dispatch_callbacks', 'result_id', literal), ('workflow_draft_dispatch_intents', 'final_result_id', number), ('workflow_draft_dispatch_events', 'result_id', number), ('workflow_submission_result_links', 'task_result_id', number), ('workflow_coding_attempts', 'result_id', number), *((t, 'source_result_id', literal) for t in ('workflow_submissions', 'workflow_submission_contributors', 'workflow_instance_contributors'))):
                for row in self.rows(table, column, value):
                    task_column = 'source_task_id' if 'source_task_id' in row else 'task_id'
                    if task_column in row:
                        _check(row[task_column] == self.task)
                    if 'instance_id' in row:
                        instances.add(row['instance_id'])
                    if table in ('workflow_request_task_bridges', 'workflow_dispatch_callbacks', 'workflow_draft_dispatch_intents', 'workflow_draft_dispatch_events', 'workflow_submission_result_links', 'workflow_coding_attempts'):
                        owner = True
        for (table, _), row in list(self.acquired.items()):
            self.budget.step()
            if table == 'workflow_receipt_evidence':
                receipt = self.one('workflow_review_receipts', row['receipt_id'])
                request = self.one('workflow_review_requests', receipt['request_id'])
                instances.add(self.one('workflow_rounds', request['round_id'])['instance_id'])
            elif table in ('workflow_dispatch_callbacks', 'workflow_dispatch_effects'):
                outbox = self.one('workflow_dispatch_outbox', row['outbox_id'])
                operation = self.one('workflow_dispatch_operations', outbox['operation_id'])
                instances.add(operation['instance_id'])
                owner = True
            elif table == 'workflow_draft_dispatch_events':
                instances.add(self.one('workflow_draft_dispatch_intents', row['intent_id'])['instance_id'])
                owner = True
            elif table in ('workflow_submission_contributors', 'workflow_submission_result_links'):
                instances.add(self.one('workflow_submissions', row['submission_id'])['instance_id'])
        if not owner:
            self.native(self.task)
            self.ancestry()
        return (owner, instances)

    def canonical(self, value: object) -> bytes:

        def size(item):
            self.budget.step()
            if type(item) is str:
                self.reserve(6 * len(item) + 2)
            elif type(item) in (dict, list, tuple):
                self.reserve(2 + 2 * len(item))
                if type(item) is dict:
                    for key, member in item.items():
                        size(key)
                        size(member)
                else:
                    for member in item:
                        size(member)
            else:
                _check(item is None or type(item) in (bool, int, float))
                self.reserve(32)
        size(value)
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':')).encode('utf8')

    def hex(self, raw: bytes) -> str:
        _check(type(raw) is bytes)
        self.reserve(2 * len(raw))
        self.budget.step()
        return raw.hex()

    def unhex(self, value: str) -> bytes:
        _check(type(value) is str)
        self.reserve(len(value))
        self.budget.step()
        return bytes.fromhex(value)

    def unbase64(self, value: str) -> bytes:
        import base64
        _check(type(value) is str)
        self.reserve(2 * len(value))
        self.budget.step()
        return base64.b64decode(value, validate=True)

    def union(self, table: str, *groups) -> list[dict]:
        keys = list((r[1] for r in self.metadata[table] if r[5]))
        seen = {}
        from runtime.workflows.review_dispatch import _same_value
        for rows in groups:
            for row in rows:
                self.budget.step()
                identity = tuple((row[k] for k in keys))
                if identity in seen:
                    _check(_same_value(seen[identity], row))
                else:
                    self.reserve(16 * (1 + len(keys)))
                    seen[identity] = row
        return list(seen.values())

    def round_receipts(self, round_id: str) -> list[dict]:
        return list((p for q in self.rows('workflow_review_requests', 'round_id', round_id) for p in self.rows('workflow_review_receipts', 'request_id', q['id'])))

    def request_evidence(self, request_id: str) -> list[dict]:
        return list((e for p in self.rows('workflow_review_receipts', 'request_id', request_id) for e in self.rows('workflow_receipt_evidence', 'receipt_id', p['id'])))

    def instance_links(self, instance: str) -> list[dict]:
        return list((link for s in self.rows('workflow_submissions', 'instance_id', instance) for link in self.rows('workflow_submission_result_links', 'submission_id', s['id'])))

    def revision_links(self, instance: str) -> list[dict]:
        return list((link for s in self.select('workflow_submissions', instance_id=instance, revision=2) for link in self.rows('workflow_submission_result_links', 'submission_id', s['id'])))

    def subject_rounds(self, instance: str) -> list[dict]:
        result = []
        for row in self.rows('workflow_rounds', 'instance_id', instance):
            subject = self.one('workflow_submissions', row['submission_id'])
            self.reserve(32 * len(row))
            result.append(dict(row, subject_instance=subject['instance_id'], subject_revision=subject['revision']))
        return result

    def orphan_draft_events(self, instance: str) -> list[dict]:
        result = []
        for row in list(self.local.get('workflow_draft_dispatch_events', {}).values()):
            self.budget.step()
            intent = self.one('workflow_draft_dispatch_intents', row['intent_id'])
            _check(type(intent['instance_id']) is str)
        return result

    def template(self, *, identity_id: str, version: int, expected_version_id: str | None=None):
        from runtime.workflows.templates import WorkflowTemplateVersion, _validate_definition_pins
        identity = self.one('workflow_template_identities', identity_id)
        mapping = self.select('workflow_template_identity_versions', template_identity_id=identity_id, version=version)
        _check(len(mapping) == 1)
        row = self.one('workflow_template_versions', mapping[0]['template_version_id'])
        _check(row['namespace'] != 'release/happyranch' and row['namespace'] == identity['namespace'] and (row['template_name'] == identity['template_name']) and (type(version) is int) and (row['version'] == version) and (mapping[0]['content_digest'] == row['definition_digest']) and (expected_version_id is None or row['id'] == expected_version_id))
        value = self.json(row['definition_bytes'], row['definition_digest'])
        self.reserve(8 * len(row['definition_bytes']))
        _check(_validate_definition_pins(value, compiler_pin=row['compiler_pin'], validator_pin=row['validator_pin'], source_pin=row['source_pin']) == row['definition_bytes'])
        publisher = self.json(row['published_by'].encode('utf8'))
        _check(type(publisher) is dict)
        return WorkflowTemplateVersion(identity_id=identity_id, version_id=row['id'], namespace=row['namespace'], template_name=row['template_name'], version=version, definition_bytes=row['definition_bytes'], definition_digest=row['definition_digest'], compiler_pin=row['compiler_pin'], validator_pin=row['validator_pin'], source_pin=row['source_pin'], publisher=publisher, published_at=row['published_at'])

    def publication(self, namespace: str, generation: int, snapshot_digest: str) -> list[dict]:
        states = ('prepared', 'file_phase_reserved', 'canonical_published', 'forward_recovery_required', 'pointer_committed', 'cache_installed', 'aborted')
        return list((row for state in states for row in self.select('workflow_publication_journals', namespace=namespace, state=state, generation=generation) if row['snapshot_digest'] == snapshot_digest))

    def stage_keys(self, *, org_slug: str, principal: str, operation_key: str) -> list[dict]:
        """Compare every REACHED K; unassociated foreign key absence is omitted."""
        result = []
        for row in list(self.local.get('workflow_dispatch_events', {}).values()):
            self.budget.step()
            operation = self.one('workflow_dispatch_operations', row['operation_id'])
            if row['event_kind'] == 'review_staged' and operation['org_slug'] == org_slug and (operation['principal'] == principal):
                body = self.json(row['event_bytes'], row['event_digest'])
                if body['stage']['command']['operation_key'] == operation_key:
                    result.append(row)
        return result

    def human_keys(self, *, org_slug: str, operation_key: str) -> list[dict]:
        """Compare every REACHED L in its distinct org/human:founder domain."""
        result = []
        for row in list(self.local.get('workflow_review_receipts', {}).values()):
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
                _check(proof['format'] == f'workflow-human-review-proof@{review_family(scope)}' and proof['operation_principal'] == 'human:founder')
                if proof['command']['operation_key'] == operation_key:
                    result.append(row)
        return result

    def handoff(self, raw: bytes, digest: str | None=None) -> dict:
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
        grouped = {}
        for fk in self.foreign.get(table, ()):
            grouped.setdefault(fk[0], []).append(fk)
        for foreign in grouped.values():
            self.budget.step()
            if any((row[fk[3]] is None for fk in foreign)):
                continue
            target = foreign[0][2]
            parent = self.select(target, **dict(((fk[4], row[fk[3]]) for fk in foreign)))
            _check(len(parent) == 1)

    def result_aliases(self, literal: str) -> None:
        self.rows('workflow_task_results', 'id', literal)
        for table, column in (('workflow_contexts', 'source_id'), ('workflow_request_task_bridges', 'result_id'), ('workflow_dispatch_callbacks', 'result_id'), ('workflow_submissions', 'source_result_id'), ('workflow_submission_contributors', 'source_result_id'), ('workflow_instance_contributors', 'source_result_id'), ('workflow_receipt_evidence', 'result_id'), ('workflow_current_assignments', 'result_id')):
            self.rows(table, column, literal)
        identities = []
        for row in self.rows('workflow_task_results', 'id', literal):
            identities.append((row['task_id'], row['session_id'], row['principal']))
        for table in ('workflow_submissions', 'workflow_submission_contributors', 'workflow_instance_contributors'):
            for row in self.rows(table, 'source_result_id', literal):
                principal = row['author_principal'] if table == 'workflow_submissions' else row['principal']
                identities.append((row['source_task_id'], row['source_session_id'], principal))
        for native in self.rows('task_results', 'task_id', self.task):
            if str(native['id']) == literal:
                _check(native['status'] == 'completed')
                identities.append((native['task_id'], native['session_id'], native['agent']))
        if identities:
            _check(all((value == identities[0] for value in identities)))

    def close_instances(self, instances: set[str]) -> None:
        """Directed producer closure; shared authority/template do not fan out."""
        bindings = {}
        for instance_id in sorted(instances):
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
            for table in ('workflow_instance_tasks', 'workflow_task_results', 'workflow_current_assignments', 'workflow_instance_contributors', 'workflow_submissions', 'workflow_rounds', 'workflow_events', 'workflow_operation_replays', 'workflow_activations', 'workflow_active_activations', 'workflow_dispatch_operations', 'workflow_request_task_bridges', 'workflow_draft_dispatch_intents', 'workflow_author_handoffs', 'workflow_document_revision_operations'):
                self.rows(table, 'instance_id', instance_id)
            _check(not self.rows('workflow_coding_runs', 'instance_id', instance_id))
            _check(not self.rows('workflow_coding_attempts', 'instance_id', instance_id))
            _check(not self.rows('workflow_coding_events', 'instance_id', instance_id))
            _check(binding['id'] not in bindings)
            bindings[binding['id']] = instance_id
        seen = set()
        while True:
            pending = []
            self.reserve(64)
            for table, rows in self.local.items():
                self.budget.step()
                for key, row in rows.items():
                    self.budget.step()
                    if (table, key) not in seen:
                        self.reserve(128)
                        pending.append((table, key, row))
            if not pending:
                break
            for table, key, row in pending:
                self.budget.step()
                seen.add((table, key))
                if table.startswith('workflow_'):
                    self.parents(table, row)
                    self.metadata_check(row, table)
                if 'instance_id' in row:
                    _check(row['instance_id'] in instances)
                if table == 'workflow_contexts':
                    _check(row['binding_snapshot_id'] in bindings)
                    for alias in self.rows('workflow_instances', 'context_id', row['id']):
                        _check(alias['id'] == bindings[row['binding_snapshot_id']])
                    for target, column in (('workflow_receipt_evidence', 'context_id'), ('workflow_draft_dispatch_intents', 'context_id'), ('workflow_document_revision_operations', 'context_id')):
                        self.rows(target, column, row['id'])
                elif table == 'workflow_binding_snapshots':
                    _check(row['id'] in bindings)
                    for target in ('workflow_receipt_evidence', 'workflow_draft_dispatch_intents', 'workflow_document_revision_operations'):
                        self.rows(target, 'binding_snapshot_id', row['id'])
                elif table == 'workflow_activations':
                    version = self.one('workflow_template_versions', row['template_version_id'])
                    mapping = self.rows('workflow_template_identity_versions', 'template_version_id', row['template_version_id'])
                    _check(len(mapping) == 1 and mapping[0]['template_identity_id'] == row['template_identity_id'] and version['namespace'] == row['authority_namespace'])
                    self.rows('workflow_activation_operations', 'activation_id', row['id'])
                    self.rows('workflow_active_activations', 'activation_id', row['id'])
                    self.rows('workflow_draft_dispatch_intents', 'activation_id', row['id'])
                    self.rows('workflow_document_revision_operations', 'activation_id', row['id'])
                elif table == 'workflow_active_activations':
                    activation = self.one('workflow_activations', row['activation_id'])
                    _check(activation['instance_id'] == row['instance_id'] and activation['activation_revision'] == row['activation_revision'] and activation['state'] == 'active')
                elif table == 'workflow_submissions':
                    for target, column in (('workflow_submission_contributors', 'submission_id'), ('workflow_rounds', 'submission_id'), ('workflow_review_receipts', 'submission_id'), ('workflow_submission_operations', 'submission_id'), ('workflow_submission_result_links', 'submission_id'), ('workflow_author_handoffs', 'submission_id'), ('workflow_document_revision_operations', 'predecessor_submission_id'), ('workflow_coding_attempts', 'submission_id')):
                        self.rows(target, column, row['id'])
                    if row['source_result_id'] is not None:
                        self.result_aliases(row['source_result_id'])
                elif table == 'workflow_rounds':
                    self.rows('workflow_review_requests', 'round_id', row['id'])
                    self.rows('workflow_document_revision_operations', 'predecessor_round_id', row['id'])
                elif table == 'workflow_review_requests':
                    for target, column in (('workflow_review_receipts', 'request_id'), ('workflow_review_requests', 'supersedes_request_id'), ('workflow_dispatch_operations', 'request_id'), ('workflow_request_task_bridges', 'request_id'), ('workflow_dispatch_outbox', 'request_id'), ('workflow_coding_attempts', 'review_request_id')):
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
                    for target, column in (('workflow_request_task_bridges', 'operation_id'), ('workflow_dispatch_outbox', 'operation_id'), ('workflow_dispatch_events', 'operation_id')):
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
                    _check(not self.rows('tasks', 'parent_task_id', row['task_id']))
        for row in self.local.get('workflow_coding_attempts', {}).values():
            self.budget.step()
            _check(False)

    def legacy_instance(self, instance_id: str) -> None:
        """Literal old F/F2 relations; no invented modern callback or receipt."""
        from runtime.workflows.templates import _validate_definition_pins
        instance = self.one('workflow_instances', instance_id)
        binding = self.one('workflow_binding_snapshots', instance['binding_snapshot_id'])
        context = self.one('workflow_contexts', instance['context_id'])
        grant = self.one('workflow_authorization_revisions', binding['authorization_revision_id'])
        version = self.one('workflow_template_versions', binding['template_version_id'])
        draft = self.one('workflow_template_drafts', version['draft_id'])
        for row, raw, dg in ((binding, 'binding_bytes', 'binding_digest'), (context, 'context_bytes', 'context_digest'), (grant, 'authority_bytes', 'authority_digest'), (version, 'definition_bytes', 'definition_digest'), (draft, 'definition_bytes', 'definition_digest')):
            value = self.json(row[raw], row[dg])
            if raw == 'definition_bytes':
                self.reserve(8 * len(row[raw]))
                _check(_validate_definition_pins(value, compiler_pin=row['compiler_pin'], validator_pin=row['validator_pin'], source_pin=row['source_pin']) == row[raw])
        for name in ('namespace', 'template_name', 'definition_bytes', 'definition_digest', 'compiler_pin', 'validator_pin', 'source_pin'):
            self.budget.step()
            _check(version[name] == draft[name])
        _check(context['binding_snapshot_id'] == binding['id'])
        if version['compiler_pin'] in ('workflow-compiler@3', 'workflow-compiler@4'):
            _check(not self.rows('workflow_submissions', 'instance_id', instance_id))
        if context['source_kind'] == 'founder-activation':
            _check(bool(self.rows('workflow_draft_dispatch_intents', 'instance_id', instance_id)))
        _check(context['source_kind'] in ('task', 'founder-activation'))
        for alias in self.rows('workflow_contexts', 'binding_snapshot_id', binding['id']):
            _check(alias['id'] == context['id'])
        for task in self.rows('workflow_instance_tasks', 'instance_id', instance_id):
            self.budget.step()
            _check(task['state'] == 'completed' and type(task['generation']) is int and (task['generation'] > 0) and all((type(task[k]) is str and bool(task[k]) for k in ('task_id', 'session_id', 'role_key'))))
        for result in self.rows('workflow_task_results', 'instance_id', instance_id):
            self.budget.step()
            self.archival(result['result_bytes'])
            _check(result['lifecycle'] == 'completed' and hashlib.sha256(result['result_bytes']).hexdigest() == result['result_digest'])
            tasks = self.select('workflow_instance_tasks', instance_id=instance_id, task_id=result['task_id'], session_id=result['session_id'])
            _check(len(tasks) == 1 and tasks[0]['generation'] == result['generation'])
        contributors = set((r['principal'] for r in self.rows('workflow_instance_contributors', 'instance_id', instance_id)))
        for assignment in self.rows('workflow_current_assignments', 'instance_id', instance_id):
            result = self.one('workflow_task_results', assignment['result_id'])
            task = self.select('workflow_instance_tasks', instance_id=instance_id, task_id=assignment['task_id'], session_id=assignment['session_id'])
            _check(len(task) == 1 and task[0]['role_key'] == assignment['role_key'])
            if assignment['role_key'].startswith('finalizer:'):
                _check(assignment['role_key'] == 'finalizer:' + assignment['principal'] and assignment['principal'] not in contributors)
            _check(all((assignment[k] == result[k] for k in ('instance_id', 'principal', 'task_id', 'session_id', 'generation', 'lifecycle'))))
        for submission in self.rows('workflow_submissions', 'instance_id', instance_id):
            _check((submission['source_result_id'] is not None or self.rows('workflow_submission_operations', 'submission_id', submission['id'])) and hashlib.sha256(submission['submission_bytes']).hexdigest() == submission['submission_digest'])
            for contributor in self.rows('workflow_submission_contributors', 'submission_id', submission['id']):
                candidates = self.select('workflow_instance_contributors', instance_id=instance_id, **dict(((k, contributor[k]) for k in ('principal', 'source_task_id', 'source_session_id', 'source_result_id'))))
                _check(len(candidates) == 1 and candidates[0]['contribution_kind'] == contributor['contribution_kind'])
        for round_ in self.rows('workflow_rounds', 'instance_id', instance_id):
            submission = self.one('workflow_submissions', round_['submission_id'])
            _check(submission['instance_id'] == instance_id and submission['revision'] == round_['current_revision'])
            for request in self.rows('workflow_review_requests', 'round_id', round_['id']):
                self.archival(request['request_scope_bytes'])
                _check(hashlib.sha256(request['request_scope_bytes']).hexdigest() == request['request_scope_digest'] and request['principal'] not in contributors)
                self.supersession('workflow_review_requests', request, 'supersedes_request_id')
                receipts = self.rows('workflow_review_receipts', 'request_id', request['id'])
                _check(request['status'] == 'pending' and (not receipts) or (request['status'] in ('approved', 'changes_requested', 'superseded') and len(receipts) == 1))
                for receipt in receipts:
                    self.archival(receipt['proof_bytes'])
                    self.supersession('workflow_review_receipts', receipt, 'supersedes_receipt_id')
                    _check(receipt['submission_id'] == submission['id'] and receipt['submission_digest'] == submission['submission_digest'] and (receipt['assignment_generation'] == request['assignment_generation']) and (receipt['request_scope_digest'] == request['request_scope_digest']) and (request['status'] == 'superseded' or request['status'] == receipt['outcome']) and (hashlib.sha256(receipt['proof_bytes']).hexdigest() == receipt['proof_digest']))
                    evidence = self.one('workflow_receipt_evidence', receipt['id'], 'receipt_id')
                    result = self.one('workflow_task_results', evidence['result_id'])
                    assignments = self.select('workflow_current_assignments', instance_id=instance_id, role_key='reviewer:' + request['principal'])
                    _check(len(assignments) == 1)
                    assignment = assignments[0]
                    _check(evidence['signer_principal'] == request['principal'] == result['principal'] == assignment['principal'])
                    _check(evidence['context_id'] == context['id'] and evidence['binding_snapshot_id'] == binding['id'] and (evidence['round_revision'] == round_['current_revision']))
                    _check(all((evidence[k] == assignment[k] for k in ('task_id', 'session_id', 'result_id'))) and all((evidence[k] == result[k] for k in ('task_id', 'session_id'))) and (result['generation'] == receipt['assignment_generation']))
                    _check(evidence['proof_bytes'] == receipt['proof_bytes'] == result['result_bytes'] and evidence['proof_digest'] == receipt['proof_digest'] == result['result_digest'])
        for event in self.rows('workflow_events', 'instance_id', instance_id):
            revision = _local_event_revision(self, event)
            if 'revision' in event:
                _check(event['revision'] == revision)
        for replay in self.rows('workflow_operation_replays', 'instance_id', instance_id):
            event = self.one('workflow_events', replay['effect_id'])
            _check(event['instance_id'] == instance_id and replay['org_slug'] == self.org and (replay['request_digest'] == hashlib.sha256(event['event_bytes']).hexdigest()))

    def archival(self, raw: bytes) -> None:
        from runtime.infrastructure.workflow_coding_schema import _metadata_string
        _check(type(raw) is bytes and bool(raw))
        self.reserve(6 * len(raw))
        text = raw.decode(json.detect_encoding(raw), errors='replace')
        index = 0
        prefixes = ('workflow-agent-review-proof@', 'workflow-human-review-proof@', 'workflow-review-request@', 'workflow-review-stage@')
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
                _check(not any((value.startswith(prefix) or (not complete and prefix.startswith(value)) for prefix in prefixes if value)))

    def supersession(self, table: str, row: dict, column: str) -> None:
        seen = set()
        while row[column] is not None:
            self.budget.step()
            _check(row['id'] not in seen)
            seen.add(row['id'])
            prior = self.one(table, row[column])
            _check(prior['assignment_generation'] < row['assignment_generation'])
            if table == 'workflow_review_requests':
                old_round = self.one('workflow_rounds', prior['round_id'])
                new_round = self.one('workflow_rounds', row['round_id'])
                _check(prior['principal'] == row['principal'] and prior['status'] == 'superseded' and (old_round['instance_id'] == new_round['instance_id']) and (old_round['current_revision'] <= new_round['current_revision']))
            else:
                old = self.one('workflow_review_requests', prior['request_id'])
                new = self.one('workflow_review_requests', row['request_id'])
                _check(old['principal'] == new['principal'] and new['supersedes_request_id'] == old['id'])
            row = prior

    def historical(self, instances: set[str]) -> None:
        while True:
            self.close_instances(instances)
            before = len(self.acquired)
            for instance in sorted(instances):
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
        _check(self.budget.remaining_bytes >= 0 and self.budget.accepted_rows <= 8192 and (self.budget.traversed <= 16384))
from runtime.infrastructure.workflow_schema import _DRAFT_EDGES, _DRAFT_MUTABLE, _DRAFT_PROJECTION
from runtime.workflows.activation import WorkflowActivationError, WorkflowActivationStore, _id, DocumentActivationRequest, parse_request, _authorization, _binding, _context as activation_context, _author_role, _task_brief, CONTEXT_LIMIT
from runtime.workflows.templates import WorkflowTemplateVersion
from runtime.workflows.artifacts import context_contract, document_from_handoff, document_from_submission, require_document_subject, review_family
from runtime.workflows.draft_dispatch import digest
from runtime.workflows.review_dispatch import _same_value, _validate_projection, _validate_transition, parse_stage
from runtime.workflows.review_receipts import _human_proof, _human_receipt_row, _receipt_rows, parse_human_review
from runtime.workflows.submissions import _hex, review_brief, review_scope
from runtime.workflows.revisions import CODE, _context, _pins, content, parse_revision, revision_actions, revision_brief
from runtime.workflows.review_join import ACTIONS, SERVICE, _replay

def _local_validate_draft_data(c: _Candidate, instance_id: str, *, expected_org_slug: str | None) -> None:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""

    def refuse() -> None:
        raise ValueError('workflow_draft_data_corrupt')
    intents = c.ordered(c.select('workflow_draft_dispatch_intents', instance_id=instance_id), 'instance_id', 'attempt_sequence')
    if intents and (not expected_org_slug):
        refuse()
    previous_by_instance: dict[str, dict] = {}
    accepted_results: dict[str, dict] = {}
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
        if not all((len(rows) == 1 for rows in (instance, activation, task, context, binding))):
            refuse()
        instance, activation, task, context, binding = (instance[0], activation[0], task[0], context[0], binding[0])
        if activation['instance_id'] != intent['instance_id'] or activation['activation_revision'] != intent['activation_revision'] or activation['authority_namespace'] != intent['authority_namespace'] or (activation['authority_generation'] != intent['authority_generation']) or (activation['authority_digest'] != intent['authority_digest']) or (binding['template_version_id'] != activation['template_version_id']) or (context['binding_snapshot_id'] != intent['binding_snapshot_id']) or (task['assigned_agent'] != intent['assigned_principal']) or (scope != {'assigned_agent': task['assigned_agent'], 'team': task['team'], 'brief': task['brief']}) or (intent['effect_key'] != f"workflow-initial-draft:{intent['instance_id']}:{intent['attempt_sequence']}") or (intent['host_execution_key'] != f"workflow-draft-host:{intent['id']}"):
            refuse()
        admission = dict(((key, intent[key]) for key in ('instance_id', 'activation_id', 'activation_revision', 'attempt_sequence', 'predecessor_intent_id', 'admission_principal', 'operation_key', 'request_digest')))
        admission['org_slug'] = expected_org_slug
        if intent['id'] != hashlib.sha256(c.canonical(admission)).hexdigest():
            refuse()
        previous = previous_by_instance.get(intent['instance_id'])
        if previous is None:
            if intent['attempt_sequence'] != 1 or instance['root_task_id'] != intent['task_id'] or instance['binding_snapshot_id'] != intent['binding_snapshot_id'] or (instance['context_id'] != intent['context_id']):
                refuse()
        elif intent['attempt_sequence'] != previous['attempt_sequence'] + 1 or intent['assignment_generation'] != previous['assignment_generation'] + 1 or intent['predecessor_intent_id'] != previous['id'] or (previous['is_current'] != 0) or (previous['state'] not in ('cancelled', 'failed', 'completed')) or (intent['admission_kind'] == 'retry' and previous['state'] != 'failed'):
            refuse()
        previous_by_instance[intent['instance_id']] = intent
        events = c.ordered(c.select('workflow_draft_dispatch_events', intent_id=intent['id']), 'event_seq')
        if not events or events[0]['created_at'] != intent['created_at']:
            refuse()
        projection = None
        digest = None
        immutable = dict(((key, c.hex(value) if isinstance(value, bytes) else value) for key, value in intent.items() if key not in _DRAFT_MUTABLE))
        for seq, event in enumerate(events, 1):
            payload = c.json(event['event_bytes'], event['event_digest'])
            if not isinstance(payload, dict) or set(payload) != set(('format', 'org_slug', 'event', 'previous_digest', 'intent', 'before', 'after', 'terminal_evidence', 'result')):
                refuse()
            event_identity = dict(((key, event[key]) for key in ('id', 'event_seq', 'event_kind', 'created_at')))
            before, after = (payload['before'], payload['after'])
            if payload['format'] != 'workflow-draft-event@1' or payload['org_slug'] != expected_org_slug or event['event_seq'] != seq or (event['id'] != f"{intent['id']}:{seq}") or (payload['event'] != event_identity) or (payload['previous_digest'] != digest) or (payload['intent'] != immutable) or (before != projection) or (not isinstance(after, dict)) or (set(after) != set(_DRAFT_PROJECTION)) or (event['state_before'] != (None if before is None else before['state'])) or (event['state_after'] != after['state']):
                refuse()
            kind = event['event_kind']
            try:
                timestamp = datetime.fromisoformat(event['created_at'])
                if timestamp.tzinfo is None or timestamp.utcoffset().total_seconds() != 0:
                    refuse()
            except (ValueError, TypeError, AttributeError):
                refuse()
            for key in ('is_current', 'cancellation_requested', 'host_launch_started'):
                if type(after[key]) is not int or after[key] not in (0, 1):
                    refuse()
            if after['state'] not in ('queued', 'claimed', 'running', 'uncertain', 'cancelled', 'failed', 'completed') or (after['is_current'] == 0 and after['state'] not in ('cancelled', 'failed', 'completed')) or (after['state'] in ('claimed', 'running', 'uncertain') and (not (after['claim_token'] and after['claim_owner']))) or (after['state'] == 'queued' and (after['host_launch_started'] or after['host_execution_id'] or after['session_id'])) or (after['state'] in ('running', 'completed') and (not (after['host_launch_started'] and after['host_execution_id'] and after['session_id']))) or (after['final_result_id'] is not None and (type(after['final_result_id']) is not int or not after['session_id'])):
                refuse()
            edge = (event['state_before'], event['state_after'])
            if kind in ('cancel_requested', 'callback_recorded', 'callback_rejected', 'retired'):
                if before is None or edge[0] != edge[1]:
                    refuse()
            elif edge not in _DRAFT_EDGES.get(kind, set()):
                refuse()
            if seq == 1 and after != dict(state='queued', is_current=1, cancellation_requested=0, claim_token=None, claim_owner=None, host_launch_started=0, host_execution_id=None, session_id=None, final_result_id=None):
                refuse()
            if before is not None:
                if before['host_launch_started'] and (not after['host_launch_started']) or (before['cancellation_requested'] and (not after['cancellation_requested'])) or (before['session_id'] is not None and before['session_id'] != after['session_id']) or (before['final_result_id'] is not None and before['final_result_id'] != after['final_result_id']) or (before['host_execution_id'] is not None and before['host_execution_id'] != after['host_execution_id']):
                    refuse()
            if before is not None and before['is_current'] != after['is_current'] and (kind != 'retired'):
                refuse()
            if before is not None and before['final_result_id'] != after['final_result_id'] and (kind != 'callback_recorded'):
                refuse()
            if kind in ('cancel_requested', 'retired', 'callback_rejected', 'callback_recorded'):
                permitted = {'cancel_requested': set(('cancellation_requested',)), 'retired': set(('is_current',)), 'callback_rejected': set(), 'callback_recorded': set(('final_result_id', 'session_id'))}[kind]
                if any((before[key] != after[key] for key in _DRAFT_PROJECTION if key not in permitted)):
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
                    if not isinstance(witness, dict) or set(witness) != set(('host_quiescent',)) or witness['host_quiescent'] is not True:
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
                result_closure = {'record': result, 'id': event['result_id'], 'digest': event['result_digest'], 'disposition': event['disposition'], 'accepted': event['callback_accepted']}
                if c.canonical(payload['result']) != c.canonical(result_closure) or hashlib.sha256(c.canonical(result)).hexdigest() != event['result_digest'] or result['task_id'] != intent['task_id'] or (result['agent'] != intent['assigned_principal']) or (result['session_id'] != event['session_id']) or (event['session_id'] != after['session_id']) or (list(c.select('workflow_dispatch_callbacks', result_id=str(event['result_id']))) + [None])[0]:
                    refuse()
                if kind == 'callback_recorded':
                    if event['callback_accepted'] != 1 or after['final_result_id'] != result['id'] or intent['id'] in accepted_results or after['cancellation_requested']:
                        refuse()
                    accepted_results[intent['id']] = result_closure
            elif kind not in ('completed', 'host_reconciled', 'retired') and payload['result'] is not None:
                refuse()
            if after['state'] == 'completed':
                result = accepted_results.get(intent['id'])
                if after['cancellation_requested'] or result is None or result['record']['status'] != 'completed' or (result['id'] != after['final_result_id']) or (payload['result'] != result) or (task['status'] != 'completed'):
                    refuse()
            projection, digest = (after, event['event_digest'])
        if projection != dict(((key, intent[key]) for key in _DRAFT_PROJECTION)) or events[-1]['created_at'] != intent['updated_at']:
            refuse()
    if any((not intent['is_current'] for intent in previous_by_instance.values())):
        refuse()
    if (list(c.orphan_draft_events(instance_id)) + [None])[0]:
        refuse()

def _local_event_revision(c: _Candidate, event: dict) -> int:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
    raw = event['event_bytes']
    kind = event['event_kind']
    code = f"workflow_legacy_event_unmappable: event={event['id']} instance={event['instance_id']}; source owner must restore authentic submission/round/event/replay archival closure"
    if not isinstance(raw, bytes) or not raw:
        raise ValueError(code)
    ordinary = hashlib.sha256(raw).hexdigest()
    historical_join = kind == 'joined' and hashlib.sha256(b'joined' + raw).hexdigest() == event['event_digest']
    if event['event_digest'] != ordinary and (not historical_join):
        raise ValueError(code)
    replays = c.select('workflow_operation_replays', effect_id=event['id'])
    if any((r['instance_id'] != event['instance_id'] or r['request_digest'] != ordinary for r in replays)):
        raise ValueError(code)
    subject = None
    try:
        parsed = c.json(raw)
        if isinstance(parsed, dict) and c.canonical(parsed) == raw:
            subject = parsed
    except _CandidateBudgetExhausted:
        raise
    except (ValueError, UnicodeError):
        pass
    if subject is not None and 'event_kind' in subject and (subject['event_kind'] != kind):
        raise ValueError(code)
    candidates = []
    for round_ in c.select('workflow_rounds', instance_id=event['instance_id']):
        submissions = c.select('workflow_submissions', id=round_['submission_id'])
        if len(submissions) != 1:
            continue
        submission = submissions[0]
        revision = submission['revision']
        if submission['instance_id'] != event['instance_id'] or type(revision) is not int or revision <= 0 or (round_['current_revision'] != revision) or (not isinstance(submission['submission_bytes'], bytes)) or (hashlib.sha256(submission['submission_bytes']).hexdigest() != submission['submission_digest']):
            continue
        if subject is not None:
            identities = {'instance_id': event['instance_id'], 'round_id': round_['id'], 'submission_id': submission['id'], 'revision': revision, 'submission_digest': submission['submission_digest']}
            if not any((k in subject for k in ('round_id', 'submission_id'))) or any((subject[k] != v or (k == 'revision' and type(subject[k]) is not int) for k, v in identities.items() if k in subject)):
                continue
        elif not historical_join:
            continue
        receipts = c.round_receipts(round_['id'])
        if any((r['submission_id'] != submission['id'] or r['submission_digest'] != submission['submission_digest'] for r in receipts)):
            continue
        receipt_valid = True
        for receipt in receipts:
            requests = c.select('workflow_review_requests', id=receipt['request_id'])
            if len(requests) != 1:
                receipt_valid = False
                break
            request = requests[0]
            if not isinstance(receipt['proof_bytes'], bytes) or hashlib.sha256(receipt['proof_bytes']).hexdigest() != receipt['proof_digest'] or (not isinstance(request['request_scope_bytes'], bytes)) or (hashlib.sha256(request['request_scope_bytes']).hexdigest() != request['request_scope_digest']) or (request['request_scope_digest'] != receipt['request_scope_digest']) or (request['assignment_generation'] != receipt['assignment_generation']) or (historical_join and (receipt['outcome'] != 'approved' or request['status'] != 'approved')):
                receipt_valid = False
                break
        if not receipt_valid:
            continue
        if historical_join and (not replays or len(receipts) != 3):
            continue
        candidates.append((round_['id'], submission['id'], revision))
    if len(candidates) != 1:
        raise ValueError(code + f'; candidates={candidates}')
    return candidates[0][2]

def _local_validate_submission_data(c: _Candidate, instance_id: str, *, expected_org_slug: str | None) -> None:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
    code = 'workflow_submission_data_corrupt'
    operations = dict(((r['submission_id'], r) for r in c.select('workflow_submission_operations', instance_id=instance_id)))
    for submission in c.select('workflow_submissions', instance_id=instance_id):
        if type(submission['revision']) is not int or submission['revision'] <= 0 or (not isinstance(submission['submission_bytes'], bytes)) or (hashlib.sha256(submission['submission_bytes']).hexdigest() != submission['submission_digest']):
            raise ValueError(code)
        op = operations.get(submission['id'])
        if op is None:
            if submission['source_result_id'] is None:
                raise ValueError(code)
            continue
        if submission['source_result_id'] is not None or not expected_org_slug or op['org_slug'] != expected_org_slug or (op['instance_id'] != submission['instance_id']) or (op['principal'] != submission['author_principal']) or (op['source_task_id'] != submission['source_task_id']) or (op['source_session_id'] != submission['source_session_id']) or any((not isinstance(op[k], str) or not op[k] or op[k].strip() != op[k] for k in ('operation_key', 'principal', 'source_task_id', 'source_session_id', 'created_at'))):
            raise ValueError(code)
        proof = c.json(op['provenance_bytes'], op['provenance_digest'])
        intents = c.select('workflow_draft_dispatch_intents', instance_id=op['instance_id'], task_id=op['source_task_id'], session_id=op['source_session_id'])
        if len(intents) != 1 or not isinstance(proof, dict) or set(proof) != set(('format', 'operation', 'submission', 'source_intent', 'request_bytes')) or (proof['format'] != 'workflow-submission-operation@1') or (c.canonical(proof['operation']) != c.canonical(dict(((k, v) for k, v in op.items() if k not in ('provenance_bytes', 'provenance_digest'))))) or (c.canonical(proof['submission']) != c.canonical(dict(((k, c.hex(v) if isinstance(v, bytes) else v) for k, v in submission.items())))) or (c.canonical(proof['source_intent']) != c.canonical(dict(((k, c.hex(v) if isinstance(v, bytes) else v) for k, v in intents[0].items() if k not in _DRAFT_MUTABLE)))) or (intents[0]['assigned_principal'] != op['principal']):
            raise ValueError(code)
        try:
            request = c.unhex(proof['request_bytes'])
        except (TypeError, ValueError):
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
        result, intent = (results[0], intents[0])
        payload = c.json(link['result_bytes'], link['result_digest'])
        callbacks = c.select('workflow_draft_dispatch_events', intent_id=intent['id'], event_kind='callback_recorded', result_id=result['id'])
        if c.canonical(payload) != c.canonical(result) or result['task_id'] != link['source_task_id'] or result['session_id'] != link['source_session_id'] or (result['agent'] != op['principal']) or (link['source_task_id'] != op['source_task_id']) or (link['source_session_id'] != op['source_session_id']) or (intent['final_result_id'] != result['id']) or (len(callbacks) != 1) or (callbacks[0]['result_digest'] != link['result_digest']):
            raise ValueError(code)

def _local_handoff_record(c: _Candidate, row: dict, *, expected_org_slug: str | None) -> dict:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
    code = 'workflow_author_handoff_storage_corrupt'

    def check(condition: bool) -> None:
        if not condition:
            raise ValueError(code)

    def same(value: object, expected: object) -> bool:
        return c.canonical(value) == c.canonical(expected)

    def closed(value: object, keys: str) -> dict:
        check(isinstance(value, dict) and set(value) == set(keys.split()))
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
        check(row['org_slug'] == expected_org_slug and re.fullmatch('[a-z0-9-]{1,40}', row['org_slug']) is not None)
        for key in ('id', 'intent_id', 'instance_id', 'principal', 'source_task_id', 'source_session_id', 'operation_key', 'submission_id'):
            check(isinstance(row[key], str) and re.fullmatch('[A-Za-z0-9._:-]{1,128}', row[key]) is not None)
        check(type(row['revision']) is int and row['revision'] == 1)
        for key, limit in (('candidate_bytes', 1048576), ('request_bytes', 2097152), ('admission_bytes', 16777216)):
            raw = row[key]
            check(isinstance(raw, bytes) and 1 <= len(raw) <= limit)
            digest_value = row[key.replace('_bytes', '_digest')]
            check(isinstance(digest_value, str) and re.fullmatch('[0-9a-f]{64}', digest_value) is not None and (hashlib.sha256(raw).hexdigest() == digest_value))
        row['candidate_bytes'].decode('utf8')
        check(re.fullmatch('\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}\\.\\d{6}Z', row['created_at']) is not None)
        datetime.strptime(row['created_at'], '%Y-%m-%dT%H:%M:%S.%fZ')
        proof = c.handoff(row['admission_bytes'], row['admission_digest'])
        family = {'workflow-author-handoff@1': 1, 'workflow-author-handoff@2': 2}.get(proof.get('format'))
        check(family is not None)
        closed(proof, 'format handoff client_request source_intent source_generation pins admission_authority' + (' repository_resolution' if family == 2 else ''))
        if family == 2:
            check(proof['repository_resolution'] is None)
        template_family = 4 if family == 2 else 3
        expected_row = dict(((k, c.hex(v) if isinstance(v, bytes) else v) for k, v in row.items() if k not in ('admission_bytes', 'admission_digest')))
        check(same(proof['handoff'], expected_row))
        command = closed(proof['client_request'], 'format operation_key activation_id expected_revision submission_bytes_base64 submission_digest' + (' artifact_kind' if family == 2 else ''))
        if family == 2:
            check(command['artifact_kind'] == 'document')
        check(command['format'] == f'workflow-author-handoff-command@{family}' and type(command['expected_revision']) is int and (command['expected_revision'] == 1) and (command['operation_key'] == row['operation_key']))
        candidate = c.unbase64(command['submission_bytes_base64'])
        check(candidate == row['candidate_bytes'] and command['submission_digest'] == row['candidate_digest'] and (base64.b64encode(candidate).decode('ascii') == command['submission_bytes_base64']))
        request = {**command, 'format': f'workflow-author-handoff-request@{family}', 'handoff_id': row['id'], 'submission_id': row['submission_id'], 'instance_id': row['instance_id'], 'revision': 1}
        check(c.canonical(request) == row['request_bytes'])
        check(row['id'] == 'workflow-author-handoff:' + hashlib.sha256(c.canonical([row['org_slug'], row['principal'], row['operation_key']])).hexdigest())
        check(row['submission_id'] == 'workflow-submission:' + hashlib.sha256(c.canonical([row['instance_id'], 1, row['org_slug'], row['principal'], row['operation_key'], f'workflow-author-handoff@{family}'])).hexdigest())
        intent = one('workflow_draft_dispatch_intents', row['intent_id'])
        check(same(proof['source_intent'], dict(((k, c.hex(v) if isinstance(v, bytes) else v) for k, v in intent.items() if k not in _DRAFT_MUTABLE))))
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
            if payload['after'] == dict(((k, generation[k]) for k in _DRAFT_PROJECTION if k != 'session_id')) | {'session_id': generation['session_id']}:
                check(datetime.fromisoformat(event['created_at']) <= datetime.strptime(row['created_at'], '%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=timezone.utc))
                witnessed = True
        check(witnessed)
        pins = closed(proof['pins'], 'template activation authorization binding context scope contract')
        activation = one('workflow_activations', intent['activation_id'])
        instance = one('workflow_instances', row['instance_id'])
        check(same(pins['activation'], dict(id=intent['activation_id'], revision=1, request_digest=activation['request_digest'])))
        from runtime.workflows.templates import WorkflowTemplateStore, _review_contract
        mapping = c.select('workflow_template_identity_versions', template_version_id=activation['template_version_id'])
        check(len(mapping) == 1)
        template = c.template(identity_id=activation['template_identity_id'], version=mapping[0]['version'], expected_version_id=activation['template_version_id'])
        check(same(pins['template'], dict(identity_id=template.identity_id, version_id=template.version_id, version=template.version, definition_digest=template.definition_digest, compiler_pin=f'workflow-compiler@{template_family}', validator_pin=f'workflow-validator@{template_family}', source_pin=f'operator-input@{template_family}')) and (template.compiler_pin, template.validator_pin, template.source_pin) == (f'workflow-compiler@{template_family}', f'workflow-validator@{template_family}', f'operator-input@{template_family}'))
        contract = _review_contract(template)
        context = one('workflow_contexts', intent['context_id'])
        binding = one('workflow_binding_snapshots', intent['binding_snapshot_id'])
        authorization = one('workflow_authorization_revisions', binding['authorization_revision_id'])
        for kind, identity, raw, dg in (('authorization', authorization['id'], authorization['authority_bytes'], authorization['authority_digest']), ('binding', binding['id'], binding['binding_bytes'], binding['binding_digest']), ('context', context['id'], context['context_bytes'], context['context_digest']), ('scope', intent['task_id'], intent['task_scope_bytes'], intent['task_scope_digest']), ('contract', template.version_id, c.canonical(contract), hashlib.sha256(c.canonical(contract)).hexdigest())):
            pin = closed(pins[kind], 'id bytes_hex digest')
            check(same(pin, dict(id=identity, bytes_hex=c.hex(raw), digest=dg)) and len(identity) <= 256 and (1 <= len(raw) <= 1048576))
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
            frozen = parse_request(ctx['request']).model_dump(by_alias=True)
            check(same(frozen, ctx['request']) and same(frozen, grant['original_request']))
            expected_grant = _authorization(org_slug=row['org_slug'], instance_id=row['instance_id'], activation_id=intent['activation_id'], task_id=intent['task_id'], request=frozen, request_digest=intent['request_digest'], template=template, timestamp=activation['created_at'])
            expected_bound = _binding(instance_id=row['instance_id'], activation_id=intent['activation_id'], authorization_id=authorization['id'], template=template, request=frozen)
            expected_context = _context(org_slug=row['org_slug'], request=frozen, snapshot=ctx['authority_snapshot'], template=template, inputs=ctx['inputs'], authorization=expected_grant, authorization_id=authorization['id'], binding=expected_bound, binding_id=binding['id'], intent_id=intent['id'])
            check(same(grant, expected_grant) and same(bound, expected_bound) and same(ctx, expected_context))
            check(contract['output']['accepted_kinds'] == ['document'] and grant['repository_binding'] is None and (bound['repository_binding'] is None) and (ctx['repository_binding'] is None) and (grant['original_request']['repository_access'] is None))
        handoff_actions = ['stage-immutable-artifact', 'submit-immutable-artifact'] if family == 2 else ['stage-immutable-document', 'submit-immutable-document']
        captured = closed(proof['admission_authority'], 'ready pointer profiles mirror cutover actions')
        ready = closed(captured['ready'], 'namespace generation snapshot_bytes_hex snapshot_digest')
        snapshot = c.handoff(hex_bytes(ready['snapshot_bytes_hex'], 1048576), ready['snapshot_digest'])
        journal = c.publication(ready['namespace'], ready['generation'], ready['snapshot_digest'])
        check(len(journal) == 1 and c.hex(journal[0]['snapshot_bytes']) == ready['snapshot_bytes_hex'] and (journal[0]['state'] == 'cache_installed') and same(snapshot, ctx['authority_snapshot']) and (ready['namespace'] == f'org/{expected_org_slug}') and (type(ready['generation']) is int) and (ready['generation'] == intent['authority_generation']) and (ready['snapshot_digest'] == intent['authority_digest']))
        check(same(captured['pointer'], dict(current_generation=ready['generation'], journal_id=journal[0]['id'], snapshot_digest=ready['snapshot_digest'], state='ready', profile_fence=journal[0]['profile_fence'])))
        check(captured['cutover'] == 'enabled' and same(captured['actions'], handoff_actions) and (set(captured['actions']) <= set(grant['original_request']['allowed_actions'])))

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
        names: list[str] = []
        for profile in snapshot_profiles:
            closed(profile, 'consumers generation profile_digest profile_name')
            profile_name(profile['profile_name'])
            i0(profile['generation'])
            digest_value(profile['profile_digest'])
            check(isinstance(profile['consumers'], list))
            for consumer in profile['consumers']:
                namespace(consumer)
            check(profile['consumers'] == sorted(set(profile['consumers'])))
            names.append(profile['profile_name'])
        check(names == sorted(set(names)))
        check(isinstance(captured['profiles'], list) and len(captured['profiles']) <= 1024)
        for profile in captured['profiles']:
            closed(profile, 'name digest')
            profile_name(profile['name'])
            digest_value(profile['digest'])
        check(same(captured['profiles'], list((dict(name=p['profile_name'], digest=p['profile_digest']) for p in snapshot_profiles))))
        check(isinstance(captured['mirror'], list) and len(captured['mirror']) <= 16384)
        grouped: list[tuple] = []
        dependencies: list[dict] = []
        stores: dict[str, dict] = {}
        registry: dict[str, dict] = {}
        for item in captured['mirror']:
            kind = item.get('kind') if isinstance(item, dict) else None
            fields = {'dependency': 'kind org_namespace profile_name consumer_identity bound_generation state', 'store': 'kind profile_name generation profile_digest state', 'registry': 'kind profile_name published_generation'}
            check(type(kind) is str and kind in fields)
            closed(item, fields[kind])
            profile_name(item['profile_name'])
            if kind == 'dependency':
                namespace(item['org_namespace'])
                namespace(item['consumer_identity'])
                i0(item['bound_generation'])
                check(type(item['state']) is str and item['state'] in ('active', 'unbound', 'removed'))
                grouped.append((0, item['org_namespace'], item['profile_name'], item['consumer_identity']))
                dependencies.append(item)
            elif kind == 'store':
                i0(item['generation'])
                digest_value(item['profile_digest'])
                check(type(item['state']) is str and item['state'] in ('active', 'removed'))
                grouped.append((1, item['profile_name']))
                stores[item['profile_name']] = item
            else:
                i0(item['published_generation'])
                grouped.append((2, item['profile_name']))
                registry[item['profile_name']] = item
        check(grouped == sorted(set(grouped)))
        required: dict[str, dict] = {}
        for dependency in dependencies:
            if dependency['org_namespace'] != ready['namespace'] or dependency['state'] == 'removed':
                continue
            name = dependency['profile_name']
            store = stores.get(name)
            published = registry.get(name)
            check(dependency['state'] == 'active' and store is not None and (published is not None))
            bound_generation = dependency['bound_generation']
            check(store['state'] == 'active' and store['generation'] == bound_generation and (published['published_generation'] == bound_generation))
            profile = required.setdefault(name, dict(consumers=[], generation=bound_generation, profile_digest=store['profile_digest'], profile_name=name))
            check(profile['generation'] == bound_generation and profile['profile_digest'] == store['profile_digest'])
            profile['consumers'].append(dependency['consumer_identity'])
        check(same(list((required[name] for name in sorted(required))), snapshot_profiles))
        return proof
    except (ValueError, KeyError, TypeError, AttributeError, UnicodeError, sqlite3.DatabaseError) as exc:
        raise ValueError(code) from exc

def _local_reviewed_source_result(c: _Candidate, row: dict, intent: dict) -> dict:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
    from runtime.workflows.review_dispatch import _same_value
    code = 'workflow_author_handoff_storage_corrupt'
    links = c.select('workflow_submission_result_links', submission_id=row['submission_id'])
    if len(links) != 1 or type(links[0]['task_result_id']) is not int or links[0]['task_result_id'] <= 0:
        raise ValueError(code)
    link = links[0]
    result = c.one('task_results', link['task_result_id'])
    callbacks = c.select('workflow_draft_dispatch_events', intent_id=intent['id'], event_kind='callback_recorded')
    terminals = list((r for r in c.select('workflow_draft_dispatch_events', intent_id=intent['id']) if r['event_kind'] in ('completed', 'host_reconciled')))
    if len(callbacks) != 1 or result['status'] != 'completed' or result['task_id'] != row['source_task_id'] or (result['agent'] != row['principal']) or (result['session_id'] != row['source_session_id']) or (intent['final_result_id'] != result['id']) or (link['source_task_id'] != result['task_id']) or (link['source_session_id'] != result['session_id']) or (not _same_value(c.json(link['result_bytes'], link['result_digest']), result)):
        raise ValueError(code)
    callback = c.json(callbacks[0]['event_bytes'], callbacks[0]['event_digest'])
    accepted = callback['result']
    if type(accepted) is not dict or set(accepted) != set(('id', 'digest', 'record', 'accepted', 'disposition')) or type(accepted['accepted']) is not int or (accepted['accepted'] != 1) or (accepted['disposition'] not in ('accepted', 'pending_host_acknowledgment')) or (not _same_value(accepted['id'], result['id'])) or (not _same_value(accepted['record'], result)) or (accepted['digest'] != digest(c.canonical(result))) or (type(callbacks[0]['callback_accepted']) is not int) or (callbacks[0]['callback_accepted'] != 1) or (not _same_value(callbacks[0]['result_id'], result['id'])):
        raise ValueError(code)
    witnessed = False
    for terminal in terminals:
        body = c.json(terminal['event_bytes'], terminal['event_digest'])
        if body['after']['state'] == 'completed' and _same_value(body['after']['final_result_id'], result['id']) and _same_value(body['terminal_evidence'], {'host_quiescent': True}) and _same_value(body['result'], accepted):
            witnessed = True
    if not witnessed:
        raise ValueError(code)
    return result

def _local_history_successor(c: _Candidate, instance_id: str, *, fulfilled: bool=False) -> bool:
    row = _local_successor_for_history(c, instance_id)
    if row is None:
        return False
    if not fulfilled:
        return True
    return (list(c.revision_links(instance_id)) + [None])[0] is not None

def _local_validate_handoff_graph(c: _Candidate, row: dict, proof: dict) -> dict | None:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
    code = 'workflow_author_handoff_storage_corrupt'
    revision = row['revision']
    generation = 2 if revision == 2 else 1
    operations = c.select('workflow_submission_operations', submission_id=row['submission_id'])
    rounds = c.select('workflow_rounds', instance_id=row['instance_id'], current_revision=revision)
    if revision == 2 and (not (list(c.select('workflow_submission_result_links', submission_id=row['submission_id'])) + [None])[0]):
        if rounds:
            raise ValueError(code)
        return None
    if not operations:
        if rounds or (list(c.select('workflow_submissions', id=row['submission_id'])) + [None])[0]:
            raise ValueError(code)
        return None
    if len(operations) != 1 or len(rounds) != 1:
        raise ValueError(code)
    intent = c.one('workflow_draft_dispatch_intents', row['intent_id'])
    result = _local_reviewed_source_result(c, row, intent)
    contributors = tuple(c.select('workflow_instance_contributors', instance_id=row['instance_id']))
    candidate = document_from_submission(c.one('workflow_submissions', row['submission_id']), org_namespace=row['org_slug'], pins=proof['pins'], source_result_id=result['id'], contributors=contributors) if revision == 2 else document_from_handoff(row, proof, source_result_id=result['id'], contributors=contributors)
    submission = document_from_submission(c.one('workflow_submissions', row['submission_id']), org_namespace=operations[0]['org_slug'], pins=proof['pins'], source_result_id=result['id'], contributors=contributors)
    _accounted_candidate_key = candidate.review_key
    _accounted_submission_key = submission.review_key
    if _accounted_candidate_key != _accounted_submission_key:
        raise ValueError(code)
    round_ = rounds[0]
    if round_['submission_id'] != row['submission_id'] or round_['state'] not in ('reviewing', 'closed') or round_['id'] != _id('workflow-round', row['instance_id'], revision, row['submission_id']):
        raise ValueError(code)
    context = c.json(c.one('workflow_contexts', intent['context_id'])['context_bytes'])
    contract = context_contract(context)
    bindings = context['binding']['bindings']
    requests = c.select('workflow_review_requests', round_id=round_['id'])
    expected = []
    tasks = []
    if len(requests) != len(contract['reviewer_roles']):
        raise ValueError(code)
    for role in contract['reviewer_roles']:
        principal = bindings[role]['principal']
        selected = list((q for q in requests if q['principal'] == principal and q['assignment_generation'] == generation))
        if len(selected) != 1:
            raise ValueError(code)
        q = selected[0]
        scope = review_scope(row=row, proof=proof, context=context, contract=contract, role=role, request_id=q['id'], round_id=round_['id'], binding=bindings[role], generation=generation)
        if q['id'] != _id('workflow-request', round_['id'], role, principal, generation) or q['status'] not in ('pending', 'approved', 'changes_requested') or q['supersedes_request_id'] is not None or (c.canonical(scope) != q['request_scope_bytes']) or (digest(q['request_scope_bytes']) != q['request_scope_digest']):
            raise ValueError(code)
        expected.append(q['id'])
        bridges = c.select('workflow_request_task_bridges', request_id=q['id'])
        dispatch = c.select('workflow_dispatch_operations', request_id=q['id'])
        if contract['role_kinds'][role] == 'human':
            if bridges or dispatch:
                raise ValueError(code)
            _local_validate_human_receipt(c, q, scope, proof)
            continue
        if len(bridges) != 1 or len(dispatch) != 1:
            raise ValueError(code)
        bridge, op = (bridges[0], dispatch[0])
        task = c.one('tasks', bridge['task_id'])
        outbox = c.select('workflow_dispatch_outbox', request_id=q['id'])
        brief = review_brief(scope, row['candidate_bytes'])
        if len(outbox) != 1 or outbox[0]['operation_id'] != op['id'] or bridge['operation_id'] != op['id'] or (op['id'] != _id('workflow-dispatch', q['id'], generation)) or (op['operation_key'] != q['id']) or (outbox[0]['id'] != _id('workflow-outbox', op['id'])) or (outbox[0]['effect_key'] != 'workflow-review:' + q['id']) or (outbox[0]['host_execution_key'] != 'workflow-review-host:' + op['id']) or (outbox[0]['recovery_owner'] != 'workflow_recovery') or (outbox[0]['artifact_revision'] != revision) or any((outbox[0][k] != intent[k] for k in ('authority_namespace', 'authority_generation', 'authority_digest'))) or (bridge['instance_id'] != row['instance_id']) or (bridge['assigned_principal'] != principal) or (bridge['assignment_generation'] != generation) or (task['assigned_agent'] != principal) or (task['team'] != bindings[role]['team']) or (op['org_slug'] != row['org_slug']) or (task['brief'] != brief) or (task['task_type'] != 'subtask') or (task['active_chain'] is not None) or (task['active_fanout'] is not None) or (task['parent_task_id'] is not None) or (task['current_session_id'] != bridge['session_id'] and (not (outbox[0]['state'] in ('claimed', 'cancelled', 'uncertain') and (not outbox[0]['host_launch_started']) and (bridge['session_id'] is None)))) or (op['principal'] != principal) or (op['instance_id'] != row['instance_id']) or (op['round_id'] != round_['id']) or (op['request_digest'] != q['request_scope_digest']):
            raise ValueError(code)
        tasks.append(task['id'])
        _local_validate_agent_receipt(c, q, scope)
    if len(tasks) > 3 or len(set(tasks)) != len(tasks) or len(set(expected)) != len(expected):
        raise ValueError(code)
    events = c.select('workflow_events', instance_id=row['instance_id'], revision=revision, event_kind='submitted')
    links = c.select('workflow_submission_result_links', submission_id=row['submission_id'])
    if len(events) != 1 or len(links) != 1 or links[0]['task_result_id'] != intent['final_result_id']:
        raise ValueError(code)
    event = c.json(events[0]['event_bytes'])
    graph = dict(submission_id=row['submission_id'], round_id=round_['id'], request_ids=expected, reviewer_task_ids=tasks)
    selected_event = dict(event_kind='submitted', instance_id=row['instance_id'], revision=revision, submission_digest=row['candidate_digest'], **graph)
    if event != selected_event:
        raise ValueError(code)
    contributor = dict(principal=row['principal'], source_task_id=row['source_task_id'], source_session_id=row['source_session_id'], source_result_id=str(intent['final_result_id']), contribution_kind='author')
    for table, key in (('workflow_submission_contributors', 'submission_id'), ('workflow_instance_contributors', 'instance_id')):
        identity = row['submission_id'] if key == 'submission_id' else row['instance_id']
        records = list((r for r in c.rows(table, key, identity) if r['contribution_kind'] == 'author'))
        if key == 'instance_id' and _local_history_successor(c, row['instance_id']):
            _local_validate_author_history(c, row['instance_id'], records)
            records = list((r for r in records if r['source_task_id'] == row['source_task_id']))
        selected_contributors = [{key: identity, **contributor}]
        if records != selected_contributors:
            raise ValueError(code)
    _local_validate_collection(c, row, proof, graph)
    return graph

def _local_successor_for_history(c: _Candidate, instance_id: str) -> dict | None:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
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
    if body.get('format') != 'workflow-document-revision-admission@1' or c.canonical(body.get('operation')) != c.canonical(_hex(dict(((k, v) for k, v in row.items() if k not in ('admission_bytes', 'admission_digest'))))) or intent['admission_kind'] != 'revision' or (intent['instance_id'] != instance_id) or (intent['context_id'] != row['context_id']) or (intent['activation_id'] != row['activation_id']) or (intent['request_bytes'] != row['request_bytes']) or (intent['request_digest'] != row['request_digest']) or (row['expected_revision'] != 1) or (row['target_revision'] != 2):
        raise ValueError(CODE)
    return row

def _local_validate_author_history(c: _Candidate, instance_id: str, records: list[dict]) -> None:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
    successor = _local_successor_for_history(c, instance_id)
    if successor is None:
        raise ValueError(CODE)
    handoffs = c.select('workflow_author_handoffs', submission_id=successor['predecessor_submission_id'])
    if len(handoffs) != 1:
        raise ValueError(CODE)
    first = handoffs[0]
    original = c.one('workflow_draft_dispatch_intents', first['intent_id'])
    result = _local_reviewed_source_result(c, first, original)
    expected = [dict(instance_id=instance_id, principal=first['principal'], source_task_id=first['source_task_id'], source_session_id=first['source_session_id'], source_result_id=str(result['id']), contribution_kind='author')]
    intent = c.one('workflow_draft_dispatch_intents', successor['draft_intent_id'])
    links = c.revision_links(instance_id)
    if links:
        subject = dict(submission_id=links[0]['submission_id'], source_task_id=intent['task_id'], source_session_id=intent['session_id'], principal=intent['assigned_principal'])
        result = _local_reviewed_source_result(c, subject, intent)
        if len(links) != 1 or links[0]['task_result_id'] != result['id']:
            raise ValueError(CODE)
        expected.append(dict(instance_id=instance_id, principal=intent['assigned_principal'], source_task_id=intent['task_id'], source_session_id=intent['session_id'], source_result_id=str(result['id']), contribution_kind='author'))
    if c.canonical(sorted(records, key=lambda r: r['source_task_id'])) != c.canonical(sorted(expected, key=lambda r: r['source_task_id'])):
        raise ValueError(CODE)

def _local_predecessor(c: _Candidate, handoff: dict, proof: dict, graph: dict) -> tuple[dict, list[dict]]:
    from runtime.workflows.review_receipts import recipient_receipt
    round_ = c.one('workflow_rounds', graph['round_id'])
    requests = list((c.one('workflow_review_requests', qid) for qid in graph['request_ids']))
    if round_['state'] != 'closed' or any((q['status'] == 'pending' for q in requests)) or (not any((q['status'] == 'changes_requested' for q in requests))):
        raise WorkflowActivationError('workflow_document_revision_predecessor_not_closed')
    receipts, feedback = ([], [])
    for request in requests:
        rows = c.select('workflow_review_receipts', request_id=request['id'])
        if len(rows) != 1:
            raise ValueError(CODE)
        receipt = rows[0]
        scope = c.json(request['request_scope_bytes'], request['request_scope_digest'])
        safe = recipient_receipt(receipt)
        receipts.append(receipt)
        feedback.append(dict(role=scope['role'], request_id=request['id'], receipt_id=receipt['id'], outcome=receipt['outcome'], findings=dict(findings_bytes_base64=safe['findings_bytes_base64'], findings_digest=safe['findings_digest']), request_scope_digest=request['request_scope_digest'], receipt_digest=receipt['proof_digest']))
    return (dict(handoff_id=handoff['id'], source_intent_id=handoff['intent_id'], submission_id=handoff['submission_id'], submission_digest=handoff['candidate_digest'], round_id=graph['round_id'], revision=1, request_ids=graph['request_ids'], receipt_ids=list((p['id'] for p in receipts)), receipt_digests=list((p['proof_digest'] for p in receipts))), feedback)

def _local_validate_revision_record(c: _Candidate, row: dict, *, expected_org_slug: str | None) -> dict:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
    try:
        command = parse_revision(c.handoff(row['request_bytes'], row['request_digest']))
        proof = c.handoff(row['admission_bytes'], row['admission_digest'])
        if set(proof) != set(('format', 'operation', 'client_request', 'predecessor', 'pins', 'admission_authority')) or proof['format'] != 'workflow-document-revision-admission@1' or c.canonical(command) != row['request_bytes'] or (c.canonical(command) != c.canonical(proof['client_request'])) or (row['org_slug'] != expected_org_slug) or (row['principal'] != 'human:founder') or (row['id'] != _id('workflow-document-revision', row['org_slug'], row['principal'], row['operation_key'])) or (c.canonical(proof['operation']) != c.canonical(_hex(dict(((k, v) for k, v in row.items() if k not in ('admission_bytes', 'admission_digest')))))):
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
        ctx = _context(row['org_slug'], row['id'], source, predecessor, feedback)
        context = c.one('workflow_contexts', row['context_id'])
        task = c.one('tasks', intent['task_id'])
        scope = dict(assigned_agent=first['assigned_principal'], team=c.one('tasks', first['task_id'])['team'], brief=revision_brief(ctx, old['candidate_bytes']))
        admission = dict(org_slug=row['org_slug'], instance_id=row['instance_id'], activation_id=row['activation_id'], activation_revision=1, attempt_sequence=2, predecessor_intent_id=first['id'], admission_principal=row['principal'], operation_key=row['operation_key'], request_digest=row['request_digest'])
        expected_immutable = dict(((k, v) for k, v in first.items() if k not in _DRAFT_MUTABLE))
        expected_immutable.update({'id': digest(c.canonical(admission)), 'attempt_sequence': 2, 'predecessor_intent_id': first['id'], 'admission_kind': 'revision', 'operation_key': row['operation_key'], 'request_bytes': row['request_bytes'], 'request_digest': row['request_digest'], 'task_id': task['id'], 'context_id': context['id'], 'assignment_generation': 2, 'task_scope_bytes': c.canonical(scope), 'task_scope_digest': digest(c.canonical(scope)), 'effect_key': f"workflow-initial-draft:{row['instance_id']}:2", 'host_execution_key': 'workflow-draft-host:' + row['draft_intent_id'], 'created_at': row['created_at']})
        if c.canonical(_hex(expected_immutable)) != c.canonical(_hex(dict(((k, v) for k, v in intent.items() if k not in _DRAFT_MUTABLE)))) or context != dict(id=_id('workflow-context', row['instance_id'], digest(c.canonical(ctx))), binding_snapshot_id=first['binding_snapshot_id'], context_bytes=c.canonical(ctx), context_digest=digest(c.canonical(ctx)), source_kind='document-revision', source_id=row['id']) or len(context['context_bytes']) > 1048576 or (len(row['admission_bytes']) > 16777216) or (row['activation_id'] != first['activation_id']) or (row['instance_id'] != first['instance_id']) or (row['binding_snapshot_id'] != first['binding_snapshot_id']) or (row['authorization_revision_id'] != oldproof['pins']['authorization']['id']) or (row['predecessor_round_id'] != graph['round_id']) or (first['is_current'] != 0) or (first['state'] != 'completed') or (task['brief'] != scope['brief']) or (task['assigned_agent'] != scope['assigned_agent']) or (task['team'] != scope['team']) or (task['task_type'] != 'subtask') or (task['parent_task_id'] is not None) or (task['active_chain'] is not None) or (task['active_fanout'] is not None) or (command != dict(format='workflow-document-revision-command@1', operation_key=row['operation_key'], instance_id=row['instance_id'], activation_id=row['activation_id'], expected_revision=1, predecessor_submission_id=old['submission_id'], predecessor_submission_digest=old['candidate_digest'], predecessor_round_id=graph['round_id'], authority=dict(namespace='org/' + row['org_slug'], generation=first['authority_generation'], snapshot_digest=first['authority_digest']))) or (c.canonical(proof['predecessor']) != c.canonical(predecessor)) or (c.canonical(proof['pins']) != c.canonical(_pins(oldproof, context, intent))) or (c.canonical(proof['admission_authority']) != c.canonical(dict(oldproof['admission_authority'], actions=revision_actions(ctx)))):
            raise ValueError(CODE)
        _local_reviewed_source_result(c, old, first)
        return proof
    except (ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise ValueError(CODE) from exc

def _local_stage(c: _Candidate, row: dict, intent: dict) -> tuple[dict, dict, dict] | None:
    rows = c.select('workflow_submission_operations', instance_id=row['instance_id'], source_task_id=intent['task_id'])
    if not rows:
        if (list(c.select('workflow_submissions', instance_id=row['instance_id'], revision=2)) + [None])[0]:
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
    command = parse_revision(original, author=True)
    sid = _id('workflow-submission', row['instance_id'], 2, row['org_slug'], intent['assigned_principal'], command['operation_key'], 'workflow-author-revision@1')
    expected = dict(command, format='workflow-author-revision-request@1', submission_id=sid, instance_id=row['instance_id'], revision=2)
    if c.canonical(request) != c.canonical(expected) or command['revision_operation_id'] != row['id'] or submission != dict(id=sid, instance_id=row['instance_id'], revision=2, submission_bytes=content(command), submission_digest=command['submission_digest'], storage_ref=None, source_task_id=intent['task_id'], source_session_id=intent['session_id'], source_result_id=None, author_principal=intent['assigned_principal']) or (operation['operation_key'] != command['operation_key']) or (operation['org_slug'] != row['org_slug']) or (operation['principal'] != intent['assigned_principal']):
        raise ValueError(CODE)
    stamp = datetime.strptime(operation['created_at'], '%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=timezone.utc)
    if operation['created_at'] != stamp.strftime('%Y-%m-%dT%H:%M:%S.%fZ'):
        raise ValueError(CODE)
    launches = c.select('workflow_draft_dispatch_events', intent_id=intent['id'], event_kind='launch_reserved')
    callbacks = list((r for r in c.select('workflow_draft_dispatch_events', intent_id=intent['id']) if r['event_kind'] in ('callback_recorded', 'cancel_requested')))
    if len(launches) != 1 or stamp < datetime.fromisoformat(launches[0]['created_at']) or any((stamp > datetime.fromisoformat(e['created_at']) for e in callbacks)):
        raise ValueError(CODE)
    return (submission, operation, command)

def _local_review_row(c: _Candidate, operation: dict) -> dict | None:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
    intent = c.one('workflow_draft_dispatch_intents', operation['draft_intent_id'])
    stage = _local_stage(c, operation, intent)
    if stage is None:
        return None
    submission, staged, _ = stage
    return dict(id=operation['id'], intent_id=intent['id'], instance_id=operation['instance_id'], org_slug=operation['org_slug'], principal=intent['assigned_principal'], source_task_id=intent['task_id'], source_session_id=intent['session_id'], operation_key=staged['operation_key'], submission_id=submission['id'], revision=2, candidate_bytes=submission['submission_bytes'], candidate_digest=submission['submission_digest'], request_bytes=c.unhex(c.handoff(staged['provenance_bytes'], staged['provenance_digest'])['request_bytes']), request_digest=staged['request_digest'], created_at=operation['created_at'])

def _local_selected_subject(c: _Candidate, scope: dict) -> tuple[dict, dict]:
    if review_family(scope) == 3:
        operation = c.one('workflow_document_revision_operations', scope['revision_operation_id'])
        proof = _local_validate_revision_record(c, operation, expected_org_slug=scope['org_slug'])
        row = _local_review_row(c, operation)
        if row is None:
            raise ValueError(CODE)
        return (row, proof)
    row = c.one('workflow_author_handoffs', scope['handoff_id'])
    return (row, _local_handoff_record(c, row, expected_org_slug=scope['org_slug']))

def _local_validate_submission_subject(c: _Candidate, submission: dict, scope: dict) -> None:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
    code = 'workflow_review_receipt_storage_corrupt'
    try:
        operations = c.select('workflow_submission_operations', submission_id=submission['id'])
        links = c.select('workflow_submission_result_links', submission_id=submission['id'])
        if len(operations) != 1 or len(links) != 1:
            raise ValueError(code)
        contributors = tuple(c.select('workflow_instance_contributors', instance_id=submission['instance_id']))
        artifact = document_from_submission(submission, org_namespace=operations[0]['org_slug'], pins=dict(((key, scope[key]) for key in ('template', 'activation', 'authorization', 'binding_snapshot_id', 'context_id'))), source_result_id=links[0]['task_result_id'], contributors=contributors)
        require_document_subject(artifact, scope)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(code) from exc

def _local_validate_human_receipt(c: _Candidate, request: dict, scope: dict, handoff: dict) -> None:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
    from runtime.workflows.review_dispatch import _same_value
    code = 'workflow_review_receipt_storage_corrupt'
    if scope['kind'] != 'human' or scope['principal'] != 'founder':
        raise ValueError(code)
    receipts = c.select('workflow_review_receipts', request_id=request['id'])
    if (list(c.select('workflow_current_assignments', instance_id=scope['instance_id'], role_key=scope['role'])) + [None])[0] or (list(c.select('workflow_instance_tasks', instance_id=scope['instance_id'], role_key=scope['role'])) + [None])[0] or (list(c.select('workflow_task_results', instance_id=scope['instance_id'], principal='founder')) + [None])[0] or (list(c.request_evidence(request['id'])) + [None])[0]:
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
    command = parse_human_review(proof['command'])
    created_at = proof['created_at']
    stamp = datetime.strptime(created_at, '%Y-%m-%dT%H:%M:%S.%fZ')
    submission = c.one('workflow_submissions', scope['submission_id'])
    contributors = set((r[0] for r in list(((r['principal'],) for r in c.select('workflow_instance_contributors', instance_id=scope['instance_id'])))))
    contributors.update(set((r[0] for r in list(((r['principal'],) for r in c.select('workflow_submission_operations', instance_id=scope['instance_id']))))))
    if command['format'] != f'workflow-human-review-command@{review_family(scope)}' or created_at != stamp.strftime('%Y-%m-%dT%H:%M:%S.%fZ') or command['request_id'] != request['id'] or (command['expected_revision'] != scope['revision']) or (command['request_scope_digest'] != request['request_scope_digest']) or (command['outcome'] not in scope['outcomes']) or (request['status'] != command['outcome']) or (request['principal'] in contributors):
        raise ValueError(code)
    _local_validate_submission_subject(c, submission, scope)
    expected = _human_proof(request, scope, handoff, command, created_at)
    if not _same_value(proof, expected) or not _same_value(receipt, _human_receipt_row(request, scope, expected)) or (not _same_value(c.human_keys(org_slug=scope['org_slug'], operation_key=command['operation_key']), [receipt])):
        raise ValueError(code)

def _local_completed_proof(c: _Candidate, request: dict, scope: dict) -> tuple[dict, dict, dict] | None:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
    from runtime.workflows.review_dispatch import _same_value
    _local_validate_review_dispatch_storage(c, request_id=request['id'])
    code = 'workflow_review_receipt_storage_corrupt'
    bridge = _local_record_bridge(c, request['id'])
    events = c.ordered(c.select('workflow_dispatch_events', operation_id=bridge['operation_id']), 'event_seq')
    stages = list((e for e in events if e['event_kind'] == 'review_staged'))
    if len(stages) != 1 or not events or events[-1]['event_kind'] != 'completed' or (bridge['state'] != 'completed'):
        return None
    terminal = events[-1]
    body = c.json(terminal['event_bytes'], terminal['event_digest'])
    stage = c.json(stages[0]['event_bytes'], stages[0]['event_digest'])
    accepted = body['result']
    if type(accepted) is not dict or type(accepted['id']) is not int or accepted['id'] <= 0 or (not _same_value(body['terminal_evidence'], {'host_quiescent': True})):
        raise ValueError(code)
    result = c.one('task_results', accepted['id'])
    task = c.one('tasks', bridge['task_id'])
    if not _same_value(result, accepted['record']) or bridge['result_id'] != str(result['id']) or result['status'] != 'completed' or (result['task_id'] != bridge['task_id']) or (result['agent'] != bridge['assigned_principal']) or (result['session_id'] != bridge['session_id']) or (task['status'] != 'completed') or (task['cancelled_at'] is not None) or (task['assigned_agent'] != result['agent']) or (task['current_session_id'] != result['session_id']) or (stage['stage']['session_id'] != result['session_id']):
        raise ValueError(code)
    if result['decision_json'] is not None or result['waiting_on_job_ids'] is not None:
        return None
    submission = c.one('workflow_submissions', scope['submission_id'])
    _local_validate_submission_subject(c, submission, scope)
    contributors = set((r[0] for r in list(((r['principal'],) for r in c.select('workflow_instance_contributors', instance_id=scope['instance_id'])))))
    contributors.update(set((r[0] for r in list(((r['principal'],) for r in c.select('workflow_submission_operations', instance_id=scope['instance_id']))))))
    if request['principal'] in contributors:
        raise ValueError(code)
    proof = dict(format=f'workflow-agent-review-proof@{review_family(scope)}', scope=scope, request_scope_digest=request['request_scope_digest'], stage_event=dict(id=stages[0]['id'], digest=stages[0]['event_digest'], body=stage), terminal_event=dict(id=terminal['id'], digest=terminal['event_digest']), callback=accepted, result_id=result['id'], task_id=result['task_id'], session_id=result['session_id'], principal=result['agent'], outcome=stage['stage']['command']['outcome'])
    return (proof, result, terminal)

def _local_record_bridge(c: _Candidate, request_id: str) -> dict:
    rows = c.select('workflow_request_task_bridges', request_id=request_id)
    if len(rows) != 1:
        raise ValueError('workflow_review_receipt_storage_corrupt')
    return rows[0]

def _local_validate_agent_receipt(c: _Candidate, request: dict, scope: dict) -> None:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
    from runtime.workflows.review_dispatch import _same_value
    code = 'workflow_review_receipt_storage_corrupt'
    receipts = c.select('workflow_review_receipts', request_id=request['id'])
    bridge = _local_record_bridge(c, request['id'])
    selectors = {'workflow_instance_tasks': dict(instance_id=scope['instance_id'], task_id=bridge['task_id']), 'workflow_task_results': dict(instance_id=scope['instance_id'], task_id=bridge['task_id']), 'workflow_current_assignments': dict(instance_id=scope['instance_id'], role_key=scope['role']), 'workflow_receipt_evidence': dict(task_id=bridge['task_id'])}
    if scope['revision'] == 1 and _local_history_successor(c, scope['instance_id'], fulfilled=True):
        selectors.pop('workflow_current_assignments')
    actual = dict(((table, c.select(table, **fields)) for table, fields in selectors.items()))
    if request['status'] == 'pending':
        _local_validate_review_dispatch_storage(c, request_id=request['id'])
        if receipts or any(actual.values()):
            raise ValueError(code)
        return
    completed = _local_completed_proof(c, request, scope)
    if len(receipts) != 1 or completed is None:
        raise ValueError(code)
    expected = _receipt_rows(request, scope, *completed)
    if request['status'] != expected['workflow_review_receipts']['outcome'] or not _same_value(receipts, [expected['workflow_review_receipts']]) or any((not _same_value(actual[table], [expected[table]]) for table in selectors)):
        raise ValueError(code)

def _local_validate_stage(c: _Candidate, body: dict, *, outbox: dict, bridge: dict) -> dict:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
    code = 'workflow_review_storage_corrupt'
    stage = body['stage']
    if type(stage) is not dict or set(stage) != set(('command', 'request_digest', 'scope', 'outbox_id', 'session_id')):
        raise ValueError(code)
    command = parse_stage(stage['command'])
    request = c.one('workflow_review_requests', bridge['request_id'])
    scope = c.json(request['request_scope_bytes'], request['request_scope_digest'])
    before = body['before']
    if command['format'] != f'workflow-review-stage-command@{review_family(scope)}' or not _same_value(stage['scope'], scope) or scope['kind'] != 'agent' or (command['outcome'] not in scope['outcomes']) or (command['request_id'] != request['id']) or (command['expected_revision'] != scope['revision']) or (command['request_scope_digest'] != request['request_scope_digest']) or (stage['request_digest'] != digest(c.canonical(command))) or (stage['outbox_id'] != outbox['id']) or (stage['session_id'] != before['bridge']['session_id']) or (scope['principal'] != bridge['assigned_principal']) or (not _same_value(scope['assignment_generation'], bridge['assignment_generation'])) or (not stage['session_id']) or (before['bridge']['result_id'] is not None) or (before['outbox']['host_launch_started'] != 1) or (before['outbox']['state'] not in ('claimed', 'running')) or (not _same_value(before, body['after'])) or (body['result'] is not None) or (body['terminal_evidence'] is not None):
        raise ValueError(code)
    matches = c.stage_keys(org_slug=body['org_slug'], principal=scope['principal'], operation_key=command['operation_key'])
    if len(matches) != 1 or matches[0]['operation_id'] != bridge['operation_id']:
        raise ValueError(code)
    return stage

def _local_validate_review_dispatch_storage(c: _Candidate, *, request_id: str) -> None:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
    code = 'workflow_review_storage_corrupt'
    bridges = c.select('workflow_request_task_bridges', request_id=request_id)
    operations = c.select('workflow_dispatch_operations', request_id=request_id)
    outboxes = c.select('workflow_dispatch_outbox', request_id=request_id)
    if len(bridges) != 1 or len(operations) != 1 or len(outboxes) != 1:
        raise ValueError(code)
    bridge, operation, outbox = (bridges[0], operations[0], outboxes[0])
    retained = {'outbox': outbox, 'bridge': bridge}
    _validate_projection(retained, outbox=outbox, bridge=bridge)
    initial = dict(outbox=dict(outbox, state='queued', claim_token=None, claim_owner=None, host_launch_started=0, host_execution_id=None, last_error=None, updated_at=outbox['created_at']), bridge=dict(bridge, state='queued', session_id=None, result_id=None))
    events = c.ordered(c.select('workflow_dispatch_events', operation_id=operation['id']), 'event_seq')
    previous = None
    accepted = None
    cancelled = False
    staged = False
    for seq, event in enumerate(events, start=1):
        body = c.json(event['event_bytes'], event['event_digest'])
        _validate_projection(body['before'], outbox=outbox, bridge=bridge)
        _validate_projection(body['after'], outbox=outbox, bridge=bridge)
        is_stage = body['event_kind'] == 'review_staged'
        keys = set(('format', 'org_slug', 'operation_id', 'request_id', 'task_id', 'event_seq', 'event_kind', 'previous_digest', 'before', 'after', 'terminal_evidence', 'result'))
        if set(body) != keys | (set(('stage',)) if is_stage else set()) or body['format'] != ('workflow-review-stage@1' if is_stage else 'workflow-review-dispatch-event@1') or body['org_slug'] != operation['org_slug'] or (body['operation_id'] != operation['id']) or (body['request_id'] != request_id) or (body['task_id'] != bridge['task_id']) or (body['event_kind'] != event['event_kind']) or (body['event_seq'] != seq) or (type(body['event_seq']) is not int) or (event['event_seq'] != seq) or (event['id'] != f"{operation['id']}:{seq}") or (body['before']['outbox']['state'] != event['state_before']) or (body['after']['outbox']['state'] != event['state_after']) or (body['previous_digest'] != (None if previous is None else previous[0]['event_digest'])) or (not _same_value(body['before'], initial if previous is None else previous[1]['after'])):
            raise ValueError(code)
        if is_stage:
            if cancelled or staged:
                raise ValueError(code)
            _local_validate_stage(c, body, outbox=outbox, bridge=bridge)
            staged = True
        else:
            _validate_transition(body, cancelled=cancelled)
        cancelled = cancelled or body['event_kind'] == 'cancel_requested'
        if body['event_kind'] == 'callback_recorded':
            if accepted is not None or not isinstance(body['result'], dict) or set(body['result']) != set(('id', 'record', 'digest', 'accepted', 'disposition')) or (type(body['result']['id']) is not int) or (type(body['result']['accepted']) is not int) or (body['result']['accepted'] != 1):
                raise ValueError(code)
            accepted = body['result']
            result = c.one('task_results', accepted['id'])
            if not _same_value(accepted['record'], result) or accepted['digest'] != digest(c.canonical(result)) or result['task_id'] != bridge['task_id'] or (result['agent'] != bridge['assigned_principal']) or (result['session_id'] != body['after']['bridge']['session_id']) or (body['after']['bridge']['result_id'] != str(result['id'])):
                raise ValueError(code)
            if accepted['disposition'] != ('accepted' if body['before']['outbox']['state'] == 'running' else 'pending_host_acknowledgment'):
                raise ValueError(code)
        if body['event_kind'] == 'completed' and (accepted is None or accepted['record']['status'] != 'completed' or (not _same_value(body['terminal_evidence'], {'host_quiescent': True})) or (not _same_value(body['result'], accepted)) or (not body['after']['outbox']['host_execution_id'])):
            raise ValueError(code)
        previous = (event, body)
    if previous is None:
        if not _same_value(retained, initial):
            raise ValueError(code)
    elif not _same_value(previous[1]['after'], retained):
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

def _local_responses(c: _Candidate, graph: dict) -> list[dict]:
    return list((c.one('workflow_review_requests', qid) for qid in graph['request_ids']))

def _local_event(c: _Candidate, row: dict, proof: dict, graph: dict, created_at: str) -> dict:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
    grant_pin = proof['pins']['authorization']
    grant = c.json(c.unhex(grant_pin['bytes_hex']), grant_pin['digest'])
    if not set(ACTIONS) <= set(grant['original_request']['allowed_actions']):
        raise ValueError('workflow_review_join_storage_corrupt')
    receipts, assignments = ([], [])
    for request in _local_responses(c, graph):
        receipt = c.select('workflow_review_receipts', request_id=request['id'])
        if len(receipt) != 1 or request['status'] != 'approved' or receipt[0]['outcome'] != 'approved':
            raise ValueError('workflow_review_join_storage_corrupt')
        receipts.append(_hex(receipt[0]))
        scope = c.json(request['request_scope_bytes'], request['request_scope_digest'])
        if scope['kind'] == 'agent':
            selected = c.select('workflow_current_assignments', instance_id=row['instance_id'], role_key=scope['role'])
            if len(selected) != 1:
                raise ValueError('workflow_review_join_storage_corrupt')
            assignments.append(selected[0])
    return dict(format='workflow-review-join@3' if row['revision'] == 2 else 'workflow-review-join@2' if proof['format'] == 'workflow-author-handoff@2' else 'workflow-review-join@1', event_kind='joined', org_slug=row['org_slug'], instance_id=row['instance_id'], round_id=graph['round_id'], submission_id=row['submission_id'], revision=row['revision'], submission_digest=row['candidate_digest'], operation=dict(principal=SERVICE, operation_key=graph['round_id'], action='approve-planning-input'), pins=proof['pins'], admission_authority=dict(proof['admission_authority'], actions=ACTIONS), receipts=receipts, assignments=assignments, created_at=created_at)

def _local_validate_collection(c: _Candidate, row: dict, proof: dict, graph: dict) -> str:
    """Candidate-only selected historical comparisons; full workflow validation is separate."""
    from runtime.workflows.review_dispatch import _same_value
    code = 'workflow_review_join_storage_corrupt'
    round_ = c.one('workflow_rounds', graph['round_id'])
    instance = c.one('workflow_instances', row['instance_id'])
    requests = _local_responses(c, graph)
    pending = any((q['status'] == 'pending' for q in requests))
    objection = any((q['status'] == 'changes_requested' for q in requests))
    events = c.select('workflow_events', instance_id=row['instance_id'], revision=row['revision'], event_kind='joined')
    replays = c.union('workflow_operation_replays', c.select('workflow_operation_replays', org_slug=row['org_slug'], principal=SERVICE, operation_key=graph['round_id']), c.rows('workflow_operation_replays', 'effect_id', _id('workflow-event', row['instance_id'], row['revision'], 'joined')))
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
    if not _same_value(body, expected) or not _same_value(event, dict(id=_id('workflow-event', row['instance_id'], row['revision'], 'joined'), instance_id=row['instance_id'], revision=row['revision'], event_kind='joined', event_bytes=raw, event_digest=digest(raw), created_at=body['created_at'])) or (not _same_value(replays, [_replay(row, graph, event)])):
        raise ValueError(code)
    return 'approved'

def _local_activation(c: _Candidate, activation_id: str, *, actor: str) -> dict:
    """Selected activation comparisons after private candidate acquisition; no full-reader certificate."""
    if not c.conn.in_transaction:
        raise WorkflowActivationError('workflow_activation_storage_corrupt')
    operations = c.rows('workflow_activation_operations', 'activation_id', activation_id)
    if not operations:
        raise WorkflowActivationError('workflow_activation_not_found')
    if len(operations) != 1 or operations[0]['org_slug'] != c.org or operations[0]['principal'] != actor:
        raise WorkflowActivationError('workflow_activation_storage_corrupt')
    operation = operations[0]
    try:
        activation = dict((list(c.select('workflow_activations', id=activation_id)) + [None])[0])
        instance = dict((list(c.select('workflow_instances', id=activation['instance_id'])) + [None])[0])
        intent = dict((list(c.select('workflow_draft_dispatch_intents', activation_id=activation_id, attempt_sequence=1)) + [None])[0])
        binding = dict((list(c.select('workflow_binding_snapshots', id=intent['binding_snapshot_id'])) + [None])[0])
        authorization = dict((list(c.select('workflow_authorization_revisions', id=binding['authorization_revision_id'])) + [None])[0])
        context = dict((list(c.select('workflow_contexts', id=intent['context_id'])) + [None])[0])
        grant = c.json(authorization['authority_bytes'])
        request = parse_request(c.json(intent['request_bytes']))
        frozen = request.model_dump(by_alias=True)
        namespace = f"org/{c.org}/workflow-instance/{instance['id']}"
        template = c.template(identity_id=request.template.identity_id, version=request.template.version, expected_version_id=activation['template_version_id'])
        timestamp = datetime.fromisoformat(activation['created_at'])
        if operation['request_digest'] != intent['request_digest'] or intent['request_digest'] != activation['request_digest'] or activation['activated_by'] != actor or (intent['admission_principal'] != actor) or (operation['operation_key'] != intent['operation_key']) or (request.operation_key != intent['operation_key']) or (instance['owner_principal'] != actor) or (authorization['namespace'] != namespace) or (authorization['revision'] != 1) or (activation['activation_revision'] != 1) or (authorization['source_pin'] != template.source_pin) or (authorization['created_at'] != activation['created_at']) or (binding['created_at'] != activation['created_at']) or (intent['created_at'] != activation['created_at']) or (timestamp.tzinfo is None) or (timestamp.utcoffset().total_seconds() != 0) or (intent['admission_kind'] != 'initial') or (intent['attempt_sequence'] != 1) or (intent['assignment_generation'] != 1) or (intent['predecessor_intent_id'] is not None) or (context['source_kind'] != 'founder-activation') or (context['source_id'] != instance['id']) or (instance['id'] != _id('workflow-instance', c.org, request.instance_id)) or (activation_id != _id('workflow-activation', instance['id'], 1, operation['request_digest'])) or (template.definition_digest != request.template.definition_digest) or (template.identity_id != activation['template_identity_id']) or (template.namespace != intent['authority_namespace']) or (not template.namespace.startswith(f'org/{c.org}/team/')) or (request.authority.namespace != f'org/{c.org}') or (request.authority.generation != intent['authority_generation']) or (request.authority.snapshot_digest != intent['authority_digest']) or (digest(intent['request_bytes']) != operation['request_digest']) or (c.canonical(frozen) != intent['request_bytes']):
            raise ValueError('closure')
        expected_grant = _authorization(org_slug=c.org, instance_id=instance['id'], activation_id=activation_id, task_id=instance['root_task_id'], request=frozen, request_digest=operation['request_digest'], template=template, timestamp=activation['created_at'])
        bound = _binding(instance_id=instance['id'], activation_id=activation_id, authorization_id=authorization['id'], template=template, request=frozen)
        stored_context = c.json(context['context_bytes'])
        journal = list(((r['snapshot_bytes'],) for r in c.publication(request.authority.namespace, request.authority.generation, request.authority.snapshot_digest)))
        snapshot_bytes = c.canonical(stored_context['authority_snapshot'])
        if len(journal) != 1 or bytes(journal[0][0]) != snapshot_bytes or digest(snapshot_bytes) != request.authority.snapshot_digest:
            raise ValueError('closure')
        WorkflowActivationStore._roles(None, request, stored_context['authority_snapshot'], template)
        inputs = stored_context['inputs']
        if not isinstance(inputs, list) or len(inputs) != len(request.inputs):
            raise ValueError('closure')
        for item, source in zip(inputs, request.inputs, strict=True):
            if not isinstance(item, dict) or set(item) != set(('pin', 'bytes_base64')) or item['pin'] != source.model_dump() or (not set(source.recipients) <= set(frozen['bindings'])) or (len(set(source.recipients)) != len(source.recipients)):
                raise ValueError('closure')
            content = c.unbase64(item['bytes_base64'])
            if digest(content) != source.sha256 or base64.b64encode(content).decode('ascii') != item['bytes_base64']:
                raise ValueError('closure')
        expected_context = activation_context(org_slug=c.org, request=frozen, snapshot=stored_context['authority_snapshot'], template=template, inputs=inputs, authorization=expected_grant, authorization_id=authorization['id'], binding=bound, binding_id=binding['id'], intent_id=intent['id'])
        author_role = _author_role(frozen, template)
        task_scope = dict(assigned_agent=frozen['bindings'][author_role]['principal'], team=frozen['bindings'][author_role]['team'], brief=_task_brief(frozen, inputs, template))
        if c.canonical(expected_grant) != authorization['authority_bytes'] or c.canonical(bound) != binding['binding_bytes'] or c.canonical(expected_context) != context['context_bytes'] or (len(context['context_bytes']) > CONTEXT_LIMIT) or (c.canonical(task_scope) != intent['task_scope_bytes']) or (binding['id'] != _id('workflow-binding', instance['id'], digest(binding['binding_bytes']))) or (context['id'] != _id('workflow-context', instance['id'], digest(context['context_bytes']))) or (authorization['id'] != _id('workflow-authorization', instance['id'], 1, digest(authorization['authority_bytes']))):
            raise ValueError('closure')
        receipt = dict(activation_id=activation_id, instance_id=instance['id'], instance_reference=request.instance_id, activation_revision=activation['activation_revision'], root_task_id=instance['root_task_id'], intent_id=intent['id'], template=grant['template'], authority=grant['authority'], bindings=frozen['bindings'], eligible_replacements=frozen['eligible_replacements'], allowed_actions=frozen['allowed_actions'], scope_digest=digest(c.canonical(frozen['scope'])), context_digest=context['context_digest'], activated_by=grant['actor'], created_at=grant['created_at'], original_request_digest=operation['request_digest'], replayed=False)
        if isinstance(request, DocumentActivationRequest):
            receipt['format'] = frozen['format'].replace('activation-request', 'activation-receipt')
        return receipt
    except (ValueError, TypeError, KeyError, AttributeError, sqlite3.DatabaseError) as exc:
        raise WorkflowActivationError('workflow_activation_storage_corrupt') from exc
