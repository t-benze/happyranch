"""Unmounted SELECT-only retained native source identities.

Caller independently admits the COMPLETE native/J structure and owns one
coherent raw-value SQLite transaction/connection/lock throughout. These are
TODAY'S task rows and retained original result/session-start identities, never
original source admission, a claimed result, replayed J or current permission.
No transaction control, native writer, tracker reconstruction or mount occurs.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime

from runtime.workflows.coding_authority_history import (
    CodingAuthorityHistoryError, _Budget, _CALL_ROW_LIMIT, _SetupHistoryReader, _token,
)
from runtime.workflows.coding_capability_history import (
    CodingCapabilityHistory, CodingCapabilityHistoryError, _history,
)
from runtime.workflows.coding_saved_records import (
    CodingSavedRecordError, _agent, _closed, _digest, _integer, _oid,
    _operation, _paths, _release, _repository,
)

_REFUSAL = 'coding_source_history_invalid'
_SOURCE_LIMIT = 20  # INCLUDING source; parent_task_id only, never revisit_of.
_VALUE_LIMIT = 1048576
_TASK_COLUMNS = (
    'id', 'status', 'assigned_agent', 'team', 'brief', 'task_type',
    'revision_count', 'created_at', 'updated_at', 'completed_at',
    'parent_task_id', 'final_output_summary', 'final_output_dir', 'executor_pid',
    'block_kind', 'note', 'orchestration_step_count', 'cancelled_at',
    'revisit_of_task_id', 'last_heartbeat', 'session_timeout_seconds',
    'blocked_on_job_ids', 'dispatched_from_thread_id', 'active_chain',
    'active_fanout', 'current_session_id', 'zombie_flagged_at',
)
_TASK_TYPES = (
    ('text',), ('text',), ('null', 'text'), ('text',), ('text',), ('text',),
    ('integer',), ('text',), ('text',), ('null', 'text'), ('null', 'text'),
    ('null', 'text'), ('null', 'text'), ('null', 'integer'), ('null', 'text'),
    ('null', 'text'), ('null', 'integer'), ('null', 'text'), ('null', 'text'),
    ('null', 'text'), ('null', 'integer'), ('null', 'text'), ('null', 'text'),
    ('null', 'text'), ('null', 'text'), ('null', 'text'), ('null', 'text'),
)
_RESULT_COLUMNS = (
    'id', 'task_id', 'agent', 'session_id', 'status', 'output_summary',
    'decision_json', 'confidence_score', 'learnings', 'risks_flagged',
    'duration_seconds', 'token_count', 'estimated_cost', 'output_dir',
    'created_at', 'waiting_on_job_ids', 'verdict', 'local_ci',
)
_RESULT_TYPES = (
    ('integer',), ('text',), ('text',), ('text',), ('text',), ('null', 'text'),
    ('null', 'text'), ('null', 'integer'), ('null', 'text'), ('null', 'text'),
    ('null', 'integer'), ('null', 'integer'), ('null', 'real'), ('null', 'text'),
    ('text',), ('null', 'text'), ('null', 'text'), ('null', 'text'),
)
_AUDIT_COLUMNS = ('id', 'task_id', 'agent', 'action', 'payload', 'timestamp')
_AUDIT_TYPES = (('integer',), ('text',), ('text',), ('text',), ('null', 'text'), ('text',))


class CodingSourceHistoryError(ValueError):
    """Categorical refusal without private SQL, paths, rows or partial records."""


@dataclass(frozen=True)
class NativeSourceColumn:
    """Original typeof/value; TEXT is ALL strict UTF8 bytes, REAL native float."""

    name: str
    storage_class: str
    value: bytes | int | float | None


@dataclass(frozen=True)
class NativeSourceRecord:
    """Complete fixed native column order, including genuine NULL/empty bytes."""

    columns: tuple[NativeSourceColumn, ...]

    def _value(self, name: str) -> bytes | int | float | None:
        return next(column.value for column in self.columns if column.name == name)

    def _text(self, name: str) -> str | None:
        value = self._value(name)
        return None if value is None else value.decode('utf8')


@dataclass(frozen=True)
class CodingSourceNativeRecords:
    """Exactly five retained-fact fields; current rows supply no old admission."""

    capability_history: CodingCapabilityHistory
    source_task_record: NativeSourceRecord
    ancestor_task_records: tuple[NativeSourceRecord, ...]
    session_start_audit_record: NativeSourceRecord
    same_session_result_records: tuple[NativeSourceRecord, ...]


def _check(condition: bool) -> None:
    if not condition:
        raise CodingSourceHistoryError(_REFUSAL)


def _projection(columns: tuple[str, ...], allowed: tuple[tuple[str, ...], ...],
                table: str, selection: str) -> str:
    # Called ONLY at module definition with the three literal native schemas
    # below. No caller table/column/filter/SQL input reaches this construction.
    # SUM uses byte lengths even for multibyte or embedded-NUL TEXT. All new
    # row variable bytes must fit remaining call bytes BEFORE Python fetch;
    # invalid/oversized data yields NULL with ORIGINAL typeof still projected.
    size = '+'.join(f"CASE WHEN typeof({name}) IN ('text','blob') "
                    f"THEN length(CAST({name} AS BLOB)) ELSE 0 END" for name in columns)
    fields = []
    for name, types in zip(columns, allowed):
        choices = ','.join("'" + kind + "'" for kind in types)
        length = f' AND length(CAST({name} AS BLOB))<={_VALUE_LIMIT}' if 'text' in types else ''
        value = f'CAST({name} AS BLOB)' if 'text' in types else name
        fields.append(f'CASE WHEN ? > 0 AND _row_bytes<=? AND typeof({name}) IN ({choices})'
                      f'{length} THEN {value} ELSE NULL END')
    fields.extend(f'typeof({name})' for name in columns)
    return ('WITH native AS (SELECT ' + ','.join(columns) + ',' + size
            + ' AS _row_bytes FROM ' + table + ' ' + selection + ') SELECT '
            + ','.join(fields) + ' FROM native')


_TASK_COUNT_SQL = 'SELECT count(*) FROM (SELECT 1 FROM tasks WHERE id=? LIMIT 2)'
_TASK_SQL = _projection(_TASK_COLUMNS, _TASK_TYPES, 'tasks', 'WHERE id=? LIMIT 1')
_AUDIT_SQL = _projection(_AUDIT_COLUMNS, _AUDIT_TYPES, 'audit_log',
    "WHERE task_id=? AND action='session_start' AND id>=? ORDER BY id LIMIT ?")
_RESULT_SQL = _projection(_RESULT_COLUMNS, _RESULT_TYPES, 'task_results',
    'WHERE task_id=? AND session_id=? AND id>=? ORDER BY id LIMIT ?')


def _native_rows(conn: sqlite3.Connection, sql: str, args: tuple,
                 columns: tuple[str, ...], types: tuple[tuple[str, ...], ...],
                 budget: _Budget) -> Iterator[NativeSourceRecord]:
    cursor = conn.cursor()
    cursor.row_factory = None
    try:
        # Page size is one. The relevant inventory advances by native INTEGER
        # ID with bounded keyset pages, not OFFSET/fetchall. Every next candidate
        # receives a NEW remaining-byte gate; no page can overspend the budget
        # through multiple buffered payload rows with a stale remaining value.
        cursor.execute(sql, args + (_CALL_ROW_LIMIT - budget.accepted_rows,
                                    budget.remaining_bytes) * len(columns))
        while (row := cursor.fetchone()) is not None:
            budget.candidate()  # before type/grammar/conflict rejection
            budget.step()
            values = budget.native_row(row, types)
            yield NativeSourceRecord(tuple(NativeSourceColumn(name, storage, value)
                for name, storage, value in zip(columns, row[len(columns):], values)))
    finally:
        cursor.close()


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        key.encode('utf8')
        _check(key not in result)
        result[key] = value
    return result


def _constant(_: str) -> None:
    raise CodingSourceHistoryError(_REFUSAL)


def _json(raw: bytes, budget: _Budget) -> object:
    # Bound already applied to raw native bytes before fetch. Retain raw bytes
    # in the result; parsing never rewrites whitespace/key order/UTF8 spelling.
    value = json.loads(raw.decode('utf8'), object_pairs_hook=_pairs, parse_constant=_constant)
    pending = [value]
    while pending:
        item = pending.pop()
        budget.step()
        if type(item) is str:
            item.encode('utf8')
        elif type(item) is float:
            _check(math.isfinite(item))  # includes overflow such as 1e999
        elif type(item) is dict:
            pending.extend(item.values())
        elif type(item) is list:
            pending.extend(item)
    return value


def _timestamp(value: str | None, *, nullable: bool = False) -> None:
    if value is None:
        _check(nullable)
        return
    _check(re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?'
                       r'(?:Z|[+-]\d{2}:\d{2})', value) is not None)
    _check(datetime.fromisoformat(value.replace('Z', '+00:00')).utcoffset() is not None)


def _optional_token(value: str | None) -> None:
    if value is not None:
        _token(value)


def _native_json(record: NativeSourceRecord, names: tuple[str, ...], budget: _Budget) -> None:
    for name in names:
        raw = record._value(name)
        if raw is not None:
            _json(raw, budget)


def _task(conn: sqlite3.Connection, identity: str, budget: _Budget) -> NativeSourceRecord:
    # Bounded independent uniqueness probe precedes the singleton payload
    # fetch. LIMIT1 below cannot mask conflicts; caller holds this snapshot.
    cursor = conn.cursor()
    cursor.row_factory = None
    try:
        cursor.execute(_TASK_COUNT_SQL, (identity,))
        count = cursor.fetchone()
        budget.candidate()
        budget.step()
        _check(count == (1,))
    finally:
        cursor.close()
    rows = []
    for row in _native_rows(conn, _TASK_SQL, (identity,), _TASK_COLUMNS, _TASK_TYPES, budget):
        _check(not rows)
        rows.append(row)
    _check(len(rows) == 1)
    row = rows[0]
    _check(row._text('id') == identity)
    _token(identity)
    _check(row._text('status') in ('pending', 'in_progress', 'escalated', 'completed',
                                 'failed', 'cancelled', 'superseded'))
    _check(row._text('task_type') in ('task', 'subtask'))
    _token(row._text('team'))
    for name in ('assigned_agent', 'parent_task_id', 'revisit_of_task_id',
                 'dispatched_from_thread_id', 'current_session_id'):
        _optional_token(row._text(name))
    for name in ('created_at', 'updated_at'):
        _timestamp(row._text(name))
    for name in ('completed_at', 'cancelled_at', 'last_heartbeat', 'zombie_flagged_at'):
        _timestamp(row._text(name), nullable=True)
    _native_json(row, ('blocked_on_job_ids', 'active_chain', 'active_fanout'), budget)
    return row


def _inventory(conn: sqlite3.Connection, sql: str, args: tuple,
               columns: tuple[str, ...], types: tuple[tuple[str, ...], ...],
               budget: _Budget) -> Iterator[NativeSourceRecord]:
    # Singleton keyset pages bound payloads with a fresh remaining-byte gate.
    # An extra candidate at the call row ceiling is fetched only to refuse;
    # no prefix result escapes. IDs must advance, including malformed rows.
    after = -9223372036854775808
    while True:
        budget.step()
        page = list(_native_rows(conn, sql, args + (after, 1), columns, types, budget))
        if not page:
            return
        _check(len(page) == 1)
        row = page[0]
        identity = row._value('id')
        _check(type(identity) is int and identity >= after and identity > 0)
        yield row
        if identity == 9223372036854775807:
            return
        after = identity + 1


def _session_start(conn: sqlite3.Connection, issuer: dict,
                   budget: _Budget) -> NativeSourceRecord:
    selected = None
    # Scan the task's complete relevant start inventory, including foreign
    # agents: SQL agent filtering must not hide a conflicting same-SID row.
    for row in _inventory(conn, _AUDIT_SQL, (issuer['task_id'],), _AUDIT_COLUMNS, _AUDIT_TYPES, budget):
        _check(row._text('task_id') == issuer['task_id'] and row._text('action') == 'session_start')
        _token(row._text('agent'))
        _timestamp(row._text('timestamp'))
        payload = _json(row._value('payload'), budget)
        required = {'workspace', 'session_id', 'invocation_purpose', 'executor', 'model'}
        _check(type(payload) is dict and set(payload) in (required, required | {'reasoning_effort'}))
        _check(type(payload['workspace']) is str)
        _optional_token(payload['session_id'])
        _check(payload['invocation_purpose'] in ('manager_decision', 'worker_execution', 'unattributed', 'completion_recovery'))
        for key in ('executor', 'model'):
            _check(payload[key] is None or type(payload[key]) is str)
        if 'reasoning_effort' in payload:
            _check(type(payload['reasoning_effort']) is str)
        if payload['session_id'] == issuer['session_id']:
            _check(selected is None and row._text('agent') == issuer['principal']
                   and payload['invocation_purpose'] == 'manager_decision')
            selected = row
    _check(selected is not None)
    return selected


def _start_decision(row: NativeSourceRecord, capability: dict, budget: _Budget) -> None:
    _check(row._text('status') == 'completed' and row._value('waiting_on_job_ids') is None)
    decision = _closed(_json(row._value('decision_json'), budget), 'action workflow')
    _check(decision['action'] == 'workflow_start')
    command = decision['workflow']
    _check(type(command) is dict)
    tag = command.get('format')
    _check(tag in ('workflow-coding-start@1', 'workflow-coding-start@2'))
    _closed(command, 'format operation_key release mapping work implementer_override'
            + (' settings' if tag == 'workflow-coding-start@2' else ''))
    if tag == 'workflow-coding-start@2':
        settings = _closed(command['settings'], 'generation snapshot_digest')
        _integer(settings['generation'], 1)
        _check(settings['generation'] <= 9223372036854775807)
        _digest(settings['snapshot_digest'])
    _operation(command['operation_key'])
    _release(command['release'])
    mapping = _closed(command['mapping'], 'id generation digest')
    _token(mapping['id'])
    _integer(mapping['generation'], 1)
    _digest(mapping['digest'])
    work = _closed(command['work'], 'brief repository_id object_format base paths')
    _check(type(work['brief']) is str and 0 < len(work['brief'].encode('utf8')) <= 16384
           and '\x00' not in work['brief'])
    _repository(work['repository_id'])
    _check(work['object_format'] in ('sha1', 'sha256'))
    _oid(work['base'], work['object_format'])
    _paths(work['paths'])
    radius = capability['radius']
    _check(all(work[key] == radius[key] for key in ('repository_id', 'object_format', 'base', 'paths')))
    if command['implementer_override'] is not None:
        _agent(command['implementer_override'])
    # The WHOLE original decision/command remains in this exact native row.
    # Original callback/source capture, mapping/effective roles, action claim,
    # source_rule and current-use equality belong to later independent owners.


def _results(conn: sqlite3.Connection, capability: dict,
             budget: _Budget) -> tuple[NativeSourceRecord, ...]:
    issuer = capability['issuer']
    selected = None
    records = []
    for row in _inventory(conn, _RESULT_SQL, (issuer['task_id'], issuer['session_id']),
                          _RESULT_COLUMNS, _RESULT_TYPES, budget):
        _check(row._text('task_id') == issuer['task_id'] and row._text('agent') == issuer['principal']
               and row._text('session_id') == issuer['session_id'])
        _token(row._text('status'))
        _timestamp(row._text('created_at'))
        # Native learnings is nullable free TEXT, inserted unchanged by its
        # owner. Its storage/UTF8/raw-byte gates still apply to EVERY result.
        _native_json(row, ('decision_json', 'risks_flagged', 'waiting_on_job_ids', 'local_ci'), budget)
        if row._value('id') == issuer['result_id']:
            _check(selected is None)
            _start_decision(row, capability, budget)
            selected = row
        records.append(row)
    if issuer['result_id'] is not None:
        _check(selected is not None and capability['action'] == 'start-coding')
    # A genuine null selected ID does NOT prove no later callback exists.
    return tuple(records)


def _source_records(conn: sqlite3.Connection, expected_org_slug: str,
                    authorization_id: str, *, budget: _Budget | None = None, setups=None) -> CodingSourceNativeRecords:
    # Private call-local composition only; preserve the public five-field API.
    _check(type(conn) is sqlite3.Connection and conn.in_transaction and conn.text_factory is str)
    _check(type(expected_org_slug) is str
           and re.fullmatch(r'[a-z][a-z0-9-]{0,62}', expected_org_slug) is not None)
    _token(authorization_id)
    if budget is None:
        budget = _Budget()
    setups = setups if setups is not None else _SetupHistoryReader(conn, expected_org_slug, budget)
    history = _history(conn, expected_org_slug, authorization_id, budget=budget, setups=setups)
    capability = json.loads(history.selected.authority_bytes)
    _check(capability['level'] == 'source')
    issuer = capability['issuer']
    # _history binds the COMPLETE native lineage in source-to-selected
    # order. Its first source P terminates at authentic U; refinements
    # retain the same issuer/result but may narrow paths. The original
    # callback must match that initial P exactly, not the narrower child.
    budget.step()
    initial_source = json.loads(history.capabilities[0].authority_bytes)
    _check(initial_source['level'] == 'source' and initial_source['issuer'] == issuer)
    source = _task(conn, issuer['task_id'], budget)
    chain = [source]
    seen = {issuer['task_id']}
    parent = source._text('parent_task_id')
    while parent is not None:
        _check(len(chain) < _SOURCE_LIMIT and parent not in seen)
        seen.add(parent)
        row = _task(conn, parent, budget)
        _check(row._text('team') == source._text('team'))
        chain.append(row)
        parent = row._text('parent_task_id')
    start = _session_start(conn, issuer, budget)
    results = _results(conn, initial_source, budget)
    return CodingSourceNativeRecords(history, source, tuple(chain[1:]), start, results)


def read_source_native_records(conn: sqlite3.Connection, *, expected_org_slug: str,
                               authorization_id: str) -> CodingSourceNativeRecords:
    """Five immutable identity fields under one shared native/P/U budget.

    Fixed parameterized SELECTs only, exact prefetch typeof/byte gates,1MiB
    per variable value,16MiB call raw bytes,8192 fetched candidates/16384
    traversal steps; native ancestry20 including source, P64/setup1024.
    Raw byte limits are not total SQLite/process/decoded-copy memory bounds.
    Native result REAL must be finite; native NULL counters/free-text empty
    bytes remain exact and cannot supply current admissible defaults.

    The original source SID selects its unique authentic manager_decision
    audit and COMPLETE same-SID result inventory, not today's assignment or
    transient tracker. Today's complete parent-to-NULL rows remain distinct
    from historical capture/claim/replay. Entitled selection and complete
    schema admission are caller prerequisites, never permission flags here.
    """
    try:
        return _source_records(conn, expected_org_slug, authorization_id)
    except (sqlite3.Error, CodingSavedRecordError, CodingAuthorityHistoryError,
            CodingCapabilityHistoryError, ValueError, TypeError, KeyError,
            UnicodeError, RecursionError, OverflowError, AttributeError):
        raise CodingSourceHistoryError(_REFUSAL) from None
