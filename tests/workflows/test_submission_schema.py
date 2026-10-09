"""G migration storage contracts; seeded graphs are not U3 execution."""
from __future__ import annotations

import sqlite3
import hashlib
import json
from pathlib import Path

import pytest

from runtime.infrastructure.database import Database
from runtime.infrastructure import workflow_schema as schema
from tests.workflows.test_draft_dispatch import draft_host
from tests.daemon.test_workflow_activation_routes import activation_org


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _legacy_graph(db: Database, layout: str, *, joined: bool = False) -> None:
    """Explicit seeded archival subjects; never claimed as a U3 producer."""
    from runtime.models import TaskRecord
    from runtime.workflows.templates import WorkflowTemplatePrincipal, WorkflowTemplateStore
    from tests.workflows.test_template_store import VALID_DEFINITION
    schema.install_or_recover(db, expected_org_slug='alpha')
    if layout == 'E':
        with db.workflow_schema_transaction() as conn:
            schema.migrate_draft_schema(conn, expected_org_slug='alpha')
    db.insert_task(TaskRecord(id='TASK-001', brief='retained archival owner', assigned_agent='maker', team='engineering'))
    principal = WorkflowTemplatePrincipal.founder(org_slug='alpha', team_slug='engineering', revalidate=lambda: None)
    version = WorkflowTemplateStore(db).publish_version(org_slug='alpha', principal=principal,
        namespace='org/alpha/team/engineering', operation_key='publish', template_name='product-design',
        definition=VALID_DEFINITION, expected_current_version=0)
    raw = _json({})
    stamp = '2026-10-05T00:00:00+00:00'
    db.execute('INSERT INTO workflow_authorization_revisions VALUES (?,?,?,?,?,?,?)',
               ('auth', 'org/alpha/team/engineering', 1, raw, _digest(raw), 'source', stamp))
    db.execute('INSERT INTO workflow_binding_snapshots VALUES (?,?,?,?,?,?)',
               ('binding', version.version_id, 'auth', raw, _digest(raw), stamp))
    db.execute('INSERT INTO workflow_contexts VALUES (?,?,?,?,?,?)',
               ('context', 'binding', raw, _digest(raw), 'task', 'TASK-001'))
    db.execute('INSERT INTO workflow_instances VALUES (?,?,?,?,?,?)',
               ('instance', 'binding', 'context', 'TASK-001', 'founder', 'reviewing'))
    for revision in (4, 9):
        body = f'原始 PRD revision {revision}\nretain exact UTF8 bytes'.encode()
        db.execute('INSERT INTO workflow_submissions VALUES (?,?,?,?,?,?,?,?,?,?)',
                   (f'sub-{revision}', 'instance', revision, body, _digest(body), None,
                    'TASK-001', 'historical-session', f'legacy-result-{revision}', 'maker'))
        db.execute('INSERT INTO workflow_rounds VALUES (?,?,?,?,?)',
                   (f'round-{revision}', 'instance', f'sub-{revision}', revision, 'reviewing'))
    db.execute('INSERT INTO workflow_submission_contributors VALUES (?,?,?,?,?,?)',
               ('sub-4', 'maker', 'TASK-001', 'historical-session', 'legacy-result-4', 'author'))
    db.execute('INSERT INTO workflow_instance_contributors VALUES (?,?,?,?,?,?)',
               ('instance', 'prior-author', 'TASK-000', 'old-session', 'old-result', 'author'))
    for i in range(3):
        proof = _json({'reviewer': i})
        db.execute('INSERT INTO workflow_review_requests VALUES (?,?,?,?,?,?,?,?)',
                   (f'q{i}', 'round-4', f'reviewer-{i}', 1, proof, _digest(proof), 'approved', None))
        db.execute('INSERT INTO workflow_review_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                   (f'r{i}', f'q{i}', 'sub-4', _digest('原始 PRD revision 4\nretain exact UTF8 bytes'.encode()),
                    1, _digest(proof), proof, _digest(proof), 'approved', None, stamp))
    for kind in (('submitted', 'joined') if joined else ('submitted',)):
        event = _json({'instance_id': 'instance', 'round_id': 'round-4', 'submission_id': 'sub-4',
                       'revision': 4, 'event_kind': kind})
        db.execute('INSERT INTO workflow_events VALUES (?,?,?,?,?,?)',
                   (f'event-{kind}', 'instance', kind, event, _digest(event), stamp))
        db.execute('INSERT INTO workflow_operation_replays VALUES (?,?,?,?,?,?)',
                   ('alpha', 'maker', kind, _digest(event), 'instance', f'event-{kind}'))
    db._conn.commit()


def _rows(conn: sqlite3.Connection) -> dict:
    return {name: tuple(tuple(r) for r in conn.execute(f'SELECT rowid,* FROM "{name}" ORDER BY rowid'))
            for (name,) in conn.execute("SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name")}


def test_g_reviewed_definitions_and_independent_complete_layouts(tmp_path: Path) -> None:
    """Fresh creation must install G while the original F/E references remain."""
    db = Database(tmp_path / 'happyranch.db')
    try:
        schema.initialize_complete_org_schema(db, expected_org_slug='alpha')
        assert schema.validate_workflow_schema(db._conn, expected_org_slug='alpha') == 'G'
        from tests.daemon.test_workflow_activation_routes import _assert_activation_org_layout
        _assert_activation_org_layout(db.path, 'G', 'reviewed full fresh G')
        with sqlite3.connect(db.path) as observer:
            names = {r[0] for r in observer.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name LIKE 'workflow_%'")}
            assert len(names) == 50
            assert observer.execute('SELECT version FROM workflow_submission_schema_versions').fetchall() == [(1,)]
            assert observer.execute('PRAGMA foreign_key_check').fetchall() == []
    finally:
        db.close()


@pytest.mark.parametrize('damage', ['missing-index', 'missing-result-index', 'missing-table', 'missing-marker',
    'unsupported-marker', 'missing-e-marker', 'missing-f-marker', 'wrong-history', 'mixed-definitions',
    'missing-unique', 'wrong-type', 'wrong-nullability', 'wrong-check', 'wrong-index-order',
    'extra-object', 'extra-index', 'extra-view', 'extra-trigger', 'unrelated-object'])
def test_g_partial_mixed_metadata_and_unknown_objects_refuse_without_writes(tmp_path: Path, damage: str) -> None:
    db = Database(tmp_path / 'happyranch.db')
    try:
        schema.initialize_complete_org_schema(db, expected_org_slug='alpha')
        assert schema.validate_workflow_schema(db._conn, expected_org_slug='alpha') == 'G'
        statements = {
            'missing-index': ['DROP INDEX workflow_submission_operations_source_idx'],
            'missing-result-index': ['DROP INDEX workflow_submission_result_links_result_idx'],
            'missing-table': ['DROP TABLE workflow_submission_result_links'],
            'missing-marker': ['DELETE FROM workflow_submission_schema_versions'],
            'unsupported-marker': ['PRAGMA ignore_check_constraints=ON', 'UPDATE workflow_submission_schema_versions SET version=2'],
            'missing-e-marker': ['DELETE FROM workflow_draft_adapter_versions'],
            'missing-f-marker': ['DELETE FROM workflow_adapter_versions'],
            'wrong-history': ["UPDATE workflow_cutover_events SET event_digest='forged'"],
            'wrong-index-order': ['DROP INDEX workflow_submission_operations_source_idx',
                'CREATE INDEX workflow_submission_operations_source_idx ON workflow_submission_operations(source_task_id,instance_id,source_session_id)'],
            'extra-object': ['CREATE TABLE workflow_unapproved (id INTEGER)'],
            'extra-index': ['CREATE INDEX workflow_unapproved ON workflow_submission_operations(principal)'],
            'extra-view': ['CREATE VIEW workflow_unapproved AS SELECT * FROM workflow_submission_operations'],
            'extra-trigger': ['CREATE TRIGGER workflow_unapproved AFTER INSERT ON workflow_submission_operations BEGIN SELECT 1; END'],
            'unrelated-object': ['CREATE TABLE unrelated_drift (id INTEGER)'],
        }
        if damage in ('mixed-definitions', 'missing-unique', 'wrong-type', 'wrong-nullability', 'wrong-check'):
            sql = db.execute("SELECT sql FROM sqlite_schema WHERE name='workflow_submissions'").fetchone()[0]
            if damage == 'mixed-definitions': sql = sql.replace('source_result_id TEXT,', 'source_result_id TEXT NOT NULL,')
            elif damage == 'missing-unique': sql = sql.replace(', UNIQUE(id,submission_digest)', '')
            elif damage == 'wrong-type': sql = sql.replace('revision INTEGER', 'revision TEXT')
            elif damage == 'wrong-nullability': sql = sql.replace('source_session_id TEXT NOT NULL', 'source_session_id TEXT')
            else: sql = sql.replace('CHECK(revision>0)', 'CHECK(revision>=0)')
            statements[damage] = ['DROP TABLE workflow_submissions', sql]
        for sql in statements[damage]: db._conn.execute(sql)
        db._conn.commit()
        before = (_rows(db._conn), tuple(tuple(r) for r in db.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY name')))
        if damage == 'unrelated-object':
            assert schema.validate_workflow_schema(db._conn, expected_org_slug='alpha') == 'G'
            with pytest.raises(ValueError, match='workflow_submission_whole_database_mismatch'):
                schema._validate_release_database(db._conn, 'G')
        else:
            with pytest.raises(ValueError, match='workflow_.*(mismatch|corrupt)'):
                schema.validate_workflow_schema(db._conn, expected_org_slug='alpha')
        assert (_rows(db._conn), tuple(tuple(r) for r in db.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY name'))) == before
    finally:
        db.close()


@pytest.mark.parametrize('layout', ['F', 'E'])
@pytest.mark.parametrize('joined', [False, True], ids=['submitted', 'submitted-and-joined'])
def test_g_rebuild_preserves_populated_legacy_relations_and_exact_values(tmp_path: Path, layout: str, joined: bool) -> None:
    path = tmp_path / 'happyranch.db'
    db = Database(path)
    try:
        _legacy_graph(db, layout, joined=joined)
        before = _rows(db._conn)
        original_children = tuple(db.execute("SELECT name,sql FROM sqlite_schema WHERE type='table' AND name IN "
                                            "('workflow_review_receipts','workflow_rounds','workflow_operation_replays') ORDER BY name"))
    finally:
        db.close()
    with sqlite3.connect(path) as writer:
        writer.execute('PRAGMA foreign_keys=ON')
        assert schema.migrate_submission_schema(writer, expected_org_slug='alpha') == 'migrated'
        assert writer.execute('PRAGMA foreign_keys').fetchone() == (1,)
    with sqlite3.connect(path) as observer:
        after = _rows(observer)
        for table, records in before.items():
            rows = after[table]
            if table == 'workflow_events':
                rows = tuple(row[:3] + row[4:] for row in rows)
            assert rows == records, table
        assert tuple(observer.execute("SELECT name,sql FROM sqlite_schema WHERE type='table' AND name IN "
                                      "('workflow_review_receipts','workflow_rounds','workflow_operation_replays') ORDER BY name")) == tuple(tuple(r) for r in original_children)
        assert {r[0] for r in observer.execute('SELECT revision FROM workflow_events')} == {4}
        assert observer.execute('PRAGMA foreign_key_check').fetchall() == []
        assert observer.execute('SELECT COUNT(*) FROM workflow_submission_operations').fetchone() == (0,)


@pytest.mark.parametrize('damage', ['ambiguous', 'opaque', 'digest', 'cross-instance-replay', 'missing-round', 'wrong-event-kind', 'wrong-submission-digest', 'wrong-receipt-digest'])
def test_g_legacy_event_mapping_refuses_ambiguity_without_history_repair(tmp_path: Path, damage: str) -> None:
    db = Database(tmp_path / 'happyranch.db')
    try:
        _legacy_graph(db, 'E')
        if damage == 'ambiguous':
            raw = b'opaque-original-F2-operation'
            db.execute("UPDATE workflow_events SET event_kind='joined',event_bytes=?,event_digest=?",
                       (raw, _digest(b'joined' + raw)))
            db.execute('UPDATE workflow_operation_replays SET request_digest=?', (_digest(raw),))
            for i in range(3):
                proof = _json({'other-reviewer': i})
                db.execute('INSERT INTO workflow_review_requests VALUES (?,?,?,?,?,?,?,?)',
                           (f'other-q{i}', 'round-9', f'reviewer-{i}', 1, proof, _digest(proof), 'approved', None))
                digest = db.execute("SELECT submission_digest FROM workflow_submissions WHERE id='sub-9'").fetchone()[0]
                db.execute('INSERT INTO workflow_review_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                           (f'other-r{i}', f'other-q{i}', 'sub-9', digest, 1, _digest(proof), proof,
                            _digest(proof), 'approved', None, '2026-10-05T00:00:00+00:00'))
        elif damage == 'opaque':
            raw = b'unsupported opaque source'
            db.execute('UPDATE workflow_events SET event_bytes=?,event_digest=?', (raw, _digest(raw)))
            db.execute('UPDATE workflow_operation_replays SET request_digest=?', (_digest(raw),))
        elif damage in ('wrong-event-kind', 'wrong-submission-digest'):
            payload = json.loads(db.execute('SELECT event_bytes FROM workflow_events').fetchone()[0])
            if damage == 'wrong-event-kind': payload['event_kind'] = 'joined'
            else: payload['submission_digest'] = 'forged'
            raw = _json(payload)
            db.execute('UPDATE workflow_events SET event_bytes=?,event_digest=?', (raw, _digest(raw)))
            db.execute('UPDATE workflow_operation_replays SET request_digest=?', (_digest(raw),))
        elif damage == 'wrong-receipt-digest':
            db.execute("UPDATE workflow_review_receipts SET proof_digest='forged' WHERE id='r0'")
        elif damage == 'digest':
            db.execute("UPDATE workflow_events SET event_digest='forged'")
        elif damage == 'cross-instance-replay':
            db.execute("UPDATE workflow_operation_replays SET request_digest='cross-owner'")
        else:
            db.execute("DELETE FROM workflow_review_receipts")
            db.execute("DELETE FROM workflow_review_requests")
            db.execute("DELETE FROM workflow_rounds WHERE id='round-4'")
        db._conn.commit()
        before = tuple(db._conn.iterdump())
    finally:
        db.close()
    with sqlite3.connect(tmp_path / 'happyranch.db') as writer:
        writer.execute('PRAGMA foreign_keys=ON')
        with pytest.raises(ValueError, match='workflow_legacy_event_unmappable'):
            schema.migrate_submission_schema(writer, expected_org_slug='alpha')
        assert tuple(writer.iterdump()) == before
        assert schema.validate_workflow_schema(writer, expected_org_slug='alpha') == 'E'


def test_g_revision_uniqueness_and_unsalted_raw_byte_reuse(tmp_path: Path) -> None:
    db = Database(tmp_path / 'happyranch.db')
    try:
        _legacy_graph(db, 'E')
    finally:
        db.close()
    with sqlite3.connect(tmp_path / 'happyranch.db') as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        schema.migrate_submission_schema(conn, expected_org_slug='alpha')
        original = conn.execute("SELECT * FROM workflow_submissions WHERE id='sub-4'").fetchone()
        conn.execute('INSERT INTO workflow_submissions VALUES (?,?,?,?,?,?,?,?,?,?)', ('sub-10', original[1], 10, *original[3:]))
        assert conn.execute('SELECT COUNT(*) FROM workflow_submissions WHERE submission_digest=?', (original[4],)).fetchone() == (2,)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute('INSERT INTO workflow_submissions VALUES (?,?,?,?,?,?,?,?,?,?)', ('duplicate', *original[1:]))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO workflow_events SELECT 'duplicate',instance_id,revision,event_kind,event_bytes,'new-digest',created_at FROM workflow_events LIMIT 1")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO workflow_events VALUES ('invalid','instance',0,'submitted',X'01','d','stamp')")


def _seed_active_submission(db: Database, *, submission_id='active-1', revision=1, intent_id=None) -> dict:
    """Validator-only G operation; the admitted source is retained verbatim."""
    if intent_id is None:
        intent_id = db.execute('SELECT id FROM workflow_draft_dispatch_intents').fetchone()[0]
    intent = dict(db.execute('SELECT * FROM workflow_draft_dispatch_intents WHERE id=?', (intent_id,)).fetchone())
    raw = ('immutable active document ' + submission_id).encode()
    submission = dict(id=submission_id, instance_id=intent['instance_id'], revision=revision,
                      submission_bytes=raw, submission_digest=_digest(raw), storage_ref=None,
                      source_task_id=intent['task_id'], source_session_id=intent['session_id'],
                      source_result_id=None, author_principal=intent['assigned_principal'])
    request = _json(dict(submission_id=submission_id, instance_id=intent['instance_id'],
                         submission_digest=submission['submission_digest'], revision=revision))
    operation = dict(submission_id=submission_id, instance_id=intent['instance_id'], org_slug='alpha',
                     principal=intent['assigned_principal'], source_task_id=intent['task_id'],
                     source_session_id=intent['session_id'], operation_key='submit-' + submission_id,
                     request_digest=_digest(request), created_at='2026-10-06T00:00:00+00:00')
    proof = _json(dict(format='workflow-submission-operation@1', operation=operation,
                       submission={k: v.hex() if isinstance(v, bytes) else v for k, v in submission.items()},
                       source_intent={k: v.hex() if isinstance(v, bytes) else v for k, v in intent.items()
                                      if k not in schema._DRAFT_MUTABLE}, request_bytes=request.hex()))
    db.execute('INSERT INTO workflow_submissions (' + ','.join(submission) + ') VALUES (' +
               ','.join('?' for _ in submission) + ')', tuple(submission.values()))
    operation.update(provenance_bytes=proof, provenance_digest=_digest(proof))
    db.execute('INSERT INTO workflow_submission_operations (' + ','.join(operation) + ') VALUES (' +
               ','.join('?' for _ in operation) + ')', tuple(operation.values()))
    db._conn.commit()
    return operation


def _seed_active_source(db: Database, *, callback=False) -> str:
    """S1 authenticated SQL graph; not an execution/host receipt."""
    from tests.workflows.test_draft_schema import _seed_valid_draft, _seed_event, _projection
    intent = _seed_valid_draft(db)
    _seed_event(db, intent, 'claimed', before=_projection(db, intent), state='claimed',
                claim_token='claim', claim_owner='owner')
    _seed_event(db, intent, 'launch_reserved', before=_projection(db, intent), host_launch_started=1)
    _seed_event(db, intent, 'running', before=_projection(db, intent), state='running',
                host_execution_id='host', session_id='session')
    if callback:
        cursor = db.execute("INSERT INTO task_results(task_id,agent,session_id,status,created_at) "
                            "VALUES ('TASK-001','maker','session','failed','2026-10-06T00:00:00+00:00')")
        result = dict(db.execute('SELECT * FROM task_results WHERE id=?', (cursor.lastrowid,)).fetchone())
        _seed_event(db, intent, 'callback_recorded', before=_projection(db, intent),
                    final_result_id=result['id'], result=result)
    db._conn.commit()
    return intent


def _seed_result_link(db: Database, operation: dict) -> dict:
    intent = dict(db.execute('SELECT * FROM workflow_draft_dispatch_intents WHERE task_id=? AND session_id=?',
                            (operation['source_task_id'], operation['source_session_id'])).fetchone())
    result = dict(db.execute('SELECT * FROM task_results WHERE id=?', (intent['final_result_id'],)).fetchone())
    raw = _json(result)
    db.execute('INSERT INTO workflow_submission_result_links VALUES (?,?,?,?,?,?,?)',
               (operation['submission_id'], operation['source_task_id'], operation['source_session_id'],
                result['id'], raw, _digest(raw), '2026-10-06T00:00:00+00:00'))
    db._conn.commit()
    return result


def test_g_pending_operation_and_legacy_result_meanings(tmp_path: Path) -> None:
    db = Database(tmp_path / 'happyranch.db')
    try:
        schema.initialize_complete_org_schema(db, expected_org_slug='alpha')
        intent = _seed_active_source(db)
        operation = _seed_active_submission(db, intent_id=intent)
        before = _rows(db._conn)
        for _ in range(2):
            assert schema.validate_workflow_schema(db._conn, expected_org_slug='alpha') == 'G'
            assert _rows(db._conn) == before
        assert db.execute('SELECT source_result_id FROM workflow_submissions').fetchone()[0] is None
        assert not db.execute('SELECT 1 FROM workflow_submission_result_links').fetchone()
        assert not db.execute('SELECT 1 FROM task_results').fetchone()
        assert operation['principal'] == 'maker'
    finally:
        db.close()


@pytest.mark.parametrize('result_status', ['completed', 'failed'])
def test_g_exact_result_links_preserve_failed_and_shared_callback_evidence(draft_host, monkeypatch, result_status) -> None:
    """Actual S2 callback plus seeded validator links, never a U3 producer."""
    from tests.daemon.test_workflow_activation_routes import BASE
    client, org, state, body, controls, observations, backend = draft_host
    original_post = client.post
    def post(url, **kwargs):
        if url.endswith('/completion'):
            kwargs['json'] = {**kwargs['json'], 'status': result_status}
        return original_post(url, **kwargs)
    monkeypatch.setattr(client, 'post', post)
    receipt = client.post(BASE, json=body)
    assert receipt.status_code == 201, receipt.text
    org.orchestrator.run_step(receipt.json()['root_task_id'])
    intent = dict(org.db.execute('SELECT * FROM workflow_draft_dispatch_intents').fetchone())
    assert intent['state'] == result_status and type(intent['final_result_id']) is int
    assert backend.calls['launch'] == backend.calls['finish'] == 1
    operations = [_seed_active_submission(org.db, submission_id='active-' + str(i), revision=i, intent_id=intent['id'])
                  for i in (1, 2)]
    source = [_seed_result_link(org.db, op) for op in operations]
    assert source[0] == source[1] and source[0]['status'] == result_status
    before = _rows(org.db._conn)
    for _ in range(2):
        assert schema.validate_workflow_schema(org.db._conn, expected_org_slug='alpha') == 'G'
        assert _rows(org.db._conn) == before
    with sqlite3.connect(org.db.path) as reader:
        assert reader.execute('SELECT source_result_id FROM workflow_submissions').fetchall() == [(None,), (None,)]
        assert reader.execute('SELECT task_result_id FROM workflow_submission_result_links').fetchall() == [(intent['final_result_id'],)] * 2
        for raw, digest in reader.execute('SELECT result_bytes,result_digest FROM workflow_submission_result_links'):
            assert raw == _json(source[0]) and digest == _digest(_json(source[0]))
    assert not org.db.execute('SELECT 1 FROM workflow_task_results').fetchone()


@pytest.mark.parametrize('damage', ['missing-operation', 'wrong-org', 'wrong-principal', 'wrong-session',
    'empty-key', 'coherent-request-revision', 'coherent-provenance-revision', 'late-legacy-result', 'newer-result', 'summary-only', 'boolean-result-id'])
def test_g_operation_result_cross_row_corruption_refuses_without_writes(tmp_path: Path, damage: str) -> None:
    db = Database(tmp_path / 'happyranch.db')
    try:
        schema.initialize_complete_org_schema(db, expected_org_slug='alpha')
        intent = _seed_active_source(db, callback=True)
        operation = _seed_active_submission(db, intent_id=intent)
        result = _seed_result_link(db, operation)
        assert schema.validate_workflow_schema(db._conn, expected_org_slug='alpha') == 'G'
        if damage == 'missing-operation':
            db.execute('DELETE FROM workflow_submission_result_links')
            db.execute('DELETE FROM workflow_submission_operations')
        elif damage in ('wrong-org', 'wrong-principal', 'wrong-session', 'empty-key'):
            column, value = {'wrong-org': ('org_slug', 'other'), 'wrong-principal': ('principal', 'other'),
                             'wrong-session': ('source_session_id', 'other'), 'empty-key': ('operation_key', '')}[damage]
            if damage == 'wrong-session':
                db.execute('DELETE FROM workflow_submission_result_links')
            db.execute(f'UPDATE workflow_submission_operations SET {column}=?', (value,))
        elif damage == 'coherent-provenance-revision':
            proof = json.loads(operation['provenance_bytes'])
            proof['submission']['revision'] = True
            raw = _json(proof)
            db.execute('UPDATE workflow_submission_operations SET provenance_bytes=?,provenance_digest=?',
                       (raw, _digest(raw)))
        elif damage == 'coherent-request-revision':
            op = dict(db.execute('SELECT * FROM workflow_submission_operations').fetchone())
            proof = json.loads(op['provenance_bytes'])
            request = json.loads(bytes.fromhex(proof['request_bytes']))
            request['revision'] = True
            request = _json(request)
            proof['request_bytes'] = request.hex()
            proof['operation']['request_digest'] = _digest(request)
            raw = _json(proof)
            db.execute('UPDATE workflow_submission_operations SET request_digest=?,provenance_bytes=?,provenance_digest=?',
                       (_digest(request), raw, _digest(raw)))
        elif damage == 'late-legacy-result':
            db.execute('UPDATE workflow_submissions SET source_result_id=?', (str(result['id']),))
        elif damage == 'newer-result':
            cursor = db.execute("INSERT INTO task_results(task_id,agent,session_id,status,created_at) "
                                "VALUES ('TASK-001','maker','session','completed','2026-10-07T00:00:00+00:00')")
            newer = dict(db.execute('SELECT * FROM task_results WHERE id=?', (cursor.lastrowid,)).fetchone())
            raw = _json(newer)
            db.execute('UPDATE workflow_submission_result_links SET task_result_id=?,result_bytes=?,result_digest=?',
                       (newer['id'], raw, _digest(raw)))
        else:
            payload = {'output_summary': result['output_summary']} if damage == 'summary-only' else {**result, 'id': True}
            raw = _json(payload)
            db.execute('UPDATE workflow_submission_result_links SET result_bytes=?,result_digest=?', (raw, _digest(raw)))
        db._conn.commit()
        before = tuple(db._conn.iterdump())
        with pytest.raises(ValueError, match='workflow_submission_data_corrupt'):
            schema.validate_workflow_schema(db._conn, expected_org_slug='alpha')
        assert tuple(db._conn.iterdump()) == before
    finally:
        db.close()
