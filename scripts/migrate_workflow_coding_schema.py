#!/usr/bin/env python3
"""I-to-empty-J SOURCE; operationally unavailable until actual-file binding ships.

All invocations refuse before writer open, including --check and ready replay.
A path stat is not proof of the SQLite connection's independently opened file.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.migrate_workflow_submission_schema import (  # noqa: E402
    _lifecycle_snapshot, resolve_org_database,
)
from runtime.infrastructure.workflow_schema import (  # noqa: E402
    _coding_org_identity, _validate_release_database, coding_migration_guidance,
    migrate_coding_schema, validate_workflow_schema,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--org', required=True)
    parser.add_argument('--check', action='store_true', help='Read-only; currently refuses (exit 1): actual-file connection binding unavailable')
    parser.add_argument('--confirm-cooperative-offline', action='store_true', help=(
        'Operator affirms documented daemon stop, all hosts/outer tails reconciled, '
        'and no cooperative daemon/host/publication/profile/move restart until return. '
        'This is not PID absence or an OS exclusion guarantee.'))
    args = parser.parse_args(argv)
    conn = None
    try:
        path = resolve_org_database(args.runtime_root, args.org)
        before = path.stat()
        # Always start read-only, including already-J replay. Only a validated
        # I source and affirmative operator boundary can open a writer.
        conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1.0)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        # This binding currently refuses categorically. Do not call the older
        # path-only _identity or treat its tuple as opened-connection proof.
        identity = _coding_org_identity(conn, expected_org_slug=args.org)
        if identity[1:3] != (before.st_dev, before.st_ino):
            raise ValueError('org_database_identity_changed')
        conn.execute('BEGIN')
        layout = validate_workflow_schema(conn, expected_org_slug=args.org)
        _validate_release_database(conn, layout, expected_org_slug=args.org)
        conn.rollback()
        if layout == 'J':
            print('already-compatible: empty coding layout J; J-compatible reader required')
            return 0
        if layout != 'I':
            raise ValueError('workflow_coding_migration_requires_i; use separately approved earlier migrations first')
        if args.check:
            print('migration-needed: ' + coding_migration_guidance())
            return 3
        if not args.confirm_cooperative_offline:
            raise ValueError('workflow_coding_cooperative_offline_confirmation_required: ' + coding_migration_guidance())
        offline = _lifecycle_snapshot(args.runtime_root)
        # A stale PID does not prove stop; the documented stop owner removes
        # both lifecycle files. Unknown or unregistered daemon homes refuse.
        if (any(item[1] is not None for item in offline if item[0] in ('daemon.pid', 'daemon.port'))
                or not any(item[0] == 'runtimes.yaml' and item[1] is not None for item in offline)):
            raise ValueError('workflow_coding_documented_daemon_stop_unproven')
        conn.close()
        conn = None
        conn = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=1.0)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        # Retain the ORIGINAL read-only identity across the reopen gap. A new
        # writer's own identity cannot become a replacement source baseline.
        if _coding_org_identity(conn, expected_org_slug=args.org) != identity:
            raise ValueError('workflow_coding_offline_source_identity_changed')

        def offline_check() -> None:
            # Resolution supplies owned markers/path prerequisites only. A
            # future supported connection binding must independently recheck
            # actual opened identity; cooperative no-restart stays external.
            if (resolve_org_database(args.runtime_root, args.org) != path
                    or _coding_org_identity(conn, expected_org_slug=args.org) != identity
                    or _lifecycle_snapshot(args.runtime_root) != offline):
                raise ValueError('workflow_coding_offline_source_identity_changed')

        outcome = migrate_coding_schema(conn, expected_org_slug=args.org, offline_check=offline_check)
        print(f'{outcome}: org {args.org}; empty coding layout J; J-compatible reader required')
        return 0
    except Exception as exc:
        # Existing naming/Pause/move/authority readers have their own refusal
        # types. No failed reader can become a successful legacy observation.
        if conn is not None:
            conn.rollback()
        message = ('workflow_coding_storage_busy' if isinstance(exc, sqlite3.OperationalError)
                   and 'locked' in str(exc) else str(exc))
        print(f'refused: {message}; original org/source/host owner must reconcile before explicit retry', file=sys.stderr)
        return 1
    finally:
        if conn is not None:
            conn.close()


if __name__ == '__main__':
    raise SystemExit(main())
