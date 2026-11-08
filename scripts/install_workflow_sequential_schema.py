#!/usr/bin/env python3
"""Explicit cooperative offline sequential installer/check; no startup upgrade.

SOURCE delivery grants no permission to operate on real orgs. Read-only checks
use immutable checkpointed files and refuse WAL/journal residue or file drift.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.migrate_workflow_submission_schema import (  # noqa: E402
    _lifecycle_snapshot, resolve_org_database,
)
from runtime.infrastructure.workflow_schema import (  # noqa: E402
    _coding_org_identity, SequentialCommitUncertain, inspect_sequential_install,
    install_sequential_schema, sequential_storage_inventory,
)


def _file_observation(path: Path) -> tuple:
    info = path.lstat()
    if (not path.is_absolute() or path.resolve(strict=True) != path
            or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
        raise ValueError('workflow_seq_file_identity_invalid:' + str(path))
    return (str(path), info.st_dev, info.st_ino, info.st_mode,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _checkpoint_observation(path: Path) -> tuple:
    """Immutable RO never creates/writes sidecars; nonempty recovery data refuses."""
    observed = [_file_observation(path)]
    for suffix in ('-wal', '-journal', '-shm'):
        sidecar = Path(str(path) + suffix)
        try:
            sidecar.lstat()
        except FileNotFoundError:
            item = None
        else:
            item = _file_observation(sidecar)
        if item is not None and suffix != '-shm' and item[4] != 0:
            raise ValueError('workflow_seq_checkpoint_required:' + str(sidecar))
        observed.append(item)
    return tuple(observed)


def _readonly_inventory(path: Path, org: str) -> tuple:
    before = _checkpoint_observation(path)
    conn = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True, timeout=1.0)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN')
        layout = inspect_sequential_install(conn, expected_org_slug=org)
        inventory = sequential_storage_inventory(conn)
        if _checkpoint_observation(path) != before:
            raise ValueError('workflow_seq_readonly_source_changed')
        return layout, inventory, before
    finally:
        conn.close()


def _backup_observation(backup: Path, source: Path, digest: str, org: str,
                        source_inventory: tuple) -> tuple:
    before = _file_observation(backup)
    if before[1:3] == _file_observation(source)[1:3]:
        raise ValueError('workflow_seq_backup_is_source')
    if re.fullmatch('[0-9a-f]{64}', digest) is None:
        raise ValueError('workflow_seq_backup_sha256_invalid')
    with backup.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    _, inventory, observation = _readonly_inventory(backup, org)
    if actual != digest or observation[0] != before or inventory != source_inventory:
        raise ValueError('workflow_seq_backup_inventory_or_digest_mismatch')
    return observation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--org', required=True)
    parser.add_argument('--check', action='store_true', help=(
        'Read-only: 0 compatible-already-installed, 3 install-needed, 1 refused (2 usage)'))
    parser.add_argument('--backup', type=Path, help='Absolute separate checkpointed SQLite backup; required for installation')
    parser.add_argument('--backup-sha256', help='Operator-verified SHA256 of the backup file')
    parser.add_argument('--confirm-cooperative-offline', action='store_true', help=(
        'Operator maintains positive stopped state and complete current writer/preparation/process/native-owner/job '
        'exclusion across all affected runtimes/shared-daemon stores/cooperative hosts, with no publication, '
        'profile/move/start/restart until return. PID absence is not historical closure or OS exclusion.'))
    args = parser.parse_args(argv)
    conn = None
    outcome = None
    result = 1
    try:
        path = resolve_org_database(args.runtime_root, args.org)
        layout, inventory, original = _readonly_inventory(path, args.org)
        if (resolve_org_database(args.runtime_root, args.org) != path
                or _checkpoint_observation(path) != original):
            raise ValueError('workflow_seq_source_identity_changed')
        if layout.sequential:
            outcome = 'compatible-already-installed: compatible reader required, engine unavailable'
            result = 0
        elif args.check:
            outcome = 'install-needed: explicit cooperative offline install with verified backup required'
            result = 3
        else:
            if not args.confirm_cooperative_offline:
                raise ValueError('workflow_seq_cooperative_offline_confirmation_required')
            if args.backup is None or args.backup_sha256 is None:
                raise ValueError('workflow_seq_verified_backup_required')
            offline = _lifecycle_snapshot(args.runtime_root)
            if (any(item[1] is not None for item in offline if item[0] in ('daemon.pid', 'daemon.port'))
                    or not any(item[0] == 'runtimes.yaml' and item[1] is not None for item in offline)):
                raise ValueError('workflow_seq_documented_daemon_stop_unproven')
            backup = _backup_observation(args.backup, path, args.backup_sha256, args.org, inventory)
            if _checkpoint_observation(path) != original:
                raise ValueError('workflow_seq_source_identity_changed')
            identity = original[0][:4]
            conn = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=1.0)
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA foreign_keys=ON')

            def offline_check() -> None:
                if (resolve_org_database(args.runtime_root, args.org) != path
                        or _coding_org_identity(conn, expected_org_slug=args.org, expected_path=str(path)) != identity
                        or _lifecycle_snapshot(args.runtime_root) != offline
                        or _checkpoint_observation(args.backup) != backup):
                    raise ValueError('workflow_seq_offline_source_or_backup_changed')

            offline_check()
            installed = install_sequential_schema(
                conn, expected_org_slug=args.org, expected_path=str(path),
                source_identity=identity, source_inventory=inventory, offline_check=offline_check)
            outcome = f'{installed}: org {args.org}; compatible reader required, engine unavailable'
            result = 0
    except SequentialCommitUncertain as exc:
        print(f'uncertain: {exc}; preserve original evidence; reconcile before any explicit retry', file=sys.stderr)
        result = 4
    except Exception as exc:
        print(f'refused: {exc}; reconcile original org/source/host owner before explicit retry', file=sys.stderr)
        result = 1
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception as exc:
                print(f'uncertain: workflow_seq_connection_close_failed: {exc}; '
                      f'prior_outcome={outcome!r}', file=sys.stderr)
                result = 4
    # Success publication follows observed COMMIT and close, never a delivery flag.
    if outcome is not None and result in (0, 3):
        print(outcome)
    return result


if __name__ == '__main__':
    raise SystemExit(main())
