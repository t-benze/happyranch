"""SELECT-only saved coding release with distinct human and system origins.

Callers own the admitted schema and coherent snapshot. No package observation,
current authority or initializer belongs in these history readers.
"""
from __future__ import annotations

from runtime.infrastructure._workflow_reader import (
    _all_true as _reader_all_true,
    _any_true as _reader_any_true,
)

from runtime.infrastructure._workflow_reader import (
    _list as _reader_list,
)

from runtime.infrastructure._workflow_reader import (
    _decode as _reader_decode,
    _encode as _reader_encode,
    _reserve as _reader_reserve,
    _work as _reader_work,
)


import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from runtime.workflows._coding_release_history import (
    NAMESPACE, PINS, ReleaseImportHistory, _absent, _check, _copies, _fetch,
    _json_work, _steps, canonical, read_release_import,
)
from runtime.workflows.coding_authority_history import _Budget, _bounded_rows
from runtime.workflows.coding_saved_records import _json
from runtime.workflows.release_templates import CODING_DEFINITION_DIGEST, validate_coding_release
from runtime.workflows.templates import WorkflowTemplateVersion, _stable_id

BUILTIN_FORMAT = 'workflow-coding-builtin-registration@1'


@dataclass(frozen=True)
class RegisteredCodingRelease:
    origin: Literal['human_import', 'system_builtin']
    rows: tuple[tuple[str, dict], ...]
    org_slug: str
    human_import: ReleaseImportHistory | None = None

    @property
    def identity_id(self) -> str:
        return self.rows[0][1]['id']

    @property
    def version_id(self) -> str:
        return self.rows[2][1]['id']

    def matches(self, table: str, row: dict, *, budget=None) -> bool:
        _reader_work((_reader_reserve(budget, 112) or (table, row, self.rows)), budget)
        return _reader_any_true((table == name and row.keys() == expected.keys()
            and _reader_all_true((type(row[key]) is type(value) and row[key] == value
                    for key, value in expected.items()), budget=budget) for name, expected in self.rows), budget=budget)

    def template(self, *, budget=None) -> WorkflowTemplateVersion:
        _reader_work(self.rows, budget)
        if self.human_import is not None:
            return self.human_import.template(budget=budget)
        row = self.rows[2][1]
        publisher = (_reader_reserve(budget, 448) or dict(kind='system', component='coding-builtin', provenance_format=BUILTIN_FORMAT))
        return WorkflowTemplateVersion(self.identity_id, self.version_id, NAMESPACE, 'coding', 1,
            row['definition_bytes'], row['definition_digest'], *PINS, publisher, row['published_at'])


def registration_rows(org_slug: str, definition: bytes, registered_at: str, *, budget=None) -> RegisteredCodingRelease:
    """Four exact rows, without publication/admission operations or a human actor."""
    _reader_work((_reader_reserve(budget, 112) or (org_slug, definition, registered_at)), budget)
    validate_coding_release(definition, budget=budget)
    _check(type(org_slug) is str and re.fullmatch(r'[a-z][a-z0-9-]{0,62}', org_slug) is not None)
    _check(type(registered_at) is str)
    stamp = datetime.strptime(registered_at, '%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=timezone.utc)
    _check(stamp.strftime('%Y-%m-%dT%H:%M:%S.%fZ') == registered_at)
    identity = _stable_id('workflow-template', NAMESPACE, 'coding', budget=budget)
    version = _stable_id('workflow-template-version', identity, 1, CODING_DEFINITION_DIGEST, budget=budget)
    draft = _stable_id('workflow-coding-builtin-draft', org_slug, identity, 1, CODING_DEFINITION_DIGEST, budget=budget)
    provenance = _reader_decode(canonical((_reader_reserve(budget, 1344) or dict(format=BUILTIN_FORMAT, org_slug=org_slug, registrar='builtin:coding-v1',
        identity=NAMESPACE + '/coding', version=1, definition_digest=CODING_DEFINITION_DIGEST,
        compiler_pin=PINS[0], validator_pin=PINS[1], source_pin=PINS[2], registered_at=registered_at)), budget=budget), 'utf8', budget=budget)
    common = (_reader_reserve(budget, 960) or dict(namespace=NAMESPACE, template_name='coding', definition_bytes=definition,
        definition_digest=CODING_DEFINITION_DIGEST, compiler_pin=PINS[0], validator_pin=PINS[1], source_pin=PINS[2]))
    return RegisteredCodingRelease('system_builtin', (_reader_reserve(budget, 128) or (
        (_reader_reserve(budget, 96) or ('workflow_template_identities', (_reader_reserve(budget, 832) or dict(id=identity, namespace=NAMESPACE, template_name='coding',
            current_version=1, status='active', created_at=registered_at)))),
        (_reader_reserve(budget, 96) or ('workflow_template_drafts', dict(id=draft, **common, author_principal=provenance, created_at=registered_at))),
        (_reader_reserve(budget, 96) or ('workflow_template_versions', dict(id=version, draft_id=draft, version=1, **common,
            published_by=provenance, published_at=registered_at))),
        (_reader_reserve(budget, 96) or ('workflow_template_identity_versions', (_reader_reserve(budget, 576) or dict(template_identity_id=identity, version=1,
            template_version_id=version, content_digest=CODING_DEFINITION_DIGEST)))),
    )), org_slug)


def read_registered_release(conn: sqlite3.Connection, *, expected_org_slug: str | None,
                            budget: _Budget | None = None) -> RegisteredCodingRelease | None:
    """Dispatch closed saved provenance; every native column must reconstruct."""
    try:
        _reader_reserve(budget, 192)
        _check(type(conn) is sqlite3.Connection and conn.in_transaction)
        _check(budget is None or type(budget) is _Budget)
        budget = _Budget() if budget is None else budget
        _steps(budget, 10)
        _copies(budget, 512)
        identity = _stable_id('workflow-template', NAMESPACE, 'coding', budget=budget)
        version = _stable_id('workflow-template-version', identity, 1, CODING_DEFINITION_DIGEST, budget=budget)
        seed = _reader_list(_bounded_rows(conn, 'workflow_template_versions', ('published_by',),
            (('text',),), budget, where='namespace=? OR id=?', args=(_reader_reserve(budget, 96) or (NAMESPACE, version)), maximum=1), budget=budget)
        if not seed:
            for table in ('workflow_template_identities', 'workflow_template_drafts', 'workflow_template_versions'):
                _absent(conn, table, 'namespace', NAMESPACE, budget)
            _absent(conn, 'workflow_template_identities', 'id', identity, budget)
            for table in ('workflow_template_identity_versions', 'workflow_template_publish_operations'):
                _absent(conn, table, 'template_identity_id', identity, budget)
                _absent(conn, table, 'template_version_id', version, budget)
            return None
        _copies(budget, 8 * len(seed[0][0]))
        (text,) = budget.row(seed[0], ('text',), counted=True, charged=True)
        raw = _reader_encode(text, 'utf8', budget=budget)
        _json_work(raw, budget, copies=4, passes=2)
        provenance = _json(raw, 1048576, reader_budget=budget)
        if provenance.get('format') == 'workflow-coding-release-import-provenance@1':
            human = read_release_import(conn, expected_org_slug=expected_org_slug, budget=budget)
            _check(human is not None)
            return RegisteredCodingRelease('human_import', human.rows, human.provenance['org_slug'], human)
        _check(provenance.get('format') == BUILTIN_FORMAT)
        org = provenance['org_slug']
        _check(expected_org_slug is None or org == expected_org_slug)
        definition_rows = _reader_list(_bounded_rows(conn, 'workflow_template_versions', ('definition_bytes',),
            (('blob',),), budget, where='namespace=? OR id=?', args=(_reader_reserve(budget, 96) or (NAMESPACE, version)), maximum=1, value_limit=634), budget=budget)
        (definition,) = budget.row(definition_rows[0], ('blob',), counted=True, charged=True)
        _json_work(definition, budget, copies=4, passes=2)
        _steps(budget, 4 * 32)
        _copies(budget, 4096 + 4 * len(raw))
        release = registration_rows(org, definition, provenance['registered_at'], budget=budget)
        # Exact canonical TEXT reconstruction closes every provenance field/type.
        for table, expected in release.rows:
            if table == 'workflow_template_identity_versions':
                where, args = (_reader_reserve(budget, 96) or ('template_identity_id=? OR template_version_id=?', _reader_reserve(budget, 96) or (identity, version)))
            elif table == 'workflow_template_drafts':
                where, args = (_reader_reserve(budget, 96) or ('namespace=? OR id=? OR id IN (SELECT draft_id FROM workflow_template_versions WHERE id=?)', _reader_reserve(budget, 112) or (NAMESPACE, expected['id'], version)))
            else:
                where, args = (_reader_reserve(budget, 96) or ('namespace=? OR id=?', _reader_reserve(budget, 96) or (NAMESPACE, expected['id'])))
            rows = _fetch(conn, table, expected, where, args, budget)
            _check(len(rows) == 1 and release.matches(table, rows[0], budget=budget))
        for column, value in (_reader_reserve(budget, 96) or (_reader_reserve(budget, 96) or (('template_identity_id', identity), _reader_reserve(budget, 96) or ('template_version_id', version)))):
            _absent(conn, 'workflow_template_publish_operations', column, value, budget)
        return release
    except ((ValueError, TypeError, KeyError, IndexError, UnicodeError, RecursionError, OverflowError, sqlite3.Error)):
        raise ValueError('coding_registered_release_history_invalid') from None
