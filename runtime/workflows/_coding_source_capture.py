"""Private lossless fixed source-capture decoding; no writer or permission API."""
from __future__ import annotations

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
    _check(len(raw) <= _VALUE_LIMIT and len(raw) <= budget.remaining_bytes)
    budget.consumed += len(raw)


def _base64(value: object, budget: _Budget) -> bytes:
    _check(type(value) is str and len(value) <= 4 * ((_VALUE_LIMIT + 2) // 3))
    # Refuse a decoded copy that cannot fit before allocating it. Padding is
    # independently validated by strict decode AND exact padded round-trip.
    _check(value.isascii() and len(value) % 4 == 0)
    size = len(value) // 4 * 3 - (len(value) - len(value.rstrip('=')))
    _check(0 <= size <= _VALUE_LIMIT and size <= budget.remaining_bytes)
    raw = base64.b64decode(value, validate=True)
    _check(base64.b64encode(raw).decode('ascii') == value)
    _copy_bytes(raw, budget)
    return raw


def _record(value: object, columns: tuple[str, ...], allowed: tuple[tuple[str, ...], ...],
            budget: _Budget) -> NativeSourceRecord:
    record = _closed(value, 'columns')
    members = record['columns']
    _check(type(members) is list and len(members) == len(columns))
    result = []
    for member, name, choices in zip(members, columns, allowed):
        budget.step()
        item = _closed(member, 'name storage_class value')
        kind, raw = item['storage_class'], item['value']
        _check(item['name'] == name and type(kind) is str and kind in choices)
        if kind == 'null':
            _check(raw is None)
        elif kind == 'integer':
            _check(type(raw) is int and -9223372036854775808 <= raw <= 9223372036854775807)
        elif kind == 'real':
            _check(type(raw) is str and re.fullmatch('[0-9a-f]{16}', raw) is not None)
            raw = struct.unpack('>d', bytes.fromhex(raw))[0]
            _check(math.isfinite(raw))
        else:
            # This complete native schema has TEXT, not BLOB columns. The
            # type table refuses blob substitutions before base64 decoding.
            raw = _base64(raw, budget)
            _check(raw.decode('utf8').encode('utf8') == raw)
        result.append(NativeSourceColumn(name, kind, raw))
    return NativeSourceRecord(tuple(result))


def _task_record(value: object, budget: _Budget) -> NativeSourceRecord:
    row = _record(value, _TASK_COLUMNS, _TASK_TYPES, budget)
    _token(row._text('id'))
    _token(row._text('team'))
    _check(row._text('status') in ('pending', 'in_progress', 'escalated', 'completed',
                                 'failed', 'cancelled', 'superseded'))
    _check(row._text('task_type') in ('task', 'subtask'))
    for name in ('assigned_agent', 'parent_task_id', 'revisit_of_task_id',
                 'dispatched_from_thread_id', 'current_session_id'):
        _optional_token(row._text(name))
    for name in ('created_at', 'updated_at'):
        _timestamp(row._text(name))
    for name in ('completed_at', 'cancelled_at', 'last_heartbeat', 'zombie_flagged_at'):
        _timestamp(row._text(name), nullable=True)
    _native_json(row, ('blocked_on_job_ids', 'active_chain', 'active_fanout'), budget)
    return row


def _claim(value: object, row: NativeSourceRecord, result_id: int) -> SourceCaptureClaim:
    keys = ('task_id assigned_agent team current_session_id step active_chain active_fanout '
            'revision note status block_kind cancelled_at result_row_id')
    claim = _closed(value, keys)
    for name in ('step', 'revision'):
        _integer(claim[name])
    _integer(claim['result_row_id'], 1)
    expected = dict(task_id=row._text('id'), assigned_agent=row._text('assigned_agent'),
        team=row._text('team'), current_session_id=row._text('current_session_id'),
        step=row._value('orchestration_step_count'), active_chain=row._text('active_chain'),
        active_fanout=row._text('active_fanout'), revision=row._value('revision_count'),
        note=row._text('note'), status=row._text('status'), block_kind=row._text('block_kind'),
        cancelled_at=row._text('cancelled_at'), result_row_id=result_id)
    _check(claim == expected)
    return SourceCaptureClaim(**claim)


def _budgets(value: object, row: NativeSourceRecord) -> SourceCaptureBudgets:
    budgets = _closed(value, 'source_step source_revision session_timeout_seconds max_revise_rounds '
                     'coding_attempt_limit coding_event_limit coding_revision_limit coding_same_principal_retry_limit')
    for name, item in budgets.items():
        if name == 'session_timeout_seconds':
            if item is not None:
                _integer(item, 1)
        else:
            _integer(item)
    _check(budgets['source_step'] == row._value('orchestration_step_count')
           and budgets['source_revision'] == row._value('revision_count')
           and budgets['session_timeout_seconds'] == row._value('session_timeout_seconds'))
    for name, maximum in (('coding_attempt_limit', 64), ('coding_event_limit', 4096),
                          ('coding_revision_limit', 3), ('coding_same_principal_retry_limit', 1)):
        _check(budgets[name] <= maximum)
    return SourceCaptureBudgets(**budgets)


def _decode_capture(audit: NativeSourceRecord, org_slug: str, budget: _Budget) -> OriginalSourceCapture:
    _check(audit._text('action') == 'coding_source_admission')
    _integer(audit._value('id'), 1)
    _timestamp(audit._text('timestamp'))
    body = _closed(_json(audit._value('payload'), budget),
        'format phase org_slug source_admission_id native_owner command_bytes_base64 task_record '
        'ancestor_records session_start_audit_id result callback_admission_audit_id native_claim '
        'owner_invocation_id budgets observed_at')
    _check(body['format'] == 'workflow-coding-source-admission@1'
           and body['org_slug'] == org_slug and type(body['phase']) is str
           and body['phase'] in ('callback', 'source'))
    phase = body['phase']
    _timestamp(body['observed_at'])
    _check(datetime.fromisoformat(body['observed_at'].replace('Z', '+00:00')).utcoffset() == timedelta(0))
    owner = _closed(body['native_owner'], 'table id')
    if phase == 'callback':
        _check(owner['table'] == 'task_results')
        _integer(owner['id'], 1)
    else:
        _check(type(owner['table']) is str and owner['table'] in (
            'workflow_authorization_revisions', 'workflow_coding_mappings'))
        _token(owner['id'])
    task = _task_record(body['task_record'], budget)
    ancestors = body['ancestor_records']
    _check(type(ancestors) is list and len(ancestors) < _SOURCE_LIMIT)
    chain = [task]
    seen = {task._text('id')}
    for item in ancestors:
        row = _task_record(item, budget)
        _check(row._text('id') not in seen and chain[-1]._text('parent_task_id') == row._text('id')
               and row._text('team') == task._text('team'))
        seen.add(row._text('id'))
        chain.append(row)
    _check(chain[-1]._value('parent_task_id') is None)
    _check(audit._text('task_id') == task._text('id')
           and audit._text('agent') == task._text('assigned_agent')
           and task._text('task_type') == 'task' and task._text('status') == 'in_progress')
    _token(task._text('assigned_agent'))
    _token(task._text('current_session_id'))
    for name in ('block_kind', 'cancelled_at', 'blocked_on_job_ids', 'active_chain', 'active_fanout'):
        _check(task._value(name) is None)
    for name in ('revision_count', 'orchestration_step_count'):
        _integer(task._value(name))
    if task._value('session_timeout_seconds') is not None:
        _integer(task._value('session_timeout_seconds'), 1)
    _integer(body['session_start_audit_id'], 1)
    command = _base64(body['command_bytes_base64'], budget)
    _check(bool(command))
    result = None
    if body['result'] is not None:
        wrapper = _closed(body['result'], 'id record')
        _integer(wrapper['id'], 1)
        result = _record(wrapper['record'], _RESULT_COLUMNS, _RESULT_TYPES, budget)
        _check(result._value('id') == wrapper['id']
               and result._text('task_id') == task._text('id')
               and result._text('agent') == task._text('assigned_agent')
               and result._text('session_id') == task._text('current_session_id')
               and result._text('status') == 'completed' and result._value('waiting_on_job_ids') is None
               and result._value('decision_json') == command)
        _timestamp(result._text('created_at'))
        # learnings remains native FREE TEXT; all other owner JSON is strict.
        _native_json(result, ('decision_json', 'risks_flagged', 'waiting_on_job_ids', 'local_ci'), budget)
        decision = _closed(_json(command, budget), 'action workflow')
        _check(decision['action'] == 'workflow_start' and type(decision['workflow']) is dict
               and decision['workflow'].get('format') == 'workflow-coding-start@1')
        # Check the COMPLETE command grammar even in unselected inventory
        # entries. This self radius is syntax only; the containing reader
        # separately binds the selected command to the actual INITIAL P.
        _start_decision(result, {'radius': decision['workflow']['work']}, budget)
    claim, budgets = None, None
    if phase == 'callback':
        _check(result is not None and owner['id'] == result._value('id'))
        for name in ('source_admission_id', 'callback_admission_audit_id', 'native_claim',
                     'owner_invocation_id', 'budgets'):
            _check(body[name] is None)
    else:
        _token(body['source_admission_id'])
        _token(body['owner_invocation_id'])
        budgets = _budgets(body['budgets'], task)
        if result is None:
            _check(body['callback_admission_audit_id'] is None and body['native_claim'] is None)
            original = _json(command, budget)
            _check(type(original) is dict and _canonical(original) == command)
        else:
            _check(owner['table'] == 'workflow_authorization_revisions')
            _integer(body['callback_admission_audit_id'], 1)
            claim = _claim(body['native_claim'], task, result._value('id'))
    return OriginalSourceCapture(audit, phase, org_slug, body['source_admission_id'],
        SourceCaptureOwner(**owner), command, task, tuple(chain[1:]), body['session_start_audit_id'],
        result, body['callback_admission_audit_id'], claim, body['owner_invocation_id'], budgets,
        body['observed_at'])


def _same_record(left: NativeSourceRecord, right: NativeSourceRecord, *, first_boundary: bool = False) -> None:
    _check(len(left.columns) == len(right.columns))
    if first_boundary:
        _check(tuple(column.name for column in left.columns) == _TASK_COLUMNS
               and len(_FIRST_BOUNDARY_COLUMNS) == 25
               and set(_FIRST_BOUNDARY_COLUMNS) == set(_TASK_COLUMNS) - {'updated_at', 'last_heartbeat'})
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
