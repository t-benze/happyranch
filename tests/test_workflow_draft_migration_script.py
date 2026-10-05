"""Actual operator script subprocesses against disposable canonical orgs."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from runtime.infrastructure.database import Database
from runtime.infrastructure.workflow_schema import install_or_recover
from runtime.runtime import RuntimeDir

SCRIPT = Path(__file__).parents[1] / 'scripts/migrate_workflow_draft_schema.py'


def _org(tmp_path: Path) -> tuple[Path, Path]:
    runtime = RuntimeDir.init(tmp_path / 'runtime')
    root = runtime.orgs_dir / 'alpha'
    (root / 'org' / 'agents').mkdir(parents=True)
    (root / 'org' / 'teams.yaml').write_text('teams: {}\n')
    db = Database(root / 'happyranch.db')
    install_or_recover(db, expected_org_slug='alpha')
    db.close()
    with sqlite3.connect(root / "happyranch.db") as conn:
        conn.execute("PRAGMA journal_mode=DELETE")
    conn.close()
    os.chmod(root / 'happyranch.db', 0o640)
    return runtime.root, root / 'happyranch.db'


def _run(runtime: Path, *extra: str, slug: str = 'alpha') -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), '--runtime-root', str(runtime), '--org', slug, *extra],
                          text=True, capture_output=True, timeout=15)


def _snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mode & 0o777)
            for p in root.rglob('*') if p.is_file()}


def _logical(path: Path) -> tuple:
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as conn:
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name")]
        return tuple((table, tuple(conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid'))) for table in tables)


def test_actual_script_check_then_migrate_replay_preserves_foundation(tmp_path: Path) -> None:
    runtime, path = _org(tmp_path)
    before, rows = _snapshot(runtime), _logical(path)
    check = _run(runtime, '--check')
    assert check.returncode == 3 and 'migration-needed' in check.stdout
    assert '--org alpha' in check.stdout
    assert _snapshot(runtime) == before
    migrated = _run(runtime)
    assert migrated.returncode == 0 and 'migrated:' in migrated.stdout, migrated.stderr
    after = dict(_logical(path))
    assert all(after[table] == records for table, records in rows)
    assert path.stat().st_mode & 0o777 == 0o640
    committed = _snapshot(runtime)
    for options in (('--check',), ()):
        replay = _run(runtime, *options)
        assert replay.returncode == 0 and 'ready:' in replay.stdout
        assert _snapshot(runtime) == committed


@pytest.mark.parametrize('slug', ['../alpha', '_archive', 'ALPHA', 'alpha\n', 'missing', 'runtime-audit'])
def test_script_refuses_bad_or_missing_org_without_creating_files(tmp_path: Path, slug: str) -> None:
    runtime, _ = _org(tmp_path)
    before = _snapshot(runtime)
    for options in (('--check',), ()):
        refused = _run(runtime, *options, slug=slug)
        assert refused.returncode == 1 and 'refused:' in refused.stderr
        assert _snapshot(runtime) == before


@pytest.mark.parametrize('target', ['missing-db', 'generic', 'symlink', 'bad-marker'])
def test_script_rejects_nonorg_or_noncanonical_target(tmp_path: Path, target: str) -> None:
    runtime, path = _org(tmp_path)
    if target == 'missing-db':
        path.unlink()
    elif target == 'generic':
        path.unlink()
        Database(path).close()
        with sqlite3.connect(path) as conn:
            conn.execute("PRAGMA journal_mode=DELETE")
        conn.close()
    elif target == 'symlink':
        other = runtime / 'runtime-audit.db'
        Database(other).close()
        path.unlink()
        path.symlink_to(other)
    else:
        (runtime / 'happyranch.yaml').write_text('schema_version: 1\n')
    before = _snapshot(runtime)
    result = _run(runtime, '--check')
    assert result.returncode == 1
    assert _snapshot(runtime) == before


@pytest.mark.parametrize('corruption', ['partial', 'history', 'source'])
def test_script_corrupt_foundation_refuses_with_exact_file_preservation(tmp_path: Path, corruption: str) -> None:
    runtime, path = _org(tmp_path)
    with sqlite3.connect(path) as conn:
        if corruption == 'partial':
            conn.execute('CREATE TABLE workflow_draft_adapter_versions (version INTEGER PRIMARY KEY CHECK(version=1))')
        elif corruption == 'history':
            conn.execute("UPDATE workflow_cutover_events SET event_digest='wrong'")
        else:
            conn.execute("INSERT INTO workflow_template_drafts VALUES ('bad','org/alpha/team/x','x',?, 'wrong','x','x','x','{}','now')", (b'{}',))
    conn.close()
    before = _snapshot(runtime)
    result = _run(runtime)
    assert result.returncode == 1
    assert _snapshot(runtime) == before


def test_script_writer_contention_is_bounded_and_leaves_no_extension(tmp_path: Path) -> None:
    runtime, path = _org(tmp_path)
    before = _snapshot(runtime)
    with sqlite3.connect(path) as writer:
        writer.execute('BEGIN IMMEDIATE')
        result = _run(runtime)
        assert result.returncode == 1 and 'locked' in result.stderr
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as reader:
            assert reader.execute("SELECT COUNT(*) FROM sqlite_schema WHERE name LIKE 'workflow_draft_%'").fetchone()[0] == 0
    assert _snapshot(runtime) == before


@pytest.mark.parametrize('boundary', ['ddl', 'commit'])
@pytest.mark.parametrize('repetition', range(5))
def test_actual_script_interruption_is_atomic_to_independent_reader(tmp_path: Path, boundary: str, repetition: int) -> None:
    """YES test-only seam: test-process DDL/commit observer, no production hook."""
    import select
    runtime, path = _org(tmp_path)
    before = _snapshot(runtime)
    driver = '''import sys, runpy, os, sqlite3
sys.path.insert(0, sys.argv[1])
import runtime.infrastructure.workflow_schema as schema
boundary = sys.argv[2]
def pause():
    print('reserved', flush=True)
    sys.stdin.readline()
    os._exit(86)
if boundary == 'ddl':
    original = schema._execute_ddl
    def execute(conn, ddl):
        original(conn, ddl)
        if ddl == schema.CANONICAL_WORKFLOW_DRAFT_DDL:
            pause()
    schema._execute_ddl = execute
else:
    connect = sqlite3.connect
    class Observed:
        def __init__(self, conn): self.conn = conn
        def __getattr__(self, key): return getattr(self.conn, key)
        def commit(self): pause()
    sqlite3.connect = lambda *args, **kwargs: Observed(connect(*args, **kwargs))
script = sys.argv[3]
sys.argv = [script, '--runtime-root', sys.argv[4], '--org', 'alpha']
runpy.run_path(script, run_name='__main__')
'''
    child = subprocess.Popen([sys.executable, '-c', driver, str(SCRIPT.parents[1]), boundary, str(SCRIPT), str(runtime)],
                             text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert select.select([child.stdout], [], [], 10)[0], 'script did not reach actual transaction boundary'
        assert child.stdout.readline().strip() == 'reserved'
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as reader:
            assert reader.execute("SELECT COUNT(*) FROM sqlite_schema WHERE name LIKE 'workflow_draft_%'").fetchone()[0] == 0
        reader.close()
        out, err = child.communicate('exit\n', timeout=10)
        assert child.returncode == 86, (out, err)
        # A real SQLite reader performs journal recovery, not a fixture rollback.
        with sqlite3.connect(path) as reader:
            assert reader.execute("SELECT COUNT(*) FROM sqlite_schema WHERE name LIKE 'workflow_draft_%'").fetchone()[0] == 0
        reader.close()
        recovered = _snapshot(runtime)
        assert all(recovered[name] == value for name, value in before.items())
        # A killed DELETE-journal writer can retain a zero-header journal;
        # it is not installed schema or a changed original file.
        extra = set(recovered) - set(before)
        assert extra <= {'orgs/alpha/happyranch.db-journal'}
        if extra:
            assert recovered[next(iter(extra))][0][:8] == bytes(8)
        result = _run(runtime)
        assert result.returncode == 0, result.stderr
        with sqlite3.connect(path) as reader:
            assert reader.execute('SELECT version FROM workflow_draft_adapter_versions').fetchall() == [(1,)]
            assert reader.execute("SELECT COUNT(*) FROM sqlite_schema WHERE type='table' AND name LIKE 'workflow_draft_%'").fetchone()[0] == 3
        reader.close()
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)


@pytest.mark.parametrize('repetition', range(5))
def test_actual_script_sibling_migrators_serialize_one_extension(tmp_path: Path, repetition: int) -> None:
    from concurrent.futures import ThreadPoolExecutor
    import threading
    runtime, path = _org(tmp_path)
    barrier = threading.Barrier(2, timeout=10)
    def run() -> subprocess.CompletedProcess[str]:
        barrier.wait()
        return _run(runtime)
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: run(), range(2)))
    assert all(r.returncode == 0 for r in results), [r.stderr for r in results]
    assert sorted(r.stdout.split(':')[0] for r in results) == ['migrated', 'ready']
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT version FROM workflow_draft_adapter_versions').fetchall() == [(1,)]
        assert conn.execute('SELECT COUNT(*) FROM workflow_cutover_events').fetchone()[0] == 1
    conn.close()


def test_actual_script_wal_migration_preserves_committed_rows(tmp_path: Path) -> None:
    runtime, path = _org(tmp_path)
    owner = Database(path)
    try:
        from runtime.models import TaskRecord
        owner.insert_task(TaskRecord(id='TASK-001', brief='persisted in WAL', team='engineering', assigned_agent='maker'))
        before = owner.get_task('TASK-001').model_dump(mode='json')
        result = _run(runtime)
        assert result.returncode == 0, result.stderr
        assert owner.get_task('TASK-001').model_dump(mode='json') == before
        assert owner.execute('SELECT version FROM workflow_draft_adapter_versions').fetchone()[0] == 1
    finally:
        owner.close()


def test_actual_script_populated_e_replay_preserves_every_original_file_and_row(tmp_path: Path) -> None:
    from tests.workflows.test_draft_schema import _migrate, _seed_valid_draft
    runtime, path = _org(tmp_path)
    db = Database(path)
    _migrate(db)
    _seed_valid_draft(db)
    db.close()
    conn = sqlite3.connect(path)
    conn.execute('PRAGMA journal_mode=DELETE')
    conn.close()
    before, rows = _snapshot(runtime), _logical(path)
    for args in ((), ('--check',)):
        result = _run(runtime, *args)
        assert result.returncode == 0 and 'ready:' in result.stdout, result.stderr
        assert _snapshot(runtime) == before
        assert _logical(path) == rows


@pytest.mark.parametrize('layout',['v0','v1'])
def test_actual_script_on_source_pinned_historical_initialization_preserves_legacy(tmp_path: Path, layout: str) -> None:
    from tests.workflows.test_u0_migration_recovery import _execute_historical_v0_database, _execute_historical_v1_runtime, _legacy_snapshot
    runtime = RuntimeDir.init(tmp_path / 'runtime')
    root = runtime.orgs_dir / 'alpha'
    (root / 'org/agents').mkdir(parents=True)
    (root / 'org/teams.yaml').write_text('teams: {}\n')
    historical = tmp_path / 'historical'
    historical.mkdir()
    if layout == 'v1':
        _execute_historical_v1_runtime(historical)
    old_path = historical / ('opc.db' if layout == 'v1' else 'v0.db')
    _execute_historical_v0_database(old_path)
    original = _snapshot(historical)
    # Copying a disposable source fixture is not a production converter.
    import shutil
    path = root / 'happyranch.db'
    shutil.copy2(old_path,path)
    db = Database(path)  # Supported generic migration, before explicit foundation.
    install_or_recover(db,expected_org_slug='alpha')
    db.close()
    before = _legacy_snapshot(path)
    result = _run(runtime.root)
    assert result.returncode == 0, result.stderr
    assert _legacy_snapshot(path) == before
    assert _snapshot(historical) == original
