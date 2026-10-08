"""Real G operator commands on disposable, socket-free offline orgs."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import select
from pathlib import Path

import pytest

from runtime.infrastructure.database import Database
from runtime.infrastructure import workflow_schema as schema
from runtime.runtime import RuntimeDir

SCRIPT = Path(__file__).parents[1] / 'scripts/migrate_workflow_submission_schema.py'


def _org(tmp_path: Path, layout: str) -> tuple[Path, Path, dict[str, str]]:
    runtime = RuntimeDir.init(tmp_path / 'runtime')
    root = runtime.orgs_dir / 'alpha'
    (root / 'org/agents').mkdir(parents=True)
    (root / 'org/teams.yaml').write_text('teams: {}\n')
    db = Database(root / 'happyranch.db')
    if layout == 'G':
        schema.initialize_complete_org_schema(db, expected_org_slug='alpha')
    else:
        schema.install_or_recover(db, expected_org_slug='alpha')
        if layout == 'E':
            with db.workflow_schema_transaction() as conn:
                schema.migrate_draft_schema(conn, expected_org_slug='alpha')
    db.close()
    home = tmp_path / 'daemon'
    home.mkdir()
    env = dict(os.environ, HAPPYRANCH_DAEMON_HOME=str(home))
    path = root / 'happyranch.db'
    with sqlite3.connect(path) as conn:
        conn.execute('PRAGMA journal_mode=DELETE')
    conn.close()
    path.chmod(0o640)
    return runtime.root, path, env


def _run(runtime: Path, env: dict[str, str], *extra: str, slug: str = 'alpha') -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), '--runtime-root', str(runtime), '--org', slug, *extra],
                          env=env, capture_output=True, text=True, timeout=15)


def _snapshot(root: Path) -> dict:
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mode & 0o777)
            for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('target', ['missing-db', 'generic', 'symlink', 'hardlink', 'bad-marker', 'traversal', 'relative-root'])
def test_g_script_target_identity_and_missing_database_refuse(tmp_path: Path, target: str) -> None:
    root, path, env = _org(tmp_path, 'F')
    slug = 'alpha'
    if target == 'missing-db':
        path.unlink()
    elif target == 'generic':
        path.unlink()
        Database(path).close()
        with sqlite3.connect(path) as conn:
            conn.execute("PRAGMA journal_mode=DELETE")
        conn.close()
    elif target in ('symlink', 'hardlink'):
        other = root / 'runtime-audit.db'
        other.write_bytes(path.read_bytes())
        path.unlink()
        if target == 'symlink':
            path.symlink_to(other)
        else:
            os.link(other, path)
    elif target == 'bad-marker':
        (root / 'happyranch.yaml').write_text('schema_version: 1\n')
    elif target == 'traversal':
        slug = '../alpha'
    before = _snapshot(root)
    for options in (('--check',), ()):
        result = _run(Path('relative') if target == 'relative-root' else root, env, *options, slug=slug)
        assert result.returncode == 1 and 'refused:' in result.stderr
        assert _snapshot(root) == before


@pytest.mark.parametrize('layout', ['F', 'E', 'G'])
def test_g_script_check_and_populated_noop_are_read_only(tmp_path: Path, layout: str) -> None:
    root, path, env = _org(tmp_path, layout)
    from runtime.models import TaskRecord
    db = Database(path)
    db.insert_task(TaskRecord(id='TASK-777', brief='durable legacy task', assigned_agent='maker', team='engineering'))
    db.close()
    with sqlite3.connect(path) as conn:
        conn.execute('PRAGMA journal_mode=DELETE')
    conn.close()
    before = _snapshot(root)
    check = _run(root, env, '--check')
    assert check.returncode == (0 if layout == 'G' else 3), check.stderr
    assert _snapshot(root) == before
    migrated = _run(root, env)
    assert migrated.returncode == 0 and 'layout G' in migrated.stdout, migrated.stderr
    with sqlite3.connect(path) as observer:
        assert observer.execute('SELECT brief FROM tasks WHERE id=?', ('TASK-777',)).fetchone() == ('durable legacy task',)
        assert observer.execute('PRAGMA foreign_key_check').fetchall() == []
    assert path.stat().st_mode & 0o777 == 0o640
    after = _snapshot(root)
    for options in (('--check',), ()):
        replay = _run(root, env, *options)
        assert replay.returncode == 0 and 'ready:' in replay.stdout and 'layout G' in replay.stdout, replay.stderr
        assert _snapshot(root) == after


@pytest.mark.parametrize('unsafe', ['live-pid', 'malformed-pid', 'port', 'malformed-registry', 'lease'])
def test_g_script_offline_quiescence_and_ownership_refuse_unsafe_work(tmp_path: Path, unsafe: str) -> None:
    root, path, env = _org(tmp_path, 'E')
    home = Path(env['HAPPYRANCH_DAEMON_HOME'])
    if unsafe == 'live-pid':
        (home / 'daemon.pid').write_text(str(os.getpid()))
    elif unsafe == 'malformed-pid':
        (home / 'daemon.pid').write_text('unknown')
    elif unsafe == 'port':
        (home / 'daemon.port').write_text('12345')
    elif unsafe == 'malformed-registry':
        (home / 'runtimes.yaml').write_text('registered: unknown\n')
    else:
        with sqlite3.connect(path) as conn:
            conn.execute('INSERT INTO workflow_publication_leases VALUES (?,?,?)', ('namespace', 'owner', os.getpid()))
        conn.close()
    before = _snapshot(root)
    result = _run(root, env)
    assert result.returncode == 1 and 'refused:' in result.stderr, result.stdout
    assert _snapshot(root) == before


_INTERRUPTION_DRIVER = '''
import importlib.util, os, sqlite3, sys
spec = importlib.util.spec_from_file_location('owned_g_script', sys.argv[1])
owner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(owner)
base = owner._MigrationConnection
boundary, action = sys.argv[3:5]
class Observed(base):
    changed = False
    hit = False
    def interrupt(self):
        if self.hit:
            return
        self.hit = True
        print('BOUNDARY', flush=True)
        if action == 'barrier':
            assert sys.stdin.readline().strip() == 'release'
        elif action == 'rollback':
            raise sqlite3.OperationalError('owned interruption after actual statement')
        elif action == 'exit':
            os._exit(91)
        elif action == 'sigkill':
            os.kill(os.getpid(), 9)
    def execute(self, sql, parameters=(), /):
        result = super().execute(sql, parameters)
        if 'DROP TABLE workflow_events' in sql:
            self.changed = True
        if boundary not in ('before-commit', 'after-commit') and boundary in sql:
            if boundary != 'PRAGMA integrity_check' or self.changed:
                self.interrupt()
        return result
    def commit(self):
        if self.changed and boundary == 'before-commit':
            self.interrupt()
        super().commit()
        if self.changed and boundary == 'after-commit':
            self.interrupt()
owner._MigrationConnection = Observed
status = owner.main(['--runtime-root', sys.argv[2], '--org', 'alpha'])
raise SystemExit(status)
'''


def _interrupted(root: Path, env: dict[str, str], boundary: str, action: str) -> subprocess.Popen[str]:
    return subprocess.Popen([sys.executable, '-c', _INTERRUPTION_DRIVER, str(SCRIPT), str(root), boundary, action],
                            env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _arrival(proc: subprocess.Popen[str]) -> None:
    assert proc.stdout is not None
    assert select.select([proc.stdout], [], [], 10)[0], 'actual migration boundary never arrived'
    assert proc.stdout.readline().strip() == 'BOUNDARY'


def _finish(proc: subprocess.Popen[str], *, release: bool = False) -> tuple[str, str]:
    try:
        return proc.communicate('release\n' if release else None, timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()


@pytest.mark.parametrize('layout', ['F', 'E'])
def test_g_script_old_or_new_visibility_and_atomic_target(tmp_path: Path, layout: str) -> None:
    root, path, env = _org(tmp_path, layout)
    proc = _interrupted(root, env, 'DROP TABLE workflow_events', 'barrier')
    try:
        _arrival(proc)
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1) as observer:
            assert schema.validate_workflow_schema(observer, expected_org_slug='alpha') == layout
            assert observer.execute("SELECT name FROM sqlite_schema WHERE name='workflow_submission_schema_versions'").fetchall() == []
        out, err = _finish(proc, release=True)
        assert proc.returncode == 0 and 'migrated:' in out, err
    finally:
        if proc.poll() is None:
            _finish(proc, release=True)
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as observer:
        assert schema.validate_workflow_schema(observer, expected_org_slug='alpha') == 'G'
        assert observer.execute('PRAGMA foreign_key_check').fetchall() == []


@pytest.mark.parametrize('layout', ['F', 'E'])
@pytest.mark.parametrize('journal', ['DELETE', 'WAL'])
@pytest.mark.parametrize('action', ['rollback', 'exit', 'sigkill'])
@pytest.mark.parametrize('boundary', [
    'CREATE TEMP TABLE submission_stage', 'CREATE TEMP TABLE event_stage',
    'DROP TABLE workflow_events', 'DROP TABLE workflow_submissions',
    'CREATE TABLE workflow_submissions', 'CREATE TABLE workflow_events',
    'CREATE INDEX workflow_events_instance_idx',
    'CREATE TABLE workflow_submission_schema_versions',
    'CREATE TABLE workflow_submission_operations',
    'CREATE TABLE workflow_submission_result_links',
    'CREATE INDEX workflow_submission_operations_source_idx',
    'CREATE INDEX workflow_submission_result_links_result_idx',
    'INSERT INTO workflow_submission_schema_versions',
    'PRAGMA integrity_check', 'before-commit', 'after-commit',
])
def test_g_script_every_interruption_recovers_complete_old_or_new(tmp_path: Path, layout: str, journal: str,
                                                                 action: str, boundary: str) -> None:
    root, path, env = _org(tmp_path, layout)
    with sqlite3.connect(path) as conn:
        conn.execute('PRAGMA journal_mode=' + journal)
    conn.close()
    proc = _interrupted(root, env, boundary, action)
    out, err = _finish(proc)
    assert 'BOUNDARY' in out, err
    assert proc.returncode != 0
    # Real SQLite recovery happens through a fresh current reader. Never delete
    # hot journals/WAL/SHM to obtain an equality assertion.
    with sqlite3.connect(path) as observer:
        expected = 'G' if boundary == 'after-commit' else layout
        # Recover through the real writable reader before the independent
        # read-only full-layout observer; hot journals are never removed.
        assert observer.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
        from tests.daemon.test_workflow_activation_routes import _assert_activation_org_layout
        _assert_activation_org_layout(path, expected, 'actual interrupted source/target recovery')
        assert schema.validate_workflow_schema(observer, expected_org_slug='alpha') == expected
        assert observer.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
        assert observer.execute('PRAGMA foreign_key_check').fetchall() == []
    observer.close()


@pytest.mark.parametrize('layout', ['F', 'E'])
def test_g_script_two_writers_and_contention_are_bounded(tmp_path: Path, layout: str) -> None:
    root, path, env = _org(tmp_path, layout)
    proc = _interrupted(root, env, 'BEGIN IMMEDIATE', 'barrier')
    try:
        _arrival(proc)
        refused = _run(root, env)
        assert refused.returncode == 1 and 'locked' in refused.stderr, refused.stdout
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as observer:
            assert schema.validate_workflow_schema(observer, expected_org_slug='alpha') == layout
        out, err = _finish(proc, release=True)
        assert proc.returncode == 0 and 'migrated:' in out, err
        replay = _run(root, env)
        assert replay.returncode == 0 and 'ready:' in replay.stdout, replay.stderr
    finally:
        if proc.poll() is None:
            _finish(proc, release=True)


@pytest.mark.parametrize('layout', ['F', 'E'])
def test_g_script_wal_preserves_committed_source_and_reader_snapshots(tmp_path: Path, layout: str) -> None:
    root, path, env = _org(tmp_path, layout)
    reader = sqlite3.connect(path)
    reader.execute('PRAGMA journal_mode=WAL')
    reader.execute('BEGIN')
    assert schema.validate_workflow_schema(reader, expected_org_slug='alpha') == layout
    try:
        migrated = _run(root, env)
        assert migrated.returncode == 0 and 'migrated:' in migrated.stdout, migrated.stderr
        assert schema.validate_workflow_schema(reader, expected_org_slug='alpha') == layout
        with sqlite3.connect(path) as observer:
            assert schema.validate_workflow_schema(observer, expected_org_slug='alpha') == 'G'
        reader.rollback()
        assert schema.validate_workflow_schema(reader, expected_org_slug='alpha') == 'G'
    finally:
        reader.close()


@pytest.mark.parametrize('source_layout', ['v0', 'v1'])
@pytest.mark.parametrize('workflow_layout', ['F', 'E'])
def test_g_script_source_pinned_v0_v1_initializer_preserves_legacy_rows(tmp_path: Path, source_layout: str, workflow_layout: str) -> None:
    import shutil
    from runtime.daemon.org_state import OrgState
    from runtime.config import Settings
    from tests.workflows.test_u0_migration_recovery import _execute_historical_v0_database, _execute_historical_v1_runtime, _legacy_snapshot
    runtime = RuntimeDir.init(tmp_path / 'runtime')
    root = runtime.orgs_dir / 'alpha'
    (root / 'org/agents').mkdir(parents=True)
    (root / 'org/teams.yaml').write_text('teams: {}\n')
    historical = tmp_path / 'historical'; historical.mkdir()
    if source_layout == 'v1': _execute_historical_v1_runtime(historical)
    old_path = historical / ('opc.db' if source_layout == 'v1' else 'v0.db')
    _execute_historical_v0_database(old_path)
    original = _snapshot(historical)
    path = root / 'happyranch.db'; shutil.copy2(old_path, path)
    db = Database(path)
    schema.install_or_recover(db, expected_org_slug='alpha')
    db.close()
    before = _legacy_snapshot(path)
    env = dict(os.environ, HAPPYRANCH_DAEMON_HOME=str(tmp_path / 'daemon'))
    Path(env['HAPPYRANCH_DAEMON_HOME']).mkdir()
    if workflow_layout == 'E':
        from tests.test_workflow_draft_migration_script import _run as run_draft
        migrated = run_draft(runtime.root)
        assert migrated.returncode == 0 and 'layout E' in migrated.stdout, migrated.stderr
    result = _run(runtime.root, env)
    assert result.returncode == 0 and 'layout G' in result.stdout, result.stderr
    assert _legacy_snapshot(path) == before and _snapshot(historical) == original
    for _ in range(2):
        owner = OrgState.load(slug='alpha', root=root, settings=Settings())
        try:
            assert schema.validate_workflow_schema(owner.db._conn, expected_org_slug='alpha') == 'G'
        finally:
            owner.close()
        cold = _legacy_snapshot(path)
        # Ordinary cold startup initializes current settings and appends its
        # real audit rows. Every original row and SQL definition is retained;
        # the operator itself above still requires exact whole-snapshot equality.
        assert cold.keys() == before.keys()
        for table, (sql, rows) in before.items():
            observed_sql, observed_rows = cold[table]
            assert observed_sql == sql, table
            assert all(row in observed_rows for row in rows), table
            if table not in ('audit_log', 'org_settings'):
                assert observed_rows == rows, table
        assert _snapshot(historical) == original
