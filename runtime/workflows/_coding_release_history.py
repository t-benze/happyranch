"""Complete fixed-release publication history, SELECT-only and never authority.

The caller owns a coherent snapshot of an independently admitted complete J
schema. This module does not invoke schema validation, package I/O or current
policy. The typed publication provenance contains the original command and
native admission capture; ordinary document provenance stays unchanged.
"""
from __future__ import annotations

from runtime.infrastructure._workflow_reader import (
    _append as _reader_append,
)

from runtime.infrastructure._workflow_reader import (
    _all_true as _reader_all_true,
    _any_true as _reader_any_true,
)

from runtime.infrastructure._workflow_reader import (
    _dict as _reader_dict,
    _list as _reader_list,
    _set as _reader_set,
    _tuple as _reader_tuple,
)

from runtime.infrastructure._workflow_reader import (
    _b64decode as _reader_b64decode,
    _b64encode as _reader_b64encode,
    _decode as _reader_decode,
    _encode as _reader_encode,
    _reserve as _reader_reserve,
    _sha256 as _reader_sha256,
    _work as _reader_work,
)


import base64
import re
import sqlite3
from dataclasses import dataclass

from runtime.workflows.coding_authority_history import _Budget, _bounded_rows, _capture_admission, _rows
from runtime.workflows.coding_saved_records import _Ancestry, _capture, _json, _timestamp
from runtime.workflows.release_templates import (
    CODING_DEFINITION_DIGEST, validate_coding_release,
)
from runtime.workflows.templates import _canonical_json, _stable_id

NAMESPACE = 'release/happyranch'
PINS = ('workflow-coding-compiler@1', 'workflow-coding-validator@1', 'release-input@1')
PUBLISHER = dict(principal_kind='human', principal_id='founder', proof_kind='founder_bearer')
COMMAND_LIMIT = 1024
_VALUE_LIMIT = 1048576
# Lexical reservation only. The original strict parsers still decide grammar,
# duplicate keys, canonical bytes and all historical meanings. In particular,
# matching a token here does not admit malformed JSON or escaped controls.
_JSON_TOKEN = re.compile(rb'"(?:[^"\\]|\\[\s\S])*"|[^"{}\[\],:\s]+|[{}\[\],:]|\s+')


def _steps(budget: _Budget, count: int) -> None:
    for _ in range(count):
        budget.step()


def _copies(budget: _Budget, size: int) -> None:
    _reader_reserve(budget, size)


def _json_work(raw: bytes, budget: _Budget, *, copies: int, passes: int) -> None:
    """Reserve serialized intermediates and visits BEFORE the pure reader.

    Tokens include every member/key/value, delimiter and whitespace run, even
    in a malformed prefix. Strings stay one lexical token; their whole byte
    length participates in the copy allowance. No decoded tree or token list
    is built here. Copies conservatively include UTF8/JSON/canonical buffers;
    this is finite cooperative work, not a process/SQL memory or time bound.
    """
    _check(type(raw) is bytes and 0 < len(raw) <= _VALUE_LIMIT)
    _copies(budget, copies * len(raw))
    offset = 0
    while True:
        _steps(budget, passes)  # includes terminal and invalid-token attempts
        if offset == len(raw):
            return
        token = _JSON_TOKEN.match(raw, offset)
        _check(token is not None)
        offset = token.end()


def _base64_work(value: object, budget: _Budget, *, limit: int, copies: int) -> bytes:
    _check(type(value) is str and 0 < len(value) <= 4 * ((limit + 2) // 3))
    # Each copy allows ASCII input, decoded bytes, canonical re-encoding and
    # its ASCII text. Charge before even the local preflight decode.
    _copies(budget, copies * 4 * len(value))
    budget.step()
    raw = _reader_b64decode(value, validate=True, budget=budget)
    _check(0 < len(raw) <= limit and _reader_decode(_reader_b64encode(raw, budget=budget), 'ascii', budget=budget) == value)
    return raw


def _capture_work(capture: dict, budget: _Budget) -> None:
    # Preflight + _capture + _capture_admission all decode the snapshot. The
    # nested _json/_snapshot/native snapshot validator/_profiles reconstruct
    # and traverse it; eight lexical passes/copies conservatively cover those
    # visits/serializations, including policy models and sorted projections.
    snapshot = _base64_work(capture['snapshot_bytes_base64'], budget,
                            limit=_VALUE_LIMIT, copies=3)
    _json_work(snapshot, budget, copies=8, passes=8)
    grant = _base64_work(capture['grant_bytes_base64'], budget,
                         limit=_VALUE_LIMIT, copies=2)
    _json_work(grant, budget, copies=4, passes=2)
    encoded = capture['profile_evidence']['ready']['snapshot_bytes_hex']
    _check(type(encoded) is str and 0 < len(encoded) <= 2 * _VALUE_LIMIT)
    # Both _profiles and _capture_admission reconstruct hex bytes. Their
    # original lowercase/even-length/value-equality checks remain decisive.
    _copies(budget, len(encoded))
    _steps(budget, 2)


def _absent(conn: sqlite3.Connection, table: str, column: str, value: str,
            budget: _Budget) -> None:
    # Fixed internal declarations only; include empty and rejected probes.
    budget.step()
    _check(not _reader_list(_rows(conn, f'SELECT 1 FROM {table} WHERE {column}=? LIMIT 1',
                          (_reader_reserve(budget, 80) or (value,)), 1, budget), budget=budget))


def canonical(value: object, *, budget=None) -> bytes:
    return _canonical_json(value, code='coding_release_import_invalid', budget=budget)


def parse_import_command(raw: bytes, *, budget=None) -> dict:
    command = _json(raw, COMMAND_LIMIT, reader_budget=budget)
    if (_reader_set(command, budget=budget) != _reader_set((_reader_reserve(budget, 112) or ('format', 'operation_key', 'expected_current_version')), budget=budget)
            or command['format'] != 'workflow-coding-release-import-command@1'
            or type(command['operation_key']) is not str
            or re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', command['operation_key']) is None
            or type(command['expected_current_version']) is not int
            or command['expected_current_version'] not in (0, 1)):
        raise ValueError('coding_release_import_invalid')
    return command


def _check(condition: bool) -> None:
    if not condition:
        raise ValueError('coding_release_import_history_invalid')


def _digest(raw: bytes, *, budget=None) -> str:
    import hashlib
    return _reader_sha256(raw, budget=budget).hexdigest()


@dataclass(frozen=True)
class ReleaseImportHistory:
    rows: tuple[tuple[str, dict], ...]
    command_bytes: bytes
    provenance: dict

    def matches(self, table: str, row: dict, *, budget=None) -> bool:
        _reader_work((_reader_reserve(budget, 112) or (table, row, self.rows)), budget)
        return _reader_any_true((table == name and row.keys() == expected.keys()
                   and _reader_all_true((type(row[key]) is type(value) and row[key] == value
                           for key, value in expected.items()), budget=budget) for name, expected in self.rows), budget=budget)

    @property
    def identity_id(self) -> str:
        return self.rows[0][1]['id']

    @property
    def version_id(self) -> str:
        return self.rows[2][1]['id']

    def template(self, *, budget=None):
        _reader_work(self.rows, budget)
        from runtime.workflows.templates import WorkflowTemplateVersion
        row = self.rows[2][1]
        return WorkflowTemplateVersion(self.identity_id, self.version_id, NAMESPACE, 'coding', 1,
            row['definition_bytes'], row['definition_digest'], *PINS,
            _reader_dict(PUBLISHER, budget=budget), row['published_at'])


def publication_rows(*, org_slug: str, definition: bytes, provenance: dict, budget=None) -> ReleaseImportHistory:
    """Construct the six exact retained rows; no writes or admission claim."""
    _reader_work((_reader_reserve(budget, 112) or (org_slug, definition, provenance)), budget)
    validate_coding_release(definition, budget=budget)
    raw = _reader_b64decode(provenance['command_bytes_base64'], validate=True, budget=budget)
    command = parse_import_command(raw, budget=budget)
    _check(command['expected_current_version'] == 0)
    digest = _digest(raw, budget=budget)
    identity = _stable_id('workflow-template', NAMESPACE, 'coding', budget=budget)
    draft = _stable_id('workflow-template-draft', identity, 1, digest, budget=budget)
    version = _stable_id('workflow-template-version', identity, 1, CODING_DEFINITION_DIGEST, budget=budget)
    now = provenance['created_at']
    publisher = _reader_decode(canonical(provenance, budget=budget), 'utf8', budget=budget)
    common = (_reader_reserve(budget, 960) or dict(namespace=NAMESPACE, template_name='coding', definition_bytes=definition,
        definition_digest=CODING_DEFINITION_DIGEST, compiler_pin=PINS[0], validator_pin=PINS[1], source_pin=PINS[2]))
    rows = (_reader_reserve(budget, 160) or (
        (_reader_reserve(budget, 96) or ('workflow_template_identities', (_reader_reserve(budget, 832) or dict(id=identity, namespace=NAMESPACE, template_name='coding',
            current_version=1, status='active', created_at=now)))),
        (_reader_reserve(budget, 96) or ('workflow_template_drafts', dict(id=draft, **common, author_principal=publisher, created_at=now))),
        (_reader_reserve(budget, 96) or ('workflow_template_versions', dict(id=version, draft_id=draft, version=1, **common,
            published_by=publisher, published_at=now))),
        (_reader_reserve(budget, 96) or ('workflow_template_identity_versions', (_reader_reserve(budget, 576) or dict(template_identity_id=identity, version=1,
            template_version_id=version, content_digest=CODING_DEFINITION_DIGEST)))),
        (_reader_reserve(budget, 96) or ('workflow_template_publish_operations', (_reader_reserve(budget, 1088) or dict(org_slug=org_slug, principal='human:founder',
            operation_key=command['operation_key'], request_digest=digest, template_identity_id=identity,
            expected_current_version=0, result_version=1, template_version_id=version)))),
        (_reader_reserve(budget, 96) or ('workflow_admission_records', _reader_dict(provenance['admission']['record'], budget=budget))),
    ))
    return ReleaseImportHistory(rows, raw, provenance)


def _fetch(conn: sqlite3.Connection, table: str, expected: dict, where: str,
           args: tuple, budget: _Budget) -> list[dict]:
    # Table/column/where fragments are exclusively internal fixed declarations.
    _steps(budget, 2 * len(expected) + 1)
    types = _reader_tuple(((_reader_reserve(budget, 448) or {str: 'text', bytes: 'blob', int: 'integer'})[type(value)]
                  for value in expected.values()), budget=budget)
    # Gate the WHOLE row against remaining shared bytes/candidates before
    # fetching. Invalid native classes and the overflow candidate still refuse.
    result = (_reader_reserve(budget, 64) or [])
    for row in _bounded_rows(conn, table, _reader_tuple(expected, budget=budget),
            _reader_tuple(((_reader_reserve(budget, 80) or (kind,)) for kind in types), budget=budget), budget, where=where, args=args, maximum=1):
        # row() retains its strict positive-value contract and computes UTF8
        # lengths again. Reserve that additional encoding before calling it.
        _steps(budget, len(expected))
        _copies(budget, sum(4 * len(value) for value in row[:len(expected)] if type(value) is str))
        _reader_append(result, _reader_dict(zip(expected, budget.row(row, types, counted=True, charged=True), strict=True), budget=budget), budget=budget)
    return result


def read_release_import(conn: sqlite3.Connection, *, expected_org_slug: str | None,
                        budget: _Budget | None = None) -> ReleaseImportHistory | None:
    """Prove the whole immutable import, including native admission/publication.

    No ID exemptions: every supported row must equal its independently derived
    full typed row. Orphan/extra release drafts, versions, links and operations
    refuse. Revoked present authority never invalidates genuine old history.
    Omitted/None owns one allowance; a supplied actual _Budget stays cumulative.
    Local pre-accounting reserves nested pure-reader work before expansion;
    their independent stricter parser/value/ancestry limits remain in force.
    """
    try:
        _reader_reserve(budget, 192)
        _check(type(conn) is sqlite3.Connection and conn.in_transaction)
        _check(budget is None or type(budget) is _Budget)
        budget = _Budget() if budget is None else budget
        budget.step()
        # Seed discovery is bounded before fetch; no compiler-pin-only admission.
        seed = _reader_list(_bounded_rows(conn, 'workflow_template_versions', ('published_by',),
            (('text',),), budget, where='namespace=?', args=(_reader_reserve(budget, 80) or (NAMESPACE,)), maximum=1), budget=budget)
        if not seed:
            for table in ('workflow_template_drafts', 'workflow_template_identities'):
                _absent(conn, table, 'namespace', NAMESPACE, budget)
            _steps(budget, 8)
            _copies(budget, 512)  # fixed stable-ID canonical preimage/result
            identity = _stable_id('workflow-template', NAMESPACE, 'coding', budget=budget)
            for table in ('workflow_template_identity_versions', 'workflow_template_publish_operations'):
                _absent(conn, table, 'template_identity_id', identity, budget)
            return None
        _copies(budget, 4 * len(seed[0][0]))  # row()'s extra UTF8 length encoding
        (text,) = budget.row(seed[0], ('text',), counted=True, charged=True)
        _copies(budget, 4 * len(text))  # before UTF8 expansion, even invalid text
        provenance_raw = _reader_encode(text, 'utf8', budget=budget)
        # Reserve outer JSON/canonicalization, capture/pin/profile traversal,
        # both admission canonicals and publication provenance reconstruction.
        # Encoded snapshot/grant JSON is accounted separately before _capture.
        _json_work(provenance_raw, budget, copies=12, passes=6)
        provenance = _json(provenance_raw, _VALUE_LIMIT, reader_budget=budget)
        _check(_reader_set(provenance, budget=budget) == _reader_set((_reader_reserve(budget, 176) or ('format', 'org_slug', 'publisher', 'command_bytes_base64', 'command_digest', 'admission', 'created_at')), budget=budget))
        _check(provenance['format'] == 'workflow-coding-release-import-provenance@1'
               and canonical(provenance['publisher'], budget=budget) == canonical(PUBLISHER, budget=budget))
        org_slug = provenance['org_slug']
        _check(type(org_slug) is str and re.fullmatch(r'[a-z][a-z0-9-]{0,62}', org_slug) is not None
               and (expected_org_slug is None or org_slug == expected_org_slug))
        _timestamp(provenance['created_at'], reader_budget=budget)
        raw = _base64_work(provenance['command_bytes_base64'], budget,
                           limit=COMMAND_LIMIT, copies=2)
        _json_work(raw, budget, copies=8, passes=2)  # now and publication_rows
        parse_import_command(raw, budget=budget)
        _check(provenance['command_digest'] == _digest(raw, budget=budget))
        admission = provenance['admission']
        _check(type(admission) is dict and _reader_set(admission, budget=budget) == _reader_set((_reader_reserve(budget, 96) or ('record', 'capture')), budget=budget))
        _capture_work(admission['capture'], budget)
        grant, _ = _capture(admission['capture'], org_slug, _Ancestry(), reader_budget=budget)
        capture = admission['capture']
        expected_admission = (_reader_reserve(budget, 704) or dict(id=capture['admission_record_id'], namespace=capture['pin']['namespace'],
            generation=capture['pin']['generation'], request_digest=_digest(raw, budget=budget), admitted_by='human:founder'))
        _check(grant == raw and canonical(admission['record'], budget=budget) == canonical(expected_admission, budget=budget))
        # The shared native helper re-encodes TEXT for row()/strict text
        # checks. Its original journal publisher/invocation/recovery fields
        # are not all in the capture. Read only bounded integer lengths first,
        # charging those candidates too; never fetch private payloads and then
        # charge their expansion after the helper has already performed it.
        ready = capture['profile_evidence']['ready']
        pointer = capture['profile_evidence']['pointer']
        for table, columns, where, args in (_reader_reserve(budget, 96) or (
            (_reader_reserve(budget, 128) or ('workflow_admission_records', ('id', 'namespace', 'request_digest', 'admitted_by'),
             'id=?', (_reader_reserve(budget, 80) or (expected_admission['id'],)))),
            (_reader_reserve(budget, 128) or ('workflow_publication_journals', ('id', 'namespace', 'snapshot_digest', 'publisher',
                'publisher_invocation', 'state', 'recovery_owner', 'file_phase_owner'),
             'id=? OR (namespace=? AND generation=? AND snapshot_digest=?)',
             (_reader_reserve(budget, 128) or (pointer['journal_id'], ready['namespace'], ready['generation'], ready['snapshot_digest'])))),
        )):
            _steps(budget, 2 * len(columns) + 1)
            sizes = ','.join(f'CASE WHEN typeof("{column}")=\'text\' '
                f'THEN length(CAST("{column}" AS BLOB)) ELSE 0 END' for column in columns)
            for lengths in _rows(conn, f'SELECT {sizes} FROM {table} WHERE {where} LIMIT 2',
                                  args, 1, budget):
                _steps(budget, len(columns))
                _check(_reader_all_true((type(size) is int and 0 <= size <= _VALUE_LIMIT for size in lengths), budget=budget))
                _copies(budget, 2 * sum(lengths))
        _capture_admission(conn, expected_admission, capture, budget)
        # The release is already saved; validation uses its bytes, not package I/O.
        definition_rows = _reader_list(_bounded_rows(conn, 'workflow_template_versions', ('definition_bytes',),
            (('blob',),), budget, where='namespace=?', args=(_reader_reserve(budget, 80) or (NAMESPACE,)), maximum=1, value_limit=634), budget=budget)
        (definition,) = budget.row(definition_rows[0], ('blob',), counted=True, charged=True)
        _check(len(definition) == 634)
        _json_work(definition, budget, copies=4, passes=2)
        # Six fixed row dictionaries (at most 13 columns each), three stable
        # IDs/digests and their bounded literals/scalars. Dynamic provenance,
        # command and definition intermediates were reserved above. Include
        # the six-row matches traversal, before constructing any returned row.
        _steps(budget, 6 * 32)
        _copies(budget, 4096)
        history = publication_rows(org_slug=org_slug, definition=definition, provenance=provenance, budget=budget)
        for table, expected in history.rows:
            budget.step()
            if table in ('workflow_template_drafts', 'workflow_template_versions', 'workflow_template_identities'):
                where, args = (_reader_reserve(budget, 96) or ('namespace=? OR id=?', _reader_reserve(budget, 96) or (NAMESPACE, expected['id'])))
            elif table in ('workflow_template_identity_versions', 'workflow_template_publish_operations'):
                where, args = (_reader_reserve(budget, 96) or ('template_identity_id=? OR template_version_id=?', _reader_reserve(budget, 96) or (history.identity_id, history.version_id)))
            else:
                where, args = (_reader_reserve(budget, 96) or ('id=?', _reader_reserve(budget, 80) or (expected['id'],)))
            rows = _fetch(conn, table, expected, where, args, budget)
            _check(len(rows) == 1 and history.matches(table, rows[0]))
        # Complete queued/native inventory closes downstream rows for both origins.
        return history
    except ((ValueError, TypeError, KeyError, IndexError, UnicodeError, RecursionError, OverflowError, sqlite3.Error)):
        raise ValueError('coding_release_import_history_invalid') from None
