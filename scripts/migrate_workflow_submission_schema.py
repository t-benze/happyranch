#!/usr/bin/env python3
"""Explicit org-only G migration; authorized cooperative offline operation only."""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import stat
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.migrate_workflow_draft_schema import resolve_org_database as _resolve_draft_database  # noqa: E402
from runtime.infrastructure.workflow_schema import (  # noqa: E402
    _event_revision, _records, _validate_release_database,
    migrate_submission_schema, submission_migration_guidance, validate_workflow_schema,
)
from runtime.runtime import daemon_home  # noqa: E402


def resolve_org_database(runtime_root: Path, slug: str) -> Path:
    if runtime_root.is_symlink():
        raise ValueError('runtime_root_symlink_refused')
    return _resolve_draft_database(runtime_root, slug)


def _lifecycle_snapshot(runtime_root: Path) -> tuple:
    """Bounded existing lifecycle observation; no socket, stop, or new protocol.

    daemon.sh stop removes PID/port files. Any remaining port or live/unknown
    PID refuses. Missing lifecycle files describe a never-recorded daemon only
    under the operator's cooperative offline precondition, not arbitrary-start
    exclusion. Readiness checks and validated G replay do not call this owner.
    """
    home = daemon_home()
    if not home.is_absolute() or home.is_symlink() or not home.is_dir():
        raise ValueError('daemon_offline_observation_unavailable; establish configured daemon home and stopped state')
    values = []
    for name in ('daemon.pid', 'daemon.port', 'runtimes.yaml'):
        path = home / name
        try:
            info = path.lstat()
        except FileNotFoundError:
            values.append((name, None))
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 65536:
            raise ValueError(f'daemon_offline_observation_invalid: {name}')
        with path.open('rb') as file:
            opened = os.fstat(file.fileno())
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError('daemon_offline_observation_changed')
            raw = file.read(65537)
        after = path.stat()
        if len(raw) > 65536 or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_mode) != (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode):
            raise ValueError('daemon_offline_observation_changed')
        values.append((name, info.st_dev, info.st_ino, info.st_mtime_ns, raw))
        if name == 'daemon.port':
            raise ValueError('daemon_offline_port_present; use documented stop/status before explicit migration')
        if name == 'daemon.pid':
            if re.fullmatch(rb'[1-9][0-9]{0,9}\n?', raw) is None:
                raise ValueError('daemon_offline_pid_malformed')
            try:
                os.kill(int(raw), 0)
            except ProcessLookupError:
                pass
            except OSError as exc:
                raise ValueError('daemon_offline_pid_unknown') from exc
            else:
                raise ValueError('daemon_offline_pid_live; stop the daemon before migration')
        if name == 'runtimes.yaml':
            registry = yaml.safe_load(raw)
            if (not isinstance(registry, dict) or set(registry) != {'active', 'registered'}
                    or not isinstance(registry['registered'], list)
                    or any(not isinstance(p, str) or not Path(p).is_absolute() for p in registry['registered'])
                    or (registry['active'] is not None and (not isinstance(registry['active'], str)
                        or registry['active'] not in registry['registered']))
                    or str(runtime_root.resolve()) not in registry['registered']):
                raise ValueError('daemon_offline_runtime_registration_invalid')
    return tuple(values)


def _identity(path: Path, conn: sqlite3.Connection) -> tuple[int, int, int]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('org_database_identity_invalid')
    actual = conn.execute('PRAGMA database_list').fetchall()
    main = [row for row in actual if row[1] == 'main']
    if (len(main) != 1 or Path(main[0][2]) != path
            or any(row[1] not in ('main', 'temp') or (row[1] == 'temp' and row[2]) for row in actual)):
        raise ValueError('org_database_connection_identity_invalid')
    return info.st_dev, info.st_ino, info.st_mode


class _MigrationConnection(sqlite3.Connection):
    """Script-owned identity/offline reread after the actual SQLite reservation."""
    path: Path
    runtime_root: Path
    source_identity: tuple[int, int, int]
    offline: tuple

    def execute(self, sql: str, parameters: tuple = (), /) -> sqlite3.Cursor:
        result = super().execute(sql, parameters)
        if sql == 'BEGIN IMMEDIATE':
            if (_identity(self.path, self) != self.source_identity
                    or _lifecycle_snapshot(self.runtime_root) != self.offline):
                raise ValueError('offline_source_identity_changed_after_writer_acquisition')
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--org', required=True)
    parser.add_argument('--check', action='store_true', help='read-only; exit 3 when migration is needed')
    args = parser.parse_args(argv)
    conn = None
    try:
        path = resolve_org_database(args.runtime_root, args.org)
        before = path.stat()
        conn = sqlite3.connect(path.as_uri() + ('?mode=ro' if args.check else '?mode=rw'), uri=True, timeout=1.0,
                               factory=_MigrationConnection)
        conn.execute('PRAGMA foreign_keys=ON')
        identity = _identity(path, conn)
        if identity[:2] != (before.st_dev, before.st_ino):
            raise ValueError('org_database_identity_changed')
        conn.execute('BEGIN')
        layout = validate_workflow_schema(conn, expected_org_slug=args.org)
        _validate_release_database(conn, layout)
        if layout != 'G':
            for event in _records(conn, 'SELECT * FROM workflow_events'):
                _event_revision(conn, event)
        conn.rollback()
        if args.check:
            if layout != 'G':
                print('migration-needed: ' + submission_migration_guidance(org_slug=args.org, runtime_root=str(args.runtime_root)))
                return 3
            outcome = 'ready'
        elif layout == 'G':
            outcome = migrate_submission_schema(conn, expected_org_slug=args.org)
        else:
            offline = _lifecycle_snapshot(args.runtime_root)
            if _identity(path, conn) != identity or _lifecycle_snapshot(args.runtime_root) != offline:
                raise ValueError('offline_source_identity_changed')
            conn.path = path
            conn.runtime_root = args.runtime_root
            conn.source_identity = identity
            conn.offline = offline
            outcome = migrate_submission_schema(conn, expected_org_slug=args.org)
        print(f'{outcome}: org {args.org}; layout G; compatible reader required')
        return 0
    except (OSError, ValueError, yaml.YAMLError, sqlite3.DatabaseError) as exc:
        if conn is not None:
            conn.rollback()
        print(f'refused: {exc}; reconcile named org/source owner before retry', file=sys.stderr)
        return 1
    finally:
        if conn is not None:
            conn.close()


if __name__ == '__main__':
    raise SystemExit(main())
