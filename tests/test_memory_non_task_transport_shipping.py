"""S05 ordinary source venue: real launches, canonical CLI/HTTP and SQLite.

Only the external Claude/Codex executable is disposable. Socket command/ack
barriers hold real A/B tasks across actual thread/dream/manual operations and
A's ordinary return. No supervisor/health/epoch/installed acceptance is implied.
"""
from __future__ import annotations

import asyncio
import importlib.metadata
import json
import os
import socket
import sqlite3
import subprocess
import sys
import textwrap
import threading
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Iterator

import httpx
import pytest
import uvicorn

from runtime.daemon import paths
from runtime.daemon.app import create_app
from runtime.daemon.dream_runner import run_dream
from runtime.daemon.state import DaemonState
from runtime.daemon.thread_runner import run_invocation
from runtime.models import DreamRecord, DreamStatus, TaskRecord, ThreadMessageKind, ThreadRecord
from runtime.orchestrator.agent_def import AgentDef, render_agent_text
from runtime.orchestrator.executor_binary_registry import set_binary
from runtime.platform.isolation import detect_platform_isolation as _actual_detector
from runtime.runtime import RuntimeDir


# This is the sole stand-in: the provider process runs shipping subprocess CLI
# commands, then emits provider-native completion/usage with a DISTINCT identity.
_PROVIDER = r'''
import importlib.metadata, json, os, socket, subprocess, sys
from pathlib import Path
import cli, runtime
# External-provider ownership evidence includes attempts refused at connect.
with Path(sys.argv[0] + '.launches').open('a') as launches:
    launches.write(json.dumps({'pid': os.getpid()}) + '\n')
prompt = sys.stdin.read()
plan = json.loads(Path(sys.argv[0] + '.json').read_text())
conn = socket.create_connection(tuple(plan['address']), timeout=30)
conn.settimeout(30)
wire = conn.makefile('rwb')
def send(body):
    wire.write((json.dumps(body) + '\n').encode()); wire.flush()
provider_id = 'provider-' + str(os.getpid())
send({'hint': os.environ.get('HAPPYRANCH_RUNTIME_SESSION_ID'),
      'provider_id': provider_id, 'pid': os.getpid(), 'prompt': prompt,
      'argv': sys.argv[1:], 'python': sys.executable,
      'imports': [cli.__file__, runtime.__file__],
      'package': importlib.metadata.version('happyranch')})
while True:
    line = wire.readline()
    if not line:
        sys.exit(2)
    request = json.loads(line)
    if request['kind'] == 'finish':
        break
    args = request['args']
    env = dict(os.environ)
    env.update(request.get('env', {}))
    result = subprocess.run([str(Path(sys.executable).parent / 'happyranch'), *args],
                            env=env, text=True, capture_output=True, timeout=10)
    send({'exit': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
wire.close(); conn.close()
if plan['executor'] == 'claude':
    print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False,
        'session_id': provider_id, 'result': 'finished',
        'usage': {'input_tokens': 12, 'output_tokens': 3}}))
else:
    print(json.dumps({'type': 'thread.started', 'thread_id': provider_id}))
    print(json.dumps({'type': 'turn.completed',
        'usage': {'input_tokens': 12, 'output_tokens': 3, 'cached_input_tokens': 0}}))
'''


class _Child:
    def __init__(self, conn):
        self.conn = conn
        conn.settimeout(20)
        self.wire = conn.makefile('rwb')
        self.finished = False
        self.completion = None
        self.needs_callback = False

    def enter(self) -> None:
        self.evidence = json.loads(self.wire.readline())
        candidate = Path(__file__).resolve().parents[1]
        assert self.evidence['python'] == sys.executable
        assert self.evidence['imports'] == [str(candidate / 'cli/__init__.py'),
                                            str(candidate / 'runtime/__init__.py')]
        assert self.evidence['package'] == importlib.metadata.version('happyranch')
        assert self.evidence['provider_id'] != self.evidence['hint']

    def command(self, args, *, env=None):
        self._send({'kind': 'cli', 'args': args, 'env': env or {}})
        result = json.loads(self.wire.readline())
        assert result['exit'] == 0, result
        return result

    def _send(self, body):
        self.wire.write((json.dumps(body) + '\n').encode())
        self.wire.flush()

    def finish(self, *, abort: bool = False) -> None:
        if self.finished:
            return
        error = None
        try:
            if not abort and not (self.needs_callback and self.completion is None):
                # A failed callback must not be followed by a clean provider
                # exit: that would legitimately launch an unobserved nudge.
                if self.completion is not None:
                    self.command(self.completion)
                self._send({'kind': 'finish'})
        except BaseException as exc:
            error = exc
        finally:
            self.finished = True
            # Shutdown before closing the buffered wire also releases a peer
            # blocked in readline, including partial handshake/broken I/O.
            for release in (lambda: self.conn.shutdown(socket.SHUT_RDWR),
                            self.wire.close, self.conn.close):
                try:
                    release()
                except BaseException as exc:
                    if error is None:
                        error = exc
                    else:
                        error.add_note(f'owned socket cleanup: {exc!r}')
        if error is not None:
            raise error


class _Venue:
    def __init__(self, org, daemon, settings, listener, url, executor):
        self.org, self.daemon, self.settings = org, daemon, settings
        self.listener, self.url, self.executor = listener, url, executor
        self.cli = str(Path(sys.executable).parent / 'happyranch')
        self.children = []
        self.started = {}
        self.receipts = []
        self.futures = []
        self.runner_futures = set()
        self.pool = ThreadPoolExecutor(max_workers=3)
        self.server = self.server_thread = self.sock = None
        self.runs_closed = self.closed = False

    def submit(self, function: Callable[..., object], *args: object) -> Future:
        future = self.pool.submit(function, *args)
        self.futures.append(future)
        if function is asyncio.run:
            self.runner_futures.add(future)
        return future

    @contextmanager
    def runners(self) -> Iterator[None]:
        try:
            yield
        finally:
            self.finish_runs(primary=sys.exception())

    @staticmethod
    def _finish_errors(errors: list[BaseException], primary: BaseException | None) -> None:
        if errors:
            if primary is not None:
                for error in errors:
                    primary.add_note(f'owned cleanup: {type(error).__name__}: {error}')
            else:
                first, *rest = errors
                for error in rest:
                    first.add_note(f'owned cleanup: {type(error).__name__}: {error}')
                raise first

    def finish_runs(self, primary: BaseException | None = None) -> None:
        if self.runs_closed:
            return
        self.runs_closed = True
        errors = []
        for child in self.children:
            try:
                child.finish()
            except BaseException as exc:
                errors.append(exc)
        # Close the private admission socket before joins. A submitted runner
        # not yet accepted (or a legitimate retry) cannot leave a held child.
        if self.listener is not None:
            try:
                self.listener.close()
            except BaseException as exc:
                errors.append(exc)
        for future in self.futures:
            try:
                future.result(timeout=20)
            except FutureTimeout as exc:
                future.cancel()
                errors.append(exc)
            except BaseException as exc:
                errors.append(exc)
        self.pool.shutdown(wait=all(f.done() for f in self.futures), cancel_futures=True)
        self._finish_errors(errors, primary)

    def close(self, primary: BaseException | None = None) -> None:
        if self.closed:
            return
        self.closed = True
        errors = []
        try:
            self.finish_runs(primary)
        except BaseException as exc:
            errors.append(exc)
        # Every release is attempted even if a child/callback/join failed.
        def stop_server() -> None:
            if self.server is not None:
                self.server.should_exit = True
            if self.server_thread is not None and self.server_thread.ident is not None:
                self.server_thread.join(timeout=5)
                if self.server_thread.is_alive():
                    self.server.force_exit = True
                    self.server_thread.join(timeout=5)
                assert not self.server_thread.is_alive(), 'owned server did not stop'
        for release in (lambda: self.listener.close() if self.listener else None,
                        stop_server,
                        lambda: self.sock.close() if self.sock else None,
                        lambda: asyncio.run(self.daemon.close_all())):
            try:
                release()
            except BaseException as exc:
                errors.append(exc)
        self._finish_errors(errors, primary)

    def accept(self, future):
        # Socket connection is the entered barrier; no timing/sleep oracle.
        # If launch failed, surface its actual result rather than hiding it.
        if future not in self.futures:
            self.futures.append(future)
        try:
            conn, _ = self.listener.accept()
        except TimeoutError:
            if future.done():
                raise AssertionError(f'provider did not launch: {future.result()}') from None
            raise
        try:
            child = _Child(conn)
        except BaseException:
            conn.close()
            raise
        child.needs_callback = future in self.runner_futures
        self.children.append(child)
        try:
            child.enter()
        except BaseException as primary:
            try:
                child.finish(abort=True)
            except BaseException as secondary:
                primary.add_note(f'partial child cleanup: {secondary!r}')
            raise
        return child

    def task(self, task):
        def registered(task_id, agent, sid):
            assert self.org.sessions.get_context_by_session(sid) == ('alpha', task_id, agent)
            self.started[task_id] = sid
        return self.org.orchestrator._run_agent(
            task, 'dev_agent', 'shipping attribution', on_session_started=registered,
            resume_session_id='provider-resume-distinct', timeout_seconds_override=120,
        )

    def watermark(self):
        return self.org.db.fetch_all_readonly('SELECT COALESCE(max(id), 0) AS id FROM audit_log')[0]['id']

    def rows(self, after):
        rows = self.org.db.fetch_all_readonly(
            "SELECT id,action,agent,task_id,payload FROM audit_log WHERE id>? "
            "AND action IN ('memory_read','memory_search') ORDER BY id", (after,))
        return [{**row, 'payload': json.loads(row['payload'])} for row in rows]

    def population(self):
        return [dict(row) for row in self.org.db.fetch_all_readonly(
            "SELECT id,action,agent,task_id,payload FROM audit_log WHERE action IN "
            "('memory_runtime_intent','memory_runtime_identity','memory_runtime_expectation',"
            "'memory_runtime_binding','memory_runtime_launched','memory_digest_impression') ORDER BY id")]

    def census(self):
        # sampled_at is the observation clock, not an invocation/population fact.
        snapshot = self.org.memory_collection.snapshot()
        snapshot.pop('sampled_at')
        return snapshot

    def assert_pair(self, after, *, task=None, sid=None, agent='dev_agent', label):
        rows = self.rows(after)
        assert [row['action'] for row in rows] == ['memory_read', 'memory_search'], (label, rows)
        for row in rows:
            assert row['agent'] == agent
            own = {'session_id': sid, 'task_id': task} if task is not None else {}
            if row['action'] == 'memory_read':
                expected = {'id': 'MEM-001', 'slug': 'shipping', **own}
                if task is not None:
                    expected['source'] = 'digest'
                assert row['task_id'] == f'AGENT-{agent}'
            else:
                expected = {'agent': agent, 'memory_ids': ['MEM-001'],
                            'hit_count': 1, 'kb_hit_count': 0, **own}
                assert row['task_id'] == (task or f'AGENT-{agent}')
            assert row['payload'] == expected, (label, row)
            if task is None and row['action'] == 'memory_read':
                assert row['payload'].get('source', 'explicit_or_other') == 'explicit_or_other'
        self.receipts.append({'case': label, 'rows': rows})

    def pair(self, *, child=None, hint='', explicit=None, agent='dev_agent', task=None, sid=None, label):
        before = self.watermark()
        population = self.population()
        census = self.census()
        for verb, value in (('get', 'MEM-001'), ('search', 'shipping')):
            args = ['memory', verb, '--org', 'alpha', '--agent', agent, value, '--json']
            if explicit is not None:
                args += ['--session-id', explicit]
            if child:
                result = child.command(args)
            else:
                process = subprocess.run([self.cli, *args],
                    env={**os.environ, 'HAPPYRANCH_RUNTIME_SESSION_ID': hint},
                    text=True, capture_output=True, timeout=10)
                result = {'exit': process.returncode, 'stdout': process.stdout, 'stderr': process.stderr}
                assert process.returncode == 0, result
            body = json.loads(result['stdout'])
            if verb == 'get':
                assert (body['id'], body['body']) == ('MEM-001', 'shipping body\n')
            else:
                assert [hit['id'] for hit in body['hits']] == ['MEM-001']
        self.assert_pair(before, task=task, sid=sid, agent=agent, label=label)
        assert self.population() == population
        assert self.census() == census

    def forged_pair(self, task, sid, target):
        before = self.watermark()
        headers = {'Authorization': f'Bearer {paths.token_file().read_text().strip()}'}
        params = {'session_id': sid, 'task_id': target}
        read = httpx.get(self.url + '/agents/dev_agent/memory/entries/MEM-001',
                         params=params, headers=headers)
        search = httpx.post(self.url + '/agents/dev_agent/memory/entries/search', params=params,
                            json={'query': 'shipping', 'task_id': target}, headers=headers)
        assert read.status_code == search.status_code == 200, (read.text, search.text)
        assert read.json()['id'] == 'MEM-001'
        assert [hit['id'] for hit in search.json()['hits']] == ['MEM-001']
        self.assert_pair(before, task=task, sid=sid, label=f'forged:{task}->{target}')


@pytest.fixture
def shipping_venue(test_settings, monkeypatch, tmp_path, request):
    executor = request.param
    # Restore the actual production detector captured before autouse doubles.
    monkeypatch.setattr('runtime.platform.isolation.detect_platform_isolation', _actual_detector)
    monkeypatch.setattr('runtime.orchestrator.executors.detect_platform_isolation', _actual_detector)
    monkeypatch.setenv('HAPPYRANCH_TEST_REAL_PLATFORM', '1')
    monkeypatch.setenv('HAPPYRANCH_DAEMON_HOME', str(tmp_path / 'daemon'))
    for name in ('HAPPYRANCH_TASK_TMP_ROOT', 'HAPPYRANCH_TASK_SCRATCH_MANIFEST',
                 'HAPPYRANCH_RUNTIME_SESSION_ID'):
        monkeypatch.delenv(name, raising=False)
    paths.ensure_daemon_home()
    paths.ensure_token()
    runtime = RuntimeDir.init(tmp_path / 'runtime')
    for name in ('start-task', 'jobs', 'make-worktree', 'thread', 'dream', 'todos', 'workspace-cleanup'):
        source = runtime.root / 'skills' / 'bundled' / name
        source.mkdir(parents=True, exist_ok=True)
        (source / 'SKILL.md').write_text(f'# {name}\n')
    root = runtime.orgs_dir / 'alpha'
    (root / 'org').mkdir(parents=True)
    (root / 'org/teams.yaml').write_text(
        'teams:\n  engineering:\n    manager: engineering_head\n    workers: [dev_agent, other_agent, code_reviewer]\n')
    test_settings.executor_rate_limit_backoff_seconds = [0]
    test_settings.session_timeout_seconds = 120
    for agent in ('dev_agent', 'other_agent', 'engineering_head', 'code_reviewer'):
        workspace = root / 'workspaces' / agent
        workspace.mkdir(parents=True)
        (workspace / 'task_history.md').write_text('# owned source fixture\n')
        (workspace / 'AGENTS.md').write_text('# owned source fixture\n')
        (workspace / 'CLAUDE.md').symlink_to('AGENTS.md')
        agents = root / 'org/agents'
        agents.mkdir(exist_ok=True)
        (agents / f'{agent}.md').write_text(render_agent_text(AgentDef(
            name=agent, team='engineering',
            role='manager' if agent == 'engineering_head' else 'worker', executor=executor,
            allow_rules=(), repos={}, enrolled_by=None, enrolled_at_task=None,
            enrolled_at=None, system_prompt='worker', description='')))
        memory = workspace / 'memory'
        memory.mkdir()
        (memory / 'MEM-001-shipping.md').write_text(
            '---\nid: MEM-001\nslug: shipping\ntitle: Shipping\ntopic: test\n'
            'provenance: experiential\nscope: agent\nlifecycle: valid\nsalience: 50\n---\n\nshipping body\n')
    daemon = DaemonState.from_runtime(runtime, test_settings)
    org = daemon.orgs['alpha']
    venue = _Venue(org, daemon, test_settings, None, '', executor)
    try:
        listener = venue.listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen(4)
        listener.settimeout(20)
        provider = venue.provider = tmp_path / executor
        provider.write_text('#!' + sys.executable + '\n' + textwrap.dedent(_PROVIDER))
        provider.chmod(0o755)
        Path(str(provider) + '.json').write_text(json.dumps({
            'address': listener.getsockname(), 'executor': executor}))
        set_binary(executor, str(provider))
        sock = venue.sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        paths.port_file().write_text(str(sock.getsockname()[1]))
        venue.url = f'http://127.0.0.1:{sock.getsockname()[1]}/api/v1/orgs/alpha'
        ready = threading.Event()

        class ReadyServer(uvicorn.Server):
            async def startup(self, sockets=None):
                await super().startup(sockets=sockets)
                ready.set()

        server = venue.server = ReadyServer(uvicorn.Config(create_app(daemon), lifespan='off', log_level='error'))
        server_thread = venue.server_thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]})
        server_thread.start()
        assert ready.wait(10) and server.started
        print(json.dumps({'venue': 'ordinary/legacy source, host_supervisor=None for runners',
                          'detector': type(_actual_detector()).__name__,
                          'task_backend': daemon.host_supervisor.health_snapshot()['backend'],
                          'python': [sys.executable, sys.version], 'cwd': str(Path.cwd())}))
        yield venue
    finally:
        primary = sys.exception()
        venue.close(primary=None if isinstance(primary, GeneratorExit) else primary)


@pytest.mark.parametrize('shipping_venue', ('claude', 'codex'), indirect=True)
def test_task_non_task_attribution_overlap_and_population(shipping_venue, monkeypatch):
    """Finite S05 set; provider isolates transport, ambient isolates overlay.

    The two forged targets isolate own-tuple validation. SID variants isolate
    registry/agent/lifecycle validation; each verb gets its own audit oracle.
    Four runner cases per provider isolate family x absent/active-A poison.
    One socket-controlled A-end/B-active transition per provider covers stale
    attribution without multiplying every SID/target/runner combination.
    """
    v = shipping_venue
    org = v.org
    assert org.memory_collection.snapshot()['assigned_intents'] == 0
    a = org.orchestrator.create_task('shipping root A')
    b = 'TASK-SHIPPING-CHILD'
    org.db.insert_task(TaskRecord(id=b, parent_task_id=a, brief='shipping child B',
                                 team='engineering', assigned_agent='dev_agent', task_type='subtask'))
    # Provider connections are entered/release barriers. Only owned children
    # are released on failure, before waiting for the executor worker threads.
    with v.runners():
        try:
            fa = v.submit(v.task, a)
            ca = v.accept(fa)
            fb = v.submit(v.task, b)
            cb = v.accept(fb)
            sa, sb = v.started[a], v.started[b]
            assert ca.evidence['hint'] == sa and cb.evidence['hint'] == sb and sa != sb
            for child in (ca, cb):
                argv = child.evidence['argv']
                if v.executor == 'claude':
                    assert argv[argv.index('--resume') + 1] == 'provider-resume-distinct'
                else:
                    assert argv[:3] == ['exec', 'resume', 'provider-resume-distinct']
                assert 'provider-resume-distinct' not in (sa, sb, child.evidence['provider_id'])
            for task, sid in ((a, sa), (b, sb)):
                persisted = org.db.get_task(task)
                assert (persisted.current_session_id, persisted.assigned_agent) == (sid, 'dev_agent')
            assert org.db.get_task(b).parent_task_id == a
            impressions = org.db.get_audit_logs_by_action('memory_digest_impression')
            assert len(impressions) == 2
            for row, task, sid in zip(impressions, (a, b), (sa, sb), strict=True):
                assert (row['task_id'], row['agent']) == (task, 'dev_agent')
                assert row['payload'] == {'agent': 'dev_agent', 'session_id': sid,
                    'digest_ids': ['MEM-001'], 'digest_count': 1, 'budget': 1500,
                    'memory_telemetry_version': 1, 'pointer_ids': ['MEM-001'], 'full_body_ids': []}
            assert org.memory_collection.snapshot()['assigned_intents'] == 2
            assert v.census()['phase_counts'] == {
                phase: {'attempted': 0 if phase == 'terminal' else 2,
                        'persisted': 0 if phase == 'terminal' else 2}
                for phase in ('intent', 'identity', 'expectation', 'binding', 'launched', 'terminal')}
            v.pair(child=ca, task=a, sid=sa, label='TASK-A-active')
            v.pair(child=cb, task=b, sid=sb, label='TASK-B-active')
            for own, sid, other in ((a, sa, b), (b, sb, a)):
                for target in (other, 'TASK-999999999'):
                    v.forged_pair(own, sid, target)
            for hint in ('', 'sess-random-unregistered', ca.evidence['provider_id']):
                v.pair(hint=hint, label=f'MANUAL-invalid:{hint}')
            v.pair(hint=sa, agent='other_agent', label='wrong-agent')
            v.pair(hint='sess-random-unregistered', explicit=sb, task=b, sid=sb,
                   label='MANUAL-valid-explicit-compatibility')

            population = v.population()
            census = v.census()
            for family in ('thread', 'dream'):
                for poison in (False, True):
                    with monkeypatch.context() as ambient:
                        if poison:
                            ambient.setenv('HAPPYRANCH_RUNTIME_SESSION_ID', sa)
                        else:
                            ambient.delenv('HAPPYRANCH_RUNTIME_SESSION_ID', raising=False)
                        name = f'{family}-{v.executor}-{poison}'
                        if family == 'thread':
                            scope = 'THR-' + name
                            org.db.insert_thread(ThreadRecord(id=scope, subject='private source proof'))
                            org.db.add_thread_participant(scope, 'dev_agent', added_by='founder')
                            seq, arrivals = org.db.record_conversational_arrival(
                                thread_id=scope, speaker='founder', kind=ThreadMessageKind.MESSAGE,
                                body_markdown='private source proof', recipients=['dev_agent'])
                            token = arrivals[0].invocation_token
                            pending = org.db.get_pending_invocation(token)
                            assert pending is not None and pending.started_at is None
                            future = v.submit(asyncio.run, run_invocation(
                                org_state=org, invocation_token=token, settings=v.settings,
                                host_supervisor=None))
                        else:
                            scope = 'DREAM-' + name
                            now = datetime.now(timezone.utc)
                            org.db.insert_dream(DreamRecord(id=scope, agent_name='dev_agent',
                                local_date=('2026-10-04' if not poison else '2026-10-05'),
                                scheduled_for=now, window_start=now-timedelta(hours=1), window_end=now))
                            assert org.db.get_dream(scope).status == DreamStatus.PENDING
                            future = v.submit(asyncio.run, run_dream(
                                org_state=org, dream_id=scope, settings=v.settings,
                                host_supervisor=None))
                        child = v.accept(future)
                        runtime_sid = child.evidence['hint']
                        payload_file = paths.daemon_home() / (name + '.json')
                        if family == 'thread':
                            payload_file.write_text(json.dumps({'thread_id': scope,
                                'invocation_token': token, 'speaker': 'dev_agent',
                                'body_markdown': 'owned source reply', 'in_response_to_seq': seq}))
                            child.completion = ['threads', 'reply', '--org', 'alpha',
                                                '--from-file', str(payload_file)]
                        else:
                            payload_file.write_text(json.dumps({'summary': 'owned private source dream'}))
                            child.completion = ['dreams', 'complete', '--org', 'alpha',
                                                '--dream-id', scope, '--from-file', str(payload_file)]
                        if family == 'thread':
                            claimed = org.db.get_invocation_any_status(token)
                            assert claimed.started_at is not None and claimed.executor == v.executor
                            delivery = org.db.get_reply_delivery_state(scope, 'dev_agent')
                            assert delivery.running_invocation_token == token
                        else:
                            claimed = org.db.get_dream(scope)
                            assert claimed.status == DreamStatus.RUNNING and claimed.started_at is not None
                        v.pair(child=child, label=name)
                        assert runtime_sid.startswith('sess-') and runtime_sid not in (sa, sb)
                        assert org.sessions.get_context_by_session(runtime_sid) is None
                        if family == 'thread':
                            assert claimed.session_id == runtime_sid
                        child.finish()
                        future.result(timeout=15)
                        if family == 'thread':
                            terminal = org.db.get_invocation_any_status(token)
                            assert terminal.status.value == 'consumed' and terminal.consumed_at is not None
                            assert terminal.reply_message_seq == seq + 1
                            assert org.db.get_thread_session(scope, 'dev_agent')[0] == child.evidence['provider_id']
                        else:
                            terminal = org.db.get_dream(scope)
                            assert terminal.status == DreamStatus.COMPLETED and terminal.ended_at is not None
                            starts = [row for row in org.db.get_audit_logs(scope) if row['action'] == 'dream_started']
                            assert len(starts) == 1 and starts[0]['payload'] == {'executor': v.executor, 'model': None}
                        usage = [dict(row) for row in org.db.fetch_all_readonly(
                            'SELECT task_id,scope_type,scope_id,executor,session_id FROM session_token_usage WHERE scope_id=?', (scope,))
                        ]
                        assert len(usage) == 1, usage
                        assert usage[0] == {'task_id': None, 'scope_type': family, 'scope_id': scope,
                            'executor': v.executor, 'session_id': runtime_sid if family == 'thread' else child.evidence['provider_id']}
                        assert v.census()['assigned_intents'] == 2
                        assert v.population() == population
                        assert v.census() == census
                        v.receipts.append({'runner': name, 'runtime_sid': runtime_sid,
                                           'provider_id': child.evidence['provider_id'], 'usage': usage})

            # Ordinary A return retires its tracker entry while B is held.
            ca.finish()
            ra, _ = fa.result(timeout=15)
            assert ra.success and ra.session_id == sa
            assert org.sessions.get_context_by_session(sb) == ('alpha', b, 'dev_agent')
            v.pair(child=cb, task=b, sid=sb, label='OVERLAP-B-after-A-end')
            v.pair(hint=sa, label='END-stale-A')
            assert org.sessions.get_context_by_session(sa) is None
            v.pair(label='END-manual')
            cb.finish()
            rb, _ = fb.result(timeout=15)
            assert rb.success and rb.session_id == sb
            assert org.sessions.get_context_by_session(sb) is None
            assert org.memory_collection.snapshot()['assigned_intents'] == 2
            assert v.census()['phase_counts'] == {
                phase: {'attempted': 2, 'persisted': 2}
                for phase in ('intent', 'identity', 'expectation', 'binding', 'launched', 'terminal')}
            audit_receipts = [receipt for receipt in v.receipts if 'rows' in receipt]
            assert len(audit_receipts) == 18
            assert len(v.rows(0)) == 36
            end_ids = {}
            for task, sid, result, child in ((a, sa, ra, ca), (b, sb, rb, cb)):
                lifecycle = [row for row in org.db.get_audit_logs(task)
                             if row['action'] in ('session_start', 'session_end')]
                assert [row['action'] for row in lifecycle] == ['session_start', 'session_end']
                assert all((row['task_id'], row['agent']) == (task, 'dev_agent') for row in lifecycle)
                assert lifecycle[0]['payload']['session_id'] == sid
                assert lifecycle[0]['payload']['executor'] == v.executor
                assert (result.token_usage.input_tokens, result.token_usage.output_tokens) == (12, 3)
                assert result.agent_session_id == child.evidence['provider_id'] != sid
                end_ids[task] = lifecycle[1]['id']
                v.receipts.append({'task_lifecycle': task, 'rows': lifecycle})
            after_end = next(row for row in audit_receipts if row['case'] == 'OVERLAP-B-after-A-end')
            stale = next(row for row in audit_receipts if row['case'] == 'END-stale-A')
            assert end_ids[a] < after_end['rows'][0]['id'] < stale['rows'][0]['id'] < end_ids[b]
            identities = org.db.get_audit_logs_by_action('memory_runtime_identity')
            assert [(row['task_id'], row['payload']['population']) for row in identities] == [(a, 'root'), (b, 'child')]
            assert org.memory_collection.validate()['census_valid'] is True
            backend = org.orchestrator._audit.compute_memory_telemetry_report()
            report = subprocess.run([v.cli, 'memory', 'report', '--org', 'alpha',
                '--agent', 'dev_agent', '--json'], env={**os.environ, 'HAPPYRANCH_RUNTIME_SESSION_ID': ''},
                text=True, capture_output=True, timeout=10)
            assert report.returncode == 0, report.stderr
            for result in (backend, json.loads(report.stdout)):
                assert result['decision'] == 'insufficient_instrumentation'
                assert result['observation_period']['thresholds_met'] is False
            assert org.db.get_audit_logs_by_action('memory_collection_epoch_started') == []
            print(json.dumps({'S05': v.executor, 'receipts': v.receipts,
                              'census': org.memory_collection.snapshot()}))
        finally:
            v.finish_runs(primary=sys.exception())


@pytest.mark.parametrize('executor', ('claude', 'codex'))
@pytest.mark.parametrize('failure', ('callback', 'early_assert', 'early_unregistered_callback'))
def test_owned_cleanup_preserves_primary_and_releases_siblings(
    test_settings, monkeypatch, tmp_path: Path, executor: str, failure: str,
) -> None:
    """Real pending reply/CLI exit 1 plus a sibling AFTER the failing child.

    Throwing into the actual yield fixture reproduces assertion unwinding;
    closing it exercises teardown with the callback as the primary failure.
    The early assertion also has a secondary callback failure, so merely
    catching every teardown error cannot satisfy preservation of both errors.
    """
    primary = AssertionError('distinct early assertion before final callbacks')
    with pytest.raises(AssertionError) as caught:
        with contextmanager(shipping_venue.__wrapped__)(
            test_settings, monkeypatch, tmp_path, SimpleNamespace(param=executor),
        ) as v:
            root = v.org.orchestrator.create_task('owned cleanup bootstrap')
            root_future = v.submit(v.task, root)
            v.accept(root_future)
            scope = 'THR-owned-cleanup'
            v.org.db.insert_thread(ThreadRecord(id=scope, subject='owned cleanup'))
            v.org.db.add_thread_participant(scope, 'dev_agent', added_by='founder')
            seq, arrivals = v.org.db.record_conversational_arrival(
                thread_id=scope, speaker='founder', kind=ThreadMessageKind.MESSAGE,
                body_markdown='owned cleanup', recipients=['dev_agent'])
            token = arrivals[0].invocation_token
            future = v.submit(asyncio.run, run_invocation(
                org_state=v.org, invocation_token=token, settings=v.settings,
                host_supervisor=None))
            child = v.accept(future)
            pending = v.org.db.get_invocation_any_status(token)
            assert pending.status.value == 'pending' and pending.started_at is not None
            assert pending.session_id == child.evidence['hint']
            if failure != 'early_unregistered_callback':
                child.completion = ['threads', 'reply', '--org', 'alpha', '--from-file',
                                    str(tmp_path / 'nonexistent-callback.json')]
            sibling = v.submit(v.task, v.org.orchestrator.create_task('held cleanup sibling'))
            sibling_child = v.accept(sibling)
            assert not sibling.done() and not future.done()
            if failure.startswith('early_'):
                # Exercise BOTH the ordinary test-finally path and the fixture
                # finalizer. Secondary errors must annotate this exact object.
                with v.runners():
                    raise primary
    observed = caught.value
    if failure.startswith('early_'):
        assert observed is primary
        notes = getattr(primary, '__notes__', [])
        if failure == 'early_assert':
            assert any("'exit': 1" in note and 'nonexistent-callback.json' in note
                       for note in notes), notes
        else:
            assert not notes, notes
    else:
        assert "'exit': 1" in str(observed)
        assert 'nonexistent-callback.json' in str(observed)
    print(json.dumps({'cleanup': failure, 'executor': executor,
        'primary': str(observed), 'secondary': getattr(observed, '__notes__', [])}))
    assert v.closed and v.runs_closed
    assert len(v.children) == 3
    launches = [json.loads(line)['pid'] for line in
                Path(str(v.provider) + '.launches').read_text().splitlines()]
    assert len(launches) == 3
    assert set(launches) == {c.evidence['pid'] for c in v.children}  # no unseen retry
    assert sibling_child.finished
    assert all(c.finished and c.conn.fileno() == -1 and c.wire.closed for c in v.children)
    assert v.listener.fileno() == -1 and v.sock.fileno() == -1
    assert v.server.should_exit and not v.server_thread.is_alive()
    assert all(f.done() for f in v.futures)
    assert root_future.result()[0].success and sibling.result()[0].success
    assert future.result() is None  # real runner returned after nonzero provider
    assert not v.daemon.orgs
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        v.org.db.fetch_all_readonly('SELECT 1 AS must_be_closed')
    # Idempotence must not retry the failed callback or reopen resources.
    child.finish()
    v.finish_runs()
    v.close()
    print(json.dumps({'cleanup_postconditions': failure, 'executor': executor,
        'children': len(v.children), 'settled_futures': len(v.futures),
        'listener_closed': v.listener.fileno() == -1,
        'server_joined': not v.server_thread.is_alive(), 'database_closed': True}))
