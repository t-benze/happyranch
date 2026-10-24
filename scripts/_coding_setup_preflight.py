"""Read-only host maintenance composition. Never constructs Database/OrgState.

This is a cooperative observation, not a maintenance reservation. The original
schema/history owners remain authoritative; missing native closure stays unknown.
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
from types import SimpleNamespace

import yaml


class Hold(ValueError):
    """An observation is unavailable or an original owner still owns work."""


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


# Ordinary producer admission predicates from the original I/J idle owner.
# They do not query layout-dependent draft/revision tables on F.
_ORDINARY = (
    ('workflow_publication_leases', '1'),
    ('workflow_profile_leases', '1'),
    ('workflow_publication_journals', "state NOT IN ('cache_installed','aborted')"),
    ('workflow_profile_operations', "state NOT IN ('published','aborted')"),
    ('workflow_recovery_claims', "state='claimed'"),
    ('workflow_authority_pointers', "state!='ready'"),
    ('workflow_dispatch_operations', "state NOT IN ('cancelled','completed')"),
    ('workflow_dispatch_outbox', "state NOT IN ('cancelled','completed')"),
    ('workflow_request_task_bridges', "state NOT IN ('cancelled','completed')"),
    ('tasks', "status NOT IN ('completed','failed','cancelled','superseded') OR active_chain IS NOT NULL OR active_fanout IS NOT NULL OR blocked_on_job_ids IS NOT NULL OR executor_pid IS NOT NULL"),
    ('task_completion_recoveries', "state!='callback_consumed' OR settled_at IS NULL"),
    ('jobs', "status NOT IN ('completed','failed','rejected','cancelled')"),
    ('thread_invocations', "status NOT IN ('consumed','declined','timeout','failed')"),
    ('dreams', "status NOT IN ('completed','failed','timeout','skipped')"),
    ('work_hours', "status NOT IN ('completed','failed','timeout','skipped')"),
    ('schedules', "active=1 OR session_id IS NOT NULL OR status NOT IN ('fired','paused','cancelled','expired','failed','timeout')"),
    ('remote_job_attempts', "state!='terminal'"),
    ('remote_runner_workspaces', "active_attempt_id IS NOT NULL OR state IN ('leased','recreate_pending','uncertain')"),
)


def owner_hold(org: str, table: str, row) -> None:
    keys = ('id', 'task_id', 'root_task_id', 'record_id', 'source_id', 'session_id',
            'current_session_id', 'preparation_id', 'operation_id', 'namespace', 'writer_id', 'invocation_id')
    owner = ', '.join(f'{k}={row[k]}' for k in keys if k in row.keys() and row[k] is not None)
    raise Hold(f'{org}: {table} {owner}; original owner must reconcile journal/native/outer closure; no stop attempted')


def inspect_org_owners(conn: sqlite3.Connection, layout: str, org: str) -> None:
    from runtime.infrastructure.workflow_schema import (
        _validate_coding_source_idle, _validate_submission_source_ownership,
    )
    from runtime.infrastructure.producer_admission import ProducerAdmissionStore, NativeWriterStore
    from runtime.infrastructure.thread_admission import ThreadAdmissionStore
    from runtime.infrastructure.task_pause_controls import validate_pause_schema, decode_journal
    from runtime.infrastructure.team_move_store import validate_team_move_schema
    from runtime.workflows.review_dispatch import validate_review_dispatch_storage

    for table, predicate in _ORDINARY:
        row = conn.execute(f'SELECT * FROM {table} WHERE {predicate} LIMIT 1').fetchone()
        if row is not None:
            owner_hold(org, table, row)
    if layout != 'F':
        row = conn.execute("SELECT * FROM workflow_draft_dispatch_intents WHERE state NOT IN ('cancelled','failed','completed') LIMIT 1").fetchone()
        if row is not None:
            owner_hold(org, 'workflow_draft_dispatch_intents', row)
    _validate_submission_source_ownership(conn, layout)
    if validate_pause_schema(conn, org_slug=org, allow_absent=True):
        for row in conn.execute('SELECT * FROM task_pause_controls'):
            if row['held'] or row['release_pending_generation'] is not None or decode_journal(row['admission_journal_json'])['entries']:
                owner_hold(org, 'task_pause_controls', row)
    if validate_team_move_schema(conn, org_slug=org):
        row = conn.execute('SELECT * FROM team_move_operations WHERE active_slot IS NOT NULL LIMIT 1').fetchone()
        if row is not None:
            owner_hold(org, 'team_move_operations', row)
    adapter = SimpleNamespace(_conn=conn)
    for state in ProducerAdmissionStore(adapter, org).history_uncommitted().values():
        if state.unknown or not state.producer_settled or any(not a.settled or a.possible_launch and not a.quiescent for a in state.attempts):
            owner_hold(org, 'producer_move_lifecycle', state.model_dump())
    for state in NativeWriterStore(adapter, org).history_uncommitted().values():
        if state.state != 'settled':
            owner_hold(org, 'team_move_native_writer', state.model_dump())
    store = ThreadAdmissionStore(adapter, org)
    scopes = list(conn.execute("SELECT DISTINCT task_id FROM audit_log WHERE action='thread_move_lifecycle'"))
    for scope in scopes:
        rows = list(conn.execute("SELECT id,agent,payload FROM audit_log WHERE task_id=? AND action='thread_move_lifecycle' ORDER BY id", (scope[0],)))
        decoded = list(store._decode_states(scope[0], rows, check=lambda: None))
        ids = {state.invocation_id for _, state in decoded}
        for invocation_id in ids:
            row = conn.execute('SELECT * FROM thread_invocations WHERE id=?', (invocation_id,)).fetchone()
            if row is None or row['thread_id'] != scope[0]:
                raise Hold(f'{org}: thread invocation {invocation_id} original owner missing')
            states = store._replay_bounded(SimpleNamespace(**dict(row)), rows, check=lambda: None)
            state = states[-1][1]
            if state.unknown or not state.producer_settled or any(not a.settled or a.possible_launch and not a.quiescent for a in state.attempts):
                owner_hold(org, 'thread_move_lifecycle', state.model_dump())

    # Refusal/started audit is NOT a terminal or outer-settlement certificate.
    # There is no supported historical ordinary-native closure reader for this
    # family. Preserve the exact original owner instead of guessing from labels.
    for row in conn.execute("SELECT id,task_id,agent,payload FROM audit_log WHERE action='task_native_launch_boundary' ORDER BY id"):
        payload = json.loads(row['payload'])
        if payload.get('disposition') == 'unclosed_preparation' or payload.get('kind') == 'started':
            raise Hold(f'{org}: {row["task_id"]} SID={payload.get("session_id")} invocation={payload.get("invocation_id")} audit={row["id"]}: '
                       'original native preparation/outer closure observation unavailable; original task/native owner must reconcile; no terminal/PID inference')

    if layout in ('I', 'J'):
        _validate_coding_source_idle(conn, expected_org_slug=org)
        return
    # Full document readers already validated draft/handoff/source history.
    # Validate F5 terminal projections with their original storage reader.
    graph = [{r[0] for r in conn.execute(f'SELECT request_id FROM {table}')} for table in
             ('workflow_dispatch_operations', 'workflow_dispatch_outbox', 'workflow_request_task_bridges')]
    if graph[0] != graph[1] or graph[0] != graph[2]:
        raise Hold(f'{org}: incomplete original F5 graph')
    for request in graph[0]:
        validate_review_dispatch_storage(conn, request_id=request)
    if layout != 'F':
        # The original complete data reader authenticates the event bytes. A
        # launched draft still needs its explicit host-quiescent final event.
        from runtime.infrastructure.workflow_schema import _stored_json
        for intent in conn.execute('SELECT * FROM workflow_draft_dispatch_intents'):
            if intent['host_launch_started']:
                event = conn.execute('SELECT * FROM workflow_draft_dispatch_events WHERE intent_id=? ORDER BY event_seq DESC LIMIT 1', (intent['id'],)).fetchone()
                if event is None or _stored_json(event['event_bytes'], event['event_digest']).get('terminal_evidence') != {'host_quiescent': True}:
                    owner_hold(org, 'workflow_draft_dispatch_intents', intent)
    from runtime.workflows.authority import WorkflowAuthorityCoordinator, validate_authority_snapshot
    from runtime.infrastructure.workflow_schema import _stored_json
    # _journal is an existing SELECT-only namespace-bound reader. Its receiver
    # needs only the literal namespace; no coordinator/Database is initialized.
    for row in conn.execute('SELECT * FROM workflow_publication_journals'):
        receiver = SimpleNamespace(namespace=row['namespace'])
        journal = WorkflowAuthorityCoordinator._journal(receiver, conn, row['id'])
        validate_authority_snapshot(_stored_json(journal['snapshot_bytes'], journal['snapshot_digest']))
        if any(type(journal[k]) is not str or not journal[k] for k in ('publisher', 'publisher_invocation', 'recovery_owner')):
            owner_hold(org, 'workflow_publication_journals', row)
    for pointer in conn.execute('SELECT * FROM workflow_authority_pointers'):
        generation, jid, value, state, fence = WorkflowAuthorityCoordinator._pointer(conn, pointer['namespace'])
        if generation:
            journal = WorkflowAuthorityCoordinator._journal(SimpleNamespace(namespace=pointer['namespace']), conn, jid)
            if (state != 'ready' or journal['state'] != 'cache_installed' or journal['generation'] != generation
                    or journal['snapshot_digest'] != value or journal['profile_fence'] != fence):
                owner_hold(org, 'workflow_authority_pointers', pointer)
    # No generic earlier-layout API closes cross-org profile/recovery terminal
    # ownership. Preserve those owners instead of duplicating an I-only reader.
    for table in ('workflow_profile_operations', 'workflow_recovery_claims', 'task_completion_recoveries'):
        row = conn.execute(f'SELECT * FROM {table} LIMIT 1').fetchone()
        if row is not None:
            owner_hold(org, table, row)


def observe_maintenance(home: Path, selected: Path) -> dict:
    from runtime.runtime import RuntimeDir
    from runtime.orchestrator.team_move_startup import require_runtime_reader, diagnostic_move_orgs
    from scripts.migrate_workflow_submission_schema import resolve_org_database
    from runtime.infrastructure.workflow_schema import validate_workflow_schema, _validate_release_database
    frozen = registry(home)
    if str(selected) != frozen['active']:
        raise Hold('selected runtime must be the literal active registered runtime; no switching')
    result = {'registry': frozen, 'orgs': [], 'directories': [], 'runtimes': []}
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
            if not org.is_dir() or not re.fullmatch(r'[a-z0-9-]{1,40}', org.name):
                # Pending/archive entries cannot silently disappear from scope.
                raise Hold(f'{org}: unaccounted/partial org entry; original registry/org owner must reconcile')
            literal(org / 'org' / 'teams.yaml')
            path = resolve_org_database(root, org.name)
            with read_database(path) as conn:
                layout = validate_workflow_schema(conn, expected_org_slug=org.name)
                _validate_release_database(conn, layout, expected_org_slug=org.name)
                inspect_org_owners(conn, layout, org.name)
            result['orgs'].append({'runtime': str(root), 'org': org.name, 'path': str(path),
                                   'identity': identity(path), 'layout': layout})
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
