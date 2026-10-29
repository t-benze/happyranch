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

from runtime.infrastructure._workflow_reader import (
    _json_dumps as _reader_json_dumps,
)

from runtime.infrastructure._workflow_reader import (
    _add as _reader_add,
    _append as _reader_append,
    _extend as _reader_extend,
    _put as _reader_put,
    _setdefault as _reader_setdefault,
    _iter as _reader_iter,
    _equal as _reader_equal,
    _any_true as _reader_any_true,
    _sorted as _reader_sorted,
)

from runtime.infrastructure._workflow_reader import (
    _dict_pairs as _reader_dict_pairs,
    _list as _reader_list,
    _set as _reader_set,
    _tuple as _reader_tuple,
)

from runtime.infrastructure._workflow_reader import (
    _ReaderBudgetExhausted,
    _decode as _reader_decode,
    _sha256 as _reader_sha256,
)


from runtime.infrastructure._workflow_reader import (
    _raw_one as _reader_raw_one, _work as _reader_work,
    _reserve as _reader_reserve, _json as _reader_json,
    _b64decode as _reader_b64decode, _fromhex as _reader_fromhex,
    _encode as _reader_encode,
)

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


class _CodingBudgetExhausted(CodingAuthorityHistoryError, _ReaderBudgetExhausted):
    """Preserve the native error category while refusing semantic fallback."""


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


def _canonical(value: object, *, budget=None) -> bytes:
    _reader_reserve(budget, 0)
    return _reader_encode(_reader_json_dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False, budget=budget), 'utf8', budget=budget)


def _digest(raw: bytes, *, budget=None) -> str:
    _reader_reserve(budget, 0)
    return _reader_sha256(raw, budget=budget).hexdigest()


def _text(value: object, *, budget=None) -> None:
    _check(type(value) is str and bool(value) and '\x00' not in value
           and len(_reader_encode(value, 'utf8', budget=budget)) <= _VALUE_LIMIT)


def _token(value: object, *, budget=None) -> None:
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
        if self.traversed > _TRAVERSAL_LIMIT:
            raise _CodingBudgetExhausted(_REFUSAL)

    def candidate(self) -> None:
        self.accepted_rows += 1
        if self.accepted_rows > _CALL_ROW_LIMIT:
            raise _CodingBudgetExhausted(_REFUSAL)

    def reserve(self, size: int) -> None:
        self.step()
        if type(size) is not int or size < 0:
            raise CodingAuthorityHistoryError(_REFUSAL)
        self.consumed += size
        if self.remaining_bytes < 0:
            raise _CodingBudgetExhausted(_REFUSAL)

    @property
    def remaining_bytes(self) -> int:
        return _BYTE_LIMIT - self.consumed

    def native_row(self, row: tuple, allowed: tuple[tuple[str, ...], ...]) -> tuple:
        """Already charged candidate; native TEXT is projected as raw BLOB.

        Nullable/empty free text and finite REAL are native-record facts only.
        The existing mandatory setup/admission row path below stays strict.
        """
        _reader_reserve(self, 128 * len(row))
        count = len(allowed)
        _check(len(row) == 2 * count)
        values, types = (_reader_reserve(self, 96) or (row[:count], row[count:]))
        native = {'text': bytes, 'integer': int, 'real': float, 'null': type(None)}
        for value, storage, choices in zip(values, types, allowed):
            _check(storage in choices and type(value) is native[storage])
            if storage == 'text':
                _check(len(value) <= _VALUE_LIMIT)
                _reader_work(value, self, copies=2)
                _check(_reader_encode(_reader_decode(value, 'utf8', budget=self), 'utf8', budget=self) == value)
                self.reserve(len(value))
            elif storage == 'real':
                _check(math.isfinite(value))
        return values

    def row(self, row: tuple, types: tuple[str, ...], *, counted: bool = False,
            charged: bool = False) -> tuple:
        _reader_reserve(self, 128 * len(row))
        if not counted:
            self.candidate()
        count = len(types)
        _check(len(row) == 2 * count and row[count:] == types)
        values = row[:count]
        native = {'text': str, 'integer': int, 'blob': bytes, 'null': type(None)}
        for value, storage in zip(values, types):
            _check(type(value) is native[storage])
            if storage in ('text', 'blob'):
                _reader_work(value, self)
                size = len(_reader_encode(value, 'utf8', budget=self)) if storage == 'text' else len(value)
                _check(0 < size <= _VALUE_LIMIT)
                if not charged:
                    self.reserve(size)
        return values


def _bounded_rows(conn: sqlite3.Connection, table: str, columns: tuple[str, ...],
                  allowed: tuple[tuple[str, ...], ...], budget: _Budget, *,
                  where: str = '1', args: tuple = (), maximum: int = _CALL_ROW_LIMIT,
                  with_rowid: bool = False, value_limit: int = _VALUE_LIMIT) -> Iterator[tuple]:
    """Private trusted-schema composition; singleton keyset pages, no OFFSET.

    Every payload cell is withheld unless the WHOLE row fits its original
    storage classes, per-value byte caps and the current shared row/byte budget.
    Original typeof and a separate validity sentinel distinguish a refused
    projection from genuine nullable NULL. No raw oversize/invalid cell reaches
    Python. SQL-engine work/memory and decoded object memory are not bounded.
    """
    _reader_reserve(budget, 256)  # local SQL builders, before their creation

    def quote(name: str) -> str:
        _reader_work(name, budget)  # scan before counting escaped quotes
        length = len(name) + name.count('"') + 2
        # replace, leading quote and final string are separate allocations.
        _reader_reserve(budget, 192 + 12 * length)
        return '"' + name.replace('"', '""') + '"'

    def joined(parts: list[str] | tuple[str, ...], separator: str) -> str:
        _reader_reserve(budget, 128)
        length = max(0, len(parts) - 1) * len(separator)
        for part in _reader_iter(parts, budget=budget):
            length += len(part)
        _reader_reserve(budget, 64 + 4 * length)
        return separator.join(parts)

    _check(type(value_limit) is int and 0 < value_limit <= 16777216)
    _reader_work((_reader_reserve(budget, 144) or (table, columns, allowed, where, args)), budget)
    _reader_reserve(budget, 128)  # map iterator, before quote is invoked
    names = _reader_tuple(map(quote, columns), budget=budget)
    _check(len(names) == len(allowed) and bool(names))
    sizes = (_reader_reserve(budget, 64) or [])
    gates = (_reader_reserve(budget, 64) or [])
    bodies = (_reader_reserve(budget, 64) or [])
    types = (_reader_reserve(budget, 64) or [])
    _reader_reserve(budget, 96 + 4 * value_limit.bit_length())
    limit = str(value_limit)
    size_template = ("CASE WHEN typeof({0}) IN ('text','blob') "
                     "THEN length(CAST({0} AS BLOB)) ELSE 0 END")
    gate_template = ("(typeof({0}) IN ({1}) AND (typeof({0}) NOT IN ('text','blob') "
                     "OR length(CAST({0} AS BLOB))<={2}))")
    body_template = 'CASE WHEN _scan_valid THEN {0} ELSE NULL END'
    type_template = 'typeof({0})'
    _reader_reserve(budget, 128)  # zip/iterator references
    for name, choices in zip(names, allowed, strict=True):
        budget.step()
        _reader_reserve(budget, 128 + 4 * (len(size_template) - 6 + 2 * len(name)))
        _reader_append(sizes, size_template.format(name), budget=budget)
        kinds = (_reader_reserve(budget, 64) or [])
        for kind in _reader_iter(choices, budget=budget):
            _reader_reserve(budget, 64 + 4 * (len(kind) + 2))
            _reader_append(kinds, f"'{kind}'", budget=budget)
        kind_sql = joined(kinds, ',')
        _reader_reserve(budget, 128 + 4 * (len(gate_template) - 15
            + 3 * len(name) + len(kind_sql) + len(limit)))
        _reader_append(gates, gate_template.format(name, kind_sql, limit), budget=budget)
        _reader_reserve(budget, 128 + 4 * (len(body_template) - 3 + len(name)))
        _reader_append(bodies, body_template.format(name), budget=budget)
        _reader_reserve(budget, 128 + 4 * (len(type_template) - 3 + len(name)))
        _reader_append(types, type_template.format(name), budget=budget)
    size = joined(sizes, '+')
    gate = joined(gates, ' AND ')
    _reader_reserve(budget, 64 + 4 * (len(gate) + len(size) + len(' AND ? > 0 AND ()<=?')))
    valid = f'{gate} AND ? > 0 AND ({size})<=?'
    selected = joined(names, ',')
    table_sql = quote(table)
    body = joined(bodies, ',')
    storage = joined(types, ',')
    query_template = ('WITH candidate AS (SELECT rowid AS _scan_rowid,{0}'
        ' FROM {1} WHERE ({2}) AND rowid>=? ORDER BY rowid LIMIT 1), '
        'gated AS (SELECT *,({3}) AS _scan_valid FROM candidate) '
        'SELECT _scan_rowid,_scan_valid,{4},{5} FROM gated')
    _reader_reserve(budget, 192 + 4 * (len(query_template) - 18 + len(selected)
        + len(table_sql) + len(where) + len(valid) + len(body) + len(storage)))
    query = query_template.format(selected, table_sql, where, valid, body, storage)
    after = -9223372036854775808
    count = 0
    try:
        _reader_reserve(budget, 112)
        while True:
            budget.step()  # includes terminal/overflow page traversal
            _reader_reserve(budget, 64 + 16 * (len(args) + 3))
            row = _reader_raw_one(conn, query, args + (_reader_reserve(budget, 112) or (after, min(maximum - count,
                _CALL_ROW_LIMIT - budget.accepted_rows), budget.remaining_bytes)), budget=budget)
            if row is None:
                return
            budget.candidate()  # charge rejected/overflow candidates too
            count += 1
            _check(count <= maximum and row[1] == 1)
            _reader_reserve(budget, 128 * len(row))
            identity, values, types = (_reader_reserve(budget, 112) or (row[0], row[2:2 + len(names)], row[2 + len(names):]))
            _check(type(identity) is int and identity >= after)
            native = (_reader_reserve(budget, 704) or {'text': str, 'blob': bytes, 'integer': int,
                      'real': float, 'null': type(None)})
            for value, storage, choices in zip(values, types, allowed, strict=True):
                budget.step()
                _check(storage in choices and type(value) is native[storage])
                if storage in ('text', 'blob'):
                    _reader_work(value, budget)
                    raw = _reader_encode(value, 'utf8', budget=budget) if storage == 'text' else value
                    budget.reserve(len(raw))
                    _check(len(raw) <= value_limit)
                elif storage == 'real':
                    _check(math.isfinite(value))
            yield (_reader_reserve(budget, 128 + 32 * (1 + len(values) + len(types))) or (identity, *values, *types)) if with_rowid else (_reader_reserve(budget, 128 + 32 * (0 + len(values) + len(types))) or (*values, *types))
            if identity == _INTEGER_MAX:
                return
            after = identity + 1
    except ((sqlite3.Error, UnicodeError, OverflowError)):
        raise CodingAuthorityHistoryError(_REFUSAL) from None



def _decoded(value: object, budget: _Budget) -> object:
    """Account traversal of an already decoded bounded value, no new parser."""
    _reader_work(value, budget)
    pending = (_reader_reserve(budget, 192) or [value])
    while pending:
        budget.step()
        item = pending.pop()
        if type(item) is dict:
            _reader_reserve(budget, 64)
            _reader_extend(pending, item.keys(), budget=budget)
            _reader_reserve(budget, 64)
            _reader_extend(pending, item.values(), budget=budget)
        elif type(item) is list:
            _reader_extend(pending, item, budget=budget)
    return value


def _rows(conn: sqlite3.Connection, sql: str, args: tuple,
          maximum: int, budget: _Budget | None = None) -> Iterator[tuple]:
    # Internal parameterized absence/length SELECTs return only fixed-width
    # native INTEGER projections. Semantic callers retain their exact type
    # checks. The raw cursor-local factory leaves connection policy unchanged;
    # maximum still rejects overflow without truncation or scalar coercion.
    # Charge the original remainder before cursor acquisition and EVERY fetch,
    # including empty/terminal/overflow attempts; None stays standalone.
    if budget is not None:
        budget.step()
        if budget.accepted_rows >= _CALL_ROW_LIMIT:
            budget.candidate()  # retain the original native resource category
    _reader_reserve(budget, 96)
    _reader_work((sql, args), budget)
    _reader_reserve(budget, 512)
    cursor = conn.cursor()
    primary = None
    try:
        cursor.row_factory = None
        _reader_reserve(budget, 0)  # statement execution, separately charged
        cursor.execute(sql, args)
        count = 0
        while True:
            if budget is not None:
                budget.step()
                if budget.accepted_rows >= _CALL_ROW_LIMIT:
                    budget.candidate()
            _reader_reserve(budget, 128 + 128 * len(cursor.description))
            row = cursor.fetchone()
            if row is None:
                return
            if budget is not None:
                budget.candidate()
            count += 1
            _check(count <= maximum)
            yield row
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            cursor.close()
        except BaseException as exc:
            if primary is None:
                raise
            primary.add_note('coding history cursor close failed: ' + type(exc).__name__)
            if isinstance(primary, GeneratorExit):
                # generator.close() suppresses a bare GeneratorExit. Keep its
                # identity and safe secondary evidence visible on early close.
                raise BaseExceptionGroup(_REFUSAL, [primary]) from None


def _admission(conn: sqlite3.Connection, issuance: dict, budget: _Budget) -> None:
    _reader_reserve(budget, 0)  # delegation visit; acquisition/reconstruction below
    expected = issuance['admission']['record']
    _capture_admission(conn, expected, issuance['admission']['capture'], budget)


def _capture_admission(conn: sqlite3.Connection, expected: dict,
                       capture: dict, budget: _Budget) -> None:
    # Private composition only: callers first parse the complete saved owner
    # and derive all five expected columns from its original command/capture.
    _reader_work((_reader_reserve(budget, 96) or (expected, capture)), budget)
    rows = _reader_list(_bounded_rows(conn, 'workflow_admission_records',
        ('id', 'namespace', 'generation', 'request_digest', 'admitted_by'),
        (('text',), ('text',), ('integer',), ('text',), ('text',)), budget,
        where='id=?', args=(_reader_reserve(budget, 80) or (expected['id'],)), maximum=1), budget=budget)
    _check(len(rows) == 1)
    actual = budget.row(rows[0], ('text', 'text', 'integer', 'text', 'text'), counted=True, charged=True)
    _check(actual == _reader_tuple((expected[key] for key in (
        'id', 'namespace', 'generation', 'request_digest', 'admitted_by')), budget=budget))
    evidence = capture['profile_evidence']
    pointer = evidence['pointer']
    ready = evidence['ready']
    journals = _reader_list(_bounded_rows(conn, 'workflow_publication_journals',
        ('id', 'namespace', 'generation', 'expected_generation', 'snapshot_bytes',
         'snapshot_digest', 'publisher', 'publisher_invocation', 'profile_fence',
         'state', 'recovery_owner', 'file_phase_owner'),
        (('text',), ('text',), ('integer',), ('integer',), ('blob',), ('text',),
         ('text',), ('text',), ('integer',), ('text',), ('text',), ('null', 'text')),
        budget, where='id=? OR (namespace=? AND generation=? AND snapshot_digest=?)',
        args=(_reader_reserve(budget, 128) or (pointer['journal_id'], ready['namespace'], ready['generation'],
              ready['snapshot_digest'])), maximum=1), budget=budget)
    _check(len(journals) == 1)
    # All twelve native columns are retained and type-checked; the nullable
    # file-phase owner stays NULL or TEXT exactly as stored, never coerced.
    owner_type = journals[0][-1]
    _check(owner_type in ('null', 'text'))
    journal = budget.row(journals[0], (_reader_reserve(budget, 256) or (
        'text', 'text', 'integer', 'integer', 'blob', 'text', 'text', 'text',
        'integer', 'text', 'text', owner_type)), counted=True, charged=True)
    (identity, namespace, generation, previous, raw, digest, publisher, invocation,
     fence, state, recovery, owner) = journal
    _token(identity, budget=budget)
    _integer(generation, 1)
    _integer(previous)
    _integer(fence)
    for value in (_reader_reserve(budget, 112) or (publisher, invocation, recovery)):
        _text(value, budget=budget)
    if owner is not None:
        _text(owner, budget=budget)
    snapshot = _reader_b64decode(capture['snapshot_bytes_base64'], validate=True, budget=budget)
    _check(identity == pointer['journal_id'] and namespace == expected['namespace'] == ready['namespace']
           and generation == expected['generation'] == ready['generation'] == pointer['current_generation']
           and generation == previous + 1 and raw == snapshot == _reader_fromhex(ready['snapshot_bytes_hex'], budget=budget)
           and digest == _digest(raw, budget=budget) == ready['snapshot_digest'] == pointer['snapshot_digest']
           and fence == pointer['profile_fence'] and state == 'cache_installed')
    # The shipped saved readers reconstruct the complete v1/v2 snapshot,
    # profiles and full mirror. Today's cache/roster/mirror are not witnesses.


def _setup_configuration(scope: dict, snapshot: dict, *, budget=None) -> None:
    """Exact published manager/repository/store configuration, no live I/O.

    Used at original issuance and independently at current use. This proves
    configured eligibility only, never adoption, task or executor permission.
    """
    _reader_reserve(budget, 128)  # generator/iteration, no full input recopy
    agents = _reader_dict_pairs(((_reader_reserve(budget, 96) or (agent['name'], agent))
        for agent in _reader_iter(snapshot['agents'], budget=budget)
        if _reader_equal(agent['status'], 'active', budget=budget)), budget=budget)
    selector = scope['manager_selector']
    _reader_work(selector['principal'], budget)
    manager = agents.get(selector['principal'])
    _reader_reserve(budget, 128)
    teams = _reader_list((team for team in _reader_iter(snapshot['teams'], budget=budget)
        if _reader_equal(team['name'], selector['team'], budget=budget)), budget=budget)
    if (manager is None or not _reader_equal(manager['role'], 'manager', budget=budget)
            or not _reader_equal(manager['team'], selector['team'], budget=budget)
            or not manager['executor'] or len(teams) != 1):
        raise CodingAuthorityHistoryError(_REFUSAL)
    lead = teams[0]['manager']
    if not _reader_equal(lead, (selector['principal'] if snapshot['schema_version'] == 1 else
                (_reader_reserve(budget, 320) or dict(kind='agent', principal=selector['principal']))), budget=budget):
        raise CodingAuthorityHistoryError(_REFUSAL)
    repositories = (_reader_reserve(budget, 64) or [])
    _reader_reserve(budget, 64)  # items view
    for key, origin in _reader_iter(manager['repos'].items(), budget=budget):
        digest = _digest(_canonical(
            (_reader_reserve(budget, 448) or [snapshot['org_slug'], key, _digest(_reader_encode(origin, 'utf8', budget=budget), budget=budget)]), budget=budget), budget=budget)
        _reader_reserve(budget, 64 + 4 * (len('workflow-repository:') + len(digest)))
        identity = 'workflow-repository:' + digest
        if _reader_equal(identity, scope['repository_id'], budget=budget):
            _reader_append(repositories, (_reader_reserve(budget, 96) or (key, origin)), budget=budget)
    if len(repositories) != 1:
        raise CodingAuthorityHistoryError(_REFUSAL)
    key, origin = repositories[0]
    for owner_id in _reader_iter(scope['scope']['store_owners'], budget=budget):
        _reader_work(owner_id, budget)
        owner = agents.get(owner_id)
        _reader_work(key, budget)
        if owner is None or not _reader_equal(owner['repos'].get(key), origin, budget=budget) or not owner['executor']:
            raise CodingAuthorityHistoryError(_REFUSAL)


def _inventory(conn: sqlite3.Connection, org_slug: str,
               budget: _Budget) -> list[tuple[HistoricalSetupIssuance, dict, dict]]:
    _reader_reserve(budget, 64 + 4 * (len('org//coding-setup/') + len(org_slug)))
    prefix = f'org/{org_slug}/coding-setup/'
    inventory: list[tuple[HistoricalSetupIssuance, dict, dict]] = (_reader_reserve(budget, 64) or [])
    operations: set[str] = _reader_set(budget=budget)
    setups: set[str] = _reader_set(budget=budget)
    identities: set[str] = _reader_set(budget=budget)
    revisions: set[tuple[str, int]] = _reader_set(budget=budget)
    # Org-wide enumeration is required for (org,human:founder,key) replay and
    # setup-subtree uniqueness, rather than trusting a caller's selected dict.
    # Every CASE gates type and caps variable-size values before Python fetch.
    # The shared budget counts accepted raw inventory/admission/journal bytes;
    # the bounded overflow row is fetched only to refuse, never retained.
    for row in _bounded_rows(conn, 'workflow_authorization_revisions',
        ('id', 'namespace', 'revision', 'authority_bytes', 'authority_digest', 'source_pin', 'created_at'),
        (('text',), ('text',), ('integer',), ('blob',), ('text',), ('text',), ('text',)),
        budget, where='substr(namespace,1,?)=?', args=(_reader_reserve(budget, 96) or (len(prefix), prefix)), maximum=_ROW_LIMIT):
        identity, namespace, revision, raw, digest, source, created_at = budget.row(
            row, ('text', 'text', 'integer', 'blob', 'text', 'text', 'text'), counted=True, charged=True)
        _token(identity, budget=budget)
        _integer(revision, 1)
        _reader_work(identity, budget)
        _check(source == _SOURCE_PIN and digest == _digest(raw, budget=budget)
               and identity not in identities and (_reader_reserve(budget, 96) or (namespace, revision)) not in revisions)
        _reader_add(identities, identity, budget=budget)
        _reader_add(revisions, (_reader_reserve(budget, 96) or (namespace, revision)), budget=budget)
        saved = read_saved_issuance(raw, expected_org_slug=org_slug, budget=budget)
        _check(saved.command_bytes is not None)
        record = _decoded(_reader_json(raw, budget), budget)
        command = _decoded(_reader_json(saved.command_bytes, budget), budget)
        _reader_reserve(budget, 64 + 4 * (len(prefix) + len(command['setup_key'])))
        expected_namespace = prefix + command['setup_key']
        _reader_work(command['operation_key'], budget)
        _check(_reader_equal(namespace, expected_namespace, budget=budget)
               and command['expected_authorization_revision'] == revision - 1
               and created_at == record['created_at']
               and command['operation_key'] not in operations)
        _reader_add(operations, command['operation_key'], budget=budget)
        setup_bytes = None
        if record['mode'] == 'issue':
            setup_bytes = _canonical(record['setup'], budget=budget)
            read_saved_setup(setup_bytes, expected_org_slug=org_slug, budget=budget)
            setup_id = record['setup']['id']
            _reader_work(setup_id, budget)
            _check(setup_id not in setups)
            _reader_add(setups, setup_id, budget=budget)
        _reader_reserve(budget, 128 + 64 * 7)  # issuance fields/object, before construction
        _reader_append(inventory, (_reader_reserve(budget, 112) or (HistoricalSetupIssuance(
            identity, namespace, revision, raw, digest, created_at, setup_bytes), record, command)), budget=budget)
    # This shallow output plus key/comparison envelope precedes the sort;
    # traversing every retained command again would not account its sorting.
    _reader_reserve(budget, 128)  # key callable
    return _reader_sorted(inventory, budget=budget, key=lambda item:
        (_reader_reserve(budget, 112) or (item[0].namespace, item[0].revision, item[0].authorization_id)))


def _verify_prefix(conn: sqlite3.Connection,
                   required: list[tuple[HistoricalSetupIssuance, dict, dict]],
                   target: HistoricalSetupIssuance, budget: _Budget,
                   verified: set[str]) -> SetupIssuanceHistory:
    # Ordered SQL plus complete prefix: no gap/fork/newest-row fallback.
    _check(len(required) == target.revision)
    prior_issue = False
    previous_time: datetime | None = None
    admission_ids: set[str] = _reader_set(budget=budget)
    result: list[HistoricalSetupIssuance] = (_reader_reserve(budget, 64) or [])
    _reader_reserve(budget, 128)  # enumerate/iteration references
    for expected_revision, (entry, record, command) in enumerate(_reader_iter(required, budget=budget), 1):
        _check(entry.revision == expected_revision
               and command['expected_authorization_revision'] == expected_revision - 1)
        _reader_work(entry.created_at, budget)
        _reader_reserve(budget, 192 + 4 * (len(entry.created_at) + 5))
        timestamp = datetime.fromisoformat(entry.created_at.replace('Z', '+00:00'))
        _check(previous_time is None or previous_time <= timestamp)
        previous_time = timestamp
        if record['mode'] == 'withdraw':
            _check(prior_issue)
        else:
            prior_issue = True
        admission_id = record['admission']['record']['id']
        _reader_work(admission_id, budget)
        _check(admission_id not in admission_ids)
        _reader_add(admission_ids, admission_id, budget=budget)
        _reader_work(entry.authorization_id, budget)
        if entry.authorization_id not in verified:
            _admission(conn, record, budget)
            _reader_add(verified, entry.authorization_id, budget=budget)
        _reader_append(result, entry, budget=budget)
    _reader_reserve(budget, 192)  # history result/object, not a deep payload copy
    return SetupIssuanceHistory(_reader_tuple(result, budget=budget))


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
        _reader_reserve(budget, 1024)
        self.identities: dict[str, HistoricalSetupIssuance] = (_reader_reserve(budget, 64) or {})
        self.setups: dict[str, HistoricalSetupIssuance] = (_reader_reserve(budget, 64) or {})
        self.namespaces: dict[str, list[tuple[HistoricalSetupIssuance, dict, dict]]] = (_reader_reserve(budget, 64) or {})
        self.histories: dict[str, SetupIssuanceHistory] = (_reader_reserve(budget, 64) or {})
        self.verified: set[str] = _reader_set(budget=budget)

    def _load(self) -> None:
        self.budget.step()  # loaded reuse still consumes this same remainder
        if self.loaded:
            return
        for entry, record, command in _reader_iter(_inventory(self.conn, self.org_slug, self.budget), budget=self.budget):
            _reader_put(entry, self.identities, entry.authorization_id, budget=self.budget)
            entries = _reader_setdefault(self.namespaces, entry.namespace,
                (_reader_reserve(self.budget, 64) or []), budget=self.budget)
            _reader_reserve(self.budget, 112)
            _reader_append(entries, (entry, record, command), budget=self.budget)
            if entry.setup_bytes is not None:
                _reader_put(entry, self.setups, record['setup']['id'], budget=self.budget)
        self.loaded = True

    def history(self, authorization_id: str) -> SetupIssuanceHistory:
        self.budget.step()
        self._load()
        _reader_work(authorization_id, self.budget, copies=3)  # identity/cache hashes and lookup
        _check(authorization_id in self.identities)
        if authorization_id not in self.histories:
            target = self.identities[authorization_id]
            required = (_reader_reserve(self.budget, 64) or [])
            _reader_work(target.namespace, self.budget)
            for entry in _reader_iter(self.namespaces[target.namespace], budget=self.budget):
                if entry[0].revision <= target.revision:
                    _reader_append(required, entry, budget=self.budget)
            _reader_put(_verify_prefix(self.conn, required, target, self.budget, self.verified),
                self.histories, authorization_id, budget=self.budget)
        _reader_reserve(self.budget, 128)
        return self.histories[authorization_id]

    def setup(self, setup_id: str, setup_bytes: bytes) -> SetupIssuanceHistory:
        self.budget.step()
        self._load()
        _reader_work(setup_id, self.budget, copies=2)
        _check(setup_id in self.setups)
        target = self.setups[setup_id]
        _check(_reader_equal(target.setup_bytes, setup_bytes, budget=self.budget))
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
    Fresh remaining row/byte gates precede each singleton keyset fetch;
    16MiB aggregate accepted raw text/BLOB bytes is enforced; this is not a
    total memory/SQLite/decoded-copy ceiling. A bounded
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


def _read_setup_storage(conn: sqlite3.Connection, *, expected_org_slug: str,
                        reader: _SetupHistoryReader) -> SetupStorage:
    """Validate every setup prefix and its exact latest native pointer.

    Caller owns the coherent transaction and independently recognized complete
    J schema. No schema reader is called here, avoiding recursive admission.
    Revocation/current configuration never changes historical validity.
    """
    try:
        budget = reader.budget
        _reader_reserve(budget, 192)
        _check(type(conn) is sqlite3.Connection and conn.in_transaction)
        _reader_work(expected_org_slug, budget)
        _check(type(expected_org_slug) is str
               and re.fullmatch(r'[a-z][a-z0-9-]{0,62}', expected_org_slug) is not None)
        reader._load()
        admissions: set[str] = _reader_set(budget=budget)
        _reader_reserve(budget, 64)  # values view
        for entries in _reader_iter(reader.namespaces.values(), budget=budget):
            reader.history(entries[-1][0].authorization_id)
            for _, record, _ in _reader_iter(entries, budget=budget):
                if record['mode'] == 'issue':
                    _setup_configuration(record['setup'], _decoded(_reader_json(_reader_b64decode(
                        record['admission']['capture']['snapshot_bytes_base64'], validate=True, budget=reader.budget), reader.budget), reader.budget), budget=reader.budget)
                identity = record['admission']['record']['id']
                _reader_work(identity, budget)
                _check(identity not in admissions)
                _reader_add(admissions, identity, budget=budget)
        _reader_reserve(budget, 64 + 4 * (len('org//coding-setup/') + len(expected_org_slug)))
        prefix = f'org/{expected_org_slug}/coding-setup/'
        pointers = (_reader_reserve(budget, 64) or [])
        _reader_reserve(budget, 352)  # pointer generator and argument tuple
        for row in _bounded_rows(conn, 'workflow_active_authorizations',
            ('namespace', 'authorization_revision_id'), (('text',), ('text',)), reader.budget,
            where='substr(namespace,1,?)=?', args=(len(prefix), prefix), maximum=_ROW_LIMIT):
            namespace, identity = reader.budget.row(row, ('text', 'text'), counted=True, charged=True)
            _reader_work(namespace, budget, copies=2)
            _check(namespace in reader.namespaces
                   and _reader_equal(identity, reader.namespaces[namespace][-1][0].authorization_id, budget=budget))
            _reader_reserve(budget, 96)
            _reader_append(pointers, (namespace, identity), budget=budget)
        _reader_reserve(budget, 128)  # uniqueness generator, before traversal/hash
        _check(len(pointers) == len(reader.namespaces)
               and len(_reader_set((namespace for namespace, _ in pointers), budget=budget)) == len(pointers))
        for identity in _reader_iter(reader.identities, budget=budget):
            _reader_reserve(budget, 336)  # alias generator and argument tuple
            aliases = _reader_list(_bounded_rows(conn, 'workflow_active_authorizations',
                ('namespace', 'authorization_revision_id'), (('text',), ('text',)), reader.budget,
                where='authorization_revision_id=?', args=(identity,), maximum=1), budget=budget)
            if aliases:
                alias = reader.budget.row(aliases[0], ('text', 'text'), counted=True, charged=True)
                _reader_reserve(budget, 128)
                _check(_reader_any_true((_reader_equal(alias, pointer, budget=budget)
                    for pointer in pointers), budget=budget))
            reader.budget.step()
            _reader_reserve(budget, 80)
            bound = _reader_raw_one(conn, 'SELECT 1 FROM workflow_binding_snapshots '
                                 'WHERE authorization_revision_id=? LIMIT 1', (identity,), budget=reader.budget)
            if bound is not None:
                _check(False)
        _reader_reserve(budget, 256)  # storage result and values view
        return SetupStorage(_reader_tuple(reader.identities.values(), budget=budget),
            _reader_tuple(pointers, budget=budget))
    except _ReaderBudgetExhausted:
        raise
    except (sqlite3.Error, CodingSavedRecordError, ValueError, TypeError,
            KeyError, UnicodeError, RecursionError, OverflowError):
        raise CodingAuthorityHistoryError(_REFUSAL) from None


def read_setup_storage(conn: sqlite3.Connection, *, expected_org_slug: str) -> SetupStorage:
    """Complete cold setup closure with a fresh public-call budget."""
    return _read_setup_storage(conn, expected_org_slug=expected_org_slug,
        reader=_SetupHistoryReader(conn, expected_org_slug, _Budget()))
