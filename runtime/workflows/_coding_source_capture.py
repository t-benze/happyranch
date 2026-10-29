"""Private lossless fixed source-capture decoding; no writer or permission API."""
from __future__ import annotations

from runtime.infrastructure._workflow_reader import (
    _add as _reader_add,
    _append as _reader_append,
)

from runtime.infrastructure._workflow_reader import (
    _set as _reader_set,
    _tuple as _reader_tuple,
)

from runtime.infrastructure._workflow_reader import (
    _b64decode as _reader_b64decode,
    _b64encode as _reader_b64encode,
    _decode as _reader_decode,
    _encode as _reader_encode,
    _fromhex as _reader_fromhex,
    _reserve as _reader_reserve,
    _work as _reader_work,
)


import base64
import math
import re
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta

from runtime.workflows.coding_authority_history import _Budget, _token
from runtime.workflows.coding_saved_records import _canonical, _closed, _integer
from runtime.workflows.coding_source_history import (
    NativeSourceColumn, NativeSourceRecord, _RESULT_COLUMNS, _RESULT_TYPES,
    _SOURCE_LIMIT, _TASK_COLUMNS, _TASK_TYPES, _VALUE_LIMIT, _check, _json,
    _native_json, _optional_token, _start_decision, _timestamp,
)


_FIRST_BOUNDARY_COLUMNS = (
    'id', 'status', 'assigned_agent', 'team', 'brief', 'task_type',
    'revision_count', 'created_at', 'completed_at', 'parent_task_id',
    'final_output_summary', 'final_output_dir', 'executor_pid', 'block_kind',
    'note', 'orchestration_step_count', 'cancelled_at', 'revisit_of_task_id',
    'session_timeout_seconds', 'blocked_on_job_ids', 'dispatched_from_thread_id',
    'active_chain', 'active_fanout', 'current_session_id', 'zombie_flagged_at',
)


@dataclass(frozen=True)
class SourceCaptureOwner:
    table: str
    id: str | int


@dataclass(frozen=True)
class SourceCaptureClaim:
    task_id: str
    assigned_agent: str
    team: str
    current_session_id: str
    step: int
    active_chain: str | None
    active_fanout: str | None
    revision: int
    note: str | None
    status: str
    block_kind: str | None
    cancelled_at: str | None
    result_row_id: int


@dataclass(frozen=True)
class SourceCaptureBudgets:
    source_step: int
    source_revision: int
    session_timeout_seconds: int | None
    max_revise_rounds: int
    coding_attempt_limit: int
    coding_event_limit: int
    coding_revision_limit: int
    coding_same_principal_retry_limit: int


@dataclass(frozen=True)
class OriginalSourceCapture:
    """Full original audit and decoded observations, never a valid-run flag.

    The audit retains ALL native payload bytes, including noncanonical JSON.
    Decoded rows retain storage classes, signed integers, NULL/empty UTF8 and
    finite REAL bits. Immutable native joins belong to the containing reader.
    """

    audit_record: NativeSourceRecord
    phase: str
    org_slug: str
    source_admission_id: str | None
    native_owner: SourceCaptureOwner
    command_bytes: bytes
    task_record: NativeSourceRecord
    ancestor_records: tuple[NativeSourceRecord, ...]
    session_start_audit_id: int
    result_record: NativeSourceRecord | None
    callback_admission_audit_id: int | None
    native_claim: SourceCaptureClaim | None
    owner_invocation_id: str | None
    budgets: SourceCaptureBudgets | None
    observed_at: str

    @property
    def root_ancestor_id(self) -> str:
        return (self.ancestor_records[-1] if self.ancestor_records else self.task_record)._text('id')


def _copy_bytes(raw: bytes, budget: _Budget) -> None:
    # Conservative copy accounting in ADDITION to raw fetched audit bytes.
    # JSON objects/SQLite allocations are not a total process-memory bound.
    _check(len(raw) <= _VALUE_LIMIT)
    _reader_reserve(budget, len(raw))


def _base64(value: object, budget: _Budget) -> bytes:
    _reader_work(value, budget)
    _check(type(value) is str and len(value) <= 4 * ((_VALUE_LIMIT + 2) // 3))
    # Refuse a decoded copy that cannot fit before allocating it. Padding is
    # independently validated by strict decode AND exact padded round-trip.
    _check(value.isascii() and len(value) % 4 == 0)
    size = len(value) // 4 * 3 - (len(value) - len(value.rstrip('=')))
    _check(0 <= size <= _VALUE_LIMIT)
    _reader_reserve(budget, size)
    raw = _reader_b64decode(value, validate=True, budget=budget)
    _check(_reader_decode(_reader_b64encode(raw, budget=budget), 'ascii', budget=budget) == value)
    _copy_bytes(raw, budget)
    return raw


def _record(value: object, columns: tuple[str, ...], allowed: tuple[tuple[str, ...], ...],
            budget: _Budget) -> NativeSourceRecord:
    _reader_work(value, budget)
    record = _closed(value, 'columns', reader_budget=budget)
    members = record['columns']
    _check(type(members) is list and len(members) == len(columns))
    result = (_reader_reserve(budget, 64) or [])
    for member, name, choices in zip(members, columns, allowed):
        budget.step()
        item = _closed(member, 'name storage_class value', reader_budget=budget)
        kind, raw = (_reader_reserve(budget, 96) or (item['storage_class'], item['value']))
        _check(item['name'] == name and type(kind) is str and kind in choices)
        if kind == 'null':
            _check(raw is None)
        elif kind == 'integer':
            _check(type(raw) is int and -9223372036854775808 <= raw <= 9223372036854775807)
        elif kind == 'real':
            _check(type(raw) is str and re.fullmatch('[0-9a-f]{16}', raw) is not None)
            raw = struct.unpack('>d', _reader_fromhex(raw, budget=budget))[0]
            _check(math.isfinite(raw))
        else:
            # This complete native schema has TEXT, not BLOB columns. The
            # type table refuses blob substitutions before base64 decoding.
            raw = _base64(raw, budget)
            _check(_reader_encode(_reader_decode(raw, 'utf8', budget=budget), 'utf8', budget=budget) == raw)
        _reader_append(result, NativeSourceColumn(name, kind, raw), budget=budget)
    return NativeSourceRecord(_reader_tuple(result, budget=budget))


def _task_record(value: object, budget: _Budget) -> NativeSourceRecord:
    row = _record(value, _TASK_COLUMNS, _TASK_TYPES, budget)
    _token(row._text('id', budget=budget), budget=budget)
    _token(row._text('team', budget=budget), budget=budget)
    _check(row._text('status', budget=budget) in ('pending', 'in_progress', 'escalated', 'completed',
                                 'failed', 'cancelled', 'superseded'))
    _check(row._text('task_type', budget=budget) in ('task', 'subtask'))
    for name in ('assigned_agent', 'parent_task_id', 'revisit_of_task_id',
                 'dispatched_from_thread_id', 'current_session_id'):
        _optional_token(row._text(name, budget=budget))
    for name in ('created_at', 'updated_at'):
        _timestamp(row._text(name, budget=budget))
    for name in ('completed_at', 'cancelled_at', 'last_heartbeat', 'zombie_flagged_at'):
        _timestamp(row._text(name, budget=budget), nullable=True)
    _native_json(row, ('blocked_on_job_ids', 'active_chain', 'active_fanout'), budget)
    return row


def _claim(value: object, row: NativeSourceRecord, result_id: int, *, budget=None) -> SourceCaptureClaim:
    _reader_work((_reader_reserve(budget, 96) or (value, row)), budget)
    keys = ('task_id assigned_agent team current_session_id step active_chain active_fanout '
            'revision note status block_kind cancelled_at result_row_id')
    claim = _closed(value, keys, reader_budget=budget)
    for name in ('step', 'revision'):
        _integer(claim[name], reader_budget=budget)
    _integer(claim['result_row_id'], 1, reader_budget=budget)
    expected = (_reader_reserve(budget, 1728) or dict(task_id=row._text('id', budget=budget), assigned_agent=row._text('assigned_agent', budget=budget),
        team=row._text('team', budget=budget), current_session_id=row._text('current_session_id', budget=budget),
        step=row._value('orchestration_step_count'), active_chain=row._text('active_chain', budget=budget),
        active_fanout=row._text('active_fanout', budget=budget), revision=row._value('revision_count'),
        note=row._text('note', budget=budget), status=row._text('status', budget=budget), block_kind=row._text('block_kind', budget=budget),
        cancelled_at=row._text('cancelled_at', budget=budget), result_row_id=result_id))
    _check(claim == expected)
    return SourceCaptureClaim(**claim)


def _budgets(value: object, row: NativeSourceRecord, *, budget=None) -> SourceCaptureBudgets:
    _reader_work((_reader_reserve(budget, 96) or (value, row)), budget)
    budgets = _closed(value, 'source_step source_revision session_timeout_seconds max_revise_rounds '
                     'coding_attempt_limit coding_event_limit coding_revision_limit coding_same_principal_retry_limit', reader_budget=budget)
    for name, item in budgets.items():
        if name == 'session_timeout_seconds':
            if item is not None:
                _integer(item, 1, reader_budget=budget)
        else:
            _integer(item, reader_budget=budget)
    _check(budgets['source_step'] == row._value('orchestration_step_count')
           and budgets['source_revision'] == row._value('revision_count')
           and budgets['session_timeout_seconds'] == row._value('session_timeout_seconds'))
    for name, maximum in (('coding_attempt_limit', 64), ('coding_event_limit', 4096),
                          ('coding_revision_limit', 3), ('coding_same_principal_retry_limit', 1)):
        _check(budgets[name] <= maximum)
    return SourceCaptureBudgets(**budgets)


def _decode_capture(audit: NativeSourceRecord, org_slug: str, budget: _Budget) -> OriginalSourceCapture:
    _check(audit._text('action', budget=budget) == 'coding_source_admission')
    _integer(audit._value('id'), 1, reader_budget=budget)
    _timestamp(audit._text('timestamp', budget=budget))
    body = _closed(_json(audit._value('payload'), budget),
        'format phase org_slug source_admission_id native_owner command_bytes_base64 task_record '
        'ancestor_records session_start_audit_id result callback_admission_audit_id native_claim '
        'owner_invocation_id budgets observed_at', reader_budget=budget)
    _check(body['format'] == 'workflow-coding-source-admission@1'
           and body['org_slug'] == org_slug and type(body['phase']) is str
           and body['phase'] in ('callback', 'source'))
    phase = body['phase']
    _timestamp(body['observed_at'])
    _check(datetime.fromisoformat(body['observed_at'].replace('Z', '+00:00')).utcoffset() == timedelta(0))
    owner = _closed(body['native_owner'], 'table id', reader_budget=budget)
    if phase == 'callback':
        _check(owner['table'] == 'task_results')
        _integer(owner['id'], 1, reader_budget=budget)
    else:
        _check(type(owner['table']) is str and owner['table'] in (
            'workflow_authorization_revisions', 'workflow_coding_mappings'))
        _token(owner['id'], budget=budget)
    task = _task_record(body['task_record'], budget)
    ancestors = body['ancestor_records']
    _check(type(ancestors) is list and len(ancestors) < _SOURCE_LIMIT)
    chain = (_reader_reserve(budget, 192) or [task])
    seen = _reader_set((_reader_reserve(budget, 80) or (task._text('id', budget=budget),)), budget=budget)
    for item in ancestors:
        row = _task_record(item, budget)
        _check(row._text('id', budget=budget) not in seen and chain[-1]._text('parent_task_id', budget=budget) == row._text('id', budget=budget)
               and row._text('team', budget=budget) == task._text('team', budget=budget))
        _reader_add(seen, row._text('id', budget=budget), budget=budget)
        _reader_append(chain, row, budget=budget)
    _check(chain[-1]._value('parent_task_id') is None)
    _check(audit._text('task_id', budget=budget) == task._text('id', budget=budget)
           and audit._text('agent', budget=budget) == task._text('assigned_agent', budget=budget)
           and task._text('task_type', budget=budget) == 'task' and task._text('status', budget=budget) == 'in_progress')
    _token(task._text('assigned_agent', budget=budget), budget=budget)
    _token(task._text('current_session_id', budget=budget), budget=budget)
    for name in ('block_kind', 'cancelled_at', 'blocked_on_job_ids', 'active_chain', 'active_fanout'):
        _check(task._value(name) is None)
    for name in ('revision_count', 'orchestration_step_count'):
        _integer(task._value(name), reader_budget=budget)
    if task._value('session_timeout_seconds') is not None:
        _integer(task._value('session_timeout_seconds'), 1, reader_budget=budget)
    _integer(body['session_start_audit_id'], 1, reader_budget=budget)
    command = _base64(body['command_bytes_base64'], budget)
    _check(bool(command))
    result = None
    if body['result'] is not None:
        wrapper = _closed(body['result'], 'id record', reader_budget=budget)
        _integer(wrapper['id'], 1, reader_budget=budget)
        result = _record(wrapper['record'], _RESULT_COLUMNS, _RESULT_TYPES, budget)
        _check(result._value('id') == wrapper['id']
               and result._text('task_id', budget=budget) == task._text('id', budget=budget)
               and result._text('agent', budget=budget) == task._text('assigned_agent', budget=budget)
               and result._text('session_id', budget=budget) == task._text('current_session_id', budget=budget)
               and result._text('status', budget=budget) == 'completed' and result._value('waiting_on_job_ids') is None
               and result._value('decision_json') == command)
        _timestamp(result._text('created_at', budget=budget))
        # learnings remains native FREE TEXT; all other owner JSON is strict.
        _native_json(result, ('decision_json', 'risks_flagged', 'waiting_on_job_ids', 'local_ci'), budget)
        decision = _closed(_json(command, budget), 'action workflow', reader_budget=budget)
        _check(decision['action'] == 'workflow_start' and type(decision['workflow']) is dict
               and decision['workflow'].get('format') in ('workflow-coding-start@1', 'workflow-coding-start@2'))
        # Check the COMPLETE command grammar even in unselected inventory
        # entries. This self radius is syntax only; the containing reader
        # separately binds the selected command to the actual INITIAL P.
        _start_decision(result, (_reader_reserve(budget, 192) or {'radius': decision['workflow']['work']}), budget)
    claim, budgets = None, None
    if phase == 'callback':
        _check(result is not None and owner['id'] == result._value('id'))
        for name in ('source_admission_id', 'callback_admission_audit_id', 'native_claim',
                     'owner_invocation_id', 'budgets'):
            _check(body[name] is None)
    else:
        _token(body['source_admission_id'], budget=budget)
        _token(body['owner_invocation_id'], budget=budget)
        budgets = _budgets(body['budgets'], task, budget=budget)
        if result is None:
            _check(body['callback_admission_audit_id'] is None and body['native_claim'] is None)
            original = _json(command, budget)
            _check(type(original) is dict and _canonical(original, reader_budget=budget) == command)
        else:
            _check(owner['table'] == 'workflow_authorization_revisions')
            _integer(body['callback_admission_audit_id'], 1, reader_budget=budget)
            claim = _claim(body['native_claim'], task, result._value('id'), budget=budget)
    _reader_work((_reader_reserve(budget, 176) or (audit, body, chain, command, result, claim, budgets)), budget)
    return OriginalSourceCapture(audit, phase, org_slug, body['source_admission_id'],
        SourceCaptureOwner(**owner), command, task, _reader_tuple(chain[1:], budget=budget), body['session_start_audit_id'],
        result, body['callback_admission_audit_id'], claim, body['owner_invocation_id'], budgets,
        body['observed_at'])


def _same_record(left: NativeSourceRecord, right: NativeSourceRecord, *, first_boundary: bool = False, budget=None) -> None:
    _reader_work((_reader_reserve(budget, 96) or (left, right)), budget)
    _check(len(left.columns) == len(right.columns))
    if first_boundary:
        _check(_reader_tuple((column.name for column in left.columns), budget=budget) == _TASK_COLUMNS
               and len(_FIRST_BOUNDARY_COLUMNS) == 25
               and _reader_set(_FIRST_BOUNDARY_COLUMNS, budget=budget) == _reader_set(_TASK_COLUMNS, budget=budget) - _reader_set((_reader_reserve(budget, 96) or ('updated_at', 'last_heartbeat')), budget=budget))
    for before, after in zip(left.columns, right.columns):
        _check(before.name == after.name)
        # BOTH independently decoded timestamp observations remain retained.
        # Even NULL/type equality is not required for these two observations
        # at C0->C1; native timestamp validity IS required by each decoder.
        if first_boundary and before.name in ('last_heartbeat', 'updated_at'):
            continue
        _check(before.storage_class == after.storage_class)
        if before.storage_class == 'real':
            _check(struct.pack('>d', before.value) == struct.pack('>d', after.value))
        else:
            _check(before.value == after.value)
