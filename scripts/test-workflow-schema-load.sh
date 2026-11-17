#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
uv run --frozen --python "${WORKFLOW_SCHEMA_PYTHON:-3.14}" python - "$@" <<'PY'
from pathlib import Path
from contextlib import ExitStack, contextmanager, closing
from contextvars import copy_context
from itertools import product
import json
import sqlite3
import stat
import sys
import logging
import os
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import DEFAULT, patch
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import io

# All new fence filesystem effects, including legacy fixture loads, stay private.
_schema_home = tempfile.TemporaryDirectory(prefix="workflow-schema-home-")
os.environ["HAPPYRANCH_DAEMON_HOME"] = str(Path(_schema_home.name).resolve())
# The real bootstrap helper must select SQLite's temp directory before its
# first SQLite work. Every shell fixture uses this isolated literal directory.
_schema_sqlite_temp = Path(_schema_home.name).resolve() / 'sqlite-temp'
_schema_sqlite_temp.mkdir(mode=0o700)
os.environ['SQLITE_TMPDIR'] = str(_schema_sqlite_temp)

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

# A focused phase selects the same retained corruption rows/assertions. The
# default/full matrix remains all four phases; no case is skipped or weakened.
returned_case = None
if len(sys.argv) == 2 and sys.argv[1].startswith('--returned-storage='):
    returned_case = sys.argv[1].split('=', 1)[1]
    sys.argv[1:] = ['--returned-storage']

start_context_case = None
START_CONTEXT_CASES = ('manager_context', 'thread_origin_manager', 'foreign_context',
    'direct_thread_refused', 'worker_refused', 'ended_or_recovery_sid_refused',
    'bearer_audience_refused', 'definition_selector', 'unavailable_causes', 'zero_writes', 'bounds')
if len(sys.argv) == 2 and sys.argv[1].startswith('--start-context='):
    start_context_case = sys.argv[1].split('=', 1)[1]
    assert start_context_case in ('all', *START_CONTEXT_CASES)
    sys.argv[1:] = ['--start-context']

bundled_case = None
if len(sys.argv) == 2 and sys.argv[1].startswith('--bundled-catalog='):
    bundled_case = sys.argv[1].split('=', 1)[1]
    assert bundled_case in ('all', 'second_bundle', 'pins', 'refusals', 'admission', 'registration')
    sys.argv[1:] = ['--bundled-catalog']

a2_case = None
if len(sys.argv) == 2 and sys.argv[1].startswith('--fresh-catalog='):
    a2_case = sys.argv[1].split('=', 1)[1]
    assert a2_case in ('route', 'historical', 'registration')
    sys.argv[1:] = ['--fresh-catalog']

bootstrap_startup_case = None
if len(sys.argv) == 2 and sys.argv[1].startswith('--bootstrap-startup='):
    bootstrap_startup_case = sys.argv[1].split('=', 1)[1]
    assert bootstrap_startup_case in ('success', 'integrity', 'backup', 'ex', 'history', 'history_precommit', 'commit_uncertain', 'kill', 'close', 'late_spawn',
                                     'discovery_cap', 'discovery_close', 'discovery_fence_close', 'discovery_descriptor_close', 'discovery_evidence_close',
                                     'discovery_registered_close', 'evidence_custody')
    sys.argv[1:] = ['--bootstrap-startup']

worker_case = None
if len(sys.argv) == 2 and sys.argv[1].startswith('--bootstrap-worker='):
    worker_case = sys.argv[1].split('=', 1)[1]
    assert worker_case in ('success', 'refuse_source', 'refuse_commit', 'cancel', 'kill',
                           'reserve', 'late_spawn', 'source_error', 'source_eof', 'attempt_eof', 'unsolicited',
                           'foreign_permit', 'duplicate_permit', 'qualification_false', 'late_spawn_after_timeout')
    sys.argv[1:] = ['--bootstrap-worker']

owners_case = None
if len(sys.argv) == 2 and sys.argv[1].startswith('--bootstrap-owners='):
    owners_case = sys.argv[1].split('=', 1)[1]
    assert owners_case in ('empty', 'producer_settled', 'producer_active', 'producer_unknown',
        'producer_quiescence', 'producer_rewrite', 'writer_settled', 'writer_active', 'writer_unknown',
        'writer_rewrite', 'pause_prepared', 'pause_unknown', 'thread_settled', 'thread_active',
        'thread_unknown', 'thread_missing', 'thread_orphan', 'thread_rewrite', 'acquisition',
        'deadline', 'sql_budget', 'query_only', 'task_active', 'task_terminal',
        'publication_lease', 'profile_lease', 'domain_acquisition', 'repository_opened',
        'repository_started', 'repository_finished', 'repository_returned', 'repository_outer',
        'repository_closed', 'repository_unsealed', 'repository_rewrite', 'repository_foreign', 'repository_acquisition')
    sys.argv[1:] = ['--bootstrap-owners']

backup_case = None
if len(sys.argv) == 2 and sys.argv[1].startswith('--bootstrap-backup='):
    backup_case = sys.argv[1].split('=', 1)[1]
    assert backup_case in ('none', 'wal', 'checkpointed_wal', 'source_init', 'destination_init', 'source_integrity',
                           'backup_integrity', 'inventory', 'file_sync', 'directory_sync',
                           'space', 'deadline', 'close', 'alias', 'shared_space', 'shared_exact',
                           'shared_minimum', 'database_space', 'temp_space', 'backup_space',
                           'rehearsal_space', 'separate_exact', 'volume_io')
    sys.argv[1:] = ['--bootstrap-backup']

rehearsal_case = None
if len(sys.argv) == 2 and sys.argv[1].startswith('--bootstrap-rehearsal='):
    rehearsal_case = sys.argv[1].split('=', 1)[1]
    assert rehearsal_case in ('none', 'first_budget', 'next_budget', 'install', 'metadata_alias',
                             'metadata_changed', 'backup_changed', 'space', 'scratch_location', 'deadline', 'load_init', 'authority', 'close')
    sys.argv[1:] = ['--bootstrap-rehearsal']

discovery_case = None
if len(sys.argv) == 2 and sys.argv[1].startswith('--bootstrap-discovery='):
    discovery_case = sys.argv[1].split('=', 1)[1]
    assert discovery_case in ('present', 'absent', 'ex_reselect', 'total', 'org', 'installed_expired',
                              'deadlines', 'busy', 'shared_close', 'selection_close', 'ex_acquire',
                              'transfer', 'load', 'load_fault', 'foreign_fence')
    sys.argv[1:] = ['--bootstrap-discovery']

selection_case = None
if len(sys.argv) == 2 and sys.argv[1].startswith('--bootstrap-selection='):
    selection_case = sys.argv[1].split('=', 1)[1]
    assert selection_case in ('absent', 'present', 'partial', 'empty_namespace', 'registered', 'registered_absent',
                              'registered_identity', 'intent', 'backup_verified', 'pre_commit', 'committed',
                              'verified', 'not_installed', 'malformed', 'namespace_alias', 'source_init',
                              'connection_close', 'descriptor_close', 'busy', 'deadline', 'ex_reselect', 'reason_parity')
    sys.argv[1:] = ['--bootstrap-selection']

install_case = None
if len(sys.argv) == 2 and sys.argv[1].startswith('--bootstrap-install='):
    install_case = sys.argv[1].split('=', 1)[1]
    assert install_case in ('none', 'rehearsal', 'rehearsal_close', 'backup_changed', 'metadata_changed',
                            'space', 'temp', 'temp_alias', 'temp_mode', 'writer_source', 'target_integrity', 'target_budget',
                            'permit', 'reserve', 'commit', 'writer_close', 'reopen', 'preservation', 'checkpoint')
    sys.argv[1:] = ['--bootstrap-install']

startup_case = None
if len(sys.argv) == 2 and sys.argv[1].startswith('--startup-isolation='):
    startup_case = sys.argv[1].split('=', 1)[1]
    assert startup_case in ('constructor', 'database', 'post_load', 'selector', 'profile',
                            'profile_database', 'profile_filesystem', 'profile_capture',
                            'main_owner', 'main_selector', 'main_sweep', 'main_unexpected', 'main_cancel', 'main_success', 'main_restore')
    sys.argv[1:] = ['--startup-isolation']

retained_phase = None
if len(sys.argv) == 2 and sys.argv[1].startswith('--retained-corruption-phase='):
    retained_phase = dict(first='first', local='between local', external='between external',
        uncommitted='between uncommitted')[sys.argv[1].split('=', 1)[1]]
    sys.argv[1:] = ['--retained-corruption']

assert sys.argv[1:] in ([], ['--start-context'], ['--bundled-catalog'], ['--returned-storage'], ['--bootstrap-startup'], ['--bootstrap-worker'], ['--bootstrap-owners'], ['--bootstrap-discovery'], ['--bootstrap-selection'], ['--bootstrap-install'], ['--bootstrap-rehearsal'], ['--bootstrap-backup'], ['--bootstrap-journal'], ['--bootstrap-precommit'], ['--startup-isolation'], ['--fresh-catalog'], ['--org-access-fence'], ['--org-access-openers'], ['--org-access-native'], ['--org-access-guardian'], ['--org-access-profile'], ['--setup-status'], ['--daily-integrity'], ['--install-integrity'], ['--bounded-acquisition'], ['--trace-custody'], ['--sequential-reuse'], ['--sequential-load'], ['--sequential-freshness'], ['--sequential-faults'], ['--sequential-sql-exhaustion'], ['--sequential-corruption'], ['--retained-corruption'], ['--document-coding-corruption'], ['--coding-authority-corruption'], ['--document-native-corruption'], ['--author-handoff-corruption'], ['--document-revision-corruption'], ['--coding-history-corruption'], ['--physical-corruption'], ['--sequential-uncommitted'], ['--i2-cutover'], ['--i2-native']), 'unknown schema-load selection'


print('SCHEMA_RUNTIME', json.dumps(dict(python=sys.version, executable=sys.executable, sqlite=sqlite3.sqlite_version,
    source=sys.modules[Database.__module__].__file__)), flush=True)

# Private storage fixtures shared by returned-tail and retained-reader cases.
from runtime.infrastructure.workflow_schema import (
    initialize_complete_org_schema, install_sequential_schema,
    sequential_storage_inventory, _coding_org_identity, _schema_snapshot,
)
from runtime.workflows.sequential_history import read_sequential_history
from runtime.workflows import sequential_history as seq_history

@contextmanager
def sequential_fixture():
    with tempfile.TemporaryDirectory(prefix='sequential-load-reuse-') as scratch:
        root = Path(scratch) / 'orgs' / 'scratch'
        (root / 'org/agents').mkdir(parents=True)
        db = Database(OrgPaths(root).db_path)
        try:
            initialize_complete_org_schema(db, expected_org_slug='scratch', layout='J')
            install_pause_schema(db, org_slug='scratch')
            install_task_ownership(db, org_slug='scratch')
            db.insert_task(TaskRecord(id='TASK-seq-load', brief='synthetic ordinary owner'))
            with _schema_snapshot(db._conn):
                identity = _coding_org_identity(db._conn, expected_org_slug='scratch',
                                               expected_path=str(db.db_path))
                inventory = sequential_storage_inventory(db._conn)
            def offline():
                assert _coding_org_identity(db._conn, expected_org_slug='scratch',
                                            expected_path=str(db.db_path)) == identity
            assert install_sequential_schema(db._conn, expected_org_slug='scratch',
                expected_path=str(db.db_path), source_identity=identity,
                source_inventory=inventory, offline_check=offline) == 'installed'
            sql = []
            with db.observe_trace(sql.append):
                yield db, root, sql
        finally:
            db.close()

# Synthetic storage only: compiler/parser and the unmodified public reader
# validate the nonempty graph. These rows confer no execution authority.
def populate_sequential(db, *, settled=False):
    import hashlib
    from runtime.workflows.sequential_definition import compile_definition, parse_start, parse_result
    encode = seq_history._bytes
    digest = lambda raw: hashlib.sha256(raw).hexdigest()
    limits = dict(content_returns=0, max_content_epochs=1, max_visits=1, max_events=4)
    raw = encode(dict(format='workflow-sequential-definition@1', identity='release/synthetic/load',
        version=1, description='Synthetic nonempty load-reader fixture',
        required_inputs=[dict(name='request', contract='bounded-text@1', required=True)],
        roles=dict(author=dict(kind='agent', executable=True, eligibility='worker', independent_of=[])),
        entry_node='author', nodes=[dict(id='author', role='author', responsibilities=['Write a fixture'],
            inputs=[dict(name='request', source='run.request', required=True)],
            output_contract=dict(slot='document', type='immutable-document@1'),
            completion_checks=['immutable-document@1'],
            outcomes=dict(produced=dict(action='terminal', condition='done'),
                          BLOCK=dict(action='fail', code='blocked')))],
        limits=limits, terminal_conditions=dict(done=dict(output='document',
            requires=['immutable-document@1', 'native-success-and-positive-outer-settlement@1']))))
    compiled = compile_definition(raw, source_digest=digest(raw))
    pin = compiled.pin
    bindings = [dict(role='author', principal='synthetic-author')]
    snapshot = encode(dict(format='workflow-sequential-settings-storage@1',
        definition=pin.model_dump(), bindings=bindings, limits=limits, admission='Allowed'))
    request = encode(dict(format='workflow-sequential-settings-operation-storage@1',
        operation_key='synthetic-settings', principal='synthetic-manager', settings_id='settings',
        expected_generation=0, snapshot_digest=digest(snapshot)))
    receipt = encode(dict(format='workflow-sequential-settings-receipt-storage@1',
        operation_id='settings-op', settings_id='settings', generation=1, snapshot_digest=digest(snapshot)))
    start = encode(dict(format='workflow-sequential-start@1', operation_key='synthetic-start',
        definition=pin.model_dump(), settings=dict(generation=1, snapshot_digest=digest(snapshot)),
        request='Synthetic stored request', inputs=[], bindings=bindings, lower_limits=limits))
    parse_start(start)
    for task, parent, agent in (('source', None, 'synthetic-manager'),
                                ('carrier', 'source', 'synthetic-manager'),
                                ('visit-task', 'carrier', 'synthetic-author')):
        db.insert_task(TaskRecord(id=task, parent_task_id=parent, assigned_agent=agent,
                                  brief='synthetic storage fixture'))
    conn = db._conn
    source_result, visit_result = [conn.execute(
        'INSERT INTO task_results(task_id,agent,session_id,created_at) VALUES (?,?,?,?)',
        (task, agent, session, '2026-11-14T00:00:00+00:00')).lastrowid
        for task, agent, session in (('source', 'synthetic-manager', 'source-session'),
                                     ('visit-task', 'synthetic-author', 'visit-session'))]
    selection = encode(dict(outputs=[]))
    content = b'Synthetic immutable document'
    output = dict(slot='document', contract='immutable-document@1', epoch=1,
        producer_visit='visit', task_id='visit-task', session_id='visit-session',
        result_id=visit_result, digest=digest(content))
    envelope = encode(dict(format='workflow-sequential-result@1', run_id='run', visit_id='visit',
        definition_digest=pin.definition_digest, input_selection_digest=digest(selection),
        outcome='produced', outputs=[output], findings=[], capability_receipts=(
            [dict(check='immutable-document@1', evidence_digest=digest(content))] if settled else [])))
    parse_result(envelope)
    event = encode(dict(format='workflow-sequential-storage-event@1', kind='admitted',
        run_id='run', sequence=1, operation_key='synthetic-start', prior_digest='0' * 64))
    notification = encode(dict(format='workflow-sequential-notification-storage@1',
        notification_id='outbox', event_id='event', run_id='run', visit_id='visit',
        task_id='visit-task', kind='visit_queued'))
    conn.execute('PRAGMA defer_foreign_keys=ON')
    conn.execute('INSERT INTO workflow_seq_definitions VALUES (?,?,?,?,?,?,?,?,?,?)',
        ('definition', pin.identity, pin.version, raw, pin.definition_digest, pin.source_digest,
         pin.compiler, pin.validator, pin.interpreter, pin.capabilities))
    conn.execute('INSERT INTO workflow_seq_settings VALUES (?,?,?,?,?,?,?)',
        ('settings', 'scratch', 'definition', pin.identity, 1, snapshot, digest(snapshot)))
    conn.execute('INSERT INTO workflow_seq_settings_operations VALUES (?,?,?,?,?,?,?,?,?)',
        ('settings-op', 'scratch', 'synthetic-manager', 'synthetic-settings', request,
         digest(request), 'settings', receipt, digest(receipt)))
    conn.execute('INSERT INTO workflow_seq_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        ('run', 'scratch', 'definition', 'settings', 'source', 'synthetic-manager', 'source-session',
         source_result, 'carrier', 'synthetic-start', start, digest(start), encode(limits),
         'visit', 'active', 1, 0, 1, 1, 0, 'event'))
    conn.execute('INSERT INTO workflow_seq_visits VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        ('visit', 'run', 'author', 1, 1, None, 'visit-task', 'synthetic-author', 'visit-session',
         visit_result, envelope, digest(envelope), encode(dict(checks=['immutable-document@1'] + (['native-success-and-positive-outer-settlement@1'] if settled else []))),
         selection, digest(selection), 'waiting', 1))
    conn.execute('INSERT INTO workflow_seq_events VALUES (?,?,?,?,?,?,?,?,?,?)',
        ('event', 'run', 1, 'synthetic-start', 'admitted', '0' * 64, event, digest(event), None, None))
    conn.execute('INSERT INTO workflow_seq_outputs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
        ('output', 'run', 'visit', 'document', 1, 'immutable-document@1', 'visit-task',
         'visit-session', visit_result, content, digest(content), encode(dict(roles=['author']))))
    conn.execute('INSERT INTO workflow_seq_evidence VALUES (?,?,?,?,?,?,?,?,?,?)',
        ('evidence', 'run', 'visit', 'review', b'{}', digest(b'{}'),
         'visit-task', 'synthetic-author', 'visit-session', visit_result))
    conn.execute('INSERT INTO workflow_seq_outbox VALUES (?,?,?,?,?,?,?,?)',
        ('outbox', 'run', 'event', 'visit_queued', 'visit', 'visit-task', notification, digest(notification)))
    for task, kind, owner in (('source', 'seq_source_waiting', 'run'),
                              ('carrier', 'seq_carrier', 'run'), ('visit-task', 'seq_visit', 'visit')):
        conn.execute('UPDATE workflow_task_ownership SET version=2,kind=?,owner_id=? WHERE task_id=?',
                     (kind, owner, task))
    conn.commit()
    history = read_sequential_history(conn, expected_org_slug='scratch')
    assert history.status == 'storage readable' and history.run_ids == ('run',)
    for table in seq_history.SEQUENTIAL_TABLES:
        assert conn.execute('SELECT count(*) FROM ' + table).fetchone()[0] > 0, table + ' fixture is empty'

def returned_storage_absence():
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

def populate_returned(db):
    """Valid private archival history, serialized with the real reducer/writers."""
    from runtime.workflows import sequential_engine as engine
    from runtime.workflows.sequential_definition import compile_definition, parse_start, parse_result
    from runtime.workflows.sequential_reducer import ActiveVisit, AuthenticatedResult, RunState, reduce_result
    from runtime.workflows.sequential_contracts import Counters, Limits
    populate_sequential(db, settled=True)
    conn = db._conn
    one = lambda table: dict(conn.execute('SELECT * FROM ' + table).fetchone())
    run, visit, definition = (one(table) for table in
        ('workflow_seq_runs', 'workflow_seq_visits', 'workflow_seq_definitions'))
    compiled = compile_definition(definition['definition_bytes'], source_digest=definition['source_digest'])
    start = parse_start(run['start_bytes'])
    envelope = parse_result(visit['result_bytes'])
    checks = ['immutable-document@1', 'native-success-and-positive-outer-settlement@1']
    decision = reduce_result(compiled, RunState(run_id='run', definition_digest=compiled.pin.definition_digest,
        original_limits=Limits.model_validate_json(run['original_limits_bytes']), lower_limits=start.lower_limits,
        counters=Counters(epoch=1, content_returns=0, visits=1, events=0),
        active_visit=ActiveVisit(node='author', visit_id='visit', task_id='visit-task', session_id='visit-session',
            epoch=1, input_selection_digest=visit['input_selection_digest']),
        selections=[], findings=[], receipts=[]), AuthenticatedResult(task_id='visit-task',
            session_id='visit-session', result_id=visit['result_id'], envelope=envelope, verified_checks=checks))
    prior = one('workflow_seq_events')['event_digest']
    raw = seq_history._bytes(dict(format='workflow-sequential-transition@1', run_id='run',
        definition_digest=compiled.pin.definition_digest, source_visit='visit', source_result=visit['result_id'],
        result_digest=decision.result_digest, output_digest=envelope.outputs[0].digest, node='author',
        outcome='produced', prior_cas=0, prior_event_digest=prior, before=decision.before.model_dump(),
        after=decision.after.model_dump(), invalidated=[], edge=decision.edge.model_dump(), successor=None))
    conn.execute("UPDATE workflow_seq_visits SET state='completed',selected=0")
    event = engine._event(conn, run, 'synthetic-transition', 'transition', prior, raw=raw,
                          source_visit='visit', source_result=visit['result_id'])
    engine._move_run(conn, run, event, state='completed', active_visit_id=None, epoch=1, content_returns=0, visits=1)
    run = one('workflow_seq_runs')
    prior = hashlib.sha256(raw).hexdigest()
    event = engine._event(conn, run, 'synthetic-return', 'returned', prior)
    engine._move_run(conn, run, event, state='completed', active_visit_id=None, epoch=1, content_returns=0, visits=1)
    engine._outbox(conn, run, event, 'run_return', None, 'source')
    conn.execute("UPDATE tasks SET status='completed',completed_at=? WHERE id IN ('source','carrier','visit-task')",
                 ('2026-11-14T01:00:00+00:00',))
    conn.commit()
    assert read_sequential_history(conn, expected_org_slug='scratch').run_ids == ('run',)


def returned_tail_cases(selected):
    import asyncio
    import threading
    from runtime.models import ThreadRecord, ThreadMessageKind, TaskStatus, ThreadInvocationStatus
    from runtime.infrastructure.audit_logger import AuditLogger
    from runtime.workflows.sequential_start import _ordinary_source, precheck_completion
    from runtime.workflows.sequential_history import _ReadBudget, _sql_budget
    from runtime.workflows import sequential_start
    from runtime.orchestrator.teams import TeamsRegistry
    from runtime.daemon.sessions import SessionTracker

    cases = ('thread_source', 'thread_ancestor', 'thread_root_return', 'thread_ancestor_nonroot_return',
        'parked_no_tail', 'duplicate_return', 'concurrent_return', 'restart_before_tail', 'restart_marker_only',
        'restart_pending_or_settled_tail', 'composed_archive_recheck', 'marker_read_unavailable')
    assert selected in (None, 'all', 'absence', *cases), selected
    if selected in (None, 'all', 'absence'):
        returned_storage_absence()
    for case in cases:
        if selected not in (None, 'all', case):
            continue
        variants = ('pending', 'consumed', 'failed') if case == 'restart_pending_or_settled_tail' else ('base',)
        if case == 'thread_root_return':
            variants = ('root', 'revisit', 'failed', 'cancelled', 'superseded')
        if case == 'marker_read_unavailable':
            variants = ('unreadable', 'malformed', 'over_budget')
        for variant in variants:
            with sequential_fixture() as (db, root, sql):
                audit = AuditLogger(db)
                db.insert_thread(ThreadRecord(id='THR-original', subject='Private original', turn_cap=1, turns_used=1))
                if case in ('thread_source', 'thread_ancestor'):
                    db.insert_task(TaskRecord(id='ordinary', assigned_agent='synthetic-manager', team='engineering',
                        status='in_progress', brief='Private ordinary source',
                        dispatched_from_thread_id='THR-original' if case == 'thread_source' else None))
                    if case == 'thread_ancestor':
                        db.insert_task(TaskRecord(id='ordinary-parent', assigned_agent='synthetic-manager',
                            team='engineering', status='in_progress', brief='Private parent',
                            dispatched_from_thread_id='THR-original'))
                        db._conn.execute("UPDATE tasks SET parent_task_id='ordinary-parent' WHERE id='ordinary'")
                        db._conn.commit()
                    (root / 'org/teams.yaml').write_text(
                        'teams:\n  engineering:\n    manager: synthetic-manager\n    workers: []\n')
                    org = SimpleNamespace(slug='scratch', db=db, teams=TeamsRegistry.load(root))
                    changes = db._conn.total_changes
                    budget = _ReadBudget()
                    with db.coherent_read_view() as conn, _sql_budget(conn, budget):
                        source, ancestor, seen = _ordinary_source(conn, org, 'ordinary', budget)
                    assert source['id'] == 'ordinary' and len(seen) == (2 if case == 'thread_ancestor' else 1)
                    assert ancestor['dispatched_from_thread_id'] == 'THR-original'
                    # Direct thread invocation is not ordinary task-session authority.
                    org.sessions = SessionTracker()
                    org.sessions.set_active('THR-original', 'synthetic-manager', 'thread-session', org_slug='scratch')
                    try:
                        precheck_completion(org, {}, task_id='ordinary', principal='synthetic-manager',
                            session_id='thread-session', status='completed', waiting_on_job_ids=None)
                    except sequential_start.SequentialRefusal:
                        pass
                    else:
                        raise AssertionError('direct_thread_refused: accepted non-task session')
                    assert db._conn.total_changes == changes, case + ' wrote during ancestry admission'
                    print('PASS R1a', case, 'direct_thread_refused zero writes', flush=True)
                    continue

                populate_returned(db)
                original = 'source'
                if case == 'thread_root_return' and variant == 'revisit':
                    original = 'predecessor'
                    db.insert_task(TaskRecord(id=original, assigned_agent='synthetic-manager', brief='Private predecessor',
                        status='failed', dispatched_from_thread_id='THR-original'))
                    db._conn.execute("UPDATE tasks SET revisit_of_task_id=? WHERE id='source'", (original,))
                    db._conn.commit()
                if case == 'thread_ancestor_nonroot_return':
                    original = 'parent'
                    db.insert_task(TaskRecord(id=original, assigned_agent='synthetic-manager', brief='Private waiting parent',
                        status='in_progress', block_kind='delegated', dispatched_from_thread_id='THR-original'))
                    db._conn.execute("UPDATE tasks SET parent_task_id=? WHERE id='source'", (original,))
                    db._conn.commit()
                else:
                    db._conn.execute('UPDATE tasks SET dispatched_from_thread_id=? WHERE id=?', ('THR-original', original))
                    db._conn.commit()
                if case == 'thread_root_return' and variant in ('failed', 'cancelled', 'superseded'):
                    db._conn.execute("UPDATE tasks SET status=? WHERE id='source'", (variant,))
                    db._conn.commit()
                if case == 'parked_no_tail':
                    db._conn.execute("UPDATE tasks SET status='in_progress',block_kind='delegated',completed_at=NULL WHERE id='source'")
                    db._conn.commit()
                audit.log_thread_dispatch('THR-original', task_id=original, dispatcher='synthetic-manager',
                                          target_agent='synthetic-manager', team='engineering')
                histories, task_wakes, thread_jobs = [], [], []
                class ThreadQueue:
                    async def put(self, job):
                        thread_jobs.append(job.invocation_token)
                class Queue:
                    thread_queue = ThreadQueue()
                    def enqueue_if_absent(self, slug, task, publisher=None):
                        if task not in task_wakes:
                            task_wakes.append(task)
                    def put_nowait(self, slug, task):
                        task_wakes.append(task)
                queue = Queue()
                loop = asyncio.new_event_loop()
                runner = threading.Thread(target=loop.run_forever)
                runner.start()
                orch = SimpleNamespace(_db=db, _audit=audit, _slug='scratch', _main_loop=loop,
                    _update_task_history=histories.append)
                dispatcher = SequentialDispatcher(SimpleNamespace(db=db, slug='scratch', orchestrator=orch))
                db._workflow_sequential_dispatch = dispatcher
                def deliver():
                    return sequential_return.notify_returned(dispatcher, 'run', queue=queue)
                def restart():
                    rediscover_returned(dispatcher, sequential=True, queue=queue)
                def snapshot():
                    asyncio.run_coroutine_threadsafe(asyncio.sleep(0), loop).result(3)
                    messages = [json.loads(row[0]) for row in db._conn.execute(
                        "SELECT system_payload_json FROM thread_messages WHERE thread_id='THR-original' AND kind='system'")]
                    invocations = [tuple(row) for row in db._conn.execute(
                        "SELECT invocation_token,status,triggering_seq FROM thread_invocations WHERE purpose='task_followup' ORDER BY id")]
                    return messages, invocations, db.get_thread('THR-original').turn_cap, list(thread_jobs)
                try:
                    if case == 'restart_before_tail':
                        assert snapshot() == ([], [], 1, [])
                        restart()
                    elif case == 'restart_marker_only':
                        with patch.object(db, 'mint_followup_invocation_with_cap_extend', side_effect=RuntimeError('private crash after marker')):
                            try:
                                deliver()
                            except RuntimeError as exc:
                                assert str(exc) == 'private crash after marker'
                            else:
                                raise AssertionError('marker-only crash seam not reached')
                        before = snapshot()
                        assert len(before[0]) == 1 and before[1:] == ([], 1, [])
                        restart()
                        assert snapshot() == before, 'restart_marker_only reminted partial tail'
                    elif case == 'concurrent_return':
                        entered, competing, release = threading.Event(), threading.Event(), threading.Event()
                        append = db.append_thread_message
                        def interrupted_append(*args, **kwargs):
                            entered.set()
                            assert release.wait(3), 'concurrent tail release timeout'
                            return append(*args, **kwargs)
                        errors = []
                        def worker(second=False):
                            try:
                                if second:
                                    competing.set()
                                deliver()
                            except BaseException as exc:
                                errors.append(exc)
                        with patch.object(db, 'append_thread_message', side_effect=interrupted_append):
                            first = threading.Thread(target=worker)
                            second = threading.Thread(target=worker, args=(True,))
                            first.start()
                            try:
                                assert entered.wait(3), 'first concurrent marker not reached'
                                second.start()
                                assert competing.wait(3)
                            finally:
                                release.set()
                                first.join(5)
                                if second.ident is not None:
                                    second.join(5)
                            assert not first.is_alive() and not second.is_alive() and not errors, errors
                    elif case == 'marker_read_unavailable':
                        if variant == 'unreadable':
                            denied = []
                            def deny(action, table, column, database, trigger):
                                if action == sqlite3.SQLITE_READ and table == 'thread_messages' and column == 'system_payload_json':
                                    denied.append((table, column))
                                    return sqlite3.SQLITE_DENY
                                return sqlite3.SQLITE_OK
                            db._conn.set_authorizer(deny)
                        else:
                            db.append_thread_message(thread_id='THR-original', speaker='synthetic-manager', kind=ThreadMessageKind.SYSTEM,
                                system_payload=dict(unrelated=True))
                            db._conn.execute('UPDATE thread_messages SET system_payload_json=?',
                                ('{' if variant == 'malformed' else json.dumps(dict(padding='x' * 1048577)),))
                            db._conn.commit()
                        before = db._conn.execute('SELECT count(*) FROM thread_invocations').fetchone()[0]
                        message_count = db._conn.execute('SELECT count(*) FROM thread_messages').fetchone()[0]
                        try:
                            deliver()
                        except (ValueError, sqlite3.Error):
                            pass
                        else:
                            raise AssertionError('marker_read_unavailable treated uncertainty as absence')
                        finally:
                            db._conn.set_authorizer(None)
                        assert db._conn.execute('SELECT count(*) FROM thread_invocations').fetchone()[0] == before
                        assert db.get_thread('THR-original').turn_cap == 1
                        assert db._conn.execute('SELECT count(*) FROM thread_messages').fetchone()[0] == message_count
                        assert histories == ['carrier', 'source'], 'marker refusal happened before admitted return tail'
                        if variant == 'unreadable':
                            assert denied, 'marker denial seam not reached'
                        print('PASS R1a', case, variant, 'no mint/cap', flush=True)
                        continue
                    else:
                        if case == 'restart_pending_or_settled_tail' and variant == 'pending':
                            orch._main_loop = None  # Native marker/mint committed before lost queue wiring.
                        assert deliver()
                        orch._main_loop = loop
                    if case in ('parked_no_tail', 'thread_ancestor_nonroot_return'):
                        assert snapshot() == ([], [], 1, []), case + ' posted root followup'
                        assert task_wakes == (['parent'] if case == 'thread_ancestor_nonroot_return' else []), (case, task_wakes, db.get_task('source'), db.get_task('parent'))
                    elif case != 'restart_marker_only':
                        messages, invocations, cap, queued = snapshot()
                        expected_queued = 0 if case == 'restart_pending_or_settled_tail' and variant == 'pending' else 1
                        assert len(messages) == len(invocations) == 1 and len(queued) == expected_queued, case + ' missing or duplicate marker/mint/queue'
                        assert messages[0]['task_id'] == 'source' and messages[0]['original_task_id'] == original
                        assert messages[0]['status'] == db.get_task('source').status.value
                        assert queued == ([invocations[0][0]] if expected_queued else []) and cap == 2, case + ' cap/token mismatch'
                        if case == 'restart_pending_or_settled_tail':
                            if variant == 'consumed':
                                assert db.consume_invocation(invocations[0][0])
                            elif variant == 'failed':
                                assert db.fail_invocation(invocations[0][0], status=ThreadInvocationStatus.FAILED, decline_reason='restart_orphan')
                        if case == 'composed_archive_recheck':
                            db.insert_thread(ThreadRecord(id='THR-composed', subject='Private composed',
                                composed_by='synthetic-manager', composed_from_task_id='source'))
                            admitted = []
                            def archive(thread, trigger):
                                admitted.append((thread, trigger, db.archive_thread_and_reset_sessions(thread,
                                    summary='', audit_scope_id=thread, audit_agent='orchestrator', auto_archive_trigger=trigger,
                                    append_archive_message=True)))
                            orch._thread_archive_notify = archive
                            restart()
                            assert admitted and admitted[-1] == ('THR-composed', 'composed_task_terminal', None), 'composed_archive_recheck lost unresolved admission'
                            db._conn.execute("UPDATE threads SET resolved_at=?,resolved_by=?,resolved_seq=1 WHERE id='THR-composed'",
                                             ('2026-11-14T02:00:00+00:00', 'synthetic-manager'))
                            db._conn.commit()
                            restart()
                            assert len(admitted) == 2 and admitted[-1][2] is not None, 'composed_archive_recheck lost fresh resolved admission'
                            assert db.get_thread('THR-composed').status.value == 'archived'
                        before = snapshot()
                        if case in ('duplicate_return', 'restart_pending_or_settled_tail'):
                            deliver()
                            restart()
                            assert snapshot() == before, case + ' repeated marker/cap/mint/queue'
                    assert 'source' in histories and 'carrier' in histories
                    print('PASS R1a', case, variant, 'rows/invocations/cap/queue', snapshot(), flush=True)
                finally:
                    asyncio.run_coroutine_threadsafe(asyncio.sleep(0), loop).result(3)
                    loop.call_soon_threadsafe(loop.stop)
                    runner.join(5)
                    loop.close()


if sys.argv[1:] == ['--returned-storage']:
    returned_tail_cases(returned_case)
    raise SystemExit(0)



# R1c uses private package data and real production readers/writers, with no launch.
if sys.argv[1:] in ([], ['--bundled-catalog'], ['--start-context']):
    import asyncio
    from dataclasses import replace
    from runtime.daemon.routes.orgs import _seed_skeleton
    from runtime.runtime import RuntimeDir
    from runtime.infrastructure.workflow_schema import initialize_fresh_org_database
    from runtime.orchestrator.org_config import initialize_fresh_reviewer_agents
    from runtime.orchestrator.host_supervisor import HostSessionSupervisor, canary_policy
    from runtime.platform.passthrough_backend import PassthroughBackend
    from runtime.workflows.profile_coordinator import ProfileCoordinator
    from runtime.workflows import sequential_release as release, sequential_builtin as builtin, sequential_start as start
    from runtime.workflows.sequential_definition import compile_definition, parse_start
    from runtime.workflows.sequential_history import StorageSettings, _ReadBudget, _bytes
    from runtime.workflows.sequential_venue import SequentialVenueUnavailable
    from runtime.workflows.coding_start import require_start_ready
    from scripts.setup_sequential_coding import _write

    def r1c_refuses(label, action, expected):
        try:
            action()
        except (ValueError, RuntimeError) as exc:
            assert str(exc) == expected, (label, 'wrong refusal', str(exc), expected)
        else:
            raise AssertionError(label + ': unexpectedly admitted')
        print('PASS R1c', label, expected, flush=True)

    async def start_context_cases(org, runtime, home, coding, save_settings):
        import httpx
        from runtime.models import ThreadRecord, ThreadInvocationPurpose
        from runtime.daemon.app import create_app
        from runtime.daemon.state import DaemonState
        from runtime.workflows.sequential_run_validation import _pins, _selected_layout
        conn = org.db._conn
        state = DaemonState(runtime=runtime, settings=Settings())
        state.orgs['scratch'] = org
        state.profile_coordinator = org._profile_coordinator
        app = create_app(state)
        (home / 'daemon.token').write_text('synthetic-start-context-founder')
        org.db.update_task('private-source', current_session_id='context-manager')
        org.sessions.set_active('private-source', 'r1c_manager', 'context-manager', org_slug='scratch')
        org.db.insert_thread(ThreadRecord(id='THR-private', subject='Private discovery thread'))
        for task, principal in (('worker-source', 'r1c_dev'), ('thread-source', 'r1c_manager')):
            org.db.insert_task(TaskRecord(id=task, assigned_agent=principal, team='engineering',
                task_type='subtask' if task == 'worker-source' else 'task', status='in_progress',
                brief='Private discovery audience', current_session_id='context-' + task,
                origin_thread_id='THR-private' if task == 'thread-source' else None))
            org.sessions.set_active(task, principal, 'context-' + task, org_slug='scratch')
        url = '/api/v1/orgs/scratch/workflows/sequential/start-context'
        cli = {'x-happyranch-surface': 'cli'}
        selector = 'release/happyranch/coding@2'
        chosen = start_context_case or 'all'

        def inventory():
            # All tables, schema and exact row bytes, including historical/native
            # rows outside the sequential inventory; private bounded fixtures only.
            return tuple(conn.iterdump())

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://asgi') as client:
            async def read(sid='context-manager', *, headers=None, params=None, path=url,
                           status=200, code=None, content=None):
                before, changes, statements = inventory(), conn.total_changes, []
                conn.set_trace_callback(statements.append)
                try:
                    response = await client.request('GET', path, headers=cli if headers is None else headers,
                        params=[('session_id', sid)] if params is None else params, content=content)
                finally:
                    conn.set_trace_callback(None)
                assert conn.total_changes == changes and inventory() == before, 'zero_writes: row/schema mutation'
                assert not any(sql.lstrip().split(' ', 1)[0].upper() in
                    ('INSERT', 'UPDATE', 'DELETE', 'REPLACE', 'COMMIT', 'END', 'CREATE', 'DROP', 'ALTER')
                    for sql in statements), ('zero_writes: forbidden SQL', statements)
                assert response.status_code == status, ('start-context route', response.status_code, response.text, status)
                if path == url:
                    assert response.headers.get('cache-control') == 'no-store', ('discovery refusal must not cache', sid, response.text, dict(response.headers))
                body = response.json()
                if code is not None:
                    assert body['detail']['code'] == code, (body, code)
                return body

            # Absence is Disabled/unset. No successful-authority or route mock.
            body = await read()
            empty = body['definitions'][0]
            assert empty['admission'] == 'Disabled' and empty['defaults'] == [] and empty['settings'] is None
            assert empty['unavailable_cause'] == 'sequential_settings_absent'
            cmd = save_settings(coding)
            setting_id = conn.execute('SELECT id FROM workflow_seq_settings WHERE generation=1').fetchone()[0]
            with org.db.coherent_read_view() as reader:
                budget = _ReadBudget()
                compiled, setting, snapshot = _pins(reader, _selected_layout(reader, budget),
                    org_slug='scratch', definition_id=conn.execute('SELECT id FROM workflow_seq_definitions').fetchone()[0],
                    settings_id=setting_id, budget=budget)

            async def manager_context(sid='context-manager'):
                body = await read(sid)
                item = body['definitions'][0]
                assert item['selector'] == selector and item['pin'] == compiled.pin.model_dump(mode='json')
                assert item['settings']['generation'] == setting['generation'] == cmd.settings.generation
                assert item['settings']['snapshot_digest'] == setting['snapshot_digest'] == cmd.settings.snapshot_digest
                assert item['settings']['snapshot'] == snapshot.model_dump(mode='json')
                assert item['admission'] == snapshot.admission == 'Allowed'
                assert item['defaults'] == [b.model_dump(mode='json') for b in snapshot.bindings]
                assert item['limits'] == snapshot.limits.model_dump(mode='json') and item['unavailable_cause'] is None
                assert item['definition_limits'] == compiled.definition.limits.model_dump(mode='json')
                assert {r['role']: r['requirement'] for r in item['roles']} == {
                    role: requirement.model_dump(mode='json') for role, requirement in compiled.definition.roles}
                for node in compiled.definition.nodes:
                    role = next(r for r in item['roles'] if r['role'] == node.role)
                    assert all(text in role['responsibilities'] for text in node.responsibilities)
                assert body['start_format'] == 'workflow-sequential-start@1' and body['max_runs'] == 1
                assert body['assignments'] == 'fixed' and body['source_semantics'] == 'source-terminal'

            for case in START_CONTEXT_CASES:
                if chosen not in ('all', case):
                    continue
                if case == 'manager_context':
                    await manager_context()
                elif case == 'thread_origin_manager':
                    await manager_context('context-thread-source')
                elif case == 'foreign_context':
                    for task, principal, slug in (('private-source', 'r1c_manager', 'foreign'),
                            ('absent-task', 'r1c_manager', 'scratch'), ('private-source', 'r1c_dev', 'scratch')):
                        sid = 'foreign-' + task + principal + slug
                        org.sessions.set_active(task, principal, sid, org_slug=slug)
                        await read(sid, status=404 if task == 'absent-task' else 403,
                            code='task_not_found' if task == 'absent-task' else 'sequential_task_session_required')
                    # A task/principal query cannot override the registry lookup.
                    org.sessions.set_active('private-source', 'r1c_manager', 'context-manager', org_slug='scratch')
                    await read(params=[('session_id', 'context-manager'), ('task_id', 'thread-source')],
                        status=422, code='sequential_start_context_invalid')
                elif case == 'direct_thread_refused':
                    invocation = org.db.mint_thread_invocation(thread_id='THR-private', agent_name='r1c_manager',
                        triggering_seq=0, purpose=ThreadInvocationPurpose.REPLY)
                    assert org.db.get_pending_invocation(invocation.invocation_token) is not None
                    # A genuine direct thread token supplies no task session.
                    await read(invocation.invocation_token, status=403, code='sequential_task_session_required')
                elif case == 'worker_refused':
                    await read('context-worker-source', status=403, code='sequential_task_session_required')
                    # A worker with task_type=task still has no manager authority.
                    conn.execute("UPDATE tasks SET task_type=? WHERE id=?", ('task', 'worker-source'))
                    conn.commit()
                    await read('context-worker-source', status=403, code='sequential_task_session_required')
                    conn.execute("UPDATE tasks SET task_type=? WHERE id=?", ('subtask', 'worker-source'))
                    conn.commit()
                elif case == 'ended_or_recovery_sid_refused':
                    org.db.update_task('private-source', current_session_id='recovery')
                    org.sessions.register_recovery_session('private-source', 'r1c_manager', 'recovery', org_slug='scratch')
                    await read('recovery', status=403, code='sequential_task_session_required')
                    await read('context-manager', status=403, code='sequential_task_session_required')
                    org.db.update_task('private-source', current_session_id='context-manager')
                    org.sessions.set_active('private-source', 'r1c_manager', 'context-manager', org_slug='scratch')
                    org.db.update_task('private-source', status='completed')
                    await read(status=403, code='sequential_task_session_required')
                    org.db.update_task('private-source', status='in_progress')
                    conn.execute("UPDATE tasks SET task_type=? WHERE id=?", ('subtask', 'private-source'))
                    conn.commit()
                    await read(status=403, code='sequential_task_session_required')
                    conn.execute("UPDATE tasks SET task_type=? WHERE id=?", ('task', 'private-source'))
                    conn.commit()
                    org.sessions.clear('private-source', 'r1c_manager')
                    await read(status=403, code='sequential_task_session_required')
                    org.sessions.set_active('private-source', 'r1c_manager', 'context-manager', org_slug='scratch')
                elif case == 'bearer_audience_refused':
                    for headers in ({}, {**cli, 'authorization': 'Bearer synthetic-start-context-founder'},
                                    {**cli, 'origin': 'http://browser'}):
                        await read(headers=headers, status=403, code='sequential_task_session_required')
                    founder = '/api/v1/orgs/scratch/workflows/sequential/definitions'
                    await read(path=founder, params=[], status=401)
                    body = await read(path=founder, params=[], headers={
                        'authorization': 'Bearer synthetic-start-context-founder'})
                    assert body['definitions'][0]['pin'] == coding.pin.model_dump(mode='json')
                elif case == 'definition_selector':
                    await read(params=[('session_id', 'context-manager'), ('definition', selector)])
                    for value in ('release/happyranch/coding@1', 'coding.v1', selector + '0',
                                  'release/unknown/workflow@2', selector + ':tampered'):
                        await read(params=[('session_id', 'context-manager'), ('definition', value)],
                            status=409, code='coding_fresh_start_retired' if value.endswith('@1')
                                else 'sequential_definition_unavailable')
                    await read(params=[('session_id', 'context-manager'), ('session_id', 'context-manager')],
                        status=422, code='sequential_start_context_invalid')
                    await read(content=b'x', status=422, code='sequential_start_context_invalid')
                elif case == 'unavailable_causes':
                    cache = dict(org.workflow_authority._cache)
                    org.workflow_authority._cache.clear()
                    try:
                        await read(status=409, code='sequential_authority_unverified')
                    finally:
                        org.workflow_authority._cache.update(cache)
                    dispatcher = org.orchestrator._workflow_sequential_dispatch
                    org.orchestrator._workflow_sequential_dispatch = None
                    try:
                        assert (await read())['definitions'][0]['unavailable_cause'] == 'sequential_host_unavailable'
                    finally:
                        org.orchestrator._workflow_sequential_dispatch = dispatcher
                    save_settings(coding, generation=2, admission='Disabled')
                    disabled = (await read())['definitions'][0]
                    assert disabled['admission'] == 'Disabled' and disabled['settings']['generation'] == 2
                    assert disabled['unavailable_cause'] == 'sequential_settings_disabled'
                    save_settings(coding, generation=3)
                    # Restore exact original row/schema bytes after these
                    # adversarial private inputs; guards are present at each read.
                    before_fixture = inventory()
                    saved = sqlite3.connect(':memory:')
                    conn.backup(saved)
                    try:
                        for row in conn.execute("SELECT name FROM sqlite_schema WHERE type='trigger' AND tbl_name IN ('workflow_seq_definitions','workflow_seq_settings','workflow_seq_settings_operations')").fetchall():
                            conn.execute('DROP TRIGGER "' + row[0] + '"')
                        conn.execute('PRAGMA foreign_keys=OFF')
                        conn.execute('DELETE FROM workflow_seq_settings_operations')
                        conn.execute('DELETE FROM workflow_seq_settings')
                        conn.execute('DELETE FROM workflow_seq_definitions')
                        conn.commit()
                        for row in saved.execute("SELECT sql FROM sqlite_schema WHERE type='trigger' AND tbl_name IN ('workflow_seq_definitions','workflow_seq_settings','workflow_seq_settings_operations')"):
                            conn.execute(row[0])
                        conn.execute('PRAGMA foreign_keys=ON')
                        assert (await read())['definitions'][0]['unavailable_cause'] == 'sequential_definition_not_registered'
                        from runtime.infrastructure.workflow_task_ownership import binding_view_ddl
                        conn.execute('DROP VIEW workflow_task_owner_bindings')
                        for kind, name in conn.execute("SELECT type,name FROM sqlite_schema WHERE name GLOB 'workflow_seq_*' AND type IN ('view','trigger')").fetchall():
                            conn.execute('DROP ' + kind + ' "' + name + '"')
                        conn.execute('PRAGMA foreign_keys=OFF')
                        for name, in conn.execute("SELECT name FROM sqlite_schema WHERE name GLOB 'workflow_seq_*' AND type='table'").fetchall():
                            conn.execute('DROP TABLE "' + name + '"')
                        conn.execute(binding_view_ddl('J', coding_extension=True))
                        conn.commit()
                        conn.execute('PRAGMA foreign_keys=ON')
                        assert (await read())['definitions'][0]['unavailable_cause'] == 'sequential_storage_unavailable'
                    finally:
                        conn.rollback()
                        saved.backup(conn)
                        saved.close()
                        assert inventory() == before_fixture, 'private input restoration changed rows/schema'
                elif case == 'bounds':
                    # Production-shaped unrelated historical volume is not a
                    # full-history prerequisite for one manager's exact context.
                    conn.executemany("INSERT INTO org_settings(section,value_json,updated_at) VALUES (?,?, '2026-11-17T00:00:00Z')",
                        [('unrelated-history-' + str(i), json.dumps({'historical': 'x' * 4096})) for i in range(2048)])
                    conn.commit()
                    started = time.monotonic()
                    await read()
                    print('R1b1 production-shaped rows=2048 payload_bytes>8388608 request_plus_inventory_seconds', round(time.monotonic()-started, 3), flush=True)
                    # Existing test-side budget fault injection; no production
                    # flag/export or successful-authority stub is introduced.
                    from runtime.workflows import sequential_views
                    real = sequential_views._ReadBudget
                    for kwargs in (dict(deadline=time.monotonic()-1), dict(sql_limit=0),
                                   dict(consumed=64*1048576), dict(traversed=65536)):
                        with patch.object(sequential_views, '_ReadBudget', lambda **kw: real(**dict(kw, **kwargs))):
                            await read(status=409, code='sequential_history_unverified')
                elif case == 'zero_writes':
                    # Historical native current-pointer bytes retain their old
                    # grammar. This is model-valid Explicit snapshot input, not
                    # a fabricated legacy execution/admission authority graph.
                    from runtime.workflows._coding_settings_records import SettingsSnapshot
                    from runtime.workflows._coding_role_records import Release
                    legacy = SettingsSnapshot(format='workflow-coding-settings-snapshot@1',
                        org_slug='scratch', generation=7, admission='explicit',
                        release=Release(identity='release/happyranch/coding', version=1,
                            definition_digest='6ee02be98738d3c95fe584b165abe07d0dbef66e510133c117ee0d62c953cad8'),
                        mapping=dict(id='historical-mapping', generation=7, digest='1'*64),
                        operation_key='historical-save', request_digest='2'*64, previous_snapshot_digest='3'*64,
                        published_at='2026-11-14T00:00:00.000000Z', published_by='human:founder')
                    conn.execute('INSERT INTO org_settings(section,value_json,updated_at,updated_by) VALUES (?,?,?,?)',
                        ('coding', legacy.model_dump_json(), legacy.published_at, 'founder'))
                    conn.commit()
                    assert (await read())['definitions'][0]['pin']['version'] == 2
                    await read('unknown', status=403, code='sequential_task_session_required')
                print('PASS R1b1', case, 'real route/registry/authority/settings; zero DML/COMMIT and identical all-table inventory', flush=True)

    async def bundled_catalog_cases(*, context_only=False):
        with tempfile.TemporaryDirectory(prefix='r1c-bundled-', dir='/tmp') as scratch:
            home = Path(scratch).resolve()
            runtime = RuntimeDir.init(home / 'runtime')
            root = runtime.orgs_dir / 'scratch'
            _seed_skeleton(root, from_example=None)
            (root / 'org/config.yaml').write_text('reviewer_agents: []\n')
            (root / 'org/teams.yaml').write_text('teams:\n  engineering:\n    manager: r1c_manager\n    workers: [r1c_dev, r1c_review, r1c_qa]\n')
            for name, role in (('r1c_manager', 'manager'), ('r1c_dev', 'worker'),
                               ('r1c_review', 'worker'), ('r1c_qa', 'worker')):
                agent = AgentDef(name=name, team='engineering', role=role, executor='claude',
                    allow_rules=(), repos={}, enrolled_by=None, enrolled_at_task=None,
                    enrolled_at=None, system_prompt='Private no-launch catalog proof')
                (root / 'org/agents' / (name + '.md')).write_text(render_agent_text(agent))
            with patch.dict(os.environ, {'HAPPYRANCH_DAEMON_HOME': str(home)}):
                db = initialize_fresh_org_database(OrgPaths(root).db_path, expected_org_slug='scratch')
                initialize_fresh_reviewer_agents(OrgPaths(root), db)
                db.close()
                org = OrgState.load(slug='scratch', root=root, settings=Settings())
                try:
                    coordinator = ProfileCoordinator(daemon_home=home, orgs={'scratch': org})
                    org._profile_coordinator = coordinator
                    org.workflow_authority._profile_coordinator = coordinator
                    coordinator.reconcile_startup()
                    org.orchestrator._main_loop = asyncio.get_running_loop()
                    org.orchestrator._queue = asyncio.Queue()
                    org.orchestrator._host_supervisor = HostSessionSupervisor(backend=PassthroughBackend(),
                        policy=canary_policy(), publisher=lambda receipt: None)
                    db, conn = org.db, org.db._conn
                    coding = release.read_coding_release()
                    original_manifest = dict(getattr(release, 'MANIFEST', {}))
                    selected = 'start_context' if context_only else bundled_case or 'all'

                    def command(compiled, *, generation=1, admission='Allowed'):
                        bindings = [dict(role=role, principal=dict(developer='r1c_dev', reviewer='r1c_review',
                            qa='r1c_qa', final_manager='r1c_manager')[role]) for role, _ in compiled.definition.roles]
                        snapshot = StorageSettings(format='workflow-sequential-settings-storage@1',
                            definition=compiled.pin, bindings=bindings, limits=compiled.definition.limits, admission=admission)
                        raw = _bytes(snapshot.model_dump(mode='json'))
                        return parse_start(_bytes(dict(format='workflow-sequential-start@1', operation_key='private-start',
                            definition=compiled.pin.model_dump(mode='json'), settings=dict(generation=generation,
                                snapshot_digest=hashlib.sha256(raw).hexdigest()), request='Private no-launch request',
                            inputs=[], bindings=bindings, lower_limits=compiled.definition.limits.model_dump(mode='json')))), raw

                    def save_settings(compiled, *, generation=1, admission='Allowed'):
                        cmd, raw = command(compiled, generation=generation, admission=admission)
                        definition_id = conn.execute('SELECT id FROM workflow_seq_definitions WHERE identity=? AND version=?',
                            (compiled.pin.identity, compiled.pin.version)).fetchone()[0]
                        _write(conn, 'scratch', 'admission', SimpleNamespace(operation_key=compiled.pin.identity + ':' + str(generation),
                            expected_generation=generation-1, definition=compiled.pin),
                            dict(write=dict(definition_id=definition_id, generation=generation, raw=raw)))
                        conn.commit()
                        return cmd

                    def precheck(cmd):
                        changes = conn.total_changes
                        try:
                            start.precheck_start(org, cmd.model_dump(mode='json'), source_task_id='private-source')
                        finally:
                            assert conn.total_changes == changes, 'R1c read-only precheck wrote'

                    db.insert_task(TaskRecord(id='private-source', assigned_agent='r1c_manager', team='engineering',
                        task_type='task', status='in_progress', brief='Private no-launch source'))

                    if selected == 'start_context':
                        await start_context_cases(org, runtime, home, coding, save_settings)
                        return

                    def inventory():
                        with _schema_snapshot(conn):
                            return sequential_storage_inventory(conn)

                    if selected in ('all', 'second_bundle', 'registration'):
                        # Private package resource + manifest override ONLY in this
                        # harness. The production reader, registrar, authority and
                        # precheck remain real. Nothing is added to shipped data.
                        document = json.loads(coding.raw)
                        document.update(identity='release/synthetic/second', version=1)
                        raw = _bytes(document) + b'\n'
                        second = compile_definition(raw[:-1], source_digest=hashlib.sha256(raw).hexdigest())
                        package = home / 'private-package'
                        directory = package / 'workflows/builtin_templates'
                        directory.mkdir(parents=True)
                        (directory / 'second.json').write_bytes(raw)
                        (directory / 'coding.v2.json').write_bytes(Path('runtime/workflows/builtin_templates/coding.v2.json').read_bytes())
                        entry = SimpleNamespace(package='runtime', resource=('workflows', 'builtin_templates', 'second.json'),
                            size=len(raw), pin=second.pin)
                        manifest = dict(original_manifest)
                        manifest[(second.pin.identity, second.pin.version)] = entry
                        files = release.resources.files
                        def private_files(name):
                            return package if name == 'runtime' else files(name)
                        with patch.object(release, 'MANIFEST', manifest, create=True), \
                             patch.object(builtin, 'MANIFEST', manifest, create=True), \
                             patch.object(release.resources, 'files', private_files):
                            cmd, _ = command(second)
                            try:
                                chosen = start._fresh_start(org, cmd)
                            except SequentialVenueUnavailable as exc:
                                raise AssertionError('second_bundle: manifest definition diverted to disposable venue: ' + str(exc)) from exc
                            assert chosen == second, 'second_bundle: generic selector did not select the second manifest pin'
                            builtin.SequentialBuiltinStore(org).ensure_registered()
                            cmd = save_settings(second)
                            precheck(cmd)
                            assert conn.execute('SELECT definition_bytes FROM workflow_seq_definitions WHERE identity=?',
                                (second.pin.identity,)).fetchone()[0] == second.raw
                            print('PASS R1c second_bundle real package reader/registrar/Allowed/current-authority/native precheck zero writes', flush=True)
                            if selected in ('all', 'registration'):
                                store = builtin.SequentialBuiltinStore(org)
                                before, changes = inventory(), conn.total_changes
                                store.ensure_registered()
                                assert conn.total_changes == changes and inventory() == before, 'exact_existing_zero_writes: registrar changed existing rows'
                                print('PASS R1c exact_existing_zero_writes two manifest entries; complete inventory/rowid/bytes', flush=True)

                    if selected in ('all', 'pins'):
                        pin = coding.pin
                        assert pin.model_dump(mode='json') == dict(identity='release/happyranch/coding', version=2,
                            source_digest='5039e9eb60a03f8de110b40123a123d25962fbff48c39af5271a0666d679d065',
                            definition_digest='a3b012d117c5905a1bac3c67a3158a8b1afa5a571acdba73c876ac7de24f790c',
                            compiler='workflow-sequential-compiler@1', validator='workflow-sequential-validator@1',
                            interpreter='workflow-sequential-interpreter@1', capabilities='workflow-sequential-capabilities@1')
                        assert len(original_manifest) == 1 and release.select_bundled_release(pin) == coding
                        entry = original_manifest[(pin.identity, pin.version)]
                        assert entry.package == 'runtime' and entry.resource == ('workflows', 'builtin_templates', 'coding.v2.json') and entry.size == 3795
                        print('PASS R1c coding_pins unchanged full manifest/source/definition/ABI/resource/size/provenance', flush=True)
                        for field in ('source_digest', 'definition_digest', 'compiler', 'validator', 'interpreter', 'capabilities'):
                            bad = pin.model_copy(update={field: '0' * 64 if field.endswith('digest') else 'unsupported@9'})
                            r1c_refuses('requested_' + field, lambda: release.select_bundled_release(bad), 'sequential_release_pin_mismatch')
                        for name, changed, expected in (
                            ('size', replace(entry, size=entry.size-1), 'sequential_release_corrupt'),
                            ('source', replace(entry, pin=pin.model_copy(update={'source_digest': '0'*64})), 'sequential_release_corrupt'),
                            ('definition', replace(entry, pin=pin.model_copy(update={'definition_digest': '0'*64})), 'sequential_release_corrupt'),
                            ('abi', replace(entry, pin=pin.model_copy(update={'compiler': 'unsupported@9'})), 'sequential_release_corrupt'),
                            ('provenance', replace(entry, package='foreign'), 'sequential_release_pin_mismatch'),
                            ('resource', replace(entry, resource=('workflows', 'builtin_templates', '../coding.v2.json')), 'sequential_release_pin_mismatch')):
                            with patch.dict(release.MANIFEST, {(pin.identity, pin.version): changed}):
                                r1c_refuses('tampered_' + name, lambda: release.select_bundled_release(changed.pin), expected)
                        with patch.object(release.resources, 'files', side_effect=FileNotFoundError):
                            r1c_refuses('missing_resource', lambda: release.select_bundled_release(pin), 'sequential_release_unavailable')

                    if selected in ('all', 'refusals'):
                        for identity, version in (('release/synthetic/unknown', 1), ('release/happyranch/coding', 99)):
                            pin = coding.pin.model_copy(update=dict(identity=identity, version=version))
                            r1c_refuses('nonmanifest_' + str(version), lambda: release.select_bundled_release(pin), 'sequential_release_pin_mismatch')
                        old = coding.pin.model_copy(update={'version': 1})
                        cmd, _ = command(coding)
                        r1c_refuses('coding_v1', lambda: start._fresh_start(org, cmd.model_copy(update={'definition': old})), 'coding_fresh_start_retired')
                        for version in (1, 2):
                            r1c_refuses('legacy_start_' + str(version), lambda: require_start_ready(conn, org,
                                dict(format='workflow-coding-start@' + str(version))), 'coding_fresh_start_retired')
                        fixture = cmd.model_copy(update={'definition': coding.pin.model_copy(update={'identity': 'release/synthetic/fixture'})})
                        # No live home is opened or changed: the production fence
                        # classifies the real account home from metadata first.
                        from runtime.workflows.sequential_venue import live_daemon_home
                        with patch.dict(os.environ, {'HAPPYRANCH_DAEMON_HOME': str(live_daemon_home()[0])}):
                            r1c_refuses('fixture_live_refused', lambda: start._fresh_start(org, fixture), 'sequential_venue_live_home')
                        r1c_refuses('fixture_marker_missing', lambda: start._fresh_start(org, fixture), 'sequential_venue_marker_absent')

                    if selected in ('all', 'admission'):
                        # Missing registration is observed with a real compiled
                        # private manifest entry before any registrar writes it.
                        missing = json.loads(coding.raw)
                        missing.update(identity='release/synthetic/missing', version=1)
                        raw = _bytes(missing) + b'\n'
                        compiled = compile_definition(raw[:-1], source_digest=hashlib.sha256(raw).hexdigest())
                        package = home / 'missing-package/workflows/builtin_templates'
                        package.mkdir(parents=True)
                        (package / 'missing.json').write_bytes(raw)
                        entry = SimpleNamespace(package='runtime', resource=('workflows', 'builtin_templates', 'missing.json'), size=len(raw), pin=compiled.pin)
                        files = release.resources.files
                        with patch.dict(release.MANIFEST, {(compiled.pin.identity, 1): entry}), \
                             patch.object(release.resources, 'files', lambda name: package.parent.parent if name == 'runtime' else files(name)):
                            cmd, _ = command(compiled)
                            changes = conn.total_changes
                            r1c_refuses('missing_registration', lambda: precheck(cmd), 'sequential_start_unavailable')
                            assert conn.total_changes == changes
                        cmd = save_settings(coding)
                        precheck(cmd)
                        print('PASS R1c coding_allowed native/current-authority/registered latest pins zero writes', flush=True)
                        dispatcher = org.orchestrator._workflow_sequential_dispatch
                        org.orchestrator._workflow_sequential_dispatch = None
                        try:
                            r1c_refuses('native_attachment_lost', lambda: precheck(cmd), 'sequential_host_unavailable')
                        finally:
                            org.orchestrator._workflow_sequential_dispatch = dispatcher
                        cache = dict(org.workflow_authority._cache)
                        org.workflow_authority._cache.clear()
                        try:
                            r1c_refuses('authority_lost', lambda: precheck(cmd), 'authority_pointer_not_ready')
                        finally:
                            org.workflow_authority._cache.update(cache)
                        disabled = save_settings(coding, generation=2, admission='Disabled')
                        r1c_refuses('stale_settings', lambda: precheck(cmd), 'workflow_seq_settings_generation_stale')
                        r1c_refuses('disabled_settings', lambda: precheck(disabled), 'sequential_start_unavailable')
                        assert conn.execute('SELECT count(*) FROM workflow_seq_runs').fetchone()[0] == 0
                        assert conn.execute('SELECT count(*) FROM workflow_seq_visits').fetchone()[0] == 0
                        assert conn.execute('SELECT count(*) FROM tasks').fetchone()[0] == 1
                        print('PASS R1c refusal matrix creates zero runs/visits/tasks', flush=True)
                finally:
                    org.close()
    if sys.argv[1:] in ([], ['--start-context']):
        asyncio.run(bundled_catalog_cases(context_only=True))
    if sys.argv[1:] != ['--start-context']:
        asyncio.run(bundled_catalog_cases())
    if sys.argv[1:] in (['--bundled-catalog'], ['--start-context']):
        raise SystemExit(0)


# A4-M drives the real daemon build before any listener, using only private orgs.
if sys.argv[1:] in ([], ['--bootstrap-startup']):
    import asyncio
    import signal
    from runtime.daemon import __main__ as entry
    from runtime.daemon.routes.orgs import _seed_skeleton
    from runtime.runtime import RuntimeDir
    from runtime.platform.passthrough_backend import PassthroughBackend
    from runtime.orchestrator.org_config import initialize_fresh_reviewer_agents
    from runtime.daemon.workflow_setup_status import read_setup_status
    from runtime.infrastructure import workflow_sequential_bootstrap as bootstrap
    from runtime.infrastructure import workflow_schema as schema
    from runtime.infrastructure.org_access_fence import OrgAccessFence
    from runtime.models import TaskStatus

    if bootstrap_startup_case in (None, 'evidence_custody'):
        import ast
        source = ast.parse(Path(bootstrap.__file__).read_text())
        attempt_names = {'_attempt_record'} | {
            alias.asname for node in ast.walk(source) if isinstance(node, ast.ImportFrom)
            for alias in node.names if alias.name == '_attempt_record' and alias.asname}
        evidence_calls = [node for node in ast.walk(source) if isinstance(node, ast.Call)
                          and (isinstance(node.func, ast.Name) and node.func.id in attempt_names
                               or isinstance(node.func, ast.Attribute) and node.func.attr == '_attempt_record')]
        assert evidence_calls, 'A4-M missing startup evidence reader'
        assert all(any(keyword.arg == 'directory_reader' and isinstance(keyword.value, (ast.Name, ast.Attribute))
                       for keyword in node.keywords)
                   for node in evidence_calls), 'A4-M startup evidence call omits custodied reader'
        assert len(evidence_calls) == 1, 'A4-M startup evidence reads bypass the single custody path'
        print('PASS A4-M mechanical startup evidence custody', flush=True)

    # Mounted keepers for discovery count and pre/post-worker close faults.
    # The 1,024 missing-DB roots reproduce normal local refusals independently
    # of the overflow org, whose real DB must never even be selected or opened.
    for fault in ('discovery_cap', 'discovery_close', 'discovery_fence_close', 'discovery_descriptor_close', 'discovery_evidence_close', 'discovery_registered_close'):
        if bootstrap_startup_case is not None and bootstrap_startup_case != fault:
            continue
        with tempfile.TemporaryDirectory(prefix='a4-mounted-discovery-') as scratch, ExitStack() as stack:
            import httpx
            from runtime.daemon.app import create_app
            home = Path(scratch).resolve()
            runtime = RuntimeDir.init(home/'runtime')
            (home/'runtimes.yaml').write_text(json.dumps(dict(active=str(runtime.root), registered=[str(runtime.root)])))
            stack.enter_context(patch.dict(os.environ, {'HAPPYRANCH_DAEMON_HOME': str(home)}))
            stack.enter_context(patch('runtime.platform.backend_factory.select_session_backend', return_value=PassthroughBackend()))
            if fault == 'discovery_cap':
                for n in range(1025):
                    root = runtime.orgs_dir/f'org-{n:04}'
                    (root/'org').mkdir(parents=True)
                    (root/'org/teams.yaml').write_text('teams: {}\n')
            else:
                root = runtime.orgs_dir/'scratch'
                _seed_skeleton(root, from_example=None)
            path = root/'happyranch.db'
            if fault in ('discovery_fence_close', 'discovery_registered_close'):
                db = Database(path)
                schema.initialize_complete_org_schema(db, expected_org_slug=root.name, layout='J')
                if fault == 'discovery_registered_close':
                    install_pause_schema(db, org_slug=root.name)
                    install_task_ownership(db, org_slug=root.name)
                    initialize_fresh_reviewer_agents(OrgPaths(root), db)
                db.close()
                if fault == 'discovery_registered_close':
                    org = OrgState.load(slug=root.name, root=root, settings=Settings())
                    org.close()
            else:
                schema.initialize_fresh_org_database(path, expected_org_slug=root.name).close()
            if fault == 'discovery_evidence_close':
                # Use the real durable publisher, not a hand-built reader fixture.
                seed = bootstrap.BootstrapStartup(home=home, runtime_root=runtime.root)
                seed._hold(root.name, 'install_incomplete', 'unknown')
                for record in reversed(seed.hold_descriptors):
                    if not record['attempted']:
                        bootstrap._close_descriptor(record)
            original_bytes = path.read_bytes()
            namespace = home/'backups/workflow-sequential'/hashlib.sha256(str(runtime.root).encode()).hexdigest()/root.name
            native_connect, native_fence_close, native_descriptor_close = sqlite3.connect, OrgAccessFence.close, os.close
            first = OSError('synthetic FIRST original discovery close failure')
            calls, connections, failed_descriptors, opened, evidence_children = [], [], [], [], []
            evidence_child_records = []
            committed = {}
            class CloseFault(sqlite3.Connection):
                def close(self):
                    calls.append(self)
                    raise first
            def connect(*args, **kwargs):
                if args[0] == path.as_uri()+'?mode=ro':
                    opened.append(args[0])
                    if fault == 'discovery_close':
                        kwargs['factory'] = CloseFault
                conn = native_connect(*args, **kwargs)
                if isinstance(conn, CloseFault):
                    connections.append(conn)
                return conn
            def fence_close(fence):
                if fault == 'discovery_fence_close' and fence.root == root:
                    calls.append(fence)
                    raise first
                return native_fence_close(fence)
            def descriptor_close(record):
                if fault == 'discovery_descriptor_close' and not failed_descriptors:
                    # Real native close denial, before _directory can retire its
                    # preceding original. Subsequent closes remain native.
                    failed_descriptors.append(record)
                    def deny(fd):
                        assert fd == record['fd']
                        calls.append(fd)
                        raise first
                    with patch.object(bootstrap.os, 'close', side_effect=deny):
                        return original_close_descriptor(record)
                return original_close_descriptor(record)
            def evidence_close(fd):
                # Fault the actual startup evidence traversal, including the old
                # shared opener on RED; earlier discovery traversal stays native.
                from runtime.daemon import workflow_setup_status as status, _disk_sweep_fs as fs
                frame = sys._getframe(1)
                traversal = None
                in_evidence = False
                while frame is not None:
                    if frame.f_code in (bootstrap._directory.__code__, fs.directory.__wrapped__.__code__):
                        traversal = frame
                    if frame.f_code is status._attempt_record.__code__:
                        in_evidence = True
                    frame = frame.f_back
                if in_evidence and traversal is not None and not failed_descriptors:
                    local = traversal.f_locals
                    child = local['child']
                    evidence_children.append(child['fd'] if isinstance(child, dict) else child)
                    evidence_child_records.append(child if isinstance(child, dict) else None)
                    failed_descriptors.append(dict(fd=fd))
                    if fault == 'discovery_registered_close':
                        # Snapshot at the actual post-worker native fault. No
                        # fixture substitutes for the helper's committed result.
                        committed.update(database=path.read_bytes(), evidence={
                            p: p.read_bytes() for p in namespace.rglob('*') if p.is_file()})
                if failed_descriptors and fd == failed_descriptors[0]['fd']:
                    calls.append(fd)
                    raise first
                return native_descriptor_close(fd)
            original_close_descriptor = bootstrap._close_descriptor
            stack.enter_context(patch.object(bootstrap.sqlite3, 'connect', side_effect=connect))
            stack.enter_context(patch.object(OrgAccessFence, 'close', fence_close))
            stack.enter_context(patch.object(bootstrap, '_close_descriptor', descriptor_close))
            if fault in ('discovery_evidence_close', 'discovery_registered_close'):
                stack.enter_context(patch.object(os, 'close', evidence_close))
            state = None
            try:
                try:
                    state = entry._build_state(Settings())
                except bootstrap.BootstrapEvidenceError as exc:
                    state = exc._attachment_owner['candidate']
                    raise AssertionError('A4-M discovery cap escaped the per-org HOLD and aborted build') from exc
                owner = state._workflow_bootstrap
                if fault == 'discovery_registered_close':
                    assert len(owner.workers) == 1, 'A4-M registered read did not follow exactly one real installer'
                    worker = owner.workers[0]
                    assert worker.result['state'] == 'verified' and worker.closed and worker.reaped and worker.permit_sent, 'A4-M helper did not commit/register/close/reap'
                    assert committed and committed['database'] != original_bytes, 'A4-M registered read fault missed committed install'
                    assert path.read_bytes() == committed['database'], 'A4-M registered read fault changed committed DB'
                    assert {p: p.read_bytes() for p in namespace.rglob('*') if p.is_file()} == committed['evidence'], 'A4-M registered read fault changed committed evidence/backup'
                    events = [json.loads(raw) for p, raw in committed['evidence'].items() if p.suffix == '.json']
                    assert sorted(event['phase'] for event in events) == sorted(('intent', 'backup_verified', 'pre_commit', 'committed', 'verified', 'registered')), 'A4-M missing committed registration phases'
                    assert read_setup_status(home, runtime.root, root.name).state == 'installed_verified', 'A4-M committed registration was undone'
                else:
                    assert not owner.workers, 'A4-M pre-worker failure spawned an installer'
                    assert path.read_bytes() == original_bytes, 'A4-M discovery refusal changed original DB'
                if fault == 'discovery_cap':
                    assert len(tuple(runtime.iter_org_roots())) == 1025
                    assert len(owner.discoveries) == 1024 and root not in owner.discoveries, 'A4-M discovery cap removed or overflow acquired'
                    assert owner.holds[root.name] == state.broken_orgs[root.name] == 'startup_upgrade_budget_unavailable', 'A4-M missing visible overflow HOLD'
                    assert len(state.workflow_setup_roots) == 1025 and len(state.broken_orgs) == 1025, 'A4-M remaining org startup loop aborted'
                    assert not opened, 'A4-M overflow org DB was opened'
                    assert not (home/'backups/workflow-sequential'/hashlib.sha256(str(runtime.root).encode()).hexdigest()/root.name).exists(), 'A4-M overflow allocated filesystem custody'
                    assert all(r['closed'] for r in owner.hold_descriptors), 'A4-M count refusal leaked descriptors'
                else:
                    route = owner.discoveries[root]
                    selected = route.selections[-1] if fault == 'discovery_registered_close' else route.selections[0]
                    if fault == 'discovery_registered_close':
                        print('A4-M post-registration close observation', json.dumps(dict(
                            native_close_attempts=len(calls), child_retained=any(
                                r is evidence_child_records[0] for r in selected.descriptors) if evidence_children else False,
                            discovery_closed=route.closed, fence_closed=route.fence._closed,
                            first_error=route.close_error is first, held=state.broken_orgs,
                            worker_result=worker.result)), flush=True)
                    assert calls and len(calls) == 1, 'A4-M original close failure not reached exactly once'
                    assert route.close_error is not None and not route.closed and not route.fence._closed
                    if fault in ('discovery_evidence_close', 'discovery_registered_close'):
                        assert owner.holds[root.name] == state.broken_orgs[root.name] == 'install_incomplete', 'A4-M prior evidence lacks visible HOLD'
                        failed = [r for r in selected.descriptors if r['fd'] == failed_descriptors[0]['fd'] and r.get('error') is first]
                        assert len(failed) == 1 and failed[0]['attempted'] and not failed[0]['closed'], 'A4-M prior evidence lost original descriptor custody'
                        assert any(r['fd'] == evidence_children[0] for r in selected.descriptors[selected.descriptors.index(failed[0])+1:]), 'A4-M prior evidence lost newly opened child custody'
                        assert selected.close_error is first, 'A4-M prior evidence lost FIRST native close cause'
                        os.fstat(failed[0]['fd'])
                        if fault == 'discovery_registered_close':
                            assert route.fence.exclusive, 'A4-M registered read lost original EX'
                            assert any(r is evidence_child_records[0] for r in selected.descriptors), 'A4-M registered read lost exact child record'
                    primary, close_error, selected_error = route.primary, route.close_error, selected.close_error
                    for _ in range(2):
                        try:
                            asyncio.run(state.close_all())
                        except bootstrap.BootstrapEvidenceError as exc:
                            assert exc.__cause__ is close_error, 'A4-M shutdown lost FIRST discovery close failure'
                        else:
                            raise AssertionError('A4-M shutdown accepted unresolved original discovery custody')
                        assert not state._shutdown_complete, 'A4-M unresolved discovery claimed successful shutdown'
                        assert route.primary is primary and route.close_error is close_error and selected.close_error is selected_error, 'A4-M FIRST close cause replaced'
                        assert len(calls) == 1, 'A4-M retried failed native close'
                        if fault == 'discovery_registered_close':
                            assert path.read_bytes() == committed['database'] and len(owner.workers) == 1, 'A4-M shutdown reran/undid installation'
                            assert {p: p.read_bytes() for p in namespace.rglob('*') if p.is_file()} == committed['evidence'], 'A4-M shutdown rewrote committed evidence/backup'
                            assert not route.fence._closed, 'A4-M refused shutdown released original EX'
                    assert close_error is first, 'A4-M FIRST native discovery close cause replaced'
                    if fault == 'discovery_close':
                        assert selected.close_error is first, 'A4-M FIRST native selection close cause replaced'
                        assert connections[0].execute('SELECT 1').fetchone()[0] == 1, 'A4-M original SQLite custody lost'
                    if fault == 'discovery_descriptor_close':
                        assert selected.close_error is first and not failed_descriptors[0]['closed']
                        os.fstat(failed_descriptors[0]['fd'])
                async def health():
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(state)), base_url='http://private') as client:
                        assert (await client.get('/api/v1/health')).status_code == 200, 'A4-M discovery refusal prevented serving'
                asyncio.run(health())
                if fault == 'discovery_cap':
                    asyncio.run(state.close_all())
                    assert state._shutdown_complete, 'A4-M resolved discovery prevented shutdown'
                print('PASS A4-M mounted discovery build/health/shutdown:', fault, flush=True)
            finally:
                # Dispose only exact private originals after the custody assertions;
                # production never retries them or changes their failure records.
                for conn in connections:
                    sqlite3.Connection.close(conn)
                for record in failed_descriptors:
                    native_descriptor_close(record['fd'])
                for fd in evidence_children:
                    if state is None or not any(r['fd'] == fd for route in state._workflow_bootstrap.discoveries.values() for selected in route.selections for r in selected.descriptors):
                        native_descriptor_close(fd)
                if state is not None:
                    for route in state._workflow_bootstrap.discoveries.values():
                        for record in route.fences:
                            if not record['closed'] and not record['transferred']:
                                native_fence_close(record['fence'])
                    if fault == 'discovery_cap' and not state._shutdown_complete:
                        asyncio.run(state.close_all())
                    state.metrics_store.close()

    mounted_cases = ('success', 'integrity', 'backup', 'ex', 'history', 'history_precommit', 'commit_uncertain', 'kill', 'close', 'late_spawn')
    for fault in mounted_cases:
        if bootstrap_startup_case is not None and bootstrap_startup_case != fault:
            continue
        with tempfile.TemporaryDirectory(prefix='a4-mounted-') as scratch:
            home = Path(scratch).resolve()
            runtime = RuntimeDir.init(home/'runtime')
            (home/'runtimes.yaml').write_text(json.dumps(dict(active=str(runtime.root), registered=[str(runtime.root)])))
            with ExitStack() as stack:
                stack.enter_context(patch.dict(os.environ, {'HAPPYRANCH_DAEMON_HOME': str(home)}))
                stack.enter_context(patch('runtime.platform.backend_factory.select_session_backend', return_value=PassthroughBackend()))
                slugs = ('happyranch', 'danze', 'family') if fault == 'success' else ('happyranch', 'family')
                for slug in slugs:
                    root = runtime.orgs_dir/slug
                    _seed_skeleton(root, from_example=None)
                    db = Database(root/'happyranch.db')
                    try:
                        schema.initialize_complete_org_schema(db, expected_org_slug=slug, layout='J')
                        install_pause_schema(db, org_slug=slug)
                        install_task_ownership(db, org_slug=slug)
                        initialize_fresh_reviewer_agents(OrgPaths(root), db)
                        db.insert_task(TaskRecord(id='TASK-mounted', brief='synthetic retained task', status=TaskStatus.COMPLETED))
                        db._conn.execute('CREATE TABLE a4_mount_sentinel(id INTEGER PRIMARY KEY,v BLOB)')
                        db._conn.execute('INSERT INTO a4_mount_sentinel VALUES(37,?)', (b'original\0typed',))
                        db._conn.commit()
                    finally:
                        db.close()
                    # Normalize ordinary startup before observing the upgrade.
                    org = OrgState.load(slug=slug, root=root, settings=Settings())
                    org.close()
                path = runtime.orgs_dir/'happyranch/happyranch.db'
                if fault == 'history':
                    with closing(sqlite3.connect(path)) as conn:
                        conn.execute("UPDATE tasks SET status='pending' WHERE id='TASK-mounted'")
                        conn.commit()
                if fault == 'integrity':
                    # A genuine stale index, independently reported non-ok by SQLite.
                    with closing(sqlite3.connect(path)) as conn:
                        conn.execute('CREATE INDEX a4_stale ON a4_mount_sentinel(v)')
                        row = tuple(conn.execute("SELECT rowid,type,name,tbl_name,rootpage,sql FROM sqlite_schema WHERE name='a4_stale'").fetchone())
                        cookie = conn.execute('PRAGMA schema_version').fetchone()[0]
                        conn.execute('PRAGMA writable_schema=ON')
                        conn.execute('DELETE FROM sqlite_schema WHERE rowid=?', (row[0],))
                        conn.execute('PRAGMA schema_version='+str(cookie+1))
                        conn.execute('PRAGMA writable_schema=RESET')
                        conn.execute("UPDATE a4_mount_sentinel SET v=x'0102'")
                        conn.execute('PRAGMA writable_schema=ON')
                        conn.execute('INSERT INTO sqlite_schema(rowid,type,name,tbl_name,rootpage,sql) VALUES(?,?,?,?,?,?)', row)
                        conn.execute('PRAGMA schema_version='+str(cookie+2))
                        conn.execute('PRAGMA writable_schema=RESET')
                        conn.commit()
                        assert conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok', 'A4-M corrupt fixture remained healthy'
                blocker = OrgAccessFence.acquire(path.parent) if fault == 'ex' else None
                native_spawn = asyncio.create_subprocess_exec
                runner = home/'fault-helper.py'
                injected = {
                    'backup': """
original_hash = bootstrap.BootstrapSource._hash
def wrong_second_hash(self, fd):
    value = original_hash(self, fd)
    self.test_hash_calls = getattr(self, 'test_hash_calls', 0)+1
    return '0'*64 if self.test_hash_calls == 2 and self.path.parent == Path(SOURCE_ROOT) else value
bootstrap.BootstrapSource._hash = wrong_second_hash
""",
                    'history_precommit': """
original_history = bootstrap.require_bootstrap_idle_history
def late_history(source, *, writer=None):
    if writer is not None and source.path.parent == Path(SOURCE_ROOT):
        writer.execute("UPDATE tasks SET status='pending' WHERE id='TASK-mounted'")
    return original_history(source, writer=writer)
bootstrap.require_bootstrap_idle_history = late_history
""",
                    'commit_uncertain': """
original_connection = bootstrap.BootstrapSource._connection
def deny_commit(self, path, *, readonly):
    conn = original_connection(self, path, readonly=readonly)
    if not readonly and path == self.path and path.parent == Path(SOURCE_ROOT):
        conn.set_authorizer(lambda action, one, two, db, trigger: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_TRANSACTION and one == 'COMMIT' else sqlite3.SQLITE_OK)
    return conn
bootstrap.BootstrapSource._connection = deny_commit
""",
                    'close': """
from runtime.daemon.org_state import OrgState
original_close = OrgState.close
def uncertain_close(self):
    if self.root == Path(SOURCE_ROOT):
        raise OSError('synthetic original close uncertainty')
    return original_close(self)
OrgState.close = uncertain_close
""",
                }.get(fault)
                if injected:
                    runner.write_text('import sys, sqlite3\nfrom pathlib import Path\nfrom runtime.infrastructure import workflow_sequential_bootstrap as bootstrap\nSOURCE_ROOT = '+repr(str(path.parent))+'\n'+injected+'\nraise SystemExit(bootstrap.bootstrap_helper_main(sys.argv[1:]))\n')
                    async def fault_spawn(*args, **kwargs):
                        if '--workflow-sequential-bootstrap-helper' in args:
                            offset = args.index('--workflow-sequential-bootstrap-helper')
                            args = (args[0], str(runner), *args[offset+1:])
                        return await native_spawn(*args, **kwargs)
                    stack.enter_context(patch.object(asyncio, 'create_subprocess_exec', fault_spawn))
                if fault == 'kill':
                    actual_run = bootstrap.BootstrapSupervisor.run
                    async def killed_run(worker, qualify):
                        calls = 0
                        async def original_qualification(identity, digest):
                            nonlocal calls
                            allowed = await qualify(identity, digest)
                            calls += 1
                            if calls == 2 and worker.discovery.root == path.parent:
                                os.killpg(worker.process.pid, signal.SIGKILL)
                                await worker.process.wait()
                            return allowed
                        return await actual_run(worker, original_qualification)
                    stack.enter_context(patch.object(bootstrap.BootstrapSupervisor, 'run', killed_run))
                if fault == 'history_precommit':
                    # Retain a real committed WAL without checkpointing it. A
                    # failed late gate must not move those bytes into the DB.
                    keeper = stack.enter_context(closing(sqlite3.connect(path)))
                    keeper.execute('PRAGMA journal_mode=WAL')
                    keeper.execute('PRAGMA wal_autocheckpoint=0')
                    keeper.execute("INSERT INTO a4_mount_sentinel VALUES(38,x'03')")
                    keeper.commit()
                    assert path.with_name(path.name+'-wal').stat().st_size > 0
                release_spawn = None
                if fault == 'late_spawn':
                    import threading
                    release_spawn = threading.Event()
                    actual_supervisor_init = bootstrap.BootstrapSupervisor.__init__
                    def delayed_owner(worker, discovery, settings):
                        actual_supervisor_init(worker, discovery, settings)
                        discovery.deadline = min(discovery.deadline, time.monotonic()+20.1)
                    async def delayed_native_spawn(*args, **kwargs):
                        if '--workflow-sequential-bootstrap-helper' in args:
                            await asyncio.get_running_loop().run_in_executor(None, release_spawn.wait)
                        return await native_spawn(*args, **kwargs)
                    stack.enter_context(patch.object(bootstrap.BootstrapSupervisor, '__init__', delayed_owner))
                    stack.enter_context(patch.object(asyncio, 'create_subprocess_exec', delayed_native_spawn))
                original_bytes = path.read_bytes()
                first_backup = None
                for start in ('first', 'next'):
                    before = {slug: (root/'happyranch.db').read_bytes() for slug, root in runtime.iter_org_roots()}
                    def durable_inventory(root):
                        with closing(sqlite3.connect((root/'happyranch.db').as_uri()+'?mode=ro', uri=True)) as conn:
                            conn.execute('PRAGMA query_only=ON')
                            conn.execute('BEGIN')
                            return schema.sequential_storage_inventory(conn)
                    inventories = None
                    if start == 'next':
                        inventories = {slug: durable_inventory(root) for slug, root in runtime.iter_org_roots()}
                        # Byte-zero belongs to automatic setup. Ordinary startup
                        # retains its existing transient publication-lease writes.
                        probe = bootstrap.BootstrapStartup(home=home, runtime_root=runtime.root)
                        probe.run(tuple(runtime.iter_org_roots()), Settings())
                        assert not probe.workers, 'A4-M second setup spawned an installer'
                        for route in probe.discoveries.values():
                            route.close()
                        assert all((root/'happyranch.db').read_bytes() == before[slug] for slug, root in runtime.iter_org_roots()), 'A4-M second automatic setup wrote org DB'
                    state = entry._build_state(Settings())
                    try:
                        expected = set(slugs) if fault == 'success' else set() if fault == 'late_spawn' else {'family'}
                        assert set(state._all_orgs_snapshot()) == expected, ('A4-M startup lost attachment isolation or bypassed hold', fault, state.broken_orgs)
                        # Existing profile policy may hold other consumers when a
                        # root is unavailable; A4 does not waive that global veto.
                        if fault == 'late_spawn':
                            owner = state._workflow_bootstrap
                            worker = owner.workers[0]
                            assert owner.thread is not None and owner.thread.is_alive() and not owner.loop.is_closed(), 'A4-M lost original pending loop'
                            assert not worker.spawn.done() and not worker.reaped and not worker.discovery.fence._closed, 'A4-M replaced unresolved original spawn'
                            try:
                                asyncio.run(state.close_all())
                            except bootstrap.BootstrapEvidenceError:
                                pass
                            else:
                                raise AssertionError('A4-M shutdown accepted pending original')
                            release_spawn.set()
                            end = time.monotonic()+5
                            while not worker.reaped and time.monotonic() < end:
                                time.sleep(.01)
                            assert worker.reaped and worker.process.returncode == -signal.SIGKILL, 'A4-M lost actual late child kill/reap'
                            assert worker.expired and not worker.closed and not worker.permit_sent, 'A4-M accepted late result or invented closure'
                            assert path.read_bytes() == original_bytes, 'A4-M late child changed source'
                            assert not worker.discovery.fence._closed, 'A4-M late reap released unknown original EX'
                            worker.discovery.fence.close()  # exact synthetic original, after assertions
                            owner.loop.call_soon_threadsafe(owner.loop.stop)
                            owner.thread.join(timeout=2)
                            assert not owner.thread.is_alive(), 'A4-M fixture failed to join original loop'
                            owner.loop.close()
                            unlaunched = [route for route in owner.discoveries.values() if route is not worker.discovery]
                            assert len(owner.workers) == 1 and len(unlaunched) == 1, 'A4-M launched another worker while original pending'
                            other = unlaunched[0]
                            print('A4-M unlaunched discovery closure', json.dumps(dict(org=other.root.name,
                                closed=other.closed, fences=[dict(attempted=r['attempted'], closed=r['closed'],
                                    native_closed=r['fence']._closed) for r in other.fences])), flush=True)
                            assert other.closed and all(r['closed'] and r['fence']._closed for r in other.fences), 'A4-M refused unlaunched worker leaked discovery custody'
                            assert all(r['closed'] for selected in other.selections for r in (*selected.connections, *selected.descriptors)), 'A4-M unlaunched discovery lost selector custody'
                            assert set(owner.holds) == set(slugs), 'A4-M unlaunched cleanup erased visible HOLD'
                            assert all((root/'happyranch.db').read_bytes() == before[slug] for slug, root in runtime.iter_org_roots()), 'A4-M unlaunched cleanup changed source DB'
                            print('PASS A4-M actual mounted startup: late_spawn original retained/late child killed/reaped', flush=True)
                            break
                        if fault != 'success':
                            assert 'happyranch' in state.broken_orgs, 'A4-M missing visible closed hold'
                            status = read_setup_status(home, runtime.root, 'happyranch')
                            expected_status = ('unknown', 'commit_uncertain') if fault in ('commit_uncertain', 'kill') else ('installed_unverified', 'installation_unverified') if fault == 'close' else None
                            if expected_status:
                                assert (status.state, status.reason) == expected_status, ('A4-M wrong retained uncertainty', fault, status)
                            if fault in ('integrity', 'ex', 'history'):
                                reason = dict(integrity='integrity_check_failed', ex='writer_exclusion_unproven', history='history_check_failed')[fault]
                                assert (status.state, status.reason) == ('not_installed', reason), ('A4-M wrong pre-intent hold', fault, status)
                                assert status.backup_path is None, 'A4-M pre-intent hold fabricated backup'
                            if fault != 'close':
                                assert path.read_bytes() == original_bytes, ('A4-M failed check changed source DB', fault)
                            if start == 'next':
                                assert not state._workflow_bootstrap.workers, 'A4-M replayed a failed or uncertain attempt'
                            elif fault in ('backup', 'history_precommit', 'commit_uncertain', 'kill', 'close', 'late_spawn'):
                                worker = next(worker for worker in state._workflow_bootstrap.workers if worker.discovery.root == path.parent)
                                assert worker.reaped, 'A4-M lost original child reap'
                                assert worker.permit_sent == (fault in ('commit_uncertain', 'close')), 'A4-M wrong original COMMIT boundary'
                                if fault in ('kill', 'close'):
                                    assert not worker.closed and not worker.discovery.fence._closed, 'A4-M fabricated closure or released unknown EX'
                                    try:
                                        asyncio.run(state.close_all())
                                    except bootstrap.BootstrapEvidenceError as exc:
                                        assert str(exc) == 'workflow_setup_original_worker_in_flight' and not state._shutdown_complete, 'A4-M wrong unresolved shutdown custody'
                                    else:
                                        raise AssertionError('A4-M shutdown claimed unresolved original closed')
                                if fault == 'backup':
                                    assert worker.result['reason'] == 'backup_verification_failed', 'A4-M backup verification not reached'
                                if fault == 'history_precommit':
                                    assert worker.result['reason'] == 'history_check_failed', 'A4-M final durable history check not reached'
                                # All native handles are synthetic. Release this exact
                                # retained parent only AFTER asserting unknown custody;
                                # the actual private child has been reaped, no descendants.
                                if not worker.discovery.fence._closed:
                                    worker.discovery.fence.close()
                        for slug, org in state._all_orgs_snapshot().items():
                            assert org.db._conn.execute("SELECT 1 FROM sqlite_schema WHERE name='workflow_seq_schema_versions'").fetchone(), 'A4-M daemon startup did not install sequential storage'
                            assert tuple(org.db._conn.execute('SELECT id,v FROM a4_mount_sentinel').fetchone()) == (37, b'original\0typed'), 'A4-M changed retained typed cells'
                            assert org.db._conn.execute('SELECT count(*) FROM workflow_seq_definitions').fetchone()[0] == 1, 'A4-M missed qualified catalog registration'
                            assert org.db._conn.execute('SELECT count(*) FROM workflow_seq_settings').fetchone()[0] == 0, 'A4-M setup allocated settings or Allow'
                            assert org.db._conn.execute('SELECT count(*) FROM workflow_seq_runs').fetchone()[0] == 0, 'A4-M setup allocated a workflow run'
                            assert [tuple(row) for row in org.db._conn.execute('SELECT id,status FROM tasks')] == [('TASK-mounted','completed')], 'A4-M setup changed native tasks'
                            status = read_setup_status(home, runtime.root, slug)
                            assert (status.state, status.reason) == ('installed_verified', 'registered'), ('A4-M missing registered evidence', status)
                            backup = Path(status.backup_path)
                            assert backup.is_file(), 'A4-M missing retained backup'
                            record = json.loads((backup.parent/'01.json').read_text())
                            assert hashlib.sha256(backup.read_bytes()).hexdigest() == record['backup_sha256'], 'A4-M backup hash not verified'
                            assert sorted(x.name for x in backup.parent.iterdir()) == ['00.json','01.json','02.json','03.json','04.json','05.json','before.db'], 'A4-M incomplete registered phase chain'
                            if slug == 'happyranch':
                                if start == 'first':
                                    first_backup = backup
                                else:
                                    assert backup == first_backup, 'A4-M second startup created another backup'
                        audit_path = home/'runtime-audit.db'
                        with closing(sqlite3.connect(audit_path.as_uri()+'?mode=ro', uri=True)) as conn:
                            audits = [json.loads(row[0]) for row in conn.execute("SELECT payload FROM audit_log WHERE task_id='' AND agent='daemon' AND action='workflow_setup_registered'")]
                        assert {row['org_slug'] for row in audits} == expected and len(audits) == len(expected), 'A4-M missing, duplicate or premature runtime registration audit'
                    finally:
                        if release_spawn is not None:
                            release_spawn.set()
                        asyncio.run(state.close_all())
                        state.metrics_store.close()
                    if start == 'next':
                        assert all(durable_inventory(root) == inventories[slug] for slug, root in runtime.iter_org_roots()), 'A4-M second full startup changed durable SQL/typed rows/rowids'
                    if blocker is not None:
                        blocker.close()
                        blocker = None
                    assert not (home/'daemon.pid').exists() and not (home/'daemon.port').exists(), 'A4-M build served a listener'
                    print('PASS A4-M actual mounted startup:', fault, start, flush=True)
    if sys.argv[1:] == ['--bootstrap-startup']:
        raise SystemExit(0)

# A4-H exercises the actual private helper and original parent socket/EX custody.
# Its source owners are known because this test creates and positively closes
# every synthetic native handle. No production validator returns fake success.
if sys.argv[1:] in ([], ['--bootstrap-worker']):
    import asyncio
    import signal
    import socket
    from runtime.infrastructure import workflow_sequential_bootstrap as bootstrap
    from runtime.infrastructure import workflow_schema as schema
    from runtime.infrastructure.org_access_fence import OrgAccessFence, OrgAccessHeld
    from runtime.daemon.routes.orgs import _seed_skeleton
    from runtime.orchestrator.org_config import initialize_fresh_reviewer_agents

    worker_cases = ('success', 'refuse_source', 'refuse_commit', 'cancel', 'kill',
                    'reserve', 'late_spawn', 'source_error', 'source_eof', 'attempt_eof', 'unsolicited',
                    'foreign_permit', 'duplicate_permit', 'qualification_false', 'late_spawn_after_timeout')
    for fault in worker_cases:
        if worker_case is not None and worker_case != fault:
            continue
        with tempfile.TemporaryDirectory(prefix='a4-worker-') as scratch:
            private = Path(scratch).resolve()
            home, runtime = private/'home', private/'runtime'
            home.mkdir(mode=0o700)
            root = runtime/'orgs/scratch'
            _seed_skeleton(root, from_example=None)
            path = root/'happyranch.db'
            db = Database(path)
            try:
                schema.initialize_complete_org_schema(db, expected_org_slug='scratch', layout='J')
                install_pause_schema(db, org_slug='scratch')
                initialize_fresh_reviewer_agents(OrgPaths(root), db)
                install_task_ownership(db, org_slug='scratch')
                db._conn.execute('CREATE TABLE a4_helper_sentinel(id INTEGER PRIMARY KEY,v BLOB)')
                db._conn.execute('INSERT INTO a4_helper_sentinel VALUES (37,?)', (b'original\0typed',))
                db._conn.commit()
                if fault == 'source_error':
                    db.insert_task(TaskRecord(id='TASK-active', brief='synthetic pending owner'))
            finally:
                db.close()

            def inventory():
                with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=0)) as conn:
                    conn.execute('PRAGMA query_only=ON')
                    conn.execute('BEGIN')
                    return schema.sequential_storage_inventory(conn)
            original, before = inventory(), path.read_bytes()
            with patch.dict(os.environ, {'HAPPYRANCH_DAEMON_HOME': str(home)}):
                startup = bootstrap.BootstrapStartup(home=home, runtime_root=runtime)
                route = startup.discover(root)
                assert route.read() == 'absent', 'A4-H fixture did not select original EX'
                namespace = home/'backups/workflow-sequential'/hashlib.sha256(str(runtime).encode()).hexdigest()/'scratch'
                if fault in ('source_eof', 'attempt_eof', 'unsolicited'):
                    left, right = socket.socketpair()
                    control = bootstrap.BootstrapControl(right, route.deadline)
                    source = bootstrap.BootstrapSource(fence=route.fence, deadline=route.deadline,
                        control=control if fault == 'source_eof' else None)
                    attempt = None
                    try:
                        if fault != 'source_eof':
                            source.open()
                            attempt = bootstrap.BootstrapAttempt(home=home, runtime_root=runtime, slug='scratch',
                                fence=route.fence, source_identity=source.source_identity,
                                source_inventory_digest=source.inventory_digest, deadline=route.deadline, control=control)
                        if fault == 'unsolicited':
                            left.sendall(b'{"command":"commit"}\n')
                        else:
                            left.shutdown(socket.SHUT_WR)
                        started = time.monotonic()
                        try:
                            source.open() if attempt is None else attempt.begin()
                        except bootstrap.BootstrapEvidenceError:
                            pass
                        else:
                            raise AssertionError('A4-H source/phase effects after parent EOF or unsolicited permission')
                        assert time.monotonic()-started < .2 and control.failed, 'A4-H parent check blocked or lost permanent revocation'
                        assert not namespace.exists(), 'A4-H revoked parent allocated intent'
                        if attempt is None:
                            assert not source.connections, 'A4-H revoked source opened SQLite'
                    finally:
                        source.close()
                        if attempt is not None:
                            attempt.close()
                        left.close()
                        right.close()
                        route.close()
                    assert inventory() == original and path.read_bytes() == before, 'A4-H revoked owner changed original'
                    print('PASS A4-H original worker/permit/cancellation:', fault, flush=True)
                    continue

                async def run_worker():
                    worker = bootstrap.BootstrapSupervisor(route, Settings())
                    loop = asyncio.get_running_loop()
                    calls, source_proofs, spawned = [], [], []
                    offset = [0.0]
                    clock = SimpleNamespace(monotonic=lambda: time.monotonic()+offset[0])
                    actual_spawn = asyncio.create_subprocess_exec
                    actual_send = loop.sock_sendall
                    release = asyncio.Event()
                    outer = asyncio.current_task()

                    async def transport_fault(channel, raw):
                        message = json.loads(raw)
                        if message.get('command') == 'commit':
                            if fault == 'foreign_permit':
                                message['attempt_id'] = '0'*32
                                raw = json.dumps(message, separators=(',', ':')).encode()+b'\n'
                            elif fault == 'duplicate_permit':
                                raw += raw
                        return await actual_send(channel, raw)

                    async def delayed_spawn(*args, **kwargs):
                        process = await actual_spawn(*args, **kwargs)
                        spawned.append(process)
                        offset[0] = route.deadline-time.monotonic()+(1 if fault == 'late_spawn_after_timeout' else -19)
                        await release.wait()
                        return process

                    async def qualify(identity, digest):
                        calls.append((identity, digest))
                        # Independent original persisted source, still legacy at
                        # BOTH permission boundaries, with exact original cells.
                        assert identity[:3] == (str(path), *route.fence.database_identity), 'A4-H wrong source binding'
                        assert digest == hashlib.sha256(json.dumps(original, sort_keys=True,
                            separators=(',', ':'), ensure_ascii=False).encode()).hexdigest(), 'A4-H wrong source inventory binding'
                        source_proofs.append(inventory() == original)
                        assert source_proofs[-1], 'A4-H rehearsal/permit after source COMMIT'
                        if len(calls) == 1:
                            assert not namespace.exists(), 'A4-H intent before parent qualification'
                            if fault == 'refuse_source':
                                raise ValueError('synthetic original qualification refusal')
                            if fault == 'qualification_false':
                                return False
                        else:
                            attempts = list(namespace.iterdir())
                            assert len(attempts) == 1, 'A4-H allocated multiple attempts'
                            record = json.loads((attempts[0]/'02.json').read_bytes())
                            assert record['phase'] == 'pre_commit' and not (attempts[0]/'03.json').exists(), 'A4-H permit before durable pre_commit or after COMMIT'
                            if fault == 'refuse_commit':
                                raise ValueError('synthetic final original qualification refusal')
                            if fault == 'cancel':
                                outer.cancel()
                                until = time.monotonic()+2
                                while not worker.expired and time.monotonic() < until:
                                    await asyncio.sleep(.001)
                            if fault == 'kill':
                                os.killpg(worker.process.pid, signal.SIGKILL)
                                await asyncio.shield(worker.reap_task)
                            if fault == 'reserve':
                                offset[0] = route.deadline-time.monotonic()-19
                        return True

                    async def late_release():
                        while not worker.expired:
                            await asyncio.sleep(.001)
                        # The actual returned child is still owned by the pending
                        # spawn; positive closure must not be invented meanwhile.
                        assert worker.spawn is not None and not worker.spawn.done(), 'A4-H lost pending original spawn'
                        assert not worker.reaped and not worker.closed, 'A4-H invented delayed-spawn closure'
                        prior_offset, offset[0] = offset[0], 0
                        second = bootstrap.BootstrapSupervisor(route, Settings())
                        second_task = asyncio.create_task(second.run(qualify))
                        await asyncio.sleep(.01)
                        if not second_task.done():
                            release.set()
                            second_task.cancel()
                            await asyncio.gather(second_task, return_exceptions=True)
                            raise AssertionError('A4-H admitted replacement behind unresolved original spawn')
                        try:
                            await second_task
                        except bootstrap.BootstrapEvidenceError as exc:
                            assert str(exc) == 'workflow_setup_original_worker_in_flight', 'A4-H wrong original-worker refusal'
                        else:
                            raise AssertionError('A4-H admitted replacement behind unresolved original spawn')
                        assert second.spawn is None, 'A4-H replacement spawned before original closure'
                        offset[0] = prior_offset
                        release.set()

                    error, value, releaser = None, None, None
                    with ExitStack() as stack:
                        stack.enter_context(patch.object(bootstrap, 'time', clock))
                        if fault in ('foreign_permit', 'duplicate_permit'):
                            stack.enter_context(patch.object(loop, 'sock_sendall', side_effect=transport_fault))
                        if fault in ('late_spawn', 'late_spawn_after_timeout'):
                            stack.enter_context(patch.object(asyncio, 'create_subprocess_exec', side_effect=delayed_spawn))
                            if fault == 'late_spawn':
                                releaser = asyncio.create_task(late_release())
                        try:
                            value = await worker.run(qualify)
                        except BaseException as exc:
                            error = exc
                        if releaser is not None:
                            await releaser
                        if fault == 'late_spawn_after_timeout':
                            assert not worker.spawn.done() and worker.process is None and not worker.reaped and not worker.closed, 'A4-H dropped unresolved original after deadline'
                            try:
                                route.close()
                            except bootstrap.BootstrapEvidenceError as exc:
                                assert str(exc) == 'workflow_setup_original_worker_in_flight', 'A4-H wrong original discovery close refusal'
                            else:
                                release.set()
                                process = await worker.spawn
                                if process.returncode is None:
                                    os.killpg(process.pid, signal.SIGKILL)
                                await process.wait()
                                raise AssertionError('A4-H released original discovery behind unresolved spawn')
                            release.set()
                        if worker.spawn is not None:
                            process = await worker.spawn
                            await process.wait()
                            if fault == 'late_spawn_after_timeout':
                                assert process.returncode == -signal.SIGKILL, 'A4-H lost late original child kill'
                    if fault == 'success':
                        assert error is None and value['state'] == 'verified' and value['reason'] == 'storage_verified', ('A4-H actual helper failed', error, worker.result)
                        assert worker.reaped and worker.closed and worker.permit_sent and len(calls) == 2, 'A4-H lost exact parent/child closure or repeated permit'
                    else:
                        assert error is not None, ('A4-H refused worker succeeded', fault)
                        assert not worker.result or worker.result['state'] != 'verified', 'A4-H late success after refusal/cancellation'
                        assert worker.expired and (not worker.permit_sent or fault in ('foreign_permit', 'duplicate_permit')), 'A4-H granted COMMIT after stop/reserve/refusal'
                        if fault == 'kill':
                            assert worker.process.returncode == -signal.SIGKILL, 'A4-H lost original pre-COMMIT kill observation'
                        assert isinstance(error, asyncio.CancelledError) == (fault == 'cancel'), 'A4-H swallowed process cancellation'
                        assert len(calls) == (0 if fault in ('source_error', 'late_spawn', 'late_spawn_after_timeout') else 1 if fault in ('refuse_source', 'qualification_false') else 2), 'A4-H intended permission boundary not reached'
                    assert worker.process is not None and worker.process.returncode is not None, 'A4-H original process not reaped'
                    # Even a positively closed child must not drop the parent's
                    # ORIGINAL EX before final registration/status/attachment.
                    route.fence.assert_held()
                    try:
                        other = OrgAccessFence.acquire(root, exclusive=True)
                    except OrgAccessHeld:
                        pass
                    else:
                        other.close()
                        raise AssertionError('A4-H released original parent EX')
                    original_spawn, original_process = worker.spawn, worker.process
                    try:
                        await worker.run(qualify)
                    except bootstrap.BootstrapEvidenceError:
                        pass
                    else:
                        raise AssertionError('A4-H replayed original worker')
                    assert worker.spawn is original_spawn and worker.process is original_process, 'A4-H replayed original worker'
                    return worker

                worker = asyncio.run(run_worker())
                route.close()
                if fault == 'success':
                    attempts = list(namespace.iterdir())
                    assert len(attempts) == 1 and sorted(p.name for p in attempts[0].iterdir()) == [
                        '00.json', '01.json', '02.json', '03.json', '04.json', '05.json', 'before.db'], 'A4-H missing registered phase'
                    with closing(sqlite3.connect(path)) as conn:
                        assert conn.execute('SELECT * FROM a4_helper_sentinel').fetchall() == [(37, b'original\0typed')], 'A4-H changed retained typed/rowid cells'
                        assert conn.execute('SELECT count(*) FROM workflow_seq_definitions').fetchone()[0] == 1, 'A4-H missing actual qualified source registration'
                else:
                    assert inventory() == original and path.read_bytes() == before, 'A4-H failed worker changed original source'
                    if fault in ('refuse_commit', 'cancel', 'kill', 'reserve', 'foreign_permit', 'duplicate_permit'):
                        attempts = list(namespace.iterdir())
                        assert len(attempts) == 1 and (attempts[0]/'02.json').exists() and not (attempts[0]/'03.json').exists(), 'A4-H lost no-replay pre_commit uncertainty'
                        assert (attempts[0]/'before.db').exists(), 'A4-H discarded verified backup'
                    else:
                        assert not namespace.exists(), 'A4-H refused source allocated attempt'
                assert not (home/'daemon.pid').exists() and not (home/'daemon.port').exists(), 'A4-H helper started daemon/listener'
                print('PASS A4-H original worker/permit/cancellation:', fault, flush=True)
    if sys.argv[1:]:
        raise SystemExit(0)

# A4 connection custody and per-org isolation use this existing shell venue.
# Faults/observers are local to each temporary fixture; no production test hook.
if sys.argv[1:] in ([], ['--startup-isolation']):
    import asyncio
    import httpx
    from runtime.infrastructure import remote_job_schema
    from runtime.daemon.routes.orgs import _seed_skeleton
    from runtime.daemon.state import DaemonState
    from runtime.daemon.app import create_app
    from runtime.runtime import RuntimeDir
    from runtime.platform.passthrough_backend import PassthroughBackend
    from runtime.workflows.profile_coordinator import ProfileCoordinator
    from runtime.workflows.authority import WorkflowAuthorityCoordinator
    from runtime.infrastructure.workflow_schema import initialize_complete_org_schema
    from runtime.orchestrator.org_config import initialize_fresh_reviewer_agents

    with tempfile.TemporaryDirectory(prefix='a4-construction-') as scratch:
        for fail_close in (False, True):
            native = sqlite3.connect(Path(scratch) / ('unknown.db' if fail_close else 'closed.db'))
            close_calls = []
            class CloseFault:
                def __getattr__(self, name):
                    return getattr(native, name)
                @property
                def row_factory(self):
                    return native.row_factory
                @row_factory.setter
                def row_factory(self, value):
                    native.row_factory = value
                def close(self):
                    close_calls.append('close')
                    if fail_close:
                        raise OSError('synthetic close uncertainty')
                    native.close()
            connection = CloseFault()
            owner = {}
            primary = ValueError('synthetic constructor refusal')
            try:
                with patch.object(sqlite3, 'connect', return_value=connection), patch.object(
                        remote_job_schema, 'validate_identity_enrollment_schema_preflight', side_effect=primary):
                    try:
                        Database(Path(scratch) / 'private.db', construction_owner=owner, acquisition_timeout=0)
                    except ValueError as exc:
                        assert exc is primary, 'A4-C1 original constructor failure was replaced'
                    else:
                        raise AssertionError('A4-C1 constructor refusal disappeared')
                assert getattr(owner.get('load_db'), '_conn', None) is connection, 'A4-C1 lost original partial connection'
                assert close_calls == ['close'], 'A4-C1 original connection closed more than once'
                assert owner['load_db_closed'] is (not fail_close), 'A4-C1 invented successful closure'
                if fail_close:
                    assert isinstance(owner['load_close_error'], OSError)
                    assert native.execute('SELECT 1').fetchone()[0] == 1
                else:
                    try:
                        native.execute('SELECT 1')
                    except sqlite3.ProgrammingError:
                        pass
                    else:
                        raise AssertionError('A4-C1 positive close left a usable original connection')
                print('PASS A4-C1 original partial constructor custody:', 'retained-unknown' if fail_close else 'closed', flush=True)
            finally:
                native.close()  # the exact synthetic handle, after custody assertions

        busy_path = Path(scratch) / 'busy.db'
        with closing(sqlite3.connect(busy_path)) as writer:
            writer.execute('CREATE TABLE private_fixture (id INTEGER)')
            writer.commit()
            writer.execute('BEGIN EXCLUSIVE')
            owner = {}
            started = time.monotonic()
            try:
                Database(busy_path, construction_owner=owner, acquisition_timeout=0)
            except sqlite3.OperationalError as exc:
                assert getattr(exc, 'sqlite_errorcode', None) == sqlite3.SQLITE_BUSY
            else:
                raise AssertionError('A4-C2 busy source accepted')
            elapsed = time.monotonic() - started
            assert elapsed < .5 and owner['load_db_closed'], ('A4-C2 replenished busy wait', elapsed)
            writer.rollback()
        print('PASS A4-C2 actual exclusive SQLite contention: zero retry, closed; seconds=', elapsed, flush=True)

    def seed_isolation_org(runtime, slug):
        root = runtime.orgs_dir / slug
        _seed_skeleton(root, from_example=None)
        db = Database(root / 'happyranch.db')
        try:
            initialize_complete_org_schema(db, expected_org_slug=slug, layout='J')
            initialize_fresh_reviewer_agents(OrgPaths(root), db)
        finally:
            db.close()
        return root

    for failure in (case for case in ('database', 'post_load', 'selector', 'profile',
                                    'profile_database', 'profile_filesystem', 'profile_capture',
                                    'main_owner', 'main_selector', 'main_sweep', 'main_unexpected', 'main_cancel', 'main_success', 'main_restore')
                    if startup_case is None or case == startup_case):
        with tempfile.TemporaryDirectory(prefix='a4-isolation-') as scratch:
            home = Path(scratch).resolve()
            runtime = RuntimeDir.init(home / 'runtime')
            first_root = seed_isolation_org(runtime, 'alpha')
            second_root = seed_isolation_org(runtime, 'beta')
            if failure in ('selector', 'main_selector', 'main_success'):
                for root in (first_root, second_root):
                    definition = AgentDef(name='manager', team='engineering', role='manager', executor='codex',
                        allow_rules=('happyranch',), repos={}, enrolled_by=None, enrolled_at_task=None,
                        enrolled_at=None, system_prompt='Synthetic private A4 selector manager.')
                    (root / 'org/agents/manager.md').write_text(render_agent_text(definition))
                    (root / 'org/teams.yaml').write_text('teams:\n  engineering:\n    manager: manager\n    workers: []\n')
            state = None
            actual_load = OrgState.load
            reached = []
            selector_calls = []
            actual_selector = WorkflowAuthorityCoordinator.ensure_authority_selector
            actual_capture = ProfileCoordinator._capture_org_dependencies
            def observed_capture(coordinator, org, **kwargs):
                if org.slug == 'alpha':
                    error = sqlite3.OperationalError if failure == 'profile_database' else OSError
                    raise error('PRIVATE profile acquisition diagnostic')
                return actual_capture(coordinator, org, **kwargs)
            @contextmanager
            def failed_inventory(coordinator):
                raise OSError('PRIVATE profile inventory diagnostic')
                yield  # preserve the context-manager protocol; no successful fact
            def observed_selector(authority, **kwargs):
                if kwargs.get('publisher') == 'daemon-state-load:selector-initialization':
                    selector_calls.append(authority._org_slug)
                    if authority._org_slug == 'alpha':
                        raise OSError('PRIVATE selector diagnostic')
                return actual_selector(authority, **kwargs)
            def observed_load(**kwargs):
                org = actual_load(**kwargs)
                reached.append(org.slug)
                if org.slug == 'alpha' and failure == 'post_load':
                    def refuse_queue(queue):
                        raise OSError('PRIVATE post-load diagnostic')
                    org.orchestrator.attach_queue = refuse_queue
                return org
            if failure == 'database':
                (first_root / 'happyranch.db').write_bytes(b'not a SQLite database')
            try:
                with ExitStack() as stack:
                    stack.enter_context(patch.dict(os.environ, {'HAPPYRANCH_DAEMON_HOME': str(home)}))
                    stack.enter_context(patch('runtime.platform.backend_factory.select_session_backend',
                                              return_value=PassthroughBackend()))
                    stack.enter_context(patch.object(OrgState, 'load', side_effect=observed_load))
                    if failure == 'selector':
                        stack.enter_context(patch.object(WorkflowAuthorityCoordinator, 'ensure_authority_selector', observed_selector))
                    if failure in ('profile_database', 'profile_filesystem'):
                        stack.enter_context(patch.object(ProfileCoordinator, '_capture_org_dependencies', observed_capture))
                    if failure == 'profile_capture':
                        stack.enter_context(patch.object(ProfileCoordinator, '_capture_orgs', failed_inventory))
                    if failure == 'profile':
                        stack.enter_context(patch.object(ProfileCoordinator, '_reconcile_startup',
                                                         side_effect=RuntimeError('PRIVATE unexpected profile error')))
                    try:
                        state = DaemonState.from_runtime(runtime, Settings())
                    except Exception as exc:
                        raise AssertionError('A4-C3 runtime aborted before isolated completion: ' + failure) from exc
                    if failure.startswith('main_'):
                        import runtime.daemon.__main__ as entry
                        # Execute the real entry tail AFTER the real state load.
                        # Only the selected failing operation is replaced/denied;
                        # beta still runs its original native tail in order.
                        original_bind = OrgState.bind_authority_v2_owner
                        original_sweep = entry._sweep_on_startup
                        original_publish = entry._publish_v2_generations_on_startup
                        tail, faults, startup_waits = [], [], []
                        primary = KeyboardInterrupt('PRIVATE genuine process cancellation') if failure == 'main_cancel' else (
                            RuntimeError('PRIVATE unexpected entry diagnostic') if failure == 'main_unexpected' else
                            OSError('PRIVATE original entry diagnostic'))
                        def bind(org):
                            if org.slug == 'alpha' and failure == 'main_restore':
                                def restoration_fault(action, name, value, *_):
                                    if action == sqlite3.SQLITE_PRAGMA and name == 'busy_timeout' and value == '5000':
                                        faults.append('native-timeout-restoration-denied')
                                        return sqlite3.SQLITE_DENY
                                    return sqlite3.SQLITE_OK
                                org.db._conn.set_authorizer(restoration_fault)
                            startup_waits.append((org.slug, 'bind', org.db._conn.execute('PRAGMA busy_timeout').fetchone()[0]))
                            tail.append((org.slug, 'bind'))
                            if org.slug == 'alpha' and failure in ('main_owner', 'main_unexpected', 'main_cancel'):
                                faults.append(primary)
                                raise primary
                            return original_bind(org)
                        def selector(authority, **kwargs):
                            if kwargs.get('publisher') == 'daemon-startup:selector-initialization':
                                tail.append((authority._org_slug, 'selector'))
                                if authority._org_slug == 'alpha' and failure == 'main_selector':
                                    faults.append(primary)
                                    raise primary
                            return actual_selector(authority, **kwargs)
                        def sweep(db, queue, slug, orchestrator, *, startup_releases=None, startup_preparations=None):
                            tail.append((slug, 'sweep'))
                            if slug == 'alpha' and failure == 'main_sweep':
                                # The native owner fails on its real first SQL,
                                # rather than on a substituted successful scan.
                                db._conn.set_authorizer(lambda *_: sqlite3.SQLITE_DENY)
                                try:
                                    return original_sweep(db, queue, slug, orchestrator, startup_releases=startup_releases, startup_preparations=startup_preparations)
                                except Exception as exc:
                                    faults.append(exc)
                                    raise
                                finally:
                                    db._conn.set_authorizer(None)
                            return original_sweep(db, queue, slug, orchestrator, startup_releases=startup_releases, startup_preparations=startup_preparations)
                        def publish(org, queue):
                            startup_waits.append((org.slug, 'publish', org.db._conn.execute('PRAGMA busy_timeout').fetchone()[0]))
                            tail.append((org.slug, 'publish'))
                            return original_publish(org, queue)
                        originals = dict(state._all_orgs_snapshot())
                        connections = {slug: org.db._conn for slug, org in originals.items()}
                        from runtime.infrastructure.org_access_fence import original_org_fence
                        fences = {slug: original_org_fence(slug) for slug in originals}
                        with patch.object(OrgState, 'bind_authority_v2_owner', bind), \
                                patch.object(WorkflowAuthorityCoordinator, 'ensure_authority_selector', selector), \
                                patch.object(entry, '_sweep_on_startup', sweep), \
                                patch.object(entry, '_publish_v2_generations_on_startup', publish):
                            try:
                                entry._initialize_runnable_startup(state)
                            except KeyboardInterrupt as exc:
                                assert failure == 'main_cancel' and exc is primary, 'A4-C4 wrong process cancellation'
                            except Exception as exc:
                                raise AssertionError('A4-C4 entry aborted remaining org/listener: ' + failure) from exc
                            else:
                                assert failure != 'main_cancel', 'A4-C4 swallowed process cancellation'
                        for org in originals.values():
                            org.db._conn.set_authorizer(None)
                        assert startup_waits and all(wait == 0 for _, _, wait in startup_waits), ('A4-C4 renewed entry SQLite busy waiting', startup_waits)
                        assert len(faults) == (0 if failure == 'main_success' else 1), ('A4-C4 intended boundary not reached', failure, tail)
                        if failure == 'main_success':
                            assert not state.broken_orgs and not state._startup_held_orgs, 'A4-C4 healthy entry held'
                            assert tail == [(slug, phase) for slug in ('alpha', 'beta') for phase in ('bind', 'selector', 'sweep', 'publish')], ('A4-C4 original entry order', tail)
                            assert all(org.db._conn.execute('PRAGMA busy_timeout').fetchone()[0] == 5000
                                       for org in state.orgs.values()), 'A4-C4 serving timeout not restored'
                        elif failure == 'main_cancel':
                            assert tail == [('alpha', 'bind')] and not state.broken_orgs, 'A4-C4 process cancellation continued orgs'
                        else:
                            assert ('beta', 'sweep') in tail and ('beta', 'publish') in tail, ('A4-C4 later tail lost', tail)
                            assert all(org is state._all_orgs_snapshot()[slug] and org.db._conn is connections[slug]
                                       and original_org_fence(slug) is fences[slug] for slug, org in originals.items()), 'A4-C4 original custody replaced'
                            assert all(conn.execute('SELECT 1').fetchone()[0] == 1 for conn in connections.values()), 'A4-C4 original connection lost'
                            assert 'alpha' in state._startup_held_orgs and 'alpha' not in state.orgs, 'A4-C4 failed org runnable'
                            held = state._startup_held_orgs['alpha']
                            assert held is originals['alpha'] and held._runner_admission_closed, 'A4-C4 failed org runnable'
                            assert held._startup_publication_held, 'A4-C4 failed publication runnable'
                            if failure == 'main_restore':
                                assert faults == ['native-timeout-restoration-denied'] and isinstance(held._startup_authority_error, sqlite3.DatabaseError), 'A4-C4 original restoration failure lost'
                                assert held._startup_authority_error.sqlite_errorcode == sqlite3.SQLITE_AUTH, 'A4-C4 wrong native restoration failure'
                            else:
                                assert held._startup_authority_error is faults[0], 'A4-C4 original failure lost'
                            assert 'PRIVATE' not in state.broken_orgs['alpha'], 'A4-C4 raw entry diagnostic escaped'
                            assert state.profile_coordinator._startup_unknown, 'A4-C4 dependency hold not renewed'
                            assert all(org.db._conn.execute('PRAGMA busy_timeout').fetchone()[0] == 0
                                       for org in state._startup_held_orgs.values()), 'A4-C4 restored held timeout'
                            assert (('alpha', 'publish') in tail) == (failure == 'main_restore'), 'A4-C4 continued failed org tail'
                        print('PASS A4-C4 actual entry tail/original handles/later tail or process cancellation:', failure, flush=True)
                    all_orgs = state._all_orgs_snapshot()
                    assert 'beta' in reached and 'beta' in all_orgs, ('A4-C3 later org not attached', failure, reached)
                    if failure in ('database', 'post_load', 'selector'):
                        assert 'alpha' in state.broken_orgs and 'alpha' not in state.orgs
                        assert 'PRIVATE' not in state.broken_orgs['alpha'], 'A4-C3 raw diagnostic escaped'
                    if failure == 'selector':
                        assert selector_calls == ['alpha', 'beta'], ('A4-C3 intended selector boundary not reached', selector_calls)
                    if failure == 'post_load':
                        assert all_orgs['alpha']._runner_admission_closed
                    if failure.startswith('profile'):
                        assert state.profile_coordinator._startup_unknown
                        assert set(state._startup_held_orgs) == {'alpha', 'beta'} and not state.orgs
                        assert all(org._runner_admission_closed for org in all_orgs.values())
                        assert all('PRIVATE' not in reason for reason in state.broken_orgs.values())
                    async def independent_app_read():
                        app = create_app(state)
                        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://private') as client:
                            response = await client.get('/api/v1/health')
                            assert response.status_code == 200, ('A4-C3 independent health failed', failure, response.status_code)
                    asyncio.run(independent_app_read())
                    assert all(owner.get('slug') != 'alpha' or not owner.get('load_db_closed')
                               for owner in state._attachment_owners.values()), 'A4-C3 closed handle retained as unknown'
                print('PASS A4-C3 per-org startup containment, later actual org, independent ASGI health:', failure, flush=True)
            finally:
                if state is not None:
                    asyncio.run(state.close_all())

    if sys.argv[1:] == ['--startup-isolation']:
        raise SystemExit(0)


# A1 extends this existing disposable shell venue. Real process pipes/files are
# barriers; trace observers record actual opens and never supply gate outcomes.
if sys.argv[1:] in ([], ['--org-access-fence'], ['--org-access-openers'], ['--org-access-native'], ['--org-access-guardian'], ['--org-access-profile']):
    import asyncio
    import importlib
    import subprocess
    import signal
    from runtime.runtime import RuntimeDir
    from runtime.infrastructure.org_access_fence import OrgAccessFence, OrgAccessHeld, original_org_fence
    from runtime.daemon.sessions import OrgFenceGuardian
    from runtime.daemon.routes.orgs import _seed_skeleton, _is_reclaimable_partial, init_org, InitOrgBody
    from runtime.daemon.state import DaemonState
    from runtime.daemon import org_integrity_monitor as monitor
    from runtime.daemon.disk_sweeper import Evidence, _manual_audit

    @contextmanager
    def exclusive_process(root):
        code = ('import sys; from pathlib import Path; '
                'from runtime.infrastructure.org_access_fence import OrgAccessFence; '
                'f=OrgAccessFence.acquire(Path(sys.argv[1]),exclusive=True); '
                'print("locked",flush=True); sys.stdin.buffer.read(1); f.close()')
        child = subprocess.Popen([sys.executable, '-c', code, str(root)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            assert child.stdout.readline() == b'locked\n', child.stderr.read().decode()
            yield
        finally:
            child.stdin.write(b'x')
            child.stdin.close()
            assert child.wait(timeout=5) == 0
            child.stdout.close()
            child.stderr.close()

    def file_state(path):
        out = []
        for suffix in ('', '-wal', '-shm', '-journal'):
            item = Path(str(path) + suffix)
            if not item.exists():
                out.append(None)
            else:
                value = item.stat()
                # This observer's read changes atime on strict-atime mounts.
                # Preserve every source identity/write timestamp and exact bytes.
                out.append(((value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
                             value.st_size, value.st_mtime_ns, value.st_ctime_ns),
                            hashlib.sha256(item.read_bytes()).hexdigest()))
        return out

    def acquire_ex(root):
        try:
            fence = OrgAccessFence.acquire(root, exclusive=True)
        except OrgAccessHeld:
            return False
        else:
            fence.close()
            return True

    if sys.argv[1:] in ([], ['--org-access-fence'], ['--org-access-openers']):
        with tempfile.TemporaryDirectory(prefix='a1-openers-', dir='/tmp') as scratch:
            home = Path(scratch).resolve()
            runtime = RuntimeDir.init(home / 'runtime')
            root = runtime.orgs_dir / 'scratch'
            _seed_skeleton(root, from_example=None)
            path = root / 'happyranch.db'
            db = Database(path)
            db.close()
            before = file_state(path)
            with patch.dict(os.environ, {'HAPPYRANCH_DAEMON_HOME': str(home)}):
                (home / 'runtimes.yaml').write_text('active: ' + str(runtime.root) + '\nregistered:\n- ' + str(runtime.root) + '\n')
                state = SimpleNamespace(runtime=runtime, orgs={}, settings=Settings(), is_idle=False)
                def setup_attempt(module, method, args):
                    clean = {key: value for key, value in os.environ.items() if key not in module._MARKERS}
                    with patch.dict(os.environ, clean, clear=True):
                        return getattr(module, method)(args)
                cases = [
                    ('org-state', lambda: OrgState.load(slug='scratch', root=root, settings=Settings())),
                    ('add-org-load-owner', lambda: DaemonState._load_attachment(state, {'slug': 'scratch', 'root': root})),
                    ('fresh-route', lambda: asyncio.run(init_org(InitOrgBody(slug='scratch'), SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(daemon=state)))))),
                    ('reclaim-reader', lambda: _is_reclaimable_partial(root)),
                    ('daily-reader', lambda: monitor.read_snapshot(root, time.monotonic() + 2)),
                    ('daily-audit', lambda: monitor._audit(root, {'snapshot_identity': None}, time.monotonic() + 2)),
                    ('sweep-reader', lambda: Evidence(root, time.monotonic() + 2)._connection().__enter__()),
                    ('sweep-audit', lambda: _manual_audit({'orgs': []}, [str(root)], home / 'summary.json')),
                    ('setup-preflight', lambda: importlib.import_module('scripts._coding_setup_preflight').read_database(path, org_root=root).__enter__()),
                    ('sequential-coding', lambda: setup_attempt(importlib.import_module('scripts.setup_sequential_coding'), 'operation', SimpleNamespace(runtime_root=runtime.root, org='scratch', confirm_cooperative_offline=True, action='register', operation_key=None))),
                    ('sequential-venue', lambda: setup_attempt(importlib.import_module('scripts.setup_sequential_venue'), '_publish', SimpleNamespace(runtime_root=runtime.root, org='scratch', confirm_disposable_venue=True))),
                ]
                for name in ('draft', 'submission', 'coding'):
                    module = importlib.import_module('scripts.migrate_workflow_' + name + '_schema')
                    for check in (True, False):
                        cases.append(('offline-' + name + ('-check' if check else '-write'), lambda module=module, check=check: module.main(['--runtime-root', str(runtime.root), '--org', 'scratch'] + (['--check'] if check else []))))
                for name in ('author_handoff', 'document_revision'):
                    module = importlib.import_module('scripts.migrate_workflow_' + name + '_schema')
                    for check in (True, False):
                        cases.append(('offline-' + name + ('-check' if check else '-write'), lambda module=module, check=check: module.main(['--org-root', str(root), '--org-slug', 'scratch'] + (['--check'] if check else []))))
                module = importlib.import_module('scripts.install_workflow_sequential_schema')
                for check in (True, False):
                    cases.append(('offline-install' + ('-check' if check else '-write'), lambda check=check: module.main(['--runtime-root', str(runtime.root), '--org', 'scratch'] + (['--check'] if check else []))))
                module_settle = importlib.import_module('scripts.settle_native_writer')
                for apply in (False, True):
                    cases.append(('offline-settle' + ('-write' if apply else '-check'), lambda apply=apply: module_settle.main(['--runtime-root', str(runtime.root), '--org', 'scratch', '--writer-id', 'a' * 32, '--justification', 'Synthetic original owner custody observation'] + (['--apply'] if apply else []))))
                raw_connect = sqlite3.connect
                for name, opener in cases:
                    opened, constructed, attempted = [], [], []
                    real_init = Database.__init__
                    real_acquire = OrgAccessFence.acquire.__func__
                    def observed_acquire(cls, root, **kwargs):
                        attempted.append(str(root))
                        return real_acquire(cls, root, **kwargs)
                    def observed_connect(*args, **kwargs):
                        if str(path) in str(args[0]):
                            opened.append(str(args[0]))
                        return raw_connect(*args, **kwargs)
                    def observed_constructor(self, *args, **kwargs):
                        constructed.append(str(args[0]) if args else str(kwargs))
                        return real_init(self, *args, **kwargs)
                    with exclusive_process(root), patch('sqlite3.connect', observed_connect), patch.object(Database, '__init__', observed_constructor), patch.object(OrgAccessFence, 'acquire', classmethod(observed_acquire)):
                        try:
                            result = opener()
                        except Exception as exc:
                            result = str(exc)
                        assert not opened and not constructed, ('A1-before-open:' + name, opened, constructed, result)
                        assert attempted, ('A1 gate was not reached:' + name, result)
                        assert file_state(path) == before, 'A1-no-DB-effect:' + name
                    print('PASS A1 EX refuses before open with no DB effect:', name, flush=True)
                # The shared backup reader also owns non-org SQL sources.
                # Its admitted global lane must remain usable without org gates.
                global_path = home / 'runtime-audit.db'
                global_db = Database(global_path)
                global_db.close()
                preflight = importlib.import_module('scripts._coding_setup_preflight')
                try:
                    with preflight.read_database(global_path) as conn:
                        assert conn.execute('SELECT count(*) FROM sqlite_schema').fetchone()[0] > 0
                except OrgAccessHeld as exc:
                    raise AssertionError('A1 machine-global reader was wrongly org-fenced') from exc
                print('PASS A1 shared machine-global reader preserves its non-org exclusion', flush=True)
                # A pre-protocol original SQL owner has no usable fence. Its
                # actual fallback must refuse before closing/reopening SQL.
                import threading
                legacy = Database(path)
                closed = OrgAccessFence.acquire(root)
                closed.close()
                legacy._org_access_fence = closed
                move = SimpleNamespace(store=SimpleNamespace(db=legacy, org_slug='scratch'), gate=threading.RLock())
                owner = producer_move.ProducerMoveAdmission(move)
                legacy._conn.execute('BEGIN')
                opened = []
                def observe_fallback(*args, **kwargs):
                    opened.append(str(args[0]))
                    return raw_connect(*args, **kwargs)
                try:
                    with exclusive_process(root), patch('sqlite3.connect', observe_fallback):
                        try:
                            owner.retain_failed_naming_load()
                        except OrgAccessHeld as exc:
                            assert str(exc) == 'org_access_original_custody_unavailable'
                        else:
                            raise AssertionError('A1 old native SQL fallback did not HOLD')
                        assert not opened, 'A1 old native SQL fallback opened under EX'
                finally:
                    legacy._conn.rollback()
                    legacy.close()
                print('PASS A1 original native fallback with old/closed custody HOLD before SQL', flush=True)
                # Independent unknown original owner, never a PID disappearance.
                try:
                    original_org_fence('scratch')
                except OrgAccessHeld:
                    pass
                else:
                    raise AssertionError('A1 old/unregistered original owner must HOLD')
                print('PASS A1 old/unregistered original owner HOLD', flush=True)

        from runtime.infrastructure import org_access_fence as access_fences
        async def refused_before_open_case():
            with tempfile.TemporaryDirectory(prefix='a1-before-open-') as scratch:
                home = Path(scratch).resolve()
                with patch.dict(os.environ, {'HAPPYRANCH_DAEMON_HOME': str(home)}):
                    runtime = RuntimeDir.init(home / 'runtime')
                    state = DaemonState(runtime=runtime, settings=Settings())
                    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(daemon=state)))
                    root = runtime.orgs_dir / 'scratch'
                    fd_dir = Path('/proc/self/fd' if sys.platform == 'linux' else '/dev/fd')
                    baseline_fds = len(list(fd_dir.iterdir()))
                    baseline_fences = set(access_fences._uncertain)
                    opened = []
                    real_connect = sqlite3.connect
                    def observe_connect(*args, **kwargs):
                        opened.append(args[0])
                        return real_connect(*args, **kwargs)
                    try:
                        with patch('sqlite3.connect', observe_connect):
                            for _ in range(4):
                                try:
                                    await init_org(InitOrgBody(slug='scratch', from_example=str(home / 'absent')), request)
                                except Exception as exc:
                                    assert getattr(exc, 'status_code', None) == 400, exc
                                else:
                                    raise AssertionError('A1 invalid example unexpectedly created org')
                                assert not root.exists() and not state.orgs and not opened
                                assert access_fences._uncertain == baseline_fences, 'A1 before-open refusal retained fence'
                                assert len(list(fd_dir.iterdir())) == baseline_fds, 'A1 before-open refusal leaked FD'
                                assert acquire_ex(root), 'A1 effect-free refusal retained SH'
                        print('PASS A1 repeated HTTP400 before-open refusals restore FD/fence baselines with zero SQLite opens', flush=True)
                        # No positive resource-closure proof: the context must still HOLD.
                        try:
                            with access_fences.org_access(root) as unknown:
                                raise OSError('private unknown resource ownership')
                        except OSError:
                            pass
                        try:
                            assert unknown in access_fences._uncertain and not acquire_ex(root)
                            assert len(list(fd_dir.iterdir())) == baseline_fds + 2
                        finally:
                            unknown.close()
                        assert access_fences._uncertain == baseline_fences
                        assert len(list(fd_dir.iterdir())) == baseline_fds and acquire_ex(root)
                        print('PASS A1 unknown resource exception retains both descriptors until positive original close', flush=True)
                    finally:
                        state.metrics_store.close()
                        state.direct_connect_authority_store.close()
        asyncio.run(refused_before_open_case())

        async def existing_directory_refusal_case(kind):
            with tempfile.TemporaryDirectory(prefix='a1-existing-refusal-') as scratch:
                home = Path(scratch).resolve()
                with patch.dict(os.environ, {'HAPPYRANCH_DAEMON_HOME': str(home)}):
                    runtime = RuntimeDir.init(home / 'runtime')
                    state = DaemonState(runtime=runtime, settings=Settings())
                    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(daemon=state)))
                    root = runtime.orgs_dir / 'retained'
                    root.mkdir()
                    db_path = root / 'happyranch.db'
                    if kind == 'workspace-only':
                        (root / 'workspaces').mkdir()
                        user_file = root / 'workspaces' / 'user-data'
                        user_file.write_bytes(b'preserve original workspace bytes')
                    else:
                        with closing(sqlite3.connect(db_path)) as seed:
                            seed.execute('CREATE TABLE durable(value)')
                            seed.execute('INSERT INTO durable VALUES (42)')
                            seed.commit()
                    before_db = file_state(db_path)
                    fd_dir = Path('/proc/self/fd' if sys.platform == 'linux' else '/dev/fd')
                    baseline_fds = len(list(fd_dir.iterdir()))
                    baseline_fences = set(access_fences._uncertain)
                    opened, closed = [], []
                    real_connect = sqlite3.connect

                    class ObservedConnection(sqlite3.Connection):
                        def execute(self, *args, **kwargs):
                            if kind == 'probe-error':
                                raise sqlite3.OperationalError('private probe failure after real open')
                            return super().execute(*args, **kwargs)

                        def close(self):
                            super().close()
                            closed.append(self)

                    def observe_connect(*args, **kwargs):
                        conn = real_connect(*args, **kwargs, factory=ObservedConnection)
                        opened.append(conn)
                        return conn

                    try:
                        with patch('sqlite3.connect', observe_connect):
                            for attempt in range(1 if kind == 'probe-error' else 3):
                                try:
                                    await init_org(InitOrgBody(slug='retained'), request)
                                except Exception as exc:
                                    assert getattr(exc, 'status_code', None) == 409, exc
                                    assert exc.detail['code'] == 'org_dir_has_data', exc.detail
                                else:
                                    raise AssertionError('A1 existing data unexpectedly reclaimed')
                                assert root.is_dir() and not state.orgs
                                assert file_state(db_path) == before_db, 'A1 refusal changed durable DB bytes/identity'
                                if kind == 'workspace-only':
                                    assert user_file.read_bytes() == b'preserve original workspace bytes'
                                    assert not opened, 'A1 workspace-only refusal opened SQLite'
                                elif kind == 'closed-db':
                                    assert len(opened) == attempt + 1 and opened == closed, 'A1 probe did not positively close every connection'
                                else:
                                    assert len(opened) == 1 and not closed, 'A1 probe error lost unknown connection'
                                    assert len(set(access_fences._uncertain) - baseline_fences) == 1
                                    assert len(list(fd_dir.iterdir())) > baseline_fds and not acquire_ex(root)
                                    print('PASS A1 existing-directory probe-error retains original route HOLD without closure proof', flush=True)
                                    continue
                                assert access_fences._uncertain == baseline_fences, f'A1 {kind} refusal retained fence'
                                assert len(list(fd_dir.iterdir())) == baseline_fds, f'A1 {kind} refusal leaked FD'
                                assert acquire_ex(root), f'A1 {kind} refusal retained SH'
                                print(f'PASS A1 existing-directory {kind} HTTP409 preserves data and restores FD/fence/EX (attempt {attempt + 1})', flush=True)
                        if kind != 'workspace-only':
                            with closing(real_connect(db_path)) as verify:
                                assert verify.execute('SELECT value FROM durable').fetchall() == [(42,)]
                    finally:
                        # Close only these private original resources after observations.
                        for conn in opened:
                            if conn not in closed:
                                conn.close()
                        for fence in set(access_fences._uncertain) - baseline_fences:
                            fence.close()
                        assert access_fences._uncertain == baseline_fences
                        assert len(list(fd_dir.iterdir())) == baseline_fds and acquire_ex(root)
                        state.metrics_store.close()
                        state.direct_connect_authority_store.close()

        refusal_failures = []
        for refusal_kind in ('workspace-only', 'closed-db', 'probe-error'):
            try:
                asyncio.run(existing_directory_refusal_case(refusal_kind))
            except AssertionError as exc:
                refusal_failures.append(f'{refusal_kind}: {exc}')
                print(f'FAIL A1 existing-directory {refusal_kind}: {exc}', flush=True)
        assert not refusal_failures, refusal_failures

    if sys.argv[1:] in ([], ['--org-access-fence'], ['--org-access-profile']):
        from runtime.workflows.profile_coordinator import ProfileCoordinator
        with tempfile.TemporaryDirectory(prefix='a1-profile-order-') as scratch:
            home = Path(scratch).resolve()
            roots = [home / 'runtime/orgs' / name for name in ('beta', 'alpha')]
            for root in roots:
                root.mkdir(parents=True)
            with patch.dict(os.environ, {'HAPPYRANCH_DAEMON_HOME': str(home)}):
                coordinator = ProfileCoordinator(daemon_home=home,
                    orgs={root.name: SimpleNamespace(root=root) for root in roots})
                opened = []
                real_open = os.open
                def observe_lock_open(path, *args, **kwargs):
                    fd = real_open(path, *args, **kwargs)
                    if str(path).endswith('.lock'):
                        opened.append(Path(path).name)
                    return fd
                expected = sorted(hashlib.sha256((str(root.parent.parent) + '\0' + root.name).encode()).hexdigest() + '.lock' for root in roots)
                expected += [hashlib.sha256(b'synthetic').hexdigest() + '.lock']
                with patch('os.open', observe_lock_open), coordinator._profile_leases(('synthetic',), wait=False):
                    assert opened == expected, ('A1 physical org/profile lock order', opened, expected)
                # An EX conflict on any dependency refuses before profile files.
                other_home = home / 'profile-coordinator-locks'
                before = sorted(other_home.iterdir())
                with exclusive_process(roots[0]):
                    try:
                        with coordinator._profile_leases(('other-profile',), wait=False):
                            raise AssertionError('A1 profile writer crossed org EX')
                    except OrgAccessHeld:
                        pass
                    assert sorted(other_home.iterdir()) == before, 'A1 profile effect before all org gates'
                catalog = lambda: [(root.name, root) for root in roots]
                startup = ProfileCoordinator(daemon_home=home, orgs={}, org_roots=catalog)
                before = sorted(other_home.iterdir())
                with exclusive_process(roots[0]):
                    try:
                        with startup.profile_read('startup-cache'):
                            raise AssertionError('A1 startup cache crossed unloaded org EX')
                    except OrgAccessHeld:
                        pass
                from runtime.workflows.profile_coordinator import ProfileCoordinatorError
                try:
                    with startup._profile_leases(('unloaded-writer',), wait=False):
                        raise AssertionError('A1 writer accepted unloaded dependency')
                except ProfileCoordinatorError as exc:
                    assert exc.code == 'profile_consumer_unloaded'
                assert sorted(other_home.iterdir()) == before, 'A1 unloaded dependency produced profile effects'
                print('PASS A1 real sorted org descriptors before profile flock; startup EX and unloaded writer refuse before profile effect', flush=True)

        async def attachment_boundary_case():
            with tempfile.TemporaryDirectory(prefix='a1-attachment-') as scratch:
                home = Path(scratch).resolve()
                with patch.dict(os.environ, {'HAPPYRANCH_DAEMON_HOME': str(home)}):
                    runtime = RuntimeDir.init(home / 'runtime')
                    state = DaemonState(runtime=runtime, settings=Settings())
                    state.profile_coordinator = ProfileCoordinator(daemon_home=home,
                        orgs=state.orgs, capture_orgs=state._capture_profile_orgs,
                        hold_startup=state._hold_startup_orgs, org_roots=runtime.iter_org_roots)
                    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(daemon=state)))
                    root = runtime.orgs_dir / 'scratch'
                    try:
                        result = await init_org(InitOrgBody(slug='scratch'), request)
                        assert result == {'slug': 'scratch', 'root': str(root)}, result
                        assert state.orgs['scratch'].root == root and (root / 'happyranch.db').is_file()
                        assert not acquire_ex(root), 'A1 original created attachment must retain SH'
                        await state.remove_org('scratch')
                        assert acquire_ex(root), 'A1 removed original attachment did not close'
                        org = await state.add_org('scratch')
                        assert state.orgs['scratch'] is org and not acquire_ex(root)
                        print('PASS A1 original empty-org create and existing on-disk attachment publish successfully', flush=True)
                        await state.remove_org('scratch')
                        # The pending candidate is legitimate; this OTHER unloaded
                        # consumer must still prevent publication, with no profile effect.
                        _seed_skeleton(runtime.orgs_dir / 'unrelated', from_example=None)
                        profile_dir = home / 'profile-coordinator-locks'
                        before = sorted(profile_dir.iterdir()) if profile_dir.exists() else []
                        try:
                            await state.add_org('scratch')
                        except ProfileCoordinatorError as exc:
                            assert exc.code == 'profile_consumer_unloaded', exc.code
                        else:
                            raise AssertionError('A1 original attachment accepted unrelated unloaded consumer')
                        assert not state.orgs and (root / 'happyranch.db').is_file()
                        assert acquire_ex(root), 'A1 rejected attachment original connection did not close'
                        assert (sorted(profile_dir.iterdir()) if profile_dir.exists() else []) == before
                        print('PASS A1 original attachment still rejects unrelated unloaded consumer before publication/profile effect', flush=True)
                    finally:
                        for slug in tuple(state.orgs):
                            await state.remove_org(slug)
                        state.metrics_store.close()
                        state.direct_connect_authority_store.close()
        asyncio.run(attachment_boundary_case())

    if sys.argv[1:] in ([], ['--org-access-fence'], ['--org-access-guardian']):
        with tempfile.TemporaryDirectory(prefix='a1-guardian-') as scratch:
            home = Path(scratch).resolve()
            root = home / 'runtime/orgs/scratch'
            root.mkdir(parents=True)
            with patch.dict(os.environ, {'HAPPYRANCH_DAEMON_HOME': str(home)}):
                fence = OrgAccessFence.acquire(root)
                guardian = OrgFenceGuardian(fence)
                fence.close()
                try:
                    assert not acquire_ex(root), 'A1 guardian released before original native closure'
                    guardian.close_confirmed()
                    assert acquire_ex(root), 'A1 positive original guardian close did not release'
                finally:
                    if guardian in OrgFenceGuardian._held:
                        guardian.close_confirmed()
                # Loss of the original parent channel is unknown; EOF cannot close.
                fence = OrgAccessFence.acquire(root)
                unknown = OrgFenceGuardian(fence)
                fence.close()
                unknown.process.stdin.close()
                try:
                    try:
                        unknown.process.wait(timeout=.1)
                    except subprocess.TimeoutExpired:
                        pass
                    else:
                        raise AssertionError('A1 unknown guardian custody released on EOF')
                    assert not acquire_ex(root), 'A1 unknown guardian custody must HOLD'
                finally:
                    # Only this synthetic guardian and its own new process group.
                    if unknown.process.poll() is None:
                        os.killpg(unknown.process.pid, signal.SIGKILL)
                    unknown.process.wait(timeout=5)
                    unknown.process.stdout.close()
                    OrgFenceGuardian._held.remove(unknown)
                print('PASS A1 original guardian interval, positive close and unknown EOF HOLD', flush=True)

        from runtime.orchestrator.host_supervisor import AdmissionRequest, HostSessionSupervisor, canary_policy
        from runtime.orchestrator.task_pause import DeferredRootPause
        from runtime.platform.passthrough_backend import PassthroughBackend
        from runtime.platform.session_backend import LaunchSpec
        from runtime.infrastructure.org_access_fence import bind_org_fence, unbind_org_fence
        # Use real admission/preparation and the actual guardian finalizer. The
        # oracle is a fresh EX acquisition after the original attachment closes.
        for refusal in ('provider-wrapper', 'final-validator', 'final-deferral',
                        'task-commit', 'task-commit-deferral', 'uncertain-abandon'):
            with tempfile.TemporaryDirectory(prefix='a1-supervisor-') as scratch:
                home = Path(scratch).resolve()
                root = home / 'runtime/orgs/scratch'
                root.mkdir(parents=True)
                with patch.dict(os.environ, {'HAPPYRANCH_DAEMON_HOME': str(home)}):
                    fence = OrgAccessFence.acquire(root)
                    bind_org_fence(fence)
                    backend = PassthroughBackend()
                    supervisor = HostSessionSupervisor(backend=backend, policy=canary_policy(),
                        publisher=lambda receipt: None)
                    request = AdmissionRequest(org='scratch', invocation_kind='task',
                        logical_id='TASK-private', executor_profile='synthetic')
                    lease = supervisor._admission.acquire(request, timeout=1)
                    assert lease is not None
                    before = set(OrgFenceGuardian._held)
                    visited = []
                    error = DeferredRootPause('private refusal') if refusal.endswith('deferral') else ValueError('private refusal')
                    def veto(*args):
                        visited.append(refusal)
                        raise error
                    def final_validator():
                        veto()
                    if refusal.startswith('task-commit'):
                        final_validator._task_pause_commit = veto
                    real_abandon = backend.abandon
                    def abandon(pending):
                        visited.append('abandon')
                        if refusal == 'uncertain-abandon':
                            raise OSError('private unconfirmed abandonment')
                        real_abandon(pending)
                    def never_launch(*args):
                        raise AssertionError('A1 refused supervisor reached native launch/body')
                    try:
                        with patch.object(backend, 'abandon', abandon), patch.object(backend, 'launch', never_launch):
                            try:
                                outcome = supervisor._execute_attempt(request, ctx=lease.ctx,
                                    launch_spec=LaunchSpec(argv=('/bin/true',)), launch_body=never_launch,
                                    grace_seconds=1, attempt=0,
                                    prelaunch_wrapper=veto if refusal == 'provider-wrapper' else None,
                                    final_prelaunch_validator=None if refusal == 'provider-wrapper' else final_validator)
                            except (ValueError, DeferredRootPause) as exc:
                                assert exc is error and refusal in ('provider-wrapper', 'final-deferral', 'task-commit', 'task-commit-deferral')
                            else:
                                assert refusal in ('final-validator', 'uncertain-abandon')
                                assert outcome.receipt is None and 'private refusal' in outcome.error
                        assert visited == [refusal, 'abandon'], visited
                        unbind_org_fence(fence)
                        fence.close()
                        retained = set(OrgFenceGuardian._held) - before
                        if refusal == 'uncertain-abandon':
                            assert len(retained) == 1 and not acquire_ex(root), 'A1 uncertain abandonment must HOLD'
                        else:
                            assert not retained and acquire_ex(root), 'A1 positive never-launched abandonment leaked guardian: ' + refusal
                        print('PASS A1 actual supervisor refusal custody:', refusal, flush=True)
                    finally:
                        lease.release()
                        if not fence._closed:
                            unbind_org_fence(fence)
                            fence.close()
                        # Only synthetic resources from this case, after the HOLD oracle.
                        for guardian in set(OrgFenceGuardian._held) - before:
                            guardian.close_confirmed()

    if sys.argv[1:] in ([], ['--org-access-fence'], ['--org-access-native']):
        from runtime.daemon import jobs_runner as job_runner
        from runtime.models import JobRecord, JobInterpreter
        async def native_job_case():
            with tempfile.TemporaryDirectory(prefix='a1-native-job-') as scratch:
                home = Path(scratch).resolve()
                root = home / 'runtime/orgs/scratch'
                root.mkdir(parents=True)
                with patch.dict(os.environ, {'HAPPYRANCH_DAEMON_HOME': str(home)}):
                    fence = OrgAccessFence.acquire(root)
                    info = os.fstat(fence.fd)
                    db = Database(root / 'happyranch.db')
                    db._org_access_fence = fence
                    db.insert_task(TaskRecord(id='TASK-a1', brief='Synthetic native child owner', assigned_agent='synthetic'))
                    db.insert_job(JobRecord(id='JOB-001', task_id='TASK-a1', agent_name='synthetic',
                        title='Synthetic inherited descriptor', rationale='Private A1 original custody proof',
                        script_text='', interpreter=JobInterpreter.PYTHON3, created_at='2031-01-01T00:00:00+00:00'))
                    gate = home / 'release'
                    script = f'''import os, json, time
from pathlib import Path
wanted = {(info.st_dev, info.st_ino)!r}
inherited = False
for fd in range(3, 256):
    try:
        s = os.fstat(fd)
    except OSError:
        continue
    inherited |= (s.st_dev, s.st_ino) == wanted
print(json.dumps({{'inherited': inherited}}), flush=True)
end = time.monotonic() + 10
while not Path({str(gate)!r}).exists() and time.monotonic() < end:
    time.sleep(.005)
'''
                    ready = asyncio.Event()
                    lines = []
                    async def publish(event):
                        if event.get('stream') == 'stdout':
                            lines.append(json.loads(event['line']))
                            ready.set()
                    registration = None
                    async def launch():
                        return await job_runner.run_job(job_id='JOB-001', task_id='TASK-a1', registration=registration,
                            script_text=script, interpreter='python3', cwd=str(root),
                            stdout_path=str(home / 'out'), stderr_path=str(home / 'err'),
                            max_runtime_seconds=15, publish=publish)
                    task = asyncio.create_task(launch())
                    registration = job_runner.register_runner_task('JOB-001', task, db=db, org_slug='scratch')
                    try:
                        await asyncio.wait_for(ready.wait(), 5)
                        assert lines == [{'inherited': True}], 'A1-JOB inherited original descriptor missing'
                        # Close every parent copy. Only the real child interval
                        # can keep EX out now, so a missing pass_fds is detectable.
                        fence.close()
                        os.close(registration.org_fence_fd)
                        registration.org_fence_fd = None
                        assert not acquire_ex(root), 'A1-JOB native child released its original fence early'
                    finally:
                        gate.touch()
                        result = await asyncio.wait_for(task, 5)
                        db.close()
                        fence.close()
                    assert result.exit_code == 0 and registration.observed
                    assert job_runner.confirm_job_processes_empty(registration), 'A1-JOB positive closure missing'
                    assert acquire_ex(root), 'A1-JOB confirmed closure did not release'
                    job_runner._RUNNER_TASKS.pop(registration.key)
                    print('PASS A1 real native JOB original descriptor survives parent copies until confirmed closure', flush=True)
        asyncio.run(native_job_case())
    if sys.argv[1:]:
        raise SystemExit(0)

# I2 observes every native connection from connect() onward. Database trace
# registrations compose with the shipping owner; auxiliary/reference connections
# retain their own native slot. No subclass, validator result or epoch is supplied.
@contextmanager
def inline_sql_observation(case, *, collected_failures=None):
    import re
    real_connect = sqlite3.connect
    real_retrofit = Database._retrofit_v2_attempts_inert_closed
    real_close = Database.close
    real_constructor = Database.__init__
    real_complete = load_guard._complete_sequential
    connections, leases, reads, physical, failures = [], {}, [], [], []
    active = []
    statements = []

    def trace(conn, sql):
        try:
            statement = sql.strip().lower()
            if re.match(r'^pragma\s+(?:(?:main|temp)\s*\.\s*)?(?:integrity_check|quick_check)\b', statement):
                physical.append((connections.index(conn), statement))
            statements.append((conn, statement))
            if not active:
                return
            record = active[-1]
            if statement == 'pragma foreign_key_check':
                record['fk'].append(conn)
            if not statement.startswith('select rowid'):
                return
            frame = sys._getframe(1)
            while frame is not None:
                name = frame.f_code.co_name
                file = Path(frame.f_code.co_filename).name
                if ((file == 'coding_start_history.py' and name == '_reserved_rows') or
                        (file == 'workflow_coding_schema.py' and name == '_coding_scan_rows')):
                    table = frame.f_locals['table']
                    if table in ('task_results', 'workflow_task_results'):
                        record['scans'].append((name, table, conn, statement))
                if file == 'sequential_history.py' and name == 'read_sequential_history':
                    record['budgets'] = {key: frame.f_locals[key] for key in ('observed', 'reference', 'budget', 'legacy')}
                frame = frame.f_back
        except BaseException as exc:
            failures.append(type(exc).__name__)

    def connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        assert type(conn) is sqlite3.Connection, case + ' lost native connection'
        connections.append(conn)  # Strong identity; closed connections cannot recycle IDs.
        conn.set_trace_callback(lambda sql: trace(conn, sql))
        return conn

    def retrofit(database):
        context = database.observe_trace(lambda sql: trace(database._conn, sql))
        lease = context.__enter__()
        leases[id(database)] = context, lease
        return real_retrofit(database)

    def close(database):
        item = leases.pop(id(database), None)
        if item is not None:
            context, lease = item
            context.__exit__(None, None, None)
            if lease.failed:
                failures.append('managed_trace_delivery')
        return real_close(database)

    def constructor(database, *args, **kwargs):
        try:
            return real_constructor(database, *args, **kwargs)
        except BaseException:
            item = leases.pop(id(database), None)
            if item is not None:
                context, lease = item
                try:
                    context.__exit__(None, None, None)
                except sqlite3.ProgrammingError:
                    # The real constructor already closed/discarded its native
                    # connection. Prove that fact, preserving its primary error.
                    try:
                        database._conn.execute('SELECT 1')
                    except sqlite3.ProgrammingError:
                        pass
                    else:
                        raise AssertionError(case + ' constructor failed to close native connection')
            raise

    def complete(conn, **kwargs):
        budget = kwargs['budget']
        before = (budget.consumed, budget.accepted_rows, budget.traversed)
        transaction = conn.in_transaction
        record = dict(conn=conn, fk=[], scans=[])
        active.append(record)
        try:
            result = real_complete(conn, **kwargs)
        finally:
            active.pop()
        assert conn.in_transaction == transaction, case + ' complete read stole caller transaction'
        record['charges'] = tuple(a-b for a,b in zip(
            (budget.consumed, budget.accepted_rows, budget.traversed), before))
        assert all(value >= 0 for value in record['charges']) and any(record['charges']), case + ' lost logical charges'
        assert record['fk'] == [conn], case + ' whole FK count per complete candidate: ' + str(len(record['fk']))
        queued = [row for row in record['scans'] if row[:2] == ('_reserved_rows', 'task_results')]
        metadata = [row for row in record['scans'] if row[:2] == ('_coding_scan_rows', 'task_results')]
        assert len(queued) == len(metadata) == 1, case + ' native result scans per candidate: ' + str([(r[0],r[1]) for r in record['scans']])
        assert all(row[2] is conn for row in record['scans']), case + ' result scan on different connection'
        # The old substring counter also included this DIFFERENT workflow table.
        workflow = [row for row in record['scans'] if row[1] == 'workflow_task_results']
        assert len(workflow) == 1 and workflow[0][0] == '_coding_scan_rows', case + ' workflow result metadata scan'
        budgets = record['budgets']
        assert {key: value.sql_limit for key, value in budgets.items()} == dict(
            observed=2000000, reference=2000000, budget=2000000, legacy=32000000), case + ' changed A/B/C/L limits'
        assert len({value.deadline for value in budgets.values()}) == 1, case + ' reset complete-read deadline'
        assert all(value.sql_steps <= value.sql_limit for value in budgets.values()), case + ' exceeded original SQL budgets'
        assert all(value.deadline > time.monotonic() for value in budgets.values()), case + ' complete reader exceeded deadline'
        reads.append(record)
        print('PASS', case, 'candidate', len(reads), 'connection', connections.index(conn),
            'whole-FK=1; scans=task_results:queued1,metadata1; workflow_task_results:metadata1;',
            'cumulative-logical-delta', record['charges'], 'SQL', {key: value.sql_steps for key,value in budgets.items()}, flush=True)
        return result

    with (patch.object(sqlite3, 'connect', connect),
          patch.object(Database, '_retrofit_v2_attempts_inert_closed', retrofit),
          patch.object(Database, 'close', close),
          patch.object(Database, '__init__', constructor),
          patch.object(load_guard, '_complete_sequential', complete)):
        yield SimpleNamespace(reads=reads, statements=statements, connections=connections)
    assert not leases and not failures, (case, 'trace custody/failure', len(leases), failures)
    if physical:
        message = case + ' physical SQL across constructor/load/auxiliary/tails: ' + str(physical)
        if collected_failures is None:
            raise AssertionError(message)
        collected_failures.append(message)
        print('FAIL', message, flush=True)
        return
    print('PASS', case, 'whole-native-connections', len(connections), 'complete-reads', len(reads),
          ('whole-FK/native-queued/native-metadata/workflow-metadata=1/1/1/1 per complete candidate; physical=0'
           if reads else 'physical=0; no sequential complete reader in this case'), flush=True)


if sys.argv[1:] in ([], ['--fresh-catalog']):
    import asyncio
    import inspect
    import httpx
    from runtime.daemon.app import create_app
    from runtime.daemon.routes.orgs import _seed_skeleton
    from runtime.daemon.state import DaemonState
    from runtime.runtime import RuntimeDir
    from runtime.infrastructure.workflow_schema import (
        initialize_complete_org_schema, initialize_fresh_org_database,
        _layout, _canonical_layout, _canonical_sequential_layout,
        sequential_storage_inventory, _schema_snapshot,
    )
    from runtime.workflows.sequential_builtin import SequentialBuiltinStore, PRESET_CATALOG
    from runtime.workflows.sequential_history import read_sequential_history, StorageSettings, _bytes
    from runtime.workflows.sequential_release import read_coding_release

    def a2_inventory(conn):
        with _schema_snapshot(conn):
            return sequential_storage_inventory(conn)

    def a2_definition_fixture(db, sql, args=()):
        # Adversarial private input only. Restore the ORIGINAL trigger SQL and
        # complete layout BEFORE the production registrar or history reader runs.
        # No disabled guard or test-only flag reaches the owner under test.
        before = _layout(db._conn)
        with db.workflow_schema_transaction() as conn:
            triggers = list(conn.execute("SELECT name,sql FROM sqlite_schema WHERE type='trigger' AND tbl_name='workflow_seq_definitions'"))
            for name, _ in triggers:
                conn.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
            conn.execute(sql, args)
            for _, ddl in triggers:
                conn.execute(ddl)
        assert _layout(db._conn) == before, 'A2 fixture failed exact trigger/layout restoration'

    if a2_case in (None, 'historical'):

        with tempfile.TemporaryDirectory(prefix='a2-historical-') as scratch:
            # Historical constructor defaults are observed independently of the
            # new owner and the literal combined target.
            for requested, expected in ((None, 'G'), ('G', 'G'), ('H', 'H'), ('I', 'I'), ('J', 'J')):
                db = Database(Path(scratch) / (expected + str(requested) + '.db'))
                try:
                    assert not _layout(db._conn)[0], 'A2 generic Database installed workflow storage'
                    kwargs = {} if requested is None else dict(layout=requested)
                    initialize_complete_org_schema(db, expected_org_slug='scratch', **kwargs)
                    assert validate_workflow_schema(db._conn, expected_org_slug='scratch') == expected, 'A2 historical default changed'
                    assert _layout(db._conn) == _canonical_layout(expected, coding_extension=expected == 'J', owner_indexed=True)
                    assert not read_sequential_history(db._conn, expected_org_slug='scratch').layout.sequential
                finally:
                    db.close()
            assert hashlib.sha256(inspect.getsource(initialize_complete_org_schema).encode()).hexdigest() == 'd0b462c6dd54bda7bef702a43fc0293cfbaadc3486f29e17c6fe9ef03406a582', 'A2 historical initializer bytes changed'
            print('PASS A2-H historical default G and explicit G/H/I/J; independent references; exact initializer bytes', flush=True)

    async def a2_route_registration():
        with tempfile.TemporaryDirectory(prefix='a2-fresh-catalog-') as scratch:
            home = Path(scratch).resolve()
            runtime = RuntimeDir.init(home / 'runtime')
            with patch.dict(os.environ, {'HAPPYRANCH_DAEMON_HOME': str(home)}):
                (home / 'daemon.token').write_text('synthetic-a2-founder')
                state = DaemonState(runtime=runtime, settings=Settings())
                from runtime.workflows.profile_coordinator import ProfileCoordinator
                state.profile_coordinator = ProfileCoordinator(daemon_home=home, orgs=state.orgs)
                app = create_app(state)
                try:
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://asgi') as client:
                        started = time.monotonic()
                        response = await client.post('/api/v1/orgs', headers={'Authorization': 'Bearer synthetic-a2-founder'}, json={'slug': 'scratch'})
                        assert response.status_code == 200, ('A2 real POST route', response.status_code, response.text)
                        org = state.orgs['scratch']
                        db, conn = org.db, org.db._conn
                        history = read_sequential_history(conn, expected_org_slug='scratch')
                        assert history.layout.document == 'J' and history.layout.sequential and history.layout.ownership == 2, 'A2 route must initialize fresh combined J+Coding+sequential+ownership2'
                        assert _layout(conn) == _canonical_sequential_layout('J', coding_extension=True, owner_indexed=True, ownership=2), 'A2 route differs from independent combined reference'
                        assert list(org.teams.teams()) == ['default']
                        manager = org.teams.manager_for_team('default')
                        assert manager.kind == 'human' and manager.principal == 'founder' and manager.workers == ()
                        assert list(OrgPaths(org.root).agents_dir.glob('*.md')) == []
                        assert {key: json.loads(value) for key, value in db.get_reviewer_agents_pair().items()} == {'reviewer_agents': [], 'reviewer_agents_policy': {'version': 2}}, 'A2 fresh reviewer policy differs'
                        from runtime.orchestrator.org_config import resolve_reviewer_configuration
                        assert resolve_reviewer_configuration(db, known_agents=set()).state == 'none'
                        for table in ('workflow_seq_settings', 'workflow_seq_settings_operations', 'workflow_seq_runs', 'workflow_seq_visits', 'tasks', 'workflow_coding_mappings'):
                            assert conn.execute('SELECT count(*) FROM ' + table).fetchone()[0] == 0, 'A2 fresh route allocated ' + table
                        compiled = read_coding_release()
                        expected_catalog = (('release/happyranch/coding', 2,
                            '5039e9eb60a03f8de110b40123a123d25962fbff48c39af5271a0666d679d065',
                            'a3b012d117c5905a1bac3c67a3158a8b1afa5a571acdba73c876ac7de24f790c'),)
                        assert PRESET_CATALOG == expected_catalog, 'A2 literal catalog grew or drifted'
                        assert [tuple(r) for r in conn.execute('SELECT identity,version,source_digest,definition_digest FROM workflow_seq_definitions')] == list(expected_catalog), 'A2 reviewed catalog registration missing or changed'
                        assert conn.execute('SELECT definition_bytes FROM workflow_seq_definitions').fetchone()[0] == compiled.raw
                        # Exclusive create refuses even a genuinely empty file;
                        # no existence/absence observation supplies ownership.
                        before = a2_inventory(conn)
                        try:
                            initialize_fresh_org_database(db.db_path, expected_org_slug='scratch')
                        except FileExistsError:
                            pass
                        else:
                            raise AssertionError('A2 fresh initializer reused an existing file')
                        assert a2_inventory(conn) == before
                        print('PASS A2-R real POST fresh combined literal reference; Default human/zero agents; none reviewer; Disabled/unset; exclusive create; seconds', round(time.monotonic()-started, 3), flush=True)

                        if a2_case in (None, 'registration'):
                            store = SequentialBuiltinStore(org)
                            # Existing exact definition takes no native reservation
                            # and makes no writes to any table, including audits.
                            before = a2_inventory(conn)
                            changes = conn.total_changes
                            store.ensure_registered()
                            assert conn.total_changes == changes and a2_inventory(conn) == before, 'A2 exact existing catalog must write zero'
                            print('PASS A2-C exact existing definition zero writes and complete typed-rowid/schema preservation', flush=True)
                            # Private synthetic retained settings/history are never
                            # launched. The real history reader authenticates them.
                            from runtime.workflows.sequential_definition import compile_definition
                            from scripts.setup_sequential_coding import _write
                            raw = json.loads(compiled.raw)
                            raw.update(identity='release/synthetic/retained', version=1)
                            retained = compile_definition(_bytes(raw), source_digest='0' * 64)
                            pin = retained.pin
                            conn.execute('INSERT INTO workflow_seq_definitions VALUES (?,?,?,?,?,?,?,?,?,?)',
                                ('retained-definition', pin.identity, pin.version, retained.raw, pin.definition_digest,
                                 pin.source_digest, pin.compiler, pin.validator, pin.interpreter, pin.capabilities))
                            saved = StorageSettings(format='workflow-sequential-settings-storage@1', definition=pin,
                                bindings=[dict(role=role, principal='synthetic-' + role) for role, _ in retained.definition.roles],
                                limits=retained.definition.limits, admission='Allowed')
                            _write(conn, 'scratch', 'admission', SimpleNamespace(operation_key='retained-settings', expected_generation=0, definition=pin),
                                dict(write=dict(definition_id='retained-definition', generation=1, raw=_bytes(saved.model_dump(mode='json')))))
                            conn.commit()
                            db.insert_task(TaskRecord(id='TASK-a2-retained', brief='Synthetic ordinary preserved task'))
                            read_sequential_history(conn, expected_org_slug='scratch')
                            a2_definition_fixture(db, "DELETE FROM workflow_seq_definitions WHERE identity='release/happyranch/coding'")
                            before = a2_inventory(conn)
                            changes, statements = conn.total_changes, []
                            with db.observe_trace(statements.append):
                                store.ensure_registered()
                            after = a2_inventory(conn)
                            assert conn.total_changes - changes == 1, 'A2 registrar wrote settings/roles/admission or other forbidden rows'
                            assert before[0] == after[0] and before[1].keys() == after[1].keys()
                            assert all(after[1][table] == rows for table, rows in before[1].items() if table != 'workflow_seq_definitions'), 'A2 registration changed existing settings/admission/history'
                            assert after[1]['workflow_seq_definitions'][0] == before[1]['workflow_seq_definitions'][0] + 1
                            assert not any('integrity_check' in sql.lower() or 'quick_check' in sql.lower() for sql in statements)
                            read_sequential_history(conn, expected_org_slug='scratch')
                            print('PASS A2-C absent definition inserts exactly one row; saved Allowed/settings/bindings and ordinary history byte/rowid exact; physical=0', flush=True)
                            a2_definition_fixture(db, "UPDATE workflow_seq_definitions SET source_digest=? WHERE identity='release/happyranch/coding'", ('1' * 64,))
                            before = a2_inventory(conn)
                            changes = conn.total_changes
                            try:
                                store.ensure_registered()
                            except ValueError as exc:
                                assert str(exc) == 'sequential_coding_definition_conflict', str(exc)
                            else:
                                raise AssertionError('A2 mismatched immutable definition accepted')
                            assert conn.total_changes == changes and a2_inventory(conn) == before
                            a2_definition_fixture(db, "UPDATE workflow_seq_definitions SET source_digest=? WHERE identity='release/happyranch/coding'", (compiled.pin.source_digest,))
                            print('PASS A2-C immutable mismatch refuses with zero writes', flush=True)
                            # A lost real cache capability must refuse before the
                            # absent definition is inserted, with no recovery here.
                            a2_definition_fixture(db, "DELETE FROM workflow_seq_definitions WHERE identity='release/happyranch/coding'")
                            cache = dict(org.workflow_authority._cache)
                            org.workflow_authority._cache.clear()
                            before, changes = a2_inventory(conn), conn.total_changes
                            try:
                                store.ensure_registered()
                            except RuntimeError as exc:
                                assert str(exc) == 'authority_pointer_not_ready'
                            else:
                                raise AssertionError('A2 registrar ignored authority qualification')
                            finally:
                                org.workflow_authority._cache.update(cache)
                            assert conn.total_changes == changes and a2_inventory(conn) == before
                            print('PASS A2-C actual authority capability loss refuses zero writes', flush=True)
                finally:
                    await state.close_all()
                    state.metrics_store.close()
                if a2_case in (None, 'registration'):
                    # The prior direct refusal left the real org missing the
                    # current definition. A genuine FIRST/NEXT startup owns
                    # registration; no fixture supplies readiness or a success.
                    startup = OrgState.load(slug='scratch', root=runtime.orgs_dir / 'scratch', settings=Settings())
                    try:
                        conn, db = startup.db._conn, startup.db
                        assert conn.execute("SELECT count(*) FROM workflow_seq_definitions WHERE identity='release/happyranch/coding'").fetchone()[0] == 1, 'A2 ordinary qualified load did not register missing catalog'
                        assert all(a2_inventory(conn)[1][table] == before[1][table]
                            for table in ('workflow_seq_settings', 'workflow_seq_settings_operations')), 'A2 ordinary load changed saved settings/admission'
                        read_sequential_history(conn, expected_org_slug='scratch')
                    finally:
                        startup.close()
                    startup = OrgState.load(slug='scratch', root=runtime.orgs_dir / 'scratch', settings=Settings())
                    try:
                        conn, db = startup.db._conn, startup.db
                        a2_definition_fixture(db, "DELETE FROM workflow_seq_definitions WHERE identity='release/happyranch/coding'")
                        try:
                            with native_writer(db, publisher='synthetic-a2-original-owner'):
                                raise RuntimeError('synthetic interrupted native writer')
                        except RuntimeError as exc:
                            assert str(exc) == 'synthetic interrupted native writer'
                        before, changes = a2_inventory(conn), conn.total_changes
                        try:
                            SequentialBuiltinStore(startup).ensure_registered()
                        except ValueError as exc:
                            assert str(exc) == 'native_writer_unsettled'
                        else:
                            raise AssertionError('A2 registrar ignored original native uncertainty')
                        assert conn.total_changes == changes and a2_inventory(conn) == before
                    finally:
                        startup.close()
                    print('PASS A2-C ordinary qualified FIRST/NEXT registration; saved state exact; genuine native UNKNOWN refuses zero writes', flush=True)
                # Real default startup on a previously created skeleton WITHOUT a
                # DB must retain the historical initializer, never the fresh owner.
                if a2_case in (None, 'route'):
                    root = runtime.orgs_dir / 'historical'
                    _seed_skeleton(root, from_example=None)
                    historical = OrgState.load(slug='historical', root=root, settings=Settings())
                    try:
                        assert not read_sequential_history(historical.db._conn, expected_org_slug='historical').layout.sequential, 'A2 absent DB was treated as positive freshness on startup'
                    finally:
                        historical.close()
                    print('PASS A2-R absent-DB startup remains historical; no fresh inference', flush=True)

    if a2_case in (None, 'registration'):
        from runtime.orchestrator.org_config import initialize_fresh_reviewer_agents
        from runtime.workflows._coding_registered_release import read_registered_release

        with tempfile.TemporaryDirectory(prefix='a2-retained-cutover-') as scratch:
            home = Path(scratch).resolve()
            root = home / 'runtime' / 'orgs' / 'scratch'
            with patch.dict(os.environ, {'HAPPYRANCH_DAEMON_HOME': str(home)}):
                # Real owners create J + Coding-absent + sequential + ownership2,
                # then retain a genuine enable request behind pre-enable work.
                _seed_skeleton(root, from_example=None)
                db = initialize_fresh_org_database(OrgPaths(root).db_path, expected_org_slug='scratch')
                try:
                    install_pause_schema(db, org_slug='scratch')
                    initialize_fresh_reviewer_agents(OrgPaths(root), db)
                    db.insert_task(TaskRecord(id='TASK-a2-cutover', brief='Synthetic pre-enable work'))
                    cutover = WorkflowCutoverStore(db, org_slug='scratch').request(
                        action='enable', operation_key='a2-retained-enable', expected_generation=1)
                    assert (cutover['state'], cutover['generation']) == ('enable_requested', 2)
                    assert {row['code'] for row in cutover['blockers']} == {'cutover_pre_enable_work'}
                    history = read_sequential_history(db._conn, expected_org_slug='scratch')
                    assert history.layout.document == 'J' and history.layout.coding and history.layout.sequential and history.layout.ownership == 2
                    with _schema_snapshot(db._conn):
                        assert read_registered_release(db._conn, expected_org_slug='scratch') is None
                finally:
                    db.close()

                original_registration = SequentialBuiltinStore.ensure_registered
                observations = []
                def a2_observe_registration(store):
                    # Observe only registrar effects, not unrelated startup
                    # publication. Always execute the real owner unchanged.
                    conn = store.db._conn
                    before, changes = a2_inventory(conn), conn.total_changes
                    try:
                        return original_registration(store)
                    finally:
                        assert conn.total_changes == changes and a2_inventory(conn) == before, 'A2 retained cutover registrar wrote on refusal'
                        assert not conn.in_transaction
                        observations.append(store.org.slug)

                with patch.object(SequentialBuiltinStore, 'ensure_registered', a2_observe_registration):
                    try:
                        startup = OrgState.load(slug='scratch', root=root, settings=Settings())
                    except ValueError as exc:
                        raise AssertionError('A2 retained enable_requested gen2 cold load refused: ' + str(exc)) from exc
                try:
                    conn, db = startup.db._conn, startup.db
                    assert observations == ['scratch'], 'A2 cold load omitted the real registrar'
                    assert conn.execute('SELECT count(*) FROM workflow_seq_definitions').fetchone()[0] == 0
                    with _schema_snapshot(conn):
                        assert read_registered_release(conn, expected_org_slug='scratch') is None
                    retained = WorkflowCutoverStore(db, org_slug='scratch').get()
                    assert (retained['state'], retained['generation'], retained['blockers']) == (cutover['state'], cutover['generation'], cutover['blockers'])
                    assert db.get_task('TASK-a2-cutover') is not None
                    read_sequential_history(conn, expected_org_slug='scratch')
                    print('PASS A2-C retained enable_requested gen2 cold load; Coding absent; real pre-enable blockers retained; registrar zero writes', flush=True)

                    # Genuine native UNKNOWN in this same pending state must
                    # still refuse, even though its rollback has no SQL effects.
                    try:
                        with native_writer(db, publisher='synthetic-a2-retained-owner'):
                            raise RuntimeError('synthetic retained native interruption')
                    except RuntimeError as exc:
                        assert str(exc) == 'synthetic retained native interruption'
                    try:
                        a2_observe_registration(SequentialBuiltinStore(startup))
                    except ValueError as exc:
                        assert str(exc) == 'native_writer_unsettled', str(exc)
                    else:
                        raise AssertionError('A2 retained cutover genuine native UNKNOWN was accepted as safe deferral')
                    assert observations == ['scratch', 'scratch']
                    print('PASS A2-C retained enable_requested gen2 genuine native UNKNOWN refuses; zero writes', flush=True)
                finally:
                    startup.close()

    if a2_case in (None, 'route', 'registration'):
        asyncio.run(a2_route_registration())
    if sys.argv[1:] == ['--fresh-catalog']:
        raise SystemExit(0)


# Real request/advance owners create C1-C5. Interruption occurs only AFTER the
# real transaction owner has committed; no history, blocker or closure is forged.
if sys.argv[1:] in ([], ['--i2-cutover']):
    from runtime.workflows.cutover import WorkflowCutoverError
    class CutoverInterrupted(BaseException):
        pass

    def cutover_rows(conn):
        return tuple(tuple(row) for row in conn.execute('SELECT * FROM workflow_cutover_state')), tuple(
            tuple(row) for row in conn.execute('SELECT * FROM workflow_cutover_events ORDER BY event_seq'))

    cutover_failures = []
    for label, target, generation in (('C1', 'enable_requested', 2), ('C2', 'compatibility_verified', 3),
            ('C3', 'disable_requested', 5), ('C4', 'draining', 6), ('C5', 'drained', 7)):
        with tempfile.TemporaryDirectory(prefix='i2-cutover-') as scratch, inline_sql_observation('I2-' + label, collected_failures=cutover_failures) as observed:
            path = Path(scratch) / 'org.db'
            db = Database(path)
            try:
                install_or_recover(db, expected_org_slug='scratch')
                with db.workflow_schema_transaction() as conn:
                    migrate_draft_schema(conn, expected_org_slug='scratch')
                store = WorkflowCutoverStore(db, org_slug='scratch')
                initial = store.get()
                assert initial['state'] == 'installed_legacy_only' and initial['generation'] == 1
                assert initial['allowed_actions'] == ['enable'] and not initial['blockers']
                if generation >= 5:
                    enabled = store.request(action='enable', operation_key='i2-enable', expected_generation=1)
                    assert enabled['state'] == 'enabled' and enabled['generation'] == 4
                    assert enabled['allowed_actions'] == ['disable'] and not enabled['blockers']
                action, key, expected = ('enable', 'i2-enable', 1) if generation < 4 else ('disable', 'i2-disable', 4)
                real_transaction = store._transaction
                @contextmanager
                def interrupt_after_commit(*, write):
                    with real_transaction(write=write) as conn:
                        yield conn
                    if write and conn.execute('SELECT state FROM workflow_cutover_state').fetchone()[0] == target:
                        assert not conn.in_transaction, 'cutover interruption preceded real COMMIT'
                        raise CutoverInterrupted()
                if target == 'drained':
                    store.request(action=action, operation_key=key, expected_generation=expected)
                else:
                    try:
                        with patch.object(store, '_transaction', interrupt_after_commit):
                            store.request(action=action, operation_key=key, expected_generation=expected)
                    except CutoverInterrupted:
                        pass
                    else:
                        raise AssertionError(label + ' did not interrupt after requested committed state')
                saved = cutover_rows(db._conn)
                # Independent native connection proves the pending state is durable.
                with closing(sqlite3.connect(path)) as reader:
                    assert cutover_rows(reader) == saved
                public = store.get()
                assert public['state'] == target and public['generation'] == generation, (label, public)
                assert not public['blockers'] and cutover_rows(db._conn) == saved
                for bad, code in ((dict(action=action, operation_key='new-key', expected_generation=generation+1), 'cutover_generation_stale'),
                        (dict(action='disable' if action == 'enable' else 'enable', operation_key=key, expected_generation=expected), 'cutover_operation_conflict')):
                    try:
                        store.request(**bad)
                    except WorkflowCutoverError as exc:
                        assert exc.code == code, (label, exc.code, code)
                    else:
                        raise AssertionError(label + ' accepted stale/conflicting request')
                    assert cutover_rows(db._conn) == saved and not db._conn.in_transaction
                before_fk = len(observed.statements)
                assert store._integrity_blockers(db._conn) == []
                assert sum(c is db._conn and sql == 'pragma foreign_key_check'
                    for c,sql in observed.statements[before_fk:]) == 1, label + ' cutover whole FK omitted'
                downgrade = store.downgrade_preflight()
                assert not downgrade['eligible'] and any(b['code'] == 'cutover_history_prevents_downgrade'
                    and b['owner'] == 'workflow_cutover_reconciler' for b in downgrade['blockers'])
                # Genuine cold reopen exercises recovery independently of request replay.
                copy = Path(scratch) / 'cold.db'
                with closing(sqlite3.connect(copy)) as dest:
                    db._conn.backup(dest)
                cold = Database(copy)
                try:
                    recovered = WorkflowCutoverStore(cold, org_slug='scratch').recover_authorized()
                    assert recovered['state'] == ('enabled' if generation < 4 else 'drained')
                    assert recovered['events'][:generation] == public['events']
                finally:
                    cold.close()
                replay = store.request(action=action, operation_key=key, expected_generation=expected)
                assert replay['replayed'] and replay['request_generation'] == expected+1
                assert replay['request_event_id'] == 'cutover-event-' + str(expected+1)
                assert replay['state'] == ('enabled' if generation < 4 else 'drained') and not replay['blockers']
                assert replay['events'][:generation] == public['events']
                stable = cutover_rows(db._conn)
                assert store.request(action=action, operation_key=key, expected_generation=expected)['replayed']
                assert cutover_rows(db._conn) == stable and not db._conn.in_transaction
                print('PASS I2-' + label, target, 'real commits/public-get/cold-recovery/replay/CAS/conflict/downgrade; original events retained', flush=True)
            finally:
                db.close()
    assert not cutover_failures, 'I2 cutover physical failures: ' + repr(cutover_failures)
    if sys.argv[1:] == ['--i2-cutover']:
        raise SystemExit(0)


# N1-N3 invoke the actual native constructor and retrofit, with exact shipped
# old/T2 literals. Synthetic parent/child payloads test storage preservation only;
# they confer no admission, candidate authority or continuation execution.
if sys.argv[1:] in ([], ['--i2-native']):
    from datetime import datetime, timezone
    from runtime.infrastructure.db import schema as native_schema
    from runtime.models import (AuthorityPolicyV2Attempt, authority_policy_v2_attempt_id,
        authority_policy_v2_contract_digest)

    def native_snapshot(conn):
        return {table: native_schema._v2_attempt_retained_rows(conn, table) for table in
            (native_schema._V2_ATTEMPTS_TABLE, *native_schema._V2_ATTEMPTS_CHILDREN)}

    def old_native_fixture(path):
        db = Database(path)
        db.close()
        with closing(sqlite3.connect(path)) as conn:
            conn.execute('PRAGMA foreign_keys=OFF')
            conn.execute('DROP TABLE authority_policy_v2_attempts')
            conn.executescript(native_schema._AUTHORITY_POLICY_V2_ATTEMPTS_OLD_SQL)
            for n in (17, 91):
                at = datetime(2026, 1, 1, tzinfo=timezone.utc)
                conn.execute('INSERT INTO task_results(id,task_id,agent,session_id,created_at) VALUES(?,?,?,?,?)',
                    (n, 'TASK-native', 'manager', 'native-' + str(n), at.isoformat()))
                attempt = AuthorityPolicyV2Attempt(
                    attempt_id=authority_policy_v2_attempt_id(team='engineering', root_task_id='TASK-native',
                        manager_agent='manager', manager_session_id='native-' + str(n), result_id=n),
                    team='engineering', root_task_id='TASK-native', manager_agent='manager',
                    manager_session_id='native-' + str(n), result_id=n,
                    binding_id='APV2B-' + '1'*64, contract_id='authority_policy_v2', contract_version='v2',
                    contract_digest=authority_policy_v2_contract_digest(), release_id='APV2-' + '2'*64,
                    activation_id='APV2A-' + '3'*64, activation_epoch=1, selector_id='APS-' + '4'*64,
                    stage='admitted', finalization_state='unfinalized', origin_boot_id='native-boot',
                    owner_attempt_id='native-owner', assessment_digest='5'*64, created_at=at)
                row = attempt.model_dump(mode='json')
                row['canonical_payload_json'] = attempt.model_dump_json()
                conn.execute('INSERT INTO authority_policy_v2_attempts(rowid,' + ','.join(row) + ') VALUES ('
                    + ','.join('?' for _ in range(len(row)+1)) + ')', (n, *row.values()))
            # Nonempty children and gapped parent rowids survive the parent rebuild.
            for table, rowid in (('authority_policy_v2_candidates', 33), ('authority_policy_v2_continue_envelopes', 67)):
                child = {name: row.get(name, 1 if kind == 'INTEGER' else 'synthetic-native')
                    for _, name, kind, required, default, pk in conn.execute('PRAGMA table_info(' + table + ')')
                    if required or pk}
                child.update(candidate_id='native-candidate', claim_key='native-claim')
                if table.endswith('candidates'):
                    child.update(lifecycle_stage='created')
                else:
                    child.update(envelope_id='native-envelope', evaluation_outcome='continue_applies', lifecycle_state='active')
                child['canonical_payload_json'] = '{}'
                conn.execute('INSERT INTO ' + table + '(rowid,' + ','.join(child) + ') VALUES ('
                    + ','.join('?' for _ in range(len(child)+1)) + ')', (rowid, *child.values()))
            conn.commit()
            conn.execute('PRAGMA foreign_keys=ON')
            assert native_schema._v2_attempt_schema_preflight(conn) == 'old'
            assert conn.execute('PRAGMA foreign_key_check').fetchone() is None
            return native_snapshot(conn), native_schema._v2_attempt_other_schema(conn)

    native_physical_failures = []
    @contextmanager
    def native_gate_receipts(case, *, deny_commit=False, deny_rollback=False):
        calls = []
        real_gate = native_schema._v2_attempt_integrity
        def gate(conn):
            start = len(observed.statements)
            result = real_gate(conn)
            statements = observed.statements[start:]
            assert sum(c is conn and sql == 'pragma foreign_key_check' for c, sql in statements) == 1, case + ' native whole FK omitted'
            calls.append(conn.in_transaction)
            if deny_commit and len(calls) == 2:
                conn.set_authorizer(lambda action, arg1, *_: sqlite3.SQLITE_DENY
                    if action == sqlite3.SQLITE_TRANSACTION and arg1 in
                        (('COMMIT', 'ROLLBACK') if deny_rollback else ('COMMIT',)) else sqlite3.SQLITE_OK)
            return result
        with inline_sql_observation(case, collected_failures=native_physical_failures) as observed, patch.object(native_schema, '_v2_attempt_integrity', gate):
            yield calls, observed

    with tempfile.TemporaryDirectory(prefix='i2-native-') as scratch:
        path = Path(scratch) / 'fresh.db'
        with native_gate_receipts('I2-N1') as (calls, observed):
            db = Database(path)
            assert native_schema._v2_attempt_schema_preflight(db._conn) == 't2'
            assert calls == [False] and not db._conn.in_transaction
            assert db._conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1
            db.close()
        print('PASS I2-N1 absent native -> fresh T2; full FK; native transaction custody', flush=True)

        path = Path(scratch) / 'old.db'
        saved, other = old_native_fixture(path)
        with native_gate_receipts('I2-N2') as (calls, observed):
            db = Database(path)
            try:
                assert calls == [True, True, False], ('I2-N2 source/target/postcommit', calls)
                assert native_snapshot(db._conn) == saved, 'I2-N2 changed typed parent/children/rowids'
                assert native_schema._v2_attempt_other_schema(db._conn) == other, 'I2-N2 changed unrelated schema'
                assert native_schema._v2_attempt_schema_preflight(db._conn) == 't2'
                assert not db._conn.in_transaction and db._conn.execute('PRAGMA legacy_alter_table').fetchone()[0] == 0
                assert db._conn.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
            finally:
                db.close()
        print('PASS I2-N2 exact old -> source/target/postcommit FK; literals/typed parents/nonempty children/gapped rowids/schema/journaling retained', flush=True)
        with native_gate_receipts('I2-N3') as (calls, observed):
            db = Database(path)
            assert not calls and native_snapshot(db._conn) == saved
            assert native_schema._v2_attempt_schema_preflight(db._conn) == 't2' and not db._conn.in_transaction
            db.close()
        print('PASS I2-N3 T2 reopen: no native integrity traversal/DDL/row rewrite', flush=True)

        for uncertain in (False, True):
            path = Path(scratch) / ('uncertain.db' if uncertain else 'rollback.db')
            saved, other = old_native_fixture(path)
            with native_gate_receipts('I2-N2-' + ('uncertain' if uncertain else 'rollback'),
                    deny_commit=True, deny_rollback=uncertain) as (calls, observed):
                try:
                    Database(path)
                except BaseExceptionGroup as exc:
                    assert 'COMMIT attempted; durable outcome uncertain' in str([getattr(e, '__notes__', []) for e in exc.exceptions])
                    if uncertain:
                        assert len(exc.exceptions) > 1, 'I2-N2 lost rollback/cleanup uncertainty'
                else:
                    raise AssertionError('I2-N2 COMMIT denial accepted')
                assert calls == [True, True]
                with closing(sqlite3.connect(path)) as conn:
                    conn.execute('PRAGMA foreign_keys=ON')
                    assert native_schema._v2_attempt_schema_preflight(conn) == 'old'
                    assert native_snapshot(conn) == saved and native_schema._v2_attempt_other_schema(conn) == other
                print('PASS I2-N2 COMMIT denied:', 'rollback uncertainty retained; constructor closes/discards' if uncertain else
                    'real rollback restores old parent/children/schema/rowids; no fresh fallback', flush=True)
    assert not native_physical_failures, 'I2 native physical failures: ' + repr(native_physical_failures)
    if sys.argv[1:] == ['--i2-native']:
        raise SystemExit(0)


# A3 mounted ASGI routes, private native storage, no lifespan/listener/runner.
if sys.argv[1:] in ([], ['--setup-status']):
    import asyncio
    import httpx
    from runtime.daemon.app import create_app
    from runtime.daemon.state import DaemonState
    from runtime.daemon.routes.orgs import _seed_skeleton
    from runtime.daemon import org_integrity_monitor as monitor
    from runtime.runtime import RuntimeDir
    from runtime.infrastructure.workflow_schema import (
        initialize_complete_org_schema, install_sequential_schema,
        sequential_storage_inventory, _coding_org_identity, _schema_snapshot,
    )

    async def setup_route_cases():
        with tempfile.TemporaryDirectory(prefix='workflow-setup-routes-') as scratch:
            home = Path(scratch)
            runtime = RuntimeDir.init(home / 'runtime')
            root = runtime.orgs_dir / 'scratch'
            _seed_skeleton(root, from_example=None)
            (root / 'org/config.yaml').write_text('reviewer_agents: []\n')
            # Explicit supported private foundation, unchanged normal loader.
            db = Database(OrgPaths(root).db_path)
            initialize_complete_org_schema(db, expected_org_slug='scratch', layout='J')
            from runtime.orchestrator.org_config import initialize_fresh_reviewer_agents
            initialize_fresh_reviewer_agents(OrgPaths(root), db)
            db.close()
            with patch.dict(os.environ, {'HAPPYRANCH_DAEMON_HOME': str(home)}):
                # Synthetic token file exercises the existing auth dependency.
                (home / 'daemon.token').write_text('synthetic-a3-founder')
                org = OrgState.load(slug='scratch', root=root, settings=Settings())
                state = DaemonState(runtime=runtime, settings=Settings(), orgs={'scratch': org},
                    broken_orgs={'broken': 'PRIVATE DB DIAGNOSTIC'})
                from runtime.workflows.profile_coordinator import ProfileCoordinator
                state.profile_coordinator = ProfileCoordinator(daemon_home=home, orgs=state.orgs)
                org._profile_coordinator = state.profile_coordinator
                org.workflow_authority._profile_coordinator = state.profile_coordinator
                state.profile_coordinator.reconcile_startup()
                broken = runtime.orgs_dir / 'broken'
                _seed_skeleton(broken, from_example=None)
                (broken / 'happyranch.db').write_bytes(b'not a database')
                unattached = runtime.orgs_dir / 'unattached'
                _seed_skeleton(unattached, from_example=None)
                app = create_app(state)
                auth = {'Authorization': 'Bearer synthetic-a3-founder'}
                path = '/api/v1/runtime/workflow-setup/orgs/'
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://asgi') as client:
                    for headers in ({}, {'Authorization': 'Bearer synthetic-wrong'}, {'X-HappyRanch-Surface': 'cli'}):
                        response = await client.get(path + 'broken', headers=headers)
                        assert response.status_code == 401, ('A3 wrong auth', response.status_code)
                    print('PASS A3 auth: missing/wrong/task-surface bearer refused', flush=True)
                    response = await client.get(path + 'missing', headers=auth)
                    assert response.status_code == 404 and response.json() == {'detail': {'code': 'unknown_org'}}, ('A3 unknown slug closed 404', response.text)
                    print('PASS A3 unknown slug: closed 404 no inventory', flush=True)
                    for suffix in ('?extra=1', '?extra=1&extra=2'):
                        assert (await client.get(path + 'broken' + suffix, headers=auth)).status_code == 422
                    assert (await client.request('GET', path + 'broken', headers=auth, content=b'x')).status_code == 422
                    for slug in ('broken', 'unattached'):
                        before = (broken / 'happyranch.db').read_bytes()
                        response = await client.get(path + slug, headers=auth)
                        assert response.status_code == 200 and response.headers['cache-control'] == 'no-store', ('A3 known unattached must be visible', response.text)
                        assert response.json()['state'] == 'unknown' and 'PRIVATE' not in response.text
                        assert (broken / 'happyranch.db').read_bytes() == before
                    print('PASS A3 known broken DB and unattached: independent no-store status', flush=True)
                    key = monitor.runtime_key(runtime.root)
                    namespace = home / 'backups/workflow-sequential' / key / 'broken'
                    namespace.mkdir(parents=True)
                    (namespace / 'malformed').write_text('PRIVATE MALFORMED EVIDENCE')
                    response = await client.get(path + 'broken', headers=auth)
                    assert response.json()['state'] == 'unknown' and response.json()['reason'] == 'evidence_unavailable'
                    assert 'PRIVATE' not in response.text
                    print('PASS A3 malformed attempt evidence: unknown sanitized status', flush=True)
                    report_dir = home / 'workflow-integrity' / key / 'broken'
                    report_dir.mkdir(parents=True, mode=0o700)
                    latest = report_dir / 'latest.json'
                    date = '2001-01-01'
                    report = dict(format=monitor.FORMAT, runtime_key=key, org='broken', scheduled_date=date,
                        finished_at='2001-01-01T01:00:00+00:00',
                        report_id=hashlib.sha256(monitor._json([key, 'broken', date])).hexdigest(),
                        **monitor._observation('full_integrity_ok', 'integrity'), reporting=dict(external='persisted', audit='unavailable'),
                        private_diagnostic='PRIVATE RAW INTEGRITY TEXT')
                    for reason, outcome in (('full_integrity_ok', 'ok'), ('full_integrity_not_ok', 'failed'), ('busy', 'unavailable')):
                        report.update(monitor._observation(reason, 'integrity'))
                        latest.write_bytes(monitor._json(report)); latest.chmod(0o600)
                        value = (await client.get(path + 'broken', headers=auth)).json()['integrity_report']
                        assert value == dict(outcome=outcome, reason=reason, checked_at=report['finished_at'],
                            scheduled_date=date, reporting=report['reporting']), ('A3 daily projection', value)
                        print('PASS A3 daily report:', reason, 'original stale date, closed projection', flush=True)
                    latest.write_text('{"outcome":"ok"}'); latest.chmod(0o600)
                    value = (await client.get(path + 'broken', headers=auth)).json()['integrity_report']
                    assert value['outcome'] == 'unavailable' and value['reason'] == 'report_unavailable' and value['scheduled_date'] is None
                    latest.unlink(); latest.symlink_to(broken / 'happyranch.db')
                    assert (await client.get(path + 'broken', headers=auth)).json()['integrity_report']['outcome'] == 'unavailable'
                    print('PASS A3 malformed/unsafe daily: unavailable; bad attempt does not hide daily', flush=True)
                    org.db.insert_task(TaskRecord(id='TASK-A3', brief='Synthetic ordinary task'))
                    task_path = '/api/v1/orgs/scratch/tasks/TASK-A3/sequential-workflow'
                    value = await client.get(task_path, headers=auth)
                    assert value.status_code == 200 and value.json()['run'] is None, ('A3 validated absent layout must be empty', value.status_code, value.text)
                    print('PASS A3 ordinary absent layout: real task/authority/full history then null', flush=True)
                    # Supported installer, no admission/settings/runs, full history unchanged.
                    with _schema_snapshot(org.db._conn):
                        identity = _coding_org_identity(org.db._conn, expected_org_slug='scratch', expected_path=str(org.db.db_path))
                        inventory = sequential_storage_inventory(org.db._conn)
                    def offline():
                        assert _coding_org_identity(org.db._conn, expected_org_slug='scratch', expected_path=str(org.db.db_path)) == identity
                    assert install_sequential_schema(org.db._conn, expected_org_slug='scratch', expected_path=str(org.db.db_path),
                        source_identity=identity, source_inventory=inventory, offline_check=offline) == 'installed'
                    value = await client.get(task_path, headers=auth)
                    assert value.status_code == 200 and value.json()['run'] is None, value.text
                    print('PASS A3 ordinary installed: no workflow banner', flush=True)
                    # Complete private append-only evidence, independent of the DB.
                    attempt = home / 'backups/workflow-sequential' / key / 'scratch' / 'synthetic-attempt'
                    attempt.mkdir(parents=True, mode=0o700)
                    backup = attempt / 'before.db'
                    backup.write_bytes(b'Synthetic backup path observation only')
                    backup.chmod(0o600)
                    prior = None
                    states = [('intent', 'unknown', 'install_incomplete'),
                        ('backup_verified', 'unknown', 'install_incomplete'),
                        ('pre_commit', 'unknown', 'commit_uncertain'),
                        ('committed', 'installed_unverified', 'installation_unverified'),
                        ('verified', 'installed_unverified', 'installation_unverified'),
                        ('registered', 'installed_verified', 'registered')]
                    for seq, (phase, status, reason) in enumerate(states):
                        event = dict(format='workflow-setup-event@1', runtime_key=key, org_slug='scratch',
                            attempt_id=attempt.name, sequence=seq, previous_digest=prior, phase=phase,
                            state=status, reason=reason, attempt_time='2001-01-01T00:00:00+00:00', backup_path=str(backup),
                            db_identity=dict(path=str(org.db.db_path), device=org.db.db_path.stat().st_dev, inode=org.db.db_path.stat().st_ino),
                            source_inventory_digest='a'*64, backup_sha256=hashlib.sha256(backup.read_bytes()).hexdigest(), target_version=1)
                        raw = json.dumps(event, sort_keys=True, separators=(',', ':')).encode()
                        record = attempt / f'{seq:02}.json'
                        record.write_bytes(raw); record.chmod(0o600)
                        prior = hashlib.sha256(raw).hexdigest()
                    status = await client.get(path + 'scratch', headers=auth)
                    assert status.json()['state'] == 'installed_verified', status.text
                    print('PASS A3 complete setup chain: independent installed evidence', flush=True)
                    original_records = {item.name: item.read_bytes() for item in attempt.glob('*.json')}
                    for length in (1, 2, 3):
                        for item in attempt.glob('*.json'):
                            item.unlink()
                        for filename, body in original_records.items():
                            if int(filename[:2]) < length:
                                target = attempt / filename
                                target.write_bytes(body); target.chmod(0o600)
                        prefix_status = (await client.get(path + 'scratch', headers=auth)).json()
                        assert prefix_status['state'] == 'unknown', prefix_status
                        terminal = dict(json.loads(original_records['00.json']), sequence=length,
                            previous_digest=hashlib.sha256(original_records[f'{length-1:02}.json']).hexdigest(),
                            phase='not_installed', state='not_installed', reason='not_installed')
                        target = attempt / f'{length:02}.json'
                        target.write_text(json.dumps(terminal)); target.chmod(0o600)
                        assert (await client.get(path + 'scratch', headers=auth)).json()['state'] == 'not_installed'
                        print('PASS A3 recorded pre-COMMIT refusal:', states[length-1][0], 'positive terminal only', flush=True)
                    for item in attempt.glob('*.json'):
                        item.unlink()
                    for filename, body in original_records.items():
                        target = attempt / filename
                        target.write_bytes(body); target.chmod(0o600)
                    saved_event = record.read_bytes()
                    parsed_event = json.loads(saved_event)
                    for case, damaged in (
                        ('malformed JSON', b'{'),
                        ('digest mismatch', json.dumps(dict(parsed_event, previous_digest='b'*64)).encode()),
                        ('path escape', json.dumps(dict(parsed_event, backup_path=str(home / 'outside'))).encode()),
                        ('raw reason', json.dumps(dict(parsed_event, reason='PRIVATE RAW ERROR')).encode()),
                        ('record cap', b'x'*65537)):
                        record.write_bytes(damaged)
                        value = (await client.get(path + 'scratch', headers=auth)).json()
                        assert value['state'] == 'unknown' and value['backup_path'] is None and 'PRIVATE' not in json.dumps(value), (case, value)
                        record.write_bytes(saved_event)
                        assert record.read_bytes() == saved_event
                        print('PASS A3 attempt refusal:', case, 'unknown; exact fixture restore', flush=True)
                    assert (await client.get(path + 'scratch', headers=auth)).json()['state'] == 'installed_verified'
                    # Native corrupt history, not a mocked reader or setup permission.
                    org.db._conn.execute('DELETE FROM workflow_task_ownership WHERE task_id=?', ('TASK-A3',))
                    org.db._conn.commit()
                    value = await client.get(task_path, headers=auth)
                    assert value.status_code == 409, ('A3 corrupt history must remain error', value.status_code, value.text)
                    assert (await client.get(path + 'scratch', headers=auth)).json()['state'] == 'installed_verified'
                    assert (await client.get('/api/v1/orgs/scratch/tasks/TASK-missing/sequential-workflow', headers=auth)).status_code == 404
                    print('PASS A3 corrupt history: real 409; absent native task 404', flush=True)
                    # Inject a negative existing role-authority refusal, never a successful grant.
                    from runtime.workflows import sequential_views
                    from runtime.workflows.sequential_engine import SequentialRefusal
                    with patch.object(sequential_views, '_current_authority', side_effect=SequentialRefusal('role_ineligible')):
                        assert (await client.get(task_path, headers=auth)).status_code == 403
                    print('PASS A3 injected role authority denial: 403 preserved', flush=True)
                org.close()
                state.metrics_store.close()
                state.direct_connect_authority_store.close()
    asyncio.run(setup_route_cases())
    if sys.argv[1:]:
        raise SystemExit(0)

# I1b uses the same private venue; it never starts a daemon or listener.
if sys.argv[1:] in ([], ['--daily-integrity']):
    import asyncio
    import signal
    import subprocess
    from datetime import datetime, timedelta, timezone
    from runtime.daemon import org_integrity_monitor as monitor
    from runtime.daemon import disk_sweeper as sweeper
    from runtime.runtime import RuntimeDir

    def daily_case(name):
        print('daily-integrity: ' + name, flush=True)

    def corrupt_index(path):
        # Keep an authentic stale native index; no successful checker stub.
        conn = sqlite3.connect(path)
        conn.execute('CREATE TABLE probe(id INTEGER PRIMARY KEY,value TEXT)')
        conn.execute('CREATE INDEX private_corrupt_index ON probe(value)')
        conn.execute("INSERT INTO probe VALUES(1,'original')")
        entry = tuple(conn.execute("SELECT rowid,type,name,tbl_name,rootpage,sql FROM sqlite_schema WHERE name='private_corrupt_index'").fetchone())
        cookie = conn.execute('PRAGMA schema_version').fetchone()[0]
        conn.execute('PRAGMA writable_schema=ON')
        conn.execute('DELETE FROM sqlite_schema WHERE rowid=?', (entry[0],))
        conn.execute('PRAGMA schema_version=' + str(cookie + 1))
        conn.execute('PRAGMA writable_schema=RESET')
        conn.execute("UPDATE probe SET value='changed' WHERE id=1")
        conn.execute('PRAGMA writable_schema=ON')
        conn.execute('INSERT INTO sqlite_schema(rowid,type,name,tbl_name,rootpage,sql) VALUES(?,?,?,?,?,?)', entry)
        conn.execute('PRAGMA schema_version=' + str(cookie + 2))
        conn.execute('PRAGMA writable_schema=RESET')
        conn.commit()
        conn.close()

    with tempfile.TemporaryDirectory(prefix='workflow-daily-integrity-') as scratch:
        private = Path(scratch)
        home = private / 'home'
        home.mkdir()
        rt = private / 'runtime'
        (rt / 'orgs').mkdir(parents=True)
        for slug in ('alpha', 'beta', 'corrupt', 'unattached', '_pending', '_archive', 'uninitialized'):
            root = rt / 'orgs' / slug
            (root / 'org').mkdir(parents=True)
            if slug != 'uninitialized':
                (root / 'org/teams.yaml').write_text('teams: {}\n')
            if slug in ('alpha', 'beta', 'corrupt'):
                db = Database(root / 'happyranch.db')
                db.close()
        alpha = rt / 'orgs/alpha'
        alpha_db = alpha / 'happyranch.db'
        corrupt_index(rt / 'orgs/corrupt/happyranch.db')
        state = SimpleNamespace(runtime=RuntimeDir(rt), broken_orgs={'unattached': 'not loaded'},
                                orgs_snapshot=lambda: {})
        due = datetime(2031, 2, 3, 3).astimezone()

        daily_case('actual RO + query_only + zero changes + full native success')
        original_connect = sqlite3.connect
        opened, connections, traces, value_limits = [], [], [], []
        class ReadObserver(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                value = super().execute(sql, *args, **kwargs)
                if sql == 'PRAGMA query_only=ON':
                    assert super().execute('PRAGMA query_only').fetchone() == (1,)
                    try:
                        super().execute('CREATE TABLE forbidden_query_only(x)')
                    except sqlite3.OperationalError as exc:
                        assert exc.sqlite_errorcode & 255 == sqlite3.SQLITE_READONLY
                    else:
                        raise AssertionError('query_only allowed a write')
                return value
            def close(self):
                assert self.total_changes == 0
                value_limits.append(self.getlimit(sqlite3.SQLITE_LIMIT_LENGTH))
                super().close()
        def observe_connect(filename, **kwargs):
            opened.append(filename)
            # A real attempted write distinguishes RO from a RW URI even
            # before query_only, without trusting the production pragma.
            conn = original_connect(filename, factory=ReadObserver, **kwargs)
            connections.append(conn)
            try:
                sqlite3.Connection.execute(conn, 'CREATE TABLE forbidden_rw_open(x)')
            except sqlite3.OperationalError as exc:
                assert exc.sqlite_errorcode & 255 == sqlite3.SQLITE_READONLY
            else:
                conn.close()
                raise AssertionError('RW-open regression allowed an actual write')
            conn.set_trace_callback(traces.append)
            return conn
        before = hashlib.sha256(alpha_db.read_bytes()).hexdigest()
        with patch.object(monitor.sqlite3, 'connect', observe_connect):
            observed = monitor.read_snapshot(alpha, time.monotonic() + 5)
        assert observed['outcome'] == 'ok', observed
        assert len(opened) == 1 and opened[0].endswith('?mode=ro') and 'immutable' not in opened[0], opened
        assert traces.count('PRAGMA integrity_check') == 1 and not any('quick_check' in sql for sql in traces)
        assert before == hashlib.sha256(alpha_db.read_bytes()).hexdigest()
        assert value_limits == [1024 * 1024], value_limits
        ledger_type = monitor.IntegrityLedger
        with patch.object(monitor, 'IntegrityLedger', lambda deadline: ledger_type(deadline, sql_limit=1000)):
            limited = monitor.read_snapshot(alpha, time.monotonic()+5)
        assert limited['reason'] == 'sql_budget' and limited['outcome'] == 'unavailable', limited
        daily_case('one pinned WAL snapshot while a private writer commits')
        pinned_root = rt / 'orgs/pinned'
        pinned_root.mkdir()
        db = Database(pinned_root / 'happyranch.db')
        db.close()
        with original_connect(pinned_root / 'happyranch.db') as writer:
            writer.execute('CREATE TABLE snapshot_probe(value)')
            writer.execute('INSERT INTO snapshot_probe VALUES(1)')
        writer.close()
        native_check = monitor.check_full_integrity
        def concurrent_check(conn, ledger):
            assert conn.in_transaction
            assert conn.execute('SELECT count(*) FROM snapshot_probe').fetchone() == (1,)
            with original_connect(pinned_root / 'happyranch.db') as writer:
                writer.execute('INSERT INTO snapshot_probe VALUES(2)')
            writer.close()
            result = native_check(conn, ledger)
            assert conn.execute('SELECT count(*) FROM snapshot_probe').fetchone() == (1,), 'read snapshot drifted'
            return result
        with patch.object(monitor, 'check_full_integrity', concurrent_check):
            pinned = monitor.read_snapshot(pinned_root, time.monotonic()+5)
            assert pinned['outcome'] == 'ok', ('daily pinned WAL snapshot', pinned)
        with original_connect(pinned_root / 'happyranch.db') as reader:
            assert reader.execute('SELECT count(*) FROM snapshot_probe').fetchone() == (2,)
        reader.close()

        daily_case('genuine non-ok index + corrupt bytes + missing + busy + identity + deadline')
        bad = monitor.read_snapshot(rt / 'orgs/corrupt', time.monotonic() + 5)
        assert bad['outcome'] == 'failed' and bad['reason'] in ('database_corrupt', 'full_integrity_not_ok'), bad
        missing = monitor.read_snapshot(rt / 'orgs/unattached', time.monotonic() + 5)
        assert missing['outcome'] == 'unavailable' and not (rt / 'orgs/unattached/happyranch.db').exists(), missing
        lock = original_connect(alpha_db)
        # WAL readers correctly coexist with an exclusive writer; a private
        # rollback-journal fixture exercises actual reader admission contention.
        lock.execute('PRAGMA journal_mode=DELETE')
        lock.execute('BEGIN EXCLUSIVE')
        started = time.monotonic()
        busy = monitor.read_snapshot(alpha, started + 5)
        assert busy['reason'] == 'busy' and time.monotonic() - started < .8, busy
        lock.rollback()
        lock.close()
        assert monitor.read_snapshot(alpha, time.monotonic() - 1)['reason'] == 'deadline'
        alias = rt / 'orgs/alias'
        alias.symlink_to(alpha, target_is_directory=True)
        assert monitor.read_snapshot(alias, time.monotonic() + 5)['outcome'] == 'unavailable'
        alias.unlink()
        corrupt_bytes = rt / 'orgs/notadb'
        corrupt_bytes.mkdir()
        (corrupt_bytes / 'happyranch.db').write_bytes(b'private synthetic not a database' * 30)
        invalid = monitor.read_snapshot(corrupt_bytes, time.monotonic() + 5)
        assert invalid['reason'] == 'database_corrupt', ('daily genuine corrupt bytes', invalid)
        assert 'private_corrupt_index' not in json.dumps(bad)

        async def daily_integration():
            daily_case('source and frozen helper dispatch: no daemon/listener')
            owner = monitor.OrgIntegrityMonitor(state)
            record = await owner.invoke('read', {'root': str(alpha)}, time.monotonic() + 5)
            assert record is not None and record['outcome'] == 'ok', record
            source_argv = monitor.helper_argv('read')
            assert source_argv == [sys.executable, '-m', 'runtime.daemon', '--workflow-integrity-helper', 'read']
            with patch.object(sys, 'frozen', True, create=True):
                assert monitor.helper_argv('read') == [sys.executable, '--workflow-integrity-helper', 'read']
            guard = """import sys,runpy,importlib.abc
class RefuseStartup(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname in ('uvicorn','runtime.daemon.app','runtime.daemon.state','runtime.infrastructure.database'):
   raise AssertionError('helper reached daemon startup: '+fullname)
sys.meta_path.insert(0, RefuseStartup())
sys.addaudithook(lambda event,args: (_ for _ in ()).throw(AssertionError('listener')) if event=='socket.bind' else None)
sys.frozen=True
sys.argv=['frozen-daemon','--workflow-integrity-helper','read']
runpy.run_module('runtime.daemon',run_name='__main__')
"""
            child = await asyncio.create_subprocess_exec(sys.executable, '-c', guard,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, err = await child.communicate(json.dumps({'root': str(alpha), 'deadline': time.monotonic()+5}).encode())
            assert child.returncode == 0 and json.loads(out)['outcome'] == 'ok', (out, err)
            assert not list(home.glob('daemon.*'))

            daily_case('two dates/two orgs + unattached + durable report/audit + repeat date')
            await owner.run_pass(due)
            base = home / 'workflow-integrity' / monitor.runtime_key(rt)
            assert {p.name for p in base.iterdir()} == {'alpha', 'beta', 'corrupt', 'unattached'}
            first = json.loads((base / 'alpha' / (due.date().isoformat()+'.json')).read_text())
            assert first['outcome'] == 'ok' and first['reason'] == 'full_integrity_ok'
            assert first['format'] == 'workflow-integrity-report@1' and len(monitor._json(first)) <= 4096
            assert json.loads((base / 'corrupt/latest.json').read_text())['outcome'] == 'failed'
            unattached = json.loads((base / 'unattached/latest.json').read_text())
            assert unattached['outcome'] == 'unavailable' and unattached['reporting']['audit'] == 'unavailable'
            # No OrgState loader, complete workflow reader or admission checker
            # is invoked by this pass, including for failed/unattached orgs.
            from runtime.daemon.routes.audit import list_audit
            db = Database(alpha_db)
            route = list_audit('alpha', SimpleNamespace(db=db), action=monitor.ACTION,
                              include_thread_origin=False)
            entries = route['entries']
            assert len(entries) == 1 and entries[0]['task_id'] == '' and entries[0]['agent'] == 'daemon'
            assert entries[0]['payload'] == dict(first, reporting={'external': 'persisted', 'audit': 'pending'}), entries
            db.close()
            hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in base.rglob('*.json')}
            # New monitor simulates process restart/repeat wake; exclusive date
            # receipts, rather than the process-local set, prevent duplicates.
            repeat = monitor.OrgIntegrityMonitor(state)
            await repeat.run_pass(due)
            assert hashes == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in base.rglob('*.json')}
            await repeat.run_pass(due + timedelta(days=1))
            for slug in ('alpha', 'beta'):
                assert len(list((base / slug).glob('*.reserved.json'))) == 2
                latest = json.loads((base / slug / 'latest.json').read_text())
                assert latest['scheduled_date'] == (due + timedelta(days=1)).date().isoformat()
                assert latest['reporting'] == {'external': 'persisted', 'audit': 'persisted'}
                assert (base / slug).stat().st_mode & 0o777 == 0o700
                assert all(p.stat().st_mode & 0o777 == 0o600 for p in (base / slug).iterdir())
            await owner.close()
            await repeat.close()

            daily_case('runtime switch keeps equal slug/date attempts independent')
            other_runtime = private / 'other-runtime'
            other_alpha = other_runtime / 'orgs/alpha'
            (other_alpha / 'org').mkdir(parents=True)
            (other_alpha / 'org/teams.yaml').write_text('teams: {}\n')
            other_db = Database(other_alpha / 'happyranch.db')
            other_db.close()
            switched = monitor.OrgIntegrityMonitor(state)
            await switched.run_pass(due)
            state.runtime = RuntimeDir(other_runtime)
            try:
                await switched.run_pass(due)
                assert (home / 'workflow-integrity' / monitor.runtime_key(other_runtime) / 'alpha' / (due.date().isoformat()+'.json')).is_file(), 'new runtime inherited another runtime attempt'
            finally:
                state.runtime = RuntimeDir(rt)
                await switched.close()

            daily_case('future 03:00 + outage/no startup catchup + stop/skip/exception/slow sweep independence')
            stop = home / 'disk-sweep'
            stop.mkdir()
            (stop / 'stop.json').write_text('{"stop":true}')
            wake_date = due + timedelta(days=3)
            before_wake = wake_date.replace(hour=2, minute=59)
            after_wake = wake_date.replace(hour=3, minute=1)
            assert sweeper.next_night(before_wake) == wake_date
            assert sweeper.next_night(wake_date).date() == wake_date.date() + timedelta(days=1)
            assert sweeper.next_night(after_wake).date() == wake_date.date() + timedelta(days=1)
            class Clock(datetime):
                @classmethod
                def now(cls, tz=None):
                    return before_wake
            sleeper = asyncio.sleep
            nightly = monitor.OrgIntegrityMonitor(state)
            sleeps, sweep_calls = [], []
            async def sleep(delay):
                sleeps.append(delay)
                if len(sleeps) > 1:
                    assert (base / 'alpha' / (wake_date.date().isoformat()+'.json')).is_file(), 'daily integrity report missing under sweep stop'
                    raise asyncio.CancelledError
                assert not nightly.tasks and not (base / 'alpha' / (wake_date.date().isoformat()+'.json')).exists(), 'startup catch-up'
                await sleeper(0)
            native_sweep = sweeper._run_scheduled_worker
            async def suspended_sweep(*args):
                sweep_calls.append('entered')
                assert nightly.tasks, 'integrity launch coupled to sweep stop'
                # Wait for the real read/report pass while sweep work is still
                # pending: no simulated successful integrity result.
                done, pending = await asyncio.wait(nightly.tasks, timeout=12)
                assert done and not pending, 'integrity waited on sweep'
                for task in done:
                    task.result()
                report = json.loads((base / 'alpha' / (wake_date.date().isoformat()+'.json')).read_text())
                assert report['outcome'] == 'ok', report
                return await native_sweep(*args)
            with patch.object(sweeper, 'datetime', Clock), patch.object(sweeper.asyncio, 'sleep', sleep), \
                 patch.object(sweeper, '_record_previous_night', lambda *args: None), \
                 patch.object(sweeper, '_run_scheduled_worker', suspended_sweep):
                try:
                    await sweeper.disk_sweeper_loop(state, nightly)
                except asyncio.CancelledError:
                    pass
            assert len(sweep_calls) == 1 and len(sleeps) == 2
            assert not (base / 'alpha' / ((due+timedelta(days=2)).date().isoformat()+'.json')).exists()
            await nightly.close()

            for variant, delta in (('sweep-exception', 6), ('sweep-marker-skip', 7)):
                daily_case(variant + ' leaves independent report')
                wake_date = due + timedelta(days=delta)
                before_wake = wake_date.replace(hour=2, minute=59)
                nightly = monitor.OrgIntegrityMonitor(state)
                sleeps, sweep_calls = [], []
                if variant == 'sweep-marker-skip':
                    sweeper.atomic_json(stop / 'last-scheduled.json', {'scheduled_date': wake_date.date().isoformat()})
                async def variant_sleep(delay):
                    sleeps.append(delay)
                    if len(sleeps) == 1:
                        assert not nightly.tasks
                        await sleeper(0)
                        return
                    if nightly.tasks:
                        done, pending = await asyncio.wait(set(nightly.tasks), timeout=12)
                        assert not pending
                        for task in done:
                            task.result()
                    report_path = base / 'alpha' / (wake_date.date().isoformat()+'.json')
                    assert report_path.is_file(), 'independent report missing after ' + variant
                    assert json.loads(report_path.read_text())['outcome'] == 'ok'
                    raise asyncio.CancelledError
                async def unavailable_sweep(*args):
                    sweep_calls.append(variant)
                    raise OSError('synthetic sweep exception')
                with patch.object(sweeper, 'datetime', Clock), patch.object(sweeper.asyncio, 'sleep', variant_sleep), \
                     patch.object(sweeper, '_record_previous_night', lambda *args: None), \
                     patch.object(sweeper, '_run_scheduled_worker', unavailable_sweep):
                    try:
                        await sweeper.disk_sweeper_loop(state, nightly)
                    except asyncio.CancelledError:
                        pass
                assert sweep_calls == ([] if variant == 'sweep-marker-skip' else [variant]), sweep_calls
                await nightly.close()

            daily_case('deadline terminate/kill/reap and cancellation')
            timed = monitor.OrgIntegrityMonitor(state)
            real_argv = monitor.helper_argv
            def slow_argv(kind):
                return [sys.executable, '-c', 'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(9)'] if kind == 'read' else real_argv(kind)
            started = time.monotonic()
            with patch.object(monitor, 'helper_argv', slow_argv):
                assert await timed.invoke('read', {}, started + .15) is None
                assert time.monotonic() - started < 2.25 and not timed.in_flight
                task = asyncio.create_task(timed.invoke('read', {}, time.monotonic()+5))
                await sleeper(.1)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                else:
                    raise AssertionError('worker cancellation swallowed')
                assert not timed.in_flight
            await timed.close()

            daily_case('unreaped original retained; later org unavailable/in_flight_reader')
            class Input:
                def write(self, body): pass
                async def drain(self): pass
                def close(self): pass
            class Stuck:
                pid = 999999999
                returncode = None
                stdin = Input()
                @property
                def stdout(self): return self
                async def read(self, n): await asyncio.Event().wait()
            original_spawn, killpg = asyncio.create_subprocess_exec, os.killpg
            stuck, launches, signals = Stuck(), [], []
            async def spawn(*argv, **kwargs):
                if argv[-1] == 'read':
                    launches.append(argv)
                    return stuck
                return await original_spawn(*argv, **kwargs)
            def signal_group(pid, sig):
                if pid == stuck.pid: signals.append(sig)
                else: killpg(pid, sig)
            held = monitor.OrgIntegrityMonitor(state)
            with patch.object(monitor.asyncio, 'create_subprocess_exec', spawn), \
                 patch.object(monitor.os, 'killpg', signal_group), \
                 patch.object(monitor, 'READ_SECONDS', .01), patch.object(monitor, 'CLEANUP_SECONDS', .02):
                await held.run_pass(due + timedelta(days=5))
                assert len(launches) == 1 and len(held.in_flight) == 1
                later = json.loads((base / 'beta' / ((due+timedelta(days=5)).date().isoformat()+'.json')).read_text())
                assert later['reason'] == 'in_flight_reader' and later['outcome'] == 'unavailable', later
                started = time.monotonic()
                await held.close()
                assert time.monotonic() - started < .2 and held.in_flight
                assert signal.SIGTERM in signals and signal.SIGKILL in signals
                stuck.returncode = -signal.SIGKILL
                held._reap_observed()
                assert not held.in_flight
                for task in asyncio.all_tasks():
                    if task is not asyncio.current_task() and not task.done():
                        # Only the test's synthetic forever-read transport.
                        task.cancel()
            return base, first

        with patch.dict(os.environ, {'HAPPYRANCH_DAEMON_HOME': str(home)}):
            base, first = asyncio.run(daily_integration())

        daily_case('audit/disk publication faults; no overwrite/symlink/hardlink')
        report = dict(first, scheduled_date='2031-03-01', report_id='f'*64)
        request = dict(home=str(home), root=str(alpha), report=report, operation='reserve')
        assert monitor._storage(request, time.monotonic()+2) == {'reserved': True}
        with patch.object(monitor, '_audit', side_effect=OSError('private failure never emit')):
            receipt = monitor._storage(dict(request, operation='publish'), time.monotonic()+2)
        assert receipt['external'] == 'persisted' and receipt['audit'] == 'unavailable'
        latest = json.loads((base / 'alpha/latest.json').read_text())
        assert latest['outcome'] == 'ok' and latest['reporting']['audit'] == 'unavailable'
        # Faults stay test-side; the real existing audit writer still runs.
        blocked_home = private / 'blocked-home'
        blocked_home.mkdir()
        (blocked_home / 'workflow-integrity').symlink_to(home / 'workflow-integrity', target_is_directory=True)
        receipt = monitor._storage(dict(request, home=str(blocked_home), operation='publish'), time.monotonic()+2)
        assert receipt['external'] == 'unavailable' and receipt['audit'] == 'persisted'
        with patch.object(monitor, '_audit', side_effect=OSError):
            receipt = monitor._storage(dict(request, home=str(blocked_home), operation='publish'), time.monotonic()+2)
        assert receipt['external'] == receipt['audit'] == 'unavailable'
        assert 'private failure' not in json.dumps(receipt)
        messages = io.StringIO()
        handler = logging.StreamHandler(messages)
        monitor.LOG.addHandler(handler)
        async def unavailable_reporting():
            owner = monitor.OrgIntegrityMonitor(state)
            await owner.run_pass(due + timedelta(days=10))
            await owner.close()
        try:
            with patch.dict(os.environ, {'HAPPYRANCH_DAEMON_HOME': str(blocked_home)}):
                asyncio.run(unavailable_reporting())
        finally:
            monitor.LOG.removeHandler(handler)
        assert 'workflow_integrity_report_unavailable reason=report_unavailable' in messages.getvalue()
        assert 'private_corrupt_index' not in messages.getvalue() and str(private) not in messages.getvalue()
        record_path = base / 'alpha/2031-03-01.json'
        digest = hashlib.sha256(record_path.read_bytes()).hexdigest()
        monitor._storage(dict(request, operation='publish'), time.monotonic()+2)
        assert hashlib.sha256(record_path.read_bytes()).hexdigest() == digest
        with monitor.directory(base / 'alpha', time.monotonic()+2) as parent:
            os.link(record_path, base / 'alpha/hardlinked.json')
            try:
                monitor._safe_read(parent, 'hardlinked.json', 32768)
            except ValueError:
                pass
            else:
                raise AssertionError('hardlinked report accepted')
        daily_case('PASS')
    if sys.argv[1:]:
        raise SystemExit(0)

# A4-N reads genuine private native histories before any install intent/effect.
if sys.argv[1:] in ([], ['--bootstrap-owners']):
    from runtime.infrastructure import workflow_sequential_bootstrap as bootstrap
    from runtime.infrastructure import workflow_schema as schema
    from runtime.infrastructure.org_access_fence import OrgAccessFence
    from runtime.infrastructure.producer_admission import ProducerAdmissionStore, ProducerLifecycle, NativeWriterStore, NativeWriterLifecycle
    from runtime.infrastructure.thread_admission import ThreadAdmissionStore, ThreadLifecycle, ThreadAttempt
    from runtime.models import ThreadRecord, ThreadInvocationPurpose

    cases = ('empty', 'producer_settled', 'producer_active', 'producer_unknown',
        'producer_quiescence', 'producer_rewrite', 'writer_settled', 'writer_active', 'writer_unknown',
        'writer_rewrite', 'pause_prepared', 'pause_unknown', 'thread_settled', 'thread_active',
        'thread_unknown', 'thread_missing', 'thread_orphan', 'thread_rewrite', 'acquisition',
        'deadline', 'sql_budget', 'query_only', 'task_active', 'task_terminal',
        'publication_lease', 'profile_lease', 'domain_acquisition', 'repository_opened',
        'repository_started', 'repository_finished', 'repository_returned', 'repository_outer',
        'repository_closed', 'repository_unsealed', 'repository_rewrite', 'repository_foreign', 'repository_acquisition')
    for fault in cases:
        if owners_case is not None and owners_case != fault:
            continue
        with tempfile.TemporaryDirectory(prefix='a4-native-history-') as scratch:
            private = Path(scratch).resolve()
            home = private/'home'
            home.mkdir(mode=0o700)
            root = private/'runtime/orgs/scratch'
            (root/'org').mkdir(parents=True)
            (root/'org/teams.yaml').write_text('teams: {}\n')
            path = root/'happyranch.db'
            db = Database(path)
            try:
                schema.initialize_complete_org_schema(db, expected_org_slug='scratch', layout='J')
                install_pause_schema(db, org_slug='scratch')
                install_task_ownership(db, org_slug='scratch')
                if fault in ('task_active', 'task_terminal'):
                    from runtime.models import TaskStatus
                    db.insert_task(TaskRecord(id='TASK-synthetic-domain', brief='private domain owner',
                        status=TaskStatus.COMPLETED if fault=='task_terminal' else TaskStatus.PENDING,
                        assigned_agent='synthetic-agent'))
                if fault in ('publication_lease', 'profile_lease'):
                    table = 'workflow_publication_leases' if fault=='publication_lease' else 'workflow_profile_leases'
                    # Even a definitely absent process supplies no release of
                    # this original retained publication/profile ownership.
                    db._conn.execute('INSERT INTO '+table+' VALUES (?,?,?)', ('synthetic-owner', 'original-token', 2147483647))
                if fault == 'domain_acquisition':
                    db._conn.executemany('INSERT INTO workflow_authority_pointers VALUES (?,0,NULL,NULL,\'ready\',0)',
                        [('synthetic-namespace-'+str(n),) for n in range(4097)])
                if fault.startswith('repository_'):
                    from runtime.infrastructure.db.tasks import repository_preparation_event
                    from runtime.models import TaskStatus
                    # Original constructors and append validator create the
                    # histories. This fixture grants no live owner clearance.
                    for number in range(1366 if fault=='repository_acquisition' else 1):
                        scope = 'TASK-synthetic-repository-'+str(number)
                        db.insert_task(TaskRecord(id=scope, brief='private terminal repository preparation',
                            status=TaskStatus.COMPLETED, assigned_agent='synthetic-agent'))
                        owner = dict(org='elsewhere' if fault=='repository_foreign' else 'scratch', kind='task',
                            scope=scope, agent='synthetic-agent', session_id='synthetic-sid',
                            invocation_id='a'*32, attempt_id='b'*32, boot_id='synthetic-boot', native_id=scope, source='c'*64)
                        repository = dict(path=str(private/'synthetic-repository'), device=1, inode=1)
                        obligation, request = format(number+1, '032x'), 'd'*32
                        events = []
                        def event(phase, **facts):
                            body = repository_preparation_event(owner, repository, obligation, len(events), phase, **facts)
                            db.append_repository_preparation_uncommitted(body)
                            events.append(body)
                        event('opened')
                        if fault != 'repository_opened':
                            command = dict(command='probe', probe='target_type', request_ref=request)
                            event('intent', **command)
                            if fault == 'repository_acquisition':
                                event('not_admitted', **command, complete=True)
                            else:
                                event('attempted', **command, attempted=True)
                                identity = dict(pid=2147483647, handle_ref='e'*64, start_pin='f'*64)
                                event('started', **command, **identity, attempted=True, started=True)
                                if fault != 'repository_started':
                                    returned = dict(attempted=True, started=True, consumed=True, returncode=0,
                                        capture_complete=True)
                                    event('consumed', **command, **identity, **returned)
                                    event('finished', **command, **identity, **returned, complete=True,
                                        receipt=dict(quiescent=True, survivors=0, cleanup_error=False, cleanup_status='clean'))
                            if fault not in ('repository_started', 'repository_finished'):
                                event('worker_returned', complete=True)
                                if fault != 'repository_returned':
                                    event('outer_closed', complete=fault!='repository_outer',
                                        inventory=dict(intervals=[], retained=[], outer_closed=fault!='repository_outer'))
                                    if fault not in ('repository_outer', 'repository_unsealed'):
                                        event('closed', complete=True, state_digest='0'*64)
                        if fault == 'repository_rewrite':
                            # Deliberate historical corruption, not a validator
                            # stub: the strict reader must retain its own cause.
                            changed = json.loads(json.dumps(events[-1]))
                            changed['owner']['source'] = '1'*64
                            db._conn.execute("UPDATE audit_log SET payload=? WHERE id=(SELECT max(id) FROM audit_log "
                                "WHERE action='repository_preparation')", (json.dumps(changed),))
                if fault.startswith('producer_'):
                    store = ProducerAdmissionStore(db, 'scratch')
                    first = ProducerLifecycle(org_slug='scratch', kind='cleanup', source_id='synthetic-source',
                        occurrence_id='synthetic-occurrence', agent_id='synthetic-agent', session_id='synthetic-sid',
                        preparation_id='synthetic-preparation', enqueued_at='2026-11-17T00:00:00+00:00', retry_initialized=False)
                    store.save_uncommitted(first)
                    if fault != 'producer_active':
                        state = first.model_copy(update=dict(producer_settled=True,
                            failed_cleanup=fault=='producer_unknown'))
                        if fault == 'producer_quiescence':
                            state = state.model_copy(update=dict(retry_initialized=True, attempts=(ThreadAttempt(
                                attempt_id='synthetic-attempt', phase='initial', boundary='host', ordinal=0,
                                settled=True, quiescent=False),)))
                        store.save_uncommitted(state)
                        if fault == 'producer_rewrite':
                            db.insert_audit_log_uncommitted(first.source_id, first.agent_id,
                                'producer_move_lifecycle', json.loads(state.model_copy(update=dict(
                                    preparation_id='synthetic-replaced-preparation')).model_dump_json()))
                if fault.startswith('writer_') or fault == 'acquisition':
                    store = NativeWriterStore(db, 'scratch')
                    first = NativeWriterLifecycle(org_slug='scratch', writer_id='synthetic-writer', publisher='disk_sweep')
                    store.save_uncommitted(first)
                    if fault != 'writer_active':
                        store.save_uncommitted(first.model_copy(update=dict(
                            state='unknown' if fault=='writer_unknown' else 'settled')))
                    if fault == 'writer_rewrite':
                        db.insert_audit_log_uncommitted('config:team-move-writer:'+first.writer_id,
                            'native_writer', 'team_move_native_writer', json.loads(first.model_copy(update=dict(
                                state='settled', publisher='synthetic-replaced-publisher')).model_dump_json()))
                    if fault == 'acquisition':
                        # Valid individual rows; only their original aggregate
                        # acquisition bound can refuse the complete history.
                        raw = first.model_copy(update=dict(state='settled')).model_dump_json()
                        db._conn.executemany("INSERT INTO audit_log(task_id,agent,action,payload,timestamp) VALUES (?,?,?,?,?)",
                            [('config:team-move-writer:'+first.writer_id, 'native_writer', 'team_move_native_writer', raw,
                              '2026-11-17T00:00:00+00:00')]*8191)
                if fault.startswith('pause_'):
                    from runtime.models import TaskStatus
                    db.insert_task(TaskRecord(id='TASK-synthetic-pause', brief='private terminal preparation',
                        status=TaskStatus.COMPLETED, assigned_agent='synthetic-agent'))
                    pause = TaskPauseStore(db, 'scratch')
                    row = pause.ensure_uncommitted('TASK-synthetic-pause')
                    row['journal']['entries'] = [dict(id='synthetic-native', org='scratch', root_task_id='TASK-synthetic-pause',
                        task_id='TASK-synthetic-pause', agent='synthetic-agent', session_id='synthetic-sid', owner='ordinary',
                        owner_identity={}, fingerprint=dict(status='pending', block_kind=None, count=0, session_id=None,
                            agent='synthetic-agent', fanout=None, jobs=None, parent=None, brief_digest='0'*64),
                        generation=0, phase='unknown' if fault=='pause_unknown' else 'prepared', retry={},
                        context={'execution_unknown': True} if fault=='pause_unknown' else {},
                        started_at=None, captured_generation=None)]
                    pause.save_journal_uncommitted(row)
                if fault.startswith('thread_'):
                    db.insert_thread(ThreadRecord(id='THR-synthetic-history', subject='private original native history'))
                    inv = db.mint_thread_invocation(thread_id='THR-synthetic-history', agent_name='synthetic-agent',
                        triggering_seq=0, purpose=ThreadInvocationPurpose.REPLY)
                    db._conn.execute("UPDATE thread_invocations SET status='failed',session_id='synthetic-sid' WHERE id=?", (inv.id,))
                    inv = SimpleNamespace(id=inv.id, thread_id=inv.thread_id, agent_name=inv.agent_name, session_id='synthetic-sid')
                    first = ThreadLifecycle(org_slug='scratch', invocation_id=inv.id, thread_id=inv.thread_id,
                        agent_id=inv.agent_name, session_id=inv.session_id, preparation_id='synthetic-thread-preparation', retry_initialized=False)
                    store = ThreadAdmissionStore(db, 'scratch')
                    if fault != 'thread_missing':
                        store.save_uncommitted(inv, first)
                        if fault != 'thread_active':
                            store.save_uncommitted(inv, first.model_copy(update=dict(producer_settled=True,
                                failed_cleanup=fault=='thread_unknown')))
                    if fault == 'thread_orphan':
                        db._conn.execute('DELETE FROM thread_invocations WHERE id=?', (inv.id,))
                    if fault == 'thread_rewrite':
                        db.insert_audit_log_uncommitted(inv.thread_id, inv.agent_name,
                            'thread_move_lifecycle', json.loads(first.model_copy(update=dict(
                                producer_settled=True, preparation_id='synthetic-replaced-preparation')).model_dump_json()))
                db._conn.commit()
                db._conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            finally:
                db.close()
            before = path.read_bytes()
            with patch.dict(os.environ, {'HAPPYRANCH_DAEMON_HOME': str(home)}):
                fence = OrgAccessFence.acquire(root, exclusive=True)
                source = bootstrap.BootstrapSource(fence=fence, deadline=time.monotonic()+119)
                original_gate = bootstrap.require_bootstrap_idle_history
                reached = []
                history_sql = []
                def observe(owner):
                    assert owner is source and owner.source.in_transaction, 'A4-N lost original snapshot'
                    assert owner.source.execute('PRAGMA query_only').fetchone()[0] == 1, 'A4-N native facade writable'
                    reached.append('original-history')
                    owner.source.set_trace_callback(history_sql.append)
                    if fault == 'sql_budget':
                        source._inventory_steps = 640000000
                    if fault == 'query_only':
                        owner.source.execute('PRAGMA query_only=OFF')
                    if fault == 'deadline':
                        # Only the private clock is changed. The original source
                        # operation stays live; its five-second read expires.
                        real = time.monotonic
                        start = real()
                        calls = [0]
                        def now():
                            calls[0] += 1
                            return start if calls[0] <= 3 else start+6
                        with patch.object(bootstrap.time, 'monotonic', side_effect=now):
                            return original_gate(owner)
                    return original_gate(owner)
                try:
                    with patch.object(bootstrap, 'require_bootstrap_idle_history', side_effect=observe), patch.object(
                            Database, '__init__', side_effect=AssertionError('A4-N constructed writable facade')):
                        error = None
                        try:
                            source.open()
                        except (ValueError, sqlite3.Error) as exc:
                            error = exc
                    good = fault in ('empty', 'producer_settled', 'writer_settled', 'thread_settled', 'task_terminal',
                                     'repository_closed')
                    assert reached == ['original-history'], ('A4-N original history gate missing', fault, reached)
                    assert (error is None) == good, ('A4-N accepted unclosed native history' if not good else 'A4-N rejected settled original history', fault, type(error).__name__)
                    if not good:
                        expected = dict(producer_rewrite='producer identity changed',
                            writer_rewrite='native writer identity changed', thread_rewrite='thread lifecycle identity changed',
                            thread_missing='thread history missing', acquisition='workflow_setup_history_acquisition_exhausted',
                            deadline='workflow_setup_history_deadline', sql_budget='workflow_setup_inventory_exhausted',
                            query_only='workflow_setup_history_owner_invalid', repository_rewrite='conflicting_history',
                            repository_acquisition='workflow_setup_history_acquisition_exhausted').get(fault, 'workflow_setup_history_not_idle')
                        if fault in ('task_active', 'publication_lease', 'profile_lease'):
                            owner = dict(task_active='tasks:TASK-synthetic-domain',
                                publication_lease='workflow_publication_leases', profile_lease='workflow_profile_leases')[fault]
                            assert owner in str(error) and str(error).startswith(('workflow_coding_source_owner_unresolved:',
                                'workflow_submission_source_owner_active:')), ('A4-N wrong current domain cause', fault, str(error))
                        else:
                            if fault == 'domain_acquisition':
                                expected = 'workflow_setup_history_acquisition_exhausted'
                            assert str(error) == expected, ('A4-N wrong native refusal cause', fault, type(error).__name__, str(error))
                    if fault == 'repository_acquisition':
                        assert not any('THEN payload END AS payload' in sql for sql in history_sql), \
                            'A4-N acquired repository payload before whole bound'
                    assert source.failed == (not good), 'A4-N failure did not latch source'
                    assert source.source is not None and source.source.in_transaction, 'A4-N original source lost'
                    if fault != 'query_only':
                        assert source.source.execute('PRAGMA query_only').fetchone()[0] == 1, 'A4-N source became writable'
                    assert not (home/'backups').exists() and source.backup_sha256 is None, 'A4-N effects before native qualification'
                    changes = source.source.total_changes
                    repeated_sql = []
                    source.source.set_trace_callback(repeated_sql.append)
                    try:
                        original_gate(source)
                    except ValueError:
                        pass
                    else:
                        raise AssertionError('A4-N replayed original history qualification')
                    finally:
                        source.source.set_trace_callback(None)
                    assert repeated_sql == [], 'A4-N repeated native history SQL'
                    assert source.source.total_changes == changes, 'A4-N repeat changed native source'
                    source.close()
                    assert source.closed and all(record['closed'] for record in source.connections), 'A4-N original close unproved'
                    assert path.read_bytes() == before, 'A4-N native history observation changed source'
                    print('PASS A4-N strict original native history/source unchanged:', fault, flush=True)
                finally:
                    source.close()
                    fence.close()
    if sys.argv[1:] == ['--bootstrap-owners']:
        raise SystemExit(0)

# A4-D composes the actual original fence/selector and native loader boundaries.
if sys.argv[1:] in ([], ['--bootstrap-discovery']):
    from runtime.infrastructure import workflow_sequential_bootstrap as bootstrap
    from runtime.infrastructure import workflow_schema as schema
    from runtime.infrastructure.org_access_fence import OrgAccessFence, OrgAccessHeld, original_org_fence
    from runtime.daemon.routes.orgs import _seed_skeleton
    from runtime.orchestrator.org_config import initialize_fresh_reviewer_agents

    discovery_cases = ('present', 'absent', 'ex_reselect', 'total', 'org', 'installed_expired',
                       'deadlines', 'busy', 'shared_close', 'selection_close', 'ex_acquire',
                       'transfer', 'load', 'load_fault', 'foreign_fence')
    for fault in discovery_cases:
        if discovery_case is not None and discovery_case != fault:
            continue
        with tempfile.TemporaryDirectory(prefix='a4-discovery-') as scratch:
            private = Path(scratch).resolve()
            home, runtime = private/'home', private/'runtime'
            home.mkdir(mode=0o700)
            root = runtime/'orgs/scratch'
            _seed_skeleton(root, from_example=None)
            path = root/'happyranch.db'
            installed = fault in ('present', 'installed_expired', 'transfer', 'load', 'load_fault', 'foreign_fence')
            db = schema.initialize_fresh_org_database(path, expected_org_slug='scratch') if installed else Database(path)
            try:
                if not installed:
                    schema.initialize_complete_org_schema(db, expected_org_slug='scratch', layout='J')
                install_pause_schema(db, org_slug='scratch')
                initialize_fresh_reviewer_agents(OrgPaths(root), db)
            finally:
                db.close()
            original_bytes = path.read_bytes()
            with patch.dict(os.environ, {'HAPPYRANCH_DAEMON_HOME': str(home)}), ExitStack() as stack:
                actual_clock = time.monotonic
                offset = [0.0]
                stack.enter_context(patch.object(bootstrap.time, 'monotonic', side_effect=lambda: actual_clock()+offset[0]))
                startup = bootstrap.BootstrapStartup(home=home, runtime_root=runtime)
                if fault in ('total', 'installed_expired'):
                    offset[0] = 181 if fault == 'installed_expired' else 121
                route = startup.discover(root)
                assert route.deadline == min(route.started_at+120, startup.deadline), 'A4-D original org/total deadlines'
                acquisitions, acquisition_intervals, closes, injected, native = [], [], [], [], []
                actual_acquire = OrgAccessFence.acquire
                actual_close = OrgAccessFence.close
                actual_select = bootstrap.BootstrapDiscovery._select
                actual_connect = sqlite3.connect
                blocker = None
                if fault == 'busy':
                    blocker = actual_acquire(root)
                def acquire(selected_root, **kwargs):
                    acquisitions.append((selected_root, dict(kwargs)))
                    interval = [actual_clock(), None]
                    acquisition_intervals.append(interval)
                    try:
                        if fault == 'busy' and len(acquisitions) == 1:
                            time.sleep(.3)
                        fence = actual_acquire(selected_root, **kwargs)
                    finally:
                        interval[1] = actual_clock()
                    if fault == 'ex_acquire' and kwargs.get('exclusive'):
                        def unavailable():
                            injected.append('ex_acquire')
                            raise OSError('synthetic returned EX observation unavailable')
                        fence.assert_held = unavailable
                    return fence
                def close(fence):
                    closes.append(fence)
                    if fault == 'shared_close' and not injected and not fence.exclusive:
                        injected.append('shared_close')
                        raise OSError('synthetic original SH close uncertainty')
                    return actual_close(fence)
                def select(discovery, fence, end):
                    result = actual_select(discovery, fence, end)
                    if fault == 'busy' and len(discovery.selections) == 1:
                        # Work between acquisitions consumes the original org
                        # deadline, but is not another native admission wait.
                        time.sleep(.04)
                    if fault == 'org' and len(discovery.selections) == 1:
                        offset[0] = 101
                    return result
                def connect(*args, **kwargs):
                    conn = actual_connect(*args, **kwargs)
                    if fault != 'selection_close':
                        return conn
                    native.append(conn)
                    class CloseFault:
                        def __getattr__(self, name):
                            return getattr(conn, name)
                        @property
                        def row_factory(self):
                            return conn.row_factory
                        @row_factory.setter
                        def row_factory(self, value):
                            conn.row_factory = value
                        def close(self):
                            injected.append('selection_close')
                            raise OSError('synthetic original selector close uncertainty')
                    return CloseFault()
                actual_close_fence = bootstrap.BootstrapDiscovery._close_fence
                def close_fence(discovery, record):
                    result = actual_close_fence(discovery, record)
                    if fault == 'ex_reselect' and len(discovery.fences) == 1 and record['closed'] and not injected:
                        injected.append('ex_reselect')
                        with closing(actual_connect(path)) as changed:
                            changed.execute('CREATE TABLE workflow_seq_arrived(v TEXT)')
                            changed.commit()
                    return result
                stack.enter_context(patch.object(OrgAccessFence, 'acquire', side_effect=acquire))
                stack.enter_context(patch.object(OrgAccessFence, 'close', close))
                stack.enter_context(patch.object(bootstrap.BootstrapDiscovery, '_select', select))
                stack.enter_context(patch.object(bootstrap.BootstrapDiscovery, '_close_fence', close_fence))
                stack.enter_context(patch.object(bootstrap.sqlite3, 'connect', side_effect=connect))
                forbidden = [stack.enter_context(patch.object(owner, name,
                    side_effect=AssertionError('A4-D source/install work in discovery')))
                    for owner, name in ((bootstrap.BootstrapSource, 'open'), (bootstrap.BootstrapAttempt, 'begin'),
                                        (bootstrap, 'require_install_integrity'), (schema, 'sequential_storage_inventory'), (schema, 'install_sequential_schema'))]
                error = None
                result = None
                started = actual_clock()
                try:
                    result = route.read()
                except BaseException as exc:
                    error = exc
                elapsed = actual_clock()-started
                assert all(call.call_count == 0 for call in forbidden), 'A4-D source/install work in discovery'
                if fault in ('shared_close', 'selection_close', 'ex_acquire'):
                    assert error is not None and route.failed and result is None, 'A4-D unproven discovery escaped'
                    assert route.primary is error, 'A4-D original discovery cause lost'
                    if fault == 'ex_acquire':
                        assert injected == ['ex_acquire'] and len(route.fences) == 2, 'A4-D lost returned EX before observation'
                        assert route.closed and all(r['closed'] for r in route.fences), 'A4-D lost returned EX custody'
                    else:
                        assert not route.closed and route.close_error is not None, 'A4-D unknown discovery closure lost'
                        before = len(closes), len(injected)
                        try:
                            route.close()
                        except bootstrap.BootstrapEvidenceError:
                            pass
                        else:
                            raise AssertionError('A4-D unknown discovery close retried')
                        assert before == (len(closes), len(injected)), 'A4-D retried original native close'
                        try:
                            actual_acquire(root, exclusive=True)
                        except OrgAccessHeld:
                            pass
                        else:
                            raise AssertionError('A4-D released unknown original fence')
                    try:
                        route.take_shared()
                    except bootstrap.BootstrapEvidenceError:
                        pass
                    else:
                        raise AssertionError('A4-D transferred uncertain original')
                else:
                    assert error is None, (fault, error)
                    expected = ('held' if fault in ('total', 'org', 'busy') else
                                'absent' if fault in ('absent', 'deadlines') else 'present')
                    assert result == expected, 'A4-D wrong fresh routing'
                    if fault in ('total', 'org'):
                        assert route.reason == 'startup_upgrade_budget_unavailable' and len(acquisitions) == 1, 'A4-D exhausted upgrade admitted EX'
                    if fault == 'installed_expired':
                        assert route.reason is None and len(acquisitions) == 1 and not route.fence.exclusive, 'A4-D upgrade expiry denied installed load'
                    if fault == 'absent':
                        assert len(route.selections) == 2 and route.fences[0]['closed'] and route.fence.exclusive, 'A4-D stale SH absence reused'
                    if fault == 'ex_reselect':
                        assert len(route.selections) == 3 and not route.fence.exclusive and all(r['closed'] for r in route.fences[:-1]), 'A4-D EX arrival bypassed SH rediscovery'
                    if fault == 'busy':
                        assert route.reason == 'writer_exclusion_unproven' and route.exclusion_error is not None, 'A4-D contention omitted visible hold'
                        assert .45 <= elapsed < .85, ('A4-D replenished cumulative busy wait', elapsed)
                        first_start, first_end = acquisition_intervals[0]
                        second_start, _ = acquisition_intervals[1]
                        # These independently observed native intervals are a
                        # LOWER bound on the production ledger's charges. The
                        # next requested wait cannot exceed its remaining .5s.
                        requested = acquisitions[1][1]['deadline'] - second_start
                        assert requested <= max(0, .5-(first_end-first_start)), 'A4-D renewed admission deadline'
                        assert 'deadline' not in acquisitions[2][1], 'A4-D exhausted wait was replenished'
                        assert not route.fence.exclusive and len(route.selections) == 2, 'A4-D contention bypassed fresh ordinary SH'
                        print('A4-D admission', json.dumps(dict(first=first_end-first_start,
                            second_requested=requested, elapsed=elapsed,
                            charged=startup.admission_seconds), sort_keys=True))
                    if fault == 'deadlines':
                        original_deadline = startup.deadline
                        offset[0] = 100
                        second = startup.discover(runtime/'orgs/later')
                        assert startup.deadline == original_deadline and second.deadline == original_deadline, 'A4-D renewed all-org window'
                        assert route.deadline == route.started_at+120, 'A4-D renewed original org window'
                        try:
                            startup.discover(root)
                        except bootstrap.BootstrapEvidenceError:
                            pass
                        else:
                            raise AssertionError('A4-D repeated original discovery')
                    if fault in ('transfer', 'load', 'load_fault'):
                        fence = route.take_shared()
                        assert fence is route.fence and route.fences[-1]['transferred'], 'A4-D original SH not transferred'
                        route.close()
                        fence.assert_held()
                        try:
                            route.take_shared()
                        except bootstrap.BootstrapEvidenceError:
                            pass
                        else:
                            raise AssertionError('A4-D transferred original SH twice')
                        if fault == 'transfer':
                            actual_close(fence)
                        else:
                            owner = dict(startup_acquisition=True)
                            writer = actual_connect(path, timeout=0) if fault == 'load_fault' else None
                            if writer is not None:
                                writer.execute('BEGIN EXCLUSIVE')
                            before = len(acquisitions)
                            loaded = None
                            try:
                                loaded = OrgState.load(slug='scratch', root=root, settings=Settings(),
                                    attachment_owner=owner, access_fence=fence)
                            except sqlite3.OperationalError:
                                assert fault == 'load_fault' and owner['load_db_closed'] and fence._closed, 'A4-D failed attachment lost original SH'
                            else:
                                assert fault == 'load' and loaded.db._org_access_fence is fence and original_org_fence('scratch') is fence, 'A4-D normal load replaced original SH'
                            finally:
                                if writer is not None:
                                    writer.rollback()
                                    writer.close()
                                if loaded is not None:
                                    loaded.close()
                            assert len(acquisitions) == before, 'A4-D normal loader reacquired original SH'
                            assert fence._closed, 'A4-D loader omitted original close'
                    elif fault == 'foreign_fence':
                        other = runtime/'orgs/foreign'
                        other.mkdir()
                        wrong = actual_acquire(other)
                        exclusive = None
                        try:
                            for selected_root in (root,):
                                try:
                                    OrgState.load(slug='scratch', root=selected_root, settings=Settings(), access_fence=wrong)
                                except ValueError as exc:
                                    assert str(exc) == 'org_access_attachment_fence_mismatch'
                                else:
                                    raise AssertionError('A4-D accepted foreign original fence')
                            wrong.assert_held()
                            route.close()
                            exclusive = actual_acquire(root, exclusive=True)
                            try:
                                OrgState.load(slug='scratch', root=root, settings=Settings(), access_fence=exclusive)
                            except ValueError as exc:
                                assert str(exc) == 'org_access_attachment_fence_mismatch'
                            else:
                                raise AssertionError('A4-D attached under EX')
                            exclusive.assert_held()
                        finally:
                            actual_close(wrong)
                            if exclusive is not None:
                                actual_close(exclusive)
                    else:
                        route.close()
                    assert route.closed, 'A4-D original discovery not closed/transferred'
                # All unknown fault cleanup here is test-owned, outside production
                # retry. Assertions above retain the uncertainty and real blocker.
                for conn in native:
                    conn.close()
                for record in route.fences:
                    if not record['closed'] and not record['transferred']:
                        actual_close(record['fence'])
                if blocker is not None:
                    actual_close(blocker)
                if fault not in ('ex_reselect', 'load'):
                    assert path.read_bytes() == original_bytes, 'A4-D discovery changed source bytes'
                print('PASS A4-D original discovery/window/custody:', fault, flush=True)
    if sys.argv[1:]:
        raise SystemExit(0)

# A4-S routes before constructor/source effects; presence alone is not validity.
# Native phase fixtures use the real publisher. They prove only routing evidence,
# not source installation/native closure, which remain the full coordinator's duty.
if sys.argv[1:] in ([], ['--bootstrap-selection']):
    from runtime.infrastructure import workflow_sequential_bootstrap as bootstrap
    from runtime.infrastructure import workflow_schema as schema
    from runtime.infrastructure.org_access_fence import OrgAccessFence
    from runtime.daemon import workflow_setup_status as setup_status
    from typing import get_args
    import re

    selection_cases = ('absent', 'present', 'partial', 'empty_namespace', 'registered', 'registered_absent',
                       'registered_identity', 'intent', 'backup_verified', 'pre_commit', 'committed',
                       'verified', 'not_installed', 'malformed', 'namespace_alias', 'source_init',
                       'connection_close', 'descriptor_close', 'busy', 'deadline', 'ex_reselect', 'reason_parity')
    for fault in selection_cases:
        if selection_case is not None and selection_case != fault:
            continue
        if fault == 'reason_parity':
            backend = set(get_args(setup_status.SetupReason))
            codes = {'default_load_budget_exceeded', 'default_load_qualification_unavailable',
                     'writer_exclusion_unproven', 'startup_upgrade_budget_unavailable'}
            text = Path('web/src/lib/api/types.ts').read_text().split('export interface WorkflowSetupStatus {', 1)[1]
            mirror = set(re.findall("'([^']+)'", text.split('  reason:', 1)[1].split(';', 1)[0]))
            assert codes <= backend and mirror == backend, 'A4-S closed hold-reason parity'
            for code in codes:
                assert setup_status.WorkflowSetupStatus(org_slug='scratch', reason=code).reason == code
            print('PASS A4-S read-only startup routing/closure:', fault, flush=True)
            continue
        with tempfile.TemporaryDirectory(prefix='a4-selection-') as scratch:
            private = Path(scratch).resolve()
            home, runtime = private/'home', private/'runtime'
            home.mkdir(mode=0o700)
            root = runtime/'orgs/scratch'
            root.mkdir(parents=True)
            path = root/'happyranch.db'
            installed_fixture = fault in ('present', 'registered', 'registered_identity')
            db = schema.initialize_fresh_org_database(path, expected_org_slug='scratch') if installed_fixture else Database(path)
            try:
                if not installed_fixture:
                    schema.initialize_complete_org_schema(db, expected_org_slug='scratch', layout='J')
                if fault == 'partial':
                    db._conn.execute('CREATE TABLE workflow_seq_partial(v TEXT)')
                    db._conn.commit()
                identity = schema._coding_org_identity(db._conn, expected_org_slug='scratch', expected_path=str(path))
                db._conn.execute('BEGIN')
                original_inventory = schema.sequential_storage_inventory(db._conn)
                db._conn.rollback()
            finally:
                db.close()
            with patch.dict(os.environ, {'HAPPYRANCH_DAEMON_HOME': str(home)}):
                phases = ('intent', 'backup_verified', 'pre_commit', 'committed', 'verified', 'registered')
                namespace = home/'backups/workflow-sequential'/hashlib.sha256(str(runtime).encode()).hexdigest()/'scratch'
                if fault in phases or fault in ('not_installed', 'registered_absent', 'registered_identity'):
                    ex = OrgAccessFence.acquire(root, exclusive=True)
                    original = json.dumps(original_inventory, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
                    recorded_identity = identity if fault != 'registered_identity' else (*identity[:2], identity[2]+1, identity[3])
                    attempt = bootstrap.BootstrapAttempt(home=home, runtime_root=runtime, slug='scratch', fence=ex,
                        source_identity=recorded_identity, source_inventory_digest=hashlib.sha256(original).hexdigest(),
                        deadline=time.monotonic()+119)
                    try:
                        attempt.begin()
                        if fault == 'not_installed':
                            attempt.publish('not_installed')
                        else:
                            last = 'registered' if fault in ('registered_absent', 'registered_identity') else fault
                            if last != 'intent':
                                # The independent status reader validates chain/file shape;
                                # it does not read/authorize using this synthetic backup.
                                attempt.backup_path.write_bytes(b'synthetic phase-fixture only')
                                attempt.backup_path.chmod(0o600)
                                digest = hashlib.sha256(attempt.backup_path.read_bytes()).hexdigest()
                                for phase in phases[1:phases.index(last)+1]:
                                    attempt.publish(phase, backup_sha256=digest if phase == 'backup_verified' else None)
                    finally:
                        attempt.close()
                        ex.close()
                elif fault in ('empty_namespace', 'malformed'):
                    namespace.mkdir(mode=0o700, parents=True)
                    if fault == 'malformed':
                        bad = namespace/'bad'
                        bad.mkdir(mode=0o700)
                        (bad/'00.json').write_bytes(b'{}')
                        (bad/'00.json').chmod(0o600)
                elif fault == 'namespace_alias':
                    other = private/'aliased-backups'
                    other.mkdir(mode=0o700)
                    (home/'backups').symlink_to(other, target_is_directory=True)
                locker = None
                if fault == 'busy':
                    locker = sqlite3.connect(path)
                    locker.execute('PRAGMA journal_mode=DELETE')
                    locker.execute('BEGIN EXCLUSIVE')
                source_bytes = path.read_bytes()
                fence = OrgAccessFence.acquire(root)
                selected = bootstrap.BootstrapSelection(home=home, fence=fence,
                    deadline=time.monotonic()+(-1 if fault == 'deadline' else 4))
                real_connect, real_close_fd = sqlite3.connect, os.close
                native, events, injected, close_calls, descriptor_calls, physical, timeouts = [], [], [], [], [], [], []
                root_inode = root.stat().st_ino
                class ObservedSelection(sqlite3.Connection):
                    def execute(self, sql, *args, **kwargs):
                        events.append(sql.split()[0].upper())
                        if fault == 'source_init' and sql == 'PRAGMA query_only=ON':
                            injected.append('source_init')
                            raise sqlite3.OperationalError('synthetic selector initialization')
                        return super().execute(sql, *args, **kwargs)
                    def close(self):
                        close_calls.append(self)
                        if fault == 'connection_close' and not injected:
                            injected.append('connection_close')
                            raise OSError('synthetic original selector close')
                        return super().close()
                def connected(target, *args, **kwargs):
                    assert str(target) == path.as_uri()+'?mode=ro', 'A4-S non-RO selector'
                    timeouts.append(kwargs.get('timeout'))
                    kwargs['factory'] = ObservedSelection
                    connection = real_connect(target, *args, **kwargs)
                    native.append(connection)
                    def traced(sql):
                        if re.search(r'(?i)\b(integrity_check|quick_check|checkpoint)\b', sql):
                            physical.append(sql.split()[0])
                    connection.set_trace_callback(traced)
                    return connection
                def closed_fd(fd):
                    if fault == 'descriptor_close' and selected.connections and os.fstat(fd).st_ino == root_inode:
                        descriptor_calls.append(fd)
                        if not injected:
                            injected.append('descriptor_close')
                            raise OSError('synthetic original selector descriptor close')
                    return real_close_fd(fd)
                started, error, result = time.monotonic(), None, None
                try:
                    with ExitStack() as stack:
                        stack.enter_context(patch.object(bootstrap.sqlite3, 'connect', side_effect=connected))
                        stack.enter_context(patch.object(bootstrap.os, 'close', side_effect=closed_fd))
                        forbidden = []
                        for owner, symbol in ((bootstrap, 'require_install_integrity'), (bootstrap, 'inspect_sequential_install'),
                                (bootstrap, 'sequential_storage_inventory'), (bootstrap.BootstrapAttempt, 'begin'),
                                (bootstrap.BootstrapSource, 'open'), (bootstrap.BootstrapSource, 'backup'),
                                (bootstrap.BootstrapRehearsal, 'run'), (bootstrap.BootstrapInstall, 'run'),
                                (Database, '__init__')):
                            forbidden.append(stack.enter_context(patch.object(owner, symbol,
                                side_effect=AssertionError('A4-S installer/source work in selector'))))
                        try:
                            result = selected.read()
                        except BaseException as exc:
                            error = exc
                        assert all(call.call_count == 0 for call in forbidden), 'A4-S installer/source work in selector'
                    assert not physical, 'A4-S physical/install work in selector'
                    passing = fault in ('absent', 'present', 'partial', 'empty_namespace', 'registered', 'ex_reselect')
                    if passing:
                        expected = 'present' if fault in ('present', 'partial', 'registered') else 'absent'
                        assert error is None and result == expected and selected.result == expected, 'A4-S wrong presence routing'
                        assert selected.closed and not selected.failed, 'A4-S success before positive closure'
                    else:
                        assert error is not None and result is None and selected.result is None, 'A4-S unproven selector escaped'
                    assert len(native) == len(selected.connections), 'A4-S lost partially constructed selector connection'
                    assert all(record['attempted'] for record in selected.connections+selected.descriptors), 'A4-S lost selector handle'
                    assert path.read_bytes() == source_bytes, 'A4-S changed original source bytes'
                    if fault in ('intent', 'backup_verified', 'pre_commit', 'committed', 'verified', 'not_installed', 'malformed', 'namespace_alias', 'deadline'):
                        assert not native, 'A4-S latched evidence/deadline opened source'
                    if fault == 'source_init':
                        assert injected == ['source_init'] and selected.closed and len(close_calls) == 1, 'A4-S original initialization closure'
                    if fault in ('connection_close', 'descriptor_close'):
                        assert injected == [fault] and not selected.closed and selected.close_error is not None, 'A4-S unknown closure lost'
                        counts = len(close_calls), len(descriptor_calls)
                        for _ in range(2):
                            try:
                                selected.close()
                            except bootstrap.BootstrapEvidenceError:
                                pass
                            else:
                                raise AssertionError('A4-S unknown close retried as success')
                        assert counts == (len(close_calls), len(descriptor_calls)), 'A4-S retried original unknown close'
                    if fault == 'busy':
                        assert isinstance(error, sqlite3.OperationalError) and time.monotonic()-started < .5, 'A4-S busy replenished wait'
                    assert all(timeout == 0 for timeout in timeouts), 'A4-S nonzero busy timeout'
                    if fault == 'registered':
                        try:
                            public = setup_status.read_setup_status(home, runtime, 'scratch').model_dump(mode='json')
                        except Exception as exc:
                            raise AssertionError('A4-S independent status projection changed') from exc
                        assert set(public) == {'format', 'org_slug', 'state', 'reason', 'attempt_time', 'backup_path',
                            'integrity_report'} and public['state'] == 'installed_verified', 'A4-S independent status projection changed'
                    if fault == 'ex_reselect':
                        fence.close()
                        fence = OrgAccessFence.acquire(root, exclusive=True)
                        with closing(real_connect(path)) as writer:
                            writer.execute('CREATE TABLE workflow_seq_arrival(v TEXT)')
                            writer.commit()
                        new = bootstrap.BootstrapSelection(home=home, fence=fence, deadline=time.monotonic()+4)
                        assert new.read() == 'present' and new.closed, 'A4-S reused stale SH absence under EX'
                    if fault != 'ex_reselect' and locker is None:
                        with closing(real_connect(path)) as check:
                            check.execute('BEGIN')
                            assert schema.sequential_storage_inventory(check) == original_inventory, 'A4-S changed original typed inventory'
                    before = len(native), len(events)
                    original_error = selected.primary
                    try:
                        selected.read()
                    except bootstrap.BootstrapEvidenceError:
                        pass
                    else:
                        raise AssertionError('A4-S replayed selector owner')
                    assert before == (len(native), len(events)) and selected.primary is original_error, 'A4-S replay lost original outcome'
                    print('PASS A4-S read-only startup routing/closure:', fault, flush=True)
                finally:
                    # Exact disposable native objects only; no production retry.
                    for connection in native:
                        sqlite3.Connection.close(connection)
                    for record in selected.descriptors:
                        if not record['closed']:
                            real_close_fd(record['fd'])
                    if locker is not None:
                        locker.close()
                    fence.close()
    if sys.argv[1:]:
        raise SystemExit(0)

# A4-W observes the actual original source writer, durable phases and independent
# reopened storage. Native faults and progress/phase observers remain test-side.
if sys.argv[1:] in ([], ['--bootstrap-install']):
    from runtime.infrastructure import workflow_sequential_bootstrap as bootstrap
    from runtime.infrastructure import workflow_schema as schema
    from runtime.infrastructure.org_access_fence import OrgAccessFence
    from runtime.infrastructure.workflow_integrity import InstallIntegrityError
    from runtime.daemon.routes.orgs import _seed_skeleton
    from runtime.orchestrator.org_config import initialize_fresh_reviewer_agents
    from runtime.workflows import sequential_history as history

    writer_cases = ('none', 'rehearsal', 'rehearsal_close', 'backup_changed', 'metadata_changed',
                    'space', 'temp', 'temp_alias', 'temp_mode', 'writer_source', 'target_integrity', 'target_budget',
                    'permit', 'reserve', 'commit', 'writer_close', 'reopen', 'preservation', 'checkpoint')
    for fault in writer_cases:
        if install_case is not None and install_case != fault:
            continue
        with tempfile.TemporaryDirectory(prefix='a4-source-install-') as scratch:
            private = Path(scratch).resolve()
            home, runtime = private/'home', private/'runtime'
            home.mkdir(mode=0o700)
            temporary = private/'sqlite-temp'
            temporary.mkdir(mode=0o700)
            root = runtime/'orgs/scratch'
            _seed_skeleton(root, from_example=None)
            path = root/'happyranch.db'
            db = Database(path)
            try:
                schema.initialize_complete_org_schema(db, expected_org_slug='scratch', layout='J')
                install_pause_schema(db, org_slug='scratch')
                initialize_fresh_reviewer_agents(OrgPaths(root), db)
                install_task_ownership(db, org_slug='scratch')
                from runtime.models import TaskStatus
                db.insert_task(TaskRecord(id='TASK-preserved', brief='synthetic retained task', status=TaskStatus.COMPLETED))
                db._conn.execute('CREATE TABLE a4_writer_sentinel(id INTEGER PRIMARY KEY,v BLOB)')
                db._conn.execute('INSERT INTO a4_writer_sentinel VALUES (37,?)', (b'original\0typed',))
                db._conn.commit()
            finally:
                db.close()
            def inventory():
                with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=0)) as conn:
                    conn.execute('PRAGMA query_only=ON')
                    conn.execute('BEGIN')
                    return schema.sequential_storage_inventory(conn)
            original = inventory()
            with patch.dict(os.environ, {'HAPPYRANCH_DAEMON_HOME': str(home), 'SQLITE_TMPDIR': str(temporary)}):
                fence = OrgAccessFence.acquire(root, exclusive=True)
                source = bootstrap.BootstrapSource(fence=fence, deadline=time.monotonic()+119)
                attempt = rehearsal = stage = None
                events, permits, injected, close_faults, commits = [], [], [], [], []
                backup_bytes = None
                actual_connection = bootstrap.BootstrapSource._connection
                actual_close = bootstrap.BootstrapSource._close_connection
                actual_checkpoint = bootstrap.BootstrapInstall._checkpoint
                actual_target = bootstrap.BootstrapInstall._default_target
                actual_integrity = schema.require_install_integrity
                actual_progress = history._ReadBudget.progress
                actual_load = OrgState.load
                current_default = [False]
                def observed_load(**kwargs):
                    events.append(('private_load', kwargs['root']))
                    return actual_load(**kwargs)
                def connected(owner, target, *, readonly):
                    conn = actual_connection(owner, target, readonly=readonly)
                    if target == path:
                        def traced(sql):
                            operation = sql.split()[0].upper()
                            events.append(('source_sql', operation))
                            if operation == 'COMMIT':
                                commits.append((attempt._phase, len(permits), time.monotonic() < source.deadline-20))
                        conn.set_trace_callback(traced)
                        def authorize(action, first, second, *_):
                            if (fault == 'commit' and action == sqlite3.SQLITE_TRANSACTION
                                    and first == 'COMMIT' and attempt is not None and attempt._phase == 'pre_commit'):
                                injected.append(fault)
                                return sqlite3.SQLITE_DENY
                            if (fault == 'reopen' and readonly and stage is not None and stage.committed
                                    and action == sqlite3.SQLITE_READ and first == 'workflow_seq_schema_versions'):
                                injected.append(fault)
                                return sqlite3.SQLITE_DENY
                            return sqlite3.SQLITE_OK
                        conn.set_authorizer(authorize)
                    return conn
                def integrity(conn, ledger, label):
                    events.append(('integrity', label))
                    main = conn.execute('PRAGMA database_list').fetchone()[2]
                    if main == str(path) and fault in ('writer_source', 'target_integrity') and label == (
                            'writer_source' if fault == 'writer_source' else 'target'):
                        injected.append(fault)
                        conn.set_authorizer(lambda action, a, b, *_: sqlite3.SQLITE_DENY
                            if action == sqlite3.SQLITE_PRAGMA and a.lower() == 'integrity_check' else sqlite3.SQLITE_OK)
                    return actual_integrity(conn, ledger, label)
                def progress(budget):
                    if fault == 'target_budget' and current_default[0] and budget.sql_limit == 32000000:
                        injected.append(fault)
                        budget.sql_steps = budget.sql_limit
                    return actual_progress(budget)
                def default_target(owner, conn):
                    events.append(('default_target', owner.committed))
                    current_default[0] = True
                    try:
                        return actual_target(owner, conn)
                    finally:
                        current_default[0] = False
                def closed(owner, conn):
                    if (fault == 'writer_close' and stage is not None and stage.committed
                            and conn is stage.writer and not close_faults):
                        record = next(row for row in owner.connections if row['connection'] is conn)
                        record['attempted'] = True
                        close_faults.append(conn)
                        injected.append(fault)
                        raise OSError('synthetic original source close uncertainty')
                    return actual_close(owner, conn)
                def checkpoint(owner, conn):
                    events.append(('checkpoint', owner.committed))
                    if not owner.committed:
                        record = json.loads((attempt.root/'01.json').read_bytes())
                        assert record['phase'] == 'backup_verified' and record['backup_sha256'] == hashlib.sha256(
                            attempt.backup_path.read_bytes()).hexdigest(), 'A4-W checkpoint before durable verified backup'
                        assert rehearsal.completed and rehearsal.closed, 'A4-W checkpoint before positive rehearsal closure'
                        try:
                            source.source.execute('SELECT 1')
                        except sqlite3.ProgrammingError:
                            pass
                        else:
                            raise AssertionError('A4-W checkpoint before original source reader closed')
                    if fault == 'checkpoint' and owner.committed:
                        injected.append(fault)
                        conn.set_authorizer(lambda action, a, b, *_: sqlite3.SQLITE_DENY
                            if action == sqlite3.SQLITE_PRAGMA and a.lower() == 'wal_checkpoint' else sqlite3.SQLITE_OK)
                    result = actual_checkpoint(owner, conn)
                    if fault == 'preservation' and owner.committed:
                        injected.append(fault)
                        conn.execute('UPDATE a4_writer_sentinel SET v=? WHERE id=37', (b'synthetic drift',))
                        conn.commit()
                    return result
                def permit(owner):
                    permits.append(owner.attempt_id)
                    assert owner is attempt and attempt._phase == 'pre_commit', 'A4-W permit before durable pre_commit'
                    record = json.loads((attempt.root/'02.json').read_bytes())
                    assert record['phase'] == 'pre_commit' and record['backup_sha256'] == source.backup_sha256
                    assert rehearsal.completed and rehearsal.closed, 'A4-W permit before positive rehearsal closure'
                    assert [item[0] for item in events].count('private_load') == 2, 'A4-W rehearsal after source COMMIT'
                    if fault == 'permit':
                        injected.append(fault)
                        return False
                    if fault == 'reserve':
                        injected.append(fault)
                        # Clock only moves AFTER the positive parent permission.
                        clock.return_value = source.deadline-19
                    return True
                failure = cleanup = None
                try:
                    with ExitStack() as stack:
                        stack.enter_context(patch.object(OrgState, 'load', side_effect=observed_load))
                        stack.enter_context(patch.object(bootstrap.BootstrapSource, '_connection', connected))
                        stack.enter_context(patch.object(bootstrap.BootstrapSource, '_close_connection', closed))
                        stack.enter_context(patch.object(bootstrap.BootstrapInstall, '_checkpoint', checkpoint))
                        stack.enter_context(patch.object(bootstrap.BootstrapInstall, '_default_target', default_target))
                        stack.enter_context(patch.object(schema, 'require_install_integrity', integrity))
                        stack.enter_context(patch.object(history._ReadBudget, 'progress', progress))
                        source.open()
                        attempt = bootstrap.BootstrapAttempt(home=home, runtime_root=runtime, slug='scratch',
                            fence=fence, source_identity=source.source_identity,
                            source_inventory_digest=source.inventory_digest, deadline=source.deadline)
                        attempt.begin()
                        bootstrap.require_bootstrap_space(source, attempt)
                        source.backup(attempt)
                        backup_bytes = attempt.backup_path.read_bytes()
                        rehearsal = bootstrap.BootstrapRehearsal(source, attempt)
                        rehearsal.run(Settings())
                        rehearsal.close()
                        stage = bootstrap.BootstrapInstall(source, attempt, rehearsal)
                        events.append(('writer_stage', None))
                        if fault == 'rehearsal':
                            rehearsal.completed = False
                            injected.append(fault)
                        elif fault == 'rehearsal_close':
                            rehearsal.closed = False
                            injected.append(fault)
                        elif fault == 'backup_changed':
                            attempt.backup_path.write_bytes(backup_bytes+b'synthetic drift')
                            injected.append(fault)
                        elif fault == 'metadata_changed':
                            (root/'org/config.yaml').write_text('name: synthetic changed metadata\n')
                            injected.append(fault)
                        elif fault == 'space':
                            # Each individual reservation fits; only the SUM
                            # on this shared volume refuses before source work.
                            free = 2*source.image_bytes+64*1048576+1024**3+sum(
                                value[5] for value in source.observation[1:] if value is not None)+4096
                            stack.enter_context(patch.object(bootstrap.os, 'fstatvfs', return_value=SimpleNamespace(f_bavail=free, f_frsize=1)))
                            injected.append(fault)
                        elif fault == 'temp':
                            stack.enter_context(patch.dict(os.environ, {'SQLITE_TMPDIR': ''}))
                            injected.append(fault)
                        elif fault == 'temp_alias':
                            alias = private/'sqlite-temp-alias'
                            alias.symlink_to(temporary, target_is_directory=True)
                            stack.enter_context(patch.dict(os.environ, {'SQLITE_TMPDIR': str(alias)}))
                            injected.append(fault)
                        elif fault == 'temp_mode':
                            temporary.chmod(0o500)
                            injected.append(fault)
                        clock = stack.enter_context(patch.object(bootstrap.time, 'monotonic', wraps=time.monotonic))
                        try:
                            stage.run(permit)
                        except BaseException as exc:
                            failure = exc
                        # The forward-clock test observes refusal first. Restore
                        # the clock before independent rollback/closure proof.
                        clock.side_effect = None
                        clock.return_value = DEFAULT
                        try:
                            stage.close()
                        except BaseException as exc:
                            cleanup = exc
                        assert (fault == 'none') == (failure is None), ('A4-W intended fault missed', fault, failure)
                        if fault != 'none':
                            assert injected, ('A4-W fault not reached', fault)
                        if fault in ('rehearsal', 'rehearsal_close'):
                            assert not stage.committed and not permits, 'A4-W admitted unclosed or incomplete rehearsal'
                            assert not any(item[0] == 'checkpoint' for item in events), 'A4-W source effects before positive rehearsal closure'
                        if fault in ('backup_changed', 'metadata_changed', 'space', 'temp', 'temp_alias', 'temp_mode'):
                            assert not any(item[0] == 'checkpoint' for item in events), 'A4-W source effects before input/volume qualification'
                            assert inventory() == original, 'A4-W changed unqualified source'
                        if fault in ('writer_source', 'target_integrity'):
                            assert isinstance(failure, InstallIntegrityError), ('A4-W replaced physical cause', type(failure))
                            assert failure.stage == ('writer_source' if fault == 'writer_source' else 'target')
                        if fault in ('target_budget', 'permit', 'reserve', 'writer_source', 'target_integrity'):
                            assert not stage.committed and stage.unchanged and cleanup is None, ('A4-W lost pre-COMMIT unchanged proof', fault, cleanup)
                            assert not commits, 'A4-W attempted COMMIT after pre-COMMIT refusal'
                            assert inventory() == original, 'A4-W changed original SQL/types/cells/rowids'
                            assert attempt._phase == 'not_installed', 'A4-W missing positively unchanged terminal event'
                        if fault == 'target_budget':
                            assert not permits, 'A4-W default target budget failure reached parent permit'
                            from runtime.workflows._read_exhaustion import exhausted
                            assert exhausted(failure), 'A4-W lost causal default target exhaustion'
                        if fault in ('permit', 'reserve'):
                            assert len(permits) == 1, 'A4-W repeated parent COMMIT request'
                            assert str(failure) == ('workflow_setup_commit_permit_refused' if fault == 'permit'
                                                    else 'workflow_setup_commit_reserve'), 'A4-W lost parent refusal/reserve cause'
                        if fault == 'commit':
                            assert stage.commit_uncertain and not stage.unchanged, 'A4-W COMMIT uncertainty became unchanged'
                            assert attempt._phase == 'pre_commit', 'A4-W retried or cleared uncertain COMMIT'
                        if fault in ('writer_close', 'reopen', 'preservation', 'checkpoint'):
                            assert stage.committed and not stage.verified and not stage.unchanged, 'A4-W post-COMMIT failure became unchanged/verified'
                            assert attempt._phase == 'committed', 'A4-W lost durable committed before fallible close/reopen'
                            with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=0)) as conn:
                                conn.execute('PRAGMA query_only=ON')
                                try:
                                    installed = conn.execute('SELECT version FROM workflow_seq_schema_versions').fetchall()
                                except sqlite3.DatabaseError:
                                    installed = None
                                assert installed == [(1,)], 'A4-W automatically restored a committed source'
                        if fault == 'writer_close':
                            assert cleanup is not None and len(close_faults) == 1, 'A4-W unknown original close retried'
                            assert close_faults[0].execute('SELECT 1').fetchone()[0] == 1, 'A4-W lost unclosed original writer'
                        if fault == 'none':
                            assert stage.verified and stage.closed and source.closed and attempt._phase == 'verified', 'A4-W target not independently verified/closed'
                            assert commits == [('pre_commit', 1, True)], 'A4-W COMMIT without durable phase/one permission/reserve'
                            assert len(permits) == 1 and [value for name, value in events if name == 'default_target'] == [False, True], 'A4-W missing actual default target/reopen gate'
                            assert [value for name, value in events if name == 'integrity'][-2:] == ['writer_source', 'target']
                            with closing(sqlite3.connect(path)) as conn:
                                assert conn.execute('SELECT id,typeof(v),hex(v) FROM a4_writer_sentinel').fetchall() == [(37, 'blob', '6F726967696E616C007479706564')]
                                assert conn.execute('SELECT count(*) FROM workflow_seq_definitions').fetchone()[0] == 0, 'A4-W registered before separate qualified owner'
                                assert conn.execute('SELECT count(*) FROM workflow_seq_settings').fetchone()[0] == 0, 'A4-W allocated settings/admission'
                        if fault != 'backup_changed':
                            assert attempt.backup_path.read_bytes() == backup_bytes, 'A4-W restored/changed original backup'
                        close_error, count = stage.close_error, len(close_faults)
                        before_close = len(events)
                        try:
                            stage.close()
                        except bootstrap.BootstrapEvidenceError:
                            assert not stage.closed, 'A4-W positive original close became unknown'
                        assert (stage.close_error is close_error and len(close_faults) == count
                                and len(events) == before_close), 'A4-W retried original close/proof or replaced original uncertainty'
                        before, primary, uncertainty = len(events), stage.primary, stage.commit_uncertain
                        try:
                            stage.run(permit)
                        except bootstrap.BootstrapEvidenceError:
                            pass
                        else:
                            raise AssertionError('A4-W replayed original writer')
                        assert len(events) == before, 'A4-W replay performed SQLite/checkpoint work'
                        assert stage.primary is primary and stage.commit_uncertain == uncertainty, 'A4-W replay replaced original failure/uncertainty'
                        print('PASS A4-W original writer/phase/default/preservation/closure:', fault, flush=True)
                finally:
                    # Exact synthetic handles only, AFTER all custody assertions.
                    # No product retry/restore or live DB is involved in teardown.
                    for record in source.connections:
                        record['connection'].close()
                    for record in source.descriptors:
                        if not record['attempted']:
                            os.close(record['fd'])
                            record['attempted'] = record['closed'] = True
                    if attempt is not None:
                        attempt.close()
                    if rehearsal is not None and not rehearsal.closed:
                        rehearsal.closed = True  # synthetic negative flag only; actual close already proved
                    fence.close()
    if sys.argv[1:]:
        raise SystemExit(0)

# A4-R extends the same private venue. Source/backup are genuine native images;
# only faults, clocks and observations are test-side. No successful owner is faked.
if sys.argv[1:] in ([], ['--bootstrap-rehearsal']):
    import shutil
    from runtime.infrastructure import workflow_sequential_bootstrap as bootstrap
    from runtime.infrastructure import workflow_schema as schema
    from runtime.infrastructure.workflow_integrity import InstallIntegrityError
    from runtime.infrastructure.org_access_fence import OrgAccessFence
    from runtime.daemon.routes.orgs import _seed_skeleton
    from runtime.orchestrator.org_config import initialize_fresh_reviewer_agents
    from runtime.workflows.sequential_history import _ReadBudget
    from runtime.workflows._read_exhaustion import exhausted
    from runtime.infrastructure import remote_job_schema
    from runtime.workflows.authority import WorkflowAuthorityCoordinator, WorkflowAuthorityError

    rehearsal_cases = ('none', 'first_budget', 'next_budget', 'install', 'metadata_alias',
                       'metadata_changed', 'backup_changed', 'space', 'scratch_location', 'deadline', 'load_init', 'authority', 'close')
    for fault in rehearsal_cases:
        if rehearsal_case is not None and rehearsal_case != fault:
            continue
        with tempfile.TemporaryDirectory(prefix='a4-writable-rehearsal-') as scratch:
            private = Path(scratch).resolve()
            home = private/'home'
            home.mkdir(mode=0o700)
            runtime = private/'runtime'
            root = runtime/'orgs/scratch'
            _seed_skeleton(root, from_example=None)
            path = root/'happyranch.db'
            db = Database(path)
            try:
                schema.initialize_complete_org_schema(db, expected_org_slug='scratch', layout='J')
                install_pause_schema(db, org_slug='scratch')
                initialize_fresh_reviewer_agents(OrgPaths(root), db)
                install_task_ownership(db, org_slug='scratch')
                db._conn.execute('CREATE TABLE a4_rehearsal_sentinel(id INTEGER PRIMARY KEY,v BLOB)')
                db._conn.execute('INSERT INTO a4_rehearsal_sentinel VALUES (37,?)', (b'original\0typed',))
                db._conn.commit()
            finally:
                db.close()
            with patch.dict(os.environ, {'HAPPYRANCH_DAEMON_HOME': str(home)}):
                fence = OrgAccessFence.acquire(root, exclusive=True)
                source = bootstrap.BootstrapSource(fence=fence, deadline=time.monotonic()+119)
                attempt = rehearsal = None
                actual_load, actual_close = OrgState.load, OrgState.close
                actual_install = schema.install_sequential_schema
                actual_progress = _ReadBudget.progress
                load_calls, install_calls, budget_calls, injections, close_calls = [], [], [], [], []
                current = [None]
                authority_error = WorkflowAuthorityError('authority_pointer_not_ready')
                construction_error = ValueError('synthetic private constructor refusal')
                def current_inventory():
                    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as conn:
                        conn.execute('PRAGMA query_only=ON')
                        conn.execute('BEGIN')
                        return schema.sequential_storage_inventory(conn)
                original = current_inventory()
                def observed_install(conn, **kwargs):
                    assert kwargs['allowance'] is source.allowance and kwargs['integrity'] is source.integrity, 'A4-R replenished install ledger'
                    assert kwargs['expected_path'] != str(path), 'A4-R wrote original source'
                    assert kwargs['source_inventory'] == original
                    install_calls.append(kwargs['expected_path'])
                    if fault == 'install':
                        injections.append(fault)
                        # Real writer-source integrity refusal on this native writer.
                        conn.set_authorizer(lambda action, a, b, *_: sqlite3.SQLITE_DENY
                            if action == sqlite3.SQLITE_PRAGMA and a.lower() == 'integrity_check' else sqlite3.SQLITE_OK)
                    return actual_install(conn, **kwargs)
                def observed_progress(budget):
                    if current[0] is not None:
                        budget_calls.append((current[0], budget.sql_limit))
                        if (fault == current[0]+'_budget' and budget.sql_limit == 32000000):
                            injections.append(fault)
                            # Consume the original native progress allowance;
                            # the production hook raises its own causal refusal.
                            budget.sql_steps = budget.sql_limit
                    return actual_progress(budget)
                def observed_close(org):
                    close_calls.append(org)
                    if fault == 'close':
                        injections.append(fault)
                        raise OSError('synthetic original private close uncertainty')
                    return actual_close(org)
                def observed_load(**kwargs):
                    assert 'ownership_sql_limit' not in kwargs and 'deadline' not in kwargs, 'A4-R nondefault writable load'
                    private_path = kwargs['root']/'happyranch.db'
                    with closing(sqlite3.connect(private_path)) as probe:
                        assert probe.execute("SELECT version FROM workflow_seq_schema_versions").fetchone()[0] == 1, 'A4-R load before private candidate install'
                        value = probe.execute('SELECT v FROM a4_rehearsal_sentinel WHERE id=37').fetchone()[0]
                    index = len(load_calls)
                    assert value == (b'original\0typed' if index == 0 else b'writable-FIRST-survives-NEXT'), 'A4-R NEXT replaced the FIRST image'
                    identity = private_path.stat()
                    load_calls.append((str(private_path), identity.st_dev, identity.st_ino))
                    current[0] = 'first' if index == 0 else 'next'
                    try:
                        with ExitStack() as faults:
                            if fault == 'load_init':
                                injections.append(fault)
                                faults.enter_context(patch.object(remote_job_schema,
                                    'validate_identity_enrollment_schema_preflight', side_effect=construction_error))
                            if fault == 'authority':
                                injections.append(fault)
                                faults.enter_context(patch.object(WorkflowAuthorityCoordinator,
                                    'recover_or_publish', side_effect=authority_error))
                            org = actual_load(**kwargs)
                        assert org.db._conn.execute('PRAGMA query_only').fetchone()[0] == 0, 'A4-R used read-only capacity instead of writable load'
                        # A real first-load transaction is independently visible
                        # to NEXT on the very same candidate. Source stays exact.
                        org.db._conn.execute('UPDATE a4_rehearsal_sentinel SET v=? WHERE id=37', (b'writable-FIRST-survives-NEXT',))
                        org.db._conn.commit()
                        if fault == 'metadata_changed':
                            injections.append(fault)
                            (root/'org/teams.yaml').write_text('teams: {}\n# source metadata changed during private load\n')
                        return org
                    finally:
                        current[0] = None
                try:
                    source.open()
                    attempt = bootstrap.BootstrapAttempt(home=home, runtime_root=runtime, slug='scratch',
                        fence=fence, source_identity=source.source_identity,
                        source_inventory_digest=source.inventory_digest, deadline=source.deadline)
                    attempt.begin()
                    source.backup(attempt)
                    before = hashlib.sha256(path.read_bytes()).hexdigest()
                    backup_before = hashlib.sha256(attempt.backup_path.read_bytes()).hexdigest()
                    rehearsal = bootstrap.BootstrapRehearsal(source, attempt)
                    if fault == 'metadata_alias':
                        (root/'org/alias.yaml').symlink_to(root/'org/teams.yaml')
                        injections.append(fault)
                    if fault == 'backup_changed':
                        with closing(sqlite3.connect(attempt.backup_path)) as conn:
                            conn.execute('UPDATE a4_rehearsal_sentinel SET v=? WHERE id=37', (b'wrong-unverified-image',))
                            conn.commit()
                        injections.append(fault)
                    error = None
                    with ExitStack() as faults:
                        faults.enter_context(patch.object(schema, 'install_sequential_schema', side_effect=observed_install))
                        faults.enter_context(patch.object(OrgState, 'load', side_effect=observed_load))
                        faults.enter_context(patch.object(OrgState, 'close', observed_close))
                        faults.enter_context(patch.object(_ReadBudget, 'progress', observed_progress))
                        if fault == 'space':
                            injections.append(fault)
                            faults.enter_context(patch.object(bootstrap.os, 'fstatvfs', return_value=SimpleNamespace(f_bavail=0, f_frsize=4096)))
                        if fault == 'scratch_location':
                            injections.append(fault)
                            faults.enter_context(patch.object(bootstrap.tempfile, 'tempdir', str(runtime)))
                        if fault == 'deadline':
                            injections.append(fault)
                            faults.enter_context(patch.object(bootstrap.time, 'monotonic', return_value=source.deadline-19))
                        try:
                            receipt = rehearsal.run(Settings())
                        except BaseException as exc:
                            error = exc
                        if fault == 'none':
                            assert error is None, ('A4-R unexpected private rehearsal failure', type(error).__name__, error)
                            assert len(load_calls) == 2 and load_calls[0] == load_calls[1], 'A4-R did not run writable FIRST/NEXT on the same image'
                            assert len(install_calls) == 1 and all(v == load_calls[0][0] for v in install_calls), 'A4-R repeated private installation'
                            assert {'first', 'next'} == {v[0] for v in budget_calls} and {32000000, 2000000} <= {v[1] for v in budget_calls}, 'A4-R skipped default complete budgets'
                            assert rehearsal.completed and receipt['backup_sha256'] == backup_before and receipt['source_inventory_digest'] == source.inventory_digest
                        else:
                            assert error is not None and injections, ('A4-R intended fault missed', fault)
                            assert rehearsal.failed and not rehearsal.completed, 'A4-R failed rehearsal accepted or not latched'
                            if fault in ('first_budget', 'next_budget'):
                                assert exhausted(error), 'A4-R exhaustion lost original causal dimension'
                                assert len(load_calls) == (1 if fault == 'first_budget' else 2), 'A4-R wrong default load exhausted'
                            elif fault == 'metadata_changed':
                                assert str(error) == 'workflow_setup_rehearsal_input_changed', 'A4-R source metadata drift accepted'
                            elif fault == 'backup_changed':
                                assert not install_calls and str(error) == 'workflow_setup_rehearsal_input_changed', 'A4-R unverified candidate installed'
                            elif fault == 'metadata_alias':
                                assert not install_calls and str(error) == 'workflow_setup_file_unsafe', 'A4-R copied aliased metadata'
                            elif fault == 'install':
                                assert not load_calls and isinstance(error, InstallIntegrityError) and error.stage == 'writer_source', 'A4-R wrong private integrity boundary'
                            elif fault == 'space':
                                assert not install_calls and str(error) == 'workflow_setup_rehearsal_space_unavailable', 'A4-R ignored rehearsal headroom'
                            elif fault == 'deadline':
                                assert not install_calls and rehearsal.root is None, 'A4-R used reserved COMMIT time for rehearsal'
                            elif fault == 'scratch_location':
                                assert not install_calls and rehearsal.root is None and str(error) == 'workflow_setup_rehearsal_location_unsafe', 'A4-R created scratch in production storage'
                            elif fault == 'load_init':
                                assert error is construction_error and len(rehearsal.attachments) == 1, 'A4-R lost private construction failure'
                                owner = rehearsal.attachments[0]
                                assert owner['load_db_closed'] and owner['db_construction_started'], 'A4-R private constructor did not positively close'
                                try:
                                    owner['load_db']._conn.execute('SELECT 1')
                                except sqlite3.ProgrammingError:
                                    pass
                                else:
                                    raise AssertionError('A4-R positive constructor close left usable native handle')
                            elif fault == 'authority':
                                assert error is authority_error and not exhausted(error), 'A4-R replaced original authority refusal with size or generic failure'
                        # Successful and failed rehearsals are never replayed.
                        try:
                            rehearsal.run(Settings())
                        except bootstrap.BootstrapEvidenceError:
                            pass
                        else:
                            raise AssertionError('A4-R repeated original rehearsal')
                        scratch_root = rehearsal.root
                        close_error = None
                        try:
                            rehearsal.close()
                        except BaseException as exc:
                            close_error = exc
                        if fault == 'close':
                            assert close_error is not None and scratch_root.exists(), 'A4-R deleted unknown original private owner'
                            assert len(rehearsal.attachments) == 1 and rehearsal.attachments[0]['returned'] is close_calls[0], 'A4-R lost original private attachment'
                            assert close_calls[0].db._conn.execute('SELECT 1').fetchone()[0] == 1
                            try:
                                rehearsal.close()
                            except bootstrap.BootstrapEvidenceError:
                                pass
                            else:
                                raise AssertionError('A4-R invented unknown private closure')
                            assert len(close_calls) == 1, 'A4-R retried unknown private close'
                        else:
                            assert close_error is None and rehearsal.closed, ('A4-R private close failed', fault, close_error)
                            assert scratch_root is None or not scratch_root.exists(), 'A4-R positively closed scratch retained'
                    assert current_inventory() == original and hashlib.sha256(path.read_bytes()).hexdigest() == before, 'A4-R changed or auto-restored source before qualification'
                    if fault != 'backup_changed':
                        assert hashlib.sha256(attempt.backup_path.read_bytes()).hexdigest() == backup_before, 'A4-R modified verified backup'
                    assert attempt._phase == 'backup_verified' and not (attempt.root/'02.json').exists(), 'A4-R advanced source COMMIT before qualification'
                    print('PASS A4-R actual writable same-image FIRST/NEXT/default budgets/source preservation/original custody:', fault, flush=True)
                finally:
                    if rehearsal is not None:
                        # Test-side rescue only AFTER retained unknown/no-retry
                        # assertions; never production clearance or replay.
                        rescued = set()
                        for org in [owner.get('returned') for owner in rehearsal.attachments] + close_calls:
                            if org is not None and id(org) not in rescued and not org._local_close_complete:
                                actual_close(org)
                                rescued.add(id(org))
                        if rehearsal.root is not None and rehearsal.root.exists():
                            rehearsal.close() if not rehearsal.failed else shutil.rmtree(rehearsal.root)
                    source.close()
                    if attempt is not None:
                        attempt.close()
                    fence.close()
    if sys.argv[1:] == ['--bootstrap-rehearsal']:
        raise SystemExit(0)

# I1a extends this existing disposable venue, with no new execution lane.
if sys.argv[1:] in ([], ['--install-integrity'], ['--bootstrap-backup'], ['--bootstrap-precommit'], ['--bootstrap-journal']):
    from scripts import install_workflow_sequential_schema as installer
    from runtime.infrastructure import workflow_schema as schema
    from runtime.infrastructure import workflow_integrity as physical

    @contextmanager
    def install_integrity_fixture(*, idle: bool = False):
        with tempfile.TemporaryDirectory(prefix='workflow-install-integrity-') as scratch:
            root = Path(scratch)
            runtime = root / 'runtime'
            org = runtime / 'orgs/scratch'
            (org / 'org').mkdir(parents=True)
            (runtime / 'happyranch.yaml').write_text('schema_version: 2\ntype: multi-org-runtime\n')
            (org / 'org/teams.yaml').write_text('teams: {}\n')
            home = root / 'daemon-home'
            home.mkdir()
            (home / 'runtimes.yaml').write_text(json.dumps({'active': str(runtime), 'registered': [str(runtime)]}))
            path = org / 'happyranch.db'
            db = Database(path)
            try:
                schema.initialize_complete_org_schema(db, expected_org_slug='scratch', layout='J')
                install_pause_schema(db, org_slug='scratch')
                install_task_ownership(db, org_slug='scratch')
                from runtime.models import TaskStatus
                db.insert_task(TaskRecord(id='TASK-integrity', brief='private preservation sentinel',
                    status=TaskStatus.COMPLETED if idle else TaskStatus.PENDING))
            finally:
                db.close()
            backup = root / 'verified-backup.db'
            # Genuine SQLite backup, checkpointed and closed before the CLI.
            with sqlite3.connect(path) as source, sqlite3.connect(backup) as target:
                source.backup(target)
            source.close()
            target.close()
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            backup_hash = hashlib.sha256(backup.read_bytes()).hexdigest()
            def inventory():
                with sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True) as conn:
                    conn.execute('PRAGMA query_only=ON')
                    conn.execute('BEGIN')
                    result = schema.sequential_storage_inventory(conn)
                conn.close()
                return result
            original = inventory()
            with patch.dict(os.environ, {'HAPPYRANCH_DAEMON_HOME': str(home)}):
                yield (path, inventory, original, before, backup, backup_hash,
                       ['--runtime-root', str(runtime), '--org', 'scratch',
                        '--confirm-cooperative-offline', '--backup', str(backup),
                        '--backup-sha256', backup_hash])

    # A4-B owns the actual source/backup snapshot and pre-checkpoint gate.
    # It does not claim original-host qualification or an installed outcome.
    if sys.argv[1:] in ([], ['--bootstrap-backup']):
        from runtime.infrastructure import workflow_sequential_bootstrap as bootstrap
        from runtime.infrastructure.org_access_fence import OrgAccessFence
        from runtime.daemon.workflow_setup_status import read_setup_status
        from runtime.runtime import daemon_home

        backup_cases = ('none', 'wal', 'checkpointed_wal', 'source_init', 'destination_init', 'source_integrity',
                        'backup_integrity', 'inventory', 'file_sync', 'directory_sync',
                        'space', 'deadline', 'close', 'alias', 'shared_space', 'shared_exact',
                        'shared_minimum', 'database_space', 'temp_space', 'backup_space',
                        'rehearsal_space', 'separate_exact', 'volume_io')
        for fault in backup_cases:
            if backup_case is not None and fault != backup_case:
                continue
            with install_integrity_fixture(idle=True) as fixture:
                path, _, _, _, _, _, _ = fixture
                native_connect = sqlite3.connect
                keeper = native_connect(path)
                keeper.execute('CREATE TABLE a4_native_preservation(id INTEGER PRIMARY KEY,i INTEGER,t TEXT,b BLOB,r REAL,n)')
                keeper.execute('INSERT INTO a4_native_preservation VALUES(77,42,?,?,0.5,NULL)', ('typed\0é', b'\0blob'))
                keeper.commit()
                if fault in ('wal', 'checkpointed_wal'):
                    assert keeper.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
                    keeper.execute('PRAGMA wal_autocheckpoint=0')
                    keeper.execute("UPDATE a4_native_preservation SET t='committed WAL sentinel' WHERE id=77")
                    keeper.commit()
                    assert path.with_name(path.name+'-wal').stat().st_size > 0
                    if fault == 'checkpointed_wal':
                        keeper.close()
                        keeper = None
                        assert not any(Path(str(path)+suffix).exists() for suffix in ('-wal', '-shm'))
                else:
                    assert keeper.execute('PRAGMA journal_mode=DELETE').fetchone()[0] == 'delete'
                    keeper.close()
                    keeper = None
                def current_inventory():
                    # The fixture alone knows its positively closed WAL image
                    # is checkpointed; preserve companion absence until the
                    # production native snapshot admission being exercised.
                    uri = path.as_uri()+'?mode=ro'+('&immutable=1' if fault == 'checkpointed_wal' else '')
                    with closing(native_connect(uri, uri=True, timeout=0)) as c:
                        c.execute('PRAGMA query_only=ON')
                        c.execute('BEGIN')
                        return schema.sequential_storage_inventory(c)
                original = current_inventory()
                observed = {name: hashlib.sha256(Path(str(path)+name).read_bytes()).hexdigest()
                            for name in ('', '-wal') if Path(str(path)+name).exists()}
                statements, events, stages, created, injections = [], [], [], [], []
                attempt = None
                with closing(OrgAccessFence.acquire(path.parent, exclusive=True)) as fence:
                    deadline = time.monotonic()+110
                    owner = bootstrap.BootstrapSource(fence=fence, deadline=deadline)
                    gate = bootstrap.require_install_integrity
                    close_connection = bootstrap.BootstrapSource._close_connection
                    sync = os.fsync
                    close_fd = os.close
                    space_gate = bootstrap.require_bootstrap_space
                    native_directory, native_stat = bootstrap._directory, os.fstat
                    native_space = os.fstatvfs
                    primary = OSError('synthetic private backup close failure')
                    def connect(filename, *args, **kwargs):
                        text = str(filename)
                        conn = native_connect(filename, *args, **kwargs)
                        if text.startswith(path.as_uri()+'?') or '/before.db?' in text:
                            created.append(conn)
                        if text.startswith(path.as_uri()+'?'):
                            conn.set_trace_callback(statements.append)
                        if text.endswith('/before.db?mode=ro'):
                            events.append('independent-backup-open')
                        if (fault == 'source_init' and text.startswith(path.as_uri()+'?')
                                or fault == 'destination_init' and text.endswith('/before.db?mode=rw')):
                            conn.set_authorizer(lambda op, one, two, db, trigger:
                                sqlite3.SQLITE_DENY if op == sqlite3.SQLITE_PRAGMA and one == 'foreign_keys'
                                else sqlite3.SQLITE_OK)
                            injections.append(fault)
                        return conn
                    def physical_gate(conn, ledger, stage):
                        start = ledger.consumed
                        assert conn.in_transaction and conn.execute('PRAGMA query_only').fetchone()[0] == 1
                        if stage == 'backup':
                            events.append('independent-backup-integrity')
                            assert Path(conn.execute('PRAGMA database_list').fetchone()[2]) == attempt.backup_path
                        if fault == stage+'_integrity':
                            conn.set_authorizer(lambda op, one, two, db, trigger:
                                sqlite3.SQLITE_DENY if op == sqlite3.SQLITE_PRAGMA and one == 'integrity_check'
                                else sqlite3.SQLITE_OK)
                            injections.append(fault)
                        try:
                            gate(conn, ledger, stage)
                        finally:
                            stages.append((stage, id(ledger), start, ledger.consumed))
                            conn.set_authorizer(None)
                    def owned_close(current, conn):
                        record = next(r for r in current.connections if r['connection'] is conn)
                        name = Path(conn.execute('PRAGMA database_list').fetchone()[2]).name if not record['attempted'] else None
                        if fault == 'close' and name == 'before.db' and not record['attempted']:
                            record['attempted'], record['error'] = True, primary
                            injections.append('close')
                            raise primary
                        close_connection(current, conn)
                        if name == 'before.db':
                            events.append('backup-connection-closed')
                            if fault == 'inventory' and 'inventory' not in injections:
                                # A valid but wrong native image BEFORE hashing:
                                # only independent exact inventory can catch it.
                                with closing(native_connect(attempt.backup_path)) as changed:
                                    changed.execute("UPDATE a4_native_preservation SET i=43 WHERE id=77")
                                    changed.commit()
                                injections.append('inventory')
                    def fsync(fd):
                        info = os.fstat(fd)
                        if attempt is not None and attempt.backup_path.exists():
                            backup_inode = attempt.backup_path.stat().st_ino
                            if info.st_ino == backup_inode:
                                events.append('backup-file-fsync')
                                if fault == 'file_sync':
                                    injections.append(fault)
                                    raise OSError('synthetic private file sync failure')
                            elif fd == attempt._parent:
                                events.append('backup-directory-fsync')
                                if fault == 'directory_sync':
                                    injections.append(fault)
                                    raise OSError('synthetic private directory sync failure')
                            else:
                                events.append('phase-file-fsync')
                        return sync(fd)
                    def descriptor_close(fd):
                        info = os.fstat(fd)
                        if attempt is not None and attempt.backup_path.exists() and info.st_ino == attempt.backup_path.stat().st_ino:
                            events.append('backup-descriptor-close')
                        return close_fd(fd)
                    def volume_gate(source, current):
                        # Observe the ORIGINAL policy at its native filesystem
                        # boundary. Only volume readings are synthetic; all
                        # original path/owner/deadline/collection logic executes.
                        events.append('combined-volume-admission')
                        assert current is attempt and source is owner
                        assert (not attempt.backup_path.exists()
                                or fault == 'alias' and attempt.backup_path.is_symlink()), 'A4-B volume qualification after backup creation'
                        database = 2*owner.image_bytes+64*1048576+1024**3+sum(
                            value[5] for value in owner.observation[1:] if value is not None)
                        required = dict(database=database, temp=database,
                            backup=owner.image_bytes+1024**3, rehearsal=owner.image_bytes+64*1048576)
                        shared = database+required['backup']+required['rehearsal']
                        paths = {path.parent: 'database', Path(os.environ['SQLITE_TMPDIR']): 'temp',
                                 attempt.root: 'backup', Path(tempfile.gettempdir()).resolve(): 'rehearsal'}
                        identities = {(target.stat().st_dev, target.stat().st_ino): category
                                      for target, category in paths.items()}
                        directories = {}
                        def directory(target, custody):
                            record = native_directory(target, custody)
                            directories[record['fd']] = paths[target]
                            return record
                        def observed_stat(fd):
                            info = native_stat(fd)
                            category = identities.get((info.st_dev, info.st_ino))
                            if category is not None and fault in (
                                    'database_space', 'temp_space', 'backup_space',
                                    'rehearsal_space', 'separate_exact'):
                                values = list(info)
                                values[2] = 100+tuple(required).index(category)
                                return os.stat_result(values)
                            return info
                        def free_space(fd):
                            category = directories[fd]
                            if fault == 'volume_io':
                                injections.append(fault)
                                raise OSError('synthetic unavailable volume observation')
                            if fault == 'shared_space':
                                free = max(required.values())+4096
                            elif fault == 'shared_exact':
                                free = shared
                            elif fault == 'shared_minimum':
                                free = database-1 if category == 'temp' else shared+4096
                            elif fault in ('database_space', 'temp_space', 'backup_space', 'rehearsal_space'):
                                free = required[category]-(category == fault.removesuffix('_space'))
                            elif fault == 'separate_exact':
                                free = required[category]
                            else:
                                return native_space(fd)
                            injections.append(fault)
                            return SimpleNamespace(f_bavail=free, f_frsize=1)
                        with ExitStack() as volume:
                            volume.enter_context(patch.object(bootstrap, '_directory', directory))
                            volume.enter_context(patch.object(bootstrap.os, 'fstat', observed_stat))
                            volume.enter_context(patch.object(bootstrap.os, 'fstatvfs', free_space))
                            return space_gate(source, current)
                    error = None
                    with ExitStack() as stack:
                        stack.enter_context(patch.object(bootstrap.sqlite3, 'connect', connect))
                        stack.enter_context(patch.object(bootstrap, 'require_install_integrity', physical_gate))
                        stack.enter_context(patch.object(bootstrap.BootstrapSource, '_close_connection', owned_close))
                        stack.enter_context(patch.object(bootstrap.os, 'fsync', fsync))
                        stack.enter_context(patch.object(bootstrap.os, 'close', descriptor_close))
                        stack.enter_context(patch.object(bootstrap, 'require_bootstrap_space', volume_gate))
                        try:
                            owner.open()
                            attempt = bootstrap.BootstrapAttempt(home=daemon_home(), runtime_root=path.parents[2],
                                slug='scratch', fence=fence, source_identity=owner.source_identity,
                                source_inventory_digest=owner.inventory_digest, deadline=deadline)
                            attempt.begin()
                            if fault == 'alias':
                                attempt.backup_path.symlink_to(path)
                                injections.append(fault)
                            if fault == 'space':
                                stack.enter_context(patch.object(bootstrap.os, 'fstatvfs',
                                    return_value=SimpleNamespace(f_bavail=0, f_frsize=4096)))
                                injections.append(fault)
                            if fault == 'deadline':
                                stack.enter_context(patch.object(bootstrap.time, 'monotonic', return_value=deadline-19))
                                injections.append(fault)
                            owner.backup(attempt)
                        except (ValueError, sqlite3.Error, OSError) as exc:
                            error = exc
                        # Drop clock/space faults before independent cleanup.
                    if fault in ('none', 'wal', 'checkpointed_wal', 'shared_exact', 'separate_exact'):
                        assert error is None, ('A4-B unexpected backup failure', fault, error)
                        assert events.count('combined-volume-admission') == 1, 'A4-B omitted before-backup volume qualification'
                        # Observe committed WAL content independently before
                        # URI-sensitive durability instrumentation can fail.
                        if fault in ('wal', 'checkpointed_wal'):
                            with closing(native_connect(attempt.backup_path.as_uri()+'?mode=ro', uri=True)) as b:
                                assert b.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='a4_native_preservation'").fetchone() is not None, 'A4-B lost committed WAL data'
                                value = b.execute('SELECT t FROM a4_native_preservation WHERE id=77').fetchone()
                                assert value is not None and value[0] == 'committed WAL sentinel', 'A4-B lost committed WAL data'
                        assert [s[0] for s in stages] == ['source', 'backup'], 'A4-B mandatory backup physical gate omitted'
                        assert len({s[1] for s in stages}) == 1 and stages[1][2] == stages[0][3] > 0, 'A4-B physical allowance replenished'
                        assert owner.backup_sha256 == hashlib.sha256(attempt.backup_path.read_bytes()).hexdigest()
                        assert os.stat(attempt.backup_path).st_ino != path.stat().st_ino
                        assert stat.S_IMODE(attempt.backup_path.stat().st_mode) == 0o600
                        ordered = ('backup-file-fsync', 'backup-descriptor-close', 'independent-backup-open',
                                   'independent-backup-integrity', 'backup-connection-closed',
                                   'backup-directory-fsync', 'phase-file-fsync')
                        # The destination close precedes the first fsync; the
                        # next close after the independent read is required here.
                        try:
                            cursor = events.index('backup-file-fsync')
                            for event in ordered[1:]:
                                cursor = events.index(event, cursor+1)
                        except ValueError as exc:
                            raise AssertionError('A4-B backup durability order missing') from exc
                        assert events[-1] == 'backup-directory-fsync', 'A4-B backup_verified directory not fsynced'
                        assert (attempt.root/'01.json').is_file(), 'A4-B verified phase missing'
                        status = read_setup_status(daemon_home(), path.parents[2], 'scratch')
                        assert status.state == 'unknown' and status.reason == 'install_incomplete', 'A4-B backup claimed installation'
                    else:
                        if fault in ('shared_space', 'shared_minimum', 'database_space', 'temp_space',
                                     'backup_space', 'rehearsal_space', 'volume_io'):
                            assert error is not None, 'A4-B accepted insufficient or unavailable combined volume'
                        assert error is not None and injections, ('A4-B intended fault missed', fault, error)
                        assert owner.failed, 'A4-B failed source owner not latched'
                        if fault in ('shared_space', 'shared_minimum', 'database_space', 'temp_space',
                                     'backup_space', 'rehearsal_space', 'volume_io'):
                            if fault == 'volume_io':
                                assert isinstance(error, OSError), 'A4-B replaced volume IO cause'
                            else:
                                assert str(error) == 'workflow_setup_volume_space_unavailable', 'A4-B accepted insufficient combined volume'
                            assert events.count('combined-volume-admission') == 1, 'A4-B omitted before-backup volume qualification'
                            assert not attempt.backup_path.exists(), 'A4-B backup created before complete volume qualification'
                            assert len(owner.connections) == 1, 'A4-B opened backup before complete volume qualification'
                        if fault.endswith('_init'):
                            assert all(any(r['connection'] is conn for r in owner.connections) for conn in created), 'A4-B lost partially initialized native connection'
                        if fault in ('source_integrity', 'backup_integrity'):
                            assert isinstance(error, physical.InstallIntegrityError) and error.stage == fault.split('_')[0], ('A4-B wrong integrity boundary', error)
                        if fault == 'inventory':
                            assert str(error) == 'workflow_setup_backup_inventory_changed', ('A4-B wrong preservation boundary', error)
                        if attempt is not None:
                            assert not (attempt.root/'01.json').exists(), 'A4-B unverified backup published'
                            try:
                                owner.backup(attempt)
                            except bootstrap.BootstrapEvidenceError as exc:
                                assert str(exc) == 'workflow_setup_source_latched', 'A4-B failed source retried'
                            else:
                                raise AssertionError('A4-B failed source replayed backup')
                    retained_backup = None
                    if attempt is not None and attempt.backup_path.is_file() and not attempt.backup_path.is_symlink():
                        retained_backup = hashlib.sha256(attempt.backup_path.read_bytes()).hexdigest()
                    try:
                        owner.close()
                    except bootstrap.BootstrapEvidenceError as exc:
                        assert fault == 'close' and str(exc) == 'workflow_setup_source_close_unknown'
                        held = [r for r in owner.connections if r.get('error') is primary]
                        assert len(held) == 1 and not held[0]['closed'], 'A4-B lost unknown original connection'
                        assert held[0]['connection'].execute('SELECT 1').fetchone()[0] == 1
                        try:
                            owner.close()
                        except bootstrap.BootstrapEvidenceError:
                            pass
                        else:
                            raise AssertionError('A4-B invented original close after unknown')
                        # Test-only final disposal after observing retained custody.
                        held[0]['connection'].close()
                    else:
                        assert fault != 'close' and owner.closed and all(r['closed'] for r in owner.connections), 'A4-B invented successful close'
                        for record in owner.connections:
                            try:
                                record['connection'].execute('SELECT 1')
                            except sqlite3.ProgrammingError:
                                pass
                            else:
                                raise AssertionError('A4-B positive close left native handle usable')
                    assert all(r['attempted'] for r in owner.connections), 'A4-B unattempted original close'
                    if attempt is not None:
                        attempt.close()
                    assert current_inventory() == original, 'A4-B source typed cells/rowids/SQL changed or auto-restored'
                    assert not any(s.upper().startswith(('COMMIT', 'INSERT ', 'UPDATE ', 'DELETE ', 'CREATE ', 'DROP ', 'PRAGMA WAL_CHECKPOINT'))
                                   for s in statements), 'A4-B source effects before independently verified backup'
                    assert observed == {name: hashlib.sha256(Path(str(path)+name).read_bytes()).hexdigest()
                                        for name in observed}, 'A4-B source/sidecar bytes changed'
                    if retained_backup is not None:
                        assert attempt.backup_path.is_file() and hashlib.sha256(attempt.backup_path.read_bytes()).hexdigest() == retained_backup, 'A4-B retained backup deleted or modified'
                    print('PASS A4-B coherent source/WAL/fsync/independent inventory/original custody:', fault, flush=True)
                if keeper is not None:
                    keeper.close()
        if sys.argv[1:] == ['--bootstrap-backup']:
            raise SystemExit(0)

    # A4-J is the independently consumed event wire/storage contract, using
    # synthetic phase inputs. It does not certify installation/owner closure.
    if sys.argv[1:] in ([], ['--bootstrap-journal']):
        from runtime.infrastructure import workflow_sequential_bootstrap as bootstrap
        from runtime.infrastructure.org_access_fence import OrgAccessFence
        from runtime.daemon.workflow_setup_status import read_setup_status
        from runtime.runtime import daemon_home

        for fault in ('none', 'pending', 'file_sync', 'directory_sync', 'reserve', 'replay', 'alias', 'terminal', 'descriptor_close'):
            with install_integrity_fixture() as fixture:
                path, inventory, original, before, backup, backup_hash, argv = fixture
                fence = OrgAccessFence.acquire(path.parent, exclusive=True)
                with closing(sqlite3.connect(path)) as connection:
                    identity = schema._coding_org_identity(connection, expected_org_slug='scratch', expected_path=str(path))
                def attempt():
                    return bootstrap.BootstrapAttempt(home=daemon_home(), runtime_root=path.parents[2], slug='scratch',
                        fence=fence, source_identity=identity,
                        source_inventory_digest=hashlib.sha256(repr(original).encode()).hexdigest(),
                        deadline=time.monotonic()+30)
                owner = attempt()
                extra = None
                if fault == 'descriptor_close':
                    native_close, failed_closes = os.close, []
                    def refused_close(fd):
                        if owner._descriptors and fd == owner._descriptors[0]['fd']:
                            failed_closes.append(fd)
                            raise OSError('synthetic original descriptor close uncertainty')
                        native_close(fd)
                    try:
                        with patch.object(bootstrap.os, 'close', refused_close):
                            try:
                                owner.begin()
                            except OSError:
                                pass
                            else:
                                raise AssertionError('A4-J directory close failure accepted')
                            assert len(owner._descriptors) == 2, 'A4-J lost partially constructed directory descriptor'
                            for _ in range(2):
                                try:
                                    owner.close()
                                except bootstrap.BootstrapEvidenceError as exc:
                                    assert str(exc) == 'workflow_setup_descriptor_close_unknown'
                                else:
                                    raise AssertionError('A4-J invented original descriptor closure')
                            assert len(failed_closes) == 1, 'A4-J retried unknown original descriptor close'
                            assert owner._descriptors[1]['closed'] and not owner._descriptors[0]['closed']
                            os.fstat(failed_closes[0])  # actual injected-open original remains retained
                            assert owner.failed and not owner.closed
                        print('PASS A4-J partially constructed original descriptor custody; no retry', flush=True)
                    finally:
                        if failed_closes:
                            native_close(failed_closes[0])  # exact synthetic leak after custody observation
                        fence.close()
                    continue
                try:
                    # Real fsync calls and original descriptors; no success stub.
                    syncs = []
                    native_fsync = os.fsync
                    def fsync(fd):
                        info = os.fstat(fd)
                        syncs.append((info.st_ino, stat.S_ISDIR(info.st_mode)))
                        native_fsync(fd)
                    import stat
                    with patch.object(bootstrap.os, 'fsync', fsync):
                        owner.begin()
                    first = owner.root / '00.json'
                    assert first.is_file() and first.stat().st_mode & 0o777 == 0o600
                    assert (first.stat().st_ino, False) in syncs, 'A4-J intent file not fsynced'
                    assert syncs[-1] == (owner.root.stat().st_ino, True), 'A4-J intent directory not fsynced'
                    assert all(p.stat().st_mode & 0o777 == 0o700 for p in
                        (owner.root, owner.namespace, owner.namespace.parent, owner.namespace.parent.parent,
                         owner.namespace.parent.parent.parent)), 'A4-J nonprivate namespace'
                    if fault == 'terminal':
                        owner.publish('not_installed')
                        assert json.loads((owner.root / '01.json').read_bytes())['state'] == 'not_installed'
                        try:
                            owner.publish('backup_verified', backup_sha256=backup_hash)
                        except bootstrap.BootstrapEvidenceError:
                            pass
                        else:
                            raise AssertionError('A4-J terminal attempt replayed')
                    else:
                        with owner.backup_path.open('xb') as target:
                            target.write(backup.read_bytes())
                            target.flush()
                            os.fchmod(target.fileno(), 0o600)
                            os.fsync(target.fileno())
                        if fault == 'pending':
                            (owner.root / '01.json.pending').write_bytes(b'incomplete')
                        elif fault == 'alias':
                            owner.backup_path.unlink()
                            owner.backup_path.symlink_to(backup)
                        if fault in ('file_sync', 'directory_sync'):
                            def refused_fsync(fd):
                                is_directory = stat.S_ISDIR(os.fstat(fd).st_mode)
                                if is_directory == (fault == 'directory_sync'):
                                    raise OSError('synthetic original fsync refusal')
                                native_fsync(fd)
                            with patch.object(bootstrap.os, 'fsync', refused_fsync):
                                try:
                                    owner.publish('backup_verified', backup_sha256=backup_hash)
                                except OSError:
                                    pass
                                else:
                                    raise AssertionError('A4-J failed fsync accepted')
                        elif fault in ('pending', 'alias'):
                            try:
                                owner.publish('backup_verified', backup_sha256=backup_hash)
                            except bootstrap.BootstrapEvidenceError:
                                pass
                            else:
                                raise AssertionError('A4-J unsafe/incomplete namespace accepted')
                        else:
                            owner.publish('backup_verified', backup_sha256=backup_hash)
                            if fault == 'reserve':
                                with patch.object(bootstrap.time, 'monotonic', return_value=owner.deadline-19):
                                    try:
                                        owner.publish('pre_commit')
                                    except bootstrap.BootstrapEvidenceError as exc:
                                        assert str(exc) == 'workflow_setup_commit_reserve'
                                    else:
                                        raise AssertionError('A4-J bypassed original COMMIT reserve')
                                assert not (owner.root / '02.json').exists(), 'A4-J published late COMMIT intent'
                            elif fault == 'replay':
                                extra = attempt()
                                try:
                                    extra.begin()
                                except bootstrap.BootstrapEvidenceError as exc:
                                    assert str(exc) == 'workflow_setup_attempt_latched'
                                else:
                                    raise AssertionError('A4-J automatically retried latched attempt')
                                assert set(p.name for p in owner.namespace.iterdir()) == {owner.attempt_id}
                            else:
                                for phase in ('pre_commit', 'committed', 'verified', 'registered'):
                                    owner.publish(phase)
                                previous = None
                                for seq, phase in enumerate(('intent', 'backup_verified', 'pre_commit', 'committed', 'verified', 'registered')):
                                    raw = (owner.root / f'{seq:02}.json').read_bytes()
                                    value = json.loads(raw)
                                    assert value['sequence'] == seq and value['previous_digest'] == previous
                                    assert value['phase'] == phase and value['source_inventory_digest'] == owner.source_inventory_digest
                                    previous = hashlib.sha256(raw).hexdigest()
                                status = read_setup_status(daemon_home(), path.parents[2], 'scratch')
                                assert (status.state, status.reason, status.backup_path) == ('installed_verified', 'registered', str(owner.backup_path)), status
                                assert {p.name for p in owner.root.iterdir()} == {'before.db', *(f'{seq:02}.json' for seq in range(6))}
                        if owner.failed:
                            frozen = {p.name: p.lstat() for p in owner.root.iterdir()}
                            try:
                                owner.publish('not_installed')
                            except bootstrap.BootstrapEvidenceError as exc:
                                assert str(exc) == 'workflow_setup_attempt_latched', 'A4-J failed publisher retried after uncertainty'
                            else:
                                raise AssertionError('A4-J failed publisher later emitted success/absence')
                            assert {p.name: p.lstat() for p in owner.root.iterdir()} == frozen
                    assert inventory() == original and hashlib.sha256(path.read_bytes()).hexdigest() == before, 'A4-J journal touched source'
                    assert hashlib.sha256(backup.read_bytes()).hexdigest() == backup_hash, 'A4-J restored/modified independent backup'
                    print('PASS A4-J original descriptor/fsync/event-chain/no-replay:', fault, flush=True)
                finally:
                    if extra is not None:
                        extra.close()
                    owner.close()
                    owner.close()  # no second close syscall for any original fd
                    assert all(record['closed'] for record in owner._descriptors)
                    fence.close()
        if sys.argv[1:] == ['--bootstrap-journal']:
            raise SystemExit(0)

    # A4-P uses the nearest I1a fixture and real primitive. All mutation,
    # deadline, authorizer and trace observers stay on this side of the boundary.
    if sys.argv[1:] in ([], ['--bootstrap-precommit']):
        from runtime.workflows.sequential_history import read_sequential_history

        for fault in ('none', 'permit', 'deadline', 'target', 'commit'):
            with install_integrity_fixture() as fixture:
                path, inventory, original, before, backup, backup_hash, argv = fixture
                statements, stages, callbacks = [], [], []
                conn = sqlite3.connect(path, timeout=0)
                conn.row_factory = sqlite3.Row
                conn.execute('PRAGMA foreign_keys=ON')
                conn.set_trace_callback(statements.append)
                allowance = schema.SequentialInstallAllowance(deadline=time.monotonic()+30)
                with schema._schema_snapshot(conn):
                    identity = schema._coding_org_identity(conn, expected_org_slug='scratch', expected_path=str(path))
                statements.clear()

                def physical_gate(connection, ledger, stage):
                    physical.require_install_integrity(connection, ledger, stage)
                    stages.append(stage)
                    if stage == 'target' and fault == 'target':
                        raise ValueError('synthetic target failure before permit')

                def offline():
                    assert schema._coding_org_identity(conn, expected_org_slug='scratch', expected_path=str(path)) == identity

                with ExitStack() as stack:
                    def permit():
                        assert stages == ['writer_source', 'target'], 'A4-P permit preceded full target integrity'
                        assert conn.in_transaction and 'COMMIT' not in statements, 'A4-P rehearsal/default gate after COMMIT'
                        history = read_sequential_history(conn, expected_org_slug='scratch')
                        assert history.layout.sequential and history.layout.ownership == 2
                        callbacks.append('default-target')
                        if fault == 'permit':
                            raise ValueError('synthetic original parent withheld permit')
                        if fault == 'deadline':
                            stack.enter_context(patch.object(schema.time, 'monotonic', return_value=allowance.deadline+1))
                        if fault == 'commit':
                            conn.set_authorizer(lambda action, arg1, *_: sqlite3.SQLITE_DENY
                                if action == sqlite3.SQLITE_TRANSACTION and arg1 == 'COMMIT' else sqlite3.SQLITE_OK)

                    stack.enter_context(patch.object(schema, 'require_install_integrity', physical_gate))
                    error = None
                    try:
                        result = schema.install_sequential_schema(conn, expected_org_slug='scratch', expected_path=str(path),
                            source_identity=identity, source_inventory=original, offline_check=offline,
                            allowance=allowance, before_commit=permit)
                    except (ValueError, schema.SequentialCommitUncertain) as exc:
                        error = exc
                if error is not None and isinstance(error.__cause__, AssertionError):
                    raise error.__cause__  # retain attribution from the real callback boundary
                assert stages == ['writer_source', 'target'], ('A4-P integrity gates changed', fault, stages)
                assert callbacks == ([] if fault == 'target' else ['default-target']), ('A4-P callback absent/repeated', fault, callbacks)
                if fault == 'none':
                    assert error is None and result == 'installed' and statements.count('COMMIT') == 1
                    assert not conn.in_transaction
                else:
                    assert error is not None, ('A4-P refused permit/target/deadline accepted', fault)
                    if fault == 'commit':
                        assert isinstance(error, schema.SequentialCommitUncertain), 'A4-P lost COMMIT uncertainty'
                    else:
                        assert type(error) is ValueError and 'COMMIT' not in statements, ('A4-P premature COMMIT', fault)
                    if fault == 'deadline':
                        assert str(error) == 'workflow_seq_install_deadline_exhausted'
                    assert 'ROLLBACK' in statements and not conn.in_transaction, ('A4-P lost owned rollback', fault)
                conn.close()
                if fault != 'none':
                    assert inventory() == original, ('A4-P changed original typed cells/rowids/SQL', fault)
                    assert hashlib.sha256(path.read_bytes()).hexdigest() == before, ('A4-P changed checkpointed fixture bytes', fault)
                else:
                    with closing(sqlite3.connect(path)) as reopened:
                        reopened.execute('PRAGMA foreign_keys=ON')
                        assert read_sequential_history(reopened, expected_org_slug='scratch').layout.sequential
                assert hashlib.sha256(backup.read_bytes()).hexdigest() == backup_hash, 'A4-P modified retained backup'
                print('PASS A4-P pre-COMMIT default target/gates/permit:', fault,
                      'committed-once' if fault == 'none' else 'original-preserved', flush=True)
        if sys.argv[1:] == ['--bootstrap-precommit']:
            raise SystemExit(0)

    with tempfile.TemporaryDirectory(prefix='workflow-bad-integrity-') as scratch:
        bad_path = Path(scratch) / 'bad.db'
        diagnostic = 'raw_private_diagnostic_never_emit'
        bad = sqlite3.connect(bad_path)
        # A genuine native non-ok result, never a successful validator stub.
        bad.execute('CREATE TABLE probe(id INTEGER PRIMARY KEY,value TEXT)')
        bad.execute('CREATE INDEX ' + diagnostic + ' ON probe(value)')
        bad.execute("INSERT INTO probe VALUES(1,'original')")
        entry = tuple(bad.execute('SELECT rowid,type,name,tbl_name,rootpage,sql FROM sqlite_schema '
                                 'WHERE name=?', (diagnostic,)).fetchone())
        cookie = bad.execute('PRAGMA schema_version').fetchone()[0]
        bad.execute('PRAGMA writable_schema=ON')
        bad.execute('DELETE FROM sqlite_schema WHERE rowid=?', (entry[0],))
        bad.execute('PRAGMA schema_version=' + str(cookie+1))
        bad.execute('PRAGMA writable_schema=RESET')
        bad.execute("UPDATE probe SET value='unindexed change' WHERE id=1")
        bad.execute('PRAGMA writable_schema=ON')
        bad.execute('INSERT INTO sqlite_schema(rowid,type,name,tbl_name,rootpage,sql) VALUES(?,?,?,?,?,?)', entry)
        bad.execute('PRAGMA schema_version=' + str(cookie+2))
        bad.execute('PRAGMA writable_schema=RESET')
        assert tuple(bad.execute('SELECT rowid,type,name,tbl_name,rootpage,sql FROM sqlite_schema '
                                'WHERE name=?', (diagnostic,)).fetchone()) == entry
        bad.commit()
        bad.close()
        bad = sqlite3.connect(bad_path.as_uri() + '?mode=ro', uri=True)
        bad.execute('PRAGMA query_only=ON')
        try:
            assert physical.check_full_integrity(bad, physical.IntegrityLedger(time.monotonic()+10)).outcome == 'failed'
            stages = ('source', 'backup', 'writer_source', 'target')
            # Every failure is reached at its own gate; preceding checks remain
            # genuine. The selected gate checks the known-bad private snapshot.
            for failed_stage, uncertainty in [(s, None) for s in stages] + [
                    ('target', 'rollback'), ('target', 'equality'),
                    ('target', 'sql_budget'), ('target', 'deadline'), (None, None)]:
                with install_integrity_fixture() as fixture:
                    path, inventory, original, before, backup, backup_hash, argv = fixture
                    seen, statements = [], []
                    native_connect = sqlite3.connect
                    def connect(*args, **kwargs):
                        conn = native_connect(*args, **kwargs)
                        if conn.execute('PRAGMA database_list').fetchone()[2] == str(path):
                            conn.set_trace_callback(statements.append)
                        return conn
                    def gate(conn, ledger, stage):
                        assert type(conn) is sqlite3.Connection
                        assert conn.in_transaction, stage + ' outside snapshot'
                        assert conn.execute('PRAGMA query_only').fetchone()[0] == (stage in ('source', 'backup'))
                        if stage == 'backup':
                            assert Path(conn.execute('PRAGMA database_list').fetchone()[2]) == backup
                        if stage == 'target':
                            assert conn.execute('SELECT version FROM workflow_seq_schema_versions').fetchone()[0] == 1
                        start = ledger.consumed
                        if stage == failed_stage and uncertainty == 'rollback':
                            conn.set_authorizer(lambda op, one, two, db, trigger:
                                sqlite3.SQLITE_DENY if op == sqlite3.SQLITE_TRANSACTION and one == 'ROLLBACK'
                                else sqlite3.SQLITE_OK)
                        if stage == failed_stage and uncertainty == 'sql_budget':
                            ledger.sql_limit = ledger.consumed
                        if stage == failed_stage and uncertainty == 'deadline':
                            ledger.deadline = time.monotonic()-1
                        try:
                            physical.require_install_integrity(bad if stage == failed_stage else conn, ledger, stage)
                        finally:
                            seen.append((stage, id(ledger), start, ledger.consumed))
                    stdout, stderr = io.StringIO(), io.StringIO()
                    with ExitStack() as stack:
                        stack.enter_context(patch.object(installer.sqlite3, 'connect', connect))
                        stack.enter_context(patch.object(installer, 'require_install_integrity', gate))
                        stack.enter_context(patch.object(schema, 'require_install_integrity', gate))
                        if uncertainty == 'equality':
                            stack.enter_context(patch.object(installer, '_prove_original_inventory',
                                side_effect=OSError(diagnostic)))
                        stack.enter_context(redirect_stdout(stdout))
                        stack.enter_context(redirect_stderr(stderr))
                        result = installer.main(argv)
                    output = stdout.getvalue() + stderr.getvalue()
                    unknown = uncertainty in ('rollback', 'equality')
                    expected_result = (4 if unknown else 1) if failed_stage else 0
                    assert result == expected_result, (failed_stage, uncertainty, result, output)
                    expected = list(stages[:stages.index(failed_stage)+1]) if failed_stage else list(stages)
                    assert [s[0] for s in seen] == expected, (failed_stage, output, seen)
                    assert len({s[1] for s in seen}) == 1 and all(s[3] >= s[2] for s in seen)
                    assert all(s[3] > s[2] for s in seen if s[0] != failed_stage or uncertainty not in ('sql_budget', 'deadline'))
                    assert all(right[2] == left[3] for left, right in zip(seen, seen[1:])), 'integrity ledger replenished'
                    assert diagnostic not in output and 'CHECK constraint failed' not in output, 'raw diagnostic leaked'
                    assert not any('INSERT INTO audit_log' in sql for sql in statements), 'integrity diagnostic written to audit'
                    if failed_stage:
                        category = 'install_integrity_unavailable' if uncertainty in ('sql_budget', 'deadline') else 'install_integrity_failed'
                        assert category + ' stage=' + failed_stage in output, output
                        if category == 'install_integrity_unavailable':
                            assert 'cause=' + uncertainty in output, output
                        assert ('unknown' if unknown else 'not_installed') in output
                        assert inventory() == original, failed_stage + ' changed original inventory'
                        assert hashlib.sha256(path.read_bytes()).hexdigest() == before, failed_stage + ' changed original hash'
                        assert 'COMMIT' not in statements, failed_stage + ' attempted COMMIT'
                        writes = [sql for sql in statements if sql.startswith(('INSERT ', 'UPDATE ', 'DELETE ', 'CREATE ', 'DROP '))]
                        if failed_stage != 'target':
                            assert not writes, failed_stage + ' performed install DML/DDL'
                        else:
                            assert any('INSERT INTO workflow_seq_schema_versions' in sql for sql in writes)
                            if uncertainty != 'rollback':
                                assert 'ROLLBACK' in statements, 'target failure lacked owned rollback'
                        print('PASS I1a gate:', failed_stage, uncertainty or 'not_installed', 'original inventory/hash equal')
                    else:
                        assert result == 0 and stdout.getvalue().startswith('installed:'), output
                        assert installer.main(argv[:4] + ['--check']) == 0, 'clean install did not reopen'
                        print('PASS I1a clean install: four gates, one cumulative ledger, actual commit/reopen')
                    assert hashlib.sha256(backup.read_bytes()).hexdigest() == backup_hash, 'backup changed'
        finally:
            bad.close()

    # Deliberately malformed or interrupted native-result delivery, not healthy
    # validation stubs. Even an ok prefix cannot certify normal cursor EOF.
    class ResultCursor:
        def __init__(self, rows):
            self.rows = iter(rows)
        def execute(self, sql):
            assert sql == 'PRAGMA integrity_check'
        def fetchone(self):
            row = next(self.rows, None)
            if isinstance(row, BaseException):
                raise row
            return row
        def close(self):
            pass
    class ResultConnection:
        def __init__(self, rows):
            self.rows = rows
            self.handler = None
        def cursor(self):
            return ResultCursor(self.rows)
        def set_progress_handler(self, handler, steps):
            self.handler = handler
    for label, rows in (
            ('empty', []), ('malformed', [('ok', 'extra')]), ('wrong type', [(b'ok',)]),
            ('extra row', [('ok',), ('ok',)]),
            ('interrupted EOF', [('ok',), sqlite3.OperationalError('raw_private_interrupt')]),
            ('interrupted execute', [KeyboardInterrupt('raw_private_interrupt')])):
        conn = ResultConnection(rows)
        result = physical.check_full_integrity(conn, physical.IntegrityLedger(time.monotonic()+10))
        assert result == physical.IntegrityResult('unavailable', 'invalid_result'), (label, result)
        assert conn.handler is None, label + ' leaked progress handler'
        print('PASS I1a unavailable:', label)
    for code, expected in ((sqlite3.SQLITE_BUSY, 'busy'), (sqlite3.SQLITE_IOERR, 'io')):
        error = sqlite3.OperationalError('raw_private_native_error')
        error.sqlite_errorcode = code
        result = physical.check_full_integrity(ResultConnection([error]), physical.IntegrityLedger(time.monotonic()+10))
        assert result == physical.IntegrityResult('unavailable', expected)
        print('PASS I1a unavailable:', expected)

    with tempfile.TemporaryDirectory(prefix='workflow-integrity-ledger-') as scratch:
        path = Path(scratch) / 'clean.db'
        conn = sqlite3.connect(path)
        conn.execute('CREATE TABLE sentinel(value)')
        conn.executemany('INSERT INTO sentinel VALUES(?)', ((i,) for i in range(2000)))
        conn.commit()
        conn.close()
        conn = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)
        conn.execute('PRAGMA query_only=ON')
        try:
            measured = physical.IntegrityLedger(time.monotonic()+10)
            assert physical.check_full_integrity(conn, measured).outcome == 'ok'
            ledger = physical.IntegrityLedger(time.monotonic()+10, sql_limit=measured.consumed*2)
            assert physical.check_full_integrity(conn, ledger).outcome == 'ok'
            first_cost = ledger.consumed
            assert physical.check_full_integrity(conn, ledger) == physical.IntegrityResult('unavailable', 'sql_budget')
            assert first_cost < ledger.consumed <= ledger.sql_limit
            assert physical.check_full_integrity(conn, ledger).outcome == 'unavailable'
            print('PASS I1a cumulative budget: second full check exhausts shared ledger, no replenishment')
            assert physical.check_full_integrity(conn, physical.IntegrityLedger(time.monotonic()-1)) == physical.IntegrityResult('unavailable', 'deadline')
            ledger = physical.IntegrityLedger(1.0)
            with patch.object(physical.time, 'monotonic', side_effect=lambda: 0.0 if ledger.consumed < 2000 else 2.0):
                assert physical.check_full_integrity(conn, ledger) == physical.IntegrityResult('unavailable', 'deadline')
            assert conn.total_changes == 0 and conn.execute('PRAGMA query_only').fetchone()[0] == 1
            assert conn.execute('SELECT count(*) FROM sentinel').fetchone()[0] == 2000
            print('PASS I1a deadline: before/during native full check, RO/query_only zero changes')
        finally:
            conn.close()
    if sys.argv[1:]:
        raise SystemExit(0)

# A0b-2 custody extends this existing real-SQLite venue. No native connection
# subclass, fake epoch, successful validator stub or production test seam.
if sys.argv[1:] not in (['--bounded-acquisition'], ['--sequential-reuse'], ['--sequential-load'], ['--sequential-freshness'], ['--sequential-faults'], ['--sequential-sql-exhaustion'], ['--sequential-corruption'], ['--retained-corruption'], ['--document-coding-corruption'], ['--coding-authority-corruption'], ['--document-native-corruption'], ['--author-handoff-corruption'], ['--document-revision-corruption'], ['--coding-history-corruption'], ['--physical-corruption'], ['--sequential-uncommitted']):
    class TraceAbort(BaseException):
        pass

    with tempfile.TemporaryDirectory(prefix='workflow-trace-custody-') as scratch:
        db = Database(Path(scratch) / 'org.db')
        try:
            assert type(db._conn) is sqlite3.Connection, 'trace custody changed native type'
            outer_sql = []
            abort = TraceAbort('synthetic interruption')
            with db.observe_trace(outer_sql.append) as outer:
                try:
                    with org_load_validation(db, org_slug='scratch'):
                        owner = load_guard._current.get()
                        db._conn.execute('SELECT 71001').fetchone()
                        db._conn.execute('CREATE TEMP TABLE trace_custody (value)')
                        assert owner.schema_epoch == 1, 'load observer lost native execution'
                        raise abort
                except TraceAbort as exc:
                    assert exc is abort, 'trace cleanup replaced the primary BaseException'
                assert not owner.active and owner.saved is None, 'load observer survived exit'
                assert load_guard._current.get() is None, 'load context was not restored'
                db._conn.execute('DROP TABLE temp.trace_custody')
                assert owner.schema_epoch == 1, 'load observer still receives statements after exit'
                assert outer_sql == ['SELECT 71001', 'CREATE TEMP TABLE trace_custody (value)',
                                     'DROP TABLE temp.trace_custody'], 'load outer observer silently replaced'
                assert not outer.failed, 'successful composed observation marked failed'
            db._conn.execute('SELECT 71002').fetchone()
            assert len(outer_sql) == 3, 'last observer exit leaked native registration'
            print('PASS A0b trace custody: load composition and BaseException restoration')

            delivered = []
            def observe(label):
                return lambda sql: delivered.append((label, sql))
            with db.observe_trace(observe('outer')):
                with db.observe_trace(observe('inner')):
                    cursor = db._conn.cursor()
                    try:
                        for _ in range(2):
                            cursor.execute('SELECT 71003').fetchone()  # Same cached SQL.
                        db._conn.executescript('SELECT 71004; SELECT 71005;')
                    finally:
                        cursor.close()
                db._conn.execute('SELECT 71006').fetchone()
            assert delivered == [(label, sql) for sql in (
                'SELECT 71003', 'SELECT 71003', 'SELECT 71004;', ' SELECT 71005;',
            ) for label in ('outer', 'inner')] + [('outer', 'SELECT 71006')], 'ordered trace delivery lost an observer'
            print('PASS A0b trace custody: ordered cached cursor and executescript delivery')

            repeated = []
            first = db.observe_trace(repeated.append)
            second = db.observe_trace(repeated.append)
            first.__enter__()
            second.__enter__()
            try:
                db._conn.execute('SELECT 71007').fetchone()
            finally:
                first.__exit__(None, None, None)
            try:
                db._conn.execute('SELECT 71008').fetchone()
            finally:
                second.__exit__(None, None, None)
            db._conn.execute('SELECT 71009').fetchone()
            assert repeated == ['SELECT 71007', 'SELECT 71007', 'SELECT 71008'], 'identity-based trace removal lost the remaining registration'
            print('PASS A0b trace custody: equal callbacks and independent exit order')

            for failure in (ValueError, TraceAbort):
                received = []
                def failing(sql):
                    raise failure('synthetic observer failure')
                with db.observe_trace(failing) as failed:
                    with db.observe_trace(received.append) as remaining:
                        db._conn.execute('SELECT 71010').fetchone()
                        assert received == ['SELECT 71010'], 'observer failure suppressed later delivery'
                        assert failed.failed and remaining.failed, 'observer failure did not poison all current observations'
                        with org_load_validation(db, org_slug='scratch'):
                            owner = load_guard._current.get()
                            assert owner.trace.failed and owner.bookend(db._conn) is None, 'failed outer observation certified a load'
                with db.observe_trace(received.append) as fresh:
                    assert not fresh.failed, 'finished delivery failure leaked into a fresh interval'
                print('PASS A0b trace custody: sticky delivery failure', failure.__name__)

            install_or_recover(db, expected_org_slug='scratch')
            def read():
                return read_workflow_schema_for_org_load(db._conn, expected_org_slug='scratch')
            import asyncio
            cancelled = asyncio.CancelledError('synthetic load cancellation')
            delivered = []
            with db.observe_trace(delivered.append):
                try:
                    with org_load_validation(db, org_slug='scratch'):
                        read()
                        cancelled_owner = load_guard._current.get()
                        assert cancelled_owner.saved is not None
                        raise cancelled
                except asyncio.CancelledError as exc:
                    assert exc is cancelled, 'load cleanup replaced cancellation'
                assert not cancelled_owner.active and cancelled_owner.saved is None
                assert load_guard._current.get() is None and not db._conn.in_transaction
                db._conn.execute('SELECT 71011').fetchone()
                assert delivered[-1] == 'SELECT 71011', 'cancellation cleanup lost the outer observer'
            print('PASS A0b trace custody: CancelledError preserves primary, clears owner and restores outer observer')
            for schema, method in product(('main', 'temp'), ('connection', 'cursor', 'script')):
                with org_load_validation(db, org_slug='scratch'):
                    read()
                    owner = load_guard._current.get()
                    assert owner.saved is not None, 'legacy source could not establish real certificate'
                    for repeat in range(2):
                        before = load_guard._bookend(db._conn)
                        full = owner.full_runs
                        ddl = f'/* custody */ -- cached execution\n CREATE TABLE {schema}.transient_trace (value)'
                        if method == 'script':
                            db._conn.executescript('BEGIN; ' + ddl + '; ROLLBACK;')
                        else:
                            db._conn.execute('BEGIN')
                            if method == 'cursor':
                                cursor = db._conn.cursor()
                                try:
                                    cursor.execute(ddl)
                                finally:
                                    cursor.close()
                            else:
                                db._conn.execute(ddl)
                            db._conn.rollback()
                        assert load_guard._bookend(db._conn) == before, 'transient DDL fixture failed to restore metadata'
                        read()
                        assert owner.full_runs == full + 1, 'transient DDL reused a stale certificate'
                print('PASS A0b legacy observer: repeated rolled-back DDL', schema, method)

            for pragma in ('query_only', 'defer_foreign_keys'):
                with org_load_validation(db, org_slug='scratch'):
                    read()
                    owner = load_guard._current.get()
                    full = owner.full_runs
                    db._conn.execute(f'/* assigning config */ PRAGMA {pragma}(ON)')
                    assert owner.bookend(db._conn) is None or owner.saved is None, 'assigning PRAGMA kept a certificate'
                    if pragma == 'defer_foreign_keys':
                        assert load_guard._bookend(db._conn) is None, 'deferred FK configuration remained eligible'
                    db._conn.execute(f'PRAGMA {pragma}=OFF')
                    read()
                    assert owner.full_runs == full + 1, 'restored PRAGMA reused a stale certificate'
                print('PASS A0b legacy observer: assigning and restored PRAGMA', pragma)

            with org_load_validation(db, org_slug='scratch'):
                read()
                owner = load_guard._current.get()
                db._conn.execute('PRAGMA page_count').fetchone()
                assert owner.observer_failed and owner.saved is None, 'unknown statement classification retained reuse'
                full = owner.full_runs
                read()
                read()
                assert owner.full_runs == full + 2, 'unknown classification did not force full fallback'
            print('PASS A0b legacy observer: unknown classification disables interval')
        finally:
            db.close()

    with tempfile.TemporaryDirectory(prefix='workflow-trace-closed-') as scratch:
        db = Database(Path(scratch) / 'org.db')
        abort = TraceAbort('synthetic body interruption')
        try:
            with db.observe_trace(lambda sql: None) as observation:
                db._conn.close()  # Genuine native cleanup failure, not a mocked setter.
                raise abort
        except TraceAbort as exc:
            assert exc is abort and observation.failed, 'cleanup lost primary failure or claimed restored custody'
            assert exc.__notes__ == ['Database trace observer cleanup failed']
        else:
            raise AssertionError('trace cleanup suppressed BaseException')
        try:
            with db.observe_trace(lambda sql: None):
                raise AssertionError('failed native custody was reused')
        except RuntimeError as exc:
            assert str(exc) == 'database_trace_custody_unavailable'
        print('PASS A0b trace custody: failed cleanup preserves primary and refuses reuse')

if sys.argv[1:] == ['--trace-custody']:
    raise SystemExit(0)

# Extend the load/source/three-target keeper to actual installed sequential
# layouts. Existing private installers and storage owners prepare the fixtures.
# Native SQLite statements, outcomes and original cumulative counters are the
# oracles; no validator replacement, success stub or fabricated epoch.
if sys.argv[1:] in ([], ['--sequential-reuse'], ['--sequential-load'], ['--sequential-freshness'], ['--sequential-faults'], ['--sequential-sql-exhaustion'], ['--sequential-corruption'], ['--retained-corruption'], ['--document-coding-corruption'], ['--coding-authority-corruption'], ['--document-native-corruption'], ['--author-handoff-corruption'], ['--document-revision-corruption'], ['--coding-history-corruption'], ['--physical-corruption'], ['--sequential-uncommitted']):
    from runtime.infrastructure.workflow_schema import (
        initialize_complete_org_schema, install_sequential_schema,
        sequential_storage_inventory, _coding_org_identity, _schema_snapshot,
    )
    from runtime.workflows.coding_authority_history import _Budget
    from runtime.workflows.sequential_history import read_sequential_history
    from runtime.workflows import sequential_history as seq_history
    from runtime.workflows._read_exhaustion import ReadExhaustion
    from runtime.infrastructure.task_pause_controls import PauseControlError

    def counters(budget):
        return budget.consumed, budget.accepted_rows, budget.traversed

    def seq_read(db, **kwargs):
        return read_workflow_schema_for_org_load(db._conn, expected_org_slug='scratch', **kwargs)

    def sequential_outcome(operation):
        try:
            return ('returned', operation())
        except (ValueError, sqlite3.Error, PauseControlError) as exc:
            cause = exc
            categories = []
            while cause is not None:
                categories.append((type(cause), str(cause)))
                cause = cause.__cause__
            return ('refused', tuple(categories))

    def populate_retained(db, root):
        import hashlib
        from runtime.infrastructure.team_move_store import FounderAdmission, MoveRequest, Phase, Diagnostic
        from runtime.infrastructure.team_move_store import _bytes as move_bytes

        # Real org storage owners, synthetic private rows. No domain move,
        # filesystem effect, human admission or native settlement is performed.
        reconcile_namespace(SimpleNamespace(root=root, db=db, slug='scratch'),
                            Namespace((('synthetic_agent', 'active'),), ()), install=True)
        store = TaskPauseStore(db, 'scratch')
        with store.writer('TASK-seq-load'):
            row = store.ensure_uncommitted('TASK-seq-load')
            row['journal']['entries'] = [dict(id='synthetic-entry', org='scratch',
                root_task_id='TASK-seq-load', task_id='TASK-seq-load', agent='synthetic_agent',
                session_id='synthetic-session', owner='ordinary', owner_identity={},
                fingerprint=dict(status='pending', block_kind=None, count=0, session_id=None,
                    agent=None, fanout=None, jobs=None, parent=None, brief_digest='0' * 64),
                generation=0, phase='prepared', retry={}, context={}, started_at=None,
                captured_generation=None)]
            store.save_journal_uncommitted(row)
        install_team_move_schema(db, org_slug='scratch')
        digest = hashlib.sha256(b'').hexdigest()
        identity = dict(device=0, inode=1, mode=0o700)
        request = MoveRequest.model_validate(dict(version=1, org_slug='scratch',
            source_team='consultant', target_team='default', target_principal='founder',
            affected_agents=('consultant_codex', 'consultant_head'), demote='consultant_head',
            retire_source=True, drain_threads=False,
            expected=dict(**{key: digest for key in ('canonical_digest', 'roster_digest',
                'naming_digest', 'profile_digest', 'dependency_digest', 'policy_digest',
                'skill_selection_digest', 'permission_digest', 'authority_digest')},
                authority_namespace='org/scratch', authority_generation=1,
                agent_revisions=(digest, digest)),
            files=tuple(dict(path=path, identity=identity, before_base64='', before_digest=digest,
                after_base64='', after_digest=digest) for path in (
                    'org/agents/consultant_codex.md', 'org/agents/consultant_head.md', 'org/teams.yaml')),
            workspaces=tuple(dict(agent_id=agent, path='/synthetic/' + agent, identity=identity)
                for agent in ('consultant_codex', 'consultant_head'))))
        moves = TeamMoveStore(db, 'scratch')
        move = moves.reserve(operation_key='synthetic-retained', request_bytes=move_bytes(request),
            admission=FounderAdmission(principal='founder', boundary='_require_human',
                org_slug='scratch', admission_id='synthetic-not-live', admitted_at='2026-11-14T00:00:00+00:00'))
        moves.transition(move['operation_id'], expected_revision=1, phase=Phase.REFUSED,
                         diagnostic=Diagnostic(code='synthetic_refusal'))
        for table in ('task_pause_controls', 'identity_name_owners', 'identity_name_claims', 'team_move_operations'):
            assert db._conn.execute('SELECT count(*) FROM ' + table).fetchone()[0] > 0, table + ' retained fixture is empty'
        assert db._conn.execute("SELECT count(*) FROM audit_log WHERE action IN ('team_move_reserved','team_move_transition')").fetchone()[0] == 2
        assert read_sequential_history(db._conn, expected_org_slug='scratch').status == 'storage readable'

    def populate_document_coding(db, *, handoff=False, human=False, artifact=False, revision=False):
        import hashlib
        from runtime.workflows.templates import WorkflowTemplateStore, WorkflowTemplatePrincipal
        from runtime.workflows._coding_registered_release import registration_rows
        from runtime.workflows.release_templates import read_coding_release

        admission_owner = 'human:founder' if revision else 'synthetic-manager'
        # Inert template publication and exact saved built-in rows, as in the
        # retained coding-audit keeper. No current setup, Allow or run exists.
        definition = dict(kind='document-review', schema_version=3 if handoff else 2,
        description='Synthetic retained document', author=dict(role='author', kind='agent'),
        output=dict(primitive='immutable-document-revision', description='Synthetic output'),
        reviewers=[dict(role='reviewer', kind='agent')], outcomes=['approved'],
        approval=dict(mode='all', revision='current', required_roles=['reviewer']),
        request_changes=None, submission=dict(timing='on-completion',
            **(dict(delivery='current-author-handoff@1') if handoff else {})))
        if human:
            definition['reviewers'][0]['kind'] = 'human'
        if revision:
            definition.update(outcomes=['approved', 'changes_requested'], request_changes=dict(
                action='return-to-author', revision='new', invalidate='all-prior-receipts'))
        if artifact:
            definition.update(kind='artifact-review', schema_version=4,
                output=dict(primitive='immutable-artifact-revision', description='Synthetic output',
                            accepted_kinds=['document']))
        if handoff:
            from runtime.workflows.templates import _validate_definition, _definition_pins, _stable_id
            conn = db._conn
            namespace, name = 'org/scratch/team/engineering', 'synthetic-document'
            raw = _validate_definition(definition)
            dg = hashlib.sha256(raw).hexdigest()
            compiler, validator, source = _definition_pins(definition)
            identity = _stable_id('workflow-template', namespace, name)
            request_digest = hashlib.sha256(seq_history._bytes(dict(namespace=namespace,
                template_name=name, expected_current_version=0, definition_digest=dg,
                compiler_pin=compiler, validator_pin=validator, source_pin=source))).hexdigest()
            draft = _stable_id('workflow-template-draft', identity, 1, request_digest)
            version = _stable_id('workflow-template-version', identity, 1, dg)
            stamp = '2026-11-14T00:00:00.000000Z'
            publisher = seq_history._bytes(dict(synthetic='archival-template')).decode()
            conn.execute('INSERT INTO workflow_template_identities VALUES (?,?,?,?,?,?)',
                         (identity, namespace, name, 1, 'active', stamp))
            conn.execute('INSERT INTO workflow_template_drafts VALUES (?,?,?,?,?,?,?,?,?,?)',
                         (draft, namespace, name, raw, dg, compiler, validator, source, publisher, stamp))
            conn.execute('INSERT INTO workflow_template_versions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                         (version, draft, namespace, name, 1, raw, dg, compiler, validator, source, publisher, stamp))
            conn.execute('INSERT INTO workflow_template_identity_versions VALUES (?,?,?,?)',
                         (identity, 1, version, dg))
            conn.execute('INSERT INTO workflow_template_publish_operations VALUES (?,?,?,?,?,?,?,?)',
                         ('scratch', 'synthetic-manager', 'synthetic-document-template', request_digest, identity, 0, 1, version))
            conn.commit()
            template = WorkflowTemplateStore._read_version(conn, identity_id=identity, version=1,
                                                           expected_version_id=version)
        else:
            template = WorkflowTemplateStore(db).publish_version(org_slug='scratch',
                principal=WorkflowTemplatePrincipal.agent(org_slug='scratch',
                    agent_name='synthetic-manager', team_slug='engineering',
                    task_id='source', session_id='source-session', revalidate=lambda: None),
                operation_key='synthetic-document-template', namespace='org/scratch/team/engineering',
                template_name='synthetic-document', expected_current_version=0, definition=definition)
        conn = db._conn
        encode = seq_history._bytes
        digest = lambda raw: hashlib.sha256(raw).hexdigest()
        stamp = '2026-11-14T00:00:00.000000Z'
        for table, row in registration_rows('scratch', read_coding_release(), stamp).rows:
            conn.execute('INSERT INTO ' + table + ' (' + ','.join(row) + ') VALUES (' +
                         ','.join('?' for _ in row) + ')', tuple(row.values()))
        conn.commit()
        db.insert_task(TaskRecord(id='document-root', assigned_agent='synthetic-author',
                                  brief='Synthetic historical document root'))
        # Historical foundation/submission rows use the retained storage grammar,
        # not the retired activation/submission services. No admission is claimed.
        raw = encode(dict(synthetic='retained-document'))
        conn.execute('INSERT INTO workflow_authorization_revisions VALUES (?,?,?,?,?,?,?)',
            ('doc-authority', template.namespace, 1, raw, digest(raw), 'operator-input@2', stamp))
        conn.execute('INSERT INTO workflow_binding_snapshots VALUES (?,?,?,?,?,?)',
            ('doc-binding', template.version_id, 'doc-authority', raw, digest(raw), stamp))
        conn.execute('INSERT INTO workflow_contexts VALUES (?,?,?,?,?,?)',
            ('doc-context', 'doc-binding', raw, digest(raw), 'synthetic-history', 'doc-source'))
        conn.execute('INSERT INTO workflow_instances VALUES (?,?,?,?,?,?)',
            ('doc-instance', 'doc-binding', 'doc-context', 'document-root', 'synthetic-author', 'draft'))
        conn.execute('UPDATE workflow_task_ownership SET kind=?,owner_id=? WHERE task_id=?',
            ('document', 'doc-instance', 'document-root'))
        conn.execute('INSERT INTO workflow_activations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            ('doc-activation', 'doc-instance', 1, template.identity_id, template.version_id,
             template.namespace, 1, digest(raw), digest(raw), 'agent:synthetic-manager', 'active', stamp))
        conn.execute('INSERT INTO workflow_template_identities VALUES (?,?,?,?,?,?)',
            ('doc-other-identity', template.namespace, 'synthetic-unused', 0, 'active', stamp))
        conn.execute('INSERT INTO workflow_active_activations VALUES (?,?,?)',
            ('doc-instance', 'doc-activation', 1))
        result = conn.execute('INSERT INTO task_results(task_id,agent,session_id,created_at) VALUES (?,?,?,?)',
            ('document-root', 'synthetic-author', 'doc-session', stamp)).lastrowid
        content = b'Synthetic historical document bytes'
        conn.execute('INSERT INTO workflow_submissions VALUES (?,?,?,?,?,?,?,?,?,?)',
            ('doc-submission', 'doc-instance', 1, content, digest(content), None,
             'document-root', 'doc-session', str(result), 'synthetic-author'))
        conn.execute('INSERT INTO workflow_rounds VALUES (?,?,?,?,?)',
            ('doc-round', 'doc-instance', 'doc-submission', 1, 'reviewing'))
        # Retain both revisions and their original review closures. The first
        # joined event uses the historical opaque preimage; the second has an
        # explicit canonical subject. Neither may silently select the newest
        # round. These are archival storage rows, not reviewer invocations.
        content2 = b'Synthetic historical document revision two'
        conn.execute('INSERT INTO workflow_submissions VALUES (?,?,?,?,?,?,?,?,?,?)',
            ('doc-submission-2', 'doc-instance', 2, content2, digest(content2), None,
             'document-root', 'doc-session', str(result), 'synthetic-author'))
        conn.execute('INSERT INTO workflow_rounds VALUES (?,?,?,?,?)',
            ('doc-round-2', 'doc-instance', 'doc-submission-2', 2, 'reviewing'))
        for revision, round_id, submission_id, content in (
                (1, 'doc-round', 'doc-submission', content),
                (2, 'doc-round-2', 'doc-submission-2', content2)):
            for reviewer in range(3 if revision == 1 else 2):
                request_id = f'doc-review-{revision}-{reviewer}'
                review_scope = encode(dict(round_id=round_id, reviewer=reviewer))
                proof = encode(dict(synthetic_receipt=request_id))
                conn.execute('INSERT INTO workflow_review_requests VALUES (?,?,?,?,?,?,?,?)',
                    (request_id, round_id, f'synthetic-reviewer-{reviewer}', 1,
                     review_scope, digest(review_scope), 'approved', None))
                conn.execute('INSERT INTO workflow_review_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                    ('receipt-' + request_id, request_id, submission_id, digest(content), 1,
                     digest(review_scope), proof, digest(proof), 'approved', None, stamp))
            event_id = f'doc-event-joined-{revision}'
            body = (b'Synthetic historical opaque joined request' if revision == 1 else
                encode(dict(instance_id='doc-instance', round_id=round_id,
                    submission_id=submission_id, submission_digest=digest(content),
                    revision=revision, event_kind='joined')))
            conn.execute('INSERT INTO workflow_events VALUES (?,?,?,?,?,?,?)',
                (event_id, 'doc-instance', revision, 'joined', body,
                 digest(b'joined' + body) if revision == 1 else digest(body), stamp))
            conn.execute('INSERT INTO workflow_operation_replays VALUES (?,?,?,?,?,?)',
                ('scratch', 'synthetic-manager', event_id, digest(body), 'doc-instance', event_id))
        # Stored prelaunch cancellation only. No retired dispatcher, session,
        # callback, host witness or native settlement is invoked or invented.
        projection_keys = ('state', 'is_current', 'cancellation_requested', 'claim_token',
            'claim_owner', 'host_launch_started', 'host_execution_id', 'session_id', 'final_result_id')
        request = encode(dict(instance_id='doc-instance', operation_key='synthetic-draft-history'))
        scope = encode(dict(assigned_agent='synthetic-author', team='engineering',
                           brief='Synthetic historical document root'))
        admission = dict(instance_id='doc-instance', activation_id='doc-activation',
            activation_revision=1, attempt_sequence=1, predecessor_intent_id=None,
            admission_principal=admission_owner, operation_key='synthetic-draft-history',
            request_digest=digest(request), org_slug='scratch')
        intent_id = digest(encode(admission))
        queued = dict(state='queued', is_current=1, cancellation_requested=0, claim_token=None,
            claim_owner=None, host_launch_started=0, host_execution_id=None, session_id=None,
            final_result_id=None)
        cancelled = dict(queued, state='cancelled', cancellation_requested=1)
        intent = dict(id=intent_id, **{key: value for key, value in admission.items() if key != 'org_slug'},
            admission_kind='initial', request_bytes=request, task_id='document-root',
            context_id='doc-context', binding_snapshot_id='doc-binding',
            assigned_principal='synthetic-author', assignment_generation=1,
            authority_namespace=template.namespace, authority_generation=1,
            authority_digest=digest(raw), task_scope_bytes=scope, task_scope_digest=digest(scope),
            effect_key='workflow-initial-draft:doc-instance:1',
            host_execution_key='workflow-draft-host:' + intent_id, **cancelled,
            recovery_owner='workflow_recovery', last_error=None, created_at=stamp, updated_at=stamp)
        conn.execute('INSERT INTO workflow_draft_dispatch_intents (' + ','.join(intent) +
            ') VALUES (' + ','.join('?' for _ in intent) + ')', tuple(intent.values()))
        immutable = {key: value.hex() if type(value) is bytes else value for key, value in intent.items()
                     if key not in (*projection_keys, 'last_error', 'updated_at')}
        previous, previous_digest = None, None
        for sequence, (kind, after) in enumerate((('admitted', queued),
                ('cancel_requested', dict(queued, cancellation_requested=1)),
                ('cancelled', cancelled)), 1):
            event_id = intent_id + ':' + str(sequence)
            payload = encode(dict(format='workflow-draft-event@1', org_slug='scratch',
                event=dict(id=event_id, event_seq=sequence, event_kind=kind, created_at=stamp),
                previous_digest=previous_digest, intent=immutable, before=previous,
                after=after, terminal_evidence=None, result=None))
            conn.execute('INSERT INTO workflow_draft_dispatch_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (event_id, intent_id, sequence, kind, None if previous is None else previous['state'],
                 after['state'], payload, digest(payload), None, None, None, None, None, stamp))
            previous, previous_digest = after, digest(payload)
        conn.execute("UPDATE tasks SET status='cancelled' WHERE id='document-root'")
        conn.execute("UPDATE workflow_task_ownership SET kind='draft',owner_id=? WHERE task_id='document-root'",
                     (intent_id,))
        conn.commit()
        history = read_sequential_history(conn, expected_org_slug='scratch')
        assert history.status == 'storage readable' and history.retained[1].release is not None
        for table in ('workflow_template_drafts', 'workflow_template_versions',
                      'workflow_authorization_revisions', 'workflow_binding_snapshots', 'workflow_contexts',
                      'workflow_instances', 'workflow_activations', 'workflow_active_activations',
                      'workflow_submissions', 'workflow_rounds',
                      'workflow_review_requests', 'workflow_review_receipts',
                      'workflow_events', 'workflow_operation_replays',
                      'workflow_draft_dispatch_intents', 'workflow_draft_dispatch_events'):
            assert conn.execute('SELECT count(*) FROM ' + table).fetchone()[0] > 0, table + ' document fixture is empty'

    def populate_document_native(db, *, handoff=False, human=False, artifact=False, revision=False):
        import hashlib
        from runtime.infrastructure.workflow_schema import _DRAFT_MUTABLE, _DRAFT_PROJECTION

        populate_document_coding(db, handoff=handoff, human=human, artifact=artifact, revision=revision)
        conn = db._conn
        encode = seq_history._bytes
        digest = lambda raw: hashlib.sha256(raw).hexdigest()
        stamp = '2026-11-14T00:00:00.000000Z'
        intent = dict(conn.execute('SELECT * FROM workflow_draft_dispatch_intents').fetchone())
        result = dict(conn.execute("SELECT * FROM task_results WHERE task_id='document-root'").fetchone())
        assert result['status'] == 'completed' and type(result['id']) is int
        # Synthetic archival storage, never a callback/host/settlement producer.
        # Replace only this fixture's prelaunch cancellation with the full
        # original admitted -> claimed -> launch -> callback -> completed chain.
        queued = dict(state='queued', is_current=1, cancellation_requested=0, claim_token=None,
            claim_owner=None, host_launch_started=0, host_execution_id=None, session_id=None,
            final_result_id=None)
        claimed = dict(queued, state='claimed', claim_token='synthetic-claim', claim_owner='workflow_dispatcher')
        launched = dict(claimed, host_launch_started=1)
        running = dict(launched, state='running', host_execution_id='synthetic-host', session_id='doc-session')
        callback = dict(running, final_result_id=result['id'])
        completed = dict(callback, state='completed')
        intent.update(completed)
        conn.execute('UPDATE workflow_draft_dispatch_intents SET ' +
            ','.join(key + '=?' for key in _DRAFT_PROJECTION) + ' WHERE id=?',
            tuple(intent[key] for key in _DRAFT_PROJECTION) + (intent['id'],))
        conn.execute("UPDATE tasks SET status='completed',current_session_id='doc-session' WHERE id='document-root'")
        conn.execute('DELETE FROM workflow_draft_dispatch_events')
        immutable = {key: value.hex() if type(value) is bytes else value for key, value in intent.items()
                     if key not in _DRAFT_MUTABLE}
        accepted = dict(record=result, id=result['id'], digest=digest(encode(result)),
                        disposition='accepted', accepted=1)
        previous, previous_digest = None, None
        for sequence, (kind, after) in enumerate((('admitted', queued), ('claimed', claimed),
                ('launch_reserved', launched), ('running', running),
                ('callback_recorded', callback), ('completed', completed)), 1):
            event_id = intent['id'] + ':' + str(sequence)
            payload = encode(dict(format='workflow-draft-event@1', org_slug='scratch',
                event=dict(id=event_id, event_seq=sequence, event_kind=kind, created_at=stamp),
                previous_digest=previous_digest, intent=immutable, before=previous, after=after,
                terminal_evidence=dict(host_quiescent=True) if kind == 'completed' else None,
                result=accepted if kind in ('callback_recorded', 'completed') else None))
            native = ((result['session_id'], result['id'], accepted['digest'], 1, 'accepted')
                      if kind == 'callback_recorded' else (None,) * 5)
            conn.execute('INSERT INTO workflow_draft_dispatch_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (event_id, intent['id'], sequence, kind, None if previous is None else previous['state'],
                 after['state'], payload, digest(payload), *native, stamp))
            previous, previous_digest = after, digest(payload)

        # G operation/provenance and INTEGER result-link storage are separate
        # from the legacy source_result_id string used by the earlier fixture.
        conn.execute("UPDATE workflow_submissions SET source_result_id=NULL WHERE id='doc-submission'")
        submission = dict(conn.execute("SELECT * FROM workflow_submissions WHERE id='doc-submission'").fetchone())
        request = encode(dict(instance_id=submission['instance_id'], revision=submission['revision'],
            submission_id=submission['id'], submission_digest=submission['submission_digest']))
        operation = dict(submission_id=submission['id'], instance_id=submission['instance_id'],
            org_slug='scratch', principal=submission['author_principal'], source_task_id=result['task_id'],
            source_session_id=result['session_id'], operation_key='synthetic-submission',
            request_digest=digest(request), created_at=stamp)
        proof = encode(dict(format='workflow-submission-operation@1', operation=operation,
            submission={key: value.hex() if type(value) is bytes else value for key, value in submission.items()},
            source_intent=immutable, request_bytes=request.hex()))
        operation.update(provenance_bytes=proof, provenance_digest=digest(proof))
        conn.execute('INSERT INTO workflow_submission_operations (' + ','.join(operation) + ') VALUES (' +
            ','.join('?' for _ in operation) + ')', tuple(operation.values()))
        conn.execute('INSERT INTO workflow_submission_result_links VALUES (?,?,?,?,?,?,?)',
            (submission['id'], result['task_id'], result['session_id'], result['id'],
             encode(result), accepted['digest'], stamp))
        conn.commit()
        assert read_sequential_history(conn, expected_org_slug='scratch').status == 'storage readable'
        assert conn.execute('SELECT count(*) FROM workflow_submission_operations').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM workflow_submission_result_links').fetchone()[0] == 1
        assert [row[0] for row in conn.execute('SELECT event_kind FROM workflow_draft_dispatch_events ORDER BY event_seq')] == [
            'admitted', 'claimed', 'launch_reserved', 'running', 'callback_recorded', 'completed']

    def populate_author_handoff(db, *, human=False, artifact=False, revision=False):
        import base64
        import hashlib
        from runtime.infrastructure.workflow_schema import _DRAFT_MUTABLE
        from runtime.workflows.activation import parse_request, _authorization, _binding, _context
        from runtime.workflows.templates import WorkflowTemplateStore, _review_contract

        populate_document_native(db, handoff=True, human=human, artifact=artifact, revision=revision)
        conn = db._conn
        encode = seq_history._bytes
        digest = lambda raw: hashlib.sha256(raw).hexdigest()
        stamp = '2026-11-14T00:00:00.000000Z'
        def one(table):
            return dict(conn.execute('SELECT * FROM ' + table).fetchone())
        def insert(table, row):
            conn.execute('INSERT INTO ' + table + ' (' + ','.join(row) + ') VALUES (' +
                ','.join('?' for _ in row) + ')', tuple(row.values()))
        def update(table, identity, **values):
            conn.execute('UPDATE ' + table + ' SET ' + ','.join(key + '=?' for key in values) + ' WHERE id=?',
                         tuple(values.values()) + (identity,))
        intent = one('workflow_draft_dispatch_intents')
        activation = one('workflow_activations')
        template = WorkflowTemplateStore._read_version(conn,
            identity_id=activation['template_identity_id'], version=1,
            expected_version_id=activation['template_version_id'])
        # Staged H is a retained historical prefix: the source finished, while
        # no G submission or review graph was committed. It grants no action.
        for table in ('workflow_submission_result_links', 'workflow_submission_operations',
                      'workflow_review_receipts', 'workflow_review_requests', 'workflow_rounds',
                      'workflow_operation_replays', 'workflow_events', 'workflow_submissions'):
            conn.execute('DELETE FROM ' + table)
        profile_digest = digest(b'synthetic-profile')
        snapshot = dict(schema_version=1, org_slug='scratch', agents=[], teams=[], reviewer_agents=[],
            active_policy_selectors=[], machine_global_profiles=[dict(consumers=['synthetic-author'],
                generation=1, profile_digest=profile_digest, profile_name='synthetic-profile')])
        raw = encode(snapshot)
        authority = dict(namespace='org/scratch', generation=1, snapshot_digest=digest(raw))
        insert('workflow_publication_journals', dict(id='handoff-journal', **authority,
            expected_generation=0, snapshot_bytes=raw, publisher='human:founder',
            publisher_invocation='synthetic-not-live', profile_fence=0, state='cache_installed',
            recovery_owner='synthetic-history', file_phase_owner=None))
        pointer = dict(current_generation=1, journal_id='handoff-journal', snapshot_digest=digest(raw),
                       state='ready', profile_fence=0)
        insert('workflow_authority_pointers', dict(namespace='org/scratch', **pointer))
        bindings = dict(author=dict(kind='agent', principal='synthetic-author', team='engineering'),
                        reviewer=dict(kind='agent', principal='synthetic-reviewer', team='engineering'))
        if human:
            bindings['reviewer'] = dict(kind='human', principal='founder', team=None)
        request = parse_request(dict(format='workflow-activation-request@4' if artifact else 'workflow-activation-request@3', operation_key='synthetic-activation',
            instance_id='doc-instance', expected_activation_revision=0,
            template=dict(identity_id=template.identity_id, version=1, definition_digest=template.definition_digest),
            authority=authority, scope=dict(brief='Synthetic retained H prefix'), bindings=bindings,
            eligible_replacements=dict(author=[], reviewer=[]),
            allowed_actions=([action.replace('document', 'artifact') for action in
                ['draft-document', 'stage-immutable-document', 'submit-immutable-document']] if artifact else
                ['draft-document', 'stage-immutable-document', 'submit-immutable-document']) +
                ['collect-review', 'approve-planning-input', 'return-to-author'], inputs=[],
            **(dict(repository_access=None) if artifact else {}))).model_dump(by_alias=True)
        authorization = _authorization(org_slug='scratch', instance_id='doc-instance',
            activation_id=activation['id'], task_id=intent['task_id'], request=request,
            request_digest=intent['request_digest'], template=template, timestamp=stamp)
        binding = _binding(instance_id='doc-instance', activation_id=activation['id'],
            authorization_id='doc-authority', template=template, request=request)
        context = _context(org_slug='scratch', request=request, snapshot=snapshot, template=template,
            inputs=[], authorization=authorization, authorization_id='doc-authority', binding=binding,
            binding_id='doc-binding', intent_id=intent['id'])
        for table, identity, column, value in (
                ('workflow_authorization_revisions', 'doc-authority', 'authority', authorization),
                ('workflow_binding_snapshots', 'doc-binding', 'binding', binding),
                ('workflow_contexts', 'doc-context', 'context', context)):
            value_bytes = encode(value)
            update(table, identity, **{column + '_bytes': value_bytes, column + '_digest': digest(value_bytes)})
        intent['authority_digest'] = authority['snapshot_digest']
        update('workflow_instances', 'doc-instance', owner_principal=intent['admission_principal'])
        update('workflow_draft_dispatch_intents', intent['id'], authority_digest=intent['authority_digest'])
        update('workflow_activations', activation['id'], authority_digest=intent['authority_digest'])
        immutable = {key: value.hex() if type(value) is bytes else value for key, value in intent.items()
                     if key not in _DRAFT_MUTABLE}
        # Preserve the original complete native lifecycle while changing its
        # immutable authority pin. Rehash every successor, not just the last.
        previous_digest = None
        generation = None
        for event in list(conn.execute('SELECT * FROM workflow_draft_dispatch_events ORDER BY event_seq')):
            payload = json.loads(event['event_bytes'])
            payload.update(intent=immutable, previous_digest=previous_digest)
            event_bytes = encode(payload)
            update('workflow_draft_dispatch_events', event['id'],
                   event_bytes=event_bytes, event_digest=digest(event_bytes))
            previous_digest = digest(event_bytes)
            if event['event_kind'] == 'running':
                generation = dict(org_slug='scratch', task_id=intent['task_id'],
                    principal=intent['assigned_principal'], activation_revision=1,
                    assignment_generation=1, attempt_sequence=1, host_execution_key=intent['host_execution_key'],
                    **payload['after'])
        assert generation is not None and generation['final_result_id'] is None
        candidate = b'Synthetic staged H immutable document'
        family = 2 if artifact else 1
        command = dict(format=f'workflow-author-handoff-command@{family}', operation_key='synthetic-handoff',
            activation_id=activation['id'], expected_revision=1,
            submission_bytes_base64=base64.b64encode(candidate).decode('ascii'), submission_digest=digest(candidate),
            **(dict(artifact_kind='document') if artifact else {}))
        identity = 'workflow-author-handoff:' + digest(encode(['scratch', 'synthetic-author', command['operation_key']]))
        submission = 'workflow-submission:' + digest(encode(['doc-instance', 1, 'scratch',
            'synthetic-author', command['operation_key'], f'workflow-author-handoff@{family}']))
        request_bytes = encode(dict(command, format=f'workflow-author-handoff-request@{family}',
            handoff_id=identity, submission_id=submission, instance_id='doc-instance', revision=1))
        row = dict(id=identity, intent_id=intent['id'], instance_id='doc-instance', org_slug='scratch',
            principal='synthetic-author', source_task_id=intent['task_id'], source_session_id='doc-session',
            operation_key=command['operation_key'], revision=1, submission_id=submission,
            candidate_bytes=candidate, candidate_digest=digest(candidate), request_bytes=request_bytes,
            request_digest=digest(request_bytes), created_at=stamp)
        pins = dict(template=dict(identity_id=template.identity_id, version_id=template.version_id, version=1,
            definition_digest=template.definition_digest, compiler_pin=template.compiler_pin,
            validator_pin=template.validator_pin, source_pin=template.source_pin),
            activation=dict(id=activation['id'], revision=1, request_digest=activation['request_digest']))
        contract = _review_contract(template)
        for name, identity, value_bytes in (
                ('authorization', 'doc-authority', encode(authorization)),
                ('binding', 'doc-binding', encode(binding)), ('context', 'doc-context', encode(context)),
                ('scope', intent['task_id'], intent['task_scope_bytes']),
                ('contract', template.version_id, encode(contract))):
            pins[name] = dict(id=identity, bytes_hex=value_bytes.hex(), digest=digest(value_bytes))
        mirror = [dict(kind='dependency', org_namespace='org/scratch', profile_name='synthetic-profile',
                       consumer_identity='synthetic-author', bound_generation=1, state='active'),
                  dict(kind='store', profile_name='synthetic-profile', generation=1,
                       profile_digest=profile_digest, state='active'),
                  dict(kind='registry', profile_name='synthetic-profile', published_generation=1)]
        proof = dict(format=f'workflow-author-handoff@{family}',
            handoff={key: value.hex() if type(value) is bytes else value for key, value in row.items()},
            client_request=command, source_intent=immutable, source_generation=generation, pins=pins,
            admission_authority=dict(ready=dict(**authority, snapshot_bytes_hex=raw.hex()), pointer=pointer,
                profiles=[dict(name='synthetic-profile', digest=profile_digest)], mirror=mirror,
                cutover='enabled', actions=(['stage-immutable-artifact', 'submit-immutable-artifact'] if artifact
                                           else ['stage-immutable-document', 'submit-immutable-document'])),
            **(dict(repository_resolution=None) if artifact else {}))
        row.update(admission_bytes=encode(proof), admission_digest=digest(encode(proof)))
        insert('workflow_author_handoffs', row)
        conn.commit()
        assert read_sequential_history(conn, expected_org_slug='scratch').status == 'storage readable'
        assert conn.execute('SELECT count(*) FROM workflow_author_handoffs').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM workflow_submission_operations').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM workflow_rounds').fetchone()[0] == 0

    def populate_handoff_graph(db, *, human=False, artifact=False, revision=False, successor=False):
        import base64
        import hashlib
        from runtime.infrastructure.workflow_schema import _DRAFT_MUTABLE
        from runtime.workflows.activation import _id
        from runtime.workflows.submissions import review_scope, review_brief, validate_handoff_graph
        from runtime.workflows.review_receipts import _receipt_rows

        number = 2 if successor else 1
        if successor:
            populate_revision(db, artifact=artifact, human=human, completed=True)
        else:
            populate_author_handoff(db, human=human, artifact=artifact, revision=revision)
        conn = db._conn
        encode = seq_history._bytes
        digest = lambda raw: hashlib.sha256(raw).hexdigest()
        stamp = '2026-11-14T00:00:00.000000Z'
        def insert(table, row):
            if successor and table == 'workflow_current_assignments':
                conn.execute('DELETE FROM workflow_current_assignments WHERE instance_id=? AND role_key=?',
                             (row['instance_id'], row['role_key']))
            conn.execute('INSERT INTO ' + table + ' (' + ','.join(row) + ') VALUES (' +
                ','.join('?' for _ in row) + ')', tuple(row.values()))
        if successor:
            from runtime.workflows.revisions import review_row
            operation = dict(conn.execute('SELECT * FROM workflow_document_revision_operations').fetchone())
            handoff = review_row(conn, operation)
            proof = json.loads(operation['admission_bytes'])
            intent = dict(conn.execute('SELECT * FROM workflow_draft_dispatch_intents WHERE id=?', (operation['draft_intent_id'],)).fetchone())
            source = dict(conn.execute('SELECT * FROM task_results WHERE id=?', (intent['final_result_id'],)).fetchone())
            submission = dict(conn.execute('SELECT * FROM workflow_submissions WHERE revision=2').fetchone())
            contribution = dict(principal=source['agent'], source_task_id=source['task_id'],
                source_session_id=source['session_id'], source_result_id=str(source['id']), contribution_kind='author')
            insert('workflow_submission_contributors', dict(submission_id=submission['id'], **contribution))
            insert('workflow_instance_contributors', dict(instance_id=handoff['instance_id'], **contribution))
        else:
            handoff = dict(conn.execute('SELECT * FROM workflow_author_handoffs').fetchone())
            proof = json.loads(handoff['admission_bytes'])
            intent = dict(conn.execute('SELECT * FROM workflow_draft_dispatch_intents').fetchone())
            source = dict(conn.execute('SELECT * FROM task_results WHERE id=?', (intent['final_result_id'],)).fetchone())
            submission = dict(id=handoff['submission_id'], instance_id=handoff['instance_id'], revision=number,
                submission_bytes=handoff['candidate_bytes'], submission_digest=handoff['candidate_digest'],
                storage_ref=None, source_task_id=source['task_id'], source_session_id=source['session_id'],
                source_result_id=None, author_principal=source['agent'])
            insert('workflow_submissions', submission)
            operation = dict(submission_id=submission['id'], instance_id=handoff['instance_id'], org_slug='scratch',
                principal=source['agent'], source_task_id=source['task_id'], source_session_id=source['session_id'],
                operation_key=handoff['operation_key'], request_digest=handoff['request_digest'], created_at=stamp)
            provenance = encode(dict(format='workflow-submission-operation@1', operation=operation,
                submission={key: value.hex() if type(value) is bytes else value for key, value in submission.items()},
                source_intent={key: value.hex() if type(value) is bytes else value for key, value in intent.items()
                               if key not in _DRAFT_MUTABLE}, request_bytes=handoff['request_bytes'].hex()))
            insert('workflow_submission_operations', dict(operation,
                provenance_bytes=provenance, provenance_digest=digest(provenance)))
            conn.execute('INSERT INTO workflow_submission_result_links VALUES (?,?,?,?,?,?,?)',
                (submission['id'], source['task_id'], source['session_id'], source['id'], encode(source), digest(encode(source)), stamp))
            contribution = dict(principal=source['agent'], source_task_id=source['task_id'],
                source_session_id=source['session_id'], source_result_id=str(source['id']), contribution_kind='author')
            insert('workflow_submission_contributors', dict(submission_id=submission['id'], **contribution))
            insert('workflow_instance_contributors', dict(instance_id=handoff['instance_id'], **contribution))
        number = handoff['revision']
        generation = 2 if successor else 1
        family = 3 if successor else 2 if artifact else 1
        round_id = _id('workflow-round', handoff['instance_id'], number, submission['id'])
        conn.execute('INSERT INTO workflow_rounds VALUES (?,?,?,?,?)',
            (round_id, handoff['instance_id'], submission['id'], number, 'reviewing'))
        context = json.loads(conn.execute('SELECT context_bytes FROM workflow_contexts WHERE id=?', (intent['context_id'],)).fetchone()[0])
        from runtime.workflows.artifacts import context_contract
        contract = context_contract(context)
        principal = context['binding']['bindings']['reviewer']['principal']
        request_id = _id('workflow-request', round_id, 'reviewer', principal, generation)
        scope = review_scope(row=handoff, proof=proof, context=context, contract=contract, role='reviewer',
            request_id=request_id, round_id=round_id, binding=context['binding']['bindings']['reviewer'], generation=generation)
        request = dict(id=request_id, round_id=round_id, principal=principal, assignment_generation=generation,
            request_scope_bytes=encode(scope), request_scope_digest=digest(encode(scope)),
            status='changes_requested' if revision else 'approved', supersedes_request_id=None)
        insert('workflow_review_requests', request)
        if human:
            from runtime.workflows.review_receipts import _human_proof, _human_receipt_row
            command = dict(format=f'workflow-human-review-command@{family}',
                action='sign-review', operation_key='synthetic-human-review-2' if successor else 'synthetic-human-review', request_id=request_id,
                expected_revision=number, request_scope_digest=request['request_scope_digest'], outcome=request['status'],
                findings_bytes_base64=base64.b64encode(b'Synthetic human findings').decode(),
                findings_digest=digest(b'Synthetic human findings'))
            receipt_proof = _human_proof(request, scope, proof, command, stamp)
            insert('workflow_review_receipts', _human_receipt_row(request, scope, receipt_proof))
            reviewer_tasks = []
        else:
            reviewer_tasks = ['synthetic-review-task-2' if successor else 'synthetic-review-task']
        if not human:
            operation_id = _id('workflow-dispatch', request_id, generation)
            outbox_id = _id('workflow-outbox', operation_id)
            task_id = reviewer_tasks[0]
            session = 'synthetic-review-session-2' if successor else 'synthetic-review-session'
            db.insert_task(TaskRecord(id=task_id, assigned_agent=principal, team='engineering',
                task_type='subtask', brief=review_brief(scope, handoff['candidate_bytes'])))
            conn.execute("UPDATE tasks SET status='completed',current_session_id=? WHERE id=?", (session, task_id))
            result_id = conn.execute('INSERT INTO task_results(task_id,agent,session_id,created_at) VALUES (?,?,?,?)',
                                    (task_id, principal, session, stamp)).lastrowid
            result = dict(conn.execute('SELECT * FROM task_results WHERE id=?', (result_id,)).fetchone())
            accepted = dict(id=result_id, record=result, digest=digest(encode(result)), accepted=1, disposition='accepted')
            insert('workflow_dispatch_operations', dict(id=operation_id, org_slug='scratch', principal=principal,
                operation_key=request_id, request_digest=request['request_scope_digest'], instance_id=handoff['instance_id'],
                round_id=round_id, request_id=request_id, state='completed', created_at=stamp))
            outbox = dict(id=outbox_id, operation_id=operation_id, request_id=request_id,
                effect_key='workflow-review:' + request_id, authority_namespace=intent['authority_namespace'],
                authority_generation=intent['authority_generation'], authority_digest=intent['authority_digest'],
                artifact_revision=number, state='queued', claim_token=None, claim_owner=None, host_launch_started=0,
                host_execution_key='workflow-review-host:' + operation_id, host_execution_id=None,
                recovery_owner='workflow_recovery', last_error=None, created_at=stamp, updated_at=stamp)
            bridge = dict(request_id=request_id, operation_id=operation_id, instance_id=handoff['instance_id'],
                task_id=task_id, assigned_principal=principal, assignment_generation=generation, session_id=None,
                result_id=None, state='queued', created_at=stamp)
            projections = [dict(outbox=outbox, bridge=bridge)]
            steps = [('claimed', dict(state='claimed', claim_token='synthetic-review-claim',
                                     claim_owner='workflow_dispatcher'), dict(state='claimed')),
                     ('launch_reserved', dict(host_launch_started=1), dict(session_id=session)),
                     ('running', dict(state='running', host_execution_id='a' * 64), dict(state='running')),
                     ('review_staged', {}, {}),
                     ('callback_recorded', {}, dict(result_id=str(result_id))),
                     ('completed', dict(state='completed'), dict(state='completed'))]
            command = dict(format=f'workflow-review-stage-command@{family}', action='stage-review',
                operation_key='synthetic-review-stage-2' if successor else 'synthetic-review-stage', request_id=request_id, expected_revision=number,
                request_scope_digest=request['request_scope_digest'], outcome=request['status'],
                findings_bytes_base64=base64.b64encode(b'Synthetic review findings').decode(),
                findings_digest=digest(b'Synthetic review findings'))
            previous_digest = None
            events = []
            for sequence, (kind, change_outbox, change_bridge) in enumerate(steps, 1):
                before = projections[-1]
                after = dict(outbox=dict(before['outbox'], **change_outbox), bridge=dict(before['bridge'], **change_bridge))
                projections.append(after)
                body = dict(format='workflow-review-stage@1' if kind == 'review_staged' else 'workflow-review-dispatch-event@1',
                    org_slug='scratch', operation_id=operation_id, request_id=request_id, task_id=task_id,
                    event_seq=sequence, event_kind=kind, previous_digest=previous_digest, before=before, after=after,
                    terminal_evidence=dict(host_quiescent=True) if kind == 'completed' else None,
                    result=accepted if kind in ('callback_recorded', 'completed') else None)
                if kind == 'review_staged':
                    body['stage'] = dict(command=command, request_digest=digest(encode(command)), scope=scope,
                                         outbox_id=outbox_id, session_id=session)
                raw = encode(body)
                event = dict(id=operation_id + ':' + str(sequence), operation_id=operation_id, event_seq=sequence,
                    event_kind=kind, state_before=before['outbox']['state'], state_after=after['outbox']['state'],
                    event_bytes=raw, event_digest=digest(raw), created_at=stamp)
                events.append((event, body))
                previous_digest = digest(raw)
            insert('workflow_dispatch_outbox', projections[-1]['outbox'])
            insert('workflow_request_task_bridges', projections[-1]['bridge'])
            conn.execute("UPDATE workflow_task_ownership SET kind='f5',owner_id=? WHERE task_id=?",
                         (request_id, task_id))
            for event, body in events:
                insert('workflow_dispatch_events', event)
            insert('workflow_dispatch_callbacks', dict(id=operation_id + ':callback:' + str(result_id),
                outbox_id=outbox_id, task_id=task_id, session_id=session, result_id=str(result_id),
                result_digest=accepted['digest'], observed_revision=number, accepted=1, disposition='accepted', created_at=stamp))
            insert('workflow_dispatch_effects', dict(id=operation_id + ':host', outbox_id=outbox_id,
                effect_key=outbox['effect_key'], effect_kind='host_launch_observed', task_id=task_id,
                session_id=session, host_execution_id='a' * 64, created_at=stamp))
            stage, terminal = events[3], events[-1]
            receipt_proof = dict(format=f'workflow-agent-review-proof@{family}', scope=scope,
                request_scope_digest=request['request_scope_digest'],
                stage_event=dict(id=stage[0]['id'], digest=stage[0]['event_digest'], body=stage[1]),
                terminal_event=dict(id=terminal[0]['id'], digest=terminal[0]['event_digest']),
                callback=accepted, result_id=result_id, task_id=task_id, session_id=session,
                principal=principal, outcome=request['status'])
            for table, record in _receipt_rows(request, scope, receipt_proof, result, terminal[0]).items():
                insert(table, record)
        graph = dict(submission_id=submission['id'], round_id=round_id,
                     request_ids=[request_id], reviewer_task_ids=reviewer_tasks)
        submitted = encode(dict(event_kind='submitted', instance_id=handoff['instance_id'], revision=number,
                                submission_digest=handoff['candidate_digest'], **graph))
        conn.execute('INSERT INTO workflow_events VALUES (?,?,?,?,?,?,?)',
            (_id('workflow-event', handoff['instance_id'], number, 'submitted'), handoff['instance_id'],
             number, 'submitted', submitted, digest(submitted), stamp))
        conn.execute("UPDATE workflow_instances SET status='reviewing' WHERE id=?", (handoff['instance_id'],))
        conn.commit()
        assert validate_handoff_graph(conn, handoff, proof) == graph
        if not revision:
            receipt = dict(conn.execute('SELECT * FROM workflow_review_receipts WHERE request_id=?', (request_id,)).fetchone())
            assignments = [dict(row) for row in conn.execute('SELECT * FROM workflow_current_assignments')]
            joined = encode(dict(format=f'workflow-review-join@{family}', event_kind='joined', org_slug='scratch',
                instance_id=handoff['instance_id'], round_id=round_id, submission_id=submission['id'], revision=number,
                submission_digest=handoff['candidate_digest'],
                operation=dict(principal='service:workflow_review_join', operation_key=round_id, action='approve-planning-input'),
                pins=proof['pins'], admission_authority=dict(proof['admission_authority'],
                    actions=['collect-review', 'approve-planning-input']),
                receipts=[{key: value.hex() if type(value) is bytes else value for key, value in receipt.items()}],
                assignments=assignments, created_at=stamp))
            joined_id = _id('workflow-event', handoff['instance_id'], number, 'joined')
            conn.execute('INSERT INTO workflow_events VALUES (?,?,?,?,?,?,?)',
                (joined_id, handoff['instance_id'], number, 'joined', joined, digest(joined), stamp))
            conn.execute('INSERT INTO workflow_operation_replays VALUES (?,?,?,?,?,?)',
                ('scratch', 'service:workflow_review_join', round_id, digest(joined), handoff['instance_id'], joined_id))
        conn.execute("UPDATE workflow_rounds SET state='closed'")
        if not revision:
            conn.execute("UPDATE workflow_instances SET status='complete' WHERE id=?", (handoff['instance_id'],))
        conn.commit()
        assert validate_handoff_graph(conn, handoff, proof) == graph
        assert read_sequential_history(conn, expected_org_slug='scratch').status == 'storage readable'
        assert conn.execute('SELECT count(*) FROM workflow_review_receipts').fetchone()[0] == (2 if successor else 1)
        if not human:
            assert [r[0] for r in conn.execute('SELECT event_kind FROM workflow_dispatch_events WHERE operation_id=? ORDER BY event_seq',
                                             (operation_id,))] == [kind for kind, _, _ in steps]
        else:
            assert conn.execute('SELECT count(*) FROM workflow_request_task_bridges').fetchone()[0] == 0
            assert conn.execute('SELECT count(*) FROM workflow_receipt_evidence').fetchone()[0] == 0

    def populate_revision(db, *, artifact=False, human=True, staged=False, completed=False):
        import hashlib
        from runtime.infrastructure.workflow_schema import _DRAFT_MUTABLE, _DRAFT_PROJECTION
        from runtime.workflows.activation import _id
        from runtime.workflows import revisions

        # Stored closed objection -> original author successor, never a live
        # return/admission/task allocator or event/settlement producer.
        populate_handoff_graph(db, human=human, artifact=artifact, revision=True)
        conn = db._conn
        encode = seq_history._bytes
        digest = lambda raw: hashlib.sha256(raw).hexdigest()
        stamp = '2026-11-14T00:00:00.000000Z'
        def one(table):
            return dict(conn.execute('SELECT * FROM ' + table).fetchone())
        def insert(table, row):
            conn.execute('INSERT INTO ' + table + ' (' + ','.join(row) + ') VALUES (' +
                ','.join('?' for _ in row) + ')', tuple(row.values()))
        first = one('workflow_draft_dispatch_intents')
        old = one('workflow_author_handoffs')
        oldproof = json.loads(old['admission_bytes'])
        graph = dict(submission_id=old['submission_id'], round_id=one('workflow_rounds')['id'],
                     request_ids=[one('workflow_review_requests')['id']], reviewer_task_ids=[])
        predecessor, feedback = revisions._predecessor(conn, old, oldproof, graph)
        command = dict(format='workflow-document-revision-command@1', operation_key='synthetic-return',
            instance_id=first['instance_id'], activation_id=first['activation_id'], expected_revision=1,
            predecessor_submission_id=old['submission_id'], predecessor_submission_digest=old['candidate_digest'],
            predecessor_round_id=graph['round_id'], authority=dict(namespace='org/scratch',
                generation=first['authority_generation'], snapshot_digest=first['authority_digest']))
        raw = encode(command)
        operation_id = _id('workflow-document-revision', 'scratch', 'human:founder', command['operation_key'])
        context = revisions._context('scratch', operation_id, one('workflow_contexts'), predecessor, feedback)
        context_raw = encode(context)
        context_id = _id('workflow-context', first['instance_id'], digest(context_raw))
        brief = revisions.revision_brief(context, old['candidate_bytes'])
        task = TaskRecord(id='synthetic-revision-author', assigned_agent=first['assigned_principal'],
                          team='engineering', task_type='subtask', brief=brief)
        db.insert_task(task)
        admission = dict(org_slug='scratch', instance_id=first['instance_id'], activation_id=first['activation_id'],
            activation_revision=1, attempt_sequence=2, predecessor_intent_id=first['id'],
            admission_principal='human:founder', operation_key=command['operation_key'], request_digest=digest(raw))
        intent_id = digest(encode(admission))
        scope = encode(dict(assigned_agent=task.assigned_agent, team=task.team, brief=brief))
        intent = dict(first, id=intent_id, attempt_sequence=2, predecessor_intent_id=first['id'],
            admission_kind='revision', operation_key=command['operation_key'], request_bytes=raw,
            request_digest=digest(raw), task_id=task.id, context_id=context_id, assignment_generation=2,
            task_scope_bytes=scope, task_scope_digest=digest(scope),
            effect_key='workflow-initial-draft:' + first['instance_id'] + ':2',
            host_execution_key='workflow-draft-host:' + intent_id, is_current=1, state='queued',
            cancellation_requested=0, claim_token=None, claim_owner=None, host_launch_started=0,
            host_execution_id=None, session_id=None, final_result_id=None, last_error=None,
            created_at=stamp, updated_at=stamp)
        context_row = dict(id=context_id, binding_snapshot_id=first['binding_snapshot_id'], context_bytes=context_raw,
            context_digest=digest(context_raw), source_kind='document-revision', source_id=operation_id)
        insert('workflow_contexts', context_row)
        # Append the retained first author's retirement without rewriting any
        # predecessor event; its original callback and terminal result survive.
        last = dict(conn.execute('SELECT * FROM workflow_draft_dispatch_events ORDER BY event_seq DESC LIMIT 1').fetchone())
        before = json.loads(last['event_bytes'])['after']
        after = dict(before, is_current=0)
        sequence = last['event_seq'] + 1
        identity = first['id'] + ':' + str(sequence)
        payload = encode(dict(format='workflow-draft-event@1', org_slug='scratch',
            event=dict(id=identity, event_seq=sequence, event_kind='retired', created_at=stamp),
            previous_digest=last['event_digest'], intent=json.loads(last['event_bytes'])['intent'],
            before=before, after=after, terminal_evidence=None, result=json.loads(last['event_bytes'])['result']))
        conn.execute('INSERT INTO workflow_draft_dispatch_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (identity, first['id'], sequence, 'retired', before['state'], after['state'], payload, digest(payload),
             None, None, None, None, None, stamp))
        conn.execute('UPDATE workflow_draft_dispatch_intents SET is_current=0 WHERE id=?', (first['id'],))
        insert('workflow_draft_dispatch_intents', intent)
        conn.execute("UPDATE workflow_task_ownership SET kind='draft',owner_id=? WHERE task_id=?", (intent_id, task.id))
        queued = {key: intent[key] for key in _DRAFT_PROJECTION}
        immutable = {key: value.hex() if type(value) is bytes else value for key, value in intent.items()
                     if key not in _DRAFT_MUTABLE}
        identity = intent_id + ':1'
        payload = encode(dict(format='workflow-draft-event@1', org_slug='scratch',
            event=dict(id=identity, event_seq=1, event_kind='admitted', created_at=stamp),
            previous_digest=None, intent=immutable, before=None, after=queued, terminal_evidence=None, result=None))
        conn.execute('INSERT INTO workflow_draft_dispatch_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (identity, intent_id, 1, 'admitted', None, 'queued', payload, digest(payload), None, None, None, None, None, stamp))
        row = dict(id=operation_id, instance_id=first['instance_id'], org_slug='scratch', principal='human:founder',
            operation_key=command['operation_key'], predecessor_submission_id=old['submission_id'],
            predecessor_round_id=graph['round_id'], expected_revision=1, target_revision=2,
            draft_intent_id=intent_id, activation_id=first['activation_id'],
            binding_snapshot_id=first['binding_snapshot_id'], context_id=context_id,
            authorization_revision_id=oldproof['pins']['authorization']['id'], request_bytes=raw,
            request_digest=digest(raw), created_at=stamp)
        proof = dict(format='workflow-document-revision-admission@1',
            operation={key: value.hex() if type(value) is bytes else value for key, value in row.items()},
            client_request=command, predecessor=predecessor, pins=revisions._pins(oldproof, context_row, intent),
            admission_authority=dict(oldproof['admission_authority'], actions=revisions.revision_actions(context)))
        insert('workflow_document_revision_operations', dict(row, admission_bytes=encode(proof), admission_digest=digest(encode(proof))))
        if staged or completed:
            import base64
            session = 'synthetic-revision-session'
            conn.execute("UPDATE tasks SET status=?,current_session_id=? WHERE id=?",
                         ('completed' if completed else 'in_progress', session, task.id))
            steps = [('claimed', dict(state='claimed', claim_token='synthetic-revision-claim', claim_owner='workflow_dispatcher')),
                     ('launch_reserved', dict(host_launch_started=1)),
                     ('running', dict(state='running', host_execution_id='synthetic-revision-host', session_id=session))]
            accepted = None
            if completed:
                result_id = conn.execute('INSERT INTO task_results(task_id,agent,session_id,created_at) VALUES (?,?,?,?)',
                    (task.id, task.assigned_agent, session, stamp)).lastrowid
                result = dict(conn.execute('SELECT * FROM task_results WHERE id=?', (result_id,)).fetchone())
                accepted = dict(record=result, id=result_id, digest=digest(encode(result)), disposition='accepted', accepted=1)
                steps += [('callback_recorded', dict(final_result_id=result_id)), ('completed', dict(state='completed'))]
            previous, previous_digest = queued, digest(payload)
            for sequence, (kind, change) in enumerate(steps, 2):
                after = dict(previous, **change)
                event_id = intent_id + ':' + str(sequence)
                payload = encode(dict(format='workflow-draft-event@1', org_slug='scratch',
                    event=dict(id=event_id, event_seq=sequence, event_kind=kind, created_at=stamp),
                    previous_digest=previous_digest, intent=immutable, before=previous, after=after,
                    terminal_evidence=dict(host_quiescent=True) if kind == 'completed' else None,
                    result=accepted if kind in ('callback_recorded', 'completed') else None))
                callback = ((session, result_id, accepted['digest'], 1, 'accepted') if kind == 'callback_recorded' else (None,) * 5)
                conn.execute('INSERT INTO workflow_draft_dispatch_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (event_id, intent_id, sequence, kind, previous['state'], after['state'], payload, digest(payload), *callback, stamp))
                previous, previous_digest = after, digest(payload)
            intent.update(previous)
            conn.execute('UPDATE workflow_draft_dispatch_intents SET ' + ','.join(key + '=?' for key in _DRAFT_PROJECTION) + ' WHERE id=?',
                         tuple(intent[key] for key in _DRAFT_PROJECTION) + (intent_id,))
            candidate = b'Synthetic immutable revised document'
            command = dict(format='workflow-author-revision-command@1', operation_key='synthetic-revision-stage',
                revision_operation_id=operation_id, expected_revision=2, artifact_kind='document',
                submission_bytes_base64=base64.b64encode(candidate).decode(), submission_digest=digest(candidate))
            submission_id = _id('workflow-submission', first['instance_id'], 2, 'scratch', task.assigned_agent,
                                command['operation_key'], 'workflow-author-revision@1')
            request = encode(dict(command, format='workflow-author-revision-request@1',
                submission_id=submission_id, instance_id=first['instance_id'], revision=2))
            submission = dict(id=submission_id, instance_id=first['instance_id'], revision=2,
                submission_bytes=candidate, submission_digest=digest(candidate), storage_ref=None,
                source_task_id=task.id, source_session_id=session, source_result_id=None, author_principal=task.assigned_agent)
            insert('workflow_submissions', submission)
            operation = dict(submission_id=submission_id, instance_id=first['instance_id'], org_slug='scratch',
                principal=task.assigned_agent, source_task_id=task.id, source_session_id=session,
                operation_key=command['operation_key'], request_digest=digest(request), created_at=stamp)
            provenance = encode(dict(format='workflow-submission-operation@1', operation=operation,
                submission={key: value.hex() if type(value) is bytes else value for key, value in submission.items()},
                source_intent=immutable, request_bytes=request.hex()))
            insert('workflow_submission_operations', dict(operation, provenance_bytes=provenance, provenance_digest=digest(provenance)))
            if completed:
                conn.execute('INSERT INTO workflow_submission_result_links VALUES (?,?,?,?,?,?,?)',
                    (submission_id, task.id, session, result_id, encode(result), accepted['digest'], stamp))
        conn.commit()
        if not completed:
            assert read_sequential_history(conn, expected_org_slug='scratch').status == 'storage readable'
        assert conn.execute('SELECT count(*) FROM workflow_document_revision_operations').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM workflow_draft_dispatch_intents').fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM workflow_review_requests WHERE status='changes_requested'").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM workflow_contexts WHERE source_kind='document-revision'").fetchone()[0] == 1

    def populate_coding_authority(db, *, setup):
        import base64
        import hashlib
        from runtime.workflows._coding_release_history import publication_rows
        from runtime.workflows.release_templates import read_coding_release

        # Fixed synthetic archival input, following the supported coding-reader
        # fixture grammar. No setup service, current human/role grant, local
        # repository, admission action, task, run or native settlement exists.
        conn = db._conn
        encode = seq_history._bytes
        digest = lambda raw: hashlib.sha256(raw).hexdigest()
        b64 = lambda raw: base64.b64encode(raw).decode('ascii')
        stamp = '2026-11-14T00:00:00.000000Z'
        namespace = 'org/scratch'
        origin = 'https://example.invalid/synthetic.git'
        manager = dict(principal='synthetic_manager', team='synthetic')
        snapshot = dict(schema_version=1, org_slug='scratch', agents=[], teams=[],
            reviewer_agents=[], machine_global_profiles=[], active_policy_selectors=[])
        if setup:
            snapshot.update(agents=[dict(name=manager['principal'], role='manager',
                team=manager['team'], status='active', executor='synthetic', model=None,
                repos=dict(synthetic=origin), allow_rules=[],
                description_digest=digest(b''), system_prompt_digest=digest(b''))],
                teams=[dict(name=manager['team'], manager=manager['principal'], workers=[])],
                active_policy_selectors=[dict(team=manager['team'], selector=None)])
        raw = encode(snapshot)
        pin = dict(namespace=namespace, generation=1, snapshot_digest=digest(raw))
        pointer = dict(current_generation=1, journal_id='synthetic-coding-journal',
            snapshot_digest=digest(raw), state='ready', profile_fence=0)
        def insert(table, row):
            conn.execute('INSERT INTO ' + table + ' (' + ','.join(row) + ') VALUES (' +
                ','.join('?' for _ in row) + ')', tuple(row.values()))
        insert('workflow_publication_journals', dict(id=pointer['journal_id'], **pin,
            expected_generation=0, snapshot_bytes=raw, publisher='human:founder',
            publisher_invocation='synthetic-not-live', profile_fence=0,
            state='cache_installed', recovery_owner='synthetic-history', file_phase_owner=None))
        insert('workflow_authority_pointers', dict(namespace=namespace, **pointer))
        def capture(command, identity):
            body = encode(command)
            admission = dict(id=identity, namespace=namespace, generation=1,
                request_digest=digest(body), admitted_by='human:founder')
            saved = dict(pin=pin, admission_record_id=identity, snapshot_bytes_base64=b64(raw),
                grant_bytes_base64=b64(body), grant_digest=digest(body),
                profile_evidence=dict(ready=dict(**pin, snapshot_bytes_hex=raw.hex()),
                    pointer=pointer, profiles=[], mirror=[]))
            return body, admission, saved
        if not setup:
            command, admission, saved = capture(dict(format='workflow-coding-release-import-command@1',
                operation_key='synthetic-import', expected_current_version=0), 'synthetic-import-admission')
            provenance = dict(format='workflow-coding-release-import-provenance@1', org_slug='scratch',
                publisher=dict(principal_kind='human', principal_id='founder', proof_kind='founder_bearer'),
                command_bytes_base64=b64(command), command_digest=digest(command),
                admission=dict(record=admission, capture=saved), created_at=stamp)
            for table, row in publication_rows(org_slug='scratch', definition=read_coding_release(),
                                               provenance=provenance).rows:
                insert(table, row)
        else:
            repository = 'workflow-repository:' + digest(encode(['scratch', 'synthetic', digest(origin.encode())]))
            scope = dict(repository_id=repository, manager_selector=manager,
                actions=['start-coding'], source_rule='current-assigned-manager-decision@1',
                base_rule='manager-select-local-base@1', scope=dict(object_formats=['sha1'],
                    store_owners=[manager['principal']], path_prefixes=['runtime'], commands=[],
                    recipient_rule='mapping-eligible-current@1', provider_rule=None,
                    original_limit_rule='lower-native-original@1'))
            key = digest(encode(['scratch', repository, manager]))
            for revision, mode in enumerate(('issue', 'withdraw'), 1):
                command, admission, saved = capture(dict(format='workflow-coding-capability-setup-command@1',
                    operation_key='synthetic-' + mode, setup_key=key,
                    expected_authorization_revision=revision - 1, mode=mode,
                    scope=scope if mode == 'issue' else None), 'synthetic-' + mode + '-admission')
                issued = (dict(format='workflow-coding-setup-authority@1', id='synthetic-setup',
                    org_slug='scratch', **scope, authority=saved, created_at=stamp) if mode == 'issue' else None)
                body = encode(dict(format='workflow-coding-capability-issuance@1',
                    command_bytes_base64=b64(command), command_digest=digest(command),
                    issuer=dict(principal_kind='human', principal_id='founder', proof_kind='founder_bearer'),
                    mode=mode, setup=issued, admission=dict(record=admission, capture=saved), created_at=stamp))
                insert('workflow_admission_records', admission)
                insert('workflow_authorization_revisions', dict(id='synthetic-' + mode,
                    namespace=namespace + '/coding-setup/' + key, revision=revision,
                    authority_bytes=body, authority_digest=digest(body),
                    source_pin='coding-capability-issuer@1', created_at=stamp))
            insert('workflow_active_authorizations', dict(namespace=namespace + '/coding-setup/' + key,
                authorization_revision_id='synthetic-withdraw'))
        conn.commit()
        history = read_sequential_history(conn, expected_org_slug='scratch')
        assert history.status == 'storage readable'
        assert (history.retained[1].release is not None) == (not setup)
        if setup:
            from runtime.workflows.coding_authority_history import read_setup_storage
            with _schema_snapshot(conn):
                storage = read_setup_storage(conn, expected_org_slug='scratch')
            assert len(storage.issuances) == 2 and storage.pointers[0][1] == 'synthetic-withdraw'
        else:
            assert history.retained[1].imported is not None

    def populate_coding_mapping(db):
        import base64
        import hashlib
        from runtime.workflows._coding_registered_release import registration_rows
        from runtime.workflows.release_templates import read_coding_release
        from runtime.workflows._coding_settings_history import snapshot_for

        # Inert native @1 prefix followed by explicit and disabled-retain human
        # @2 publications. These are archival SQL inputs, never service calls,
        # live role assignments, current Allow, source mints or Coding runs.
        conn = db._conn
        encode = seq_history._bytes
        digest = lambda raw: hashlib.sha256(raw).hexdigest()
        b64 = lambda raw: base64.b64encode(raw).decode('ascii')
        stamp = '2026-11-14T00:00:00.000000Z'
        def insert(table, row):
            conn.execute('INSERT INTO ' + table + ' (' + ','.join(row) + ') VALUES (' +
                         ','.join('?' for _ in row) + ')', tuple(row.values()))
        for table, row in registration_rows('scratch', read_coding_release(), stamp).rows:
            insert(table, row)
        roles = {role: dict(principal='synthetic_' + role, team='engineering')
                 for role in ('agent_manager', 'code_reviewer', 'implementer', 'qa_engineer')}
        origin = 'https://example.invalid/synthetic.git'
        reference = dict(repository_id='workflow-repository:' + digest(encode(
            ['scratch', 'synthetic', digest(origin.encode())])), repo_key='synthetic',
            origin_digest=digest(origin.encode()), object_format='sha1')
        snapshot = dict(schema_version=1, org_slug='scratch',
            agents=[dict(name=binding['principal'], role='manager' if role == 'agent_manager' else 'worker',
                team='engineering', status='active', executor='synthetic', model=None,
                repos=dict(synthetic=origin), allow_rules=[], description_digest=digest(b''),
                system_prompt_digest=digest(b'')) for role, binding in roles.items()],
            teams=[dict(name='engineering', manager=roles['agent_manager']['principal'],
                workers=[roles[role]['principal'] for role in roles if role != 'agent_manager'])],
            reviewer_agents=[], machine_global_profiles=[],
            active_policy_selectors=[dict(team='engineering', selector=None)])
        raw = encode(snapshot)
        pin = dict(namespace='org/scratch', generation=1, snapshot_digest=digest(raw))
        pointer = dict(current_generation=1, journal_id='synthetic-mapping-journal',
            snapshot_digest=digest(raw), state='ready', profile_fence=0)
        insert('workflow_publication_journals', dict(id=pointer['journal_id'], **pin,
            expected_generation=0, snapshot_bytes=raw, publisher='human:founder',
            publisher_invocation='synthetic-not-live', profile_fence=0, state='cache_installed',
            recovery_owner='synthetic-history', file_phase_owner=None))
        insert('workflow_authority_pointers', dict(namespace='org/scratch', **pointer))
        mapping = dict(release=dict(identity='release/happyranch/coding', version=1,
            definition_digest='6ee02be98738d3c95fe584b165abe07d0dbef66e510133c117ee0d62c953cad8'),
            roles=roles, eligible_replacements={role: [] for role in roles},
            repository_references=[reference], authority=pin)
        manager = roles['agent_manager']['principal']
        db.insert_task(TaskRecord(id='synthetic-mapping-source', assigned_agent=manager,
            team='engineering', brief='Synthetic retained mapping source', status='in_progress',
            current_session_id='synthetic-mapping-session', orchestration_step_count=1))
        conn.execute('UPDATE tasks SET created_at=?,updated_at=? WHERE id=?',
                     (stamp, stamp, 'synthetic-mapping-source'))
        start = conn.execute('INSERT INTO audit_log(task_id,agent,action,payload,timestamp) VALUES(?,?,?,?,?)',
            ('synthetic-mapping-source', manager, 'session_start', encode(dict(workspace='synthetic',
                session_id='synthetic-mapping-session', invocation_purpose='manager_decision',
                executor='synthetic', model=None)).decode(), stamp)).lastrowid
        cursor = conn.execute("SELECT * FROM tasks WHERE id='synthetic-mapping-source'")
        task = dict(zip((column[0] for column in cursor.description), cursor.fetchone(), strict=True))
        from runtime.workflows.coding_source_history import _TASK_COLUMNS
        columns = dict(columns=[dict(name=key, storage_class='null' if value is None else
            'integer' if type(value) is int else 'text',
            value=b64(value.encode()) if type(value) is str else value) for key, value in ((key, task[key]) for key in _TASK_COLUMNS)])
        previous = None
        explicit = None
        for generation in (1, 2, 3):
            human = generation != 1
            at = f'2026-11-14T00:00:0{generation}.000000Z'
            identity = 'coding-mapping:' + f'{generation:032x}'
            admission_id = ('coding-settings-admission:' if human else 'synthetic-mapping-admission:') + f'{generation:032x}'
            command = dict(format='workflow-coding-settings-command@1' if human else 'workflow-coding-mapping-command@2',
                operation_key='synthetic-mapping-' + str(generation), mapping=mapping,
                expected_generation=generation - 2 if human else 0)
            if human:
                command.update(org_slug='scratch', expected_mapping_generation=generation - 1,
                               admission='disabled' if generation == 3 else 'explicit')
            body = encode(command)
            capture = dict(pin=pin, admission_record_id=admission_id, snapshot_bytes_base64=b64(raw),
                grant_bytes_base64=b64(body), grant_digest=digest(body),
                profile_evidence=dict(ready=dict(**pin, snapshot_bytes_hex=raw.hex()),
                                      pointer=pointer, profiles=[], mirror=[]))
            issuer = (dict(kind='human', principal='founder', proof_kind='founder_bearer') if human else
                dict(kind='agent', principal=manager, task_id=task['id'],
                     session_id='synthetic-mapping-session', result_id=None))
            record = dict(format='workflow-coding-agent-read-role-record@2' if human else
                'workflow-coding-agent-read-role-record@1', org_slug='scratch',
                command_bytes_base64=b64(body), command_digest=digest(body), issuer=issuer,
                authority=capture, mapping=mapping, created_at=at)
            if human:
                record['settings'] = dict(generation=generation - 1, admission=command['admission'],
                    previous_snapshot_digest=None if previous is None else digest(encode(previous)))
            encoded = encode(record)
            values = (identity, 'scratch', 'release/happyranch/coding', generation,
                      'human:founder' if human else 'agent:' + manager,
                      command['operation_key'], digest(body), encoded, digest(encoded), at)
            conn.execute('INSERT INTO workflow_coding_mappings VALUES(?,?,?,?,?,?,?,?,?,?)', values)
            insert('workflow_admission_records', dict(id=admission_id, namespace='org/scratch',
                generation=1, request_digest=digest(body), admitted_by=values[4]))
            if human:
                after = snapshot_for(values, record, command, previous)
                publication = dict(format='workflow-coding-settings-publication@1', org_slug='scratch',
                    principal='human:founder', operation_key=command['operation_key'], request_digest=digest(body),
                    command_bytes_base64=b64(body), mapping_id=identity, mapping_generation=generation,
                    mapping_digest=values[8], admission_id=admission_id, before=previous, after=after)
                config = dict(section='coding', tiers=sorted(k for k in after if previous is None or
                    previous[k] != after[k]), before=previous, after=after)
                for action, payload in (('coding_settings_publication', publication), ('org_config_write', config)):
                    conn.execute('INSERT INTO audit_log(task_id,agent,action,payload,timestamp) VALUES(?,?,?,?,?)',
                                 ('config:coding', 'founder', action, encode(payload).decode(), at))
                previous = after
                if generation == 2:
                    explicit = after
            else:
                payload = dict(format='workflow-coding-source-admission@1', phase='source', org_slug='scratch',
                    source_admission_id=admission_id, native_owner=dict(table='workflow_coding_mappings', id=identity),
                    command_bytes_base64=b64(body), task_record=columns, ancestor_records=[],
                    session_start_audit_id=start, result=None, callback_admission_audit_id=None,
                    native_claim=None, owner_invocation_id='synthetic-mapping-invocation',
                    budgets=dict(source_step=1, source_revision=0, session_timeout_seconds=None,
                        max_revise_rounds=3, coding_attempt_limit=64, coding_event_limit=4096,
                        coding_revision_limit=3, coding_same_principal_retry_limit=1), observed_at=at)
                conn.execute('INSERT INTO audit_log(task_id,agent,action,payload,timestamp) VALUES(?,?,?,?,?)',
                             (task['id'], manager, 'coding_source_admission', encode(payload).decode(), at))
        conn.execute('INSERT INTO org_settings(section,value_json,updated_at,updated_by) VALUES(?,?,?,?)',
                     ('coding', encode(previous).decode(), previous['published_at'], 'founder'))
        conn.commit()
        history = read_sequential_history(conn, expected_org_slug='scratch')
        assert history.status == 'storage readable'
        inventory = history.retained[1]
        assert len(inventory.mappings.mappings) == 3
        assert inventory.mappings.selected.source_capture.payload['after']['admission'] == 'disabled'
        return dict(mapping=mapping, explicit=explicit, capture=capture, snapshot=snapshot, pin=pin,
                    pointer=pointer, stamp=stamp)


    def populate_coding_source(db):
        import base64
        import hashlib
        from runtime.workflows.coding_saved_records import ROLES
        saved = populate_coding_mapping(db)
        conn, encode = db._conn, seq_history._bytes
        digest = lambda raw: hashlib.sha256(raw).hexdigest()
        b64 = lambda raw: base64.b64encode(raw).decode('ascii')
        roles, pin = saved['mapping']['roles'], saved['pin']
        manager = roles['agent_manager']['principal']
        repository = saved['mapping']['repository_references'][0]
        issuer = dict(kind='agent', principal=manager, task_id='synthetic-mapping-source',
                      session_id='synthetic-mapping-session', result_id=None)
        def insert(table, row):
            conn.execute('INSERT INTO ' + table + ' (' + ','.join(row) + ') VALUES (' +
                         ','.join('?' for _ in row) + ')', tuple(row.values()))
        def capture(grant, admission):
            raw = encode(grant)
            return dict(saved['capture'], admission_record_id=admission,
                        grant_bytes_base64=b64(raw), grant_digest=digest(raw))
        # Retain only the native mapping prefix. This separate private fixture
        # supplies a custody mapping and a nonallocating capability; no run,
        # allocator, repository operation or live setup service is called.
        conn.execute('DELETE FROM org_settings WHERE section=\'coding\'')
        conn.execute("DELETE FROM audit_log WHERE action IN ('coding_settings_publication','org_config_write')")
        conn.execute('DELETE FROM workflow_coding_mappings WHERE generation>1')
        conn.execute("DELETE FROM workflow_admission_records WHERE id LIKE 'coding-settings-admission:%'")
        scope = dict(repository_id=repository['repository_id'], manager_selector=roles['agent_manager'],
            actions=['import-release-coding', 'publish-coding-mapping', 'adopt-local-store'],
            source_rule='current-assigned-manager-decision@1', base_rule='manager-select-local-base@1',
            scope=dict(object_formats=['sha1'], store_owners=[manager], path_prefixes=['runtime'],
                commands=[], recipient_rule='mapping-eligible-current@1', provider_rule=None,
                original_limit_rule='lower-native-original@1'))
        setup_key = digest(encode(['scratch', repository['repository_id'], roles['agent_manager']]))
        command = dict(format='workflow-coding-capability-setup-command@1', operation_key='synthetic-source-setup',
            setup_key=setup_key, expected_authorization_revision=0, mode='issue', scope=scope)
        authority = capture(command, 'synthetic-source-setup-admission')
        admission = dict(id=authority['admission_record_id'], namespace='org/scratch', generation=1,
                         request_digest=digest(encode(command)), admitted_by='human:founder')
        setup = dict(format='workflow-coding-setup-authority@1', id='synthetic-source-setup',
            org_slug='scratch', **scope, authority=authority, created_at=saved['stamp'])
        issuance = dict(format='workflow-coding-capability-issuance@1', command_bytes_base64=b64(encode(command)),
            command_digest=digest(encode(command)), issuer=dict(principal_kind='human', principal_id='founder',
                proof_kind='founder_bearer'), mode='issue', setup=setup,
            admission=dict(record=admission, capture=authority), created_at=saved['stamp'])
        insert('workflow_admission_records', admission)
        insert('workflow_authorization_revisions', dict(id='synthetic-source-issue',
            namespace='org/scratch/coding-setup/' + setup_key, revision=1, authority_bytes=encode(issuance),
            authority_digest=digest(encode(issuance)), source_pin='coding-capability-issuer@1', created_at=saved['stamp']))
        insert('workflow_active_authorizations', dict(namespace='org/scratch/coding-setup/' + setup_key,
            authorization_revision_id='synthetic-source-issue'))
        binding = dict(format='workflow-repository-binding@1', action='read-repository-artifact', org_slug='scratch',
            repository_id=repository['repository_id'], repo_key=repository['repo_key'], origin_digest=repository['origin_digest'],
            store_owner=manager, authority=pin,
            recipients=[dict(role=role, kind='agent', **roles[role]) for role in ROLES], reader='local-git-objects@1')
        store = dict(repo_device=1, repo_inode=2, git_device=1, git_inode=3,
                     objects_device=1, objects_inode=4, config_digest=digest(b'synthetic'))
        adoption = dict(format='workflow-repository-adoption@1', operation_id='synthetic-adoption',
            store_identity_digest=digest(encode(store)), adopted_by=manager, authority=pin, policy='adopted-local-store@1')
        proof = dict(format='workflow-repository-adoption-proof@1', repository_id=repository['repository_id'],
            operation_id=adoption['operation_id'], issuer=issuer, authority=pin, store_identity=store,
            configured_origin_digest=repository['origin_digest'], observed_origin_digest=repository['origin_digest'],
            object_format='sha1', policy=adoption['policy'], observed_at=saved['stamp'],
            checks={key: True for key in ('primary_store','no_follow','ordinary_git_directory','no_alternates',
                                          'no_promisor','no_shallow','no_replace_or_graft','no_helpers')})
        mapping = dict(format='workflow-coding-mapping@1', release=saved['mapping']['release'], roles=roles,
            eligible_replacements=saved['mapping']['eligible_replacements'], authority=pin,
            repository_bindings=[dict(binding=binding, adoption=adoption)])
        command = dict(format='workflow-coding-mapping-command@1', operation_key='synthetic-mapping-1',
            expected_generation=0, mapping=dict(release=mapping['release'], roles=roles,
                eligible_replacements=mapping['eligible_replacements'], authority=pin,
                repository_selectors=[dict(repository_id=repository['repository_id'], store_owner=manager,
                    repo_key='synthetic', policy=adoption['policy'])]))
        authority = capture(dict(format='workflow-coding-mapping-grant@1', setups=[setup]),
                            'synthetic-mapping-admission:' + f'{1:032x}')
        record = dict(format='workflow-coding-mapping-record@1', command_bytes_base64=b64(encode(command)),
            command_digest=digest(encode(command)), issuer=issuer, authority=authority,
            adoptions=[proof], mapping=mapping, created_at='2026-11-14T00:00:01.000000Z')
        conn.execute('UPDATE workflow_coding_mappings SET request_digest=?,mapping_bytes=?,mapping_digest=? WHERE generation=1',
                     (digest(encode(command)), encode(record), digest(encode(record))))
        conn.execute('UPDATE workflow_admission_records SET request_digest=? WHERE id=?',
                     (digest(encode(command)), authority['admission_record_id']))
        old_capture = json.loads(conn.execute("SELECT payload FROM audit_log WHERE action='coding_source_admission'").fetchone()[0])
        old_capture['command_bytes_base64'] = b64(encode(command))
        conn.execute("UPDATE audit_log SET payload=? WHERE action='coding_source_admission'", (encode(old_capture).decode(),))
        # Keep the source task distinct from the mapping issuer so source
        # ancestry/result corruptions reach their own native reader.
        from runtime.workflows.coding_source_history import _TASK_COLUMNS
        issuer = dict(issuer, task_id='synthetic-capability-source', session_id='synthetic-capability-session')
        ancestor_id = 'synthetic-source-ancestor'
        for task_id, parent, status in ((ancestor_id, None, 'completed'),
                                       (issuer['task_id'], ancestor_id, 'in_progress')):
            db.insert_task(TaskRecord(id=task_id, assigned_agent=manager, team='engineering',
                brief='Synthetic retained source ' + task_id, status=status, parent_task_id=parent,
                current_session_id=issuer['session_id'] if parent else None,
                orchestration_step_count=1 if parent else 0))
            conn.execute('UPDATE tasks SET created_at=?,updated_at=?,completed_at=? WHERE id=?',
                         (saved['stamp'], saved['stamp'], saved['stamp'] if status == 'completed' else None, task_id))
        start_id = conn.execute('INSERT INTO audit_log(task_id,agent,action,payload,timestamp) VALUES(?,?,?,?,?)',
            (issuer['task_id'], manager, 'session_start', encode(dict(workspace='synthetic',
                session_id=issuer['session_id'], invocation_purpose='manager_decision', executor='synthetic', model=None)).decode(),
                saved['stamp'])).lastrowid
        def captured_task(task_id):
            values = conn.execute('SELECT ' + ','.join(_TASK_COLUMNS) + ' FROM tasks WHERE id=?', (task_id,)).fetchone()
            return dict(columns=[dict(name=key,
                storage_class='null' if value is None else 'integer' if type(value) is int else 'text',
                value=b64(value.encode()) if type(value) is str else value)
                for key, value in zip(_TASK_COLUMNS, values, strict=True)])
        old_capture = dict(old_capture, task_record=captured_task(issuer['task_id']),
            ancestor_records=[captured_task(ancestor_id)], session_start_audit_id=start_id,
            owner_invocation_id='synthetic-source-invocation')
        mapping_row = conn.execute('SELECT id,generation,mapping_digest FROM workflow_coding_mappings').fetchone()
        at = '2026-11-14T00:00:05.000000Z'
        command = dict(format='workflow-coding-source-command@1', operation_key='synthetic-nonallocating-source',
            parent_id=setup['id'], mapping=dict(id=mapping_row[0], generation=mapping_row[1], digest=mapping_row[2]),
            action='adopt-local-store', work=dict(repository_id=repository['repository_id'], store_owner=manager,
                object_format='sha1', base='1' * 40, paths=['runtime'], commands=[]))
        authorization_id, admission_id = 'synthetic-source-capability', 'synthetic-source-admission'
        capability = dict(format='workflow-coding-capability@1', id=authorization_id, parent_id=setup['id'],
            level='source', issuer=issuer, subject=dict(org_slug='scratch', principal=manager, team='engineering',
                source_task_id=issuer['task_id'], source_session_id=issuer['session_id'], instance_id=None,
                attempt_id=None, control_id=None, task_id=issuer['task_id'], session_id=issuer['session_id']),
            action=command['action'], radius=dict(command['work'], provider_repository=None, pr_number=None,
                expected_head=None, head_rule='own-development-output@1'),
            authority=capture(setup, admission_id), source_admission_id=admission_id, created_at=at)
        insert('workflow_authorization_revisions', dict(id=authorization_id,
            namespace='org/scratch/coding-capability/' + authorization_id, revision=1,
            authority_bytes=encode(capability), authority_digest=digest(encode(capability)),
            source_pin='workflow-coding-capability@1', created_at=at))
        insert('workflow_admission_records', dict(id=admission_id, namespace='org/scratch', generation=1,
            request_digest=digest(encode(capability)), admitted_by='agent:' + manager))
        operation = dict(format='workflow-coding-source-operation@1', org_slug='scratch', principal='agent:' + manager,
            authorization_id=authorization_id, admission_id=admission_id, session_id=issuer['session_id'],
            command_bytes_base64=b64(encode(command)), command_digest=digest(encode(command)), created_at=at)
        insert('audit_log', dict(task_id=issuer['task_id'], agent=manager, action='coding_source_operation',
                                payload=encode(operation).decode(), timestamp=at))
        original = dict(old_capture, native_owner=dict(table='workflow_authorization_revisions', id=authorization_id),
            source_admission_id=admission_id, command_bytes_base64=b64(encode(command)), observed_at=at)
        insert('audit_log', dict(task_id=issuer['task_id'], agent=manager, action='coding_source_admission',
                                payload=encode(original).decode(), timestamp=at))
        child_id, child_admission = 'synthetic-source-refinement', 'synthetic-refinement-admission'
        child = dict(capability, id=child_id, parent_id=authorization_id,
                     authority=capture(capability, child_admission), created_at='2026-11-14T00:00:06.000000Z')
        insert('workflow_authorization_revisions', dict(id=child_id,
            namespace='org/scratch/coding-capability/' + child_id, revision=1, authority_bytes=encode(child),
            authority_digest=digest(encode(child)), source_pin='workflow-coding-capability@1', created_at=child['created_at']))
        insert('workflow_admission_records', dict(id=child_admission, namespace='org/scratch', generation=1,
            request_digest=digest(encode(child)), admitted_by='agent:' + manager))
        child_command = dict(command, parent_id=authorization_id, operation_key='synthetic-refinement')
        child_operation = dict(operation, authorization_id=child_id, admission_id=child_admission,
            command_bytes_base64=b64(encode(child_command)), command_digest=digest(encode(child_command)),
            created_at=child['created_at'])
        insert('audit_log', dict(task_id=issuer['task_id'], agent=manager, action='coding_source_operation',
                                payload=encode(child_operation).decode(), timestamp=child['created_at']))
        conn.execute('INSERT INTO task_results(task_id,agent,session_id,status,created_at) VALUES(?,?,?,?,?)',
                     (issuer['task_id'], manager, issuer['session_id'], 'completed', child['created_at']))
        # Today's source is terminal, while its original capture remains exact.
        # No queued graph or full queued-task comparison is involved.
        conn.execute('UPDATE tasks SET status=?,completed_at=?,updated_at=? WHERE id=?',
                     ('completed', at, at, issuer['task_id']))
        conn.commit()
        history = read_sequential_history(conn, expected_org_slug='scratch')
        assert history.status == 'storage readable'
        assert conn.execute('SELECT count(*) FROM workflow_coding_runs').fetchone()[0] == 0
        assert conn.execute('SELECT status FROM tasks WHERE id=?', (issuer['task_id'],)).fetchone()[0] == 'completed'


    def populate_coding_start(db):
        # Preserved private reproducer for the step20 27/30-column limitation.
        # It deliberately reaches the unchanged full-reader refusal and is
        # never enrolled as a passing corruption-phase baseline.
        import hashlib
        from runtime.workflows.coding_bootstrap import encode_row, identity, initial_scope, journal_rows, pinned_rows, task_brief
        from runtime.workflows.coding_start_history import claim_for

        saved = populate_coding_mapping(db)
        conn = db._conn
        encode = seq_history._bytes
        digest = lambda raw: hashlib.sha256(raw).hexdigest()
        roles, mapping = saved['mapping']['roles'], saved['mapping']
        at = '2026-11-14T00:00:04.000000Z'
        from runtime.workflows.coding_source_history import _TASK_COLUMNS, _RESULT_COLUMNS, _AUDIT_COLUMNS
        def row(table, where, args=()):
            columns = _TASK_COLUMNS if table == 'tasks' else _RESULT_COLUMNS if table == 'task_results' else _AUDIT_COLUMNS
            cursor = conn.execute('SELECT ' + ','.join(columns) + ' FROM ' + table + ' WHERE ' + where, args)
            values = cursor.fetchone()
            assert values is not None and cursor.fetchone() is None
            return dict(zip((column[0] for column in cursor.description), values, strict=True))
        command = dict(format='workflow-coding-start@2', operation_key='synthetic-coding-start',
            release=mapping['release'], mapping=saved['explicit']['mapping'],
            settings=dict(generation=saved['explicit']['generation'], snapshot_digest=digest(encode(saved['explicit']))),
            work=dict(brief='Synthetic retained work', repository_id=mapping['repository_references'][0]['repository_id'],
                      object_format='sha1', base='1' * 40, paths=['runtime']), implementer_override=None)
        manager = roles['agent_manager']['principal']
        source_id, carrier_id, leaf_id = 'synthetic-start-source', 'synthetic-start-carrier', 'synthetic-start-leaf'
        db.insert_task(TaskRecord(id=source_id, task_type='task', assigned_agent=manager, team='engineering',
            brief='Synthetic retained start source', status='in_progress', orchestration_step_count=1,
            current_session_id='synthetic-start-session'))
        conn.execute('UPDATE tasks SET created_at=?,updated_at=? WHERE id=?', (at, at, source_id))
        source_before = row('tasks', 'id=?', (source_id,))
        start_id = conn.execute('INSERT INTO audit_log(task_id,agent,action,payload,timestamp) VALUES(?,?,?,?,?)',
            (source_id, manager, 'session_start', encode(dict(workspace='synthetic',
                session_id='synthetic-start-session', invocation_purpose='manager_decision',
                executor='synthetic', model=None)).decode(), at)).lastrowid
        result_id = conn.execute('INSERT INTO task_results(task_id,agent,session_id,status,decision_json,created_at) '
            'VALUES(?,?,?,?,?,?)', (source_id, manager, 'synthetic-start-session', 'completed',
            encode(dict(action='workflow_start', workflow=command)).decode(), at)).lastrowid
        callback = row('task_results', 'id=?', (result_id,))
        native = dict(id='synthetic-start-invocation', org='scratch', root_task_id=source_id,
            task_id=source_id, agent=manager, session_id='synthetic-start-session', owner='ordinary',
            owner_identity={}, fingerprint=dict(status='pending', block_kind=None, count=0,
                session_id=None, agent=manager, fanout=None, jobs=None, parent=None,
                brief_digest=digest(source_before['brief'].encode())), generation=0, phase='running',
            retry={}, context={}, started_at=at, captured_generation=None)
        source = dict(task_id=source_id, session_id='synthetic-start-session', principal=manager,
            result=dict(id=result_id, record=encode_row(callback)),
            result_claim=dict(native_claim=claim_for(source_before, result_id),
                owner_invocation_id=native['id'], action_fingerprint=digest(callback['decision_json'].encode())),
            parent_task_id=None, root_ancestor_id=source_id,
            budgets=dict(source_step=1, source_revision=0, session_timeout_seconds=None,
                max_revise_rounds=3, coding_attempt_limit=64, coding_event_limit=4096,
                coding_revision_limit=3, coding_same_principal_retry_limit=1))
        native_source = dict(ancestors=[], session_start=row('audit_log', 'id=?', (start_id,)),
            invocation=dict(id=native['id'], owner='ordinary', entry=native),
            checkpoints=[dict(source=encode_row(source_before), ancestors=[])])
        instance = identity('root', 'scratch', source_id, result_id, digest(encode(command)))
        attempt_id = identity('attempt', instance, 1)
        event_id = identity('event', 'scratch', instance, 'agent:' + manager, command['operation_key'])
        authority = dict(saved['capture'], admission_record_id=identity('admission', instance))
        import base64
        authority.update(grant_bytes_base64=base64.b64encode(encode(command)).decode(), grant_digest=digest(encode(command)))
        template = conn.execute("SELECT template_identity_id,template_version_id FROM workflow_template_identity_versions WHERE template_identity_id IN "
            "(SELECT id FROM workflow_template_identities WHERE namespace='release/happyranch')").fetchone()
        template = dict(identity_id=template[0], version_id=template[1], **command['release'])
        pins, context, binding = pinned_rows(org='scratch', command=command, source=source,
            native_source=native_source, authority=authority, mapping=mapping, roles=roles,
            template=template, carrier_id=carrier_id, instance_id=instance, created_at=at)
        # Only native private row construction and pure record serializers.
        # No retired start, native allocation, queue, callback or host producer.
        for task_id, selector, status, block, brief, parent in (
            (carrier_id, roles['agent_manager'], 'in_progress', 'delegated', 'Structural coding carrier ' + instance, source_id),
            (leaf_id, roles['implementer'], 'pending', None, task_brief(command['work']), carrier_id)):
            db.insert_task(TaskRecord(id=task_id, task_type='subtask', assigned_agent=selector['principal'],
                team=selector['team'], status=status, block_kind=block, brief=brief, parent_task_id=parent))
            conn.execute('UPDATE tasks SET created_at=?,updated_at=? WHERE id=?', (at, at, task_id))
        conn.commit()
        conn.execute('PRAGMA foreign_keys=OFF')
        for table, entries in pins.items():
            for entry in entries:
                conn.execute('INSERT INTO ' + table + ' (' + ','.join(entry) + ') VALUES (' +
                             ','.join('?' for _ in entry) + ')', tuple(entry.values()))
        scope = encode(initial_scope(org='scratch', instance=instance, attempt=attempt_id,
                                    command=command, roles=roles, context=context))
        run = dict(instance_id=instance, mapping_id=command['mapping']['id'], cas_version=1,
            current_revision=0, state='development', current_attempt_id=attempt_id, last_event_id=event_id)
        attempt = dict(id=attempt_id, instance_id=instance, revision=1, stage='development', attempt_sequence=1,
            assignment_generation=1, predecessor_id=None, task_id=leaf_id, principal=roles['implementer']['principal'],
            session_id=None, result_id=None, submission_id=None, review_request_id=None, state='queued',
            scope_bytes=scope, scope_digest=digest(scope))
        # Cyclic foreign references are checked after the full archival graph exists.
        conn.execute('PRAGMA foreign_keys=OFF')
        for table, entry in (('workflow_coding_runs', run), ('workflow_coding_attempts', attempt)):
            conn.execute('INSERT INTO ' + table + ' (' + ','.join(entry) + ') VALUES (' +
                         ','.join('?' for _ in entry) + ')', tuple(entry.values()))
        conn.execute('UPDATE tasks SET block_kind=?,note=?,updated_at=? WHERE id=?',
                     ('delegated', 'coding-owned:' + instance, at, source_id))
        tasks = [row('tasks', 'id=?', (key,)) for key in sorted((source_id, carrier_id, leaf_id))]
        before = dict(run=None, attempts=[dict(id=attempt_id, record=None)], instances=[dict(id=instance, record=None)],
            tasks=[dict(id=task['id'], record=encode_row(source_before) if task['id'] == source_id else None) for task in tasks],
            rounds=[], requests=[], assignments=[])
        after = dict(run=run, attempts=[dict(id=attempt_id, record=encode_row(attempt))],
            instances=[dict(id=instance, record=encode_row(pins['workflow_instances'][0]))],
            tasks=[dict(id=task['id'], record=encode_row(task)) for task in tasks], rounds=[], requests=[], assignments=[])
        payload = dict(format='workflow-coding-event@1', id=event_id, instance_id=instance, event_seq=1,
            event_kind='run_started', attempt_id=attempt_id, operation=dict(org_slug='scratch', principal='agent:' + manager,
                key=command['operation_key'], request_digest=digest(encode(command))), previous_digest=None,
            before=before, after=after, created_at=at, evidence=dict(kind='run_started', command=command,
                origin=dict(kind='manager_result', principal=manager, task_id=source_id,
                    session_id='synthetic-start-session', result_id=result_id, claim=None), authority=authority,
                native_task_observations=[], rows=journal_rows(pins), facts=dict(source=source,
                    carrier_task_id=carrier_id, release=command['release'], mapping_id=command['mapping']['id'],
                    mapping_digest=command['mapping']['digest'], context_id=identity('context', instance),
                    binding_snapshot_id=identity('binding', instance), authorization_id=identity('authorization', instance),
                    activation_id=identity('activation', instance), first_attempt_id=attempt_id)))
        raw = encode(payload)
        event = dict(id=event_id, instance_id=instance, event_seq=1, attempt_id=attempt_id, org_slug='scratch',
            principal='agent:' + manager, operation_key=command['operation_key'], request_digest=digest(encode(command)),
            event_kind='run_started', previous_digest=None, event_bytes=raw, event_digest=digest(raw), created_at=at)
        conn.execute('INSERT INTO workflow_coding_events (' + ','.join(event) + ') VALUES (' +
                     ','.join('?' for _ in event) + ')', tuple(event.values()))
        for task_id, kind, owner in ((source_id, 'coding_source_waiting', instance),
                (carrier_id, 'coding_carrier', instance), (leaf_id, 'coding_attempt', attempt_id)):
            conn.execute('UPDATE workflow_task_ownership SET kind=?,owner_id=? WHERE task_id=?', (kind, owner, task_id))
        conn.commit()
        conn.execute('PRAGMA foreign_keys=ON')
        history = read_sequential_history(conn, expected_org_slug='scratch')
        assert history.status == 'storage readable'
        entry = history.retained[1].queued.runs[instance]
        assert entry['attempt']['state'] == 'queued' and entry['command']['settings'] == command['settings']
        assert history.retained[1].mappings.selected.source_capture.payload['after']['admission'] == 'disabled'
        return dict(saved=saved, instance=instance, source=source, command=command, native=native, payload=payload)


    if sys.argv[1:] in ([], ['--sequential-reuse'], ['--sequential-corruption'], ['--retained-corruption'], ['--document-coding-corruption'], ['--coding-authority-corruption'], ['--document-native-corruption'], ['--author-handoff-corruption'], ['--document-revision-corruption'], ['--coding-history-corruption'], ['--physical-corruption'], ['--sequential-uncommitted']):
        corruptions = (
            ('definition digest', "UPDATE workflow_seq_definitions SET definition_digest='bad'"),
            ('definition bytes', "UPDATE workflow_seq_definitions SET definition_bytes=x'7b7d'"),
            ('compiler pin', "UPDATE workflow_seq_definitions SET compiler_pin='wrong'"),
            ('validator pin', "UPDATE workflow_seq_definitions SET validator_pin='wrong'"),
            ('interpreter pin', "UPDATE workflow_seq_definitions SET interpreter_pin='wrong'"),
            ('capability pin', "UPDATE workflow_seq_definitions SET capability_pin='wrong'"),
            ('settings digest', "UPDATE workflow_seq_settings SET snapshot_digest='bad'"),
            ('settings owner', "UPDATE workflow_seq_settings SET org_slug='wrong'"),
            ('settings generation', 'UPDATE workflow_seq_settings SET generation=2'),
            ('settings request', "UPDATE workflow_seq_settings_operations SET request_digest='bad'"),
            ('settings receipt', "UPDATE workflow_seq_settings_operations SET receipt_digest='bad'"),
            ('start digest', "UPDATE workflow_seq_runs SET start_digest='bad'"),
            ('run counters', 'UPDATE workflow_seq_runs SET event_count=2'),
            ('native task parent', "UPDATE tasks SET parent_task_id='source' WHERE id='visit-task'"),
            ('native task principal', "UPDATE tasks SET assigned_agent='wrong' WHERE id='source'"),
            ('native INTEGER result', "UPDATE task_results SET session_id='wrong' WHERE task_id='source'"),
            ('visit result identity', "UPDATE task_results SET agent='wrong' WHERE task_id='visit-task'"),
            ('visit selection', "UPDATE workflow_seq_visits SET input_selection_digest='bad'"),
            ('visit envelope', "UPDATE workflow_seq_visits SET result_digest='bad'"),
            ('output digest', "UPDATE workflow_seq_outputs SET content_digest='bad'"),
            ('output recipients', "UPDATE workflow_seq_outputs SET recipients_bytes=x'7b7d'"),
            ('evidence digest', "UPDATE workflow_seq_evidence SET evidence_digest='bad'"),
            ('event chain', "UPDATE workflow_seq_events SET prior_digest='bad'"),
            ('outbox payload', "UPDATE workflow_seq_outbox SET notification_digest='bad'"),
            ('source mark', "UPDATE workflow_task_ownership SET owner_id='wrong' WHERE task_id='source'"),
            ('carrier mark', "UPDATE workflow_task_ownership SET org_slug='wrong' WHERE task_id='carrier'"),
            ('visit mark', "UPDATE workflow_task_ownership SET owner_id='wrong' WHERE task_id='visit-task'"),
            ('complete index', 'DROP INDEX workflow_seq_one_selected_visit'),
            ('complete schema', 'ALTER TABLE workflow_seq_evidence ADD COLUMN unexpected TEXT'),
            ('foreign key', "UPDATE workflow_seq_settings SET definition_id='missing'"),
        )
        refusal_codes = dict.fromkeys((name for name, _ in corruptions), 'workflow_seq_digest_invalid')
        refusal_codes.update(dict.fromkeys(('compiler pin', 'validator pin', 'interpreter pin', 'capability pin'),
                                           'workflow_seq_definition_pin_conflict'))
        refusal_codes.update({
            'settings owner': 'workflow_seq_settings_owner_invalid',
            'settings generation': 'workflow_seq_settings_generation_gap',
            'run counters': 'workflow_seq_run_history_conflict',
            'native task parent': 'workflow_seq_visit_task_conflict',
            'native task principal': 'workflow_seq_source_carrier_conflict',
            'native INTEGER result': 'workflow_seq_native_result_conflict',
            'visit result identity': 'workflow_seq_native_result_conflict',
            'output recipients': 'workflow_seq_output_recipients_invalid',
            'event chain': 'workflow_seq_event_chain_invalid',
            'source mark': 'workflow_seq_ownership_invalid',
            'carrier mark': 'workflow_seq_ownership_invalid',
            'visit mark': 'workflow_seq_ownership_invalid',
            'complete index': 'workflow_seq_complete_layout_mismatch',
            'complete schema': 'workflow_seq_complete_layout_mismatch',
            'foreign key': 'workflow_seq_foreign_key_invalid',
        })
        sequential_names = set(refusal_codes)
        retained_corruptions = (
            ('Pause parity', "UPDATE task_pause_controls SET held=1", 'pause_control_unavailable'),
            ('Pause actor', "UPDATE task_pause_controls SET actor=''", 'pause_control_unavailable'),
            ('Pause timestamp', "UPDATE task_pause_controls SET updated_at='2026-11-14T00:00:00'", 'pause_control_unavailable'),
            ('Pause journal org', "UPDATE task_pause_controls SET admission_journal_json=json_set(admission_journal_json,'$.entries[0].org','wrong')", 'pause_control_unavailable'),
            ('Pause ancestry', "UPDATE tasks SET parent_task_id='source' WHERE id='TASK-seq-load'", 'pause_control_unavailable'),
            ('name org', "UPDATE identity_name_schema SET org_slug='wrong'", 'naming_schema_marker_mismatch'),
            ('name current claim', "UPDATE identity_name_owners SET current_label='Missing' WHERE canonical_id='synthetic_agent'", 'naming_current_claim_missing'),
            ('name reserved claim', "UPDATE identity_name_claims SET id_reserved=0,permanent=1 WHERE owner_id='synthetic_agent'", 'naming_live_id_missing'),
            ('move org', "UPDATE team_move_operations SET org_slug='wrong'", 'team_move_record_mismatch'),
            ('move request digest', "UPDATE team_move_operations SET request_digest='" + '0' * 64 + "'", 'team_move_record_mismatch'),
            ('move receipt digest', "UPDATE team_move_operations SET receipt_digest='" + '0' * 64 + "'", 'team_move_receipt_mismatch'),
            ('move diagnostic digest', "UPDATE team_move_operations SET diagnostic_digest='" + '0' * 64 + "'", 'team_move_diagnostic_digest'),
            ('move audit payload', "UPDATE audit_log SET payload='{}' WHERE action='team_move_transition'", 'team_move_audit_history'),
            ('move audit principal', "UPDATE audit_log SET agent='wrong' WHERE action='team_move_reserved'", 'team_move_audit_history'),
            ('move audit gap', "DELETE FROM audit_log WHERE action='team_move_transition'", 'team_move_audit_history'),
        )
        retained_names = {name for name, _, _ in retained_corruptions}
        refusal_codes.update({name: code for name, _, code in retained_corruptions})
        document_coding_corruptions = (
            ('document draft digest', "UPDATE workflow_template_drafts SET definition_digest='bad' WHERE namespace!='release/happyranch'", 'workflow_source_data_corrupt'),
            ('document version digest', "UPDATE workflow_template_versions SET definition_digest='bad' WHERE namespace!='release/happyranch'", 'workflow_source_data_corrupt'),
            ('document compiler pin', "UPDATE workflow_template_versions SET compiler_pin='wrong' WHERE namespace!='release/happyranch'", 'invalid_template_definition'),
            ('document version draft equality', "UPDATE workflow_template_versions SET template_name='wrong' WHERE namespace!='release/happyranch'", 'workflow_source_data_corrupt'),
            ('document authority digest', "UPDATE workflow_authorization_revisions SET authority_digest='bad'", 'workflow_source_data_corrupt'),
            ('document binding digest', "UPDATE workflow_binding_snapshots SET binding_digest='bad'", 'workflow_source_data_corrupt'),
            ('document context digest', "UPDATE workflow_contexts SET context_digest='bad'", 'workflow_source_data_corrupt'),
            ('document activation namespace', "UPDATE workflow_activations SET authority_namespace='wrong'", 'workflow_source_data_corrupt'),
            ('document activation identity', "UPDATE workflow_activations SET template_identity_id='doc-other-identity'", 'workflow_source_data_corrupt'),
            ('document active pointer', 'UPDATE workflow_active_activations SET activation_revision=2', 'workflow_source_data_corrupt'),
            ('document submission digest', "UPDATE workflow_submissions SET submission_bytes=x'00' WHERE id='doc-submission'", 'workflow_submission_data_corrupt'),
            ('document round revision', "UPDATE workflow_rounds SET current_revision=3 WHERE id='doc-round'", 'workflow_submission_data_corrupt'),
            ('document intent request', "UPDATE workflow_draft_dispatch_intents SET request_digest='bad'", 'workflow_draft_data_corrupt'),
            ('document intent scope', "UPDATE workflow_draft_dispatch_intents SET task_scope_digest='bad'", 'workflow_draft_data_corrupt'),
            ('document intent authority', "UPDATE workflow_draft_dispatch_intents SET authority_digest='bad'", 'workflow_draft_data_corrupt'),
            ('document intent principal', "UPDATE workflow_draft_dispatch_intents SET assigned_principal='wrong'", 'workflow_draft_data_corrupt'),
            ('document intent effect', "UPDATE workflow_draft_dispatch_intents SET effect_key='wrong'", 'workflow_draft_data_corrupt'),
            ('document intent host key', "UPDATE workflow_draft_dispatch_intents SET host_execution_key='wrong'", 'workflow_draft_data_corrupt'),
            ('document intent projection', 'UPDATE workflow_draft_dispatch_intents SET cancellation_requested=0', 'workflow_draft_data_corrupt'),
            ('document event digest', "UPDATE workflow_draft_dispatch_events SET event_digest='bad' WHERE event_seq=3", 'workflow_draft_data_corrupt'),
            ('document event gap', 'DELETE FROM workflow_draft_dispatch_events WHERE event_seq=2', 'workflow_draft_data_corrupt'),
            ('document native cancellation', "UPDATE tasks SET status='completed' WHERE id='document-root'", 'workflow_draft_data_corrupt'),
            ('Coding built-in provenance', "UPDATE workflow_template_versions SET published_by='{}' WHERE namespace='release/happyranch'", 'coding_registered_release_history_invalid'),
            ('Coding built-in org', "UPDATE workflow_template_versions SET published_by=json_set(published_by,'$.org_slug','wrong') WHERE namespace='release/happyranch'", 'coding_registered_release_history_invalid'),
            ('Coding built-in draft', "UPDATE workflow_template_drafts SET author_principal='{}' WHERE namespace='release/happyranch'", 'coding_registered_release_history_invalid'),
            ('Coding built-in definition', "UPDATE workflow_template_versions SET definition_bytes=x'7b7d' WHERE namespace='release/happyranch'", 'coding_registered_release_history_invalid'),
            ('Coding built-in pin', "UPDATE workflow_template_versions SET compiler_pin='wrong' WHERE namespace='release/happyranch'", 'coding_registered_release_history_invalid'),
            ('Coding built-in identity', "UPDATE workflow_template_identities SET current_version=2 WHERE namespace='release/happyranch'", 'coding_registered_release_history_invalid'),
            ('Coding built-in version link', "UPDATE workflow_template_identity_versions SET content_digest='bad' WHERE template_identity_id IN (SELECT id FROM workflow_template_identities WHERE namespace='release/happyranch')", 'coding_registered_release_history_invalid'),
        )
        document_coding_names = {name for name, _, _ in document_coding_corruptions}
        # Full historical error text is part of the differential oracle. Rows
        # distinguish malformed preimages/replays from a missing joined closure.
        joined_refusal = ('workflow_legacy_event_unmappable: event=doc-event-joined-1 '
            'instance=doc-instance; source owner must restore authentic '
            'submission/round/event/replay archival closure')
        document_review_corruptions = (
            ('document second submission digest', "UPDATE workflow_submissions SET submission_bytes=x'00' WHERE revision=2", 'workflow_submission_data_corrupt'),
            ('document second round revision', "UPDATE workflow_rounds SET current_revision=3 WHERE id='doc-round-2'", 'workflow_submission_data_corrupt'),
            ('document prior event revision', "UPDATE workflow_events SET revision=3 WHERE id='doc-event-joined-1'", 'workflow_submission_data_corrupt'),
            ('document prior event digest', "UPDATE workflow_events SET event_digest='bad' WHERE id='doc-event-joined-1'", joined_refusal),
            ('document joined replay digest', "UPDATE workflow_operation_replays SET request_digest='bad' WHERE effect_id='doc-event-joined-1'", joined_refusal),
            ('document joined replay missing', "DELETE FROM workflow_operation_replays WHERE effect_id='doc-event-joined-1'", joined_refusal + '; candidates=[]'),
            ('document joined receipt missing', "DELETE FROM workflow_review_receipts WHERE id='receipt-doc-review-1-0'", joined_refusal + '; candidates=[]'),
            ('document joined request scope', "UPDATE workflow_review_requests SET request_scope_digest='bad' WHERE id='doc-review-1-0'", joined_refusal + '; candidates=[]'),
            ('document joined request generation', "UPDATE workflow_review_requests SET assignment_generation=2 WHERE id='doc-review-1-0'", joined_refusal + '; candidates=[]'),
            ('document joined receipt proof', "UPDATE workflow_review_receipts SET proof_digest='bad' WHERE id='receipt-doc-review-1-0'", joined_refusal + '; candidates=[]'),
            ('document joined receipt scope', "UPDATE workflow_review_receipts SET request_scope_digest='bad' WHERE id='receipt-doc-review-1-0'", joined_refusal + '; candidates=[]'),
            ('document joined receipt generation', "UPDATE workflow_review_receipts SET assignment_generation=2 WHERE id='receipt-doc-review-1-0'", joined_refusal + '; candidates=[]'),
            ('document joined request status', "UPDATE workflow_review_requests SET status='changes_requested' WHERE id='doc-review-1-0'", joined_refusal + '; candidates=[]'),
            ('document joined receipt outcome', "UPDATE workflow_review_receipts SET outcome='changes_requested' WHERE id='receipt-doc-review-1-0'", joined_refusal + '; candidates=[]'),
            ('document second event revision', "UPDATE workflow_events SET revision=3 WHERE id='doc-event-joined-2'", 'workflow_submission_data_corrupt'),
        )
        document_coding_names.update(name for name, _, _ in document_review_corruptions)
        document_coding_corruptions += document_review_corruptions
        coding_import_corruptions = (
            ('Coding import command', "UPDATE workflow_template_versions SET published_by=json_set(published_by,'$.command_digest','bad') WHERE namespace='release/happyranch'"),
            ('Coding import publisher', "UPDATE workflow_template_versions SET published_by=json_set(published_by,'$.publisher.principal_id','wrong') WHERE namespace='release/happyranch'"),
            ('Coding import capture', "UPDATE workflow_template_versions SET published_by=json_set(published_by,'$.admission.capture.grant_digest','bad') WHERE namespace='release/happyranch'"),
            ('Coding import draft', "UPDATE workflow_template_drafts SET author_principal='{}' WHERE namespace='release/happyranch'"),
            ('Coding import definition', "UPDATE workflow_template_versions SET definition_bytes=x'7b7d' WHERE namespace='release/happyranch'"),
            ('Coding import pin', "UPDATE workflow_template_versions SET compiler_pin='wrong' WHERE namespace='release/happyranch'"),
            ('Coding import identity', "UPDATE workflow_template_identities SET current_version=2 WHERE namespace='release/happyranch'"),
            ('Coding import version link', "UPDATE workflow_template_identity_versions SET content_digest='bad'"),
            ('Coding import replay request', "UPDATE workflow_template_publish_operations SET request_digest='bad'"),
            ('Coding import replay result', 'UPDATE workflow_template_publish_operations SET result_version=2'),
            ('Coding import admission issuer', "UPDATE workflow_admission_records SET admitted_by='agent:synthetic'"),
            ('Coding import admission generation', 'UPDATE workflow_admission_records SET generation=2'),
            ('Coding import journal state', "UPDATE workflow_publication_journals SET state='prepared'"),
            ('Coding import journal fence', 'UPDATE workflow_publication_journals SET profile_fence=1'),
        )
        coding_setup_corruptions = (
            ('Coding setup issue pin', "UPDATE workflow_authorization_revisions SET source_pin='wrong' WHERE id='synthetic-issue'"),
            ('Coding setup withdraw pin', "UPDATE workflow_authorization_revisions SET source_pin='wrong' WHERE id='synthetic-withdraw'"),
            ('Coding setup issue timestamp', "UPDATE workflow_authorization_revisions SET created_at='2026-11-15T00:00:00.000000Z' WHERE id='synthetic-issue'"),
            ('Coding setup withdraw timestamp', "UPDATE workflow_authorization_revisions SET created_at='2026-11-15T00:00:00.000000Z' WHERE id='synthetic-withdraw'"),
            ('Coding setup issue revision', "UPDATE workflow_authorization_revisions SET revision=3 WHERE id='synthetic-issue'"),
            ('Coding setup withdraw revision', "UPDATE workflow_authorization_revisions SET revision=3 WHERE id='synthetic-withdraw'"),
            ('Coding setup missing issue', "DELETE FROM workflow_authorization_revisions WHERE id='synthetic-issue'"),
            ('Coding setup stale pointer', "UPDATE workflow_active_authorizations SET authorization_revision_id='synthetic-issue'"),
            ('Coding setup missing pointer', 'DELETE FROM workflow_active_authorizations'),
            ('Coding setup issue admission', "UPDATE workflow_admission_records SET admitted_by='agent:synthetic' WHERE id='synthetic-issue-admission'"),
            ('Coding setup withdraw admission', "UPDATE workflow_admission_records SET admitted_by='agent:synthetic' WHERE id='synthetic-withdraw-admission'"),
            ('Coding setup issue generation', "UPDATE workflow_admission_records SET generation=2 WHERE id='synthetic-issue-admission'"),
            ('Coding setup withdraw generation', "UPDATE workflow_admission_records SET generation=2 WHERE id='synthetic-withdraw-admission'"),
            ('Coding setup journal state', "UPDATE workflow_publication_journals SET state='prepared'"),
            ('Coding setup journal fence', 'UPDATE workflow_publication_journals SET profile_fence=1'),
        )
        def corrupt_document_terminal(connection, *, witness):
            import hashlib
            row = connection.execute("SELECT id,event_bytes FROM workflow_draft_dispatch_events WHERE event_kind='completed'").fetchone()
            payload = json.loads(row[1])
            if witness:
                payload['terminal_evidence']['host_quiescent'] = False
            else:
                payload['result']['accepted'] = 0
            raw = seq_history._bytes(payload)
            connection.execute('UPDATE workflow_draft_dispatch_events SET event_bytes=?,event_digest=? WHERE id=?',
                (raw, hashlib.sha256(raw).hexdigest(), row[0]))

        document_native_corruptions = (
            ('document operation org', "UPDATE workflow_submission_operations SET org_slug='wrong'"),
            ('document operation principal', "UPDATE workflow_submission_operations SET principal='wrong'"),
            ('document operation key', "UPDATE workflow_submission_operations SET operation_key='wrong'"),
            ('document operation request', "UPDATE workflow_submission_operations SET request_digest='bad'"),
            ('document operation provenance', "UPDATE workflow_submission_operations SET provenance_bytes=x'7b7d'"),
            ('document operation digest', "UPDATE workflow_submission_operations SET provenance_digest='bad'"),
            ('document operation timestamp', "UPDATE workflow_submission_operations SET created_at='wrong'"),
            ('document operation legacy source', "UPDATE workflow_submissions SET source_result_id='1' WHERE id='doc-submission'"),
            ('document link INTEGER result', "UPDATE workflow_submission_result_links SET task_result_id=(SELECT id FROM task_results WHERE task_id='source')"),
            ('document link result bytes', "UPDATE workflow_submission_result_links SET result_bytes=x'7b7d'"),
            ('document link result digest', "UPDATE workflow_submission_result_links SET result_digest='bad'"),
            ('document completed result principal', "UPDATE task_results SET agent='wrong' WHERE task_id='document-root'"),
            ('document completed result session', "UPDATE task_results SET session_id='wrong' WHERE task_id='document-root'"),
            ('document completed result status', "UPDATE task_results SET status='failed' WHERE task_id='document-root'"),
            ('document completed task principal', "UPDATE tasks SET assigned_agent='wrong' WHERE id='document-root'"),
            ('document completed task status', "UPDATE tasks SET status='failed' WHERE id='document-root'"),
            ('document completed intent result', "UPDATE workflow_draft_dispatch_intents SET final_result_id=(SELECT id FROM task_results WHERE task_id='source')"),
            ('document completed intent session', "UPDATE workflow_draft_dispatch_intents SET session_id='wrong'"),
            ('document completed host identity', "UPDATE workflow_draft_dispatch_intents SET host_execution_id='wrong'"),
            ('document completed claim owner', "UPDATE workflow_draft_dispatch_intents SET claim_owner='wrong'"),
            ('document completed cancellation', 'UPDATE workflow_draft_dispatch_intents SET cancellation_requested=1'),
            ('document callback session', "UPDATE workflow_draft_dispatch_events SET session_id='wrong' WHERE event_kind='callback_recorded'"),
            ('document callback INTEGER result', "UPDATE workflow_draft_dispatch_events SET result_id=(SELECT id FROM task_results WHERE task_id='source') WHERE event_kind='callback_recorded'"),
            ('document callback result digest', "UPDATE workflow_draft_dispatch_events SET result_digest='bad' WHERE event_kind='callback_recorded'"),
            ('document callback disposition', "UPDATE workflow_draft_dispatch_events SET disposition='wrong' WHERE event_kind='callback_recorded'"),
            ('document completed terminal witness', lambda conn: corrupt_document_terminal(conn, witness=True)),
            ('document completed terminal result', lambda conn: corrupt_document_terminal(conn, witness=False)),
        )
        def corrupt_handoff_proof(connection, path, value):
            import hashlib
            row = connection.execute('SELECT id,admission_bytes FROM workflow_author_handoffs').fetchone()
            proof = json.loads(row[1])
            target = proof
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            raw = seq_history._bytes(proof)
            connection.execute('UPDATE workflow_author_handoffs SET admission_bytes=?,admission_digest=? WHERE id=?',
                               (raw, hashlib.sha256(raw).hexdigest(), row[0]))

        author_handoff_corruptions = (
            ('H org', "UPDATE workflow_author_handoffs SET org_slug='wrong'"),
            ('H principal', "UPDATE workflow_author_handoffs SET principal='wrong'"),
            ('H source session', "UPDATE workflow_author_handoffs SET source_session_id='wrong'"),
            ('H candidate digest', "UPDATE workflow_author_handoffs SET candidate_digest='0000000000000000000000000000000000000000000000000000000000000000'"),
            ('H request digest', "UPDATE workflow_author_handoffs SET request_digest='0000000000000000000000000000000000000000000000000000000000000000'"),
            ('H admission digest', "UPDATE workflow_author_handoffs SET admission_digest='0000000000000000000000000000000000000000000000000000000000000000'"),
            ('H closed admission', lambda conn: corrupt_handoff_proof(conn, ('extra',), True)),
            ('H command operation', lambda conn: corrupt_handoff_proof(conn, ('client_request', 'operation_key'), 'wrong')),
            ('H command candidate', lambda conn: corrupt_handoff_proof(conn, ('client_request', 'submission_bytes_base64'), 'eA==')),
            ('H source immutable', lambda conn: corrupt_handoff_proof(conn, ('source_intent', 'recovery_owner'), 'wrong')),
            ('H source generation org', lambda conn: corrupt_handoff_proof(conn, ('source_generation', 'org_slug'), 'wrong')),
            ('H source generation principal', lambda conn: corrupt_handoff_proof(conn, ('source_generation', 'principal'), 'wrong')),
            ('H source generation session', lambda conn: corrupt_handoff_proof(conn, ('source_generation', 'session_id'), 'wrong')),
            ('H source generation numeric type', lambda conn: corrupt_handoff_proof(conn, ('source_generation', 'activation_revision'), True)),
            ('H source generation assignment', lambda conn: corrupt_handoff_proof(conn, ('source_generation', 'assignment_generation'), 2)),
            ('H source generation attempt', lambda conn: corrupt_handoff_proof(conn, ('source_generation', 'attempt_sequence'), 2)),
            ('H source generation claim', lambda conn: corrupt_handoff_proof(conn, ('source_generation', 'claim_token'), 'wrong')),
            ('H source generation owner', lambda conn: corrupt_handoff_proof(conn, ('source_generation', 'claim_owner'), 'wrong')),
            ('H source generation host', lambda conn: corrupt_handoff_proof(conn, ('source_generation', 'host_execution_id'), 'wrong')),
            ('H source generation result', lambda conn: corrupt_handoff_proof(conn, ('source_generation', 'final_result_id'), 1)),
            ('H source generation state', lambda conn: corrupt_handoff_proof(conn, ('source_generation', 'state'), 'completed')),
            ('H source generation cancellation', lambda conn: corrupt_handoff_proof(conn, ('source_generation', 'cancellation_requested'), 1)),
            ('H template pin', lambda conn: corrupt_handoff_proof(conn, ('pins', 'template', 'validator_pin'), 'wrong')),
            ('H activation pin', lambda conn: corrupt_handoff_proof(conn, ('pins', 'activation', 'revision'), 2)),
            ('H authorization pin', lambda conn: corrupt_handoff_proof(conn, ('pins', 'authorization', 'bytes_hex'), '7b7d')),
            ('H binding pin', lambda conn: corrupt_handoff_proof(conn, ('pins', 'binding', 'digest'), '0000000000000000000000000000000000000000000000000000000000000000')),
            ('H context pin', lambda conn: corrupt_handoff_proof(conn, ('pins', 'context', 'id'), 'wrong')),
            ('H scope pin', lambda conn: corrupt_handoff_proof(conn, ('pins', 'scope', 'bytes_hex'), '7b7d')),
            ('H contract pin', lambda conn: corrupt_handoff_proof(conn, ('pins', 'contract', 'digest'), '0000000000000000000000000000000000000000000000000000000000000000')),
            ('H captured ready', lambda conn: corrupt_handoff_proof(conn, ('admission_authority', 'ready', 'generation'), 2)),
            ('H captured pointer', lambda conn: corrupt_handoff_proof(conn, ('admission_authority', 'pointer', 'journal_id'), 'wrong')),
            ('H captured profiles', lambda conn: corrupt_handoff_proof(conn, ('admission_authority', 'profiles', 0, 'digest'), '0000000000000000000000000000000000000000000000000000000000000000')),
            ('H captured dependency', lambda conn: corrupt_handoff_proof(conn, ('admission_authority', 'mirror', 0, 'state'), 'unbound')),
            ('H captured dependency generation', lambda conn: corrupt_handoff_proof(conn, ('admission_authority', 'mirror', 0, 'bound_generation'), 2)),
            ('H captured store', lambda conn: corrupt_handoff_proof(conn, ('admission_authority', 'mirror', 1, 'state'), 'removed')),
            ('H captured registry', lambda conn: corrupt_handoff_proof(conn, ('admission_authority', 'mirror', 2, 'published_generation'), 2)),
            ('H captured missing mirror', lambda conn: corrupt_handoff_proof(conn, ('admission_authority', 'mirror'), [])),
            ('H captured cutover', lambda conn: corrupt_handoff_proof(conn, ('admission_authority', 'cutover'), 'disabled')),
            ('H captured actions', lambda conn: corrupt_handoff_proof(conn, ('admission_authority', 'actions'), ['stage-immutable-document'])),
        )
        def corrupt_review_event(connection, kind, path, value):
            import hashlib
            row = connection.execute('SELECT id,event_bytes FROM workflow_dispatch_events WHERE event_kind=?', (kind,)).fetchone()
            body = json.loads(row[1])
            target = body
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            raw = seq_history._bytes(body)
            connection.execute('UPDATE workflow_dispatch_events SET event_bytes=?,event_digest=? WHERE id=?',
                               (raw, hashlib.sha256(raw).hexdigest(), row[0]))

        def corrupt_joined(connection, path, value):
            import hashlib
            row = connection.execute("SELECT id,event_bytes FROM workflow_events WHERE event_kind='joined'").fetchone()
            body = json.loads(row[1])
            target = body
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            raw = seq_history._bytes(body)
            dg = hashlib.sha256(raw).hexdigest()
            connection.execute('UPDATE workflow_events SET event_bytes=?,event_digest=? WHERE id=?', (raw, dg, row[0]))
            # Preserve replay/digest closure to reach the collection semantics.
            connection.execute('UPDATE workflow_operation_replays SET request_digest=? WHERE effect_id=?', (dg, row[0]))

        author_graph_corruptions = (
            ('H graph joined authorization', lambda conn: corrupt_joined(conn, ('pins', 'authorization', 'digest'), '0000000000000000000000000000000000000000000000000000000000000000')),
            ('H graph joined receipt', lambda conn: corrupt_joined(conn, ('receipts', 0, 'proof_digest'), '0000000000000000000000000000000000000000000000000000000000000000')),
            ('H graph joined assignment', lambda conn: corrupt_joined(conn, ('assignments', 0, 'principal'), 'wrong')),
            ('H graph joined actions', lambda conn: corrupt_joined(conn, ('admission_authority', 'actions'), ['collect-review'])),
            ('H graph joined operation', lambda conn: corrupt_joined(conn, ('operation', 'action'), 'wrong')),
            ('H graph round state', "UPDATE workflow_rounds SET state='superseded'"),
            ('H graph request principal', "UPDATE workflow_review_requests SET principal='wrong'"),
            ('H graph operation principal', "UPDATE workflow_dispatch_operations SET principal='wrong'"),
            ('H graph operation key', "UPDATE workflow_dispatch_operations SET operation_key='wrong'"),
            ('H graph operation request', "UPDATE workflow_dispatch_operations SET request_digest='bad'"),
            ('H graph outbox effect', "UPDATE workflow_dispatch_outbox SET effect_key='wrong'"),
            ('H graph outbox host key', "UPDATE workflow_dispatch_outbox SET host_execution_key='wrong'"),
            ('H graph outbox authority', "UPDATE workflow_dispatch_outbox SET authority_digest='bad'"),
            ('H graph outbox recovery owner', "UPDATE workflow_dispatch_outbox SET recovery_owner='wrong'"),
            ('H graph bridge principal', "UPDATE workflow_request_task_bridges SET assigned_principal='wrong'"),
            ('H graph bridge generation', 'UPDATE workflow_request_task_bridges SET assignment_generation=2'),
            ('H graph native task parent', "UPDATE tasks SET parent_task_id='document-root' WHERE id='synthetic-review-task'"),
            ('H graph native task team', "UPDATE tasks SET team='wrong' WHERE id='synthetic-review-task'"),
            ('H graph native task brief', "UPDATE tasks SET brief='wrong' WHERE id='synthetic-review-task'"),
            ('H graph native result principal', "UPDATE task_results SET agent='wrong' WHERE task_id='synthetic-review-task'"),
            ('H graph native result session', "UPDATE task_results SET session_id='wrong' WHERE task_id='synthetic-review-task'"),
            ('H graph native result status', "UPDATE task_results SET status='failed' WHERE task_id='synthetic-review-task'"),
            ('H graph callback digest', "UPDATE workflow_dispatch_callbacks SET result_digest='bad'"),
            ('H graph callback accepted', 'UPDATE workflow_dispatch_callbacks SET accepted=0'),
            ('H graph callback revision', 'UPDATE workflow_dispatch_callbacks SET observed_revision=2'),
            ('H graph effect host', "UPDATE workflow_dispatch_effects SET host_execution_id='wrong'"),
            ('H graph receipt evidence signer', "UPDATE workflow_receipt_evidence SET signer_principal='wrong'"),
            ('H graph receipt evidence result', 'UPDATE workflow_receipt_evidence SET round_revision=2'),
            ('H graph assignment principal', "UPDATE workflow_current_assignments SET principal='wrong'"),
            ('H graph missing contributor', 'DELETE FROM workflow_submission_contributors'),
            ('H graph review terminal witness', lambda conn: corrupt_review_event(conn, 'completed', ('terminal_evidence', 'host_quiescent'), False)),
            ('H graph review terminal accepted', lambda conn: corrupt_review_event(conn, 'completed', ('result', 'accepted'), 0)),
            ('H graph review stage subject', lambda conn: corrupt_review_event(conn, 'review_staged', ('stage', 'scope', 'principal'), 'wrong')),
            ('H graph review stage command', lambda conn: corrupt_review_event(conn, 'review_staged', ('stage', 'command', 'expected_revision'), 2)),
            ('H graph review callback native type', lambda conn: corrupt_review_event(conn, 'callback_recorded', ('result', 'id'), True)),
        )
        author_graph_names = {name for name, _ in author_graph_corruptions}
        refusal_codes.update({name: 'workflow_author_handoff_storage_corrupt' for name in author_graph_names})
        author_handoff_names = {name for name, _ in author_handoff_corruptions}
        def corrupt_saved_json(connection, table, column, path, value, *, where=''):
            import hashlib
            rows = list(connection.execute('SELECT id,' + column + '_bytes FROM ' + table +
                                           (' WHERE ' + where if where else '')))
            assert len(rows) == 1, 'ambiguous synthetic saved record'
            identity, raw = rows[0]
            body = json.loads(raw)
            target = body
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            raw = seq_history._bytes(body)
            connection.execute('UPDATE ' + table + ' SET ' + column + '_bytes=?,' + column + '_digest=? WHERE id=?',
                               (raw, hashlib.sha256(raw).hexdigest(), identity))

        # Same keeper/contracts, distinct stored family: @2 reconstructs the
        # complete @4 grant/binding/context and null repository relationship.
        artifact_handoff_corruptions = tuple((name.replace('H ', 'H artifact ', 1), mutation)
                                             for name, mutation in author_handoff_corruptions) + (
            ('H artifact repository resolution', lambda conn: corrupt_handoff_proof(conn, ('repository_resolution',), {})),
            ('H artifact selected kind', lambda conn: corrupt_handoff_proof(conn, ('client_request', 'artifact_kind'), 'code-change')),
            ('H artifact family discriminator', lambda conn: corrupt_handoff_proof(conn, ('format',), 'workflow-author-handoff@1')),
        )
        artifact_graph_corruptions = tuple((name.replace('H graph ', 'H artifact graph ', 1), mutation)
                                           for name, mutation in author_graph_corruptions)
        human_graph_corruptions = tuple((name, lambda conn, path=path, value=value:
            corrupt_saved_json(conn, 'workflow_review_receipts', 'proof', path, value)) for name, path, value in (
            ('H human actor kind', ('actor', 'principal_kind'), 'agent'),
            ('H human actor identity', ('actor', 'principal_id'), 'wrong'),
            ('H human bearer proof', ('actor', 'proof_kind'), 'task_session'),
            ('H human operation principal', ('operation_principal',), 'wrong'),
            ('H human command family', ('command', 'format'), 'workflow-review-stage-command@1'),
            ('H human command request', ('command', 'request_id'), 'wrong'),
            ('H human command revision', ('command', 'expected_revision'), True),
            ('H human command scope', ('command', 'request_scope_digest'), '0' * 64),
            ('H human command outcome', ('command', 'outcome'), 'changes_requested'),
            ('H human findings digest', ('command', 'findings_digest'), '0' * 64),
            ('H human operation digest', ('request_digest',), '0' * 64),
            ('H human authority actions', ('admission_authority', 'actions'), []),
            ('H human authority generation', ('admission_authority', 'ready', 'generation'), 2),
            ('H human template pin', ('pins', 'template', 'version'), 2),
            ('H human scope kind', ('scope', 'kind'), 'agent'),
            ('H human scope principal', ('scope', 'principal'), 'wrong'),
        )) + (
            ('H human receipt outcome', "UPDATE workflow_review_receipts SET outcome='changes_requested'"),
            ('H human receipt generation', 'UPDATE workflow_review_receipts SET assignment_generation=2'),
            ('H human receipt timestamp', "UPDATE workflow_review_receipts SET created_at='2026-11-15T00:00:00.000000Z'"),
            ('H human receipt missing', 'DELETE FROM workflow_review_receipts'),
            ('H human unexpected agent task', "INSERT INTO workflow_instance_tasks VALUES ('doc-instance','document-root','doc-session','reviewer',1,'completed')"),
        )
        artifact_human_corruptions = tuple((name.replace('H human ', 'H artifact human ', 1), mutation)
                                           for name, mutation in human_graph_corruptions)
        revision_corruptions = tuple((name, lambda conn, path=path, value=value:
            corrupt_saved_json(conn, 'workflow_document_revision_operations', 'admission', path, value))
            for name, path, value in (
                ('I closed admission', ('unexpected',), True),
                ('I predecessor handoff', ('predecessor', 'handoff_id'), 'wrong'),
                ('I predecessor source intent', ('predecessor', 'source_intent_id'), 'wrong'),
                ('I predecessor submission digest', ('predecessor', 'submission_digest'), '0' * 64),
                ('I predecessor request set', ('predecessor', 'request_ids'), []),
                ('I predecessor receipt set', ('predecessor', 'receipt_ids'), []),
                ('I predecessor receipt digests', ('predecessor', 'receipt_digests'), ['0' * 64]),
                ('I command predecessor', ('client_request', 'predecessor_submission_id'), 'wrong'),
                ('I command round', ('client_request', 'predecessor_round_id'), 'wrong'),
                ('I command authority', ('client_request', 'authority', 'generation'), 2),
                ('I original source pin', ('pins', 'scope', 'id'), 'wrong'),
                ('I revision context pin', ('pins', 'context', 'id'), 'wrong'),
                ('I return actions', ('admission_authority', 'actions'), []),
                ('I return captured generation', ('admission_authority', 'ready', 'generation'), 2),
            )) + tuple((name, lambda conn, path=path, value=value:
                corrupt_saved_json(conn, 'workflow_contexts', 'context', path, value,
                                   where="source_kind='document-revision'"))
                for name, path, value in (
                    ('I feedback role', ('revision_feedback', 0, 'role'), 'wrong'),
                    ('I feedback request', ('revision_feedback', 0, 'request_id'), 'wrong'),
                    ('I feedback receipt', ('revision_feedback', 0, 'receipt_id'), 'wrong'),
                    ('I feedback outcome', ('revision_feedback', 0, 'outcome'), 'approved'),
                    ('I feedback findings', ('revision_feedback', 0, 'findings', 'findings_digest'), '0' * 64),
                    ('I feedback receipt digest', ('revision_feedback', 0, 'receipt_digest'), '0' * 64),
                    ('I feedback missing', ('revision_feedback',), []),
                    ('I context predecessor', ('predecessor', 'round_id'), 'wrong'),
                    ('I context source', ('source_context_id',), 'wrong'),
                    ('I context return operation', ('revision_operation_id',), 'wrong'),
                    ('I context target revision', ('target_revision',), True),
                )) + (
                    ('I return request digest', "UPDATE workflow_document_revision_operations SET request_digest='" + '0' * 64 + "'"),
                    ('I return predecessor round', "UPDATE workflow_document_revision_operations SET predecessor_round_id='wrong'"),
                    ('I return original author', "UPDATE tasks SET assigned_agent='wrong' WHERE id='synthetic-revision-author'"),
                    ('I return author brief', "UPDATE tasks SET brief='wrong' WHERE id='synthetic-revision-author'"),
                    ('I return author parent', "UPDATE tasks SET parent_task_id='source' WHERE id='synthetic-revision-author'"),
                    ('I return author kind', "UPDATE tasks SET task_type='task' WHERE id='synthetic-revision-author'"),
                    ('I return author chain', "UPDATE tasks SET active_chain='[]' WHERE id='synthetic-revision-author'"),
                    ('I return author team', "UPDATE tasks SET team='wrong' WHERE id='synthetic-revision-author'"),
                    ('I return assignment generation', "UPDATE workflow_draft_dispatch_intents SET assignment_generation=3 WHERE admission_kind='revision'"),
                    ('I return retired predecessor', lambda conn: (
                        conn.execute("UPDATE workflow_draft_dispatch_intents SET is_current=0,state='cancelled' WHERE admission_kind='revision'"),
                        conn.execute("UPDATE workflow_draft_dispatch_intents SET is_current=1 WHERE admission_kind='initial'"))),
                    ('I return predecessor retirement event', "DELETE FROM workflow_draft_dispatch_events WHERE event_kind='retired'"),
                    ('I return missing admission', 'DELETE FROM workflow_document_revision_operations'),
                )
        artifact_revision_corruptions = tuple((name.replace('I ', 'I artifact ', 1), mutation)
                                               for name, mutation in revision_corruptions)
        def corrupt_revision_stage(connection, key, value):
            import hashlib
            submission_id, stored = connection.execute("SELECT submission_id,provenance_bytes FROM workflow_submission_operations WHERE source_task_id='synthetic-revision-author'").fetchone()
            proof = json.loads(stored)
            request = json.loads(bytes.fromhex(proof['request_bytes']))
            request[key] = value
            raw = seq_history._bytes(request)
            request_digest = hashlib.sha256(raw).hexdigest()
            proof['request_bytes'] = raw.hex()
            proof['operation']['request_digest'] = request_digest
            raw = seq_history._bytes(proof)
            connection.execute('UPDATE workflow_submission_operations SET request_digest=?,provenance_bytes=?,provenance_digest=? WHERE submission_id=?',
                               (request_digest, raw, hashlib.sha256(raw).hexdigest(), submission_id))

        revision_stage_corruptions = tuple((name, lambda conn, key=key, value=value: corrupt_revision_stage(conn, key, value))
            for name, key, value in (
                ('I stage return operation', 'revision_operation_id', 'wrong'),
                ('I stage command format', 'format', 'workflow-author-handoff-request@1'),
                ('I stage native revision type', 'expected_revision', True),
                ('I stage artifact kind', 'artifact_kind', 'code-change'),
                ('I stage operation key', 'operation_key', 'wrong'),
                ('I stage candidate bytes', 'submission_bytes_base64', 'V3Jvbmc='),
            )) + (
                ('I stage operation timestamp', "UPDATE workflow_submission_operations SET created_at='2026-11-15T00:00:00.000000Z' WHERE source_task_id='synthetic-revision-author'"),
                ('I stage provenance digest', "UPDATE workflow_submission_operations SET provenance_digest='bad' WHERE source_task_id='synthetic-revision-author'"),
                ('I stage submission digest', "UPDATE workflow_submissions SET submission_bytes=x'00' WHERE revision=2"),
                ('I stage native source session', "UPDATE workflow_draft_dispatch_intents SET session_id='wrong' WHERE admission_kind='revision'"),
            )
        artifact_revision_stage_corruptions = tuple((name.replace('I stage ', 'I artifact stage ', 1), mutation)
                                                   for name, mutation in revision_stage_corruptions)
        revision_graph_corruptions = (
            ('I graph joined authorization', lambda conn: corrupt_joined(conn, ('pins', 'authorization', 'id'), 'wrong')),
            ('I graph joined receipt', lambda conn: corrupt_joined(conn, ('receipts', 0, 'outcome'), 'changes_requested')),
            ('I graph joined assignment', lambda conn: corrupt_joined(conn, ('assignments', 0, 'generation'), 1)),
            ('I graph joined actions', lambda conn: corrupt_joined(conn, ('admission_authority', 'actions'), [])),
            ('I graph native source result', "UPDATE task_results SET agent='wrong' WHERE task_id='synthetic-revision-author'"),
            ('I graph native reviewer result', "UPDATE task_results SET session_id='wrong' WHERE task_id='synthetic-review-task-2'"),
            ('I graph reviewer parent', "UPDATE tasks SET parent_task_id='source' WHERE id='synthetic-review-task-2'"),
            ('I graph reviewer assignment', "UPDATE workflow_current_assignments SET principal='wrong' WHERE generation=2"),
            ('I graph callback revision', 'UPDATE workflow_dispatch_callbacks SET observed_revision=1 WHERE outbox_id IN (SELECT id FROM workflow_dispatch_outbox WHERE artifact_revision=2)'),
            ('I graph native link digest', "UPDATE workflow_submission_result_links SET result_digest='bad' WHERE source_task_id='synthetic-revision-author'"),
        ) + tuple((name, lambda conn, path=path, value=value:
            corrupt_saved_json(conn, 'workflow_review_receipts', 'proof', path, value, where='assignment_generation=2'))
            for name, path, value in (
                ('I graph review family', ('format',), 'workflow-agent-review-proof@1'),
                ('I graph review subject', ('scope', 'revision_operation_id'), 'wrong'),
                ('I graph review generation', ('scope', 'assignment_generation'), 1),
                ('I graph review stage revision', ('stage_event', 'body', 'stage', 'command', 'expected_revision'), 1),
                ('I graph review native result', ('result_id',), True),
            ))
        artifact_revision_graph_corruptions = tuple((name.replace('I graph ', 'I artifact graph ', 1), mutation)
                                                   for name, mutation in revision_graph_corruptions)
        revision_human_corruptions = tuple((name, lambda conn, path=path, value=value:
            corrupt_saved_json(conn, 'workflow_review_receipts', 'proof', path, value, where='assignment_generation=2'))
            for name, path, value in (
                ('I human actor identity', ('actor', 'principal_id'), 'wrong'),
                ('I human bearer proof', ('actor', 'proof_kind'), 'task_session'),
                ('I human command family', ('command', 'format'), 'workflow-human-review-command@1'),
                ('I human command revision', ('command', 'expected_revision'), 1),
                ('I human findings digest', ('command', 'findings_digest'), '0' * 64),
                ('I human predecessor subject', ('scope', 'revision_operation_id'), 'wrong'),
                ('I human original pin', ('pins', 'template', 'version'), 2),
                ('I human return authority', ('admission_authority', 'actions'), []),
            ))
        artifact_revision_human_corruptions = tuple((name.replace('I human ', 'I artifact human ', 1), mutation)
                                                   for name, mutation in revision_human_corruptions)
        family_cases = (
            (artifact_handoff_corruptions, lambda db: populate_author_handoff(db, artifact=True)),
            (artifact_graph_corruptions, lambda db: populate_handoff_graph(db, artifact=True)),
            (human_graph_corruptions, lambda db: populate_handoff_graph(db, human=True)),
            (artifact_human_corruptions, lambda db: populate_handoff_graph(db, human=True, artifact=True)),
            (revision_corruptions, populate_revision),
            (artifact_revision_corruptions, lambda db: populate_revision(db, artifact=True)),
            (revision_stage_corruptions, lambda db: populate_revision(db, staged=True)),
            (artifact_revision_stage_corruptions, lambda db: populate_revision(db, staged=True, artifact=True)),
            (revision_graph_corruptions, lambda db: populate_handoff_graph(db, successor=True)),
            (artifact_revision_graph_corruptions, lambda db: populate_handoff_graph(db, successor=True, artifact=True)),
            (revision_human_corruptions, lambda db: populate_handoff_graph(db, successor=True, human=True)),
            (artifact_revision_human_corruptions, lambda db: populate_handoff_graph(db, successor=True, human=True, artifact=True)),
        )
        family_populators = {name: populate for cases, populate in family_cases for name, _ in cases}
        family_corruptions = tuple(case for cases, _ in family_cases for case in cases)
        refusal_codes.update({name: ('workflow_document_revision_storage_corrupt' if name.startswith('I ')
                                    else 'workflow_author_handoff_storage_corrupt') for name in family_populators})
        for prefix in ('I ', 'I artifact '):
            refusal_codes[prefix + 'return request digest'] = 'workflow_author_handoff_storage_corrupt'
            refusal_codes[prefix + 'return predecessor round'] = 'workflow_source_data_corrupt'
            for suffix in ('return original author', 'return author brief', 'return author team',
                           'return assignment generation', 'return retired predecessor',
                           'return predecessor retirement event'):
                refusal_codes[prefix + suffix] = 'workflow_draft_data_corrupt'
        refusal_codes['H artifact selected kind'] = ('workflow_coding_data_unsupported: '
            'workflow_author_handoffs.admission_bytes; complete family reader required')
        for prefix in ('H human ', 'H artifact human '):
            refusal_codes[prefix + 'receipt generation'] = (
                'workflow_legacy_event_unmappable: event=workflow-event:'
                '3b8e5809c3337ce570ae4075d3646b3a7d7cd3ba70c9e05d242daaba60128782 instance=doc-instance; '
                'source owner must restore authentic submission/round/event/replay archival closure; candidates=[]')
        for prefix in ('I stage ', 'I artifact stage '):
            for suffix in ('native revision type', 'artifact kind', 'candidate bytes'):
                refusal_codes[prefix + suffix] = 'workflow_document_revision_invalid_request'
            for suffix in ('operation timestamp', 'provenance digest', 'submission digest'):
                refusal_codes[prefix + suffix] = 'workflow_submission_data_corrupt'
            refusal_codes[prefix + 'native source session'] = 'workflow_draft_data_corrupt'
        for cases in (revision_graph_corruptions, artifact_revision_graph_corruptions,
                      revision_human_corruptions, artifact_revision_human_corruptions):
            refusal_codes.update({name: 'workflow_author_handoff_storage_corrupt' for name, _ in cases})
        for prefix in ('I graph ', 'I artifact graph '):
            refusal_codes[prefix + 'native source result'] = 'workflow_draft_data_corrupt'
            refusal_codes[prefix + 'native link digest'] = 'workflow_submission_data_corrupt'
        refusal_codes.update({name: 'workflow_author_handoff_storage_corrupt' for name in author_handoff_names})
        document_native_names = {name for name, _ in document_native_corruptions}
        refusal_codes.update({name: ('workflow_submission_data_corrupt' if name.startswith(('document operation ', 'document link '))
                                    else 'workflow_draft_data_corrupt') for name in document_native_names})
        coding_import_names = {name for name, _ in coding_import_corruptions}
        coding_setup_names = {name for name, _ in coding_setup_corruptions}
        refusal_codes.update({name: 'coding_registered_release_history_invalid' for name in coding_import_names})
        refusal_codes.update({name: 'coding_authority_history_invalid' for name in coding_setup_names})
        refusal_codes.update({name: code for name, _, code in document_coding_corruptions})
        # Step13 convergence: each row owns a distinct native column, saved
        # predicate or join; shared checks are not repeated per family.
        coding_history_checks = []
        def coding_case(name, mutation, predicate):
            coding_history_checks.append((name, mutation, predicate))

        def coding_json(connection, table, column, where, path, value, *, digest_column=None):
            import hashlib
            selected = list(connection.execute('SELECT rowid,' + column + ' FROM ' + table + ' WHERE ' + where))
            assert len(selected) == 1, 'ambiguous synthetic Coding record'
            rowid, original = selected[0]
            body = json.loads(original)
            target = body
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            raw = seq_history._bytes(body)
            stored = raw if type(original) is bytes else raw.decode()
            if digest_column:
                connection.execute('UPDATE ' + table + ' SET ' + column + '=?,' + digest_column + '=? WHERE rowid=?',
                                   (stored, hashlib.sha256(raw).hexdigest(), rowid))
            else:
                connection.execute('UPDATE ' + table + ' SET ' + column + '=? WHERE rowid=?', (stored, rowid))

        def corrupt_coding_column(conn, column, value):
            previous = conn.execute('PRAGMA ignore_check_constraints').fetchone()[0]
            if column == 'release_identity':
                conn.execute('PRAGMA ignore_check_constraints=ON')
            try:
                conn.execute('UPDATE workflow_coding_mappings SET ' + column + '=? WHERE generation=1', (value,))
            finally:
                if column == 'release_identity':
                    conn.execute('PRAGMA ignore_check_constraints=' + str(previous))
                    assert conn.execute('PRAGMA ignore_check_constraints').fetchone()[0] == previous

        for column, value in (('id', 'wrong-mapping'), ('org_slug', 'foreign'),
                ('release_identity', 'release/foreign/coding'), ('generation', 4),
                ('principal', 'agent:foreign'), ('operation_key', 'foreign-key'),
                ('request_digest', '0' * 64), ('mapping_bytes', b'{}'),
                ('mapping_digest', '0' * 64), ('created_at', '2026-11-14T00:00:04.000000Z')):
            coding_case('Coding mapping column ' + column,
                lambda conn, column=column, value=value: corrupt_coding_column(conn, column, value),
                'coding_mapping_history.py:104-129 native ' + column)
        for name, sql in (
            ('missing prefix', 'DELETE FROM workflow_coding_mappings WHERE generation=1'),
            ('orphan capture', "DELETE FROM audit_log WHERE action='coding_source_admission'"),
            ('missing native admission', "DELETE FROM workflow_admission_records WHERE id LIKE 'synthetic-mapping-admission:%'"),
            ('native admission namespace', "UPDATE workflow_admission_records SET namespace='org/foreign' WHERE id LIKE 'synthetic-mapping-admission:%'"),
            ('native admission generation', "UPDATE workflow_admission_records SET generation=2 WHERE id LIKE 'synthetic-mapping-admission:%'"),
            ('native admission request', "UPDATE workflow_admission_records SET request_digest='bad' WHERE id LIKE 'synthetic-mapping-admission:%'"),
            ('native admission principal', "UPDATE workflow_admission_records SET admitted_by='agent:foreign' WHERE id LIKE 'synthetic-mapping-admission:%'"),
            ('current native task identity', "UPDATE tasks SET created_at='2026-11-13T00:00:00.000000Z' WHERE id='synthetic-mapping-source'"),
            ('current native task parent', "UPDATE tasks SET parent_task_id='source' WHERE id='synthetic-mapping-source'"),
            ('current native task type', "UPDATE tasks SET task_type='subtask' WHERE id='synthetic-mapping-source'"),
            ('missing session start', "DELETE FROM audit_log WHERE task_id='synthetic-mapping-source' AND action='session_start'"),
            ('foreign session agent', "UPDATE audit_log SET agent='foreign' WHERE task_id='synthetic-mapping-source' AND action='session_start'"),
            ('future session start', "UPDATE audit_log SET timestamp='2026-11-15T00:00:00.000000Z' WHERE task_id='synthetic-mapping-source' AND action='session_start'"),
        ):
            coding_case('Coding mapping ' + name, sql,
                'coding_mapping_history.py:135-189; _coding_mapping_admission.py:74-135 ' + name)
        for name, path, value in (
            ('closed record', ('extra',), True), ('family', ('format',), 'workflow-coding-unknown@1'),
            ('record org', ('org_slug',), 'foreign'), ('command encoding', ('command_bytes_base64',), 'e30='),
            ('command digest', ('command_digest',), '0' * 64),
            ('issuer principal', ('issuer', 'principal'), 'foreign'),
            ('issuer task', ('issuer', 'task_id'), 'source'),
            ('issuer session', ('issuer', 'session_id'), 'foreign'),
            ('issuer result', ('issuer', 'result_id'), 1),
            ('mapping equality', ('mapping', 'repository_references', 0, 'object_format'), 'sha256'),
            ('capture admission', ('authority', 'admission_record_id'), 'foreign'),
            ('capture grant', ('authority', 'grant_digest'), '0' * 64),
            ('capture snapshot', ('authority', 'pin', 'snapshot_digest'), '0' * 64),
            ('capture pointer', ('authority', 'profile_evidence', 'pointer', 'state'), 'recovering'),
            ('capture ready', ('authority', 'profile_evidence', 'ready', 'generation'), 2),
            ('capture mirror', ('authority', 'profile_evidence', 'mirror'), [dict(unknown=True)]),
        ):
            coding_case('Coding mapping ' + name, lambda conn, path=path, value=value:
                coding_json(conn, 'workflow_coding_mappings', 'mapping_bytes', 'generation=1', path, value,
                            digest_column='mapping_digest'),
                '_coding_role_records.py:190-210; coding_mapping_history.py:116-129 ' + name)
        for name, path, value in (
            ('phase', ('phase',), 'callback'), ('org', ('org_slug',), 'foreign'),
            ('native owner', ('native_owner', 'id'), 'foreign'),
            ('source admission', ('source_admission_id',), 'foreign'),
            ('command', ('command_bytes_base64',), 'e30='),
            ('session reference', ('session_start_audit_id',), 999999),
            ('original invocation', ('owner_invocation_id',), ''),
            ('original step', ('budgets', 'source_step'), 2),
            ('original revision', ('budgets', 'source_revision'), 1),
            ('original timeout', ('budgets', 'session_timeout_seconds'), 1),
            ('original attempt cap', ('budgets', 'coding_attempt_limit'), 65),
            ('original event cap', ('budgets', 'coding_event_limit'), 4097),
            ('original revision cap', ('budgets', 'coding_revision_limit'), 4),
            ('original retry cap', ('budgets', 'coding_same_principal_retry_limit'), 2),
            ('observed time', ('observed_at',), '2026-11-14T00:00:00.000000Z'),
            ('ancestry closure', ('ancestor_records',), [dict(columns=[])]),
        ):
            coding_case('Coding mapping source ' + name, lambda conn, path=path, value=value:
                coding_json(conn, 'audit_log', 'payload', "action='coding_source_admission'", path, value),
                '_coding_source_capture.py:204-305; _coding_mapping_admission.py:74-135 ' + name)
        for name, path, value in (
            ('format', ('format',), 'wrong'), ('org', ('org_slug',), 'foreign'),
            ('principal', ('principal',), 'agent:foreign'), ('operation', ('operation_key',), 'foreign'),
            ('request', ('request_digest',), '0' * 64), ('command', ('command_bytes_base64',), 'e30='),
            ('mapping reference', ('mapping_id',), 'foreign'), ('mapping generation', ('mapping_generation',), 4),
            ('mapping digest', ('mapping_digest',), '0' * 64), ('admission reference', ('admission_id',), 'foreign'),
            ('before snapshot', ('before',), {}), ('after snapshot', ('after', 'admission'), 'disabled'),
            ('closed publication', ('extra',), True),
        ):
            coding_case('Coding settings publication ' + name, lambda conn, path=path, value=value:
                coding_json(conn, 'audit_log', 'payload',
                    "action='coding_settings_publication' AND json_extract(payload,'$.mapping_generation')=2", path, value),
                '_coding_settings_history.py:113-151,173-192 ' + name)
        for name, sql in (
            ('missing config audit', "DELETE FROM audit_log WHERE action='org_config_write' AND json_extract(payload,'$.after.generation')=1"),
            ('missing publication', "DELETE FROM audit_log WHERE action='coding_settings_publication' AND json_extract(payload,'$.mapping_generation')=2"),
            ('publication scope', "UPDATE audit_log SET task_id='config:foreign' WHERE action='coding_settings_publication' AND json_extract(payload,'$.mapping_generation')=2"),
            ('publication agent', "UPDATE audit_log SET agent='foreign' WHERE action='coding_settings_publication' AND json_extract(payload,'$.mapping_generation')=2"),
            ('publication time', "UPDATE audit_log SET timestamp='2026-11-14T00:00:04.000000Z' WHERE action='coding_settings_publication' AND json_extract(payload,'$.mapping_generation')=2"),
            ('config scope', "UPDATE audit_log SET task_id='config:foreign' WHERE action='org_config_write' AND json_extract(payload,'$.after.generation')=1"),
            ('config agent', "UPDATE audit_log SET agent='foreign' WHERE action='org_config_write' AND json_extract(payload,'$.after.generation')=1"),
            ('config time', "UPDATE audit_log SET timestamp='2026-11-14T00:00:04.000000Z' WHERE action='org_config_write' AND json_extract(payload,'$.after.generation')=1"),
            ('missing current', "DELETE FROM org_settings WHERE section='coding'"),
            ('current value type', "UPDATE org_settings SET value_json=x'7b7d' WHERE section='coding'"),
            ('current timestamp', "UPDATE org_settings SET updated_at='2026-11-14T00:00:04.000000Z' WHERE section='coding'"),
            ('current principal', "UPDATE org_settings SET updated_by='foreign' WHERE section='coding'"),
            ('orphan admission', "INSERT INTO workflow_admission_records VALUES('coding-settings-admission:ffffffffffffffffffffffffffffffff','org/scratch',1,'synthetic','human:founder')"),
        ):
            coding_case('Coding settings ' + name, sql, '_coding_settings_history.py:95-164,195-219 ' + name)
        for name, path, value in (
            ('section', ('section',), 'foreign'), ('tiers', ('tiers',), []),
            ('before', ('before',), {}), ('after', ('after', 'admission'), 'disabled'),
            ('closed config', ('extra',), True),
        ):
            coding_case('Coding settings config ' + name, lambda conn, path=path, value=value:
                coding_json(conn, 'audit_log', 'payload',
                    "action='org_config_write' AND json_extract(payload,'$.after.generation')=1", path, value),
                '_coding_settings_history.py:95-108,140-147 ' + name)
        for name, path, value in (
            ('format', ('format',), 'wrong'), ('org', ('org_slug',), 'foreign'),
            ('generation', ('generation',), 1), ('admission', ('admission',), 'explicit'),
            ('release', ('release', 'version'), 2), ('mapping id', ('mapping', 'id'), 'foreign'),
            ('mapping generation', ('mapping', 'generation'), 2),
            ('mapping digest', ('mapping', 'digest'), '0' * 64), ('operation', ('operation_key',), 'foreign'),
            ('request', ('request_digest',), '0' * 64), ('predecessor', ('previous_snapshot_digest',), '0' * 64),
            ('published time', ('published_at',), '2026-11-14T00:00:04.000000Z'),
            ('published by', ('published_by',), 'agent:foreign'), ('closed snapshot', ('extra',), True),
        ):
            coding_case('Coding settings current ' + name, lambda conn, path=path, value=value:
                coding_json(conn, 'org_settings', 'value_json', "section='coding'", path, value),
                '_coding_settings_history.py:205-219; _coding_settings_records.py:48-67 ' + name)
        for name, path, value in (
            ('human issuer', ('issuer', 'proof_kind'), 'task_session'),
            ('human record org', ('org_slug',), 'foreign'),
            ('settings generation', ('settings', 'generation'), True),
            ('settings admission', ('settings', 'admission'), 'disabled'),
            ('settings predecessor', ('settings', 'previous_snapshot_digest'), 'bad'),
            ('human capture pin', ('authority', 'pin', 'generation'), 2),
        ):
            coding_case('Coding settings record ' + name, lambda conn, path=path, value=value:
                coding_json(conn, 'workflow_coding_mappings', 'mapping_bytes', 'generation=2', path, value,
                            digest_column='mapping_digest'),
                '_coding_settings_records.py:194-249 ' + name)
        # Nonallocating source history remains reachable with a terminal
        # native task and no queued graph. Shared mapping/capture predicates
        # above retain their keeper; these rows add custody/P/operation joins.
        from runtime.workflows.coding_mapping_history import CodingMappingHistoryError
        from runtime.workflows.coding_saved_records import CodingSavedRecordError
        from runtime.workflows.coding_source_history import CodingSourceHistoryError
        from runtime.workflows.coding_capability_history import CodingCapabilityHistoryError
        coding_source_names = set()
        coding_source_refusals = {}
        def source_case(name, mutation, predicate, error=CodingSourceHistoryError, code='coding_source_history_invalid'):
            name = 'Coding source ' + name
            coding_case(name, mutation, predicate)
            coding_source_names.add(name)
            coding_source_refusals[name] = (error, code)

        def corrupt_source_order(conn):
            operation = conn.execute("SELECT id FROM audit_log WHERE action='coding_source_operation' AND payload LIKE '%synthetic-source-capability%'").fetchone()[0]
            original = conn.execute("SELECT id FROM audit_log WHERE action='coding_source_admission' AND payload LIKE '%synthetic-source-capability%'").fetchone()[0]
            temporary = conn.execute('SELECT max(id)+1 FROM audit_log').fetchone()[0]
            conn.execute('UPDATE audit_log SET id=? WHERE id=?', (temporary, operation))
            conn.execute('UPDATE audit_log SET id=? WHERE id=?', (operation, original))
            conn.execute('UPDATE audit_log SET id=? WHERE id=?', (original, temporary))

        for column, value in (('id', 'foreign-capability'), ('namespace', 'org/foreign'), ('revision', 2),
                ('authority_bytes', b'{}'), ('authority_digest', '0' * 64),
                ('source_pin', 'wrong-source'), ('created_at', '2026-11-14T00:00:06.000000Z')):
            source_case('native capability ' + column, lambda conn, column=column, value=value:
                conn.execute('UPDATE workflow_authorization_revisions SET ' + column + '=? WHERE id=?',
                    (value, 'synthetic-source-capability')),
                'coding_capability_history.py:94-160 native ' + column,
                CodingCapabilityHistoryError, 'coding_capability_history_invalid')
        for name, sql in (
            ('missing operation', "DELETE FROM audit_log WHERE action='coding_source_operation'"),
            ('missing original', "DELETE FROM audit_log WHERE action='coding_source_admission' AND payload LIKE '%synthetic-source-capability%'"),
            ('missing capability', "DELETE FROM workflow_authorization_revisions WHERE id='synthetic-source-capability'"),
            ('missing own admission', "DELETE FROM workflow_admission_records WHERE id='synthetic-source-admission'"),
            ('own namespace', "UPDATE workflow_admission_records SET namespace='org/foreign' WHERE id='synthetic-source-admission'"),
            ('own generation', "UPDATE workflow_admission_records SET generation=2 WHERE id='synthetic-source-admission'"),
            ('own request', "UPDATE workflow_admission_records SET request_digest='bad' WHERE id='synthetic-source-admission'"),
            ('own principal', "UPDATE workflow_admission_records SET admitted_by='agent:foreign' WHERE id='synthetic-source-admission'"),
            ('operation native agent', "UPDATE audit_log SET agent='foreign' WHERE action='coding_source_operation'"),
            ('operation native task', "UPDATE audit_log SET task_id='source' WHERE action='coding_source_operation'"),
            ('operation before mint', "UPDATE audit_log SET timestamp='2026-11-14T00:00:04.000000Z' WHERE action='coding_source_operation'"),
            ('original before operation', corrupt_source_order),
        ):
            source_case(name, sql, '_coding_source_admission.py:68-151; coding_source_admission_history.py:136-167 ' + name)
        for name, path, value in (
            ('closed', ('extra',), True), ('format', ('format',), 'unknown'), ('org', ('org_slug',), 'foreign'),
            ('principal', ('principal',), 'agent:foreign'), ('authorization', ('authorization_id',), 'foreign'),
            ('admission', ('admission_id',), 'foreign'), ('session', ('session_id',), 'foreign'),
            ('command encoding', ('command_bytes_base64',), 'e30='), ('command digest', ('command_digest',), '0' * 64),
            ('created', ('created_at',), '2026-11-14T00:00:04.000000Z'),
        ):
            source_case('operation ' + name, lambda conn, path=path, value=value:
                coding_json(conn, 'audit_log', 'payload', "action='coding_source_operation' AND payload LIKE '%synthetic-source-capability%'", path, value),
                '_coding_source_admission.py:26-43,82-104 ' + name)
        def source_command(conn, path, value):
            import base64
            import hashlib
            row = conn.execute("SELECT id,payload FROM audit_log WHERE action='coding_source_operation' AND payload LIKE '%synthetic-source-capability%'").fetchone()
            operation = json.loads(row[1])
            command = json.loads(base64.b64decode(operation['command_bytes_base64']))
            target = command
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            raw = seq_history._bytes(command)
            operation.update(command_bytes_base64=base64.b64encode(raw).decode(),
                             command_digest=hashlib.sha256(raw).hexdigest())
            conn.execute('UPDATE audit_log SET payload=? WHERE id=?', (seq_history._bytes(operation).decode(), row[0]))
        for name, path, value in (
            ('closed', ('extra',), True), ('format', ('format',), 'unknown'), ('operation', ('operation_key',), ''),
            ('parent', ('parent_id',), 'foreign'), ('mapping id', ('mapping', 'id'), 'foreign'),
            ('mapping generation', ('mapping', 'generation'), 2), ('mapping digest', ('mapping', 'digest'), '0' * 64),
            ('action', ('action',), 'import-release-coding'), ('repository', ('work','repository_id'), 'workflow-repository:' + '0' * 64),
            ('store owner', ('work','store_owner'), 'foreign'), ('format radius', ('work','object_format'), 'sha256'),
            ('base', ('work','base'), '2' * 40), ('paths', ('work','paths'), ['other']),
            ('commands', ('work','commands'), [['synthetic']]),
        ):
            source_case('command ' + name, lambda conn, path=path, value=value: source_command(conn, path, value),
                'coding_saved_records.py:994-1017; _coding_source_admission.py:88-118 ' + name)
        for name, path, value in (
            ('closed', ('extra',), True), ('format', ('format',), 'unknown'), ('id', ('id',), 'foreign'),
            ('parent', ('parent_id',), 'foreign'), ('level', ('level',), 'attempt'),
            ('issuer kind', ('issuer','kind'), 'human'), ('issuer principal', ('issuer','principal'), 'foreign'),
            ('issuer task', ('issuer','task_id'), 'source'), ('issuer session', ('issuer','session_id'), 'foreign'),
            ('issuer result', ('issuer','result_id'), 1), ('subject org', ('subject','org_slug'), 'foreign'),
            ('subject principal', ('subject','principal'), 'foreign'), ('subject team', ('subject','team'), 'foreign'),
            ('subject source task', ('subject','source_task_id'), 'source'),
            ('subject source session', ('subject','source_session_id'), 'foreign'),
            ('subject instance', ('subject','instance_id'), 'foreign'), ('subject attempt', ('subject','attempt_id'), 'foreign'),
            ('subject control', ('subject','control_id'), 'foreign'), ('subject task', ('subject','task_id'), 'source'),
            ('subject session', ('subject','session_id'), 'foreign'),
            ('action containment', ('action',), 'start-coding'),
            ('repository containment', ('radius','repository_id'), 'workflow-repository:' + '0' * 64),
            ('store containment', ('radius','store_owner'), 'foreign'), ('format containment', ('radius','object_format'), 'sha256'),
            ('base syntax', ('radius','base'), 'bad'), ('path containment', ('radius','paths'), ['other']),
            ('command containment', ('radius','commands'), [['synthetic']]),
            ('provider closure', ('radius','provider_repository'), 'synthetic/foreign'),
            ('PR closure', ('radius','pr_number'), 1), ('head closure', ('radius','expected_head'), '1' * 40),
            ('head rule', ('radius','head_rule'), 'exact-subject@1'),
            ('capture admission', ('authority','admission_record_id'), 'foreign'),
            ('capture grant', ('authority','grant_digest'), '0' * 64),
            ('capture pin', ('authority','pin','generation'), 2),
            ('source admission', ('source_admission_id',), 'foreign'),
            ('prior mint', ('created_at',), '2026-11-13T00:00:00.000000Z'),
        ):
            source_case('capability ' + name, lambda conn, path=path, value=value:
                coding_json(conn, 'workflow_authorization_revisions', 'authority_bytes',
                    "id='synthetic-source-capability'", path, value, digest_column='authority_digest'),
                'coding_saved_records.py:807-940; coding_capability_history.py:143-169 ' + name,
                CodingSavedRecordError, 'coding_saved_record_invalid')
        for name, path, value in (
            ('format', ('mapping','format'), 'unknown'),
            ('adoption issuer', ('adoptions',0,'issuer','principal'), 'foreign'),
            ('adoption origin', ('adoptions',0,'observed_origin_digest'), '0' * 64),
            ('adoption identity', ('adoptions',0,'store_identity','repo_inode'), 5),
            ('adoption time', ('adoptions',0,'observed_at'), '2026-11-15T00:00:00.000000Z'),
            ('adoption checks', ('adoptions',0,'checks','no_helpers'), False),
            ('adoption format', ('adoptions',0,'object_format'), 'sha256'),
            ('binding recipient', ('mapping','repository_bindings',0,'binding','recipients',0,'principal'), 'foreign'),
            ('binding authority', ('mapping','repository_bindings',0,'binding','authority','generation'), 2),
            ('binding origin', ('mapping','repository_bindings',0,'binding','origin_digest'), '0' * 64),
            ('adoption policy', ('mapping','repository_bindings',0,'adoption','policy'), 'unknown'),
        ):
            source_case('custody mapping ' + name, lambda conn, path=path, value=value:
                coding_json(conn, 'workflow_coding_mappings', 'mapping_bytes', 'generation=1', path, value,
                            digest_column='mapping_digest'),
                'coding_saved_records.py:642-719; _coding_mapping_admission.py:91-116 ' + name,
                CodingMappingHistoryError, 'coding_mapping_history_invalid')


        for name, sql in (
            ('native ancestor cycle', "UPDATE tasks SET parent_task_id='synthetic-capability-source' WHERE id='synthetic-source-ancestor'"),
            ('native ancestor team', "UPDATE tasks SET team='foreign' WHERE id='synthetic-source-ancestor'"),
            ('native ancestor created', "UPDATE tasks SET created_at='2026-11-13T00:00:00.000000Z' WHERE id='synthetic-source-ancestor'"),
            ('native ancestor type', "UPDATE tasks SET task_type='subtask' WHERE id='synthetic-source-ancestor'"),
            ('native result agent', "UPDATE task_results SET agent='foreign' WHERE task_id='synthetic-capability-source'"),
            ('native result status', "UPDATE task_results SET status='' WHERE task_id='synthetic-capability-source'"),
            ('native result timestamp', "UPDATE task_results SET created_at='bad' WHERE task_id='synthetic-capability-source'"),
            ('native result decision', "UPDATE task_results SET decision_json='{' WHERE task_id='synthetic-capability-source'"),
            ('native result risks', "UPDATE task_results SET risks_flagged='{' WHERE task_id='synthetic-capability-source'"),
            ('native result jobs', "UPDATE task_results SET waiting_on_job_ids='{' WHERE task_id='synthetic-capability-source'"),
            ('native result CI', "UPDATE task_results SET local_ci='{' WHERE task_id='synthetic-capability-source'"),
            ('refinement missing parent operation', "DELETE FROM audit_log WHERE action='coding_source_operation' AND payload LIKE '%synthetic-source-capability%'"),
            ('refinement operation order', "UPDATE audit_log SET id=5 WHERE action='coding_source_operation' AND payload LIKE '%synthetic-source-refinement%'"),
            ('refinement native parent absent', "DELETE FROM workflow_authorization_revisions WHERE id='synthetic-source-capability'"),
        ):
            source_case(name, sql, 'coding_source_history.py:242-396; coding_source_admission_history.py:59-71; _coding_source_admission.py:120-140 ' + name)
        for name, path, value in (
            ('closed original', ('extra',), True),
            ('original task link', ('task_record','columns',0,'value'), 'Zm9yZWlnbg=='),
            ('original observed time', ('observed_at',), '2026-11-14T00:00:04.000000Z'),
            ('original own admission', ('source_admission_id',), 'synthetic-refinement-admission'),
            ('original native owner', ('native_owner','id'), 'synthetic-source-refinement'),
            ('original session start', ('session_start_audit_id',), 999),
            ('original command link', ('command_bytes_base64',), 'e30='),
            ('original callback absence', ('callback_admission_audit_id',), 1),
            ('original result absence', ('result',), {}),
            ('original ancestor closure', ('ancestor_records',), []),
        ):
            source_case(name, lambda conn, path=path, value=value:
                coding_json(conn, 'audit_log', 'payload',
                    "action='coding_source_admission' AND payload LIKE '%synthetic-source-capability%'", path, value),
                'coding_source_admission_history.py:74-97,138-167; _coding_source_capture.py:222-285 ' + name)
        def refined_capability(conn, path, value):
            import hashlib
            coding_json(conn, 'workflow_authorization_revisions', 'authority_bytes',
                "id='synthetic-source-refinement'", path, value, digest_column='authority_digest')
            raw = conn.execute("SELECT authority_bytes FROM workflow_authorization_revisions WHERE id='synthetic-source-refinement'").fetchone()[0]
            conn.execute("UPDATE workflow_admission_records SET request_digest=? WHERE id='synthetic-refinement-admission'",
                         (hashlib.sha256(raw).hexdigest(),))
        for name, path, value in (
            ('refinement issuer equality', ('issuer','session_id'), 'foreign'),
            ('refinement radius widening', ('radius','paths'), ['runtime/child', 'other']),
            ('refinement source admission', ('source_admission_id',), 'synthetic-refinement-admission'),
            ('refinement parent identity', ('parent_id',), 'foreign'),
        ):
            source_case(name, lambda conn, path=path, value=value: refined_capability(conn, path, value),
                'coding_saved_records.py:944-984 ' + name, CodingSavedRecordError, 'coding_saved_record_invalid')


        source_case('operation duplicate key', lambda conn: source_command(conn, ('operation_key',), 'synthetic-refinement'),
            '_coding_mapping_admission.py:42-46 operation uniqueness',
            CodingMappingHistoryError, 'coding_mapping_history_invalid')
        def corrupt_parent_bytes(conn):
            import base64
            import hashlib
            row = json.loads(conn.execute("SELECT authority_bytes FROM workflow_authorization_revisions WHERE id='synthetic-source-refinement'").fetchone()[0])
            parent = json.loads(base64.b64decode(row['authority']['grant_bytes_base64']))
            parent['created_at'] = '2026-11-14T00:00:04.000000Z'
            raw = seq_history._bytes(parent)
            row['authority'].update(grant_bytes_base64=base64.b64encode(raw).decode(), grant_digest=hashlib.sha256(raw).hexdigest())
            raw = seq_history._bytes(row)
            conn.execute("UPDATE workflow_authorization_revisions SET authority_bytes=?,authority_digest=? WHERE id='synthetic-source-refinement'",
                         (raw, hashlib.sha256(raw).hexdigest()))
            conn.execute("UPDATE workflow_admission_records SET request_digest=? WHERE id='synthetic-refinement-admission'",
                         (hashlib.sha256(raw).hexdigest(),))
        source_case('refinement native parent bytes', corrupt_parent_bytes,
            'coding_capability_history.py:168-170 complete native parent byte equality',
            CodingCapabilityHistoryError, 'coding_capability_history_invalid')

        # Exact first owners: the shared capture inventory refuses malformed
        # operations during mapping; native admissions and JSON retain their
        # original categories rather than being relabeled as source errors.
        from runtime.workflows.coding_authority_history import CodingAuthorityHistoryError
        for name, error, code in (
            ('Coding source native capability id', CodingSourceHistoryError, 'coding_source_history_invalid'),
            ('Coding source missing original', CodingSourceHistoryError, 'coding_source_capture_unavailable'),
            ('Coding source missing own admission', CodingAuthorityHistoryError, 'coding_authority_history_invalid'),
            ('Coding source own namespace', CodingAuthorityHistoryError, 'coding_authority_history_invalid'),
            ('Coding source own generation', CodingAuthorityHistoryError, 'coding_authority_history_invalid'),
            ('Coding source own request', CodingAuthorityHistoryError, 'coding_authority_history_invalid'),
            ('Coding source own principal', CodingAuthorityHistoryError, 'coding_authority_history_invalid'),
            ('Coding source operation native agent', CodingMappingHistoryError, 'coding_mapping_history_invalid'),
            ('Coding source operation before mint', CodingMappingHistoryError, 'coding_mapping_history_invalid'),
            ('Coding source operation closed', CodingMappingHistoryError, 'coding_mapping_history_invalid'),
            ('Coding source operation format', CodingMappingHistoryError, 'coding_mapping_history_invalid'),
            ('Coding source operation org', CodingMappingHistoryError, 'coding_mapping_history_invalid'),
            ('Coding source operation principal', CodingMappingHistoryError, 'coding_mapping_history_invalid'),
            ('Coding source operation command encoding', CodingMappingHistoryError, 'coding_mapping_history_invalid'),
            ('Coding source operation command digest', CodingMappingHistoryError, 'coding_mapping_history_invalid'),
            ('Coding source command closed', CodingMappingHistoryError, 'coding_mapping_history_invalid'),
            ('Coding source command format', CodingMappingHistoryError, 'coding_mapping_history_invalid'),
            ('Coding source command operation', CodingMappingHistoryError, 'coding_mapping_history_invalid'),
            ('Coding source command format radius', CodingMappingHistoryError, 'coding_mapping_history_invalid'),
            ('Coding source capability id', CodingCapabilityHistoryError, 'coding_capability_history_invalid'),
            ('Coding source native ancestor cycle', CodingSourceHistoryError, 'coding_source_history_invalid'),
            ('Coding source native ancestor created', CodingSourceHistoryError, 'coding_source_history_invalid'),
            ('Coding source native ancestor type', CodingSourceHistoryError, 'coding_source_history_invalid'),
            ('Coding source native result status', CodingAuthorityHistoryError, 'coding_authority_history_invalid'),
            ('Coding source native result decision', json.JSONDecodeError, 'Expecting property name enclosed in double quotes: line 1 column 2 (char 1)'),
            ('Coding source native result risks', json.JSONDecodeError, 'Expecting property name enclosed in double quotes: line 1 column 2 (char 1)'),
            ('Coding source native result jobs', json.JSONDecodeError, 'Expecting property name enclosed in double quotes: line 1 column 2 (char 1)'),
            ('Coding source native result CI', json.JSONDecodeError, 'Expecting property name enclosed in double quotes: line 1 column 2 (char 1)'),
            ('Coding source closed original', CodingMappingHistoryError, 'coding_mapping_history_invalid'),
            ('Coding source original task link', CodingMappingHistoryError, 'coding_mapping_history_invalid'),
            ('Coding source original callback absence', CodingMappingHistoryError, 'coding_mapping_history_invalid'),
            ('Coding source original result absence', CodingMappingHistoryError, 'coding_mapping_history_invalid'),
            ('Coding source original ancestor closure', CodingMappingHistoryError, 'coding_mapping_history_invalid'),
        ):
            coding_source_refusals[name] = (error, code)

        coding_history_corruptions = tuple((name, mutation) for name, mutation, _ in coding_history_checks)
        coding_history_names = {name for name, _ in coding_history_corruptions}
        refusal_codes.update({name: 'coding_mapping_history_invalid' for name in coding_history_names})
        refusal_codes.update({name: refusal[1] for name, refusal in coding_source_refusals.items()})


        if sys.argv[1:] == ['--retained-corruption']:
            corruptions = ()
        corruptions += tuple((name, mutation) for name, mutation, _ in retained_corruptions)
        if sys.argv[1:] == ['--document-coding-corruption']:
            corruptions = ()
        if sys.argv[1:] != ['--retained-corruption']:
            corruptions += tuple((name, mutation) for name, mutation, _ in document_coding_corruptions)
        if sys.argv[1:] == ['--coding-authority-corruption']:
            corruptions = coding_import_corruptions + coding_setup_corruptions
        elif sys.argv[1:] in ([], ['--sequential-reuse'], ['--sequential-corruption'], ['--document-coding-corruption']):
            corruptions += coding_import_corruptions + coding_setup_corruptions
        if sys.argv[1:] == ['--document-native-corruption']:
            corruptions = document_native_corruptions
        elif sys.argv[1:] in ([], ['--sequential-reuse'], ['--sequential-corruption'], ['--document-coding-corruption']):
            corruptions += document_native_corruptions
        if sys.argv[1:] == ['--author-handoff-corruption']:
            corruptions = author_handoff_corruptions + author_graph_corruptions
        elif sys.argv[1:] in ([], ['--sequential-reuse'], ['--sequential-corruption'], ['--document-coding-corruption']):
            corruptions += author_handoff_corruptions + author_graph_corruptions
        if sys.argv[1:] == ['--document-revision-corruption']:
            corruptions = family_corruptions
        elif sys.argv[1:] in ([], ['--sequential-reuse'], ['--sequential-corruption'], ['--document-coding-corruption']):
            corruptions += family_corruptions
        if sys.argv[1:] == ['--coding-history-corruption']:
            corruptions = coding_history_corruptions
        elif sys.argv[1:] in ([], ['--sequential-reuse'], ['--sequential-corruption'], ['--document-coding-corruption']):
            corruptions += coding_history_corruptions
        def corrupt_physical_index(connection, *, surplus):
            # Synthetic private corruption: preserve the exact index definition
            # while making SQLite change its table without index maintenance.
            entry = tuple(connection.execute("SELECT rowid,type,name,tbl_name,rootpage,sql "
                "FROM sqlite_schema WHERE name='load_integrity_index'").fetchone())
            cookie = connection.execute('PRAGMA schema_version').fetchone()[0]
            connection.execute('PRAGMA writable_schema=ON')
            try:
                connection.execute('DELETE FROM sqlite_schema WHERE rowid=?', (entry[0],))
                connection.execute('PRAGMA schema_version=' + str(cookie + 1))
                connection.execute('PRAGMA writable_schema=RESET')
                if surplus:
                    connection.execute('DELETE FROM load_integrity_probe WHERE id=1')
                else:
                    connection.execute("UPDATE load_integrity_probe SET value='changed' WHERE id=1")
                connection.execute('PRAGMA writable_schema=ON')
                connection.execute('INSERT INTO sqlite_schema(rowid,type,name,tbl_name,rootpage,sql) '
                    'VALUES(?,?,?,?,?,?)', entry)
                connection.execute('PRAGMA schema_version=' + str(cookie + 2))
            finally:
                connection.execute('PRAGMA writable_schema=RESET')
            restored = tuple(connection.execute("SELECT rowid,type,name,tbl_name,rootpage,sql "
                "FROM sqlite_schema WHERE name='load_integrity_index'").fetchone())
            assert restored == entry, 'physical fixture changed the original index definition'

        physical_corruptions = (
            ('physical missing index entry', lambda conn: corrupt_physical_index(conn, surplus=False)),
            ('physical surplus index entry', lambda conn: corrupt_physical_index(conn, surplus=True)),
        )
        physical_names = {name for name, _ in physical_corruptions}
        refusal_codes.update({name: 'install_integrity_failed' for name in physical_names})
        if sys.argv[1:] == ['--physical-corruption']:
            corruptions = physical_corruptions
        elif sys.argv[1:] in ([], ['--sequential-reuse'], ['--sequential-corruption']):
            corruptions += physical_corruptions
        corruption_failures = []
        corruption_cases = [(row, phase) for row in corruptions
            for phase in ('first', 'between local', 'between external', 'between uncommitted')
            if (retained_phase is None or phase == retained_phase)
            if sys.argv[1:] != ['--sequential-uncommitted'] or
                (row[0] in sequential_names and phase == 'between uncommitted')]
        for (name, mutation), phase in corruption_cases:
            with sequential_fixture() as (db, root, sql):
                populate_sequential(db)
                if name in physical_names:
                    db._conn.execute('CREATE TABLE load_integrity_probe (id INTEGER PRIMARY KEY, value TEXT)')
                    db._conn.execute('CREATE INDEX load_integrity_index ON load_integrity_probe(value)')
                    db._conn.execute("INSERT INTO load_integrity_probe VALUES(1,'original')")
                    db._conn.commit()
                if name in retained_names:
                    populate_retained(db, root)
                if name in document_coding_names:
                    populate_document_coding(db)
                if name in document_native_names:
                    populate_document_native(db)
                if name in author_handoff_names:
                    populate_author_handoff(db)
                if name in author_graph_names:
                    populate_handoff_graph(db)
                if name in family_populators:
                    family_populators[name](db)
                if name in coding_history_names:
                    (populate_coding_source if name in coding_source_names else populate_coding_mapping)(db)
                if name in coding_import_names or name in coding_setup_names:
                    populate_coding_authority(db, setup=name in coding_setup_names)
                # Warm outside the new owner too: a forbidden cross-load cache
                # must not certify even the first use in the following scope.
                seq_read(db)
                try:
                    with org_load_validation(db, org_slug='scratch'):
                        owner = load_guard._current.get()
                        if phase != 'first':
                            seq_read(db)
                            assert owner.sequential_saved is not None, 'nonempty baseline failed to certify'
                        hits = owner.hits
                        before = db._conn.total_changes
                        with sqlite3.connect(db.db_path) as writer:
                            connection = db._conn if phase in ('between local', 'between uncommitted') else writer
                            # Restore FK ON before reading: the FK mutation
                            # must reach the complete checker, not config fallback.
                            if name not in retained_names:
                                connection.execute('PRAGMA foreign_keys=OFF')
                            if phase == 'between uncommitted':
                                connection.execute('BEGIN')
                            # Corruption preparation alone suspends immutable
                            # fixture triggers; restore their exact SQL before
                            # either reader sees this private image. Schema
                            # parity then prevents a missing-trigger false negative.
                            triggers = list(connection.execute(
                                "SELECT name,sql FROM sqlite_schema WHERE type='trigger' AND tbl_name=?",
                                (mutation.split()[1] if isinstance(mutation, str) and mutation.startswith('UPDATE ') else '',)))
                            for trigger, ddl in triggers:
                                connection.execute('DROP TRIGGER "' + trigger.replace('"', '""') + '"')
                            if callable(mutation):
                                mutation(connection)
                            else:
                                connection.execute(mutation)
                            for trigger, ddl in triggers:
                                connection.execute(ddl)
                            if phase != 'between uncommitted':
                                connection.commit()
                            if name not in retained_names:
                                connection.execute('PRAGMA foreign_keys=ON')
                        if phase not in ('between local', 'between uncommitted'):
                            assert db._conn.total_changes == before, 'external fixture changed local counter'
                        expected = sequential_outcome(lambda: read_sequential_history(
                            db._conn, expected_org_slug='scratch').retained)
                        if name in physical_names:
                            # I2 moves physical-only detection to the real I1 checker.
                            # Keep both corruptions and all four timing phases.
                            from runtime.infrastructure.workflow_integrity import check_full_integrity, IntegrityLedger
                            assert expected[0] == 'returned', name + ' logical read unexpectedly refused'
                            actual = sequential_outcome(lambda: seq_read(db))
                            assert actual == expected, name + ' changed logical load outcome'
                            result = check_full_integrity(db._conn, IntegrityLedger(time.monotonic()+5))
                            assert result.outcome == 'failed' and result.cause == 'full_integrity_not_ok', (name, result)
                            assert db._conn.in_transaction == (phase == 'between uncommitted'), name + ' physical checker stole snapshot'
                            if phase == 'between uncommitted':
                                db._conn.rollback()
                                db._conn.execute('PRAGMA foreign_keys=ON')
                                seq_read(db)
                        else:
                            assert expected[0] == 'refused', name + ' fixture was accepted by complete reader'
                            assert all(category[0] is not ReadExhaustion for category in expected[1]), name + ' hidden by exhaustion'
                            if name in retained_names:
                                from runtime.infrastructure.team_move_store import TeamMoveError
                                error_type = (PauseControlError if name.startswith('Pause ') else
                                              names.NamingError if name.startswith('name ') else TeamMoveError)
                            elif name == 'document compiler pin':
                                from runtime.workflows.templates import WorkflowTemplateError
                                error_type = WorkflowTemplateError
                            elif name in coding_history_names:
                                from runtime.workflows.coding_mapping_history import CodingMappingHistoryError
                                error_type = coding_source_refusals[name][0] if name in coding_source_names else CodingMappingHistoryError
                            elif name in coding_setup_names:
                                from runtime.workflows.coding_authority_history import CodingAuthorityHistoryError
                                error_type = CodingAuthorityHistoryError
                            elif name in family_populators and refusal_codes[name] == 'workflow_document_revision_invalid_request':
                                from runtime.workflows.activation import WorkflowActivationError
                                error_type = WorkflowActivationError
                            else:
                                error_type = ValueError
                            assert expected[1][0] == (error_type, refusal_codes[name]), name + ' missed its semantic boundary: ' + repr(expected)
                            actual = sequential_outcome(lambda: seq_read(db))
                            assert actual == expected, name + ' corruption changed complete refusal'
                            assert owner.hits == hits and owner.sequential_saved is None, name + ' corruption retained a certificate'
                            assert db._conn.in_transaction == (phase == 'between uncommitted'), name + ' corruption changed snapshot ownership'
                            if phase == 'between uncommitted':
                                db._conn.rollback()
                                db._conn.execute('PRAGMA foreign_keys=ON')
                                full = owner.full_runs
                                seq_read(db)
                                assert owner.full_runs == full + 1 and owner.hits == hits, name + ' rollback resurrected a certificate'
                    print('PASS A0b sequential corruption matrix:', name, phase, refusal_codes[name], flush=True)
                except AssertionError as exc:
                    corruption_failures.append((name, phase, str(exc)))
                    print('FAIL A0b sequential corruption matrix:', name, phase, str(exc), flush=True)
        assert not corruption_failures, 'sequential corruption matrix failures: ' + repr(corruption_failures)
        if sys.argv[1:] in (['--sequential-corruption'], ['--retained-corruption'], ['--document-coding-corruption'], ['--coding-authority-corruption'], ['--document-native-corruption'], ['--author-handoff-corruption'], ['--document-revision-corruption'], ['--coding-history-corruption'], ['--physical-corruption'], ['--sequential-uncommitted']):
            raise SystemExit(0)

    if sys.argv[1:] in ([], ['--sequential-reuse'], ['--sequential-faults'], ['--sequential-sql-exhaustion']):
        fault_failures = []
        @contextmanager
        def sequential_fault_case(name):
            try:
                yield
            except AssertionError as exc:
                fault_failures.append((name, str(exc)))
                print('FAIL A0b sequential fault:', name, str(exc), flush=True)

        # Add real VM work inside the original A/B/C/L progress contexts.
        # Delegation preserves each actual allowance and the original deadline;
        # no counter is preset and no native/progress limit is reduced.
        for phase, allowance in product(('first', 'changed source', 'changed target'), ('A', 'B', 'C', 'L')):
            name = 'original SQL ' + allowance + ' ' + phase
            with sequential_fault_case(name), sequential_fixture() as (db, root, sql):
                seq_read(db)  # Cross-owner caching must not hide first-read exhaustion.
                with org_load_validation(db, org_slug='scratch'):
                    owner = load_guard._current.get()
                    if phase != 'first':
                        seq_read(db)
                        db._conn.execute("UPDATE tasks SET brief=brief||'sql' WHERE id='TASK-seq-load'")
                        db._conn.commit()
                    if phase == 'changed target':
                        db._conn.execute('BEGIN IMMEDIATE')
                        db._conn.execute("UPDATE tasks SET brief=brief||'target' WHERE id='TASK-seq-load'")
                    hits = owner.hits
                    def observe_sql_exhaustion(operation):
                        selected, charged = [None], []
                        original_layout, original_sql = seq_history._read_layout, seq_history._sql_budget
                        def select_layout(conn, observed, reference, data):
                            selected[0] = dict(A=observed, B=reference, C=data).get(allowance)
                            return original_layout(conn, observed, reference, data)
                        @contextmanager
                        def metered(conn, budget):
                            with original_sql(conn, budget):
                                if not charged and (budget is selected[0] or
                                        (allowance == 'L' and budget.sql_limit == 32000000)):
                                    charged.append(budget)
                                    conn.execute('WITH RECURSIVE load_work(n) AS '
                                        '(VALUES(0) UNION ALL SELECT n+1 FROM load_work WHERE n<4000000) '
                                        'SELECT sum(n) FROM load_work').fetchone()
                                yield
                        with patch.object(seq_history, '_read_layout', select_layout), patch.object(
                                seq_history, '_sql_budget', metered):
                            outcome = sequential_outcome(operation)
                        return outcome, charged
                    operation = (lambda: load_guard.read_workflow_schema_target_for_org_load(
                        db._conn, expected_org_slug='scratch', legacy=lambda: validate_workflow_schema(
                            db._conn, expected_org_slug='scratch'))) if phase == 'changed target' else (
                        lambda: seq_read(db))
                    try:
                        actual, actual_charged = observe_sql_exhaustion(operation)
                        expected, expected_charged = observe_sql_exhaustion(lambda: read_sequential_history(
                            db._conn, expected_org_slug='scratch').retained)
                        assert actual == expected and actual[0] == 'refused', name + ' changed complete refusal'
                        for charged in (actual_charged, expected_charged):
                            assert len(charged) == 1, name + ' omitted its original native SQL meter'
                            budget = charged[0]
                            limit = 32000000 if allowance == 'L' else 2000000
                            exhaustion = budget._sql_exhaustion
                            assert type(budget) is seq_history._ReadBudget and budget.sql_limit == limit
                            assert budget.sql_steps == limit + 1000, name + ' changed its original SQL limit'
                            assert isinstance(exhaustion, ReadExhaustion) and exhaustion.allowance is budget
                            assert (exhaustion.dimension, exhaustion.requested, exhaustion.remaining) == ('sql', 1000, 0)
                        assert owner.hits == hits and owner.sequential_saved is None, name + ' retained an exhausted certificate'
                        assert db._conn.in_transaction == (phase == 'changed target'), name + ' stole native snapshot ownership'
                    finally:
                        if db._conn.in_transaction:
                            db._conn.rollback()
                print('PASS A0b sequential SQL exhaustion:', allowance, phase, flush=True)
        if sys.argv[1:] == ['--sequential-sql-exhaustion']:
            assert not fault_failures, 'sequential SQL exhaustion failures: ' + repr(fault_failures)
            raise SystemExit(0)

        # Extend the legacy metadata keeper to the complete sequential branch.
        # A native denial must fall back/refuse exactly like the public reader;
        # checking only the final tuple would miss an illicit successful hit.
        for phase, pragma in product(('initial', 'primed'), ('data_version', 'schema_version', 'database_list',
                       'foreign_keys', 'defer_foreign_keys', 'read_uncommitted',
                       'writable_schema', 'ignore_check_constraints',
                       'recursive_triggers', 'trusted_schema', 'query_only',
                       'journal_mode', 'locking_mode', 'synchronous', 'temp_store')):
            name = ('initial metadata ' if phase == 'initial' else 'metadata ') + pragma
            with sequential_fault_case(name), sequential_fixture() as (db, root, sql):
                with org_load_validation(db, org_slug='scratch'):
                    owner = load_guard._current.get()
                    if phase == 'primed':
                        seq_read(db)
                        assert owner.sequential_saved is not None
                    else:
                        assert owner.sequential_saved is None
                    denied = []
                    def deny(action, arg1, *_):
                        if action == sqlite3.SQLITE_PRAGMA and arg1 == pragma:
                            denied.append(arg1)
                            return sqlite3.SQLITE_DENY
                        return sqlite3.SQLITE_OK
                    db._conn.set_authorizer(deny)
                    try:
                        hits = owner.hits
                        expected = sequential_outcome(lambda: read_sequential_history(
                            db._conn, expected_org_slug='scratch').retained)
                        for attempt in range(2):
                            actual = sequential_outcome(lambda: seq_read(db))
                            assert denied and actual == expected, name + ' denial changed complete outcome'
                            assert owner.hits == hits and owner.sequential_saved is None, name + ' denial returned a sequential hit'
                            assert not db._conn.in_transaction, name + ' denial leaked owned snapshot'
                    finally:
                        db._conn.set_authorizer(None)
                    full = sql.count('PRAGMA foreign_key_check')
                    seq_read(db)
                    assert sql.count('PRAGMA foreign_key_check') == full + 1 and owner.hits == hits, name + ' restoration resurrected a fact'
                    assert not db._conn.in_transaction, name + ' restoration leaked owned snapshot'
                print('PASS A0b sequential metadata denial:', phase, pragma)

        for limit, value in ((sqlite3.SQLITE_LIMIT_SQL_LENGTH, 16), (sqlite3.SQLITE_LIMIT_COLUMN, 1)):
            with sequential_fault_case('initial native limit denial ' + str(limit)), sequential_fixture() as (db, root, sql):
                previous = db._conn.setlimit(limit, value)
                db._conn.set_authorizer(lambda *_: sqlite3.SQLITE_OK)
                try:
                    with org_load_validation(db, org_slug='scratch'):
                        owner = load_guard._current.get()
                        expected = sequential_outcome(lambda: read_sequential_history(
                            db._conn, expected_org_slug='scratch').retained)
                        for attempt in range(2):
                            actual = sequential_outcome(lambda: seq_read(db))
                            assert actual == expected and actual[0] == 'refused', 'initial native limit denial changed complete outcome'
                            assert owner.hits == 0 and owner.sequential_saved is None, 'initial native limit denial certified reuse'
                            assert not db._conn.in_transaction, 'initial native limit denial leaked snapshot'
                        db._conn.setlimit(limit, previous)
                        db._conn.set_authorizer(None)
                        full = sql.count('PRAGMA foreign_key_check')
                        seq_read(db)
                        assert sql.count('PRAGMA foreign_key_check') == full + 1 and owner.hits == 0, 'initial native limit restoration resurrected a fact'
                        assert not db._conn.in_transaction, 'initial native limit restoration leaked snapshot'
                finally:
                    db._conn.setlimit(limit, previous)
                    db._conn.set_authorizer(None)
                print('PASS A0b sequential initial native limit denial:', limit)

        # Restoring a setting before the next read also restores the metadata
        # tuple. Only observation of the actual assignments invalidates reuse.
        for pragma in ('foreign_keys', 'defer_foreign_keys', 'read_uncommitted',
                       'writable_schema', 'ignore_check_constraints',
                       'recursive_triggers', 'trusted_schema', 'query_only',
                       'synchronous', 'temp_store'):
            with sequential_fault_case('restored config ' + pragma), sequential_fixture() as (db, root, sql):
                if pragma == 'temp_store':
                    # The installer used then dropped a TEMP staging table.
                    # Start with its empty native TEMP schema reset, so changing
                    # and restoring temp_store really restores the cookie too.
                    db._conn.execute('PRAGMA temp_store=2')
                    db._conn.execute('PRAGMA temp_store=0')
                with org_load_validation(db, org_slug='scratch'):
                    seq_read(db)
                    owner = load_guard._current.get()
                    before = load_guard._bookend(db._conn)
                    previous = db._conn.execute('PRAGMA ' + pragma).fetchone()[0]
                    changed = 2 if pragma == 'temp_store' else (0 if previous else 1)
                    db._conn.execute(f'PRAGMA {pragma}={changed}')
                    assert db._conn.execute('PRAGMA ' + pragma).fetchone()[0] != previous, pragma + ' fixture did not change configuration'
                    db._conn.execute(f'PRAGMA {pragma}={previous}')
                    assert load_guard._bookend(db._conn) == before, pragma + ' fixture failed to restore metadata'
                    hits = owner.hits
                    full = sql.count('PRAGMA foreign_key_check')
                    actual = sequential_outcome(lambda: seq_read(db))
                    assert owner.hits == hits and sql.count('PRAGMA foreign_key_check') == full + 1, pragma + ' restored configuration returned a hit'
                    expected = sequential_outcome(lambda: read_sequential_history(
                        db._conn, expected_org_slug='scratch').retained)
                    assert actual == expected and actual[0] == 'returned', pragma + ' restoration changed complete outcome'
                    assert not db._conn.in_transaction, pragma + ' restoration leaked owned snapshot'
                print('PASS A0b sequential restored configuration:', pragma)

        for config in ('native config', 'SQL limit', 'row factory', 'text factory', 'isolation level',
                       'query_only', 'foreign_keys', 'defer_foreign_keys',
                       'read_uncommitted', 'writable_schema', 'ignore_check_constraints',
                       'recursive_triggers', 'trusted_schema',
                       'journal_mode', 'locking_mode', 'synchronous', 'temp_store'):
            with sequential_fault_case('config ' + config), sequential_fixture() as (db, root, sql):
                conn = db._conn
                with org_load_validation(db, org_slug='scratch'):
                    seq_read(db)
                    owner = load_guard._current.get()
                    if config == 'native config':
                        option = sqlite3.SQLITE_DBCONFIG_DEFENSIVE
                        previous = conn.getconfig(option)
                        conn.setconfig(option, not previous)
                        restore = lambda: conn.setconfig(option, previous)
                    elif config == 'SQL limit':
                        option = sqlite3.SQLITE_LIMIT_SQL_LENGTH
                        previous = conn.getlimit(option)
                        conn.setlimit(option, previous - 1)
                        restore = lambda: conn.setlimit(option, previous)
                    elif config == 'row factory':
                        previous = conn.row_factory
                        conn.row_factory = lambda cursor, row: tuple(row)
                        restore = lambda: setattr(conn, 'row_factory', previous)
                    elif config == 'text factory':
                        previous = conn.text_factory
                        conn.text_factory = bytes
                        restore = lambda: setattr(conn, 'text_factory', previous)
                    elif config == 'isolation level':
                        previous = conn.isolation_level
                        conn.isolation_level = None
                        restore = lambda: setattr(conn, 'isolation_level', previous)
                    else:
                        previous = conn.execute('PRAGMA ' + config).fetchone()[0]
                        value = dict(journal_mode='DELETE', locking_mode='EXCLUSIVE',
                                     synchronous=0, temp_store=2).get(config)
                        if value is None:
                            value = 1 - previous
                        conn.execute(f'PRAGMA {config}={value}')
                        assert conn.execute('PRAGMA ' + config).fetchone()[0] != previous, config + ' fixture did not change native configuration'
                        restore = lambda: conn.execute(f'PRAGMA {config}={previous}')
                    try:
                        hits = owner.hits
                        actual = sequential_outcome(lambda: seq_read(db))
                        expected = sequential_outcome(lambda: read_sequential_history(
                            conn, expected_org_slug='scratch').retained)
                        assert actual == expected, config + ' changed complete outcome'
                        assert owner.hits == hits, config + ' change returned a sequential hit'
                    finally:
                        restore()
                    # The restored configuration cannot resurrect its old fact.
                    before = sql.count('PRAGMA foreign_key_check')
                    seq_read(db)
                    assert sql.count('PRAGMA foreign_key_check') == before + 1, config + ' restoration resurrected a sequential fact'
                print('PASS A0b sequential config identity:', config)

        for autocommit in (True, False):
            with sequential_fault_case('autocommit ' + str(autocommit)), sequential_fixture() as (db, root, sql):
                conn = db._conn
                with org_load_validation(db, org_slug='scratch'):
                    seq_read(db)
                    owner = load_guard._current.get()
                    hits = owner.hits
                    conn.autocommit = autocommit
                    try:
                        # Preserve the complete reader's native ownership even
                        # when Python autocommit makes rollback() a no-op.
                        initial_transaction = conn.in_transaction
                        expected = sequential_outcome(lambda: read_sequential_history(
                            conn, expected_org_slug='scratch').retained)
                        expected_transaction = conn.in_transaction
                        if not initial_transaction and conn.in_transaction:
                            conn.execute('ROLLBACK')
                        actual = sequential_outcome(lambda: seq_read(db))
                        assert actual == expected, 'autocommit changed complete outcome'
                        assert conn.in_transaction == expected_transaction, 'autocommit changed complete snapshot ownership'
                        assert owner.hits == hits and owner.sequential_saved is None, 'autocommit returned a sequential hit'
                    finally:
                        conn.autocommit = sqlite3.LEGACY_TRANSACTION_CONTROL
                        conn.rollback()
                    before = sql.count('PRAGMA foreign_key_check')
                    seq_read(db)
                    assert sql.count('PRAGMA foreign_key_check') == before + 1, 'autocommit restoration resurrected a fact'
                print('PASS A0b sequential autocommit identity:', autocommit)

        for attachment in ('additional database', 'restored ATTACH/DETACH', 'missing main', 'dangling main'):
            with sequential_fault_case('attachment ' + attachment), sequential_fixture() as (db, root, sql):
                conn = db._conn
                with org_load_validation(db, org_slug='scratch'):
                    seq_read(db)
                    owner = load_guard._current.get()
                    hits = owner.hits
                    before = load_guard._bookend(conn)
                    original = Path(db.db_path)
                    held = original.with_suffix('.held')
                    if attachment in ('additional database', 'restored ATTACH/DETACH'):
                        conn.execute("ATTACH DATABASE ':memory:' AS extra")
                        if attachment == 'restored ATTACH/DETACH':
                            conn.execute('DETACH DATABASE extra')
                            assert load_guard._bookend(conn) == before, 'ATTACH/DETACH failed to restore native metadata'
                        try:
                            full = sql.count('PRAGMA foreign_key_check')
                            actual = sequential_outcome(lambda: seq_read(db))
                            assert owner.hits == hits, attachment + ' returned a sequential hit'
                            if attachment == 'additional database':
                                expected = sequential_outcome(lambda: read_sequential_history(
                                    conn, expected_org_slug='scratch').retained)
                                assert actual == expected and owner.sequential_saved is None, 'attached database changed complete outcome or certified'
                            else:
                                assert actual[0] == 'returned' and sql.count('PRAGMA foreign_key_check') == full + 1, 'restored ATTACH/DETACH skipped complete validation'
                        finally:
                            if attachment == 'additional database':
                                conn.execute('DETACH DATABASE extra')
                    else:
                        # Move only this private synthetic DB; the native main
                        # connection remains open. No live path is touched.
                        original.rename(held)
                        if attachment == 'dangling main':
                            original.symlink_to(original.with_suffix('.absent'))
                        try:
                            actual = sequential_outcome(lambda: seq_read(db))
                            assert actual == ('refused', ((ValueError, 'workflow_load_attachment_changed'),)), attachment + ' lost explicit attachment refusal'
                            assert owner.hits == hits and owner.sequential_saved is None, attachment + ' retained a certificate'
                            assert not conn.in_transaction, attachment + ' leaked an owned snapshot'
                        finally:
                            if original.is_symlink():
                                original.unlink()
                            held.rename(original)
                    full = sql.count('PRAGMA foreign_key_check')
                    seq_read(db)
                    if attachment != 'restored ATTACH/DETACH':
                        assert sql.count('PRAGMA foreign_key_check') == full + 1, attachment + ' restoration resurrected a fact'
                print('PASS A0b sequential attachment identity:', attachment)

        for limit, value in ((sqlite3.SQLITE_LIMIT_SQL_LENGTH, 64), (sqlite3.SQLITE_LIMIT_COLUMN, 1)):
            with sequential_fault_case('native limit denial ' + str(limit)), sequential_fixture() as (db, root, sql):
                with org_load_validation(db, org_slug='scratch'):
                    seq_read(db)
                    owner = load_guard._current.get()
                    previous = db._conn.setlimit(limit, value)
                    # Native authorizer installation expires SQLite's prepared
                    # statement cache so these small limits genuinely deny SQL.
                    db._conn.set_authorizer(lambda *_: sqlite3.SQLITE_OK)
                    try:
                        hits = owner.hits
                        actual = sequential_outcome(lambda: seq_read(db))
                        expected = sequential_outcome(lambda: read_sequential_history(
                            db._conn, expected_org_slug='scratch').retained)
                        assert actual == expected and actual[0] == 'refused', 'native limit denial returned a sequential tuple'
                        assert owner.hits == hits and owner.sequential_saved is None, 'native limit denial retained a sequential certificate'
                        assert not db._conn.in_transaction, 'native limit denial leaked its snapshot'
                    finally:
                        db._conn.setlimit(limit, previous)
                        db._conn.set_authorizer(None)
                print('PASS A0b sequential native limit denial:', limit)

        with sequential_fixture() as (db, root, sql):
            for schema, method in product(('main', 'temp'), ('connection', 'cursor', 'script')):
                with sequential_fault_case('CREATE/DROP ' + schema + ' ' + method), org_load_validation(db, org_slug='scratch'):
                    seq_read(db)
                    owner = load_guard._current.get()
                    for repeat in range(2):
                        before = load_guard._bookend(db._conn)
                        full = owner.full_runs
                        create = f'/* cached CREATE/DROP */ -- native execution\n CREATE TABLE {schema}.seq_pair (v)'
                        drop = f'/* cached CREATE/DROP */ DROP TABLE {schema}.seq_pair'
                        if method == 'script':
                            db._conn.executescript('BEGIN; ' + create + '; ' + drop + '; ROLLBACK;')
                        else:
                            db._conn.execute('BEGIN')
                            cursor = db._conn if method == 'connection' else db._conn.cursor()
                            try:
                                cursor.execute(create)
                                cursor.execute(drop)
                            finally:
                                if cursor is not db._conn:
                                    cursor.close()
                            db._conn.rollback()
                        assert load_guard._bookend(db._conn) == before, 'CREATE/DROP fixture did not restore metadata'
                        seq_read(db)
                        assert owner.full_runs == full + 1, 'restored CREATE/DROP returned a sequential hit'
                    print('PASS A0b sequential restored CREATE/DROP:', schema, method)

            for ownership in ('wrong org', 'arbitrary source', 'arbitrary target', 'savepoint source'):
                with sequential_fault_case('ownership ' + ownership), org_load_validation(db, org_slug='scratch'):
                    seq_read(db)
                    owner = load_guard._current.get()
                    hits = owner.hits
                    slug = 'another-org' if ownership == 'wrong org' else 'scratch'
                    if ownership != 'wrong org':
                        db._conn.execute('SAVEPOINT arbitrary' if ownership == 'savepoint source' else 'BEGIN')
                        db._conn.execute('SELECT rootpage FROM main.sqlite_schema LIMIT 1').fetchone()
                    try:
                        if ownership == 'arbitrary target':
                            operation = lambda: load_guard.read_workflow_schema_target_for_org_load(
                                db._conn, expected_org_slug=slug, legacy=lambda: validate_workflow_schema(
                                    db._conn, expected_org_slug=slug))
                        else:
                            operation = lambda: read_workflow_schema_for_org_load(db._conn,
                                expected_org_slug=slug, writer_source=ownership != 'wrong org')
                        actual = sequential_outcome(operation)
                        expected = sequential_outcome(lambda: read_sequential_history(
                            db._conn, expected_org_slug=slug).retained)
                        assert actual == expected and owner.hits == hits, ownership + ' escaped sequential owner identity'
                        assert db._conn.in_transaction == (ownership != 'wrong org'), ownership + ' stole caller transaction'
                        assert owner.sequential_saved is None, ownership + ' retained an ineligible certificate'
                    finally:
                        db._conn.rollback()
                    print('PASS A0b sequential ownership exclusion:', ownership)

            with sequential_fault_case('ownership wrong connection'), sequential_fixture() as (other, other_root, other_sql):
                with org_load_validation(db, org_slug='scratch'):
                    seq_read(db)
                    owner = load_guard._current.get()
                    hits = owner.hits
                    before = other_sql.count('PRAGMA foreign_key_check')
                    actual = seq_read(other)
                    assert actual == read_sequential_history(other._conn, expected_org_slug='scratch').retained
                    assert owner.hits == hits and owner.sequential_saved is None, 'wrong connection borrowed a sequential certificate'
                    assert other_sql.count('PRAGMA foreign_key_check') == before + 2
                print('PASS A0b sequential ownership exclusion: wrong connection')

        # Transactions that predate observation have unknown ownership, even
        # when their SQL resembles an audited source or a fresh cutover target.
        for kind, target in product(('reader', 'pinned reader', 'writer', 'savepoint'), (False, True)):
            name = 'preexisting ownership ' + kind + (' target' if target else ' source')
            with sequential_fault_case(name), sequential_fixture() as (db, root, sql):
                conn = db._conn
                conn.execute({'reader': 'BEGIN', 'pinned reader': 'BEGIN',
                              'writer': 'BEGIN IMMEDIATE', 'savepoint': 'SAVEPOINT original'}[kind])
                if kind == 'pinned reader':
                    conn.execute('SELECT rootpage FROM main.sqlite_schema LIMIT 1').fetchone()
                try:
                    with org_load_validation(db, org_slug='scratch'):
                        owner = load_guard._current.get()
                        assert owner.transaction_mode is None, name + ' fixture observed preexisting transaction'
                        for repeat in range(2):
                            full = sql.count('PRAGMA foreign_key_check')
                            operation = (lambda: load_guard.read_workflow_schema_target_for_org_load(
                                conn, expected_org_slug='scratch', legacy=lambda: validate_workflow_schema(
                                    conn, expected_org_slug='scratch'))) if target else (
                                lambda: seq_read(db, writer_source=True))
                            actual = sequential_outcome(operation)
                            assert sql.count('PRAGMA foreign_key_check') == full + 1, name + ' bypassed complete fallback'
                            expected = sequential_outcome(lambda: read_sequential_history(
                                conn, expected_org_slug='scratch').retained)
                            assert actual == expected and actual[0] == 'returned', name + ' changed complete outcome'
                            assert conn.in_transaction and owner.hits == 0 and owner.sequential_saved is None, name + ' stole ownership or certified unknown interval'
                        conn.rollback()  # Only the original caller releases it.
                        full = sql.count('PRAGMA foreign_key_check')
                        seq_read(db)
                        assert sql.count('PRAGMA foreign_key_check') == full + 1 and owner.sequential_saved is not None, name + ' exit did not start a fresh complete fact'
                finally:
                    conn.rollback()
                print('PASS A0b sequential unknown transaction ownership:', name)

        with sequential_fault_case('initial attachment unavailable'), sequential_fixture() as (db, root, sql):
            original = Path(db.db_path)
            held = original.with_suffix('.initial-held')
            original.rename(held)
            try:
                with org_load_validation(db, org_slug='scratch'):
                    owner = load_guard._current.get()
                    assert owner.attachment is None, 'initial attachment fixture retained identity'
                    for repeat in range(2):
                        full = sql.count('PRAGMA foreign_key_check')
                        actual = sequential_outcome(lambda: seq_read(db))
                        assert sql.count('PRAGMA foreign_key_check') == full + 1, 'unknown initial attachment skipped complete read'
                        expected = sequential_outcome(lambda: read_sequential_history(
                            db._conn, expected_org_slug='scratch').retained)
                        assert actual == expected and owner.hits == 0 and owner.sequential_saved is None, 'unknown initial attachment certified reuse'
                        assert not db._conn.in_transaction, 'unknown initial attachment leaked snapshot'
                    held.rename(original)
                    detached = original.with_suffix('.initial-detached')
                    replacement = root / 'initial-replacement.db'
                    sqlite3.connect(replacement).close()
                    try:
                        for phase in ('restored', 'replaced'):
                            if phase == 'replaced':
                                original.rename(detached)
                                replacement.rename(original)
                            for repeat in range(2):
                                full = sql.count('PRAGMA foreign_key_check')
                                foreign = sql.count('PRAGMA foreign_key_check')
                                actual = sequential_outcome(lambda: seq_read(db))
                                assert sql.count('PRAGMA foreign_key_check') == full + 1, phase + ' initial attachment skipped complete read'
                                assert sql.count('PRAGMA foreign_key_check') == foreign + 1, phase + ' initial attachment skipped FK read'
                                expected = sequential_outcome(lambda: read_sequential_history(
                                    db._conn, expected_org_slug='scratch').retained)
                                assert actual == expected and actual[0] == 'returned', phase + ' initial attachment changed complete outcome'
                                assert owner.hits == 0 and owner.sequential_saved is None and owner.certificate == 0, phase + ' initial attachment certified reuse'
                                assert owner.attachment is None, phase + ' initial attachment fabricated identity'
                                assert not db._conn.in_transaction, phase + ' initial attachment leaked snapshot'
                            print('PASS A0b sequential initial attachment unavailable:', phase)
                    finally:
                        if detached.exists():
                            original.unlink()
                            detached.rename(original)
            finally:
                if held.exists():
                    held.rename(original)
            print('PASS A0b sequential initial attachment unavailable')

        for race in ('source acquisition', 'source writer fence', 'target acquisition', 'target pinned snapshot'):
            with sequential_fault_case('borrowed ' + race), sequential_fixture() as (db, root, sql):
                other = sqlite3.connect(db.db_path, timeout=0)
                try:
                    with org_load_validation(db, org_slug='scratch'):
                        seq_read(db)
                        owner = load_guard._current.get()
                        moved, fenced = [], []
                        def corrupt():
                            other.execute("UPDATE workflow_task_ownership SET org_slug='wrong'")
                            other.commit()
                            moved.append(True)
                        def at_boundary(statement):
                            if moved or fenced:
                                return
                            if race == 'source acquisition' and statement == 'BEGIN IMMEDIATE':
                                corrupt()  # Native callback precedes the writer reservation.
                            elif race == 'target acquisition' and statement == 'SELECT rootpage FROM main.sqlite_schema LIMIT 1':
                                corrupt()  # BEGIN exists, but its main snapshot is not pinned yet.
                            elif race == 'target pinned snapshot' and statement == 'PRAGMA main.data_version':
                                corrupt()  # Explicit main pin has selected the original snapshot.
                        with db.observe_trace(at_boundary):
                            db._conn.execute('BEGIN IMMEDIATE' if race.startswith('source') else 'BEGIN')
                            try:
                                if race == 'source writer fence':
                                    try:
                                        corrupt()
                                    except sqlite3.OperationalError as exc:
                                        assert str(exc) == 'database is locked', str(exc)
                                        fenced.append(True)
                                        other.rollback()
                                    else:
                                        raise AssertionError('borrowed source lost its native writer fence')
                                hits = owner.hits
                                pins = sql.count('SELECT rootpage FROM main.sqlite_schema LIMIT 1')
                                operation = (lambda: seq_read(db, writer_source=True)) if race.startswith('source') else (
                                    lambda: load_guard.read_workflow_schema_target_for_org_load(db._conn,
                                        expected_org_slug='scratch', legacy=lambda: validate_workflow_schema(
                                            db._conn, expected_org_slug='scratch')))
                                actual = sequential_outcome(operation)
                                assert sql.count('SELECT rootpage FROM main.sqlite_schema LIMIT 1') == pins + 1, race + ' omitted borrowed main pin'
                                assert moved or fenced, race + ' did not reach its native barrier'
                                expected = sequential_outcome(lambda: read_sequential_history(
                                    db._conn, expected_org_slug='scratch').retained)
                                assert actual == expected and db._conn.in_transaction, race + ' changed selected snapshot or stole caller transaction'
                                if race in ('source acquisition', 'target acquisition'):
                                    assert actual[0] == 'refused' and owner.hits == hits, race + ' returned speculative reuse'
                                else:
                                    assert actual[0] == 'returned' and owner.hits == hits + 1, race + ' lost valid pinned reuse'
                            finally:
                                db._conn.rollback()
                        if race == 'target pinned snapshot':
                            # The old result was valid only for the selected caller
                            # snapshot. Its native exit must expose the committed corruption.
                            actual = sequential_outcome(lambda: seq_read(db))
                            expected = sequential_outcome(lambda: read_sequential_history(
                                db._conn, expected_org_slug='scratch').retained)
                            assert actual == expected and actual[0] == 'refused', 'borrowed target exit hid committed corruption'
                    print('PASS A0b sequential borrowed race:', race)
                finally:
                    other.rollback()
                    other.close()

        for interval in ('source', 'target', 'changed target'):
            with sequential_fault_case('deadline ' + interval), sequential_fixture() as (db, root, sql):
                with org_load_validation(db, org_slug='scratch'):
                    seq_read(db)
                    owner = load_guard._current.get()
                    db._conn.execute('BEGIN' if interval == 'target' else 'BEGIN IMMEDIATE')
                    if interval == 'changed target':
                        db._conn.execute("UPDATE tasks SET brief=brief||'expired' WHERE id='TASK-seq-load'")
                    real_clock, offset = time.monotonic, [0.0]
                    original_deadline = real_clock() + 5.0
                    def expire(statement):
                        if statement == 'SELECT rootpage FROM main.sqlite_schema LIMIT 1':
                            offset[0] = 6.0
                    try:
                        with db.observe_trace(expire), patch.object(time, 'monotonic', side_effect=lambda: real_clock() + offset[0]):
                            operation = (lambda: seq_read(db, writer_source=True)) if interval == 'source' else (
                                lambda: load_guard.read_workflow_schema_target_for_org_load(db._conn,
                                    expected_org_slug='scratch', legacy=lambda: validate_workflow_schema(
                                        db._conn, expected_org_slug='scratch')))
                            actual = sequential_outcome(operation)
                            expected = sequential_outcome(lambda: read_sequential_history(db._conn,
                                expected_org_slug='scratch', deadline=original_deadline).retained)
                            assert offset[0] == 6.0 and actual == expected and actual[0] == 'refused', interval + ' reset the borrowed acquisition deadline'
                            assert any(category[1] == 'workflow_seq_read_deadline_exhausted' for category in actual[1]), interval + ' did not refuse at original deadline'
                            assert db._conn.in_transaction and owner.sequential_saved is None, interval + ' deadline stole caller transaction or retained a certificate'
                    finally:
                        db._conn.rollback()
                print('PASS A0b sequential borrowed deadline:', interval)

        # The same original five-second deadline covers entry, real complete
        # work and the final native snapshot release. Trace barriers advance a
        # test clock; neither the real budget nor validator is replaced.
        for state, boundary in product(('first', 'primed source', 'changed target'),
                                       ('entry', 'integrity', 'release')):
            name = 'whole deadline ' + state + ' ' + boundary
            with sequential_fault_case(name), sequential_fixture() as (db, root, sql):
                with org_load_validation(db, org_slug='scratch'):
                    if state != 'first':
                        seq_read(db)
                    owner = load_guard._current.get()
                    if state == 'changed target':
                        db._conn.execute("UPDATE tasks SET brief=brief||'deadline' WHERE id='TASK-seq-load'")
                        db._conn.commit()
                    hits = owner.hits
                    real_clock = time.monotonic
                    def observe_expiry(operation):
                        offset, reached, exhaustions = [0.0], [], []
                        deadline = real_clock() + 5.0
                        def expire(statement):
                            if not reached and (boundary == 'entry' or
                                    (boundary == 'integrity' and statement == 'PRAGMA foreign_key_check') or
                                    (boundary == 'release' and statement == 'ROLLBACK')):
                                reached.append(statement)
                                offset[0] = 6.0
                        with db.observe_trace(expire), patch.object(time, 'monotonic',
                                side_effect=lambda: real_clock() + offset[0]):
                            def observed():
                                try:
                                    return operation(deadline)
                                except (ValueError, sqlite3.Error) as exc:
                                    cause = exc
                                    while cause is not None:
                                        if isinstance(cause, ReadExhaustion):
                                            exhaustions.append((cause.dimension, cause.requested, cause.remaining))
                                        cause = cause.__cause__
                                    raise
                            outcome = sequential_outcome(observed)
                        return outcome, reached, exhaustions
                    # At entry/release the primed source exercises hit acquisition;
                    # its integrity variant exercises a genuine post-write miss.
                    if state == 'primed source' and boundary == 'integrity':
                        db._conn.execute("UPDATE tasks SET brief=brief||'middle' WHERE id='TASK-seq-load'")
                        db._conn.commit()
                    operation = (lambda deadline: load_guard.read_workflow_schema_target_for_org_load(
                        db._conn, expected_org_slug='scratch', legacy=lambda: validate_workflow_schema(
                            db._conn, expected_org_slug='scratch'))) if state == 'changed target' else (
                        lambda deadline: seq_read(db))
                    actual, actual_barrier, actual_exhaustions = observe_expiry(operation)
                    expected, expected_barrier, expected_exhaustions = observe_expiry(lambda deadline: read_sequential_history(
                        db._conn, expected_org_slug='scratch', deadline=deadline).retained)
                    assert actual_barrier and expected_barrier, name + ' did not reach native expiry barriers'
                    assert actual == expected and actual[0] == 'refused', name + ' reset the original deadline'
                    # Native progress interruption keeps its historical 'interrupted'
                    # code; the causal allowance supplies deadline attribution.
                    for exhausted in (actual_exhaustions, expected_exhaustions):
                        assert exhausted and all(dimension == 'deadline' and remaining <= 0
                            for dimension, requested, remaining in exhausted), name + ' lost original deadline attribution'
                    assert owner.hits == hits and owner.sequential_saved is None, name + ' retained an expired certificate'
                    assert not db._conn.in_transaction, name + ' leaked an owned snapshot'
                assert not owner.active and not owner.ledgers and load_guard._current.get() is None
                print('PASS A0b sequential whole deadline:', state, boundary)

        for release in ('owned before ROLLBACK', 'owned after ROLLBACK',
                        'source COMMIT', 'source ROLLBACK',
                        'target COMMIT', 'target ROLLBACK'):
            with sequential_fault_case('release race ' + release), sequential_fixture() as (db, root, sql):
                other = sqlite3.connect(db.db_path, timeout=0)
                try:
                    with org_load_validation(db, org_slug='scratch'):
                        seq_read(db)
                        owner = load_guard._current.get()
                        moved = []
                        def corrupt():
                            other.execute("UPDATE workflow_task_ownership SET org_slug='wrong'")
                            other.commit()
                            moved.append(True)
                        hits = owner.hits
                        if release.startswith('owned'):
                            released = []
                            def at_exit(statement):
                                if moved:
                                    return
                                if statement == 'ROLLBACK':
                                    if release == 'owned before ROLLBACK':
                                        corrupt()  # WAL writer precedes native release.
                                    else:
                                        released.append(True)
                                elif released and not db._conn.in_transaction:
                                    corrupt()  # Native release finished; outside bookend has not.
                            with db.observe_trace(at_exit):
                                actual = sequential_outcome(lambda: seq_read(db))
                        else:
                            source = release.startswith('source')
                            db._conn.execute('BEGIN IMMEDIATE' if source else 'BEGIN')
                            operation = (lambda: seq_read(db, writer_source=True)) if source else (
                                lambda: load_guard.read_workflow_schema_target_for_org_load(db._conn,
                                    expected_org_slug='scratch', legacy=lambda: validate_workflow_schema(
                                        db._conn, expected_org_slug='scratch')))
                            assert sequential_outcome(operation)[0] == 'returned'
                            assert db._conn.in_transaction and owner.hits == hits + 1, release + ' lost borrowed snapshot'
                            # The caller alone releases its native reservation.
                            db._conn.execute(release.split()[1])
                            assert not db._conn.in_transaction, release + ' failed native release'
                            corrupt()
                            hits = owner.hits
                            actual = sequential_outcome(lambda: seq_read(db))
                        expected = sequential_outcome(lambda: read_sequential_history(
                            db._conn, expected_org_slug='scratch').retained)
                        assert moved, release + ' omitted native snapshot release barrier'
                        assert actual == expected and actual[0] == 'refused', release + ' hid committed corruption'
                        assert owner.hits == hits and owner.sequential_saved is None, release + ' reused a released certificate'
                        assert not db._conn.in_transaction, release + ' stole or leaked transaction ownership'
                    print('PASS A0b sequential release race:', release)
                finally:
                    other.rollback()
                    other.close()

        assert not fault_failures, 'sequential fault matrix failures: ' + repr(fault_failures)
        if sys.argv[1:] == ['--sequential-faults']:
            raise SystemExit(0)

    if sys.argv[1:] != ['--sequential-load']:
        with sequential_fixture() as (db, root, sql):
            with org_load_validation(db, org_slug='scratch'):
                first = seq_read(db)
                owner = load_guard._current.get()
                assert owner.sequential_saved is not None, 'sequential source did not certify complete validation'
                for _ in range(9):
                    assert seq_read(db) is first, 'same-load sequential tuple was not reused'
                assert owner.full_runs == 1 and owner.hits == 9, 'sequential selection bypass repeated complete reads'
                assert sql.count('PRAGMA foreign_key_check') == 1 and not any('integrity_check' in statement.lower() or 'quick_check' in statement.lower() for statement in sql)
                original = owner.sequential_saved[3]
                assert all(value > 0 for value in original), 'complete source did not retain original charges'
                caller = _Budget()
                seq_read(db, budget=caller)
                assert counters(caller) == original, 'fresh caller did not import retained charges once'
                seq_read(db, budget=caller)
                assert counters(caller) == original, 'same fact charged twice to original caller'
                db._conn.execute('BEGIN IMMEDIATE')
                try:
                    seq_read(db, budget=caller, writer_source=True)
                    load_guard.read_workflow_schema_target_for_org_load(db._conn,
                        expected_org_slug='scratch', budget=caller,
                        legacy=lambda: validate_workflow_schema(db._conn, expected_org_slug='scratch'))
                    assert db._conn.in_transaction, 'sequential target stole caller transaction'
                    assert counters(caller) == original, 'unchanged source/target double charged'
                    begin = max(i for i, statement in enumerate(sql) if statement == 'BEGIN IMMEDIATE')
                    assert sql[begin + 1] == 'SELECT rootpage FROM main.sqlite_schema LIMIT 1', 'borrowed source lost explicit main snapshot pin before metadata'
                    db._conn.execute("UPDATE tasks SET brief=brief||'x' WHERE id='TASK-seq-load'")
                    seq_read(db, budget=caller, writer_source=True)
                    assert counters(caller) == tuple(2 * value for value in original), 'changed target reset or lost cumulative work'
                    assert owner.full_runs == 2, 'local write reused stale sequential validation'
                finally:
                    db._conn.rollback()
                seq_read(db)
                assert owner.full_runs == 3, 'rollback resurrected old sequential certificate'
            assert owner.sequential_saved is None and not owner.ledgers, 'scope exit leaked certificate or ledger'
            print('PASS A0b sequential ledger: ten reads, import once, unchanged target, changed target and rollback')

            for site in ('cutover', 'ownership', 'naming'):
                org = SimpleNamespace(root=root, db=db, slug='scratch')
                if site == 'naming':
                    reconcile_namespace(org, Namespace((), ()), install=True)
                with org_load_validation(db, org_slug='scratch'):
                    seq_read(db)
                    before = sql.count('PRAGMA foreign_key_check')
                    if site == 'cutover':
                        WorkflowCutoverStore(db, org_slug='scratch').recover_authorized()
                    elif site == 'ownership':
                        install_task_ownership(db, org_slug='scratch')
                    else:
                        reconcile_namespace(org, Namespace((), ()), install=True, compare_only=True)
                    assert sql.count('PRAGMA foreign_key_check') == before, site + ' sequential unchanged target read fresh'
                    if site == 'cutover':
                        begin = max(i for i, statement in enumerate(sql) if statement == 'BEGIN')
                        assert sql[begin + 1] == 'SELECT rootpage FROM main.sqlite_schema LIMIT 1', 'cutover target lost fresh explicit main snapshot pin'
                print('PASS A0b sequential targets:', site, 'original transaction and complete source')

            for boundary in ('BEGIN', 'ROLLBACK'):
                other = sqlite3.connect(db.db_path)
                try:
                    with org_load_validation(db, org_slug='scratch'):
                        seq_read(db)
                        before = sql.count('PRAGMA foreign_key_check')
                        moved = []
                        def move(statement):
                            if statement == boundary and not moved:
                                moved.append(True)
                                other.execute("UPDATE tasks SET brief=brief||'r' WHERE id='TASK-seq-load'")
                                other.commit()
                        with db.observe_trace(move):
                            seq_read(db)
                        assert moved and sql.count('PRAGMA foreign_key_check') == before + 1, boundary + ' race returned speculative reuse'
                        assert not db._conn.in_transaction, 'owned snapshot leaked after race'
                finally:
                    other.close()
                print('PASS A0b sequential snapshot race:', boundary)

            for schema, method in product(('main', 'temp'), ('connection', 'cursor', 'script')):
                with org_load_validation(db, org_slug='scratch'):
                    seq_read(db)
                    owner = load_guard._current.get()
                    for repeat in range(2):
                        before = load_guard._bookend(db._conn)
                        full = owner.full_runs
                        ddl = f'/* repeated */ -- execution observer\n CREATE TABLE {schema}.seq_transient (v)'
                        if method == 'script':
                            db._conn.executescript('BEGIN; ' + ddl + '; ROLLBACK;')
                        else:
                            db._conn.execute('BEGIN')
                            cursor = db._conn if method == 'connection' else db._conn.cursor()
                            try:
                                cursor.execute(ddl)
                            finally:
                                if cursor is not db._conn:
                                    cursor.close()
                            db._conn.rollback()
                        assert load_guard._bookend(db._conn) == before
                        seq_read(db)
                        assert owner.full_runs == full + 1, 'sequential restored DDL returned stale tuple'
                print('PASS A0b sequential observer:', schema, method, 'cached rolled-back DDL')

            for pragma in ('query_only', 'defer_foreign_keys'):
                with org_load_validation(db, org_slug='scratch'):
                    seq_read(db)
                    owner = load_guard._current.get()
                    full = owner.full_runs
                    db._conn.execute(f'PRAGMA {pragma}(ON)')
                    if pragma == 'defer_foreign_keys':
                        assert owner.bookend(db._conn) is None, 'sequential deferred FK allowed a hit'
                    db._conn.execute(f'PRAGMA {pragma}=OFF')
                    seq_read(db)
                    assert owner.full_runs == full + 1, 'sequential restored config returned stale tuple'
                print('PASS A0b sequential observer: restored', pragma)

            # Real corruption, differential against the unchanged public reader.
            for external in (False, True):
                other = sqlite3.connect(db.db_path) if external else db._conn
                try:
                    with org_load_validation(db, org_slug='scratch'):
                        seq_read(db)
                        owner = load_guard._current.get()
                        local = db._conn.total_changes
                        other.execute("UPDATE workflow_task_ownership SET org_slug='wrong'")
                        if external:
                            other.commit()
                            assert db._conn.total_changes == local
                        errors = []
                        for operation in (lambda: seq_read(db, writer_source=not external),
                                          lambda: read_sequential_history(db._conn, expected_org_slug='scratch')):
                            try:
                                operation()
                            except ValueError as exc:
                                cause = exc
                                while cause is not None:
                                    assert not isinstance(cause, ReadExhaustion), 'corruption hidden by exhaustion'
                                    cause = cause.__cause__
                                errors.append((type(exc), str(exc)))
                            else:
                                raise AssertionError('corrupt sequential ownership disappeared behind a hit')
                        assert errors[0] == errors[1], 'fresh/reused corruption refusals differ'
                        assert owner.sequential_saved is None
                finally:
                    if external:
                        other.execute("UPDATE workflow_task_ownership SET org_slug='scratch'")
                        other.commit()
                        other.close()
                    else:
                        db._conn.rollback()
                print('PASS A0b sequential corruption:', 'external' if external else 'local', 'exact fresh refusal')

            for variant in ('mark', 'schema'):
                with org_load_validation(db, org_slug='scratch'):
                    seq_read(db)
                    owner = load_guard._current.get()
                    hits, local = owner.hits, db._conn.total_changes
                    before = db._conn.execute('PRAGMA main.data_version').fetchone()[0]
                    with sqlite3.connect(db.db_path) as writer:
                        writer.execute("UPDATE workflow_task_ownership SET org_slug='wrong'" if variant == 'mark' else
                                       'ALTER TABLE workflow_seq_definitions ADD COLUMN unexpected TEXT')
                        writer.commit()
                        changed = db._conn.execute('PRAGMA main.data_version').fetchone()[0]
                        assert changed != before and db._conn.total_changes == local, 'external mutation omitted independent version/counter evidence'
                        expected = sequential_outcome(lambda: read_sequential_history(db._conn, expected_org_slug='scratch').retained)
                        actual = sequential_outcome(lambda: seq_read(db))
                        assert actual == expected and actual[0] == 'refused', 'external ' + variant + ' escaped complete refusal'
                        assert all(kind is not ReadExhaustion for kind, _ in actual[1]), 'external mutation hidden by exhaustion'
                        assert owner.hits == hits and owner.sequential_saved is None
                        writer.execute("UPDATE workflow_task_ownership SET org_slug='scratch'" if variant == 'mark' else
                                       'ALTER TABLE workflow_seq_definitions DROP COLUMN unexpected')
                        writer.commit()
                    assert db._conn.execute('PRAGMA main.data_version').fetchone()[0] != changed
                    assert db._conn.total_changes == local
                    full = sql.count('PRAGMA foreign_key_check')
                    restored = seq_read(db)
                    assert restored == read_sequential_history(db._conn, expected_org_slug='scratch').retained
                    assert sql.count('PRAGMA foreign_key_check') == full + 2 and owner.hits == hits, 'external restoration reused a certificate'
                    assert not db._conn.in_transaction
                print('PASS A0b sequential external restore:', variant, 'data_version changed, local counter fixed, fresh complete read')

            for exclusion in ('arbitrary snapshot', 'public', 'nondefault budget', 'unaccounted budget', 'validator policy'):
                with org_load_validation(db, org_slug='scratch'):
                    seq_read(db)
                    before = sql.count('PRAGMA foreign_key_check')
                    if exclusion == 'arbitrary snapshot':
                        db._conn.execute('BEGIN')
                        db._conn.execute('SELECT rootpage FROM sqlite_schema LIMIT 1').fetchone()
                        try:
                            seq_read(db)
                            assert db._conn.in_transaction
                        finally:
                            db._conn.rollback()
                    elif exclusion == 'public':
                        read_sequential_history(db._conn, expected_org_slug='scratch')
                    elif exclusion == 'nondefault budget':
                        class Nondefault(_Budget):
                            pass
                        try:
                            seq_read(db, budget=Nondefault())
                        except ValueError as exc:
                            assert str(exc) == 'workflow_coding_data_invalid_budget'
                        else:
                            raise AssertionError('nondefault budget bypassed original exact-type refusal')
                    elif exclusion == 'unaccounted budget':
                        seq_read(db, budget=_Budget(consumed=1))
                    else:
                        # Observe by delegation to the REAL reader, never stub success.
                        with patch.object(seq_history, 'read_sequential_history', wraps=read_sequential_history):
                            seq_read(db)
                    if exclusion != 'nondefault budget':
                        assert sql.count('PRAGMA foreign_key_check') == before + 1, exclusion + ' escaped reuse policy'
                    else:
                        assert load_guard._current.get().sequential_saved is None, 'nondefault budget retained reuse'
                print('PASS A0b sequential exclusion:', exclusion)

            for dimension, limit in (('consumed', 16 * 1048576), ('accepted_rows', 8192), ('traversed', 16384)):
                with org_load_validation(db, org_slug='scratch'):
                    seq_read(db)
                    # A fresh known caller first imports the actual complete charge.
                    ledger = _Budget()
                    seq_read(db, budget=ledger)
                    setattr(ledger, dimension, limit)
                    db._conn.execute("UPDATE tasks SET brief=brief||'e' WHERE id='TASK-seq-load'")
                    db._conn.commit()
                    before = counters(ledger)
                    fresh_ledger = _Budget(*before)
                    errors = []
                    for operation in (lambda: seq_read(db, budget=ledger),
                                      lambda: read_sequential_history(db._conn,
                                          expected_org_slug='scratch', retained_budget=fresh_ledger)):
                        try:
                            operation()
                        except ValueError as exc:
                            errors.append((type(exc), str(exc)))
                        else:
                            raise AssertionError('changed sequential target widened original ' + dimension + ' allowance')
                    assert errors[0] == errors[1], 'original exhaustion category changed'
                    assert counters(ledger) == counters(fresh_ledger), 'changed target reset cumulative original charges'
                    assert all(a >= b for a, b in zip(counters(ledger), before)), 'exhausted caller allowance reset'
                print('PASS A0b sequential exhaustion: original cumulative', dimension)

            import asyncio
            import threading
            for execution in ('thread', 'async task'):
                before = []
                with org_load_validation(db, org_slug='scratch'):
                    seq_read(db)
                    context = copy_context()
                    count = sql.count('PRAGMA foreign_key_check')
                    errors = []
                    def foreign():
                        try:
                            seq_read(db)
                        except BaseException as exc:
                            errors.append(exc)
                    if execution == 'thread':
                        worker = threading.Thread(target=lambda: context.run(foreign))
                        worker.start()
                        worker.join(10)
                        assert not worker.is_alive(), 'foreign execution did not return'
                    else:
                        async def foreign_task():
                            foreign()
                        asyncio.run(foreign_task())
                    assert not errors and sql.count('PRAGMA foreign_key_check') == count + 1, 'sequential certificate crossed ' + execution
                print('PASS A0b sequential exclusion: foreign', execution)

            with org_load_validation(db, org_slug='scratch'):
                seq_read(db)
                inherited = copy_context()
            before = sql.count('PRAGMA foreign_key_check')
            inherited.run(seq_read, db)
            with org_load_validation(db, org_slug='scratch'):
                seq_read(db)
            seq_read(db)
            assert sql.count('PRAGMA foreign_key_check') == before + 3, 'sequential certificate escaped load lifetime'
            print('PASS A0b sequential exclusion: inherited inactive context, fresh load and outside scope')

            with org_load_validation(db, org_slug='scratch'):
                seq_read(db)
                owner = load_guard._current.get()
                original_bookend = owner.bookend
                calls = []
                class SnapshotAbort(BaseException):
                    pass
                abort = SnapshotAbort('synthetic owned snapshot interruption')
                def interrupted_bookend(conn):
                    result = original_bookend(conn)
                    calls.append(True)
                    if len(calls) == 2:
                        conn.set_authorizer(lambda action, arg1, *_: sqlite3.SQLITE_DENY
                            if action == sqlite3.SQLITE_TRANSACTION and arg1 == 'ROLLBACK' else sqlite3.SQLITE_OK)
                        raise abort
                    return result
                try:
                    with patch.object(owner, 'bookend', interrupted_bookend):
                        seq_read(db)
                except BaseException as exc:
                    assert exc is abort and exc.__notes__ == ['Workflow load snapshot cleanup failed'], 'snapshot cleanup replaced primary BaseException'
                    assert owner.observer_failed and owner.sequential_saved is None
                else:
                    raise AssertionError('snapshot interruption suppressed')
                finally:
                    db._conn.set_authorizer(None)
                    db._conn.rollback()
            print('PASS A0b sequential BaseException: owned snapshot cleanup failure preserves primary and disables reuse')

            with org_load_validation(db, org_slug='scratch'):
                seq_read(db)
                owner = load_guard._current.get()
                real_clock = time.monotonic
                offset = [0.0]
                def advance_clock(statement):
                    if statement == 'SELECT rootpage FROM main.sqlite_schema LIMIT 1':
                        offset[0] = 6.0
                with db.observe_trace(advance_clock), patch.object(time, 'monotonic', side_effect=lambda: real_clock() + offset[0]):
                    try:
                        seq_read(db)
                    except ValueError as exc:
                        assert str(exc) == 'workflow_seq_read_deadline_exhausted'
                    else:
                        raise AssertionError('sequential hit reset the original acquisition deadline')
                assert not db._conn.in_transaction and owner.sequential_saved is None
            print('PASS A0b sequential deadline: hit acquisition expiry preserves original deadline and cleanup')

        for failure in ('installation', 'delivery', 'classification', 'lost native slot'):
            with sequential_fixture() as (db, root, sql):
                @contextmanager
                def unavailable(callback):
                    raise RuntimeError('synthetic unavailable registration')
                    yield
                def bad_delivery(statement):
                    raise ValueError('synthetic delivery error')
                with ExitStack() as stack:
                    if failure == 'installation':
                        stack.enter_context(patch.object(db, 'observe_trace', unavailable))
                    with org_load_validation(db, org_slug='scratch'):
                        seq_read(db)
                        owner = load_guard._current.get()
                        assert (owner.sequential_saved is None) == (failure == 'installation'), 'observer failure fixture lacked a live certificate'
                        if failure == 'delivery':
                            stack.enter_context(db.observe_trace(bad_delivery))
                        elif failure == 'classification':
                            db._conn.execute('PRAGMA page_count').fetchone()
                        elif failure == 'lost native slot':
                            # Adversarial contract violation: prove the next real
                            # observation notices absence without a native getter.
                            db._conn.set_trace_callback(None)
                        hits = owner.hits
                        seq_read(db)
                        seq_read(db)
                        assert owner.hits == hits and owner.sequential_saved is None, failure + ' retained sequential reuse'
                print('PASS A0b sequential observer failure:', failure)


        with sequential_fixture() as (db, root, sql):
            with org_load_validation(db, org_slug='scratch'):
                seq_read(db)
                replacement = root / 'replacement.db'
                other = sqlite3.connect(replacement)
                other.close()
                replacement.replace(db.db_path)
                try:
                    seq_read(db)
                except ValueError as exc:
                    assert str(exc) == 'workflow_load_attachment_changed'
                else:
                    raise AssertionError('replaced attachment validated old open descriptor')
            print('PASS A0b sequential attachment: replaced file refuses old descriptor')

    # Actual installed OrgState calls all ten discriminators. Observe the real
    # owner and native checks without replacing a validator or fabricating epoch.
    from runtime.daemon import org_state as org_state_module
    with sequential_fixture() as (db, root, sql):
        reconcile_namespace(SimpleNamespace(root=root, db=db, slug='scratch'), Namespace((), ()), install=True)
        (root / 'org/teams.yaml').write_text('teams:\n  engineering:\n    manager: {kind: human, principal: founder}\n    workers: []\n')
        (root / 'org/config.yaml').write_text('reviewer_agents: []\n')
        from runtime.orchestrator.org_config import initialize_fresh_reviewer_agents
        initialize_fresh_reviewer_agents(OrgPaths(root), db)
        observed_loads = []
        @contextmanager
        def capture_load(database, **kwargs):
            with org_load_validation(database, **kwargs):
                owner = load_guard._current.get()
                statements = []
                with database.observe_trace(statements.append):
                    yield
                observed_loads.append((owner.full_runs, owner.hits, statements.count('PRAGMA foreign_key_check')))
                assert not any('integrity_check' in sql.lower() or 'quick_check' in sql.lower() for sql in statements), 'I2-load physical SQL inside load'
        with patch.dict(os.environ, {'HAPPYRANCH_DAEMON_HOME': str(root.parent.parent)}), patch.object(org_state_module, 'org_load_validation', capture_load):
            # Establish original settings/namespace once, then retain a genuine
            # prepared ordinary journal for the measured first/next pair.
            state = OrgState.load(slug='scratch', root=root, settings=Settings())
            state.close()
            control_sql = []
            @contextmanager
            def uncached_load(database, **kwargs):
                with database.observe_trace(control_sql.append):
                    yield
            with patch.object(org_state_module, 'org_load_validation', uncached_load):
                state = OrgState.load(slug='scratch', root=root, settings=Settings())
                assert state.naming_readiness == 'ready'
                state.close()
            assert not any('integrity_check' in sql.lower() or 'quick_check' in sql.lower() for sql in control_sql), 'I2-load uncached physical SQL'
            assert control_sql.count('PRAGMA foreign_key_check') == 10, 'uncached whole-load control omitted FK checks'
            print('PASS A0b actual sequential OrgState.load: uncached control ten whole-FK/domain reads')
            store = TaskPauseStore(db, 'scratch')
            fingerprint = dict(status='pending', block_kind=None, count=0,
                session_id=None, agent=None, fanout=None, jobs=None, parent=None, brief_digest='0' * 64)
            entry = dict(id='ordinary', org='scratch', root_task_id='TASK-seq-load',
                task_id='TASK-seq-load', agent='builder', session_id='synthetic-session',
                owner='ordinary', owner_identity={}, fingerprint=fingerprint,
                generation=0, phase='prepared', retry={}, context={}, started_at=None, captured_generation=None)
            with store.writer('TASK-seq-load'):
                row = store.ensure_uncommitted('TASK-seq-load')
                row['journal']['entries'] = [entry]
                store.save_journal_uncommitted(row)
            observed_loads.clear()
            with inline_sql_observation('I2-load') as whole:
                for phase in ('first', 'next'):
                    state = OrgState.load(slug='scratch', root=root, settings=Settings())
                    assert state.naming_readiness == 'ready'
                    state.close()
                    assert store.row_uncommitted('TASK-seq-load')['journal']['entries'] == [
                        dict(entry, phase='unknown', context={'execution_unknown': True})
                    ], phase + ' did not retain the actual Pause restoration'
                    full, hits, physical = observed_loads[-1]
                    assert full + hits == 10, phase + ' did not exercise all ten sequential discriminators: ' + str(observed_loads[-1])
                    assert full == (2 if phase == 'first' else 1), phase + ' lost write-sensitive whole-load validation count: ' + str(observed_loads[-1])
                    assert physical == full, phase + ' lost full FK checks on misses'
                    print('PASS A0b actual sequential OrgState.load:', phase, 'full', full, 'hits', hits)

                assert len(whole.reads) == 3, 'I2-load FIRST/NEXT complete candidate coverage'
                # A real nonempty roster exercises the current eligible-selector
                # loop and profile coordinator after OrgState returns. No runner,
                # listener, admission grant or selected workflow is launched.
                definition = AgentDef(name='manager', team='engineering', role='manager', executor='codex',
                    allow_rules=('happyranch',), repos={}, enrolled_by=None, enrolled_at_task=None,
                    enrolled_at=None, system_prompt='Synthetic private keeper manager.')
                (root / 'org/agents/manager.md').write_text(render_agent_text(definition))
                (root / 'org/teams.yaml').write_text('teams:\n  engineering:\n    manager: manager\n    workers: []\n')
                from runtime.workflows.profile_coordinator import ProfileCoordinator
                from runtime.orchestrator.active_authority_policy import is_eligible_policy_manager
                for phase in ('roster-first', 'roster-next'):
                    state = OrgState.load(slug='scratch', root=root, settings=Settings())
                    try:
                        assert state.naming_readiness == 'ready' and not state._startup_publication_held
                        assert state._startup_authority_error is None, 'I2-load real authority recovery did not finish'
                        coordinator = ProfileCoordinator(daemon_home=root.parent.parent, orgs={'scratch': state})
                        state._profile_coordinator = coordinator
                        state.workflow_authority._profile_coordinator = coordinator
                        coordinator.reconcile_startup()
                        selectors = []
                        for team in state.teams.teams():
                            manager = state.teams.executable_manager_for_team(team)
                            if manager is not None and is_eligible_policy_manager(root=state.root,
                                    agent_name=manager, team=team, teams=state.teams):
                                state.workflow_authority.ensure_authority_selector(team=team,
                                    publisher='daemon-state-load:selector-initialization')
                                selectors.append((team, manager))
                        coordinator.reconcile_startup()
                        assert selectors == [('engineering', 'manager')], 'I2-load missed current eligible selector'
                        assert not state.db._conn.in_transaction, 'I2-load profile/selector stole native transaction'
                        print('PASS I2-load', phase, 'real recovery/projection/authority/profile/all-current-selectors', selectors, flush=True)
                    finally:
                        state.close()

if sys.argv[1:] in (['--sequential-reuse'], ['--sequential-load'], ['--sequential-freshness']):
    raise SystemExit(0)

# A0 extends this venue's real-SQLite case tables at the shared acquisition seam.
from runtime.workflows.coding_authority_history import (
    CodingAuthorityHistoryError, _Budget, _bounded_rows,
)
from runtime.workflows.sequential_history import _ReadBudget, _sql_budget

class GateCursor(sqlite3.Cursor):
    def fetchone(self):
        row = super().fetchone()
        self.connection.fetched.append(row)
        return row

    def close(self):
        self.connection.closed_cursors += 1
        super().close()

class GateConnection(sqlite3.Connection):
    def cursor(self):
        return super().cursor(factory=GateCursor)

# No affinity: each literal retains its native SQLite storage class.
gate_columns = ('text_cell', 'blob_cell', 'integer_cell', 'real_cell', 'null_cell')
gate_types = ('text', 'blob', 'integer', 'real', 'null')
gate_allowed = tuple((kind,) for kind in gate_types)
gate_literals = ("'é'||char(0)||'尾'", "X'00ff'", '7', '1.25', 'NULL')
gate_values = ('é\x00尾', b'\x00\xff', 7, 1.25, None)
# label, substituted cell/expression, initial counters, local limit, value cap,
# refusal phase (or success), final consumed/rows/work deltas.
gate_cases = (
    ('native-classes-NUL-multibyte', None, {}, 8192, 8, None, (8, 1, 7)),
    ('nullable-NULL', None, {}, 8192, 8, None, (8, 1, 7)),
    ('nullable-text', (4, "'nullable'"), {}, 8192, 8, None, (16, 1, 7)),
    ('empty-text', (0, "''"), {}, 8192, 8, None, (2, 1, 7)),
    ('empty-blob', (1, "X''"), {}, 8192, 8, None, (6, 1, 7)),
    ('SQL-TRUE-integer', (2, 'TRUE'), {}, 8192, 8, None, (8, 1, 7)),
    ('SQL-FALSE-integer', (2, 'FALSE'), {}, 8192, 8, None, (8, 1, 7)),
    ('minimum-integer-cell', (2, '-9223372036854775808'), {}, 8192, 8, None, (8, 1, 7)),
    ('maximum-integer-cell', (2, '9223372036854775807'), {}, 8192, 8, None, (8, 1, 7)),
    ('exact-value-limit', (0, "'12345678'"), {}, 8192, 8, None, (10, 1, 7)),
    ('over-value-limit', (0, "'123456789'"), {}, 8192, 8, 'withheld', (0, 1, 1)),
    ('oversized-blob', (1, 'zeroblob(9)'), {}, 8192, 8, 'withheld', (0, 1, 1)),
    ('exact-default-value-limit', (1, 'zeroblob(1048576)'), {}, 8192, 1048576, None, (1048582, 1, 7)),
    ('over-default-value-limit', (1, 'zeroblob(1048577)'), {}, 8192, 1048576, 'withheld', (0, 1, 1)),
    ('NUL-suffix-byte-limit', None, {}, 8192, 5, 'withheld', (0, 1, 1)),
    ('text-is-integer', (0, '9'), {}, 8192, 8, 'withheld', (0, 1, 1)),
    ('text-is-blob', (0, "X'61'"), {}, 8192, 8, 'withheld', (0, 1, 1)),
    ('text-is-NULL', (0, 'NULL'), {}, 8192, 8, 'withheld', (0, 1, 1)),
    ('blob-is-text', (1, "'blob'"), {}, 8192, 8, 'withheld', (0, 1, 1)),
    ('integer-is-real', (2, '7.5'), {}, 8192, 8, 'withheld', (0, 1, 1)),
    ('real-is-integer', (3, '1'), {}, 8192, 8, 'withheld', (0, 1, 1)),
    ('NULL-is-text', (4, "'null'"), {}, 8192, 8, 'withheld', (0, 1, 1)),
    ('nonfinite-real', (3, '1e999'), {}, 8192, 8, 'decoded', (8, 1, 5)),
    ('invalid-UTF8', (0, "CAST(X'80' AS TEXT)"), {}, 8192, 8, 'fetch', (0, 0, 1)),
    ('exact-shared-bytes', None, {'consumed': 16777208}, 8192, 8, None, (8, 1, 7)),
    ('shared-byte-overflow', None, {'consumed': 16777209}, 8192, 8, 'withheld', (0, 1, 1)),
    ('shared-row-overflow', None, {'accepted_rows': 8192}, 8192, 8, 'withheld', (0, 1, 1)),
    ('shared-work-overflow', None, {'traversed': 16384}, 8192, 8, 'unfetched', (0, 0, 1)),
    ('local-row-overflow', None, {}, 0, 8, 'withheld', (0, 1, 1)),
)
for label, replacement, initial, maximum, cap, phase, deltas in gate_cases:
    with GateConnection(':memory:') as conn:
        conn.fetched, conn.closed_cursors = [], 0
        conn.execute('CREATE TABLE gate_cells (' + ','.join(gate_columns) + ')')
        literals = list(gate_literals)
        if replacement:
            literals[replacement[0]] = replacement[1]
        conn.execute('INSERT INTO gate_cells VALUES (' + ','.join(literals) + ')')
        expected_values = tuple(conn.execute('SELECT * FROM gate_cells').fetchone()) if phase != 'fetch' else None
        stored_types = tuple(conn.execute('SELECT ' + ','.join(
            'typeof(' + column + ')' for column in gate_columns) + ' FROM gate_cells').fetchone())
        allowed = gate_allowed[:4] + (('null', 'text'),) if label.startswith('nullable-') else gate_allowed
        expected_types = (*gate_types[:4], 'text') if label == 'nullable-text' else gate_types
        budget = _Budget(**initial)
        before = (budget.consumed, budget.accepted_rows, budget.traversed)
        try:
            rows = list(_bounded_rows(conn, 'gate_cells', gate_columns, allowed,
                                     budget, maximum=maximum, value_limit=cap))
        except CodingAuthorityHistoryError as exc:
            assert phase is not None and str(exc) == 'coding_authority_history_invalid', (label, exc)
        else:
            assert phase is None and rows == [(*expected_values, *expected_types)], (label, rows)
            assert tuple(type(value) for value in rows[0][:5]) == tuple(type(value) for value in expected_values), label
        assert tuple(value - old for value, old in zip(
            (budget.consumed, budget.accepted_rows, budget.traversed), before)) == deltas, label
        if conn.fetched:
            assert conn.fetched[0][7:12] == stored_types, label
        if phase == 'withheld':
            assert len(conn.fetched) == 1 and conn.fetched[0][1] == 0, label
            assert conn.fetched[0][2:7] == (None,) * 5, (label, conn.fetched)
        elif phase in ('unfetched', 'fetch'):
            assert conn.fetched == [], label
        else:
            assert conn.fetched[0][1] == 1 and conn.fetched[0][2:7] == expected_values, label
        assert conn.closed_cursors == 1, label
        print('PASS A0 bounded acquisition:', label, 'charges', deltas)
    conn.close()

for maximum, expected_ids, expected_charges in (
        (8192, (-9223372036854775808, -2, 9223372036854775807), (24, 3, 18)),
        (2, None, (16, 3, 13))):
    with GateConnection(':memory:') as conn:
        conn.fetched, conn.closed_cursors = [], 0
        conn.execute('CREATE TABLE gate_cells (' + ','.join(gate_columns) + ')')
        for identity in (-9223372036854775808, -2, 5, 9223372036854775807):
            values = (*gate_values[:2], 8 if identity == 5 else 7, *gate_values[3:])
            conn.execute('INSERT INTO gate_cells (rowid,' + ','.join(gate_columns) + ') VALUES (?,?,?,?,?,?)', (identity, *values))
        budget = _Budget()
        try:
            rows = list(_bounded_rows(conn, 'gate_cells', gate_columns, gate_allowed, budget,
                                     where='integer_cell=?', args=(7,), maximum=maximum, with_rowid=True))
        except CodingAuthorityHistoryError as exc:
            assert expected_ids is None and str(exc) == 'coding_authority_history_invalid'
            assert conn.fetched[-1][1:7] == (0, None, None, None, None, None)
        else:
            assert tuple(row[0] for row in rows) == expected_ids
            assert all(row[1:] == (*gate_values, *gate_types) for row in rows)
        assert (budget.consumed, budget.accepted_rows, budget.traversed) == expected_charges
        assert conn.closed_cursors == 1
        print('PASS A0 rowid extremes/gaps/WHERE/overflow:', maximum, expected_charges)
    conn.close()

# Same finite SQL allowance and original retained logical counters at each width.
# The wide valid fixture must complete, rather than silently validating a subset.
for width in (1, 4, 11, 32):
    with sqlite3.connect(':memory:') as conn:
        columns = tuple('cell_' + str(index) for index in range(width))
        conn.execute('CREATE TABLE gate_width (' + ','.join(columns) + ')')
        conn.executemany('INSERT INTO gate_width VALUES (' + ','.join('?' for _ in columns) + ')',
                         [('x',) * width] * 400)
        logical = _Budget()
        sql = _ReadBudget()  # Original 2M SQL / 5s; no raised or reset allowance.
        try:
            with _sql_budget(conn, sql):
                rows = list(_bounded_rows(conn, 'gate_width', columns, (('text',),) * width, logical))
        except ValueError as exc:
            raise AssertionError(f'A0 width={width} complete acquisition refused; SQL={sql.sql_steps}; cause={exc.__cause__}') from exc
        assert rows == [('x',) * width + ('text',) * width] * 400, width
        assert (logical.consumed, logical.accepted_rows, logical.traversed) == (400 * width, 400, 400 * (width + 1) + 1), width
        print('PASS A0 width:', width, 'rows=400 SQL=', sql.sql_steps,
              'logical=', logical.consumed, logical.accepted_rows, logical.traversed)
    conn.close()

# A0b's companion guard uses the same native-class/byte/decoding table style.
# These are guard inputs, not a replacement complete schema or history reader.
# No affinity allows malformed classes to reach the production prerequisite.
from runtime.infrastructure.workflow_sequential_ownership import validate_sequential_ownership_guards

ownership_tables = (
    ('tasks', ('id', 'parent_task_id', 'assigned_agent', 'current_session_id'),
     ("'task'", "'parent'", "'builder'", "'sid'"), (False, True, True, True)),
    ('workflow_task_ownership', ('task_id', 'org_slug', 'version', 'kind', 'owner_id'),
     ("'task'", "'scratch'", '1', "'legacy'", 'NULL'), (False, False, False, False, True)),
)
for table, columns, literals, nullable in ownership_tables:
    for index, column in enumerate(columns):
        integer = column == 'version'
        variants = (
            ('integer', '2', integer),
            ('real', '2.5', False),
            ('blob', "X'61'", False),
            ('NULL', 'NULL', nullable[index]),
            ('empty-text', "''", not integer),
            ('ASCII', "'plain'", not integer),
            ('multibyte', "'é尾'", not integer),
            ('NUL-suffix', "'a'||char(0)||'尾'", not integer),
            ('exact-byte-cap', "printf('%.*c',1048576,'x')", not integer),
            ('over-byte-cap', "printf('%.*c',1048577,'x')", False),
            ('NUL-over-byte-cap', "'a'||char(0)||printf('%.*c',1048575,'x')", False),
            ('invalid-UTF8', "CAST(X'80' AS TEXT)", False),
        )
        for label, literal, valid in variants:
            with GateConnection(':memory:') as conn:
                conn.fetched, conn.closed_cursors = [], 0
                for fixture_table, fixture_columns, fixture_literals, _ in ownership_tables:
                    conn.execute('CREATE TABLE ' + fixture_table + ' (' + ','.join(fixture_columns) + ')')
                    cells = list(fixture_literals)
                    if fixture_table == table:
                        cells[index] = literal
                    conn.execute('INSERT INTO ' + fixture_table + ' VALUES (' + ','.join(cells) + ')')
                conn.execute('CREATE VIEW workflow_task_owner_bindings AS '
                             'SELECT task_id,kind AS relation,owner_id,task_id AS instance_id '
                             'FROM workflow_task_ownership WHERE 0')
                budget = _Budget()
                try:
                    validate_sequential_ownership_guards(conn, budget=budget)
                except CodingAuthorityHistoryError as exc:
                    assert not valid and str(exc) == 'coding_authority_history_invalid', (table, column, label, exc)
                else:
                    assert valid, (table, column, label, 'guard accepted invalid cell')
                if valid and label in ('multibyte', 'NUL-suffix'):
                    stored = tuple(conn.execute('SELECT * FROM ' + table).fetchone())
                    expected = (sum(len(value.encode('utf8')) for value in stored if type(value) is str),
                                1, 8 + len(columns) + 1)
                    assert (budget.consumed, budget.accepted_rows, budget.traversed) == expected, (table, column, label)
                    assert len([row for row in conn.fetched if row is not None]) == 1
                elif valid:
                    # Two binding probes; each table adds gate, exceptional
                    # phase and terminal keyset traversal: 2 + 3 + 3 = 8.
                    assert (budget.consumed, budget.accepted_rows, budget.traversed) == (0, 0, 8), (table, column, label)
                else:
                    # Whole-table type/size refusal must precede payload fetch.
                    # Invalid UTF8 reaches the actual decoder only for text slots.
                    decoded = label == 'invalid-UTF8' and not integer
                    expected_work = (3 if table == 'tasks' else 6) + (2 if decoded else 0)
                    assert (budget.consumed, budget.accepted_rows, budget.traversed) == (0, 0, expected_work), (table, column, label)
                    assert all(row is None for row in conn.fetched), (table, column, label, 'payload acquired before type proof')
                print('PASS A0b ownership guard:', table, column, label,
                      'charges', budget.consumed, budget.accepted_rows, budget.traversed)
            conn.close()

# A malformed class anywhere in a table prevents the exceptional-text scan of
# that table, even when another row contains malformed UTF8. Check both orders.
for table, columns, literals, _ in ownership_tables:
    for bad_first in (False, True):
        with GateConnection(':memory:') as conn:
            conn.fetched, conn.closed_cursors = [], 0
            for fixture_table, fixture_columns, _, _ in ownership_tables:
                conn.execute('CREATE TABLE ' + fixture_table + ' (' + ','.join(fixture_columns) + ')')
            rows = [list(literals), list(literals)]
            rows[0][0] = '7'
            rows[1][0] = "CAST(X'80' AS TEXT)"
            for cells in rows if bad_first else reversed(rows):
                conn.execute('INSERT INTO ' + table + ' VALUES (' + ','.join(cells) + ')')
            conn.execute('CREATE VIEW workflow_task_owner_bindings AS '
                         'SELECT task_id,kind AS relation,owner_id,task_id AS instance_id '
                         'FROM workflow_task_ownership WHERE 0')
            budget = _Budget()
            try:
                validate_sequential_ownership_guards(conn, budget=budget)
            except CodingAuthorityHistoryError:
                pass
            else:
                raise AssertionError((table, bad_first, 'mixed corruption accepted'))
            assert all(row is None for row in conn.fetched), (table, bad_first, 'type prerequisite lost')
            assert (budget.consumed, budget.accepted_rows, budget.traversed) == (0, 0, 3 if table == 'tasks' else 6)
            print('PASS A0b whole-table prerequisite:', table, 'bad-first', bad_first)
        conn.close()

if sys.argv[1:] == ['--bounded-acquisition']:
    raise SystemExit(0)

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
    establish_reviewer_agents_policy, write_org_setting_to_db, OrgConfig, load_org_config,
)
from runtime.workflows.authority import WorkflowAuthorityError
from runtime.infrastructure.db.knowledge import ReviewerPolicyError

# B2 parser/dataclass defaults are independent of the legacy startup pin.
startup_expect(OrgConfig().reviewer_agents == (), 'B2-dataclass-default-none')
with tempfile.TemporaryDirectory(prefix='reviewer-default-') as scratch:
    paths = OrgPaths(Path(scratch))
    paths.org_config_path.parent.mkdir(parents=True)
    for label, config, expected in (
            ('missing-file', None, ()), ('empty-yaml', '', ()),
            ('omitted-yaml', 'threads: {enabled: true}\n', ()),
            ('explicit-none', 'reviewer_agents: []\n', ()),
            ('explicit-other', 'reviewer_agents: [other]\n', ('other',))):
        if config is not None:
            paths.org_config_path.write_text(config)
        startup_expect(load_org_config(paths).reviewer_agents == expected, 'B2-parser-'+label)

@contextmanager
def literal_reviewer_fixture(*, raw='[]', marker=None, sentinel=False, config='', saved=None,
                             verdict=None, expectation=None, reviewer='code_reviewer',
                             metadata=('2001-01-01T00:00:00+00:00', 'founder')):
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
        if config is not None:
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
                       ('TASK-102', 'completed', reviewer, 'subtask', None, None, 'TASK-101', 'synthetic-child', None)]
            if saved == 'carrier':
                records += [('TASK-100', 'in_progress', 'owner', 'task', 'delegated', None, None, None, fanout),
                            ('TASK-103', 'completed', 'worker', 'subtask', None, None, 'TASK-100', None, None)]
            for task_id, status, agent, kind, blocked, active, parent, session, fan in records:
                db._conn.execute('INSERT INTO tasks (id,status,assigned_agent,team,brief,task_type,block_kind,active_chain,parent_task_id,current_session_id,active_fanout,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (task_id,status,agent,'engineering','Synthetic legacy task',kind,blocked,active,parent,session,fan,*[metadata[0]]*2))
            db._conn.commit()
            db.insert_task_result('TASK-102', reviewer, 'synthetic-child',
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


def submit_reviewer_decision(state, action, *, downstream=True, reviewer='code_reviewer', reviewer_in_then=False):
    db, orch = state.db, state.orchestrator
    parent_id = db.next_task_id()
    db.insert_task(TaskRecord(id=parent_id, assigned_agent='owner', brief='Synthetic submission'))
    assert db.try_claim_for_step(parent_id, TaskStatus.PENDING, None, 1)
    db.update_task(parent_id, current_session_id='synthetic-owner')
    child = {'agent': reviewer, 'prompt': 'Synthetic review',
             'then': [{'agent': 'worker', 'prompt': 'Synthetic downstream'}]}
    if reviewer_in_then:
        child = {'agent': 'worker', 'prompt': 'Synthetic author',
                 'then': [{'agent': reviewer, 'prompt': 'Synthetic review'},
                          {'agent': 'other', 'prompt': 'Synthetic downstream'}]}
    if not downstream:
        child.pop('then')
    decision = ({'action': 'delegate', **child} if action == 'delegate' else
                {'action': 'fanout', 'width_cap_ack': 2,
                 'children': [child, {'agent': 'worker', 'prompt': 'Synthetic sibling'}]})
    # The persisted-report reader retains the legacy JSON summary transport;
    # decision_json alone is historical evidence, not report.decision.
    db.insert_task_result(parent_id, 'owner', 'synthetic-owner', json.dumps(decision), 100,
                          decision_json=json.dumps(decision))
    report = db.get_latest_completion_report(parent_id)
    assert orch._parse_next_step(report).action == action, 'submission did not reach its decision'
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
            for in_then in (False, True):
                parent, before = submit_reviewer_decision(state, action, reviewer_in_then=in_then)
                rows = db.get_task_results(parent)
                case = tag+'-'+action+('-then' if in_then else '-first')
                startup_expect(not db.get_children(parent) and db.next_task_id() == before
                    and db.get_task(parent).status == TaskStatus.PENDING
                    and any('HARD REJECT' in row['output_summary'] for row in rows), case+'-omission-zero-children')
                queued = list(orch._queue._queue._queue)
                startup_expect(all(db.get_task(item[1]).parent_task_id is None for item in queued), case+'-no-child-queue')

for raw, saved, verdict in product(
        ('[]', '["code_reviewer"]', '["senior_dev"]', '["family_manager"]'),
        ('ordinary', 'carrier'), (None, 'REQUEST_CHANGES', 'REVISE', 'BLOCK', 'PASS', 'APPROVE')):
    reviewer = (json.loads(raw) or ['code_reviewer'])[0]
    with literal_reviewer_fixture(raw=raw, saved=saved, verdict=verdict, reviewer=reviewer) as (state, original):
        db, orch = state.db, state.orchestrator
        before = db.next_task_id()
        before_links = db._conn.execute('SELECT count(*) FROM task_attachments').fetchone()[0]
        reviewer_steps._enqueue_parent_if_waiting(orch, 'TASK-102')
        parent = db.get_task('TASK-101')
        tag = (('L2a' if saved == 'ordinary' else 'L2b') if raw == '[]' else 'L4-'+reviewer+'-'+saved)+'-'+str(verdict)
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
with literal_reviewer_fixture(raw='[]', marker='{"version":2}') as (state, original):
    for action in ('delegate', 'fanout'):
        parent, before = submit_reviewer_decision(state, action)
        startup_expect(bool(state.db.get_children(parent)) and state.db.next_task_id() != before,
                       'L3-none-'+action+'-omitted-expectation-admitted')

for slug, raw, timestamp in (( 'happyranch', '["code_reviewer"]', '2026-08-24T08:41:47.100629+00:00'),
                        ( 'danze', '["senior_dev"]', '2026-11-05T11:14:11.056269+00:00'),
                        ( 'family', '["family_manager"]', '2026-11-05T11:14:16.517155+00:00')):
    for sentinel in (False, True):
        with literal_reviewer_fixture(raw=raw, sentinel=sentinel, metadata=(timestamp,'founder')) as (state, original):
            db = state.db
            startup_expect(tuple(db._conn.execute("SELECT * FROM org_settings WHERE section='reviewer_agents'").fetchone()) == original,
                           'L4-'+slug+'-'+raw+'-'+str(sentinel)+'-all-columns-unchanged')
            startup_expect(reviewer_audits(db) == [
                ('config:reviewer_agents_policy', 'orchestrator', {
                    'section': 'reviewer_agents_policy', 'tiers': ['version'],
                    'before': {}, 'after': {'version': 2}})],
                           'L4-'+slug+'-'+raw+'-'+str(sentinel)+'-marker-audit-only')
            for action in ('delegate', 'fanout'):
                parent, before = submit_reviewer_decision(state, action, reviewer=json.loads(raw)[0])
                startup_expect(not db.get_children(parent) and db.next_task_id() == before
                    and any('HARD REJECT' in row['output_summary'] for row in db.get_task_results(parent)),
                    'L4-'+slug+'-'+raw+'-'+str(sentinel)+'-'+action+'-omission-preserved')

for sentinel in (False, True):
    for config, expected in ((None, ['code_reviewer']), ('', ['code_reviewer']), ('reviewer_agents: [other]\n', ['other']),
                             ('reviewer_agents: [ghost]\n', ['code_reviewer']),
                             ('reviewer_agents: [other, ghost]\n', ['code_reviewer']),
                             ('reviewer_agents: []\n', ['code_reviewer'])):
        with literal_reviewer_fixture(raw=None, sentinel=sentinel, config=config) as (state, original):
            db = state.db
            tag = 'L6-missing-'+str(sentinel)+'-'+repr(config)
            startup_expect(original is None and json.loads(db.get_org_setting('reviewer_agents')) == expected,
                           tag)
            startup_expect(reviewer_audits(db) == [
                ('config:reviewer_agents', 'orchestrator', {'section': 'reviewer_agents',
                    'tiers': ['value'], 'before': {}, 'after': {'value': expected}}),
                ('config:reviewer_agents_policy', 'orchestrator', {'section': 'reviewer_agents_policy',
                    'tiers': ['version'], 'before': {}, 'after': {'version': 2}})],
                tag+'-exact-once-audits-reopen')
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
        for action in ('delegate','fanout'):
            parent,before = submit_reviewer_decision(state,action)
            startup_expect(not state.db.get_children(parent) and state.db.next_task_id() == before
                and 'reviewer configuration unavailable' in (state.db.get_task(parent).note or ''),
                'L6-stale-'+action+'-zero-child-allocation-'+raw)
for saved in ('ordinary', 'carrier'):
    for raw, marker in (('["ghost"]', '{"version":2}'), ('["code_reviewer","ghost"]', '{"version":2}'),
                        ('broken', '{"version":2}'), ('[]', None), ('[]', '{"version":true}')):
        with literal_reviewer_fixture(saved=saved, verdict='APPROVE') as (state, original):
            db, orch = state.db, state.orchestrator
            # Corruption/staleness after load must also refuse saved advancement.
            db._conn.execute("UPDATE org_settings SET value_json=? WHERE section='reviewer_agents'", (raw,))
            if marker is None:
                db._conn.execute("DELETE FROM org_settings WHERE section='reviewer_agents_policy'")
            else:
                db._conn.execute("UPDATE org_settings SET value_json=? WHERE section='reviewer_agents_policy'", (marker,))
            db._conn.commit()
            before = db.next_task_id()
            links = db._conn.execute('SELECT count(*) FROM task_attachments').fetchone()[0]
            reviewer_steps._enqueue_parent_if_waiting(orch, 'TASK-102')
            parent = db.get_task('TASK-101')
            startup_expect(db.get_children(parent.id) == ['TASK-102'] and db.next_task_id() == before
                and parent.active_chain is None
                and db._conn.execute('SELECT count(*) FROM task_attachments').fetchone()[0] == links,
                'L6-'+saved+'-unavailable-zero-downstream-'+str((raw,marker)))
            target = 'TASK-101' if saved == 'ordinary' else 'TASK-100'
            startup_expect([item[1] for item in list(orch._queue._queue._queue)] == [target]
                and (saved != 'carrier' or parent.status == TaskStatus.FAILED),
                'L6-'+saved+'-unavailable-wake-or-fanin-'+str((raw,marker)))

# L5: two real connections with SQL trace/event ordering, no timing sleeps or
# production failpoint. A third owner is never inferred from a fixture flag.
import threading
for first, second in (('convert','convert'), ('convert','clear'), ('clear','convert'),
                      ('convert','configure'), ('configure','convert'), ('legacy-configure','convert')):
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
        trace_observers = ExitStack()
        trace_observers.enter_context(dbs[0].observe_trace(trace_first))
        trace_observers.enter_context(dbs[1].observe_trace(trace_second))
        def act(db, action):
            try:
                if action == 'convert':
                    db.establish_reviewer_agents_policy(['code_reviewer'])
                elif action == 'legacy-configure':
                    db._conn.execute('BEGIN IMMEDIATE')
                    db._conn.execute("UPDATE org_settings SET value_json='[\"other\"]' WHERE section='reviewer_agents'")
                    db.get_reviewer_agents_pair()  # Hold the real SQLite owner until the contender enters.
                    db._conn.commit()
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
            trace_observers.close()
        expected = [] if 'clear' in (first,second) else ['other'] if any('configure' in action for action in (first,second)) else ['code_reviewer']
        startup_expect(not errors and not any(t.is_alive() for t in threads)
                       and json.loads(dbs[0].get_org_setting('reviewer_agents')) == expected
                       and sum(a[0]=='config:reviewer_agents_policy' for a in reviewer_audits(dbs[0])) == 1,
                       'L5-serialized-'+first+'-'+second)
        for db in dbs:
            db.close()
with tempfile.TemporaryDirectory(prefix='reviewer-lock-refusal-') as scratch:
    path = Path(scratch)/'org.db'
    owner, contender = Database(path), Database(path)
    owner._conn.execute("INSERT INTO org_settings VALUES ('reviewer_agents','[]','old','founder')")
    owner._conn.commit()
    contender._conn.execute('PRAGMA busy_timeout=10')  # Bound this synthetic lock-exhaustion case only.
    owner._conn.execute('BEGIN IMMEDIATE')
    try:
        contender.establish_reviewer_agents_policy(['code_reviewer'])
    except sqlite3.OperationalError as exc:
        startup_expect('locked' in str(exc) and contender.get_reviewer_agents_pair() == {'reviewer_agents':'[]'}
            and not reviewer_audits(contender), 'L5-lock-exhaustion-keeps-unclassified-pair')
    else:
        startup_expect(False, 'L5-lock-exhaustion-keeps-unclassified-pair')
    finally:
        owner._conn.rollback()
        owner.close()
        contender.close()
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
            trace_observers = ExitStack()
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
                trace_observers.enter_context(db.observe_trace(statements.append))
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
                    before = sum(sql == 'PRAGMA foreign_key_check' for sql in statements)
                    original_advance(conn, marker, events, **kwargs)
                    assert sum(sql == 'PRAGMA foreign_key_check' for sql in statements) == before + 1, 'recovery mutation lost its full target check before COMMIT'
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
                    before_checks = sum(sql == 'PRAGMA foreign_key_check' for sql in statements)
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
                                and sum(sql == 'PRAGMA foreign_key_check' for sql in statements) == before_checks)
                        # Reuse returns the exact complete tuple, without reconstruction.
                        if target_results:
                            assert len(target_results) == 1 and target_results[0] is cached
                        assert owner.saved[1] is cached
                        assert 'PRAGMA foreign_key_check' in statements
                    else:
                        good = sum(sql == 'PRAGMA foreign_key_check' for sql in statements) > before_checks
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
                        before = sum(sql == 'PRAGMA foreign_key_check' for sql in statements)
                        cutover.get()
                        assert sum(sql == 'PRAGMA foreign_key_check' for sql in statements) == before + 1, 'public cutover get reused within load scope'
            finally:
                db._conn.set_authorizer(None)
                trace_observers.close()
                db.close()
assert not target_failures, target_failures

returned_tail_cases(None)

with tempfile.TemporaryDirectory(prefix='workflow-load-') as scratch:
    paths = OrgPaths(root=Path(scratch))
    paths.org_dir.mkdir()
    paths.agents_dir.mkdir()
    db = Database(paths.db_path)
    trace_observers = ExitStack()
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
        trace_observers.enter_context(db.observe_trace(statements.append))

        def checks():
            return sum(s == 'PRAGMA foreign_key_check' for s in statements)

        def read(**kwargs):
            return read_workflow_schema_for_org_load(db._conn, expected_org_slug='scratch', **kwargs)

        with org_load_validation(db, org_slug='scratch'):
            first = read()
            assert checks() == 1, 'first validation lost foreign_key_check'
            assert 'PRAGMA foreign_key_check' in statements, 'first full validation lost FK check'
            assert read() == first
            assert checks() == 1, 'unchanged load repeated the full traversal'
        print('PASS unchanged reuse and first-run whole-FK/domain')

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
                        if sql == boundary and not moved:
                            moved = True
                            other.execute("UPDATE org_settings SET value_json=? WHERE section='probe'", ('"' + boundary + '"',))
                            other.commit()
                    with db.observe_trace(move_at_boundary):
                        read()
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
        trace_observers.close()
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
        trace_observers.close()
        db.close()
assert not startup_failures, startup_failures
PY
