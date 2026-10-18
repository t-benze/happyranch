"""Complete fixed-release publication history, SELECT-only and never authority.

The caller owns a coherent snapshot of an independently admitted complete J
schema. This module does not invoke schema validation, package I/O or current
policy. The typed publication provenance contains the original command and
native admission capture; ordinary document provenance stays unchanged.
"""
from __future__ import annotations

import base64
import re
import sqlite3
from dataclasses import dataclass

from runtime.workflows.coding_authority_history import _Budget, _capture_admission, _rows
from runtime.workflows.coding_saved_records import _Ancestry, _capture, _json, _timestamp
from runtime.workflows.release_templates import (
    CODING_DEFINITION_DIGEST, validate_coding_release,
)
from runtime.workflows.templates import _canonical_json, _stable_id

NAMESPACE = 'release/happyranch'
PINS = ('workflow-coding-compiler@1', 'workflow-coding-validator@1', 'release-input@1')
PUBLISHER = dict(principal_kind='human', principal_id='founder', proof_kind='founder_bearer')
COMMAND_LIMIT = 1024


def canonical(value: object) -> bytes:
    return _canonical_json(value, code='coding_release_import_invalid')


def parse_import_command(raw: bytes) -> dict:
    command = _json(raw, COMMAND_LIMIT)
    if (set(command) != {'format', 'operation_key', 'expected_current_version'}
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


def _digest(raw: bytes) -> str:
    import hashlib
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class ReleaseImportHistory:
    rows: tuple[tuple[str, dict], ...]
    command_bytes: bytes
    provenance: dict

    def matches(self, table: str, row: dict) -> bool:
        return any(table == name and row.keys() == expected.keys()
                   and all(type(row[key]) is type(value) and row[key] == value
                           for key, value in expected.items()) for name, expected in self.rows)

    @property
    def identity_id(self) -> str:
        return self.rows[0][1]['id']

    @property
    def version_id(self) -> str:
        return self.rows[2][1]['id']

    def template(self):
        from runtime.workflows.templates import WorkflowTemplateVersion
        row = self.rows[2][1]
        return WorkflowTemplateVersion(self.identity_id, self.version_id, NAMESPACE, 'coding', 1,
            row['definition_bytes'], row['definition_digest'], *PINS,
            dict(PUBLISHER), row['published_at'])


def publication_rows(*, org_slug: str, definition: bytes, provenance: dict) -> ReleaseImportHistory:
    """Construct the six exact retained rows; no writes or admission claim."""
    validate_coding_release(definition)
    raw = base64.b64decode(provenance['command_bytes_base64'], validate=True)
    command = parse_import_command(raw)
    _check(command['expected_current_version'] == 0)
    digest = _digest(raw)
    identity = _stable_id('workflow-template', NAMESPACE, 'coding')
    draft = _stable_id('workflow-template-draft', identity, 1, digest)
    version = _stable_id('workflow-template-version', identity, 1, CODING_DEFINITION_DIGEST)
    now = provenance['created_at']
    publisher = canonical(provenance).decode('utf8')
    common = dict(namespace=NAMESPACE, template_name='coding', definition_bytes=definition,
        definition_digest=CODING_DEFINITION_DIGEST, compiler_pin=PINS[0], validator_pin=PINS[1], source_pin=PINS[2])
    rows = (
        ('workflow_template_identities', dict(id=identity, namespace=NAMESPACE, template_name='coding',
            current_version=1, status='active', created_at=now)),
        ('workflow_template_drafts', dict(id=draft, **common, author_principal=publisher, created_at=now)),
        ('workflow_template_versions', dict(id=version, draft_id=draft, version=1, **common,
            published_by=publisher, published_at=now)),
        ('workflow_template_identity_versions', dict(template_identity_id=identity, version=1,
            template_version_id=version, content_digest=CODING_DEFINITION_DIGEST)),
        ('workflow_template_publish_operations', dict(org_slug=org_slug, principal='human:founder',
            operation_key=command['operation_key'], request_digest=digest, template_identity_id=identity,
            expected_current_version=0, result_version=1, template_version_id=version)),
        ('workflow_admission_records', dict(provenance['admission']['record'])),
    )
    return ReleaseImportHistory(rows, raw, provenance)


def _fetch(conn: sqlite3.Connection, table: str, expected: dict, where: str,
           args: tuple, budget: _Budget) -> list[dict]:
    # Table/column/where fragments are exclusively internal fixed declarations.
    projections, types = [], []
    for column, value in expected.items():
        kind = {str: 'text', bytes: 'blob', int: 'integer'}[type(value)]
        bound = f' AND length(CAST("{column}" AS BLOB))<=1048576' if kind != 'integer' else ''
        projections.append(f'CASE WHEN typeof("{column}")=\'{kind}\'{bound} THEN "{column}" ELSE NULL END')
        types.append(kind)
    query = 'SELECT ' + ','.join(projections + [f'typeof("{key}")' for key in expected])
    query += f' FROM {table} WHERE {where} LIMIT 2'
    return [dict(zip(expected, budget.row(row, tuple(types), counted=True), strict=True))
            for row in _rows(conn, query, args, 1, budget)]


def read_release_import(conn: sqlite3.Connection, *, expected_org_slug: str | None) -> ReleaseImportHistory | None:
    """Prove the whole immutable import, including native admission/publication.

    No ID exemptions: every supported row must equal its independently derived
    full typed row. Orphan/extra release drafts, versions, links and operations
    refuse. Revoked present authority never invalidates genuine old history.
    """
    try:
        _check(type(conn) is sqlite3.Connection and conn.in_transaction)
        budget = _Budget()
        # Seed discovery is bounded before fetch; no compiler-pin-only admission.
        seed = list(_rows(conn, "SELECT CASE WHEN typeof(published_by)='text' "
            "AND length(CAST(published_by AS BLOB))<=1048576 THEN published_by ELSE NULL END,"
            "typeof(published_by) FROM workflow_template_versions WHERE namespace=? LIMIT 2",
            (NAMESPACE,), 1, budget))
        if not seed:
            for table in ('workflow_template_drafts', 'workflow_template_identities'):
                _check(conn.execute(f'SELECT 1 FROM {table} WHERE namespace=? LIMIT 1',
                                    (NAMESPACE,)).fetchone() is None)
            identity = _stable_id('workflow-template', NAMESPACE, 'coding')
            for table in ('workflow_template_identity_versions', 'workflow_template_publish_operations'):
                _check(conn.execute(f'SELECT 1 FROM {table} WHERE template_identity_id=? LIMIT 1',
                                    (identity,)).fetchone() is None)
            return None
        (text,) = budget.row(seed[0], ('text',), counted=True)
        provenance = _json(text.encode('utf8'), 1048576)
        _check(set(provenance) == {'format', 'org_slug', 'publisher', 'command_bytes_base64',
                                 'command_digest', 'admission', 'created_at'})
        _check(provenance['format'] == 'workflow-coding-release-import-provenance@1'
               and canonical(provenance['publisher']) == canonical(PUBLISHER))
        org_slug = provenance['org_slug']
        _check(type(org_slug) is str and re.fullmatch(r'[a-z][a-z0-9-]{0,62}', org_slug) is not None
               and (expected_org_slug is None or org_slug == expected_org_slug))
        _timestamp(provenance['created_at'])
        raw = base64.b64decode(provenance['command_bytes_base64'], validate=True)
        _check(base64.b64encode(raw).decode('ascii') == provenance['command_bytes_base64'])
        parse_import_command(raw)
        _check(provenance['command_digest'] == _digest(raw))
        admission = provenance['admission']
        _check(type(admission) is dict and set(admission) == {'record', 'capture'})
        grant, _ = _capture(admission['capture'], org_slug, _Ancestry())
        capture = admission['capture']
        expected_admission = dict(id=capture['admission_record_id'], namespace=capture['pin']['namespace'],
            generation=capture['pin']['generation'], request_digest=_digest(raw), admitted_by='human:founder')
        _check(grant == raw and canonical(admission['record']) == canonical(expected_admission))
        _capture_admission(conn, expected_admission, capture, budget)
        # The release is already saved; validation uses its bytes, not package I/O.
        definition_rows = list(_rows(conn, "SELECT CASE WHEN typeof(definition_bytes)='blob' "
            "AND length(definition_bytes)=634 THEN definition_bytes ELSE NULL END,typeof(definition_bytes) "
            'FROM workflow_template_versions WHERE namespace=? LIMIT 2', (NAMESPACE,), 1, budget))
        (definition,) = budget.row(definition_rows[0], ('blob',), counted=True)
        history = publication_rows(org_slug=org_slug, definition=definition, provenance=provenance)
        for table, expected in history.rows:
            if table in ('workflow_template_drafts', 'workflow_template_versions', 'workflow_template_identities'):
                where, args = 'namespace=? OR id=?', (NAMESPACE, expected['id'])
            elif table in ('workflow_template_identity_versions', 'workflow_template_publish_operations'):
                where, args = 'template_identity_id=? OR template_version_id=?', (history.identity_id, history.version_id)
            else:
                where, args = 'id=?', (expected['id'],)
            rows = _fetch(conn, table, expected, where, args, budget)
            _check(len(rows) == 1 and history.matches(table, rows[0]))
        # Unsupported activations/bindings cannot use import as execution authority.
        for table, column in (('workflow_activations', 'template_version_id'),
                              ('workflow_binding_snapshots', 'template_version_id')):
            _check(conn.execute(f'SELECT 1 FROM {table} WHERE {column}=? LIMIT 1',
                                (history.version_id,)).fetchone() is None)
        return history
    except (ValueError, TypeError, KeyError, IndexError, UnicodeError, RecursionError, sqlite3.Error):
        raise ValueError('coding_release_import_history_invalid') from None
