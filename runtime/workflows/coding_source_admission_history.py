"""Unmounted SELECT-only ORIGINAL coding source-admission capture history.

Caller admits complete native/J structure and owns one raw-value SQLite
transaction/connection/lock. Retained C0/C1 evidence is separate from today's
native rows. Neither these captures nor their native links authenticate J,
current policy, actual invocation/Pause ownership or producer permission.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass

from runtime.workflows._coding_source_capture import (
    OriginalSourceCapture, _decode_capture, _same_record,
)
from runtime.workflows.coding_authority_history import CodingAuthorityHistoryError, _Budget
from runtime.workflows.coding_capability_history import (
    CodingCapabilityHistoryError, HistoricalCodingCapability,
)
from runtime.workflows.coding_saved_records import CodingSavedRecordError
from runtime.workflows.coding_source_history import (
    CodingSourceHistoryError, CodingSourceNativeRecords,
    _AUDIT_COLUMNS, _AUDIT_TYPES, _check, _inventory, _native_rows, _projection, _source_records,
    _start_decision, _task,
)

_REFUSAL = 'coding_source_history_invalid'
_UNAVAILABLE = 'coding_source_capture_unavailable'
# Complete fixed-action inventory, not a JSON scan or task/agent filter that
# could hide a conflicting same owner/admission placed under a foreign tuple.
# Keyset singleton pages renew BOTH remaining-row and byte gates before fetch.
_CAPTURE_SQL = _projection(_AUDIT_COLUMNS, _AUDIT_TYPES, 'audit_log',
    "WHERE action='coding_source_admission' AND id>=? ORDER BY id LIMIT ?")
_AUDIT_LINK_SQL = _projection(_AUDIT_COLUMNS, _AUDIT_TYPES, 'audit_log', 'WHERE id=? LIMIT 2')


@dataclass(frozen=True)
class CodingSourceAdmissionHistory:
    """Retained originals and explicit immutable native linkage only.

    initial_source is the actual original P, even when selection attenuates
    it. native_records keeps the original five-field identity API intact.
    callback_capture is absent for a nonallocating source, even when a later
    native callback exists. action_fingerprint hashes the FULL raw decision.
    """

    native_records: CodingSourceNativeRecords
    initial_source: HistoricalCodingCapability
    source_capture: OriginalSourceCapture
    callback_capture: OriginalSourceCapture | None
    action_fingerprint: str | None


def _native_task_links(conn: sqlite3.Connection, capture: OriginalSourceCapture,
                       native: CodingSourceNativeRecords, budget: _Budget) -> None:
    # Mutable assignment/SID/team/counters/timestamps are NEVER compared to
    # today's rows. Native task identity, creation and structural parent/type
    # remain independent links, not replay of the original full snapshots.
    current = {row._text('id'): row for row in (native.source_task_record, *native.ancestor_task_records)}
    for original in (capture.task_record, *capture.ancestor_records):
        identity = original._text('id')
        row = current.get(identity)
        if row is None:
            row = _task(conn, identity, budget)
        budget.step()
        for name in ('id', 'created_at', 'parent_task_id', 'task_type'):
            _check(original._value(name) == row._value(name))


def _selected_links(capture: OriginalSourceCapture, native: CodingSourceNativeRecords,
                    initial: dict, budget: _Budget) -> None:
    issuer = initial['issuer']
    task = capture.task_record
    _check(capture.org_slug == initial['subject']['org_slug']
           and task._text('id') == issuer['task_id']
           and task._text('assigned_agent') == issuer['principal']
           and task._text('team') == initial['subject']['team']
           and task._text('current_session_id') == issuer['session_id']
           and capture.session_start_audit_id == native.session_start_audit_record._value('id'))
    if issuer['result_id'] is None:
        _check(capture.result_record is None and initial['action'] != 'start-coding')
    else:
        _check(capture.result_record is not None and initial['action'] == 'start-coding')
        results = [row for row in native.same_session_result_records if row._value('id') == issuer['result_id']]
        _check(len(results) == 1)
        _same_record(capture.result_record, results[0])
        _start_decision(capture.result_record, initial, budget)


def _first_boundary(callback: OriginalSourceCapture, source: OriginalSourceCapture) -> None:
    _check(callback.phase == 'callback' and source.phase == 'source'
           and source.callback_admission_audit_id == callback.audit_record._value('id')
           and callback.audit_record._value('id') < source.audit_record._value('id')
           and callback.session_start_audit_id == source.session_start_audit_id
           and callback.command_bytes == source.command_bytes)
    _same_record(callback.result_record, source.result_record)
    _check(len(callback.ancestor_records) == len(source.ancestor_records))
    # EXACT25 named columns, in complete27 native order. Only the two valid
    # timestamp observations may differ at this boundary; BOTH are retained.
    # Each original chain has already independently closed parent-to-NULL.
    for c0, c1 in zip((callback.task_record, *callback.ancestor_records),
                      (source.task_record, *source.ancestor_records)):
        _same_record(c0, c1, first_boundary=True)


def _read(conn: sqlite3.Connection, org_slug: str, authorization_id: str) -> CodingSourceAdmissionHistory:
    budget = _Budget()
    native = _source_records(conn, org_slug, authorization_id, budget=budget)
    initial_row = native.capability_history.capabilities[0]
    initial = json.loads(initial_row.authority_bytes)
    # The initial source admission is already independently joined to ALL five
    # actual native admission columns by the capability reader. No selected
    # refinement mint, mapping owner or caller-provided source can substitute.
    source_admission_id = initial['source_admission_id']
    _check(source_admission_id == initial_row.own_admission.id)
    captures = []
    owners = set()
    admissions = set()
    selected = None
    for audit in _inventory(conn, _CAPTURE_SQL, (), _AUDIT_COLUMNS, _AUDIT_TYPES, budget):
        capture = _decode_capture(audit, org_slug, budget)
        owner_key = (capture.phase, capture.native_owner.table, capture.native_owner.id)
        _check(owner_key not in owners)
        owners.add(owner_key)
        if capture.phase == 'source':
            _check(capture.source_admission_id not in admissions)
            admissions.add(capture.source_admission_id)
        captures.append(capture)
        if capture.phase == 'source' and (
                capture.source_admission_id == source_admission_id
                or (capture.native_owner.table == 'workflow_authorization_revisions'
                    and capture.native_owner.id == initial_row.authorization_id)):
            _check(selected is None and capture.source_admission_id == source_admission_id
                   and capture.native_owner.table == 'workflow_authorization_revisions'
                   and capture.native_owner.id == initial_row.authorization_id)
            selected = capture
    # Entire bounded inventory is decoded first: absent originals are distinct
    # from malformed/duplicate/conflicting/foreign evidence, with no prefix return.
    if selected is None:
        raise CodingSourceHistoryError(_UNAVAILABLE)
    _selected_links(selected, native, initial, budget)
    _native_task_links(conn, selected, native, budget)
    callback = None
    if initial['issuer']['result_id'] is not None:
        for capture in captures:
            if capture.phase == 'callback' and (
                    capture.native_owner.id == initial['issuer']['result_id']
                    or capture.audit_record._value('id') == selected.callback_admission_audit_id):
                _check(callback is None
                       and capture.native_owner.id == initial['issuer']['result_id']
                       and capture.audit_record._value('id') == selected.callback_admission_audit_id)
                callback = capture
        if callback is None:
            # An existing referenced foreign action is INVALID, not missing
            # capture evidence. Full bounded native row gates still apply.
            links = _native_rows(conn, _AUDIT_LINK_SQL,
                (selected.callback_admission_audit_id,), _AUDIT_COLUMNS, _AUDIT_TYPES, budget)
            try:
                _check(next(links, None) is None)  # ANY native row here is a conflict
            finally:
                links.close()
            raise CodingSourceHistoryError(_UNAVAILABLE)
        _selected_links(callback, native, initial, budget)
        _first_boundary(callback, selected)
        # Original C0 and C1 independently link to retained native identities;
        # no call to today's _retry_claim_matches or tracker reconstruction.
        _native_task_links(conn, callback, native, budget)
    return CodingSourceAdmissionHistory(native, initial_row, selected, callback,
        None if callback is None else hashlib.sha256(callback.command_bytes).hexdigest())


def read_source_admission_history(conn: sqlite3.Connection, *, expected_org_slug: str,
                                  authorization_id: str) -> CodingSourceAdmissionHistory:
    """Read fixed original captures using one coherent native/P/U call budget.

    Missing captures: coding_source_capture_unavailable. Invalid, conflicting,
    foreign, oversized or unknown evidence: coding_source_history_invalid.
    Neither category exposes private details or partial records. Compatible
    complete schema admission/entitled selection are caller prerequisites.

    SELECTs only; no await, transaction control, write, file/network/roster I/O.
    One8192-row/16384-step/16MiB budget includes conservative decoded-byte copies;
    all variable values and complete raw audit payloads <=1MiB, source20/P64/
    setup1024. Native NULL/empty/REAL/raw UTF8 observations are never normalized.
    Limits do not prove total SQLite/process/JSON-object memory consumption.

    Original nonallocating absence is admission-time evidence only. C0/C1
    compare exactly25 fields and complete ancestor order, preserving both
    timestamp observations. C1=C2 ALL27, full J run_started/source-baseline and
    replay/native writers/heartbeat/current policy/actual invocation/Pause/
    move/host/terminal/recovery/cutover remain separate prerequisite readers
    BEFORE EVERY producer. This API does not consume direct mapping history.
    """
    try:
        return _read(conn, expected_org_slug, authorization_id)
    except CodingSourceHistoryError as error:
        if str(error) == _UNAVAILABLE:
            raise CodingSourceHistoryError(_UNAVAILABLE) from None
        raise CodingSourceHistoryError(_REFUSAL) from None
    except (sqlite3.Error, CodingSavedRecordError, CodingAuthorityHistoryError,
            CodingCapabilityHistoryError, ValueError, TypeError, KeyError,
            UnicodeError, RecursionError, OverflowError, AttributeError):
        raise CodingSourceHistoryError(_REFUSAL) from None
