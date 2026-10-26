"""Read-only host maintenance composition. Never constructs Database/OrgState.

This is a cooperative observation, not a maintenance reservation. The original
schema/history owners remain authoritative; historical closure is not inferred.
Only setup_coding imports this module, after its stdlib host-entry denial.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import time
from contextlib import contextmanager
from pathlib import Path

import yaml


class Hold(ValueError):
    """An observation is unavailable or an original owner still owns work."""

    def __init__(self, message: str, *, category: str = 'observation_hold', details: dict | None = None) -> None:
        super().__init__(message)
        # Message interpolation is private, never a terminal-safe diagnostic.
        self.category = category
        self.details = details


def literal(path: Path, *, missing: bool = False) -> Path:
    if not path.is_absolute() or '..' in path.parts or '.' in path.parts:
        raise Hold(f'absolute literal path required: {path}')
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            if missing:
                continue
            raise Hold(f'missing path: {part}') from None
        if stat.S_ISLNK(info.st_mode):
            raise Hold(f'aliased path: {part}; original path owner must reconcile')
    return path


def identity(path: Path) -> tuple:
    literal(path)
    s = path.lstat()
    return (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_nlink)


def read_file(path: Path, limit: int = 1048576) -> bytes:
    before = identity(path)
    if not stat.S_ISREG(before[2]) or before[4] != 1:
        raise Hold(f'not a single-link regular file: {path}')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        s = os.fstat(stream.fileno())
        if (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_nlink) != before:
            raise Hold(f'file changed: {path}')
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        if (s.st_size, s.st_mtime_ns, s.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise Hold(f'file changed during read: {path}')
    if len(raw) > limit or identity(path) != before:
        raise Hold(f'file moving/oversized: {path}')
    return raw


def registry(home: Path) -> dict:
    raw = read_file(home / 'runtimes.yaml', 65536)
    # safe_load alone silently accepts duplicate keys. Refuse them at every level.
    class Unique(yaml.SafeLoader):
        pass
    def mapping(loader, node):
        result = {}
        for k, v in node.value:
            key = loader.construct_object(k)
            if key in result:
                raise Hold('duplicate registry key')
            result[key] = loader.construct_object(v)
        return result
    Unique.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    data = yaml.load(raw, Loader=Unique)
    if (type(data) is not dict or set(data) != {'active', 'registered'}
            or type(data['registered']) is not list or not data['registered']
            or len(data['registered']) > 128
            or any(type(x) is not str for x in data['registered'])
            or len(set(data['registered'])) != len(data['registered'])
            or data['active'] not in data['registered']):
        raise Hold('registry membership/active runtime invalid; registry owner must reconcile')
    for value in data['registered']:
        path = literal(Path(value))
        if str(path) != value or not path.is_dir():
            raise Hold(f'nonliteral registered runtime: {value}')
    return dict(data, digest=hashlib.sha256(raw).hexdigest())


@contextmanager
def read_database(path: Path):
    before = identity(path)
    if not stat.S_ISREG(before[2]) or before[4] != 1 or before[3] != os.getuid():
        raise Hold(f'unknown database custody: {path}')
    for suffix in ('-wal', '-shm', '-journal'):
        candidate = Path(str(path) + suffix)
        literal(candidate, missing=True)
        if candidate.exists() and not stat.S_ISREG(candidate.lstat().st_mode):
            raise Hold(f'invalid SQLite companion: {candidate}')
    conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)
    try:
        deadline, steps = time.monotonic() + 120, 0
        def progress():
            nonlocal steps
            steps += 10000
            return int(steps > 50000000 or time.monotonic() > deadline)
        conn.set_progress_handler(progress, 10000)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        conn.execute('PRAGMA foreign_keys=ON')
        if Path(conn.execute('PRAGMA database_list').fetchone()[2]) != path:
            raise Hold(f'SQLite source mismatch: {path}')
        conn.execute('BEGIN')
        if [r[0] for r in conn.execute('PRAGMA integrity_check')] != ['ok'] or conn.execute('PRAGMA foreign_key_check').fetchone():
            raise Hold(f'integrity/foreign keys refused: {path}')
        yield conn
        if identity(path) != before:
            raise Hold(f'database identity moved: {path}')
    finally:
        conn.close()


def inspect_org_owners(conn: sqlite3.Connection, layout: str, org: str) -> None:
    """One migration classification at wrapper and every inner writer boundary.

    Full schema/data/release readers run first. This is only the durable half
    of current exclusion; process/preparation/cooperative host checks remain
    required. No audit/journal is cleared or historical closure reconstructed.
    """
    from runtime.infrastructure.workflow_schema import _validate_coding_source_idle
    try:
        _validate_coding_source_idle(conn, expected_org_slug=org, layout=layout)
    except ValueError as exc:
        # Preserve current owner attribution in private diagnostics; unrelated
        # schema/data errors retain their original exception and refusal.
        message = str(exc)
        prefixes = ('workflow_coding_source_owner_unresolved: ', 'workflow_submission_source_owner_active: ')
        prefix = next((value for value in prefixes if message.startswith(value)), None)
        if prefix is None:
            raise
        table, _, reference = message[len(prefix):].split(';', 1)[0].partition(':')
        if re.fullmatch(r'[a-z_]+', table) is None:
            raise
        raise Hold('current owner prevents maintenance; establish complete present exclusion',
                   category='current_owner_active',
                   details={'org': org, 'table': table, 'owner': {'id': reference} if reference else {},
                            'original_cause': message}) from exc


def observe_maintenance(home: Path, selected: Path) -> dict:
    from runtime.runtime import RuntimeDir
    from runtime.orchestrator.team_move_startup import require_runtime_reader, diagnostic_move_orgs
    from scripts.migrate_workflow_submission_schema import resolve_org_database
    from runtime.infrastructure.workflow_schema import _read_workflow_schema
    from runtime.infrastructure.workflow_coding_schema import _validate_coding_dependencies
    from runtime.identities.schema import validate_names
    frozen = registry(home)
    if str(selected) != frozen['active']:
        raise Hold('selected runtime must be the literal active registered runtime; no switching')
    result = {'registry': frozen, 'orgs': [], 'directories': [], 'runtimes': [], 'excluded': []}
    for name in frozen['registered']:
        root = Path(name)
        runtime = RuntimeDir.load(root)
        result['runtimes'].append((str(root), identity(root), digest_file(root / 'happyranch.yaml')))
        diagnostics = require_runtime_reader(runtime) | diagnostic_move_orgs(runtime)
        if diagnostics:
            raise Hold(f'{root}: diagnostic orgs {diagnostics}; original move/org owner must reconcile')
        directory = literal(root / 'orgs')
        entries = sorted(directory.iterdir())
        if len(entries) > 1024:
            raise Hold(f'{directory}: complete org census budget exhausted')
        result['directories'].append((str(directory), identity(directory), tuple(p.name for p in entries)))
        for org in entries:
            literal(org)
            if org.name in ('_pending', '_archive') and org.is_dir():
                # Account only for the untouched boundary. Do not enumerate,
                # read or copy members, archives or original OP evidence.
                result['excluded'].append((str(org), identity(org)))
                continue
            if not org.is_dir() or not re.fullmatch(r'[a-z0-9-]{1,40}', org.name):
                # Pending/archive entries cannot silently disappear from scope.
                raise Hold(f'{org}: unaccounted/partial org entry; original registry/org owner must reconcile')
            literal(org / 'org' / 'teams.yaml')
            path = resolve_org_database(root, org.name)
            with read_database(path) as conn:
                layout, coding = _read_workflow_schema(conn, expected_org_slug=org.name)
                _validate_coding_dependencies(conn)
                validate_names(conn, org_slug=org.name)
                inspect_org_owners(conn, layout, org.name)
            result['orgs'].append({'runtime': str(root), 'org': org.name, 'path': str(path),
                                   'identity': identity(path), 'layout': layout, 'coding': coding is not None})
    return result


def digest_file(path: Path) -> str:
    return hashlib.sha256(read_file(path)).hexdigest()


def checked_processes() -> list:
    from runtime.platform.process_census import default_process_reader, _checked_observation
    reader = default_process_reader()
    with _checked_observation():
        rows = reader.read_all()
        for row in rows:
            if row.start_identity is None or row.ppid is None:
                raise Hold(f'process {row.pid}: unavailable identity/ancestry; process observer owner must reconcile')
    return rows
