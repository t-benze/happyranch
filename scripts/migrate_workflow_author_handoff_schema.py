#!/usr/bin/env python3
"""Explicit offline org-only G-to-H migration; never a startup upgrade."""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.migrate_workflow_submission_schema import (  # noqa: E402
    _MigrationConnection, _identity, _lifecycle_snapshot, resolve_org_database,
)
from runtime.infrastructure.workflow_schema import (  # noqa: E402
    _validate_release_database, _validate_submission_source_ownership,
    author_handoff_migration_guidance, migrate_author_handoff_schema, validate_workflow_schema,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--org-root', required=True, type=Path)
    parser.add_argument('--org-slug', required=True)
    parser.add_argument('--check', action='store_true', help='No writes; exit 1 if migration is required')
    args = parser.parse_args(argv)
    conn = None
    try:
        root = args.org_root
        if not root.is_absolute() or root.is_symlink() or root.name != args.org_slug or root.parent.name != 'orgs':
            raise ValueError('invalid_org_root')
        runtime_root = root.parent.parent
        path = resolve_org_database(runtime_root, args.org_slug)
        if path.parent != root:
            raise ValueError('org_root_identity_mismatch')
        before = path.stat()
        conn = sqlite3.connect(path.as_uri() + ('?mode=ro' if args.check else '?mode=rw'),
                               uri=True, timeout=5.0, factory=_MigrationConnection)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        identity = _identity(path, conn)
        if identity[:2] != (before.st_dev, before.st_ino):
            raise ValueError('org_database_identity_changed')
        conn.execute('BEGIN')
        layout = validate_workflow_schema(conn, expected_org_slug=args.org_slug)
        _validate_release_database(conn, layout, expected_org_slug=args.org_slug)
        conn.rollback()
        if layout not in ('G', 'H', 'I', 'J'):
            raise ValueError('workflow_author_handoff_requires_g; use the separately approved G migration first')
        if layout in ('H', 'I', 'J'):
            print(f'already-compatible: layout {layout}; compatible reader required')
            return 0
        if args.check:
            print('migration-required: ' + author_handoff_migration_guidance())
            return 1
        offline = _lifecycle_snapshot(runtime_root)
        _validate_submission_source_ownership(conn, layout)
        if _identity(path, conn) != identity or _lifecycle_snapshot(runtime_root) != offline:
            raise ValueError('offline_source_identity_changed')
        conn.path, conn.runtime_root = path, runtime_root
        conn.source_identity, conn.offline = identity, offline
        outcome = migrate_author_handoff_schema(conn, expected_org_slug=args.org_slug)
        print(f'{outcome}: layout H; compatible reader required')
        return 0
    except (OSError, ValueError, yaml.YAMLError, sqlite3.DatabaseError) as exc:
        if conn is not None:
            conn.rollback()
        message = 'workflow_author_handoff_storage_busy' if isinstance(exc, sqlite3.OperationalError) and 'locked' in str(exc) else str(exc)
        print(f'refused: {message}; reconcile org/source ownership before retry', file=sys.stderr)
        return 1
    finally:
        if conn is not None:
            conn.close()


if __name__ == '__main__':
    raise SystemExit(main())
