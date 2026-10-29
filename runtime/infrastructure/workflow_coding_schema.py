"""Unchanged coding DDL beside supported document layouts and closed histories.

Complete setup/import/mapping/settings/start/native/return/cancellation readers
share their original allowance, including metadata paths. Structural recognition
is separate from history reconstruction, with no recursive acceptance or live
whole-org SQL equality. This module grants no repository or current authority.
"""
from __future__ import annotations

from runtime.infrastructure._workflow_reader import (
    _decode as _reader_decode,
)

from runtime.infrastructure._workflow_reader import (
    _add as _reader_add,
    _append as _reader_append,
    _put as _reader_put,
    _setdefault as _reader_setdefault,
)

from runtime.infrastructure._workflow_reader import (
    _all_true as _reader_all_true,
    _any_true as _reader_any_true,
)

from runtime.infrastructure._workflow_reader import (
    _dict as _reader_dict,
    _dict_pairs as _reader_dict_pairs,
    _list as _reader_list,
    _set as _reader_set,
    _sorted as _reader_sorted,
    _tuple as _reader_tuple,
)

from runtime.infrastructure._workflow_reader import (
    _all as _reader_all, _one as _reader_one, _rows as _reader_rows,
    _json as _reader_json, _json_work as _reader_json_work,
    _reserve as _reader_reserve, _work as _reader_work,
)

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from runtime.workflows._coding_release_history import ReleaseImportHistory
    from runtime.workflows._coding_registered_release import RegisteredCodingRelease
    from runtime.workflows.coding_authority_history import _Budget
    from runtime.workflows.coding_mapping_history import CodingMappingHistory
    from runtime.workflows.coding_start_history import QueuedStorage

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
    'owner': ('coding',),
    'registrar': ('builtin:coding-v1',),
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


def has_coding_schema_objects(conn: sqlite3.Connection, *, budget=None) -> bool:
    """Detect the reserved family regardless of object kind, spelling or rows.

    tbl_name also covers indexes/triggers with unrelated names on coding tables.
    This is a validation trigger, never a filtered schema compatibility reader.
    """
    _reader_reserve(budget, 0)
    return _reader_any_true((str(name).casefold().startswith('workflow_coding_')
               or str(name).casefold() == 'workflow_coding'
               for row in _reader_all(conn, 'SELECT name,tbl_name FROM sqlite_schema', budget=budget)
               for name in row), budget=budget)


def _validate_coding_dependencies(conn: sqlite3.Connection, *, budget=None) -> None:
    """Needed native storage contracts, independent of unrelated native DDL.

    Workflow parents and every transitive key/constraint are compared against
    literal supported F/E/G/H/I/J definitions by _read_structure. Native rows
    keep their existing typed/semantic ownership readers. Here even empty
    storage must provide their real columns and parent keys; task ALTER order
    and unrelated native tables are deliberately not a coding prerequisite.
    """
    _reader_reserve(budget, 0)
    from runtime.workflows.coding_source_history import (
        _TASK_COLUMNS, _TASK_TYPES, _RESULT_COLUMNS, _RESULT_TYPES,
        _AUDIT_COLUMNS, _AUDIT_TYPES,
    )
    contracts = (_reader_reserve(budget, 704) or {
        'tasks': (_reader_reserve(budget, 112) or (_TASK_COLUMNS, _TASK_TYPES, 'id')),
        'task_results': (_reader_reserve(budget, 112) or (_RESULT_COLUMNS, _RESULT_TYPES, 'id')),
        'audit_log': (_reader_reserve(budget, 112) or (_AUDIT_COLUMNS, _AUDIT_TYPES, 'id')),
        'org_settings': (('section', 'value_json', 'updated_at', 'updated_by'),
                         (('text',), ('text',), ('text',), ('null', 'text')), 'section'),
        'work_hours': (('agent_name', 'status'), (('text',), ('text',)), 'id'),
    })
    for table, (columns, types, primary) in contracts.items():
        objects = _reader_list((_reader_tuple(row, budget=budget) for row in _reader_rows(
            conn, 'SELECT type,name,sql FROM sqlite_schema WHERE lower(name)=?', (_reader_reserve(budget, 80) or (table,)), budget=budget)), budget=budget)
        if len(objects) != 1 or objects[0][:2] != (_reader_reserve(budget, 96) or ('table', table)):
            raise ValueError('workflow_coding_shared_schema_mismatch: ' + table)
        metadata = _reader_dict_pairs(((_reader_reserve(budget, 96) or (row[1],  _reader_tuple(row, budget=budget) )) for row in _reader_rows(conn, f'PRAGMA table_xinfo("{table}")', budget=budget)), budget=budget)
        keys = _reader_list((name for name, row in metadata.items() if row[5]), budget=budget)
        if keys != (_reader_reserve(budget, 192) or [primary]):
            raise ValueError('workflow_coding_shared_key_mismatch: ' + table)
        for name, allowed in zip(columns, types, strict=True):
            row = metadata.get(name)
            declared = next(kind.upper() for kind in allowed if kind != 'null')
            if (row is None or row[2].upper() != declared or row[6] != 0
                    or ('null' not in allowed and name != primary and row[3] != 1)):
                raise ValueError('workflow_coding_shared_column_mismatch: ' + table)
        if table in ('task_results', 'audit_log'):
            if metadata[primary][2].upper() != 'INTEGER' or 'AUTOINCREMENT' not in objects[0][2].upper():
                raise ValueError('workflow_coding_shared_integer_key_required: ' + table)


def _coding_scalar(key: str, value: object, *, incomplete: bool = False) -> bool:
    """Reserved values only in typed fields; ID/name/prose mentions grant nothing."""
    if isinstance(value, bytes):
        value = value.decode('utf8', errors='replace')
    if not isinstance(value, str):
        return False
    if ((key == 'format' and value.casefold().startswith('workflow-coding-builtin-registration'))
            or (key == 'registrar' and value.casefold().startswith('builtin:coding-v'))):
        return True
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
                for text in (_reader_reserve(budget, 96) or (key, item)):
                    if isinstance(text, str):
                        for _ in text:
                            budget.step()
            if _coding_scalar(key, item) or _coding_value(item, budget):
                return True
    if isinstance(value, list):
        return _reader_any_true((_coding_value(item, budget) for item in value), budget=budget)
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
    parts: list[str] = (_reader_reserve(budget, 64) or [])
    index = start + 1
    valid = True
    escapes = (_reader_reserve(budget, 1088) or {'"': '"', '\\': '\\', '/': '/', 'b': '\b', 'f': '\f',
               'n': '\n', 'r': '\r', 't': '\t'})
    while index < len(text):
        if budget is not None:
            budget.step()
        char = text[index]
        index += 1
        if char == '"':
            return (_reader_reserve(budget, 112) or (''.join(parts), index, valid))
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
                if len(digits) == 4 and _reader_all_true((digit in '0123456789abcdefABCDEF' for digit in digits), budget=budget):
                    if valid:
                        _reader_append(parts, chr(int(digits, 16)), budget=budget)
                    index += 4
                else:
                    valid = False
            elif escape in escapes:
                if valid:
                    _reader_append(parts, escapes[escape], budget=budget)
            else:
                valid = False
        elif ord(char) < 32:
            valid = False
        elif valid:
            _reader_append(parts, char, budget=budget)
    return (_reader_reserve(budget, 112) or (''.join(parts), index, False))


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
    # None is ordinary discrimination, independent of mapping/queued enrollment.
    # Selected coding rows pass their enclosing budget unchanged, including fallback.
    if not isinstance(raw, (_reader_reserve(budget, 96) or (bytes, str))):
        return  # Existing retained-family readers check their required types.
    if len(raw) > 16777216:
        raise ValueError(f'workflow_coding_boundary_scan_bound: {location}')
    try:
        # Check EVERY duplicate-key value; later document values cannot erase
        # a reserved value even when the outer object subsequently truncates.
        _reader_reserve(budget, 96)
        def pairs(items: list[tuple[str, object]]) -> dict:
            if budget is not None:
                budget.step()  # also account for empty objects
            result = (_reader_reserve(budget, 64) or {})
            for key, value in items:
                if budget is not None:
                    budget.step()  # duplicate members and dict construction
                    for text in (_reader_reserve(budget, 96) or (key, value)):
                        if isinstance(text, str):
                            for _ in text:
                                budget.step()
                if _coding_scalar(key, value) or _coding_value(value, budget):
                    raise ValueError(f'workflow_coding_data_unsupported: {location}; complete family reader required')
                _reader_put(value, result, key, budget=budget)
            return result
        value = _reader_json(raw, object_pairs_hook=pairs, budget=budget)
        if _coding_value(value, budget):
            raise ValueError(f'workflow_coding_data_unsupported: {location}; complete family reader required')
    except ((UnicodeError, json.JSONDecodeError)):
        # Mirror JSON's supported UTF8/16/32 input encodings even when the
        # envelope truncates. A malformed byte envelope cannot lose its family
        # merely because the successful decoder would recognize its encoding.
        text = _reader_decode(raw, json.detect_encoding(raw), errors='replace', budget=budget) if isinstance(raw, bytes) else raw
        if _malformed_coding_value(text, budget):
            raise ValueError(f'workflow_coding_data_unsupported: {location}; malformed coding metadata') from None
        return  # Family discrimination only, never a legacy validity PASS.
    except RecursionError as exc:
        raise ValueError(f'workflow_coding_boundary_scan_bound: {location}') from exc


@dataclass(frozen=True)
class _CodingInventory:
    """Local result of complete validation, never an accepted caller input.

    Consume only in the enclosing connection/org/read interval, before writes.
    Public readers discard this result; no cross-snapshot cache is installed.
    """

    budget: _Budget
    release: RegisteredCodingRelease | None
    imported: ReleaseImportHistory | None
    mappings: CodingMappingHistory | None
    queued: QueuedStorage


def validate_coding_data(conn: sqlite3.Connection, *, layout: str,
                         expected_org_slug: str | None = None) -> None:
    _read_coding_data(conn, layout=layout, expected_org_slug=expected_org_slug)


def _read_coding_data(conn: sqlite3.Connection, *, layout: str,
                      expected_org_slug: str | None,
                      budget: _Budget | None = None,
                      advances: list[tuple[tuple[int, int], int]] | None = None) -> _CodingInventory | None:
    # Own a coherent snapshot only when the caller has none. Central callers
    # have already recognized the complete schema; no recursive schema read.
    if budget is not None:
        from runtime.workflows.coding_authority_history import _Budget
        if type(budget) is not _Budget:
            raise ValueError('workflow_coding_data_invalid_budget')
    owned = not conn.in_transaction
    # A snapshot we open ourselves holds no uncommitted rows of ours, so the
    # audit rows it verified are committed; advance only after it ends.
    collected = (_reader_reserve(budget, 64) or []) if owned else advances
    if owned:
        conn.execute('BEGIN')
    primary = None
    try:
        # This independently entered boundary owns its structural validation.
        # The installed reader calls the data validator below its own exact
        # comparison instead; neither path trusts an inventory or a flag.
        if has_coding_schema_objects(conn, budget=budget):
            from runtime.infrastructure.workflow_schema import _read_structure
            layout, _ = _read_structure(conn, budget=budget)
        inventory = _validate_coding_data(conn, layout=layout, expected_org_slug=expected_org_slug,
                                          advances=collected, budget=budget)
    except BaseException as exc:
        primary = exc
        raise
    finally:
        if owned:
            try:
                conn.rollback()
            except BaseException as exc:
                if primary is None:
                    raise
                primary.add_note('workflow coding snapshot rollback failed: ' + type(exc).__name__)
    if owned:
        for file_key, last_id in collected or ():
            _advance_audit_log_watermark(conn, file_key, last_id)
    return inventory


def _coding_metadata_candidate(raw: object, *, location: str, budget=None) -> bool:
    """Ordinary discrimination only; a candidate still needs bounded acquisition."""
    _reader_work((_reader_reserve(budget, 96) or (raw,location)), budget, copies=2)
    try:
        _refuse_coding_metadata(raw, location=location, budget=budget)
    except ValueError as exc:
        if str(exc).startswith('workflow_coding_data_unsupported:'):
            return True
        raise
    return False


def _coding_candidate_rows(conn, table, columns, allowed, budget, *, scalar,
                           structured, keys, known, watermark):
    """Locate independently expected rows and reserved metadata, never admit by ID.

    The complete boundary acquires ordinary shared history under the supplied
    original allowance, with type/size gates before payload copies or parsing.
    Known coding rows bypass ONLY that ordinary scan/watermark; each candidate
    is freshly acquired with the original bounded cell gates and SAME remainder.
    """
    _reader_work((_reader_reserve(budget, 176) or (table,columns,allowed,scalar,structured,keys,watermark)), budget, copies=2)
    from runtime.workflows.coding_authority_history import _bounded_rows
    quoted = '"' + table.replace('"', '""') + '"'
    key_names = _reader_list(('"' + key.replace('"', '""') + '"' for key in keys), budget=budget)
    key_sql = '(' + ','.join(key_names) + ')' if len(keys) > 1 else key_names[0]
    placeholders = '(' + ','.join('?' for _ in keys) + ')' if len(keys) > 1 else '?'
    for identity in known:
        selected = _reader_list(_bounded_rows(conn, table, _reader_tuple(columns, budget=budget), allowed, budget,
            where=key_sql + '=' + placeholders, args=_reader_tuple((value for _, value in identity), budget=budget),
            maximum=1, with_rowid=True), budget=budget)
        if len(selected) != 1:
            raise ValueError('workflow_coding_expected_row_missing')
        row = selected[0]
        yield (_reader_reserve(budget, 128 + 32 * (2 + len(row))) or (row[0], True, *row[1:len(columns) + 1]))
    metadata = _reader_list(dict.fromkeys((_reader_reserve(budget, 128 + 32 * (0 + len(scalar) + len(structured))) or (*scalar, *structured))), budget=budget)
    projection = ','.join('"' + name.replace('"', '""') + '"' for name in metadata)
    predicates, args = (_reader_reserve(budget, 96) or (_reader_reserve(budget, 64) or [], ()))
    if known:
        _reader_append(predicates, '(' + ' OR '.join(name + ' IS NULL' for name in key_names)
                          + ' OR ' + key_sql + ' NOT IN ('
                          + ','.join(placeholders for _ in known) + '))', budget=budget)
        args += _reader_tuple((value for identity in known for _, value in identity), budget=budget)
    if watermark is not None:
        _reader_append(predicates, 'rowid>?', budget=budget)
        args += (_reader_reserve(budget, 80) or (watermark,))
    where = ' WHERE ' + ' AND '.join(predicates) if predicates else ''
    for rowid, *values in _reader_rows(conn, 'SELECT rowid' + (',' + projection if projection else '')
                                       + ' FROM ' + quoted + where + ' ORDER BY rowid', args, budget=budget):
        candidate = _reader_any_true((_coding_scalar(name, raw) if name in scalar else
                        _coding_metadata_candidate(raw, location=f'{table}.{name}', budget=budget)
                        for name, raw in zip(metadata, values, strict=True)), budget=budget)
        if not candidate:
            yield (_reader_reserve(budget, 112) or (rowid, False, None))  # ordinary prefix observation, not a coding row
            continue
        yield from ((_reader_reserve(budget, 128 + 32 * (2 + len(row))) or (row[0], False, *row[1:len(columns) + 1])) for row in _bounded_rows(
            conn, table, _reader_tuple(columns, budget=budget), allowed, budget, where='rowid=?',
            args=(_reader_reserve(budget, 80) or (rowid,)), maximum=1, with_rowid=True))


def _validate_coding_data(conn: sqlite3.Connection, *, layout: str,
                          expected_org_slug: str | None,
                          advances: list[tuple[tuple[int, int], int]] | None = None,
                          budget: _Budget | None = None) -> _CodingInventory | None:
    """Complete setup/import/mapping/queued data below its structural owner.

    No ID skip-list or trusted flag: each supported native row is compared in
    full, with native storage types, against the complete independently parsed
    history/admission/journal/pointer closure. All other cells retain the shared
    unknown/malformed/duplicate/incomplete discriminator. The installed reader
    or independent _read_coding_data entry matches the full schema first, in
    this same snapshot. This internal data phase supplies no structural proof
    or publication authority and never accepts a validation-skipping flag.
    """
    _reader_reserve(budget, 0)
    tables = _reader_set((row[0] for row in _reader_rows(conn, "SELECT name FROM sqlite_schema WHERE type='table'", budget=budget)), budget=budget)
    storage = None
    release = None
    imported = None
    mappings = None
    mapping_rows = (_reader_reserve(budget, 64) or {})
    queued = None
    coding_present = has_coding_schema_objects(conn, budget=budget)
    if coding_present:
        _validate_coding_dependencies(conn, budget=budget)
    if coding_present and expected_org_slug is not None:
        from runtime.workflows.coding_authority_history import (
            _Budget, _SetupHistoryReader, _bounded_rows, _read_setup_storage,
        )
        if budget is not None and type(budget) is not _Budget:
            raise ValueError('workflow_coding_data_invalid_budget')
        budget = _Budget() if budget is None else budget
        setups = _SetupHistoryReader(conn, expected_org_slug, budget)
        storage = _read_setup_storage(conn, expected_org_slug=expected_org_slug, reader=setups)
        from runtime.workflows._coding_registered_release import read_registered_release
        release = read_registered_release(conn, expected_org_slug=expected_org_slug, budget=budget)
        imported = None if release is None else release.human_import
        from runtime.workflows.coding_mapping_history import _read_mapping_storage
        from runtime.workflows._coding_mapping_admission import _MappingCaptures
        from runtime.workflows._coding_source_admission import source_storage
        captures = _MappingCaptures(conn, expected_org_slug, budget)
        mappings, mapping_rows = _read_mapping_storage(conn, expected_org_slug=expected_org_slug,
                                                       budget=budget, setups=setups, captures=captures)
        _, source_rows = source_storage(conn, expected_org_slug, budget, setups, captures, mappings)
        for table, entries in source_rows.items():
            index = _reader_setdefault(mapping_rows, table, _reader_reserve(budget, 64) or {}, budget=budget)
            for key, value in entries.items():
                budget.step()
                if key in index and index[key] != value:
                    raise ValueError('workflow_coding_source_row_conflict')
                _reader_put(value, index, key, budget=budget)
        if mappings is not None and release is None:
            raise ValueError('workflow_coding_mapping_release_missing')
        from runtime.workflows.coding_start_history import _read_queued_data
        queued = _read_queued_data(conn, expected_org_slug, budget, mappings, release,
                                   layout=layout)
    else:
        # A canonical settings pointer is a coding-family fact even without J
        # mappings/audits. Earlier layouts cannot silently treat it as absent.
        if 'org_settings' in tables and _reader_one(conn, "SELECT 1 FROM org_settings WHERE section='coding' LIMIT 1", budget=budget) is not None:
            raise ValueError('workflow_coding_settings_requires_complete_reader')
        # A supplied original allowance remains cumulative on earlier layouts.
        # Standalone old readers with no allowance keep their literal behavior.
        if budget is not None:
            from runtime.workflows.coding_authority_history import _bounded_rows
    if release is not None:
        from runtime.workflows.coding_authority_history import _bounded_rows
        for table in ('workflow_activations', 'workflow_binding_snapshots'):
            columns = _reader_tuple((row[1] for row in _reader_rows(conn, f'PRAGMA table_xinfo("{table}")', budget=budget)), budget=budget)
            where, args = (_reader_reserve(budget, 96) or ('template_version_id=?', _reader_reserve(budget, 80) or (release.version_id,)))
            if table == 'workflow_activations':
                where += ' OR template_identity_id=?'
                args += (_reader_reserve(budget, 80) or (release.identity_id,))
            for row in _bounded_rows(conn, table, columns,
                    (('text', 'blob', 'integer', 'real', 'null'),) * len(columns), budget,
                    where=where, args=args):
                data = _reader_dict(zip(columns, row[:len(columns)], strict=True), budget=budget)
                # Reserve encode_row/base64/canonical intermediates before
                # comparing the complete downstream row with queued history.
                from runtime.workflows._coding_release_history import _copies, _steps
                _steps(budget, 4 * len(columns))
                _copies(budget, 12 * sum(len(value) for value in data.values()
                                        if type(value) in (_reader_reserve(budget, 96) or (str, bytes))))
                if queued is None or not queued.matches(table, data, budget=budget):
                    raise ValueError('workflow_coding_downstream_row_mismatch')
    # Empty J is still a complete coding read: absence must not replenish or
    # bypass the allowance for the subsequent native typed-row discrimination.
    bounded_coding = budget is not None
    for table in CODING_TABLES:
        if table not in tables:
            if coding_present:
                raise ValueError(f'workflow_coding_data_unsupported: missing {table}')
            continue
        if queued is not None and table != 'workflow_coding_mappings':
            continue  # All queued J rows reconstructed independently above.
        if table == 'workflow_coding_mappings' and mappings is not None:
            continue  # Every row is compared below against its complete closed prefix.
        if _reader_one(conn, f'SELECT 1 FROM {table} LIMIT 1', budget=budget) is not None:
            raise ValueError(f'workflow_coding_data_unsupported: {table}; complete family reader required')
    for table in _reader_sorted(tables, budget=budget):
        if not (table.startswith('workflow_') or table in ('tasks', 'task_results', 'audit_log')):
            continue
        quoted = str(table).replace('"', '""')
        columns = _reader_list((row[1] for row in _reader_rows(conn, f'PRAGMA table_xinfo("{quoted}")', budget=budget)), budget=budget)
        scalar = _reader_list((column for column in columns if column in CODING_PREFIXES
                  or column in CODING_VALUES or column in CODING_NAMESPACE_COLUMNS), budget=budget)
        structured = _reader_list((column for column in columns
                      if (column.endswith(('_bytes', '_json')) or column in ('payload', 'author_principal', 'published_by'))
                      and column not in ('submission_bytes', 'candidate_bytes')), budget=budget)
        selected_columns = scalar + structured
        native_setup = table in ('workflow_authorization_revisions', 'workflow_active_authorizations')
        native_import = table in ('workflow_template_identities', 'workflow_template_drafts',
            'workflow_template_versions', 'workflow_template_identity_versions',
            'workflow_template_publish_operations', 'workflow_admission_records')
        native_mapping = mappings is not None and table in (
            'workflow_coding_mappings', 'workflow_admission_records', 'audit_log', 'workflow_authorization_revisions')
        native_queued = queued is not None and table in (_reader_reserve(budget, 128 + 32 * (0 + len(queued.admitted.keys()) + len(CODING_TABLES))) or (
            *queued.admitted.keys(), *CODING_TABLES))
        # Enrollment chooses the full comparison projection, never a table-wide
        # coding charge. Ordinary rows remain ordinary after mapping/start.
        table_bounded = bounded_coding
        if native_setup or native_import or native_mapping or native_queued:
            selected_columns = columns
        if not selected_columns:
            continue
        selected = ','.join('"' + column.replace('"', '""') + '"' for column in selected_columns)
        file_key = _audit_log_file_key(conn, budget=budget) if table == 'audit_log' else None
        watermark = _audit_log_watermark(conn, file_key, budget=budget)
        last_id = watermark
        if table_bounded:
            types = (_reader_reserve(budget, 576) or {
                'workflow_coding_mappings': (('text',), ('text',), ('text',), ('integer',),
                    ('text',), ('text',), ('text',), ('blob',), ('text',), ('text',)),
                'workflow_authorization_revisions': (('text',), ('text',), ('integer',), ('blob',),
                    ('text',), ('text',), ('text',)),
                'workflow_admission_records': (('text',), ('text',), ('integer',), ('text',), ('text',)),
                'audit_log': (('integer',), ('text',), ('text',), ('text',), ('null', 'text'), ('text',)),
            }).get(table)
            allowed = types or (('text', 'blob', 'integer', 'real', 'null'),) * len(selected_columns)
            if table in CODING_TABLES:
                bounded = _bounded_rows(conn, table, _reader_tuple(selected_columns, budget=budget), allowed, budget, with_rowid=True,
                                        value_limit=16777216 if table == 'workflow_coding_events' else 1048576)
                rows = ((_reader_reserve(budget, 128 + 32 * (2 + len(row))) or (row[0], False, *row[1:len(selected_columns) + 1])) for row in bounded)
            else:
                keys = _reader_tuple((row[1] for row in _reader_sorted(_reader_rows(conn, f'PRAGMA table_xinfo("{quoted}")', budget=budget),
                                                       key=lambda row: row[5], budget=budget) if row[5]), budget=budget) or ('rowid',)
                def locator(expected):
                    return _reader_tuple(((_reader_reserve(budget, 96) or (type(expected[key]), expected[key])) for key in keys), budget=budget)
                known = _reader_set(budget=budget)
                for expected in mapping_rows.get(table, (_reader_reserve(budget, 64) or {})).values():
                    budget.step()
                    _reader_add(known, locator(expected), budget=budget)
                if release is not None:
                    for name, expected in release.rows:
                        budget.step()
                        if name == table:
                            _reader_add(known, locator(expected), budget=budget)
                if native_setup and storage is not None:
                    entries = (storage.issuances if table == 'workflow_authorization_revisions' else storage.pointers)
                    for entry in entries:
                        budget.step()
                        identity = entry.authorization_id if table == 'workflow_authorization_revisions' else entry[0]
                        _reader_add(known, (_reader_reserve(budget, 80) or ((_reader_reserve(budget, 96) or (str, identity)),)), budget=budget)
                if native_queued:
                    from runtime.workflows.coding_authority_history import _decoded
                    for raw in queued.admitted.get(table, ()):
                        # The independently reconstructed encoding locates a key
                        # only. Charge this copy/decode; full typed equality below
                        # remains mandatory even if the key is unchanged.
                        _reader_reserve(budget, len(raw))
                        expected = _decoded(_reader_json(raw, budget=budget), budget)
                        _reader_add(known, locator(expected), budget=budget)
                rows = _coding_candidate_rows(conn, table, selected_columns, allowed, budget,
                    scalar=scalar, structured=structured, keys=keys, known=known,
                    watermark=watermark if file_key else None)
        else:
            query = f'SELECT rowid, {selected} FROM "{quoted}"' + (' WHERE rowid > ? ORDER BY rowid' if file_key else '')
            rows = ((_reader_reserve(budget, 128 + 32 * (2 + len(row))) or (row[0], False, *row[1:])) for row in conn.execute(query, (_reader_reserve(budget, 80) or (watermark,)) if file_key else ()))
        for rowid, expected_candidate, *row in rows:
            last_id = max(last_id, rowid)
            if table_bounded and row == (_reader_reserve(budget, 192) or [None]):
                continue  # Already discriminated ordinary metadata, no coding spend.
            data = _reader_dict(zip(selected_columns, row, strict=True), budget=budget)
            if native_queued and queued.matches(table, data, budget=budget):
                budget.step()
                continue  # Complete typed row equality after independent semantic closure.
            if table in CODING_TABLES and table != 'workflow_coding_mappings':
                raise ValueError('workflow_coding_queued_row_mismatch')
            if native_mapping:
                budget.step()
                expected = mapping_rows.get(table, (_reader_reserve(budget, 64) or {})).get((_reader_reserve(budget, 96) or (type(data.get('id')), data.get('id'))))
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
            if release is not None and native_import:
                if table_bounded:
                    # Complete four/six-row release comparison is also charged here.
                    for _, expected in release.rows:
                        for _ in expected:
                            budget.step()
                if release.matches(table, data, budget=budget):
                    continue  # Complete independently derived typed row, never an ID exemption.
            if native_setup and storage is not None:
                # A supported family dispatch is an exact whole-row equality,
                # never an exemption based solely on namespace, ID or digest.
                if table == 'workflow_authorization_revisions':
                    for entry in storage.issuances:
                        if table_bounded:
                            budget.step()
                        expected = (_reader_reserve(budget, 960) or dict(id=entry.authorization_id, namespace=entry.namespace,
                            revision=entry.revision, authority_bytes=entry.authority_bytes,
                            authority_digest=entry.authority_digest,
                            source_pin='coding-capability-issuer@1', created_at=entry.created_at))
                        if (data.keys() == expected.keys() and _reader_all_true((type(data[key]) is type(value)
                                and data[key] == value for key, value in expected.items()), budget=budget)):
                            break
                    else:
                        entry = None
                    if entry is not None:
                        continue
                elif (type(data.get('namespace')) is str
                        and type(data.get('authorization_revision_id')) is str
                        and _reader_set(data, budget=budget) == _reader_set((_reader_reserve(budget, 96) or ('namespace', 'authorization_revision_id')), budget=budget)
                        and (_reader_reserve(budget, 96) or (data['namespace'], data['authorization_revision_id'])) in storage.pointers):
                    continue
            if expected_candidate:
                raise ValueError('workflow_coding_expected_row_mismatch')
            for column, raw in zip(selected_columns, row, strict=True):
                if table_bounded:
                    budget.step()  # scalar classification or one metadata decode
                if column in scalar:
                    if _coding_scalar(column, raw):
                        raise ValueError(f'workflow_coding_data_unsupported: {table}.{column}; coding reader required')
                else:
                    _refuse_coding_metadata(raw, location=f'{table}.{column}',
                                            budget=budget if table_bounded else None)
        if file_key is not None:
            if advances is not None:
                _reader_append(advances, (_reader_reserve(budget, 96) or (file_key, last_id)), budget=budget)  # applied after the owned snapshot ends
            # A joined reader without an outer collector may validate, but
            # cannot publish: its successful return is not an owning COMMIT.
    if queued is not None:
        return _CodingInventory(budget, release, imported, mappings, queued)
    return None


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


def _audit_log_file_key(conn: sqlite3.Connection, *, budget=None) -> tuple[int, int] | None:
    _reader_reserve(budget, 0)
    try:
        _reader_reserve(budget, 96)
        path = next((row[2] for row in _reader_all(conn, 'PRAGMA database_list', budget=budget) if row[1] == 'main'), '')
        if not path:
            return None
        stat = os.stat(path)
    except ((sqlite3.DatabaseError, OSError)):
        return None
    return (_reader_reserve(budget, 96) or (stat.st_dev, stat.st_ino))


def _audit_log_watermark(conn: sqlite3.Connection, file_key: tuple[int, int] | None, *, budget=None) -> int:
    _reader_reserve(budget, 0)
    if file_key is None:
        return 0
    with _AUDIT_LOG_WATERMARK_LOCK:
        watermark = _AUDIT_LOG_WATERMARKS.get(file_key, 0)
    current_max = _reader_one(conn, 'SELECT COALESCE(MAX(rowid), 0) FROM audit_log', budget=budget)[0]
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
    if _audit_log_file_key(conn) != file_key:
        with _AUDIT_LOG_WATERMARK_LOCK:
            _AUDIT_LOG_WATERMARKS.pop(file_key, None)
        return
    current_max = conn.execute('SELECT COALESCE(MAX(rowid), 0) FROM audit_log').fetchone()[0]
    if type(current_max) is not int or current_max < last_id:
        with _AUDIT_LOG_WATERMARK_LOCK:
            _AUDIT_LOG_WATERMARKS.pop(file_key, None)
        return
    with _AUDIT_LOG_WATERMARK_LOCK:
        if current_max < _AUDIT_LOG_WATERMARKS.get(file_key, 0):
            _AUDIT_LOG_WATERMARKS.pop(file_key, None)
            return
        if last_id > _AUDIT_LOG_WATERMARKS.get(file_key, 0):
            _AUDIT_LOG_WATERMARKS[file_key] = last_id
