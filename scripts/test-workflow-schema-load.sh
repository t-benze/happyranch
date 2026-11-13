#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
uv run --frozen --python 3.14 python - <<'PY'
from pathlib import Path
from contextvars import copy_context
import sqlite3
import logging
import os
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

from runtime.infrastructure.database import Database
from runtime.infrastructure.workflow_schema import install_or_recover, validate_workflow_schema, migrate_draft_schema
from runtime.infrastructure.workflow_schema_load import org_load_validation, read_workflow_schema_for_org_load
from runtime.infrastructure.team_move_store import install_team_move_schema, TeamMoveStore
from runtime.infrastructure.task_pause_controls import install_pause_schema, TaskPauseStore
from runtime.infrastructure.workflow_task_ownership import install_task_ownership
from runtime.models import TaskRecord
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.org_config import seed_org_settings_from_config, backfill_reviewer_agents_setting
from runtime.orchestrator.task_pause import restore_pause_preparations
from runtime.orchestrator.producer_move import native_writer
from runtime.orchestrator.team_move import TeamMoveCoordinator
from runtime.workflows.cutover import WorkflowCutoverStore
from runtime.workflows import sequential_return
from runtime.workflows.recovery import rediscover_returned
from runtime.workflows.sequential_dispatch import SequentialDispatcher
from runtime.infrastructure import workflow_schema_load as load_guard
from runtime.identities.registry import Namespace, reconcile_namespace
from runtime.daemon.org_state import OrgState
from runtime.config import Settings

# Real OrgState.load, without a listener, runners or serving-daemon access.
with tempfile.TemporaryDirectory(prefix='org-load-timing-') as scratch:
    root = Path(scratch) / 'orgs' / 'scratch'
    (root / 'org' / 'agents').mkdir(parents=True)
    (root / 'org' / 'teams.yaml').write_text('teams: {}\n')
    messages = []
    class Capture(logging.Handler):
        def emit(self, record):
            messages.append(record.getMessage())
    log = logging.getLogger(load_guard.__name__)
    capture = Capture()
    prior_level = log.level
    log.setLevel(logging.INFO)
    log.addHandler(capture)
    try:
        with patch.dict(os.environ, {'HAPPYRANCH_DAEMON_HOME': scratch}):
            settings = Settings()
            # Warm installation, then measure a genuine reopen of the same fixture.
            state = OrgState.load(slug='scratch', root=root, settings=settings)
            state.close()
            messages.clear()
            start = time.perf_counter()
            state = OrgState.load(slug='scratch', root=root, settings=settings)
            elapsed = time.perf_counter() - start
            state.close()
        print(f'LOCAL FIXTURE OrgState.load reopen: wall={elapsed:.6f}s; {messages[-1]}')
        print('Empty-roster fixture; authority publication remains fenced; no serving/restart claim.')
    finally:
        log.removeHandler(capture)
        log.setLevel(prior_level)

# Each table row drives the actual production owner, with an actual SQLite DB.
# Interposition only observes/perturbs the boundary AFTER the real source check.
target_failures = []
for site in ('cutover', 'ownership', 'naming'):
    for scenario in ('zero-write', 'owner-write', 'unrelated-write', 'unavailable-bookend'):
        with tempfile.TemporaryDirectory(prefix=f'load-target-{site}-') as scratch:
            paths = OrgPaths(root=Path(scratch))
            paths.org_dir.mkdir()
            paths.agents_dir.mkdir()
            db = Database(paths.db_path)
            try:
                install_or_recover(db, expected_org_slug='scratch')
                with db.workflow_schema_transaction() as conn:
                    migrate_draft_schema(conn, expected_org_slug='scratch')
                install_team_move_schema(db, org_slug='scratch')
                install_pause_schema(db, org_slug='scratch')
                if site != 'ownership' or scenario != 'owner-write':
                    install_task_ownership(db, org_slug='scratch')
                elif scenario == 'owner-write':
                    db.insert_task(TaskRecord(id='TASK-target', brief='ownership backfill fixture'))
                org = SimpleNamespace(root=paths.root, db=db, slug='scratch')
                namespace = Namespace((), ())
                if site == 'naming':
                    reconcile_namespace(org, namespace, install=True)
                    if scenario == 'owner-write':
                        namespace = Namespace((('builder', 'active'),), ())
                cutover = WorkflowCutoverStore(db, org_slug='scratch')
                if site == 'cutover' and scenario == 'owner-write':
                    # Genuine event writer prepares a committed disable request.
                    with cutover._transaction(write=True) as conn:
                        from runtime.workflows.coding_authority_history import _Budget
                        budget = _Budget()
                        for _ in range(4):
                            marker, events, _ = cutover._read(conn, budget=budget)
                            cutover._advance(conn, marker, events, key='disable-fixture' if marker['generation'] == 4 else 'enable-fixture', budget=budget)
                statements = []
                db._conn.set_trace_callback(statements.append)
                original_source = load_guard.read_workflow_schema_for_org_load
                injected = False
                target_results = []
                original_target = getattr(load_guard, 'read_workflow_schema_target_for_org_load', None)
                def target(conn, **kwargs):
                    assert conn.in_transaction, 'target lost its caller snapshot'
                    result = original_target(conn, **kwargs)
                    assert conn.in_transaction, 'target ended its caller transaction'
                    target_results.append(result)
                    return result
                original_advance = cutover._advance
                def advance(conn, marker, events, **kwargs):
                    before = sum(sql == 'PRAGMA integrity_check' for sql in statements)
                    original_advance(conn, marker, events, **kwargs)
                    assert sum(sql == 'PRAGMA integrity_check' for sql in statements) == before + 1, 'recovery mutation lost its full target check before COMMIT'
                def source(conn, **kwargs):
                    global injected
                    result = original_source(conn, **kwargs)
                    if kwargs.get('writer_source') and not injected:
                        injected = True
                        if scenario == 'unrelated-write':
                            conn.execute("INSERT INTO org_settings(section,value_json,updated_at) VALUES ('target-probe','{}','2026-01-01T00:00:00+00:00')")
                        elif scenario == 'unavailable-bookend':
                            def deny(action, arg1, arg2, database, origin):
                                return (sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_PRAGMA
                                        and arg1 == 'data_version' else sqlite3.SQLITE_OK)
                            conn.set_authorizer(deny)
                    return result
                with org_load_validation(db, org_slug='scratch'):
                    cached = original_source(db._conn, expected_org_slug='scratch')
                    owner = load_guard._current.get()
                    before_full, before_hits = owner.full_runs, owner.hits
                    changes = db._conn.total_changes
                    before_checks = sum(sql == 'PRAGMA integrity_check' for sql in statements)
                    with (patch.object(load_guard, 'read_workflow_schema_for_org_load', side_effect=source),
                          patch.object(load_guard, 'read_workflow_schema_target_for_org_load', side_effect=target, create=True),
                          patch.object(cutover, '_advance', side_effect=advance)):
                        if site == 'cutover':
                            projection = cutover.recover_authorized()
                            assert projection['org_slug'] == 'scratch'
                            if scenario == 'owner-write':
                                assert projection['generation'] > 5, projection
                        elif site == 'ownership':
                            install_task_ownership(db, org_slug='scratch')
                            assert db._task_ownership_org == 'scratch'
                            if scenario == 'owner-write':
                                assert tuple(db._conn.execute('SELECT task_id,kind FROM workflow_task_ownership').fetchone()) == ('TASK-target', 'legacy')
                        else:
                            reconcile_namespace(org, namespace, install=True)
                            if scenario == 'owner-write':
                                assert db._conn.execute("SELECT current_label FROM identity_name_owners WHERE canonical_id='builder'").fetchone()[0] == 'builder'
                    db._conn.set_authorizer(None)
                    assert injected, (site, scenario, 'source seam not reached')
                    assert not db._conn.in_transaction, 'target changed caller transaction ownership'
                    if scenario == 'zero-write':
                        good = (owner.full_runs == before_full and owner.hits == before_hits + 2
                                and db._conn.total_changes == changes
                                and sum(sql == 'PRAGMA integrity_check' for sql in statements) == before_checks)
                        # Reuse returns the exact complete tuple, without reconstruction.
                        if target_results:
                            assert len(target_results) == 1 and target_results[0] is cached
                        assert owner.saved[1] is cached
                        assert 'PRAGMA integrity_check' in statements
                    else:
                        good = sum(sql == 'PRAGMA integrity_check' for sql in statements) > before_checks
                        if scenario in ('owner-write', 'unrelated-write'):
                            assert db._conn.total_changes > changes, (site, scenario, 'no actual write')
                    if not good:
                        target_failures.append((site, scenario, owner.full_runs - before_full, owner.hits - before_hits))
                    print('TARGET', site, scenario, 'PASS' if good else 'FAIL',
                          f'full_delta={owner.full_runs-before_full} hits_delta={owner.hits-before_hits}')
                if site == 'cutover' and scenario == 'zero-write':
                    # The public read keeps its original full path even IN a load scope.
                    with org_load_validation(db, org_slug='scratch'):
                        original_source(db._conn, expected_org_slug='scratch')
                        before = sum(sql == 'PRAGMA integrity_check' for sql in statements)
                        cutover.get()
                        assert sum(sql == 'PRAGMA integrity_check' for sql in statements) == before + 1, 'public cutover get reused within load scope'
            finally:
                db._conn.set_authorizer(None)
                db.close()
assert not target_failures, target_failures

with tempfile.TemporaryDirectory(prefix='returned-storage-') as scratch:
    db = Database(Path(scratch) / 'org.db')
    dispatcher = SequentialDispatcher(SimpleNamespace(db=db, slug='scratch'))
    try:
        for installed in (False, True):
            if installed:
                install_or_recover(db, expected_org_slug='scratch')
            with patch.object(sequential_return, 'read_sequential_history',
                              side_effect=AssertionError('absent storage entered complete reader')) as reader:
                assert sequential_return.returned_run_ids(dispatcher) == ()
                rediscover_returned(dispatcher, sequential=True)
                reader.assert_not_called()
            print('PASS returned absence skips complete reader:', 'legacy' if installed else 'native-only')

        # Presence includes mixed-case names, table associations and TEMP;
        # even malformed/partial storage must reach the complete reader.
        for ddl, cleanup in (
            ('CREATE TABLE WoRkFlOw_SeQ_probe (id)', 'DROP TABLE WoRkFlOw_SeQ_probe'),
            ('CREATE VIEW workflow_seq_probe AS SELECT 1', 'DROP VIEW workflow_seq_probe'),
            ('CREATE TEMP TABLE workflow_seq_probe (id)', 'DROP TABLE temp.workflow_seq_probe'),
        ):
            db._conn.execute(ddl)
            db._conn.commit()
            try:
                for consume in (sequential_return.returned_run_ids,
                                lambda owner: rediscover_returned(owner, sequential=True)):
                    with patch.object(sequential_return, 'read_sequential_history',
                                      side_effect=ValueError('complete-reader-refusal')) as reader:
                        try:
                            consume(dispatcher)
                        except ValueError as exc:
                            assert str(exc) == 'complete-reader-refusal', exc
                        else:
                            raise AssertionError('present storage suppressed complete-reader refusal')
                        reader.assert_called_once_with(db._conn, expected_org_slug='scratch')
                # The real reader must also refuse these incomplete layouts.
                try:
                    sequential_return.returned_run_ids(dispatcher)
                except ValueError:
                    pass
                else:
                    raise AssertionError('partial sequential storage accepted')
                print('PASS returned presence preserves complete-reader refusal:', ddl)
            finally:
                db._conn.execute(cleanup)
                db._conn.commit()
    finally:
        db.close()

with tempfile.TemporaryDirectory(prefix='workflow-load-') as scratch:
    paths = OrgPaths(root=Path(scratch))
    paths.org_dir.mkdir()
    paths.agents_dir.mkdir()
    db = Database(paths.db_path)
    try:
        install_or_recover(db, expected_org_slug='scratch')
        install_team_move_schema(db, org_slug='scratch')
        install_pause_schema(db, org_slug='scratch')
        install_task_ownership(db, org_slug='scratch')
        TeamMoveCoordinator(TeamMoveStore(db, 'scratch'))
        db.insert_task(TaskRecord(id='TASK-1', brief='isolated startup writer'))
        store = db._task_pause_store = TaskPauseStore(db, 'scratch')
        with store.writer('TASK-1'):
            store.ensure_uncommitted('TASK-1')
        statements = []
        db._conn.set_trace_callback(statements.append)

        def checks():
            return sum(s == 'PRAGMA integrity_check' for s in statements)

        def read(**kwargs):
            return read_workflow_schema_for_org_load(db._conn, expected_org_slug='scratch', **kwargs)

        with org_load_validation(db, org_slug='scratch'):
            first = read()
            assert checks() == 1, 'first validation lost integrity_check'
            assert 'PRAGMA foreign_key_check' in statements, 'first full validation lost FK check'
            assert read() == first
            assert checks() == 1, 'unchanged load repeated the full traversal'
        print('PASS unchanged reuse and first-run integrity/FK')

        failures = []

        def expect(condition, message):
            if not condition:
                failures.append(message)
                print('FAIL:', message)

        actions = {
            'own DML': lambda: db.upsert_org_setting('probe', '{}'),
            'main schema cookie': lambda: db._conn.execute('CREATE TABLE load_probe (value)'),
            'TEMP schema cookie': lambda: db._conn.execute('CREATE TEMP TABLE temp_probe (value)'),
            'settings seed': lambda: seed_org_settings_from_config(paths, db),
            'settings reviewer backfill': lambda: backfill_reviewer_agents_setting(paths, db),
            'Pause journal save': lambda: save_pause(),
            'Pause startup restore': lambda: restore_pause_preparations(SimpleNamespace(_db=db)),
            'naming reservation audit': lambda: reserve_names(),
        }

        def save_pause():
            with store.writer('TASK-1') as (_, row):
                store.save_journal_uncommitted(row)

        def reserve_names():
            with native_writer(db, publisher='identity_names_load'):
                pass
            assert db._conn.execute("SELECT count(*) FROM audit_log WHERE action='team_move_native_writer'").fetchone()[0] == 2

        for name, action in actions.items():
            if name == 'settings reviewer backfill':
                db._conn.execute("DELETE FROM org_settings WHERE section='reviewer_agents'")
                db._conn.commit()
            with org_load_validation(db, org_slug='scratch'):
                read()
                before = checks()
                changes = db._conn.total_changes
                action()
                db._conn.commit()
                if name in ('main schema cookie', 'TEMP schema cookie'):
                    assert db._conn.total_changes == changes, name + ' must exercise DDL without DML detection'
                else:
                    assert db._conn.total_changes > changes, name + ' did not execute a real writer'
                read()
                expect(checks() == before + 1, name + ' failed to invalidate')
                read()
                expect(checks() == before + 1, name + ' did not establish a fresh reusable result')
            print('CASE invalidation:', name)

        other = sqlite3.connect(paths.db_path)
        try:
            with org_load_validation(db, org_slug='scratch'):
                read()
                before, local = checks(), db._conn.total_changes
                other.execute("UPDATE org_settings SET value_json='null' WHERE section='probe'")
                other.commit()
                assert db._conn.total_changes == local
                read()
                expect(checks() == before + 1, 'independent commit reused stale validation')
            print('CASE independent connection data_version')
        finally:
            other.close()

        for unavailable in ('data_version', 'schema_version', 'database_list', 'query_only'):
            with org_load_validation(db, org_slug='scratch'):
                read()
                before = checks()
                def deny(action, arg1, arg2, database, source):
                    return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_PRAGMA and arg1 == unavailable else sqlite3.SQLITE_OK
                db._conn.set_authorizer(deny)
                try:
                    read()
                    read()
                finally:
                    db._conn.set_authorizer(None)
                expect(checks() == before + 2, unavailable + ' failure did not fall back')
            print('CASE unknown/failed bookend:', unavailable)

        other = sqlite3.connect(paths.db_path)
        try:
            for boundary in ('BEGIN', 'ROLLBACK'):
                with org_load_validation(db, org_slug='scratch'):
                    read()
                    before = checks()
                    moved = False
                    def move_at_boundary(sql):
                        global moved
                        statements.append(sql)
                        if sql == boundary and not moved:
                            moved = True
                            other.execute("UPDATE org_settings SET value_json=? WHERE section='probe'", ('"' + boundary + '"',))
                            other.commit()
                    db._conn.set_trace_callback(move_at_boundary)
                    try:
                        read()
                    finally:
                        db._conn.set_trace_callback(statements.append)
                    assert moved, boundary + ' did not exercise the real snapshot boundary'
                    expect(checks() == before + 1, boundary + ' movement returned reused validation')
                print('CASE independent movement at snapshot boundary:', boundary)
        finally:
            other.close()

        with org_load_validation(db, org_slug='scratch'):
            read()
            before = checks()
            db._conn.execute('PRAGMA query_only=ON')
            try:
                read()
            finally:
                db._conn.execute('PRAGMA query_only=OFF')
            expect(checks() == before + 1, 'connection configuration change did not invalidate')
        print('CASE connection configuration identity')
        for config_name in ('native config', 'SQL limit'):
            with org_load_validation(db, org_slug='scratch'):
                read()
                before = checks()
                if config_name == 'native config':
                    prior = db._conn.getconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE)
                    db._conn.setconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE, not prior)
                else:
                    prior = db._conn.getlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH)
                    db._conn.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, prior - 1)
                try:
                    read()
                finally:
                    if config_name == 'native config':
                        db._conn.setconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE, prior)
                    else:
                        db._conn.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, prior)
                expect(checks() == before + 1, config_name + ' change did not invalidate')
            print('CASE connection configuration identity:', config_name)

        with org_load_validation(db, org_slug='scratch'):
            read()
            inherited = copy_context()
        before = checks()
        inherited.run(read)
        inherited.run(read)
        assert checks() == before + 2, 'ended load reused through an inherited context'
        before = checks()
        with org_load_validation(db, org_slug='scratch'):
            read()
        with org_load_validation(db, org_slug='scratch'):
            read()
        read()
        assert checks() == before + 3, 'reuse escaped the org load'
        print('PASS scope exit, fresh load and public fallback')

        with org_load_validation(db, org_slug='scratch'):
            read()
            before = checks()
            db._conn.execute('BEGIN')
            try:
                read()
                read()
            finally:
                db._conn.rollback()
            assert checks() == before + 2, 'unknown caller read snapshot reused validation'
            read()  # Establish a fresh reusable source after the unknown view.
            before = checks()
            install_team_move_schema(db, org_slug='scratch')
            assert checks() == before, 'team-move source did not use the real load seam'
            install_task_ownership(db, org_slug='scratch')
            assert checks() == before, 'unchanged ownership target repeated the full read'
            before = checks()
            WorkflowCutoverStore(db, org_slug='scratch').recover_authorized()
            assert checks() == before, 'unchanged cutover recovery target repeated the full read'
        print('PASS unknown caller snapshot, real source owners and zero-write targets')

        assert not failures, failures
        print('PASS all named invalidation/bookend cases')
        db._conn.set_trace_callback(None)
        start = time.perf_counter()
        for _ in range(10):
            validate_workflow_schema(db._conn, expected_org_slug='scratch')
        baseline = time.perf_counter() - start
        start = time.perf_counter()
        with org_load_validation(db, org_slug='scratch'):
            for _ in range(10):
                read()
        optimized = time.perf_counter() - start
        print(f'ISOLATED ten unchanged source validations: before={baseline:.6f}s after={optimized:.6f}s')
        print('Synthetic local SQLite; not a serving-daemon restart or production-size estimate.')
    finally:
        db.close()
PY
