#!/usr/bin/env python3
"""Offline disposable-venue settings publisher. Exit 0 ok, 1 refused, 2 usage.

No runtime caller, public route, live installation or authority grant. The
operator maintains stopped-state exclusion throughout this invocation.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sqlite3
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.install_workflow_sequential_schema import _checkpoint_observation  # noqa: E402
from scripts.migrate_workflow_submission_schema import (  # noqa: E402
    _lifecycle_snapshot, resolve_org_database,
)
from runtime.infrastructure.workflow_schema import inspect_sequential_install  # noqa: E402
from runtime.workflows.sequential_contracts import Limits  # noqa: E402
from runtime.workflows.sequential_definition import CompiledDefinition, compile_definition  # noqa: E402
from runtime.workflows.sequential_history import (  # noqa: E402
    StorageSettings, _bytes, read_sequential_history,
)
from runtime.workflows.sequential_venue import (  # noqa: E402
    VenueObservation, _home_fd, _no_constant, _signature, _unique, _venue_home,
    remove_marker, write_marker,
)

# Exact setup_coding._MARKERS values; importing that host setup would couple owners.
_MARKERS = ('HAPPYRANCH_RUNTIME_SESSION_ID', 'HAPPYRANCH_ORG_SLUG',
            'HAPPYRANCH_TASK_TMP_ROOT', 'HAPPYRANCH_TASK_SCRATCH_MANIFEST')


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _identity(kind: str, *parts: str) -> str:
    return 'seq-' + kind + '-' + _digest(_bytes(parts))


def _read_file(path: Path) -> bytes:
    info = path.lstat()
    if (path.resolve(strict=True) != path.absolute() or not stat.S_ISREG(info.st_mode)
            or info.st_size > 1048576):
        raise ValueError('sequential_venue_input_file_invalid')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if _signature(os.fstat(stream.fileno())) != _signature(info):
            raise ValueError('sequential_venue_input_changed')
        raw = stream.read(1048577)
        if (_signature(os.fstat(stream.fileno())) != _signature(info)
                or _signature(path.lstat()) != _signature(info) or len(raw) > 1048576):
            raise ValueError('sequential_venue_input_changed')
    return raw


def _definition(path: Path, source_digest: str, canonical_digest: str | None) -> CompiledDefinition:
    """EM A1': remove exactly the presentation LF; never normalize JSON."""
    raw = _read_file(path)
    if _digest(raw) != source_digest:
        raise ValueError('sequential_venue_source_digest_mismatch')
    if len(raw) < 2 or raw[-1:] != b'\n' or raw[-2:-1] in (b'\n', b'\r'):
        raise ValueError('sequential_venue_definition_newline_invalid')
    canonical = raw[:-1]
    if canonical_digest is not None and _digest(canonical) != canonical_digest:
        raise ValueError('sequential_venue_canonical_digest_mismatch')
    return compile_definition(canonical, source_digest=source_digest)


def _json_file(path: Path) -> object:
    return json.loads(_read_file(path).decode('utf8', errors='strict'),
                      object_pairs_hook=_unique, parse_constant=_no_constant)


def _offline(runtime_root: Path) -> tuple:
    snapshot = _lifecycle_snapshot(runtime_root)
    if (any(item[1] is not None for item in snapshot if item[0] in ('daemon.pid', 'daemon.port'))
            or not any(item[0] == 'runtimes.yaml' and item[1] is not None for item in snapshot)):
        raise ValueError('sequential_venue_daemon_stop_unproven')
    return snapshot


def _publish(args: argparse.Namespace) -> dict:
    org_root = args.runtime_root / 'orgs' / args.org
    home = _venue_home(org_root)  # No file opens before account-home exclusion.
    if not args.confirm_disposable_venue:
        raise ValueError('sequential_venue_confirmation_required')
    if any(name in os.environ for name in _MARKERS):
        raise ValueError('sequential_venue_agent_environment_refused')
    if (not args.runtime_root.is_absolute()
            or args.runtime_root.resolve(strict=True) != args.runtime_root):
        raise ValueError('sequential_venue_runtime_alias_refused')
    # Cooperative serialization includes post-COMMIT oracle/marker publication.
    # No lock file is created; this is not an OS exclusion of other writers.
    lock = _home_fd(home)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _publish_locked(args, home, org_root)
    finally:
        os.close(lock)


def _publish_locked(args: argparse.Namespace, home: Path, org_root: Path) -> dict:
    offline = _offline(args.runtime_root)  # Includes exact runtimes.yaml membership.
    path = resolve_org_database(args.runtime_root, args.org)
    identity = _signature(path.lstat())[:2]
    compiled = _definition(args.definition, args.definition_sha256, args.canonical_sha256)
    limits = compiled.definition.limits if args.limits_file is None else Limits.model_validate(_json_file(args.limits_file))
    if any(getattr(limits, name) > getattr(compiled.definition.limits, name) for name in Limits.model_fields):
        raise ValueError('sequential_venue_limits_exceed_definition')
    snapshot = StorageSettings.model_validate(dict(format='workflow-sequential-settings-storage@1',
        definition=compiled.pin, bindings=_json_file(args.bindings_file), limits=limits,
        admission=args.admission))
    bindings = {item.role: item.principal for item in snapshot.bindings}
    if len(bindings) != len(snapshot.bindings) or bindings.keys() != dict(compiled.definition.roles).keys():
        raise ValueError('sequential_venue_bindings_conflict')
    for role, requirement in compiled.definition.roles:
        if any(bindings[role] == bindings[other] for other in requirement.independent_of):
            raise ValueError('sequential_venue_independence_conflict')
    # Validate operator labels/key with the same finite syntax as binding principals.
    from runtime.workflows.sequential_contracts import BindingClaim
    BindingClaim(role='operator', principal=args.principal)
    BindingClaim(role='operation', principal=args.operation_key)
    raw = _bytes(snapshot.model_dump(mode='json'))
    digest = _digest(raw)
    settings_id = _identity('settings', args.org, args.principal, args.operation_key)
    operation_id = _identity('settings-operation', args.org, args.principal, args.operation_key)
    definition_id = _identity('definition', compiled.pin.definition_digest)
    # Immutable RO preflight: a refusal cannot create a journal or database.
    checkpoint = _checkpoint_observation(path)
    preflight = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        preflight.execute('PRAGMA foreign_keys=ON')
        if not inspect_sequential_install(preflight, expected_org_slug=args.org).sequential:
            raise ValueError('sequential_venue_storage_not_installed')
        if _checkpoint_observation(path) != checkpoint:
            raise ValueError('sequential_venue_checkpoint_changed')
    finally:
        preflight.close()
    conn = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=10.0)
    conn.row_factory = sqlite3.Row
    committed = False
    try:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN IMMEDIATE')
        if (_venue_home(org_root) != home or _offline(args.runtime_root) != offline
                or resolve_org_database(args.runtime_root, args.org) != path
                or _signature(path.lstat())[:2] != identity):
            raise ValueError('sequential_venue_source_changed')
        if not inspect_sequential_install(conn, expected_org_slug=args.org).sequential:
            raise ValueError('sequential_venue_storage_not_installed')
        existing = conn.execute('SELECT o.receipt_bytes,s.snapshot_bytes,s.definition_id '
            'FROM workflow_seq_settings_operations o JOIN workflow_seq_settings s ON s.id=o.settings_id '
            'WHERE o.org_slug=? AND o.principal=? AND o.operation_key=?',
            (args.org, args.principal, args.operation_key)).fetchone()
        replay = existing is not None
        if replay:
            if existing['snapshot_bytes'] != raw:
                raise ValueError('sequential_venue_operation_conflict')
            receipt_raw = existing['receipt_bytes']
            conn.rollback()  # Exact replay writes zero rows.
        else:
            found = conn.execute('SELECT * FROM workflow_seq_definitions WHERE definition_digest=? '
                                 'OR (identity=? AND version=?)',
                                 (compiled.pin.definition_digest, compiled.pin.identity, compiled.pin.version)).fetchall()
            if found:
                if (len(found) != 1 or found[0]['definition_bytes'] != compiled.raw
                        or found[0]['source_digest'] != compiled.pin.source_digest):
                    raise ValueError('sequential_venue_definition_conflict')
                definition_id = found[0]['id']
            generation = conn.execute('SELECT coalesce(max(generation),0)+1 FROM workflow_seq_settings '
                'WHERE org_slug=? AND definition_identity=?', (args.org, compiled.pin.identity)).fetchone()[0]
            request_raw = _bytes(dict(format='workflow-sequential-settings-operation-storage@1',
                operation_key=args.operation_key, principal=args.principal, settings_id=settings_id,
                expected_generation=generation - 1, snapshot_digest=digest))
            receipt_raw = _bytes(dict(format='workflow-sequential-settings-receipt-storage@1',
                operation_id=operation_id, settings_id=settings_id, generation=generation, snapshot_digest=digest))
            # Withdraw an earlier marker before republishing too: oracle failure stays off.
            remove_marker(org_root=org_root)
            if not found:
                pin = compiled.pin
                conn.execute('INSERT INTO workflow_seq_definitions '
                    '(id,identity,version,definition_bytes,definition_digest,source_digest,compiler_pin,'
                    'validator_pin,interpreter_pin,capability_pin) VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (definition_id, pin.identity, pin.version, compiled.raw, pin.definition_digest,
                     pin.source_digest, pin.compiler, pin.validator, pin.interpreter, pin.capabilities))
            conn.execute('INSERT INTO workflow_seq_settings '
                '(id,org_slug,definition_id,definition_identity,generation,snapshot_bytes,snapshot_digest) '
                'VALUES (?,?,?,?,?,?,?)', (settings_id, args.org, definition_id, compiled.pin.identity,
                                          generation, raw, digest))
            conn.execute('INSERT INTO workflow_seq_settings_operations '
                '(id,org_slug,principal,operation_key,request_bytes,request_digest,settings_id,receipt_bytes,receipt_digest) '
                'VALUES (?,?,?,?,?,?,?,?,?)', (operation_id, args.org, args.principal, args.operation_key,
                    request_raw, _digest(request_raw), settings_id, receipt_raw, _digest(receipt_raw)))
            conn.commit()
            committed = True
        if read_sequential_history(conn, expected_org_slug=args.org).status != 'storage readable':
            raise ValueError('sequential_venue_oracle_unreadable')
        receipt = json.loads(receipt_raw)
        newest = conn.execute('SELECT max(generation) FROM workflow_seq_settings WHERE org_slug=? '
            'AND definition_identity=?', (args.org, compiled.pin.identity)).fetchone()[0]
        # An old operation replay never changes the current marker decision.
        if receipt['generation'] == newest:
            if (_venue_home(org_root) != home or _offline(args.runtime_root) != offline
                    or _signature(path.lstat())[:2] != identity):
                raise ValueError('sequential_venue_source_changed_after_commit')
            if args.admission == 'Allowed':
                write_marker(VenueObservation(home, args.runtime_root, args.org, *identity))
            else:
                remove_marker(org_root=org_root)
        return dict(canonical_sha256=compiled.pin.definition_digest, receipt=receipt)
    except Exception:
        if not committed:
            conn.rollback()
        else:
            print('settings COMMITTED; post-commit refusal; preserve rows and original operation key', file=sys.stderr)
        raise
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('runtime-root', 'definition', 'bindings-file'):
        parser.add_argument('--' + flag, type=Path, required=True)
    for flag in ('org', 'definition-sha256', 'operation-key', 'principal'):
        parser.add_argument('--' + flag, required=True)
    parser.add_argument('--canonical-sha256', help='Optional exact SHA256 of raw definition minus its one final LF')
    parser.add_argument('--limits-file', type=Path)
    parser.add_argument('--admission', choices=('Allowed', 'Disabled'), required=True)
    parser.add_argument('--confirm-disposable-venue', action='store_true')
    args = parser.parse_args(argv)
    try:
        print(json.dumps(_publish(args), sort_keys=True, separators=(',', ':'), allow_nan=False))
        return 0
    except Exception as exc:
        print(f'refused: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
