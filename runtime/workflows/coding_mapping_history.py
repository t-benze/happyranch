"""SELECT-only version-aware mapping/original native admission closure.

Caller admits the complete native/J schema and owns one coherent SQLite read
transaction/connection/lock with raw native values for the entire call. This
reader establishes saved grammar, original operation/generation prefix and
historical admission/publication/native containment and, for custody records
only, original setup closure. Agent-read role records never inherit custody. No current default,
grant, task/SID/policy/source-rule/capability/J graph authenticity is asserted.
No current pointer, roster, cache, filesystem, objects or network is read.
"""
from __future__ import annotations

import base64
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from runtime.workflows.coding_authority_history import (
    CodingAuthorityHistoryError, SetupIssuanceHistory, _Budget,
    _SetupHistoryReader, _canonical, _capture_admission, _digest,
    _integer, _bounded_rows, _decoded, _token,
)
from runtime.workflows.coding_saved_records import (
    CodingSavedRecordError, read_saved_mapping_record,
)

_RELEASE = 'release/happyranch/coding'
_ROW_LIMIT = 1024
_REFUSAL = 'coding_mapping_history_invalid'
_TYPES = ('text', 'text', 'text', 'integer', 'text', 'text', 'text',
          'blob', 'text', 'text')


class CodingMappingHistoryError(ValueError):
    """Category-only refusal without private record/SQL diagnostics."""


@dataclass(frozen=True)
class HistoricalCodingMapping:
    """All ten original native columns, command bytes and setup closures.

    storage_classes retains original typeof evidence; full saved bytes include
    the capture, M roots and adoption records, without attesting live stores.
    setup_histories follow M repository order and end at each exact issue.
    """

    mapping_id: str
    org_slug: str
    release_identity: str
    generation: int
    principal: str
    operation_key: str
    request_digest: str
    mapping_bytes: bytes
    mapping_digest: str
    created_at: str
    storage_classes: tuple[str, ...]
    command_bytes: bytes
    setup_histories: tuple[SetupIssuanceHistory, ...]
    source_capture: object


@dataclass(frozen=True)
class CodingMappingHistory:
    """Contiguous historical prefix, never a current default/entitlement."""

    mappings: tuple[HistoricalCodingMapping, ...]

    @property
    def selected(self) -> HistoricalCodingMapping:
        return self.mappings[-1]


def _check(condition: bool) -> None:
    if not condition:
        raise CodingMappingHistoryError(_REFUSAL)


def _history(conn: sqlite3.Connection, org_slug: str,
             mapping_id: str, *, budget: _Budget | None = None,
             setups: _SetupHistoryReader | None = None,
             expected_rows: dict | None = None, captures=None) -> CodingMappingHistory:
    budget = budget if budget is not None else _Budget()
    setups = setups if setups is not None else _SetupHistoryReader(conn, org_slug, budget)
    from runtime.workflows._coding_mapping_admission import _MappingCaptures, audit_values, mapping_capture
    captures = captures if captures is not None else _MappingCaptures(conn, org_slug, budget)
    inventory: list[tuple[tuple, tuple[str, ...], bytes, dict]] = []
    identities: set[str] = set()
    generations: set[int] = set()
    operations: set[tuple[str, str]] = set()
    digests: set[str] = set()
    # Org inventory finds operation conflicts independently of generation and
    # selected ID. Including that ID also categorically refuses a foreign row.
    # Every one of the ten projections gates the ORIGINAL storage class before
    # fetch; TEXT byte length includes multibyte and embedded-NUL suffixes.
    # Invalid/oversize values become bounded NULL with original typeof intact.
    columns = ('id', 'org_slug', 'release_identity', 'generation', 'principal',
               'operation_key', 'request_digest', 'mapping_bytes', 'mapping_digest', 'created_at')
    for row in _bounded_rows(conn, 'workflow_coding_mappings', columns,
        tuple((kind,) for kind in _TYPES), budget,
        where='org_slug=? OR id=?', args=(org_slug, mapping_id), maximum=_ROW_LIMIT):
        budget.step()
        values = budget.row(row, _TYPES, counted=True, charged=True)
        (identity, actual_org, release, generation, principal, key,
         request_digest, raw, digest, created_at) = values
        _token(identity)
        _integer(generation, 1)
        _check(actual_org == org_slug and release == _RELEASE
               and identity not in identities and generation not in generations
               and (principal, key) not in operations and digest not in digests
               and digest == _digest(raw))
        saved = read_saved_mapping_record(raw, expected_org_slug=org_slug)
        _check(saved.command_bytes is not None)
        record = _decoded(json.loads(raw), budget)
        command = _decoded(json.loads(saved.command_bytes), budget)
        # WorkflowTemplatePrincipal.operation_principal convention. This is
        # historical attribution, not construction of an authenticated actor.
        operation_principal = 'agent:' + record['issuer']['principal']
        _check(principal == operation_principal
               and release == record['mapping']['release']['identity']
               and key == command['operation_key']
               and generation == command['expected_generation'] + 1
               and request_digest == record['command_digest'] == _digest(saved.command_bytes)
               and created_at == record['created_at'])
        identities.add(identity)
        generations.add(generation)
        operations.add((principal, key))
        digests.add(digest)
        inventory.append((values, row[10:], saved.command_bytes, record))
    captures.load()
    _check(set(captures.by_mapping) == identities)
    inventory.sort(key=lambda entry: (entry[0][3], entry[0][0]))
    selected = [entry for entry in inventory if entry[0][0] == mapping_id]
    _check(len(selected) == 1)
    target_generation = selected[0][0][3]
    required = [entry for entry in inventory if entry[0][3] <= target_generation]
    _check(len(required) == target_generation)
    previous_time: datetime | None = None
    admissions: set[str] = set()
    result: list[HistoricalCodingMapping] = []
    for expected_generation, (values, storage_classes, command_raw, record) in enumerate(required, 1):
        budget.step()
        _check(values[3] == expected_generation)
        timestamp = datetime.fromisoformat(values[9].replace('Z', '+00:00'))
        _check(previous_time is None or previous_time <= timestamp)
        previous_time = timestamp
        capture = record['authority']
        admission_id = capture['admission_record_id']
        _check(admission_id not in admissions)
        admissions.add(admission_id)
        # No reduced/native DTO: compare the actual complete five-column row
        # against full original command, agent operation principal and capture.
        expected = dict(id=admission_id, namespace=capture['pin']['namespace'],
                        generation=capture['pin']['generation'],
                        request_digest=values[6], admitted_by=values[4])
        _capture_admission(conn, expected, capture, budget)
        grant = _decoded(json.loads(base64.b64decode(capture['grant_bytes_base64'], validate=True)), budget)
        histories = []
        for setup in (grant['setups'] if record['format'] == 'workflow-coding-mapping-record@1' else []):
            budget.step()
            # Pure reader already checks U -> mapping/adoption prior times,
            # manager/actions/repository radius and all adoption pin equalities.
            # Exact canonical U must belong to a genuine native issuance with
            # its own earlier complete prefix/admission/publication closure.
            histories.append(setups.setup(setup['id'], _canonical(setup)))
        original = mapping_capture(conn, org_slug, values[0], command_raw, record, budget,
                                   inventory=captures)
        if expected_rows is not None:
            # Derive these complete typed rows from the validated record ONCE,
            # while it is decoded. IDs only locate candidates for full equality.
            rows = (('workflow_coding_mappings', dict(zip(columns, values, strict=True))),
                    ('workflow_admission_records', expected), ('audit_log', audit_values(original)))
            for table, row in rows:
                budget.step()
                index = expected_rows.setdefault(table, {})
                identity = (type(row['id']), row['id'])
                _check(identity not in index)
                index[identity] = row
        result.append(HistoricalCodingMapping(
            *values, storage_classes, command_raw, tuple(histories), original))
    return CodingMappingHistory(tuple(result))


def read_mapping_history(conn: sqlite3.Connection, *, expected_org_slug: str,
                         mapping_id: str) -> CodingMappingHistory:
    """Resolve selected native ID and its complete org/release prefix.

    Prerequisites: caller-owned coherent read snapshot and complete native/J
    structural admission (raw values, no converters). No transaction is begun
    or ended. Fixed parameterized SELECTs only, at most1024 mapping inventory
    rows and1024 setup inventory rows, each text/BLOB value capped at1MiB BEFORE
    fetch with original typeof evidence. Across the entire composed call:
    at most8192 fetched native candidates,16384 traversal steps and16MiB accepted
    raw text/BLOB bytes, without per-root resets. Singleton keyset pages gate
    each complete row against remaining row/byte budgets before fetch. One bounded overflow fetch
    refuses; SQL engine/rejected-row/decoded-copy memory is not bounded here.
    Call-local verified history is reused only within this snapshot.

    All inventory grammar/native comparisons/operation uniqueness are checked;
    native admission/publication/setup closure is established for the selected
    mapping prefix and its required issuance prefixes only. Later withdrawal
    does not invalidate an earlier genuine issue or restore any current grant.
    Original mapping task/SID/session-start/capture/publication policy is checked;
    current authority/capabilities/store identity are separate producer/read gates.
    Run/event/result/session/receipt/heartbeat graph remains owed with its producer. Human setup needs its
    own complete readers only. Reading never attests or adopts a store.
    """
    try:
        _check(type(conn) is sqlite3.Connection and conn.in_transaction)
        _check(type(expected_org_slug) is str
               and re.fullmatch(r'[a-z][a-z0-9-]{0,62}', expected_org_slug) is not None)
        _token(mapping_id)
        return _history(conn, expected_org_slug, mapping_id)
    except (sqlite3.Error, CodingSavedRecordError, CodingAuthorityHistoryError,
            ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        raise CodingMappingHistoryError(_REFUSAL) from None


def _read_mapping_storage(conn: sqlite3.Connection, *, expected_org_slug: str,
                          budget: _Budget, setups: _SetupHistoryReader | None = None, captures=None
                          ) -> tuple[CodingMappingHistory | None, dict]:
    """Private complete-history/expected-row composition; no nested budget reset."""
    try:
        _check(type(conn) is sqlite3.Connection and conn.in_transaction)
        _check(type(expected_org_slug) is str
               and re.fullmatch(r'[a-z][a-z0-9-]{0,62}', expected_org_slug) is not None)
        # Inventory is complete below. This bounded seed is selection only,
        # never an exemption or latest-row validity fallback.
        budget.step()
        cursor = conn.cursor()
        cursor.row_factory = None
        try:
            cursor.execute("SELECT CASE WHEN ? > 0 AND typeof(id)='text' "
                "AND length(CAST(id AS BLOB))<=min(128,?) THEN id ELSE NULL END,typeof(id) "
                "FROM workflow_coding_mappings ORDER BY generation DESC,id LIMIT 1",
                (8192 - budget.accepted_rows, budget.remaining_bytes))
            row = cursor.fetchone()
        finally:
            cursor.close()
        if row is None:
            return None, {}
        budget.candidate()
        _check(row[0] is not None and row[1] == 'text')
        budget.row(row, ('text',), counted=True)
        _token(row[0])
        expected = {}
        history = _history(conn, expected_org_slug, row[0], budget=budget,
                           setups=setups, expected_rows=expected, captures=captures)
        return history, expected
    except (sqlite3.Error, CodingSavedRecordError, CodingAuthorityHistoryError,
            ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        raise CodingMappingHistoryError(_REFUSAL) from None


def read_mapping_storage(conn: sqlite3.Connection, *, expected_org_slug: str) -> CodingMappingHistory | None:
    """Complete bounded mapping prefix; an empty store is not current authority."""
    return _read_mapping_storage(conn, expected_org_slug=expected_org_slug, budget=_Budget())[0]
