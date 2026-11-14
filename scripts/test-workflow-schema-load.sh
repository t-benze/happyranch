#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
uv run --frozen --python "${WORKFLOW_SCHEMA_PYTHON:-3.14}" python - "$@" <<'PY'
from pathlib import Path
from contextlib import ExitStack, contextmanager
from contextvars import copy_context
from itertools import product
import json
import sqlite3
import sys
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

assert sys.argv[1:] in ([], ['--bounded-acquisition'], ['--trace-custody'], ['--sequential-reuse'], ['--sequential-load'], ['--sequential-faults'], ['--sequential-sql-exhaustion'], ['--sequential-corruption'], ['--retained-corruption'], ['--document-coding-corruption'], ['--coding-authority-corruption'], ['--document-native-corruption'], ['--physical-corruption'], ['--sequential-uncommitted']), 'unknown schema-load selection'

# A0b-2 custody extends this existing real-SQLite venue. No native connection
# subclass, fake epoch, successful validator stub or production test seam.
if sys.argv[1:] not in (['--bounded-acquisition'], ['--sequential-reuse'], ['--sequential-load'], ['--sequential-faults'], ['--sequential-sql-exhaustion'], ['--sequential-corruption'], ['--retained-corruption'], ['--document-coding-corruption'], ['--coding-authority-corruption'], ['--document-native-corruption'], ['--physical-corruption'], ['--sequential-uncommitted']):
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
if sys.argv[1:] in ([], ['--sequential-reuse'], ['--sequential-load'], ['--sequential-faults'], ['--sequential-sql-exhaustion'], ['--sequential-corruption'], ['--retained-corruption'], ['--document-coding-corruption'], ['--coding-authority-corruption'], ['--document-native-corruption'], ['--physical-corruption'], ['--sequential-uncommitted']):
    from runtime.infrastructure.workflow_schema import (
        initialize_complete_org_schema, install_sequential_schema,
        sequential_storage_inventory, _coding_org_identity, _schema_snapshot,
    )
    from runtime.workflows.coding_authority_history import _Budget
    from runtime.workflows.sequential_history import read_sequential_history
    from runtime.workflows import sequential_history as seq_history
    from runtime.workflows._read_exhaustion import ReadExhaustion
    from runtime.infrastructure.task_pause_controls import PauseControlError

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

    # Synthetic storage only: compiler/parser and the unmodified public reader
    # validate the nonempty graph. These rows confer no execution authority.
    def populate_sequential(db):
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
            outcome='produced', outputs=[output], findings=[], capability_receipts=[]))
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
             visit_result, envelope, digest(envelope), encode(dict(checks=['immutable-document@1'])),
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

    def populate_document_coding(db):
        import hashlib
        from runtime.workflows.templates import WorkflowTemplateStore, WorkflowTemplatePrincipal
        from runtime.workflows._coding_registered_release import registration_rows
        from runtime.workflows.release_templates import read_coding_release

        # Inert template publication and exact saved built-in rows, as in the
        # retained coding-audit keeper. No current setup, Allow or run exists.
        template = WorkflowTemplateStore(db).publish_version(org_slug='scratch',
            principal=WorkflowTemplatePrincipal.agent(org_slug='scratch',
                agent_name='synthetic-manager', team_slug='engineering',
                task_id='source', session_id='source-session', revalidate=lambda: None),
            operation_key='synthetic-document-template', namespace='org/scratch/team/engineering',
            template_name='synthetic-document', expected_current_version=0,
            definition=dict(kind='document-review', schema_version=2,
                description='Synthetic retained document', author=dict(role='author', kind='agent'),
                output=dict(primitive='immutable-document-revision', description='Synthetic output'),
                reviewers=[dict(role='reviewer', kind='agent')], outcomes=['approved'],
                approval=dict(mode='all', revision='current', required_roles=['reviewer']),
                request_changes=None, submission=dict(timing='on-completion')))
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
            admission_principal='synthetic-manager', operation_key='synthetic-draft-history',
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

    def populate_document_native(db):
        import hashlib
        from runtime.infrastructure.workflow_schema import _DRAFT_MUTABLE, _DRAFT_PROJECTION

        populate_document_coding(db)
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

    if sys.argv[1:] in ([], ['--sequential-reuse'], ['--sequential-corruption'], ['--retained-corruption'], ['--document-coding-corruption'], ['--coding-authority-corruption'], ['--document-native-corruption'], ['--physical-corruption'], ['--sequential-uncommitted']):
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
        document_native_names = {name for name, _ in document_native_corruptions}
        refusal_codes.update({name: ('workflow_submission_data_corrupt' if name.startswith(('document operation ', 'document link '))
                                    else 'workflow_draft_data_corrupt') for name in document_native_names})
        coding_import_names = {name for name, _ in coding_import_corruptions}
        coding_setup_names = {name for name, _ in coding_setup_corruptions}
        refusal_codes.update({name: 'coding_registered_release_history_invalid' for name in coding_import_names})
        refusal_codes.update({name: 'coding_authority_history_invalid' for name in coding_setup_names})
        refusal_codes.update({name: code for name, _, code in document_coding_corruptions})
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
        refusal_codes.update({name: 'workflow_source_data_corrupt' for name in physical_names})
        if sys.argv[1:] == ['--physical-corruption']:
            corruptions = physical_corruptions
        elif sys.argv[1:] in ([], ['--sequential-reuse'], ['--sequential-corruption']):
            corruptions += physical_corruptions
        corruption_failures = []
        corruption_cases = [(row, phase) for row in corruptions
            for phase in ('first', 'between local', 'between external', 'between uncommitted')
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
                        assert expected[0] == 'refused', name + ' fixture was accepted by complete reader'
                        assert all(category[0] is not ReadExhaustion for category in expected[1]), name + ' hidden by exhaustion'
                        if name in retained_names:
                            from runtime.infrastructure.team_move_store import TeamMoveError
                            error_type = (PauseControlError if name.startswith('Pause ') else
                                          names.NamingError if name.startswith('name ') else TeamMoveError)
                        elif name == 'document compiler pin':
                            from runtime.workflows.templates import WorkflowTemplateError
                            error_type = WorkflowTemplateError
                        elif name in coding_setup_names:
                            from runtime.workflows.coding_authority_history import CodingAuthorityHistoryError
                            error_type = CodingAuthorityHistoryError
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
        if sys.argv[1:] in (['--sequential-corruption'], ['--retained-corruption'], ['--document-coding-corruption'], ['--coding-authority-corruption'], ['--document-native-corruption'], ['--physical-corruption'], ['--sequential-uncommitted']):
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
                    full = sql.count('PRAGMA integrity_check')
                    seq_read(db)
                    assert sql.count('PRAGMA integrity_check') == full + 1 and owner.hits == hits, name + ' restoration resurrected a fact'
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
                        full = sql.count('PRAGMA integrity_check')
                        seq_read(db)
                        assert sql.count('PRAGMA integrity_check') == full + 1 and owner.hits == 0, 'initial native limit restoration resurrected a fact'
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
                    full = sql.count('PRAGMA integrity_check')
                    actual = sequential_outcome(lambda: seq_read(db))
                    assert owner.hits == hits and sql.count('PRAGMA integrity_check') == full + 1, pragma + ' restored configuration returned a hit'
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
                    before = sql.count('PRAGMA integrity_check')
                    seq_read(db)
                    assert sql.count('PRAGMA integrity_check') == before + 1, config + ' restoration resurrected a sequential fact'
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
                    before = sql.count('PRAGMA integrity_check')
                    seq_read(db)
                    assert sql.count('PRAGMA integrity_check') == before + 1, 'autocommit restoration resurrected a fact'
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
                            full = sql.count('PRAGMA integrity_check')
                            actual = sequential_outcome(lambda: seq_read(db))
                            assert owner.hits == hits, attachment + ' returned a sequential hit'
                            if attachment == 'additional database':
                                expected = sequential_outcome(lambda: read_sequential_history(
                                    conn, expected_org_slug='scratch').retained)
                                assert actual == expected and owner.sequential_saved is None, 'attached database changed complete outcome or certified'
                            else:
                                assert actual[0] == 'returned' and sql.count('PRAGMA integrity_check') == full + 1, 'restored ATTACH/DETACH skipped complete validation'
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
                    full = sql.count('PRAGMA integrity_check')
                    seq_read(db)
                    if attachment != 'restored ATTACH/DETACH':
                        assert sql.count('PRAGMA integrity_check') == full + 1, attachment + ' restoration resurrected a fact'
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
                    before = other_sql.count('PRAGMA integrity_check')
                    actual = seq_read(other)
                    assert actual == read_sequential_history(other._conn, expected_org_slug='scratch').retained
                    assert owner.hits == hits and owner.sequential_saved is None, 'wrong connection borrowed a sequential certificate'
                    assert other_sql.count('PRAGMA integrity_check') == before + 2
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
                            full = sql.count('PRAGMA integrity_check')
                            operation = (lambda: load_guard.read_workflow_schema_target_for_org_load(
                                conn, expected_org_slug='scratch', legacy=lambda: validate_workflow_schema(
                                    conn, expected_org_slug='scratch'))) if target else (
                                lambda: seq_read(db, writer_source=True))
                            actual = sequential_outcome(operation)
                            assert sql.count('PRAGMA integrity_check') == full + 1, name + ' bypassed complete fallback'
                            expected = sequential_outcome(lambda: read_sequential_history(
                                conn, expected_org_slug='scratch').retained)
                            assert actual == expected and actual[0] == 'returned', name + ' changed complete outcome'
                            assert conn.in_transaction and owner.hits == 0 and owner.sequential_saved is None, name + ' stole ownership or certified unknown interval'
                        conn.rollback()  # Only the original caller releases it.
                        full = sql.count('PRAGMA integrity_check')
                        seq_read(db)
                        assert sql.count('PRAGMA integrity_check') == full + 1 and owner.sequential_saved is not None, name + ' exit did not start a fresh complete fact'
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
                        full = sql.count('PRAGMA integrity_check')
                        actual = sequential_outcome(lambda: seq_read(db))
                        assert sql.count('PRAGMA integrity_check') == full + 1, 'unknown initial attachment skipped complete read'
                        expected = sequential_outcome(lambda: read_sequential_history(
                            db._conn, expected_org_slug='scratch').retained)
                        assert actual == expected and owner.hits == 0 and owner.sequential_saved is None, 'unknown initial attachment certified reuse'
                        assert not db._conn.in_transaction, 'unknown initial attachment leaked snapshot'
                    held.rename(original)
                    full = sql.count('PRAGMA integrity_check')
                    seq_read(db)
                    assert sql.count('PRAGMA integrity_check') == full + 1, 'initial attachment restoration skipped complete read'
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
                                    (boundary == 'integrity' and statement == 'PRAGMA integrity_check') or
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
                assert sql.count('PRAGMA integrity_check') == 1 and 'PRAGMA foreign_key_check' in sql
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
                    before = sql.count('PRAGMA integrity_check')
                    if site == 'cutover':
                        WorkflowCutoverStore(db, org_slug='scratch').recover_authorized()
                    elif site == 'ownership':
                        install_task_ownership(db, org_slug='scratch')
                    else:
                        reconcile_namespace(org, Namespace((), ()), install=True, compare_only=True)
                    assert sql.count('PRAGMA integrity_check') == before, site + ' sequential unchanged target read fresh'
                    if site == 'cutover':
                        begin = max(i for i, statement in enumerate(sql) if statement == 'BEGIN')
                        assert sql[begin + 1] == 'SELECT rootpage FROM main.sqlite_schema LIMIT 1', 'cutover target lost fresh explicit main snapshot pin'
                print('PASS A0b sequential targets:', site, 'original transaction and complete source')

            for boundary in ('BEGIN', 'ROLLBACK'):
                other = sqlite3.connect(db.db_path)
                try:
                    with org_load_validation(db, org_slug='scratch'):
                        seq_read(db)
                        before = sql.count('PRAGMA integrity_check')
                        moved = []
                        def move(statement):
                            if statement == boundary and not moved:
                                moved.append(True)
                                other.execute("UPDATE tasks SET brief=brief||'r' WHERE id='TASK-seq-load'")
                                other.commit()
                        with db.observe_trace(move):
                            seq_read(db)
                        assert moved and sql.count('PRAGMA integrity_check') == before + 1, boundary + ' race returned speculative reuse'
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

            for exclusion in ('arbitrary snapshot', 'public', 'nondefault budget', 'unaccounted budget', 'validator policy'):
                with org_load_validation(db, org_slug='scratch'):
                    seq_read(db)
                    before = sql.count('PRAGMA integrity_check')
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
                        assert sql.count('PRAGMA integrity_check') == before + 1, exclusion + ' escaped reuse policy'
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
                    count = sql.count('PRAGMA integrity_check')
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
                    assert not errors and sql.count('PRAGMA integrity_check') == count + 1, 'sequential certificate crossed ' + execution
                print('PASS A0b sequential exclusion: foreign', execution)

            with org_load_validation(db, org_slug='scratch'):
                seq_read(db)
                inherited = copy_context()
            before = sql.count('PRAGMA integrity_check')
            inherited.run(seq_read, db)
            with org_load_validation(db, org_slug='scratch'):
                seq_read(db)
            seq_read(db)
            assert sql.count('PRAGMA integrity_check') == before + 3, 'sequential certificate escaped load lifetime'
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
        (root / 'org/teams.yaml').write_text('teams: {}\n')
        observed_loads = []
        @contextmanager
        def capture_load(database, **kwargs):
            with org_load_validation(database, **kwargs):
                owner = load_guard._current.get()
                statements = []
                with database.observe_trace(statements.append):
                    yield
                observed_loads.append((owner.full_runs, owner.hits, statements.count('PRAGMA integrity_check')))
        with patch.dict(os.environ, {'HAPPYRANCH_DAEMON_HOME': str(root.parent.parent)}), patch.object(org_state_module, 'org_load_validation', capture_load):
            # Establish original settings/namespace once, then retain a genuine
            # prepared ordinary journal for the measured first/next pair.
            state = OrgState.load(slug='scratch', root=root, settings=Settings())
            state.close()
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
                assert physical == full, phase + ' lost full physical checks on misses'
                print('PASS A0b actual sequential OrgState.load:', phase, 'full', full, 'hits', hits)

if sys.argv[1:] in (['--sequential-reuse'], ['--sequential-load']):
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
                trace_observers.close()
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
