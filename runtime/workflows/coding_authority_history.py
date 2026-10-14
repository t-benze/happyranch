"""Unmounted SELECT-only K2a2b1 native setup issuance history.

Caller must first admit the complete native schema and own a coherent SQLite
read transaction, connection and lock for the whole call (raw native values,
no custom converters). This reader neither begins nor ends that transaction.
It establishes retained row/command/admission/publication and setup containment
facts only. It never authenticates a live human, grants current access, proves
task/session/result ancestry or admits populated J. No current pointer, cache,
roster, file, object or network is read. Later relational/current-use owners
must establish those separate facts before any producer can be mounted.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime

from runtime.workflows.coding_saved_records import (
    CodingSavedRecordError, read_saved_issuance, read_saved_setup,
)

_VALUE_LIMIT = 1048576
_ROW_LIMIT = 1024
_BYTE_LIMIT = 16 * _VALUE_LIMIT
_INTEGER_MAX = 9223372036854775807
_SOURCE_PIN = 'coding-capability-issuer@1'
_REFUSAL = 'coding_authority_history_invalid'


class CodingAuthorityHistoryError(ValueError):
    """Category-only refusal; no native row, bytes or SQL diagnostics."""


@dataclass(frozen=True)
class HistoricalSetupIssuance:
    """Exact historical native wrapper and its independently hashed subtree."""

    authorization_id: str
    namespace: str
    revision: int
    authority_bytes: bytes
    authority_digest: str
    created_at: str
    setup_bytes: bytes | None


@dataclass(frozen=True)
class SetupIssuanceHistory:
    """Contiguous prefix ending at the selected retained authorization ID.

    Later withdrawal does not invalidate an old issue. No activity/current
    selection is asserted; setup ID is never an authorization row ID alias.
    """

    issuances: tuple[HistoricalSetupIssuance, ...]

    @property
    def selected(self) -> HistoricalSetupIssuance:
        return self.issuances[-1]


def _check(condition: bool) -> None:
    if not condition:
        raise CodingAuthorityHistoryError(_REFUSAL)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf8')


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _text(value: object) -> None:
    _check(type(value) is str and bool(value) and '\x00' not in value
           and len(value.encode('utf8')) <= _VALUE_LIMIT)


def _token(value: object) -> None:
    _check(type(value) is str and re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', value) is not None)


def _integer(value: object, minimum: int = 0) -> None:
    _check(type(value) is int and minimum <= value <= _INTEGER_MAX)


@dataclass
class _Budget:
    consumed: int = 0

    def row(self, row: tuple, types: tuple[str, ...]) -> tuple:
        count = len(types)
        _check(len(row) == 2 * count and row[count:] == types)
        values = row[:count]
        native = {'text': str, 'integer': int, 'blob': bytes, 'null': type(None)}
        for value, storage in zip(values, types):
            _check(type(value) is native[storage])
            if storage in ('text', 'blob'):
                size = len(value.encode('utf8')) if storage == 'text' else len(value)
                _check(0 < size <= _VALUE_LIMIT)
                self.consumed += size
                _check(self.consumed <= _BYTE_LIMIT)
        return values


def _rows(conn: sqlite3.Connection, sql: str, args: tuple,
          maximum: int) -> Iterator[tuple]:
    # All SQL call sites below are fixed-table literal parameterized SELECTs.
    # Cursor-local factory preserves caller configuration, never normalizes
    # native NULL/BLOB/TEXT/INTEGER or materializes an unbounded result set.
    # CASE bounds TEXT by original UTF8 bytes as well as BLOBs before
    # allocation. Oversize becomes NULL and fails its original storage type;
    # original embedded NULs/bytes remain intact, never a substr projection.
    cursor = conn.cursor()
    cursor.row_factory = None
    try:
        cursor.execute(sql, args)
        count = 0
        while (row := cursor.fetchone()) is not None:
            count += 1
            _check(count <= maximum)
            yield row
    finally:
        cursor.close()


def _admission(conn: sqlite3.Connection, issuance: dict, budget: _Budget) -> None:
    expected = issuance['admission']['record']
    rows = list(_rows(conn,
        'SELECT CASE WHEN length(CAST(id AS BLOB))<=1048576 THEN id ELSE NULL END,'
        'CASE WHEN length(CAST(namespace AS BLOB))<=1048576 THEN namespace ELSE NULL END,generation,'
        'CASE WHEN length(CAST(request_digest AS BLOB))<=1048576 THEN request_digest ELSE NULL END,'
        'CASE WHEN length(CAST(admitted_by AS BLOB))<=1048576 THEN admitted_by ELSE NULL END,'
        'typeof(id),typeof(namespace),typeof(generation),typeof(request_digest),typeof(admitted_by) '
        'FROM workflow_admission_records WHERE id=? LIMIT 2', (expected['id'],), 1))
    _check(len(rows) == 1)
    actual = budget.row(rows[0], ('text', 'text', 'integer', 'text', 'text'))
    _check(actual == tuple(expected[key] for key in (
        'id', 'namespace', 'generation', 'request_digest', 'admitted_by')))
    # The pure reader already binds complete Q, Founder provenance and the
    # org publication pin to this five-column record, also for withdrawal.
    capture = issuance['admission']['capture']
    evidence = capture['profile_evidence']
    pointer = evidence['pointer']
    ready = evidence['ready']
    journals = list(_rows(conn,
        'SELECT CASE WHEN length(CAST(id AS BLOB))<=1048576 THEN id ELSE NULL END,'
        'CASE WHEN length(CAST(namespace AS BLOB))<=1048576 THEN namespace ELSE NULL END,'
        'generation,expected_generation,'
        'CASE WHEN length(snapshot_bytes)<=? THEN snapshot_bytes ELSE NULL END,'
        'CASE WHEN length(CAST(snapshot_digest AS BLOB))<=1048576 THEN snapshot_digest ELSE NULL END,'
        'CASE WHEN length(CAST(publisher AS BLOB))<=1048576 THEN publisher ELSE NULL END,'
        'CASE WHEN length(CAST(publisher_invocation AS BLOB))<=1048576 THEN publisher_invocation ELSE NULL END,'
        'profile_fence,CASE WHEN length(CAST(state AS BLOB))<=1048576 THEN state ELSE NULL END,'
        'CASE WHEN length(CAST(recovery_owner AS BLOB))<=1048576 THEN recovery_owner ELSE NULL END,'
        'CASE WHEN length(CAST(file_phase_owner AS BLOB))<=1048576 THEN file_phase_owner ELSE NULL END,'
        'typeof(id),typeof(namespace),typeof(generation),typeof(expected_generation),'
        'typeof(snapshot_bytes),typeof(snapshot_digest),typeof(publisher),typeof(publisher_invocation),'
        'typeof(profile_fence),typeof(state),typeof(recovery_owner),typeof(file_phase_owner) '
        'FROM workflow_publication_journals WHERE id=? OR '
        '(namespace=? AND generation=? AND snapshot_digest=?) LIMIT 3',
        (_VALUE_LIMIT, pointer['journal_id'], ready['namespace'], ready['generation'],
         ready['snapshot_digest']), 1))
    _check(len(journals) == 1)
    # All twelve native columns are retained and type-checked; the nullable
    # file-phase owner stays NULL or TEXT exactly as stored, never coerced.
    owner_type = journals[0][-1]
    _check(owner_type in ('null', 'text'))
    journal = budget.row(journals[0], (
        'text', 'text', 'integer', 'integer', 'blob', 'text', 'text', 'text',
        'integer', 'text', 'text', owner_type))
    (identity, namespace, generation, previous, raw, digest, publisher, invocation,
     fence, state, recovery, owner) = journal
    _token(identity)
    _integer(generation, 1)
    _integer(previous)
    _integer(fence)
    for value in (publisher, invocation, recovery):
        _text(value)
    if owner is not None:
        _text(owner)
    snapshot = base64.b64decode(capture['snapshot_bytes_base64'], validate=True)
    _check(identity == pointer['journal_id'] and namespace == expected['namespace'] == ready['namespace']
           and generation == expected['generation'] == ready['generation'] == pointer['current_generation']
           and generation == previous + 1 and raw == snapshot == bytes.fromhex(ready['snapshot_bytes_hex'])
           and digest == _digest(raw) == ready['snapshot_digest'] == pointer['snapshot_digest']
           and fence == pointer['profile_fence'] and state == 'cache_installed')
    # read_saved_issuance reconstructs the complete saved v1/v2 snapshot,
    # profiles and full mirror. Today's cache/roster/mirror are not witnesses.


def _history(conn: sqlite3.Connection, org_slug: str,
             authorization_id: str) -> SetupIssuanceHistory:
    prefix = f'org/{org_slug}/coding-setup/'
    budget = _Budget()
    inventory: list[tuple[HistoricalSetupIssuance, dict, dict]] = []
    operations: set[str] = set()
    setups: set[str] = set()
    identities: set[str] = set()
    revisions: set[tuple[str, int]] = set()
    # Org-wide enumeration is required for (org,human:founder,key) replay and
    # setup-subtree uniqueness, rather than trusting a caller's selected dict.
    # CASE caps each SQLite BLOB before Python allocation; streaming and the
    # shared budget bound the complete inventory plus required journal reads.
    for row in _rows(conn,
        'SELECT CASE WHEN length(CAST(id AS BLOB))<=1048576 THEN id ELSE NULL END,'
        'CASE WHEN length(CAST(namespace AS BLOB))<=1048576 THEN namespace ELSE NULL END,revision,'
        'CASE WHEN length(authority_bytes)<=? THEN authority_bytes ELSE NULL END,'
        'CASE WHEN length(CAST(authority_digest AS BLOB))<=1048576 THEN authority_digest ELSE NULL END,'
        'CASE WHEN length(CAST(source_pin AS BLOB))<=1048576 THEN source_pin ELSE NULL END,'
        'CASE WHEN length(CAST(created_at AS BLOB))<=1048576 THEN created_at ELSE NULL END,'
        'typeof(id),typeof(namespace),typeof(revision),typeof(authority_bytes),'
        'typeof(authority_digest),typeof(source_pin),typeof(created_at) '
        'FROM workflow_authorization_revisions WHERE substr(namespace,1,?)=? '
        'ORDER BY namespace,revision,id LIMIT ?',
        (_VALUE_LIMIT, len(prefix), prefix, _ROW_LIMIT + 1), _ROW_LIMIT):
        identity, namespace, revision, raw, digest, source, created_at = budget.row(
            row, ('text', 'text', 'integer', 'blob', 'text', 'text', 'text'))
        _token(identity)
        _integer(revision, 1)
        _check(source == _SOURCE_PIN and digest == _digest(raw)
               and identity not in identities and (namespace, revision) not in revisions)
        identities.add(identity)
        revisions.add((namespace, revision))
        saved = read_saved_issuance(raw, expected_org_slug=org_slug)
        _check(saved.command_bytes is not None)
        record = json.loads(raw)
        command = json.loads(saved.command_bytes)
        _check(namespace == prefix + command['setup_key']
               and command['expected_authorization_revision'] == revision - 1
               and created_at == record['created_at']
               and command['operation_key'] not in operations)
        operations.add(command['operation_key'])
        setup_bytes = None
        if record['mode'] == 'issue':
            setup_bytes = _canonical(record['setup'])
            read_saved_setup(setup_bytes, expected_org_slug=org_slug)
            setup_id = record['setup']['id']
            _check(setup_id not in setups)
            setups.add(setup_id)
        inventory.append((HistoricalSetupIssuance(
            identity, namespace, revision, raw, digest, created_at, setup_bytes), record, command))
    selected = [entry for entry in inventory if entry[0].authorization_id == authorization_id]
    _check(len(selected) == 1)
    target = selected[0][0]
    required = [entry for entry in inventory
                if entry[0].namespace == target.namespace and entry[0].revision <= target.revision]
    # Ordered SQL plus complete prefix: no gap/fork/newest-row fallback.
    _check(len(required) == target.revision)
    prior_issue = False
    previous_time: datetime | None = None
    admission_ids: set[str] = set()
    result: list[HistoricalSetupIssuance] = []
    for expected_revision, (entry, record, command) in enumerate(required, 1):
        _check(entry.revision == expected_revision
               and command['expected_authorization_revision'] == expected_revision - 1)
        timestamp = datetime.fromisoformat(entry.created_at.replace('Z', '+00:00'))
        _check(previous_time is None or previous_time <= timestamp)
        previous_time = timestamp
        if record['mode'] == 'withdraw':
            _check(prior_issue)
        else:
            prior_issue = True
        admission_id = record['admission']['record']['id']
        _check(admission_id not in admission_ids)
        admission_ids.add(admission_id)
        _admission(conn, record, budget)
        result.append(entry)
    return SetupIssuanceHistory(tuple(result))


def read_setup_issuance_history(conn: sqlite3.Connection, *, expected_org_slug: str,
                                authorization_id: str) -> SetupIssuanceHistory:
    """Resolve one retained native identity and its complete historical prefix.

    Complete native schema admission and a caller-owned coherent read snapshot
    are prerequisites. At most1024 org setup rows, 1MiB per native value and
    16MiB aggregate retained native text/BLOB bytes; overflow refuses without
    truncation. Org-wide wrapper inventory establishes unique operations and
    setup subtrees. Admissions/journals are proved for every revision through
    the selected row only, not unrelated or later histories. The selected
    issue's exact setup_bytes are its subtree, never authority_bytes/digest.
    No implicit transaction, writer, repair, current authority or resolver.
    """
    try:
        _check(type(conn) is sqlite3.Connection and conn.in_transaction)
        _check(type(expected_org_slug) is str
               and re.fullmatch(r'[a-z][a-z0-9-]{0,62}', expected_org_slug) is not None)
        _token(authorization_id)
        return _history(conn, expected_org_slug, authorization_id)
    except (sqlite3.Error, CodingSavedRecordError, ValueError, TypeError,
            KeyError, UnicodeError, RecursionError, OverflowError):
        raise CodingAuthorityHistoryError(_REFUSAL) from None
