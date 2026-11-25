"""C34 cases in the existing private released-graph schema-shell venue."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager, redirect_stdout
from io import StringIO
from pathlib import Path
import os

import httpx
import hashlib
import json
import sqlite3
from typing import Any, Callable
from unittest.mock import patch

from runtime.daemon.routes.tasks import CancelBody, cancel_task
from runtime.orchestrator.task_pause import fingerprint
from runtime.workflows.sequential_cancel_close import ACTION
from runtime.workflows.sequential_cancellation_tail import drain_source_cancellations
from runtime.workflows.sequential_history import _ReadBudget, read_sequential_history

PATH = '/api/v1/orgs/scratch/workflows/sequential/cancel-close-never-launched'


async def run_cases(fixture: Callable[..., Any], start: Callable[..., Any], inventory: Callable[..., Any]) -> None:
    for identity in ('release/happyranch/draft-review', 'release/happyranch/coding'):
        async with fixture() as f:
            run = (await start(f, (identity,)))[0]
            tid, vid = f.ids.visit_task_id, f.ids.visit_id
            # Literal retained uncertainty, not a test implementation of closure.
            # Reserve 256 historical SIDs without executing or settling anything.
            store = f.db._task_pause_store
            with store.writer(tid) as (root, row):
                row = store.ensure_uncommitted(root['id'])
                entries = []
                for n in range(256):
                    entries.append(dict(id='historical-' + str(n), org='scratch', root_task_id=root['id'],
                        task_id=tid, agent=f.db.get_task(tid).assigned_agent, session_id='reserved-' + str(n),
                        owner='sequential', owner_identity={}, fingerprint=fingerprint(f.db.get_task(tid)),
                        generation=row['generation'], phase='unknown', retry={},
                        context=dict(execution_unknown=True, producer_settled=True), started_at=None, captured_generation=None))
                row['journal']['entries'] = entries
                store.save_journal_uncommitted(row)
            await cancel_task(f.source.id, CancelBody(rationale='private historical stop'), f.org)
            current = dict(f.conn.execute('SELECT * FROM workflow_seq_runs WHERE id=?', (run['id'],)).fetchone())
            assert (current['state'], current['progress']) == ('running', 'cancelling'), current
            assert f.db.get_task(tid).status == 'cancelled'
            command = dict(operation_key='private-human-close', run_id=run['id'], expected_cas=current['cas'])
            journal = f.conn.execute('SELECT admission_journal_json FROM task_pause_controls WHERE root_task_id=?',
                (root['id'],)).fetchone()[0]
            from runtime.workflows import sequential_history as history_owner
            reserve = history_owner._reserve_generic_cancellation
            charges = []
            def observe_reserve(data: dict, budget: _ReadBudget) -> None:
                before = budget.consumed
                reserve(data, budget)
                charges.append(budget.consumed-before)
            with patch.object(history_owner, '_reserve_generic_cancellation', side_effect=observe_reserve):
                read_sequential_history(f.conn, expected_org_slug='scratch', reserve_cancellation=True)
            assert charges == [charges[0]] and charges[0] > 16*1048576
            original = inventory(f)
            # The ordinary native tail still needs terminal+outer and must not
            # adopt historical unknowns as closure proof.
            drain_source_cancellations(f.org, f.source.id)
            assert inventory(f) == original, 'automatic drain broadened'

            async def post(body: dict = command, *, status: int = 200, code: str | None = None,
                           headers: dict | None = None) -> Any:
                before = inventory(f)
                response = await f.client.post(PATH, json=body, headers=f.auth if headers is None else headers)
                assert response.status_code == status, (status, response.status_code, response.text)
                if code:
                    assert response.json()['detail']['code'] == code, response.text
                    assert inventory(f) == before, code + ' mutated persistent/queue state'
                return response

            for invalid in ({}, dict(command, extra=1), dict(command, expected_cas=True),
                    dict(command, expected_cas=-1), dict(command, operation_key='bad\n'),
                    dict(command, run_id='bad'), dict(command, operation_key='a'*129)):
                await post(invalid, status=422, code='invalid_request')
            response = await f.client.post(PATH, content=b'{"operation_key":"a","operation_key":"b"}', headers=f.auth)
            assert response.status_code == 422 and response.json()['detail']['code'] == 'invalid_request'
            await post(headers=dict(f.auth, **{'X-HappyRanch-Task-Session': 'private'}), status=422, code='invalid_request')
            await post(dict(command, run_id='seq-run-' + '0'*64), status=404, code='run_not_found')
            await post(dict(command, expected_cas=99), status=409, code='stale_state')
            # A request without the human bearer cannot use this route.
            response = await f.client.post(PATH, json=command, headers=f.cli)
            assert response.status_code in (401, 403), response.text
            assert inventory(f) == original
            status_path = '/api/v1/orgs/scratch/workflows/sequential/runs/' + run['id'] + '/cancel-close-never-launched'
            response = await f.client.get(status_path, params={'operation_key': command['operation_key']}, headers=f.auth)
            assert response.status_code == 404 and response.json()['detail']['code'] == 'operation_not_found'
            assert inventory(f) == original

            # Each private mutation is restored from a transactionally consistent
            # SQLite backup. Constraints stay enabled for the supported fixtures.
            saved = sqlite3.connect(':memory:')
            f.conn.backup(saved)
            mutations = [
                ('run_not_cancelling', 409, "UPDATE workflow_seq_runs SET progress='queued' WHERE id=?", (run['id'],)),
                ('visit_not_never_bound', 409, "UPDATE workflow_seq_visits SET session_id='old',state='running' WHERE id=?", (vid,)),
                ('visit_task_not_cancelled', 409, "UPDATE tasks SET status='pending',cancelled_at=NULL WHERE id=?", (tid,)),
                ('history_conflict', 409, "UPDATE tasks SET assigned_agent='s3_alt' WHERE id=?", (tid,)),
                ('session_start_present', 409, "INSERT INTO audit_log(task_id,agent,action,payload,timestamp) VALUES (?,'old','session_start','{}','2026-11-25T00:00:00+00:00')", (tid,)),
                ('executor_admission_present', 409, "UPDATE tasks SET current_session_id='old' WHERE id=?", (tid,)),
                ('executor_admission_present', 409, 'UPDATE tasks SET executor_pid=123 WHERE id=?', (tid,)),
                ('executor_admission_present', 409, 'UPDATE tasks SET orchestration_step_count=1 WHERE id=?', (tid,)),
                ('executor_admission_present', 409, "INSERT INTO audit_log(task_id,agent,action,payload,timestamp) VALUES (?,'old','authority_policy_session_binding','{}','2026-11-25T00:00:00+00:00')", (tid,)),
                ('executor_admission_present', 409, "INSERT INTO audit_log(task_id,agent,action,payload,timestamp) VALUES (?,'old','authority_policy_selector_session_binding','{}','2026-11-25T00:00:00+00:00')", (tid,)),
                ('preparation_audit_present', 409, "INSERT INTO audit_log(task_id,agent,action,payload,timestamp) VALUES (?,'old','repository_preparation_refused','{}','2026-11-25T00:00:00+00:00')", (tid,)),
                ('launch_boundary_present', 409, "INSERT INTO audit_log(task_id,agent,action,payload,timestamp) VALUES (?,'old','task_native_launch_boundary','{}','2026-11-25T00:00:00+00:00')", (tid,)),
                ('workflow_evidence_present', 409, "INSERT INTO workflow_seq_evidence(id,run_id,visit_id,kind,evidence_bytes,evidence_digest) VALUES ('private-proof',?,?,'outer',X'7b7d','bad')", (run['id'], vid)),
                ('callback_result_present', 409, "INSERT INTO task_results(task_id,agent,session_id,status,output_summary,created_at) VALUES (?,'old','old','completed','private','2026-11-25T00:00:00+00:00')", (tid,)),
                ('callback_result_present', 409, "UPDATE workflow_seq_visits SET generic_checks_bytes=X'7b7d' WHERE id=?", (vid,)),
                ('workflow_result_event_present', 409, "INSERT INTO workflow_seq_events(id,run_id,engine_version,sequence,operation_key,kind,prior_digest,event_bytes,event_digest,source_visit_id) VALUES ('private-result-event',?,2,3,'private-result','held','private',X'7b7d','private',?)", (run['id'], vid)),
                ('workflow_output_present', 409, "INSERT INTO workflow_seq_outputs(id,run_id,engine_version,visit_id,slot,contract,task_id,session_id,result_id,content_bytes,content_digest,recipients_bytes) VALUES ('private-output',?,2,?,'draft','immutable-document@1',?,'old',?,X'7b7d','bad',X'5b5d')", (run['id'], vid, tid, run['source_result_id'])),
                ('evidence_unavailable', 503, "UPDATE task_pause_controls SET admission_journal_json='broken' WHERE root_task_id=?", (root['id'],)),
            ]
            try:
                from runtime.models import TaskRecord
                f.db.insert_task(TaskRecord(id='TASK-legacy-carrier', assigned_agent=f.source.assigned_agent,
                    task_type='task', status='cancelled', brief='Private retained carrier'))
                legacy = dict(current, id='seq-run-' + '1'*64, carrier_task_id='TASK-legacy-carrier',
                    engine_version=1, state='cancelled', progress=None, active_visit_id=None, epoch=1,
                    content_returns=0, assignment_snapshot_bytes=None, assignment_snapshot_digest=None,
                    assignment_generation=None, start_operation_key='private-old')
                f.conn.execute('INSERT INTO workflow_seq_runs (' + ','.join(legacy) + ') VALUES (' +
                    ','.join('?' for _ in legacy) + ')', tuple(legacy.values())); f.conn.commit()
                await post(dict(command, run_id=legacy['id']), status=422, code='unsupported_engine')
                saved.backup(f.conn)
                binding = dict(f.conn.execute('SELECT * FROM authority_policy_v2_session_bindings LIMIT 1').fetchone())
                binding.update(binding_id='private-historical-binding', root_task_id=tid)
                f.conn.execute('INSERT INTO authority_policy_v2_session_bindings (' + ','.join(binding) + ') VALUES (' +
                    ','.join('?' for _ in binding) + ')', tuple(binding.values())); f.conn.commit()
                await post(status=409, code='executor_admission_present'); saved.backup(f.conn)
                for state in ('pending', 'running', 'completed', 'failed', 'rejected'):
                    f.conn.execute('INSERT INTO jobs(id,task_id,agent_name,title,script_text,interpreter,status,created_at) '
                        "VALUES ('JOB-private',?,'private','private','exit 0','bash',?,'2026-11-25T00:00:00+00:00')", (tid,state))
                    f.conn.commit()
                    await post(status=409, code='job_present'); saved.backup(f.conn)
                for code, status, sql, args in mutations:
                    f.conn.execute(sql, args); f.conn.commit()
                    await post(status=status, code=code)
                    saved.backup(f.conn)
                    print('PASS cancel-close refusal', identity, code, flush=True)
                for field, value in (('phase', 'possible_launch'), ('phase', 'running'),
                        ('phase', 'result_processing'), ('phase', 'action_started'),
                        ('started_at', '2026-11-25T00:00:00+00:00'), ('captured_generation', 1)):
                    changed = json.loads(journal); changed['entries'][0][field] = value
                    f.conn.execute('UPDATE task_pause_controls SET admission_journal_json=? WHERE root_task_id=?',
                        (json.dumps(changed), root['id'])); f.conn.commit()
                    await post(status=409, code='journal_launch_evidence')
                    saved.backup(f.conn)
                changed = json.loads(journal); changed['entries'][0]['owner'] = 'job'
                changed['entries'][0]['context']['job_submission'] = dict(audit_id=1,digest='0'*64)
                f.conn.execute('UPDATE task_pause_controls SET admission_journal_json=? WHERE root_task_id=?',
                    (json.dumps(changed), root['id'])); f.conn.commit()
                await post(status=409, code='journal_launch_evidence'); saved.backup(f.conn)
                changed = json.loads(journal); changed['entries'][0]['owner'] = 'recovery'
                changed['entries'][0]['context']['recovery_return'] = dict(origin_session_id='old',provider_session_id='provider',
                    duration_seconds=0,recovery_session_id='return',deadline_at=None)
                f.conn.execute('UPDATE task_pause_controls SET admission_journal_json=? WHERE root_task_id=?',
                    (json.dumps(changed), root['id'])); f.conn.commit()
                await post(status=409, code='journal_launch_evidence'); saved.backup(f.conn)
                changed = json.loads(journal); changed['entries'][0]['context']['audit_ids'] = [999999]
                f.conn.execute('UPDATE task_pause_controls SET admission_journal_json=? WHERE root_task_id=?',
                    (json.dumps(changed), root['id'])); f.conn.commit()
                await post(status=503, code='evidence_unavailable'); saved.backup(f.conn)

                # Real rollback, including deferred-FK failure at COMMIT.
                actual_transaction = f.org.workflow_authority._admission_transaction
                @contextmanager
                def fail_commit() -> Any:
                    with actual_transaction() as conn:
                        yield conn
                        conn.execute('PRAGMA defer_foreign_keys=ON')
                        conn.execute('INSERT INTO workflow_seq_inbox_deliveries '
                            '(initiator_task_id,session_id,notification_id) VALUES (?,?,?)',
                            (f.source.id, 'private-commit-fault', 'missing-notification'))
                with patch.object(f.org.workflow_authority, '_admission_transaction', fail_commit):
                    await post(status=503, code='evidence_unavailable')
                with patch('runtime.workflows.sequential_control._append_event', side_effect=sqlite3.OperationalError('private event fault')):
                    await post(status=503, code='evidence_unavailable')
                # Real rollback after a finish event/member/inbox was written.
                with patch.object(f.db, 'insert_audit_log_uncommitted', side_effect=sqlite3.OperationalError('private audit fault')):
                    await post(status=503, code='evidence_unavailable')
                assert inventory(f) == original
                from runtime.workflows import sequential_cancel_close as owner
                original_read = owner.read_sequential_history
                reads = []
                def fail_postwrite(*args: Any, **kwargs: Any) -> Any:
                    reads.append(1)
                    if len(reads) == 2:
                        raise ValueError('private postwrite validation fault')
                    return original_read(*args, **kwargs)
                with patch.object(owner, 'read_sequential_history', side_effect=fail_postwrite):
                    await post(status=503, code='evidence_unavailable')
                assert len(reads) == 2 and inventory(f) == original
                with patch.object(_ReadBudget, 'check', side_effect=ValueError('private exhausted allowance')):
                    await post(status=503, code='evidence_unavailable')
                with patch.object(f.org.workflow_authority, 'capture_admission', side_effect=OSError('private authority unavailable')):
                    await post(status=503, code='control_unavailable')

                event_count = f.conn.execute('SELECT count(*) FROM workflow_seq_events').fetchone()[0]
                responses = await asyncio.gather(f.client.post(PATH, json=command, headers=f.auth),
                    f.client.post(PATH, json=command, headers=f.auth))
                assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
                receipt = responses[0].json()
                assert responses[1].json() == receipt
                assert (receipt['before_cas'], receipt['after_cas']) == (current['cas'], current['cas']+1)
                assert receipt['native_cleanup'] == 'unresolved_unchanged'
                assert receipt['journal_sha256'] == hashlib.sha256(journal.encode()).hexdigest()
                assert f.conn.execute('SELECT admission_journal_json FROM task_pause_controls WHERE root_task_id=?',
                    (root['id'],)).fetchone()[0] == journal
                assert f.conn.execute('SELECT count(*) FROM workflow_seq_events').fetchone()[0] == event_count+1
                assert f.conn.execute('SELECT count(*) FROM audit_log WHERE action=?', (ACTION,)).fetchone()[0] == 1
                assert f.db.get_task(f.source.id).status == 'cancelled'
                assert not f.dispatcher.queue_admission_allowed(tid)
                with patch.object(history_owner, '_reserve_generic_cancellation', side_effect=observe_reserve):
                    read_sequential_history(f.conn, expected_org_slug='scratch', reserve_cancellation=True)
                assert charges[-1] == 0
                closed = inventory(f)
                assert (await post()).json() == receipt and inventory(f) == closed
                response = await f.client.get(status_path, params={'operation_key': command['operation_key']}, headers=f.auth)
                assert response.json() == receipt and inventory(f) == closed
                await post(dict(command, expected_cas=0), status=409, code='operation_key_conflict')
                await post(dict(command, operation_key='different'), status=409, code='already_closed')
                # Drive the real thin CLI/client through this ASGI application.
                from cli.main import build_parser
                loop = asyncio.get_running_loop()
                native_client = httpx.Client
                requests = []
                def transport(request: httpx.Request) -> httpx.Response:
                    requests.append((request.method, request.url.path))
                    received = asyncio.run_coroutine_threadsafe(f.client.send(request), loop).result(timeout=10)
                    return httpx.Response(received.status_code, content=received.content, headers=received.headers, request=request)
                def private_client(*args: Any, **kwargs: Any) -> httpx.Client:
                    return native_client(*args, **dict(kwargs, transport=httpx.MockTransport(transport)))
                command_file = Path(os.environ['HAPPYRANCH_DAEMON_HOME']) / 'private-human-close.json'
                command_file.write_text(json.dumps(command))
                for argv in (['sequential-cancel-close-never-launched', '--org', 'scratch', '--from-file', str(command_file)],
                        ['sequential-cancel-close-status', run['id'], '--org', 'scratch', '--operation-key', command['operation_key']]):
                    args = build_parser().parse_args(argv)
                    output = StringIO()
                    with patch('httpx.Client', private_client), redirect_stdout(output):
                        await asyncio.to_thread(args.func, args)
                    assert json.loads(output.getvalue()) == receipt
                assert sum(method == 'POST' for method, _ in requests) == 1
                assert inventory(f) == closed
                # Concurrent same-key requests serialize to the original receipt.
                responses = await asyncio.gather(post(), post())
                assert all(r.json() == receipt for r in responses) and inventory(f) == closed
                f.conn.execute("UPDATE audit_log SET payload='{}' WHERE action=?", (ACTION,)); f.conn.commit()
                await post(status=503, code='evidence_unavailable')
                print('PASS cancel-close accept/replay/readback/rollback/256-unknowns/ordinary-tail', identity, flush=True)
            finally:
                saved.close()

    # Normal pristine cancellation can win first without a human-close receipt.
    async with fixture() as f:
        run = (await start(f))[0]
        await cancel_task(f.source.id, CancelBody(rationale='private engine winner'), f.org)
        assert f.conn.execute('SELECT state FROM workflow_seq_runs WHERE id=?', (run['id'],)).fetchone()[0] == 'finished'
        before = inventory(f)
        response = await f.client.post(PATH, json=dict(operation_key='late-human',run_id=run['id'],expected_cas=0), headers=f.auth)
        assert response.status_code == 409 and response.json()['detail']['code'] == 'already_closed'
        assert inventory(f) == before
