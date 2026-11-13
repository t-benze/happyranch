#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
uv run --frozen --python 3.14 python - <<'PY'
from pathlib import Path
from contextlib import contextmanager
from contextvars import copy_context
import json
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
from runtime.identities import registry as names
from runtime.orchestrator import producer_move
from runtime.orchestrator.agent_def import AgentDef, render_agent_text

startup_failures = []
def startup_expect(condition, case):
    print('STARTUP', case, 'PASS' if condition else 'FAIL')
    if not condition:
        startup_failures.append(case)

# Observe real load reservations as well as persisted audit history. The spy
# delegates to the shipping writer; no fixture supplies its disposition/rows.
real_writer = producer_move.native_writer
reservations = []
@contextmanager
def observe_writer(db, **kwargs):
    if kwargs['publisher'] == 'identity_names_load':
        reservations.append(db._conn.total_changes)
    with real_writer(db, **kwargs) as disposition:
        if kwargs['publisher'] == 'identity_names_load':
            assert naming_rows(db)[-1]['state'] == 'active', 'naming entered before durable reservation'
        yield disposition

def naming_rows(db):
    return [json.loads(row[0]) for row in db._conn.execute(
        "SELECT payload FROM audit_log WHERE action='team_move_native_writer' ORDER BY id")
        if json.loads(row[0])['publisher'] == 'identity_names_load']

# B1 L1-L6. Literal legacy rows precede the first real production load.
# No fresh initializer, resolver stub, supplied reviewer set or launched worker.
from runtime.models import BlockKind, TaskStatus, CompletionReport
from runtime.daemon.queue import TaskQueue
from runtime.orchestrator import run_step as reviewer_steps
from runtime.orchestrator.org_config import (
    OrgConfigError, resolve_reviewer_configuration, resolve_known_agent_names,
    establish_reviewer_agents_policy, write_org_setting_to_db,
)
from runtime.workflows.authority import WorkflowAuthorityError
from runtime.infrastructure.db.knowledge import ReviewerPolicyError

@contextmanager
def literal_reviewer_fixture(*, raw='[]', marker=None, sentinel=False, config='', saved=None,
                             verdict=None, expectation=None, metadata=('2001-01-01T00:00:00+00:00', 'founder')):
    with tempfile.TemporaryDirectory(prefix='reviewer-legacy-') as scratch:
        root = Path(scratch) / 'orgs' / 'scratch'
        (root / 'org' / 'agents').mkdir(parents=True)
        (root / 'org' / 'teams.yaml').write_text(
            'teams:\n  engineering:\n    manager: owner\n    workers: [code_reviewer, worker, other, senior_dev, family_manager]\n')
        for name in ('owner', 'code_reviewer', 'worker', 'other', 'senior_dev', 'family_manager'):
            role = 'manager' if name == 'owner' else 'worker'
            (root / 'org' / 'agents' / f'{name}.md').write_text(
                f'---\nname: {name}\nteam: engineering\nrole: {role}\nexecutor: claude\n---\nSynthetic inert role.\n')
            (root / 'workspaces' / name).mkdir(parents=True)
        (root / 'org' / 'config.yaml').write_text(config)
        if sentinel:
            (root / '.org_settings_seeded').write_text('')
        paths = OrgPaths(root)
        db = Database(paths.db_path)
        if raw is not None:
            db._conn.execute('INSERT INTO org_settings VALUES (?,?,?,?)',
                             ('reviewer_agents', raw, *metadata))
        if marker is not None:
            db._conn.execute('INSERT INTO org_settings VALUES (?,?,?,?)',
                             ('reviewer_agents_policy', marker, *metadata))
        db._conn.commit()
        if marker is None:
            assert db.get_org_setting('reviewer_agents_policy') is None
        if saved is not None:
            # Literal pre-change omitted-expectation chain, before upgrade.
            chain = '{"step_index":0,"legs":[{"agent":"worker","prompt":"Synthetic downstream"}],"step_audit_id":0}'
            assert 'first_leg_expect_verdict' not in json.loads(chain)
            if expectation is not None:
                chain = json.dumps({**json.loads(chain), 'first_leg_expect_verdict': expectation})
            fanout = '{"children_ids":["TASK-101","TASK-103"],"children_details":[],"width":2,"manager_agent":"owner","status":"spawned"}'
            records = [('TASK-101', 'in_progress', 'owner', 'subtask' if saved == 'carrier' else 'task',
                        'delegated', chain, 'TASK-100' if saved == 'carrier' else None, None, None),
                       ('TASK-102', 'completed', 'code_reviewer', 'subtask', None, None, 'TASK-101', 'synthetic-child', None)]
            if saved == 'carrier':
                records += [('TASK-100', 'in_progress', 'owner', 'task', 'delegated', None, None, None, fanout),
                            ('TASK-103', 'completed', 'worker', 'subtask', None, None, 'TASK-100', None, None)]
            for task_id, status, agent, kind, blocked, active, parent, session, fan in records:
                db._conn.execute('INSERT INTO tasks (id,status,assigned_agent,team,brief,task_type,block_kind,active_chain,parent_task_id,current_session_id,active_fanout,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (task_id,status,agent,'engineering','Synthetic legacy task',kind,blocked,active,parent,session,fan,*[metadata[0]]*2))
            db._conn.commit()
            db.insert_task_result('TASK-102', 'code_reviewer', 'synthetic-child',
                                  'Synthetic genuine completed report', 100, verdict=verdict)
        original = db._conn.execute("SELECT * FROM org_settings WHERE section='reviewer_agents'").fetchone()
        original = tuple(original) if original else None
        db.close()
        with patch.dict(os.environ, {'HAPPYRANCH_DAEMON_HOME': scratch}):
            state = OrgState.load(slug='scratch', root=root, settings=Settings())
            state.close()
            state = OrgState.load(slug='scratch', root=root, settings=Settings())
            state.orchestrator._queue = TaskQueue()  # Real queue, never started.
            try:
                yield state, original
            finally:
                state.close()


def reviewer_audits(db):
    return [(row['task_id'], row['agent'], json.loads(row['payload'])) for row in db._conn.execute(
        "SELECT task_id,agent,payload FROM audit_log WHERE action='org_config_write' AND task_id IN ('config:reviewer_agents','config:reviewer_agents_policy') ORDER BY id")]


def submit_reviewer_decision(state, action, *, downstream=True):
    db, orch = state.db, state.orchestrator
    parent_id = db.next_task_id()
    db.insert_task(TaskRecord(id=parent_id, assigned_agent='owner', brief='Synthetic submission'))
    assert db.try_claim_for_step(parent_id, TaskStatus.PENDING, None, 1)
    db.update_task(parent_id, current_session_id='synthetic-owner')
    child = {'agent': 'code_reviewer', 'prompt': 'Synthetic review',
             'then': [{'agent': 'worker', 'prompt': 'Synthetic downstream'}]}
    if not downstream:
        child.pop('then')
    decision = ({'action': 'delegate', **child} if action == 'delegate' else
                {'action': 'fanout', 'children': [child, {'agent': 'worker', 'prompt': 'Synthetic sibling'}]})
    db.insert_task_result(parent_id, 'owner', 'synthetic-owner', 'Synthetic submitted decision', 100,
                          decision_json=json.dumps(decision))
    report = db.get_latest_completion_report(parent_id)
    before = db.next_task_id()
    reviewer_steps._consume_completion_report(orch, parent_id, report, reclaim_terminal_worktree=False)
    return parent_id, before


for sentinel in (False, True):
    with literal_reviewer_fixture(sentinel=sentinel) as (state, original):
        db, orch = state.db, state.orchestrator
        tag = 'L1b' if sentinel else 'L1a'
        startup_expect(json.loads(db.get_org_setting('reviewer_agents')) == ['code_reviewer'], tag+'-legacy-empty-pinned')
        audits = reviewer_audits(db)
        startup_expect(audits == [
            ('config:reviewer_agents', 'orchestrator', {'section': 'reviewer_agents', 'tiers': ['value'], 'before': {'value': []}, 'after': {'value': ['code_reviewer']}}),
            ('config:reviewer_agents_policy', 'orchestrator', {'section': 'reviewer_agents_policy', 'tiers': ['version'], 'before': {}, 'after': {'version': 2}}),
        ], tag+'-exact-once-audits-reopen')
        for action in ('delegate', 'fanout'):
            parent, before = submit_reviewer_decision(state, action)
            rows = db.get_task_results(parent)
            startup_expect(not db.get_children(parent) and db.next_task_id() == before
                and db.get_task(parent).status == TaskStatus.PENDING
                and any('HARD REJECT' in row['output_summary'] for row in rows), tag+'-'+action+'-omission-zero-children')
            queued = list(orch._queue._queue._queue)
            startup_expect(all(db.get_task(item[1]).parent_task_id is None for item in queued), tag+'-'+action+'-no-child-queue')

for saved in ('ordinary', 'carrier'):
    for verdict in (None, 'REQUEST_CHANGES', 'REVISE', 'BLOCK', 'PASS', 'APPROVE'):
        with literal_reviewer_fixture(saved=saved, verdict=verdict) as (state, original):
            db, orch = state.db, state.orchestrator
            before = db.next_task_id()
            before_links = db._conn.execute('SELECT count(*) FROM task_attachments').fetchone()[0]
            reviewer_steps._enqueue_parent_if_waiting(orch, 'TASK-102')
            parent = db.get_task('TASK-101')
            tag = ('L2a' if saved == 'ordinary' else 'L2b')+'-'+str(verdict)
            if verdict == 'APPROVE':
                startup_expect(len(db.get_children(parent.id)) == 2 and db.next_task_id() != before,
                               tag+'-advances-once')
                reviewer_steps._enqueue_parent_if_waiting(orch, 'TASK-102')
                startup_expect(len(db.get_children(parent.id)) == 2, tag+'-duplicate-no-advance')
            else:
                startup_expect(db.get_children(parent.id) == ['TASK-102'] and db.next_task_id() == before
                    and parent.active_chain is None and db._conn.execute('SELECT count(*) FROM task_attachments').fetchone()[0] == before_links,
                    tag+'-zero-downstream-ids-links')
                target = 'TASK-101' if saved == 'ordinary' else 'TASK-100'
                queued = list(orch._queue._queue._queue)
                startup_expect([item[1] for item in queued] == [target], tag+'-wake-or-fanin-tail')
                if saved == 'carrier':
                    startup_expect(parent.status == TaskStatus.FAILED, tag+'-carrier-failed')

# Marked none retains ordinary advancement and explicit saved expectations.
for expectation, verdict, advances in ((None, None, True), ('APPROVE', 'PASS', False)):
    for saved in ('ordinary','carrier'):
        with literal_reviewer_fixture(raw='[]', marker='{"version":2}', saved=saved,
                                      verdict=verdict, expectation=expectation) as (state, original):
            reviewer_steps._enqueue_parent_if_waiting(state.orchestrator, 'TASK-102')
            startup_expect(len(state.db.get_children('TASK-101')) == (2 if advances else 1),
                           'L3-none-'+saved+'-'+str(expectation)+'-explicit-expectations-retained')

for raw, marker in (('["code_reviewer"]', None), ('[]', '{"version":2}')):
    with literal_reviewer_fixture(raw=raw, marker=marker) as (state, original):
        parent,before = submit_reviewer_decision(state,'delegate',downstream=False)
        startup_expect(len(state.db.get_children(parent)) == 1,
                       'L3-bare-single-leg-exception-'+raw)

for raw, timestamp in (( '["code_reviewer"]', '2026-08-24T08:41:47.100629+00:00'),
                        ( '["senior_dev"]', '2026-11-05T11:14:11.056269+00:00'),
                        ( '["family_manager"]', '2026-11-05T11:14:16.517155+00:00')):
    for sentinel in (False, True):
        with literal_reviewer_fixture(raw=raw, sentinel=sentinel, metadata=(timestamp,'founder')) as (state, original):
            db = state.db
            startup_expect(tuple(db._conn.execute("SELECT * FROM org_settings WHERE section='reviewer_agents'").fetchone()) == original,
                           'L4-'+raw+'-'+str(sentinel)+'-all-columns-unchanged')
            startup_expect([row[0] for row in reviewer_audits(db)] == ['config:reviewer_agents_policy'],
                           'L4-'+raw+'-'+str(sentinel)+'-marker-audit-only')

for sentinel in (False, True):
    for config, expected in (('', ['code_reviewer']), ('reviewer_agents: [other]\n', ['other']),
                             ('reviewer_agents: [ghost]\n', ['code_reviewer']), ('reviewer_agents: []\n', ['code_reviewer'])):
        with literal_reviewer_fixture(raw=None, sentinel=sentinel, config=config) as (state, original):
            startup_expect(json.loads(state.db.get_org_setting('reviewer_agents')) == expected,
                           'L6-missing-'+str(sentinel)+'-'+repr(config))
for raw, marker in (('broken',None), ('[]','{"version":true}'), ('[]','{"version":3}'),
                    ('[]','{"version":2,"extra":0}'), ('[]','{"version":2,"version":2}'),
                    (None,'{"version":2}'), (b'[]',None), ('[]',b'{"version":2}')):
    try:
        with literal_reviewer_fixture(raw=raw, marker=marker):
            startup_expect(False, 'L6-corrupt-pair-refused-'+str((raw,marker)))
    except OrgConfigError:
        startup_expect(True, 'L6-corrupt-pair-refused-'+str((raw,marker)))
for raw in ('["ghost"]', '["code_reviewer","ghost"]'):
    with literal_reviewer_fixture(raw=raw) as (state, original):
        resolution = resolve_reviewer_configuration(state.db, known_agents=resolve_known_agent_names(OrgPaths(state.root)))
        startup_expect(resolution.state == 'missing' and resolution.missing_agents == ('ghost',), 'L6-stale-no-default-masking-'+raw)
        try:
            state.workflow_authority.capture_canonical_snapshot()
        except WorkflowAuthorityError as exc:
            startup_expect(exc.code == 'authority_reviewer_incoherent', 'L6-stale-authority-refusal-'+raw)
        else:
            startup_expect(False, 'L6-stale-authority-refusal-'+raw)
        parent,before = submit_reviewer_decision(state,'delegate')
        startup_expect(not state.db.get_children(parent) and state.db.next_task_id() == before
            and 'reviewer configuration unavailable' in state.db.get_task(parent).note,
            'L6-stale-zero-child-allocation-'+raw)

# L5: two real connections with SQL trace/event ordering, no timing sleeps or
# production failpoint. A third owner is never inferred from a fixture flag.
import threading
for first, second in (('convert','convert'), ('convert','clear'), ('clear','convert'),
                      ('convert','configure'), ('configure','convert')):
    with tempfile.TemporaryDirectory(prefix='reviewer-race-') as scratch:
        dbs = [Database(Path(scratch)/'org.db') for _ in range(2)]
        dbs[0]._conn.execute("INSERT INTO org_settings VALUES ('reviewer_agents','[]','old','founder')")
        dbs[0]._conn.commit()
        locked, waiting, release = threading.Event(), threading.Event(), threading.Event()
        errors = []
        def trace_first(sql):
            if sql.startswith('SELECT section, value_json FROM org_settings'):
                locked.set()
                if not release.wait(10):
                    errors.append('first transaction was not released')
        def trace_second(sql):
            if sql == 'BEGIN IMMEDIATE':
                waiting.set()
        dbs[0]._conn.set_trace_callback(trace_first)
        dbs[1]._conn.set_trace_callback(trace_second)
        def act(db, action):
            try:
                if action == 'convert':
                    db.establish_reviewer_agents_policy(['code_reviewer'])
                else:
                    db.replace_reviewer_agents([] if action == 'clear' else ['other'])
            except BaseException as exc:
                errors.append(repr(exc))
        threads = [threading.Thread(target=act,args=(dbs[0],first)), threading.Thread(target=act,args=(dbs[1],second))]
        try:
            threads[0].start()
            assert locked.wait(10), 'L5 first connection never owned locked reread'
            threads[1].start()
            assert waiting.wait(10), 'L5 competing BEGIN never reached'
        finally:
            release.set()
            for thread in threads:
                if thread.ident is not None:
                    thread.join(10)
            for db in dbs:
                db._conn.set_trace_callback(None)
        expected = [] if 'clear' in (first,second) else ['other'] if 'configure' in (first,second) else ['code_reviewer']
        startup_expect(not errors and not any(t.is_alive() for t in threads)
                       and json.loads(dbs[0].get_org_setting('reviewer_agents')) == expected
                       and sum(a[0]=='config:reviewer_agents_policy' for a in reviewer_audits(dbs[0])) == 1,
                       'L5-serialized-'+first+'-'+second)
        for db in dbs:
            db.close()
with tempfile.TemporaryDirectory(prefix='reviewer-rollback-') as scratch:
    path = Path(scratch)/'org.db'
    db = Database(path)
    db._conn.execute("INSERT INTO org_settings VALUES ('reviewer_agents','[]','old','founder')")
    db._conn.execute("CREATE TRIGGER synthetic_abort_marker BEFORE INSERT ON audit_log WHEN NEW.task_id='config:reviewer_agents_policy' BEGIN SELECT RAISE(ABORT,'synthetic marker audit failure'); END")
    db._conn.commit()
    try:
        db.establish_reviewer_agents_policy(['code_reviewer'])
    except sqlite3.IntegrityError:
        startup_expect(db.get_reviewer_agents_pair() == {'reviewer_agents':'[]'} and not reviewer_audits(db), 'L5-audit-failure-rolls-back-whole-pair')
    else:
        startup_expect(False,'L5-audit-failure-rolls-back-whole-pair')
    db._conn.execute('DROP TRIGGER synthetic_abort_marker')
    db._conn.commit()
    db.close()
    db = Database(path)
    db.establish_reviewer_agents_policy(['code_reviewer'])
    startup_expect(json.loads(db.get_org_setting('reviewer_agents')) == ['code_reviewer'] and len(reviewer_audits(db)) == 2,
                   'L5-reopen-after-rollback-converts-once')
    db.close()
# Existing schema-load owner already uses SQLite authorizers for commit faults.
# Deny the real COMMIT, then observe rollback and production reopen.
with tempfile.TemporaryDirectory(prefix='reviewer-commit-refusal-') as scratch:
    path = Path(scratch)/'org.db'
    db = Database(path)
    db._conn.execute("INSERT INTO org_settings VALUES ('reviewer_agents','[]','old','founder')")
    db._conn.commit()
    def deny_commit(action, arg1, arg2, database, source):
        return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_TRANSACTION and arg1 == 'COMMIT' else sqlite3.SQLITE_OK
    db._conn.set_authorizer(deny_commit)
    try:
        db.establish_reviewer_agents_policy(['code_reviewer'])
    except sqlite3.DatabaseError:
        startup_expect(db.get_reviewer_agents_pair() == {'reviewer_agents':'[]'} and not reviewer_audits(db),
                       'L5-commit-refusal-rolls-back-whole-pair')
    else:
        startup_expect(False,'L5-commit-refusal-rolls-back-whole-pair')
    finally:
        db._conn.set_authorizer(None)
    db.close()
    db = Database(path)
    db.establish_reviewer_agents_policy(['code_reviewer'])
    startup_expect(json.loads(db.get_org_setting('reviewer_agents')) == ['code_reviewer'] and len(reviewer_audits(db)) == 2,
                   'L5-reopen-after-commit-refusal-converts-once')
    db.close()
print('NOT RUN L5 physical disk-I/O/crash failure: authorizer covers actual COMMIT refusal, not a physical storage crash.')


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
            reservations.clear()
            start = time.perf_counter()
            with patch('runtime.workflows.authority.native_writer', observe_writer):
                state = OrgState.load(slug='scratch', root=root, settings=settings)
            elapsed = time.perf_counter() - start
            startup_expect(not reservations and len(naming_rows(state.db)) == 2
                           and state.naming_readiness == 'ready', 'naming-noop-zero-rows-and-reservations')
            state.close()
        print(f'LOCAL FIXTURE OrgState.load reopen: wall={elapsed:.6f}s; {messages[-1]}')
        print('Empty-roster fixture; authority publication remains fenced; no serving/restart claim.')
    finally:
        log.removeHandler(capture)
        log.setLevel(prior_level)

# Would-write/uncertain proofs keep main's active plus settled/UNKNOWN history.
# Failed cleanup retains the original active row when no terminal write is proven.
for scenario in ('would-write', 'unreadable', 'failed-read', 'failed-commit', 'failed-commit-rollback', 'interrupt'):
    with tempfile.TemporaryDirectory(prefix='naming-load-') as scratch:
        root = Path(scratch) / 'orgs' / 'scratch'
        (root / 'org' / 'agents').mkdir(parents=True)
        (root / 'org' / 'teams.yaml').write_text('teams: {}\n')
        with patch.dict(os.environ, {'HAPPYRANCH_DAEMON_HOME': scratch}):
            settings = Settings()
            state = OrgState.load(slug='scratch', root=root, settings=settings)
            state.close()
            if scenario == 'would-write':
                directory = root / 'org/agents/_terminated'
                directory.mkdir()
                definition = AgentDef(name='retired', team='engineering', role='worker',
                    executor='synthetic_unregistered', allow_rules=('happyranch',), repos={},
                    enrolled_by=None, enrolled_at_task=None, enrolled_at=None,
                    system_prompt='Inert synthetic definition.')
                (directory / 'retired.md').write_text(render_agent_text(definition))
            real_transaction = names.transaction
            @contextmanager
            def commit_failure(db):
                try:
                    with real_transaction(db) as conn:
                        yield conn
                        conn.set_authorizer(lambda action, arg1, *_:
                            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_TRANSACTION
                            and arg1 in (('COMMIT', 'ROLLBACK') if scenario == 'failed-commit-rollback'
                                         else ('COMMIT',)) else sqlite3.SQLITE_OK)
                finally:
                    db._conn.set_authorizer(None)
            error_patch = {
                'would-write': patch.object(names, 'capture_namespace', wraps=names.capture_namespace),
                'unreadable': patch.object(names, 'capture_namespace', side_effect=OSError('synthetic unreadable')),
                'failed-read': patch.object(names, 'read_rows', side_effect=sqlite3.DatabaseError('synthetic read failure')),
                'failed-commit': patch.object(names, 'transaction', commit_failure),
                'failed-commit-rollback': patch.object(names, 'transaction', commit_failure),
                'interrupt': patch.object(names, 'capture_namespace', side_effect=KeyboardInterrupt('synthetic interrupt')),
            }[scenario]
            reservations.clear()
            try:
                with patch('runtime.workflows.authority.native_writer', observe_writer), error_patch:
                    state = OrgState.load(slug='scratch', root=root, settings=settings)
            except KeyboardInterrupt:
                assert scenario == 'interrupt'
                db = Database(OrgPaths(root=root).db_path)
            except Exception:
                if scenario != 'failed-commit-rollback':
                    raise
                db = Database(OrgPaths(root=root).db_path)
            else:
                assert scenario != 'failed-commit-rollback', 'unclean proof load must abort'
                db = state.db
            rows = naming_rows(db)
            if scenario == 'failed-commit-rollback':
                assert db._conn.execute('SELECT count(*) FROM identity_name_owners').fetchone()[0] == 1
                db.close()
                state = OrgState.load(slug='scratch', root=root, settings=settings)
                # Main retains the original active reservation when both naming
                # COMMIT and ROLLBACK fail; closing the failed org cannot erase it.
                assert state._startup_publication_held, 'naming-failed-commit-rollback-next-boot: failed proof cleanup lost the original next-boot hold'
                startup_expect([r['state'] for r in rows] == ['active', 'settled', 'active']
                    and rows[0]['writer_id'] == rows[1]['writer_id']
                    and rows[2]['writer_id'] != rows[0]['writer_id']
                    and naming_rows(state.db) == rows, 'naming-failed-commit-rollback-history-and-next-boot')
                print('NAMING failed-commit-rollback states=', [r['state'] for r in rows],
                      'next_boot_held=', state._startup_publication_held)
                state.close()
                continue
            expected = 'settled' if scenario == 'would-write' else 'unknown'
            startup_expect(len(reservations) == 1 and len(rows) == 4
                and [r['state'] for r in rows[-2:]] == ['active', expected]
                and rows[-1]['writer_id'] == rows[-2]['writer_id'], 'naming-' + scenario + '-ownership')
            if scenario == 'would-write':
                assert tuple(db._conn.execute("SELECT lifecycle,current_label FROM identity_name_owners WHERE canonical_id='retired'").fetchone()) == ('terminated', 'retired')
            else:
                assert db._conn.execute("SELECT count(*) FROM identity_name_owners").fetchone()[0] == 1
            if scenario == 'interrupt':
                db.close()
            else:
                assert state.naming_readiness == ('ready' if scenario == 'would-write' else 'unavailable')
                state.close()
            state = OrgState.load(slug='scratch', root=root, settings=settings)
            startup_expect(state._startup_publication_held == (scenario != 'would-write')
                and len(naming_rows(state.db)) == 4, 'naming-' + scenario + '-next-boot')
            state.close()

with tempfile.TemporaryDirectory(prefix='status-normalize-') as scratch:
    db = Database(Path(scratch) / 'org.db')
    try:
        cases = (
            ('valid', 'escalated', None, 'escalated', None, 0),
            ('dirty', 'escalated', 'delegated', 'escalated', None, 1),
            ('legacy', 'blocked', 'escalated', 'escalated', None, 1),
            ('delegate', 'blocked', 'delegated', 'in_progress', 'delegated', 1),
            ('job', 'blocked', 'blocked_on_job', 'in_progress', 'blocked_on_job', 1),
            ('approved', 'approved', None, 'completed', None, 1),
            ('rejected', 'rejected', None, 'failed', None, 1),
            ('review', 'in_review', None, 'failed', None, 1),
            ('superseded', 'resolved_superseded', None, 'superseded', None, 1),
        )
        for key, status, reason, *_ in cases:
            db.insert_task(TaskRecord(id=key, brief='synthetic migration'))
            db._conn.execute('UPDATE tasks SET status=?,block_kind=? WHERE id=?', (status, reason, key))
        db._conn.execute('CREATE TEMP TABLE status_updates (task_id TEXT)')
        db._conn.execute('CREATE TEMP TRIGGER observe_status AFTER UPDATE OF status,block_kind ON main.tasks BEGIN INSERT INTO status_updates VALUES (new.id); END')
        db._conn.commit()
        db._create_tables()
        for key, _, _, status, reason, updates in cases:
            assert tuple(db._conn.execute('SELECT status,block_kind FROM tasks WHERE id=?', (key,)).fetchone()) == (status, reason), key
            count = db._conn.execute('SELECT count(*) FROM status_updates WHERE task_id=?', (key,)).fetchone()[0]
            startup_expect(count == updates, 'status-' + key + '-writes')
        db._conn.execute('DELETE FROM status_updates')
        db._conn.commit()
        db._create_tables()
        startup_expect(db._conn.execute('SELECT count(*) FROM status_updates').fetchone()[0] == 0, 'status-second-pass-no-writes')
    finally:
        db.close()

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
            if name.startswith('Pause '):
                # A byte-different journal still exercises invalidation. The
                # canonical no-op is covered separately below.
                db._conn.execute("UPDATE task_pause_controls SET admission_journal_json=? WHERE root_task_id='TASK-1'",
                                 ('{ "version": 1, "entries": [] }',))
                db._conn.commit()
            if name == 'settings reviewer backfill':
                db._conn.execute("DELETE FROM org_settings WHERE section IN ('reviewer_agents','reviewer_agents_policy')")
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

        before = db._conn.total_changes
        journal_before = db._conn.execute("SELECT admission_journal_json FROM task_pause_controls WHERE root_task_id='TASK-1'").fetchone()[0]
        restore_pause_preparations(SimpleNamespace(_db=db))
        restore_pause_preparations(SimpleNamespace(_db=db))  # actual sweep re-run
        startup_expect(db._conn.total_changes == before, 'pause-identical-restore-and-sweep-zero-writes')
        assert db._conn.execute("SELECT admission_journal_json FROM task_pause_controls WHERE root_task_id='TASK-1'").fetchone()[0] == journal_before

        # Real recovery changes still persist once; the sweep then preserves
        # exact bytes and uncertainty. A job-owned prepared entry stays owned.
        fingerprint = dict(status='pending', block_kind=None, count=0,
            session_id=None, agent=None, fanout=None, jobs=None, parent=None,
            brief_digest='0' * 64)
        entry = dict(id='ordinary', org='scratch', root_task_id='TASK-1',
            task_id='TASK-1', agent='builder', session_id='synthetic-session',
            owner='ordinary', owner_identity={}, fingerprint=fingerprint,
            generation=0, phase='prepared', retry={}, context={},
            started_at=None, captured_generation=None)
        job = dict(entry, id='job', owner='job', session_id=None)
        with store.writer('TASK-1') as (_, row):
            row['journal']['entries'] = [entry, job]
            store.save_journal_uncommitted(row)
        before = db._conn.total_changes
        restore_pause_preparations(SimpleNamespace(_db=db))
        assert db._conn.total_changes == before + 1
        actual = store.row_uncommitted('TASK-1')['journal']['entries']
        assert actual == [dict(entry, phase='unknown', context={'execution_unknown': True}), job], actual
        before = db._conn.total_changes
        restored_bytes = store.row_uncommitted('TASK-1')['admission_journal_json']
        restore_pause_preparations(SimpleNamespace(_db=db))
        startup_expect(db._conn.total_changes == before
            and store.row_uncommitted('TASK-1')['admission_journal_json'] == restored_bytes,
            'pause-recovered-unknown-and-job-sweep-zero-writes')

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
assert not startup_failures, startup_failures
PY
