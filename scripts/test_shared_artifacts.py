"""Slice A private ASGI/SQLite/filesystem exercises; no lifespan or live org.

Run: uv run --frozen python scripts/test_shared_artifacts.py [--case metadata]
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from types import SimpleNamespace
import json
import sqlite3
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from runtime.infrastructure.artifact_store import ArtifactStore
from runtime.infrastructure.database import Database
from runtime.infrastructure.shared_artifact_service import QUOTA_BYTES, SharedArtifactError, SharedArtifactService
from runtime.models import ThreadRecord, TaskRecord, TaskStatus
from scripts.test_agent_retirement import PREFIX, fixture

NOW = datetime(2026,11,23,tzinfo=timezone.utc)


def snapshot(org: object) -> tuple:
    tables = ('shared_artifacts','shared_artifact_generations','shared_artifact_operations')
    rows = tuple(tuple(tuple(r) for r in org.db._conn.execute(f'SELECT * FROM {t} ORDER BY 1,2')) for t in tables)
    root = org.root/'artifacts'
    blobs = tuple((str(p.relative_to(root)),p.read_bytes()) for p in sorted(root.rglob('*')) if p.is_file())
    return rows,blobs


async def metadata_case(root: Path) -> None:
    async with fixture(root) as (org,client):
        org.db.insert_thread(ThreadRecord(id='THR-origin',subject='Synthetic origin'))
        valid = dict(agent='worker',thread_id='THR-origin',purpose='Persistent reference',retention='default')
        variants = [dict(valid,purpose=None),dict(valid,agent=''),dict(valid,thread_id=None),
                    dict(valid,task_id='TASK-foreign'),dict(valid,thread_id='THR-foreign'),
                    dict(valid,agent='missing'),dict(valid,retention='forever'),
                    dict(valid,retention='reference',reason=' '),
                    dict(valid,expires_at='2027-05-23T00:00:00Z'),
                    dict(valid,expires_at='2026-12-01'),dict(valid,expires_at='bad')]
        for index,values in enumerate(variants):
            before = snapshot(org)
            response = await client.post(PREFIX+'/artifacts',params={k:v for k,v in values.items() if v is not None},
                                         files={'file':('invalid.bin',b'synthetic')})
            assert response.status_code == 422,(index,response.status_code,response.text)
            assert response.json()['detail']['code'] in ('artifact_metadata_required','artifact_metadata_invalid')
            assert snapshot(org) == before,index
        before = snapshot(org)
        cli_omission = await client.post(PREFIX+'/artifacts',headers={'X-HappyRanch-Surface':'cli'},params={'agent':'founder'},files={'file':('cli.bin',b'CLI')})
        assert cli_omission.status_code == 422,cli_omission.text
        assert snapshot(org) == before
        missing_file = await client.post(PREFIX+'/artifacts',params=valid)
        assert missing_file.status_code == 422,missing_file.text
        assert missing_file.json()['detail']['code'] == 'artifact_metadata_invalid',missing_file.text
        bad_model = await client.post(PREFIX+'/artifacts/missing/renew',json={'agent':None})
        assert bad_model.status_code == 422,bad_model.text
        assert bad_model.json()['detail']['code'] == 'artifact_metadata_invalid',bad_model.text
        for retention,days in [('default',90),('reference',180)]:
            response = await client.post(PREFIX+'/artifacts',params={**valid,'name':retention+'.bin','retention':retention,'reason':'design reference'},files={'file':('source.bin',b'source')})
            assert response.status_code == 200,response.text
            assert datetime.fromisoformat(response.json()['expires_at']) == NOW+timedelta(days=days)
        print('PASS metadata: 11 invalid cases leave zero bytes/rows; exact90/180 grants')


async def provisional_case(root: Path) -> None:
    clock = [NOW]
    with patch('runtime.infrastructure.shared_artifact_service._utc',side_effect=lambda:clock[0]):
        async with fixture(root) as (org,client):
            put = await client.post(PREFIX+'/artifacts',params={'agent':'founder','name':'browser.bin'},files={'file':('browser.bin',b'founder')})
            assert put.status_code == 200,put.text
            pending = put.json()
            assert (pending['state'],pending['owner_kind'],pending['owner'],pending['owner_agent_id']) == ('pending-attribution','human','founder','engineering_manager')
            assert pending['origin_thread_id'] is None and pending['expires_at'] is None
            clock[0] += timedelta(hours=24)
            downloaded = await client.get(PREFIX+'/artifacts/browser.bin')
            assert downloaded.status_code == 200,downloaded.text
            row = org.db.get_shared_artifact('browser.bin')
            assert datetime.fromisoformat(row['expires_at']) == NOW+timedelta(days=90)
            assert org.db._conn.execute("SELECT COUNT(*) FROM shared_artifact_notice_receipts WHERE notice_kind='attribution_timeout'").fetchone()[0] == 1
            compose = await client.post(PREFIX+'/threads',json={'subject':'Private binding','recipients':['worker'],'body_markdown':'hello','attachments':[{'artifact_name':'browser.bin'}]})
            assert compose.status_code == 200,compose.text
            tid = compose.json()['thread_id']
            row = org.db.get_shared_artifact('browser.bin')
            assert row['origin_thread_id'] == tid and row['purpose'] == 'thread attachment'
            assert row['owner_agent_id'] == 'manager',row
            expiry = row['expires_at']
            clock[0] += timedelta(days=1)
            compose2 = await client.post(PREFIX+'/threads',json={'subject':'Another link','recipients':['worker'],'body_markdown':'hello','attachments':[{'artifact_name':'browser.bin'}]})
            assert compose2.status_code == 200,compose2.text
            assert org.db.get_shared_artifact('browser.bin')['expires_at'] == expiry
            detail = await client.get(PREFIX+'/threads/'+tid)
            attachment = detail.json()['messages'][0]['attachments'][0]
            assert attachment['generation'] == 1 and attachment['lifecycle']['owner'] == 'founder'
            # Pre-ID CLI multipart path derives the real thread origin inside its transaction.
            response = await client.post(PREFIX+'/threads',params={'shared_uploads':'true'},
                data={'body':json.dumps({'subject':'Multipart','recipients':['worker'],'body_markdown':'hello'})},files=[('files',('shared.bin',b'multipart'))])
            assert response.status_code == 200,response.text
            tid2 = response.json()['thread_id']
            row = org.db._conn.execute("SELECT * FROM shared_artifact_generations WHERE origin_thread_id=?",(tid2,)).fetchone()
            assert row is not None,'canonical source must have a generation row'
            assert row['owner_kind'] == 'human' and row['owner_agent_id'] == 'manager'
            # Binding audit and final COMMIT failure roll back the entire new thread.
            await client.post(PREFIX+'/artifacts',params={'agent':'founder','name':'rollback.bin'},files={'file':('rollback.bin',b'rollback')})
            def thread_rows() -> tuple:
                return tuple(org.db._conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                             for table in ('threads','thread_participants','thread_messages','shared_artifact_thread_links'))
            counts = thread_rows()
            from runtime.infrastructure.audit_logger import AuditLogger
            original = AuditLogger.log_artifact_transition
            def fail_binding(logger: object, **kwargs: object) -> None:
                if kwargs['action'] == 'artifact_attribution_bound':
                    raise RuntimeError('synthetic_binding_audit_failure')
                original(logger,**kwargs)
            body = {'subject':'Rollback','recipients':['worker'],'body_markdown':'hello','attachments':[{'artifact_name':'rollback.bin'}]}
            with patch.object(AuditLogger,'log_artifact_transition',fail_binding):
                try:
                    await client.post(PREFIX+'/threads',json=body)
                    assert False
                except RuntimeError:
                    pass
            assert thread_rows() == counts
            assert org.db.get_shared_artifact('rollback.bin')['origin_thread_id'] is None
            raw = org.db._conn
            org.db._conn = CommitFailure(raw,2)
            try:
                await client.post(PREFIX+'/threads',json=body)
                assert False
            except sqlite3.OperationalError:
                pass
            finally:
                org.db._conn = raw
            assert thread_rows() == counts
            assert org.db.get_shared_artifact('rollback.bin')['state'] == 'pending-attribution'
            before = snapshot(org)
            org.db._conn = CommitFailure(raw,1)
            try:
                await client.post(PREFIX+'/threads',params={'shared_uploads':'true'},data={'body':json.dumps({'subject':'Compensate','recipients':['worker'],'body_markdown':'hello'})},files=[('files',('new shared.bin',b'compensate'))])
                assert False
            except sqlite3.OperationalError:
                pass
            finally:
                org.db._conn = raw
            assert snapshot(org) == before and thread_rows() == counts
            # Concurrent attaches bind once, retaining first provenance.
            responses = await asyncio.gather(client.post(PREFIX+'/threads',json=body),client.post(PREFIX+'/threads',json=body))
            assert all(response.status_code == 200 for response in responses)
            assert org.db._conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='artifact_attribution_bound' AND task_id='artifact:rollback.bin'").fetchone()[0] == 1
            # Mixed private/shared composition retains the separate private store.
            mixed = await client.post(PREFIX+'/threads',data={'body':json.dumps(body)},files=[('files',('private.bin',b'private'))])
            assert mixed.status_code == 200,mixed.text
            mixed_detail = await client.get(PREFIX+'/threads/'+mixed.json()['thread_id'])
            mixed_attachments = mixed_detail.json()['messages'][0]['attachments']
            assert len(mixed_attachments) == 2 and mixed_attachments[1]['thread_attachment_id']
            # Due provisional attachment cannot grant a silent restore.
            await client.post(PREFIX+'/artifacts',params={'agent':'founder','name':'expired.bin'},files={'file':('expired.bin',b'expired provisional')})
            clock[0] = NOW+timedelta(days=100)
            expired_body = {'subject':'Expired provisional','recipients':['worker'],'body_markdown':'hello','attachments':[{'artifact_name':'expired.bin'}]}
            rejected = await client.post(PREFIX+'/threads',json=expired_body)
            assert rejected.status_code == 409,rejected.text
            assert org.db.get_shared_artifact('expired.bin')['origin_thread_id'] is None
            # Synthetic B proof input drives A's real recovery/restore/bind seam.
            org.db._shared_artifact_reference_checker = lambda conn,row:'absent'
            org.db._shared_artifact_effective_mode = lambda conn:'enforce'
            with org.db.shared_artifact_transaction() as conn:
                conn.execute("UPDATE shared_artifact_org_state SET rollout_started_at=?,preview_acceptance='synthetic-reviewed-preview',inventory_review_id='synthetic-reviewed-inventory',source_protection_verified=1 WHERE id=1",((clock[0]-timedelta(days=8)).isoformat(),))
            unavailable = await client.get(PREFIX+'/artifacts/expired.bin')
            assert unavailable.status_code == 410,unavailable.text
            restored = await client.post(PREFIX+'/artifacts/expired.bin/restore',json={'agent':'founder','continuing_use':'Attach current reference','expires_at':(clock[0]+timedelta(days=90)).isoformat()})
            assert restored.status_code == 200 and restored.json()['state'] == 'pending-attribution',restored.text
            rebound = await client.post(PREFIX+'/threads',json=expired_body)
            assert rebound.status_code == 200,rebound.text
            assert org.db.get_shared_artifact('expired.bin')['origin_thread_id'] == rebound.json()['thread_id']
            print('PASS provisional: exact24h timeout once; atomic real-THR bind, replay keeps expiry; shared multipart')


class CommitFailure:
    def __init__(self,conn: object,nth: int) -> None:
        self.conn,self.nth,self.count = conn,nth,0
    def __getattr__(self,name: str) -> object:
        return getattr(self.conn,name)
    def commit(self) -> None:
        self.count += 1
        if self.count == self.nth:
            raise sqlite3.OperationalError('synthetic_commit_failure')
        self.conn.commit()


def service_case(root: Path) -> None:
    root.mkdir()
    db = Database(root/'private.sqlite')
    db.insert_thread(ThreadRecord(id='THR-origin',subject='Synthetic'))
    svc = SharedArtifactService(db,ArtifactStore(root/'artifacts'),clock=lambda:NOW,known_agents={'worker'})
    meta = svc.metadata(agent='worker',thread_id='THR-origin',purpose='Persistent',retention='default')
    db._shared_artifact_reference_checker = lambda conn,row:'absent'
    first = svc.put('exact.bin',b'one',meta)
    operation_id = str(uuid.uuid4())
    second = svc.put('exact.bin',b'two',meta,operation_id=operation_id)
    assert first['generation'] == 1 and second['generation'] == 2
    assert svc.put('exact.bin',b'two',meta,operation_id=operation_id)['generation'] == 2
    try:
        svc.lookup('exact.bin',1)
        assert False,'old generation must be explainable'
    except SharedArtifactError as exc:
        assert exc.status == 410 and exc.detail['reason'] == 'replaced'
    assert svc.store.read('exact.bin') == b'two'
    # Simulate failure after rename, before the final COMMIT: durable intent finalizes once.
    raw = db._conn
    db._conn = CommitFailure(raw,4)
    failed_id = str(uuid.uuid4())
    try:
        svc.put('commit.bin',b'committed bytes',meta,operation_id=failed_id)
        assert False,'forced COMMIT must fail'
    except sqlite3.OperationalError:
        pass
    finally:
        db._conn = raw
    op = raw.execute('SELECT * FROM shared_artifact_operations WHERE operation_id=?',(failed_id,)).fetchone()
    assert op['phase'] == 'prepared' and svc.store.read('commit.bin') == b'committed bytes'
    svc.reconcile_operation(failed_id)
    svc.reconcile_operation(failed_id)
    assert raw.execute("SELECT COUNT(*) FROM audit_log WHERE action='artifact_put' AND json_extract(payload,'$.operation_id')=?",(failed_id,)).fetchone()[0] == 1
    # Rename failure preserves old bytes and releases only positively owned staging.
    failure_id = str(uuid.uuid4())
    with patch('runtime.infrastructure.shared_artifact_service.os.replace',side_effect=OSError('synthetic_rename_failure')):
        try:
            svc.put('exact.bin',b'refused',meta,operation_id=failure_id)
            assert False
        except OSError:
            pass
    assert svc.store.read('exact.bin') == b'two'
    op = raw.execute('SELECT phase,reserved_bytes FROM shared_artifact_operations WHERE operation_id=?',(failure_id,)).fetchone()
    assert tuple(op) == ('aborted',0),tuple(op)
    # Exact failed request retries its original generation/operation.
    assert svc.put('exact.bin',b'refused',meta,operation_id=failure_id)['generation'] == 3
    # Staging failure cannot strand quota reservations or public bytes.
    stage_id = str(uuid.uuid4())
    with patch('runtime.infrastructure.shared_artifact_service.os.fsync',side_effect=OSError('synthetic_stage_failure')):
        try:
            svc.put('stage.bin',b'stage',meta,operation_id=stage_id)
            assert False
        except OSError:
            pass
    assert not svc.store.exists('stage.bin')
    assert tuple(raw.execute('SELECT phase,reserved_bytes FROM shared_artifact_operations WHERE operation_id=?',(stage_id,)).fetchone()) == ('aborted',0)
    before_audit = raw.execute('SELECT COUNT(*) FROM shared_artifact_operations').fetchone()[0]
    with patch.object(svc,'_event',side_effect=RuntimeError('synthetic_audit_failure')):
        try:
            svc.put('audit.bin',b'audit',meta)
            assert False
        except RuntimeError:
            pass
    assert not svc.store.exists('audit.bin')
    assert raw.execute('SELECT COUNT(*) FROM shared_artifact_operations').fetchone()[0] == before_audit
    db._shared_artifact_reference_checker = lambda conn,row:'live'
    try:
        svc.put('exact.bin',b'live refused',meta)
        assert False
    except SharedArtifactError as exc:
        assert exc.detail['code'] == 'artifact_live_dependency'
    assert svc.store.read('exact.bin') == b'refused'
    db._shared_artifact_reference_checker = lambda conn,row:'absent'
    # Old writer bytes are legacy with null expiry, counted and non-overwritable.
    svc.store.put('legacy.bin',b'legacy')
    with db.shared_artifact_transaction() as conn:
        used,reserved = svc.reconcile_usage_uncommitted(conn)
    assert used == sum(p.stat().st_size for p in svc.store.root.rglob('*') if p.is_file()) and reserved == 0
    assert db.get_shared_artifact('legacy.bin')['expires_at'] is None
    try:
        svc.put('legacy.bin',b'new',meta)
        assert False
    except SharedArtifactError as exc:
        assert exc.detail['code'] == 'artifact_name_retained'
    assert svc.store.read('legacy.bin') == b'legacy'
    svc.clock = lambda:NOW-timedelta(days=100)
    oldmeta = svc.metadata(agent='worker',thread_id='THR-origin',purpose='Old reference',retention='default')
    svc.put('late.bin',b'late',oldmeta)
    svc.clock = lambda:NOW
    db._shared_artifact_effective_mode = lambda conn:'enforce'
    with db.shared_artifact_transaction() as conn:
        conn.execute("UPDATE shared_artifact_org_state SET rollout_started_at=?,preview_acceptance='synthetic-reviewed-preview',inventory_review_id='synthetic-reviewed-inventory',source_protection_verified=1 WHERE id=1",((NOW-timedelta(days=8)).isoformat(),))
    try:
        svc.lookup('late.bin')
        assert False
    except SharedArtifactError as exc:
        assert exc.status == 410
    late = db.get_shared_artifact('late.bin')
    assert late['recovery_started_at'] == NOW.isoformat()
    assert late['recoverable_until'] == (NOW+timedelta(days=7)).isoformat()
    db.insert_thread(ThreadRecord(id='THR-notice',subject='Synthetic notice primitive'))
    expiry = db.get_shared_artifact('exact.bin')['expires_at']
    with db.shared_artifact_transaction():
        first_notice = db.shared_artifact_notice_uncommitted(name='exact.bin',generation=3,expires_at=expiry,
            notice_kind='expiry',destination_thread_id='THR-notice',payload={'file':'exact.bin','owner':'worker','expiry':expiry})
        same_notice = db.shared_artifact_notice_uncommitted(name='exact.bin',generation=3,expires_at=expiry,
            notice_kind='expiry',destination_thread_id='THR-notice',payload={'file':'exact.bin','owner':'worker','expiry':expiry})
    assert first_notice == same_notice
    before_messages = raw.execute("SELECT COUNT(*) FROM thread_messages WHERE thread_id='THR-notice'").fetchone()[0]
    with patch.object(db,'insert_audit_log_uncommitted',side_effect=RuntimeError('synthetic_notice_audit_failure')):
        try:
            with db.shared_artifact_transaction():
                db.shared_artifact_notice_uncommitted(name='exact.bin',generation=3,expires_at=expiry,
                    notice_kind='attribution_timeout',destination_thread_id='THR-notice',payload={'file':'exact.bin'})
            assert False
        except RuntimeError:
            pass
    assert raw.execute("SELECT COUNT(*) FROM thread_messages WHERE thread_id='THR-notice'").fetchone()[0] == before_messages
    assert raw.execute("SELECT COUNT(*) FROM shared_artifact_notice_receipts WHERE notice_kind='attribution_timeout'").fetchone()[0] == 0
    db.close()
    reopened = Database(root/'private.sqlite')
    assert reopened.get_shared_artifact('legacy.bin')['expires_at'] is None
    reopened.close()
    print('PASS journal: exact overwrite/history/replay; forced final COMMIT recovery once; rename compensation/retry; legacy null TTL')


async def custom_case(root: Path) -> None:
    from runtime.daemon.routes import custom_skills
    async with fixture(root) as (org,client):
        def payload(slug: str) -> dict:
            return {'slug':slug,'name':slug,'skill_md':f'---\nname: {slug}\ndescription: Synthetic artifact admission case.\n---\n\nSynthetic bounded instruction.\n'}
        response = await client.post(PREFIX+'/custom-skills',json=payload('artifact-positive'))
        assert response.status_code == 201,response.text
        row = org.db._conn.execute('SELECT * FROM shared_artifact_generations').fetchone()
        assert row is not None,'canonical source must have a generation row'
        assert row['owner_kind'] == 'human' and row['owner_agent_id'] == 'engineering_manager'
        assert row['system_derived'] == 1 and row['protected_source'] == 1
        assert row['origin_task_id'] is None and 'artifact-positive' in row['purpose']
        assert org.db._conn.execute("SELECT COUNT(*) FROM shared_artifact_operations WHERE phase='committed'").fetchone()[0] == 1
        org.db.insert_task(TaskRecord(id='TASK-source',assigned_agent='worker',team='retiring',brief='Synthetic source authoring.',status=TaskStatus.IN_PROGRESS,current_session_id='sess-source'))
        org.sessions.set_active('TASK-source','worker','sess-source',org_slug=org.slug)
        bearer = client.headers.pop('Authorization')
        try:
            agent_response = await client.post(PREFIX+'/custom-skills/agent-create',params={'session_id':'sess-source'},json=payload('artifact-agent'))
        finally:
            client.headers['Authorization'] = bearer
        assert agent_response.status_code == 201,agent_response.text
        agent_row = org.db._conn.execute("SELECT * FROM shared_artifact_generations WHERE owner='worker'").fetchone()
        assert agent_row['owner_kind'] == 'agent' and agent_row['origin_task_id'] == 'TASK-source'
        for kind in ('blob','commit'):
            before = snapshot(org)
            versions = org.db._conn.execute('SELECT COUNT(*) FROM custom_skill_versions').fetchone()[0]
            if kind == 'blob':
                context = patch.object(ArtifactStore,'put',side_effect=OSError('synthetic_blob_failure'))
            else:
                context = patch.object(org.db,'_conn',CommitFailure(org.db._conn,1))
            try:
                with context:
                    await client.post(PREFIX+'/custom-skills',json=payload('artifact-'+kind))
                assert False,kind
            except (OSError,sqlite3.OperationalError):
                pass
            assert snapshot(org) == before,kind
            assert org.db._conn.execute('SELECT COUNT(*) FROM custom_skill_versions').fetchone()[0] == versions
        store = ArtifactStore(org.root/'artifacts')
        store.put('custom-skills/blocked-skill',b'preexisting peer alias')
        before = snapshot(org)
        versions = org.db._conn.execute('SELECT COUNT(*) FROM custom_skill_versions').fetchone()[0]
        collision = await client.post(PREFIX+'/custom-skills',json=payload('blocked-skill'))
        assert collision.status_code == 400 and collision.json()['detail']['code'] == 'invalid_artifact_name',collision.text
        assert snapshot(org) == before and store.read('custom-skills/blocked-skill') == b'preexisting peer alias'
        assert org.db._conn.execute('SELECT COUNT(*) FROM custom_skill_versions').fetchone()[0] == versions
        print('PASS custom sources: version create counted/journaled; forced blob and COMMIT failures leave zero residue')


async def quota_and_history_case(root: Path) -> None:
    async with fixture(root) as (org,client):
        org.db.insert_thread(ThreadRecord(id='THR-origin',subject='Synthetic'))
        put = await client.post(PREFIX+'/artifacts',params={'agent':'worker','thread_id':'THR-origin','purpose':'Persistent','retention':'default','name':'history.bin'},files={'file':('history.bin',b'history')})
        assert put.status_code == 200,put.text
        svc = org.db._shared_artifact_service
        with org.db.shared_artifact_transaction() as conn:
            used,reserved = svc.reconcile_usage_uncommitted(conn)
            remaining = QUOTA_BYTES-used
            # Private synthetic in-flight requests, using the real quota amount.
            index = 0
            while remaining:
                size = min(10*1024*1024,remaining)
                index += 1
                conn.execute("INSERT INTO shared_artifact_operations(operation_id,name,generation,operation,phase,reserved_bytes,created_at,updated_at) VALUES(?,?,1,'put','reserved',?,?,?)",(str(uuid.uuid4()),f'reservation-{index}',size,NOW.isoformat(),NOW.isoformat()))
                remaining -= size
            assert svc.reconcile_usage_uncommitted(conn) == (used,QUOTA_BYTES-used)
        before = snapshot(org)
        refusal = await client.post(PREFIX+'/artifacts',params={'agent':'worker','thread_id':'THR-origin','purpose':'Persistent','retention':'default','name':'full.bin'},files={'file':('full.bin',b'x')})
        assert refusal.status_code == 413,refusal.text
        assert refusal.json()['detail']['quota_bytes'] == 5_000_000_000
        assert snapshot(org) == before
        with org.db.shared_artifact_transaction() as conn:
            conn.execute("UPDATE shared_artifact_operations SET reserved_bytes=reserved_bytes-1 WHERE operation_id=(SELECT operation_id FROM shared_artifact_operations WHERE name LIKE 'reservation-%' LIMIT 1)")
        exact = await client.post(PREFIX+'/artifacts',params={'agent':'worker','thread_id':'THR-origin','purpose':'Persistent','retention':'default','name':'exact-limit.bin'},files={'file':('exact-limit.bin',b'x')})
        assert exact.status_code == 200,exact.text
        with org.db.shared_artifact_transaction() as conn:
            used_now,reserved_now = svc.reconcile_usage_uncommitted(conn)
            assert used_now+reserved_now == QUOTA_BYTES
        assert (await client.get(PREFIX+'/artifacts/history.bin')).content == b'history'
        thread = await client.post(PREFIX+'/threads',json={'subject':'Historical link','recipients':['worker'],'body_markdown':'Synthetic history','attachments':[{'artifact_name':'history.bin'}]})
        assert thread.status_code == 200,thread.text
        historical_id = thread.json()['thread_id']
        with org.db.shared_artifact_transaction() as conn:
            conn.execute("UPDATE threads SET status='archived',archived_at=? WHERE id=?",(NOW.isoformat(),historical_id))
        # Existing counted recovery bytes restore at full quota without reserving again.
        with org.db.shared_artifact_transaction() as conn:
            conn.execute("UPDATE shared_artifact_generations SET state='recoverable',expired_on=?,recovery_started_at=?,recoverable_until=?,reason='retention_expired' WHERE name='history.bin'",(NOW.isoformat(),NOW.isoformat(),(NOW+timedelta(days=7)).isoformat()))
        missing = await client.get(PREFIX+'/artifacts/history.bin')
        assert missing.status_code == 410 and missing.json()['detail']['code'] == 'artifact_unavailable'
        assert missing.json()['detail']['owner'] == 'worker' and missing.json()['detail']['can_restore'] is True
        from cli.client.client import OpcClient
        from cli.commands.artifacts import cmd_artifacts_get
        loop = asyncio.get_running_loop()
        def bridge_get(path: str, **kwargs: object) -> object:
            return asyncio.run_coroutine_threadsafe(client.get(path,**kwargs),loop).result()
        destination = root/'unchanged.bin'
        destination.write_bytes(b'keep destination')
        def cli_get() -> tuple[int,str]:
            opc = OpcClient.__new__(OpcClient)
            opc._client = SimpleNamespace(get=bridge_get)
            output = StringIO()
            with patch.object(OpcClient,'from_env',return_value=opc),redirect_stderr(output):
                try:
                    cmd_artifacts_get(argparse.Namespace(org='alpha',name='history.bin',generation=None,output=destination))
                except SystemExit as exc:
                    return exc.code,output.getvalue()
            return 0,output.getvalue()
        code,message = await asyncio.to_thread(cli_get)
        assert code == 1 and 'worker' in message and 'retention_expired' in message,(code,message)
        assert destination.read_bytes() == b'keep destination'
        from cli.commands.threads import cmd_threads_show
        def cli_show() -> str:
            opc = OpcClient.__new__(OpcClient)
            opc._client = SimpleNamespace(get=bridge_get)
            output = StringIO()
            with patch.object(OpcClient,'from_env',return_value=opc),redirect_stdout(output):
                cmd_threads_show(argparse.Namespace(org='alpha',thread_id=historical_id,json=False))
            return output.getvalue()
        shown = await asyncio.to_thread(cli_show)
        assert 'unavailable:' in shown and 'worker' in shown and 'retention_expired' in shown,shown
        restored = await client.post(PREFIX+'/artifacts/history.bin/restore',json={'agent':'worker','continuing_use':'Needed reference','expires_at':(NOW+timedelta(days=90)).isoformat()})
        assert restored.status_code == 200,restored.text
        renewal = await client.post(PREFIX+'/artifacts/history.bin/renew',json={'agent':'worker','continuing_use':'Needed reference','expires_at':(NOW+timedelta(days=90,microseconds=1)).isoformat()})
        assert renewal.status_code == 422,renewal.text
        previous = org.db.get_shared_artifact('history.bin')['expires_at']
        await client.get(PREFIX+'/artifacts/history.bin')
        assert org.db.get_shared_artifact('history.bin')['expires_at'] == previous
        reference = await client.post(PREFIX+'/artifacts/history.bin/renew',json={'agent':'worker','continuing_use':'Design reference','retention':'reference','reason':'Current design source','expires_at':(NOW+timedelta(days=180)).isoformat()})
        assert reference.status_code == 200,reference.text
        beyond = await client.post(PREFIX+'/artifacts/history.bin/renew',json={'agent':'worker','continuing_use':'Design reference','retention':'reference','reason':'Current design source','expires_at':(NOW+timedelta(days=180,microseconds=1)).isoformat()})
        assert beyond.status_code == 422,beyond.text
        with org.db.shared_artifact_transaction() as conn:
            conn.execute("UPDATE shared_artifact_generations SET state='recoverable',expired_on=?,recovery_started_at=?,recoverable_until=?,reason='retention_expired' WHERE name='history.bin'",(NOW.isoformat(),NOW.isoformat(),(NOW+timedelta(days=7)).isoformat()))
        with patch('runtime.infrastructure.shared_artifact_service._utc',return_value=NOW+timedelta(days=7)):
            boundary = await client.post(PREFIX+'/artifacts/history.bin/restore',json={'agent':'worker','continuing_use':'At boundary','expires_at':(NOW+timedelta(days=97)).isoformat()})
            assert boundary.status_code == 409,boundary.text
            expired = await client.get(PREFIX+'/artifacts/history.bin')
            assert expired.status_code == 410 and expired.json()['detail']['can_restore'] is False
        with org.db.shared_artifact_transaction() as conn:
            conn.execute("UPDATE shared_artifact_generations SET state='deleted',deleted_at=?,reason='retention_expired' WHERE name='history.bin'",(NOW.isoformat(),))
        deleted = await client.get(PREFIX+'/artifacts/history.bin')
        assert deleted.status_code == 410 and deleted.json()['detail']['deleted_at'] == NOW.isoformat()
        unknown = await client.get(PREFIX+'/artifacts/never-known.bin')
        assert unknown.status_code == 404,unknown.text
        # Custom writer must refuse before any blob/version/generation/journal residue.
        from runtime.daemon.routes.custom_skills import _artifact_key
        payload={'slug':'quota-custom','name':'Quota custom','skill_md':'---\nname: quota-custom\ndescription: Synthetic quota case.\n---\n\nSynthetic.'}
        versions = org.db._conn.execute('SELECT COUNT(*) FROM custom_skill_versions').fetchone()[0]
        before_custom = snapshot(org)
        response = await client.post(PREFIX+'/custom-skills',json=payload)
        assert response.status_code == 413,response.text
        assert org.db._conn.execute('SELECT COUNT(*) FROM custom_skill_versions').fetchone()[0] == versions
        assert snapshot(org) == before_custom
        assert not svc.store.exists(_artifact_key('quota-custom',payload['skill_md']))
        with org.db.shared_artifact_transaction() as conn:
            conn.execute("UPDATE shared_artifact_operations SET reserved_bytes=reserved_bytes-2 WHERE operation_id=(SELECT operation_id FROM shared_artifact_operations WHERE name LIKE 'reservation-%' LIMIT 1)")
        metadata = svc.metadata(agent='worker',thread_id='THR-origin',purpose='Concurrent persistent files',retention='default')
        admissions = await asyncio.gather(asyncio.to_thread(svc.put,'concurrent-a.bin',b'aa',metadata),
            asyncio.to_thread(svc.put,'concurrent-b.bin',b'b',metadata),return_exceptions=True)
        assert sum(isinstance(result,dict) for result in admissions) == 1,admissions
        assert sum(isinstance(result,SharedArtifactError) and result.status == 413 for result in admissions) == 1,admissions
        with org.db.shared_artifact_transaction() as conn:
            used,reserved = svc.reconcile_usage_uncommitted(conn)
            assert used+reserved <= QUOTA_BYTES
        print('PASS quota/history: exact5GB reservations counted once; +1 refused zero residue; downloads and restore at limit; renew bound+1; never-known404')


def shape_case(root: Path) -> None:
    """Production file-count/byte shape, sparse private bytes, real hash/SQL pass."""
    root.mkdir()
    db = Database(root/'private.sqlite')
    store = ArtifactStore(root/'artifacts')
    total = 1_700_000_000
    each,remainder = divmod(total,7500)
    for index in range(7500):
        with (store.root/f'legacy-{index}.bin').open('wb') as stream:
            stream.truncate(each+int(index < remainder))
    service = SharedArtifactService(db,store,clock=lambda:NOW)
    started = time.monotonic()
    with db.shared_artifact_transaction() as conn:
        used,reserved = service.reconcile_usage_uncommitted(conn)
    elapsed = time.monotonic()-started
    assert used == 1_700_000_000 and reserved == 0,(used,reserved)
    assert db._conn.execute("SELECT COUNT(*) FROM shared_artifact_generations WHERE state='legacy_unclassified' AND expires_at IS NULL AND owner IS NULL").fetchone()[0] == 7500
    db.close()
    print(f'PASS shape: 7500 sparse legacy files /1700000000 observed bytes; real hash+SQL scan {elapsed:.3f}s; no inferred TTL')


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--case',choices=('all','metadata','provisional','journal','custom','quota','shape'),default='all')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='shared-artifact-A-') as directory, patch('runtime.infrastructure.shared_artifact_service._utc',return_value=NOW):
        root = Path(directory)
        cases = {'metadata':metadata_case,'provisional':provisional_case,'journal':service_case,'custom':custom_case,'quota':quota_and_history_case,'shape':shape_case}
        for name,case in cases.items():
            if args.case in ('all',name):
                result = case(root/name)
                if asyncio.iscoroutine(result):
                    await result
    print('Slice A selected exercises completed; no live org or lifespan used.')


if __name__ == '__main__':
    asyncio.run(main())
