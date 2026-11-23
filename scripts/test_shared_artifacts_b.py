"""Slice B private acceptance checks; no daemon lifespan or live org.

Run: uv run --frozen python scripts/test_shared_artifacts_b.py --case manual-delete
Manager item7: effective enforce retires; preview/off are nonmutating.
--case quota-chunks checks atomic bounded multi-message quota delivery.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from datetime import datetime, timedelta
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime.models import ThreadMessageKind, ThreadRecord, TaskRecord, TaskStatus
from runtime.infrastructure.shared_artifact_references import install_reference_seams, reference_outcomes, ReferenceProof, ReferenceUnavailable
from scripts.test_agent_retirement import PREFIX, fixture
from scripts.test_shared_artifacts import NOW, snapshot


async def manual_delete_case(root: Path, *, mode: str, live: bool = False) -> None:
    """Observe the real DELETE route with installed B reference/mode adapters."""
    with patch('runtime.infrastructure.shared_artifact_service._utc', return_value=NOW):
        async with fixture(root) as (org, client):
            (org.root/'org'/'config.yaml').write_text(
                'reviewer_agents: [other]\nshared_artifacts:\n  cleanup_mode: '+mode+'\n')
            install_reference_seams(org)
            org.db.insert_thread(ThreadRecord(id='THR-origin', subject='Synthetic origin'))
            put = await client.post(
                PREFIX + '/artifacts',
                params=dict(agent='worker', thread_id='THR-origin',
                            purpose='Persistent design source', retention='default'),
                files={'file': ('manual-preview.bin', b'synthetic retained source')},
            )
            assert put.status_code == 200, put.text
            # Synthetic persisted gates never touch live config or establish Q3 proof.
            if mode == 'enforce':
                with org.db.shared_artifact_transaction() as conn:
                    conn.execute(
                        "UPDATE shared_artifact_org_state SET rollout_started_at=?,"
                        "preview_acceptance='synthetic-preview',"
                        "inventory_review_id='synthetic-inventory',"
                        "source_protection_verified=1 WHERE id=1",
                        ((NOW - timedelta(days=8)).isoformat(),),
                    )
            if live:
                org.db.append_thread_message(thread_id='THR-origin', speaker='worker',
                    kind=ThreadMessageKind.MESSAGE,
                    body_markdown='Keep [artifact:manual-preview.bin] for the open review.')
            before = snapshot(org)
            response = await client.delete(
                PREFIX + '/artifacts/manual-preview.bin', params={'agent': 'worker'},
            )
            row = org.db.get_shared_artifact('manual-preview.bin')
            print(json.dumps(dict(
                case='manual-delete-'+mode+('-live' if live else ''),
                status=response.status_code, response=response.json(),
                persisted_state=row['state'],
                recovery_started_at=row['recovery_started_at'],
                recoverable_until=row['recoverable_until'],
                snapshot_unchanged=snapshot(org) == before,
            ), sort_keys=True), flush=True)
            assert response.status_code == 200, response.text
            should_retire = mode == 'enforce' and not live
            assert response.json().get('mutated') is should_retire, response.text
            if should_retire:
                assert row['state'] == response.json()['state'] == 'recoverable'
                assert row['recoverable_until'] == (NOW+timedelta(days=7)).isoformat()
                assert (org.root/'artifacts'/'manual-preview.bin').read_bytes() == b'synthetic retained source'
            else:
                assert row['state'] == 'active' and row['recovery_started_at'] is None
                if live:
                    assert response.json()['protected'] is True
                else:
                    assert response.json()['effective_mode'] == 'preview', response.text
                    assert snapshot(org) == before, 'preview/off DELETE changed lifecycle rows or bytes'
            events = org.db._conn.execute(
                "SELECT action,payload FROM audit_log WHERE task_id=? "
                "AND action IN ('artifact_preview','artifact_expire','artifact_delete')",
                ('artifact:manual-preview.bin',),
            ).fetchall()
            if should_retire:
                assert len(events) == 1 and events[0]['action'] == 'artifact_expire'
            elif not live:
                assert len(events) == 1 and events[0]['action'] == 'artifact_preview'
                assert json.loads(events[0]['payload'])['mutated'] is False
            else:
                assert not events, 'live reference must prevent expiry/delete'
            print('PASS manual-delete-'+mode+('-live' if live else ''))


async def quota_chunks_case(root: Path) -> None:
    """One crossing must retain every bounded visible chunk without duplicate replay."""
    async with fixture(root) as (org, _client):
        assert (await _client.get(PREFIX+'/artifacts')).status_code == 200
        org.db.insert_thread(ThreadRecord(id='THR-notice', subject='Private quota notice'))
        payloads = [dict(notice_type='artifact_quota', crossing_sequence=1,
                        routing=dict(agent='manager', reason='accountable_manager', thread_id='THR-notice'),
                        files=[dict(file_name=f'chunk-{i}.bin', expiry_date=NOW.isoformat(),
                                    owner_kind='agent', owner='worker', purpose='Synthetic quota proof',
                                    recovery_deadline=None, renew_command=f'happyranch artifacts renew chunk-{i}.bin',
                                    restore_command=f'happyranch artifacts restore chunk-{i}.bin')],
                        chunk_index=i, chunk_count=2) for i in range(2)]
        db = org.db
        with db.shared_artifact_transaction():
            sequences = db.shared_artifact_quota_alert_uncommitted(
                crossing_sequence=1, destination_thread_id='THR-notice', payloads=payloads)
        messages = db._conn.execute(
            "SELECT seq,system_payload_json FROM thread_messages WHERE thread_id='THR-notice' ORDER BY seq").fetchall()
        visible = [json.loads(r['system_payload_json']) for r in messages]
        print(json.dumps(dict(case='quota-chunks', sequences=sequences, persisted_payloads=visible)), flush=True)
        assert len(visible) == 2 and visible == payloads, 'Every quota chunk must be visible'
        # Restart-style replay reads the durable receipt, not an in-memory cache.
        with db.shared_artifact_transaction():
            replay = db.shared_artifact_quota_alert_uncommitted(
                crossing_sequence=1, destination_thread_id='THR-notice', payloads=[{'ignored':'retry'}])
            assert replay == sequences
            assert db.shared_artifact_notice_uncommitted(
                name='', generation=1, expires_at='', notice_kind='quota_alert',
                destination_thread_id='THR-notice', payload={'ignored':'wrapper retry'}) == sequences[0]
        receipt = db._conn.execute("SELECT * FROM shared_artifact_notice_receipts WHERE notice_kind='quota_alert'").fetchall()
        assert len(receipt) == 1
        assert json.loads(receipt[0]['payload_json']) == dict(chunk_count=2, message_seqs=sequences, payloads=payloads)
        audits = db._conn.execute("SELECT task_id,payload FROM audit_log WHERE action='artifact_quota_alert'").fetchall()
        assert len(audits) == 1 and audits[0]['task_id'] == 'config:shared_artifacts'
        assert json.loads(audits[0]['payload'])['message_seqs'] == sequences
        # A second-chunk failure and an audit failure must roll back the whole
        # caller transaction, including its quota crossing latch.
        append = db._append_thread_message_uncommitted
        calls = 0
        def fail_second(**kwargs: object) -> int:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('synthetic_second_chunk_failure')
            return append(**kwargs)
        for method, failure in (('_append_thread_message_uncommitted', fail_second),
                                ('insert_audit_log_uncommitted', RuntimeError('synthetic_audit_failure'))):
            kwargs = {'side_effect':failure}
            try:
                with patch.object(db, method, **kwargs):
                    with db.shared_artifact_transaction() as conn:
                        conn.execute('UPDATE shared_artifact_org_state SET quota_above=1,crossing_sequence=2 WHERE id=1')
                        db.shared_artifact_quota_alert_uncommitted(
                            crossing_sequence=2, destination_thread_id='THR-notice', payloads=payloads)
                raise AssertionError('failure was not injected')
            except RuntimeError as exc:
                assert 'synthetic_' in str(exc)
            assert db._conn.execute("SELECT COUNT(*) FROM thread_messages WHERE thread_id='THR-notice'").fetchone()[0] == 2
            assert db._conn.execute('SELECT crossing_sequence FROM shared_artifact_org_state').fetchone()[0] == 0
            assert db._conn.execute('SELECT COUNT(*) FROM shared_artifact_notice_receipts').fetchone()[0] == 1
        # 7,500 complete entries through the actual message/receipt/audit writer.
        # 25 entries/chunk stays well within the conservative escaped-JSON bound.
        started = time.monotonic()
        shape = [dict(payloads[i % 2]['files'][0], file_name=f'owner-2026-11-23-file-{i:05}.bin') for i in range(7500)]
        chunks = [dict(notice_type='artifact_quota', files=shape[i:i+25]) for i in range(0,7500,25)]
        with db.shared_artifact_transaction():
            seqs = db.shared_artifact_quota_alert_uncommitted(
                crossing_sequence=2, destination_thread_id='THR-notice', payloads=chunks)
        shape_rows = db._conn.execute("SELECT system_payload_json FROM thread_messages WHERE thread_id='THR-notice' AND seq>? ORDER BY seq",(sequences[-1],)).fetchall()
        persisted = [json.loads(r[0]) for r in shape_rows]
        assert [f for chunk in persisted for f in chunk['files']] == shape
        assert len(seqs) == len(persisted) == 300
        assert all(p['crossing_sequence'] == 2 and p['chunk_count'] == 300 and p['chunk_index'] == i
                   for i,p in enumerate(persisted))
        maximum = max(len(r[0].encode()) for r in shape_rows)
        elapsed = time.monotonic()-started
        assert maximum <= 32*1024 and elapsed < 60
        assert db._conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='artifact_quota_alert'").fetchone()[0] == 2
        from runtime.daemon.shared_artifacts_notices import SharedArtifactNotices
        production_rows = [dict(name=f['file_name'],state='active',owner='worker',owner_kind='agent',
                                owner_agent_id='worker',expires_at=f['expiry_date'],purpose=f['purpose']) for f in shape]
        with db.shared_artifact_transaction() as conn:
            built_seqs = SharedArtifactNotices(org).deliver_quota(conn,production_rows,
                quota=dict(crossing=True,crossing_sequence=3,used_bytes=4000000000,reserved_bytes=0,quota_bytes=5000000000,above=True),
                now=NOW,mode='preview',deadline=started+60)
        built = db._conn.execute("SELECT system_payload_json FROM thread_messages WHERE thread_id=(SELECT destination_thread_id FROM shared_artifact_notice_receipts WHERE generation=3 AND notice_kind='quota_alert') ORDER BY seq").fetchall()
        # This is a new, provenance-verified management thread.
        assert [f['file_name'] for r in built for f in json.loads(r[0])['files']] == [f['file_name'] for f in shape]
        assert all(len(r[0].encode()) <= 32*1024 for r in built)
        assert time.monotonic()-started < 60
        print(f'PASS production quota builder:7500 files;{len(built_seqs)} messages;max_bytes={max(len(r[0].encode()) for r in built)};elapsed={time.monotonic()-started:.3f}s')
        print(f'PASS quota-chunks: all chunks/replay/rollback;7500 entries;300 messages;max_bytes={maximum};elapsed={elapsed:.3f}s')


async def references_case(root: Path) -> None:
    """Real corpus/transition boundary, including generation pins and fail-closed proof."""
    with patch('runtime.infrastructure.shared_artifact_service._utc', return_value=NOW):
        async with fixture(root) as (org, client):
            db = org.db
            install_reference_seams(org, origin='http://synthetic.test')
            db.insert_thread(ThreadRecord(id='THR-ref', subject='Reference acceptance'))
            response = await client.post(PREFIX+'/artifacts',
                params=dict(agent='worker',thread_id='THR-ref',purpose='Persistent proof',retention='default'),
                files={'file':('source.bin',b'synthetic reference source')})
            assert response.status_code == 200
            row = db.get_shared_artifact('source.bin')
            def check(expected: str, label: str, *, deadline: float | None = None) -> None:
                with db.shared_artifact_transaction() as conn:
                    outcome, refs = reference_outcomes(org,conn,[row],
                        deadline=deadline if deadline is not None else time.monotonic()+60,
                        origin='http://synthetic.test')[row['name'],row['generation']]
                    assert outcome == expected, (label,outcome,refs)
                    result = db._shared_artifact_service.lifecycle_transition_uncommitted(
                        conn,name=row['name'],generation=row['generation'],expected_revision=row['revision'],
                        action='retire',reason='synthetic_reference_acceptance',reference_outcome=outcome,
                        effective_mode='enforce',operation_id=label)
                    assert result['mutated'] is (expected == 'absent')
                persisted = db.get_shared_artifact(row['name'])
                assert persisted['state'] == ('recoverable' if expected == 'absent' else 'active')
                if expected == 'absent':
                    db._shared_artifact_service.renew(row['name'],agent='worker',continuing_use='synthetic case reset',
                        expires_at=(NOW+timedelta(days=90)).isoformat(),restore=True)
                row.update(db.get_shared_artifact(row['name']))
                print('PASS references:'+label+':'+expected)
            with db.shared_artifact_transaction() as conn:
                conn.execute("UPDATE shared_artifact_org_state SET rollout_started_at=?,preview_acceptance='private',inventory_review_id='private',source_protection_verified=1",((NOW-timedelta(days=8)).isoformat(),))
            check('absent','empty')
            db.insert_task(TaskRecord(id='TASK-100',brief='Need [artifact:source.bin]',assigned_agent='worker'))
            for status in (TaskStatus.PENDING,TaskStatus.IN_PROGRESS,TaskStatus.ESCALATED):
                with db.shared_artifact_transaction() as conn:
                    conn.execute("UPDATE tasks SET status=?,brief='Need [artifact:source.bin]' WHERE id='TASK-100'",(status,))
                check('live','task-'+status)
                with db.shared_artifact_transaction() as conn:
                    conn.execute("UPDATE tasks SET status='completed',brief='Done' WHERE id='TASK-100'")
            db.insert_task(TaskRecord(id='TASK-101',brief='Read TASK-102',assigned_agent='worker'))
            db.insert_task(TaskRecord(id='TASK-102',status=TaskStatus.COMPLETED,brief='Need `source.bin`; see TASK-101',assigned_agent='worker'))
            check('live','handoff-cycle')
            with db.shared_artifact_transaction() as conn:
                conn.execute("UPDATE tasks SET brief='No artifacts' WHERE id='TASK-102'")
            db.insert_task(TaskRecord(id='TASK-103',status=TaskStatus.COMPLETED,parent_task_id='TASK-101',brief='source.bin',assigned_agent='worker'))
            check('live','direct-child')
            with db.shared_artifact_transaction() as conn:
                conn.execute("UPDATE tasks SET status='completed',brief='Done' WHERE id IN ('TASK-101','TASK-102','TASK-103')")
            for text,expected,label in (
                ('[artifact:source.bin]','live','explicit'),
                ('`source.bin`','live','exact-token'),
                ('prefix-source.bin suffixsource.bin SOURCE.bin','absent','no-substring-or-case-fold'),
                ('/api/v1/orgs/alpha/artifacts/source%2Ebin?generation=1','live','url-once-pin'),
                ('/api/v1/orgs/alpha/artifacts/source.bin?generation=2','absent','other-generation'),
                ('https://remote.invalid/api/v1/orgs/alpha/artifacts/source.bin','absent','foreign-origin'),
                ('/api/v1/orgs/other/artifacts/source.bin','absent','foreign-org'),
                ('[artifact:../source.bin]','indeterminate','malformed-explicit'),
                ('/api/v1/orgs/alpha/artifacts/source%ZZbin','indeterminate','malformed-url'),
            ):
                db.append_thread_message(thread_id='THR-ref',speaker='worker',kind=ThreadMessageKind.MESSAGE,body_markdown=text)
                check(expected,label)
                with db.shared_artifact_transaction() as conn:
                    conn.execute("DELETE FROM thread_messages WHERE thread_id='THR-ref'")
            db.append_thread_message(thread_id='THR-ref',speaker='system',kind=ThreadMessageKind.SYSTEM,
                system_payload={'attachments':[{'artifact_name':'source.bin','generation':2}]})
            check('absent','structured-other-generation')
            with db.shared_artifact_transaction() as conn:
                conn.execute("UPDATE thread_messages SET system_payload_json=? WHERE thread_id='THR-ref'",(json.dumps({'attachments':[{'artifact_name':'source.bin','generation':1}]}),))
            check('live','structured-generation')
            with db.shared_artifact_transaction() as conn:
                conn.execute("UPDATE threads SET status='archived' WHERE id='THR-ref'")
            check('absent','archived-only')
            check('indeterminate','deadline',deadline=time.monotonic()-1)
            with patch('runtime.infrastructure.shared_artifact_references._MAX_ROWS',0):
                check('indeterminate','row-cap')
            with db.shared_artifact_transaction() as conn:
                proof = ReferenceProof(conn,slug=org.slug,store=db._shared_artifact_service.store,deadline=time.monotonic()+60)
                conn.execute("UPDATE tasks SET brief=brief")
                # An actual own write after the snapshot invalidates it.
                conn.execute("UPDATE threads SET subject='changed snapshot' WHERE id='THR-ref'")
                try:
                    proof.outcome(row)
                    raise AssertionError('changed snapshot accepted')
                except ReferenceUnavailable:
                    pass
            # Canonical source records, including noncurrent/retired versions and
            # surviving materialization/recovery readers. No eligibility shortcuts.
            with db.shared_artifact_transaction() as conn:
                conn.execute("INSERT INTO custom_skills(id,org_slug,slug,name,origin_kind,created_at,created_by) VALUES('skill','alpha','source','Synthetic','human',?,'founder')",(NOW.isoformat(),))
                conn.execute("INSERT INTO custom_skill_versions(id,skill_id,content_hash,content_artifact_key,created_at,author_kind,author_identity) VALUES(1,'skill',?,'source.bin',?,'human','founder')",(row['sha256'],NOW.isoformat()))
            check('live','b2-all-versions')
            with db.shared_artifact_transaction() as conn:
                conn.execute("UPDATE custom_skills SET retired_at=? WHERE id='skill'",(NOW.isoformat(),))
            check('indeterminate','retired-no-reader-exclusion')
            with db.shared_artifact_transaction() as conn:
                conn.execute("INSERT INTO custom_skill_materializations(skill_id,agent_name,session_context,session_id,version_id,content_hash,success,created_at) VALUES('skill','worker','thread','synthetic',1,?,1,?)",(row['sha256'],NOW.isoformat()))
            check('live','retired-materialization-reader')
            assert db._conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='artifact_protected'").fetchone()[0] >= 1


async def notices_case(root: Path) -> None:
    from runtime.daemon.shared_artifacts_notices import SharedArtifactNotices, NoticeUnavailable
    from cli.commands.threads import cmd_threads_show
    with patch('runtime.infrastructure.shared_artifact_service._utc', return_value=NOW):
        async with fixture(root, workers=('worker','engineering_manager')) as (org,client):
            db = org.db
            db.insert_thread(ThreadRecord(id='THR-origin',subject='Synthetic origin'))
            response = await client.post(PREFIX+'/artifacts',params=dict(agent='worker',thread_id='THR-origin',purpose='Persistent notice source',retention='default'),files={'file':('visible-source.bin',b'visible source')})
            assert response.status_code == 200
            svc = db._shared_artifact_service
            row = db.get_shared_artifact('visible-source.bin')
            notices = SharedArtifactNotices(org)
            def deliver(when: object, item: dict = row) -> int:
                with db.shared_artifact_transaction() as conn:
                    return notices.deliver_file(conn,item,now=when,mode='preview',deadline=time.monotonic()+60)
            assert deliver(NOW+timedelta(days=82)) == 0
            before = snapshot(org)
            assert deliver(NOW+timedelta(days=83)) == 1
            assert snapshot(org) == before, 'notices must not mutate lifecycle or bytes'
            notices = SharedArtifactNotices(org)
            assert deliver(NOW+timedelta(days=85)) == 0
            receipt = db._conn.execute("SELECT * FROM shared_artifact_notice_receipts WHERE notice_kind='expiry'").fetchone()
            thread_id = receipt['destination_thread_id']
            inbox = await client.get(PREFIX+'/threads',params={'status':'open'})
            assert any(t['thread_id']==thread_id and 'worker' in t['participants'] for t in inbox.json()['threads'])
            detail = await client.get(PREFIX+'/threads/'+thread_id)
            assert detail.status_code == 200
            payload = detail.json()['messages'][0]['system_payload']
            assert payload['late_delivery'] is False
            required = ('file_name','expiry_date','owner_kind','owner','routing','purpose','recovery_deadline','renew_command','restore_command')
            assert all(key in payload for key in required)
            assert not ({'kind_tag','event'} & payload.keys())
            assert payload['file_name']=='visible-source.bin' and payload['owner']=='worker'
            output = StringIO()
            transport = SimpleNamespace(get=lambda path: detail)
            with patch('cli.commands.threads.OpcClient.from_env',return_value=transport), patch('cli.commands.threads._shared._fetch_available_orgs',return_value=['alpha']), patch('cli.commands.threads.identities.labels',return_value={}), redirect_stdout(output):
                cmd_threads_show(SimpleNamespace(org='alpha',thread_id=thread_id,json=False))
            visible = output.getvalue()
            assert all(word in visible for word in ('visible-source.bin',row['expires_at'],'worker','routing','happyranch artifacts renew','happyranch artifacts restore'))
            print('CLI SHOW PRIVATE NOTICE\n'+visible)
            # Renewal changes the receipt identity; actual late timestamp remains truthful.
            svc.clock = lambda: NOW+timedelta(days=85)
            svc.renew(row['name'],agent='worker',continuing_use='continued design review',expires_at=(NOW+timedelta(days=100)).isoformat())
            row.update(db.get_shared_artifact(row['name']))
            assert deliver(NOW+timedelta(days=95)) == 1
            latest = db._conn.execute('SELECT system_payload_json FROM thread_messages WHERE thread_id=? ORDER BY seq DESC LIMIT 1',(thread_id,)).fetchone()
            assert json.loads(latest[0])['late_delivery'] is True
            assert json.loads(latest[0])['delivered_at'] == (NOW+timedelta(days=95)).isoformat()
            # Synthetic recorded accountable routes, with real persisted inbox messages.
            for index,(owner_kind,owner,label) in enumerate((('human','founder','human'),('agent','retired-agent','retired'),(None,None,'unknown'))):
                item = dict(row,expires_at=(NOW+timedelta(days=101+index)).isoformat(),owner_kind=owner_kind,owner=owner,owner_agent_id='manager')
                assert deliver(NOW+timedelta(days=120),item) == 1
                r = db._conn.execute("SELECT destination_thread_id,payload_json FROM shared_artifact_notice_receipts WHERE expires_at=?",(item['expires_at'],)).fetchone()
                p = json.loads(r['payload_json'])
                assert p['routing']['agent']=='manager' and 'recorded_accountable_manager' in p['routing']['reason']
                assert db.is_thread_participant(r['destination_thread_id'],'manager')
                assert not db.is_thread_participant(r['destination_thread_id'],'founder')
            provisional = await client.post(PREFIX+'/artifacts',params={'agent':'founder'},files={'file':('unbound.bin',b'provisional')})
            assert provisional.status_code==200
            item = db.get_shared_artifact('unbound.bin')
            before = snapshot(org)
            assert deliver(datetime.fromisoformat(item['created_at'])+timedelta(hours=24),item)==1
            assert snapshot(org)==before
            r=db._conn.execute("SELECT payload_json FROM shared_artifact_notice_receipts WHERE name='unbound.bin'").fetchone()
            assert json.loads(r[0])['expiry_date']==(datetime.fromisoformat(item['created_at'])+timedelta(days=90)).isoformat()
            assert json.loads(r[0])['routing']['agent']=='engineering_manager'
            # Audit failure rolls back message, thread creation and receipt.
            failed = dict(row,expires_at=(NOW+timedelta(days=130)).isoformat())
            count=db._conn.execute('SELECT COUNT(*) FROM thread_messages').fetchone()[0]
            with patch.object(db,'insert_audit_log_uncommitted',side_effect=RuntimeError('synthetic_notice_audit_failure')):
                try:
                    deliver(NOW+timedelta(days=131),failed)
                    raise AssertionError('audit failure missing')
                except RuntimeError:
                    pass
            assert db._conn.execute('SELECT COUNT(*) FROM thread_messages').fetchone()[0]==count
            with patch.object(notices,'_active',return_value=False):
                with db.shared_artifact_transaction() as conn:
                    try:
                        notices.deliver_file(conn,failed,now=NOW+timedelta(days=131),mode='off',deadline=time.monotonic()+60)
                    except NoticeUnavailable:
                        pass
            assert db._conn.execute('SELECT delivery_state FROM shared_artifact_notice_receipts WHERE expires_at=?',(failed['expires_at'],)).fetchone()[0]=='pending'
            # Actual latch and all messages share one transaction across restart/rearm.
            with db.shared_artifact_transaction() as conn:
                conn.execute("INSERT INTO shared_artifact_operations(operation_id,name,generation,operation,phase,reserved_bytes,created_at,updated_at) VALUES('synthetic-reservation','pending.bin',1,'put','reserved',4000000000,?,?)",(NOW.isoformat(),NOW.isoformat()))
            for reserve,expected in ((4000000000,True),(4000000000,False),(0,False),(4000000000,True)):
                notices=SharedArtifactNotices(org)
                with db.shared_artifact_transaction() as conn:
                    conn.execute("UPDATE shared_artifact_operations SET reserved_bytes=? WHERE operation_id='synthetic-reservation'",(reserve,))
                    quota=svc.quota_crossing_uncommitted(conn)
                    assert quota['crossing'] is expected
                    seqs=notices.deliver_quota(conn,[db.get_shared_artifact('visible-source.bin')],quota=quota,now=NOW,mode='off',deadline=time.monotonic()+60)
                    assert bool(seqs) is expected
            assert db._conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='artifact_quota_alert'").fetchone()[0]==2
            print('PASS notices: private inbox/real CLI full JSON;exact due/replay/renewal/late;human-retired-unknown manager;24h timeout;pending/audit rollback;quota crossing-repeat-below-recross')


async def main(case: str) -> None:
    with tempfile.TemporaryDirectory(prefix='shared-artifact-B-') as directory:
        root = Path(directory)
        if case in ('manual-delete', 'manual-delete-default'):
            await manual_delete_case(root / 'default', mode='preview')
            await manual_delete_case(root / 'off', mode='off')
        if case in ('manual-delete', 'manual-delete-enforce'):
            await manual_delete_case(root / 'enforce', mode='enforce')
            await manual_delete_case(root / 'live', mode='enforce', live=True)
        if case == 'notices':
            await notices_case(root/'notices')
        if case == 'references':
            await references_case(root/'references')
        if case == 'quota-chunks':
            await quota_chunks_case(root/'quota')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=(
        'manual-delete', 'manual-delete-default', 'manual-delete-enforce',
        'quota-chunks', 'references', 'notices',
    ), default='manual-delete')
    asyncio.run(main(parser.parse_args().case))
