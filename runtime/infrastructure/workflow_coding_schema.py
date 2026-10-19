"""Exact additive J definitions and closed per-family coding admission.

K2b/K2c admit complete historical setup/import/mapping/source and agent-read
role-default histories and K3a queued starts on unchanged J, including metadata-only schema reads.
Each later producer must ship its own complete readers. No schema installation,
repository resolution or current authority is provided by this module.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from runtime.workflows.coding_authority_history import _Budget

# Accepted TASK10693 step34: 3987 UTF8 bytes, SHA256
# 2c4b556671ef76582cdcbaed1e68d8575d9648ce29773f52c3d947ceaeeb0d70.
CANONICAL_WORKFLOW_CODING_DDL = """\
CREATE TABLE workflow_coding_mappings (
  id TEXT PRIMARY KEY,
  org_slug TEXT NOT NULL,
  release_identity TEXT NOT NULL CHECK(release_identity='release/happyranch/coding'),
  generation INTEGER NOT NULL CHECK(typeof(generation)='integer' AND generation>0),
  principal TEXT NOT NULL,
  operation_key TEXT NOT NULL,
  request_digest TEXT NOT NULL,
  mapping_bytes BLOB NOT NULL CHECK(typeof(mapping_bytes)='blob' AND length(mapping_bytes) BETWEEN 1 AND 1048576),
  mapping_digest TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  UNIQUE(org_slug,release_identity,generation),
  UNIQUE(org_slug,principal,operation_key)
);
CREATE TABLE workflow_coding_runs (
  instance_id TEXT PRIMARY KEY REFERENCES workflow_instances(id),
  mapping_id TEXT NOT NULL REFERENCES workflow_coding_mappings(id),
  cas_version INTEGER NOT NULL CHECK(typeof(cas_version)='integer' AND cas_version>0),
  current_revision INTEGER NOT NULL CHECK(typeof(current_revision)='integer' AND current_revision BETWEEN 0 AND 3),
  state TEXT NOT NULL CHECK(state IN ('development','code_review','qa','manager_closeout','held','complete','cancelled')),
  current_attempt_id TEXT REFERENCES workflow_coding_attempts(id),
  last_event_id TEXT REFERENCES workflow_coding_events(id),
  UNIQUE(instance_id,current_attempt_id)
);
CREATE TABLE workflow_coding_attempts (
  id TEXT PRIMARY KEY,
  instance_id TEXT NOT NULL REFERENCES workflow_coding_runs(instance_id),
  revision INTEGER NOT NULL CHECK(typeof(revision)='integer' AND revision BETWEEN 1 AND 3),
  stage TEXT NOT NULL CHECK(stage IN ('development','code_review','qa','manager_closeout')),
  attempt_sequence INTEGER NOT NULL CHECK(typeof(attempt_sequence)='integer' AND attempt_sequence>0),
  assignment_generation INTEGER NOT NULL CHECK(typeof(assignment_generation)='integer' AND assignment_generation>0),
  predecessor_id TEXT REFERENCES workflow_coding_attempts(id),
  task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id),
  principal TEXT NOT NULL,
  session_id TEXT,
  result_id INTEGER UNIQUE REFERENCES task_results(id),
  submission_id TEXT REFERENCES workflow_submissions(id),
  review_request_id TEXT UNIQUE REFERENCES workflow_review_requests(id),
  state TEXT NOT NULL CHECK(state IN ('queued','claimed','running','uncertain','blocked','failed','completed','cancelled','superseded')),
  scope_bytes BLOB NOT NULL CHECK(typeof(scope_bytes)='blob' AND length(scope_bytes) BETWEEN 1 AND 1048576),
  scope_digest TEXT NOT NULL UNIQUE,
  UNIQUE(instance_id,revision,stage,attempt_sequence),
  UNIQUE(instance_id,stage,assignment_generation),
  CHECK(predecessor_id IS NULL OR predecessor_id!=id)
);
CREATE TABLE workflow_coding_events (
  id TEXT PRIMARY KEY,
  instance_id TEXT NOT NULL REFERENCES workflow_coding_runs(instance_id),
  event_seq INTEGER NOT NULL CHECK(typeof(event_seq)='integer' AND event_seq>0),
  attempt_id TEXT REFERENCES workflow_coding_attempts(id),
  org_slug TEXT NOT NULL,
  principal TEXT NOT NULL,
  operation_key TEXT NOT NULL,
  request_digest TEXT NOT NULL,
  event_kind TEXT NOT NULL CHECK(event_kind IN ('run_started','stage_queued','session_bound','launch_reserved','launch_observed','artifact_staged','review_staged','callback_recorded','terminal_recorded','advance','return','replacement','held','paused','resumed','cancelled','ci_observed','merge_reserved','merge_observed','contribution_recorded','host_reconciled','cleanup_settled','manager_completed','manager_control_queued','manager_control_session_bound','manager_control_launch_reserved','manager_control_launch_observed','manager_control_selected','manager_control_callback_recorded','manager_control_terminal_recorded','manager_control_cleanup_settled','manager_control_settled')),
  previous_digest TEXT,
  event_bytes BLOB NOT NULL CHECK(typeof(event_bytes)='blob' AND length(event_bytes) BETWEEN 1 AND 16777216),
  event_digest TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  UNIQUE(instance_id,event_seq),
  UNIQUE(org_slug,principal,operation_key)
);
"""

CODING_TABLES = (
    'workflow_coding_mappings', 'workflow_coding_runs',
    'workflow_coding_attempts', 'workflow_coding_events',
)


# One finite inventory drives scalar, decoded and incomplete-envelope checks.
# release-input@1 is shared source provenance, NOT a coding discriminator.
CODING_PREFIXES = {
    'format': ('workflow-coding-', 'workflow-coding@', 'workflow-code-change@'),
    'compiler_pin': ('workflow-coding-',),
    'validator_pin': ('workflow-coding-',),
    'source_pin': ('workflow-coding-',),
    'action': ('coding_',),
}
CODING_VALUES = {
    'format': ('coding-scope@1', 'coding-replacement-selection@1'),
    'source_pin': ('coding-capability-issuer@1',),
    'kind': ('code-change',),
    'artifact_kind': ('code-change',),
    'source_kind': ('coding-manager-result',),
    'business_purpose': ('coding-replacement-selection@1',),
    'action': ('workflow_start',),
    'identity': ('release/happyranch/coding',),
    'release_identity': ('release/happyranch/coding',),
    'namespace': ('release/happyranch',),
}
CODING_NAMESPACE_COLUMNS = ('namespace', 'authority_namespace', 'org_namespace')


def has_coding_schema_objects(conn: sqlite3.Connection) -> bool:
    """Detect the reserved family regardless of object kind, spelling or rows.

    tbl_name also covers indexes/triggers with unrelated names on coding tables.
    This is a validation trigger, never a filtered schema compatibility reader.
    """
    return any(str(name).casefold().startswith('workflow_coding_')
               or str(name).casefold() == 'workflow_coding'
               for row in conn.execute('SELECT name,tbl_name FROM sqlite_schema')
               for name in row)


def _coding_scalar(key: str, value: object, *, incomplete: bool = False) -> bool:
    """Reserved values only in typed fields; ID/name/prose mentions grant nothing."""
    if isinstance(value, bytes):
        value = value.decode('utf8', errors='replace')
    if not isinstance(value, str):
        return False
    if key == 'action' and value.lower().startswith('coding_'):
        return True  # Preserve the former scalar audit LIKE discriminator.
    if key in CODING_NAMESPACE_COLUMNS:
        key = 'namespace'
        parts = value.split('/')
        if (len(parts) >= 3 and parts[0] == 'org' and parts[1]
                and (parts[2] == 'coding-setup' or (incomplete and parts[2]
                     and 'coding-setup'.startswith(parts[2])))):
            return True
    prefixes = CODING_PREFIXES.get(key, ())
    values = CODING_VALUES.get(key, ())
    return (value.startswith(prefixes) or value in values
            or (incomplete and any(item.startswith(value) for item in (*prefixes, *values))))


def _coding_value(value: object, budget: _Budget | None) -> bool:
    """Recognize reserved typed metadata, not mentions in document prose."""
    if budget is not None:
        budget.step()
    if isinstance(value, dict):
        for key, item in value.items():
            if budget is not None:
                budget.step()
                for text in (key, item):
                    if isinstance(text, str):
                        for _ in text:
                            budget.step()
            if _coding_scalar(key, item) or _coding_value(item, budget):
                return True
    if isinstance(value, list):
        return any(_coding_value(item, budget) for item in value)
    return False


def _metadata_string(text: str, start: int, budget: _Budget | None) -> tuple[str, int, bool]:
    """Consume ONE JSON string without mistaking its prose for object members.

    Decode escaped keys/values even if the containing object never closes.
    On an invalid/truncated escape retain its decoded prefix and consume the
    rest of that string; absence of complete JSON never erases a discriminator.
    This lexer is not a retained-family validity reader.
    """
    if budget is not None:
        budget.step()  # opening quote
    parts: list[str] = []
    index = start + 1
    valid = True
    escapes = {'"': '"', '\\': '\\', '/': '/', 'b': '\b', 'f': '\f',
               'n': '\n', 'r': '\r', 't': '\t'}
    while index < len(text):
        if budget is not None:
            budget.step()
        char = text[index]
        index += 1
        if char == '"':
            return ''.join(parts), index, valid
        if char == '\\':
            if index == len(text):
                break
            if budget is not None:
                budget.step()
            escape = text[index]
            index += 1
            if escape == 'u':
                digits = text[index:index + 4]
                if budget is not None:
                    for _ in digits:
                        budget.step()
                if len(digits) == 4 and all(digit in '0123456789abcdefABCDEF' for digit in digits):
                    if valid:
                        parts.append(chr(int(digits, 16)))
                    index += 4
                else:
                    valid = False
            elif escape in escapes:
                if valid:
                    parts.append(escapes[escape])
            else:
                valid = False
        elif ord(char) < 32:
            valid = False
        elif valid:
            parts.append(char)
    return ''.join(parts), index, False


def _malformed_coding_value(text: str, budget: _Budget | None) -> bool:
    """Visit every lexical member, including duplicate and unclosed objects.

    Only structured envelopes participate. Quoted document prose is consumed
    as one string, never searched for embedded JSON snippets or coding words.
    The same finite scalar inventory handles every successful-decoder family.
    """
    index = 0
    while index < len(text) and text[index] in ' \t\r\n\ufeff':
        if budget is not None:
            budget.step()
        index += 1
    if budget is not None:
        budget.step()
    if index == len(text) or text[index] not in '{[':
        return False
    while index < len(text):
        if budget is not None:
            budget.step()
        if text[index] != '"':
            index += 1
            continue
        key, index, key_complete = _metadata_string(text, index, budget)
        following = index
        while following < len(text) and text[following] in ' \t\r\n':
            if budget is not None:
                budget.step()
            following += 1
        if not key_complete or following == len(text) or text[following] != ':':
            continue
        following += 1
        while following < len(text) and text[following] in ' \t\r\n':
            if budget is not None:
                budget.step()
            following += 1
        if following < len(text) and text[following] == '"':
            item, index, complete = _metadata_string(text, following, budget)
            if _coding_scalar(key, item, incomplete=not complete):
                return True
    return False


def _refuse_coding_metadata(raw: object, *, location: str, budget: _Budget | None) -> None:
    # None preserves the ordinary nonmapping discriminator/watermark contract.
    # Mapping scans pass their enclosing budget unchanged, including fallback.
    if not isinstance(raw, (bytes, str)):
        return  # Existing retained-family readers check their required types.
    if len(raw) > 16777216:
        raise ValueError(f'workflow_coding_boundary_scan_bound: {location}')
    try:
        # Check EVERY duplicate-key value; later document values cannot erase
        # a reserved value even when the outer object subsequently truncates.
        def pairs(items: list[tuple[str, object]]) -> dict:
            if budget is not None:
                budget.step()  # also account for empty objects
            result = {}
            for key, value in items:
                if budget is not None:
                    budget.step()  # duplicate members and dict construction
                    for text in (key, value):
                        if isinstance(text, str):
                            for _ in text:
                                budget.step()
                if _coding_scalar(key, value) or _coding_value(value, budget):
                    raise ValueError(f'workflow_coding_data_unsupported: {location}; complete family reader required')
                result[key] = value
            return result
        value = json.loads(raw, object_pairs_hook=pairs)
        if _coding_value(value, budget):
            raise ValueError(f'workflow_coding_data_unsupported: {location}; complete family reader required')
    except (UnicodeError, json.JSONDecodeError):
        # Mirror JSON's supported UTF8/16/32 input encodings even when the
        # envelope truncates. A malformed byte envelope cannot lose its family
        # merely because the successful decoder would recognize its encoding.
        text = raw.decode(json.detect_encoding(raw), errors='replace') if isinstance(raw, bytes) else raw
        if _malformed_coding_value(text, budget):
            raise ValueError(f'workflow_coding_data_unsupported: {location}; malformed coding metadata') from None
        return  # Family discrimination only, never a legacy validity PASS.
    except RecursionError as exc:
        raise ValueError(f'workflow_coding_boundary_scan_bound: {location}') from exc


def validate_coding_data(conn: sqlite3.Connection, *, layout: str,
                         expected_org_slug: str | None = None) -> None:
    # Own a coherent snapshot only when the caller has none. Central callers
    # have already recognized the complete schema; no recursive schema read.
    owned = not conn.in_transaction
    if owned:
        conn.execute('BEGIN')
    try:
        _validate_coding_data(conn, layout=layout, expected_org_slug=expected_org_slug)
    finally:
        if owned:
            conn.rollback()


def _validate_coding_data(conn: sqlite3.Connection, *, layout: str,
                          expected_org_slug: str | None) -> None:
    """Positive setup/import/mapping/queued closure; refuse unsupported coding families.

    No ID skip-list or trusted flag: each supported native row is compared in
    full, with native storage types, against the complete independently parsed
    history/admission/journal/pointer closure. All other cells retain the shared
    unknown/malformed/duplicate/incomplete discriminator. Recovery must match
    the full schema first, before either legacy return. No task/schema effects.
    """
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
    storage = None
    imported = None
    mappings = None
    mapping_rows = {}
    queued = None
    budget = None
    if layout == 'J' and expected_org_slug is not None:
        from runtime.workflows.coding_authority_history import (
            _Budget, _SetupHistoryReader, _bounded_rows, _read_setup_storage,
        )
        budget = _Budget()
        setups = _SetupHistoryReader(conn, expected_org_slug, budget)
        storage = _read_setup_storage(conn, expected_org_slug=expected_org_slug, reader=setups)
        from runtime.workflows._coding_release_history import read_release_import
        imported = read_release_import(conn, expected_org_slug=expected_org_slug)
        from runtime.workflows.coding_mapping_history import _read_mapping_storage
        from runtime.workflows._coding_mapping_admission import _MappingCaptures
        from runtime.workflows._coding_source_admission import source_storage
        captures = _MappingCaptures(conn, expected_org_slug, budget)
        mappings, mapping_rows = _read_mapping_storage(conn, expected_org_slug=expected_org_slug,
                                                       budget=budget, setups=setups, captures=captures)
        _, source_rows = source_storage(conn, expected_org_slug, budget, setups, captures, mappings)
        for table, entries in source_rows.items():
            index = mapping_rows.setdefault(table, {})
            for key, value in entries.items():
                budget.step()
                if key in index and index[key] != value:
                    raise ValueError('workflow_coding_source_row_conflict')
                index[key] = value
        if mappings is not None and imported is None:
            raise ValueError('workflow_coding_mapping_release_missing')
        from runtime.workflows.coding_start_history import read_queued
        queued = read_queued(conn, expected_org_slug, budget, mappings, imported)
    bounded_coding = mappings is not None or bool(queued and (queued.runs or queued.callbacks))
    for table in CODING_TABLES:
        if table not in tables:
            if layout == 'J':
                raise ValueError(f'workflow_coding_data_unsupported: missing {table}')
            continue
        if queued is not None and table != 'workflow_coding_mappings':
            continue  # All queued J rows reconstructed independently above.
        if table == 'workflow_coding_mappings' and mappings is not None:
            continue  # Every row is compared below against its complete closed prefix.
        if conn.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone() is not None:
            raise ValueError(f'workflow_coding_data_unsupported: {table}; complete family reader required')
    for table in sorted(tables):
        if not (table.startswith('workflow_') or table in ('tasks', 'task_results', 'audit_log')):
            continue
        quoted = str(table).replace('"', '""')
        columns = [row[1] for row in conn.execute(f'PRAGMA table_xinfo("{quoted}")')]
        scalar = [column for column in columns if column in CODING_PREFIXES
                  or column in CODING_VALUES or column in CODING_NAMESPACE_COLUMNS]
        structured = [column for column in columns
                      if (column.endswith(('_bytes', '_json')) or column == 'payload')
                      and column not in ('submission_bytes', 'candidate_bytes')]
        selected_columns = scalar + structured
        native_setup = table in ('workflow_authorization_revisions', 'workflow_active_authorizations')
        native_import = table in ('workflow_template_identities', 'workflow_template_drafts',
            'workflow_template_versions', 'workflow_template_identity_versions',
            'workflow_template_publish_operations', 'workflow_admission_records')
        native_mapping = mappings is not None and table in (
            'workflow_coding_mappings', 'workflow_admission_records', 'audit_log', 'workflow_authorization_revisions')
        native_queued = queued is not None and table in (
            *queued.admitted.keys(), *CODING_TABLES)
        if native_setup or native_import or native_mapping or native_queued:
            selected_columns = columns
        if not selected_columns:
            continue
        selected = ','.join('"' + column.replace('"', '""') + '"' for column in selected_columns)
        file_key = _audit_log_file_key(conn) if table == 'audit_log' and not bounded_coding else None
        watermark = _audit_log_watermark(conn, file_key)
        last_id = watermark
        if bounded_coding:
            # Every selected cell, including ordinary audit scalars/payloads,
            # receives fresh type/per-value/remaining-byte gates before fetch.
            # The mapping path never trusts an audit watermark. Ordinary
            # nonmapping scanning below retains its historical contract.
            types = {
                'workflow_coding_mappings': (('text',), ('text',), ('text',), ('integer',),
                    ('text',), ('text',), ('text',), ('blob',), ('text',), ('text',)),
                'workflow_authorization_revisions': (('text',), ('text',), ('integer',), ('blob',),
                    ('text',), ('text',), ('text',)),
                'workflow_admission_records': (('text',), ('text',), ('integer',), ('text',), ('text',)),
                'audit_log': (('integer',), ('text',), ('text',), ('text',), ('null', 'text'), ('text',)),
            }.get(table)
            # Other legacy families keep their existing native type semantics;
            # their selected values still receive the same raw fetch bounds.
            allowed = types or (('text', 'blob', 'integer', 'real', 'null'),) * len(selected_columns)
            bounded = _bounded_rows(conn, table, tuple(selected_columns), allowed, budget, with_rowid=True,
                                    value_limit=16777216 if table == 'workflow_coding_events' else 1048576)
            rows = (row[:len(selected_columns) + 1] for row in bounded)
        else:
            query = f'SELECT rowid, {selected} FROM "{quoted}"' + (' WHERE rowid > ? ORDER BY rowid' if file_key else '')
            rows = conn.execute(query, (watermark,) if file_key else ())
        for rowid, *row in rows:
            data = dict(zip(selected_columns, row, strict=True))
            if native_queued and queued.matches(table, data):
                budget.step()
                continue  # Complete typed row equality after independent semantic closure.
            if table in CODING_TABLES and table != 'workflow_coding_mappings':
                raise ValueError('workflow_coding_queued_row_mismatch')
            if native_mapping:
                budget.step()
                expected = mapping_rows.get(table, {}).get((type(data.get('id')), data.get('id')))
                if expected is not None:
                    budget.step()  # index lookup is never positive admission
                    equal = data.keys() == expected.keys()
                    for key, value in expected.items():
                        budget.step()
                        equal = equal and type(data.get(key)) is type(value) and data[key] == value
                    if equal:
                        continue  # Complete typed row equality, not an ID exemption.
                if table == 'workflow_coding_mappings':
                    raise ValueError('workflow_coding_mapping_row_mismatch')
            if imported is not None and native_import:
                if bounded_coding:
                    # Fixed six-row import comparison is also charged here.
                    for _, expected in imported.rows:
                        for _ in expected:
                            budget.step()
                if imported.matches(table, data):
                    continue  # Complete independently derived typed row, never an ID exemption.
            if native_setup and storage is not None:
                # A supported family dispatch is an exact whole-row equality,
                # never an exemption based solely on namespace, ID or digest.
                if table == 'workflow_authorization_revisions':
                    for entry in storage.issuances:
                        if bounded_coding:
                            budget.step()
                        expected = dict(id=entry.authorization_id, namespace=entry.namespace,
                            revision=entry.revision, authority_bytes=entry.authority_bytes,
                            authority_digest=entry.authority_digest,
                            source_pin='coding-capability-issuer@1', created_at=entry.created_at)
                        if (data.keys() == expected.keys() and all(type(data[key]) is type(value)
                                and data[key] == value for key, value in expected.items())):
                            break
                    else:
                        entry = None
                    if entry is not None:
                        continue
                elif (type(data.get('namespace')) is str
                        and type(data.get('authorization_revision_id')) is str
                        and set(data) == {'namespace', 'authorization_revision_id'}
                        and (data['namespace'], data['authorization_revision_id']) in storage.pointers):
                    continue
            for column, raw in zip(selected_columns, row, strict=True):
                if bounded_coding:
                    budget.step()  # scalar classification or one metadata decode
                if column in scalar:
                    if _coding_scalar(column, raw):
                        raise ValueError(f'workflow_coding_data_unsupported: {table}.{column}; coding reader required')
                else:
                    _refuse_coding_metadata(raw, location=f'{table}.{column}',
                                            budget=budget if bounded_coding else None)
            last_id = rowid
        if file_key is not None:
            _advance_audit_log_watermark(conn, file_key, last_id)


# audit_log is insert-only at runtime (AUTOINCREMENT id; only the one-time
# pre-serving legacy script->job schema migration rewrites rows). Rescanning
# every historical payload on each classify_task held Database._lock for
# seconds per task step, so rows already verified clean in this process are
# skipped. Keyed by the database file identity (never id(conn), which can be
# reused); in-memory/unidentifiable databases always rescan fully. A refusal
# never advances the watermark. Direct same-UID rewrites of already-verified
# rows remain outside this cooperative guarantee, as elsewhere.
_AUDIT_LOG_WATERMARKS: dict[tuple[int, int], int] = {}
_AUDIT_LOG_WATERMARK_LOCK = threading.Lock()


def _audit_log_file_key(conn: sqlite3.Connection) -> tuple[int, int] | None:
    try:
        path = next((row[2] for row in conn.execute('PRAGMA database_list') if row[1] == 'main'), '')
        if not path:
            return None
        stat = os.stat(path)
    except (sqlite3.DatabaseError, OSError):
        return None
    return (stat.st_dev, stat.st_ino)


def _audit_log_watermark(conn: sqlite3.Connection, file_key: tuple[int, int] | None) -> int:
    if file_key is None:
        return 0
    with _AUDIT_LOG_WATERMARK_LOCK:
        watermark = _AUDIT_LOG_WATERMARKS.get(file_key, 0)
    current_max = conn.execute('SELECT COALESCE(MAX(rowid), 0) FROM audit_log').fetchone()[0]
    if type(current_max) is int and current_max >= watermark:
        return watermark
    # Rows vanished or ids regressed: the cached proof no longer applies, and
    # a stale higher mark must not later skip reused ids.
    with _AUDIT_LOG_WATERMARK_LOCK:
        _AUDIT_LOG_WATERMARKS.pop(file_key, None)
    return 0


def _advance_audit_log_watermark(conn: sqlite3.Connection, file_key: tuple[int, int], last_id: int) -> None:
    # Inside an open transaction our own uncommitted rows are visible; a
    # rollback would reuse their AUTOINCREMENT ids, so never cache past them.
    if conn.in_transaction:
        return
    with _AUDIT_LOG_WATERMARK_LOCK:
        if last_id > _AUDIT_LOG_WATERMARKS.get(file_key, 0):
            _AUDIT_LOG_WATERMARKS[file_key] = last_id
