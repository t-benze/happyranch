"""SELECT-only native setup issuance history and K2b complete cold closure.

Caller must first admit the complete native schema and own a coherent SQLite
read transaction, connection and lock for the whole call (raw native values,
no custom converters). This reader neither begins nor ends that transaction.
It establishes retained row/command/admission/publication and setup containment
facts only. It never authenticates a live human, grants current access, proves
task/session/result ancestry. The historical public reader selects no current
pointer. K2b's complete storage composition additionally validates the latest
pointer without treating it as current authority. No cache, roster, file,
object or network is read. Each producer supplies its own complete current-use
consumers; future-stage readers do not precede human setup production.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
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
_CALL_ROW_LIMIT = 8192
_TRAVERSAL_LIMIT = 16384
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
    accepted_rows: int = 0
    traversed: int = 0

    def step(self) -> None:
        self.traversed += 1
        _check(self.traversed <= _TRAVERSAL_LIMIT)

    def candidate(self) -> None:
        self.accepted_rows += 1
        _check(self.accepted_rows <= _CALL_ROW_LIMIT)

    @property
    def remaining_bytes(self) -> int:
        return _BYTE_LIMIT - self.consumed

    def native_row(self, row: tuple, allowed: tuple[tuple[str, ...], ...]) -> tuple:
        """Already charged candidate; native TEXT is projected as raw BLOB.

        Nullable/empty free text and finite REAL are native-record facts only.
        The existing mandatory setup/admission row path below stays strict.
        """
        count = len(allowed)
        _check(len(row) == 2 * count)
        values, types = row[:count], row[count:]
        native = {'text': bytes, 'integer': int, 'real': float, 'null': type(None)}
        for value, storage, choices in zip(values, types, allowed):
            _check(storage in choices and type(value) is native[storage])
            if storage == 'text':
                _check(len(value) <= _VALUE_LIMIT)
                _check(value.decode('utf8').encode('utf8') == value)
                self.consumed += len(value)
                _check(self.consumed <= _BYTE_LIMIT)
            elif storage == 'real':
                _check(math.isfinite(value))
        return values

    def row(self, row: tuple, types: tuple[str, ...], *, counted: bool = False) -> tuple:
        if not counted:
            self.candidate()
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
          maximum: int, budget: _Budget | None = None) -> Iterator[tuple]:
    # All SQL call sites below are fixed-table literal parameterized SELECTs.
    # Cursor-local factory preserves caller configuration, never normalizes
    # native NULL/BLOB/TEXT/INTEGER or materializes an unbounded result set.
    # Every projected value has an exact native storage-class gate. TEXT
    # uses BLOB length (including multibyte/NUL suffixes); BLOB uses byte
    # length; INTEGER returns only a native scalar. Invalid/oversize values
    # become NULL, retaining original typeof evidence for refusal. Genuine
    # nullable owner NULL stays NULL; no bytes are coerced or truncated.
    # These gates bound each value before fetch, not SQLite engine memory.
    # _Budget accounts aggregate accepted raw bytes after each bounded fetch;
    # a refused row and decoded/copied structures are not retained-byte totals.
    cursor = conn.cursor()
    cursor.row_factory = None
    try:
        cursor.execute(sql, args)
        count = 0
        while (row := cursor.fetchone()) is not None:
            if budget is not None:
                budget.candidate()
            count += 1
            _check(count <= maximum)
            yield row
    finally:
        cursor.close()


def _admission(conn: sqlite3.Connection, issuance: dict, budget: _Budget) -> None:
    expected = issuance['admission']['record']
    _capture_admission(conn, expected, issuance['admission']['capture'], budget)


def _capture_admission(conn: sqlite3.Connection, expected: dict,
                       capture: dict, budget: _Budget) -> None:
    # Private composition only: callers first parse the complete saved owner
    # and derive all five expected columns from its original command/capture.
    rows = list(_rows(conn,
        "SELECT CASE WHEN typeof(id)='text' AND length(CAST(id AS BLOB))<=1048576 THEN id ELSE NULL END,"
        "CASE WHEN typeof(namespace)='text' AND length(CAST(namespace AS BLOB))<=1048576 THEN namespace ELSE NULL END,"
        "CASE WHEN typeof(generation)='integer' THEN generation ELSE NULL END,"
        "CASE WHEN typeof(request_digest)='text' AND length(CAST(request_digest AS BLOB))<=1048576 THEN request_digest ELSE NULL END,"
        "CASE WHEN typeof(admitted_by)='text' AND length(CAST(admitted_by AS BLOB))<=1048576 THEN admitted_by ELSE NULL END,"
        'typeof(id),typeof(namespace),typeof(generation),typeof(request_digest),typeof(admitted_by) '
        'FROM workflow_admission_records WHERE id=? LIMIT 2', (expected['id'],), 1, budget))
    _check(len(rows) == 1)
    actual = budget.row(rows[0], ('text', 'text', 'integer', 'text', 'text'), counted=True)
    _check(actual == tuple(expected[key] for key in (
        'id', 'namespace', 'generation', 'request_digest', 'admitted_by')))
    evidence = capture['profile_evidence']
    pointer = evidence['pointer']
    ready = evidence['ready']
    journals = list(_rows(conn,
        "SELECT CASE WHEN typeof(id)='text' AND length(CAST(id AS BLOB))<=1048576 THEN id ELSE NULL END,"
        "CASE WHEN typeof(namespace)='text' AND length(CAST(namespace AS BLOB))<=1048576 THEN namespace ELSE NULL END,"
        "CASE WHEN typeof(generation)='integer' THEN generation ELSE NULL END,"
        "CASE WHEN typeof(expected_generation)='integer' THEN expected_generation ELSE NULL END,"
        "CASE WHEN typeof(snapshot_bytes)='blob' AND length(snapshot_bytes)<=? THEN snapshot_bytes ELSE NULL END,"
        "CASE WHEN typeof(snapshot_digest)='text' AND length(CAST(snapshot_digest AS BLOB))<=1048576 THEN snapshot_digest ELSE NULL END,"
        "CASE WHEN typeof(publisher)='text' AND length(CAST(publisher AS BLOB))<=1048576 THEN publisher ELSE NULL END,"
        "CASE WHEN typeof(publisher_invocation)='text' AND length(CAST(publisher_invocation AS BLOB))<=1048576 THEN publisher_invocation ELSE NULL END,"
        "CASE WHEN typeof(profile_fence)='integer' THEN profile_fence ELSE NULL END,"
        "CASE WHEN typeof(state)='text' AND length(CAST(state AS BLOB))<=1048576 THEN state ELSE NULL END,"
        "CASE WHEN typeof(recovery_owner)='text' AND length(CAST(recovery_owner AS BLOB))<=1048576 THEN recovery_owner ELSE NULL END,"
        "CASE WHEN typeof(file_phase_owner)='text' AND length(CAST(file_phase_owner AS BLOB))<=1048576 THEN file_phase_owner ELSE NULL END,"
        'typeof(id),typeof(namespace),typeof(generation),typeof(expected_generation),'
        'typeof(snapshot_bytes),typeof(snapshot_digest),typeof(publisher),typeof(publisher_invocation),'
        'typeof(profile_fence),typeof(state),typeof(recovery_owner),typeof(file_phase_owner) '
        'FROM workflow_publication_journals WHERE id=? OR '
        '(namespace=? AND generation=? AND snapshot_digest=?) LIMIT 3',
        (_VALUE_LIMIT, pointer['journal_id'], ready['namespace'], ready['generation'],
         ready['snapshot_digest']), 1, budget))
    _check(len(journals) == 1)
    # All twelve native columns are retained and type-checked; the nullable
    # file-phase owner stays NULL or TEXT exactly as stored, never coerced.
    owner_type = journals[0][-1]
    _check(owner_type in ('null', 'text'))
    journal = budget.row(journals[0], (
        'text', 'text', 'integer', 'integer', 'blob', 'text', 'text', 'text',
        'integer', 'text', 'text', owner_type), counted=True)
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
    # The shipped saved readers reconstruct the complete v1/v2 snapshot,
    # profiles and full mirror. Today's cache/roster/mirror are not witnesses.


def _setup_configuration(scope: dict, snapshot: dict) -> None:
    """Exact published manager/repository/store configuration, no live I/O.

    Used at original issuance and independently at current use. This proves
    configured eligibility only, never adoption, task or executor permission.
    """
    agents = {agent['name']: agent for agent in snapshot['agents'] if agent['status'] == 'active'}
    selector = scope['manager_selector']
    manager = agents.get(selector['principal'])
    teams = [team for team in snapshot['teams'] if team['name'] == selector['team']]
    if (manager is None or manager['role'] != 'manager' or manager['team'] != selector['team']
            or not manager['executor'] or len(teams) != 1):
        raise CodingAuthorityHistoryError(_REFUSAL)
    lead = teams[0]['manager']
    if lead != (selector['principal'] if snapshot['schema_version'] == 1 else
                dict(kind='agent', principal=selector['principal'])):
        raise CodingAuthorityHistoryError(_REFUSAL)
    repositories = []
    for key, origin in manager['repos'].items():
        identity = 'workflow-repository:' + _digest(_canonical(
            [snapshot['org_slug'], key, _digest(origin.encode('utf8'))]))
        if identity == scope['repository_id']:
            repositories.append((key, origin))
    if len(repositories) != 1:
        raise CodingAuthorityHistoryError(_REFUSAL)
    key, origin = repositories[0]
    for owner_id in scope['scope']['store_owners']:
        owner = agents.get(owner_id)
        if owner is None or owner['repos'].get(key) != origin or not owner['executor']:
            raise CodingAuthorityHistoryError(_REFUSAL)


def _inventory(conn: sqlite3.Connection, org_slug: str,
               budget: _Budget) -> list[tuple[HistoricalSetupIssuance, dict, dict]]:
    prefix = f'org/{org_slug}/coding-setup/'
    inventory: list[tuple[HistoricalSetupIssuance, dict, dict]] = []
    operations: set[str] = set()
    setups: set[str] = set()
    identities: set[str] = set()
    revisions: set[tuple[str, int]] = set()
    # Org-wide enumeration is required for (org,human:founder,key) replay and
    # setup-subtree uniqueness, rather than trusting a caller's selected dict.
    # Every CASE gates type and caps variable-size values before Python fetch.
    # The shared budget counts accepted raw inventory/admission/journal bytes;
    # the bounded overflow row is fetched only to refuse, never retained.
    for row in _rows(conn,
        "SELECT CASE WHEN typeof(id)='text' AND length(CAST(id AS BLOB))<=1048576 THEN id ELSE NULL END,"
        "CASE WHEN typeof(namespace)='text' AND length(CAST(namespace AS BLOB))<=1048576 THEN namespace ELSE NULL END,"
        "CASE WHEN typeof(revision)='integer' THEN revision ELSE NULL END,"
        "CASE WHEN typeof(authority_bytes)='blob' AND length(authority_bytes)<=? THEN authority_bytes ELSE NULL END,"
        "CASE WHEN typeof(authority_digest)='text' AND length(CAST(authority_digest AS BLOB))<=1048576 THEN authority_digest ELSE NULL END,"
        "CASE WHEN typeof(source_pin)='text' AND length(CAST(source_pin AS BLOB))<=1048576 THEN source_pin ELSE NULL END,"
        "CASE WHEN typeof(created_at)='text' AND length(CAST(created_at AS BLOB))<=1048576 THEN created_at ELSE NULL END,"
        'typeof(id),typeof(namespace),typeof(revision),typeof(authority_bytes),'
        'typeof(authority_digest),typeof(source_pin),typeof(created_at) '
        'FROM workflow_authorization_revisions WHERE substr(namespace,1,?)=? '
        'ORDER BY namespace,revision,id LIMIT ?',
        (_VALUE_LIMIT, len(prefix), prefix, _ROW_LIMIT + 1), _ROW_LIMIT, budget):
        identity, namespace, revision, raw, digest, source, created_at = budget.row(
            row, ('text', 'text', 'integer', 'blob', 'text', 'text', 'text'), counted=True)
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
    return inventory


def _verify_prefix(conn: sqlite3.Connection,
                   required: list[tuple[HistoricalSetupIssuance, dict, dict]],
                   target: HistoricalSetupIssuance, budget: _Budget,
                   verified: set[str]) -> SetupIssuanceHistory:
    # Ordered SQL plus complete prefix: no gap/fork/newest-row fallback.
    _check(len(required) == target.revision)
    prior_issue = False
    previous_time: datetime | None = None
    admission_ids: set[str] = set()
    result: list[HistoricalSetupIssuance] = []
    for expected_revision, (entry, record, command) in enumerate(required, 1):
        budget.step()
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
        if entry.authorization_id not in verified:
            _admission(conn, record, budget)
            verified.add(entry.authorization_id)
        result.append(entry)
    return SetupIssuanceHistory(tuple(result))


class _SetupHistoryReader:
    """Call-local native lookup; no injected records, resolver or new budget.

    Inventory and verified admissions are reused within the caller's one
    snapshot only. Full setup bytes select the actual containing issuance;
    a setup ID is never treated as an authorization ID or wrapper digest.
    """

    def __init__(self, conn: sqlite3.Connection, org_slug: str, budget: _Budget) -> None:
        self.conn = conn
        self.org_slug = org_slug
        self.budget = budget
        self.loaded = False
        self.identities: dict[str, HistoricalSetupIssuance] = {}
        self.setups: dict[str, HistoricalSetupIssuance] = {}
        self.namespaces: dict[str, list[tuple[HistoricalSetupIssuance, dict, dict]]] = {}
        self.histories: dict[str, SetupIssuanceHistory] = {}
        self.verified: set[str] = set()

    def _load(self) -> None:
        if self.loaded:
            return
        for entry, record, command in _inventory(self.conn, self.org_slug, self.budget):
            self.budget.step()
            self.identities[entry.authorization_id] = entry
            self.namespaces.setdefault(entry.namespace, []).append((entry, record, command))
            if entry.setup_bytes is not None:
                self.setups[record['setup']['id']] = entry
        self.loaded = True

    def history(self, authorization_id: str) -> SetupIssuanceHistory:
        self.budget.step()
        self._load()
        _check(authorization_id in self.identities)
        if authorization_id not in self.histories:
            target = self.identities[authorization_id]
            required = []
            for entry in self.namespaces[target.namespace]:
                self.budget.step()
                if entry[0].revision <= target.revision:
                    required.append(entry)
            self.histories[authorization_id] = _verify_prefix(
                self.conn, required, target, self.budget, self.verified)
        return self.histories[authorization_id]

    def setup(self, setup_id: str, setup_bytes: bytes) -> SetupIssuanceHistory:
        self.budget.step()
        self._load()
        _check(setup_id in self.setups)
        target = self.setups[setup_id]
        _check(target.setup_bytes == setup_bytes)
        return self.history(target.authorization_id)


def _history(conn: sqlite3.Connection, org_slug: str,
             authorization_id: str) -> SetupIssuanceHistory:
    return _SetupHistoryReader(conn, org_slug, _Budget()).history(authorization_id)


def read_setup_issuance_history(conn: sqlite3.Connection, *, expected_org_slug: str,
                                authorization_id: str) -> SetupIssuanceHistory:
    """Resolve one retained native identity and its complete historical prefix.

    Complete native schema admission and a caller-owned coherent read snapshot
    are prerequisites. At most1024 accepted org setup rows, 1MiB per native
    text/BLOB value before fetch, and exact native INTEGER scalar projections.
    Original typeof accompanies every value, including nullable owner NULL.
    After each bounded fetch, 16MiB aggregate accepted raw text/BLOB bytes is
    enforced; this is not a total memory/SQLite/decoded-copy ceiling. A bounded
    overflow row is fetched only to refuse; no evidence is truncated/coerced.
    Org-wide wrapper inventory establishes unique operations and
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


@dataclass(frozen=True)
class SetupStorage:
    """Complete cold setup closure, without any claim of current authority."""

    issuances: tuple[HistoricalSetupIssuance, ...]
    pointers: tuple[tuple[str, str], ...]


def read_setup_storage(conn: sqlite3.Connection, *, expected_org_slug: str) -> SetupStorage:
    """Validate every setup prefix and its exact latest native pointer.

    Caller owns the coherent transaction and independently recognized complete
    J schema. No schema reader is called here, avoiding recursive admission.
    Revocation/current configuration never changes historical validity.
    """
    try:
        _check(type(conn) is sqlite3.Connection and conn.in_transaction)
        _check(type(expected_org_slug) is str
               and re.fullmatch(r'[a-z][a-z0-9-]{0,62}', expected_org_slug) is not None)
        reader = _SetupHistoryReader(conn, expected_org_slug, _Budget())
        reader._load()
        admissions: set[str] = set()
        for entries in reader.namespaces.values():
            reader.history(entries[-1][0].authorization_id)
            for _, record, _ in entries:
                if record['mode'] == 'issue':
                    _setup_configuration(record['setup'], json.loads(base64.b64decode(
                        record['admission']['capture']['snapshot_bytes_base64'], validate=True)))
                identity = record['admission']['record']['id']
                _check(identity not in admissions)
                admissions.add(identity)
        prefix = f'org/{expected_org_slug}/coding-setup/'
        pointers = []
        for row in _rows(conn,
            "SELECT CASE WHEN typeof(namespace)='text' AND length(CAST(namespace AS BLOB))<=1048576 THEN namespace ELSE NULL END,"
            "CASE WHEN typeof(authorization_revision_id)='text' AND length(CAST(authorization_revision_id AS BLOB))<=1048576 THEN authorization_revision_id ELSE NULL END,"
            'typeof(namespace),typeof(authorization_revision_id) '
            'FROM workflow_active_authorizations WHERE substr(namespace,1,?)=? '
            'ORDER BY namespace LIMIT ?',
            (len(prefix), prefix, _ROW_LIMIT + 1), _ROW_LIMIT, reader.budget):
            namespace, identity = reader.budget.row(row, ('text', 'text'), counted=True)
            _check(namespace in reader.namespaces
                   and identity == reader.namespaces[namespace][-1][0].authorization_id)
            pointers.append((namespace, identity))
        _check(len(pointers) == len(reader.namespaces)
               and len({namespace for namespace, _ in pointers}) == len(pointers))
        for identity in reader.identities:
            aliases = list(_rows(conn,
                "SELECT CASE WHEN typeof(namespace)='text' AND length(CAST(namespace AS BLOB))<=1048576 THEN namespace ELSE NULL END,"
                "CASE WHEN typeof(authorization_revision_id)='text' AND length(CAST(authorization_revision_id AS BLOB))<=1048576 THEN authorization_revision_id ELSE NULL END,"
                'typeof(namespace),typeof(authorization_revision_id) FROM workflow_active_authorizations '
                'WHERE authorization_revision_id=? LIMIT 2', (identity,), 1, reader.budget))
            if aliases:
                alias = reader.budget.row(aliases[0], ('text', 'text'), counted=True)
                _check(alias in pointers)
            _check(conn.execute('SELECT 1 FROM workflow_binding_snapshots '
                                'WHERE authorization_revision_id=? LIMIT 1', (identity,)).fetchone() is None)
        return SetupStorage(tuple(reader.identities.values()), tuple(pointers))
    except (sqlite3.Error, CodingSavedRecordError, ValueError, TypeError,
            KeyError, UnicodeError, RecursionError, OverflowError):
        raise CodingAuthorityHistoryError(_REFUSAL) from None
