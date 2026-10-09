"""S1 store cases; seeded draft rows are validator evidence, not S2 execution."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import sqlite3
import tarfile
from pathlib import Path, PurePosixPath

import pytest

from runtime.infrastructure.database import Database
from runtime.infrastructure import workflow_schema as schema
from tests.workflows.test_draft_dispatch import draft_host
from tests.daemon.test_workflow_activation_routes import activation_org
from runtime.workflows.cutover import WorkflowCutoverStore, WorkflowCutoverError


def test_foundation_enable_refuses_before_any_event_and_names_script(tmp_path: Path) -> None:
    db = Database(tmp_path / 'happyranch.db')
    try:
        schema.install_or_recover(db)
        before = [tuple(r) for r in db.execute('SELECT * FROM workflow_cutover_events')]
        store = WorkflowCutoverStore(db, org_slug='alpha')
        with pytest.raises(WorkflowCutoverError, match='draft_schema_migration_required'):
            store.request(action='enable', operation_key='enable', expected_generation=1)
        assert [tuple(r) for r in db.execute('SELECT * FROM workflow_cutover_events')] == before
        projection = store.get()
        assert projection['state'] == 'installed_legacy_only'
        assert 'migrate_workflow_draft_schema.py' in projection['blockers'][0]['required_action']
        assert '--org alpha' in projection['blockers'][0]['required_action']
        assert not db.execute("SELECT 1 FROM sqlite_schema WHERE name='workflow_draft_adapter_versions'").fetchall()
    finally:
        db.close()


def _migrate(db: Database, slug: str = 'alpha') -> None:
    with db.workflow_schema_transaction() as conn:
        schema.migrate_draft_schema(conn, expected_org_slug=slug)


def test_separate_ddl_matches_reviewed_fixture_and_complete_f_e_layouts(tmp_path: Path) -> None:
    fixture = Path(__file__).parents[1] / 'fixtures/workflow_u0/proposed_workflow_draft_schema.sql'
    assert schema.CANONICAL_WORKFLOW_DRAFT_DDL.encode() == fixture.read_bytes()
    db = Database(tmp_path / 'happyranch.db')
    try:
        schema.install_or_recover(db)
        assert schema.validate_workflow_schema(db._conn, expected_org_slug='alpha') == 'F'
        original = [tuple(r) for r in db.execute('SELECT * FROM workflow_cutover_events')]
        _migrate(db)
        assert schema.validate_workflow_schema(db._conn, expected_org_slug='alpha') == 'E'
        assert [tuple(r) for r in db.execute('SELECT * FROM workflow_cutover_events')] == original
        assert schema.install_or_recover(db, expected_org_slug='alpha') == 'reopened'
        assert schema._layout(db._conn) == schema._canonical_layout('E')
        assert len([r for r in schema._layout(db._conn)[0] if r[0] == 'table']) == 47
        assert len([r for r in schema._layout(db._conn)[0] if r[0] == 'index' and r[3] and r[1].startswith('workflow_draft_')]) == 6
        store = WorkflowCutoverStore(db, org_slug='alpha')
        assert store.get()['blockers'] == []
        assert store.request(action='enable', operation_key='enable', expected_generation=1)['state'] == 'enabled'
        assert store.request(action='disable', operation_key='disable', expected_generation=4)['state'] == 'drained'
        assert not store.downgrade_preflight()['eligible']
    finally:
        db.close()


@pytest.mark.parametrize('sql', [
    'DROP INDEX workflow_draft_current_idx',
    'CREATE INDEX workflow_rogue ON workflow_cutover_state(state)',
    'DELETE FROM workflow_draft_adapter_versions',
    'PRAGMA ignore_check_constraints=ON; UPDATE workflow_draft_adapter_versions SET version=2',
    'ALTER TABLE workflow_draft_dispatch_events ADD COLUMN forged TEXT',
])
def test_partial_wrong_or_unknown_extension_refuses_without_repair(tmp_path: Path, sql: str) -> None:
    db = Database(tmp_path / 'happyranch.db')
    try:
        schema.install_or_recover(db)
        _migrate(db)
        db._conn.executescript(sql)
        before = tuple(db._conn.iterdump())
        for _ in range(2):
            with pytest.raises(ValueError, match='workflow_.*(mismatch|corrupt)'):
                with db.workflow_schema_transaction() as conn:
                    schema.migrate_draft_schema(conn, expected_org_slug='alpha')
            assert tuple(db._conn.iterdump()) == before
    finally:
        db.close()


def test_empty_extension_always_requires_compatible_reader_but_is_not_work(tmp_path: Path) -> None:
    db = Database(tmp_path / 'happyranch.db')
    try:
        schema.install_or_recover(db)
        store = WorkflowCutoverStore(db, org_slug='alpha')
        assert store.downgrade_preflight()['eligible']
        _migrate(db)
        result = store.downgrade_preflight()
        assert not result['eligible']
        assert [b['code'] for b in result['blockers']] == ['draft_schema_requires_compatible_reader']
        assert store._compatibility_blockers(db._conn) == []
        assert store._drain_blockers(db._conn) == []
    finally:
        db.close()


def test_existing_org_missing_database_load_twice_never_creates_extension(tmp_path: Path) -> None:
    from runtime.config import Settings
    from runtime.daemon.org_state import OrgState
    root = tmp_path / 'runtime/orgs/alpha'
    (root / 'org/agents').mkdir(parents=True)
    (root / 'org/teams.yaml').write_text('teams: {}\n')
    for _ in range(2):
        org = OrgState.load(slug='alpha', root=root, settings=Settings())
        try:
            assert schema.validate_workflow_schema(org.db._conn, expected_org_slug='alpha') == 'F'
            assert WorkflowCutoverStore(org.db, org_slug='alpha').get()['blockers'][0]['code'] == 'draft_schema_migration_required'
        finally:
            org.close()


@pytest.mark.parametrize('extension_origin', ['migration', 'new-org'])
def test_source_pinned_preceding_reader_reopens_f_and_refuses_e(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extension_origin: str, preceding_source: Path) -> None:
    import subprocess
    import sys
    from runtime.config import Settings
    from runtime.daemon.org_state import OrgState
    source = preceding_source
    root = tmp_path / 'runtime/orgs/alpha'
    (root / 'org/agents').mkdir(parents=True)
    (root / 'org/teams.yaml').write_text('teams: {}\n')
    org = OrgState.load(slug='alpha', root=root, settings=Settings())
    from runtime.models import TaskRecord
    org.db.insert_task(TaskRecord(id='TASK-100',brief='preserved legacy row',assigned_agent='maker',team='engineering'))
    org.close()
    f_before = {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mode & 0o777) for p in root.rglob('*') if p.is_file()}
    driver = _PRECEDING_IMPORT_CHECK + '''
import runtime.daemon.org_state as module
from runtime.config import Settings
org = module.OrgState.load(slug='alpha', root=Path(sys.argv[2]), settings=Settings())
org.close()
assert_pinned_imports()
print('pinned-reader-reopened')
'''
    for _ in range(2):
        old = subprocess.run([sys.executable, '-c', driver, str(source), str(root)], text=True, capture_output=True, timeout=15)
        assert old.returncode == 0 and 'pinned-reader-reopened' in old.stdout, old.stderr
        assert {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mode & 0o777) for p in root.rglob('*') if p.is_file()} == f_before
    if extension_origin == 'migration':
        db = Database(root / 'happyranch.db')
        _migrate(db)
        db.close()
    else:
        # The historical 'new-org' E input now uses the original explicit
        # operator, because actual new POST creates G. Keep the native ID.
        from runtime.runtime import RuntimeDir
        from tests.test_workflow_draft_migration_script import _run
        runtime = RuntimeDir.init(tmp_path / 'runtime')
        result = _run(runtime.root)
        assert result.returncode == 0 and 'layout E' in result.stdout, result.stderr
    before = {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mode & 0o777) for p in root.rglob('*') if p.is_file()}
    old = subprocess.run([sys.executable, '-c', driver, str(source), str(root)], text=True, capture_output=True, timeout=15)
    assert old.returncode != 0 and 'workflow_schema_object_set_mismatch' in old.stderr
    assert {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mode & 0o777) for p in root.rglob('*') if p.is_file()} == before
    for _ in range(2):
        current = OrgState.load(slug='alpha', root=root, settings=Settings())
        assert schema.validate_workflow_schema(current.db._conn, expected_org_slug='alpha') == 'E'
        current.close()


def _json(value: object) -> bytes:
    import json
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def _digest(value: bytes) -> str:
    import hashlib
    return hashlib.sha256(value).hexdigest()


def _seed_valid_draft(db: Database) -> str:
    """SQL/validator case only. No S2 activation/dispatch acceptance is claimed."""
    from runtime.models import TaskRecord
    from runtime.workflows.templates import WorkflowTemplatePrincipal, WorkflowTemplateStore
    from tests.workflows.test_template_store import VALID_DEFINITION
    db.insert_task(TaskRecord(id='TASK-001', brief='draft', team='engineering', assigned_agent='maker'))
    principal = WorkflowTemplatePrincipal.founder(org_slug='alpha', team_slug='engineering', revalidate=lambda: None)
    version = WorkflowTemplateStore(db).publish_version(org_slug='alpha', principal=principal,
        namespace='org/alpha/team/engineering', operation_key='publish', template_name='product-design',
        definition=VALID_DEFINITION, expected_current_version=0)
    empty = _json({})
    digest = _digest(empty)
    timestamp = '2026-10-05T00:00:00+00:00'
    db.execute('INSERT INTO workflow_authorization_revisions VALUES (?,?,?,?,?,?,?)', ('auth','org/alpha/team/engineering',1,empty,digest,'source',timestamp))
    db.execute('INSERT INTO workflow_binding_snapshots VALUES (?,?,?,?,?,?)', ('binding',version.version_id,'auth',empty,digest,timestamp))
    db.execute('INSERT INTO workflow_contexts VALUES (?,?,?,?,?,?)', ('context','binding',empty,digest,'task','TASK-001'))
    db.execute('INSERT INTO workflow_instances VALUES (?,?,?,?,?,?)', ('instance','binding','context','TASK-001','founder','draft'))
    db.execute('INSERT INTO workflow_activations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
        ('activation','instance',1,version.identity_id,version.version_id,'org/alpha/team/engineering',1,digest,digest,'founder','active',timestamp))
    db.execute('INSERT INTO workflow_active_activations VALUES (?,?,?)', ('instance','activation',1))
    request = _json({'action': 'activate', 'instance_id': 'instance'})
    scope = _json({'assigned_agent': 'maker', 'team': 'engineering', 'brief': 'draft'})
    admission = dict(org_slug='alpha', instance_id='instance', activation_id='activation', activation_revision=1,
                     attempt_sequence=1, predecessor_intent_id=None, admission_principal='founder', operation_key='activate', request_digest=_digest(request))
    intent_id = _digest(_json(admission))
    intent = dict(id=intent_id, instance_id='instance', activation_id='activation', activation_revision=1, attempt_sequence=1,
        predecessor_intent_id=None, admission_kind='initial', admission_principal='founder', operation_key='activate',
        request_bytes=request, request_digest=_digest(request), task_id='TASK-001', context_id='context',
        binding_snapshot_id='binding', assigned_principal='maker', assignment_generation=1,
        authority_namespace='org/alpha/team/engineering', authority_generation=1, authority_digest=digest,
        task_scope_bytes=scope, task_scope_digest=_digest(scope), effect_key='workflow-initial-draft:instance:1',
        host_execution_key='workflow-draft-host:'+intent_id, is_current=1, state='queued', cancellation_requested=0,
        claim_token=None, claim_owner=None, host_launch_started=0, host_execution_id=None, session_id=None,
        final_result_id=None, recovery_owner='workflow_recovery', last_error=None, created_at=timestamp, updated_at=timestamp)
    columns = ','.join(intent)
    db.execute(f'INSERT INTO workflow_draft_dispatch_intents ({columns}) VALUES ({",".join("?" for _ in intent)})', tuple(intent.values()))
    _seed_event(db, intent_id, 'admitted', before=None)
    db._conn.commit()
    return intent_id


_PROJECTION = ('state', 'is_current', 'cancellation_requested', 'claim_token', 'claim_owner', 'host_launch_started', 'host_execution_id', 'session_id', 'final_result_id')


def _seed_event(db: Database, intent_id: str, kind: str, *, before: dict | None, terminal: bool = False, result: dict | None = None, **changes: object) -> None:
    intent = dict(db.execute('SELECT * FROM workflow_draft_dispatch_intents WHERE id=?', (intent_id,)).fetchone())
    old = db.execute('SELECT event_seq,event_digest FROM workflow_draft_dispatch_events WHERE intent_id=? ORDER BY event_seq DESC LIMIT 1', (intent_id,)).fetchone()
    seq = 1 if old is None else old['event_seq'] + 1
    after = {key: intent[key] for key in _PROJECTION}
    after.update(changes)
    mutable = set(_PROJECTION) | {'last_error', 'updated_at'}
    immutable = {key: (value.hex() if isinstance(value, bytes) else value) for key, value in intent.items() if key not in mutable}
    event_id = f'{intent_id}:{seq}'
    timestamp = f'2026-10-05T00:00:{seq-1:02d}+00:00'
    callback = kind in ('callback_recorded','callback_rejected')
    closure = None
    if result is not None:
        if callback:
            closure = dict(record=result,id=result['id'],digest=_digest(_json(result)),disposition='accepted',accepted=int(kind=='callback_recorded'))
        else:
            import json
            accepted = db.execute("SELECT event_bytes FROM workflow_draft_dispatch_events WHERE intent_id=? AND event_kind='callback_recorded'",(intent_id,)).fetchone()
            closure = json.loads(accepted[0])['result']
    payload = dict(format='workflow-draft-event@1', org_slug='alpha', event=dict(id=event_id,event_seq=seq,event_kind=kind,created_at=timestamp),
                   previous_digest=None if old is None else old['event_digest'], intent=immutable, before=before, after=after,
                   terminal_evidence={'host_quiescent': True} if terminal else None, result=closure)
    raw = _json(payload)
    callback = kind in ('callback_recorded','callback_rejected')
    db.execute('INSERT INTO workflow_draft_dispatch_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (event_id,intent_id,seq,kind,None if before is None else before['state'],after['state'],raw,_digest(raw),
         after['session_id'] if callback else None, result['id'] if callback else None,
         _digest(_json(result)) if callback else None, int(kind=='callback_recorded') if callback else None,
         'accepted' if callback else None,timestamp))
    db.execute('UPDATE workflow_draft_dispatch_intents SET '+','.join(key+'=?' for key in _PROJECTION)+',updated_at=? WHERE id=?',
               tuple(after[key] for key in _PROJECTION)+(timestamp,intent_id))


def _projection(db: Database, intent_id: str) -> dict:
    row = dict(db.execute('SELECT * FROM workflow_draft_dispatch_intents WHERE id=?', (intent_id,)).fetchone())
    return {key: row[key] for key in _PROJECTION}


def _seed_launched_terminal_history(db: Database, source: str, target: str, kind: str) -> str:
    """Retained SQL history only; no host producer or S2 execution proof."""
    intent = _seed_valid_draft(db)
    _seed_event(db, intent, 'claimed', before=_projection(db, intent), state='claimed',
                claim_token='claim', claim_owner='owner')
    _seed_event(db, intent, 'launch_reserved', before=_projection(db, intent), host_launch_started=1)
    if source != 'claimed':
        _seed_event(db, intent, 'running', before=_projection(db, intent), state='running',
                    host_execution_id='host', session_id='session')
    if source == 'uncertain':
        _seed_event(db, intent, 'uncertain', before=_projection(db, intent), state='uncertain')
    result = None
    if target == 'completed':
        cursor = db.execute("INSERT INTO task_results(task_id,agent,session_id,status,created_at) "
                            "VALUES ('TASK-001','maker','session','completed','2026-10-05T00:00:00Z')")
        result = dict(db.execute('SELECT * FROM task_results WHERE id=?', (cursor.lastrowid,)).fetchone())
        _seed_event(db, intent, 'callback_recorded', before=_projection(db, intent),
                    final_result_id=result['id'], result=result)
    db.execute('UPDATE tasks SET status=? WHERE id=?', (target, 'TASK-001'))
    _seed_event(db, intent, kind, before=_projection(db, intent), state=target, terminal=True, result=result)
    db._conn.commit()
    return intent


@pytest.mark.parametrize('source,target,kind', [
    ('claimed', 'cancelled', 'cancelled'), ('claimed', 'failed', 'failed'),
    ('running', 'cancelled', 'cancelled'), ('running', 'failed', 'failed'),
    ('running', 'completed', 'completed'),
    ('uncertain', 'cancelled', 'cancelled'), ('uncertain', 'failed', 'failed'),
    ('uncertain', 'completed', 'completed'),
    ('uncertain', 'cancelled', 'host_reconciled'), ('uncertain', 'failed', 'host_reconciled'),
    ('uncertain', 'completed', 'host_reconciled'),
])
@pytest.mark.parametrize('witness', [
    pytest.param({'host_quiescent': True}, id='literal-true'),
    pytest.param({'host_quiescent': 1}, id='integer-one'),
    pytest.param({'host_quiescent': 1.0}, id='float-one'),
    pytest.param({'host_quiescent': False}, id='false'),
    pytest.param({'host_quiescent': 0}, id='zero'),
    pytest.param({'host_quiescent': None}, id='null-value'),
    pytest.param({}, id='missing-key'),
    pytest.param({'host_quiescent': True, 'extra': True}, id='extra-key'),
    pytest.param(None, id='missing-evidence'),
    pytest.param([{'host_quiescent': True}], id='array-shape'),
    pytest.param(True, id='boolean-shape'),
    pytest.param('true', id='string-shape'),
])
def test_launched_terminal_witness_requires_literal_true_without_writes(
    tmp_path: Path, source: str, target: str, kind: str, witness: object,
) -> None:
    db = Database(tmp_path / 'happyranch.db')
    try:
        schema.install_or_recover(db)
        _migrate(db)
        store = WorkflowCutoverStore(db, org_slug='alpha')
        assert store.request(action='enable', operation_key='enable', expected_generation=1)['state'] == 'enabled'
        intent = _seed_launched_terminal_history(db, source, target, kind)
        # Validate the complete legal closure before changing only its witness.
        assert schema.validate_workflow_schema(db._conn, expected_org_slug='alpha') == 'E'
        event = dict(db.execute('SELECT * FROM workflow_draft_dispatch_events WHERE intent_id=? '
                                'ORDER BY event_seq DESC LIMIT 1', (intent,)).fetchone())
        payload = json.loads(event['event_bytes'])
        payload['terminal_evidence'] = witness
        raw = schema._canonical_bytes(payload)
        digest = hashlib.sha256(raw).hexdigest()
        db.execute('UPDATE workflow_draft_dispatch_events SET event_bytes=?,event_digest=? WHERE id=?',
                   (raw, digest, event['id']))
        db._conn.commit()
        assert hashlib.sha256(db.execute('SELECT event_bytes FROM workflow_draft_dispatch_events WHERE id=?',
                                        (event['id'],)).fetchone()[0]).hexdigest() == digest
        before = tuple(db._conn.iterdump())
        file_bytes, file_mode = db.db_path.read_bytes(), db.db_path.stat().st_mode
        valid = isinstance(witness, dict) and set(witness) == {'host_quiescent'} and witness['host_quiescent'] is True
        for _ in range(2):
            if valid:
                assert schema.validate_workflow_schema(db._conn, expected_org_slug='alpha') == 'E'
                assert schema.install_or_recover(db, expected_org_slug='alpha') == 'reopened'
                assert not store.get()['blockers']
            else:
                with pytest.raises(ValueError, match='workflow_draft_data_corrupt'):
                    schema.validate_workflow_schema(db._conn, expected_org_slug='alpha')
                with pytest.raises(ValueError, match='workflow_draft_data_corrupt'):
                    schema.install_or_recover(db, expected_org_slug='alpha')
                projection = store.recover_authorized()
                assert projection['state'] == 'enabled'
                assert projection['reconciliation_required']
                assert projection['blockers'][0]['code'] == 'cutover_draft_closure'
                with pytest.raises(WorkflowCutoverError, match='cutover_storage_corrupt'):
                    store.request(action='disable', operation_key='disable', expected_generation=4)
            assert tuple(db._conn.iterdump()) == before
            assert db.db_path.read_bytes() == file_bytes and db.db_path.stat().st_mode == file_mode
        if valid:
            drained = store.request(action='disable', operation_key='disable', expected_generation=4)
            assert drained['state'] == 'drained' and drained['blockers'] == []
    finally:
        db.close()


@pytest.mark.parametrize('state', ['queued','claimed','running','uncertain','cancel-pending','cancelled','failed','completed'])
def test_sql_seeded_draft_closure_and_drain_projection(tmp_path: Path, state: str) -> None:
    db = Database(tmp_path / 'happyranch.db')
    try:
        schema.install_or_recover(db)
        _migrate(db)
        store = WorkflowCutoverStore(db, org_slug='alpha')
        assert store.request(action='enable', operation_key='enable', expected_generation=1)['state'] == 'enabled'
        intent = _seed_valid_draft(db)
        if state not in ('queued', 'cancelled'):
            _seed_event(db,intent,'claimed',before=_projection(db,intent),state='claimed',claim_token='claim',claim_owner='owner')
        if state not in ('queued','claimed','cancelled'):
            _seed_event(db,intent,'launch_reserved',before=_projection(db,intent),host_launch_started=1)
            _seed_event(db,intent,'running',before=_projection(db,intent),state='running',host_execution_id='host',session_id='session')
        if state == 'uncertain':
            _seed_event(db,intent,'uncertain',before=_projection(db,intent),state='uncertain')
        if state == 'cancel-pending':
            _seed_event(db,intent,'cancel_requested',before=_projection(db,intent),cancellation_requested=1)
        if state in ('cancelled','failed'):
            db.execute('UPDATE tasks SET status=? WHERE id=?', (state,'TASK-001'))
            _seed_event(db,intent,state,before=_projection(db,intent),state=state,terminal=state=='failed')
        if state == 'completed':
            cursor = db.execute("INSERT INTO task_results(task_id,agent,session_id,status,created_at) VALUES ('TASK-001','maker','session','completed','2026-10-05T00:00:00Z')")
            result = dict(db.execute('SELECT * FROM task_results WHERE id=?',(cursor.lastrowid,)).fetchone())
            _seed_event(db,intent,'callback_recorded',before=_projection(db,intent),final_result_id=result['id'],result=result)
            db.execute("UPDATE tasks SET status='completed' WHERE id='TASK-001'")
            _seed_event(db,intent,'completed',before=_projection(db,intent),state='completed',terminal=True,result=result)
        db._conn.commit()
        assert schema.validate_workflow_schema(db._conn,expected_org_slug='alpha') == 'E'
        result = store.request(action='disable',operation_key='disable',expected_generation=4)
        if state in ('cancelled','failed','completed'):
            assert result['state'] == 'drained' and result['blockers'] == []
        else:
            assert result['state'] == 'draining' and result['reconciliation_required']
            assert result['blockers'][0]['record_id'] == intent
        # Replay is a byte-preserving read even with populated extension.
        before = tuple(db._conn.iterdump())
        assert schema.install_or_recover(db,expected_org_slug='alpha') == 'reopened'
        _migrate(db)
        assert tuple(db._conn.iterdump()) == before
    finally:
        db.close()


@pytest.mark.parametrize('corruption', ['digest','root','missing-event','projection','terminal-pointer','foreign-session'])
def test_sql_seeded_invalid_draft_cannot_hide_behind_terminal_projection(tmp_path: Path, corruption: str) -> None:
    db = Database(tmp_path / 'happyranch.db')
    try:
        schema.install_or_recover(db)
        _migrate(db)
        intent = _seed_valid_draft(db)
        if corruption == 'digest':
            db.execute("UPDATE workflow_draft_dispatch_events SET event_digest='forged'")
        elif corruption == 'root':
            db.execute("UPDATE workflow_instances SET root_task_id='TASK-other'")
        elif corruption == 'missing-event':
            db.execute('DELETE FROM workflow_draft_dispatch_events')
        elif corruption == 'projection':
            db.execute("UPDATE workflow_draft_dispatch_intents SET state='cancelled'")
        elif corruption == 'terminal-pointer':
            db.execute("UPDATE workflow_draft_dispatch_intents SET state='cancelled',is_current=0")
        else:
            db.execute("UPDATE workflow_draft_dispatch_intents SET session_id='foreign',state='cancelled'")
        db._conn.commit()
        before = tuple(db._conn.iterdump())
        with pytest.raises(ValueError, match='workflow_draft_data_corrupt'):
            schema.validate_workflow_schema(db._conn,expected_org_slug='alpha')
        projection = WorkflowCutoverStore(db,org_slug='alpha').get()
        assert projection['reconciliation_required']
        assert projection['blockers'][0]['code'] == 'cutover_draft_closure'
        assert tuple(db._conn.iterdump()) == before
    finally:
        db.close()


# Trusted identities are independent of the supplied manifest. The bundle is
# the exact public historical runtime subtree, never a candidate schema oracle.
_PRECEDING_PIN = 'faf40744f8a0119d54056338865777588121b7af'
_PRECEDING_TAR_SHA256 = '948a51b79ebe3632489c7bb82371363f70b4d8890b93097d5ff82fe0b319cc89'
_PRECEDING_GZIP_SHA256 = 'bf1dbd80a7600c3bb7ea92791dd9f6db77a7f9539885dc0092e8c6ddf4d067d9'
_PRECEDING_MANIFEST_SHA256 = 'af53ce5b65535208808c4c32baf9e6919aa73b6eda6b9bbd8b50efb92f110adf'
_PRECEDING_SCHEMA_SHA256 = '01acbc4bc5c9745c481baa9e920dd244f5b32ea4fb511eac4941da3f95620538'
_PRECEDING_FIXTURES = Path(__file__).parents[1] / 'fixtures/workflow_u0'
_PRECEDING_STEM = 'preceding_reader_faf40744_runtime'
_PRECEDING_IMPORT_CHECK = '''import importlib, sys
from pathlib import Path
source = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(source))
for name in ('runtime', 'runtime.daemon.org_state', 'runtime.config',
             'runtime.infrastructure.database', 'runtime.infrastructure.workflow_schema',
             'runtime.workflows.cutover'):
    module = importlib.import_module(name)
    assert Path(module.__file__).resolve().is_relative_to(source / 'runtime'), (name, module.__file__)
def assert_pinned_imports():
    for name, module in tuple(sys.modules.items()):
        if name == 'runtime' or name.startswith('runtime.'):
            assert Path(module.__file__).resolve().is_relative_to(source / 'runtime'), (name, module.__file__)
assert_pinned_imports()
'''


def _extract_preceding_source(fixture_dir: Path, source: Path) -> Path:
    def read(suffix: str, expected: str) -> bytes:
        path = fixture_dir / (_PRECEDING_STEM + suffix)
        try:
            raw = path.read_bytes()
        except FileNotFoundError as exc:
            raise ValueError(f'preceding_source_missing: {path.name}') from exc
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError(f'preceding_source_hash_mismatch: {path.name}')
        return raw

    packed = read('.tar.gz', _PRECEDING_GZIP_SHA256)
    manifest = json.loads(read('.manifest.json', _PRECEDING_MANIFEST_SHA256))
    if (manifest['format'], manifest['commit'], manifest['tree'], manifest['runtime_tree'], manifest['subtree']) != (
        'workflow-preceding-reader-source@1', _PRECEDING_PIN,
        '0c1b6ca39dde2e81b3feae795335e60daa14ecea',
        '87a09c834af88cabe151bc96cb7cdb48ac3ecf72', 'runtime',
    ):
        raise ValueError('preceding_source_identity_mismatch')
    raw = gzip.decompress(packed)
    if len(raw) != 6318080 or hashlib.sha256(raw).hexdigest() != _PRECEDING_TAR_SHA256:
        raise ValueError('preceding_source_tar_mismatch')
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        files = []
        paths = set()
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if (path.is_absolute() or '..' in path.parts or not path.parts
                or path.parts[0] != 'runtime' or member.name in paths
                or not (member.isdir() or member.isfile())):
                raise ValueError('preceding_source_unsafe_member')
            paths.add(member.name)
            if member.isfile():
                files.append(dict(path=member.name, mode=oct(member.mode), size=member.size,
                                  sha256=hashlib.sha256(archive.extractfile(member).read()).hexdigest()))
        if files != manifest['files']:
            raise ValueError('preceding_source_file_manifest_mismatch')
        if source.exists():
            raise ValueError('preceding_source_destination_exists')
        source.mkdir()
        archive.extractall(source, filter='data')
        # The safe data filter removes group-write bits. Restore only modes
        # authenticated in the pinned tar, after path/type validation.
        for entry in files:
            (source / entry['path']).chmod(int(entry['mode'], 8))
    actual = []
    for entry in files:
        path = source / entry['path']
        actual.append(dict(path=entry['path'], mode=oct(path.stat().st_mode & 0o777),
                           size=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    if actual != files:
        raise ValueError('preceding_source_extracted_manifest_mismatch')
    if hashlib.sha256((source / 'runtime/infrastructure/workflow_schema.py').read_bytes()).hexdigest() != _PRECEDING_SCHEMA_SHA256:
        raise ValueError('preceding_source_schema_mismatch')
    return source


@pytest.fixture(scope='module')
def preceding_source(tmp_path_factory: pytest.TempPathFactory) -> Path:
    source = tmp_path_factory.mktemp('S1-preceding-source') / 'source'
    return _extract_preceding_source(_PRECEDING_FIXTURES, source)


@pytest.mark.parametrize('damage', [
    'missing-archive', 'truncated-archive', 'corrupt-archive', 'unknown-archive',
    'missing-manifest', 'wrong-commit', 'wrong-file-mode', 'unknown-manifest',
])
def test_preceding_source_refuses_untrusted_fixture_before_extraction(tmp_path: Path, damage: str) -> None:
    fixture_dir = tmp_path / 'fixtures'
    fixture_dir.mkdir()
    archive = fixture_dir / (_PRECEDING_STEM + '.tar.gz')
    manifest = fixture_dir / (_PRECEDING_STEM + '.manifest.json')
    archive.write_bytes((_PRECEDING_FIXTURES / archive.name).read_bytes())
    manifest.write_bytes((_PRECEDING_FIXTURES / manifest.name).read_bytes())
    if damage == 'missing-archive':
        archive.unlink()
    elif damage == 'truncated-archive':
        archive.write_bytes(archive.read_bytes()[:-1])
    elif damage == 'corrupt-archive':
        raw = bytearray(archive.read_bytes())
        raw[len(raw) // 2] ^= 1
        archive.write_bytes(raw)
    elif damage == 'unknown-archive':
        archive.write_bytes(gzip.compress(b'unrelated historical source', mtime=0))
    elif damage == 'missing-manifest':
        manifest.unlink()
    else:
        supplied = json.loads(manifest.read_bytes())
        if damage == 'wrong-commit':
            supplied['commit'] = '0' * 40
        elif damage == 'wrong-file-mode':
            supplied['files'][0]['mode'] = '0o777'
        else:
            supplied['format'] = 'unknown@1'
        manifest.write_text(json.dumps(supplied))
    source = tmp_path / 'source'
    expected = 'preceding_source_missing' if damage.startswith('missing-') else 'preceding_source_hash_mismatch'
    with pytest.raises(ValueError, match=expected):
        _extract_preceding_source(fixture_dir, source)
    assert not source.exists()


def test_preceding_source_extracts_exact_bytes_modes_and_refuses_existing_destination(tmp_path: Path) -> None:
    source = _extract_preceding_source(_PRECEDING_FIXTURES, tmp_path / 'source')
    manifest = json.loads((_PRECEDING_FIXTURES / (_PRECEDING_STEM + '.manifest.json')).read_bytes())
    assert {str(p.relative_to(source)) for p in source.rglob('*') if p.is_file()} == {e['path'] for e in manifest['files']}
    before = {str(p.relative_to(source)): (p.read_bytes(), p.stat().st_mode & 0o777) for p in source.rglob('*') if p.is_file()}
    for entry in manifest['files']:
        raw, mode = before[entry['path']]
        assert hashlib.sha256(raw).hexdigest() == entry['sha256']
        assert len(raw) == entry['size'] and oct(mode) == entry['mode']
    with pytest.raises(ValueError, match='preceding_source_destination_exists'):
        _extract_preceding_source(_PRECEDING_FIXTURES, source)
    assert {str(p.relative_to(source)): (p.read_bytes(), p.stat().st_mode & 0o777) for p in source.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('generation', range(1,8))
def test_source_pinned_progressed_f_never_auto_advances_and_actual_script_migrates(tmp_path: Path, preceding_source: Path, generation: int) -> None:
    """YES test-side commit observation over the actual pinned cutover writer."""
    import subprocess
    import sys
    from runtime.config import Settings
    from runtime.daemon.org_state import OrgState
    from runtime.runtime import RuntimeDir
    from tests.test_workflow_draft_migration_script import _run
    runtime = RuntimeDir.init(tmp_path / 'runtime')
    root = runtime.orgs_dir / 'alpha'
    (root / 'org/agents').mkdir(parents=True)
    (root / 'org/teams.yaml').write_text('teams: {}\n')
    driver = _PRECEDING_IMPORT_CHECK + '''
from runtime.daemon.org_state import OrgState
from runtime.config import Settings
from runtime.workflows.cutover import WorkflowCutoverStore
org=OrgState.load(slug='alpha',root=Path(sys.argv[2]),settings=Settings())
target=int(sys.argv[3]); original=org.db._conn
class Boundary(Exception): pass
class Observer:
    def __getattr__(self,key): return getattr(original,key)
    def commit(self):
        original.commit()
        if original.execute('SELECT generation FROM workflow_cutover_state').fetchone()[0] == target:
            raise Boundary()
if target>1:
    org.db._conn=Observer()
    store=WorkflowCutoverStore(org.db,org_slug='alpha')
    try:
        store.request(action='enable',operation_key='historical-enable',expected_generation=1)
        if target>=5:
            store.request(action='disable',operation_key='historical-disable',expected_generation=4)
    except Boundary: pass
    finally: org.db._conn=original
assert original.execute('SELECT generation FROM workflow_cutover_state').fetchone()[0]==target
org.close()
assert_pinned_imports()
'''
    producer = subprocess.run([sys.executable,'-c',driver,str(preceding_source),str(root),str(generation)],text=True,capture_output=True,timeout=15)
    assert producer.returncode == 0, producer.stderr
    def snapshot() -> tuple:
        conn = sqlite3.connect(root / 'happyranch.db')
        try:
            schema_rows = tuple(conn.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY name'))
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name")]
            return schema_rows,tuple((table,tuple(conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid'))) for table in tables)
        finally:
            conn.close()
    before = snapshot()
    additive_indexes = (
        ('index', 'idx_audit_log_task_id', 'audit_log',
         'CREATE INDEX idx_audit_log_task_id ON audit_log(task_id)'),
        ('index', 'idx_cleanup_tasks_agent_created_id', 'tasks',
         'CREATE INDEX idx_cleanup_tasks_agent_created_id ON tasks(assigned_agent,created_at DESC,id DESC)'),
        ('index', 'idx_cleanup_trigger_task_agent', 'audit_log',
         "CREATE INDEX idx_cleanup_trigger_task_agent ON audit_log(task_id,agent) WHERE action='workspace_cleanup_triggered'"),
        ('index', 'idx_cleanup_results_task_agent_id', 'task_results',
         'CREATE INDEX idx_cleanup_results_task_agent_id ON task_results(task_id,agent,id DESC)'),
    )
    assert not {row[1] for row in before[0]} & {row[1] for row in additive_indexes}
    # Independent reviewed declarations: the authentic predecessor lacks all
    # four indexes; main4cc1 shipped the three cleanup indexes. SQLite stores
    # CREATE INDEX without IF NOT EXISTS. No expectations derive from actual DB.
    expected_schema = tuple(sorted((*before[0], *additive_indexes), key=lambda row: row[1]))
    for _ in range(2):
        org = OrgState.load(slug='alpha',root=root,settings=Settings())
        try:
            status = WorkflowCutoverStore(org.db,org_slug='alpha').get()
            assert status['generation'] == generation
            assert 'migrate_workflow_draft_schema.py' in status['blockers'][0]['required_action']
            assert schema.validate_workflow_schema(org.db._conn,expected_org_slug='alpha') == 'F'
        finally:
            org.close()
        after_schema, after_data = snapshot()
        assert after_schema == expected_schema
        assert set(after_schema) - set(before[0]) == set(additive_indexes)
        assert len(after_schema) == len(before[0]) + 4
        assert after_data == before[1]
    migrated = _run(runtime.root)
    assert migrated.returncode == 0 and 'migrated:' in migrated.stdout, migrated.stderr
    org = OrgState.load(slug='alpha',root=root,settings=Settings())
    try:
        expected = 1 if generation==1 else (4 if generation<=4 else 7)
        assert WorkflowCutoverStore(org.db,org_slug='alpha').get()['generation'] == expected
        assert schema.validate_workflow_schema(org.db._conn,expected_org_slug='alpha') == 'E'
    finally:
        org.close()


def test_sql_seeded_causal_replacement_keeps_original_root_and_requires_cancel_before_retirement(tmp_path: Path) -> None:
    from runtime.models import TaskRecord
    db = Database(tmp_path / 'happyranch.db')
    try:
        schema.install_or_recover(db)
        _migrate(db)
        first = _seed_valid_draft(db)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute('UPDATE workflow_draft_dispatch_intents SET is_current=0 WHERE id=?', (first,))
        db.execute("UPDATE tasks SET status='cancelled' WHERE id='TASK-001'")
        _seed_event(db,first,'cancelled',before=_projection(db,first),state='cancelled')
        _seed_event(db,first,'retired',before=_projection(db,first),is_current=0)
        db._conn.commit()
        db.insert_task(TaskRecord(id='TASK-002',brief='draft2',assigned_agent='maker',team='engineering'))
        second = dict(db.execute('SELECT * FROM workflow_draft_dispatch_intents WHERE id=?',(first,)).fetchone())
        request = _json({'action':'reassignment','predecessor_intent_id':first})
        second.update(attempt_sequence=2,assignment_generation=2,admission_kind='reassignment',predecessor_intent_id=first,
                      operation_key='replace',task_id='TASK-002',is_current=1,state='queued',request_bytes=request,request_digest=_digest(request),
                      task_scope_bytes=_json({'assigned_agent':'maker','team':'engineering','brief':'draft2'}),effect_key='workflow-initial-draft:instance:2')
        second['task_scope_digest'] = _digest(second['task_scope_bytes'])
        admission = {key:second[key] for key in ('instance_id','activation_id','activation_revision','attempt_sequence','predecessor_intent_id','admission_principal','operation_key','request_digest')}
        admission['org_slug']='alpha'
        second['id']=_digest(_json(admission))
        second['host_execution_key']='workflow-draft-host:'+second['id']
        db.execute('INSERT INTO workflow_draft_dispatch_intents ('+','.join(second)+') VALUES ('+','.join('?' for _ in second)+')',tuple(second.values()))
        _seed_event(db,second['id'],'admitted',before=None)
        db._conn.commit()
        assert schema.validate_workflow_schema(db._conn,expected_org_slug='alpha')=='E'
        assert db.execute('SELECT root_task_id FROM workflow_instances').fetchone()[0]=='TASK-001'
        assert db.execute('SELECT COUNT(*) FROM workflow_draft_dispatch_intents').fetchone()[0]==2
        assert db.execute('SELECT state FROM workflow_draft_dispatch_intents WHERE id=?',(first,)).fetchone()[0]=='cancelled'
    finally:
        db.close()


@pytest.mark.parametrize('corruption',['disposition','accepted','result-bytes'])
def test_sql_seeded_callback_preimage_binds_full_normalized_result_and_disposition(tmp_path: Path, corruption: str) -> None:
    db = Database(tmp_path / 'happyranch.db')
    try:
        schema.install_or_recover(db)
        _migrate(db)
        intent = _seed_valid_draft(db)
        _seed_event(db,intent,'claimed',before=_projection(db,intent),state='claimed',claim_token='claim',claim_owner='owner')
        _seed_event(db,intent,'launch_reserved',before=_projection(db,intent),host_launch_started=1)
        _seed_event(db,intent,'running',before=_projection(db,intent),state='running',host_execution_id='host',session_id='session')
        cursor = db.execute("INSERT INTO task_results(task_id,agent,session_id,status,created_at) VALUES ('TASK-001','maker','session','completed','2026-10-05T00:00:00Z')")
        result = dict(db.execute('SELECT * FROM task_results WHERE id=?',(cursor.lastrowid,)).fetchone())
        _seed_event(db,intent,'callback_recorded',before=_projection(db,intent),final_result_id=result['id'],result=result)
        db._conn.commit()
        assert schema.validate_workflow_schema(db._conn,expected_org_slug='alpha') == 'E'
        if corruption == 'disposition':
            db.execute("UPDATE workflow_draft_dispatch_events SET disposition='changed' WHERE event_kind='callback_recorded'")
        elif corruption == 'accepted':
            db.execute('PRAGMA ignore_check_constraints=ON')
            db.execute("UPDATE workflow_draft_dispatch_events SET callback_accepted=0 WHERE event_kind='callback_recorded'")
        else:
            db.execute("UPDATE task_results SET output_summary='changed' WHERE id=?",(result['id'],))
        db._conn.commit()
        before = tuple(db._conn.iterdump())
        with pytest.raises(ValueError,match='workflow_draft_data_corrupt'):
            schema.validate_workflow_schema(db._conn,expected_org_slug='alpha')
        assert tuple(db._conn.iterdump()) == before
    finally:
        db.close()


_S2_STEM = 'preceding_reader_b0b55e9f_runtime'

def _extract_s2_source(fixture_dir: Path, source: Path) -> Path:
    def read(suffix: str, expected: str) -> bytes:
        path = fixture_dir / (_S2_STEM + suffix)
        try:
            raw = path.read_bytes()
        except FileNotFoundError as exc:
            raise ValueError(f'preceding_source_missing: {path.name}') from exc
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError(f'preceding_source_hash_mismatch: {path.name}')
        return raw

    packed = read('.tar.gz', '7812a3b4cc6887c2744805c3622da94fa07b2611202b4a2194ad2745235e185d')
    manifest = json.loads(read('.manifest.json', '055168a183049ea7b95acd6acfa656744629610760fa9e0664f1e9767336e7c8'))
    if (manifest['format'], manifest['commit'], manifest['tree'], manifest['runtime_tree'], manifest['subtree']) != (
        'workflow-preceding-reader-source@1', 'b0b55e9f3302d04c4a5feffee56971db12eac25e',
        'c8545c48dd63f3d1798cbb4453639b546b9ee473',
        '470f1194847c05348531bd6bb59faae41f49c278', 'runtime',
    ):
        raise ValueError('preceding_source_identity_mismatch')
    raw = gzip.decompress(packed)
    if len(raw) != 6461440 or hashlib.sha256(raw).hexdigest() != '4f1f972b009945f93b63d7001c82b24cd43e04ab4152cc2f6793f9d8efa9d6e6':
        raise ValueError('preceding_source_tar_mismatch')
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        files = []
        paths = set()
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if (path.is_absolute() or '..' in path.parts or not path.parts
                or path.parts[0] != 'runtime' or member.name in paths
                or not (member.isdir() or member.isfile())):
                raise ValueError('preceding_source_unsafe_member')
            paths.add(member.name)
            if member.isfile():
                files.append(dict(path=member.name, mode=oct(member.mode), size=member.size,
                                  sha256=hashlib.sha256(archive.extractfile(member).read()).hexdigest()))
        if files != manifest['files']:
            raise ValueError('preceding_source_file_manifest_mismatch')
        if source.exists():
            raise ValueError('preceding_source_destination_exists')
        source.mkdir()
        archive.extractall(source, filter='data')
        # The safe data filter removes group-write bits. Restore only modes
        # authenticated in the pinned tar, after path/type validation.
        for entry in files:
            (source / entry['path']).chmod(int(entry['mode'], 8))
    actual = []
    for entry in files:
        path = source / entry['path']
        actual.append(dict(path=entry['path'], mode=oct(path.stat().st_mode & 0o777),
                           size=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    if actual != files:
        raise ValueError('preceding_source_extracted_manifest_mismatch')
    if hashlib.sha256((source / 'runtime/infrastructure/workflow_schema.py').read_bytes()).hexdigest() != 'aeb4dc21e80f2e4f112d7f76b2d273aa761317e5499fc577401d6581cfcbf835':
        raise ValueError('preceding_source_schema_mismatch')
    return source


@pytest.mark.parametrize('damage', ['none', 'missing-archive', 'truncated-archive', 'corrupt-archive',
    'missing-manifest', 'wrong-pin', 'wrong-mode', 'wrong-member-hash', 'existing-destination'])
def test_g_s2_reader_archive_identity_bytes_modes_and_safe_extraction(tmp_path: Path, damage: str) -> None:
    fixture_dir = tmp_path / 'fixtures'
    fixture_dir.mkdir()
    archive = fixture_dir / (_S2_STEM + '.tar.gz')
    manifest = fixture_dir / (_S2_STEM + '.manifest.json')
    archive.write_bytes((_PRECEDING_FIXTURES / archive.name).read_bytes())
    manifest.write_bytes((_PRECEDING_FIXTURES / manifest.name).read_bytes())
    source = tmp_path / 'source'
    if damage == 'missing-archive':
        archive.unlink()
    elif damage == 'truncated-archive':
        archive.write_bytes(archive.read_bytes()[:-1])
    elif damage == 'corrupt-archive':
        raw = bytearray(archive.read_bytes()); raw[len(raw) // 2] ^= 1; archive.write_bytes(raw)
    elif damage == 'missing-manifest':
        manifest.unlink()
    elif damage in ('wrong-pin', 'wrong-mode', 'wrong-member-hash'):
        supplied = json.loads(manifest.read_bytes())
        if damage == 'wrong-pin':
            supplied['commit'] = '0' * 40
        else:
            supplied['files'][0]['mode' if damage == 'wrong-mode' else 'sha256'] = '0o777' if damage == 'wrong-mode' else '0' * 64
        manifest.write_text(json.dumps(supplied))
    elif damage == 'existing-destination':
        source.mkdir(); (source / 'retained').write_bytes(b'owned preexisting source')
    if damage != 'none':
        before = {p.name: p.read_bytes() for p in source.glob('*')} if source.exists() else None
        with pytest.raises(ValueError, match='preceding_source_(missing|hash_mismatch|destination_exists)'):
            _extract_s2_source(fixture_dir, source)
        assert ({p.name: p.read_bytes() for p in source.glob('*')} if source.exists() else None) == before
        return
    extracted = _extract_s2_source(fixture_dir, source)
    entries = json.loads(manifest.read_bytes())['files']
    assert len(entries) == 265
    assert {str(p.relative_to(source)) for p in extracted.rglob('*') if p.is_file()} == {e['path'] for e in entries}
    for entry in entries:
        path = source / entry['path']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry['sha256']
        assert path.stat().st_mode & 0o777 == int(entry['mode'], 8)


def _g_closed_database_identity(root: Path) -> tuple:
    """Independent full durable observation, without using a workflow reader.

    SQL dump alone loses rowids, storage types and text after embedded NULs.
    Retain those facts separately for EVERY table, including SQLite's sequence
    and the publication leases. Physical free-list/page bytes are not durable
    application identity: ordinary compatible recovery commits a temporary lease.
    """
    from contextlib import closing
    import struct

    def quote(name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    with closing(sqlite3.connect(root / 'happyranch.db')) as observer:
        observer.text_factory = bytes
        objects = tuple(observer.execute(
            'SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name'
        ))
        tables = []
        for (raw_name,) in observer.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name"
        ):
            name = raw_name.decode('utf-8')
            columns = tuple(observer.execute('PRAGMA table_xinfo(' + quote(name) + ')'))
            names = [column[1].decode('utf-8') for column in columns]
            rowid = next((alias for alias in ('rowid', '_rowid_', 'oid')
                          if alias not in {column.lower() for column in names}), None)
            table_flags = next(row for row in observer.execute('PRAGMA table_list')
                               if row[0] == b'main' and row[1] == raw_name)
            has_rowid = not table_flags[4]
            assert not has_rowid or rowid is not None, ('unobservable rowid', name)
            fields = [quote(rowid)] if has_rowid else []
            for column in names:
                # CAST text AS BLOB preserves embedded NUL and original UTF8
                # bytes. REAL values additionally retain their IEEE value.
                fields.extend(('typeof(' + quote(column) + ')', quote(column),
                               'CAST(' + quote(column) + ' AS BLOB)'))
            rows = []
            for row in observer.execute('SELECT ' + ','.join(fields) + ' FROM ' + quote(name)):
                values = tuple(struct.pack('!d', value) if isinstance(value, float) else value
                               for value in row)
                rows.append(values)
            tables.append((raw_name, columns, has_rowid, tuple(sorted(rows, key=repr))))
        assert observer.execute('SELECT * FROM workflow_publication_leases').fetchall() == []
        header = tuple((pragma, tuple(observer.execute('PRAGMA ' + pragma)))
                       for pragma in ('application_id', 'user_version', 'schema_version', 'encoding'))
        # iterdump expects the normal text factory; the independently typed
        # observations above retain raw bytes that SQL's quoting cannot express.
        observer.text_factory = str
        dump = tuple(observer.iterdump())
        return objects, tuple(tables), header, dump


def _g_closed_compatible_file_identity(root: Path) -> dict:
    """Full file set/modes/non-DB bytes; forbid residual database sidecars."""
    import os
    import stat
    result = {}
    for path in (root, *sorted(root.rglob('*'))):
        info = path.lstat()
        assert stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode), path
        name = str(path.relative_to(root))
        if stat.S_ISLNK(info.st_mode):
            result[name] = ('symlink', stat.S_IMODE(info.st_mode), os.fsencode(os.readlink(path)))
        elif stat.S_ISDIR(info.st_mode):
            result[name] = ('directory', stat.S_IMODE(info.st_mode))
        else:
            assert name not in ('happyranch.db-wal', 'happyranch.db-shm', 'happyranch.db-journal'), name
            result[name] = ('file', stat.S_IMODE(info.st_mode),
                            None if name == 'happyranch.db' else path.read_bytes())
    return result


@pytest.mark.parametrize('activation_org', ['E'], indirect=True, ids=['actual-existing-E'])
@pytest.mark.parametrize('origin', ['migration', 'new-org'])
def test_g_source_pinned_faf_and_s2_readers_refuse_g_without_writes(tmp_path: Path, draft_host, preceding_source: Path, origin: str) -> None:
    import subprocess
    import sys
    from runtime.config import Settings
    from runtime.daemon.org_state import OrgState
    from runtime.daemon.app import create_app
    from runtime.daemon.state import DaemonState
    from runtime.runtime import RuntimeDir
    from runtime.daemon import paths
    from fastapi.testclient import TestClient
    from tests.daemon.test_workflow_activation_routes import BASE
    from tests.test_workflow_submission_migration_script import _run as run_submission
    client, org, state, body, controls, observations, backend = draft_host
    response = client.post(BASE, json=body)
    assert response.status_code == 201, response.text
    org.orchestrator.run_step(response.json()['root_task_id'])
    assert org.db.execute('SELECT state FROM workflow_draft_dispatch_intents').fetchone()[0] == 'completed'
    assert backend.calls['launch'] == backend.calls['finish'] == 1
    root = org.root
    assert org.sessions.iter_active() == []
    # Take the baseline only after the actual committed
    # callback is checkpointed and every fixture oracle has released its reader.
    assert org.db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] == 0
    client.close(); org.close()
    assert not (root / 'happyranch.db-wal').exists()
    assert not (root / 'happyranch.db-shm').exists()
    s2 = _extract_s2_source(_PRECEDING_FIXTURES, tmp_path / 's2-source')
    driver = _PRECEDING_IMPORT_CHECK + """
from runtime.daemon.org_state import OrgState
from runtime.config import Settings
org=OrgState.load(slug='alpha',root=Path(sys.argv[2]),settings=Settings())
org.close()
assert_pinned_imports()
print('pinned-reader-reopened')
"""
    def snapshot():
        files = _g_closed_compatible_file_identity(root)
        database = root / 'happyranch.db'
        # Physical validators/refusals retain the ENTIRE database bytes too.
        files['happyranch.db'] = ('file', database.stat().st_mode & 0o7777,
                                 database.read_bytes())
        return files
    # Validator-only is the physical no-write boundary. Use an ordinary
    # writable connection and the authentic pinned module, before ANY reopen.
    # A read-only SQLite connection must not conceal an attempted write.
    validator_driver = _PRECEDING_IMPORT_CHECK + """
import sqlite3
from contextlib import closing
from runtime.infrastructure.workflow_schema import validate_workflow_schema
with closing(sqlite3.connect(Path(sys.argv[2]) / 'happyranch.db')) as conn:
    assert validate_workflow_schema(conn, expected_org_slug='alpha') == 'E'
assert_pinned_imports()
print('pinned-validator-only')
"""
    before = snapshot()
    for _ in range(2):
        validated = subprocess.run([sys.executable, '-c', validator_driver, str(s2), str(root)], capture_output=True, text=True, timeout=15)
        assert validated.returncode == 0 and 'pinned-validator-only' in validated.stdout, validated.stderr
        assert snapshot() == before
    e_durable_before = _g_closed_database_identity(root)
    e_files_before = _g_closed_compatible_file_identity(root)
    for _ in range(2):
        result = subprocess.run([sys.executable, '-c', driver, str(s2), str(root)], capture_output=True, text=True, timeout=15)
        assert result.returncode == 0 and 'pinned-reader-reopened' in result.stdout, result.stderr
        assert _g_closed_database_identity(root) == e_durable_before
        assert _g_closed_compatible_file_identity(root) == e_files_before
    if origin == 'migration':
        import os
        migrated = run_submission(root.parent.parent, dict(os.environ))
        assert migrated.returncode == 0 and 'migrated:' in migrated.stdout, migrated.stderr
    else:
        runtime = RuntimeDir.init(tmp_path / 'fresh-runtime')
        fresh_state = DaemonState.from_runtime(runtime, Settings())
        fresh_client = TestClient(create_app(fresh_state), headers={'Authorization': f'Bearer {paths.ensure_token()}'})
        try:
            response = fresh_client.post('/api/v1/orgs', json={'slug': 'alpha'})
            assert response.status_code == 200, response.text
            root = fresh_state.orgs['alpha'].root
        finally:
            fresh_client.close()
            for owner in fresh_state.orgs.values(): owner.close()
    before = snapshot()
    for source in (preceding_source, s2):
        for _ in range(2):
            refused = subprocess.run([sys.executable, '-c', driver, str(source), str(root)], capture_output=True, text=True, timeout=15)
            assert refused.returncode != 0 and 'workflow_schema_object_set_mismatch' in refused.stderr, refused.stderr
            assert snapshot() == before
    from contextlib import closing
    for _ in range(2):
        with closing(sqlite3.connect(root / 'happyranch.db')) as validator:
            assert schema.validate_workflow_schema(validator, expected_org_slug='alpha') == 'G'
        assert snapshot() == before
    g_durable_before = _g_closed_database_identity(root)
    g_files_before = _g_closed_compatible_file_identity(root)
    for _ in range(2):
        current = OrgState.load(slug='alpha', root=root, settings=Settings())
        try:
            assert schema.validate_workflow_schema(current.db._conn, expected_org_slug='alpha') == 'G'
        finally:
            current.close()
        assert _g_closed_database_identity(root) == g_durable_before
        assert _g_closed_compatible_file_identity(root) == g_files_before
    # Independent keeper for the SAME full-object mismatch/no-write boundary
    # used by the pinned readers. A real current validator refuses one extra
    # object on a separately owned complete-G copy, on a writable connection.
    # This allows causal write/refusal controls without altering either archive.
    import shutil
    root = shutil.copytree(root, tmp_path / 'g-validator-refusal')
    with closing(sqlite3.connect(root / 'happyranch.db')) as writer:
        writer.execute('CREATE INDEX workflow_g_refusal_probe ON workflow_submission_operations(operation_key)')
        writer.commit()
    before = snapshot()
    for _ in range(2):
        with closing(sqlite3.connect(root / 'happyranch.db')) as validator:
            assert validator.execute(
                "SELECT type,tbl_name FROM sqlite_schema WHERE name='workflow_g_refusal_probe'"
            ).fetchone() == ('index', 'workflow_submission_operations')
            with pytest.raises(ValueError, match='workflow_schema_object_set_mismatch'):
                schema.validate_workflow_schema(validator, expected_org_slug='alpha')
        assert snapshot() == before
