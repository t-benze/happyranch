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
import threading
from zoneinfo import ZoneInfo
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
                'reviewer_agents: [other]\nshared_artifacts:\n  cleanup_mode: '+repr(mode)+'\n')
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
            # Live parent/direct child are depth0; two explicit handoffs are
            # protected. A third hop must not load task or result payloads.
            with db.shared_artifact_transaction() as conn:
                conn.execute("UPDATE tasks SET brief='Read TASK-104' WHERE id='TASK-103'")
            for task_id, text in (('TASK-104', 'Read TASK-105'),
                                  ('TASK-105', 'Read TASK-106'),
                                  ('TASK-106', '[artifact:../invalid.bin]')):
                db.insert_task(TaskRecord(id=task_id,status=TaskStatus.COMPLETED,
                                          brief=text,assigned_agent='worker'))
            with db.shared_artifact_transaction() as conn:
                conn.execute("INSERT INTO task_results(task_id,agent,session_id,decision_json,created_at) "
                             "VALUES('TASK-106','worker','beyond-depth','{',?)",(NOW.isoformat(),))
            reads = []
            db._conn.set_trace_callback(reads.append)
            try:
                check('absent','handoff-depth3-not-loaded')
            finally:
                db._conn.set_trace_callback(None)
            payload_reads = [sql for sql in reads if sql.startswith((
                'SELECT id,brief,final_output_summary FROM tasks WHERE',
                'SELECT task_id,output_summary,decision_json,risks_flagged FROM task_results WHERE'))]
            assert payload_reads and all("'TASK-106'" not in sql for sql in payload_reads), payload_reads
            with db.shared_artifact_transaction() as conn:
                conn.execute("UPDATE tasks SET brief='[artifact:source.bin] Read TASK-106' WHERE id='TASK-105'")
            check('live','handoff-depth2-protects')
            # The same malformed reference/payload is still indeterminate
            # when it moves inside the selected set.
            with db.shared_artifact_transaction() as conn:
                conn.execute("UPDATE tasks SET brief='[artifact:../invalid.bin]' WHERE id='TASK-105'")
            check('indeterminate','handoff-depth2-malformed-reference')
            with db.shared_artifact_transaction() as conn:
                conn.execute("UPDATE tasks SET brief='No artifacts' WHERE id='TASK-105'")
                conn.execute("UPDATE task_results SET task_id='TASK-105' WHERE session_id='beyond-depth'")
            check('indeterminate','handoff-depth2-malformed-payload')
            with db.shared_artifact_transaction() as conn:
                conn.execute("DELETE FROM task_results WHERE session_id='beyond-depth'")
            with db.shared_artifact_transaction() as conn:
                conn.execute("UPDATE tasks SET status='completed',brief='Done' WHERE id IN ('TASK-101','TASK-102','TASK-103')")
            with db.shared_artifact_transaction() as conn:
                conn.execute("UPDATE tasks SET status='pending',brief='Read TASK-999999' WHERE id='TASK-100'")
                conn.execute("INSERT INTO task_results(task_id,agent,session_id,created_at) VALUES('TASK-100','worker','missing-mention',?)",(NOW.isoformat(),))
            for table, field in (('tasks','brief'), ('tasks','final_output_summary'),
                                 ('task_results','output_summary'), ('task_results','decision_json'),
                                 ('task_results','risks_flagged')):
                for existing in (False, True):
                    text = 'Read TASK-999999' + (' and TASK-102' if existing else '')
                    value = json.dumps({'handoff':text}) if field == 'decision_json' else (
                        json.dumps([text]) if field == 'risks_flagged' else text)
                    with db.shared_artifact_transaction() as conn:
                        conn.execute("UPDATE tasks SET brief='No artifacts' WHERE id='TASK-100'")
                        conn.execute("UPDATE tasks SET brief='Need [artifact:source.bin]' WHERE id='TASK-102'")
                        conn.execute(f"UPDATE {table} SET {field}=? WHERE " + (
                            "id='TASK-100'" if table == 'tasks' else "session_id='missing-mention'"), (value,))
                    check('live' if existing else 'absent', 'missing-mention-'+field+('-existing' if existing else ''))
                    with db.shared_artifact_transaction() as conn:
                        conn.execute(f"UPDATE {table} SET {field}=? WHERE " + (
                            "id='TASK-100'" if table == 'tasks' else "session_id='missing-mention'"),
                            ('No artifacts' if field == 'brief' else None,))
            with db.shared_artifact_transaction() as conn:
                conn.execute("UPDATE tasks SET status='unknown',brief='No artifacts' WHERE id='TASK-100'")
            check('indeterminate','unknown-task-status')
            with db.shared_artifact_transaction() as conn:
                conn.execute("UPDATE tasks SET status='in_progress',brief='source.bin',block_kind='delegated' WHERE id='TASK-100'")
            check('live','delegated-wait')
            with db.shared_artifact_transaction() as conn:
                conn.execute("UPDATE tasks SET brief='No artifacts',final_output_summary='source.bin',block_kind='blocked_on_job' WHERE id='TASK-100'")
            check('live','job-wait-final-summary')
            with db.shared_artifact_transaction() as conn:
                conn.execute("UPDATE tasks SET final_output_summary=NULL WHERE id='TASK-100'")
                conn.execute("INSERT INTO task_results(task_id,agent,session_id,created_at) VALUES('TASK-100','worker','synthetic-reference',?)",(NOW.isoformat(),))
            for field,value in (('output_summary','source.bin'),('decision_json',json.dumps({'handoff':['source.bin']})),('risks_flagged',json.dumps(['source.bin']))):
                with db.shared_artifact_transaction() as conn:
                    conn.execute(f"UPDATE task_results SET {field}=? WHERE session_id='synthetic-reference'",(value,))
                check('live','result-'+field)
                with db.shared_artifact_transaction() as conn:
                    conn.execute(f"UPDATE task_results SET {field}=NULL WHERE session_id='synthetic-reference'")
            with db.shared_artifact_transaction() as conn:
                conn.execute("UPDATE task_results SET decision_json='{' WHERE session_id='synthetic-reference'")
            check('indeterminate','malformed-result-json')
            with db.shared_artifact_transaction() as conn:
                conn.execute("UPDATE task_results SET decision_json='42' WHERE session_id='synthetic-reference'")
            check('indeterminate','malformed-result-root')
            with db.shared_artifact_transaction() as conn:
                conn.execute("UPDATE task_results SET decision_json=NULL WHERE session_id='synthetic-reference'")
                conn.execute("UPDATE tasks SET status='completed',brief='Done',block_kind=NULL WHERE id='TASK-100'")
            for text,expected,label in (
                ('[artifact:source.bin]','live','explicit'),
                ('`source.bin`','live','exact-token'),
                ('Keep source.bin.','live','markdown-punctuation'),
                ('source.bin:','live','colon-punctuation'),
                ('source.bin?','live','question-punctuation'),
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
            from runtime.models import ThreadAttachment
            with db.shared_artifact_transaction() as conn:
                conn.execute("UPDATE threads SET status='open' WHERE id='THR-ref'")
                conn.execute("DELETE FROM thread_messages WHERE thread_id='THR-ref'")
            db.append_thread_message(thread_id='THR-ref',speaker='worker',kind=ThreadMessageKind.MESSAGE,
                attachments=[ThreadAttachment(artifact_name='source.bin',display_name='source.bin',uploaded_by='worker')])
            check('live','actual-shared-attachment')
            with db.shared_artifact_transaction() as conn:
                conn.execute("UPDATE threads SET status='archived' WHERE id='THR-ref'")

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
            with db.shared_artifact_transaction() as conn:
                used,reserved=db._shared_artifact_service.reconcile_usage_uncommitted(conn)
                assert used==len(b'synthetic reference source') and reserved==0
            stream,_metadata=db._shared_artifact_service.open_download(row['name'])
            with stream:
                assert stream.read()==b'synthetic reference source'
            assert db._conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='artifact_protected'").fetchone()[0] >= 1


async def handoff_paths_case(root: Path) -> None:
    """Task path qualification at the real proof, replacement and preview boundaries."""
    from runtime.daemon.shared_artifacts_scheduler import sweep
    root.mkdir(parents=True)
    cases = (
        ('foreign-relative', '/orgs/demo/tasks/TASK-1/cancel', 200),
        ('foreign-api', '/api/v1/orgs/demo/tasks/TASK-1/cancel', 200),
        ('foreign-absolute', 'https://synthetic.test/orgs/demo/tasks/TASK-1/cancel', 200),
        ('foreign-absolute-api', 'https://synthetic.test/api/v1/orgs/demo/tasks/TASK-1/cancel', 200),
        ('same-relative', '/orgs/alpha/tasks/TASK-1/cancel', 200),
        ('same-api', '/api/v1/orgs/alpha/tasks/TASK-1/cancel', 200),
        ('same-absolute', 'https://synthetic.test/orgs/alpha/tasks/TASK-1/cancel', 200),
        ('same-absolute-api', 'https://synthetic.test/api/v1/orgs/alpha/tasks/TASK-1/cancel', 200),
        ('bare', 'TASK-1', 200),
        ('foreign-and-bare', '/orgs/demo/tasks/TASK-1/cancel then TASK-1', 200),
    )
    forms = ('/orgs/{slug}/tasks/{task}/cancel',
             '/api/v1/orgs/{slug}/tasks/{task}/cancel',
             'https://synthetic.test/orgs/{slug}/tasks/{task}/cancel',
             'https://synthetic.test/api/v1/orgs/{slug}/tasks/{task}/cancel')
    cases += tuple(
        (f'{label}-{index}', form.format(slug=slug, task=task), status)
        for label, slug, task, status in (
            ('plain-live', 'alpha', 'TASK-200', 409),
            ('encoded-live', '%61lpha', 'TASK-200', 409),
            ('encoded-missing', '%61lpha', 'TASK-1', 200),
            ('malformed-percent', '%6glpha', 'TASK-1', 503),
            ('trailing-percent', 'alpha%', 'TASK-1', 503),
            ('invalid-utf8', '%ff', 'TASK-1', 503),
            ('encoded-slash', 'alpha%2fdemo', 'TASK-1', 503),
            ('invalid-slug', 'demo%5Forg', 'TASK-1', 503),
            ('overlong-slug', 'd'*41, 'TASK-1', 503),
            ('double-encoded-same', '%2561lpha', 'TASK-200', 409),
            ('double-encoded-foreign', '%2564emo', 'TASK-1', 503),
            ('encoded-foreign', '%64emo', 'TASK-1', 200),
        )
        for index, form in enumerate(forms)
    )
    with patch('runtime.infrastructure.shared_artifact_service._utc', return_value=NOW):
        for label, text, expected_status in cases:
            complete = expected_status != 503
            async with fixture(root/label) as (org, client):
                db = org.db
                install_reference_seams(org)
                db.insert_thread(ThreadRecord(id='THR-path', subject='Synthetic path reference'))
                params = dict(agent='worker', thread_id='THR-path',
                              purpose='Persistent path acceptance', retention='default')
                response = await client.post(PREFIX+'/artifacts', params=params,
                    files={'file': ('path.bin', b'original path bytes')})
                assert response.status_code == 200, response.text
                db._shared_artifact_service.clock = lambda: NOW
                db.insert_task(TaskRecord(id='TASK-100', brief='Live parent', assigned_agent='worker'))
                db.insert_task(TaskRecord(id='TASK-101', status=TaskStatus.COMPLETED,
                    parent_task_id='TASK-100', brief='Direct child', assigned_agent='worker'))
                db.insert_task(TaskRecord(id='TASK-200', status=TaskStatus.COMPLETED,
                    brief='Keep [artifact:path.bin]', assigned_agent='worker'))
                # The real route consumes a single-decoded ASGI path. In
                # particular, a second decode must not resolve TASK-200.
                if text.startswith('/api/v1/') and label.startswith((
                    'plain-live-', 'encoded-live-', 'encoded-missing-', 'double-encoded-')):
                    detail = await client.get(text.removesuffix('/cancel'))
                    resolves = expected_status == 409 and not label.startswith('double-encoded-')
                    assert detail.status_code == (200 if resolves else 404), (label, detail.text)
                    if resolves:
                        assert detail.json()['task']['task_id'] == 'TASK-200', (label, detail.text)
                with db.shared_artifact_transaction() as conn:
                    conn.execute(
                        'INSERT INTO task_results(task_id,agent,session_id,output_summary,created_at) '
                        'VALUES(?,?,?,?,?)', ('TASK-101', 'worker', 'path-result', 'POST '+text, NOW.isoformat()))
                    assert conn.execute("SELECT id FROM tasks WHERE id='TASK-1'").fetchone() is None
                before = snapshot(org)
                response = await client.post(PREFIX+'/artifacts', params=params,
                    files={'file': ('path.bin', b'replacement path bytes')})
                assert response.status_code == expected_status, (label, expected_status, response.status_code, response.text)
                if not complete:
                    assert response.json()['detail']['code'] == 'artifact_reference_check_unavailable', response.text
                with db.shared_artifact_transaction() as conn:
                    row = db.get_shared_artifact('path.bin')
                    outcome = reference_outcomes(org, conn, [row], deadline=time.monotonic()+60)
                    if expected_status == 409:
                        assert outcome['path.bin', row['generation']][1] == ['TASK-200'], (label, outcome)
                    assert outcome['path.bin', row['generation']][0] == (
                        'absent' if expected_status == 200 else 'live' if expected_status == 409 else 'indeterminate'), (label, outcome)
                result = sweep(org, 'handoff-path-'+label)
                assert result['complete'] is complete, (label, result)
                assert bool(result.get('reference_incomplete', False)) is (not complete), (label, result)
                assert result['effective_mode'] == 'preview', result
                started = db._conn.execute('SELECT rollout_started_at FROM shared_artifact_org_state').fetchone()[0]
                assert started == (NOW.isoformat() if complete else None), (label, started)
                if expected_status != 200:
                    assert snapshot(org) == before, label
                    assert result['protected'] == 1, result
                expected = b'replacement path bytes' if expected_status == 200 else b'original path bytes'
                assert (org.root/'artifacts'/'path.bin').read_bytes() == expected, label
                print(f'PASS handoff-paths:{label}:replacement{response.status_code}:preview_complete={complete}')


async def history_case(root: Path) -> None:
    """Closed production-sized history must not consume the live text budget."""
    from runtime.daemon.shared_artifacts_scheduler import sweep
    with patch('runtime.infrastructure.shared_artifact_service._utc', return_value=NOW):
        async with fixture(root) as (org, client):
            db = org.db
            install_reference_seams(org)
            db.insert_thread(ThreadRecord(id='THR-history', subject='Synthetic history'))
            params = dict(agent='worker', thread_id='THR-history',
                          purpose='Persistent history acceptance', retention='default')
            names = ('free.bin', 'other-free.bin', 'live.bin', 'child.bin', 'handoff.bin')
            for name in names:
                response = await client.post(PREFIX+'/artifacts', params=params,
                    files={'file': (name, b'original history acceptance bytes')})
                assert response.status_code == 200, response.text
            db._shared_artifact_service.clock = lambda: NOW
            # Actual TEXT storage, not sparse files or an increased scanner cap.
            brief = 'Closed [artifact:free.bin] '.ljust(4096, 'x')
            summary = 'Closed [artifact:other-free.bin] '.ljust(6400, 'x')
            with db.shared_artifact_transaction() as conn:
                conn.executemany(
                    'INSERT INTO tasks(id,status,brief,created_at,updated_at) VALUES(?,?,?,?,?)',
                    ((f'TASK-{i}', 'completed', brief, NOW.isoformat(), NOW.isoformat())
                     for i in range(1, 19001)))
                conn.executemany(
                    'INSERT INTO task_results(task_id,agent,session_id,output_summary,created_at) '
                    'VALUES(?,?,?,?,?)',
                    ((f'TASK-{i % 19000 + 1}', 'worker', f'history-{i}', summary, NOW.isoformat())
                     for i in range(34000)))
                sizes = conn.execute(
                    'SELECT (SELECT COUNT(*) FROM tasks), (SELECT COUNT(*) FROM task_results), '
                    '(SELECT SUM(length(CAST(brief AS BLOB))) FROM tasks) + '
                    '(SELECT SUM(length(CAST(output_summary AS BLOB))) FROM task_results)').fetchone()
                assert tuple(sizes) == (19000, 34000, 295424000), tuple(sizes)
            for task_id, status, parent, text in (
                ('TASK-900000', TaskStatus.PENDING, None, '[artifact:live.bin]'),
                ('TASK-900001', TaskStatus.IN_PROGRESS, None, 'Running synthetic work'),
                ('TASK-900002', TaskStatus.ESCALATED, None, 'Escalated synthetic work'),
                ('TASK-900003', TaskStatus.COMPLETED, 'TASK-900000', 'Read TASK-900004'),
                ('TASK-900004', TaskStatus.COMPLETED, None, 'Read TASK-900005'),
                ('TASK-900005', TaskStatus.COMPLETED, None, 'Cycle TASK-900004'),
            ):
                db.insert_task(TaskRecord(id=task_id, status=status, parent_task_id=parent,
                                          brief=text, assigned_agent='worker'))
            with db.shared_artifact_transaction() as conn:
                conn.executemany(
                    'INSERT INTO task_results(task_id,agent,session_id,output_summary,created_at) '
                    'VALUES(?,?,?,?,?)',
                    ((task_id, 'worker', 'live-'+task_id, text, NOW.isoformat())
                     for task_id, text in (('TASK-900003', '[artifact:child.bin]'),
                                           ('TASK-900005', '[artifact:handoff.bin]'))))
            started = time.monotonic()
            for name in names:
                response = await client.post(PREFIX+'/artifacts', params=params,
                    files={'file': (name, b'replacement history acceptance bytes')})
                expected = 200 if name in ('free.bin', 'other-free.bin') else 409
                assert response.status_code == expected, (name, response.status_code, response.text)
                if expected == 409:
                    assert response.json()['detail']['code'] == 'artifact_live_dependency', response.text
                    assert (org.root/'artifacts'/name).read_bytes() == b'original history acceptance bytes'
                else:
                    assert (org.root/'artifacts'/name).read_bytes() == b'replacement history acceptance bytes'
            result = sweep(org, 'production-history', deadline=started+60)
            assert result['complete'] and not result.get('reference_incomplete', False), result
            assert result['effective_mode'] == 'preview' and result['protected'] == 3, result
            assert db._conn.execute('SELECT rollout_started_at FROM shared_artifact_org_state').fetchone()[0] == NOW.isoformat()
            elapsed = time.monotonic()-started
            assert elapsed < 60 and not result['deadline_exhausted'], result
            with db.shared_artifact_transaction() as conn:
                proof = ReferenceProof(conn, slug=org.slug, store=db._shared_artifact_service.store,
                                       deadline=started+60)
                assert proof.outcome(db.get_shared_artifact('free.bin'))[0] == 'absent'
                for name, source in (('live.bin', 'TASK-900000'), ('child.bin', 'TASK-900003'),
                                     ('handoff.bin', 'TASK-900005')):
                    assert proof.outcome(db.get_shared_artifact(name)) == ('live', [source])
                metrics = dict(rows_scanned=proof.rows, bytes_scanned=proof.bytes)
            print(json.dumps(dict(case='history', closed_tasks=sizes[0], historical_results=sizes[1],
                historical_text_bytes=sizes[2], elapsed_seconds=time.monotonic()-started,
                sweep=result, **metrics)), flush=True)
            print('PASS history:19000 closed tasks;34000 results;295424000 historical text bytes;'
                  'unreferenced replacement200;live/child/handoff409;complete preview;rollout started;shared60s')


async def live_cap_case(root: Path) -> None:
    """A genuinely oversized live closure still protects bytes and blocks rollout."""
    from runtime.daemon.shared_artifacts_scheduler import sweep
    with patch('runtime.infrastructure.shared_artifact_service._utc', return_value=NOW):
        async with fixture(root) as (org, client):
            db = org.db
            install_reference_seams(org)
            db.insert_thread(ThreadRecord(id='THR-live-cap', subject='Synthetic live cap'))
            params = dict(agent='worker', thread_id='THR-live-cap',
                          purpose='Persistent live cap acceptance', retention='default')
            response = await client.post(PREFIX+'/artifacts', params=params,
                files={'file': ('capped.bin', b'held live cap bytes')})
            assert response.status_code == 200, response.text
            db._shared_artifact_service.clock = lambda: NOW
            # Same recognized handoff closure as ordinary scanning, over32MiB of
            # valid live result text. No patched production bound or parser fault.
            db.insert_task(TaskRecord(id='TASK-800000', brief='Read TASK-800001', assigned_agent='worker'))
            db.insert_task(TaskRecord(id='TASK-800001', status=TaskStatus.COMPLETED,
                                      brief='Required synthetic handoff', assigned_agent='worker'))
            with db.shared_artifact_transaction() as conn:
                conn.executemany(
                    'INSERT INTO task_results(task_id,agent,session_id,output_summary,created_at) '
                    'VALUES(?,?,?,?,?)',
                    (('TASK-800001', 'worker', f'live-cap-{i}', 'x'*(1024*1024), NOW.isoformat())
                     for i in range(33)))
            before = snapshot(org)
            response = await client.post(PREFIX+'/artifacts', params=params,
                files={'file': ('capped.bin', b'must not replace')})
            assert response.status_code == 503, response.text
            assert response.json()['detail']['code'] == 'artifact_reference_check_unavailable', response.text
            result = sweep(org, 'live-closure-over-cap')
            assert not result['complete'] and result['reference_incomplete'], result
            assert result['protected'] == 1 and result['used_bytes'] == len(b'held live cap bytes'), result
            assert db._conn.execute('SELECT rollout_started_at FROM shared_artifact_org_state').fetchone()[0] is None
            assert snapshot(org) == before, 'cap refusal changed lifecycle or bytes'
            assert (org.root/'artifacts'/'capped.bin').read_bytes() == b'held live cap bytes'
            audits = db._conn.execute(
                "SELECT payload FROM audit_log WHERE task_id='artifact:capped.bin' AND action='artifact_protected'").fetchall()
            assert audits and all(json.loads(r[0])['reason'] == 'dependency_check_unavailable' for r in audits)
            print('PASS live-cap:34603008 live handoff/result bytes;replacement503;'
                  'incomplete sweep;protected/counting/audit;rollout unset;bytes unchanged')


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
            late_bound = await client.post(PREFIX+'/artifacts',params={'agent':'founder'},files={'file':('bound-after-timeout.bin',b'provisional then bound')})
            assert late_bound.status_code == 200
            with patch('runtime.infrastructure.shared_artifact_service._utc',return_value=NOW+timedelta(hours=25)):
                overdue = await client.get(PREFIX+'/artifacts/bound-after-timeout.bin')
                assert overdue.status_code == 200
            from runtime.models import ThreadAttachment
            db.append_thread_message(thread_id='THR-origin',speaker='worker',kind=ThreadMessageKind.MESSAGE,
                attachments=[ThreadAttachment(artifact_name='bound-after-timeout.bin',display_name='bound-after-timeout.bin',uploaded_by='founder')])
            bound=db.get_shared_artifact('bound-after-timeout.bin')
            assert bound['state']=='active' and bound['expires_at'] != (NOW+timedelta(days=90)).isoformat()
            assert deliver(NOW+timedelta(hours=25),bound)==1
            queued=db._conn.execute("SELECT delivery_state,payload_json FROM shared_artifact_notice_receipts WHERE name='bound-after-timeout.bin' AND notice_kind='attribution_timeout'").fetchone()
            assert queued['delivery_state']=='delivered'
            assert json.loads(queued['payload_json'])['expiry_date']==(NOW+timedelta(days=90)).isoformat()
            assert json.loads(queued['payload_json'])['current_expiry']==bound['expires_at']
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


async def sweep_case(root: Path) -> None:
    from runtime.daemon.shared_artifacts_scheduler import sweep, finish_delete, current_window
    from runtime.models import ThreadAttachment
    from runtime.infrastructure.shared_artifact_service import SharedArtifactError, SharedArtifactService
    from runtime.infrastructure.artifact_store import ArtifactStore
    from runtime.infrastructure.database import Database
    with patch('runtime.infrastructure.shared_artifact_service._utc', return_value=NOW):
        async with fixture(root) as (org,client):
            db=org.db
            db.insert_thread(ThreadRecord(id='THR-origin',subject='Lifecycle acceptance'))
            for name in ('auto.bin','expire.bin','crash.bin','replacement.bin','fence.bin','hash.bin','symlink.bin'):
                response=await client.post(PREFIX+'/artifacts',params=dict(agent='worker',thread_id='THR-origin',purpose='Persistent source',retention='default',expires_at=(NOW+timedelta(hours=1)).isoformat()),files={'file':(name,b'synthetic lifecycle bytes')})
                assert response.status_code==200
            svc=db._shared_artifact_service
            when=[NOW+timedelta(hours=2)]
            svc.clock=lambda:when[0]
            install_reference_seams(org)
            before=snapshot(org)
            result=sweep(org,'preview-1')
            assert result['complete'] and result['effective_mode']=='preview',result
            assert snapshot(org)==before
            assert db._conn.execute('SELECT rollout_started_at FROM shared_artifact_org_state').fetchone()[0]==when[0].isoformat()
            assert sweep(org,'preview-1')['outcome']=='already_serviced'
            config=org.root/'org'/'config.yaml'
            config.write_text('reviewer_agents: [other]\nshared_artifacts: {cleanup_mode: enforce}\n')
            result=sweep(org,'premature')
            assert result['effective_mode']=='preview' and snapshot(org)==before
            with db.shared_artifact_transaction() as conn:
                conn.execute("UPDATE shared_artifact_org_state SET rollout_started_at=?,preview_acceptance='synthetic',inventory_review_id='synthetic',source_protection_verified=1",((NOW-timedelta(days=8)).isoformat(),))
            when[0]=NOW+timedelta(days=8)
            db.append_thread_message(thread_id='THR-origin',speaker='worker',kind=ThreadMessageKind.MESSAGE,body_markdown='[artifact:expire.bin]')
            result=sweep(org,'live-hold')
            assert db.get_shared_artifact('expire.bin')['state']=='active'
            assert db.get_shared_artifact('crash.bin')['state']=='recoverable'
            assert db.get_shared_artifact('crash.bin')['recoverable_until']==(when[0]+timedelta(days=7)).isoformat()
            with db.shared_artifact_transaction() as conn:
                conn.execute("UPDATE threads SET status='archived' WHERE id='THR-origin'")
            when[0]+=timedelta(days=1)
            assert sweep(org,'hold-ended')['expired']==1
            assert db.get_shared_artifact('expire.bin')['recoverable_until']==(when[0]+timedelta(days=7)).isoformat()
            when[0]=NOW+timedelta(days=15)-timedelta(microseconds=1)
            assert sweep(org,'before-deadline')['deleted']==0
            when[0]=NOW+timedelta(days=15)
            db.insert_thread(ThreadRecord(id='THR-auto',subject='Hold other acceptance files'))
            db.append_thread_message(thread_id='THR-auto',speaker='worker',kind=ThreadMessageKind.MESSAGE,
                body_markdown=' '.join('[artifact:'+n+']' for n in ('crash.bin','replacement.bin','fence.bin','hash.bin','symlink.bin')))
            automated=sweep(org,'actual-recovery-deadline')
            assert automated['deleted']==1 and db.get_shared_artifact('auto.bin')['state']=='deleted',automated
            assert not (svc.store.root/'auto.bin').exists()
            with db.shared_artifact_transaction() as conn:
                conn.execute("UPDATE threads SET status='archived' WHERE id='THR-auto'")
            def prepare(name: str, operation_id: str) -> None:
                with db.shared_artifact_transaction() as conn:
                    row=db._shared_artifact_uncommitted(conn,name)
                    outcome=reference_outcomes(org,conn,[row],deadline=time.monotonic()+60)[name,row['generation']][0]
                    svc.prepare_delete_uncommitted(conn,name=name,generation=row['generation'],expected_revision=row['revision'],reference_outcome=outcome,effective_mode='enforce',operation_id=operation_id,deadline=time.monotonic()+60)
            prepare('crash.bin','synthetic-crash-delete')
            audit=db.insert_audit_log_uncommitted
            def fail_final(**kwargs: object) -> None:
                if kwargs['action']=='artifact_delete':
                    import sqlite3
                    raise sqlite3.OperationalError('synthetic_final_audit_failure')
                return audit(**kwargs)
            with patch.object(db,'insert_audit_log_uncommitted',side_effect=fail_final):
                try:
                    finish_delete(org,'synthetic-crash-delete',deadline=time.monotonic()+60)
                    raise AssertionError('final audit failure missing')
                except Exception as exc:
                    assert 'synthetic_final_audit_failure' in str(exc)
            assert not (svc.store.root/'crash.bin').exists()
            assert db.get_shared_artifact('crash.bin')['state']=='recoverable'
            assert db._conn.execute("SELECT phase FROM shared_artifact_operations WHERE operation_id='synthetic-crash-delete'").fetchone()[0]=='issued'
            # New SQLite connection + new service reconstructs only durable facts.
            recovered=Database(Path(db._conn.execute('PRAGMA database_list').fetchone()[2]))
            recovered_org=SimpleNamespace(db=recovered,root=org.root,slug=org.slug,teams=org.teams)
            SharedArtifactService(recovered,ArtifactStore(svc.store.root),clock=lambda:when[0])
            install_reference_seams(recovered_org)
            try:
                assert finish_delete(recovered_org,'synthetic-crash-delete',deadline=time.monotonic()+60)
                assert recovered.get_shared_artifact('crash.bin')['state']=='deleted'
                assert recovered._conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='artifact_delete' AND task_id='artifact:crash.bin'").fetchone()[0]==1
            finally:
                recovered.close()
            # Same-hash inode replacement after intent is held, never re-unlinked.
            prepare('replacement.bin','synthetic-replacement-delete')
            path=svc.store.root/'replacement.bin'
            alternate=svc.store.root/'replacement-new.bin'
            alternate.write_bytes(path.read_bytes())
            alternate.replace(path)
            assert not finish_delete(org,'synthetic-replacement-delete',deadline=time.monotonic()+60)
            assert path.exists() and db.get_shared_artifact('replacement.bin')['state']=='recoverable'
            prepare('hash.bin','synthetic-hash-delete')
            hash_path=svc.store.root/'hash.bin'
            hash_path.write_bytes(b'changed lifecycle bytes!')
            assert not finish_delete(org,'synthetic-hash-delete',deadline=time.monotonic()+60)
            assert hash_path.exists()
            prepare('symlink.bin','synthetic-symlink-delete')
            symlink_path=svc.store.root/'symlink.bin'
            symlink_path.unlink()
            symlink_path.symlink_to(svc.store.root/'replacement.bin')
            try:
                finish_delete(org,'synthetic-symlink-delete',deadline=time.monotonic()+60)
                raise AssertionError('symlink replacement accepted')
            except SharedArtifactError:
                pass
            assert symlink_path.is_symlink() and path.exists()
            # Reference committed after preparation wins the fresh final fence.
            db.insert_thread(ThreadRecord(id='THR-fence',subject='Concurrent reference'))
            prepare('fence.bin','synthetic-fence-delete')
            db.append_thread_message(thread_id='THR-fence',speaker='worker',kind=ThreadMessageKind.MESSAGE,body_markdown='[artifact:fence.bin]')
            assert not finish_delete(org,'synthetic-fence-delete',deadline=time.monotonic()+60)
            assert (svc.store.root/'fence.bin').exists()
            with db.shared_artifact_transaction() as conn:
                conn.execute("UPDATE threads SET status='archived' WHERE id='THR-fence'")
            entered,release,writer_started,writer_done=(threading.Event() for _ in range(4))
            identity=svc.identity
            def barrier(path: Path, **kwargs: object) -> dict:
                value=identity(path,**kwargs)
                entered.set()
                assert release.wait(5), 'bounded fence release missing'
                return value
            errors=[]
            def attach() -> None:
                writer_started.set()
                try:
                    db.append_thread_message(thread_id='THR-origin',speaker='worker',kind=ThreadMessageKind.MESSAGE,
                        attachments=[ThreadAttachment(artifact_name='fence.bin',display_name='fence.bin',uploaded_by='worker')])
                except SharedArtifactError as exc:
                    errors.append(exc.status)
                finally:
                    writer_done.set()
            with patch.object(svc,'identity',side_effect=barrier):
                task=asyncio.create_task(asyncio.to_thread(finish_delete,org,'synthetic-fence-delete',deadline=time.monotonic()+60))
                assert await asyncio.to_thread(entered.wait,5)
                writer=threading.Thread(target=attach)
                writer.start()
                assert await asyncio.to_thread(writer_started.wait,1)
                assert not await asyncio.to_thread(writer_done.wait,.05), 'reference writer escaped final fence'
                release.set()
                assert await task
                await asyncio.to_thread(writer.join,5)
            assert writer_done.is_set() and errors==[410]
            assert db.get_shared_artifact('fence.bin')['state']=='deleted'
            # Default/off/invalid and deadline exhaustion never retire bytes.
            config.write_text('reviewer_agents: [other]\nshared_artifacts: {cleanup_mode: \"off\"}\n')
            when[0]=NOW+timedelta(days=17)
            assert sweep(org,'off')['effective_mode']=='off'
            assert db.get_shared_artifact('expire.bin')['state']=='recoverable'
            config.write_text('reviewer_agents: [other]\nshared_artifacts: {cleanup_mode: invalid}\n')
            assert sweep(org,'invalid')['effective_mode']=='preview'
            assert db.get_shared_artifact('expire.bin')['state']=='recoverable'
            result=sweep(org,'exhausted',deadline=time.monotonic()-1)
            assert not result['complete'] and result['deadline_exhausted']
            with db.shared_artifact_transaction() as conn:
                deletion=json.loads(conn.execute("SELECT payload FROM audit_log WHERE action='artifact_delete' AND task_id='artifact:fence.bin'").fetchone()[0])
                assert deletion['reclaimed_bytes']==len(b'synthetic lifecycle bytes')
            assert current_window(NOW,ZoneInfo('UTC'))=='2026-11-22T04:00:00+00:00'
            assert current_window(NOW+timedelta(hours=4),ZoneInfo('UTC'))=='2026-11-23T04:00:00+00:00'
            print('PASS sweep:preview/gates/off/invalid/window/budget;live hold ends with fresh7d;full recovery;unlink+tombstone;restart after final-audit failure;inode replacement hold;reference arrival/attachment writer final fence;reclaimed-byte audit')


async def sweep_shape_case(root: Path) -> None:
    from runtime.daemon.shared_artifacts_scheduler import sweep
    async with fixture(root,workers=('worker','engineering_manager')) as (org,client):
        assert (await client.get(PREFIX+'/artifacts')).status_code==200
        svc=org.db._shared_artifact_service
        for index in range(7500):
            with (svc.store.root/f'legacy-{index:05}.bin').open('wb') as stream:
                stream.truncate(226667)
        install_reference_seams(org)
        with org.db.shared_artifact_transaction() as conn:
            conn.execute("INSERT INTO shared_artifact_operations(operation_id,name,generation,operation,phase,reserved_bytes,created_at,updated_at) VALUES('shape-reservation','pending.bin',1,'put','reserved',2299997500,?,?)",(NOW.isoformat(),NOW.isoformat()))
        started=time.monotonic()
        result=sweep(org,'shape-7500',deadline=started+60)
        elapsed=time.monotonic()-started
        assert result['complete'] and result['processed']==7500 and result['legacy']==7500,result
        assert result['used_bytes']==7500*226667
        assert org.db._conn.execute('SELECT COUNT(*) FROM shared_artifact_generations WHERE expires_at IS NOT NULL').fetchone()[0]==0
        assert len(list(svc.store.root.glob('legacy-*.bin')))==7500
        messages=org.db._conn.execute("SELECT system_payload_json FROM thread_messages WHERE kind='system'").fetchall()
        payloads=[json.loads(m[0]) for m in messages]
        assert sum(len(p['files']) for p in payloads)==7500
        assert all(f['action_required']=='reviewed_inventory_classification' and f['renew_command'] is None and f['restore_command'] is None for p in payloads for f in p['files'])
        assert all(len(m[0].encode())<=32*1024 for m in messages)
        assert org.db._conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='artifact_quota_alert'").fetchone()[0]==1
        assert elapsed<60
        print(f'PASS integrated quota:80% real latch;7500 file entries;chunks={len(messages)};max_bytes={max(len(m[0].encode()) for m in messages)};same_sweep_budget={elapsed:.3f}s')
        print(f'PASS sweep-shape:7500 files;bytes={result["used_bytes"]};30 keyset batches;shared_elapsed={elapsed:.3f}s;no legacy expiry/unlink')


async def main(case: str) -> None:
    if case == 'all':
        for selected in ('manual-delete','quota-chunks','references','handoff-paths','history','live-cap','notices','sweep','sweep-shape'):
            await main(selected)
        print('All selected Slice B private acceptance cases passed.')
        return
    with tempfile.TemporaryDirectory(prefix='shared-artifact-B-') as directory:
        root = Path(directory)
        if case in ('manual-delete', 'manual-delete-default'):
            await manual_delete_case(root / 'default', mode='preview')
            await manual_delete_case(root / 'off', mode='off')
        if case in ('manual-delete', 'manual-delete-enforce'):
            await manual_delete_case(root / 'enforce', mode='enforce')
            await manual_delete_case(root / 'live', mode='enforce', live=True)
        if case == 'sweep':
            await sweep_case(root/'sweep')
        if case == 'sweep-shape':
            await sweep_shape_case(root/'shape')
        if case == 'notices':
            await notices_case(root/'notices')
        if case == 'references':
            await references_case(root/'references')
        if case == 'handoff-paths':
            await handoff_paths_case(root/'handoff-paths')
        if case == 'history':
            await history_case(root/'history')
        if case == 'live-cap':
            await live_cap_case(root/'live-cap')
        if case == 'quota-chunks':
            await quota_chunks_case(root/'quota')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=(
        'all', 'manual-delete', 'manual-delete-default', 'manual-delete-enforce',
        'quota-chunks', 'references', 'handoff-paths', 'history', 'live-cap', 'notices', 'sweep', 'sweep-shape',
    ), default='all')
    asyncio.run(main(parser.parse_args().case))
