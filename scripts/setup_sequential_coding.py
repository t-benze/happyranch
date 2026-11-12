#!/usr/bin/env python3
"""Explicit cooperative offline Coding registration/admission; never installation.

SOURCE delivery grants no live operating authority. Public cutover remains OFF.
Exit 0 observed success/read, 1 refused, 2 usage, 4 publication uncertain.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Callable, Literal

from pydantic import TypeAdapter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.install_workflow_sequential_schema import (  # noqa: E402
    _checkpoint_observation, _file_observation,
)
from scripts.migrate_workflow_submission_schema import (  # noqa: E402
    _lifecycle_snapshot, resolve_org_database,
)
from runtime.infrastructure.workflow_schema import (  # noqa: E402
    _coding_org_identity,
)
from runtime.workflows.coding_authority_history import _bounded_rows  # noqa: E402
from runtime.workflows.sequential_contracts import Closed, DefinitionPin, Positive, Token  # noqa: E402
from runtime.workflows.sequential_definition import CompiledDefinition, _json  # noqa: E402
from runtime.workflows.sequential_engine import _engine_rows, _identity as _record_id  # noqa: E402
from runtime.workflows.sequential_history import (  # noqa: E402
    SequentialHistory, StorageSettings, _ReadBudget, _bytes, _decode, _sql_budget, read_sequential_history,
)
from runtime.workflows.sequential_release import read_coding_release  # noqa: E402

# The existing offline operator, separate from Founder PUT/lookup's namespace.
# This is operation provenance, not a runtime principal or permission grant.
PRINCIPAL = 'operator/sequential-coding'
_MARKERS = ('HAPPYRANCH_RUNTIME_SESSION_ID', 'HAPPYRANCH_ORG_SLUG',
            'HAPPYRANCH_TASK_TMP_ROOT', 'HAPPYRANCH_TASK_SCRATCH_MANIFEST')

# Sized against 10x the observed 1.453GB / 15,617-task / 27,327-result org.
# Complete-reader defaults and the existing installer remain unchanged.
_OPERATION_SECONDS = 900.0
_BACKUP_BYTES = 32 * 1024**3
_HASH_CHUNK = 4 * 1048576
_OWNERSHIP_SQL_LIMIT = 640000000
_WRITE_COLUMNS = {
    'workflow_seq_definitions': ('id', 'identity', 'version', 'definition_bytes',
        'definition_digest', 'source_digest', 'compiler_pin', 'validator_pin',
        'interpreter_pin', 'capability_pin'),
    'workflow_seq_settings': ('id', 'org_slug', 'definition_id', 'definition_identity',
        'generation', 'snapshot_bytes', 'snapshot_digest'),
    'workflow_seq_settings_operations': ('id', 'org_slug', 'principal', 'operation_key',
        'request_bytes', 'request_digest', 'settings_id', 'receipt_bytes', 'receipt_digest'),
}


def _history(conn: sqlite3.Connection, org: str, deadline: float) -> SequentialHistory:
    return read_sequential_history(conn, expected_org_slug=org, deadline=deadline,
                                   ownership_sql_limit=_OWNERSHIP_SQL_LIMIT)


def _inventory(conn: sqlite3.Connection, deadline: float) -> tuple:
    """Complete SQL plus typed rowid/cells of every relation we may write.

    Unrelated history has schema/identity protection and complete-reader guards,
    not a whole-database Python copy/hash. Native pre-fetch gates retain 8192
    acquired rows, 1MiB cells and 64MiB raw/copy work per preservation snapshot.
    """
    if type(conn) is not sqlite3.Connection or not conn.in_transaction:
        raise ValueError('sequential_coding_inventory_requires_native_snapshot')
    budget = _ReadBudget(deadline=deadline)

    def rows(table: str, columns: tuple[str, ...]) -> tuple:
        allowed = (('text', 'blob', 'integer', 'real', 'null'),) * len(columns)
        result = []
        for row in _bounded_rows(conn, table, columns, allowed, budget, with_rowid=True):
            budget.reserve(512 + 128 * len(columns))
            result.append(row)
        return tuple(result)

    with _sql_budget(conn, budget):
        schema = rows('sqlite_schema', ('type', 'name', 'tbl_name', 'sql'))
        tables = {table: rows(table, columns) for table, columns in _WRITE_COLUMNS.items()}
    return schema, tables


def _readonly_inventory(path: Path, org: str, deadline: float) -> tuple:
    before = _checkpoint_observation(path)
    conn = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True, timeout=1.0)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        layout = _history(conn, org, deadline).layout
        if layout.document is None or layout.ownership != (2 if layout.sequential else 1):
            raise ValueError('workflow_seq_install_requires_complete_document_and_ownership_companion')
        with _sql_budget(conn, _ReadBudget(deadline=deadline, sql_limit=_OWNERSHIP_SQL_LIMIT)):
            if conn.execute('PRAGMA foreign_key_check').fetchone() is not None:
                raise ValueError('workflow_seq_install_foreign_key_mismatch')
        # Missing storage is reported before acquiring any absent relation.
        inventory = _inventory(conn, deadline) if layout.sequential else None
        if _checkpoint_observation(path) != before:
            raise ValueError('workflow_seq_readonly_source_changed')
        return layout, inventory, before
    finally:
        conn.close()


def _backup_observation(backup: Path, source: Path, digest: str, org: str,
                        source_inventory: tuple, deadline: float) -> tuple:
    before = _file_observation(backup)
    if before[1:3] == _file_observation(source)[1:3]:
        raise ValueError('workflow_seq_backup_is_source')
    if re.fullmatch('[0-9a-f]{64}', digest) is None:
        raise ValueError('workflow_seq_backup_sha256_invalid')
    if before[4] > _BACKUP_BYTES:
        raise ValueError('sequential_coding_backup_bytes_exhausted')
    hashed = hashlib.sha256()
    count = 0
    with backup.open('rb') as stream:
        while True:
            if time.monotonic() >= deadline:
                raise ValueError('sequential_coding_backup_deadline_exhausted')
            block = stream.read(min(_HASH_CHUNK, _BACKUP_BYTES - count + 1))
            if not block:
                break
            count += len(block)
            if count > _BACKUP_BYTES:
                raise ValueError('sequential_coding_backup_bytes_exhausted')
            hashed.update(block)
    _, inventory, observation = _readonly_inventory(backup, org, deadline)
    if (hashed.hexdigest() != digest or count != before[4]
            or observation[0] != before or inventory != source_inventory):
        raise ValueError('workflow_seq_backup_inventory_or_digest_mismatch')
    return observation


def _preserved(before: tuple, after: tuple, inserted: dict[str, tuple]) -> None:
    schema, tables = before
    expected = {table: rows + ((inserted[table],) if table in inserted else ())
                for table, rows in tables.items()}
    if after != (schema, expected):
        raise ValueError('sequential_coding_preservation_mismatch')


def _insert_only(action: str) -> Callable[[int, str | None, str | None, str | None, str | None], int]:
    tables = ({'workflow_seq_definitions'} if action == 'register' else
              {'workflow_seq_settings', 'workflow_seq_settings_operations'})

    def authorize(code: int, first: str | None, second: str | None,
                  database: str | None, trigger: str | None) -> int:
        # Installed only around the literal INSERT statements; no fallback,
        # trigger, schema, UPDATE/DELETE or unrelated-table write is permitted.
        if (trigger is None and database == 'main' and code == sqlite3.SQLITE_INSERT
                and first in tables):
            return sqlite3.SQLITE_OK
        if trigger is None and code in (sqlite3.SQLITE_READ, sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    return authorize


class AdmissionCommand(Closed):
    format: Literal['workflow-sequential-coding-admission@1']
    operation_key: Token
    definition: DefinitionPin
    expected_generation: Positive
    admission: Literal['Allowed', 'Disabled']


class PublicationUncertain(ValueError):
    """COMMIT was attempted, or an observed publication could not be verified."""


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _command(path: Path) -> AdmissionCommand:
    before = _file_observation(path)
    if before[4] > 16384:
        raise ValueError('sequential_coding_command_too_large')

    def observed(info: os.stat_result) -> tuple:
        return (str(path), info.st_dev, info.st_ino, info.st_mode,
                info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        if observed(os.fstat(stream.fileno())) != before:
            raise ValueError('sequential_coding_command_changed')
        raw = stream.read(16385)
        if observed(os.fstat(stream.fileno())) != before or _file_observation(path) != before:
            raise ValueError('sequential_coding_command_changed')
    return AdmissionCommand.model_validate(_json(raw, 16384))


def _offline(args: argparse.Namespace) -> tuple:
    if not args.confirm_cooperative_offline:
        raise ValueError('sequential_coding_cooperative_offline_confirmation_required')
    if any(name in os.environ for name in _MARKERS):
        raise ValueError('sequential_coding_agent_environment_refused')
    snapshot = _lifecycle_snapshot(args.runtime_root)
    if (any(item[1] is not None for item in snapshot if item[0] in ('daemon.pid', 'daemon.port'))
            or not any(item[0] == 'runtimes.yaml' and item[1] is not None for item in snapshot)):
        raise ValueError('sequential_coding_daemon_stop_unproven')
    return snapshot


def _inspect(conn: sqlite3.Connection, *, org: str, action: str, command: AdmissionCommand | None,
             key: str | None, compiled: CompiledDefinition, budget: _ReadBudget) -> dict:
    history = _history(conn, org, budget.deadline)
    if not history.layout.sequential:
        raise ValueError('sequential_coding_storage_not_installed')
    pin = compiled.pin
    with _sql_budget(conn, budget):
        definitions = _engine_rows(conn, 'workflow_seq_definitions', budget,
            'definition_digest=? OR (identity=? AND version=?)',
            (pin.definition_digest, pin.identity, pin.version))
        definition = dict(id=_record_id('definition', pin.definition_digest), identity=pin.identity,
            version=pin.version, definition_bytes=compiled.raw, definition_digest=pin.definition_digest,
            source_digest=pin.source_digest, compiler_pin=pin.compiler, validator_pin=pin.validator,
            interpreter_pin=pin.interpreter, capability_pin=pin.capabilities)
        if definitions:
            # Existing immutable ID may come from another compatible publisher.
            definition['id'] = definitions[0]['id']
            if len(definitions) != 1 or definitions[0] != definition:
                raise ValueError('sequential_coding_definition_conflict')
        result = dict(definition=pin.model_dump(mode='json'), registered=bool(definitions))
        if action == 'register':
            return dict(result, write=None if definitions else definition)
        if not definitions or (command is not None and command.definition != pin):
            raise ValueError('sequential_coding_definition_conflict')
        settings = _engine_rows(conn, 'workflow_seq_settings', budget,
            'org_slug=? AND definition_identity=?', (org, pin.identity))
        current = max(settings, key=lambda row: row['generation']) if settings else None
        result['current'] = None if current is None else dict(generation=current['generation'],
            snapshot_digest=current['snapshot_digest'],
            admission=_decode(current['snapshot_bytes'], budget)['admission'])
        operations = _engine_rows(conn, 'workflow_seq_settings_operations', budget,
            'org_slug=? AND principal=? AND operation_key=?', (org, PRINCIPAL, key))
        if operations:
            operation = operations[0]
            saved = next(row for row in settings if row['id'] == operation['settings_id'])
            snapshot = StorageSettings.model_validate(_decode(saved['snapshot_bytes'], budget))
            original = AdmissionCommand(format='workflow-sequential-coding-admission@1',
                operation_key=key, definition=snapshot.definition,
                expected_generation=saved['generation'] - 1, admission=snapshot.admission)
            if command is not None and command != original:
                raise ValueError('sequential_coding_operation_conflict')
            return dict(result, command=original.model_dump(mode='json'),
                receipt=_decode(operation['receipt_bytes'], budget), write=None)
        if command is None:
            raise ValueError('sequential_coding_operation_not_found')
        if current is None:
            raise ValueError('sequential_coding_defaults_required')
        if current['generation'] != command.expected_generation:
            raise ValueError('sequential_coding_generation_stale')
        if current['generation'] == 9223372036854775807:
            raise ValueError('sequential_coding_generation_exhausted')
        snapshot = StorageSettings.model_validate(_decode(current['snapshot_bytes'], budget))
        # Copy only NEW saved defaults. No role resolution, old Explicit lookup,
        # run re-pinning, resource override or automatic initial settings.
        raw = _bytes(snapshot.model_copy(update={'admission': command.admission}).model_dump(mode='json'))
        return dict(result, command=command.model_dump(mode='json'), write=dict(
            definition_id=definition['id'], generation=current['generation'] + 1, raw=raw))


def _write(conn: sqlite3.Connection, org: str, action: str, command: AdmissionCommand | None,
           selected: dict) -> dict[str, tuple]:
    row = selected['write']

    def insert(table: str, values: tuple) -> tuple:
        columns = ','.join(_WRITE_COLUMNS[table])
        cursor = conn.execute(f'INSERT INTO {table} ({columns}) VALUES ({",".join("?" for _ in values)})', values)
        types = {str: 'text', bytes: 'blob', int: 'integer'}
        return (cursor.lastrowid, *values, *(types[type(value)] for value in values))

    if action == 'register':
        return {'workflow_seq_definitions': insert('workflow_seq_definitions', tuple(row.values()))}
    assert command is not None
    key = command.operation_key
    setting_id = _record_id('settings', org, PRINCIPAL, key)
    operation_id = _record_id('settings-operation', org, PRINCIPAL, key)
    digest = _digest(row['raw'])
    request = _bytes(dict(format='workflow-sequential-settings-operation-storage@1',
        operation_key=key, principal=PRINCIPAL, settings_id=setting_id,
        expected_generation=command.expected_generation, snapshot_digest=digest))
    receipt = _bytes(dict(format='workflow-sequential-settings-receipt-storage@1',
        operation_id=operation_id, settings_id=setting_id, generation=row['generation'], snapshot_digest=digest))
    return {
        'workflow_seq_settings': insert('workflow_seq_settings',
            (setting_id, org, row['definition_id'], command.definition.identity,
             row['generation'], row['raw'], digest)),
        'workflow_seq_settings_operations': insert('workflow_seq_settings_operations',
            (operation_id, org, PRINCIPAL, key, request, _digest(request),
             setting_id, receipt, _digest(receipt))),
    }


def operation(args: argparse.Namespace) -> dict:
    deadline = time.monotonic() + _OPERATION_SECONDS
    budget = _ReadBudget(deadline=deadline)
    offline = _offline(args)
    command = None
    key = args.operation_key
    if key is not None:
        TypeAdapter(Token).validate_python(key, strict=True)
    if args.action == 'admission':
        command = _command(args.from_file)
        key = command.operation_key
    compiled = read_coding_release()  # Resource discovery precedes SQLite scopes.
    path = resolve_org_database(args.runtime_root, args.org)
    layout, inventory, observation = _readonly_inventory(path, args.org, deadline)
    if not layout.sequential:
        raise ValueError('sequential_coding_storage_not_installed')

    def stable() -> None:
        if (time.monotonic() >= deadline or _offline(args) != offline
                or resolve_org_database(args.runtime_root, args.org) != path):
            raise ValueError('sequential_coding_source_or_deadline_changed')

    conn = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True, timeout=1.0)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        selected = _inspect(conn, org=args.org, action=args.action, command=command,
                            key=key, compiled=compiled, budget=budget)
        stable()
        if _checkpoint_observation(path) != observation:
            raise ValueError('sequential_coding_source_changed')
    finally:
        conn.close()
    stable()
    if selected['write'] is None:
        return {name: value for name, value in selected.items() if name != 'write'}
    if args.backup is None or args.backup_sha256 is None:
        raise ValueError('sequential_coding_verified_backup_required')
    backup = _backup_observation(args.backup, path, args.backup_sha256, args.org, inventory, deadline)
    if _checkpoint_observation(path) != observation:
        raise ValueError('sequential_coding_source_changed')
    stable()
    conn = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    attempted = False
    try:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN IMMEDIATE')

        def writer_stable() -> None:
            stable()
            if (_coding_org_identity(conn, expected_org_slug=args.org,
                                     expected_path=str(path)) != observation[0][:4]
                    or _checkpoint_observation(args.backup) != backup):
                raise ValueError('sequential_coding_identity_or_backup_changed')

        writer_stable()
        if _inventory(conn, deadline) != inventory:
            raise ValueError('sequential_coding_inventory_changed')
        current = _inspect(conn, org=args.org, action=args.action, command=command,
                           key=key, compiled=compiled, budget=budget)
        if current != selected:
            raise ValueError('sequential_coding_selection_changed')
        changes = conn.total_changes
        conn.set_authorizer(_insert_only(args.action))
        try:
            inserted = _write(conn, args.org, args.action, command, selected)
        finally:
            conn.set_authorizer(None)
        if conn.total_changes - changes != len(inserted):
            raise ValueError('sequential_coding_write_count_mismatch')
        _preserved(inventory, _inventory(conn, deadline), inserted)
        verified = _inspect(conn, org=args.org, action=args.action, command=command,
                            key=key, compiled=compiled, budget=budget)
        if verified['write'] is not None:
            raise ValueError('sequential_coding_publication_unverified')
        writer_stable()
        attempted = True
        conn.commit()
        # Read again in one snapshot after actual COMMIT, preserving ambiguity.
        conn.execute('BEGIN')
        result = _inspect(conn, org=args.org, action=args.action, command=command,
                          key=key, compiled=compiled, budget=budget)
        _preserved(inventory, _inventory(conn, deadline), inserted)
        writer_stable()
        conn.rollback()
    except Exception as exc:
        try:
            if conn.in_transaction:
                conn.rollback()
        except Exception as rollback:
            raise PublicationUncertain(f'sequential_coding_rollback_uncertain: {exc}; rollback: {rollback}; '
                                       'preserve original command/key') from rollback
        if attempted:
            raise PublicationUncertain(f'sequential_coding_publication_uncertain: {exc}; '
                                       'preserve original command/key') from exc
        raise
    finally:
        try:
            conn.close()
        except Exception as exc:
            raise PublicationUncertain(f'sequential_coding_close_uncertain: {exc}; '
                                       'preserve original command/key') from exc
    try:
        stable()
    except Exception as exc:
        raise PublicationUncertain(f'sequential_coding_post_commit_uncertain: {exc}; '
                                   'preserve original command/key') from exc
    return {name: value for name, value in result.items() if name != 'write'}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('register', 'admission', 'admission-show'))
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--org', required=True)
    parser.add_argument('--confirm-cooperative-offline', action='store_true', help=(
        'Operator maintains positive stopped state and complete native writer/preparation/process/owner/job '
        'exclusion across affected runtimes/shared-daemon stores/cooperative hosts until return; '
        'no publication/profile/start/restart. PID absence is not historical closure or OS exclusion.'))
    parser.add_argument('--backup', type=Path)
    parser.add_argument('--backup-sha256')
    parser.add_argument('--from-file', type=Path)
    parser.add_argument('--operation-key', type=str)
    args = parser.parse_args(argv)
    if ((args.action == 'admission') != (args.from_file is not None)
            or (args.action == 'admission-show') != (args.operation_key is not None)):
        parser.error('admission requires --from-file; admission-show requires --operation-key; register accepts neither')
    try:
        result = operation(args)
        try:
            print(_bytes(result).decode('utf8'), flush=True)
        except OSError as exc:
            raise PublicationUncertain('sequential_coding_delivery_uncertain; preserve original command/key') from exc
        return 0
    except PublicationUncertain as exc:
        print(f'uncertain: {exc}', file=sys.stderr)
        return 4
    except Exception as exc:
        print(f'refused: {exc}; preserve original command/key; reconcile before explicit retry', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
