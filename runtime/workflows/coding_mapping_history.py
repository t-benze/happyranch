"""Unmounted SELECT-only retained coding mapping and setup history.

Caller admits the complete native/J schema and owns one coherent SQLite read
transaction/connection/lock with raw native values for the entire call. This
reader establishes saved grammar, original operation/generation prefix and
historical admission/publication/setup containment only. No current default,
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
    _integer, _rows, _token,
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
             mapping_id: str) -> CodingMappingHistory:
    budget = _Budget()
    setups = _SetupHistoryReader(conn, org_slug, budget)
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
    for row in _rows(conn,
        "SELECT CASE WHEN typeof(id)='text' AND length(CAST(id AS BLOB))<=1048576 THEN id ELSE NULL END,"
        "CASE WHEN typeof(org_slug)='text' AND length(CAST(org_slug AS BLOB))<=1048576 THEN org_slug ELSE NULL END,"
        "CASE WHEN typeof(release_identity)='text' AND length(CAST(release_identity AS BLOB))<=1048576 THEN release_identity ELSE NULL END,"
        "CASE WHEN typeof(generation)='integer' THEN generation ELSE NULL END,"
        "CASE WHEN typeof(principal)='text' AND length(CAST(principal AS BLOB))<=1048576 THEN principal ELSE NULL END,"
        "CASE WHEN typeof(operation_key)='text' AND length(CAST(operation_key AS BLOB))<=1048576 THEN operation_key ELSE NULL END,"
        "CASE WHEN typeof(request_digest)='text' AND length(CAST(request_digest AS BLOB))<=1048576 THEN request_digest ELSE NULL END,"
        "CASE WHEN typeof(mapping_bytes)='blob' AND length(mapping_bytes)<=1048576 THEN mapping_bytes ELSE NULL END,"
        "CASE WHEN typeof(mapping_digest)='text' AND length(CAST(mapping_digest AS BLOB))<=1048576 THEN mapping_digest ELSE NULL END,"
        "CASE WHEN typeof(created_at)='text' AND length(CAST(created_at AS BLOB))<=1048576 THEN created_at ELSE NULL END,"
        'typeof(id),typeof(org_slug),typeof(release_identity),typeof(generation),'
        'typeof(principal),typeof(operation_key),typeof(request_digest),'
        'typeof(mapping_bytes),typeof(mapping_digest),typeof(created_at) '
        'FROM workflow_coding_mappings WHERE org_slug=? OR id=? '
        'ORDER BY generation,id LIMIT ?',
        (org_slug, mapping_id, _ROW_LIMIT + 1), _ROW_LIMIT):
        budget.step()
        values = budget.row(row, _TYPES)
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
        record = json.loads(raw)
        command = json.loads(saved.command_bytes)
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
        grant = json.loads(base64.b64decode(capture['grant_bytes_base64'], validate=True))
        histories = []
        for setup in grant['setups']:
            budget.step()
            # Pure reader already checks U -> mapping/adoption prior times,
            # manager/actions/repository radius and all adoption pin equalities.
            # Exact canonical U must belong to a genuine native issuance with
            # its own earlier complete prefix/admission/publication closure.
            histories.append(setups.setup(setup['id'], _canonical(setup)))
        result.append(HistoricalCodingMapping(
            *values, storage_classes, command_raw, tuple(histories)))
    return CodingMappingHistory(tuple(result))


def read_mapping_history(conn: sqlite3.Connection, *, expected_org_slug: str,
                         mapping_id: str) -> CodingMappingHistory:
    """Resolve selected native ID and its complete org/release prefix.

    Prerequisites: caller-owned coherent read snapshot and complete native/J
    structural admission (raw values, no converters). No transaction is begun
    or ended. Fixed parameterized SELECTs only, at most1024 mapping inventory
    rows and1024 setup inventory rows, each text/BLOB value capped at1MiB BEFORE
    fetch with original typeof evidence. Across the entire composed call:
    at most8192 accepted native rows,16384 traversal steps and16MiB accepted
    raw text/BLOB bytes, without per-root resets. One bounded overflow fetch
    refuses; SQL engine/rejected-row/decoded-copy memory is not bounded here.
    Call-local verified history is reused only within this snapshot.

    All inventory grammar/native comparisons/operation uniqueness are checked;
    native admission/publication/setup closure is established for the selected
    mapping prefix and its required issuance prefixes only. Later withdrawal
    does not invalidate an earlier genuine issue or restore any current grant.
    Native issuer task/SID/policy/source rule, current authority/capabilities and
    full native/J run/event/result/session/receipt/heartbeat graph remain owed
    downstream with each corresponding mapping producer. Human setup needs its
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
