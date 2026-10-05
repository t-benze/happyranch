#!/usr/bin/env python3
"""Explicit, org-only S1 migration. Run only with operator authorization."""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys

import yaml
from pathlib import Path

# A direct script invocation must use this checkout's release code.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime.infrastructure.workflow_schema import (  # noqa: E402
    draft_migration_guidance, migrate_draft_schema, validate_workflow_schema,
)
from runtime.runtime import RuntimeDir  # noqa: E402


def resolve_org_database(runtime_root: Path, slug: str) -> Path:
    if (not runtime_root.is_absolute() or re.fullmatch(r'[a-z0-9-]{1,40}', slug) is None
            or slug in {'_pending', '_archive'}):
        raise ValueError('invalid_runtime_root_or_org_slug')
    root = runtime_root.resolve(strict=True)
    marker = root / 'happyranch.yaml'
    if marker.is_symlink() or not marker.is_file():
        raise ValueError('invalid_runtime_marker')
    data = yaml.safe_load(marker.read_text())
    if (not isinstance(data, dict) or type(data.get('schema_version')) is not int
            or data['schema_version'] != 2 or data.get('type') != 'multi-org-runtime'):
        raise ValueError('invalid_runtime_marker')
    runtime = RuntimeDir.load(root)
    org = runtime.orgs_dir / slug
    # A symlink may redirect to another org or even runtime-audit.db. Refuse it.
    if (runtime.orgs_dir.is_symlink() or org.is_symlink() or not org.is_dir()
            or org.resolve(strict=True) != root / 'orgs' / slug
            or (org / 'org').is_symlink() or (org / 'org' / 'teams.yaml').is_symlink()
            or not (org / 'org' / 'teams.yaml').is_file()):
        raise ValueError('not_an_initialized_org')
    path = org / 'happyranch.db'
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise ValueError('missing_or_nonorg_database')
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--org', required=True)
    parser.add_argument('--check', action='store_true', help='read-only readiness; exit 3 if migration needed')
    args = parser.parse_args(argv)
    conn = None
    try:
        path = resolve_org_database(args.runtime_root, args.org)
        conn = sqlite3.connect(path.as_uri() + ('?mode=ro' if args.check else '?mode=rw'),
                               uri=True, timeout=1.0)
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN' if args.check else 'BEGIN IMMEDIATE')
        if args.check:
            layout = validate_workflow_schema(conn, expected_org_slug=args.org)
            conn.rollback()
            if layout == 'F':
                print('migration-needed: ' + draft_migration_guidance(org_slug=args.org, runtime_root=str(args.runtime_root)))
                return 3
            outcome = 'ready'
        else:
            outcome = migrate_draft_schema(conn, expected_org_slug=args.org)
            conn.commit()
        print(f'{outcome}: org {args.org}; layout E; compatible reader required')
        return 0
    except (OSError, ValueError, yaml.YAMLError, sqlite3.DatabaseError) as exc:
        if conn is not None:
            conn.rollback()
        print(f'refused: {exc}; reconcile the named org/storage and retry the explicit script', file=sys.stderr)
        return 1
    finally:
        if conn is not None:
            conn.close()


if __name__ == '__main__':
    raise SystemExit(main())
