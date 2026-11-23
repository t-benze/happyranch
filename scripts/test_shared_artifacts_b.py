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
from datetime import timedelta
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime.models import ThreadMessageKind, ThreadRecord
from runtime.infrastructure.shared_artifact_references import install_reference_seams
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
        print(f'PASS quota-chunks: all chunks/replay/rollback;7500 entries;300 messages;max_bytes={maximum};elapsed={elapsed:.3f}s')


async def main(case: str) -> None:
    with tempfile.TemporaryDirectory(prefix='shared-artifact-B-') as directory:
        root = Path(directory)
        if case in ('manual-delete', 'manual-delete-default'):
            await manual_delete_case(root / 'default', mode='preview')
            await manual_delete_case(root / 'off', mode='off')
        if case in ('manual-delete', 'manual-delete-enforce'):
            await manual_delete_case(root / 'enforce', mode='enforce')
            await manual_delete_case(root / 'live', mode='enforce', live=True)
        if case == 'quota-chunks':
            await quota_chunks_case(root/'quota')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=(
        'manual-delete', 'manual-delete-default', 'manual-delete-enforce',
        'quota-chunks',
    ), default='manual-delete')
    asyncio.run(main(parser.parse_args().case))
