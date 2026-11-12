#!/usr/bin/env python3
"""Explicit cooperative offline Coding registration/admission; never installation.

SOURCE delivery grants no live operating authority. Public cutover remains OFF.
Exit 0 observed success/read, 1 refused, 2 usage, 4 publication uncertain.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Literal

from pydantic import TypeAdapter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.install_workflow_sequential_schema import (  # noqa: E402
    _backup_observation, _checkpoint_observation, _file_observation, _readonly_inventory,
)
from scripts.migrate_workflow_submission_schema import (  # noqa: E402
    _lifecycle_snapshot, resolve_org_database,
)
from runtime.infrastructure.workflow_schema import (  # noqa: E402
    _coding_org_identity, sequential_storage_inventory,
)
from runtime.workflows.sequential_contracts import Closed, DefinitionPin, Positive, Token  # noqa: E402
from runtime.workflows.sequential_definition import CompiledDefinition, _json  # noqa: E402
from runtime.workflows.sequential_engine import _engine_rows, _identity as _record_id  # noqa: E402
from runtime.workflows.sequential_history import (  # noqa: E402
    StorageSettings, _ReadBudget, _bytes, _decode, _sql_budget, read_sequential_history,
)
from runtime.workflows.sequential_release import read_coding_release  # noqa: E402

# The existing offline operator, separate from Founder PUT/lookup's namespace.
# This is operation provenance, not a runtime principal or permission grant.
PRINCIPAL = 'operator/sequential-coding'
_MARKERS = ('HAPPYRANCH_RUNTIME_SESSION_ID', 'HAPPYRANCH_ORG_SLUG',
            'HAPPYRANCH_TASK_TMP_ROOT', 'HAPPYRANCH_TASK_SCRATCH_MANIFEST')


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
    history = read_sequential_history(conn, expected_org_slug=org, deadline=budget.deadline)
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
           selected: dict) -> None:
    row = selected['write']
    if action == 'register':
        columns = ','.join(row)
        conn.execute(f'INSERT INTO workflow_seq_definitions ({columns}) VALUES ({",".join("?" for _ in row)})',
                     tuple(row.values()))
        return
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
    conn.execute('INSERT INTO workflow_seq_settings '
        '(id,org_slug,definition_id,definition_identity,generation,snapshot_bytes,snapshot_digest) '
        'VALUES (?,?,?,?,?,?,?)', (setting_id, org, row['definition_id'], command.definition.identity,
                                 row['generation'], row['raw'], digest))
    conn.execute('INSERT INTO workflow_seq_settings_operations '
        '(id,org_slug,principal,operation_key,request_bytes,request_digest,settings_id,receipt_bytes,receipt_digest) '
        'VALUES (?,?,?,?,?,?,?,?,?)', (operation_id, org, PRINCIPAL, key, request, _digest(request),
                                      setting_id, receipt, _digest(receipt)))


def operation(args: argparse.Namespace) -> dict:
    deadline = time.monotonic() + 5.0
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
    layout, inventory, observation = _readonly_inventory(path, args.org)
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
    backup = _backup_observation(args.backup, path, args.backup_sha256, args.org, inventory)
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
        if sequential_storage_inventory(conn) != inventory:
            raise ValueError('sequential_coding_inventory_changed')
        current = _inspect(conn, org=args.org, action=args.action, command=command,
                           key=key, compiled=compiled, budget=budget)
        if current != selected:
            raise ValueError('sequential_coding_selection_changed')
        _write(conn, args.org, args.action, command, selected)
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
