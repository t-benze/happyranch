"""S04 ordinary source shipping through real run_step, CLI, routes and SQLite.

Provider executables alone are disposable. This is not contained, installed,
completion-recovery, collection-health or epoch acceptance.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import socket
import sqlite3
import subprocess
import sys
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

import pytest

from runtime.daemon import jobs_runner
from runtime.models import TaskRecord, TaskStatus
from runtime.orchestrator.executors import _parse_claude_session_id, _parse_codex_session_id
from tests.test_memory_non_task_transport_shipping import shipping_venue as _existing_venue


@pytest.fixture
def retry_venue(test_settings, monkeypatch, tmp_path: Path, request) -> Iterator:
    # Reuse the reviewed real OrgState/bootstrap/provider socket fixture and
    # exception-safe partial-launch cleanup, preserving all its assertions.
    with contextmanager(_existing_venue.__wrapped__)(
        test_settings, monkeypatch, tmp_path, request,
    ) as v:
        v.org.orchestrator.attach_host_supervisor(None)
        # Extend only the external executable to expose native output and one
        # intentional provider nonzero. Runtime/executor admission stays real.
        source = v.provider.read_text()
        source = source.replace(
            "if plan['executor'] == 'claude':",
            "def emit(value):\n"
            "    with Path(sys.argv[0] + '.output.' + str(os.getpid())).open('a') as receipt:\n"
            "        receipt.write(value + '\\n')\n"
            "    print(value)\n"
            "if plan['executor'] == 'claude':")
        source = source.replace('print(json.dumps(', 'emit(json.dumps(')
        v.provider.write_text(source)
        with v.provider.open('a') as provider:
            provider.write("\nif plan.get('exit_code', 0):\n"
                           "    print('owned provider failure', file=sys.stderr)\n"
                           "sys.exit(plan.get('exit_code', 0))\n")
        v.invocations = []
        v.owned_jobs = []
        v.job_sockets = []
        try:
            yield v
        finally:
            errors = []
            # Closing the harmless job's socket is its release barrier. Join
            # its real registered runner BEFORE the inherited DB/server close.
            for connection in v.job_sockets:
                try:
                    connection.close()
                except BaseException as error:
                    errors.append(error)
            for runner in v.owned_jobs:
                try:
                    _join_job(runner)
                except BaseException as error:
                    errors.append(error)
            v._finish_errors(errors, sys.exception())
    assert v.closed and v.runs_closed
    assert all(f.done() for f in v.futures)
    assert all(c.finished and c.conn.fileno() == -1 and c.wire.closed for c in v.children)
    assert all(j.done() for j in v.owned_jobs)
    assert all(s.fileno() == -1 for s in v.job_sockets)
    assert v.listener.fileno() == v.sock.fileno() == -1
    assert not v.server_thread.is_alive() and not v.daemon.orgs
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        v.org.db.fetch_all_readonly('SELECT 1')
    launches = [json.loads(line)['pid'] for line in
                Path(str(v.provider) + '.launches').read_text().splitlines()]
    assert set(launches) == {c.evidence['pid'] for c in v.children}
    assert len(launches) == len(v.invocations)  # no nudge/recovery/unseen retry
    print(json.dumps({'cleanup': request.param, 'launches': launches,
                      'futures_settled': len(v.futures), 'jobs_settled': len(v.owned_jobs),
                      'sockets_closed': True, 'server_joined': True, 'database_closed': True}))


def _new_worker(v, brief: str) -> str:
    task = v.org.db.next_task_id()
    v.org.db.insert_task(TaskRecord(id=task, team='engineering', brief=brief,
                                   assigned_agent='dev_agent', task_type='subtask'))
    return task


def _launch(v, task: str, *, metadata: dict | None = None, fail: bool = False):
    plan_path = Path(str(v.provider) + '.json')
    plan = json.loads(plan_path.read_text())
    plan['exit_code'] = 7 if fail else 0
    plan_path.write_text(json.dumps(plan))
    future = v.submit(v.org.orchestrator.run_step, task, metadata)
    child = v.accept(future)
    agent = v.org.db.get_task(task).assigned_agent
    child.needs_callback = not fail
    sid = child.evidence['hint']
    child.completion = None
    # Declare a valid ordinary cleanup callback immediately after acceptance;
    # assertion unwinding cannot create a callbackless successful provider.
    if not fail:
        _callback(v, child, task, decision={'action': 'done', 'summary': 'cleanup'}
                  if agent == 'engineering_head' else None)
    v.invocations.append({'task': task, 'agent': agent, 'sid': sid,
                          'provider': child.evidence, 'success': not fail})
    assert v.org.sessions.get_context_by_session(sid) == ('alpha', task, agent)
    assert sid != child.evidence['provider_id']
    argv = child.evidence['argv']
    assert '--resume' not in argv and 'resume' not in argv
    if v.executor == 'claude':
        assert '--output-format' in argv and 'json' in argv
    else:
        assert argv[0] == 'exec' and '--session-id' not in argv
    return child, future


def _callback(v, child, task: str, *, decision: dict | None = None,
              jobs: list[str] | None = None) -> None:
    body = {'task_id': task, 'session_id': child.evidence['hint'],
            'agent': v.org.db.get_task(task).assigned_agent,
            'status': 'blocked' if jobs else 'completed', 'summary': 'owned S04 callback'}
    if decision is not None:
        body['decision'] = decision
    if jobs:
        body['waiting_on_job_ids'] = jobs
    path = v.provider.parent / f'{body["session_id"]}-callback.json'
    path.write_text(json.dumps(body))
    child.completion = ['report-completion', '--org', 'alpha', '--from-file', str(path)]


def _finish(child, future) -> None:
    child.finish()
    assert future.result(timeout=15) is None


def _dequeue(v, expected: str) -> dict | None:
    # Consume the actual production queue after the producer's joined return.
    slug, task, metadata = v.daemon.queue._queue.get_nowait()
    v.daemon.queue._queue.task_done()
    assert (slug, task) == ('alpha', expected)
    return metadata


def _operations(v, child, task: str) -> None:
    agent, sid = v.org.db.get_task(task).assigned_agent, child.evidence['hint']
    impressions = [r for r in v.org.db.get_audit_logs(task)
                   if r['action'] == 'memory_digest_impression' and r['payload']['session_id'] == sid]
    assert len(impressions) == 1
    impression = impressions[0]['payload']
    # Only actual rendered pointer lines define shown IDs. Body/title mentions
    # never become an oracle-shaped shown list.
    digest = child.evidence['prompt'].split('=== MEMORY-DIGEST (system) ===', 1)[1]
    rendered = re.findall(r'^- `(MEM-\d+)` —', digest, re.MULTILINE)
    assert rendered and set(rendered) == set(impression['pointer_ids'])
    assert impression['full_body_ids'] == []
    assert set(impression['digest_ids']) == set(rendered)
    assert impression['memory_telemetry_version'] == 1
    shown = rendered[0]
    # A newly searchable file created AFTER the actual render cannot be shown
    # in this invocation. Subsequent generations render their own new digest.
    index = len(v.invocations)
    nonshown, query = f'MEM-{900 + index:03}', f'nonshown{index}unique'
    memory = v.org.root / 'workspaces' / agent / 'memory'
    (memory / f'{nonshown}-{query}.md').write_text(
        f'---\nid: {nonshown}\nslug: {query}\ntitle: {query}\ntopic: test\n'
        'provenance: experiential\nscope: agent\nlifecycle: valid\nsalience: 50\n---\n\n'
        f'{query} body\n')
    assert nonshown not in impression['digest_ids'] and nonshown not in digest
    before = v.watermark()
    responses = []
    for verb, value in (('get', shown), ('search', query), ('get', nonshown)):
        response = child.command(['memory', verb, '--org', 'alpha', '--agent', agent, value, '--json'])
        body = json.loads(response['stdout'])
        if verb == 'search':
            assert [h['id'] for h in body['hits']] == [nonshown]
        else:
            assert body['id'] == value
        responses.append(response)
    rows = v.rows(before)
    assert [r['action'] for r in rows] == ['memory_read', 'memory_search', 'memory_read']
    for row in rows:
        assert row['agent'] == agent
        assert (row['payload']['task_id'], row['payload']['session_id']) == (task, sid)
        assert v.org.sessions.get_context_by_session(sid) == ('alpha', task, agent)
    assert rows[0]['task_id'] == rows[2]['task_id'] == f'AGENT-{agent}'
    assert rows[1]['task_id'] == task
    assert rows[0]['payload'] == {'id': shown, 'slug': 'shipping' if shown == 'MEM-001'
                                 else next(p.stem.split('-', 2)[2] for p in memory.glob(shown + '-*')),
                                 'task_id': task, 'session_id': sid, 'source': 'digest'}
    assert rows[1]['payload'] == {'agent': agent, 'memory_ids': [nonshown], 'hit_count': 1,
                                 'kb_hit_count': 0, 'task_id': task, 'session_id': sid}
    assert rows[2]['payload'] == {'id': nonshown, 'slug': query, 'source': 'search',
                                 'task_id': task, 'session_id': sid}
    v.receipts.append({'tuple': ['alpha', agent, task, sid], 'impression': impressions[0],
                       'shown': rendered, 'nonshown': nonshown, 'operations': rows,
                       'cli_responses': responses})


def _negatives(v, child, task: str, stale: str, old_provider: str) -> None:
    sid = child.evidence['hint']
    # Missing/random/stale/provider/generated remain usable, without borrowing
    # the live task or its job's identity. Both verbs exercise canonical CLI.
    for hint in ('', 'sess-random', stale, old_provider, child.evidence['provider_id'],
                 'executor-generated-unregistered'):
        v.pair(hint=hint, label=f'S04-invalid:{hint}')
    v.pair(hint='sess-random', explicit=sid, task=task, sid=sid,
           label='S04-valid-explicit-both-verbs')
    v.pair(hint=sid, explicit='', task=task, sid=sid,
           label='S04-empty-explicit-fallback-both-verbs')


async def _await_job(runner) -> None:
    await asyncio.shield(runner)


def _join_job(runner) -> None:
    if runner.done():
        runner.result()
    else:
        asyncio.run_coroutine_threadsafe(_await_job(runner), runner.get_loop()).result(timeout=15)


def _submit_job(v, child, task: str) -> tuple[str, socket.socket]:
    listener = socket.socket()
    v.job_sockets.append(listener)
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    listener.settimeout(10)
    # Harmless local job holds a connection until the test releases it; a
    # socket entered/release barrier orders terminal commit, never a sleep.
    program = (f'import socket; s=socket.create_connection({listener.getsockname()!r}, timeout=15); '
               's.sendall(b"entered"); s.recv(1); s.close(); print("owned job completed")')
    body = {'task_id': task, 'session_id': child.evidence['hint'], 'title': 'owned S04 job',
            'rationale': 'Harmless local socket-barrier job',
            'script': f'{shlex.quote(sys.executable)} -c {shlex.quote(program)}\n',
            'interpreter': 'bash', 'review_required': False, 'persistent': False,
            'max_runtime_seconds': 30}
    path = v.provider.parent / 'owned-job.json'
    path.write_text(json.dumps(body))
    response = child.command(['jobs', 'submit', '--org', 'alpha', '--from-file', str(path)])
    match = re.search(r'JOB-\d+', response['stdout'])
    assert match, response
    jid = match.group()
    runner = jobs_runner._RUNNER_TASKS[jid]
    v.owned_jobs.append(runner)
    conn, _ = listener.accept()
    v.job_sockets.append(conn)
    conn.settimeout(10)
    assert conn.recv(7) == b'entered'
    listener.close()
    job = v.org.db.get_job(jid)
    assert job.status.value == 'running' and (job.task_id, job.agent_name) == (task, 'dev_agent')
    assert job.script_text == body['script'] and job.max_runtime_seconds == 30
    v.receipts.append({'job_submit': response, 'job': job.model_dump(mode='json')})
    return jid, conn


def _census_and_guard(v, *, workers: int, managers: int) -> None:
    n = len(v.invocations)
    assert n == workers + managers
    assert v.census()['assigned_intents'] == n
    assert v.census()['phase_counts'] == {
        p: {'attempted': n, 'persisted': n}
        for p in ('intent', 'identity', 'expectation', 'binding', 'launched', 'terminal')}
    phases = {p: v.org.db.get_audit_logs_by_action('memory_runtime_' + p)
              for p in ('intent', 'identity', 'expectation', 'binding', 'launched', 'terminal')}
    assert all(len(rows) == n for rows in phases.values())
    assert Counter(row['payload']['invocation_purpose'] for row in phases['identity']) == {
        **({'worker_execution': workers} if workers else {}),
        **({'manager_decision': managers} if managers else {}),
    }
    for ordinal, invocation in enumerate(v.invocations, 1):
        task, agent, sid = (invocation[k] for k in ('task', 'agent', 'sid'))
        actual = v.org.db.get_task(task)
        for p, rows in phases.items():
            own = [r for r in rows if r['payload']['ordinal'] == ordinal]
            assert len(own) == 1
            row = own[0]
            assert (row['task_id'], row['agent']) == (task, agent)
            assert row['payload']['session_id'] == (None if p == 'intent' else sid)
        identity = phases['identity'][ordinal - 1]['payload']
        assert identity['population'] == ('child' if actual.parent_task_id else 'root')
        assert identity['task_type'] == actual.task_type
        assert identity['parent_task_id'] == actual.parent_task_id and identity['parent_known'] is True
        assert identity['executor'] == v.executor
        terminal = phases['terminal'][ordinal - 1]['payload']
        assert terminal['success'] is invocation['success']
        assert terminal['launched_callbacks'] == 1
        assert phases['launched'][ordinal - 1]['payload']['callback_count'] == 1
        expectation = phases['expectation'][ordinal - 1]['payload']
        assert expectation['state'] == 'nonempty' and expectation['reason'] == 'rendered_ids'
        assert expectation['rendered_text_present'] is True
        starts = [r for r in v.org.db.get_audit_logs(task) if r['action'] == 'session_start'
                  and r['payload']['session_id'] == sid]
        assert len(starts) == 1 and starts[0]['agent'] == agent
        assert starts[0]['payload']['invocation_purpose'] == identity['invocation_purpose']
        assert identity['invocation_purpose'] == (
            'manager_decision' if actual.task_type == 'task' else 'worker_execution')
        output = Path(str(v.provider) + '.output.' + str(invocation['provider']['pid'])).read_text()
        envelope = [json.loads(line) for line in output.splitlines()]
        provider_id = invocation['provider']['provider_id']
        parser = _parse_claude_session_id if v.executor == 'claude' else _parse_codex_session_id
        assert parser(output) == provider_id and provider_id != sid
        if v.executor == 'claude':
            assert envelope == [{'type': 'result', 'subtype': 'success', 'is_error': False,
                                 'session_id': provider_id, 'result': 'finished',
                                 'usage': {'input_tokens': 12, 'output_tokens': 3}}]
        else:
            assert envelope == [{'type': 'thread.started', 'thread_id': provider_id},
                                {'type': 'turn.completed', 'usage': {'input_tokens': 12,
                                  'output_tokens': 3, 'cached_input_tokens': 0}}]
        usage = v.org.db.fetch_all_readonly(
            'SELECT * FROM session_token_usage WHERE task_id=? AND agent=? AND session_id=?',
            (task, agent, sid))
        # Existing _run_command intentionally omits usage for nonzero exit,
        # even if the external executable printed a success-shaped envelope.
        assert len(usage) == (1 if invocation['success'] else 0)
        if invocation['success']:
            assert (usage[0]['input_tokens'], usage[0]['output_tokens']) == (12, 3)
        v.receipts.append({'provider_output': output, 'provider_id': provider_id,
                           'runtime_sid': sid, 'usage': [dict(row) for row in usage]})
        assert v.org.sessions.get_context_by_session(sid) is None
    assert v.org.memory_collection.validate()['census_valid'] is True
    backend = v.org.orchestrator._audit.compute_memory_telemetry_report()
    result = subprocess.run([v.cli, 'memory', 'report', '--org', 'alpha', '--agent', 'dev_agent', '--json'],
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    for report in (backend, json.loads(result.stdout)):
        assert report['decision'] == 'insufficient_instrumentation'
        assert report['observation_period']['thresholds_met'] is False
    assert v.org.db.get_audit_logs_by_action('memory_collection_epoch_started') == []
    assert v.org.db.fetch_all_readonly('SELECT * FROM task_completion_recoveries') == []
    print(json.dumps({'S04': v.executor, 'invocations': v.invocations, 'receipts': v.receipts,
                      'census': v.census(), 'phases': phases,
                      'lifecycle': {r['task']: v.org.db.get_audit_logs(r['task']) for r in v.invocations}}))


@pytest.mark.parametrize('retry_venue', ('claude', 'codex'), indirect=True)
def test_same_task_job_resumption_uses_new_runtime_identity(retry_venue) -> None:
    """v24: outcome=new SID/same task and retired r1; regression=job/provider
    SID borrowing or stale credit; keeper insufficiency=resume_helper mocks
    queue and PR984 bypasses run_step; seam=real run_step/jobs/CLI/routes, no hook.
    """
    v, org = retry_venue, retry_venue.org
    task = _new_worker(v, 'S04 same-task blocked job continuation')
    r1, f1 = _launch(v, task)
    _operations(v, r1, task)
    jid, connection = _submit_job(v, r1, task)
    _callback(v, r1, task, jobs=[jid])
    _finish(r1, f1)
    parked = org.db.get_task(task)
    assert parked.status == TaskStatus.IN_PROGRESS and parked.block_kind.value == 'blocked_on_job'
    assert json.loads(parked.blocked_on_job_ids) == [jid]
    blocked = org.db.get_latest_task_result(task, 'dev_agent', r1.evidence['hint'])
    assert blocked['status'] == 'blocked'
    assert org.sessions.get_context_by_session(r1.evidence['hint']) is None
    assert v.daemon.queue._queue.empty()
    connection.close()
    _join_job(v.owned_jobs[0])
    job = org.db.get_job(jid)
    assert job.status.value == 'completed' and job.exit_code == 0
    assert job.stdout_head == 'owned job completed\n'
    metadata = _dequeue(v, task)
    assert metadata == {'trigger': 'job_terminal', 'triggering_job_id': jid}
    r2, f2 = _launch(v, task, metadata=metadata)
    assert r2.evidence['hint'] != r1.evidence['hint']
    assert '=== BLOCKED-JOBS-RESULTS (system) ===' in r2.evidence['prompt']
    assert f'{jid}  completed (exit 0)' in r2.evidence['prompt']
    _operations(v, r2, task)
    _negatives(v, r2, task, r1.evidence['hint'], r1.evidence['provider_id'])
    _finish(r2, f2)
    assert org.db.get_task(task).status == TaskStatus.COMPLETED
    resumed = [r for r in org.db.get_audit_logs(task) if r['action'] == 'task_resumed_from_jobs']
    assert len(resumed) == 1
    assert resumed[0]['payload']['blocking_job_ids'] == [jid]
    assert resumed[0]['payload']['job_outcomes'] == {jid: 'completed'}
    _census_and_guard(v, workers=2, managers=0)


@pytest.mark.parametrize('retry_venue', ('claude', 'codex'), indirect=True)
def test_manager_failed_child_retry_keeps_history_and_overlap_identity(retry_venue) -> None:
    """v24: outcome=real FAILED A/direct-linked successful B, sibling intact;
    regression=retry mutates A, reuses SID or clears same-agent sibling;
    keeper insufficiency=structural retry units/PR984 lack manager callbacks;
    seam=real manager policy bootstrap/callback/try_delegate/worker run_step.
    """
    v, org = retry_venue, retry_venue.org
    parent = org.orchestrator.create_task('S04 isolated ordinary manager retry')
    m1, fm1 = _launch(v, parent)
    assert org.teams.is_team_manager('engineering_head')
    _operations(v, m1, parent)
    _callback(v, m1, parent, decision={'action': 'delegate', 'agent': 'dev_agent', 'prompt': 'child A'})
    _finish(m1, fm1)
    children = org.db.get_children(parent)
    assert len(children) == 1
    a = children[0]
    _dequeue(v, a)
    ca, fa = _launch(v, a, fail=True)
    _operations(v, ca, a)
    sibling = _new_worker(v, 'S04 held same-agent sibling')
    cb_other, fb_other = _launch(v, sibling)
    _operations(v, cb_other, sibling)
    _finish(ca, fa)
    failed = org.db.get_task(a).model_dump(mode='json')
    failed_results = org.db.fetch_all_readonly('SELECT * FROM task_results WHERE task_id=?', (a,))
    failed_history = org.db.fetch_all_readonly('SELECT * FROM audit_log WHERE task_id=? ORDER BY id', (a,))
    assert failed['status'] == 'failed' and failed['parent_task_id'] == parent
    assert 'owned provider failure' in failed['note']
    assert org.sessions.get_context_by_session(ca.evidence['hint']) is None
    v.pair(child=cb_other, task=sibling, sid=cb_other.evidence['hint'], label='sibling-after-A-failure')
    _dequeue(v, parent)
    m2, fm2 = _launch(v, parent)
    _operations(v, m2, parent)
    _callback(v, m2, parent, decision={'action': 'delegate', 'agent': 'dev_agent',
                                    'prompt': 'child B revised work', 'revisit_of_task_id': a})
    _finish(m2, fm2)
    children = org.db.get_children(parent)
    assert len(children) == 2
    b = next(c for c in children if c != a)
    retry = org.db.get_task(b)
    assert (retry.parent_task_id, retry.revisit_of_task_id, retry.assigned_agent) == (parent, a, 'dev_agent')
    _dequeue(v, b)
    cb, fb = _launch(v, b)
    assert cb.evidence['hint'] != ca.evidence['hint']
    assert cb.evidence['hint'] not in [i['provider']['provider_id'] for i in v.invocations]
    _operations(v, cb, b)
    _negatives(v, cb, b, ca.evidence['hint'], ca.evidence['provider_id'])
    v.pair(child=cb_other, task=sibling, sid=cb_other.evidence['hint'], label='sibling-after-B-bootstrap')
    _finish(cb, fb)
    assert org.db.get_task(b).status == TaskStatus.COMPLETED
    assert org.db.get_task(a).model_dump(mode='json') == failed
    assert org.db.fetch_all_readonly('SELECT * FROM task_results WHERE task_id=?', (a,)) == failed_results
    assert org.db.fetch_all_readonly('SELECT * FROM audit_log WHERE task_id=? ORDER BY id', (a,)) == failed_history
    v.pair(child=cb_other, task=sibling, sid=cb_other.evidence['hint'], label='sibling-after-B-terminal')
    _finish(cb_other, fb_other)
    _dequeue(v, parent)
    m3, fm3 = _launch(v, parent)
    _operations(v, m3, parent)
    _callback(v, m3, parent, decision={'action': 'done', 'summary': 'B completed; failed A preserved'})
    _finish(m3, fm3)
    assert org.db.get_task(parent).status == TaskStatus.COMPLETED
    assert org.db.get_task(a).model_dump(mode='json') == failed
    assert org.db.fetch_all_readonly('SELECT * FROM task_results WHERE task_id=?', (a,)) == failed_results
    assert org.db.fetch_all_readonly('SELECT * FROM audit_log WHERE task_id=? ORDER BY id', (a,)) == failed_history
    _census_and_guard(v, workers=3, managers=3)
